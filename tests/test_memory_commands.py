"""Tests for Memory Subsystem API (Part A of issue #133).

Covers:
- MemoryCapable protocol type checking
- CI-V command builders produce correct frames
- Parsers handle valid/invalid data
- Radio methods call correct builders
- Web handler dispatch for memory commands
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from rigplane.commands import (
    CONTROLLER_ADDR,
    bcd_encode_value,
    build_memory_clear,
    build_memory_contents_set,
    build_memory_mode_set,
    build_memory_to_vfo,
    build_memory_write,
    parse_memory_contents_response,
)
from rigplane.radio_protocol import MemoryCapable
from rigplane.types import BandStackRegister, CivFrame, MemoryChannel


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

RADIO_ADDR = 0x98  # IC-7610 default CI-V address


# ---------------------------------------------------------------------------
# MemoryCapable protocol type-checking
# ---------------------------------------------------------------------------


class TestMemoryCapableProtocol:
    """Verify MemoryCapable is a runtime-checkable Protocol."""

    def test_class_with_all_methods_is_memory_capable(self) -> None:
        class FakeRadio:
            async def set_memory_mode(self, channel: int) -> None: ...
            async def memory_write(self) -> None: ...
            async def memory_to_vfo(self, channel: int) -> None: ...
            async def memory_clear(self, channel: int) -> None: ...
            async def set_memory_contents(self, mem: MemoryChannel) -> None: ...
            async def set_bsr(self, bsr: BandStackRegister) -> None: ...

        assert isinstance(FakeRadio(), MemoryCapable)

    def test_class_missing_method_is_not_memory_capable(self) -> None:
        class IncompleteRadio:
            async def set_memory_mode(self, channel: int) -> None: ...

            # missing memory_write, etc.

        assert not isinstance(IncompleteRadio(), MemoryCapable)

    def test_protocol_in_all(self) -> None:
        from rigplane import radio_protocol

        assert "MemoryCapable" in radio_protocol.__all__


# ---------------------------------------------------------------------------
# CI-V command builders
# ---------------------------------------------------------------------------


class TestMemoryModeSetBuilder:
    """build_memory_mode_set produces correct CI-V frames."""

    def test_channel_1(self) -> None:
        frame = build_memory_mode_set(1, to_addr=RADIO_ADDR)
        # 0xFE 0xFE <to> <from> 0x08 <BCD channel 2 bytes> 0xFD
        assert frame[:4] == b"\xfe\xfe\x98\xe0"
        assert frame[4] == 0x08  # command
        # Channel 1 in 2-byte BCD
        assert frame[-1:] == b"\xfd"

    def test_channel_101(self) -> None:
        frame = build_memory_mode_set(101, to_addr=RADIO_ADDR)
        assert frame[4] == 0x08

    def test_channel_0_raises(self) -> None:
        with pytest.raises(ValueError, match="Channel must be 1-101"):
            build_memory_mode_set(0, to_addr=RADIO_ADDR)

    def test_channel_102_raises(self) -> None:
        with pytest.raises(ValueError, match="Channel must be 1-101"):
            build_memory_mode_set(102, to_addr=RADIO_ADDR)


class TestMemoryWriteBuilder:
    """build_memory_write produces correct CI-V frames."""

    def test_basic_frame(self) -> None:
        frame = build_memory_write(to_addr=RADIO_ADDR)
        assert frame[:4] == b"\xfe\xfe\x98\xe0"
        assert frame[4] == 0x09  # command
        assert frame[-1:] == b"\xfd"


class TestMemoryToVfoBuilder:
    """build_memory_to_vfo produces correct CI-V frames."""

    def test_channel_50(self) -> None:
        frame = build_memory_to_vfo(50, to_addr=RADIO_ADDR)
        assert frame[4] == 0x0A  # command

    def test_channel_0_raises(self) -> None:
        with pytest.raises(ValueError, match="Channel must be 1-101"):
            build_memory_to_vfo(0, to_addr=RADIO_ADDR)


class TestMemoryClearBuilder:
    """build_memory_clear produces correct CI-V frames."""

    def test_channel_10(self) -> None:
        frame = build_memory_clear(10, to_addr=RADIO_ADDR)
        assert frame[4] == 0x0B  # command

    def test_channel_0_raises(self) -> None:
        with pytest.raises(ValueError, match="Channel must be 1-101"):
            build_memory_clear(0, to_addr=RADIO_ADDR)


class TestMemoryContentsSetBuilder:
    """build_memory_contents_set produces correct CI-V frames."""

    def test_basic_channel(self) -> None:
        mem = MemoryChannel(
            channel=1,
            frequency_hz=14_074_000,
            mode=0x01,  # USB
            filter=1,
            scan=0,
            datamode=0,
            tonemode=0,
            name="FT8",
        )
        frame = build_memory_contents_set(mem, to_addr=RADIO_ADDR)
        assert frame[:4] == b"\xfe\xfe\x98\xe0"
        assert frame[4] == 0x1A  # command
        assert frame[5] == 0x00  # sub-command
        assert frame[-1:] == b"\xfd"

    def test_invalid_type_raises(self) -> None:
        with pytest.raises(TypeError, match="Expected MemoryChannel"):
            build_memory_contents_set({"channel": 1}, to_addr=RADIO_ADDR)  # type: ignore[arg-type]

    def test_invalid_channel_raises(self) -> None:
        mem = MemoryChannel(
            channel=0,
            frequency_hz=14_074_000,
            mode=0x01,
            filter=1,
            scan=0,
            datamode=0,
            tonemode=0,
        )
        with pytest.raises(ValueError, match="Channel must be 1-101"):
            build_memory_contents_set(mem, to_addr=RADIO_ADDR)


# ---------------------------------------------------------------------------
# Parser tests
# ---------------------------------------------------------------------------


class TestParseMemoryContentsResponse:
    """parse_memory_contents_response handles valid/invalid data."""

    def _make_frame(
        self,
        channel: int = 1,
        freq_bcd: bytes = b"\x00\x00\x40\x07\x14",  # 14_074_000 BCD-LE
        mode: int = 0x01,
        filt: int = 0x01,
        scan: int = 0x00,
        datamode_tonemode: int = 0x00,
        name: bytes = b"FT8\x00\x00\x00\x00\x00\x00\x00",
    ) -> CivFrame:
        """Build a synthetic memory contents response frame."""
        ch_bcd = bcd_encode_value(channel, byte_count=2)
        # 2(channel) + 1(scan) + 5(freq) + 1(mode) + 1(filter)
        # + 1(datamode|tonemode) + 3(tone) + 3(tsql) + 10(name) = 27
        # total data = channel(2) + payload(26) = 28
        # channel(2) + scan(1) + freq(5) + mode(1) + filter(1)
        # + datamode|tonemode(1) + tone(3) + tsql(3) + name(10) + padding(1) = 28
        data = (
            ch_bcd
            + bytes([scan])
            + freq_bcd
            + bytes([mode, filt, datamode_tonemode])
            + b"\x00\x00\x00"  # tone_freq
            + b"\x00\x00\x00"  # tsql_freq
            + name
            + b"\x00"  # padding to reach 28 bytes
        )
        return CivFrame(
            to_addr=CONTROLLER_ADDR,
            from_addr=RADIO_ADDR,
            command=0x1A,
            sub=0x00,
            data=data,
        )

    def test_valid_response(self) -> None:
        frame = self._make_frame(channel=5)
        mem = parse_memory_contents_response(frame)
        assert mem.channel == 5
        assert mem.mode == 1  # USB
        assert mem.filter == 1
        assert mem.name == "FT8"

    def test_wrong_command_raises(self) -> None:
        frame = CivFrame(
            to_addr=CONTROLLER_ADDR,
            from_addr=RADIO_ADDR,
            command=0x03,
            sub=None,
            data=b"\x00" * 28,
        )
        with pytest.raises(ValueError, match="Not a memory contents response"):
            parse_memory_contents_response(frame)

    def test_too_short_data_raises(self) -> None:
        frame = CivFrame(
            to_addr=CONTROLLER_ADDR,
            from_addr=RADIO_ADDR,
            command=0x1A,
            sub=0x00,
            data=b"\x00" * 10,
        )
        with pytest.raises(ValueError, match="too short"):
            parse_memory_contents_response(frame)


# ---------------------------------------------------------------------------
# Radio method tests (IcomRadio calls correct builders)
# ---------------------------------------------------------------------------


class TestIcomRadioMemoryMethods:
    """Verify IcomRadio memory methods call the correct builders."""

    def _make_radio(self) -> MagicMock:
        radio = MagicMock()
        radio._radio_addr = RADIO_ADDR
        radio._send_fire_and_forget = AsyncMock()
        return radio

    @pytest.mark.asyncio
    async def test_set_memory_mode_calls_builder(self) -> None:
        from rigplane.radio import IcomRadio

        radio = self._make_radio()
        # Bind the real method to our mock
        bound = IcomRadio.set_memory_mode.__get__(radio, type(radio))
        await bound(5)
        radio._send_fire_and_forget.assert_called_once()
        frame_arg = radio._send_fire_and_forget.call_args[0][0]
        assert frame_arg[4] == 0x08  # memory mode command

    @pytest.mark.asyncio
    async def test_memory_write_calls_builder(self) -> None:
        from rigplane.radio import IcomRadio

        radio = self._make_radio()
        bound = IcomRadio.memory_write.__get__(radio, type(radio))
        await bound()
        radio._send_fire_and_forget.assert_called_once()
        frame_arg = radio._send_fire_and_forget.call_args[0][0]
        assert frame_arg[4] == 0x09

    @pytest.mark.asyncio
    async def test_memory_to_vfo_calls_builder(self) -> None:
        from rigplane.radio import IcomRadio

        radio = self._make_radio()
        bound = IcomRadio.memory_to_vfo.__get__(radio, type(radio))
        await bound(42)
        radio._send_fire_and_forget.assert_called_once()
        frame_arg = radio._send_fire_and_forget.call_args[0][0]
        assert frame_arg[4] == 0x0A

    @pytest.mark.asyncio
    async def test_memory_clear_calls_builder(self) -> None:
        from rigplane.radio import IcomRadio

        radio = self._make_radio()
        bound = IcomRadio.memory_clear.__get__(radio, type(radio))
        await bound(7)
        radio._send_fire_and_forget.assert_called_once()
        frame_arg = radio._send_fire_and_forget.call_args[0][0]
        assert frame_arg[4] == 0x0B

    @pytest.mark.asyncio
    async def test_set_memory_mode_rejects_invalid_channel(self) -> None:
        from rigplane.radio import IcomRadio

        radio = self._make_radio()
        bound = IcomRadio.set_memory_mode.__get__(radio, type(radio))
        with pytest.raises(ValueError, match="Channel must be 1-101"):
            await bound(0)

    @pytest.mark.asyncio
    async def test_set_memory_contents_calls_builder(self) -> None:
        from rigplane.radio import IcomRadio

        radio = self._make_radio()
        bound = IcomRadio.set_memory_contents.__get__(radio, type(radio))
        mem = MemoryChannel(
            channel=1,
            frequency_hz=14_074_000,
            mode=0x01,
            filter=1,
            scan=0,
            datamode=0,
            tonemode=0,
        )
        await bound(mem)
        radio._send_fire_and_forget.assert_called_once()
        frame_arg = radio._send_fire_and_forget.call_args[0][0]
        assert frame_arg[4] == 0x1A
        assert frame_arg[5] == 0x00

    @pytest.mark.asyncio
    async def test_set_bsr_calls_builder(self) -> None:
        from rigplane.radio import IcomRadio

        radio = self._make_radio()
        bound = IcomRadio.set_bsr.__get__(radio, type(radio))
        bsr = BandStackRegister(
            band=5,
            register=1,
            frequency_hz=14_074_000,
            mode=0x01,
            filter=1,
        )
        await bound(bsr)
        radio._send_fire_and_forget.assert_called_once()
        frame_arg = radio._send_fire_and_forget.call_args[0][0]
        assert frame_arg[4] == 0x1A
        assert frame_arg[5] == 0x01


# ---------------------------------------------------------------------------
# Web handler dispatch tests
# ---------------------------------------------------------------------------


class TestHandlerMemoryDispatch:
    """Verify ControlHandler dispatches memory commands correctly."""

    def _make_handler(self, *, memory_capable: bool = True) -> Any:
        from rigplane.web.handlers import ControlHandler

        ws = MagicMock()
        ws.send_text = AsyncMock()

        radio = MagicMock()
        radio.connected = True
        radio.radio_ready = True
        radio.model = "IC-7610"
        radio.capabilities = {"memory"}

        if memory_capable:
            radio.set_memory_mode = AsyncMock()
            radio.memory_write = AsyncMock()
            radio.memory_to_vfo = AsyncMock()
            radio.memory_clear = AsyncMock()
            radio.set_memory_contents = AsyncMock()
            radio.set_bsr = AsyncMock()

        server = MagicMock()
        from rigplane.web.radio_poller import CommandQueue

        server.command_queue = CommandQueue()

        handler = ControlHandler(
            ws=ws,
            radio=radio,
            server_version="test",
            radio_model="IC-7610",
            server=server,
        )
        return handler, server.command_queue

    @pytest.mark.asyncio
    async def test_set_memory_mode_dispatch(self) -> None:
        handler, q = self._make_handler()
        result = await handler._enqueue_command("set_memory_mode", {"channel": 5})
        assert result == {"channel": 5}
        cmds = q.drain()
        assert len(cmds) == 1
        from rigplane.web.radio_poller import SetMemoryMode

        assert isinstance(cmds[0], SetMemoryMode)
        assert cmds[0].channel == 5

    @pytest.mark.asyncio
    async def test_memory_write_dispatch(self) -> None:
        handler, q = self._make_handler()
        result = await handler._enqueue_command("memory_write", {})
        assert result == {}
        cmds = q.drain()
        assert len(cmds) == 1
        from rigplane.web.radio_poller import MemoryWrite

        assert isinstance(cmds[0], MemoryWrite)

    @pytest.mark.asyncio
    async def test_memory_to_vfo_dispatch(self) -> None:
        handler, q = self._make_handler()
        result = await handler._enqueue_command("memory_to_vfo", {"channel": 42})
        assert result == {"channel": 42}
        cmds = q.drain()
        assert len(cmds) == 1
        from rigplane.web.radio_poller import MemoryToVfo

        assert isinstance(cmds[0], MemoryToVfo)
        assert cmds[0].channel == 42

    @pytest.mark.asyncio
    async def test_memory_clear_dispatch(self) -> None:
        handler, q = self._make_handler()
        result = await handler._enqueue_command("memory_clear", {"channel": 10})
        assert result == {"channel": 10}
        cmds = q.drain()
        assert len(cmds) == 1
        from rigplane.web.radio_poller import MemoryClear

        assert isinstance(cmds[0], MemoryClear)
        assert cmds[0].channel == 10

    @pytest.mark.asyncio
    async def test_set_memory_contents_dispatch(self) -> None:
        handler, q = self._make_handler()
        params = {
            "channel": 1,
            "frequency_hz": 14_074_000,
            "mode": 1,
            "filter": 1,
            "scan": 0,
            "datamode": 0,
            "tonemode": 0,
            "name": "FT8",
        }
        result = await handler._enqueue_command("set_memory_contents", params)
        assert result == {"channel": 1}
        cmds = q.drain()
        assert len(cmds) == 1
        from rigplane.web.radio_poller import SetMemoryContents

        assert isinstance(cmds[0], SetMemoryContents)
        assert cmds[0].mem.channel == 1
        assert cmds[0].mem.frequency_hz == 14_074_000

    @pytest.mark.asyncio
    async def test_set_memory_contents_ignores_transport_session_id(self) -> None:
        handler, q = self._make_handler()
        params = {
            "channel": 1,
            "frequency_hz": 14_074_000,
            "mode": 1,
            "filter": 1,
            "scan": 0,
            "datamode": 0,
            "tonemode": 0,
            "name": "FT8",
            "session_id": "ws-a",
        }

        result = await handler._enqueue_command("set_memory_contents", params)

        assert result == {"channel": 1}
        cmds = q.drain()
        assert len(cmds) == 1
        from rigplane.web.radio_poller import SetMemoryContents

        assert isinstance(cmds[0], SetMemoryContents)
        assert cmds[0].mem.channel == 1
        assert cmds[0].mem.frequency_hz == 14_074_000

    @pytest.mark.asyncio
    async def test_set_bsr_dispatch(self) -> None:
        handler, q = self._make_handler()
        params = {
            "band": 5,
            "register": 1,
            "frequency_hz": 14_074_000,
            "mode": 1,
            "filter": 1,
        }
        result = await handler._enqueue_command("set_bsr", params)
        assert result == {"band": 5, "register": 1}
        cmds = q.drain()
        assert len(cmds) == 1
        from rigplane.web.radio_poller import SetBsr

        assert isinstance(cmds[0], SetBsr)
        assert cmds[0].bsr.band == 5

    @pytest.mark.asyncio
    async def test_set_bsr_ignores_transport_session_id(self) -> None:
        handler, q = self._make_handler()
        params = {
            "band": 5,
            "register": 1,
            "frequency_hz": 14_074_000,
            "mode": 1,
            "filter": 1,
            "session_id": "ws-a",
        }

        result = await handler._enqueue_command("set_bsr", params)

        assert result == {"band": 5, "register": 1}
        cmds = q.drain()
        assert len(cmds) == 1
        from rigplane.web.radio_poller import SetBsr

        assert isinstance(cmds[0], SetBsr)
        assert cmds[0].bsr.band == 5

    @pytest.mark.asyncio
    async def test_memory_command_rejects_non_capable(self) -> None:
        """A radio without memory methods should fail the MemoryCapable check."""
        from rigplane.web.handlers import ControlHandler
        from rigplane.web.radio_poller import CommandQueue

        ws = MagicMock()
        ws.send_text = AsyncMock()

        # Use a minimal class that does NOT have memory methods
        class BareRadio:
            connected = True
            radio_ready = True
            model = "FakeRadio"
            capabilities: set[str] = set()

        radio = BareRadio()
        server = MagicMock()
        server.command_queue = CommandQueue()

        handler = ControlHandler(
            ws=ws,
            radio=radio,  # type: ignore[arg-type]
            server_version="test",
            radio_model="FakeRadio",
            server=server,
        )
        with pytest.raises(ValueError, match="MemoryCapable"):
            await handler._enqueue_command("set_memory_mode", {"channel": 1})

    @pytest.mark.asyncio
    async def test_set_memory_mode_validates_channel(self) -> None:
        handler, q = self._make_handler()
        with pytest.raises(ValueError, match="channel must be 1-101"):
            await handler._enqueue_command("set_memory_mode", {"channel": 200})


# ---------------------------------------------------------------------------
# TOML rig config tests
# ---------------------------------------------------------------------------


class TestIc7610TomlMemory:
    """Verify memory commands are enabled in ic7610.toml."""

    def test_memory_commands_in_toml(self) -> None:
        from rigplane.rig_loader import load_rig
        from pathlib import Path

        toml_path = Path(__file__).parents[1] / "rigs" / "ic7610.toml"
        config = load_rig(toml_path)
        # All memory commands should be present (not commented out)
        assert "set_memory_mode" in config.commands
        assert "memory_write" in config.commands
        assert "memory_to_vfo" in config.commands
        assert "memory_clear" in config.commands
        assert "set_memory_contents" in config.commands

    def test_vox_delay_not_marked_unimplemented(self) -> None:
        """VOX delay should be in commands without NOT_IMPLEMENTED flag."""
        from rigplane.rig_loader import load_rig
        from pathlib import Path

        toml_path = Path(__file__).parents[1] / "rigs" / "ic7610.toml"
        config = load_rig(toml_path)
        assert "get_vox_delay" in config.commands
        assert "set_vox_delay" in config.commands
