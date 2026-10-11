"""Property tests for CI-V framing and the CI-V receive path."""

from __future__ import annotations

import asyncio
import re

import pytest
from hypothesis import given
from hypothesis import strategies as st

from rigplane.commands import (
    CONTROLLER_ADDR,
    build_civ_frame,
    build_cmd29_frame,
    command_carries_sub,
    parse_civ_frame,
)
from rigplane.core.civ import iter_civ_frames
from rigplane.core.types import CivFrame
from rigplane.radio import IcomRadio
from rigplane.runtime._civ_rx import CivRuntime

pytestmark = pytest.mark.property

_CMD29 = 0x29
_TERMINATOR = 0xFD
_PREAMBLE_BYTE = 0xFE

# Bytes a well-formed frame may carry between its preamble and terminator.
_frame_byte = st.integers(0, 0xFF).filter(lambda b: b != _TERMINATOR)
_frame_data = st.lists(_frame_byte, max_size=16).map(bytes)


@st.composite
def _plain_frame(draw: st.DrawFn) -> CivFrame:
    command = draw(_frame_byte.filter(lambda b: b != _CMD29))
    return CivFrame(
        to_addr=draw(_frame_byte),
        from_addr=draw(_frame_byte),
        command=command,
        sub=draw(_frame_byte) if command_carries_sub(command) else None,
        data=draw(_frame_data),
    )


def _build(frame: CivFrame) -> bytes:
    return build_civ_frame(
        frame.to_addr, frame.from_addr, frame.command, sub=frame.sub, data=frame.data
    )


@given(_plain_frame())
def test_parse_inverts_build(frame: CivFrame) -> None:
    assert parse_civ_frame(_build(frame)) == frame


@given(_plain_frame(), st.sampled_from((0x00, 0x01)))
def test_parse_inverts_cmd29_build(frame: CivFrame, receiver: int) -> None:
    wire = build_cmd29_frame(
        frame.to_addr,
        frame.from_addr,
        frame.command,
        sub=frame.sub,
        data=frame.data,
        receiver=receiver,
    )
    assert parse_civ_frame(wire) == CivFrame(
        to_addr=frame.to_addr,
        from_addr=frame.from_addr,
        command=frame.command,
        sub=frame.sub,
        data=frame.data,
        receiver=receiver,
    )


# Junk never contains the preamble byte, so it cannot open a frame.
_junk = st.lists(
    st.integers(0, 0xFF).filter(lambda b: b != _PREAMBLE_BYTE), max_size=12
).map(bytes)


@given(st.lists(st.tuples(_junk, _plain_frame()), max_size=6), _junk)
def test_noisy_stream_yields_exactly_the_valid_frames(
    pieces: list[tuple[bytes, CivFrame]], tail: bytes
) -> None:
    wires = [_build(frame) for _, frame in pieces]
    stream = b"".join(junk + wire for (junk, _), wire in zip(pieces, wires)) + tail
    assert list(iter_civ_frames(stream)) == wires


@given(st.binary(max_size=40))
def test_parse_raises_only_value_error_on_arbitrary_bytes(raw: bytes) -> None:
    try:
        parse_civ_frame(raw)
    except ValueError:
        pass


@given(st.lists(_frame_byte, max_size=24).map(bytes))
def test_parse_raises_only_value_error_inside_a_frame_envelope(body: bytes) -> None:
    try:
        parse_civ_frame(b"\xfe\xfe" + body + b"\xfd")
    except ValueError:
        pass


# Command bytes CivRuntime dispatches on: every ``_handle_XX`` method, plus the
# bytes branched on directly (frequency/mode, power, transceiver ID, the cmd29
# prefix, NAK and ACK). Each gets its own run so its decoders see enough
# examples; ``None`` draws the command byte freely.
_ROUTED_COMMANDS = sorted(
    {
        int(name.removeprefix("_handle_"), 16)
        for name in dir(CivRuntime)
        if re.fullmatch(r"_handle_[0-9a-f]{2}", name)
    }
    | {0x00, 0x01, 0x03, 0x04, 0x18, 0x19, _CMD29, 0xFA, 0xFB}
)
# Low sub-command bytes are the ones the handlers branch on most.
_sub = st.one_of(st.none(), st.integers(0x00, 0x07), _frame_byte)
_RadioFrame = tuple[int, int, int | None, bytes]


def _radio_frame_body(command: int | None) -> st.SearchStrategy[_RadioFrame]:
    return st.tuples(
        st.sampled_from((CONTROLLER_ADDR, 0x00)),
        _frame_byte if command is None else st.just(command),
        _sub,
        st.lists(_frame_byte, max_size=24).map(bytes),
    )


async def _feed_rx_payload(payload_pieces: list[tuple[bytes, _RadioFrame]]) -> None:
    radio = IcomRadio("radio.invalid", model="IC-7610")
    runtime = radio._civ_runtime  # noqa: SLF001
    radio_addr = radio._radio_addr  # noqa: SLF001
    payload = b"".join(
        junk + build_civ_frame(to_addr, radio_addr, command, sub=sub, data=data)
        for junk, (to_addr, command, sub, data) in payload_pieces
    )
    # The per-frame body of CivRuntime._civ_rx_loop, without its catch-all
    # around _route_civ_frame: anything that escapes here would otherwise
    # be swallowed as "Unhandled exception while routing CI-V frame".
    for frame_bytes in iter_civ_frames(payload):
        try:
            frame = parse_civ_frame(frame_bytes)
        except ValueError:
            continue
        runtime._remember_raw_received_frame_bytes(frame, frame_bytes)  # noqa: SLF001
        runtime.deliver_raw_civ(frame_bytes)
        await runtime._route_civ_frame(  # noqa: SLF001
            frame,
            generation=radio._civ_epoch,  # noqa: SLF001
        )


@pytest.mark.parametrize(
    "command",
    [*_ROUTED_COMMANDS, None],
    ids=[*(f"0x{c:02X}" for c in _ROUTED_COMMANDS), "any"],
)
@given(data=st.data())
def test_rx_routing_raises_nothing_for_arbitrary_radio_frames(
    command: int | None, data: st.DataObject
) -> None:
    pieces = data.draw(
        st.lists(
            st.tuples(st.binary(max_size=8), _radio_frame_body(command)),
            min_size=1,
            max_size=4,
        )
    )
    asyncio.run(_feed_rx_payload(pieces))
