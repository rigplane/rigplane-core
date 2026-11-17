"""The remote E2E sidecar exposes the simulator's own PTT journal (MOR-4493).

``tests/e2e/remote_web_server.py`` serves ``GET /sim`` to the Playwright
rows. The PTT transitions come from ``MockIcomRadio.ptt_transitions`` (the
simulator records every real flip), never from a sampling observer: a key
and unkey inside one sampling interval must still show both transitions.
The wire shape is the producer's frozen ``SimSnapshot``; the Node consumer
(frontend/tests/e2e/remote/station.ts ``parseSimState``) checks responses
against the annotations served at ``/sim/contract``.
"""

from __future__ import annotations

import asyncio
import importlib.util
import json
import sys
import time
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

from testkit.ic7610_fakes import ic7610_lan_fake

from rigplane.web.server import WebConfig, WebServer
from rigplane.web.transport.webrtc import webrtc_available

_SERVER_PATH = Path(__file__).resolve().parents[1] / "e2e" / "remote_web_server.py"

# What frontend/tests/e2e/remote/station.ts SIM_CONTRACT expects; the two
# literals change together or this test and the Node parser both fail.
_SNAPSHOT_CONTRACT = {
    "ptt_on": "bool",
    "freq_hz": "int",
    "ptt_transitions": "tuple[tuple[float, bool], ...]",
    "tx_audio_signal_frames": "int",
}
_FREQ_REQUEST_CONTRACT = {"hz": "int"}
_CONSUMER_CONTRACT = {
    "SimSnapshot": _SNAPSHOT_CONTRACT,
    "SimFreqRequest": _FREQ_REQUEST_CONTRACT,
}


# What frontend/tests/e2e/remote/station.ts RelayedAnswer reads from the
# offer reply: the `status` discriminator and the RTCSessionDescriptionInit
# pair. Same convention as _CONSUMER_CONTRACT: both literals change together.
_CONSUMED_ANSWER_KEYS = {"status", "sdp", "type"}


