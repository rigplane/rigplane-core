"""MOR-3754 split seam over a narrow dual-receiver CI-V peer.

IC-7610: slots A/B of the fake stand for physical MAIN/SUB; the 25/26
selectors are absolute (0=MAIN, 1=SUB) regardless of the operator selection,
and VFO selection (0x07) must not happen during split operation.

IC-9700: slots A/B are MAIN A/B; ``sub_tuple`` is an unrelated, immutable
SUB sentinel that split traffic must never touch. The relative 25/26
behaviour of the shared fake already matches its MAIN A/B topology.

Characterization seam only; not a full dual-receiver simulator.
"""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from dataclasses import asdict
from pathlib import Path

import pytest

from serial_stub import SplitIcomTransport
from rigplane.core.state_pipeline_contracts import (
    FieldPath,
    Observation,
    SourceMetadata,
)
from rigplane.profiles import get_radio_profile
from rigplane.radio import IcomRadio
from rigplane.rig_loader import RigLoadError, load_rig
from rigplane.rigctld.contract import RigctldConfig
from rigplane.rigctld.server import RigctldServer
from rigplane.types import Mode

MODELS = ["IC-7610", "IC-9700"]
SPLIT_FREQS = {"IC-7610": 14_080_100, "IC-9700": 145_801_000}
_SUB_SENTINEL = (145_800_000, Mode.FM, 3, 0)


class DualSplitIcomTransport(SplitIcomTransport):
    """Narrow peer: absolute MAIN/SUB selectors for IC-7610, sentinel SUB."""

    def __init__(self, *, selected: str = "A", direct_selector: bool) -> None:
        super().__init__(selected=selected)
        self.direct_selector = direct_selector
        self.sub_tuple = _SUB_SENTINEL
        self.truncate_mode_readback = False

    async def send_tracked(self, packet: bytes, *, is_current=None) -> None:
        start = packet.index(b"\xfe\xfe")
        command = packet[start + 4]
        if self.truncate_mode_readback and command == 0x26:
            from _helpers import wrap_civ_in_udp
            from rigplane.commands import (
                CONTROLLER_ADDR,
                build_civ_frame,
                parse_civ_frame,
            )

            request = parse_civ_frame(packet[start:])
            if len(request.data) == 1:
                self.requests.append((command, request.sub, request.data))
                reply = build_civ_frame(
                    CONTROLLER_ADDR,
                    request.to_addr,
                    command,
                    data=request.data + bytes([Mode.USB]),
                )
                self._responses.put_nowait(wrap_civ_in_udp(reply))
                return
        if self.direct_selector and command == 0x07:
            raise AssertionError("VFO selection must not happen in split tests")
        saved = self.selected
        if self.direct_selector and command in (0x25, 0x26):
            # Selector 0 is always physical MAIN (slot A), selector 1 always
            # physical SUB (slot B), so pin the base's relative base to A.
            self.selected = "A"
        try:
            await super().send_tracked(packet, is_current=is_current)
        finally:
            self.selected = saved


def _rf_state(server: RigctldServer, value: bool | None) -> None:
    server._state_store.apply_current(
        Observation(
            path=FieldPath.global_("tx_state", "ptt"),
            value=value,
            source=SourceMetadata(source="test", provider="tests"),
            timestamp_monotonic=asyncio.get_running_loop().time(),
            max_age=60,
        )
    )


@asynccontextmanager
async def _dual_peer(model: str, *, selected: str = "A"):
    transport = DualSplitIcomTransport(
        selected=selected, direct_selector=model == "IC-7610"
    )
    radio = IcomRadio("127.0.0.1", model=model, timeout=0.5)
    radio._civ_transport = transport
    radio._ctrl_transport = transport
    radio._connected = True
    radio._civ_min_interval = 0
    # Initial hardware state: MAIN A/B selection and independent SUB tuple.
    radio._radio_state.receiver("MAIN").active_slot = selected
    sub = radio._radio_state.receiver("SUB")
    sub.freq, sub.mode, sub.filter, sub.data_mode = (145_800_000, "FM", 3, 0)
    for name, value in zip(
        ("freq_hz", "mode", "filter_num", "data_mode"),
        (145_800_000, "FM", 3, 0),
    ):
        radio.state_store.apply_current(
            Observation(
                path=FieldPath.active("1", "freq_mode", name),
                value=value,
                source=SourceMetadata(source="test", provider="tests"),
                timestamp_monotonic=asyncio.get_running_loop().time(),
                max_age=60,
            )
        )
    config = RigctldConfig(host="127.0.0.1", port=0, cache_ttl=0, command_timeout=2)
    server = RigctldServer(radio, config, allow_not_ready=True)
    server._state_store = radio.state_store
    try:
        await server.start()
        _rf_state(server, False)
        from test_rigctld_wsjtx_split_mk2 import _client

        async with _client(server) as (reader, writer):
            yield radio, transport, server, reader, writer
    finally:
        await server.stop()
        await radio._civ_runtime.stop_pump()
        radio._connected = False


