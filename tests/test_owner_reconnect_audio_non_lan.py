"""Held RX audio demand across an owner disconnect + connect on the
non-LAN backends that replace ``connect()``.

MOR-3415 established on LAN (IC-7610 over Ethernet) that RX demand held by
``AudioSession`` survives an owner ``disconnect()`` and nothing re-establishes
it after ``connect()``; the LAN tail landed in ``CoreRadio.connect`` (MOR-3436
phase 1, PR #4180). This file pins the same owner sequence on the backends
that define their own ``connect()`` and therefore never reach that tail:

- Icom serial USB — ``backends/_icom_serial_base.py:
  _IcomSerialRadioBase.connect`` (IC-7300/IC-705 USB path). The backend serves
  RX audio through the radio-owned ``CoreRadio.audio_session`` over
  ``_serial_audio_driver`` (``_IcomSerialRadioBase.start_rx`` /
  ``stop_rx``); ``disconnect()`` calls ``_stop_serial_audio_driver()``.
- Yaesu CAT — ``backends/yaesu_cat/radio.py: YaesuCatRadio.connect``. Same
  shape: its own ``audio_session`` property wrapping an ``AudioBus`` over the
  USB audio driver (``YaesuCatRadio.start_rx`` / ``stop_rx``), and
  ``disconnect()`` stops the driver's RX and TX legs.

Both backends now re-establish the held demand at the end of their owner
``connect()`` (and, for the serial backend, at the end of its own
``soft_reconnect()``), gated on live session demand and the per-attempt
owner-stop fence, deferred to the identity hold's completion when the connect
returns into the hold.

The rigctld client backend (``backends/rigctld_client/radio.py:
RigctldClientRadio``) has no audio path at all — the module defines no
``AudioSession``, no ``audio_bus``, no ``start_rx`` and advertises no
``"audio"`` capability — so no test is written for it.

Each strand test holds RX demand the way a web ``audio_start`` subscriber does
(the relay's ``web/handlers/audio.py: AudioBroadcaster._resolve_rx_route``
resolves ``radio.audio_session.subscribe_rx``), runs the owner
``disconnect()`` → ``connect()`` pair, and asserts the driver's RX leg is live
again exactly once (the fake drivers count ``rx_starts``/``rx_running``).
The ``_FakeUsbAudioDriver`` counts every ``start_rx``, so an assertion of the
exact count pins "exactly one re-establish" — the double-recovery guard
(``asyncio.current_task() is not getattr(self, "_reconnect_task", None)``, the
same identity test ``CoreRadio.connect`` uses) keeps the resident reconnect
loop's round single. The Yaesu backend has no reconnect path of its own (no
watchdog, no ``soft_reconnect``, nothing in ``src/`` re-calls its
``connect()`` on the radio's behalf), so it has no exactly-once loop test and
no double-recovery caller to guard against. The positive controls prove the
assertions are reachable in the same fixture by making the call the LAN
recovery makes — ``audio/session.py: AudioSession.reestablish`` — after
``connect()``.

The serial connects run the backend's real identity gate (the fake link
answers the ``19 00`` identity read), and the hold tests drive it into its
no-response state; the Yaesu gate is driven through the ``FakeCatRadio``
silence window the same way.
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock

from rigplane.backends.ic7300 import Ic7300SerialRadio
from rigplane.backends.yaesu_cat.radio import YaesuCatRadio
from rigplane.runtime._audio_recovery import AudioRecoveryState
from rigplane.runtime._connection_state import RadioConnectionState
from rigplane.types import AudioCodec
from rigplane_testkit.yaesu import FakeCatRadio

from test_icom7610_serial_radio import (
    _FakeSerialCivLink,
    _FakeUsbAudioDriver,
    _wait_until,
)
from test_yaesu_audio import FakeAudioDriver

_PCM_FRAME = b"\x01\x02\x03\x04"


def _recovery_events(cb: MagicMock) -> list[AudioRecoveryState]:
    """The ``on_audio_recovery`` states emitted so far, in order."""
    return [c.args[0] for c in cb.call_args_list]


def _icom_serial_radio(
    recovery_cb: MagicMock | None = None,
) -> tuple[Ic7300SerialRadio, _FakeUsbAudioDriver, _FakeSerialCivLink]:
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
    if recovery_cb is not None:
        radio._on_audio_recovery = recovery_cb
    # The fake link answers no keep-alive poll; hold the CI-V watchdog off
    # for the test's lifetime (no tick, no link-down declaration).
    radio._SERIAL_WATCHDOG_INTERVAL_S = 3600.0
    # Fast identity re-read cadence and answer window for the hold tests.
    radio._SERIAL_IDENTITY_REREAD_BACKOFF_S = (0.05,)
    radio._SERIAL_IDENTITY_REREAD_STEADY_S = 0.05
    radio._civ_get_timeout = 0.1
    return radio, driver, link


async def _yaesu_radio() -> tuple[YaesuCatRadio, FakeAudioDriver, FakeCatRadio]:
    fake = FakeCatRadio()
    await fake.start()
    driver = FakeAudioDriver()
    radio = YaesuCatRadio(
        device=fake.device_url,
        profile="ftx1",
        audio_driver=driver,  # type: ignore[arg-type]
    )
    # Fast identity re-read cadence for the hold test.
    radio._IDENTITY_REREAD_BACKOFF_S = (0.05,)
    radio._IDENTITY_REREAD_STEADY_S = 0.05
    return radio, driver, fake


# ---------------------------------------------------------------------------
# Icom serial (IC-7300, USB CI-V + USB audio)
# ---------------------------------------------------------------------------


async def test_icom_serial_owner_reconnect_reestablishes_held_rx_demand() -> None:
    """Owner disconnect() + connect() on the Icom serial backend
    re-establishes the RX demand the AudioSession still holds, exactly
    once (MOR-3436)."""
    recovery_cb = MagicMock()
    radio, driver, _link = _icom_serial_radio(recovery_cb)
    await radio.connect()
    session = radio.audio_session
    sub = await session.subscribe_rx("web")
    try:
        assert driver.rx_running is True
        assert driver.rx_starts == 1  # the held demand armed the first leg
        driver.emit_rx_pcm(_PCM_FRAME)
        packet = await sub.get(timeout=1.0)
        assert packet is not None and packet.data == _PCM_FRAME

        await radio.disconnect()
        assert driver.rx_running is False
        assert session.rx_demand == 1  # the demand survives the disconnect

        await radio.connect()
        assert radio.connected is True
        assert session.rx_demand == 1
        # The held RX demand is live again on the driver after the owner
        # connect — re-established exactly once.
        assert driver.rx_running is True
        assert driver.rx_starts == 2
        assert _recovery_events(recovery_cb) == [
            AudioRecoveryState.RECOVERING,
            AudioRecoveryState.RECOVERED,
        ]
        driver.emit_rx_pcm(_PCM_FRAME)
        packet = await sub.get(timeout=1.0)
        assert packet is not None and packet.data == _PCM_FRAME
    finally:
        await sub.release()
        await radio.disconnect()


async def test_icom_serial_owner_connect_without_demand_runs_no_recovery() -> None:
    """No session demand held across the owner disconnect → the connect
    ends with the driver's RX leg down."""
    radio, driver, _link = _icom_serial_radio()
    await radio.connect()
    await radio.disconnect()
    await radio.connect()
    assert radio.connected is True
    assert driver.rx_running is False
    assert driver.rx_starts == 0
    await radio.disconnect()


