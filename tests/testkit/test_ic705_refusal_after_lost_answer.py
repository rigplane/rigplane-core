"""A refusal after a lost answer names the read the radio refused (MOR-4209).

End to end on the real serial path: ``Ic705SerialRadio`` connected to the
recorded IC-705 USB personality through the production ``SerialCivLink``
opener seam, with reads sent the way ``web/radio_poller.py`` sends polls
(fire-and-forget on the BACKGROUND lane). The frequency read's answer is
lost on the wire, the S-meter read is answered, and the tuner-status read
(``1C 01``, not in the recorded table) is refused with a bare ``FA``.

MOR-4058 hands every refusal to the acquisition scheduler as a terminal
answer for the read it names (``CivRequestTracker.take_refused_reads``).
Before MOR-4209 the lost frequency read's sink was still the oldest open
one, so the ``FA`` named the frequency read: the scheduler withheld a
healthy frequency/mode/PTT path as "unavailable" while the read the radio
really refused timed out. The radio answers in order, so the S-meter answer
already proves the frequency answer is gone.
"""

from __future__ import annotations

from rigplane.backends.ic705.serial import Ic705SerialRadio
from rigplane.backends.icom7610.drivers.serial_civ_link import SerialCivLink
from rigplane.commands.commander import Priority
from rigplane.core.civ import CivRequestKey
from rigplane.testing.timing import await_until

from testkit.ic705_fakes import ic705_usb_fake

_DEVICE = "civ-serial-fake"  # synthetic node name; no real path is opened


async def test_a_lost_answer_does_not_take_the_next_refusal() -> None:
    fake = ic705_usb_fake()
    link = SerialCivLink(
        device=_DEVICE, open_serial_connection=fake.open_serial_connection
    )
    radio = Ic705SerialRadio(
        device=_DEVICE,
        civ_link=link,
        _enumerate_serial_ports_fn=lambda: [],  # no real OS port enumeration
    )
    await radio.connect()
    try:
        tracker = radio._civ_request_tracker
        named: list[tuple[CivRequestKey, ...]] = []
        take = tracker.take_refused_reads

        def recording_take() -> tuple[CivRequestKey, ...]:
            refused = take()
            if refused:
                named.append(refused)
            return refused

        tracker.take_refused_reads = recording_take  # type: ignore[method-assign]
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
        assert named == [(CivRequestKey(command=0x1C, sub=0x01),)]
        # The lost read left nothing open to take a later FB/FA either.
        assert tracker.ack_sink_count == 0
    finally:
        await radio.disconnect()
