from __future__ import annotations

import asyncio
import gc
import json
import os
import weakref
from types import SimpleNamespace
from typing import Any

import pytest

from scripts.soak import soak_driver


def _collector() -> Any:
    cls = getattr(soak_driver, "_TaskProvenance", None)
    assert cls is not None, "opt-in task provenance collector is missing"
    return cls()


def _entry(pre: int | None, post: int | None, recovered: bool = True) -> dict[str, Any]:
    return {
        "startedAtS": 10.0,
        "recovered": recovered,
        "tasksPre": pre,
        "tasksPost": post,
    }


async def _pending() -> None:
    await asyncio.Event().wait()


async def test_runtime_producer_correlates_only_selected_request_and_restores() -> None:
    collector = _collector()
    collector.begin_cycle(1)
    probe = collector.begin_probe("pre")
    calls = 0
    payloads: list[dict[str, Any]] = []
    entered = asyncio.Event()
    resume = asyncio.Event()

    class Web:
        _radio = None
        _reconnects_total = 0
        _client_tasks: set[asyncio.Task[Any]] = set()
        _config = soak_driver.WebConfig()

        def _runtime_process_payload(self) -> dict[str, Any]:
            nonlocal calls
            calls += 1
            return soak_driver.WebServer._runtime_process_payload(self)

        async def _serve_runtime(
            self, writer: Any, headers: dict[str, str] | None = None
        ) -> None:
            if writer == "selected":
                entered.set()
                await resume.wait()
            payloads.append(self._runtime_process_payload())

    web = Web()
    stack = SimpleNamespace(web=web, radio=SimpleNamespace(), rigctld=SimpleNamespace())
    original_payload = web._runtime_process_payload
    original_handler = web._serve_runtime
    adapter = soak_driver._RuntimeTaskAdapter(stack, collector)
    adapter.install()
    try:
        with adapter.request(probe) as headers:
            reader = asyncio.StreamReader()
            raw_headers = "".join(
                f"{key}: {value}\r\n" for key, value in headers.items()
            )
            reader.feed_data(
                (
                    "GET /api/v1/runtime HTTP/1.1\r\nHost: localhost\r\n"
                    f"{raw_headers}\r\n"
                ).encode()
            )
            reader.feed_eof()
            request = await soak_driver.WebServer._read_request(web, reader)
            assert request is not None
            method, path, parsed_headers, query = request
            selected = asyncio.create_task(
                soak_driver.WebServer._handle_http(
                    web, "selected", method, path, parsed_headers, reader, query
                )
            )
            await entered.wait()
            await web._serve_runtime("unrelated", {})
            assert probe["snapshot"] is None
            resume.set()
            await selected
        snapshot = probe["snapshot"]
        assert snapshot["identityEligible"] is True
        assert snapshot["route"] == "runtime"
        assert snapshot["producerPid"] == os.getpid()
        assert snapshot["producerLoopLabel"] == 1
        assert snapshot["measuredCount"] == payloads[-1]["asyncioTasks"]
        assert snapshot["inventoryCount"] == payloads[-1]["asyncioTasks"]
        assert snapshot["countAgreement"] is True
        assert calls == 2
    finally:
        adapter.close()
        collector.close()
    assert web._runtime_process_payload == original_payload
    assert web._serve_runtime == original_handler
    assert "_runtime_process_payload" not in vars(web)
    assert "_serve_runtime" not in vars(web)


