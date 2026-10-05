"""Universal USB audio driver for all serial-connected radios (macOS-first).

Supports automatic device resolution when multiple USB Audio devices
are present (e.g. IC-7300 + FTX-1 both connected via USB).
Works with any radio that exposes a standard USB Audio Class device:
Icom (IC-7300, IC-705, IC-9700), Yaesu (FTX-1, FT-710, FT-991A),
Kenwood (TS-890S, TS-590SG), etc.

See :mod:`rigplane.usb_audio_resolve` for the topology-based resolution logic.
"""

from __future__ import annotations

import asyncio
import logging
import platform
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from dataclasses import dataclass
from types import SimpleNamespace
from typing import Any, Callable, Coroutine, Iterator, Literal

from .backend import (
    AudioBackend,
    AudioDeviceConfig,
    AudioDeviceId,
    AudioDeviceInfo,
    DuplexStream,
    PortAudioBackend,
    RxStream,
    TxStream,
    _platform_uid_from_device_name,
)

logger = logging.getLogger(__name__)

_SILENCE_WARN_SECONDS = 10
"""Consecutive bit-exact-zero RX seconds before the silence watchdog warns."""

_DEPENDENCY_HINT = (
    "USB audio backend requires optional dependencies sounddevice and numpy. "
    "Install with: pip install rigplane[bridge]"
)


def _is_silent_frame(frame: bytes) -> bool:
    """Cheap bit-exact-zero check — one memcmp, no per-sample Python loop."""
    return bool(frame) and frame == bytes(len(frame))


# Ordered by selection preference (lower index = stronger match). The
# C-Media identity ranks the Xiegu X6200's audio codec ahead of an unknown
# commodity device on platforms without topology resolution (MOR-219). It is
# placed after the explicit "usb audio" names so a vendor CODEC still wins
# when both are present.
_USB_NAME_PATTERNS: tuple[str, ...] = (
    "usb audio codec",
    "usb audio",
    "icom",
    "yaesu",
    "kenwood",
    "c-media",
    "cmedia",
    "ftdi",
)


class AudioDeviceSelectionError(RuntimeError):
    """Raised when no suitable USB audio device can be selected."""


class AudioDriverLifecycleError(RuntimeError):
    """Raised on invalid USB audio lifecycle operations."""


class AudioAlreadyStartedError(AudioDriverLifecycleError):
    """Raised when starting an audio stream that is already running.

    Shared by the USB driver and the Icom LAN stream (MOR-563) so
    consumers can match the benign double-start case by type instead of
    message text. Subclasses :class:`AudioDriverLifecycleError` (and thus
    ``RuntimeError``) so existing ``except`` clauses keep catching it.
    """


class AudioNotStartedError(AudioDriverLifecycleError):
    """Raised when using an audio stream that has not been started.

    Shared by the USB driver and the Icom LAN stream (MOR-563).
    Subclasses :class:`AudioDriverLifecycleError` (and thus
    ``RuntimeError``) so existing ``except`` clauses keep catching it.
    """


class AudioCaptureOpenTimeoutError(AudioDriverLifecycleError):
    """Raised when a bounded off-loop PortAudio operation did not finish.

    Bench-observed (2026-08-11, MOR-1438): a macOS TCC microphone-consent
    prompt that never renders leaves the blocking CoreAudio open call
    hanging forever. The open now runs off the event loop (see
    ``UsbAudioDriver._open_stream``) and is bounded by
    ``capture_open_timeout``; a stuck open raises this instead of hanging
    the caller (and, before this fix, the whole event loop). MOR-2892
    widened the same treatment to every other PortAudio call reachable
    from the web server — the format probe (``Pa_IsFormatSupported``, the
    2026-09-28 stand freeze), device enumeration, and stream stop/close —
    so this now also fires when one of those exceeds the bound. Subclasses
    :class:`AudioDriverLifecycleError` so it flows through the SAME
    honest-downgrade path other lifecycle failures already use (MOR-582,
    ADR Sec3.4): the exception propagates to :class:`~rigplane.audio.bus.AudioBus`,
    which keeps ``rx_active``/``tx_active`` false and surfaces the failure
    to WS clients — no bespoke availability flag. The next subscriber
    attempt opens a fresh stream from scratch.
    """


_DEFAULT_SAMPLE_RATE_CANDIDATES: tuple[int, ...] = (48_000, 24_000, 16_000, 8_000)

_CAPTURE_OPEN_TIMEOUT_S = 8.0
"""Default bound (seconds) on an RX/TX stream open, see ``_open_stream``."""

_WINDOWS_PNP_TIMEOUT_S = 5.0
_WINDOWS_SELECTION_RESERVE_S = 0.25

_CAPTURE_OPEN_MAX_WORKERS = 8
"""Size of the driver-owned open/close thread pool (MOR-1438, F3).

A DEDICATED pool, not ``loop.run_in_executor(None, ...)``'s process-wide
default executor: that pool is shared with the web server, eibi lookups,
diagnostics, and discovery, so a single wedged capture open would
permanently eat one of its threads and, given enough stuck opens, starve
those unrelated subsystems too. Sized for a couple of concurrent RX/TX
opens plus a couple of abandoned-handle closes running at once.

``_rx_lock``/``_tx_lock`` serialize CONCURRENT opens, NOT the pool's worker
budget (MOR-1573 correction of an earlier, wrong claim here): a wedged
open is abandoned (see ``_open_stream``) so the NEXT sequential open can
start while the stuck one's worker keeps running unattended in the
background. Enough sequential wedged opens against an otherwise-healthy
device still exhausts this pool one worker at a time. The saturation
check in ``_open_stream`` catches that case and fails fast with an honest
"pool saturated" error instead of letting the new open queue behind the
stuck ones and burn the full ``capture_open_timeout`` for an unrelated
reason.
"""


@contextmanager
def _portaudio_worker_com() -> Iterator[None]:
    """Scope COM to the Windows thread performing native stream operations.

    PortAudio initialization on another thread does not initialize this
    worker. WASAPI callback start marshals COM interfaces here. Never change
    an existing apartment or uninitialize an initialization owned by someone
    else; S_OK and S_FALSE both acquire one count that this scope releases.
    """
    if sys.platform != "win32":
        yield
        return

    import ctypes

    ole32 = ctypes.WinDLL("ole32")
    ole32.CoInitializeEx.argtypes = [ctypes.c_void_p, ctypes.c_uint32]
    ole32.CoInitializeEx.restype = ctypes.c_int32
    ole32.CoUninitialize.argtypes = []
    ole32.CoUninitialize.restype = None
    hresult = int(ole32.CoInitializeEx(None, 0)) & 0xFFFFFFFF
    owned = hresult in (0, 1)
    if not owned and hresult != 0x80010106:  # RPC_E_CHANGED_MODE: already initialized.
        raise RuntimeError(
            f"Windows audio worker COM initialization failed: 0x{hresult:08x}"
        )
    try:
        yield
    finally:
        if owned:
            ole32.CoUninitialize()


def _drive_stream_open(coro: "Coroutine[Any, Any, None]") -> None:
    """Run a stream's ``start()``/``stop()`` coroutine to completion here.

    ``RxStream``/``TxStream`` implementations (PortAudio, Fake) perform no
    genuine async I/O inside ``start()``/``stop()`` — the real device open
    and close are synchronous CoreAudio/PortAudio calls made from an
    ``async def`` only for Protocol conformance — so driving the coroutine
    via a private event loop on a worker thread (MOR-1438) safely moves
    any blocking work off the caller's loop without changing the
    ``RxStream``/``TxStream`` contract. Reused for BOTH the initial open
    (:meth:`_BoundedPortAudioPool.open_stream_bounded`) and a late/abandoned
    handle's close (:meth:`_BoundedPortAudioPool.close_late_stream`, F2): a
    wedged device can block its ``stop()`` exactly as it blocked its ``start()``.
    """
    try:
        with _portaudio_worker_com():
            asyncio.run(coro)
    finally:
        # COM refusal precedes asyncio.run: do not leak that unawaited start.
        coro.close()


