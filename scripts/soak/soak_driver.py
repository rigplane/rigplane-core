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

Exit status is 0 on a PASS verdict and 1 otherwise: FAIL, or INCOMPLETE (no
FAIL, but not every criterion PASSed). Run from the repository root (the
simulator stack imports the top-level ``rigplane_testkit`` package)::

    uv run python -m scripts.soak.soak_driver --duration 3600
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import gc
import json
import logging
import os
import random
import secrets
import socket
import stat
import sys
import tempfile
import time
import weakref
from collections import Counter
from contextvars import Context, ContextVar
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, AsyncIterator, Callable

import aiohttp

from rigplane.rigctld.contract import RigctldConfig
from rigplane.rigctld.server import RigctldServer
from rigplane.runtime.controller_authority import ControllerAuthority
from rigplane.runtime.managed_tx_composition import (
    ManagedTxComposition,
    install_managed_tx_composition,
)
from rigplane.runtime.radio import IcomRadio
from rigplane.web.server import WebConfig, WebServer
from rigplane.web.web_startup import (
    attach_managed_tx_composition,
    prepare_managed_tx_observation_generation,
)
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
# Process-wide counts — a remote server's pid observed through the
# runtime document — carry churn this run did not create, so their
# comparison keeps a tolerance (MOR-3575).
STABILITY_TOLERANCE = 5
# In-process owned sockets: the forced reconnect replaces the radio's
# connection sockets one-for-one and the listeners persist, so a
# surviving extra owned socket is a leak — the comparison is exact
# (measured: 0 nonzero deltas in 90 end-to-end reconnects, MOR-3575).
IN_PROCESS_FD_STABILITY_TOLERANCE = 0
# In-process owned tasks: the forced reconnect tears down and recreates
# every transport loop set (ping/retransmit/idle/rx loops across three
# transports, the commander, the token renewer, on-demand web and
# stdlib helper tasks), so the probe-time count legitimately differs by
# the rebuild's last spawn/drain step in either direction. Measured:
# |delta| <= 2 in 90 end-to-end reconnects (0:41, +-1:44, +-2:5); two
# is the smallest tolerance that evidence justifies. Removing the churn
# by identity would have to exclude the stack's own task classes — the
# leak surface this criterion watches — or need creation-time ancestry.
IN_PROCESS_TASK_STABILITY_TOLERANCE = 2
_STABILITY_PROBE_COUNT = 3
_TRANSIENT_DRAIN_TIMEOUT_S = 40.0
# A post-reconnect median above pre + tolerance may be teardown still
# draining (MOR-3575): settle first — gc once, then resample in small
# steps until _STABILITY_SETTLE_STABLE_READINGS consecutive resamples
# show no decrease and no surplus over pre — and take the median again.
# A plateau still above tolerance keeps sampling until the window (or
# its resample budget) expires; the caller always re-probes, so a
# persistent surplus still fails. The window bounds the settle wait (its
# samples and step sleeps), not the median re-probe after it.
_STABILITY_SETTLE_WINDOW_S = 5.0
_STABILITY_SETTLE_STEP_S = 0.25
_STABILITY_SETTLE_STABLE_READINGS = 3
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
# Two controller sessions key against each other this often (MOR-3538).
TX_SESSIONS_INTERVAL_S = 20.0
# How long the winning session holds the transmitter before the cycle ends it.
TX_HOLD_S = 1.0
# TX must be off this long after the holder ends, in any of the four modes. The
# slowest is a silent holder: its lease (ControllerAuthority.TTL_MS) counts from
# its grant and the watchdog ticks every ControllerAuthority.HEARTBEAT_MS; the
# rest of the budget is the release actuation and slack.
TX_OFF_BUDGET_S = 15.0
# How a cycle ends the winner's transmission: it unkeys, its session is
# deleted, its link is cut without a close, or it goes silent with the link open.
_TX_END_MODES = ("unkey", "delete", "cut", "half_open")
_TX_VIOLATIONS_KEPT = 20
# How long a lease renewal before the close waits for the server's answer.
_TX_RENEW_TIMEOUT_S = 2.0
# After a cycle closes its clients no session may remain this long. The cycle
# renews every live lease just before it closes (_TxSessionClient.renew), so a
# session kept only by its lease stays about TTL_MS; the wait is HEARTBEAT_MS
# shorter, which leaves a margin of that much: one still there then was not
# ended by its channel's loss, whatever phase the lease and the server's
# expiry tick had.
_TX_SESSION_END_S = (
    ControllerAuthority.TTL_MS - ControllerAuthority.HEARTBEAT_MS
) / 1000

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
    tx_sessions_interval_s: float = TX_SESSIONS_INTERVAL_S
    probe_diagnostics: bool = False
    task_provenance: bool = False


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

_DIAGNOSTIC_FIELDS = ("rssBytes", "fds", "asyncioTasks", "connection")
_DIAGNOSTIC_ROUTES = frozenset({"runtime", "driver", "state", "missing"})
_DIAGNOSTIC_REASONS = frozenset(
    {
        "runtime_unavailable",
        "process_missing",
        "process_invalid",
        "field_missing",
        "field_null",
        "field_invalid",
        "state_unavailable",
    }
)
_DIAGNOSTIC_BOUNDARIES = frozenset(
    {
        "cycleStart",
        "disconnectStart",
        "disconnectEnd",
        "recoveryStart",
        "recoveryEnd",
        "settleStart",
        "settleEnd",
        "cycleEnd",
    }
)


def _diagnostic_sample_values(sample: ProcessSample) -> dict[str, Any]:
    return {
        "t": sample.t_s,
        "rssBytes": sample.rss_bytes,
        "fds": sample.fds,
        "asyncioTasks": sample.asyncio_tasks,
        "connection": (
            sample.connection
            if sample.connection in {"connected", "disconnected"}
            else None
        ),
    }


@dataclass
class _ProbeCapture:
    sequence: int
    phase: str
    started: float
    ended: float | None = None
    sample: dict[str, Any] | None = None
    selections: dict[str, Any] | None = None

    def select(self, field: str, route: str, reason: str | None) -> None:
        if field not in _DIAGNOSTIC_FIELDS or route not in _DIAGNOSTIC_ROUTES:
            raise ValueError("invalid diagnostic selection")
        if reason is not None and reason not in _DIAGNOSTIC_REASONS:
            raise ValueError("invalid diagnostic missing reason")
        assert self.selections is not None
        self.selections[field] = {
            "route": route,
            "runtimeMissingReason": reason,
            "fallbackMissingReason": (
                "measurement_unavailable"
                if route == "missing" and field != "connection"
                else None
            ),
        }


