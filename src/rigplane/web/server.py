"""WebSocket + HTTP server for the rigplane Web UI.

Implements:
- Minimal asyncio HTTP server (no external deps)
- RFC 6455 WebSocket upgrade
- HTTP endpoints: GET /, GET /api/v1/info, GET /api/v1/capabilities
- WebSocket channels: /api/v1/ws, /api/v1/scope, /api/v1/audio

Architecture
------------
Single asyncio.start_server accepts raw TCP. For each connection:
1. Read HTTP request line + headers
2. If Upgrade: websocket → perform RFC 6455 handshake, route to WS handler
3. Else → serve HTTP response (static file or JSON API)

The server holds an optional radio protocol instance and uses it for
command dispatch and scope data delivery.
"""

from __future__ import annotations

import asyncio
import gzip as _gzip
import hmac
import json
import logging
import math
import mimetypes
import os
import pathlib
import signal as _signal
import sys
import time
import urllib.parse
from collections.abc import Callable, Coroutine
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Literal, TextIO

from .. import __version__
from .._bounded_queue import BoundedQueue
from ..radio_state import RadioState
from ..capabilities import CAP_AUDIO, CAP_SCOPE
from ..exceptions import TimeoutError as RigplaneTimeoutError
from ..audio_analyzer import AudioAnalyzer
from ..audio_fft_scope import AudioFftScope
from ..env_config import (
    get_audio_rx_jitter_ceiling_ms,
    get_audio_rx_jitter_floor_ms,
)
from ..startup_checks import assert_radio_startup_ready
from ._delta_encoder import DeltaEncoder  # noqa: TID251
from .discovery import DiscoveryResponder  # noqa: TID251
from .dx_cluster import DXClusterClient, SpotBuffer  # noqa: TID251
from .handlers import (  # noqa: TID251
    AudioBroadcaster,
    AudioHandler,
    ControlHandler,
    DiagnosticsHandler,
    ScopeHandler,
)
from .rtc import handle_rtc_offer, rtc_capability_info, webrtc_available  # noqa: TID251
from .radio_poller import CommandQueue, DisableScope, EnableScope, RadioPoller  # noqa: TID251
from .runtime_helpers import (  # noqa: TID251
    build_public_state_payload,
    classify_radio_health,
    radio_ready,
    runtime_capabilities,
)
from .websocket import (  # noqa: TID251
    WS_KEEPALIVE_INTERVAL,
    WebSocketConnection,
    make_accept_key,
    negotiate_deflate,
)
from ..radio_protocol import CivTransactionCapable

if TYPE_CHECKING:
    from ..audio_bridge import AudioBridge
    from ..profiles import RadioProfile
    from ..radio_protocol import Radio

__all__ = ["WebConfig", "WebServer", "run_web_server"]

logger = logging.getLogger(__name__)


def _install_shutdown_signal_handlers(
    loop: asyncio.AbstractEventLoop,
    on_signal: Callable[[], None],
) -> None:
    for sig in (_signal.SIGTERM, _signal.SIGINT):
        try:
            loop.add_signal_handler(sig, on_signal)
        except NotImplementedError:
            _signal.signal(sig, lambda _signum, _frame: on_signal())


_DEFAULT_STATIC_DIR = pathlib.Path(__file__).parent / "static"
_RADIO_MODEL = "IC-7610"
_MAX_POST_BODY = 256 * 1024  # 256 KiB — hard ceiling for all POST body reads
_MAX_COMMAND_BATCH_STEPS = 128
_COMMAND_BATCH_STEP_TIMEOUT = 10.0
_CIV_TRANSACTION_BATCH_STEP_TYPE = "raw_civ_transaction"
_CIV_TRANSACTION_BATCH_STEP_KEYS = frozenset(
    {"type", "id", "command", "sub", "data", "expect", "timeout_ms"}
)

_CivTransactionExpect = Literal["none", "ack", "data"]
_MISSING_BATCH_STEP_ID = object()


class _HttpBatchValidationError(ValueError):
    def __init__(self, error: str, message: str) -> None:
        super().__init__(message)
        self.error = error


@dataclass(frozen=True, slots=True)
class _HttpBatchStep:
    index: int
    name: str
    command: Any
    result: dict[str, Any]


@dataclass(frozen=True, slots=True)
class _HttpBatchTransactionStep:
    index: int
    step_id: Any
    command: int
    sub: int | None
    data: bytes
    expect: _CivTransactionExpect
    timeout: float


class _HttpCommandCollector:
    def __init__(self) -> None:
        self.commands: list[Any] = []

    def put(self, command: Any) -> None:
        self.commands.append(command)


async def _read_capped_body(
    reader: asyncio.StreamReader,
    content_length: int,
) -> bytes | None:
    """Read up to *content_length* bytes, rejecting oversize requests.

    Returns the raw bytes on success, or ``None`` when *content_length*
    exceeds :data:`_MAX_POST_BODY` (caller must send HTTP 413).
    """
    if content_length > _MAX_POST_BODY:
        return None
    return await asyncio.wait_for(
        reader.readexactly(content_length),
        timeout=5.0,
    )


def _redact_token_in_path(path: str) -> str:
    """Return `path` with any `token=` query value replaced by `***`.

    Prevents auth tokens from leaking into log captures when clients
    authenticate via the `?token=` query parameter (see issue #948).
    """
    if "token=" not in path:
        return path
    base, _, query = path.partition("?")
    if not query:
        return path
    parts = []
    for pair in query.split("&"):
        key, eq, _value = pair.partition("=")
        if key == "token" and eq:
            parts.append("token=***")
        else:
            parts.append(pair)
    return f"{base}?{'&'.join(parts)}"


# Mode/filter lists moved to RadioProfile (profiles.py)


def _load_band_plan_config_sync(path: pathlib.Path) -> dict[str, Any]:
    """Synchronous helper for :meth:`WebServer._handle_band_plan_config`.

    Reads a TOML file from disk via :func:`tomllib.load`. Runs in a
    worker thread so the event loop is not blocked.
    """
    import tomllib

    with open(path, "rb") as f:
        return tomllib.load(f)


def _format_band_plan_config(new_region: str, existing: dict[str, Any]) -> str:
    """Render the band-plan ``_config.toml`` body as a single string.

    Pure function — no I/O — so it is cheap to run on the event loop.
    Output is byte-identical to the previous inline ``f.write()``
    sequence in :meth:`WebServer._handle_band_plan_config`.
    """
    lines = [
        "# Band plan configuration\n\n",
        "[settings]\n",
        f'region = "{new_region}"\n',
    ]
    if "layers" in existing:
        lines.append("\n[layers]\n")
        for k, v in existing["layers"].items():
            lines.append(f"{k} = {'true' if v else 'false'}\n")
    return "".join(lines)


def _serialize_filter_config(profile: "RadioProfile") -> dict[str, dict[str, object]]:
    config = profile.filter_config or {}
    result: dict[str, dict[str, object]] = {}
    for mode, rule in config.items():
        result[mode] = {
            "defaults": list(rule.defaults),
            "fixed": rule.fixed,
            **({"stepHz": rule.step_hz} if rule.step_hz is not None else {}),
            **({"minHz": rule.min_hz} if rule.min_hz is not None else {}),
            **({"maxHz": rule.max_hz} if rule.max_hz is not None else {}),
            **(
                {
                    "segments": [
                        {
                            "hzMin": segment.hz_min,
                            "hzMax": segment.hz_max,
                            "stepHz": segment.step_hz,
                            "indexMin": segment.index_min,
                        }
                        for segment in rule.segments
                    ]
                }
                if rule.segments
                else {}
            ),
            **({"table": list(rule.table)} if rule.table else {}),
        }
    return result


def _serialize_keyboard_config(profile: "RadioProfile") -> dict[str, object] | None:
    keyboard = profile.keyboard
    if keyboard is None:
        return None
    return {
        "leaderKey": keyboard.leader_key,
        "leaderTimeoutMs": keyboard.leader_timeout_ms,
        "altHints": keyboard.alt_hints,
        "helpTitle": keyboard.help_title,
        "bindings": [
            {
                "id": binding.id,
                "action": binding.action,
                "sequence": list(binding.sequence),
                "section": binding.section,
                **({"label": binding.label} if binding.label else {}),
                **({"description": binding.description} if binding.description else {}),
                **({"modifiers": list(binding.modifiers)} if binding.modifiers else {}),
                **({"repeatable": True} if binding.repeatable else {}),
                **({"params": binding.params} if binding.params else {}),
            }
            for binding in keyboard.bindings
        ],
    }


def _runtime_capabilities(radio: "Radio | None") -> set[str]:
    """Backward-compatible alias to shared runtime_capabilities helper."""
    return runtime_capabilities(radio)


def _supports_scope(radio: "Radio | None") -> bool:
    return "scope" in runtime_capabilities(radio)


def _supports_audio(radio: "Radio | None") -> bool:
    return "audio" in runtime_capabilities(radio)


@dataclass
class WebConfig:
    """Configuration for :class:`WebServer`.

    Attributes:
        host: Bind address (default: 0.0.0.0).
        port: HTTP/WS port (default: 8080).
        static_dir: Directory to serve static files from.
        radio_model: Radio model string for the hello/info response.
        max_clients: Maximum concurrent WebSocket clients.
        keepalive_interval: Seconds between WebSocket keepalive pings.
            Set to a very large value (e.g. 9999) to disable during tests.
    """

    host: str = "0.0.0.0"
    port: int = 8080
    static_dir: pathlib.Path = field(default_factory=lambda: _DEFAULT_STATIC_DIR)
    radio_model: str = _RADIO_MODEL
    max_clients: int = 100
    keepalive_interval: float = WS_KEEPALIVE_INTERVAL
    dx_cluster_host: str = ""
    dx_cluster_port: int = 0
    dx_callsign: str = ""
    auth_token: str = ""  # empty = no auth required
    tls_cert: str = ""  # path to cert PEM (empty = auto self-signed)
    tls_key: str = ""  # path to key PEM (empty = auto self-signed)
    tls: bool = False  # enable TLS (HTTPS with auto self-signed cert)
    discovery: bool = True  # enable UDP discovery responder
    discovery_port: int = 8470  # UDP port for discovery
    read_only: bool = False  # reject PTT and other transmit commands
    emit_startup_event: bool = False  # emit JSON runtime startup event to stdout


class ConnectionManager:
    """Track WebSocket connections per-IP per-channel; evict excess and reap zombies."""

    MAX_PER_IP_PER_CHANNEL: int = 9  # 3 tabs × 3 WS (control + scope + audio-scope)

    def __init__(self) -> None:
        self._connections: dict[tuple[str, str], list[WebSocketConnection]] = {}

    def register(
        self, ip: str, channel: str, ws: WebSocketConnection
    ) -> list[WebSocketConnection]:
        """Register ws for (ip, channel) and return any evicted excess connections."""
        key = (ip, channel)
        conns = self._connections.setdefault(key, [])
        conns.append(ws)
        evicted: list[WebSocketConnection] = []
        while len(conns) > self.MAX_PER_IP_PER_CHANNEL:
            evicted.append(conns.pop(0))
        # Debug: log current connection count per channel
        if evicted:
            logger.debug(
                "conn_manager: %s from %s now has %d connections (evicted %d)",
                channel,
                ip,
                len(conns),
                len(evicted),
            )
        return evicted

    def unregister(self, ip: str, channel: str, ws: WebSocketConnection) -> None:
        """Remove ws from (ip, channel) tracking."""
        key = (ip, channel)
        conns = self._connections.get(key, [])
        try:
            conns.remove(ws)
        except ValueError:
            pass
        if not conns:
            self._connections.pop(key, None)

    def reap_dead(self) -> list[WebSocketConnection]:
        """Remove and return all tracked connections where ws.is_alive() == False."""
        dead: list[WebSocketConnection] = []
        for key in list(self._connections):
            conns = self._connections[key]
            alive = [ws for ws in conns if ws.is_alive()]
            dead_here = [ws for ws in conns if not ws.is_alive()]
            dead.extend(dead_here)
            if alive:
                self._connections[key] = alive
            else:
                self._connections.pop(key, None)
        return dead