class _BoundedPortAudioPool:
    """ONE process-wide bounded off-loop submission path for PortAudio work.

    Stand incident (2026-09-28): a pending macOS microphone-permission
    prompt (TCC) blocked ``Pa_IsFormatSupported`` on the event-loop
    thread and froze the whole server (MOR-2892). Every web-reachable
    PortAudio call now runs here — the :class:`UsbAudioDriver` (probes,
    enumeration, opens/stops) AND the :class:`AudioBridge`
    (enumeration, starts/stops) — awaited no longer than the caller's
    bound. ONE pool and ONE saturation counter is the point: a second
    executor would let the two subsystems wedge each other's blind
    spot. Past the bound the request fails with one operator-readable
    warning while the server keeps answering; the abandoned work item
    keeps running unattended on its worker. ``inflight`` counts
    submitted-but-unfinished operations (MOR-1573): an abandoned call
    stays counted until its future resolves, so the fail-fast tracks
    REAL worker-pool pressure.
    """

    def __init__(self) -> None:
        # DEDICATED pool, not the process-wide default executor
        # (MOR-1438, F3; bound: ``_CAPTURE_OPEN_MAX_WORKERS``).
        # Non-daemon threads: a wedged-open thread delays ``atexit`` —
        # the same failure mode the frozen loop already had pre-fix.
        self._executor = ThreadPoolExecutor(
            max_workers=_CAPTURE_OPEN_MAX_WORKERS,
            thread_name_prefix="rigplane-audio-open",
        )
        self.inflight = 0
        # A timed-out close still owns its worker and must not be run twice
        # concurrently. Failed abandoned cleanup retains the stream until an
        # explicit later open can retry it on this same pool.
        self._stops: dict[int, asyncio.Future[Any]] = {}
        self._late_streams: dict[
            int, tuple[RxStream | TxStream | DuplexStream, str]
        ] = {}

    def submit_tracked(self, fn: Callable[[], Any]) -> "asyncio.Future[Any]":
        """Submit *fn* to the pool; ``inflight`` uncounts only on settle.

        Abandoned (timed-out) calls stay counted, keeping saturation honest.
        """
        loop = asyncio.get_running_loop()
        future = loop.run_in_executor(self._executor, fn)
        self.inflight += 1

        def _on_settled(_future: "asyncio.Future[Any]") -> None:
            self.inflight -= 1

        future.add_done_callback(_on_settled)
        return future

    async def run_bounded(
        self,
        fn: Callable[[], Any],
        *,
        what: str,
        direction: str,
        timeout: float,
        warn_on_timeout: bool = True,
    ) -> Any:
        """Run a blocking call off the loop, bounded.

        An abandoned item has no stream handle to late-close and remains
        counted until its worker settles.
        """
        if self.inflight >= _CAPTURE_OPEN_MAX_WORKERS:
            logger.warning(
                "usb-audio: PortAudio worker pool saturated by %d stuck "
                "operation(s) — failing the %s %s fast instead of queuing "
                "behind them",
                self.inflight,
                direction.upper(),
                what,
            )
            raise AudioCaptureOpenTimeoutError(
                f"PortAudio worker pool saturated by "
                f"{self.inflight} stuck operation(s)."
            )

        future = self.submit_tracked(fn)
        _done, pending = await asyncio.wait({future}, timeout=timeout)
        if future in pending:
            if warn_on_timeout:
                logger.warning(
                    "usb-audio: %s %s did not finish within %.1fs — an OS audio "
                    "device call is stuck; a microphone permission prompt may "
                    "be pending on the computer running RigPlane (macOS: grant "
                    "Microphone access to the app that launched the server, "
                    "e.g. Terminal); failing this audio request",
                    direction.upper(),
                    what,
                    timeout,
                )
            raise AudioCaptureOpenTimeoutError(
                f"{direction.upper()} {what} timed out after {timeout}s."
            )
        return future.result()

    async def open_stream_bounded(
        self,
        stream: "RxStream | TxStream | DuplexStream",
        start_coro: "Coroutine[Any, Any, None]",
        *,
        direction: str,
        timeout: float,
        what: str = "capture open",
    ) -> None:
        """Start a stream off the loop, bounded, with late-close cleanup.

        Saturation fail-fast (MOR-1573), the bounded await, and the
        abandoned-open cleanup (MOR-1438 F1/F2). The fail-fast path
        closes the pre-constructed *start_coro* explicitly (else Python
        warns "coroutine was never awaited"). *what* names the
        operation in warnings (bridge legs open through here too).
        """
        if self.inflight >= _CAPTURE_OPEN_MAX_WORKERS:
            logger.warning(
                "usb-audio: %s %s worker pool saturated by %d "
                "stuck open(s) — failing fast instead of queuing behind "
                "them",
                direction.upper(),
                what,
                self.inflight,
            )
            start_coro.close()
            raise AudioCaptureOpenTimeoutError(
                f"{direction.upper()} {what} worker pool saturated "
                f"by {self.inflight} stuck open(s)."
            )

        deadline = asyncio.get_running_loop().time() + timeout
        try:
            for old_stream, old_direction in list(self._late_streams.values()):
                await self.stop_stream_bounded(
                    old_stream,
                    direction=old_direction,
                    timeout=max(0.0, deadline - asyncio.get_running_loop().time()),
                )
        except BaseException:
            start_coro.close()
            raise
        if asyncio.get_running_loop().time() >= deadline:
            start_coro.close()
            raise AudioCaptureOpenTimeoutError(
                f"{direction.upper()} cleanup exhausted the open timeout."
            )
        if self.inflight >= _CAPTURE_OPEN_MAX_WORKERS:
            start_coro.close()
            raise AudioCaptureOpenTimeoutError(
                "PortAudio worker pool saturated after cleanup."
            )
        future = self.submit_tracked(lambda: _drive_stream_open(start_coro))
        try:
            _done, pending = await asyncio.wait(
                {future},
                timeout=max(0.0, deadline - asyncio.get_running_loop().time()),
            )
        except asyncio.CancelledError:
            self.abandon_open(future, stream, direction, what)
            raise
        if future in pending:
            self.abandon_open(future, stream, direction, what)
            logger.warning(
                "usb-audio: %s %s timed out after %.1fs — likely "
                "blocked on OS capture-permission consent (macOS TCC "
                "microphone-consent prompt that never rendered, see "
                "MOR-1420); marking %s audio unavailable for this session, "
                "will retry on the next subscriber",
                direction.upper(),
                what,
                timeout,
                direction,
            )
            raise AudioCaptureOpenTimeoutError(
                f"{direction.upper()} {what} timed out after {timeout}s."
            )
        future.result()  # re-raise the open's own exception, if any

    def abandon_open(
        self,
        future: "asyncio.Future[None]",
        stream: "RxStream | TxStream | DuplexStream",
        direction: str,
        what: str = "capture open",
    ) -> None:
        """Detach from a still-running background open (MOR-1438, F1).

        Nobody awaits *future* any more, so :meth:`close_late_stream`
        closes the handle when it settles (else a late open flips
        ``running`` True with no consumer). An already-settled future
        closes immediately.
        """
        if future.done():
            self.close_late_stream(future, stream, direction, what)
            return

        def _on_late_open(late_future: "asyncio.Future[None]") -> None:
            self.close_late_stream(late_future, stream, direction, what)

        future.add_done_callback(_on_late_open)

    def close_late_stream(
        self,
        future: "asyncio.Future[None]",
        stream: "RxStream | TxStream | DuplexStream",
        direction: str,
        what: str = "capture open",
    ) -> None:
        """Close a stream handle that finished opening after abandonment.

        Fires as a done-callback ON THE EVENT-LOOP THREAD, but the close
        itself runs on this pool via :func:`_drive_stream_open`
        (MOR-1438, F2) — a wedged device blocks ``stop()`` exactly as
        it blocked ``start()`` — and is COUNTED via
        :meth:`submit_tracked` (MOR-2892). An unclosed late handle
        would hold the OS device open forever. Even a late open exception can
        retain a native handle when its own cleanup failed.
        """
        if future.cancelled():
            return
        exc = future.exception()
        if exc is not None:
            logger.warning(
                "usb-audio: %s %s abandoned and later failed: %s",
                direction.upper(),
                what,
                exc,
                exc_info=exc,
            )
        else:
            logger.warning(
                "usb-audio: %s %s completed after it was abandoned "
                "— closing the late handle instead of leaking it",
                direction.upper(),
                what,
            )
        self._late_streams[id(stream)] = stream, direction
        try:
            close_future = self._submit_stop(stream)
        except AudioCaptureOpenTimeoutError:
            logger.warning("usb-audio: retaining late %s handle for cleanup", direction)
            return

        def _on_close_done(done_future: "asyncio.Future[None]") -> None:
            if done_future.cancelled():
                return
            exc = done_future.exception()
            if exc is not None:
                logger.debug(
                    "usb-audio: failed to close late %s handle",
                    direction,
                    exc_info=exc,
                )

        close_future.add_done_callback(_on_close_done)

    def _submit_stop(
        self, stream: "RxStream | TxStream | DuplexStream"
    ) -> "asyncio.Future[Any]":
        key = id(stream)
        pending = self._stops.get(key)
        if pending is not None:
            return pending
        if self.inflight >= _CAPTURE_OPEN_MAX_WORKERS:
            logger.warning(
                "usb-audio: PortAudio worker pool saturated during stream stop"
            )
            raise AudioCaptureOpenTimeoutError(
                "PortAudio worker pool saturated during cleanup."
            )
        future = self.submit_tracked(lambda: _drive_stream_open(stream.stop()))
        self._stops[key] = future

        def settled(done: "asyncio.Future[Any]") -> None:
            if self._stops.get(key) is done:
                del self._stops[key]
            if not done.cancelled() and done.exception() is None:
                self._late_streams.pop(key, None)

        future.add_done_callback(settled)
        return future

    async def stop_stream_bounded(
        self,
        stream: "RxStream | TxStream | DuplexStream",
        *,
        direction: str,
        timeout: float,
    ) -> None:
        """Stop/close *stream* off the loop, bounded.

        A real ``stream.stop()`` is a synchronous Pa_StopStream +
        Pa_CloseStream pair — the same blocking call class kept off the
        loop. ``close_late_stream`` already drives the ABANDONED-handle
        close; this covers every ordinary stop path.
        """
        future = self._submit_stop(stream)
        _done, pending = await asyncio.wait({future}, timeout=timeout)
        if future in pending:
            logger.warning(
                "usb-audio: %s stream stop did not finish within %.1fs — retaining "
                "the handle until native cleanup succeeds",
                direction.upper(),
                timeout,
            )
            raise AudioCaptureOpenTimeoutError(
                f"{direction.upper()} stream stop timed out after {timeout}s."
            )
        future.result()


bounded_portaudio_pool = _BoundedPortAudioPool()
"""Module singleton shared by ``UsbAudioDriver`` and ``AudioBridge``.

One executor, one bound, one counter — deliberately NOT per-instance
(MOR-2892 review round 1)."""