class _ProbeDiagnostics:
    def __init__(self) -> None:
        self.probes: list[_ProbeCapture] = []
        self.medians: dict[str, Any] = {"pre": None, "post": None}
        self.boundaries: dict[str, float] = {}
        self.outcome = "not_started"

    def boundary(self, name: str) -> None:
        if name not in _DIAGNOSTIC_BOUNDARIES:
            raise ValueError("invalid diagnostic boundary")
        self.boundaries[name] = time.monotonic()

    def begin(self, phase: str) -> _ProbeCapture:
        if phase not in {"pre", "post"}:
            raise ValueError("invalid diagnostic phase")
        if len(self.probes) >= 6 or sum(p.phase == phase for p in self.probes) >= 3:
            raise ValueError("diagnostic probe limit")
        probe = _ProbeCapture(
            len(self.probes) + 1, phase, time.monotonic(), selections={}
        )
        self.probes.append(probe)
        return probe

    def drop_phase(self, phase: str) -> None:
        """Discard the retained probes of one phase, freeing its budget.

        When the first post median shows a surplus the cycle settles and
        takes the median again (MOR-3575): the transient trio is dropped
        so the settled re-probe fits the six-probe bound. The median goes
        with it; the re-probe sets the settled one.
        """
        if phase not in {"pre", "post"}:
            raise ValueError("invalid diagnostic phase")
        self.probes = [p for p in self.probes if p.phase != phase]
        self.medians[phase] = None

    def to_dict(self) -> dict[str, Any]:
        phases = {}
        for phase in ("pre", "post"):
            attempted = sum(p.phase == phase for p in self.probes)
            completed = sum(
                p.phase == phase and p.sample is not None for p in self.probes
            )
            phases[phase] = {
                "status": (
                    "complete"
                    if completed == 3
                    else "partial"
                    if attempted
                    else "absent"
                ),
                "attempted": attempted,
                "completed": completed,
                "median": self.medians[phase],
            }
        return {
            "schemaVersion": 1,
            "selectedReconnect": 1,
            "maxProbes": 6,
            "outcome": self.outcome,
            "boundariesMonotonicS": dict(self.boundaries),
            "phases": phases,
            "probes": [
                {
                    "sequence": p.sequence,
                    "phase": p.phase,
                    "startedMonotonicS": p.started,
                    "endedMonotonicS": p.ended,
                    "sample": p.sample,
                    "selections": p.selections,
                }
                for p in self.probes
            ],
        }


def _runtime_missing_reason(runtime: dict[str, Any] | None, field: str) -> str | None:
    if runtime is None:
        return "runtime_unavailable"
    if "process" not in runtime:
        return "process_missing"
    proc = runtime["process"]
    if not isinstance(proc, dict):
        return "process_invalid"
    if field not in proc:
        return "field_missing"
    if proc[field] is None:
        return "field_null"
    if _proc_int(proc, field) is None:
        return "field_invalid"
    return None


_TASK_ROLES = frozenset({"radio.civ_rx", "radio.watchdog", "web.client"})
_TASK_HEADER = "x-rigplane-soak-probe"
_TASK_REQUEST: ContextVar[dict[str, str] | None] = ContextVar(
    "soak_task_request", default=None
)
_TASK_ENTRY_LIMIT = 128
_TASK_EVENT_LIMIT = 256
_TASK_REGISTRY_LIMIT = 256


def _task_counted(task: asyncio.Task[Any]) -> bool:
    return not task.get_name().startswith(_DRIVER_TASK_PREFIX)


class _TaskProvenance:
    def __init__(self) -> None:
        self.active: dict[str, Any] | None = None
        self.first_failure: dict[str, Any] | None = None
        self._registry = weakref.WeakKeyDictionary()
        self._callbacks = weakref.WeakKeyDictionary()
        self._next_label = 0
        self._generation = 0
        self._loop: asyncio.AbstractEventLoop | None = None

    def begin_cycle(self, reconnect: int) -> None:
        self._release_callbacks()
        self._loop = asyncio.get_running_loop()
        self.active = {
            "reconnect": reconnect,
            "outcome": "in_progress",
            "taskStability": None,
            "probes": [],
            "events": [],
            "completionCoverage": "observed_only",
            "labelMapCoverage": "complete",
        }

    def begin_probe(self, phase: str) -> dict[str, Any]:
        if self.active is None or phase not in {"pre", "post"}:
            raise ValueError("invalid task provenance phase")
        probes = self.active["probes"]
        if len(probes) >= 6 or sum(p["phase"] == phase for p in probes) >= 3:
            raise ValueError("task provenance probe limit")
        probe = {"sequence": len(probes) + 1, "phase": phase, "snapshot": None}
        probes.append(probe)
        return probe

    def drop_phase(self, phase: str) -> None:
        """Discard the retained probes of one phase, freeing its budget.

        When the first post median shows a surplus the cycle settles and
        takes the median again (MOR-3575): the transient trio is dropped
        so the settled re-probe fits the six-probe bound. Task labels and
        completion events are per-cycle tracking, not per-probe, so they
        stay.
        """
        if self.active is None or phase not in {"pre", "post"}:
            raise ValueError("invalid task provenance phase")
        self.active["probes"] = [
            p for p in self.active["probes"] if p["phase"] != phase
        ]

    def unknown(
        self,
        probe: dict[str, Any],
        route: str,
        measured: int | None,
        reason: str = "correlation_missing",
    ) -> None:
        probe["snapshot"] = {
            "route": route,
            "measuredCount": measured,
            "identityEligible": False,
            "coverage": reason,
            "countAgreement": None,
            "tasks": [],
        }

    def capture(
        self,
        probe: dict[str, Any],
        route: str,
        measured: int,
        *,
        tasks: set[asyncio.Task[Any]] | None = None,
        bindings: list[tuple[asyncio.Task[Any], str]] | tuple[Any, ...] = (),
    ) -> None:
        if self.active is None or route not in {"runtime", "driver"}:
            raise ValueError("invalid task provenance capture")
        population = asyncio.all_tasks() if tasks is None else tasks
        now = time.monotonic()
        roles: dict[asyncio.Task[Any], set[str]] = {}
        for task, role in bindings:
            if role in _TASK_ROLES:
                roles.setdefault(task, set()).add(role)
        inventory_count = sum(
            route == "runtime" or _task_counted(t) for t in population
        )
        entries = []
        truncated = len(population) > _TASK_ENTRY_LIMIT
        for task in population:
            if len(entries) >= _TASK_ENTRY_LIMIT:
                break
            metadata = self._registry.get(task)
            if metadata is None:
                if len(self._registry) >= _TASK_REGISTRY_LIMIT:
                    truncated = True
                    self.active["labelMapCoverage"] = "truncated"
                    continue
                self._next_label += 1
                metadata = (self._next_label, now)
                self._registry[task] = metadata
            label, first = metadata
            if task not in self._callbacks:
                sink = weakref.ref(self)
                generation = self._generation

                def done(
                    finished: asyncio.Task[Any],
                    *,
                    label: int = label,
                    generation: int = generation,
                    sink: Any = sink,
                ) -> None:
                    collector = sink()
                    if collector is None or collector._generation != generation:
                        return
                    active = collector.active
                    if active is None:
                        return
                    if len(active["events"]) >= _TASK_EVENT_LIMIT:
                        active["completionCoverage"] = "truncated"
                        return
                    active["events"].append(
                        {
                            "taskLabel": label,
                            "observedMonotonicS": time.monotonic(),
                            "done": finished.done(),
                            "cancelled": finished.cancelled(),
                        }
                    )

                self._callbacks[task] = done
                task.add_done_callback(done, context=Context())
            bound = roles.get(task, set())
            entries.append(
                {
                    "taskLabel": label,
                    "firstObservedMonotonicS": first,
                    "cancellingCount": task.cancelling(),
                    "role": next(iter(bound)) if len(bound) == 1 else "unclassified",
                    "soakPrefix": not _task_counted(task),
                    "countedByRoute": route == "runtime" or _task_counted(task),
                }
            )
        probe["snapshot"] = {
            "route": route,
            "producerPid": os.getpid(),
            "producerLoopLabel": (
                1 if asyncio.get_running_loop() is self._loop else None
            ),
            "observedMonotonicS": now,
            "measuredCount": measured,
            "inventoryCount": inventory_count,
            "countAgreement": measured == inventory_count,
            "identityEligible": (
                measured == inventory_count and asyncio.get_running_loop() is self._loop
            ),
            "coverage": "truncated" if truncated else "complete",
            "roleCoverage": (
                "complete"
                if not truncated
                and all(e["role"] != "unclassified" for e in entries)
                and all(len(r) == 1 for r in roles.values())
                else "unknown"
            ),
            "lifetimeCoverage": "observed_only",
            "roleMultiplicity": dict(Counter(e["role"] for e in entries)),
            "tasks": entries,
        }

    def fallback(self, probe: dict[str, Any]) -> int:
        population = asyncio.all_tasks()
        measured = sum(_task_counted(task) for task in population)
        self.capture(probe, "driver", measured, tasks=population)
        return measured

    def _release_callbacks(self) -> None:
        self._generation += 1
        for task, callback in list(self._callbacks.items()):
            task.remove_done_callback(callback)
        self._callbacks.clear()

    def close(self) -> None:
        self._release_callbacks()
        self._registry.clear()

    def finish_cycle(self, entry: dict[str, Any], outcome: str) -> None:
        self._release_callbacks()
        assert self.active is not None
        self.active["outcome"] = outcome
        checks = _stability_checks([entry], "tasks")
        if not checks:
            if outcome == "recovered":
                self.active["outcome"] = "missing_task_measurement"
            return
        self.active["taskStability"] = checks[0]
        if not checks[0]["ok"] and self.first_failure is None:
            self.first_failure = json.loads(json.dumps(self.active))
        self.active = None

    def to_dict(self) -> dict[str, Any]:
        return json.loads(
            json.dumps(
                {
                    "schemaVersion": 1,
                    "maxProbes": 6,
                    "maxTaskEntries": _TASK_ENTRY_LIMIT,
                    "maxEvents": _TASK_EVENT_LIMIT,
                    "maxTrackedTasks": _TASK_REGISTRY_LIMIT,
                    "active": self.active,
                    "firstFailure": self.first_failure,
                }
            )
        )


