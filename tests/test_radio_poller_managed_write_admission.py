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
    SelectVfo,
    SendCiv,
    SetAntenna1,
    SetBand,
    SetFreq,
    SetMode,
    SetTunerStatus,
)
from rigplane.runtime.managed_tx_state import ManagedTxOutcome
from rigplane.runtime.managed_queue_boundary import ManagedQueuedWriteBoundary
from rigplane.web.radio_poller import CommandQueue, CommandQueueEntry, RadioPoller
from test_managed_tx_authority import authority


_PTT = FieldPath.global_("tx_state", "ptt")


def _radio() -> SimpleNamespace:
    return SimpleNamespace(
        profile=resolve_radio_profile(model="IC-7300"),
        capabilities={CAP_ANTENNA, CAP_TUNER},
        supports_command=lambda _name, *, receiver=None: receiver is None,
        send_civ=AsyncMock(),
        set_antenna_1=AsyncMock(),
        set_ptt=AsyncMock(),
        set_tuner_status=AsyncMock(),
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


class _Runner:
    async def run(self, operation):
        return await operation(lambda: True)


def _boundary_for_test() -> tuple[ManagedQueuedWriteBoundary, SimpleNamespace, _Runner]:
    runner = _Runner()
    managed = SimpleNamespace(admit_managed_write=AsyncMock(return_value=True))
    boundary = ManagedQueuedWriteBoundary(
        SimpleNamespace(authority=managed, local_tx_work_runner=runner),
        session_is_live=lambda _session: True,
    )
    return boundary, managed, runner


def _poller(
    *, admitted: bool = True, composed: bool = True
) -> tuple[RadioPoller, SimpleNamespace, AsyncMock, StateStore]:
    radio = _radio()
    store = StateStore()
    store.begin_provider_generation()
    managed = SimpleNamespace(admit_managed_write=AsyncMock(return_value=admitted))
    poller = RadioPoller(
        radio,
        CommandQueue(),
        state_store=store,
    )
    if composed:
        port = SimpleNamespace(authority=managed, local_tx_work_runner=_Runner())
        poller.attach_managed_queue_boundary(
            ManagedQueuedWriteBoundary(port, session_is_live=lambda _session: True),
            required=True,
        )
    return poller, radio, managed.admit_managed_write, store


async def test_composed_mapped_write_bypasses_observed_rf_after_admission() -> None:
    poller, radio, admit, store = _poller()
    _observe_tx(store)

    await poller._execute_queued_entry_action(  # noqa: SLF001
        CommandQueueEntry(
            SetAntenna1(True),
            command_id="cmd-1",
            source="websocket",
            session_id="ws-1",
        )
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


@pytest.mark.parametrize(
    "command",
    [SetFreq(7_100_000), SetMode("USB"), SetBand(5), SelectVfo("A")],
)
async def test_managed_ordinary_writes_bypass_legacy_staging_and_rf_policy(
    command: object,
) -> None:
    poller, _radio, admit, store = _poller()
    _observe_tx(store)
    execute = AsyncMock()
    poller._execute = execute  # type: ignore[method-assign]  # noqa: SLF001
    entry = CommandQueueEntry(command)

    assert poller._stage_entry_for_turn(entry) == [entry]  # noqa: SLF001
    await poller._execute_queued_entry_action(entry)  # noqa: SLF001

    execute.assert_awaited_once_with(
        command,
        command_id=None,
        source="websocket",
        session_id=None,
        command_service=None,
        _managed_boundary_applied=True,
    )
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

    await poller._execute_queued_entry_action(CommandQueueEntry(intent))  # noqa: SLF001

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
        await poller._execute_queued_entry_action(  # noqa: SLF001
            CommandQueueEntry(intent)
        )

    admit.assert_not_awaited()
    radio.set_antenna_1.assert_not_awaited()


async def test_rejected_mapped_write_fails_before_radio_io() -> None:
    poller, radio, admit, _store = _poller(admitted=False)

    with pytest.raises(CommandError, match="authority refused write"):
        await poller._execute_queued_entry_action(  # noqa: SLF001
            CommandQueueEntry(SetAntenna1(True), command_id="cmd-rejected")
        )

    admit.assert_awaited_once()
    radio.set_antenna_1.assert_not_awaited()


@pytest.mark.parametrize(("value", "admitted"), [(0, True), (1, False), (2, False)])
async def test_real_authority_applies_tuner_value_policy_before_typed_radio_io(
    value: int, admitted: bool
) -> None:
    managed, _, _, _, _, _ = authority()
    radio = _radio()
    store = StateStore()
    store.begin_provider_generation()
    poller = RadioPoller(
        radio,
        CommandQueue(),
        state_store=store,
    )
    poller.attach_managed_queue_boundary(
        ManagedQueuedWriteBoundary(
            SimpleNamespace(authority=managed, local_tx_work_runner=_Runner()),
            session_is_live=lambda _session: True,
        ),
        required=True,
    )
    try:
        assert await managed.transmit_on() is ManagedTxOutcome.ACCEPTED
        if admitted:
            await poller._execute_queued_entry_action(  # noqa: SLF001
                CommandQueueEntry(
                    SetTunerStatus(value),
                    command_id=f"cmd-tuner-{value}",
                    source="websocket",
                )
            )
            radio.set_tuner_status.assert_awaited_once_with(value)
        else:
            with pytest.raises(CommandError, match="authority refused write"):
                await poller._execute_queued_entry_action(  # noqa: SLF001
                    CommandQueueEntry(
                        SetTunerStatus(value),
                        command_id=f"cmd-tuner-{value}",
                        source="websocket",
                    )
                )
            radio.set_tuner_status.assert_not_awaited()
    finally:
        await managed.force_off()
        await managed.close()


def test_managed_boundary_attachment_preserves_exact_identity() -> None:
    poller, _radio, _admit, _store = _poller(composed=False)
    first, _authority, _runner = _boundary_for_test()
    second, _authority2, _runner2 = _boundary_for_test()

    poller.attach_managed_queue_boundary(first, required=True)

    assert poller.managed_queue_boundary is first
    with pytest.raises(RuntimeError, match="already attached"):
        poller.attach_managed_queue_boundary(second, required=True)


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
        await poller._execute_queued_entry_action(  # noqa: SLF001
            CommandQueueEntry(SendCiv(command=0x1A, data=b"\x01"), command_id="cmd-raw")
        )

    admit.assert_not_awaited()
    radio.send_civ.assert_not_awaited()
