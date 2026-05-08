"""Internal runtime host Protocols (P0 decomposition).

These Protocols describe the attributes and methods that the composed
runtimes (CivRuntime, ControlPhaseRuntime, AudioRecoveryRuntime) expect
from their host (CoreRadio). State remains on the host; runtimes
access it via self._host.

They are not part of the public API.
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, Any, Callable, Protocol

if TYPE_CHECKING:
    from rigplane._bounded_queue import BoundedQueue
    from rigplane._civ_rx import CivRuntime
    from rigplane._state_cache import StateCache
    from rigplane.audio import AudioPacket, AudioStream
    from rigplane.civ import CivEvent, CivRequestTracker
    from rigplane.commander import IcomCommander
    from rigplane.radio_state import RadioState
    from rigplane.scope import ScopeAssembler, ScopeFrame
    from rigplane.transport import IcomTransport
    from rigplane.types import Mode


__all__ = [
    "CivRuntimeHost",
    "ControlPhaseHost",
    "AudioRuntimeHost",
]


class CivRuntimeHost(Protocol):
    """Host interface required by CivRuntime."""

    # CI-V transport and commander
    _civ_transport: "IcomTransport | None"
    _commander: "IcomCommander | None"

    # CI-V request tracking / generation
    _civ_request_tracker: "CivRequestTracker"
    _civ_epoch: int
    _civ_ack_sink_grace: float
    _civ_get_timeout: float

    # CI-V send pacing / sequence numbers
    _civ_send_seq: int
    _last_civ_send_monotonic: float
    _civ_min_interval: float

    # CI-V RX pump and watchdog tasks
    _civ_rx_task: "asyncio.Task[None] | None"
    _civ_data_watchdog_task: "asyncio.Task[None] | None"

    # CI-V health / recovery state
    _civ_stream_ready: bool
    _civ_recovering: bool
    _civ_recovery_lock: asyncio.Lock
    _civ_recovery_wait_timeout: float

    # CI-V watchdog / last data timestamps
    _last_civ_data_received: "float | None"
    _civ_waiter_ttl_gc_interval: float
    _civ_last_waiter_gc_monotonic: float

    # Scope and CI-V event queues
    _scope_assembler: "ScopeAssembler"
    _scope_frame_queue: "BoundedQueue[ScopeFrame]"
    _scope_callback: "Callable[[ScopeFrame], Any] | None"
    _scope_activity_counter: int
    _scope_activity_event: asyncio.Event
    _civ_event_queue: "BoundedQueue[CivEvent]"

    # State cache / last-known values
    _state_cache: "StateCache"
    _last_freq_hz: "int | None"
    _last_mode: "Mode | None"
    _filter_width: "int | None"
    _last_vfo: "str | None"
    _civ_retry_slice_timeout: float
    _radio_addr: int

    # Global radio state and callbacks
    _radio_state: "RadioState"
    _on_state_change: "Callable[[str, dict[str, Any]], None] | None"

    # Connection flags used by helpers
    _connected: bool

    # Helpers provided by the host
    async def disconnect(self) -> None: ...

    async def connect(self) -> None: ...

    async def soft_reconnect(self) -> None: ...

    async def _force_cleanup_civ(self) -> None: ...

    async def _send_open_close(self, *, open_stream: bool) -> None: ...


class ControlPhaseHost(Protocol):
    """Host interface required by ``ControlPhaseRuntime``."""

    # Basic connection configuration
    _host: str
    _port: int
    _username: str
    _password: str

    # Connection state and flags
    _conn_state: Any
    _has_connected_once: bool
    _auto_reconnect: bool

    # Control and CI-V transports
    _ctrl_transport: "IcomTransport"
    _civ_transport: "IcomTransport | None"
    _audio_transport: "IcomTransport | None"

    # Ports and local bind ports
    _civ_port: int
    _audio_port: int
    _civ_local_port: int
    _audio_local_port: int
    _local_bind_host: str | None

    # Auth / token state
    _token: int
    _tok_request: int
    _auth_seq: int
    _audio_codec: Any
    _audio_tx_codec: Any
    _audio_sample_rate: int
    _audio_rx_sample_rate: int
    _audio_tx_sample_rate: int
    _audio_stream_request: Any
    _audio_stream_contract: Any

    # Status / error tracking
    _last_status_error: int
    _last_status_disconnected: bool

    # Background tasks
    _token_task: "asyncio.Task[None] | None"
    _watchdog_task: "asyncio.Task[None] | None"
    _reconnect_task: "asyncio.Task[None] | None"

    # Timers and constants
    _WATCHDOG_HEALTH_LOG_INTERVAL: float
    _STATUS_RETRY_PAUSE: float
    _STATUS_REJECT_COOLDOWN: float
    TOKEN_RENEWAL_INTERVAL: float
    TOKEN_PACKET_SIZE: int

    # CI-V flags shared with CivRuntime
    _civ_stream_ready: bool
    _civ_recovering: bool
    _civ_last_waiter_gc_monotonic: float
    _last_civ_data_received: "float | None"

    # Composed CI-V runtime (host delegates to it for pump/watchdog/worker)
    _civ_runtime: "CivRuntime"

    # CI-V send sequence numbers (used by open/close)
    _civ_send_seq: int
    _audio_send_seq: int

    # Scope assembler (shared with CivRuntimeHost; used to set shed callback)
    _scope_assembler: "ScopeAssembler"

    # Pre-bound sockets for CI-V and audio (set during connect, consumed by open)
    _civ_sock_pending: Any  # socket.socket | None
    _audio_sock_pending: Any  # socket.socket | None

    # Optional: audio stream and callbacks (for disconnect cleanup)
    _audio_stream: Any  # AudioStream | None
    _pcm_tx_fmt: Any  # tuple[int, int, int] | None
    _pcm_rx_user_callback: Any
    _opus_rx_user_callback: Any

    # Optional callback after soft_reconnect
    _on_reconnect: "Callable[[], None] | None"

    # Control-phase internal helpers (host methods called by runtime/mixin)
    def _start_token_renewal(self) -> None: ...

    def _stop_token_renewal(self) -> None: ...

    def _start_watchdog(self) -> None: ...

    def _stop_watchdog(self) -> None: ...

    def _stop_reconnect(self) -> None: ...

    async def _watchdog_loop(self) -> None: ...

    async def _wait_for_packet(
        self, transport: "IcomTransport", *, size: int, label: str
    ) -> bytes: ...

    async def _send_token_ack(self) -> None: ...

    async def _receive_guid(self) -> "bytes | None": ...

    async def _send_conninfo(
        self,
        guid: "bytes | None",
        civ_local_port: int = 0,
        audio_local_port: int = 0,
    ) -> None: ...

    async def _receive_civ_port(self) -> int: ...

    def _status_retry_pause(self) -> float: ...

    async def _send_open_close(self, *, open_stream: bool) -> None: ...

    def _advance_civ_generation(self, reason: str) -> None: ...

    def _start_civ_rx_pump(self) -> None: ...

    def _start_civ_data_watchdog(self) -> None: ...

    async def _stop_civ_data_watchdog(self) -> None: ...

    def _start_audio_watchdog(self) -> None: ...

    async def _stop_audio_watchdog(self) -> None: ...

    async def _stop_civ_worker(self) -> None: ...

    async def _stop_civ_rx_pump(self) -> None: ...

    async def disconnect(self) -> None: ...

    def _start_civ_worker(self) -> None: ...

    async def _token_renewal_loop(self) -> None: ...

    async def _send_token(self, magic: int) -> None: ...

    async def _flush_queue(
        self, transport: "IcomTransport", max_pkts: int = 200
    ) -> int: ...

    async def _send_open_close_on_transport(
        self,
        transport: "IcomTransport",
        *,
        send_seq: int,
        open_stream: bool,
    ) -> None: ...


class AudioRuntimeHost(Protocol):
    """Host interface required by AudioRecoveryRuntime."""

    # Audio transports and stream
    _audio_transport: "IcomTransport | None"
    _audio_stream: "AudioStream | None"

    # PCM / Opus callbacks and state
    _pcm_rx_user_callback: "Callable[[bytes | None], None] | None"
    _opus_rx_user_callback: "Callable[[AudioPacket | None], None] | None"
    _pcm_tx_fmt: "tuple[int, int, int] | None"
    _pcm_transcoder_fmt: "tuple[int, int, int] | None"
    _pcm_rx_jitter_depth: int
    _opus_rx_jitter_depth: int

    # Auto-recovery configuration and callback
    _auto_recover_audio: bool
    _on_audio_recovery: "Callable[[Any], None] | None"

    # Audio snapshot helpers expect AudioState enum for .state
    async def start_audio_rx_pcm(
        self,
        callback: "Callable[[bytes | None], None]",
        *,
        sample_rate: int,
        channels: int,
        frame_ms: int,
        jitter_depth: int,
    ) -> None: ...

    async def start_audio_rx_opus(
        self,
        callback: "Callable[[AudioPacket | None], None]",
        *,
        jitter_depth: int,
    ) -> None: ...

    async def start_audio_tx_pcm(
        self,
        *,
        sample_rate: int,
        channels: int,
        frame_ms: int,
    ) -> None: ...

    async def start_audio_tx_opus(self) -> None: ...
