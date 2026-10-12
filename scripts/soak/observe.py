"""GET-only observation of an already running ``rigplane web`` server.

Samples two read endpoints every ``--interval-s`` seconds and judges the
soak criteria from what the server reports about itself.

Receive-only by construction: the only HTTP calls in this module go
through :class:`GetOnlyClient`, which sends ``GET`` and nothing else, and
only to :data:`READ_ONLY_PATHS`. Both paths are listed with ``"auth":
"none"`` in ``web/api_contract.py: STABLE_HTTP_ENDPOINTS``, and
``WebConfig.auth_token`` is rejected when set, so there is no credential to
pass.
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import json
import logging
import os
import signal
import time
import urllib.parse
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import aiohttp

from scripts.soak import soak_driver
from scripts.soak.soak_driver import (
    ProcessSample,
    SoakConfig,
    _proc_int,
    _stability_checks,
    _stability_criterion,
    _verdict,
    evaluate_report,
)

logger = logging.getLogger("soak")

RUNTIME_PATH = "/api/v1/runtime"
READY_PATH = "/readyz"
READ_ONLY_PATHS = (RUNTIME_PATH, READY_PATH)
# Default budget for one down episode (--recovery-budget-s).
OBSERVE_RECOVERY_BUDGET_S = 180.0
GET_TIMEOUT_S = 5.0


@dataclass(frozen=True)
class Reply:
    """One GET result; ``status`` is None when no HTTP response arrived."""

    status: int | None
    body: dict[str, Any] | None


class GetOnlyClient:
    """The observer's only door to the server: GET on READ_ONLY_PATHS."""

    def __init__(self, session: aiohttp.ClientSession, base_url: str) -> None:
        self._session = session
        self._base_url = base_url

    async def get_json(self, path: str) -> Reply:
        if path not in READ_ONLY_PATHS:
            raise ValueError(f"not a read-only observer path: {path!r}")
        try:
            async with self._session.get(
                f"{self._base_url}{path}",
                timeout=aiohttp.ClientTimeout(total=GET_TIMEOUT_S),
            ) as resp:
                status = resp.status
                raw = await resp.read()
        except Exception:
            return Reply(None, None)  # no HTTP response arrived
        try:
            body = json.loads(raw)
        except ValueError:
            body = None
        return Reply(status, body if isinstance(body, dict) else None)


@dataclass
class ObserveConfig:
    base_url: str
    duration_s: float
    interval_s: float
    recovery_budget_s: float
    warmup_s: float
    report_path: str = ""


@dataclass
class ObserveSample(ProcessSample):
    """A ``ProcessSample`` plus what the two endpoints said about the link."""

    pid: int | None = None
    reconnects_total: int | None = None
    reachable: bool = False
    down_reasons: tuple[str, ...] = ()

    @property
    def down(self) -> bool:
        return bool(self.down_reasons)

    def to_dict(self) -> dict[str, Any]:
        return {
            **super().to_dict(),
            "pid": self.pid,
            "reconnectsTotal": self.reconnects_total,
            "reachable": self.reachable,
            "down": self.down,
            "downReasons": list(self.down_reasons),
        }


def _dig(doc: dict[str, Any] | None, *keys: str) -> Any:
    for key in keys:
        if not isinstance(doc, dict):
            return None
        doc = doc.get(key)
    return doc


def parse_sample(t_s: float, runtime: Reply, ready: Reply) -> ObserveSample:
    """One sample from the two replies; a missing or non-integer field is None.

    A sample is down when the server did not answer, ``/readyz`` says not
    ready, or ``station.health.radioLink`` is anything but ``connected``.
    A server that answers but says nothing about the link is down too: only
    positive evidence reads as up.
    """
    doc = runtime.body if runtime.status == 200 else None
    process = _dig(doc, "process")
    process = process if isinstance(process, dict) else {}
    link = _dig(doc, "station", "health", "radioLink")
    link = link if isinstance(link, str) else None
    # /readyz answers 503 with a JSON body when not ready.
    ready_doc = ready.body if ready.status in (200, 503) else None
    ready_flag = _dig(ready_doc, "radioReady")
    ready_flag = ready_flag if isinstance(ready_flag, bool) else None
    reachable = runtime.status is not None or ready.status is not None

    reasons: list[str] = []
    if not reachable:
        reasons.append("unreachable")
    else:
        if ready_flag is False:
            reasons.append("not_ready")
        if link is not None and link != "connected":
            reasons.append(f"radioLink={link}")
        if ready_flag is None and link is None:
            reasons.append("no_link_evidence")
    connection = None if not reachable else ("disconnected" if reasons else "connected")
    return ObserveSample(
        t_s=t_s,
        rss_bytes=_proc_int(process, "rssBytes"),
        fds=_proc_int(process, "openFds"),
        asyncio_tasks=_proc_int(process, "asyncioTasks"),
        connection=connection,
        pid=_proc_int(doc or {}, "pid"),
        reconnects_total=_proc_int(process, "reconnectsTotal"),
        reachable=reachable,
        down_reasons=tuple(reasons),
    )


