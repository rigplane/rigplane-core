"""MOR-3591: Core never switches the selected receiver to reach another one.

Owner rule (2026-10-29): on a radio without a per-receiver addressing
command (no cmd29 route for that command, no profile-declared direct
selector) a receiver-scoped write or read goes only to the receiver the
operator has selected; any other receiver -- and either receiver while the
selection is unknown -- is refused with ``ReceiverNotSelectedError``
(``reason_code == "receiver_not_selected"``).

The backbone is one wire-level test over every receiver-scoped operation
that used to switch the selection, on three profiles (IC-9700: no cmd29,
selector 01 is not SUB; IC-7610: cmd29 routes plus selector 01 = physical
SUB; IC-705: single receiver), asserting on the captured CI-V frames that no
``0x07 0xD0/0xD1`` selection frame is ever sent.
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

import pytest

from rigplane.commands import CONTROLLER_ADDR, build_civ_frame, parse_civ_frame
from rigplane.core.exceptions import (
    CommandError,
    ReceiverNotSelectedError,
    TimeoutError,
)
from rigplane.core.state_pipeline_contracts import FieldPath
from rigplane.core.types import CivFrame, bcd_encode
from rigplane.radio import IcomRadio
from rigplane.runtime.callable_support import unselected_selector_is_sub

from test_radio import MockTransport

_SELECT_CODES = frozenset({0xD0, 0xD1})
_FREQ = 145_500_000
_ACK = 0xFB

# Reads whose reply carries a value; anything else is answered with an ACK.
_READ_REPLY: dict[tuple[int, ...], bytes] = {
    (0x03,): bcd_encode(_FREQ),
    (0x04,): bytes([0x01, 0x01]),
    # The request already carries selector 01; the reply echoes it.
    (0x25, 0x01): bcd_encode(_FREQ),
    (0x26, 0x01): bytes([0x01, 0x00, 0x01]),  # USB, DATA off, FIL1
    (0x1A, 0x03): bytes([0x10]),
    (0x16, 0x42): bytes([0x01]),
    (0x16, 0x43): bytes([0x01]),
    (0x1B, 0x00): bytes([0x00, 0x08, 0x85]),
    (0x1B, 0x01): bytes([0x00, 0x08, 0x85]),
}


def _inner(frame: bytes) -> tuple[bool, int | None, bytes]:
    """(cmd29-wrapped, receiver byte, inner command+payload) of a request."""
    body = frame[4:-1]
    if body[0] == 0x29:
        return True, body[1], body[2:]
    return False, None, body


class Wire:
    """Captures every outgoing CI-V frame and answers like the radio."""

    def __init__(self, radio: IcomRadio) -> None:
        self.radio = radio
        self.frames: list[bytes] = []
        # 0x07 0xD2 selection reads are kept apart: they read, never select.
        self.selection_reads: list[bytes] = []
        # The 0x07 0xD2 answer (None: the radio does not answer it).
        self.d2_answer: int | None = None
        self.fail_select = False
        # Runs when a 0x07 D0/D1 select is sent, before it is answered.
        self.on_select: Callable[[], Awaitable[None]] | None = None

    async def send(self, civ_frame: bytes, **kwargs: Any) -> CivFrame | None:
        frame = bytes(civ_frame)
        wrapped, receiver, inner = _inner(frame)
        if inner == b"\x07\xd2":
            self.selection_reads.append(frame)
            if self.d2_answer is None:
                raise TimeoutError("no 07 D2 answer")
            return parse_civ_frame(
                bytes([0xFE, 0xFE, CONTROLLER_ADDR, frame[2], 0x07, 0xD2])
                + bytes([self.d2_answer, 0xFD])
            )
        self.frames.append(frame)
        if inner[0] == 0x07 and inner[1:2] and inner[1] in _SELECT_CODES:
            if self.on_select is not None:
                await self.on_select()
            if self.fail_select:
                raise TimeoutError("select timed out")
        if not kwargs.get("wait_response", True):
            return None
        addr = frame[2]
        key = (inner[0], inner[1]) if len(inner) == 2 else (inner[0],)
        extra = _READ_REPLY.get(key) if len(inner) <= 2 else None
        if extra is None:
            return CivFrame(to_addr=CONTROLLER_ADDR, from_addr=addr, command=_ACK)
        prefix = bytes([0x29, receiver]) if wrapped else b""
        reply = bytes([0xFE, 0xFE, CONTROLLER_ADDR, addr]) + prefix + inner + extra
        return parse_civ_frame(reply + b"\xfd")

    def selection_frames(self) -> list[bytes]:
        return [
            f
            for f in self.frames
            if _inner(f)[2][0] == 0x07
            and len(_inner(f)[2]) > 1
            and _inner(f)[2][1] in _SELECT_CODES
        ]


def _radio(model: str) -> tuple[IcomRadio, Wire]:
    radio = IcomRadio("192.168.1.102", timeout=0.05, model=model)
    radio._connected = True
    transport = MockTransport()
    radio._civ_transport = transport
    radio._ctrl_transport = transport
    wire = Wire(radio)
    radio._send_civ_raw = wire.send  # type: ignore[method-assign]
    radio._radio_state.main.mode = "USB"
    radio._radio_state.sub.mode = "USB"
    return radio, wire


def _feed(radio: IcomRadio, *payload: int) -> None:
    """Deliver one radio-originated frame through the real CI-V RX path."""
    frame = build_civ_frame(CONTROLLER_ADDR, radio._radio_addr, payload[0])
    raw = frame[:-1] + bytes(payload[1:]) + b"\xfd"
    radio._update_state_cache_from_frame(parse_civ_frame(raw))


def _observe_selection(radio: IcomRadio, receiver: int) -> None:
    """The radio answers 0x07 0xD2 (which band is selected)."""
    _feed(radio, 0x07, 0xD2, receiver)


@dataclass(frozen=True)
class Op:
    name: str
    call: Callable[[IcomRadio, int], Awaitable[Any]]
    cmd: tuple[int, int | None]
    declared: tuple[str, ...] = ()
    direct: tuple[str, ...] = ()


OPS: tuple[Op, ...] = (
    Op(
        "set_freq",
        lambda r, rx: r.set_freq(_FREQ, receiver=rx),
        (0x05, None),
        ("set_freq",),
        ("set_unselected_freq", "get_unselected_freq"),
    ),
    Op(
        "set_mode",
        lambda r, rx: r.set_mode("USB", receiver=rx),
        (0x06, None),
        ("set_mode",),
        ("set_unselected_mode", "get_unselected_mode", "get_unselected_freq"),
    ),
    Op(
        "set_data_mode",
        lambda r, rx: r.set_data_mode(True, receiver=rx),
        (0x1A, 0x06),
        ("set_data_mode",),
    ),
    Op(
        "set_filter_width",
        lambda r, rx: r.set_filter_width(2400, receiver=rx),
        (0x1A, 0x03),
        ("set_filter_width",),
    ),
    Op(
        "get_filter_width",
        lambda r, rx: r.get_filter_width(receiver=rx),
        (0x1A, 0x03),
        ("set_filter_width",),
    ),
    Op(
        "set_repeater_tone",
        lambda r, rx: r.set_repeater_tone(True, receiver=rx),
        (0x16, 0x42),
        ("set_repeater_tone",),
    ),
    Op(
        "get_repeater_tone",
        lambda r, rx: r.get_repeater_tone(receiver=rx),
        (0x16, 0x42),
        ("get_repeater_tone",),
    ),
    Op(
        "set_repeater_tsql",
        lambda r, rx: r.set_repeater_tsql(True, receiver=rx),
        (0x16, 0x43),
        ("set_repeater_tsql",),
    ),
    Op(
        "get_repeater_tsql",
        lambda r, rx: r.get_repeater_tsql(receiver=rx),
        (0x16, 0x43),
        ("get_repeater_tsql",),
    ),
    Op(
        "set_tone_freq",
        lambda r, rx: r.set_tone_freq(8850, receiver=rx),
        (0x1B, 0x00),
        ("set_tone_freq",),
    ),
    Op(
        "get_tone_freq",
        lambda r, rx: r.get_tone_freq(receiver=rx),
        (0x1B, 0x00),
        ("get_tone_freq",),
    ),
    Op(
        "set_tsql_freq",
        lambda r, rx: r.set_tsql_freq(8850, receiver=rx),
        (0x1B, 0x01),
        ("set_tsql_freq",),
    ),
    Op(
        "get_tsql_freq",
        lambda r, rx: r.get_tsql_freq(receiver=rx),
        (0x1B, 0x01),
        ("get_tsql_freq",),
    ),
    Op("set_vfo_slot", lambda r, rx: r.set_vfo_slot("A", receiver=rx), (0x07, None)),
    Op(
        "set_vfo_slot_confirmed",
        lambda r, rx: r._set_vfo_slot_confirmed("A", receiver=rx),
        (0x07, None),
    ),
    Op(
        "get_freq",
        lambda r, rx: r.get_freq(receiver=rx),
        (0x03, None),
        (),
        ("get_unselected_freq",),
    ),
    Op(
        "get_mode_info",
        lambda r, rx: r.get_mode_info(receiver=rx),
        (0x04, None),
        (),
        ("get_unselected_mode",),
    ),
)

_DUAL_MODELS = ("IC-9700", "IC-7610")


def _declared(model: str, op: Op) -> bool:
    profile = IcomRadio("192.168.1.102", model=model).profile
    cmd_map = profile.command_map
    return cmd_map is not None and all(cmd_map.has(name) for name in op.declared)


def _expected(radio: IcomRadio, op: Op, receiver: int, selected: int | None) -> str:
    profile = radio.profile
    cmd_map = profile.command_map
    if (
        receiver == 1
        and op.direct
        and unselected_selector_is_sub(profile)
        and cmd_map is not None
        and all(cmd_map.has(name) for name in op.direct)
    ):
        return "direct"
    if receiver == 1 and profile.supports_cmd29(*op.cmd):
        return "cmd29"
    if receiver == 0 and (profile.supports_cmd29(*op.cmd) or selected == 0):
        return "sent"
    if selected == receiver:
        return "plain"
    return "refused"


_CASES = [
    pytest.param(
        model,
        op,
        receiver,
        selected,
        id=f"{model}-{op.name}-rx{receiver}-sel{selected}",
    )
    for model in _DUAL_MODELS
    for op in OPS
    if _declared(model, op)
    for receiver in (0, 1)
    for selected in (None, 0, 1)
]


class TestNoHiddenSelectionSwitch:
    """Backbone: every operation x dual-RX profile x selection, on the wire."""

    @pytest.mark.asyncio
    @pytest.mark.parametrize(("model", "op", "receiver", "selected"), _CASES)
    async def test_wire(
        self, model: str, op: Op, receiver: int, selected: int | None
    ) -> None:
        radio, wire = _radio(model)
        if selected is not None:
            _observe_selection(radio, selected)
        expected = _expected(radio, op, receiver, selected)

        if expected == "refused":
            with pytest.raises(ReceiverNotSelectedError) as excinfo:
                await op.call(radio, receiver)
            assert excinfo.value.reason_code == "receiver_not_selected"
            assert wire.frames == []
        else:
            await op.call(radio, receiver)
            assert wire.frames, "the operation sent nothing"

        assert wire.selection_frames() == []
        if expected == "plain":
            assert all(not _inner(f)[0] for f in wire.frames)
            assert all(_inner(f)[2][0] == op.cmd[0] for f in wire.frames)
        elif expected == "cmd29":
            assert all(_inner(f)[:2] == (True, receiver) for f in wire.frames)
        elif expected == "direct":
            assert all(not _inner(f)[0] for f in wire.frames)
            assert all(
                _inner(f)[2][:2] in (b"\x25\x01", b"\x26\x01") for f in wire.frames
            )


class TestSingleReceiverUnchanged:
    """IC-705: receiver 0 needs no selection readback; receiver 1 is refused."""

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "op", [op for op in OPS if _declared("IC-705", op)], ids=lambda op: op.name
    )
    async def test_receiver0_sent_plain(self, op: Op) -> None:
        radio, wire = _radio("IC-705")
        await op.call(radio, 0)
        assert wire.frames
        assert all(not _inner(f)[0] for f in wire.frames)
        assert wire.selection_frames() == []

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "op", [op for op in OPS if _declared("IC-705", op)], ids=lambda op: op.name
    )
    async def test_receiver1_refused_by_profile(self, op: Op) -> None:
        radio, wire = _radio("IC-705")
        with pytest.raises((CommandError, ValueError)) as excinfo:
            await op.call(radio, 1)
        assert not isinstance(excinfo.value, ReceiverNotSelectedError)
        assert wire.frames == []


class TestSelectionIsFailClosed:
    """'Selected' comes only from the radio's answer or an acknowledged select."""

    @pytest.mark.asyncio
    async def test_defaulted_cache_is_not_a_selection(self) -> None:
        radio, wire = _radio("IC-9700")
        # RadioState.active defaults to MAIN before any readback.
        assert radio._radio_state.active == "MAIN"
        with pytest.raises(ReceiverNotSelectedError):
            await radio.set_freq(_FREQ, receiver=0)
        assert wire.frames == []

    @pytest.mark.asyncio
    async def test_lost_d2_reply_then_late_answer(self) -> None:
        radio, wire = _radio("IC-9700")
        # The connect-time 0x07 0xD2 query's reply was lost: refuse.
        for receiver in (0, 1):
            with pytest.raises(ReceiverNotSelectedError):
                await radio.set_mode("FM", receiver=receiver)
        assert wire.frames == []
        # The next poll's answer establishes the selection.
        _observe_selection(radio, 1)
        await radio.set_mode("FM", receiver=1)
        assert [_inner(f)[2][0] for f in wire.frames] == [0x06]
        with pytest.raises(ReceiverNotSelectedError):
            await radio.set_mode("FM", receiver=0)

    @pytest.mark.asyncio
    async def test_explicit_select_establishes_selection(self) -> None:
        radio, wire = _radio("IC-9700")
        await radio.select_receiver(1)
        await radio.set_freq(_FREQ, receiver=1)
        assert [_inner(f)[2][:2] for f in wire.frames] == [
            b"\x07\xd1",
            b"\x05" + bcd_encode(_FREQ)[:1],
        ]

    @pytest.mark.asyncio
    async def test_timed_out_select_leaves_selection_unknown(self) -> None:
        radio, wire = _radio("IC-9700")
        _observe_selection(radio, 0)
        wire.fail_select = True
        with pytest.raises(TimeoutError):
            await radio.select_receiver(1)
        wire.frames.clear()
        # The radio may or may not have switched: neither receiver is safe.
        for receiver in (0, 1):
            with pytest.raises(ReceiverNotSelectedError):
                await radio.set_freq(_FREQ, receiver=receiver)
        assert wire.frames == []

    @pytest.mark.asyncio
    async def test_new_generation_forgets_selection(self) -> None:
        radio, wire = _radio("IC-9700")
        _observe_selection(radio, 0)
        radio._civ_runtime.advance_generation("reconnect-attempt")
        with pytest.raises(ReceiverNotSelectedError):
            await radio.set_freq(_FREQ, receiver=0)
        assert wire.frames == []


