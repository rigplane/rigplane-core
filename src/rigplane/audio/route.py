"""Audio route and LAN stream request resolution."""

from __future__ import annotations

import asyncio
import inspect
import logging
from dataclasses import dataclass
from enum import StrEnum
from typing import TYPE_CHECKING, Any

from rigplane.types import AudioCodec, get_audio_capabilities

if TYPE_CHECKING:
    from rigplane.radio_protocol import Radio

__all__ = [
    "AudioConfigSource",
    "AudioRoute",
    "AudioStreamContract",
    "AudioStreamRequest",
    "DataModePolicy",
    "RadioTransport",
    "RxAudioSource",
    "TxAudioFacts",
    "TxAudioSource",
    "audio_stream_contract_from_request",
    "resolve_lan_audio_stream_request",
    "resolve_audio_route",
    "rigctld_wsjtx_policy",
    "apply_wsjtx_data_mod_input",
    "profile_data_mode_count",
    "tx_audio_facts",
    "tx_codec_for_radio",
    "tx_path_for_codec",
]

logger = logging.getLogger(__name__)

_AUDIO_CAPABILITIES = get_audio_capabilities()
_DEFAULT_RX_CODEC = _AUDIO_CAPABILITIES.default_codec
_CHANNELS_BY_CODEC: dict[AudioCodec, int] = {
    AudioCodec.ULAW_1CH: 1,
    AudioCodec.PCM_1CH_8BIT: 1,
    AudioCodec.PCM_1CH_16BIT: 1,
    AudioCodec.PCM_2CH_8BIT: 2,
    AudioCodec.PCM_2CH_16BIT: 2,
    AudioCodec.ULAW_2CH: 2,
    AudioCodec.OPUS_1CH: 1,
    AudioCodec.OPUS_2CH: 2,
}


class RadioTransport(StrEnum):
    """Control transport family used by the active radio backend."""

    LAN = "lan"
    SERIAL = "serial"
    REMOTE = "remote"
    UNKNOWN = "unknown"


class TxAudioSource(StrEnum):
    """How TX audio enters the radio hardware."""

    LAN = "lan"
    USB = "usb"
    ACC = "acc"
    UNAVAILABLE = "unavailable"


class RxAudioSource(StrEnum):
    """How RX audio leaves the radio hardware."""

    LAN = "lan"
    USB = "usb"
    ACC = "acc"
    UNAVAILABLE = "unavailable"


class DataModePolicy(StrEnum):
    """WSJT-X packet-mode DATA policy implied by an audio route."""

    DATA2_LAN = "data2_lan"
    DATA1_LAN = "data1_lan"
    DATA1_USB = "data1_usb"
    LEGACY = "legacy"


class AudioConfigSource(StrEnum):
    """Where an effective LAN audio request value came from."""

    EXPLICIT = "explicit"
    PROFILE_DEFAULT = "profile-default"
    PROFILE_CODEC_DEFAULT = "profile-codec-default"
    GLOBAL_DEFAULT = "global-default"
    FALLBACK = "fallback"


@dataclass(frozen=True)
class AudioRoute:
    """Resolved radio audio route and the DATA policy derived from it."""

    radio_transport: RadioTransport
    tx_audio_source: TxAudioSource
    rx_audio_source: RxAudioSource
    data_mode_policy: DataModePolicy
    bridge_required: bool


@dataclass(frozen=True, slots=True)
class AudioStreamRequest:
    """Concrete Icom LAN audio values to write into conninfo."""

    rx_codec: AudioCodec
    tx_codec: AudioCodec
    rx_sample_rate_hz: int
    tx_sample_rate_hz: int
    rx_channels: int
    tx_channels: int
    rx_codec_source: AudioConfigSource
    tx_codec_source: AudioConfigSource
    rx_sample_rate_source: AudioConfigSource
    tx_sample_rate_source: AudioConfigSource


