"""RadioPoller — fire-and-forget CI-V serialiser.

## ARCHITECTURE PRINCIPLE: FIRE-AND-FORGET ONLY

All CI-V communication MUST be fire-and-forget.  Never await a CI-V response.

Why: The IC-7610 scope streams ~225 CI-V packets/sec on port 50002.  When
a request-response command waits for a specific reply, the response packet
gets lost among scope frames, causing 2-second timeouts that cascade and
freeze the entire poller.

wfview (the reference implementation) works the same way: commands go out,
responses are parsed from the incoming stream — nobody waits for a specific
reply.

How it works:
1. RadioPoller sends fire-and-forget CI-V queries (get_freq, get_mode, etc.)
2. The CI-V RX loop receives all packets and projects them into RadioState.
3. RadioState is the canonical source of truth for web consumers.
4. Poll freshness stays local to the poller; broadcast events notify on changes.

DO NOT add request-response (await get_frequency, await get_mode, etc.)
to this module. If you need new data, add parsing to the CI-V RX path instead.
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import TYPE_CHECKING, Any, Callable

from ..exceptions import CommandError
from ..exceptions import ConnectionError as RadioConnectionError
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
from .._state_queries import build_state_queries
from ..profiles import RadioProfile, resolve_radio_profile
from ..types import AudioCodec

if TYPE_CHECKING:
    from ..radio_protocol import Radio
    from ..radio_state import RadioState

__all__ = [
    "RadioPoller",
    "CommandQueue",
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


def _audio_tx_codec_and_rate(radio: Any) -> tuple[AudioCodec | None, int]:
    contract = getattr(radio, "audio_stream_contract", None)
    tx_codec = getattr(contract, "tx_codec", None)
    tx_sr = getattr(contract, "tx_sample_rate_hz", None)
    if not isinstance(tx_sr, int) or isinstance(tx_sr, bool) or tx_sr <= 0:
        tx_sr = 48000
    return tx_codec, tx_sr


# ------------------------------------------------------------------
# Command types — canonical definitions live in rigplane._poller_types.
# Re-exported here for backward compatibility.
# ------------------------------------------------------------------

from .._poller_types import (  # noqa: E402
    Command,
    CommandQueue,
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

    State is updated from the CI-V RX stream into RadioState,
    NOT from polling responses.
    """

    def __init__(
        self,
        radio: "Radio",
        command_queue: CommandQueue,
        legacy_queue: CommandQueue | None = None,
        *,
        on_state_event: Callable[[str, dict[str, Any]], None] | None = None,
        radio_state: "RadioState | None" = None,
    ) -> None:
        queue = legacy_queue if legacy_queue is not None else command_queue
        self._radio = radio
        self._radio_state = radio_state
        self._queue = queue
        self._on_state_event = on_state_event
        self._poll_index: int = 0
        self._revision: int = 0
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
        # Set by default — cleared at _run() start, re-set after initial fetch.
        # This prevents EnableScope from hanging in tests that don't call start().
        self._initial_fetch_done = asyncio.Event()
        self._initial_fetch_done.set()
        self._scope_enable_deferred = False
        # Issue #715: track user-initiated freq/mode writes so the unselected-
        # slot poll subroutine can debounce around them, and per-receiver
        # timestamps so each receiver's unselected slot is refreshed no more
        # than once per _UNSELECTED_SLOT_INTERVAL.
        self._last_user_write_ts: float = 0.0
        self._last_unselected_poll: dict[int, float] = {}

    def start(self) -> None:
        if self._task is not None and not self._task.done():
            return
        self._task = asyncio.get_running_loop().create_task(
            self._run(), name="radio-poller"
        )
        logger.info("radio-poller: started")

    def stop(self) -> None:
        if self._task is not None:
            self._task.cancel()
            self._task = None
            logger.info("radio-poller: stopped")

    @property
    def running(self) -> bool:
        return self._task is not None and not self._task.done()

    @property
    def revision(self) -> int:
        """Monotonic counter incremented on every radio state change."""
        return self._revision

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

    def bump_revision(self) -> None:
        """Increment the revision counter (called on each state change)."""
        self._revision += 1

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
        if receiver != 0 and self._profile.supports_cmd29(cmd, sub):
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
        sub_byte: int | None,
        receiver: int | None,
    ) -> None:
        """Send a single state query (shared by initial fetch and slow rotation)."""
        if receiver is not None:
            if cmd_byte in (0x25, 0x26):
                await self._civ(cmd_byte, data=bytes([receiver]))
            else:
                inner = bytes([receiver, cmd_byte])
                if sub_byte is not None:
                    inner += bytes([sub_byte])
                await self._civ(0x29, data=inner)
        elif cmd_byte == 0x27 and sub_byte in self._SCOPE_RECEIVER_PREFIX_SUBS:
            # Scope control queries need receiver prefix (00=MAIN, 01=SUB)
            scope_rx = 0
            if self._radio_state:
                scope_rx = self._radio_state.scope_controls.receiver
            await self._civ(cmd_byte, sub=sub_byte, data=bytes([scope_rx]))
        else:
            await self._civ(cmd_byte, sub=sub_byte, data=b"")

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
        # keep the existing throttle.
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
            ("get_scope_rbw", radio.get_scope_rbw),
        )
        for label, getter in scope_getters:
            try:
                await asyncio.wait_for(getter(), timeout=self._SCOPE_GETTER_TIMEOUT)
            except asyncio.TimeoutError:
                logger.debug(
                    "radio-poller: %s timed out after %.0f ms (response dropped)",
                    label,
                    self._SCOPE_GETTER_TIMEOUT * 1000,
                )
            except Exception:
                logger.debug("radio-poller: %s failed", label, exc_info=True)
            await asyncio.sleep(self._adaptive_gap())
        logger.info("radio-poller: scope controls fetched (receiver=%d)", scope_rx)

    async def _run(self) -> None:
        _backoff = 0.0
        _MAX_BACKOFF = 5.0  # max pause when radio is disconnected

        # Initial state is now fetched by CoreRadio._fetch_initial_state()
        # during connect(). Just signal readiness immediately.
        self._scope_enable_deferred = False
        self._initial_fetch_done.set()

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
                            await self._execute(cmd)
                            if entry.future is not None and not entry.future.done():
                                entry.future.set_result(None)
                            _backoff = 0.0
                        except (ConnectionError, RadioConnectionError) as exc:
                            if entry.future is not None and not entry.future.done():
                                entry.future.set_exception(exc)
                            _backoff = min(_backoff + 0.5, _MAX_BACKOFF)
                        except Exception as exc:
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
                    logger.debug("radio-poller: query error", exc_info=True)

                # 3. Issue #715: opportunistically refresh the unselected
                # VFO slot on each receiver.  Fully gated (PTT, queue
                # pressure, debounce, per-rx interval) so it cannot
                # regress fast-poll cadence.
                for _rx in range(self._profile.receiver_count):
                    try:
                        await self._poll_unselected_slot(_rx)
                    except (ConnectionError, RadioConnectionError):
                        _backoff = min(_backoff + 0.5, _MAX_BACKOFF)
                        break
                    except Exception:
                        logger.debug(
                            "radio-poller: unselected-slot poll error", exc_info=True
                        )

                # 4. Wait for next cycle
                await self._queue.wait(timeout=self._fast_interval)
        except asyncio.CancelledError:
            pass
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
    ) -> Any:
        """Send a raw CI-V command if the backend provides a CI-V transport.

        For non-Icom backends this is a no-op — scope/meter polling simply
        won't happen, which is acceptable.

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
            )
        return None

    def _current_active(self) -> str:
        rs = getattr(self._radio, "_radio_state", None)
        _active = getattr(rs, "active", None) if rs is not None else None
        return _active if isinstance(_active, str) else "MAIN"

    async def _execute(self, cmd: Command) -> None:
        radio = self._radio
        _r: Any = radio  # cast for capability methods not on base Radio protocol
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
                # Optimistic state update for frequency
                if self._radio_state:
                    target = (
                        self._radio_state.sub if rx != 0 else self._radio_state.main
                    )
                    if target:
                        target.freq = freq
                    self.bump_revision()
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
                # Optimistic state update for mode
                if self._radio_state:
                    target = (
                        self._radio_state.sub if rx != 0 else self._radio_state.main
                    )
                    if target:
                        target.mode = mode
                    self.bump_revision()
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
                if self._radio_state:
                    target = (
                        self._radio_state.sub if rx != 0 else self._radio_state.main
                    )
                    target.filter_width = width
                    self.bump_revision()
                if self._on_state_event:
                    self._on_state_event(
                        "filter_width_changed", {"width": width, "receiver": rx}
                    )
            case SetFilterShape(shape=shape, receiver=rx):
                self._ensure_receiver_supported(rx, operation="set_filter_shape")
                if shape not in (0, 1):
                    raise CommandError(
                        f"set_filter_shape value must be 0 or 1, got {shape}"
                    )
                if CAP_FILTER_SHAPE not in self._caps:
                    raise CommandError(
                        "set_filter_shape is not supported by this backend"
                    )
                await radio.set_filter_shape(shape, receiver=rx)
                if self._radio_state:
                    target = (
                        self._radio_state.sub if rx != 0 else self._radio_state.main
                    )
                    target.filter_shape = shape
                    self.bump_revision()
                if self._on_state_event:
                    self._on_state_event(
                        "filter_shape_changed", {"shape": shape, "receiver": rx}
                    )
            case PttOn():
                logger.info("poller: PTT ON")
                # Start TX audio stream before PTT (LAN audio requires this)
                if CAP_AUDIO in self._caps:
                    try:
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
                        logger.warning("poller: start TX audio failed: %s", e)
                await radio.set_ptt(True)
            case PttOff():
                logger.info("poller: PTT OFF")
                await radio.set_ptt(False)
                # Stop TX audio stream after PTT, then restart RX
                if CAP_AUDIO in self._caps:
                    try:
                        tx_codec, _tx_sr = _audio_tx_codec_and_rate(radio)
                        if tx_codec == AudioCodec.PCM_1CH_16BIT:
                            await radio.stop_audio_tx_pcm()
                        else:
                            await radio.stop_audio_tx_opus()
                        logger.info("poller: TX audio stream stopped")

                        # Restart RX audio after TX (IC-7610 doesn't support full duplex)
                        async def _noop_rx(_pkt: Any) -> None:
                            pass

                        await radio.start_audio_rx_opus(_noop_rx)
                        logger.info("poller: RX audio stream restarted")
                    except Exception as e:
                        logger.debug("poller: audio stream transition failed: %s", e)
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
                if self._radio_state:
                    target = (
                        self._radio_state.sub if rx != 0 else self._radio_state.main
                    )
                    target.nb = on
                    self.bump_revision()
                if self._on_state_event:
                    self._on_state_event("nb_changed", {"on": on, "receiver": rx})
            case SetNR(on=on, receiver=rx):
                self._ensure_receiver_supported(rx, operation="set_nr")
                if CAP_NR in self._caps:
                    await radio.set_nr(on, receiver=rx)
                if self._radio_state:
                    target = (
                        self._radio_state.sub if rx != 0 else self._radio_state.main
                    )
                    target.nr = on
                    self.bump_revision()
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
                if self._radio_state:
                    target = (
                        self._radio_state.sub if rx != 0 else self._radio_state.main
                    )
                    target.att = db
                    if db > 0:
                        target.preamp = 0
                    self.bump_revision()
                if self._on_state_event:
                    self._on_state_event(
                        "attenuator_changed", {"db": db, "receiver": rx}
                    )
            case SetPreamp(level=level, receiver=rx):
                self._ensure_receiver_supported(rx, operation="set_preamp")
                if CAP_PREAMP in self._caps:
                    await radio.set_preamp(level, receiver=rx)
                if self._radio_state:
                    target = (
                        self._radio_state.sub if rx != 0 else self._radio_state.main
                    )
                    target.preamp = level
                    if level > 0:
                        target.att = 0
                    self.bump_revision()
                if self._on_state_event:
                    self._on_state_event(
                        "preamp_changed", {"level": level, "receiver": rx}
                    )
            case SetPbtInner(level=level, receiver=rx):
                await _r.set_pbt_inner(level, receiver=rx)
                if self._radio_state:
                    target = (
                        self._radio_state.sub if rx != 0 else self._radio_state.main
                    )
                    target.pbt_inner = level
                    self.bump_revision()
                if self._on_state_event:
                    self._on_state_event(
                        "pbt_inner_changed", {"level": level, "receiver": rx}
                    )
            case SetPbtOuter(level=level, receiver=rx):
                await _r.set_pbt_outer(level, receiver=rx)
                if self._radio_state:
                    target = (
                        self._radio_state.sub if rx != 0 else self._radio_state.main
                    )
                    target.pbt_outer = level
                    self.bump_revision()
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
                    self.bump_revision()
                if self._on_state_event:
                    self._on_state_event(
                        "if_shift_changed", {"offset": offset, "receiver": rx}
                    )
            case SetNRLevel(level=level, receiver=rx):
                await _r.set_nr_level(level, receiver=rx)
                if self._radio_state:
                    target = (
                        self._radio_state.sub if rx != 0 else self._radio_state.main
                    )
                    target.nr_level = level
                    self.bump_revision()
            case SetNBLevel(level=level, receiver=rx):
                await _r.set_nb_level(level, receiver=rx)
                if self._radio_state:
                    target = (
                        self._radio_state.sub if rx != 0 else self._radio_state.main
                    )
                    target.nb_level = level
                    self.bump_revision()
            case SetAutoNotch(on=on, receiver=rx):
                await _r.set_auto_notch(on, receiver=rx)
                if self._radio_state:
                    target = (
                        self._radio_state.sub if rx != 0 else self._radio_state.main
                    )
                    target.auto_notch = on
                    self.bump_revision()
            case SetManualNotch(on=on, receiver=rx):
                await _r.set_manual_notch(on, receiver=rx)
                if self._radio_state:
                    target = (
                        self._radio_state.sub if rx != 0 else self._radio_state.main
                    )
                    target.manual_notch = on
                    self.bump_revision()
            case SetNotchFilter(level=level):
                await _r.set_notch_filter(level)
                if self._radio_state:
                    self._radio_state.notch_filter = level
                    self.bump_revision()
            case SetAgcTimeConstant(value=value, receiver=rx):
                await _r.set_agc_time_constant(value, receiver=rx)
                if self._radio_state:
                    target = (
                        self._radio_state.sub if rx != 0 else self._radio_state.main
                    )
                    target.agc_time_constant = value
                    self.bump_revision()
            case SetCwPitch(value=value):
                await _r.set_cw_pitch(value)
                if self._radio_state:
                    self._radio_state.cw_pitch = value
                    self.bump_revision()
            case SetKeySpeed(speed=speed):
                await _r.set_key_speed(speed)
                if self._radio_state:
                    self._radio_state.key_speed = speed
                    self.bump_revision()
            case SetBreakIn(mode=mode):
                await _r.set_break_in(mode)
                if self._radio_state:
                    self._radio_state.break_in = mode
                    self.bump_revision()
            case SetApf(mode=mode, receiver=rx):
                self._ensure_receiver_supported(rx, operation="set_apf")
                await _r.set_audio_peak_filter(mode, receiver=rx)
                if self._radio_state:
                    target = (
                        self._radio_state.sub if rx != 0 else self._radio_state.main
                    )
                    target.apf_type_level = mode
                    self.bump_revision()
            case SetTwinPeak(on=on, receiver=rx):
                self._ensure_receiver_supported(rx, operation="set_twin_peak")
                await _r.set_twin_peak_filter(on, receiver=rx)
                if self._radio_state:
                    target = (
                        self._radio_state.sub if rx != 0 else self._radio_state.main
                    )
                    target.twin_peak_filter = on
                    self.bump_revision()
            case SetDriveGain(level=level):
                await _r.set_drive_gain(level)
                if self._radio_state:
                    self._radio_state.drive_gain = level
                    self.bump_revision()
            case ScanStart(scan_type=st):
                await _r.scan_start(mode=st)
                if self._radio_state:
                    self._radio_state.scanning = True
                    self._radio_state.scan_type = st
                    self.bump_revision()
            case ScanStop():
                await _r.scan_stop()
                if self._radio_state:
                    self._radio_state.scanning = False
                    self._radio_state.scan_type = 0
                    self.bump_revision()
            case ScanSetDfSpan(span=span):
                await _r.scan_set_df_span(span)
                if self._radio_state:
                    self.bump_revision()
            case ScanSetResume(mode=resume_mode):
                await _r.scan_set_resume(resume_mode)
                if self._radio_state:
                    self._radio_state.scan_resume_mode = resume_mode & 0x0F
                    self.bump_revision()
            case SetDataMode(mode=mode, receiver=rx):
                self._ensure_receiver_supported(rx, operation="set_data_mode")
                if not 0 <= mode <= 3:
                    raise CommandError(f"set_data_mode mode must be 0-3, got {mode}")
                await radio.set_data_mode(mode, receiver=rx)
                if self._radio_state:
                    target = (
                        self._radio_state.sub if rx != 0 else self._radio_state.main
                    )
                    target.data_mode = mode
                    self.bump_revision()
                if self._on_state_event:
                    self._on_state_event(
                        "data_mode_changed", {"mode": mode, "receiver": rx}
                    )
            case SetMicGain(level=level):
                await _r.set_mic_gain(level)
                if self._radio_state:
                    self._radio_state.mic_gain = level
                    self.bump_revision()
            case SetVox(on=on):
                await _r.set_vox(on)
                if self._radio_state:
                    self._radio_state.vox_on = on
                    self.bump_revision()
            case SetCompressorLevel(level=level):
                await _r.set_compressor_level(level)
                if self._radio_state:
                    self._radio_state.compressor_level = level
                    self.bump_revision()
            case SetMonitor(on=on):
                await _r.set_monitor(on)
                if self._radio_state:
                    self._radio_state.monitor_on = on
                    self.bump_revision()
            case SetMonitorGain(level=level):
                await _r.set_monitor_gain(level)
                if self._radio_state:
                    self._radio_state.monitor_gain = level
                    self.bump_revision()
            case SetDialLock(on=on):
                await _r.set_dial_lock(on)
                if self._radio_state:
                    self._radio_state.dial_lock = on
                    self.bump_revision()
            case SetAgc(mode=mode, receiver=rx):
                if CAP_AGC in self._caps:
                    self._ensure_receiver_supported(rx, operation="set_agc")
                    await radio.set_agc(mode, receiver=rx)
                else:
                    # Wire bytes from TOML: set_agc = [0x16, 0x12]
                    await self._send_cmd("set_agc", bytes([mode]), receiver=rx)
                if self._radio_state:
                    target = (
                        self._radio_state.sub if rx != 0 else self._radio_state.main
                    )
                    target.agc = mode
                    self.bump_revision()
                if self._on_state_event:
                    self._on_state_event("agc_changed", {"mode": mode, "receiver": rx})
            case SetRitStatus(on=on):
                await _r.set_rit_status(on)
                if self._radio_state:
                    self._radio_state.rit_on = on
                    self.bump_revision()
                if self._on_state_event:
                    self._on_state_event("rit_changed", {"on": on})
            case SetRitTxStatus(on=on):
                await _r.set_rit_tx_status(on)
                if self._radio_state:
                    self._radio_state.rit_tx = on
                    self.bump_revision()
                if self._on_state_event:
                    self._on_state_event("rit_tx_changed", {"on": on})
            case SetRitFrequency(freq=freq):
                await _r.set_rit_frequency(freq)
                if self._radio_state:
                    self._radio_state.rit_freq = freq
                    self.bump_revision()
                if self._on_state_event:
                    self._on_state_event("rit_freq_changed", {"hz": freq})
            case SetSplit(on=on):
                await _r.set_split(on)
                if self._radio_state:
                    self._radio_state.split = on
                    self.bump_revision()
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
                        # Update local state immediately (don't wait for transceive echo)
                        if self._radio_state:
                            target = self._radio_state.main
                            if target:
                                target.freq = freq
                                target.mode = mode_name
                            self.bump_revision()
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
                # Select the target receiver via the public
                # ``ReceiverBankCapable.select_receiver`` (issue #1172).
                # Pre-#771 this used a MAIN↔SUB swap (0x07 0xB0) as a hack
                # so that LAN audio (MAIN-only at the time) would "follow"
                # the selected receiver.  After #721/#755 introduced
                # Phones L/R Mix + audio_config, audio routing is
                # independent of which receiver is selected, and the swap
                # hack actively corrupted user state on every click
                # (frequencies/modes traded places).  Wave 4-A landed
                # ``select_receiver`` (CI-V 0x07 0xD0/0xD1) on every
                # backend, so the poller now goes through the typed API
                # rather than a raw ``_civ`` write.  Idempotent: re-clicking
                # the active receiver emits no CI-V (the state event still
                # fires so UI listeners can refresh).
                vfo_upper = vfo.upper()
                is_sub = vfo_upper in ("SUB", "B")
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
            case EnableScope(policy=policy):
                if CAP_SCOPE in self._caps:
                    # Defer scope enable during initial fetch to avoid
                    # CI-V packet queue overflow (scope data + fetch).
                    if not self._initial_fetch_done.is_set():
                        if not self._scope_enable_deferred:
                            logger.info(
                                "radio-poller: deferring scope enable until initial fetch completes"
                            )
                            self._scope_enable_deferred = True
                        self._queue.put(EnableScope(policy=policy))
                    else:
                        await radio.enable_scope(policy=policy)
                        logger.info("radio-poller: scope enabled")
                        await self._fetch_scope_controls()
            case DisableScope():
                if CAP_SCOPE in self._caps:
                    await radio.disable_scope()
                    logger.info("radio-poller: scope disabled")
            case SwitchScopeReceiver(receiver=receiver):
                # Fire-and-forget scope receiver select (0x27 0x12)
                self._ensure_receiver_supported(
                    receiver,
                    operation="switch_scope_receiver",
                )
                await self._civ(0x27, sub=0x12, data=bytes([receiver]))
                logger.info(
                    "radio-poller: scope receiver → %s",
                    "SUB" if receiver else "MAIN",
                )
            case SetScopeDuringTx(on=on):
                if CAP_SCOPE in self._caps:
                    await radio.set_scope_during_tx(on)
                    if self._radio_state:
                        self._radio_state.scope_controls.during_tx = on
                    self.bump_revision()
            case SetScopeCenterType(center_type=center_type):
                if CAP_SCOPE in self._caps:
                    await radio.set_scope_center_type(center_type)
                    if self._radio_state:
                        self._radio_state.scope_controls.center_type = center_type
                    self.bump_revision()
            case SetScopeFixedEdge(edge=edge, start_hz=start_hz, end_hz=end_hz):
                if CAP_SCOPE in self._caps:
                    await radio.set_scope_fixed_edge(
                        edge=edge,
                        start_hz=start_hz,
                        end_hz=end_hz,
                    )
            case SetScopeDual(dual=dual):
                if CAP_SCOPE in self._caps:
                    await radio.set_scope_dual(dual)
                    if self._radio_state:
                        self._radio_state.scope_controls.dual = dual
                    self.bump_revision()
            case SetScopeMode(mode=mode):
                if CAP_SCOPE in self._caps:
                    await radio.set_scope_mode(mode)
                    if self._radio_state:
                        self._radio_state.scope_controls.mode = mode
                    self.bump_revision()
            case SetScopeSpan(span=span):
                if CAP_SCOPE in self._caps:
                    await radio.set_scope_span(span)
                    if self._radio_state:
                        self._radio_state.scope_controls.span = span
                    self.bump_revision()
            case SetScopeSpeed(speed=speed):
                if CAP_SCOPE in self._caps:
                    await radio.set_scope_speed(speed)
                    if self._radio_state:
                        self._radio_state.scope_controls.speed = speed
                    self.bump_revision()
            case SetScopeRef(ref=ref):
                if CAP_SCOPE in self._caps:
                    await radio.set_scope_ref(ref)
                    if self._radio_state:
                        self._radio_state.scope_controls.ref_db = float(ref)
                    self.bump_revision()
            case SetScopeHold(on=on):
                if CAP_SCOPE in self._caps:
                    await radio.set_scope_hold(on)
                    if self._radio_state:
                        self._radio_state.scope_controls.hold = on
                    self.bump_revision()
            case SetScopeEdge(edge=edge):
                if CAP_SCOPE in self._caps:
                    await radio.set_scope_edge(edge)
                    if self._radio_state:
                        self._radio_state.scope_controls.edge = edge
                    self.bump_revision()
            case SetScopeVbw(narrow=narrow):
                if CAP_SCOPE in self._caps:
                    await radio.set_scope_vbw(narrow)
                    if self._radio_state:
                        self._radio_state.scope_controls.vbw_narrow = narrow
                    self.bump_revision()
            case SetScopeRbw(rbw=rbw):
                if CAP_SCOPE in self._caps:
                    await radio.set_scope_rbw(rbw)
                    if self._radio_state:
                        self._radio_state.scope_controls.rbw = rbw
                    self.bump_revision()
            case SetPowerstat(on=on):
                if CAP_POWER_CONTROL in self._caps:
                    await radio.set_powerstat(on)
                    # Optimistic update: radio won't respond to polls when off
                    if self._radio_state is not None:
                        self._radio_state.power_on = on
                    self._emit("powerstat_changed", {"power_on": on})
                    self.bump_revision()
                    logger.info("radio-poller: power %s", "ON" if on else "OFF")
            case SetTunerStatus(value=value):
                if CAP_TUNER in self._caps:
                    await radio.set_tuner_status(value)
                    if self._radio_state is not None:
                        self._radio_state.tuner_status = value
                    self._emit("tuner_changed", {"value": value})
                    self.bump_revision()
            case SetAntenna1(on=on):
                # IC-7610: 0x12 0x00 selects ANT1, data byte encodes RX-ANT OFF/ON.
                if CAP_ANTENNA in self._caps:
                    await radio.set_antenna_1(on)
                    if self._radio_state is not None:
                        self._radio_state.tx_antenna = 1
                        self._radio_state.rx_antenna_1 = on
                    self.bump_revision()
            case SetAntenna2(on=on):
                if CAP_ANTENNA in self._caps:
                    await radio.set_antenna_2(on)
                    if self._radio_state is not None:
                        self._radio_state.tx_antenna = 2
                        self._radio_state.rx_antenna_2 = on
                    self.bump_revision()
            case SetRxAntennaAnt1(on=on):
                # IC-7610 RX-ANT is encoded as data byte on 0x12 0x00.
                # WARNING: This selects ANT1 as TX.
                if CAP_ANTENNA in self._caps:
                    await radio.set_rx_antenna_ant1(on)
                    if self._radio_state is not None:
                        self._radio_state.tx_antenna = 1
                        self._radio_state.rx_antenna_1 = on
                    self.bump_revision()
            case SetRxAntennaAnt2(on=on):
                # IC-7610 RX-ANT is encoded as data byte on 0x12 0x01.
                # WARNING: This selects ANT2 as TX.
                if CAP_ANTENNA in self._caps:
                    await radio.set_rx_antenna_ant2(on)
                    if self._radio_state is not None:
                        self._radio_state.tx_antenna = 2
                        self._radio_state.rx_antenna_2 = on
                    self.bump_revision()
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
                    self.bump_revision()
            case SetTsqlFreq(freq_hz=freq, receiver=rx):
                self._ensure_receiver_supported(rx, operation="set_tsql_freq")
                if CAP_TSQL in self._caps:
                    await radio.set_tsql_freq(freq, receiver=rx)
                if self._radio_state:
                    target = (
                        self._radio_state.sub if rx != 0 else self._radio_state.main
                    )
                    target.tsql_freq = freq
                    self.bump_revision()
            case SetMainSubTracking(on=on):
                if CAP_MAIN_SUB_TRACKING in self._caps:
                    await radio.set_main_sub_tracking(on)
                if self._radio_state:
                    self._radio_state.main_sub_tracking = on
                    self.bump_revision()
            case SetSsbTxBandwidth(value=value):
                if CAP_SSB_TX_BW in self._caps:
                    await radio.set_ssb_tx_bandwidth(value)
                if self._radio_state:
                    self._radio_state.ssb_tx_bandwidth = value
                    self.bump_revision()
            case SetManualNotchWidth(value=value, receiver=rx):
                self._ensure_receiver_supported(rx, operation="set_manual_notch_width")
                if CAP_NOTCH in self._caps:
                    await radio.set_manual_notch_width(value, receiver=rx)
                self.bump_revision()
            case SetBreakInDelay(level=level):
                if CAP_BREAK_IN in self._caps:
                    await radio.set_break_in_delay(level)
                if self._radio_state:
                    self._radio_state.break_in_delay = level
                    self.bump_revision()
            case SetVoxGain(level=level):
                if CAP_VOX in self._caps:
                    await radio.set_vox_gain(level)
                if self._radio_state:
                    self._radio_state.vox_gain = level
                    self.bump_revision()
            case SetAntiVoxGain(level=level):
                if CAP_VOX in self._caps:
                    await radio.set_anti_vox_gain(level)
                if self._radio_state:
                    self._radio_state.anti_vox_gain = level
                    self.bump_revision()
            case SetVoxDelay(level=level):
                if CAP_VOX in self._caps:
                    await radio.set_vox_delay(level)
                if self._radio_state:
                    self._radio_state.vox_delay = level
                    self.bump_revision()
            case SetNbDepth(level=level, receiver=rx):
                self._ensure_receiver_supported(rx, operation="set_nb_depth")
                if CAP_NB in self._caps:
                    await radio.set_nb_depth(level, receiver=rx)
                if self._radio_state:
                    self._radio_state.nb_depth = level
                    self.bump_revision()
            case SetNbWidth(level=level, receiver=rx):
                self._ensure_receiver_supported(rx, operation="set_nb_width")
                if CAP_NB in self._caps:
                    await radio.set_nb_width(level, receiver=rx)
                if self._radio_state:
                    self._radio_state.nb_width = level
                    self.bump_revision()
            case SetDashRatio(value=value):
                if CAP_CW in self._caps:
                    await radio.set_dash_ratio(value)
                if self._radio_state:
                    self._radio_state.dash_ratio = value
                    self.bump_revision()
            case SetRepeaterTone(on=on, receiver=rx):
                self._ensure_receiver_supported(rx, operation="set_repeater_tone")
                if CAP_REPEATER_TONE in self._caps:
                    await radio.set_repeater_tone(on, receiver=rx)
                if self._radio_state:
                    target = (
                        self._radio_state.sub if rx != 0 else self._radio_state.main
                    )
                    target.repeater_tone = on
                    self.bump_revision()
            case SetRepeaterTsql(on=on, receiver=rx):
                self._ensure_receiver_supported(rx, operation="set_repeater_tsql")
                if CAP_TSQL in self._caps:
                    await radio.set_repeater_tsql(on, receiver=rx)
                if self._radio_state:
                    target = (
                        self._radio_state.sub if rx != 0 else self._radio_state.main
                    )
                    target.repeater_tsql = on
                    self.bump_revision()
            case SetRxAntenna(antenna=antenna, on=on):
                if CAP_RX_ANTENNA in self._caps:
                    if antenna == 1:
                        await radio.set_rx_antenna_ant1(on)
                    else:
                        await radio.set_rx_antenna_ant2(on)
                self.bump_revision()
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
            case SetDataOffModInput(source=source):
                if CAP_DATA_MODE in self._caps:
                    await radio.set_data_off_mod_input(source)
            case SetData1ModInput(source=source):
                if CAP_DATA_MODE in self._caps:
                    await radio.set_data1_mod_input(source)
            case SetData2ModInput(source=source):
                if CAP_DATA_MODE in self._caps:
                    await radio.set_data2_mod_input(source)
            case SetData3ModInput(source=source):
                if CAP_DATA_MODE in self._caps:
                    await radio.set_data3_mod_input(source)
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
                    self.bump_revision()
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
                    self.bump_revision()
            case SetTuningStep(step=step):
                await _r.set_tuning_step(step)
                if self._radio_state:
                    self._radio_state.tuning_step = step
                    self.bump_revision()
            case SetXfcStatus(on=on):
                await _r.set_xfc_status(on)
            case SetTxFreqMonitor(on=on):
                await _r.set_tx_freq_monitor(on)
                if self._radio_state:
                    self._radio_state.tx_freq_monitor = on
                    self.bump_revision()
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
                        self.bump_revision()
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

    async def _send_query(self) -> None:
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
            await self._civ(cmd_byte, sub=sub_byte, data=b"")
        else:
            if not self._STATE_QUERIES:
                self._poll_index += 1
                return
            state_idx = (self._poll_index // 2) % len(self._STATE_QUERIES)
            cmd_byte, sub_byte, receiver = self._STATE_QUERIES[state_idx]
            await self._send_one_state_query(cmd_byte, sub_byte, receiver)
        self._poll_index += 1

    # Issue #715: unselected-slot slow-poll cycle.
    # Rate-limit and debounce thresholds are conservative — this cycle
    # is a correctness feature (populate vfo_b) not a throughput one.
    _UNSELECTED_SLOT_INTERVAL: float = 5.0  # sec between refreshes per rx
    _UNSELECTED_SLOT_DEBOUNCE: float = 0.5  # sec after last user freq/mode write

    def _unselected_slot_gate(self, receiver: int) -> bool:
        """Return True iff it is safe to read the unselected slot on *receiver*."""
        if self._radio_state is None or getattr(self._radio_state, "ptt", False):
            return False
        if self._queue.has_commands:
            return False
        now = time.monotonic()
        if (now - self._last_user_write_ts) < self._UNSELECTED_SLOT_DEBOUNCE:
            return False
        last = self._last_unselected_poll.get(receiver, 0.0)
        if (now - last) < self._UNSELECTED_SLOT_INTERVAL:
            return False
        if not self._profile.supports_receiver(receiver):
            return False
        # Need a true A/B-within-receiver swap primitive. swap_main_sub_code
        # is NOT a fallback — on IC-7610 the 0x07 0xB0 byte toggles MAIN↔SUB,
        # not A↔B inside a receiver, which would flip the radio's active-RX
        # state on every slow-poll cycle.
        if self._profile.swap_ab_code is None:
            return False
        return True

    async def _poll_unselected_slot(self, receiver: int) -> None:
        """Read freq+mode of the inactive VFO slot on *receiver*.

        Uses a transient ``host._vfo_slot_override`` flag so ``_civ_rx.py``
        routes the 0x03/0x04 responses to the opposite slot.  On dual-RX
        profiles the target receiver is selected first via ``0x07`` and
        restored after.  Swap → query → swap-back keeps the radio's
        active-slot state unchanged for the user.
        """
        if not self._unselected_slot_gate(receiver):
            return
        rs = self._radio_state
        assert rs is not None  # gate guarantees
        rx_name = "MAIN" if receiver == 0 else "SUB"
        rx_state = rs.receiver(rx_name)
        target_slot = "B" if rx_state.active_slot == "A" else "A"
        swap_code = self._profile.swap_ab_code
        assert swap_code is not None  # gate guarantees (see _unselected_slot_gate)
        # Pre-select MAIN/SUB on dual-RX rigs so the swap hits the intended
        # receiver.  Restore after the read.
        pre_switched = False
        pre_code: int | None = None
        post_code: int | None = None
        if self._profile.receiver_count > 1:
            current = self._current_active()
            if rx_name != current:
                if rx_name == "SUB" and self._profile.vfo_sub_code is not None:
                    pre_code = self._profile.vfo_sub_code
                    post_code = self._profile.vfo_main_code
                elif rx_name == "MAIN" and self._profile.vfo_main_code is not None:
                    pre_code = self._profile.vfo_main_code
                    post_code = self._profile.vfo_sub_code
                if pre_code is None or post_code is None:
                    return  # profile cannot switch — skip
        override_map = getattr(self._radio, "_vfo_slot_override", None)
        if not isinstance(override_map, dict):
            override_map = {}
            try:
                self._radio._vfo_slot_override = override_map  # type: ignore[attr-defined]
            except AttributeError:
                return  # host refuses attribute — skip silently
        try:
            if pre_code is not None:
                await self._civ(0x07, data=bytes([pre_code]))
                await asyncio.sleep(self._adaptive_gap())
                pre_switched = True
            # Swap A/B within the selected receiver.
            await self._civ(0x07, data=bytes([swap_code]))
            await asyncio.sleep(self._adaptive_gap())
            # Responses for the queries below must route to the opposite slot.
            override_map[rx_name] = target_slot
            await self._civ(0x03, data=b"")
            await asyncio.sleep(self._adaptive_gap())
            await self._civ(0x04, data=b"")
            # Give the CI-V RX pump a moment to drain responses before we
            # swap back (the override stays set until *after* swap-back
            # so any late 0x03/0x04 response still routes to the
            # opposite slot rather than polluting vfo_a via the property
            # setter).
            await asyncio.sleep(self._adaptive_gap() * 2)
        finally:
            # Always try to swap back so the radio ends in the original
            # slot even when a query errored.  Override is cleared after
            # the swap-back send + a gap to cover in-flight responses.
            try:
                await self._civ(0x07, data=bytes([swap_code]))
            except Exception:
                logger.warning(
                    "radio-poller: failed to restore VFO A/B on receiver=%d",
                    receiver,
                    exc_info=True,
                )
            await asyncio.sleep(self._adaptive_gap())
            override_map.pop(rx_name, None)
            if pre_switched and post_code is not None:
                try:
                    await self._civ(0x07, data=bytes([post_code]))
                except Exception:
                    logger.warning(
                        "radio-poller: failed to restore MAIN/SUB selection",
                        exc_info=True,
                    )
        self._last_unselected_poll[receiver] = time.monotonic()

    def _emit(self, name: str, data: dict[str, Any]) -> None:
        if self._on_state_event is not None:
            self._on_state_event(name, data)
