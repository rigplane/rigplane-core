"""Shared serial control-link I/O evidence."""

from __future__ import annotations

from collections.abc import Callable


class SerialResponse(bytes):
    """Serial bytes carrying deferred, session-bound recovery credit."""

    record_response: Callable[[], None]

    def __new__(
        cls, data: bytes, record_response: Callable[[], None]
    ) -> SerialResponse:
        response = super().__new__(cls, data)
        response.record_response = record_response
        return response


class SerialLinkHealth:
    """Shared serial I/O failure and unanswered-poll evidence."""

    TIMEOUT_THRESHOLD = 3

    def __init__(self) -> None:
        self.io_failed = False
        self.generation = 0
        self.no_reply = False
        self.consecutive_timeouts = 0
        self.silence_started: float | None = None
        self.no_reply_note = ""

    @property
    def responding(self) -> bool:
        return not self.io_failed and not self.no_reply

    @staticmethod
    def silence_limit_s(answer_window: float, watchdog_interval: float) -> float:
        return 2.0 * answer_window + watchdog_interval

    def reset_poll_evidence(self) -> None:
        self.consecutive_timeouts = 0
        self.silence_started = None

    def observe_poll(
        self,
        *,
        now: float,
        pending: bool,
        timeout_delta: int = 0,
        received: bool = False,
        answer_window: float,
        watchdog_interval: float,
        timeout_threshold: int = TIMEOUT_THRESHOLD,
    ) -> bool:
        """Apply the serial timeout/outstanding-poll rule (MOR-2861)."""
        if received:
            self.reset_poll_evidence()
        else:
            self.consecutive_timeouts += timeout_delta
            if pending and self.silence_started is None:
                self.silence_started = now
            elif not pending and timeout_delta == 0:
                self.silence_started = None
        crossed = False
        if self.consecutive_timeouts >= timeout_threshold:
            self.no_reply_note = (
                f"{self.consecutive_timeouts} consecutive command timeout(s) "
                "with no response"
            )
            crossed = True
        if self.silence_started is not None:
            elapsed = now - self.silence_started
            limit_s = self.silence_limit_s(answer_window, watchdog_interval)
            if elapsed >= limit_s:
                self.no_reply_note = (
                    "polls outstanding and no CI-V frame parsed for "
                    f"{elapsed:.1f}s (silence limit {limit_s:.1f}s)"
                )
                crossed = True
        if crossed and not self.no_reply:
            self.generation += 1
            self.no_reply = True
        return crossed

    def record_failure(self) -> None:
        self.generation += 1
        self.io_failed = True

    def record_response(self) -> None:
        self.io_failed = False
        self.no_reply = False
        self.reset_poll_evidence()

    def end_session(self) -> None:
        self.generation += 1
        self.record_response()
        self.no_reply_note = ""

    def record_open(self) -> None:
        """Reset I/O failure; no-reply recovery still needs a radio response."""
        self.generation += 1
        self.io_failed = False
        self.reset_poll_evidence()