@dataclass(frozen=True, slots=True)
class AudioStreamContract:
    """Accepted/effective radio-native Icom LAN audio values."""

    rx_codec: AudioCodec
    tx_codec: AudioCodec
    rx_sample_rate_hz: int
    tx_sample_rate_hz: int
    rx_channels: int
    tx_channels: int
    rx_codec_source: AudioConfigSource
    tx_codec_source: AudioConfigSource
    rx_sample_rate_source: AudioConfigSource
    tx_sample_rate_source: AudioConfigSource
    fallback_reason: str | None = None


def audio_stream_contract_from_request(
    request: AudioStreamRequest,
) -> AudioStreamContract:
    """Create the initial effective contract from a pre-conninfo request."""

    return AudioStreamContract(
        rx_codec=request.rx_codec,
        tx_codec=request.tx_codec,
        rx_sample_rate_hz=request.rx_sample_rate_hz,
        tx_sample_rate_hz=request.tx_sample_rate_hz,
        rx_channels=request.rx_channels,
        tx_channels=request.tx_channels,
        rx_codec_source=request.rx_codec_source,
        tx_codec_source=request.tx_codec_source,
        rx_sample_rate_source=request.rx_sample_rate_source,
        tx_sample_rate_source=request.tx_sample_rate_source,
    )


def profile_data_mode_count(radio: "Radio") -> int:
    """Bound an explicit numeric DATA sub-mode by the radio's profile.

    The profile's ``data_mode_count`` (IC-7610: 3, IC-7300: 1) caps the
    sub-mode a caller may write; a missing or unusable count reads as 1,
    the one DATA mode every data-capable profile declares.
    """
    profile = getattr(radio, "profile", None)
    count = getattr(profile, "data_mode_count", 1)
    return count if isinstance(count, int) and count > 0 else 1


async def apply_wsjtx_data_mod_input(
    radio: "Radio",
    *,
    data_mode: int | bool,
    source: int | None,
) -> None:
    """Select the profile's MOD input for an explicit DATA sub-mode.

    Shared WSJT-X glue (MOR-3821 P0d): with an explicit numeric sub-mode
    (``rigctld_wsjtx_policy`` of the resolved route) the radio transmits
    from the input assigned to that sub-mode, so the matching
    ``set_data<N>_mod_input`` setter runs before the key. A plain boolean
    DATA flag carries no sub-mode, a missing source selects nothing, and
    a radio without that setter keeps its current input: all three are a
    no-op, never an error.
    """
    if source is None or type(data_mode) is not int:
        return
    setter_name = f"set_data{data_mode}_mod_input"
    setter = getattr(radio, setter_name, None)
    if setter is None:
        logger.debug(
            "WSJT-X compat: radio has no %s, skipping DATA mod input",
            setter_name,
        )
        return
    await setter(source)


def _profile_codec(name: str | None) -> AudioCodec | None:
    if not name:
        return None
    try:
        return AudioCodec[name]
    except KeyError:
        return None


def _profile_rx_codec(profile: Any) -> AudioCodec | None:
    preference = getattr(profile, "codec_preference", None)
    if not preference:
        return None
    supported = _AUDIO_CAPABILITIES.supported_codecs
    for name in preference:
        codec = _profile_codec(name)
        if codec in supported:
            return codec
    return None


def _sample_rate_for_codec(
    profile: Any,
    codec: AudioCodec,
) -> tuple[int | None, AudioConfigSource | None]:
    by_codec = getattr(profile, "sample_rate_by_codec", None) or {}
    if codec.name in by_codec:
        return int(by_codec[codec.name]), AudioConfigSource.PROFILE_CODEC_DEFAULT
    default_rate = getattr(profile, "default_sample_rate_hz", None)
    if default_rate is not None:
        return int(default_rate), AudioConfigSource.PROFILE_DEFAULT
    return None, None