class TestIc9700SubRead:
    """Selector 01 is not SUB on IC-9700: SUB comes from observed state only."""

    @pytest.mark.asyncio
    async def test_never_observed_is_refused_without_wire(self) -> None:
        radio, wire = _radio("IC-9700")
        _observe_selection(radio, 0)
        with pytest.raises(ReceiverNotSelectedError):
            await radio.get_freq(receiver=1)
        with pytest.raises(ReceiverNotSelectedError):
            await radio.get_mode_info(receiver=1)
        assert wire.frames == []

    @pytest.mark.asyncio
    async def test_value_observed_while_sub_selected(self) -> None:
        radio, wire = _radio("IC-9700")
        _observe_selection(radio, 1)
        _feed(radio, 0x03, *bcd_encode(435_000_000))
        _feed(radio, 0x04, 0x05, 0x02)  # FM, FIL2
        _observe_selection(radio, 0)
        _feed(radio, 0x03, *bcd_encode(145_000_000))

        assert await radio.get_freq(receiver=1) == 435_000_000
        mode, filt = await radio.get_mode_info(receiver=1)
        assert (mode.name, filt) == ("FM", 2)
        assert wire.frames == []
        snapshot = radio.state_store.snapshot()
        main = snapshot.field(FieldPath.active("0", "freq_mode", "freq_hz"))
        assert main.value == 145_000_000


