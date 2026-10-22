"""Tests for the standalone simulator launcher (`python -m rigplane_testkit.sim serve`).

In-process tests use ``build_stack`` and the aiohttp test client;
subprocess tests start the real entry point — one end to end against a
production ``IcomRadio``, one against the IC-7300 serial fake's real
socket endpoint, the rest checking signals and --trace.
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
import serial
from aiohttp import ClientSession
from aiohttp.test_utils import TestClient, TestServer

from rigplane.commands import CONTROLLER_ADDR, build_civ_frame, parse_civ_frame
from rigplane.radio import IcomRadio

from rigplane_testkit.serial.endpoints import pty_supported
from rigplane_testkit.sim.__main__ import _control_arg, build_parser
from rigplane_testkit.sim.engine import Ok, SetMeter
from rigplane_testkit.sim.serve import (
    TRACE_RECORD_LIMIT,
    TraceLog,
    build_stack,
    format_control_host,
    startup_line,
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
    await stack.backend.start()
    client = TestClient(TestServer(stack.app))
    await client.start_server()
    try:
        yield SimpleNamespace(inner=stack, client=client)
    finally:
        await client.close()
        await stack.backend.stop()
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
    # 5000 levels: the parse depth limit differs between Python versions.
    async with _stack() as s:
        deep = "[" * 5000 + "]" * 5000
        before = await (await s.client.get("/state")).json()
        token = s.inner.backend.radio.token
        resp = await _post_raw(s, f'{{"event":"session_drop","junk":{deep}}}')
        assert resp.status == 400
        body = _strict_loads(await resp.text())
        assert body["ok"] is False and body["error"] == "invalid_args"
        rec = (await (await s.client.get("/trace")).json())["events"][-1]
        assert rec["kind"] == "event" and rec["args"] == {}
        assert rec["name"] in ("", "session_drop")
        # No handler ran: /state unchanged; a drop that ran rotates the token.
        assert await (await s.client.get("/state")).json() == before
        assert s.inner.backend.radio.token == token


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
    assert "--keepalive-hold-s" in capsys.readouterr().err


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


# --- Serial events (in-process) ---------------------------------------------


async def test_stop_cancels_pending_reappear_timer() -> None:
    async with _stack("--radio", "ic7300") as s:  # last --radio wins
        await s.client.post("/event", json={"event": "usb_vanish", "for_s": 5})
        handle = s.inner.backend.vanish._reappear
        assert handle is not None  # the 5 s restore timer is pending
        await s.inner.backend.stop()
        assert handle.cancelled()
        names = [rec["name"] for rec in s.inner.trace.snapshot(0)["events"]]
        assert "usb_reappear" not in names


@pytest.mark.parametrize("literal", _BAD_NUMERIC_LITERALS + ("-1", "0", "null"))
@pytest.mark.parametrize("event", ("silence", "usb_vanish"))
async def test_serial_events_reject_bad_durations(event: str, literal: str) -> None:
    async with _stack("--radio", "ic7300") as s:
        fake = s.inner.backend.radio
        resp = await _post_raw(s, f'{{"event":"{event}","for_s":{literal}}}')
        assert resp.status == 400, literal
        assert _strict_loads(await resp.text())["error"] == "invalid_args"
        assert fake._vanished is False and fake._silent_until == 0.0


# --- FTX-1 (Yaesu CAT serial, MOR-3602) --------------------------------------

_FTX1_STARTUP_KEYS = "sim radio fidelity evidence transport device pty control seed"
_FTX1_STATE_KEYS = "frequency_hz mode ptt s_meter open_count vanished"


async def test_ftx1_startup_line_keys_and_state() -> None:
    args = _args("--radio", "ftx1")
    stack = build_stack(args)
    await stack.backend.start()
    client = TestClient(TestServer(stack.app))
    await client.start_server()
    try:
        line = startup_line(stack.backend, args, 4321)
        assert list(line) == _FTX1_STARTUP_KEYS.split()
        assert line["sim"] == "rigplane-sim/1" and line["radio"] == "FTX-1"
        assert line["fidelity"] == "A" and line["evidence"] == "simulated"
        assert line["transport"] == "serial" and line["seed"] == 0
        assert line["device"].startswith("socket://127.0.0.1:")
        if pty_supported():
            assert isinstance(line["pty"], str) and line["pty"].startswith("/dev/")
        else:
            assert line["pty"] is None

        state = await (await client.get("/state")).json()
        assert list(state) == _FTX1_STATE_KEYS.split()
        assert state["frequency_hz"] == 147_225_000  # seeded from the recording
        assert state["mode"] == "FM"  # the CAT mode NAME, not a number
        assert state["ptt"] is False and state["vanished"] is False
        assert isinstance(state["s_meter"], int)
        assert state["open_count"] == (1 if pty_supported() else 0)
    finally:
        await client.close()
        await stack.backend.stop()
        stack.trace.close()


async def test_ftx1_events() -> None:
    async with _stack("--radio", "ftx1") as s:
        resp = await s.client.post(
            "/event", json={"event": "set_frequency", "hz": 7_074_000}
        )
        assert resp.status == 200
        assert (await resp.json())["frequency_hz"] == 7_074_000
        state = await (await s.client.get("/state")).json()
        assert state["frequency_hz"] == 7_074_000

        # Above the FTX-1's 470 MHz top: the existing invalid_args path.
        for hz in (999_999_999, "7074000"):
            resp = await s.client.post(
                "/event", json={"event": "set_frequency", "hz": hz}
            )
            assert resp.status == 400, hz
            assert (await resp.json())["error"] == "invalid_args", hz
        assert (await (await s.client.get("/state")).json())[
            "frequency_hz"
        ] == 7_074_000

        resp = await s.client.post("/event", json={"event": "silence", "for_s": 0.05})
        assert resp.status == 200
        resp = await s.client.post("/event", json={"event": "silence", "for_s": -1})
        assert resp.status == 400

        resp = await s.client.post("/event", json={"event": "usb_vanish"})
        assert resp.status == 200
        assert (await (await s.client.get("/state")).json())["vanished"] is True
        resp = await s.client.post("/event", json={"event": "usb_reappear"})
        assert resp.status == 200
        assert (await (await s.client.get("/state")).json())["vanished"] is False

        # IC-7610-only events answer unsupported.
        resp = await s.client.post("/event", json={"event": "session_drop"})
        assert resp.status == 400
        assert (await resp.json())["error"] == "unsupported"


async def test_ftx1_s_meter_state_matches_the_wire() -> None:
    async with _stack("--radio", "ftx1") as s:
        fake = s.inner.backend.radio
        reader, writer = await fake.open_serial_connection()
        try:
            # The seeded MAIN AGC is OFF (recorded GT00;), which pins SM0
            # at 0 on the wire: enable AGC first (a served SET, no reply).
            writer.write(b"GT01;")
            outcome = fake.model.engine.apply(SetMeter("s_meter_main", 77))
            assert isinstance(outcome, Ok)
            # /state reads the same s_meter_main the SM0; reply serves.
            state = await (await s.client.get("/state")).json()
            assert state["s_meter"] == 77
            writer.write(b"SM0;")
            assert await asyncio.wait_for(reader.readuntil(b";"), 3.0) == b"SM0077;"
        finally:
            writer.close()


async def test_ftx1_unmodelled_socket_request_is_traced() -> None:
    async with _stack("--radio", "ftx1") as s:
        device = s.inner.backend.startup_fields()["device"]
        port = await asyncio.to_thread(_socket_serial, device)
        try:
            await asyncio.to_thread(port.write, b"QQ;")
            reply = await asyncio.wait_for(
                asyncio.to_thread(port.read_until, b";"), 3.0
            )
            assert bytes(reply) == b"?;"
        finally:
            await asyncio.to_thread(port.close)

        body = await (await s.client.get("/trace")).json()
        gaps = [rec for rec in body["events"] if rec["name"] == "not_modelled"]
        assert len(gaps) == 1
        assert gaps[0]["kind"] == "event"
        assert gaps[0]["args"] == {"request": "QQ;"}
        assert gaps[0]["result"] == {"ok": False, "outcome": "unsupported"}


async def test_ftx1_keyer_memory_over_socket_serial() -> None:
    """MOR-3678: KM/KY through serve --radio ftx1's real TCP endpoint.

    pyserial's ``socket://`` open, one burst of CAT bytes, the model behind
    the adapter's splitter. Per the CAT OM 2508-C (printed p.17-18): KM1CQ;
    stores, KM1; reads it back, KY01; plays TEXT memory 1 (no answer), KY0;
    lacks P2 and KY; is a read KY does not have, so both answer ?;. Nothing
    is transmitted: TX; still reads TX0; and the engine PTT stays off.
    """
    async with _stack("--radio", "ftx1") as s:
        fake = s.inner.backend.radio
        device = s.inner.backend.startup_fields()["device"]
        port = await asyncio.to_thread(_socket_serial, device)
        try:
            await asyncio.to_thread(port.write, b"KM1CQ;KM1;KY01;KY0;KY;TX;")
            replies = [
                bytes(
                    await asyncio.wait_for(
                        asyncio.to_thread(port.read_until, b";"), 5.0
                    )
                )
                for _ in range(4)
            ]
        finally:
            await asyncio.to_thread(port.close)

        assert replies == [b"KM1CQ;", b"?;", b"?;", b"TX0;"]
        assert fake.model.keying_memory == {1: "CQ"}
        assert fake.model.keyer_play_requests == ((0, 1),)
        assert fake.ptt is False
        state = await (await s.client.get("/state")).json()
        assert state["ptt"] is False


async def test_ftx1_stop_cancels_pending_reappear_timer() -> None:
    async with _stack("--radio", "ftx1") as s:
        await s.client.post("/event", json={"event": "usb_vanish", "for_s": 5})
        handle = s.inner.backend.vanish._reappear
        assert handle is not None  # the 5 s restore timer is pending
        await s.inner.backend.stop()
        assert handle.cancelled()
        names = [rec["name"] for rec in s.inner.trace.snapshot(0)["events"]]
        assert "usb_reappear" not in names


def test_ftx1_profile_failure_is_a_clear_startup_error(tmp_path, monkeypatch) -> None:
    # Missing files under the root: a one-line SystemExit, not a traceback
    # from deep inside a loader.
    monkeypatch.setattr("rigplane_testkit.sim.serve._FTX1_REPO_ROOT", tmp_path)
    with pytest.raises(SystemExit) as exc:
        build_stack(_args("--radio", "ftx1"))
    message = str(exc.value)
    assert "serve --radio ftx1" in message and "ftx1.toml" in message

    # Bad TOML surfaces the loader's own message, still as SystemExit.
    profile = tmp_path / "rigplane_testkit" / "sim" / "profiles" / "ftx1.toml"
    profile.parent.mkdir(parents=True)
    profile.write_text("not [valid", encoding="utf-8")
    with pytest.raises(SystemExit) as exc:
        build_stack(_args("--radio", "ftx1"))
    assert "serve --radio ftx1" in str(exc.value)
    assert "invalid TOML" in str(exc.value)


async def test_ftx1_vanish_is_visible_until_an_explicit_reappear() -> None:
    # A long window: nothing here depends on how fast the next request runs.
    async with _stack("--radio", "ftx1") as s:
        resp = await s.client.post("/event", json={"event": "usb_vanish", "for_s": 60})
        assert resp.status == 200
        assert (await (await s.client.get("/state")).json())["vanished"] is True
        resp = await s.client.post("/event", json={"event": "usb_reappear"})
        assert resp.status == 200
        assert (await (await s.client.get("/state")).json())["vanished"] is False


async def test_ftx1_scheduled_reappear_fires_and_is_traced() -> None:
    # Wait for the condition with a deadline; do not assert the transient
    # vanished state, which a loaded worker can legitimately miss.
    async with _stack("--radio", "ftx1") as s:
        resp = await s.client.post(
            "/event", json={"event": "usb_vanish", "for_s": 0.05}
        )
        assert resp.status == 200

        async def _reappeared() -> None:
            while True:
                state = await (await s.client.get("/state")).json()
                names = [rec["name"] for rec in s.inner.trace.snapshot(0)["events"]]
                if not state["vanished"] and "usb_reappear" in names:
                    return
                await asyncio.sleep(0.01)

        await asyncio.wait_for(_reappeared(), 5)


def _socket_serial(url: str) -> serial.SerialBase:
    """The production open over pyserial's ``socket://`` URL, 3 s timeout."""
    return serial.serial_for_url(url, baudrate=38400, timeout=3)


