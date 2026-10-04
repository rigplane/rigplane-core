"""Shared base class for Icom serial (USB CI-V) radio backends.

Consolidates the connection lifecycle, audio plumbing, scope guardrails,
CIV watchdog, and soft-reconnect logic that is identical across all
serial-backed radios (IC-705, IC-7300, IC-9700, IC-7610).
"""

from __future__ import annotations

import asyncio
import fnmatch
import logging
import math
import os
import re
import time
from collections.abc import Awaitable
from typing import TYPE_CHECKING, Callable, Literal, Protocol

from .._connection_state import RadioConnectionState
from ..audio import AudioPacket
from ..audio.lan_stream import SYNTHETIC_RX_IDENT
from ..commands import parse_ack_nak
from ..core.radio_protocol import RadioIdentity, RadioIdentityStatus
from ..core.serial_open import serial_open_diagnostic
from ..exceptions import (
    AudioFormatError,
    CommandError,
    ConnectionError,
    TimeoutError as RigplaneTimeoutError,
)
from ..radio import CoreRadio
from ..types import AudioCodec, ScopeCompletionPolicy, get_audio_capabilities

if TYPE_CHECKING:
    from .discovery import SerialPortCandidate
    from .icom7610.drivers.serial_civ_link import SerialCivLink
    from .icom7610.drivers.serial_session import SerialSessionDriver
    from ..profiles import RadioProfile

logger = logging.getLogger(__name__)
_AUDIO_CAPABILITIES = get_audio_capabilities()
_DEFAULT_AUDIO_CODEC = _AUDIO_CAPABILITIES.default_codec
_DEFAULT_AUDIO_SAMPLE_RATE = _AUDIO_CAPABILITIES.default_sample_rate_hz
_TWO_CHANNEL_CODECS = {
    AudioCodec.PCM_2CH_8BIT,
    AudioCodec.PCM_2CH_16BIT,
    AudioCodec.ULAW_2CH,
    AudioCodec.OPUS_2CH,
}
# MOR-2595: Icom serial gap, wfview's 25 ms send-to-send for these radios
# (IC-7300, IC-7610, IC-705, IC-9700; HasFDComms). Unmeasured until the
# IC-7300 is on the bench. A subclass that is not an Icom keeps 50 ms.
_ICOM_SERIAL_CIV_MIN_INTERVAL_MS = 25.0
_NON_ICOM_SERIAL_CIV_MIN_INTERVAL_MS = 50.0
_SERIAL_SCOPE_MIN_BAUD = 115200


def _env_bool(name: str, default: bool = False) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


# MOR-1453: trailing device-node suffix that encodes the physical USB port
# (macOS ``/dev/cu.usbserial-1420``) or enumeration order (Linux
# ``/dev/ttyUSB0``). Stripped to derive a glob pattern that matches sibling
# nodes of the same USB-serial family after a replug renumbers the node.
# NOTE: the separator itself is eaten by the match, e.g.
# ``/dev/cu.usbserial-1420`` -> ``/dev/cu.usbserial*`` (no trailing ``-``),
# which also matches unhyphenated siblings like ``/dev/cu.usbserial9931``.
_TRAILING_NODE_SUFFIX_RE = re.compile(r"(-[0-9A-Za-z]+|[0-9]+)$")


def _derive_reconnect_glob(device: str) -> str:
    """Best-effort glob pattern matching sibling device nodes of *device*.

    ``/dev/cu.usbserial-1420`` -> ``/dev/cu.usbserial*``;
    ``/dev/ttyUSB0`` -> ``/dev/ttyUSB*``.
    """
    stripped = _TRAILING_NODE_SUFFIX_RE.sub("", device)
    if not stripped or stripped == device:
        return device + "*"
    return stripped + "*"


class _SerialAudioDriver(Protocol):
    async def start_rx(
        self,
        callback: Callable[[bytes], None] | None = None,
        *,
        sample_rate: int | None = None,
        channels: int | None = None,
        frame_ms: int | None = None,
    ) -> None: ...

    async def stop_rx(self) -> None: ...

    async def start_tx(
        self,
        *,
        sample_rate: int | None = None,
        channels: int | None = None,
        frame_ms: int | None = None,
    ) -> None: ...

    async def stop_tx(self) -> None: ...

    async def _push_tx_pcm(self, frame: bytes) -> None: ...

    @property
    def tx_running(self) -> bool: ...

    def set_serial_port(self, serial_port: str | None) -> None: ...


