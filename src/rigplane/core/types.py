"""Enums, dataclasses, and helper functions for the Icom LAN protocol."""

from dataclasses import dataclass, field
from enum import IntEnum, StrEnum
from typing import Literal

from rigplane.core.env_config import get_audio_sample_rate

__all__ = [
    "PacketType",
    "Mode",
    "AgcMode",
    "AudioPeakFilter",
    "BreakInMode",
    "FilterShape",
    "SsbTxBandwidth",
    "RepeaterShiftDirection",
    "ToneSquelchType",
    "ctcss_booleans_for_tone_squelch_type",
    "AudioCodec",
    "AudioCapabilities",
    "get_audio_capabilities",
    "ScopeCompletionPolicy",
    "ScopeFixedEdge",
    "PacketHeader",
    "CivFrame",
    "HEADER_SIZE",
    "AUDIO_HEADER_SIZE",
    "bcd_encode",
    "bcd_decode",
    "MemoryChannel",
    "MemoryEntry",
    "MemoryProfile",
    "MemoryWriteField",
    "MemoryWriteLayout",
    "MEMORY_WRITE_ENCODINGS",
    "MEMORY_WRITE_CLEAR_FORMS",
    "BandStackRegister",
]

# Fixed header size: 4 (len) + 2 (type) + 2 (seq) + 4 (sentid) + 4 (rcvdid) = 16 bytes
HEADER_SIZE = 0x10

# Audio packet header: standard 16-byte header + 8 bytes audio sub-header
AUDIO_HEADER_SIZE = 0x18


class PacketType(IntEnum):
    """Packet type codes from the Icom LAN UDP protocol.

    The type field is at offset 0x04 in every packet header (2 bytes LE).
    Values derived from wfview reference implementation.
    """

    DATA = 0x00
    CONTROL = 0x01
    ARE_YOU_THERE = 0x03
    I_AM_HERE = 0x04
    DISCONNECT = 0x05
    ARE_YOU_READY = 0x06
    PING = 0x07


class AudioCodec(IntEnum):
    """Audio codec identifiers used in conninfo packets.

    Values match the codec byte at offsets 0x72 (rxcodec) and 0x73 (txcodec)
    in the conninfo packet. Opus codecs (0x40/0x41) are only available
    when the radio reports connection_type == "WFVIEW".

    Reference: wfview audioconverter.h lines 123-133.
    """

    ULAW_1CH = 0x01
    PCM_1CH_8BIT = 0x02
    PCM_1CH_16BIT = 0x04
    PCM_2CH_8BIT = 0x08
    PCM_2CH_16BIT = 0x10
    ULAW_2CH = 0x20
    OPUS_1CH = 0x40
    OPUS_2CH = 0x41


class Mode(IntEnum):
    """Icom CI-V operating modes.

    Values match the CI-V mode byte sent/received in mode commands.
    """

    LSB = 0x00
    USB = 0x01
    AM = 0x02
    CW = 0x03
    RTTY = 0x04
    FM = 0x05
    WFM = 0x06
    CW_R = 0x07
    RTTY_R = 0x08
    PSK = 0x12
    PSK_R = 0x13
    DV = 0x17


class AgcMode(IntEnum):
    """IC-7610 AGC mode selection."""

    FAST = 0x01
    MID = 0x02
    SLOW = 0x03


class AudioPeakFilter(IntEnum):
    """IC-7610 APF mode selection."""

    OFF = 0x00
    WIDE = 0x01
    MID = 0x02
    NAR = 0x03


class BreakInMode(IntEnum):
    """IC-7610 BK-IN mode selection."""

    OFF = 0x00
    SEMI = 0x01
    FULL = 0x02


class FilterShape(IntEnum):
    """IC-7610 DSP IF filter shape."""

    SHARP = 0x00
    SOFT = 0x01


class SsbTxBandwidth(IntEnum):
    """IC-7610 SSB transmit bandwidth preset."""

    WIDE = 0x00
    MID = 0x01
    NAR = 0x02


