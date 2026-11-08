"""Real serial ingress and web wire regression; shared with the mobile browser test."""

from __future__ import annotations

import asyncio
import json
import math
import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

import aiohttp
import pytest

from rigplane.backends.ic7300 import Ic7300SerialRadio
from rigplane.commands import build_civ_frame
from rigplane.core.acquisition_scheduler import StateFreshnessService
from rigplane.core.state_store import FreshnessState
from rigplane.core.tx_target import KnownTxTarget
from rigplane.core.types import bcd_encode
from rigplane.profiles import resolve_radio_profile
from rigplane.runtime.managed_tx_composition import (
    ManagedTxComposition,
    install_managed_tx_composition,
)
from rigplane.testing.timing import await_until
from rigplane.web.server import WebConfig, WebServer
from rigplane.web.state_schema import StateUpdateEnvelope
from rigplane.web.web_startup import attach_managed_tx_composition
from test_icom7610_serial_radio import _FakeSerialCivLink, _FakeUsbAudioDriver

_GOLDEN = Path(__file__).parent / "golden/ic7300-split-tx-wire.json"
pytestmark = pytest.mark.realtime(
    reason="real serial RX pump, production poller and command response deadlines on a fake link"
)


class SplitLink(_FakeSerialCivLink):
    def __init__(self, *, model_id: int = 0x94) -> None:
        super().__init__(model_id=model_id)
        self.operating_hz = 29_600_000
        self.split = False
        self.ptt = False
        self.answer_frequency = True

    async def send(self, frame: bytes) -> None:
        await super().send(frame)
        query = frame[4:-1]
        payload: bytes | None
        if query in (b"\x19\x00", b"\x07\xd2"):
            return
        if query == b"\x03":
            payload = b"\x03" + bcd_encode(self.operating_hz) if self.answer_frequency else None
        elif query == b"\x04":
            payload = b"\x04\x01\x02"
        elif query == b"\x0f":
            payload = bytes((0x0F, self.split))
        elif query == b"\x1c\x00":
            payload = bytes((0x1C, 0x00, self.ptt))
        elif query in (b"\x25\x00", b"\x25\x01"):
            payload = query + bcd_encode(29_600_000 if query[-1] == 0 else 29_100_000)
        elif query in (b"\x26\x00", b"\x26\x01"):
            payload = query + b"\x01\x00\x02"
        elif query in (b"\x07\x00", b"\x07\x01"):
            payload = b"\xfb"
        elif query in (b"\x1c\x00\x00", b"\x1c\x00\x01", b"\x17\xff", b"\x1c\x01\x00"):
            return
        else:
            payload = b"\xfa"
        if payload is not None:
            self.queue_response(bytes((0xFE, 0xFE, frame[3], frame[2])) + payload + b"\xfd")


async def _ingress(radio: Ic7300SerialRadio, link: SplitLink, frames: list[str]) -> None:
    marker = build_civ_frame(
        0xE0, radio.profile.civ_addr, 0x19, sub=0x00, data=bytes((link.model_id,))
    )
    delivered = asyncio.Event()
    subscription = radio.add_raw_civ_listener(
        lambda raw: delivered.set() if raw == marker else None
    )
    try:
        for frame in frames:
            link.queue_response(bytes.fromhex(frame))
        link.queue_response(marker)
        await await_until(delivered.is_set, description="serial ingress marker")
    finally:
        subscription.close()


