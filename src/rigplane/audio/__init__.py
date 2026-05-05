"""Universal audio subsystem for icom-lan.

Provides:
- LAN audio streaming (IC-7610 UDP) — from :mod:`.lan_stream`
- USB audio device driver (all serial radios) — from :mod:`.usb_driver`

Eager block is intentionally limited to :mod:`.lan_stream` symbols. Heavier
abstractions (audio backends, DSP, USB driver, resampler) are loaded lazily
via :pep:`562` ``__getattr__`` so consumers that only touch
``AudioPacket``/``AudioStream`` (the wire-protocol types used by
:mod:`icom_lan.radio` and :mod:`icom_lan.transport`) don't drag in
PortAudio, ``numpy``-backed DSP, or platform USB plumbing.

Direct submodule imports (``from icom_lan.audio.backend import ...``)
remain the canonical path for callers that want the heavier abstractions.
"""

from typing import Any

# LAN audio (eager — used by transport / radio / sync at import time)
from .lan_stream import (  # noqa: F401
    AUDIO_HEADER_SIZE,
    MAX_AUDIO_PAYLOAD,
    RX_IDENT_0xA0,
    TX_IDENT,
    AudioPacket,
    AudioState,
    AudioStats,
    AudioStream,
    JitterBuffer,
    build_audio_packet,
    parse_audio_packet,
)

# Lazy submodule attribute map: name -> (module, attribute).
# Resolved on first access via PEP 562 ``__getattr__`` and cached in
# ``globals()`` so subsequent lookups skip this hook.
_LAZY_MAP: dict[str, tuple[str, str]] = {
    # Audio backend abstraction (protocol + implementations)
    "AudioBackend": ("icom_lan.audio.backend", "AudioBackend"),
    "AudioDeviceId": ("icom_lan.audio.backend", "AudioDeviceId"),
    "AudioDeviceInfo": ("icom_lan.audio.backend", "AudioDeviceInfo"),
    "FakeAudioBackend": ("icom_lan.audio.backend", "FakeAudioBackend"),
    "FakeRxStream": ("icom_lan.audio.backend", "FakeRxStream"),
    "FakeTxStream": ("icom_lan.audio.backend", "FakeTxStream"),
    "PortAudioBackend": ("icom_lan.audio.backend", "PortAudioBackend"),
    "RxStream": ("icom_lan.audio.backend", "RxStream"),
    "TxStream": ("icom_lan.audio.backend", "TxStream"),
    # Configuration
    "AudioConfig": ("icom_lan.audio.config", "AudioConfig"),
    "load_audio_config": ("icom_lan.audio.config", "load_audio_config"),
    "save_audio_config": ("icom_lan.audio.config", "save_audio_config"),
    # DSP pipeline
    "DspPipeline": ("icom_lan.audio.dsp", "DspPipeline"),
    "DspStage": ("icom_lan.audio.dsp", "DspStage"),
    "Limiter": ("icom_lan.audio.dsp", "Limiter"),
    "NoiseGate": ("icom_lan.audio.dsp", "NoiseGate"),
    "RmsNormalizer": ("icom_lan.audio.dsp", "RmsNormalizer"),
    # Resampling
    "PcmResampler": ("icom_lan.audio.resample", "PcmResampler"),
    "SampleRateNegotiation": (
        "icom_lan.audio.resample",
        "SampleRateNegotiation",
    ),
    "negotiate_sample_rate": (
        "icom_lan.audio.resample",
        "negotiate_sample_rate",
    ),
    # USB audio driver
    "AudioDeviceSelectionError": (
        "icom_lan.audio.usb_driver",
        "AudioDeviceSelectionError",
    ),
    "AudioDriverLifecycleError": (
        "icom_lan.audio.usb_driver",
        "AudioDriverLifecycleError",
    ),
    "UsbAudioDevice": ("icom_lan.audio.usb_driver", "UsbAudioDevice"),
    "UsbAudioDriver": ("icom_lan.audio.usb_driver", "UsbAudioDriver"),
    "list_usb_audio_devices": (
        "icom_lan.audio.usb_driver",
        "list_usb_audio_devices",
    ),
    "select_usb_audio_devices": (
        "icom_lan.audio.usb_driver",
        "select_usb_audio_devices",
    ),
}


def __getattr__(name: str) -> Any:
    """:pep:`562` lazy hook for heavier audio abstractions."""
    target = _LAZY_MAP.get(name)
    if target is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    module_name, attr_name = target
    import importlib

    module = importlib.import_module(module_name)
    attr = getattr(module, attr_name)
    globals()[name] = attr  # cache for subsequent access
    return attr


def __dir__() -> list[str]:
    return sorted({*globals().keys(), *_LAZY_MAP.keys()})


__all__ = [
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
    # LAN audio
    "AUDIO_HEADER_SIZE",
    "AudioPacket",
    "AudioState",
    "AudioStats",
    "AudioStream",
    "JitterBuffer",
    "MAX_AUDIO_PAYLOAD",
    "RX_IDENT_0xA0",
    "build_audio_packet",
    "parse_audio_packet",
    "TX_IDENT",
    # DSP
    "DspPipeline",
    "DspStage",
    "Limiter",
    "NoiseGate",
    "RmsNormalizer",
    # Resampling
    "PcmResampler",
    "SampleRateNegotiation",
    "negotiate_sample_rate",
    # USB audio
    "AudioDeviceSelectionError",
    "AudioDriverLifecycleError",
    "UsbAudioDevice",
    "UsbAudioDriver",
    "list_usb_audio_devices",
    "select_usb_audio_devices",
]
