"""20-second smoke of the operated soak driver against the simulator.

Drives the full in-process production stack — ``MockIcomRadio`` +
``IcomRadio`` + ``WebServer`` + ``RigctldServer`` — through the soak driver
(``scripts/soak/soak_driver.py``) at 1-second cadences with one forced
radio disconnect/connect, and pins:

* the JSON report shape and an INCOMPLETE verdict for the short run (the
  RSS criterion correctly reports NOT_EVALUATED below its fit-span floor, so
  the run cannot certify);
* the verdict rule (``_verdict``): FAIL on an error or any FAIL, PASS only
  when every criterion passed, INCOMPLETE otherwise, and an exit status of 0
  for PASS alone;
* the pass-criterion math itself, on synthetic one-hour samples
  (``evaluate_report``): a small slope passes, a steep one fails, and a
  reconnect-window allocation burst is excluded from the fit;
* the sampler's per-field merge: runtime ``process`` fields (PR #4044
  names) first, driver-side measurement for each field the runtime did
  not report, and the live connection status from the state document;
* the recovery criterion's teeth without an injected blackout
  (``fault_drop=0.0``): suppressing only the connect POST with a
  budget below the stack's self-recovery time must report an
  unrecovered disconnect and a FAIL verdict, and a disconnect that
  never happened must not count as recovered;
* a ``ps`` failure (OSError or its 10 s timeout) in the driver-side
  RSS fallback leaves the field None instead of raising into the
  reconnect cycle's stability probe;
* the report file and its ``verdict`` field survive a stack-teardown
  ``stop()`` that raises;
* a reconnect cycle still running at the run deadline must not wedge
  the run (the periodic loops must always yield);
* the multi-session TX scenario (MOR-3538): every end mode of the holder
  (unkey, delete, cut, silent) passes beside the other criteria, and the
  criterion fails when a silent holder keeps the transmitter or a session
  outlives its channel — for unkey, cut and half_open alike, and with the
  leases forced old at the close.
* the post-reconnect stability settle (MOR-3575): a transient surplus
  that drains after recovery passes once settled — even across a long
  flat plateau mid-drain, for FDs and task counts alike, and whether or
  not probe/task capture is on — a persistent one still fails after the
  full window, and the settle gives up at its window or on a hung
  sample. The feeds are scripted; the window test drives a controlled
  monotonic clock so the deadline itself ends the settle (one late
  settle sample hangs for real and is bounded by the in-task timeout on
  the remaining window), and the hung-sample test uses a real 50 ms
  window against a real 30 s hang. The step sleep is clamped to the
  remaining window, a sample cancelled inside the ``ps`` fallback kills
  and reaps its child before the cancellation propagates, and the
  settle's own bound adds no task to the counts it takes: on the live
  loop a task delta exactly at tolerance must still settle, measured by
  both count routes with the CI interpreter's task-wrapping
  ``wait_for`` semantics emulated (Python 3.11; 3.12+ await in-task).
"""

from __future__ import annotations

import asyncio
import itertools
import json
import os
import socket
import time
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Callable

import pytest

from scripts.soak import soak_driver

# asyncio_mode=auto (pyproject.toml) drives the async tests; no marker.

RSS_LIMIT = 50 * 1024 * 1024


def _smoke_config(**overrides: Any) -> soak_driver.SoakConfig:
    defaults: dict[str, Any] = dict(
        duration_s=20.0,
        report_path="",
        tune_interval_s=1.0,
        mode_interval_s=2.0,
        rigctld_interval_s=2.0,
        reconnect_interval_s=8.0,
        metrics_interval_s=1.0,
        reconnect_budget_s=25.0,
        fault_drop=0.0,
        fault_duration_s=3.0,
        rss_warmup_s=5.0,
        # The multi-session TX scenario has its own tests below; the others
        # keep the stack they were written against.
        tx_sessions_interval_s=0.0,
    )
    defaults.update(overrides)
    return soak_driver.SoakConfig(**defaults)


def _hour_of_samples(
    slope_bytes_per_s: float, step_s: float = 30.0
) -> list[soak_driver.ProcessSample]:
    base = 80 * 1024 * 1024
    return [
        soak_driver.ProcessSample(
            t_s=t,
            rss_bytes=int(base + slope_bytes_per_s * t),
            fds=20,
            asyncio_tasks=25,
            connection="connected",
        )
        for t in [i * step_s for i in range(int(3600 / step_s) + 1)]
    ]


def _one_reconnect(start_s: float, end_s: float) -> dict[str, Any]:
    return {
        "startedAtS": start_s,
        "recovered": True,
        "disconnectObserved": True,
        "recoveryS": end_s - start_s,
        "budgetS": 30.0,
        "windowStartS": start_s,
        "windowEndS": end_s,
        "fdsPre": 20,
        "tasksPre": 25,
        "fdsPost": 20,
        "tasksPost": 25,
    }


def test_evaluate_report_passes_small_slope() -> None:
    config = _smoke_config(duration_s=3600.0)
    criteria = soak_driver.evaluate_report(
        config, _hour_of_samples(slope_bytes_per_s=1000.0), []
    )
    # 1000 B/s = 3.4 MiB/h, well under the 50 MiB/h limit.
    assert criteria["rssGrowth"]["status"] == "PASS"
    assert criteria["recovery"]["status"] == "NOT_EVALUATED"


def test_evaluate_report_fails_steep_slope() -> None:
    config = _smoke_config(duration_s=3600.0)
    criteria = soak_driver.evaluate_report(
        config, _hour_of_samples(slope_bytes_per_s=20_000.0), []
    )
    # 20 kB/s ≈ 68.7 MiB/h, over the 50 MiB/h limit.
    assert criteria["rssGrowth"]["status"] == "FAIL"


def test_evaluate_report_excludes_reconnect_window_from_rss_fit() -> None:
    config = _smoke_config(duration_s=3600.0)
    samples = _hour_of_samples(slope_bytes_per_s=1000.0)
    # A 30 MiB allocation burst inside the reconnect window must not move
    # the fit: those samples are excluded.
    for sample in samples:
        if 1800.0 <= sample.t_s <= 1830.0:
            sample.rss_bytes += 30 * 1024 * 1024
    criteria = soak_driver.evaluate_report(
        config, samples, [_one_reconnect(1798.0, 1832.0)]
    )
    assert criteria["rssGrowth"]["status"] == "PASS"
    assert criteria["recovery"]["status"] == "PASS"
    assert criteria["fdsStability"]["checks"][0]["ok"] is True
    assert criteria["tasksStability"]["checks"][0]["ok"] is True


def test_evaluate_report_fails_out_of_tolerance_reconnect() -> None:
    config = _smoke_config(duration_s=3600.0)
    reconnect = _one_reconnect(300.0, 320.0)
    reconnect["fdsPost"] = 30  # +10 fds across one reconnect
    reconnect["tasksPost"] = 31  # +6 tasks
    criteria = soak_driver.evaluate_report(
        config, _hour_of_samples(slope_bytes_per_s=1000.0), [reconnect]
    )
    assert criteria["fdsStability"]["status"] == "FAIL"
    assert criteria["tasksStability"]["status"] == "FAIL"
    assert criteria["fdsStability"]["checks"][0]["delta"] == 10
    assert criteria["tasksStability"]["checks"][0]["delta"] == 6


def test_evaluate_report_short_run_rss_not_evaluated() -> None:
    config = _smoke_config(duration_s=20.0)
    criteria = soak_driver.evaluate_report(
        config,
        _hour_of_samples(slope_bytes_per_s=20_000.0)[:4],
        [],
    )
    assert criteria["rssGrowth"]["status"] == "NOT_EVALUATED"


