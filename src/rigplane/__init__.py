"""rigplane: Python library for controlling Icom transceivers over LAN.

Public API is split into two stability tiers (Form F sealing, v0.19+):

* **Tier 1 — STABLE.** Eager-imported below. Semver-protected: breaking
  changes require a major-version bump.
* **Tier 2 — BEST-EFFORT.** Lazy-loaded via :pep:`562` ``__getattr__``. Still
  importable as ``from rigplane import X``; backwards-compatible by default
  but may change between minor releases with a deprecation cycle.

Submodules and private symbols (``rigplane.web``, ``rigplane.cli``,
``rigplane.rigctld``, anything starting with ``_``) are **internal** and
may change without warning.
"""

# Import directly from the submodule to keep ``import rigplane`` lightweight.
# The diagnostics package also exposes upload helpers that initialize the HTTP
# client stack; package import should not pay that cost. See issue #1413.
from rigplane.diagnostics._logging import (
    configure_diagnostic_logging as _configure_diagnostic_logging,
)

_configure_diagnostic_logging()
del _configure_diagnostic_logging

from importlib.metadata import version as _pkg_version  # noqa: E402
from typing import TYPE_CHECKING, Any  # noqa: E402

__version__ = _pkg_version("rigplane")

# === Tier 1 — eager (semver-stable from v0.19) ===

from .backends import (  # noqa: F401, E402
    BackendConfig,
    LanBackendConfig,
    RigctldBackendConfig,
    SerialBackendConfig,
    YaesuCatBackendConfig,
    create_radio,
)
from .exceptions import (  # noqa: F401, E402
    AudioCodecBackendError,
    AudioError,
    AudioFormatError,
    AudioTranscodeError,
    AuthenticationError,
    CommandError,
    ConnectionError,
    RigplaneError,
    TimeoutError,
)
from .profiles import RadioProfile  # noqa: F401, E402
from .radio_protocol import (  # noqa: F401, E402
    AdvancedControlCapable,
    AntennaControlCapable,
    AudioCapable,
    AudioTransport,
    CivCommandCapable,
    CwControlCapable,
    DspControlCapable,
    DualReceiverCapable,
    LevelsCapable,
    MemoryCapable,
    MetersCapable,
    ModeInfoCapable,
    PowerControlCapable,
    Radio,
    ReceiverBankCapable,
    RecoverableConnection,
    RepeaterControlCapable,
    RepeaterShiftCapable,
    RigctldRoutable,
    RitXitCapable,
    ScopeCapable,
    SplitCapable,
    StateCacheCapable,
    StateNotifyCapable,
    StatePollable,
    StatePoller,
    SystemControlCapable,
    TransceiverBankCapable,
    UsbAudioCapable,
    VfoSlotCapable,
    VoiceControlCapable,
)
from .radio_state import RadioState, VfoSlotState, YaesuStateExtension  # noqa: F401, E402
from .runtime.session_lifecycle import (  # noqa: F401, E402
    LifecycleErrorReason,
    LifecycleEvent,
    LifecycleState,
    LifecycleStatus,
    RadioPresence,
    RadioSessionLifecycle,
)
from .types import (  # noqa: F401, E402
    AudioCodec,
    BreakInMode,
    Mode,
    RepeaterShiftDirection,
)

# Static types for the lazy WebRTC wire contracts (MOR-272): type checkers
# see the real classes; at runtime they stay lazy (``_LAZY_MAP`` below).
if TYPE_CHECKING:
    from .web.api_channel import (  # noqa: F401, TID251
        SEND_HIGH_WATER_BYTES as API_SEND_HIGH_WATER_BYTES,
    )
    from .web.api_channel import (  # noqa: F401, TID251
        ApiErrorFrame,
        ApiFrame,
        ApiFrameError,
        ApiMessageType,
        ApiPayloadError,
        ApiRequestHead,
        ApiResponseHead,
        api_request_messages,
        parse_api_server_frame,
    )
    from .web.channel_attach import (  # noqa: F401, TID251
        ATTACH_ACK_VERSION,
        CONTROL_WS_QUERY_KEYS,
        DATA_CHANNELS,
        RIGCTL_MAX_FRAME_BYTES,
        RIGCTL_VERSIONS,
        ChannelAttachedAck,
        ChannelKind,
        ClientChannelParams,
        ControlWsQuery,
        ControllerRole,
        DataChannelLabel,
        DataChannelSpec,
        RigctlAttachErrorCode,
        RigctlAttachErrorV1,
        decode_rigctl_attach_result,
    )
    from .web.memory_api import (  # noqa: F401, TID251
        MEMORIES_QUERY_KEYS,
        MemoriesQuery,
    )
    from .web.transport.ice_gathering import (  # noqa: F401, TID251
        GATHER_TIMEOUT_S,
        IceGatheringPolicy,
        IceServer,
        apply_ice_gathering_policy,
        install_ice_gathering_policy,
    )
    from .web.transport.webrtc_contract import (  # noqa: F401, TID251
        IceCandidateInit,
        WebRtcAnswer,
        WebRtcClose,
        WebRtcCloseResult,
        WebRtcIceCandidate,
        WebRtcOffer,
        WebRtcPath,
        WebRtcRequestError,
        WebRtcSessionEnded,
        WebRtcSessionEndReason,
        WebRtcTransportStatus,
    )
    from .web.transport.turn_selection import (  # noqa: F401, TID251
        UdpReachabilityCache,
        select_turn_url,
    )

