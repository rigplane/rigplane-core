"""A CI-V write confirmed by the radio's echo counts as ACK (MOR-4436).

Over LAN the radio answers a SET not with FB but with a frame from the
radio address to the controller carrying exactly the write's command,
sub-command, receiver and data bytes. ``CivRequestTracker`` treats that
echo as the write's ACK: the waiting write future resolves with a
synthesized FB, and a fire-and-forget write sink is retired.
"""

from __future__ import annotations

import asyncio

import pytest

from rigplane.commands import (
    CONTROLLER_ADDR,
    RECEIVER_MAIN,
    RECEIVER_SUB,
    build_civ_frame,
    build_cmd29_frame,
    parse_ack_nak,
    parse_civ_frame,
)
from rigplane.core.civ import (
    CivEvent,
    CivEventType,
    CivRequestTracker,
    read_identity_from_frame,
    request_key_from_frame,
)
from rigplane.core.types import CivFrame
from rigplane.profiles import get_radio_profile

_BROADCAST = 0x00

_PROFILE_NAMES = ["G7250", "IC-7610"]


@pytest.fixture(
    params=[get_radio_profile(name).civ_addr for name in _PROFILE_NAMES],
    ids=_PROFILE_NAMES,
)
def radio_addr(request: pytest.FixtureRequest) -> int:
    """Two CI-V radio addresses from the loaded profiles.

    The echo-ACK mechanism keys on the write identity, never on a literal
    address, so every tracker test runs against both the G7250 and the
    IC-7610 address.
    """
    return request.param


def _frame(
    to_addr: int,
    from_addr: int,
    command: int,
    sub: int | None = None,
    data: bytes = b"",
) -> CivFrame:
    """Parse a production-built CI-V frame."""
    return parse_civ_frame(
        build_civ_frame(to_addr, from_addr, command, sub=sub, data=data or None)
    )


def _write(radio: int, command: int, sub: int | None, data: bytes) -> CivFrame:
    """An outgoing controller-to-radio write frame."""
    return _frame(radio, CONTROLLER_ADDR, command, sub, data)


def _echo(radio: int, command: int, sub: int | None, data: bytes) -> CivFrame:
    """The radio's byte-identical echo of a write, radio to controller."""
    return _frame(CONTROLLER_ADDR, radio, command, sub, data)


@pytest.mark.asyncio
async def test_exact_echo_resolves_blocking_write_waiter_as_ack(
    radio_addr: int,
) -> None:
    tracker = CivRequestTracker()
    write = _write(radio_addr, 0x14, 0x01, b"\x01\x10")
    future = tracker.register_ack(
        wait=True,
        consume_backlog=False,
        write_identity=read_identity_from_frame(write),
    )
    assert isinstance(future, asyncio.Future)

    assert (
        tracker.resolve_write_echo(_echo(radio_addr, 0x14, 0x01, b"\x01\x10")) is True
    )

    result = await future
    assert parse_ack_nak(result) is True
    assert result.command == 0xFB
    assert result.to_addr == CONTROLLER_ADDR
    assert result.from_addr == radio_addr


@pytest.mark.asyncio
async def test_fb_still_acks_and_fa_still_refuses_write_waiter(
    radio_addr: int,
) -> None:
    tracker = CivRequestTracker()
    write = _write(radio_addr, 0x14, 0x01, b"\x01\x10")
    ack_future = tracker.register_ack(
        wait=True,
        consume_backlog=False,
        write_identity=read_identity_from_frame(write),
    )
    assert isinstance(ack_future, asyncio.Future)
    assert (
        tracker.resolve(
            CivEvent(
                type=CivEventType.ACK,
                frame=_frame(CONTROLLER_ADDR, radio_addr, 0xFB),
            )
        )
        is True
    )
    assert parse_ack_nak(await ack_future) is True

    nak_future = tracker.register_ack(
        wait=True,
        consume_backlog=False,
        write_identity=read_identity_from_frame(write),
    )
    assert isinstance(nak_future, asyncio.Future)
    assert (
        tracker.resolve(
            CivEvent(
                type=CivEventType.NAK,
                frame=_frame(CONTROLLER_ADDR, radio_addr, 0xFA),
            )
        )
        is True
    )
    refused = await nak_future
    assert parse_ack_nak(refused) is False
    assert refused.command == 0xFA


@pytest.mark.asyncio
async def test_echo_of_second_write_resolves_only_second_future(
    radio_addr: int,
) -> None:
    tracker = CivRequestTracker()
    first = _write(radio_addr, 0x14, 0x01, b"\x01\x00")
    second = _write(radio_addr, 0x14, 0x01, b"\x01\x10")
    first_future = tracker.register_ack(
        wait=True,
        consume_backlog=False,
        write_identity=read_identity_from_frame(first),
    )
    second_future = tracker.register_ack(
        wait=True,
        consume_backlog=False,
        write_identity=read_identity_from_frame(second),
    )
    assert isinstance(first_future, asyncio.Future)
    assert isinstance(second_future, asyncio.Future)

    assert (
        tracker.resolve_write_echo(_echo(radio_addr, 0x14, 0x01, b"\x01\x10")) is True
    )

    assert parse_ack_nak(await second_future) is True
    assert not first_future.done()


