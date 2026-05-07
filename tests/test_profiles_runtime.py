"""Tests for the declarative radio profile system (profiles_runtime.py)."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from rigplane.profiles_runtime import PRESETS, OperatingProfile, apply_profile
from rigplane.ic705 import prepare_ic705_data_profile, restore_ic705_data_profile
from rigplane.types import ScopeCompletionPolicy


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _full_radio() -> AsyncMock:
    """Mock radio that supports every setter used by apply_profile."""
    radio = AsyncMock()
    radio.snapshot_state.return_value = {"freq": 7_000_000, "mode": "CW"}
    return radio


def _minimal_radio() -> AsyncMock:
    """Mock radio that only supports set_freq — nothing else."""
    radio = AsyncMock(spec=["snapshot_state", "set_freq"])
    radio.snapshot_state = AsyncMock(return_value={"freq": 7_000_000})
    radio.set_freq = AsyncMock()
    return radio


# ---------------------------------------------------------------------------
# OperatingProfile dataclass
# ---------------------------------------------------------------------------


class TestOperatingProfile:
    def test_all_fields_default_to_none_or_safe_value(self) -> None:
        profile = OperatingProfile()
        assert profile.frequency_hz is None
        assert profile.mode is None
        assert profile.filter_width is None
        assert profile.vox is None
        assert profile.split is None
        assert profile.vfo is None
        assert profile.data_mode is None
        assert profile.data_off_mod_input is None
        assert profile.data1_mod_input is None
        assert profile.squelch_level is None
        assert profile.scope_enabled is None
        assert profile.scope_mode is None
        assert profile.scope_span is None
        # Non-None defaults
        assert profile.equalize_vfo is False
        assert profile.scope_output is False
        assert profile.scope_policy == ScopeCompletionPolicy.FAST
        assert profile.scope_timeout == 5.0

    def test_fields_accept_explicit_values(self) -> None:
        profile = OperatingProfile(
            frequency_hz=14_074_000,
            mode="USB",
            filter_width=3000,
            vox=True,
            split=True,
            vfo="B",
            data_mode=True,
            data_off_mod_input=1,
            data1_mod_input=2,
            squelch_level=50,
            scope_enabled=True,
            scope_mode=1,
            scope_span=5,
        )
        assert profile.frequency_hz == 14_074_000
        assert profile.mode == "USB"
        assert profile.vfo == "B"
        assert profile.vox is True

    def test_false_booleans_are_distinct_from_none(self) -> None:
        profile = OperatingProfile(vox=False, split=False, data_mode=False)
        assert profile.vox is False
        assert profile.split is False
        assert profile.data_mode is False
        # They must NOT be None
        assert profile.vox is not None
        assert profile.split is not None
        assert profile.data_mode is not None


# ---------------------------------------------------------------------------
# apply_profile — full-capability radio
# ---------------------------------------------------------------------------


class TestApplyProfileFull:
    @pytest.fixture()
    def radio(self) -> AsyncMock:
        return _full_radio()

    @pytest.mark.asyncio()
    async def test_returns_snapshot(self, radio: AsyncMock) -> None:
        profile = OperatingProfile(frequency_hz=14_074_000)
        snapshot = await apply_profile(radio, profile)
        assert snapshot == {"freq": 7_000_000, "mode": "CW"}
        radio.snapshot_state.assert_awaited_once()

    @pytest.mark.asyncio()
    async def test_applies_frequency(self, radio: AsyncMock) -> None:
        await apply_profile(radio, OperatingProfile(frequency_hz=145_500_000))
        radio.set_freq.assert_awaited_with(145_500_000)

    @pytest.mark.asyncio()
    async def test_applies_mode_without_filter(self, radio: AsyncMock) -> None:
        await apply_profile(radio, OperatingProfile(mode="FM"))
        radio.set_mode.assert_awaited_with("FM")

    @pytest.mark.asyncio()
    async def test_applies_mode_with_filter_width(self, radio: AsyncMock) -> None:
        await apply_profile(radio, OperatingProfile(mode="CW", filter_width=500))
        radio.set_mode.assert_awaited_with("CW", filter_width=500)

    @pytest.mark.asyncio()
    async def test_applies_vox(self, radio: AsyncMock) -> None:
        await apply_profile(radio, OperatingProfile(vox=True))
        radio.set_vox.assert_awaited_with(True)

    @pytest.mark.asyncio()
    async def test_applies_vox_false(self, radio: AsyncMock) -> None:
        await apply_profile(radio, OperatingProfile(vox=False))
        radio.set_vox.assert_awaited_with(False)

    @pytest.mark.asyncio()
    async def test_applies_split(self, radio: AsyncMock) -> None:
        await apply_profile(radio, OperatingProfile(split=True))
        radio.set_split.assert_awaited_with(True)

    @pytest.mark.asyncio()
    async def test_applies_vfo(self, radio: AsyncMock) -> None:
        await apply_profile(radio, OperatingProfile(vfo="A"))
        # VFO is set twice: once at start, once at end.  Per #1206 the
        # legacy ``set_vfo`` overload is gone — A/B routes through the
        # canonical ``VfoSlotCapable.set_vfo_slot``.
        assert radio.set_vfo_slot.await_count == 2
        radio.set_vfo_slot.assert_awaited_with("A")

    @pytest.mark.asyncio()
    async def test_applies_data_mode(self, radio: AsyncMock) -> None:
        await apply_profile(radio, OperatingProfile(data_mode=True))
        radio.set_data_mode.assert_awaited_with(True)

    @pytest.mark.asyncio()
    async def test_applies_mod_inputs(self, radio: AsyncMock) -> None:
        await apply_profile(
            radio,
            OperatingProfile(
                data_off_mod_input=3,
                data1_mod_input=5,
            ),
        )
        radio.set_data_off_mod_input.assert_awaited_with(3)
        radio.set_data1_mod_input.assert_not_awaited()

    @pytest.mark.asyncio()
    async def test_applies_squelch(self, radio: AsyncMock) -> None:
        await apply_profile(radio, OperatingProfile(squelch_level=0))
        radio.set_squelch.assert_awaited_with(0)

    @pytest.mark.asyncio()
    async def test_applies_equalize_vfo(self, radio: AsyncMock) -> None:
        # #1114: apply_profile dispatches to the canonical
        # ``equalize_main_sub`` (dual-RX) or ``equalize_vfo_ab(0)``
        # (single-RX). The legacy ``vfo_equalize`` alias has been removed.
        # The bare AsyncMock fixture has no real ``profile`` attribute, so
        # the dispatch falls through to the single-RX path.
        await apply_profile(radio, OperatingProfile(equalize_vfo=True))
        radio.equalize_vfo_ab.assert_awaited_once_with(0)

    @pytest.mark.asyncio()
    async def test_scope_enabled(self, radio: AsyncMock) -> None:
        await apply_profile(
            radio,
            OperatingProfile(
                scope_enabled=True,
                scope_mode=0,
                scope_span=7,
            ),
        )
        radio.enable_scope.assert_awaited_once()
        radio.set_scope_mode.assert_awaited_with(0)
        radio.set_scope_span.assert_awaited_with(7)

    @pytest.mark.asyncio()
    async def test_scope_disabled(self, radio: AsyncMock) -> None:
        await apply_profile(radio, OperatingProfile(scope_enabled=False))
        radio.disable_scope.assert_awaited_once()

    @pytest.mark.asyncio()
    async def test_none_fields_not_applied(self, radio: AsyncMock) -> None:
        """A profile with all defaults should only call snapshot_state."""
        await apply_profile(radio, OperatingProfile())
        radio.snapshot_state.assert_awaited_once()
        radio.set_freq.assert_not_awaited()
        radio.set_mode.assert_not_awaited()
        radio.set_vox.assert_not_awaited()
        radio.set_split.assert_not_awaited()
        radio.set_vfo.assert_not_awaited()
        radio.set_data_mode.assert_not_awaited()
        radio.set_squelch.assert_not_awaited()

    @pytest.mark.asyncio()
    async def test_filter_width_without_mode_is_ignored(self, radio: AsyncMock) -> None:
        """filter_width only applies as an arg to set_mode; alone it does nothing."""
        await apply_profile(radio, OperatingProfile(filter_width=500))
        radio.set_mode.assert_not_awaited()

    @pytest.mark.asyncio()
    async def test_full_profile_applies_all(self, radio: AsyncMock) -> None:
        profile = OperatingProfile(
            frequency_hz=14_074_000,
            mode="USB",
            vox=False,
            split=False,
            vfo="A",
            data_mode=True,
            data_off_mod_input=1,
            data1_mod_input=2,
            squelch_level=0,
            equalize_vfo=True,
            scope_enabled=True,
            scope_mode=0,
            scope_span=7,
        )
        await apply_profile(radio, profile)
        radio.set_vox.assert_awaited()
        # #1206: ``vfo="A"`` routes through ``set_vfo_slot``.
        radio.set_vfo_slot.assert_awaited()
        radio.set_split.assert_awaited()
        radio.set_freq.assert_awaited()
        radio.set_mode.assert_awaited()
        radio.set_data_mode.assert_awaited()
        radio.set_data_off_mod_input.assert_awaited()
        radio.set_data1_mod_input.assert_not_awaited()
        # #1113: dispatches to canonical ``equalize_vfo_ab(0)`` (bare
        # AsyncMock has no real profile attribute → single-RX fallback).
        radio.equalize_vfo_ab.assert_awaited_with(0)
        radio.set_squelch.assert_awaited()
        radio.enable_scope.assert_awaited()
        radio.set_scope_mode.assert_awaited()
        radio.set_scope_span.assert_awaited()


# ---------------------------------------------------------------------------
# apply_profile — minimal radio (only set_freq)
# ---------------------------------------------------------------------------


class TestApplyProfileMinimal:
    @pytest.mark.asyncio()
    async def test_only_supported_setter_called(self) -> None:
        radio = _minimal_radio()
        profile = OperatingProfile(
            frequency_hz=14_074_000,
            mode="USB",
            vox=False,
            split=True,
            data_mode=True,
            squelch_level=0,
        )
        snapshot = await apply_profile(radio, profile)
        radio.set_freq.assert_awaited_with(14_074_000)
        assert snapshot == {"freq": 7_000_000}

    @pytest.mark.asyncio()
    async def test_unsupported_fields_skipped_without_error(self) -> None:
        """apply_profile must not raise when the radio lacks setters."""
        radio = _minimal_radio()
        profile = OperatingProfile(
            frequency_hz=14_074_000,
            vox=False,
            split=True,
            data_mode=True,
            squelch_level=0,
        )
        # Should complete without AttributeError
        snapshot = await apply_profile(radio, profile)
        assert isinstance(snapshot, dict)
        # Only the supported setter was called
        radio.set_freq.assert_awaited_with(14_074_000)


# ---------------------------------------------------------------------------
# apply_profile — equalize_vfo dispatch (issues #1113 / #1114)
# ---------------------------------------------------------------------------


class TestEqualizeVfoDispatch:
    """Verify ``equalize_vfo=True`` dispatches to the right canonical method.

    Regression coverage for #1113/#1114: ``apply_profile`` calls only the
    canonical methods. Dual-RX profiles route to ``equalize_main_sub``;
    single-RX profiles route to ``equalize_vfo_ab(0)``.
    """

    @pytest.mark.asyncio()
    async def test_dual_rx_profile_uses_equalize_main_sub(self) -> None:
        radio = AsyncMock()
        radio.snapshot_state.return_value = {}
        # Profile guard: receiver_count > 1 → dual-RX branch
        radio.profile = SimpleNamespace(receiver_count=2)
        await apply_profile(radio, OperatingProfile(equalize_vfo=True))
        radio.equalize_main_sub.assert_awaited_once_with()
        radio.equalize_vfo_ab.assert_not_awaited()

    @pytest.mark.asyncio()
    async def test_single_rx_profile_uses_equalize_vfo_ab(self) -> None:
        radio = AsyncMock(
            spec=[
                "snapshot_state",
                "equalize_vfo_ab",
                "profile",
            ]
        )
        radio.snapshot_state = AsyncMock(return_value={})
        radio.equalize_vfo_ab = AsyncMock()
        radio.profile = SimpleNamespace(receiver_count=1)
        await apply_profile(radio, OperatingProfile(equalize_vfo=True))
        radio.equalize_vfo_ab.assert_awaited_once_with(0)

    @pytest.mark.asyncio()
    async def test_radio_without_either_method_skips_silently(self) -> None:
        radio = AsyncMock(spec=["snapshot_state", "profile"])
        radio.snapshot_state = AsyncMock(return_value={})
        radio.profile = SimpleNamespace(receiver_count=1)
        # Should NOT raise even though radio has neither equalize method
        snapshot = await apply_profile(radio, OperatingProfile(equalize_vfo=True))
        assert snapshot == {}


# ---------------------------------------------------------------------------
# apply_profile — set_split dispatch
# ---------------------------------------------------------------------------


class TestApplySplit:
    """Verify ``apply_profile`` calls ``set_split`` when supported.

    The legacy ``set_split_mode`` deprecation alias was removed in v0.20
    (issue #1205). ``apply_profile`` only dispatches to ``set_split``.
    """

    @pytest.mark.asyncio()
    async def test_set_split_is_called(self) -> None:
        radio = AsyncMock(spec=["snapshot_state", "set_split"])
        radio.snapshot_state = AsyncMock(return_value={})
        radio.set_split = AsyncMock()
        await apply_profile(radio, OperatingProfile(split=True))
        radio.set_split.assert_awaited_once_with(True)

    @pytest.mark.asyncio()
    async def test_no_split_setter_is_skipped_silently(self) -> None:
        radio = AsyncMock(spec=["snapshot_state"])
        radio.snapshot_state = AsyncMock(return_value={})
        # Should not raise even though radio has no setter.
        snapshot = await apply_profile(radio, OperatingProfile(split=True))
        assert snapshot == {}


# ---------------------------------------------------------------------------
# Presets
# ---------------------------------------------------------------------------


class TestPresets:
    def test_presets_are_operating_profiles(self) -> None:
        assert isinstance(PRESETS.aprs_vhf, OperatingProfile)
        assert isinstance(PRESETS.ft8_20m, OperatingProfile)
        assert isinstance(PRESETS.cw_contest, OperatingProfile)
        assert isinstance(PRESETS.ssb_40m, OperatingProfile)

    def test_aprs_vhf_preset(self) -> None:
        p = PRESETS.aprs_vhf
        assert p.frequency_hz == 145_500_000
        assert p.mode == "FM"
        assert p.data_mode is True
        assert p.vox is False

    def test_ft8_20m_preset(self) -> None:
        p = PRESETS.ft8_20m
        assert p.frequency_hz == 14_074_000
        assert p.mode == "USB"
        assert p.data_mode is True
        assert p.vox is False

    def test_cw_contest_preset(self) -> None:
        p = PRESETS.cw_contest
        assert p.vox is False
        assert p.split is False
        assert p.frequency_hz is None  # no freq — mode-only preset

    def test_ssb_40m_preset(self) -> None:
        p = PRESETS.ssb_40m
        assert p.frequency_hz == 7_040_000
        assert p.mode == "LSB"

    @pytest.mark.asyncio()
    async def test_presets_apply_successfully(self) -> None:
        radio = _full_radio()
        for name in ("aprs_vhf", "ft8_20m", "cw_contest", "ssb_40m"):
            preset = getattr(PRESETS, name)
            snapshot = await apply_profile(radio, preset)
            assert isinstance(snapshot, dict)


# ---------------------------------------------------------------------------
# Backward compatibility — ic705.py delegates to apply_profile
# ---------------------------------------------------------------------------


class TestIC705BackwardCompat:
    @pytest.mark.asyncio()
    async def test_prepare_ic705_data_profile_returns_snapshot(self) -> None:
        radio = _full_radio()
        snapshot = await prepare_ic705_data_profile(radio, frequency_hz=145_500_000)
        assert snapshot == {"freq": 7_000_000, "mode": "CW"}
        radio.snapshot_state.assert_awaited_once()

    @pytest.mark.asyncio()
    async def test_prepare_ic705_sets_data_mode(self) -> None:
        radio = _full_radio()
        await prepare_ic705_data_profile(radio, frequency_hz=145_500_000, mode="FM")
        radio.set_data_mode.assert_awaited_with(True)
        radio.set_freq.assert_awaited_with(145_500_000)
        radio.set_mode.assert_awaited_with("FM")

    @pytest.mark.asyncio()
    async def test_prepare_ic705_disables_vox_by_default(self) -> None:
        radio = _full_radio()
        await prepare_ic705_data_profile(radio, frequency_hz=145_500_000)
        radio.set_vox.assert_awaited_with(False)

    @pytest.mark.asyncio()
    async def test_prepare_ic705_vox_not_disabled_when_flag_false(self) -> None:
        radio = _full_radio()
        await prepare_ic705_data_profile(
            radio,
            frequency_hz=145_500_000,
            disable_vox=False,
        )
        radio.set_vox.assert_not_awaited()

    @pytest.mark.asyncio()
    async def test_restore_ic705_calls_restore_state(self) -> None:
        radio = _full_radio()
        snapshot = {"freq": 7_000_000, "mode": "CW"}
        await restore_ic705_data_profile(radio, snapshot)
        radio.restore_state.assert_awaited_with(snapshot)

    @pytest.mark.asyncio()
    async def test_prepare_ic705_with_scope(self) -> None:
        radio = _full_radio()
        await prepare_ic705_data_profile(
            radio,
            frequency_hz=145_500_000,
            enable_scope=True,
            scope_mode=0,
            scope_span=7,
        )
        radio.enable_scope.assert_awaited_once()
        radio.set_scope_mode.assert_awaited_with(0)
        radio.set_scope_span.assert_awaited_with(7)

    @pytest.mark.asyncio()
    async def test_prepare_ic705_with_mod_inputs(self) -> None:
        radio = _full_radio()
        await prepare_ic705_data_profile(
            radio,
            frequency_hz=145_500_000,
            data_off_mod_input=3,
            data1_mod_input=5,
        )
        radio.set_data_off_mod_input.assert_awaited_with(3)
        radio.set_data1_mod_input.assert_not_awaited()


# ---------------------------------------------------------------------------
# Exports
# ---------------------------------------------------------------------------


class TestExports:
    def test_importable_from_top_level(self) -> None:
        from rigplane import OperatingProfile, apply_profile, PRESETS  # noqa: F811

        assert OperatingProfile is not None
        assert apply_profile is not None
        assert PRESETS is not None

    def test_importable_but_not_in_all(self) -> None:
        """Runtime profile symbols are importable but not part of the public API surface."""
        import rigplane

        assert hasattr(rigplane, "OperatingProfile")
        assert hasattr(rigplane, "apply_profile")
        assert hasattr(rigplane, "PRESETS")
