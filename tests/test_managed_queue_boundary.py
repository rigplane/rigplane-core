from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from rigplane.core.state_pipeline_contracts import CommandIntent
from rigplane.exceptions import CommandError
from rigplane.runtime._poller_types import (
    CommandQueueEntry,
    PttOff,
    PttOn,
    ScanStart,
    SendCiv,
    SetFreq,
    SetTunerStatus,
)
from rigplane.runtime.managed_queue_boundary import ManagedQueuedWriteBoundary
from rigplane.runtime.managed_tx_state import ManagedTxOutcome


class _Runner:
    def __init__(self) -> None:
        self.calls = 0

    async def run(self, operation):
        self.calls += 1
        return await operation(lambda: True)


def _submission(
    outcome: ManagedTxOutcome = ManagedTxOutcome.ACCEPTED,
) -> SimpleNamespace:
    return SimpleNamespace(
        outcome=outcome,
        wait_settlement=AsyncMock(return_value="settled"),
    )


def _boundary(
    *,
    admitted: bool = True,
    live: bool = True,
) -> tuple[ManagedQueuedWriteBoundary, SimpleNamespace, _Runner]:
    runner = _Runner()
    authority = SimpleNamespace(
        submit_ptt=AsyncMock(return_value=_submission()),
        submit_force_off=AsyncMock(return_value=_submission()),
        admit_managed_write=AsyncMock(return_value=admitted),
    )
    port = SimpleNamespace(authority=authority, local_tx_work_runner=runner)
    return (
        ManagedQueuedWriteBoundary(port, session_is_live=lambda _session: live),
        authority,
        runner,
    )


@pytest.mark.parametrize(
    "command",
    [PttOn(), PttOff(), SetFreq(7_100_000), SetTunerStatus(0)],
)
def test_boundary_handles_the_entire_public_queue_contract(command: object) -> None:
    boundary, _authority, _runner = _boundary()

    assert boundary.handles(command)


def test_explicit_special_commands_retain_the_legacy_interlock_route() -> None:
    boundary, _authority, _runner = _boundary()

    assert not boundary.bypasses_legacy_interlock(SendCiv(command=0x1A))
    assert not boundary.bypasses_legacy_interlock(ScanStart())
    assert boundary.bypasses_legacy_interlock(SetFreq(7_100_000))


async def test_ptt_on_uses_authority_and_never_calls_provider() -> None:
    boundary, authority, _runner = _boundary()
    provider = AsyncMock()
    entry = CommandQueueEntry(
        PttOn(), source="websocket", session_id="ws-1", command_id="ptt-on"
    )

    assert await boundary.dispatch(entry, provider) == "settled"

    authority.submit_ptt.assert_awaited_once_with(True, "ws-1")
    authority.submit_force_off.assert_not_awaited()
    provider.assert_not_awaited()


async def test_dead_ptt_owner_fails_before_authority_or_provider() -> None:
    boundary, authority, _runner = _boundary(live=False)
    provider = AsyncMock()
    entry = CommandQueueEntry(PttOn(), source="websocket", session_id="gone")

    with pytest.raises(CommandError, match="no longer live"):
        await boundary.dispatch(entry, provider)

    authority.submit_ptt.assert_not_awaited()
    provider.assert_not_awaited()


async def test_ownerless_ptt_off_force_releases_without_provider_io() -> None:
    boundary, authority, _runner = _boundary()
    provider = AsyncMock()
    entry = CommandQueueEntry(PttOff(), source="http")

    assert await boundary.dispatch(entry, provider) == "settled"

    authority.submit_force_off.assert_awaited_once()
    authority.submit_ptt.assert_not_awaited()
    provider.assert_not_awaited()


async def test_ordinary_typed_command_transparently_reaches_provider() -> None:
    boundary, authority, _runner = _boundary()
    provider = AsyncMock(return_value="written")
    command = SetFreq(7_100_000)

    assert await boundary.dispatch(CommandQueueEntry(command), provider) == "written"

    provider.assert_awaited_once_with(command)
    authority.admit_managed_write.assert_not_awaited()


@pytest.mark.parametrize(("value", "admitted"), [(0, True), (1, False), (2, False)])
async def test_tuner_policy_precedes_provider_io_and_off_uses_local_runner(
    value: int, admitted: bool
) -> None:
    boundary, authority, runner = _boundary(admitted=admitted)
    provider = AsyncMock(return_value="written")
    command = SetTunerStatus(value)
    entry = CommandQueueEntry(
        command,
        command_id=f"tuner-{value}",
        source="websocket",
        session_id="ws-1",
    )

    if admitted:
        assert await boundary.dispatch(entry, provider) == "written"
        provider.assert_awaited_once_with(command)
        assert runner.calls == 1
    else:
        with pytest.raises(CommandError, match="authority refused write"):
            await boundary.dispatch(entry, provider)
        provider.assert_not_awaited()
        assert runner.calls == 0

    intent = authority.admit_managed_write.await_args.args[0]
    assert intent.name == "set_tuner_status"
    assert intent.params == {"value": value}


async def test_unknown_descriptor_fails_before_admission_or_provider_io() -> None:
    boundary, authority, _runner = _boundary()
    provider = AsyncMock()
    intent = CommandIntent(
        id="unknown",
        name="unknown_write",
        params={},
        source="websocket",
        target=None,
    )

    with pytest.raises(CommandError, match="no canonical command descriptor"):
        await boundary.dispatch(CommandQueueEntry(intent), provider)

    authority.admit_managed_write.assert_not_awaited()
    provider.assert_not_awaited()
