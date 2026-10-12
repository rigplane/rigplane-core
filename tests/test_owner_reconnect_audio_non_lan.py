"""Characterization: held RX audio demand across an owner disconnect +
connect on the non-LAN backends that replace ``connect()``.

MOR-3415 established on LAN (IC-7610 over Ethernet) that RX demand held by
``AudioSession`` survives an owner ``disconnect()`` and nothing re-establishes
it after ``connect()``. This file characterizes the same owner sequence on the
backends that define their own ``connect()`` and therefore never reach any
CoreRadio-level audio recovery:

- Icom serial USB — ``backends/_icom_serial_base.py:
  _IcomSerialRadioBase.connect`` (IC-7300/IC-705 USB path). The backend serves
  RX audio through the radio-owned ``CoreRadio.audio_session`` over
  ``_serial_audio_driver`` (``_IcomSerialRadioBase.start_rx`` /
  ``stop_rx``); ``disconnect()`` calls ``_stop_serial_audio_driver()``.
- Yaesu CAT — ``backends/yaesu_cat/radio.py: YaesuCatRadio.connect``. Same
  shape: its own ``audio_session`` property wrapping an ``AudioBus`` over the
  USB audio driver (``YaesuCatRadio.start_rx`` / ``stop_rx``), and
  ``disconnect()`` stops the driver's RX and TX legs.

The rigctld client backend (``backends/rigctld_client/radio.py:
RigctldClientRadio``) has no audio path at all — the module defines no
``AudioSession``, no ``audio_bus``, no ``start_rx`` and advertises no
``"audio"`` capability — so no test is written for it.

Each strand test holds RX demand the way a web ``audio_start`` subscriber does
(the relay's ``web/handlers/audio.py: AudioBroadcaster._resolve_rx_route``
resolves ``radio.audio_session.subscribe_rx``), runs the owner
``disconnect()`` → ``connect()`` pair, and asserts the driver's RX leg is live
again. Each control test proves the assertion is reachable in the same fixture
by making the call the LAN recovery makes —
``audio/session.py: AudioSession.reestablish`` — after ``connect()``.

Both connects run the backends' real identity gates (the fakes answer the
``19 00`` / ``ID;`` identity reads), so the gate's answering path is exercised;
the held no-response identity states are not covered here.
"""

from __future__ import annotations

from rigplane.backends.ic7300 import Ic7300SerialRadio
from rigplane.backends.yaesu_cat.radio import YaesuCatRadio
from rigplane.types import AudioCodec
from rigplane_testkit.yaesu import FakeCatRadio

from test_icom7610_serial_radio import _FakeSerialCivLink, _FakeUsbAudioDriver
from test_yaesu_audio import FakeAudioDriver

_PCM_FRAME = b"\x01\x02\x03\x04"


def _icom_serial_radio() -> tuple[Ic7300SerialRadio, _FakeUsbAudioDriver]:
    link = _FakeSerialCivLink(model_id=0x94)
    driver = _FakeUsbAudioDriver()
    radio = Ic7300SerialRadio(
        device="/dev/ttyUSB-fake",
        civ_link=link,
        audio_driver=driver,
        audio_codec=AudioCodec.PCM_1CH_16BIT,
        # Hermetic on any host: no OS enumeration, so no real port is probed.
        _enumerate_serial_ports_fn=lambda: [],
    )
    # The fake link answers no keep-alive poll; hold the CI-V watchdog off
    # for the test's lifetime (no tick, no link-down declaration).
    radio._SERIAL_WATCHDOG_INTERVAL_S = 3600.0
    return radio, driver


async def _yaesu_radio() -> tuple[YaesuCatRadio, FakeAudioDriver, FakeCatRadio]:
    fake = FakeCatRadio()
    await fake.start()
    driver = FakeAudioDriver()
    radio = YaesuCatRadio(
        device=fake.device_url,
        profile="ftx1",
        audio_driver=driver,  # type: ignore[arg-type]
    )
    return radio, driver, fake


