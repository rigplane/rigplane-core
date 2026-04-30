"""icom-lan: Python library for controlling Icom transceivers over LAN.

Public API is split into two stability tiers (Form F sealing, v0.19+):

* **Tier 1 — STABLE.** Eager-imported below. Semver-protected: breaking
  changes require a major-version bump.
* **Tier 2 — BEST-EFFORT.** Lazy-loaded via :pep:`562` ``__getattr__``. Still
  importable as ``from icom_lan import X``; backwards-compatible by default
  but may change between minor releases with a deprecation cycle.

Submodules and private symbols (``icom_lan.web``, ``icom_lan.cli``,
``icom_lan.rigctld``, anything starting with ``_``) are **internal** and
may change without warning.
"""

from importlib.metadata import version as _pkg_version
from typing import Any

__version__ = _pkg_version("icom-lan")

# === Tier 1 — eager (semver-stable from v0.19) ===

from .backends import (  # noqa: F401
    BackendConfig,
    LanBackendConfig,
    SerialBackendConfig,
    YaesuCatBackendConfig,
    create_radio,
)
from .exceptions import (  # noqa: F401
    AudioCodecBackendError,
    AudioError,
    AudioFormatError,
    AudioTranscodeError,
    AuthenticationError,
    CommandError,
    ConnectionError,
    IcomLanError,
    TimeoutError,
)
from .profiles import RadioProfile  # noqa: F401
from .radio_protocol import (  # noqa: F401
    AdvancedControlCapable,
    AntennaControlCapable,
    AudioCapable,
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
    RitXitCapable,
    ScopeCapable,
    SplitCapable,
    StateCacheCapable,
    StateNotifyCapable,
    SystemControlCapable,
    TransceiverBankCapable,
    TransceiverStatusCapable,
    VfoSlotCapable,
    VoiceControlCapable,
)
from .radio_state import RadioState, VfoSlotState, YaesuStateExtension  # noqa: F401
from .types import AudioCodec, BreakInMode, Mode  # noqa: F401

