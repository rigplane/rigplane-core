"""MOR-4660: a USB voice key whose MOD input will not carry the audio warns.

Bench 2026-11-21 (IC-705 over USB, Pro beta.20-rc.1): the radio keyed, no
voice went out — the station pushed TX PCM over USB while the radio's DATA
OFF MOD input was WLAN (CI-V ``1A 05 01 18`` = 3). The owner/adviser shape
after PR #4756 was parked is a WARNING, never a refusal: an accepted
managed PTT in a voice mode over a USB TX audio link reads the
transmitting VFO's DATA-OFF modulation source and tells the operator when
it is not the profile's USB value — or when it cannot be read.

Two tiers, both through web-layer seams (the parked PR's lesson: not
CAT-only):

- the managed-PTT ingress (``ControlHandler._enqueue_command``) with real
  profiles and a scripted radio getter: the full gate matrix, the
  never-delayed/never-refused PTT proof, cancellation and split;
- the CI-V simulator (``MockIcomRadio`` over real UDP) behind the
  production ``IcomRadio``: the actual ``1A 05`` exchange per profile, a
  real NAK, and the USB value mapping end to end.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from contextlib import asynccontextmanager
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from rigplane.core.exceptions import CommandRejectedError
from rigplane.core.state_pipeline_contracts import (
    FieldPath,
    Observation,
    SourceMetadata,
)
from rigplane.core.state_store import FreshnessClock, StateStore
from rigplane.profiles import resolve_radio_profile
from rigplane.runtime.managed_tx_state import ManagedTxOutcome
from rigplane.web.handlers.control import ControlHandler
from rigplane.web.mod_input_warning import (
    MOD_INPUT_NOT_USB_CODE,
    MOD_INPUT_UNREADABLE_CODE,
    warn_when_mod_input_blocks_voice,
)
from rigplane_testkit.serial import CivSerialFake

_LOGGER_NAME = "rigplane.web.mod_input_warning"

#: (model, CI-V/model address, DATA-OFF MOD sub-address, a non-USB value
#: on that model, the model's USB value, that value's source name).
#: The IC-705 row is the bench incident byte for byte (WLAN = 3); the
#: IC-7300/IC-9700 rows prove the per-profile USB value mapping — 3 is
#: WLAN on the IC-705 and USB on the IC-9700.
_SIMULATOR_ROWS: tuple[tuple[str, int, bytes, int, int, str], ...] = (
    ("IC-705", 0xA4, b"\x01\x18", 3, 1, "WLAN"),
    ("IC-7300", 0x94, b"\x00\x66", 0, 3, "MIC"),
    ("IC-9700", 0xA2, b"\x01\x15", 1, 3, "ACC"),
)


class _Submission:
    outcome: ManagedTxOutcome

    def __init__(self, outcome: ManagedTxOutcome) -> None:
        self.outcome = outcome

    async def wait_settlement(self) -> _Submission:
        raise AssertionError("Web PTT admission waited for provider settlement")


class _Authority:
    """The managed-TX authority double of test_web_managed_tx_ingress."""

    def __init__(self) -> None:
        self.calls: list[tuple[bool, str]] = []
        self.next_outcome = ManagedTxOutcome.ACCEPTED
        self.intent = "rx"

    def start_ptt_submission(
        self,
        on: bool,
        owner: str,
        *,
        ready: asyncio.Future[None],
        expires_at_monotonic: float,
    ) -> asyncio.Task[_Submission]:
        self.calls.append((on, owner))
        submission = _Submission(self.next_outcome)

        async def complete() -> _Submission:
            await ready
            if submission.outcome is ManagedTxOutcome.ACCEPTED:
                self.intent = f"ptt:{owner}" if on else "rx"
            return submission

        del expires_at_monotonic
        return asyncio.create_task(complete())

    async def submit_ptt(self, on: bool, owner: str) -> _Submission:
        self.calls.append((on, owner))
        submission = _Submission(self.next_outcome)
        if submission.outcome is ManagedTxOutcome.ACCEPTED:
            self.intent = f"ptt:{owner}" if on else "rx"
        return submission

    async def submit_force_off(self) -> _Submission:
        return _Submission(self.next_outcome)

    async def owner_disconnect(self, owner: str) -> ManagedTxOutcome:
        outcome = (
            ManagedTxOutcome.ACCEPTED
            if self.intent == f"ptt:{owner}"
            else ManagedTxOutcome.REJECTED
        )
        if outcome is ManagedTxOutcome.ACCEPTED:
            self.intent = "rx"
        return outcome


class _Toasts:
    """Captured session notifications."""

    def __init__(self) -> None:
        self.sent: list[tuple[str, str, str, str | None, dict[str, Any] | None]] = []

    def __call__(
        self,
        session_id: str,
        level: str,
        message: str,
        category: str = "system",
        *,
        code: str | None = None,
        params: dict[str, Any] | None = None,
    ) -> bool:
        self.sent.append((level, message, category, code, params))
        return True

    def codes(self) -> list[str | None]:
        return [row[3] for row in self.sent]


def _seeded_store(
    *,
    clock: FreshnessClock | None = None,
    mode: str | None = "USB",
    data_mode: int | None = 0,
    split: bool | None = False,
    active_slot: str | None = "A",
    unselected_mode: str | None = None,
    unselected_data_mode: int | None = None,
    stale: bool = False,
) -> StateStore:
    """A store whose primary receiver is observed exactly as asked."""

    clock = clock if clock is not None else FreshnessClock(start=10.0)
    store = StateStore(freshness_clock=clock)

    def apply(path: FieldPath, value: object) -> None:
        store.apply(
            Observation(
                path=path,
                value=value,
                source=SourceMetadata(source="poll_response", provider="test"),
                timestamp_monotonic=clock.now(),
                max_age=60.0,
            )
        )

    for receiver_id in ("0", "main"):
        if active_slot is not None:
            apply(FieldPath.active_slot(receiver_id), active_slot)
        if mode is not None:
            apply(FieldPath.active(receiver_id, "freq_mode", "mode"), mode)
        if data_mode is not None:
            apply(FieldPath.active(receiver_id, "freq_mode", "data_mode"), data_mode)
        if unselected_mode is not None:
            apply(
                FieldPath.unselected(receiver_id, "freq_mode", "mode"), unselected_mode
            )
        if unselected_data_mode is not None:
            apply(
                FieldPath.unselected(receiver_id, "freq_mode", "data_mode"),
                unselected_data_mode,
            )
    if split is not None:
        apply(FieldPath.global_("tx_state", "split"), split)
    if stale:
        store.mark_stale_due(now=clock.now() + 120.0)
    return store


def _radio(
    model: str,
    *,
    getter: Any,
    backend_id: str | None = "icom_serial",
) -> MagicMock:
    profile = resolve_radio_profile(model=model)
    radio = MagicMock()
    radio.profile = profile
    radio.model = profile.model
    radio.capabilities = set(profile.capabilities)
    radio.connected = True
    radio.radio_ready = True
    radio.set_ptt = AsyncMock()
    radio.get_data_off_mod_input = getter
    if backend_id is not None:
        radio.backend_id = backend_id
    return radio


class _Ingress:
    """One ControlHandler wired like test_web_managed_tx_ingress' harness."""

    def __init__(
        self,
        model: str,
        *,
        getter: Any,
        store: StateStore | None = None,
        backend_id: str | None = "icom_serial",
        with_notification: bool = True,
    ) -> None:
        self.authority = _Authority()
        self.toasts = _Toasts() if with_notification else None
        self.retained: set[asyncio.Task[Any]] = set()
        queue = MagicMock()
        server = SimpleNamespace(
            command_queue=queue,
            command_state_store=(store if store is not None else _seeded_store()),
            register_control_event_queue=MagicMock(return_value={}),
            unregister_control_event_queue=MagicMock(),
            build_state_update_envelope=MagicMock(return_value={}),
            _managed_tx_authority=lambda: self.authority,
            enqueue_managed_positive_tx=self._enqueue_positive_tx,
            _spawn=self._spawn,
        )
        if self.toasts is not None:
            server.send_notification_to_session = self.toasts
        self.server = server
        self.radio = _radio(model, getter=getter, backend_id=backend_id)
        self.handler = ControlHandler(
            ws=MagicMock(outbound_progress=0),
            radio=self.radio,
            server_version="test",
            radio_model=model,
            server=server,
            session_id="websocket-test",
            managed_tx_authority=self.authority,
        )

    def _spawn(self, coro: Any) -> asyncio.Task[Any]:
        task = asyncio.create_task(coro)
        self.retained.add(task)
        task.add_done_callback(self.retained.discard)
        return task

    async def _enqueue_positive_tx(
        self,
        *,
        ready: asyncio.Future[None],
        submission: asyncio.Task[_Submission],
        **_kwargs: Any,
    ) -> _Submission:
        ready.set_result(None)
        return await submission

    async def drain(self) -> None:
        """Let every spawned check task finish (no wall-clock waits)."""
        pending = [task for task in self.retained if not task.done()]
        if pending:
            await asyncio.wait(pending)


