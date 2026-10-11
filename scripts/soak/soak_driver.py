"""Operated soak driver for the rigplane web stack against the simulator.

Runs a real ``WebServer`` + ``RigctldServer`` in-process against a
``MockIcomRadio`` (IC-7610 simulator) for a configurable duration and
operates it the way an operator would: tune + readback on one cadence,
mode changes on another, a rigctld replay (``F``/``f``/``M``/``m``/``t``)
on a third, a forced radio disconnect/connect on a fourth, with a
WebSocket client subscribed to state and audio the whole time. Process
metrics (RSS / FDs / threads / asyncio tasks) are sampled periodically
and evaluated against the ticket's pass criteria:

* RSS growth < 50 MB/h (least-squares slope over post-warmup samples);
* FDs and asyncio tasks within ±5 of the pre-reconnect value after each
  forced reconnect;
* zero unrecovered disconnects: every forced disconnect is back to
  CONNECTED within ``--reconnect-budget`` seconds.

The driver can also operate an already-running server with ``--url``;
pass ``--rigctld-url`` to keep the replay running against its rigctld.

Exit status is 0 on a PASS verdict and 1 on FAIL. A JSON report is
written to ``--report``. Run from the repository root (the simulator
stack imports the top-level ``rigplane_testkit`` package)::

    uv run python -m scripts.soak.soak_driver --simulator --duration 3600
    uv run python -m scripts.soak.soak_driver --url http://127.0.0.1:8080

Process metrics: the sampler prefers a ``process`` object (``rssBytes`` /
``fds`` / ``threads``) from ``GET /api/v1/runtime`` when the server
exposes one; until that endpoint field exists it falls back to
driver-side sampling of the server PID (``/proc`` on Linux, ``ps`` and
``/dev/fd`` elsewhere). asyncio task counts are only observable when the
stack runs in this process (simulator mode); in ``--url`` mode the task
criterion is reported as NOT_EVALUATED.
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
# Below this fitted span the 50 MB/h bound is under the RSS noise floor
# (MB-scale wobble over seconds), so the criterion reports
# NOT_EVALUATED instead of guessing: 199 MB/h over 9 fitted seconds is
# noise, not a leak measurement.
RSS_MIN_FIT_SPAN_S = 600.0
STABILITY_TOLERANCE = 5
_STABILITY_PROBE_COUNT = 3
_DRIVER_TASK_PREFIX = "soak-"

_TUNE_FREQS = (14_074_000, 14_074_500, 7_074_000, 7_074_500, 3_574_000)
_MODES = ("USB", "LSB")
# The simulator's unsolicited loop drifts +10 Hz per frame; the frequency
# readback accepts this much above the commanded value before calling a
# mismatch (rigplane_testkit/icom/simulator.py: MockIcomRadio._unsolicited_loop).
_READBACK_DRIFT_HZ = 10_000


@dataclass
class SoakConfig:
    """Everything the driver needs; see ``--help`` for the flags."""

    duration_s: float
    url: str | None = None  # None → simulator mode (in-process stack)
    rigctld_url: str | None = None
    report_path: str = ""
    tune_interval_s: float = 2.0
    mode_interval_s: float = 10.0
    rigctld_interval_s: float = 15.0
    reconnect_interval_s: float = 300.0
    metrics_interval_s: float = 30.0
    reconnect_budget_s: float = 30.0
    fault_drop: float = 0.0  # 0 → no FaultPlan blackout at reconnects
    fault_duration_s: float = 3.0
    rss_warmup_s: float = 60.0


@dataclass
class ProcessSample:
    t_s: float
    rss_bytes: int | None
    fds: int | None
    threads: int | None
    asyncio_tasks: int | None
    connection: str | None
    source: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "t": round(self.t_s, 3),
            "rssBytes": self.rss_bytes,
            "fds": self.fds,
            "threads": self.threads,
            "asyncioTasks": self.asyncio_tasks,
            "connection": self.connection,
            "source": self.source,
        }


# ---------------------------------------------------------------------------
# Driver-side process metrics (the fallback sampler path)
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


def _proc_threads(pid: int) -> int | None:
    """Thread count via /proc/<pid>/status; None without /proc."""
    try:
        for line in Path(f"/proc/{pid}/status").read_text().splitlines():
            if line.startswith("Threads:"):
                return int(line.split()[1])
    except (OSError, ValueError, IndexError):
        pass
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


async def _driver_side_process_metrics(
    pid: int,
) -> tuple[int | None, int | None, int | None]:
    """(rss_bytes, fds, threads) for one PID, best-effort per field."""
    rss = _statm_rss_bytes(pid)
    if rss is None:
        out = await _ps_output(["-o", "rss=", "-p", str(pid)])
        with contextlib.suppress(ValueError, TypeError):
            rss = int(out.strip()) * 1024  # ps rss is in KB
    threads = _proc_threads(pid)
    if threads is None:
        out = await _ps_output(["-M", "-p", str(pid)])
        if out is not None:
            # ps -M prints a header, one process row, then one row per thread.
            threads = max(0, len([ln for ln in out.splitlines() if ln.strip()]) - 2)
    return rss, _fd_count(pid), threads


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


class ProcessMetricsSampler:
    """Samples the server process metrics for the soak report.

    Each sample first fetches ``GET /api/v1/runtime`` (also the driver's
    connection-state source). If that document carries a ``process``
    object with any of ``rssBytes`` / ``fds`` / ``threads``, those values
    are used and the sample's source is ``runtime.process``; otherwise the
    sampler measures the server PID itself (``/proc`` on Linux, ``ps`` /
    ``/dev/fd`` elsewhere) and the source is ``driver-side``. asyncio task
    counts are added locally in simulator mode only.
    """

    def __init__(
        self, session: aiohttp.ClientSession, base_url: str, *, in_process: bool
    ) -> None:
        self._session = session
        self._base_url = base_url
        self._in_process = in_process
        self.runtime_process_seen = False
        self.server_pid: int | None = None

    async def fetch_runtime(self, timeout_s: float = 5.0) -> dict[str, Any] | None:
        url = f"{self._base_url}/api/v1/runtime"
        try:
            async with self._session.get(
                url, timeout=aiohttp.ClientTimeout(total=timeout_s)
            ) as resp:
                if resp.status != 200:
                    return None
                body = await resp.json()
        except (aiohttp.ClientError, asyncio.TimeoutError, ValueError):
            return None
        return body if isinstance(body, dict) else None

    async def sample(self, t_s: float) -> ProcessSample:
        runtime = await self.fetch_runtime()
        connection: str | None = None
        rss: int | None = None
        fds: int | None = None
        threads: int | None = None
        source = "driver-side"
        if runtime is not None:
            conn = runtime.get("connection")
            if isinstance(conn, dict) and isinstance(conn.get("state"), str):
                connection = conn["state"]
            pid = runtime.get("pid")
            if isinstance(pid, int) and not isinstance(pid, bool):
                self.server_pid = pid
            proc = runtime.get("process")
            if isinstance(proc, dict):
                proc_rss = _proc_int(proc, "rssBytes")
                proc_fds = _proc_int(proc, "fds")
                proc_threads = _proc_int(proc, "threads")
                if (
                    proc_rss is not None
                    or proc_fds is not None
                    or (proc_threads is not None)
                ):
                    rss, fds, threads = proc_rss, proc_fds, proc_threads
                    source = "runtime.process"
                    self.runtime_process_seen = True
        pid = self.server_pid
        if source == "driver-side" and (pid is not None or self._in_process):
            rss, fds, threads = await _driver_side_process_metrics(
                pid if pid is not None else os.getpid()
            )
        tasks = _server_asyncio_task_count() if self._in_process else None
        return ProcessSample(
            t_s=t_s,
            rss_bytes=rss,
            fds=fds,
            threads=threads,
            asyncio_tasks=tasks,
            connection=connection,
            source=source,
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
    if error is not None:
        return "FAIL"
    for criterion in criteria.values():
        if criterion.get("status") == "FAIL":
            return "FAIL"
    return "PASS"


def summary_line(report: dict[str, Any]) -> str:
    criteria = report["criteria"]
    rss = criteria["rssGrowth"]
    rss_mb = (
        f"{rss['slopeBytesPerHour'] / (1024 * 1024):+.1f}MB/h"
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
        f" limit={RSS_LIMIT_BYTES_PER_HOUR // (1024 * 1024)}MB/h"
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
        *,
        rigctld_url: str | None = None,
        sim: MockIcomRadio | None = None,
        in_process: bool = False,
    ) -> None:
        self._cfg = config
        self._base_url = base_url
        self._rigctld_url = rigctld_url
        self._sim = sim
        self._in_process = in_process
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
        except (aiohttp.ClientError, asyncio.TimeoutError, ValueError):
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
        except (aiohttp.ClientError, asyncio.TimeoutError, ValueError):
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
        except (aiohttp.ClientError, asyncio.TimeoutError):
            return False

    async def _fetch_connection_state(self) -> str | None:
        """One fresh GET of the runtime connection state."""
        assert self._sampler is not None
        runtime = await self._sampler.fetch_runtime()
        if runtime is None:
            return None
        conn = runtime.get("connection")
        state = conn.get("state") if isinstance(conn, dict) else None
        if isinstance(state, str):
            self._connection_state = state
        return self._connection_state

    async def _wait_connection(
        self,
        predicate: Callable[[str | None], bool],
        timeout_s: float,
    ) -> bool:
        """Poll the runtime endpoint until connection.state matches.

        Only a state observed on a successful poll counts; a cached value
        from before the disconnect never satisfies the predicate."""
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
        if not self._rigctld_url:
            self._counts["rigctldSkipped"] += 1
            return
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
        assert self._sim is not None
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
        """POST connect (retrying) until the runtime reports CONNECTED."""
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
            threads=median("threads"),
            asyncio_tasks=median("asyncio_tasks"),
            connection=probes[-1].connection,
            source=probes[-1].source,
        )

    async def _reconnect_cycle(self) -> None:
        self._counts["forcedReconnects"] += 1
        assert self._sampler is not None
        self._cycle_active = True
        try:
            await self._reconnect_cycle_inner()
        finally:
            self._cycle_active = False

    async def _reconnect_cycle_inner(self) -> None:
        window_start_s = self._elapsed()
        pre = await self._stability_probe()
        started_s = self._elapsed()
        logger.info("soak: forced reconnect starting at +%.1fs", started_s)
        await self._post_radio_control("disconnect")
        await self._wait_connection(lambda state: state != "connected", timeout_s=10.0)
        if self._sim is not None and self._cfg.fault_drop > 0.0:
            self._install_fault_plan()
            logger.info(
                "soak: fault plan installed (drop=%.2f for %.1fs)",
                self._cfg.fault_drop,
                self._cfg.fault_duration_s,
            )
        recovered = await self._do_connect_until_recovered(
            self._elapsed() + self._cfg.reconnect_budget_s
        )
        if self._sim is not None:
            self._sim.fault_plan = None
        logger.info(
            "soak: reconnect %s after %.1fs",
            "recovered" if recovered else "UNRECOVERED",
            self._elapsed() - started_s,
        )
        entry: dict[str, Any] = {
            "startedAtS": round(started_s, 3),
            "recovered": recovered,
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
            try:
                await self._reconnect_cycle()
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("soak: reconnect cycle failed")
                self._reconnects.append(
                    {
                        "startedAtS": round(self._elapsed(), 3),
                        "recovered": False,
                        "recoveryS": None,
                        "budgetS": self._cfg.reconnect_budget_s,
                        "fdsPre": None,
                        "tasksPre": None,
                        "fdsPost": None,
                        "tasksPost": None,
                        "error": "cycle raised",
                    }
                )
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
            self._sampler = ProcessMetricsSampler(
                session, self._base_url, in_process=self._in_process
            )
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
                # operator and WS tasks are cancelled.
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
            "mode": "simulator" if self._cfg.url is None else "url",
            "url": self._base_url,
            "rigctldUrl": self._rigctld_url,
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
            "sampler": {
                "runtimeProcessObjectSeen": (
                    self._sampler.runtime_process_seen
                    if self._sampler is not None
                    else False
                ),
                "description": (
                    "process metrics from the runtime endpoint's process"
                    " object when present, else driver-side /proc + ps"
                    " sampling of the server pid; asyncio task counts from"
                    " the local loop in simulator mode"
                ),
            },
            "config": {
                "tuneIntervalS": self._cfg.tune_interval_s,
                "modeIntervalS": self._cfg.mode_interval_s,
                "rigctldIntervalS": self._cfg.rigctld_interval_s,
                "reconnectIntervalS": self._cfg.reconnect_interval_s,
                "metricsIntervalS": self._cfg.metrics_interval_s,
                "reconnectBudgetS": self._cfg.reconnect_budget_s,
                "faultDrop": self._cfg.fault_drop,
                "faultDurationS": self._cfg.fault_duration_s,
                "rssWarmupS": self._cfg.rss_warmup_s,
            },
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
            # follows; capture it as soon as the phases are done.
            self._ended_s = self._elapsed()
        return self._build_report()


async def run_soak(config: SoakConfig) -> dict[str, Any]:
    """Run one soak and return its report; writes the file when asked."""
    if config.url is None:
        stack = await _start_simulator_stack()
        try:
            driver = SoakDriver(
                config,
                f"http://127.0.0.1:{stack.web_port}",
                rigctld_url=f"127.0.0.1:{stack.rigctld_port}",
                sim=stack.sim,
                in_process=True,
            )
            report = await driver.run()
        finally:
            await stack.aclose()
    else:
        driver = SoakDriver(
            config,
            config.url.rstrip("/"),
            rigctld_url=config.rigctld_url,
            sim=None,
            in_process=False,
        )
        report = await driver.run()
    if config.report_path:
        Path(config.report_path).write_text(
            json.dumps(report, indent=2) + "\n", encoding="utf-8"
        )
    return report


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m scripts.soak.soak_driver",
        description="Operated soak driver for the rigplane web stack.",
    )
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument(
        "--simulator",
        action="store_true",
        help="run MockIcomRadio + WebServer + RigctldServer in-process",
    )
    mode.add_argument(
        "--url",
        help="operate an already-running server, e.g. http://127.0.0.1:8080",
    )
    parser.add_argument(
        "--rigctld-url",
        default=None,
        help="host:port of a rigctld server (url mode; skipped when absent)",
    )
    parser.add_argument(
        "--duration", type=float, default=3600.0, help="seconds (default 3600)"
    )
    parser.add_argument("--report", default="soak-report.json", help="JSON report path")
    parser.add_argument("--tune-interval", type=float, default=2.0)
    parser.add_argument("--mode-interval", type=float, default=10.0)
    parser.add_argument("--rigctld-interval", type=float, default=15.0)
    parser.add_argument("--reconnect-interval", type=float, default=300.0)
    parser.add_argument("--metrics-interval", type=float, default=30.0)
    parser.add_argument(
        "--reconnect-budget",
        type=float,
        default=30.0,
        help="seconds each forced disconnect may take to recover",
    )
    parser.add_argument(
        "--fault-drop",
        type=float,
        default=0.0,
        help="drop probability of the FaultPlan blackout at each reconnect",
    )
    parser.add_argument(
        "--fault-duration",
        type=float,
        default=3.0,
        help="seconds the FaultPlan blackout stays active at each reconnect",
    )
    parser.add_argument(
        "--rss-warmup",
        type=float,
        default=60.0,
        help="seconds excluded from the RSS linear fit (capped at 25%% of duration)",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    logging.basicConfig(
        level=logging.WARNING,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    config = SoakConfig(
        duration_s=args.duration,
        url=args.url,
        rigctld_url=args.rigctld_url,
        report_path=args.report,
        tune_interval_s=args.tune_interval,
        mode_interval_s=args.mode_interval,
        rigctld_interval_s=args.rigctld_interval,
        reconnect_interval_s=args.reconnect_interval,
        metrics_interval_s=args.metrics_interval,
        reconnect_budget_s=args.reconnect_budget,
        fault_drop=args.fault_drop,
        fault_duration_s=args.fault_duration,
        rss_warmup_s=args.rss_warmup,
    )
    report = asyncio.run(run_soak(config))
    print(summary_line(report))
    print(f"soak report written to {args.report}")
    return 0 if report["verdict"] == "PASS" else 1


if __name__ == "__main__":
    sys.exit(main())
