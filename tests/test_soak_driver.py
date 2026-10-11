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
* the recovery criterion's teeth under the forced-fault run the ticket
  names (``FaultPlan`` drop 100 % for 3 s): a driver that skips the
  reconnect half must report an unrecovered disconnect and a FAIL
  verdict.
"""

from __future__ import annotations

from typing import Any

import pytest

from scripts.soak import soak_driver

# asyncio_mode=auto (pyproject.toml) drives the async tests; no marker.

RSS_LIMIT = 50 * 1024 * 1024


def _smoke_config(**overrides: Any) -> soak_driver.SoakConfig:
    defaults: dict[str, Any] = dict(
        duration_s=20.0,
        url=None,
        rigctld_url=None,
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
            threads=4,
            asyncio_tasks=25,
            connection="connected",
            source="driver-side",
        )
        for t in [i * step_s for i in range(int(3600 / step_s) + 1)]
    ]


def _one_reconnect(start_s: float, end_s: float) -> dict[str, Any]:
    return {
        "startedAtS": start_s,
        "recovered": True,
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
    # 1000 B/s = 3.6 MB/h, well under the 50 MB/h limit.
    assert criteria["rssGrowth"]["status"] == "PASS"
    assert criteria["recovery"]["status"] == "NOT_EVALUATED"


def test_evaluate_report_fails_steep_slope() -> None:
    config = _smoke_config(duration_s=3600.0)
    criteria = soak_driver.evaluate_report(
        config, _hour_of_samples(slope_bytes_per_s=20_000.0), []
    )
    # 20 kB/s = 72 MB/h, over the 50 MB/h limit.
    assert criteria["rssGrowth"]["status"] == "FAIL"


def test_evaluate_report_excludes_reconnect_window_from_rss_fit() -> None:
    config = _smoke_config(duration_s=3600.0)
    samples = _hour_of_samples(slope_bytes_per_s=1000.0)
    # A 30 MB allocation burst inside the reconnect window must not move
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


async def test_soak_smoke_green_verdict_and_report_shape() -> None:
    report = await soak_driver.run_soak(_smoke_config())

    assert report["verdict"] == "PASS"
    assert report["mode"] == "simulator"
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

    samples = report["samples"]
    assert len(samples) >= 10
    assert all(sample["source"] == "driver-side" for sample in samples)
    assert all(sample["rssBytes"] is not None for sample in samples)
    assert all(sample["fds"] is not None for sample in samples)
    assert all(sample["asyncioTasks"] is not None for sample in samples)


async def test_soak_recovery_criterion_fails_when_reconnect_is_skipped(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The unrecovered-disconnect assertion has teeth under the fault.

    The forced-fault run the ticket names (``FaultPlan`` drop 100 % for
    3 s around the reconnect), with the driver's connect half replaced by
    a no-op (the production mutation "skip reconnects"), must report an
    unrecovered disconnect and a FAIL verdict.
    """

    async def _never_connect(self: soak_driver.SoakDriver, deadline_s: float) -> bool:
        return False

    monkeypatch.setattr(
        soak_driver.SoakDriver, "_do_connect_until_recovered", _never_connect
    )
    report = await soak_driver.run_soak(
        _smoke_config(reconnect_budget_s=3.0, fault_drop=1.0)
    )

    assert report["criteria"]["recovery"]["unrecovered"] >= 1
    assert report["verdict"] == "FAIL"