@pytest.mark.asyncio
@pytest.mark.parametrize("model", MODELS)
@pytest.mark.parametrize("main_selected", ["A", "B"])
async def test_split_targets_sub_and_preserves_rx(model, main_selected):
    from test_rigctld_wsjtx_split_mk2 import _client, _exchange

    freq = SPLIT_FREQS[model]
    async with _dual_peer(model, selected=main_selected) as (
        _radio,
        peer,
        server,
        reader,
        writer,
    ):
        sub_before = asdict(_radio._radio_state.receiver("SUB"))
        if model == "IC-7610":
            rx_before = peer.slots["A"]
        else:
            rx_before = peer.slots[main_selected]
        assert await _exchange(writer, reader, b"S 1 VFOB\n") == b"RPRT 0\n"
        assert await _exchange(writer, reader, b"I %d\n" % freq) == b"RPRT 0\n"
        assert await _exchange(writer, reader, b"X PKTUSB 2400\n") == b"RPRT 0\n"
        async with _client(server) as (other_reader, other_writer):
            assert await _exchange(other_writer, other_reader, b"i\n") == (
                b"%d\n" % freq
            )
            assert await _exchange(other_writer, other_reader, b"x\n") == (b"PKTUSB\n")
        assert await _exchange(writer, reader, b"X USB 2400\n") == b"RPRT 0\n"
        assert not any(cmd in (0x05, 0x06, 0x07, 0x1A) for cmd, _, _ in peer.requests)
        if model == "IC-7610":
            assert peer.slots["A"] == rx_before
            assert peer.slots["B"][0] == freq
            assert peer.slots["B"][3] == 0
        else:
            assert peer.slots[main_selected] == rx_before
            tx_slot = "B" if main_selected == "A" else "A"
            assert peer.slots[tx_slot][0] == freq
            assert peer.slots[tx_slot][3] == 0
        snapshot = _radio.state_store.snapshot()
        target_path = FieldPath.active if model == "IC-7610" else FieldPath.unselected
        target_receiver = "1" if model == "IC-7610" else "0"
        for name, expected in (("freq_hz", freq), ("mode", "USB"), ("data_mode", 0)):
            assert (
                snapshot.field(target_path(target_receiver, "freq_mode", name)).value
                == expected
            )
        if model == "IC-9700":
            for name, expected in zip(
                ("freq_hz", "mode", "filter_num", "data_mode"),
                (145_800_000, "FM", 3, 0),
            ):
                assert (
                    snapshot.field(FieldPath.active("1", "freq_mode", name)).value
                    == expected
                )
            assert asdict(_radio._radio_state.receiver("SUB")) == sub_before
            main = _radio._radio_state.receiver("MAIN")
            mirror = main.vfo_b if main_selected == "A" else main.vfo_a
            assert (mirror.freq_hz, mirror.mode, mirror.data_mode) == (freq, "USB", 0)
        else:
            sub = _radio._radio_state.receiver("SUB")
            assert (sub.freq, sub.mode, sub.data_mode) == (freq, "USB", 0)
        assert peer.sub_tuple == _SUB_SENTINEL
        assert peer.selected == main_selected


@pytest.mark.asyncio
@pytest.mark.parametrize("model", MODELS)
@pytest.mark.parametrize("kind", ["freq", "mode"])
async def test_split_nak_preserves_physical_tuples(model, kind):
    from test_rigctld_wsjtx_split_mk2 import _exchange

    freq = SPLIT_FREQS[model]
    async with _dual_peer(model) as (_radio, peer, server, reader, writer):
        before = dict(peer.slots)
        payload = (b"I %d\n" % freq) if kind == "freq" else b"X PKTUSB 2400\n"
        if kind == "freq":
            peer.reject_frequency = True
        else:
            peer.reject_mode = True
        assert await _exchange(writer, reader, b"S 1 VFOB\n") == b"RPRT 0\n"
        assert await _exchange(writer, reader, payload) == b"RPRT -9\n"
        assert dict(peer.slots) == before
        assert peer.sub_tuple == _SUB_SENTINEL
        assert peer.selected == "A"


@pytest.mark.asyncio
@pytest.mark.parametrize("data_mode", [2, 3])
async def test_ic7610_data_mode_clears_sub_data_above_1(data_mode):
    from test_rigctld_wsjtx_split_mk2 import _exchange

    async with _dual_peer("IC-7610") as (_radio, peer, server, reader, writer):
        peer.slots["B"] = (7_076_000, Mode.LSB, 3, data_mode)
        assert await _exchange(writer, reader, b"S 1 VFOB\n") == b"RPRT 0\n"
        assert await _exchange(writer, reader, b"X USB\n") == b"RPRT 0\n"
        assert peer.slots["B"][1] == Mode.USB
        assert peer.slots["B"][2] == 3
        assert peer.slots["B"][3] == 0


@pytest.mark.parametrize(
    ("model", "target"),
    [("IC-7610", "sub_receiver"), ("IC-9700", "main_unselected")],
)
def test_shipped_split_target_declares_writes(model, target):
    profile = get_radio_profile(model)
    assert profile.split_tx_target == target
    assert profile.supports_command("set_unselected_freq")
    assert profile.supports_command("set_unselected_mode")


@pytest.mark.parametrize("invalid", ['"typo"', "true", '["sub_receiver"]'])
def test_invalid_split_target_is_load_error(tmp_path, invalid):
    source = (Path(__file__).resolve().parent.parent / "rigs/ic7610.toml").read_text()
    path = tmp_path / "invalid.toml"
    path.write_text(
        source.replace(
            'split_tx_target = "sub_receiver"', f"split_tx_target = {invalid}"
        )
    )
    with pytest.raises(RigLoadError, match="split_tx_target"):
        load_rig(path)


@pytest.mark.asyncio
@pytest.mark.parametrize("model", MODELS)
async def test_missing_data_preservation_reply_refuses_without_set(model):
    from test_rigctld_wsjtx_split_mk2 import _exchange

    async with _dual_peer(model) as (_radio, peer, server, reader, writer):
        before = dict(peer.slots)
        peer.truncate_mode_readback = True
        assert await _exchange(writer, reader, b"S 1 VFOB\n") == b"RPRT 0\n"
        assert await _exchange(writer, reader, b"X RTTY 1800\n") == b"RPRT -11\n"
        assert peer.slots == before
        assert not any(
            command == 0x26 and len(data) > 1 for command, _, data in peer.requests
        )