def test_echo_of_second_write_retires_only_second_sink(radio_addr: int) -> None:
    tracker = CivRequestTracker()
    first = _write(radio_addr, 0x14, 0x01, b"\x01\x00")
    second = _write(radio_addr, 0x14, 0x01, b"\x01\x10")
    tracker.register_ack(wait=False, write_identity=read_identity_from_frame(first))
    tracker.register_ack(wait=False, write_identity=read_identity_from_frame(second))
    assert tracker.ack_sink_count == 2

    assert (
        tracker.resolve_write_echo(_echo(radio_addr, 0x14, 0x01, b"\x01\x10")) is True
    )

    assert tracker.ack_sink_count == 1


@pytest.mark.asyncio
async def test_echo_with_no_pending_write_leaves_tracker_unchanged(
    radio_addr: int,
) -> None:
    tracker = CivRequestTracker()
    unrelated = _write(radio_addr, 0x14, 0x01, b"\x01\x00")
    plain = tracker.register_ack(wait=True, consume_backlog=False)
    assert isinstance(plain, asyncio.Future)
    write_future = tracker.register_ack(
        wait=True,
        consume_backlog=False,
        write_identity=read_identity_from_frame(unrelated),
    )
    assert isinstance(write_future, asyncio.Future)
    before = tracker.pending_count

    assert (
        tracker.resolve_write_echo(_echo(radio_addr, 0x14, 0x01, b"\x01\x10")) is False
    )

    assert tracker.pending_count == before
    assert not plain.done()
    assert not write_future.done()


@pytest.mark.asyncio
async def test_broadcast_echo_is_not_an_ack(radio_addr: int) -> None:
    tracker = CivRequestTracker()
    write = _write(radio_addr, 0x14, 0x01, b"\x01\x10")
    future = tracker.register_ack(
        wait=True,
        consume_backlog=False,
        write_identity=read_identity_from_frame(write),
    )
    assert isinstance(future, asyncio.Future)

    broadcast = _frame(_BROADCAST, radio_addr, 0x14, 0x01, b"\x01\x10")

    assert tracker.resolve_write_echo(broadcast) is False
    assert not future.done()


@pytest.mark.asyncio
async def test_fb_and_fa_frames_are_not_echoes(radio_addr: int) -> None:
    tracker = CivRequestTracker()
    write = _write(radio_addr, 0x14, 0x01, b"\x01\x10")
    future = tracker.register_ack(
        wait=True,
        consume_backlog=False,
        write_identity=read_identity_from_frame(write),
    )
    assert isinstance(future, asyncio.Future)

    assert (
        tracker.resolve_write_echo(_frame(CONTROLLER_ADDR, radio_addr, 0xFB)) is False
    )
    assert (
        tracker.resolve_write_echo(_frame(CONTROLLER_ADDR, radio_addr, 0xFA)) is False
    )
    assert not future.done()


def test_stale_generation_echo_is_not_an_ack(radio_addr: int) -> None:
    tracker = CivRequestTracker()
    old_generation = tracker.generation
    write = _write(radio_addr, 0x14, 0x01, b"\x01\x10")
    tracker.register_ack(wait=False, write_identity=read_identity_from_frame(write))
    tracker.advance_generation()
    current = _write(radio_addr, 0x14, 0x01, b"\x01\x20")
    tracker.register_ack(wait=False, write_identity=read_identity_from_frame(current))

    assert (
        tracker.resolve_write_echo(
            _echo(radio_addr, 0x14, 0x01, b"\x01\x10"), generation=old_generation
        )
        is False
    )
    assert tracker.ack_sink_count == 1


@pytest.mark.asyncio
async def test_nak_only_waiter_is_not_claimed_as_a_write(
    radio_addr: int,
) -> None:
    tracker = CivRequestTracker()
    write = _write(radio_addr, 0x14, 0x01, b"\x01\x10")
    future = tracker.register_ack(
        wait=True,
        consume_backlog=False,
        nak_only=True,
        write_identity=read_identity_from_frame(write),
    )
    assert isinstance(future, asyncio.Future)

    assert (
        tracker.resolve_write_echo(_echo(radio_addr, 0x14, 0x01, b"\x01\x10")) is False
    )
    assert not future.done()


