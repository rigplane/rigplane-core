"""Public package export tests."""

import rigplane
from rigplane import AudioCodecBackendError, AudioFormatError, ScopeCompletionPolicy


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
        "TransceiverStatusCapable",
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
    }
    assert set(rigplane.__all__) == expected_public


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
        assert hasattr(rigplane, name), (
            f"{name} should still be importable from rigplane"
        )
        assert name not in rigplane.__all__, f"{name} should NOT be in __all__"


def test_component_state_types_not_top_level() -> None:
    """Pin the docs/api/public-api-surface.md claim about component state types.

    ReceiverState and ScopeControlsState are components of RadioState and
    importable from rigplane.core.radio_state. They are not re-exported from
    the top-level package: the Form F tier-1 sealing (#1200) promoted
    RadioState/VfoSlotState/YaesuStateExtension from this module and not
    these two, and no commit has ever exported them.
    """
    from rigplane.core.radio_state import ReceiverState, ScopeControlsState

    assert not hasattr(rigplane, "ReceiverState")
    assert not hasattr(rigplane, "ScopeControlsState")
    state = rigplane.RadioState()
    assert isinstance(state.main, ReceiverState)
    assert isinstance(state.sub, ReceiverState)
    assert isinstance(state.scope_controls, ScopeControlsState)
