"""Yaesu web-projection acceptance: observation-backed TX controls survive.

MOR-447 establishes a Yaesu-driven analogue of the Icom acceptance pattern in
``tests/test_civ_rx_coverage.py`` (value / survives-unrelated-poll /
projects-available). The :class:`YaesuObservationAdapter` ``poll_tx_controls``
lane feeds a :class:`StateStore`; the public payload must then project each TX
control as ``availability == "available"`` and the value must not snap back
when an unrelated observation lands on a later cycle. The scaffold is reusable
by MOR-443 / MOR-448.
"""
# mypy: disable-error-code=untyped-decorator

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from rigplane.core.state_acquisition_policy import RadioAcquisitionProfile
from rigplane.core.state_store import StateStore
from rigplane.profiles import get_radio_profile
from rigplane.web.runtime_helpers import build_public_state_payload_from_snapshot

from rigplane.backends.yaesu_cat.observations import YaesuObservationAdapter

# (store path, public fieldStatus key, expected value) for the five TX controls.
_TX_CONTROL_CASES = (
    ("global.operator_controls.power_level", "powerLevel", 55),
    ("global.operator_controls.mic_gain", "micGain", 40),
    ("global.tx_state.compressor_on", "compressorOn", True),
    ("global.operator_controls.compressor_level", "compressorLevel", 25),
    ("global.tx_state.vox_on", "voxOn", True),
)


def _clock() -> float:
    return 123.456


def _profile_state_acquisition() -> RadioAcquisitionProfile:
    profile = get_radio_profile("FTX-1")
    assert profile.state_acquisition is not None
    return profile.state_acquisition


def _make_radio() -> MagicMock:
    radio = MagicMock()
    radio.capabilities = {"dual_rx", "meters", "tx", "vox", "compressor"}
    # Power emits the watt SETPOINT (read_power), not the RM5 meter.
    radio.read_power = AsyncMock(return_value=(2, 55))
    radio.read_mic_gain = AsyncMock(return_value=40)
    radio.read_processor = AsyncMock(return_value=True)
    radio.read_processor_level = AsyncMock(return_value=25)
    radio.read_vox = AsyncMock(return_value=True)
    # An unrelated RX-meter read used to prove no snap-back.
    radio.read_s_meter = AsyncMock(return_value=150)
    return radio


def _adapter(radio: MagicMock) -> YaesuObservationAdapter:
    return YaesuObservationAdapter(
        radio,
        profile=_profile_state_acquisition(),
        clock=_clock,
    )


async def _apply_tx_controls(store: StateStore, radio: MagicMock) -> None:
    for observation in await _adapter(radio).poll_tx_controls():
        store.apply(observation)


@pytest.mark.asyncio
async def test_tx_controls_observation_value() -> None:
    store = StateStore()
    radio = _make_radio()

    await _apply_tx_controls(store, radio)

    snapshot = store.snapshot()
    for store_path, _public_path, expected in _TX_CONTROL_CASES:
        assert snapshot.field(store_path).value == expected


@pytest.mark.asyncio
async def test_tx_controls_survive_unrelated_observation() -> None:
    store = StateStore()
    radio = _make_radio()

    await _apply_tx_controls(store, radio)
    # An unrelated RX-meter observation on a later cycle must not disturb the
    # TX-control setpoints (no snap-back to the default snapshot value).
    for observation in await _adapter(radio).poll_rx_meters():
        store.apply(observation)

    snapshot = store.snapshot()
    for store_path, _public_path, expected in _TX_CONTROL_CASES:
        assert snapshot.field(store_path).value == expected


@pytest.mark.asyncio
async def test_tx_controls_project_available() -> None:
    store = StateStore()
    radio = _make_radio()

    await _apply_tx_controls(store, radio)
    # An unrelated observation must not re-gate the projected availability.
    for observation in await _adapter(radio).poll_rx_meters():
        store.apply(observation)

    payload = build_public_state_payload_from_snapshot(
        store.snapshot(),
        radio=None,
        receiver_count=2,
    )

    for _store_path, public_path, _expected in _TX_CONTROL_CASES:
        status = payload["fieldStatus"][public_path]
        assert status["observed"] is True
        assert status["availability"] == "available"