async def test_icom_serial_owner_stop_during_connect_runs_no_recovery() -> None:
    """Owner ``disconnect()`` landing while ``connect()`` awaits its
    identity read leaves no recovery and no deferred stash behind."""
    recovery_cb = MagicMock()
    radio, driver, _link = _icom_serial_radio(recovery_cb)
    await radio.connect()
    session = radio.audio_session
    sub = await session.subscribe_rx("web")
    assert driver.rx_running is True

    in_read = asyncio.Event()
    gate = asyncio.Event()

    async def _gated_silence() -> None:
        in_read.set()
        await gate.wait()
        return None  # silence: the gate records no_response

    radio._read_serial_identity_payload = AsyncMock(side_effect=_gated_silence)

    await radio.disconnect()
    assert driver.rx_running is False

    connect_task = asyncio.create_task(radio.connect())
    await asyncio.wait_for(in_read.wait(), timeout=5.0)
    await radio.disconnect()  # the owner stops the radio mid-connect
    gate.set()
    await asyncio.wait_for(connect_task, timeout=5.0)

    assert driver.rx_running is False  # nothing re-armed on the dead link
    assert _recovery_events(recovery_cb) == []  # no RECOVERING/FAILED at all
    assert radio._serial_deferred_audio is None
    assert session.rx_demand == 1  # the demand stays held, un-armed
    await sub.release()
    await radio.disconnect()


