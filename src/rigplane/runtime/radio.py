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
from typing import TYPE_CHECKING, Literal, cast

if TYPE_CHECKING:
    from typing import Any, Awaitable, Callable

    from rigplane._runtime_protocols import ControlPhaseHost

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
from rigplane.runtime._civ_rx import CivRuntime
from rigplane.runtime._dual_rx_runtime import DualRxRuntimeMixin
from rigplane.runtime._scope_runtime import ScopeRuntimeMixin

# Import split modules
from rigplane.runtime._connection_state import RadioConnectionState
from rigplane.runtime._control_phase import (
    CONNINFO_SIZE,  # noqa: F401 (re-export for tests)
    OPENCLOSE_SIZE,  # noqa: F401 (re-export for tests)
    STATUS_SIZE,  # noqa: F401 (re-export for tests)
    TOKEN_ACK_SIZE,  # noqa: F401 (re-export for tests)
    ControlPhaseRuntime,
)
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
    build_memory_clear,
    build_memory_contents_set,
    build_memory_mode_set,
    build_memory_to_vfo,
    build_memory_write,
    get_acc1_mod_level,
    get_af_level,
    get_af_mute,
    get_agc,
    get_agc_time_constant,
    get_alc,
    get_antenna_1,
    get_antenna_2,
    get_anti_vox_gain,
    get_apf_type_level,
    get_audio_peak_filter,
    get_auto_notch,
    get_band_edge_freq,
    get_break_in,
    get_break_in_delay,
    get_civ_output_ant,
    get_civ_transceive,
    get_comp_meter,
    get_compressor,
    get_compressor_level,
    get_cw_pitch,
    get_dash_ratio,
    get_data1_mod_input,
    get_data2_mod_input,
    get_data3_mod_input,
    get_data_off_mod_input,
    get_dial_lock,
    get_digisel,
    get_digisel_shift,
    get_drive_gain,
    get_dual_watch,
    get_filter_shape,
    get_filter_width,
    get_id_meter,
    get_ip_plus,
    get_key_speed,
    get_lan_mod_level,
    get_manual_notch,
    get_manual_notch_width,
    get_mic_gain,
    get_monitor,
    get_monitor_gain,
    get_nb,
    get_nb_depth,
    get_nb_level,
    get_nb_width,
    get_notch_filter,
    get_nr,
    get_nr_level,
    get_overflow_status,
    get_pbt_inner,
    get_pbt_outer,
    get_power_meter,
    get_ref_adjust,
    get_rf_gain,
    get_rf_power,
    get_rit_frequency,
    get_rit_status,
    get_rit_tx_status,
    get_rx_antenna_ant1,
    get_rx_antenna_ant2,
    get_s_meter,
    get_s_meter_sql_status,
    get_speech,
    get_split,
    get_ssb_tx_bandwidth,
    get_swr,
    get_system_date,
    get_system_time,
    get_transceiver_id,
    get_tuner_status,
    get_tuning_step,
    get_twin_peak_filter,
    get_tx_freq_monitor,
    get_usb_mod_level,
    get_utc_offset,
    get_various_squelch,
    get_vd_meter,
    get_vox,
    get_vox_delay,
    get_vox_gain,
    get_xfc_status,
    parse_ack_nak,
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
    get_powerstat,
    parse_powerstat,
    power_off,
    power_on,
    ptt_off,
    ptt_on,
    quick_dual_watch,
    quick_split,
    scan_set_df_span,
    scan_set_resume,
    scan_start,
    scan_start_type,
    scan_stop,
    send_cw,
    set_acc1_mod_level,
    set_af_level,
    set_af_mute,
    set_agc,
    set_agc_time_constant,
    set_antenna_1,
    set_antenna_2,
    set_anti_vox_gain,
    set_apf_type_level,
    set_attenuator,
    set_attenuator_level,
    set_audio_peak_filter,
    set_auto_notch,
    set_break_in,
    set_break_in_delay,
    set_bsr,
    set_civ_output_ant,
    set_civ_transceive,
    set_compressor,
    set_compressor_level,
    set_cw_pitch,
    set_dash_ratio,
    set_data1_mod_input,
    set_data2_mod_input,
    set_data3_mod_input,
    set_data_off_mod_input,
    set_dial_lock,
    set_digisel,
    set_digisel_shift,
    set_drive_gain,
    set_dual_watch,
    set_filter_shape,
    set_freq,
    set_ip_plus,
    set_key_speed,
    set_lan_mod_level,
    set_manual_notch,
    set_manual_notch_width,
    set_mic_gain,
    set_mode,
    set_monitor,
    set_monitor_gain,
    set_nb,
    set_nb_depth,
    set_nb_level,
    set_nb_width,
    set_notch_filter,
    set_nr,
    set_nr_level,
    set_pbt_inner,
    set_pbt_outer,
    set_preamp,
    set_ref_adjust,
    set_rf_gain,
    set_rf_power,
    set_rit_frequency,
    set_rit_status,
    set_rit_tx_status,
    set_rx_antenna_ant1,
    set_rx_antenna_ant2,
    set_split,
    set_ssb_tx_bandwidth,
    set_system_date,
    set_system_time,
    set_tuner_status,
    set_tuning_step,
    set_twin_peak_filter,
    set_tx_freq_monitor,
    set_usb_mod_level,
    set_utc_offset,
    set_vox,
    set_vox_delay,
    set_vox_gain,
    set_xfc_status,
    stop_cw,
)
from rigplane.commands import (
    get_attenuator as get_attenuator_cmd,  # Transceiver status family (#136); VFO / Dual Watch / Scanning (#132); Tone/TSQL (#134); System/Config commands (#135); Memory and band-stacking (#133)
)
from rigplane.commands import get_data_mode as get_data_mode_cmd
from rigplane.commands import get_main_sub_tracking as _get_main_sub_tracking_cmd
from rigplane.commands import get_preamp as get_preamp_cmd
from rigplane.commands import get_repeater_tone as _get_repeater_tone_cmd
from rigplane.commands import get_repeater_tsql as _get_repeater_tsql_cmd
from rigplane.commands import get_tone_freq as _get_tone_freq_cmd
from rigplane.commands import get_tsql_freq as _get_tsql_freq_cmd
from rigplane.commands import set_data_mode as set_data_mode_cmd
from rigplane.commands import set_main_sub_tracking as _set_main_sub_tracking_cmd
from rigplane.commands import set_repeater_tone as _set_repeater_tone_cmd
from rigplane.commands import set_repeater_tsql as _set_repeater_tsql_cmd
from rigplane.commands import set_tone_freq as _set_tone_freq_cmd
from rigplane.commands import set_tsql_freq as _set_tsql_freq_cmd
from rigplane.commands import set_vfo as _select_vfo_cmd
from rigplane.core.exceptions import CommandError, TimeoutError
from rigplane.runtime.meter_cal import interpolate_swr
from rigplane.profiles import RadioProfile, resolve_radio_profile
from rigplane.core.radio_state import RadioState
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
    "RadioProfile",
    "AudioCodec",
    "RadioConnectionState",
    "ScopeFrame",
    "ScopeCompletionPolicy",
]


