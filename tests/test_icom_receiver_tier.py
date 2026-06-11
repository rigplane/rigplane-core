"""Receiver-tier protocol implementations on IcomRadio (issue #1170).

Wave 4-A: ``ReceiverBankCapable`` and ``VfoSlotCapable`` on the Icom backend.

Coverage matrix:
- Protocol satisfaction (``isinstance``) on all four supported Icom rigs.
- Wire-frame golden bytes for ``select_receiver`` (dual-RX) and
  ``set_vfo_slot`` (every rig).
- ValueError on out-of-range ``receiver`` for single-RX rigs (IC-7300, IC-705).
- ValueError on unknown receiver names / invalid slot strings.
- Single-RX ``select_receiver(0)`` is a no-op (no wire frame emitted).
- IC-7610 SUB ``set_vfo_slot`` uses VFO-switch fallback (SEL_SUB → slot →
  restore SEL_MAIN).
- ``get_vfo_slot`` / ``get_active_receiver`` read cached state.

Hardware-smoke pending — these tests verify wire-bytes only.
"""

from __future__ import annotations

import pytest

from rigplane.radio import IcomRadio
from rigplane.radio_protocol import ReceiverBankCapable, VfoSlotCapable

from test_radio import MockTransport, _wrap_civ_in_udp


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def mock_transport() -> MockTransport:
    return MockTransport()


def _make_radio(model: str, mock_transport: MockTransport) -> IcomRadio:
    """Build a connected IcomRadio for ``model`` wired to ``mock_transport``."""
    r = IcomRadio("192.168.1.100", model=model, timeout=0.05)
    r._civ_transport = mock_transport
    r._ctrl_transport = mock_transport
    r._connected = True
    return r


@pytest.fixture
def ic7610(mock_transport: MockTransport):
    r = _make_radio("IC-7610", mock_transport)
    yield r
    r._connected = False


@pytest.fixture
def ic9700(mock_transport: MockTransport):
    r = _make_radio("IC-9700", mock_transport)
    yield r
    r._connected = False


@pytest.fixture
def ic7300(mock_transport: MockTransport):
    r = _make_radio("IC-7300", mock_transport)
    yield r
    r._connected = False


@pytest.fixture
def ic705(mock_transport: MockTransport):
    r = _make_radio("IC-705", mock_transport)
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


# IC-7610 / IC-9700 receiver-select wire bytes.
SEL_MAIN_7610 = b"\xfe\xfe\x98\xe0\x07\xd0\xfd"
SEL_SUB_7610 = b"\xfe\xfe\x98\xe0\x07\xd1\xfd"
SEL_MAIN_9700 = b"\xfe\xfe\xa2\xe0\x07\xd0\xfd"
SEL_SUB_9700 = b"\xfe\xfe\xa2\xe0\x07\xd1\xfd"

# Per-rig VFO-A and VFO-B slot-select wire bytes (CI-V 0x07 0x00 / 0x07 0x01).
SLOT_A_7610 = b"\xfe\xfe\x98\xe0\x07\x00\xfd"
SLOT_B_7610 = b"\xfe\xfe\x98\xe0\x07\x01\xfd"
SLOT_A_9700 = b"\xfe\xfe\xa2\xe0\x07\x00\xfd"
SLOT_B_9700 = b"\xfe\xfe\xa2\xe0\x07\x01\xfd"
SLOT_A_7300 = b"\xfe\xfe\x94\xe0\x07\x00\xfd"
SLOT_B_7300 = b"\xfe\xfe\x94\xe0\x07\x01\xfd"
SLOT_A_705 = b"\xfe\xfe\xa4\xe0\x07\x00\xfd"
SLOT_B_705 = b"\xfe\xfe\xa4\xe0\x07\x01\xfd"

# CI-V ACK frames per rig (FB) wrapped in UDP for MockTransport.receive_packet.
ACK_IC7610 = _wrap_civ_in_udp(b"\xfe\xfe\xe0\x98\xfb\xfd")
ACK_IC9700 = _wrap_civ_in_udp(b"\xfe\xfe\xe0\xa2\xfb\xfd")


# ---------------------------------------------------------------------------
# Protocol satisfaction (runtime_checkable isinstance)
# ---------------------------------------------------------------------------


