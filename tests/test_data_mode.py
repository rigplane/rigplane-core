"""Tests for IC-7610 DATA mode (CI-V 0x1A 0x06) support.

Covers:
- commands.py: get_data_mode, set_data_mode, parse_data_mode_response
- state_cache.py: data_mode field, update/invalidate, is_fresh, snapshot
- handler.py: PKTUSB/PKTLSB/PKTRTTY set_mode, get_mode with DATA active
- poller.py: data mode polled each cycle and stored in cache
"""

from __future__ import annotations

import time
from unittest.mock import AsyncMock

import pytest

from rigplane.commands import (
    get_data_mode,
    parse_civ_frame,
    parse_data_mode_response,
    set_data_mode,
)
from rigplane.rigctld.contract import RigctldCommand, RigctldConfig
from rigplane.rigctld.handler import RigctldHandler
from rigplane.rigctld.poller import RadioPoller
from rigplane.rigctld.state_cache import StateCache
from rigplane.types import CivFrame, Mode

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def set_cmd(long_cmd: str, *args: str) -> RigctldCommand:
    return RigctldCommand(
        short_cmd="", long_cmd=long_cmd, args=tuple(args), is_set=True
    )


def get_cmd(long_cmd: str, *args: str) -> RigctldCommand:
    return RigctldCommand(
        short_cmd="", long_cmd=long_cmd, args=tuple(args), is_set=False
    )


# ---------------------------------------------------------------------------
# commands.py — get_data_mode / set_data_mode
# ---------------------------------------------------------------------------


class TestGetDataModeCommand:
    def test_frame_bytes(self) -> None:
        frame = get_data_mode(to_addr=0x98)
        assert frame == b"\xfe\xfe\x98\xe0\x1a\x06\xfd"

    def test_custom_addresses(self) -> None:
        frame = get_data_mode(to_addr=0x94, from_addr=0xE1)
        assert frame == b"\xfe\xfe\x94\xe1\x1a\x06\xfd"


class TestSetDataModeCommand:
    def test_on(self) -> None:
        frame = set_data_mode(True, to_addr=0x98)
        assert frame == b"\xfe\xfe\x98\xe0\x1a\x06\x01\xfd"

    def test_off(self) -> None:
        frame = set_data_mode(False, to_addr=0x98)
        assert frame == b"\xfe\xfe\x98\xe0\x1a\x06\x00\xfd"

    def test_data2(self) -> None:
        frame = set_data_mode(2, to_addr=0x98)
        assert frame == b"\xfe\xfe\x98\xe0\x1a\x06\x02\xfd"

    def test_data3(self) -> None:
        frame = set_data_mode(3, to_addr=0x98)
        assert frame == b"\xfe\xfe\x98\xe0\x1a\x06\x03\xfd"

    def test_data3_sub_receiver_uses_cmd29(self) -> None:
        frame = set_data_mode(3, to_addr=0x98, receiver=1)
        assert frame == b"\xfe\xfe\x98\xe0\x29\x01\x1a\x06\x03\xfd"


class TestParseDataModeResponse:
    def _frame(self, data_byte: int) -> CivFrame:
        raw = bytes([0xFE, 0xFE, 0xE0, 0x98, 0x1A, 0x06, data_byte, 0xFD])
        return parse_civ_frame(raw)

    def test_data_off(self) -> None:
        frame = self._frame(0x00)
        assert parse_data_mode_response(frame) is False

    def test_data1(self) -> None:
        frame = self._frame(0x01)
        assert parse_data_mode_response(frame) is True

    def test_data2(self) -> None:
        frame = self._frame(0x02)
        assert parse_data_mode_response(frame) is True

    def test_data3(self) -> None:
        frame = self._frame(0x03)
        assert parse_data_mode_response(frame) is True

    def test_wrong_command_raises(self) -> None:
        frame = CivFrame(
            to_addr=0xE0, from_addr=0x98, command=0x04, sub=None, data=b"\x01"
        )
        with pytest.raises(ValueError, match="Not a DATA mode response"):
            parse_data_mode_response(frame)

    def test_wrong_sub_raises(self) -> None:
        frame = CivFrame(
            to_addr=0xE0, from_addr=0x98, command=0x1A, sub=0x05, data=b"\x01"
        )
        with pytest.raises(ValueError, match="Not a DATA mode response"):
            parse_data_mode_response(frame)

    def test_no_data_raises(self) -> None:
        frame = CivFrame(to_addr=0xE0, from_addr=0x98, command=0x1A, sub=0x06, data=b"")
        with pytest.raises(ValueError, match="no data byte"):
            parse_data_mode_response(frame)


