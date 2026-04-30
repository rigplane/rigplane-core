"""CI-V receive pump and event dispatch for IcomRadio."""

from __future__ import annotations

import asyncio
import logging
import time
from typing import TYPE_CHECKING, Any

from icom_lan.civ import CivEvent, CivEventType, iter_civ_frames, request_key_from_frame
from icom_lan.commander import IcomCommander, Priority
from icom_lan.commands import (
    CONTROLLER_ADDR,
    parse_bool_response,
    parse_civ_frame,
    parse_frequency_response,
    parse_level_response,
    parse_mode_response,
    parse_scope_center_type_response,
    parse_scope_during_tx_response,
    parse_scope_edge_response,
    parse_scope_fixed_edge_response,
    parse_scope_hold_response,
    parse_scope_main_sub_response,
    parse_scope_mode_response,
    parse_scope_rbw_response,
    parse_scope_ref_response,
    parse_scope_single_dual_response,
    parse_scope_span_response,
    parse_scope_speed_response,
    parse_scope_vbw_response,
)
from icom_lan.exceptions import ConnectionError, TimeoutError
from icom_lan.scope import ScopeFrame
from icom_lan.types import CivFrame

if TYPE_CHECKING:
    from ._runtime_protocols import CivRuntimeHost
    from icom_lan.radio_state import RadioState

logger = logging.getLogger(__name__)

CIV_HEADER_SIZE = 0x15
_SCOPE_BACKLOG_SHED_THRESHOLD = 256
_SCOPE_BACKLOG_KEEP_LATEST = 64

__all__ = ["CivRuntime", "CIV_HEADER_SIZE"]

_CMD14_RECEIVER_LEVEL_FIELDS = {
    0x01: "af_level",
    0x02: "rf_gain",
    0x03: "squelch",
    0x05: "apf_type_level",
    0x06: "nr_level",
    0x07: "pbt_inner",
    0x08: "pbt_outer",
    0x12: "nb_level",
    0x13: "digisel_shift",
}

_CMD14_GLOBAL_LEVEL_FIELDS = {
    0x0B: "mic_gain",
    0x0D: "notch_filter",
    0x0E: "compressor_level",
    0x0F: "break_in_delay",
    0x14: "drive_gain",
    0x15: "monitor_gain",
    0x16: "vox_gain",
    0x17: "anti_vox_gain",
}

_CMD16_RECEIVER_BOOL_FIELDS = {
    0x22: "nb",
    0x40: "nr",
    0x41: "auto_notch",
    0x42: "repeater_tone",
    0x43: "repeater_tsql",
    0x48: "manual_notch",
    0x4E: "digisel",
    0x4F: "twin_peak_filter",
    0x65: "ipplus",
}

_CMD16_RECEIVER_VALUE_FIELDS = {
    0x12: ("agc", 1),
    0x32: ("audio_peak_filter", 1),
    0x56: ("filter_shape", 1),
}

_CMD16_GLOBAL_BOOL_FIELDS = {
    0x44: "compressor_on",
    0x45: "monitor_on",
    0x46: "vox_on",
    0x50: "dial_lock",
    0x5E: "main_sub_tracking",
}

_CMD16_GLOBAL_VALUE_FIELDS = {
    0x47: ("break_in", 1),
    0x58: ("ssb_tx_bandwidth", 1),
}

# Sub-command to state-change event name for unsolicited 0x16 updates (web/poller).
_CMD16_NOTIFY_EVENTS = {
    0x12: "agc_changed",
    0x32: "audio_peak_filter_changed",
    0x56: "filter_shape_changed",
    0x41: "auto_notch_changed",
    0x42: "repeater_tone_changed",
    0x43: "repeater_tsql_changed",
    0x48: "manual_notch_changed",
    0x4F: "twin_peak_filter_changed",
    0x44: "compressor_changed",
    0x45: "monitor_changed",
    0x46: "vox_changed",
    0x50: "dial_lock_changed",
    0x47: "break_in_changed",
    0x58: "ssb_tx_bandwidth_changed",
    0x65: "ipplus_changed",
}

_CMD1A_CTL_MEM_LEVEL_FIELDS = {
    b"\x00\x70": ("ref_adjust", 2),
    b"\x02\x28": ("dash_ratio", 1),
    b"\x02\x90": ("nb_depth", 1),
    b"\x02\x91": ("nb_width", 2),
    b"\x02\x92": ("vox_delay", 1),
}

# CI-V data watchdog (wfview icomudpcivdata::watchdog)
# If no CI-V data for this long, send open_close to restart the stream.
_CIV_DATA_WATCHDOG_TIMEOUT = 2.0  # seconds (wfview: 2000ms)
_CIV_DATA_WATCHDOG_RETRY = 0.1  # retry interval (wfview: 100ms via startCivDataTimer)