async def test_icom_serial_soft_reconnect_reestablishes_held_rx_demand_once() -> None:
    """The backend's own reconnect path (the watchdog's link-down →
    ``soft_reconnect`` flow) re-establishes the held RX demand exactly
    once."""
    recovery_cb = MagicMock()
    radio, driver, _link = _icom_serial_radio(recovery_cb)
    await radio.connect()
    session = radio.audio_session
    sub = await session.subscribe_rx("web")
    try:
        assert driver.rx_running is True
        # The link-down declaration stops the audio driver before the
        # watchdog's soft_reconnect recovers the link.
        await radio._declare_serial_link_down()
        assert driver.rx_running is False
        assert session.rx_demand == 1

        await radio.soft_reconnect()
        assert radio.connected is True
        assert driver.rx_running is True
        assert driver.rx_starts == 2  # re-established exactly once
        assert _recovery_events(recovery_cb) == [
            AudioRecoveryState.RECOVERING,
            AudioRecoveryState.RECOVERED,
        ]
        driver.emit_rx_pcm(_PCM_FRAME)
        packet = await sub.get(timeout=1.0)
        assert packet is not None and packet.data == _PCM_FRAME
    finally:
        await sub.release()
        await radio.disconnect()


async def test_icom_serial_identity_hold_reestablishes_once_after_completion() -> None:
    """A connect that returns into the identity hold re-establishes
    nothing until the hold completes, then exactly once."""
    recovery_cb = MagicMock()
    radio, driver, link = _icom_serial_radio(recovery_cb)
    await radio.connect()
    session = radio.audio_session
    sub = await session.subscribe_rx("web")
    try:
        await radio.disconnect()
        assert driver.rx_running is False

        link.answer_identity = False  # the reconnect's gate reads silence
        await radio.connect()  # returns while the identity hold owns the link
        assert not radio.connected
        assert driver.rx_running is False  # nothing re-armed during the hold
        assert radio._serial_deferred_audio is not None
        assert _recovery_events(recovery_cb) == []

        link.answer_identity = True  # the re-read gets its answer
        assert await _wait_until(lambda: radio.connected, timeout_s=5.0)
        assert await _wait_until(lambda: driver.rx_running, timeout_s=5.0)
        assert driver.rx_starts == 2  # re-established exactly once, after
        assert radio._serial_deferred_audio is None
        assert _recovery_events(recovery_cb) == [
            AudioRecoveryState.RECOVERING,
            AudioRecoveryState.RECOVERED,
        ]
        driver.emit_rx_pcm(_PCM_FRAME)
        packet = await sub.get(timeout=1.0)
        assert packet is not None and packet.data == _PCM_FRAME
    finally:
        await sub.release()
        await radio.disconnect()


async def test_icom_serial_hard_reconnect_loop_recovers_held_demand_once() -> None:
    """A resident hard-reconnect round over the real ``connect()`` recovers
    the held demand exactly once — ``connect()`` itself must leave the
    recovery to the loop (the not-the-reconnect-task guard)."""
    recovery_cb = MagicMock()
    radio, driver, _link = _icom_serial_radio(recovery_cb)
    await radio.connect()
    session = radio.audio_session
    sub = await session.subscribe_rx("web")
    try:
        await radio.disconnect()
        radio._conn_state = RadioConnectionState.RECONNECTING
        radio._reconnect_task = asyncio.create_task(radio._reconnect_loop())
        await asyncio.wait_for(radio._reconnect_task, timeout=5.0)
        assert radio.connected is True

        assert driver.rx_running is True
        assert driver.rx_starts == 2  # the loop's own recovery, exactly once
        assert _recovery_events(recovery_cb) == [
            AudioRecoveryState.RECOVERING,
            AudioRecoveryState.RECOVERED,
        ]
        driver.emit_rx_pcm(_PCM_FRAME)
        packet = await sub.get(timeout=1.0)
        assert packet is not None and packet.data == _PCM_FRAME
    finally:
        await sub.release()
        await radio.disconnect()


async def test_icom_serial_manual_reestablish_recovers_held_rx_demand() -> None:
    """Positive control: ``AudioSession.reestablish()`` after ``connect()`` —
    the call the LAN recovery makes — restarts the driver RX leg in this
    fixture, so the strand assertion above is reachable.
    ``auto_recover_audio=False`` keeps the manual call the only recovery
    now that ``connect()`` recovers on its own."""
    radio, driver, _link = _icom_serial_radio()
    radio._auto_recover_audio = False
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


