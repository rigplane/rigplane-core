"""CI-V frame builders, parser, and all command/sub-command constants.

This is the kernel of the commands package -- every other module imports
from here, but this module imports nothing from siblings.
"""

from __future__ import annotations

import functools
from typing import TYPE_CHECKING, Any, TypeVar

from ..types import CivFrame

if TYPE_CHECKING:
    from collections.abc import Callable

    from ..command_map import CommandMap

_BuilderT = TypeVar("_BuilderT", bound="Callable[..., bytes]")

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

# Sub-command for 0x14 (Levels). The rest of the 0x14 family
# (`commands/levels.py`, MOR-2006 Steps 5..N module 2) is profile-only now:
# every other sub-command byte lives solely in `rigs/*.toml`, reached by
# name through the required ``cmd_map``, with no code-level constant left
# to read -- `_SUB_RF_POWER` itself survives only because
# `tests/test_backend_contract_matrix.py` and
# `tests/test_civ_command_profiling.py` build raw frames with it directly,
# never through `levels.py: get_rf_power`/`set_rf_power`.
_SUB_RF_POWER = 0x0A

# Sub-commands for 0x15 (Meters)
_SUB_S_METER = 0x02
_SUB_POWER_METER = 0x11
_SUB_SWR_METER = 0x12
_SUB_ALC_METER = 0x13

# Sub-commands for 0x1C (PTT / Transceiver status)
_SUB_PTT = 0x00

# Sub-commands for 0x1A (CTL_MEM)
_SUB_CTL_MEM = 0x05
_SUB_DATA_MODE = 0x06
_SUB_MEMORY_CONTENTS = 0x00
_SUB_BAND_STACK = 0x01
_SUB_AGC_TIME_CONSTANT = 0x04

# CTL_MEM prefixes (0x1A 0x05 ...). `commands/levels.py`'s own five
# (ref_adjust, dash_ratio, nb_depth, nb_width, vox_delay) are gone as of
# MOR-2006 Steps 5..N module 2 -- their menu addresses live only in
# `rigs/*.toml` now, reached by name.
_CTL_MEM_SYSTEM_DATE = b"\x01\x58"
_CTL_MEM_SYSTEM_TIME = b"\x01\x59"
_CTL_MEM_UTC_OFFSET = b"\x01\x62"

# Antenna command (0x12)
_SUB_ANT1 = 0x00
_SUB_ANT2 = 0x01
_SUB_RX_ANT_ANT1 = 0x12
_SUB_RX_ANT_ANT2 = 0x13

# ATT / Preamp / DSP function sub-commands (0x11 / 0x16)
_CMD_ATT = 0x11
_CMD_PREAMP = 0x16
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
_SUB_TWIN_PEAK_FILTER = 0x4F
_SUB_DIAL_LOCK = 0x50
_SUB_FILTER_SHAPE = 0x56
_SUB_SSB_TX_BANDWIDTH = 0x58
_SUB_MAIN_SUB_TRACKING = 0x5E
_SUB_REPEATER_TONE = 0x42
_SUB_REPEATER_TSQL = 0x43

# Tone frequency sub-commands (0x1B)
_SUB_TONE_FREQ = 0x00
_SUB_TSQL_FREQ = 0x01

# Power control
_CMD_POWER_CTRL = 0x18

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
    0x21,  # RIT/XIT -- named _CMD_RIT until system.py's own reader
    # (MOR-2008 batch 1) was its last, same as 0x27/0x16/0x19 below
    0x27,
    0x16,
    _CMD_TONE,
    0x12,  # Antenna -- named _CMD_ANTENNA until antenna.py's own reader
    # (MOR-2008 batch 2) was its last, same as 0x27/0x16/0x19 below
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


