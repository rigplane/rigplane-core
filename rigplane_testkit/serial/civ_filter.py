"""Client-side frame gate for a CI-V capture on a real radio.

The CI-V sibling of :class:`rigplane_testkit.yaesu.CatReadOnlyGuard`: hand
:meth:`CivFrameGuard.new_filter` to
``SerialCivRecorder(client_filter=...)`` and the bridge forwards only the
complete ``FE FE ... FD`` frames the predicate accepts. Everything else (a
refused frame, bytes outside any frame) never reaches the radio and is
returned as a blocked chunk for the recorder to log.

The adversary is the polling client under capture (the Core server, a
validation run): its connect sequence or a stray control may write; the
capture session must not depend on it behaving.
"""

from __future__ import annotations

from collections.abc import Callable

_PREAMBLE = b"\xfe\xfe"
_TERMINATOR = b"\xfd"

# A CI-V frame is a few dozen bytes at most; a longer unterminated run is
# garbage, not a frame waiting for its FD.
_MAX_FRAME = 128


class CivFrameGuard:
    """The predicate over one complete client frame (``True`` = forward)."""

    def __init__(self, allows: Callable[[bytes], bool]) -> None:
        self._allows = allows

    def allows(self, frame: bytes) -> bool:
        return self._allows(frame)

    def new_filter(self) -> CivFrameFilter:
        """A fresh per-connection filter (it holds a partial frame between reads)."""
        return CivFrameFilter(self)


class CivFrameFilter:
    """Splits a client byte stream into frames and forwards the allowed ones."""

    def __init__(self, guard: CivFrameGuard) -> None:
        self._guard = guard
        self._buf = bytearray()

    def feed(self, data: bytes) -> tuple[bytes, list[bytes]]:
        """Consume one chunk; return ``(forward, blocked_chunks)``.

        A partial frame is held until its ``FD`` arrives. Bytes before a
        ``FE FE`` preamble, and an unterminated run longer than a frame can
        be, are blocked; a trailing lone ``FE`` is kept for the next chunk.
        """
        self._buf += data
        forward = bytearray()
        blocked: list[bytes] = []
        while True:
            start = self._buf.find(_PREAMBLE)
            if start < 0:
                keep = 1 if self._buf[-1:] == _PREAMBLE[:1] else 0
                if len(self._buf) > keep:
                    blocked.append(bytes(self._buf[: len(self._buf) - keep]))
                del self._buf[: len(self._buf) - keep]
                break
            if start:
                blocked.append(bytes(self._buf[:start]))
                del self._buf[:start]
            end = self._buf.find(_TERMINATOR, 2)
            if end < 0:
                if len(self._buf) > _MAX_FRAME:
                    blocked.append(bytes(self._buf))
                    self._buf.clear()
                break
            frame = bytes(self._buf[: end + 1])
            del self._buf[: end + 1]
            if self._guard.allows(frame):
                forward += frame
            else:
                blocked.append(frame)
        return bytes(forward), blocked
