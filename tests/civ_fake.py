"""A stateful CI-V radio fake for wire-level tests (MOR-2492, PR 1).

``CivRadioFake`` is a transport-free radio: it holds wire-level identity
(model name, CI-V address, model-id bytes) and answers one frame at a
time via :meth:`CivRadioFake.handle`, which is deterministic, performs no
I/O, and needs no event loop. ``CivSerialPort`` adapts a radio to the
``(reader, writer)`` pair that ``open_serial_port`` returns, behind an
``opener`` callable suitable for the discovery probes' ``_open_serial=``
seam.

Codec ringfence (MOR-2492, "PR 1 — frozen scope"):

1. The frame *envelope* is shared with production — ``parse_civ_frame`` /
   ``build_civ_frame`` — but nothing here touches ``SerialFrameCodec``:
   ``CivSerialPort`` de-frames its inbound stream with a few deliberately
   dumb lines (split on ``FE FE``, take to the first ``FD``), so this fake
   cannot mask a codec bug by reimplementing codec behaviour.
2. Payload bytes are never shared: every payload byte below (model-id
   bytes, the ``0xFA`` NAK) is a literal. This module imports from
   ``rigplane.commands`` exactly ``parse_civ_frame``, ``build_civ_frame``,
   ``command_carries_sub`` and ``CONTROLLER_ADDR`` — nothing else.
3. ``tests/test_serial_civ_link.py`` and ``tests/test_serial_stub_framing.py``
   are forbidden consumers of this fake: they build their streams from byte
   literals precisely so that a framing bug shared by both ends of the wire
   stays visible, and moving them onto this fake would hide it again.

An IC-705 NAKing ``0x1D 0x19`` is identity, not a fault: the radio refuses
because that is what an IC-705 is. Faults live on the port (``silent=``),
never on the radio.
"""

from __future__ import annotations

import asyncio
from typing import Any

from rigplane.commands import (
    CONTROLLER_ADDR,
    build_civ_frame,
    command_carries_sub,
    parse_civ_frame,
)
from rigplane.profiles import resolve_radio_profile
from rigplane.types import CivFrame

_BROADCAST_ADDR = 0x00  # CI-V broadcast: every radio answers, none NAKs it

# Wire opcodes as the radio sees them: command byte, then the sub-command
# byte where the command carries one (command_carries_sub: 0x19 yes, 0x1D
# no), else the first payload byte.
_TRANSCEIVER_ID_OPCODE = b"\x19\x00"  # read transceiver ID — every CI-V radio
_XIEGU_MODEL_ID_OPCODE = b"\x1d\x19"  # Xiegu-only (X6200 CI-V V1.0.6, p.9)

# Wire-level identity per model: the transceiver-ID reply payload, plus the
# extra opcodes the model implements mapped to their reply payloads. Every
# byte is a literal by ringfence rule 2. The X6200 emulates an IC-705 at the
# transceiver-ID level (b"\x01\x05"), so the CI-V payload cannot
# disambiguate the two — exactly why the 0x1D 0x19 probe (MOR-226) exists.
_IDENTITY_TABLE: dict[str, tuple[bytes, dict[bytes, bytes]]] = {
    "IC-7610": (b"\x01\x06", {}),
    "IC-705": (b"\x01\x05", {}),
    "X6200": (b"\x01\x05", {_XIEGU_MODEL_ID_OPCODE: b"\x62\x00"}),
}


