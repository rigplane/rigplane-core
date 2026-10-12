"""Operated soak driver for the rigplane web stack against the simulator.

Runs a real ``WebServer`` + ``RigctldServer`` in-process against a
``MockIcomRadio`` (IC-7610 simulator) for a configurable duration and
operates it the way an operator would: tune + readback on one cadence,
mode changes on another, a rigctld replay (``F``/``f``/``M``/``m``/``t``)
on a third, a forced radio disconnect/connect on a fourth, with a
WebSocket client subscribed to state and audio the whole time. Process
metrics (RSS / FDs / asyncio tasks) are sampled periodically and
evaluated against the ticket's pass criteria (see ``scripts/soak/
README.md`` for the criteria and their known limits).

Cadences are constants in this module, not CLI flags; the simulator soak
takes only ``--duration`` and ``--report`` (``--observe-url``: observe.py).
Exit status is 0 on a PASS verdict and 1 otherwise: FAIL, or INCOMPLETE (no
FAIL, but not every criterion PASSed). Run from the repository root (the
simulator stack imports the top-level ``rigplane_testkit`` package)::

    uv run python -m scripts.soak.soak_driver --duration 3600
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import json
import logging
import os
import sys
import time
from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

import aiohttp

from rigplane.rigctld.contract import RigctldConfig
from rigplane.rigctld.server import RigctldServer
from rigplane.runtime.radio import IcomRadio
from rigplane.web.server import WebConfig, WebServer
from rigplane_testkit.icom.faults import FaultPlan, FaultRule
from rigplane_testkit.icom.simulator import MockIcomRadio

logger = logging.getLogger("soak")

RSS_LIMIT_BYTES_PER_HOUR = 50 * 1024 * 1024
# Below this fitted span the 50 MiB/h bound is under the RSS noise floor
# (MiB-scale wobble over seconds), so the criterion reports
# NOT_EVALUATED instead of guessing: 199 MiB/h over 9 fitted seconds is
# noise, not a leak measurement.
RSS_MIN_FIT_SPAN_S = 600.0
# The verdict of a run with no error and no FAIL in which not every criterion
# PASSed.
VERDICT_INCOMPLETE = "INCOMPLETE"
STABILITY_TOLERANCE = 5
_STABILITY_PROBE_COUNT = 3
_DRIVER_TASK_PREFIX = "soak-"

TUNE_INTERVAL_S = 2.0
MODE_INTERVAL_S = 10.0
RIGCTLD_INTERVAL_S = 15.0
RECONNECT_INTERVAL_S = 300.0
METRICS_INTERVAL_S = 30.0
RECONNECT_BUDGET_S = 30.0
FAULT_DROP = 0.0
FAULT_DURATION_S = 3.0
RSS_WARMUP_S = 60.0

_TUNE_FREQS = (14_074_000, 14_074_500, 7_074_000, 7_074_500, 3_574_000)
_MODES = ("USB", "LSB")
# The simulator's unsolicited loop drifts +10 Hz per frame; the frequency
# readback accepts this much above the commanded value before calling a
# mismatch (rigplane_testkit/icom/simulator.py: MockIcomRadio._unsolicited_loop).
_READBACK_DRIFT_HZ = 10_000


@dataclass
class SoakConfig:
    """Everything the driver needs; cadences default to the constants."""

    duration_s: float
    report_path: str = ""
    tune_interval_s: float = TUNE_INTERVAL_S
    mode_interval_s: float = MODE_INTERVAL_S
    rigctld_interval_s: float = RIGCTLD_INTERVAL_S
    reconnect_interval_s: float = RECONNECT_INTERVAL_S
    metrics_interval_s: float = METRICS_INTERVAL_S
    reconnect_budget_s: float = RECONNECT_BUDGET_S
    fault_drop: float = FAULT_DROP
    fault_duration_s: float = FAULT_DURATION_S
    rss_warmup_s: float = RSS_WARMUP_S


@dataclass
class ProcessSample:
    t_s: float
    rss_bytes: int | None
    fds: int | None
    asyncio_tasks: int | None
    connection: str | None

    def to_dict(self) -> dict[str, Any]:
        return {
            "t": round(self.t_s, 3),
            "rssBytes": self.rss_bytes,
            "fds": self.fds,
            "asyncioTasks": self.asyncio_tasks,
            "connection": self.connection,
        }


# ---------------------------------------------------------------------------
# Driver-side process metrics (the per-field fallback sampler path)
# ---------------------------------------------------------------------------


async def _ps_output(args: list[str]) -> str | None:
    """Run ``ps`` and return stdout, or None when unavailable."""
    try:
        proc = await asyncio.create_subprocess_exec(
            "ps",
            *args,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
        )
    except OSError:
        return None
    try:
        out, _ = await asyncio.wait_for(proc.communicate(), timeout=10.0)
    except asyncio.TimeoutError:
        with contextlib.suppress(ProcessLookupError):
            proc.kill()
        await proc.wait()
        return None
    return out.decode("utf-8", "replace")


def _statm_rss_bytes(pid: int) -> int | None:
    """RSS via /proc/<pid>/statm (pages × page size); None without /proc."""
    try:
        parts = Path(f"/proc/{pid}/statm").read_text().split()
        return int(parts[1]) * os.sysconf("SC_PAGE_SIZE")
    except (OSError, ValueError, IndexError):
        return None


def _fd_count(pid: int) -> int | None:
    """Open-FD count via /proc/<pid>/fd, or /dev/fd for this process."""
    try:
        return len(os.listdir(f"/proc/{pid}/fd"))
    except OSError:
        if pid == os.getpid():
            try:
                return len(os.listdir("/dev/fd"))
            except OSError:
                return None
        return None


async def _driver_side_process_metrics(pid: int) -> tuple[int | None, int | None]:
    """(rss_bytes, fds) for one PID, best-effort per field."""
    rss = _statm_rss_bytes(pid)
    if rss is None:
        out = await _ps_output(["-o", "rss=", "-p", str(pid)])
        # None on OSError or the 10 s timeout: leave the field None —
        # int(None.strip()) raises AttributeError, which the
        # suppression below does not cover.
        if out is not None:
            with contextlib.suppress(ValueError, TypeError):
                rss = int(out.strip()) * 1024  # ps rss is in KB
    return rss, _fd_count(pid)


def _server_asyncio_task_count() -> int:
    """asyncio tasks belonging to the soaked stack (driver tasks excluded).

    Every task this driver creates is named with the ``soak-`` prefix
    (``_DRIVER_TASK_PREFIX``); everything else running on the loop is the
    in-process stack, so the difference is the server-side task count.
    """
    return sum(
        1
        for task in asyncio.all_tasks()
        if not task.get_name().startswith(_DRIVER_TASK_PREFIX)
    )


def _proc_int(proc: dict[str, Any], name: str) -> int | None:
    value = proc.get(name)
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def _state_connection_status(body: dict[str, Any]) -> str | None:
    """Connection status from one ``GET /api/v1/state`` document.

    ``connection.rigConnected`` is reconciled from the radio's live
    ``conn_state`` on every snapshot build (server.py:
    ``WebServer._snapshot_for_delivery``), unlike the runtime payload's
    ``connection`` block, which repeats the last reconnect-status
    callback payload until the next one arrives and can therefore be
    stale in both directions.
    """
    conn = body.get("connection")
    if not isinstance(conn, dict):
        return None
    connected = conn.get("rigConnected")
    if isinstance(connected, bool):
        return "connected" if connected else "disconnected"
    return None


class ProcessMetricsSampler:
    """Samples the server process metrics for the soak report.

    Each field is filled independently: a value from the runtime
    document's ``process`` object (``rssBytes`` / ``openFds`` /
    ``asyncioTasks``, the names of PR #4044; ``rssBytes`` is null where
    neither ``/proc/self/statm`` nor macOS ``proc_pidinfo`` is available)
    wins when present, and any field the runtime did not report is
    measured driver-side for this process (the stack runs in-process):
    RSS via ``/proc`` or ``ps``, FDs via ``/proc`` or ``/dev/fd``, tasks
    via ``_server_asyncio_task_count``. The connection column comes from
    the state document (``_state_connection_status``), not the runtime
    payload.
    """

    def __init__(self, session: aiohttp.ClientSession, base_url: str) -> None:
        self._session = session
        self._base_url = base_url

    async def _get_json(
        self, path: str, timeout_s: float = 5.0
    ) -> dict[str, Any] | None:
        # Best-effort: a poll must never crash a cycle. Besides network
        # errors this also swallows the connector's transient
        # "File descriptor ... is used by transport" RuntimeError seen
        # under connection churn against the simulator.
        assert self._session is not None
        try:
            async with self._session.get(
                f"{self._base_url}{path}",
                timeout=aiohttp.ClientTimeout(total=timeout_s),
            ) as resp:
                if resp.status != 200:
                    return None
                body = await resp.json()
        except Exception:
            return None
        return body if isinstance(body, dict) else None

    async def fetch_runtime(self) -> dict[str, Any] | None:
        return await self._get_json("/api/v1/runtime")

    async def fetch_connection_status(self) -> str | None:
        body = await self._get_json("/api/v1/state")
        if body is None:
            return None
        return _state_connection_status(body)

    async def sample(self, t_s: float) -> ProcessSample:
        runtime = await self.fetch_runtime()
        rss: int | None = None
        fds: int | None = None
        tasks: int | None = None
        if runtime is not None:
            proc = runtime.get("process")
            if isinstance(proc, dict):
                rss = _proc_int(proc, "rssBytes")
                fds = _proc_int(proc, "openFds")
                tasks = _proc_int(proc, "asyncioTasks")
        if rss is None or fds is None:
            d_rss, d_fds = await _driver_side_process_metrics(os.getpid())
            if rss is None:
                rss = d_rss
            if fds is None:
                fds = d_fds
        if tasks is None:
            tasks = _server_asyncio_task_count()
        return ProcessSample(
            t_s=t_s,
            rss_bytes=rss,
            fds=fds,
            asyncio_tasks=tasks,
            connection=await self.fetch_connection_status(),
        )


# ---------------------------------------------------------------------------
# In-process simulator stack
# ---------------------------------------------------------------------------


@dataclass
class _SimulatorStack:
    sim: MockIcomRadio
    radio: IcomRadio
    web: WebServer
    rigctld: RigctldServer

    @property
    def web_port(self) -> int:
        return self.web.port

    @property
    def rigctld_port(self) -> int:
        # RigctldServer exposes no port accessor; read the bound socket.
        return int(self.rigctld._server.sockets[0].getsockname()[1])  # noqa: SLF001

    async def aclose(self) -> None:
        await self.rigctld.stop()
        await self.web.stop()
        with contextlib.suppress(Exception):
            await self.radio.disconnect()
        await self.sim.stop()


async def _start_simulator_stack() -> _SimulatorStack:
    """The `rigplane web` stack minus managed TX, against the simulator.

    Managed TX composition is deliberately absent: it persists a user
    config file (platformdirs), which a soak must not touch. Without it
    the RigctldServer takes the same standalone wiring as `rigplane
    serve`, reusing the web seat's state services on the radio.
    """
    sim = MockIcomRadio(
        single_owner=True,
        unsolicited_civ=True,
        unsolicited_interval_s=0.1,
        enable_audio=True,
    )
    await sim.start()
    # timeout=1.0: the simulator does not answer a few menu reads the
    # poller seeds at connect (NB depth/width, DATA MOD inputs); at the
    # 5 s default each unanswered read stalls the web command drain by a
    # full timeout before the poller loop starts. On loopback 1.0 s is
    # ample for the reads the simulator does answer.
    radio = IcomRadio(
        host="127.0.0.1",
        port=sim.control_port,
        username="soak",
        password="soak",
        timeout=1.0,
        model="IC-7610",
    )
    await radio.connect()
    web = WebServer(radio, WebConfig(host="127.0.0.1", port=0, discovery=False))
    await web.start()
    # command_timeout above the 2 s default: with the web poller sharing
    # the CI-V lane, a set under acquisition contention can exceed it.
    rigctld = RigctldServer(
        radio, RigctldConfig(host="127.0.0.1", port=0, command_timeout=10.0)
    )
    await rigctld.start()
    return _SimulatorStack(sim=sim, radio=radio, web=web, rigctld=rigctld)


# ---------------------------------------------------------------------------
# Criteria evaluation (pure)
# ---------------------------------------------------------------------------


def _linear_slope_per_hour(points: list[tuple[float, float]]) -> float | None:
    """Least-squares slope (y units per hour) over (t_seconds, value) pairs."""
    n = len(points)
    if n < 2:
        return None
    mean_x = sum(t for t, _ in points) / n
    mean_y = sum(v for _, v in points) / n
    sxx = sum((t - mean_x) ** 2 for t, _ in points)
    if sxx <= 0.0:
        return None
    sxy = sum((t - mean_x) * (v - mean_y) for t, v in points)
    return sxy / sxx * 3600.0


def _stability_checks(
    reconnects: list[dict[str, Any]], field: str
) -> list[dict[str, Any]]:
    """Per-reconnect {pre, post, delta, ok} checks for one metric field."""
    checks: list[dict[str, Any]] = []
    for entry in reconnects:
        if not entry["recovered"]:
            continue
        pre = entry.get(f"{field}Pre")
        post = entry.get(f"{field}Post")
        if pre is None or post is None:
            continue
        delta = post - pre
        checks.append(
            {
                "reconnectStartedAtS": entry["startedAtS"],
                "pre": pre,
                "post": post,
                "delta": delta,
                "ok": abs(delta) <= STABILITY_TOLERANCE,
            }
        )
    return checks


def _stability_criterion(checks: list[dict[str, Any]]) -> dict[str, Any]:
    if checks:
        status = "PASS" if all(check["ok"] for check in checks) else "FAIL"
    else:
        status = "NOT_EVALUATED"
    return {"status": status, "tolerance": STABILITY_TOLERANCE, "checks": checks}


def evaluate_report(
    config: SoakConfig,
    samples: list[ProcessSample],
    reconnects: list[dict[str, Any]],
) -> dict[str, Any]:
    """The ticket's three pass criteria over the collected evidence.

    The RSS fit excludes the warm-up window AND each forced-reconnect
    window (entry ``windowStartS``..``windowEndS``): a reconnect's
    allocation burst is a bounded transient, not steady-state growth, and
    on a short run a single burst would dominate the slope.
    """
    warmup_s = min(config.rss_warmup_s, config.duration_s * 0.25)
    excluded = [
        (entry["windowStartS"], entry["windowEndS"])
        for entry in reconnects
        if entry.get("windowStartS") is not None and entry.get("windowEndS") is not None
    ]
    rss_points = [
        (sample.t_s, sample.rss_bytes)
        for sample in samples
        if sample.t_s >= warmup_s
        and sample.rss_bytes is not None
        and not any(start <= sample.t_s <= end for start, end in excluded)
    ]
    slope = _linear_slope_per_hour(rss_points)
    span = (
        max(t for t, _ in rss_points) - min(t for t, _ in rss_points)
        if rss_points
        else 0.0
    )
    if slope is None or span < RSS_MIN_FIT_SPAN_S:
        rss_status, slope_out = "NOT_EVALUATED", None
    else:
        rss_status = "PASS" if slope < RSS_LIMIT_BYTES_PER_HOUR else "FAIL"
        slope_out = round(slope, 1)
    rss = {
        "status": rss_status,
        "slopeBytesPerHour": slope_out,
        "limitBytesPerHour": RSS_LIMIT_BYTES_PER_HOUR,
        "samplesFitted": len(rss_points),
        "fittedSpanS": round(span, 3),
        "minFitSpanS": RSS_MIN_FIT_SPAN_S,
        "warmupExcludedS": round(warmup_s, 3),
    }
    fds_checks = _stability_checks(reconnects, "fds")
    tasks_checks = _stability_checks(reconnects, "tasks")
    unrecovered = sum(1 for check in reconnects if not check["recovered"])
    recovery = {
        "status": "FAIL" if unrecovered else "PASS",
        "forced": len(reconnects),
        "unrecovered": unrecovered,
        "budgetS": config.reconnect_budget_s,
    }
    if not reconnects:
        recovery["status"] = "NOT_EVALUATED"
    return {
        "rssGrowth": rss,
        "fdsStability": _stability_criterion(fds_checks),
        "tasksStability": _stability_criterion(tasks_checks),
        "recovery": recovery,
    }


def _verdict(criteria: dict[str, Any], error: str | None) -> str:
    """FAIL on an error or any FAIL; PASS if criteria exist and all PASS; else
    INCOMPLETE."""
    if error is not None:
        return "FAIL"
    statuses = [criterion.get("status") for criterion in criteria.values()]
    if "FAIL" in statuses:
        return "FAIL"
    if statuses and all(status == "PASS" for status in statuses):
        return "PASS"
    return VERDICT_INCOMPLETE


def summary_line(report: dict[str, Any]) -> str:
    criteria = report["criteria"]
    rss = criteria["rssGrowth"]
    rss_mb = (
        f"{rss['slopeBytesPerHour'] / (1024 * 1024):+.1f}MiB/h"
        if rss["slopeBytesPerHour"] is not None
        else "n/a"
    )
    fds_deltas = [
        abs(check["delta"])
        for check in criteria["fdsStability"]["checks"]
        if "delta" in check
    ]
    tasks_deltas = [
        abs(check["delta"])
        for check in criteria["tasksStability"]["checks"]
        if "delta" in check
    ]
    recovery = criteria["recovery"]
    return (
        f"soak {report['verdict']} rss={rss_mb}"
        f" limit={RSS_LIMIT_BYTES_PER_HOUR // (1024 * 1024)}MiB/h"
        f" fds_max_delta={max(fds_deltas, default=0)}"
        f" tasks_max_delta={max(tasks_deltas, default=0)}"
        f" unrecovered={recovery['unrecovered']}/{recovery['forced']}"
        f" samples={len(report['samples'])}"
    )


# ---------------------------------------------------------------------------
# The driver
# ---------------------------------------------------------------------------


class SoakDriver:
    """Operates one server for the configured duration and reports."""

    def __init__(
        self,
        config: SoakConfig,
        base_url: str,
        rigctld_url: str,
        sim: MockIcomRadio,
    ) -> None:
        self._cfg = config
        self._base_url = base_url
        self._rigctld_url = rigctld_url
        self._sim = sim
        self._session: aiohttp.ClientSession | None = None
        self._ws_session: aiohttp.ClientSession | None = None
        self._sampler: ProcessMetricsSampler | None = None
        self._samples: list[ProcessSample] = []
        self._reconnects: list[dict[str, Any]] = []
        self._counts: Counter[str] = Counter()
        self._connection_state: str | None = None
        self._error: str | None = None
        self._ready_s = 0.0
        self._t0 = 0.0
        self._ended_s: float | None = None
        # True while a forced reconnect cycle is concluding past the run
        # deadline: the operator loops keep running so the post-reconnect
        # stability probe samples an operating server, not an idle one.
        self._cycle_active = False
        self._freq_i = 0
        self._mode_i = 0
        self._last_freq: int | None = None
        self._last_mode: str | None = None

    # -- plumbing ----------------------------------------------------------

    def _elapsed(self) -> float:
        return time.monotonic() - self._t0

    async def _fetch_state(self, timeout_s: float = 5.0) -> dict[str, Any] | None:
        assert self._session is not None
        try:
            async with self._session.get(
                f"{self._base_url}/api/v1/state",
                timeout=aiohttp.ClientTimeout(total=timeout_s),
            ) as resp:
                if resp.status != 200:
                    return None
                body = await resp.json()
        except Exception:
            return None
        return body if isinstance(body, dict) else None

    async def _post_command(
        self, name: str, params: dict[str, Any], timeout_s: float = 10.0
    ) -> bool:
        assert self._session is not None
        try:
            async with self._session.post(
                f"{self._base_url}/api/v1/commands",
                json={"name": name, "params": params},
                timeout=aiohttp.ClientTimeout(total=timeout_s),
            ) as resp:
                if resp.status != 200:
                    return False
                body = await resp.json()
        except Exception:
            return False
        return isinstance(body, dict) and body.get("ok") is True

    async def _post_radio_control(self, action: str, timeout_s: float = 10.0) -> bool:
        assert self._session is not None
        try:
            async with self._session.post(
                f"{self._base_url}/api/v1/radio/{action}",
                data=b"",
                timeout=aiohttp.ClientTimeout(total=timeout_s),
            ) as resp:
                return resp.status == 200
        except Exception:
            return False

    async def _fetch_connection_state(self) -> str | None:
        """One fresh poll of the live connection status (state document)."""
        assert self._sampler is not None
        status = await self._sampler.fetch_connection_status()
        if status is not None:
            self._connection_state = status
        return self._connection_state

    async def _wait_connection(
        self,
        predicate: Callable[[str | None], bool],
        timeout_s: float,
    ) -> bool:
        """Poll the state document until the connection status matches.

        Only a status observed on a successful poll counts; a cached
        value from before the disconnect never satisfies the predicate."""
        deadline = self._elapsed() + timeout_s
        while True:
            state = await self._fetch_connection_state()
            if state is not None and predicate(state):
                return True
            if self._elapsed() >= deadline:
                return False
            await asyncio.sleep(0.3)

    # -- operator actions ----------------------------------------------------

    def _skip_operator_action(self) -> bool:
        return self._connection_state != "connected"

    async def _poll_state_main(self, timeout_s: float) -> dict[str, Any] | None:
        """Poll /api/v1/state until ``main`` is populated (or timeout)."""
        deadline = self._elapsed() + timeout_s
        while True:
            state = await self._fetch_state()
            main = state.get("main") if isinstance(state, dict) else None
            if isinstance(main, dict) and main.get("freqHz") is not None:
                return main
            if self._elapsed() >= deadline:
                return None
            await asyncio.sleep(0.3)

    async def _tune_once(self) -> None:
        if self._last_freq is not None and not self._skip_operator_action():
            # The simulator drifts its frequency autonomously (+10 Hz per
            # unsolicited frame), so the readback accepts a window above
            # the commanded target rather than exact equality.
            main = await self._poll_state_main(timeout_s=2.0)
            freq = main.get("freqHz") if main is not None else None
            if freq is None:
                # The store has not observed the field at all (the
                # simulator does not answer every profile poll) — not a
                # wrong-value readback.
                self._counts["readbackUnobserved"] += 1
            elif not self._last_freq <= freq <= (self._last_freq + _READBACK_DRIFT_HZ):
                self._counts["readbackMismatches"] += 1
        if self._skip_operator_action():
            self._counts["tunesSkipped"] += 1
            return
        freq = _TUNE_FREQS[self._freq_i % len(_TUNE_FREQS)]
        self._freq_i += 1
        if await self._post_command("set_freq", {"freq": freq}):
            self._counts["tunes"] += 1
            self._last_freq = freq
        else:
            self._counts["tuneFailures"] += 1

    async def _mode_once(self) -> None:
        if self._last_mode is not None and not self._skip_operator_action():
            main = await self._poll_state_main(timeout_s=2.0)
            mode = main.get("mode") if main is not None else None
            if mode is None:
                self._counts["modeReadbackUnobserved"] += 1
            elif mode != self._last_mode:
                self._counts["modeReadbackMismatches"] += 1
        if self._skip_operator_action():
            self._counts["modeChangesSkipped"] += 1
            return
        mode = _MODES[self._mode_i % len(_MODES)]
        self._mode_i += 1
        if await self._post_command("set_mode", {"mode": mode}):
            self._counts["modeChanges"] += 1
            self._last_mode = mode
        else:
            self._counts["modeFailures"] += 1

    async def _rigctld_cmd(
        self,
        writer: asyncio.StreamWriter,
        reader: asyncio.StreamReader,
        cmd: str,
        extra_lines: int = 0,
    ) -> list[str]:
        writer.write(f"{cmd}\n".encode("ascii"))
        await writer.drain()
        first = (await asyncio.wait_for(reader.readline(), timeout=5.0)).decode()
        lines = [first.strip()]
        for _ in range(extra_lines):
            if lines[0].startswith("RPRT"):
                break
            more = (await asyncio.wait_for(reader.readline(), timeout=5.0)).decode()
            lines.append(more.strip())
        return lines

    async def _rigctld_replay_once(self) -> None:
        if self._skip_operator_action():
            self._counts["rigctldSkipped"] += 1
            return
        host, _, port = self._rigctld_url.partition(":")
        freq = self._last_freq if self._last_freq is not None else _TUNE_FREQS[0]
        mode = self._last_mode if self._last_mode is not None else _MODES[0]
        failed_step: str | None = None
        try:
            reader, writer = await asyncio.wait_for(
                asyncio.open_connection(host, int(port)), timeout=5.0
            )
        except (OSError, asyncio.TimeoutError, ValueError) as exc:
            self._counts["rigctldFailures"] += 1
            logger.warning("soak: rigctld replay connect failed: %s", exc)
            return
        try:
            if await self._rigctld_cmd(writer, reader, f"F {freq}") != ["RPRT 0"]:
                failed_step = "F"
            freq_reply = await self._rigctld_cmd(writer, reader, "f")
            if not (len(freq_reply) == 1 and freq_reply[0].isdigit()):
                failed_step = failed_step or "f"
            if await self._rigctld_cmd(writer, reader, f"M {mode} 0") != ["RPRT 0"]:
                failed_step = failed_step or "M"
            mode_reply = await self._rigctld_cmd(writer, reader, "m", extra_lines=1)
            if not (len(mode_reply) == 2 and not mode_reply[0].startswith("RPRT")):
                failed_step = failed_step or "m"
            ptt_reply = await self._rigctld_cmd(writer, reader, "t")
            if ptt_reply[0] not in ("0", "1"):
                failed_step = failed_step or "t"
        except (OSError, asyncio.TimeoutError) as exc:
            failed_step = f"io: {exc!r}"
        finally:
            writer.close()
            with contextlib.suppress(Exception):
                await writer.wait_closed()
        if failed_step is None:
            self._counts["rigctldReplays"] += 1
        else:
            self._counts["rigctldFailures"] += 1
            logger.warning("soak: rigctld replay failed at step %s", failed_step)

    # -- forced disconnect / reconnect ----------------------------------------

    def _install_fault_plan(self) -> None:
        """Black out the simulated radio link for the configured window."""
        self._sim.fault_plan = FaultPlan(
            control_from_client=FaultRule(
                drop_probability=self._cfg.fault_drop,
                duration_s=self._cfg.fault_duration_s,
            ),
            control_to_client=FaultRule(
                drop_probability=self._cfg.fault_drop,
                duration_s=self._cfg.fault_duration_s,
            ),
            civ_from_client=FaultRule(
                drop_probability=self._cfg.fault_drop,
                duration_s=self._cfg.fault_duration_s,
            ),
            civ_to_client=FaultRule(
                drop_probability=self._cfg.fault_drop,
                duration_s=self._cfg.fault_duration_s,
            ),
        )

    async def _do_connect_until_recovered(self, deadline_s: float) -> bool:
        """POST connect (retrying) until the stack reports CONNECTED.

        The criterion is the stack being back to CONNECTED, however that
        happens: the driver's connect POST, or the radio's own CI-V
        watchdog soft reconnect (~8 s after a disconnect), whichever
        comes first.
        """
        while self._elapsed() < deadline_s:
            remaining = deadline_s - self._elapsed()
            await self._post_radio_control(
                "connect", timeout_s=max(1.0, min(10.0, remaining))
            )
            if await self._wait_connection(
                lambda state: state == "connected", timeout_s=2.0
            ):
                return True
        return await self._fetch_connection_state() == "connected"

    async def _stability_probe(self) -> ProcessSample:
        """Median of 3 quick samples: in-flight requests and rigctld
        replays make single task/FD counts oscillate by a few; the median
        keeps the pre/post comparison about leaks, not about a spike."""
        assert self._sampler is not None
        probes = [
            await self._sampler.sample(self._elapsed())
            for _ in range(_STABILITY_PROBE_COUNT)
        ]

        def median(field_name: str) -> int | None:
            present = [
                value
                for value in (getattr(probe, field_name) for probe in probes)
                if value is not None
            ]
            if not present:
                return None
            present.sort()
            return present[len(present) // 2]

        return ProcessSample(
            t_s=probes[len(probes) // 2].t_s,
            rss_bytes=median("rss_bytes"),
            fds=median("fds"),
            asyncio_tasks=median("asyncio_tasks"),
            connection=probes[-1].connection,
        )

    def _record_unrecovered_cycle(self, reason: str) -> None:
        self._reconnects.append(
            {
                "startedAtS": round(self._elapsed(), 3),
                "recovered": False,
                "disconnectObserved": None,
                "recoveryS": None,
                "budgetS": self._cfg.reconnect_budget_s,
                "windowStartS": None,
                "windowEndS": None,
                "fdsPre": None,
                "tasksPre": None,
                "fdsPost": None,
                "tasksPost": None,
                "error": reason,
            }
        )

    async def _reconnect_cycle(self) -> None:
        self._counts["forcedReconnects"] += 1
        assert self._sampler is not None
        self._cycle_active = True
        try:
            await self._reconnect_cycle_inner()
        except asyncio.CancelledError:
            # The cycle was cancelled without concluding (the run's wait
            # for it timed out): a forced disconnect that never recovered
            # must still fail the verdict, so record it as unrecovered.
            self._record_unrecovered_cycle("cycle cancelled before concluding")
            raise
        except Exception:
            logger.exception("soak: reconnect cycle failed")
            self._record_unrecovered_cycle("cycle raised")
        finally:
            self._cycle_active = False

    async def _reconnect_cycle_inner(self) -> None:
        window_start_s = self._elapsed()
        pre = await self._stability_probe()
        started_s = self._elapsed()
        logger.info("soak: forced reconnect starting at +%.1fs", started_s)
        await self._post_radio_control("disconnect")
        disconnect_observed = await self._wait_connection(
            lambda state: state != "connected", timeout_s=10.0
        )
        if not disconnect_observed:
            logger.warning("soak: disconnect never became observable; failing cycle")
        recovered = False
        if self._cfg.fault_drop > 0.0:
            self._install_fault_plan()
            logger.info(
                "soak: fault plan installed (drop=%.2f for %.1fs)",
                self._cfg.fault_drop,
                self._cfg.fault_duration_s,
            )
        try:
            if disconnect_observed:
                recovered = await self._do_connect_until_recovered(
                    self._elapsed() + self._cfg.reconnect_budget_s
                )
        finally:
            self._sim.fault_plan = None
        logger.info(
            "soak: reconnect %s after %.1fs",
            "recovered" if recovered else "UNRECOVERED",
            self._elapsed() - started_s,
        )
        entry: dict[str, Any] = {
            "startedAtS": round(started_s, 3),
            "recovered": recovered,
            "disconnectObserved": disconnect_observed,
            "recoveryS": round(self._elapsed() - started_s, 3),
            "budgetS": self._cfg.reconnect_budget_s,
            "windowStartS": round(window_start_s, 3),
            "windowEndS": None,
            "fdsPre": pre.fds,
            "tasksPre": pre.asyncio_tasks,
            "fdsPost": None,
            "tasksPost": None,
        }
        if recovered:
            # The stack rebuilds its poller/keepalive tasks on connect;
            # let that settle so the probe compares steady states.
            await asyncio.sleep(3.0)
            post = await self._stability_probe()
            entry["fdsPost"] = post.fds
            entry["tasksPost"] = post.asyncio_tasks
        entry["windowEndS"] = round(self._elapsed(), 3)
        self._reconnects.append(entry)

    async def _reconnect_loop(self) -> None:
        next_due = self._ready_s + self._cfg.reconnect_interval_s
        while self._elapsed() < self._cfg.duration_s:
            wait = min(next_due, self._cfg.duration_s) - self._elapsed()
            if wait > 0:
                await asyncio.sleep(wait)
            if self._elapsed() >= self._cfg.duration_s:
                return
            if self._elapsed() + 1e-9 < next_due:
                continue
            await self._reconnect_cycle()
            next_due = self._elapsed() + self._cfg.reconnect_interval_s

    # -- periodic loops -------------------------------------------------------

    def _operating(self) -> bool:
        """Operator loops run until the deadline AND in-flight cycles end."""
        return self._elapsed() < self._cfg.duration_s or self._cycle_active

    async def _run_periodic(
        self,
        name: str,
        interval_s: float,
        action: Callable[[], Any],
    ) -> None:
        next_due = self._ready_s
        while self._operating():
            wait = min(next_due, self._cfg.duration_s) - self._elapsed()
            if wait > 0:
                await asyncio.sleep(wait)
            if not self._operating():
                return
            if self._elapsed() + 1e-9 < next_due:
                # Reachable only past the run deadline while an in-flight
                # reconnect cycle keeps this loop alive: the capped wait
                # above is negative, so nothing slept. Sleep to the due
                # point — a bare ``continue`` here spins without ever
                # yielding and starves the cycle's task on this shared
                # event loop.
                await asyncio.sleep(next_due - self._elapsed())
                continue
            try:
                await action()
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("soak: %s action failed", name)
            next_due = self._elapsed() + interval_s

    async def _metrics_once(self) -> None:
        assert self._sampler is not None
        sample = await self._sampler.sample(self._elapsed())
        self._samples.append(sample)
        if sample.connection is not None:
            self._connection_state = sample.connection

    async def _ws_reader_loop(self, kind: str, url: str) -> None:
        """Stay subscribed to one WS channel; resubscribe on any drop."""
        assert self._ws_session is not None
        while True:
            try:
                async with self._ws_session.ws_connect(url, autoping=True) as ws:
                    if kind == "state":
                        await ws.send_json(
                            {"type": "subscribe", "streams": ["state", "events"]}
                        )
                    else:
                        await ws.send_json({"type": "audio_start", "direction": "rx"})
                    async for msg in ws:
                        if msg.type in (
                            aiohttp.WSMsgType.TEXT,
                            aiohttp.WSMsgType.BINARY,
                        ):
                            self._counts[f"{kind}WsMessages"] += 1
                    # Server closed the stream: count and resubscribe.
                    self._counts["wsDrops"] += 1
            except asyncio.CancelledError:
                raise
            except Exception:
                self._counts["wsDrops"] += 1
            await asyncio.sleep(0.5)

    # -- main run -------------------------------------------------------------

    async def _wait_ready(self, timeout_s: float) -> bool:
        return await self._wait_connection(
            lambda state: state == "connected", timeout_s=timeout_s
        )

    async def _run_phases(self) -> None:
        timeout = aiohttp.ClientTimeout(total=None, connect=10.0)
        # Two sessions: the two WebSocket readers hold their connections
        # for the whole run, so a shared capped pool starves every HTTP
        # request behind them. The HTTP session is capped at 2 (models one
        # operator's browser tab); the WS session is uncapped.
        async with (
            aiohttp.ClientSession(timeout=timeout) as ws_session,
            aiohttp.ClientSession(
                timeout=timeout, connector=aiohttp.TCPConnector(limit=2)
            ) as session,
        ):
            self._ws_session = ws_session
            self._session = session
            self._sampler = ProcessMetricsSampler(session, self._base_url)
            if not await self._wait_ready(
                max(60.0, self._cfg.reconnect_budget_s * 2.0)
            ):
                self._error = "server did not report a connected radio"
                return
            self._ready_s = self._elapsed()
            tasks = [
                asyncio.create_task(
                    self._ws_reader_loop("state", f"{self._base_url}/api/v1/ws"),
                    name="soak-ws-state",
                ),
                asyncio.create_task(
                    self._ws_reader_loop("audio", f"{self._base_url}/api/v1/audio"),
                    name="soak-ws-audio",
                ),
                asyncio.create_task(
                    self._run_periodic(
                        "tune", self._cfg.tune_interval_s, self._tune_once
                    ),
                    name="soak-tune",
                ),
                asyncio.create_task(
                    self._run_periodic(
                        "mode", self._cfg.mode_interval_s, self._mode_once
                    ),
                    name="soak-mode",
                ),
                asyncio.create_task(
                    self._run_periodic(
                        "rigctld",
                        self._cfg.rigctld_interval_s,
                        self._rigctld_replay_once,
                    ),
                    name="soak-rigctld",
                ),
                asyncio.create_task(
                    self._run_periodic(
                        "metrics",
                        self._cfg.metrics_interval_s,
                        self._metrics_once,
                    ),
                    name="soak-metrics",
                ),
            ]
            reconnect_task = asyncio.create_task(
                self._reconnect_loop(), name="soak-reconnect"
            )
            try:
                await asyncio.sleep(max(0.0, self._cfg.duration_s - self._elapsed()))
            finally:
                # The reconnect loop starts no new cycles past the
                # deadline; let an in-flight one conclude (its post probe
                # needs the operator loops still running) before the
                # operator and WS tasks are cancelled. On timeout the
                # cycle is cancelled and records itself as unrecovered,
                # so the report still comes out with a FAIL verdict.
                try:
                    await asyncio.wait_for(
                        reconnect_task,
                        timeout=self._cfg.reconnect_budget_s + 60.0,
                    )
                except asyncio.TimeoutError:
                    reconnect_task.cancel()
                    await asyncio.gather(reconnect_task, return_exceptions=True)
                for task in tasks:
                    task.cancel()
                await asyncio.gather(*tasks, return_exceptions=True)

    def _build_report(self) -> dict[str, Any]:
        criteria = evaluate_report(self._cfg, self._samples, self._reconnects)
        verdict = _verdict(criteria, self._error)
        return {
            "schemaVersion": 1,
            "verdict": verdict,
            "startedAt": datetime.now(timezone.utc).isoformat(),
            "durationConfiguredS": self._cfg.duration_s,
            "durationActualS": round(
                self._ended_s if self._ended_s is not None else self._elapsed(), 3
            ),
            "error": self._error,
            "counts": dict(self._counts),
            "criteria": criteria,
            "reconnects": self._reconnects,
            "samples": [sample.to_dict() for sample in self._samples],
        }

    async def run(self) -> dict[str, Any]:
        self._t0 = time.monotonic()
        try:
            await self._run_phases()
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.exception("soak driver failed")
            self._error = f"{type(exc).__name__}: {exc}"
        finally:
            # The operated duration excludes the stack teardown that
            # follows; capture it as soon as the phases are done. The
            # report below is built from whatever concluded: a phases
            # error, or an internal cycle timeout (recorded unrecovered),
            # still yields a FAIL report rather than none.
            self._ended_s = self._elapsed()
        return self._build_report()


async def run_soak(config: SoakConfig) -> dict[str, Any]:
    """Run one soak and return its report; writes the file when asked.

    The report file is written inside the same ``finally``, before the
    stack teardown: an exception from a ``stop()`` during
    ``_SimulatorStack.aclose`` must not cost the report file and its
    ``verdict`` field (``test_soak_report_written_when_a_stop_raises``
    pins it); the stdout verdict line is printed by ``main`` only after
    ``run_soak`` returns, so a raising teardown still loses that line.
    """
    stack = await _start_simulator_stack()
    report: dict[str, Any] | None = None
    try:
        driver = SoakDriver(
            config,
            f"http://127.0.0.1:{stack.web_port}",
            rigctld_url=f"127.0.0.1:{stack.rigctld_port}",
            sim=stack.sim,
        )
        report = await driver.run()
        return report
    finally:
        if config.report_path and report is not None:
            Path(config.report_path).write_text(
                json.dumps(report, indent=2) + "\n", encoding="utf-8"
            )
        await stack.aclose()


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m scripts.soak.soak_driver",
        description="Operated soak driver for the rigplane web stack.",
    )
    parser.add_argument(
        "--duration", type=float, default=3600.0, help="seconds (default 3600)"
    )
    parser.add_argument("--report", default="soak-report.json", help="JSON report path")
    return parser


def main(argv: list[str] | None = None) -> int:
    from scripts.soak import observe

    parser = _build_parser()
    observe.add_arguments(parser)
    args = parser.parse_args(argv)
    logging.basicConfig(
        level=logging.WARNING,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    if args.observe_url is not None:
        return observe.main_from_args(args, parser)
    config = SoakConfig(duration_s=args.duration, report_path=args.report)
    report = asyncio.run(run_soak(config))
    print(summary_line(report))
    print(f"soak report written to {args.report}")
    return 0 if report["verdict"] == "PASS" else 1


if __name__ == "__main__":
    sys.exit(main())