# === Tier 2 — lazy via PEP 562 ===
#
# Names below are still importable as ``from icom_lan import X`` and
# ``icom_lan.X``, but their backing modules are loaded only on first
# access. This keeps ``from icom_lan import Radio`` from transitively
# pulling in audio, transport, web, rigctld and CLI machinery.
#
# Format: name -> (module, attribute)
_LAZY_MAP: dict[str, tuple[str, str]] = {
    # --- Backward-compat radio facades ---
    "IcomRadio": ("icom_lan.runtime.radio", "IcomRadio"),
    "AudioRecoveryState": ("icom_lan.runtime.radio", "AudioRecoveryState"),
    # --- CI-V commander internals ---
    "IcomCommander": ("icom_lan.commands.commander", "IcomCommander"),
    "Priority": ("icom_lan.commands.commander", "Priority"),
    # --- Connection / transport ---
    "ConnectionState": ("icom_lan.core.transport", "ConnectionState"),
    "IcomTransport": ("icom_lan.core.transport", "IcomTransport"),
    "RadioConnectionState": ("icom_lan.runtime._connection_state", "RadioConnectionState"),
    # --- Auth helpers ---
    "AuthResponse": ("icom_lan.core.auth", "AuthResponse"),
    "StatusResponse": ("icom_lan.core.auth", "StatusResponse"),
    "build_conninfo_packet": ("icom_lan.core.auth", "build_conninfo_packet"),
    "build_login_packet": ("icom_lan.core.auth", "build_login_packet"),
    "encode_credentials": ("icom_lan.core.auth", "encode_credentials"),
    "parse_auth_response": ("icom_lan.core.auth", "parse_auth_response"),
    "parse_status_response": ("icom_lan.core.auth", "parse_status_response"),
    # --- Wire-protocol helpers ---
    "identify_packet_type": ("icom_lan.core.protocol", "identify_packet_type"),
    "parse_header": ("icom_lan.core.protocol", "parse_header"),
    "serialize_header": ("icom_lan.core.protocol", "serialize_header"),
    # --- Audio (LAN stream) ---
    "AUDIO_HEADER_SIZE": ("icom_lan.audio", "AUDIO_HEADER_SIZE"),
    "AudioPacket": ("icom_lan.audio", "AudioPacket"),
    "AudioState": ("icom_lan.audio", "AudioState"),
    "AudioStats": ("icom_lan.audio", "AudioStats"),
    "AudioStream": ("icom_lan.audio", "AudioStream"),
    "JitterBuffer": ("icom_lan.audio", "JitterBuffer"),
    # --- Audio backend / DSP / config ---
    "AudioBackend": ("icom_lan.audio.backend", "AudioBackend"),
    "PortAudioBackend": ("icom_lan.audio.backend", "PortAudioBackend"),
    "FakeAudioBackend": ("icom_lan.audio.backend", "FakeAudioBackend"),
    "NoiseGate": ("icom_lan.audio.dsp", "NoiseGate"),
    "RmsNormalizer": ("icom_lan.audio.dsp", "RmsNormalizer"),
    "Limiter": ("icom_lan.audio.dsp", "Limiter"),
    "DspPipeline": ("icom_lan.audio.dsp", "DspPipeline"),
    "AudioConfig": ("icom_lan.audio.config", "AudioConfig"),
    "UsbAudioDriver": ("icom_lan.audio.usb_driver", "UsbAudioDriver"),
    # --- Profiles / runtime profiles ---
    "get_radio_profile": ("icom_lan.profiles", "get_radio_profile"),
    "resolve_radio_profile": ("icom_lan.profiles", "resolve_radio_profile"),
    "OperatingProfile": ("icom_lan.runtime.profiles_runtime", "OperatingProfile"),
    "apply_profile": ("icom_lan.runtime.profiles_runtime", "apply_profile"),
    "PRESETS": ("icom_lan.runtime.profiles_runtime", "PRESETS"),
    # --- Radio model registry ---
    "RADIOS": ("icom_lan.runtime.radios", "RADIOS"),
    "RadioModel": ("icom_lan.runtime.radios", "RadioModel"),
    "get_civ_addr": ("icom_lan.runtime.radios", "get_civ_addr"),
    "IC_7610_ADDR": ("icom_lan.runtime.radios", "IC_7610_ADDR"),
    # --- IC-705 helpers ---
    "prepare_ic705_data_profile": (
        "icom_lan.runtime.ic705",
        "prepare_ic705_data_profile",
    ),
    "restore_ic705_data_profile": (
        "icom_lan.runtime.ic705",
        "restore_ic705_data_profile",
    ),
    # --- CI-V command helpers (subset historically re-exported) ---
    "CONTROLLER_ADDR": ("icom_lan.commands", "CONTROLLER_ADDR"),
    "RECEIVER_MAIN": ("icom_lan.commands", "RECEIVER_MAIN"),
    "RECEIVER_SUB": ("icom_lan.commands", "RECEIVER_SUB"),
    "build_civ_frame": ("icom_lan.commands", "build_civ_frame"),
    "build_cmd29_frame": ("icom_lan.commands", "build_cmd29_frame"),
    "parse_ack_nak": ("icom_lan.commands", "parse_ack_nak"),
    "parse_civ_frame": ("icom_lan.commands", "parse_civ_frame"),
    "parse_frequency_response": ("icom_lan.commands", "parse_frequency_response"),
    "parse_meter_response": ("icom_lan.commands", "parse_meter_response"),
    "parse_mode_response": ("icom_lan.commands", "parse_mode_response"),
    "get_alc": ("icom_lan.commands", "get_alc"),
    "get_attenuator": ("icom_lan.commands", "get_attenuator"),
    "get_freq": ("icom_lan.commands", "get_freq"),
    "get_mode": ("icom_lan.commands", "get_mode"),
    "get_rf_power": ("icom_lan.commands", "get_rf_power"),
    "get_preamp": ("icom_lan.commands", "get_preamp"),
    "get_s_meter": ("icom_lan.commands", "get_s_meter"),
    "get_swr": ("icom_lan.commands", "get_swr"),
    "ptt_off": ("icom_lan.commands", "ptt_off"),
    "ptt_on": ("icom_lan.commands", "ptt_on"),
    "get_af_level": ("icom_lan.commands", "get_af_level"),
    "get_rf_gain": ("icom_lan.commands", "get_rf_gain"),
    "set_af_level": ("icom_lan.commands", "set_af_level"),
    "set_rf_gain": ("icom_lan.commands", "set_rf_gain"),
    "set_attenuator": ("icom_lan.commands", "set_attenuator"),
    "set_attenuator_level": ("icom_lan.commands", "set_attenuator_level"),
    "set_freq": ("icom_lan.commands", "set_freq"),
    "set_mode": ("icom_lan.commands", "set_mode"),
    "set_rf_power": ("icom_lan.commands", "set_rf_power"),
    "set_preamp": ("icom_lan.commands", "set_preamp"),
    "get_scope_center_type": ("icom_lan.commands", "get_scope_center_type"),
    "get_scope_during_tx": ("icom_lan.commands", "get_scope_during_tx"),
    "get_scope_edge": ("icom_lan.commands", "get_scope_edge"),
    "get_scope_fixed_edge": ("icom_lan.commands", "get_scope_fixed_edge"),
    "get_scope_hold": ("icom_lan.commands", "get_scope_hold"),
    "get_scope_main_sub": ("icom_lan.commands", "get_scope_main_sub"),
    "get_scope_mode": ("icom_lan.commands", "get_scope_mode"),
    "get_scope_rbw": ("icom_lan.commands", "get_scope_rbw"),
    "get_scope_ref": ("icom_lan.commands", "get_scope_ref"),
    "get_scope_single_dual": ("icom_lan.commands", "get_scope_single_dual"),
    "get_scope_span": ("icom_lan.commands", "get_scope_span"),
    "get_scope_speed": ("icom_lan.commands", "get_scope_speed"),
    "get_scope_vbw": ("icom_lan.commands", "get_scope_vbw"),
    "scope_data_output_off": ("icom_lan.commands", "scope_data_output_off"),
    "scope_data_output_on": ("icom_lan.commands", "scope_data_output_on"),
    "scope_main_sub": ("icom_lan.commands", "scope_main_sub"),
    "scope_off": ("icom_lan.commands", "scope_off"),
    "scope_on": ("icom_lan.commands", "scope_on"),
    "scope_set_center_type": ("icom_lan.commands", "scope_set_center_type"),
    "scope_set_during_tx": ("icom_lan.commands", "scope_set_during_tx"),
    "scope_set_edge": ("icom_lan.commands", "scope_set_edge"),
    "scope_set_fixed_edge": ("icom_lan.commands", "scope_set_fixed_edge"),
    "scope_set_hold": ("icom_lan.commands", "scope_set_hold"),
    "scope_set_mode": ("icom_lan.commands", "scope_set_mode"),
    "scope_set_rbw": ("icom_lan.commands", "scope_set_rbw"),
    "scope_set_ref": ("icom_lan.commands", "scope_set_ref"),
    "scope_set_span": ("icom_lan.commands", "scope_set_span"),
    "scope_set_speed": ("icom_lan.commands", "scope_set_speed"),
    "scope_set_vbw": ("icom_lan.commands", "scope_set_vbw"),
    "scope_single_dual": ("icom_lan.commands", "scope_single_dual"),
    # --- Scope assembler / frames ---
    "ScopeAssembler": ("icom_lan.scope", "ScopeAssembler"),
    "ScopeFrame": ("icom_lan.scope", "ScopeFrame"),
    # --- Scope rendering (optional Pillow dep — handled by ImportError below) ---
    "SCOPE_THEMES": ("icom_lan.scope.render", "THEMES"),
    "amplitude_to_color": ("icom_lan.scope.render", "amplitude_to_color"),
    "render_scope_image": ("icom_lan.scope.render", "render_scope_image"),
    "render_spectrum": ("icom_lan.scope.render", "render_spectrum"),
    "render_waterfall": ("icom_lan.scope.render", "render_waterfall"),
    # --- Public types not in tier-1 ---
    "HEADER_SIZE": ("icom_lan.core.types", "HEADER_SIZE"),
    "AudioCapabilities": ("icom_lan.core.types", "AudioCapabilities"),
    "CivFrame": ("icom_lan.core.types", "CivFrame"),
    "PacketHeader": ("icom_lan.core.types", "PacketHeader"),
    "PacketType": ("icom_lan.core.types", "PacketType"),
    "ScopeCompletionPolicy": ("icom_lan.core.types", "ScopeCompletionPolicy"),
    "ScopeFixedEdge": ("icom_lan.core.types", "ScopeFixedEdge"),
    "bcd_decode": ("icom_lan.core.types", "bcd_decode"),
    "bcd_encode": ("icom_lan.core.types", "bcd_encode"),
    "get_audio_capabilities": ("icom_lan.core.types", "get_audio_capabilities"),
}