class TestIc7610DirectSub:
    """Selector 01 is the physical SUB on IC-7610 (profile data)."""

    @pytest.mark.asyncio
    async def test_sub_freq_write_uses_selector_01_with_readback(self) -> None:
        radio, wire = _radio("IC-7610")
        _observe_selection(radio, 0)
        await radio.set_freq(_FREQ, receiver=1)
        assert [_inner(f)[2] for f in wire.frames] == [
            b"\x25\x01" + bcd_encode(_FREQ),
            b"\x25\x01",
        ]
        assert radio._radio_state.sub.freq == _FREQ

    @pytest.mark.asyncio
    async def test_sub_read_uses_selector_01_whatever_is_selected(self) -> None:
        radio, wire = _radio("IC-7610")
        assert await radio.get_freq(receiver=1) == _FREQ
        mode, filt = await radio.get_mode_info(receiver=1)
        assert (mode.name, filt) == ("USB", 1)
        assert [_inner(f)[2] for f in wire.frames] == [b"\x25\x01", b"\x26\x01"]

    @pytest.mark.asyncio
    async def test_sub_read_rejects_wrong_selector_echo(self) -> None:
        radio, wire = _radio("IC-7610")

        async def wrong_echo(civ_frame: bytes, **kwargs: Any) -> CivFrame | None:
            wire.frames.append(bytes(civ_frame))
            return parse_civ_frame(
                bytes([0xFE, 0xFE, CONTROLLER_ADDR, civ_frame[2], 0x25, 0x00])
                + bcd_encode(_FREQ)
                + b"\xfd"
            )

        radio._send_civ_raw = wrong_echo  # type: ignore[method-assign]
        with pytest.raises(CommandError, match="selector mismatch"):
            await radio.get_freq(receiver=1)

    @pytest.mark.asyncio
    async def test_unrouted_main_command_while_sub_selected_is_refused(self) -> None:
        radio, wire = _radio("IC-7610")
        _observe_selection(radio, 1)
        with pytest.raises(ReceiverNotSelectedError, match="select that receiver"):
            await radio.set_freq(_FREQ, receiver=0)
        with pytest.raises(ReceiverNotSelectedError):
            await radio.set_data_mode(True, receiver=0)
        assert wire.frames == []