class WebServer:
    """Asyncio HTTP + WebSocket server for the rigplane Web UI.

    Args:
        radio: Connected Radio protocol instance (optional; needed for live data).
        config: Server configuration (defaults to WebConfig()).
    """

    def __init__(
        self,
        radio: "Radio | None" = None,
        config: WebConfig | None = None,
    ) -> None:
        self._radio = radio
        self._config = config or WebConfig()
        self._server: asyncio.Server | None = None
        self._runtime_started_at = time.monotonic()
        self._runtime_log_path: str | None = None
        self._runtime_rigctld_addr: str | None = None
        self._runtime_last_error: str | None = None
        self._client_tasks: set[asyncio.Task[None]] = set()
        self._scope_handlers: set["ScopeHandler"] = set()
        self._audio_scope_handlers: set["ScopeHandler"] = set()
        self._scope_enabled = False
        self._scope_enable_lock: asyncio.Lock = asyncio.Lock()
        self._scope_disable_grace: float = 2.0
        raw_radio_state = (
            getattr(radio, "radio_state", None) if radio is not None else None
        )
        self._radio_state: RadioState = (
            raw_radio_state if isinstance(raw_radio_state, RadioState) else RadioState()
        )
        self._audio_broadcaster = AudioBroadcaster(radio)
        # Audio FFT scope: available when radio has audio capability.
        # For non-hardware-scope radios, also feeds /api/v1/scope (legacy).
        # For hardware-scope radios, audio FFT is ONLY on /api/v1/audio-scope.
        # PCM tap is lazy — enabled only when audio-scope clients connect.
        self._audio_fft_scope: AudioFftScope | None = None
        _has_audio = (CAP_AUDIO in radio.capabilities) if radio is not None else False
        _has_scope = (CAP_SCOPE in radio.capabilities) if radio is not None else False
        if radio is not None and _has_audio:
            self._audio_fft_scope = AudioFftScope(fft_size=2048, fps=20, avg_count=2)
            self._audio_fft_scope.on_frame(self._broadcast_audio_scope)
            if not _has_scope:
                # No hardware scope — audio FFT also feeds /api/v1/scope
                self._audio_fft_scope.on_frame(self._broadcast_scope)
                self._audio_broadcaster.set_pcm_tap(self._audio_fft_scope.feed_audio)
            logger.info(
                "Audio FFT scope available (has_audio=%s, has_hw_scope=%s)",
                _has_audio,
                _has_scope,
            )
        # Audio analyzer: lightweight SNR estimator, tapped from PCM stream.
        self._audio_analyzer: AudioAnalyzer | None = None
        if radio is not None and _has_audio:
            self._audio_analyzer = AudioAnalyzer()
            self._audio_analyzer_tap = self._audio_broadcaster._tap_registry.register(
                "audio-analyzer", self._audio_analyzer.feed_audio
            )
        self._command_queue: CommandQueue = CommandQueue()
        self._radio_poller: RadioPoller | None = None
        self._state_poller: Any | None = None  # StatePoller (lazy, optional)
        # Control handler event queues
        self._control_event_queues: set[BoundedQueue[dict[str, Any]]] = set()
        # State broadcast throttle
        self._last_state_broadcast: float = 0.0
        # Delta encoder for efficient state broadcasting
        self._delta_encoder: DeltaEncoder = DeltaEncoder(full_state_interval=100)
        self._health_revision: int = 0
        self._health_signature: tuple[object, ...] | None = None
        self._health_since_monotonic: float = time.monotonic()
        # Audio bridge (virtual device integration)
        self._audio_bridge: "AudioBridge | None" = None
        # Scope health monitor
        self._scope_last_nonzero: float = 0.0
        self._scope_health_task: asyncio.Task[None] | None = None
        self._scope_health_interval: float = (
            10.0  # seconds of zero frames before re-enable
        )
        self._scope_reenable_task: asyncio.Task[None] | None = None
        self._scope_reenable_poll_interval: float = 0.5
        self._scope_reenable_timeout: float = 30.0
        # prevent GC of fire-and-forget tasks
        self._bg_tasks: set[asyncio.Task[Any]] = set()
        self._scope_health_max_retries: int = 3  # give up after N failed re-enables
        # Band plan registry
        from .band_plan import BandPlanRegistry  # noqa: TID251

        self._band_plan = BandPlanRegistry()
        # EiBi broadcast database
        from .eibi import EiBiProvider  # noqa: TID251

        self._eibi = EiBiProvider()
        # DX cluster
        self._spot_buffer: SpotBuffer = SpotBuffer()
        self._dx_client: DXClusterClient | None = None
        self._dx_client_task: asyncio.Task[None] | None = None
        # Connection manager and zombie reaper
        self._conn_manager: ConnectionManager = ConnectionManager()
        self._zombie_reaper_task: asyncio.Task[None] | None = None
        # UDP discovery responder
        self._discovery: DiscoveryResponder | None = None
        # Diagnostic upload session manager (preview/send/save/delete).
        # Sweeper task is started lazily on the first preview request and
        # explicitly stopped during web shutdown.
        self._diagnostics: DiagnosticsHandler = DiagnosticsHandler()

    def __del__(self) -> None:
        """Emit WARN if instance is collected while server is still running (forgotten teardown)."""
        try:
            # Cancel zombie reaper to avoid RuntimeWarning on pending coroutine
            task = getattr(self, "_zombie_reaper_task", None)
            if task is not None:
                task.cancel()
            still_running = getattr(self, "_server", None) is not None or getattr(
                self, "_server_was_running", False
            )
            if still_running:
                logger.warning(
                    "WebServer collected while still running; "
                    "ensure stop() or async context manager is used."
                )
        except Exception:
            pass  # avoid raising in destructor

    def _spawn(self, coro: Coroutine[Any, Any, Any]) -> asyncio.Task[Any]:
        """Create a background task and prevent GC from collecting it."""
        task = asyncio.get_running_loop().create_task(coro)
        self._bg_tasks.add(task)
        task.add_done_callback(self._bg_tasks.discard)
        return task

    # ------------------------------------------------------------------
    # Helpers for scope callback operations
    # ------------------------------------------------------------------

    def _get_profile(self) -> "RadioProfile":
        """Resolve the RadioProfile for the connected radio."""
        from ..profiles import RadioProfile, resolve_radio_profile

        raw_profile = getattr(self._radio, "profile", None) if self._radio else None
        if isinstance(raw_profile, RadioProfile):
            return raw_profile
        # Try resolving from the radio's own model name
        radio_model = getattr(self._radio, "model", None) if self._radio else None
        if isinstance(radio_model, str):
            try:
                return resolve_radio_profile(model=radio_model)
            except KeyError:
                pass
        # Last resort: config default
        try:
            return resolve_radio_profile(model=self._config.radio_model)
        except KeyError:
            return resolve_radio_profile(model="IC-7610")

    def _radio_ready(self) -> bool:
        """Backend view of radio readiness (CI-V healthy)."""
        return radio_ready(self._radio)

    async def ensure_startup_ready(self, timeout: float = 5.0) -> None:
        """Assert that the attached radio is ready before exposing the server."""
        _ = timeout
        assert_radio_startup_ready(self._radio, component="web startup")

    def _set_scope_data_callback(self, callback: Any) -> None:
        """Set the scope data callback on the radio if it supports it."""
        if self._radio is not None and CAP_SCOPE in self._radio.capabilities:
            self._radio.on_scope_data(callback)  # type: ignore[attr-defined]

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def ensure_scope_enabled(self, handler: "ScopeHandler") -> None:
        """Register a scope handler and enable scope on radio if needed.

        Scope enable goes through the RadioPoller command queue to avoid
        concurrent CI-V access.  For audio FFT scope, no hardware enable
        is needed — frames are generated from the audio stream.
        """
        async with self._scope_enable_lock:
            self._scope_handlers.add(handler)
            if self._radio is not None:
                if not _supports_scope(self._radio):
                    logger.info(
                        "scope: active radio does not expose runtime scope support"
                    )
                    return
                # Always ensure callback is wired for new handlers
                self._set_scope_data_callback(self._broadcast_scope)
                if self._scope_enabled:
                    logger.debug(
                        "scope: already enabled, skipping re-enable (%d handlers)",
                        len(self._scope_handlers),
                    )
                    return
                if self._radio_ready():
                    self._command_queue.put(EnableScope())
                    self._scope_enabled = True
                    logger.info("scope: enable queued")
                else:
                    self._schedule_scope_enable_when_ready(reason="handler_connect")
                    logger.info("scope: defer enable until radio_ready")

    def unregister_scope_handler(self, handler: "ScopeHandler") -> None:
        """Unregister a scope handler."""
        self._scope_handlers.discard(handler)
        if (
            not self._scope_handlers
            and self._radio is not None
            and _supports_scope(self._radio)
        ):
            self._set_scope_data_callback(None)
            if self._scope_enabled:
                self._spawn(self._disable_scope_async())

    async def _disable_scope_async(self) -> None:
        """Disable scope on the radio when no more handlers are connected."""
        if self._radio is None or not _supports_scope(self._radio):
            return
        await asyncio.sleep(self._scope_disable_grace)
        if self._scope_handlers:
            logger.debug("scope: disable task aborted — handler reconnected")
            if self._radio is not None:
                self._set_scope_data_callback(self._broadcast_scope)
            return
        self._command_queue.put(DisableScope())
        if not self._scope_handlers:
            self._scope_enabled = False
            logger.info("scope: disable queued (no active handlers)")
        else:
            logger.debug(
                "scope: disable queued but new handler present — will re-enable"
            )
            if self._radio is not None:
                self._set_scope_data_callback(self._broadcast_scope)

    def _broadcast_scope(self, frame: Any) -> None:
        """Broadcast scope frame to all registered handlers.

        Also extract VFO frequency from scope center mode frames
        and update state cache — bypasses CI-V polling for freq.
        """
        for h in list(self._scope_handlers):
            h.enqueue_frame(frame)
        # Scope health: track whether frames carry real data
        self._scope_health_check(frame)

    def _broadcast_audio_scope(self, frame: Any) -> None:
        """Broadcast audio FFT scope frame to /api/v1/audio-scope handlers only."""
        for h in list(self._audio_scope_handlers):
            h.enqueue_frame(frame)

    async def ensure_audio_scope_enabled(self, handler: "ScopeHandler") -> None:
        """Register an audio scope handler. Lazy PCM tap + relay enable."""
        was_empty = not self._audio_scope_handlers
        self._audio_scope_handlers.add(handler)
        if self._audio_fft_scope is not None:
            self._update_fft_scope_freq()
            self._update_fft_scope_mode()
            if was_empty:
                self._audio_broadcaster.set_pcm_tap(self._audio_fft_scope.feed_audio)
                # Ensure relay is running so PCM tap fires even without audio WS clients
                await self._audio_broadcaster.ensure_relay()
                logger.info("audio-scope: PCM tap + relay enabled (first client)")
        logger.info(
            "audio-scope: handler registered (%d total)",
            len(self._audio_scope_handlers),
        )

    def unregister_audio_scope_handler(self, handler: "ScopeHandler") -> None:
        """Unregister an audio scope handler. Disable PCM tap when last client leaves."""
        self._audio_scope_handlers.discard(handler)
        if not self._audio_scope_handlers and self._audio_fft_scope is not None:
            # Only disable tap for hardware-scope radios (non-hw radios keep tap always on)
            _has_scope = (
                CAP_SCOPE in self._radio.capabilities
                if self._radio is not None
                else False
            )
            if _has_scope:
                self._audio_broadcaster.set_pcm_tap(None)
                logger.info("audio-scope: PCM tap disabled (no clients)")
        logger.info(
            "audio-scope: handler unregistered (%d remaining)",
            len(self._audio_scope_handlers),
        )

    def _update_fft_scope_freq(self) -> None:
        """Sync AudioFftScope center frequency from current radio state."""
        if self._audio_fft_scope is None:
            return
        main = getattr(self._radio_state, "main", None)
        if main is not None:
            freq = getattr(main, "freq", 0)
            if isinstance(freq, int) and freq > 0:
                self._audio_fft_scope.set_center_freq(freq)

    def _update_fft_scope_mode(self) -> None:
        """Sync AudioFftScope bandwidth from current radio mode via rig profile."""
        if self._audio_fft_scope is None:
            return
        main = getattr(self._radio_state, "main", None)
        if main is None:
            return
        mode = getattr(main, "mode", None)
        data_mode = getattr(main, "data_mode", 0)
        profile = self._get_profile()
        rule = profile.resolve_filter_rule(mode, data_mode=data_mode)
        if rule is not None and rule.max_hz is not None:
            self._audio_fft_scope.set_mode_bandwidth(rule.max_hz)
        else:
            self._audio_fft_scope.set_mode_bandwidth(None)

    # ------------------------------------------------------------------
    # RadioPoller integration
    # ------------------------------------------------------------------

    @property
    def command_queue(self) -> CommandQueue:
        """Command queue consumed by RadioPoller."""
        return self._command_queue

    def register_control_event_queue(self, q: BoundedQueue[dict[str, Any]]) -> None:
        """Register a ControlHandler event queue for broadcast."""
        self._control_event_queues.add(q)

    def unregister_control_event_queue(self, q: BoundedQueue[dict[str, Any]]) -> None:
        """Unregister a ControlHandler event queue."""
        self._control_event_queues.discard(q)

    def broadcast_event(self, name: str, data: dict[str, Any]) -> None:
        """Push an event to all ControlHandler event queues."""
        event = {"type": "event", "name": name, "data": data}
        for q in list(self._control_event_queues):
            try:
                q.put_nowait(event)
            except asyncio.QueueFull:
                logger.debug("broadcast_event: queue full, dropping event=%s", name)

    def _broadcast_state_update(self) -> None:
        """Broadcast current state to all control WS clients (throttled).

        Uses delta encoding to reduce payload size for subsequent updates.
        Sends full state on initial connection, then only changed fields.
        """
        import time

        now = time.monotonic()
        if now - self._last_state_broadcast < 0.05:
            return
        self._last_state_broadcast = now

        # Keep audio FFT scope center freq and mode bandwidth in sync
        self._update_fft_scope_freq()
        self._update_fft_scope_mode()

        body = self.build_public_state()

        # Encode state as delta to reduce bandwidth
        delta = self._delta_encoder.encode(body)
        event = {"type": "state_update", "data": delta}

        for q in list(self._control_event_queues):
            try:
                q.put_nowait(event)
            except asyncio.QueueFull:
                logger.warning("control state queue full; dropping state_update")

    def build_public_state(self, *, updated_at: str | None = None) -> dict[str, Any]:
        """Return the canonical public state payload for web consumers."""
        revision = self._radio_poller.revision if self._radio_poller is not None else 0
        health = self._build_radio_health()
        return build_public_state_payload(
            self._radio_state,
            radio=self._radio,
            revision=revision,
            receiver_count=self._get_profile().receiver_count,
            updated_at=updated_at,
            scope_clients=len(self._scope_handlers),
            control_clients=len(self._control_event_queues),
            audio_clients=len(self._audio_broadcaster._clients),
            radio_health=health,
            health_revision=self._health_revision,
        )

    def _build_radio_health(self) -> dict[str, Any]:
        """Build radio health and advance the health revision on transitions."""
        now = time.monotonic()
        health = classify_radio_health(
            self._radio,
            server_reachable=True,
            now_monotonic=now,
        )
        signature = (
            health.get("serverReachable"),
            health.get("radioLink"),
            health.get("readiness"),
            health.get("likelyCause"),
            health.get("lastError"),
        )
        if self._health_signature != signature:
            self._health_signature = signature
            self._health_revision += 1
            self._health_since_monotonic = now
        health["sinceMs"] = int(max(0.0, now - self._health_since_monotonic) * 1000)
        return health

    def broadcast_notification(
        self,
        level: str,
        message: str,
        category: str = "system",
        *,
        code: str | None = None,
        params: dict[str, Any] | None = None,
    ) -> None:
        """Broadcast a notification to all connected WebSocket clients.

        Args:
            level: Severity level — "info", "warning", "error", or "success".
            message: Legacy human-readable notification text. Kept as a stable
                English fallback for out-of-tree consumers that have not yet
                migrated to ``code``-based resolution. The in-repo Svelte
                frontend ignores this when ``code`` is set, and resolves the
                localized string from ``core.toast.<code>`` via
                ``messageFromReasonCode`` (RP-ML-005). New callers should pass
                ``code`` plus an English-stable ``message`` for log/wire
                correlation.
            category: Logical category — "connection", "dx_cluster", "bridge",
                "system".
            code: Optional stable reason code (lowerCamelCase ASCII) the
                frontend resolves into a localized message. When omitted, the
                ``message`` field is the only display source (legacy path).
            params: Optional named-placeholder substitutions for the localized
                message. Values must already be canonical English (frequencies,
                radio names, protocol values); the i18n layer never reformats
                interpolated values (strategy glossary §4.3).

        Wire schema (additive, backward compatible):
            ``{type: "notification", level, message, category[, code, params]}``

        Existing clients that only read ``message`` continue to work unchanged.
        """
        notification: dict[str, Any] = {
            "type": "notification",
            "level": level,
            "message": message,
            "category": category,
        }
        if code is not None:
            notification["code"] = code
        if params:
            notification["params"] = dict(params)
        for q in list(self._control_event_queues):
            try:
                q.put_nowait(notification)
            except asyncio.QueueFull:
                logger.debug(
                    "broadcast_notification: queue full, dropping notification"
                )

    def _broadcast_dx_spot(self, spot: Any) -> None:
        """Add DX spot to buffer and push dx_spot message to all control clients."""
        self._spot_buffer.add(spot)
        msg = {
            "type": "dx_spot",
            "spot": {
                "spotter": spot.spotter,
                "freq": spot.freq,
                "call": spot.call,
                "comment": spot.comment,
                "time_utc": spot.time_utc,
                "timestamp": spot.timestamp,
            },
        }
        for q in list(self._control_event_queues):
            try:
                q.put_nowait(msg)
            except asyncio.QueueFull:
                pass

    # ------------------------------------------------------------------
    # Meter handler registration (no poller — RadioPoller broadcasts)
    # ------------------------------------------------------------------

    def _on_radio_state_change(self, name: str, data: dict[str, Any]) -> None:
        """Callback from CI-V RX stream (_update_state_cache_from_frame).

        This is the PRIMARY update path.  Called whenever the radio sends
        a CI-V frame (solicited response or unsolicited change).
        """
        if self._radio_poller is not None:
            self._radio_poller.bump_revision()
        self.broadcast_event(name, data)
        self._broadcast_state_update()
        if name == "connection_state":
            if data.get("connected"):
                self.broadcast_notification(
                    "success",
                    "Radio connected",
                    "connection",
                    code="radioConnected",
                )
            else:
                self.broadcast_notification(
                    "warning",
                    "Radio disconnected",
                    "connection",
                    code="radioDisconnected",
                )

    def _on_radio_reconnect(self) -> None:
        """Called after soft_reconnect — refetch state and re-enable scope."""
        # Clear poller readiness so scope waits for refetch to complete
        if self._radio_poller is not None:
            self._radio_poller._initial_fetch_done.clear()

        async def _refetch_and_reenable() -> None:
            """Refetch state, signal readiness, then re-enable scope."""
            try:
                if self._radio is not None and hasattr(
                    self._radio, "_fetch_initial_state"
                ):
                    await self._radio._fetch_initial_state()
            except Exception:
                logger.warning("reconnect: refetch failed", exc_info=True)
            finally:
                if self._radio_poller is not None:
                    self._radio_poller._initial_fetch_done.set()
            # Re-enable scope after refetch completes.
            # Do NOT gate on self._radio_ready(): that property waits for
            # CI-V broadcast data, but on IC-7610 in the "deaf" firmware
            # state broadcast may only resume once a scope-enable command
            # is sent — creating a deadlock where scope re-enable waits
            # for the very signal it would itself trigger.  The session
            # is already up at this point (soft_reconnect completed auth +
            # discovery), so queue EnableScope unconditionally; if the
            # radio is genuinely unreachable the command will fail on its
            # own, which is strictly better than a silent 30-second wait
            # every reconnect cycle.
            if (
                self._scope_handlers
                and self._radio is not None
                and _supports_scope(self._radio)
            ):
                self._set_scope_data_callback(self._broadcast_scope)
                self._command_queue.put(EnableScope())
                self._scope_enabled = True
                logger.info(
                    "scope: re-enable queued after reconnect (%d handlers)",
                    len(self._scope_handlers),
                )

        self._spawn(_refetch_and_reenable())

    def _schedule_scope_enable_when_ready(self, *, reason: str) -> None:
        """Schedule delayed scope enable once radio becomes ready."""
        if (
            self._scope_reenable_task is not None
            and not self._scope_reenable_task.done()
        ):
            return
        loop = asyncio.get_running_loop()
        self._scope_reenable_task = loop.create_task(
            self._wait_and_enable_scope(reason=reason),
            name="scope-reenable-when-ready",
        )

    async def _wait_and_enable_scope(self, *, reason: str) -> None:
        """Wait until radio_ready before queuing EnableScope."""
        import time

        deadline = time.monotonic() + self._scope_reenable_timeout
        try:
            while True:
                if not self._scope_handlers or self._radio is None:
                    return
                if not _supports_scope(self._radio):
                    return
                if self._radio_ready():
                    self._set_scope_data_callback(self._broadcast_scope)
                    self._command_queue.put(EnableScope())
                    self._scope_enabled = True
                    logger.info(
                        "scope: enable queued after %s (%d handlers)",
                        reason,
                        len(self._scope_handlers),
                    )
                    return
                if time.monotonic() >= deadline:
                    logger.warning(
                        "scope: radio not ready after %.0fs (%s), skipping re-enable",
                        self._scope_reenable_timeout,
                        reason,
                    )
                    return
                await asyncio.sleep(self._scope_reenable_poll_interval)
        except asyncio.CancelledError:
            pass
        finally:
            self._scope_reenable_task = None

    def _scope_health_check(self, frame: Any) -> None:
        """Track whether scope frames contain real data (non-zero pixels)."""
        import time

        try:
            # ScopeFrame has .pixels (bytes-like)
            pixels = getattr(frame, "pixels", None) or b""
            if any(b != 0 for b in pixels):
                self._scope_last_nonzero = time.monotonic()
        except (AttributeError, TypeError):
            logger.debug("scope health check: unexpected frame type", exc_info=True)

    async def _scope_health_monitor(self) -> None:
        """Background task: re-enable scope if frames are all-zero for too long.

        For serial-only radios (has_lan=False), scope data delivery differs
        significantly — the radio may need CI-V output enabled, higher baud
        rates, etc.  Limit re-enable attempts to avoid flooding the serial
        link and logs.
        """
        import time

        max_retries = self._scope_health_max_retries
        retries = 0
        try:
            while True:
                await asyncio.sleep(self._scope_health_interval)
                if not self._scope_handlers or self._radio is None:
                    continue
                if not _supports_scope(self._radio):
                    continue
                # Don't re-enable scope while radio is disconnected
                if not self._radio_ready():
                    self._scope_last_nonzero = time.monotonic()  # reset timer
                    retries = 0
                    continue
                now = time.monotonic()
                if self._scope_last_nonzero == 0.0:
                    # Never seen non-zero — might be starting up
                    self._scope_last_nonzero = now
                    continue
                elapsed = now - self._scope_last_nonzero
                if elapsed > self._scope_health_interval:
                    if retries >= max_retries:
                        if retries == max_retries:
                            logger.warning(
                                "scope-health: giving up after %d retries "
                                "(scope may not be available on this backend)",
                                max_retries,
                            )
                            retries += 1  # log once
                        continue
                    self._command_queue.put(EnableScope())
                    self._scope_last_nonzero = now  # reset to avoid spam
                    retries += 1
                    logger.warning(
                        "scope-health: all-zero frames for %.0fs, re-enabling scope "
                        "(attempt %d/%d)",
                        elapsed,
                        retries,
                        max_retries,
                    )
                else:
                    # Scope is healthy — reset retry counter
                    retries = 0
        except asyncio.CancelledError:
            pass

    async def _zombie_reaper(self, interval: float = 30.0) -> None:
        """Periodically reap dead WebSocket connections from scope/audio handlers."""
        try:
            while True:
                await asyncio.sleep(interval)
                dead_ws = self._conn_manager.reap_dead()

                # Reap dead scope handlers
                dead_scope = [
                    h for h in list(self._scope_handlers) if not h._ws.is_alive()
                ]
                before = len(self._scope_handlers)
                for h in dead_scope:
                    self.unregister_scope_handler(h)
                after = len(self._scope_handlers)
                if dead_scope:
                    logger.info(
                        "zombie-reaper: reaped %d scope handlers (%d→%d active)",
                        len(dead_scope),
                        before,
                        after,
                    )

                # Reap dead audio clients
                reaped_audio = await self._audio_broadcaster.reap_dead_clients()
                if reaped_audio:
                    logger.info(
                        "zombie-reaper: reaped %d dead audio clients", reaped_audio
                    )

                if dead_ws or dead_scope or reaped_audio:
                    logger.info(
                        "zombie-reaper: found %d dead ws, %d dead scope, %d dead audio",
                        len(dead_ws),
                        len(dead_scope),
                        reaped_audio,
                    )
                else:
                    logger.debug("zombie-reaper: no dead connections found")
        except asyncio.CancelledError:
            pass

    def _on_poller_state_event(self, name: str, data: dict[str, Any]) -> None:
        """Callback from RadioPoller — forward event and push fresh state."""
        self.broadcast_event(name, data)
        self._broadcast_state_update()

    async def start(self) -> None:
        """Start the HTTP/WS listener and RadioPoller (if radio is connected)."""
        from .web_startup import start_web_server  # noqa: TID251

        await start_web_server(self)

    # ------------------------------------------------------------------
    # Audio Bridge (virtual device integration)
    # ------------------------------------------------------------------

    async def start_audio_bridge(
        self,
        device_name: str | None = None,
        tx_device_name: str | None = None,
        tx_enabled: bool = True,
        label: str | None = None,
        max_retries: int = 5,
        retry_base_delay: float = 1.0,
    ) -> None:
        """Start the audio bridge to a virtual audio device.

        Args:
            device_name: Device name for RX (e.g. "BlackHole 2ch"). Auto-detects if None.
            tx_device_name: Separate device for TX (e.g. "BlackHole 16ch").
                            Required for bidirectional audio to avoid feedback.
            tx_enabled: Whether to bridge TX (device → radio).
            label: Descriptive label for log messages. If ``None``, derived from radio model.
        """
        from ..audio_bridge import AudioBridge, derive_bridge_label

        if self._audio_bridge is not None and self._audio_bridge.running:
            logger.warning("audio-bridge: already running")
            return
        if self._radio is None:
            raise RuntimeError("No radio connected")
        if not _supports_audio(self._radio):
            raise RuntimeError(
                "Audio bridge is unavailable: active radio does not support audio streaming."
            )

        label = derive_bridge_label(self._radio, label)

        self._audio_bridge = AudioBridge(
            self._radio,  # type: ignore[arg-type]
            device_name=device_name,
            tx_device_name=tx_device_name,
            tx_enabled=tx_enabled,
            label=label,
            max_retries=max_retries,
            retry_base_delay=retry_base_delay,
        )
        await self._audio_bridge.start()
        self.broadcast_notification(
            "success",
            "Audio bridge started",
            "bridge",
            code="audioBridgeStarted",
        )

    async def stop_audio_bridge(self) -> None:
        """Stop the audio bridge."""
        if self._audio_bridge is not None:
            await self._audio_bridge.stop()
            self._audio_bridge = None
            self.broadcast_notification(
                "info",
                "Audio bridge stopped",
                "bridge",
                code="audioBridgeStopped",
            )

    @property
    def audio_bridge_stats(self) -> dict[str, Any] | None:
        """Audio bridge stats, or None if not running."""
        if self._audio_bridge is not None:
            stats = self._audio_bridge.stats
            return stats if isinstance(stats, dict) else None
        return None

    async def stop(self) -> None:
        """Close the listener, stop RadioPoller, disconnect radio, cancel tasks."""
        from .web_startup import stop_web_server  # noqa: TID251

        await stop_web_server(self)

    async def serve_forever(self) -> None:
        """Start and block until cancelled.  Handles SIGTERM/SIGINT gracefully."""
        await self.start()
        assert self._server is not None
        if self._config.emit_startup_event:
            self.emit_startup_event()

        loop = asyncio.get_running_loop()
        stop_event = asyncio.Event()
        _signal_count = 0

        def _on_signal() -> None:
            nonlocal _signal_count
            _signal_count += 1
            if _signal_count == 1:
                logger.info("received shutdown signal")
                stop_event.set()
            elif _signal_count == 2:
                logger.info("second signal — cancelling all tasks")
                for task in asyncio.all_tasks(loop):
                    task.cancel()
            else:
                logger.info("forced exit")
                import os

                os._exit(1)

        _install_shutdown_signal_handlers(loop, _on_signal)

        try:
            await stop_event.wait()
        finally:
            # Shield stop() from CancelledError so server.close() always runs
            try:
                await asyncio.shield(self.stop())
            except (asyncio.CancelledError, Exception):
                # Last resort: close TCP listener directly
                if self._server is not None:
                    self._server.close()
                    self._server = None

    async def __aenter__(self) -> WebServer:
        await self.start()
        return self

    async def __aexit__(self, *args: object) -> None:
        await self.stop()

    @property
    def port(self) -> int:
        """Actual bound port (useful when config.port == 0)."""
        if self._server is None:
            return self._config.port
        return int(self._server.sockets[0].getsockname()[1])

    # ------------------------------------------------------------------
    # Connection acceptance
    # ------------------------------------------------------------------

    def _accept_client(
        self,
        reader: asyncio.StreamReader,
        writer: asyncio.StreamWriter,
    ) -> None:
        if len(self._client_tasks) >= self._config.max_clients:
            logger.warning(
                "max_clients reached (%d), rejecting connection",
                self._config.max_clients,
            )
            writer.close()
            return
        loop = asyncio.get_running_loop()
        task = loop.create_task(self._handle_connection(reader, writer))
        self._client_tasks.add(task)
        task.add_done_callback(self._client_tasks.discard)

    # ------------------------------------------------------------------
    # HTTP request parsing
    # ------------------------------------------------------------------

    async def _read_request(
        self, reader: asyncio.StreamReader
    ) -> tuple[str, str, dict[str, str], dict[str, list[str]]] | None:
        """Read and parse an HTTP request line + headers.

        Returns:
            Tuple of (method, path, headers_dict, query_params) or None on EOF/error.
        """
        try:
            request_line = await asyncio.wait_for(reader.readline(), timeout=10.0)
        except asyncio.TimeoutError:
            return None
        if not request_line:
            return None

        parts = request_line.decode("ascii", errors="replace").strip().split(" ", 2)
        if len(parts) < 2:
            return None
        method, raw_path = parts[0], parts[1]

        # Decode path (preserve query string separately)
        parsed = urllib.parse.urlparse(raw_path)
        path = urllib.parse.unquote(parsed.path)
        query = urllib.parse.parse_qs(parsed.query)

        headers: dict[str, str] = {}
        while True:
            try:
                line = await asyncio.wait_for(reader.readline(), timeout=5.0)
            except asyncio.TimeoutError:
                break
            stripped = line.strip()
            if not stripped:
                break
            if b":" in stripped:
                key, _, value = stripped.partition(b":")
                headers[key.decode("ascii", errors="replace").strip().lower()] = (
                    value.decode("ascii", errors="replace").strip()
                )

        return method, path, headers, query

    # ------------------------------------------------------------------
    # Main connection handler
    # ------------------------------------------------------------------

    async def _handle_connection(
        self,
        reader: asyncio.StreamReader,
        writer: asyncio.StreamWriter,
    ) -> None:
        peer = writer.get_extra_info("peername", ("?", 0))
        try:
            result = await self._read_request(reader)
            if result is None:
                return
            method, path, headers, query = result

            logger.debug(
                "request: %s %s from %s:%s",
                method,
                _redact_token_in_path(path),
                peer[0],
                peer[1],
            )

            # WebSocket upgrade?
            if (
                headers.get("upgrade", "").lower() == "websocket"
                and headers.get("connection", "").lower().find("upgrade") >= 0
            ):
                await self._handle_websocket(reader, writer, path, headers, query)
            else:
                await self._handle_http(writer, method, path, headers, reader, query)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.debug("connection error from %s:%s: %s", peer[0], peer[1], exc)
        finally:
            try:
                writer.close()
                await writer.wait_closed()
            except OSError:
                pass

    # ------------------------------------------------------------------
    # HTTP handlers
    # ------------------------------------------------------------------

    async def _handle_http(
        self,
        writer: asyncio.StreamWriter,
        method: str,
        path: str,
        headers: dict[str, str] | None = None,
        reader: asyncio.StreamReader | None = None,
        query: dict[str, list[str]] | None = None,
    ) -> None:
        from .web_routing import dispatch_http_request  # noqa: TID251

        await dispatch_http_request(self, writer, method, path, headers, reader, query)

    async def _serve_info(
        self, writer: asyncio.StreamWriter, headers: dict[str, str] | None = None
    ) -> None:
        raw_model = (
            getattr(self._radio, "model", None) if self._radio is not None else None
        )
        model = raw_model if isinstance(raw_model, str) else self._config.radio_model
        caps = _runtime_capabilities(self._radio)
        has_dual_rx = "dual_rx" in caps
        profile = self._get_profile()
        raw_connected = (
            getattr(self._radio, "connected", False) if self._radio else False
        )
        connected = raw_connected if isinstance(raw_connected, bool) else False
        raw_control_connected = (
            getattr(self._radio, "control_connected", False) if self._radio else False
        )
        control_connected = (
            raw_control_connected if isinstance(raw_control_connected, bool) else False
        )
        body = json.dumps(
            {
                # Backward-compatible legacy fields
                "server": "rigplane",
                "version": __version__,
                "proto": 1,
                "radio": model,
                # New structured fields
                "model": model,
                "capabilities": {
                    "hasSpectrum": "scope" in caps,
                    "hasAudio": "audio" in caps,
                    "hasTx": "tx" in caps,
                    "hasDualReceiver": has_dual_rx,
                    "hasTuner": "tuner" in caps,
                    "hasCw": "cw" in caps,
                    "hasWebrtc": webrtc_available() and "audio" in caps,
                    "maxReceivers": (
                        profile.receiver_count
                        if self._radio is not None
                        else (2 if has_dual_rx else 1)
                    ),
                    "tags": sorted(caps),
                    "modes": list(profile.modes),
                    "filters": list(profile.filters),
                    "filterWidthMin": profile.filter_width_min,
                    "filterWidthMax": profile.filter_width_max,
                    "filterConfig": _serialize_filter_config(profile),
                    "vfoScheme": profile.vfo_scheme,
                    "hasLan": profile.has_lan,
                    "attValues": (
                        list(profile.att_values) if profile.att_values else None
                    ),
                    "attLabels": profile.att_labels,
                    "preValues": (
                        list(profile.pre_values) if profile.pre_values else None
                    ),
                    "preLabels": profile.pre_labels,
                    "agcModes": list(profile.agc_modes) if profile.agc_modes else None,
                    "agcLabels": profile.agc_labels,
                    "antennas": profile.antenna_tx_count,
                    "dataModeCount": profile.data_mode_count,
                    "dataModeLabels": profile.data_mode_labels,
                    "keyboard": _serialize_keyboard_config(profile),
                    **({"controls": profile.controls} if profile.controls else {}),
                    "txBands": [
                        {"name": b.name, "start": b.start, "end": b.end}
                        for fr in profile.freq_ranges
                        for b in fr.bands
                    ]
                    or None,
                },
                "connection": {
                    "rigConnected": connected,
                    "radioReady": self._radio_ready(),
                    "controlConnected": control_connected,
                    "wsClients": len(self._client_tasks),
                },
            },
            separators=(",", ":"),
        ).encode()
        await _send_json(writer, body, headers)

    async def _serve_health(
        self, writer: asyncio.StreamWriter, headers: dict[str, str] | None = None
    ) -> None:
        body = json.dumps(
            {
                "status": "ok",
                "pid": os.getpid(),
                "version": __version__,
            },
            separators=(",", ":"),
        ).encode()
        await _send_json(writer, body, headers)

    async def _serve_ready(
        self, writer: asyncio.StreamWriter, headers: dict[str, str] | None = None
    ) -> None:
        ready = self._radio_ready()
        body = json.dumps(
            {
                "status": "ready" if ready else "not_ready",
                "radioReady": ready,
            },
            separators=(",", ":"),
        ).encode()
        if ready:
            await _send_json(writer, body, headers)
        else:
            await _send_response(
                writer,
                503,
                "Service Unavailable",
                body,
                {"Content-Type": "application/json"},
            )

    def _runtime_bind_payload(self) -> dict[str, Any]:
        if self._server is not None and self._server.sockets:
            host, port = self._server.sockets[0].getsockname()[:2]
            return {"host": str(host), "port": int(port)}
        return {"host": self._config.host, "port": int(self._config.port)}

    def _runtime_base_url(self) -> str:
        bind = self._runtime_bind_payload()
        scheme = "https" if self._config.tls else "http"
        return f"{scheme}://{bind['host']}:{bind['port']}"

    def _radio_runtime_payload(self) -> dict[str, Any]:
        radio = self._radio
        raw_connected = (
            getattr(radio, "connected", False) if radio is not None else False
        )
        connected = raw_connected if isinstance(raw_connected, bool) else False
        raw_control_connected = (
            getattr(radio, "control_connected", False) if radio is not None else False
        )
        control_connected = (
            raw_control_connected if isinstance(raw_control_connected, bool) else False
        )
        return {
            "model": getattr(radio, "model", self._config.radio_model)
            if radio is not None
            else self._config.radio_model,
            "connected": connected,
            "controlConnected": control_connected,
            "radioReady": self._radio_ready(),
        }

    def _station_readiness_payload(self) -> dict[str, Any]:
        radio = self._radio
        backend = getattr(radio, "backend_id", None) if radio is not None else None
        radio_payload = self._radio_runtime_payload()
        health = self._build_radio_health()
        if radio_payload["radioReady"]:
            readiness = "ready_with_radio"
            message = "Station server is ready with an attached radio."
        elif radio is None:
            readiness = "requires_configuration_or_auth"
            message = "Start the station server with a configured radio target."
        elif backend in {"icom_serial", "yaesu_cat"}:
            readiness = "no_usb_radio_connected"
            message = "Connect the radio by USB and confirm serial permissions."
        elif backend == "rigplane":
            likely = health.get("likelyCause")
            readiness = (
                "radio_powered_off_or_unreachable"
                if likely in {"radio_powered_off_likely", "radio_not_responding"}
                else "lan_radio_unsupported_or_not_found"
            )
            message = "Power on the radio and confirm it is reachable on the LAN."
        else:
            readiness = "requires_configuration_or_auth"
            message = "Configure a supported radio target before using this station."
        return {
            "readiness": readiness,
            "radioAvailable": bool(radio_payload["radioReady"]),
            "backend": backend,
            "health": health,
            "authRequired": bool(self._config.auth_token),
            "message": message,
        }

    def _station_status_payload(self) -> dict[str, Any]:
        base_url = self._runtime_base_url()
        radio_payload = self._radio_runtime_payload()
        display_name = (
            radio_payload["model"]
            if radio_payload["model"]
            else self._config.radio_model
        )
        return {
            "schema": "rigplane.station.status.v1",
            "service": "rigplane",
            "kind": "station_server",
            "version": __version__,
            "displayName": display_name,
            "instanceId": None,
            "baseUrl": base_url,
            "healthUrl": f"{base_url}/healthz",
            "readinessUrl": f"{base_url}/readyz",
            "runtimeUrl": f"{base_url}/api/v1/runtime",
            "stationUrl": f"{base_url}/api/v1/station",
            "station": self._station_readiness_payload(),
            "radio": radio_payload,
        }

    def startup_event_payload(self) -> dict[str, Any]:
        base_url = self._runtime_base_url()
        return {
            "type": "rigplane.runtime.started",
            "pid": os.getpid(),
            "baseUrl": base_url,
            "healthUrl": f"{base_url}/healthz",
            "runtimeUrl": f"{base_url}/api/v1/runtime",
            "logPath": self._runtime_log_path,
        }

    def emit_startup_event(self, stream: TextIO | None = None) -> None:
        target = stream if stream is not None else sys.stdout
        print(
            json.dumps(self.startup_event_payload(), separators=(",", ":")),
            file=target,
            flush=True,
        )

    def _runtime_bridge_payload(self) -> dict[str, Any]:
        bridge = self._audio_bridge
        if bridge is None:
            return {"enabled": False, "running": False}
        stats = getattr(bridge, "stats", None)
        if callable(stats):
            stats = stats()
        if not isinstance(stats, dict):
            stats = {}
        return {
            "enabled": True,
            "running": bool(getattr(bridge, "running", False)),
            "stats": stats,
        }

    async def _serve_runtime(
        self, writer: asyncio.StreamWriter, headers: dict[str, str] | None = None
    ) -> None:
        radio = self._radio
        body = json.dumps(
            {
                "pid": os.getpid(),
                "uptimeSeconds": round(time.monotonic() - self._runtime_started_at, 1),
                "version": __version__,
                "bind": self._runtime_bind_payload(),
                "logPath": self._runtime_log_path,
                "authRequired": bool(self._config.auth_token),
                "backend": getattr(radio, "backend_id", None)
                if radio is not None
                else None,
                "radio": self._radio_runtime_payload(),
                "station": self._station_readiness_payload(),
                "rigctld": {
                    "enabled": self._runtime_rigctld_addr is not None,
                    "address": self._runtime_rigctld_addr,
                },
                "bridge": self._runtime_bridge_payload(),
                "lastError": self._runtime_last_error,
            },
            separators=(",", ":"),
        ).encode()
        await _send_json(writer, body, headers)

    async def _serve_station_status(
        self, writer: asyncio.StreamWriter, headers: dict[str, str] | None = None
    ) -> None:
        body = json.dumps(
            self._station_status_payload(),
            separators=(",", ":"),
        ).encode()
        await _send_json(writer, body, headers)

    async def _serve_state(
        self, writer: asyncio.StreamWriter, headers: dict[str, str] | None = None
    ) -> None:
        body_dict = self.build_public_state()
        revision = int(body_dict.get("revision", 0))
        health_revision = int(body_dict.get("healthRevision", 0))
        body = json.dumps(body_dict, separators=(",", ":")).encode()
        await _send_json(writer, body, headers, etag=f'"{revision}-{health_revision}"')

    async def _serve_audio_analysis(
        self, writer: asyncio.StreamWriter, headers: dict[str, str] | None = None
    ) -> None:
        """GET /api/v1/audio/analysis -- return current audio analysis snapshot."""
        if self._audio_analyzer is None:
            body = json.dumps(
                {"error": "unavailable", "message": "Audio analyzer not active"},
                separators=(",", ":"),
            ).encode()
            await _send_response(
                writer, 404, "Not Found", body, {"Content-Type": "application/json"}
            )
            return
        body = json.dumps(
            self._audio_analyzer.to_dict(), separators=(",", ":")
        ).encode()
        await _send_json(writer, body, headers)

    def _get_meter_cal_payload(self) -> dict[str, Any]:
        """Extract meter calibration from radio backend or profile."""
        result: dict[str, Any] = {}
        # Try radio._config (Yaesu CAT backend)
        # Guard: getattr on MagicMock returns MagicMock, not None — use isinstance checks
        radio_config = getattr(self._radio, "_config", None) if self._radio else None
        if radio_config is not None:
            mc = getattr(radio_config, "meter_calibrations", None)
            mr = getattr(radio_config, "meter_redlines", None)
            if isinstance(mc, dict):
                result["meterCalibrations"] = mc
            if isinstance(mr, dict):
                result["meterRedlines"] = mr
            if result:
                return result
        # Fallback to profile
        profile = self._get_profile()
        mc = getattr(profile, "meter_calibrations", None)
        if mc:
            result["meterCalibrations"] = mc
        mr = getattr(profile, "meter_redlines", None)
        if mr:
            result["meterRedlines"] = mr
        return result

    async def _serve_capabilities(
        self, writer: asyncio.StreamWriter, headers: dict[str, str] | None = None
    ) -> None:
        caps = _runtime_capabilities(self._radio)
        _raw_model = (
            getattr(self._radio, "model", None) if self._radio is not None else None
        )
        model: str = (
            _raw_model if isinstance(_raw_model, str) else self._config.radio_model
        )
        profile = self._get_profile()

        freq_ranges = [
            {
                "start": r.start,
                "end": r.end,
                "label": r.label,
                "bands": [
                    {
                        "name": b.name,
                        "start": b.start,
                        "end": b.end,
                        "default": b.default,
                        **({"bsrCode": b.bsr_code} if b.bsr_code is not None else {}),
                    }
                    for b in r.bands
                ],
            }
            for r in profile.freq_ranges
        ]

        body = json.dumps(
            {
                "model": model,
                "scope": "scope" in caps,
                "audio": "audio" in caps,
                "tx": "tx" in caps,
                "capabilities": sorted(caps),
                "receivers": profile.receiver_count,
                "vfoScheme": profile.vfo_scheme,
                "freqRanges": freq_ranges,
                "modes": list(profile.modes),
                "filters": list(profile.filters),
                "filterWidthMin": profile.filter_width_min,
                "filterWidthMax": profile.filter_width_max,
                "filterConfig": _serialize_filter_config(profile),
                "attValues": list(profile.att_values) if profile.att_values else [0],
                "attLabels": profile.att_labels if profile.att_labels else {},
                "preValues": list(profile.pre_values) if profile.pre_values else [0],
                "preLabels": profile.pre_labels if profile.pre_labels else {},
                "agcModes": list(profile.agc_modes) if profile.agc_modes else [],
                "agcLabels": profile.agc_labels if profile.agc_labels else {},
                "dataModeCount": profile.data_mode_count,
                "dataModeLabels": (
                    profile.data_mode_labels if profile.data_mode_labels else {}
                ),
                "keyboard": _serialize_keyboard_config(profile),
                "scopeSource": (
                    "hardware"
                    if "scope" in caps
                    else ("audio_fft" if self._audio_fft_scope is not None else None)
                ),
                "audioFftAvailable": self._audio_fft_scope is not None,
                "scopeConfig": {
                    "centerMode": True,
                    "amplitudeMax": 160,
                    "defaultSpan": (
                        (self._audio_fft_scope.bandwidth_hz or 48000)
                        if self._audio_fft_scope is not None
                        else 500000
                    ),
                },
                "audioConfig": {
                    "sampleRate": 48000,
                    "channels": 1,
                    "codecs": ["opus"],
                    "jitterFloorMs": get_audio_rx_jitter_floor_ms(),
                    "jitterCeilingMs": get_audio_rx_jitter_ceiling_ms(),
                },
                "antennas": profile.antenna_tx_count,
                "webrtc": rtc_capability_info(),
                **({"controls": profile.controls} if profile.controls else {}),
                "txBands": [
                    {"name": b.name, "start": b.start, "end": b.end}
                    for fr in profile.freq_ranges
                    for b in fr.bands
                ]
                or None,
                **self._get_meter_cal_payload(),
            },
            separators=(",", ":"),
        ).encode()
        await _send_json(writer, body, headers)

    async def _serve_dx_spots(self, writer: asyncio.StreamWriter) -> None:
        spots = self._spot_buffer.get_spots()
        body = json.dumps({"spots": spots}, separators=(",", ":")).encode()
        await _send_response(
            writer, 200, "OK", body, {"Content-Type": "application/json"}
        )

    async def _serve_band_plan_segments(
        self,
        writer: asyncio.StreamWriter,
        query: dict[str, list[str]],
    ) -> None:
        """GET /api/v1/band-plan/segments?start=<hz>&end=<hz>[&layers=ham,broadcast]"""
        try:
            start = int(query.get("start", ["0"])[0])
            end = int(query.get("end", ["60000000"])[0])
        except (ValueError, IndexError):
            start, end = 0, 60_000_000

        layer_list = query.get("layers", [])
        layer_str = layer_list[0] if layer_list else None
        layers = layer_str.split(",") if layer_str else None

        segments = self._band_plan.get_segments(start, end, layers)

        # EiBi on-air overlay segments (optional)
        if self._eibi.loaded and (layers is None or "broadcast-eibi" in layers):
            try:
                segments.extend(self._eibi.get_segments(start, end, on_air_only=True))
            except Exception:
                logger.exception("eibi: failed to generate overlay segments")

        # Sort by start freq (stable for overlay rendering)
        segments.sort(key=lambda s: s.get("start", 0))

        body = json.dumps({"segments": segments}, separators=(",", ":")).encode()
        await _send_response(
            writer, 200, "OK", body, {"Content-Type": "application/json"}
        )

    async def _serve_band_plan_layers(self, writer: asyncio.StreamWriter) -> None:
        """GET /api/v1/band-plan/layers"""
        layers = self._band_plan.get_layers()

        # Add pseudo-layer for EiBi overlay (even if not loaded yet)
        layers.append(
            {
                "name": "EiBi (live)",
                "layer": "broadcast-eibi",
                "priority": 5,
                "file": "sked-*.csv",
                "source": "http://www.eibispace.de/dx/",
                "region": "",
                "updated": self._eibi.last_updated or "",
            }
        )

        # Sort by priority desc
        layers = sorted(layers, key=lambda layer: -layer.get("priority", 0))

        body = json.dumps({"layers": layers}, separators=(",", ":")).encode()
        await _send_response(
            writer, 200, "OK", body, {"Content-Type": "application/json"}
        )

    async def _serve_band_plan_config(self, writer: asyncio.StreamWriter) -> None:
        """GET /api/v1/band-plan/config"""
        body = json.dumps(
            {
                "region": self._band_plan.region,
                "availableRegions": ["US", "IARU-R1", "IARU-R2", "IARU-R3"],
            },
            separators=(",", ":"),
        ).encode()
        await _send_response(
            writer, 200, "OK", body, {"Content-Type": "application/json"}
        )

    async def _handle_band_plan_config(
        self,
        writer: asyncio.StreamWriter,
        headers: dict[str, str] | None = None,
        reader: asyncio.StreamReader | None = None,
    ) -> None:
        """POST /api/v1/band-plan/config — update region, reload band plans."""
        try:
            body_bytes = b""
            if reader is not None:
                cl = int((headers or {}).get("content-length", "0"))
                if cl > 0:
                    read_result = await _read_capped_body(reader, cl)
                    if read_result is None:
                        err = json.dumps(
                            {"error": "request_too_large"},
                            separators=(",", ":"),
                        ).encode()
                        await _send_response(
                            writer,
                            413,
                            "Content Too Large",
                            err,
                            {"Content-Type": "application/json"},
                        )
                        writer.close()
                        return
                    body_bytes = read_result
            if not body_bytes:
                err = json.dumps(
                    {"error": "missing_body"},
                    separators=(",", ":"),
                ).encode()
                await _send_response(
                    writer,
                    400,
                    "Bad Request",
                    err,
                    {"Content-Type": "application/json"},
                )
                return

            payload = json.loads(body_bytes)
            new_region = payload.get("region", "")
            valid_regions = {"US", "IARU-R1", "IARU-R2", "IARU-R3"}
            if new_region not in valid_regions:
                err = json.dumps(
                    {"error": "invalid_region", "valid": sorted(valid_regions)},
                    separators=(",", ":"),
                ).encode()
                await _send_response(
                    writer,
                    400,
                    "Bad Request",
                    err,
                    {"Content-Type": "application/json"},
                )
                return

            # Write config and reload
            from pathlib import Path as _Path

            project_bp = _Path(__file__).resolve().parents[3] / "band-plans"
            config_path = project_bp / "_config.toml"
            existing: dict[str, Any] = {}
            if config_path.is_file():
                existing = await asyncio.to_thread(
                    _load_band_plan_config_sync, config_path
                )

            # Write back (simple format — tomli-w not required)
            content = _format_band_plan_config(new_region, existing)
            await asyncio.to_thread(config_path.write_text, content)

            # Reload band plans
            self._band_plan.load(project_bp)
            logger.info("band-plan: region changed to %s, reloaded", new_region)

            body = json.dumps(
                {
                    "status": "ok",
                    "region": self._band_plan.region,
                    "segments": self._band_plan.segment_count,
                },
                separators=(",", ":"),
            ).encode()
            await _send_response(
                writer,
                200,
                "OK",
                body,
                {"Content-Type": "application/json"},
            )
        except Exception as exc:
            logger.exception("band-plan config update failed")
            err = json.dumps(
                {"error": str(exc)},
                separators=(",", ":"),
            ).encode()
            await _send_response(
                writer,
                500,
                "Internal Server Error",
                err,
                {"Content-Type": "application/json"},
            )

    async def _handle_eibi_fetch(
        self,
        writer: asyncio.StreamWriter,
        headers: dict[str, str] | None = None,
        reader: asyncio.StreamReader | None = None,
    ) -> None:
        """POST /api/v1/eibi/fetch — download and parse EiBi database."""
        try:
            force = False
            if reader is not None:
                cl = int((headers or {}).get("content-length", "0"))
                if cl > 0:
                    read_result = await _read_capped_body(reader, cl)
                    if read_result is None:
                        err = json.dumps(
                            {"error": "request_too_large"},
                            separators=(",", ":"),
                        ).encode()
                        await _send_response(
                            writer,
                            413,
                            "Content Too Large",
                            err,
                            {"Content-Type": "application/json"},
                        )
                        writer.close()
                        return
                    payload = json.loads(read_result)
                    force = payload.get("force", False)

            result = await self._eibi.fetch(force=force)
            body = json.dumps(result, separators=(",", ":")).encode()
            status = 200 if result.get("status") == "ok" else 502
            await _send_response(
                writer,
                status,
                "OK" if status == 200 else "Bad Gateway",
                body,
                {"Content-Type": "application/json"},
            )
        except Exception as exc:
            logger.exception("eibi fetch failed")
            err = json.dumps(
                {"error": str(exc)},
                separators=(",", ":"),
            ).encode()
            await _send_response(
                writer,
                500,
                "Internal Server Error",
                err,
                {"Content-Type": "application/json"},
            )

    async def _serve_eibi_stations(
        self,
        writer: asyncio.StreamWriter,
        query: dict[str, list[str]],
    ) -> None:
        """GET /api/v1/eibi/stations — paginated station list with filters."""
        if not self._eibi.loaded:
            err = json.dumps(
                {
                    "error": "not_loaded",
                    "message": "EiBi data not loaded. POST /api/v1/eibi/fetch first.",
                },
                separators=(",", ":"),
            ).encode()
            await _send_response(
                writer,
                404,
                "Not Found",
                err,
                {"Content-Type": "application/json"},
            )
            return

        on_air = query.get("on_air", [""])[0].lower() in ("true", "1", "yes")
        band = query.get("band", [None])[0]
        language = query.get("lang", [None])[0] or query.get("language", [None])[0]
        country = query.get("country", [None])[0]
        q = query.get("q", [None])[0] or query.get("query", [None])[0]
        sort = query.get("sort", ["freq"])[0]
        page = int(query.get("page", ["1"])[0])
        limit = min(int(query.get("limit", ["100"])[0]), 500)

        result = self._eibi.get_stations(
            on_air=on_air,
            band=band,
            language=language,
            country=country,
            query=q,
            sort=sort,
            page=page,
            limit=limit,
        )
        body = json.dumps(result, separators=(",", ":")).encode()
        await _send_response(
            writer,
            200,
            "OK",
            body,
            {"Content-Type": "application/json"},
        )

    async def _serve_eibi_segments(
        self,
        writer: asyncio.StreamWriter,
        query: dict[str, list[str]],
    ) -> None:
        """GET /api/v1/eibi/segments — on-air stations as overlay segments."""
        if not self._eibi.loaded:
            body = json.dumps(
                {"segments": []},
                separators=(",", ":"),
            ).encode()
            await _send_response(
                writer,
                200,
                "OK",
                body,
                {"Content-Type": "application/json"},
            )
            return

        start_hz = int(query.get("start", ["0"])[0])
        end_hz = int(query.get("end", ["30000000"])[0])
        on_air = query.get("on_air", ["true"])[0].lower() != "false"

        segments = self._eibi.get_segments(start_hz, end_hz, on_air_only=on_air)
        body = json.dumps(
            {"segments": segments},
            separators=(",", ":"),
        ).encode()
        await _send_response(
            writer,
            200,
            "OK",
            body,
            {"Content-Type": "application/json"},
        )

    async def _send_json(
        self,
        writer: asyncio.StreamWriter,
        status: int,
        reason: str,
        payload: dict[str, Any],
    ) -> None:
        body = json.dumps(payload, separators=(",", ":")).encode()
        await _send_response(
            writer,
            status,
            reason,
            body,
            {"Content-Type": "application/json"},
        )

    async def _read_json_object(
        self,
        writer: asyncio.StreamWriter,
        headers: dict[str, str] | None,
        reader: asyncio.StreamReader | None,
    ) -> dict[str, Any] | None:
        try:
            content_length = int((headers or {}).get("content-length", "0"))
        except ValueError:
            await self._send_json(
                writer,
                400,
                "Bad Request",
                {
                    "error": "invalid_content_length",
                    "message": "Content-Length must be an integer",
                },
            )
            return None
        if reader is None or content_length <= 0:
            await self._send_json(
                writer,
                400,
                "Bad Request",
                {"error": "missing_body", "message": "JSON request body required"},
            )
            return None
        read_result = await _read_capped_body(reader, content_length)
        if read_result is None:
            await self._send_json(
                writer,
                413,
                "Content Too Large",
                {"error": "request_too_large"},
            )
            writer.close()
            return None
        try:
            payload = json.loads(read_result)
        except json.JSONDecodeError as exc:
            await self._send_json(
                writer,
                400,
                "Bad Request",
                {"error": "invalid_json", "message": str(exc)},
            )
            return None
        if not isinstance(payload, dict):
            await self._send_json(
                writer,
                400,
                "Bad Request",
                {
                    "error": "invalid_request",
                    "message": "JSON body must be an object",
                },
            )
            return None
        return payload

    def _control_handler_for(self, *, server: Any | None = None) -> ControlHandler:
        return ControlHandler(
            None,  # type: ignore[arg-type]
            self._radio,
            __version__,
            self._config.radio_model,
            server=server if server is not None else self,
            read_only=self._config.read_only,
        )

    async def _handle_http_commands(
        self,
        path: str,
        writer: asyncio.StreamWriter,
        headers: dict[str, str] | None = None,
        reader: asyncio.StreamReader | None = None,
    ) -> None:
        """Handle POST /api/v1/commands and /api/v1/commands/batch."""
        if self._radio is None and path != "/api/v1/commands/batch":
            await self._send_json(
                writer,
                503,
                "Service Unavailable",
                {"error": "no_radio", "message": "No radio configured"},
            )
            return

        payload = await self._read_json_object(writer, headers, reader)
        if payload is None:
            return

        if path == "/api/v1/commands":
            await self._handle_http_single_command(writer, payload)
            return
        if path == "/api/v1/commands/batch":
            if self._radio is None:
                raw_steps = payload.get("steps")
                is_transaction_only_batch = (
                    isinstance(raw_steps, list)
                    and bool(raw_steps)
                    and all(
                        self._is_http_batch_transaction_step(step) for step in raw_steps
                    )
                )
                if not is_transaction_only_batch:
                    await self._send_json(
                        writer,
                        503,
                        "Service Unavailable",
                        {"error": "no_radio", "message": "No radio configured"},
                    )
                    return
            await self._handle_http_command_batch(writer, payload)
            return

        await _send_response(writer, 404, "Not Found", b"", {})

    @staticmethod
    def _parse_civ_byte(value: Any, name: str, *, required: bool = True) -> int | None:
        if value is None:
            if required:
                raise ValueError(f"missing required '{name}' parameter")
            return None
        if isinstance(value, bool) or not isinstance(value, int):
            raise ValueError(f"{name} must be an integer byte")
        parsed = int(value)
        if not 0 <= parsed <= 0xFF:
            raise ValueError(f"{name} must be 0-255, got {parsed}")
        return parsed

    @staticmethod
    def _parse_civ_hex_data(value: Any) -> bytes:
        if value is None:
            return b""
        if not isinstance(value, str):
            raise ValueError("data must be a hex string")
        if len(value) % 2 != 0:
            raise ValueError("data must be an even-length hex string")
        if any(ch not in "0123456789abcdefABCDEF" for ch in value):
            raise ValueError("data must be a compact hex string")
        return bytes.fromhex(value)

    @staticmethod
    def _parse_civ_transaction_timeout(value: Any) -> float | None:
        if value is None:
            return None
        if isinstance(value, bool) or not isinstance(value, int | float):
            raise ValueError("timeout_ms must be a positive finite number")
        timeout_ms = float(value)
        if not math.isfinite(timeout_ms):
            raise ValueError("timeout_ms must be a positive finite number")
        if timeout_ms <= 0:
            raise ValueError("timeout_ms must be positive")
        return timeout_ms / 1000.0

    @staticmethod
    def _serialize_civ_transaction_result(result: Any) -> dict[str, Any]:
        frame = getattr(result, "frame", None)
        frame_bytes = getattr(result, "frame_bytes", None)
        if frame is None:
            return {
                "frame": None,
                "command": None,
                "sub": None,
                "data": None,
            }
        return {
            "frame": frame_bytes.hex().upper() if frame_bytes is not None else None,
            "command": frame.command,
            "sub": frame.sub,
            "data": frame.data.hex().upper(),
        }

    async def _handle_http_civ_transaction(
        self,
        writer: asyncio.StreamWriter,
        headers: dict[str, str] | None = None,
        reader: asyncio.StreamReader | None = None,
    ) -> None:
        """Handle POST /api/v1/civ/transaction."""
        if self._radio is None:
            await self._send_json(
                writer,
                503,
                "Service Unavailable",
                {"error": "no_radio", "message": "No radio configured"},
            )
            return

        payload = await self._read_json_object(writer, headers, reader)
        if payload is None:
            return

        if self._config.read_only:
            await self._send_json(
                writer,
                403,
                "Forbidden",
                {
                    "error": "read_only",
                    "message": "raw CI-V transactions are disabled in read-only mode",
                },
            )
            return

        if not isinstance(self._radio, CivTransactionCapable):
            await self._send_json(
                writer,
                409,
                "Conflict",
                {
                    "error": "unsupported_command",
                    "message": "active backend does not support raw CI-V transactions",
                },
            )
            return

        try:
            command = self._parse_civ_byte(payload.get("command"), "command")
            sub = self._parse_civ_byte(payload.get("sub"), "sub", required=False)
            data = self._parse_civ_hex_data(payload.get("data", ""))
            raw_expect = payload.get("expect", "data")
            if raw_expect not in ("none", "ack", "data"):
                raise ValueError("expect must be one of: none, ack, data")
            timeout = self._parse_civ_transaction_timeout(payload.get("timeout_ms"))
        except ValueError as exc:
            await self._send_json(
                writer,
                400,
                "Bad Request",
                {"error": "invalid_request", "message": str(exc)},
            )
            return

        try:
            assert command is not None
            result = await self._radio.send_civ_transaction(
                command,
                sub=sub,
                data=data,
                expect=raw_expect,
                timeout=timeout,
            )
        except (RigplaneTimeoutError, TimeoutError):
            await self._send_json(
                writer,
                504,
                "Gateway Timeout",
                {
                    "error": "transaction_timeout",
                    "message": "raw CI-V transaction timed out",
                },
            )
            return
        except RuntimeError as exc:
            message = str(exc)
            if "already owned" in message:
                await self._send_json(
                    writer,
                    409,
                    "Conflict",
                    {"error": "civ_owner_conflict", "message": message},
                )
                return
            await self._send_json(
                writer,
                500,
                "Internal Server Error",
                {"error": "transaction_failed", "message": message},
            )
            return
        except ValueError as exc:
            await self._send_json(
                writer,
                400,
                "Bad Request",
                {"error": "invalid_request", "message": str(exc)},
            )
            return

        status = getattr(result, "status", "response")
        response: dict[str, Any] = {
            "ok": status != "nak",
            "status": status,
            "result": self._serialize_civ_transaction_result(result),
        }
        if status == "nak":
            response["error"] = "radio_nak"
        if "id" in payload:
            response["id"] = payload["id"]
        await self._send_json(writer, 200, "OK", response)

    async def _handle_http_single_command(
        self,
        writer: asyncio.StreamWriter,
        payload: dict[str, Any],
    ) -> None:
        raw_name = payload.get("name")
        if not isinstance(raw_name, str) or not raw_name:
            await self._send_json(
                writer,
                400,
                "Bad Request",
                {"error": "invalid_request", "message": "name must be a string"},
            )
            return
        if raw_name not in ControlHandler._COMMANDS:  # noqa: SLF001
            await self._send_json(
                writer,
                400,
                "Bad Request",
                {
                    "error": "unknown_command",
                    "message": f"unknown command: {raw_name!r}",
                },
            )
            return
        raw_params = payload.get("params", {})
        if not isinstance(raw_params, dict):
            await self._send_json(
                writer,
                400,
                "Bad Request",
                {"error": "invalid_request", "message": "params must be an object"},
            )
            return
        try:
            result = await self._control_handler_for()._enqueue_command(  # noqa: SLF001
                raw_name,
                raw_params,
            )
        except PermissionError as exc:
            await self._send_json(
                writer,
                403,
                "Forbidden",
                {"error": "read_only", "message": str(exc)},
            )
            return
        except ValueError as exc:
            await self._send_json(
                writer,
                400,
                "Bad Request",
                {"error": "invalid_request", "message": str(exc)},
            )
            return
        except RuntimeError as exc:
            message = str(exc)
            status, reason, code = (
                (409, "Conflict", "unsupported_command")
                if "does not support" in message
                else (500, "Internal Server Error", "command_failed")
            )
            await self._send_json(
                writer,
                status,
                reason,
                {"error": code, "message": message},
            )
            return

        response: dict[str, Any] = {
            "ok": True,
            "name": raw_name,
            "result": result,
        }
        if "id" in payload:
            response["id"] = payload["id"]
        await self._send_json(writer, 200, "OK", response)

    async def _prepare_http_batch_step(
        self,
        index: int,
        raw_step: Any,
    ) -> _HttpBatchStep:
        if not isinstance(raw_step, dict):
            raise _HttpBatchValidationError(
                "invalid_step",
                f"step {index} must be an object",
            )
        raw_name = raw_step.get("name")
        if not isinstance(raw_name, str) or not raw_name:
            raise _HttpBatchValidationError(
                "invalid_step",
                f"step {index} name must be a string",
            )
        raw_params = raw_step.get("params", {})
        if not isinstance(raw_params, dict):
            raise _HttpBatchValidationError(
                "invalid_step",
                f"step {index} params must be an object",
            )
        if raw_name not in ControlHandler._COMMANDS:  # noqa: SLF001
            raise _HttpBatchValidationError(
                "unknown_command",
                f"unknown command: {raw_name!r}",
            )
        if raw_name in ControlHandler._READ_ONLY_HANDLERS:  # noqa: SLF001
            raise _HttpBatchValidationError(
                "unsupported_in_batch",
                f"command {raw_name!r} bypasses the command queue",
            )

        collector = _HttpCommandCollector()
        proxy_server = type("_HttpBatchProxy", (), {"command_queue": collector})()
        result = await self._control_handler_for(server=proxy_server)._enqueue_command(  # noqa: SLF001
            raw_name,
            raw_params,
        )
        if len(collector.commands) != 1:
            raise _HttpBatchValidationError(
                "unsupported_in_batch",
                f"command {raw_name!r} did not produce exactly one queued command",
            )
        return _HttpBatchStep(
            index=index,
            name=raw_name,
            command=collector.commands[0],
            result=result,
        )

    def _prepare_http_batch_transaction_step(
        self,
        index: int,
        raw_step: Any,
    ) -> _HttpBatchTransactionStep:
        if not isinstance(raw_step, dict):
            raise _HttpBatchValidationError(
                "invalid_step",
                f"step {index} must be an object",
            )
        extra_keys = set(raw_step) - _CIV_TRANSACTION_BATCH_STEP_KEYS
        if extra_keys:
            extra = ", ".join(sorted(extra_keys))
            raise _HttpBatchValidationError(
                "invalid_step",
                f"step {index} has unsupported raw CI-V transaction field(s): {extra}",
            )
        try:
            if raw_step.get("type") != _CIV_TRANSACTION_BATCH_STEP_TYPE:
                raise ValueError("type must be raw_civ_transaction")
            command = self._parse_civ_byte(raw_step.get("command"), "command")
            sub = self._parse_civ_byte(raw_step.get("sub"), "sub", required=False)
            data = self._parse_civ_hex_data(raw_step.get("data", ""))
            raw_expect = raw_step.get("expect")
            if raw_expect not in ("none", "ack", "data"):
                raise ValueError("expect must be one of: none, ack, data")
            timeout = self._parse_civ_transaction_timeout(raw_step.get("timeout_ms"))
        except ValueError as exc:
            raise _HttpBatchValidationError("invalid_request", str(exc)) from exc
        assert command is not None
        return _HttpBatchTransactionStep(
            index=index,
            step_id=raw_step.get("id", _MISSING_BATCH_STEP_ID),
            command=command,
            sub=sub,
            data=data,
            expect=raw_expect,
            timeout=timeout if timeout is not None else _COMMAND_BATCH_STEP_TIMEOUT,
        )

    @staticmethod
    def _is_http_batch_transaction_step(raw_step: Any) -> bool:
        return (
            isinstance(raw_step, dict)
            and raw_step.get("type") == _CIV_TRANSACTION_BATCH_STEP_TYPE
        )

    @staticmethod
    def _is_http_batch_typed_step(raw_step: Any) -> bool:
        return (
            isinstance(raw_step, dict) and "type" in raw_step and "name" not in raw_step
        )

    def _failed_transaction_batch_result(
        self,
        step: _HttpBatchTransactionStep,
        *,
        status: str,
        error: str,
        message: str,
        result: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        response: dict[str, Any] = {
            "index": step.index,
            "type": _CIV_TRANSACTION_BATCH_STEP_TYPE,
            "ok": False,
            "status": status,
            "error": error,
            "message": message,
        }
        if step.step_id is not _MISSING_BATCH_STEP_ID:
            response["id"] = step.step_id
        if result is not None:
            response["result"] = result
        return response

    async def _execute_http_batch_transaction_step(
        self,
        step: _HttpBatchTransactionStep,
    ) -> dict[str, Any]:
        if self._config.read_only:
            return self._failed_transaction_batch_result(
                step,
                status="read_only",
                error="read_only",
                message="raw CI-V transactions are disabled in read-only mode",
            )
        if self._radio is None:
            return self._failed_transaction_batch_result(
                step,
                status="no_radio",
                error="no_radio",
                message="No radio configured",
            )
        if not isinstance(self._radio, CivTransactionCapable):
            return self._failed_transaction_batch_result(
                step,
                status="unsupported",
                error="unsupported_command",
                message="active backend does not support raw CI-V transactions",
            )

        try:
            result = await self._radio.send_civ_transaction(
                step.command,
                sub=step.sub,
                data=step.data,
                expect=step.expect,
                timeout=step.timeout,
            )
        except (RigplaneTimeoutError, TimeoutError):
            return self._failed_transaction_batch_result(
                step,
                status="timed_out",
                error="transaction_timeout",
                message="raw CI-V transaction timed out",
            )
        except RuntimeError as exc:
            message = str(exc)
            if "already owned" in message:
                return self._failed_transaction_batch_result(
                    step,
                    status="owner_conflict",
                    error="civ_owner_conflict",
                    message=message,
                )
            return self._failed_transaction_batch_result(
                step,
                status="failed_execution",
                error="transaction_failed",
                message=message,
            )
        except ValueError as exc:
            return self._failed_transaction_batch_result(
                step,
                status="failed_validation",
                error="invalid_request",
                message=str(exc),
            )

        status = getattr(result, "status", "response")
        response: dict[str, Any] = {
            "index": step.index,
            "type": _CIV_TRANSACTION_BATCH_STEP_TYPE,
            "ok": status != "nak",
            "status": status,
        }
        if step.step_id is not _MISSING_BATCH_STEP_ID:
            response["id"] = step.step_id
        serialized = self._serialize_civ_transaction_result(result)
        if status == "nak":
            response.update(
                {
                    "error": "radio_nak",
                    "message": "radio returned CI-V NAK",
                    "result": serialized,
                }
            )
        else:
            response["result"] = serialized
        return response

    @staticmethod
    def _skipped_batch_result(index: int, name: str | None = None) -> dict[str, Any]:
        result: dict[str, Any] = {
            "index": index,
            "ok": False,
            "status": "skipped",
            "error": "skipped_after_failure",
            "message": "skipped after earlier batch failure",
        }
        if name is not None:
            result["name"] = name
        return result

    @classmethod
    def _skipped_batch_result_for_raw_step(
        cls,
        index: int,
        raw_step: Any,
    ) -> dict[str, Any]:
        if (
            isinstance(raw_step, dict)
            and raw_step.get("type") == _CIV_TRANSACTION_BATCH_STEP_TYPE
        ):
            result = cls._skipped_batch_result(index)
            result["type"] = _CIV_TRANSACTION_BATCH_STEP_TYPE
            if "id" in raw_step:
                result["id"] = raw_step["id"]
            return result
        name = raw_step.get("name") if isinstance(raw_step, dict) else None
        return cls._skipped_batch_result(index, name)

    async def _handle_http_command_batch(
        self,
        writer: asyncio.StreamWriter,
        payload: dict[str, Any],
    ) -> None:
        raw_steps = payload.get("steps")
        if not isinstance(raw_steps, list):
            await self._send_json(
                writer,
                400,
                "Bad Request",
                {"error": "invalid_request", "message": "steps must be a list"},
            )
            return
        if not raw_steps:
            await self._send_json(
                writer,
                400,
                "Bad Request",
                {"error": "invalid_request", "message": "steps must not be empty"},
            )
            return
        if len(raw_steps) > _MAX_COMMAND_BATCH_STEPS:
            await self._send_json(
                writer,
                400,
                "Bad Request",
                {
                    "error": "batch_too_large",
                    "message": f"max {_MAX_COMMAND_BATCH_STEPS} steps supported",
                },
            )
            return

        raw_continue_on_error = payload.get("continue_on_error", False)
        if not isinstance(raw_continue_on_error, bool):
            await self._send_json(
                writer,
                400,
                "Bad Request",
                {
                    "error": "invalid_request",
                    "message": "continue_on_error must be a boolean",
                },
            )
            return
        continue_on_error = raw_continue_on_error
        results: list[dict[str, Any]] = []

        for index, raw_step in enumerate(raw_steps):
            if self._is_http_batch_transaction_step(raw_step):
                try:
                    transaction_step = self._prepare_http_batch_transaction_step(
                        index,
                        raw_step,
                    )
                    transaction_result = (
                        await self._execute_http_batch_transaction_step(
                            transaction_step
                        )
                    )
                except _HttpBatchValidationError as exc:
                    transaction_result = {
                        "index": index,
                        "type": _CIV_TRANSACTION_BATCH_STEP_TYPE,
                        "ok": False,
                        "status": "failed_validation",
                        "error": exc.error,
                        "message": str(exc),
                    }
                    if isinstance(raw_step, dict) and "id" in raw_step:
                        transaction_result["id"] = raw_step["id"]

                results.append(transaction_result)
                if transaction_result.get("ok") is True or continue_on_error:
                    continue
                for skip_index in range(index + 1, len(raw_steps)):
                    results.append(
                        self._skipped_batch_result_for_raw_step(
                            skip_index,
                            raw_steps[skip_index],
                        )
                    )
                await self._send_batch_response(writer, payload, results)
                return

            if self._is_http_batch_typed_step(raw_step):
                raw_type = raw_step.get("type")
                results.append(
                    {
                        "index": index,
                        "ok": False,
                        "status": "failed_validation",
                        "error": "unknown_step_type",
                        "message": f"unknown step type: {raw_type!r}",
                    }
                )
                if continue_on_error:
                    continue
                for skip_index in range(index + 1, len(raw_steps)):
                    results.append(
                        self._skipped_batch_result_for_raw_step(
                            skip_index,
                            raw_steps[skip_index],
                        )
                    )
                await self._send_batch_response(writer, payload, results)
                return

            name = raw_step.get("name") if isinstance(raw_step, dict) else None
            if self._radio is None:
                results.append(
                    {
                        "index": index,
                        "name": name,
                        "ok": False,
                        "status": "no_radio",
                        "error": "no_radio",
                        "message": "No radio configured",
                    }
                )
                if continue_on_error:
                    continue
                for skip_index in range(index + 1, len(raw_steps)):
                    results.append(
                        self._skipped_batch_result_for_raw_step(
                            skip_index,
                            raw_steps[skip_index],
                        )
                    )
                await self._send_batch_response(writer, payload, results)
                return
            try:
                step = await self._prepare_http_batch_step(index, raw_step)
            except PermissionError as exc:
                results.append(
                    {
                        "index": index,
                        "name": name,
                        "ok": False,
                        "status": "failed_validation",
                        "error": "read_only",
                        "message": str(exc),
                    }
                )
                step = None
            except _HttpBatchValidationError as exc:
                results.append(
                    {
                        "index": index,
                        "name": name,
                        "ok": False,
                        "status": "failed_validation",
                        "error": exc.error,
                        "message": str(exc),
                    }
                )
                step = None
            except (ValueError, RuntimeError) as exc:
                message = str(exc)
                error = (
                    "unsupported_command"
                    if "does not support" in message or "not supported" in message
                    else "invalid_request"
                )
                results.append(
                    {
                        "index": index,
                        "name": name,
                        "ok": False,
                        "status": "failed_validation",
                        "error": error,
                        "message": message,
                    }
                )
                step = None

            if step is None:
                if continue_on_error:
                    continue
                for skip_index in range(index + 1, len(raw_steps)):
                    results.append(
                        self._skipped_batch_result_for_raw_step(
                            skip_index,
                            raw_steps[skip_index],
                        )
                    )
                await self._send_batch_response(writer, payload, results)
                return

            loop = asyncio.get_running_loop()
            future: asyncio.Future[None] = loop.create_future()
            self._command_queue.put_ordered(step.command, future=future)
            try:
                await asyncio.wait_for(future, timeout=_COMMAND_BATCH_STEP_TIMEOUT)
            except TimeoutError as exc:
                results.append(
                    {
                        "index": step.index,
                        "name": step.name,
                        "ok": False,
                        "status": "timed_out",
                        "error": "command_timeout",
                        "message": str(exc) or type(exc).__name__,
                    }
                )
                if not continue_on_error:
                    for skip_index in range(step.index + 1, len(raw_steps)):
                        results.append(
                            self._skipped_batch_result_for_raw_step(
                                skip_index,
                                raw_steps[skip_index],
                            )
                        )
                    await self._send_batch_response(writer, payload, results)
                    return
            except Exception as exc:
                results.append(
                    {
                        "index": step.index,
                        "name": step.name,
                        "ok": False,
                        "status": "failed_execution",
                        "error": "command_failed",
                        "message": str(exc) or type(exc).__name__,
                    }
                )
                if not continue_on_error:
                    for skip_index in range(step.index + 1, len(raw_steps)):
                        results.append(
                            self._skipped_batch_result_for_raw_step(
                                skip_index,
                                raw_steps[skip_index],
                            )
                        )
                    await self._send_batch_response(writer, payload, results)
                    return
            else:
                results.append(
                    {
                        "index": step.index,
                        "name": step.name,
                        "ok": True,
                        "status": "executed",
                        "result": step.result,
                    }
                )

        await self._send_batch_response(writer, payload, results)

    async def _send_batch_response(
        self,
        writer: asyncio.StreamWriter,
        payload: dict[str, Any],
        results: list[dict[str, Any]],
    ) -> None:
        response: dict[str, Any] = {
            "ok": all(result.get("ok") is True for result in results),
            "results": results,
        }
        if "id" in payload:
            response["id"] = payload["id"]
        await self._send_json(writer, 200, "OK", response)

    async def _handle_radio_control(
        self,
        path: str,
        writer: asyncio.StreamWriter,
        headers: dict[str, str] | None = None,
        reader: asyncio.StreamReader | None = None,
    ) -> None:
        """Handle POST /api/v1/radio/{disconnect,connect,power,cw/send,cw/stop}."""
        radio = self._radio
        if radio is None:
            body = json.dumps(
                {"error": "no_radio", "message": "No radio configured"},
                separators=(",", ":"),
            ).encode()
            await _send_response(
                writer,
                503,
                "Service Unavailable",
                body,
                {"Content-Type": "application/json"},
            )
            return

        try:
            if path == "/api/v1/radio/disconnect":
                await radio.disconnect()
                resp = {"status": "disconnected"}
            elif path == "/api/v1/radio/connect":
                await radio.connect()
                resp = {"status": "connecting"}
            elif path == "/api/v1/radio/power":
                # Read JSON body for power state
                body_bytes = b""
                if reader is not None:
                    cl = int((headers or {}).get("content-length", "0"))
                    if cl > 0:
                        read_result = await _read_capped_body(reader, cl)
                        if read_result is None:
                            err = json.dumps(
                                {"error": "request_too_large"},
                                separators=(",", ":"),
                            ).encode()
                            await _send_response(
                                writer,
                                413,
                                "Content Too Large",
                                err,
                                {"Content-Type": "application/json"},
                            )
                            writer.close()
                            return
                        body_bytes = read_result
                if not body_bytes:
                    err = json.dumps(
                        {
                            "error": "missing_body",
                            "message": "JSON body with 'state' required",
                        },
                        separators=(",", ":"),
                    ).encode()
                    await _send_response(
                        writer,
                        400,
                        "Bad Request",
                        err,
                        {"Content-Type": "application/json"},
                    )
                    return
                payload = json.loads(body_bytes)
                power_state = payload.get("state")
                if power_state not in ("on", "off"):
                    err = json.dumps(
                        {
                            "error": "invalid_state",
                            "message": "state must be 'on' or 'off'",
                        },
                        separators=(",", ":"),
                    ).encode()
                    await _send_response(
                        writer,
                        400,
                        "Bad Request",
                        err,
                        {"Content-Type": "application/json"},
                    )
                    return
                if power_state == "on" and not getattr(
                    radio, "control_connected", False
                ):
                    # Radio is off → reconnect transport first, then send power-on CI-V
                    logger.info("power-on: radio disconnected, reconnecting first")
                    try:
                        await radio.connect()
                        # Give transport a moment to establish
                        await asyncio.sleep(1.0)
                    except Exception as conn_err:
                        logger.warning("power-on: reconnect failed: %s", conn_err)
                        # Try anyway — some radios accept CI-V on stale transport
                is_on = power_state == "on"
                await radio.set_powerstat(is_on)  # type: ignore[attr-defined]
                # Optimistic state update: radio won't respond to polls when off
                if self._radio_state is not None:
                    self._radio_state.power_on = is_on
                self._on_radio_state_change("powerstat_changed", {"power_on": is_on})
                resp = {"status": "ok", "power": power_state}
            elif path == "/api/v1/radio/cw/send":
                body_bytes = b""
                if reader is not None:
                    cl = int((headers or {}).get("content-length", "0"))
                    if cl > 0:
                        read_result = await _read_capped_body(reader, cl)
                        if read_result is None:
                            err = json.dumps(
                                {"error": "request_too_large"},
                                separators=(",", ":"),
                            ).encode()
                            await _send_response(
                                writer,
                                413,
                                "Content Too Large",
                                err,
                                {"Content-Type": "application/json"},
                            )
                            writer.close()
                            return
                        body_bytes = read_result
                if not body_bytes:
                    err = json.dumps(
                        {
                            "error": "missing_body",
                            "message": "JSON body with 'text' required",
                        },
                        separators=(",", ":"),
                    ).encode()
                    await _send_response(
                        writer,
                        400,
                        "Bad Request",
                        err,
                        {"Content-Type": "application/json"},
                    )
                    return
                payload = json.loads(body_bytes)
                text = payload.get("text") if isinstance(payload, dict) else None
                if not isinstance(text, str):
                    err = json.dumps(
                        {
                            "error": "invalid_text",
                            "message": "text must be a string",
                        },
                        separators=(",", ":"),
                    ).encode()
                    await _send_response(
                        writer,
                        400,
                        "Bad Request",
                        err,
                        {"Content-Type": "application/json"},
                    )
                    return
                handler = ControlHandler(
                    None,  # type: ignore[arg-type]
                    radio,
                    __version__,
                    self._config.radio_model,
                    server=self,
                    read_only=self._config.read_only,
                )
                resp = await handler._enqueue_command(  # noqa: SLF001
                    "send_cw_text",
                    {"text": text},
                )
            elif path == "/api/v1/radio/cw/stop":
                handler = ControlHandler(
                    None,  # type: ignore[arg-type]
                    radio,
                    __version__,
                    self._config.radio_model,
                    server=self,
                    read_only=self._config.read_only,
                )
                resp = await handler._enqueue_command(  # noqa: SLF001
                    "stop_cw_text",
                    {},
                )
            else:
                await _send_response(writer, 404, "Not Found", b"", {})
                return
        except PermissionError as exc:
            body = json.dumps(
                {"error": "read_only", "message": str(exc)},
                separators=(",", ":"),
            ).encode()
            await _send_response(
                writer,
                403,
                "Forbidden",
                body,
                {"Content-Type": "application/json"},
            )
            return
        except ValueError as exc:
            body = json.dumps(
                {"error": "invalid_request", "message": str(exc)},
                separators=(",", ":"),
            ).encode()
            await _send_response(
                writer,
                400,
                "Bad Request",
                body,
                {"Content-Type": "application/json"},
            )
            return
        except RuntimeError as exc:
            message = str(exc)
            status, reason, code = (
                (409, "Conflict", "unsupported_command")
                if "does not support" in message
                else (500, "Internal Server Error", "command_failed")
            )
            body = json.dumps(
                {"error": code, "message": message},
                separators=(",", ":"),
            ).encode()
            await _send_response(
                writer,
                status,
                reason,
                body,
                {"Content-Type": "application/json"},
            )
            return
        except Exception as exc:
            body = json.dumps(
                {"error": str(exc)},
                separators=(",", ":"),
            ).encode()
            await _send_response(
                writer,
                500,
                "Internal Server Error",
                body,
                {"Content-Type": "application/json"},
            )
            return

        body = json.dumps(resp, separators=(",", ":")).encode()
        await _send_response(
            writer,
            200,
            "OK",
            body,
            {"Content-Type": "application/json"},
        )

    async def _handle_rtc_offer(
        self,
        writer: asyncio.StreamWriter,
        headers: dict[str, str] | None,
        reader: asyncio.StreamReader | None,
    ) -> None:
        """Handle POST /api/v1/rtc/offer — WebRTC SDP signaling."""
        if not webrtc_available():
            body = json.dumps(
                {
                    "status": "error",
                    "code": "webrtc_unavailable",
                    "message": "WebRTC backend unavailable; install rigplane[webrtc].",
                },
                separators=(",", ":"),
            ).encode()
            await _send_response(
                writer,
                501,
                "Not Implemented",
                body,
                {"Content-Type": "application/json"},
            )
            return

        # Read request body
        body_bytes = b""
        if reader is not None:
            cl = int((headers or {}).get("content-length", "0"))
            if cl > 0:
                read_result = await _read_capped_body(reader, cl)
                if read_result is None:
                    err = json.dumps(
                        {"error": "request_too_large"},
                        separators=(",", ":"),
                    ).encode()
                    await _send_response(
                        writer,
                        413,
                        "Content Too Large",
                        err,
                        {"Content-Type": "application/json"},
                    )
                    writer.close()
                    return
                body_bytes = read_result
        if not body_bytes:
            err = json.dumps(
                {
                    "status": "error",
                    "code": "missing_body",
                    "message": "JSON body with 'sdp' and 'type' required.",
                },
                separators=(",", ":"),
            ).encode()
            await _send_response(
                writer,
                400,
                "Bad Request",
                err,
                {"Content-Type": "application/json"},
            )
            return

        try:
            payload = json.loads(body_bytes)
        except (json.JSONDecodeError, ValueError):
            err = json.dumps(
                {
                    "status": "error",
                    "code": "invalid_json",
                    "message": "Request body is not valid JSON.",
                },
                separators=(",", ":"),
            ).encode()
            await _send_response(
                writer,
                400,
                "Bad Request",
                err,
                {"Content-Type": "application/json"},
            )
            return

        sdp = payload.get("sdp")
        offer_type = payload.get("type", "offer")
        if not isinstance(sdp, str) or not sdp.strip():
            err = json.dumps(
                {
                    "status": "error",
                    "code": "missing_sdp",
                    "message": "Field 'sdp' is required and must be a non-empty string.",
                },
                separators=(",", ":"),
            ).encode()
            await _send_response(
                writer,
                400,
                "Bad Request",
                err,
                {"Content-Type": "application/json"},
            )
            return

        result = await handle_rtc_offer(sdp, offer_type, self._radio)

        if result.get("status") == "ok":
            status_code, reason = 200, "OK"
        elif result.get("code") == "audio_unavailable":
            status_code, reason = 503, "Service Unavailable"
        elif result.get("code") == "sdp_error":
            status_code, reason = 400, "Bad Request"
        else:
            status_code, reason = 500, "Internal Server Error"

        resp_body = json.dumps(result, separators=(",", ":")).encode()
        await _send_response(
            writer,
            status_code,
            reason,
            resp_body,
            {"Content-Type": "application/json"},
        )

    # ------------------------------------------------------------------
    # Diagnostic upload endpoints (issue #1396)
    # ------------------------------------------------------------------

    def _resolve_diagnostic_dirs(self) -> tuple[pathlib.Path, pathlib.Path]:
        """Resolve config_dir / log_dir for diagnostic bundle generation.

        Uses ``platformdirs`` so the layout matches the always-on
        diagnostic logging (``_logging.py``) and config contributors.
        """
        import platformdirs

        # Defense in depth — already runs at package init via
        # ``configure_diagnostic_logging()``. Idempotent here.
        from rigplane._platformdirs_migration import migrate_legacy_platformdirs

        migrate_legacy_platformdirs()

        config_dir = pathlib.Path(platformdirs.user_config_path("rigplane"))
        log_dir = pathlib.Path(platformdirs.user_cache_path("rigplane")) / "logs"
        return config_dir, log_dir

    async def _handle_diagnose_preview(
        self,
        writer: asyncio.StreamWriter,
        headers: dict[str, str] | None,
        reader: asyncio.StreamReader | None,
    ) -> None:
        """POST /api/v1/diagnose/preview — build a bundle, mint preview/CSRF."""
        from .handlers.diagnostics import _ClientError  # noqa: TID251

        body_dict = await self._read_json_body(writer, headers, reader)
        if body_dict is None:
            return  # response already sent

        config_dir, log_dir = self._resolve_diagnostic_dirs()
        try:
            result = await self._diagnostics.handle_preview(
                body_dict, self._radio, config_dir, log_dir
            )
        except _ClientError as exc:
            await _send_diag_error(writer, exc.status, exc.code, exc.message)
            return
        except Exception as exc:  # noqa: BLE001 — bubble up as 500
            logger.exception("diagnose/preview failed")
            await _send_diag_error(writer, 500, "preview_failed", str(exc))
            return

        body = json.dumps(result, separators=(",", ":")).encode()
        await _send_response(
            writer, 200, "OK", body, {"Content-Type": "application/json"}
        )

    async def _handle_diagnose_send(
        self,
        writer: asyncio.StreamWriter,
        headers: dict[str, str] | None,
        reader: asyncio.StreamReader | None,
    ) -> None:
        """POST /api/v1/diagnose/send — upload a previewed bundle."""
        from .handlers.diagnostics import (  # noqa: TID251
            _ClientError,
            check_origin_or_loopback,
        )
        from rigplane.diagnostics import (
            BundleTooLarge,
            DiagnosticUploadError,
            ForbiddenContent,
            MetadataInvalid,
            NetworkError,
            RateLimited,
            UploadFailed,
        )

        h = headers or {}
        allowed, reason = check_origin_or_loopback(
            h.get("origin"),
            self._config.host,
            self._config.port,
            h.get("host"),
        )
        if not allowed:
            await _send_diag_error(writer, 403, reason, reason)
            return
        csrf = h.get("x-diagnostic-csrf", "")

        body_dict = await self._read_json_body(writer, headers, reader)
        if body_dict is None:
            return

        try:
            result = await self._diagnostics.handle_send(body_dict, csrf)
        except _ClientError as exc:
            await _send_diag_error(writer, exc.status, exc.code, exc.message)
            return
        except RateLimited as exc:
            await _send_diag_error(
                writer,
                429,
                "rate_limited",
                str(exc),
                extra={"retry_after_seconds": exc.retry_after_seconds},
            )
            return
        except BundleTooLarge as exc:
            await _send_diag_error(writer, 413, "bundle_too_large", str(exc))
            return
        except ForbiddenContent as exc:
            await _send_diag_error(
                writer,
                422,
                "forbidden_content",
                str(exc),
                extra={"pattern": exc.pattern} if exc.pattern else None,
            )
            return
        except MetadataInvalid as exc:
            await _send_diag_error(
                writer,
                400,
                "metadata_invalid",
                str(exc),
                extra={"field": exc.field} if exc.field else None,
            )
            return
        except NetworkError as exc:
            await _send_diag_error(writer, 502, "network_error", str(exc))
            return
        except UploadFailed as exc:
            await _send_diag_error(
                writer,
                502,
                "upload_failed",
                str(exc),
                extra={"upstream_status": exc.status},
            )
            return
        except DiagnosticUploadError as exc:
            await _send_diag_error(writer, 502, "upload_failed", str(exc))
            return
        except Exception as exc:  # noqa: BLE001
            logger.exception("diagnose/send failed")
            await _send_diag_error(writer, 500, "send_failed", str(exc))
            return

        body = json.dumps(result, separators=(",", ":")).encode()
        await _send_response(
            writer, 200, "OK", body, {"Content-Type": "application/json"}
        )

    async def _handle_diagnose_save(
        self,
        writer: asyncio.StreamWriter,
        headers: dict[str, str] | None,
        reader: asyncio.StreamReader | None,
    ) -> None:
        """POST /api/v1/diagnose/save — return the bundle as a download."""
        from .handlers.diagnostics import (  # noqa: TID251
            _ClientError,
            check_origin_or_loopback,
        )

        h = headers or {}
        allowed, reason = check_origin_or_loopback(
            h.get("origin"),
            self._config.host,
            self._config.port,
            h.get("host"),
        )
        if not allowed:
            await _send_diag_error(writer, 403, reason, reason)
            return
        csrf = h.get("x-diagnostic-csrf", "")

        body_dict = await self._read_json_body(writer, headers, reader)
        if body_dict is None:
            return

        try:
            zip_bytes, filename = await self._diagnostics.handle_save(body_dict, csrf)
        except _ClientError as exc:
            await _send_diag_error(writer, exc.status, exc.code, exc.message)
            return
        except Exception as exc:  # noqa: BLE001
            logger.exception("diagnose/save failed")
            await _send_diag_error(writer, 500, "save_failed", str(exc))
            return

        await _send_response(
            writer,
            200,
            "OK",
            zip_bytes,
            {
                "Content-Type": "application/zip",
                "Content-Disposition": f'attachment; filename="{filename}"',
            },
        )

    async def _handle_diagnose_delete(
        self,
        writer: asyncio.StreamWriter,
        headers: dict[str, str] | None,
        preview_id: str,
    ) -> None:
        """DELETE /api/v1/diagnose/preview/<preview_id>."""
        from .handlers.diagnostics import (  # noqa: TID251
            _ClientError,
            check_origin_or_loopback,
        )

        h = headers or {}
        allowed, reason = check_origin_or_loopback(
            h.get("origin"),
            self._config.host,
            self._config.port,
            h.get("host"),
        )
        if not allowed:
            await _send_diag_error(writer, 403, reason, reason)
            return
        csrf = h.get("x-diagnostic-csrf", "")
        if not preview_id:
            await _send_diag_error(
                writer, 400, "preview_missing", "preview_id required"
            )
            return

        try:
            await self._diagnostics.handle_delete(preview_id, csrf)
        except _ClientError as exc:
            await _send_diag_error(writer, exc.status, exc.code, exc.message)
            return
        except Exception as exc:  # noqa: BLE001
            logger.exception("diagnose/delete failed")
            await _send_diag_error(writer, 500, "delete_failed", str(exc))
            return
        await _send_response(writer, 204, "No Content", b"", {})

    async def _read_json_body(
        self,
        writer: asyncio.StreamWriter,
        headers: dict[str, str] | None,
        reader: asyncio.StreamReader | None,
    ) -> dict[str, Any] | None:
        """Read a JSON object body; send an error response and return ``None`` on failure."""
        cl_str = (headers or {}).get("content-length", "0")
        try:
            cl = int(cl_str)
        except ValueError:
            cl = 0
        body_bytes = b""
        if reader is not None and cl > 0:
            read_result = await _read_capped_body(reader, cl)
            if read_result is None:
                await _send_diag_error(
                    writer, 413, "request_too_large", "request body too large"
                )
                writer.close()
                return None
            body_bytes = read_result
        if not body_bytes:
            return {}
        try:
            payload = json.loads(body_bytes)
        except (json.JSONDecodeError, ValueError) as exc:
            await _send_diag_error(writer, 400, "invalid_json", str(exc))
            return None
        if not isinstance(payload, dict):
            await _send_diag_error(writer, 400, "invalid_body", "JSON object required")
            return None
        return payload

    async def _handle_bridge(
        self,
        method: str,
        writer: asyncio.StreamWriter,
    ) -> None:
        """Handle /api/v1/bridge — GET status, POST start, DELETE stop."""
        if method == "GET":
            stats = self.audio_bridge_stats
            body = json.dumps(
                {
                    "running": stats is not None and stats.get("running", False),
                    **(stats or {}),
                },
                separators=(",", ":"),
            ).encode()
            await _send_response(
                writer,
                200,
                "OK",
                body,
                {"Content-Type": "application/json"},
            )
        elif method == "POST":
            try:
                await self.start_audio_bridge()
                body = json.dumps({"status": "started"}, separators=(",", ":")).encode()
                await _send_response(
                    writer,
                    200,
                    "OK",
                    body,
                    {"Content-Type": "application/json"},
                )
            except Exception as exc:
                body = json.dumps(
                    {"error": str(exc)},
                    separators=(",", ":"),
                ).encode()
                await _send_response(
                    writer,
                    500,
                    "Error",
                    body,
                    {"Content-Type": "application/json"},
                )
        elif method == "DELETE":
            await self.stop_audio_bridge()
            body = json.dumps({"status": "stopped"}, separators=(",", ":")).encode()
            await _send_response(
                writer,
                200,
                "OK",
                body,
                {"Content-Type": "application/json"},
            )
        else:
            await _send_response(writer, 405, "Method Not Allowed", b"", {})

    async def _serve_static(self, writer: asyncio.StreamWriter, filename: str) -> None:
        # Prevent path traversal
        static_dir = self._config.static_dir.resolve()
        target = (static_dir / filename).resolve()
        if not str(target).startswith(str(static_dir)):
            await _send_response(writer, 403, "Forbidden", b"Forbidden", {})
            return

        if not target.exists() or not target.is_file():
            await _send_response(writer, 404, "Not Found", b"Not Found", {})
            return

        try:
            body = target.read_bytes()
        except OSError:
            await _send_response(
                writer, 500, "Internal Server Error", b"Read error", {}
            )
            return

        mime, _ = mimetypes.guess_type(str(target))
        ct = mime or "application/octet-stream"
        await _send_response(
            writer,
            200,
            "OK",
            body,
            {
                "Content-Type": ct,
                "Cache-Control": "no-cache, no-store, must-revalidate",
            },
        )

    # ------------------------------------------------------------------
    # WebSocket upgrade + routing
    # ------------------------------------------------------------------

    async def _handle_websocket(
        self,
        reader: asyncio.StreamReader,
        writer: asyncio.StreamWriter,
        path: str,
        headers: dict[str, str],
        query: dict[str, list[str]] | None = None,
    ) -> None:
        # Auth check: accept Bearer header or ?token= query param
        if self._config.auth_token:
            auth_header = headers.get("authorization", "")
            token_param = (query or {}).get("token", [""])[0]
            expected_bearer = f"Bearer {self._config.auth_token}"
            token_bytes = self._config.auth_token.encode("utf-8")
            header_ok = hmac.compare_digest(
                auth_header.encode("utf-8"), expected_bearer.encode("utf-8")
            )
            query_ok = hmac.compare_digest(token_param.encode("utf-8"), token_bytes)
            if not header_ok and not query_ok:
                await _send_response(writer, 401, "Unauthorized", b"Unauthorized", {})
                return

        ws_key = headers.get("sec-websocket-key", "")
        if not ws_key:
            await _send_response(writer, 400, "Bad Request", b"Missing key", {})
            return

        accept = make_accept_key(ws_key)
        # Negotiate permessage-deflate (RFC 7692)
        ext_header = headers.get("sec-websocket-extensions", "")
        deflate_resp = negotiate_deflate(ext_header) if ext_header else None
        ext_line = (
            f"Sec-WebSocket-Extensions: {deflate_resp}\r\n" if deflate_resp else ""
        )
        response = (
            "HTTP/1.1 101 Switching Protocols\r\n"
            "Upgrade: websocket\r\n"
            "Connection: Upgrade\r\n"
            f"Sec-WebSocket-Accept: {accept}\r\n"
            f"{ext_line}"
            "\r\n"
        )
        writer.write(response.encode("ascii"))
        await writer.drain()

        ws = WebSocketConnection(reader, writer, deflate=bool(deflate_resp))
        raw_model = (
            getattr(self._radio, "model", None) if self._radio is not None else None
        )
        model = raw_model if isinstance(raw_model, str) else self._config.radio_model

        if path == "/api/v1/ws":
            handler: Any = ControlHandler(
                ws,
                self._radio,
                __version__,
                model,
                server=self,
                read_only=self._config.read_only,
            )
        elif path == "/api/v1/scope":
            handler = ScopeHandler(ws, self._radio, server=self)
        elif path == "/api/v1/audio-scope":
            if self._audio_fft_scope is None:
                await ws.close(1008, "audio FFT scope not available")
                return
            handler = ScopeHandler(ws, self._radio, server=self, audio_mode=True)
        elif path == "/api/v1/audio":
            handler = AudioHandler(ws, self._radio, self._audio_broadcaster)
        else:
            await ws.close(1008, "unknown channel")
            return

        peer = writer.get_extra_info("peername", ("?", 0))
        ip = str(peer[0])

        # Register with connection manager; evict oldest excess connections
        evicted = self._conn_manager.register(ip, path, ws)
        for old_ws in evicted:
            logger.info(
                "ws: evicting old connection from %s on %s (per-IP limit)", ip, path
            )
            try:
                await old_ws.close(1001, "replaced by newer connection")
            except Exception:
                pass

        logger.info(
            "ws connect: %s %s:%s (active=%d)",
            path,
            peer[0],
            peer[1],
            len(self._client_tasks),
        )
        keepalive = asyncio.create_task(
            ws.keepalive_loop(self._config.keepalive_interval)
        )
        try:
            await handler.run()
        except Exception as exc:
            logger.debug("ws handler error on %s: %s", _redact_token_in_path(path), exc)
        finally:
            keepalive.cancel()
            try:
                await keepalive
            except asyncio.CancelledError:
                pass
            self._conn_manager.unregister(ip, path, ws)
            logger.info(
                "ws disconnect: %s %s:%s (active=%d)",
                path,
                peer[0],
                peer[1],
                len(self._client_tasks) - 1,
            )


# ------------------------------------------------------------------
# HTTP response helper
# ------------------------------------------------------------------


async def _send_json(
    writer: asyncio.StreamWriter,
    body: bytes,
    headers: dict[str, str] | None = None,
    *,
    etag: str | None = None,
) -> None:
    """Send a JSON response with optional gzip and ETag support."""
    extra: dict[str, str] = {"Content-Type": "application/json"}
    if etag:
        if_none_match = (headers or {}).get("if-none-match", "")
        if if_none_match == etag:
            await _send_response(writer, 304, "Not Modified", b"", {"ETag": etag})
            return
        extra["ETag"] = etag
    if len(body) > 1024 and "gzip" in (headers or {}).get("accept-encoding", ""):
        body = _gzip.compress(body, compresslevel=1)
        extra["Content-Encoding"] = "gzip"
        extra["Vary"] = "Accept-Encoding"
    await _send_response(writer, 200, "OK", body, extra)


async def _send_diag_error(
    writer: asyncio.StreamWriter,
    status: int,
    code: str,
    message: str,
    *,
    extra: dict[str, Any] | None = None,
) -> None:
    """Send a structured ``{error, message, ...}`` response for diagnostic endpoints."""
    payload: dict[str, Any] = {"error": code, "message": message}
    if extra:
        for k, v in extra.items():
            if v is not None:
                payload[k] = v
    body = json.dumps(payload, separators=(",", ":")).encode()
    reason_map = {
        400: "Bad Request",
        403: "Forbidden",
        404: "Not Found",
        413: "Content Too Large",
        422: "Unprocessable Entity",
        429: "Too Many Requests",
        500: "Internal Server Error",
        502: "Bad Gateway",
    }
    reason = reason_map.get(status, "Error")
    await _send_response(
        writer, status, reason, body, {"Content-Type": "application/json"}
    )


_SECURITY_HEADERS: dict[str, str] = {
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "no-referrer",
    "Content-Security-Policy": (
        "default-src 'self' ws: wss:; img-src 'self' data:; "
        "style-src 'self' 'unsafe-inline' https://fonts.googleapis.com; "
        "font-src 'self' https://fonts.gstatic.com https://cdn.jsdelivr.net"
    ),
}


async def _send_response(
    writer: asyncio.StreamWriter,
    status: int,
    reason: str,
    body: bytes,
    extra_headers: dict[str, str],
) -> None:
    headers = {
        "Content-Length": str(len(body)),
        **_SECURITY_HEADERS,
        **extra_headers,
    }
    header_lines = "".join(f"{k}: {v}\r\n" for k, v in headers.items())
    response = (f"HTTP/1.1 {status} {reason}\r\n{header_lines}\r\n").encode(
        "ascii"
    ) + body
    writer.write(response)
    await writer.drain()


# ------------------------------------------------------------------
# Convenience entry point
# ------------------------------------------------------------------


async def run_web_server(radio: "Radio | None" = None, **kwargs: Any) -> None:
    """Create a :class:`WebServer` from *kwargs* and run it forever.

    Keyword arguments are forwarded to :class:`WebConfig`.

    Example::

        await run_web_server(radio, host="0.0.0.0", port=8080)
    """
    config = WebConfig(**kwargs)
    server = WebServer(radio, config)
    await server.serve_forever()