def __getattr__(name: str) -> Any:
    """:pep:`562` lazy attribute hook for tier-2 names.

    Resolves tier-2 names on first access, caches them in ``globals()``
    so subsequent lookups skip this hook entirely. Returns :class:`~typing.Any`
    so downstream typecheckers (mypy, pyright) keep the original symbol
    types when a consumer does ``from icom_lan import IcomRadio``.
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
    """Include lazy tier-2 names in ``dir(icom_lan)`` for IDE/REPL discovery."""
    return sorted({*globals().keys(), *_LAZY_MAP.keys()})


# Public API surface — tier-1 (semver-stable) + tier-2 (best-effort lazy).
__all__ = [
    "__version__",
    # --- Tier 1: Factory ---
    "create_radio",
    # --- Tier 1: Backend configs ---
    "BackendConfig",
    "LanBackendConfig",
    "SerialBackendConfig",
    "YaesuCatBackendConfig",
    # --- Tier 1: Radio + capability protocols ---
    "Radio",
    "AdvancedControlCapable",
    "AntennaControlCapable",
    "AudioCapable",
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
    "RitXitCapable",
    "ScopeCapable",
    "SplitCapable",
    "StateCacheCapable",
    "StateNotifyCapable",
    "SystemControlCapable",
    "TransceiverBankCapable",
    "TransceiverStatusCapable",
    "VfoSlotCapable",
    "VoiceControlCapable",
    # --- Tier 1: Exceptions ---
    "IcomLanError",
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
    # --- Tier 1: Public state types ---
    "RadioState",
    "RadioProfile",
    "VfoSlotState",
    "YaesuStateExtension",
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
]
