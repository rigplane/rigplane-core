"""GET-only observe mode of the soak driver (``scripts/soak/observe.py``).

The observer watches an already running ``rigplane web`` through two read
endpoints. These tests run it against a scripted fake HTTP server on
127.0.0.1 with the cadences compressed, plus pure-function tests on
synthetic samples where the arithmetic needs exact timestamps. They pin:

* the observer only sends ``GET`` to ``/api/v1/runtime`` and ``/readyz``;
* the RSS slope, the FD / task drift checks and the drop-episode budget,
  including the budget boundary and an episode still open at the end;
* an unreachable server counts as down, and a server that answers without
  saying anything about the link is not read as up;
* a field the server reports as null is recorded as null and its criterion
  is NOT_EVALUATED, never PASS;
* SIGINT and SIGTERM still produce the report through the real entry point.
"""

from __future__ import annotations

import asyncio
import json
import signal
import socket
import sys
from collections import Counter
from pathlib import Path
from typing import Any, Callable

import aiohttp
import pytest
from aiohttp import web
from aiohttp.test_utils import TestServer

from scripts.soak import observe, soak_driver

# asyncio_mode=auto (pyproject.toml) drives the async tests; no marker.

MIB = 1024 * 1024
REPO_ROOT = Path(__file__).resolve().parents[1]
DROP = object()  # the fake closes the connection without answering
READY = observe.Reply(200, {"status": "ready", "radioReady": True})
NOT_READY = observe.Reply(503, {"status": "not_ready", "radioReady": False})
GONE = observe.Reply(None, None)


def _runtime_doc(
    rss: int | None = 100 * MIB,
    fds: int | None = 20,
    tasks: int | None = 25,
    reconnects: int | None = 0,
    link: str | None = "connected",
) -> dict[str, Any]:
    process = {"rssBytes": rss, "openFds": fds, "asyncioTasks": tasks}
    process["reconnectsTotal"] = reconnects
    return {"pid": 4242, "station": {"health": {"radioLink": link}}, "process": process}


def _up(t_s: float, **doc: Any) -> observe.ObserveSample:
    return observe.parse_sample(t_s, observe.Reply(200, _runtime_doc(**doc)), READY)


def _down(t_s: float, **doc: Any) -> observe.ObserveSample:
    reply = observe.Reply(200, _runtime_doc(link="reconnecting", **doc))
    return observe.parse_sample(t_s, reply, NOT_READY)


def _series(count: int, **per_sample: Callable[[int], Any]) -> list[Any]:
    """``count`` up samples 30 s apart; ``per_sample`` maps a field to f(i)."""
    return [
        _up(i * 30.0, **{name: fn(i) for name, fn in per_sample.items()})
        for i in range(count)
    ]


def _report(
    samples: list[observe.ObserveSample], duration_s: float, **cfg: Any
) -> dict[str, Any]:
    values: dict[str, Any] = dict(
        base_url="http://127.0.0.1:1",
        duration_s=duration_s,
        interval_s=30.0,
        recovery_budget_s=180.0,
        warmup_s=60.0,
    )
    config = observe.ObserveConfig(**{**values, **cfg})
    return observe.build_report(config, samples, duration_s, "t", None)


# --- parsing: what counts as a down sample ----------------------------------


@pytest.mark.parametrize(
    ("runtime", "ready", "reasons"),
    [
        (GONE, GONE, ("unreachable",)),
        (observe.Reply(200, _runtime_doc()), NOT_READY, ("not_ready",)),
        (
            observe.Reply(200, _runtime_doc(link="reconnecting")),
            READY,
            ("radioLink=reconnecting",),
        ),
        (
            observe.Reply(200, _runtime_doc(link="disconnected")),
            NOT_READY,
            ("not_ready", "radioLink=disconnected"),
        ),
        # The server answered but neither endpoint says anything about the
        # link (wrong URL, an error page): not evidence of a live link.
        (observe.Reply(404, None), observe.Reply(404, None), ("no_link_evidence",)),
        # Up: either endpoint alone, with the other one failing.
        (observe.Reply(200, _runtime_doc()), GONE, ()),
        (GONE, READY, ()),
    ],
)
def test_parse_sample_down_reasons(
    runtime: observe.Reply, ready: observe.Reply, reasons: tuple[str, ...]
) -> None:
    sample = observe.parse_sample(1.0, runtime, ready)
    assert sample.down_reasons == reasons
    assert sample.down is bool(reasons)