class RepeaterShiftDirection(IntEnum):
    """Repeater shift direction (FTX-1 CAT ``OS`` P2 codes, MOR-2111).

    Values match the FTX-1's wire codes exactly. ``ARS`` (Automatic Repeater
    Shift) is not a universal value — it is this radio's, not every radio's;
    see :class:`rigplane.core.radio_protocol.RepeaterShiftCapable` for the
    protocol-level caveat about which radios can represent which values.
    """

    SIMPLEX = 0x00
    PLUS = 0x01
    MINUS = 0x02
    ARS = 0x03


class ToneSquelchType(StrEnum):
    """Neutral tone squelch types a profile's ``[tone_squelch_types]`` table
    maps its selector codes to (MOR-2131). ``_T`` / ``_R`` name what the
    radio uses on transmit and receive when the two differ.
    """

    OFF = "off"
    TONE = "tone"
    TSQL = "tsql"
    DTCS = "dtcs"
    DTCS_T = "dtcs_t"
    TONE_T_DTCS_R = "tone_t_dtcs_r"
    DTCS_T_TSQL_R = "dtcs_t_tsql_r"
    TONE_T_TSQL_R = "tone_t_tsql_r"
    PR_FREQ = "pr_freq"
    REV_TONE = "rev_tone"


_CTCSS_BOOLEANS_BY_TONE_SQUELCH_TYPE: dict[ToneSquelchType, tuple[bool, bool]] = {
    ToneSquelchType.OFF: (False, False),
    ToneSquelchType.TONE: (True, False),
    ToneSquelchType.TSQL: (True, True),
}


def ctcss_booleans_for_tone_squelch_type(
    tone_squelch_type: ToneSquelchType | None,
) -> tuple[bool | None, bool | None]:
    """Return the ``(repeater_tone, repeater_tsql)`` pair a type implies.

    Only OFF, TONE and TSQL are expressible as the two CTCSS booleans; any
    other type, or an unknown one, reads ``(None, None)``.
    """
    if tone_squelch_type is None:
        return (None, None)
    return _CTCSS_BOOLEANS_BY_TONE_SQUELCH_TYPE.get(tone_squelch_type, (None, None))


class ScopeCompletionPolicy(StrEnum):
    """Policies for awaiting completion of scope commands."""

    STRICT = "strict"  # Wait for a CI-V ACK response
    FAST = "fast"  # Fire-and-forget, do not wait for ACK
    VERIFY = "verify"  # Fire-and-forget, but await actual scope data activity


@dataclass(frozen=True, slots=True)
class ScopeFixedEdge:
    """Fixed-edge scope bounds for IC-7610 scope configuration."""

    range_index: int
    edge: int
    start_hz: int
    end_hz: int


@dataclass(frozen=True, slots=True)
class AudioCapabilities:
    """Static rigplane audio capability matrix and defaults."""

    supported_codecs: tuple[AudioCodec, ...]
    supported_sample_rates_hz: tuple[int, ...]
    supported_channels: tuple[int, ...]
    default_codec: AudioCodec
    default_sample_rate_hz: int
    default_channels: int

    def to_dict(self) -> dict[str, object]:
        """Return a JSON-friendly representation with stable key ordering."""
        return {
            "supported_codecs": [
                {"name": codec.name, "value": int(codec)}
                for codec in self.supported_codecs
            ],
            "supported_sample_rates_hz": list(self.supported_sample_rates_hz),
            "supported_channels": list(self.supported_channels),
            "default_codec": {
                "name": self.default_codec.name,
                "value": int(self.default_codec),
            },
            "default_sample_rate_hz": self.default_sample_rate_hz,
            "default_channels": self.default_channels,
        }


