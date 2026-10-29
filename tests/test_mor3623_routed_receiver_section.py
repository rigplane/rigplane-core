"""MOR-3623: the shared MAIN/SUB switch-and-restore as one critical section.

``runtime/_dual_rx_runtime.py: DualRxRuntimeMixin._run_receiver_routed_once``
serves every dual-receiver profile that addresses a receiver by selecting
it (``07 D0``/``07 D1``) — the IC-9700 for most per-receiver commands, the
IC-7610 for VFO-slot selection — so each case runs on both. Simulated:
``MockTransport`` stands in for the radio; no hardware is touched.

* An active receiver the radio has not reported is read back (``07 D2``)
  before "no switch needed" is decided; a profile with no readback refuses.
* A routed operation is refused while another task's selection change is
  in flight, and its frame is refused at the wire when a later selection
  moved the radio; a later selection is never overwritten by our restore.
* A cancelled caller waits for the restoration, including its timeout
  retry, before the cancellation propagates.
"""

from __future__ import annotations

import asyncio
from dataclasses import replace

import pytest

from _helpers import wrap_civ_in_udp
from rigplane.commands import CONTROLLER_ADDR, build_civ_frame, parse_civ_frame
from rigplane.commands.command_map import CommandMap
from rigplane.core.exceptions import CommandError, CommandRejectedError, TimeoutError
from rigplane.radio import IcomRadio
from rigplane.types import CivFrame
from test_radio import MockTransport

MODELS = [
    pytest.param("IC-9700", id="ic9700"),
    pytest.param("IC-7610", id="ic7610"),
]


def _radio(model: str, transport: MockTransport) -> IcomRadio:
    radio = IcomRadio("192.168.1.100", timeout=2.0, model=model)
    radio._civ_transport = transport
    radio._ctrl_transport = transport
    radio._connected = True
    return radio


def _reply(radio: IcomRadio, command: int, data: bytes = b"") -> bytes:
    return wrap_civ_in_udp(
        build_civ_frame(CONTROLLER_ADDR, radio.profile.civ_addr, command, data=data)
    )


def _frames(transport: MockTransport) -> list[tuple[int, bytes]]:
    frames = [
        parse_civ_frame(packet[packet.index(b"\xfe\xfe") :])
        for packet in transport.sent_packets
        if b"\xfe\xfe" in packet
    ]
    return [(frame.command, frame.data) for frame in frames]


def _observe_selection(radio: IcomRadio, value: int) -> None:
    """The radio reports its selection (``07 D2``) through the RX path."""
    radio._civ_runtime._update_state_cache_from_frame(
        CivFrame(
            to_addr=CONTROLLER_ADDR,
            from_addr=radio.profile.civ_addr,
            command=0x07,
            sub=None,
            data=bytes([0xD2, value]),
        )
    )


def _split_off_action(radio: IcomRadio):
    """A routed action whose frame (``0F 00``) waits for the radio's ACK."""

    async def action(is_current=None):
        civ = build_civ_frame(
            radio.profile.civ_addr, CONTROLLER_ADDR, 0x0F, data=b"\x00"
        )
        return await radio._send_civ_expect(
            civ, label="routed_test_action", is_current=is_current
        )

    return action


async def _until(condition, *, deadline: float = 5.0) -> None:
    async with asyncio.timeout(deadline):
        while not condition():
            await asyncio.sleep(0)


# ── The selection must be a reading before "no switch needed" ────────


@pytest.mark.asyncio
@pytest.mark.parametrize("model", MODELS)
async def test_an_unreported_selection_is_read_back_before_skipping_the_switch(
    model,
) -> None:
    """SUB is physically selected and the initial ``07 D2`` answer was lost:
    the mirror still holds its default MAIN. A MAIN request must read the
    selection back and switch, not send its frame to SUB."""
    transport = MockTransport()
    radio = _radio(model, transport)
    assert radio._radio_state.active == "MAIN"  # the dataclass default
    transport.queue_response_on_send(1, _reply(radio, 0x07, b"\xd2\x01"))
    for n in (2, 3, 4):
        transport.queue_response_on_send(n, _reply(radio, 0xFB))
    await radio._run_with_receiver_vfo_fallback(
        receiver=0, operation="routed_test", action=_split_off_action(radio)
    )
    assert _frames(transport) == [
        (0x07, b"\xd2"),  # read the selection: SUB
        (0x07, b"\xd0"),  # select MAIN
        (0x0F, b"\x00"),  # the action, on MAIN
        (0x07, b"\xd1"),  # restore SUB
    ]
    assert radio._radio_state.active == "SUB"