class _RuntimeTaskAdapter:
    """In-process overlay keeping every count on one owned population.

    The runtime payload and the driver-side fallbacks in
    ``ProcessMetricsSampler.sample`` all read the same population: socket
    FDs on the harness-created ports, and tasks attributed by run
    ownership (pending tasks that appeared after the run's baseline,
    minus the driver's ``soak-`` prefixed tasks) — never by coroutine
    origin module. With a provenance collector it also correlates one
    request per probe for task capture, as before.
    """

    def __init__(
        self,
        stack: Any,
        collector: _TaskProvenance | None,
        task_baseline: frozenset[asyncio.Task[Any]] | None = None,
    ) -> None:
        self._stack = stack
        self._collector = collector
        self._ports = _run_owned_ports(stack)
        # Identities alive before the run started (``run_soak`` snapshots
        # before starting the stack, so the stack's tasks are run-owned).
        self._task_baseline = (
            frozenset(asyncio.all_tasks()) if task_baseline is None else task_baseline
        )
        self._loop = asyncio.get_running_loop()
        self._pid = os.getpid()
        self._pending: dict[str, dict[str, Any]] = {}
        self._producer: ContextVar[dict[str, Any] | None] = ContextVar(
            "soak_task_producer", default=None
        )
        self._probe_conn: ContextVar[frozenset[int] | None] = ContextVar(
            "soak_probe_conn", default=None
        )
        self._installed = False

    def owned_task_population(self) -> set[asyncio.Task[Any]]:
        """Pending tasks the run owns, attributed by creation not origin.

        Run-owned: appeared after the baseline (the stack's startup
        tasks, per-request handlers, and standard-library helpers the
        run's tasks spawned, whatever module defines the coroutine) and
        not one of the driver's ``soak-`` prefixed traffic tasks.
        """
        return {
            task
            for task in asyncio.all_tasks()
            if task not in self._task_baseline and _task_counted(task)
        }

    def owned_fd_count(self) -> int | None:
        """The owned-socket FD count; None when it cannot be measured."""
        return _owned_socket_fd_count(self._ports) if self._ports else None

    def install(self) -> None:
        web = self._stack.web
        if any(
            name in vars(web) for name in ("_serve_runtime", "_runtime_process_payload")
        ):
            return
        original_handler = web._serve_runtime
        original_payload = web._runtime_process_payload

        async def handler(writer: Any, headers: dict[str, str] | None = None) -> None:
            probe = self._pending.pop((headers or {}).get(_TASK_HEADER, ""), None)
            state = {"probe": probe, "calls": 0} if probe is not None else None
            context = self._producer.set(state)
            conn = self._probe_conn.set(_probe_connection_ports(writer))
            try:
                await original_handler(writer, headers)
            finally:
                self._probe_conn.reset(conn)
                self._producer.reset(context)

        def payload() -> dict[str, Any]:
            result = original_payload()
            # The probe's own instruments stay out of the measurement:
            # the request's handler task (this very task — the fallback
            # route runs in a ``soak-`` task and never has it) and the
            # probe connection's own two sockets, else a
            # runtime-pre/fallback-post pair subtracts them from a real
            # leak's delta (MOR-3575).
            handler_task = asyncio.current_task()
            owned_tasks = {
                task
                for task in self.owned_task_population()
                if task is not handler_task
            }
            if _proc_int(result, "asyncioTasks") is not None:
                result["asyncioTasks"] = len(owned_tasks)
            fds = (
                _owned_socket_fd_count(self._ports, skip_ports=self._probe_conn.get())
                if self._ports
                else None
            )
            if fds is not None and _proc_int(result, "openFds") is not None:
                result["openFds"] = fds
            state = self._producer.get()
            if state is not None:
                probe = state["probe"]
                state["calls"] += 1
                count = _proc_int(result, "asyncioTasks")
                if state["calls"] != 1:
                    self._collector.unknown(
                        probe, "runtime", count, "correlation_ambiguous"
                    )
                elif (
                    count is not None
                    and os.getpid() == self._pid
                    and asyncio.get_running_loop() is self._loop
                ):
                    try:
                        bindings = []
                        for name, role in (
                            ("_civ_rx_task", "radio.civ_rx"),
                            ("_watchdog_task", "radio.watchdog"),
                        ):
                            task = getattr(self._stack.radio, name, None)
                            if isinstance(task, asyncio.Task):
                                bindings.append((task, role))
                        bindings.extend(
                            (task, "web.client")
                            for task in getattr(web, "_client_tasks", ())
                            if isinstance(task, asyncio.Task)
                        )
                        self._collector.capture(
                            probe,
                            "runtime",
                            count,
                            tasks=owned_tasks,
                            bindings=bindings,
                        )
                    except Exception:
                        self._collector.unknown(probe, "runtime", count)
                else:
                    self._collector.unknown(probe, "runtime", count)
            return result

        try:
            web._serve_runtime = handler
            web._runtime_process_payload = payload
        except Exception:
            for name in ("_serve_runtime", "_runtime_process_payload"):
                if name in vars(web):
                    delattr(web, name)
            return
        self._installed = True

    @contextlib.contextmanager
    def request(self, probe: dict[str, Any]) -> Any:
        if self._pending or asyncio.get_running_loop() is not self._loop:
            context = _TASK_REQUEST.set(None)
            try:
                yield {}
            finally:
                _TASK_REQUEST.reset(context)
            return
        token = secrets.token_hex(16)
        headers = {_TASK_HEADER: token}
        self._pending[token] = probe
        context = _TASK_REQUEST.set(headers)
        try:
            yield headers
        finally:
            _TASK_REQUEST.reset(context)
            self._pending.pop(token, None)

    def close(self) -> None:
        if self._installed:
            del self._stack.web._serve_runtime
            del self._stack.web._runtime_process_payload
            self._installed = False
        self._pending.clear()


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
    except asyncio.CancelledError:
        # A caller-side bound (the settle window's timeout giving up
        # on this sample) cancels us mid-communicate: the ps child
        # must not outlive the call, so kill and reap it before
        # letting the cancellation propagate.
        with contextlib.suppress(ProcessLookupError):
            proc.kill()
        await proc.wait()
        raise
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


def _socket_port(name: Any) -> int | None:
    """The port of a socket address, or None for non-internet names."""
    if isinstance(name, tuple) and len(name) >= 2 and type(name[1]) is int:
        return name[1]
    return None