# ---------------------------------------------------------------------------
# state_cache.py — data_mode field
# ---------------------------------------------------------------------------


class TestStateCacheDataMode:
    def test_default_false(self) -> None:
        cache = StateCache()
        assert cache.data_mode is False
        assert cache.data_mode_ts == 0.0

    def test_update_sets_value_and_timestamp(self) -> None:
        cache = StateCache()
        before = time.monotonic()
        cache.update_data_mode(True)
        after = time.monotonic()
        assert cache.data_mode is True
        assert before <= cache.data_mode_ts <= after

    def test_invalidate_resets_timestamp(self) -> None:
        cache = StateCache()
        cache.update_data_mode(True)
        cache.invalidate_data_mode()
        assert cache.data_mode_ts == 0.0
        assert cache.data_mode is True  # value preserved, just stale

    def test_is_fresh_after_update(self) -> None:
        cache = StateCache()
        cache.update_data_mode(False)
        assert cache.is_fresh("data_mode", 60.0) is True

    def test_not_fresh_before_update(self) -> None:
        cache = StateCache()
        assert cache.is_fresh("data_mode", 60.0) is False

    def test_not_fresh_after_invalidate(self) -> None:
        cache = StateCache()
        cache.update_data_mode(True)
        cache.invalidate_data_mode()
        assert cache.is_fresh("data_mode", 60.0) is False

    def test_snapshot_includes_data_mode(self) -> None:
        cache = StateCache()
        cache.update_data_mode(True)
        snap = cache.snapshot()
        assert snap["data_mode"] is True
        assert snap["data_mode_age"] is not None
        assert snap["data_mode_age"] >= 0.0

    def test_snapshot_data_mode_age_none_before_update(self) -> None:
        cache = StateCache()
        snap = cache.snapshot()
        assert snap["data_mode_age"] is None


# ---------------------------------------------------------------------------
# handler.py — PKTUSB / PKTLSB set_mode
# ---------------------------------------------------------------------------


@pytest.fixture
def config() -> RigctldConfig:
    return RigctldConfig()


@pytest.fixture
def mock_radio() -> AsyncMock:
    radio = AsyncMock()
    radio.get_data_mode.return_value = False
    return radio


@pytest.fixture
def handler(mock_radio: AsyncMock, config: RigctldConfig) -> RigctldHandler:
    return RigctldHandler(mock_radio, config)


