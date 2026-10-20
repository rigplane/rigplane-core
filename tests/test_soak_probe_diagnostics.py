from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace
from typing import Any

import pytest

from scripts.soak import soak_driver


def _driver(enabled: bool) -> soak_driver.SoakDriver:
    return soak_driver.SoakDriver(
        soak_driver.SoakConfig(duration_s=45, probe_diagnostics=enabled),
        "http://secret.invalid/auth",
        "secret.invalid:1234",
        SimpleNamespace(fault_plan=None),
    )


async def _cycle(
    monkeypatch: pytest.MonkeyPatch,
    enabled: bool,
    *,
    recovered: bool = True,
    interrupt_at: int | None = None,
    cancelled: bool = False,
) -> tuple[soak_driver.SoakDriver, list[dict[str, int]], list[str | float]]:
    driver = _driver(enabled)
    sampler = soak_driver.ProcessMetricsSampler(object(), "http://secret.invalid/auth")
    raw = [
        {"rssBytes": rss, "openFds": fds, "asyncioTasks": tasks}
        for rss, fds, tasks in (
            (100, 10, 51),
            (900, 40, 53),
            (200, 20, 52),
            (500, 12, 45),
            (300, 22, 47),
            (400, 20, 46),
        )
    ]
    calls: list[str | float] = []
    index = 0

    async def runtime() -> dict[str, Any]:
        nonlocal index
        if index == interrupt_at:
            if cancelled:
                raise asyncio.CancelledError
            raise RuntimeError("secret exception with auth body")
        value = raw[index % len(raw)]
        index += 1
        return {"process": value, "auth": "secret body"}

    async def status() -> str:
        return "connected"

    async def control(action: str) -> None:
        calls.append(action)

    async def wait(*args: Any, **kwargs: Any) -> bool:
        return True

    async def connect(deadline: float) -> bool:
        calls.append("connect")
        return recovered

    async def sleep(delay: float) -> None:
        calls.append(delay)

    monkeypatch.setattr(sampler, "fetch_runtime", runtime)
    monkeypatch.setattr(sampler, "fetch_connection_status", status)
    monkeypatch.setattr(driver, "_post_radio_control", control)
    monkeypatch.setattr(driver, "_wait_connection", wait)
    monkeypatch.setattr(driver, "_do_connect_until_recovered", connect)
    monkeypatch.setattr(soak_driver.asyncio, "sleep", sleep)
    monkeypatch.setattr(driver, "_elapsed", lambda: 10.0)
    driver._sampler = sampler
    if cancelled:
        with pytest.raises(asyncio.CancelledError):
            await driver._reconnect_cycle()
    else:
        await driver._reconnect_cycle()
    return driver, raw, calls