@pytest.fixture()
def usb_getter() -> AsyncMock:
    return AsyncMock(return_value=3)


async def _ptt_on(ingress: _Ingress) -> dict[str, Any]:
    result = await ingress.handler._enqueue_command("ptt", {"state": True})
    await ingress.drain()
    return result


# ---------------------------------------------------------------------------
# The warning itself
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_ic705_mod_not_usb_warns_the_keying_session(
    usb_getter: AsyncMock, caplog: pytest.LogCaptureFixture
) -> None:
    """The bench incident: voice over USB with DATA OFF MOD = WLAN (3)."""
    with caplog.at_level(logging.DEBUG, logger=_LOGGER_NAME):
        ingress = _Ingress("IC-705", getter=usb_getter)
        assert await _ptt_on(ingress) == {"state": True}
    assert ingress.authority.calls == [(True, "websocket-test")]
    assert ingress.toasts.codes() == [MOD_INPUT_NOT_USB_CODE]
    level, message, category, code, params = ingress.toasts.sent[0]
    assert level == "warning"
    assert category == "audio"
    assert params == {"source": "WLAN", "expected": "USB"}
    assert "voice will not go out" in message
    assert any(
        record.levelno == logging.WARNING and "WLAN" in record.message
        for record in caplog.records
    )


