from __future__ import annotations

import time
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from rigplane.capabilities import CAP_ANTENNA, CAP_TUNER
from rigplane.core.state_pipeline_contracts import (
    CommandIntent,
    FieldPath,
    Observation,
    SourceMetadata,
)
from rigplane.core.state_store import StateStore
from rigplane.exceptions import CommandError
from rigplane.profiles import resolve_radio_profile
from rigplane.runtime._poller_types import (
    PttOff,
    PttOn,
    SendCiv,
    SetAntenna1,
    SetTunerStatus,
)
from rigplane.web.radio_poller import CommandQueue, CommandQueueEntry, RadioPoller


_PTT = FieldPath.global_("tx_state", "ptt")


def _radio() -> SimpleNamespace:
    return SimpleNamespace(
        profile=resolve_radio_profile(model="IC-7300"),
        capabilities={CAP_ANTENNA, CAP_TUNER},
        supports_command=lambda _name, *, receiver=None: receiver is None,
        send_civ=AsyncMock(),
        set_antenna_1=AsyncMock(),
        set_ptt=AsyncMock(),
    )


def _observe_tx(store: StateStore) -> None:
    store.apply(
        Observation(
            path=_PTT,
            value=True,
            source=SourceMetadata(source="poll_response", provider="test"),
            timestamp_monotonic=time.monotonic(),
            max_age=1.0,
            provider_generation=store.provider_generation,
        )
    )


def _poller(
    *, admitted: bool = True, composed: bool = True
) -> tuple[RadioPoller, SimpleNamespace, AsyncMock, StateStore]:
    radio = _radio()
    store = StateStore()
    store.begin_provider_generation()
    authority = SimpleNamespace(admit_managed_write=AsyncMock(return_value=admitted))
    port = SimpleNamespace(authority=authority) if composed else None
    poller = RadioPoller(
        radio,
        CommandQueue(),
        state_store=store,
        managed_tx_port=port,
    )
    return poller, radio, authority.admit_managed_write, store


async def test_composed_mapped_write_bypasses_observed_rf_after_admission() -> None:
    poller, radio, admit, store = _poller()
    _observe_tx(store)

    await poller._execute(  # noqa: SLF001
        SetAntenna1(True),
        command_id="cmd-1",
        source="websocket",
        session_id="ws-1",
    )

    admitted = admit.await_args.args[0]
    assert admitted.name == "set_antenna_1"
    assert admitted.id == "cmd-1"
    admit.assert_awaited_once()
    radio.set_antenna_1.assert_awaited_once_with(True)


def test_composed_mapped_write_bypasses_legacy_deferred_staging() -> None:
    poller, _radio, admit, store = _poller()
    _observe_tx(store)
    entry = CommandQueueEntry(SetTunerStatus(1), command_id="cmd-tuner")

    assert poller._stage_entry_for_turn(entry) == [entry]  # noqa: SLF001

    admit.assert_not_awaited()


def test_unmanaged_tuner_engage_is_not_deferred() -> None:
    poller, _radio, admit, store = _poller(composed=False)
    _observe_tx(store)
    entry = CommandQueueEntry(SetTunerStatus(1), command_id="cmd-tuner")

    assert poller._stage_entry_for_turn(entry) == [entry]  # noqa: SLF001

    admit.assert_not_awaited()


async def test_composed_descriptor_write_is_admitted_exactly_once() -> None:
    poller, radio, admit, store = _poller()
    _observe_tx(store)
    intent = CommandIntent(
        id="cmd-2",
        name="set_antenna_1",
        params={"on": False, "rx_antenna_1": False},
        source="websocket",
        target=FieldPath.global_("slow_state", "rx_antenna_1"),
        timeout=3.0,
    )

    await poller._execute(intent)  # noqa: SLF001

    admit.assert_awaited_once_with(intent)
    radio.set_antenna_1.assert_awaited_once_with(on=False)


async def test_noncanonical_intent_fails_before_admission_or_radio_io() -> None:
    poller, radio, admit, _store = _poller()
    intent = CommandIntent(
        id="cmd-unknown",
        name="unknown_write",
        params={},
        source="websocket",
        target=None,
        timeout=3.0,
    )

    with pytest.raises(CommandError, match="no canonical command descriptor"):
        await poller._execute(intent)  # noqa: SLF001

    admit.assert_not_awaited()
    radio.set_antenna_1.assert_not_awaited()


async def test_rejected_mapped_write_fails_before_radio_io() -> None:
    poller, radio, admit, _store = _poller(admitted=False)

    with pytest.raises(CommandError, match="authority refused write"):
        await poller._execute(  # noqa: SLF001
            SetAntenna1(True),
            command_id="cmd-rejected",
        )

    admit.assert_awaited_once()
    radio.set_antenna_1.assert_not_awaited()


def test_required_managed_construction_without_port_fails_closed() -> None:
    radio = _radio()

    with pytest.raises(CommandError, match="managed transmit authority unavailable"):
        RadioPoller(
            radio,
            CommandQueue(),
            managed_tx_required=True,
        )

    radio.set_antenna_1.assert_not_awaited()


async def test_explicit_unmanaged_construction_retains_legacy_interlock() -> None:
    poller, radio, admit, store = _poller(composed=False)
    _observe_tx(store)

    with pytest.raises(CommandError, match="RF state is TX"):
        await poller._execute(  # noqa: SLF001
            SetAntenna1(True),
            command_id="cmd-unmanaged",
        )

    admit.assert_not_awaited()
    radio.set_antenna_1.assert_not_awaited()


async def test_raw_command_keeps_legacy_interlock_when_port_is_composed() -> None:
    poller, radio, admit, store = _poller()
    _observe_tx(store)

    with pytest.raises(CommandError, match="RF state is TX"):
        await poller._execute(  # noqa: SLF001
            SendCiv(command=0x1A, data=b"\x01"),
            command_id="cmd-raw",
        )

    admit.assert_not_awaited()
    radio.send_civ.assert_not_awaited()


@pytest.mark.parametrize("command", [PttOn(), PttOff()])
async def test_composed_ptt_never_falls_through_non_ptt_admission_or_raw_io(
    command: PttOn | PttOff,
) -> None:
    poller, radio, admit, _store = _poller()

    with pytest.raises(CommandError, match="PTT must be submitted through authority"):
        await poller._execute(  # noqa: SLF001
            command,
            command_id="cmd-ptt",
            source="websocket",
            session_id="ws-1",
        )

    admit.assert_not_awaited()
    radio.set_ptt.assert_not_awaited()