def _run_owned_ports(stack: Any) -> frozenset[int]:
    """Bound ports of the listeners the in-process harness itself created."""
    sim = getattr(stack, "sim", None)
    candidates = [
        getattr(stack, "web_port", None),
        getattr(stack, "rigctld_port", None),
        *(
            getattr(sim, name, None)
            for name in ("control_port", "civ_port", "audio_port")
        ),
    ]
    return frozenset(
        port for port in candidates if type(port) is int and 0 < port < 65536
    )


def _probe_connection_ports(writer: Any) -> frozenset[int] | None:
    """The two endpoint ports of the probe request's own connection.

    The handler's ``writer`` carries the server side. Port numbers alone
    do not identify a connection across addresses and protocols, so the
    pair is only a best-effort match for the probe's sockets, not an
    exact connection identity. ``None`` when unreadable.
    """
    try:
        sock = None if writer is None else writer.get_extra_info("socket")
        local = _socket_port(sock.getsockname())
        peer = _socket_port(sock.getpeername())
    except (AttributeError, OSError):
        return None
    if local is None or peer is None:
        return None
    return frozenset((local, peer))


def _owned_socket_fd_count(
    ports: frozenset[int], skip_ports: frozenset[int] | None = None
) -> int | None:
    """Socket FDs whose local or peer port is one of ``ports``.

    Sockets on the harness-created ports are the run's stability signal;
    the count makes no claim about the rest of the process. ``None``
    when no FD directory can be listed, so the caller keeps the
    reported value. ``skip_ports`` names the endpoint ports of one
    connection — the live probe's own request — and removes every
    socket matched by those ports (a port-based best-effort match, not
    a unique connection identity): counting the probe's sockets would
    inflate a runtime-route baseline by two FDs the fallback route
    cannot see and mask a real leak of the smallest detectable size
    (MOR-3575).
    """
    for fd_dir in ("/proc/self/fd", "/dev/fd"):
        try:
            names = os.listdir(fd_dir)
        except OSError:
            continue
        count = 0
        for name in names:
            try:
                fd = int(name)
            except ValueError:
                continue
            try:
                dup = os.dup(fd)
            except OSError:
                # The listing can name its own, already closed, directory
                # descriptor; transient reuse is skipped alike.
                continue
            try:
                if not stat.S_ISSOCK(os.fstat(dup).st_mode):
                    os.close(dup)
                    continue
                # From here the probe socket owns the dup and closes it.
                probe = socket.socket(fileno=dup)
            except OSError:
                with contextlib.suppress(OSError):
                    os.close(dup)
                continue
            local = peer = None
            with probe:
                with contextlib.suppress(OSError):
                    local = _socket_port(probe.getsockname())
                with contextlib.suppress(OSError):
                    peer = _socket_port(probe.getpeername())
            if local in ports or peer in ports:
                if skip_ports and {local, peer} == skip_ports:
                    continue
                count += 1
        return count
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
    return sum(1 for task in asyncio.all_tasks() if _task_counted(task))


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
    RSS via ``/proc`` or ``ps``; FDs and tasks — with the overlay
    installed — from its owned population, the same population the
    runtime payload reports (``owned_fd_count`` /
    ``owned_task_population``), without it via ``/proc`` or ``/dev/fd``
    and ``_server_asyncio_task_count``. The connection column comes
    from the state document (``_state_connection_status``), not the
    runtime payload.
    """

    def __init__(self, session: aiohttp.ClientSession, base_url: str) -> None:
        self._session = session
        self._base_url = base_url
        self._task_provenance: _TaskProvenance | None = None
        self._runtime_task_adapter: _RuntimeTaskAdapter | None = None

    async def _get_json(
        self, path: str, timeout_s: float = 5.0
    ) -> dict[str, Any] | None:
        # Best-effort: a poll must never crash a cycle. Besides network
        # errors this also swallows the connector's transient
        # "File descriptor ... is used by transport" RuntimeError seen
        # under connection churn against the simulator.
        assert self._session is not None
        try:
            kwargs: dict[str, Any] = {}
            if path == "/api/v1/runtime" and _TASK_REQUEST.get() is not None:
                kwargs["headers"] = _TASK_REQUEST.get()
            async with self._session.get(
                f"{self._base_url}{path}",
                timeout=aiohttp.ClientTimeout(total=timeout_s),
                **kwargs,
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

    async def sample(
        self,
        t_s: float,
        capture: _ProbeCapture | None = None,
        *,
        task_probe: dict[str, Any] | None = None,
    ) -> ProcessSample:
        collector = self._task_provenance if task_probe is not None else None
        adapter = self._runtime_task_adapter
        if collector is not None and adapter is not None:
            with adapter.request(task_probe):
                runtime = await self.fetch_runtime()
        else:
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
        if capture is not None:
            for field, runtime_field, value in (
                ("rssBytes", "rssBytes", rss),
                ("fds", "openFds", fds),
                ("asyncioTasks", "asyncioTasks", tasks),
            ):
                capture.select(
                    field,
                    "runtime" if value is not None else "missing",
                    _runtime_missing_reason(runtime, runtime_field),
                )
        if rss is None or fds is None:
            # Give a just-finished or refused probe request one loop
            # pass to finish closing its transport: this reduces the
            # chance that the fallback counts the probe's own dying
            # socket, but one pass is not a guarantee of closure
            # (on a /proc host the metrics helper below takes no await
            # that would do it either).
            await asyncio.sleep(0)
            d_rss, d_fds = await _driver_side_process_metrics(os.getpid())
            if rss is None:
                rss = d_rss
                if capture is not None:
                    capture.select(
                        "rssBytes",
                        "driver" if rss is not None else "missing",
                        _runtime_missing_reason(runtime, "rssBytes"),
                    )
            if fds is None:
                # In-process run: measure the same owned-socket population
                # the runtime payload reports, so a failed runtime request
                # cannot switch populations between the pre and post probes.
                fds = adapter.owned_fd_count() if adapter is not None else None
                if fds is None:
                    fds = d_fds
                if capture is not None:
                    capture.select(
                        "fds",
                        "driver" if fds is not None else "missing",
                        _runtime_missing_reason(runtime, "openFds"),
                    )
        if tasks is None:
            if adapter is not None:
                population = adapter.owned_task_population()
                tasks = len(population)
                if collector is not None:
                    collector.capture(task_probe, "driver", tasks, tasks=population)
            elif collector is not None:
                tasks = collector.fallback(task_probe)
            else:
                tasks = _server_asyncio_task_count()
            if capture is not None:
                capture.select(
                    "asyncioTasks",
                    "driver",
                    _runtime_missing_reason(runtime, "asyncioTasks"),
                )
        elif collector is not None:
            snapshot = task_probe["snapshot"]
            if (
                snapshot is None
                or runtime.get("pid") != snapshot.get("producerPid")
                or tasks != snapshot.get("measuredCount")
            ):
                collector.unknown(task_probe, "runtime", tasks)
        sample = ProcessSample(
            t_s=t_s,
            rss_bytes=rss,
            fds=fds,
            asyncio_tasks=tasks,
            connection=await self.fetch_connection_status(),
        )
        if capture is not None:
            capture.select(
                "connection",
                "state" if sample.connection is not None else "missing",
                None if sample.connection is not None else "state_unavailable",
            )
            capture.sample = _diagnostic_sample_values(sample)
        return sample


# ---------------------------------------------------------------------------
# In-process simulator stack
# ---------------------------------------------------------------------------


@dataclass
class _SimulatorStack:
    sim: MockIcomRadio
    radio: IcomRadio
    web: WebServer
    rigctld: RigctldServer
    # Set only with managed TX: the composition and the directory holding its
    # config file, which the stack removes on close.
    composition: ManagedTxComposition | None = None
    config_dir: tempfile.TemporaryDirectory[str] | None = None

    @property
    def web_port(self) -> int:
        return self.web.port

    @property
    def rigctld_port(self) -> int:
        # RigctldServer exposes no port accessor; read the bound socket.
        return int(self.rigctld._server.sockets[0].getsockname()[1])  # noqa: SLF001

    def session_count(self) -> int:
        """Controller sessions the web server still holds."""
        return len(self.web._controller._sessions)  # noqa: SLF001

    async def aclose(self) -> None:
        await self.rigctld.stop()
        await self.web.stop()
        with contextlib.suppress(Exception):
            await self.radio.disconnect()
        if self.composition is not None:
            with contextlib.suppress(Exception):
                await self.composition.shutdown(termination=asyncio.Event())
        await self.sim.stop()
        if self.config_dir is not None:
            self.config_dir.cleanup()


async def _start_simulator_stack(managed_tx: bool = False) -> _SimulatorStack:
    """The `rigplane web` stack against the simulator.

    By default managed TX composition is absent, so the RigctldServer takes
    the same standalone wiring as `rigplane serve`, reusing the web seat's
    state services on the radio. With ``managed_tx`` the stack is composed
    the way the CLI composes it (MOR-3538): the simulator keeps a stateful
    PTT, and the composition's config file lives in a temporary directory
    the stack removes on close, so the soak never touches the user's config.
    """
    sim = MockIcomRadio(
        single_owner=True,
        unsolicited_civ=True,
        unsolicited_interval_s=0.1,
        enable_audio=True,
        stateful_ptt=managed_tx,
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
    composition = None
    config_dir: tempfile.TemporaryDirectory[str] | None = None
    if managed_tx:
        config_dir = tempfile.TemporaryDirectory(prefix="rigplane-soak-")
        composition = ManagedTxComposition(
            radio, config_path=Path(config_dir.name) / "managed-tx.json"
        )
        install_managed_tx_composition(radio, composition)
    await radio.connect()
    web = WebServer(radio, WebConfig(host="127.0.0.1", port=0, discovery=False))
    # command_timeout above the 2 s default: with the web poller sharing
    # the CI-V lane, a set under acquisition contention can exceed it.
    rigctld_config = RigctldConfig(host="127.0.0.1", port=0, command_timeout=10.0)
    if composition is None:
        await web.start()
        rigctld = RigctldServer(radio, rigctld_config)
        await rigctld.start()
    else:
        prepare_managed_tx_observation_generation(web)
        await composition.bind_state_store(web.command_state_store)
        attach_managed_tx_composition(web, composition)
        rigctld = RigctldServer(
            radio,
            rigctld_config,
            allow_not_ready=True,
            managed_tx_authority=composition.authority,
            command_queue=web.command_queue,
            command_service=web.command_service,
        )
        await rigctld.start()
        web.attach_rigctl_channel(rigctld.open_channel)
        await web.start()
    return _SimulatorStack(
        sim=sim,
        radio=radio,
        web=web,
        rigctld=rigctld,
        composition=composition,
        config_dir=config_dir,
    )


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


def _entry_tolerance(entry: dict[str, Any], field: str) -> int:
    """The comparison tolerance one reconnect entry's field was measured under.

    ``ownedPopulation`` marks the in-process run's counts (the adapter's
    owned population): exact for sockets, within the measured rebuild
    bound for tasks. Anything else — the remote-pid path — keeps the
    process-wide tolerance.
    """
    if entry.get("ownedPopulation"):
        if field == "fds":
            return IN_PROCESS_FD_STABILITY_TOLERANCE
        return IN_PROCESS_TASK_STABILITY_TOLERANCE
    return STABILITY_TOLERANCE


def _run_tolerance(reconnects: list[dict[str, Any]], field: str) -> int:
    """The tolerance a whole report's counts were measured under."""
    for entry in reconnects:
        if entry.get("ownedPopulation"):
            return _entry_tolerance(entry, field)
    return STABILITY_TOLERANCE


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
                "ok": abs(delta) <= _entry_tolerance(entry, field),
            }
        )
    return checks


