"""Shared serial control-link I/O evidence."""

from __future__ import annotations

from collections.abc import Callable

from .exceptions import CommandError


class SerialResponse(bytes):
    """Serial bytes carrying deferred, session-bound recovery credit."""

    record_response: Callable[[], None]

    def __new__(
        cls, data: bytes, record_response: Callable[[], None]
    ) -> SerialResponse:
        response = super().__new__(cls, data)
        response.record_response = record_response
        return response


def require_keying_control(health: SerialLinkHealth | None) -> None:
    """Refuse new KEY admission on a latched serial link failure."""
    if health is not None and (
        health.io_failed or getattr(health, "no_reply", False) is True
    ):
        raise CommandError("Keying requires control connectivity")


class SerialLinkHealth:
    """Keep an I/O failure visible until a reopen or a radio response.

    A successful local write alone cannot prove that the radio is reachable.
    Port ownership remains with each transport's connection lifecycle.
    """

    def __init__(self) -> None:
        self.io_failed = False
        self.generation = 0

    def record_failure(self) -> None:
        self.generation += 1
        self.io_failed = True

    def record_response(self) -> None:
        self.io_failed = False

    def record_open(self) -> None:
        self.generation += 1
        self.io_failed = False
