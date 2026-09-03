"""Contract test: every public name in rigplane's Tier 1 + Tier 2 lazy
API must resolve via PEP 562 ``__getattr__``.

This test is a hard acceptance gate during the modularization effort
(see ``docs/plans/2026-04-29-modularization-plan.md`` §6 R1). It also
serves as a permanent guard against accidental public API removal.

The name lists below are intentionally hardcoded — do **NOT** compute
them from ``rigplane._LAZY_MAP`` or ``rigplane.audio._LAZY_MAP``. The
point of the test is to fail loudly when a name disappears, not to
reflect the current state of the lazy map.

Source of truth: ``docs/plans/discovery-artifacts/init-snapshot.md``.
"""

# Tier 1 — eager-imported, semver-stable surface.
# Every name above the ``# === Tier 2 — lazy`` divider in
# ``src/rigplane/__init__.py``'s ``__all__``.
TIER1_NAMES = [
    "__version__",
    # --- Factory ---
    "create_radio",
    # --- Backend configs ---
    "BackendConfig",
    "LanBackendConfig",
    "SerialBackendConfig",
    "YaesuCatBackendConfig",
    # --- Radio + capability protocols ---
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
    # --- Exceptions ---
    "RigplaneError",
    "ConnectionError",
    "AuthenticationError",
    "CommandError",
    "TimeoutError",
    "AudioError",
    "AudioCodecBackendError",
    "AudioFormatError",
    "AudioTranscodeError",
    # --- Public types/enums ---
    "Mode",
    "AudioCodec",
    "BreakInMode",
    "RepeaterShiftDirection",
    # --- Public state types ---
    "RadioState",
    "RadioProfile",
    "VfoSlotState",
    "YaesuStateExtension",
]

# Tier 2 — lazy-loaded via PEP 562 ``__getattr__``.
# Every name below the ``# === Tier 2 — lazy`` divider in
# ``src/rigplane/__init__.py``'s ``__all__``.
TIER2_LAZY_NAMES = [
    # --- Backward-compat radio facade ---
    "IcomRadio",
    # --- Commander internals ---
    "IcomCommander",
    "Priority",
    # --- Audio primitives ---
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

# ``rigplane.audio`` — every key of its ``_LAZY_MAP``.
AUDIO_LAZY_NAMES = [
    # Audio backend abstraction
    "AudioBackend",
    "AudioDeviceId",
    "AudioDeviceInfo",
    "FakeAudioBackend",
    "FakeRxStream",
    "FakeTxStream",
    "PortAudioBackend",
    "RxStream",
    "TxStream",
    # Configuration
    "AudioConfig",
    "load_audio_config",
    "save_audio_config",
    # DSP pipeline
    "DspPipeline",
    "DspStage",
    "Limiter",
    "NoiseGate",
    "RmsNormalizer",
    # Resampling
    "PcmResampler",
    "SampleRateNegotiation",
    "negotiate_sample_rate",
    # USB audio driver
    "AudioDeviceSelectionError",
    "AudioDriverLifecycleError",
    "UsbAudioDevice",
    "UsbAudioDriver",
    "list_usb_audio_devices",
    "select_usb_audio_devices",
]


def test_tier1_names_resolve() -> None:
    """Every Tier 1 name must be a real attribute of ``rigplane``."""
    import rigplane

    for name in TIER1_NAMES:
        assert hasattr(rigplane, name), (
            f"Tier 1 public API regression: rigplane.{name} no longer "
            f"resolves. This is a breaking change to the public API. "
            f"Check the migration plan and re-export shims."
        )


def test_tier2_lazy_names_resolve() -> None:
    """Every Tier 2 name must resolve through PEP 562 ``__getattr__``."""
    import rigplane

    for name in TIER2_LAZY_NAMES:
        assert hasattr(rigplane, name), (
            f"Tier 2 lazy resolution regression: rigplane.{name} "
            f"failed to resolve via __getattr__. Either the LAZY_MAP "
            f"target is wrong or the canonical module is missing."
        )


def test_audio_lazy_names_resolve() -> None:
    """Every ``rigplane.audio`` lazy name must resolve via its ``__getattr__``."""
    import rigplane.audio

    for name in AUDIO_LAZY_NAMES:
        assert hasattr(rigplane.audio, name), (
            f"rigplane.audio.{name} failed to resolve. Check audio "
            f"package _LAZY_MAP and re-export shims."
        )
