# mypy: disable-error-code="no-any-return,misc,assignment"
"""IcomRadio — high-level async API for Icom transceivers over LAN.

Usage::

    async with IcomRadio("192.168.1.100", username="u", password="p") as radio:
        freq = await radio.get_freq()
        print(f"Freq: {freq / 1e6:.3f} MHz")
        await radio.set_freq(7_074_000)
        await radio.set_mode("USB")
"""

from __future__ import annotations

import asyncio
import logging
import os
import socket as _socket
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Literal, Protocol, cast, runtime_checkable

if TYPE_CHECKING:
    from typing import Any, Awaitable, Callable

    from rigplane._runtime_protocols import ControlPhaseHost
    from rigplane.core.acquisition_scheduler import RadioStateModelService
    from rigplane.core.radio_protocol import ManagedTxSupervisor
    from rigplane.core.tx_safety import ProviderPttObservation, TxSafetySnapshot

    def _managed_tx_runtime_satisfies_supervisor(
        runtime: ManagedRadioRuntime,
    ) -> ManagedTxSupervisor:
        """Mypy-only: fails to type-check if ``ManagedRadioRuntime`` drifts
        from :class:`~rigplane.core.radio_protocol.ManagedTxSupervisor`.

        Guarded by ``TYPE_CHECKING`` — never defined and never called at
        runtime, so it costs nothing now that :meth:`CoreRadio._arm_managed_tx`
        builds ``self._managed_tx_runtime`` (MOR-1016). Its only job is to turn a
        ``request_on``/``release_owner`` signature drift on either side into
        a ``uv run mypy src/`` error here, since the assembly-time
        ``getattr_static`` two-step (:meth:`ManagedTxApi.bind`) only checks
        member presence, never shape.
        """
        return runtime


from . import radio_initial_state as _initial_state
from . import radio_reconnect as _reconnect
from . import radio_state_snapshot as _state_snapshot
from rigplane.runtime._audio_recovery import AudioRecoveryRuntime, AudioRecoveryState
from rigplane.runtime._audio_runtime_mixin import AudioRuntimeMixin
from rigplane.audio._transcoder import PcmOpusTranscoder
from rigplane.audio.route import (
    AudioStreamContract,
    AudioStreamRequest,
    audio_stream_contract_from_request,
    resolve_lan_audio_stream_request,
)
from rigplane.core._bounded_queue import BoundedQueue
from rigplane.runtime._civ_rx import (
    CivRuntime,
    RawCivExpectation,
    RawCivTransactionResult,
)
from rigplane.runtime._dual_rx_runtime import DualRxRuntimeMixin
from rigplane.runtime._scope_runtime import ScopeRuntimeMixin
from rigplane.runtime.callable_support import supports_callable
from rigplane.runtime.managed_radio_runtime import ManagedRadioRuntime
from rigplane.runtime.managed_tx_authority import ManagedTxAuthority, ShutdownResult
from rigplane.runtime.managed_tx_config import ManagedTxTotConfigStore
from rigplane.runtime.managed_tx_effect_lane import (
    ManagedTxActuator,
    ManagedTxEffectLane,
)
from rigplane.runtime.managed_tx_effect_service import managed_tx_effect_service
from rigplane.runtime.managed_tx_fence import TxAbortFence
from rigplane.runtime.local_tx_work import LocalTxWorkRunner
from rigplane.runtime.managed_tx_state import (
    AbortOperation,
    ActuationOperation,
    ActuationResult,
    EffectToken,
)

# Import split modules
from rigplane.runtime._connection_state import RadioConnectionState
from rigplane.runtime._control_phase import (
    CONNINFO_SIZE,  # noqa: F401 (re-export for tests)
    OPENCLOSE_SIZE,  # noqa: F401 (re-export for tests)
    STATUS_SIZE,  # noqa: F401 (re-export for tests)
    TOKEN_ACK_SIZE,  # noqa: F401 (re-export for tests)
    ControlPhaseRuntime,
    ControlPhaseSessionMechanism,
)
from rigplane.runtime.session_lifecycle import CoreRadioSessionLifecycle
from rigplane.audio import AudioPacket, AudioStream
from rigplane.core.civ import CivEvent, CivRequestTracker
from rigplane.commands.commander import IcomCommander, Priority
from rigplane.commands import (
    _SUB_REPEATER_TONE,
    _SUB_REPEATER_TSQL,
    CONTROLLER_ADDR,
    RECEIVER_MAIN,
    _level_bcd_decode,
    bcd_encode_value,
    build_civ_frame,
    filter_hz_to_index,
    filter_index_to_hz,
    get_acc1_mod_level,
    get_af_mute,
    get_agc,
    get_agc_time_constant,
    get_anti_vox_gain,
    get_apf_type_level,
    get_audio_peak_filter,
    get_auto_notch,
    get_break_in,
    get_break_in_delay,
    get_civ_output_ant,
    get_civ_transceive,
    get_compressor,
    get_compressor_level,
    get_cw_pitch,
    get_dash_ratio,
    get_data1_mod_input,
    get_data2_mod_input,
    get_data3_mod_input,
    get_data_off_mod_input,
    get_dial_lock,
    get_digisel_shift,
    get_drive_gain,
    get_dual_watch,
    get_filter_shape,
    get_key_speed,
    get_lan_mod_level,
    get_manual_notch,
    get_manual_notch_width,
    get_mic_gain,
    get_monitor,
    get_monitor_gain,
    get_nb_depth,
    get_nb_level,
    get_nb_width,
    get_notch_filter,
    get_nr_level,
    get_overflow_status,
    get_pbt_inner,
    get_pbt_outer,
    get_quick_dual_watch,
    get_quick_split,
    get_ref_adjust,
    get_s_meter_sql_status,
    get_squelch,
    get_ssb_tx_bandwidth,
    get_system_date,
    get_system_time,
    get_twin_peak_filter,
    get_usb_mod_level,
    get_utc_offset,
    get_various_squelch,
    get_vox,
    get_vox_delay,
    get_vox_gain,
    parse_ack_nak,
    parse_band_stack_response,
    parse_bool_response,
    parse_data_mode_response,
    parse_frequency_response,
    parse_level_response,
    parse_meter_response,
    parse_rit_frequency_response,
    parse_system_date_response,
    parse_system_time_response,
    parse_tone_freq_response,
    parse_tsql_freq_response,
    parse_utc_offset_response,
    parse_powerstat,
)
from rigplane.commands import get_main_sub_tracking as _get_main_sub_tracking_cmd
from rigplane.commands import get_repeater_tone as _get_repeater_tone_cmd
from rigplane.commands import get_repeater_tsql as _get_repeater_tsql_cmd
from rigplane.core.env_config import get_managed_tx_enabled
from rigplane.core.exceptions import CommandError, TimeoutError
from rigplane.core.state_store import StateStore
from rigplane.core.tx_observation import (
    RADIO_READBACK_SOURCES,
    TX_READ_DEADLINE_SECONDS,
    TxStateReading,
)
from rigplane.core.tx_safety import TxOutcome
from rigplane.runtime.meter_cal import interpolate_swr
from rigplane.commands.bound import BoundCommands
from rigplane.commands.command_map import CommandMap
from rigplane.profiles import RadioProfile, resolve_radio_profile
from rigplane.core.radio_state import RadioState
from rigplane.core.state_diagnostics import StateDiagnosticsRecorder
from rigplane.core._state_cache import StateCache
from rigplane.scope import ScopeAssembler, ScopeFrame
from rigplane.core.transport import IcomTransport
from rigplane.core.types import (
    AgcMode,
    AudioCodec,
    AudioPeakFilter,
    BandStackRegister,
    BreakInMode,
    CivFrame,
    FilterShape,
    MemoryChannel,
    Mode,
    ScopeCompletionPolicy,
    SsbTxBandwidth,
    get_audio_capabilities,
)

__all__ = [
    "AudioRecoveryState",
    "CoreRadio",
    "IcomRadio",
    "ManagedTxComposition",
    "ManagedTxCompositionPort",
    "ManagedTxProviderEvent",
    "install_managed_tx_composition",
    "RawCivSubscription",
    "RadioProfile",
    "AudioCodec",
    "RadioConnectionState",
    "ScopeFrame",
    "ScopeCompletionPolicy",
]


logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class ManagedTxProviderEvent:
    """One provider lifecycle event shared by TX and observation consumers."""

    provider_generation: int
    observation_generation: int

    def __post_init__(self) -> None:
        if self.provider_generation < 0 or self.observation_generation < 0:
            raise ValueError("managed TX generations must be non-negative")


_ProviderHook = Callable[[ManagedTxProviderEvent], Awaitable[None]]


@runtime_checkable
class ManagedTxCompositionPort(Protocol):
    """Typed production seat shared by Web, rigctld, and provider lifecycle."""

    @property
    def authority(self) -> ManagedTxAuthority: ...

    @property
    def abort_fence(self) -> TxAbortFence: ...

    @property
    def local_tx_work_runner(self) -> LocalTxWorkRunner: ...

    @property
    def active_provider(self) -> ManagedTxProviderEvent | None: ...

    async def activate_provider(self, event: ManagedTxProviderEvent) -> None: ...

    def start_provider_unavailable(
        self, event: ManagedTxProviderEvent
    ) -> asyncio.Task[None]: ...

    async def shutdown(self, termination: asyncio.Event) -> ShutdownResult: ...


class _SynchronousProviderInvalidation(Protocol):
    def start_provider_unavailable(self) -> asyncio.Task[None]: ...


async def _no_provider_hook(_event: ManagedTxProviderEvent) -> None:
    return None


class ManagedTxComposition:
    """The sole production graph for managed-transmit policy and effects."""

    def __init__(
        self,
        actuator: ManagedTxActuator,
        *,
        config_path: Path,
        prepare_provider: _ProviderHook = _no_provider_hook,
        retire_provider: _ProviderHook = _no_provider_hook,
    ) -> None:
        if not isinstance(actuator, ManagedTxActuator):
            raise TypeError("production managed TX requires a normalized actuator")
        self._prepare_provider = prepare_provider
        self._retire_provider = retire_provider
        self._abort_fence = TxAbortFence()
        self._local_tx_work_runner = LocalTxWorkRunner(self._abort_fence)
        self._config_store = ManagedTxTotConfigStore(config_path)
        self._active_provider: ManagedTxProviderEvent | None = None
        self._events: dict[int, ManagedTxProviderEvent] = {}
        self._invalidation_tasks: dict[int, asyncio.Task[None]] = {}
        self._transition_lock = asyncio.Lock()
        self._shutting_down = False
        self._shutdown_task: asyncio.Task[ShutdownResult] | None = None

        async def poison_generation(generation: int) -> None:
            event = self._events.get(generation)
            if event is not None:
                await self.start_provider_unavailable(event)

        self._lane = ManagedTxEffectLane(
            actuator,
            poison_generation=poison_generation,
        )
        self._authority = ManagedTxAuthority(
            self._lane,
            self._config_store,
            self._abort_fence,
            provider_generation=None,
        )

    @property
    def authority(self) -> ManagedTxAuthority:
        return self._authority

    @property
    def abort_fence(self) -> TxAbortFence:
        return self._abort_fence

    @property
    def local_tx_work_runner(self) -> LocalTxWorkRunner:
        return self._local_tx_work_runner

    @property
    def active_provider(self) -> ManagedTxProviderEvent | None:
        return self._active_provider

    async def activate_provider(self, event: ManagedTxProviderEvent) -> None:
        async with self._transition_lock:
            if self._shutting_down:
                raise RuntimeError("managed TX composition is shutting down")
            if not callable(
                getattr(self._authority, "start_provider_unavailable", None)
            ):
                raise RuntimeError(
                    "managed TX authority lacks synchronous provider invalidation"
                )
            current = self._active_provider
            if current == event:
                return
            if current is not None:
                raise RuntimeError("current provider must become unavailable first")
            if self._events and event.provider_generation <= max(self._events):
                raise ValueError("provider generation must increase")
            if event.provider_generation in self._events:
                raise ValueError("provider generation is already bound")
            await self._prepare_provider(event)
            try:
                await self._authority.provider_available(event.provider_generation)
            except BaseException:
                await self._retire_provider(event)
                raise
            self._events[event.provider_generation] = event
            self._active_provider = event

    def start_provider_unavailable(
        self, event: ManagedTxProviderEvent
    ) -> asyncio.Task[None]:
        existing = self._invalidation_tasks.get(event.provider_generation)
        if existing is not None:
            return existing
        if self._active_provider != event:
            return asyncio.create_task(_no_provider_hook(event))
        starter = getattr(self._authority, "start_provider_unavailable", None)
        if not callable(starter):
            raise RuntimeError(
                "managed TX authority lacks synchronous provider invalidation"
            )
        authority = cast(_SynchronousProviderInvalidation, self._authority)
        authority_cleanup = authority.start_provider_unavailable()
        self._active_provider = None

        async def finish() -> None:
            await authority_cleanup
            await self._retire_provider(event)

        task = asyncio.create_task(finish())
        self._invalidation_tasks[event.provider_generation] = task
        return task

    async def shutdown(self, termination: asyncio.Event) -> ShutdownResult:
        async with self._transition_lock:
            task = self._shutdown_task
            if task is None:
                self._shutting_down = True
                task = asyncio.create_task(self._complete_shutdown(termination))
                self._shutdown_task = task
        return await asyncio.shield(task)

    async def _complete_shutdown(self, termination: asyncio.Event) -> ShutdownResult:
        event = self._active_provider
        if not self._events:
            await self._authority.close()
            return ShutdownResult.DRAINED

        async def retire(generation: int) -> None:
            bound = self._events.get(generation)
            if bound is None:
                raise RuntimeError("managed TX retirement lost provider event")
            await self._retire_provider(bound)

        result = await self._authority.shutdown(
            retire_provider=retire,
            termination=termination,
        )
        if result is ShutdownResult.DRAINED:
            self._active_provider = None
            if event is not None:
                self._invalidation_tasks.pop(event.provider_generation, None)
        return result


class _LegacyManagedTxCutoverBlocker:
    async def request_on(self, _owner: object) -> object:
        raise RuntimeError("legacy PTT ingress is blocked by production composition")

    async def release_owner(self, _owner: object, *, reason: object) -> object:
        raise RuntimeError("legacy PTT ingress is blocked by production composition")


_LEGACY_MANAGED_TX_CUTOVER_BLOCKER = _LegacyManagedTxCutoverBlocker()


def install_managed_tx_composition(
    radio: object, composition: ManagedTxCompositionPort
) -> None:
    """Install before connect, suppressing legacy-owner and raw-PTT fallback."""

    installer = getattr(radio, "install_managed_tx_composition", None)
    if callable(installer):
        installer(composition)
        return
    if getattr(radio, "_managed_tx_composition", None) is not None:
        raise RuntimeError("managed TX composition is already installed")
    setattr(radio, "_managed_tx_composition", composition)
    try:
        setattr(radio, "managed_tx", _LEGACY_MANAGED_TX_CUTOVER_BLOCKER)
    except (AttributeError, TypeError) as exc:
        setattr(radio, "_managed_tx_composition", None)
        raise RuntimeError("radio cannot block legacy managed TX fallback") from exc


_AUDIO_CAPABILITIES = get_audio_capabilities()
_DEFAULT_AUDIO_CODEC = _AUDIO_CAPABILITIES.default_codec
_DEFAULT_AUDIO_SAMPLE_RATE = _AUDIO_CAPABILITIES.default_sample_rate_hz
# Default TTLs (seconds) for the GET-command cache fallback paths.
_DEFAULT_CACHE_TTL: dict[str, float] = {"freq": 10.0, "mode": 10.0, "rf_power": 30.0}

# Threshold for ``Radio.connected`` to treat a UDP transport as unhealthy.
# A single transient ``error_received`` (e.g. EAGAIN/EWOULDBLOCK/Broken pipe)
# should not latch the socket into a disconnected state — the counter is
# cumulative and only the 30s watchdog resets it via ``soft_reconnect``.
# Require >=3 accumulated errors before reporting ``connected = False``.
_UDP_ERROR_THRESHOLD: int = 3

# Deadline for the managed-TX arming probe (MOR-1016).  Deliberately shorter
# than ``_civ_get_timeout``: by the time it runs the rig has already answered
# a full initial-state fetch, so a PTT read that does not come back promptly
# means the command is unsupported rather than merely slow — and the whole
# point of the probe is to settle that *without* holding up ``connect()``.
# Being wrong here costs supervised TX for one epoch, never the session.
_MANAGED_TX_PROBE_TIMEOUT_S: float = 0.5

# Deadline for the managed-TX teardown waits (MOR-1016): the shutdown that
# carries a held lease's OFF out through a still-open CI-V path, and the
# provider-park that shuts the gate ahead of a soft_disconnect.  Deliberately
# longer than ``ManagedRadioRuntime``'s own 3 s effect-service bound, which is
# what the durable OFF and its retries actually run under: an outer wait that
# expired first would cut the release short rather than bound a wedged one, and
# the remaining headroom covers ticker cancellation and port retirement.
_MANAGED_TX_TEARDOWN_TIMEOUT_S: float = 5.0


async def _managed_tx_provider_released_by_disconnect() -> None:
    """``ManagedRadioRuntime.shutdown``'s release hook, deliberately empty.

    The hook exists for an owner whose provider outlives the runtime — a server
    handing a shared rig back. ``disconnect()`` is the other case: the shutdown
    has already retired the managed CI-V port by the time this runs, and the
    session under it is released on the very next line, so there is nothing
    left here to hand back. Clearing the radio's own managed-TX members is
    deliberately *not* done here: the shutdown task is shielded, so on the
    bounded path this may run long after ``disconnect()`` returned.
    """
    return None


class RawCivSubscription:
    """Handle for a raw CI-V listener registered via ``add_raw_civ_listener``.

    Part of the raw CI-V pipe seam (MOR-164) that lets a transparent Hamlib A1
    bridge use RigPlane purely as a CI-V byte transport. Call :meth:`close` (or
    use the handle as a context manager) to unregister the listener.
    """

    def __init__(
        self,
        registry: list[Callable[[bytes], Any]],
        callback: Callable[[bytes], Any],
    ) -> None:
        self._registry = registry
        self._callback = callback
        self._closed = False

    def close(self) -> None:
        """Unregister the listener. Idempotent."""
        if self._closed:
            return
        self._closed = True
        try:
            self._registry.remove(self._callback)
        except ValueError:
            pass

    def __enter__(self) -> "RawCivSubscription":
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()