@pytest.mark.asyncio
async def test_ic705_mod_usb_is_silent(usb_getter: AsyncMock) -> None:
    ingress = _Ingress("IC-705", getter=usb_getter)
    usb_getter.return_value = 1  # the IC-705's USB value
    await _ptt_on(ingress)
    usb_getter.assert_awaited_once()
    assert ingress.toasts.sent == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("model", "not_usb", "usb", "source"),
    (
        ("IC-7300", 1, 3, "ACC"),
        ("IC-9700", 1, 3, "ACC"),
        ("IC-7300MK2", 0, 1, "MIC"),
        ("IC-7610", 1, 3, "ACC"),
        ("IC-905", 3, 1, "LAN"),
    ),
)
async def test_per_profile_usb_value_mapping(
    usb_getter: AsyncMock, model: str, not_usb: int, usb: int, source: str
) -> None:
    """The USB value comes from profile data: 3 warns on an IC-905 and is
    USB on an IC-9700; 1 is ACC on an IC-7300 and USB on an IC-7300MK2."""
    ingress = _Ingress(model, getter=usb_getter)
    usb_getter.return_value = not_usb
    await _ptt_on(ingress)
    assert ingress.toasts.codes() == [MOD_INPUT_NOT_USB_CODE]
    assert ingress.toasts.sent[0][4] == {"source": source, "expected": "USB"}

    ingress_ok = _Ingress(model, getter=AsyncMock(return_value=usb))
    await _ptt_on(ingress_ok)
    assert ingress_ok.toasts.sent == []