async def test_yaesu_cat_owner_reconnect_reestablishes_held_rx_demand() -> None:
    """Owner disconnect() + connect() on the Yaesu CAT backend
    re-establishes the RX demand the AudioSession still holds, exactly
    once (MOR-3436)."""
    radio, driver, fake = await _yaesu_radio()
    try:
        await radio.connect()
        session = radio.audio_session
        sub = await session.subscribe_rx("web")
        try:
            assert driver.rx_running is True
            assert driver.rx_starts == 1
            driver.inject_rx_frame(_PCM_FRAME)
            packet = await sub.get(timeout=1.0)
            assert packet is not None and packet.data == _PCM_FRAME

            await radio.disconnect()
            assert driver.rx_running is False
            assert session.rx_demand == 1  # the demand survives

            await radio.connect()
            assert radio.connected is True
            assert session.rx_demand == 1
            # The held RX demand is live again on the driver after the
            # owner connect — re-established exactly once.
            assert driver.rx_running is True
            assert driver.rx_starts == 2
            driver.inject_rx_frame(_PCM_FRAME)
            packet = await sub.get(timeout=1.0)
            assert packet is not None and packet.data == _PCM_FRAME
        finally:
            await sub.release()
            await radio.disconnect()
    finally:
        await fake.stop()


async def test_yaesu_cat_owner_connect_without_demand_runs_no_recovery() -> None:
    """No session demand held across the owner disconnect → the connect
    ends with the driver's RX leg down (and never creates a session)."""
    radio, driver, fake = await _yaesu_radio()
    try:
        await radio.connect()
        await radio.disconnect()
        await radio.connect()
        assert radio.connected is True
        assert driver.rx_running is False
        assert driver.rx_starts == 0
        assert radio._audio_session is None
        await radio.disconnect()
    finally:
        await fake.stop()


async def test_yaesu_cat_owner_stop_during_connect_runs_no_recovery() -> None:
    """Owner ``disconnect()`` landing while ``connect()`` seeds the state
    leaves no re-establishment behind: the generation the tail checks has
    moved."""
    radio, driver, fake = await _yaesu_radio()
    try:
        await radio.connect()
        session = radio.audio_session
        sub = await session.subscribe_rx("web")
        assert driver.rx_running is True

        in_seed = asyncio.Event()
        release_seed = asyncio.Event()

        async def _gated_seed() -> None:
            in_seed.set()
            await release_seed.wait()

        radio._seed_state_from_if = AsyncMock(side_effect=_gated_seed)

        # The tail must not even attempt the re-establishment after the
        # owner stop: spy on the session call the tail makes.
        reestablish_calls: list[int] = []
        real_reestablish = radio.audio_session.reestablish

        async def _spied_reestablish() -> None:
            reestablish_calls.append(1)
            await real_reestablish()

        radio.audio_session.reestablish = _spied_reestablish  # type: ignore[method-assign]

        await radio.disconnect()
        assert driver.rx_running is False

        connect_task = asyncio.create_task(radio.connect())
        await asyncio.wait_for(in_seed.wait(), timeout=5.0)
        await radio.disconnect()  # the owner stops the radio mid-connect
        release_seed.set()
        await asyncio.wait_for(connect_task, timeout=5.0)

        assert driver.rx_running is False  # nothing re-armed on the dead link
        assert reestablish_calls == []  # no attempt after the owner stop
        assert session.rx_demand == 1  # the demand stays held, un-armed
        await sub.release()
        await radio.disconnect()
    finally:
        await fake.stop()


async def test_yaesu_cat_identity_hold_reestablishes_once_after_completion() -> None:
    """A connect that returns into the identity hold re-establishes
    nothing until the hold completes, then exactly once."""
    radio, driver, fake = await _yaesu_radio()
    try:
        await radio.connect()
        session = radio.audio_session
        sub = await session.subscribe_rx("web")
        try:
            await radio.disconnect()
            assert driver.rx_running is False

            # The reconnect's ID read falls inside the silence window (the
            # transport query times out at ~1 s), then the re-read answers.
            fake.silence(1.2)
            await radio.connect()  # returns while the identity hold owns the port
            assert not radio.connected
            assert driver.rx_running is False  # nothing re-armed during the hold
            assert radio._audio_reestablish_generation is not None

            assert await _wait_until(lambda: radio.connected, timeout_s=5.0)
            assert driver.rx_running is True
            assert driver.rx_starts == 2  # re-established exactly once, after
            assert radio._audio_reestablish_generation is None
            driver.inject_rx_frame(_PCM_FRAME)
            packet = await sub.get(timeout=1.0)
            assert packet is not None and packet.data == _PCM_FRAME
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