def _load_server() -> ModuleType:
    spec = importlib.util.spec_from_file_location("remote_web_server", _SERVER_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    # dataclasses resolves string annotations through sys.modules.
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


@pytest.mark.realtime(reason="asserts the journal's stamps are on the wall clock")
def test_key_and_unkey_inside_one_sampling_interval_record_both_transitions() -> None:
    server = _load_server()
    sim = ic7610_lan_fake(stateful_ptt=True)
    before = time.time()
    # Back to back, microseconds apart: a 25 ms sampler would see only OFF.
    sim._set_simulated_ptt(True, wire_set=False)  # noqa: SLF001
    sim._set_simulated_ptt(False, wire_set=False)  # noqa: SLF001
    after = time.time()

    snapshot = server.sim_snapshot(sim)

    assert snapshot.ptt_on is False
    assert [keyed for _, keyed in snapshot.ptt_transitions] == [True, False]
    stamps = [stamp for stamp, _ in snapshot.ptt_transitions]
    assert stamps == sorted(stamps)
    # Wall-clock seconds on the clock the Node side compares.
    assert all(before - 0.5 <= stamp <= after + 0.5 for stamp in stamps)


def test_contract_matches_the_dataclass_and_the_consumer() -> None:
    server = _load_server()
    sim = ic7610_lan_fake(stateful_ptt=True)

    import dataclasses

    payload = dataclasses.asdict(server.sim_snapshot(sim))

    assert server.sim_contract() == _CONSUMER_CONTRACT
    assert set(payload) == set(_SNAPSHOT_CONTRACT)


def test_sidecar_serves_the_snapshot_and_its_contract_over_http() -> None:
    server = _load_server()
    sim = ic7610_lan_fake(stateful_ptt=True)
    sim._set_simulated_ptt(True, wire_set=False)  # noqa: SLF001
    sim._set_simulated_ptt(False, wire_set=False)  # noqa: SLF001

    async def get(port: int, path: str) -> Any:
        reader, writer = await asyncio.open_connection("127.0.0.1", port)
        writer.write(f"GET {path} HTTP/1.1\r\nHost: x\r\n\r\n".encode())
        await writer.drain()
        raw = await reader.read()
        writer.close()
        head, _, body = raw.partition(b"\r\n\r\n")
        assert head.startswith(b"HTTP/1.1 200")
        return json.loads(body)

    async def scenario() -> tuple[Any, Any]:
        sidecar = server._SimSidecar(sim)  # noqa: SLF001
        listener = await sidecar.serve("127.0.0.1", 0)
        port = listener.sockets[0].getsockname()[1]
        try:
            return await get(port, "/sim"), await get(port, "/sim/contract")
        finally:
            listener.close()

    state, contract = asyncio.run(scenario())

    assert contract == _CONSUMER_CONTRACT
    assert set(state) == set(_SNAPSHOT_CONTRACT)
    assert state["ptt_on"] is False
    assert [keyed for _, keyed in state["ptt_transitions"]] == [True, False]
    assert isinstance(state["freq_hz"], int)


@pytest.mark.skipif(not webrtc_available(), reason="aiortc extra not installed")
def test_real_offer_reply_carries_exactly_what_the_harness_consumes() -> None:
    """The P0 probe relays this reply; build it with the real handler."""
    from aiortc import RTCPeerConnection

    class _Writer:
        def __init__(self) -> None:
            self.buffer = b""

        def write(self, data: bytes) -> None:
            self.buffer += data

        async def drain(self) -> None:
            return None

        def close(self) -> None:
            return None

        def get_extra_info(self, *_a: Any, **_k: Any) -> tuple[str, int]:
            return ("127.0.0.1", 0)

    async def scenario() -> tuple[int, dict[str, Any]]:
        server = WebServer(None, WebConfig(webrtc_enabled=True))
        client = RTCPeerConnection()
        client.createDataChannel("control")
        await client.setLocalDescription(await client.createOffer())
        body = json.dumps(
            {"sdp": client.localDescription.sdp, "type": client.localDescription.type}
        ).encode()
        reader = asyncio.StreamReader()
        reader.feed_data(body)
        reader.feed_eof()
        writer = _Writer()
        try:
            await server._handle_webrtc_offer(  # noqa: SLF001
                writer, {"content-length": str(len(body))}, reader
            )
        finally:
            if server._webrtc_sessions is not None:  # noqa: SLF001
                await server._webrtc_sessions.close_all()  # noqa: SLF001
            await client.close()
        head, _, payload = writer.buffer.partition(b"\r\n\r\n")
        return int(head.split(b" ", 2)[1]), json.loads(payload)

    code, reply = asyncio.run(scenario())

    assert code == 200
    assert reply["status"] == "ok"
    assert reply["type"] == "answer"
    assert isinstance(reply["sdp"], str) and reply["sdp"].startswith("v=0")
    # Everything the harness reads is present; the one extra key (sessionId)
    # is ignored by the harness.
    assert _CONSUMED_ANSWER_KEYS <= set(reply)
    assert set(reply) - _CONSUMED_ANSWER_KEYS == {"sessionId"}


@pytest.mark.parametrize(
    "body",
    [
        b"",
        b"not json",
        b"[]",
        b"{}",
        b'{"hz": "14074000"}',
        b'{"hz": 14074000.5}',
        b'{"hz": true}',
        b'{"hz": -1}',
        b'{"hz": 14074000, "extra": 1}',
    ],
)
def test_bad_frequency_request_is_rejected_before_it_reaches_the_simulator(
    body: bytes,
) -> None:
    server = _load_server()
    with pytest.raises(ValueError):
        server.parse_freq_request(body)


def test_frequency_request_over_http_is_parsed_through_its_contract() -> None:
    server = _load_server()
    sim = ic7610_lan_fake(stateful_ptt=True)
    before = sim._frequency  # noqa: SLF001

    async def post(port: int, body: bytes) -> tuple[int, Any]:
        reader, writer = await asyncio.open_connection("127.0.0.1", port)
        writer.write(
            b"POST /sim/freq HTTP/1.1\r\nHost: x\r\nContent-Length: "
            + str(len(body)).encode()
            + b"\r\n\r\n"
            + body
        )
        await writer.drain()
        raw = await reader.read()
        writer.close()
        head, _, payload = raw.partition(b"\r\n\r\n")
        return int(head.split(b" ", 2)[1]), json.loads(payload)

    async def scenario() -> list[tuple[int, Any]]:
        listener = await server._SimSidecar(sim).serve("127.0.0.1", 0)  # noqa: SLF001
        port = listener.sockets[0].getsockname()[1]
        try:
            return [
                await post(port, b'{"hz": "x"}'),
                await post(port, b"nope"),
                await post(port, b'{"hz": 7074000}'),
            ]
        finally:
            listener.close()

    rejected_type, rejected_json, accepted = asyncio.run(scenario())

    assert rejected_type[0] == 400
    assert rejected_json[0] == 400
    assert accepted == (200, {"ok": True, "freq_hz": 7_074_000})
    assert sim._frequency == 7_074_000 != before  # noqa: SLF001


def test_snapshot_counts_only_client_audio_frames_that_carry_signal() -> None:
    from rigplane_testkit.icom.simulator_audio import ClientAudioFrame

    server = _load_server()
    sim = ic7610_lan_fake(capture_tx_audio=True)

    def frame(payload: bytes) -> ClientAudioFrame:
        return ClientAudioFrame(
            seq=0,
            inner_seq=0,
            payload_len=len(payload),
            received_at=0.0,
            ptt=True,
            payload=payload,
            tx_format=None,
        )

    sim.audio_tx_frames.extend([frame(b"\x00" * 8), frame(b"\x00\x10" * 4), frame(b"")])

    assert server.sim_snapshot(sim).tx_audio_signal_frames == 1