def resolve_lan_audio_stream_request(
    *,
    profile: Any,
    requested_rx_codec: AudioCodec | int,
    requested_sample_rate_hz: int,
    rx_codec_explicit: bool = False,
    sample_rate_explicit: bool = False,
) -> AudioStreamRequest:
    """Resolve concrete direct Icom LAN audio values before conninfo.

    The resolver is intentionally small: profile policy may replace only caller
    defaults, while explicit constructor/CLI/env choices remain authoritative.
    """

    requested_codec = AudioCodec(requested_rx_codec)
    if rx_codec_explicit:
        rx_codec = requested_codec
        rx_codec_source = AudioConfigSource.EXPLICIT
    else:
        profile_rx_codec = _profile_rx_codec(profile)
        if profile_rx_codec is not None:
            rx_codec = profile_rx_codec
            rx_codec_source = AudioConfigSource.PROFILE_DEFAULT
        else:
            rx_codec = requested_codec
            rx_codec_source = (
                AudioConfigSource.GLOBAL_DEFAULT
                if requested_codec == _DEFAULT_RX_CODEC
                else AudioConfigSource.EXPLICIT
            )

    profile_tx_codec = _profile_codec(getattr(profile, "tx_codec", None))
    if profile_tx_codec is not None:
        tx_codec = profile_tx_codec
        tx_codec_source = AudioConfigSource.PROFILE_DEFAULT
    else:
        tx_codec = AudioCodec.PCM_1CH_16BIT
        tx_codec_source = AudioConfigSource.GLOBAL_DEFAULT

    if sample_rate_explicit:
        rx_sample_rate_hz = requested_sample_rate_hz
        tx_sample_rate_hz = requested_sample_rate_hz
        rx_sample_rate_source = AudioConfigSource.EXPLICIT
        tx_sample_rate_source = AudioConfigSource.EXPLICIT
    else:
        rx_rate, rx_rate_source = _sample_rate_for_codec(profile, rx_codec)
        tx_rate, tx_rate_source = _sample_rate_for_codec(profile, tx_codec)
        rx_sample_rate_hz = rx_rate or requested_sample_rate_hz
        tx_sample_rate_hz = tx_rate or rx_sample_rate_hz
        rx_sample_rate_source = rx_rate_source or AudioConfigSource.GLOBAL_DEFAULT
        tx_sample_rate_source = tx_rate_source or rx_sample_rate_source

    return AudioStreamRequest(
        rx_codec=rx_codec,
        tx_codec=tx_codec,
        rx_sample_rate_hz=rx_sample_rate_hz,
        tx_sample_rate_hz=tx_sample_rate_hz,
        rx_channels=_CHANNELS_BY_CODEC[rx_codec],
        tx_channels=_CHANNELS_BY_CODEC[tx_codec],
        rx_codec_source=rx_codec_source,
        tx_codec_source=tx_codec_source,
        rx_sample_rate_source=rx_sample_rate_source,
        tx_sample_rate_source=tx_sample_rate_source,
    )


def resolve_audio_route(radio: "Radio") -> AudioRoute:
    """Resolve the active radio audio route without looking at CLI bridge flags."""

    backend_id = getattr(radio, "backend_id", None)
    if backend_id == "rigplane":
        profile = getattr(radio, "profile", None)
        inputs = getattr(profile, "data_mode_inputs", None) or ()
        supports_command = getattr(radio, "supports_command", None)
        data_policy = (
            DataModePolicy.DATA2_LAN
            if profile_data_mode_count(radio) >= 2
            else DataModePolicy.LEGACY
        )
        if (
            data_policy == DataModePolicy.LEGACY
            and (5, "LAN") in inputs
            and callable(supports_command)
            and supports_command("set_data1_mod_input") is True
        ):
            data_policy = DataModePolicy.DATA1_LAN
        return AudioRoute(
            radio_transport=RadioTransport.LAN,
            tx_audio_source=TxAudioSource.LAN,
            rx_audio_source=RxAudioSource.LAN,
            data_mode_policy=data_policy,
            bridge_required=False,
        )

    if (
        backend_id in {"icom_serial", "yaesu_cat"}
        or getattr(radio, "has_usb_audio", False) is True
    ):
        return AudioRoute(
            radio_transport=RadioTransport.SERIAL,
            tx_audio_source=TxAudioSource.USB,
            rx_audio_source=RxAudioSource.USB,
            data_mode_policy=DataModePolicy.DATA1_USB,
            bridge_required=True,
        )

    return AudioRoute(
        radio_transport=RadioTransport.UNKNOWN,
        tx_audio_source=TxAudioSource.UNAVAILABLE,
        rx_audio_source=RxAudioSource.UNAVAILABLE,
        data_mode_policy=DataModePolicy.LEGACY,
        bridge_required=False,
    )