class TestHandlerPktUsb:
    @pytest.mark.asyncio
    async def test_set_pktusb_sets_mode_usb(
        self, handler: RigctldHandler, mock_radio: AsyncMock
    ) -> None:
        resp = await handler.execute(set_cmd("set_mode", "PKTUSB"))
        assert resp.ok
        mock_radio.set_mode.assert_awaited_once_with("USB", filter_width=None)

    @pytest.mark.asyncio
    async def test_set_pktusb_enables_data_mode(
        self, handler: RigctldHandler, mock_radio: AsyncMock
    ) -> None:
        await handler.execute(set_cmd("set_mode", "PKTUSB"))
        mock_radio.set_data_mode.assert_awaited_once_with(True)

    @pytest.mark.asyncio
    async def test_set_pktlsb_sets_mode_lsb(
        self, handler: RigctldHandler, mock_radio: AsyncMock
    ) -> None:
        resp = await handler.execute(set_cmd("set_mode", "PKTLSB"))
        assert resp.ok
        mock_radio.set_mode.assert_awaited_once_with("LSB", filter_width=None)

    @pytest.mark.asyncio
    async def test_set_pktlsb_enables_data_mode(
        self, handler: RigctldHandler, mock_radio: AsyncMock
    ) -> None:
        await handler.execute(set_cmd("set_mode", "PKTLSB"))
        mock_radio.set_data_mode.assert_awaited_once_with(True)

    @pytest.mark.asyncio
    async def test_set_non_packet_mode_does_not_force_data_change(
        self, handler: RigctldHandler, mock_radio: AsyncMock
    ) -> None:
        await handler.execute(set_cmd("set_mode", "USB"))
        mock_radio.set_data_mode.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_set_pktrtty_sets_mode_rtty_and_enables_data(
        self, handler: RigctldHandler, mock_radio: AsyncMock
    ) -> None:
        resp = await handler.execute(set_cmd("set_mode", "PKTRTTY"))
        assert resp.ok
        mock_radio.set_mode.assert_awaited_once_with("RTTY", filter_width=None)
        mock_radio.set_data_mode.assert_awaited_once_with(True)


class TestHandlerGetModeWithDataMode:
    @pytest.mark.asyncio
    async def test_get_mode_usb_with_data_returns_pktusb(
        self, handler: RigctldHandler, mock_radio: AsyncMock
    ) -> None:
        mock_radio.get_mode_info.return_value = (Mode.USB, 1)
        mock_radio.get_data_mode.return_value = True
        resp = await handler.execute(get_cmd("get_mode"))
        assert resp.ok
        assert resp.values[0] == "PKTUSB"

    @pytest.mark.asyncio
    async def test_get_mode_lsb_with_data_returns_pktlsb(
        self, handler: RigctldHandler, mock_radio: AsyncMock
    ) -> None:
        mock_radio.get_mode_info.return_value = (Mode.LSB, 1)
        mock_radio.get_data_mode.return_value = True
        resp = await handler.execute(get_cmd("get_mode"))
        assert resp.ok
        assert resp.values[0] == "PKTLSB"

    @pytest.mark.asyncio
    async def test_get_mode_usb_data_off_returns_usb(
        self, handler: RigctldHandler, mock_radio: AsyncMock
    ) -> None:
        mock_radio.get_mode_info.return_value = (Mode.USB, 1)
        mock_radio.get_data_mode.return_value = False
        resp = await handler.execute(get_cmd("get_mode"))
        assert resp.values[0] == "USB"

    @pytest.mark.asyncio
    async def test_get_mode_rtty_with_data_returns_pktrtty(
        self, handler: RigctldHandler, mock_radio: AsyncMock
    ) -> None:
        mock_radio.get_mode_info.return_value = (Mode.RTTY, 1)
        mock_radio.get_data_mode.return_value = True
        resp = await handler.execute(get_cmd("get_mode"))
        assert resp.ok
        assert resp.values[0] == "PKTRTTY"

    @pytest.mark.asyncio
    async def test_get_mode_am_with_data_stays_am(
        self, handler: RigctldHandler, mock_radio: AsyncMock
    ) -> None:
        """DATA mode does not affect non-USB/LSB modes."""
        mock_radio.get_mode_info.return_value = (Mode.AM, None)
        mock_radio.get_data_mode.return_value = True
        resp = await handler.execute(get_cmd("get_mode"))
        assert resp.values[0] == "AM"

    @pytest.mark.asyncio
    async def test_set_pktusb_then_get_returns_pktusb(
        self, handler: RigctldHandler, mock_radio: AsyncMock
    ) -> None:
        """Round-trip: set PKTUSB, then get_mode returns PKTUSB from cache."""
        mock_radio.get_mode_info.return_value = (Mode.USB, 1)
        mock_radio.get_data_mode.return_value = True

        await handler.execute(set_cmd("set_mode", "PKTUSB"))
        # Cache invalidated by set; next get_mode will re-query radio.
        resp = await handler.execute(get_cmd("get_mode"))
        assert resp.values[0] == "PKTUSB"