def _stability_criterion(
    checks: list[dict[str, Any]], tolerance: int = STABILITY_TOLERANCE
) -> dict[str, Any]:
    if checks:
        status = "PASS" if all(check["ok"] for check in checks) else "FAIL"
    else:
        status = "NOT_EVALUATED"
    return {"status": status, "tolerance": tolerance, "checks": checks}


def _stability_counts_decreased(before: ProcessSample, after: ProcessSample) -> bool:
    """Either stability count strictly lower; a missing field never counts."""
    for field in ("fds", "asyncio_tasks"):
        old = getattr(before, field)
        new = getattr(after, field)
        if old is not None and new is not None and new < old:
            return True
    return False


def _stability_surplus(
    pre: ProcessSample, post: ProcessSample, owned_counts: bool
) -> bool:
    """Either post count above pre + its tolerance; missing fields never count.

    ``owned_counts`` selects the in-process tolerances (exact for owned
    sockets, the rebuild bound for owned tasks); the process-wide
    tolerance otherwise.
    """
    fds_tolerance, tasks_tolerance = (
        (IN_PROCESS_FD_STABILITY_TOLERANCE, IN_PROCESS_TASK_STABILITY_TOLERANCE)
        if owned_counts
        else (STABILITY_TOLERANCE, STABILITY_TOLERANCE)
    )
    if (
        pre.fds is not None
        and post.fds is not None
        and post.fds - pre.fds > fds_tolerance
    ):
        return True
    if (
        pre.asyncio_tasks is not None
        and post.asyncio_tasks is not None
        and post.asyncio_tasks - pre.asyncio_tasks > tasks_tolerance
    ):
        return True
    return False


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
        "fdsStability": _stability_criterion(
            fds_checks, _run_tolerance(reconnects, "fds")
        ),
        "tasksStability": _stability_criterion(
            tasks_checks, _run_tolerance(reconnects, "tasks")
        ),
        "recovery": recovery,
    }