class TestSwapEqualAb:
    """A3: the A/B opcode is sent directly; no receiver is selected first."""

    @pytest.mark.asyncio
    @pytest.mark.parametrize("method", ["swap_vfo_ab", "equalize_vfo_ab"])
    async def test_single_rx_sends_only_the_opcode(self, method: str) -> None:
        radio, wire = _radio("IC-705")
        await getattr(radio, method)(0)
        assert len(wire.frames) == 1
        assert wire.selection_frames() == []

    @pytest.mark.asyncio
    @pytest.mark.parametrize("method", ["swap_vfo_ab", "equalize_vfo_ab"])
    async def test_dual_rx_unselected_receiver_refused(self, method: str) -> None:
        import dataclasses

        radio, wire = _radio("IC-9700")
        # No shipped dual-RX profile declares A/B swap/equal; give the IC-9700
        # profile both codes to reach the dual-RX branch.
        radio._profile = dataclasses.replace(
            radio._profile, swap_ab_code=0xB0, equal_ab_code=0xA0
        )
        _observe_selection(radio, 1)
        with pytest.raises(ReceiverNotSelectedError):
            await getattr(radio, method)(0)
        assert wire.frames == []
        await getattr(radio, method)(1)
        assert len(wire.frames) == 1
        assert wire.selection_frames() == []


class TestStableReasonCode:
    def test_http_error_code(self) -> None:
        from rigplane.web.server import _command_error_code

        exc = ReceiverNotSelectedError("select that receiver first")
        assert _command_error_code(exc) == "receiver_not_selected"

    def test_admission_stays_route_based(self) -> None:
        radio, _ = _radio("IC-9700")
        # Selection unknown: MAIN is admitted (the call refuses with the
        # stable code), SUB without a cmd29 route is not admitted.
        assert radio.supports_command("set_af_level", receiver=0) is True
        assert radio.supports_command("set_af_level", receiver=1) is False


