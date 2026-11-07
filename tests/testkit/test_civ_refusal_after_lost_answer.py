"""A refusal after a lost answer names the read the radio refused (MOR-4209).

End to end on the real serial path, for two radios: ``Ic705SerialRadio``
against the recorded IC-705 USB personality and ``Ic7300SerialRadio``
against the stateful IC-7300 serial fake, each through the production
``SerialCivLink`` opener seam, with reads sent the way
``web/radio_poller.py`` sends polls (fire-and-forget on the BACKGROUND
lane). The frequency read's answer is lost on the wire, the S-meter read
is answered, and the tuner-status read (``1C 01``, answered by neither
fake) is refused with a bare ``FA``.

MOR-4058 hands every refusal to the acquisition scheduler as a terminal
answer for the read it names (``CivRequestTracker.take_refused_reads``).
Before MOR-4209 the lost frequency read's sink was still the oldest open
one, so the ``FA`` named the frequency read: the scheduler withheld a
healthy frequency/mode/PTT path as "unavailable" while the read the radio
really refused timed out. A serial line keeps the radio's answer order
(``SerialCivTransport.ordered_ingress``), so the S-meter answer already
proves the frequency answer is gone.

On LAN, UDP may deliver a delayed or retransmitted datagram after a later
one; the LAN transport therefore delivers CI-V datagrams strictly by their
sequence number (``IcomCivTransport.ordered_ingress``, MOR-4205), so the same
proof holds there. The LAN cases replay the radio's answers reordered, or
with one lost, through a real ``IcomCivTransport`` and the production CI-V
pump.
"""

from __future__ import annotations

from collections.abc import Callable

import pytest

from rigplane.backends._icom_serial_base import _IcomSerialRadioBase
from rigplane.backends.ic705.serial import Ic705SerialRadio
from rigplane.backends.ic7300 import Ic7300SerialRadio
from rigplane.backends.icom7610.drivers.serial_civ_link import SerialCivLink
from rigplane.backends.icom7610.drivers.serial_session import SerialCivTransport
from rigplane.commands import CONTROLLER_ADDR, build_civ_frame
from rigplane.commands.commander import Priority
from rigplane.core.civ import CivRequestKey, CivRequestTracker
from rigplane.core.transport import (
    RETRANSMIT_PERIOD,
    ConnectionState,
    IcomCivTransport,
    RxOrderStats,
)
from rigplane.radio import IcomRadio
from rigplane.testing.timing import VirtualClock, await_until
from rigplane.types import bcd_encode

from lan_reorder_fake import LanRadioFake
from rigplane_testkit.serial import CivSerialFake
from testkit.ic705_fakes import ic705_usb_fake

_DEVICE = "civ-serial-fake"  # synthetic node name; no real path is opened

_FREQ = CivRequestKey(command=0x03, sub=None)
_S_METER = CivRequestKey(command=0x15, sub=0x02)
_TUNER = CivRequestKey(command=0x1C, sub=0x01)


def _record_refusals(
    tracker: CivRequestTracker,
) -> list[tuple[CivRequestKey, ...]]:
    """Record what each refusal named before the runtime hands it on."""
    named: list[tuple[CivRequestKey, ...]] = []
    take = tracker.take_refused_reads

    def recording_take() -> tuple[CivRequestKey, ...]:
        refused = take()
        if refused:
            named.append(refused)
        return refused

    tracker.take_refused_reads = recording_take  # type: ignore[method-assign]
    return named


_SERIAL_RADIOS: list[
    tuple[str, type[_IcomSerialRadioBase], Callable[[], CivSerialFake]]
] = [
    ("ic705", Ic705SerialRadio, ic705_usb_fake),
    ("ic7300", Ic7300SerialRadio, CivSerialFake),
]


def test_ingress_order_is_a_transport_contract() -> None:
    assert SerialCivTransport.ordered_ingress is True
    assert IcomCivTransport.ordered_ingress is True