async def test_sampler_fallback_captures_actual_filtered_population(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    collector = _collector()
    collector.begin_cycle(1)
    probe = collector.begin_probe("pre")
    sampler = soak_driver.ProcessMetricsSampler(object(), "http://secret.invalid/auth")
    sampler._task_provenance = collector
    excluded = asyncio.create_task(_pending(), name="soak-secret-token")
    included = asyncio.create_task(_pending(), name="secret-user-name")

    async def runtime() -> None:
        return None

    async def metrics(pid: int) -> tuple[int, int]:
        return 123, 4

    async def status() -> str:
        return "connected"

    monkeypatch.setattr(sampler, "fetch_runtime", runtime)
    monkeypatch.setattr(sampler, "fetch_connection_status", status)
    monkeypatch.setattr(soak_driver, "_driver_side_process_metrics", metrics)
    try:
        sample = await sampler.sample(2.0, task_probe=probe)
        snapshot = probe["snapshot"]
        assert snapshot["route"] == "driver"
        assert snapshot["measuredCount"] == sample.asyncio_tasks
        assert snapshot["countAgreement"] is True
        assert (
            sum(t["countedByRoute"] for t in snapshot["tasks"]) == sample.asyncio_tasks
        )
        assert any(
            t["soakPrefix"] and not t["countedByRoute"] for t in snapshot["tasks"]
        )
        assert snapshot["identityEligible"] is True
        assert "secret" not in json.dumps(collector.to_dict())
    finally:
        collector.close()
        for task in (excluded, included):
            task.cancel()
        await asyncio.gather(excluded, included, return_exceptions=True)


async def test_runtime_without_correlated_producer_stays_unknown(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    collector = _collector()
    collector.begin_cycle(1)
    probe = collector.begin_probe("pre")
    sampler = soak_driver.ProcessMetricsSampler(object(), "http://secret.invalid/auth")
    sampler._task_provenance = collector

    async def runtime() -> dict[str, Any]:
        return {
            "pid": os.getpid(),
            "process": {"rssBytes": 1, "openFds": 2, "asyncioTasks": 52},
        }

    async def status() -> None:
        return None

    monkeypatch.setattr(sampler, "fetch_runtime", runtime)
    monkeypatch.setattr(sampler, "fetch_connection_status", status)
    try:
        sample = await sampler.sample(2.0, task_probe=probe)
        assert sample.asyncio_tasks == 52
        assert probe["snapshot"]["identityEligible"] is False
        assert probe["snapshot"]["coverage"] == "correlation_missing"
        assert probe["snapshot"]["tasks"] == []
    finally:
        collector.close()


async def test_weak_monotonic_labels_callbacks_redaction_and_cleanup() -> None:
    collector = _collector()
    collector.begin_cycle(1)
    task = asyncio.create_task(_pending(), name="secret-auth-token")
    reference = weakref.ref(task)
    probe = collector.begin_probe("pre")
    collector.capture(
        probe, "runtime", 1, tasks={task}, bindings=[(task, "radio.civ_rx")]
    )
    label = probe["snapshot"]["tasks"][0]["taskLabel"]
    again = collector.begin_probe("pre")
    collector.capture(
        again, "runtime", 1, tasks={task}, bindings=[(task, "radio.civ_rx")]
    )
    assert again["snapshot"]["tasks"][0]["taskLabel"] == label
    assert len(task._callbacks) == 1
    collector.finish_cycle(_entry(1, 1), "recovered")
    collector.begin_cycle(2)
    collector.capture(collector.begin_probe("pre"), "runtime", 1, tasks={task})
    assert collector.active["probes"][0]["snapshot"]["tasks"][0]["taskLabel"] == label
    task.cancel()
    await asyncio.gather(task, return_exceptions=True)
    events = collector.to_dict()["active"]["events"]
    assert len(events) == 1
    assert set(events[0]) == {"taskLabel", "observedMonotonicS", "done", "cancelled"}
    assert events[0]["taskLabel"] == label
    assert events[0]["done"] and events[0]["cancelled"]
    assert isinstance(events[0]["observedMonotonicS"], float)
    del task
    await asyncio.sleep(0)
    gc.collect()
    assert reference() is None
    replacement = asyncio.create_task(_pending(), name="secret-next-name")
    probe = collector.begin_probe("post")
    collector.capture(
        probe,
        "runtime",
        1,
        tasks={replacement},
        bindings=[(replacement, "radio.civ_rx")],
    )
    assert probe["snapshot"]["tasks"][0]["taskLabel"] > label
    encoded = json.dumps(collector.to_dict())
    assert "secret" not in encoded
    assert "createdAt" not in encoded
    assert "ownerGeneration" not in encoded
    assert "exception" not in encoded
    collector.close()
    assert not replacement._callbacks
    replacement.cancel()
    await asyncio.gather(replacement, return_exceptions=True)


async def test_done_observer_never_retrieves_exception() -> None:
    collector = _collector()
    collector.begin_cycle(1)

    async def fail() -> None:
        raise RuntimeError("secret-result")

    task = asyncio.create_task(fail())
    try:
        collector.capture(collector.begin_probe("pre"), "runtime", 1, tasks={task})
        await asyncio.sleep(0)
        await asyncio.sleep(0)
        assert task._log_traceback is True
        assert collector.to_dict()["active"]["events"][0]["cancelled"] is False
        assert "secret" not in json.dumps(collector.to_dict())
    finally:
        collector.close()
        assert isinstance(task.exception(), RuntimeError)


async def test_bounds_alias_unknown_and_no_complete_lifetime_claim() -> None:
    collector = _collector()
    collector.begin_cycle(1)
    tasks = {asyncio.create_task(_pending()) for _ in range(140)}
    task = next(iter(tasks))
    try:
        probe = collector.begin_probe("pre")
        collector.capture(
            probe,
            "runtime",
            140,
            tasks=tasks,
            bindings=[(task, "radio.civ_rx"), (task, "radio.watchdog")],
        )
        snapshot = probe["snapshot"]
        assert len(snapshot["tasks"]) == 128
        assert snapshot["inventoryCount"] == 140
        assert snapshot["coverage"] == "truncated"
        assert snapshot["roleCoverage"] == "unknown"
        assert snapshot["lifetimeCoverage"] == "observed_only"
        assert any(t["role"] == "unclassified" for t in snapshot["tasks"])
        assert len(collector._registry) <= 256
        for phase in ("pre", "pre", "post", "post", "post"):
            collector.capture(collector.begin_probe(phase), "runtime", 140, tasks=tasks)
        with pytest.raises(ValueError):
            collector.begin_probe("post")
        for owned in tasks:
            owned.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        assert len(collector.to_dict()["active"]["events"]) <= 256
    finally:
        collector.close()
        for owned in tasks:
            owned.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)


async def test_second_failure_retention_uses_existing_check_and_is_immutable() -> None:
    collector = _collector()
    for cycle, pre, post in ((1, 52, 47), (2, 52, 46), (3, 52, 60), (4, None, None)):
        collector.begin_cycle(cycle)
        collector.capture(collector.begin_probe("pre"), "runtime", 0, tasks=set())
        entry = _entry(pre, post)
        collector.finish_cycle(entry, "recovered")
        state = collector.to_dict()
        if cycle == 4:
            assert state["active"]["outcome"] == "missing_task_measurement"
            assert state["active"]["taskStability"] is None
        else:
            assert state["active"] is None
        if cycle == 1:
            assert state["firstFailure"] is None
        else:
            assert state["firstFailure"]["reconnect"] == 2
            expected = soak_driver._stability_checks([_entry(52, 46)], "tasks")[0]
            assert state["firstFailure"]["taskStability"] == expected
    frozen = json.dumps(collector.to_dict()["firstFailure"])
    collector.begin_cycle(5)
    state = collector.to_dict()
    assert state["active"]["reconnect"] == 5
    assert state["active"]["outcome"] == "in_progress"
    assert state["active"]["taskStability"] is None
    collector.finish_cycle(_entry(52, None, False), "unrecovered")
    assert collector.to_dict()["active"]["outcome"] == "unrecovered"
    assert collector.to_dict()["active"]["taskStability"] is None
    assert json.dumps(collector.to_dict()["firstFailure"]) == frozen
    collector.close()


def test_flag_defaults_off_and_disabled_report_shape() -> None:
    parser = soak_driver._build_parser()
    assert getattr(parser.parse_args([]), "task_provenance", None) is False
    assert parser.parse_args(["--task-provenance"]).task_provenance
    driver = soak_driver.SoakDriver(
        soak_driver.SoakConfig(duration_s=45), "http://secret", "secret:1", None
    )
    assert "taskProvenance" not in driver._build_report()


async def test_driver_second_cycle_fail_and_partial_cleanup(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cls = _collector().__class__
    config = soak_driver.SoakConfig(duration_s=45, task_provenance=True)
    driver = soak_driver.SoakDriver(
        config, "http://secret", "secret:1", SimpleNamespace(fault_plan=None)
    )
    sampler = soak_driver.ProcessMetricsSampler(object(), "http://secret")
    driver._sampler = sampler
    values = iter([52] * 3 + [47] * 3 + [52] * 3 + [46] * 3)

    async def runtime() -> dict[str, Any]:
        return {
            "process": {"rssBytes": 100, "openFds": 10, "asyncioTasks": next(values)}
        }

    async def status() -> str:
        return "connected"

    async def noop(*args: Any, **kwargs: Any) -> bool:
        return True

    monkeypatch.setattr(sampler, "fetch_runtime", runtime)
    monkeypatch.setattr(sampler, "fetch_connection_status", status)
    monkeypatch.setattr(driver, "_post_radio_control", noop)
    monkeypatch.setattr(driver, "_wait_connection", noop)
    monkeypatch.setattr(driver, "_do_connect_until_recovered", noop)
    monkeypatch.setattr(soak_driver.asyncio, "sleep", noop)
    monkeypatch.setattr(driver, "_elapsed", lambda: 10.0)
    assert isinstance(driver._task_provenance, cls)
    await driver._reconnect_cycle()
    await driver._reconnect_cycle()
    report = driver._build_report()
    assert report["verdict"] == "FAIL"
    assert report["criteria"]["tasksStability"]["checks"][-1]["delta"] == -6
    retained = report["taskProvenance"]["firstFailure"]
    assert retained["reconnect"] == 2
    assert len(retained["probes"]) == 6
    assert [p["phase"] for p in retained["probes"]] == ["pre"] * 3 + ["post"] * 3

    async def cancel() -> None:
        raise asyncio.CancelledError

    monkeypatch.setattr(sampler, "fetch_runtime", cancel)
    with pytest.raises(asyncio.CancelledError):
        await driver._reconnect_cycle()
    assert driver._build_report()["taskProvenance"]["active"]["outcome"] == "cancelled"
    assert not driver._task_provenance._registry
    assert sampler._task_provenance is None


async def test_sampler_header_reaches_producer_and_context_cleans_on_cancel() -> None:
    collector = _collector()
    collector.begin_cycle(1)
    probe = collector.begin_probe("pre")

    class Web:
        _radio = None
        _reconnects_total = 0

        def _runtime_process_payload(self) -> dict[str, Any]:
            return soak_driver.WebServer._runtime_process_payload(self)

        async def _serve_runtime(self, writer: list[Any], headers: Any = None) -> None:
            writer.append(self._runtime_process_payload())

    web = Web()
    adapter = soak_driver._RuntimeTaskAdapter(
        SimpleNamespace(web=web, radio=SimpleNamespace()), collector
    )
    calls = []

    class Response:
        status = 200

        def __init__(self, headers: dict[str, str]) -> None:
            self.headers = headers
            self.body: list[Any] = []

        async def __aenter__(self) -> Any:
            await web._serve_runtime(self.body, self.headers)
            return self

        async def __aexit__(self, *args: Any) -> None:
            pass

        async def json(self) -> dict[str, Any]:
            return {"pid": os.getpid(), "process": self.body[0]}

    class Session:
        def get(self, url: str, **kwargs: Any) -> Response:
            calls.append(kwargs)
            return Response(kwargs.get("headers", {}))

    sampler = soak_driver.ProcessMetricsSampler(Session(), "http://secret.invalid")
    sampler._task_provenance = collector
    sampler._runtime_task_adapter = adapter
    adapter.install()
    try:
        sample = await sampler.sample(1.0, task_probe=probe)
        assert probe["snapshot"]["identityEligible"] is True
        assert probe["snapshot"]["measuredCount"] == sample.asyncio_tasks
        assert "headers" in calls[0]
        assert "headers" not in calls[1]
        assert not adapter._pending
        assert soak_driver._TASK_REQUEST.get() is None
        with pytest.raises(asyncio.CancelledError), adapter.request(probe):
            raise asyncio.CancelledError
        assert not adapter._pending
        assert soak_driver._TASK_REQUEST.get() is None
    finally:
        adapter.close()
        collector.close()


async def test_event_bound_and_late_callback_cannot_change_frozen_failure() -> None:
    collector = _collector()
    collector.begin_cycle(1)
    callback = None
    for _ in range(3):
        tasks = {asyncio.create_task(_pending()) for _ in range(100)}
        collector.capture(collector.begin_probe("pre"), "runtime", 100, tasks=tasks)
        callback = next(iter(collector._callbacks.values()))
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        del task, tasks
        await asyncio.sleep(0)
        gc.collect()
    assert len(collector.active["events"]) == 256
    assert collector.active["completionCoverage"] == "truncated"
    collector.finish_cycle(_entry(52, 46), "recovered")
    frozen = json.dumps(collector.to_dict()["firstFailure"])
    callback(asyncio.current_task())
    assert json.dumps(collector.to_dict()["firstFailure"]) == frozen
    assert not collector._registry


async def test_opt_in_numeric_and_legacy_mode_equivalence(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    reports = []
    for enabled in (False, True):
        driver = soak_driver.SoakDriver(
            soak_driver.SoakConfig(
                duration_s=45, task_provenance=enabled, probe_diagnostics=True
            ),
            "http://secret",
            "secret:1",
            SimpleNamespace(fault_plan=None),
        )
        sampler = soak_driver.ProcessMetricsSampler(object(), "http://secret")
        values = iter([52] * 3 + [46] * 3)

        async def runtime() -> dict[str, Any]:
            return {
                "process": {
                    "rssBytes": 100,
                    "openFds": 10,
                    "asyncioTasks": next(values),
                }
            }

        async def status() -> str:
            return "connected"

        async def noop(*args: Any, **kwargs: Any) -> bool:
            return True

        monkeypatch.setattr(sampler, "fetch_runtime", runtime)
        monkeypatch.setattr(sampler, "fetch_connection_status", status)
        monkeypatch.setattr(driver, "_post_radio_control", noop)
        monkeypatch.setattr(driver, "_wait_connection", noop)
        monkeypatch.setattr(driver, "_do_connect_until_recovered", noop)
        monkeypatch.setattr(soak_driver.asyncio, "sleep", noop)
        monkeypatch.setattr(soak_driver.time, "monotonic", lambda: 10.0)
        monkeypatch.setattr(driver, "_elapsed", lambda: 10.0)
        driver._sampler = sampler
        await driver._reconnect_cycle()
        report = driver._build_report()
        report.pop("startedAt")
        report.pop("taskProvenance", None)
        reports.append(report)
    assert reports[0] == reports[1]
    assert reports[0]["verdict"] == "FAIL"
    assert reports[0]["probeDiagnostics"]["selectedReconnect"] == 1
    assert len(reports[0]["probeDiagnostics"]["probes"]) == 6


async def test_token_replay_ambiguity_and_partial_install_restore() -> None:
    collector = _collector()
    collector.begin_cycle(1)

    class Web:
        twice = False

        def _runtime_process_payload(self) -> dict[str, Any]:
            return {"asyncioTasks": len(asyncio.all_tasks())}

        async def _serve_runtime(self, writer: Any, headers: Any = None) -> None:
            self._runtime_process_payload()
            if self.twice:
                self._runtime_process_payload()

    web = Web()
    adapter = soak_driver._RuntimeTaskAdapter(
        SimpleNamespace(web=web, radio=SimpleNamespace()), collector
    )
    adapter.install()
    try:
        probe = collector.begin_probe("pre")
        with adapter.request(probe) as headers:
            await web._serve_runtime(None, headers)
            frozen = json.dumps(probe)
            await web._serve_runtime(None, headers)
            assert json.dumps(probe) == frozen
        web.twice = True
        probe = collector.begin_probe("pre")
        with adapter.request(probe) as headers:
            await web._serve_runtime(None, headers)
        assert probe["snapshot"]["coverage"] == "correlation_ambiguous"
        assert probe["snapshot"]["countAgreement"] is None
        assert not probe["snapshot"]["identityEligible"]
    finally:
        adapter.close()
        collector.close()

    class Broken(Web):
        def __setattr__(self, name: str, value: Any) -> None:
            if name == "_runtime_process_payload":
                raise RuntimeError("secret")
            super().__setattr__(name, value)

    broken = Broken()
    adapter = soak_driver._RuntimeTaskAdapter(SimpleNamespace(web=broken), collector)
    adapter.install()
    adapter.close()
    assert not vars(broken)