class TestRigctldRefusal:
    """rigctld answers a receiver refusal as RPRT -9 (rejected), not -7."""

    @pytest.mark.asyncio
    async def test_main_mode_while_sub_selected_is_rejected(self) -> None:
        from test_rigctld_wsjtx_split_mk2 import _exchange, _peer

        async with _peer("IC-7610") as (radio, peer, _server, reader, writer):
            _observe_selection(radio, 1)
            sent_before = len(peer.requests)
            reply = await _exchange(writer, reader, b"M VFOA USB 2400\n")
            assert reply == b"RPRT -9\n"
            assert len(peer.requests) == sent_before


# --- Second round (pre-review findings) -----------------------------------


def _commander_radio(
    model: str, *, timeout: float = 2.0
) -> tuple[IcomRadio, MockTransport]:
    """A radio with the real commander worker and wire seam, fake transport."""
    from _helpers import observe_selected_receiver

    radio = IcomRadio("192.168.1.102", timeout=timeout, model=model)
    transport = MockTransport()
    radio._civ_transport = transport
    radio._ctrl_transport = transport
    radio._connected = True
    radio._radio_state.main.mode = "USB"
    observe_selected_receiver(radio, 0)
    return radio, transport


def _civ_frames(packets: list[bytes]) -> list[bytes]:
    out: list[bytes] = []
    for packet in packets:
        start = packet.find(b"\xfe\xfe")
        end = packet.find(b"\xfd", start)
        if start >= 0 and end > start:
            out.append(packet[start + 4 : end])
    return out


async def _queued_behind_a_selection_change(
    radio: IcomRadio,
    transport: MockTransport,
    operation: Callable[[IcomRadio], Awaitable[Any]],
    new_selection: int,
) -> asyncio.Task[Any]:
    """Queue ``operation`` behind a raw in-flight read, then let the radio
    report ``new_selection`` before the queued command reaches the wire."""
    from _helpers import wrap_civ_in_udp

    # A raw 1C 00 read (no receiver admission) occupies the serialized send.
    blocker = asyncio.create_task(radio.send_civ(0x1C, 0x00))
    async with asyncio.timeout(2):
        while not transport.sent_packets:
            await asyncio.sleep(0)
    queued = asyncio.create_task(operation(radio))
    await asyncio.sleep(0.01)
    assert not queued.done()
    _observe_selection(radio, new_selection)  # the radio reports the change
    transport.queue_response(
        wrap_civ_in_udp(build_civ_frame(CONTROLLER_ADDR, 0xA2, 0x1C, 0x00, b"\x00"))
    )
    await asyncio.wait_for(blocker, 2)
    return queued


class TestSelectionCheckedAtWireDispatch:
    """P1: a selection change between admission and dispatch refuses the
    queued plain command before it reaches the wire."""

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        ("operation", "opcode"),
        [
            (lambda r: r.set_freq(145_000_000, receiver=0), 0x05),
            (lambda r: r.set_vfo_slot("A", receiver=0), 0x07),
            (lambda r: r.set_af_level(128, receiver=0), 0x14),
        ],
        ids=["set_freq", "set_vfo_slot", "set_af_level"],
    )
    async def test_queued_main_write_refused_when_sub_becomes_selected(
        self, operation: Callable[[IcomRadio], Awaitable[Any]], opcode: int
    ) -> None:
        radio, transport = _commander_radio("IC-9700")
        radio._civ_runtime.start_worker()
        try:
            write = await _queued_behind_a_selection_change(
                radio, transport, operation, new_selection=1
            )
            with pytest.raises(ReceiverNotSelectedError):
                await asyncio.wait_for(write, 2)
        finally:
            await radio._civ_runtime.stop_worker()
            radio._connected = False
        sent = _civ_frames(transport.sent_packets)
        assert sent == [b"\x1c\x00"]  # only the blocking read; no write, no select
        assert opcode not in [frame[0] for frame in sent[1:]]

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        ("operation", "opcode"),
        [
            (lambda r, rx: r.get_freq(receiver=rx), 0x03),
            (lambda r, rx: r.get_mode_info(receiver=rx), 0x04),
        ],
        ids=["get_freq", "get_mode_info"],
    )
    @pytest.mark.parametrize(
        ("receiver", "new_selection"), [(0, 1), (1, 0)], ids=["main", "sub"]
    )
    async def test_queued_selected_read_refused_when_selection_changes(
        self,
        operation: Callable[[IcomRadio, int], Awaitable[Any]],
        opcode: int,
        receiver: int,
        new_selection: int,
    ) -> None:
        radio, transport = _commander_radio("IC-9700")
        _observe_selection(radio, receiver)  # the read is for the selected one
        radio._civ_runtime.start_worker()
        try:
            read = await _queued_behind_a_selection_change(
                radio,
                transport,
                lambda r: operation(r, receiver),
                new_selection=new_selection,
            )
            with pytest.raises(ReceiverNotSelectedError):
                await asyncio.wait_for(read, 2)
        finally:
            await radio._civ_runtime.stop_worker()
            radio._connected = False
        sent = _civ_frames(transport.sent_packets)
        assert sent == [b"\x1c\x00"]  # the plain read never reached the wire
        assert opcode not in [frame[0] for frame in sent]