_CRITERIA = ("rssGrowth", "fdsStability", "tasksStability", "recovery")


def _statuses(**by_name: str) -> dict[str, Any]:
    """Criteria carrying only a status; NOT_EVALUATED unless set."""
    return {n: {"status": by_name.get(n, "NOT_EVALUATED")} for n in _CRITERIA}


def test_verdict_is_incomplete_when_nothing_was_evaluated() -> None:
    assert soak_driver._verdict(_statuses(), None) == soak_driver.VERDICT_INCOMPLETE
    assert soak_driver._verdict({}, None) == soak_driver.VERDICT_INCOMPLETE


@pytest.mark.parametrize("passed", _CRITERIA)
def test_verdict_is_incomplete_when_only_one_criterion_passed(passed: str) -> None:
    criteria = _statuses(**{passed: "PASS"})
    assert soak_driver._verdict(criteria, None) == soak_driver.VERDICT_INCOMPLETE


@pytest.mark.parametrize("unevaluated", _CRITERIA)
def test_verdict_is_incomplete_when_one_criterion_was_not_evaluated(
    unevaluated: str,
) -> None:
    criteria = _statuses(**{n: "PASS" for n in _CRITERIA if n != unevaluated})
    assert soak_driver._verdict(criteria, None) == soak_driver.VERDICT_INCOMPLETE


def test_verdict_passes_when_every_criterion_passed() -> None:
    criteria = _statuses(**dict.fromkeys(_CRITERIA, "PASS"))
    assert soak_driver._verdict(criteria, None) == "PASS"


@pytest.mark.parametrize("failed", _CRITERIA)
@pytest.mark.parametrize("others", ["PASS", "NOT_EVALUATED"])
def test_verdict_fails_on_any_failed_criterion(failed: str, others: str) -> None:
    criteria = _statuses(**(dict.fromkeys(_CRITERIA, others) | {failed: "FAIL"}))
    assert soak_driver._verdict(criteria, None) == "FAIL"


@pytest.mark.parametrize("status", ["PASS", "NOT_EVALUATED"])
def test_verdict_fails_on_a_driver_error(status: str) -> None:
    criteria = _statuses(**dict.fromkeys(_CRITERIA, status))
    assert soak_driver._verdict(criteria, "RuntimeError: boom") == "FAIL"


def test_a_run_that_evaluated_nothing_is_incomplete_not_pass() -> None:
    """The reported defect: 120 s of NOT_EVALUATED used to print PASS."""
    config = _smoke_config(duration_s=120.0)
    criteria = soak_driver.evaluate_report(
        config, _hour_of_samples(slope_bytes_per_s=1000.0)[:4], []
    )
    assert {c["status"] for c in criteria.values()} == {"NOT_EVALUATED"}
    assert soak_driver._verdict(criteria, None) == soak_driver.VERDICT_INCOMPLETE


def test_an_hour_with_a_recovered_reconnect_evaluates_all_four_and_passes() -> None:
    config = _smoke_config(duration_s=3600.0)
    criteria = soak_driver.evaluate_report(
        config,
        _hour_of_samples(slope_bytes_per_s=1000.0),
        [_one_reconnect(1798.0, 1832.0)],
    )
    assert {c["status"] for c in criteria.values()} == {"PASS"}
    assert soak_driver._verdict(criteria, None) == "PASS"


def _fake_report(verdict: str) -> dict[str, Any]:
    criteria = soak_driver.evaluate_report(
        _smoke_config(duration_s=3600.0),
        _hour_of_samples(slope_bytes_per_s=1000.0),
        [_one_reconnect(1798.0, 1832.0)],
    )
    return {"verdict": verdict, "criteria": criteria, "samples": []}


@pytest.mark.parametrize(
    ("verdict", "status"),
    [("PASS", 0), (soak_driver.VERDICT_INCOMPLETE, 1), ("FAIL", 1)],
)
def test_exit_status_is_zero_only_for_a_pass_verdict(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    tmp_path: Path,
    verdict: str,
    status: int,
) -> None:
    async def _run(config: soak_driver.SoakConfig) -> dict[str, Any]:
        return _fake_report(verdict)

    monkeypatch.setattr(soak_driver, "run_soak", _run)
    assert soak_driver.main(["--report", str(tmp_path / "r.json")]) == status
    assert capsys.readouterr().out.startswith(f"soak {verdict} ")


