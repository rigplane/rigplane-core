"""Focused profile-domain parity tests for neutral tone/TSQL writes (MOR-2129)."""

from __future__ import annotations

from dataclasses import replace
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from rigplane.capabilities import CAP_REPEATER_TONE, CAP_TSQL
from rigplane.core.acquisition_scheduler import (
    AcquisitionPriority,
    AcquisitionScheduler,
)
from rigplane.core.state_pipeline_contracts import FieldPath
from rigplane.exceptions import CommandError
from rigplane.profiles import RadioProfile, resolve_radio_profile
from rigplane.radio_state import RadioState
from rigplane.web.radio_poller import (
    CommandQueue,
    RadioPoller,
    SetToneFreq,
    SetTsqlFreq,
)

pytestmark = pytest.mark.usefixtures("observed_rx_dispatch_premise")


def _poller(
    *, domain: Any = (8850, 10000)
) -> tuple[RadioPoller, MagicMock, RadioState, AcquisitionScheduler]:
    base = resolve_radio_profile(model="IC-7300")
    profile: RadioProfile = replace(base, ctcss_tones_centihz=domain)
    assert profile.state_acquisition is not None

    radio = MagicMock()
    radio.profile = profile
    radio.model = profile.model
    radio.capabilities = {CAP_REPEATER_TONE, CAP_TSQL}
    radio._radio_state = SimpleNamespace(active="MAIN")
    radio.set_tone_freq = AsyncMock()
    radio.set_tsql_freq = AsyncMock()
    scheduler = AcquisitionScheduler(profile=profile.state_acquisition)
    radio._acquisition_scheduler = scheduler

    state = RadioState()
    state.main.tone_freq = 7770
    state.main.tsql_freq = 7780
    return (
        RadioPoller(radio, CommandQueue(), radio_state=state),
        radio,
        state,
        scheduler,
    )


@pytest.mark.asyncio
async def test_valid_profile_member_stays_centihz_and_schedules_both_readbacks() -> (
    None
):
    poller, radio, state, scheduler = _poller()

    await poller._execute(SetToneFreq(freq_hz=8850, receiver=0))  # noqa: SLF001
    await poller._execute(SetTsqlFreq(freq_hz=8850, receiver=0))  # noqa: SLF001

    radio.set_tone_freq.assert_awaited_once_with(8850, receiver=0)
    radio.set_tsql_freq.assert_awaited_once_with(8850, receiver=0)
    assert type(state.main.tone_freq) is int
    assert type(state.main.tsql_freq) is int
    assert state.main.tone_freq == 8850
    assert state.main.tsql_freq == 8850

    pending = scheduler.pending_requests()
    assert {path for request in pending for path in request.paths} == {
        FieldPath.receiver("main", "operator_controls", "tone_freq"),
        FieldPath.receiver("main", "operator_controls", "tsql_freq"),
    }
    assert {request.priority for request in pending} == {AcquisitionPriority.USER}
    assert {request.reason for request in pending} == {"post_write_readback"}


@pytest.mark.parametrize("command_type", [SetToneFreq, SetTsqlFreq])
@pytest.mark.parametrize(
    ("domain", "value"),
    [
        (None, 8850),
        ((), 8850),
        ((8850, True), 8850),
        ((10000, 8850), 8850),
        ((8850, 10000), 12340),
        ((8850, 10000), True),
        ((8850, 10000), 88.5),
    ],
)
@pytest.mark.asyncio
async def test_invalid_profile_or_value_fails_before_setter_mirror_or_readback(
    command_type: type[SetToneFreq] | type[SetTsqlFreq],
    domain: Any,
    value: Any,
) -> None:
    poller, radio, state, scheduler = _poller(domain=domain)

    with pytest.raises(CommandError, match="CTCSS profile domain"):
        await poller._execute(command_type(freq_hz=value, receiver=0))  # type: ignore[arg-type]  # noqa: SLF001

    radio.set_tone_freq.assert_not_awaited()
    radio.set_tsql_freq.assert_not_awaited()
    assert state.main.tone_freq == 7770
    assert state.main.tsql_freq == 7780
    assert scheduler.pending_requests() == ()