async def test_disabled_equivalence_and_actual_six_probe_medians(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    disabled, _, disabled_calls = await _cycle(monkeypatch, False)
    enabled, raw, enabled_calls = await _cycle(monkeypatch, True)
    before = disabled._build_report()
    after = enabled._build_report()
    diagnostic = after.pop("probeDiagnostics")
    before.pop("startedAt")
    after.pop("startedAt")
    assert json.dumps(before) == json.dumps(after)
    assert disabled._probe_diagnostics is None
    assert disabled_calls == enabled_calls == ["disconnect", "connect", 3.0]
    assert len(diagnostic["probes"]) == 6
    assert [p["sequence"] for p in diagnostic["probes"]] == list(range(1, 7))
    assert [p["phase"] for p in diagnostic["probes"]] == ["pre"] * 3 + ["post"] * 3
    for probe, source in zip(diagnostic["probes"], raw, strict=True):
        assert probe["sample"] == {
            "t": 10.0,
            "rssBytes": source["rssBytes"],
            "fds": source["openFds"],
            "asyncioTasks": source["asyncioTasks"],
            "connection": "connected",
        }
        assert probe["endedMonotonicS"] >= probe["startedMonotonicS"]
        assert all(
            s["runtimeMissingReason"] is None for s in probe["selections"].values()
        )
    assert diagnostic["phases"]["pre"]["median"] == {
        "t": 10.0,
        "rssBytes": 200,
        "fds": 20,
        "asyncioTasks": 52,
        "connection": "connected",
    }
    assert diagnostic["phases"]["post"]["median"] == {
        "t": 10.0,
        "rssBytes": 400,
        "fds": 20,
        "asyncioTasks": 46,
        "connection": "connected",
    }
    assert diagnostic["outcome"] == "recovered"
    assert set(diagnostic["boundariesMonotonicS"]) == soak_driver._DIAGNOSTIC_BOUNDARIES
    boundary_times = list(diagnostic["boundariesMonotonicS"].values())
    assert boundary_times == sorted(boundary_times)
    assert "secret" not in json.dumps(diagnostic)
    frozen = json.dumps(diagnostic)
    await enabled._reconnect_cycle()
    assert json.dumps(enabled._probe_diagnostics.to_dict()) == frozen


@pytest.mark.parametrize("interrupt_at", [None, 3, 4, 5])
async def test_unrecovered_and_partial_post_are_explicit(
    monkeypatch: pytest.MonkeyPatch,
    interrupt_at: int | None,
) -> None:
    driver, _, calls = await _cycle(
        monkeypatch,
        True,
        recovered=interrupt_at is not None,
        interrupt_at=interrupt_at,
    )
    diagnostic = driver._build_report()["probeDiagnostics"]
    assert diagnostic["phases"]["pre"]["completed"] == 3
    assert diagnostic["phases"]["post"]["median"] is None
    if interrupt_at is None:
        assert diagnostic["outcome"] == "unrecovered"
        assert diagnostic["phases"]["post"]["status"] == "absent"
        assert len(diagnostic["probes"]) == 3
        assert calls == ["disconnect", "connect"]
        assert "settleStart" not in diagnostic["boundariesMonotonicS"]
    else:
        assert diagnostic["outcome"] == "raised"
        assert diagnostic["phases"]["post"]["status"] == "partial"
        assert diagnostic["phases"]["post"]["completed"] == interrupt_at - 3
        assert diagnostic["probes"][-1]["sample"] is None
        assert diagnostic["probes"][-1]["endedMonotonicS"] is not None
    assert not driver._reconnects[0]["recovered"]
    assert "secret" not in json.dumps(diagnostic)


async def test_cancelled_post_preserves_pre_and_propagates_cancellation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    driver, _, _ = await _cycle(monkeypatch, True, interrupt_at=4, cancelled=True)
    diagnostic = driver._build_report()["probeDiagnostics"]
    assert diagnostic["outcome"] == "cancelled"
    assert diagnostic["phases"]["pre"]["completed"] == 3
    assert diagnostic["phases"]["post"] == {
        "status": "partial",
        "attempted": 2,
        "completed": 1,
        "median": None,
    }
    assert diagnostic["probes"][-1]["endedMonotonicS"] is not None
    assert not driver._reconnects[0]["recovered"]
    assert not driver._capture_cycle


async def test_partial_pre_and_no_started_cycle_are_explicit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    empty = _driver(True)._build_report()["probeDiagnostics"]
    assert empty["outcome"] == "not_started"
    assert empty["probes"] == []
    assert empty["phases"]["post"]["status"] == "absent"
    driver, _, calls = await _cycle(monkeypatch, True, interrupt_at=1)
    diagnostic = driver._build_report()["probeDiagnostics"]
    assert diagnostic["outcome"] == "raised"
    assert diagnostic["phases"]["pre"] == {
        "status": "partial",
        "attempted": 2,
        "completed": 1,
        "median": None,
    }
    assert diagnostic["phases"]["post"]["status"] == "absent"
    assert calls == []


@pytest.mark.parametrize(
    ("runtime", "reason"),
    [
        (None, "runtime_unavailable"),
        ({}, "process_missing"),
        ({"process": "secret body"}, "process_invalid"),
        ({"process": {}}, "field_missing"),
        ({"process": {"rssBytes": None}}, "field_null"),
        ({"process": {"rssBytes": True}}, "field_invalid"),
    ],
)
async def test_selection_branches_and_missing_reasons(
    monkeypatch: pytest.MonkeyPatch,
    runtime: dict[str, Any] | None,
    reason: str,
) -> None:
    sampler = soak_driver.ProcessMetricsSampler(object(), "http://secret.invalid/auth")

    async def fetch() -> dict[str, Any] | None:
        return runtime

    async def fallback(pid: int) -> tuple[int, None]:
        return (123, None)

    async def status() -> None:
        return None

    monkeypatch.setattr(sampler, "fetch_runtime", fetch)
    monkeypatch.setattr(sampler, "fetch_connection_status", status)
    monkeypatch.setattr(soak_driver, "_driver_side_process_metrics", fallback)
    monkeypatch.setattr(soak_driver, "_server_asyncio_task_count", lambda: 7)
    diagnostics = soak_driver._ProbeDiagnostics()
    capture = diagnostics.begin("pre")
    sample = await sampler.sample(2.0, capture)
    assert sample == soak_driver.ProcessSample(2.0, 123, None, 7, None)
    assert capture.selections["rssBytes"] == {
        "route": "driver",
        "runtimeMissingReason": reason,
        "fallbackMissingReason": None,
    }
    assert capture.selections["fds"]["route"] == "missing"
    assert (
        capture.selections["fds"]["fallbackMissingReason"] == "measurement_unavailable"
    )
    assert capture.selections["asyncioTasks"]["route"] == "driver"
    assert (
        capture.selections["connection"]["runtimeMissingReason"] == "state_unavailable"
    )
    assert "secret" not in json.dumps(diagnostics.to_dict())


async def test_mixed_routes_preserve_each_actual_selected_value(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sampler = soak_driver.ProcessMetricsSampler(object(), "http://secret.invalid/auth")

    async def runtime() -> dict[str, Any]:
        return {"process": {"rssBytes": 42, "openFds": None, "asyncioTasks": "secret"}}

    async def fallback(pid: int) -> tuple[int, int]:
        return 900, 12

    async def status() -> str:
        return "disconnected"

    monkeypatch.setattr(sampler, "fetch_runtime", runtime)
    monkeypatch.setattr(sampler, "fetch_connection_status", status)
    monkeypatch.setattr(soak_driver, "_driver_side_process_metrics", fallback)
    monkeypatch.setattr(soak_driver, "_server_asyncio_task_count", lambda: 46)
    capture = soak_driver._ProbeDiagnostics().begin("post")
    sample = await sampler.sample(2.0, capture)
    assert sample == soak_driver.ProcessSample(2.0, 42, 12, 46, "disconnected")
    assert capture.selections == {
        "rssBytes": {
            "route": "runtime",
            "runtimeMissingReason": None,
            "fallbackMissingReason": None,
        },
        "fds": {
            "route": "driver",
            "runtimeMissingReason": "field_null",
            "fallbackMissingReason": None,
        },
        "asyncioTasks": {
            "route": "driver",
            "runtimeMissingReason": "field_invalid",
            "fallbackMissingReason": None,
        },
        "connection": {
            "route": "state",
            "runtimeMissingReason": None,
            "fallbackMissingReason": None,
        },
    }


def test_allowlists_and_storage_limits() -> None:
    diagnostic = soak_driver._ProbeDiagnostics()
    for phase in ("pre", "post"):
        for _ in range(3):
            probe = diagnostic.begin(phase)
    for phase in ("pre", "post", "secret phase"):
        with pytest.raises(ValueError):
            diagnostic.begin(phase)
    for args in (
        ("secret", "runtime", None),
        ("fds", "secret", None),
        ("fds", "missing", "secret"),
    ):
        with pytest.raises(ValueError):
            probe.select(*args)
    with pytest.raises(ValueError):
        diagnostic.boundary("secret")
    assert len(diagnostic.probes) == 6
    values = soak_driver._diagnostic_sample_values(
        soak_driver.ProcessSample(1.0, 1, 2, 3, "secret auth body")
    )
    assert values["connection"] is None
    assert "secret" not in json.dumps(diagnostic.to_dict())


def test_cli_defaults_and_short_single_attempt_cadence() -> None:
    parser = soak_driver._build_parser()
    defaults = parser.parse_args([])
    assert not defaults.probe_diagnostics
    assert defaults.reconnect_interval == soak_driver.RECONNECT_INTERVAL_S
    args = parser.parse_args(
        [
            "--duration",
            "45",
            "--reconnect-interval",
            "30",
            "--probe-diagnostics",
        ]
    )
    assert args.probe_diagnostics
    assert args.duration == 45
    assert args.reconnect_interval == 30


async def test_reconnect_loop_cannot_start_second_attempt_before_45_seconds(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    driver = _driver(True)
    driver._cfg.reconnect_interval_s = 30
    now = 0.0
    attempts = 0

    async def sleep(delay: float) -> None:
        nonlocal now
        now += delay

    async def cycle() -> None:
        nonlocal attempts
        attempts += 1

    monkeypatch.setattr(driver, "_elapsed", lambda: now)
    monkeypatch.setattr(soak_driver.asyncio, "sleep", sleep)
    monkeypatch.setattr(driver, "_reconnect_cycle", cycle)
    await driver._reconnect_loop()
    assert attempts == 1
    assert now == 45
