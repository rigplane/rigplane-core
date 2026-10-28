"""Memory mode/write/clear/contents and band stacking register commands.

All nine builders in this module migrated onto the bound command map in
MOR-2008 (batch 4, `docs/plans/2026-08-29-profile-driven-command-bytes.md`
§4 Steps 5..N, "Group B"): each now requires ``cmd_map`` -- no hardcoded
fallback ever existed to delete here, unlike batches 1-3's modules, since
none of these nine ever took a ``cmd_map`` parameter at all before this
batch (`tests/command_map_parity_uncovered.txt`'s own
``hardcode_only_builders`` census, pre-migration: 16, this module's share
of it: 9).

Canonical command-map key naming (owner ruling, MOR-2008 batch 4): a
genuine get/set *value* pair keeps its ``get_``/``set_`` prefix
(``get_memory_mode``/``set_memory_mode``, ``get_memory_contents``/
``set_memory_contents``, ``get_bsr``/``set_bsr``); a single-wire-form
*action* command uses the bare name, the same convention already
established for ``power_on``/``power_off``/``ptt_on``/``ptt_off``/
``scan_start`` -- so ``build_memory_write``/``build_memory_to_vfo``/
``build_memory_clear`` resolve ``"memory_write"``/``"memory_to_vfo"``/
``"memory_clear"``, not a ``get_``/``set_`` spelling. The Python function
names keep their anomalous ``build_X_get``/``build_X_set``/``build_X``
shape (only ``get_bsr``/``set_bsr`` already followed the package-wide
``get_X``/``set_X`` convention) -- renaming a public function is out of
this migration's scope; only the TOML key spellings were reconciled.

``get_memory_mode`` is left deliberately undeclared (D1 state 3) on every
CI-V profile: the four Icom manuals phrase 0x08 as "Select the Memory
mode" (imperative), not "Send/read" like the package's other bidirectional
rows, and no live-bench probe has settled whether it answers a bare read
-- see each profile's own "PROVENANCE OPEN" note. Calling it now raises
`core.exceptions.CommandError` (undeclared-command refusal) on every
shipped Icom profile, the honest outcome until that probe happens.

``build_memory_to_vfo`` still appends the 2-byte BCD channel payload the
manuals show 0x0A accepting no data column at all -- MOR-2055, not touched
here. ``build_memory_clear`` builds the clear in the form the profile
declares (``[memory].write_layout.clear``): ``1A 00`` + address + ``FF``
for the radios whose profiles declare that form, the legacy ``0x0B`` +
channel for the IC-7610 whose wire bytes this package keeps unchanged
(MOR-2055).
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from ..core.types import (
    MemoryEntry,
    MemoryProfile,
    MemoryWriteField,
    MemoryWriteLayout,
    Mode,
    RepeaterShiftDirection,
    ToneSquelchType,
)
from ..types import bcd_decode, bcd_encode
from ._codec import _bcd_decode_value, bcd_encode_value
from ._frame import (
    CONTROLLER_ADDR,
    _CMD_CTL_MEM,
    _CMD_MEMORY_MODE,
    _SUB_BAND_STACK,
    _SUB_MEMORY_CONTENTS,
    _build_from_map,
    expose_command_key,
    require_cmd_map,
)
from .tone import _encode_tone_freq, _require_profile_tone

if TYPE_CHECKING:
    from ..command_map import CommandMap
    from ..types import BandStackRegister, CivFrame, MemoryChannel


@expose_command_key(lambda cmd_map: "get_memory_mode")
@require_cmd_map
def build_memory_mode_get(
    to_addr: int, from_addr: int = CONTROLLER_ADDR, *, cmd_map: CommandMap
) -> bytes:
    """Build CI-V frame to get current memory mode (0x08)."""
    return _build_from_map(
        cmd_map, "get_memory_mode", to_addr=to_addr, from_addr=from_addr
    )


@expose_command_key(lambda cmd_map: "set_memory_mode")
@require_cmd_map
def build_memory_mode_set(
    channel: int,
    to_addr: int,
    from_addr: int = CONTROLLER_ADDR,
    *,
    cmd_map: CommandMap,
    memory: MemoryProfile | None = None,
) -> bytes:
    """Build CI-V frame to set memory mode (0x08).

    With a profile's ``[memory]`` section the channel is bounded by it;
    otherwise by the IC-7610 range 1-101.
    """
    first, last = (
        (1, 101) if memory is None else (memory.first_channel, memory.last_channel)
    )
    if not first <= channel <= last:
        raise ValueError(f"Channel must be {first}-{last}, got {channel}")
    data = bcd_encode_value(channel, byte_count=2)
    return _build_from_map(
        cmd_map, "set_memory_mode", to_addr=to_addr, from_addr=from_addr, data=data
    )


@expose_command_key(lambda cmd_map: "set_memory_group")
@require_cmd_map
def build_memory_group_set(
    memory: MemoryProfile,
    group: int,
    to_addr: int,
    from_addr: int = CONTROLLER_ADDR,
    *,
    cmd_map: CommandMap,
) -> bytes:
    """Build CI-V frame to select a memory group (``08 A0 gggg``, IC-705)."""
    if not memory.first_group <= group <= memory.last_group:
        raise ValueError(
            f"memory group must be {memory.first_group}-{memory.last_group}, got {group}"
        )
    return _build_from_map(
        cmd_map,
        "set_memory_group",
        to_addr=to_addr,
        from_addr=from_addr,
        data=bcd_encode_value(group, byte_count=2),
    )


def parse_memory_mode_response(frame: CivFrame) -> int:
    """Parse memory mode response (0x08)."""
    if frame.command != _CMD_MEMORY_MODE:
        raise ValueError(f"Not a memory mode response: 0x{frame.command:02x}")
    if len(frame.data) < 2:
        raise ValueError(f"Expected 2 bytes for memory mode, got {len(frame.data)}")
    return _bcd_decode_value(frame.data[:2])


@expose_command_key(lambda cmd_map: "memory_write")
@require_cmd_map
def build_memory_write(
    to_addr: int, from_addr: int = CONTROLLER_ADDR, *, cmd_map: CommandMap
) -> bytes:
    """Build CI-V frame to write VFO to memory (0x09)."""
    return _build_from_map(
        cmd_map, "memory_write", to_addr=to_addr, from_addr=from_addr
    )


@expose_command_key(lambda cmd_map: "memory_to_vfo")
@require_cmd_map
def build_memory_to_vfo(
    channel: int,
    to_addr: int,
    from_addr: int = CONTROLLER_ADDR,
    *,
    cmd_map: CommandMap,
) -> bytes:
    """Build CI-V frame to load memory to VFO (0x0A).

    Note:
        The 2-byte BCD channel payload appended here is contradicted by
        all four Icom manuals, which document 0x0A as a bare command with
        no data field -- see MOR-2055 for the payload question. This
        migration re-plumbs the command-map lookup only and does not
        change these wire bytes.
    """
    if not 1 <= channel <= 101:
        raise ValueError(f"Channel must be 1-101, got {channel}")
    data = bcd_encode_value(channel, byte_count=2)
    return _build_from_map(
        cmd_map, "memory_to_vfo", to_addr=to_addr, from_addr=from_addr, data=data
    )


@expose_command_key(lambda cmd_map: "memory_clear")
@require_cmd_map
def build_memory_clear(
    memory: MemoryProfile,
    group: int | None,
    channel: int,
    to_addr: int,
    from_addr: int = CONTROLLER_ADDR,
    *,
    cmd_map: CommandMap,
) -> bytes:
    """Build the channel-clear frame in the profile's declared form.

    One command-map key (``memory_clear``) whose row carries each radio's
    chosen clear bytes; the payload shape comes from the profile's layout
    declaration (``[memory].write_layout.clear``, MOR-3644):

    - ``"1a00_ff"`` -- the IC-705 (guide p.17), IC-9700 (p.14), IC-7300
      (Advanced Manual 11a p.19-11) and IC-7300MK2 (p.17) form: the
      ``1A 00`` command, the radio's own memory address, then ``FF``.
    - ``"0b_channel"`` -- the legacy ``0x0B`` command with a 2-byte BCD
      channel payload, kept for the IC-7610 so its wire bytes do not
      change. Its own guide (Rev. 4 p.11) documents the clear as
      ``1A 00 + channel + FF``; that discrepancy is deferred to MOR-2055.

    The address is bounds-checked by the profile before any byte is
    built. A grouped memory requires an explicit ``group``: clearing an
    unnamed group could erase a slot in a group the caller never named.
    """
    layout = _require_write_layout(memory)
    group = _required_memory_group(group, memory, "clear")
    address = _encode_memory_address(memory, group, channel)
    if layout.clear == "0b_channel":
        data = bcd_encode_value(channel, byte_count=2)
    else:
        data = address + b"\xff"
    return _build_from_map(
        cmd_map,
        "memory_clear",
        to_addr=to_addr,
        from_addr=from_addr,
        data=data,
    )


@expose_command_key(lambda cmd_map: "get_memory_contents")
@require_cmd_map
def build_memory_contents_get(
    channel: int,
    to_addr: int,
    from_addr: int = CONTROLLER_ADDR,
    *,
    cmd_map: CommandMap,
) -> bytes:
    """Build CI-V frame to get memory contents (0x1A 0x00)."""
    if not 1 <= channel <= 101:
        raise ValueError(f"Channel must be 1-101, got {channel}")
    channel_bcd = bcd_encode_value(channel, byte_count=2)
    return _build_from_map(
        cmd_map,
        "get_memory_contents",
        to_addr=to_addr,
        from_addr=from_addr,
        data=channel_bcd,
    )


@expose_command_key(lambda cmd_map: "set_memory_contents")
@require_cmd_map
def build_memory_contents_set(
    mem: MemoryChannel,
    memory: MemoryProfile,
    to_addr: int,
    from_addr: int = CONTROLLER_ADDR,
    *,
    cmd_map: CommandMap,
    modes: tuple[str, ...],
    ctcss_tones_centihz: tuple[int, ...] | None = None,
    data_mode_count: int = 1,
) -> bytes:
    """Build the ``1A 00`` SET frame from the profile's declared layout.

    One table-driven encoder (MOR-3644): the record is built from the
    profile's ``[memory].write_layout`` field table, the address is the
    profile's own form (``_encode_memory_address``, the same helper the
    read side uses), and every field value is validated before the frame
    is built -- an out-of-range group/channel, tone, frequency or name
    refuses without emitting a byte. ``ctcss_tones_centihz`` is the
    profile's tone domain, the same one ``set_tone_freq`` admits through
    (``commands/tone.py``); ``data_mode_count`` is the profile's
    ``[data_mode].count`` (the IC-7610's record stores DATA1-3, the other
    manuals' records 0/1). A layout without the matching fields ignores
    both. ``modes`` is the active profile's mode list; DD additionally
    requires a declared DD group. In centi-Hz layouts, an active TONE/TSQL
    mode requires an explicit parameter; only an inactive parameter uses
    the declared default. Fixed DTCS fields cannot accept DTCS mode.
    ``layout.copies`` decides how many times the channel-data block
    appears on the wire (the sourced manuals print it twice -- the
    TX/split copy a non-split entry fills with the RX values).

    A grouped memory (IC-705 groups, IC-9700 band codes) requires an
    explicit ``group``: ``MemoryChannel.group`` left at ``None`` refuses
    rather than silently writing group 0.
    """
    from ..types import MemoryChannel as MC

    if not isinstance(mem, MC):
        raise TypeError(f"Expected MemoryChannel, got {type(mem)}")
    layout = _require_write_layout(memory)
    group = _required_memory_group(mem.group, memory, "write")
    address = _encode_memory_address(memory, group, mem.channel)
    if mem.mode != _MODE_DD:
        try:
            mode = Mode(mem.mode)
        except ValueError:
            raise ValueError(f"unknown CI-V mode code 0x{mem.mode:02x}") from None
        if mode.name.replace("_", "-") not in modes:
            raise ValueError(f"mode {mode.name} is not declared by the active profile")
    else:
        _dd_memory_mode(mem.mode, memory, group)
    _check_tone_representation(mem, layout, ctcss_tones_centihz)
    record = _write_record(mem, memory, layout, ctcss_tones_centihz, data_mode_count)
    return _build_from_map(
        cmd_map,
        "set_memory_contents",
        to_addr=to_addr,
        from_addr=from_addr,
        data=address + record,
    )


def _required_memory_group(group: int | None, memory: MemoryProfile, verb: str) -> int:
    """The explicit group a grouped memory needs; ``None`` is no choice.

    A channel-list radio (IC-7300/MK2, IC-7610) has only group 0. A
    grouped radio (IC-705 memory groups, IC-9700 band codes) refuses an
    unspecified group: silently defaulting to 0 could erase or overwrite a
    slot in a group the caller never named.
    """
    if group is None:
        if memory.addressing != "channel":
            raise ValueError(
                f"a memory group/band is required to {verb} a channel on this radio"
            )
        return 0
    return group


def _require_write_layout(memory: MemoryProfile) -> MemoryWriteLayout:
    """The structurally valid write layout this profile declares, or refuse.

    The runtime admission gate (:func:`rigplane.profiles.admit_memory_write`)
    is the policy check; this is the builder's own precondition, so a
    direct SDK call on a layout-less profile cannot build a record either.
    """
    layout = memory.write_layout
    if not isinstance(layout, MemoryWriteLayout) or layout.validation_errors():
        raise ValueError(
            "this profile declares no verified memory write layout "
            "([memory].write_layout)"
        )
    return layout


def _dd_memory_mode(mode_code: int, memory: MemoryProfile, group: int) -> bool:
    """Whether this mode byte is DD mode in one of the profile's DD groups.

    The shared read/write rule (MOR-3644): the DD byte is a mode only in
    the groups the profile declares (``dd_groups``); anywhere else it is
    not a mode this radio can store.
    """
    dd = mode_code == _MODE_DD and group in memory.dd_groups
    if mode_code == _MODE_DD and not dd:
        raise ValueError(f"DD mode is not valid for memory group {group}")
    return dd


def _check_duplex_nibble(duplex: int, dd: bool, memory: MemoryProfile) -> None:
    """The duplex nibble rules, shared by the read parser and the write
    encoder: 0-2 on a group/channel radio, 0-3 on a band/code radio
    (3 = RPS); DUP-/DUP+ never in DD mode, RPS only in it."""
    if duplex > (2 if memory.addressing == "group_channel" else 3) or (
        (duplex == 3) != dd and duplex != 0
    ):
        raise ValueError(f"duplex nibble {duplex} does not fit the mode")


def _check_frequency_digits(hi_byte: int, memory: MemoryProfile) -> None:
    """The GHz and 100 MHz digit rules, shared by the read parser and the
    write encoder so a record the write builds is always one the read
    side accepts back: the GHz digit is fixed 0 on a group/channel radio
    (MOR-3650) and at most 1 elsewhere; the 100 MHz digit is
    0-4 (both radios' guides)."""
    ghz, hundred_mhz = hi_byte >> 4, hi_byte & 0x0F
    if memory.addressing == "group_channel" and ghz:
        raise ValueError("frequency GHz digit is fixed 0")
    if ghz > 1 or hundred_mhz > 4:
        raise ValueError("frequency 100 MHz or GHz digit out of range")


def _encode_record_frequency(
    frequency_hz: int, memory: MemoryProfile, max_hz: int | None
) -> bytes:
    """The 5-byte LE BCD frequency, digit-checked and ceiling-checked."""
    if isinstance(frequency_hz, bool) or not isinstance(frequency_hz, int):
        raise ValueError(f"frequency must be an int in Hz, got {frequency_hz!r}")
    if frequency_hz <= 0:
        raise ValueError(f"frequency must be positive, got {frequency_hz}")
    if max_hz is not None and frequency_hz > max_hz:
        raise ValueError(f"frequency {frequency_hz} exceeds the layout limit {max_hz}")
    encoded = bcd_encode(frequency_hz)  # raises past 10 digits on its own
    _check_frequency_digits(encoded[4], memory)
    return encoded


def _encode_record_name(name: str, length: int, pad: bytes) -> bytes:
    """An ASCII name padded to the declared field width."""
    if len(name) > length:
        raise ValueError(f"memory name is {len(name)} characters, limit is {length}")
    try:
        encoded = name.encode("ascii")
    except UnicodeEncodeError:
        raise ValueError("memory name must be ASCII") from None
    if any(not 0x20 <= byte <= 0x7E for byte in encoded):
        raise ValueError("memory name must be printable ASCII")
    return encoded.ljust(length, pad)


def _check_tone_representation(
    mem: MemoryChannel,
    layout: MemoryWriteLayout,
    ctcss_tones_centihz: tuple[int, ...] | None,
) -> None:
    """Refuse a tone supplied in the representation the record does not store.

    The layout's field table names exactly one tone representation: the
    centi-Hz ``tone``/``tsql`` fields or the legacy Hz ``tone_hz_bcd``/
    ``tsql_hz_bcd`` ones. A value supplied in the other representation
    would be silently dropped from the record (an unset field writes the
    declared default, not the caller's tone), so it refuses here, before
    any byte is built.
    """
    encodings = {field.encoding for field in layout.fields}
    if mem.tonemode == 3 and any(
        field.field == "dtcs" and field.encoding == "fixed" for field in layout.fields
    ):
        raise ValueError(
            "DTCS mode requires a caller-supplied code; this field is fixed"
        )
    if encodings & {"tone", "tsql"} and mem.tonemode in (1, 2):
        tone = mem.tone_centihz if mem.tonemode == 1 else mem.tsql_centihz
        if tone is None:
            raise ValueError("active tone mode requires an explicit tone frequency")
        _require_profile_tone(tone, ctcss_tones_centihz)
    if encodings & {"tone", "tsql"} and (
        mem.tone_freq_hz is not None or mem.tsql_freq_hz is not None
    ):
        raise ValueError(
            "this record stores tones in exact centi-Hz: use "
            "tone_centihz/tsql_centihz, not tone_freq_hz/tsql_freq_hz"
        )
    if encodings & {"tone_hz_bcd", "tsql_hz_bcd"} and (
        mem.tone_centihz is not None or mem.tsql_centihz is not None
    ):
        raise ValueError(
            "this record stores tones in Hz: use "
            "tone_freq_hz/tsql_freq_hz, not tone_centihz/tsql_centihz"
        )


def _encode_record_tone(
    centihz: int | None,
    default_centihz: int | None,
    ctcss_tones_centihz: tuple[int, ...] | None,
) -> bytes:
    """A tone field: the layout's declared default when the caller leaves
    it unset -- a zero field is not a valid CTCSS value -- else the
    caller's value; both admitted through the profile's CTCSS domain."""
    if centihz is None:
        centihz = default_centihz
    return _encode_tone_freq(_require_profile_tone(centihz, ctcss_tones_centihz))


def _encode_write_field(
    field: MemoryWriteField,
    mem: MemoryChannel,
    memory: MemoryProfile,
    layout: MemoryWriteLayout,
    ctcss_tones_centihz: tuple[int, ...] | None,
    data_mode_count: int,
) -> bytes:
    """Encode one declared field; the value is validated first, so an
    out-of-range input refuses before the record holds a byte of it."""
    match field.encoding:
        case "select":
            # Split OFF | select OFF: the write API does not expose split
            # or select-memory assignment in this slice (MOR-3644).
            return b"\x00"
        case "scan":
            if mem.scan not in (0, 1):
                raise ValueError(f"scan flag must be 0 or 1, got {mem.scan}")
            return bytes([mem.scan])
        case "frequency":
            return _encode_record_frequency(mem.frequency_hz, memory, field.max_hz)
        case "mode":
            # The raw CI-V mode code of the read block; DD (0x22) only in
            # the profile's DD groups (the shared reader rule).
            if mem.mode == _MODE_DD:
                _dd_memory_mode(mem.mode, memory, mem.group)
            else:
                try:
                    Mode(mem.mode)
                except ValueError:
                    raise ValueError(
                        f"unknown CI-V mode code 0x{mem.mode:02x}"
                    ) from None
            return bytes([mem.mode])
        case "mode_bcd":
            return bcd_encode_value(mem.mode, byte_count=1)
        case "filter":
            if not 1 <= mem.filter <= 3:
                raise ValueError(f"filter must be 1-3, got {mem.filter}")
            return bytes([mem.filter])
        case "data_mode":
            if not 0 <= mem.datamode <= data_mode_count:
                raise ValueError(
                    f"data mode must be 0-{data_mode_count}, got {mem.datamode}"
                )
            return bytes([mem.datamode])
        case "data_tone":
            if not 0 <= mem.datamode <= data_mode_count:
                raise ValueError(
                    f"data mode must be 0-{data_mode_count}, got {mem.datamode}"
                )
            if not 0 <= mem.tonemode <= layout.tone_max:
                raise ValueError(
                    f"tone mode must be 0-{layout.tone_max}, got {mem.tonemode}"
                )
            return bytes([(mem.datamode << 4) | mem.tonemode])
        case "duplex_tone":
            # The reader's own duplex rule, shared: RPS (3) is the IC-9700
            # DD value, DUP-/DUP+ never in DD.
            _check_duplex_nibble(
                mem.duplex,
                _dd_memory_mode(mem.mode, memory, mem.group),
                memory,
            )
            if not 0 <= mem.tonemode <= layout.tone_max:
                raise ValueError(
                    f"tone mode must be 0-{layout.tone_max}, got {mem.tonemode}"
                )
            return bytes([(mem.duplex << 4) | mem.tonemode])
        case "tone":
            return _encode_record_tone(
                mem.tone_centihz, layout.default_tone_centihz, ctcss_tones_centihz
            )
        case "tsql":
            return _encode_record_tone(
                mem.tsql_centihz, layout.default_tone_centihz, ctcss_tones_centihz
            )
        case "tone_hz_bcd" | "tsql_hz_bcd":  # the legacy record's Hz field
            hz = (
                mem.tone_freq_hz
                if field.encoding == "tone_hz_bcd"
                else mem.tsql_freq_hz
            )
            return b"\x00" * 3 if hz is None else bcd_encode_value(hz, byte_count=3)
        case "name":
            return _encode_record_name(mem.name, field.length, b"\x20")
        case "name_nul":
            return _encode_record_name(mem.name, field.length, b"\x00")
        case "fixed":
            # A declared constant run (the record's own default/OFF
            # bytes); the loader checked the hex and the length.
            if field.value is None:
                raise ValueError(f"fixed field {field.field!r} declares no value")
            return bytes.fromhex(field.value)
    raise ValueError(f"unknown write field encoding {field.encoding!r}")


def _write_record(
    mem: MemoryChannel,
    memory: MemoryProfile,
    layout: MemoryWriteLayout,
    ctcss_tones_centihz: tuple[int, ...] | None,
    data_mode_count: int,
) -> bytes:
    """The write record after the memory address, one field at a time.

    ``layout.copies`` repeats the channel-data block (the fields between
    the leading select byte and the name field, per
    :meth:`MemoryWriteLayout.data_block_span`): the sourced Icom manuals
    print the block's position labels twice -- the TX/split copy, which a
    non-split entry fills with the RX values, as each manual's NOTE
    prescribes (IC-705 guide p.17, IC-9700 p.14, IC-7300 Advanced Manual
    11a p.19-11, IC-7300MK2 p.17). Fields at or past the block end (the
    name) shift after the repeats.
    """
    span = layout.data_block_span()
    block_len = (span[1] - span[0]) if span is not None else 0
    shift = (layout.copies - 1) * block_len
    record = bytearray(layout.record_length + shift)
    for field in layout.fields:
        encoded = _encode_write_field(
            field, mem, memory, layout, ctcss_tones_centihz, data_mode_count
        )
        offset = field.offset
        if span is not None and field.offset >= span[1]:
            offset += shift  # trailing fields land after the last copy
        record[offset : offset + field.length] = encoded
        if span is not None and span[0] <= field.offset < span[1]:
            for copy in range(1, layout.copies):
                start = field.offset + copy * block_len
                record[start : start + field.length] = encoded
    return bytes(record)


def parse_memory_contents_response(frame: CivFrame) -> MemoryChannel:
    """Parse memory contents response (0x1A 0x00)."""
    from ..types import MemoryChannel

    if frame.command != _CMD_CTL_MEM or frame.sub != _SUB_MEMORY_CONTENTS:
        raise ValueError(
            f"Not a memory contents response: 0x{frame.command:02x} sub=0x{frame.sub!r}"
        )
    if len(frame.data) < 28:
        raise ValueError(f"Memory contents too short: {len(frame.data)} bytes")

    data = frame.data
    return MemoryChannel(
        channel=_bcd_decode_value(data[0:2]),
        scan=data[2],
        frequency_hz=bcd_decode(data[3:8]),
        mode=_bcd_decode_value(data[8:9]),
        filter=_bcd_decode_value(data[9:10]),
        datamode=(data[10] >> 4) & 0x0F,
        tonemode=data[10] & 0x0F,
        tone_freq_hz=_bcd_decode_value(data[11:14])
        if data[11:14] != b"\x00\x00\x00"
        else None,
        tsql_freq_hz=_bcd_decode_value(data[14:17])
        if data[14:17] != b"\x00\x00\x00"
        else None,
        name=data[17:27].rstrip(b"\x00").decode("ascii", errors="replace"),
    )


# --- Band Stacking Register ---


@expose_command_key(lambda cmd_map: "get_bsr")
@require_cmd_map
def get_bsr(
    band: int,
    register: int,
    to_addr: int,
    from_addr: int = CONTROLLER_ADDR,
    *,
    cmd_map: CommandMap,
) -> bytes:
    """Build CI-V frame to get band stacking register (0x1A 0x01)."""
    if not 0 <= band <= 24:
        raise ValueError(f"Band must be 0-24, got {band}")
    if not 1 <= register <= 3:
        raise ValueError(f"Register must be 1-3, got {register}")
    data = bytes([band, register])
    return _build_from_map(
        cmd_map, "get_bsr", to_addr=to_addr, from_addr=from_addr, data=data
    )


@expose_command_key(lambda cmd_map: "set_bsr")
@require_cmd_map
def set_bsr(
    bsr: BandStackRegister,
    to_addr: int,
    from_addr: int = CONTROLLER_ADDR,
    *,
    cmd_map: CommandMap,
) -> bytes:
    """Build CI-V frame to set band stacking register (0x1A 0x01)."""
    from ..types import BandStackRegister as BSR

    if not isinstance(bsr, BSR):
        raise TypeError(f"Expected BandStackRegister, got {type(bsr)}")
    if not 0 <= bsr.band <= 24:
        raise ValueError(f"Band must be 0-24, got {bsr.band}")
    if not 1 <= bsr.register <= 3:
        raise ValueError(f"Register must be 1-3, got {bsr.register}")

    payload = bytes([bsr.band, bsr.register])
    payload += bcd_encode(bsr.frequency_hz)
    payload += bcd_encode_value(bsr.mode, byte_count=1)
    payload += bcd_encode_value(bsr.filter, byte_count=1)
    return _build_from_map(
        cmd_map, "set_bsr", to_addr=to_addr, from_addr=from_addr, data=payload
    )


def parse_band_stack_response(frame: CivFrame) -> BandStackRegister:
    """Parse band stacking register response (0x1A 0x01)."""
    from ..types import BandStackRegister

    if frame.command != _CMD_CTL_MEM or frame.sub != _SUB_BAND_STACK:
        raise ValueError(
            f"Not a band stack response: 0x{frame.command:02x} sub=0x{frame.sub!r}"
        )
    if len(frame.data) < 9:
        raise ValueError(f"Band stack data too short: {len(frame.data)} bytes")

    data = frame.data
    return BandStackRegister(
        band=data[0],
        register=data[1],
        frequency_hz=bcd_decode(data[2:7]),
        mode=_bcd_decode_value(data[7:8]),
        filter=_bcd_decode_value(data[8:9]),
    )


# Backward-compat aliases
build_band_stack_get = get_bsr
build_band_stack_set = set_bsr


# --- Read-only memory listing (MOR-3627) ----------------------------------
#
# CI-V ``1A 00`` (IC-705 guide p.18, IC-9700 p.14; real IC-705: MOR-3650). A
# reply is the address (group 2 + channel 2 bytes, or band 1 + channel 2), a
# select byte, the 47-byte channel block twice (the second is the split TX
# copy), then a 16-byte name. A block is: frequency 5, mode 1, filter 1, data
# mode 1, duplex|tone 1, digital squelch 1, TX tone 3, TSQL tone 3, DTCS 3, DV
# code 1, duplex offset 3, UR/R1/R2 24. An empty slot answers address + ``FF``.

_MEMORY_BLOCK_LEN = 47
_MODE_DD = 0x22  # IC-9700 digital data mode (1.2 GHz)
_MEMORY_NAME_LEN = 16
_ADDRESS_LEN = {"group_channel": 4, "band_channel": 3}
_DUPLEX_BY_NIBBLE = {
    0: RepeaterShiftDirection.SIMPLEX,
    1: RepeaterShiftDirection.MINUS,
    2: RepeaterShiftDirection.PLUS,
}
_TONE_MODE_BY_NIBBLE = {
    0: ToneSquelchType.OFF,
    1: ToneSquelchType.TONE,
    2: ToneSquelchType.TSQL,
    3: ToneSquelchType.DTCS,
}


def _bcd_int(raw: bytes) -> int:
    """Strict packed-BCD to int; a non-decimal nibble is a corrupt reply."""
    digits = raw.hex()
    if not digits.isdigit():
        raise ValueError(f"not packed BCD: {digits}")
    return int(digits)


def _encode_memory_address(memory: MemoryProfile, group: int, channel: int) -> bytes:
    """The ``1A 00`` address bytes for one slot, bounds-checked by the profile.

    ``group_channel`` (IC-705): 2-byte BCD group + 2-byte BCD channel.
    ``band_channel`` (IC-9700): 1-byte BCD band code + 2-byte BCD channel.
    ``channel`` (IC-7300/MK2, IC-7610): 2-byte BCD channel only -- the
    bounds check already pins the group to 0 on a channel list. A boolean
    address (``True`` is an ``int`` to ``range``) is refused: it would
    silently encode group/channel 1.
    """
    if isinstance(group, bool) or not isinstance(group, int):
        raise ValueError(f"memory group must be an integer, got {group!r}")
    if isinstance(channel, bool) or not isinstance(channel, int):
        raise ValueError(f"memory channel must be an integer, got {channel!r}")
    if not memory.first_group <= group <= memory.last_group:
        raise ValueError(
            f"memory group must be {memory.first_group}-{memory.last_group}, got {group}"
        )
    if not memory.first_channel <= channel <= memory.last_channel:
        raise ValueError(
            f"memory channel must be {memory.first_channel}-{memory.last_channel}, "
            f"got {channel}"
        )
    if memory.addressing == "channel":
        return bcd_encode_value(channel, byte_count=2)
    if memory.addressing not in _ADDRESS_LEN:
        raise ValueError(f"{memory.addressing} is not a CI-V memory layout")
    group_bytes = 2 if memory.addressing == "group_channel" else 1
    return bcd_encode_value(group, byte_count=group_bytes) + bcd_encode_value(
        channel, byte_count=2
    )


@expose_command_key(lambda cmd_map: "get_memory_contents")
@require_cmd_map
def build_memory_entry_read(
    memory: MemoryProfile,
    group: int,
    channel: int,
    to_addr: int,
    from_addr: int = CONTROLLER_ADDR,
    *,
    cmd_map: CommandMap,
) -> bytes:
    """Build the read-only CI-V frame for one stored memory slot (``1A 00``)."""
    return _build_from_map(
        cmd_map,
        "get_memory_contents",
        to_addr=to_addr,
        from_addr=from_addr,
        data=_encode_memory_address(memory, group, channel),
    )


def _parse_block(block: bytes, memory: MemoryProfile, group: int) -> dict[str, Any]:
    """Validate one 47-byte channel block and return its decoded fields.

    Fields the read model does not expose (filter, DTCS polarity, DV code,
    call signs) are not validated: a listing must not drop a real channel over
    a value it never shows.
    """
    mode_code = block[5]  # the guide's mode codes are raw bytes (DV = 0x17)
    if block[7] not in (0, 1):
        raise ValueError(f"data mode byte must be 00 or 01, got {block[7]:02X}")
    if block[9] not in (0x00, 0x10, 0x20):  # digital squelch: off, DSQL, CSQL
        raise ValueError(f"digital squelch byte must be X0, got {block[9]:02X}")
    duplex_nibble, tone_nibble = block[8] >> 4, block[8] & 0x0F
    tone_mode = _TONE_MODE_BY_NIBBLE.get(tone_nibble)
    if tone_mode is None:
        raise ValueError(f"unknown tone mode nibble {tone_nibble}")
    dd = _dd_memory_mode(mode_code, memory, group)
    # RPS (3) exists only in DD; DD has no DUP-/DUP+ (the shared rule the
    # write encoder applies to the value before a byte is built).
    _check_duplex_nibble(duplex_nibble, dd, memory)
    # The shared GHz/100 MHz digit rules (MOR-3644): the write encoder
    # checks the same byte, so a record it builds always reads back.
    _check_frequency_digits(block[4], memory)
    if block[22] >> 4 and group not in memory.offset_10mhz_groups:
        raise ValueError(f"offset 10 MHz digit is not valid for memory group {group}")
    # tone and TSQL frequencies: a fixed 00 byte, then a 100 Hz digit of 0-2
    if block[10] or block[13] or block[11] >> 4 > 2 or block[14] >> 4 > 2:
        raise ValueError("tone frequency digits out of range")
    dtcs_digits = f"{block[17]:02x}{block[18]:02x}"
    if not dtcs_digits.isdigit() or dtcs_digits[0] != "0" or max(dtcs_digits) > "7":
        raise ValueError(f"DTCS code is not three octal digits: {dtcs_digits}")
    try:
        mode = Mode(mode_code).name.replace("_R", "-R")
    except ValueError:
        mode = "DD" if dd else f"0x{mode_code:02X}"
    return {
        "frequency_hz": _bcd_int(block[4::-1]),
        "mode": mode,
        "data_mode": block[7] == 1,
        "duplex": _DUPLEX_BY_NIBBLE.get(duplex_nibble),
        # [10 MHz|1 MHz][100 kHz|10 kHz][1 kHz|100 Hz], most significant first
        "offset_hz": _bcd_int(block[22:19:-1]) * 100,
        "tone_mode": tone_mode,
        "tone_centihz": _bcd_int(block[10:13]) * 10,
        "tsql_centihz": _bcd_int(block[13:16]) * 10,
        "dtcs_code": int(dtcs_digits),
    }


def parse_memory_entry(data: bytes, memory: MemoryProfile) -> MemoryEntry:
    """Decode a ``1A 00`` reply body (the bytes after ``1A 00``) into a slot."""
    if memory.addressing not in _ADDRESS_LEN:
        raise ValueError(f"{memory.addressing} is not a CI-V memory layout")
    address_len = _ADDRESS_LEN[memory.addressing]
    group_len = address_len - 2
    if len(data) < address_len + 1:
        raise ValueError(f"memory reply too short: {len(data)} bytes")
    group = _bcd_int(data[:group_len])
    channel = _bcd_int(data[group_len:address_len])
    if not (
        memory.first_group <= group <= memory.last_group
        and memory.first_channel <= channel <= memory.last_channel
    ):
        raise ValueError(
            f"memory reply address {group}/{channel} is outside the profile"
        )
    if len(data) == address_len + 1 and data[address_len] == 0xFF:
        return MemoryEntry(group=group, channel=channel, empty=True)
    select = data[address_len]  # split (high nibble 0/1) | select memory (0-3)
    max_split = 1 if memory.addressing == "group_channel" else 0  # IC-9700: fixed 0
    if select >> 4 > max_split or select & 0x0F > 3:
        raise ValueError(f"unknown select/split byte {select:02X}")
    start = address_len + 1
    expected = start + 2 * _MEMORY_BLOCK_LEN + _MEMORY_NAME_LEN
    if len(data) != expected:
        raise ValueError(f"memory reply is {len(data)} bytes, expected {expected}")
    fields = _parse_block(data[start : start + _MEMORY_BLOCK_LEN], memory, group)
    tx_block = data[start + _MEMORY_BLOCK_LEN : expected - _MEMORY_NAME_LEN]
    _parse_block(tx_block, memory, group)
    name_raw = data[expected - _MEMORY_NAME_LEN : expected]
    if not all(0x20 <= b <= 0x7E or b == 0 for b in name_raw):
        raise ValueError("memory name is not printable ASCII")
    name = name_raw.decode("ascii").replace("\x00", " ").rstrip()
    return MemoryEntry(group=group, channel=channel, name=name, **fields)