def decode_wire_tuple(wire: tuple[int, ...]) -> tuple[int, int | None, bytes]:
    """Decode one ``[commands]`` wire tuple into ``(command, sub, prefix)``.

    The single decoder named in
    `docs/plans/2026-08-29-profile-driven-command-bytes.md` §2 ("Exactly one
    decoder of a `[commands]` wire tuple into `(command, sub, prefix)`") and
    used by Step 3 (§4) to build `commands/bound.py: BoundCommands.expect`
    from the same map entry `_build_from_map` builds the request from.

    The first element is the CI-V command, the second (if present) is the
    sub-command, and any remaining elements are the frame's further constant
    bytes -- per the tuple contract ruled in Q7 (§8.1): a tuple holds every
    constant byte of the frame, whether that is extended menu addressing
    (e.g. 0x1A 0x05 0x00 0x64 for IC-7300 ACC1 mod level), a selector byte,
    or a constant payload byte (e.g. 0x1C 0x00 0x01 for X6100 ptt_on).
    """
    command = wire[0]
    sub = wire[1] if len(wire) > 1 else None
    prefix = bytes(wire[2:])
    return command, sub, prefix


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

    Decodes the wire tuple via :func:`decode_wire_tuple`; any prefix bytes
    it returns are prepended to *data*, and only what the caller passes as
    *data* is appended after them.
    """
    wire = cmd_map.get(name)
    command, sub, prefix = decode_wire_tuple(wire)
    if prefix:
        data = prefix + data if data else prefix
    if command29:
        return build_cmd29_frame(
            to_addr, from_addr, command, sub=sub, data=data, receiver=receiver
        )
    return build_civ_frame(to_addr, from_addr, command, sub=sub, data=data)


def expose_command_key(
    key: Callable[[CommandMap], str],
) -> Callable[[_BuilderT], _BuilderT]:
    """Attach the command-map key a builder resolves through `_build_from_map`.

    Per the owner ruling on MOR-2003 (Step 3,
    `docs/plans/2026-08-29-profile-driven-command-bytes.md` §3.1/§4): every
    builder that exposes a key does so uniformly as
    ``Callable[[CommandMap], str]``, even where the key is a fixed literal
    the callable ignores its argument to return -- so `commands/speech.py:
    get_speech`'s per-map probe (``"set_speech" if cmd_map.has("set_speech")
    else "get_speech"``) fits the same shape rather than needing a split.

    `commands/bound.py: BoundCommands.expect` calls the attached callable
    with the bound map to learn which entry a builder's reply must be
    matched against, decoded by the same :func:`decode_wire_tuple` the
    request used. Attaches the callable as *fn*'s ``cmd_map_key`` attribute
    and returns *fn* unchanged -- it does not wrap the call, so the
    Step 1 fallback-audit wrapper (`commands/_fallback_audit.py`), which
    copies a wrapped function's ``__dict__`` via `functools.wraps`, carries
    the attribute through unaffected either way.
    """

    def decorator(fn: _BuilderT) -> _BuilderT:
        fn.cmd_map_key = key  # type: ignore[attr-defined]
        return fn

    return decorator


_CMD_MAP_EXPLANATION = (
    "as of MOR-2006 (docs/plans/2026-08-29-profile-driven-command-bytes.md), "
    "every rigplane.commands builder requires the radio's CommandMap and no "
    "longer has a hardcoded fallback. Call it through the radio's bound "
    "commands instead of the free function directly (commands/bound.py: "
    "BoundCommands, e.g. self._commands.<builder>(...))."
)


def require_cmd_map(fn: _BuilderT) -> _BuilderT:
    """Wrap *fn* so a missing or ``None`` ``cmd_map`` explains the Q6 API break.

    Per Q6 (`docs/plans/2026-08-29-profile-driven-command-bytes.md` §8.1):
    ``cmd_map`` is required, no default, no deprecation cycle. Two ways a
    call can still lack a real map, both raising ``TypeError`` with the
    same ``_CMD_MAP_EXPLANATION`` appended:

    - **Omitted entirely.** Python's own missing-argument ``TypeError``
      names only the parameter -- caught here and the explanation appended,
      so a call missing some *other* required argument too still shows it.
    - **Passed explicitly as ``None``.** Nothing in *fn*'s own signature
      rejects this at runtime (a type checker would) -- previously it
      reached *fn*'s body and failed there on its own terms, e.g. an
      ``AttributeError`` from ``None.get(name)`` inside `_build_from_map`
      for the de-delegated builders this migration writes. Checked before
      *fn* is ever called, so this can no longer produce a byte either.

    Applied module by module as Steps 5..N reach each one;
    `commands/config.py` (module 1) is the first caller.
    """

    @functools.wraps(fn)
    def wrapper(*args: Any, **kwargs: Any) -> Any:
        if "cmd_map" in kwargs and kwargs["cmd_map"] is None:
            raise TypeError(
                f"{fn.__name__}() cmd_map is None -- {_CMD_MAP_EXPLANATION}"
            )
        try:
            return fn(*args, **kwargs)
        except TypeError as exc:
            if "cmd_map" in kwargs or "'cmd_map'" not in str(exc):
                raise
            raise TypeError(f"{exc} -- {_CMD_MAP_EXPLANATION}") from None

    return wrapper  # type: ignore[return-value]


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