@asynccontextmanager
async def _session(tmp_path: Path) -> AsyncIterator[tuple[Ic7300SerialRadio, SplitLink, aiohttp.ClientSession, str]]:
    link = SplitLink()
    radio = Ic7300SerialRadio(
        device="/dev/rigplane-test-no-hardware",
        civ_link=link,
        audio_driver=_FakeUsbAudioDriver(),
        timeout=0.1,
        _enumerate_serial_ports_fn=lambda: [],
    )
    composition = ManagedTxComposition(radio, config_path=tmp_path / "tx.json")
    install_managed_tx_composition(radio, composition)
    await composition.bind_state_store(radio.state_store)
    server: WebServer | None = None
    try:
        await radio.connect()
        assert radio.connected
        await _ingress(radio, link, ["FEFEE0940F00FD"])
        assert await radio.get_freq() == 29_600_000
        link.split = True
        await _ingress(radio, link, ["FEFEE0940F01FD", "FEFEE0941C0000FD"])
        server = WebServer(radio, WebConfig(host="127.0.0.1", port=0))
        attach_managed_tx_composition(server, composition)
        await server.start()

        def ready() -> bool:
            try:
                value = radio.state_store.snapshot().field("global.tx_state.tx_target").value
            except KeyError:
                return False
            return isinstance(value, KnownTxTarget) and value.frequency_hz == 29_100_000

        await await_until(ready, timeout=15.0, description="web startup A selection and TX target")
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=10.0)) as session:
            yield radio, link, session, f"http://127.0.0.1:{server.port}"
    finally:
        if server is not None:
            await server.stop()
        await radio.disconnect()
        await composition.shutdown(asyncio.Event())


def _at(document: dict[str, Any], path: list[str]) -> Any:
    value: Any = document
    for key in path:
        value = value[key]
    return value


async def test_ic7300_public_ingress_split_tx_matches_browser_golden(tmp_path: Path) -> None:
    golden = json.loads(_GOLDEN.read_text())
    captured = []
    async with _session(tmp_path) as (radio, link, session, base):
        for step in golden["steps"]:
            if step["name"] == "ptt_on":
                link.ptt = True
            elif step["name"] == "ptt_off":
                link.ptt = False
            await _ingress(radio, link, step["frames"])
            async with session.get(base + "/api/v1/state") as response:
                assert response.status == 200
                payload = await response.json()
            async with session.ws_connect(base.replace("http", "ws") + "/api/v1/ws") as ws:
                async with asyncio.timeout(5):
                    while True:
                        message = json.loads((await ws.receive()).data)
                        if message["type"] == "state_update":
                            envelope = message["data"]
                            assert envelope["type"] == "full"
                            StateUpdateEnvelope.model_validate(envelope)
                            assert envelope["stateRevision"] >= 1
                            assert envelope["observationSeq"] >= 1
                            ws_payload = envelope["data"]
                            break
            # This assertion precedes golden maintenance: key-up must never poison RX.
            assert payload["main"]["freqHz"] == 29_600_000, step["name"]
            assert ws_payload["main"]["freqHz"] == 29_600_000, step["name"]
            assert radio.radio_state.main.freq == 29_600_000, step["name"]
            assert radio.state_cache.freq == 29_600_000, step["name"]
            actual_set = []
            for path, expected in step["envelope"]["set"]:
                actual = _at(payload, path)
                if path[0] == "fieldStatus":
                    actual = {key: actual[key] for key in expected}
                    observed_at = actual["lastObservedMonotonic"]
                    assert isinstance(observed_at, (int, float))
                    assert math.isfinite(observed_at) and observed_at >= 0
                    # Compare producer authority rows while pinning their clock origin.
                    actual["lastObservedMonotonic"] = 1000.0
                    ws_actual = {key: _at(ws_payload, path)[key] for key in expected}
                    ws_observed_at = ws_actual["lastObservedMonotonic"]
                    assert isinstance(ws_observed_at, (int, float))
                    assert math.isfinite(ws_observed_at) and ws_observed_at >= 0
                    ws_actual["lastObservedMonotonic"] = 1000.0
                    assert ws_actual == actual
                else:
                    assert _at(ws_payload, path) == actual
                actual_set.append([path, actual])
            async with session.get(base + "/api/v1/managed-transmit") as response:
                assert response.status == 200
                managed = await response.json()
            managed["sampledAt"] = step["managedTransmit"]["sampledAt"]
            captured.append((step, actual_set, managed))
        if os.environ.get("UPDATE_GOLDENS") == "1":
            for step, actual_set, managed in captured:
                step["envelope"]["set"] = actual_set
                step["managedTransmit"] = managed
            _GOLDEN.write_text(
                '{\n  "model": "IC-7300",\n  "steps": [\n'
                + ",\n".join("    " + json.dumps(step) for step in golden["steps"])
                + "\n  ]\n}\n"
            )
        for step, actual_set, managed in captured:
            assert actual_set == step["envelope"]["set"], step["name"]
            assert managed == step["managedTransmit"], step["name"]