# --- Subprocess end to end ----------------------------------------------------


async def _start_sim(
    *extra: str, radio: str = "ic7610"
) -> tuple[asyncio.subprocess.Process, dict]:
    proc = await asyncio.create_subprocess_exec(
        sys.executable,
        "-m",
        "rigplane_testkit.sim",
        "serve",
        "--radio",
        radio,
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


async def _wire_identity(reader, writer, ident: bytes) -> bytes:
    # Send *ident* on a raw stream; return the parsed reply past the echo.
    writer.write(ident)
    await writer.drain()
    echo = await asyncio.wait_for(reader.readuntil(b"\xfd"), 5)
    assert echo == ident
    return parse_civ_frame(bytes(await asyncio.wait_for(reader.readuntil(b"\xfd"), 5)))


async def _wait_trace_record(control: str, name: str) -> dict:
    # Bounded poll (5 s, 10 ms ticks) of GET /trace for a *name* record.
    async with ClientSession() as http:

        async def _poll() -> dict:
            events: list[dict] = []
            while not any(rec["name"] == name for rec in events):
                events = _strict_loads(
                    await (await http.get(f"{control}/trace")).text()
                )["events"]
                await asyncio.sleep(0.01)
            return next(rec for rec in events if rec["name"] == name)

        return await asyncio.wait_for(_poll(), 5.0)


async def test_subprocess_serial_end_to_end(tmp_path: Path) -> None:
    if sys.platform == "win32":
        pytest.skip("POSIX signal delivery")
    trace_path = tmp_path / "trace.jsonl"
    proc, line = await _start_sim("--trace", str(trace_path), radio="ic7300")
    try:
        keys = "sim radio fidelity evidence transport device pty control seed"
        assert list(line) == keys.split()
        assert line["radio"] == "IC-7300" and line["fidelity"] == "B"
        assert isinstance(line["pty"], str) and line["pty"].startswith("/dev/")
        host, port = line["device"].removeprefix("socket://").rsplit(":", 1)
        ident = build_civ_frame(0x94, CONTROLLER_ADDR, 0x19, sub=0x00)
        reader, writer = await asyncio.open_connection(host, int(port))
        assert (await _wire_identity(reader, writer, ident)).data == b"\x94"

        # usb_vanish window: the client drops, the traced reappear lands, it answers.
        async with ClientSession() as http:
            async with http.post(
                f"{line['control']}/event",
                json={"event": "usb_vanish", "for_s": 0.3},
            ) as resp:
                assert resp.status == 200
        assert await asyncio.wait_for(reader.read(), 5) == b""
        reappear = await _wait_trace_record(line["control"], "usb_reappear")
        assert reappear["kind"] == "event" and reappear["result"] == {"ok": True}

        reader2, writer2 = await asyncio.open_connection(host, int(port))
        assert (await _wire_identity(reader2, writer2, ident)).data == b"\x94"
        writer2.close()

        async with ClientSession() as http:
            state = await (await http.get(f"{line['control']}/state")).json()
            # The serial /state key set; IC-7610-only events answer unsupported.
            wanted = "frequency_hz mode ptt s_meter open_count vanished".split()
            assert set(state) == set(wanted)
            assert (state["frequency_hz"], state["vanished"]) == (14_074_000, False)
            async with http.post(
                f"{line['control']}/event", json={"event": "session_drop"}
            ) as resp:
                assert resp.status == 400
                assert (await resp.json())["error"] == "unsupported"

        proc.send_signal(signal.SIGTERM)
        assert await asyncio.wait_for(proc.wait(), 10) == 0
    finally:
        await _kill_if_alive(proc)

    file_records = [_strict_loads(x) for x in trace_path.read_text().splitlines()]
    assert (file_records[-1]["kind"], file_records[-1]["name"]) == ("stop", "shutdown")
    assert file_records[-1]["result"] == {"ok": True}


async def test_subprocess_serial_end_to_end_ftx1(tmp_path: Path) -> None:
    if sys.platform == "win32":
        pytest.skip("POSIX signal delivery")
    trace_path = tmp_path / "trace.jsonl"
    proc, line = await _start_sim("--trace", str(trace_path), radio="ftx1")
    try:
        keys = "sim radio fidelity evidence transport device pty control seed"
        assert list(line) == keys.split()
        assert line["radio"] == "FTX-1" and line["fidelity"] == "A"
        assert line["evidence"] == "simulated" and line["transport"] == "serial"
        if pty_supported():
            assert isinstance(line["pty"], str) and line["pty"].startswith("/dev/")
        else:
            assert line["pty"] is None

        port = await asyncio.to_thread(_socket_serial, line["device"])
        try:
            await asyncio.to_thread(port.write, b"ID;")
            reply = await asyncio.wait_for(asyncio.to_thread(port.read_until, b";"), 10)
            assert bytes(reply) == b"ID0840;"
        finally:
            await asyncio.to_thread(port.close)

        async with ClientSession() as http:
            async with http.post(f"{line['control']}/stop") as resp:
                assert resp.status == 200
                assert await resp.json() == {"ok": True}

        assert await asyncio.wait_for(proc.wait(), 10) == 0
        # Nothing else was ever printed on stdout.
        assert await proc.stdout.read() == b""
    finally:
        await _kill_if_alive(proc)

    file_records = [_strict_loads(x) for x in trace_path.read_text().splitlines()]
    assert (file_records[-1]["kind"], file_records[-1]["name"]) == ("stop", "shutdown")
    assert file_records[-1]["result"] == {"ok": True}