class TestFailedSelectLeavesSelectionUnknown:
    """P1: a failed or cancelled select forgets the selection, even when a
    0x07 0xD2 answer arrived while it was in flight."""

    @pytest.mark.asyncio
    async def test_timeout_with_stale_d2_answer_during_select(self) -> None:
        radio, wire = _radio("IC-9700")
        _observe_selection(radio, 0)

        async def stale_answer() -> None:
            _observe_selection(radio, 0)  # an earlier D2 query's reply

        wire.on_select = stale_answer
        wire.fail_select = True
        with pytest.raises(TimeoutError):
            await radio.select_receiver(1)
        wire.frames.clear()
        for receiver in (0, 1):
            with pytest.raises(ReceiverNotSelectedError):
                await radio.set_freq(_FREQ, receiver=receiver)
        assert wire.frames == []

    @pytest.mark.asyncio
    async def test_cancelled_select(self) -> None:
        radio, wire = _radio("IC-9700")
        _observe_selection(radio, 0)
        entered, never = asyncio.Event(), asyncio.Event()

        async def hang() -> None:
            entered.set()
            _observe_selection(radio, 0)
            await never.wait()

        wire.on_select = hang
        task = asyncio.create_task(radio.select_receiver(1))
        await asyncio.wait_for(entered.wait(), 1)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        wire.frames.clear()
        with pytest.raises(ReceiverNotSelectedError):
            await radio.set_freq(_FREQ, receiver=0)
        assert wire.frames == []


class TestReconnectOwnership:
    """P2: after a new CI-V generation, plain answers filed under a stale
    selection are not served as the other receiver's value."""

    @pytest.mark.asyncio
    async def test_pre_d2_answer_after_reconnect_is_not_sub(self) -> None:
        radio, wire = _radio("IC-9700")
        _observe_selection(radio, 1)  # SUB selected before the reconnect
        radio._civ_runtime.advance_generation("reconnect-attempt")
        # The radio came back on MAIN; its 0x03 answer arrives before 07 D2
        # and is filed under the stale RadioState.active (SUB).
        _feed(radio, 0x03, *bcd_encode(145_000_000))
        _observe_selection(radio, 0)
        with pytest.raises(ReceiverNotSelectedError):
            await radio.get_freq(receiver=1)
        assert wire.frames == []
        # A genuinely owned SUB answer is served.
        _observe_selection(radio, 1)
        _feed(radio, 0x03, *bcd_encode(435_000_000))
        _observe_selection(radio, 0)
        assert await radio.get_freq(receiver=1) == 435_000_000


class TestMalformedSelectors:
    """P2: only selector 00/01 establishes a selection or a receiver value."""

    @pytest.mark.asyncio
    @pytest.mark.parametrize("value", [0x02, 0xFF])
    async def test_malformed_d2_establishes_nothing(self, value: int) -> None:
        radio, wire = _radio("IC-9700")
        _feed(radio, 0x07, 0xD2, value)
        assert radio._selected_receiver() is None
        with pytest.raises(ReceiverNotSelectedError):
            await radio.set_freq(_FREQ, receiver=1)
        assert wire.frames == []
        with pytest.raises(KeyError):
            radio.state_store.snapshot().field(
                FieldPath.global_("slow_state", "active")
            )

    @pytest.mark.parametrize(
        "payload",
        [
            (0x25, 0x02, *bcd_encode(435_000_000)),
            (0x26, 0x02, 0x05, 0x00, 0x01),
        ],
        ids=["0x25", "0x26"],
    )
    def test_malformed_25_26_selector_is_dropped(
        self, payload: tuple[int, ...]
    ) -> None:
        radio, _wire = _radio("IC-7610")
        before = (radio._radio_state.sub.freq, radio._radio_state.sub.mode)
        _feed(radio, *payload)
        snapshot = radio.state_store.snapshot()
        for name in ("freq_hz", "mode"):
            with pytest.raises(KeyError):
                snapshot.field(FieldPath.active("1", "freq_mode", name))
        assert (radio._radio_state.sub.freq, radio._radio_state.sub.mode) == before


class TestMainReadsAreReceiverAware:
    """P2: MAIN reads follow the SUB rule; never SUB's value for MAIN."""

    @pytest.mark.asyncio
    @pytest.mark.parametrize("model", ["IC-9700", "IC-7610"])
    async def test_main_read_while_sub_selected(self, model: str) -> None:
        radio, wire = _radio(model)
        _observe_selection(radio, 0)
        _feed(radio, 0x03, *bcd_encode(145_000_000))
        _feed(radio, 0x04, 0x01, 0x02)  # USB FIL2
        _observe_selection(radio, 1)
        assert await radio.get_freq(receiver=0) == 145_000_000
        mode, filt = await radio.get_mode_info(receiver=0)
        assert (mode.name, filt) == ("USB", 2)
        assert wire.frames == []  # the plain 03/04 would read SUB

    @pytest.mark.asyncio
    async def test_main_read_with_unknown_selection_is_refused(self) -> None:
        radio, wire = _radio("IC-9700")
        with pytest.raises(ReceiverNotSelectedError):
            await radio.get_freq(receiver=0)
        with pytest.raises(ReceiverNotSelectedError):
            await radio.get_mode_info(receiver=0)
        assert wire.frames == []


