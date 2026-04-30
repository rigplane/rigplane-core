"""Tests for Band Stack Register (BSR) recall band switching.

Covers:
- _civ_expects_response for BSR read (1A 01) — must return True
- SetBand handler: BSR recall path (read → set_frequency → set_mode)
- SetBand handler: fallback to default freq when BSR unavailable
- SetBand handler: state update + event emission after BSR recall
- SetBand handler: unknown bsr_code logged as warning
- BandInfo.bsr_code in rig profiles (ic7300, ic7610)
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from icom_lan.profiles import resolve_radio_profile
from icom_lan.radio_state import RadioState
from icom_lan.rigctld.state_cache import StateCache
from icom_lan.web.radio_poller import (
    CommandQueue,
    RadioPoller,
    SetBand,
)


# ---------------------------------------------------------------------------
# _civ_expects_response tests
# ---------------------------------------------------------------------------


class TestCivExpectsResponseBSR:
    """Ensure 1A 01 <band> <register> is treated as a response-expected GET."""

    def _make_frame(self, cmd: int, sub: int | None, data: bytes) -> SimpleNamespace:
        return SimpleNamespace(command=cmd, sub=sub, data=data)

    def test_bsr_read_expects_response(self) -> None:
        """BSR read (1A 01 with 2 data bytes) should expect a response."""
        from icom_lan.runtime._civ_rx import CivRuntime

        frame = self._make_frame(cmd=0x1A, sub=0x01, data=bytes([0x05, 0x01]))
        assert CivRuntime._civ_expects_response(frame) is True

    def test_bsr_write_does_not_expect_response(self) -> None:
        """BSR write (1A 01 with >2 data bytes = full content) → fire-and-forget."""
        from icom_lan.runtime._civ_rx import CivRuntime

        # Write: band(1) + register(1) + freq(5) + mode(1) + filter(1) = 9 bytes
        data = bytes([0x05, 0x01, 0x00, 0x40, 0x26, 0x14, 0x00, 0x01, 0x01])
        frame = self._make_frame(cmd=0x1A, sub=0x01, data=data)
        assert CivRuntime._civ_expects_response(frame) is False

    def test_config_read_still_works(self) -> None:
        """Existing 1A 05 (config read, 2 data bytes) still expects response."""
        from icom_lan.runtime._civ_rx import CivRuntime

        frame = self._make_frame(cmd=0x1A, sub=0x05, data=bytes([0x00, 0x71]))
        assert CivRuntime._civ_expects_response(frame) is True


# ---------------------------------------------------------------------------
# Helpers for RadioPoller tests
# ---------------------------------------------------------------------------


def _make_radio(model: str = "IC-7610", active: str = "MAIN") -> MagicMock:
    profile = resolve_radio_profile(model=model)
    radio = MagicMock()
    radio.profile = profile
    radio.model = profile.model
    radio.capabilities = set(profile.capabilities)
    radio._radio_state = SimpleNamespace(active=active)
    radio.send_civ = AsyncMock(return_value=None)
    radio.set_freq = AsyncMock()
    radio.set_mode = AsyncMock()
    radio.enable_scope = AsyncMock()
    radio.disable_scope = AsyncMock()
    radio.on_scope_data = MagicMock()
    radio.capture_scope_frame = AsyncMock()
    radio.capture_scope_frames = AsyncMock()
    radio.set_scope_during_tx = AsyncMock()
    radio.set_scope_center_type = AsyncMock()
    return radio


def _make_poller(
    radio: MagicMock,
    events: list | None = None,
    model: str = "IC-7610",
) -> RadioPoller:
    state = RadioState()
    poller = RadioPoller(
        radio,
        StateCache(),
        CommandQueue(),
        on_state_event=(lambda name, data: events.append((name, data)))
        if events is not None
        else None,
        radio_state=state,
    )
    return poller


def _bsr_response_frame(
    band: int, register: int, freq_bcd: bytes, mode: int, filt: int
) -> SimpleNamespace:
    """Build a fake CivFrame for BSR read response."""
    data = bytes([band, register]) + freq_bcd + bytes([mode, filt])
    return SimpleNamespace(
        to_addr=0xE0,
        from_addr=0x94,
        command=0x1A,
        sub=0x01,
        data=data,
        receiver=None,
    )


# ---------------------------------------------------------------------------
# SetBand handler tests
# ---------------------------------------------------------------------------


class TestSetBandBSRRecall:
    """Test BSR recall path in SetBand command handler."""

    @pytest.mark.asyncio
    async def test_bsr_recall_sets_freq_and_mode(self) -> None:
        """BSR recall should read freq/mode from radio and apply them."""
        radio = _make_radio(model="IC-7300")
        # BSR response: 14.264 MHz, USB (mode=1), FIL1 (filter=1)
        freq_bcd = b"\x00\x40\x26\x14\x00"  # 14264000 in BCD
        bsr_resp = _bsr_response_frame(0x05, 0x01, freq_bcd, 0x01, 0x01)
        radio.send_civ = AsyncMock(return_value=bsr_resp)

        events: list[tuple[str, dict]] = []
        poller = _make_poller(radio, events=events, model="IC-7300")

        await poller._execute(SetBand(band=5))  # noqa: SLF001

        radio.set_freq.assert_awaited_once_with(14264000)
        radio.set_mode.assert_awaited_once_with("USB", 1)

    @pytest.mark.asyncio
    async def test_bsr_recall_updates_state(self) -> None:
        """BSR recall should update RadioState immediately."""
        radio = _make_radio(model="IC-7300")
        freq_bcd = b"\x00\x70\x20\x07\x00"  # 7207000 in BCD
        bsr_resp = _bsr_response_frame(0x03, 0x01, freq_bcd, 0x00, 0x01)
        radio.send_civ = AsyncMock(return_value=bsr_resp)

        poller = _make_poller(radio, model="IC-7300")
        rev_before = poller.revision

        await poller._execute(SetBand(band=3))  # noqa: SLF001

        assert poller.revision > rev_before
        assert poller._radio_state.main.freq == 7207000  # noqa: SLF001
        assert poller._radio_state.main.mode == "LSB"  # noqa: SLF001

    @pytest.mark.asyncio
    async def test_bsr_recall_emits_events(self) -> None:
        """BSR recall should emit freq_changed and mode_changed events."""
        radio = _make_radio(model="IC-7300")
        freq_bcd = b"\x00\x40\x66\x03\x00"  # 3664000 in BCD (3.664 MHz)
        bsr_resp = _bsr_response_frame(0x02, 0x01, freq_bcd, 0x00, 0x01)
        radio.send_civ = AsyncMock(return_value=bsr_resp)

        events: list[tuple[str, dict]] = []
        poller = _make_poller(radio, events=events, model="IC-7300")

        await poller._execute(SetBand(band=2))  # noqa: SLF001

        event_names = [name for name, _ in events]
        assert "freq_changed" in event_names
        assert "mode_changed" in event_names

        freq_event = next(d for n, d in events if n == "freq_changed")
        assert freq_event["freq"] == 3664000

    @pytest.mark.asyncio
    async def test_bsr_recall_lsb_mode(self) -> None:
        """Mode code 0x00 = LSB should be parsed correctly."""
        radio = _make_radio(model="IC-7300")
        freq_bcd = b"\x00\x00\x10\x07\x00"  # 7100000
        bsr_resp = _bsr_response_frame(0x03, 0x01, freq_bcd, 0x00, 0x01)
        radio.send_civ = AsyncMock(return_value=bsr_resp)

        poller = _make_poller(radio, model="IC-7300")
        await poller._execute(SetBand(band=3))  # noqa: SLF001

        radio.set_mode.assert_awaited_once_with("LSB", 1)

    @pytest.mark.asyncio
    async def test_bsr_recall_cw_mode(self) -> None:
        """Mode code 0x03 = CW should be parsed correctly."""
        radio = _make_radio(model="IC-7300")
        freq_bcd = b"\x00\x00\x20\x14\x00"  # 14200000
        bsr_resp = _bsr_response_frame(0x05, 0x01, freq_bcd, 0x03, 0x02)
        radio.send_civ = AsyncMock(return_value=bsr_resp)

        poller = _make_poller(radio, model="IC-7300")
        await poller._execute(SetBand(band=5))  # noqa: SLF001

        radio.set_mode.assert_awaited_once_with("CW", 2)


class TestSetBandFallback:
    """Test fallback path when BSR is unavailable."""

    @pytest.mark.asyncio
    async def test_fallback_when_bsr_returns_none(self) -> None:
        """When send_civ returns None, fall back to default freq from profile."""
        radio = _make_radio(model="IC-7300")
        radio.send_civ = AsyncMock(return_value=None)

        poller = _make_poller(radio, model="IC-7300")
        await poller._execute(SetBand(band=5))  # noqa: SLF001

        # Fallback: default 20m freq from ic7300.toml
        radio.set_freq.assert_awaited_once()
        freq_arg = radio.set_freq.call_args[0][0]
        assert 13_900_000 <= freq_arg <= 14_500_000  # within 20m band

    @pytest.mark.asyncio
    async def test_fallback_when_bsr_response_too_short(self) -> None:
        """BSR response with insufficient data should trigger fallback."""
        radio = _make_radio(model="IC-7300")
        short_resp = SimpleNamespace(
            to_addr=0xE0,
            from_addr=0x94,
            command=0x1A,
            sub=0x01,
            data=b"\x05\x01\x00",  # only 3 bytes, need >= 8
            receiver=None,
        )
        radio.send_civ = AsyncMock(return_value=short_resp)

        poller = _make_poller(radio, model="IC-7300")
        await poller._execute(SetBand(band=5))  # noqa: SLF001

        radio.set_freq.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_fallback_when_send_civ_raises(self) -> None:
        """Exception in send_civ should trigger fallback, not crash."""
        radio = _make_radio(model="IC-7300")
        radio.send_civ = AsyncMock(side_effect=TimeoutError("CI-V timeout"))

        poller = _make_poller(radio, model="IC-7300")
        await poller._execute(SetBand(band=3))  # noqa: SLF001

        # Fallback: default 40m freq
        radio.set_freq.assert_awaited_once()
        freq_arg = radio.set_freq.call_args[0][0]
        assert 6_900_000 <= freq_arg <= 7_500_000  # within 40m band

    @pytest.mark.asyncio
    async def test_unknown_bsr_code_no_crash(self) -> None:
        """Unknown BSR code with no matching band should not crash."""
        radio = _make_radio(model="IC-7300")
        radio.send_civ = AsyncMock(return_value=None)

        poller = _make_poller(radio, model="IC-7300")
        # band=99 doesn't exist in any profile
        await poller._execute(SetBand(band=99))  # noqa: SLF001

        # No frequency set (unknown band, no fallback)
        radio.set_freq.assert_not_awaited()


# ---------------------------------------------------------------------------
# Rig profile BSR code tests
# ---------------------------------------------------------------------------


class TestRigProfileBSRCodes:
    """Verify BSR codes are defined in rig profiles."""

    @pytest.mark.parametrize("model", ["IC-7300", "IC-7610"])
    def test_profile_has_bsr_codes(self, model: str) -> None:
        """Rig profile bands should have bsr_code defined."""
        profile = resolve_radio_profile(model=model)
        bands_with_bsr = []
        for fr in profile.freq_ranges:
            for bi in fr.bands:
                if bi.bsr_code is not None:
                    bands_with_bsr.append(bi.name)
        assert len(bands_with_bsr) >= 10, (
            f"{model} should have BSR codes for >=10 bands"
        )

    @pytest.mark.parametrize("model", ["IC-7300", "IC-7610"])
    def test_bsr_codes_unique_per_profile(self, model: str) -> None:
        """BSR codes must be unique within a profile."""
        profile = resolve_radio_profile(model=model)
        codes = []
        for fr in profile.freq_ranges:
            for bi in fr.bands:
                if bi.bsr_code is not None:
                    codes.append(bi.bsr_code)
        assert len(codes) == len(set(codes)), f"Duplicate BSR codes in {model}"

    @pytest.mark.parametrize(
        "model,band_name,expected_bsr",
        [
            ("IC-7300", "160m", 1),
            ("IC-7300", "80m", 2),
            ("IC-7300", "40m", 3),
            ("IC-7300", "20m", 5),
            ("IC-7300", "6m", 10),
            ("IC-7610", "160m", 1),
            ("IC-7610", "20m", 5),
        ],
    )
    def test_specific_bsr_codes(
        self, model: str, band_name: str, expected_bsr: int
    ) -> None:
        """Verify specific BSR codes match CI-V spec."""
        profile = resolve_radio_profile(model=model)
        for fr in profile.freq_ranges:
            for bi in fr.bands:
                if bi.name == band_name:
                    assert bi.bsr_code == expected_bsr, (
                        f"{model} {band_name}: expected BSR {expected_bsr}, got {bi.bsr_code}"
                    )
                    return
        pytest.fail(f"Band {band_name} not found in {model} profile")
