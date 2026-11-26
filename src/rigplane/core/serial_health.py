"""Shared serial control-link I/O evidence."""

from __future__ import annotations


class SerialLinkHealth:
    """Keep an I/O failure visible until a reopen or a radio response.

    A successful local write alone cannot prove that the radio is reachable.
    Port ownership remains with each transport's connection lifecycle.
    """

    def __init__(self) -> None:
        self.io_failed = False

    def record_failure(self) -> None:
        self.io_failed = True

    def record_response(self) -> None:
        self.io_failed = False

    def record_open(self) -> None:
        self.io_failed = False