def find_episodes(
    samples: list[ObserveSample], ended_s: float, budget_s: float
) -> list[dict[str, Any]]:
    """Every run of down samples, with its measured recovery time.

    ``startedAtS`` is the first down sample and ``endedAtS`` the first up
    sample after it, so both are accurate to one sampling interval. An
    episode still open at the end of the run counts up to ``ended_s``.
    """
    episodes: list[dict[str, Any]] = []
    current: dict[str, Any] | None = None
    for sample in samples:
        if sample.down:
            if current is None:
                current = {
                    "startedAtS": round(sample.t_s, 3),
                    "endedAtS": None,
                    "reasons": [],
                }
                episodes.append(current)
            current["reasons"] += [
                r for r in sample.down_reasons if r not in current["reasons"]
            ]
        else:
            if current is not None:
                current["endedAtS"] = round(sample.t_s, 3)
                current = None
    for episode in episodes:
        end = episode["endedAtS"]
        episode["recovered"] = end is not None
        episode["recoveryS"] = round(
            (ended_s if end is None else end) - episode["startedAtS"], 3
        )
        episode["exceedsBudget"] = episode["recoveryS"] > budget_s
    return episodes


def _nearest_up(
    samples: list[ObserveSample], index: int, field: str, step: int
) -> ObserveSample | None:
    """Nearest sample before (step -1) or after (+1) with the link up and
    ``field`` present."""
    stop = len(samples) if step > 0 else -1
    return next(
        (
            samples[j]
            for j in range(index + step, stop, step)
            if not samples[j].down and getattr(samples[j], field) is not None
        ),
        None,
    )


def find_natural_reconnects(samples: list[ObserveSample]) -> list[dict[str, Any]]:
    """One entry per ``reconnectsTotal`` increase between consecutive readings.

    Entries carry the keys of the driver's forced-reconnect entries that the
    stability checks read. Pre is the FD / task count at the nearest up
    sample before the sample that showed the increase, post the count at the
    first up sample after it.
    """
    entries: list[dict[str, Any]] = []
    previous: int | None = None
    for i, sample in enumerate(samples):
        total = sample.reconnects_total
        if total is None:
            continue
        if previous is not None and total > previous:
            entry: dict[str, Any] = {
                "startedAtS": round(sample.t_s, 3),
                "recovered": any(not later.down for later in samples[i + 1 :]),
            }
            for name, field in (("fds", "fds"), ("tasks", "asyncio_tasks")):
                pre = _nearest_up(samples, i, field, -1)
                post = _nearest_up(samples, i, field, +1)
                entry[f"{name}Pre"] = getattr(pre, field) if pre else None
                entry[f"{name}Post"] = getattr(post, field) if post else None
            entries.append(entry)
        previous = total
    return entries


def _drift_criterion(
    samples: list[ObserveSample],
    reconnects: list[dict[str, Any]],
    name: str,
    field: str,
    warmup_s: float,
) -> dict[str, Any]:
    """Natural-reconnect checks plus first-vs-last over the up samples after
    warm-up."""
    checks = [
        {**check, "kind": "naturalReconnect"}
        for check in _stability_checks(reconnects, name)
    ]
    values = [
        getattr(s, field)
        for s in samples
        if s.t_s >= warmup_s and not s.down and getattr(s, field) is not None
    ]
    if len(values) >= 2:
        delta = values[-1] - values[0]
        checks.append(
            {
                "kind": "firstVsLast",
                "pre": values[0],
                "post": values[-1],
                "delta": delta,
                "ok": abs(delta) <= soak_driver.STABILITY_TOLERANCE,
            }
        )
    return _stability_criterion(checks)


def build_report(
    cfg: ObserveConfig,
    samples: list[ObserveSample],
    ended_s: float,
    started_at: str,
    interrupted_by: str | None,
    error: str | None = None,
) -> dict[str, Any]:
    """The driver's report keys plus ``mode``, ``criteriaEvaluated`` and
    ``episodes``, evaluated over what was sampled.

    The RSS fit is the simulator soak's (``evaluate_report``) with no
    reconnect windows excluded.
    """
    warmup_s = min(cfg.warmup_s, ended_s * 0.25)
    reconnects = find_natural_reconnects(samples)
    episodes = find_episodes(samples, ended_s, cfg.recovery_budget_s)
    over_budget = sum(1 for e in episodes if e["exceedsBudget"])
    fit = SoakConfig(duration_s=ended_s, rss_warmup_s=cfg.warmup_s)
    criteria = {
        "rssGrowth": evaluate_report(fit, samples, [])["rssGrowth"],
        "fdsStability": _drift_criterion(samples, reconnects, "fds", "fds", warmup_s),
        "tasksStability": _drift_criterion(
            samples, reconnects, "tasks", "asyncio_tasks", warmup_s
        ),
        "recovery": {
            "status": "NOT_EVALUATED"
            if not samples
            else ("FAIL" if over_budget else "PASS"),
            # The driver's keys: here "forced" counts down episodes and
            # "unrecovered" those longer than the budget.
            "forced": len(episodes),
            "unrecovered": over_budget,
            "budgetS": cfg.recovery_budget_s,
        },
    }
    evaluated = [n for n, c in criteria.items() if c["status"] != "NOT_EVALUATED"]
    if error is None and interrupted_by is not None:
        error = (
            f"interrupted by {interrupted_by} after {ended_s:.0f}s"
            f" of {cfg.duration_s:.0f}s"
        )
    if error is None and not evaluated:
        error = "no criterion could be evaluated"
    return {
        "schemaVersion": 1,
        "mode": "observe",
        "verdict": _verdict(criteria, error),
        "startedAt": started_at,
        "durationConfiguredS": cfg.duration_s,
        "durationActualS": round(ended_s, 3),
        "error": error,
        "counts": {
            "samples": len(samples),
            "unreachableSamples": sum(1 for s in samples if not s.reachable),
        },
        "criteria": criteria,
        "criteriaEvaluated": evaluated,
        "reconnects": reconnects,
        "episodes": episodes,
        "samples": [sample.to_dict() for sample in samples],
    }