class CoreRadio(ScopeRuntimeMixin, AudioRuntimeMixin, DualRxRuntimeMixin):
    """High-level async interface for controlling an Icom transceiver over LAN.

    Manages two UDP connections:
    - Control port (default 50001): authentication and session management.
    - CI-V port (default 50002): CI-V command exchange.

    Args:
        host: Radio IP address or hostname.
        port: Radio control port.
        username: Authentication username.
        password: Authentication password.
        radio_addr: Optional CI-V address override. If omitted, uses
            the resolved profile default.
        timeout: Default timeout for operations in seconds.

    Example::

        async with IcomRadio("192.168.1.100", username="u", password="p") as radio:
            freq = await radio.get_freq()
            await radio.set_freq(7_074_000)
    """

    # Watchdog timing (used by _watchdog_loop)
    WATCHDOG_CHECK_INTERVAL = 0.5
    _WATCHDOG_HEALTH_LOG_INTERVAL = 30.0

    # PowerControlCapable: Icom CI-V uses a raw 0-255 scale on the wire
    # (cmd 0x14 0x0A). Inspected by upper layers to decide unit
    # translation before queueing SetPower. See
    # :class:`rigplane.core.radio_protocol.PowerControlCapable`.
    native_power_unit: Literal["raw_255", "watts"] = "raw_255"

    def _log_undeclared_command(self, name: str) -> None:
        """D1 state 3's WARNING (plan §4 Step 4 / §8.1): *name* is neither
        declared nor declared absent. `commands/bound.py: BoundCommands`
        calls this hook right before it refuses -- `commands/` performs no
        logging itself (`commands/LAYER.md`).
        """
        logger.warning(
            "%s is not declared by profile %s and not recorded as absent "
            "-- refusing rather than guessing "
            "(docs/plans/2026-08-29-profile-driven-command-bytes.md §8.1 D1)",
            name,
            self._profile.model,
        )

    def supports_command(self, command: str, *, receiver: int | None = None) -> bool:
        """Return support derived from this radio's profile and call graph."""
        supported = supports_callable(self._profile, command)
        if receiver is None:
            return supported
        if (
            not supported
            or command
            not in {
                "set_af_level",
                "set_rf_gain",
                "set_squelch",
                "set_attenuator_level",
            }
            or isinstance(receiver, bool)
            or not isinstance(receiver, int)
            or not self._profile.supports_receiver(receiver)
            or not callable(getattr(self, command, None))
            or not self._profile.supports_capability(
                "attenuator"
                if command == "set_attenuator_level"
                else command.removeprefix("set_")
            )
        ):
            return False
        try:
            if command == "set_attenuator_level":
                command_map = self._profile.command_map
                if (
                    not self._profile.att_values
                    or command_map is None
                    or not command_map.has("set_attenuator")
                ):
                    return False
                self._require_cmd29_route(
                    0x11, None, receiver=receiver, operation=command
                )
                return True
            return self._level_command29(command, receiver=receiver) is not None
        except CommandError:
            return False

    def _level_command29(self, command: str, *, receiver: int) -> bool | None:
        """Resolve the wrapper flag; leave missing-command refusal to the builder."""
        command_map = self._profile.command_map
        if command_map is None or not command_map.has(command):
            return None
        from rigplane.commands._frame import decode_wire_tuple

        opcode, sub, _ = decode_wire_tuple(command_map.get(command))
        self._require_cmd29_route(opcode, sub, receiver=receiver, operation=command)
        return self._profile.supports_cmd29(opcode, sub)

    def _stop_token_renewal(self) -> None:
        """Delegate to control-phase runtime."""
        self._control_phase._stop_token_renewal()

    def __init__(
        self,
        host: str,
        port: int = 50001,
        username: str = "",
        password: str = "",
        radio_addr: int | None = None,
        timeout: float = 5.0,
        audio_codec: AudioCodec | int = _DEFAULT_AUDIO_CODEC,
        audio_sample_rate: int | None = None,
        audio_codec_explicit: bool | None = None,
        audio_sample_rate_explicit: bool | None = None,
        auto_reconnect: bool = False,
        reconnect_delay: float = 2.0,
        reconnect_max_delay: float = 60.0,
        watchdog_timeout: float = 30.0,
        auto_recover_audio: bool = True,
        on_audio_recovery: "Callable[[AudioRecoveryState], None] | None" = None,
        cache_ttl_s: "dict[str, float] | None" = None,
        profile: RadioProfile | str | None = None,
        model: str | None = None,
    ) -> None:
        self._host = host
        self._port = port
        self._username = username
        self._password = password
        if radio_addr is not None and not (0 <= radio_addr <= 0xFF):
            raise ValueError("radio_addr must be a single byte (0..255).")
        self._timeout = timeout
        self._audio_codec = AudioCodec(audio_codec)
        self._audio_tx_codec = AudioCodec.PCM_1CH_16BIT
        requested_sample_rate = (
            _DEFAULT_AUDIO_SAMPLE_RATE
            if audio_sample_rate is None
            else audio_sample_rate
        )
        self._audio_sample_rate = requested_sample_rate
        self._audio_rx_sample_rate = requested_sample_rate
        self._audio_tx_sample_rate = requested_sample_rate
        self._audio_stream_request: AudioStreamRequest | None = None
        self._audio_stream_contract: AudioStreamContract | None = None
        self._ctrl_transport = IcomTransport()
        self._civ_transport: IcomTransport | None = None
        self._audio_transport: IcomTransport | None = None
        self._audio_stream: AudioStream | None = None
        self._pcm_transcoder: PcmOpusTranscoder | None = None
        self._pcm_transcoder_fmt: tuple[int, int, int] | None = None
        self._pcm_tx_fmt: tuple[int, int, int] | None = None
        self._conn_state = RadioConnectionState.DISCONNECTED
        self._token: int = 0
        self._tok_request: int = 0
        self._auth_seq: int = 0
        self._civ_port: int = 0
        self._audio_port: int = 0
        self._local_bind_host: str | None = None
        self._civ_sock_pending: _socket.socket | None = None
        self._audio_sock_pending: _socket.socket | None = None
        self._civ_send_seq: int = 0
        self._audio_send_seq: int = 0
        self._last_civ_send_monotonic: float = 0.0
        self._civ_min_interval: float = (
            float(os.environ.get("ICOM_CIV_MIN_INTERVAL_MS", "35")) / 1000.0
        )
        self._commander: IcomCommander | None = None
        self._filter_width: int | None = None
        self._attenuator_state: bool | None = None
        self._preamp_level: int | None = None
        self._last_freq_hz: int | None = None
        self._last_mode: Mode | None = None
        self._last_power: int | None = None
        self._last_split: bool | None = None
        self._last_vfo: str | None = None
        self._token_task: asyncio.Task[None] | None = None
        self._auto_reconnect = auto_reconnect
        self._reconnect_delay = reconnect_delay
        self._reconnect_max_delay = reconnect_max_delay
        self._watchdog_timeout = watchdog_timeout
        self._watchdog_task: asyncio.Task[None] | None = None
        self._reconnect_task: asyncio.Task[None] | None = None
        self._auto_recover_audio = auto_recover_audio
        self._on_audio_recovery = on_audio_recovery
        self._on_reconnect: Callable[[], None] | None = None
        self._on_reconnect_status: Callable[[dict[str, Any]], None] | None = None
        self._civ_stream_ready: bool = False
        self._civ_recovering: bool = False
        self._last_civ_data_received: float | None = None
        self._civ_recovery_lock = asyncio.Lock()
        self._civ_recovery_wait_timeout: float = float(
            os.environ.get("ICOM_CIV_RECOVERY_WAIT_TIMEOUT_S", "12.0")
        )
        self._civ_ready_idle_timeout: float = float(
            os.environ.get("ICOM_CIV_READY_IDLE_TIMEOUT_S", "5.0")
        )
        self._pcm_rx_user_callback: Callable[[bytes | None], None] | None = None
        self._pcm_rx_jitter_depth: int = 5
        self._opus_rx_user_callback: Callable[[AudioPacket | None], None] | None = None
        self._opus_rx_jitter_depth: int = 5
        # AudioBus — lazy-initialized pub/sub for multi-consumer audio
        self._audio_bus: Any = None
        # AudioSession — lazy-initialized radio-owned singleton (MOR-579)
        self._audio_session: Any = None
        self._scope_assembler: ScopeAssembler = ScopeAssembler()
        self._scope_callback: Callable[[ScopeFrame], Any] | None = None
        # MOR-2222: last scope-display mode published from the waveform
        # stream, per receiver (0=MAIN, 1=SUB) — lets the stream
        # change-detect before writing to the StateStore.
        self._scope_stream_last_mode: dict[int, int] = {}
        # MOR-2256: last scope-display span index published from the
        # waveform stream (center mode only), per receiver -- same
        # change-detect purpose as _scope_stream_last_mode above.
        self._scope_stream_last_span: dict[int, int] = {}
        # Raw CI-V pipe listeners (MOR-164): receive inbound on-wire frame bytes.
        self._raw_civ_listeners: list[Callable[[bytes], Any]] = []
        # External CAT-session ownership (MOR-166 slice 2): when True, cooperating
        # pollers pause so they do not pollute an external master's byte stream.
        self._external_cat_session: bool = False
        self._external_cat_session_owner: str | None = None
        self._civ_rx_task: asyncio.Task[None] | None = None
        self._civ_data_watchdog_task: asyncio.Task[None] | None = None
        self._audio_watchdog_task: asyncio.Task[None] | None = None
        self._civ_request_tracker = CivRequestTracker()
        self._civ_epoch = self._civ_request_tracker.generation
        self._scope_frame_queue: BoundedQueue[ScopeFrame] = BoundedQueue(maxsize=64)
        self._scope_activity_counter: int = 0
        self._scope_activity_event = asyncio.Event()
        self._civ_event_queue: BoundedQueue[CivEvent] = BoundedQueue(maxsize=256)
        self._civ_ack_sink_grace: float = (
            float(os.environ.get("ICOM_CIV_ACK_SINK_GRACE_MS", "120")) / 1000.0
        )
        self._civ_waiter_ttl_gc_interval: float = 1.0
        self._civ_last_waiter_gc_monotonic: float = time.monotonic()
        self._civ_retry_slice_timeout: float = (
            float(os.environ.get("ICOM_CIV_RETRY_SLICE_MS", "150")) / 1000.0
        )
        self._state_cache: StateCache = StateCache()
        # Canonical state-ingress store exposed via ``state_store``. It is
        # constructed bare here and does NOT decay on its own (MOR-432):
        # freshness aging requires a StateFreshnessService wired over this
        # store and driven by a running loop. In production the web/rigctld
        # server wires and runs that service against this same store at
        # startup. Used headless without a server, this store never ages
        # fields to STALE.
        self._state_store: StateStore = StateStore()
        self._state_model_service: RadioStateModelService | None = None
        self._state_diagnostics: StateDiagnosticsRecorder | None = None
        self._on_state_change: Callable[[str, dict[str, Any]], None] | None = (
            None  # set by server
        )
        self._radio_state: RadioState = RadioState()  # may be replaced by WebServer
        _ttl = {**_DEFAULT_CACHE_TTL, **(cache_ttl_s or {})}
        self._cache_ttl_freq: float = _ttl["freq"]
        self._cache_ttl_mode: float = _ttl["mode"]
        self._cache_ttl_rf_power: float = _ttl["rf_power"]
        self._profile = resolve_radio_profile(
            profile=profile,
            model=model,
            radio_addr=radio_addr,
        )
        # Bound once, at construction (MOR-2003 Step 3,
        # docs/plans/2026-08-29-profile-driven-command-bytes.md §3.1/§4). A
        # hand-built profile with no ``command_map`` (e.g. a test fixture
        # constructed outside ``profiles/rig_loader.py``) and a loaded
        # profile that declares no CI-V commands both bind an empty
        # ``CommandMap`` here rather than raising -- construction must
        # never fail on this. As of Steps 5..N module 1
        # (`commands/config.py`, MOR-2006), the mod-level/mod-input/CI-V
        # option getters and setters below are the first call sites to
        # read ``self._commands``; the rest still migrate module by
        # module.
        #
        # ``absent_command_sources``/``on_undeclared`` implement D1's
        # undeclared-command policy (step 4b, plan §4 Step 4 / §8.1): this
        # reads ``RadioProfile.absent_command_sources`` (plain data) and
        # hands it, plus the logging side effect state 3 needs, to
        # `commands/bound.py: BoundCommands` -- which imports nothing from
        # `profiles` and only ever calls the hook it is given, never
        # `logging` directly (`commands/LAYER.md` bans I/O in `commands/`).
        self._commands = BoundCommands(
            self._profile.command_map or CommandMap({}),
            self._profile.absent_command_sources,
            on_undeclared=self._log_undeclared_command,
        )
        # Apply per-profile codec preference override (#797) — only if caller
        # accepted the global default. An explicit non-default value always wins.
        # Limitation kept for compatibility with the historical constructor:
        # passing the global default codec value is indistinguishable from
        # omitting it, so profile codec preference may still apply in that case.
        codec_is_explicit = (
            audio_codec_explicit is True
            or AudioCodec(audio_codec) != _DEFAULT_AUDIO_CODEC
        )
        sample_rate_is_explicit = (
            audio_sample_rate_explicit is True
            or (audio_sample_rate_explicit is None and audio_sample_rate is not None)
            or "ICOM_AUDIO_SAMPLE_RATE" in os.environ
        )
        self._audio_stream_request = resolve_lan_audio_stream_request(
            profile=self._profile,
            requested_rx_codec=audio_codec,
            requested_sample_rate_hz=requested_sample_rate,
            rx_codec_explicit=codec_is_explicit,
            sample_rate_explicit=sample_rate_is_explicit,
        )
        self._audio_stream_contract = audio_stream_contract_from_request(
            self._audio_stream_request
        )
        self._audio_codec = self._audio_stream_contract.rx_codec
        self._audio_tx_codec = self._audio_stream_contract.tx_codec
        self._audio_rx_sample_rate = self._audio_stream_contract.rx_sample_rate_hz
        self._audio_tx_sample_rate = self._audio_stream_contract.tx_sample_rate_hz
        self._audio_sample_rate = self._audio_rx_sample_rate
        self._radio_addr = self._profile.civ_addr if radio_addr is None else radio_addr
        # GET commands use a shorter timeout than the general connection timeout.
        # wfview-style: send once, short deadline, fall back to cache.
        self._civ_get_timeout: float = min(timeout, 2.0)
        # Composed runtimes (P0 decomposition); order: civ first so control_phase can call it.
        self._civ_runtime: CivRuntime = CivRuntime(self)
        self._control_phase: ControlPhaseRuntime = ControlPhaseRuntime(
            cast("ControlPhaseHost", self)
        )
        # Unified session lifecycle (policy layer) over the control-phase packet
        # mechanism.  ``CoreRadio.connect/disconnect/soft_reconnect/scan`` route
        # through this so the guaranteed-release + cooldown-aware-retry policy is
        # owned in one place (design 2026-06-22; task A3).  The resident-retry
        # bound mirrors the removed legacy wrapper (``_DATA_PORT_COOLDOWN_RETRIES
        # + 1`` attempts) so an in-process ``CoreRadio`` still surfaces a
        # ConnectionError on a persistently-unready radio rather than spinning
        # forever; the longer-lived Pro service uses the lifecycle's resident
        # default directly.
        self._session_mechanism: ControlPhaseSessionMechanism = (
            ControlPhaseSessionMechanism(self._control_phase)
        )
        self._session_lifecycle: CoreRadioSessionLifecycle = CoreRadioSessionLifecycle(
            self._session_mechanism,
            max_connect_attempts=4,
            # In-process SDK retry is immediate: the meaningful wait between
            # attempts is the radio's own keepalive window, and the lifecycle
            # already RELEASES the (partial) session before each retry, so no
            # client-side cooldown sleep is needed here.  The within-attempt
            # conninfo busy-retry pacing still lives in ``_connect_once``
            # (``_STATUS_RETRY_PAUSE``).  The longer-lived Pro service constructs
            # the lifecycle with the default 10/30 s cooldowns.
            not_ready_cooldown_s=0.0,
            reject_cooldown_s=0.0,
            # Recovery backoff is ZERO for the in-process CoreRadio (A4): the
            # CI-V data watchdog already spends its patient OpenClose phase
            # (~``_OPENCLOSE_DEADLINE`` = 60 s) BEFORE handing off to this
            # RECOVERING loop, and ``soft_reconnect`` rebuilds the data path
            # within one attempt.  A second 45/60 s lifecycle backoff per attempt
            # would double-count that wait and stall the fast-recovery poller /
            # direct ``soft_reconnect()`` callers.  The lifecycle still owns the
            # attempt COUNT + exhaustion → CLOSING + release (single owner); only
            # the inter-attempt sleep is collapsed.  The longer-lived Pro service
            # constructs the lifecycle with the default ``_RECONNECT_BACKOFF``.
            recovery_backoff_s=(0.0, 0.0, 0.0),
        )
        self._audio_runtime: AudioRecoveryRuntime = AudioRecoveryRuntime(self)
        # Managed TX supervisor (MOR-1016).  Built once, by the first
        # ``connect()`` that reaches ``_arm_managed_tx`` — never here: arming
        # needs a live ``_civ_transport`` to capture, which does not exist
        # until the control phase has run.  From that point on the member is
        # non-None for the life of the radio: a failed arm degrades it to
        # NOT_READY, never back to ``None`` (see ``_arm_managed_tx``).
        self._managed_tx_runtime: ManagedRadioRuntime | None = None
        self._managed_tx_composition: ManagedTxCompositionPort | None = None
        # CI-V epoch the last arming attempt was made against; ``None`` until
        # the first attempt.  Bounds arming to one attempt per epoch.
        self._managed_tx_armed_epoch: int | None = None
        # Identity of the port the runtime is bound to, recorded the moment
        # ``replace_provider`` captured it: (provider generation, CI-V epoch,
        # transport).  Never cleared — every way a binding dies moves one of
        # the three (see ``_managed_tx_binding_is_live``).
        self._managed_tx_bound_port: tuple[int | None, int, object] | None = None
        # Serialises the arming steps, so two callers cannot both find the
        # binding dead and both replace the provider.
        self._managed_tx_arm_lock = asyncio.Lock()

    # Host shims for ControlPhaseRuntime and Icom7610SerialRadio (delegate to civ_runtime)
    def _advance_civ_generation(self, reason: str) -> None:
        self._civ_runtime.advance_generation(reason)

    def _start_civ_rx_pump(self) -> None:
        self._civ_runtime.start_pump()

    async def _stop_civ_rx_pump(self) -> None:
        await self._civ_runtime.stop_pump()

    def _start_civ_data_watchdog(self) -> None:
        self._civ_runtime.start_data_watchdog()

    async def _stop_civ_data_watchdog(self) -> None:
        await self._civ_runtime.stop_data_watchdog()

    def _start_audio_watchdog(self) -> None:
        if self._audio_watchdog_task is None or self._audio_watchdog_task.done():
            self._audio_watchdog_task = asyncio.create_task(
                self._audio_watchdog_loop(), name="audio-error-watchdog"
            )

    async def _stop_audio_watchdog(self) -> None:
        task = self._audio_watchdog_task
        if task is not None and not task.done():
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
        self._audio_watchdog_task = None

    async def _audio_watchdog_loop(self) -> None:
        await _reconnect.audio_error_watchdog_loop(self)

    def _start_civ_worker(self) -> None:
        self._civ_runtime.start_worker()

    async def _stop_civ_worker(self) -> None:
        await self._civ_runtime.stop_worker()

    def __del__(self) -> None:
        """Emit WARN if instance is collected while still connected (forgotten teardown)."""
        try:
            if self._conn_state == RadioConnectionState.CONNECTED:
                logger.warning(
                    "Radio collected with active connection/tasks; "
                    "ensure disconnect() or async with is used."
                )
        except Exception:
            pass  # avoid raising in destructor

    @property
    def conn_state(self) -> RadioConnectionState:
        """Current connection state."""
        return self._conn_state

    @property
    def connected(self) -> bool:
        """Whether the radio is currently connected and CI-V transport is healthy."""
        if self._conn_state != RadioConnectionState.CONNECTED:
            return False
        civ = self._civ_transport
        if civ is None:
            return False
        # Check for UDP errors (only on real IcomTransport, not mocks).
        # A single transient EAGAIN/EWOULDBLOCK should not latch the socket
        # into a "disconnected" state — only treat the transport as unhealthy
        # after repeated errors.  The counter is reset on soft_reconnect and
        # on reset_udp_error_count() after sustained healthy packet receipt.
        error_count = getattr(civ, "_udp_error_count", None)
        if isinstance(error_count, int) and error_count >= _UDP_ERROR_THRESHOLD:
            return False
        return True

    @property
    def control_connected(self) -> bool:
        """Whether the control transport is alive (LAN session active)."""
        ctrl = self._ctrl_transport
        if ctrl is None:
            return False
        return getattr(ctrl, "_udp_transport", None) is not None

    @property
    def remote_control_unreachable(self) -> bool:
        """Whether the radio's host is reachable but its CI-V/remote-control
        network server is not accepting the session.

        After a radio power-cycle the IP comes back up (pings/ARP resolve) but
        the CI-V network server on the control/CI-V UDP port may not be
        listening yet.  On a *connected* UDP socket the kernel surfaces the
        peer's ICMP port-unreachable as ``error_received`` (e.g.
        ``[Errno 32] Broken pipe`` / ``[Errno 111] Connection refused``), which
        increments ``_udp_error_count``.  A truly unreachable host produces no
        ICMP at all — sends simply time out and the error counter stays at 0.

        This property is therefore True when the control transport socket is
        open (so the host route exists) and has accumulated port-unreachable
        errors while the session is not established — i.e. the remote-control
        server is refusing/ignoring us rather than the network being gone.
        """
        if self._conn_state == RadioConnectionState.CONNECTED:
            return False
        ctrl = self._ctrl_transport
        if ctrl is None:
            return False
        # Socket open == route to host exists (or recently existed); a closed
        # socket means we never got that far.
        if getattr(ctrl, "_udp_transport", None) is None:
            return False
        error_count = getattr(ctrl, "_udp_error_count", 0)
        return isinstance(error_count, int) and error_count >= _UDP_ERROR_THRESHOLD

    @property
    def radio_ready(self) -> bool:
        """Whether CI-V stream is healthy enough for client operations."""
        if not self.connected:
            return False
        if self._civ_recovering or not self._civ_stream_ready:
            return False
        last = getattr(self, "_last_civ_data_received", None)
        if not isinstance(last, (int, float)):
            return False
        return (time.monotonic() - float(last)) <= self._civ_ready_idle_timeout

    @property
    def managed_tx(self) -> ManagedTxSupervisor | None:
        """The radio's managed TX supervisor, or ``None`` when unmanaged.

        Structural implementation of
        :class:`~rigplane.core.radio_protocol.ManagedTxCapable`: a real class
        member, never conjured through ``__getattr__``, so
        :meth:`~rigplane.core.radio_protocol.ManagedTxApi.bind`'s
        ``getattr_static`` read finds it. ``None`` outside a connect session —
        before the first ``connect()`` and again after :meth:`disconnect` —
        where every ingress keeps using the legacy :meth:`set_ptt` path.
        Within one, from :meth:`_arm_managed_tx` to teardown, this answers that
        session's runtime for good, whether or not the arming succeeded:
        a rig whose provider never came ready refuses keys with ``NOT_READY``
        rather than reverting to an unsupervised write (MOR-1193).
        """
        if self._managed_tx_composition is not None:
            return cast(ManagedTxSupervisor, _LEGACY_MANAGED_TX_CUTOVER_BLOCKER)
        return self._managed_tx_runtime

    def install_managed_tx_composition(
        self, composition: ManagedTxCompositionPort
    ) -> None:
        """Install the production authority before any radio session starts."""

        if self._managed_tx_composition is not None:
            raise RuntimeError("managed TX composition is already installed")
        if self._managed_tx_runtime is not None or self._conn_state is not (
            RadioConnectionState.DISCONNECTED
        ):
            raise RuntimeError(
                "managed TX composition must be installed before connect"
            )
        self._managed_tx_composition = composition

    @property
    def tx_snapshot(self) -> "TxSafetySnapshot | None":
        """Current managed TX state, or ``None`` when no runtime exists yet.

        Read-only passthrough to the supervisor's snapshot so presentation
        (MOR-1015) can render *why* a key was refused — unarmed provider,
        someone else's lease, a rig that is not observably OFF — without
        reaching into ``_managed_tx_runtime`` or re-deriving TX state from
        the legacy poller's PTT mirror.
        """
        runtime = self._managed_tx_runtime
        return None if runtime is None else runtime.tx_snapshot

    @property
    def _managed_tx_target_id(self) -> str:
        """Stable identity of the physical rig this radio's TX runtime owns.

        One managed runtime supervises one rig, so the id has to separate two
        radios that a single process holds at once and stay the same across
        that rig's reconnects. For a LAN radio that is the backend family plus
        the CI-V endpoint (``_host`` is the rig's address and ``_civ_port`` the
        data port the control phase negotiated); serial radios carry the OS
        device path instead, since ``_IcomSerialRadioBase`` passes the device
        as ``host`` with no ports at all and ``f"…:{host}:0"`` would read as a
        LAN rig on port zero. That branch is inert today — the serial backend
        overrides ``connect()`` without calling ``super()``, so nothing arms
        it (MOR-1219) — and exists so the id is right the day it does.
        """
        device = getattr(self, "_serial_device", None)
        if isinstance(device, str) and device:
            return f"serial:{device}"
        return f"{self.backend_id}:{self._host}:{self._civ_port}"

    async def _managed_tx_provider_answers_ptt(self) -> bool:
        """Ask the rig for PTT once, unmanaged, before any port is captured.

        The guard on the retirement trap. ``request_fresh_ptt`` treats "the
        provider could not prove PTT state" as grounds to retire the managed
        port, and ``CivRuntime.retire_managed_tx_port`` implements retirement
        by advancing the CI-V epoch and **disconnecting the transport itself**
        — correct for a port that was armed and is now suspect, catastrophic
        for one that failed its very first read: it would close the CI-V
        socket ``connect()`` opened moments earlier and hand the caller back a
        radio that reports ``connected is False``.

        So the rig proves it answers ``0x1C 0x00`` on the ordinary command
        path first, where a timeout costs one ``CommandError`` and nothing
        else. Only then is the runtime given a port it can retire. A rig that
        does not implement the command — or is not answering at all — never
        reaches step 2, and keeps its session.

        This narrows the trap rather than closing it: a rig that answers the
        probe and then drops the seed reply still loses the socket. Closing it
        for good means teaching either ``request_fresh_ptt`` or
        ``retire_managed_tx_port`` that an unarmed port is not a suspect one,
        which is a change to modules this PR does not touch.
        """
        civ = build_civ_frame(self._radio_addr, CONTROLLER_ADDR, 0x1C, sub=0x00)
        try:
            await self._send_civ_expect(
                civ, label="managed_tx_ptt_probe", timeout=_MANAGED_TX_PROBE_TIMEOUT_S
            )
        except Exception:
            return False
        return True

    def _managed_tx_binding_is_live(self) -> bool:
        """Whether the runtime's provider is bound to *this* CI-V port, now.

        The one question a re-arm may key off, and the reason
        ``_managed_tx_armed_epoch`` cannot answer it: that marker is written
        before arming's first await, and arming's own retirement step can
        advance the CI-V epoch before the capture that follows it. A perfectly
        successful arm can therefore leave the marker one epoch behind a live
        binding — at which point an epoch-keyed guard waves the *next* caller
        through into exactly the second ``replace_provider`` this exists to
        prevent, one call later than the naive case. That second call retires a
        current port, and retirement advances the generation and disconnects
        the transport, so the redundant re-arm is what breaks the connection
        the reconnect just repaired.

        The three facts recorded at capture are compared against the live ones
        instead, which is also why nothing has to clear them: a retirement
        advances the supervisor's provider generation, a CI-V recovery advances
        the epoch, and a rebuilt data path replaces the transport object. Any
        one of the three moving means the recorded port is no longer the bound
        one, and the answer flips to "re-arm" on its own.
        """
        runtime, bound = self._managed_tx_runtime, self._managed_tx_bound_port
        if runtime is None or bound is None or self._civ_transport is None:
            return False
        generation, epoch, transport = bound
        return (
            transport is self._civ_transport
            and epoch == self._civ_epoch
            and generation == runtime.tx_snapshot.provider_generation
        )

    async def rearm_managed_tx(self) -> None:
        """Re-arm managed TX after a repaired CI-V path. The sole re-arm path.

        Every consumer that used to reach for ``replace_provider`` of its own
        accord routes here instead — the control phase ahead of its reconnect
        callbacks, the Web recovery hook behind them — and exactly one of them
        does the work: a caller that finds the provider already bound to the
        live port returns having touched nothing. That no-op is the point.
        Rebinding is not idempotent, and the cost of the redundant call is not
        a wasted round trip but the transport itself
        (:meth:`_managed_tx_binding_is_live`).

        Past the guard this is arming unchanged — probe, capture, seed, and the
        same degradation: a rig that cannot be supervised keeps its published
        runtime and refuses TX rather than falling back to the unsupervised
        legacy write (MOR-1193). Failures never propagate, so a caller on a
        recovery path can await it without guarding.

        A radio with no CI-V transport is not re-armable at all, and that is
        what makes a disconnected one inert: :meth:`disconnect` clears the
        runtime, so a re-arm that ran anyway would *build a second one* and
        republish ``managed_tx`` on a radio with nothing to bind it to — a
        supervisor whose port was captured from a closed session, which is a
        worse answer than the honest ``None``. Only ``connect()`` brings a
        runtime back.
        """
        if self._managed_tx_composition is not None:
            return
        if self._civ_transport is None or self._managed_tx_binding_is_live():
            return
        async with self._managed_tx_arm_lock:
            # Re-checked under the lock: the holder this caller queued behind
            # may have been the one that armed the very port it came to replace.
            if not self._managed_tx_binding_is_live():
                await self._run_managed_tx_arm()

    async def _arm_managed_tx(self) -> None:
        """Arm managed TX from ``connect()``, once per CI-V epoch.

        The epoch bound belongs to this entry point, not to the arming steps:
        a fresh connect is the one caller with nothing to compare a binding
        against, so "have I already tried on this epoch" is the only cheap
        question it can ask. Re-arm callers ask a sharper one — see
        :meth:`rearm_managed_tx`.

        ``RIGPLANE_MANAGED_TX=0`` stops this short of building anything, so
        ``managed_tx`` stays ``None`` and every ingress keeps the legacy
        unsupervised ``set_ptt`` path. The check itself lives one level down,
        in :meth:`_run_managed_tx_arm`, because that is the sole construction
        site and ``rearm_managed_tx`` reaches it too — a gate here alone would
        let the first CI-V recovery arm the radio the operator switched off.

        **Naming trap (R9):** the CLI's ``--managed`` flag and the
        ``args.managed_runtime`` namespace it sets select the *local station
        runtime* (web host binding, bundled rigctld) and have nothing whatever
        to do with managed TX. ``RIGPLANE_MANAGED_TX`` is the only managed-TX
        switch; neither reads the other.
        """
        if self._managed_tx_composition is not None:
            return
        async with self._managed_tx_arm_lock:
            if self._managed_tx_armed_epoch != self._civ_epoch:
                await self._run_managed_tx_arm()

    async def _run_managed_tx_arm(self) -> None:
        """Build, bind and seed the managed TX runtime for this CI-V epoch.

        Four steps, all of which must land before a key is allowed:

        1. construct the runtime — once per *connect session*, never per arm,
           so every ingress that already bound a facade keeps pointing at the
           same supervisor across a reconnect, a re-arm or a soft_disconnect.
           The session, not the radio, is the bound: :meth:`disconnect` shuts
           the runtime down and clears it (PR4), and the next ``connect()``
           builds a new one. A fresh runtime's provider generations start again
           at 1, which is why teardown also clears the CI-V layer's
           generation-keyed registry — marks left by the previous session would
           otherwise read as this one's and fail every arm from here on
           (:meth:`~rigplane.runtime._civ_rx.CivRuntime.reset_managed_tx_generations`);
        2. prove the rig answers PTT reads at all
           (:meth:`_managed_tx_provider_answers_ptt`) — the guard that keeps a
           failed arm from costing the CI-V session;
        3. ``replace_provider(ready=True)`` — captures the *current* CI-V
           transport as the managed port, which is why this runs at the end of
           ``connect()`` and never in ``__init__``;
        4. ``request_fresh_ptt()`` — one CI-V ``0x1C 0x00`` whose answer seeds
           the authoritative OFF observation. Not optional and not cosmetic:
           nothing polls PTT periodically, and ``request_on`` refuses with
           ``RADIO_NOT_OFF`` until an observation exists, so a runtime armed
           without this step can never key at all.

        Failure at any step never propagates: a rig that cannot supervise TX
        must still be usable for RX, tuning and audio. It degrades to
        provider-not-ready and *stays published* — dropping the runtime would
        hand the next key to the legacy unsupervised ``set_ptt`` with no lease,
        no owner and no watchdog, which is the exact bypass MOR-1193 closed.

        Serialised by ``_managed_tx_arm_lock``, so the steps below never
        interleave with a second attempt. A step-4 failure retires the managed
        port, which advances the epoch by itself — so the radio is immediately
        eligible for a fresh attempt on the next ``connect()``/rearm rather
        than being latched off for good.

        Ahead of all four sits the kill switch. ``RIGPLANE_MANAGED_TX=0``
        returns before the construction, so there is no runtime to publish and
        ``managed_tx`` answers ``None`` — which every ingress already reads as
        "unmanaged" and routes around, back to the legacy ``set_ptt`` write.
        That is a different thing from a failed arm, which keeps the runtime
        and refuses keys, and it is deliberately the louder of the two: a
        failed arm is the rig's doing and the operator gets refusals to show
        for it, while this one silently removes the lease, the owner and the
        watchdog from every key on the radio. So it warns on every connect for
        as long as it is set, and the marker below stays unwritten so a later
        connect made with the switch back on arms normally.
        """
        if not get_managed_tx_enabled():
            logger.warning(
                "managed TX disabled by RIGPLANE_MANAGED_TX for %s: TX falls "
                "back to the legacy unsupervised set_ptt path — no lease, no "
                "owner, no keep-alive watchdog. Unset the variable to restore "
                "supervision. (Unrelated to the CLI's --managed flag.)",
                self._managed_tx_target_id,
            )
            return
        self._managed_tx_armed_epoch = self._civ_epoch
        runtime = self._managed_tx_runtime
        if runtime is None:
            runtime = ManagedRadioRuntime(
                self._managed_tx_target_id,
                service_factory=managed_tx_effect_service,
                provider_lifecycle=self,
            )
            self._managed_tx_runtime = runtime
        step = "ptt_probe"
        try:
            if await self._managed_tx_provider_answers_ptt():
                step = "replace_provider"
                bound = (await runtime.replace_provider(ready=True)).snapshot
                if bound.provider_ready:
                    # Identity of the port just captured, for the re-arm guard.
                    # Recorded here rather than inside
                    # ``_capture_managed_tx_port`` so a backend that overrides
                    # that lifecycle hook cannot silently lose it.
                    self._managed_tx_bound_port = (
                        bound.provider_generation,
                        self._civ_epoch,
                        self._civ_transport,
                    )
                    step = "request_fresh_ptt"
                    if (await runtime.request_fresh_ptt()).outcome is TxOutcome.APPLIED:
                        return
            logger.error(
                "managed TX arming for %s did not complete at %s; TX stays "
                "refused until a later connect re-arms it",
                runtime.target_id,
                step,
            )
        except Exception:
            logger.error(
                "managed TX arming for %s raised at %s; TX stays refused until "
                "a later connect re-arms it",
                runtime.target_id,
                step,
                exc_info=True,
            )
        # Degrade, never disappear.  A step-4 failure has already retired the
        # port and marked the (new) provider not-ready, so this is a no-op
        # there; the probe and capture paths need it stated explicitly.
        try:
            await runtime.set_provider_ready(ready=False)
        except Exception:
            logger.debug(
                "managed TX degrade-to-not-ready failed for %s",
                runtime.target_id,
                exc_info=True,
            )

    async def _shutdown_managed_tx(self) -> None:
        """Stop supervised TX while the CI-V path can still carry the OFF.

        Ordered ahead of the session teardown in :meth:`disconnect` for the one
        reason that matters: ``shutdown`` emergency-releases a held lease, and
        a WRITE_OFF issued after the transport is gone is a rig left keyed with
        nobody watching it. Bounded for the mirror-image reason — a supervisor
        wedged on a rig that stopped answering must not hold ``disconnect()``
        open, since closing the socket is itself the de-key of last resort.
        The runtime shields its own shutdown task, so the bound abandons the
        wait rather than the release: the OFF keeps trying behind us.

        Clearing the three managed-TX members afterwards is the other half of
        the MOR-1193 invariant, not a weakening of it. "Managed-eligible means
        ``managed_tx`` is never ``None``" holds *from connect to disconnect*;
        past disconnect there is no session to supervise, and the next
        ``connect()`` arms a fresh runtime. Keeping this one published would
        advertise a supervisor over a closed port — and leave the re-arm path
        able to rebuild a binding for a rig that is gone.

        Dropping the runtime is also what restarts its provider-generation
        counter, which the CI-V layer uses as a registry key — so the session's
        entries there have to go with it, or session 2's generation 1 collides
        with session 1's and every arm from here on fails
        (:meth:`~rigplane.runtime._civ_rx.CivRuntime.reset_managed_tx_generations`).
        Ordered inside the ``finally`` with the members it belongs to, so a
        shutdown that timed out or raised still leaves a connectable radio.
        """
        composition = self._managed_tx_composition
        if composition is not None:
            termination = asyncio.Event()
            task = asyncio.create_task(composition.shutdown(termination))
            try:
                await asyncio.wait_for(
                    asyncio.shield(task), timeout=_MANAGED_TX_TEARDOWN_TIMEOUT_S
                )
            except TimeoutError:
                termination.set()
                await asyncio.shield(task)
            return
        runtime = self._managed_tx_runtime
        if runtime is None:
            return
        try:
            await asyncio.wait_for(
                runtime.shutdown(
                    release_provider=_managed_tx_provider_released_by_disconnect
                ),
                timeout=_MANAGED_TX_TEARDOWN_TIMEOUT_S,
            )
        except asyncio.TimeoutError:
            logger.error(
                "managed TX shutdown for %s did not settle within %.1fs; "
                "disconnecting anyway — the rig may still be keyed",
                runtime.target_id,
                _MANAGED_TX_TEARDOWN_TIMEOUT_S,
            )
        except Exception:
            logger.error(
                "managed TX shutdown for %s failed; disconnecting anyway",
                runtime.target_id,
                exc_info=True,
            )
        finally:
            self._managed_tx_runtime = None
            self._managed_tx_bound_port = None
            self._managed_tx_armed_epoch = None
            self._civ_runtime.reset_managed_tx_generations()

    async def _park_managed_tx(self) -> None:
        """Refuse keys for the length of a CI-V gap, without unpublishing.

        ``soft_disconnect`` takes the data path down and expects it back, so
        the runtime outlives it — but in between, a lease granted on the
        strength of a provider still marked ready would have its WRITE_ON land
        on a socket that is already closing, which is a key the supervisor
        believes in and the rig never saw. Marking the provider not-ready first
        makes ``request_on`` answer ``NOT_READY`` for the length of the gap,
        and turns a lease held across it into the release that
        :meth:`rearm_managed_tx` services against the repaired port.

        Bounded and fail-soft: a gate that will not shut is not a reason to
        refuse to tear down the path it guards.
        """
        composition = self._managed_tx_composition
        if composition is not None:
            event = composition.active_provider
            if event is not None:
                await composition.start_provider_unavailable(event)
            return
        runtime = self._managed_tx_runtime
        if runtime is None:
            return
        try:
            await asyncio.wait_for(
                runtime.set_provider_ready(ready=False),
                timeout=_MANAGED_TX_TEARDOWN_TIMEOUT_S,
            )
        except Exception:
            logger.warning(
                "managed TX did not park for %s ahead of soft_disconnect",
                runtime.target_id,
                exc_info=True,
            )

    # ------------------------------------------------------------------
    # Backwards-compatible property shims for _connected / _intentional_disconnect
    # (used by tests and internal loops — keep in sync with _conn_state)
    # ------------------------------------------------------------------

    @property
    def _connected(self) -> bool:
        return self._conn_state == RadioConnectionState.CONNECTED

    @_connected.setter
    def _connected(self, value: bool) -> None:
        if value:
            self._conn_state = RadioConnectionState.CONNECTED
        elif self._conn_state == RadioConnectionState.CONNECTED:
            self._conn_state = RadioConnectionState.DISCONNECTED

    @property
    def _intentional_disconnect(self) -> bool:
        return self._conn_state == RadioConnectionState.DISCONNECTED

    @_intentional_disconnect.setter
    def _intentional_disconnect(self, value: bool) -> None:
        if value:
            self._conn_state = RadioConnectionState.DISCONNECTED
        elif self._conn_state == RadioConnectionState.DISCONNECTED:
            # Clearing intentional disconnect means reconnect is allowed.
            self._conn_state = RadioConnectionState.RECONNECTING

    @property
    def state_cache(self) -> StateCache:
        """Last-known radio state cache (frequency, mode, PTT, meters).

        Updated from both explicit GET responses and unsolicited CI-V frames
        (e.g. VFO knob turns).  Callers can read this directly for a
        non-blocking snapshot of recent state.
        """
        return self._state_cache

    @property
    def state_store(self) -> StateStore:
        """Canonical confirmed-observation store for runtime state ingress."""

        return self._state_store

    @property
    def state_model_service(self) -> "RadioStateModelService | None":
        """Optional StateStore freshness/acquisition service for consumers."""

        return self._state_model_service

    @state_model_service.setter
    def state_model_service(self, service: "RadioStateModelService | None") -> None:
        self._state_model_service = service

    @property
    def radio_state(self) -> RadioState:
        """Dual-receiver state snapshot (MAIN + SUB receivers, PTT, etc.).

        Populated by the CI-V RX stream.  May be replaced by
        :class:`~rigplane.web.server.WebServer` with a shared instance.
        """
        return self._radio_state

    @property
    def audio_bus(self) -> Any:
        """Lazy-initialized AudioBus for pub/sub audio distribution."""
        if self._audio_bus is None:
            from rigplane.audio.bus import AudioBus

            self._audio_bus = AudioBus(self)
        return self._audio_bus

    @property
    def audio_session(self) -> Any:
        """Lazy-initialized radio-owned AudioSession singleton (MOR-579).

        ONE session per radio, shared by every consumer (bridge, web TX
        handler, poller PTT hooks) so the TX refcount can never split
        across independent sessions. Wraps the shared :attr:`audio_bus`.
        """
        if self._audio_session is None:
            from rigplane.audio.session import AudioSession

            self._audio_session = AudioSession(self)
        return self._audio_session

    @property
    def profile(self) -> RadioProfile:
        """Active runtime radio profile."""
        return self._profile

    @property
    def model(self) -> str:
        """Human-readable radio model name."""
        return self._profile.model

    @property
    def backend_id(self) -> str:
        """Stable backend family identifier — ``"rigplane"`` for LAN/CI-V-over-Ethernet."""
        return "rigplane"

    @property
    def capabilities(self) -> set[str]:
        """Set of capability tags supported by this radio.

        Standard tags: ``audio``, ``scope``, ``dual_rx``, ``meters``,
        ``tx``, ``cw``.
        """
        return set(self._profile.capabilities)

    @staticmethod
    def _coerce_mode(mode: Mode | str) -> Mode:
        """Normalize mode input and validate string names."""
        if isinstance(mode, Mode):
            return mode
        raw_mode = mode
        mode_key = mode.strip().upper().replace("-", "_")
        try:
            return Mode[mode_key]
        except KeyError as exc:
            supported = ", ".join(m.name for m in Mode)
            raise ValueError(
                f"Unknown mode: {raw_mode!r}. Supported modes: {supported}"
            ) from exc

    def set_state_change_callback(
        self, callback: Callable[[str, dict[str, Any]], None] | None
    ) -> None:
        """Register callback for CI-V state change notifications."""
        self._on_state_change = callback

    def set_reconnect_callback(self, callback: Callable[[], None] | None) -> None:
        """Register callback invoked after successful soft reconnect."""
        self._on_reconnect = callback

    def set_reconnect_status_callback(
        self, callback: Callable[[dict[str, Any]], None] | None
    ) -> None:
        """Register callback for reconnect-status updates (MOR-594).

        The callback receives a small structured payload at each meaningful
        reconnect edge: ``{"state": "reconnecting" | "connected" |
        "disconnected", "attempt": int, "next_retry_seconds": float | None}``.
        Invocation is best-effort — a raising callback never breaks the
        watchdog/reconnect loops.
        """
        self._on_reconnect_status = callback

    def civ_stats(self) -> dict[str, int]:
        """Return CI-V request tracker statistics for monitoring.

        Returns:
            Dict with keys ``active_waiters``, ``stale_cleaned``,
            ``timeouts``, ``generation``, ``ack_backlog_hits``,
            ``ack_backlog_drops``, and ``ack_orphans``.
        """
        return self._civ_request_tracker.snapshot_stats()

    def update_credentials(
        self, *, username: str | None = None, password: str | None = None
    ) -> None:
        """Update the stored login credentials for future full reconnects.

        The new values are used the next time a full ``connect()`` performs
        authentication (the login packet is built from the stored
        credentials). ``soft_reconnect()`` intentionally reuses the existing
        session token and does NOT re-authenticate, so a rotated credential
        only takes effect on a full reconnect.

        Args:
            username: New username, or ``None`` to keep the current one.
            password: New password, or ``None`` to keep the current one.
        """
        if username is not None:
            self._username = username
        if password is not None:
            self._password = password

    async def connect(self) -> None:
        """Open connection to the radio and authenticate.

        Delegates to the composed ControlPhaseRuntime, then fetches
        initial radio state so RadioState is populated before consumers.

        A fresh (re)connection means any prior external-CAT session is over —
        its transport is gone — so clear leaked ownership first (#1702). Without
        this, a ``begin_external_cat_session()`` that was never matched by an
        ``end_external_cat_session()`` (managed-runtime crash/restart, a dropped
        Hamlib bridge) would keep cooperating pollers paused forever, freezing
        ``radio_state`` while rigctld/web serve a stale frequency.

        Managed TX is armed last (MOR-1016): it captures the CI-V transport
        this connect just built, and it must not delay the state fetch every
        consumer waits on. It never raises — see :meth:`_arm_managed_tx` — so
        ``connect()`` returns with ``managed_tx`` published either way.
        """
        self._reset_external_cat_session()
        await self._session_lifecycle.connect()
        await self._fetch_initial_state()
        await self._arm_managed_tx()

    async def __aenter__(self) -> "CoreRadio":
        # Route through the lifecycle's guaranteed-release context manager so a
        # failure between connect() and the body still releases the session
        # (graceful-close Hole 2).  ``__aexit__`` always releases.
        await self.connect()
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb) -> None:  # type: ignore[no-untyped-def]
        await self.disconnect()

    async def disconnect(self) -> None:
        """Cleanly disconnect from the radio (always releases; idempotent).

        Routes through the lifecycle (guaranteed-release policy).  If the radio
        still reports a live session afterwards — i.e. the underlying
        ``_conn_state`` was driven CONNECTED outside the lifecycle's own
        connect() path (legacy/test injection, soft_disconnect bookkeeping) and
        the lifecycle therefore treated disconnect() as an idempotent no-op —
        fall back to the control-phase graceful teardown so a genuinely live
        session is always released (graceful-close invariant).

        Managed TX is shut down first (MOR-1016), bounded, because the OFF a
        held lease owes the rig has to go out over a transport that is still
        open — see :meth:`_shutdown_managed_tx`, which is also where the radio
        stops being managed until the next ``connect()``.
        """
        await self._shutdown_managed_tx()
        await self._session_lifecycle.disconnect()
        if self._conn_state == RadioConnectionState.CONNECTED:
            await self._control_phase.disconnect()

    async def soft_disconnect(self) -> None:
        """Disconnect CI-V and audio but keep control transport alive.

        This allows fast reconnect without re-authentication — the radio
        keeps the session open on the control port.

        Managed TX is parked, not shut down (MOR-1016): the runtime survives
        the gap and is re-armed against the repaired port, so the only thing
        that changes here is that it stops accepting keys — see
        :meth:`_park_managed_tx`.
        """
        if self._conn_state != RadioConnectionState.CONNECTED:
            return
        await self._park_managed_tx()
        self._conn_state = RadioConnectionState.DISCONNECTING
        self._civ_runtime.advance_generation("soft_disconnect")

        # Stop audio
        await self._stop_audio_watchdog()
        if self._audio_stream is not None:
            await self._audio_stream.stop_rx()
            await self._audio_stream.stop_tx()
            self._audio_stream = None
        self._pcm_tx_fmt = None
        self._pcm_rx_user_callback = None
        self._opus_rx_user_callback = None
        if self._audio_transport is not None:
            try:
                await self._send_audio_open_close(open_stream=False)
            except Exception:
                logger.debug("soft_disconnect: audio open/close failed", exc_info=True)
            await self._audio_transport.disconnect()
            self._audio_transport = None

        # Stop CI-V
        if self._civ_transport:
            try:
                await self._send_open_close(open_stream=False)
            except Exception:
                logger.debug("soft_disconnect: civ open/close failed", exc_info=True)
            await self._civ_runtime.stop_data_watchdog()
            await self._civ_runtime.stop_worker()
            await self._civ_runtime.stop_pump()
            await self._civ_transport.disconnect()
            self._civ_transport = None

        self._conn_state = RadioConnectionState.DISCONNECTED
        self._civ_stream_ready = False
        self._civ_recovering = False
        logger.info(
            "Soft disconnect from %s:%d (control kept alive)", self._host, self._port
        )

    async def _force_cleanup_civ(self) -> None:
        """Unconditionally tear down CI-V transport regardless of state.

        Used as a last resort before reconnect when normal soft_disconnect
        fails or state is inconsistent (e.g. after struct overflow crash).
        """
        logger.info("force_cleanup_civ: tearing down CI-V unconditionally")
        await self._civ_runtime.stop_data_watchdog()
        await self._civ_runtime.stop_worker()
        await self._civ_runtime.stop_pump()
        if self._civ_transport is not None:
            try:
                await self._civ_transport.disconnect()
            except Exception:
                logger.debug(
                    "force_cleanup_civ: transport disconnect failed", exc_info=True
                )
            self._civ_transport = None
        ctrl_alive = bool(
            self._ctrl_transport
            and getattr(self._ctrl_transport, "_udp_transport", None) is not None
        )
        self._conn_state = (
            RadioConnectionState.RECONNECTING
            if ctrl_alive
            else RadioConnectionState.DISCONNECTED
        )
        self._civ_stream_ready = False
        self._civ_recovering = ctrl_alive

    async def soft_reconnect(self) -> None:
        """Reconnect CI-V transport using the existing control session.

        Routes through the unified lifecycle RECOVERING loop
        (:meth:`CoreRadioSessionLifecycle.soft_reconnect` →
        :meth:`CoreRadioSessionLifecycle._run_recover`), which is now the SINGLE
        owner of recovery policy: the per-attempt primitive
        (``soft_reconnect_once`` = control + token reused; raises on failure),
        the N-attempt count (``_MAX_RECONNECTS``), the backoff
        (``_RECONNECT_BACKOFF``), and exhaustion → CLOSING + full release.

        The CI-V data watchdog (``_civ_rx.py``) remains the stall DETECTOR: it
        watches CI-V data flow, sends patient OpenClose nudges, and on patience
        exhaustion TRIGGERS this method in a detached task — but it no longer
        owns the retry/backoff/exhaustion ladder (that moved here). The
        watchdog still unconditionally re-arms in ``finally`` so a later stall
        is caught again (#1217).
        """
        await self._session_lifecycle.soft_reconnect()

    async def _send_open_close(self, *, open_stream: bool) -> None:
        """Delegate to control-phase runtime (for soft_disconnect, _force_cleanup_civ, etc.)."""
        await self._control_phase._send_open_close(open_stream=open_stream)

    def _check_connected(self) -> None:
        """Delegate to CI-V runtime (raises ConnectionError if not connected)."""
        self._civ_runtime._check_connected()

    async def _execute_civ_raw(
        self,
        civ_frame: bytes,
        wait_response: bool = True,
        *,
        is_current: "Callable[[], bool] | None" = None,
    ) -> CivFrame | None:
        """Delegate to CI-V runtime (for tests and internal callers)."""
        return await self._civ_runtime.execute_civ_raw(
            civ_frame,
            wait_response=wait_response,
            is_current=is_current,
        )

    def _update_state_cache_from_frame(self, frame: CivFrame) -> None:
        """Delegate to CI-V runtime (for tests that feed unsolicited frames)."""
        self._civ_runtime._update_state_cache_from_frame(frame)

    async def _send_civ_raw(
        self,
        civ_frame: bytes,
        *,
        priority: Priority = Priority.NORMAL,
        key: str | None = None,
        dedupe: bool = False,
        wait_response: bool = True,
        timeout: float | None = None,
        wait_dispatch: bool = True,
        is_current: "Callable[[], bool] | None" = None,
    ) -> CivFrame | None:
        """Delegate to CI-V runtime (keeps existing call sites unchanged)."""
        return await self._civ_runtime.send_civ_raw(
            civ_frame,
            priority=priority,
            key=key,
            dedupe=dedupe,
            wait_response=wait_response,
            timeout=timeout,
            wait_dispatch=wait_dispatch,
            is_current=is_current,
        )

    async def _send_civ_expect(
        self,
        civ_frame: bytes,
        *,
        label: str = "command",
        priority: Priority = Priority.NORMAL,
        key: str | None = None,
        dedupe: bool = False,
        timeout: float | None = None,
    ) -> CivFrame:
        """Send a CIV frame and raise CommandError if no response."""
        resp = await self._send_civ_raw(
            civ_frame,
            priority=priority,
            key=key,
            dedupe=dedupe,
            timeout=timeout,
        )
        if resp is None:
            raise CommandError(f"No response for {label}")
        return resp

    async def _send_audio_open_close(self, *, open_stream: bool) -> None:
        """Delegate to control-phase runtime."""
        await self._control_phase._send_audio_open_close(open_stream=open_stream)

    async def _send_token(self, magic: int) -> None:
        """Delegate to control-phase runtime."""
        await self._control_phase._send_token(magic)

    # ------------------------------------------------------------------
    # Initial state fetch
    # ------------------------------------------------------------------

    _initial_state_fetched: bool = False

    _INITIAL_STATE_GAP_LAN: float = 0.012
    _INITIAL_STATE_GAP_SERIAL: float = 0.050

    async def _fetch_initial_state(self) -> None:
        """Fetch full radio state once to populate RadioState (delegates)."""
        await _initial_state.fetch_initial_state(self)

    # ------------------------------------------------------------------
    # Watchdog & reconnect loops
    # ------------------------------------------------------------------

    async def _watchdog_loop(self) -> None:
        """Monitor connection health (delegates to ``radio_reconnect``)."""
        await _reconnect.watchdog_loop(self)

    async def _reconnect_loop(self) -> None:
        """Attempt to reconnect with exponential backoff (delegates)."""
        await _reconnect.reconnect_loop(self)

    # ------------------------------------------------------------------
    # Public CI-V API
    # ------------------------------------------------------------------

    async def send_civ(
        self,
        command: int,
        sub: int | None = None,
        data: bytes | None = None,
        *,
        wait_response: bool = True,
        priority: Priority = Priority.NORMAL,
        wait_dispatch: bool = True,
    ) -> CivFrame | None:
        """Send a CI-V command.

        Args:
            command: CI-V command byte.
            sub: Optional sub-command byte.
            data: Optional payload data.
            wait_response: If False, fire-and-forget (no response wait).
            priority: Commander lane priority. Defaults to NORMAL so user
                commands are not de-prioritized; background pollers pass
                ``Priority.BACKGROUND`` so polls yield to user commands.
            wait_dispatch: If False, return immediately after enqueueing
                instead of awaiting the commander dispatching this frame.
                Background pollers pass False so the poll burst does not park
                the poll loop on the commander future (the response still
                arrives via the RX path). Defaults to True so user commands
                keep their blocking contract.

        Returns:
            Parsed response CivFrame, or None if wait_response=False.
        """
        self._check_connected()
        frame = build_civ_frame(
            self._radio_addr, CONTROLLER_ADDR, command, sub=sub, data=data
        )
        return await self._send_civ_raw(
            frame,
            wait_response=wait_response,
            priority=priority,
            wait_dispatch=wait_dispatch,
        )

    # ------------------------------------------------------------------
    # Raw CI-V pipe (MOR-164) — transparent byte transport for Hamlib A1
    # ------------------------------------------------------------------

    async def send_civ_raw_fire_and_forget(self, frame: bytes) -> None:
        """Transmit a raw CI-V frame without waiting for or matching a response.

        For the Hamlib A1 bridge, where the *external* CAT master (Hamlib) owns
        request/response matching and RigPlane is used purely as a byte
        transport. Unlike :meth:`send_civ`, this never registers a response
        waiter, so it does **not** time out merely because the radio answered a
        write with a bare ACK (``FE FE E0 98 FB FD``); the ACK is observed via
        :meth:`add_raw_civ_listener` instead.
        """
        self._check_connected()
        await self._send_civ_raw(frame, wait_response=False)

    def add_raw_civ_listener(
        self, callback: Callable[[bytes], Any]
    ) -> RawCivSubscription:
        """Register a listener for inbound raw CI-V frame bytes.

        The callback receives the exact on-wire frame bytes of each inbound
        frame addressed to the controller (including bare ACK/NAK frames).
        Unsolicited transceive broadcasts (``to_addr == 0x00``) are filtered out
        so they do not pollute a Hamlib-owned byte stream. Returns a
        :class:`RawCivSubscription`; call ``.close()`` to unregister.

        To stop RigPlane's own pollers from competing on the wire while an
        external CAT master consumes this stream, wrap the session with
        :meth:`begin_external_cat_session` / :meth:`end_external_cat_session`
        (MOR-166 slice 2).
        """
        self._raw_civ_listeners.append(callback)
        return RawCivSubscription(self._raw_civ_listeners, callback)

    # ---- External CAT-session ownership (MOR-166 slice 2) ----

    @property
    def external_cat_session_active(self) -> bool:
        """True while an external CAT master (e.g. a Hamlib bridge) owns the wire."""
        return self._external_cat_session

    def begin_external_cat_session(self) -> None:
        """Mark the radio as owned by an external CAT session.

        Cooperating pollers (e.g. the web ``RadioPoller``) pause their own CI-V
        traffic while this is set, so they do not pollute the owner's byte
        stream. Idempotent when the external owner already holds the session.
        """
        if self._external_cat_session:
            current = self._external_cat_session_owner or "external"
            if current == "external":
                self._external_cat_session_owner = "external"
                return
            raise RuntimeError(f"CI-V stream is already owned by {current}")
        self._external_cat_session = True
        self._external_cat_session_owner = "external"

    def end_external_cat_session(self) -> None:
        """Release legacy external-CAT-session ownership. Idempotent."""
        if self._external_cat_session_owner not in (None, "external"):
            return
        self._external_cat_session = False
        self._external_cat_session_owner = None

    def _reset_external_cat_session(self) -> None:
        """Unconditionally clear external-CAT ownership (#1702).

        Used on (re)connect: a new connection invalidates any prior session
        regardless of owner, so this drops a leaked ``begin_external_cat_session``
        that was never released. Unlike :meth:`end_external_cat_session` it does
        not respect the current owner — the old session can no longer exist.
        """
        self._external_cat_session = False
        self._external_cat_session_owner = None

    def _claim_external_cat_session(self, owner: str) -> None:
        """Claim exclusive CI-V stream ownership for a scoped transaction."""
        if self._external_cat_session:
            current = self._external_cat_session_owner or "external"
            raise RuntimeError(f"CI-V stream is already owned by {current}")
        self._external_cat_session = True
        self._external_cat_session_owner = owner

    def _release_external_cat_session(self, owner: str) -> None:
        """Release a transaction claim without disturbing another owner."""
        if self._external_cat_session_owner not in (None, owner):
            return
        self._external_cat_session = False
        self._external_cat_session_owner = None

    async def send_civ_transaction(
        self,
        command: int,
        sub: int | None = None,
        data: bytes | None = None,
        *,
        expect: RawCivExpectation = "data",
        timeout: float | None = None,
    ) -> RawCivTransactionResult:
        """Send one raw CI-V command with explicit response semantics.

        This is intentionally separate from the web poller's fire-and-forget
        queue. While the transaction is active, cooperating pollers pause via
        ``external_cat_session_active`` so their background traffic cannot
        consume or pollute the caller's response.
        """
        self._check_connected()
        if not 0 <= command <= 0xFF:
            raise ValueError(f"command must be 0-255, got {command}")
        if sub is not None and not 0 <= sub <= 0xFF:
            raise ValueError(f"sub must be 0-255, got {sub}")
        if expect not in ("none", "ack", "data"):
            raise ValueError(f"unsupported CI-V expectation: {expect!r}")

        payload = data or b""
        frame = build_civ_frame(
            self._radio_addr, CONTROLLER_ADDR, command, sub=sub, data=payload
        )
        owner = "raw-civ-transaction"
        self._claim_external_cat_session(owner)
        try:
            return await self._civ_runtime.execute_civ_transaction(
                frame,
                expect=expect,
                timeout=timeout,
            )
        finally:
            self._release_external_cat_session(owner)

    async def reconcile_state(self) -> None:
        """Re-read full radio state after an external session changed the rig.

        Call once the external CAT master has finished; refreshes RigPlane's
        ``RadioState`` from the wire so it reflects any frequency/mode/etc. the
        external master applied while it owned the session.
        """
        await self._fetch_initial_state()

    async def get_freq(
        self, receiver: int = RECEIVER_MAIN, *, bypass_cache: bool = False
    ) -> int:
        """Get the current operating frequency in Hz.

        Args:
            receiver: 0=MAIN, 1=SUB.
            bypass_cache: Skip dedupe and cache fallback (used by RadioPoller).

        On timeout falls back to the state cache (if populated) rather than
        raising immediately, allowing callers to remain responsive while the
        radio is busy streaming scope data.
        """
        self._check_connected()
        self._require_receiver(receiver, operation="get_freq")
        if receiver == RECEIVER_MAIN:
            return await self._get_frequency_main(bypass_cache=bypass_cache)

        # Use 0x25 0x01 (unselected receiver freq) — no VFO swap needed.
        return await self._get_unselected_freq()

    async def set_freq(self, freq_hz: int, receiver: int = 0) -> None:
        """Set the operating frequency.

        Args:
            freq_hz: Frequency in Hz.
            receiver: 0=MAIN, 1=SUB.
        """
        self._check_connected()
        self._require_receiver(receiver, operation="set_freq")
        if receiver == RECEIVER_MAIN:
            await self._set_frequency_main(freq_hz)
            return

        if self._profile.supports_cmd29(0x05):
            civ = self._commands.set_freq(
                freq_hz, to_addr=self._radio_addr, receiver=receiver
            )
            await self._send_civ_raw(civ, wait_response=False)
        else:
            await self._run_with_receiver_vfo_fallback(
                receiver=receiver,
                operation="set_freq",
                action=lambda: self._set_frequency_main(freq_hz, update_cache=False),
            )

        self._radio_state.receiver("SUB").freq = freq_hz

    async def get_mode(self, receiver: int = 0) -> tuple[str, int | None]:
        """Get current mode as (name, filter) — Protocol-compatible.

        Returns a ``(mode_name, filter_number)`` tuple. For the Icom-specific
        :class:`Mode` enum, use :meth:`get_mode_info` instead.

        .. note:: The returned mode name is the Mode enum ``.name`` attribute
           (e.g. ``"USB"``, ``"CW"``), which matches hamlib mode strings.
        """
        mode, filt = await self.get_mode_info(receiver=receiver)
        return mode.name, filt

    async def get_mode_enum(self) -> "Mode":
        """Get the current operating mode as a :class:`Mode` enum (legacy).

        .. deprecated:: 0.12
           Use :meth:`get_mode` (returns ``tuple[str, int | None]``) or
           :meth:`get_mode_info` (returns ``tuple[Mode, int | None]``).
        """
        mode, _ = await self.get_mode_info()
        return mode

    async def get_mode_info(
        self, receiver: int = RECEIVER_MAIN
    ) -> tuple[Mode, int | None]:
        """Get current mode and filter number (if reported by radio).

        On timeout falls back to the state cache when populated.
        """
        self._check_connected()
        self._require_receiver(receiver, operation="get_mode_info")
        if receiver == RECEIVER_MAIN:
            return await self._get_mode_info_main(update_cache=True)

        # Use 0x26 0x01 (unselected receiver mode) — no VFO swap needed.
        return await self._get_unselected_mode()

    async def get_filter(self, receiver: int = 0) -> int | None:
        """Get current mode filter number (1-3) when available.

        Args:
            receiver: 0=MAIN, 1=SUB.
        """
        _, filt = await self.get_mode_info(receiver=receiver)
        if filt is not None:
            return filt
        # Fallback: only MAIN has the legacy ``_filter_width`` cache; SUB
        # falls back to ``None`` rather than returning MAIN's cached value.
        if receiver == RECEIVER_MAIN:
            return self._filter_width
        return None

    async def set_filter(self, filter_width: int, receiver: int = 0) -> None:
        """Set filter number (1-3) while keeping current mode unchanged."""
        mode_name, _ = await self.get_mode(receiver=receiver)
        await self.set_mode(mode_name, filter_width=filter_width, receiver=receiver)

    async def set_filter_width(self, width_hz: int, receiver: int = 0) -> None:
        """Set DSP IF filter width in Hz (CI-V 0x1A 0x03).

        Hz is translated to a profile-defined index and wrapped via cmd29 only
        when the profile lists ``[0x1A, 0x03]`` in its cmd29 routes
        (IC-7610). The profile must declare a ``set_filter_width`` command
        before the runtime sends a write. On dual-RX profiles that do not
        list the cmd29 route but do declare VFO select codes (IC-9700),
        ``receiver=1`` (SUB) is reached by temporarily selecting SUB via
        CI-V 0x07, sending the plain (non-cmd29) frame, then restoring the
        previously active receiver — mirroring :meth:`set_freq`. Profiles
        with neither a cmd29 route nor VFO select codes still raise
        ``CommandError`` instead of silently writing MAIN.

        Args:
            width_hz: Filter width in Hz. Bounds and step depend on the
                current mode's profile rule.
            receiver: 0=MAIN, 1=SUB.
        """
        self._check_connected()
        self._require_receiver(receiver, operation="set_filter_width")
        if not self._profile.supports_command("set_filter_width"):
            raise CommandError(
                f"set_filter_width is unsupported by profile {self._profile.model}"
            )

        target = self._radio_state.receiver("SUB" if receiver else "MAIN")
        mode_name = getattr(target, "mode", None)
        data_mode = int(getattr(target, "data_mode", 0) or 0)
        rule = self._profile.resolve_filter_rule(mode_name, data_mode=data_mode)

        min_hz = self._profile.filter_width_min
        max_hz = self._profile.filter_width_max
        if rule is not None:
            if rule.fixed:
                raise CommandError(
                    f"set_filter_width is unsupported for fixed-width mode {mode_name}"
                )
            if rule.min_hz is not None:
                min_hz = rule.min_hz
            if rule.max_hz is not None:
                max_hz = rule.max_hz
        if not min_hz <= width_hz <= max_hz:
            raise CommandError(
                f"set_filter_width value must be {min_hz}-{max_hz} Hz "
                f"for {mode_name}, got {width_hz}"
            )

        if rule is None or not rule.segments:
            raise CommandError(
                f"set_filter_width has no filter-width mapping for mode {mode_name}"
            )
        try:
            payload_value = filter_hz_to_index(width_hz, segments=rule.segments)
        except ValueError as exc:
            raise CommandError(str(exc)) from exc

        bcd_index_byte = bcd_encode_value(payload_value, byte_count=1)
        # CI-V 1A 03: 1-byte BCD index (wfview-confirmed). cmd29-wrapped
        # for receiver routing on dual-RX rigs (IC-7610), direct on single-RX
        # (IC-705) and on MAIN for dual-RX rigs without cmd29 support
        # (IC-9700). SUB on a dual-RX rig without cmd29 support but with VFO
        # select codes (IC-9700) is reached via the VFO-select fallback
        # instead of sent to MAIN; SUB with neither raises below.
        if receiver != RECEIVER_MAIN and not self._profile.supports_cmd29(0x1A, 0x03):
            await self._run_with_receiver_vfo_fallback(
                receiver=receiver,
                operation="set_filter_width",
                action=lambda: self.send_civ(
                    0x1A, sub=0x03, data=bcd_index_byte, wait_response=False
                ),
            )
            return

        self._require_cmd29_route(
            0x1A,
            0x03,
            receiver=receiver,
            operation="set_filter_width",
        )
        if self._profile.supports_cmd29(0x1A, 0x03):
            await self.send_civ(
                0x29,
                data=bytes([receiver, 0x1A, 0x03]) + bcd_index_byte,
                wait_response=False,
            )
        else:
            await self.send_civ(
                0x1A, sub=0x03, data=bcd_index_byte, wait_response=False
            )

    async def get_filter_width(self, receiver: int = 0) -> int:
        """Get DSP IF filter width in Hz (CI-V 0x1A 0x03).

        The response encoding is profile-driven. Icom profiles use a 1-byte
        BCD segmented index; Xiegu X6200 uses a single raw byte index. The
        request is cmd29-wrapped only when the profile lists ``[0x1A, 0x03]``
        in its cmd29 routes. On dual-RX profiles that do not list the
        route but do declare VFO select codes (IC-9700), ``receiver=1``
        (SUB) is reached via the VFO-select fallback (mirroring
        :meth:`set_freq`) instead of silently reading MAIN. Profiles with
        neither a cmd29 route nor VFO select codes still raise
        ``CommandError``.

        Args:
            receiver: 0=MAIN, 1=SUB.

        Returns:
            Filter width in Hz.
        """
        self._check_connected()
        self._require_receiver(receiver, operation="get_filter_width")

        # CI-V 1A 03: 1-byte BCD index for every Icom rig (wfview-confirmed).
        # cmd29-wrapped only for receiver routing on dual-RX rigs that list
        # the route (IC-7610). IC-705/IC-9700 send the request directly on
        # MAIN; SUB without a cmd29 route uses the VFO-select fallback when
        # available, else is rejected below.
        if receiver != RECEIVER_MAIN and not self._profile.supports_cmd29(0x1A, 0x03):

            async def _action() -> CivFrame:
                civ = build_civ_frame(self._radio_addr, CONTROLLER_ADDR, 0x1A, sub=0x03)
                return await self._send_civ_expect(
                    civ,
                    key=f"get_filter_width:{receiver}",
                    dedupe=True,
                    label="get_filter_width",
                )

            resp = await self._run_with_receiver_vfo_fallback(
                receiver=receiver,
                operation="get_filter_width",
                action=_action,
            )
        else:
            self._require_cmd29_route(
                0x1A,
                0x03,
                receiver=receiver,
                operation="get_filter_width",
            )
            if self._profile.supports_cmd29(0x1A, 0x03):
                civ = self._commands.get_filter_width(
                    to_addr=self._radio_addr, receiver=receiver
                )
            else:
                civ = build_civ_frame(self._radio_addr, CONTROLLER_ADDR, 0x1A, sub=0x03)

            resp = await self._send_civ_expect(
                civ,
                key=f"get_filter_width:{receiver}",
                dedupe=True,
                label="get_filter_width",
            )
        if resp.command != 0x1A or resp.sub != 0x03:
            raise ValueError(
                f"Not a filter-width response: command 0x{resp.command:02x} "
                f"sub 0x{0 if resp.sub is None else resp.sub:02x}"
            )

        if self._profile.filter_width_encoding == "raw_byte_index":
            if not resp.data:
                raise ValueError("Filter-width response payload too short")
            value = resp.data[0]
        else:
            value = parse_level_response(
                resp,
                command=0x1A,
                sub=0x03,
                bcd_bytes=1,
            )

        target = self._radio_state.receiver("SUB" if receiver else "MAIN")
        mode_name = getattr(target, "mode", None)
        data_mode = int(getattr(target, "data_mode", 0) or 0)
        rule = self._profile.resolve_filter_rule(mode_name, data_mode=data_mode)
        if rule is not None and rule.segments:
            try:
                return filter_index_to_hz(value, segments=rule.segments)
            except ValueError:
                # Out-of-band index — return raw value rather than fail.
                return value
        return value

    async def set_mode(
        self, mode: Mode | str, filter_width: int | None = None, receiver: int = 0
    ) -> None:
        """Set the operating mode.

        Args:
            mode: Mode enum or string name (e.g. "USB", "LSB").
            filter_width: Optional filter number (1-3).
            receiver: 0=MAIN, 1=SUB.
        """
        self._check_connected()
        self._require_receiver(receiver, operation="set_mode")
        parsed_mode = self._coerce_mode(mode)

        if receiver == RECEIVER_MAIN:
            await self._set_mode_main(parsed_mode, filter_width=filter_width)
            return

        if self._profile.supports_cmd29(0x06):
            civ = self._commands.set_mode(
                parsed_mode,
                filter_width=filter_width,
                to_addr=self._radio_addr,
                receiver=receiver,
            )
            await self._send_civ_raw(civ, wait_response=False)
        else:
            await self._run_with_receiver_vfo_fallback(
                receiver=receiver,
                operation="set_mode",
                action=lambda: self._set_mode_main(
                    parsed_mode, filter_width=filter_width, update_cache=False
                ),
            )

        sub = self._radio_state.receiver("SUB")
        sub.mode = parsed_mode.name
        if filter_width is not None:
            sub.filter = filter_width

    async def get_data_mode(self) -> bool:
        """Get the IC-7610 DATA mode state (command 0x1A 0x06).

        Returns:
            True if DATA mode is active (DATA1/2/3), False if off.
        """
        self._check_connected()
        civ = self._commands.get_data_mode(to_addr=self._radio_addr)
        resp = await self._send_civ_expect(civ, label="get_data_mode")
        return parse_data_mode_response(resp)

    async def set_data_mode(self, on: int | bool, receiver: int = 0) -> None:
        """Set receiver DATA mode (command 0x1A 0x06).

        Args:
            on: False/0 to disable, True/1 to enable DATA1 mode, or an explicit
                DATA mode 0-3.
            receiver: 0 = main, 1 = sub.
        """
        self._check_connected()
        self._require_capability("data_mode", operation="set_data_mode")
        self._require_receiver(receiver, operation="set_data_mode")

        if receiver != RECEIVER_MAIN and not self._profile.supports_cmd29(0x1A, 0x06):

            async def _action() -> None:
                civ = self._commands.set_data_mode(
                    on, to_addr=self._radio_addr, receiver=RECEIVER_MAIN
                )
                resp = await self._send_civ_expect(civ, label="action")
                ack = parse_ack_nak(resp)
                if ack is False:
                    raise CommandError(
                        f"Radio rejected set_data_mode({on}, receiver={receiver})"
                    )

            await self._run_with_receiver_vfo_fallback(
                receiver=receiver,
                operation="set_data_mode",
                action=_action,
            )
            return

        self._require_cmd29_route(
            0x1A,
            0x06,
            receiver=receiver,
            operation="set_data_mode",
        )
        civ = self._commands.set_data_mode(
            on, to_addr=self._radio_addr, receiver=receiver
        )
        resp = await self._send_civ_expect(civ, label="action")
        ack = parse_ack_nak(resp)
        if ack is False:
            raise CommandError(
                f"Radio rejected set_data_mode({on}, receiver={receiver})"
            )

    def _parse_level(self, resp: "CivFrame") -> int:
        """Parse a level BCD response into an integer 0-255."""
        return _level_bcd_decode(resp.data)

    async def _get_bcd_level(
        self,
        civ: bytes,
        *,
        key: str,
        command: int,
        sub: int,
        prefix: bytes = b"",
        bcd_bytes: int = 2,
    ) -> int:
        """Send a GET command and parse a BCD-encoded integer response."""
        self._check_connected()
        resp = await self._send_civ_expect(
            civ, key=key, dedupe=True, label="get_bcd_level"
        )
        return parse_level_response(
            resp,
            command=command,
            sub=sub,
            prefix=prefix,
            bcd_bytes=bcd_bytes,
        )

    async def _get_bool_value(
        self,
        civ: bytes,
        *,
        key: str,
        command: int,
        sub: int,
        prefix: bytes = b"",
    ) -> bool:
        """Send a GET command and parse a boolean response."""
        self._check_connected()
        resp = await self._send_civ_expect(
            civ, key=key, dedupe=True, label="get_bool_value"
        )
        return parse_bool_response(resp, command=command, sub=sub, prefix=prefix)

    def _expect_shape(self, builder: Callable[..., bytes]) -> tuple[int, int, bytes]:
        """The ``(command, sub, prefix)`` a migrated getter's reply must match.

        Thin wrapper over `commands/bound.py: BoundCommands.expect` (MOR-2006
        §6 population 1): ``expect`` types ``sub`` as ``int | None`` per the
        wire-tuple contract, but every builder routed through here so far
        always has one -- the assertion fails loudly instead of reaching
        ``_get_bcd_level``/``_get_bool_value`` with the wrong type.
        """
        command, sub, prefix = self._commands.expect(builder)
        assert sub is not None, (
            f"{getattr(builder, '__qualname__', builder)}: map entry has no "
            "sub-command byte"
        )
        return command, sub, prefix

    async def _send_fire_and_forget(self, civ: bytes) -> None:
        """Send a fire-and-forget CI-V command after connection checks."""
        self._check_connected()
        await self._send_civ_raw(civ, wait_response=False)

    # commands/levels.py, migrated onto the bound command map (MOR-2006
    # Steps 5..N module 2, plan §4): requests go through
    # ``self._commands.<builder>`` and matcher-backed replies through
    # ``self._expect_shape`` (§6 population 1). ``get_rf_power``,
    # ``get_rf_gain`` and ``get_af_level`` parse their reply with
    # ``_level_bcd_decode``/``_parse_level`` directly, without checking the
    # reply's command/sub bytes, so they have no matcher shape to migrate.

    async def get_rf_power(self) -> int:
        """Get the RF power level (0-255).

        On timeout falls back to the state cache if populated.
        """
        self._check_connected()
        civ = self._commands.get_rf_power(to_addr=self._radio_addr)
        try:
            resp = await self._send_civ_expect(
                civ, key="get_rf_power", dedupe=True, label="get_rf_power"
            )
            level = _level_bcd_decode(resp.data)
            self._last_power = level
            self._state_cache.update_rf_power(level / 255.0)
            return level
        except TimeoutError:
            if (
                self._state_cache.is_fresh("rf_power", self._cache_ttl_rf_power)
                and self._state_cache.rf_power is not None
            ):
                cached_level = round(self._state_cache.rf_power * 255)
                logger.debug("get_rf_power: timeout, returning cached %d", cached_level)
                return cached_level
            raise

    async def set_rf_power(self, level: int) -> None:
        """Set the RF power level (0-255).

        Args:
            level: Power level 0-255.
        """
        self._check_connected()
        civ = self._commands.set_rf_power(level, to_addr=self._radio_addr)
        await self._send_civ_raw(civ, wait_response=False)
        self._last_power = level

    async def get_rf_gain(self, receiver: int = 0) -> int:
        """Read the current RF gain level (0-255)."""
        self._check_connected()
        self._require_receiver(receiver, operation="get_rf_gain")
        self._require_cmd29_route(
            0x14,
            0x02,
            receiver=receiver,
            operation="get_rf_gain",
        )
        cmd29 = self._profile.supports_cmd29(0x14, 0x02)
        civ = self._commands.get_rf_gain(
            to_addr=self._radio_addr, receiver=receiver, command29=cmd29
        )
        try:
            resp = await self._send_civ_expect(
                civ,
                key=f"get_rf_gain:{receiver}",
                dedupe=True,
                label="get_rf_gain",
            )
            return self._parse_level(resp)
        except TimeoutError:
            raise

    async def set_rf_gain(self, level: int, receiver: int = 0) -> None:
        """Set RF gain level (0-255)."""
        if not 0 <= level <= 255:
            raise ValueError(f"RF gain must be 0-255, got {level}")
        self._check_connected()
        self._require_capability("rf_gain", operation="set_rf_gain")
        self._require_receiver(receiver, operation="set_rf_gain")
        cmd29 = bool(self._level_command29("set_rf_gain", receiver=receiver))
        civ = self._commands.set_rf_gain(
            level, to_addr=self._radio_addr, receiver=receiver, command29=cmd29
        )
        await self._send_civ_raw(civ, wait_response=False)

    async def get_af_level(self, receiver: int = 0) -> int:
        """Read the current AF output level (0-255)."""
        self._check_connected()
        self._require_receiver(receiver, operation="get_af_level")
        self._require_cmd29_route(
            0x14,
            0x01,
            receiver=receiver,
            operation="get_af_level",
        )
        cmd29 = self._profile.supports_cmd29(0x14, 0x01)
        civ = self._commands.get_af_level(
            to_addr=self._radio_addr, receiver=receiver, command29=cmd29
        )
        try:
            resp = await self._send_civ_expect(
                civ,
                key=f"get_af_level:{receiver}",
                dedupe=True,
                label="get_af_level",
            )
            return self._parse_level(resp)
        except TimeoutError:
            raise

    async def set_af_level(self, level: int, receiver: int = 0) -> None:
        """Set AF output level (0-255)."""
        if not 0 <= level <= 255:
            raise ValueError(f"AF level must be 0-255, got {level}")
        self._check_connected()
        self._require_capability("af_level", operation="set_af_level")
        self._require_receiver(receiver, operation="set_af_level")
        cmd29 = bool(self._level_command29("set_af_level", receiver=receiver))
        civ = self._commands.set_af_level(
            level, to_addr=self._radio_addr, receiver=receiver, command29=cmd29
        )
        await self._send_civ_raw(civ, wait_response=False)

    async def set_squelch(self, level: int, receiver: int = 0) -> None:
        """Set squelch level (0-255, 0=open)."""
        if not 0 <= level <= 255:
            raise ValueError(f"Squelch level must be 0-255, got {level}")
        self._check_connected()
        self._require_capability("squelch", operation="set_squelch")
        self._require_receiver(receiver, operation="set_squelch")
        cmd29 = bool(self._level_command29("set_squelch", receiver=receiver))
        civ = self._commands.set_squelch(
            level, to_addr=self._radio_addr, receiver=receiver, command29=cmd29
        )
        await self._send_civ_raw(civ, wait_response=False)

    async def get_squelch(self, receiver: int = 0) -> int:
        """Read the current squelch level (0-255)."""
        self._check_connected()
        self._require_capability("squelch", operation="get_squelch")
        self._require_receiver(receiver, operation="get_squelch")
        self._require_cmd29_route(
            0x14,
            0x03,
            receiver=receiver,
            operation="get_squelch",
        )
        cmd29 = self._profile.supports_cmd29(0x14, 0x03)
        civ = self._commands.get_squelch(
            to_addr=self._radio_addr, receiver=receiver, command29=cmd29
        )
        command, sub, prefix = self._expect_shape(get_squelch)
        return await self._get_bcd_level(
            civ,
            key=f"get_squelch:{receiver}",
            command=command,
            sub=sub,
            prefix=prefix,
        )

    async def get_apf_type_level(self, receiver: int = RECEIVER_MAIN) -> int:
        """Read APF Type Level (0-255)."""
        self._require_receiver(receiver, operation="get_apf_type_level")
        self._require_cmd29_route(
            0x14, 0x05, receiver=receiver, operation="get_apf_type_level"
        )
        cmd29 = self._profile.supports_cmd29(0x14, 0x05)
        civ = self._commands.get_apf_type_level(
            to_addr=self._radio_addr, receiver=receiver, command29=cmd29
        )
        command, sub, prefix = self._expect_shape(get_apf_type_level)
        return await self._get_bcd_level(
            civ,
            key=f"get_apf_type_level:{receiver}",
            command=command,
            sub=sub,
            prefix=prefix,
        )

    async def set_apf_type_level(
        self, level: int, receiver: int = RECEIVER_MAIN
    ) -> None:
        """Set APF Type Level (0-255)."""
        self._require_receiver(receiver, operation="set_apf_type_level")
        self._require_cmd29_route(
            0x14, 0x05, receiver=receiver, operation="set_apf_type_level"
        )
        cmd29 = self._profile.supports_cmd29(0x14, 0x05)
        await self._send_fire_and_forget(
            self._commands.set_apf_type_level(
                level, to_addr=self._radio_addr, receiver=receiver, command29=cmd29
            )
        )

    async def get_nr_level(self, receiver: int = RECEIVER_MAIN) -> int:
        """Read NR Level (0-255)."""
        self._require_receiver(receiver, operation="get_nr_level")
        self._require_cmd29_route(
            0x14, 0x06, receiver=receiver, operation="get_nr_level"
        )
        cmd29 = self._profile.supports_cmd29(0x14, 0x06)
        civ = self._commands.get_nr_level(
            to_addr=self._radio_addr, receiver=receiver, command29=cmd29
        )
        command, sub, prefix = self._expect_shape(get_nr_level)
        return await self._get_bcd_level(
            civ, key=f"get_nr_level:{receiver}", command=command, sub=sub, prefix=prefix
        )

    async def set_nr_level(self, level: int, receiver: int = RECEIVER_MAIN) -> None:
        """Set NR Level (0-255)."""
        self._require_receiver(receiver, operation="set_nr_level")
        self._require_cmd29_route(
            0x14, 0x06, receiver=receiver, operation="set_nr_level"
        )
        cmd29 = self._profile.supports_cmd29(0x14, 0x06)
        await self._send_fire_and_forget(
            self._commands.set_nr_level(
                level, to_addr=self._radio_addr, receiver=receiver, command29=cmd29
            )
        )

    async def get_pbt_inner(self, receiver: int = RECEIVER_MAIN) -> int:
        """Read PBT Inner level (0-255)."""
        self._require_receiver(receiver, operation="get_pbt_inner")
        self._require_cmd29_route(
            0x14, 0x07, receiver=receiver, operation="get_pbt_inner"
        )
        cmd29 = self._profile.supports_cmd29(0x14, 0x07)
        civ = self._commands.get_pbt_inner(
            to_addr=self._radio_addr, receiver=receiver, command29=cmd29
        )
        command, sub, prefix = self._expect_shape(get_pbt_inner)
        return await self._get_bcd_level(
            civ,
            key=f"get_pbt_inner:{receiver}",
            command=command,
            sub=sub,
            prefix=prefix,
        )

    async def set_pbt_inner(self, level: int, receiver: int = RECEIVER_MAIN) -> None:
        """Set PBT Inner level (0-255)."""
        self._require_receiver(receiver, operation="set_pbt_inner")
        self._require_cmd29_route(
            0x14, 0x07, receiver=receiver, operation="set_pbt_inner"
        )
        cmd29 = self._profile.supports_cmd29(0x14, 0x07)
        await self._send_fire_and_forget(
            self._commands.set_pbt_inner(
                level, to_addr=self._radio_addr, receiver=receiver, command29=cmd29
            )
        )

    async def get_pbt_outer(self, receiver: int = RECEIVER_MAIN) -> int:
        """Read PBT Outer level (0-255)."""
        self._require_receiver(receiver, operation="get_pbt_outer")
        self._require_cmd29_route(
            0x14, 0x08, receiver=receiver, operation="get_pbt_outer"
        )
        cmd29 = self._profile.supports_cmd29(0x14, 0x08)
        civ = self._commands.get_pbt_outer(
            to_addr=self._radio_addr, receiver=receiver, command29=cmd29
        )
        command, sub, prefix = self._expect_shape(get_pbt_outer)
        return await self._get_bcd_level(
            civ,
            key=f"get_pbt_outer:{receiver}",
            command=command,
            sub=sub,
            prefix=prefix,
        )

    async def set_pbt_outer(self, level: int, receiver: int = RECEIVER_MAIN) -> None:
        """Set PBT Outer level (0-255)."""
        self._require_receiver(receiver, operation="set_pbt_outer")
        self._require_cmd29_route(
            0x14, 0x08, receiver=receiver, operation="set_pbt_outer"
        )
        cmd29 = self._profile.supports_cmd29(0x14, 0x08)
        await self._send_fire_and_forget(
            self._commands.set_pbt_outer(
                level, to_addr=self._radio_addr, receiver=receiver, command29=cmd29
            )
        )

    async def get_cw_pitch(self) -> int:
        """Read CW pitch in Hz."""
        command, sub, prefix = self._expect_shape(get_cw_pitch)
        level = await self._get_bcd_level(
            self._commands.get_cw_pitch(to_addr=self._radio_addr),
            key="get_cw_pitch",
            command=command,
            sub=sub,
            prefix=prefix,
        )
        return round((((600.0 / 255.0) * level) + 300) / 5.0) * 5

    async def set_cw_pitch(self, pitch_hz: int) -> None:
        """Set CW pitch in Hz."""
        await self._send_fire_and_forget(
            self._commands.set_cw_pitch(pitch_hz, to_addr=self._radio_addr)
        )

    async def get_mic_gain(self) -> int:
        """Read Mic Gain (0-255)."""
        command, sub, prefix = self._expect_shape(get_mic_gain)
        return await self._get_bcd_level(
            self._commands.get_mic_gain(to_addr=self._radio_addr),
            key="get_mic_gain",
            command=command,
            sub=sub,
            prefix=prefix,
        )

    async def set_mic_gain(self, level: int) -> None:
        """Set Mic Gain (0-255)."""
        await self._send_fire_and_forget(
            self._commands.set_mic_gain(level, to_addr=self._radio_addr)
        )

    async def get_key_speed(self) -> int:
        """Read key speed in WPM."""
        command, sub, prefix = self._expect_shape(get_key_speed)
        level = await self._get_bcd_level(
            self._commands.get_key_speed(to_addr=self._radio_addr),
            key="get_key_speed",
            command=command,
            sub=sub,
            prefix=prefix,
        )
        return round((level / 6.071) + 6)

    async def set_key_speed(self, wpm: int) -> None:
        """Set key speed in WPM."""
        await self._send_fire_and_forget(
            self._commands.set_key_speed(wpm, to_addr=self._radio_addr)
        )

    async def get_notch_filter(self, receiver: int = RECEIVER_MAIN) -> int:
        """Read notch filter level (0-255)."""
        self._require_receiver(receiver, operation="get_notch_filter")
        self._require_cmd29_route(
            0x14, 0x0D, receiver=receiver, operation="get_notch_filter"
        )
        cmd29 = self._profile.supports_cmd29(0x14, 0x0D)
        civ = self._commands.get_notch_filter(
            to_addr=self._radio_addr, receiver=receiver, command29=cmd29
        )
        command, sub, prefix = self._expect_shape(get_notch_filter)
        return await self._get_bcd_level(
            civ,
            key=f"get_notch_filter:{receiver}",
            command=command,
            sub=sub,
            prefix=prefix,
        )

    async def set_notch_filter(self, level: int, receiver: int = RECEIVER_MAIN) -> None:
        """Set notch filter level (0-255)."""
        self._require_receiver(receiver, operation="set_notch_filter")
        self._require_cmd29_route(
            0x14, 0x0D, receiver=receiver, operation="set_notch_filter"
        )
        cmd29 = self._profile.supports_cmd29(0x14, 0x0D)
        await self._send_fire_and_forget(
            self._commands.set_notch_filter(
                level, to_addr=self._radio_addr, receiver=receiver, command29=cmd29
            )
        )

    async def get_compressor_level(self) -> int:
        """Read compressor level (0-255)."""
        command, sub, prefix = self._expect_shape(get_compressor_level)
        return await self._get_bcd_level(
            self._commands.get_compressor_level(to_addr=self._radio_addr),
            key="get_compressor_level",
            command=command,
            sub=sub,
            prefix=prefix,
        )

    async def set_compressor_level(self, level: int) -> None:
        """Set compressor level (0-255)."""
        await self._send_fire_and_forget(
            self._commands.set_compressor_level(level, to_addr=self._radio_addr)
        )

    async def get_break_in_delay(self) -> int:
        """Read break-in delay level (0-255)."""
        command, sub, prefix = self._expect_shape(get_break_in_delay)
        return await self._get_bcd_level(
            self._commands.get_break_in_delay(to_addr=self._radio_addr),
            key="get_break_in_delay",
            command=command,
            sub=sub,
            prefix=prefix,
        )

    async def set_break_in_delay(self, level: int) -> None:
        """Set break-in delay level (0-255)."""
        await self._send_fire_and_forget(
            self._commands.set_break_in_delay(level, to_addr=self._radio_addr)
        )

    async def get_nb_level(self, receiver: int = RECEIVER_MAIN) -> int:
        """Read NB level (0-255)."""
        self._require_receiver(receiver, operation="get_nb_level")
        self._require_cmd29_route(
            0x14, 0x12, receiver=receiver, operation="get_nb_level"
        )
        cmd29 = self._profile.supports_cmd29(0x14, 0x12)
        civ = self._commands.get_nb_level(
            to_addr=self._radio_addr, receiver=receiver, command29=cmd29
        )
        command, sub, prefix = self._expect_shape(get_nb_level)
        return await self._get_bcd_level(
            civ, key=f"get_nb_level:{receiver}", command=command, sub=sub, prefix=prefix
        )

    async def set_nb_level(self, level: int, receiver: int = RECEIVER_MAIN) -> None:
        """Set NB level (0-255)."""
        self._require_receiver(receiver, operation="set_nb_level")
        self._require_cmd29_route(
            0x14, 0x12, receiver=receiver, operation="set_nb_level"
        )
        cmd29 = self._profile.supports_cmd29(0x14, 0x12)
        await self._send_fire_and_forget(
            self._commands.set_nb_level(
                level, to_addr=self._radio_addr, receiver=receiver, command29=cmd29
            )
        )

    async def get_digisel_shift(self, receiver: int = RECEIVER_MAIN) -> int:
        """Read DIGI-SEL Shift (0-255)."""
        self._require_receiver(receiver, operation="get_digisel_shift")
        self._require_cmd29_route(
            0x14, 0x13, receiver=receiver, operation="get_digisel_shift"
        )
        cmd29 = self._profile.supports_cmd29(0x14, 0x13)
        civ = self._commands.get_digisel_shift(
            to_addr=self._radio_addr, receiver=receiver, command29=cmd29
        )
        command, sub, prefix = self._expect_shape(get_digisel_shift)
        return await self._get_bcd_level(
            civ,
            key=f"get_digisel_shift:{receiver}",
            command=command,
            sub=sub,
            prefix=prefix,
        )

    async def set_digisel_shift(
        self, level: int, receiver: int = RECEIVER_MAIN
    ) -> None:
        """Set DIGI-SEL Shift (0-255)."""
        self._require_receiver(receiver, operation="set_digisel_shift")
        self._require_cmd29_route(
            0x14, 0x13, receiver=receiver, operation="set_digisel_shift"
        )
        cmd29 = self._profile.supports_cmd29(0x14, 0x13)
        await self._send_fire_and_forget(
            self._commands.set_digisel_shift(
                level, to_addr=self._radio_addr, receiver=receiver, command29=cmd29
            )
        )

    async def get_drive_gain(self) -> int:
        """Read drive gain (0-255)."""
        command, sub, prefix = self._expect_shape(get_drive_gain)
        return await self._get_bcd_level(
            self._commands.get_drive_gain(to_addr=self._radio_addr),
            key="get_drive_gain",
            command=command,
            sub=sub,
            prefix=prefix,
        )

    async def set_drive_gain(self, level: int) -> None:
        """Set drive gain (0-255)."""
        await self._send_fire_and_forget(
            self._commands.set_drive_gain(level, to_addr=self._radio_addr)
        )

    async def get_monitor_gain(self) -> int:
        """Read monitor gain (0-255)."""
        command, sub, prefix = self._expect_shape(get_monitor_gain)
        return await self._get_bcd_level(
            self._commands.get_monitor_gain(to_addr=self._radio_addr),
            key="get_monitor_gain",
            command=command,
            sub=sub,
            prefix=prefix,
        )

    async def set_monitor_gain(self, level: int) -> None:
        """Set monitor gain (0-255)."""
        await self._send_fire_and_forget(
            self._commands.set_monitor_gain(level, to_addr=self._radio_addr)
        )

    async def get_vox_gain(self) -> int:
        """Read VOX gain (0-255)."""
        command, sub, prefix = self._expect_shape(get_vox_gain)
        return await self._get_bcd_level(
            self._commands.get_vox_gain(to_addr=self._radio_addr),
            key="get_vox_gain",
            command=command,
            sub=sub,
            prefix=prefix,
        )

    async def set_vox_gain(self, level: int) -> None:
        """Set VOX gain (0-255)."""
        await self._send_fire_and_forget(
            self._commands.set_vox_gain(level, to_addr=self._radio_addr)
        )

    async def get_anti_vox_gain(self) -> int:
        """Read anti-VOX gain (0-255)."""
        command, sub, prefix = self._expect_shape(get_anti_vox_gain)
        return await self._get_bcd_level(
            self._commands.get_anti_vox_gain(to_addr=self._radio_addr),
            key="get_anti_vox_gain",
            command=command,
            sub=sub,
            prefix=prefix,
        )

    async def set_anti_vox_gain(self, level: int) -> None:
        """Set anti-VOX gain (0-255)."""
        await self._send_fire_and_forget(
            self._commands.set_anti_vox_gain(level, to_addr=self._radio_addr)
        )

    async def get_ref_adjust(self) -> int:
        """Read REF Adjust (0-511)."""
        command, sub, prefix = self._expect_shape(get_ref_adjust)
        return await self._get_bcd_level(
            self._commands.get_ref_adjust(to_addr=self._radio_addr),
            key="get_ref_adjust",
            command=command,
            sub=sub,
            prefix=prefix,
        )

    async def set_ref_adjust(self, value: int) -> None:
        """Set REF Adjust (0-511)."""
        await self._send_fire_and_forget(
            self._commands.set_ref_adjust(value, to_addr=self._radio_addr)
        )

    async def get_dash_ratio(self) -> int:
        """Read dash ratio (28-45)."""
        command, sub, prefix = self._expect_shape(get_dash_ratio)
        return await self._get_bcd_level(
            self._commands.get_dash_ratio(to_addr=self._radio_addr),
            key="get_dash_ratio",
            command=command,
            sub=sub,
            prefix=prefix,
            bcd_bytes=1,
        )

    async def set_dash_ratio(self, value: int) -> None:
        """Set dash ratio (28-45)."""
        await self._send_fire_and_forget(
            self._commands.set_dash_ratio(value, to_addr=self._radio_addr)
        )

    async def get_nb_depth(self) -> int:
        """Read NB depth (0-9)."""
        command, sub, prefix = self._expect_shape(get_nb_depth)
        return await self._get_bcd_level(
            self._commands.get_nb_depth(to_addr=self._radio_addr),
            key="get_nb_depth",
            command=command,
            sub=sub,
            prefix=prefix,
            bcd_bytes=1,
        )

    async def set_nb_depth(self, value: int) -> None:
        """Set NB depth (0-9)."""
        await self._send_fire_and_forget(
            self._commands.set_nb_depth(value, to_addr=self._radio_addr)
        )

    async def get_nb_width(self) -> int:
        """Read NB width (0-255)."""
        command, sub, prefix = self._expect_shape(get_nb_width)
        return await self._get_bcd_level(
            self._commands.get_nb_width(to_addr=self._radio_addr),
            key="get_nb_width",
            command=command,
            sub=sub,
            prefix=prefix,
        )

    async def set_nb_width(self, value: int) -> None:
        """Set NB width (0-255)."""
        await self._send_fire_and_forget(
            self._commands.set_nb_width(value, to_addr=self._radio_addr)
        )

    async def get_vox_delay(self) -> int:
        """Read VOX delay (0-20, units of 0.1s)."""
        command, sub, prefix = self._expect_shape(get_vox_delay)
        return await self._get_bcd_level(
            self._commands.get_vox_delay(to_addr=self._radio_addr),
            key="get_vox_delay",
            command=command,
            sub=sub,
            prefix=prefix,
            bcd_bytes=1,
        )

    async def set_vox_delay(self, level: int) -> None:
        """Set VOX delay (0-20, units of 0.1s)."""
        await self._send_fire_and_forget(
            self._commands.set_vox_delay(level, to_addr=self._radio_addr)
        )

    async def get_af_mute(self, receiver: int = RECEIVER_MAIN) -> bool:
        """Read AF mute status."""
        self._require_receiver(receiver, operation="get_af_mute")
        self._require_cmd29_route(
            0x1A, 0x09, receiver=receiver, operation="get_af_mute"
        )
        cmd29 = self._profile.supports_cmd29(0x1A, 0x09)
        civ = self._commands.get_af_mute(
            to_addr=self._radio_addr, receiver=receiver, command29=cmd29
        )
        command, sub, _ = self._expect_shape(get_af_mute)
        return await self._get_bool_value(
            civ, key=f"get_af_mute:{receiver}", command=command, sub=sub
        )

    async def set_af_mute(self, on: bool, receiver: int = RECEIVER_MAIN) -> None:
        """Set AF mute status."""
        self._require_receiver(receiver, operation="set_af_mute")
        self._require_cmd29_route(
            0x1A, 0x09, receiver=receiver, operation="set_af_mute"
        )
        cmd29 = self._profile.supports_cmd29(0x1A, 0x09)
        await self._send_fire_and_forget(
            self._commands.set_af_mute(
                on, to_addr=self._radio_addr, receiver=receiver, command29=cmd29
            )
        )

    async def get_s_meter_sql_status(self, receiver: int = RECEIVER_MAIN) -> bool:
        """Read S-meter squelch status for the selected receiver."""
        self._require_receiver(receiver, operation="get_s_meter_sql_status")
        self._require_cmd29_route(
            0x15, 0x01, receiver=receiver, operation="get_s_meter_sql_status"
        )
        cmd29 = self._profile.supports_cmd29(0x15, 0x01)
        civ = self._commands.get_s_meter_sql_status(
            to_addr=self._radio_addr, receiver=receiver, command29=cmd29
        )
        command, sub, _ = self._expect_shape(get_s_meter_sql_status)
        return await self._get_bool_value(
            civ,
            key=f"get_s_meter_sql_status:{receiver}",
            command=command,
            sub=sub,
        )

    async def get_overflow_status(self) -> bool:
        """Read OVF indicator status."""
        civ = self._commands.get_overflow_status(to_addr=self._radio_addr)
        command, sub, _ = self._expect_shape(get_overflow_status)
        return await self._get_bool_value(
            civ,
            key="get_overflow_status",
            command=command,
            sub=sub,
        )

    async def get_agc(self, receiver: int = RECEIVER_MAIN) -> int:
        """Read AGC mode.

        Returns the raw profile-declared value (see the radio's ``[agc]
        modes`` in its TOML profile), not an ``AgcMode`` enum member.
        ``AgcMode`` is the IC-7610/generic Icom FAST/MID/SLOW shape — a
        radio with a differently-shaped domain (e.g. the X6200's
        OFF=0/FAST=1/SLOW=2/AUTO=3) would otherwise raise on OFF (0 is not
        a member) or be silently mislabeled for SLOW/AUTO (index 2/3 mean
        something else there). Label mapping is data-side (``[agc]
        labels``), mirroring ``set_agc``'s MOR-1522 fix (MOR-1529).
        """
        self._require_receiver(receiver, operation="get_agc")
        self._require_cmd29_route(0x16, 0x12, receiver=receiver, operation="get_agc")
        cmd29 = self._profile.supports_cmd29(0x16, 0x12)
        command, sub, _ = self._expect_shape(get_agc)
        value = await self._get_bcd_level(
            self._commands.get_agc(
                to_addr=self._radio_addr, receiver=receiver, command29=cmd29
            ),
            key=f"get_agc:{receiver}",
            command=command,
            sub=sub,
            bcd_bytes=1,
        )
        agc_modes = self._profile.agc_modes
        if agc_modes is not None and value not in agc_modes:
            raise ValueError(
                f"AGC mode {value} reported by radio is not in declared domain "
                f"{sorted(agc_modes)} for {self._profile.model}"
            )
        return value

    async def set_agc(self, mode: AgcMode | int, receiver: int = RECEIVER_MAIN) -> None:
        """Set AGC mode.

        Valid values are declared per radio by the profile's ``[agc] modes``
        (e.g. IC-7300/IC-7610 offer FAST/MID/SLOW only; the X6200
        additionally declares OFF/AUTO). ``AgcMode`` is the IC-7610/generic
        Icom FAST/MID/SLOW enum and is no longer used to gate the value —
        a profile-declared value outside that enum (e.g. X6200's OFF=0)
        must not be rejected just because it isn't an IC-7610 mode (MOR-1522).
        """
        self._require_receiver(receiver, operation="set_agc")
        self._require_cmd29_route(0x16, 0x12, receiver=receiver, operation="set_agc")
        cmd29 = self._profile.supports_cmd29(0x16, 0x12)
        mode_int = int(mode)
        agc_modes = self._profile.agc_modes
        if agc_modes is not None and mode_int not in agc_modes:
            raise ValueError(
                f"AGC mode must be one of {sorted(agc_modes)} for "
                f"{self._profile.model}, got {mode_int}"
            )
        await self._send_fire_and_forget(
            self._commands.set_agc(
                mode_int, to_addr=self._radio_addr, receiver=receiver, command29=cmd29
            )
        )

    async def get_audio_peak_filter(
        self, receiver: int = RECEIVER_MAIN
    ) -> AudioPeakFilter:
        """Read audio peak filter mode."""
        self._require_receiver(receiver, operation="get_audio_peak_filter")
        self._require_cmd29_route(
            0x16, 0x32, receiver=receiver, operation="get_audio_peak_filter"
        )
        cmd29 = self._profile.supports_cmd29(0x16, 0x32)
        command, sub, _ = self._expect_shape(get_audio_peak_filter)
        value = await self._get_bcd_level(
            self._commands.get_audio_peak_filter(
                to_addr=self._radio_addr, receiver=receiver, command29=cmd29
            ),
            key=f"get_audio_peak_filter:{receiver}",
            command=command,
            sub=sub,
            bcd_bytes=1,
        )
        return AudioPeakFilter(value)

    async def set_audio_peak_filter(
        self,
        mode: AudioPeakFilter | int,
        receiver: int = RECEIVER_MAIN,
    ) -> None:
        """Set audio peak filter mode."""
        self._require_receiver(receiver, operation="set_audio_peak_filter")
        self._require_cmd29_route(
            0x16, 0x32, receiver=receiver, operation="set_audio_peak_filter"
        )
        cmd29 = self._profile.supports_cmd29(0x16, 0x32)
        apf = AudioPeakFilter(mode)
        await self._send_fire_and_forget(
            self._commands.set_audio_peak_filter(
                apf, to_addr=self._radio_addr, receiver=receiver, command29=cmd29
            )
        )

    async def get_auto_notch(self, receiver: int = RECEIVER_MAIN) -> bool:
        """Read auto-notch status."""
        self._require_receiver(receiver, operation="get_auto_notch")
        self._require_cmd29_route(
            0x16, 0x41, receiver=receiver, operation="get_auto_notch"
        )
        cmd29 = self._profile.supports_cmd29(0x16, 0x41)
        civ = self._commands.get_auto_notch(
            to_addr=self._radio_addr, receiver=receiver, command29=cmd29
        )
        command, sub, _ = self._expect_shape(get_auto_notch)
        return await self._get_bool_value(
            civ,
            key=f"get_auto_notch:{receiver}",
            command=command,
            sub=sub,
        )

    async def set_auto_notch(self, on: bool, receiver: int = RECEIVER_MAIN) -> None:
        """Set auto-notch status."""
        self._require_receiver(receiver, operation="set_auto_notch")
        self._require_cmd29_route(
            0x16, 0x41, receiver=receiver, operation="set_auto_notch"
        )
        cmd29 = self._profile.supports_cmd29(0x16, 0x41)
        await self._send_fire_and_forget(
            self._commands.set_auto_notch(
                on, to_addr=self._radio_addr, receiver=receiver, command29=cmd29
            )
        )

    async def get_compressor(self) -> bool:
        """Read speech compressor status."""
        civ = self._commands.get_compressor(to_addr=self._radio_addr)
        command, sub, _ = self._expect_shape(get_compressor)
        return await self._get_bool_value(
            civ, key="get_compressor", command=command, sub=sub
        )

    async def set_compressor(self, on: bool) -> None:
        """Set speech compressor status."""
        await self._send_fire_and_forget(
            self._commands.set_compressor(on, to_addr=self._radio_addr)
        )

    async def get_monitor(self) -> bool:
        """Read monitor status."""
        civ = self._commands.get_monitor(to_addr=self._radio_addr)
        command, sub, _ = self._expect_shape(get_monitor)
        return await self._get_bool_value(
            civ, key="get_monitor", command=command, sub=sub
        )

    async def set_monitor(self, on: bool) -> None:
        """Set monitor status."""
        await self._send_fire_and_forget(
            self._commands.set_monitor(on, to_addr=self._radio_addr)
        )

    async def get_vox(self) -> bool:
        """Read VOX status."""
        civ = self._commands.get_vox(to_addr=self._radio_addr)
        command, sub, _ = self._expect_shape(get_vox)
        return await self._get_bool_value(civ, key="get_vox", command=command, sub=sub)

    async def set_vox(self, on: bool) -> None:
        """Set VOX status."""
        await self._send_fire_and_forget(
            self._commands.set_vox(on, to_addr=self._radio_addr)
        )

    async def get_break_in(self) -> BreakInMode:
        """Read break-in mode."""
        command, sub, _ = self._expect_shape(get_break_in)
        value = await self._get_bcd_level(
            self._commands.get_break_in(to_addr=self._radio_addr),
            key="get_break_in",
            command=command,
            sub=sub,
            bcd_bytes=1,
        )
        return BreakInMode(value)

    async def set_break_in(self, mode: BreakInMode | int) -> None:
        """Set break-in mode.

        Valid values are declared per radio by the profile's ``[break_in]
        values`` (e.g. IC-705/IC-7300/IC-9700/IC-7610 all offer OFF/SEMI/
        FULL). Unlike ``set_agc``/``set_preamp`` (MOR-1522/MOR-1523), a
        missing domain here is NOT treated as "unvalidated, pass through" —
        some profiles (X6100/X6200) advertise the ``break_in`` capability
        but have no trustworthy in-repo source for a value domain (see the
        ``rigs/x6200.toml``/``rigs/x6100.toml`` ``[capabilities]`` notes),
        so this refuses any value rather than guess (MOR-1534).

        Raises:
            ValueError: If the profile declares no break-in domain, or if
                ``mode`` is not in the profile's declared domain.
        """
        mode_int = int(mode)
        break_in_modes = self._profile.break_in_modes
        if break_in_modes is None:
            raise ValueError(
                f"No break-in value domain declared for {self._profile.model}; "
                "refusing to set an unvalidated break-in mode"
            )
        if mode_int not in break_in_modes:
            raise ValueError(
                f"Break-in mode must be one of {sorted(break_in_modes)} for "
                f"{self._profile.model}, got {mode_int}"
            )
        await self._send_fire_and_forget(
            self._commands.set_break_in(mode_int, to_addr=self._radio_addr)
        )

    async def get_manual_notch(self, receiver: int = RECEIVER_MAIN) -> bool:
        """Read manual-notch status."""
        self._require_receiver(receiver, operation="get_manual_notch")
        self._require_cmd29_route(
            0x16, 0x48, receiver=receiver, operation="get_manual_notch"
        )
        cmd29 = self._profile.supports_cmd29(0x16, 0x48)
        civ = self._commands.get_manual_notch(
            to_addr=self._radio_addr, receiver=receiver, command29=cmd29
        )
        command, sub, _ = self._expect_shape(get_manual_notch)
        return await self._get_bool_value(
            civ,
            key=f"get_manual_notch:{receiver}",
            command=command,
            sub=sub,
        )

    async def set_manual_notch(self, on: bool, receiver: int = RECEIVER_MAIN) -> None:
        """Set manual-notch status."""
        self._require_receiver(receiver, operation="set_manual_notch")
        self._require_cmd29_route(
            0x16, 0x48, receiver=receiver, operation="set_manual_notch"
        )
        cmd29 = self._profile.supports_cmd29(0x16, 0x48)
        await self._send_fire_and_forget(
            self._commands.set_manual_notch(
                on, to_addr=self._radio_addr, receiver=receiver, command29=cmd29
            )
        )

    async def get_manual_notch_width(self, receiver: int = RECEIVER_MAIN) -> int:
        """Read manual notch width (0=WIDE, 1=MID, 2=NAR)."""
        self._require_receiver(receiver, operation="get_manual_notch_width")
        self._require_cmd29_route(
            0x16, 0x57, receiver=receiver, operation="get_manual_notch_width"
        )
        cmd29 = self._profile.supports_cmd29(0x16, 0x57)
        civ = self._commands.get_manual_notch_width(
            to_addr=self._radio_addr, receiver=receiver, command29=cmd29
        )
        command, sub, _ = self._expect_shape(get_manual_notch_width)
        return await self._get_bcd_level(
            civ,
            key=f"get_manual_notch_width:{receiver}",
            command=command,
            sub=sub,
            bcd_bytes=1,
        )

    async def set_manual_notch_width(
        self, value: int, receiver: int = RECEIVER_MAIN
    ) -> None:
        """Set manual notch width.

        Valid values are declared per radio by the profile's ``[notch]
        width_values`` (e.g. IC-705/IC-7300/IC-9700/IC-7610 all declare
        0=WIDE/1=MID/2=NAR). A profile that declares no width domain is
        unvalidated here (permissive), mirroring ``set_agc``/``set_preamp``
        (MOR-1522/MOR-1523) — the ``notch`` capability covers auto-notch
        too, so its absence does not by itself mean this control is wrong
        to call (MOR-1534).

        Raises:
            ValueError: If the profile declares a width domain and
                ``value`` is not in it.
        """
        self._require_receiver(receiver, operation="set_manual_notch_width")
        self._require_cmd29_route(
            0x16, 0x57, receiver=receiver, operation="set_manual_notch_width"
        )
        notch_width_values = self._profile.notch_width_values
        if notch_width_values is not None and value not in notch_width_values:
            raise ValueError(
                f"Manual notch width must be one of {sorted(notch_width_values)} "
                f"for {self._profile.model}, got {value}"
            )
        cmd29 = self._profile.supports_cmd29(0x16, 0x57)
        await self._send_fire_and_forget(
            self._commands.set_manual_notch_width(
                value, to_addr=self._radio_addr, receiver=receiver, command29=cmd29
            )
        )

    async def get_twin_peak_filter(self, receiver: int = RECEIVER_MAIN) -> bool:
        """Read twin peak filter status."""
        self._require_receiver(receiver, operation="get_twin_peak_filter")
        self._require_cmd29_route(
            0x16, 0x4F, receiver=receiver, operation="get_twin_peak_filter"
        )
        cmd29 = self._profile.supports_cmd29(0x16, 0x4F)
        civ = self._commands.get_twin_peak_filter(
            to_addr=self._radio_addr, receiver=receiver, command29=cmd29
        )
        command, sub, _ = self._expect_shape(get_twin_peak_filter)
        return await self._get_bool_value(
            civ,
            key=f"get_twin_peak_filter:{receiver}",
            command=command,
            sub=sub,
        )

    async def set_twin_peak_filter(
        self, on: bool, receiver: int = RECEIVER_MAIN
    ) -> None:
        """Set twin peak filter status."""
        self._require_receiver(receiver, operation="set_twin_peak_filter")
        self._require_cmd29_route(
            0x16, 0x4F, receiver=receiver, operation="set_twin_peak_filter"
        )
        cmd29 = self._profile.supports_cmd29(0x16, 0x4F)
        await self._send_fire_and_forget(
            self._commands.set_twin_peak_filter(
                on, to_addr=self._radio_addr, receiver=receiver, command29=cmd29
            )
        )

    async def get_dial_lock(self) -> bool:
        """Read dial lock status."""
        civ = self._commands.get_dial_lock(to_addr=self._radio_addr)
        command, sub, _ = self._expect_shape(get_dial_lock)
        return await self._get_bool_value(
            civ, key="get_dial_lock", command=command, sub=sub
        )

    async def set_dial_lock(self, on: bool) -> None:
        """Set dial lock status."""
        await self._send_fire_and_forget(
            self._commands.set_dial_lock(on, to_addr=self._radio_addr)
        )

    async def get_filter_shape(self, receiver: int = RECEIVER_MAIN) -> FilterShape:
        """Read DSP IF filter shape."""
        self._require_receiver(receiver, operation="get_filter_shape")
        self._require_cmd29_route(
            0x16, 0x56, receiver=receiver, operation="get_filter_shape"
        )
        cmd29 = self._profile.supports_cmd29(0x16, 0x56)
        command, sub, _ = self._expect_shape(get_filter_shape)
        value = await self._get_bcd_level(
            self._commands.get_filter_shape(
                to_addr=self._radio_addr, receiver=receiver, command29=cmd29
            ),
            key=f"get_filter_shape:{receiver}",
            command=command,
            sub=sub,
            bcd_bytes=1,
        )
        return FilterShape(value)

    async def set_filter_shape(
        self,
        shape: FilterShape | int,
        receiver: int = RECEIVER_MAIN,
    ) -> None:
        """Set DSP IF filter shape.

        Valid values are declared per radio by the profile's
        ``[filter_shape] values`` (IC-705/IC-7300/IC-9700/IC-7610 all
        declare 0=SHARP/1=SOFT). A profile that declares no domain at all
        (every radio outside that family) is unvalidated here (permissive),
        mirroring ``set_agc``/``set_preamp`` (MOR-1522/MOR-1523) — none of
        those radios advertise the ``filter_shape`` capability, so this
        path is unreachable for them today (MOR-1534).

        Raises:
            ValueError: If the profile declares a domain and ``shape`` is
                not in it.
        """
        self._require_receiver(receiver, operation="set_filter_shape")
        self._require_cmd29_route(
            0x16, 0x56, receiver=receiver, operation="set_filter_shape"
        )
        shape_int = int(shape)
        filter_shape_values = self._profile.filter_shape_values
        if filter_shape_values is not None and shape_int not in filter_shape_values:
            raise ValueError(
                f"Filter shape must be one of {sorted(filter_shape_values)} for "
                f"{self._profile.model}, got {shape_int}"
            )
        cmd29 = self._profile.supports_cmd29(0x16, 0x56)
        await self._send_fire_and_forget(
            self._commands.set_filter_shape(
                shape_int,
                to_addr=self._radio_addr,
                receiver=receiver,
                command29=cmd29,
            )
        )

    async def get_ssb_tx_bandwidth(self) -> SsbTxBandwidth:
        """Read SSB transmit bandwidth preset."""
        command, sub, _ = self._expect_shape(get_ssb_tx_bandwidth)
        value = await self._get_bcd_level(
            self._commands.get_ssb_tx_bandwidth(to_addr=self._radio_addr),
            key="get_ssb_tx_bandwidth",
            command=command,
            sub=sub,
            bcd_bytes=1,
        )
        return SsbTxBandwidth(value)

    async def set_ssb_tx_bandwidth(self, bandwidth: SsbTxBandwidth | int) -> None:
        """Set SSB transmit bandwidth preset.

        Valid values are declared per radio by the profile's
        ``[ssb_tx_bw] values`` (IC-705/IC-7300/IC-9700/IC-7610 all declare
        0=WIDE/1=MID/2=NAR). A profile that declares no domain at all
        (every radio outside that family) is unvalidated here (permissive),
        mirroring ``set_agc``/``set_preamp`` (MOR-1522/MOR-1523) — none of
        those radios advertise the ``ssb_tx_bw`` capability, so this path
        is unreachable for them today (MOR-1534).

        Raises:
            ValueError: If the profile declares a domain and ``bandwidth``
                is not in it.
        """
        bandwidth_int = int(bandwidth)
        ssb_tx_bw_values = self._profile.ssb_tx_bw_values
        if ssb_tx_bw_values is not None and bandwidth_int not in ssb_tx_bw_values:
            raise ValueError(
                f"SSB TX bandwidth must be one of {sorted(ssb_tx_bw_values)} for "
                f"{self._profile.model}, got {bandwidth_int}"
            )
        await self._send_fire_and_forget(
            self._commands.set_ssb_tx_bandwidth(bandwidth_int, to_addr=self._radio_addr)
        )

    async def get_main_sub_tracking(self) -> bool:
        """Read Main/Sub Tracking status."""
        civ = self._commands.get_main_sub_tracking(to_addr=self._radio_addr)
        command, sub, _ = self._expect_shape(_get_main_sub_tracking_cmd)
        return await self._get_bool_value(
            civ, key="get_main_sub_tracking", command=command, sub=sub
        )

    async def set_main_sub_tracking(self, on: bool) -> None:
        """Set Main/Sub Tracking status."""
        await self._send_fire_and_forget(
            self._commands.set_main_sub_tracking(on, to_addr=self._radio_addr)
        )

    async def get_agc_time_constant(self, receiver: int = RECEIVER_MAIN) -> int:
        """Read AGC time constant preset (0-13)."""
        self._require_receiver(receiver, operation="get_agc_time_constant")
        self._require_cmd29_route(
            0x1A, 0x04, receiver=receiver, operation="get_agc_time_constant"
        )
        cmd29 = self._profile.supports_cmd29(0x1A, 0x04)
        command, sub, _ = self._expect_shape(get_agc_time_constant)
        return await self._get_bcd_level(
            self._commands.get_agc_time_constant(
                to_addr=self._radio_addr, receiver=receiver, command29=cmd29
            ),
            key=f"get_agc_time_constant:{receiver}",
            command=command,
            sub=sub,
            bcd_bytes=1,
        )

    async def set_agc_time_constant(
        self, value: int, receiver: int = RECEIVER_MAIN
    ) -> None:
        """Set AGC time constant preset (0-13)."""
        self._require_receiver(receiver, operation="set_agc_time_constant")
        self._require_cmd29_route(
            0x1A, 0x04, receiver=receiver, operation="set_agc_time_constant"
        )
        cmd29 = self._profile.supports_cmd29(0x1A, 0x04)
        await self._send_fire_and_forget(
            self._commands.set_agc_time_constant(
                value,
                to_addr=self._radio_addr,
                receiver=receiver,
                command29=cmd29,
            )
        )

    async def get_s_meter(self) -> int:
        """Read the S-meter value (0-255)."""
        self._check_connected()
        civ = self._commands.get_s_meter(to_addr=self._radio_addr)
        resp = await self._send_civ_expect(civ, label="get_s_meter")
        return parse_meter_response(resp)

    async def get_swr(self) -> float:
        """Read the SWR as a calibrated ratio (>= 1.0).

        Uses the piecewise-linear table defined in
        ``[[meters.swr.calibration]]`` of the active rig profile. Falls
        back to a legacy linear approximation when no calibration table
        is configured.

        For the raw 0–255 BCD reading (e.g. for charts that need the
        unscaled value) use :meth:`get_swr_meter`.
        """
        self._check_connected()
        civ = self._commands.get_swr(to_addr=self._radio_addr)
        resp = await self._send_civ_expect(civ, label="get_swr")
        raw = parse_meter_response(resp)
        return interpolate_swr(raw, self._profile.meter_calibrations)

    async def get_swr_meter(self) -> int:
        """Read the raw SWR meter value (0-255).

        Mirrors the Yaesu ``*_meter`` naming on ``MetersCapable``. For a
        calibrated SWR ratio (>= 1.0) use :meth:`get_swr`.
        """
        self._check_connected()
        civ = self._commands.get_swr(to_addr=self._radio_addr)
        resp = await self._send_civ_expect(civ, label="get_swr_meter")
        return parse_meter_response(resp)

    async def get_alc_meter(self) -> int:
        """Read the ALC meter value (raw 0-255)."""
        self._check_connected()
        civ = self._commands.get_alc(to_addr=self._radio_addr)
        resp = await self._send_civ_expect(civ, label="get_alc_meter")
        return parse_meter_response(resp)

    async def set_ptt(self, on: bool) -> None:
        """Toggle PTT (Push-To-Talk).

        Fire-and-forget: the command is sent at IMMEDIATE priority without
        blocking for an ACK. PTT state changes only on decoded radio readback.

        Args:
            on: True for TX, False for RX.
        """
        self._check_connected()
        civ = (
            self._commands.ptt_on(to_addr=self._radio_addr)
            if on
            else self._commands.ptt_off(to_addr=self._radio_addr)
        )
        await self._send_civ_raw(civ, priority=Priority.IMMEDIATE, wait_response=False)
        logger.debug("set_ptt(%s) sent (fire-and-forget)", on)

    async def actuate(
        self,
        token: EffectToken,
        operation: ActuationOperation | AbortOperation,
        *,
        is_current: "Callable[[], bool]",
    ) -> ActuationResult:
        """Execute one authority-tokened Icom TX semantic on the urgent lane."""
        del token  # Authority owns token identity; the adapter owns no generation.
        try:
            if operation in (
                ActuationOperation.PTT_ON,
                ActuationOperation.TRANSMIT_ON,
            ):
                civ = self._commands.ptt_on(to_addr=self._radio_addr)
                priority = Priority.IMMEDIATE
            elif operation is ActuationOperation.FORCE_RECEIVE:
                civ = self._commands.ptt_off(to_addr=self._radio_addr)
                priority = Priority.FORCE_RELEASE
            elif operation is AbortOperation.STOP_CW:
                civ = self._commands.stop_cw(to_addr=self._radio_addr)
                priority = Priority.ABORT
            elif operation is AbortOperation.STOP_TUNE:
                civ = self._commands.set_tuner_status(0, to_addr=self._radio_addr)
                priority = Priority.ABORT
            else:
                return ActuationResult.REJECTED
        except CommandError:
            return ActuationResult.REJECTED
        await self._send_civ_raw(
            civ,
            priority=priority,
            wait_response=False,
            is_current=is_current,
        )
        return ActuationResult.ACCEPTED

    async def read_transmit_state(self) -> TxStateReading:
        """One solicited CI-V transmit-state observation.

        The directed reply is parsed through the shared observation decoder;
        only a matching PTT observation supplies a value. Icom provides no
        keying attribution. Wire-level failures return a ``failure`` tag;
        connection preconditions still raise.
        """
        self._check_connected()
        frame = build_civ_frame(self._radio_addr, CONTROLLER_ADDR, 0x1C, sub=0x00)
        try:
            reply = await self._send_civ_expect(
                frame, label="tx-state", timeout=TX_READ_DEADLINE_SECONDS
            )
        except (asyncio.TimeoutError, TimeoutError):
            # Both are real outcomes here: ``asyncio.TimeoutError`` from the
            # outer wait, or this module's own ``core.exceptions.TimeoutError``
            # ("CI-V response timed out") when the backend's own GET deadline
            # (``_civ_get_timeout``) fires first -- it can be shorter than
            # ``TX_READ_DEADLINE_SECONDS`` on a radio constructed with a tight
            # ``timeout=`` (as the conformance fake does).
            return TxStateReading(value=None, failure="timeout")
        except CommandError:
            return TxStateReading(value=None, failure="read-error")
        for observation in self._civ_runtime._observations_from_frame(reply):
            if str(observation.path) != "global.tx_state.ptt":
                continue
            source = str(observation.source.source)
            if source in RADIO_READBACK_SOURCES:
                return TxStateReading(
                    value=bool(observation.value),
                    attributed=None,  # Icom reports no attribution (§3.7)
                    source=source,
                    verified_readback=True,
                )
        return TxStateReading(value=None, failure="unverifiable-provenance")

    def _bind_authoritative_ptt_observer(
        self,
        *,
        provider_generation: int,
        observer: "Callable[[ProviderPttObservation], None]",
    ) -> None:
        """Bind decoded PTT readback to a managed-provider generation."""
        self._civ_runtime.bind_ptt_observer(
            provider_generation=provider_generation,
            observer=observer,
        )

    def _capture_managed_tx_port(
        self,
        provider_generation: int,
        observer: "Callable[[ProviderPttObservation], None]",
    ) -> bool:
        return self._civ_runtime.capture_managed_port(provider_generation, observer)

    async def _write_managed_ptt(self, provider_generation: int, on: bool) -> None:
        civ = (self._commands.ptt_on if on else self._commands.ptt_off)(
            to_addr=self._radio_addr
        )
        await self._civ_runtime.write_managed_ptt(civ, provider_generation)

    async def _retire_managed_tx_port(self, provider_generation: int) -> None:
        await self._civ_runtime.retire_managed_tx_port(provider_generation)

    def _unbind_authoritative_ptt_observer(self) -> None:
        """Detach the managed-provider PTT readback observer."""
        self._civ_runtime.unbind_ptt_observer()

    async def _request_authoritative_ptt_read(
        self,
        provider_generation: int,
        observer: "Callable[[ProviderPttObservation], None]",
    ) -> None:
        self._check_connected()
        civ = build_civ_frame(self._radio_addr, CONTROLLER_ADDR, 0x1C, sub=0x00)
        if not await self._civ_runtime.request_authoritative_ptt_read(
            civ,
            provider_generation=provider_generation,
            observer=observer,
        ):
            raise CommandError("PTT response was not authoritative for this request")

    # ------------------------------------------------------------------
    # Transceiver status family (#136)
    # ------------------------------------------------------------------

    async def get_band_edge_freq(self) -> int:
        """Read the current band-edge frequency in Hz."""
        self._check_connected()
        civ = self._commands.get_band_edge_freq(to_addr=self._radio_addr)
        resp = await self._send_civ_expect(
            civ, key="get_band_edge_freq", dedupe=True, label="get_band_edge_freq"
        )
        return parse_frequency_response(resp)

    async def get_various_squelch(self, receiver: int = RECEIVER_MAIN) -> bool:
        """Read various-squelch status for the selected receiver."""
        self._require_receiver(receiver, operation="get_various_squelch")
        self._require_cmd29_route(
            0x15, 0x05, receiver=receiver, operation="get_various_squelch"
        )
        cmd29 = self._profile.supports_cmd29(0x15, 0x05)
        civ = self._commands.get_various_squelch(
            to_addr=self._radio_addr, receiver=receiver, command29=cmd29
        )
        command, sub, _ = self._expect_shape(get_various_squelch)
        return await self._get_bool_value(
            civ,
            key=f"get_various_squelch:{receiver}",
            command=command,
            sub=sub,
        )

    async def get_power_meter(self) -> int:
        """Read the RF power meter (0-255 raw BCD)."""
        self._check_connected()
        civ = self._commands.get_power_meter(to_addr=self._radio_addr)
        resp = await self._send_civ_expect(civ, label="get_power_meter")
        return parse_meter_response(resp)

    async def get_comp_meter(self) -> int:
        """Read the compressor meter (0-255 raw BCD)."""
        self._check_connected()
        civ = self._commands.get_comp_meter(to_addr=self._radio_addr)
        resp = await self._send_civ_expect(civ, label="get_comp_meter")
        return parse_meter_response(resp)

    async def get_vd_meter(self) -> int:
        """Read the Vd supply voltage meter (0-255 raw BCD)."""
        self._check_connected()
        civ = self._commands.get_vd_meter(to_addr=self._radio_addr)
        resp = await self._send_civ_expect(civ, label="get_vd_meter")
        return parse_meter_response(resp)

    async def get_id_meter(self) -> int:
        """Read the Id drain current meter (0-255 raw BCD)."""
        self._check_connected()
        civ = self._commands.get_id_meter(to_addr=self._radio_addr)
        resp = await self._send_civ_expect(civ, label="get_id_meter")
        return parse_meter_response(resp)

    async def get_speech(self, what: int = 0) -> None:
        """Trigger voice synthesizer announcement.

        Fire-and-forget.

        Args:
            what: 0 = all (S-meter, frequency, mode),
                  1 = frequency + S-meter,
                  2 = mode.
        """
        self._check_connected()
        civ = self._commands.get_speech(what, to_addr=self._radio_addr)
        await self._send_civ_raw(civ, wait_response=False)

    async def get_transceiver_id(self) -> int:
        """Read the transceiver model ID (IC-7610 = 0x98)."""
        self._check_connected()
        civ = self._commands.get_transceiver_id(to_addr=self._radio_addr)
        resp = await self._send_civ_expect(civ, label="get_transceiver_id")
        if resp.data:
            return resp.data[0]
        return 0

    async def get_tuner_status(self) -> int:
        """Read the tuner/ATU status (0=off, 1=on, 2=tuning)."""
        self._check_connected()
        civ = self._commands.get_tuner_status(to_addr=self._radio_addr)
        resp = await self._send_civ_expect(civ, label="get_tuner_status")
        if resp.data:
            return resp.data[0]
        return 0

    async def set_tuner_status(self, value: int) -> None:
        """Set the tuner/ATU status (0=off, 1=on, 2=tune).

        Fire-and-forget SET command.
        """
        self._check_connected()
        civ = self._commands.set_tuner_status(value, to_addr=self._radio_addr)
        await self._send_civ_raw(civ, wait_response=False)

    async def get_xfc_status(self) -> bool:
        """Read XFC (transmit frequency correction) status."""
        self._check_connected()
        civ = self._commands.get_xfc_status(to_addr=self._radio_addr)
        resp = await self._send_civ_expect(civ, label="get_xfc_status")
        return bool(resp.data[0]) if resp.data else False

    async def set_xfc_status(self, on: bool) -> None:
        """Set XFC status on/off. Fire-and-forget."""
        self._check_connected()
        civ = self._commands.set_xfc_status(on, to_addr=self._radio_addr)
        await self._send_civ_raw(civ, wait_response=False)

    async def get_rit_frequency(self) -> int:
        """Read the RIT frequency offset in Hz (±9999)."""
        self._check_connected()
        civ = self._commands.get_rit_frequency(to_addr=self._radio_addr)
        resp = await self._send_civ_expect(civ, label="get_rit_frequency")
        return parse_rit_frequency_response(resp.data)

    async def set_rit_frequency(self, offset_hz: int) -> None:
        """Set the RIT frequency offset in Hz (±9999). Fire-and-forget."""
        self._check_connected()
        civ = self._commands.set_rit_frequency(offset_hz, to_addr=self._radio_addr)
        await self._send_civ_raw(civ, wait_response=False)

    async def get_rit_status(self) -> bool:
        """Read RIT on/off status."""
        self._check_connected()
        civ = self._commands.get_rit_status(to_addr=self._radio_addr)
        resp = await self._send_civ_expect(civ, label="get_rit_status")
        return bool(resp.data[0]) if resp.data else False

    async def set_rit_status(self, on: bool) -> None:
        """Set RIT on/off. Fire-and-forget."""
        self._check_connected()
        civ = self._commands.set_rit_status(on, to_addr=self._radio_addr)
        await self._send_civ_raw(civ, wait_response=False)

    async def get_rit_tx_status(self) -> bool:
        """Read RIT TX status."""
        self._check_connected()
        civ = self._commands.get_rit_tx_status(to_addr=self._radio_addr)
        resp = await self._send_civ_expect(civ, label="get_rit_tx_status")
        return bool(resp.data[0]) if resp.data else False

    async def set_rit_tx_status(self, on: bool) -> None:
        """Set RIT TX status on/off. Fire-and-forget."""
        self._check_connected()
        civ = self._commands.set_rit_tx_status(on, to_addr=self._radio_addr)
        await self._send_civ_raw(civ, wait_response=False)

    # ------------------------------------------------------------------
    # VFO / Split
    # ------------------------------------------------------------------

    async def _set_vfo_wire(self, vfo: str) -> None:
        """Wire-level CI-V VFO select.

        Internal helper used by capability-protocol implementations
        (:meth:`select_receiver`, :meth:`_run_with_receiver_vfo_fallback`,
        :meth:`swap_vfo_ab`, :meth:`equalize_vfo_ab`).  The legacy public
        ``set_vfo("A"/"B"/"MAIN"/"SUB")`` overload was removed in v0.20
        (#1206); external code must use
        :class:`~rigplane.radio_protocol.ReceiverBankCapable` /
        :class:`~rigplane.radio_protocol.VfoSlotCapable` instead.

        Args:
            vfo: "A", "B", "MAIN", or "SUB" (case-insensitive on input).

        The selector byte comes from the profile, not from a table here:
        ``B``/``SUB`` name the secondary VFO and take ``vfo_sub_code``,
        anything else the primary and ``vfo_main_code``.  One pair serves
        both spellings because a profile declares one primary and one
        secondary selector whatever the rig's front panel calls them
        (``[vfo] main_select`` / ``sub_select`` in ``rigs/*.toml``).  A
        profile that declares no code for the VFO asked for raises, rather
        than falling back to a byte this method invented.
        """
        self._check_connected()
        name = vfo.upper()
        code = (
            self._profile.vfo_sub_code
            if name in ("B", "SUB")
            else self._profile.vfo_main_code
        )
        if code is None:
            raise CommandError(
                f"profile {self._profile.model} declares no VFO select code for {name}"
            )
        civ = self._commands.set_vfo(code, to_addr=self._radio_addr)
        resp = await self._send_civ_expect(civ, label="set_vfo")
        ack = parse_ack_nak(resp)
        if ack is False:
            raise CommandError(f"Radio rejected VFO select {vfo}")
        self._last_vfo = vfo.upper()

    async def set_split(self, on: bool) -> None:
        """Enable or disable split mode (CI-V ``0x0F``)."""
        self._check_connected()
        civ = self._commands.set_split(on, to_addr=self._radio_addr)
        resp = await self._send_civ_expect(civ, label="set_split")
        ack = parse_ack_nak(resp)
        if ack is False:
            raise CommandError(f"Radio rejected split {'on' if on else 'off'}")
        self._last_split = on

    async def get_split(self) -> bool:
        """Read split mode state (CI-V ``0x0F``).

        Returns ``True`` when split is enabled, ``False`` otherwise.  On a
        radio that does not respond, returns the cached last-known value
        (defaulting to ``False``).
        """
        self._check_connected()
        civ = self._commands.get_split(to_addr=self._radio_addr)
        try:
            resp = await self._send_civ_expect(civ, label="get_split")
        except (CommandError, TimeoutError):
            if self._last_split is not None:
                logger.debug(
                    "get_split: no response, returning cached %s", self._last_split
                )
                return self._last_split
            return False
        if resp.data:
            on = bool(resp.data[0])
            self._last_split = on
            return on
        if self._last_split is not None:
            return self._last_split
        return False

    async def get_tuning_step(self) -> int:
        """Read the tuning step index (0-8, BCD-encoded per IC-7610, CI-V 0x10)."""
        self._check_connected()
        civ = self._commands.get_tuning_step(to_addr=self._radio_addr)
        resp = await self._send_civ_expect(civ, label="get_tuning_step")
        if resp.data:
            b = resp.data[0]
            return ((b >> 4) & 0x0F) * 10 + (b & 0x0F)
        return 0

    async def set_tuning_step(self, step: int) -> None:
        """Set the tuning step index (0-8, BCD-encoded, CI-V 0x10). Fire-and-forget."""
        self._check_connected()
        civ = self._commands.set_tuning_step(step, to_addr=self._radio_addr)
        await self._send_civ_raw(civ, wait_response=False)

    async def scan_start(self, mode: int = 0) -> None:
        """Start scanning (CI-V 0x0E). Fire-and-forget.

        Args:
            mode: Scan type sub-byte.  0 (default) sends 0x01 (programmed scan)
                  for backward compatibility.  Non-zero values are forwarded
                  directly as the scan-type sub-byte (e.g. 0x03 = ΔF scan),
                  validated against the profile's own domain (MOR-2007
                  ruling 4) when it declares one -- permissive, like
                  ``set_agc``, when it does not.
        """
        self._check_connected()
        if mode == 0:
            civ = self._commands.scan_start(to_addr=self._radio_addr)
        else:
            scan_types = self._profile.scan_type_values
            if scan_types is not None and mode not in scan_types:
                raise ValueError(
                    f"Scan type must be one of {sorted(scan_types)} for "
                    f"{self._profile.model}, got {hex(mode)}"
                )
            civ = self._commands.scan_start_type(mode, to_addr=self._radio_addr)
        await self._send_civ_raw(civ, wait_response=False)

    async def scan_stop(self) -> None:
        """Stop scanning (CI-V 0x0E 0x00). Fire-and-forget."""
        self._check_connected()
        civ = self._commands.scan_stop(to_addr=self._radio_addr)
        await self._send_civ_raw(civ, wait_response=False)

    async def scan_set_df_span(self, span: int) -> None:
        """Set ΔF scan span (CI-V 0x0E 0xA1-0xA7). Fire-and-forget."""
        self._check_connected()
        civ = self._commands.scan_set_df_span(span, to_addr=self._radio_addr)
        await self._send_civ_raw(civ, wait_response=False)

    async def scan_set_resume(self, mode: int) -> None:
        """Set scan resume mode (CI-V 0x0E sub). Fire-and-forget.

        Valid values are declared per radio by the profile's
        ``[scan_resume] values`` (MOR-2007 ruling 4) -- every documented
        CI-V guide lists only 0xD0 (OFF) and 0xD3 ("Close&Delay"), not the
        0xD1/0xD2 5s/10s states this method used to accept unconditionally.
        Permissive when the profile declares no domain, like ``set_agc``.
        """
        self._check_connected()
        scan_resume_modes = self._profile.scan_resume_values
        if scan_resume_modes is not None and mode not in scan_resume_modes:
            raise ValueError(
                f"Scan resume mode must be one of {sorted(scan_resume_modes)} for "
                f"{self._profile.model}, got {hex(mode)}"
            )
        civ = self._commands.scan_set_resume(mode, to_addr=self._radio_addr)
        await self._send_civ_raw(civ, wait_response=False)

    async def get_dual_watch(self) -> bool:
        """Query dual watch status.

        Returns:
            True if dual watch is enabled, False otherwise.

        The reply shape comes from the same map entry the request used
        (`commands/bound.py: BoundCommands.expect`), but unlike a
        ``_get_bool_value``-style getter it cannot assume the value lands
        in ``frame.sub``: IC-7610's ``[0x07, 0xC2]`` is a VFO-select-family
        command, and ``0x07`` carries no CI-V sub-command per
        `commands/_frame.py: command_carries_sub` (so
        `runtime/_civ_rx.py`'s unsolicited-frame decoding, which shares
        that same parser, is unaffected) -- the query's marker byte is
        echoed as ``data[0]`` instead, and ``BoundCommands.expect`` puts
        that same byte at the front of ``prefix`` (``sub=None``) rather
        than in ``sub``. IC-9700's ``[0x16, 0x59]`` is a real CI-V
        sub-command family (``0x16`` IS in that set), so its marker lands
        in ``frame.sub`` normally and ``prefix`` is empty. Handling both
        keeps this getter correct across profiles rather than pinned to
        whichever shape the request happened to use. Resolves its shape
        via ``BoundCommands.expect`` directly rather than
        ``self._expect_shape``: that helper asserts a non-``None`` ``sub``,
        which IC-7610's row legitimately does not have.
        """
        self._check_connected()
        command, sub, prefix = self._commands.expect(get_dual_watch)
        civ = self._commands.get_dual_watch(to_addr=self._radio_addr)
        resp = await self._send_civ_expect(civ, label="get_dual_watch")
        if resp.command != command:
            return False
        if resp.sub is not None:
            # e.g. IC-9700's [0x16, 0x59]: 0x16 IS in command_carries_sub,
            # so parse_civ_frame already split the marker into .sub.
            if resp.sub != sub:
                return False
            data = resp.data[len(prefix) :]
            return bool(data) and data[0] != 0x00
        # e.g. IC-7610's [0x07, 0xC2]: 0x07 carries no CI-V sub-command, so
        # the marker is echoed as data[0] instead of landing in .sub, and
        # BoundCommands.expect put it at prefix[0] instead of sub.
        if not prefix or not resp.data or resp.data[0] != prefix[0]:
            return False
        data = resp.data[1:]
        return bool(data) and data[0] != 0x00

    async def set_dual_watch(self, on: bool) -> None:
        """Enable or disable dual watch. Fire-and-forget."""
        self._check_connected()
        civ = self._commands.set_dual_watch(on, to_addr=self._radio_addr)
        await self._send_civ_raw(civ, wait_response=False)

    async def get_quick_split(self) -> bool:
        """Read the persistent Quick Split menu toggle (CI-V 0x1A 0x05).

        MOR-2007 ruling 2: replaces the pre-migration ``quick_split()``,
        which always sent this same bare-GET frame and never read the
        reply -- it fired nothing. ``0x1A`` carries a real CI-V
        sub-command (unlike ``get_dual_watch``'s ``0x07`` family above),
        so this is a plain ``_get_bool_value`` getter, the same shape as
        `commands/config.py: get_civ_transceive`.
        """
        command, sub, prefix = self._expect_shape(get_quick_split)
        return await self._get_bool_value(
            self._commands.get_quick_split(to_addr=self._radio_addr),
            key="get_quick_split",
            command=command,
            sub=sub,
            prefix=prefix,
        )

    async def set_quick_split(self, enabled: bool) -> None:
        """Write the persistent Quick Split menu toggle. Fire-and-forget."""
        await self._send_fire_and_forget(
            self._commands.set_quick_split(enabled, to_addr=self._radio_addr)
        )

    async def get_quick_dual_watch(self) -> bool:
        """Read the persistent Quick Dual Watch menu toggle.

        See ``get_quick_split`` -- same ruling, same shape.
        """
        command, sub, prefix = self._expect_shape(get_quick_dual_watch)
        return await self._get_bool_value(
            self._commands.get_quick_dual_watch(to_addr=self._radio_addr),
            key="get_quick_dual_watch",
            command=command,
            sub=sub,
            prefix=prefix,
        )

    async def set_quick_dual_watch(self, enabled: bool) -> None:
        """Write the persistent Quick Dual Watch menu toggle. Fire-and-forget."""
        await self._send_fire_and_forget(
            self._commands.set_quick_dual_watch(enabled, to_addr=self._radio_addr)
        )

    async def get_attenuator_level(self, receiver: int = RECEIVER_MAIN) -> int:
        """Read attenuator level in dB (Command29-aware).

        Args:
            receiver: RECEIVER_MAIN (0) or RECEIVER_SUB (1).
        """
        self._check_connected()
        self._require_capability("attenuator", operation="get_attenuator_level")
        self._require_receiver(receiver, operation="get_attenuator_level")
        self._require_cmd29_route(
            0x11, None, receiver=receiver, operation="get_attenuator_level"
        )
        cmd29 = self._profile.supports_cmd29(0x11)
        civ = self._commands.get_attenuator(
            to_addr=self._radio_addr, receiver=receiver, command29=cmd29
        )
        try:
            resp = await self._send_civ_expect(civ, label="get_attenuator_level")
            if resp.data:
                raw = resp.data[0]
                val = ((raw >> 4) & 0x0F) * 10 + (raw & 0x0F)
                self._attenuator_state = val != 0
                return val
        except TimeoutError:
            pass

        if self._attenuator_state is not None:
            return 18 if self._attenuator_state else 0
        raise CommandError("Radio returned empty attenuator response")

    async def get_attenuator(self, receiver: int = 0) -> bool:
        """Read attenuator state (compat wrapper)."""
        return (await self.get_attenuator_level(receiver)) > 0

    def project_attenuator_observation_value(self, db: int) -> int:
        return db

    async def set_attenuator_level(
        self, db: int, receiver: int = RECEIVER_MAIN
    ) -> None:
        """Set attenuator level in dB (Command29-aware).

        Fire-and-forget: the command is sent without waiting for an ACK.
        The attenuator state is updated optimistically.

        Args:
            db: Attenuation in dB. Valid values are declared per radio by the
                profile's ``[attenuator] values`` (e.g. IC-7300 has a single
                20 dB step; IC-7610 has 0..45 in 3 dB steps).
            receiver: RECEIVER_MAIN (0) or RECEIVER_SUB (1).
        """
        self._check_connected()
        self._require_capability("attenuator", operation="set_attenuator_level")
        self._require_receiver(receiver, operation="set_attenuator_level")
        self._require_cmd29_route(
            0x11, None, receiver=receiver, operation="set_attenuator_level"
        )
        att_values = self._profile.att_values
        if att_values is None:
            # Data-driven only: no universal numeric fallback. A profile that
            # declares the "attenuator" capability must also declare its
            # valid dB steps via [attenuator] values in its rig TOML.
            raise CommandError(
                f"set_attenuator_level is not supported by profile "
                f"{self._profile.model} (missing capability: attenuator values)"
            )
        if db not in att_values:
            raise ValueError(
                f"Attenuator level must be one of {sorted(att_values)} dB "
                f"for {self._profile.model}, got {db}"
            )
        cmd29 = self._profile.supports_cmd29(0x11)
        civ = self._commands.set_attenuator_level(
            db, to_addr=self._radio_addr, receiver=receiver, command29=cmd29
        )
        await self._send_civ_raw(civ, wait_response=False)
        self._attenuator_state = db > 0
        logger.debug("set_attenuator(%d dB) sent (fire-and-forget)", db)

    async def set_attenuator(self, on: bool, receiver: int = RECEIVER_MAIN) -> None:
        """Enable or disable attenuator (compat wrapper, Command29-aware).

        Resolves ``on`` to a value declared by the profile's ``[attenuator]
        values`` and delegates to :meth:`set_attenuator_level` -- this
        method no longer invents a level (MOR-2086; the deleted
        ``commands/dsp.py: set_attenuator`` hardcoded 18, which is an
        IC-7610 step invalid on every other CI-V profile). ``on=True``
        requires the profile to declare exactly one non-zero attenuator
        value; a stepped attenuator (e.g. IC-7610, several declared
        non-zero steps) has no well-defined boolean "on", so it is refused
        -- call :meth:`set_attenuator_level` directly with the desired dB
        value instead. A profile declaring no ``[attenuator] values`` at
        all is refused the same way.
        """
        self._check_connected()
        self._require_capability("attenuator", operation="set_attenuator")
        self._require_receiver(receiver, operation="set_attenuator")
        self._require_cmd29_route(
            0x11, None, receiver=receiver, operation="set_attenuator"
        )
        att_values = self._profile.att_values
        if att_values is None:
            raise CommandError(
                f"set_attenuator is not supported by profile "
                f"{self._profile.model} (missing capability: attenuator values)"
            )
        if not on:
            await self.set_attenuator_level(0, receiver=receiver)
            return
        non_zero = sorted({v for v in att_values if v != 0})
        if len(non_zero) != 1:
            raise CommandError(
                f"set_attenuator(on=True) is ambiguous for profile "
                f"{self._profile.model}: {len(non_zero)} declared non-zero "
                f"attenuator values {non_zero}; call set_attenuator_level "
                f"directly with the desired dB value instead"
            )
        await self.set_attenuator_level(non_zero[0], receiver=receiver)

    async def get_preamp(self, receiver: int = RECEIVER_MAIN) -> int:
        """Read preamp level (0=off, 1=PREAMP1, 2=PREAMP2) (Command29-aware).

        Args:
            receiver: RECEIVER_MAIN (0) or RECEIVER_SUB (1).
        """
        self._check_connected()
        self._require_capability("preamp", operation="get_preamp")
        self._require_receiver(receiver, operation="get_preamp")
        self._require_cmd29_route(0x16, 0x02, receiver=receiver, operation="get_preamp")
        cmd29 = self._profile.supports_cmd29(0x16, 0x02)
        civ = self._commands.get_preamp(
            to_addr=self._radio_addr, receiver=receiver, command29=cmd29
        )
        try:
            resp = await self._send_civ_expect(civ, label="get_preamp")
            if resp.data:
                raw = resp.data[0]
                self._preamp_level = ((raw >> 4) & 0x0F) * 10 + (raw & 0x0F)
                return self._preamp_level
        except TimeoutError:
            pass

        if self._preamp_level is not None:
            return self._preamp_level
        raise CommandError("Radio returned empty preamp response")

    async def set_preamp(self, level: int = 1, receiver: int = RECEIVER_MAIN) -> None:
        """Set preamp level (Command29-aware).

        Valid values are declared per radio by the profile's ``[preamp]
        values`` (e.g. IC-7300 offers OFF/P.AMP1/P.AMP2 = 0/1/2; the X6200
        only declares OFF/P.AMP1 = 0/1 — it has no second preamp stage).

        Args:
            level: preamp level, must be one of the profile's declared
                ``[preamp] values``.
            receiver: RECEIVER_MAIN (0) or RECEIVER_SUB (1).

        Raises:
            ValueError: If ``level`` is not in the profile's declared
                preamp domain.
            CommandError: If DIGI-SEL (IP+) is enabled. On IC-7610, PREAMP and
                DIGI-SEL are mutually exclusive — disable DIGI-SEL first.
        """
        self._check_connected()
        self._require_capability("preamp", operation="set_preamp")
        self._require_receiver(receiver, operation="set_preamp")
        self._require_cmd29_route(
            0x16,
            0x02,
            receiver=receiver,
            operation="set_preamp",
        )
        pre_values = self._profile.pre_values
        if pre_values is not None and level not in pre_values:
            raise ValueError(
                f"Preamp level must be one of {sorted(pre_values)} for "
                f"{self._profile.model}, got {level}"
            )

        # Pre-flight: check DIGI-SEL / PREAMP mutual exclusion
        if level > 0 and "digisel" in self.capabilities:
            try:
                if await self.get_digisel(receiver=receiver):
                    raise CommandError(
                        f"Cannot set preamp level {level}: DIGI-SEL (IP+) is ON. "
                        "PREAMP and DIGI-SEL are mutually exclusive — disable DIGI-SEL first."
                    )
            except CommandError:
                raise
            except Exception:
                logger.debug(
                    "set_preamp: unexpected error checking DIGI-SEL, proceeding",
                    exc_info=True,
                )

        cmd29 = self._profile.supports_cmd29(0x16, 0x02)
        civ = self._commands.set_preamp(
            level, to_addr=self._radio_addr, receiver=receiver, command29=cmd29
        )
        await self._send_civ_raw(civ, wait_response=False)
        self._preamp_level = level

    async def get_digisel(self, receiver: int = RECEIVER_MAIN) -> bool:
        """Read DIGI-SEL status (IC-7610 frontend selector).

        Wrapping is gated purely on ``self._profile.supports_cmd29(0x16,
        0x4E)`` via ``_require_cmd29_route`` (a no-op for receiver=MAIN,
        which raises for receiver!=MAIN without a route) — matching every
        other command in this family. Previously this also hard-raised
        ``CommandError`` for receiver=MAIN when the profile lacked the
        route, instead of unwrapping like the rest of the family (MOR-1537).

        That earlier raise was NOT purely redundant, contrary to MOR-1537's
        framing: it was the only thing turning a profile that over-declares
        the ``"digisel"`` capability without a matching cmd29 route/command
        into a fast, explicit ``CommandError`` instead of a live, always-
        0x16/0x4E CI-V send that blocks for a full timeout because the
        radio never answers (MOR-1540 hit this on IC-705). The fix for that
        class of bug is correcting the capability declaration at the source
        (``rigs/<model>.toml``), not restoring the raise here — this method
        still unwraps for MAIN as documented above.
        """
        self._check_connected()
        self._require_capability("digisel", operation="get_digisel")
        self._require_receiver(receiver, operation="get_digisel")
        self._require_cmd29_route(
            0x16,
            0x4E,
            receiver=receiver,
            operation="get_digisel",
        )
        cmd29 = self._profile.supports_cmd29(0x16, 0x4E)
        civ = self._commands.get_digisel(
            to_addr=self._radio_addr, receiver=receiver, command29=cmd29
        )
        resp = await self._send_civ_expect(civ, label="get_digisel")
        if not resp.data:
            raise CommandError("Radio returned empty DIGI-SEL response")
        raw = resp.data[0]
        val = ((raw >> 4) & 0x0F) * 10 + (raw & 0x0F)
        return bool(val)

    async def set_digisel(self, on: bool, receiver: int = 0) -> None:
        """Set DIGI-SEL status."""
        self._check_connected()
        self._require_capability("digisel", operation="set_digisel")
        self._require_receiver(receiver, operation="set_digisel")
        self._require_cmd29_route(
            0x16,
            0x4E,
            receiver=receiver,
            operation="set_digisel",
        )
        cmd29 = self._profile.supports_cmd29(0x16, 0x4E)
        civ = self._commands.set_digisel(
            on, to_addr=self._radio_addr, receiver=receiver, command29=cmd29
        )
        resp = await self._send_civ_expect(civ, label="set_digisel")
        ack = parse_ack_nak(resp)
        if ack is False:
            raise CommandError(f"Radio rejected DIGI-SEL {'on' if on else 'off'}")

    async def get_nb(self) -> bool:
        """Read Noise Blanker status."""
        self._check_connected()
        cmd29 = self._profile.supports_cmd29(0x16, 0x22)
        civ = self._commands.get_nb(to_addr=self._radio_addr, command29=cmd29)
        resp = await self._send_civ_expect(civ, label="get_nb")
        return resp.data[0] == 0x01 if resp.data else False

    async def set_nb(self, on: bool, receiver: int = 0) -> None:
        """Set Noise Blanker on/off."""
        self._check_connected()
        self._require_capability("nb", operation="set_nb")
        self._require_receiver(receiver, operation="set_nb")
        self._require_cmd29_route(
            0x16,
            0x22,
            receiver=receiver,
            operation="set_nb",
        )
        cmd29 = self._profile.supports_cmd29(0x16, 0x22)
        civ = self._commands.set_nb(
            on, to_addr=self._radio_addr, receiver=receiver, command29=cmd29
        )
        await self._send_civ_raw(civ, wait_response=False)

    async def get_nr(self) -> bool:
        """Read Noise Reduction status."""
        self._check_connected()
        cmd29 = self._profile.supports_cmd29(0x16, 0x40)
        civ = self._commands.get_nr(to_addr=self._radio_addr, command29=cmd29)
        resp = await self._send_civ_expect(civ, label="get_nr")
        return resp.data[0] == 0x01 if resp.data else False

    async def set_nr(self, on: bool, receiver: int = 0) -> None:
        """Set Noise Reduction on/off."""
        self._check_connected()
        self._require_capability("nr", operation="set_nr")
        self._require_receiver(receiver, operation="set_nr")
        self._require_cmd29_route(
            0x16,
            0x40,
            receiver=receiver,
            operation="set_nr",
        )
        cmd29 = self._profile.supports_cmd29(0x16, 0x40)
        civ = self._commands.set_nr(
            on, to_addr=self._radio_addr, receiver=receiver, command29=cmd29
        )
        await self._send_civ_raw(civ, wait_response=False)

    async def get_ip_plus(self) -> bool:
        """Read IP+ status."""
        self._check_connected()
        cmd29 = self._profile.supports_cmd29(0x16, 0x65)
        civ = self._commands.get_ip_plus(to_addr=self._radio_addr, command29=cmd29)
        resp = await self._send_civ_expect(civ, label="get_ip_plus")
        return resp.data[0] == 0x01 if resp.data else False

    async def set_ip_plus(self, on: bool, receiver: int = 0) -> None:
        """Set IP+ on/off."""
        self._check_connected()
        self._require_capability("ip_plus", operation="set_ip_plus")
        self._require_receiver(receiver, operation="set_ip_plus")
        self._require_cmd29_route(
            0x16,
            0x65,
            receiver=receiver,
            operation="set_ip_plus",
        )
        cmd29 = self._profile.supports_cmd29(0x16, 0x65)
        civ = self._commands.set_ip_plus(
            on, to_addr=self._radio_addr, receiver=receiver, command29=cmd29
        )
        await self._send_civ_raw(civ, wait_response=False)

    async def get_repeater_tone(self, receiver: int = 0) -> bool:
        """Read repeater tone status (0x16 0x42)."""
        self._check_connected()
        self._require_receiver(receiver, operation="get_repeater_tone")
        command, sub, _ = self._expect_shape(_get_repeater_tone_cmd)

        if receiver != RECEIVER_MAIN and not self._profile.supports_cmd29(
            0x16, _SUB_REPEATER_TONE
        ):

            async def _action() -> bool:
                civ = self._commands.get_repeater_tone(
                    to_addr=self._radio_addr,
                    receiver=RECEIVER_MAIN,
                    command29=False,
                )
                return await self._get_bool_value(
                    civ,
                    key=f"get_repeater_tone:{receiver}",
                    command=command,
                    sub=sub,
                )

            return await self._run_with_receiver_vfo_fallback(
                receiver=receiver,
                operation="get_repeater_tone",
                action=_action,
            )

        self._require_cmd29_route(
            0x16, _SUB_REPEATER_TONE, receiver=receiver, operation="get_repeater_tone"
        )
        cmd29 = self._profile.supports_cmd29(0x16, _SUB_REPEATER_TONE)
        civ = self._commands.get_repeater_tone(
            to_addr=self._radio_addr, receiver=receiver, command29=cmd29
        )
        return await self._get_bool_value(
            civ,
            key=f"get_repeater_tone:{receiver}",
            command=command,
            sub=sub,
        )

    async def set_repeater_tone(self, on: bool, receiver: int = 0) -> None:
        """Set repeater tone on/off (0x16 0x42)."""
        self._check_connected()
        self._require_receiver(receiver, operation="set_repeater_tone")

        if receiver != RECEIVER_MAIN and not self._profile.supports_cmd29(
            0x16, _SUB_REPEATER_TONE
        ):

            async def _action() -> None:
                await self._send_fire_and_forget(
                    self._commands.set_repeater_tone(
                        on,
                        to_addr=self._radio_addr,
                        receiver=RECEIVER_MAIN,
                        command29=False,
                    )
                )

            await self._run_with_receiver_vfo_fallback(
                receiver=receiver,
                operation="set_repeater_tone",
                action=_action,
            )
            return

        self._require_cmd29_route(
            0x16, _SUB_REPEATER_TONE, receiver=receiver, operation="set_repeater_tone"
        )
        cmd29 = self._profile.supports_cmd29(0x16, _SUB_REPEATER_TONE)
        await self._send_fire_and_forget(
            self._commands.set_repeater_tone(
                on, to_addr=self._radio_addr, receiver=receiver, command29=cmd29
            )
        )

    async def get_repeater_tsql(self, receiver: int = 0) -> bool:
        """Read repeater TSQL status (0x16 0x43)."""
        self._check_connected()
        self._require_receiver(receiver, operation="get_repeater_tsql")
        command, sub, _ = self._expect_shape(_get_repeater_tsql_cmd)

        if receiver != RECEIVER_MAIN and not self._profile.supports_cmd29(
            0x16, _SUB_REPEATER_TSQL
        ):

            async def _action() -> bool:
                civ = self._commands.get_repeater_tsql(
                    to_addr=self._radio_addr,
                    receiver=RECEIVER_MAIN,
                    command29=False,
                )
                return await self._get_bool_value(
                    civ,
                    key=f"get_repeater_tsql:{receiver}",
                    command=command,
                    sub=sub,
                )

            return await self._run_with_receiver_vfo_fallback(
                receiver=receiver,
                operation="get_repeater_tsql",
                action=_action,
            )

        self._require_cmd29_route(
            0x16, _SUB_REPEATER_TSQL, receiver=receiver, operation="get_repeater_tsql"
        )
        cmd29 = self._profile.supports_cmd29(0x16, _SUB_REPEATER_TSQL)
        civ = self._commands.get_repeater_tsql(
            to_addr=self._radio_addr, receiver=receiver, command29=cmd29
        )
        return await self._get_bool_value(
            civ,
            key=f"get_repeater_tsql:{receiver}",
            command=command,
            sub=sub,
        )

    async def set_repeater_tsql(self, on: bool, receiver: int = 0) -> None:
        """Set repeater TSQL on/off (0x16 0x43)."""
        self._check_connected()
        self._require_receiver(receiver, operation="set_repeater_tsql")

        if receiver != RECEIVER_MAIN and not self._profile.supports_cmd29(
            0x16, _SUB_REPEATER_TSQL
        ):

            async def _action() -> None:
                await self._send_fire_and_forget(
                    self._commands.set_repeater_tsql(
                        on,
                        to_addr=self._radio_addr,
                        receiver=RECEIVER_MAIN,
                        command29=False,
                    )
                )

            await self._run_with_receiver_vfo_fallback(
                receiver=receiver,
                operation="set_repeater_tsql",
                action=_action,
            )
            return

        self._require_cmd29_route(
            0x16, _SUB_REPEATER_TSQL, receiver=receiver, operation="set_repeater_tsql"
        )
        cmd29 = self._profile.supports_cmd29(0x16, _SUB_REPEATER_TSQL)
        await self._send_fire_and_forget(
            self._commands.set_repeater_tsql(
                on, to_addr=self._radio_addr, receiver=receiver, command29=cmd29
            )
        )

    def _ctcss_tones_centihz(self) -> tuple[int, ...]:
        """Return this radio's validated, exact-centiHz CTCSS domain."""
        domain = self._profile.ctcss_tones_centihz
        if (
            not isinstance(domain, tuple)
            or not domain
            or any(type(tone_centihz) is not int for tone_centihz in domain)
        ):
            raise ValueError("CTCSS tone domain must be a non-empty tuple of ints")
        return domain

    async def get_tone_freq(self, receiver: int = 0) -> int:
        """Read CTCSS tone frequency in exact centiHz (0x1B 0x00)."""
        self._check_connected()
        self._require_receiver(receiver, operation="get_tone_freq")
        domain = self._ctcss_tones_centihz()

        if receiver != RECEIVER_MAIN and not self._profile.supports_cmd29(0x1B, 0x00):
            civ = self._commands.get_tone_freq(
                to_addr=self._radio_addr,
                receiver=RECEIVER_MAIN,
                command29=False,
                ctcss_tones_centihz=domain,
            )

            async def _action() -> int:
                resp = await self._send_civ_expect(civ, label="get_tone_freq")
                _, freq = parse_tone_freq_response(resp, ctcss_tones_centihz=domain)
                return freq

            return await self._run_with_receiver_vfo_fallback(
                receiver=receiver,
                operation="get_tone_freq",
                action=_action,
            )

        self._require_cmd29_route(
            0x1B, 0x00, receiver=receiver, operation="get_tone_freq"
        )
        cmd29 = self._profile.supports_cmd29(0x1B, 0x00)
        civ = self._commands.get_tone_freq(
            to_addr=self._radio_addr,
            receiver=receiver,
            command29=cmd29,
            ctcss_tones_centihz=domain,
        )
        resp = await self._send_civ_expect(civ, label="get_tone_freq")
        _, freq = parse_tone_freq_response(resp, ctcss_tones_centihz=domain)
        return freq

    async def set_tone_freq(self, freq_centihz: int, receiver: int = 0) -> None:
        """Set CTCSS tone frequency in exact centiHz (0x1B 0x00)."""
        self._check_connected()
        self._require_receiver(receiver, operation="set_tone_freq")
        domain = self._ctcss_tones_centihz()

        if receiver != RECEIVER_MAIN and not self._profile.supports_cmd29(0x1B, 0x00):
            civ = self._commands.set_tone_freq(
                freq_centihz,
                to_addr=self._radio_addr,
                receiver=RECEIVER_MAIN,
                command29=False,
                ctcss_tones_centihz=domain,
            )

            async def _action() -> None:
                await self._send_fire_and_forget(civ)

            await self._run_with_receiver_vfo_fallback(
                receiver=receiver,
                operation="set_tone_freq",
                action=_action,
            )
            return

        self._require_cmd29_route(
            0x1B, 0x00, receiver=receiver, operation="set_tone_freq"
        )
        cmd29 = self._profile.supports_cmd29(0x1B, 0x00)
        await self._send_fire_and_forget(
            self._commands.set_tone_freq(
                freq_centihz,
                to_addr=self._radio_addr,
                receiver=receiver,
                command29=cmd29,
                ctcss_tones_centihz=domain,
            )
        )

    async def get_tsql_freq(self, receiver: int = 0) -> int:
        """Read TSQL frequency in exact centiHz (0x1B 0x01)."""
        self._check_connected()
        self._require_receiver(receiver, operation="get_tsql_freq")
        domain = self._ctcss_tones_centihz()

        if receiver != RECEIVER_MAIN and not self._profile.supports_cmd29(0x1B, 0x01):
            civ = self._commands.get_tsql_freq(
                to_addr=self._radio_addr,
                receiver=RECEIVER_MAIN,
                command29=False,
                ctcss_tones_centihz=domain,
            )

            async def _action() -> int:
                resp = await self._send_civ_expect(civ, label="get_tsql_freq")
                _, freq = parse_tsql_freq_response(resp, ctcss_tones_centihz=domain)
                return freq

            return await self._run_with_receiver_vfo_fallback(
                receiver=receiver,
                operation="get_tsql_freq",
                action=_action,
            )

        self._require_cmd29_route(
            0x1B, 0x01, receiver=receiver, operation="get_tsql_freq"
        )
        cmd29 = self._profile.supports_cmd29(0x1B, 0x01)
        civ = self._commands.get_tsql_freq(
            to_addr=self._radio_addr,
            receiver=receiver,
            command29=cmd29,
            ctcss_tones_centihz=domain,
        )
        resp = await self._send_civ_expect(civ, label="get_tsql_freq")
        _, freq = parse_tsql_freq_response(resp, ctcss_tones_centihz=domain)
        return freq

    async def set_tsql_freq(self, freq_centihz: int, receiver: int = 0) -> None:
        """Set TSQL frequency in exact centiHz (0x1B 0x01)."""
        self._check_connected()
        self._require_receiver(receiver, operation="set_tsql_freq")
        domain = self._ctcss_tones_centihz()

        if receiver != RECEIVER_MAIN and not self._profile.supports_cmd29(0x1B, 0x01):
            civ = self._commands.set_tsql_freq(
                freq_centihz,
                to_addr=self._radio_addr,
                receiver=RECEIVER_MAIN,
                command29=False,
                ctcss_tones_centihz=domain,
            )

            async def _action() -> None:
                await self._send_fire_and_forget(civ)

            await self._run_with_receiver_vfo_fallback(
                receiver=receiver,
                operation="set_tsql_freq",
                action=_action,
            )
            return

        self._require_cmd29_route(
            0x1B, 0x01, receiver=receiver, operation="set_tsql_freq"
        )
        cmd29 = self._profile.supports_cmd29(0x1B, 0x01)
        await self._send_fire_and_forget(
            self._commands.set_tsql_freq(
                freq_centihz,
                to_addr=self._radio_addr,
                receiver=receiver,
                command29=cmd29,
                ctcss_tones_centihz=domain,
            )
        )

    # ------------------------------------------------------------------
    # System/Config commands (#135)
    # ------------------------------------------------------------------

    async def get_antenna_1(self) -> bool:
        """Read ANT1 selection status (0x12 0x00)."""
        return await self._get_bool_value(
            self._commands.get_antenna_1(to_addr=self._radio_addr),
            key="get_antenna_1",
            command=0x12,
            sub=0x00,
        )

    async def set_antenna_1(self, enabled: bool) -> None:
        """Select ANT1 (0x12 0x00 <00|01>).

        IC-7610: data byte encodes RX-ANT OFF/ON.
        """
        await self._send_fire_and_forget(
            self._commands.set_antenna_1(enabled, to_addr=self._radio_addr)
        )

    async def get_antenna_2(self) -> bool:
        """Read ANT2 selection status (0x12 0x01)."""
        return await self._get_bool_value(
            self._commands.get_antenna_2(to_addr=self._radio_addr),
            key="get_antenna_2",
            command=0x12,
            sub=0x01,
        )

    async def set_antenna_2(self, enabled: bool) -> None:
        """Select ANT2 (0x12 0x01 <00|01>).

        IC-7610: data byte encodes RX-ANT OFF/ON.
        """
        await self._send_fire_and_forget(
            self._commands.set_antenna_2(enabled, to_addr=self._radio_addr)
        )

    async def get_rx_antenna_ant1(self) -> bool:
        """Read RX ANT state for ANT1.

        NOTE: On IC-7610 this is implemented via 0x12 0x00 and may select ANT1.
        """
        return await self._get_bool_value(
            self._commands.get_rx_antenna_ant1(to_addr=self._radio_addr),
            key="get_rx_antenna_ant1",
            command=0x12,
            sub=0x00,
        )

    async def set_rx_antenna_ant1(self, enabled: bool) -> None:
        """Set RX ANT state for ANT1 (0x12 0x00 <00|01>)."""
        await self._send_fire_and_forget(
            self._commands.set_rx_antenna_ant1(enabled, to_addr=self._radio_addr)
        )

    async def get_rx_antenna_ant2(self) -> bool:
        """Read RX ANT state for ANT2.

        NOTE: On IC-7610 this is implemented via 0x12 0x01 and may select ANT2.
        """
        return await self._get_bool_value(
            self._commands.get_rx_antenna_ant2(to_addr=self._radio_addr),
            key="get_rx_antenna_ant2",
            command=0x12,
            sub=0x01,
        )

    async def set_rx_antenna_ant2(self, enabled: bool) -> None:
        """Set RX ANT state for ANT2 (0x12 0x01 <00|01>)."""
        await self._send_fire_and_forget(
            self._commands.set_rx_antenna_ant2(enabled, to_addr=self._radio_addr)
        )

    # commands/config.py, migrated onto the bound command map (MOR-2006
    # Steps 5..N module 1, plan §4): requests go through
    # ``self._commands.<builder>`` and replies through
    # ``self._expect_shape`` (§6 population 1), rather than the literal
    # ``command=``/``sub=``/``prefix=`` values every getter below still uses.

    async def get_acc1_mod_level(self) -> int:
        """Read ACC1 modulation level (0-255)."""
        command, sub, prefix = self._expect_shape(get_acc1_mod_level)
        return await self._get_bcd_level(
            self._commands.get_acc1_mod_level(to_addr=self._radio_addr),
            key="get_acc1_mod_level",
            command=command,
            sub=sub,
            prefix=prefix,
        )

    async def set_acc1_mod_level(self, level: int) -> None:
        """Set ACC1 modulation level (0-255)."""
        await self._send_fire_and_forget(
            self._commands.set_acc1_mod_level(level, to_addr=self._radio_addr)
        )

    async def get_usb_mod_level(self) -> int:
        """Read USB modulation level (0-255)."""
        command, sub, prefix = self._expect_shape(get_usb_mod_level)
        return await self._get_bcd_level(
            self._commands.get_usb_mod_level(to_addr=self._radio_addr),
            key="get_usb_mod_level",
            command=command,
            sub=sub,
            prefix=prefix,
        )

    async def set_usb_mod_level(self, level: int) -> None:
        """Set USB modulation level (0-255)."""
        await self._send_fire_and_forget(
            self._commands.set_usb_mod_level(level, to_addr=self._radio_addr)
        )

    async def get_lan_mod_level(self) -> int:
        """Read LAN modulation level (0-255)."""
        command, sub, prefix = self._expect_shape(get_lan_mod_level)
        return await self._get_bcd_level(
            self._commands.get_lan_mod_level(to_addr=self._radio_addr),
            key="get_lan_mod_level",
            command=command,
            sub=sub,
            prefix=prefix,
        )

    async def set_lan_mod_level(self, level: int) -> None:
        """Set LAN modulation level (0-255)."""
        await self._send_fire_and_forget(
            self._commands.set_lan_mod_level(level, to_addr=self._radio_addr)
        )

    async def get_data_off_mod_input(self) -> int:
        """Read Data Off modulation input source (0-5)."""
        command, sub, prefix = self._expect_shape(get_data_off_mod_input)
        return await self._get_bcd_level(
            self._commands.get_data_off_mod_input(to_addr=self._radio_addr),
            key="get_data_off_mod_input",
            command=command,
            sub=sub,
            prefix=prefix,
            bcd_bytes=1,
        )

    async def set_data_off_mod_input(self, source: int) -> None:
        """Set Data Off modulation input source (0-5)."""
        await self._send_fire_and_forget(
            self._commands.set_data_off_mod_input(source, to_addr=self._radio_addr)
        )

    async def get_data1_mod_input(self) -> int:
        """Read DATA1 modulation input source (0-4)."""
        command, sub, prefix = self._expect_shape(get_data1_mod_input)
        return await self._get_bcd_level(
            self._commands.get_data1_mod_input(to_addr=self._radio_addr),
            key="get_data1_mod_input",
            command=command,
            sub=sub,
            prefix=prefix,
            bcd_bytes=1,
        )

    async def set_data1_mod_input(self, source: int) -> None:
        """Set DATA1 modulation input source (0-4)."""
        await self._send_fire_and_forget(
            self._commands.set_data1_mod_input(source, to_addr=self._radio_addr)
        )

    async def get_data2_mod_input(self) -> int:
        """Read DATA2 modulation input source (0-4)."""
        command, sub, prefix = self._expect_shape(get_data2_mod_input)
        return await self._get_bcd_level(
            self._commands.get_data2_mod_input(to_addr=self._radio_addr),
            key="get_data2_mod_input",
            command=command,
            sub=sub,
            prefix=prefix,
            bcd_bytes=1,
        )

    async def set_data2_mod_input(self, source: int) -> None:
        """Set DATA2 modulation input source (0-4)."""
        await self._send_fire_and_forget(
            self._commands.set_data2_mod_input(source, to_addr=self._radio_addr)
        )

    async def get_data3_mod_input(self) -> int:
        """Read DATA3 modulation input source (0-4)."""
        command, sub, prefix = self._expect_shape(get_data3_mod_input)
        return await self._get_bcd_level(
            self._commands.get_data3_mod_input(to_addr=self._radio_addr),
            key="get_data3_mod_input",
            command=command,
            sub=sub,
            prefix=prefix,
            bcd_bytes=1,
        )

    async def set_data3_mod_input(self, source: int) -> None:
        """Set DATA3 modulation input source (0-4)."""
        await self._send_fire_and_forget(
            self._commands.set_data3_mod_input(source, to_addr=self._radio_addr)
        )

    async def get_civ_transceive(self) -> bool:
        """Read CI-V transceive status."""
        command, sub, prefix = self._expect_shape(get_civ_transceive)
        return await self._get_bool_value(
            self._commands.get_civ_transceive(to_addr=self._radio_addr),
            key="get_civ_transceive",
            command=command,
            sub=sub,
            prefix=prefix,
        )

    async def set_civ_transceive(self, enabled: bool) -> None:
        """Set CI-V transceive status."""
        await self._send_fire_and_forget(
            self._commands.set_civ_transceive(enabled, to_addr=self._radio_addr)
        )

    async def get_civ_output_ant(self) -> bool:
        """Read CI-V output (ANT) status."""
        command, sub, prefix = self._expect_shape(get_civ_output_ant)
        return await self._get_bool_value(
            self._commands.get_civ_output_ant(to_addr=self._radio_addr),
            key="get_civ_output_ant",
            command=command,
            sub=sub,
            prefix=prefix,
        )

    async def set_civ_output_ant(self, enabled: bool) -> None:
        """Set CI-V output (ANT) status."""
        await self._send_fire_and_forget(
            self._commands.set_civ_output_ant(enabled, to_addr=self._radio_addr)
        )

    async def get_system_date(self) -> tuple[int, int, int]:
        """Read system date as (year, month, day)."""
        self._check_connected()
        civ = self._commands.get_system_date(to_addr=self._radio_addr)
        resp = await self._send_civ_expect(
            civ, key="get_system_date", dedupe=True, label="get_system_date"
        )
        _, _, prefix = self._expect_shape(get_system_date)
        return parse_system_date_response(resp, prefix=prefix)

    async def set_system_date(self, year: int, month: int, day: int) -> None:
        """Set system date.

        Args:
            year: 4-digit year.
            month: Month 1-12.
            day: Day 1-31.
        """
        await self._send_fire_and_forget(
            self._commands.set_system_date(year, month, day, to_addr=self._radio_addr)
        )

    async def get_system_time(self) -> tuple[int, int]:
        """Read system time as (hour, minute)."""
        self._check_connected()
        civ = self._commands.get_system_time(to_addr=self._radio_addr)
        resp = await self._send_civ_expect(
            civ, key="get_system_time", dedupe=True, label="get_system_time"
        )
        _, _, prefix = self._expect_shape(get_system_time)
        return parse_system_time_response(resp, prefix=prefix)

    async def set_system_time(self, hour: int, minute: int) -> None:
        """Set system time.

        Args:
            hour: Hour 0-23.
            minute: Minute 0-59.
        """
        await self._send_fire_and_forget(
            self._commands.set_system_time(hour, minute, to_addr=self._radio_addr)
        )

    async def get_utc_offset(self) -> tuple[int, int, bool]:
        """Read UTC offset as (hours, minutes, is_negative)."""
        self._check_connected()
        civ = self._commands.get_utc_offset(to_addr=self._radio_addr)
        resp = await self._send_civ_expect(
            civ, key="get_utc_offset", dedupe=True, label="get_utc_offset"
        )
        _, _, prefix = self._expect_shape(get_utc_offset)
        return parse_utc_offset_response(resp, prefix=prefix)

    async def set_utc_offset(self, hours: int, minutes: int, is_negative: bool) -> None:
        """Set UTC offset.

        Args:
            hours: Offset hours 0-14.
            minutes: Offset minutes, one of 0/15/30/45.
            is_negative: True for negative (west) offset.
        """
        await self._send_fire_and_forget(
            self._commands.set_utc_offset(
                hours, minutes, is_negative, to_addr=self._radio_addr
            )
        )

    async def snapshot_state(self) -> dict[str, object]:
        """Best-effort snapshot of core rig state for safe restore.

        Implementation lives in :mod:`rigplane.radio_state_snapshot` (#1258).
        """
        return await _state_snapshot.snapshot_state(self)

    async def restore_state(self, state: dict[str, object]) -> None:
        """Best-effort restore of state produced by :meth:`snapshot_state`.

        Implementation lives in :mod:`rigplane.radio_state_snapshot` (#1258).
        """
        await _state_snapshot.restore_state(self, state)

    async def run_state_transaction(
        self,
        body: "Callable[[], Awaitable[None]]",
    ) -> None:
        """Run operation with snapshot/restore guard (wfview-style safety pattern)."""
        self._check_connected()

        async def _body() -> dict[str, object]:
            await body()
            return {}

        if self._commander is None:
            snapshot = await self.snapshot_state()
            try:
                await body()
            finally:
                await self.restore_state(snapshot)
            return

        await self._commander.transaction(
            snapshot=self.snapshot_state,
            restore=self.restore_state,
            body=_body,
        )

    # ------------------------------------------------------------------
    # CW keying
    # ------------------------------------------------------------------

    async def send_cw_text(self, text: str) -> None:
        """Send CW text via the radio's built-in keyer.

        Text is split into 30-character chunks.

        Args:
            text: CW text (A-Z, 0-9, prosigns).
        """
        self._check_connected()
        frames = self._commands.send_cw(text, to_addr=self._radio_addr)
        for frame in frames:
            resp = await self._send_civ_expect(frame, label="send_cw_text")
            ack = parse_ack_nak(resp)
            if ack is False:
                raise CommandError("Radio rejected CW text")

    async def stop_cw_text(self) -> None:
        """Stop CW sending."""
        self._check_connected()
        civ = self._commands.stop_cw(to_addr=self._radio_addr)
        resp = await self._send_civ_raw(civ, priority=Priority.IMMEDIATE)
        if resp is not None and parse_ack_nak(resp) is False:
            raise CommandError("Radio rejected CW stop")

    async def power_control(self, on: bool) -> None:
        """Power the radio on or off.

        Args:
            on: True to power on, False to power off.
        """
        await self.set_powerstat(on)

    async def get_powerstat(self) -> bool:
        """Get the current radio power state (PowerControlCapable protocol)."""
        self._check_connected()
        civ = self._commands.get_powerstat(to_addr=self._radio_addr)
        resp = await self._send_civ_expect(civ, label="get_powerstat")
        return parse_powerstat(resp)

    async def set_powerstat(self, on: bool) -> None:
        """Power the radio on or off (PowerControlCapable protocol).

        Note: IC-7610 via LAN may NAK a power-on command while the radio
        is still booting.  The command is fire-and-forget for power-on —
        a NAK is logged but not raised, since the radio does power up.
        """
        self._check_connected()
        civ = (
            self._commands.power_on(to_addr=self._radio_addr)
            if on
            else self._commands.power_off(to_addr=self._radio_addr)
        )
        resp = await self._send_civ_expect(civ, label="set_powerstat")
        ack = parse_ack_nak(resp)
        if ack is False:
            if on:
                # IC-7610 may NAK power-on while booting — not a real error
                import logging

                logging.getLogger(__name__).warning(
                    "Power ON got NAK (radio may still be booting — ignoring)"
                )
            else:
                raise CommandError("Radio rejected power off")

    # --- Memory Commands ---

    async def get_memory_mode(self) -> int:
        """Get currently selected memory channel (1-101).

        Raises:
            NotImplementedError: IC-7610 does not support reading the current
                memory channel. Command 0x08 is SELECT-only per the official
                CI-V Reference Manual.
        """
        raise NotImplementedError(
            "IC-7610 does not support reading the current memory channel. "
            "Command 0x08 is SELECT-only (no GET variant). "
            "See IC-7610 CI-V Reference Manual page 4."
        )

    async def set_memory_mode(self, channel: int) -> None:
        """Select memory channel (1-101)."""
        if not 1 <= channel <= 101:
            raise ValueError(f"Channel must be 1-101, got {channel}")
        await self._send_fire_and_forget(
            self._commands.build_memory_mode_set(channel, to_addr=self._radio_addr)
        )

    async def memory_write(self) -> None:
        """Write current VFO state to selected memory channel."""
        await self._send_fire_and_forget(
            self._commands.build_memory_write(to_addr=self._radio_addr)
        )

    async def memory_to_vfo(self, channel: int) -> None:
        """Load memory channel to VFO."""
        if not 1 <= channel <= 101:
            raise ValueError(f"Channel must be 1-101, got {channel}")
        await self._send_fire_and_forget(
            self._commands.build_memory_to_vfo(channel, to_addr=self._radio_addr)
        )

    async def memory_clear(self, channel: int) -> None:
        """Clear memory channel."""
        if not 1 <= channel <= 101:
            raise ValueError(f"Channel must be 1-101, got {channel}")
        await self._send_fire_and_forget(
            self._commands.build_memory_clear(channel, to_addr=self._radio_addr)
        )

    async def get_memory_contents(self, channel: int) -> MemoryChannel:
        """Read full memory channel data.

        Args:
            channel: Memory channel number (1-101).

        Raises:
            NotImplementedError: IC-7610 does not support reading memory
                contents. Command 0x1A 0x00 GET is not documented in the
                official CI-V Reference Manual.
        """
        if not 1 <= channel <= 101:
            raise ValueError(f"Channel must be 1-101, got {channel}")
        raise NotImplementedError(
            f"IC-7610 does not support reading memory channel {channel} contents. "
            "Command 0x1A 0x00 GET is not documented in the CI-V Reference Manual."
        )

    async def set_memory_contents(self, mem: MemoryChannel) -> None:
        """Write full memory channel data."""
        if not 1 <= mem.channel <= 101:
            raise ValueError(f"Channel must be 1-101, got {mem.channel}")
        await self._send_fire_and_forget(
            self._commands.build_memory_contents_set(mem, to_addr=self._radio_addr)
        )

    async def get_bsr(self, band: int, register: int) -> BandStackRegister:
        """Read band stacking register (band 0-24, register 1-3).

        Issues CI-V ``0x1A 0x01 <band> <register>`` and parses the band-stack
        response (frequency / mode / filter). Icom firmware (IC-7300,
        IC-7610, IC-705, IC-9700, X6200, ...) answers this read; the historic
        ``NotImplementedError`` was a stale IC-7610-era assumption.

        Args:
            band: Band number (0-24).
            register: Register number (1-3).

        Returns:
            The decoded band-stack register entry.
        """
        if not 0 <= band <= 24:
            raise ValueError(f"Band must be 0-24, got {band}")
        if not 1 <= register <= 3:
            raise ValueError(f"Register must be 1-3, got {register}")
        self._check_connected()
        civ = self._commands.get_bsr(band, register, to_addr=self._radio_addr)
        resp = await self._send_civ_expect(civ, label="get_bsr")
        return parse_band_stack_response(resp)

    async def set_bsr(self, bsr: BandStackRegister) -> None:
        """Write band stacking register."""
        if not 0 <= bsr.band <= 24:
            raise ValueError(f"Band must be 0-24, got {bsr.band}")
        if not 1 <= bsr.register <= 3:
            raise ValueError(f"Register must be 1-3, got {bsr.register}")
        await self._send_fire_and_forget(
            self._commands.set_bsr(bsr, to_addr=self._radio_addr)
        )

    # ------------------------------------------------------------------
    # Backward-compat aliases — old names kept for existing callers
    # ------------------------------------------------------------------

    get_frequency = get_freq
    set_frequency = set_freq
    get_power = get_rf_power
    set_power = set_rf_power
    start_scan = scan_start
    stop_scan = scan_stop
    speech = get_speech
    get_band_stack = get_bsr
    set_band_stack = set_bsr
    set_band = set_bsr  # BSR is the IC-7610's band select mechanism