_SUPPORTED_AUDIO_CODECS: tuple[AudioCodec, ...] = tuple(AudioCodec)
_SUPPORTED_AUDIO_SAMPLE_RATES_HZ: tuple[int, ...] = (8000, 16000, 24000, 48000)
_AUDIO_CODEC_CHANNELS: dict[AudioCodec, int] = {
    AudioCodec.ULAW_1CH: 1,
    AudioCodec.PCM_1CH_8BIT: 1,
    AudioCodec.PCM_1CH_16BIT: 1,
    AudioCodec.PCM_2CH_8BIT: 2,
    AudioCodec.PCM_2CH_16BIT: 2,
    AudioCodec.ULAW_2CH: 2,
    AudioCodec.OPUS_1CH: 1,
    AudioCodec.OPUS_2CH: 2,
}
_DEFAULT_CODEC_PREFERENCE: tuple[AudioCodec, ...] = (
    # Preferred: stereo PCM16 so dual-RX radios deliver L=MAIN + R=SUB in the
    # LAN stream (epic #787).  Single-RX radios' firmware downgrades to mono
    # during handshake; the broadcaster's ``_refresh_codec_state`` reads the
    # actual negotiated codec back.
    #
    # NOTE: the stereo value (0x10) is safe in the conninfo ``rxcodec`` field
    # *only* because ``_control_phase._send_conninfo`` explicitly forces the
    # ``txcodec`` field to a mono value — IC-7610 stock firmware rejects the
    # session with ``error=0xFFFFFFFF`` if ``txcodec`` itself is stereo (its
    # mic path is mono-only, same constraint wfview enforces via its UI at
    # ``settingswidget.cpp:118-124``).  Issue #794.
    AudioCodec.PCM_2CH_16BIT,
    AudioCodec.PCM_1CH_16BIT,
    AudioCodec.ULAW_2CH,
    AudioCodec.ULAW_1CH,
    AudioCodec.PCM_2CH_8BIT,
    AudioCodec.PCM_1CH_8BIT,
    AudioCodec.OPUS_2CH,
    AudioCodec.OPUS_1CH,
)


def _build_audio_capabilities() -> AudioCapabilities:
    supported_channels = tuple(
        sorted({_AUDIO_CODEC_CHANNELS[codec] for codec in _SUPPORTED_AUDIO_CODECS})
    )
    default_codec = next(
        codec for codec in _DEFAULT_CODEC_PREFERENCE if codec in _SUPPORTED_AUDIO_CODECS
    )
    implied_default_channels = _AUDIO_CODEC_CHANNELS[default_codec]
    default_channels = (
        implied_default_channels
        if implied_default_channels in supported_channels
        else supported_channels[0]
    )
    default_sample_rate_hz = get_audio_sample_rate()
    return AudioCapabilities(
        supported_codecs=_SUPPORTED_AUDIO_CODECS,
        supported_sample_rates_hz=_SUPPORTED_AUDIO_SAMPLE_RATES_HZ,
        supported_channels=supported_channels,
        default_codec=default_codec,
        default_sample_rate_hz=default_sample_rate_hz,
        default_channels=default_channels,
    )


_AUDIO_CAPABILITIES = _build_audio_capabilities()


def get_audio_capabilities() -> AudioCapabilities:
    """Return rigplane audio capabilities with deterministic defaults.

    Default selection rules:
    1. Codec: first supported codec from ``_DEFAULT_CODEC_PREFERENCE``.
    2. Sample rate: highest supported sample rate.
    3. Channels: channel-count implied by default codec (fallback to minimum).
    """
    return _AUDIO_CAPABILITIES


@dataclass(frozen=True, slots=True)
class PacketHeader:
    """Fixed 16-byte header present in every Icom LAN UDP packet.

    Attributes:
        length: Total packet length in bytes (including this header).
        type: Packet type code.
        seq: Sequence number.
        sender_id: Sender's connection ID (assigned during handshake).
        receiver_id: Receiver's connection ID.
    """

    length: int
    type: int
    seq: int
    sender_id: int
    receiver_id: int


@dataclass(frozen=True, slots=True)
class CivFrame:
    """Parsed CI-V frame.

    Attributes:
        to_addr: Destination CI-V address.
        from_addr: Source CI-V address.
        command: CI-V command byte.
        sub: Optional sub-command byte.
        data: Payload data (excluding command and sub bytes).
        receiver: Optional receiver index for Command29-wrapped frames.
    """

    to_addr: int
    from_addr: int
    command: int
    sub: int | None = None
    data: bytes = b""
    receiver: int | None = None


