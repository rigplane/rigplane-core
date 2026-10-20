"""Tests for the standalone simulator launcher (`python -m rigplane_testkit.sim serve`).

In-process tests build the same objects ``serve.py`` builds (via
``build_stack``) and drive the control endpoint through the aiohttp test
client; one subprocess test runs the real entry point end to end against a
production ``IcomRadio``, exactly the way the Pro dogfood scorecard will
bind to it (MOR-3593).
"""

from __future__ import annotations

import argparse
import asyncio
import json
import socket
import struct
import sys
from pathlib import Path

from aiohttp import ClientSession
from aiohttp.test_utils import TestClient, TestServer

from rigplane.radio import IcomRadio

from rigplane_testkit.sim.__main__ import build_parser
from rigplane_testkit.sim.serve import ServeStack, build_stack

from _perf_helpers import fast_connect
from testkit.test_simulator_faults import _UdpClient, _ayt

_REPO_ROOT = Path(__file__).resolve().parents[2]

_STARTUP_KEYS = [
    "sim",
    "radio",
    "fidelity",
    "evidence",
    "transport",
    "host",
    "control_port",
    "username",
    "password",
    "control",
    "seed",
]

# The four (port, direction) streams a lan_drop blackouts.
_LAN_DROP_STREAMS = (
    "control_to_client",
    "control_from_client",
    "civ_to_client",
    "civ_from_client",
)


def _args(*extra: str) -> argparse.Namespace:
    return build_parser().parse_args(["serve", "--radio", "ic7610", *extra])


class _Stack:
    """build_stack result started for one in-process test."""

    def __init__(self, stack: ServeStack) -> None:
        self.inner = stack
        self.client: TestClient | None = None

    async def __aenter__(self) -> "_Stack":
        await self.inner.radio.start()
        self.client = TestClient(TestServer(self.inner.app))
        await self.client.start_server()
        return self

    async def __aexit__(self, *exc: object) -> None:
        assert self.client is not None
        # Mirror run_serve's shutdown: no lan_drop restore may outlive the
        # radio (it would mutate a stopped radio's fault plan).
        self.inner.lan_drop.cancel()
        await self.client.close()
        await self.inner.radio.stop()
        self.inner.trace.close()


def _stack(*extra: str) -> _Stack:
    return _Stack(build_stack(_args(*extra)))


# ---------------------------------------------------------------------------
# Control endpoint semantics (in-process)
# ---------------------------------------------------------------------------


async def test_unknown_event_returns_400_unsupported() -> None:
    async with _stack() as s:
        resp = await s.client.post("/event", json={"event": "nope"})
        assert resp.status == 400
        assert await resp.json() == {
            "ok": False,
            "error": "unsupported",
            "event": "nope",
        }


async def test_bad_args_return_400_invalid_args() -> None:
    async with _stack() as s:
        resp = await s.client.post("/event", json={"event": "set_frequency", "hz": "x"})
        assert resp.status == 400
        body = await resp.json()
        assert body["ok"] is False
        assert body["error"] == "invalid_args"

        resp = await s.client.post("/event", json={"event": "lan_drop", "for_s": 0})
        assert resp.status == 400
        assert (await resp.json())["error"] == "invalid_args"

        resp = await s.client.post(
            "/event", data=b"not json", headers={"Content-Type": "application/json"}
        )
        assert resp.status == 400
        assert (await resp.json())["error"] == "invalid_args"


async def test_set_frequency_changes_state() -> None:
    async with _stack() as s:
        state = await (await s.client.get("/state")).json()
        assert state["frequency_hz"] == 14_074_000
        assert state["ptt"] is False
        assert state["session_held"] is False
        assert state["login_count"] == 0

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


async def test_lan_drop_installs_full_blackout_rules() -> None:
    async with _stack() as s:
        seed_before = s.inner.radio.fault_plan.seed
        plan = s.inner.radio.fault_plan
        assert plan is not None
        saved = {stream: getattr(plan, stream) for stream in _LAN_DROP_STREAMS}
        resp = await s.client.post("/event", json={"event": "lan_drop", "for_s": 0.2})
        assert resp.status == 200
        assert plan.seed == seed_before
        for stream in _LAN_DROP_STREAMS:
            rule = getattr(plan, stream)
            assert rule.drop_probability == 1.0, stream
            # The window is exact: serve.py restores the rules itself, so
            # the rules carry no duration bound of their own.
            assert rule.duration_s is None, stream
        # After exactly for_s the ORIGINAL rule objects are back.
        await asyncio.sleep(0.4)
        for stream in _LAN_DROP_STREAMS:
            assert getattr(plan, stream) is saved[stream], stream


async def test_overlapping_lan_drop_restores_once_at_later_deadline() -> None:
    async with _stack() as s:
        plan = s.inner.radio.fault_plan
        assert plan is not None
        saved = {stream: getattr(plan, stream) for stream in _LAN_DROP_STREAMS}
        await s.client.post("/event", json={"event": "lan_drop", "for_s": 5.0})
        first_timer = s.inner.lan_drop.timer
        assert first_timer is not None

        await asyncio.sleep(0.1)
        resp = await s.client.post("/event", json={"event": "lan_drop", "for_s": 0.2})
        assert resp.status == 200
        # The first restore is gone; the blackout runs until the SECOND
        # deadline and then restores the ORIGINAL rules once.
        assert first_timer.cancelled()
        for stream in _LAN_DROP_STREAMS:
            assert getattr(plan, stream).drop_probability == 1.0, stream

        await asyncio.sleep(0.4)
        for stream in _LAN_DROP_STREAMS:
            assert getattr(plan, stream) is saved[stream], stream
        body = await (await s.client.get("/trace")).json()
        ends = [rec for rec in body["events"] if rec["name"] == "lan_drop_end"]
        assert len(ends) == 1


