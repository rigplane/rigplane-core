"""Canonical Yaesu actuator contract for runtime-managed transmit effects."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from rigplane.backends.yaesu_cat.parser import format_command
from rigplane.backends.yaesu_cat.radio import YaesuCatRadio
from rigplane.command_spec import CatCommandSpec
from rigplane.runtime.managed_tx_effect_lane import ManagedTxActuator
from rigplane.runtime.managed_tx_state import (
    AbortOperation,
    ActuationOperation,
    ActuationResult,
    EffectToken,
)
from rigplane.rig_loader import load_rig


_RIGS_DIR = Path(__file__).parents[1] / "rigs"
_PROFILE_TEMPLATES = {
    "set_ptt": CatCommandSpec(write="ZP{state};"),
    "send_cw": CatCommandSpec(write="ZC{type}{mem};"),
    "set_tuner": CatCommandSpec(write="ZT{src}{type}{state};"),
}


def _token() -> EffectToken:
    return EffectToken(7, 3, "yaesu-actuator")


def _profile_radio(*, omit: str | None = None) -> YaesuCatRadio:
    config = load_rig(_RIGS_DIR / "ftx1.toml")
    commands = dict(config.commands)
    commands.update(_PROFILE_TEMPLATES)
    if omit is not None:
        commands.pop(omit)
    radio = YaesuCatRadio("/dev/null", profile=replace(config, commands=commands))
    radio._transport._connected = True
    radio._transport.write = AsyncMock()
    return radio


def test_yaesu_radio_satisfies_managed_tx_actuator_protocol() -> None:
    assert isinstance(_profile_radio(), ManagedTxActuator)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "operation",
    [ActuationOperation.PTT_ON, ActuationOperation.TRANSMIT_ON],
    ids=lambda operation: operation.value,
)
async def test_managed_on_uses_profile_ptt_and_propagates_currency(
    operation: ActuationOperation,
) -> None:
    radio = _profile_radio()

    def current() -> bool:
        return True

    result = await radio.actuate(_token(), operation, is_current=current)

    assert result is ActuationResult.ACCEPTED
    radio._transport.write.assert_awaited_once_with(
        format_command(_PROFILE_TEMPLATES["set_ptt"].write, state="1"),
        is_current=current,
        urgent=False,
    )
    assert radio.radio_state.ptt is False


@pytest.mark.asyncio
async def test_force_receive_uses_urgent_profile_ptt_without_claiming_observation(
) -> None:
    radio = _profile_radio()
    radio.radio_state.ptt = True

    def current() -> bool:
        return True

    result = await radio.actuate(
        _token(), ActuationOperation.FORCE_RECEIVE, is_current=current
    )

    assert result is ActuationResult.ACCEPTED
    radio._transport.write.assert_awaited_once_with(
        format_command(_PROFILE_TEMPLATES["set_ptt"].write, state="0"),
        is_current=current,
        urgent=True,
    )
    assert radio.radio_state.ptt is True


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("operation", "profile_key", "params"),
    [
        (AbortOperation.STOP_CW, "send_cw", {"type": " ", "mem": ""}),
        (
            AbortOperation.STOP_TUNE,
            "set_tuner",
            {"src": "0", "type": "0", "state": "0"},
        ),
    ],
    ids=["stop_cw", "stop_tune"],
)
async def test_managed_abort_uses_urgent_profile_command(
    operation: AbortOperation,
    profile_key: str,
    params: dict[str, str],
) -> None:
    radio = _profile_radio()

    def current() -> bool:
        return True

    result = await radio.actuate(_token(), operation, is_current=current)

    assert result is ActuationResult.ACCEPTED
    radio._transport.write.assert_awaited_once_with(
        format_command(_PROFILE_TEMPLATES[profile_key].write, **params),
        is_current=current,
        urgent=True,
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("operation", "missing_profile_key"),
    [
        (ActuationOperation.PTT_ON, "set_ptt"),
        (ActuationOperation.TRANSMIT_ON, "set_ptt"),
        (ActuationOperation.FORCE_RECEIVE, "set_ptt"),
        (AbortOperation.STOP_CW, "send_cw"),
        (AbortOperation.STOP_TUNE, "set_tuner"),
    ],
    ids=["ptt_on", "transmit_on", "force_receive", "stop_cw", "stop_tune"],
)
async def test_missing_profile_command_refuses_before_io(
    operation: ActuationOperation | AbortOperation,
    missing_profile_key: str,
) -> None:
    radio = _profile_radio(omit=missing_profile_key)

    result = await radio.actuate(_token(), operation, is_current=lambda: True)

    assert result is ActuationResult.REJECTED
    radio._transport.write.assert_not_awaited()
