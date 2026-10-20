"""Tests for the standalone simulator launcher (`python -m rigplane_testkit.sim serve`).

In-process tests use ``build_stack`` and the aiohttp test client;
subprocess tests start the real entry point — one end to end against a
production ``IcomRadio``, the rest checking signals and --trace.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import signal
import sys
from contextlib import asynccontextmanager, suppress
from pathlib import Path
from types import SimpleNamespace

import pytest
from aiohttp import ClientSession
from aiohttp.test_utils import TestClient, TestServer

from rigplane.radio import IcomRadio

from rigplane_testkit.sim.__main__ import _control_arg, build_parser
from rigplane_testkit.sim.serve import (
    TRACE_RECORD_LIMIT,
    TraceLog,
    build_stack,
    format_control_host,
)

from _perf_helpers import fast_connect

_REPO_ROOT = Path(__file__).resolve().parents[2]

_STARTUP_KEYS = (
    "sim radio fidelity evidence transport host control_port username control seed"
).split()

# Obvious test-only credentials for the subprocess run: only --password can
# put this value on the wire, so any appearance in output is a leak.
_TEST_USERNAME = "sim-e2e-user"
_TEST_PASSWORD = "sim-e2e-pass-2f8d41"

# JSON literals a permissive parser accepts but that must never reach a
# radio timer as a duration; set_frequency additionally rejects 7.5 and "7".
_BAD_NUMERIC_LITERALS = ("NaN", "Infinity", "-Infinity", "true", '"5"')
_HZ_BAD_LITERALS = ("NaN", "Infinity", "-Infinity", "true", "7.5", '"7"')


def _args(*extra: str) -> argparse.Namespace:
    return build_parser().parse_args(["serve", "--radio", "ic7610", *extra])


def _strict_loads(text: str) -> dict:
    def _reject(literal: str) -> None:
        raise AssertionError(f"non-finite JSON literal in body: {literal}")

    return json.loads(text, parse_constant=_reject)


async def _post_raw(s, data: str):
    return await s.client.post(
        "/event", data=data, headers={"Content-Type": "application/json"}
    )


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
        assert resp.status == 400 and (await resp.json())["error"] == "invalid_args"

        resp = await s.client.post(
            "/event", data=b"not json", headers={"Content-Type": "application/json"}
        )
        assert resp.status == 400 and (await resp.json())["error"] == "invalid_args"


async def test_non_finite_or_mistyped_numbers_rejected() -> None:
    # Raw text bodies: the guard lives in the handler, not JSON decoding.
    with pytest.raises(AssertionError):
        _strict_loads('{"x": NaN}')  # the parser itself must be strict
    async with _stack() as s:
        cases = (("power_cycle", "down_for_s"), ("keepalive_silence", "for_s"))
        for event, arg in cases:
            for literal in _BAD_NUMERIC_LITERALS:
                resp = await _post_raw(s, f'{{"event":"{event}","{arg}":{literal}}}')
                assert resp.status == 400, literal
                body = _strict_loads(await resp.text())
                assert body["error"] == "invalid_args", literal
                assert body["event"] == event and body["detail"], literal

        for literal in _HZ_BAD_LITERALS:
            resp = await _post_raw(s, f'{{"event":"set_frequency","hz":{literal}}}')
            assert resp.status == 400, literal
            body = _strict_loads(await resp.text())
            assert body["error"] == "invalid_args", literal
            assert body["event"] == "set_frequency" and body["detail"], literal


async def test_non_finite_args_never_reach_trace_output(tmp_path: Path) -> None:
    # NaN/Infinity must not surface verbatim in GET /trace or the --trace file.
    trace_path = tmp_path / "trace.jsonl"
    async with _stack("--trace", str(trace_path)) as s:
        for literal in ("NaN", "Infinity"):
            resp = await _post_raw(
                s, f'{{"event":"power_cycle","down_for_s":{literal}}}'
            )
            assert resp.status == 400, literal
        # An accepted event with non-finite floats nested in an extra key.
        resp = await _post_raw(
            s, '{"event":"session_drop","junk":{"deep":[NaN, Infinity, -Infinity]}}'
        )
        assert resp.status == 200
        body = _strict_loads(await (await s.client.get("/trace")).text())
    file_records = [_strict_loads(x) for x in trace_path.read_text().splitlines()]
    for records in (body["events"], file_records):
        rejected = [rec for rec in records if rec["name"] == "power_cycle"]
        assert [rec["args"]["down_for_s"] for rec in rejected] == ["NaN", "Infinity"]
        junked = [rec for rec in records if rec["name"] == "session_drop"]
        assert junked[0]["args"]["junk"] == {"deep": ["NaN", "Infinity", "-Infinity"]}


async def test_huge_integers_rejected() -> None:
    # 10**1000 parses to an exact int too large for any float conversion.
    async with _stack() as s:
        for event, arg in (
            ("set_frequency", "hz"),
            ("power_cycle", "down_for_s"),
            ("keepalive_silence", "for_s"),
        ):
            resp = await _post_raw(s, json.dumps({"event": event, arg: 10**1000}))
            assert resp.status == 400, event
            assert (await resp.json())["error"] == "invalid_args", event


async def test_deeply_nested_body_rejected_and_traced() -> None:
    # 5000 levels: parsing survives; the sanitizer rejects before any handler.
    async with _stack() as s:
        deep = "[" * 5000 + "]" * 5000
        before = await (await s.client.get("/state")).json()
        token = s.inner.radio.token
        resp = await _post_raw(s, f'{{"event":"session_drop","junk":{deep}}}')
        assert resp.status == 400
        verdict = {"ok": False, "error": "invalid_args", "event": "session_drop"}
        assert (await resp.json()) == verdict
        rec = (await (await s.client.get("/trace")).json())["events"][-1]
        assert (rec["kind"], rec["name"], rec["args"]) == ("event", "session_drop", {})
        assert rec["result"] == verdict
        # No handler ran: /state unchanged; a drop that ran rotates the token.
        assert await (await s.client.get("/state")).json() == before
        assert s.inner.radio.token == token


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
        transitions = 0
        original_set = s.inner.stop_event.set

        def _counting_set() -> None:
            nonlocal transitions
            transitions += 0 if s.inner.stop_event.is_set() else 1
            original_set()

        s.inner.stop_event.set = _counting_set  # type: ignore[method-assign]
        for _ in range(2):
            resp = await s.client.post("/stop")
            assert resp.status == 200 and await resp.json() == {"ok": True}
        await asyncio.wait_for(s.inner.stop_event.wait(), 5)
        assert s.inner.stop_event.is_set() and transitions == 1


def test_trace_log_is_bounded_but_n_keeps_counting() -> None:
    trace = TraceLog()
    for i in range(TRACE_RECORD_LIMIT + 5):
        trace.append("event", "tick", {"i": i}, {"ok": True})
    snapshot = trace.snapshot(0)
    # The last TRACE_RECORD_LIMIT records stay in memory; n keeps counting.
    assert len(snapshot["events"]) == TRACE_RECORD_LIMIT
    assert snapshot["events"][0]["n"] == 5
    assert snapshot["events"][-1]["n"] == TRACE_RECORD_LIMIT + 4
    assert snapshot["next"] == TRACE_RECORD_LIMIT + 5
    assert trace.snapshot(snapshot["next"])["events"] == []


def test_control_host_forms() -> None:
    assert format_control_host("127.0.0.1") == "127.0.0.1"
    assert format_control_host("localhost") == "localhost"
    assert format_control_host("::1") == "[::1]"
    assert format_control_host("fe80::1") == "[fe80::1]"
    with pytest.raises(argparse.ArgumentTypeError, match=r"\[host\]:port"):
        _control_arg("::1")


@pytest.mark.parametrize("bad", ["nan", "inf", "-1"])
def test_hold_s_rejects_nonfinite_or_negative(bad: str, capsys) -> None:
    with pytest.raises(SystemExit) as exc:
        _args("--keepalive-hold-s", bad)
    assert exc.value.code == 2
    assert "--keepalive-hold-s" in capsys.read_outerr().err


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
    try:
        line = json.loads(await asyncio.wait_for(proc.stdout.readline(), 20))
    except BaseException:
        # The caller never received proc: it must not leak.
        with suppress(ProcessLookupError):
            proc.kill()
        await proc.wait()
        raise
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

        # The password is never printed, logged or returned: scan output
        # and responses while the subprocess is still running.
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
        # U+FFFD replacement cannot spell the ASCII password, so this scan stays safe.
        assert _TEST_PASSWORD not in stderr.decode(errors="replace")
    finally:
        await _kill_if_alive(proc)

    # --trace FILE: one valid JSON line per record, final stop record included.
    records = [_strict_loads(x) for x in trace_path.read_text().splitlines()]
    assert all(record["n"] == i for i, record in enumerate(records))
    assert records[0]["kind"] == "start"
    assert (records[-1]["kind"], records[-1]["name"]) == ("stop", "shutdown")
    assert records[-1]["result"] == {"ok": True}


@pytest.mark.parametrize("sig", [signal.SIGTERM, signal.SIGINT])
async def test_signal_exits_0_and_writes_stop_record(tmp_path: Path, sig) -> None:
    if sys.platform == "win32":
        pytest.skip("POSIX signal delivery")
    trace_path = tmp_path / "trace.jsonl"
    proc, _line = await _start_sim("--trace", str(trace_path))
    proc.send_signal(sig)
    try:
        assert await asyncio.wait_for(proc.wait(), 10) == 0
    finally:
        await _kill_if_alive(proc)
    records = [_strict_loads(x) for x in trace_path.read_text().splitlines()]
    assert records[-1]["kind"] == "stop"
    assert records[-1]["result"] == {"ok": True}