@dataclass(frozen=True, slots=True)
class UsbAudioDevice:
    """Normalized USB audio device descriptor."""

    index: int
    name: str
    input_channels: int
    output_channels: int
    default_samplerate: int = 48_000
    is_default_input: bool = False
    is_default_output: bool = False
    platform_uid: str = ""

    @property
    def supports_rx(self) -> bool:
        """Whether the device can capture RX audio from radio (input channels)."""
        return self.input_channels > 0

    @property
    def supports_tx(self) -> bool:
        """Whether the device can play TX audio to radio (output channels)."""
        return self.output_channels > 0

    @property
    def duplex(self) -> bool:
        """Whether the device supports both capture and playback."""
        return self.supports_rx and self.supports_tx

    def to_dict(self) -> dict[str, object]:
        """Return a JSON-friendly device descriptor for diagnostics."""

        return {
            "index": self.index,
            "name": self.name,
            "input_channels": self.input_channels,
            "output_channels": self.output_channels,
            "default_samplerate": self.default_samplerate,
            "platform_uid": self.platform_uid,
        }


@dataclass(frozen=True, slots=True)
class UsbAudioStreamContract:
    """Effective OS audio-device stream contract for one direction."""

    direction: str
    device: UsbAudioDevice
    sample_rate_hz: int
    channels: int
    frame_ms: int
    sample_rate_source: str
    channel_source: str = "requested"
    fallback_reason: str | None = None
    open_channels: int | None = None
    """Device-native channel count the OS stream is opened at.

    ``None`` means the stream opens at ``channels`` (open == deliver). It is set
    only when the device is opened at MORE channels than downstream consumes —
    i.e. a mono (deliver=1) request on a stereo-native device (open=2): the
    stream opens at the native count and software-downmixes back to ``channels``
    (MOR-504). ``channels`` always stays the DELIVERED count (downstream DSP is
    48 kHz mono), so diagnostics and the fixed-frame contract see the mono path.
    """

    @property
    def effective_open_channels(self) -> int:
        """Channel count to open the OS stream at (defaults to ``channels``)."""
        return self.channels if self.open_channels is None else self.open_channels

    def to_dict(self) -> dict[str, object]:
        payload: dict[str, object] = {
            "direction": self.direction,
            "device": self.device.to_dict(),
            "sample_rate_hz": self.sample_rate_hz,
            "channels": self.channels,
            "frame_ms": self.frame_ms,
            "sample_rate_source": self.sample_rate_source,
            "channel_source": self.channel_source,
        }
        if self.fallback_reason:
            payload["fallback_reason"] = self.fallback_reason
        if self.open_channels is not None:
            payload["open_channels"] = self.open_channels
        return payload


@dataclass(frozen=True, slots=True)
class UsbAudioContract:
    """Effective RX/TX OS audio contract for a USB-audio radio path."""

    rx: UsbAudioStreamContract | None = None
    tx: UsbAudioStreamContract | None = None

    def to_dict(self) -> dict[str, object]:
        return {
            "rx": self.rx.to_dict() if self.rx is not None else None,
            "tx": self.tx.to_dict() if self.tx is not None else None,
        }


def _safe_int(value: object, default: int = 0) -> int:
    try:
        if isinstance(value, (int, float, str, bytes, bytearray)):
            return int(value)
        return default
    except (TypeError, ValueError):
        return default


def _name_score(name: str) -> int:
    lowered = name.lower()
    for idx, pattern in enumerate(_USB_NAME_PATTERNS):
        if pattern in lowered:
            return idx
    return 99


def _format_device_choices(devices: list[UsbAudioDevice]) -> str:
    choices: list[str] = []
    for dev in sorted(devices, key=lambda item: (item.index, item.name.lower())):
        label = f"[{dev.index}] {dev.name}"
        if dev.platform_uid:
            label = f"{label} ({dev.platform_uid})"
        choices.append(label)
    return ", ".join(choices) or "<none>"


def _unique_override_match(
    matches: list[UsbAudioDevice],
    *,
    override: str,
    direction: str,
) -> UsbAudioDevice | None:
    if len(matches) == 1:
        return matches[0]
    if len(matches) > 1:
        available = _format_device_choices(matches)
        raise AudioDeviceSelectionError(
            f"Ambiguous {direction.upper()} device override {override!r}. "
            f"Matching {direction.upper()} devices: {available}"
        )
    return None


def _find_by_override(
    devices: list[UsbAudioDevice],
    *,
    override: str,
    direction: str,
) -> UsbAudioDevice:
    selector = override.strip()
    if not selector:
        raise AudioDeviceSelectionError(
            f"{direction.upper()} device override must be a non-empty string."
        )

    index_matches = [
        dev
        for dev in devices
        if selector in {str(dev.index), f"[{dev.index}]", f"index:{dev.index}"}
    ]
    match = _unique_override_match(
        index_matches,
        override=selector,
        direction=direction,
    )
    if match is not None:
        return match

    exact = [
        dev
        for dev in devices
        if dev.name == selector or (dev.platform_uid and dev.platform_uid == selector)
    ]
    match = _unique_override_match(
        exact,
        override=selector,
        direction=direction,
    )
    if match is not None:
        return match

    selector_ci = selector.lower()
    exact_ci = [
        dev
        for dev in devices
        if dev.name.lower() == selector_ci
        or (dev.platform_uid and dev.platform_uid.lower() == selector_ci)
    ]
    match = _unique_override_match(
        exact_ci,
        override=selector,
        direction=direction,
    )
    if match is not None:
        return match

    partial = [
        dev
        for dev in devices
        if selector_ci in dev.name.lower()
        or (dev.platform_uid and selector_ci in dev.platform_uid.lower())
    ]
    match = _unique_override_match(
        partial,
        override=selector,
        direction=direction,
    )
    if match is not None:
        return match

    available = _format_device_choices(devices)
    raise AudioDeviceSelectionError(
        f"Unknown {direction.upper()} device override {selector!r}. "
        f"Available {direction.upper()} devices: {available}"
    )


def _auto_pick(
    devices: list[UsbAudioDevice],
    *,
    direction: str,
) -> UsbAudioDevice:
    if direction == "rx":
        default_attr = "is_default_input"
    else:
        default_attr = "is_default_output"
    ranked = sorted(
        devices,
        key=lambda dev: (
            _name_score(dev.name),
            0 if getattr(dev, default_attr) else 1,
            dev.index,
            dev.name.lower(),
        ),
    )
    return ranked[0]


def select_usb_audio_devices(
    devices: list[UsbAudioDevice],
    *,
    rx_device: str | None = None,
    tx_device: str | None = None,
) -> tuple[UsbAudioDevice, UsbAudioDevice]:
    """Select RX/TX devices deterministically with override precedence."""
    rx_candidates = [dev for dev in devices if dev.supports_rx]
    tx_candidates = [dev for dev in devices if dev.supports_tx]
    duplex_candidates = [dev for dev in devices if dev.duplex]

    if not rx_candidates:
        raise AudioDeviceSelectionError("No suitable RX USB audio device was found.")
    if not tx_candidates:
        raise AudioDeviceSelectionError("No suitable TX USB audio device was found.")

    if rx_device is None and tx_device is None and duplex_candidates:
        selected = _auto_pick(duplex_candidates, direction="rx")
        return selected, selected

    selected_rx: UsbAudioDevice
    selected_tx: UsbAudioDevice

    if rx_device is not None:
        selected_rx = _find_by_override(
            rx_candidates, override=rx_device, direction="rx"
        )
    elif tx_device is not None:
        selected_tx = _find_by_override(
            tx_candidates, override=tx_device, direction="tx"
        )
        selected_rx = (
            selected_tx
            if selected_tx.supports_rx
            else _auto_pick(rx_candidates, direction="rx")
        )
    else:
        selected_rx = _auto_pick(rx_candidates, direction="rx")

    if tx_device is not None:
        selected_tx = _find_by_override(
            tx_candidates, override=tx_device, direction="tx"
        )
    elif rx_device is not None and selected_rx.supports_tx:
        selected_tx = selected_rx
    else:
        selected_tx = (
            selected_rx
            if selected_rx.supports_tx
            else _auto_pick(tx_candidates, direction="tx")
        )

    return selected_rx, selected_tx


def _get_uid_map() -> dict[str, str]:
    """Return CoreAudio name→UID map on macOS, empty dict elsewhere."""
    if sys.platform != "darwin":
        return {}
    try:
        from rigplane.audio._macos_uid import get_device_uid_map

        return get_device_uid_map()
    except Exception:
        logger.debug("CoreAudio UID lookup unavailable", exc_info=True)
        return {}


def _device_info_to_usb(
    info: AudioDeviceInfo,
    uid_map: dict[str, str],
) -> UsbAudioDevice:
    """Convert an :class:`AudioDeviceInfo` to a :class:`UsbAudioDevice`."""
    return UsbAudioDevice(
        index=int(info.id),
        name=info.name,
        input_channels=info.input_channels,
        output_channels=info.output_channels,
        default_samplerate=info.default_samplerate,
        is_default_input=info.is_default_input,
        is_default_output=info.is_default_output,
        platform_uid=(
            info.platform_uid
            or uid_map.get(info.name, "")
            or _platform_uid_from_device_name(info.name)
        ),
    )


def _devices_from_backend(backend: AudioBackend) -> list[UsbAudioDevice]:
    """List devices from a backend, enriching with platform UIDs."""
    uid_map = _get_uid_map()
    return [_device_info_to_usb(info, uid_map) for info in backend.list_devices()]