# ---------------------------------------------------------------------------
# handler.py — get_mode: data_mode from cache when mode is fresh
# ---------------------------------------------------------------------------


class TestHandlerGetModeCacheDataMode:
    @pytest.mark.asyncio
    async def test_cached_mode_uses_cached_data_mode(
        self, handler: RigctldHandler, mock_radio: AsyncMock
    ) -> None:
        """When mode is fresh in cache, data_mode is also served from cache."""
        mock_radio.get_mode_info.return_value = (Mode.USB, 1)
        mock_radio.get_data_mode.return_value = False
        # Populate cache
        resp1 = await handler.execute(get_cmd("get_mode"))
        assert resp1.values[0] == "USB"
        # Now the cache is fresh; second call should not hit radio
        resp2 = await handler.execute(get_cmd("get_mode"))
        assert resp2.values[0] == "USB"
        mock_radio.get_mode_info.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_set_mode_packet_updates_data_mode_cache(
        self, handler: RigctldHandler, mock_radio: AsyncMock
    ) -> None:
        """After set_mode(PKT*), data_mode cache is refreshed to True."""
        mock_radio.get_mode_info.return_value = (Mode.USB, 1)
        mock_radio.get_data_mode.return_value = False
        await handler.execute(get_cmd("get_mode"))  # populate cache
        handler._cache.update_data_mode(False)

        await handler.execute(set_cmd("set_mode", "PKTUSB"))
        assert handler._cache.data_mode is True
        assert handler._cache.data_mode_ts > 0.0


# ---------------------------------------------------------------------------
# poller.py — data mode polled each cycle
# ---------------------------------------------------------------------------


@pytest.fixture
def poll_config() -> RigctldConfig:
    return RigctldConfig(poll_interval=0.01)


@pytest.fixture
def poll_cache() -> StateCache:
    return StateCache()


@pytest.fixture
def poll_radio() -> AsyncMock:
    radio = AsyncMock()
    radio.get_freq.return_value = 14_074_000
    radio.get_mode_info.return_value = (Mode.USB, 1)
    radio.get_data_mode.return_value = True
    return radio


@pytest.fixture
def poller(
    poll_radio: AsyncMock, poll_cache: StateCache, poll_config: RigctldConfig
) -> RadioPoller:
    return RadioPoller(poll_radio, poll_cache, poll_config)


@pytest.mark.asyncio
async def test_poller_updates_data_mode_in_cache(
    poller: RadioPoller,
    poll_cache: StateCache,
    poll_radio: AsyncMock,
) -> None:
    import asyncio

    await poller.start()
    await asyncio.sleep(0.05)
    await poller.stop()
    assert poll_cache.data_mode is True
    assert poll_cache.is_fresh("data_mode", 1.0) is True


@pytest.mark.asyncio
async def test_poller_data_mode_false(
    poller: RadioPoller,
    poll_cache: StateCache,
    poll_radio: AsyncMock,
) -> None:
    import asyncio

    poll_radio.get_data_mode.return_value = False
    await poller.start()
    await asyncio.sleep(0.05)
    await poller.stop()
    assert poll_cache.data_mode is False


@pytest.mark.asyncio
async def test_poller_data_mode_error_does_not_crash(
    poller: RadioPoller,
    poll_radio: AsyncMock,
) -> None:
    import asyncio

    from rigplane.exceptions import TimeoutError as IcomTimeoutError

    poll_radio.get_data_mode.side_effect = IcomTimeoutError("timeout")
    await poller.start()
    await asyncio.sleep(0.05)
    assert poller._task is not None
    assert not poller._task.done()
    await poller.stop()