# === Tier 2 — lazy via PEP 562 ===
#
# Names below are still importable as ``from rigplane import X`` and
# ``rigplane.X``, but their backing modules are loaded only on first
# access. This keeps ``from rigplane import Radio`` from transitively
# pulling in audio, transport, web, rigctld and CLI machinery.
#
# Format: name -> (module, attribute)
_LAZY_MAP: dict[str, tuple[str, str]] = {
    # --- Hamlib discovery payload builder (MOR-911) ---
    "build_hamlib_discovery_payload": (
        "rigplane.backends.discovery",
        "build_hamlib_discovery_payload",
    ),
    # --- LAN radio liveness probe (MOR-3669) ---
    "probe_lan_radio": ("rigplane.backends.discovery", "probe_lan_radio"),
    # --- WebRTC wire contracts for out-of-process offerers (MOR-272) ---
    "ClientChannelParams": ("rigplane.web.channel_attach", "ClientChannelParams"),
    "ChannelAttachedAck": ("rigplane.web.channel_attach", "ChannelAttachedAck"),
    "ChannelKind": ("rigplane.web.channel_attach", "ChannelKind"),
    "DataChannelSpec": ("rigplane.web.channel_attach", "DataChannelSpec"),
    "DataChannelLabel": ("rigplane.web.channel_attach", "DataChannelLabel"),
    "DATA_CHANNELS": ("rigplane.web.channel_attach", "DATA_CHANNELS"),
    "ATTACH_ACK_VERSION": ("rigplane.web.channel_attach", "ATTACH_ACK_VERSION"),
    "RIGCTL_MAX_FRAME_BYTES": ("rigplane.web.channel_attach", "RIGCTL_MAX_FRAME_BYTES"),
    "RIGCTL_VERSIONS": ("rigplane.web.channel_attach", "RIGCTL_VERSIONS"),
    "RigctlAttachErrorCode": ("rigplane.web.channel_attach", "RigctlAttachErrorCode"),
    "RigctlAttachErrorV1": ("rigplane.web.channel_attach", "RigctlAttachErrorV1"),
    "decode_rigctl_attach_result": (
        "rigplane.web.channel_attach",
        "decode_rigctl_attach_result",
    ),
    # --- HTTP/WS query contracts (MOR-4252) ---
    "ControlWsQuery": ("rigplane.web.channel_attach", "ControlWsQuery"),
    "ControllerRole": ("rigplane.web.channel_attach", "ControllerRole"),
    "CONTROL_WS_QUERY_KEYS": (
        "rigplane.web.channel_attach",
        "CONTROL_WS_QUERY_KEYS",
    ),
    "MemoriesQuery": ("rigplane.web.memory_api", "MemoriesQuery"),
    "MEMORIES_QUERY_KEYS": ("rigplane.web.memory_api", "MEMORIES_QUERY_KEYS"),
    "IceServer": ("rigplane.web.transport.ice_gathering", "IceServer"),
    "IceGatheringPolicy": (
        "rigplane.web.transport.ice_gathering",
        "IceGatheringPolicy",
    ),
    "GATHER_TIMEOUT_S": ("rigplane.web.transport.ice_gathering", "GATHER_TIMEOUT_S"),
    "install_ice_gathering_policy": (
        "rigplane.web.transport.ice_gathering",
        "install_ice_gathering_policy",
    ),
    "apply_ice_gathering_policy": (
        "rigplane.web.transport.ice_gathering",
        "apply_ice_gathering_policy",
    ),
    "WebRtcOffer": ("rigplane.web.transport.webrtc_contract", "WebRtcOffer"),
    "WebRtcPath": ("rigplane.web.transport.webrtc_contract", "WebRtcPath"),
    "WebRtcSessionEnded": (
        "rigplane.web.transport.webrtc_contract",
        "WebRtcSessionEnded",
    ),
    "WebRtcSessionEndReason": (
        "rigplane.web.transport.webrtc_contract",
        "WebRtcSessionEndReason",
    ),
    "WebRtcAnswer": ("rigplane.web.transport.webrtc_contract", "WebRtcAnswer"),
    "WebRtcClose": ("rigplane.web.transport.webrtc_contract", "WebRtcClose"),
    "WebRtcCloseResult": (
        "rigplane.web.transport.webrtc_contract",
        "WebRtcCloseResult",
    ),
    "WebRtcIceCandidate": (
        "rigplane.web.transport.webrtc_contract",
        "WebRtcIceCandidate",
    ),
    "WebRtcRequestError": (
        "rigplane.web.transport.webrtc_contract",
        "WebRtcRequestError",
    ),
    "WebRtcTransportStatus": (
        "rigplane.web.transport.webrtc_contract",
        "WebRtcTransportStatus",
    ),
    "IceCandidateInit": ("rigplane.web.transport.webrtc_contract", "IceCandidateInit"),
    "UdpReachabilityCache": (
        "rigplane.web.transport.turn_selection",
        "UdpReachabilityCache",
    ),
    "select_turn_url": ("rigplane.web.transport.turn_selection", "select_turn_url"),
    "ApiErrorFrame": ("rigplane.web.api_channel", "ApiErrorFrame"),
    "ApiFrame": ("rigplane.web.api_channel", "ApiFrame"),
    "ApiFrameError": ("rigplane.web.api_channel", "ApiFrameError"),
    "ApiMessageType": ("rigplane.web.api_channel", "ApiMessageType"),
    "ApiPayloadError": ("rigplane.web.api_channel", "ApiPayloadError"),
    "ApiRequestHead": ("rigplane.web.api_channel", "ApiRequestHead"),
    "ApiResponseHead": ("rigplane.web.api_channel", "ApiResponseHead"),
    "parse_api_server_frame": ("rigplane.web.api_channel", "parse_api_server_frame"),
    "api_request_messages": ("rigplane.web.api_channel", "api_request_messages"),
    "API_SEND_HIGH_WATER_BYTES": ("rigplane.web.api_channel", "SEND_HIGH_WATER_BYTES"),
    # --- Backward-compat radio facades ---
    "IcomRadio": ("rigplane.runtime.radio", "IcomRadio"),
    "AudioRecoveryState": ("rigplane.runtime.radio", "AudioRecoveryState"),
    # --- CI-V commander internals ---
    "IcomCommander": ("rigplane.commands.commander", "IcomCommander"),
    "Priority": ("rigplane.commands.commander", "Priority"),
    # --- Connection / transport ---
    "ConnectionState": ("rigplane.core.transport", "ConnectionState"),
    "IcomTransport": ("rigplane.core.transport", "IcomTransport"),
    "RadioConnectionState": (
        "rigplane.runtime._connection_state",
        "RadioConnectionState",
    ),
    # --- Auth helpers ---
    "AuthResponse": ("rigplane.core.auth", "AuthResponse"),
    "StatusResponse": ("rigplane.core.auth", "StatusResponse"),
    "build_conninfo_packet": ("rigplane.core.auth", "build_conninfo_packet"),
    "build_login_packet": ("rigplane.core.auth", "build_login_packet"),
    "encode_credentials": ("rigplane.core.auth", "encode_credentials"),
    "parse_auth_response": ("rigplane.core.auth", "parse_auth_response"),
    "parse_status_response": ("rigplane.core.auth", "parse_status_response"),
    # --- Wire-protocol helpers ---
    "identify_packet_type": ("rigplane.core.protocol", "identify_packet_type"),
    "parse_header": ("rigplane.core.protocol", "parse_header"),
    "serialize_header": ("rigplane.core.protocol", "serialize_header"),
    # --- Audio (LAN stream) ---
    "AUDIO_HEADER_SIZE": ("rigplane.audio", "AUDIO_HEADER_SIZE"),
    "AudioPacket": ("rigplane.audio", "AudioPacket"),
    "AudioState": ("rigplane.audio", "AudioState"),
    "AudioStats": ("rigplane.audio", "AudioStats"),
    "AudioStream": ("rigplane.audio", "AudioStream"),
    "JitterBuffer": ("rigplane.audio", "JitterBuffer"),
    # --- Audio backend / DSP / config ---
    "AudioBackend": ("rigplane.audio.backend", "AudioBackend"),
    "PortAudioBackend": ("rigplane.audio.backend", "PortAudioBackend"),
    "FakeAudioBackend": ("rigplane.audio.backend", "FakeAudioBackend"),
    "NoiseGate": ("rigplane.audio.dsp", "NoiseGate"),
    "RmsNormalizer": ("rigplane.audio.dsp", "RmsNormalizer"),
    "Limiter": ("rigplane.audio.dsp", "Limiter"),
    "DspPipeline": ("rigplane.audio.dsp", "DspPipeline"),
    "AudioConfig": ("rigplane.audio.config", "AudioConfig"),
    "UsbAudioDriver": ("rigplane.audio.usb_driver", "UsbAudioDriver"),
    # --- Profiles / runtime profiles ---
    "get_radio_profile": ("rigplane.profiles", "get_radio_profile"),
    "resolve_radio_profile": ("rigplane.profiles", "resolve_radio_profile"),
    "OperatingProfile": ("rigplane.runtime.profiles_runtime", "OperatingProfile"),
    "apply_profile": ("rigplane.runtime.profiles_runtime", "apply_profile"),
    "PRESETS": ("rigplane.runtime.profiles_runtime", "PRESETS"),
    # --- Radio model registry ---
    "RADIOS": ("rigplane.runtime.radios", "RADIOS"),
    "RadioModel": ("rigplane.runtime.radios", "RadioModel"),
    "get_civ_addr": ("rigplane.runtime.radios", "get_civ_addr"),
    "IC_7610_ADDR": ("rigplane.runtime.radios", "IC_7610_ADDR"),
    # --- IC-705 helpers ---
    "prepare_ic705_data_profile": (
        "rigplane.runtime.ic705",
        "prepare_ic705_data_profile",
    ),
    "restore_ic705_data_profile": (
        "rigplane.runtime.ic705",
        "restore_ic705_data_profile",
    ),
    # --- CI-V command helpers (subset historically re-exported) ---
    "CONTROLLER_ADDR": ("rigplane.commands", "CONTROLLER_ADDR"),
    "RECEIVER_MAIN": ("rigplane.commands", "RECEIVER_MAIN"),
    "RECEIVER_SUB": ("rigplane.commands", "RECEIVER_SUB"),
    "build_civ_frame": ("rigplane.commands", "build_civ_frame"),
    "build_cmd29_frame": ("rigplane.commands", "build_cmd29_frame"),
    "parse_ack_nak": ("rigplane.commands", "parse_ack_nak"),
    "parse_civ_frame": ("rigplane.commands", "parse_civ_frame"),
    "parse_frequency_response": ("rigplane.commands", "parse_frequency_response"),
    "parse_meter_response": ("rigplane.commands", "parse_meter_response"),
    "parse_mode_response": ("rigplane.commands", "parse_mode_response"),
    "get_alc": ("rigplane.commands", "get_alc"),
    "get_attenuator": ("rigplane.commands", "get_attenuator"),
    "get_freq": ("rigplane.commands", "get_freq"),
    "get_mode": ("rigplane.commands", "get_mode"),
    "get_rf_power": ("rigplane.commands", "get_rf_power"),
    "get_preamp": ("rigplane.commands", "get_preamp"),
    "get_s_meter": ("rigplane.commands", "get_s_meter"),
    "get_swr": ("rigplane.commands", "get_swr"),
    "ptt_off": ("rigplane.commands", "ptt_off"),
    "ptt_on": ("rigplane.commands", "ptt_on"),
    "get_af_level": ("rigplane.commands", "get_af_level"),
    "get_rf_gain": ("rigplane.commands", "get_rf_gain"),
    "set_af_level": ("rigplane.commands", "set_af_level"),
    "set_rf_gain": ("rigplane.commands", "set_rf_gain"),
    "set_attenuator_level": ("rigplane.commands", "set_attenuator_level"),
    "set_freq": ("rigplane.commands", "set_freq"),
    "set_mode": ("rigplane.commands", "set_mode"),
    "set_rf_power": ("rigplane.commands", "set_rf_power"),
    "set_preamp": ("rigplane.commands", "set_preamp"),
    "get_scope_center_type": ("rigplane.commands", "get_scope_center_type"),
    "get_scope_during_tx": ("rigplane.commands", "get_scope_during_tx"),
    "get_scope_edge": ("rigplane.commands", "get_scope_edge"),
    "get_scope_fixed_edge": ("rigplane.commands", "get_scope_fixed_edge"),
    "get_scope_hold": ("rigplane.commands", "get_scope_hold"),
    "get_scope_main_sub": ("rigplane.commands", "get_scope_main_sub"),
    "get_scope_mode": ("rigplane.commands", "get_scope_mode"),
    "get_scope_rbw": ("rigplane.commands", "get_scope_rbw"),
    "get_scope_ref": ("rigplane.commands", "get_scope_ref"),
    "get_scope_single_dual": ("rigplane.commands", "get_scope_single_dual"),
    "get_scope_span": ("rigplane.commands", "get_scope_span"),
    "get_scope_speed": ("rigplane.commands", "get_scope_speed"),
    "get_scope_vbw": ("rigplane.commands", "get_scope_vbw"),
    "scope_main_sub": ("rigplane.commands", "scope_main_sub"),
    "scope_off": ("rigplane.commands", "scope_off"),
    "scope_on": ("rigplane.commands", "scope_on"),
    "scope_set_center_type": ("rigplane.commands", "scope_set_center_type"),
    "scope_set_during_tx": ("rigplane.commands", "scope_set_during_tx"),
    "scope_set_edge": ("rigplane.commands", "scope_set_edge"),
    "scope_set_fixed_edge": ("rigplane.commands", "scope_set_fixed_edge"),
    "scope_set_hold": ("rigplane.commands", "scope_set_hold"),
    "scope_set_mode": ("rigplane.commands", "scope_set_mode"),
    "scope_set_rbw": ("rigplane.commands", "scope_set_rbw"),
    "scope_set_ref": ("rigplane.commands", "scope_set_ref"),
    "scope_set_span": ("rigplane.commands", "scope_set_span"),
    "scope_set_speed": ("rigplane.commands", "scope_set_speed"),
    "scope_set_vbw": ("rigplane.commands", "scope_set_vbw"),
    "scope_single_dual": ("rigplane.commands", "scope_single_dual"),
    # --- Scope assembler / frames ---
    "ScopeAssembler": ("rigplane.scope", "ScopeAssembler"),
    "ScopeFrame": ("rigplane.scope", "ScopeFrame"),
    # --- Scope rendering (optional Pillow dep — handled by ImportError below) ---
    "SCOPE_THEMES": ("rigplane.scope.render", "THEMES"),
    "amplitude_to_color": ("rigplane.scope.render", "amplitude_to_color"),
    "render_scope_image": ("rigplane.scope.render", "render_scope_image"),
    "render_spectrum": ("rigplane.scope.render", "render_spectrum"),
    "render_waterfall": ("rigplane.scope.render", "render_waterfall"),
    # --- Public types not in tier-1 ---
    "HEADER_SIZE": ("rigplane.core.types", "HEADER_SIZE"),
    "AudioCapabilities": ("rigplane.core.types", "AudioCapabilities"),
    "CivFrame": ("rigplane.core.types", "CivFrame"),
    "PacketHeader": ("rigplane.core.types", "PacketHeader"),
    "PacketType": ("rigplane.core.types", "PacketType"),
    "ScopeCompletionPolicy": ("rigplane.core.types", "ScopeCompletionPolicy"),
    "ScopeFixedEdge": ("rigplane.core.types", "ScopeFixedEdge"),
    "bcd_decode": ("rigplane.core.types", "bcd_decode"),
    "bcd_encode": ("rigplane.core.types", "bcd_encode"),
    "get_audio_capabilities": ("rigplane.core.types", "get_audio_capabilities"),
}