class CivRuntime:
    """Composed CI-V runtime; holds logic moved from _CivRxMixin (Phase 3).

    Uses self._host (CivRuntimeHost) for all state; host provides connect,
    disconnect, soft_reconnect, _force_cleanup_civ, _send_open_close.
    """

    def __init__(self, host: "CivRuntimeHost") -> None:
        self._host = host
        # Detached reconnect task spawned from the watchdog escalation path.
        # Tracked here so stop_data_watchdog() can cancel it and prevent a
        # late soft_reconnect from firing after an explicit disconnect
        # (Codex P1 on PR #851).
        self._reconnect_task: asyncio.Task[None] | None = None

    # ------------------------------------------------------------------
    # Public API (design doc)
    # ------------------------------------------------------------------

    def start_pump(self) -> None:
        """Start always-on CI-V receive pump."""
        if self._host._civ_rx_task is None or self._host._civ_rx_task.done():
            self._host._civ_rx_task = asyncio.create_task(
                self._civ_rx_loop(self._host._civ_epoch)
            )

    async def stop_pump(self) -> None:
        """Stop CI-V receive pump and fail pending request futures."""
        self._host._civ_request_tracker.fail_all(
            ConnectionError("CI-V RX pump stopped")
        )
        if self._host._civ_rx_task is not None and not self._host._civ_rx_task.done():
            self._host._civ_rx_task.cancel()
            try:
                await self._host._civ_rx_task
            except asyncio.CancelledError:
                pass
        self._host._civ_rx_task = None

    def start_data_watchdog(self) -> None:
        """Start CI-V data watchdog task."""
        task = self._host._civ_data_watchdog_task
        if task is not None and not task.done():
            return
        self._host._civ_data_watchdog_task = asyncio.create_task(
            self._civ_data_watchdog_loop(), name="civ-data-watchdog"
        )
        logger.info("civ-data-watchdog: started")

    async def stop_data_watchdog(self) -> None:
        """Stop CI-V data watchdog and any in-flight detached reconnect task.

        The reconnect helper (spawned from the escalation path as a detached
        task) is cancelled alongside the watchdog loop so an explicit
        disconnect during the cooldown cannot be undone by a late
        soft_reconnect.
        """
        task = getattr(self._host, "_civ_data_watchdog_task", None)
        if task is not None and not task.done():
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
        self._host._civ_data_watchdog_task = None

        rc_task = self._reconnect_task
        if rc_task is not None and not rc_task.done():
            rc_task.cancel()
            try:
                await rc_task
            except asyncio.CancelledError:
                pass
        self._reconnect_task = None

    def advance_generation(self, reason: str) -> None:
        """Advance CI-V request generation and fail stale waiters."""
        self._host._civ_epoch = self._host._civ_request_tracker.advance_generation(
            ConnectionError(f"CI-V generation advanced: {reason}")
        )

    async def execute_civ_raw(
        self,
        civ_frame: bytes,
        wait_response: bool = True,
        deadline_monotonic: "float | None" = None,
    ) -> "CivFrame | None":
        """Execute one CI-V command via request tracker (public API)."""
        return await self._execute_civ_raw(
            civ_frame,
            wait_response=wait_response,
            deadline_monotonic=deadline_monotonic,
        )

    async def send_civ_raw(
        self,
        civ_frame: bytes,
        *,
        priority: Priority = Priority.NORMAL,
        key: "str | None" = None,
        dedupe: bool = False,
        wait_response: bool = True,
        timeout: "float | None" = None,
    ) -> "CivFrame | None":
        """Enqueue a CI-V command and wait for its response (public API)."""
        return await self._send_civ_raw(
            civ_frame,
            priority=priority,
            key=key,
            dedupe=dedupe,
            wait_response=wait_response,
            timeout=timeout,
        )

    def start_worker(self) -> None:
        """Start serialized CI-V commander."""
        self._ensure_civ_runtime()
        self.start_pump()
        if self._host._commander is None:
            self._host._commander = IcomCommander(
                self.execute_civ_raw,
                min_interval=self._host._civ_min_interval,
            )
        self._host._commander.start()

    async def stop_worker(self) -> None:
        """Stop CI-V commander and fail pending commands."""
        if self._host._commander is not None:
            await self._host._commander.stop()

    # ------------------------------------------------------------------
    # Generation / stale-waiter housekeeping (from mixin)
    # ------------------------------------------------------------------

    def _cleanup_stale_civ_waiters(self) -> None:
        """Run periodic stale waiter GC on request tracker."""
        now = time.monotonic()
        if (
            now - self._host._civ_last_waiter_gc_monotonic
            < self._host._civ_waiter_ttl_gc_interval
        ):
            return
        cleaned = self._host._civ_request_tracker.cleanup_stale(now_monotonic=now)
        self._host._civ_last_waiter_gc_monotonic = now
        if cleaned:
            logger.debug("Cleaned %d stale CI-V waiter(s)", cleaned)

    # ------------------------------------------------------------------
    # CI-V data watchdog loop (from mixin)
    # ------------------------------------------------------------------

    async def _civ_data_watchdog_loop(self) -> None:
        """Monitor CI-V data flow; recover with open_close then soft_reconnect.

        OpenClose is retried patiently (wfview icomudpcivdata.cpp:31 pattern —
        never escalates). icom-lan keeps a safety-net deadline at
        _OPENCLOSE_DEADLINE before handing recovery off to a detached
        reconnect task. The task is detached so its cooldown sleep survives
        the watchdog loop exiting.
        """
        _OPENCLOSE_DEADLINE = 60.0
        _MAX_RECONNECTS = 3
        _RECONNECT_BACKOFF = (45.0, 60.0, 60.0)
        recovering = False
        recovery_start: float = 0.0
        reconnect_count = 0
        try:
            while True:
                await asyncio.sleep(
                    _CIV_DATA_WATCHDOG_RETRY
                    if recovering
                    else _CIV_DATA_WATCHDOG_TIMEOUT / 2
                )

                last = getattr(self._host, "_last_civ_data_received", None)
                if last is None:
                    continue

                idle = time.monotonic() - last
                if idle > _CIV_DATA_WATCHDOG_TIMEOUT:
                    if not recovering:
                        civ_t = getattr(self._host, "_civ_transport", None)
                        rx_count = civ_t.rx_packet_count if civ_t else -1
                        q_size = civ_t._packet_queue.qsize() if civ_t else -1
                        logger.warning(
                            "civ-data-watchdog: no CI-V data for %.1fs, "
                            "requesting data start "
                            "(transport rx_count=%d, queue=%d)",
                            idle,
                            rx_count,
                            q_size,
                        )
                        recovering = True
                        self._host._civ_recovering = True
                        self._host._civ_stream_ready = False
                        recovery_start = time.monotonic()

                    elapsed_recovery = time.monotonic() - recovery_start

                    if elapsed_recovery < _OPENCLOSE_DEADLINE:
                        try:
                            await self._host._send_open_close(open_stream=True)
                        except (ConnectionError, TimeoutError, OSError) as exc:
                            logger.debug(
                                "civ-data-watchdog: open_close failed: %s",
                                exc,
                            )
                        except Exception:
                            logger.warning(
                                "civ-data-watchdog: unexpected error in open_close",
                                exc_info=True,
                            )
                    else:
                        reconnect_count += 1
                        reconnect_pause = _RECONNECT_BACKOFF[
                            min(reconnect_count - 1, len(_RECONNECT_BACKOFF) - 1)
                        ]
                        # Recovery runs in a detached task so its cooldown
                        # sleep is honored even when this watchdog task
                        # exits. The prior inline `await stop_data_watchdog()`
                        # cancelled this task and caused the cooldown to be
                        # skipped entirely (self-cancel bug).
                        if reconnect_count > _MAX_RECONNECTS:
                            logger.warning(
                                "civ-data-watchdog: %d soft reconnects failed, "
                                "attempting full reconnect",
                                reconnect_count - 1,
                            )
                            self._reconnect_task = asyncio.create_task(
                                self._watchdog_full_reconnect(reconnect_pause),
                                name="civ-watchdog-full-reconnect",
                            )
                            return
                        logger.warning(
                            "civ-data-watchdog: OpenClose failed for %.1fs, "
                            "triggering soft_reconnect (%d/%d), cooldown=%.0fs",
                            elapsed_recovery,
                            reconnect_count,
                            _MAX_RECONNECTS,
                            reconnect_pause,
                        )
                        self._reconnect_task = asyncio.create_task(
                            self._watchdog_soft_reconnect(reconnect_pause),
                            name="civ-watchdog-soft-reconnect",
                        )
                        return
                else:
                    if recovering:
                        logger.info("civ-data-watchdog: CI-V data resumed")
                        recovering = False
                        self._host._civ_recovering = False
                        self._host._civ_stream_ready = True
        except asyncio.CancelledError:
            pass

    async def _watchdog_soft_reconnect(self, cooldown: float) -> None:
        """Detached recovery: force_cleanup → sleep(cooldown) → soft_reconnect.

        Runs outside the watchdog loop so the cooldown sleep survives the
        watchdog task exiting. Falls back to full reconnect on failure.
        """
        try:
            await self._host._force_cleanup_civ()
            await asyncio.sleep(cooldown)
            await self._host.soft_reconnect()
        except (ConnectionError, TimeoutError, OSError):
            logger.error(
                "civ-data-watchdog: soft_reconnect failed, "
                "falling back to full reconnect",
                exc_info=True,
            )
            try:
                await self._host.disconnect()
                await asyncio.sleep(cooldown)
                await self._host.connect()
            except (ConnectionError, TimeoutError, OSError):
                logger.error(
                    "civ-data-watchdog: full reconnect also failed",
                    exc_info=True,
                )
            except Exception:
                logger.error(
                    "civ-data-watchdog: unexpected error in full reconnect fallback",
                    exc_info=True,
                )
        except Exception:
            logger.error(
                "civ-data-watchdog: unexpected error in soft_reconnect",
                exc_info=True,
            )

    async def _watchdog_full_reconnect(self, cooldown: float) -> None:
        """Detached full reconnect after max soft_reconnect attempts exceeded."""
        try:
            await self._host._force_cleanup_civ()
            await self._host.disconnect()
            await asyncio.sleep(cooldown)
            await self._host.connect()
        except (ConnectionError, TimeoutError, OSError):
            logger.error(
                "civ-data-watchdog: full reconnect failed",
                exc_info=True,
            )
        except Exception:
            logger.error(
                "civ-data-watchdog: unexpected error in full reconnect",
                exc_info=True,
            )

    def _ensure_civ_runtime(self) -> None:
        """Ensure CI-V transport exists (tests may bypass connect())."""
        if self._host._civ_transport is None:
            raise ConnectionError("Not connected to radio")

    # ------------------------------------------------------------------
    # CI-V RX loop + routing (from mixin)
    # ------------------------------------------------------------------

    def _is_scope_chunk_packet(self, pkt: bytes) -> bool:
        """Return True when a UDP packet carries only scope chunk CI-V frame(s)."""
        if len(pkt) <= CIV_HEADER_SIZE:
            return False
        payload = pkt[CIV_HEADER_SIZE:]
        saw_frame = False
        for frame_bytes in iter_civ_frames(payload):
            saw_frame = True
            try:
                frame = parse_civ_frame(frame_bytes)
            except ValueError:
                return False
            if not (
                frame.command == 0x27 and frame.sub == 0x00 and len(frame.data) >= 3
            ):
                return False
        return saw_frame

    def _shed_scope_backlog(self, packets: list[bytes]) -> list[bytes]:
        """Drop stale scope-only packets when RX backlog gets dangerously large.

        Preserve all non-scope packets and keep only the newest scope packets.
        This intentionally favors control/state freshness over perfectly complete
        scope frames under overload conditions.
        """
        if len(packets) < _SCOPE_BACKLOG_SHED_THRESHOLD:
            return packets

        scope_packets: list[bytes] = []
        non_scope_packets: list[bytes] = []
        for pkt in packets:
            if self._is_scope_chunk_packet(pkt):
                scope_packets.append(pkt)
            else:
                non_scope_packets.append(pkt)

        if len(scope_packets) <= _SCOPE_BACKLOG_KEEP_LATEST:
            return packets

        kept_scope = scope_packets[-_SCOPE_BACKLOG_KEEP_LATEST:]
        dropped = len(scope_packets) - len(kept_scope)
        logger.warning(
            "civ-rx: shedding %d stale scope packet(s) under backlog pressure "
            "(batch=%d, kept_scope=%d, non_scope=%d)",
            dropped,
            len(packets),
            len(kept_scope),
            len(non_scope_packets),
        )
        return non_scope_packets + kept_scope

    async def _civ_rx_loop(self, generation: int) -> None:
        """Continuously consume CI-V transport packets and route events."""
        assert self._host._civ_transport is not None
        self._host._last_civ_data_received = time.monotonic()
        try:
            while self._host._civ_transport is not None:
                try:
                    packet = await self._host._civ_transport.receive_packet(timeout=0.2)
                except asyncio.TimeoutError:
                    self._cleanup_stale_civ_waiters()
                    continue

                packets = [packet]
                queue = getattr(self._host._civ_transport, "_packet_queue", None)
                if queue is not None:
                    while not queue.empty():
                        try:
                            packets.append(queue.get_nowait())
                        except asyncio.QueueEmpty:
                            break

                packets = self._shed_scope_backlog(packets)

                self._host._last_civ_data_received = time.monotonic()
                self._host._civ_stream_ready = True
                self._host._civ_recovering = False

                for pkt in packets:
                    if len(pkt) <= CIV_HEADER_SIZE:
                        continue
                    payload = pkt[CIV_HEADER_SIZE:]
                    for frame_bytes in iter_civ_frames(payload):
                        try:
                            frame = parse_civ_frame(frame_bytes)
                        except ValueError:
                            continue
                        try:
                            await self._route_civ_frame(frame, generation=generation)
                        except Exception:
                            logger.exception(
                                "Unhandled exception while routing CI-V frame"
                            )
                self._cleanup_stale_civ_waiters()
        except asyncio.CancelledError:
            pass

    async def _route_civ_frame(self, frame: CivFrame, *, generation: int) -> None:
        """Route one parsed CI-V frame into command/scope event paths."""
        if frame.from_addr != self._host._radio_addr:
            return
        if frame.to_addr not in (CONTROLLER_ADDR, 0x00):
            return
        if frame.command != 0x27:
            logger.debug(
                "civ-rx: cmd=0x%02X sub=0x%02X to=0x%02X data=%s",
                frame.command,
                frame.sub or 0,
                frame.to_addr,
                frame.data.hex() if frame.data else "",
            )

        if frame.command == 0x27 and frame.sub == 0x00 and len(frame.data) >= 3:
            receiver = frame.data[0]
            self._host._scope_activity_counter += 1
            self._host._scope_activity_event.set()
            self._publish_civ_event(
                CivEvent(
                    type=CivEventType.SCOPE_CHUNK,
                    frame=frame,
                    receiver=receiver,
                )
            )
            scope_frame = self._host._scope_assembler.feed(frame.data[1:], receiver)
            if scope_frame is not None:
                self._publish_scope_frame(scope_frame)
            return

        if frame.command == 0xFB:
            event = CivEvent(type=CivEventType.ACK, frame=frame)
        elif frame.command == 0xFA:
            event = CivEvent(type=CivEventType.NAK, frame=frame)
        else:
            event = CivEvent(type=CivEventType.RESPONSE, frame=frame)
            self._update_state_cache_from_frame(frame)
        self._publish_civ_event(event)
        self._host._civ_request_tracker.resolve(event, generation=generation)

    def _update_state_cache_from_frame(self, frame: CivFrame) -> None:
        """Best-effort update of state cache from an incoming CI-V frame."""
        host = self._host
        _rs = getattr(host, "_radio_state", None)
        _rx = None
        if _rs is not None:
            if frame.receiver is not None:
                _rx_name = "MAIN" if frame.receiver == 0x00 else "SUB"
            else:
                _rx_name = _rs.active
            _rx = _rs.receiver(_rx_name)
        try:
            if frame.command in (0x03, 0x00):
                # Frequency: 0x03 = response to GET, 0x00 = unsolicited (e.g. VFO knob)
                freq = parse_frequency_response(frame)
                host._state_cache.update_freq(freq)
                host._last_freq_hz = freq
            elif frame.command in (0x04, 0x01):
                mode_val, filt = parse_mode_response(frame)
                host._state_cache.update_mode(mode_val.name, filt)
                host._last_mode = mode_val
                if filt is not None:
                    host._filter_width = filt
            elif frame.command == 0x1C and frame.sub == 0x00 and frame.data:
                host._state_cache.update_ptt(bool(frame.data[0]))
            elif frame.command == 0x18 and frame.data and len(frame.data) == 1:
                # Power status response (0x18): 0x00=off, 0x01=on
                power_on = bool(frame.data[0])
                host._state_cache.update_powerstat(power_on)
                if _rs is not None:
                    _rs.power_on = power_on
                self._notify_change("powerstat_changed", {"power_on": power_on})
            elif frame.command == 0x11 and frame.data and _rx is not None:
                # Attenuator response (plain CI-V, no cmd29)
                val = frame.data[0]
                _rx.att = ((val >> 4) & 0x0F) * 10 + (val & 0x0F)
            elif frame.command == 0x12 and frame.data and _rs is not None:
                # Antenna select / RX-ANT state (plain CI-V)
                # IC-7610 CI-V reference:
                #   0x12 0x00 <00|01> = select ANT1, data = RX ANT OFF/ON
                #   0x12 0x01 <00|01> = select ANT2, data = RX ANT OFF/ON
                # NOTE: This command is NOT safe to poll.
                sub = frame.sub or 0
                val = bool(frame.data[0])
                if sub == 0x00:
                    _rs.tx_antenna = 1
                    _rs.rx_antenna_1 = val
                elif sub == 0x01:
                    _rs.tx_antenna = 2
                    _rs.rx_antenna_2 = val
            elif (
                frame.command == 0x14
                and frame.data
                and len(frame.data) >= 2
                and _rx is not None
            ):
                # Level response (plain CI-V, no cmd29)
                sub = frame.sub or 0
                raw = ((frame.data[0] >> 4) & 0x0F) * 100 + (frame.data[0] & 0x0F) * 10
                if len(frame.data) > 1:
                    raw += (frame.data[1] >> 4) & 0x0F
                if sub == 0x01:
                    _rx.af_level = raw
                elif sub == 0x02:
                    _rx.rf_gain = raw
                elif sub == 0x03:
                    _rx.squelch = raw
                elif sub == 0x06:
                    _rx.nr_level = raw
                elif sub == 0x12:
                    _rx.nb_level = raw
            elif frame.command == 0x16:
                data = frame.data
                sub = frame.sub or 0
                if data and sub == 0x02 and _rx is not None:
                    # Preamp response (plain CI-V)
                    _rx.preamp = data[0]
                elif data and sub == 0x12 and _rx is not None:
                    # AGC mode response (plain CI-V)
                    _rx.agc = data[0]
                elif data and sub == 0x22 and _rx is not None:
                    # NB on/off (plain CI-V)
                    _rx.nb = bool(data[0])
                    self._notify_change("nb_changed", {"on": bool(data[0])})
                elif data and sub == 0x40 and _rx is not None:
                    # NR on/off (plain CI-V)
                    _rx.nr = bool(data[0])
                    self._notify_change("nr_changed", {"on": bool(data[0])})
                elif data and sub == 0x32:
                    host._state_cache.filter_width = ((data[0] >> 4) & 0x0F) * 10 + (
                        data[0] & 0x0F
                    )
                elif data and sub == 0x65:
                    self._notify_change("ipplus_changed", {"on": bool(data[0])})
            elif (
                frame.command == 0x1A
                and frame.sub == 0x03
                and frame.data
                and _rx is not None
            ):
                from icom_lan.commands import _bcd_decode_value, filter_index_to_hz

                filter_index = _bcd_decode_value(frame.data)
                profile = getattr(host, "_profile", None)
                if (
                    profile is not None
                    and getattr(profile, "filter_width_encoding", None)
                    == "segmented_bcd_index"
                ):
                    rule = profile.resolve_filter_rule(
                        getattr(_rx, "mode", None),
                        data_mode=int(getattr(_rx, "data_mode", 0) or 0),
                    )
                    if rule is not None and rule.segments:
                        decoded_width = filter_index_to_hz(
                            filter_index, segments=rule.segments
                        )
                    else:
                        decoded_width = filter_index
                else:
                    decoded_width = filter_index
                _rx.filter_width = decoded_width
                if getattr(frame, "receiver", None) in (None, 0x00):
                    host._state_cache.filter_width = decoded_width
            elif frame.command == 0x07 and frame.data and len(frame.data) >= 2:
                sub07 = frame.data[0]
                val07 = frame.data[1]
                if sub07 == 0xD2:
                    host._last_vfo = "SUB" if val07 else "MAIN"
                elif sub07 == 0xC2:
                    host._state_cache.dual_watch = bool(val07)
                    self._notify_change("dual_watch_changed", {"on": bool(val07)})
            elif frame.command == 0x21:
                if frame.sub == 0x00 and len(frame.data) >= 3:
                    from icom_lan.commands import parse_rit_frequency_response

                    hz = parse_rit_frequency_response(frame.data)
                    self._notify_change("rit_freq_changed", {"hz": hz})
                elif frame.sub == 0x01 and frame.data:
                    self._notify_change("rit_changed", {"on": bool(frame.data[0])})
                elif frame.sub == 0x02 and frame.data:
                    self._notify_change("rit_tx_changed", {"on": bool(frame.data[0])})
            elif frame.command == 0x0E and frame.data:
                # Only 0x00 (stop) and 0x01-0x23 (start variants) change scan state.
                # 0xA1-0xA7 (ΔF span) and 0xD0-0xD3 (resume mode) are config-only.
                sub_0e = frame.data[0]
                if sub_0e <= 0x23:
                    self._notify_change("scanning_changed", {"on": bool(sub_0e)})
            elif frame.command == 0x10 and frame.data:
                b = frame.data[0]
                step = ((b >> 4) & 0x0F) * 10 + (b & 0x0F)
                self._notify_change("tuning_step_changed", {"step": step})
        except (ValueError, IndexError, KeyError, AttributeError, TypeError) as exc:
            logger.debug("civ-rx: cache update failed: %s", exc)
        except Exception:
            logger.warning("civ-rx: unexpected error in cache update", exc_info=True)
        self._update_radio_state_from_frame(frame)

    def _update_radio_state_from_frame(self, frame: CivFrame) -> None:
        """Update RadioState from a CI-V frame (additive alongside StateCache).

        Top-level dispatch via ``_RADIO_STATE_HANDLERS`` (cmd-keyed). Each
        handler is a small private method that performs the same mutations
        as the original if/elif ladder. Behavior is fenced by the golden
        tests in ``tests/test_civ_rx_dispatch_golden.py``.
        """
        rs: "RadioState | None" = getattr(self._host, "_radio_state", None)
        if rs is None:
            return
        try:
            if frame.receiver is not None:
                rx_name = "MAIN" if frame.receiver == 0x00 else "SUB"
            else:
                rx_name = rs.active
            rx = rs.receiver(rx_name)

            # Per-slot override: when the poller is actively reading the
            # unselected VFO slot on a receiver (issue #715), it sets
            # ``host._vfo_slot_override[rx_name]`` to "A" or "B" so that
            # incoming 0x03/0x04 responses are routed to the correct slot
            # instead of the currently-active one.
            override_map = getattr(self._host, "_vfo_slot_override", None)
            slot_override: str | None = None
            if isinstance(override_map, dict):
                _so = override_map.get(rx_name)
                if _so in ("A", "B"):
                    slot_override = _so

            handler = self._RADIO_STATE_HANDLERS.get(frame.command)
            if handler is not None:
                handler(self, frame, rx, rs, slot_override)

        except (ValueError, IndexError, KeyError, AttributeError, TypeError) as exc:
            logger.debug(
                "civ-rx: state update failed for cmd=0x%02x sub=0x%02x: %s",
                frame.command or 0,
                frame.sub or 0,
                exc,
            )
        except Exception:
            logger.warning("civ-rx: unexpected error in state update", exc_info=True)

    # ------------------------------------------------------------------
    # Per-command handlers for _update_radio_state_from_frame.
    #
    # All handlers share the uniform signature
    # ``(self, frame, rx, rs, slot_override)``. Some handlers ignore
    # ``rx`` or ``slot_override``; the uniform shape keeps the dispatch
    # site at ``_update_radio_state_from_frame`` clean.
    # ------------------------------------------------------------------

    def _handle_freq(
        self,
        frame: CivFrame,
        rx: Any,
        rs: "RadioState",
        slot_override: str | None,
    ) -> None:
        # cmd 0x03 (GET freq response) and 0x00 (unsolicited freq).
        freq = parse_frequency_response(frame)
        if slot_override is not None:
            from dataclasses import replace as _replace

            tgt_slot = rx.vfo_b if slot_override == "B" else rx.vfo_a
            new_slot = _replace(tgt_slot, freq_hz=freq)
            if slot_override == "B":
                rx.vfo_b = new_slot
            else:
                rx.vfo_a = new_slot
        else:
            rx.freq = freq

    def _handle_mode(
        self,
        frame: CivFrame,
        rx: Any,
        rs: "RadioState",
        slot_override: str | None,
    ) -> None:
        # cmd 0x04 (GET mode response) and 0x01 (unsolicited mode).
        mode_val, filt = parse_mode_response(frame)
        if slot_override is not None:
            from dataclasses import replace as _replace

            tgt_slot = rx.vfo_b if slot_override == "B" else rx.vfo_a
            kw: dict[str, Any] = {"mode": mode_val.name}
            if filt is not None:
                kw["filter_num"] = filt
            new_slot = _replace(tgt_slot, **kw)
            if slot_override == "B":
                rx.vfo_b = new_slot
            else:
                rx.vfo_a = new_slot
        else:
            rx.mode = mode_val.name
            if filt is not None:
                rx.filter = filt

    def _handle_07(
        self,
        frame: CivFrame,
        rx: Any,
        rs: "RadioState",
        slot_override: str | None,
    ) -> None:
        # cmd 0x07: VFO/dual-watch sub-subs (0xD2 active, 0xC2 dual-watch).
        if len(frame.data) >= 2:
            sub07 = frame.data[0]
            val07 = frame.data[1]
            if sub07 == 0xD2:
                new_active = "SUB" if val07 else "MAIN"
                if rs.active != new_active:
                    rs.active = new_active
                    logger.debug("civ-rx: active receiver → %s", new_active)
                    self._notify_change(
                        "active_receiver_changed", {"active": new_active}
                    )
            elif sub07 == 0xC2:
                new_dw = bool(val07)
                if rs.dual_watch != new_dw:
                    rs.dual_watch = new_dw
                    logger.debug("civ-rx: dual watch → %s", "ON" if new_dw else "OFF")
                    self._notify_change("dual_watch_changed", {"on": new_dw})

    def _handle_0e(
        self,
        frame: CivFrame,
        rx: Any,
        rs: "RadioState",
        slot_override: str | None,
    ) -> None:
        # cmd 0x0E: scan state.
        if frame.data:
            sub_0e = frame.data[0]
            # 0xA1-0xA7 (ΔF span) and 0xD0-0xD3 (resume) are config-only
            if sub_0e <= 0x23:
                rs.scanning = bool(sub_0e)

    def _handle_0f(
        self,
        frame: CivFrame,
        rx: Any,
        rs: "RadioState",
        slot_override: str | None,
    ) -> None:
        # cmd 0x0F: split on/off.
        if frame.data:
            rs.split = bool(frame.data[0])

    def _handle_10(
        self,
        frame: CivFrame,
        rx: Any,
        rs: "RadioState",
        slot_override: str | None,
    ) -> None:
        # cmd 0x10: tuning step (BCD nibble pair).
        if frame.data:
            b = frame.data[0]
            rs.tuning_step = ((b >> 4) & 0x0F) * 10 + (b & 0x0F)

    def _handle_11(
        self,
        frame: CivFrame,
        rx: Any,
        rs: "RadioState",
        slot_override: str | None,
    ) -> None:
        # cmd 0x11: attenuator value (BCD nibbles → dB).
        if frame.data:
            val = frame.data[0]
            rx.att = ((val >> 4) & 0x0F) * 10 + (val & 0x0F)

    def _handle_12(
        self,
        frame: CivFrame,
        rx: Any,
        rs: "RadioState",
        slot_override: str | None,
    ) -> None:
        # cmd 0x12: antenna selection.
        # Sub 0x00 → ANT1, 0x01 → ANT2.
        # Data byte: 0x00 = RX ANT OFF, 0x01 = RX ANT ON.
        sub12 = frame.sub
        if sub12 in (0x00, 0x01):
            rs.tx_antenna = sub12 + 1  # 0x00→1, 0x01→2
            if frame.data:
                rx_ant_on = bool(frame.data[0])
                if sub12 == 0x00:
                    rs.rx_antenna_1 = rx_ant_on
                else:
                    rs.rx_antenna_2 = rx_ant_on

    def _handle_14(
        self,
        frame: CivFrame,
        rx: Any,
        rs: "RadioState",
        slot_override: str | None,
    ) -> None:
        # cmd 0x14: receiver/global levels (BCD pairs).
        if len(frame.data) >= 2:
            b0, b1 = frame.data[0], frame.data[1]
            raw = (b0 >> 4) * 1000 + (b0 & 0x0F) * 100 + (b1 >> 4) * 10 + (b1 & 0x0F)
            sub = frame.sub
            if sub in _CMD14_RECEIVER_LEVEL_FIELDS:
                setattr(rx, _CMD14_RECEIVER_LEVEL_FIELDS[sub], raw)
            elif sub == 0x0A:
                rs.power_level = raw
            elif sub == 0x09:
                rs.cw_pitch = int(round((((600.0 / 255.0) * raw) + 300) / 5.0) * 5.0)
            elif sub == 0x0C:
                rs.key_speed = round((raw / 6.071) + 6)
            elif sub in _CMD14_GLOBAL_LEVEL_FIELDS:
                setattr(rs, _CMD14_GLOBAL_LEVEL_FIELDS[sub], raw)

    def _handle_15(
        self,
        frame: CivFrame,
        rx: Any,
        rs: "RadioState",
        slot_override: str | None,
    ) -> None:
        # cmd 0x15: meter/SQL reads.
        if frame.sub == 0x01 and frame.data:
            rx.s_meter_sql_open = bool(frame.data[0])
        elif frame.sub == 0x05 and frame.data:
            rx.s_meter_sql_open = bool(frame.data[0])
        elif frame.sub == 0x07 and frame.data:
            rs.overflow = bool(frame.data[0])
        elif len(frame.data) >= 2:
            b0, b1 = frame.data[0], frame.data[1]
            raw = (b0 >> 4) * 1000 + (b0 & 0x0F) * 100 + (b1 >> 4) * 10 + (b1 & 0x0F)
            if frame.sub == 0x02:
                rs.receiver(rs.active).s_meter = raw
            elif frame.sub == 0x11:
                rs.power_meter = raw
            elif frame.sub == 0x12:
                rs.swr_meter = raw
            elif frame.sub == 0x13:
                rs.alc_meter = raw
            elif frame.sub == 0x14:
                rs.comp_meter = raw
            elif frame.sub == 0x15:
                rs.vd_meter = raw
            elif frame.sub == 0x16:
                rs.id_meter = raw

    def _handle_16(
        self,
        frame: CivFrame,
        rx: Any,
        rs: "RadioState",
        slot_override: str | None,
    ) -> None:
        # cmd 0x16: function on/off + values (flags, BCD nibbles).
        sub = frame.sub or 0
        data = frame.data
        if sub == 0 and len(data) >= 2:
            sub = data[0]
            data = data[1:]
        if data:
            val = data[0]
            if sub == 0x02:
                rx.preamp = val
            elif sub in _CMD16_RECEIVER_BOOL_FIELDS:
                setattr(rx, _CMD16_RECEIVER_BOOL_FIELDS[sub], bool(val))
            elif sub in _CMD16_RECEIVER_VALUE_FIELDS:
                field, _ = _CMD16_RECEIVER_VALUE_FIELDS[sub]
                setattr(rx, field, ((val >> 4) & 0x0F) * 10 + (val & 0x0F))
            elif sub in _CMD16_GLOBAL_BOOL_FIELDS:
                setattr(rs, _CMD16_GLOBAL_BOOL_FIELDS[sub], bool(val))
            elif sub in _CMD16_GLOBAL_VALUE_FIELDS:
                field, _ = _CMD16_GLOBAL_VALUE_FIELDS[sub]
                setattr(rs, field, ((val >> 4) & 0x0F) * 10 + (val & 0x0F))
            event_name = _CMD16_NOTIFY_EVENTS.get(sub)
            if event_name is not None:
                if (
                    sub in _CMD16_RECEIVER_BOOL_FIELDS
                    or sub in _CMD16_GLOBAL_BOOL_FIELDS
                ):
                    self._notify_change(event_name, {"on": bool(val)})
                elif (
                    sub in _CMD16_RECEIVER_VALUE_FIELDS
                    or sub in _CMD16_GLOBAL_VALUE_FIELDS
                ):
                    decoded = ((val >> 4) & 0x0F) * 10 + (val & 0x0F)
                    self._notify_change(event_name, {"value": decoded})

    def _handle_1a(
        self,
        frame: CivFrame,
        rx: Any,
        rs: "RadioState",
        slot_override: str | None,
    ) -> None:
        # cmd 0x1A: CTL mem levels + filter width + data mode + AF mute.
        sub = frame.sub
        if sub == 0x04:
            rx.agc_time_constant = parse_level_response(
                frame,
                command=0x1A,
                sub=0x04,
                bcd_bytes=1,
            )
        if sub == 0x03 and frame.data:
            from icom_lan.commands import _bcd_decode_value, filter_index_to_hz

            filter_index = _bcd_decode_value(frame.data)
            profile = getattr(self._host, "_profile", None)
            if (
                profile is not None
                and getattr(profile, "filter_width_encoding", None)
                == "segmented_bcd_index"
            ):
                rule = profile.resolve_filter_rule(
                    rx.mode,
                    data_mode=int(getattr(rx, "data_mode", 0) or 0),
                )
                if rule is not None and rule.segments:
                    rx.filter_width = filter_index_to_hz(
                        filter_index, segments=rule.segments
                    )
                else:
                    rx.filter_width = filter_index
            else:
                rx.filter_width = filter_index
        elif sub == 0x05:
            for prefix, (
                field,
                bcd_bytes,
            ) in _CMD1A_CTL_MEM_LEVEL_FIELDS.items():
                if frame.data.startswith(prefix):
                    setattr(
                        rs,
                        field,
                        parse_level_response(
                            frame,
                            command=0x1A,
                            sub=0x05,
                            prefix=prefix,
                            bcd_bytes=bcd_bytes,
                        ),
                    )
                    break
        elif sub == 0x06 and frame.data:
            rx.data_mode = frame.data[0]
        elif sub == 0x09:
            rx.af_mute = parse_bool_response(frame, command=0x1A, sub=0x09)

    def _handle_1b(
        self,
        frame: CivFrame,
        rx: Any,
        rs: "RadioState",
        slot_override: str | None,
    ) -> None:
        # cmd 0x1B: tone/TSQL freq (frequency-encoded).
        if len(frame.data) >= 3:
            from icom_lan.commands import _decode_tone_freq

            freq_hz = _decode_tone_freq(frame.data)
            freq_centihz = round(freq_hz * 100)
            if frame.sub == 0x00:
                rx.tone_freq = freq_centihz
            elif frame.sub == 0x01:
                rx.tsql_freq = freq_centihz

    def _handle_1c(
        self,
        frame: CivFrame,
        rx: Any,
        rs: "RadioState",
        slot_override: str | None,
    ) -> None:
        # cmd 0x1C: PTT (sub 0x00), tuner status (0x01), tx-freq monitor (0x03).
        if frame.sub == 0x00:
            if frame.data:
                rs.ptt = bool(frame.data[0])
        elif frame.sub == 0x01:
            if frame.data:
                rs.tuner_status = frame.data[0]
        elif frame.sub == 0x03:
            if frame.data:
                rs.tx_freq_monitor = bool(frame.data[0])

    def _handle_1e(
        self,
        frame: CivFrame,
        rx: Any,
        rs: "RadioState",
        slot_override: str | None,
    ) -> None:
        # cmd 0x1E: TX band edge (sub 0x01, payload ≥ 10 bytes).
        if frame.sub == 0x01 and len(frame.data) >= 10:
            from icom_lan.commands.tx_band import parse_tx_band_edge_response
            from icom_lan.radio_state import TxBandEdge

            start_hz, end_hz = parse_tx_band_edge_response(frame.data)
            tx_edge = TxBandEdge(start_hz=start_hz, end_hz=end_hz)
            if tx_edge not in rs.tx_band_edges:
                rs.tx_band_edges.append(tx_edge)

    def _handle_21(
        self,
        frame: CivFrame,
        rx: Any,
        rs: "RadioState",
        slot_override: str | None,
    ) -> None:
        # cmd 0x21: RIT freq, on/off, tx status.
        if frame.sub == 0x00 and len(frame.data) >= 3:
            from icom_lan.commands import parse_rit_frequency_response

            rs.rit_freq = parse_rit_frequency_response(frame.data)
        elif frame.sub == 0x01 and frame.data:
            rs.rit_on = bool(frame.data[0])
        elif frame.sub == 0x02 and frame.data:
            rs.rit_tx = bool(frame.data[0])

    def _handle_25(
        self,
        frame: CivFrame,
        rx: Any,
        rs: "RadioState",
        slot_override: str | None,
    ) -> None:
        # cmd 0x25: dual-RX freq by receiver ID.
        if len(frame.data) >= 6:
            from icom_lan.types import bcd_decode

            rcvr_byte = frame.data[0]
            which = "MAIN" if rcvr_byte == 0x00 else "SUB"
            rs.receiver(which).freq = bcd_decode(frame.data[1:6])

    def _handle_26(
        self,
        frame: CivFrame,
        rx: Any,
        rs: "RadioState",
        slot_override: str | None,
    ) -> None:
        # cmd 0x26: dual-RX mode by receiver ID + optional data_mode + filter.
        if len(frame.data) >= 2:
            from icom_lan.types import Mode

            rcvr_byte = frame.data[0]
            which = "MAIN" if rcvr_byte == 0x00 else "SUB"
            tgt = rs.receiver(which)
            tgt.mode = Mode(frame.data[1]).name
            if len(frame.data) >= 3:
                tgt.data_mode = frame.data[2]
            if len(frame.data) >= 4:
                tgt.filter = frame.data[3]

    def _handle_27(
        self,
        frame: CivFrame,
        rx: Any,
        rs: "RadioState",
        slot_override: str | None,
    ) -> None:
        # cmd 0x27: scope control (11 sub-subs).
        scope = rs.scope_controls
        if frame.sub == 0x12:
            scope.receiver = parse_scope_main_sub_response(frame)
        elif frame.sub == 0x13:
            scope.dual = parse_scope_single_dual_response(frame)
        elif frame.sub == 0x14:
            receiver, mode = parse_scope_mode_response(frame)
            if receiver is not None:
                scope.receiver = receiver
            scope.mode = mode
        elif frame.sub == 0x15:
            receiver, span = parse_scope_span_response(frame)
            if receiver is not None:
                scope.receiver = receiver
            scope.span = span
        elif frame.sub == 0x16:
            receiver, edge = parse_scope_edge_response(frame)
            if receiver is not None:
                scope.receiver = receiver
            scope.edge = edge
        elif frame.sub == 0x17:
            receiver, hold = parse_scope_hold_response(frame)
            if receiver is not None:
                scope.receiver = receiver
            scope.hold = hold
        elif frame.sub == 0x19:
            receiver, ref_db = parse_scope_ref_response(frame)
            if receiver is not None:
                scope.receiver = receiver
            scope.ref_db = ref_db
        elif frame.sub == 0x1A:
            receiver, speed = parse_scope_speed_response(frame)
            if receiver is not None:
                scope.receiver = receiver
            scope.speed = speed
        elif frame.sub == 0x1B:
            scope.during_tx = parse_scope_during_tx_response(frame)
        elif frame.sub == 0x1C:
            receiver, center_type = parse_scope_center_type_response(frame)
            if receiver is not None:
                scope.receiver = receiver
            scope.center_type = center_type
        elif frame.sub == 0x1D:
            receiver, vbw_narrow = parse_scope_vbw_response(frame)
            if receiver is not None:
                scope.receiver = receiver
            scope.vbw_narrow = vbw_narrow
        elif frame.sub == 0x1E:
            scope.fixed_edge = parse_scope_fixed_edge_response(frame)
            scope.edge = scope.fixed_edge.edge
        elif frame.sub == 0x1F:
            receiver, rbw = parse_scope_rbw_response(frame)
            if receiver is not None:
                scope.receiver = receiver
            scope.rbw = rbw

    # Top-level dispatch table for ``_update_radio_state_from_frame``.
    # Keyed by ``frame.command``; sub-cmd dispatch lives inside each
    # handler. 0x03/0x00 (freq) and 0x04/0x01 (mode) share handlers
    # because the unsolicited transceive variants have identical state
    # mutations as their GET-response counterparts.
    _RADIO_STATE_HANDLERS: dict[int, Any] = {
        0x00: _handle_freq,
        0x01: _handle_mode,
        0x03: _handle_freq,
        0x04: _handle_mode,
        0x07: _handle_07,
        0x0E: _handle_0e,
        0x0F: _handle_0f,
        0x10: _handle_10,
        0x11: _handle_11,
        0x12: _handle_12,
        0x14: _handle_14,
        0x15: _handle_15,
        0x16: _handle_16,
        0x1A: _handle_1a,
        0x1B: _handle_1b,
        0x1C: _handle_1c,
        0x1E: _handle_1e,
        0x21: _handle_21,
        0x25: _handle_25,
        0x26: _handle_26,
        0x27: _handle_27,
    }

    def _notify_change(self, event_name: str, data: dict[str, Any]) -> None:
        """Notify server of state change (best-effort)."""
        cb = getattr(self._host, "_on_state_change", None)
        if cb is not None:
            logger.debug("civ-rx: notify %s %s", event_name, data)
            try:
                cb(event_name, data)
            except Exception:
                logger.warning("civ-rx: notify failed", exc_info=True)
        else:
            logger.debug("civ-rx: no callback for %s", event_name)

    def _publish_scope_frame(self, frame: ScopeFrame) -> None:
        """Publish a complete scope frame to callback and bounded queue."""
        self._publish_civ_event(CivEvent(type=CivEventType.SCOPE_FRAME))
        self._host._scope_frame_queue.put_drop_oldest(frame)
        callback = self._host._scope_callback
        if callback is not None:
            try:
                callback(frame)
            except Exception:
                logger.exception("Scope callback raised an exception")

    def _publish_civ_event(self, event: CivEvent) -> None:
        """Publish CI-V event to internal event queue (best effort)."""
        self._host._civ_event_queue.put_drop_oldest(event)

    def _check_connected(self) -> None:
        """Raise ConnectionError if not connected."""
        ctrl = getattr(self._host, "_ctrl_transport", None)
        ctrl_alive = bool(ctrl and getattr(ctrl, "_udp_transport", None) is not None)
        recovering = bool(getattr(self._host, "_civ_recovering", False))
        if not self._host._connected or self._host._civ_transport is None:
            if ctrl_alive and recovering:
                return
            raise ConnectionError("Not connected to radio")

    async def _wait_for_civ_transport_recovery(
        self, timeout: "float | None" = None
    ) -> None:
        """Wait for CI-V transport recovery or trigger a fast soft-reconnect."""
        wait_timeout = (
            timeout
            if timeout is not None
            else getattr(self._host, "_civ_recovery_wait_timeout", 12.0)
        )
        deadline = time.monotonic() + max(0.5, float(wait_timeout))
        fast_attempted = False

        while time.monotonic() < deadline:
            ctrl = getattr(self._host, "_ctrl_transport", None)
            ctrl_alive = bool(
                ctrl and getattr(ctrl, "_udp_transport", None) is not None
            )
            if not ctrl_alive:
                raise ConnectionError("Not connected to radio")

            civ_t = getattr(self._host, "_civ_transport", None)
            if civ_t is not None and getattr(self._host, "_connected", False):
                return

            if civ_t is None and not fast_attempted:
                fast_attempted = True
                lock = getattr(self._host, "_civ_recovery_lock", None)
                if isinstance(lock, asyncio.Lock):
                    async with lock:
                        civ_t2 = getattr(self._host, "_civ_transport", None)
                        if civ_t2 is None and ctrl_alive:
                            try:
                                await self._host.soft_reconnect()
                            except (ConnectionError, TimeoutError, OSError) as exc:
                                logger.debug(
                                    "Fast CI-V soft_reconnect attempt failed: %s",
                                    exc,
                                )
                            except Exception:
                                logger.warning(
                                    "Fast CI-V soft_reconnect: unexpected error",
                                    exc_info=True,
                                )
                else:
                    try:
                        await self._host.soft_reconnect()
                    except (ConnectionError, TimeoutError, OSError) as exc:
                        logger.debug(
                            "Fast CI-V soft_reconnect attempt failed: %s",
                            exc,
                        )
                    except Exception:
                        logger.warning(
                            "Fast CI-V soft_reconnect: unexpected error",
                            exc_info=True,
                        )

            await asyncio.sleep(0.2)

        raise TimeoutError("CI-V transport recovery timed out")

    def _wrap_civ(self, civ_frame: bytes) -> bytes:
        """Wrap a CI-V frame in a UDP data packet for the CI-V port."""
        import struct

        assert self._host._civ_transport is not None
        total_len = CIV_HEADER_SIZE + len(civ_frame)
        pkt = bytearray(total_len)
        struct.pack_into("<I", pkt, 0, total_len)
        struct.pack_into("<H", pkt, 4, 0x00)
        struct.pack_into("<I", pkt, 8, self._host._civ_transport.my_id)
        struct.pack_into("<I", pkt, 0x0C, self._host._civ_transport.remote_id)
        pkt[0x10] = 0xC1
        struct.pack_into("<H", pkt, 0x11, len(civ_frame))
        struct.pack_into(">H", pkt, 0x13, self._host._civ_send_seq)
        self._host._civ_send_seq = (self._host._civ_send_seq + 1) & 0xFFFF
        pkt[CIV_HEADER_SIZE:] = civ_frame
        return bytes(pkt)

    async def _send_civ_raw(
        self,
        civ_frame: bytes,
        *,
        priority: Priority = Priority.NORMAL,
        key: "str | None" = None,
        dedupe: bool = False,
        wait_response: bool = True,
        timeout: "float | None" = None,
    ) -> "CivFrame | None":
        """Enqueue a CI-V command and wait for its response."""
        if self._host._civ_transport is None or not self._host._connected:
            await self._wait_for_civ_transport_recovery(timeout=timeout)
        assert self._host._civ_transport is not None
        self._ensure_civ_runtime()

        if self._host._commander is None:
            coro = self._execute_civ_raw(civ_frame, wait_response=wait_response)
            if timeout is not None:
                return await asyncio.wait_for(coro, timeout=timeout)
            return await coro

        return await self._host._commander.send(
            civ_frame,
            priority=priority,
            key=key,
            dedupe=dedupe,
            wait_response=wait_response,
            timeout=timeout,
        )

    @staticmethod
    def _civ_expects_response(frame: CivFrame) -> bool:
        """Determine if a CI-V frame expects a data RESPONSE or just an ACK/NAK."""
        if frame.command in (0x03, 0x04, 0x25, 0x26):
            return True
        if frame.command == 0x07 and frame.data == b"\xc2":
            return True
        if frame.command == 0x1A and frame.sub == 0x05 and len(frame.data) == 2:
            return True
        if frame.command == 0x1A and frame.sub == 0x01 and len(frame.data) == 2:
            # Band Stacking Register read: 1A 01 <band> <register>
            return True
        if frame.command == 0x17:
            return False
        if frame.command == 0x27:
            # Scope-control GETs for subs 0x14, 0x15, 0x16, 0x17, 0x19, 0x1A,
            # 0x1D, 0x1F may carry a single receiver-prefix byte (00=MAIN,
            # 01=SUB) and still expect a data response. Subs without prefix
            # (0x12, 0x13, 0x1B, 0x1C, 0x1E) keep the empty-data heuristic.
            _SCOPE_GET_WITH_RX_PREFIX = (
                0x14,
                0x15,
                0x16,
                0x17,
                0x19,
                0x1A,
                0x1D,
                0x1F,
            )
            if frame.sub in _SCOPE_GET_WITH_RX_PREFIX:
                return len(frame.data) <= 1
            return len(frame.data) == 0
        return len(frame.data) == 0

    async def _drain_ack_sinks_before_blocking(self) -> None:
        """Give fire-and-forget ACK sinks a short chance to drain."""
        if self._host._civ_request_tracker.ack_sink_count == 0:
            return

        deadline = time.monotonic() + self._host._civ_ack_sink_grace
        while (
            self._host._civ_request_tracker.ack_sink_count > 0
            and time.monotonic() < deadline
        ):
            await asyncio.sleep(0.005)

        dropped = self._host._civ_request_tracker.drop_ack_sinks()
        if dropped:
            logger.debug(
                "Dropped %d stale ACK sink waiter(s) before blocking command", dropped
            )

    async def _execute_civ_raw(
        self,
        civ_frame: bytes,
        wait_response: bool = True,
        deadline_monotonic: "float | None" = None,
    ) -> "CivFrame | None":
        """Execute one CI-V command via request tracker (serialized by worker)."""
        assert self._host._civ_transport is not None
        self._ensure_civ_runtime()

        parsed_frame = parse_civ_frame(civ_frame)
        request_key = request_key_from_frame(parsed_frame)
        expects_response = self._civ_expects_response(parsed_frame)
        if deadline_monotonic is None:
            deadline_monotonic = time.monotonic() + self._host._civ_get_timeout

        self._cleanup_stale_civ_waiters()

        if not wait_response:
            ack_sink_token: "int | None" = None

            if not expects_response:
                token_or_future = self._host._civ_request_tracker.register_ack(
                    wait=False
                )
                if isinstance(token_or_future, int):
                    ack_sink_token = token_or_future

            self.start_pump()

            now = time.monotonic()
            delta = now - self._host._last_civ_send_monotonic
            if delta < self._host._civ_min_interval:
                await asyncio.sleep(self._host._civ_min_interval - delta)

            pkt = self._wrap_civ(civ_frame)
            try:
                await self._host._civ_transport.send_tracked(pkt)
            except Exception:
                if ack_sink_token is not None:
                    self._host._civ_request_tracker.unregister_ack_sink(ack_sink_token)
                raise

            self._host._last_civ_send_monotonic = time.monotonic()
            return None

        await self._drain_ack_sinks_before_blocking()

        remaining_total = deadline_monotonic - time.monotonic()
        if remaining_total <= 0:
            raise TimeoutError("CI-V response timed out")

        pending: "asyncio.Future[CivFrame] | None" = None
        try:
            if expects_response:
                pending = self._host._civ_request_tracker.register_response(request_key)
            else:
                pending_or_token = self._host._civ_request_tracker.register_ack(
                    wait=True
                )
                if isinstance(pending_or_token, int):
                    raise RuntimeError("ACK waiter registration returned sink token")
                pending = pending_or_token

            self.start_pump()

            now = time.monotonic()
            delta = now - self._host._last_civ_send_monotonic
            if delta < self._host._civ_min_interval:
                await asyncio.sleep(self._host._civ_min_interval - delta)

            pkt = self._wrap_civ(civ_frame)
            await self._host._civ_transport.send_tracked(pkt)
            self._host._last_civ_send_monotonic = time.monotonic()
            assert pending is not None
            remaining = deadline_monotonic - time.monotonic()
            if remaining <= 0:
                raise TimeoutError("CI-V response timed out")
            try:
                return await asyncio.wait_for(pending, timeout=remaining)
            except asyncio.TimeoutError:
                self._host._civ_request_tracker.note_timeout()
                logger.debug(
                    "CI-V command 0x%02X timed out",
                    request_key.command,
                )
                raise TimeoutError("CI-V response timed out")
        finally:
            if pending is not None:
                self._host._civ_request_tracker.unregister(pending)
