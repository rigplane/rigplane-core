"""Per-receiver + VFO round-trip integration tests (issue #726).

Verifies that set-freq / set-mode / VFO-swap operations produce the correct
CI-V byte sequence across dual-RX (IC-7610) and single-RX (IC-7300) profiles.

Matrix coverage:
- IC-7610 MAIN/SUB set_freq / set_mode via fallback receiver-select (cmd29 not
  supported for 0x05/0x06 per the protocol invariant in CLAUDE.md).
- IC-7300 single-receiver set_freq / set_mode (direct, no select).
- swap_main_sub (dual-RX only) and swap_vfo_ab (1-Rx only) wire bytes.
- Error paths: swap_vfo_ab on dual-RX and swap_main_sub on 1-Rx raise
  CommandError (from PR #746).

Uses MockTransport from tests/test_radio.py — same pattern as
tests/test_selected_freq_mode.py.
"""

from __future__ import annotations

import pytest

from rigplane.exceptions import CommandError
from rigplane.radio import IcomRadio
from rigplane.types import Mode, bcd_encode

from test_radio import MockTransport, _wrap_civ_in_udp
from _helpers import observe_selected_receiver


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def mock_transport() -> MockTransport:
    return MockTransport()


@pytest.fixture
def ic7610(mock_transport: MockTransport):
    r = IcomRadio("192.168.1.100", model="IC-7610", timeout=0.05)
    r._civ_transport = mock_transport
    r._ctrl_transport = mock_transport
    r._connected = True
    observe_selected_receiver(r, 0)
    yield r
    r._connected = False  # reset _conn_state so __del__ stays quiet


@pytest.fixture
def ic7300(mock_transport: MockTransport):
    r = IcomRadio("192.168.1.101", model="IC-7300", timeout=0.05)
    r._civ_transport = mock_transport
    r._ctrl_transport = mock_transport
    r._connected = True
    observe_selected_receiver(r, 0)
    yield r
    r._connected = False


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _civ_bytes(packets: list[bytes]) -> list[bytes]:
    """Extract CI-V frames (FE FE ... FD) from UDP-wrapped or raw packets."""
    out: list[bytes] = []
    for pkt in packets:
        i = pkt.find(b"\xfe\xfe")
        if i < 0:
            continue
        j = pkt.find(b"\xfd", i)
        if j < 0:
            continue
        out.append(pkt[i : j + 1])
    return out


# IC-7610 CI-V address is 0x98; our local address defaults to 0xE0.
SEL_MAIN = b"\xfe\xfe\x98\xe0\x07\xd0\xfd"
SEL_SUB = b"\xfe\xfe\x98\xe0\x07\xd1\xfd"
SWAP_B0 = b"\x07\xb0"

# CI-V ACK frame (FB) wrapped in UDP for MockTransport.receive_packet.
ACK_IC7610 = _wrap_civ_in_udp(b"\xfe\xfe\xe0\x98\xfb\xfd")
ACK_IC7300 = _wrap_civ_in_udp(b"\xfe\xfe\xe0\x94\xfb\xfd")


# ---------------------------------------------------------------------------
# set_freq: per-receiver routing
# ---------------------------------------------------------------------------


class TestPerReceiverFreq:
    @pytest.mark.asyncio
    async def test_ic7610_set_freq_main_no_select_prefix(
        self, ic7610: IcomRadio, mock_transport: MockTransport
    ) -> None:
        mock_transport.queue_response_on_send(1, ACK_IC7610)
        await ic7610.set_freq(14_074_000, receiver=0)
        frames = _civ_bytes(mock_transport.sent_packets)
        freq_frame = next((f for f in frames if f[4:5] == b"\x05"), None)
        assert freq_frame is not None, (
            f"expected a 0x05 set-freq frame; got {[f.hex() for f in frames]}"
        )
        assert freq_frame[5:-1] == bcd_encode(14_074_000), (
            f"BCD payload mismatch: got {freq_frame[5:-1].hex()}"
        )
        assert not any(f == SEL_MAIN or f == SEL_SUB for f in frames), (
            "no 0x07 0xD0/D1 receiver-select expected for MAIN path"
        )

    @pytest.mark.asyncio
    async def test_ic7610_set_freq_sub_uses_selector_01(
        self, ic7610: IcomRadio, mock_transport: MockTransport
    ) -> None:
        # MOR-3591: selector 01 is the physical SUB (profile data): 0x25 0x01
        # SET, then its readback; the selection is never switched. Release
        # each answer only after its own send.
        mock_transport.queue_response_on_send(1, ACK_IC7610)
        mock_transport.queue_response_on_send(
            2,
            _wrap_civ_in_udp(
                b"\xfe\xfe\xe0\x98\x25\x01" + bcd_encode(7_074_000) + b"\xfd"
            ),
        )
        await ic7610.set_freq(7_074_000, receiver=1)
        frames = _civ_bytes(mock_transport.sent_packets)
        assert [f[4:6] for f in frames] == [b"\x25\x01", b"\x25\x01"]
        assert frames[0][6:-1] == bcd_encode(7_074_000)
        assert not any(f == SEL_MAIN or f == SEL_SUB for f in frames)

    @pytest.mark.asyncio
    async def test_ic7300_set_freq_direct(
        self, ic7300: IcomRadio, mock_transport: MockTransport
    ) -> None:
        mock_transport.queue_response_on_send(1, ACK_IC7300)
        await ic7300.set_freq(14_074_000, receiver=0)
        frames = _civ_bytes(mock_transport.sent_packets)
        freq_frame = next((f for f in frames if f[4:5] == b"\x05"), None)
        assert freq_frame is not None
        assert freq_frame[5:-1] == bcd_encode(14_074_000)
        assert not any(f == SEL_MAIN or f == SEL_SUB for f in frames), (
            "1-Rx profile must not send receiver-select"
        )

    @pytest.mark.asyncio
    async def test_ic7300_set_freq_receiver_1_raises(self, ic7300: IcomRadio) -> None:
        with pytest.raises((ValueError, CommandError, NotImplementedError)):
            await ic7300.set_freq(7_074_000, receiver=1)