@pytest.mark.asyncio
@pytest.mark.parametrize("model", ("FT-991A", "X6100", "G7250"))
async def test_profile_without_mod_input_stays_silent(
    usb_getter: AsyncMock, model: str
) -> None:
    """Yaesu/Xiegu profiles declare no modulation-input setting; the check
    is skipped by data, not by radio name."""
    ingress = _Ingress(model, getter=usb_getter)
    await _ptt_on(ingress)
    usb_getter.assert_not_awaited()
    assert ingress.toasts.sent == []


@pytest.mark.asyncio
async def test_unreadable_mod_input_warns_with_reason(
    caplog: pytest.LogCaptureFixture,
) -> None:
    getter = AsyncMock(side_effect=TimeoutError("civ timeout"))
    with caplog.at_level(logging.DEBUG, logger=_LOGGER_NAME):
        ingress = _Ingress("IC-705", getter=getter)
        await _ptt_on(ingress)
    assert ingress.toasts.codes() == [MOD_INPUT_UNREADABLE_CODE]
    level, message, category, code, params = ingress.toasts.sent[0]
    assert level == "warning"
    assert params == {"reason": "TimeoutError"}
    assert any(record.levelno == logging.WARNING for record in caplog.records)


# ---------------------------------------------------------------------------
# The gate matrix: when the check must stay silent
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "mode", ("CW", "CW-R", "RTTY", "WFM", "DV"), ids=lambda mode: mode
)
async def test_non_voice_modes_stay_silent(usb_getter: AsyncMock, mode: str) -> None:
    ingress = _Ingress(
        "IC-705", getter=usb_getter, store=_seeded_store(mode=mode, data_mode=0)
    )
    await _ptt_on(ingress)
    usb_getter.assert_not_awaited()
    assert ingress.toasts.sent == []


@pytest.mark.asyncio
async def test_data_mode_on_stays_silent(usb_getter: AsyncMock) -> None:
    """USB-D is a data key: the DATA MOD source applies, out of scope."""
    ingress = _Ingress(
        "IC-705", getter=usb_getter, store=_seeded_store(mode="USB", data_mode=1)
    )
    await _ptt_on(ingress)
    usb_getter.assert_not_awaited()
    assert ingress.toasts.sent == []


@pytest.mark.asyncio
async def test_lan_route_stays_silent(usb_getter: AsyncMock) -> None:
    """A LAN link carries TX audio over LAN; the USB check does not apply."""
    ingress = _Ingress("IC-705", getter=usb_getter, backend_id="rigplane")
    await _ptt_on(ingress)
    usb_getter.assert_not_awaited()
    assert ingress.toasts.sent == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("store_kwargs", "label"),
    (
        ({"mode": None}, "mode-unobserved"),
        ({"active_slot": None}, "slot-unobserved"),
        ({"split": None}, "split-unobserved"),
        ({"stale": True}, "stale"),
    ),
)
async def test_unusable_state_stays_silent(
    usb_getter: AsyncMock, store_kwargs: dict[str, Any], label: str
) -> None:
    ingress = _Ingress("IC-705", getter=usb_getter, store=_seeded_store(**store_kwargs))
    await _ptt_on(ingress)
    usb_getter.assert_not_awaited()
    assert ingress.toasts.sent == []


