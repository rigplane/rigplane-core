"""One managed admission boundary for every provider-backed Web queue drain."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING, Any

from rigplane.core.command_dispatch import command_descriptor
from rigplane.core.exceptions import CommandError
from rigplane.core.state_pipeline_contracts import CommandIntent
from rigplane.runtime._poller_types import (
    Command,
    CommandQueueEntry,
    PttOff,
    PttOn,
    ScanStart,
    SendCiv,
    SetTunerStatus,
)
from rigplane.runtime.managed_tx_ingress import stable_managed_tx_owner
from rigplane.runtime.managed_tx_state import ManagedTxOutcome
from rigplane.runtime.managed_write_intent_bridge import (
    ManagedWriteCommand,
    managed_write_intent,
)

if TYPE_CHECKING:
    from rigplane.runtime.radio import ManagedTxCompositionPort


ProviderExecute = Callable[[object], Awaitable[Any]]


class ManagedQueuedWriteBoundary:
    """Route managed queue work through one composition port before provider I/O."""

    def __init__(
        self,
        port: ManagedTxCompositionPort,
        *,
        session_is_live: Callable[[str], bool],
    ) -> None:
        self._port = port
        self._session_is_live = session_is_live

    @property
    def port(self) -> ManagedTxCompositionPort:
        return self._port

    @staticmethod
    def handles(command: object) -> bool:
        """Whether *command* belongs to the shared Web queue contract."""
        return isinstance(command, Command)

    @staticmethod
    def bypasses_legacy_interlock(command: object) -> bool:
        """Whether canonical managed admission replaces observed-RF policy.

        Raw CI-V and scan-start remain on their explicit legacy safety routes.
        PTT never reaches a provider callback; every other public queue command
        is either admitted here or is an ordinary typed write whose broad legacy
        RF/defer block the managed composition deliberately replaces.
        """
        return isinstance(command, Command) and not isinstance(
            command, (SendCiv, ScanStart)
        )

    async def dispatch(
        self,
        entry: CommandQueueEntry,
        execute_provider: ProviderExecute,
    ) -> Any:
        """Consume one managed entry or admit it once before provider execution."""

        command = entry.command
        if not self.handles(command):
            raise TypeError(
                f"unsupported managed queue command: {type(command).__name__}"
            )

        if isinstance(command, PttOn):
            owner = stable_managed_tx_owner(
                entry.source or "websocket", entry.session_id
            )
            if owner is None:
                raise CommandError("managed PTT ON requires a stable session owner")
            if not self._session_is_live(owner.session_id):
                raise CommandError("managed PTT ON session is no longer live")
            submission = await self._port.authority.submit_ptt(True, owner.session_id)
            if submission.outcome is not ManagedTxOutcome.ACCEPTED:
                raise CommandError(f"managed TX rejected PTT ON: {submission.outcome}")
            return await submission.wait_settlement()

        if isinstance(command, PttOff):
            owner = stable_managed_tx_owner(
                entry.source or "websocket", entry.session_id
            )
            submission = (
                await self._port.authority.submit_force_off()
                if owner is None
                else await self._port.authority.submit_ptt(False, owner.session_id)
            )
            if submission.outcome is ManagedTxOutcome.REJECTED:
                raise CommandError("managed TX rejected PTT OFF")
            return await submission.wait_settlement()

        if not isinstance(command, (CommandIntent, ManagedWriteCommand)):
            return await execute_provider(command)

        intent: CommandIntent
        if isinstance(command, CommandIntent):
            if command_descriptor(command.name) is None:
                raise CommandError(
                    f"no canonical command descriptor for {command.name!r}"
                )
            intent = command
        else:
            if entry.command_id is None:
                raise CommandError("managed write requires a command id")
            intent = managed_write_intent(
                command,
                command_id=entry.command_id,
                source=entry.source or "websocket",
                session_id=entry.session_id,
            )

        if not await self._port.authority.admit_managed_write(intent):
            raise CommandError("managed transmit authority refused write")

        if isinstance(command, SetTunerStatus):
            return await self._port.local_tx_work_runner.run(
                lambda _is_current: execute_provider(command)
            )
        return await execute_provider(command)
