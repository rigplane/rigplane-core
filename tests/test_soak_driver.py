"""20-second smoke of the operated soak driver against the simulator.

Drives the full in-process production stack — ``MockIcomRadio`` +
``IcomRadio`` + ``WebServer`` + ``RigctldServer`` — through the soak driver
(``scripts/soak/soak_driver.py``) at 1-second cadences with one forced
radio disconnect/connect, and pins:

* the JSON report shape and a green verdict for the short run (the RSS
  criterion correctly reports NOT_EVALUATED below its fit-span floor);
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
* the report file and its verdict line survive a stack-teardown
  ``stop()`` that raises;
* a reconnect cycle still running at the run deadline must not wedge
  the run (the periodic loops must always yield).
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

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


async def test_soak_smoke_green_verdict_and_report_shape() -> None:
    report = await soak_driver.run_soak(_smoke_config())

    assert report["verdict"] == "PASS"
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
    assert report["verdict"] in ("PASS", "FAIL")


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
    assert report["verdict"] == "PASS"
    assert report["durationConfiguredS"] == 6.0
