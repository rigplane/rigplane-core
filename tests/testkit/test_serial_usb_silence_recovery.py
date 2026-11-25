"""USB silence mid-session, then the radio answers again (MOR-4654).

The bench IC-705 went silent on USB three times (serial ports still
listed, identity never accepted, the radio's USB audio interface gone;
recovered by replug or radio reboot). Two checks, no new mechanism:

1. AUTOMATIC RECOVERY: the running station reconnects by itself when the
   radio starts answering again with the port still present. The fake
   stops answering CI-V with the port open, then answers again — the
   station must recover without restart, through the existing owner
   (the serial CI-V watchdog's ``soft_reconnect`` + the background
   identity re-read in ``backends/_icom_serial_base.py``).
2. NO SILENT FAILURE: while silent, the published health must say the
   radio is not answering (``radio_not_responding``) — never the LAN
   ``radio_network_lost`` cause — with the no-answer sentence in
   ``lastError``.

Covers the IC-705 and one more serial profile (IC-7300): the mechanism
is shared, so a per-radio branch would fail the second row.
"""

from __future__ import annotations

import asyncio
import contextlib
import json

import pytest

from rigplane import RadioConnectionState
from rigplane.backends.ic705 import Ic705SerialRadio
from rigplane.backends.ic7300 import Ic7300SerialRadio
from rigplane.backends.icom7610.drivers.serial_civ_link import SerialCivLink
from rigplane.backends.yaesu_cat.radio import YaesuCatRadio
from rigplane.commands.commander import Priority
from rigplane.core.radio_protocol import RadioIdentityStatus
from rigplane.web.runtime_helpers import classify_radio_health
from rigplane.web.server import WebServer

from rigplane_testkit.serial import CivSerialFake
from rigplane.testing.timing import await_until
from rigplane_testkit.yaesu import FakeCatRadio

_DEVICE = "civ-serial-fake"  # synthetic node name; no real path is opened

# Compressed cadence (production knobs, same shape as
# ``test_serial_silent_link.py``): short answer window and watchdog tick
# so the silence evidence crosses the link-down threshold in well under
# a second, and a short identity re-read backoff so the recovery lands
# quickly once the fake answers again.
_ANSWER_WINDOW_S = 0.2
_WATCHDOG_TICK_S = 0.05


def _radio_for(model: str, fake: CivSerialFake) -> Ic705SerialRadio | Ic7300SerialRadio:
    """The CLI's serial construction with the fake behind the opener seam."""
    link = SerialCivLink(
        device=_DEVICE, open_serial_connection=fake.open_serial_connection
    )
    cls = Ic705SerialRadio if model == "IC-705" else Ic7300SerialRadio
    radio = cls(
        device=_DEVICE,
        civ_link=link,
        _enumerate_serial_ports_fn=lambda: [],  # no real OS port enumeration
    )
    radio._civ_get_timeout = _ANSWER_WINDOW_S
    radio._SERIAL_WATCHDOG_INTERVAL_S = _WATCHDOG_TICK_S
    radio._SERIAL_WATCHDOG_RETRY_S = 0.05
    radio._SERIAL_WATCHDOG_RETRY_MAX_S = 0.1
    radio._SERIAL_IDENTITY_REREAD_BACKOFF_S = (0.05, 0.1)
    radio._SERIAL_IDENTITY_REREAD_STEADY_S = 0.1
    return radio


async def _poll_loop(
    radio: Ic705SerialRadio | Ic7300SerialRadio, stop: asyncio.Event
) -> None:
    """A live poller: fire-and-forget CI-V ``0x03`` reads, errors ignored."""
    while not stop.is_set():
        try:
            await radio.send_civ(
                0x03,
                wait_response=False,
                priority=Priority.BACKGROUND,
                wait_dispatch=False,
            )
        except Exception:
            pass
        await asyncio.sleep(0.02)


class _FakeWriter:
    """Minimal writer for capturing an HTTP response (buffer, drain)."""

    def __init__(self) -> None:
        self.buffer = bytearray()

    def write(self, data: bytes) -> None:
        self.buffer.extend(data)

    async def drain(self) -> None:
        return None


async def _served_station_status(server: WebServer) -> dict:
    """Fetch the real ``/api/v1/station`` handler output, hardware faked only."""
    writer = _FakeWriter()
    await server._serve_station_status(writer)  # noqa: SLF001
    text = writer.buffer.decode("ascii", errors="replace")
    body_start = text.index("\r\n\r\n") + 4
    return json.loads(text[body_start:])


