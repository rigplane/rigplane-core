"""P22: a Windows access-denied audio open gets its own failure category.

PortAudio/sounddevice reports a privacy or permission refusal with
E_ACCESSDENIED — HRESULT 0x80070005, signed -2147024891 or unsigned
2147942405 — in the error's integer codes (``args`` or ``errno``), never
in matchable locale text. The one shared classifier
(:func:`audio_start_failure_details`) maps both code forms to
``device_access_denied`` on every radio alike: the code decides, never
the device name, so there is no per-radio branch. A non-access-denied
error keeps its old reason.

A fake ``sounddevice``-shaped error stands in for PortAudio: no audio
device, no Windows.
"""

from __future__ import annotations

import pytest

from rigplane.audio.backend import AudioDeviceBusyError
from rigplane.audio.usb_driver import (
    AudioCaptureOpenTimeoutError,
    AudioDeviceAmbiguousError,
    AudioDeviceSelectionError,
    AudioFormatUnsupportedError,
    AudioTopologyUnavailableError,
    audio_start_failure_access_code,
    audio_start_failure_details,
    audio_start_failure_holder,
    audio_start_failure_reason,
)


class FakePortAudioError(Exception):
    """The shape sounddevice's PortAudioError has: (message, code[, host error])."""


SIGNED = -2147024891
UNSIGNED = 2147942405  # 0x80070005


def _denied(code: int, message: str) -> FakePortAudioError:
    """The real Windows shape: PaErrorCode -9999 plus the WASAPI host triple."""
    return FakePortAudioError(
        message,
        -9999,
        ("Windows WASAPI", code, "Access is denied. (0x80070005)"),
    )


# Device labels from several radios' identities: the code classifies, the
# name never does. The Finnish rename pins the MOR-3710 shape.
RADIO_LABELS = [
    "Microphone (USB Audio CODEC)",  # IC-7300 default endpoint name
    "Microphone (2- USB Audio CODEC)",  # IC-7610 W3 duplicate-prefix shape
    "Mikrofoni (USB Audio CODEC)",  # localized rename, same endpoint
]


@pytest.mark.parametrize("code", [SIGNED, UNSIGNED], ids=["signed", "unsigned"])
@pytest.mark.parametrize("label", RADIO_LABELS)
def test_both_denied_code_forms_map_to_access_denied_on_every_radio(code, label):
    error = _denied(code, f"Error opening InputStream: {label} [PaErrorCode -9999]")
    reason, candidates = audio_start_failure_details(error)
    assert reason == "device_access_denied"
    assert reason != "other"
    assert reason != "device_in_use"
    assert candidates == ()
    assert audio_start_failure_holder(error) is None
    assert audio_start_failure_access_code(error) == code


def test_denied_code_with_unrelated_message_text_still_maps_by_code():
    """No locale text list is consulted: a message with none of the English
    markers maps the same way, by its code alone."""
    error = _denied(SIGNED, "Error opening InputStream: Unanticipated host error")
    assert audio_start_failure_reason(error) == "device_access_denied"


def test_denied_errno_maps_to_access_denied():
    error = OSError(SIGNED, "Permission denied")
    assert error.errno == SIGNED
    assert audio_start_failure_reason(error) == "device_access_denied"


def test_denied_code_in_the_chain_maps_to_access_denied():
    """Wrapping never hides the OS refusal: the chain is scanned, not just
    the outermost error."""
    outer = RuntimeError("shared open failed")
    outer.__cause__ = _denied(
        UNSIGNED, "Error opening InputStream: Unanticipated host error"
    )
    assert audio_start_failure_reason(outer) == "device_access_denied"


@pytest.mark.parametrize(
    "error,reason",
    [
        (AudioCaptureOpenTimeoutError("open timed out"), "open_timeout"),
        (AudioFormatUnsupportedError("no supported rate"), "format"),
        (
            AudioDeviceBusyError("held by another program", device_index=3),
            "device_in_use",
        ),
        (AudioDeviceAmbiguousError("several match"), "usb_ambiguous"),
        (AudioTopologyUnavailableError("no topology"), "usb_topology_unavailable"),
        (AudioDeviceSelectionError("no suitable device"), "other"),
        (FakePortAudioError("Error opening InputStream", -9998), "other"),
        (OSError("Error opening stream: shared refused"), "other"),
        (
            FakePortAudioError("Error opening InputStream: Invalid device", -9996),
            "other",
        ),
    ],
    ids=[
        "open-timeout",
        "format",
        "busy-keeps-device-in-use",
        "ambiguous",
        "topology-unavailable",
        "selection",
        "channels",
        "plain-oserror",
        "invalid-device-without-a-holder",
    ],
)
def test_non_denied_errors_keep_their_old_reason(error, reason):
    assert audio_start_failure_details(error) == (reason, ())
    assert audio_start_failure_access_code(error) is None


def test_the_category_is_one_of_the_publishable_reasons():
    from typing import get_args

    from rigplane.audio.usb_driver import AudioStartFailureReason

    assert "device_access_denied" in get_args(AudioStartFailureReason)