def test_cli_exits_non_zero_for_a_run_too_short_to_evaluate_anything(
    capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    """The real stack for 3 s at the default cadences: no reconnect, no RSS fit.

    The multi-session TX cycle that starts at once is allowed to finish, so
    its criterion may be evaluated; every other criterion is not.
    """
    report_path = tmp_path / "r.json"
    status = soak_driver.main(["--duration", "3", "--report", str(report_path)])
    report = json.loads(report_path.read_text(encoding="utf-8"))
    statuses = {key: c["status"] for key, c in report["criteria"].items()}
    assert statuses.pop("txSessions") in ("PASS", "NOT_EVALUATED")
    assert set(statuses.values()) == {"NOT_EVALUATED"}
    assert report["error"] is None
    assert report["verdict"] == soak_driver.VERDICT_INCOMPLETE
    assert capsys.readouterr().out.startswith("soak INCOMPLETE ")
    assert status == 1


def test_state_connection_status_reads_live_rig_connected() -> None:
    body: dict[str, Any] = {"connection": {"rigConnected": True}}
    assert soak_driver._state_connection_status(body) == "connected"
    disconnected = soak_driver._state_connection_status(
        {"connection": {"rigConnected": False}}
    )
    assert disconnected == "disconnected"
    assert soak_driver._state_connection_status({}) is None
    assert soak_driver._state_connection_status({"connection": None}) is None
    assert soak_driver._state_connection_status({"connection": {}}) is None


async def test_sampler_merges_runtime_process_fields_per_field(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Runtime ``process`` fields win per field; gaps fill driver-side.

    PR #4044 names ``openFds`` (not ``fds``) and reports a null
    ``rssBytes`` where the platform has no ``/proc/self/statm`` (macOS):
    a whole-block switch would silently turn the FD and RSS criteria
    into NOT_EVALUATED there.
    """
    sampler = soak_driver.ProcessMetricsSampler(object(), "http://127.0.0.1:1")

    async def _runtime() -> dict[str, Any] | None:
        return {"process": {"rssBytes": None, "openFds": 41, "asyncioTasks": 9}}

    async def _status() -> str | None:
        return "connected"

    async def _driver_side(pid: int) -> tuple[int | None, int | None]:
        assert pid > 0
        return (111 * 1024 * 1024, 50)

    monkeypatch.setattr(sampler, "fetch_runtime", _runtime)
    monkeypatch.setattr(sampler, "fetch_connection_status", _status)
    monkeypatch.setattr(soak_driver, "_driver_side_process_metrics", _driver_side)
    monkeypatch.setattr(soak_driver, "_server_asyncio_task_count", lambda: 7)

    sample = await sampler.sample(1.5)
    assert sample.rss_bytes == 111 * 1024 * 1024  # runtime null → driver-side
    assert sample.fds == 41  # runtime openFds
    assert sample.asyncio_tasks == 9  # runtime asyncioTasks
    assert sample.connection == "connected"


async def test_sampler_falls_back_fully_without_runtime_process(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sampler = soak_driver.ProcessMetricsSampler(object(), "http://127.0.0.1:1")

    async def _runtime() -> dict[str, Any] | None:
        return {}  # this head's runtime document has no process object

    async def _status() -> str | None:
        return None

    async def _driver_side(pid: int) -> tuple[int | None, int | None]:
        return (222 * 1024 * 1024, 60)

    monkeypatch.setattr(sampler, "fetch_runtime", _runtime)
    monkeypatch.setattr(sampler, "fetch_connection_status", _status)
    monkeypatch.setattr(soak_driver, "_driver_side_process_metrics", _driver_side)
    monkeypatch.setattr(soak_driver, "_server_asyncio_task_count", lambda: 33)

    sample = await sampler.sample(2.5)
    assert sample.rss_bytes == 222 * 1024 * 1024
    assert sample.fds == 60
    assert sample.asyncio_tasks == 33
    assert sample.connection is None


async def test_driver_side_metrics_tolerate_a_missing_ps_sample(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A None ``ps`` result must leave the RSS field None, not raise.

    ``_ps_output`` returns None on OSError or its 10 s timeout; the
    old ``int(out.strip())`` then raised AttributeError, which the
    best-effort suppression around the parse did not cover — in the
    metrics loop the sample was silently dropped, and inside
    ``_stability_probe`` the exception propagated, the cycle recorded
    "cycle raised", and the recovery criterion falsely failed
    (reproduced on macOS). ``_statm_rss_bytes`` is forced to None so
    the ``ps`` fallback also runs on Linux, where ``/proc`` answers.
    """

    async def _no_ps(args: list[str]) -> str | None:
        return None

    monkeypatch.setattr(soak_driver, "_statm_rss_bytes", lambda pid: None)
    monkeypatch.setattr(soak_driver, "_ps_output", _no_ps)

    rss, _fds = await soak_driver._driver_side_process_metrics(999999)

    assert rss is None


class _BlockingPsProcess:
    """A fake ``ps`` child that never finishes and records its teardown."""

    def __init__(self) -> None:
        self.calls: list[str] = []

    async def communicate(self) -> tuple[bytes, None]:
        await asyncio.Event().wait()
        raise AssertionError("unreachable")

    def kill(self) -> None:
        self.calls.append("kill")

    async def wait(self) -> None:
        self.calls.append("wait")


async def test_ps_output_kills_and_reaps_its_child_when_cancelled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A cancelled sample must not leak its ps child (MOR-3575).

    The settle window's timeout can cancel a sample that is inside
    ``_ps_output``; the child is killed and reaped before the
    cancellation propagates. RED on the pre-fix helper: the cancel
    propagated straight through and these kill/reap assertions failed.
    """

    async def _spawn_ps(*args: Any, **kwargs: Any) -> _BlockingPsProcess:
        return proc

    proc = _BlockingPsProcess()
    monkeypatch.setattr(soak_driver.asyncio, "create_subprocess_exec", _spawn_ps)
    task = asyncio.create_task(soak_driver._ps_output(["-o", "rss="]))
    await asyncio.sleep(0.01)  # reach the blocking communicate()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert proc.calls == ["kill", "wait"]


async def test_soak_smoke_report_shape_and_incomplete_verdict() -> None:
    report = await soak_driver.run_soak(_smoke_config())

    # 20 s cannot fit the RSS slope (below), so the other three criteria
    # pass and the run still does not certify.
    assert report["verdict"] == soak_driver.VERDICT_INCOMPLETE
    assert report["error"] is None
    assert report["criteria"]["recovery"]["status"] == "PASS"
    # The verdict follows from the report's own criteria and error.
    verdict = soak_driver._verdict(report["criteria"], report["error"])
    assert verdict == soak_driver.VERDICT_INCOMPLETE
    assert report["schemaVersion"] == 1
    assert report["durationConfiguredS"] == 20.0
    assert report["durationActualS"] >= 20.0

    counts = report["counts"]
    assert counts["tunes"] >= 5
    assert counts["modeChanges"] >= 3
    assert counts["rigctldReplays"] >= 2
    assert counts["forcedReconnects"] >= 1
    assert counts["stateWsMessages"] >= 1
    assert counts.get("wsDrops", 0) == 0  # absent key = no drop counted

    recovery = report["criteria"]["recovery"]
    assert recovery["unrecovered"] == 0
    assert recovery["forced"] >= 1
    assert recovery["budgetS"] == 25.0

    fds = report["criteria"]["fdsStability"]
    assert fds["status"] == "PASS"
    assert fds["tolerance"] == 5
    assert fds["checks"], "expected at least one completed reconnect check"
    assert all(check["ok"] for check in fds["checks"])

    tasks = report["criteria"]["tasksStability"]
    assert tasks["status"] == "PASS"
    assert tasks["tolerance"] == 5
    assert tasks["checks"], "expected at least one completed reconnect check"

    rss = report["criteria"]["rssGrowth"]
    # 20 s of operation is under the criterion's noise floor: the fit
    # span (>= 600 s) gate must report NOT_EVALUATED rather than guess.
    assert rss["status"] == "NOT_EVALUATED"
    assert rss["limitBytesPerHour"] == RSS_LIMIT
    assert rss["samplesFitted"] >= 2

    assert all(entry["disconnectObserved"] is True for entry in report["reconnects"])

    samples = report["samples"]
    assert len(samples) >= 10
    assert all(sample["rssBytes"] is not None for sample in samples)
    assert all(sample["fds"] is not None for sample in samples)
    assert all(sample["asyncioTasks"] is not None for sample in samples)


async def test_soak_completes_when_reconnect_cycle_outlives_the_run() -> None:
    """A cycle running past the deadline must not wedge the run.

    Past the deadline the periodic loops' capped wait goes negative and
    the not-due ``continue`` used to run without a single await — with
    the radio disconnected the tune/mode/rigctld actions also returned
    without awaiting — freezing the event loop so the in-flight cycle
    could never finish and no report was produced (reproduced on the
    test host: a 20 s run with ``fault_drop=1.0`` and a 25 s budget
    never returned and was killed externally). Here a real connect runs
    under ``fault_drop=1.0`` with the cycle starting before and ending
    after the 8 s deadline; the run must complete inside the bound.
    """
    report = await asyncio.wait_for(
        soak_driver.run_soak(
            _smoke_config(
                duration_s=8.0,
                reconnect_interval_s=4.0,
                fault_drop=1.0,
            )
        ),
        timeout=90.0,
    )

    assert report["counts"]["forcedReconnects"] >= 1
    assert report["verdict"] in ("PASS", "FAIL", soak_driver.VERDICT_INCOMPLETE)


async def test_soak_recovery_criterion_fails_when_connect_is_suppressed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The recovery criterion has teeth against a dead connect endpoint.

    The mutation suppresses only ``POST /api/v1/radio/connect``; the
    disconnect half is untouched, and no blackout is injected
    (``fault_drop=0.0``). The criterion measures the whole stack
    returning to CONNECTED — the driver's connect POST or the stack's
    own recovery, whichever comes first: after an operator disconnect
    the radio's CI-V watchdog soft-reconnects on its own roughly 8 s
    later (measured 7.6 s on the test host; ``rigplane.runtime.radio``
    logs ``force_cleanup_civ`` followed by ``soft_reconnect: control
    transport gone, doing full connect``). The 5 s budget sits below
    that self-recovery time but above the driver-POST recovery of the
    unmutated stack (measured on the test host: suppressed run
    unrecovered at 6.45 s with a FAIL verdict; unmutated run recovered
    in 2.47 s, PASS), so removing the monkeypatch turns this test RED
    while the mutation fails it. A ``fault_drop=1.0`` variant would
    detect nothing: under the 3 s blackout even a working connect
    recovers only after ~16 s, so the 5 s budget fails the unmutated
    run too.
    """
    real = soak_driver.SoakDriver._post_radio_control

    async def _drop_connect(
        self: soak_driver.SoakDriver, action: str, timeout_s: float = 10.0
    ) -> bool:
        if action == "connect":
            return False
        return await real(self, action, timeout_s=timeout_s)  # type: ignore[arg-type]

    monkeypatch.setattr(soak_driver.SoakDriver, "_post_radio_control", _drop_connect)
    report = await soak_driver.run_soak(
        _smoke_config(reconnect_budget_s=5.0, fault_drop=0.0)
    )

    assert report["criteria"]["recovery"]["unrecovered"] >= 1
    assert report["verdict"] == "FAIL"


async def test_soak_counts_a_disconnect_that_never_happened_as_unrecovered(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A disconnect that never happened must not count as recovered.

    The mutation suppresses only ``POST /api/v1/radio/disconnect``: the
    radio never leaves CONNECTED, so a cycle that only checks "connect
    done + state connected" would report the recovery of a disconnect
    that never occurred. The cycle must record ``disconnectObserved``
    false and count the reconnect as unrecovered, failing the verdict.
    """
    real = soak_driver.SoakDriver._post_radio_control

    async def _drop_disconnect(
        self: soak_driver.SoakDriver, action: str, timeout_s: float = 10.0
    ) -> bool:
        if action == "disconnect":
            return False
        return await real(self, action, timeout_s=timeout_s)  # type: ignore[arg-type]

    monkeypatch.setattr(soak_driver.SoakDriver, "_post_radio_control", _drop_disconnect)
    report = await soak_driver.run_soak(_smoke_config(duration_s=26.0))

    assert report["reconnects"], "expected at least one forced reconnect cycle"
    first = report["reconnects"][0]
    assert first["disconnectObserved"] is False
    assert first["recovered"] is False
    assert report["criteria"]["recovery"]["unrecovered"] >= 1
    assert report["verdict"] == "FAIL"


async def test_soak_report_written_when_a_stop_raises(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A teardown failure must not cost the report and its verdict.

    ``run_soak`` used to write the report only after
    ``stack.aclose()``; an exception from any ``stop()`` there
    replaced the verdict line and the report file with a traceback.
    The report is now written inside the same ``finally``, before the
    teardown. Here the simulator's ``stop()`` performs its real
    cleanup and then raises, and the report file must still exist
    with a verdict.
    """
    report_path = tmp_path / "soak-report.json"
    real_sim_stop = soak_driver.MockIcomRadio.stop

    async def _stop_then_raise(self: soak_driver.MockIcomRadio) -> None:
        await real_sim_stop(self)
        raise RuntimeError("simulator stop reported a failure")

    monkeypatch.setattr(soak_driver.MockIcomRadio, "stop", _stop_then_raise)

    with pytest.raises(RuntimeError):
        await soak_driver.run_soak(
            _smoke_config(duration_s=6.0, report_path=str(report_path))
        )

    report = json.loads(report_path.read_text(encoding="utf-8"))
    # 6 s is below the first forced reconnect (8 s) and the RSS fit floor.
    assert report["verdict"] == soak_driver.VERDICT_INCOMPLETE
    assert report["durationConfiguredS"] == 6.0


# ---------------------------------------------------------------------------
# MOR-3538: the multi-session TX scenario
# ---------------------------------------------------------------------------


def test_evaluate_tx_sessions_statuses() -> None:
    passed = soak_driver.evaluate_tx_sessions(4, [])
    failed = soak_driver.evaluate_tx_sessions(4, [f"cut: v{i}" for i in range(30)])
    none = soak_driver.evaluate_tx_sessions(0, [])
    assert (passed["status"], none["status"], failed["status"]) == (
        "PASS",
        "NOT_EVALUATED",
        "FAIL",
    )
    assert failed["violationCount"] == 30 and len(failed["violations"]) == 20
    assert soak_driver._verdict({"txSessions": failed}, None) == "FAIL"
    assert soak_driver._verdict({"txSessions": none}, None) == (
        soak_driver.VERDICT_INCOMPLETE
    )


async def test_soak_multi_session_tx_passes_in_every_end_mode_beside_a_reconnect() -> (
    None
):
    """Four cycles cover unkey, delete, cut and a silent holder; a forced
    reconnect in the same run keeps the FD and task criteria passing."""
    report = await soak_driver.run_soak(
        _smoke_config(
            duration_s=34.0,
            tx_sessions_interval_s=1.0,
            reconnect_interval_s=16.0,
            reconnect_budget_s=25.0,
        )
    )
    tx = report["criteria"]["txSessions"]
    assert tx["violations"] == [] and tx["status"] == "PASS", tx
    assert tx["cycles"] >= 4
    counts = report["counts"]
    for mode in ("unkey", "delete", "cut", "half_open"):
        assert counts[f"txSessionCycles.{mode}"] >= 1, counts
    assert report["error"] is None
    for key in ("recovery", "fdsStability", "tasksStability"):
        assert report["criteria"][key]["status"] == "PASS", report["criteria"][key]
    assert report["verdict"] == soak_driver.VERDICT_INCOMPLETE


async def test_soak_multi_session_tx_fails_when_a_silent_holder_keeps_the_transmitter(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A holder that stops renewing must lose the transmitter: with the lease
    made effectively endless, the silent-holder cycle reports it."""
    from rigplane.runtime.controller_authority import ControllerAuthority

    monkeypatch.setattr(ControllerAuthority, "TTL_MS", 3_600_000)
    monkeypatch.setattr(soak_driver, "_TX_END_MODES", ("half_open",))
    monkeypatch.setattr(soak_driver, "TX_OFF_BUDGET_S", 3.0)
    report = await soak_driver.run_soak(
        _smoke_config(duration_s=6.0, tx_sessions_interval_s=100.0)
    )
    tx = report["criteria"]["txSessions"]
    assert tx["status"] == "FAIL", tx
    assert any("TX still on" in v and "half_open" in v for v in tx["violations"]), tx
    assert report["verdict"] == "FAIL"


@pytest.mark.parametrize("mode", ["unkey", "cut", "half_open"])
async def test_soak_multi_session_tx_fails_when_a_session_outlives_its_channel(
    monkeypatch: pytest.MonkeyPatch, mode: str
) -> None:
    """A session whose primary channel is gone must end at once, not at its
    lease: with the channel's detach made a no-op the cycle reports a leak.

    The pinned signal is the other session's final-close leak, identical
    in every mode: the close-time renew gives its still-open link a full
    lease, and the sabotaged close leaves it past the check window, which
    the cycle must report. In ``cut`` and ``half_open`` the holder's own
    lease also runs out mid-cycle while the driver waits out the TX
    release. ``delete`` is excluded as a scoping choice: its HTTP DELETE
    ends the holder's session through ``release``, bypassing ``detach``,
    so the mode adds no signal beyond the common one. The violation must
    come from this mode's own cycle.
    """
    from rigplane.runtime.controller_authority import ControllerAuthority

    monkeypatch.setattr(ControllerAuthority, "detach", lambda self, ticket: None)
    monkeypatch.setattr(soak_driver, "_TX_END_MODES", (mode,))
    report = await soak_driver.run_soak(
        _smoke_config(duration_s=6.0, tx_sessions_interval_s=100.0)
    )
    tx = report["criteria"]["txSessions"]
    assert tx["status"] == "FAIL", tx
    assert any(
        v.startswith(f"{mode}: ") and "left after the cycle" in v
        for v in tx["violations"]
    ), tx


async def test_soak_multi_session_tx_catches_a_leak_whatever_the_age_of_the_leases(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The bad phase of the leak check, forced: leases that are old at the close.

    No heartbeat runs and the hold is 4 s of an 8 s lease, so both leases
    have about 2.5 s left when the cycle closes its clients — old enough
    that without the close-time renew a leaked session's lease ends inside
    the 4 s check window and the reaper can end it there: the alignment
    that made the check flaky. The 8 s lease (the check window stays at
    the import-derived 4 s) keeps the close well clear of the lease end on
    a loaded runner, where the production 6 s lease left under a second;
    the cycle must renew the leases before it closes, so the leak is still
    there whatever the age. Only ``unkey`` can run here: with no heartbeat
    the TX release of ``cut`` and ``half_open`` waits out the holder's
    lease, which is also the other session's end, so neither session
    survives to the close.
    """
    from rigplane.runtime.controller_authority import ControllerAuthority

    monkeypatch.setattr(soak_driver._TxSessionClient, "start_heartbeat", lambda s: None)
    monkeypatch.setattr(ControllerAuthority, "detach", lambda self, ticket: None)
    monkeypatch.setattr(ControllerAuthority, "TTL_MS", 8_000)
    monkeypatch.setattr(soak_driver, "_TX_END_MODES", ("unkey",))
    monkeypatch.setattr(soak_driver, "TX_HOLD_S", 4.0)
    report = await soak_driver.run_soak(
        _smoke_config(duration_s=6.0, tx_sessions_interval_s=100.0)
    )
    tx = report["criteria"]["txSessions"]
    assert tx["status"] == "FAIL", tx
    assert any("left after the cycle" in v for v in tx["violations"]), tx


async def test_renew_gives_a_session_its_full_lease_whatever_its_age() -> None:
    """The leak check after a TX cycle must not depend on the leases' phase.

    The cycle renews every live lease just before it closes its clients, so a
    session that is not ended by its channel's loss stays a full ``TTL_MS``.
    Driven on a fake controller clock: a lease aged to within 1.5 s of its end
    is back at the full ``TTL_MS`` after ``renew``, deterministically.
    """
    from rigplane.runtime.controller_authority import ControllerAuthority

    stack = await soak_driver._start_simulator_stack(managed_tx=True)
    try:
        controller = stack.web._controller
        now = [time.monotonic()]
        controller._clock = lambda: now[0]
        client = soak_driver._TxSessionClient(f"http://127.0.0.1:{stack.web_port}")
        await client.open()
        try:
            (session,) = controller._sessions.values()
            now[0] += (ControllerAuthority.TTL_MS - 1500) / 1000
            assert session.deadline - now[0] == pytest.approx(1.5)
            await client.renew()
            ttl = ControllerAuthority.TTL_MS / 1000
            assert session.deadline - now[0] == pytest.approx(ttl)
            # A leaked session then outlives the check window by at least one
            # reaper tick, so the reaper cannot end it inside the window: the
            # coincidence of lease end and window end that failed the release
            # validation run (publish.yml, 3.13) cannot recur.
            margin = ttl - soak_driver._TX_SESSION_END_S
            assert margin >= ControllerAuthority.HEARTBEAT_MS / 1000
        finally:
            await client.close()
    finally:
        await stack.aclose()


def _process_script(
    fds_values: list[int],
    tasks_values: list[int] | None = None,
    tasks: int = 30,
) -> Callable[[], dict[str, int]]:
    """A runtime ``process`` feed replaying scripted counts, then holding."""
    fds = iter(fds_values)
    last_fds = fds_values[-1]
    task_feed = iter(tasks_values) if tasks_values is not None else None
    last_tasks = tasks_values[-1] if tasks_values is not None else tasks

    def script() -> dict[str, int]:
        return {
            "rssBytes": 1024,
            "openFds": next(fds, last_fds),
            "asyncioTasks": (
                next(task_feed, last_tasks) if task_feed is not None else tasks
            ),
        }

    return script


class _SettleClock:
    """A controlled monotonic clock for the settle's deadline math.

    Every scripted sample costs ``sample_cost_s`` of window time and
    every recorded sleep advances the clock by its delay, so the test
    decides exactly when the settle's window expires without waiting
    for real time to pass. Installed as a ``time``-module stub inside
    ``soak_driver`` only: on Python 3.13 the event loop's ``time()``
    reads ``time.monotonic`` dynamically, so patching the stdlib module
    attribute globally would freeze the loop's own timers.
    """

    def __init__(self, sample_cost_s: float) -> None:
        self.now = 1000.0
        self._sample_cost_s = sample_cost_s

    def monotonic(self) -> float:
        return self.now

    def advance_by_sample(self) -> None:
        self.now += self._sample_cost_s

    def advance(self, seconds: float) -> None:
        self.now += seconds


async def _run_cycle_with_process_script(
    monkeypatch: pytest.MonkeyPatch,
    script: Callable[[], dict[str, int]],
    *,
    hang_call: int | None = None,
    probe_diagnostics: bool = False,
    task_provenance: bool = False,
    clock: _SettleClock | None = None,
) -> tuple[soak_driver.SoakDriver, list[float]]:
    """One recovered cycle over a scripted feed; records asyncio.sleep delays.

    ``hang_call`` makes the Nth ``fetch_runtime`` call sleep 30 real
    seconds (an unbounded sampler hang). ``clock``, when given, is
    only advanced by every sample and sleep recorded here, driving
    the settle's deadline arithmetic; installing it as the driver's
    ``time.monotonic`` is the caller's job (see the window and clamp
    tests), because patching the ``time`` module attribute inside the
    helper would scope the stub to every caller whether wanted or not.
    """
    driver = soak_driver.SoakDriver(
        soak_driver.SoakConfig(
            duration_s=45,
            probe_diagnostics=probe_diagnostics,
            task_provenance=task_provenance,
        ),
        "http://127.0.0.1:1",
        "127.0.0.1:1",
        SimpleNamespace(fault_plan=None),
    )
    sampler = soak_driver.ProcessMetricsSampler(object(), "http://127.0.0.1:1")
    real_sleep = asyncio.sleep
    sleeps: list[float] = []
    calls = 0

    async def runtime() -> dict[str, Any]:
        nonlocal calls
        calls += 1
        if hang_call is not None and calls == hang_call:
            await real_sleep(30.0)
        feed = script()
        if clock is not None:
            clock.advance_by_sample()
        return {"process": feed}

    async def status() -> str:
        return "connected"

    async def control(action: str) -> None:
        pass

    async def wait(*args: Any, **kwargs: Any) -> bool:
        return True

    async def connect(deadline: float) -> bool:
        return True

    async def sleep(delay: float) -> None:
        sleeps.append(delay)
        if clock is not None:
            clock.advance(delay)

    monkeypatch.setattr(sampler, "fetch_runtime", runtime)
    monkeypatch.setattr(sampler, "fetch_connection_status", status)
    monkeypatch.setattr(driver, "_post_radio_control", control)
    monkeypatch.setattr(driver, "_wait_connection", wait)
    monkeypatch.setattr(driver, "_do_connect_until_recovered", connect)
    monkeypatch.setattr(soak_driver.asyncio, "sleep", sleep)
    monkeypatch.setattr(driver, "_elapsed", lambda: 10.0)
    driver._sampler = sampler
    await driver._reconnect_cycle()
    return driver, sleeps


def _stability_status(entry: dict[str, Any], field: str) -> dict[str, Any]:
    return soak_driver._stability_criterion(
        soak_driver._stability_checks([entry], field)
    )


async def test_stability_post_probe_settles_a_transient_fd_surplus(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A draining transient must not read as a leak (MOR-3575, CI 39451768550)."""
    script = _process_script(
        [48, 48, 48, 56, 55, 54, 53, 52, 51, 50, 49, 48, 48, 48, 48, 48, 48]
    )
    driver, sleeps = await _run_cycle_with_process_script(monkeypatch, script)
    (entry,) = driver._reconnects
    assert entry["recovered"] is True
    assert entry["fdsPost"] == 48
    assert entry["tasksPost"] == 30
    assert _stability_status(entry, "fds")["status"] == "PASS"
    assert _stability_status(entry, "tasks")["status"] == "PASS"
    assert sleeps[0] == 3.0
    settle = sleeps[1:]
    assert settle and all(s == soak_driver._STABILITY_SETTLE_STEP_S for s in settle)
    assert sum(settle) < soak_driver._STABILITY_SETTLE_WINDOW_S


async def test_stability_post_probe_still_fails_a_persistent_fd_surplus(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A surplus that never drains still fails (MOR-3575 control)."""
    script = _process_script([48, 48, 48] + [55] * 30)
    driver, sleeps = await _run_cycle_with_process_script(monkeypatch, script)
    (entry,) = driver._reconnects
    assert entry["recovered"] is True
    assert entry["fdsPost"] == 55
    assert _stability_status(entry, "fds")["status"] == "FAIL"
    # The flat surplus never clears: the settle runs its full resample
    # budget (window/step = 20) before the re-probe still fails.
    assert sleeps == [3.0] + [soak_driver._STABILITY_SETTLE_STEP_S] * 20


async def test_stability_settle_waits_out_a_plateau_before_further_decay(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A flat pair mid-drain is not the settled value (MOR-3575)."""
    script = _process_script(
        [48, 48, 48, 56, 55, 54, 55, 55, 55, 54, 53, 52, 51, 50, 49, 48, 48, 48, 48, 48]
    )
    driver, sleeps = await _run_cycle_with_process_script(monkeypatch, script)
    (entry,) = driver._reconnects
    assert entry["fdsPost"] == 48
    assert _stability_status(entry, "fds")["status"] == "PASS"
    assert len(sleeps) > 2


async def test_stability_settle_is_bounded_by_its_window(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The window, not the resample budget, ends a never-settling feed.

    A controlled monotonic clock drives the deadline arithmetic: with a
    1.0 s window, every sample costs 0.1 s of it and every sleep
    advances the clock by its delay. After the first settle sample and
    one 0.25 s step the next settle sample starts with 0.65 s of window
    left — and that late sample hangs for 30 real seconds, so the
    in-task timeout on the remaining window gives up on it. The settle
    ends after a single step although the budget allows four, and the
    cycle still concludes with a FAIL on the never-settling counts.
    Remove the sample-bounding timeout and this goes red: the 30 s hang
    outruns the outer bound instead of being cancelled at the window's
    end. (The step-sleep clamp is pinned separately, by
    test_stability_settle_clamps_its_step_sleep_to_the_remaining_window.)
    """
    real_monotonic = time.monotonic
    monkeypatch.setattr(soak_driver, "_STABILITY_SETTLE_WINDOW_S", 1.0)
    clock = _SettleClock(sample_cost_s=0.1)
    monkeypatch.setattr(soak_driver, "time", SimpleNamespace(monotonic=clock.monotonic))
    falling = itertools.chain([48, 48, 48], itertools.count(10**9, -1))

    def script() -> dict[str, int]:
        return {"rssBytes": 1024, "openFds": next(falling), "asyncioTasks": 30}

    started = real_monotonic()
    driver, sleeps = await asyncio.wait_for(
        _run_cycle_with_process_script(monkeypatch, script, hang_call=8, clock=clock),
        timeout=10.0,
    )
    assert real_monotonic() - started < 5.0
    (entry,) = driver._reconnects
    assert entry["recovered"] is True
    assert _stability_status(entry, "fds")["status"] == "FAIL"
    # One clamped step sleep; the late hung sample consumed the rest of
    # the window, so the settle ended by the window — not by its budget
    # of four steps (window 1.0 s / step 0.25 s) nor by a calm reading
    # (the counts fall forever, so calm never reaches its threshold).
    assert sleeps == [3.0, soak_driver._STABILITY_SETTLE_STEP_S]


async def test_stability_settle_clamps_its_step_sleep_to_the_remaining_window(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A step sleep never outruns the window (MOR-3575).

    A controlled clock with 0.8 s samples leaves 0.2 s of the 1.0 s
    window after the settle's first sample, so the step sleep is
    clamped to that 0.2 s instead of its full 0.25 s; the next
    resample then starts past the window and the settle ends. Remove
    the clamp and this goes red: the recorded sleep is the full 0.25 s
    step.
    """
    monkeypatch.setattr(soak_driver, "_STABILITY_SETTLE_WINDOW_S", 1.0)
    clock = _SettleClock(sample_cost_s=0.8)
    monkeypatch.setattr(soak_driver, "time", SimpleNamespace(monotonic=clock.monotonic))
    falling = itertools.chain([48, 48, 48], itertools.count(10**9, -1))

    def script() -> dict[str, int]:
        return {"rssBytes": 1024, "openFds": next(falling), "asyncioTasks": 30}

    driver, sleeps = await _run_cycle_with_process_script(
        monkeypatch, script, clock=clock
    )
    (entry,) = driver._reconnects
    assert entry["recovered"] is True
    assert _stability_status(entry, "fds")["status"] == "FAIL"
    assert sleeps[0] == 3.0
    # Clamped to the 0.2 s left of the window — not the full 0.25 s step
    # (the controlled clock's deadline arithmetic carries float error,
    # so compare approximately and against the step bound).
    assert sleeps[1] == pytest.approx(1.0 - 0.8)
    assert sleeps[1] < soak_driver._STABILITY_SETTLE_STEP_S


async def test_stability_settle_gives_up_on_a_hung_sample(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A sample past the window ends the settle; the cycle still concludes.

    The 7th runtime call is the settle's first sample; it hangs 30 real
    seconds against a real 50 ms window, and the in-task timeout on
    the remaining window cancels it. The outer 10 s bound is the
    discrimination: without the timeout in _settle_sample the injected
    hang is unguarded, the bound fires TimeoutError, and this test goes
    red instead of passing after 30 s.
    """
    monkeypatch.setattr(soak_driver, "_STABILITY_SETTLE_WINDOW_S", 0.05)
    script = _process_script([48, 48, 48, 56, 55, 54, 48, 48, 48])
    started = time.monotonic()
    driver, sleeps = await asyncio.wait_for(
        _run_cycle_with_process_script(
            monkeypatch,
            script,
            hang_call=2 * soak_driver._STABILITY_PROBE_COUNT + 1,
        ),
        timeout=10.0,
    )
    assert time.monotonic() - started < 5.0
    (entry,) = driver._reconnects
    assert entry["recovered"] is True
    assert entry["fdsPost"] == 48
    assert _stability_status(entry, "fds")["status"] == "PASS"
    assert sleeps == [3.0]


async def test_stability_settle_counts_no_task_of_its_own(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The settle's bound must not add a task to the counts it takes.

    Five long-lived tasks make a task delta that sits exactly at
    tolerance, and the sampler counts the live loop both ways the
    report does — ``len(asyncio.all_tasks())`` (the runtime route,
    server.py) and the non-``soak-`` fallback. CI's quick suite runs
    Python 3.11 (``UV_PYTHON: "3.11"`` in quick.yml), whose
    ``wait_for`` runs the awaited coroutine as a task of its own;
    Python 3.12+ reimplemented ``wait_for`` over ``asyncio.timeout``,
    in-task, so this host's 3.13 cannot show the bias unaided and the
    monkeypatched ``wait_for`` restores the 3.11 wrap. Under that
    wrap a ``wait_for``-bounded settle measures one task above the
    delta, the boundary reads as a surplus that never drains, and the
    settle spins to its window; the in-task bound never calls
    ``wait_for``, both routes read exactly the delta, and the settle
    ends after the initial sample plus three stable readings.
    """
    driver = soak_driver.SoakDriver(
        soak_driver.SoakConfig(duration_s=45),
        "http://127.0.0.1:1",
        "127.0.0.1:1",
        SimpleNamespace(fault_plan=None),
    )
    runtime_route_counts: list[int] = []
    fallback_route_counts: list[int] = []

    class _LiveTaskCountingSampler:
        async def sample(
            self,
            t_s: float,
            capture: Any = None,
            *,
            task_probe: Any = None,
        ) -> soak_driver.ProcessSample:
            runtime_route_counts.append(len(asyncio.all_tasks()))
            fallback_route_counts.append(soak_driver._server_asyncio_task_count())
            return soak_driver.ProcessSample(
                t_s=t_s,
                rss_bytes=1024,
                fds=48,
                asyncio_tasks=fallback_route_counts[-1],
                connection="connected",
            )

    driver._sampler = _LiveTaskCountingSampler()
    real_wait_for = asyncio.wait_for

    async def wait_for_wrapping_its_awaitable_in_a_task(
        awaiting: Any, timeout: float
    ) -> Any:
        return await real_wait_for(asyncio.ensure_future(awaiting), timeout)

    monkeypatch.setattr(
        soak_driver.asyncio, "wait_for", wait_for_wrapping_its_awaitable_in_a_task
    )
    baseline_runtime = len(asyncio.all_tasks())
    baseline_fallback = soak_driver._server_asyncio_task_count()
    extras = [
        asyncio.create_task(asyncio.sleep(30.0))
        for _ in range(soak_driver.STABILITY_TOLERANCE)
    ]
    try:
        pre = soak_driver.ProcessSample(
            t_s=0.0,
            rss_bytes=1024,
            fds=48,
            asyncio_tasks=baseline_fallback,
            connection="connected",
        )
        await driver._settle_stability_counts(pre)
    finally:
        for extra in extras:
            extra.cancel()
        await asyncio.gather(*extras, return_exceptions=True)
    assert fallback_route_counts
    assert all(
        count - baseline_fallback == soak_driver.STABILITY_TOLERANCE
        for count in fallback_route_counts
    )
    assert all(
        count - baseline_runtime == soak_driver.STABILITY_TOLERANCE
        for count in runtime_route_counts
    )
    assert (
        len(fallback_route_counts) == 1 + soak_driver._STABILITY_SETTLE_STABLE_READINGS
    )


async def test_stability_settle_waits_out_a_long_plateau_before_late_decay(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A plateau above tolerance is not settled until it decays (MOR-3575)."""
    script = _process_script(
        [48, 48, 48, 56, 55, 54]
        + [55] * 8
        + [54, 53, 52, 51, 50, 49, 48, 48, 48, 48, 48, 48, 48]
    )
    driver, sleeps = await _run_cycle_with_process_script(monkeypatch, script)
    (entry,) = driver._reconnects
    assert entry["fdsPost"] == 48
    assert _stability_status(entry, "fds")["status"] == "PASS"
    assert len(sleeps[1:]) > 3


async def test_stability_settle_applies_under_probe_diagnostics(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Capture must not disable the settle: same transient, same PASS (MOR-3575).

    The transient trio is dropped so the settled re-probe fits the
    six-probe bound; the report keeps the settled post trio.
    """
    script = _process_script(
        [48, 48, 48, 56, 55, 54, 53, 52, 51, 50, 49, 48, 48, 48, 48, 48, 48]
    )
    driver, sleeps = await _run_cycle_with_process_script(
        monkeypatch, script, probe_diagnostics=True
    )
    (entry,) = driver._reconnects
    assert entry["recovered"] is True
    assert entry["fdsPost"] == 48
    assert _stability_status(entry, "fds")["status"] == "PASS"
    assert len(sleeps) > 1
    diagnostic = driver._build_report()["probeDiagnostics"]
    assert diagnostic["maxProbes"] == 6
    assert [p["phase"] for p in diagnostic["probes"]] == ["pre"] * 3 + ["post"] * 3
    assert [p["sequence"] for p in diagnostic["probes"]] == list(range(1, 7))
    assert diagnostic["phases"]["post"]["median"]["fds"] == 48
    assert diagnostic["phases"]["post"]["status"] == "complete"


async def test_stability_settle_applies_under_task_provenance(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Task capture must not disable the settle either (MOR-3575).

    Nine probes begin (the transient post trio plus its settled
    re-probe) but the transient trio is dropped first, so at most six
    are ever retained.
    """
    script = _process_script(
        [48, 48, 48, 56, 55, 54, 53, 52, 51, 50, 49, 48, 48, 48, 48, 48, 48]
    )
    calls: list[str] = []
    retained_high_water = 0
    original = soak_driver._TaskProvenance.begin_probe

    def counting(self: Any, phase: str) -> dict[str, Any]:
        nonlocal retained_high_water
        calls.append(phase)
        probe = original(self, phase)
        assert self.active is not None
        retained_high_water = max(retained_high_water, len(self.active["probes"]))
        return probe

    monkeypatch.setattr(soak_driver._TaskProvenance, "begin_probe", counting)
    driver, sleeps = await _run_cycle_with_process_script(
        monkeypatch, script, task_provenance=True
    )
    (entry,) = driver._reconnects
    assert entry["recovered"] is True
    assert entry["fdsPost"] == 48
    assert _stability_status(entry, "fds")["status"] == "PASS"
    assert len(sleeps) > 1
    assert calls == ["pre"] * 3 + ["post"] * 6
    assert retained_high_water == 6


async def test_stability_post_probe_settles_a_transient_tasks_surplus(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The settle covers task counts too: FDs stable, tasks drain (MOR-3575)."""
    script = _process_script(
        [48] * 18,
        tasks_values=[30] * 3 + [38, 37, 36, 35, 34, 33, 32, 31] + [30] * 7,
    )
    driver, sleeps = await _run_cycle_with_process_script(monkeypatch, script)
    (entry,) = driver._reconnects
    assert entry["recovered"] is True
    assert entry["fdsPost"] == 48
    assert entry["tasksPost"] == 30
    assert _stability_status(entry, "fds")["status"] == "PASS"
    assert _stability_status(entry, "tasks")["status"] == "PASS"
    # Eight resamples: every task decrease resets the calm count.
    assert len(sleeps[1:]) == 8


async def test_stability_post_probe_still_fails_a_persistent_tasks_surplus(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A task surplus that never drains still fails; FDs stay green (MOR-3575)."""
    script = _process_script([48] * 30, tasks_values=[30, 30, 30] + [37] * 27)
    driver, sleeps = await _run_cycle_with_process_script(monkeypatch, script)
    (entry,) = driver._reconnects
    assert entry["recovered"] is True
    assert entry["fdsPost"] == 48
    assert entry["tasksPost"] == 37
    assert _stability_status(entry, "fds")["status"] == "PASS"
    assert _stability_status(entry, "tasks")["status"] == "FAIL"
    assert sleeps == [3.0] + [soak_driver._STABILITY_SETTLE_STEP_S] * 20


# ---------------------------------------------------------------------------
# MOR-3575 step 3: the in-process provider counts only what the run owns
# ---------------------------------------------------------------------------


def _stability_criteria_for(
    fds_pre: int, fds_post: int, tasks_pre: int, tasks_post: int
) -> dict[str, Any]:
    """The report's own criterion math (unchanged tolerance) on one cycle."""
    entry = _one_reconnect(300.0, 320.0)
    entry.update(
        fdsPre=fds_pre,
        fdsPost=fds_post,
        tasksPre=tasks_pre,
        tasksPost=tasks_post,
    )
    return soak_driver.evaluate_report(
        _smoke_config(duration_s=3600.0),
        _hour_of_samples(slope_bytes_per_s=1000.0),
        [entry],
    )


def _listener(backlog: int = 1) -> socket.socket:
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    sock.listen(backlog)
    return sock


def test_owned_socket_fd_count_sees_only_run_owned_ports() -> None:
    """Unrelated listeners and pipes are invisible; owned sockets count
    while open, by exactly one each, and stop counting once closed."""
    unrelated = _listener()
    read_fd, write_fd = os.pipe()
    owned = _listener()
    ports = frozenset({owned.getsockname()[1]})
    client = accepted = None
    try:
        assert soak_driver._owned_socket_fd_count(frozenset()) == 0
        assert soak_driver._owned_socket_fd_count(ports) == 1
        client = socket.create_connection(owned.getsockname())
        accepted, _ = owned.accept()
        assert soak_driver._owned_socket_fd_count(ports) == 3
        accepted.close()
        client.close()
        assert soak_driver._owned_socket_fd_count(ports) == 1
    finally:
        for resource in (client, accepted, owned, unrelated):
            if resource is not None:
                resource.close()
        os.close(read_fd)
        os.close(write_fd)


def test_leaked_run_owned_sockets_fail_the_stability_criterion() -> None:
    """Negative controls through the real criterion (MOR-3575).

    Six leaked run-owned sockets — the smallest leak over the unchanged
    tolerance — FAIL the criterion; one leak registers its exact delta of
    one (the live signal carries legitimate +-1 transients, e.g. the web
    server's delayed state broadcasts, so failing on a single one would
    re-introduce the flake this change removes). An unrelated FD between
    the probes must not change the verdict — that FD is the CI flake
    (run 39451768550).
    """
    owned = _listener(backlog=8)
    ports = frozenset({owned.getsockname()[1]})
    unrelated = _listener()
    read_fd, write_fd = os.pipe()
    leaks: list[socket.socket] = []
    try:
        pre = soak_driver._owned_socket_fd_count(ports)
        leaks.append(socket.create_connection(owned.getsockname()))
        one = soak_driver._owned_socket_fd_count(ports)
        assert one == pre + 1
        one_leak = _stability_criteria_for(pre, one, 25, 25)
        assert one_leak["fdsStability"]["checks"][0]["delta"] == 1
        assert one_leak["fdsStability"]["status"] == "PASS"
        for _ in range(soak_driver.STABILITY_TOLERANCE):
            leaks.append(socket.create_connection(owned.getsockname()))
        post = soak_driver._owned_socket_fd_count(ports)
        assert post == pre + 1 + soak_driver.STABILITY_TOLERANCE
        criteria = _stability_criteria_for(pre, post, 25, 25)
        assert criteria["fdsStability"]["status"] == "FAIL"
        assert criteria["fdsStability"]["checks"][0]["delta"] == (
            1 + soak_driver.STABILITY_TOLERANCE
        )
        for leak in leaks:
            leak.close()
        leaks.clear()
        post = soak_driver._owned_socket_fd_count(ports)
        assert post == pre  # the unrelated listener and pipe never counted
        assert (
            _stability_criteria_for(pre, post, 25, 25)["fdsStability"]["status"]
            == "PASS"
        )
    finally:
        for leak in leaks:
            leak.close()
        owned.close()
        unrelated.close()
        os.close(read_fd)
        os.close(write_fd)


async def test_leaked_run_owned_tasks_fail_the_stability_criterion(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Six leaked run-owned tasks FAIL the criterion; one registers its
    exact delta; a foreign-module task (asyncio's own sleep — the
    late-teardown noise the CI flake counted) never moves the count."""

    async def linger() -> None:
        await asyncio.Event().wait()

    monkeypatch.setattr(soak_driver, "_OWNED_TASK_MODULE_PREFIXES", (__name__,))
    leaks: list[asyncio.Task[None]] = []
    foreign: asyncio.Task[None] | None = None
    try:
        pre = len(soak_driver._owned_task_population())
        leaks.append(asyncio.create_task(linger()))
        one = len(soak_driver._owned_task_population())
        assert one == pre + 1
        one_leak = _stability_criteria_for(20, 20, pre, one)
        assert one_leak["tasksStability"]["checks"][0]["delta"] == 1
        assert one_leak["tasksStability"]["status"] == "PASS"
        for _ in range(soak_driver.STABILITY_TOLERANCE):
            leaks.append(asyncio.create_task(linger()))
        post = len(soak_driver._owned_task_population())
        assert post == pre + 1 + soak_driver.STABILITY_TOLERANCE
        assert (
            _stability_criteria_for(20, 20, pre, post)["tasksStability"]["status"]
            == "FAIL"
        )
        foreign = asyncio.create_task(asyncio.sleep(30.0))
        assert len(soak_driver._owned_task_population()) == post
    finally:
        pending = [*leaks]
        if foreign is not None:
            pending.append(foreign)
        for task in pending:
            task.cancel()
        if pending:
            await asyncio.gather(*pending, return_exceptions=True)


async def test_in_process_runtime_payload_reports_owned_counts_only() -> None:
    """Through the harness overlay the runtime ``process`` block reports
    owned counts: an unrelated FD leaves it unchanged, a run-owned-port
    socket moves it by one while open (MOR-3575)."""

    class Web:
        _radio = None
        _reconnects_total = 0

        def _runtime_process_payload(self) -> dict[str, Any]:
            return soak_driver.WebServer._runtime_process_payload(self)

        async def _serve_runtime(self, *args: Any) -> None:  # pragma: no cover
            ...

    web = Web()
    owned = _listener()
    port = owned.getsockname()[1]
    stack = SimpleNamespace(
        web=web, radio=SimpleNamespace(), rigctld=SimpleNamespace(), web_port=port
    )
    adapter = soak_driver._RuntimeTaskAdapter(stack, None)
    adapter.install()
    unrelated = client = None
    try:
        baseline = web._runtime_process_payload()
        assert baseline["openFds"] is not None
        unrelated = _listener()
        before = web._runtime_process_payload()
        # The unrelated listener must not move the owned FD count — the
        # process-wide number the payload used to report moved by one.
        assert before["openFds"] == baseline["openFds"]
        assert before["asyncioTasks"] == baseline["asyncioTasks"]
        client = socket.create_connection(("127.0.0.1", port))
        after = web._runtime_process_payload()
        assert after["openFds"] == before["openFds"] + 1
        assert after["asyncioTasks"] == before["asyncioTasks"]
        client.close()
        assert web._runtime_process_payload()["openFds"] == before["openFds"]
    finally:
        for resource in (client, unrelated, owned):
            if resource is not None:
                resource.close()
        adapter.close()
    assert "_runtime_process_payload" not in vars(web)
    assert "_serve_runtime" not in vars(web)