class TestDirectSubWriteKeepsReadback:
    """P2: the IC-7610 selector-01 write stores the verified readback."""

    @pytest.mark.asyncio
    async def test_differing_readback_is_stored_and_reported(self) -> None:
        radio, wire = _radio("IC-7610")
        radio._radio_state.sub.freq = 7_000_000
        with pytest.raises(CommandError, match="readback 145500000 Hz"):
            await radio.set_freq(145_500_100, receiver=1)
        assert radio._radio_state.sub.freq == _FREQ  # the radio's readback


class TestQueuedRefusalReachesIssuer:
    """P2: a queued command's receiver refusal keeps its stable code."""

    def test_poller_marks_lifecycle_with_reason(self) -> None:
        from rigplane.web.radio_poller import RadioPoller

        calls: list[dict[str, Any]] = []

        class Service:
            def fail_command(self, command_id: str, **params: Any) -> bool:
                calls.append({"command_id": command_id, **params})
                return True

        class Entry:
            command_service = Service()
            command_id = "cmd-1"
            session_id = "session-issuer"
            source = "websocket"

        RadioPoller._mark_queued_command_failed(
            None,  # type: ignore[arg-type]
            Entry(),  # type: ignore[arg-type]
            ReceiverNotSelectedError("select that receiver first"),
        )
        assert calls[0]["details"] == {"reason": "receiver_not_selected"}

    @pytest.mark.asyncio
    async def test_failed_lifecycle_carries_the_code_to_the_issuer(self) -> None:
        from rigplane.web.server import WebConfig, WebServer
        from test_web_server_coverage import _ack_held, _life, _register_two_sessions

        srv = WebServer(None, WebConfig(radio_model="IC-9700"))
        issuer_q, bystander_q = _register_two_sessions(srv)
        await _ack_held(srv)
        srv._on_command_lifecycle_event(
            _life(
                state="failed",
                message="select that receiver first",
                details={
                    "session_id": "session-issuer",
                    "reason": "receiver_not_selected",
                },
            )
        )
        assert issuer_q.get_nowait()["type"] == "notification"
        payload = issuer_q.get_nowait()
        assert payload["details"] == {"reason": "receiver_not_selected"}
        assert bystander_q.empty()

    @pytest.mark.asyncio
    async def test_forged_reason_is_rejected(self) -> None:
        from rigplane.web.server import WebConfig, WebServer
        from test_web_server_coverage import _ack_held, _life, _register_two_sessions

        srv = WebServer(None, WebConfig(radio_model="IC-9700"))
        issuer_q, _bystander_q = _register_two_sessions(srv)
        await _ack_held(srv)
        srv._on_command_lifecycle_event(
            _life(
                state="failed",
                details={"session_id": "session-issuer", "reason": "made_up"},
            )
        )
        assert issuer_q.empty()


class TestHttpConflict:
    """P2: HTTP answers a receiver refusal 409 with the stable code."""

    @pytest.mark.asyncio
    async def test_main_set_freq_while_sub_selected(self) -> None:
        from rigplane.web.radio_poller import RadioPoller
        from rigplane.web.server import WebConfig, WebServer
        from test_web_split_writes import _request

        radio, transport = _commander_radio("IC-9700")
        _observe_selection(radio, 1)
        web = WebServer(radio, WebConfig(host="127.0.0.1", port=0))
        poller = RadioPoller(
            radio, web.command_queue, state_store=web.command_state_store
        )
        web._server = await asyncio.start_server(web._accept_client, "127.0.0.1", 0)

        async def execute_queued_command() -> None:
            await web.command_queue.wait(timeout=5.0)
            entry = web.command_queue.take_entry()
            assert entry is not None
            # The poller loop logs the refusal; the HTTP issuer gets it.
            with pytest.raises(ReceiverNotSelectedError):
                await poller._execute_queued_entry(entry)

        consumer = asyncio.create_task(execute_queued_command())
        try:
            status, body = await asyncio.wait_for(
                _request(web, "http", "set_freq", {"freq": 145_000_000, "receiver": 0}),
                timeout=10.0,
            )
            await asyncio.wait_for(consumer, timeout=5.0)
        finally:
            consumer.cancel()
            web._server.close()
            await web._server.wait_closed()
            radio._connected = False
        assert status == 409
        assert body["error"] == "receiver_not_selected"
        assert transport.sent_packets == []