# --- drop episodes and the recovery budget ----------------------------------


def test_episodes_run_from_first_down_to_first_up_sample() -> None:
    # up@0, down@30..90, up@120 (90 s), up@150, down@180..390, up@420 (240 s).
    samples = [_up(0.0), _down(30.0), _down(60.0), _down(90.0), _up(120.0), _up(150.0)]
    samples += [_down(180.0 + 30.0 * i) for i in range(8)] + [_up(420.0)]
    episodes = observe.find_episodes(samples, 450.0, 180.0)
    assert [e["startedAtS"] for e in episodes] == [30.0, 180.0]
    assert [e["endedAtS"] for e in episodes] == [120.0, 420.0]
    assert [e["recoveryS"] for e in episodes] == [90.0, 240.0]
    assert [e["exceedsBudget"] for e in episodes] == [False, True]
    assert episodes[0]["reasons"] == ["not_ready", "radioLink=reconnecting"]
    recovery = _report(samples + [_up(450.0)], 450.0)["criteria"]["recovery"]
    assert (recovery["status"], recovery["forced"], recovery["unrecovered"]) == (
        "FAIL",
        2,
        1,
    )
    # Exactly the budget is not over it; one more sampling step is.
    assert not observe.find_episodes([_down(30.0), _up(210.0)], 240.0, 180.0)[0][
        "exceedsBudget"
    ]
    assert observe.find_episodes([_down(30.0), _up(240.0)], 240.0, 180.0)[0][
        "exceedsBudget"
    ]
    # An episode still open at the end counts with the time so far.
    open_samples = [_up(0.0), _down(30.0)]
    (still_open,) = observe.find_episodes(open_samples, 300.0, 180.0)
    assert (still_open["recovered"], still_open["recoveryS"]) == (False, 270.0)
    assert still_open["exceedsBudget"] is True


def test_nothing_sampled_evaluates_nothing_and_fails() -> None:
    report = _report([], 100.0)
    assert report["criteria"]["recovery"]["status"] == "NOT_EVALUATED"
    assert report["criteriaEvaluated"] == []
    assert report["verdict"] == "FAIL"
    assert "no criterion" in report["error"]


# --- RSS slope, FD / task drift ---------------------------------------------


@pytest.mark.parametrize(("mib_per_hour", "status"), [(100, "FAIL"), (20, "PASS")])
def test_rss_slope_against_the_50_mib_per_hour_limit(
    mib_per_hour: int, status: str
) -> None:
    samples = _series(241, rss=lambda i: 100 * MIB + int(i / 120 * mib_per_hour * MIB))
    assert _report(samples, 7200.0)["criteria"]["rssGrowth"]["status"] == status


@pytest.mark.parametrize(("last_fds", "status"), [(32, "FAIL"), (24, "PASS")])
def test_first_versus_last_drift_against_the_tolerance(
    last_fds: int, status: str
) -> None:
    samples = _series(241, fds=lambda i: 20 if i < 120 else last_fds)
    criteria = _report(samples, 7200.0)["criteria"]
    check = criteria["fdsStability"]["checks"][-1]
    assert (check["kind"], check["pre"], check["post"]) == ("firstVsLast", 20, last_fds)
    assert criteria["fdsStability"]["status"] == status
    assert criteria["tasksStability"]["status"] == "PASS"


def test_drift_ignores_samples_while_the_link_was_down() -> None:
    samples = _series(241)
    samples[240] = _down(7200.0, tasks=90)  # last sample: down, another count
    tasks = _report(samples, 7200.0)["criteria"]["tasksStability"]
    assert (tasks["status"], tasks["checks"][-1]["post"]) == ("PASS", 25)


