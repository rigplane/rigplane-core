"""RadioPoller — CI-V command and acquisition serialiser.

## ARCHITECTURE PRINCIPLE: FIRE-AND-FORGET ONLY

Background CI-V acquisition MUST be fire-and-forget.  Do not await responses
from the streaming poll path.

Why: The IC-7610 scope streams ~225 CI-V packets/sec on port 50002.  When
a request-response command waits for a specific reply, the response packet
gets lost among scope frames, causing 2-second timeouts that cascade and
freeze the entire poller.

wfview (the reference implementation) works the same way: commands go out,
responses are parsed from the incoming stream — nobody waits for a specific
reply.

How it works:
1. RadioPoller sends fire-and-forget CI-V queries (get_freq, get_mode, etc.)
2. The CI-V RX loop receives packets and applies observations to StateStore.
3. StateStore snapshots are the canonical source of truth for web consumers.
   RadioState mirrors remain compatibility surfaces only.
4. Poll freshness stays local to the poller; broadcast events notify on changes.

The one deliberate request-response exception is an explicit user command
whose positive ACK is itself required evidence.  Such a transaction remains
on the provider's existing serialized command lane and may perform bounded,
read-only post-ACK readback; it must never be used by background acquisition.
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import TYPE_CHECKING, Any, Callable, cast

from ..exceptions import CommandError
from ..exceptions import ConnectionError as RadioConnectionError
from ..core.exceptions import TimeoutError as RigplaneTimeoutError
from ..capabilities import (
    CAP_AF_LEVEL,
    CAP_AGC,
    CAP_ANTENNA,
    CAP_APF,
    CAP_ATTENUATOR,
    CAP_AUDIO,
    CAP_BREAK_IN,
    CAP_COMPRESSOR,
    CAP_CW,
    CAP_DATA_MODE,
    CAP_DIGISEL,
    CAP_DUAL_RX,
    CAP_DUAL_WATCH,
    CAP_FILTER_SHAPE,
    CAP_FILTER_WIDTH,
    CAP_IP_PLUS,
    CAP_MAIN_SUB_TRACKING,
    CAP_NB,
    CAP_NOTCH,
    CAP_NR,
    CAP_POWER_CONTROL,
    CAP_PREAMP,
    CAP_REPEATER_TONE,
    CAP_RF_GAIN,
    CAP_RX_ANTENNA,
    CAP_SCOPE,
    CAP_SQUELCH,
    CAP_SSB_TX_BW,
    CAP_SYSTEM_SETTINGS,
    CAP_TSQL,
    CAP_TUNER,
    CAP_VOX,
)
from .._queue_pressure import PRESSURE_THRESHOLD
from ..commands.commander import Priority
from ..core.command_service import (
    CommandService,
)
from ..core.acquisition_scheduler import (
    AcquisitionExecutor,
    AcquisitionRequest,
    AcquisitionScheduler,
    MeterObservationCoalescer,
    civ_acquisition_executor_for_provider,
    split_ctl_mem_sub,
)
from ..core.state_pipeline_contracts import (
    CommandSource,
    FieldPath,
    Observation,
    SourceMetadata,
)
from ..core.radio_protocol import (
    ManagedTxApi,
    RelativeVfoReadbackCapable,
)
from ..core.state_diagnostics import StateDiagnosticsRecorder
from ..core.state_store import FreshnessState, StateSnapshot, StateStore
from ..core.tx_safety import BACKEND_MAX_KEY_DOWN_SECONDS, TxOutcome
from ..core.tx_target import (
    KnownTxTarget,
    TxReceiver,
    TxSlot,
    TxTarget,
    UnknownTxTarget,
)
from .._state_queries import build_state_queries
from ..profiles import RadioProfile, resolve_radio_profile
from ..runtime.managed_tx_ingress import bind_managed_tx, refuse_key_without_owner
from ..types import AudioCodec

if TYPE_CHECKING:
    from ..radio_protocol import Radio
    from ..radio_state import RadioState

__all__ = [
    "RadioPoller",
    "CommandQueue",
    "CommandQueueEntry",
    "SetAgcTimeConstant",
    "SetDataMode",
    "SetFilterWidth",
    "SetFilterShape",
    "SetPbtInner",
    "SetPbtOuter",
    "SetIfShift",
    "SetRitFrequency",
    "SetRitStatus",
    "SetRitTxStatus",
    "SetSplit",
    "EnableScope",
    "DisableScope",
    "SwitchScopeReceiver",
    "SetScopeDuringTx",
    "SetScopeCenterType",
    "SetScopeEdge",
    "SetScopeFixedEdge",
    "SetScopeDual",
    "SetScopeMode",
    "SetScopeRbw",
    "SetScopeSpan",
    "SetScopeSpeed",
    "SetScopeRef",
    "SetScopeHold",
    "SetScopeVbw",
    "SetAntenna1",
    "SetAntenna2",
    "SetRxAntennaAnt1",
    "SetRxAntennaAnt2",
    "SetSystemDate",
    "SetSystemTime",
    "SetAcc1ModLevel",
    "SetUsbModLevel",
    "SetLanModLevel",
    "SetDualWatch",
    "SetCompressor",
    "SetApf",
    "SetTwinPeak",
    "SetDriveGain",
    "ScanSetDfSpan",
    "ScanSetResume",
    "ScanStart",
    "ScanStop",
    "SendCiv",
    "SetToneFreq",
    "SetTsqlFreq",
    "SetMainSubTracking",
    "SetSsbTxBandwidth",
    "SetManualNotchWidth",
    "SetBreakInDelay",
    "SetVoxGain",
    "SetAntiVoxGain",
    "SetVoxDelay",
    "SetNbDepth",
    "SetNbWidth",
    "SetDashRatio",
    "SetRepeaterTone",
    "SetRepeaterTsql",
    "SetRxAntenna",
    "SetRefAdjust",
    "SetCivTransceive",
    "SetCivOutputAnt",
    "SetAfMute",
    "SetTunerStatus",
    "SetTuningStep",
    "SetXfcStatus",
    "SetTxFreqMonitor",
    "SetUtcOffset",
    "QuickSplit",
    "QuickDualWatch",
    "QuickDwTrigger",
    "QuickSplitTrigger",
]

logger = logging.getLogger(__name__)

_GAP: float = 0.012
_GAP_SERIAL: float = 0.050  # serial CI-V needs more breathing room
_SEND_TIMEOUT: float = 1.0
_DEFAULT_POLL_FIELD_TTL: float = 0.2
_FAST_INTERVAL: float = 0.025  # meters — wfview queue interval for LAN (25ms)
_FAST_INTERVAL_SERIAL: float = 0.100  # serial: 10 polls/sec for responsive meters
_SLOW_INTERVAL: float = 0.25  # levels/settings — rarely change

# Fallback for the derived tx_target field's own freshness TTL when a
# profile has no [state_acquisition] block at all (MOR-1496 review R3, F1
# follow-up). Renamed from ``_TX_TARGET_MIN_MAX_AGE`` (MOR-1501, #2422
# review) — despite the "MIN" naming this is a straight substitute, not a
# ``max()``-clamped floor: when a profile has no acquisition policy at all
# there is no computed TTL to clamp against, so ``_tx_target_max_age`` swaps
# this value in outright. ``4 * self._fast_interval`` alone is not a
# defensible TX-gate horizon: on a LAN profile (``_FAST_INTERVAL`` = 25ms)
# that floors to 0.1s, which the verifier measured causing 6.6
# stale-transitions/s on an idle IC-705 (no [state_acquisition] block).
# Matches the concrete 3.0s ``freshness_ttl_seconds`` IC-7300's own
# ``[state_acquisition]`` block already uses for this same field via
# ``policy_for`` — not the unrelated generic ``AcquisitionPolicy`` dataclass
# default (15.0s, calibrated for slower-changing fields, not a TX gate).
_TX_TARGET_FALLBACK_MAX_AGE: float = 3.0

_KEY_ACCEPTED = frozenset({TxOutcome.ACCEPTED, TxOutcome.IDEMPOTENT})  # lease is ours

# MOR-1181: how long the shutdown TX-safety drain may hold teardown open.
# ``CoreRadio._shutdown_managed_tx``'s doctrine — a wedged rig must not hold
# shutdown open, since closing the socket is itself the de-key of last resort —
# at a shorter bound than its 5 s: this runs inside ``stop_web_server``, ahead of
# the disconnect that then spends that 5 s on the managed release. 2.0 s matches
# every other bound there and dwarfs a fire-and-forget CI-V unkey.
_SHUTDOWN_TX_DRAIN_TIMEOUT_S: float = 2.0

# MOR-1220: max key-down for a radio that arms NO supervisor — every shipped
# serial/USB Icom backend (``_IcomSerialRadioBase.connect`` never calls
# ``_arm_managed_tx``). Since MOR-1011/1012 deleted the frontend's 3-minute
# ``PTT_SAFETY_MS`` timers those rigs had NO key-down bound anywhere in the
# product. Restored here, where the key is issued, at the managed watchdog's own
# duration — imported, not re-spelled. The managed path keeps its own bound; a
# key this poller never issued is not its to time out.
_MAX_KEY_DOWN_SECONDS: float = BACKEND_MAX_KEY_DOWN_SECONDS

# MOR-874: how long a healthy-link in-flight acquisition request may stay
# suppressed after its FIRST send-relative deadline expiry before being
# treated as a REAL timeout. The healthy-link gate (see
# ``_civ_link_healthy``) intentionally suppresses the false-timeout ->
# adaptive-decay chain when the radio answered but the deadline raced under
# load. But the gate reads the GLOBAL last-CI-V timestamp (refreshed by ANY
# frame: meters/scope/transceive), so a link under external-CAT load reads
# healthy ~permanently. Without a bound, a request whose specific answer is
# genuinely lost (UDP drop / radio silently ignores that command) would stay
# in flight forever — never re-sent, never failed. This bound caps the
# suppression: a slightly-late answer (the common case) still credits within
# the window with NO extra CI-V traffic, but once the window elapses with the
# request still uncredited we fall back to a real timeout — drop it so the
# scheduler re-queues/re-sends and the normal failure accounting/decay applies.
# Sized to a couple of answer windows so a healthy radio's reply always lands
# first; it does not add poll pressure for late-but-arriving answers.
_ACQUISITION_HEALTHY_GRACE_SECONDS: float = 6.0

# MOR-615: per-DATA-group MOD-input source fields (IC-7610 0x1A 05 00
# 0x91-0x94). Field name == ``get_<name>`` / ``set_<name>`` radio method
# suffix == legacy RadioState attribute == StateStore slow_state leaf.
_MOD_INPUT_FIELDS: tuple[str, ...] = (
    "data_off_mod_input",
    "data1_mod_input",
    "data2_mod_input",
    "data3_mod_input",
)

# MOR-1495 review R2: the assumed scan-resume-mode default seeded at
# connect (masked, i.e. already ``& 0x0F`` — matches how ``ScanSetResume``
# stores it). No ``rigs/*.toml`` declares a scan-resume default, and CI-V
# 0x0E has no read command to observe the radio's own power-on value, so
# this can only ever be an ASSUMPTION, never a confirmed fact.
#
# A secondary source (Icom IC-7300 Full Manual, "Scan Set Mode" section, as
# indexed by manualslib.com) describes SCAN RESUME as ON/OFF and states a
# factory default of ON — but this was NOT independently cross-checked
# against Icom's own primary PDF (this environment's fetch tooling could
# not retrieve it: it exceeds the 10 MB single-fetch limit), and that
# secondary source does not pin which CI-V sub-byte (0xD1/0xD2/0xD3)
# corresponds to "ON" with the confidence this constant would need.
# Seeding 0xD0 (OFF, masked 0x00) is the CONSERVATIVE choice instead: it is
# the same "nothing is happening until commanded" assumption already made
# for ``scanning=False`` immediately below, and being wrong about it is
# low-stakes — it only affects a cosmetic default shown before the operator
# ever cycles resume mode via the surface's own RESUME control.
# TODO(MOR-1495 follow-up): confirm the true factory default against a
# bench IC-7300 after a full/master reset (or Icom's own CI-V reference,
# not just the operating manual) and correct this seed if it differs.
_SCAN_RESUME_MODE_DEFAULT_MASKED = 0x00  # assumed 0xD0 (OFF) & 0x0F


def _audio_tx_codec_and_rate(radio: Any) -> tuple[AudioCodec | None, int]:
    """Legacy TX-format resolution for radios WITHOUT the neutral
    ``AudioTransport`` surface; only reachable from the PTT fallback path
    (MOR-543). Radios exposing ``start_tx``/``stop_tx`` resolve the format
    themselves from their negotiated contract.
    """
    contract = getattr(radio, "audio_stream_contract", None)
    tx_codec = getattr(contract, "tx_codec", None)
    tx_sr = getattr(contract, "tx_sample_rate_hz", None)
    if not isinstance(tx_sr, int) or isinstance(tx_sr, bool) or tx_sr <= 0:
        tx_sr = 48000
    return tx_codec, tx_sr


def _should_restart_rx(mode: str) -> bool:
    """Whether the RX path must be re-armed after a PTT TX cycle.

    *mode* is the radio's ``audio_duplex_mode`` descriptor. Returns True
    for ALL modes for now, preserving the MOR-506 unconditional re-arm
    semantics exactly: the re-arm reinstates the single-slot RX callback
    through the AudioBus, and skipping it for ``"full"`` transports is
    deferred to a hardware-gated follow-up (verify on a real IC-7610
    first). When that flip lands, this helper is the one-line decision
    point (MOR-543).
    """
    logger.debug("poller: audio_duplex_mode=%s -> restart_rx=True", mode)
    return True


# ------------------------------------------------------------------
# Command types — canonical definitions live in rigplane._poller_types.
# Re-exported here for backward compatibility.
# ------------------------------------------------------------------

from .._poller_types import (  # noqa: E402
    Command,
    CommandQueue,
    CommandQueueEntry,
    DisableScope,
    EnableScope,
    MemoryClear,
    MemoryToVfo,
    MemoryWrite,
    PttOff,
    PttOn,
    QuickDualWatch,
    QuickDwTrigger,
    QuickSplit,
    QuickSplitTrigger,
    ScanSetDfSpan,
    ScanSetResume,
    ScanStart,
    ScanStop,
    SelectVfo,
    SendCiv,
    SetAcc1ModLevel,
    SetAfLevel,
    SetAfMute,
    SetAgc,
    SetAgcTimeConstant,
    SetAntenna1,
    SetAntenna2,
    SetAntiVoxGain,
    SetApf,
    SetAttenuator,
    SetAudioPeakFilter,
    SetAutoNotch,
    SetBand,
    SetBreakIn,
    SetBreakInDelay,
    SetBsr,
    SetCivOutputAnt,
    SetCivTransceive,
    SetCompressor,
    SetCompressorLevel,
    SetCwPitch,
    SetDashRatio,
    SetData1ModInput,
    SetData2ModInput,
    SetData3ModInput,
    SetDataMode,
    SetDataOffModInput,
    SetDialLock,
    SetDigiSel,
    SetDigiselShift,
    SetDriveGain,
    SetDualWatch,
    SetFilter,
    SetFilterShape,
    SetFilterWidth,
    SetFreq,
    SetIfShift,
    SetIpPlus,
    SetKeySpeed,
    SetLanModLevel,
    SetMainSubTracking,
    SetManualNotch,
    SetManualNotchWidth,
    SetMemoryContents,
    SetMemoryMode,
    SetMicGain,
    SetMode,
    SetMonitor,
    SetMonitorGain,
    SetNB,
    SetNBLevel,
    SetNR,
    SetNRLevel,
    SetNbDepth,
    SetNbWidth,
    SetNotchFilter,
    SetPbtInner,
    SetPbtOuter,
    SetPower,
    SetPowerstat,
    SetPreamp,
    SetRefAdjust,
    SetRepeaterTone,
    SetRepeaterTsql,
    SetRfGain,
    SetRitFrequency,
    SetRitStatus,
    SetRitTxStatus,
    SetRxAntenna,
    SetRxAntennaAnt1,
    SetRxAntennaAnt2,
    SetScopeCenterType,
    SetScopeDual,
    SetScopeDuringTx,
    SetScopeEdge,
    SetScopeFixedEdge,
    SetScopeHold,
    SetScopeMode,
    SetScopeRbw,
    SetScopeRef,
    SetScopeSpeed,
    SetScopeSpan,
    SetScopeVbw,
    SetSplit,
    SetSquelch,
    SetSsbTxBandwidth,
    SetSystemDate,
    SetSystemTime,
    SetToneFreq,
    SetTsqlFreq,
    SetTunerStatus,
    SetTuningStep,
    SetTwinPeak,
    SetTxFreqMonitor,
    SetUsbModLevel,
    SetUtcOffset,
    SetVox,
    SetVoxDelay,
    SetVoxGain,
    SetXfcStatus,
    Speak,
    SwitchScopeReceiver,
    VfoEqualize,
    VfoSwap,
)


# ------------------------------------------------------------------
# RadioPoller
# ------------------------------------------------------------------


class RadioPoller:
    """Fire-and-forget CI-V poller.

    Executes queued commands and acquisition work, applying observations and
    readbacks through StateStore/CommandService while maintaining compatibility
    mirrors where required.
    """

    def __init__(
        self,
        radio: "Radio",
        command_queue: CommandQueue,
        legacy_queue: CommandQueue | None = None,
        *,
        on_state_event: Callable[[str, dict[str, Any]], None] | None = None,
        radio_state: "RadioState | None" = None,
        diagnostics: StateDiagnosticsRecorder | None = None,
        state_store: StateStore | None = None,
        acquisition_executor: AcquisitionExecutor | None = None,
    ) -> None:
        queue = legacy_queue if legacy_queue is not None else command_queue
        self._radio = radio
        self._radio_state = radio_state
        self._state_diagnostics = diagnostics
        self._state_store = state_store or StateStore()
        raw_scheduler = getattr(radio, "_acquisition_scheduler", None)
        self._acquisition_scheduler = (
            raw_scheduler if isinstance(raw_scheduler, AcquisitionScheduler) else None
        )
        self._acquisition_executor = acquisition_executor
        self._acquisition_in_flight: dict[str, tuple[frozenset[FieldPath], float]] = {}
        # MOR-874: monotonic time of the FIRST healthy-link deadline expiry per
        # in-flight request id. Seeds the bounded grace window
        # (``_ACQUISITION_HEALTHY_GRACE_SECONDS``) after which a still-uncredited
        # request stops being suppressed and is treated as a real timeout.
        # Entries are cleared when a request leaves flight (credited or dropped)
        # so the map never leaks.
        self._acquisition_healthy_grace_started: dict[str, float] = {}
        self._queue = queue
        self._on_state_event = on_state_event
        self._poll_index: int = 0
        self._task: asyncio.Task[None] | None = None
        self._last_polled: dict[str, float] = {}
        self._caps: set[str] = self._radio_capabilities()
        self._profile: RadioProfile = self._runtime_profile()
        self._cmd_map: dict[str, tuple[int, ...]] = self._load_command_map()
        # Serial backends need slower polling to avoid flooding the CI-V link
        self._is_serial: bool = not self._profile.has_lan
        self._gap: float = _GAP_SERIAL if self._is_serial else _GAP
        self._fast_interval: float = (
            _FAST_INTERVAL_SERIAL if self._is_serial else _FAST_INTERVAL
        )
        self._FAST_CMDS = (
            self._FAST_CMDS_SERIAL if self._is_serial else self._FAST_CMDS_LAN
        )
        self._STATE_QUERIES = self._build_state_queries()
        self._relative_vfo_retention_max_age: float | None = None
        if self._profile.vfo_readback == "selected_unselected":
            retention_age, coherence_window = self._relative_vfo_retention_policy()
            self._relative_vfo_retention_max_age = retention_age
            self._state_store.configure_relative_vfo_retention(
                generation=self._provider_generation(),
                max_age=retention_age,
                coherence_window=coherence_window,
            )
        if self._acquisition_executor is None:
            raw_executor = getattr(radio, "__dict__", {}).get("_acquisition_executor")
            execute = getattr(raw_executor, "execute", None)
            if callable(execute):
                self._acquisition_executor = cast(AcquisitionExecutor, raw_executor)
        if (
            self._acquisition_executor is None
            and self._acquisition_scheduler is not None
        ):
            self._acquisition_executor = civ_acquisition_executor_for_provider(
                self._acquisition_scheduler.provider,
                self._send_one_state_query,
                supports_cmd29=self._profile.supports_cmd29,
            )
        # Set by default — cleared at _run() start, re-set after initial fetch.
        # This prevents EnableScope from hanging in tests that don't call start().
        self._initial_fetch_done = asyncio.Event()
        self._initial_fetch_done.set()
        self._scope_enable_deferred = False
        self._scope_demand_generation = queue.latest_scope_demand_generation
        self._scope_session_state: tuple[bool, bool] | None = None
        self._scope_session_active = False
        # Issue #715: track user-initiated freq/mode writes so the unselected-
        # slot poll subroutine can debounce around them, and per-receiver
        # timestamps so each receiver's unselected slot is refreshed no more
        # than once per _UNSELECTED_SLOT_INTERVAL.
        self._last_user_write_ts: float = 0.0
        self._last_unselected_poll: dict[int, float] = {}
        self._vfo_binding_generation = 0
        # MOR-615: (main, sub) data_mode pair seen at the last MOD-input fetch;
        # a change triggers a refetch of the per-DATA-group MOD-input sources.
        self._mod_input_data_modes: tuple[int, int] | None = None
        # MOR-1220: the unmanaged max-key-down backstop. Per-instance, so a new
        # connect starts unarmed; overridable for tests.
        self._max_key_down_seconds: float = _MAX_KEY_DOWN_SECONDS
        self._max_key_down_timer: asyncio.TimerHandle | None = None

    def _provider_generation(self) -> int:
        return cast(int, self._state_store.provider_generation)

    def _relative_vfo_retention_policy(self) -> tuple[float, float]:
        """Derive a finite tuple window from this provider's expected cadence."""

        health_grace = getattr(self._radio, "_civ_ready_idle_timeout", 2.0)
        if not isinstance(health_grace, (int, float)) or isinstance(health_grace, bool):
            health_grace = 2.0
        health_grace = max(0.1, float(health_grace))
        acquisition = self._profile.state_acquisition
        cadence = (
            None if acquisition is None else acquisition.default_policy.cadence_seconds
        )
        expected_rotation = (
            float(cadence)
            if cadence is not None
            else 2.0 * len(self._STATE_QUERIES) * self._fast_interval
        )
        return (2.0 * expected_rotation + health_grace, health_grace)

    def _apply_bsr_readback_observations(
        self,
        *,
        freq: int,
        mode: str,
        command_id: str | None,
        source: CommandSource,
        session_id: str | None,
        command_service: CommandService | None,
        provider_generation: int,
    ) -> None:
        observed_at = time.monotonic()
        metadata = SourceMetadata(
            source="poll_response",
            provider="web_poller",
            native_id="bsr_readback",
            command_source=source,
            session_id=session_id,
        )
        observations = (
            Observation(
                path=FieldPath.active("0", "freq_mode", "freq_hz"),
                value=freq,
                source=metadata,
                timestamp_monotonic=observed_at,
                correlation_id=f"{command_id}:freq" if command_id else None,
                provider_generation=provider_generation,
            ),
            Observation(
                path=FieldPath.active("0", "freq_mode", "mode"),
                value=mode,
                source=metadata,
                timestamp_monotonic=observed_at,
                correlation_id=f"{command_id}:mode" if command_id else None,
                provider_generation=provider_generation,
            ),
        )
        for observation in observations:
            if command_service is not None:
                command_service.apply_observation(observation)
            else:
                self._state_store.apply(observation)

    def _apply_compatibility_mirror(
        self,
        apply: Callable[["RadioState"], None],
    ) -> None:
        """Mirror confirmed state into legacy RadioState delivery surfaces.

        CommandService/StateStore observations remain the source of truth for
        lifecycle, overlays, and reconciliation. This mirror only keeps the
        existing web delivery path fed until MOR-341 finishes that migration.
        """

        state = self._radio_state
        if state is None:
            return
        apply(state)

    def _mark_queued_command_failed(
        self,
        entry: CommandQueueEntry,
        exc: BaseException,
        *,
        timed_out: bool = False,
    ) -> None:
        if entry.command_service is None or entry.command_id is None:
            return
        message = str(exc) or None
        params: dict[str, Any] = {
            "message": message,
            "timed_out": timed_out,
            "session_id": entry.session_id,
        }
        if entry.source is not None:
            params["source"] = entry.source
        entry.command_service.fail_command(
            entry.command_id,
            **params,
        )

    def _arm_max_key_down(self, source: CommandSource, session_id: str | None) -> None:
        """Bound a key this poller just issued on an unmanaged radio (MOR-1220).

        Restart-on-key: a re-key replaces the pending bound. The key's ingress
        identity rides along, so the forced unkey binds what the operator's own
        unkey would have.
        """
        self._cancel_max_key_down()
        seconds = self._max_key_down_seconds
        self._max_key_down_timer = asyncio.get_running_loop().call_later(
            seconds, self._on_max_key_down, seconds, source, session_id
        )

    def _cancel_max_key_down(self) -> None:
        """Disarm the backstop; safe to call when nothing is armed."""
        if self._max_key_down_timer is not None:
            self._max_key_down_timer.cancel()
            self._max_key_down_timer = None

    def _on_max_key_down(
        self, seconds: float, source: CommandSource, session_id: str | None
    ) -> None:
        """Force the unkey the operator did not send.

        ENQUEUED, never written here: the loop then gives it the audio teardown
        and the managed/legacy split every other unkey gets, and a shutdown
        racing this expiry finds a ``PttOff`` in the queue — exactly what
        MOR-1181's drain exists to deliver.
        """
        self._max_key_down_timer = None
        logger.error(
            "radio-poller: max key-down (%gs) exceeded on unmanaged radio; "
            "forcing unkey",
            seconds,
        )
        self._queue.put(PttOff(), source=source, session_id=session_id)
        self._emit("tx_max_key_down", {"seconds": seconds, "session_id": session_id})

    def start(self) -> None:
        if self._task is not None and not self._task.done():
            return
        self._task = asyncio.get_running_loop().create_task(
            self._run(), name="radio-poller"
        )
        logger.info("radio-poller: started")

    def stop(self) -> None:
        """Cancel the loop and drop the task; deliberately synchronous.

        Draining the queue is not this method's job, and cannot be: on a full
        server shutdown the writable session's teardown ``PttOff`` is not
        enqueued until the client tasks are cancelled, well after this call. The
        caller keeps the poller and awaits :meth:`drain_tx_safety_commands` once
        those tasks have been gathered (MOR-1181, ``stop_web_server``).
        """
        # MOR-1220: an unfired backstop must not outlive its poller. What it
        # enqueued BEFORE this survives — a ``PttOff`` the final drain still
        # delivers — but minting one after promises what nothing is left to keep.
        # On this path the teardown ``PttOff`` is the whole cover for an
        # unmanaged rig: ``CoreRadio.disconnect`` de-keys the managed path only.
        self._cancel_max_key_down()
        if self._task is not None:
            self._task.cancel()
            self._task = None
            logger.info("radio-poller: stopped")

    async def drain_tx_safety_commands(
        self, *, timeout: float = _SHUTDOWN_TX_DRAIN_TIMEOUT_S
    ) -> None:
        """Execute pending unkeys on the way out; discard everything else.

        MOR-1181. The loop exits with entries still queued, and exactly one class
        of them is still worth running: a ``PttOff``, because the alternative is
        a transmitter left keyed by a process that is gone. Stale freq/mode/level
        writes must not fire on the way out, and a pending ``PttOn`` must NEVER
        run here — keying a rig this process is abandoning is the worst outcome
        this path can produce. Non-unkeys are discarded, counted and logged.

        Shielded because this runs on an already-cancelled task, which loop
        teardown would otherwise re-cancel mid-write; the bound therefore
        abandons the wait, not the write, and names at ERROR what it could not
        confirm.
        """
        entries = self._queue.drain_entries()
        undelivered = [e for e in entries if isinstance(e.command, PttOff)]
        discarded = [
            type(e.command).__name__
            for e in entries
            if not isinstance(e.command, PttOff)
        ]
        if discarded:
            logger.info(
                "radio-poller: shutdown discarded %d pending command(s) — the "
                "TX-safety drain executes PttOff only: %s",
                len(discarded),
                ", ".join(discarded),
            )
        if not undelivered:
            return
        try:
            await asyncio.wait_for(
                asyncio.shield(self._execute_pending_unkeys(undelivered)),
                timeout=timeout,
            )
        except (TimeoutError, asyncio.TimeoutError):
            logger.error(
                "radio-poller: shutdown TX drain did not settle within %.1fs; "
                "%d unkey(s) unconfirmed, the rig may still be keyed: %s",
                timeout,
                len(undelivered),
                ", ".join(e.command_id or "PttOff" for e in undelivered),
            )

    async def _execute_pending_unkeys(self, pending: list[CommandQueueEntry]) -> None:
        """Run each queued unkey, dropping it from *pending* once it has run.

        *pending* is the caller's own list, so what is left in it when the bound
        expires is exactly what the ERROR line has to name.
        """
        while pending:
            entry = pending[0]
            try:
                await self._execute(
                    entry.command,
                    command_id=entry.command_id,
                    source=entry.source or "websocket",
                    session_id=entry.session_id,
                    command_service=entry.command_service,
                )
            except Exception as exc:
                logger.error(
                    "radio-poller: shutdown unkey failed: %s", exc, exc_info=True
                )
                self._mark_queued_command_failed(entry, exc)
                if entry.future is not None and not entry.future.done():
                    entry.future.set_exception(exc)
            else:
                if entry.future is not None and not entry.future.done():
                    entry.future.set_result(None)
            finally:
                pending.pop(0)

    @property
    def running(self) -> bool:
        return self._task is not None and not self._task.done()

    def _adaptive_gap(self) -> float:
        """Return gap adjusted for queue pressure.

        At pressure < 0.5: return base gap unchanged.
        At pressure 0.5-0.7: linear interpolation from 1x to 2x gap.
        At pressure > 0.7: return 2x gap.
        """
        try:
            raw = self._radio.queue_pressure  # type: ignore[attr-defined]
            if not isinstance(raw, (int, float)):
                return self._gap
            pressure: float = float(raw)
        except (AttributeError, TypeError):
            return self._gap
        if pressure < 0.5:
            return self._gap
        if pressure > PRESSURE_THRESHOLD:
            return self._gap * 2.0
        # Linear interpolation between 0.5 and threshold
        t: float = (pressure - 0.5) / (PRESSURE_THRESHOLD - 0.5)
        return self._gap * (1.0 + t)

    def mark_polled(self, field: str) -> None:
        """Record the last successful poll time for a logical field."""
        self._last_polled[field] = time.monotonic()

    def state_is_fresh(self, field: str, ttl: float = _DEFAULT_POLL_FIELD_TTL) -> bool:
        """Return True if *field* was polled recently enough to skip re-query."""
        last = self._last_polled.get(field)
        return last is not None and (time.monotonic() - last) < ttl

    def _radio_capabilities(self) -> set[str]:
        raw_caps = getattr(self._radio, "capabilities", None)
        return set(raw_caps) if isinstance(raw_caps, set) else set()

    def _runtime_profile(self) -> RadioProfile:
        raw_profile = getattr(self._radio, "profile", None)
        if isinstance(raw_profile, RadioProfile):
            return raw_profile
        raw_model = getattr(self._radio, "model", None)
        try:
            if isinstance(raw_model, str) and raw_model.strip():
                return resolve_radio_profile(model=raw_model)
        except KeyError:
            pass
        if "dual_rx" in self._caps:
            return resolve_radio_profile(model="IC-7610")
        return resolve_radio_profile(model="IC-7300")

    def _load_command_map(self) -> dict[str, tuple[int, ...]]:
        """Load command wire bytes from TOML rig profile."""
        try:
            from pathlib import Path

            from ..rig_loader import discover_rigs

            for rig_dir in [
                Path(__file__).resolve().parent.parent.parent.parent / "rigs",
                Path(__file__).resolve().parent.parent / "rigs",
            ]:
                if rig_dir.is_dir():
                    rigs = discover_rigs(rig_dir)
                    for _model, rig_config in rigs.items():
                        if rig_config.model == self._profile.model:
                            # Convert CommandSpec to CI-V wire bytes (filters CAT commands)
                            cmd_map = rig_config.to_command_map()
                            return {name: cmd_map.get(name) for name in cmd_map}
        except Exception:
            logger.debug("radio-poller: failed to load command map", exc_info=True)
        return {}

    async def _send_cmd(
        self,
        cmd_name: str,
        data: bytes = b"",
        *,
        receiver: int = 0,
    ) -> bool:
        """Send a command using wire bytes from TOML profile.

        Returns True if command was found and sent, False otherwise.
        """
        wire = self._cmd_map.get(cmd_name)
        if not wire:
            logger.debug("radio-poller: command %s not in profile", cmd_name)
            return False
        cmd = wire[0]
        sub = wire[1] if len(wire) > 1 else None
        extra = bytes(wire[2:]) if len(wire) > 2 else b""
        payload = extra + data
        if self._profile.supports_cmd29(cmd, sub):
            inner = bytes([receiver, cmd])
            if sub is not None:
                inner += bytes([sub])
            await self._civ(0x29, data=inner + payload)
        else:
            await self._civ(cmd, sub=sub, data=payload)
        return True

    def _supports_capability(self, capability: str) -> bool:
        return capability in self._caps

    def _ensure_receiver_supported(self, receiver: int, *, operation: str) -> None:
        if self._profile.supports_receiver(receiver):
            return
        raise CommandError(
            f"{operation} does not support receiver={receiver} for profile "
            f"{self._profile.model} (receivers={self._profile.receiver_count})"
        )

    def _build_state_queries(self) -> list[tuple[int, int | None, int | None]]:
        result: list[tuple[int, int | None, int | None]] = build_state_queries(
            self._profile,
            self._caps,
            is_serial=self._is_serial,
        )
        return result

    # Scope sub-commands that require a receiver prefix byte in READ queries.
    # Without the prefix, IC-7610 silently ignores the query.
    # 0x12 (receiver select), 0x13 (single/dual), 0x1B (during TX) do NOT need it.
    _SCOPE_RECEIVER_PREFIX_SUBS = frozenset(
        {
            0x14,  # mode (center/fixed/scroll)
            0x15,  # span
            0x16,  # edge number
            0x17,  # hold
            0x19,  # ref level
            0x1A,  # sweep speed
            # 0x1C (center type) does NOT take receiver prefix — sending 0x00
            # as prefix is misinterpreted as SET center_type=0 (Filter center).
            0x1D,  # VBW
            0x1E,  # fixed edge frequencies
            0x1F,  # RBW
        }
    )

    async def _send_one_state_query(
        self,
        cmd_byte: int,
        sub_byte: int | bytes | None,
        receiver: int | None,
        *,
        priority: Priority = Priority.BACKGROUND,
    ) -> None:
        """Send a single state query (shared by initial fetch and slow rotation).

        Defaults to ``Priority.BACKGROUND`` so both the odd-cycle state poll
        and the acquisition-scheduler executor (which is bound to this method)
        yield to user commands on the shared CI-V lane (MOR-497i).  All sends
        here are fire-and-forget (``wait_dispatch=False``) so the poll burst
        does not park the poll loop on the commander future (MOR-497ii); the
        response still arrives via the CI-V RX path.

        ``sub_byte`` is ``bytes`` only for multi-byte ctl-mem sub-addressing
        (0x1A/0x05 "quick set" reads, e.g. voxDelay, MOR-1483) — always a
        global (``receiver is None``) read today, so only the final
        non-receiver, non-scope branch below needs to split it.
        """
        if (
            receiver is None
            and cmd_byte in (0x25, 0x26)
            and sub_byte == 0x01
            and self._profile.vfo_readback == "selected_unselected"
        ):
            await self._civ(
                cmd_byte,
                data=b"\x01",
                priority=priority,
                wait_dispatch=False,
            )
        elif receiver is not None:
            assert not isinstance(sub_byte, (bytes, bytearray)), (
                "multi-byte ctl-mem sub-addressing is global-only (receiver=None)"
            )
            if cmd_byte in (0x25, 0x26):
                await self._civ(
                    cmd_byte,
                    data=bytes([receiver]),
                    priority=priority,
                    wait_dispatch=False,
                )
            else:
                inner = bytes([receiver, cmd_byte])
                if sub_byte is not None:
                    inner += bytes([sub_byte])
                await self._civ(
                    0x29, data=inner, priority=priority, wait_dispatch=False
                )
        elif cmd_byte == 0x27 and sub_byte in self._SCOPE_RECEIVER_PREFIX_SUBS:
            # Scope control queries need receiver prefix (00=MAIN, 01=SUB)
            scope_rx = 0
            if self._radio_state:
                scope_rx = self._radio_state.scope_controls.receiver
            assert isinstance(sub_byte, int)
            await self._civ(
                cmd_byte,
                sub=sub_byte,
                data=bytes([scope_rx]),
                priority=priority,
                wait_dispatch=False,
            )
        else:
            civ_sub, extra_data = split_ctl_mem_sub(sub_byte)
            await self._civ(
                cmd_byte,
                sub=civ_sub,
                data=extra_data,
                priority=priority,
                wait_dispatch=False,
            )

    # Per-getter timeout for scope-control fetches.  The IC-7610 scope stream
    # (~225 pkt/s) sometimes drops individual control responses; a long wait
    # here would stall the EnableScope hot path and the poller's command-queue
    # drain.  200 ms is well below the user-visible threshold and an order of
    # magnitude shorter than the 2.0 s default GET timeout, so a missed reply
    # is logged at debug and the next getter runs immediately.  See #1181.
    _SCOPE_GETTER_TIMEOUT: float = 0.2

    async def _fetch_scope_controls(self) -> None:
        """Fetch scope control state (span, mode, speed, hold, etc.).

        Called after scope is enabled — IC-7610 ignores scope control
        queries when scope data output is off.

        Note: commands 0x14, 0x15, 0x16, 0x17, 0x19, 0x1A, 0x1D, 0x1F
        require a receiver prefix byte (00=MAIN, 01=SUB) in the READ
        query — without it the IC-7610 silently ignores the query.
        The public ``get_scope_*`` methods on ``ScopeRuntimeMixin`` add
        the prefix from ``radio_state.scope_controls.receiver`` for
        each affected sub-command.

        Each getter is bounded by ``_SCOPE_GETTER_TIMEOUT``: a dropped
        scope-control response (common on busy scope streams) only costs
        that much before the loop continues, instead of blocking the hot
        path for the full CI-V GET timeout.  Cancellation propagates into
        ``_send_civ_expect`` whose ``finally`` block unregisters the
        request-tracker entry, so repeated timeouts do not accumulate.
        """
        from ..radio_protocol import ScopeCapable

        radio = self._radio
        if not isinstance(radio, ScopeCapable):
            return

        scope_rx = 0
        if self._radio_state:
            scope_rx = self._radio_state.scope_controls.receiver

        # Iterate through all scope-control getters in the same order as
        # the previous raw 0x27 sub-command sequence so cadence/queue
        # behavior is preserved. Each call sleeps `_adaptive_gap()` to
        # keep the existing throttle. `get_scope_fixed_edge` (0x1E) was
        # never part of that legacy sequence — MOR-1530 closed the gap —
        # so it is placed immediately ahead of rbw (0x1F) to keep the
        # trailing entries in ascending sub-command order. Calling it with
        # no arguments here (rather than at EnableScope time) is
        # intentional: it defaults to the CURRENT known <range><edge> slot
        # (see ``ScopeRuntimeMixin.get_scope_fixed_edge``), so the periodic
        # fetch always re-reads the freshest relevant slot instead of a
        # fixed one.
        scope_getters: tuple[tuple[str, Any], ...] = (
            ("get_scope_receiver", radio.get_scope_receiver),
            ("get_scope_dual", radio.get_scope_dual),
            ("get_scope_during_tx", radio.get_scope_during_tx),
            ("get_scope_center_type", radio.get_scope_center_type),
            ("get_scope_mode", radio.get_scope_mode),
            ("get_scope_span", radio.get_scope_span),
            ("get_scope_edge", radio.get_scope_edge),
            ("get_scope_hold", radio.get_scope_hold),
            ("get_scope_ref", radio.get_scope_ref),
            ("get_scope_speed", radio.get_scope_speed),
            ("get_scope_vbw", radio.get_scope_vbw),
            ("get_scope_fixed_edge", radio.get_scope_fixed_edge),
            ("get_scope_rbw", radio.get_scope_rbw),
        )
        for label, getter in scope_getters:
            ok = await self._scope_getter_attempt(label, getter)
            if not ok and label == "get_scope_rbw":
                # rbw's fieldStatus intermittently reports "missing" on the
                # live stand (MOR-1524) — one bounded retry, still within
                # this getter's own timeout budget, recovers most drops
                # without extending the overall fetch cadence.
                await self._scope_getter_attempt(label, getter)
            await asyncio.sleep(self._adaptive_gap())
        logger.info("radio-poller: scope controls fetched (receiver=%d)", scope_rx)

    async def _scope_getter_attempt(self, label: str, getter: Any) -> bool:
        """Run one bounded scope-control GET; return ``True`` on success.

        Shared by ``_fetch_scope_controls`` so a dropped response (common on
        busy scope streams) only costs ``_SCOPE_GETTER_TIMEOUT`` before the
        caller decides whether to retry or move on.
        """
        try:
            await asyncio.wait_for(getter(), timeout=self._SCOPE_GETTER_TIMEOUT)
            return True
        except asyncio.TimeoutError:
            logger.debug(
                "radio-poller: %s timed out after %.0f ms (response dropped)",
                label,
                self._SCOPE_GETTER_TIMEOUT * 1000,
            )
        except Exception:
            logger.debug("radio-poller: %s failed", label, exc_info=True)
        return False

    async def _reconfirm_scope_field(self, label: str, getter: Any) -> None:
        """Force a fresh confirmed observation for one scope-control leaf.

        Scope-control fields are fetched once, at ``EnableScope`` time
        (``_fetch_scope_controls`` above) and never touched again by the main
        poll loop — by design, to avoid interfering with the high-rate scope
        waveform stream. A ``Set*`` write only mutates the optimistic
        ``RadioState.scope_controls`` mirror below; without a follow-up GET,
        the StateStore's confirmed observation for that leaf is never
        refreshed, so the public snapshot keeps re-applying the STALE
        pre-write observation over the fresh optimistic value on every poll —
        the ``scopeControls.<leaf>`` readout desync MOR-1446 reported (span
        stuck at its pre-change reading, ref stuck at 0). Bounded by
        ``_SCOPE_GETTER_TIMEOUT`` for the same reason as
        ``_fetch_scope_controls``: a dropped response on a busy scope stream
        must not stall the command queue.
        """
        try:
            await asyncio.wait_for(getter(), timeout=self._SCOPE_GETTER_TIMEOUT)
        except asyncio.TimeoutError:
            logger.debug(
                "radio-poller: %s reconfirm timed out after %.0f ms (response dropped)",
                label,
                self._SCOPE_GETTER_TIMEOUT * 1000,
            )
        except Exception:
            logger.debug("radio-poller: %s reconfirm failed", label, exc_info=True)

    def _apply_global_control_observation(
        self,
        name: str,
        value: Any,
        *,
        family: str = "operator_controls",
        command_id: str | None = None,
        source: CommandSource | None = None,
        session_id: str | None = None,
        command_service: CommandService | None = None,
        provider_generation: int,
    ) -> None:
        """Apply a confirmed global readback value to the StateStore.

        Forward-consistent route (mirrors ``_apply_bsr_readback_observations``):
        the web public-state projection reads ``global.<family>.<name>``
        from the StateStore, so a readback observation is the source of truth —
        not the legacy ``RadioState`` mirror. Used for NB depth/width whose
        4-byte menu GET (0x1A 05 02 90/91) cannot ride the poll-query envelope
        (MOR-491-B) and for the MOD-input sources (``slow_state``, MOR-615).
        """
        observation = Observation(
            path=FieldPath.global_(family, name),
            value=value,
            source=SourceMetadata(
                source="poll_response",
                provider="web_poller",
                native_id=f"{name}_readback",
                command_source=source,
                session_id=session_id,
            ),
            timestamp_monotonic=time.monotonic(),
            correlation_id=f"{command_id}:{name}" if command_id else None,
            provider_generation=provider_generation,
        )
        if command_service is not None:
            command_service.apply_observation(observation)
        else:
            self._state_store.apply(observation)

    def _apply_global_command_echo_observation(
        self,
        name: str,
        value: Any,
        *,
        family: str = "slow_state",
        command_id: str | None = None,
        source: CommandSource | None = None,
        session_id: str | None = None,
        command_service: CommandService | None = None,
        provider_generation: int,
    ) -> None:
        """Apply a command-sourced (unconfirmable) value to the StateStore.

        Sibling of :meth:`_apply_global_control_observation` for fields the
        radio has NO read command for at all (e.g. IC-7300 scan, CI-V 0x0E —
        CAT-audit confirmed SET-only, MOR-1495), so no follow-up GET can ever
        turn this into a genuine readback the way NB depth/width or the
        MOD-input sources do. Labelled ``command_response`` — never
        ``poll_response`` — so the StateStore's own provenance stays honest
        about the fact that this value was never confirmed by the radio, even
        though the owner-ruled web presentation shows it plainly with no
        "commanded, not confirmed" marker (MOR-1495 ruling). A front-panel
        scan stop is invisible to the web until the operator presses STOP in
        the web — accepted limitation, not fixable without a read command.
        """
        observation = Observation(
            path=FieldPath.global_(family, name),
            value=value,
            source=SourceMetadata(
                source="command_response",
                provider="web_poller",
                native_id=f"{name}_command_echo",
                command_source=source,
                session_id=session_id,
            ),
            timestamp_monotonic=time.monotonic(),
            correlation_id=f"{command_id}:{name}" if command_id else None,
            provider_generation=provider_generation,
        )
        if command_service is not None:
            command_service.apply_observation(observation)
        else:
            self._state_store.apply(observation)

    def _seed_scan_facts_at_connect(self) -> None:
        """Seed ``scanning``/``scan_resume_mode`` once at connect (and again
        on soft-reconnect) so the scan START control can ever leave the
        "missing" ``fieldStatus`` gate that keeps it disabled (MOR-1495
        review R2 — verifier-caught bootstrap deadlock).

        The round-1 fix (:meth:`_apply_global_command_echo_observation`,
        called from the ``ScanStart``/``ScanStop``/``ScanSetResume`` command
        handlers) is the ONLY writer of these fields — but the ONLY trigger
        for those commands is the UI, and the UI's own ``usable()`` gate
        (``RitXitScanSurface.svelte``) requires the fields to already be
        known before it will enable the button that would send the very
        first command. Nothing breaks that cycle without an explicit seed.

        PURE LOCAL SEED — never sends anything to the radio. Unlike
        :meth:`establish_vfo_identity` (its sibling call site in
        :meth:`_run`'s startup section and in the server's reconnect path),
        which commands VFO A over the wire and therefore pauses while an
        external CAT session owns it, this seed touches only the local
        StateStore and needs no such guard.

        "Not scanning until we command it" is an ASSUMED-UNTIL-COMMANDED
        fact — the same accepted-dishonesty class as this PR's documented
        front-panel-scan-stop-is-invisible limitation, not a claim about
        anything actually observed on the radio. A soft-reconnect reseeds
        it unconditionally rather than trusting a pre-reconnect commanded
        value, mirroring ``reset_vfo_session()``'s unconditional discard of
        ``active_slot`` on every reconnect (MOR-1443) — the true state is
        unknown again after any link drop.

        ``scan_type`` is deliberately NOT seeded here: an assumed type
        would let an unconfirmed guess masquerade as an observed radio
        fact, which is exactly what the surface's other honesty gates
        exist to prevent. The START flow instead owns the type as local UI
        state (``RitXitScanSurface.svelte``'s own ``selectedType``,
        mirroring v2 ``ScanPanel``'s shape/default), sent explicitly with
        every ``scan_start`` command — so START no longer depends on an
        observed type at all.
        """
        provider_generation = self._provider_generation()
        self._apply_global_command_echo_observation(
            "scanning",
            False,
            provider_generation=provider_generation,
        )
        self._apply_global_command_echo_observation(
            "scan_resume_mode",
            _SCAN_RESUME_MODE_DEFAULT_MASKED,
            provider_generation=provider_generation,
        )

    async def _fetch_nb_controls(self) -> None:
        """One-shot readback of NB depth/width into the StateStore (MOR-491-B).

        NB depth (0x1A 05 02 90) and width (0x1A 05 02 91) are global menu
        items whose 4-byte READ query cannot be expressed in the poll-query
        envelope, so they are not tracked by the continuous poller. Read them
        once at connect via the existing direct getters and apply the real
        values as StateStore observations so the web sliders seed correctly.

        Each getter is resilient: a read error or timeout is logged at debug
        and never kills the poller (mirrors ``_fetch_scope_controls``).
        """
        if CAP_NB not in self._caps:
            return
        provider_generation = self._provider_generation()
        radio: Any = self._radio
        getters: tuple[tuple[str, str, Any], ...] = (
            ("nb_depth", "get_nb_depth", getattr(radio, "get_nb_depth", None)),
            ("nb_width", "get_nb_width", getattr(radio, "get_nb_width", None)),
        )
        for name, label, getter in getters:
            if getter is None:
                continue
            try:
                value = await getter()
            except Exception:
                logger.debug("radio-poller: %s failed", label, exc_info=True)
                continue
            self._apply_global_control_observation(
                name,
                value,
                provider_generation=provider_generation,
            )
        logger.info("radio-poller: NB controls fetched")

    async def _read_mod_input(
        self,
        name: str,
        *,
        command_id: str | None = None,
        source: CommandSource | None = None,
        session_id: str | None = None,
        command_service: CommandService | None = None,
        provider_generation: int | None = None,
    ) -> None:
        """Read one DATA-group MOD-input source into the StateStore + mirror.

        Resilient: a read error or timeout is logged at debug and never kills
        the caller (mirrors ``_fetch_nb_controls``).
        """
        generation = (
            self._provider_generation()
            if provider_generation is None
            else provider_generation
        )
        getter = getattr(self._radio, f"get_{name}", None)
        if getter is None:
            return
        try:
            value = int(await getter())
        except Exception:
            logger.debug("radio-poller: get_%s failed", name, exc_info=True)
            return
        if self._radio_state is not None:
            setattr(self._radio_state, name, value)
        self._apply_global_control_observation(
            name,
            value,
            family="slow_state",
            command_id=command_id,
            source=source,
            session_id=session_id,
            command_service=command_service,
            provider_generation=generation,
        )

    async def _fetch_mod_inputs(self) -> None:
        """Readback of the per-DATA-group MOD-input sources (MOR-615).

        DATA OFF/1/2/3 MOD (0x1A 05 00 0x91-0x94) are global menu items the
        continuous poller never tracks. Read them via the direct getters and
        apply the confirmed values as StateStore observations (+ legacy
        RadioState mirror) so the web state exposes the radio's current MOD
        routing. Gated on CAP_DATA_MODE so non-IC-7610 radios are unaffected.
        """
        if CAP_DATA_MODE not in self._caps:
            return
        if self._radio_state is not None:
            self._mod_input_data_modes = (
                self._radio_state.main.data_mode,
                self._radio_state.sub.data_mode,
            )
        for name in _MOD_INPUT_FIELDS:
            await self._read_mod_input(name)

    async def _refresh_mod_inputs_on_data_mode_change(self) -> None:
        """Refetch the MOD-input sources when any receiver's data_mode changed.

        The legacy RadioState mirror's ``data_mode`` is kept current by the
        regular 0x26 poll (``_handle_26``), so this catches both web-initiated
        and front-panel data-mode changes within one poll cycle (MOR-615).
        """
        if CAP_DATA_MODE not in self._caps or self._radio_state is None:
            return
        current = (
            self._radio_state.main.data_mode,
            self._radio_state.sub.data_mode,
        )
        if current == self._mod_input_data_modes:
            return
        await self._fetch_mod_inputs()

    async def _run(self) -> None:
        _backoff = 0.0
        _MAX_BACKOFF = 5.0  # max pause when radio is disconnected

        # Initial state is now fetched by CoreRadio._fetch_initial_state()
        # during connect(). Just signal readiness immediately.
        self._scope_enable_deferred = False
        self._initial_fetch_done.set()

        # NB depth/width are global menu items that cannot ride the poll-query
        # envelope, so seed them once at connect via direct getters (MOR-491-B).
        try:
            await self._fetch_nb_controls()
        except Exception:
            logger.debug(
                "radio-poller: NB controls initial fetch failed", exc_info=True
            )

        # Per-DATA-group MOD-input sources are global menu items the continuous
        # poller never tracks; seed them once at connect (MOR-615).
        try:
            await self._fetch_mod_inputs()
        except Exception:
            logger.debug("radio-poller: MOD-input initial fetch failed", exc_info=True)

        # MOR-1443: some CI-V radios can never passively report which VFO
        # slot (A/B) is active; command VFO A once so identity is known.
        try:
            await self.establish_vfo_identity()
        except Exception:
            # A ruled behaviour (owner decision, MOR-1443) silently not
            # happening is worth surfacing above debug (review R2).
            logger.warning(
                "radio-poller: auto VFO identity establish failed", exc_info=True
            )

        # MOR-1495 review R2: scan (CI-V 0x0E) is SET-ONLY, so nothing else
        # ever seeds scanning/scan_resume_mode — without this, the web UI's
        # scan controls stay disabled forever (bootstrap deadlock: the only
        # writer of these fields is a scan command, and the only trigger for
        # a scan command is a UI control gated on these fields already being
        # known). Pure local seed — never touches the radio.
        try:
            self._seed_scan_facts_at_connect()
        except Exception:
            logger.warning("radio-poller: scan facts seed failed", exc_info=True)

        try:
            while True:
                # 0. External CAT session (e.g. Hamlib A1 bridge) owns the wire —
                # pause RigPlane's own polling/commands to avoid CI-V cross-talk
                # in the owner's byte stream (MOR-166 slice 2). Queued commands
                # stay buffered and drain once the session ends. ``is True`` (not
                # just truthy) so duck-typed / mock radios never quiesce by
                # accident — only a real bool flag does.
                if getattr(self._radio, "external_cat_session_active", False) is True:
                    await asyncio.sleep(self._adaptive_gap())
                    continue

                # 1. Drain command queue (fire-and-forget writes)
                if self._queue.has_commands:
                    for entry in self._queue.drain_entries():
                        cmd = entry.command
                        if entry.future is not None and entry.future.cancelled():
                            logger.debug(
                                "radio-poller: skipping cancelled queued cmd: %s",
                                type(cmd).__name__,
                            )
                            continue
                        try:
                            await self._execute(
                                cmd,
                                command_id=entry.command_id,
                                source=entry.source or "websocket",
                                session_id=entry.session_id,
                                command_service=entry.command_service,
                            )
                            if entry.future is not None and not entry.future.done():
                                entry.future.set_result(None)
                            _backoff = 0.0
                        except (TimeoutError, RigplaneTimeoutError) as exc:
                            self._mark_queued_command_failed(
                                entry,
                                exc,
                                timed_out=True,
                            )
                            if entry.future is not None and not entry.future.done():
                                entry.future.set_exception(exc)
                            logger.warning(
                                "radio-poller: cmd timeout: %s",
                                type(cmd).__name__,
                                exc_info=True,
                            )
                        except (ConnectionError, RadioConnectionError) as exc:
                            self._mark_queued_command_failed(entry, exc)
                            if entry.future is not None and not entry.future.done():
                                entry.future.set_exception(exc)
                            _backoff = min(_backoff + 0.5, _MAX_BACKOFF)
                        except Exception as exc:
                            self._mark_queued_command_failed(entry, exc)
                            if entry.future is not None and not entry.future.done():
                                entry.future.set_exception(exc)
                            logger.warning(
                                "radio-poller: cmd error: %s",
                                type(cmd).__name__,
                                exc_info=True,
                            )
                        await asyncio.sleep(self._adaptive_gap())

                # If disconnected, back off to avoid log spam
                if _backoff > 0:
                    await asyncio.sleep(_backoff)
                    # Still try one query to detect reconnection
                    try:
                        await self._send_query()
                        _backoff = 0.0
                        logger.info("radio-poller: connection restored")
                    except (ConnectionError, RadioConnectionError):
                        _backoff = min(_backoff + 0.5, _MAX_BACKOFF)
                        continue
                    except Exception:
                        continue

                # 2. Send fast meter query
                try:
                    await self._send_query()
                except (ConnectionError, RadioConnectionError):
                    _backoff = min(_backoff + 0.5, _MAX_BACKOFF)
                    logger.info(
                        "radio-poller: radio disconnected, backing off %.1fs", _backoff
                    )
                    continue
                except Exception:
                    # MOR-1440: a dead serial link surfaces here as a bare
                    # TimeoutError (CI-V transport recovery-wait gate), not
                    # ConnectionError — back off same as the branch above
                    # instead of hammering a doomed wire every poll cycle.
                    if not bool(getattr(self._radio, "connected", True)):
                        _backoff = min(_backoff + 0.5, _MAX_BACKOFF)
                        logger.info(
                            "radio-poller: radio disconnected, backing off %.1fs",
                            _backoff,
                        )
                        continue
                    logger.debug("radio-poller: query error", exc_info=True)

                # 2b. data_mode changed (web command or front panel) => refetch
                # the per-DATA-group MOD-input sources (MOR-615).
                try:
                    await self._refresh_mod_inputs_on_data_mode_change()
                except Exception:
                    logger.debug("radio-poller: MOD-input refresh error", exc_info=True)

                # 3. Issue #715: opportunistically refresh the unselected
                # VFO slot on each receiver.  Fully gated (PTT, queue
                # pressure, debounce, per-rx interval) so it cannot
                # regress fast-poll cadence.
                if self._acquisition_scheduler is None:
                    for _rx in range(self._profile.receiver_count):
                        try:
                            await self._poll_unselected_slot(_rx)
                        except (ConnectionError, RadioConnectionError):
                            _backoff = min(_backoff + 0.5, _MAX_BACKOFF)
                            break
                        except Exception:
                            logger.debug(
                                "radio-poller: unselected-slot poll error",
                                exc_info=True,
                            )

                # 3b. Re-derive tx_target from currently observed active-VFO
                # identity/split/frequency facts (MOR-1496). State-store reads
                # only, no wire I/O. NOT reached on every iteration — the
                # external-CAT/backoff/dead-link branches above all `continue`
                # past this — so the field carries its own TTL (F1,
                # _tx_target_max_age) rather than relying on cadence to
                # notice a stale input; _publish_tx_target itself skips the
                # write when nothing changed and the stored entry is still
                # fresh (F2).
                try:
                    self._publish_tx_target()
                except Exception:
                    logger.debug(
                        "radio-poller: tx_target derivation error", exc_info=True
                    )

                # 4. Wait for next cycle
                await self._queue.wait(timeout=self._fast_interval)
        except asyncio.CancelledError:
            # MOR-1181: an unkey abandoned in the queue here is a keyed
            # transmitter. Covers every cancellation of this task; the shutdown
            # ORDERING — the teardown unkey is not enqueued until long after
            # this runs — is the caller's half, in ``stop_web_server``.
            # MOR-1220: same disarm as ``stop()`` — this covers cancellations
            # that never went through it. Ahead of the drain, which still
            # delivers an expiry already in the queue.
            self._cancel_max_key_down()
            await self.drain_tx_safety_commands()
        except Exception:
            logger.exception(
                "radio-poller: FATAL — task crashed, commands will stop working"
            )

    async def _civ(
        self,
        cmd: int,
        *,
        sub: int | None = None,
        data: bytes = b"",
        wait_response: bool = False,
        priority: Priority = Priority.NORMAL,
        wait_dispatch: bool = True,
    ) -> Any:
        """Send a raw CI-V command if the backend provides a CI-V transport.

        For non-Icom backends this is a no-op — scope/meter polling simply
        won't happen, which is acceptable.

        ``priority`` defaults to NORMAL so user-command call sites (e.g. the
        in-``_execute`` VFO switch) are never de-prioritized; background poll
        call sites pass ``Priority.BACKGROUND`` explicitly so polls yield to
        user commands on the shared CI-V lane (MOR-497i).

        ``wait_dispatch`` defaults to True so user-command call sites keep the
        blocking/awaited contract; background poll call sites pass False so the
        poll burst is fire-and-forget and does not park the poll loop on the
        commander future (MOR-497ii).

        Returns:
            CivFrame response if wait_response=True and backend supports it,
            else None.
        """
        from ..radio_protocol import CivCommandCapable

        if isinstance(self._radio, CivCommandCapable):
            return await self._radio.send_civ(
                cmd,
                sub=sub,
                data=data,
                wait_response=wait_response,
                priority=priority,
                wait_dispatch=wait_dispatch,
            )
        return None

    def _current_active(self) -> str:
        rs = getattr(self._radio, "_radio_state", None)
        _active = getattr(rs, "active", None) if rs is not None else None
        return _active if isinstance(_active, str) else "MAIN"

    def _scope_demand_is_stale(self, generation: int) -> bool:
        latest = max(
            self._scope_demand_generation,
            self._queue.latest_scope_demand_generation,
        )
        if generation < latest:
            logger.debug(
                "radio-poller: dropping stale scope demand generation %d (current=%d)",
                generation,
                latest,
            )
            return True
        self._scope_demand_generation = generation
        return False

    async def _enable_scope_session(self, *, policy: str) -> None:
        """Capture scope state once, then enable through the existing wire lane."""
        radio = self._radio
        get_state = getattr(radio, "get_scope_session_state", None)
        restore_state = getattr(radio, "restore_scope_session_state", None)
        if get_state is None or restore_state is None:
            raise CommandError(
                "scope backend cannot preserve pre-session panel/output state"
            )
        if self._scope_session_state is None:
            self._scope_session_state = await get_state()
        try:
            await radio.enable_scope(policy=policy)  # type: ignore[attr-defined]
        except BaseException:
            # Once enable starts, its wire effect is uncertain: FAST can fail
            # after the panel ON write, and VERIFY can time out after both ON
            # writes were applied.  Roll back before reporting failure.  The
            # captured tuple remains owned if rollback itself fails, allowing a
            # later DisableScope teardown to retry deterministically.
            self._scope_session_active = True
            try:
                await self.restore_scope_session()
            except BaseException:
                logger.exception(
                    "radio-poller: scope enable failed and rollback is pending"
                )
            raise
        self._scope_session_active = True

    async def restore_scope_session(self) -> None:
        """Restore exactly what the first successful scope viewer inherited."""
        if not self._scope_session_active or self._scope_session_state is None:
            return
        if getattr(self._radio, "external_cat_session_active", False) is True:
            raise CommandError(
                "scope restore deferred while an external CAT session owns the wire"
            )
        restore_state = getattr(self._radio, "restore_scope_session_state", None)
        if restore_state is None:
            raise CommandError(
                "scope backend cannot restore pre-session panel/output state"
            )
        await restore_state(self._scope_session_state)
        self._scope_session_active = False
        self._scope_session_state = None

    async def restore_scope_after_shutdown(self) -> None:
        """Restore scope through this stopped poller's sole command executor.

        ``stop_web_server`` calls this only after ``stop()`` and the final TX
        safety drain.  Re-entering ``_execute`` therefore uses the same sole
        poller executor as the live ``CommandQueue`` consumer, with no second
        task or radio-control lane.  The backend write continues through its
        existing ``_send_civ_raw`` / ``IcomCommander`` serialization.
        """
        if CAP_SCOPE not in self._caps:
            raise CommandError("scope restore unavailable on this radio")
        generation = (
            max(
                self._scope_demand_generation,
                self._queue.latest_scope_demand_generation,
            )
            + 1
        )
        await self._execute(DisableScope(generation=generation))
        if self._scope_session_active:
            raise CommandError("scope restore did not settle")

    def _managed_tx(
        self, source: CommandSource, session_id: str | None
    ) -> ManagedTxApi | None:
        """Bind this control session's managed TX facade; ``None`` if unmanaged.

        The rule it applies — only a websocket session carries an owner identity
        stable enough to release the lease it takes — now lives in
        ``runtime.managed_tx_ingress`` with the rest of the gate, because
        rigctld (MOR-1014) and the CLI/SDK (routed under MOR-1170/MOR-1171)
        apply the same one and the two-step supervisor read behind it belongs
        in exactly one place (MOR-1198).
        """
        return bind_managed_tx(self._radio, source, session_id)

    def _refuse_key_from_gone_session(
        self, source: CommandSource, session_id: str | None
    ) -> None:
        """Reject a key enqueued by a control session that is already gone.

        The entry outlives its author: a session can enqueue PTT ON and drop
        before this drain, and the supervisor grants a lease to any owner, alive
        or dead. Gated on the same pair as ``_managed_tx`` — only a websocket
        session publishes liveness, and ``session_id is None`` (HTTP PTT)
        carries none to check, so it passes through. ON only: an unkey refused
        for being late would strand the rig keyed.

        That last sentence is the whole reason the teardown unkey is safe, and
        it is the only reason: since MOR-1185 it arrives carrying its session's
        id, and by drain time that session is already unregistered. Hoisting
        this call anywhere the ``PttOff`` arm can reach would strand a keyed
        rig on every disconnect.

        This narrows the window; it does not close it. A session can still die
        between this check and the write it guards.
        """
        if source != "websocket" or not session_id:
            return
        if self._queue.session_is_live(session_id):
            return
        raise CommandError(f"control session {session_id} is gone: PTT ON refused")

    async def _stop_tx_audio_leg(self) -> None:
        """Stop the TX audio stream and re-arm RX; never raises."""
        radio = self._radio
        if CAP_AUDIO not in self._caps:
            return
        try:
            stop_tx = getattr(radio, "stop_tx", None)
            if stop_tx is not None:
                # Neutral AudioTransport surface (MOR-543).
                await stop_tx()
            else:
                # Legacy per-codec fallback.
                tx_codec, _tx_sr = _audio_tx_codec_and_rate(radio)
                if tx_codec == AudioCodec.PCM_1CH_16BIT:
                    await radio.stop_audio_tx_pcm()
                else:
                    await radio.stop_audio_tx_opus()
            logger.info("poller: TX audio stream stopped")

            # Re-arm RX through the AudioBus so the real subscriber callback is
            # reinstated rather than a throwaway no-op clobbering the
            # single-slot RX callback (MOR-506). The LAN stream itself IS
            # full-duplex; the re-arm currently fires for every
            # audio_duplex_mode (including "full") until skipping it is
            # verified on real IC-7610 hardware — that flip is a hardware-gated
            # follow-up to MOR-543, one line inside _should_restart_rx.
            duplex_mode = getattr(radio, "audio_duplex_mode", "half")
            if _should_restart_rx(duplex_mode):
                await radio.audio_bus.restart_rx()
                logger.info("poller: RX audio stream restarted")
        except Exception as e:
            logger.debug("poller: audio stream transition failed: %s", e)

    async def _execute(
        self,
        cmd: Command,
        *,
        command_id: str | None = None,
        source: CommandSource = "websocket",
        session_id: str | None = None,
        command_service: CommandService | None = None,
    ) -> None:
        radio = self._radio
        provider_generation = self._provider_generation()
        _r: Any = radio  # cast for capability methods not on base Radio protocol
        # Alias the command source under a distinct name: ``source`` is reused as
        # a match capture variable by several ``case Set*ModInput(source=...)``
        # arms below, so referencing the parameter directly inside the match
        # would make mypy collapse the two into one (conflicting) type.
        command_source = source
        from ..radio_protocol import (
            MemoryCapable,
        )

        match cmd:
            case SendCiv(command=command, sub=sub, data=data):
                from ..radio_protocol import CivCommandCapable

                if not isinstance(radio, CivCommandCapable):
                    raise CommandError("send_civ is not supported by this backend")
                await radio.send_civ(
                    command,
                    sub=sub,
                    data=data,
                    wait_response=False,
                )
            case SetFreq(freq=freq, receiver=rx):
                self._last_user_write_ts = time.monotonic()
                self._ensure_receiver_supported(rx, operation="set_freq")
                current = self._current_active()
                if rx != 0 and self._profile.supports_cmd29(0x05):
                    await radio.set_freq(freq, receiver=rx)
                elif rx != 0:
                    if (
                        self._profile.vfo_sub_code is None
                        or self._profile.vfo_main_code is None
                    ):
                        raise CommandError(
                            f"set_freq receiver={rx} is unsupported by profile {self._profile.model}: "
                            "no cmd29 route and no VFO switch codes"
                        )
                    if current != "SUB":
                        await self._civ(0x07, data=bytes([self._profile.vfo_sub_code]))
                        await asyncio.sleep(self._gap)
                    await radio.set_freq(freq)
                    if current != "SUB":
                        await asyncio.sleep(self._gap)
                        await self._civ(0x07, data=bytes([self._profile.vfo_main_code]))
                else:
                    if current != "MAIN" and self._profile.vfo_main_code is not None:
                        await self._civ(0x07, data=bytes([self._profile.vfo_main_code]))
                        await asyncio.sleep(self._gap)
                    await radio.set_freq(freq)
                    if current != "MAIN" and self._profile.vfo_sub_code is not None:
                        await asyncio.sleep(self._gap)
                        await self._civ(0x07, data=bytes([self._profile.vfo_sub_code]))
                # Compatibility mirror until web state delivery reads StateStore.
                if self._radio_state:
                    target = (
                        self._radio_state.sub if rx != 0 else self._radio_state.main
                    )
                    if target:
                        target.freq = freq
                    self.mark_polled("freq")
                if self._on_state_event:
                    self._on_state_event("freq_changed", {"freq": freq, "receiver": rx})
            case SetMode(mode=mode, filter_width=fw, receiver=rx):
                self._last_user_write_ts = time.monotonic()
                self._ensure_receiver_supported(rx, operation="set_mode")
                current = self._current_active()
                if rx != 0 and self._profile.supports_cmd29(0x06):
                    await radio.set_mode(mode, fw, receiver=rx)
                elif rx != 0:
                    if (
                        self._profile.vfo_sub_code is None
                        or self._profile.vfo_main_code is None
                    ):
                        raise CommandError(
                            f"set_mode receiver={rx} is unsupported by profile {self._profile.model}: "
                            "no cmd29 route and no VFO switch codes"
                        )
                    if current != "SUB":
                        await self._civ(0x07, data=bytes([self._profile.vfo_sub_code]))
                        await asyncio.sleep(self._gap)
                    await radio.set_mode(mode, fw)
                    if current != "SUB":
                        await asyncio.sleep(self._gap)
                        await self._civ(0x07, data=bytes([self._profile.vfo_main_code]))
                else:
                    if current != "MAIN" and self._profile.vfo_main_code is not None:
                        await self._civ(0x07, data=bytes([self._profile.vfo_main_code]))
                        await asyncio.sleep(self._gap)
                    await radio.set_mode(mode, fw)
                    if current != "MAIN" and self._profile.vfo_sub_code is not None:
                        await asyncio.sleep(self._gap)
                        await self._civ(0x07, data=bytes([self._profile.vfo_sub_code]))
                # Compatibility mirror until web state delivery reads StateStore.
                if self._radio_state:
                    target = (
                        self._radio_state.sub if rx != 0 else self._radio_state.main
                    )
                    if target:
                        target.mode = mode
                    self.mark_polled("mode")
                if self._on_state_event:
                    self._on_state_event("mode_changed", {"mode": mode, "receiver": rx})
            case SetFilter(filter_num=fn, receiver=rx):
                if CAP_FILTER_WIDTH in self._caps:
                    self._ensure_receiver_supported(rx, operation="set_filter")
                    await radio.set_filter(fn, receiver=rx)
            case SetFilterWidth(width=width, receiver=rx):
                self._ensure_receiver_supported(rx, operation="set_filter_width")
                if not 50 <= width <= 10000:
                    raise CommandError(
                        f"set_filter_width value must be 50-10000 Hz, got {width}"
                    )
                # Hz↔index translation, profile-aware bounds + cmd29 wrapping
                # are owned by the backend (P2-04). Issue #1101.
                await radio.set_filter_width(width, receiver=rx)
                # filter_width read-after-write now flows through CommandService
                # pending overlays + the 0x1A 0x03 StateStore observation emitted
                # by ``_civ_rx.py`` (MOR-437); no legacy RadioState mirror needed.
                if self._on_state_event:
                    self._on_state_event(
                        "filter_width_changed", {"width": width, "receiver": rx}
                    )
            case SetFilterShape(shape=shape, receiver=rx):
                self._ensure_receiver_supported(rx, operation="set_filter_shape")
                if CAP_FILTER_SHAPE not in self._caps:
                    raise CommandError(
                        "set_filter_shape is not supported by this backend"
                    )
                # Domain legality (which shape values are valid for THIS
                # profile) is CoreRadio.set_filter_shape's job — the single
                # validation seat, not a hardcoded 0/1 duplicate here
                # (MOR-1534, mirrors the set_agc/set_preamp precedent).
                await radio.set_filter_shape(shape, receiver=rx)
                if self._radio_state:
                    target = (
                        self._radio_state.sub if rx != 0 else self._radio_state.main
                    )
                    target.filter_shape = shape
                if self._on_state_event:
                    self._on_state_event(
                        "filter_shape_changed", {"shape": shape, "receiver": rx}
                    )
            case PttOn():
                # Before the log line, the TX audio leg, and the lease: a
                # refused key must leave no trace on the air or in the rig.
                self._refuse_key_from_gone_session(command_source, session_id)
                logger.info("poller: PTT ON")
                managed = self._managed_tx(command_source, session_id)
                # Start TX audio stream before PTT (LAN audio requires this)
                if CAP_AUDIO in self._caps:
                    try:
                        start_tx = getattr(radio, "start_tx", None)
                        if start_tx is not None:
                            # Neutral AudioTransport surface (MOR-543): the
                            # backend resolves the TX format from its
                            # negotiated contract.
                            await start_tx()
                            logger.info(
                                "poller: TX audio stream started (neutral start_tx)"
                            )
                        else:
                            # Legacy per-codec fallback for radios without
                            # the neutral surface.
                            tx_codec, tx_sr = _audio_tx_codec_and_rate(radio)
                            if tx_codec == AudioCodec.PCM_1CH_16BIT:
                                await radio.start_audio_tx_pcm(sample_rate=tx_sr)
                            else:
                                await radio.start_audio_tx_opus()
                            logger.info(
                                "poller: TX audio stream started (tx_codec=%s, sr=%d)",
                                tx_codec,
                                tx_sr,
                            )
                    except Exception as e:
                        # MOR-1178: a failed arm refuses the key. Swallowed, it
                        # fell through to the write below and keyed a rig whose
                        # modulation path is dead — an unmodulated carrier the
                        # operator believes is a transmission — and did so
                        # before any lease existed to record it. So disarm the
                        # half-armed leg and refuse, exactly as the two
                        # refusals below: a refused key leaves no trace on the
                        # air, and costs one reported, recoverable transmission
                        # where a silent carrier costs airtime nobody can see.
                        logger.warning(
                            "poller: refusing PTT ON: start TX audio failed: %s", e
                        )
                        await self._stop_tx_audio_leg()
                        raise CommandError(
                            f"TX audio failed to arm, refusing PTT ON: {e}"
                        ) from e
                if managed is None:
                    # Binding nothing is two findings, and only one may reach
                    # the raw write: an unmanaged rig (every shipped
                    # serial/USB Icom backend this poller serves; bounded
                    # below by MOR-1220's 180s backstop, full managed arm
                    # pending MOR-1219), or an ingress with no owner a lease could be
                    # released against — which on a managed rig would key with
                    # no lease, no owner and no watchdog, the unsupervised
                    # bypass management exists to close. Resolving a supervisor
                    # is backend code that can fail (MOR-1187) and runs with the
                    # TX audio leg above already armed, so refusal and failed
                    # resolution alike disarm it on the way out, mirroring the
                    # managed refusal below: a refused key leaves no trace on
                    # the air.
                    try:
                        if refuse_key_without_owner(radio, command_source, session_id):
                            logger.warning(
                                "poller: refusing PTT ON from %s ingress: this "
                                "radio is managed and the request carries no "
                                "releasable owner",
                                command_source,
                            )
                            raise CommandError(
                                f"managed TX refused PTT ON from {command_source}: "
                                "no owner identity to hold the lease"
                            )
                    except BaseException:
                        await self._stop_tx_audio_leg()
                        raise
                    await radio.set_ptt(True)
                    # MOR-1220: only now, and only here. After the write, so a
                    # key that never reached the rig arms no bound; on this arm
                    # only, so the managed path keeps the supervisor's watchdog
                    # as its single bound and an EXTERNAL key stays untouched.
                    self._arm_max_key_down(command_source, session_id)
                else:
                    transition = await managed.set_ptt(True)
                    if transition.outcome not in _KEY_ACCEPTED:
                        # The TX audio leg above is armed but the rig is not
                        # ours: disarm it, or modulation keeps flowing towards
                        # a rig nobody keyed. Reported, never swallowed — the
                        # operator must not believe they are on the air.
                        await self._stop_tx_audio_leg()
                        raise CommandError(
                            f"managed TX rejected PTT ON: {transition.outcome}"
                        )
            case PttOff():
                logger.info("poller: PTT OFF")
                # The unkey is a fire-and-forget CI-V write and can raise
                # (connection/timeout/transport). So can the bind below it:
                # resolving the supervisor runs backend code — a ``managed_tx``
                # accessor is free to fail — so it binds INSIDE this guard, not
                # above it (MOR-1187). The audio teardown must run through
                # either failure: a failed de-key with the TX audio leg still
                # pumping modulation into the rig is the worst outcome
                # available (MOR-1013). ``finally`` keeps the original
                # exception intact — it propagates unwrapped so the caller's
                # ``_mark_queued_command_failed`` classification is unchanged —
                # while the teardown's own ``except Exception`` guarantees a
                # failing teardown can never replace it.
                try:
                    managed = self._managed_tx(command_source, session_id)
                    if managed is None:
                        # No owner gate here, and there must never be one: the
                        # key arm above refuses an ownerless ingress, but an
                        # unkey refused for the same reason strands a keyed
                        # transmitter with nobody able to take it off the air
                        # (the ``_refuse_key_from_gone_session`` asymmetry).
                        # Ownerless de-keys keep the unconditional legacy write.
                        await radio.set_ptt(False)
                    else:
                        # A refused release answers STALE: nothing was keyed, or
                        # another owner holds the lease. Neither is actionable,
                        # and raising would break defensive unkeys in ``finally``.
                        await managed.set_ptt(False)
                    # MOR-1220: every unkey this poller issues disarms the
                    # backstop — operator, teardown and drain all reach here.
                    # Below the write, never in the ``finally``: an unkey that
                    # RAISED left the rig keyed, and must not drop the bound.
                    self._cancel_max_key_down()
                finally:
                    await self._stop_tx_audio_leg()
            case SetPower(level=level, unit=unit):
                if unit != "raw_255":
                    raise ValueError(
                        f"Icom backend expects SetPower unit='raw_255' "
                        f"(0-255 CI-V scale); got unit={unit!r}"
                    )
                if CAP_POWER_CONTROL in self._caps:
                    await radio.set_rf_power(level)
            case SetRfGain(level=level, receiver=rx):
                if CAP_RF_GAIN in self._caps:
                    self._ensure_receiver_supported(rx, operation="set_rf_gain")
                    await radio.set_rf_gain(level, receiver=rx)
            case SetAfLevel(level=level, receiver=rx):
                if CAP_AF_LEVEL in self._caps:
                    self._ensure_receiver_supported(rx, operation="set_af_level")
                    await radio.set_af_level(level, receiver=rx)
            case SetSquelch(level=level, receiver=rx):
                if CAP_SQUELCH in self._caps:
                    self._ensure_receiver_supported(rx, operation="set_squelch")
                    await radio.set_squelch(level, receiver=rx)
            case SetNB(on=on, receiver=rx):
                self._ensure_receiver_supported(rx, operation="set_nb")
                if CAP_NB in self._caps:
                    await radio.set_nb(on, receiver=rx)
                # nb read-after-write now flows through CommandService pending
                # overlays + the 0x16 0x22 StateStore observation (MOR-437).
                if self._on_state_event:
                    self._on_state_event("nb_changed", {"on": on, "receiver": rx})
            case SetNR(on=on, receiver=rx):
                self._ensure_receiver_supported(rx, operation="set_nr")
                if CAP_NR in self._caps:
                    await radio.set_nr(on, receiver=rx)
                # nr read-after-write now flows through CommandService pending
                # overlays + the 0x16 0x40 StateStore observation (MOR-437).
                if self._on_state_event:
                    self._on_state_event("nr_changed", {"on": on, "receiver": rx})
            case SetDigiSel(on=on, receiver=rx):
                if CAP_DIGISEL in self._caps:
                    self._ensure_receiver_supported(rx, operation="set_digisel")
                    await radio.set_digisel(on, receiver=rx)
                if self._on_state_event:
                    self._on_state_event("digisel_changed", {"on": on, "receiver": rx})
            case SetIpPlus(on=on, receiver=rx):
                if CAP_IP_PLUS in self._caps:
                    self._ensure_receiver_supported(rx, operation="set_ipplus")
                    await radio.set_ip_plus(on, receiver=rx)
                if self._on_state_event:
                    self._on_state_event("ipplus_changed", {"on": on, "receiver": rx})
            case SetAttenuator(db=db, receiver=rx):
                self._ensure_receiver_supported(rx, operation="set_attenuator")
                if CAP_ATTENUATOR in self._caps:
                    await radio.set_attenuator_level(db, receiver=rx)
                # att read-after-write now flows through CommandService pending
                # overlays + the 0x11 StateStore observation (MOR-437).
                if self._on_state_event:
                    self._on_state_event(
                        "attenuator_changed", {"db": db, "receiver": rx}
                    )
            case SetPreamp(level=level, receiver=rx):
                self._ensure_receiver_supported(rx, operation="set_preamp")
                if CAP_PREAMP in self._caps:
                    await radio.set_preamp(level, receiver=rx)
                # preamp read-after-write now flows through CommandService pending
                # overlays + the 0x16 0x02 StateStore observation (MOR-437).
                if self._on_state_event:
                    self._on_state_event(
                        "preamp_changed", {"level": level, "receiver": rx}
                    )
            case SetPbtInner(level=level, receiver=rx):
                await _r.set_pbt_inner(level, receiver=rx)
                self._apply_compatibility_mirror(
                    lambda state: setattr(
                        state.sub if rx != 0 else state.main,
                        "pbt_inner",
                        level,
                    )
                )
                if self._on_state_event:
                    self._on_state_event(
                        "pbt_inner_changed", {"level": level, "receiver": rx}
                    )
            case SetPbtOuter(level=level, receiver=rx):
                await _r.set_pbt_outer(level, receiver=rx)
                self._apply_compatibility_mirror(
                    lambda state: setattr(
                        state.sub if rx != 0 else state.main,
                        "pbt_outer",
                        level,
                    )
                )
                if self._on_state_event:
                    self._on_state_event(
                        "pbt_outer_changed", {"level": level, "receiver": rx}
                    )
            case SetIfShift(offset=offset, receiver=rx):
                await _r.set_if_shift(offset, receiver=rx)
                if self._radio_state:
                    target = (
                        self._radio_state.sub if rx != 0 else self._radio_state.main
                    )
                    target.if_shift = offset
                if self._on_state_event:
                    self._on_state_event(
                        "if_shift_changed", {"offset": offset, "receiver": rx}
                    )
            case SetNRLevel(level=level, receiver=rx):
                # nr_level read-after-write via overlays + 0x14 0x06 observation.
                await _r.set_nr_level(level, receiver=rx)
            case SetNBLevel(level=level, receiver=rx):
                # nb_level read-after-write via overlays + 0x14 0x12 observation.
                await _r.set_nb_level(level, receiver=rx)
            case SetAutoNotch(on=on, receiver=rx):
                # auto_notch read-after-write via overlays + 0x16 0x41 observation.
                await _r.set_auto_notch(on, receiver=rx)
            case SetManualNotch(on=on, receiver=rx):
                # manual_notch read-after-write via overlays + 0x16 0x48 observation.
                await _r.set_manual_notch(on, receiver=rx)
            case SetNotchFilter(level=level):
                await _r.set_notch_filter(level)
                if self._radio_state:
                    self._radio_state.notch_filter = level
            case SetAgcTimeConstant(value=value, receiver=rx):
                # agc_time_constant read-after-write via overlays + 0x1A 0x04
                # StateStore observation (MOR-437).
                await _r.set_agc_time_constant(value, receiver=rx)
            case SetCwPitch(value=value):
                await _r.set_cw_pitch(value)
                if self._radio_state:
                    self._radio_state.cw_pitch = value
            case SetKeySpeed(speed=speed):
                await _r.set_key_speed(speed)
                if self._radio_state:
                    self._radio_state.key_speed = speed
            case SetBreakIn(mode=mode):
                await _r.set_break_in(mode)
                if self._radio_state:
                    self._radio_state.break_in = mode
            case SetApf(mode=mode, receiver=rx):
                self._ensure_receiver_supported(rx, operation="set_apf")
                await _r.set_audio_peak_filter(mode, receiver=rx)
                if self._radio_state:
                    target = (
                        self._radio_state.sub if rx != 0 else self._radio_state.main
                    )
                    target.apf_type_level = mode
            case SetTwinPeak(on=on, receiver=rx):
                self._ensure_receiver_supported(rx, operation="set_twin_peak")
                await _r.set_twin_peak_filter(on, receiver=rx)
                if self._radio_state:
                    target = (
                        self._radio_state.sub if rx != 0 else self._radio_state.main
                    )
                    target.twin_peak_filter = on
            case SetDriveGain(level=level):
                await _r.set_drive_gain(level)
                if self._radio_state:
                    self._radio_state.drive_gain = level
            case ScanStart(scan_type=st):
                await _r.scan_start(mode=st)
                if self._radio_state:
                    self._radio_state.scanning = True
                    self._radio_state.scan_type = st
                # CI-V 0x0E is SET-ONLY on IC-7300 (CAT audit: no read
                # command) — a command-echoed observation is the only way
                # this ever leaves "missing" in the public fieldStatus
                # projection (MOR-1495).
                for name, value in (("scanning", True), ("scan_type", st)):
                    self._apply_global_command_echo_observation(
                        name,
                        value,
                        command_id=command_id,
                        source=command_source,
                        session_id=session_id,
                        command_service=command_service,
                        provider_generation=provider_generation,
                    )
            case ScanStop():
                await _r.scan_stop()
                if self._radio_state:
                    self._radio_state.scanning = False
                    self._radio_state.scan_type = 0
                for name, value in (("scanning", False), ("scan_type", 0)):
                    self._apply_global_command_echo_observation(
                        name,
                        value,
                        command_id=command_id,
                        source=command_source,
                        session_id=session_id,
                        command_service=command_service,
                        provider_generation=provider_generation,
                    )
            case ScanSetDfSpan(span=span):
                await _r.scan_set_df_span(span)
            case ScanSetResume(mode=resume_mode):
                await _r.scan_set_resume(resume_mode)
                masked = resume_mode & 0x0F
                if self._radio_state:
                    self._radio_state.scan_resume_mode = masked
                self._apply_global_command_echo_observation(
                    "scan_resume_mode",
                    masked,
                    command_id=command_id,
                    source=command_source,
                    session_id=session_id,
                    command_service=command_service,
                    provider_generation=provider_generation,
                )
            case SetDataMode(mode=mode, receiver=rx):
                self._ensure_receiver_supported(rx, operation="set_data_mode")
                if not 0 <= mode <= 3:
                    raise CommandError(f"set_data_mode mode must be 0-3, got {mode}")
                await radio.set_data_mode(mode, receiver=rx)
                # data_mode read-after-write via overlays + 0x1A 0x06 observation.
                if self._on_state_event:
                    self._on_state_event(
                        "data_mode_changed", {"mode": mode, "receiver": rx}
                    )
            case SetMicGain(level=level):
                # mic_gain read-after-write via overlays + 0x14 0x0B observation.
                await _r.set_mic_gain(level)
            case SetVox(on=on):
                # vox_on read-after-write via overlays + 0x16 0x46 observation.
                await _r.set_vox(on)
            case SetCompressorLevel(level=level):
                # compressor_level read-after-write via overlays + 0x14 0x0E obs.
                await _r.set_compressor_level(level)
            case SetMonitor(on=on):
                # monitor_on read-after-write via overlays + 0x16 0x45 observation.
                await _r.set_monitor(on)
            case SetMonitorGain(level=level):
                # monitor_gain read-after-write via overlays + 0x14 0x15 observation.
                await _r.set_monitor_gain(level)
            case SetDialLock(on=on):
                await _r.set_dial_lock(on)
                if self._radio_state:
                    self._radio_state.dial_lock = on
            case SetAgc(mode=mode, receiver=rx):
                if CAP_AGC in self._caps:
                    self._ensure_receiver_supported(rx, operation="set_agc")
                    await radio.set_agc(mode, receiver=rx)
                else:
                    # Wire bytes from TOML: set_agc = [0x16, 0x12]
                    await self._send_cmd("set_agc", bytes([mode]), receiver=rx)
                # agc read-after-write via overlays + 0x16 0x12 observation.
                if self._on_state_event:
                    self._on_state_event("agc_changed", {"mode": mode, "receiver": rx})
            case SetRitStatus(on=on):
                await _r.set_rit_status(on)
                if self._radio_state:
                    self._radio_state.rit_on = on
                if self._on_state_event:
                    self._on_state_event("rit_changed", {"on": on})
            case SetRitTxStatus(on=on):
                await _r.set_rit_tx_status(on)
                if self._radio_state:
                    self._radio_state.rit_tx = on
                if self._on_state_event:
                    self._on_state_event("rit_tx_changed", {"on": on})
            case SetRitFrequency(freq=freq):
                await _r.set_rit_frequency(freq)
                if self._radio_state:
                    self._radio_state.rit_freq = freq
                if self._on_state_event:
                    self._on_state_event("rit_freq_changed", {"hz": freq})
            case SetSplit(on=on):
                await _r.set_split(on)
                # split read-after-write via overlays + 0x0F StateStore
                # observation (MOR-437).
                if self._on_state_event:
                    self._on_state_event("split_changed", {"on": on})
            case SetBand(band=band):
                self._last_user_write_ts = time.monotonic()
                # Band Stack Register recall: 0x1A 0x01 <bsr_code> <register>
                # Read stored freq/mode from register 01 (latest)
                from ..commands import bcd_decode
                from ..types import Mode as CivMode

                bsr_ok = False
                try:
                    resp = await self._civ(
                        0x1A,
                        sub=0x01,
                        data=bytes([band, 0x01]),
                        wait_response=True,
                    )
                    if (
                        resp
                        and hasattr(resp, "data")
                        and resp.data
                        and len(resp.data) >= 8
                    ):
                        # BSR response: [1A 01 band reg] freq(5 BCD) mode filter ...
                        # Skip first 2 bytes (band + register) to get freq
                        freq = bcd_decode(resp.data[2:7])
                        mode_code = resp.data[7]
                        filter_num = resp.data[8] if len(resp.data) > 8 else 1
                        try:
                            mode_name = CivMode(mode_code).name.replace("_", "-")
                        except ValueError:
                            mode_name = "USB"
                        logger.info(
                            "BSR recall: band=%d freq=%d mode=%s fil=%d",
                            band,
                            freq,
                            mode_name,
                            filter_num,
                        )
                        await radio.set_freq(freq)
                        await asyncio.sleep(self._gap)
                        await radio.set_mode(mode_name, filter_num)
                        self._apply_bsr_readback_observations(
                            freq=freq,
                            mode=mode_name,
                            command_id=command_id,
                            source=source,
                            session_id=session_id,
                            command_service=command_service,
                            provider_generation=provider_generation,
                        )
                        # Update local state immediately (don't wait for transceive echo)
                        if self._radio_state:
                            target = self._radio_state.main
                            if target:
                                target.freq = freq
                                target.mode = mode_name
                            self.mark_polled("freq")
                            self.mark_polled("mode")
                        if self._on_state_event:
                            self._on_state_event(
                                "freq_changed", {"freq": freq, "receiver": 0}
                            )
                            self._on_state_event(
                                "mode_changed", {"mode": mode_name, "receiver": 0}
                            )
                        bsr_ok = True
                except Exception:
                    logger.debug("BSR recall failed", exc_info=True)

                if not bsr_ok:
                    # Fallback: set default freq from rig profile
                    default_freq: int | None = None
                    for fr in self._profile.freq_ranges:
                        for bi in fr.bands:
                            if bi.bsr_code == band:
                                default_freq = bi.default
                                break
                        if default_freq is not None:
                            break
                    if default_freq is not None:
                        logger.info(
                            "BSR fallback: band=%d → freq=%d", band, default_freq
                        )
                        await radio.set_freq(default_freq)
                    else:
                        logger.warning("set_band: unknown bsr_code=%d", band)
            case SelectVfo(vfo=vfo):
                self._last_user_write_ts = time.monotonic()
                vfo_upper = vfo.upper()
                slot: str | None = None
                if vfo_upper in ("A", "B"):
                    slot = vfo_upper
                elif self._profile.receiver_count == 1 and vfo_upper in (
                    "VFOA",
                    "VFOB",
                ):
                    slot = vfo_upper[-1]
                if slot is not None:
                    active_name = self._current_active().upper()
                    receiver = 1 if active_name == "SUB" else 0
                    self._ensure_receiver_supported(
                        receiver, operation="select_vfo_slot"
                    )
                    if self._profile.vfo_readback == "selected_unselected":
                        await self._select_and_bind_vfo_slot(
                            slot,
                            receiver=receiver,
                            active_name=active_name,
                            command_id=command_id,
                            source=source,
                            session_id=session_id,
                            command_service=command_service,
                            provider_generation=provider_generation,
                        )
                    else:
                        set_vfo_slot = getattr(radio, "set_vfo_slot", None)
                        if set_vfo_slot is not None:
                            await set_vfo_slot(slot, receiver=receiver)
                        else:
                            legacy_set_vfo = getattr(radio, "set_vfo", None)
                            if legacy_set_vfo is None:
                                logger.warning(
                                    "radio-poller: select_vfo(%s) — backend lacks "
                                    "set_vfo_slot and set_vfo; skipping",
                                    vfo,
                                )
                                return
                            await legacy_set_vfo(slot)
                        if self._radio_state is not None:
                            self._radio_state.receiver(active_name).active_slot = slot
                        if self._on_state_event:
                            self._on_state_event(
                                "vfo_changed", {"vfo": slot, "receiver": receiver}
                            )
                    return

                if vfo_upper in ("SUB", "1") or (
                    self._profile.receiver_count > 1 and vfo_upper == "VFOB"
                ):
                    is_sub = True
                elif vfo_upper in ("MAIN", "0") or (
                    self._profile.receiver_count > 1 and vfo_upper == "VFOA"
                ):
                    is_sub = False
                else:
                    raise CommandError(f"unknown VFO selection {vfo!r}")
                if is_sub:
                    self._ensure_receiver_supported(1, operation="select_vfo")
                current = self._current_active()
                # NB: local is intentionally named ``target_name`` — the
                # enclosing ``match`` has earlier branches that bind
                # ``target`` to ``ReceiverState | None`` (``self._radio_state.
                # sub`` / ``.main``).  Reusing the name here would confuse
                # mypy's type narrowing across branches.
                target_name = "SUB" if is_sub else "MAIN"
                if target_name != current:
                    if (is_sub and self._profile.vfo_sub_code is None) or (
                        not is_sub and self._profile.vfo_main_code is None
                    ):
                        raise CommandError(
                            f"select_vfo({vfo}) is unsupported by profile "
                            f"{self._profile.model}: no MAIN/SUB select code"
                        )
                    # Issue #1189: legacy backends (e.g. SerialMockRadio,
                    # 3rd-party Radio implementers) predate
                    # ``ReceiverBankCapable`` and only expose the legacy
                    # ``set_vfo`` overload.  Fall back to it so the poller
                    # does not AttributeError on those backends.  The
                    # DeprecationWarning from ``IcomRadio.set_vfo``
                    # (#1187) is intentional — it signals migration.
                    select_receiver = getattr(radio, "select_receiver", None)
                    if select_receiver is not None:
                        await select_receiver(target_name)
                        logger.info("radio-poller: select_receiver=%s", target_name)
                    else:
                        legacy_set_vfo = getattr(radio, "set_vfo", None)
                        if legacy_set_vfo is None:
                            logger.warning(
                                "radio-poller: select_vfo(%s) — backend "
                                "lacks select_receiver and set_vfo; skipping",
                                vfo,
                            )
                            return
                        await legacy_set_vfo(target_name)
                        logger.info(
                            "radio-poller: legacy set_vfo=%s "
                            "(backend lacks ReceiverBankCapable)",
                            target_name,
                        )
                    # ``select_receiver`` updates ``_radio_state.active`` on
                    # the dual-RX runtime; mirror it on radios that don't
                    # ship that wiring (test mocks, custom backends).
                    rs = getattr(self._radio, "_radio_state", None)
                    if rs is not None and hasattr(rs, "active"):
                        rs.active = target_name
                    # Scope follows the selected receiver: emit 0x27 0x12 so
                    # the spectrum/waterfall flips to the new band.  In
                    # dual-scope mode this still updates the "selected"
                    # receiver marker; in single-scope mode the displayed
                    # band changes.  Capability-gated so single-RX profiles
                    # (IC-7300/705) are unaffected.
                    if CAP_SCOPE in self._caps and CAP_DUAL_RX in self._caps:
                        scope_rx = 1 if is_sub else 0
                        try:
                            await self._civ(0x27, sub=0x12, data=bytes([scope_rx]))
                            logger.info(
                                "radio-poller: scope receiver → %s "
                                "(follows select_vfo)",
                                target,
                            )
                        except Exception:
                            logger.debug(
                                "radio-poller: scope follow failed",
                                exc_info=True,
                            )
                if self._on_state_event:
                    self._on_state_event("vfo_changed", {"vfo": vfo})
            case VfoSwap():
                self._last_user_write_ts = time.monotonic()
                if CAP_DUAL_RX in self._caps:
                    await radio.swap_main_sub()
                # After swap, active VFO stays same but freqs are exchanged
                if self._on_state_event:
                    self._on_state_event("vfo_swapped", {})
            case VfoEqualize():
                self._last_user_write_ts = time.monotonic()
                if CAP_DUAL_RX in self._caps:
                    await radio.equalize_main_sub()
            case EnableScope(policy=policy, generation=generation):
                if CAP_SCOPE in self._caps:
                    # Defer scope enable during initial fetch to avoid
                    # CI-V packet queue overflow (scope data + fetch).
                    if not self._initial_fetch_done.is_set():
                        if self._scope_demand_is_stale(generation):
                            return
                        if not self._scope_enable_deferred:
                            logger.info(
                                "radio-poller: deferring scope enable until initial fetch completes"
                            )
                            self._scope_enable_deferred = True
                        self._queue.put(
                            EnableScope(policy=policy, generation=generation)
                        )
                    else:
                        if self._scope_demand_is_stale(generation):
                            return
                        await self._enable_scope_session(policy=policy)
                        logger.info("radio-poller: scope enabled")
                        await self._fetch_scope_controls()
            case DisableScope(generation=generation):
                if CAP_SCOPE in self._caps:
                    if self._scope_demand_is_stale(generation):
                        return
                    await self.restore_scope_session()
                    logger.info("radio-poller: scope session state restored")
            case SwitchScopeReceiver(receiver=receiver):
                # Fire-and-forget scope receiver select (0x27 0x12)
                self._ensure_receiver_supported(
                    receiver,
                    operation="switch_scope_receiver",
                )
                await self._civ(0x27, sub=0x12, data=bytes([receiver]))
                if self._radio_state:
                    self._radio_state.scope_controls.receiver = receiver
                if CAP_SCOPE in self._caps:
                    await self._reconfirm_scope_field(
                        "get_scope_receiver", radio.get_scope_receiver
                    )
                logger.info(
                    "radio-poller: scope receiver → %s",
                    "SUB" if receiver else "MAIN",
                )
            case SetScopeDuringTx(on=on):
                if CAP_SCOPE in self._caps:
                    await radio.set_scope_during_tx(on)
                    if self._radio_state:
                        self._radio_state.scope_controls.during_tx = on
                    await self._reconfirm_scope_field(
                        "get_scope_during_tx", radio.get_scope_during_tx
                    )
            case SetScopeCenterType(center_type=center_type):
                if CAP_SCOPE in self._caps:
                    await radio.set_scope_center_type(center_type)
                    if self._radio_state:
                        self._radio_state.scope_controls.center_type = center_type
                    await self._reconfirm_scope_field(
                        "get_scope_center_type", radio.get_scope_center_type
                    )
            case SetScopeFixedEdge(edge=edge, start_hz=start_hz, end_hz=end_hz):
                if CAP_SCOPE in self._caps:
                    await radio.set_scope_fixed_edge(
                        edge=edge,
                        start_hz=start_hz,
                        end_hz=end_hz,
                    )
                    # radio.set_scope_fixed_edge already resolves the wire
                    # range_index and mirrors the full ScopeFixedEdge into
                    # this SAME RadioState.scope_controls object (see
                    # ScopeRuntimeMixin.set_scope_fixed_edge) — a separate
                    # optimistic write here would be a no-op (MOR-1530: a
                    # prior version of this arm did exactly that and it
                    # silently no-opped in production, masked by a bare
                    # AsyncMock double in tests). The reconfirm GET must
                    # target the SAME slot the SET just wrote — the IC-7610
                    # selector addresses ONE specific slot (MOR-662), so a
                    # bare re-read would default back to range 1/edge 1 and
                    # clobber the mirror with an unrelated slot's data.
                    if self._radio_state:
                        written = self._radio_state.scope_controls.fixed_edge
                        await self._reconfirm_scope_field(
                            "get_scope_fixed_edge",
                            lambda w=written: radio.get_scope_fixed_edge(
                                range_index=w.range_index, edge=w.edge
                            ),
                        )
                    else:
                        await self._reconfirm_scope_field(
                            "get_scope_fixed_edge", radio.get_scope_fixed_edge
                        )
            case SetScopeDual(dual=dual):
                if CAP_SCOPE in self._caps:
                    await radio.set_scope_dual(dual)
                    if self._radio_state:
                        self._radio_state.scope_controls.dual = dual
                    await self._reconfirm_scope_field(
                        "get_scope_dual", radio.get_scope_dual
                    )
            case SetScopeMode(mode=mode):
                if CAP_SCOPE in self._caps:
                    await radio.set_scope_mode(mode)
                    if self._radio_state:
                        self._radio_state.scope_controls.mode = mode
                    await self._reconfirm_scope_field(
                        "get_scope_mode", radio.get_scope_mode
                    )
            case SetScopeSpan(span=span):
                if CAP_SCOPE in self._caps:
                    await radio.set_scope_span(span)
                    if self._radio_state:
                        self._radio_state.scope_controls.span = span
                    await self._reconfirm_scope_field(
                        "get_scope_span", radio.get_scope_span
                    )
            case SetScopeSpeed(speed=speed):
                if CAP_SCOPE in self._caps:
                    await radio.set_scope_speed(speed)
                    if self._radio_state:
                        self._radio_state.scope_controls.speed = speed
                    await self._reconfirm_scope_field(
                        "get_scope_speed", radio.get_scope_speed
                    )
            case SetScopeRef(ref=ref):
                if CAP_SCOPE in self._caps:
                    await radio.set_scope_ref(ref)
                    if self._radio_state:
                        self._radio_state.scope_controls.ref_db = float(ref)
                    await self._reconfirm_scope_field(
                        "get_scope_ref", radio.get_scope_ref
                    )
            case SetScopeHold(on=on):
                if CAP_SCOPE in self._caps:
                    await radio.set_scope_hold(on)
                    if self._radio_state:
                        self._radio_state.scope_controls.hold = on
                    await self._reconfirm_scope_field(
                        "get_scope_hold", radio.get_scope_hold
                    )
            case SetScopeEdge(edge=edge):
                if CAP_SCOPE in self._caps:
                    await radio.set_scope_edge(edge)
                    if self._radio_state:
                        self._radio_state.scope_controls.edge = edge
                    await self._reconfirm_scope_field(
                        "get_scope_edge", radio.get_scope_edge
                    )
            case SetScopeVbw(narrow=narrow):
                if CAP_SCOPE in self._caps:
                    await radio.set_scope_vbw(narrow)
                    if self._radio_state:
                        self._radio_state.scope_controls.vbw_narrow = narrow
                    await self._reconfirm_scope_field(
                        "get_scope_vbw", radio.get_scope_vbw
                    )
            case SetScopeRbw(rbw=rbw):
                if CAP_SCOPE in self._caps:
                    await radio.set_scope_rbw(rbw)
                    if self._radio_state:
                        self._radio_state.scope_controls.rbw = rbw
                    await self._reconfirm_scope_field(
                        "get_scope_rbw", radio.get_scope_rbw
                    )
            case SetPowerstat(on=on):
                if CAP_POWER_CONTROL in self._caps:
                    await radio.set_powerstat(on)
                    # Optimistic update: radio won't respond to polls when off
                    if self._radio_state is not None:
                        self._radio_state.power_on = on
                    self._emit("powerstat_changed", {"power_on": on})
                    logger.info("radio-poller: power %s", "ON" if on else "OFF")
            case SetTunerStatus(value=value):
                if CAP_TUNER in self._caps:
                    await radio.set_tuner_status(value)
                    # tuner_status read-after-write via overlays + 0x1C 0x01
                    # StateStore observation (MOR-437).
                    self._emit("tuner_changed", {"value": value})
            case SetAntenna1(on=on):
                # IC-7610: 0x12 0x00 selects ANT1, data byte encodes RX-ANT OFF/ON.
                if CAP_ANTENNA in self._caps:
                    await radio.set_antenna_1(on)
                    if self._radio_state is not None:
                        self._radio_state.tx_antenna = 1
                        self._radio_state.rx_antenna_1 = on
            case SetAntenna2(on=on):
                if CAP_ANTENNA in self._caps:
                    await radio.set_antenna_2(on)
                    if self._radio_state is not None:
                        self._radio_state.tx_antenna = 2
                        self._radio_state.rx_antenna_2 = on
            case SetRxAntennaAnt1(on=on):
                # IC-7610 RX-ANT is encoded as data byte on 0x12 0x00.
                # WARNING: This selects ANT1 as TX.
                if CAP_ANTENNA in self._caps:
                    await radio.set_rx_antenna_ant1(on)
                    if self._radio_state is not None:
                        self._radio_state.tx_antenna = 1
                        self._radio_state.rx_antenna_1 = on
            case SetRxAntennaAnt2(on=on):
                # IC-7610 RX-ANT is encoded as data byte on 0x12 0x01.
                # WARNING: This selects ANT2 as TX.
                if CAP_ANTENNA in self._caps:
                    await radio.set_rx_antenna_ant2(on)
                    if self._radio_state is not None:
                        self._radio_state.tx_antenna = 2
                        self._radio_state.rx_antenna_2 = on
            case SetSystemDate(year=year, month=month, day=day):
                if CAP_SYSTEM_SETTINGS in self._caps:
                    await radio.set_system_date(year, month, day)
            case SetSystemTime(hour=hour, minute=minute):
                if CAP_SYSTEM_SETTINGS in self._caps:
                    await radio.set_system_time(hour, minute)
            case SetAcc1ModLevel(level=level):
                if CAP_DATA_MODE in self._caps:
                    await radio.set_acc1_mod_level(level)
            case SetUsbModLevel(level=level):
                if CAP_DATA_MODE in self._caps:
                    await radio.set_usb_mod_level(level)
            case SetLanModLevel(level=level):
                if CAP_DATA_MODE in self._caps:
                    await radio.set_lan_mod_level(level)
            case SetDualWatch(on=on):
                if CAP_DUAL_WATCH in self._caps:
                    await radio.set_dual_watch(on)
            case SetCompressor(on=on):
                if CAP_COMPRESSOR in self._caps:
                    await radio.set_compressor(on)
            case SetToneFreq(freq_hz=freq, receiver=rx):
                self._ensure_receiver_supported(rx, operation="set_tone_freq")
                if CAP_REPEATER_TONE in self._caps:
                    await radio.set_tone_freq(freq, receiver=rx)
                if self._radio_state:
                    target = (
                        self._radio_state.sub if rx != 0 else self._radio_state.main
                    )
                    target.tone_freq = freq
            case SetTsqlFreq(freq_hz=freq, receiver=rx):
                self._ensure_receiver_supported(rx, operation="set_tsql_freq")
                if CAP_TSQL in self._caps:
                    await radio.set_tsql_freq(freq, receiver=rx)
                if self._radio_state:
                    target = (
                        self._radio_state.sub if rx != 0 else self._radio_state.main
                    )
                    target.tsql_freq = freq
            case SetMainSubTracking(on=on):
                if CAP_MAIN_SUB_TRACKING in self._caps:
                    await radio.set_main_sub_tracking(on)
                if self._radio_state:
                    self._radio_state.main_sub_tracking = on
            case SetSsbTxBandwidth(value=value):
                if CAP_SSB_TX_BW in self._caps:
                    await radio.set_ssb_tx_bandwidth(value)
                if self._radio_state:
                    self._radio_state.ssb_tx_bandwidth = value
            case SetManualNotchWidth(value=value, receiver=rx):
                self._ensure_receiver_supported(rx, operation="set_manual_notch_width")
                if CAP_NOTCH in self._caps:
                    await radio.set_manual_notch_width(value, receiver=rx)
            case SetBreakInDelay(level=level):
                if CAP_BREAK_IN in self._caps:
                    await radio.set_break_in_delay(level)
                if self._radio_state:
                    self._radio_state.break_in_delay = level
            case SetVoxGain(level=level):
                if CAP_VOX in self._caps:
                    await radio.set_vox_gain(level)
                if self._radio_state:
                    self._radio_state.vox_gain = level
            case SetAntiVoxGain(level=level):
                if CAP_VOX in self._caps:
                    await radio.set_anti_vox_gain(level)
                if self._radio_state:
                    self._radio_state.anti_vox_gain = level
            case SetVoxDelay(level=level):
                if CAP_VOX in self._caps:
                    await radio.set_vox_delay(level)
                if self._radio_state:
                    self._radio_state.vox_delay = level
            case SetNbDepth(level=level, receiver=rx):
                self._ensure_receiver_supported(rx, operation="set_nb_depth")
                if CAP_NB in self._caps:
                    # NB depth is a GLOBAL menu item (0x1A 05 02 90), not
                    # per-receiver: the setter takes no ``receiver`` argument.
                    await radio.set_nb_depth(level)
                    # Write-through readback (MOR-491-B): confirm the value the
                    # radio actually took (clamp/quantization) instead of a blind
                    # optimistic mirror. Resilient: a failed readback never kills
                    # the command path.
                    try:
                        confirmed = await radio.get_nb_depth()
                    except Exception:
                        logger.debug(
                            "radio-poller: get_nb_depth readback failed",
                            exc_info=True,
                        )
                    else:
                        self._apply_global_control_observation(
                            "nb_depth",
                            confirmed,
                            command_id=command_id,
                            source=command_source,
                            session_id=session_id,
                            command_service=command_service,
                            provider_generation=provider_generation,
                        )
            case SetNbWidth(level=level, receiver=rx):
                self._ensure_receiver_supported(rx, operation="set_nb_width")
                if CAP_NB in self._caps:
                    # NB width is a GLOBAL menu item (0x1A 05 02 91), not
                    # per-receiver: the setter takes no ``receiver`` argument.
                    await radio.set_nb_width(level)
                    # Write-through readback (MOR-491-B): confirm the real value.
                    try:
                        confirmed = await radio.get_nb_width()
                    except Exception:
                        logger.debug(
                            "radio-poller: get_nb_width readback failed",
                            exc_info=True,
                        )
                    else:
                        self._apply_global_control_observation(
                            "nb_width",
                            confirmed,
                            command_id=command_id,
                            source=command_source,
                            session_id=session_id,
                            command_service=command_service,
                            provider_generation=provider_generation,
                        )
            case SetDashRatio(value=value):
                if CAP_CW in self._caps:
                    await radio.set_dash_ratio(value)
                if self._radio_state:
                    self._radio_state.dash_ratio = value
            case SetRepeaterTone(on=on, receiver=rx):
                self._ensure_receiver_supported(rx, operation="set_repeater_tone")
                if CAP_REPEATER_TONE in self._caps:
                    await radio.set_repeater_tone(on, receiver=rx)
                if self._radio_state:
                    target = (
                        self._radio_state.sub if rx != 0 else self._radio_state.main
                    )
                    target.repeater_tone = on
            case SetRepeaterTsql(on=on, receiver=rx):
                self._ensure_receiver_supported(rx, operation="set_repeater_tsql")
                if CAP_TSQL in self._caps:
                    await radio.set_repeater_tsql(on, receiver=rx)
                if self._radio_state:
                    target = (
                        self._radio_state.sub if rx != 0 else self._radio_state.main
                    )
                    target.repeater_tsql = on
            case SetRxAntenna(antenna=antenna, on=on):
                if CAP_RX_ANTENNA in self._caps:
                    if antenna == 1:
                        await radio.set_rx_antenna_ant1(on)
                    else:
                        await radio.set_rx_antenna_ant2(on)
            case SetMemoryMode(channel=channel):
                if isinstance(radio, MemoryCapable):
                    await radio.set_memory_mode(channel)
            case MemoryWrite():
                if isinstance(radio, MemoryCapable):
                    await radio.memory_write()
            case MemoryToVfo(channel=channel):
                if isinstance(radio, MemoryCapable):
                    await radio.memory_to_vfo(channel)
            case MemoryClear(channel=channel):
                if isinstance(radio, MemoryCapable):
                    await radio.memory_clear(channel)
            case SetMemoryContents(mem=mem):
                if isinstance(radio, MemoryCapable):
                    await radio.set_memory_contents(mem)
            case SetBsr(bsr=bsr):
                if isinstance(radio, MemoryCapable):
                    await radio.set_bsr(bsr)
            case SetDataOffModInput(source=mod_source):
                if CAP_DATA_MODE in self._caps:
                    await radio.set_data_off_mod_input(mod_source)
                    # Write-through readback (MOR-615): confirm the value the
                    # radio actually took, mirroring the NB depth/width route.
                    await self._read_mod_input(
                        "data_off_mod_input",
                        command_id=command_id,
                        source=command_source,
                        session_id=session_id,
                        command_service=command_service,
                        provider_generation=provider_generation,
                    )
            case SetData1ModInput(source=mod_source):
                if CAP_DATA_MODE in self._caps:
                    await radio.set_data1_mod_input(mod_source)
                    await self._read_mod_input(
                        "data1_mod_input",
                        command_id=command_id,
                        source=command_source,
                        session_id=session_id,
                        command_service=command_service,
                        provider_generation=provider_generation,
                    )
            case SetData2ModInput(source=mod_source):
                if CAP_DATA_MODE in self._caps:
                    await radio.set_data2_mod_input(mod_source)
                    await self._read_mod_input(
                        "data2_mod_input",
                        command_id=command_id,
                        source=command_source,
                        session_id=session_id,
                        command_service=command_service,
                        provider_generation=provider_generation,
                    )
            case SetData3ModInput(source=mod_source):
                if CAP_DATA_MODE in self._caps:
                    await radio.set_data3_mod_input(mod_source)
                    await self._read_mod_input(
                        "data3_mod_input",
                        command_id=command_id,
                        source=command_source,
                        session_id=session_id,
                        command_service=command_service,
                        provider_generation=provider_generation,
                    )
            case SetAudioPeakFilter(on=on, receiver=rx):
                self._ensure_receiver_supported(rx, operation="set_audio_peak_filter")
                if CAP_APF in self._caps:
                    await radio.set_audio_peak_filter(int(on), receiver=rx)
            case SetDigiselShift(level=level, receiver=rx):
                self._ensure_receiver_supported(rx, operation="set_digisel_shift")
                if CAP_DIGISEL in self._caps:
                    await radio.set_digisel_shift(level, receiver=rx)
            case SetRefAdjust(value=value):
                await _r.set_ref_adjust(value)
                if self._radio_state:
                    self._radio_state.ref_adjust = value
            case SetCivTransceive(on=on):
                await _r.set_civ_transceive(on)
            case SetCivOutputAnt(on=on):
                await _r.set_civ_output_ant(on)
            case SetAfMute(on=on, receiver=rx):
                await _r.set_af_mute(on, receiver=rx)
                if self._radio_state:
                    target = (
                        self._radio_state.sub if rx != 0 else self._radio_state.main
                    )
                    target.af_mute = on
            case SetTuningStep(step=step):
                await _r.set_tuning_step(step)
                if self._radio_state:
                    self._radio_state.tuning_step = step
            case SetXfcStatus(on=on):
                await _r.set_xfc_status(on)
            case SetTxFreqMonitor(on=on):
                await _r.set_tx_freq_monitor(on)
                if self._radio_state:
                    self._radio_state.tx_freq_monitor = on
            case SetUtcOffset(hours=hours, minutes=minutes, is_negative=is_negative):
                await _r.set_utc_offset(hours, minutes, is_negative)
            case QuickSplit():
                await _r.quick_split()
            case QuickDualWatch():
                await _r.quick_dual_watch()
            case QuickDwTrigger():
                self._last_user_write_ts = time.monotonic()
                if CAP_DUAL_RX in self._caps:
                    await _r.equalize_main_sub()
                    await _r.set_dual_watch(True)
                    logger.info("radio-poller: quick DW (equalize + DW ON)")
                    if self._on_state_event:
                        self._on_state_event("dual_watch_changed", {"on": True})
            case QuickSplitTrigger():
                self._last_user_write_ts = time.monotonic()
                if CAP_DUAL_RX in self._caps:
                    await _r.equalize_main_sub()
                    await _r.set_split(True)
                    logger.info("radio-poller: quick SPLIT (equalize + SPLIT ON)")
                    if self._radio_state:
                        self._radio_state.split = True
                    if self._on_state_event:
                        self._on_state_event("split_changed", {"on": True})
            case Speak(mode=what):
                await _r.get_speech(what)

    # Fast: meters (polled on even cycles)
    # wfview: Priority=Highest, queue interval 25ms for LAN (HasFDComms)
    # For serial: only high-priority meters to keep S-meter responsive.
    _FAST_CMDS_LAN: list[tuple[int, int | None]] = [
        (0x15, 0x02),  # S-meter
        (0x15, 0x11),  # RF power
        (0x15, 0x12),  # SWR
        (0x15, 0x13),  # ALC
        (0x15, 0x14),  # Compressor meter
        (0x15, 0x15),  # VD (voltage)
        (0x15, 0x16),  # Id (PA drain current)
    ]
    _FAST_CMDS_SERIAL: list[tuple[int, int | None]] = [
        (0x15, 0x02),  # S-meter — polled every cycle for responsiveness
        (0x15, 0x11),  # RF power
        (0x15, 0x02),  # S-meter again (2:1 ratio vs other meters)
        (0x15, 0x12),  # SWR
    ]
    _FAST_CMDS: list[tuple[int, int | None]] = _FAST_CMDS_LAN  # class default

    # Issue #937 — two-tier meter scheme (LAN only).
    # HIGH tier — emitted on most meter cycles, gated by PTT.
    _HIGH_TIER_RX: list[tuple[int, int | None]] = [
        (0x15, 0x02),  # S-meter
    ]
    _HIGH_TIER_TX: list[tuple[int, int | None]] = [
        (0x15, 0x11),  # RF power
        (0x15, 0x12),  # SWR
        (0x15, 0x13),  # ALC
    ]
    # LOW tier — emitted every _LOW_STRIDE-th HIGH meter cycle, rotating.
    _LOW_TIER: list[tuple[int, int | None]] = [
        (0x15, 0x14),  # Compressor meter
        (0x15, 0x15),  # Vd
        (0x15, 0x16),  # Id
    ]
    _LOW_STRIDE: int = 5

    # State queries interleaved on odd cycles.
    # Tuple: (cmd, sub, receiver) where receiver=None means global query.
    # Populated per instance from runtime profile/capabilities.
    _STATE_QUERIES: list[tuple[int, int | None, int | None]] = []

    def _pick_high_meter(self, high_idx: int) -> tuple[int, int | None]:
        """Choose HIGH-tier meter based on PTT state."""
        on_tx = (
            getattr(self._radio_state, "ptt", False)
            if self._radio_state is not None
            else False
        )
        if not on_tx:
            return self._HIGH_TIER_RX[0]
        return self._HIGH_TIER_TX[high_idx % len(self._HIGH_TIER_TX)]

    def _flush_due_meter_observations(self) -> None:
        coalescer = getattr(self._radio, "_meter_observation_coalescer", None)
        if not isinstance(coalescer, MeterObservationCoalescer):
            return
        runtime = getattr(self._radio, "_civ_runtime", None)
        flush_due = getattr(runtime, "flush_due_meter_observations", None)
        if not callable(flush_due):
            return
        try:
            flush_due(now=time.monotonic())
        except Exception:
            logger.debug("radio-poller: meter coalescer flush failed", exc_info=True)

    def _acquisition_request_expired(
        self,
        request: AcquisitionRequest,
        *,
        sent_at: float,
        now: float,
    ) -> bool:
        # MOR-874: the deadline must be SEND-relative once the request has
        # actually been dispatched. The enqueue-relative ``deadline_monotonic``
        # only bounds requests still waiting to be sent (``sent_at == 0``);
        # using it (or ``min`` with it) for a sent request fires a false
        # timeout when the request sat queued under load and the radio's fast
        # answer lands just after enqueue_time + max_age. A sent request is
        # only expired ``timeout`` (or ``max_age`` when no explicit timeout is
        # set) seconds after its SEND time — never relative to enqueue time.
        if sent_at > 0.0:
            window: float = (
                request.timeout if request.timeout is not None else request.max_age
            )
            return bool(now >= sent_at + window)
        return bool(now >= request.deadline_monotonic)

    def _civ_link_healthy(self, *, now: float) -> bool:
        """Return True when the CI-V transport is demonstrably alive (MOR-874).

        Used to gate false acquisition timeouts: a request deadline that fires
        while the link is healthy (recent CI-V data, no recovery in progress)
        is a queueing artifact, not a backend failure, and must not decay the
        adaptive cadence.
        """

        radio = self._radio
        if bool(getattr(radio, "_civ_recovering", False)):
            return False
        last_civ = getattr(radio, "_last_civ_data_received", None)
        if not isinstance(last_civ, (int, float)) or isinstance(last_civ, bool):
            return False
        ready_timeout = getattr(radio, "_civ_ready_idle_timeout", 2.0)
        if not isinstance(ready_timeout, (int, float)) or isinstance(
            ready_timeout, bool
        ):
            ready_timeout = 2.0
        return (now - float(last_civ)) <= float(ready_timeout)

    async def _send_scheduler_requests(self) -> None:
        scheduler = self._acquisition_scheduler
        if scheduler is None:
            return
        now = time.monotonic()
        # MOR-1525: gate tx_only cadence membership (TX/PA meters: power, SWR,
        # ALC, comp) on the CANONICAL ``global.tx_state.ptt`` observation, not
        # the legacy RadioState.ptt mirror the MOR-1485 comment above used to
        # justify. The mirror was live-proven to desync from the canonical
        # fact: after a TX it stayed True while the StateStore's own
        # observation had already flipped False in RX, so the tx_only group
        # kept polling at ~1s cadence during confirmed RX (operator-visible
        # as the SWR readout flapping 0<->1, MOR-1525). Read the same field
        # ``build_public_state_payload_from_snapshot`` serves to the UI, so
        # this can never disagree with what the operator is shown. Fail
        # closed: unobserved/stale/unknown ptt -> tx_active False, so
        # tx_only meters stay idle rather than spuriously poll — the honest
        # direction when the fact isn't known.
        try:
            ptt_field = self._state_store.snapshot().field(
                FieldPath.global_("tx_state", "ptt")
            )
        except KeyError:
            tx_active = False
        else:
            tx_active = ptt_field.freshness is FreshnessState.FRESH and bool(
                ptt_field.value
            )
        scheduler.due_requests(now=now, tx_active=tx_active)
        # MOR-1533: dispatch must use the tx_active-gated view. Crediting an
        # already-sent answer (runtime._civ_rx) uses the unfiltered
        # pending_requests() instead, so an answer landing after de-key is
        # never blinded by this gate -- see dispatchable_requests()'s
        # docstring.
        pending = scheduler.dispatchable_requests()
        pending_ids = {request.id for request in pending}
        for request_id in tuple(self._acquisition_in_flight):
            if request_id not in pending_ids:
                del self._acquisition_in_flight[request_id]
                # MOR-874: request left flight (credited / dropped) — drop its
                # grace bookkeeping so the map never leaks.
                self._acquisition_healthy_grace_started.pop(request_id, None)

        for request in pending:
            sent_paths: frozenset[FieldPath] = frozenset()
            sent_at = 0.0
            existing = self._acquisition_in_flight.get(request.id)
            if existing is not None:
                sent_paths, sent_at = existing
                if self._acquisition_request_expired(
                    request,
                    sent_at=sent_at,
                    now=now,
                ):
                    # MOR-874: when the deadline fires but the CI-V link is
                    # healthy, this is (usually) a false timeout — the radio
                    # answered and the deadline raced under load. Suppress the
                    # false-timeout -> adaptive-decay chain and keep the request
                    # in flight so the returning observation can credit it.
                    #
                    # But the health gate reads the GLOBAL last-CI-V timestamp,
                    # so under external-CAT load it reads healthy ~permanently;
                    # a request whose specific answer is genuinely lost would
                    # then be pinned forever. Bound the suppression with a grace
                    # window: once it elapses with the request still uncredited,
                    # fall back to a REAL timeout (drop it so the scheduler
                    # re-queues/re-sends and normal failure accounting/decay
                    # applies).
                    link_healthy = self._civ_link_healthy(now=now)
                    grace_expired = False
                    if link_healthy:
                        grace_started = self._acquisition_healthy_grace_started.get(
                            request.id
                        )
                        if grace_started is None:
                            self._acquisition_healthy_grace_started[request.id] = now
                            grace_started = now
                        if now - grace_started >= _ACQUISITION_HEALTHY_GRACE_SECONDS:
                            grace_expired = True
                    # Treat a grace-expired healthy expiry exactly like an
                    # unhealthy one: count it, drop it, let cadence advance.
                    timeout_is_real = (not link_healthy) or grace_expired
                    self._record_state_diagnostic(
                        "acquisition_request_failed",
                        "web.radio_poller",
                        request_id=request.id,
                        paths=[str(path) for path in request.paths],
                        reason="acquisition_request_timeout",
                        link_healthy=link_healthy,
                        grace_expired=grace_expired,
                    )
                    scheduler.record_acquisition_failure(
                        request,
                        reason="acquisition_request_timeout",
                        failed_paths=sent_paths or frozenset(request.paths),
                        now=now,
                        link_healthy=not timeout_is_real,
                    )
                    if timeout_is_real:
                        self._acquisition_in_flight.pop(request.id, None)
                        self._acquisition_healthy_grace_started.pop(request.id, None)
                        continue
                    # Healthy link, still within grace: leave in flight, skip
                    # re-send this cycle (no extra CI-V traffic — important not
                    # to compete with external CAT).
                    sent_paths = sent_paths.intersection(request.paths)
                    if all(path in sent_paths for path in request.paths):
                        continue
                else:
                    sent_paths = sent_paths.intersection(request.paths)

            if all(path in sent_paths for path in request.paths):
                continue

            executor = self._acquisition_executor
            if executor is None:
                self._record_state_diagnostic(
                    "acquisition_executor_missing",
                    "web.radio_poller",
                    request_id=request.id,
                    paths=[str(path) for path in request.paths],
                    provider=request.provider,
                )
                scheduler.record_acquisition_failure(
                    request,
                    reason="acquisition_executor_missing",
                    now=now,
                )
                continue

            result = await executor.execute(
                request,
                already_sent_paths=sent_paths,
            )
            newly_sent = tuple(result.sent_paths)
            failed_paths = tuple(result.failed_paths)
            if failed_paths:
                reason = result.failure_reason or "acquisition_request_failed"
                self._record_state_diagnostic(
                    "acquisition_request_failed",
                    "web.radio_poller",
                    request_id=request.id,
                    paths=[str(path) for path in failed_paths],
                    reason=reason,
                    provider=request.provider,
                )
                scheduler.record_acquisition_failure(
                    request,
                    reason=reason,
                    failed_paths=failed_paths,
                    now=now,
                )

            if newly_sent:
                self._acquisition_in_flight[request.id] = (
                    sent_paths.union(newly_sent),
                    now,
                )
                self._record_state_diagnostic(
                    "acquisition_request_sent",
                    "web.radio_poller",
                    request_id=request.id,
                    paths=[str(path) for path in newly_sent],
                    # MOR-1533: dispatchable_requests(), matching this
                    # drain's own dispatch view -- not the unfiltered
                    # pending_requests(), which would also count entries
                    # this drain will never send (withheld tx_only hints).
                    pending_request_count=len(scheduler.dispatchable_requests()),
                )

    async def _send_query(self) -> None:
        self._flush_due_meter_observations()
        if self._acquisition_scheduler is not None:
            await self._send_scheduler_requests()
            return
        # Even cycles → meter query; odd cycles → state query.
        if self._poll_index % 2 == 0:
            if self._is_serial:
                # Serial path UNCHANGED — keep flat round-robin over _FAST_CMDS.
                fast_idx = (self._poll_index // 2) % len(self._FAST_CMDS)
                cmd_byte, sub_byte = self._FAST_CMDS[fast_idx]
            else:
                # LAN: two-tier scheme (issue #937).
                high_idx = self._poll_index // 2
                on_tx = (
                    getattr(self._radio_state, "ptt", False)
                    if self._radio_state is not None
                    else False
                )
                if not on_tx and high_idx % self._LOW_STRIDE == 0:
                    low_idx = (high_idx // self._LOW_STRIDE) % len(self._LOW_TIER)
                    cmd_byte, sub_byte = self._LOW_TIER[low_idx]
                else:
                    cmd_byte, sub_byte = self._pick_high_meter(high_idx)
            self._record_state_diagnostic(
                "meter_cadence",
                "web.radio_poller",
                command=f"0x{cmd_byte:02x}",
                sub=None if sub_byte is None else f"0x{sub_byte:02x}",
                poll_index=self._poll_index,
                serial=self._is_serial,
            )
            self._record_state_diagnostic(
                "backend_read",
                "web.radio_poller",
                family="meters",
                command=f"0x{cmd_byte:02x}",
                sub=None if sub_byte is None else f"0x{sub_byte:02x}",
            )
            await self._civ(
                cmd_byte,
                sub=sub_byte,
                data=b"",
                priority=Priority.BACKGROUND,
                wait_dispatch=False,
            )
        else:
            if not self._STATE_QUERIES:
                self._poll_index += 1
                return
            state_idx = (self._poll_index // 2) % len(self._STATE_QUERIES)
            cmd_byte, sub_byte, receiver = self._STATE_QUERIES[state_idx]
            self._record_state_diagnostic(
                "backend_read",
                "web.radio_poller",
                family="state",
                command=f"0x{cmd_byte:02x}",
                sub=None if sub_byte is None else f"0x{sub_byte:02x}",
                receiver=receiver,
            )
            await self._send_one_state_query(cmd_byte, sub_byte, receiver)
        self._poll_index += 1

    # Issue #2303: passive observation must never select or exchange a VFO.
    # Inactive A/B state may therefore remain unknown/stale until a genuinely
    # non-mutating provider read exists for the active profile.
    _UNSELECTED_SLOT_INTERVAL: float = 5.0  # sec between refreshes per rx
    _UNSELECTED_SLOT_DEBOUNCE: float = 0.5  # sec after last user freq/mode write

    def _unselected_slot_gate(self, receiver: int) -> bool:
        """Return False: exchange-based inactive-slot reads mutate hardware."""
        _ = receiver
        return False

    async def _poll_unselected_slot(self, receiver: int) -> None:
        """Intentionally do nothing; swap/query/swap is not passive polling."""
        _ = receiver

    @staticmethod
    def _relative_vfo_fields() -> tuple[str, ...]:
        return ("freq_hz", "mode", "filter_num", "data_mode")

    async def establish_vfo_identity(self) -> None:
        """Command VFO A once, but only when the radio cannot ever report
        which slot (A/B) is active on its own (MOR-1443).

        Discriminator: ``vfo_readback == "selected_unselected"`` is
        profile/capability data (``rigs/<model>.toml``), not a hardcoded
        model check. It already means "this CI-V radio can read the
        selected/unselected VFO's frequency+mode, but never which physical
        slot is selected" — issue #2303 forbids learning that passively
        (a swap-based probe would itself mutate the radio), so absent a
        commanded write, ``activeSlot`` stays unknown forever and the UI is
        stuck behind the manual "Select VFO A/B" fallback.

        Ruled exception to the no-uncommanded-writes doctrine (owner
        decision, MOR-1443, session 19): the owner explicitly accepted the
        radio-visible side effect — a radio left on VFO B at app start gets
        switched to A once. Radios that CAN report identity on their own
        (absolute CI-V VFO readback, Yaesu CAT ``get_vfo_select``, the
        rigctld client) declare a different ``vfo_readback`` value (or use a
        backend that never touches this poller at all) and never reach this
        branch — they keep reading, never writing.

        Called once per connect from the one-time startup section of
        :meth:`_run`, and again from the web server's reconnect path
        (``WebServer._on_radio_reconnect`` → its ``_refetch_and_reenable``
        closure, right after the poller readiness gate is re-set) so that a
        soft-reconnect re-establishes identity too, instead of staying
        unknown until process restart (MOR-1443 review R2, finding 1).
        ``reset_vfo_session()`` always runs first on that path and
        unconditionally discards ``active_slot`` — no reconnect path
        retains it. The already-observed gate below therefore is not about
        surviving a reconnect: between ``reset_vfo_session()`` clearing
        identity and this coroutine's own read of the state store, the
        poll loop is still running and may drain an operator-initiated
        ``SelectVfo`` in that window, legitimately observing identity again
        first — the gate exists to avoid a redundant second commanded
        write over that observation (and to guard any future retention
        path). Also paused, like the rest of this poller's writes, while an
        external CAT session owns the wire (MOR-166 slice 2) — a commanded
        VFO A here would collide with the owner's byte stream.

        Goes through the normal :class:`SelectVfo` command path so the
        existing confirmed-select readback is what marks identity observed,
        exactly like an operator-issued "Select VFO A/B" would.
        """

        # External CAT session (e.g. Hamlib A1 bridge) owns the wire — this
        # write must not escape that pause any more than the poll loop's own
        # writes do (MOR-1443 review R2, finding 2). ``is True`` (not just
        # truthy), matching the poll loop's own guard, so duck-typed / mock
        # radios never quiesce by accident — only a real bool flag does.
        if getattr(self._radio, "external_cat_session_active", False) is True:
            return
        if self._profile.vfo_readback != "selected_unselected":
            return
        receiver = 1 if self._current_active().upper() == "SUB" else 0
        try:
            self._state_store.snapshot().field(FieldPath.active_slot(str(receiver)))
        except KeyError:
            pass
        else:
            return  # identity already observed — nothing to establish
        logger.info(
            "radio-poller: active-VFO identity unqueryable and unobserved; "
            "auto-commanding VFO A once (MOR-1443, receiver=%d)",
            receiver,
        )
        await self._execute(SelectVfo(vfo="A"))

    def _vfo_identity_paths(self, receiver: int) -> tuple[FieldPath, ...]:
        receiver_id = str(receiver)
        paths: list[FieldPath] = [FieldPath.active_slot(receiver_id)]
        for slot in ("A", "B"):
            paths.extend(
                FieldPath.vfo_slot(receiver_id, slot, "freq_mode", name)
                for name in self._relative_vfo_fields()
            )
        return tuple(paths)

    def reset_vfo_session(self) -> None:
        """Invalidate connection-epoch A/B proof without touching TX facts."""

        self._vfo_binding_generation += 1
        relative_reset = self._state_store.reset_relative_vfo_retention(
            generation=self._provider_generation()
        )
        try:
            setattr(self._radio, "_relative_vfo_observations_suspended", False)
        except Exception:
            pass
        paths: list[FieldPath] = []
        for receiver in range(self._profile.receiver_count):
            paths.extend(self._vfo_identity_paths(receiver))
            receiver_id = str(receiver)
            paths.extend(
                FieldPath.unselected(receiver_id, "freq_mode", name)
                for name in self._relative_vfo_fields()
            )
            if self._profile.vfo_readback == "selected_unselected":
                paths.extend(
                    FieldPath.active(receiver_id, "freq_mode", name)
                    for name in self._relative_vfo_fields()
                )
        changeset = self._state_store.discard(paths)
        if (relative_reset.changes or changeset.changes) and self._on_state_event:
            self._on_state_event("vfo_identity_reset", {})

    def _discard_vfo_identity(self, receiver: int) -> None:
        self._state_store.discard(self._vfo_identity_paths(receiver))

    def _tx_target_receiver(self) -> tuple[int, TxReceiver]:
        """Resolve the receiver index + MAIN/SUB label carrying TX.

        Mirrors :meth:`establish_vfo_identity`'s own resolution (MOR-1443).
        """
        receiver = 1 if self._current_active().upper() == "SUB" else 0
        label: TxReceiver = "SUB" if receiver == 1 else "MAIN"
        return receiver, label

    @staticmethod
    def _tx_target_input(
        snapshot: StateSnapshot, path: FieldPath
    ) -> tuple[Any, UnknownTxTarget | None]:
        """Look up one derivation input: ``(value, None)`` if FRESH, else
        ``(None, UnknownTxTarget(...))`` explaining why it cannot be used."""
        try:
            field = snapshot.field(path)
        except KeyError:
            return None, UnknownTxTarget(reason="not-observed")
        if field.freshness is FreshnessState.STALE:
            return None, UnknownTxTarget(reason="stale")
        if field.freshness is not FreshnessState.FRESH:
            return None, UnknownTxTarget(reason="not-observed")
        return field.value, None

    def _compute_tx_target(self) -> TxTarget:
        """Derive TX target identity from already-observed CI-V facts (MOR-1496).

        Unlike Yaesu CAT's native ``get_tx_func`` (see
        ``backends/yaesu_cat/observations.py``), Icom CI-V never reports a TX
        target directly. The facts that determine it are already tracked
        independently in the state store: active-VFO identity
        (``receiver.<rx>.vfo.active_slot``, established once per connect by
        :meth:`establish_vfo_identity`, MOR-1443, since this CI-V scheme can
        never passively report which slot is active), split
        (``global.tx_state.split``, cmd 0x0F), and the selected/unselected
        frequencies (``receiver.<rx>.[active|unselected].freq_mode.freq_hz``,
        cmd 0x25).

        Split OFF transmits on the selected-slot frequency; split ON
        transmits on the OTHER (unselected) slot's frequency — do not copy
        Yaesu's MAIN/SUB ``get_tx_func`` toggle semantics here, it answers a
        different question. Any input unobserved or stale fails this closed;
        each input is re-checked on every call, so the result's freshness is
        only ever as good as its weakest input.

        Only radios that can never passively report VFO identity
        (``vfo_readback == "selected_unselected"``, the exact gate
        :meth:`establish_vfo_identity` uses) get a derivation; other CI-V VFO
        schemes (absolute readback, MAIN/SUB-only radios like IC-9700/IC-7610)
        stay ``unsupported`` rather than guess at unvalidated split semantics.
        """
        if self._profile.vfo_readback != "selected_unselected":
            return UnknownTxTarget(reason="unsupported")

        receiver, receiver_label = self._tx_target_receiver()
        receiver_id = str(receiver)
        snapshot = self._state_store.snapshot()

        slot, unknown = self._tx_target_input(
            snapshot, FieldPath.active_slot(receiver_id)
        )
        if unknown is not None:
            return unknown
        if slot not in ("A", "B"):
            return UnknownTxTarget(reason="contradiction")

        split_value, unknown = self._tx_target_input(
            snapshot, FieldPath.global_("tx_state", "split")
        )
        if unknown is not None:
            return unknown
        split_on = bool(split_value)

        if split_on:
            freq_path = FieldPath.unselected(receiver_id, "freq_mode", "freq_hz")
            target_slot: TxSlot = "B" if slot == "A" else "A"
        else:
            freq_path = FieldPath.active(receiver_id, "freq_mode", "freq_hz")
            target_slot = cast(TxSlot, slot)

        frequency, unknown = self._tx_target_input(snapshot, freq_path)
        if unknown is not None:
            return unknown
        if type(frequency) is not int or isinstance(frequency, bool) or frequency <= 0:
            return UnknownTxTarget(reason="contradiction")

        return KnownTxTarget(
            receiver=receiver_label,
            slot=target_slot,
            frequency_hz=frequency,
        )

    def _tx_target_max_age(self) -> float:
        """TTL for the derived ``tx_target`` field itself (review R2, F1).

        Without this, ``StateStore.mark_stale_due`` skips the field forever
        (it only ages entries with ``max_age`` set — see its own docstring),
        so a stale input would silently freeze ``tx_target`` at its last
        FRESH value instead of degrading, a fail-open on a TX gate. Reuses
        this profile's default acquisition TTL — ``policy_for`` falls back
        to ``default_policy`` for any path with no declared capability (3.0s
        on IC-7300) — which needs no capability declaration for
        ``tx_target`` itself; see :meth:`_publish_tx_target` for why that
        declaration must never exist. Falls back to
        ``_TX_TARGET_FALLBACK_MAX_AGE`` (a fixed default, not a bare multiple
        of the poll loop's own fast interval — see that constant's comment)
        if a profile has no acquisition policy at all.
        """
        acquisition = self._profile.state_acquisition
        ttl = (
            None
            if acquisition is None
            else acquisition.policy_for(
                FieldPath.global_("tx_state", "tx_target")
            ).freshness_ttl_seconds
        )
        return ttl if ttl is not None else _TX_TARGET_FALLBACK_MAX_AGE

    def _publish_tx_target(self) -> None:
        """Recompute and, if changed or stale, republish tx_target (MOR-1496).

        Reached only on poll-loop iterations that fall through to step 4 —
        NOT "every tick": the external-CAT pause and the connection-backoff/
        dead-link branches above all ``continue`` past this point, so the
        weakest-input-tracking claim below depends on :meth:`_tx_target_max_age`
        giving the field its own TTL (F1), not on this being called on a fixed
        cadence.

        Skips the store write when the recomputed value is unchanged AND the
        currently stored entry is still comfortably FRESH — under HALF its
        own TTL old (review R2, F2 + review R3 fix): re-applying an
        identical value on every reachable tick was bumping the store's
        global ``observation_seq`` for no semantic change, which busts
        delivery-key no-op suppression and HTTP 304s for the WHOLE snapshot,
        not just this field. The half-TTL renew margin is load-bearing, not
        cosmetic: R2 skipped on value-equality alone with no age check, so on
        a healthy radio the stored entry's ``last_observed_monotonic`` never
        advanced between real changes — it aged out under its own TTL and
        flapped known/unknown every TTL period purely from the skip itself
        (verifier measured 12 transitions in 18s, each pushing a WS
        broadcast). A value change, the stored entry crossing the half-TTL
        mark, or it having aged fully past its own TTL, all still write —
        the last of those is what lets a healthy-again re-derivation heal
        the field back to FRESH after it actually went stale.

        Never declare ``global.tx_state.tx_target`` in any profile's
        polling_only/unsolicited_push capability metadata (rigs/*.toml or
        ``RadioAcquisitionProfile.field_policies``): its absence from
        capability metadata is exactly what lets the TTL-driven
        reconciliation request this ``max_age`` generates drop cleanly
        instead of looping — ``AcquisitionScheduler.query_for_path`` has no
        CI-V wire mapping for this derived field, so a declared/pollable
        capability here would retry forever as ``no_civ_query_mapping``.

        Uses ``apply_current`` (not ``apply``): this runs synchronously off
        the just-read snapshot with no ``await`` in between, so it always
        stamps the store's current provider generation.
        """
        # IC-705 also has vfo_readback == "selected_unselected" but currently
        # ships no [state_acquisition] block, so nothing ever actively polls
        # split for it (AcquisitionScheduler.query_for_path's split mapping
        # only fires through a scheduler built from that block) — tx_target
        # is derived here regardless, but will sit at "not-observed" on that
        # radio until split is ever observed. Tracked as a follow-up; not
        # fixed here.
        if self._profile.vfo_readback != "selected_unselected":
            return

        target = self._compute_tx_target()
        path = FieldPath.global_("tx_state", "tx_target")
        max_age = self._tx_target_max_age()
        now = time.monotonic()
        try:
            current = self._state_store.snapshot().field(path)
        except KeyError:
            current = None
        if (
            current is not None
            and current.freshness is FreshnessState.FRESH
            and current.value == target
            and now - current.last_observed_monotonic < max_age * 0.5
        ):
            return

        observation = Observation(
            path=path,
            value=target,
            source=SourceMetadata(
                source="local_reconcile",
                provider="icom_civ",
                native_id="tx_target_derivation",
            ),
            timestamp_monotonic=now,
            max_age=max_age,
        )
        self._state_store.apply_current(observation)

    async def _select_and_bind_vfo_slot(
        self,
        slot: str,
        *,
        receiver: int,
        active_name: str,
        command_id: str | None,
        source: CommandSource,
        session_id: str | None,
        command_service: CommandService | None,
        provider_generation: int,
    ) -> None:
        """Select once, then bind only transaction-scoped passive readback."""

        self._vfo_binding_generation += 1
        generation = self._vfo_binding_generation
        selection_confirmed = False
        setattr(self._radio, "_relative_vfo_observations_suspended", True)

        def apply(path: FieldPath, value: Any) -> None:
            observation = Observation(
                path=path,
                value=value,
                source=SourceMetadata(
                    source="command_response",
                    provider="vfo_binding",
                    command_source=source,
                    session_id=session_id,
                    native_id="explicit_slot_ack_readback",
                ),
                timestamp_monotonic=time.monotonic(),
                correlation_id=command_id,
                provider_generation=provider_generation,
            )
            if command_service is not None:
                command_service.apply_observation(observation)
            else:
                self._state_store.apply(observation)

        try:
            confirmed_select = getattr(self._radio, "_set_vfo_slot_confirmed", None)
            if confirmed_select is None or not asyncio.iscoroutinefunction(
                confirmed_select
            ):
                raise CommandError(
                    "selected/unselected provider lacks confirmed VFO selection"
                )
            await confirmed_select(slot, receiver=receiver)
            if (
                generation != self._vfo_binding_generation
                or provider_generation != self._provider_generation()
            ):
                return

            self._discard_vfo_identity(receiver)
            selection_confirmed = True
            apply(FieldPath.active_slot(str(receiver)), slot)
            if self._radio_state is not None:
                self._radio_state.receiver(active_name).active_slot = slot

            if not isinstance(self._radio, RelativeVfoReadbackCapable):
                raise CommandError("provider lacks relative VFO readback")
            selected = await self._radio.read_relative_vfo(selected=True)
            if (
                generation != self._vfo_binding_generation
                or provider_generation != self._provider_generation()
            ):
                return
            unselected = await self._radio.read_relative_vfo(selected=False)
            if (
                generation != self._vfo_binding_generation
                or provider_generation != self._provider_generation()
            ):
                return
            receiver_id = str(receiver)
            for state, relative_slot in (
                (selected, "selected"),
                (unselected, "unselected"),
            ):
                for name in self._relative_vfo_fields():
                    value = getattr(state, name)
                    relative_path = (
                        FieldPath.active(receiver_id, "freq_mode", name)
                        if relative_slot == "selected"
                        else FieldPath.unselected(receiver_id, "freq_mode", name)
                    )
                    apply(relative_path, value)
            logger.info(
                "radio-poller: confirmed VFO slot=%s receiver=%d generation=%d",
                slot,
                receiver,
                generation,
            )
            if self._on_state_event:
                self._on_state_event("vfo_changed", {"vfo": slot, "receiver": receiver})
        except BaseException:
            # After ACK the target remains known even if passive readback fails.
            if (
                generation == self._vfo_binding_generation
                and provider_generation == self._provider_generation()
                and not selection_confirmed
            ):
                self._discard_vfo_identity(receiver)
            raise
        finally:
            if generation == self._vfo_binding_generation:
                setattr(self._radio, "_relative_vfo_observations_suspended", False)

    def _emit(self, name: str, data: dict[str, Any]) -> None:
        if self._on_state_event is not None:
            self._on_state_event(name, data)

    def _record_state_diagnostic(self, kind: str, source: str, **details: Any) -> None:
        if self._state_diagnostics is not None:
            self._state_diagnostics.record(kind, source, **details)