# ---------------------------------------------------------------------------
# Icom serial (IC-7300, USB CI-V + USB audio)
# ---------------------------------------------------------------------------


async def test_icom_serial_owner_reconnect_leaves_held_rx_demand_dead() -> None:
    """Owner disconnect() + connect() on the Icom serial backend does not
    re-establish RX demand the AudioSession still holds (MOR-3436 phase 1)."""
    radio, driver = _icom_serial_radio()
    await radio.connect()
    session = radio.audio_session
    sub = await session.subscribe_rx("web")
    try:
        assert driver.rx_running is True
        driver.emit_rx_pcm(_PCM_FRAME)
        packet = await sub.get(timeout=1.0)
        assert packet is not None and packet.data == _PCM_FRAME

        await radio.disconnect()
        assert driver.rx_running is False
        assert session.rx_demand == 1  # the demand survives the disconnect

        await radio.connect()
        assert radio.connected is True
        assert session.rx_demand == 1
        # The property under characterization: the held RX demand is live
        # again on the driver after the owner connect.
        assert driver.rx_running is True
    finally:
        await sub.release()
        await radio.disconnect()


async def test_icom_serial_manual_reestablish_recovers_held_rx_demand() -> None:
    """Positive control: ``AudioSession.reestablish()`` after ``connect()`` —
    the call the LAN recovery makes — restarts the driver RX leg in this
    fixture, so the strand assertion above is reachable."""
    radio, driver = _icom_serial_radio()
    await radio.connect()
    session = radio.audio_session
    sub = await session.subscribe_rx("web")
    try:
        await radio.disconnect()
        assert driver.rx_running is False

        await radio.connect()
        await session.reestablish()

        assert driver.rx_running is True
        driver.emit_rx_pcm(_PCM_FRAME)
        packet = await sub.get(timeout=1.0)
        assert packet is not None and packet.data == _PCM_FRAME
    finally:
        await sub.release()
        await radio.disconnect()


# ---------------------------------------------------------------------------
# Yaesu CAT (FTX-1, serial CAT + USB audio)
# ---------------------------------------------------------------------------


async def test_yaesu_cat_owner_reconnect_leaves_held_rx_demand_dead() -> None:
    """Owner disconnect() + connect() on the Yaesu CAT backend does not
    re-establish RX demand the AudioSession still holds (MOR-3436 phase 1)."""
    radio, driver, fake = await _yaesu_radio()
    try:
        await radio.connect()
        session = radio.audio_session
        sub = await session.subscribe_rx("web")
        try:
            assert driver.rx_running is True
            driver.inject_rx_frame(_PCM_FRAME)
            packet = await sub.get(timeout=1.0)
            assert packet is not None and packet.data == _PCM_FRAME

            await radio.disconnect()
            assert driver.rx_running is False
            assert session.rx_demand == 1  # the demand survives the disconnect

            await radio.connect()
            assert radio.connected is True
            assert session.rx_demand == 1
            # The property under characterization: the held RX demand is
            # live again on the driver after the owner connect.
            assert driver.rx_running is True
        finally:
            await sub.release()
            await radio.disconnect()
    finally:
        await fake.stop()


async def test_yaesu_cat_manual_reestablish_recovers_held_rx_demand() -> None:
    """Positive control: ``AudioSession.reestablish()`` after ``connect()`` —
    the call the LAN recovery makes — restarts the driver RX leg in this
    fixture, so the strand assertion above is reachable."""
    radio, driver, fake = await _yaesu_radio()
    try:
        await radio.connect()
        session = radio.audio_session
        sub = await session.subscribe_rx("web")
        try:
            await radio.disconnect()
            assert driver.rx_running is False

            await radio.connect()
            await session.reestablish()

            assert driver.rx_running is True
            driver.inject_rx_frame(_PCM_FRAME)
            packet = await sub.get(timeout=1.0)
            assert packet is not None and packet.data == _PCM_FRAME
        finally:
            await sub.release()
            await radio.disconnect()
    finally:
        await fake.stop()