class TestSelectionEstablishedByReading:
    """The connect flow and reads establish the selection with a bounded
    0x07 0xD2 read; nothing ever selects."""

    @pytest.mark.asyncio
    async def test_main_read_establishes_selection_on_demand(self) -> None:
        radio, wire = _radio("IC-9700")
        wire.d2_answer = 0
        assert await radio.get_freq() == _FREQ
        assert len(wire.selection_reads) == 1
        assert [_inner(f)[2] for f in wire.frames] == [b"\x03"]
        assert radio._selected_receiver() == 0
        assert wire.selection_frames() == []

    @pytest.mark.asyncio
    async def test_unanswered_read_stays_fail_closed(self) -> None:
        radio, wire = _radio("IC-9700")
        with pytest.raises(ReceiverNotSelectedError):
            await radio.get_freq()
        assert len(wire.selection_reads) == 1
        assert wire.frames == []

    @pytest.mark.asyncio
    @pytest.mark.parametrize("model", ["IC-7610", "IC-9700"])
    async def test_connect_state_fetch_queries_the_selection(self, model: str) -> None:
        radio, _wire = _radio(model)
        radio.send_civ = AsyncMock()  # type: ignore[method-assign]
        radio._INITIAL_STATE_GAP_LAN = 0.0  # type: ignore[misc]
        radio._INITIAL_STATE_GAP_SERIAL = 0.0  # type: ignore[misc]
        await radio._fetch_initial_state()
        sent = [
            (c.args[0], c.kwargs.get("data")) for c in radio.send_civ.await_args_list
        ]
        assert (0x07, b"\xd2") in sent
        # Its answer, through the real RX path, establishes the selection.
        _observe_selection(radio, 1)
        assert radio._selected_receiver() == 1

    @pytest.mark.asyncio
    async def test_write_establishes_selection_on_demand(self) -> None:
        radio, wire = _radio("IC-9700")
        wire.d2_answer = 0
        await radio.set_freq(_FREQ)
        assert len(wire.selection_reads) == 1
        assert [_inner(f)[2][:1] for f in wire.frames] == [b"\x05"]

    @pytest.mark.asyncio
    async def test_single_receiver_reads_no_selection(self) -> None:
        radio, wire = _radio("IC-705")
        await radio.get_freq()
        assert wire.selection_reads == []


class TestWebSocketRefusalEndToEnd:
    """P1: the refusal reaches the issuing client through the real WebSocket
    API: real client, real command, real queue and poller; only the radio
    link is fake."""

    @pytest.mark.asyncio
    async def test_sub_set_freq_while_main_selected(self) -> None:
        import contextlib
        import json

        from rigplane.web.radio_poller import RadioPoller
        from rigplane.web.server import WebConfig, WebServer
        from test_web_server import _ws_connect, _ws_recv_frame, _ws_send_text

        # MAIN selected. A short CI-V timeout: the poller's start-up menu reads
        # go unanswered on this fake link before it runs the queued command.
        radio, transport = _commander_radio("IC-9700", timeout=0.2)
        web = WebServer(radio, WebConfig(host="127.0.0.1", port=0))
        poller = RadioPoller(
            radio, web.command_queue, state_store=web.command_state_store
        )
        web._server = await asyncio.start_server(web._accept_client, "127.0.0.1", 0)

        async def open_client() -> tuple[asyncio.StreamReader, asyncio.StreamWriter]:
            reader, writer, _ = await _ws_connect("127.0.0.1", web.port, "/api/v1/ws")
            for expected in ("hello", "state_update"):
                _, payload = await _ws_recv_frame(reader, timeout=5.0)
                assert json.loads(payload)["type"] == expected
            # As the frontend runtime does (frontend-runtime.ts).
            await _ws_send_text(
                writer, json.dumps({"type": "subscribe", "streams": ["events"]})
            )
            return reader, writer

        async def frames_until(
            reader: asyncio.StreamReader, done: Callable[[dict[str, Any]], bool]
        ) -> list[dict[str, Any]]:
            seen: list[dict[str, Any]] = []
            while True:
                opcode, payload = await _ws_recv_frame(reader, timeout=10.0)
                if opcode != 0x1:  # text frames only
                    continue
                message = json.loads(payload)
                seen.append(message)
                if done(message):
                    return seen

        issuer = await open_client()
        bystander = await open_client()
        poller.start()  # the real poller loop executes the queued command
        try:
            await _ws_send_text(
                issuer[1],
                json.dumps(
                    {
                        "type": "cmd",
                        "id": "sub-freq",
                        "name": "set_freq",
                        "params": {"freq": 435_000_000, "receiver": 1},
                    }
                ),
            )
            issuer_frames = await frames_until(
                issuer[0],
                lambda m: (
                    m.get("type") == "command_lifecycle" and m.get("state") == "failed"
                ),
            )
            bystander_frames: list[dict[str, Any]] = []
            with contextlib.suppress(asyncio.TimeoutError, TimeoutError):
                while True:
                    opcode, payload = await _ws_recv_frame(bystander[0], timeout=0.3)
                    if opcode == 0x1:
                        bystander_frames.append(json.loads(payload))
        finally:
            poller.stop()
            for _reader, writer in (issuer, bystander):
                writer.close()
            web._server.close()
            await web._server.wait_closed()
            radio._connected = False

        assert {"type": "response", "id": "sub-freq", "ok": True}.items() <= next(
            m for m in issuer_frames if m.get("type") == "response"
        ).items()
        notification = next(m for m in issuer_frames if m.get("type") == "notification")
        assert notification["level"] == "error"
        lifecycle = issuer_frames[-1]
        assert lifecycle["commandId"] == "sub-freq"
        assert lifecycle["details"] == {"reason": "receiver_not_selected"}
        assert not any(
            m.get("type") in ("command_lifecycle", "notification")
            for m in bystander_frames
        )
        # Only the poller's own start-up reads reached the radio: no
        # frequency write and no receiver selection.
        sent = _civ_frames(transport.sent_packets)
        assert not [f for f in sent if f[:1] in (b"\x05", b"\x07")]