class TestProtocolSatisfaction:
    """``IcomRadio`` exposes the receiver-tier protocols on every Icom rig."""

    def test_ic7610_satisfies_receiver_bank_capable(self, ic7610: IcomRadio) -> None:
        assert isinstance(ic7610, ReceiverBankCapable)

    def test_ic7610_satisfies_vfo_slot_capable(self, ic7610: IcomRadio) -> None:
        assert isinstance(ic7610, VfoSlotCapable)

    def test_ic9700_satisfies_both_protocols(self, ic9700: IcomRadio) -> None:
        assert isinstance(ic9700, ReceiverBankCapable)
        assert isinstance(ic9700, VfoSlotCapable)

    def test_ic7300_satisfies_both_protocols(self, ic7300: IcomRadio) -> None:
        assert isinstance(ic7300, ReceiverBankCapable)
        assert isinstance(ic7300, VfoSlotCapable)

    def test_ic705_satisfies_both_protocols(self, ic705: IcomRadio) -> None:
        assert isinstance(ic705, ReceiverBankCapable)
        assert isinstance(ic705, VfoSlotCapable)


# ---------------------------------------------------------------------------
# receiver_count property
# ---------------------------------------------------------------------------


class TestReceiverCount:
    def test_ic7610_receiver_count_2(self, ic7610: IcomRadio) -> None:
        assert ic7610.receiver_count == 2

    def test_ic9700_receiver_count_2(self, ic9700: IcomRadio) -> None:
        assert ic9700.receiver_count == 2

    def test_ic7300_receiver_count_1(self, ic7300: IcomRadio) -> None:
        assert ic7300.receiver_count == 1

    def test_ic705_receiver_count_1(self, ic705: IcomRadio) -> None:
        assert ic705.receiver_count == 1


# ---------------------------------------------------------------------------
# select_receiver
# ---------------------------------------------------------------------------


class TestSelectReceiver:
    @pytest.mark.asyncio
    async def test_ic7610_main_emits_0xD0(
        self, ic7610: IcomRadio, mock_transport: MockTransport
    ) -> None:
        mock_transport.queue_response(ACK_IC7610)
        await ic7610.select_receiver(0)
        frames = _civ_bytes(mock_transport.sent_packets)
        assert SEL_MAIN_7610 in frames
        assert ic7610._radio_state.active == "MAIN"

    @pytest.mark.asyncio
    async def test_ic7610_sub_emits_0xD1(
        self, ic7610: IcomRadio, mock_transport: MockTransport
    ) -> None:
        mock_transport.queue_response(ACK_IC7610)
        await ic7610.select_receiver(1)
        frames = _civ_bytes(mock_transport.sent_packets)
        assert SEL_SUB_7610 in frames
        assert ic7610._radio_state.active == "SUB"

    @pytest.mark.asyncio
    async def test_ic7610_by_name_main(
        self, ic7610: IcomRadio, mock_transport: MockTransport
    ) -> None:
        mock_transport.queue_response(ACK_IC7610)
        await ic7610.select_receiver("main")
        frames = _civ_bytes(mock_transport.sent_packets)
        assert SEL_MAIN_7610 in frames

    @pytest.mark.asyncio
    async def test_ic7610_by_name_sub_case_insensitive(
        self, ic7610: IcomRadio, mock_transport: MockTransport
    ) -> None:
        mock_transport.queue_response(ACK_IC7610)
        await ic7610.select_receiver("SUB")
        frames = _civ_bytes(mock_transport.sent_packets)
        assert SEL_SUB_7610 in frames

    @pytest.mark.asyncio
    async def test_ic9700_sub_emits_0xD1(
        self, ic9700: IcomRadio, mock_transport: MockTransport
    ) -> None:
        """IC-9700 uses the same 0x07 0xD0/0xD1 MAIN/SUB scheme as IC-7610."""
        mock_transport.queue_response(ACK_IC9700)
        await ic9700.select_receiver(1)
        frames = _civ_bytes(mock_transport.sent_packets)
        assert SEL_SUB_9700 in frames

    @pytest.mark.asyncio
    async def test_ic7610_unknown_name_raises_value_error(
        self, ic7610: IcomRadio, mock_transport: MockTransport
    ) -> None:
        with pytest.raises(ValueError, match="unknown receiver name"):
            await ic7610.select_receiver("tertiary")
        # No wire frame sent on validation failure.
        assert _civ_bytes(mock_transport.sent_packets) == []

    @pytest.mark.asyncio
    async def test_ic7610_out_of_range_raises_value_error(
        self, ic7610: IcomRadio, mock_transport: MockTransport
    ) -> None:
        with pytest.raises(ValueError, match="out of range"):
            await ic7610.select_receiver(2)

    @pytest.mark.asyncio
    async def test_ic7300_zero_is_noop(
        self, ic7300: IcomRadio, mock_transport: MockTransport
    ) -> None:
        await ic7300.select_receiver(0)
        # Single-RX: no wire frame emitted.
        assert _civ_bytes(mock_transport.sent_packets) == []

    @pytest.mark.asyncio
    async def test_ic7300_receiver_1_raises_value_error(
        self, ic7300: IcomRadio, mock_transport: MockTransport
    ) -> None:
        with pytest.raises(ValueError, match="out of range"):
            await ic7300.select_receiver(1)
        assert _civ_bytes(mock_transport.sent_packets) == []

    @pytest.mark.asyncio
    async def test_ic705_receiver_1_raises_value_error(self, ic705: IcomRadio) -> None:
        with pytest.raises(ValueError, match="out of range"):
            await ic705.select_receiver(1)


