"""Custom exception hierarchy for rigplane."""

__all__ = [
    "RigplaneError",
    "ConnectionError",
    "RadioSessionHeldError",
    "AuthenticationError",
    "CommandError",
    "BackgroundSendDropped",
    "CommandRejectedError",
    "ModeReadbackError",
    "TimeoutError",
    "DiscoveryTimeout",
    "AudioError",
    "AudioCodecBackendError",
    "AudioFormatError",
    "AudioTranscodeError",
]


class RigplaneError(Exception):
    """Base exception for all rigplane errors."""


class ConnectionError(RigplaneError):
    """Raised when a connection to the radio fails or is lost."""


class RadioSessionHeldError(ConnectionError):
    """Raised only by the start-up live-holder stop in
    ``CoreRadioSessionLifecycle._first_connect_busy_pass``.

    A subclass of :class:`ConnectionError`, so every existing ``except``
    still catches it; the CLI maps it to exit code 3 (a deliberate stop,
    not a crash).
    """


class AuthenticationError(RigplaneError):
    """Raised when authentication with the radio fails."""


class CommandError(RigplaneError):
    """Raised when a CI-V command fails or returns an error."""


class BackgroundSendDropped(CommandError):
    """A fire-and-forget background send was dropped at the commander cap.

    Not a link failure: the cap is doing its job, and the caller re-queues
    the request at its normal cadence.
    """


class CommandRejectedError(CommandError):
    """The radio positively refused a command (e.g. a Yaesu ``?;`` response)."""


class ModeReadbackError(CommandError):
    """A mode write completed but its readback did not reach the target."""

    def __init__(self, requested_mode: str, observed_mode: str) -> None:
        super().__init__(
            f"Mode readback mismatch: requested {requested_mode!r}, "
            f"observed {observed_mode!r}"
        )
        self.requested_mode = requested_mode
        self.observed_mode = observed_mode


class TimeoutError(RigplaneError):
    """Raised when an operation times out."""


class DiscoveryTimeout(TimeoutError):
    """Discovery got no answer.

    Raised by ``IcomTransport._discover`` when the radio answers none of
    the "Are You There" probes.  A subclass of :class:`TimeoutError`, so
    every existing ``except TimeoutError`` handler and message assertion
    behaves exactly as before; the start-up busy loop catches exactly
    this subclass without parsing the message.
    """


class AudioError(RigplaneError):
    """Base exception for audio codec/transcoding failures."""


class AudioCodecBackendError(AudioError):
    """Raised when the Opus backend is unavailable."""


class AudioFormatError(AudioError):
    """Raised when PCM/Opus input format is invalid or unsupported."""


class AudioTranscodeError(AudioError):
    """Raised when PCM/Opus encode/decode operation fails."""