def __getattr__(name: str) -> Any:
    """:pep:`562` lazy attribute hook for tier-2 names.

    Resolves tier-2 names on first access, caches them in ``globals()``
    so subsequent lookups skip this hook entirely. Returns :class:`~typing.Any`
    so downstream typecheckers (mypy, pyright) keep the original symbol
    types when a consumer does ``from rigplane import IcomRadio``.
    """
    target = _LAZY_MAP.get(name)
    if target is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    module_name, attr_name = target
    import importlib

    module = importlib.import_module(module_name)
    attr = getattr(module, attr_name)
    globals()[name] = attr  # cache for subsequent access (skip __getattr__)
    return attr


def __dir__() -> list[str]:
    """Include lazy tier-2 names in ``dir(rigplane)`` for IDE/REPL discovery."""
    return sorted({*globals().keys(), *_LAZY_MAP.keys()})


# Public API surface — tier-1 (semver-stable) + tier-2 (best-effort lazy).
__all__ = [
    "__version__",
    # --- Tier 1: Factory ---
    "create_radio",
    # --- Tier 1: Backend configs ---
    "BackendConfig",
    "LanBackendConfig",
    "RigctldBackendConfig",
    "SerialBackendConfig",
    "YaesuCatBackendConfig",
    # --- Tier 1: Radio + capability protocols ---
    "Radio",
    "AdvancedControlCapable",
    "AntennaControlCapable",
    "AudioCapable",
    "AudioTransport",
    "CivCommandCapable",
    "CwControlCapable",
    "DspControlCapable",
    "DualReceiverCapable",
    "LevelsCapable",
    "MemoryCapable",
    "MetersCapable",
    "ModeInfoCapable",
    "PowerControlCapable",
    "ReceiverBankCapable",
    "RecoverableConnection",
    "RepeaterControlCapable",
    "RepeaterShiftCapable",
    "RigctldRoutable",
    "RitXitCapable",
    "ScopeCapable",
    "SplitCapable",
    "StateCacheCapable",
    "StateNotifyCapable",
    "StatePollable",
    "StatePoller",
    "SystemControlCapable",
    "TransceiverBankCapable",
    "UsbAudioCapable",
    "VfoSlotCapable",
    "VoiceControlCapable",
    # --- Tier 1: Exceptions ---
    "RigplaneError",
    "ConnectionError",
    "AuthenticationError",
    "CommandError",
    "TimeoutError",
    "AudioError",
    "AudioCodecBackendError",
    "AudioFormatError",
    "AudioTranscodeError",
    # --- Tier 1: Public types/enums ---
    "Mode",
    "AudioCodec",
    "BreakInMode",
    "RepeaterShiftDirection",
    # --- Tier 1: Public state types ---
    "RadioState",
    "RadioProfile",
    "VfoSlotState",
    "YaesuStateExtension",
    # --- Tier 1: Session lifecycle (v2.11, D6) ---
    "RadioSessionLifecycle",
    "LifecycleState",
    "LifecycleStatus",
    "LifecycleEvent",
    "LifecycleErrorReason",
    "RadioPresence",
    # --- Tier 2 (lazy): backward-compat facade ---
    "IcomRadio",
    # --- Tier 2 (lazy): commander internals ---
    "IcomCommander",
    "Priority",
    # --- Tier 2 (lazy): audio primitives ---
    "AudioStream",
    "AudioBackend",
    "PortAudioBackend",
    "FakeAudioBackend",
    "AudioConfig",
    "NoiseGate",
    "RmsNormalizer",
    "Limiter",
    "DspPipeline",
    "UsbAudioDriver",
    # --- Tier 2 (lazy): WebRTC wire contracts (MOR-272) ---
    "ClientChannelParams",
    "ChannelAttachedAck",
    "ChannelKind",
    "DataChannelSpec",
    "DataChannelLabel",
    "DATA_CHANNELS",
    "ATTACH_ACK_VERSION",
    "RIGCTL_MAX_FRAME_BYTES",
    "RIGCTL_VERSIONS",
    "RigctlAttachErrorCode",
    "RigctlAttachErrorV1",
    "decode_rigctl_attach_result",
    "IceServer",
    "IceGatheringPolicy",
    "GATHER_TIMEOUT_S",
    "install_ice_gathering_policy",
    "apply_ice_gathering_policy",
    "WebRtcOffer",
    "WebRtcPath",
    "WebRtcSessionEnded",
    "WebRtcSessionEndReason",
    "WebRtcAnswer",
    "WebRtcClose",
    "WebRtcCloseResult",
    "WebRtcIceCandidate",
    "WebRtcRequestError",
    "WebRtcTransportStatus",
    "IceCandidateInit",
    "UdpReachabilityCache",
    "select_turn_url",
    "ApiFrame",
    "ApiFrameError",
    "ApiMessageType",
    "ApiRequestHead",
    "ApiResponseHead",
    "ApiErrorFrame",
    "ApiPayloadError",
    "api_request_messages",
    "parse_api_server_frame",
    "API_SEND_HIGH_WATER_BYTES",
    # --- Tier 2 (lazy): HTTP/WS query contracts (MOR-4252) ---
    "ControlWsQuery",
    "ControllerRole",
    "CONTROL_WS_QUERY_KEYS",
    "MemoriesQuery",
    "MEMORIES_QUERY_KEYS",
]