class CivRadioFake:
    """A CI-V radio reduced to identity: what it is decides what it answers.

    The CI-V address defaults to the resolved profile's ``civ_addr`` (no
    hardcoded address table here); ``civ_addr=`` overrides it explicitly.
    There is deliberately no fault surface on this class — refusals it emits
    (the ``0xFA`` NAK) follow from the model's identity alone.
    """

    def __init__(self, model: str = "IC-7610", *, civ_addr: int | None = None) -> None:
        try:
            transceiver_id, extra_opcodes = _IDENTITY_TABLE[model]
        except KeyError:
            known = ", ".join(sorted(_IDENTITY_TABLE))
            raise KeyError(
                f"unknown fake radio model {model!r}; known: {known}"
            ) from None
        self._model = model
        self._civ_addr = (
            civ_addr
            if civ_addr is not None
            else resolve_radio_profile(model=model).civ_addr
        )
        self._model_id = transceiver_id
        self._opcodes = {_TRANSCEIVER_ID_OPCODE: transceiver_id, **extra_opcodes}
        self._commands: list[CivFrame] = []

    @property
    def model(self) -> str:
        return self._model

    @property
    def civ_addr(self) -> int:
        return self._civ_addr

    @property
    def model_id(self) -> bytes:
        """The model-id bytes this radio reports in its transceiver-ID reply."""
        return self._model_id

    @property
    def commands(self) -> tuple[CivFrame, ...]:
        """Every inbound frame, parsed, in order of arrival."""
        return tuple(self._commands)

    def handle(self, frame: bytes) -> bytes | None:
        """Parse *frame*, record it, and return the radio's reply or None.

        Deterministic, no I/O, no event loop needed; it does record each
        parsed frame on ``self._commands``. Frames addressed to another
        radio are recorded but not answered. An opcode this model does not
        implement gets the ``0xFA`` NAK (identity, not a fault); an unknown
        broadcast gets silence, as on a real bus.
        """
        parsed = parse_civ_frame(frame)
        self._commands.append(parsed)
        if parsed.to_addr not in (self._civ_addr, _BROADCAST_ADDR):
            return None
        opcode = bytes([parsed.command])
        if command_carries_sub(parsed.command):
            if parsed.sub is not None:
                opcode += bytes([parsed.sub])
        elif parsed.data:
            opcode += parsed.data[:1]
        reply_data = self._opcodes.get(opcode)
        if reply_data is None:
            if parsed.to_addr == _BROADCAST_ADDR:
                return None
            return build_civ_frame(CONTROLLER_ADDR, self._civ_addr, 0xFA)
        # Echo the opcode and append the reply payload, re-splitting command/
        # sub through the sanctioned accessor (0x19 carries a sub, 0x1D does
        # not — its 0x19 is payload). Replies go to the controller, matching
        # every probe this fake serves.
        payload = opcode + reply_data
        if command_carries_sub(payload[0]):
            return build_civ_frame(
                CONTROLLER_ADDR,
                self._civ_addr,
                payload[0],
                sub=payload[1],
                data=payload[2:],
            )
        return build_civ_frame(
            CONTROLLER_ADDR, self._civ_addr, payload[0], data=payload[1:]
        )


class CivSerialPort:
    """Presents a :class:`CivRadioFake` as an async ``(reader, writer)`` pair.

    ``opener`` matches the ``opener=`` contract of
    ``rigplane.core.serial_open.open_serial_port`` (keyword ``url`` /
    ``baudrate``), so it passes straight to the discovery probes'
    ``_open_serial=`` parameter. With ``silent=True`` the port opens but the
    device on it is not a radio: writes are accepted, reads never yield.
    That is the port's one fault mode; the radio has none.
    """

    def __init__(self, radio: CivRadioFake, *, silent: bool = False) -> None:
        self._radio = radio
        self._silent = silent

    async def opener(
        self,
        *,
        url: str | None = None,
        baudrate: int | None = None,
        **kwargs: Any,
    ) -> tuple[asyncio.StreamReader, _PortWriter]:
        """Open a fresh ``(reader, writer)`` pair bound to this port's radio."""
        reader = asyncio.StreamReader()
        return reader, _PortWriter(self._radio, self._silent, reader)


class _PortWriter:
    """The write half of a :class:`CivSerialPort`.

    De-frames host bytes into the radio and feeds its replies to the paired
    reader. De-framing is deliberately dumb (ringfence rule 1): split on
    ``FE FE``, take to the first ``FD`` — no resync on nested preamble, no
    ``0xFC`` abort handling, no partial-frame timeout, no length cap.
    """

    def __init__(
        self, radio: CivRadioFake, silent: bool, reader: asyncio.StreamReader
    ) -> None:
        self._radio = radio
        self._silent = silent
        self._reader = reader
        self._buf = bytearray()
        self.closed = False

    def write(self, data: bytes) -> None:
        if self._silent:
            return  # not a radio: writes are accepted, nothing ever answers
        self._buf += data
        while True:
            start = self._buf.find(b"\xfe\xfe")
            if start < 0:
                self._buf.clear()
                return
            end = self._buf.find(b"\xfd", start + 2)
            if end < 0:
                del self._buf[:start]
                return
            reply = self._radio.handle(bytes(self._buf[start : end + 1]))
            del self._buf[: end + 1]
            if reply is not None:
                self._reader.feed_data(reply)

    async def drain(self) -> None:
        pass

    def close(self) -> None:
        self.closed = True
        self._reader.feed_eof()

    async def wait_closed(self) -> None:
        pass
