"""Custom exception hierarchy for rigplane."""

__all__ = [
    "RigplaneError",
    "ConnectionError",
    "AuthenticationError",
    "CommandError",
    "CivNakError",
    "TimeoutError",
    "AudioError",
    "AudioCodecBackendError",
    "AudioFormatError",
    "AudioTranscodeError",
]


class RigplaneError(Exception):
    """Base exception for all rigplane errors."""


class ConnectionError(RigplaneError):
    """Raised when a connection to the radio fails or is lost."""


class AuthenticationError(RigplaneError):
    """Raised when authentication with the radio fails."""


class CommandError(RigplaneError):
    """Raised when a CI-V command fails or returns an error."""


class CivNakError(CommandError):
    """Raised when the radio explicitly declines a CI-V read with NAK.

    Distinct from :class:`TimeoutError`, which means no reply arrived at
    all: a :class:`CivNakError` means a reply did arrive, and it was a
    refusal (CI-V command ``0xFA``).
    """


class TimeoutError(RigplaneError):
    """Raised when an operation times out."""


class AudioError(RigplaneError):
    """Base exception for audio codec/transcoding failures."""


class AudioCodecBackendError(AudioError):
    """Raised when the Opus backend is unavailable."""


class AudioFormatError(AudioError):
    """Raised when PCM/Opus input format is invalid or unsupported."""


class AudioTranscodeError(AudioError):
    """Raised when PCM/Opus encode/decode operation fails."""
