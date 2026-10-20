from __future__ import annotations

import asyncio
from typing import Any

import pytest

from rigplane.web.server import WebServer
from scripts.soak import soak_driver


async def test_sampler_missing_runtime_tasks_changes_population_without_task_loss(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    server = WebServer(None)
    sampler = soak_driver.ProcessMetricsSampler(object(), "http://127.0.0.1:1")
    runtime_tasks_present = True
    release = asyncio.Event()
    entered = [asyncio.Event() for _ in range(8)]
    owned: list[asyncio.Task[None]] = []

    async def wait_at_gate(ready: asyncio.Event) -> None:
        ready.set()
        await release.wait()

    async def runtime() -> dict[str, Any]:
        process = server._runtime_process_payload()
        process.update(rssBytes=1024, openFds=20)
        if not runtime_tasks_present:
            del process["asyncioTasks"]
        return {"process": process}

    async def status() -> str:
        return "connected"

    async def unexpected_process_fallback(pid: int) -> tuple[int, int]:
        pytest.fail(f"Unexpected RSS/FD fallback for PID {pid}")

    monkeypatch.setattr(sampler, "fetch_runtime", runtime)
    monkeypatch.setattr(sampler, "fetch_connection_status", status)
    monkeypatch.setattr(
        soak_driver, "_driver_side_process_metrics", unexpected_process_fallback
    )

    try:
        for index, ready in enumerate(entered):
            name = f"product-{index}" if index < 2 else f"soak-population-{index}"
            owned.append(asyncio.create_task(wait_at_gate(ready), name=name))
        for ready in entered:
            await ready.wait()

        population = asyncio.all_tasks()
        product_tasks = set(owned[:2])
        soak_tasks = set(owned[2:])
        incidental_tasks = population - set(owned)
        incidental_soak_tasks = {
            task for task in incidental_tasks if task.get_name().startswith("soak-")
        }
        assert product_tasks | soak_tasks <= population
        assert all(not task.done() for task in owned)
        assert not release.is_set()

        reported = await sampler.sample(1.0)
        runtime_tasks_present = False
        fallback = await sampler.sample(2.0)

        assert asyncio.all_tasks() == population
        assert all(not task.done() for task in owned)
        assert not release.is_set()
        assert reported.asyncio_tasks == len(population)
        assert fallback.asyncio_tasks == (
            len(incidental_tasks - incidental_soak_tasks) + len(product_tasks)
        )
        assert reported.asyncio_tasks - fallback.asyncio_tasks == (
            len(soak_tasks) + len(incidental_soak_tasks)
        )
        assert reported.rss_bytes == fallback.rss_bytes == 1024
        assert reported.fds == fallback.fds == 20
        assert reported.connection == fallback.connection == "connected"
    finally:
        for task in owned:
            task.cancel()
        results = await asyncio.gather(*owned, return_exceptions=True)
        for result in results:
            if isinstance(result, BaseException) and not isinstance(
                result, asyncio.CancelledError
            ):
                raise result
            assert isinstance(result, asyncio.CancelledError)
        assert all(task.done() for task in owned)


def test_negative_six_reconnect_task_delta_fails_report() -> None:
    config = soak_driver.SoakConfig(duration_s=3600.0, rss_warmup_s=0.0)
    samples = [
        soak_driver.ProcessSample(
            t_s=t_s,
            rss_bytes=1024,
            fds=20,
            asyncio_tasks=52,
            connection="connected",
        )
        for t_s in (0.0, 3600.0)
    ]
    reconnect = {
        "startedAtS": 300.0,
        "recovered": True,
        "windowStartS": 300.0,
        "windowEndS": 320.0,
        "fdsPre": 20,
        "fdsPost": 20,
        "tasksPre": 52,
        "tasksPost": 46,
    }

    criteria = soak_driver.evaluate_report(config, samples, [reconnect])

    assert criteria["rssGrowth"]["status"] == "PASS"
    assert criteria["fdsStability"]["status"] == "PASS"
    assert criteria["recovery"]["status"] == "PASS"
    assert criteria["tasksStability"] == {
        "status": "FAIL",
        "tolerance": 5,
        "checks": [
            {
                "reconnectStartedAtS": 300.0,
                "pre": 52,
                "post": 46,
                "delta": -6,
                "ok": False,
            }
        ],
    }
    assert soak_driver._verdict(criteria, error=None) == "FAIL"
