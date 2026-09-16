"""Shared serial-port opener that keeps the modem control lines deasserted.

pyserial asserts DTR and RTS by default when a port is opened. On a radio
whose USB SEND/KEY input is wired to DTR or RTS (e.g. Icom SET ->
Connectors -> USB SEND), an asserted line keys the transmitter, so every
serial open in rigplane routes through :func:`open_serial_port`.

The helper builds the pyserial instance with ``do_not_open=True``, drives
both lines low while the port is still closed (pyserial stores them as
``_dtr_state``/``_rts_state`` and applies them in ``Serial.open()``),
opens the port, and then deasserts both lines again on the live object
behind ``writer.transport.serial``. At a radio's default settings neither
line keys anything (Icom USB SEND ships OFF), so this is a precaution for
stations that deliberately point USB SEND at DTR or RTS. Which half is
load-bearing is unestablished and is settled on the bench (MOR-2493).
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from typing import Any

logger = logging.getLogger(__name__)


async def open_serial_port(
    url: str,
    *,
    baudrate: int,
    opener: Callable[..., Awaitable[tuple[Any, Any]]] | None = None,
    **kwargs: Any,
) -> tuple[Any, Any]:
    """Open serial port *url* with DTR and RTS deasserted.

    The default opener constructs the pyserial instance closed, drives
    both modem control lines low before the port is opened, opens it, and
    wraps it the way ``serial_asyncio.open_serial_connection`` does.
    *opener* replaces that whole step in tests. After the open returns,
    both lines are deasserted once more on the live serial object as a
    second line of defence.

    Args:
        url: Serial device path (e.g. ``/dev/ttyUSB0``).
        baudrate: Baud rate.
        opener: Replacement for the real open step (used in tests).
        kwargs: Extra pyserial constructor parameters (``bytesize``,
            ``parity``, ``stopbits``, …).

    Returns:
        The ``(reader, writer)`` pair from the opener.
    """
    if opener is None:
        opener = _open_with_idle_lines
    reader, writer = await opener(url=url, baudrate=baudrate, **kwargs)
    _deassert_control_lines(writer, url)
    return reader, writer


async def _open_with_idle_lines(
    url: str, *, baudrate: int, **kwargs: Any
) -> tuple[Any, Any]:
    """Real open: configure DTR/RTS low on the closed port, then open + wrap.

    Mirrors ``serial_asyncio.open_serial_connection``, except the pyserial
    instance comes from :func:`_configure_serial_idle_lines` instead of a
    plain ``serial_for_url`` call.
    """
    from rigplane.core._optional_deps import _require_pyserial_asyncio

    _require_pyserial_asyncio()
    import serial_asyncio  # type: ignore[import-untyped]

    serial_instance = _configure_serial_idle_lines(url, baudrate=baudrate, **kwargs)
    loop = asyncio.get_running_loop()
    reader = asyncio.StreamReader()
    protocol = asyncio.StreamReaderProtocol(reader)
    transport, _ = await serial_asyncio.connection_for_serial(
        loop, lambda: protocol, serial_instance
    )
    writer = asyncio.StreamWriter(transport, protocol, reader, loop)
    return reader, writer


def _configure_serial_idle_lines(url: str, *, baudrate: int, **kwargs: Any) -> Any:
    """Build the pyserial instance, hold DTR/RTS low while closed, open it.

    ``dtr``/``rts`` are post-construction properties, not constructor
    kwargs — passing them to ``serial_for_url`` raises ``ValueError``.
    pyserial stores the values as ``_dtr_state``/``_rts_state`` and
    applies them in ``Serial.open()``.
    """
    import serial

    instance = serial.serial_for_url(url, baudrate=baudrate, do_not_open=True, **kwargs)
    instance.dtr = False
    instance.rts = False
    instance.open()
    return instance


def _deassert_control_lines(writer: Any, port: str) -> None:
    """Drive DTR and RTS low on the pyserial object behind *writer*.

    A writer without ``.transport.serial`` (every fake opener in tests) is
    a normal case, not an error; it also tolerates a future
    ``pyserial-asyncio`` that no longer exposes ``.serial``, given the
    ``>=0.6`` version floor. A port that refuses a control-line write
    (some virtual and Bluetooth ports do) is still usable, so the failure
    is swallowed — but it is logged at warning with the port name, because
    this is a TX-safety path and must be visible at default logging.
    """
    serial = getattr(getattr(writer, "transport", None), "serial", None)
    if serial is None:
        return
    for line in ("dtr", "rts"):
        try:
            setattr(serial, line, False)
        except Exception:
            logger.warning(
                "serial open: %s refused %s deassert; continuing",
                port,
                line,
                exc_info=True,
            )


__all__ = ["open_serial_port"]