def list_usb_audio_devices(sounddevice_module: Any) -> list[UsbAudioDevice]:
    """Return normalized system audio devices.

    .. deprecated::
        Prefer :func:`_devices_from_backend` with an :class:`AudioBackend`.
        This function is kept for backward compatibility with CLI code.
    """
    raw_devices = list(sounddevice_module.query_devices())
    default_input_idx: int | None = None
    default_output_idx: int | None = None
    default_raw = getattr(getattr(sounddevice_module, "default", None), "device", None)
    if isinstance(default_raw, (list, tuple)) and len(default_raw) >= 2:
        default_input_idx = _safe_int(default_raw[0], default=-1)
        default_output_idx = _safe_int(default_raw[1], default=-1)

    uid_map = _get_uid_map()

    normalized: list[UsbAudioDevice] = []
    for idx, raw in enumerate(raw_devices):
        index = _safe_int(raw.get("index", idx), default=idx)
        name = str(raw.get("name", f"device-{index}"))
        normalized.append(
            UsbAudioDevice(
                index=index,
                name=name,
                input_channels=_safe_int(raw.get("max_input_channels")),
                output_channels=_safe_int(raw.get("max_output_channels")),
                default_samplerate=_safe_int(
                    raw.get("default_samplerate"), default=48_000
                ),
                is_default_input=(
                    default_input_idx is not None and index == default_input_idx
                ),
                is_default_output=(
                    default_output_idx is not None and index == default_output_idx
                ),
                platform_uid=uid_map.get(name, "")
                or _platform_uid_from_device_name(name),
            )
        )
    return normalized


def _extract_sounddevice_module(backend: AudioBackend) -> Any | None:
    """Extract the underlying sounddevice module from a PortAudioBackend."""
    if isinstance(backend, PortAudioBackend):
        return backend.sounddevice_module
    return None


def resolve_usb_duplex_mode(
    rx_dev: UsbAudioDevice,
    tx_dev: UsbAudioDevice,
) -> Literal["full", "exclusive"]:
    """Resolve the USB duplex policy for a selected RX/TX device pair.

    Returns ``"exclusive"`` iff the platform is macOS AND RX and TX resolve
    to the same device index AND the device is a real CODEC (not a virtual
    loopback such as the RigPlane Virtual Cable/VB-Cable/PipeWire). On macOS CoreAudio, two separate
    streams on one real C-Media USB CODEC fail with AUHAL -50 (MOR-531), so
    such a device must be owned exclusively by ONE full-duplex stream.
    Everything else — separate devices, virtual loopbacks, non-macOS
    platforms — supports the two-stream path: ``"full"``.

    Pure read-only policy (MOR-534, AudioTransport 1/12). Consumed via
    :attr:`UsbAudioDriver.duplex_mode`, which the backends expose as
    ``audio_duplex_mode`` for the AudioSession's setup-order sequencing.
    """
    if sys.platform != "darwin":
        return "full"
    if rx_dev.index != tx_dev.index:
        return "full"
    # Single source of truth with the bridge path: reuse (not move) the
    # virtual-loopback predicate. Imported lazily and called via the module
    # attribute so existing monkeypatch targets on ``rigplane.audio.bridge``
    # keep steering both consumers.
    from rigplane.audio import bridge

    info = AudioDeviceInfo(
        id=AudioDeviceId(rx_dev.index),
        name=rx_dev.name,
        input_channels=rx_dev.input_channels,
        output_channels=rx_dev.output_channels,
    )
    if bridge._is_virtual_loopback_device(info):
        return "full"
    return "exclusive"


