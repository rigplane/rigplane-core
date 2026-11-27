"""Custom exception hierarchy for rigplane."""

__all__ = [
    "RigplaneError",
    "ConnectionError",
    "RadioSessionHeldError",
    "StaleBeforeWireError",
    "AuthenticationError",
    "CommandError",
    "CatReplyValidationError",
    "BackgroundSendDropped",
    "CommandRejectedError",
    "ModeReadbackError",
    "ReceiverNotSelectedError",
    "TimeoutError",
    "RadioNotReadyError",
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


class StaleBeforeWireError(ConnectionError):
    """A CI-V command was refused before its frame was written to the wire.

    Raised by ``IcomCommander.send`` when not started and ``stop`` for
    queued commands; by ``CivRuntime._send_civ_raw`` on failed transport
    recovery; and by ``_execute_civ_raw`` / ``_send_civ_frame_now`` for
    missing transport or pre-send session, port, observer and currency
    refusals. This includes the transport's ``is_current`` guard raising
    ``CommandError`` before writing inside ``send_tracked``.

    It remains a :class:`ConnectionError`. Split-mode read-modify-write
    (``DualRxRuntimeMixin.write_relative_vfo_mode``, MOR-3743) retries it
    only when its scope also reports interference, at most three total
    attempts with no retry delay. Commander or recovery failure alone
    propagates on the first attempt. Post-send currency failures and
    transport failures that may have written keep their plain types.
    """


class AuthenticationError(RigplaneError):
    """Raised when authentication with the radio fails."""


class CommandError(RigplaneError):
    """Raised when a CI-V command fails or returns an error."""


class CatReplyValidationError(CommandError):
    """A CAT reply that parsed but is semantically invalid.

    The radio answered with a well-formed frame whose content cannot be
    true: an enum code outside the manual's domain, a receiver digit that
    does not match the addressed receiver, a tone index outside the
    profile's resolved catalog. The wire works and the radio works — this
    one answer is unusable, so the field is marked unavailable with a
    reason while its siblings keep publishing.

    A subclass of :class:`CommandError`, so every existing ``except
    CommandError`` handler still catches it unchanged; only readers of the
    field-level observation contract distinguish it. It is deliberately
    NOT raised for transport or connection failures (those are
    ``ConnectionError``/``CatTransportError`` and must still propagate to
    the poller's reconnect path) nor for programming/config errors
    (missing profile spec, out-of-range write argument), which stay plain
    ``CommandError`` and stay loud.
    """


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


class ReceiverNotSelectedError(CommandError):
    """A receiver-scoped command was refused: its receiver is not selected.

    On a dual-receiver radio a command without a per-receiver address
    (no cmd29 route, no profile-declared direct selector) reaches only the
    receiver the operator has selected. Core never switches the selection
    behind the operator's back, so a command for a receiver that is known
    not to be the selected one is refused (MOR-3591). While the selection
    is merely not known yet (never established in this CI-V generation),
    the answer is the retriable :class:`RadioNotReadyError` instead.
    ``reason_code`` is the stable machine-readable refusal reason carried
    by the WebSocket ``error`` field and the HTTP error envelope.
    """

    reason_code = "receiver_not_selected"


class TimeoutError(RigplaneError):
    """Raised when an operation times out."""


class RadioNotReadyError(TimeoutError):
    """A command cannot be answered yet: the radio has not answered CI-V
    since the link was established, or it answered while its receiver
    selection is still unknown (MOR-4382 follow-up).

    A ``connect()`` whose radio answers nothing keeps the link up; the
    receiver selection stays unknown and ``radio_ready`` stays False until
    the first answer. A receiver-scoped command in that window raises this
    instead of claiming a receiver is not selected. The same holds once
    the radio HAS answered but its 0x07 0xD2 selection read is still
    unanswered (the connect tail catching up, or the bounded on-demand
    read timing out behind a busy link): ``radio_ready`` can already be
    True while no receiver can be addressed, and the answer stays this
    retriable error. A subclass of :class:`TimeoutError`, so every
    existing handler keeps treating it as retriable: rigctld's
    ``RPRT -5`` (ETIMEOUT), the web ``command_timeout`` envelope, the
    poller's timeout backoff. Once the selection is known and the
    receiver is not it, the genuine :class:`ReceiverNotSelectedError`
    (MOR-3591) is raised.
    """


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
