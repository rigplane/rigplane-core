"""Xiegu G7250 hardware scope over LAN (MOR-4436 scope PR).

The G7250 (firmware V1.0.1, wfview network mode) answers its CI-V scope
over LAN. The profile declares it as data (``rigs/g7250.toml`` ``[scope]``);
the shared scope mechanism gains two data-driven facts it needs: a 6-byte
span value and ``full_width`` span presets in the waveform span lookup.
Every wire byte below comes from the recorded bench radio
(``tests/fixtures/g7250/lan_scope_20261113.json``).
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import Generator
from pathlib import Path
from typing import Any

import aiohttp
import pytest
from test_radio import MockTransport
from test_web_server import _addr

from rigplane.commands import parse_civ_frame, parse_scope_span_response, scope_set_span
from rigplane.core.state_pipeline_contracts import FieldScope
from rigplane.profiles import RadioProfile, get_radio_profile
from rigplane.profiles.rig_loader import RigLoadError, load_rig
from rigplane.radio import IcomRadio
from rigplane.scope import ScopeAssembler
from rigplane.scope.models import FollowView, Subscription
from rigplane.testing.timing import await_until
from rigplane.types import bcd_encode
from rigplane.web.protocol import (
    decode_scope_frame_v2,
    decode_scope_granted,
    encode_scope_subscription,
)
from rigplane.web.server import WebConfig, WebServer
from rigplane_testkit.icom import MockIcomRadio

_FIXTURE = Path(__file__).parent / "fixtures" / "g7250" / "lan_scope_20261113.json"
_RIGS = Path(__file__).resolve().parents[1] / "rigs"
_G7250_CIV_ADDR = 0xA0
_CONTROLLER_ADDR = 0xE0


def _capture() -> dict[str, Any]:
    data: dict[str, Any] = json.loads(_FIXTURE.read_text())
    return data


def _frame(hex_bytes: str) -> bytes:
    return bytes.fromhex(hex_bytes)


def _scope_paths(profile: RadioProfile) -> set[str]:
    acquisition = profile.state_acquisition
    assert acquisition is not None
    return {
        str(capability.path)
        for capability in acquisition.capabilities
        if capability.path.scope is FieldScope.SCOPE_CONTROLS
    }


# ---------------------------------------------------------------------------
# Profile data through the real loader
# ---------------------------------------------------------------------------


def test_g7250_profile_declares_the_lan_scope() -> None:
    profile = get_radio_profile("G7250")
    assert "scope" in profile.capabilities
    assert profile.scope_span_presets_hz == (48000, 96000, 192000, 384000)
    assert (profile.scope_ref_min_db, profile.scope_ref_max_db) == (-20.0, 20.0)
    assert profile.scope_span_preset_semantics == "full_width"
    assert profile.scope_span_value_bytes == 6
    # full_width: each preset is the view width itself.
    assert profile.scope_span_view_widths_hz == profile.scope_span_presets_hz
    assert _scope_paths(profile) == {
        "scope_controls.global.display.mode",
        "scope_controls.global.display.span",
        "scope_controls.global.display.edge",
        "scope_controls.global.display.ref_db",
    }


@pytest.mark.parametrize("model", ["IC-705", "IC-7300", "IC-7610", "IC-9700"])
def test_icom_profiles_keep_the_five_byte_half_width_span(model: str) -> None:
    profile = get_radio_profile(model)
    assert profile.scope_span_value_bytes == 5
    assert profile.scope_span_preset_semantics == "half_width"


@pytest.mark.parametrize(
    ("value", "message"),
    [
        ("span_value_bytes = 4", "span_value_bytes"),
        ("span_value_bytes = true", "span_value_bytes"),
    ],
)
def test_loader_rejects_bad_span_value_width(
    tmp_path: Path, value: str, message: str
) -> None:
    text = (_RIGS / "g7250.toml").read_text()
    text = text.replace("span_value_bytes = 6", "", 1)
    text = text.replace("[scope]\n", f"[scope]\n{value}\n", 1)
    path = tmp_path / "g7250.toml"
    path.write_text(text)
    with pytest.raises(RigLoadError, match=message):
        load_rig(path)


# ---------------------------------------------------------------------------
# Recorded wire bytes through the shared scope codec
# ---------------------------------------------------------------------------


def test_recorded_g7250_span_reply_decodes() -> None:
    profile = get_radio_profile("G7250")
    reply = parse_civ_frame(_frame(_capture()["requests_replies"]["27 15 00"]))
    receiver, index = parse_scope_span_response(reply, profile.scope_span_presets_hz)
    assert receiver == 0
    assert profile.scope_span_presets_hz[index] == 192000


def test_g7250_span_set_matches_the_applied_bytes() -> None:
    profile = get_radio_profile("G7250")
    assert profile.command_map is not None
    frame = scope_set_span(
        profile.scope_span_presets_hz.index(192000),
        to_addr=profile.civ_addr,
        cmd_map=profile.command_map,
        presets=profile.scope_span_presets_hz,
        receiver=0,
        value_bytes=profile.scope_span_value_bytes,
    )
    assert frame == _frame(_capture()["span_set_sent"])


def test_recorded_g7250_wave_frame_assembles() -> None:
    raw = _frame(_capture()["wave_frame"])
    decoded = _capture()["wave_decoded"]
    frame = ScopeAssembler().feed(raw[7:-1], raw[6])
    assert frame is not None
    assert frame.mode == 0
    assert frame.start_freq_hz == decoded["start_hz"]
    assert frame.end_freq_hz == decoded["end_hz"]
    assert len(frame.pixels) == decoded["pixels"] == 475


# ---------------------------------------------------------------------------
# The waveform stream publishes the full-width span index
# ---------------------------------------------------------------------------


@pytest.fixture
def g7250_radio() -> Generator[IcomRadio, None, None]:
    transport = MockTransport()
    radio = IcomRadio("192.0.2.1", model="G7250")
    radio._civ_transport = transport
    radio._ctrl_transport = transport
    radio._connected = True
    yield radio
    radio._connected = False


async def test_recorded_wave_publishes_the_192000_span(g7250_radio: IcomRadio) -> None:
    """Center +/- 96000 on a full_width radio is the 192000 preset, not 96000."""
    frame = parse_civ_frame(_frame(_capture()["wave_frame"]))
    await g7250_radio._civ_runtime._route_civ_frame(
        frame, generation=g7250_radio._civ_epoch
    )
    field = g7250_radio._state_store.snapshot().field(
        "scope_controls.global.display.span"
    )
    presets = g7250_radio.profile.scope_span_presets_hz
    assert field.value == presets.index(192000)


# ---------------------------------------------------------------------------
# A span change through the real /api/v1/scope WebSocket (round-2 pre-review)
# ---------------------------------------------------------------------------


class _G7250LanScopeFake(MockIcomRadio):
    """The G7250's scope answers on the shared LAN simulator.

    Every wfview fact this regression needs is a base-simulator knob:
    the radio address, ping discovery, echo SET confirmation, the
    6-byte ``27 15`` span register, and ``27 00`` wave replay whose
    centre-mode span field tracks the wire value (half of it for a
    ``full_width`` radio). Two recorded registers the base does not
    model are answered here with the bench recording's reply shapes
    (``tests/fixtures/g7250/lan_scope_20261113.json``): mode ``27 14``
    (``27 14 00`` -> ``27 14 00 00``) and edge ``27 16``
    (``27 16 00`` -> ``27 16 00 01``), so the session's receiver
    capture reads real values instead of degrading.
    """

    def __init__(self) -> None:
        super().__init__(
            radio_addr=_G7250_CIV_ADDR,
            substream_discovery="ping",
            write_confirmation="echo",
            lan_civ_echo=False,
            ignored_civ_commands={(0x05, None)},
            span_preset_semantics="full_width",
            scope_span_value_bytes=6,
            scope_stream=True,
            capture_civ_frames=True,
        )
        self.model_id = b"\xa0"  # recorded 19 00 answer (MOR-4436)
        self.scope_mode = 0
        self.scope_edge = 1

    def _dispatch_scope(self, payload: bytes, from_addr: int) -> bytes | None:
        if not payload:
            return super()._dispatch_scope(payload, from_addr)
        sub, rest = payload[0], payload[1:]
        if sub in (0x14, 0x16) and rest[:1] == b"\x00" and len(rest) in (1, 2):
            if len(rest) == 1:  # GET: the recording replies sub + receiver + value
                value = self.scope_mode if sub == 0x14 else self.scope_edge
                return self._civ_frame(
                    from_addr,
                    self._radio_addr,
                    0x27,
                    data=payload[:2] + bytes([value]),
                )
            if sub == 0x14:  # SET: kept, and confirmed like every write
                self.scope_mode = rest[1]
            else:
                self.scope_edge = rest[1]
            return self._civ_ack(from_addr, self._radio_addr)
        return super()._dispatch_scope(payload, from_addr)


@pytest.mark.realtime(reason="real LAN simulator, radio and scope websocket")
@pytest.mark.timeout(60)
async def test_scope_ws_span_request_writes_six_byte_set_and_streams_full_width() -> (
    None
):
    """A span change through the real ``/api/v1/scope`` WebSocket reaches
    the G7250 as a six-byte ``27 15`` SET, and the frames come back
    covering the requested full width.

    Round-2 pre-review P1: the production handoff
    ``value_bytes=self._profile.scope_span_value_bytes``
    (``runtime/_scope_runtime.py``) had no coverage — the encoder test
    passed the width directly and the waveform test drove private RX
    routing, so dropping the argument left them green while reverting
    G7250 span writes to five bytes. Here the radio boots on the
    recorded 192000 span and the subscription asks for 96000, so a
    dropped argument fails twice over: the fake (a six-byte radio)
    refuses the five-byte SET, so neither the six-byte frame nor a
    96000-wide streamed window ever appears.
    """
    profile = get_radio_profile("G7250")
    six_byte_set = (
        b"\xfe\xfe"
        + bytes((profile.civ_addr, _CONTROLLER_ADDR))
        + b"\x27\x15\x00"
        + bcd_encode(96_000, width=6)
        + b"\xfd"
    )
    sim = _G7250LanScopeFake()
    await sim.start()
    radio = IcomRadio(
        "127.0.0.1",
        port=sim.control_port,
        username="testuser",
        password="testpass",
        model="G7250",
    )
    server = WebServer(radio, WebConfig(host="127.0.0.1", port=0))
    try:
        await radio.connect()
        radio._radio_state.main.freq = 14_074_000  # the simulator's dial
        await server.start()
        host, port = _addr(server)
        async with aiohttp.ClientSession() as client:
            async with client.ws_connect(f"http://{host}:{port}/api/v1/scope") as ws:
                await ws.send_str(
                    encode_scope_subscription(
                        Subscription("radio", 0, FollowView("vfo", 96_000), 475, 20)
                    )
                )
                message = await ws.receive(timeout=10)
                assert message.type is aiohttp.WSMsgType.TEXT, message.type
                granted = decode_scope_granted(message.data)
                assert granted.view.end_hz - granted.view.start_hz == 96_000
                assert await await_until(
                    lambda: six_byte_set in sim.civ_frames_received,
                    timeout=10,
                    description="the six-byte span SET reaches the radio",
                )
                assert sim.scope_span_wire_hz == 96_000
                async with asyncio.timeout(10):
                    while True:  # skip pre-write frames; the radio must move
                        message = await ws.receive(timeout=10)
                        if message.type is aiohttp.WSMsgType.TEXT:
                            assert '"type":"error"' not in message.data
                            continue
                        assert message.type is aiohttp.WSMsgType.BINARY
                        frame = decode_scope_frame_v2(message.data)
                        width = frame.coverage_end_hz - frame.coverage_start_hz
                        if width == 96_000:
                            assert frame.pixels
                            break
    finally:
        await server.stop()
        await radio.disconnect()
        await sim.stop()