@pytest.mark.parametrize(("fds_after", "status"), [(33, "FAIL"), (23, "PASS")])
def test_natural_reconnect_compares_around_the_increment(
    fds_after: int, status: str
) -> None:
    samples = _series(20)
    # reconnectsTotal 0 -> 1 first seen at sample 10, link down; up from 11 on.
    samples[10] = _down(300.0, reconnects=1, fds=2)
    for i in range(11, 20):
        samples[i] = _up(i * 30.0, reconnects=1, fds=fds_after)
    report = _report(samples, 600.0, warmup_s=30.0)
    (entry,) = report["reconnects"]
    assert (entry["fdsPre"], entry["fdsPost"]) == (20, fds_after)
    fds = report["criteria"]["fdsStability"]
    assert fds["checks"][0]["kind"] == "naturalReconnect"
    assert (fds["checks"][0]["delta"], fds["status"]) == (fds_after - 20, status)
    assert report["criteria"]["tasksStability"]["checks"][0]["ok"] is True


def test_report_has_the_driver_keys_plus_the_observe_additions() -> None:
    report = _report(_series(5), 120.0)
    assert set(report) == {
        "schemaVersion",
        "mode",
        "verdict",
        "startedAt",
        "durationConfiguredS",
        "durationActualS",
        "error",
        "counts",
        "criteria",
        "criteriaEvaluated",
        "reconnects",
        "episodes",
        "samples",
    }


# --- the GET-only client and the fake server --------------------------------


class _FakeServer:
    """A scripted HTTP server on 127.0.0.1 that records every request."""

    def __init__(
        self, runtime: Callable[[int], Any], ready: Callable[[int], Any] | None = None
    ) -> None:
        ok = (200, {"status": "ready", "radioReady": True})
        self._scripts: dict[str, Callable[[int], Any]] = {
            "/api/v1/runtime": runtime,
            "/readyz": ready or (lambda n: ok),
        }
        self.requests: list[tuple[str, str]] = []
        self.counts: Counter[str] = Counter()
        self.url = ""
        self._server: TestServer | None = None

    async def _handle(self, request: web.Request) -> web.StreamResponse:
        self.requests.append((request.method, request.path))
        script = self._scripts.get(request.path)
        if request.method != "GET" or script is None:
            return web.Response(status=405 if script else 404)
        n = self.counts[request.path]
        self.counts[request.path] += 1
        value = script(n)
        if value is DROP:
            assert request.transport is not None
            request.transport.close()
            return web.Response(status=500)
        status, body = value if isinstance(value, tuple) else (200, value)
        return web.json_response(body, status=status)

    async def __aenter__(self) -> _FakeServer:
        app = web.Application()
        app.router.add_route("*", "/{tail:.*}", self._handle)
        self._server = TestServer(app)  # binds 127.0.0.1
        await self._server.start_server()
        self.url = str(self._server.make_url("")).rstrip("/")
        return self

    async def __aexit__(self, *exc_info: object) -> None:
        assert self._server is not None
        await self._server.close()


async def test_client_refuses_a_path_outside_the_read_allowlist() -> None:
    paths = ("/api/v1/commands", "/api/v1/radio/connect", "/api/v1/ws", "/")
    async with _FakeServer(lambda n: _runtime_doc()) as server:
        async with aiohttp.ClientSession() as session:
            client = observe.GetOnlyClient(session, server.url)
            for path in paths:
                with pytest.raises(ValueError):
                    await client.get_json(path)
    assert server.requests == []


# --- end to end against the fake server (cadences compressed) ---------------


def _cfg(url: str, tmp_path: Path, duration_s: float) -> observe.ObserveConfig:
    return observe.ObserveConfig(
        base_url=url,
        duration_s=duration_s,
        interval_s=0.05,
        recovery_budget_s=0.3,
        warmup_s=0.2,
        report_path=str(tmp_path / "report.json"),
    )


@pytest.fixture
def short_rss_fit(monkeypatch: pytest.MonkeyPatch) -> None:
    """Let the compressed runs reach the RSS fit (the floor is 600 s)."""
    monkeypatch.setattr(soak_driver, "RSS_MIN_FIT_SPAN_S", 0.5)


def _not_ready_for(first: int, last: int) -> Callable[[int], Any]:
    def ready(n: int) -> Any:
        if first <= n < last:
            return (503, {"status": "not_ready", "radioReady": False})
        return (200, {"status": "ready", "radioReady": True})

    return ready