@pytest.mark.asyncio
@pytest.mark.parametrize("model", MODELS)
async def test_a_reported_selection_needs_no_readback(model) -> None:
    transport = MockTransport()
    radio = _radio(model, transport)
    _observe_selection(radio, 0x00)
    transport.queue_response_on_send(1, _reply(radio, 0xFB))
    await radio._run_with_receiver_vfo_fallback(
        receiver=0, operation="routed_test", action=_split_off_action(radio)
    )
    assert _frames(transport) == [(0x0F, b"\x00")]


@pytest.mark.asyncio
@pytest.mark.parametrize("model", MODELS)
async def test_no_declared_readback_refuses_instead_of_trusting_the_default(
    model,
) -> None:
    transport = MockTransport()
    radio = _radio(model, transport)
    command_map = radio.profile.command_map
    assert command_map is not None and command_map.has("get_main_sub_band")
    radio._profile = replace(
        radio.profile,
        command_map=CommandMap(
            {
                name: command_map.get(name)
                for name in command_map
                if name != "get_main_sub_band"
            }
        ),
    )
    with pytest.raises(CommandError, match="get_main_sub_band"):
        await radio._run_with_receiver_vfo_fallback(
            receiver=0, operation="routed_test", action=_split_off_action(radio)
        )
    assert _frames(transport) == []


# ── One critical section against competing selections ────────────────


@pytest.mark.asyncio
@pytest.mark.parametrize("model", MODELS)
async def test_a_competing_routed_request_is_refused_while_a_switch_is_pending(
    model,
) -> None:
    """A SUB request is switching (its ``07 D1`` ACK pending) when a MAIN
    request arrives. The mirror still says MAIN, so the MAIN request would
    skip switching and land its frame on SUB: it must be refused, and the
    SUB request completes untouched."""
    transport = MockTransport()
    radio = _radio(model, transport)
    _observe_selection(radio, 0x00)
    transport.queue_response_on_send(2, _reply(radio, 0xFB))  # SUB action
    transport.queue_response_on_send(3, _reply(radio, 0xFB))  # restore MAIN
    sub_request = asyncio.create_task(
        radio._run_with_receiver_vfo_fallback(
            receiver=1, operation="routed_test", action=_split_off_action(radio)
        )
    )
    try:
        await _until(lambda: len(transport.sent_packets) == 1)
        with pytest.raises(CommandRejectedError):
            await radio._run_with_receiver_vfo_fallback(
                receiver=0, operation="routed_test", action=_split_off_action(radio)
            )
        assert _frames(transport) == [(0x07, b"\xd1")]
        transport.queue_response(_reply(radio, 0xFB))  # the SUB switch ACK
        await asyncio.wait_for(sub_request, 5)
    finally:
        if not sub_request.done():
            sub_request.cancel()
        await asyncio.gather(sub_request, return_exceptions=True)
    assert _frames(transport) == [
        (0x07, b"\xd1"),
        (0x0F, b"\x00"),
        (0x07, b"\xd0"),
    ]
    assert radio._radio_state.active == "MAIN"


@pytest.mark.asyncio
@pytest.mark.parametrize("model", MODELS)
@pytest.mark.parametrize(("later", "later_code"), [(0, 0xD0), (1, 0xD1)])
async def test_a_later_selection_is_preserved_and_stops_our_frame(
    model, later, later_code
) -> None:
    """Another caller selects a receiver after our switch, before our frame
    is sent — even the receiver we switched to. Our frame is refused at
    the wire and our restoration is skipped: their selection owns the
    radio."""
    transport = MockTransport()
    radio = _radio(model, transport)
    _observe_selection(radio, 0x00)
    transport.queue_response_on_send(1, _reply(radio, 0xFB))  # our SUB switch
    transport.queue_response_on_send(2, _reply(radio, 0xFB))  # their selection
    entered, gate = asyncio.Event(), asyncio.Event()
    action = _split_off_action(radio)

    async def gated_action(is_current=None):
        entered.set()
        await gate.wait()
        return await action(is_current)

    ours = asyncio.create_task(
        radio._run_with_receiver_vfo_fallback(
            receiver=1, operation="routed_test", action=gated_action
        )
    )
    try:
        await asyncio.wait_for(entered.wait(), 5)  # switched, frame not sent
        await radio.select_receiver(later)
        gate.set()
        with pytest.raises(CommandRejectedError):
            await asyncio.wait_for(ours, 5)
    finally:
        gate.set()
        if not ours.done():
            ours.cancel()
        await asyncio.gather(ours, return_exceptions=True)
    assert _frames(transport) == [(0x07, b"\xd1"), (0x07, bytes([later_code]))]
    assert radio._radio_state.active == ("SUB" if later else "MAIN")