logger = logging.getLogger(__name__)

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

    # All public commands supported by Icom CI-V backends.
    _KNOWN_COMMANDS: frozenset[str] = frozenset(
        {
            # Frequency / mode / data
            "get_freq",
            "set_freq",
            "get_mode",
            "set_mode",
            "get_data_mode",
            "set_data_mode",
            "get_mode_enum",
            "get_mode_info",
            # TX
            "set_ptt",
            # Filter / DSP
            "get_filter",
            "set_filter",
            "get_filter_width",
            "set_filter_width",
            "get_filter_shape",
            "set_filter_shape",
            "set_nb",
            "get_nb",
            "set_nr",
            "get_nr",
            "set_digisel",
            "get_digisel",
            "set_ip_plus",
            "get_ip_plus",
            "set_agc",
            "get_agc",
            "get_auto_notch",
            "set_auto_notch",
            "get_manual_notch",
            "set_manual_notch",
            "get_manual_notch_width",
            "set_manual_notch_width",
            "get_audio_peak_filter",
            "set_audio_peak_filter",
            "get_twin_peak_filter",
            "set_twin_peak_filter",
            # Levels
            "set_af_level",
            "get_af_level",
            "set_rf_gain",
            "get_rf_gain",
            "set_squelch",
            "get_squelch",
            "get_nr_level",
            "set_nr_level",
            "get_nb_level",
            "set_nb_level",
            "get_mic_gain",
            "set_mic_gain",
            "get_drive_gain",
            "set_drive_gain",
            "get_compressor_level",
            "set_compressor_level",
            "get_monitor_gain",
            "set_monitor_gain",
            "get_vox_gain",
            "set_vox_gain",
            "get_anti_vox_gain",
            "set_anti_vox_gain",
            "get_apf_type_level",
            "set_apf_type_level",
            "get_pbt_inner",
            "set_pbt_inner",
            "get_pbt_outer",
            "set_pbt_outer",
            "get_cw_pitch",
            "set_cw_pitch",
            "get_notch_filter",
            "set_notch_filter",
            "get_ref_adjust",
            "set_ref_adjust",
            "get_digisel_shift",
            "set_digisel_shift",
            "get_nb_depth",
            "set_nb_depth",
            "get_nb_width",
            "set_nb_width",
            "get_dash_ratio",
            "set_dash_ratio",
            "get_break_in_delay",
            "set_break_in_delay",
            "get_vox_delay",
            "set_vox_delay",
            "get_af_mute",
            "set_af_mute",
            "get_agc_time_constant",
            "set_agc_time_constant",
            # Meters
            "get_s_meter",
            "get_swr",
            "get_swr_meter",
            "get_alc_meter",
            "get_rf_power",
            "set_rf_power",
            "get_power_meter",
            "get_comp_meter",
            "get_vd_meter",
            "get_id_meter",
            "get_s_meter_sql_status",
            "get_overflow_status",
            # CW
            "send_cw_text",
            "stop_cw_text",
            "get_key_speed",
            "set_key_speed",
            "get_break_in",
            "set_break_in",
            # Attenuator / preamp
            "get_attenuator",
            "set_attenuator",
            "get_attenuator_level",
            "set_attenuator_level",
            "get_preamp",
            "set_preamp",
            # Antenna
            "get_antenna_1",
            "set_antenna_1",
            "get_antenna_2",
            "set_antenna_2",
            "get_rx_antenna_ant1",
            "set_rx_antenna_ant1",
            "get_rx_antenna_ant2",
            "set_rx_antenna_ant2",
            # Toggles
            "get_compressor",
            "set_compressor",
            "get_monitor",
            "set_monitor",
            "get_vox",
            "set_vox",
            "get_dial_lock",
            "set_dial_lock",
            "get_dual_watch",
            "set_dual_watch",
            # VFO / split / scan
            "get_split",
            "set_split",
            "get_tuning_step",
            "set_tuning_step",
            "scan_start",
            "scan_stop",
            # Repeater tone
            "get_repeater_tone",
            "set_repeater_tone",
            "get_repeater_tsql",
            "set_repeater_tsql",
            "get_tone_freq",
            "set_tone_freq",
            "get_tsql_freq",
            "set_tsql_freq",
            # RIT / XIT
            "get_rit_frequency",
            "set_rit_frequency",
            "get_rit_status",
            "set_rit_status",
            "get_rit_tx_status",
            "set_rit_tx_status",
            "get_tx_freq_monitor",
            "set_tx_freq_monitor",
            # Tuner
            "get_tuner_status",
            "set_tuner_status",
            "get_xfc_status",
            "set_xfc_status",
            # Mod levels / input
            "get_acc1_mod_level",
            "set_acc1_mod_level",
            "get_usb_mod_level",
            "set_usb_mod_level",
            "get_lan_mod_level",
            "set_lan_mod_level",
            "get_data_off_mod_input",
            "set_data_off_mod_input",
            "get_data1_mod_input",
            "set_data1_mod_input",
            "get_data2_mod_input",
            "set_data2_mod_input",
            "get_data3_mod_input",
            "set_data3_mod_input",
            # System
            "get_system_date",
            "set_system_date",
            "get_system_time",
            "set_system_time",
            "get_utc_offset",
            "set_utc_offset",
            "get_civ_transceive",
            "set_civ_transceive",
            "get_civ_output_ant",
            "set_civ_output_ant",
            "get_powerstat",
            "set_powerstat",
            "get_transceiver_id",
            "get_speech",
            "get_band_edge_freq",
            "get_various_squelch",
            "set_band",
            # SSB TX bandwidth
            "get_ssb_tx_bandwidth",
            "set_ssb_tx_bandwidth",
            # Dual receiver
            "get_main_sub_tracking",
            "set_main_sub_tracking",
            # Memory
            "get_memory_mode",
            "set_memory_mode",
            "memory_write",
            "memory_to_vfo",
            "memory_clear",
            "get_memory_contents",
            "set_memory_contents",
            "get_bsr",
            "set_bsr",
            # Scope
            "enable_scope",
            "disable_scope",
            "get_scope_receiver",
            "set_scope_receiver",
            "get_scope_dual",
            "set_scope_dual",
            "get_scope_mode",
            "set_scope_mode",
            "get_scope_span",
            "set_scope_span",
            "get_scope_edge",
            "set_scope_edge",
            "get_scope_hold",
            "set_scope_hold",
            "get_scope_ref",
            "set_scope_ref",
            "get_scope_speed",
            "set_scope_speed",
            "get_scope_during_tx",
            "set_scope_during_tx",
            "get_scope_center_type",
            "set_scope_center_type",
            "get_scope_vbw",
            "set_scope_vbw",
            "get_scope_fixed_edge",
            "set_scope_fixed_edge",
            "get_scope_rbw",
            "set_scope_rbw",
            "capture_scope_frame",
            "capture_scope_frames",
            # Audio
            "start_audio_rx_opus",
            "stop_audio_rx_opus",
            "start_audio_rx_pcm",
            "stop_audio_rx_pcm",
            "start_audio_tx_opus",
            "stop_audio_tx_opus",
            "start_audio_tx_pcm",
            "stop_audio_tx_pcm",
            "push_audio_tx_opus",
            "push_audio_tx_pcm",
            # CI-V raw
            "send_civ",
        }
    )

    def supports_command(self, command: str) -> bool:
        """Check if this radio supports a specific command."""
        return command in self._KNOWN_COMMANDS

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
        self._scope_assembler: ScopeAssembler = ScopeAssembler()
        self._scope_callback: Callable[[ScopeFrame], Any] | None = None
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
        self._audio_runtime: AudioRecoveryRuntime = AudioRecoveryRuntime(self)

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
            from rigplane.audio_bus import AudioBus

            self._audio_bus = AudioBus(self)
        return self._audio_bus

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
        mode_key = mode.strip().upper()
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

    def civ_stats(self) -> dict[str, int]:
        """Return CI-V request tracker statistics for monitoring.

        Returns:
            Dict with keys ``active_waiters``, ``stale_cleaned``,
            ``timeouts``, ``generation``, ``ack_backlog_hits``,
            ``ack_backlog_drops``, and ``ack_orphans``.
        """
        return self._civ_request_tracker.snapshot_stats()

    async def connect(self) -> None:
        """Open connection to the radio and authenticate.

        Delegates to the composed ControlPhaseRuntime, then fetches
        initial radio state so RadioState is populated before consumers.
        """
        await self._control_phase.connect()
        await self._fetch_initial_state()

    async def __aenter__(self) -> "CoreRadio":
        await self.connect()
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb) -> None:  # type: ignore[no-untyped-def]
        await self.disconnect()

    async def disconnect(self) -> None:
        """Cleanly disconnect from the radio."""
        await self._control_phase.disconnect()

    async def soft_disconnect(self) -> None:
        """Disconnect CI-V and audio but keep control transport alive.

        This allows fast reconnect without re-authentication — the radio
        keeps the session open on the control port.
        """
        if self._conn_state != RadioConnectionState.CONNECTED:
            return
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
        """Reconnect CI-V transport using existing control session.

        Delegates to the composed ControlPhaseRuntime.
        """
        await self._control_phase.soft_reconnect()

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
        deadline_monotonic: float | None = None,
    ) -> CivFrame | None:
        """Delegate to CI-V runtime (for tests and internal callers)."""
        return await self._civ_runtime.execute_civ_raw(
            civ_frame,
            wait_response=wait_response,
            deadline_monotonic=deadline_monotonic,
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
    ) -> CivFrame | None:
        """Delegate to CI-V runtime (keeps existing call sites unchanged)."""
        return await self._civ_runtime.send_civ_raw(
            civ_frame,
            priority=priority,
            key=key,
            dedupe=dedupe,
            wait_response=wait_response,
            timeout=timeout,
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
    ) -> CivFrame | None:
        """Send a CI-V command.

        Args:
            command: CI-V command byte.
            sub: Optional sub-command byte.
            data: Optional payload data.
            wait_response: If False, fire-and-forget (no response wait).

        Returns:
            Parsed response CivFrame, or None if wait_response=False.
        """
        self._check_connected()
        frame = build_civ_frame(
            self._radio_addr, CONTROLLER_ADDR, command, sub=sub, data=data
        )
        return await self._send_civ_raw(frame, wait_response=wait_response)

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
            civ = set_freq(freq_hz, to_addr=self._radio_addr, receiver=receiver)
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

        Hz is translated to a profile-defined 1-byte BCD index (wfview's
        ``funcFilterWidth`` segmented formula — see ``icomcommander.cpp:1131``)
        and wrapped via cmd29 only when the profile lists ``[0x1A, 0x03]`` in
        its cmd29 routes (IC-7610). IC-705 and IC-9700 send the frame directly.

        Args:
            width_hz: Filter width in Hz. Bounds and step depend on the
                current mode's profile rule.
            receiver: 0=MAIN, 1=SUB.
        """
        self._check_connected()
        self._require_receiver(receiver, operation="set_filter_width")

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
        # (IC-705) and on dual-RX rigs without cmd29 support (IC-9700).
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

        Per wfview's ``funcFilterWidth`` handler (``icomcommander.cpp:1131``),
        all Icom rigs return a 1-byte BCD segmented index. The request is
        cmd29-wrapped only when the profile lists ``[0x1A, 0x03]`` in its
        cmd29 routes (IC-7610). IC-705 and IC-9700 send the request directly
        and the response is decoded with the same segmented formula
        (issue #1156).

        Args:
            receiver: 0=MAIN, 1=SUB.

        Returns:
            Filter width in Hz.
        """
        self._check_connected()
        self._require_receiver(receiver, operation="get_filter_width")

        # CI-V 1A 03: 1-byte BCD index for every Icom rig (wfview-confirmed).
        # cmd29-wrapped only for receiver routing on dual-RX rigs that list
        # the route (IC-7610). IC-705/IC-9700 send the request directly.
        if self._profile.supports_cmd29(0x1A, 0x03):
            civ = get_filter_width(to_addr=self._radio_addr, receiver=receiver)
        else:
            civ = build_civ_frame(self._radio_addr, CONTROLLER_ADDR, 0x1A, sub=0x03)

        value = await self._get_bcd_level(
            civ,
            key=f"get_filter_width:{receiver}",
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
            civ = set_mode(
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
        civ = get_data_mode_cmd(to_addr=self._radio_addr)
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
                civ = set_data_mode_cmd(
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
        civ = set_data_mode_cmd(on, to_addr=self._radio_addr, receiver=receiver)
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

    async def _send_fire_and_forget(self, civ: bytes) -> None:
        """Send a fire-and-forget CI-V command after connection checks."""
        self._check_connected()
        await self._send_civ_raw(civ, wait_response=False)

    async def get_rf_power(self) -> int:
        """Get the RF power level (0-255).

        On timeout falls back to the state cache if populated.
        """
        self._check_connected()
        civ = get_rf_power(to_addr=self._radio_addr)
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
        civ = set_rf_power(level, to_addr=self._radio_addr)
        await self._send_civ_raw(civ, wait_response=False)
        self._last_power = level

    async def get_rf_gain(self, receiver: int = 0) -> int:
        """Read the current RF gain level (0-255).

        Routes through cmd29 (0x29 0x01) for the SUB receiver, mirroring
        ``set_rf_gain``.
        """
        self._check_connected()
        self._require_receiver(receiver, operation="get_rf_gain")
        self._require_cmd29_route(
            0x14,
            0x02,
            receiver=receiver,
            operation="get_rf_gain",
        )
        civ = get_rf_gain(to_addr=self._radio_addr, receiver=receiver)
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
        self._require_cmd29_route(
            0x14,
            0x02,
            receiver=receiver,
            operation="set_rf_gain",
        )
        civ = set_rf_gain(level, to_addr=self._radio_addr, receiver=receiver)
        await self._send_civ_raw(civ, wait_response=False)

    async def get_af_level(self, receiver: int = 0) -> int:
        """Read the current AF output level (0-255).

        Routes through cmd29 (0x29 0x01) for the SUB receiver, mirroring
        ``set_af_level``.
        """
        self._check_connected()
        self._require_receiver(receiver, operation="get_af_level")
        self._require_cmd29_route(
            0x14,
            0x01,
            receiver=receiver,
            operation="get_af_level",
        )
        civ = get_af_level(to_addr=self._radio_addr, receiver=receiver)
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
        self._require_cmd29_route(
            0x14,
            0x01,
            receiver=receiver,
            operation="set_af_level",
        )
        civ = set_af_level(level, to_addr=self._radio_addr, receiver=receiver)
        await self._send_civ_raw(civ, wait_response=False)

    async def set_squelch(self, level: int, receiver: int = 0) -> None:
        """Set squelch level (0-255, 0=open)."""
        if not 0 <= level <= 255:
            raise ValueError(f"Squelch level must be 0-255, got {level}")
        self._check_connected()
        self._require_capability("squelch", operation="set_squelch")
        self._require_receiver(receiver, operation="set_squelch")
        self._require_cmd29_route(
            0x14,
            0x03,
            receiver=receiver,
            operation="set_squelch",
        )
        from rigplane.commands import set_squelch as _set_squelch

        civ = _set_squelch(level, to_addr=self._radio_addr, receiver=receiver)
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
        from rigplane.commands import get_squelch as _get_squelch

        civ = _get_squelch(to_addr=self._radio_addr, receiver=receiver)
        return await self._get_bcd_level(
            civ,
            key=f"get_squelch:{receiver}",
            command=0x14,
            sub=0x03,
        )

    async def get_apf_type_level(self, receiver: int = RECEIVER_MAIN) -> int:
        """Read APF Type Level (0-255)."""
        self._require_receiver(receiver, operation="get_apf_type_level")
        self._require_cmd29_route(
            0x14, 0x05, receiver=receiver, operation="get_apf_type_level"
        )
        civ = get_apf_type_level(to_addr=self._radio_addr, receiver=receiver)
        return await self._get_bcd_level(
            civ,
            key=f"get_apf_type_level:{receiver}",
            command=0x14,
            sub=0x05,
        )

    async def set_apf_type_level(
        self, level: int, receiver: int = RECEIVER_MAIN
    ) -> None:
        """Set APF Type Level (0-255)."""
        self._require_receiver(receiver, operation="set_apf_type_level")
        self._require_cmd29_route(
            0x14, 0x05, receiver=receiver, operation="set_apf_type_level"
        )
        await self._send_fire_and_forget(
            set_apf_type_level(level, to_addr=self._radio_addr, receiver=receiver)
        )

    async def get_nr_level(self, receiver: int = RECEIVER_MAIN) -> int:
        """Read NR Level (0-255)."""
        self._require_receiver(receiver, operation="get_nr_level")
        self._require_cmd29_route(
            0x14, 0x06, receiver=receiver, operation="get_nr_level"
        )
        civ = get_nr_level(to_addr=self._radio_addr, receiver=receiver)
        return await self._get_bcd_level(
            civ, key=f"get_nr_level:{receiver}", command=0x14, sub=0x06
        )

    async def set_nr_level(self, level: int, receiver: int = RECEIVER_MAIN) -> None:
        """Set NR Level (0-255)."""
        self._require_receiver(receiver, operation="set_nr_level")
        self._require_cmd29_route(
            0x14, 0x06, receiver=receiver, operation="set_nr_level"
        )
        await self._send_fire_and_forget(
            set_nr_level(level, to_addr=self._radio_addr, receiver=receiver)
        )

    async def get_pbt_inner(self, receiver: int = RECEIVER_MAIN) -> int:
        """Read PBT Inner level (0-255)."""
        self._require_receiver(receiver, operation="get_pbt_inner")
        self._require_cmd29_route(
            0x14, 0x07, receiver=receiver, operation="get_pbt_inner"
        )
        civ = get_pbt_inner(to_addr=self._radio_addr, receiver=receiver)
        return await self._get_bcd_level(
            civ, key=f"get_pbt_inner:{receiver}", command=0x14, sub=0x07
        )

    async def set_pbt_inner(self, level: int, receiver: int = RECEIVER_MAIN) -> None:
        """Set PBT Inner level (0-255)."""
        self._require_receiver(receiver, operation="set_pbt_inner")
        self._require_cmd29_route(
            0x14, 0x07, receiver=receiver, operation="set_pbt_inner"
        )
        await self._send_fire_and_forget(
            set_pbt_inner(level, to_addr=self._radio_addr, receiver=receiver)
        )

    async def get_pbt_outer(self, receiver: int = RECEIVER_MAIN) -> int:
        """Read PBT Outer level (0-255)."""
        self._require_receiver(receiver, operation="get_pbt_outer")
        self._require_cmd29_route(
            0x14, 0x08, receiver=receiver, operation="get_pbt_outer"
        )
        civ = get_pbt_outer(to_addr=self._radio_addr, receiver=receiver)
        return await self._get_bcd_level(
            civ, key=f"get_pbt_outer:{receiver}", command=0x14, sub=0x08
        )

    async def set_pbt_outer(self, level: int, receiver: int = RECEIVER_MAIN) -> None:
        """Set PBT Outer level (0-255)."""
        self._require_receiver(receiver, operation="set_pbt_outer")
        self._require_cmd29_route(
            0x14, 0x08, receiver=receiver, operation="set_pbt_outer"
        )
        await self._send_fire_and_forget(
            set_pbt_outer(level, to_addr=self._radio_addr, receiver=receiver)
        )

    async def get_cw_pitch(self) -> int:
        """Read CW pitch in Hz."""
        level = await self._get_bcd_level(
            get_cw_pitch(to_addr=self._radio_addr),
            key="get_cw_pitch",
            command=0x14,
            sub=0x09,
        )
        return round((((600.0 / 255.0) * level) + 300) / 5.0) * 5

    async def set_cw_pitch(self, pitch_hz: int) -> None:
        """Set CW pitch in Hz."""
        await self._send_fire_and_forget(
            set_cw_pitch(pitch_hz, to_addr=self._radio_addr)
        )

    async def get_mic_gain(self) -> int:
        """Read Mic Gain (0-255)."""
        return await self._get_bcd_level(
            get_mic_gain(to_addr=self._radio_addr),
            key="get_mic_gain",
            command=0x14,
            sub=0x0B,
        )

    async def set_mic_gain(self, level: int) -> None:
        """Set Mic Gain (0-255)."""
        await self._send_fire_and_forget(set_mic_gain(level, to_addr=self._radio_addr))

    async def get_key_speed(self) -> int:
        """Read key speed in WPM."""
        level = await self._get_bcd_level(
            get_key_speed(to_addr=self._radio_addr),
            key="get_key_speed",
            command=0x14,
            sub=0x0C,
        )
        return round((level / 6.071) + 6)

    async def set_key_speed(self, wpm: int) -> None:
        """Set key speed in WPM."""
        await self._send_fire_and_forget(set_key_speed(wpm, to_addr=self._radio_addr))

    async def get_notch_filter(self, receiver: int = RECEIVER_MAIN) -> int:
        """Read notch filter level (0-255)."""
        self._require_receiver(receiver, operation="get_notch_filter")
        self._require_cmd29_route(
            0x14, 0x0D, receiver=receiver, operation="get_notch_filter"
        )
        return await self._get_bcd_level(
            get_notch_filter(to_addr=self._radio_addr, receiver=receiver),
            key=f"get_notch_filter:{receiver}",
            command=0x14,
            sub=0x0D,
        )

    async def set_notch_filter(self, level: int, receiver: int = RECEIVER_MAIN) -> None:
        """Set notch filter level (0-255)."""
        self._require_receiver(receiver, operation="set_notch_filter")
        self._require_cmd29_route(
            0x14, 0x0D, receiver=receiver, operation="set_notch_filter"
        )
        await self._send_fire_and_forget(
            set_notch_filter(level, to_addr=self._radio_addr, receiver=receiver)
        )

    async def get_compressor_level(self) -> int:
        """Read compressor level (0-255)."""
        return await self._get_bcd_level(
            get_compressor_level(to_addr=self._radio_addr),
            key="get_compressor_level",
            command=0x14,
            sub=0x0E,
        )

    async def set_compressor_level(self, level: int) -> None:
        """Set compressor level (0-255)."""
        await self._send_fire_and_forget(
            set_compressor_level(level, to_addr=self._radio_addr)
        )

    async def get_break_in_delay(self) -> int:
        """Read break-in delay level (0-255)."""
        return await self._get_bcd_level(
            get_break_in_delay(to_addr=self._radio_addr),
            key="get_break_in_delay",
            command=0x14,
            sub=0x0F,
        )

    async def set_break_in_delay(self, level: int) -> None:
        """Set break-in delay level (0-255)."""
        await self._send_fire_and_forget(
            set_break_in_delay(level, to_addr=self._radio_addr)
        )

    async def get_nb_level(self, receiver: int = RECEIVER_MAIN) -> int:
        """Read NB level (0-255)."""
        self._require_receiver(receiver, operation="get_nb_level")
        self._require_cmd29_route(
            0x14, 0x12, receiver=receiver, operation="get_nb_level"
        )
        civ = get_nb_level(to_addr=self._radio_addr, receiver=receiver)
        return await self._get_bcd_level(
            civ, key=f"get_nb_level:{receiver}", command=0x14, sub=0x12
        )

    async def set_nb_level(self, level: int, receiver: int = RECEIVER_MAIN) -> None:
        """Set NB level (0-255)."""
        self._require_receiver(receiver, operation="set_nb_level")
        self._require_cmd29_route(
            0x14, 0x12, receiver=receiver, operation="set_nb_level"
        )
        await self._send_fire_and_forget(
            set_nb_level(level, to_addr=self._radio_addr, receiver=receiver)
        )

    async def get_digisel_shift(self, receiver: int = RECEIVER_MAIN) -> int:
        """Read DIGI-SEL Shift (0-255)."""
        self._require_receiver(receiver, operation="get_digisel_shift")
        self._require_cmd29_route(
            0x14, 0x13, receiver=receiver, operation="get_digisel_shift"
        )
        civ = get_digisel_shift(to_addr=self._radio_addr, receiver=receiver)
        return await self._get_bcd_level(
            civ, key=f"get_digisel_shift:{receiver}", command=0x14, sub=0x13
        )

    async def set_digisel_shift(
        self, level: int, receiver: int = RECEIVER_MAIN
    ) -> None:
        """Set DIGI-SEL Shift (0-255)."""
        self._require_receiver(receiver, operation="set_digisel_shift")
        self._require_cmd29_route(
            0x14, 0x13, receiver=receiver, operation="set_digisel_shift"
        )
        await self._send_fire_and_forget(
            set_digisel_shift(level, to_addr=self._radio_addr, receiver=receiver)
        )

    async def get_drive_gain(self) -> int:
        """Read drive gain (0-255)."""
        return await self._get_bcd_level(
            get_drive_gain(to_addr=self._radio_addr),
            key="get_drive_gain",
            command=0x14,
            sub=0x14,
        )

    async def set_drive_gain(self, level: int) -> None:
        """Set drive gain (0-255)."""
        await self._send_fire_and_forget(
            set_drive_gain(level, to_addr=self._radio_addr)
        )

    async def get_monitor_gain(self) -> int:
        """Read monitor gain (0-255)."""
        return await self._get_bcd_level(
            get_monitor_gain(to_addr=self._radio_addr),
            key="get_monitor_gain",
            command=0x14,
            sub=0x15,
        )

    async def set_monitor_gain(self, level: int) -> None:
        """Set monitor gain (0-255)."""
        await self._send_fire_and_forget(
            set_monitor_gain(level, to_addr=self._radio_addr)
        )

    async def get_vox_gain(self) -> int:
        """Read VOX gain (0-255)."""
        return await self._get_bcd_level(
            get_vox_gain(to_addr=self._radio_addr),
            key="get_vox_gain",
            command=0x14,
            sub=0x16,
        )

    async def set_vox_gain(self, level: int) -> None:
        """Set VOX gain (0-255)."""
        await self._send_fire_and_forget(set_vox_gain(level, to_addr=self._radio_addr))

    async def get_anti_vox_gain(self) -> int:
        """Read anti-VOX gain (0-255)."""
        return await self._get_bcd_level(
            get_anti_vox_gain(to_addr=self._radio_addr),
            key="get_anti_vox_gain",
            command=0x14,
            sub=0x17,
        )

    async def set_anti_vox_gain(self, level: int) -> None:
        """Set anti-VOX gain (0-255)."""
        await self._send_fire_and_forget(
            set_anti_vox_gain(level, to_addr=self._radio_addr)
        )

    async def get_ref_adjust(self) -> int:
        """Read REF Adjust (0-511)."""
        return await self._get_bcd_level(
            get_ref_adjust(to_addr=self._radio_addr),
            key="get_ref_adjust",
            command=0x1A,
            sub=0x05,
            prefix=b"\x00\x70",
        )

    async def set_ref_adjust(self, value: int) -> None:
        """Set REF Adjust (0-511)."""
        await self._send_fire_and_forget(
            set_ref_adjust(value, to_addr=self._radio_addr)
        )

    async def get_dash_ratio(self) -> int:
        """Read dash ratio (28-45)."""
        return await self._get_bcd_level(
            get_dash_ratio(to_addr=self._radio_addr),
            key="get_dash_ratio",
            command=0x1A,
            sub=0x05,
            prefix=b"\x02\x28",
            bcd_bytes=1,
        )

    async def set_dash_ratio(self, value: int) -> None:
        """Set dash ratio (28-45)."""
        await self._send_fire_and_forget(
            set_dash_ratio(value, to_addr=self._radio_addr)
        )

    async def get_nb_depth(self) -> int:
        """Read NB depth (0-9)."""
        return await self._get_bcd_level(
            get_nb_depth(to_addr=self._radio_addr),
            key="get_nb_depth",
            command=0x1A,
            sub=0x05,
            prefix=b"\x02\x90",
            bcd_bytes=1,
        )

    async def set_nb_depth(self, value: int) -> None:
        """Set NB depth (0-9)."""
        await self._send_fire_and_forget(set_nb_depth(value, to_addr=self._radio_addr))

    async def get_nb_width(self) -> int:
        """Read NB width (0-255)."""
        return await self._get_bcd_level(
            get_nb_width(to_addr=self._radio_addr),
            key="get_nb_width",
            command=0x1A,
            sub=0x05,
            prefix=b"\x02\x91",
        )

    async def set_nb_width(self, value: int) -> None:
        """Set NB width (0-255)."""
        await self._send_fire_and_forget(set_nb_width(value, to_addr=self._radio_addr))

    async def get_vox_delay(self) -> int:
        """Read VOX delay (0-20, units of 0.1s)."""
        return await self._get_bcd_level(
            get_vox_delay(to_addr=self._radio_addr),
            key="get_vox_delay",
            command=0x1A,
            sub=0x05,
            prefix=b"\x02\x92",
            bcd_bytes=1,
        )

    async def set_vox_delay(self, level: int) -> None:
        """Set VOX delay (0-20, units of 0.1s)."""
        await self._send_fire_and_forget(set_vox_delay(level, to_addr=self._radio_addr))

    async def get_af_mute(self, receiver: int = RECEIVER_MAIN) -> bool:
        """Read AF mute status."""
        self._require_receiver(receiver, operation="get_af_mute")
        self._require_cmd29_route(
            0x1A, 0x09, receiver=receiver, operation="get_af_mute"
        )
        civ = get_af_mute(to_addr=self._radio_addr, receiver=receiver)
        return await self._get_bool_value(
            civ, key=f"get_af_mute:{receiver}", command=0x1A, sub=0x09
        )

    async def set_af_mute(self, on: bool, receiver: int = RECEIVER_MAIN) -> None:
        """Set AF mute status."""
        self._require_receiver(receiver, operation="set_af_mute")
        self._require_cmd29_route(
            0x1A, 0x09, receiver=receiver, operation="set_af_mute"
        )
        await self._send_fire_and_forget(
            set_af_mute(on, to_addr=self._radio_addr, receiver=receiver)
        )

    async def get_s_meter_sql_status(self, receiver: int = RECEIVER_MAIN) -> bool:
        """Read S-meter squelch status for the selected receiver."""
        self._require_receiver(receiver, operation="get_s_meter_sql_status")
        self._require_cmd29_route(
            0x15, 0x01, receiver=receiver, operation="get_s_meter_sql_status"
        )
        civ = get_s_meter_sql_status(to_addr=self._radio_addr, receiver=receiver)
        return await self._get_bool_value(
            civ,
            key=f"get_s_meter_sql_status:{receiver}",
            command=0x15,
            sub=0x01,
        )

    async def get_overflow_status(self) -> bool:
        """Read OVF indicator status."""
        civ = get_overflow_status(to_addr=self._radio_addr)
        return await self._get_bool_value(
            civ,
            key="get_overflow_status",
            command=0x15,
            sub=0x07,
        )

    async def get_agc(self, receiver: int = RECEIVER_MAIN) -> AgcMode:
        """Read AGC mode."""
        self._require_receiver(receiver, operation="get_agc")
        command29 = receiver != RECEIVER_MAIN
        if command29:
            self._require_cmd29_route(
                0x16, 0x12, receiver=receiver, operation="get_agc"
            )
        value = await self._get_bcd_level(
            get_agc(to_addr=self._radio_addr, receiver=receiver),
            key=f"get_agc:{receiver}",
            command=0x16,
            sub=0x12,
            bcd_bytes=1,
        )
        return AgcMode(value)

    async def set_agc(self, mode: AgcMode | int, receiver: int = RECEIVER_MAIN) -> None:
        """Set AGC mode."""
        self._require_receiver(receiver, operation="set_agc")
        if receiver != RECEIVER_MAIN:
            self._require_cmd29_route(
                0x16, 0x12, receiver=receiver, operation="set_agc"
            )
        agc = AgcMode(mode)
        await self._send_fire_and_forget(
            set_agc(agc, to_addr=self._radio_addr, receiver=receiver)
        )

    async def get_audio_peak_filter(
        self, receiver: int = RECEIVER_MAIN
    ) -> AudioPeakFilter:
        """Read audio peak filter mode."""
        self._require_receiver(receiver, operation="get_audio_peak_filter")
        self._require_cmd29_route(
            0x16, 0x32, receiver=receiver, operation="get_audio_peak_filter"
        )
        value = await self._get_bcd_level(
            get_audio_peak_filter(to_addr=self._radio_addr, receiver=receiver),
            key=f"get_audio_peak_filter:{receiver}",
            command=0x16,
            sub=0x32,
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
        apf = AudioPeakFilter(mode)
        await self._send_fire_and_forget(
            set_audio_peak_filter(apf, to_addr=self._radio_addr, receiver=receiver)
        )

    async def get_auto_notch(self, receiver: int = RECEIVER_MAIN) -> bool:
        """Read auto-notch status."""
        self._require_receiver(receiver, operation="get_auto_notch")
        self._require_cmd29_route(
            0x16, 0x41, receiver=receiver, operation="get_auto_notch"
        )
        civ = get_auto_notch(to_addr=self._radio_addr, receiver=receiver)
        return await self._get_bool_value(
            civ,
            key=f"get_auto_notch:{receiver}",
            command=0x16,
            sub=0x41,
        )

    async def set_auto_notch(self, on: bool, receiver: int = RECEIVER_MAIN) -> None:
        """Set auto-notch status."""
        self._require_receiver(receiver, operation="set_auto_notch")
        self._require_cmd29_route(
            0x16, 0x41, receiver=receiver, operation="set_auto_notch"
        )
        await self._send_fire_and_forget(
            set_auto_notch(on, to_addr=self._radio_addr, receiver=receiver)
        )

    async def get_compressor(self) -> bool:
        """Read speech compressor status."""
        civ = get_compressor(to_addr=self._radio_addr)
        return await self._get_bool_value(
            civ, key="get_compressor", command=0x16, sub=0x44
        )

    async def set_compressor(self, on: bool) -> None:
        """Set speech compressor status."""
        await self._send_fire_and_forget(set_compressor(on, to_addr=self._radio_addr))

    async def get_monitor(self) -> bool:
        """Read monitor status."""
        civ = get_monitor(to_addr=self._radio_addr)
        return await self._get_bool_value(
            civ, key="get_monitor", command=0x16, sub=0x45
        )

    async def set_monitor(self, on: bool) -> None:
        """Set monitor status."""
        await self._send_fire_and_forget(set_monitor(on, to_addr=self._radio_addr))

    async def get_vox(self) -> bool:
        """Read VOX status."""
        civ = get_vox(to_addr=self._radio_addr)
        return await self._get_bool_value(civ, key="get_vox", command=0x16, sub=0x46)

    async def set_vox(self, on: bool) -> None:
        """Set VOX status."""
        await self._send_fire_and_forget(set_vox(on, to_addr=self._radio_addr))

    async def get_break_in(self) -> BreakInMode:
        """Read break-in mode."""
        value = await self._get_bcd_level(
            get_break_in(to_addr=self._radio_addr),
            key="get_break_in",
            command=0x16,
            sub=0x47,
            bcd_bytes=1,
        )
        return BreakInMode(value)

    async def set_break_in(self, mode: BreakInMode | int) -> None:
        """Set break-in mode."""
        break_in = BreakInMode(mode)
        await self._send_fire_and_forget(
            set_break_in(break_in, to_addr=self._radio_addr)
        )

    async def get_manual_notch(self, receiver: int = RECEIVER_MAIN) -> bool:
        """Read manual-notch status."""
        self._require_receiver(receiver, operation="get_manual_notch")
        self._require_cmd29_route(
            0x16, 0x48, receiver=receiver, operation="get_manual_notch"
        )
        civ = get_manual_notch(to_addr=self._radio_addr, receiver=receiver)
        return await self._get_bool_value(
            civ,
            key=f"get_manual_notch:{receiver}",
            command=0x16,
            sub=0x48,
        )

    async def set_manual_notch(self, on: bool, receiver: int = RECEIVER_MAIN) -> None:
        """Set manual-notch status."""
        self._require_receiver(receiver, operation="set_manual_notch")
        self._require_cmd29_route(
            0x16, 0x48, receiver=receiver, operation="set_manual_notch"
        )
        await self._send_fire_and_forget(
            set_manual_notch(on, to_addr=self._radio_addr, receiver=receiver)
        )

    async def get_manual_notch_width(self, receiver: int = RECEIVER_MAIN) -> int:
        """Read manual notch width (0=WIDE, 1=MID, 2=NAR)."""
        self._require_receiver(receiver, operation="get_manual_notch_width")
        self._require_cmd29_route(
            0x16, 0x57, receiver=receiver, operation="get_manual_notch_width"
        )
        civ = get_manual_notch_width(to_addr=self._radio_addr, receiver=receiver)
        return await self._get_bcd_level(
            civ,
            key=f"get_manual_notch_width:{receiver}",
            command=0x16,
            sub=0x57,
            bcd_bytes=1,
        )

    async def set_manual_notch_width(
        self, value: int, receiver: int = RECEIVER_MAIN
    ) -> None:
        """Set manual notch width (0=WIDE, 1=MID, 2=NAR)."""
        self._require_receiver(receiver, operation="set_manual_notch_width")
        self._require_cmd29_route(
            0x16, 0x57, receiver=receiver, operation="set_manual_notch_width"
        )
        await self._send_fire_and_forget(
            set_manual_notch_width(value, to_addr=self._radio_addr, receiver=receiver)
        )

    async def get_twin_peak_filter(self, receiver: int = RECEIVER_MAIN) -> bool:
        """Read twin peak filter status."""
        self._require_receiver(receiver, operation="get_twin_peak_filter")
        self._require_cmd29_route(
            0x16, 0x4F, receiver=receiver, operation="get_twin_peak_filter"
        )
        civ = get_twin_peak_filter(to_addr=self._radio_addr, receiver=receiver)
        return await self._get_bool_value(
            civ,
            key=f"get_twin_peak_filter:{receiver}",
            command=0x16,
            sub=0x4F,
        )

    async def set_twin_peak_filter(
        self, on: bool, receiver: int = RECEIVER_MAIN
    ) -> None:
        """Set twin peak filter status."""
        self._require_receiver(receiver, operation="set_twin_peak_filter")
        self._require_cmd29_route(
            0x16, 0x4F, receiver=receiver, operation="set_twin_peak_filter"
        )
        await self._send_fire_and_forget(
            set_twin_peak_filter(on, to_addr=self._radio_addr, receiver=receiver)
        )

    async def get_dial_lock(self) -> bool:
        """Read dial lock status."""
        civ = get_dial_lock(to_addr=self._radio_addr)
        return await self._get_bool_value(
            civ, key="get_dial_lock", command=0x16, sub=0x50
        )

    async def set_dial_lock(self, on: bool) -> None:
        """Set dial lock status."""
        await self._send_fire_and_forget(set_dial_lock(on, to_addr=self._radio_addr))

    async def get_filter_shape(self, receiver: int = RECEIVER_MAIN) -> FilterShape:
        """Read DSP IF filter shape."""
        self._require_receiver(receiver, operation="get_filter_shape")
        self._require_cmd29_route(
            0x16, 0x56, receiver=receiver, operation="get_filter_shape"
        )
        value = await self._get_bcd_level(
            get_filter_shape(to_addr=self._radio_addr, receiver=receiver),
            key=f"get_filter_shape:{receiver}",
            command=0x16,
            sub=0x56,
            bcd_bytes=1,
        )
        return FilterShape(value)

    async def set_filter_shape(
        self,
        shape: FilterShape | int,
        receiver: int = RECEIVER_MAIN,
    ) -> None:
        """Set DSP IF filter shape."""
        self._require_receiver(receiver, operation="set_filter_shape")
        self._require_cmd29_route(
            0x16, 0x56, receiver=receiver, operation="set_filter_shape"
        )
        filter_shape = FilterShape(shape)
        await self._send_fire_and_forget(
            set_filter_shape(
                filter_shape,
                to_addr=self._radio_addr,
                receiver=receiver,
            )
        )

    async def get_ssb_tx_bandwidth(self) -> SsbTxBandwidth:
        """Read SSB transmit bandwidth preset."""
        value = await self._get_bcd_level(
            get_ssb_tx_bandwidth(to_addr=self._radio_addr),
            key="get_ssb_tx_bandwidth",
            command=0x16,
            sub=0x58,
            bcd_bytes=1,
        )
        return SsbTxBandwidth(value)

    async def set_ssb_tx_bandwidth(self, bandwidth: SsbTxBandwidth | int) -> None:
        """Set SSB transmit bandwidth preset."""
        ssb_tx_bandwidth = SsbTxBandwidth(bandwidth)
        await self._send_fire_and_forget(
            set_ssb_tx_bandwidth(ssb_tx_bandwidth, to_addr=self._radio_addr)
        )

    async def get_main_sub_tracking(self) -> bool:
        """Read Main/Sub Tracking status."""
        civ = _get_main_sub_tracking_cmd(to_addr=self._radio_addr)
        return await self._get_bool_value(
            civ, key="get_main_sub_tracking", command=0x16, sub=0x5E
        )

    async def set_main_sub_tracking(self, on: bool) -> None:
        """Set Main/Sub Tracking status."""
        await self._send_fire_and_forget(
            _set_main_sub_tracking_cmd(on, to_addr=self._radio_addr)
        )

    async def get_agc_time_constant(self, receiver: int = RECEIVER_MAIN) -> int:
        """Read AGC time constant preset (0-13)."""
        self._require_receiver(receiver, operation="get_agc_time_constant")
        self._require_cmd29_route(
            0x1A, 0x04, receiver=receiver, operation="get_agc_time_constant"
        )
        return await self._get_bcd_level(
            get_agc_time_constant(to_addr=self._radio_addr, receiver=receiver),
            key=f"get_agc_time_constant:{receiver}",
            command=0x1A,
            sub=0x04,
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
        await self._send_fire_and_forget(
            set_agc_time_constant(
                value,
                to_addr=self._radio_addr,
                receiver=receiver,
            )
        )

    async def get_s_meter(self) -> int:
        """Read the S-meter value (0-255)."""
        self._check_connected()
        civ = get_s_meter(to_addr=self._radio_addr)
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
        civ = get_swr(to_addr=self._radio_addr)
        resp = await self._send_civ_expect(civ, label="get_swr")
        raw = parse_meter_response(resp)
        return interpolate_swr(raw, self._profile.meter_calibrations)

    async def get_swr_meter(self) -> int:
        """Read the raw SWR meter value (0-255).

        Mirrors the Yaesu ``*_meter`` naming on ``MetersCapable``. For a
        calibrated SWR ratio (>= 1.0) use :meth:`get_swr`.
        """
        self._check_connected()
        civ = get_swr(to_addr=self._radio_addr)
        resp = await self._send_civ_expect(civ, label="get_swr_meter")
        return parse_meter_response(resp)

    async def get_alc_meter(self) -> int:
        """Read the ALC meter value (raw 0-255)."""
        self._check_connected()
        civ = get_alc(to_addr=self._radio_addr)
        resp = await self._send_civ_expect(civ, label="get_alc_meter")
        return parse_meter_response(resp)

    async def set_ptt(self, on: bool) -> None:
        """Toggle PTT (Push-To-Talk).

        Fire-and-forget: the command is sent at IMMEDIATE priority without
        blocking for an ACK.  The state cache is updated optimistically.

        Args:
            on: True for TX, False for RX.
        """
        self._check_connected()
        civ = (
            ptt_on(to_addr=self._radio_addr)
            if on
            else ptt_off(to_addr=self._radio_addr)
        )
        await self._send_civ_raw(civ, priority=Priority.IMMEDIATE, wait_response=False)
        self._state_cache.update_ptt(on)
        logger.debug("set_ptt(%s) sent (fire-and-forget)", on)

    # ------------------------------------------------------------------
    # Transceiver status family (#136)
    # ------------------------------------------------------------------

    async def get_band_edge_freq(self) -> int:
        """Read the current band-edge frequency in Hz."""
        self._check_connected()
        civ = get_band_edge_freq(to_addr=self._radio_addr)
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
        civ = get_various_squelch(to_addr=self._radio_addr, receiver=receiver)
        return await self._get_bool_value(
            civ,
            key=f"get_various_squelch:{receiver}",
            command=0x15,
            sub=0x05,
        )

    async def get_power_meter(self) -> int:
        """Read the RF power meter (0-255 raw BCD)."""
        self._check_connected()
        civ = get_power_meter(to_addr=self._radio_addr)
        resp = await self._send_civ_expect(civ, label="get_power_meter")
        return parse_meter_response(resp)

    async def get_comp_meter(self) -> int:
        """Read the compressor meter (0-255 raw BCD)."""
        self._check_connected()
        civ = get_comp_meter(to_addr=self._radio_addr)
        resp = await self._send_civ_expect(civ, label="get_comp_meter")
        return parse_meter_response(resp)

    async def get_vd_meter(self) -> int:
        """Read the Vd supply voltage meter (0-255 raw BCD)."""
        self._check_connected()
        civ = get_vd_meter(to_addr=self._radio_addr)
        resp = await self._send_civ_expect(civ, label="get_vd_meter")
        return parse_meter_response(resp)

    async def get_id_meter(self) -> int:
        """Read the Id drain current meter (0-255 raw BCD)."""
        self._check_connected()
        civ = get_id_meter(to_addr=self._radio_addr)
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
        civ = get_speech(what, to_addr=self._radio_addr)
        await self._send_civ_raw(civ, wait_response=False)

    async def get_transceiver_id(self) -> int:
        """Read the transceiver model ID (IC-7610 = 0x98)."""
        self._check_connected()
        civ = get_transceiver_id(to_addr=self._radio_addr)
        resp = await self._send_civ_expect(civ, label="get_transceiver_id")
        if resp.data:
            return resp.data[0]
        return 0

    async def get_tuner_status(self) -> int:
        """Read the tuner/ATU status (0=off, 1=on, 2=tuning)."""
        self._check_connected()
        civ = get_tuner_status(to_addr=self._radio_addr)
        resp = await self._send_civ_expect(civ, label="get_tuner_status")
        if resp.data:
            return resp.data[0]
        return 0

    async def set_tuner_status(self, value: int) -> None:
        """Set the tuner/ATU status (0=off, 1=on, 2=tune).

        Fire-and-forget SET command.
        """
        self._check_connected()
        civ = set_tuner_status(value, to_addr=self._radio_addr)
        await self._send_civ_raw(civ, wait_response=False)

    async def get_xfc_status(self) -> bool:
        """Read XFC (transmit frequency correction) status."""
        self._check_connected()
        civ = get_xfc_status(to_addr=self._radio_addr)
        resp = await self._send_civ_expect(civ, label="get_xfc_status")
        return bool(resp.data[0]) if resp.data else False

    async def set_xfc_status(self, on: bool) -> None:
        """Set XFC status on/off. Fire-and-forget."""
        self._check_connected()
        civ = set_xfc_status(on, to_addr=self._radio_addr)
        await self._send_civ_raw(civ, wait_response=False)

    async def get_tx_freq_monitor(self) -> bool:
        """Read TX frequency monitor status."""
        self._check_connected()
        civ = get_tx_freq_monitor(to_addr=self._radio_addr)
        resp = await self._send_civ_expect(civ, label="get_tx_freq_monitor")
        return bool(resp.data[0]) if resp.data else False

    async def set_tx_freq_monitor(self, on: bool) -> None:
        """Set TX frequency monitor on/off. Fire-and-forget."""
        self._check_connected()
        civ = set_tx_freq_monitor(on, to_addr=self._radio_addr)
        await self._send_civ_raw(civ, wait_response=False)

    async def get_rit_frequency(self) -> int:
        """Read the RIT frequency offset in Hz (±9999)."""
        self._check_connected()
        civ = get_rit_frequency(to_addr=self._radio_addr)
        resp = await self._send_civ_expect(civ, label="get_rit_frequency")
        return parse_rit_frequency_response(resp.data)

    async def set_rit_frequency(self, offset_hz: int) -> None:
        """Set the RIT frequency offset in Hz (±9999). Fire-and-forget."""
        self._check_connected()
        civ = set_rit_frequency(offset_hz, to_addr=self._radio_addr)
        await self._send_civ_raw(civ, wait_response=False)

    async def get_rit_status(self) -> bool:
        """Read RIT on/off status."""
        self._check_connected()
        civ = get_rit_status(to_addr=self._radio_addr)
        resp = await self._send_civ_expect(civ, label="get_rit_status")
        return bool(resp.data[0]) if resp.data else False

    async def set_rit_status(self, on: bool) -> None:
        """Set RIT on/off. Fire-and-forget."""
        self._check_connected()
        civ = set_rit_status(on, to_addr=self._radio_addr)
        await self._send_civ_raw(civ, wait_response=False)

    async def get_rit_tx_status(self) -> bool:
        """Read RIT TX status."""
        self._check_connected()
        civ = get_rit_tx_status(to_addr=self._radio_addr)
        resp = await self._send_civ_expect(civ, label="get_rit_tx_status")
        return bool(resp.data[0]) if resp.data else False

    async def set_rit_tx_status(self, on: bool) -> None:
        """Set RIT TX status on/off. Fire-and-forget."""
        self._check_connected()
        civ = set_rit_tx_status(on, to_addr=self._radio_addr)
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
        """
        self._check_connected()
        civ = _select_vfo_cmd(vfo, to_addr=self._radio_addr)
        resp = await self._send_civ_expect(civ, label="set_vfo")
        ack = parse_ack_nak(resp)
        if ack is False:
            raise CommandError(f"Radio rejected VFO select {vfo}")
        self._last_vfo = vfo.upper()

    async def set_split(self, on: bool) -> None:
        """Enable or disable split mode (CI-V ``0x0F``)."""
        self._check_connected()
        civ = set_split(on, to_addr=self._radio_addr)
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
        civ = get_split(to_addr=self._radio_addr)
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
        civ = get_tuning_step(to_addr=self._radio_addr)
        resp = await self._send_civ_expect(civ, label="get_tuning_step")
        if resp.data:
            b = resp.data[0]
            return ((b >> 4) & 0x0F) * 10 + (b & 0x0F)
        return 0

    async def set_tuning_step(self, step: int) -> None:
        """Set the tuning step index (0-8, BCD-encoded, CI-V 0x10). Fire-and-forget."""
        self._check_connected()
        civ = set_tuning_step(step, to_addr=self._radio_addr)
        await self._send_civ_raw(civ, wait_response=False)

    async def scan_start(self, mode: int = 0) -> None:
        """Start scanning (CI-V 0x0E). Fire-and-forget.

        Args:
            mode: Scan type sub-byte.  0 (default) sends 0x01 (programmed scan)
                  for backward compatibility.  Non-zero values are forwarded
                  directly as the scan-type sub-byte (e.g. 0x03 = ΔF scan).
        """
        self._check_connected()
        if mode == 0:
            civ = scan_start(to_addr=self._radio_addr)
        else:
            civ = scan_start_type(mode, to_addr=self._radio_addr)
        await self._send_civ_raw(civ, wait_response=False)

    async def scan_stop(self) -> None:
        """Stop scanning (CI-V 0x0E 0x00). Fire-and-forget."""
        self._check_connected()
        civ = scan_stop(to_addr=self._radio_addr)
        await self._send_civ_raw(civ, wait_response=False)

    async def scan_set_df_span(self, span: int) -> None:
        """Set ΔF scan span (CI-V 0x0E 0xA1-0xA7). Fire-and-forget."""
        self._check_connected()
        civ = scan_set_df_span(span, to_addr=self._radio_addr)
        await self._send_civ_raw(civ, wait_response=False)

    async def scan_set_resume(self, mode: int) -> None:
        """Set scan resume mode (CI-V 0x0E 0xD0-0xD3). Fire-and-forget."""
        self._check_connected()
        civ = scan_set_resume(mode, to_addr=self._radio_addr)
        await self._send_civ_raw(civ, wait_response=False)

    async def get_dual_watch(self) -> bool:
        """Query dual watch status (CI-V 0x07 0xC2).

        Returns:
            True if dual watch is enabled, False otherwise.
        """
        self._check_connected()
        civ = get_dual_watch(to_addr=self._radio_addr)
        resp = await self._send_civ_expect(civ, label="get_dual_watch")
        # Response: cmd=0x07, data=[0xC2, <value>]
        if resp.data and len(resp.data) >= 2 and resp.data[0] == 0xC2:
            return bool(resp.data[1])
        return False

    async def set_dual_watch(self, on: bool) -> None:
        """Enable or disable dual watch (CI-V 0x07 0xC0/0xC1). Fire-and-forget."""
        self._check_connected()
        civ = set_dual_watch(on, to_addr=self._radio_addr)
        await self._send_civ_raw(civ, wait_response=False)

    async def quick_dual_watch(self) -> None:
        """One-shot dual watch trigger (CI-V 0x1A 0x05 0x00 0x32). Fire-and-forget."""
        self._check_connected()
        civ = quick_dual_watch(to_addr=self._radio_addr)
        await self._send_civ_raw(civ, wait_response=False)

    async def quick_split(self) -> None:
        """One-shot split trigger (CI-V 0x1A 0x05 0x00 0x33). Fire-and-forget."""
        self._check_connected()
        civ = quick_split(to_addr=self._radio_addr)
        await self._send_civ_raw(civ, wait_response=False)

    async def get_attenuator_level(self, receiver: int = RECEIVER_MAIN) -> int:
        """Read attenuator level in dB (Command29-aware).

        Args:
            receiver: RECEIVER_MAIN (0) or RECEIVER_SUB (1).
        """
        self._check_connected()
        self._require_capability("attenuator", operation="get_attenuator_level")
        self._require_receiver(receiver, operation="get_attenuator_level")
        if not self._profile.supports_cmd29(0x11):
            raise CommandError(
                f"get_attenuator_level is unsupported by profile {self._profile.model}: "
                "no cmd29 route for command 0x11"
            )
        civ = get_attenuator_cmd(to_addr=self._radio_addr, receiver=receiver)
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

    async def set_attenuator_level(
        self, db: int, receiver: int = RECEIVER_MAIN
    ) -> None:
        """Set attenuator level in dB (Command29-aware).

        Fire-and-forget: the command is sent without waiting for an ACK.
        The attenuator state is updated optimistically.

        Args:
            db: Attenuation in dB (0..45 in 3 dB steps).
            receiver: RECEIVER_MAIN (0) or RECEIVER_SUB (1).
        """
        self._check_connected()
        self._require_capability("attenuator", operation="set_attenuator_level")
        self._require_receiver(receiver, operation="set_attenuator_level")
        if not self._profile.supports_cmd29(0x11):
            raise CommandError(
                f"set_attenuator_level is unsupported by profile {self._profile.model}: "
                "no cmd29 route for command 0x11"
            )
        if db < 0 or db > 45 or db % 3 != 0:
            raise ValueError(f"Attenuator level must be 0..45 in 3 dB steps, got {db}")
        civ = set_attenuator_level(db, to_addr=self._radio_addr, receiver=receiver)
        await self._send_civ_raw(civ, wait_response=False)
        self._attenuator_state = db > 0
        logger.debug("set_attenuator(%d dB) sent (fire-and-forget)", db)

    async def set_attenuator(self, on: bool, receiver: int = RECEIVER_MAIN) -> None:
        """Enable or disable attenuator (compat wrapper, Command29-aware)."""
        self._check_connected()
        self._require_capability("attenuator", operation="set_attenuator")
        self._require_receiver(receiver, operation="set_attenuator")
        if not self._profile.supports_cmd29(0x11):
            raise CommandError(
                f"set_attenuator is unsupported by profile {self._profile.model}: "
                "no cmd29 route for command 0x11"
            )
        civ = set_attenuator(on, to_addr=self._radio_addr, receiver=receiver)
        await self._send_civ_raw(civ, wait_response=False)
        self._attenuator_state = on

    async def get_preamp(self, receiver: int = RECEIVER_MAIN) -> int:
        """Read preamp level (0=off, 1=PREAMP1, 2=PREAMP2) (Command29-aware).

        Args:
            receiver: RECEIVER_MAIN (0) or RECEIVER_SUB (1).
        """
        self._check_connected()
        self._require_capability("preamp", operation="get_preamp")
        self._require_receiver(receiver, operation="get_preamp")
        if not self._profile.supports_cmd29(0x16, 0x02):
            raise CommandError(
                f"get_preamp is unsupported by profile {self._profile.model}: "
                "no cmd29 route for command 0x16/0x02"
            )
        civ = get_preamp_cmd(to_addr=self._radio_addr, receiver=receiver)
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
        """Set preamp level (0=off, 1=PREAMP1, 2=PREAMP2) (Command29-aware).

        Args:
            level: 0=off, 1=PREAMP1, 2=PREAMP2.
            receiver: RECEIVER_MAIN (0) or RECEIVER_SUB (1).

        Raises:
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

        # Pre-flight: check DIGI-SEL / PREAMP mutual exclusion
        if level > 0:
            try:
                if await self.get_digisel():
                    raise CommandError(
                        f"Cannot set preamp level {level}: DIGI-SEL (IP+) is ON. "
                        "PREAMP and DIGI-SEL are mutually exclusive — disable DIGI-SEL first."
                    )
            except CommandError as exc:
                if "DIGI-SEL" in str(exc) and "mutually exclusive" in str(exc):
                    raise  # Our own error — propagate
                # get_digisel() failed (radio doesn't support it, timeout, etc.) — ignore
            except Exception:
                logger.debug(
                    "set_preamp: unexpected error checking DIGI-SEL, proceeding",
                    exc_info=True,
                )

        civ = set_preamp(level, to_addr=self._radio_addr, receiver=receiver)
        await self._send_civ_raw(civ, wait_response=False)
        self._preamp_level = level

    async def get_digisel(self) -> bool:
        """Read DIGI-SEL status (IC-7610 frontend selector)."""
        self._check_connected()
        self._require_capability("digisel", operation="get_digisel")
        if not self._profile.supports_cmd29(0x16, 0x4E):
            raise CommandError(
                f"get_digisel is unsupported by profile {self._profile.model}: "
                "no cmd29 route for command 0x16/0x4E"
            )
        civ = get_digisel(to_addr=self._radio_addr)
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
        civ = set_digisel(on, to_addr=self._radio_addr, receiver=receiver)
        resp = await self._send_civ_expect(civ, label="set_digisel")
        ack = parse_ack_nak(resp)
        if ack is False:
            raise CommandError(f"Radio rejected DIGI-SEL {'on' if on else 'off'}")

    async def get_nb(self) -> bool:
        """Read Noise Blanker status."""
        self._check_connected()
        civ = get_nb(to_addr=self._radio_addr)
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
        civ = set_nb(on, to_addr=self._radio_addr, receiver=receiver)
        await self._send_civ_raw(civ, wait_response=False)

    async def get_nr(self) -> bool:
        """Read Noise Reduction status."""
        self._check_connected()
        civ = get_nr(to_addr=self._radio_addr)
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
        civ = set_nr(on, to_addr=self._radio_addr, receiver=receiver)
        await self._send_civ_raw(civ, wait_response=False)

    async def get_ip_plus(self) -> bool:
        """Read IP+ status."""
        self._check_connected()
        civ = get_ip_plus(to_addr=self._radio_addr)
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
        civ = set_ip_plus(on, to_addr=self._radio_addr, receiver=receiver)
        await self._send_civ_raw(civ, wait_response=False)

    async def get_repeater_tone(self, receiver: int = 0) -> bool:
        """Read repeater tone status (0x16 0x42)."""
        civ = _get_repeater_tone_cmd(to_addr=self._radio_addr, receiver=receiver)
        return await self._get_bool_value(
            civ, key="get_repeater_tone", command=0x16, sub=_SUB_REPEATER_TONE
        )

    async def set_repeater_tone(self, on: bool, receiver: int = 0) -> None:
        """Set repeater tone on/off (0x16 0x42)."""
        await self._send_fire_and_forget(
            _set_repeater_tone_cmd(on, to_addr=self._radio_addr, receiver=receiver)
        )

    async def get_repeater_tsql(self, receiver: int = 0) -> bool:
        """Read repeater TSQL status (0x16 0x43)."""
        civ = _get_repeater_tsql_cmd(to_addr=self._radio_addr, receiver=receiver)
        return await self._get_bool_value(
            civ, key="get_repeater_tsql", command=0x16, sub=_SUB_REPEATER_TSQL
        )

    async def set_repeater_tsql(self, on: bool, receiver: int = 0) -> None:
        """Set repeater TSQL on/off (0x16 0x43)."""
        await self._send_fire_and_forget(
            _set_repeater_tsql_cmd(on, to_addr=self._radio_addr, receiver=receiver)
        )

    async def get_tone_freq(self, receiver: int = 0) -> float:
        """Read CTCSS tone frequency in Hz (0x1B 0x00)."""
        self._check_connected()
        civ = _get_tone_freq_cmd(to_addr=self._radio_addr, receiver=receiver)
        resp = await self._send_civ_expect(civ, label="get_tone_freq")
        _, freq = parse_tone_freq_response(resp)
        return freq

    async def set_tone_freq(self, freq_hz: float, receiver: int = 0) -> None:
        """Set CTCSS tone frequency in Hz (0x1B 0x00)."""
        await self._send_fire_and_forget(
            _set_tone_freq_cmd(freq_hz, to_addr=self._radio_addr, receiver=receiver)
        )

    async def get_tsql_freq(self, receiver: int = 0) -> float:
        """Read TSQL frequency in Hz (0x1B 0x01)."""
        self._check_connected()
        civ = _get_tsql_freq_cmd(to_addr=self._radio_addr, receiver=receiver)
        resp = await self._send_civ_expect(civ, label="get_tsql_freq")
        _, freq = parse_tsql_freq_response(resp)
        return freq

    async def set_tsql_freq(self, freq_hz: float, receiver: int = 0) -> None:
        """Set TSQL frequency in Hz (0x1B 0x01)."""
        await self._send_fire_and_forget(
            _set_tsql_freq_cmd(freq_hz, to_addr=self._radio_addr, receiver=receiver)
        )

    # ------------------------------------------------------------------
    # System/Config commands (#135)
    # ------------------------------------------------------------------

    async def get_antenna_1(self) -> bool:
        """Read ANT1 selection status (0x12 0x00)."""
        return await self._get_bool_value(
            get_antenna_1(to_addr=self._radio_addr),
            key="get_antenna_1",
            command=0x12,
            sub=0x00,
        )

    async def set_antenna_1(self, enabled: bool) -> None:
        """Select ANT1 (0x12 0x00 <00|01>).

        IC-7610: data byte encodes RX-ANT OFF/ON.
        """
        await self._send_fire_and_forget(
            set_antenna_1(enabled, to_addr=self._radio_addr)
        )

    async def get_antenna_2(self) -> bool:
        """Read ANT2 selection status (0x12 0x01)."""
        return await self._get_bool_value(
            get_antenna_2(to_addr=self._radio_addr),
            key="get_antenna_2",
            command=0x12,
            sub=0x01,
        )

    async def set_antenna_2(self, enabled: bool) -> None:
        """Select ANT2 (0x12 0x01 <00|01>).

        IC-7610: data byte encodes RX-ANT OFF/ON.
        """
        await self._send_fire_and_forget(
            set_antenna_2(enabled, to_addr=self._radio_addr)
        )

    async def get_rx_antenna_ant1(self) -> bool:
        """Read RX ANT state for ANT1.

        NOTE: On IC-7610 this is implemented via 0x12 0x00 and may select ANT1.
        """
        return await self._get_bool_value(
            get_rx_antenna_ant1(to_addr=self._radio_addr),
            key="get_rx_antenna_ant1",
            command=0x12,
            sub=0x00,
        )

    async def set_rx_antenna_ant1(self, enabled: bool) -> None:
        """Set RX ANT state for ANT1 (0x12 0x00 <00|01>)."""
        await self._send_fire_and_forget(
            set_rx_antenna_ant1(enabled, to_addr=self._radio_addr)
        )

    async def get_rx_antenna_ant2(self) -> bool:
        """Read RX ANT state for ANT2.

        NOTE: On IC-7610 this is implemented via 0x12 0x01 and may select ANT2.
        """
        return await self._get_bool_value(
            get_rx_antenna_ant2(to_addr=self._radio_addr),
            key="get_rx_antenna_ant2",
            command=0x12,
            sub=0x01,
        )

    async def set_rx_antenna_ant2(self, enabled: bool) -> None:
        """Set RX ANT state for ANT2 (0x12 0x01 <00|01>)."""
        await self._send_fire_and_forget(
            set_rx_antenna_ant2(enabled, to_addr=self._radio_addr)
        )

    async def get_acc1_mod_level(self) -> int:
        """Read ACC1 modulation level (0-255)."""
        return await self._get_bcd_level(
            get_acc1_mod_level(to_addr=self._radio_addr),
            key="get_acc1_mod_level",
            command=0x14,
            sub=0x0B,
        )

    async def set_acc1_mod_level(self, level: int) -> None:
        """Set ACC1 modulation level (0-255)."""
        await self._send_fire_and_forget(
            set_acc1_mod_level(level, to_addr=self._radio_addr)
        )

    async def get_usb_mod_level(self) -> int:
        """Read USB modulation level (0-255)."""
        return await self._get_bcd_level(
            get_usb_mod_level(to_addr=self._radio_addr),
            key="get_usb_mod_level",
            command=0x14,
            sub=0x10,
        )

    async def set_usb_mod_level(self, level: int) -> None:
        """Set USB modulation level (0-255)."""
        await self._send_fire_and_forget(
            set_usb_mod_level(level, to_addr=self._radio_addr)
        )

    async def get_lan_mod_level(self) -> int:
        """Read LAN modulation level (0-255)."""
        return await self._get_bcd_level(
            get_lan_mod_level(to_addr=self._radio_addr),
            key="get_lan_mod_level",
            command=0x14,
            sub=0x11,
        )

    async def set_lan_mod_level(self, level: int) -> None:
        """Set LAN modulation level (0-255)."""
        await self._send_fire_and_forget(
            set_lan_mod_level(level, to_addr=self._radio_addr)
        )

    async def get_data_off_mod_input(self) -> int:
        """Read Data Off modulation input source (0-5)."""
        return await self._get_bcd_level(
            get_data_off_mod_input(to_addr=self._radio_addr),
            key="get_data_off_mod_input",
            command=0x1A,
            sub=0x05,
            prefix=b"\x00\x91",
            bcd_bytes=1,
        )

    async def set_data_off_mod_input(self, source: int) -> None:
        """Set Data Off modulation input source (0-5)."""
        await self._send_fire_and_forget(
            set_data_off_mod_input(source, to_addr=self._radio_addr)
        )

    async def get_data1_mod_input(self) -> int:
        """Read DATA1 modulation input source (0-4)."""
        return await self._get_bcd_level(
            get_data1_mod_input(to_addr=self._radio_addr),
            key="get_data1_mod_input",
            command=0x1A,
            sub=0x05,
            prefix=b"\x00\x92",
            bcd_bytes=1,
        )

    async def set_data1_mod_input(self, source: int) -> None:
        """Set DATA1 modulation input source (0-4)."""
        await self._send_fire_and_forget(
            set_data1_mod_input(source, to_addr=self._radio_addr)
        )

    async def get_data2_mod_input(self) -> int:
        """Read DATA2 modulation input source (0-4)."""
        return await self._get_bcd_level(
            get_data2_mod_input(to_addr=self._radio_addr),
            key="get_data2_mod_input",
            command=0x1A,
            sub=0x05,
            prefix=b"\x00\x93",
            bcd_bytes=1,
        )

    async def set_data2_mod_input(self, source: int) -> None:
        """Set DATA2 modulation input source (0-4)."""
        await self._send_fire_and_forget(
            set_data2_mod_input(source, to_addr=self._radio_addr)
        )

    async def get_data3_mod_input(self) -> int:
        """Read DATA3 modulation input source (0-4)."""
        return await self._get_bcd_level(
            get_data3_mod_input(to_addr=self._radio_addr),
            key="get_data3_mod_input",
            command=0x1A,
            sub=0x05,
            prefix=b"\x00\x94",
            bcd_bytes=1,
        )

    async def set_data3_mod_input(self, source: int) -> None:
        """Set DATA3 modulation input source (0-4)."""
        await self._send_fire_and_forget(
            set_data3_mod_input(source, to_addr=self._radio_addr)
        )

    async def get_civ_transceive(self) -> bool:
        """Read CI-V transceive status."""
        return await self._get_bool_value(
            get_civ_transceive(to_addr=self._radio_addr),
            key="get_civ_transceive",
            command=0x1A,
            sub=0x05,
            prefix=b"\x01\x29",
        )

    async def set_civ_transceive(self, enabled: bool) -> None:
        """Set CI-V transceive status."""
        await self._send_fire_and_forget(
            set_civ_transceive(enabled, to_addr=self._radio_addr)
        )

    async def get_civ_output_ant(self) -> bool:
        """Read CI-V output (ANT) status."""
        return await self._get_bool_value(
            get_civ_output_ant(to_addr=self._radio_addr),
            key="get_civ_output_ant",
            command=0x1A,
            sub=0x05,
            prefix=b"\x01\x30",
        )

    async def set_civ_output_ant(self, enabled: bool) -> None:
        """Set CI-V output (ANT) status."""
        await self._send_fire_and_forget(
            set_civ_output_ant(enabled, to_addr=self._radio_addr)
        )

    async def get_system_date(self) -> tuple[int, int, int]:
        """Read system date as (year, month, day)."""
        self._check_connected()
        civ = get_system_date(to_addr=self._radio_addr)
        resp = await self._send_civ_expect(
            civ, key="get_system_date", dedupe=True, label="get_system_date"
        )
        return parse_system_date_response(resp)

    async def set_system_date(self, year: int, month: int, day: int) -> None:
        """Set system date.

        Args:
            year: 4-digit year.
            month: Month 1-12.
            day: Day 1-31.
        """
        await self._send_fire_and_forget(
            set_system_date(year, month, day, to_addr=self._radio_addr)
        )

    async def get_system_time(self) -> tuple[int, int]:
        """Read system time as (hour, minute)."""
        self._check_connected()
        civ = get_system_time(to_addr=self._radio_addr)
        resp = await self._send_civ_expect(
            civ, key="get_system_time", dedupe=True, label="get_system_time"
        )
        return parse_system_time_response(resp)

    async def set_system_time(self, hour: int, minute: int) -> None:
        """Set system time.

        Args:
            hour: Hour 0-23.
            minute: Minute 0-59.
        """
        await self._send_fire_and_forget(
            set_system_time(hour, minute, to_addr=self._radio_addr)
        )

    async def get_utc_offset(self) -> tuple[int, int, bool]:
        """Read UTC offset as (hours, minutes, is_negative)."""
        self._check_connected()
        civ = get_utc_offset(to_addr=self._radio_addr)
        resp = await self._send_civ_expect(
            civ, key="get_utc_offset", dedupe=True, label="get_utc_offset"
        )
        return parse_utc_offset_response(resp)

    async def set_utc_offset(self, hours: int, minutes: int, is_negative: bool) -> None:
        """Set UTC offset.

        Args:
            hours: Offset hours 0-14.
            minutes: Offset minutes, one of 0/15/30/45.
            is_negative: True for negative (west) offset.
        """
        await self._send_fire_and_forget(
            set_utc_offset(hours, minutes, is_negative, to_addr=self._radio_addr)
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
        frames = send_cw(text, to_addr=self._radio_addr)
        for frame in frames:
            resp = await self._send_civ_expect(frame, label="send_cw_text")
            ack = parse_ack_nak(resp)
            if ack is False:
                raise CommandError("Radio rejected CW text")

    async def stop_cw_text(self) -> None:
        """Stop CW sending."""
        self._check_connected()
        civ = stop_cw(to_addr=self._radio_addr)
        await self._send_civ_raw(civ, priority=Priority.IMMEDIATE)
        # Stop CW may not return ACK, just ignore

    async def power_control(self, on: bool) -> None:
        """Power the radio on or off.

        Args:
            on: True to power on, False to power off.
        """
        await self.set_powerstat(on)

    async def get_powerstat(self) -> bool:
        """Get the current radio power state (PowerControlCapable protocol)."""
        self._check_connected()
        civ = get_powerstat(to_addr=self._radio_addr)
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
            power_on(to_addr=self._radio_addr)
            if on
            else power_off(to_addr=self._radio_addr)
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
            build_memory_mode_set(channel, to_addr=self._radio_addr)
        )

    async def memory_write(self) -> None:
        """Write current VFO state to selected memory channel."""
        await self._send_fire_and_forget(build_memory_write(to_addr=self._radio_addr))

    async def memory_to_vfo(self, channel: int) -> None:
        """Load memory channel to VFO."""
        if not 1 <= channel <= 101:
            raise ValueError(f"Channel must be 1-101, got {channel}")
        await self._send_fire_and_forget(
            build_memory_to_vfo(channel, to_addr=self._radio_addr)
        )

    async def memory_clear(self, channel: int) -> None:
        """Clear memory channel."""
        if not 1 <= channel <= 101:
            raise ValueError(f"Channel must be 1-101, got {channel}")
        await self._send_fire_and_forget(
            build_memory_clear(channel, to_addr=self._radio_addr)
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
            build_memory_contents_set(mem, to_addr=self._radio_addr)
        )

    async def get_bsr(self, band: int, register: int) -> BandStackRegister:
        """Read band stacking register (band 0-24, register 1-3).

        Args:
            band: Band number (0-24).
            register: Register number (1-3).

        Raises:
            NotImplementedError: IC-7610 does not support reading band
                stacking registers. Command 0x1A 0x01 GET is not documented
                in the official CI-V Reference Manual.
        """
        if not 0 <= band <= 24:
            raise ValueError(f"Band must be 0-24, got {band}")
        if not 1 <= register <= 3:
            raise ValueError(f"Register must be 1-3, got {register}")
        raise NotImplementedError(
            f"IC-7610 does not support reading band stack register (band={band}, reg={register}). "
            "Command 0x1A 0x01 GET is not documented in the CI-V Reference Manual."
        )

    async def set_bsr(self, bsr: BandStackRegister) -> None:
        """Write band stacking register."""
        if not 0 <= bsr.band <= 24:
            raise ValueError(f"Band must be 0-24, got {bsr.band}")
        if not 1 <= bsr.register <= 3:
            raise ValueError(f"Register must be 1-3, got {bsr.register}")
        await self._send_fire_and_forget(set_bsr(bsr, to_addr=self._radio_addr))

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
