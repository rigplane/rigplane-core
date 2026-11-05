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

LAN keeps no such order (``IcomTransport.ordered_ingress``): a delayed or
retransmitted refusal may arrive after a later read's answer, so there the
answer order proves nothing and no sink is retired by it.
"""

from __future__ import annotations

from collections.abc import Callable

import pytest

from rigplane.backends._icom_serial_base import _IcomSerialRadioBase
from rigplane.backends.ic705.serial import Ic705SerialRadio
from rigplane.backends.ic7300 import Ic7300SerialRadio
from rigplane.backends.icom7610.drivers.serial_civ_link import SerialCivLink
from rigplane.backends.icom7610.drivers.serial_session import SerialCivTransport
from rigplane.commands import CONTROLLER_ADDR, build_civ_frame, parse_civ_frame
from rigplane.commands.commander import Priority
from rigplane.core.civ import CivRequestKey, CivRequestTracker
from rigplane.core.transport import IcomTransport
from rigplane.radio import IcomRadio
from rigplane.testing.timing import await_until

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
    assert IcomTransport.ordered_ingress is False


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


@pytest.mark.parametrize(
    ("model", "radio_addr"),
    [("IC-705", 0xA4), ("IC-7610", 0x98)],
    ids=["ic705", "ic7610"],
)
async def test_lan_answer_order_retires_no_older_read(
    model: str, radio_addr: int
) -> None:
    """Read B's answer arrives before read A's retransmitted FA on LAN: the
    FA refuses A, and the later read C is not refused."""
    radio = IcomRadio("192.168.1.100", timeout=0.05, model=model)
    radio._civ_transport = IcomTransport()
    radio._radio_addr = radio_addr
    tracker = radio._civ_request_tracker
    named = _record_refusals(tracker)
    for key in (_FREQ, _S_METER, _TUNER):
        tracker.register_ack(wait=False, response_key=key, refused_read=key)
    runtime = radio._civ_runtime
    try:
        answer = build_civ_frame(
            CONTROLLER_ADDR, radio_addr, 0x15, sub=0x02, data=b"\x00\x40"
        )
        await runtime._route_civ_frame(
            parse_civ_frame(answer), generation=radio._civ_epoch
        )
        late_refusal = build_civ_frame(CONTROLLER_ADDR, radio_addr, 0xFA)
        await runtime._route_civ_frame(
            parse_civ_frame(late_refusal), generation=radio._civ_epoch
        )

        assert named == [(_FREQ,)]
        assert tracker.ack_sink_count == 1  # the tuner read still waits
    finally:
        radio._civ_transport = None
