"""Backward-compatible re-export — driver moved to icom_lan.audio.usb_driver.

All public symbols are re-exported so existing imports continue to work.
New code should import from :mod:`icom_lan.audio` directly.
"""

from icom_lan.audio.usb_driver import (  # noqa: F401
    AudioDeviceSelectionError,
    AudioDriverLifecycleError,
    UsbAudioDevice,
    UsbAudioDriver,
    list_usb_audio_devices,
    select_usb_audio_devices,
)

__all__ = [
    "AudioDeviceSelectionError",
    "AudioDriverLifecycleError",
    "UsbAudioDevice",
    "UsbAudioDriver",
    "list_usb_audio_devices",
    "select_usb_audio_devices",
]
