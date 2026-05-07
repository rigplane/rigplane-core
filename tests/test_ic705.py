"""Tests for IC-705 data-profile helpers."""

from unittest.mock import AsyncMock

import pytest

from rigplane.ic705 import (
    prepare_ic705_data_profile,
    restore_ic705_data_profile,
)


@pytest.mark.asyncio
async def test_prepare_ic705_data_profile_applies_expected_sequence() -> None:
    radio = AsyncMock()
    radio.snapshot_state.return_value = {"frequency": 145_825_000}

    snapshot = await prepare_ic705_data_profile(
        radio,
        frequency_hz=145_825_000,
        data_off_mod_input=3,
        data1_mod_input=4,
        enable_scope=True,
    )

    assert snapshot == {"frequency": 145_825_000}
    radio.snapshot_state.assert_awaited_once()
    radio.set_vox.assert_awaited_once_with(False)
    # #1206: ``apply_profile`` routes ``vfo="A"`` through the canonical
    # ``set_vfo_slot`` (the legacy ``set_vfo`` overload was removed).
    radio.set_vfo_slot.assert_any_await("A")
    radio.set_split.assert_awaited_once_with(False)
    radio.set_freq.assert_awaited_once_with(145_825_000)
    radio.set_mode.assert_awaited_once_with("FM")
    radio.set_data_mode.assert_awaited_once_with(True)
    radio.set_data_off_mod_input.assert_awaited_once_with(3)
    radio.set_data1_mod_input.assert_not_awaited()
    # #1113: apply_profile dispatches to canonical ``equalize_main_sub`` /
    # ``equalize_vfo_ab`` instead of the deprecated ``vfo_equalize`` alias.
    # The bare AsyncMock has no real ``profile`` attribute, so the dispatch
    # takes the single-RX fallback path (matches IC-705's actual profile).
    radio.equalize_vfo_ab.assert_awaited_once_with(0)
    radio.set_squelch.assert_awaited_once_with(0)
    radio.enable_scope.assert_awaited_once_with(
        output=False,
        policy="fast",
        timeout=5.0,
    )
    radio.set_scope_mode.assert_awaited_once_with(0)
    radio.set_scope_span.assert_awaited_once_with(7)
    # ``vfo="A"`` is applied twice: once after VOX, once after scope.
    assert radio.set_vfo_slot.await_count == 2


@pytest.mark.asyncio
async def test_prepare_ic705_data_profile_skips_optional_steps() -> None:
    radio = AsyncMock()
    radio.snapshot_state.return_value = {"mode": "USB"}

    await prepare_ic705_data_profile(
        radio,
        frequency_hz=14_074_000,
        mode="USB",
        disable_vox=False,
        data_off_mod_input=None,
        data1_mod_input=None,
        squelch_level=None,
        enable_scope=False,
    )

    radio.set_vox.assert_not_called()
    radio.set_data_off_mod_input.assert_not_called()
    radio.set_data1_mod_input.assert_not_called()
    radio.set_squelch.assert_not_called()
    radio.enable_scope.assert_not_called()
    radio.set_scope_mode.assert_not_called()
    radio.set_scope_span.assert_not_called()
    radio.set_mode.assert_awaited_once_with("USB")


@pytest.mark.asyncio
async def test_restore_ic705_data_profile_delegates_to_restore_state() -> None:
    radio = AsyncMock()
    snapshot = {"frequency": 145_825_000, "data_mode": False}

    await restore_ic705_data_profile(radio, snapshot)

    radio.restore_state.assert_awaited_once_with(snapshot)
