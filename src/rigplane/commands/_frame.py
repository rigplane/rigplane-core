"""CI-V frame builders, parser, and all command/sub-command constants.

This is the kernel of the commands package -- every other module imports
from here, but this module imports nothing from siblings.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from ..types import CivFrame

if TYPE_CHECKING:
    from ..command_map import CommandMap

# CI-V addresses
CONTROLLER_ADDR = 0xE0

# Receiver IDs for Command29 (dual-receiver radios)
RECEIVER_MAIN = 0x00
RECEIVER_SUB = 0x01
_CMD_RECEIVER_PREFIX = 0x29

# CI-V command codes
_CMD_FREQ_GET = 0x03
_CMD_MODE_GET = 0x04
_CMD_FREQ_SET = 0x05
_CMD_MODE_SET = 0x06
_CMD_LEVEL = 0x14  # Levels (RF power, etc.)
_CMD_METER = 0x15  # Meter readings
_CMD_PTT = 0x1C  # Transceiver status / PTT
_CMD_CTL_MEM = 0x1A  # Memory / configuration command
_CMD_BAND_EDGE = 0x02  # Band edge frequency
_CMD_RIT = 0x21  # RIT/XIT
_CMD_TONE = 0x1B  # Tone/TSQL frequency
_CMD_MEMORY_MODE = 0x08  # Memory mode (select channel)
_CMD_MEMORY_WRITE = 0x09  # Memory write
_CMD_MEMORY_TO_VFO = 0x0A  # Memory to VFO
_CMD_MEMORY_CLEAR = 0x0B  # Memory clear
_CMD_TX_BAND_EDGE = 0x1E  # TX band edge frequencies
_CMD_SELECTED_FREQ = 0x25  # Selected/Unselected receiver frequency
_CMD_SELECTED_MODE = 0x26  # Selected/Unselected receiver mode
_CMD_ACK = 0xFB
_CMD_NAK = 0xFA

# Sub-commands for 0x14 (Levels)
_SUB_AF_LEVEL = 0x01
_SUB_RF_GAIN = 0x02
_SUB_SQL = 0x03
_SUB_APF_TYPE_LEVEL = 0x05
_SUB_NR_LEVEL = 0x06
_SUB_PBT_INNER = 0x07
_SUB_PBT_OUTER = 0x08
_SUB_CW_PITCH = 0x09
_SUB_RF_POWER = 0x0A
_SUB_MIC_GAIN = 0x0B
_SUB_KEY_SPEED = 0x0C
_SUB_NOTCH_FILTER = 0x0D
_SUB_COMPRESSOR_LEVEL = 0x0E
_SUB_BREAK_IN_DELAY = 0x0F
_SUB_NB_LEVEL = 0x12
_SUB_DIGISEL_SHIFT = 0x13
_SUB_DRIVE_GAIN = 0x14
_SUB_MONITOR_GAIN = 0x15
_SUB_VOX_GAIN = 0x16
_SUB_ANTI_VOX_GAIN = 0x17

# Sub-commands for 0x15 (Meters)
_SUB_S_METER = 0x02
_SUB_VARIOUS_SQUELCH = 0x05
_SUB_POWER_METER = 0x11
_SUB_SWR_METER = 0x12
_SUB_ALC_METER = 0x13
_SUB_COMP_METER = 0x14
_SUB_VD_METER = 0x15
_SUB_ID_METER = 0x16

# Sub-commands for 0x1C (PTT / Transceiver status)
_SUB_PTT = 0x00
_SUB_TUNER_STATUS = 0x01
_SUB_XFC_STATUS = 0x02
_SUB_TX_FREQ_MONITOR = 0x03

# Sub-commands for 0x1A (CTL_MEM)
_SUB_CTL_MEM = 0x05
_SUB_DATA_MODE = 0x06
_SUB_AF_MUTE = 0x09
_SUB_MEMORY_CONTENTS = 0x00
_SUB_BAND_STACK = 0x01
_SUB_AGC_TIME_CONSTANT = 0x04
_SUB_FILTER_WIDTH = 0x03

# CTL_MEM prefixes (0x1A 0x05 ...)
_CTL_MEM_REF_ADJUST = b"\x00\x70"
_CTL_MEM_DASH_RATIO = b"\x02\x28"
_CTL_MEM_NB_DEPTH = b"\x02\x90"
_CTL_MEM_NB_WIDTH = b"\x02\x91"
_CTL_MEM_VOX_DELAY = b"\x02\x92"
_CTL_MEM_DATA_OFF_MOD_INPUT = b"\x00\x91"
_CTL_MEM_DATA1_MOD_INPUT = b"\x00\x92"
_CTL_MEM_DATA2_MOD_INPUT = b"\x00\x93"
_CTL_MEM_DATA3_MOD_INPUT = b"\x00\x94"
_CTL_MEM_CIV_TRANSCEIVE = b"\x01\x29"
_CTL_MEM_CIV_OUTPUT_ANT = b"\x01\x30"
_CTL_MEM_SYSTEM_DATE = b"\x01\x58"
_CTL_MEM_SYSTEM_TIME = b"\x01\x59"
_CTL_MEM_UTC_OFFSET = b"\x01\x62"
_CTL_MEM_QUICK_DUAL_WATCH = b"\x00\x32"
_CTL_MEM_QUICK_SPLIT = b"\x00\x33"

# Antenna command (0x12)
_CMD_ANTENNA = 0x12
_SUB_ANT1 = 0x00
_SUB_ANT2 = 0x01
_SUB_RX_ANT_ANT1 = 0x12
_SUB_RX_ANT_ANT2 = 0x13

# Modulation level sub-commands for 0x14
_SUB_ACC1_MOD_LEVEL = 0x0B
_SUB_USB_MOD_LEVEL = 0x10
_SUB_LAN_MOD_LEVEL = 0x11

# VFO / Scan / Dual Watch
_CMD_VFO_SELECT = 0x07
_CMD_VFO_EQUAL = 0x07
_CMD_SPLIT = 0x0F
_CMD_SCAN = 0x0E
_CMD_TUNING_STEP = 0x10
_VFO_DUAL_WATCH_OFF = 0xC0
_VFO_DUAL_WATCH_ON = 0xC1
_VFO_DUAL_WATCH_QUERY = 0xC2

# ATT / Preamp / DSP function sub-commands (0x11 / 0x16)
_CMD_ATT = 0x11
_CMD_PREAMP = 0x16
_SUB_S_METER_SQL_STATUS = 0x01
_SUB_OVERFLOW_STATUS = 0x07
_SUB_PREAMP_STATUS = 0x02
_SUB_AGC = 0x12
_SUB_AUDIO_PEAK_FILTER = 0x32
_SUB_AUTO_NOTCH = 0x41
_SUB_COMPRESSOR = 0x44
_SUB_MONITOR = 0x45
_SUB_VOX = 0x46
_SUB_BREAK_IN = 0x47
_SUB_MANUAL_NOTCH = 0x48
_SUB_MANUAL_NOTCH_WIDTH = 0x57
_SUB_DIGISEL_STATUS = 0x4E
_SUB_TWIN_PEAK_FILTER = 0x4F
_SUB_DIAL_LOCK = 0x50
_SUB_FILTER_SHAPE = 0x56
_SUB_SSB_TX_BANDWIDTH = 0x58
_SUB_NB = 0x22
_SUB_NR = 0x40
_SUB_IP_PLUS = 0x65
_SUB_MAIN_SUB_TRACKING = 0x5E
_SUB_REPEATER_TONE = 0x42
_SUB_REPEATER_TSQL = 0x43

# Tone frequency sub-commands (0x1B)
_SUB_TONE_FREQ = 0x00
_SUB_TSQL_FREQ = 0x01

# RIT sub-commands (0x21)
_SUB_RIT_FREQ = 0x00
_SUB_RIT_STATUS = 0x01
_SUB_RIT_TX_STATUS = 0x02

# CW keying
_CMD_SEND_CW = 0x17

# Power control
_CMD_POWER_CTRL = 0x18

# Speech
_CMD_SPEECH = 0x13

# Transceiver ID
_CMD_TRANSCEIVER_ID = 0x19
_SUB_TRANSCEIVER_ID = 0x00

# Scope / Waterfall (0x27)
_CMD_SCOPE = 0x27
_SUB_SCOPE_ON = 0x10
_SUB_SCOPE_DATA_OUTPUT = 0x11
_SUB_SCOPE_MAIN_SUB = 0x12
_SUB_SCOPE_SINGLE_DUAL = 0x13
_SUB_SCOPE_MODE = 0x14
_SUB_SCOPE_SPAN = 0x15
_SUB_SCOPE_EDGE = 0x16
_SUB_SCOPE_HOLD = 0x17
_SUB_SCOPE_REF = 0x19
_SUB_SCOPE_SPEED = 0x1A
_SUB_SCOPE_DURING_TX = 0x1B
_SUB_SCOPE_CENTER_TYPE = 0x1C
_SUB_SCOPE_VBW = 0x1D
_SUB_SCOPE_FIXED_EDGE = 0x1E
_SUB_SCOPE_RBW = 0x1F

# CI-V frame markers
_PREAMBLE = b"\xfe\xfe"
_TERMINATOR = b"\xfd"

# Commands that use sub-commands (for parse disambiguation)
_COMMANDS_WITH_SUB: set[int] = {
    _CMD_LEVEL,
    _CMD_METER,
    _CMD_PTT,
    _CMD_CTL_MEM,
    _CMD_RIT,
    0x27,
    0x16,
    _CMD_TONE,
    _CMD_ANTENNA,
    0x19,
}


def build_civ_frame(
    to_addr: int,
    from_addr: int,
    command: int,
    sub: int | None = None,
    data: bytes | None = None,
) -> bytes:
    """Build a CI-V frame.

    Args:
        to_addr: Destination CI-V address.
        from_addr: Source CI-V address.
        command: CI-V command byte.
        sub: Optional sub-command byte.
        data: Optional payload data.

    Returns:
        Complete CI-V frame bytes.
    """
    frame = bytearray(_PREAMBLE)
    frame.append(to_addr)
    frame.append(from_addr)
    frame.append(command)
    if sub is not None:
        frame.append(sub)
    if data:
        frame.extend(data)
    frame.extend(_TERMINATOR)
    return bytes(frame)


def build_cmd29_frame(
    to_addr: int,
    from_addr: int,
    command: int,
    sub: int | None = None,
    data: bytes | None = None,
    receiver: int = RECEIVER_MAIN,
) -> bytes:
    """Build a Command29-wrapped CI-V frame for dual-receiver radios.

    For commands marked Command29=true in IC-7610.rig, the frame format is::

        FE FE <to> <from> 29 <receiver> <cmd> [<sub>] [<data>...] FD

    The 0x29 prefix tells the radio which receiver (MAIN/SUB) the command
    targets, without requiring a VFO select first.

    Args:
        to_addr: Destination CI-V address.
        from_addr: Source CI-V address.
        command: Original CI-V command byte (e.g. 0x11 for ATT, 0x16 for PREAMP).
        sub: Optional sub-command byte.
        data: Optional payload data.
        receiver: RECEIVER_MAIN (0x00) or RECEIVER_SUB (0x01).

    Returns:
        Complete CI-V frame bytes with Command29 prefix.
    """
    inner = bytearray()
    inner.append(command)
    if sub is not None:
        inner.append(sub)
    if data:
        inner.extend(data)
    return build_civ_frame(
        to_addr,
        from_addr,
        _CMD_RECEIVER_PREFIX,
        data=bytes([receiver]) + bytes(inner),
    )


def _build_from_map(
    cmd_map: CommandMap,
    name: str,
    to_addr: int,
    from_addr: int = CONTROLLER_ADDR,
    data: bytes | None = None,
    receiver: int = RECEIVER_MAIN,
    command29: bool = False,
) -> bytes:
    """Build a CI-V frame using wire bytes from a CommandMap.

    Wire bytes may have 1-N elements.  The first byte is the CI-V command,
    the second (if present) is the sub-command, and any remaining bytes are
    prepended to *data* as extended sub-command addressing (e.g. 0x1A 0x05
    0x00 0x64 for IC-7300 ACC1 mod level).
    """
    wire = cmd_map.get(name)
    command = wire[0]
    sub = wire[1] if len(wire) > 1 else None
    # Extra wire bytes beyond command+sub become a data prefix
    if len(wire) > 2:
        extra = bytes(wire[2:])
        data = extra + data if data else extra
    if command29:
        return build_cmd29_frame(
            to_addr, from_addr, command, sub=sub, data=data, receiver=receiver
        )
    return build_civ_frame(to_addr, from_addr, command, sub=sub, data=data)


def parse_civ_frame(data: bytes) -> CivFrame:
    """Parse a CI-V frame into a CivFrame.

    Args:
        data: Raw CI-V frame bytes (including FE FE preamble and FD terminator).

    Returns:
        Parsed CivFrame.

    Raises:
        ValueError: If frame is malformed.
    """
    if len(data) < 6:
        raise ValueError(f"CI-V frame too short: {len(data)} bytes")
    if data[:2] != _PREAMBLE:
        raise ValueError(f"Invalid CI-V preamble: {data[:2].hex()}")
    if data[-1:] != _TERMINATOR:
        raise ValueError(f"Missing CI-V terminator: {data[-1]:02x}")

    to_addr = data[2]
    from_addr = data[3]
    command = data[4]
    payload = data[5:-1]

    # Handle Command29 prefix (dual-receiver): unwrap 0x29 <receiver> <real_cmd> ...
    if command == _CMD_RECEIVER_PREFIX and len(payload) >= 2:
        receiver = payload[0]
        real_command = payload[1]
        inner_payload = payload[2:]
        # Check if real command uses sub-commands
        if real_command in _COMMANDS_WITH_SUB and len(inner_payload) >= 1:
            return CivFrame(
                to_addr=to_addr,
                from_addr=from_addr,
                command=real_command,
                sub=inner_payload[0],
                data=bytes(inner_payload[1:]),
                receiver=receiver,
            )
        # PREAMP (0x16) uses sub-commands too
        if real_command == _CMD_PREAMP and len(inner_payload) >= 1:
            return CivFrame(
                to_addr=to_addr,
                from_addr=from_addr,
                command=real_command,
                sub=inner_payload[0],
                data=bytes(inner_payload[1:]),
                receiver=receiver,
            )
        return CivFrame(
            to_addr=to_addr,
            from_addr=from_addr,
            command=real_command,
            sub=None,
            data=bytes(inner_payload),
            receiver=receiver,
        )

    # Determine if first payload byte is a sub-command
    if command in _COMMANDS_WITH_SUB and len(payload) >= 1:
        return CivFrame(
            to_addr=to_addr,
            from_addr=from_addr,
            command=command,
            sub=payload[0],
            data=bytes(payload[1:]),
        )

    return CivFrame(
        to_addr=to_addr,
        from_addr=from_addr,
        command=command,
        sub=None,
        data=bytes(payload),
    )


def parse_ack_nak(frame: CivFrame) -> bool | None:
    """Check if frame is ACK (0xFB) or NAK (0xFA).

    Args:
        frame: Parsed CivFrame.

    Returns:
        True for ACK, False for NAK, None if neither.
    """
    if frame.command == _CMD_ACK:
        return True
    if frame.command == _CMD_NAK:
        return False
    return None