def bcd_encode(freq_hz: int) -> bytes:
    """Encode a frequency in Hz to Icom BCD format (5 bytes).

    BCD encoding stores pairs of decimal digits in each byte,
    least-significant digits first (little-endian BCD).

    Args:
        freq_hz: Frequency in Hz (e.g. 14074000).

    Returns:
        5 bytes of BCD-encoded frequency.

    Raises:
        ValueError: If frequency is negative or exceeds 10 digits.

    Examples:
        >>> bcd_encode(14074000).hex()
        '0040071400'
    """
    if freq_hz < 0:
        raise ValueError(f"Frequency must be non-negative, got {freq_hz}")

    digits = f"{freq_hz:010d}"
    if len(digits) > 10:
        raise ValueError(f"Frequency {freq_hz} exceeds 10 digits")

    # BCD little-endian: byte[i] stores digit pair for positions 2i and 2i+1.
    # Low nibble = digit at position 2i (even power of 10).
    # High nibble = digit at position 2i+1 (odd power of 10).
    # Digits string is big-endian: digits[0]=most significant, digits[9]=units.
    result = bytearray(5)
    for i in range(5):
        low = int(digits[9 - 2 * i])  # position 2i
        high = int(digits[9 - 2 * i - 1])  # position 2i+1
        result[i] = (high << 4) | low
    return bytes(result)


def bcd_decode(data: bytes) -> int:
    """Decode Icom BCD-encoded frequency bytes to Hz.

    Args:
        data: 5 bytes of BCD-encoded frequency.

    Returns:
        Frequency in Hz.

    Raises:
        ValueError: If data is not exactly 5 bytes or contains invalid BCD.

    Examples:
        >>> bcd_decode(bytes.fromhex('0040071400'))
        14074000
    """
    if len(data) != 5:
        raise ValueError(f"BCD data must be exactly 5 bytes, got {len(data)}")

    freq = 0
    for i in range(len(data)):
        high = (data[i] >> 4) & 0x0F
        low = data[i] & 0x0F
        if high > 9 or low > 9:
            raise ValueError(f"Invalid BCD digit in byte {i}: 0x{data[i]:02x}")
        # low nibble = digit at position 2i, high nibble = position 2i+1
        freq += low * (10 ** (2 * i)) + high * (10 ** (2 * i + 1))
    return freq


@dataclass
class MemoryChannel:
    """One memory channel as the write API programmes it.

    The legacy 26-byte IC-7610 record read ``scan``, ``mode`` (BCD of the
    int), ``filter``, ``tone_freq_hz``/``tsql_freq_hz`` (Hz, BCD) and a
    NUL-padded 10-char name; the manual-verified layouts (MOR-3644) use
    the raw CI-V mode code, centi-Hz-exact tones against the profile's
    CTCSS domain (the ``1B 00`` encoding), a duplex nibble and the
    profile's own address form (``group``: memory group or band code).

    Attributes:
        channel: Memory channel number, bounded by the profile.
        frequency_hz: Frequency in Hz.
        mode: CI-V mode code (raw byte on the verified layouts, BCD of
            the int on the legacy IC-7610 record).
        filter: Filter number (1-3).
        scan: Scan flag (0/1, legacy record only).
        datamode: Data mode flag (0/1, high nibble where combined).
        tonemode: Tone mode nibble (0=OFF, 1=TONE, 2=TSQL, 3=DTCS).
        tone_freq_hz: CTCSS tone in Hz (legacy record only).
        tsql_freq_hz: TSQL frequency in Hz (legacy record only).
        name: Memory channel name, bounded by the layout's name field.
        group: Memory group (IC-705) or band code (IC-9700); ``None``
            means the caller did not choose one, which a grouped profile
            refuses (never silently group 0); the channel-only radios
            treat it as 0.
        duplex: Duplex nibble (0=OFF, 1=DUP-, 2=DUP+, 3=RPS in DD).
        tone_centihz: CTCSS tone in exact centi-Hz (verified layouts).
        tsql_centihz: TSQL frequency in exact centi-Hz (verified layouts).
    """

    channel: int
    frequency_hz: int
    mode: int
    filter: int
    scan: int
    datamode: int
    tonemode: int
    tone_freq_hz: int | None = None
    tsql_freq_hz: int | None = None
    name: str = ""
    group: int | None = None
    duplex: int = 0
    tone_centihz: int | None = None
    tsql_centihz: int | None = None