@pytest.mark.asyncio
async def test_older_read_sink_wins_over_write_echo(radio_addr: int) -> None:
    tracker = CivRequestTracker()
    read = _frame(radio_addr, CONTROLLER_ADDR, 0x1C, 0x00)
    tracker.register_ack(wait=False, response_key=request_key_from_frame(read))
    write = _write(radio_addr, 0x1C, 0x00, b"\x00")
    write_future = tracker.register_ack(
        wait=True,
        consume_backlog=False,
        write_identity=read_identity_from_frame(write),
    )
    assert isinstance(write_future, asyncio.Future)

    assert (
        tracker.resolve_write_echo(
            _frame(CONTROLLER_ADDR, radio_addr, 0x1C, 0x00, b"\x00")
        )
        is False
    )
    assert not write_future.done()

    assert (
        tracker.resolve(
            CivEvent(
                type=CivEventType.RESPONSE,
                frame=_frame(CONTROLLER_ADDR, radio_addr, 0x1C, 0x00, b"\x00"),
            )
        )
        is True
    )


@pytest.mark.asyncio
async def test_newer_read_sink_wins_over_write_echo(radio_addr: int) -> None:
    tracker = CivRequestTracker()
    write = _write(radio_addr, 0x1C, 0x00, b"\x00")
    write_future = tracker.register_ack(
        wait=True,
        consume_backlog=False,
        write_identity=read_identity_from_frame(write),
    )
    assert isinstance(write_future, asyncio.Future)
    read = _frame(radio_addr, CONTROLLER_ADDR, 0x1C, 0x00)
    tracker.register_ack(wait=False, response_key=request_key_from_frame(read))

    assert (
        tracker.resolve_write_echo(
            _frame(CONTROLLER_ADDR, radio_addr, 0x1C, 0x00, b"\x00")
        )
        is False
    )
    assert not write_future.done()

    assert (
        tracker.resolve(
            CivEvent(
                type=CivEventType.RESPONSE,
                frame=_frame(CONTROLLER_ADDR, radio_addr, 0x1C, 0x00, b"\x00"),
            )
        )
        is True
    )


@pytest.mark.asyncio
async def test_blocking_read_waiter_wins_over_write_echo(radio_addr: int) -> None:
    tracker = CivRequestTracker()
    write = _write(radio_addr, 0x1C, 0x00, b"\x00")
    write_future = tracker.register_ack(
        wait=True,
        consume_backlog=False,
        write_identity=read_identity_from_frame(write),
    )
    assert isinstance(write_future, asyncio.Future)
    read = _frame(radio_addr, CONTROLLER_ADDR, 0x1C, 0x00)
    read_future = tracker.register_response(request_key_from_frame(read))

    echo = _frame(CONTROLLER_ADDR, radio_addr, 0x1C, 0x00, b"\x00")
    assert tracker.resolve_write_echo(echo) is False
    assert not write_future.done()

    assert tracker.resolve(CivEvent(type=CivEventType.RESPONSE, frame=echo)) is True
    assert await read_future == echo
    assert not write_future.done()


@pytest.mark.asyncio
async def test_data_mismatch_by_one_byte_is_not_an_echo(
    radio_addr: int,
) -> None:
    tracker = CivRequestTracker()
    write = _write(radio_addr, 0x14, 0x01, b"\x01\x10")
    future = tracker.register_ack(
        wait=True,
        consume_backlog=False,
        write_identity=read_identity_from_frame(write),
    )
    assert isinstance(future, asyncio.Future)

    assert (
        tracker.resolve_write_echo(_echo(radio_addr, 0x14, 0x01, b"\x01\x11")) is False
    )
    assert not future.done()


@pytest.mark.asyncio
async def test_receiver_mismatch_is_not_an_echo(radio_addr: int) -> None:
    tracker = CivRequestTracker()
    outgoing = parse_civ_frame(
        build_cmd29_frame(
            radio_addr,
            CONTROLLER_ADDR,
            0x16,
            sub=0x02,
            data=b"\x01",
            receiver=RECEIVER_MAIN,
        )
    )
    future = tracker.register_ack(
        wait=True,
        consume_backlog=False,
        write_identity=read_identity_from_frame(outgoing),
    )
    assert isinstance(future, asyncio.Future)

    other_receiver = parse_civ_frame(
        build_cmd29_frame(
            CONTROLLER_ADDR,
            radio_addr,
            0x16,
            sub=0x02,
            data=b"\x01",
            receiver=RECEIVER_SUB,
        )
    )

    assert tracker.resolve_write_echo(other_receiver) is False
    assert not future.done()

    same_receiver = parse_civ_frame(
        build_cmd29_frame(
            CONTROLLER_ADDR,
            radio_addr,
            0x16,
            sub=0x02,
            data=b"\x01",
            receiver=RECEIVER_MAIN,
        )
    )
    assert tracker.resolve_write_echo(same_receiver) is True
    assert parse_ack_nak(await future) is True