class UsbAudioDriver:
    """Stateful USB audio driver with deterministic device selection.

    Delegates stream I/O to an :class:`AudioBackend` while retaining
    the USB-specific device selection heuristics and topology resolution.
    """

    _BYTES_PER_SAMPLE = 2  # s16le

    def __init__(
        self,
        config: AudioDeviceConfig | None = None,
        *,
        rx_device: str | None = None,
        tx_device: str | None = None,
        serial_port: str | None = None,
        sample_rate: int = 48_000,
        channels: int = 1,
        frame_ms: int = 20,
        backend: AudioBackend | None = None,
        rx_audio_channel: str = "mix",
        capture_open_timeout: float = _CAPTURE_OPEN_TIMEOUT_S,
    ) -> None:
        # ONE per-device config carrier (MOR-578). Callers either hand a
        # ready-made :class:`AudioDeviceConfig` or use the historical keyword
        # parameters, from which an identical carrier is built (back-compat:
        # when ``config`` is provided it is authoritative and the per-keyword
        # equivalents are ignored). ``serial_port`` (topology-resolution hint)
        # and ``backend`` (stream implementation) are not device config and
        # stay separate keywords.
        #
        # ``rx_audio_channel`` — stereo→mono downmix selection (MOR-508):
        # "mix" = (L+R)//2 (default, unchanged for every rig), "left"/"right"
        # = that channel at full level. Only consulted when a mono request
        # opens a stereo-native device (MOR-504 under-request downmix). The
        # FTX-1 sets "left" because its USB RX audio is on the LEFT channel
        # only.
        self._config = (
            config
            if config is not None
            else AudioDeviceConfig(
                rx_device=rx_device,
                tx_device=tx_device,
                sample_rate=sample_rate,
                channels=channels,
                frame_ms=frame_ms,
                rx_audio_channel=rx_audio_channel,
            )
        )
        self._serial_port = serial_port
        self._backend: AudioBackend = backend or PortAudioBackend()
        # MOR-1438: bound on how long an RX/TX stream open may block before
        # it is treated as stuck (see ``_open_stream``). Overridable so
        # tests can shrink it well below ``_CAPTURE_OPEN_TIMEOUT_S`` without
        # a real multi-second wait.
        self._capture_open_timeout = capture_open_timeout
        # MOR-2892: every bounded off-loop PortAudio operation submits to
        # the module-wide ``bounded_portaudio_pool`` (ONE executor, ONE
        # saturation counter — shared with ``AudioBridge``; see the pool
        # docstring and ``_CAPTURE_OPEN_MAX_WORKERS``). No per-instance
        # executor exists any more.

        self._selected_rx: UsbAudioDevice | None = None
        self._selection_generation = 0
        self._selected_tx: UsbAudioDevice | None = None

        self._rx_stream: RxStream | None = None
        self._tx_stream: TxStream | None = None
        self._stream_cleanup: set[str] = set()
        # Single full-duplex stream for the same-device RX+TX case (MOR-531):
        # opening separate InputStream + OutputStream on one C-Media CODEC fails
        # with macOS CoreAudio AUHAL -50. When set, it drives BOTH directions.
        self._duplex_stream: DuplexStream | None = None
        # MOR-546: the driver owns the RX callback across the exclusive
        # same-device handoff (plain RX <-> the one duplex stream). Every
        # stream is started on the stable ``_deliver_rx`` entry point;
        # ``_rx_callback`` is the CURRENT consumer callback (None = RX demand
        # unwired — frames drain and drop). The backends clear their OWN
        # copies before the session calls start_tx, so the callback must
        # live here for the handoff to keep frames flowing.
        self._rx_callback: Callable[[bytes], None] | None = None
        # MOR-2792: readable silent-now flag owned by ``_silence_watchdog``.
        self._rx_silent = False
        self._usb_audio_contract = UsbAudioContract()

        self._rx_lock = asyncio.Lock()
        self._tx_lock = asyncio.Lock()

    @property
    def rx_silent(self) -> bool:
        """True while RX capture has delivered only bit-exact zeros for the
        watchdog window (see ``_SILENCE_WARN_SECONDS``). Cleared by the next
        non-zero frame."""
        return self._rx_silent

    @property
    def rx_running(self) -> bool:
        if self._duplex_stream is not None:
            return (
                "_duplex_stream" not in self._stream_cleanup
                and self._duplex_stream.running
            )
        return (
            "_rx_stream" not in self._stream_cleanup
            and self._rx_stream is not None
            and self._rx_stream.running
        )

    @property
    def tx_running(self) -> bool:
        if self._duplex_stream is not None:
            return (
                "_duplex_stream" not in self._stream_cleanup
                and self._duplex_stream.running
            )
        return (
            "_tx_stream" not in self._stream_cleanup
            and self._tx_stream is not None
            and self._tx_stream.running
        )

    def set_serial_port(self, serial_port: str | None) -> None:
        """Rebind topology-based audio resolution to a new serial port.

        Used after serial-node rediscovery (MOR-1453) so the next audio
        resolution re-resolves RX/TX device indices against the radio's
        new (renumbered) device node instead of the one captured at
        construction. ``start_rx``/``start_tx`` resolve devices afresh, but
        ``duplex_mode`` and ``selected_rx_device``/``selected_tx_device``
        read the ``_selected_rx``/``_selected_tx`` cache directly without
        forcing a fresh resolution, so it is cleared here to avoid
        reporting a stale device from the old port between rediscovery
        and the next start call.
        """
        self._serial_port = serial_port
        self._selection_generation += 1
        self._selected_rx = None
        self._selected_tx = None

    @property
    def selected_rx_device(self) -> UsbAudioDevice | None:
        return self._selected_rx

    @property
    def selected_tx_device(self) -> UsbAudioDevice | None:
        return self._selected_tx

    @property
    def duplex_mode(self) -> Literal["full", "exclusive"]:
        """USB duplex policy for the resolved RX/TX pair (pure cache read).

        MOR-2892: resolving devices is PortAudio enumeration and must
        stay off the event loop, so the property never resolves — it
        only reads the selection cache warmed by the bounded start paths
        (:meth:`start_rx`/:meth:`start_tx`/:meth:`start_duplex`). A cold
        cache (before the first start, or right after
        :meth:`set_serial_port`) returns ``"full"`` — the same safe
        default the backend's ``audio_duplex_mode`` already degrades to.
        The exclusive topology is still enforced on every start: the
        bounded resolution there re-checks the resolved pair, and the
        RX → duplex handoff (:meth:`_start_tx_exclusive`) keeps an
        rx-first sequence safe on an exclusive device.
        """
        rx, tx = self._selected_rx, self._selected_tx
        if rx is None or tx is None:
            return "full"
        return resolve_usb_duplex_mode(rx, tx)

    @property
    def usb_audio_contract(self) -> UsbAudioContract | None:
        if self._usb_audio_contract.rx is None and self._usb_audio_contract.tx is None:
            return None
        return self._usb_audio_contract

    def _ensure_selected_devices(self) -> tuple[UsbAudioDevice, UsbAudioDevice]:
        devices = _devices_from_backend(self._backend)

        # If serial_port is set and no explicit rx/tx overrides, try
        # topology-based resolution to find the correct audio pair.
        if (
            self._serial_port
            and self._config.rx_device is None
            and self._config.tx_device is None
        ):
            resolved = self._try_resolve_from_serial(devices)
            if resolved is not None:
                self._selected_rx, self._selected_tx = resolved
                return resolved

        selected_rx, selected_tx = select_usb_audio_devices(
            devices,
            rx_device=self._config.rx_device,
            tx_device=self._config.tx_device,
        )
        self._selected_rx = selected_rx
        self._selected_tx = selected_tx
        return selected_rx, selected_tx

    async def _select_devices_bounded(
        self, *, direction: str
    ) -> tuple[UsbAudioDevice, UsbAudioDevice]:
        serial_port = self._serial_port
        if not (
            platform.system() == "Windows"
            and serial_port
            and self._config.rx_device is None
            and self._config.tx_device is None
        ):
            return await self._run_portaudio_bounded(
                self._ensure_selected_devices,
                what="device enumeration",
                direction=direction,
            )

        config = self._config
        generation = self._selection_generation
        deadline = time.monotonic() + self._capture_open_timeout
        self._selected_rx = None
        self._selected_tx = None

        def remaining() -> float:
            timeout = deadline - time.monotonic()
            if timeout <= 0:
                raise AudioCaptureOpenTimeoutError(
                    f"{direction.upper()} device enumeration exceeded "
                    "selection deadline."
                )
            return timeout

        devices = await bounded_portaudio_pool.run_bounded(
            lambda: _devices_from_backend(self._backend),
            what="device enumeration",
            direction=direction,
            timeout=remaining(),
        )
        from ._usb_resolve import _query_windows_pnp_devices

        def query() -> list[Any]:
            try:
                return list(_query_windows_pnp_devices())
            except Exception:  # noqa: BLE001 — optional metadata only
                logger.debug("usb-audio: optional Windows topology unavailable")
                return []

        records: list[Any] = []
        reserve = min(_WINDOWS_SELECTION_RESERVE_S, self._capture_open_timeout / 4)
        topology_timeout = min(_WINDOWS_PNP_TIMEOUT_S, remaining() - reserve)
        if topology_timeout > 0:
            try:
                records = await bounded_portaudio_pool.run_bounded(
                    query,
                    what="optional Windows topology",
                    direction=direction,
                    timeout=topology_timeout,
                    warn_on_timeout=False,
                )
            except AudioCaptureOpenTimeoutError:
                logger.info(
                    "usb-audio: optional Windows topology did not settle; "
                    "requiring an unambiguous audio pair"
                )
        selected = await bounded_portaudio_pool.run_bounded(
            lambda: self._select_windows_devices(devices, records, serial_port),
            what="device enumeration",
            direction=direction,
            timeout=remaining(),
        )
        remaining()
        if (
            generation != self._selection_generation
            or serial_port != self._serial_port
            or config is not self._config
        ):
            raise AudioDriverLifecycleError("USB audio selection context changed.")
        self._selected_rx, self._selected_tx = selected
        return selected

    def _select_windows_devices(
        self,
        devices: list[UsbAudioDevice],
        records: list[Any],
        serial_port: str,
    ) -> tuple[UsbAudioDevice, UsbAudioDevice]:
        from ._usb_resolve import WindowsAudioTopologyError, _resolve_windows

        sd_module = _extract_sounddevice_module(self._backend)
        if records:
            if any(r.com_port for r in records) and not any(
                r.com_port and r.com_port.strip().upper() == serial_port.strip().upper()
                for r in records
            ):
                raise AudioDeviceSelectionError(
                    "Windows USB topology does not include the selected serial radio."
                )
            try:
                mapping = _resolve_windows(
                    serial_port,
                    sounddevice_module=(
                        sd_module
                        if sd_module is not None
                        else SimpleNamespace(query_devices=lambda: [])
                    ),
                    pnp_query=lambda: records,
                )
            except WindowsAudioTopologyError as exc:
                raise AudioDeviceSelectionError(str(exc)) from exc
            if mapping is not None:
                rx = next(
                    (d for d in devices if d.index == mapping.rx_device_index), None
                )
                tx = next(
                    (d for d in devices if d.index == mapping.tx_device_index), None
                )
                if rx is None or tx is None or not rx.supports_rx or not tx.supports_tx:
                    raise AudioDeviceSelectionError(
                        "Windows USB topology does not match enumerated audio devices."
                    )
                return rx, tx
        candidates = [d for d in devices if _name_score(d.name) < 99]
        for direction in ("rx", "tx"):
            identities = {
                ("uid", d.platform_uid) if d.platform_uid else ("index", d.index)
                for d in candidates
                if (d.supports_rx if direction == "rx" else d.supports_tx)
            }
            if len(identities) != 1:
                raise AudioDeviceSelectionError(
                    "Windows USB audio selection is ambiguous or unavailable; "
                    "select explicit RX/TX devices for the serial radio."
                )
        return select_usb_audio_devices(candidates)

    def _try_resolve_from_serial(
        self,
        devices: list[UsbAudioDevice],
    ) -> tuple[UsbAudioDevice, UsbAudioDevice] | None:
        """Attempt topology-based audio device resolution from serial port."""
        sd_module = _extract_sounddevice_module(self._backend)
        if sd_module is None:
            logger.debug(
                "usb-audio: topology resolution skipped — backend %s "
                "is not PortAudioBackend; falling back to name-based selection",
                type(self._backend).__name__,
            )
            return None

        from ..usb_audio_resolve import resolve_audio_for_serial_port

        mapping = resolve_audio_for_serial_port(
            self._serial_port,  # type: ignore[arg-type]
            sounddevice_module=sd_module,
        )
        if mapping is None:
            return None

        rx_dev = next((d for d in devices if d.index == mapping.rx_device_index), None)
        tx_dev = next((d for d in devices if d.index == mapping.tx_device_index), None)
        if rx_dev is None or tx_dev is None:
            logger.warning(
                "usb-audio: topology resolved indices [%d, %d] but devices "
                "not found in normalized list",
                mapping.rx_device_index,
                mapping.tx_device_index,
            )
            return None

        logger.info(
            "usb-audio: topology-resolved devices for %s: RX=[%d] %s, TX=[%d] %s",
            self._serial_port,
            rx_dev.index,
            rx_dev.name,
            tx_dev.index,
            tx_dev.name,
        )
        return rx_dev, tx_dev

    def list_devices(self) -> list[UsbAudioDevice]:
        """List normalized devices from the active audio backend."""
        return _devices_from_backend(self._backend)

    def _sample_rate_candidates(self, requested: int) -> tuple[int, ...]:
        return tuple(dict.fromkeys((requested, *_DEFAULT_SAMPLE_RATE_CANDIDATES)))

    def _clamp_channels(
        self,
        *,
        direction: str,
        device: UsbAudioDevice,
        requested_channels: int,
    ) -> tuple[int, int, str, str | None]:
        """Reconcile requested channels with the device's real capability.

        Returns ``(deliver_channels, open_channels, channel_source,
        fallback_reason)``:

        - ``deliver_channels`` — the count delivered downstream (the broadcaster
          / FFT scope contract is 48 kHz mono).
        - ``open_channels`` — the count the OS stream is opened at.

        Two reconciliation directions, both healing a codec/device mismatch that
        PortAudio otherwise rejects with PaErrorCode -9998 ("Invalid number of
        channels") on Windows, or AUHAL -10863 on macOS CoreAudio:

        - **Over-request (MOR-238)** — a profile / codec asks for MORE channels
          than the device exposes (stereo request on a mono capture endpoint).
          Both open and deliver clamp DOWN to ``device_max``; downstream is mono,
          so a 1-channel capture feeds the broadcaster cleanly.
        - **Under-request (MOR-504)** — a mono (deliver=1) request on a
          stereo-NATIVE device. macOS AUHAL refuses to open a 2-channel device at
          1 channel (err -10863), so the stream opens at the device-native count
          and the RX stream software-downmixes back to mono before delivery. RX
          path only (TX never up-opens — there is no mix-up direction for
          playback).
        """

        device_max = (
            device.input_channels if direction == "rx" else device.output_channels
        )
        # A device advertising zero channels for this direction is a selection
        # bug, not a reconcile target — leave the request untouched and let the
        # open surface the real error.
        if device_max < 1:
            return requested_channels, requested_channels, "requested", None
        if requested_channels > device_max:
            reason = f"channels-{requested_channels}-clamped-to-device-{device_max}"
            return device_max, device_max, "device-clamp", reason
        if direction == "rx" and requested_channels < device_max:
            # Open native, deliver the (mono) request via software downmix.
            reason = (
                f"channels-{requested_channels}-opened-as-device-{device_max}-downmix"
            )
            return requested_channels, device_max, "device-native", reason
        return requested_channels, requested_channels, "requested", None

    async def _resolve_stream_contract(
        self,
        *,
        direction: str,
        device: UsbAudioDevice,
        requested_sample_rate: int,
        channels: int,
        frame_ms: int,
        allow_sample_rate_fallback: bool,
    ) -> UsbAudioStreamContract:
        """Resolve the stream contract, probing formats off the loop.

        ``check_sample_rate`` is the ``Pa_IsFormatSupported`` format probe —
        the exact call a pending macOS microphone-permission prompt froze
        the whole stand server with (MOR-2892) — so every probe goes
        through ``_run_portaudio_bounded``.
        """
        device_id = AudioDeviceId(device.index)
        (
            deliver_channels,
            open_channels,
            channel_source,
            channel_reason,
        ) = self._clamp_channels(
            direction=direction,
            device=device,
            requested_channels=channels,
        )
        # Only carry ``open_channels`` when it differs from the delivered count
        # (under-request downmix); otherwise the OS stream opens at ``channels``.
        open_field = open_channels if open_channels != deliver_channels else None
        if await self._run_portaudio_bounded(
            lambda: self._backend.check_sample_rate(
                device_id,
                requested_sample_rate,
                direction=direction,
            ),
            what="format probe",
            direction=direction,
        ):
            source = "default" if allow_sample_rate_fallback else "explicit"
            return UsbAudioStreamContract(
                direction=direction,
                device=device,
                sample_rate_hz=requested_sample_rate,
                channels=deliver_channels,
                frame_ms=frame_ms,
                sample_rate_source=source,
                channel_source=channel_source,
                fallback_reason=channel_reason,
                open_channels=open_field,
            )

        if not allow_sample_rate_fallback:
            raise AudioDriverLifecycleError(
                f"Explicit {direction.upper()} sample rate "
                f"{requested_sample_rate} Hz is not supported by [{device.index}] "
                f"{device.name}."
            )

        for candidate in self._sample_rate_candidates(requested_sample_rate):
            if candidate == requested_sample_rate:
                continue
            if await self._run_portaudio_bounded(
                lambda candidate=candidate: self._backend.check_sample_rate(
                    device_id,
                    candidate,
                    direction=direction,
                ),
                what="format probe",
                direction=direction,
            ):
                sample_reason = f"sample-rate-{requested_sample_rate}-unsupported"
                fallback_reason = (
                    f"{sample_reason}; {channel_reason}"
                    if channel_reason
                    else sample_reason
                )
                return UsbAudioStreamContract(
                    direction=direction,
                    device=device,
                    sample_rate_hz=candidate,
                    channels=deliver_channels,
                    frame_ms=frame_ms,
                    sample_rate_source="fallback",
                    channel_source=channel_source,
                    fallback_reason=fallback_reason,
                    open_channels=open_field,
                )

        raise AudioDriverLifecycleError(
            f"No supported {direction.upper()} sample rate found for [{device.index}] "
            f"{device.name}; tried {list(self._sample_rate_candidates(requested_sample_rate))}."
        )

    def _silence_watchdog(
        self, callback: Callable[[bytes], None], frame_ms: int
    ) -> Callable[[bytes], None]:
        """Wrap *callback* to warn once on sustained bit-exact RX silence.

        macOS silently hands microphone-unpermissioned capture contexts
        (e.g. ssh-spawned processes) all-zero CoreAudio frames: the stream
        opens fine and frames keep flowing, but every sample is exactly 0 —
        indistinguishable from a live capture of true silence without this
        check. Tracks consecutive all-zero frames with a plain counter (no
        new timer); warns once after ``_SILENCE_WARN_SECONDS``, then logs
        one INFO on recovery and re-arms for the next silent stretch.
        """
        threshold = max(1, -(-(_SILENCE_WARN_SECONDS * 1000) // max(1, frame_ms)))
        state = {"silent_frames": 0, "warned": False}
        self._rx_silent = False

        def _watchdog(frame: bytes) -> None:
            if _is_silent_frame(frame):
                state["silent_frames"] += 1
                if state["silent_frames"] == threshold and not state["warned"]:
                    state["warned"] = True
                    self._rx_silent = True
                    logger.warning(
                        "usb-audio: RX capture has delivered only digital "
                        "silence for ~%ds — the input may lack OS capture "
                        "permission (macOS: grant Microphone to the "
                        "launching app/context) or the radio's USB audio "
                        "output may be off",
                        _SILENCE_WARN_SECONDS,
                    )
            else:
                if state["warned"]:
                    logger.info("RX audio signal detected")
                state["silent_frames"] = 0
                state["warned"] = False
                self._rx_silent = False
            callback(frame)

        return _watchdog

    @property
    def _inflight_opens(self) -> int:
        """Read-only alias for the shared pool's pressure (MOR-2892)."""
        return bounded_portaudio_pool.inflight

    async def _open_stream(
        self,
        stream: RxStream | TxStream | DuplexStream,
        start_coro: "Coroutine[Any, Any, None]",
        *,
        direction: str,
    ) -> None:
        """Open *stream* off the event loop, bounded by ``_capture_open_timeout``.

        Moves the stream's ``start()`` — a genuinely blocking OS-level
        device open on the real PortAudio backend — to a worker thread
        so a stuck open (bench-observed: a macOS TCC microphone-consent
        prompt that never renders, MOR-1420) cannot freeze the caller's
        event loop (MOR-1438). On timeout: logs one actionable warning
        and raises :class:`AudioCaptureOpenTimeoutError`; the background
        open keeps running and its late-arriving handle is closed (see
        ``bounded_portaudio_pool.open_stream_bounded`` / ``abandon_open``
        / ``close_late_stream`` for the mechanics, including the MOR-1573
        pool-saturation fail-fast and the MOR-1438 F1 cancellation
        handling).
        """
        await bounded_portaudio_pool.open_stream_bounded(
            stream,
            start_coro,
            direction=direction,
            timeout=self._capture_open_timeout,
        )

    async def _run_portaudio_bounded(
        self,
        fn: Callable[[], Any],
        *,
        what: str,
        direction: str,
    ) -> Any:
        """Run a PortAudio-touching call off the loop, bounded (MOR-2892).

        Stand incident (2026-09-28): a pending macOS microphone-permission
        prompt (TCC) blocked ``Pa_IsFormatSupported`` — the RX relay's
        format probe — ON the event-loop thread, freezing the whole server
        until SIGKILL. Every PortAudio call reachable from the web server
        now goes through here (or through ``_open_stream`` for stream
        opens): executed on the shared bounded worker pool and awaited no
        longer than ``capture_open_timeout``. See
        ``bounded_portaudio_pool.run_bounded`` for the full contract
        (saturation fail-fast, operator-readable timeout warning, honest
        failure while the server keeps answering HTTP and WebSockets).
        """
        return await bounded_portaudio_pool.run_bounded(
            fn,
            what=what,
            direction=direction,
            timeout=self._capture_open_timeout,
        )

    async def _stop_stream_bounded(
        self,
        stream: RxStream | TxStream | DuplexStream,
        *,
        direction: str,
    ) -> None:
        """Stop/close *stream* off the loop, bounded (MOR-2892).

        A real ``stream.stop()`` is a synchronous Pa_StopStream +
        Pa_CloseStream pair — the same blocking call class this ticket
        keeps off the loop. ``close_late_stream`` already drives the
        ABANDONED-handle close off-loop (MOR-1438 F2); this covers every
        ordinary stop path (``stop_rx``/``stop_tx``/``stop_duplex`` and
        the exclusive-handoff yield).
        """
        await bounded_portaudio_pool.stop_stream_bounded(
            stream,
            direction=direction,
            timeout=self._capture_open_timeout,
        )

    async def _close_owned_stream(
        self,
        slot: Literal["_rx_stream", "_tx_stream", "_duplex_stream"],
        *,
        direction: str,
    ) -> None:
        """Caller holds the direction lock; ownership ends only after close.

        ``running`` can already be false after a refused native close. That
        does not mean the native handle or its MTA lease was released.
        """
        stream = getattr(self, slot)
        if stream is not None:
            # Keep ownership without advertising a close-pending handle as
            # available, even while its native stop is still blocked.
            self._stream_cleanup.add(slot)
            await self._stop_stream_bounded(stream, direction=direction)
            if getattr(self, slot) is stream:
                setattr(self, slot, None)
                self._stream_cleanup.discard(slot)

    def _store_stream_contract(self, contract: UsbAudioStreamContract) -> None:
        if contract.direction == "rx":
            self._usb_audio_contract = UsbAudioContract(
                rx=contract,
                tx=self._usb_audio_contract.tx,
            )
        else:
            self._usb_audio_contract = UsbAudioContract(
                rx=self._usb_audio_contract.rx,
                tx=contract,
            )

    def _deliver_rx(self, frame: bytes) -> None:
        """Stable stream-facing RX entry point (MOR-546).

        Registered (watchdog-wrapped) with whichever stream currently
        carries RX — the plain input stream or the exclusive same-device
        duplex stream — so the RX ↔ duplex handoff never re-wires the
        stream side. Reads the CURRENT user callback each frame: while
        none is wired (TX armed before its RX leg joined, or after
        ``stop_rx`` during duplex) frames are drained and dropped.
        """
        callback = self._rx_callback
        if callback is not None:
            callback(frame)

    async def start_rx(
        self,
        callback: Callable[[bytes], None] | None = None,
        *,
        sample_rate: int | None = None,
        channels: int | None = None,
        frame_ms: int | None = None,
        allow_sample_rate_fallback: bool = True,
    ) -> None:
        """Start capture loop and deliver PCM frames to callback.

        MOR-546: while the exclusive same-device duplex stream carries the
        TX leg, RX JOINS that stream — only the driver-owned RX callback
        (:attr:`_rx_callback`) is re-pointed; no second input stream is
        opened on the device (a second stream on one macOS USB CODEC fails
        with AUHAL -50).
        """
        if callback is None:
            raise AudioDriverLifecycleError("Audio RX callback is required.")
        if not callable(callback):
            raise TypeError("Audio RX callback must be callable.")

        async with self._rx_lock:
            if self._duplex_stream is not None and not self.tx_running:
                await self._close_owned_stream("_duplex_stream", direction="duplex")
            if self._duplex_stream is not None:
                # Exclusive same-device: the duplex stream already carries
                # the RX leg — joining re-points the driver-owned callback.
                if self._rx_callback is not None:
                    raise AudioAlreadyStartedError("RX stream already started.")
                self._rx_callback = callback
                logger.info("usb-audio: RX joined the running duplex stream")
                return
            if self.rx_running:
                raise AudioAlreadyStartedError("RX stream already started.")
            self._rx_callback = callback
            try:
                await self._open_rx_stream_locked(
                    sample_rate=sample_rate,
                    channels=channels,
                    frame_ms=frame_ms,
                    allow_sample_rate_fallback=allow_sample_rate_fallback,
                )
            except BaseException:
                self._rx_callback = None
                raise

    async def _open_rx_stream_locked(
        self,
        *,
        sample_rate: int | None,
        channels: int | None,
        frame_ms: int | None,
        allow_sample_rate_fallback: bool,
    ) -> None:
        """Open the plain RX input stream; caller holds ``_rx_lock``.

        ``_rx_callback`` must already be wired: the stream starts on the
        stable :meth:`_deliver_rx` entry point so a later exclusive duplex
        handoff (MOR-546) keeps delivering to it.
        """
        await self._close_owned_stream("_rx_stream", direction="rx")
        if self._duplex_stream is not None and not self.tx_running:
            await self._close_owned_stream("_duplex_stream", direction="duplex")
        selected_rx, _ = await self._select_devices_bounded(direction="rx")
        sr = self._config.sample_rate if sample_rate is None else sample_rate
        ch = self._config.channels if channels is None else channels
        fm = self._config.frame_ms if frame_ms is None else frame_ms
        if (sr * fm) % 1000 != 0:
            raise AudioDriverLifecycleError(
                "Invalid RX frame format: sample_rate * frame_ms must be divisible by 1000."
            )
        contract = await self._resolve_stream_contract(
            direction="rx",
            device=selected_rx,
            requested_sample_rate=sr,
            channels=ch,
            frame_ms=fm,
            allow_sample_rate_fallback=allow_sample_rate_fallback,
        )
        # Log the effective capture request before opening the
        # InputStream. ``input_channels`` is included because a codec /
        # device channel-count mismatch is the failure mode behind
        # MOR-236: requesting more channels than the mono USB CODEC
        # exposes makes PortAudio reject the stream with "Invalid number
        # of channels" (PaErrorCode -9998), which previously surfaced
        # only as an opaque "audio-bus: failed to start RX" with zero
        # RX frames reaching the browser.
        logger.info(
            "usb-audio: opening RX capture — device=[%d] %s, %d Hz, "
            "open %d ch / deliver %d ch (requested %d, source=%s), %d ms "
            "(device input_channels=%d)",
            selected_rx.index,
            selected_rx.name,
            contract.sample_rate_hz,
            contract.effective_open_channels,
            contract.channels,
            ch,
            contract.channel_source,
            fm,
            selected_rx.input_channels,
        )
        stream = self._backend.open_rx(
            AudioDeviceId(selected_rx.index),
            sample_rate=contract.sample_rate_hz,
            channels=contract.effective_open_channels,
            frame_ms=fm,
            deliver_channels=contract.channels,
            rx_audio_channel=self._config.rx_audio_channel,
        )
        self._rx_stream = stream
        try:
            await self._open_stream(
                stream,
                stream.start(self._silence_watchdog(self._deliver_rx, fm)),
                direction="rx",
            )
        except (AudioCaptureOpenTimeoutError, asyncio.CancelledError):
            # Never leave a stuck-open handle wired up as "the" RX
            # stream (MOR-1438): the next start_rx() must create a
            # fresh one rather than observe this abandoned attempt
            # flip ``running`` True behind its back. Cancellation
            # (F1) gets the same treatment as a timeout — both leave
            # the background open running unattended.
            self._rx_stream = None
            raise
        self._store_stream_contract(contract)
        logger.info(
            "usb-audio: RX capture running — device=[%d] %s",
            selected_rx.index,
            selected_rx.name,
        )

    async def _reopen_plain_rx_locked(self, *, reason: str) -> None:
        """Best-effort return to plain RX; caller holds ``_rx_lock``.

        Shared by the two duplex → plain-RX transitions — ``stop_tx``
        after a duplex teardown and ``_start_tx_exclusive`` after a
        failed duplex open: the still-wired ``_rx_callback`` gets a
        fresh plain stream when possible. An ordinary failure is
        logged, never raised (the RX demand stays armed for the next
        ``start_rx`` — the honest-downgrade retry path, MOR-582); a
        cancellation propagates.
        """
        if self._rx_callback is None or self.rx_running:
            return
        try:
            await self._open_rx_stream_locked(
                sample_rate=None,
                channels=None,
                frame_ms=None,
                allow_sample_rate_fallback=True,
            )
        except Exception:
            logger.warning(
                "usb-audio: failed to return to plain RX after %s — RX "
                "demand stays armed for the next start_rx",
                reason,
                exc_info=True,
            )

    async def stop_rx(self) -> None:
        """Stop capture loop and close RX stream.

        MOR-546: while the exclusive same-device duplex stream runs, the
        TX leg owns the device — ``stop_rx`` only unwires the driver-owned
        RX callback (frames drain); :meth:`stop_tx` owns the duplex
        teardown.
        """
        async with self._rx_lock:
            self._rx_callback = None
            if self._duplex_stream is not None and self.tx_running:
                return
            await self._close_owned_stream("_duplex_stream", direction="duplex")
            await self._close_owned_stream("_rx_stream", direction="rx")

    async def start_tx(
        self,
        *,
        sample_rate: int | None = None,
        channels: int | None = None,
        frame_ms: int | None = None,
        allow_sample_rate_fallback: bool = True,
    ) -> None:
        """Start playback loop for outgoing PCM frames.

        MOR-546: when the duplex policy is ``exclusive`` (macOS, RX and TX
        resolved to the same physical USB CODEC) the TX arm moves BOTH
        legs to ONE duplex stream via :meth:`_start_tx_exclusive` instead
        of opening a second OutputStream on that device. Separate-device
        (``full``) behaviour below is unchanged.

        MOR-2892: ``duplex_mode`` is a pure cache read, so the devices
        are resolved through the bounded off-loop path BEFORE the policy
        is consulted — the decision never triggers on-loop enumeration,
        and a cold cache cannot silently take the two-stream path on an
        exclusive device.
        """
        selected_rx, selected_tx = await self._select_devices_bounded(direction="tx")
        if resolve_usb_duplex_mode(selected_rx, selected_tx) == "exclusive":
            await self._start_tx_exclusive(
                sample_rate=sample_rate,
                channels=channels,
                frame_ms=frame_ms,
                allow_sample_rate_fallback=allow_sample_rate_fallback,
                selected=(selected_rx, selected_tx),
            )
            return
        async with self._tx_lock:
            if self.tx_running:
                raise AudioAlreadyStartedError("TX stream already started.")
            await self._close_owned_stream("_tx_stream", direction="tx")
            if self._duplex_stream is not None and not self.tx_running:
                await self._close_owned_stream("_duplex_stream", direction="duplex")

            sr = self._config.sample_rate if sample_rate is None else sample_rate
            ch = self._config.channels if channels is None else channels
            fm = self._config.frame_ms if frame_ms is None else frame_ms
            if (sr * fm) % 1000 != 0:
                raise AudioDriverLifecycleError(
                    "Invalid TX frame format: sample_rate * frame_ms must be divisible by 1000."
                )
            contract = await self._resolve_stream_contract(
                direction="tx",
                device=selected_tx,
                requested_sample_rate=sr,
                channels=ch,
                frame_ms=fm,
                allow_sample_rate_fallback=allow_sample_rate_fallback,
            )
            stream = self._backend.open_tx(
                AudioDeviceId(selected_tx.index),
                sample_rate=contract.sample_rate_hz,
                channels=contract.channels,
                frame_ms=fm,
            )
            self._tx_stream = stream
            try:
                # TX opens share the RX blocking-open shape (a synchronous
                # OutputStream construct + .start() on the real backend) so
                # they get the same off-loop + bounded-timeout treatment
                # (MOR-1438), even though the specific bench trigger — a
                # macOS TCC microphone-consent prompt — is RX/input-only.
                await self._open_stream(stream, stream.start(), direction="tx")
            except (AudioCaptureOpenTimeoutError, asyncio.CancelledError):
                self._tx_stream = None
                raise
            self._store_stream_contract(contract)

    async def start_duplex(
        self,
        callback: Callable[[bytes], None] | None = None,
        *,
        sample_rate: int | None = None,
        channels: int | None = None,
        frame_ms: int | None = None,
        allow_sample_rate_fallback: bool = True,
    ) -> None:
        """Start a single full-duplex RX+TX stream on the SAME USB CODEC.

        Opens ONE ``sd.Stream`` via :meth:`AudioBackend.open_duplex` when RX and
        TX resolve to the same device, so a USB-CODEC radio can transmit while RX
        capture keeps running — avoiding the macOS CoreAudio AUHAL -50 that two
        separate streams cause on one C-Media device (MOR-531). RX frames go to
        *callback*; TX frames are pushed via :meth:`_push_tx_pcm` (routed through
        the duplex stream's TX queue). The two-stream :meth:`start_rx` /
        :meth:`start_tx` path is unchanged for separate-device use.

        MOR-546: *callback* is kept by the driver (:attr:`_rx_callback`)
        and reached through the stable :meth:`_deliver_rx` entry point, so
        the exclusive :meth:`start_tx` arm and this method share the same
        stream core (:meth:`_open_duplex_stream_locked`).
        """
        if callback is None:
            raise AudioDriverLifecycleError("Audio RX callback is required.")
        if not callable(callback):
            raise TypeError("Audio RX callback must be callable.")

        async with self._rx_lock, self._tx_lock:
            if self.rx_running or self.tx_running:
                raise AudioDriverLifecycleError(
                    "Duplex stream requires both RX and TX idle."
                )
            await self._close_owned_stream("_rx_stream", direction="rx")
            await self._close_owned_stream("_tx_stream", direction="tx")
            previous_callback = self._rx_callback
            self._rx_callback = callback
            try:
                await self._open_duplex_stream_locked(
                    sample_rate=sample_rate,
                    channels=channels,
                    frame_ms=frame_ms,
                    allow_sample_rate_fallback=allow_sample_rate_fallback,
                )
            except BaseException:
                self._rx_callback = previous_callback
                raise

    async def _start_tx_exclusive(
        self,
        *,
        sample_rate: int | None,
        channels: int | None,
        frame_ms: int | None,
        allow_sample_rate_fallback: bool,
        selected: tuple[UsbAudioDevice, UsbAudioDevice],
    ) -> None:
        """Arm TX on an exclusive same-device CODEC as ONE duplex stream (MOR-546).

        A second OutputStream on the same USB CODEC kills the running
        capture (macOS CoreAudio AUHAL -50 — the live MOR-546 FTX-1
        defect), so the TX leg opens as a single full-duplex stream. A
        live plain RX stream yields the device first; RX delivery resumes
        on the SAME driver-owned callback (:attr:`_rx_callback`), so
        consumers keep their wiring and frames keep flowing. When the
        duplex open fails, a still-wired RX demand gets its plain stream
        back on a best-effort basis (:meth:`_reopen_plain_rx_locked` —
        logged, never raised; a cancel propagates) before the original
        TX failure reaches the caller. Audio only — no PTT/TX command is
        involved here.

        *selected* is the already-resolved device pair from
        :meth:`start_tx`'s bounded enumeration (MOR-2892) — reusing it
        keeps the exclusive arm at exactly ONE enumeration per start.
        """
        async with self._rx_lock, self._tx_lock:
            if self.tx_running:
                raise AudioAlreadyStartedError("TX stream already started.")
            # A live plain RX stream yields the device; its callback is
            # kept in ``_rx_callback`` and resumes on the duplex stream.
            await self._close_owned_stream("_rx_stream", direction="rx")
            await self._close_owned_stream("_tx_stream", direction="tx")
            try:
                await self._open_duplex_stream_locked(
                    sample_rate=sample_rate,
                    channels=channels,
                    frame_ms=frame_ms,
                    allow_sample_rate_fallback=allow_sample_rate_fallback,
                    selected=selected,
                )
            except BaseException:
                # The failed arm already stopped plain RX — hand a
                # still-wired RX demand its plain stream back (the same
                # best-effort contract as ``stop_tx``) before the caller
                # sees TX fail.
                await self._reopen_plain_rx_locked(reason="the failed duplex open")
                raise

    async def _open_duplex_stream_locked(
        self,
        *,
        sample_rate: int | None,
        channels: int | None,
        frame_ms: int | None,
        allow_sample_rate_fallback: bool,
        selected: tuple[UsbAudioDevice, UsbAudioDevice] | None = None,
    ) -> None:
        """Open the single full-duplex stream; caller holds BOTH locks.

        RX frames are delivered through the stable :meth:`_deliver_rx`
        entry point, so the driver-owned :attr:`_rx_callback` (None = TX
        armed without RX demand — frames drain) survives every later
        handoff (MOR-546).

        *selected* carries an already-resolved device pair when the
        caller just ran the bounded enumeration (the exclusive
        :meth:`start_tx` arm, MOR-2892); ``None`` resolves here as
        before (e.g. :meth:`start_duplex`).
        """
        await self._close_owned_stream("_duplex_stream", direction="duplex")
        if selected is None:
            selected = await self._select_devices_bounded(direction="duplex")
        selected_rx, selected_tx = selected
        if selected_rx.index != selected_tx.index:
            raise AudioDriverLifecycleError(
                "Duplex stream requires RX and TX on the SAME device "
                f"(got RX=[{selected_rx.index}] {selected_rx.name}, "
                f"TX=[{selected_tx.index}] {selected_tx.name}); "
                "use start_rx/start_tx for separate devices."
            )

        sr = self._config.sample_rate if sample_rate is None else sample_rate
        ch = self._config.channels if channels is None else channels
        fm = self._config.frame_ms if frame_ms is None else frame_ms
        if (sr * fm) % 1000 != 0:
            raise AudioDriverLifecycleError(
                "Invalid duplex frame format: sample_rate * frame_ms must "
                "be divisible by 1000."
            )

        rx_contract = await self._resolve_stream_contract(
            direction="rx",
            device=selected_rx,
            requested_sample_rate=sr,
            channels=ch,
            frame_ms=fm,
            allow_sample_rate_fallback=allow_sample_rate_fallback,
        )
        tx_contract = await self._resolve_stream_contract(
            direction="tx",
            device=selected_tx,
            requested_sample_rate=rx_contract.sample_rate_hz,
            channels=ch,
            frame_ms=fm,
            allow_sample_rate_fallback=False,
        )
        logger.info(
            "usb-audio: opening DUPLEX — device=[%d] %s, %d Hz, RX open %d ch "
            "/ deliver %d ch, TX %d ch, %d ms",
            selected_rx.index,
            selected_rx.name,
            rx_contract.sample_rate_hz,
            rx_contract.effective_open_channels,
            rx_contract.channels,
            tx_contract.channels,
            fm,
        )
        stream = self._backend.open_duplex(
            AudioDeviceId(selected_rx.index),
            sample_rate=rx_contract.sample_rate_hz,
            channels=rx_contract.effective_open_channels,
            frame_ms=fm,
            deliver_channels=rx_contract.channels,
            rx_audio_channel=self._config.rx_audio_channel,
            tx_channels=tx_contract.channels,
        )
        self._duplex_stream = stream
        try:
            # MOR-1573: duplex opens get the SAME off-loop + bounded-
            # timeout treatment as RX/TX (MOR-1438) — a stuck duplex
            # open is the same blocking OS-level device open as either
            # single-direction stream, just on the shared CODEC.
            await self._open_stream(
                stream,
                stream.start(self._silence_watchdog(self._deliver_rx, fm)),
                direction="duplex",
            )
        except (AudioCaptureOpenTimeoutError, asyncio.CancelledError):
            # The pool owns abandoned opens and their late cleanup. Ordinary
            # failures retain this slot until successful native close.
            self._duplex_stream = None
            raise
        self._store_stream_contract(rx_contract)
        self._store_stream_contract(tx_contract)
        logger.info(
            "usb-audio: DUPLEX running — device=[%d] %s",
            selected_rx.index,
            selected_rx.name,
        )

    async def stop_duplex(self) -> None:
        """Stop and close the full-duplex stream."""
        async with self._rx_lock, self._tx_lock:
            await self._close_owned_stream("_duplex_stream", direction="duplex")

    async def _push_tx_pcm(self, frame: bytes) -> None:
        """Queue one PCM frame for playback."""
        if not self.tx_running:
            raise AudioNotStartedError("Audio TX stream is not started.")
        if not isinstance(frame, (bytes, bytearray, memoryview)):
            raise TypeError("PCM TX frame must be bytes-like.")
        # Same-device duplex: TX rides the single stream's TX queue.
        if self._duplex_stream is not None:
            await self._duplex_stream.write(bytes(frame))
            return
        assert self._tx_stream is not None
        await self._tx_stream.write(bytes(frame))

    async def stop_tx(self) -> None:
        """Stop playback loop and close TX stream.

        MOR-546: in exclusive same-device mode the TX leg IS the duplex
        stream — closing it returns the device to plain RX capture when an
        RX callback is still wired (:meth:`stop_rx` unwires it first).
        Separate-device (``full``) behaviour is unchanged.
        """
        async with self._rx_lock, self._tx_lock:
            duplex_stream = self._duplex_stream
            if duplex_stream is None:
                await self._close_owned_stream("_tx_stream", direction="tx")
                return
            await self._close_owned_stream("_duplex_stream", direction="duplex")
            await self._reopen_plain_rx_locked(reason="the duplex teardown")


__all__ = [
    "AudioAlreadyStartedError",
    "AudioCaptureOpenTimeoutError",
    "AudioDeviceConfig",
    "AudioDeviceSelectionError",
    "AudioDriverLifecycleError",
    "AudioNotStartedError",
    "UsbAudioDevice",
    "UsbAudioContract",
    "UsbAudioDriver",
    "UsbAudioStreamContract",
    "list_usb_audio_devices",
    "resolve_usb_duplex_mode",
    "select_usb_audio_devices",
]