@pytest.mark.asyncio
async def test_split_reads_the_transmitting_vfo(usb_getter: AsyncMock) -> None:
    """Split transmits on the OTHER slot: a CW unselected VFO under a
    USB-voice selected VFO is a CW key and stays silent; the reverse
    (voice unselected, CW selected) warns."""
    silent = _Ingress(
        "IC-705",
        getter=usb_getter,
        store=_seeded_store(
            mode="USB",
            data_mode=0,
            split=True,
            unselected_mode="CW",
            unselected_data_mode=0,
        ),
    )
    await _ptt_on(silent)
    usb_getter.assert_not_awaited()

    warning = _Ingress(
        "IC-705",
        getter=AsyncMock(return_value=3),
        store=_seeded_store(
            mode="CW",
            data_mode=0,
            split=True,
            unselected_mode="USB",
            unselected_data_mode=0,
        ),
    )
    await _ptt_on(warning)
    assert warning.toasts.codes() == [MOD_INPUT_NOT_USB_CODE]


# ---------------------------------------------------------------------------
# PTT is never delayed, refused, or ordered around the check
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.timeout(10)
async def test_ptt_is_never_delayed_or_refused_by_the_check(
    virtual_clock: Any,
) -> None:
    """A getter that never answers cannot hold the keying: admission
    returns without advancing the clock past the read, and the PTT call
    records an accepted key."""

    gate = asyncio.Event()

    async def blocked_getter() -> int:
        await gate.wait()
        return 3

    ingress = _Ingress("IC-705", getter=blocked_getter)
    async with asyncio.timeout(1.0):
        assert await ingress.handler._enqueue_command("ptt", {"state": True}) == {
            "state": True
        }
    assert not any(task.done() for task in ingress.retained)
    assert ingress.authority.calls == [(True, "websocket-test")]
    gate.set()
    await ingress.drain()
    # The read completes only now, after the keying long returned.
    assert ingress.toasts.codes() == [MOD_INPUT_NOT_USB_CODE]


@pytest.mark.asyncio
async def test_refused_ptt_runs_no_check(usb_getter: AsyncMock) -> None:
    ingress = _Ingress("IC-705", getter=usb_getter)
    ingress.authority.next_outcome = ManagedTxOutcome.REJECTED
    with pytest.raises(CommandRejectedError):
        await ingress.handler._enqueue_command("ptt", {"state": True})
    usb_getter.assert_not_awaited()
    assert ingress.toasts.sent == []


@pytest.mark.asyncio
async def test_ptt_off_runs_no_check(usb_getter: AsyncMock) -> None:
    ingress = _Ingress("IC-705", getter=usb_getter)
    assert await ingress.handler._enqueue_command("ptt", {"state": False}) == {
        "state": False
    }
    usb_getter.assert_not_awaited()


@pytest.mark.asyncio
async def test_cancelled_check_neither_warns_nor_leaks(
    caplog: pytest.LogCaptureFixture,
) -> None:
    gate = asyncio.Event()

    async def blocked_getter() -> int:
        await gate.wait()
        return 3

    with caplog.at_level(logging.DEBUG, logger=_LOGGER_NAME):
        ingress = _Ingress("IC-705", getter=blocked_getter)
        await ingress.handler._enqueue_command("ptt", {"state": True})
    (task,) = tuple(ingress.retained)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert ingress.toasts.sent == []
    assert not any(record.levelno >= logging.ERROR for record in caplog.records)


