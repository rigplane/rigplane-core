"""Slaved IQ source for the Xiegu UDP datagram protocol (MOR-4398)."""

from __future__ import annotations

import logging
import socket
import struct
import threading
import time
from math import isfinite
from typing import Any, Callable

from rigplane.core._optional_deps import _require_numpy

from .types import IqBlock, IqSourceFacts, UdpIqSourceSpec

__all__ = ["XieguUdpIqSource", "encode_tune"]

logger = logging.getLogger(__name__)

_SOCKET_TIMEOUT_S = 0.05
_CLOSE_JOIN_TIMEOUT_S = 1.0
_DATAGRAMS_PER_BLOCK = 10


def encode_tune(hz: int) -> bytes:
    return struct.pack(">IHQ", 0xAA5555AA, 1, hz)


class XieguUdpIqSource:
    """Receive a radio-owned UDP IQ stream without independently tuning it.

    Ten datagrams form a 1920-sample block (about 40 ms, 25 callbacks/s);
    partial blocks flush after that interval. Tune writes wait for a settled
    centre and silence; no stop exists, so the radio streams until poweroff.
    """

    def __init__(
        self,
        spec: UdpIqSourceSpec,
        *,
        clock: Callable[[], float] = time.monotonic,
        start_grace_s: float = 0.5,
        start_settle_s: float = 0.5,
        stall_s: float = 2.0,
    ) -> None:
        _require_numpy()
        import numpy as np

        timings = (start_grace_s, start_settle_s, stall_s)
        if not all(isfinite(value) for value in timings):
            raise ValueError("timing values must be finite")
        if start_grace_s < 0 or start_settle_s < 0 or stall_s <= 0:
            raise ValueError("start_grace_s/start_settle_s must be >= 0; stall_s > 0")

        self._spec = spec
        self._peer_host: str | None = None
        self._np = np
        self._clock = clock
        self._start_grace_s = float(start_grace_s)
        self._start_settle_s = float(start_settle_s)
        self._stall_s = float(stall_s)
        self._facts = IqSourceFacts(
            "slaved", spec.center_offset_hz, spec.usable_bandwidth_hz
        )
        self._block_period_s = (
            _DATAGRAMS_PER_BLOCK * spec.samples_per_datagram / spec.sample_rate_hz
        )

        self._lock = threading.Lock()
        self._center_freq = 0
        self._center_changed_s = 0.0
        self._callback: Callable[[IqBlock], None] | None = None
        self._callback_warned = False
        self._dropped_datagrams = 0

        self._is_open = False
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._socket: socket.socket | None = None
        self._opened_at_s = 0.0
        self._last_valid_s: float | None = None
        self._next_tune_s = 0.0

    def open(self) -> None:
        with self._lock:
            if self._is_open:
                return
            sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            try:
                sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
                sock.bind(("", self._spec.receive_port))
                sock.settimeout(_SOCKET_TIMEOUT_S)
            except OSError as exc:
                sock.close()
                raise OSError(
                    f"could not bind UDP receive port {self._spec.receive_port}: {exc}"
                ) from exc

            now = self._clock()
            self._socket = sock
            self._peer_host = None
            self._opened_at_s = now
            self._last_valid_s = None
            self._next_tune_s = now + self._start_grace_s
            self._dropped_datagrams = 0
            self._stop.clear()
            self._is_open = True
            thread = threading.Thread(
                target=self._reader_loop, args=(sock,), daemon=True
            )
            thread.name = "XieguUdpIqSource-reader"
            self._thread = thread
            try:
                thread.start()
            except BaseException:
                self._is_open = False
                self._thread = None
                self._socket = None
                self._stop.set()
                sock.close()
                raise

    def close(self) -> None:
        with self._lock:
            self._is_open = False
            self._center_freq = 0
            self._center_changed_s = self._clock()
            self._stop.set()
            sock = self._socket
            self._socket = None
            thread = self._thread
        if sock is not None:
            sock.close()
        if thread is not None and thread is not threading.current_thread():
            thread.join(timeout=_CLOSE_JOIN_TIMEOUT_S)
            if thread.is_alive():
                logger.error("UDP IQ reader thread did not stop within 1 s")
        with self._lock:
            if self._thread is thread:
                self._thread = None

    @property
    def is_open(self) -> bool:
        with self._lock:
            return self._is_open

    @property
    def facts(self) -> IqSourceFacts:
        return self._facts

    def set_center_freq(self, hz: int) -> None:
        with self._lock:
            value = int(hz)
            if value != self._center_freq:
                self._center_freq = value
                self._center_changed_s = self._clock()

    @property
    def center_freq_hz(self) -> int:
        with self._lock:
            return self._center_freq

    def set_sample_rate(self, hz: int) -> None:
        if hz != self._spec.sample_rate_hz:
            raise ValueError(
                f"sample_rate_hz is fixed at {self._spec.sample_rate_hz}, got {hz}"
            )

    @property
    def sample_rate_hz(self) -> int:
        return self._spec.sample_rate_hz

    def set_gain(self, db: float | None) -> None:
        """Do nothing: this radio-LAN protocol has no gain control."""

    def frequency_range_hz(self) -> tuple[int, int]:
        return (self._spec.tune_min_hz, self._spec.tune_max_hz)

    def on_block(self, callback: Callable[[IqBlock], None] | None) -> None:
        with self._lock:
            self._callback = callback

    @property
    def dropped_datagrams(self) -> int:
        with self._lock:
            return self._dropped_datagrams

    @property
    def stalled(self) -> bool:
        with self._lock:
            if not self._is_open:
                return False
            last_valid = self._last_valid_s
            since = self._opened_at_s if last_valid is None else last_valid
        return self._clock() - since >= self._stall_s

    def _reader_loop(self, sock: socket.socket) -> None:
        pending: list[Any] = []
        pending_center = 0
        pending_started_s = 0.0
        pending_overflow = False
        expected_size = self._spec.samples_per_datagram * 4

        def flush() -> None:
            nonlocal pending, pending_overflow
            if not pending:
                return
            samples = pending[0] if len(pending) == 1 else self._np.concatenate(pending)
            block = IqBlock(
                samples=samples,
                center_freq_hz=pending_center,
                sample_rate_hz=self._spec.sample_rate_hz,
                timestamp_s=pending_started_s,
                overflow=pending_overflow,
            )
            pending = []
            pending_overflow = False
            self._deliver(block)

        try:
            peer_host = socket.gethostbyname(self._spec.host)
            self._peer_host = peer_host
            while not self._stop.is_set():
                try:
                    payload, address = sock.recvfrom(65535)
                except ConnectionResetError:
                    pass
                except TimeoutError:
                    pass
                except OSError as exc:
                    if not self._stop.is_set():
                        logger.warning("UDP IQ receive failed: %s", exc)
                    break
                else:
                    now = self._clock()
                    if address[0] != peer_host:
                        with self._lock:
                            self._dropped_datagrams += 1
                    elif len(payload) != expected_size:
                        with self._lock:
                            self._dropped_datagrams += 1
                        flush()
                        pending_overflow = True
                    else:
                        with self._lock:
                            previous_valid = self._last_valid_s
                            self._last_valid_s = now
                            center = self._center_freq
                        if (
                            previous_valid is not None
                            and now - previous_valid >= self._stall_s
                        ):
                            flush()
                            pending_overflow = True
                        if pending and center != pending_center:
                            flush()
                        if center != 0:
                            pairs = self._np.frombuffer(payload, dtype="<i2").astype(
                                self._np.float32
                            )
                            pairs = pairs.reshape(-1, 2)
                            samples = self._np.empty(
                                self._spec.samples_per_datagram,
                                dtype=self._np.complex64,
                            )
                            samples.real = pairs[:, 0]
                            samples.imag = pairs[:, 1]
                            samples *= self._np.float32(1 / 32768)
                            if not pending:
                                pending_center = center
                                pending_started_s = now
                            pending.append(samples)
                            if len(pending) >= _DATAGRAMS_PER_BLOCK:
                                flush()

                now = self._clock()
                if pending and now - pending_started_s >= self._block_period_s:
                    flush()
                self._maybe_start(now)
        except OSError as exc:
            if not self._stop.is_set():
                logger.warning("UDP IQ host resolution failed: %s", exc)
        finally:
            if not self._stop.is_set():
                with self._lock:
                    self._is_open = False
                    self._center_freq = 0
                    self._center_changed_s = self._clock()
                    self._peer_host = None
                    if self._socket is sock:
                        self._socket = None
                sock.close()

    def _maybe_start(self, now: float) -> None:
        with self._lock:
            if not self._is_open or self._center_freq == 0 or now < self._next_tune_s:
                return
            last_valid = self._last_valid_s
            silence_since = self._opened_at_s if last_valid is None else last_valid
            silence_s = self._start_grace_s if last_valid is None else self._stall_s
            if now - silence_since < silence_s:
                return
            if now - self._center_changed_s < self._start_settle_s:
                return
            vfo_hz = self._center_freq - self._spec.center_offset_hz
            if not self._spec.tune_min_hz <= vfo_hz <= self._spec.tune_max_hz:
                return
            sock = self._socket
            peer_host = self._peer_host
            if sock is None or peer_host is None:
                return
            self._next_tune_s = now + self._stall_s
            try:
                # Keep the lock through sendto so a centre update cannot make
                # this VFO-write stale between the snapshot and the wire.
                sock.sendto(encode_tune(vfo_hz), (peer_host, self._spec.radio_port))
            except OSError as exc:
                logger.warning("UDP IQ tune command failed: %s", exc)

    def _deliver(self, block: IqBlock) -> None:
        with self._lock:
            callback = self._callback
        if callback is None:
            return
        try:
            callback(block)
        except Exception:  # noqa: BLE001 - reader thread must survive consumers
            if not self._callback_warned:
                self._callback_warned = True
                logger.exception("IqBlock on_block callback raised; continuing")
        else:
            self._callback_warned = False
