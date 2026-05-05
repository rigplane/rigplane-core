"""Custom exception hierarchy for icom-lan."""

__all__ = [
    "IcomLanError",
    "ConnectionError",
    "AuthenticationError",
    "CommandError",
    "TimeoutError",
    "AudioError",
    "AudioCodecBackendError",
    "AudioFormatError",
    "AudioTranscodeError",
]


class IcomLanError(Exception):
    """Base exception for all icom-lan errors."""


class ConnectionError(IcomLanError):
    """Raised when a connection to the radio fails or is lost."""


class AuthenticationError(IcomLanError):
    """Raised when authentication with the radio fails."""


class CommandError(IcomLanError):
    """Raised when a CI-V command fails or returns an error."""


class TimeoutError(IcomLanError):
    """Raised when an operation times out."""


class AudioError(IcomLanError):
    """Base exception for audio codec/transcoding failures."""


class AudioCodecBackendError(AudioError):
    """Raised when the Opus backend is unavailable."""


class AudioFormatError(AudioError):
    """Raised when PCM/Opus input format is invalid or unsupported."""


class AudioTranscodeError(AudioError):
    """Raised when PCM/Opus encode/decode operation fails."""