class IcomRadio(CoreRadio):
    """LAN adapter for IC-7610 built on top of the shared executable core."""

    @staticmethod
    async def _flush_queue(transport: IcomTransport, max_pkts: int = 200) -> int:
        """Flush receive queue on the given transport (delegates to ControlPhaseRuntime)."""
        from rigplane._control_phase import ControlPhaseRuntime

        return await ControlPhaseRuntime._flush_queue(transport, max_pkts)

    pass


# ---------------------------------------------------------------------------
# Protocol compliance checks (not executed automatically — call explicitly)
# ---------------------------------------------------------------------------


def _check_protocol_compliance() -> None:
    """Verify IcomRadio satisfies all Radio protocol variants.

    Note: ``@runtime_checkable`` checks only method/attribute *existence*.
    It does not validate full runtime semantics.
    """
    from rigplane.radio_protocol import (
        AudioCapable,
        DualReceiverCapable,
        Radio,
        ReceiverBankCapable,
        ScopeCapable,
        VfoSlotCapable,
    )

    assert isinstance(IcomRadio(host=""), Radio), (
        "IcomRadio does not satisfy Radio protocol"
    )
    assert isinstance(IcomRadio(host=""), AudioCapable), (
        "IcomRadio does not satisfy AudioCapable protocol"
    )
    assert isinstance(IcomRadio(host=""), ScopeCapable), (
        "IcomRadio does not satisfy ScopeCapable protocol"
    )
    assert isinstance(IcomRadio(host=""), DualReceiverCapable), (
        "IcomRadio does not satisfy DualReceiverCapable protocol"
    )
    assert isinstance(IcomRadio(host=""), ReceiverBankCapable), (
        "IcomRadio does not satisfy ReceiverBankCapable protocol"
    )
    assert isinstance(IcomRadio(host=""), VfoSlotCapable), (
        "IcomRadio does not satisfy VfoSlotCapable protocol"
    )