@pytest.mark.asyncio
@pytest.mark.parametrize("model", MODELS)
async def test_a_selection_change_while_our_frame_awaits_its_ack_is_reported(
    model,
) -> None:
    """Our frame is on the wire when another caller selects MAIN: the ACK
    cannot tell which receiver applied it last, so the outcome is reported
    unknown (``CommandError``), never success, and our restore is skipped."""
    transport = MockTransport()
    radio = _radio(model, transport)
    _observe_selection(radio, 0x00)
    transport.queue_response_on_send(1, _reply(radio, 0xFB))  # our SUB switch
    ours = asyncio.create_task(
        radio._run_with_receiver_vfo_fallback(
            receiver=1, operation="routed_test", action=_split_off_action(radio)
        )
    )
    theirs = None
    try:
        await _until(lambda: len(transport.sent_packets) == 2)
        theirs = asyncio.create_task(radio.select_receiver(0))
        await _until(lambda: len(transport.sent_packets) == 3)
        transport.queue_response(_reply(radio, 0xFB))  # our frame's ACK
        transport.queue_response(_reply(radio, 0xFB))  # their selection's ACK
        with pytest.raises(CommandError, match="outcome is unknown"):
            await asyncio.wait_for(ours, 5)
        await asyncio.wait_for(theirs, 5)
    finally:
        for task in (ours, theirs):
            if task is not None and not task.done():
                task.cancel()
        await asyncio.gather(
            *(t for t in (ours, theirs) if t is not None), return_exceptions=True
        )
    assert _frames(transport) == [
        (0x07, b"\xd1"),
        (0x0F, b"\x00"),
        (0x07, b"\xd0"),  # theirs, not a restore of ours
    ]
    assert radio._radio_state.active == "MAIN"


# ── Cancellation joins the restoration ───────────────────────────────


@pytest.mark.asyncio
@pytest.mark.parametrize("model", MODELS)
async def test_cancel_waits_for_a_restoration_that_times_out_and_retries(
    model,
) -> None:
    """The caller is cancelled while the restoration runs; that attempt
    then times out. The retry still runs, and the caller finishes only
    after it — with the cancellation."""
    radio = IcomRadio("192.168.1.100", timeout=2.0, model=model)
    radio._connected = True
    _observe_selection(radio, 0x00)
    restore_started = asyncio.Event()
    restore_gate = asyncio.Event()
    calls: list[str] = []

    async def fake_set_vfo_wire(vfo: str) -> None:
        calls.append(vfo)
        if vfo == "MAIN" and calls.count("MAIN") == 1:
            restore_started.set()
            await restore_gate.wait()
            raise TimeoutError("restore timed out")

    radio._set_vfo_wire = fake_set_vfo_wire  # type: ignore[method-assign]

    async def action(is_current=None):
        return "written"

    task = asyncio.create_task(
        radio._run_with_receiver_vfo_fallback(
            receiver=1, operation="routed_test", action=action
        )
    )
    try:
        await asyncio.wait_for(restore_started.wait(), 5)
        task.cancel()
        for _ in range(20):
            await asyncio.sleep(0)
        assert not task.done()  # held until the restoration finished
        restore_gate.set()
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(task, 5)
    finally:
        restore_gate.set()
        if not task.done():
            task.cancel()
        await asyncio.gather(task, return_exceptions=True)
    assert calls == ["SUB", "MAIN", "MAIN"]
    assert radio._radio_state.active == "MAIN"
