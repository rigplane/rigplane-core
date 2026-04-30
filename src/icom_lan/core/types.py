"""Enums, dataclasses, and helper functions for the Icom LAN protocol."""

from dataclasses import dataclass
from enum import IntEnum, StrEnum

from icom_lan.env_config import get_audio_sample_rate

__all__ = [
    "PacketType",
    "Mode",
    "AgcMode",
    "AudioPeakFilter",
    "BreakInMode",
    "FilterShape",
    "SsbTxBandwidth",
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
    """Static icom-lan audio capability matrix and defaults."""

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
    """Return icom-lan audio capabilities with deterministic defaults.

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
    """Memory channel data structure for IC-7610.

    Represents a single memory channel entry with frequency, mode, filter,
    tone settings, and name.

    Attributes:
        channel: Memory channel number (1-101).
        frequency_hz: Frequency in Hz.
        mode: Mode code (BCD-encoded, see Mode enum).
        filter: Filter number (1-3).
        scan: Scan flag (0/1).
        datamode: Data mode flag (high nibble of combined byte).
        tonemode: Tone mode flag (low nibble of combined byte).
        tone_freq_hz: CTCSS tone frequency in Hz (optional).
        tsql_freq_hz: TSQL frequency in Hz (optional).
        name: Memory channel name (max 10 ASCII characters).
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