async def test_lan_drop_end_record_in_trace() -> None:
    async with _stack() as s:
        await s.client.post("/event", json={"event": "lan_drop", "for_s": 0.2})
        await asyncio.sleep(0.4)
        body = await (await s.client.get("/trace")).json()
        rec = next(rec for rec in body["events"] if rec["name"] == "lan_drop_end")
        assert rec["kind"] == "event"
        assert rec["args"] == {}
        assert rec["result"] == {"ok": True}


async def test_shutdown_cancels_pending_lan_drop_restore() -> None:
    async with _stack() as s:
        await s.client.post("/event", json={"event": "lan_drop", "for_s": 30.0})
        timer = s.inner.lan_drop.timer
        assert timer is not None
        assert not timer.cancelled()
    # Teardown ran the serve shutdown path: no scheduled restore remains.
    assert timer.cancelled()
    assert s.inner.lan_drop.timer is None


async def test_trace_records_and_since_filter() -> None:
    async with _stack() as s:
        await s.client.post("/event", json={"event": "set_frequency", "hz": 7_000_000})
        await s.client.post("/event", json={"event": "nope"})

        body = await (await s.client.get("/trace")).json()
        kinds = [(rec["kind"], rec["name"]) for rec in body["events"]]
        assert kinds == [
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


# ---------------------------------------------------------------------------
# Trace determinism
# ---------------------------------------------------------------------------

_DETERMINISM_EVENTS = [
    {"event": "set_frequency", "hz": 7_074_000},
    {"event": "set_frequency", "hz": 0},
    {"event": "nope"},
    {"event": "lan_drop", "for_s": 1.5},
    {"event": "power_cycle", "down_for_s": 0.2},
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


# ---------------------------------------------------------------------------
# Subprocess end to end
# ---------------------------------------------------------------------------


async def test_subprocess_end_to_end() -> None:
    proc = await asyncio.create_subprocess_exec(
        sys.executable,
        "-m",
        "rigplane_testkit.sim",
        "serve",
        "--radio",
        "ic7610",
        "--seed",
        "1",
        cwd=_REPO_ROOT,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.DEVNULL,
    )
    try:
        first = await asyncio.wait_for(proc.stdout.readline(), 20)
        line = json.loads(first)
        assert list(line) == _STARTUP_KEYS
        assert line["sim"] == "rigplane-sim/1"
        assert line["radio"] == "IC-7610"
        assert line["fidelity"] == "A"
        assert line["evidence"] == "simulated"
        assert line["transport"] == "lan"
        assert line["host"] == "127.0.0.1"
        assert line["seed"] == 1
        assert isinstance(line["control_port"], int) and line["control_port"] > 0
        assert line["username"] and line["password"]
        # "control" is the HTTP endpoint; "control_port" the radio LAN port.
        http_port = int(line["control"].rsplit(":", 1)[1])
        assert line["control"] == f"http://127.0.0.1:{http_port}"
        assert http_port > 0

        radio = IcomRadio(
            host=line["host"],
            port=line["control_port"],
            username=line["username"],
            password=line["password"],
            timeout=5.0,
            model="IC-7610",
        )
        with fast_connect():
            await radio.connect()
        assert await radio.get_freq() == 14_074_000

        async with ClientSession() as http:
            async with http.post(
                f"{line['control']}/event",
                json={"event": "set_frequency", "hz": 7_100_000},
            ) as resp:
                assert resp.status == 200
        assert await radio.get_freq() == 7_100_000
        await radio.disconnect()

        async with ClientSession() as http:
            async with http.post(f"{line['control']}/stop") as resp:
                assert resp.status == 200
                assert await resp.json() == {"ok": True}

        assert await asyncio.wait_for(proc.wait(), 10) == 0
        # Nothing else was ever printed on stdout.
        assert await proc.stdout.read() == b""

        with socket.socket() as sock:
            sock.settimeout(2.0)
            try:
                sock.connect(("127.0.0.1", http_port))
            except OSError:
                pass
            else:
                raise AssertionError("control endpoint still accepts connections")
    finally:
        if proc.returncode is None:
            proc.kill()
            await proc.wait()


# ---------------------------------------------------------------------------
# lan_drop wire effect
# ---------------------------------------------------------------------------


async def test_lan_drop_blackout_on_the_wire() -> None:
    async with _stack() as s:
        loop = asyncio.get_running_loop()
        udp = _UdpClient()
        transport, _ = await loop.create_datagram_endpoint(
            lambda: udp, remote_addr=("127.0.0.1", s.inner.radio.control_port)
        )
        try:
            udp.send(_ayt(), s.inner.radio.control_port)
            reply = await udp.recv(5.0)
            assert reply[4:6] == struct.pack("<H", 0x04)  # I-Am-Here

            resp = await s.client.post(
                "/event", json={"event": "lan_drop", "for_s": 1.0}
            )
            assert resp.status == 200

            # The window opened at the event itself, so this AYT falls
            # inside it and is swallowed.
            udp.send(_ayt(), s.inner.radio.control_port)
            await udp.expect_silence(0.4)
            await asyncio.sleep(1.0)  # >= 1.3 s since the event

            udp.send(_ayt(), s.inner.radio.control_port)
            reply = await udp.recv(5.0)
            assert reply[4:6] == struct.pack("<H", 0x04)
        finally:
            transport.close()