async def test_public_get_freq_during_split_tx_does_not_poison_rx_fallback(tmp_path: Path) -> None:
    async with _session(tmp_path) as (radio, link, _session_, _base):
        link.ptt = True
        link.operating_hz = 29_100_000
        await _ingress(radio, link, ["FEFEE0941C0001FD"])
        assert await radio.get_freq() == 29_100_000
        link.ptt = False
        await _ingress(radio, link, ["FEFEE0941C0000FD"])
        link.answer_frequency = False
        assert await radio.get_freq() == 29_600_000


@asynccontextmanager
async def _frequency_radio(model: str) -> AsyncIterator[tuple[Ic7300SerialRadio, SplitLink]]:
    profile = resolve_radio_profile(model=model)
    link = SplitLink(model_id=profile.civ_addr)
    radio = Ic7300SerialRadio(
        device="/dev/rigplane-test-no-hardware",
        model=model,
        civ_link=link,
        audio_driver=_FakeUsbAudioDriver(),
        timeout=0.1,
        _enumerate_serial_ports_fn=lambda: [],
    )
    try:
        await radio.connect()
        assert radio.connected
        yield radio, link
    finally:
        await radio.disconnect()


@pytest.mark.parametrize("model", ["X6100", "X6200"])
@pytest.mark.parametrize("split_on", [False, True])
async def test_public_frequency_ingress_without_polled_vfo_readback_keeps_rx(
    model: str, split_on: bool
) -> None:
    async with _frequency_radio(model) as (radio, link):
        addr = radio.profile.civ_addr
        if split_on:
            await _ingress(radio, link, [build_civ_frame(0xE0, addr, 0x0F, data=b"\x01").hex()])
        await _ingress(
            radio, link, [build_civ_frame(0xE0, addr, 0x03, data=bcd_encode(29_600_000)).hex()]
        )
        assert radio.radio_state.main.freq == 29_600_000
        assert radio.state_cache.freq == 29_600_000
        assert radio.state_store.snapshot().field("receiver.0.active.freq_mode.freq_hz").value == 29_600_000


@pytest.mark.parametrize("command", [0x00, 0x03])
async def test_public_ic7300_frequency_before_split_observation_keeps_rx(command: int) -> None:
    async with _frequency_radio("IC-7300") as (radio, link):
        await _ingress(
            radio, link,
            [build_civ_frame(0x00 if command == 0x00 else 0xE0, 0x94, command, data=bcd_encode(29_600_000)).hex()],
        )
        assert radio.radio_state.main.freq == 29_600_000
        assert radio.state_cache.freq == 29_600_000
        assert radio.state_store.snapshot().field("receiver.0.active.freq_mode.freq_hz").value == 29_600_000


async def test_public_ic7300_stale_split_on_still_drops_keyup_frequency() -> None:
    async with _frequency_radio("IC-7300") as (radio, link):
        await _ingress(
            radio, link, ["FEFEE0940F00FD", "FEFEE094030000602900FD", "FEFEE0940F01FD"]
        )
        split = radio.state_store.snapshot().field("global.tx_state.split")
        assert split.max_age is not None
        StateFreshnessService(store=radio.state_store).tick(
            now=split.last_observed_monotonic + split.max_age + 1.0
        )
        split = radio.state_store.snapshot().field("global.tx_state.split")
        assert split.value is True and split.freshness is FreshnessState.STALE
        await _ingress(radio, link, ["FEFEE094000000102900FD"])
        assert radio.radio_state.main.freq == 29_600_000
        assert radio.state_cache.freq == 29_600_000
        assert radio.state_store.snapshot().field("receiver.0.active.freq_mode.freq_hz").value == 29_600_000