@pytest.mark.parametrize(
    ("radio_cls", "make_fake"),
    [(cls, make) for _, cls, make in _SERIAL_RADIOS],
    ids=[name for name, _, _ in _SERIAL_RADIOS],
)
async def test_a_lost_answer_does_not_take_the_next_refusal(
    radio_cls: type[_IcomSerialRadioBase],
    make_fake: Callable[[], CivSerialFake],
) -> None:
    fake = make_fake()
    link = SerialCivLink(
        device=_DEVICE, open_serial_connection=fake.open_serial_connection
    )
    radio = radio_cls(
        device=_DEVICE,
        civ_link=link,
        _enumerate_serial_ports_fn=lambda: [],  # no real OS port enumeration
    )
    await radio.connect()
    try:
        tracker = radio._civ_request_tracker
        named = _record_refusals(tracker)
        fake.lose_next_answer(0x03)
        for command, sub in ((0x03, None), (0x15, 0x02), (0x1C, 0x01)):
            await radio.send_civ(
                command,
                sub=sub,
                wait_response=False,
                priority=Priority.BACKGROUND,
                wait_dispatch=False,
            )

        assert await await_until(
            lambda: (
                any(frame.command == 0x1C for frame in fake.commands) and bool(named)
            ),
            timeout=5.0,
        ), "the tuner-status refusal never reached the tracker"
        assert named == [(_TUNER,)]
        # The lost read left nothing open to take a later FB/FA either.
        assert tracker.ack_sink_count == 0
    finally:
        await radio.disconnect()


@pytest.mark.timeout(10)
@pytest.mark.parametrize("wire", ["reordered", "lost"])
@pytest.mark.parametrize(
    ("model", "radio_addr"),
    [("IC-705", 0xA4), ("IC-7610", 0x98)],
    ids=["ic705", "ic7610"],
)
async def test_lan_answers_reach_the_tracker_in_the_radio_order(
    model: str, radio_addr: int, wire: str, virtual_clock: VirtualClock
) -> None:
    """The radio answers the frequency read, then the S-meter read, then
    refuses the tuner read with a bare FA. On the wire the S-meter answer and
    the FA overtake the frequency answer ("reordered"), or the frequency
    answer never arrives ("lost"). Either way the FA names the tuner read,
    and no read is left open to take a later FB/FA."""
    radio = IcomRadio("192.168.1.100", timeout=0.05, model=model)
    transport = IcomCivTransport()
    transport.state = ConnectionState.CONNECTED
    radio._civ_transport = transport
    radio._radio_addr = radio_addr
    lan = LanRadioFake(transport, first_seq=9)
    tracker = radio._civ_request_tracker
    named = _record_refusals(tracker)
    for key in (_FREQ, _S_METER, _TUNER):
        tracker.register_ack(wait=False, response_key=key, refused_read=key)
    keepalive = lan.idle()
    freq = lan.packet(
        build_civ_frame(CONTROLLER_ADDR, radio_addr, 0x03, data=bcd_encode(14_074_000))
    )
    s_meter = lan.packet(
        build_civ_frame(CONTROLLER_ADDR, radio_addr, 0x15, sub=0x02, data=b"\x00\x40")
    )
    refusal = lan.packet(build_civ_frame(CONTROLLER_ADDR, radio_addr, 0xFA))
    transport.start_retransmit_loop()
    radio._civ_runtime.start_pump()
    try:
        lan.deliver(keepalive)
        if wire == "reordered":
            lan.deliver(s_meter, refusal, freq)
        else:
            lan.deliver(refusal, s_meter)
        # Past the retransmit budget: a lost answer has been given up.
        await virtual_clock.elapse(6 * RETRANSMIT_PERIOD)

        assert named == [(_TUNER,)]
        assert tracker.ack_sink_count == 0
        assert transport.rx_order_stats == (
            RxOrderStats(reordered=1)
            if wire == "reordered"
            else RxOrderStats(reordered=1, lost=1)
        )
    finally:
        transport.state = ConnectionState.DISCONNECTED
        radio._civ_transport = None
        for task in (transport._retransmit_task, radio._civ_rx_task):
            if task is not None:
                task.cancel()
