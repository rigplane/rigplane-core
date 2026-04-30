"""Public package export tests."""

import icom_lan
from icom_lan import AudioCodecBackendError, AudioFormatError, ScopeCompletionPolicy


def test_scope_completion_policy_exported() -> None:
    assert ScopeCompletionPolicy.VERIFY.value == "verify"


def test_audio_errors_exported() -> None:
    assert issubclass(AudioCodecBackendError, Exception)
    assert issubclass(AudioFormatError, Exception)


def test_public_api_surface() -> None:
    """__all__ contains tier-1 (eager) + tier-2 (lazy) public surface.

    See :pep:`562` and Form F sealing (epic #1193) for the tier policy.
    """
    expected_public = {
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
        "StatePollable",
        "StatePoller",
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
    }
    assert set(icom_lan.__all__) == expected_public


def test_internal_symbols_still_importable() -> None:
    """Internal symbols absent from __all__ are still importable by name.

    These are tier-2 lazy targets (resolved via PEP 562 ``__getattr__``)
    that we don't promote to the documented public surface. They remain
    accessible for backward compatibility — ``hasattr`` triggers the
    lazy hook and resolves the underlying symbol.
    """
    internal_symbols = [
        "build_civ_frame",
        "parse_civ_frame",
        "bcd_encode",
        "bcd_decode",
        "IC_7610_ADDR",
        "CONTROLLER_ADDR",
        "RECEIVER_MAIN",
        "RECEIVER_SUB",
        "IcomTransport",
        "ScopeAssembler",
        "ScopeFrame",
        "PacketHeader",
        "PacketType",
        "CivFrame",
        "AudioCapabilities",
        "get_audio_capabilities",
        "ScopeCompletionPolicy",
        "ScopeFixedEdge",
        "HEADER_SIZE",
    ]
    for name in internal_symbols:
        assert hasattr(icom_lan, name), (
            f"{name} should still be importable from icom_lan"
        )
        assert name not in icom_lan.__all__, f"{name} should NOT be in __all__"