async def _sample_loop(
    cfg: ObserveConfig, samples: list[ObserveSample], t0: float
) -> None:
    # force_close: every request opens its own connection.
    async with aiohttp.ClientSession(
        connector=aiohttp.TCPConnector(force_close=True)
    ) as session:
        client = GetOnlyClient(session, cfg.base_url)
        next_due = 0.0
        while time.monotonic() - t0 < cfg.duration_s:
            t_s = time.monotonic() - t0
            runtime = await client.get_json(RUNTIME_PATH)
            sample = parse_sample(t_s, runtime, await client.get_json(READY_PATH))
            samples.append(sample)
            next_due += cfg.interval_s
            pause = min(next_due, cfg.duration_s) - (time.monotonic() - t0)
            if pause > 0:
                await asyncio.sleep(pause)
            else:
                next_due = time.monotonic() - t0


async def run_observe(cfg: ObserveConfig) -> dict[str, Any]:
    """Observe until the duration ends or SIGINT / SIGTERM, then write the report."""
    started_at = datetime.now(timezone.utc).isoformat()
    samples: list[ObserveSample] = []
    t0 = time.monotonic()
    task = asyncio.create_task(_sample_loop(cfg, samples, t0), name="soak-observe")
    interrupted: list[str] = []
    loop = asyncio.get_running_loop()

    def on_signal(name: str) -> None:
        interrupted.append(name)
        task.cancel()

    installed: list[signal.Signals] = []
    for sig in (signal.SIGINT, signal.SIGTERM):
        with contextlib.suppress(NotImplementedError, RuntimeError, ValueError):
            loop.add_signal_handler(sig, on_signal, sig.name)
            installed.append(sig)
    error: str | None = None
    try:
        await task
    except asyncio.CancelledError:
        if not interrupted:
            raise
    except Exception as exc:
        logger.exception("soak observer failed")
        error = f"{type(exc).__name__}: {exc}"
    finally:
        for sig in installed:
            loop.remove_signal_handler(sig)
    report = build_report(
        cfg,
        samples,
        time.monotonic() - t0,
        started_at,
        interrupted[0] if interrupted else None,
        error,
    )
    if cfg.report_path:
        Path(cfg.report_path).write_text(
            json.dumps(report, indent=2) + "\n", encoding="utf-8"
        )
    return report


def add_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--observe-url",
        help="observe an already running server with GET requests only,"
        " instead of running the simulator soak",
    )
    group = parser.add_argument_group("observe mode (with --observe-url)")
    group.add_argument(
        "--interval-s",
        type=float,
        default=soak_driver.METRICS_INTERVAL_S,
        help="seconds between samples (default %(default)s)",
    )
    group.add_argument(
        "--recovery-budget-s",
        type=float,
        default=OBSERVE_RECOVERY_BUDGET_S,
        help="longest tolerated link-down episode (default %(default)s)",
    )


def main_from_args(args: argparse.Namespace, parser: argparse.ArgumentParser) -> int:
    parts = urllib.parse.urlsplit(args.observe_url)
    if parts.scheme != "http" or not parts.hostname or parts.path not in ("", "/"):
        parser.error("--observe-url must look like http://host:port")
    cfg = ObserveConfig(
        base_url=f"http://{parts.netloc}",
        duration_s=args.duration,
        interval_s=args.interval_s,
        recovery_budget_s=args.recovery_budget_s,
        warmup_s=soak_driver.RSS_WARMUP_S,
        report_path=args.report,
    )
    if min(cfg.duration_s, cfg.interval_s) <= 0:
        parser.error("--duration and --interval-s must be positive")
    if not os.access(Path(args.report).resolve().parent, os.W_OK):
        parser.error(f"cannot write the report to {args.report}")
    report = asyncio.run(run_observe(cfg))
    print(soak_driver.summary_line(report))
    print(f"criteria evaluated: {', '.join(report['criteriaEvaluated']) or 'none'}")
    print(f"soak report written to {args.report}")
    return 0 if report["verdict"] == "PASS" else 1