# ---------------------------------------------------------------------------
# set_mode: per-receiver routing
# ---------------------------------------------------------------------------


class TestPerReceiverMode:
    @pytest.mark.asyncio
    async def test_ic7610_set_mode_main_no_select(
        self, ic7610: IcomRadio, mock_transport: MockTransport
    ) -> None:
        await ic7610.set_mode(Mode.USB, receiver=0)
        frames = _civ_bytes(mock_transport.sent_packets)
        assert any(f[4:5] == b"\x06" for f in frames)
        assert not any(f == SEL_MAIN or f == SEL_SUB for f in frames)

    @pytest.mark.asyncio
    async def test_ic7610_set_mode_sub_uses_selector_01(
        self, ic7610: IcomRadio, mock_transport: MockTransport
    ) -> None:
        # Preservation read (0x25/0x26 01), SET 0x26 01, readback -- no select.
        freq = _wrap_civ_in_udp(
            b"\xfe\xfe\xe0\x98\x25\x01" + bcd_encode(7_074_000) + b"\xfd"
        )
        usb = _wrap_civ_in_udp(b"\xfe\xfe\xe0\x98\x26\x01\x01\x00\x01\xfd")
        lsb = _wrap_civ_in_udp(b"\xfe\xfe\xe0\x98\x26\x01\x00\x00\x01\xfd")
        for index, answer in enumerate((freq, usb, ACK_IC7610, freq, lsb), start=1):
            mock_transport.queue_response_on_send(index, answer)
        await ic7610.set_mode(Mode.LSB, receiver=1)
        frames = _civ_bytes(mock_transport.sent_packets)
        assert frames[2][4:-1] == b"\x26\x01\x00\x00\x01"  # LSB, DATA off, FIL1
        assert all(f[4:6] in (b"\x25\x01", b"\x26\x01") for f in frames)
        assert not any(f == SEL_MAIN or f == SEL_SUB for f in frames)

    @pytest.mark.asyncio
    async def test_ic7300_set_mode_direct(
        self, ic7300: IcomRadio, mock_transport: MockTransport
    ) -> None:
        await ic7300.set_mode(Mode.CW, receiver=0)
        frames = _civ_bytes(mock_transport.sent_packets)
        assert any(f[4:5] == b"\x06" for f in frames)
        assert not any(f == SEL_MAIN or f == SEL_SUB for f in frames)


# ---------------------------------------------------------------------------
# VFO swap / equalize: profile-aware error paths
# ---------------------------------------------------------------------------


class TestVfoSwap:
    @pytest.mark.asyncio
    async def test_ic7610_swap_main_sub_sends_0xB0(
        self, ic7610: IcomRadio, mock_transport: MockTransport
    ) -> None:
        await ic7610.swap_main_sub()
        frames = _civ_bytes(mock_transport.sent_packets)
        assert any(SWAP_B0 in f for f in frames), (
            f"expected 0x07 0xB0 in some frame; got {[f.hex() for f in frames]}"
        )

    @pytest.mark.asyncio
    async def test_ic7610_swap_vfo_ab_raises_command_error(
        self, ic7610: IcomRadio
    ) -> None:
        """Per PR #746: swap_vfo_ab must raise on dual-RX profiles that don't
        declare swap_ab_code (IC-7610 declares only swap_main_sub_code)."""
        with pytest.raises(CommandError):
            await ic7610.swap_vfo_ab(receiver=0)

    @pytest.mark.asyncio
    async def test_ic7300_swap_vfo_ab_sends_0xB0(
        self, ic7300: IcomRadio, mock_transport: MockTransport
    ) -> None:
        await ic7300.swap_vfo_ab(receiver=0)
        frames = _civ_bytes(mock_transport.sent_packets)
        assert any(SWAP_B0 in f for f in frames)

    @pytest.mark.asyncio
    async def test_ic7300_swap_main_sub_raises_command_error(
        self, ic7300: IcomRadio
    ) -> None:
        """Per PR #746: swap_main_sub must raise on 1-Rx profiles."""
        with pytest.raises(CommandError):
            await ic7300.swap_main_sub()
