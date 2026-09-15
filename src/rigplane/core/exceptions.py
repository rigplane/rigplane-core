"""Custom exception hierarchy for rigplane."""

__all__ = [
    "RigplaneError",
    "ConnectionError",
    "AuthenticationError",
    "CommandError",
    "CommandRejectedError",
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


class CommandRejectedError(CommandError):
    """Raised when the radio positively refused a command (e.g. a Yaesu
    ``?;`` response), as opposed to a locally-detected encoding error, a
    missing command template, or any other :class:`CommandError` that never
    reached the radio at all."""


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