@pytest.mark.usefixtures("short_rss_fit")
async def test_flat_series_passes_and_only_reads(tmp_path: Path) -> None:
    async with _FakeServer(lambda n: _runtime_doc()) as server:
        report = await observe.run_observe(_cfg(server.url, tmp_path, 2.0))
    assert report["verdict"] == "PASS", report["criteria"]
    assert {c["status"] for c in report["criteria"].values()} == {"PASS"}
    assert report["episodes"] == []
    on_disk = json.loads((tmp_path / "report.json").read_text(encoding="utf-8"))
    assert on_disk["verdict"] == "PASS"
    # GET-only pin: every request the fake saw was a GET to one of the two
    # read endpoints, and both were polled.
    assert {method for method, _ in server.requests} == {"GET"}
    assert {path for _, path in server.requests} == {"/api/v1/runtime", "/readyz"}


@pytest.mark.usefixtures("short_rss_fit")
async def test_rss_ramp_fails_the_run(tmp_path: Path) -> None:
    async with _FakeServer(lambda n: _runtime_doc(rss=100 * MIB + n * 5 * MIB)) as s:
        report = await observe.run_observe(_cfg(s.url, tmp_path, 2.0))
    assert report["criteria"]["rssGrowth"]["status"] == "FAIL"
    assert report["criteria"]["recovery"]["status"] == "PASS"
    assert report["verdict"] == "FAIL"


@pytest.mark.usefixtures("short_rss_fit")
@pytest.mark.parametrize(
    ("down_samples", "exceeds", "verdict"), [(12, True, "FAIL"), (2, False, "PASS")]
)
async def test_drop_against_the_recovery_budget(
    tmp_path: Path, down_samples: int, exceeds: bool, verdict: str
) -> None:
    # 12 not-ready samples last ~0.6 s and 2 last ~0.1 s; the budget is 0.3 s.
    ready = _not_ready_for(6, 6 + down_samples)
    async with _FakeServer(lambda n: _runtime_doc(), ready) as server:
        report = await observe.run_observe(_cfg(server.url, tmp_path, 2.0))
    (episode,) = report["episodes"]  # recorded whether or not over budget
    assert (episode["recovered"], episode["exceedsBudget"]) == (True, exceeds)
    assert episode["reasons"] == ["not_ready"]
    assert report["verdict"] == verdict, report["criteria"]


@pytest.mark.usefixtures("short_rss_fit")
async def test_a_server_that_stops_answering_counts_as_down(tmp_path: Path) -> None:
    def runtime(n: int) -> Any:
        return DROP if 6 <= n < 8 else _runtime_doc()

    def ready(n: int) -> Any:
        return DROP if 6 <= n < 8 else (200, {"status": "ready", "radioReady": True})

    async with _FakeServer(runtime, ready) as server:
        report = await observe.run_observe(_cfg(server.url, tmp_path, 2.0))
    (episode,) = report["episodes"]
    assert (episode["reasons"], episode["recovered"]) == (["unreachable"], True)
    down = [s for s in report["samples"] if s["down"]]
    assert down and all(s["reachable"] is False for s in down)
    assert all(s["rssBytes"] is None for s in down)


async def test_server_that_never_answers_is_one_open_episode_and_fails(
    tmp_path: Path,
) -> None:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        url = f"http://127.0.0.1:{probe.getsockname()[1]}"
    report = await observe.run_observe(_cfg(url, tmp_path, 1.0))
    (episode,) = report["episodes"]
    assert (episode["recovered"], episode["exceedsBudget"]) == (False, True)
    assert episode["reasons"] == ["unreachable"]
    assert report["criteria"]["recovery"]["status"] == "FAIL"
    assert report["verdict"] == "FAIL"


@pytest.mark.usefixtures("short_rss_fit")
async def test_null_process_fields_are_recorded_null_and_not_evaluated(
    tmp_path: Path,
) -> None:
    doc = _runtime_doc(rss=None, fds=None, tasks=None, reconnects=None)
    async with _FakeServer(lambda n: doc) as server:
        report = await observe.run_observe(_cfg(server.url, tmp_path, 1.5))
    for field in ("rssBytes", "fds", "asyncioTasks", "reconnectsTotal"):
        assert all(s[field] is None for s in report["samples"])
    statuses = {name: c["status"] for name, c in report["criteria"].items()}
    assert statuses == {
        "rssGrowth": "NOT_EVALUATED",
        "fdsStability": "NOT_EVALUATED",
        "tasksStability": "NOT_EVALUATED",
        "recovery": "PASS",
    }
    assert report["criteriaEvaluated"] == ["recovery"]
    assert report["error"] is None
    assert report["verdict"] == soak_driver.VERDICT_INCOMPLETE
    verdict = soak_driver._verdict(report["criteria"], report["error"])
    assert verdict == report["verdict"]


