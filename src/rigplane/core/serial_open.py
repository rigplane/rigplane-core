"""Shared serial-port opener that keeps the modem control lines deasserted.

pyserial defaults to ``dtr=True, rts=True`` when a port is opened. On a
radio whose USB SEND/KEY input is wired to DTR or RTS (e.g. Icom SET ->
Connectors -> USB SEND), asserting either line keys the transmitter, so
every serial open in rigplane routes through :func:`open_serial_port`.

The helper applies both halves of the mitigation: it requests both lines
inactive at open time (kwargs forwarded to pyserial's ``serial_for_url``)
and deasserts them again on the live pyserial object afterwards
(``writer.transport.serial``). Which half is strictly necessary is
unestablished — the kernel may assert the lines at open before userspace
clears them — and is checked on the bench per MOR-2228.
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from typing import Any

logger = logging.getLogger(__name__)

SerialOpener = Callable[..., Awaitable[tuple[Any, Any]]]


async def open_serial_port(
    url: str,
    *,
    baudrate: int,
    opener: SerialOpener | None = None,
    **kwargs: Any,
) -> tuple[Any, Any]:
    """Open *url* via ``serial_asyncio`` with DTR and RTS held inactive.

    Args:
        url: Serial device path (e.g. ``/dev/ttyUSB0``).
        baudrate: Baud rate.
        opener: Replacement for ``serial_asyncio.open_serial_connection``
            (used in tests). When ``None`` the real opener is resolved.
        kwargs: Extra pyserial parameters (``bytesize``, ``parity``, …).

    Returns:
        The ``(reader, writer)`` pair from the underlying opener.
    """
    if opener is None:
        opener = _default_opener()
    reader, writer = await opener(
        url=url, baudrate=baudrate, dtr=False, rts=False, **kwargs
    )
    _deassert_control_lines(writer, url)
    return reader, writer


def _default_opener() -> SerialOpener:
    """Return the real serial_asyncio opener, or raise ImportError with hint."""
    from rigplane.core._optional_deps import _require_pyserial_asyncio

    _require_pyserial_asyncio()
    import serial_asyncio  # type: ignore[import-untyped]

    return serial_asyncio.open_serial_connection  # type: ignore[no-any-return]


def _deassert_control_lines(writer: Any, port: str) -> None:
    """Drive DTR and RTS low on the pyserial object behind *writer*.

    A writer without ``.transport.serial`` (every fake opener in tests) is
    a normal case, not an error. A port that refuses a control-line write
    (some virtual and Bluetooth ports do) must still be usable: the
    failure is swallowed and logged at debug level with the port name.
    """
    serial = getattr(getattr(writer, "transport", None), "serial", None)
    if serial is None:
        return
    for line in ("dtr", "rts"):
        try:
            setattr(serial, line, False)
        except Exception:
            logger.debug(
                "serial open: %s refused %s deassert; continuing",
                port,
                line,
                exc_info=True,
            )


__all__ = ["SerialOpener", "open_serial_port"]