# ---------------------------------------------------------------------------
# get_active_receiver
# ---------------------------------------------------------------------------


class TestGetActiveReceiver:
    @pytest.mark.asyncio
    async def test_ic7610_default_main_is_zero(self, ic7610: IcomRadio) -> None:
        assert await ic7610.get_active_receiver() == 0

    @pytest.mark.asyncio
    async def test_ic7610_after_select_sub_returns_one(
        self, ic7610: IcomRadio, mock_transport: MockTransport
    ) -> None:
        mock_transport.queue_response(ACK_IC7610)
        await ic7610.select_receiver(1)
        assert await ic7610.get_active_receiver() == 1

    @pytest.mark.asyncio
    async def test_ic7300_always_zero(self, ic7300: IcomRadio) -> None:
        assert await ic7300.get_active_receiver() == 0

    @pytest.mark.asyncio
    async def test_ic705_always_zero(self, ic705: IcomRadio) -> None:
        assert await ic705.get_active_receiver() == 0


# ---------------------------------------------------------------------------
# set_vfo_slot — wire-frame golden bytes
# ---------------------------------------------------------------------------


class TestSetVfoSlot:
    @pytest.mark.asyncio
    async def test_ic7610_main_slot_a_emits_0x07_0x00(
        self, ic7610: IcomRadio, mock_transport: MockTransport
    ) -> None:
        # Dual-RX path: VFO-switch fallback. _radio_state.active defaults to
        # MAIN, so when receiver=0 the fallback detects current==target and
        # does NOT switch, but the surrounding pattern still emits the slot
        # frame directly. No ACK queueing needed.
        await ic7610.set_vfo_slot("A", receiver=0)
        frames = _civ_bytes(mock_transport.sent_packets)
        assert SLOT_A_7610 in frames

    @pytest.mark.asyncio
    async def test_ic7610_main_slot_b_emits_0x07_0x01(
        self, ic7610: IcomRadio, mock_transport: MockTransport
    ) -> None:
        await ic7610.set_vfo_slot("B", receiver=0)
        frames = _civ_bytes(mock_transport.sent_packets)
        assert SLOT_B_7610 in frames

    @pytest.mark.asyncio
    async def test_ic7610_sub_uses_select_restore_pattern(
        self, ic7610: IcomRadio, mock_transport: MockTransport
    ) -> None:
        # Dual-RX SUB path: select SUB → emit slot → restore MAIN.
        # Release each ACK only after its own send (1=select SUB, 2=slot
        # fire-and-forget, 3=restore MAIN). Pre-queueing ACKs upfront lets the
        # rx pump race them into the wrong consumer (fire-and-forget ACK sink
        # vs restore waiter); the race winner differs between 3.11 and 3.12+
        # because pre-3.12 asyncio.wait_for wraps the awaitable in an extra
        # Task (gh-96764), changing scheduling order.
        mock_transport.queue_response_on_send(1, ACK_IC7610)
        mock_transport.queue_response_on_send(2, ACK_IC7610)
        mock_transport.queue_response_on_send(3, ACK_IC7610)
        await ic7610.set_vfo_slot("B", receiver=1)
        frames = _civ_bytes(mock_transport.sent_packets)
        sel_idx = next((i for i, f in enumerate(frames) if f == SEL_SUB_7610), None)
        slot_idx = next((i for i, f in enumerate(frames) if f == SLOT_B_7610), None)
        assert sel_idx is not None, "expected 0x07 0xD1 (select SUB)"
        assert slot_idx is not None and slot_idx > sel_idx
        restore_idx = next(
            (i for i, f in enumerate(frames) if f == SEL_MAIN_7610 and i > slot_idx),
            None,
        )
        assert restore_idx is not None, (
            "expected 0x07 0xD0 (restore MAIN) after slot frame"
        )

    @pytest.mark.asyncio
    async def test_ic9700_main_slot_a_emits_0x07_0x00(
        self, ic9700: IcomRadio, mock_transport: MockTransport
    ) -> None:
        # IC-9700 also dual-RX with same wire scheme; receiver=0 (MAIN) is
        # current, so no select/restore round-trip.
        await ic9700.set_vfo_slot("A", receiver=0)
        frames = _civ_bytes(mock_transport.sent_packets)
        assert SLOT_A_9700 in frames

    @pytest.mark.asyncio
    async def test_ic9700_sub_uses_select_restore_pattern(
        self, ic9700: IcomRadio, mock_transport: MockTransport
    ) -> None:
        # Per-send ACK release — see test_ic7610_sub_uses_select_restore_
        # pattern for the 3.11-vs-3.12+ scheduling rationale.
        mock_transport.queue_response_on_send(1, ACK_IC9700)
        mock_transport.queue_response_on_send(2, ACK_IC9700)
        mock_transport.queue_response_on_send(3, ACK_IC9700)
        await ic9700.set_vfo_slot("A", receiver=1)
        frames = _civ_bytes(mock_transport.sent_packets)
        assert SEL_SUB_9700 in frames
        assert SLOT_A_9700 in frames

    @pytest.mark.asyncio
    async def test_ic7300_slot_a_direct(
        self, ic7300: IcomRadio, mock_transport: MockTransport
    ) -> None:
        await ic7300.set_vfo_slot("A", receiver=0)
        frames = _civ_bytes(mock_transport.sent_packets)
        assert SLOT_A_7300 in frames
        # Single-RX must not emit any receiver-select wrapping.
        assert all(b"\xd0" not in f and b"\xd1" not in f for f in frames)

    @pytest.mark.asyncio
    async def test_ic7300_slot_b_direct(
        self, ic7300: IcomRadio, mock_transport: MockTransport
    ) -> None:
        await ic7300.set_vfo_slot("B", receiver=0)
        frames = _civ_bytes(mock_transport.sent_packets)
        assert SLOT_B_7300 in frames

    @pytest.mark.asyncio
    async def test_ic705_slot_b_direct(
        self, ic705: IcomRadio, mock_transport: MockTransport
    ) -> None:
        await ic705.set_vfo_slot("B", receiver=0)
        frames = _civ_bytes(mock_transport.sent_packets)
        assert SLOT_B_705 in frames

    @pytest.mark.asyncio
    async def test_ic705_slot_a_direct(
        self, ic705: IcomRadio, mock_transport: MockTransport
    ) -> None:
        await ic705.set_vfo_slot("a", receiver=0)
        frames = _civ_bytes(mock_transport.sent_packets)
        # Case-insensitive — lowercase "a" still routes to 0x00.
        assert SLOT_A_705 in frames

    @pytest.mark.asyncio
    async def test_ic7300_receiver_1_raises_value_error(
        self, ic7300: IcomRadio
    ) -> None:
        with pytest.raises(ValueError, match="out of range"):
            await ic7300.set_vfo_slot("A", receiver=1)

    @pytest.mark.asyncio
    async def test_ic705_receiver_1_raises_value_error(self, ic705: IcomRadio) -> None:
        with pytest.raises(ValueError, match="out of range"):
            await ic705.set_vfo_slot("B", receiver=1)

    @pytest.mark.asyncio
    async def test_invalid_slot_raises_value_error(self, ic7300: IcomRadio) -> None:
        with pytest.raises(ValueError, match="slot must be"):
            await ic7300.set_vfo_slot("C", receiver=0)


