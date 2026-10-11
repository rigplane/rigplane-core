"""Serial identity/reconnect chain through the real backend (MOR-3211).

These tests close MOR-3064 with characterization, not another fix: the real
``Ic7300SerialRadio`` — the class ``rigplane web --backend serial --model
IC-7300`` resolves through ``backends/factory.py`` — is driven against
``CivSerialFake`` mounted through the production ``SerialCivLink``
``open_serial_connection`` seam, so the connect gate, the background identity
re-read, the watchdog's ``soft_reconnect`` and the epoch fences all run
unmodified. Only the cadence knobs the backend exposes for tests
(``_civ_get_timeout``, ``_SERIAL_*`` class attributes) are compressed; no
identity read is stubbed.

Covered tickets: MOR-3071/MOR-3078 (connected latches only on the identity
answer), MOR-3064 (connected only when the requested model answers; identity
read ownership across a reconnect), MOR-2861's silent-link half lives in
``test_serial_silent_link.py``.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Callable

import pytest

from rigplane import RadioConnectionState
from rigplane.backends.ic7300 import Ic7300SerialRadio
from rigplane.backends.icom7610.drivers.serial_civ_link import SerialCivLink
from rigplane.core.radio_protocol import RadioIdentity, RadioIdentityStatus
from rigplane.exceptions import ConnectionError as RigplaneConnectionError
from rigplane.profiles import resolve_radio_profile

from rigplane_testkit.serial import CivSerialFake

_DEVICE = "civ-serial-fake"  # synthetic node name; no real path is opened
_PROFILE = resolve_radio_profile(model="IC-7300")


def _radio_for(fake: CivSerialFake) -> Ic7300SerialRadio:
    """The CLI's serial construction with the fake behind the opener seam.

    ``factory.create_radio(SerialBackendConfig(model="IC-7300"))`` builds
    ``Ic7300SerialRadio(device=...)``; here the fake's opener replaces the OS
    port through the documented ``SerialCivLink(open_serial_connection=...)``
    seam. The empty enumeration keeps MOR-1453 rediscovery from ever touching
    real OS ports.
    """
    link = SerialCivLink(
        device=_DEVICE, open_serial_connection=fake.open_serial_connection
    )
    return Ic7300SerialRadio(
        device=_DEVICE,
        civ_link=link,
        _enumerate_serial_ports_fn=lambda: [],
    )


async def _wait_until(predicate: Callable[[], bool], *, timeout_s: float = 3.0) -> bool:
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        if predicate():
            return True
        await asyncio.sleep(0.01)
    return predicate()


def _identity(radio: Ic7300SerialRadio) -> RadioIdentity:
    identity = radio.connection_identity
    assert identity is not None
    return identity


async def test_delayed_identity_gates_connected_until_the_answer_arrives() -> None:
    """MOR-3071/MOR-3078: a late ``19 00`` answer holds CONNECTED off.

    With the fake answering the identity read 1.5 s late (inside the 2.0 s
    answer window ``_civ_get_timeout`` gets from the default 5 s timeout),
    ``connect()`` must not report connected before the answer arrives, and
    the identity it publishes must verify against the IC-7300 profile.
    """
    fake = CivSerialFake()
    fake.delay_identity_answer(1.5)
    radio = _radio_for(fake)

    started = time.monotonic()
    # The scripted answer cannot reach the gate before started + 1.5 s
    # (call_later arms when the fake sees the read), so every observation
    # taken before started + 1.4 s is provably pre-answer.
    answer_due = started + 1.4
    connect_task = asyncio.create_task(radio.connect())
    pre_answer_connected: list[bool] = []
    pre_answer_statuses: set[RadioIdentityStatus] = set()
    while not connect_task.done():
        if time.monotonic() < answer_due:
            pre_answer_connected.append(radio.connected)
            identity = radio.connection_identity
            if identity is not None:
                pre_answer_statuses.add(identity.status)
        await asyncio.sleep(0.025)
    await connect_task
    elapsed = time.monotonic() - started
    try:
        # The gate actually held the connect: pre-answer samples exist, none
        # reported connected, and the identity never left ``checking`` before
        # the answer could arrive. (After the answer, ``connect()`` still
        # runs the latch and the managed-TX arm round trips, so late samples
        # may legitimately show CONNECTED/verified — only pre-answer state
        # is the gate's claim.)
        assert pre_answer_connected, "connect() returned before the gate could hold it"
        assert not any(pre_answer_connected)
        assert pre_answer_statuses == {RadioIdentityStatus.CHECKING}
        assert elapsed >= 1.4

        assert radio.connected
        assert radio.conn_state is RadioConnectionState.CONNECTED
        identity = _identity(radio)
        assert identity.status is RadioIdentityStatus.VERIFIED
        assert identity.answered_id in _PROFILE.expected_identity_ids
        assert identity.answered_model == _PROFILE.model
        assert radio.last_error is None
    finally:
        await radio.disconnect()


async def test_identity_answered_only_after_reconnect_completes_on_reopen() -> None:
    """MOR-3078/MOR-3064: the first attempt holds; the reconnect completes it.

    The fake drops ``19 00`` on open ordinal 1 and answers from ordinal 2 on.
    The first ``connect()`` must return without latching CONNECTED (status
    ``no_response``, link handed to the background re-read), and a reopen —
    the operator's disconnect/connect — must run the gate again on the new
    link and finish ``verified``, never stranded in ``checking``.
    """
    fake = CivSerialFake()
    fake.answer_identity_after_reconnect_only()
    radio = _radio_for(fake)
    radio._civ_get_timeout = 0.3
    radio._SERIAL_IDENTITY_REREAD_BACKOFF_S = (0.05, 0.1)
    radio._SERIAL_IDENTITY_REREAD_STEADY_S = 0.2

    await radio.connect()  # held: the gate's answer window lapses on silence
    try:
        assert not radio.connected
        assert radio.conn_state is RadioConnectionState.CONNECTING
        identity = _identity(radio)
        assert identity.status is RadioIdentityStatus.NO_RESPONSE
        assert radio.last_error is not None and "No answer" in radio.last_error
        assert fake.open_count == 1

        await radio.disconnect()
        assert radio.connection_identity is None

        await radio.connect()  # ordinal 2: the fake now answers the gate
        assert radio.connected
        assert radio.conn_state is RadioConnectionState.CONNECTED
        identity = _identity(radio)
        assert identity.status is RadioIdentityStatus.VERIFIED
        assert identity.answered_id in _PROFILE.expected_identity_ids
        assert fake.open_count == 2
        assert radio.last_error is None
    finally:
        await radio.disconnect()


async def test_wrong_model_id_is_never_reported_as_the_requested_radio() -> None:
    """MOR-3064: connected only when the requested model answers.

    The fake answers ``19 00`` with another radio's native id (profile-derived
    from ``rigs/ic7610.toml``). The backend must refuse to latch CONNECTED,
    publish ``identity_mismatch`` with the id it actually heard, surface the
    mismatch in ``last_error``, and keep refusing operator commands while the
    background re-read keeps getting the same wrong answer.
    """
    fake = CivSerialFake()
    fake.wrong_model_id()
    radio = _radio_for(fake)
    radio._SERIAL_IDENTITY_REREAD_BACKOFF_S = (0.05, 0.05, 0.1)
    radio._SERIAL_IDENTITY_REREAD_STEADY_S = 0.1

    await radio.connect()  # the mismatch answer is immediate; no latch
    try:
        assert not radio.connected
        assert radio.conn_state is RadioConnectionState.CONNECTING
        identity = _identity(radio)
        assert identity.status is RadioIdentityStatus.IDENTITY_MISMATCH
        assert identity.answered_id == fake.model_id.hex().upper()
        assert identity.answered_id not in _PROFILE.expected_identity_ids
        assert identity.answered_model is None
        assert radio.last_error is not None and "does not match" in radio.last_error

        with pytest.raises(RigplaneConnectionError):
            await radio.get_freq()

        # Several re-read cycles later the verdict stands.
        await asyncio.sleep(0.35)
        assert not radio.connected
        assert _identity(radio).status is RadioIdentityStatus.IDENTITY_MISMATCH
    finally:
        await radio.disconnect()


async def test_usb_vanish_reappear_maps_the_error_and_repeats_the_gate() -> None:
    """MOR-3064/#3959: a vanished device reports ``serial_not_found``, and the
    identity read runs again — owned by the reconnect's gate — before CONNECTED
    latches on the reopened link.

    The fake raises ``ENODEV`` on every open connection and on new opens while
    vanished (``core/serial_open.py: serial_open_diagnostic`` maps that to
    ``serial_not_found``). The watchdog's ``soft_reconnect`` surfaces the
    mapped diagnostic in ``serial_open_error`` while it retries; once the
    device reappears, a later retry opens ordinal 2, re-reads the identity
    and latches CONNECTED only with a freshly verified answer.
    """
    fake = CivSerialFake()
    radio = _radio_for(fake)
    radio._civ_get_timeout = 0.2
    radio._SERIAL_WATCHDOG_INTERVAL_S = 0.02
    radio._SERIAL_WATCHDOG_RETRY_S = 0.05
    radio._SERIAL_WATCHDOG_RETRY_MAX_S = 0.1

    await radio.connect()
    assert radio.connected
    first_open_count = fake.open_count

    fake.usb_vanish()
    try:
        assert await _wait_until(
            lambda: (radio.serial_open_error or "").startswith("serial_not_found"),
            timeout_s=3.0,
        ), "the vanished device never surfaced as serial_not_found"
        assert not radio.connected
        assert radio.conn_state is RadioConnectionState.RECONNECTING

        fake.usb_reappear()
        assert await _wait_until(lambda: radio.connected, timeout_s=3.0), (
            "the watchdog never recovered the reappeared device"
        )
        assert radio.conn_state is RadioConnectionState.CONNECTED
        identity = _identity(radio)
        assert identity.status is RadioIdentityStatus.VERIFIED
        assert identity.answered_id in _PROFILE.expected_identity_ids
        assert fake.open_count > first_open_count  # a real reopen happened
        assert radio.serial_open_error is None  # cleared by the successful open
        # The recovered link carries traffic, not just flags.
        assert await radio.get_freq(bypass_cache=True) == fake.frequency_hz
    finally:
        await radio.disconnect()