def evaluate_tx_sessions(cycles: int, violations: list[str]) -> dict[str, Any]:
    """The multi-session TX criterion: no cycle broke an invariant.

    ``NOT_EVALUATED`` when no cycle ran to its end, so a run too short or too
    disconnected to key twice cannot certify.
    """
    if violations:
        status = "FAIL"
    elif cycles:
        status = "PASS"
    else:
        status = "NOT_EVALUATED"
    return {
        "status": status,
        "cycles": cycles,
        "violations": violations[:_TX_VIOLATIONS_KEPT],
        "violationCount": len(violations),
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
# A controller-session client for the multi-session TX scenario
# ---------------------------------------------------------------------------


class _TxSessionClient:
    """One controller session as a client drives it (MOR-3534).

    Its own HTTP session holds the primary control WebSocket, so closing that
    session cuts the link without a WebSocket close, as a dead network does.
    """

    def __init__(self, base_url: str) -> None:
        self._base_url = base_url
        self._http: aiohttp.ClientSession | None = None
        self._ws: aiohttp.ClientWebSocketResponse | None = None
        self._heartbeat: asyncio.Task[None] | None = None
        self.key = ""
        self._next_id = 0

    async def open(self) -> None:
        self._http = aiohttp.ClientSession(
            timeout=aiohttp.ClientTimeout(total=None, connect=10.0)
        )
        async with self._http.post(
            f"{self._base_url}/api/v1/controller", json={"kind": "pro"}
        ) as resp:
            if resp.status != 201:
                raise RuntimeError(f"controller grant refused: {resp.status}")
            self.key = (await resp.json())["controller_key"]
        self._ws = await self._http.ws_connect(
            f"{self._base_url}/api/v1/ws",
            protocols=(f"rigplane-controller-v1.{self.key}",),
        )

    def start_heartbeat(self) -> None:
        """Renew the session every HEARTBEAT_MS; one that does not expires after TTL_MS."""

        async def beat() -> None:
            assert self._ws is not None
            while True:
                await asyncio.sleep(ControllerAuthority.HEARTBEAT_MS / 1000)
                await self._ws.send_json({"type": "controller_heartbeat"})

        self._heartbeat = asyncio.create_task(beat(), name="soak-tx-heartbeat")

    async def command(self, name: str, timeout_s: float = 10.0) -> bool:
        """Run one control command; whether the server answered ``ok``."""
        assert self._ws is not None
        self._next_id += 1
        command_id = f"soak-{self._next_id}"
        await self._ws.send_json(
            {"type": "cmd", "id": command_id, "name": name, "params": {}}
        )
        deadline = time.monotonic() + timeout_s
        while True:
            msg = await asyncio.wait_for(
                self._ws.receive(), timeout=max(0.1, deadline - time.monotonic())
            )
            if msg.type is aiohttp.WSMsgType.TEXT:
                data = msg.json()
                if data.get("type") == "response" and data.get("id") == command_id:
                    return data.get("ok") is True
            elif msg.type in (
                aiohttp.WSMsgType.CLOSE,
                aiohttp.WSMsgType.CLOSING,
                aiohttp.WSMsgType.CLOSED,
                aiohttp.WSMsgType.ERROR,
            ):
                return False

    async def delete(self) -> None:
        """End the session over HTTP, as a client leaving does."""
        assert self._http is not None
        async with self._http.delete(
            f"{self._base_url}/api/v1/controller",
            headers={"X-RigPlane-Controller": self.key},
        ):
            pass

    async def renew(self) -> None:
        """Renew the lease once and wait for the answer, best effort.

        The background beat stops first, so afterwards the session's lease runs
        its full TTL_MS from the moment the server processed this beat; a
        session that is gone or whose link is dead is left alone.
        """
        if self._heartbeat is not None:
            self._heartbeat.cancel()
            await asyncio.gather(self._heartbeat, return_exceptions=True)
            self._heartbeat = None
        if self._ws is None or self._ws.closed:
            return
        with contextlib.suppress(Exception):
            await self._ws.send_json({"type": "controller_heartbeat"})
            deadline = time.monotonic() + _TX_RENEW_TIMEOUT_S
            while True:
                msg = await asyncio.wait_for(
                    self._ws.receive(), timeout=max(0.1, deadline - time.monotonic())
                )
                if msg.type is aiohttp.WSMsgType.TEXT:
                    if msg.json().get("type") == "controller_heartbeat":
                        return
                elif msg.type in (
                    aiohttp.WSMsgType.CLOSE,
                    aiohttp.WSMsgType.CLOSING,
                    aiohttp.WSMsgType.CLOSED,
                    aiohttp.WSMsgType.ERROR,
                ):
                    return

    async def close(self) -> None:
        """Drop the link; closing the HTTP session sends no WebSocket close."""
        if self._heartbeat is not None:
            self._heartbeat.cancel()
            await asyncio.gather(self._heartbeat, return_exceptions=True)
            self._heartbeat = None
        if self._http is not None:
            with contextlib.suppress(Exception):
                await self._http.close()
            self._http = None


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
        session_count: Callable[[], int] | None = None,
    ) -> None:
        self._cfg = config
        self._base_url = base_url
        self._rigctld_url = rigctld_url
        self._sim = sim
        self._session_count = session_count
        # Held by a multi-session TX cycle and by a forced reconnect cycle:
        # the stability probes of the latter must not see the former's
        # sockets and handler tasks.
        self._exclusive = asyncio.Lock()
        self._transient_condition = asyncio.Condition()
        self._transient_paused = False
        self._transient_active = 0
        self._tx_keyed = False
        self._tx_seed = secrets.randbits(32)
        self._tx_rng = random.Random(self._tx_seed)
        self._tx_modes: list[str] = []
        self._tx_cycles = 0
        self._tx_violations: list[str] = []
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
        self._probe_diagnostics = (
            _ProbeDiagnostics() if config.probe_diagnostics else None
        )
        self._capture_cycle = False
        self._task_provenance = _TaskProvenance() if config.task_provenance else None
        self._runtime_task_adapter: _RuntimeTaskAdapter | None = None

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
        # Not while a session transmits: retuning a keyed radio is refused.
        return self._connection_state != "connected" or self._tx_keyed

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

    # -- multi-session TX (MOR-3538) -------------------------------------------

    def _ptt_on_writes(self) -> int:
        return sum(1 for _at, on in self._sim.ptt_set_writes if on)

    async def _wait_ptt(self, expected: bool, timeout_s: float) -> bool:
        deadline = time.monotonic() + timeout_s
        while self._sim.ptt_on is not expected:
            if time.monotonic() >= deadline:
                return False
            await asyncio.sleep(0.05)
        return True

    async def _force_tx_off(self) -> None:
        assert self._session is not None
        with contextlib.suppress(Exception):
            async with self._session.post(
                f"{self._base_url}/api/v1/managed-transmit/command",
                json={"operation": "force_off"},
                timeout=aiohttp.ClientTimeout(total=10.0),
            ):
                pass

    async def _tx_sessions_once(self) -> None:
        """Two sessions race to key; one is cut, silenced or ends; the other keys.

        Per cycle the invariants are: exactly one of two simultaneous keys is
        accepted and the radio is keyed once; a second key is refused while
        the holder holds; the transmitter is off within ``TX_OFF_BUDGET_S`` of
        the holder ending however it ends; the other session then keys; the
        radio saw no key beyond those two; and no session is left behind.
        """
        if self._skip_operator_action() or self._exclusive.locked():
            self._counts["txSessionsSkipped"] += 1
            return
        if not self._tx_modes:
            # Every mode comes up once per round, in a random order.
            self._tx_modes = list(_TX_END_MODES)
            self._tx_rng.shuffle(self._tx_modes)
        mode = self._tx_modes.pop()
        async with self._exclusive:
            try:
                problems = await self._tx_sessions_cycle(mode)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                problems = [f"raised {exc!r}"]
            finally:
                self._tx_keyed = False
        self._tx_cycles += 1
        self._counts[f"txSessionCycles.{mode}"] += 1
        for problem in problems:
            logger.warning("soak: multi-session TX (%s): %s", mode, problem)
            self._tx_violations.append(f"{mode}: {problem} (seed {self._tx_seed})")

    async def _tx_sessions_cycle(self, mode: str) -> list[str]:
        problems: list[str] = []
        clients = [_TxSessionClient(self._base_url), _TxSessionClient(self._base_url)]
        try:
            await self._tx_sessions_steps(mode, clients, problems)
        finally:
            if self._sim.ptt_on:
                await self._force_tx_off()
            # renew() gives a full TTL_MS only to a client whose link is
            # still open and whose session is still alive — no TTL without
            # a live session and an ack — so the check below does not
            # depend on how old that lease was at the close. A cut-mode
            # holder whose link died earlier is skipped: its lease keeps
            # its age and the reaper may end that session before this
            # close; a half_open holder is skipped too — its session was
            # already reaped mid-cycle on the silent still-open link. The
            # check then leans on the other session, renewed here: a
            # close that failed to end its session would leak here with
            # a full lease. Without the renew a cycle of about 2 s put
            # the end of a leaked session's lease on the end of the check
            # window, and the reaper tick that ends it landed inside the
            # window (measured as a forced-cycle sweep of 1.6 to 1.8 s
            # on the Mac mini, recorded in PR #4263: 7 of 25 sabotaged
            # runs passed; 0 of 25 with the renew).
            await asyncio.gather(*(client.renew() for client in clients))
            for client in clients:
                await client.close()
        # Closing the clients ended their sessions on the server, which sees
        # a primary channel go away; none may stay for _TX_SESSION_END_S.
        if self._session_count is not None:
            deadline = time.monotonic() + _TX_SESSION_END_S
            while self._session_count() and time.monotonic() < deadline:
                await asyncio.sleep(0.2)
            if left := self._session_count():
                problems.append(f"{left} session(s) left after the cycle")
        return problems

    async def _tx_sessions_steps(
        self, mode: str, clients: list[_TxSessionClient], problems: list[str]
    ) -> None:
        for client in clients:
            await client.open()
        writes_before = self._ptt_on_writes()
        outcomes = await asyncio.gather(
            *(client.command("ptt_on") for client in clients)
        )
        if sorted(outcomes) != [False, True]:
            problems.append(f"simultaneous keys answered {outcomes}")
        if True not in outcomes:
            return
        holder = clients[outcomes.index(True)]
        other = clients[outcomes.index(True) ^ 1]
        self._tx_keyed = True
        # The other session renews its lease while the holder's story unfolds;
        # the holder's lease runs out on its own where the mode leaves it silent.
        other.start_heartbeat()
        if not await self._wait_ptt(True, 5.0):
            problems.append("the radio was never keyed")
        if self._ptt_on_writes() - writes_before != 1:
            problems.append("the radio was keyed other than once")
        if await other.command("ptt_on"):
            problems.append("a second session keyed over the holder")
        await asyncio.sleep(TX_HOLD_S)
        if not self._sim.ptt_on:
            problems.append("TX dropped while the holder held it")
        if mode == "unkey":
            if not await holder.command("ptt_off"):
                problems.append("the holder's unkey was refused")
        elif mode == "delete":
            await holder.delete()
        elif mode == "cut":
            await holder.close()
        # half_open: nothing; the link stays open and silent.
        if not await self._wait_ptt(False, TX_OFF_BUDGET_S):
            problems.append(f"TX still on {TX_OFF_BUDGET_S:.0f}s after {mode}")
        self._tx_keyed = False
        # The holder is gone: the other session keys, once the release debt of
        # the first transmission clears.
        for _ in range(50):
            if await other.command("ptt_on"):
                break
            await asyncio.sleep(0.2)
        else:
            problems.append("the other session could not key after the release")
            return
        self._tx_keyed = True
        if not await self._wait_ptt(True, 5.0):
            problems.append("the other session's key never reached the radio")
        if not await other.command("ptt_off"):
            problems.append("the other session's unkey was refused")
        if not await self._wait_ptt(False, TX_OFF_BUDGET_S):
            problems.append("TX still on after the other session's unkey")
        if self._ptt_on_writes() - writes_before != 2:
            problems.append("the radio was keyed other than twice")

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

    @contextlib.asynccontextmanager
    async def _quiesce_transient_actions(self) -> AsyncIterator[None]:
        async with self._transient_condition:
            self._transient_paused = True
            try:
                async with asyncio.timeout(_TRANSIENT_DRAIN_TIMEOUT_S):
                    await self._transient_condition.wait_for(
                        lambda: self._transient_active == 0
                    )
                yield
            finally:
                self._transient_paused = False
                self._transient_condition.notify_all()

    async def _stability_probe(self, phase: str = "pre") -> ProcessSample:
        async with self._quiesce_transient_actions():
            return await self._stability_probe_samples(phase)

    async def _stability_probe_samples(self, phase: str) -> ProcessSample:
        """Median of three samples with periodic actions paused and drained."""
        assert self._sampler is not None
        diagnostics = self._probe_diagnostics if self._capture_cycle else None
        provenance = self._task_provenance
        if diagnostics is None and provenance is None:
            probes = [
                await self._sampler.sample(self._elapsed())
                for _ in range(_STABILITY_PROBE_COUNT)
            ]
        else:
            probes = []
            for _ in range(_STABILITY_PROBE_COUNT):
                capture = diagnostics.begin(phase) if diagnostics is not None else None
                task_probe = (
                    provenance.begin_probe(phase) if provenance is not None else None
                )
                try:
                    if task_probe is None:
                        probes.append(
                            await self._sampler.sample(self._elapsed(), capture)
                        )
                    else:
                        probes.append(
                            await self._sampler.sample(
                                self._elapsed(), capture, task_probe=task_probe
                            )
                        )
                finally:
                    if capture is not None:
                        capture.ended = time.monotonic()

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

        result = ProcessSample(
            t_s=probes[len(probes) // 2].t_s,
            rss_bytes=median("rss_bytes"),
            fds=median("fds"),
            asyncio_tasks=median("asyncio_tasks"),
            connection=probes[-1].connection,
        )
        if diagnostics is not None:
            diagnostics.medians[phase] = _diagnostic_sample_values(result)
        return result

    async def _settle_sample(self, deadline: float) -> ProcessSample | None:
        """One settle sample, bounded by the remaining window (None past it).

        The bound is an in-task timeout, not ``wait_for``: on Python
        3.11 (the CI quick suite's interpreter) ``wait_for`` runs the
        awaited coroutine as a task of its own, which both task-count
        routes count — the owned population includes it (post-baseline,
        not ``soak-`` prefixed) — so the settle would measure one task
        above the probe-time population and read a delta sitting exactly
        at tolerance as a surplus.
        ``asyncio.timeout`` cancels without creating a task on every
        supported version: the sampler runs on the settle's own
        ``soak-`` task, the same task the probes run on, so the
        compared populations are identical.
        """
        assert self._sampler is not None
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return None
        with contextlib.suppress(asyncio.TimeoutError):
            async with asyncio.timeout(remaining):
                return await self._sampler.sample(self._elapsed())
        return None

    async def _settle_stability_counts(self, pre: ProcessSample) -> None:
        async with self._quiesce_transient_actions():
            await self._settle_quiesced_counts(pre)

    async def _settle_quiesced_counts(self, pre: ProcessSample) -> None:
        """Resample while the counts still decrease, within a window.

        A flat pair may be a plateau before further decay: the counts
        read as settled only after _STABILITY_SETTLE_STABLE_READINGS
        consecutive resamples without a decrease AND within tolerance
        of pre. A plateau still above tolerance keeps sampling until
        the window expires; the resample budget (one per step) bounds
        the loop when sleeps do not advance the clock. The deadline
        bounds the first sample, every resample and the step sleeps; a
        timeout ends the settle and the caller re-probes regardless, so
        a persistent surplus still fails.
        """
        assert self._sampler is not None
        gc.collect()
        deadline = time.monotonic() + _STABILITY_SETTLE_WINDOW_S
        budget = max(
            _STABILITY_SETTLE_STABLE_READINGS,
            int(_STABILITY_SETTLE_WINDOW_S // _STABILITY_SETTLE_STEP_S),
        )
        previous = await self._settle_sample(deadline)
        if previous is None:
            return
        calm = 0
        for _ in range(budget):
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return
            await asyncio.sleep(min(_STABILITY_SETTLE_STEP_S, remaining))
            current = await self._settle_sample(deadline)
            if current is None:
                return
            if _stability_counts_decreased(previous, current):
                calm = 0
            else:
                calm += 1
            previous = current
            if calm >= _STABILITY_SETTLE_STABLE_READINGS and not _stability_surplus(
                pre, current, self._runtime_task_adapter is not None
            ):
                return

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
        self._capture_cycle = (
            self._probe_diagnostics is not None
            and self._counts["forcedReconnects"] == 1
        )
        if self._capture_cycle:
            assert self._probe_diagnostics is not None
            self._probe_diagnostics.outcome = "in_progress"
            self._probe_diagnostics.boundary("cycleStart")
        if self._task_provenance is not None:
            self._task_provenance.begin_cycle(self._counts["forcedReconnects"])
            self._sampler._task_provenance = self._task_provenance
        outcome = "recovered"
        try:
            await self._reconnect_cycle_inner()
        except asyncio.CancelledError:
            outcome = "cancelled"
            if self._capture_cycle:
                assert self._probe_diagnostics is not None
                self._probe_diagnostics.outcome = "cancelled"
            # The cycle was cancelled without concluding (the run's wait
            # for it timed out): a forced disconnect that never recovered
            # must still fail the verdict, so record it as unrecovered.
            self._record_unrecovered_cycle("cycle cancelled before concluding")
            raise
        except Exception:
            outcome = "raised"
            if self._capture_cycle:
                assert self._probe_diagnostics is not None
                self._probe_diagnostics.outcome = "raised"
            logger.exception("soak: reconnect cycle failed")
            self._record_unrecovered_cycle("cycle raised")
        finally:
            if self._capture_cycle:
                assert self._probe_diagnostics is not None
                self._probe_diagnostics.boundary("cycleEnd")
            if self._task_provenance is not None:
                entry = self._reconnects[-1]
                if outcome == "recovered" and not entry["recovered"]:
                    outcome = "unrecovered"
                self._task_provenance.finish_cycle(entry, outcome)
                self._sampler._task_provenance = None
            self._capture_cycle = False
            self._cycle_active = False

    async def _reconnect_cycle_inner(self) -> None:
        window_start_s = self._elapsed()
        pre = await self._stability_probe()
        started_s = self._elapsed()
        diagnostics = self._probe_diagnostics if self._capture_cycle else None
        logger.info("soak: forced reconnect starting at +%.1fs", started_s)
        if diagnostics is not None:
            diagnostics.boundary("disconnectStart")
        await self._post_radio_control("disconnect")
        disconnect_observed = await self._wait_connection(
            lambda state: state != "connected", timeout_s=10.0
        )
        if diagnostics is not None:
            diagnostics.boundary("disconnectEnd")
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
                if diagnostics is not None:
                    diagnostics.boundary("recoveryStart")
                recovered = await self._do_connect_until_recovered(
                    self._elapsed() + self._cfg.reconnect_budget_s
                )
        finally:
            self._sim.fault_plan = None
        if diagnostics is not None:
            diagnostics.boundary("recoveryEnd")
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
            # Marks the pre/post counts as the in-process owned
            # population, so the criterion compares them exactly (fds)
            # / within the rebuild bound (tasks), not with the
            # process-wide tolerance.
            "ownedPopulation": self._runtime_task_adapter is not None,
            "fdsPre": pre.fds,
            "tasksPre": pre.asyncio_tasks,
            "fdsPost": None,
            "tasksPost": None,
        }
        if recovered:
            # The stack rebuilds its poller/keepalive tasks on connect;
            # let that settle so the probe compares steady states.
            if diagnostics is not None:
                diagnostics.boundary("settleStart")
            await asyncio.sleep(3.0)
            if diagnostics is not None:
                diagnostics.boundary("settleEnd")
            post = await self._stability_probe("post")
            if _stability_surplus(pre, post, self._runtime_task_adapter is not None):
                # A surplus right after recovery may be teardown still
                # draining (MOR-3575): settle, then take the median again.
                # The settle's own samples are uncaptured; the transient
                # trio is dropped so the settled re-probe fits the
                # six-probe bound. Observation never changes the verdict:
                # a surplus surviving the settle still fails.
                await self._settle_stability_counts(pre)
                if diagnostics is not None:
                    diagnostics.drop_phase("post")
                if self._task_provenance is not None:
                    self._task_provenance.drop_phase("post")
                post = await self._stability_probe("post")
            entry["fdsPost"] = post.fds
            entry["tasksPost"] = post.asyncio_tasks
        entry["windowEndS"] = round(self._elapsed(), 3)
        self._reconnects.append(entry)
        if diagnostics is not None:
            diagnostics.outcome = "recovered" if recovered else "unrecovered"

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
            async with self._exclusive:
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
            async with self._transient_condition:
                await self._transient_condition.wait_for(
                    lambda: not self._transient_paused
                )
                self._transient_active += 1
            try:
                await action()
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("soak: %s action failed", name)
            finally:
                async with self._transient_condition:
                    self._transient_active -= 1
                    self._transient_condition.notify_all()
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
            # Run-wide, so the fallback fields read the owned population.
            self._sampler._runtime_task_adapter = self._runtime_task_adapter
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
            if self._cfg.tx_sessions_interval_s > 0:
                tasks.append(
                    asyncio.create_task(
                        self._run_periodic(
                            "tx-sessions",
                            self._cfg.tx_sessions_interval_s,
                            self._tx_sessions_once,
                        ),
                        name="soak-tx-sessions",
                    )
                )
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
                # A multi-session TX cycle in flight ends on its own; cancelling
                # it mid-transmission would only leave the stack to clean up.
                with contextlib.suppress(asyncio.TimeoutError):
                    await asyncio.wait_for(self._exclusive.acquire(), timeout=60.0)
                for task in tasks:
                    task.cancel()
                await asyncio.gather(*tasks, return_exceptions=True)

    def _build_report(self) -> dict[str, Any]:
        criteria = evaluate_report(self._cfg, self._samples, self._reconnects)
        if self._cfg.tx_sessions_interval_s > 0:
            criteria["txSessions"] = evaluate_tx_sessions(
                self._tx_cycles, self._tx_violations
            )
        verdict = _verdict(criteria, self._error)
        report: dict[str, Any] = {
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
        if self._probe_diagnostics is not None:
            report["probeDiagnostics"] = self._probe_diagnostics.to_dict()
        if self._task_provenance is not None:
            report["taskProvenance"] = self._task_provenance.to_dict()
        return report

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
    managed_tx = config.tx_sessions_interval_s > 0
    # Baseline of task identities alive before the run creates anything;
    # taken before the stack starts so the stack's tasks are run-owned.
    task_baseline = frozenset(asyncio.all_tasks())
    stack = await _start_simulator_stack(managed_tx=managed_tx)
    report: dict[str, Any] | None = None
    adapter: _RuntimeTaskAdapter | None = None
    try:
        driver = SoakDriver(
            config,
            f"http://127.0.0.1:{stack.web_port}",
            rigctld_url=f"127.0.0.1:{stack.rigctld_port}",
            sim=stack.sim,
            session_count=stack.session_count if managed_tx else None,
        )
        # Always installed in-process: every route reads owned counts.
        adapter = _RuntimeTaskAdapter(stack, driver._task_provenance, task_baseline)
        driver._runtime_task_adapter = adapter
        adapter.install()
        report = await driver.run()
        return report
    finally:
        if adapter is not None:
            adapter.close()
            if driver._task_provenance is not None:
                driver._task_provenance.close()
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
    parser.add_argument(
        "--probe-diagnostics",
        action="store_true",
        help="capture at most six probes from the first reconnect",
    )
    parser.add_argument(
        "--task-provenance",
        action="store_true",
        help="capture bounded active and first task-stability-failing reconnect tasks",
    )
    parser.add_argument(
        "--reconnect-interval",
        type=float,
        default=RECONNECT_INTERVAL_S,
        help="forced reconnect cadence in seconds",
    )
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
    config = SoakConfig(
        duration_s=args.duration,
        report_path=args.report,
        probe_diagnostics=args.probe_diagnostics,
        task_provenance=args.task_provenance,
        reconnect_interval_s=args.reconnect_interval,
    )
    report = asyncio.run(run_soak(config))
    print(summary_line(report))
    print(f"soak report written to {args.report}")
    return 0 if report["verdict"] == "PASS" else 1


if __name__ == "__main__":
    sys.exit(main())