MemoryAddressing = Literal["group_channel", "band_channel", "channel"]

# The write-record field encodings the package can build (MOR-3644). One
# shared vocabulary for the TOML loader, the admission gate and the
# table-driven encoder in ``commands/memory.py``; the per-radio facts
# (offsets, widths, name length) live in each profile's
# ``[memory.write_layout]`` table, not here.
MEMORY_WRITE_ENCODINGS: frozenset[str] = frozenset(
    {
        "select",  # the split/select byte, written 00 (both OFF)
        "scan",  # the scan flag byte (legacy record)
        "frequency",  # 5-byte LE BCD, optional max_hz limit
        "mode",  # the raw CI-V mode code byte
        "mode_bcd",  # BCD of the mode int (legacy record)
        "filter",  # the filter byte, 1-3
        "data_mode",  # the standalone data-mode byte, 00/01
        "data_tone",  # datamode<<4 | tonemode in one byte
        "duplex_tone",  # duplex<<4 | tonemode in one byte
        "tone",  # 3-byte CTCSS BCD (the 1B 00 encoding), the layout default when unset
        "tsql",  # the tone-squelch twin of "tone"
        "tone_hz_bcd",  # BCD of tone_freq_hz (legacy record)
        "tsql_hz_bcd",  # BCD of tsql_freq_hz (legacy record)
        "name",  # ASCII, space padded
        "name_nul",  # ASCII, NUL padded (legacy record)
        "fixed",  # a fixed byte run, given as hex in the field's ``value``
    }
)

# The channel-clear payload shapes (MOR-3644). The command bytes live in
# each profile's ``memory_clear`` command-map row -- the same entry the
# clear builder resolves, so the exposed metadata always matches the
# emitted frame. ``"1a00_ff"`` is the IC-705/IC-9700/IC-7300/IC-7300MK2
# manuals' clear: the ``1A 00`` command plus the radio's own address form
# plus ``FF``. ``"0b_channel"`` is the legacy ``0x0B`` payload (a 2-byte
# BCD channel) the IC-7610 profile keeps so its wire bytes do not change
# in this PR (MOR-2055).
MEMORY_WRITE_CLEAR_FORMS: frozenset[str] = frozenset({"1a00_ff", "0b_channel"})

# Per-encoding byte widths the loader enforces on every declaration.
_WRITE_ENCODING_WIDTHS: dict[str, int | None] = {
    "frequency": 5,
    "mode": 1,
    "mode_bcd": 1,
    "filter": 1,
    "data_mode": 1,
    "data_tone": 1,
    "duplex_tone": 1,
    "tone": 3,
    "tsql": 3,
    "tone_hz_bcd": 3,
    "tsql_hz_bcd": 3,
    "select": 1,
    "scan": 1,
}


@dataclass(frozen=True, slots=True)
class MemoryWriteField:
    """One field of a profile-declared write record (MOR-3644).

    ``offset`` is relative to the record that follows the memory address;
    ``encoding`` names one shared encoder from
    :data:`MEMORY_WRITE_ENCODINGS`; ``max_hz`` is the optional frequency
    ceiling the declaration enforces (the GHz/100 MHz digit rules the
    read parser applies are shared code and always checked). ``value`` is
    the hex byte string a ``fixed`` field writes (its declared ``length``
    in bytes); it is invalid on any other encoding.
    """

    field: str
    offset: int
    length: int
    encoding: str
    max_hz: int | None = None
    value: str | None = None


