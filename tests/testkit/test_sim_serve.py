"""Tests for the standalone simulator launcher (`python -m rigplane_testkit.sim serve`).

In-process tests build the same objects ``serve.py`` builds (via
``build_stack``) and drive the control endpoint through the aiohttp test
client; subprocess tests run the real entry point against a production
``IcomRadio``.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import signal
import sys
from contextlib import asynccontextmanager
from pathlib import Path
from types import SimpleNamespace

import pytest
from aiohttp import ClientSession
from aiohttp.test_utils import TestClient, TestServer

from rigplane.radio import IcomRadio

from rigplane_testkit.sim.__main__ import _control_arg, build_parser
from rigplane_testkit.sim.serve import build_stack, format_control_host

from _perf_helpers import fast_connect

_REPO_ROOT = Path(__file__).resolve().parents[2]

_STARTUP_KEYS = (
    "sim radio fidelity evidence transport host control_port username control seed"
).split()

# Obvious test-only credentials for the subprocess run: the password is a
# value nothing but --password could put on the wire, so any appearance of
# it in process output is a leak.
_TEST_USERNAME = "sim-e2e-user"
_TEST_PASSWORD = "sim-e2e-pass-2f8d41"

# JSON literals a permissive parser (aiohttp's request.json()) accepts but
# that must never reach a radio timer as a duration; true and "5" pin the
# bool/str rejection next to them.
_BAD_NUMERIC_LITERALS = ("NaN", "Infinity", "-Infinity", "true", '"5"')


def _args(*extra: str) -> argparse.Namespace:
    return build_parser().parse_args(["serve", "--radio", "ic7610", *extra])


def _strict_loads(text: str) -> dict:
    def _reject(literal: str) -> None:
        raise AssertionError(f"non-finite JSON literal in body: {literal}")

    return json.loads(text, parse_constant=_reject)


@asynccontextmanager
async def _stack(*extra: str):
    stack = build_stack(_args(*extra))
    await stack.radio.start()
    client = TestClient(TestServer(stack.app))
    await client.start_server()
    try:
        yield SimpleNamespace(inner=stack, client=client)
    finally:
        await client.close()
        await stack.radio.stop()
        stack.trace.close()


# --- Control endpoint semantics (in-process) -------------------------------


async def test_event_error_responses() -> None:
    async with _stack() as s:
        resp = await s.client.post("/event", json={"event": "nope"})
        assert resp.status == 400
        assert await resp.json() == {
            "ok": False,
            "error": "unsupported",
            "event": "nope",
        }

        resp = await s.client.post("/event", json={"event": "set_frequency", "hz": "x"})
        assert resp.status == 400
        assert (await resp.json())["error"] == "invalid_args"

        resp = await s.client.post(
            "/event", data=b"not json", headers={"Content-Type": "application/json"}
        )
        assert resp.status == 400
        assert (await resp.json())["error"] == "invalid_args"


async def test_non_finite_or_mistyped_numbers_rejected() -> None:
    # Raw text bodies: NaN/Infinity parse permissively, so the guard lives
    # in the handler, not in JSON decoding.
    with pytest.raises(AssertionError):
        _strict_loads('{"x": NaN}')  # the parser itself must be strict
    async with _stack() as s:
        cases = (("power_cycle", "down_for_s"), ("keepalive_silence", "for_s"))
        for event, arg in cases:
            for literal in _BAD_NUMERIC_LITERALS:
                resp = await s.client.post(
                    "/event",
                    data=f'{{"event":"{event}","{arg}":{literal}}}',
                    headers={"Content-Type": "application/json"},
                )
                assert resp.status == 400, literal
                body = _strict_loads(await resp.text())
                assert body["error"] == "invalid_args", literal


async def test_set_frequency_changes_state() -> None:
    async with _stack() as s:
        state = await (await s.client.get("/state")).json()
        assert state["frequency_hz"] == 14_074_000
        assert state["ptt"] is False

        resp = await s.client.post(
            "/event", json={"event": "set_frequency", "hz": 7_100_000}
        )
        assert resp.status == 200
        assert (await resp.json())["ok"] is True

        state = await (await s.client.get("/state")).json()
        assert state["frequency_hz"] == 7_100_000
        assert set(state) == {
            "frequency_hz",
            "mode",
            "ptt",
            "session_held",
            "login_count",
        }


async def test_second_stop_is_harmless() -> None:
    async with _stack() as s:
        for _ in range(2):
            resp = await s.client.post("/stop")
            assert resp.status == 200
            assert await resp.json() == {"ok": True}


def test_control_host_forms() -> None:
    assert format_control_host("127.0.0.1") == "127.0.0.1"
    assert format_control_host("localhost") == "localhost"
    assert format_control_host("::1") == "[::1]"
    assert format_control_host("fe80::1") == "[fe80::1]"
    with pytest.raises(argparse.ArgumentTypeError, match=r"\[host\]:port"):
        _control_arg("::1")


# --- Trace ------------------------------------------------------------------


async def test_trace_records_and_since_filter() -> None:
    async with _stack() as s:
        await s.client.post("/event", json={"event": "set_frequency", "hz": 7_000_000})
        await s.client.post("/event", json={"event": "nope"})

        body = await (await s.client.get("/trace")).json()
        assert [(rec["kind"], rec["name"]) for rec in body["events"]] == [
            ("start", "serve"),
            ("event", "set_frequency"),
            ("event", "nope"),
        ]
        assert body["next"] == 3
        assert all(rec["n"] == i for i, rec in enumerate(body["events"]))

        tail = await (await s.client.get("/trace?since=2")).json()
        assert [rec["n"] for rec in tail["events"]] == [2]
        assert tail["events"][0]["result"] == {
            "ok": False,
            "error": "unsupported",
            "event": "nope",
        }

        for raw in ("-3", "abc"):  # negative and non-numeric read as 0
            body = await (await s.client.get(f"/trace?since={raw}")).json()
            assert [rec["n"] for rec in body["events"]] == [0, 1, 2], raw
        body = await (await s.client.get("/trace?since=99")).json()
        assert body["events"] == []
        assert body["next"] == 3


# --- Trace determinism --------------------------------------------------------

# All four supported events plus two rejected posts.
_DETERMINISM_EVENTS = [
    {"event": "set_frequency", "hz": 7_074_000},
    {"event": "set_frequency", "hz": 0},
    {"event": "nope"},
    {"event": "session_drop", "notice": False},
    {"event": "power_cycle", "down_for_s": 0.2},
    {"event": "keepalive_silence", "for_s": 0.1},
]


async def _stripped_trace(seed: int) -> list[str]:
    async with _stack("--seed", str(seed)) as s:
        for event in _DETERMINISM_EVENTS:
            await s.client.post("/event", json=event)
        body = await (await s.client.get("/trace")).json()
    return [
        json.dumps(
            {key: value for key, value in rec.items() if key != "t"},
            sort_keys=True,
            separators=(",", ":"),
        )
        for rec in body["events"]
    ]


async def test_same_seed_and_events_produce_identical_trace() -> None:
    first = await _stripped_trace(1)
    second = await _stripped_trace(1)
    assert first == second
    assert len(first) == 1 + len(_DETERMINISM_EVENTS)


# --- Subprocess end to end ----------------------------------------------------


async def _start_sim(*extra: str) -> tuple[asyncio.subprocess.Process, dict]:
    proc = await asyncio.create_subprocess_exec(
        sys.executable,
        "-m",
        "rigplane_testkit.sim",
        "serve",
        "--radio",
        "ic7610",
        *extra,
        cwd=_REPO_ROOT,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    line = json.loads(await asyncio.wait_for(proc.stdout.readline(), 20))
    return proc, line


async def _kill_if_alive(proc: asyncio.subprocess.Process) -> None:
    if proc.returncode is None:
        proc.kill()
        await proc.wait()


async def test_subprocess_end_to_end(tmp_path: Path) -> None:
    trace_path = tmp_path / "trace.jsonl"
    extra = ["--seed", "1", "--trace", str(trace_path)]
    extra += ["--username", _TEST_USERNAME, "--password", _TEST_PASSWORD]
    proc, line = await _start_sim(*extra)
    try:
        assert list(line) == _STARTUP_KEYS
        assert line["sim"] == "rigplane-sim/1" and line["radio"] == "IC-7610"
        assert line["fidelity"] == "A" and line["evidence"] == "simulated"
        assert line["transport"] == "lan" and line["host"] == "127.0.0.1"
        assert line["seed"] == 1 and line["username"] == _TEST_USERNAME
        assert isinstance(line["control_port"], int) and line["control_port"] > 0
        # "control" is the HTTP endpoint; "control_port" the radio LAN port.
        http_port = int(line["control"].rsplit(":", 1)[1])
        assert line["control"] == f"http://127.0.0.1:{http_port}"

        radio = IcomRadio(
            host=line["host"],
            port=line["control_port"],
            username=_TEST_USERNAME,
            password=_TEST_PASSWORD,
            timeout=5.0,
            model="IC-7610",
        )
        with fast_connect():
            await radio.connect()
        assert await radio.get_freq() == 14_074_000

        # The password is never printed, logged or returned: scan the
        # startup line and both readable control bodies, then drive the
        # endpoint, while the subprocess is still running.
        async with ClientSession() as http:
            state_body = await (await http.get(f"{line['control']}/state")).text()
            trace_body = await (await http.get(f"{line['control']}/trace")).text()
            async with http.post(
                f"{line['control']}/event",
                json={"event": "set_frequency", "hz": 7_100_000},
            ) as resp:
                assert resp.status == 200
        assert _TEST_PASSWORD not in json.dumps(line)
        assert _TEST_PASSWORD not in state_body
        assert _TEST_PASSWORD not in trace_body
        assert await radio.get_freq() == 7_100_000
        await radio.disconnect()

        async with ClientSession() as http:
            async with http.post(f"{line['control']}/stop") as resp:
                assert resp.status == 200
                assert await resp.json() == {"ok": True}

        assert await asyncio.wait_for(proc.wait(), 10) == 0
        # Nothing else was ever printed on stdout; no password anywhere.
        assert await proc.stdout.read() == b""
        stderr = await proc.stderr.read()
        # U+FFFD replacements of non-UTF-8 bytes cannot spell the ASCII
        # password, so decoding does not weaken this scan.
        assert _TEST_PASSWORD not in stderr.decode(errors="replace")
    finally:
        await _kill_if_alive(proc)

    # --trace FILE: one valid JSON line per record, final stop record included.
    records = [_strict_loads(x) for x in trace_path.read_text().splitlines()]
    assert all(record["n"] == i for i, record in enumerate(records))
    assert records[0]["kind"] == "start"
    assert records[-1]["kind"] == "stop"
    assert records[-1]["name"] == "shutdown"
    assert records[-1]["result"] == {"ok": True}


async def test_sigterm_exits_0_and_writes_stop_record(tmp_path: Path) -> None:
    if sys.platform == "win32":
        pytest.skip("POSIX signal delivery")
    trace_path = tmp_path / "trace.jsonl"
    proc, _line = await _start_sim("--trace", str(trace_path))
    proc.send_signal(signal.SIGTERM)
    try:
        assert await asyncio.wait_for(proc.wait(), 10) == 0
    finally:
        await _kill_if_alive(proc)
    records = [_strict_loads(x) for x in trace_path.read_text().splitlines()]
    assert records[-1]["kind"] == "stop"
    assert records[-1]["result"] == {"ok": True}