@pytest.mark.asyncio
async def test_a_broken_check_is_contained_and_logged(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A gate bug must die in the detached task, never in the event loop."""

    def boom() -> int:
        raise RuntimeError("gate exploded")

    ingress = _Ingress("IC-705", getter=AsyncMock(side_effect=boom))
    with caplog.at_level(logging.DEBUG, logger=_LOGGER_NAME):
        await _ptt_on(ingress)
    assert any(
        record.levelno == logging.WARNING and "mod-input" in record.message
        for record in caplog.records
    )
    assert not any(record.levelno >= logging.ERROR for record in caplog.records)


@pytest.mark.asyncio
async def test_without_a_session_path_the_log_line_remains(
    caplog: pytest.LogCaptureFixture,
) -> None:
    getter = AsyncMock(return_value=3)
    with caplog.at_level(logging.DEBUG, logger=_LOGGER_NAME):
        ingress = _Ingress("IC-705", getter=getter, with_notification=False)
        await _ptt_on(ingress)
    assert ingress.toasts is None
    assert any(
        record.levelno == logging.WARNING and "WLAN" in record.message
        for record in caplog.records
    )


# ---------------------------------------------------------------------------
# The wire: the production serial backend against the CI-V fake
# ---------------------------------------------------------------------------


@asynccontextmanager
async def _serial_stack(model: str, civ_address: int):
    """A production USB-connected radio: the model's own serial backend
    over ``CivSerialFake``'s in-memory duplex.

    This is the bench incident's shape — an Icom on USB, control and TX
    audio on the same link — so ``backend_id`` is ``icom_serial`` and the
    resolved route is USB without any test doubles in between. The
    MOD-input menu items are served through the fake's ``hold`` registers
    (the echo model: a bare ``1A 05 <item>`` GET answers the stored
    value byte, a SET stores it); values 0-5 encode as themselves in BCD.
    """
    from rigplane.backends.ic705.serial import Ic705SerialRadio
    from rigplane.backends.ic7300.serial import Ic7300SerialRadio
    from rigplane.backends.ic9700.serial import Ic9700SerialRadio
    from rigplane.backends.icom7610.drivers.serial_civ_link import SerialCivLink

    radio_class = {
        "IC-705": Ic705SerialRadio,
        "IC-7300": Ic7300SerialRadio,
        "IC-9700": Ic9700SerialRadio,
    }[model]
    fake = CivSerialFake(model=model, model_id=bytes((civ_address,)))
    link = SerialCivLink(
        device="fake-tty", open_serial_connection=fake.open_serial_connection
    )
    radio = radio_class(device="fake-tty", civ_link=link, model=model)
    try:
        await radio.connect()
        yield fake, radio
    finally:
        with contextlib.suppress(Exception):
            await radio.disconnect()


def _capture_server(store: StateStore, toasts: _Toasts) -> SimpleNamespace:
    return SimpleNamespace(
        command_state_store=store, send_notification_to_session=toasts
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("model", "civ_address", "sub_address", "not_usb", "usb", "source"),
    _SIMULATOR_ROWS,
    ids=("IC-705", "IC-7300", "IC-9700"),
)
async def test_the_real_wire_read_across_profiles(
    model: str,
    civ_address: int,
    sub_address: bytes,
    not_usb: int,
    usb: int,
    source: str,
) -> None:
    """The production read of the DATA-OFF MOD input per profile: the
    IC-705 row is the bench incident byte for byte (WLAN = 3 warns), and
    the IC-9700 row proves the mapping is per-profile data (3 is USB
    there, so it clears)."""
    async with _serial_stack(model, civ_address) as (fake, radio):
        item = bytes((0x1A, 0x05)) + sub_address
        fake.hold(item, bytes((not_usb,)))
        toasts = _Toasts()
        await warn_when_mod_input_blocks_voice(
            _capture_server(_seeded_store(), toasts), radio, "websocket-test"
        )
        assert toasts.codes() == [MOD_INPUT_NOT_USB_CODE]
        assert toasts.sent[0][4] == {"source": source, "expected": "USB"}

        fake.hold(item, bytes((usb,)))
        clear = _Toasts()
        await warn_when_mod_input_blocks_voice(
            _capture_server(_seeded_store(), clear), radio, "websocket-test"
        )
        assert clear.sent == []


@pytest.mark.asyncio
async def test_an_unanswered_menu_read_warns_as_unreadable() -> None:
    """A menu item the radio does not answer (here: never held, so the
    fake NAKs) is a read failure: it warns with the reason instead of
    failing silently."""
    async with _serial_stack("IC-705", 0xA4) as (_fake, radio):
        toasts = _Toasts()
        await warn_when_mod_input_blocks_voice(
            _capture_server(_seeded_store(), toasts), radio, "websocket-test"
        )
        assert toasts.codes() == [MOD_INPUT_UNREADABLE_CODE]
        assert toasts.sent[0][4] == {"reason": "CommandRejectedError"}
