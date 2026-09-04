"""Managed rigctld writes enter the composition authority before delivery."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from rigplane.capabilities import CAP_RIT
from rigplane.core.command_service import CommandExecutionResult, CommandService
from rigplane.core.state_store import StateStore
from rigplane.rigctld.contract import HamlibError, RigctldConfig, RigctldResponse
from rigplane.rigctld.handler import RigctldHandler
from rigplane.rigctld.protocol import parse_line
from rigplane.runtime import tx_interlock
from rigplane.runtime.managed_tx_state import ManagedTxOutcome


class _DefaultExecutor:
    async def execute(self, _intent: object) -> CommandExecutionResult:
        raise AssertionError("RigctldHandler must provide the write executor")


def _handler(*, admitted: bool) -> tuple[RigctldHandler, AsyncMock, AsyncMock]:
    store = StateStore()
    radio = AsyncMock()
    radio.capabilities = {CAP_RIT}
    radio.state_store = store
    routing = Mock()
    routing.set_func = AsyncMock(return_value=RigctldResponse())
    routing.set_level = AsyncMock(return_value=RigctldResponse())
    radio.rigctld_routing = Mock(return_value=routing)
    authority = SimpleNamespace(
        admit_managed_write=AsyncMock(return_value=admitted),
        submit_ptt=AsyncMock(
            return_value=SimpleNamespace(outcome=ManagedTxOutcome.ACCEPTED)
        ),
    )
    service = CommandService(executor=_DefaultExecutor(), state_store=store)
    return (
        RigctldHandler(
            radio,
            RigctldConfig(),
            state_store=store,
            managed_tx_authority=authority,
            command_service=service,
        ),
        radio,
        authority.admit_managed_write,
    )


@pytest.mark.asyncio
async def test_rejected_managed_write_never_reaches_overlay_or_provider() -> None:
    handler, radio, admission = _handler(admitted=False)

    response = await handler.execute(parse_line(b"F 14074000"))

    assert response.error is HamlibError.ERJCTED
    admission.assert_awaited_once()
    assert admission.await_args.args[0].name == "set_freq"
    radio.set_freq.assert_not_awaited()
    assert handler._command_service.lifecycle_events() == ()  # noqa: SLF001


@pytest.mark.asyncio
async def test_approved_managed_write_dispatches_without_observed_rf_gate() -> None:
    handler, radio, admission = _handler(admitted=True)

    response = await handler.execute(parse_line(b"F 14074000"))

    assert response.ok
    admission.assert_awaited_once()
    radio.set_freq.assert_awaited_once_with(14_074_000, receiver=0)


@pytest.mark.asyncio
async def test_managed_ptt_does_not_double_admit_as_a_write() -> None:
    handler, _radio, admission = _handler(admitted=True)

    response = await handler.execute(parse_line(b"T 0"), session_id="client")

    assert response.ok
    admission.assert_not_awaited()


@pytest.mark.asyncio
async def test_standalone_rigctld_keeps_legacy_observed_rf_interlock(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = StateStore()
    radio = AsyncMock()
    radio.capabilities = {CAP_RIT}
    radio.state_store = store
    routing = Mock()
    routing.set_func = AsyncMock(return_value=RigctldResponse())
    radio.rigctld_routing = Mock(return_value=routing)
    handler = RigctldHandler(radio, RigctldConfig(), state_store=store)
    monkeypatch.setattr(
        handler, "_resolve_rigctld_rf_state", lambda: tx_interlock.RfState.TX
    )

    response = await handler.execute(parse_line(b"U TUNER 1"))

    assert response.error is HamlibError.ERJCTED
    routing.set_func.assert_not_awaited()


def test_managed_context_cannot_supply_delivery_without_authority() -> None:
    store = StateStore()
    service = CommandService(executor=_DefaultExecutor(), state_store=store)

    with pytest.raises(ValueError, match="together"):
        RigctldHandler(
            AsyncMock(),
            RigctldConfig(),
            state_store=store,
            command_service=service,
        )