def rigctld_wsjtx_policy(route: AudioRoute) -> tuple[int | None, int | None]:
    """Return ``RigctldConfig`` WSJT-X DATA fields for a resolved route."""

    if route.data_mode_policy == DataModePolicy.DATA2_LAN:
        return 2, 5
    if route.data_mode_policy == DataModePolicy.DATA1_LAN:
        return 1, 5
    return None, None


#: TX route (source, policy) pairs a PCM TX frame can actually exit through
#: (MOR-3821 A2): the radio-side allowlist shared by the web voice-TX seat
#: and the TCI adapter, so there is one copy of it.
TX_ROUTE_ALLOWLIST: frozenset[tuple[TxAudioSource, DataModePolicy]] = frozenset(
    {
        (TxAudioSource.LAN, DataModePolicy.DATA2_LAN),
        (TxAudioSource.LAN, DataModePolicy.DATA1_LAN),
        (TxAudioSource.LAN, DataModePolicy.LEGACY),
        (TxAudioSource.USB, DataModePolicy.DATA1_USB),
        (TxAudioSource.ACC, DataModePolicy.LEGACY),
    }
)


def tx_codec_for_radio(radio: object) -> AudioCodec | None:
    """Radio wire TX codec for PCM/Opus voice paths, or ``None``.

    Moved from ``web/handlers/audio.py`` (MOR-3821 A2): the radio-only
    lookup the web seat and the TCI adapter share.
    """
    codec = getattr(radio, "audio_tx_codec", None)
    if codec is None:
        codec = getattr(getattr(radio, "audio_stream_contract", None), "tx_codec", None)
    if codec is None:
        codec = getattr(radio, "audio_codec", None)
    supported = (AudioCodec.PCM_1CH_16BIT, AudioCodec.OPUS_1CH)
    return codec if isinstance(codec, AudioCodec) and codec in supported else None


def _concrete_async_method(owner: object, name: str, method: object) -> Any | None:
    """A bound coroutine with the exact seam arity, else ``None``.

    Moved from ``web/handlers/audio.py`` (MOR-3821 A2): rejects stubs,
    unbound functions, sync callables and wrong-arity doubles without
    calling anything.
    """
    if not callable(method) or not asyncio.iscoroutinefunction(method):
        return None
    if name in getattr(owner, "__dict__", {}):
        return None
    base = next((cls for cls in type(owner).__mro__ if name in cls.__dict__), None)
    if (
        base is None
        or getattr(base, "_is_protocol", False)
        or getattr(base.__dict__[name], "__isabstractmethod", False)
    ):
        return None
    if getattr(method, "__self__", None) is not owner:
        return None
    args = (b"",) if "push" in name else ("web",) if name == "acquire_tx" else ()
    kwargs = {"sample_rate": 48_000} if name == "start_audio_tx_pcm" else {}
    try:
        inspect.signature(method).bind(*args, **kwargs)
    except (TypeError, ValueError):
        return None
    return method