@pytest.mark.realtime(
    reason="link-down and recovery budgets are real-clock timeouts on the live watchdog"
)
@pytest.mark.parametrize("model", ["IC-705", "IC-7300"])
async def test_usb_silence_then_answers_recovers_without_restart(model: str) -> None:
    """Port present, CI-V silent, then answering again: station recovers.

    ``CivSerialFake.silence`` models the bench failure (the port stays
    open, nothing answers — including the identity read); ending it with
    ``silence(0)`` models the radio answering again after the operator
    replugs the cable or restarts the radio, without re-enumeration.
    Recovery must come from the resident watchdog + identity re-read:
    the owner-stop epoch must not move (no owner teardown/restart).
    """
    fake = (
        CivSerialFake(model="IC-705", model_id=b"\xa4")
        if model == "IC-705"
        else CivSerialFake(model="IC-7300")
    )
    radio = _radio_for(model, fake)

    await radio.connect()
    assert radio.connected
    assert radio.conn_state is RadioConnectionState.CONNECTED
    owner_stop_epoch = radio._owner_stop_epoch

    stop = asyncio.Event()
    poller = asyncio.create_task(_poll_loop(radio, stop))
    try:
        assert await await_until(
            lambda: any(frame.command == 0x03 for frame in fake.commands),
            timeout=2.0,
            description="the poller never reached the wire",
        )

        fake.silence(30.0)
        assert await await_until(
            lambda: radio.conn_state is not RadioConnectionState.CONNECTED,
            timeout=5.0,
            description="silent link never declared link-down",
        ), "silent link with the port present never declared link-down"
        assert not radio.connected

        # NO SILENT FAILURE while the radio is silent: the published
        # health names the unanswered radio, not a lost network.
        health = classify_radio_health(radio)
        assert health["radioLink"] == "reconnecting"
        assert health["readiness"] in ("recovering", "stalled")
        assert health["likelyCause"] == "radio_not_responding", (
            f"USB silence surfaced as {health['likelyCause']!r}: "
            "a USB radio has no network to lose"
        )

        # The same verdict through the REAL status endpoint, hardware
        # faked only: the identity gate must have recorded the
        # plain-words no-answer sentence (port + cable/RF guidance) first.
        assert await await_until(
            lambda: (
                isinstance(radio.last_error, str)
                and _DEVICE in radio.last_error
                and "ferrite" in radio.last_error
            ),
            timeout=5.0,
            description="identity gate never recorded the no-answer message",
        ), "silent link never recorded the plain-words no-answer message"
        served = await _served_station_status(WebServer(radio))
        assert served["station"]["readiness"] == "no_usb_radio_connected"
        served_health = served["station"]["health"]
        assert served_health["radioLink"] == "reconnecting"
        assert served_health["likelyCause"] == "radio_not_responding", (
            f"served status says {served_health['likelyCause']!r}: "
            "reverting the shared classifier must fail this row"
        )
        assert _DEVICE in (served_health["lastError"] or ""), (
            "served lastError names no port — triage cannot tell which link is silent"
        )
        assert "ferrite" in (served_health["lastError"] or ""), (
            "served lastError lost the USB-cable guidance — "
            "reverting the no-answer sentence must fail this row"
        )

        # The radio answers again with the port still present.
        fake.silence(0.0)
        assert await await_until(
            lambda: (
                radio.connected and radio.conn_state is RadioConnectionState.CONNECTED
            ),
            timeout=15.0,
            description="station never recovered after the radio answered again",
        ), "the running station did not reconnect by itself"
        assert radio._owner_stop_epoch == owner_stop_epoch, (
            "recovery went through an owner teardown/restart instead of "
            "the resident watchdog + identity re-read"
        )

        # The recovered link answers: the identity the latch required is
        # a CI-V round trip, and the profile accepts it.
        identity = radio.connection_identity
        assert identity is not None
        assert identity.status in (
            RadioIdentityStatus.VERIFIED,
            RadioIdentityStatus.UNVERIFIED,
        ), f"recovered without an answered identity: {identity.status!r}"
        assert radio.last_error is None

        # ... and the published health is back to ready, not stuck —
        # through the real endpoint too.
        assert classify_radio_health(radio)["likelyCause"] == "unknown"
        assert classify_radio_health(radio)["readiness"] == "ready"
        recovered = await _served_station_status(WebServer(radio))
        assert recovered["station"]["health"]["likelyCause"] == "unknown"
        assert recovered["station"]["health"]["readiness"] == "ready"
        assert recovered["station"]["readiness"] == "ready_with_radio"
    finally:
        stop.set()
        poller.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await poller
        await radio.disconnect()


@pytest.mark.asyncio
async def test_yaesu_transport_down_after_session_is_not_responding() -> None:
    """MOR-4654 round 1 (P2): the conn_state-less serial sibling.

    Yaesu CAT has no explicit connection state after an established
    session drops (the bench shape: the USB device is gone), so the
    link reads ``unknown``. The shared serial-family classifier must
    still name the silent radio (``radio_not_responding``), never
    ``unknown``. The loss episode is counted by the real transport
    recovery primitive (the poller's own ``transport.reconnect()``),
    not hand-set; the answered identity is the real connect's.
    """
    fake = FakeCatRadio()
    await fake.start()
    try:
        radio = YaesuCatRadio(device=fake.device_url, profile="ftx1")
        await asyncio.wait_for(radio.connect(), 30)
        assert radio.connected
        assert radio.serial_reconnects_total == 0

        # The USB device is gone mid-session; the poller's recovery
        # primitive runs against the gone device and leaves the
        # transport down with one counted loss episode.
        await fake.stop()
        with contextlib.suppress(Exception):
            await asyncio.wait_for(radio._transport.reconnect(), 15)  # noqa: SLF001
        assert not radio.connected
        assert radio.serial_reconnects_total >= 1

        health = classify_radio_health(radio)
        assert health["radioLink"] == "unknown"
        assert health["likelyCause"] == "radio_not_responding", (
            f"down Yaesu link surfaced as {health['likelyCause']!r}: "
            "the shared serial-family verdict must cover conn_state-less backends"
        )
        assert health["readiness"] == "stalled"
        assert WebServer(radio)._build_radio_health()["likelyCause"] == (  # noqa: SLF001
            "radio_not_responding"
        )
        served = await _served_station_status(WebServer(radio))
        served_health = served["station"]["health"]
        assert served_health["radioLink"] == "unknown"
        assert served_health["likelyCause"] == "radio_not_responding"
        assert served_health["readiness"] == "stalled"
    finally:
        with contextlib.suppress(Exception):
            await radio.disconnect()
        await fake.stop()