# ---------------------------------------------------------------------------
# get_vfo_slot — reads cached state
# ---------------------------------------------------------------------------


class TestGetVfoSlot:
    @pytest.mark.asyncio
    async def test_ic7610_main_default_a(self, ic7610: IcomRadio) -> None:
        assert await ic7610.get_vfo_slot(receiver=0) == "A"

    @pytest.mark.asyncio
    async def test_ic7610_sub_default_a(self, ic7610: IcomRadio) -> None:
        assert await ic7610.get_vfo_slot(receiver=1) == "A"

    @pytest.mark.asyncio
    async def test_ic7610_after_set_b_returns_b(
        self, ic7610: IcomRadio, mock_transport: MockTransport
    ) -> None:
        await ic7610.set_vfo_slot("B", receiver=0)
        assert await ic7610.get_vfo_slot(receiver=0) == "B"

    @pytest.mark.asyncio
    async def test_ic7610_per_receiver_independent(
        self, ic7610: IcomRadio, mock_transport: MockTransport
    ) -> None:
        # Set MAIN.B; SUB stays on A.
        await ic7610.set_vfo_slot("B", receiver=0)
        assert await ic7610.get_vfo_slot(receiver=0) == "B"
        assert await ic7610.get_vfo_slot(receiver=1) == "A"

    @pytest.mark.asyncio
    async def test_ic7300_after_set_b_returns_b(
        self, ic7300: IcomRadio, mock_transport: MockTransport
    ) -> None:
        await ic7300.set_vfo_slot("B", receiver=0)
        assert await ic7300.get_vfo_slot(receiver=0) == "B"

    @pytest.mark.asyncio
    async def test_ic7300_receiver_1_raises_value_error(
        self, ic7300: IcomRadio
    ) -> None:
        with pytest.raises(ValueError, match="out of range"):
            await ic7300.get_vfo_slot(receiver=1)