def _lifecycle(
    owner: object, names: tuple[str, ...]
) -> tuple[tuple[Any, ...] | None, bool]:
    raw = tuple(getattr(owner, name, None) for name in names)
    methods = tuple(
        _concrete_async_method(owner, name, method)
        for name, method in zip(names, raw, strict=True)
    )
    return (methods if all(methods) else None), any(item is not None for item in raw)


def tx_path_for_codec(
    radio: object, codec: AudioCodec
) -> tuple[str, tuple[Any, ...]] | None:
    """How TX audio reaches this radio, or ``None`` when it cannot.

    Moved from ``web/handlers/audio.py`` (MOR-3821 A2): prefers the
    session lease (``acquire_tx``), then the neutral transport surface,
    then the legacy codec shims. The ``("session", ...)`` first element
    carries ``acquire_tx`` followed by the neutral tail; the legacy
    kinds carry the full start/push/stop triple.
    """
    neutral, has_neutral = _lifecycle(radio, ("start_tx", "push_tx", "stop_tx"))
    session = getattr(radio, "audio_session", None)
    if session is not None:
        acquire_raw = getattr(session, "acquire_tx", None)
        acquire = _concrete_async_method(session, "acquire_tx", acquire_raw)
        return ("session", (acquire, *neutral[1:])) if acquire and neutral else None
    if has_neutral:
        return ("neutral", neutral) if neutral is not None else None
    suffix = "pcm" if codec == AudioCodec.PCM_1CH_16BIT else "opus"
    lifecycle, _ = _lifecycle(
        radio, tuple(f"{op}_audio_tx_{suffix}" for op in ("start", "push", "stop"))
    )
    return (suffix, lifecycle) if lifecycle is not None else None


@dataclass(frozen=True, slots=True)
class TxAudioFacts:
    """Radio-only PCM TX route facts (MOR-3821 A2).

    ``codec``/``path``/``lifecycle`` mirror the web seat's probe;
    ``route`` is the resolved route (``None`` when unresolvable) and
    ``required_mod_input`` the WSJT-X MOD input for the route (``None``
    when the caller reports no MOD-input routing). ``route_accepted``
    folds the route allowlist and the required-input sanity into one
    fail-closed bit; ``codec``/``path`` stay populated for diagnosis.
    """

    codec: AudioCodec | None
    path: str | None
    lifecycle: tuple[Any, ...] | None
    route: AudioRoute | None
    required_mod_input: int | None
    route_accepted: bool


def tx_audio_facts(radio: object, *, has_mod_input_routing: bool) -> TxAudioFacts:
    """Resolve the radio-only TX audio facts both voice seats share.

    Never raises: every probe degrades to a field of ``None``/``False``,
    so callers fail closed on radios with a partial audio surface.
    """
    try:
        codec = tx_codec_for_radio(radio)
    except Exception:
        codec = None
    path: str | None = None
    lifecycle: tuple[Any, ...] | None = None
    if codec is not None:
        try:
            probed = tx_path_for_codec(radio, codec)
        except Exception:
            probed = None
        if probed is not None:
            path, lifecycle = probed
    route: AudioRoute | None = None
    required: int | None = None
    try:
        route = resolve_audio_route(radio)
        policy_required = rigctld_wsjtx_policy(route)[1]
        required = policy_required if has_mod_input_routing else None
    except Exception:
        route = None
        required = None
    accepted = False
    if route is not None:
        accepted = (route.tx_audio_source, route.data_mode_policy) in TX_ROUTE_ALLOWLIST
        if (
            accepted
            and required is not None
            and (
                route.tx_audio_source is not TxAudioSource.LAN
                or not isinstance(required, int)
                or isinstance(required, bool)
                or not 0 <= required <= 9_007_199_254_740_991
            )
        ):
            accepted = False
    return TxAudioFacts(
        codec=codec,
        path=path,
        lifecycle=lifecycle,
        route=route,
        required_mod_input=required,
        route_accepted=accepted,
    )