class _IcomSerialRadioBase(CoreRadio):
    """Base for all Icom serial-backend radios.

    Subclasses must set ``_DEFAULT_MODEL`` as a class variable.
    """

    _DEFAULT_MODEL: str = ""
    _serial_civ_min_interval_ms: float = _ICOM_SERIAL_CIV_MIN_INTERVAL_MS
    _SERIAL_WATCHDOG_INTERVAL_S = 0.2
    _SERIAL_WATCHDOG_RETRY_S = 0.5
    # Cap for the exponential backoff applied after repeated reconnect failures
    # (e.g. the USB serial device node briefly disappearing during macOS device
    # renumbering on a CH342 bridge — MOR-237). Without a backoff the watchdog
    # retried every _SERIAL_WATCHDOG_RETRY_S and flooded the log with full
    # tracebacks twice a second while the port was gone.
    _SERIAL_WATCHDOG_RETRY_MAX_S = 5.0
    # Consecutive CI-V command timeouts (no ACK/response within
    # ``_civ_get_timeout``) that force the connection state machine into
    # link-down. The raw serial health flag (``SerialCivLink.healthy``) only
    # flips when a read/write syscall raises; a USB-serial adapter that
    # vanishes without the OS surfacing an error leaves it stuck healthy
    # forever (MOR-1440 bench evidence). CI-V timeouts on the existing
    # keep-alive cadence are the reliable probe.
    _SERIAL_LINK_DOWN_TIMEOUT_THRESHOLD = 3
    # Defence in depth for MOR-2081 (see ``_stop_civ_data_watchdog``): bounds
    # the await on the watchdog task during teardown so a future regression
    # of the same absorbed-cancellation shape is a loud, bounded failure
    # instead of a 300s pytest-timeout / hung disconnect().
    _SERIAL_CIV_WATCHDOG_TEARDOWN_TIMEOUT_S = 5.0
    # MOR-3071: backoff for the background identity re-read on the open
    # link — 1, 2, 4 and 8 s, then every 15 s. Class attributes so tests
    # can drive the cadence with virtual time (the same seam
    # ``_SERIAL_WATCHDOG_RETRY_S`` uses).
    _SERIAL_IDENTITY_REREAD_BACKOFF_S: tuple[float, ...] = (1.0, 2.0, 4.0, 8.0)
    _SERIAL_IDENTITY_REREAD_STEADY_S: float = 15.0

    def __init__(
        self,
        *,
        device: str,
        baudrate: int = 115200,
        radio_addr: int | None = None,
        timeout: float = 5.0,
        audio_codec: AudioCodec | int = _DEFAULT_AUDIO_CODEC,
        audio_sample_rate: int = _DEFAULT_AUDIO_SAMPLE_RATE,
        rx_device: str | None = None,
        tx_device: str | None = None,
        ptt_mode: str = "civ",
        profile: "RadioProfile | str | None" = None,
        model: str | None = None,
        allow_low_baud_scope: bool = False,
        civ_link: SerialCivLink | None = None,
        session_driver: SerialSessionDriver | None = None,
        audio_driver: _SerialAudioDriver | None = None,
        reconnect_glob: str | None = None,
        _civ_identity_probe: Callable[[str], Awaitable[int | None]] | None = None,
        _enumerate_serial_ports_fn: Callable[[], "list[SerialPortCandidate]"]
        | None = None,
    ) -> None:
        from .icom7610.drivers.serial_civ_link import SerialCivLink
        from .icom7610.drivers.serial_session import SerialSessionDriver
        from ..audio.usb_driver import AudioDeviceConfig, UsbAudioDriver

        if session_driver is not None and civ_link is not None:
            raise ValueError("Provide either civ_link or session_driver, not both.")
        super().__init__(
            host=device,
            port=0,
            username="",
            password="",
            radio_addr=radio_addr,
            timeout=timeout,
            audio_codec=audio_codec,
            audio_sample_rate=audio_sample_rate,
            profile=profile,
            model=model or self._DEFAULT_MODEL or None,
        )
        self._serial_device = device
        self._serial_baudrate = baudrate
        self._serial_rx_device_override = rx_device
        self._serial_tx_device_override = tx_device
        # MOR-1453: rediscovery seams for a renumbered device node. Glob is
        # auto-derived unless overridden; identity is captured fresh after
        # each successful connect (see ``_capture_serial_identity``).
        self._serial_reconnect_glob = reconnect_glob or _derive_reconnect_glob(device)
        self._civ_identity_probe = (
            _civ_identity_probe or self._default_civ_identity_probe
        )
        self._enumerate_serial_ports_fn = (
            _enumerate_serial_ports_fn or self._default_enumerate_serial_ports
        )
        self._serial_hw_identity: tuple[str | None, int | None, int | None] | None = (
            None
        )
        if ptt_mode != "civ":
            raise ValueError(
                "Unsupported serial PTT mode. Only 'civ' is currently supported."
            )
        self._serial_ptt_mode = ptt_mode
        self._allow_low_baud_scope = allow_low_baud_scope or _env_bool(
            "ICOM_SERIAL_SCOPE_ALLOW_LOW_BAUD",
            default=False,
        )
        self._low_baud_scope_warned = False
        serial_min_interval_ms = float(
            os.environ.get(
                "ICOM_SERIAL_CIV_MIN_INTERVAL_MS",
                f"{self._serial_civ_min_interval_ms}",
            )
        )
        if serial_min_interval_ms <= 0:
            raise ValueError("ICOM_SERIAL_CIV_MIN_INTERVAL_MS must be > 0")
        self._civ_min_interval = serial_min_interval_ms / 1000.0
        # MOR-2595: serial round trip is unmeasured, so the estimate is the
        # gap itself until the IC-7300 is measured.
        self._civ_rtt_estimate = self._civ_min_interval
        serial_link = civ_link or SerialCivLink(device=device, baudrate=baudrate)
        self._serial_session = session_driver or SerialSessionDriver(serial_link)
        # MOR-1453: raw link ref for rediscovery's set_device rebind. None
        # when a bespoke session_driver was supplied (its link is then
        # unreachable here -- currently unused in practice).
        self._serial_civ_link = serial_link if session_driver is None else None
        self._serial_audio_driver = audio_driver or UsbAudioDriver(
            AudioDeviceConfig(
                rx_device=rx_device,
                tx_device=tx_device,
                sample_rate=audio_sample_rate,
                channels=self._serial_audio_channels_for_codec(),
                frame_ms=20,
            ),
            serial_port=device,
            backend=None,  # default PortAudioBackend
        )
        self._serial_audio_seq = 0
        # MOR-2465: identity of the active RX delivery callable. PCM frames
        # arriving on the PortAudio thread are marshalled onto the owning
        # event loop and re-check this identity there, so a frame scheduled
        # before stop_rx()/a restart cannot enter a new RX session. None
        # when no RX session is active.
        self._serial_rx_delivery: Callable[[bytes], None] | None = None
        # MOR-1440 link-down detection: consecutive-timeout evidence tracked
        # against the CI-V request tracker's lifetime counters (see
        # ``_serial_civ_timeout_evidence_crossed_threshold``).
        self._civ_consecutive_timeouts = 0
        self._civ_watchdog_last_seen_timeouts = 0
        self._civ_watchdog_last_seen_rx_packets = 0
        # MOR-2861: second evidence path — a silent link (polls outstanding,
        # ``rx_packet_count`` frozen) longer than the silence limit
        # (``_serial_link_down_silence_limit_s``). The clock source is a
        # seam so tests can drive a fake clock.
        self._civ_silence_started_monotonic: float | None = None
        self._civ_silence_time_source: Callable[[], float] = time.monotonic
        self._civ_link_down_note = ""
        # MOR-1440 review round 2: identity of the transport the above two
        # baselines were last measured against. Every (re)connect installs a
        # *brand-new* ``SerialCivTransport`` (see
        # ``SerialSessionDriver.connect``), so ``rx_packet_count`` restarts at
        # 0 while these baselines would otherwise keep a stale high-water
        # mark from the outgoing transport — see
        # ``_civ_watchdog_rebaseline``.
        self._civ_watchdog_last_transport: object | None = None
        # MOR-2841: durable record that the link-down detector has fired at
        # least once. The RECONNECTING state it announces is transient — the
        # watchdog's soft_reconnect can reopen a present-but-silent port
        # within seconds and return to CONNECTED (stand evidence,
        # 2026-09-28) — while the fact that the radio answered nothing
        # survives for the web startup gate to read.
        self._civ_link_down_ever_declared = False
        # MOR-2876: text of the latest failed port open; None once an open
        # succeeds.
        self.last_error: str | None = None
        self.serial_open_error: str | None = None
        # MOR-2876: set once a port open succeeds; no sibling-port search
        # before that (``_maybe_rediscover_serial_device``).
        self._has_connected_once = False
        # MOR-3071: typed identity of the radio on the open link. None until
        # a connect attempt opens the session (a port that never opened has
        # no identity — the #3902 path is unchanged); reset to ``checking``
        # by every connect attempt; cleared by ``disconnect``.
        self._connection_identity: RadioIdentity | None = None
        # MOR-3071: the background re-read task that owns the open link
        # while identity is ``no_response``.
        self._serial_identity_reread_task: asyncio.Task[None] | None = None
        # MOR-3071 round 4: the CI-V generation whose identity gate
        # produced the recorded answer (set by ``_latch_serial_connected``).
        # CONNECTED may latch only while this matches ``_civ_epoch``:
        # every new link — a connect attempt or a soft_reconnect —
        # advances the generation before its gate runs, so an answer can
        # never be credited to a transport it was not recorded on.
        # Cleared at the start of every connect attempt, in
        # ``soft_reconnect``, ``disconnect`` and whenever the link the
        # answer belonged to is declared dead or the hold is abandoned.
        self._serial_identity_answer_epoch: int | None = None
        # MOR-3071 round 4: True while a connect()/soft_reconnect()
        # identity gate is awaiting its answer — one of the two states
        # in which a task actively owns the open link for the identity
        # phase (the other is a live re-read task).
        self._serial_identity_gate_running = False
        # MOR-3078: True while the identity hold in progress was entered
        # by soft_reconnect (managed TX parked, reconnect tail owed). The
        # background re-read consumes it when a late answer completes the
        # connect: it must finish the soft_reconnect — the re-arm plus
        # the ``_on_reconnect`` callback — rather than the plain connect
        # tail. Cleared everywhere a new attempt, a teardown or an
        # abandonment retires the hold it was recorded for.
        self._serial_identity_hold_reconnect_origin = False

    # ------------------------------------------------------------------
    # Backend identity
    # ------------------------------------------------------------------

    @property
    def backend_id(self) -> str:
        """Stable backend family identifier — ``"icom_serial"`` for serial CI-V."""
        return "icom_serial"

    @property
    def usb_audio_contract(self) -> object | None:
        """Effective OS audio contract reported by the USB audio driver."""

        return getattr(self._serial_audio_driver, "usb_audio_contract", None)

    @property
    def audio_tx_codec(self) -> AudioCodec:
        """Effective TX codec (MOR-532 codec descriptor surface).

        The serial USB CODEC mic input is mono-only by hardware:
        ``start_audio_tx_pcm`` clamps the PortAudio output stream to one
        channel (GH#1382), so the TX payload is always mono 16-bit PCM
        regardless of the configured RX codec. Consumed by later MOR-532
        epic steps.
        """
        return AudioCodec.PCM_1CH_16BIT

    @property
    def audio_duplex_mode(self) -> str:
        """Duplex capability (MOR-532 duplex descriptor surface).

        Delegates to the USB audio driver's ``duplex_mode`` policy (MOR-534)
        via ``getattr`` so the internal ``_SerialAudioDriver`` protocol stays
        narrow. Falls back to ``"full"`` when the driver does not expose the
        property or when resolving it raises (device enumeration may fail on
        offline hosts and test doubles). Single source of duplex policy for
        later MOR-532 epic steps.
        """
        try:
            mode = getattr(self._serial_audio_driver, "duplex_mode", None)
        except Exception:
            return "full"
        return mode if isinstance(mode, str) else "full"

    @property
    def audio_setup_order(self) -> Literal["rx_first", "tx_first", "atomic"]:
        """Setup ordering descriptor (MOR-575, ADR §3.3).

        Derived from :attr:`audio_duplex_mode` — single source of truth,
        so the two descriptors never drift: ``"exclusive"`` (same-device
        macOS: one duplex stream, setup does not decompose into
        rx/tx-first) → ``"atomic"``; ``"full"`` (separate RX/TX devices,
        order-indifferent) → ``"rx_first"``; ``"half"`` or any
        unexpected/raising duplex mode degrades to the ``"rx_first"``
        safe default — the same robustness ``audio_duplex_mode`` has
        toward the driver. Nothing consumes this yet — the AudioSession
        (MOR-562 step 8) and bridge (step 9) will read it.
        """
        try:
            mode = self.audio_duplex_mode
        except Exception:
            return "rx_first"
        if mode == "exclusive":
            return "atomic"
        # "full", "half", and anything unexpected → rx_first (safe default).
        return "rx_first"

    # ------------------------------------------------------------------
    # Connection properties
    # ------------------------------------------------------------------

    @property
    def connected(self) -> bool:
        if self._conn_state != RadioConnectionState.CONNECTED:
            return False
        if self._civ_transport is None:
            return False
        return self._serial_session.connected

    @property
    def control_connected(self) -> bool:
        return self._serial_session.connected

    @property
    def radio_ready(self) -> bool:
        if not self.connected:
            return False
        if self._civ_recovering or not self._civ_stream_ready:
            return False
        return self._serial_session.ready

    @property
    def connection_identity(self) -> RadioIdentity | None:
        """Typed identity of the radio on the open link (MOR-3071).

        ``None`` while no connect attempt has opened the session (and after
        ``disconnect``); ``status`` is ``checking``/``no_response`` while the
        identity phase owns the open link, and ``connected`` stays false
        until a well-formed identity answer completes the connect.
        """
        return self._connection_identity

    # ------------------------------------------------------------------
    # Connect / disconnect / reconnect
    # ------------------------------------------------------------------

    async def connect(self) -> None:
        if self.connected:
            return
        if self._identity_phase_owns_open_link():
            # MOR-3071: the identity read (or its re-read) already owns
            # the open link — the web power-on path would reopen it (and
            # toggle DTR/RTS) for nothing. Round 5: ONE predicate, shared
            # with the watchdog's guard.
            return

        # MOR-3071 round 4: a committed attempt opens a NEW link. The
        # answer recorded for the old link no longer applies, and a live
        # re-read task from an earlier hold must be cancelled before the
        # reopen — it would otherwise wake up on the new transport,
        # where the new gate's read is already the one reader of
        # ``19 00``.
        self._cancel_serial_identity_reread()
        self._serial_identity_answer_epoch = None
        self._serial_identity_hold_reconnect_origin = False
        self._conn_state = RadioConnectionState.CONNECTING
        self._civ_stream_ready = False
        self._civ_recovering = False
        self._last_status_error = 0
        self._last_status_disconnected = False
        attempt_epoch = self._civ_epoch
        try:
            await self._serial_session.connect()
        except Exception as exc:
            diagnostic = serial_open_diagnostic(exc)
            message = (
                f"Failed to connect serial session on {self._serial_device}: {exc}"
            )
            if self._civ_epoch == attempt_epoch:
                self._conn_state = RadioConnectionState.DISCONNECTED
                self._civ_stream_ready = False
                self._civ_recovering = False
                self.last_error = f"Failed to connect serial session: {diagnostic}"
                self.serial_open_error = diagnostic
            raise ConnectionError(message) from exc
        if self._civ_epoch != attempt_epoch:
            return
        self.last_error = None
        self.serial_open_error = None
        self._has_connected_once = True

        # A connect() over a runtime whose CI-V worker and RX pump still
        # run (the web power-on route reconnects after a link-down
        # declaration that deliberately leaves them alive) must not leave
        # them bound to the retired generation: the stale pump would
        # consume and drop every answer to the new epoch — the identity
        # read first of all. The same discipline soft_reconnect applies
        # before its reopen; no-ops on a first connect.
        await self._stop_civ_worker()
        await self._stop_civ_rx_pump()

        self._ctrl_transport = self._serial_session.control_transport  # type: ignore[assignment]
        self._civ_transport = self._serial_session.civ_transport  # type: ignore[assignment]
        self._advance_civ_generation("serial-connect")
        attempt_epoch = self._civ_epoch
        self._civ_last_waiter_gc_monotonic = time.monotonic()
        self._last_civ_data_received = time.monotonic()
        self._start_civ_rx_pump()
        self._start_civ_data_watchdog()
        self._start_civ_worker()

        # MOR-3071: hold the connect until the radio answers the identity
        # read. The RX pump and CI-V worker above must already run for the
        # read to get its reply, but CONNECTED must not latch before the
        # answer. On silence connect() returns without raising: the link
        # stays open, the re-read task keeps asking, and the web serves in
        # the radio-not-answering state.
        self._serial_identity_gate_running = True
        try:
            identity_answered = await self._gate_serial_identity()
        except Exception as exc:
            # MOR-3071 round 5: what the gate does not translate itself
            # (a write OSError on an opened-but-wrong port, CI-V runtime
            # errors) follows the session-open failure contract above.
            # MOR-3078: the crashed gate owns no hold — the ``checking``
            # it recorded dies with it, or the web would publish a hold
            # nobody drives forever.
            message = (
                f"Failed to read the radio identity on {self._serial_device}: {exc}"
            )
            if self._civ_epoch == attempt_epoch:
                self._connection_identity = None
                self._serial_identity_hold_reconnect_origin = False
                self._conn_state = RadioConnectionState.DISCONNECTED
                self._civ_stream_ready = False
                self._civ_recovering = False
                self.last_error = message
            raise ConnectionError(message) from exc
        finally:
            if self._civ_epoch == attempt_epoch:
                self._serial_identity_gate_running = False
        if not identity_answered:
            return
        self._latch_serial_connected()
        if (
            self._managed_tx_composition is not None
            and self._serial_session.ready
            and self._civ_transport is not None
        ):
            await self._arm_managed_tx()

    async def soft_disconnect(self) -> None:
        await self.disconnect()

    async def disconnect(self) -> None:
        self.serial_open_error = None
        # Always stop watchdog first to avoid orphan retry loops on failed reconnects.
        self._cancel_serial_identity_reread()
        self._connection_identity = None
        self._serial_identity_answer_epoch = None
        self._serial_identity_hold_reconnect_origin = False
        await self._stop_civ_data_watchdog()
        await self._stop_serial_audio_driver()
        if (
            self._conn_state != RadioConnectionState.CONNECTED
            and not self._serial_session.connected
        ):
            self._conn_state = RadioConnectionState.DISCONNECTED
            self._civ_stream_ready = False
            self._civ_recovering = False
            return
        if self._conn_state != RadioConnectionState.CONNECTED:
            await self._stop_civ_worker()
            await self._stop_civ_rx_pump()
            await self._serial_session.disconnect()
            self._ctrl_transport = self._serial_session.control_transport  # type: ignore[assignment]
            self._civ_transport = None
            self._conn_state = RadioConnectionState.DISCONNECTED
            self._civ_stream_ready = False
            self._civ_recovering = False
            return
        await super().disconnect()
        await self._serial_session.disconnect()
        self._ctrl_transport = self._serial_session.control_transport  # type: ignore[assignment]

    async def soft_reconnect(self) -> None:
        if (
            self._serial_session.ready
            and self._civ_transport is not None
            and self._serial_identity_answered_for_current_link()
        ):
            # MOR-3071 round 4: a ready link counts as recovered only
            # when the identity gate answered on THIS CI-V generation. A
            # ready session without an answer (an abandoned hold, a
            # crashed gate) must fall through and re-gate.
            return
        if self._identity_phase_owns_open_link():
            # MOR-3064: single-flight ownership — the identity read (or
            # its re-read) already owns this open, ready link; this is
            # the same state ``connect()`` returns early from. A
            # duplicate recovery here (the CI-V recovery kick in
            # ``_civ_rx._wait_for_civ_transport_recovery``, the web
            # ``radio_connect`` route) would retire the in-flight gate's
            # CI-V generation and reopen the port: its pending read dies
            # as silence, the epoch fence discards the reply, and the
            # retired gate's tail leaves a hold nobody drives (the
            # MOR-3078 Linux quick failure). The in-flight owner keeps
            # the link; a real replacement — a link that stopped being
            # ready, ``disconnect`` — does not own it and reopens as
            # before.
            return

        if self._managed_tx_composition is not None:
            await self._park_managed_tx()
        self._conn_state = RadioConnectionState.RECONNECTING
        self._civ_stream_ready = False
        self._civ_recovering = True
        # MOR-3071 round 4: same discipline as connect() — the new link
        # starts with no recorded answer and no old re-read task that
        # could read on the new transport.
        self._cancel_serial_identity_reread()
        self._serial_identity_answer_epoch = None
        self._serial_identity_hold_reconnect_origin = True
        self._advance_civ_generation("serial-soft-reconnect")
        # MOR-3064: the generation this attempt committed to. A gate or
        # open that comes back retired (a ``disconnect`` or teardown ran
        # underneath this attempt) owns no state to write — the tails
        # below check against it.
        attempt_epoch = self._civ_epoch
        await self._stop_civ_worker()
        await self._stop_civ_rx_pump()
        await self._serial_session.disconnect()
        await self._maybe_rediscover_serial_device()

        try:
            await self._serial_session.connect()
        except Exception as exc:
            # Keep recovery state so watchdog can continue retries.
            # MOR-3064: a retired attempt (its generation was replaced
            # underneath it) writes no state — whoever replaced the link
            # owns the connection state now.
            diagnostic = serial_open_diagnostic(exc)
            message = (
                f"Failed to reconnect serial session on {self._serial_device}: {exc}"
            )
            if self._civ_epoch == attempt_epoch:
                self._conn_state = RadioConnectionState.RECONNECTING
                self._civ_stream_ready = False
                self._civ_recovering = True
                self.last_error = f"Failed to reconnect serial session: {diagnostic}"
                self.serial_open_error = diagnostic
            raise ConnectionError(message) from exc
        if self._civ_epoch != attempt_epoch:
            return
        self.last_error = None
        self.serial_open_error = None
        self._has_connected_once = True

        self._ctrl_transport = self._serial_session.control_transport  # type: ignore[assignment]
        self._civ_transport = self._serial_session.civ_transport  # type: ignore[assignment]
        self._civ_last_waiter_gc_monotonic = time.monotonic()
        self._last_civ_data_received = time.monotonic()
        self._start_civ_rx_pump()
        self._start_civ_worker()

        # MOR-3071: same identity gate as connect() — a replugged radio must
        # answer before CONNECTED latches again. On silence the re-read task
        # takes over the open link; the state stays CONNECTING (not
        # RECONNECTING) so the watchdog's ready-branch cannot latch
        # CONNECTED without an identity.
        self._serial_identity_gate_running = True
        try:
            identity_answered = await self._gate_serial_identity()
        except Exception as exc:
            # MOR-3071 round 5: same contract as the reconnect-open
            # failure above — keep the state the watchdog retries from.
            # MOR-3078: same crash contract as connect() — the checking
            # the gate recorded dies with it.
            # MOR-3064: a retired gate writes no state — the attempt
            # that still owns the current generation does.
            message = (
                f"Failed to read the radio identity on {self._serial_device}: {exc}"
            )
            if self._civ_epoch == attempt_epoch:
                self._connection_identity = None
                self._serial_identity_hold_reconnect_origin = False
                self._conn_state = RadioConnectionState.RECONNECTING
                self._civ_stream_ready = False
                self._civ_recovering = True
                self.last_error = message
            raise ConnectionError(message) from exc
        finally:
            if self._civ_epoch == attempt_epoch:
                self._serial_identity_gate_running = False
        if not identity_answered:
            if self._civ_epoch == attempt_epoch:
                # MOR-3064: only the attempt that still owns the current
                # generation may park the radio in the hold state — a
                # retired gate must not overwrite whatever replaced it
                # (``disconnect``'s DISCONNECTED, a successor's latched
                # CONNECTED), which left a hold nobody drives.
                self._conn_state = RadioConnectionState.CONNECTING
            return
        self._latch_serial_connected()
        # MOR-1440 review round 2 (B1 item 2): this is the RECONNECTING ->
        # CONNECTED transition. Re-baseline the link-death detector here,
        # explicitly, rather than waiting for the next watchdog tick's
        # transport-identity check to notice: any CI-V command timeouts
        # banked while the evidence check was short-circuited during
        # RECONNECTING (see ``_serial_civ_watchdog_loop``) must not be
        # credited against the link that just recovered.
        self._civ_watchdog_rebaseline()
        if (
            self._managed_tx_composition is not None
            and self._serial_session.ready
            and self._civ_transport is not None
        ):
            await self.rearm_managed_tx()
        if self._on_reconnect is not None:
            try:
                self._on_reconnect()
            except Exception:
                logger.debug(
                    "serial soft_reconnect: _on_reconnect callback failed",
                    exc_info=True,
                )

    def start_reconnect_recovery(self) -> None:
        """Keep retrying the port after :meth:`connect` failed to open it.

        Enters the state the serial watchdog recovers from — ``RECONNECTING``
        with the watchdog running — so its ``soft_reconnect`` retries the
        port with the existing backoff; :meth:`disconnect` stops it.
        ``connect`` never starts this itself; the web/station session in
        ``cli: _ManagedTxRadioSession`` does (MOR-2876).
        """
        self._conn_state = RadioConnectionState.RECONNECTING
        self._civ_stream_ready = False
        self._civ_recovering = True
        self._start_civ_data_watchdog()

    # ------------------------------------------------------------------
    # Connect-time identity gate (MOR-3071)
    # ------------------------------------------------------------------

    def _identity_hold_status_active(self) -> bool:
        identity = self._connection_identity
        return identity is not None and identity.status in (
            RadioIdentityStatus.CHECKING,
            RadioIdentityStatus.NO_RESPONSE,
            RadioIdentityStatus.IDENTITY_MISMATCH,
        )

    def _identity_phase_owns_open_link(self) -> bool:
        """Whether the identity read (or its re-read) owns the open link.

        True while the identity status is ``checking``/``no_response``,
        the session is still *ready* — ``SerialCivLink`` keeps
        ``connected`` after a recoverable error while ``healthy`` drops,
        so ``connected`` alone cannot tell a live link from a dead one
        (MOR-3071 round 2) — and a task is actually driving the hold:
        its gate awaiting an answer, or its re-read task alive. A status
        left behind by a crashed gate or an abandoned hold owns nothing
        and must not block recovery (MOR-3071 round 4).
        """
        return (
            self._identity_hold_status_active()
            and self._serial_session.ready
            and self._serial_identity_hold_has_owner()
        )

    def _serial_identity_hold_has_owner(self) -> bool:
        """Whether a gate or re-read task actively drives the identity hold.

        MOR-3071 round 4: ``checking``/``no_response`` alone must not
        block recovery — a gate that crashed mid-read or an abandoned
        hold leaves the status behind with nobody driving it. The hold
        owns the open link only while its gate is awaiting an answer or
        its re-read task is alive.
        """
        if self._serial_identity_gate_running:
            return True
        task = self._serial_identity_reread_task
        return task is not None and not task.done()

    def _serial_identity_answered_for_current_link(self) -> bool:
        """Whether the identity gate answered for the CURRENT link.

        MOR-3071 round 4 invariant: the answer is recorded with the CI-V
        generation it was answered at (``_civ_epoch``, set in
        ``_latch_serial_connected`` — reusing the same generation
        mechanism as ``_advance_civ_generation``/``_managed_tx_armed_epoch``),
        and every new link — a connect attempt or a soft_reconnect —
        advances the generation before its gate runs. The session driver
        itself is fixed per radio instance and installs a brand-new
        transport on every connect, each such swap advancing the
        generation, so an answer can never be credited to a transport it
        was not recorded on — no matter which path swapped the link.
        """
        epoch = self._serial_identity_answer_epoch
        return epoch is not None and epoch == self._civ_epoch

    def _power_on_allowed_in_no_response(self) -> bool:
        """Whether POWER ON may pass while the connect is held (MOR-3071).

        The narrowest allowance past ``_check_connected``: POWER ON only,
        only while the identity is ``no_response`` and the hold actually
        owns a READY open link (MOR-3078: the same readiness the hold
        predicate requires — a session whose ``connected`` lingers while
        ``ready`` dropped owns nothing, and POWER ON must not go out on
        it) — MOR-2841's promise that Power ON from the UI stays
        available for a switched-off radio. Everything else stays
        refused.
        """
        identity = self._connection_identity
        return (
            identity is not None
            and identity.status is RadioIdentityStatus.NO_RESPONSE
            and self._serial_session.connected
            and self._identity_phase_owns_open_link()
        )

    async def set_powerstat(self, on: bool) -> None:
        """Power on/off with the MOR-3071 ``no_response`` POWER ON allowance.

        POWER ON in ``no_response`` goes out on the open link through the
        direct request-tracker path: ``_send_civ_raw`` would enter
        ``_wait_for_civ_transport_recovery`` (``_connected`` is false while
        the connect is held) and kick a fast ``soft_reconnect`` — a port
        reopen that toggles DTR/RTS and that this phase must never do. The
        re-read task then picks the radio up once it boots.
        """
        if on and self._power_on_allowed_in_no_response():
            civ = self._commands.power_on(to_addr=self._radio_addr)
            try:
                await self._execute_civ_raw(civ, wait_response=False)
            except OSError as exc:
                # MOR-3078: a dead writer (the unplugged-cable shape,
                # where ``connected``/``ready`` linger while writes fail)
                # follows the gate's own contract — rigplane's
                # ConnectionError with the OSError as its cause.
                self.last_error = (
                    f"POWER ON could not be sent on {self._serial_device}: {exc}"
                )
                raise ConnectionError(self.last_error) from exc
            return
        await super().set_powerstat(on)

    async def _read_serial_identity_payload(self) -> bytes | None:
        """Send the profile's ``get_transceiver_id`` read; ``None`` = silence.

        Runs on the direct request-tracker path (``_execute_civ_raw``), not
        ``_send_civ_raw``: the latter's recovery wait would soft_reconnect
        and reopen the port while CONNECTED is deliberately not latched.
        The answer window is the existing CI-V answer timeout
        (``_civ_get_timeout``). Returns ``b""`` when the profile declares
        no identity read (connects as today, MOR-3064 part 4), the raw
        payload bytes after the ``19 00`` echo when the addressed radio
        answers, and ``None`` when nothing answered.
        """
        builder = getattr(self._commands, "get_transceiver_id", None)
        if not callable(builder):
            return b""
        try:
            request = builder(to_addr=self._radio_addr)
        except CommandError:
            # This profile's command map declares no identity read
            # (shipped Icom profiles all do): connects as today.
            return b""
        try:
            resp = await self._execute_civ_raw(request, wait_response=True)
        except (RigplaneTimeoutError, asyncio.TimeoutError):
            # ``_execute_civ_raw`` raises rigplane's TimeoutError (not
            # asyncio's) when the answer window runs out — both are caught
            # so a silent link reads as silence, never as a connect crash.
            return None
        except ConnectionError:
            return None
        if resp is None:
            return None
        # Only a well-formed reply from the addressed radio counts: the
        # request tracker keys the answer to our own ``19 00`` request, and
        # the source address must be the configured CI-V address (never
        # compared the other way — the payload is not the address).
        if resp.from_addr != self._radio_addr or not resp.data:
            return None
        return resp.data

    def _record_serial_identity_payload(self, payload: bytes | None) -> bool:
        """Compare this link's native ID with the selected profile only."""
        answered_id = payload.hex().upper() if payload else None
        expected_ids = self._profile.expected_identity_ids
        if payload is None or (expected_ids and not answered_id):
            status = RadioIdentityStatus.NO_RESPONSE
        elif not expected_ids:
            status = RadioIdentityStatus.UNVERIFIED
        elif answered_id in expected_ids:
            status = RadioIdentityStatus.VERIFIED
        else:
            status = RadioIdentityStatus.IDENTITY_MISMATCH
        self._connection_identity = RadioIdentity(
            status=status,
            expected_model=self.model,
            answered_model=self.model
            if status is RadioIdentityStatus.VERIFIED
            else None,
            answered_id=answered_id,
        )
        if status is RadioIdentityStatus.IDENTITY_MISMATCH:
            self.last_error = (
                f"The radio on {self._serial_device} does not match "
                f"the selected {self.model} profile."
            )
        elif status is RadioIdentityStatus.NO_RESPONSE:
            self.last_error = (
                f"No answer from the radio on {self._serial_device} "
                f"({self.model} profile, {self._serial_baudrate} baud): another "
                "radio may be on this port, or it is switched off."
            )
        else:
            self.last_error = None
        return status in (RadioIdentityStatus.VERIFIED, RadioIdentityStatus.UNVERIFIED)

    async def _gate_serial_identity(self) -> bool:
        """Run the identity read gate; True when the connect may proceed.

        Resets the identity to ``checking``, sends the read once, and on a
        well-formed answer compares the native ID with profile metadata;
        profiles without expected IDs remain ``unverified``. The CI-V
        address is not compared — it is user-settable.
        On silence or mismatch leaves the link open with a
        plain ``last_error`` sentence, hands the link to the background
        re-read task, and returns False — CONNECTED must not latch.
        """
        self._connection_identity = RadioIdentity(
            status=RadioIdentityStatus.CHECKING,
            expected_model=self.model,
        )
        probe_identity = self._connection_identity
        epoch = self._civ_epoch
        payload = await self._read_serial_identity_payload()
        if epoch != self._civ_epoch:
            if self._connection_identity is probe_identity:
                self._connection_identity = None
            return False
        if self._record_serial_identity_payload(payload):
            return True
        logger.warning(
            "rigplane (%s): identity not accepted on %s "
            "(%d baud); holding the connect — the re-read task keeps "
            "asking on the open link",
            self.model,
            self._serial_device,
            self._serial_baudrate,
        )
        self._start_serial_identity_reread()
        return False

    def _latch_serial_connected(self) -> None:
        """Latch CONNECTED after the identity gate passed (MOR-3071).

        The same tail ``connect`` always ran, plus the reset of the MOR-1440
        link-down evidence: identity-read timeouts banked while the connect
        was held must not count against the link once CONNECTED latches.
        Round 4 also records the CI-V generation the answer belongs to —
        the one fact every later CONNECTED transition checks against.
        """
        self._cancel_serial_identity_reread()
        self._serial_identity_answer_epoch = self._civ_epoch
        self._conn_state = RadioConnectionState.CONNECTED
        self._civ_stream_ready = self._serial_session.ready
        self._civ_recovering = not self._civ_stream_ready
        self._capture_serial_identity()
        self._civ_watchdog_rebaseline()
        logger.info(
            "Connected to %s over serial (%s @ %d baud)",
            self.model,
            self._serial_device,
            self._serial_baudrate,
        )

    def _start_serial_identity_reread(self) -> None:
        """Start the background identity re-read unless one already runs."""
        task = self._serial_identity_reread_task
        if task is not None and not task.done():
            return
        self._serial_identity_reread_task = asyncio.create_task(
            self._serial_identity_reread_loop(),
            name="serial-identity-reread",
        )

    def _cancel_serial_identity_reread(self) -> None:
        task = self._serial_identity_reread_task
        if task is not None and not task.done() and task is not asyncio.current_task():
            task.cancel()
        self._serial_identity_reread_task = None

    def _abandon_serial_identity_hold(
        self, reason: str, *, exc: BaseException | None = None
    ) -> None:
        """Leave an identity hold whose link died (MOR-3071 round 2).

        ``SerialCivLink`` keeps ``connected`` set on a dead link, so the
        hold would never trigger MOR-1440 detection (CONNECTED-only) or
        ``soft_reconnect``; drop it into the watchdog's recovery state.

        Round 4: the recorded identity answer dies with the hold — the
        recovery link must re-answer before anything latches again. The
        identity itself stays ``None`` (not the last hold status): the
        hold's link is gone, ``last_error`` below carries the story of
        what happened, and the watchdog's very next ``soft_reconnect``
        re-runs the gate, which restores ``checking`` and then the real
        status within one recovery cycle. A kept ``checking``/
        ``no_response`` would instead describe a hold nobody drives
        (its re-read task is cancelled below) — the round-2 null
        semantics, now stated explicitly.
        """
        self._cancel_serial_identity_reread()
        self._connection_identity = None
        self._serial_identity_answer_epoch = None
        self._serial_identity_hold_reconnect_origin = False
        self._conn_state = RadioConnectionState.RECONNECTING
        self._civ_stream_ready = False
        self._civ_recovering = True
        self.last_error = (
            f"The serial link on {self._serial_device} failed while waiting "
            f"for the {self.model} identity answer; reconnecting."
        )
        logger.warning(
            "rigplane (%s): abandoning the identity hold on %s: %s",
            self.model,
            self._serial_device,
            reason,
            exc_info=exc,
        )

    async def _serial_identity_reread_loop(self) -> None:
        """Re-read ``19 00`` on the open link until the radio answers.

        Backoff 1, 2, 4 and 8 s, then every 15 s. Never reopens the port —
        every serial open toggles DTR/RTS (``core/serial_open.py``) — and
        leaves the loop to ``_latch_serial_connected`` as soon as the
        identity phase stops owning the link (answer, disconnect, or a
        link that died mid-hold — MOR-3071 round 2 hands the link to the
        watchdog's recovery instead).
        """
        attempts = 0
        try:
            while True:
                backoff = self._SERIAL_IDENTITY_REREAD_BACKOFF_S
                delay = (
                    backoff[attempts]
                    if attempts < len(backoff)
                    else self._SERIAL_IDENTITY_REREAD_STEADY_S
                )
                attempts += 1
                await asyncio.sleep(delay)
                if not self._identity_phase_owns_open_link():
                    if self._identity_hold_status_active():
                        # Still in the hold, but the link died under it:
                        # hand it to the watchdog's recovery (round 2).
                        self._abandon_serial_identity_hold(
                            "the serial link stopped being ready"
                        )
                    return
                epoch = self._civ_epoch
                payload = await self._read_serial_identity_payload()
                if (
                    epoch != self._civ_epoch
                    or not self._identity_phase_owns_open_link()
                ):
                    return
                if not self._record_serial_identity_payload(payload):
                    continue
                self.last_error = None
                # MOR-3078: consume the origin once, before anything can
                # observe it again — exactly one tail may run for this
                # hold, whichever path completed it.
                reconnect_origin = self._serial_identity_hold_reconnect_origin
                self._serial_identity_hold_reconnect_origin = False
                self._latch_serial_connected()
                if reconnect_origin:
                    # MOR-3078: the hold was entered by soft_reconnect,
                    # which parked managed TX and owes its success tail.
                    # ``rearm_managed_tx`` never propagates failures, so
                    # the latch cannot be undone by a re-arm problem; the
                    # callback is isolated exactly as soft_reconnect's
                    # own tail isolates it.
                    if (
                        self._managed_tx_composition is not None
                        and self._serial_session.ready
                        and self._civ_transport is not None
                    ):
                        await self.rearm_managed_tx()
                    if self._on_reconnect is not None:
                        try:
                            self._on_reconnect()
                        except Exception:
                            logger.debug(
                                "serial identity re-read: _on_reconnect "
                                "callback failed",
                                exc_info=True,
                            )
                elif (
                    self._managed_tx_composition is not None
                    and self._serial_session.ready
                    and self._civ_transport is not None
                ):
                    try:
                        await self._arm_managed_tx()
                    except Exception:
                        # MOR-3071 round 4: ``connect()`` lets an
                        # ``_arm_managed_tx`` failure propagate to its
                        # caller only AFTER the latch — CONNECTED, the
                        # stream flags and the identity survive it. The
                        # re-read has no caller to raise to: the same
                        # failure is logged with its traceback and the
                        # latch stands (the link has already answered).
                        logger.error(
                            "rigplane (%s): managed TX arming failed after "
                            "the identity answer on %s; the connection "
                            "stays up",
                            self.model,
                            self._serial_device,
                            exc_info=True,
                        )
                return
        except asyncio.CancelledError:
            pass
        except Exception as exc:
            # An OSError from a dead writer (or any other non-cancel
            # error, ``_arm_managed_tx`` above included) must not kill
            # the re-read with an unretrieved task exception — hand the
            # link to the watchdog's recovery, which reopens and re-gates.
            self._abandon_serial_identity_hold(
                f"the identity re-read failed: {exc}", exc=exc
            )

    # ------------------------------------------------------------------
    # Renumbered-node rediscovery (MOR-1453)
    # ------------------------------------------------------------------

    def _default_enumerate_serial_ports(self) -> "list[SerialPortCandidate]":
        """Real OS port enumeration -- read-only, never opens a device."""
        from .discovery import enumerate_serial_ports

        return enumerate_serial_ports()

    def _capture_serial_identity(self) -> None:
        """Snapshot the connected adapter's hw identity for rediscovery.

        Called after every successful connect/soft_reconnect so a
        fingerprint (USB ``serial_number``, else vid/pid) is always fresh
        for the *current* adapter, not assumed stable across the very
        first connect (itself possibly post-replug). Resets to ``None``
        when enumeration fails or the path isn't found (synthetic paths
        in tests), rather than keeping a stale value.
        """
        self._serial_hw_identity = None
        try:
            enumerated = self._enumerate_serial_ports_fn()
        except Exception:
            logger.debug("serial identity capture failed", exc_info=True)
            return
        for candidate in enumerated:
            if candidate.device == self._serial_device:
                self._serial_hw_identity = (
                    candidate.serial_number,
                    candidate.vid,
                    candidate.pid,
                )
                return

    async def _maybe_rediscover_serial_device(self) -> None:
        """Rediscover a renumbered device node before retrying connect.

        macOS encodes the physical USB port in the device path
        (``/dev/cu.usbserial-1420``), so a replug renames the node and
        leaves ``soft_reconnect`` retrying a vanished path forever
        (follow-up from MOR-1440). No-op while the configured path exists,
        and before the first successful port open (MOR-2876).

        Identity model (review round 2 design ruling): a CI-V address
        probe as *primary* signal was rejected -- a different radio can
        answer it (the documented IC-705/X6200 shared 0xA4 collision),
        hijacking a neighbor's port and repeatedly writing CI-V frames
        into that neighbor's live link. Instead:

        - PRIMARY: match candidates (OS-enumerated, filtered by
          ``_serial_reconnect_glob``) against our own USB ``serial_number``
          captured at the last connect -- no port is ever opened for this.
          An empty string (pyserial surfaces ``""``, not ``None``, for a
          stripped-descriptor adapter on some Linux/Windows hosts) is
          treated as "no fingerprint", not a wildcard -- falls through to
          FALLBACK instead of matching any other empty-serial candidate.
          A candidate is also rejected when its enumerated vid/pid is
          *known* to differ from ours, closing coincidental cross-vendor
          serial-string collisions.
        - FALLBACK (adapter exposed no usable serial_number): the CI-V
          probe, but skip -- never open -- a candidate whose vid/pid is
          *known* to differ from ours. When our own identity is entirely
          unknown (enumeration failed or never found our path), FALLBACK
          runs with zero vid/pid filtering. Narrows, does not eliminate,
          the collision risk when neither side exposes a serial number
          and both share (or lack) vid/pid -- documented residual gap.
          Ports rejected by ``discovery._is_candidate`` (e.g. non-USB or
          virtual ports) are never enumerated and so never rediscoverable
          -- silent by design, matching initial discovery's own filter.
        """
        if os.path.exists(self._serial_device):
            return
        if not self._has_connected_once:
            # No identity was ever captured to match, and FALLBACK would
            # probe other enumerated ports matching the glob.
            return
        try:
            enumerated = self._enumerate_serial_ports_fn()
        except Exception:
            logger.debug("serial rediscovery: port enumeration failed", exc_info=True)
            return
        candidates = sorted(
            (
                c
                for c in enumerated
                if c.device != self._serial_device
                and fnmatch.fnmatch(c.device, self._serial_reconnect_glob)
            ),
            key=lambda c: c.device,
        )
        if not candidates:
            return

        target_serial, target_vid, target_pid = self._serial_hw_identity or (
            None,
            None,
            None,
        )
        adopted: str | None = None
        if target_serial:
            for candidate in candidates:
                if candidate.serial_number != target_serial:
                    continue
                if target_vid is not None and candidate.vid not in (None, target_vid):
                    continue
                if target_pid is not None and candidate.pid not in (None, target_pid):
                    continue
                adopted = candidate.device
                break
        else:
            for candidate in candidates:
                if target_vid is not None and candidate.vid not in (None, target_vid):
                    continue
                if target_pid is not None and candidate.pid not in (None, target_pid):
                    continue
                try:
                    address = await self._civ_identity_probe(candidate.device)
                except Exception:
                    logger.debug(
                        "serial rediscovery: identity probe failed for %s",
                        candidate.device,
                        exc_info=True,
                    )
                    continue
                if address is not None and address == self._radio_addr:
                    adopted = candidate.device
                    break

        if adopted is None:
            return
        logger.warning(
            "rigplane (%s): serial node %s vanished; rediscovered radio at %s",
            self.model,
            self._serial_device,
            adopted,
        )
        self._serial_device = adopted
        if self._serial_civ_link is not None:
            self._serial_civ_link.set_device(adopted)
        self._serial_audio_driver.set_serial_port(adopted)

    async def _default_civ_identity_probe(self, port: str) -> int | None:
        """Probe *port* for a CI-V radio and return its address, or ``None``.

        FALLBACK only (see ``_maybe_rediscover_serial_device``) -- opens
        the port and writes a real CI-V frame.
        """
        from .discovery import probe_serial_civ

        result = await probe_serial_civ(port, baud_rates=[self._serial_baudrate])
        return result.address if result is not None else None

    # ------------------------------------------------------------------
    # Scope
    # ------------------------------------------------------------------

    async def get_scope_session_state(self) -> tuple[bool, bool]:
        """Reject unsupported serial scope sessions before any CI-V mutation."""
        self._check_connected()
        self._ensure_scope_baud_guardrail()
        return await super().get_scope_session_state()

    async def enable_scope(
        self,
        *,
        output: bool = True,
        policy: ScopeCompletionPolicy | str = ScopeCompletionPolicy.VERIFY,
        timeout: float = 5.0,
    ) -> None:
        self._check_connected()
        self._ensure_scope_baud_guardrail()
        await super().enable_scope(output=output, policy=policy, timeout=timeout)

    async def disable_scope(
        self, *, policy: ScopeCompletionPolicy | str = ScopeCompletionPolicy.FAST
    ) -> None:
        await super().disable_scope(policy=policy)
        pol = ScopeCompletionPolicy(policy)
        wait_resp = pol == ScopeCompletionPolicy.STRICT
        resp = await self._send_civ_raw(
            self._commands.scope_off(to_addr=self._radio_addr),
            wait_response=wait_resp,
        )
        if wait_resp and resp is not None and parse_ack_nak(resp) is False:
            raise CommandError("Radio rejected scope disable")

    # ------------------------------------------------------------------
    # Neutral AudioTransport surface (MOR-532 epic, MOR-540)
    # ------------------------------------------------------------------

    async def start_rx(
        self,
        callback: Callable[[AudioPacket | None], None],
        *,
        jitter_depth: int = 5,
    ) -> None:
        """Start RX capture from the USB CODEC (``AudioTransport.start_rx``).

        ``jitter_depth`` is accepted for ``AudioRuntimeMixin.start_rx`` signature
        compatibility but unused: this is a locally captured CODEC stream with no
        network wire, so there is no inter-packet jitter to buffer against.

        Locally captured PCM (transcoded to Opus when the negotiated codec
        is an Opus variant) is framed into synthetic :class:`AudioPacket`
        instances marked with ``SYNTHETIC_RX_IDENT`` — there is no LAN wire
        header on this path.
        """
        if not callable(callback):
            raise TypeError("callback must be callable and accept AudioPacket | None.")
        self._check_connected()

        owner_loop = asyncio.get_running_loop()

        sample_rate = self.audio_sample_rate
        channels = self._serial_audio_channels_for_codec()
        frame_ms = 20
        transcoder = (
            self._get_pcm_transcoder(
                sample_rate=sample_rate,
                channels=channels,
                frame_ms=frame_ms,
            )
            if self._serial_codec_is_opus()
            else None
        )

        def _deliver_rx_frame(pcm_frame: bytes) -> None:
            if self._serial_rx_delivery is not _deliver_rx_frame:
                return
            payload = pcm_frame
            if transcoder is not None:
                try:
                    payload = transcoder.pcm_to_opus(pcm_frame)
                except Exception:
                    logger.warning(
                        "serial-audio: failed to encode PCM frame to Opus",
                        exc_info=True,
                    )
                    return
            packet = AudioPacket(
                ident=SYNTHETIC_RX_IDENT,
                send_seq=self._serial_audio_seq,
                data=payload,
            )
            self._serial_audio_seq = (self._serial_audio_seq + 1) & 0xFFFF
            callback(packet)

        def _on_pcm_frame(pcm_frame: bytes) -> None:
            # PortAudio thread: schedule delivery; packet work runs on the
            # owner loop (MOR-2465).
            owner_loop.call_soon_threadsafe(_deliver_rx_frame, pcm_frame)

        # Arm delivery identity and the user callback before the await:
        # the driver may invoke the callback before start_rx returns. If
        # the start fails, roll both back so a still-running previous
        # session keeps delivering instead of being orphaned (MOR-2465).
        previous_delivery = self._serial_rx_delivery
        previous_callback = self._opus_rx_user_callback
        self._opus_rx_user_callback = callback
        self._serial_rx_delivery = _deliver_rx_frame
        try:
            await self._serial_audio_driver.start_rx(
                _on_pcm_frame,
                sample_rate=sample_rate,
                channels=channels,
                frame_ms=frame_ms,
            )
        except BaseException:
            self._serial_rx_delivery = previous_delivery
            self._opus_rx_user_callback = previous_callback
            raise

    async def stop_rx(self) -> None:
        """Stop RX capture (``AudioTransport.stop_rx``)."""
        self._serial_rx_delivery = None
        self._opus_rx_user_callback = None
        await self._serial_audio_driver.stop_rx()

    async def start_tx(self) -> None:
        """Arm the USB CODEC TX path (``AudioTransport.start_tx``)."""
        self._check_connected()
        await self._serial_audio_driver.start_tx(
            sample_rate=self.audio_sample_rate,
            channels=self._serial_audio_channels_for_codec(),
            frame_ms=20,
        )

    async def push_tx(self, data: bytes) -> None:
        """Push one TX frame (``AudioTransport.push_tx``).

        Opus input is transcoded to PCM when the negotiated codec is an
        Opus variant; the USB CODEC driver always consumes PCM.
        """
        self._check_connected()
        if not self._serial_audio_driver.tx_running:
            raise RuntimeError("Audio TX not started")
        payload = bytes(data)
        if self._serial_codec_is_opus():
            transcoder = self._get_pcm_transcoder(
                sample_rate=self.audio_sample_rate,
                channels=self._serial_audio_channels_for_codec(),
                frame_ms=20,
            )
            payload = transcoder.opus_to_pcm(payload)
        await self._serial_audio_driver._push_tx_pcm(payload)

    async def stop_tx(self) -> None:
        """Close the TX path (``AudioTransport.stop_tx``)."""
        await self._serial_audio_driver.stop_tx()
        self._pcm_tx_fmt = None

    # ------------------------------------------------------------------
    # Audio RX (legacy opus-family shims -> neutral AudioTransport)
    # ------------------------------------------------------------------

    async def start_audio_rx_opus(
        self,
        callback: Callable[[AudioPacket | None], None],
        *,
        jitter_depth: int = 5,
    ) -> None:
        if isinstance(jitter_depth, bool) or not isinstance(jitter_depth, int):
            raise TypeError(
                f"jitter_depth must be an int, got {type(jitter_depth).__name__}."
            )
        if jitter_depth < 0:
            raise ValueError(f"jitter_depth must be >= 0, got {jitter_depth}.")
        self._opus_rx_jitter_depth = jitter_depth
        await self.start_rx(callback)

    async def stop_audio_rx_opus(self) -> None:
        await self.stop_rx()

    async def start_audio_rx_pcm(
        self,
        callback: Callable[[bytes | None], None],
        *,
        sample_rate: int = 48000,
        channels: int = 1,
        frame_ms: int = 20,
        jitter_depth: int = 5,
    ) -> None:
        if not callable(callback):
            raise TypeError("callback must be callable and accept bytes | None.")
        for name, value in (
            ("sample_rate", sample_rate),
            ("channels", channels),
            ("frame_ms", frame_ms),
            ("jitter_depth", jitter_depth),
        ):
            if isinstance(value, bool) or not isinstance(value, int):
                raise TypeError(f"{name} must be an int, got {type(value).__name__}.")
        if jitter_depth < 0:
            raise ValueError(f"jitter_depth must be >= 0, got {jitter_depth}.")
        if (sample_rate * frame_ms) % 1000 != 0:
            raise AudioFormatError(
                "sample_rate * frame_ms must produce an integer frame size."
            )

        self._check_connected()
        self._pcm_rx_user_callback = callback
        self._pcm_rx_jitter_depth = jitter_depth

        def _on_pcm_frame(pcm_frame: bytes) -> None:
            callback(pcm_frame)

        await self._serial_audio_driver.start_rx(
            _on_pcm_frame,
            sample_rate=sample_rate,
            channels=channels,
            frame_ms=frame_ms,
        )

    async def stop_audio_rx_pcm(self) -> None:
        self._pcm_rx_user_callback = None
        await self._serial_audio_driver.stop_rx()

    # ------------------------------------------------------------------
    # Audio TX (legacy opus-family shims -> neutral AudioTransport)
    # ------------------------------------------------------------------

    async def start_audio_tx_opus(self) -> None:
        await self.start_tx()

    async def push_audio_tx_opus(self, opus_data: bytes) -> None:
        await self.push_tx(opus_data)

    async def stop_audio_tx_opus(self) -> None:
        await self.stop_tx()

    # ------------------------------------------------------------------
    # Audio TX (PCM) — arms ``_pcm_tx_fmt`` so ``push_audio_tx_pcm`` works
    # over the serial USB CODEC path (MOR-242).
    # ------------------------------------------------------------------

    async def start_audio_tx_pcm(
        self,
        *,
        sample_rate: int | None = None,
        channels: int | None = None,
        frame_ms: int | None = None,
    ) -> None:
        # Signature mirrors the base ``AudioRuntimeMixin`` contract (``int |
        # None``) so subclassing does not violate the Liskov substitution
        # principle; ``None`` resolves to the serial-path defaults.
        if sample_rate is None:
            sample_rate = 48000
        if channels is None:
            channels = 1
        if frame_ms is None:
            frame_ms = 20
        for name, value in (
            ("sample_rate", sample_rate),
            ("channels", channels),
            ("frame_ms", frame_ms),
        ):
            if isinstance(value, bool) or not isinstance(value, int):
                raise TypeError(f"{name} must be an int, got {type(value).__name__}.")
        if (sample_rate * frame_ms) % 1000 != 0:
            raise AudioFormatError(
                "sample_rate * frame_ms must produce an integer frame size."
            )

        # Icom USB CODEC mic input is mono-only by hardware; the LAN path
        # enforces this by forcing txcodec to a mono value in _send_conninfo
        # (issue #794).  For the serial path we clamp channels to 1 here so
        # that PortAudio always opens the USB CODEC as a mono output stream.
        # Opening with channels=2 causes the radio ALC to behave erratically
        # for the first 5-10 seconds of TX (GH#1382 regression; consistent with
        # the MOR-238 RX clamp).
        tx_channels = 1

        self._check_connected()
        await self._serial_audio_driver.start_tx(
            sample_rate=sample_rate,
            channels=tx_channels,
            frame_ms=frame_ms,
        )
        self._pcm_tx_fmt = (sample_rate, tx_channels, frame_ms)

    async def push_audio_tx_pcm(
        self,
        pcm_bytes: bytes | bytearray | memoryview,
    ) -> None:
        self._check_connected()
        if self._pcm_tx_fmt is None:
            raise RuntimeError(
                "PCM TX not started; call start_audio_tx_pcm() before push_audio_tx_pcm()."
            )
        if not isinstance(pcm_bytes, (bytes, bytearray, memoryview)):
            raise AudioFormatError("PCM input must be bytes-like.")
        sample_rate, channels, frame_ms = self._pcm_tx_fmt
        if (sample_rate * frame_ms) % 1000 != 0:
            raise AudioFormatError(
                "sample_rate * frame_ms must produce an integer frame size."
            )
        frame_samples = (sample_rate * frame_ms) // 1000
        expected = frame_samples * channels * 2
        frame = bytes(pcm_bytes)
        if len(frame) != expected:
            raise AudioFormatError(
                f"PCM frame size mismatch: expected {expected} bytes "
                f"({frame_ms}ms at {sample_rate}Hz, {channels}ch s16le), got {len(frame)}."
            )
        await self._serial_audio_driver._push_tx_pcm(frame)

    async def stop_audio_tx_pcm(self) -> None:
        await self.stop_audio_tx_opus()

    async def _push_pcm_tx(self, frame: bytes) -> None:
        if not isinstance(frame, bytes):
            raise TypeError(f"frame must be bytes, got {type(frame).__name__}.")
        if len(frame) == 0:
            raise ValueError("frame must not be empty.")

        self._check_connected()
        await self._serial_audio_driver._push_tx_pcm(frame)

    # ------------------------------------------------------------------
    # Serial stubs (no-ops for serial transport)
    # ------------------------------------------------------------------

    async def _send_open_close(self, *, open_stream: bool) -> None:
        _ = open_stream
        return None

    async def _send_token(self, magic: int) -> None:
        _ = magic
        return None

    # ------------------------------------------------------------------
    # CIV data watchdog
    # ------------------------------------------------------------------

    def _start_civ_data_watchdog(self) -> None:
        _existing_watchdog = getattr(self, "_civ_data_watchdog_task", None)
        if _existing_watchdog is not None and not _existing_watchdog.done():
            return
        self._civ_data_watchdog_task = asyncio.create_task(
            self._serial_civ_watchdog_loop(),
            name="serial-civ-watchdog",
        )

    async def _stop_civ_data_watchdog(self) -> None:
        task = getattr(self, "_civ_data_watchdog_task", None)
        if task is not None and not task.done():
            task.cancel()
            try:
                await asyncio.wait_for(
                    task, timeout=self._SERIAL_CIV_WATCHDOG_TEARDOWN_TIMEOUT_S
                )
            except asyncio.CancelledError:
                pass
            except asyncio.TimeoutError:
                # MOR-2081 defence in depth: the primary fix (the two
                # discriminators in CivRuntime.stop_pump / IcomCommander.stop)
                # makes this branch unreachable for the traced mechanism;
                # this bounds any future regression of the same shape to a
                # loud, diagnosable failure instead of a hung disconnect().
                logger.error(
                    "civ-data-watchdog: teardown await exceeded %.1fs bound "
                    "(task cancelled=%s, %r); continuing disconnect",
                    self._SERIAL_CIV_WATCHDOG_TEARDOWN_TIMEOUT_S,
                    task.cancelled(),
                    task,
                )
        self._civ_data_watchdog_task = None

    async def _serial_civ_watchdog_loop(self) -> None:
        # Number of consecutive failed soft-reconnect attempts. Drives a capped
        # exponential backoff and demotes repeated, identical failures from a
        # WARNING-with-traceback to a quiet DEBUG so a transient/vanished port
        # does not flood the log (MOR-237).
        consecutive_failures = 0
        try:
            while True:
                await asyncio.sleep(self._SERIAL_WATCHDOG_INTERVAL_S)
                if self._conn_state not in (
                    RadioConnectionState.CONNECTED,
                    RadioConnectionState.RECONNECTING,
                ):
                    continue
                if self._identity_phase_owns_open_link():
                    # MOR-3071: the identity phase owns the open link and
                    # its re-read task retries ``19 00`` in place — the
                    # watchdog must neither soft_reconnect nor reopen
                    # here (every open toggles DTR/RTS), and its
                    # ready-branch must not latch CONNECTED without an
                    # identity answer.
                    continue
                if (
                    self._conn_state == RadioConnectionState.CONNECTED
                    and self._serial_civ_timeout_evidence_crossed_threshold()
                ):
                    await self._declare_serial_link_down()
                    continue
                if (
                    self._serial_session.ready
                    and self._serial_identity_answered_for_current_link()
                ):
                    # MOR-3071 round 4: the ready-branch latches CONNECTED
                    # only when the identity gate answered for the CURRENT
                    # CI-V generation. A ready session without an answer
                    # (an abandoned hold, a crashed gate, any RECONNECTING
                    # leftover) falls through to soft_reconnect below,
                    # which re-opens and runs the identity gate — it must
                    # never latch here.
                    self._civ_stream_ready = True
                    self._civ_recovering = False
                    self._conn_state = RadioConnectionState.CONNECTED
                    # MOR-2861: do NOT re-stamp ``_last_civ_data_received``
                    # here — a session that merely reads ready provides no
                    # liveness evidence; only a CI-V frame the RX pump
                    # actually parsed may stamp it (``_civ_rx.py``).
                    consecutive_failures = 0
                    continue

                self._civ_stream_ready = False
                self._civ_recovering = True
                try:
                    await self.soft_reconnect()
                    consecutive_failures = 0
                except Exception as exc:
                    consecutive_failures += 1
                    if consecutive_failures == 1:
                        # First failure of a run: surface it once, with the
                        # full traceback for diagnosis.
                        logger.warning(
                            "serial-civ-watchdog: soft reconnect failed (%s); "
                            "retrying with backoff",
                            exc,
                            exc_info=True,
                        )
                    else:
                        # Subsequent identical failures (e.g. port still gone):
                        # keep the log quiet, just note the count.
                        logger.debug(
                            "serial-civ-watchdog: soft reconnect still failing "
                            "(attempt %d): %s",
                            consecutive_failures,
                            exc,
                        )
                    await asyncio.sleep(
                        self._serial_watchdog_retry_delay(consecutive_failures)
                    )
        except asyncio.CancelledError:
            pass

    def _serial_watchdog_retry_delay(self, consecutive_failures: int) -> float:
        """Capped exponential backoff for repeated reconnect failures.

        The first retry uses ``_SERIAL_WATCHDOG_RETRY_S``; each subsequent
        failure doubles the delay up to ``_SERIAL_WATCHDOG_RETRY_MAX_S`` so a
        long-absent port is retried slowly and quietly instead of twice a
        second with a full traceback (MOR-237).
        """
        exponent = max(0, consecutive_failures - 1)
        delay: float = float(self._SERIAL_WATCHDOG_RETRY_S)
        cap: float = float(self._SERIAL_WATCHDOG_RETRY_MAX_S)
        if delay >= cap:
            return cap
        if delay <= 0.0:
            return delay
        saturation_exponent = math.ceil(math.log2(cap / delay))
        if exponent >= saturation_exponent:
            return cap
        return math.ldexp(delay, exponent)

    def _civ_watchdog_rebaseline(self) -> None:
        """Reset link-death detector baselines against current state.

        MOR-1440 review round 2 (B1): every (re)connect installs a
        brand-new ``SerialCivTransport`` (``SerialSessionDriver.connect``
        always constructs one, even on reconnect), so its
        ``rx_packet_count`` restarts at 0 — a stale high-water mark from the
        outgoing transport would otherwise make the "real traffic happened"
        reset signal read false by construction right when it matters most.
        Likewise, ``_civ_request_tracker.timeout_count`` is a lifetime
        counter never reset on reconnect, so a frozen
        ``_civ_watchdog_last_seen_timeouts`` baseline (left stale by the
        RECONNECTING short-circuit in ``_serial_civ_watchdog_loop``) would
        otherwise credit an entire outage's worth of banked timeouts as one
        lump delta against the link that just recovered.

        Called from two places: (a) lazily, inside
        :meth:`_serial_civ_timeout_evidence_crossed_threshold`, whenever the
        transport identity has changed since the last tick — a defensive net
        that catches any path that swaps ``_civ_transport`` — and (b)
        explicitly at the end of :meth:`soft_reconnect`, the actual
        RECONNECTING -> CONNECTED transition, so the outage's banked
        timeouts never survive to the first post-recovery evidence check.
        """
        rx_count = getattr(self._civ_transport, "rx_packet_count", None)
        self._civ_watchdog_last_seen_rx_packets = (
            rx_count if isinstance(rx_count, int) else 0
        )
        self._civ_watchdog_last_seen_timeouts = self._civ_request_tracker.timeout_count
        self._civ_consecutive_timeouts = 0
        # MOR-2861: the silence evidence clock resets here too — a swapped-in
        # transport must not inherit an accrued silent interval.
        self._civ_silence_started_monotonic = None
        self._civ_watchdog_last_transport = self._civ_transport

    def _serial_link_down_silence_limit_s(self) -> float:
        """Serial link-down silence limit N (MOR-2861): the longest
        believable "polls outstanding, no frame parsed" interval a healthy
        link can show.

        N = two answer windows (``_civ_get_timeout``) + one watchdog tick
        (``_SERIAL_WATCHDOG_INTERVAL_S``) — about 4.2 s at the 2.0 s answer
        window. No cadence term: the silence clock only runs while a waiter
        younger than one answer window is outstanding (nothing outstanding
        clears it; ``CivRequestTracker.response_pending_count`` ignores
        older waiters), so a slow poll cadence — with idle gaps between
        polls — can never run the clock. Two full windows of continuously
        fresh polls outstanding with ``rx_packet_count`` frozen means the
        link is down; the extra tick covers watchdog-tick phase against the
        asynchronous RX pump.
        """
        return 2.0 * self._civ_get_timeout + float(self._SERIAL_WATCHDOG_INTERVAL_S)

    def _serial_civ_timeout_evidence_crossed_threshold(self) -> bool:
        """Track consecutive CI-V command timeouts as live-link evidence.

        Deliberately does *not* key off ``_last_civ_data_received``: this
        watchdog's own "still ready" fast path re-stamps that timestamp every
        tick purely because the raw health flag reads true (see the branch
        below), which would mask a silently-dead link exactly like the raw
        flag does. ``rx_packet_count`` only advances when a frame was
        actually parsed off the wire (``SerialCivTransport.receive_packet``),
        so it is unaffected by that stamp and safe to use as the "real
        traffic happened" reset signal.

        Shared-bus blind spot: on a shared CI-V bus (multiple radios/
        controllers on the same serial line), ``rx_packet_count`` advances
        for *any* frame this transport parses off the wire, including a
        transceive broadcast from another device — not only responses to
        our own commands. That is an acceptable false "still alive" signal
        (traffic proves the physical bus is up), just not scoped to "this
        radio is answering us" as tightly as it may look. Not applicable to
        direct USB-CI-V connections (e.g. IC-7300), which have no shared bus.

        MOR-1440 review round 2 (B1): a (re)connect can swap in a brand-new
        transport between ticks. If it has, re-baseline against it instead
        of judging this tick — see :meth:`_civ_watchdog_rebaseline`.

        MOR-2861, second evidence path — silent link with polls outstanding:
        fire-and-forget polls and scope GETs cancelled at the 0.2 s answer
        window never produce a tracker timeout, so the timeout counter alone
        misses a link that has simply stopped answering (2026-09-28 IC-7300
        incident). If fresh polls are outstanding
        (``CivRequestTracker.response_pending_count`` with
        ``max_age_s=_civ_get_timeout`` — GET response waiters plus the keyed
        response sinks of fire-and-forget reads, each younger than one
        answer window, and not the bare ACK sinks of set-type sends) and
        ``rx_packet_count`` has not advanced for the silence limit
        (``_serial_link_down_silence_limit_s``), the link is declared down.
        A waiter older than the answer window is a lost poll, not evidence
        that the radio still owes us data, so it neither starts nor holds
        the silence clock.
        """
        if self._civ_transport is not self._civ_watchdog_last_transport:
            self._civ_watchdog_rebaseline()
            return False

        tracker = self._civ_request_tracker
        total = tracker.timeout_count
        rx_count = getattr(self._civ_transport, "rx_packet_count", None)
        rx_advanced = isinstance(rx_count, int) and rx_count > (
            self._civ_watchdog_last_seen_rx_packets
        )
        now = self._civ_silence_time_source()
        pending = tracker.response_pending_count(
            now_monotonic=now,
            max_age_s=self._civ_get_timeout,
        )
        if rx_advanced:
            self._civ_consecutive_timeouts = 0
            self._civ_silence_started_monotonic = None
        elif pending > 0 and self._civ_silence_started_monotonic is None:
            # First frozen tick with polls outstanding: start the silence
            # clock. (Timeout deltas deliberately do NOT reset it — a tracked
            # command timeout is itself proof of silence, the two evidence
            # paths race and whichever threshold crosses first reports.)
            self._civ_silence_started_monotonic = now
        elif total > self._civ_watchdog_last_seen_timeouts:
            self._civ_consecutive_timeouts += (
                total - self._civ_watchdog_last_seen_timeouts
            )
        elif pending == 0:
            # Nothing outstanding: a quiet link is idle, not down.
            self._civ_silence_started_monotonic = None

        self._civ_watchdog_last_seen_timeouts = total
        if isinstance(rx_count, int):
            self._civ_watchdog_last_seen_rx_packets = rx_count
        if self._civ_consecutive_timeouts >= self._SERIAL_LINK_DOWN_TIMEOUT_THRESHOLD:
            self._civ_link_down_note = (
                f"{self._civ_consecutive_timeouts} consecutive CI-V command "
                "timeout(s) with no response"
            )
            return True
        silence_started = self._civ_silence_started_monotonic
        if silence_started is not None:
            elapsed = now - silence_started
            limit_s = self._serial_link_down_silence_limit_s()
            if elapsed >= limit_s:
                self._civ_link_down_note = (
                    "polls outstanding and no CI-V frame parsed for "
                    f"{elapsed:.1f}s (silence limit {limit_s:.1f}s)"
                )
                return True
        return False

    async def _declare_serial_link_down(self) -> None:
        """Force the state machine to link-down on consecutive CI-V timeouts.

        Orders the *safety-critical* part of ``soft_disconnect``'s teardown
        the same way it does: park managed TX first (a WRITE_ON granted on
        the strength of a provider still marked ready must not land on a
        dead wire), then stop audio, then tear down the raw serial link so
        the next watchdog tick's ``ready`` check reads honest state instead
        of a stale healthy flag that would otherwise undo this transition.

        Deliberately does *not* mirror the rest of ``soft_disconnect``: it
        leaves the CI-V worker and RX pump running and does not advance the
        CI-V generation. The watchdog needs both alive to drive recovery
        (``soft_reconnect`` — called from ``_serial_civ_watchdog_loop`` right
        after this — is what tears them down and rebuilds them for the new
        transport); a full disconnect-style teardown here would leave
        nothing to restart the recovery loop.
        """
        logger.error(
            "rigplane (%s): serial link-down on %s — %s; marking connection reconnecting",
            self.model,
            self._serial_device,
            self._civ_link_down_note,
        )
        self._conn_state = RadioConnectionState.RECONNECTING
        self._civ_link_down_ever_declared = True
        self._civ_stream_ready = False
        self._civ_recovering = True
        self._civ_consecutive_timeouts = 0
        # MOR-3071 round 4: the identity answer belonged to the link that
        # just died — the recovery soft_reconnect re-answers before
        # CONNECTED may latch again.
        self._serial_identity_answer_epoch = None
        await self._park_managed_tx()
        await self._stop_serial_audio_driver()
        await self._serial_session.disconnect()

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    async def _stop_serial_audio_driver(self) -> None:
        self._pcm_tx_fmt = None
        self._pcm_rx_user_callback = None
        self._opus_rx_user_callback = None
        self._serial_rx_delivery = None
        try:
            await self._serial_audio_driver.stop_tx()
        except Exception:
            logger.debug("serial-audio: failed to stop TX path", exc_info=True)
        try:
            await self._serial_audio_driver.stop_rx()
        except Exception:
            logger.debug("serial-audio: failed to stop RX path", exc_info=True)

    def _ensure_scope_baud_guardrail(self) -> None:
        if self._serial_baudrate >= _SERIAL_SCOPE_MIN_BAUD:
            return
        if not self._allow_low_baud_scope:
            if not self._low_baud_scope_warned:
                logger.warning(
                    "Scope disabled at low baud rate (%d < %d). "
                    "Set allow_low_baud_scope=True to override or set "
                    "ICOM_SERIAL_SCOPE_ALLOW_LOW_BAUD=1.",
                    self._serial_baudrate,
                    _SERIAL_SCOPE_MIN_BAUD,
                )
                self._low_baud_scope_warned = True
            raise ConnectionError(
                f"Scope unavailable at {self._serial_baudrate} baud "
                f"(minimum {_SERIAL_SCOPE_MIN_BAUD}). "
                f"Set allow_low_baud_scope=True to override."
            )

    def _serial_audio_channels_for_codec(self) -> int:
        return 2 if self._audio_codec in _TWO_CHANNEL_CODECS else 1

    def _serial_codec_is_opus(self) -> bool:
        return self._audio_codec in {
            AudioCodec.OPUS_1CH,
            AudioCodec.OPUS_2CH,
        }

    async def _ensure_audio_started(self) -> None:
        pass

    async def _ensure_audio_stopped(self) -> None:
        pass


__all__ = [
    "_IcomSerialRadioBase",
    "_SerialAudioDriver",
    "_env_bool",
    "_SERIAL_SCOPE_MIN_BAUD",
]