@dataclass(frozen=True, slots=True)
class MemoryWriteLayout:
    """A radio's verified ``1A 00`` write record, as profile data.

    The record is the bytes after the memory address (whose form comes
    from :class:`MemoryProfile` addressing): ``fields`` must tile
    ``[0, record_length)`` exactly, so a gap, an overlap or an undeclared
    tail byte is a load-time error, never a silently wrong byte.
    ``clear`` names the channel-clear wire form from
    :data:`MEMORY_WRITE_CLEAR_FORMS`; ``tone_max`` bounds the tone-mode
    nibble (0=OFF, 1=TONE, 2=TSQL, 3=DTCS) for radios whose record has
    no DTCS field. ``copies`` is the number of times the channel-data
    block -- the fields between a leading ``select`` byte and the name
    field -- appears on the wire: the sourced Icom manuals print the
    block's position labels twice (the TX/split copy, which a non-split
    entry fills with the RX values; IC-705 guide p.17, IC-9700 guide
    p.14, IC-7300 Advanced Manual 11a p.19-11 and IC-7300MK2 guide p.17,
    each with the same NOTE), so those layouts declare ``copies = 2``
    and the name lands after the
    last copy. The declared ``fields`` table always tiles ONE copy
    (``record_length`` is that one-copy length); the wire record is
    :meth:`wire_record_length` bytes. ``default_tone_centihz`` is the
    tone a ``tone``/``tsql`` field carries when the caller leaves it
    unset -- a zero field is not a valid CTCSS value -- and is required
    exactly when the table declares such a field; the loader checks it is
    a member of the profile's CTCSS domain.
    """

    clear: str
    record_length: int
    fields: tuple[MemoryWriteField, ...]
    tone_max: int = 3
    copies: int = 1
    default_tone_centihz: int | None = None

    def data_block_span(self) -> tuple[int, int] | None:
        """``[start, end)`` of the channel-data block ``copies`` repeats.

        The block starts after a leading ``select`` field (offset 0) and
        ends at the name field. ``None`` when the table has no name field
        or leaves no room -- structural validation reports the tables
        where that is an error; the encoder only runs on valid layouts.
        """
        name = next(
            (f for f in self.fields if f.encoding in ("name", "name_nul")), None
        )
        if name is None:
            return None
        start = 0
        for entry in self.fields:
            if entry.encoding == "select" and entry.offset == 0:
                start = entry.offset + entry.length
        if name.offset <= start:
            return None
        return (start, name.offset)

    def wire_record_length(self) -> int:
        """The record length on the wire: the declared one-copy tiling
        plus the extra block repeats ``copies`` asks for."""
        span = self.data_block_span()
        if span is None or not isinstance(self.copies, int) or self.copies <= 1:
            return self.record_length
        return self.record_length + (self.copies - 1) * (span[1] - span[0])

    def domain_errors(
        self, ctcss_tones_centihz: tuple[int, ...] | None
    ) -> tuple[str, ...]:
        """Errors against the profile's CTCSS domain (the loader and the
        admission gate both apply them, after :meth:`validation_errors`)."""
        tone = self.default_tone_centihz
        if tone is None:
            return ()
        if ctcss_tones_centihz is None or tone not in ctcss_tones_centihz:
            return (
                f"default_tone_centihz {tone!r} is not a member of the "
                "profile's CTCSS domain ([ctcss])",
            )
        return ()

    def validation_errors(self) -> tuple[str, ...]:
        """Structural errors that make this declaration unbuildable.

        Empty means valid. Called by the TOML loader (which raises with
        the file name) and by the runtime admission gate (which refuses
        SDK-supplied profiles), so both sides apply the same rules. Member
        types are checked first: an SDK caller can splice any junk into a
        frozen dataclass with ``dataclasses.replace``, and that must
        surface as a refusal here, never as a ``TypeError`` at the gate.
        """
        errors: list[str] = []
        if not isinstance(self.clear, str) or self.clear not in (
            MEMORY_WRITE_CLEAR_FORMS
        ):
            errors.append(f"unknown clear form {self.clear!r}")
        if isinstance(self.record_length, bool) or not isinstance(
            self.record_length, int
        ):
            errors.append(f"record_length must be an int, got {self.record_length!r}")
        elif not 1 <= self.record_length <= 128:
            errors.append(f"record_length must be 1..128, got {self.record_length}")
        if isinstance(self.tone_max, bool) or not isinstance(self.tone_max, int):
            errors.append(f"tone_max must be an int, got {self.tone_max!r}")
        elif not 0 <= self.tone_max <= 3:
            errors.append(f"tone_max must be 0..3, got {self.tone_max}")
        if isinstance(self.copies, bool) or not isinstance(self.copies, int):
            errors.append(f"copies must be an int, got {self.copies!r}")
        elif not 1 <= self.copies <= 2:
            # No sourced manual shows more than the block twice (the
            # TX/split copy); a third copy would need its manual first.
            errors.append(f"copies must be 1..2, got {self.copies}")
        if self.default_tone_centihz is not None and (
            isinstance(self.default_tone_centihz, bool)
            or not isinstance(self.default_tone_centihz, int)
        ):
            errors.append(
                "default_tone_centihz must be an int, "
                f"got {self.default_tone_centihz!r}"
            )
        if not isinstance(self.fields, tuple) or not all(
            isinstance(entry, MemoryWriteField) for entry in self.fields
        ):
            return ("the field table is not a tuple of memory write fields", *errors)
        if not self.fields:
            return ("the field table is empty", *errors)
        names: set[str] = set()
        for entry in self.fields:
            if not isinstance(entry.field, str) or not entry.field:
                errors.append(f"empty or non-string field name {entry.field!r}")
            if not isinstance(entry.encoding, str) or (
                entry.encoding not in MEMORY_WRITE_ENCODINGS
            ):
                errors.append(f"unknown encoding {entry.encoding!r}")
            if (
                isinstance(entry.offset, bool)
                or not isinstance(entry.offset, int)
                or isinstance(entry.length, bool)
                or not isinstance(entry.length, int)
            ):
                errors.append(f"field {entry.field!r}: offset/length must be ints")
            if entry.encoding == "frequency" and entry.max_hz is not None:
                if isinstance(entry.max_hz, bool) or not isinstance(entry.max_hz, int):
                    errors.append(f"field {entry.field!r}: max_hz must be an int")
                elif not 0 < entry.max_hz <= 9_999_999_999:
                    errors.append(
                        f"field {entry.field!r}: max_hz must be 1..9_999_999_999"
                    )
            if entry.encoding == "fixed":
                if not isinstance(entry.value, str):
                    errors.append(
                        f"field {entry.field!r}: a fixed field needs a hex string value"
                    )
            elif entry.value is not None:
                errors.append(
                    f"field {entry.field!r}: value is only valid on a fixed field"
                )
        if errors:
            return tuple(errors)  # no tiling analysis over junk members
        # Exact tiling of [0, record_length): no gap, no overlap, no
        # undeclared tail byte the preallocated record would silently
        # zero-fill.
        cursor = 0
        for entry in sorted(self.fields, key=lambda item: item.offset):
            if entry.field in names:
                errors.append(f"duplicate field name {entry.field!r}")
            names.add(entry.field)
            if entry.offset < 0 or entry.length < 1:
                errors.append(f"field {entry.field!r}: offset/length must be >= 0/1")
            elif entry.offset + entry.length > self.record_length:
                errors.append(f"field {entry.field!r} runs past record_length")
            elif entry.offset != cursor:
                kind = "overlap" if entry.offset < cursor else "a gap"
                errors.append(f"field {entry.field!r} {kind}s at offset {cursor}")
            cursor = entry.offset + entry.length
            width = _WRITE_ENCODING_WIDTHS.get(entry.encoding)
            if width is not None and entry.length != width:
                errors.append(
                    f"field {entry.field!r}: encoding {entry.encoding!r} "
                    f"needs length {width}, got {entry.length}"
                )
            if entry.encoding == "fixed" and isinstance(entry.value, str):
                if len(entry.value) != 2 * entry.length or any(
                    char not in "0123456789abcdefABCDEF" for char in entry.value
                ):
                    errors.append(
                        f"field {entry.field!r}: the fixed value must be "
                        f"{entry.length} bytes of hex"
                    )
        if cursor != self.record_length:
            errors.append(
                f"the table leaves the last "
                f"{self.record_length - cursor} byte(s) undeclared"
            )
        for encodings, what in (
            (("frequency",), "a frequency"),
            (("mode", "mode_bcd"), "a mode"),
            (("name", "name_nul"), "a name"),
        ):
            if sum(item.encoding in encodings for item in self.fields) != 1:
                errors.append(f"the table must declare exactly {what} field")
        for encoding in ("tone", "tsql", "tone_hz_bcd", "tsql_hz_bcd", "select"):
            count = sum(item.encoding == encoding for item in self.fields)
            if count > 1:
                errors.append(f"the table declares {encoding} more than once")
        has_tone_fields = any(item.encoding in ("tone", "tsql") for item in self.fields)
        if has_tone_fields and self.default_tone_centihz is None:
            # A zero tone field is not a valid CTCSS value: an unset
            # tone/tsql must carry the layout's declared default instead.
            errors.append(
                "the table declares tone/tsql fields but no default_tone_centihz"
            )
        if not has_tone_fields and self.default_tone_centihz is not None:
            errors.append("default_tone_centihz without a tone/tsql field")
        if (
            isinstance(self.copies, int)
            and not isinstance(self.copies, bool)
            and self.copies > 1
        ):
            # A repeated block needs the select byte to lead the record
            # (the repeat starts after it) and the name to trail it.
            span = self.data_block_span()
            if not any(
                entry.encoding == "select" and entry.offset == 0
                for entry in self.fields
            ):
                errors.append(
                    "copies > 1 needs the select field at offset 0 "
                    "(the block repeat starts after it)"
                )
            elif span is None:
                errors.append("copies > 1 needs a name field after the repeated block")
        return tuple(errors)