@pytest.mark.parametrize("sig", [signal.SIGTERM, signal.SIGINT])
async def test_report_is_written_when_the_run_is_signalled(
    tmp_path: Path, sig: signal.Signals
) -> None:
    report_path = tmp_path / "report.json"
    async with _FakeServer(lambda n: _runtime_doc()) as server:
        proc = await asyncio.create_subprocess_exec(
            sys.executable,
            "-m",
            "scripts.soak.soak_driver",
            *("--observe-url", server.url, "--duration", "120"),
            *("--interval-s", "0.1"),
            *("--report", str(report_path)),
            cwd=REPO_ROOT,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            async with asyncio.timeout(60.0):
                while server.counts["/api/v1/runtime"] < 4:
                    assert proc.returncode is None, "observer exited early"
                    await asyncio.sleep(0.05)
            proc.send_signal(sig)
            async with asyncio.timeout(30.0):
                stdout, _ = await proc.communicate()
        finally:
            if proc.returncode is None:
                proc.kill()
                await proc.wait()
    assert proc.returncode == 1
    report = json.loads(report_path.read_text(encoding="utf-8"))
    assert report["verdict"] == "FAIL"
    assert sig.name in report["error"]
    assert report["counts"]["samples"] >= 3
    assert report["durationActualS"] < 120
    assert b"report written" in stdout


# --- command line -----------------------------------------------------------


@pytest.mark.parametrize(
    "argv",
    [
        ["--observe-url", "https://127.0.0.1:8098"],
        ["--observe-url", "http://127.0.0.1:8098/api"],
        ["--observe-url", "http://127.0.0.1:8098", "--interval-s", "0"],
        ["--observe-url", "http://127.0.0.1:8098", "--report", "/no/such/dir/r.json"],
    ],
)
def test_cli_rejects_unusable_observe_arguments(argv: list[str]) -> None:
    with pytest.raises(SystemExit) as exc:
        soak_driver.main(argv)
    assert exc.value.code == 2


def test_cli_exits_1_when_an_observe_run_left_a_criterion_not_evaluated(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    async def _fake_run(cfg: observe.ObserveConfig) -> dict[str, Any]:
        return observe.build_report(cfg, _series(3), 60.0, "t", None)

    monkeypatch.setattr(observe, "run_observe", _fake_run)
    argv = ["--observe-url", "http://127.0.0.1:8098", "--report", str(tmp_path / "r")]
    # 60 s of samples is under the RSS fit floor: INCOMPLETE, not PASS.
    assert soak_driver.main(argv) == 1


def test_cli_dispatches_observe_mode_with_the_documented_defaults(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # Lower the RSS fit floor so the 60 s series evaluates all four criteria.
    monkeypatch.setattr(soak_driver, "RSS_MIN_FIT_SPAN_S", 0.5)
    seen: list[observe.ObserveConfig] = []

    async def _fake_run(cfg: observe.ObserveConfig) -> dict[str, Any]:
        seen.append(cfg)
        return observe.build_report(cfg, _series(3), 60.0, "t", None)

    monkeypatch.setattr(observe, "run_observe", _fake_run)
    argv = ["--observe-url", "http://127.0.0.1:8098/", "--duration", "28800"]
    code = soak_driver.main([*argv, "--report", str(tmp_path / "r.json")])
    (cfg,) = seen
    assert cfg.base_url == "http://127.0.0.1:8098"
    assert cfg.duration_s == 28800.0
    assert cfg.interval_s == soak_driver.METRICS_INTERVAL_S
    assert cfg.warmup_s == soak_driver.RSS_WARMUP_S
    assert cfg.recovery_budget_s == 180.0
    assert code == 0