@dataclass(frozen=True, slots=True)
class MemoryProfile:
    """How a radio's stored memory channels are addressed (MOR-3627).

    ``group_channel`` is the IC-705 shape, ``band_channel`` the IC-9700's and
    ``channel`` a single numbered list (FTX-1, group 0 only); the
    bounds limit the group number (or band code) and the channel, and
    ``recall`` says the profile's select commands recall a listed channel.
    """

    addressing: MemoryAddressing
    first_group: int
    last_group: int
    first_channel: int
    last_channel: int
    recall: bool = False
    dd_groups: tuple[int, ...] = field(default=(), kw_only=True)
    offset_10mhz_groups: tuple[int, ...] = field(default=(), kw_only=True)
    frequency_ranges: tuple[tuple[int, int, int], ...] = field(default=(), kw_only=True)
    # The write-side record layout accepted for this profile (MOR-3644):
    # a field table declaring the verified ``1A 00`` SET record and the
    # channel-clear wire form, validated structurally by
    # :meth:`MemoryWriteLayout.validation_errors` at load time and by the
    # runtime admission gate for SDK-supplied profiles. Absent (the
    # default) means NOT verified: memory contents writes and clears are
    # refused before any byte reaches the wire. Declaring the read-side
    # fields alone proves nothing about the write layout.
    write_layout: MemoryWriteLayout | None = field(default=None, kw_only=True)


@dataclass(frozen=True, slots=True)
class MemoryEntry:
    """One stored memory channel as read from the radio (MOR-3627).

    A read model, not what the write path (:class:`MemoryChannel`) sends.
    ``group`` is the memory group (IC-705) or band code (IC-9700); an empty
    slot carries only its address; tone values are centi-Hz (8850 = 88.5 Hz).
    """

    group: int
    channel: int
    empty: bool = False
    frequency_hz: int | None = None
    mode: str | None = None
    data_mode: bool = False
    duplex: RepeaterShiftDirection | None = None
    offset_hz: int | None = None
    tone_mode: ToneSquelchType | None = None
    tone_centihz: int | None = None
    tsql_centihz: int | None = None
    dtcs_code: int | None = None
    name: str = ""


@dataclass
class BandStackRegister:
    """Band stacking register entry for IC-7610.

    Represents a quick-recall frequency/mode setting for a band.

    Attributes:
        band: Band code (0x00-0x18, see availableBands in wfview).
        register: Register number (1-3).
        frequency_hz: Frequency in Hz.
        mode: Mode code (BCD-encoded).
        filter: Filter number (1-3).
    """

    band: int
    register: int
    frequency_hz: int
    mode: int
    filter: int
