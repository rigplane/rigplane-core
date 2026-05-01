"""Control WebSocket handler — JSON commands, events, state."""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING, Any, ClassVar, Literal, cast

from ..._bounded_queue import BoundedQueue
from ...profiles import RadioProfile
from ...radio_state import RadioState
from ..protocol import (  # noqa: TID251
    decode_json,
    encode_json,
)
from ..radio_poller import (  # noqa: TID251
    PttOff,
    PttOn,
    ScanSetDfSpan,
    ScanSetResume,
    ScanStart,
    ScanStop,
    SelectVfo,
    SetAcc1ModLevel,
    SetAfLevel,
    SetAgc,
    SetAgcTimeConstant,
    SetAntenna1,
    SetAntenna2,
    SetApf,
    SetAttenuator,
    SetAutoNotch,
    SetBand,
    SetCompressor,
    SetCompressorLevel,
    SetBreakIn,
    SetCwPitch,
    SetDataMode,
    SetKeySpeed,
    SetDialLock,
    SetDigiSel,
    SetDualWatch,
    SetFilter,
    SetFilterShape,
    SetFilterWidth,
    SetFreq,
    SetIfShift,
    SetIpPlus,
    SetLanModLevel,
    SetManualNotch,
    SetMicGain,
    SetMode,
    SetMonitor,
    SetMonitorGain,
    SetNB,
    SetNBLevel,
    SetNotchFilter,
    SetNR,
    SetNRLevel,
    SetPbtInner,
    SetPbtOuter,
    SetPower,
    SetPowerstat,
    SetPreamp,
    SetRfGain,
    SetRitFrequency,
    SetRitStatus,
    SetRitTxStatus,
    SetRxAntennaAnt1,
    SetRxAntennaAnt2,
    SetScopeCenterType,
    SetScopeDuringTx,
    SetScopeEdge,
    SetScopeFixedEdge,
    SetScopeDual,
    SetScopeMode,
    SetScopeRbw,
    SetScopeSpan,
    SetScopeSpeed,
    SetScopeRef,
    SetScopeHold,
    SetScopeVbw,
    SetSplit,
    SetSquelch,
    SetSystemDate,
    SetSystemTime,
    SetTwinPeak,
    SetDriveGain,
    SetUsbModLevel,
    SetVox,
    SwitchScopeReceiver,
    VfoEqualize,
    VfoSwap,
    SetToneFreq,
    SetTsqlFreq,
    SetMainSubTracking,
    SetSsbTxBandwidth,
    SetManualNotchWidth,
    SetBreakInDelay,
    SetVoxGain,
    SetAntiVoxGain,
    SetVoxDelay,
    SetNbDepth,
    SetNbWidth,
    SetDashRatio,
    SetRepeaterTone,
    SetRepeaterTsql,
    SetRxAntenna,
    SetMemoryMode,
    MemoryWrite,
    MemoryToVfo,
    MemoryClear,
    SetMemoryContents,
    SetBsr,
    SetDataOffModInput,
    SetData1ModInput,
    SetData2ModInput,
    SetData3ModInput,
    SetAudioPeakFilter,
    SetDigiselShift,
    SetRefAdjust,
    SetCivTransceive,
    SetCivOutputAnt,
    SetAfMute,
    SetTuningStep,
    SetXfcStatus,
    SetTxFreqMonitor,
    SetUtcOffset,
    QuickSplit,
    QuickDualWatch,
    QuickDwTrigger,
    QuickSplitTrigger,
    Speak,
)
from ..runtime_helpers import (  # noqa: TID251
    build_public_state_payload,
    radio_ready,
    runtime_capabilities,
)
from ..websocket import WS_OP_TEXT, WebSocketConnection  # noqa: TID251

if TYPE_CHECKING:
    from ...radio_protocol import Radio

from ...capabilities import (
    CAP_AF_LEVEL,
    CAP_ANTENNA,
    CAP_BAND_EDGE,
    CAP_BREAK_IN,
    CAP_CW,
    CAP_DATA_MODE,
    CAP_DUAL_WATCH,
    CAP_POWER_CONTROL,
    CAP_RF_GAIN,
    CAP_SQUELCH,
    CAP_SYSTEM_SETTINGS,
    CAP_TUNER,
    CAP_TUNING_STEP,
    CAP_TX,
    CAP_XFC,
)
from ...radio_protocol import MemoryCapable

__all__ = ["ControlHandler"]

logger = logging.getLogger(__name__)
_MAX_CW_TEXT_CHARS = 512


class ControlHandler:
    """Handles the /api/v1/ws control WebSocket channel.

    Receives JSON commands from the client and enqueues them via the
    server's CommandQueue.  Receives broadcast events from RadioPoller
    via an asyncio.Queue and forwards them to the WebSocket.

    Args:
        ws: Established WebSocket connection.
        radio: Radio protocol instance (may be None in standalone mode).
        server_version: Version string for the hello message.
        radio_model: Radio model string for the hello message.
        server: WebServer instance for command_queue and event broadcast.
    """

    _COMMANDS = frozenset(
        [
            "set_freq",
            "set_band",
            "set_mode",
            "set_filter",
            "set_filter_width",
            "set_filter_shape",
            "set_if_shift",
            "ptt",
            "set_rf_power",
            "set_power",  # backward-compat alias for set_rf_power
            "set_powerstat",
            "set_rf_gain",
            "set_af_level",
            "set_sql",
            "set_squelch",
            "set_nb",
            "set_nr",
            "set_nr_level",
            "set_nb_level",
            "set_auto_notch",
            "set_manual_notch",
            "set_notch_filter",
            "set_digisel",
            "set_ip_plus",
            "set_ipplus",  # backward-compat alias for set_ip_plus
            "set_att",
            "set_attenuator",
            "set_preamp",
            "set_pbt_inner",
            "set_pbt_outer",
            "set_cw_pitch",
            "set_key_speed",
            "set_break_in",
            "set_apf",
            "set_twin_peak",
            "set_drive_gain",
            "scan_start",
            "scan_stop",
            "scan_set_df_span",
            "scan_set_resume",
            "set_data_mode",
            "set_mic_gain",
            "set_vox",
            "set_compressor_level",
            "set_monitor",
            "set_monitor_gain",
            "set_dial_lock",
            "set_agc_time_constant",
            "set_agc",
            "set_rit_status",
            "set_rit_tx_status",
            "set_rit_frequency",
            "set_split",
            "set_vfo",
            "select_vfo",  # backward-compat alias for set_vfo
            "ptt_on",
            "ptt_off",
            "vfo_swap",
            "vfo_equalize",
            "switch_scope_receiver",
            "set_scope_during_tx",
            "set_scope_center_type",
            "set_scope_edge",
            "set_scope_fixed_edge",
            "set_scope_vbw",
            "set_scope_rbw",
            "set_scope_dual",
            "set_scope_mode",
            "set_scope_span",
            "set_scope_speed",
            "set_scope_ref",
            "set_scope_hold",
            "set_antenna_1",
            "set_antenna_2",
            "set_rx_antenna_ant1",
            "set_rx_antenna_ant2",
            "get_system_date",
            "set_system_date",
            "get_system_time",
            "set_system_time",
            "set_acc1_mod_level",
            "set_usb_mod_level",
            "set_lan_mod_level",
            "get_dual_watch",
            "set_dual_watch",
            "get_tuner_status",
            "set_tuner_status",
            "set_comp",
            "set_compressor",
            "set_tone_freq",
            "set_tsql_freq",
            "set_main_sub_tracking",
            "set_ssb_tx_bw",
            "set_manual_notch_width",
            "send_cw_text",
            "stop_cw_text",
            "get_break_in_delay",
            "get_dash_ratio",
            "set_break_in_delay",
            "set_vox_gain",
            "set_anti_vox_gain",
            "set_vox_delay",
            "set_nb_depth",
            "set_nb_width",
            "set_dash_ratio",
            "set_repeater_tone",
            "set_repeater_tsql",
            "set_rx_antenna",
            "set_memory_mode",
            "memory_write",
            "memory_to_vfo",
            "memory_clear",
            "set_memory_contents",
            "set_bsr",
            "get_acc1_mod_level",
            "get_usb_mod_level",
            "get_lan_mod_level",
            "get_data_off_mod_input",
            "set_data_off_mod_input",
            "get_data1_mod_input",
            "set_data1_mod_input",
            "get_data2_mod_input",
            "set_data2_mod_input",
            "get_data3_mod_input",
            "set_data3_mod_input",
            "set_audio_peak_filter",
            "set_digisel_shift",
            "speak",
            # Issue #410 — system/config
            "get_ref_adjust",
            "set_ref_adjust",
            "get_civ_transceive",
            "set_civ_transceive",
            "get_civ_output_ant",
            "set_civ_output_ant",
            "get_af_mute",
            "set_af_mute",
            "get_tuning_step",
            "set_tuning_step",
            "get_utc_offset",
            "set_utc_offset",
            # Issue #411 — band/split advanced
            "get_band_edge_freq",
            "get_xfc_status",
            "set_xfc_status",
            "get_tx_freq_monitor",
            "set_tx_freq_monitor",
            "get_quick_split",
            "set_quick_split",
            "get_quick_dual_watch",
            "set_quick_dual_watch",
            # Epic #774 — Quick-DW / Quick-Split composite triggers
            # (emulate the IC-7610 front-panel long-press: equalize M→S
            # then enable DW/Split).  Distinct from the broken
            # get_/set_quick_* aliases above, which send the config-flag
            # read frame 0x1A 05 00 32/33.  See follow-up in epic #774.
            "quick_dualwatch",
            "quick_split",
            # Issue #677 — CW auto-tune via FFT peak detection
            "cw_auto_tune",
        ]
    )

    # Commands that key the transmitter — rejected when read_only=True.
    # set_tuner_status value=2 (TUNING) is handled inline in _enqueue_read_only.
    _TX_COMMANDS: frozenset[str] = frozenset(
        {
            "ptt",
            "ptt_on",
            "ptt_off",
            "send_cw_text",
        }
    )

    def __init__(
        self,
        ws: WebSocketConnection,
        radio: "Radio | None",
        server_version: str,
        radio_model: str,
        server: Any = None,
        read_only: bool = False,
    ) -> None:
        self._ws = ws
        self._radio = radio
        self._version = server_version
        self._radio_model = radio_model
        self._server = server
        self._read_only = read_only
        self._subscribed_streams: set[str] = set()
        self._event_queue: BoundedQueue[dict[str, Any]] = BoundedQueue(
            maxsize=100,
        )
        # Per-command rate limiting: command_name -> (last_time, drop_count)
        self._cmd_last: dict[str, float] = {}
        self._cmd_drops: dict[str, int] = {}
        # Minimum interval between same command (seconds).
        # Continuous slider/knob drag sends dozens of set_* per second.
        self._CMD_MIN_INTERVAL = 0.05  # 50ms = max 20 commands/sec per client

    async def run(self) -> None:
        """Run the control channel lifecycle."""
        await self._send_hello()
        # Wait for radio to populate initial state before sending snapshot.
        # Fallback: send whatever we have after 2 s so the client isn't stuck.
        await self._wait_radio_ready(timeout=2.0)
        # Send initial full state so this client has a baseline immediately.
        # Sent directly (not via event queue) so it arrives right after hello
        # and before the recv loop — no interleaving with command responses.
        if self._server is not None:
            try:
                initial_state = self._server.build_public_state()
                rev = self._server._delta_encoder.revision
                msg = {
                    "type": "state_update",
                    "data": {"type": "full", "data": initial_state, "revision": rev},
                }
                await self._ws.send_text(encode_json(msg))
            except Exception:
                logger.debug("control: failed to send initial state", exc_info=True)
            self._server.register_control_event_queue(self._event_queue)
        event_task: asyncio.Task[None] = asyncio.create_task(self._event_sender_loop())
        try:
            while True:
                opcode, payload = await self._ws.recv()
                if opcode == WS_OP_TEXT:
                    await self._handle_text(payload.decode("utf-8"))
        except EOFError:
            pass
        finally:
            event_task.cancel()
            try:
                await event_task
            except asyncio.CancelledError:
                pass
            if self._server is not None:
                self._server.unregister_control_event_queue(self._event_queue)

    async def _event_sender_loop(self) -> None:
        """Drain event queue and forward events to WebSocket."""
        try:
            while True:
                event = await self._event_queue.get()
                msg_type = event.get("type")
                if msg_type == "notification":
                    await self._send_json(event)
                elif msg_type == "state_update":
                    # Always forward state updates (clients need fresh state)
                    await self._send_json(event)
                elif (
                    "state" in self._subscribed_streams
                    or "events" in self._subscribed_streams
                ):
                    await self._send_json(event)
        except asyncio.CancelledError:
            pass

    async def _send_hello(self) -> None:
        raw_connected = (
            getattr(self._radio, "connected", False) if self._radio else False
        )
        caps = sorted(self._capabilities())
        msg = {
            "type": "hello",
            "proto": 1,
            "server": "icom-lan",
            "version": self._version,
            "radio": self._radio_model,
            "connected": raw_connected if isinstance(raw_connected, bool) else False,
            "radio_ready": self._radio_ready(),
            "capabilities": caps,
        }
        await self._ws.send_text(encode_json(msg))

    def _capabilities(self) -> set[str]:
        return set(runtime_capabilities(self._radio))

    def _ensure_receiver_supported(self, receiver: int) -> None:
        if self._radio is None:
            return
        raw_profile = getattr(self._radio, "profile", None)
        if isinstance(raw_profile, RadioProfile):
            receiver_count = raw_profile.receiver_count
        else:
            receiver_count = 2 if "dual_rx" in self._capabilities() else 1
        if 0 <= receiver < receiver_count:
            return
        raise ValueError(
            f"receiver={receiver} is not supported by active profile "
            f"(receivers={receiver_count})"
        )

    def _ensure_capability(self, capability: str, command_name: str) -> None:
        if self._radio is None:
            return
        caps = self._capabilities()
        # Also check profile capabilities (runtime_capabilities may strip
        # protocol-gated tags like dual_rx even when the profile supports them).
        raw_profile = getattr(self._radio, "profile", None)
        if isinstance(raw_profile, RadioProfile):
            if capability in raw_profile.capabilities:
                return
        if capability in caps:
            return
        logger.debug(
            "Skipping %s: capability '%s' not supported by %s",
            command_name,
            capability,
            getattr(self, "_radio_model", "unknown"),
        )
        raise ValueError(
            f"command {command_name!r} is not supported by active profile "
            f"(missing capability: {capability})"
        )

    def _radio_ready(self) -> bool:
        """Return backend radio readiness (CI-V healthy), with fallback."""
        return bool(radio_ready(self._radio))

    async def _wait_radio_ready(self, *, timeout: float = 2.0) -> None:
        """Wait until radio reports ready, with *timeout* fallback.

        If the radio is ``None`` (offline/test mode) or already ready,
        returns immediately.  Otherwise polls every 100 ms up to
        *timeout* seconds, then gives up silently so the client still
        gets a (possibly incomplete) snapshot.
        """
        if self._radio is None or self._radio_ready():
            return
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            await asyncio.sleep(0.1)
            if self._radio_ready():
                return
        logger.debug(
            "control: radio not ready after %.1fs, sending snapshot anyway",
            timeout,
        )

    def _backend_recovering(self) -> bool:
        """Whether backend is already managing a reconnect/recovery path."""
        if self._radio is None:
            return False
        conn_state = getattr(self._radio, "conn_state", None)
        state_value = getattr(conn_state, "value", conn_state)
        if state_value in {"connecting", "reconnecting", "disconnecting"}:
            return True
        connected = getattr(self._radio, "connected", False)
        connected_bool = connected if isinstance(connected, bool) else False
        return bool(connected_bool and not self._radio_ready())

    async def _handle_text(self, text: str) -> None:
        try:
            msg = decode_json(text)
        except ValueError as exc:
            logger.debug("control: invalid JSON: %s", exc)
            return

        msg_type = msg.get("type")

        if msg_type == "subscribe":
            await self._handle_subscribe(msg)
        elif msg_type == "unsubscribe":
            await self._handle_unsubscribe(msg)
        elif msg_type == "cmd":
            await self._handle_command(msg)
        elif msg_type == "radio_connect":
            await self._handle_radio_connect(msg)
        elif msg_type == "radio_disconnect":
            await self._handle_radio_disconnect(msg)
        else:
            logger.debug("control: unknown message type: %r", msg_type)

    async def _handle_radio_connect(self, msg: dict[str, Any]) -> None:
        """Handle radio_connect request — reconnect the radio."""
        logger.info("radio_connect requested")
        msg_id = msg.get("id", "")
        if self._radio is None:
            await self._send_json(
                {
                    "type": "response",
                    "id": msg_id,
                    "ok": False,
                    "error": "no_radio",
                    "message": "no radio instance",
                }
            )
            return
        if self._backend_recovering():
            await self._send_json(
                {
                    "type": "response",
                    "id": msg_id,
                    "ok": False,
                    "error": "backend_recovering",
                    "message": "backend is already managing radio recovery",
                }
            )
            return
        try:
            if self._radio.connected:
                await self._send_json(
                    {
                        "type": "response",
                        "id": msg_id,
                        "ok": True,
                        "result": {"status": "already_connected"},
                    }
                )
                return
            from ...radio_protocol import RecoverableConnection

            if isinstance(self._radio, RecoverableConnection):
                recoverable = cast(RecoverableConnection, self._radio)
                try:
                    await recoverable.soft_reconnect()
                except Exception:
                    logger.info("soft_reconnect failed, trying full connect")
                    await self._radio.connect()
            else:
                await self._radio.connect()
            await self._send_json(
                {
                    "type": "response",
                    "id": msg_id,
                    "ok": True,
                    "result": {"status": "connected"},
                }
            )
            await self._broadcast_connection_state(True)
        except Exception as exc:
            logger.warning("radio_connect failed: %s", exc)
            await self._send_json(
                {
                    "type": "response",
                    "id": msg_id,
                    "ok": False,
                    "error": "connect_failed",
                    "message": str(exc),
                }
            )

    async def _handle_radio_disconnect(self, msg: dict[str, Any]) -> None:
        """Handle radio_disconnect request — disconnect the radio."""
        logger.info("radio_disconnect requested")
        msg_id = msg.get("id", "")
        if self._radio is None:
            await self._send_json(
                {
                    "type": "response",
                    "id": msg_id,
                    "ok": False,
                    "error": "no_radio",
                    "message": "no radio instance",
                }
            )
            return
        try:
            if not self._radio.connected:
                await self._send_json(
                    {
                        "type": "response",
                        "id": msg_id,
                        "ok": True,
                        "result": {"status": "already_disconnected"},
                    }
                )
                return
            from ...radio_protocol import RecoverableConnection

            if isinstance(self._radio, RecoverableConnection):
                await cast(RecoverableConnection, self._radio).soft_disconnect()
            else:
                await self._radio.disconnect()
            await self._send_json(
                {
                    "type": "response",
                    "id": msg_id,
                    "ok": True,
                    "result": {"status": "disconnected"},
                }
            )
            await self._broadcast_connection_state(False)
        except Exception as exc:
            logger.warning("radio_disconnect failed: %s", exc)
            await self._send_json(
                {
                    "type": "response",
                    "id": msg_id,
                    "ok": False,
                    "error": "disconnect_failed",
                    "message": str(exc),
                }
            )

    async def _broadcast_connection_state(self, connected: bool) -> None:
        """Broadcast connection state change to this client."""
        await self._send_json(
            {
                "type": "event",
                "event": "connection_state",
                "connected": connected,
                "radio_ready": self._radio_ready(),
            }
        )

    async def _send_json(self, obj: dict[str, Any]) -> None:
        """Send a JSON message to the WebSocket client."""
        await self._ws.send_text(encode_json(obj))

    async def _handle_subscribe(self, msg: dict[str, Any]) -> None:
        streams = msg.get("streams", [])
        if isinstance(streams, list):
            self._subscribed_streams.update(str(s) for s in streams)
        await self._send_state_snapshot()

    async def _handle_unsubscribe(self, msg: dict[str, Any]) -> None:
        streams = msg.get("streams", [])
        if isinstance(streams, list):
            for s in streams:
                self._subscribed_streams.discard(str(s))

    async def _send_state_snapshot(self) -> None:
        payload: dict[str, Any] | None = None
        builder = (
            getattr(self._server, "build_public_state", None)
            if self._server is not None
            else None
        )
        if callable(builder):
            try:
                payload = cast(dict[str, Any], builder())
            except Exception as exc:
                logger.debug("control: public state build failed: %s", exc)

        if payload is None:
            raw_radio_state = (
                getattr(self._radio, "radio_state", None)
                if self._radio is not None
                else None
            )
            radio_state = (
                raw_radio_state
                if isinstance(raw_radio_state, RadioState)
                else RadioState()
            )
            raw_profile = (
                getattr(self._radio, "profile", None)
                if self._radio is not None
                else None
            )
            if isinstance(raw_profile, RadioProfile):
                receiver_count = raw_profile.receiver_count
            else:
                receiver_count = 2 if "dual_rx" in self._capabilities() else 1
            payload = build_public_state_payload(
                radio_state,
                radio=self._radio,
                revision=0,
                receiver_count=receiver_count,
            )

        msg_out = {"type": "state_update", "data": payload}
        await self._ws.send_text(encode_json(msg_out))
        # Send current DX spots if available
        if self._server is not None and hasattr(self._server, "_spot_buffer"):
            spots = self._server._spot_buffer.get_spots()
            await self._ws.send_text(encode_json({"type": "dx_spots", "spots": spots}))

    async def _handle_command(self, msg: dict[str, Any]) -> None:
        cmd_id = msg.get("id", "")
        name = msg.get("name", "")
        params = msg.get("params", {})

        # ── Server-side rate limiting (per client, per command) ──
        # Only throttle SET commands (continuous slider/knob drag).
        # GET and read-only commands pass through.
        if name.startswith("set_"):
            now = time.monotonic()
            last = self._cmd_last.get(name, 0.0)
            if now - last < self._CMD_MIN_INTERVAL:
                drops = self._cmd_drops.get(name, 0) + 1
                self._cmd_drops[name] = drops
                if drops == 1 or drops % 50 == 0:
                    logger.warning(
                        "rate-limit: dropping %s (%.0fms since last, dropped=%d)",
                        name,
                        (now - last) * 1000,
                        drops,
                    )
                # Still ACK the client so it doesn't stall
                await self._ws.send_text(
                    encode_json(
                        {
                            "type": "response",
                            "id": cmd_id,
                            "ok": True,
                            "result": {"throttled": True},
                        }
                    )
                )
                return
            self._cmd_last[name] = now
            self._cmd_drops[name] = 0

        if name not in self._COMMANDS:
            await self._ws.send_text(
                encode_json(
                    {
                        "type": "response",
                        "id": cmd_id,
                        "ok": False,
                        "error": "unknown_command",
                        "message": f"unknown command: {name!r}",
                    }
                )
            )
            return

        if self._radio is None:
            await self._ws.send_text(
                encode_json(
                    {
                        "type": "response",
                        "id": cmd_id,
                        "ok": False,
                        "error": "no_radio",
                        "message": "no radio connected",
                    }
                )
            )
            return

        try:
            result = await self._enqueue_command(name, params)
            await self._ws.send_text(
                encode_json(
                    {
                        "type": "response",
                        "id": cmd_id,
                        "ok": True,
                        "result": result,
                    }
                )
            )
        except Exception as exc:
            logger.warning("control: command %r failed: %s", name, exc)
            await self._ws.send_text(
                encode_json(
                    {
                        "type": "response",
                        "id": cmd_id,
                        "ok": False,
                        "error": "command_failed",
                        "message": str(exc),
                    }
                )
            )

    async def _enqueue_command(
        self, name: str, params: dict[str, Any]
    ) -> dict[str, Any]:
        """Build a Command dataclass, enqueue it, and return the ack result.

        Delegates to group-specific handlers to keep each branch focused.
        """
        logger.info("enqueue_command: %s params=%s", name, params)
        radio = self._radio

        # Transmit safety gate — must come before any dispatch.
        if self._read_only and name in self._TX_COMMANDS:
            raise PermissionError(f"read-only mode: {name} rejected")

        # Read-only commands — bypass command queue
        result = await self._enqueue_read_only(name, params, radio)
        if result is not None:
            return result

        q = self._server.command_queue if self._server is not None else None
        if q is None:
            raise RuntimeError("no command queue available")

        # Dispatch to group handlers
        for handler in (
            self._enqueue_rc_frequency,
            self._enqueue_rc_power,
            self._enqueue_rc_dsp,
            self._enqueue_rc_audio,
            self._enqueue_rc_scope,
            self._enqueue_rc_antenna,
            self._enqueue_rc_system,
            self._enqueue_rc_memory,
            self._enqueue_rc_misc,
        ):
            result = handler(name, params, q, radio)
            if result is not None:
                return result

        raise ValueError(f"unhandled command: {name!r}")

    # ------------------------------------------------------------------
    # Read-only commands (no command queue needed)
    # ------------------------------------------------------------------

    async def _enqueue_read_only(
        self,
        name: str,
        params: dict[str, Any],
        radio: "Radio | None",
    ) -> dict[str, Any] | None:
        """Handle get_* and CW text commands that bypass the command queue.

        Returns None if *name* is not a read-only command.
        """
        handler = self._READ_ONLY_HANDLERS.get(name)
        if handler is None:
            return None
        return await handler(self, params, radio)

    # ------------------------------------------------------------------
    # Read-only command handlers (one per command, dispatched via table)
    # ------------------------------------------------------------------

    async def _ro_get_system_date(
        self, params: dict[str, Any], radio: "Radio | None"
    ) -> dict[str, Any]:
        if radio is None:
            raise RuntimeError("radio connection not available")
        if CAP_SYSTEM_SETTINGS not in radio.capabilities:
            raise RuntimeError("radio does not support this command")
        year, month, day = await radio.get_system_date()
        return {"year": year, "month": month, "day": day}

    async def _ro_get_system_time(
        self, params: dict[str, Any], radio: "Radio | None"
    ) -> dict[str, Any]:
        if radio is None:
            raise RuntimeError("radio connection not available")
        if CAP_SYSTEM_SETTINGS not in radio.capabilities:
            raise RuntimeError("radio does not support this command")
        hour, minute = await radio.get_system_time()
        return {"hour": hour, "minute": minute}

    async def _ro_get_dual_watch(
        self, params: dict[str, Any], radio: "Radio | None"
    ) -> dict[str, Any]:
        if radio is None:
            raise RuntimeError("radio connection not available")
        if CAP_DUAL_WATCH not in radio.capabilities:
            raise RuntimeError("radio does not support this command")
        on = await radio.get_dual_watch()
        return {"on": on}

    async def _ro_get_tuner_status(
        self, params: dict[str, Any], radio: "Radio | None"
    ) -> dict[str, Any]:
        if radio is None:
            raise RuntimeError("radio connection not available")
        if CAP_TUNER not in radio.capabilities:
            raise RuntimeError("radio does not support this command")
        status = await radio.get_tuner_status()
        label = {0: "OFF", 1: "ON", 2: "TUNING"}.get(status, "UNKNOWN")
        return {"status": status, "label": label}

    async def _ro_send_cw_text(
        self, params: dict[str, Any], radio: "Radio | None"
    ) -> dict[str, Any]:
        if radio is None:
            raise RuntimeError("radio connection not available")
        if CAP_CW not in radio.capabilities:
            raise RuntimeError("radio does not support this command")
        text = str(params.get("text", ""))
        if len(text) > _MAX_CW_TEXT_CHARS:
            raise ValueError(
                "CW text too long: "
                f"max {_MAX_CW_TEXT_CHARS} characters, got {len(text)}"
            )
        await radio.send_cw_text(text)
        return {"text": text}

    async def _ro_stop_cw_text(
        self, params: dict[str, Any], radio: "Radio | None"
    ) -> dict[str, Any]:
        if radio is None:
            raise RuntimeError("radio connection not available")
        if CAP_CW not in radio.capabilities:
            raise RuntimeError("radio does not support this command")
        await radio.stop_cw_text()
        return {}

    async def _ro_get_break_in_delay(
        self, params: dict[str, Any], radio: "Radio | None"
    ) -> dict[str, Any]:
        if radio is None:
            raise RuntimeError("radio connection not available")
        if CAP_BREAK_IN not in radio.capabilities:
            raise RuntimeError("radio does not support this command")
        level = await radio.get_break_in_delay()
        return {"level": level}

    async def _ro_get_dash_ratio(
        self, params: dict[str, Any], radio: "Radio | None"
    ) -> dict[str, Any]:
        if radio is None:
            raise RuntimeError("radio connection not available")
        if CAP_CW not in radio.capabilities:
            raise RuntimeError("radio does not support this command")
        value = await radio.get_dash_ratio()
        return {"value": value}

    async def _get_mod_level(self, attr: str, radio: "Radio | None") -> dict[str, Any]:
        # Shared body for get_acc1_mod_level / get_usb_mod_level / get_lan_mod_level.
        if radio is None:
            raise RuntimeError("radio connection not available")
        if CAP_DATA_MODE not in radio.capabilities:
            raise RuntimeError("radio does not support this command")
        level = await getattr(radio, attr)()
        return {"level": level}

    async def _ro_get_acc1_mod_level(
        self, params: dict[str, Any], radio: "Radio | None"
    ) -> dict[str, Any]:
        return await self._get_mod_level("get_acc1_mod_level", radio)

    async def _ro_get_usb_mod_level(
        self, params: dict[str, Any], radio: "Radio | None"
    ) -> dict[str, Any]:
        return await self._get_mod_level("get_usb_mod_level", radio)

    async def _ro_get_lan_mod_level(
        self, params: dict[str, Any], radio: "Radio | None"
    ) -> dict[str, Any]:
        return await self._get_mod_level("get_lan_mod_level", radio)

    async def _get_data_mod_input(
        self, attr: str, radio: "Radio | None"
    ) -> dict[str, Any]:
        # Shared body for get_data{off,1,2,3}_mod_input.
        if radio is None:
            raise RuntimeError("radio connection not available")
        if CAP_DATA_MODE not in radio.capabilities:
            raise RuntimeError("radio does not support this command")
        source = await getattr(radio, attr)()
        return {"source": source}

    async def _ro_get_data_off_mod_input(
        self, params: dict[str, Any], radio: "Radio | None"
    ) -> dict[str, Any]:
        return await self._get_data_mod_input("get_data_off_mod_input", radio)

    async def _ro_get_data1_mod_input(
        self, params: dict[str, Any], radio: "Radio | None"
    ) -> dict[str, Any]:
        return await self._get_data_mod_input("get_data1_mod_input", radio)

    async def _ro_get_data2_mod_input(
        self, params: dict[str, Any], radio: "Radio | None"
    ) -> dict[str, Any]:
        return await self._get_data_mod_input("get_data2_mod_input", radio)

    async def _ro_get_data3_mod_input(
        self, params: dict[str, Any], radio: "Radio | None"
    ) -> dict[str, Any]:
        return await self._get_data_mod_input("get_data3_mod_input", radio)

    async def _ro_set_tuner_status(
        self, params: dict[str, Any], radio: "Radio | None"
    ) -> dict[str, Any]:
        if "value" not in params:
            raise ValueError("missing required 'value' parameter")
        value = int(params["value"])
        if value not in (0, 1, 2):
            raise ValueError(f"tuner value must be 0, 1, or 2, got {value}")
        if self._read_only and value == 2:
            raise PermissionError("read-only mode: set_tuner_status TUNING rejected")
        # Try direct call if the radio has the method
        if radio is not None and CAP_TUNER in radio.capabilities:
            await radio.set_tuner_status(value)
        else:
            # Route through command queue
            from ..radio_poller import SetTunerStatus  # noqa: TID251

            q = self._server.command_queue if self._server is not None else None
            if q is None:
                raise RuntimeError("no command queue available")
            q.put(SetTunerStatus(value))
        label = {0: "OFF", 1: "ON", 2: "TUNING"}[value]
        return {"value": value, "label": label}

    async def _ro_get_ref_adjust(
        self, params: dict[str, Any], radio: "Radio | None"
    ) -> dict[str, Any]:
        if radio is None:
            raise RuntimeError("radio connection not available")
        if CAP_SYSTEM_SETTINGS not in radio.capabilities:
            raise RuntimeError("radio does not support this command")
        value = await radio.get_ref_adjust()
        return {"value": value}

    async def _ro_get_civ_transceive(
        self, params: dict[str, Any], radio: "Radio | None"
    ) -> dict[str, Any]:
        if radio is None:
            raise RuntimeError("radio connection not available")
        if CAP_SYSTEM_SETTINGS not in radio.capabilities:
            raise RuntimeError("radio does not support this command")
        on = await radio.get_civ_transceive()
        return {"on": on}

    async def _ro_get_civ_output_ant(
        self, params: dict[str, Any], radio: "Radio | None"
    ) -> dict[str, Any]:
        if radio is None:
            raise RuntimeError("radio connection not available")
        if CAP_ANTENNA not in radio.capabilities:
            raise RuntimeError("radio does not support this command")
        on = await radio.get_civ_output_ant()
        return {"on": on}

    async def _ro_get_af_mute(
        self, params: dict[str, Any], radio: "Radio | None"
    ) -> dict[str, Any]:
        if radio is None:
            raise RuntimeError("radio connection not available")
        if CAP_AF_LEVEL not in radio.capabilities:
            raise RuntimeError("radio does not support this command")
        rx = int(params.get("receiver", 0))
        self._ensure_receiver_supported(rx)
        on = await radio.get_af_mute(receiver=rx)
        return {"on": on, "receiver": rx}

    async def _ro_get_tuning_step(
        self, params: dict[str, Any], radio: "Radio | None"
    ) -> dict[str, Any]:
        if radio is None:
            raise RuntimeError("radio connection not available")
        if CAP_TUNING_STEP not in radio.capabilities:
            raise RuntimeError("radio does not support this command")
        step = await radio.get_tuning_step()
        return {"step": step}

    async def _ro_get_utc_offset(
        self, params: dict[str, Any], radio: "Radio | None"
    ) -> dict[str, Any]:
        if radio is None:
            raise RuntimeError("radio connection not available")
        if CAP_SYSTEM_SETTINGS not in radio.capabilities:
            raise RuntimeError("radio does not support this command")
        hours, minutes, is_negative = await radio.get_utc_offset()
        return {"hours": hours, "minutes": minutes, "is_negative": is_negative}

    async def _ro_get_band_edge_freq(
        self, params: dict[str, Any], radio: "Radio | None"
    ) -> dict[str, Any]:
        if radio is None:
            raise RuntimeError("radio connection not available")
        if CAP_BAND_EDGE not in radio.capabilities:
            raise RuntimeError("radio does not support this command")
        freq = await radio.get_band_edge_freq()
        return {"freq": freq}

    async def _ro_get_xfc_status(
        self, params: dict[str, Any], radio: "Radio | None"
    ) -> dict[str, Any]:
        if radio is None:
            raise RuntimeError("radio connection not available")
        if CAP_XFC not in radio.capabilities:
            raise RuntimeError("radio does not support this command")
        on = await radio.get_xfc_status()
        return {"on": on}

    async def _ro_get_tx_freq_monitor(
        self, params: dict[str, Any], radio: "Radio | None"
    ) -> dict[str, Any]:
        if radio is None:
            raise RuntimeError("radio connection not available")
        if CAP_TX not in radio.capabilities:
            raise RuntimeError("radio does not support this command")
        on = await radio.get_tx_freq_monitor()
        return {"on": on}

    async def _ro_cw_auto_tune(
        self, params: dict[str, Any], radio: "Radio | None"
    ) -> dict[str, Any]:
        return await self._cw_auto_tune()

    # Dispatch table: command name → read-only handler.
    # Entries returning None mean the command is not handled here and the
    # caller (``_enqueue_command``) routes it through the command queue.
    _READ_ONLY_HANDLERS: ClassVar[
        dict[
            str,
            Callable[
                ["ControlHandler", dict[str, Any], "Radio | None"],
                Awaitable[dict[str, Any]],
            ],
        ]
    ] = {
        "get_system_date": _ro_get_system_date,
        "get_system_time": _ro_get_system_time,
        "get_dual_watch": _ro_get_dual_watch,
        "get_tuner_status": _ro_get_tuner_status,
        "send_cw_text": _ro_send_cw_text,
        "stop_cw_text": _ro_stop_cw_text,
        "get_break_in_delay": _ro_get_break_in_delay,
        "get_dash_ratio": _ro_get_dash_ratio,
        "get_acc1_mod_level": _ro_get_acc1_mod_level,
        "get_usb_mod_level": _ro_get_usb_mod_level,
        "get_lan_mod_level": _ro_get_lan_mod_level,
        "get_data_off_mod_input": _ro_get_data_off_mod_input,
        "get_data1_mod_input": _ro_get_data1_mod_input,
        "get_data2_mod_input": _ro_get_data2_mod_input,
        "get_data3_mod_input": _ro_get_data3_mod_input,
        "set_tuner_status": _ro_set_tuner_status,
        "get_ref_adjust": _ro_get_ref_adjust,
        "get_civ_transceive": _ro_get_civ_transceive,
        "get_civ_output_ant": _ro_get_civ_output_ant,
        "get_af_mute": _ro_get_af_mute,
        "get_tuning_step": _ro_get_tuning_step,
        "get_utc_offset": _ro_get_utc_offset,
        "get_band_edge_freq": _ro_get_band_edge_freq,
        "get_xfc_status": _ro_get_xfc_status,
        "get_tx_freq_monitor": _ro_get_tx_freq_monitor,
        "cw_auto_tune": _ro_cw_auto_tune,
    }

    async def _cw_auto_tune(self) -> dict[str, Any]:
        """Detect CW tone via FFT and shift VFO to zero-beat."""
        from ...cw_auto_tuner import CwAutoTuner

        if self._server is None:
            raise RuntimeError("server not available")

        broadcaster = self._server._audio_broadcaster
        tuner = CwAutoTuner()

        done: asyncio.Event = asyncio.Event()
        result: list[int | None] = []

        def _on_detected(hz: int | None) -> None:
            result.append(hz)
            done.set()

        tuner.start_collection(_on_detected)
        tap_handle = broadcaster._tap_registry.register(
            "cw_auto_tune",
            tuner.feed_audio,
        )
        await broadcaster.ensure_relay()

        try:
            await asyncio.wait_for(done.wait(), timeout=3.0)
        except asyncio.TimeoutError:
            tuner.cancel()
            return {"detected": None, "applied": False}
        finally:
            broadcaster._tap_registry.unregister(tap_handle)

        hz = result[0] if result else None
        if hz is None:
            return {"detected": None, "applied": False}

        # Read current CW pitch from state, compute VFO shift
        state = self._server._radio_state
        cw_pitch = state.cw_pitch if state.cw_pitch else 600
        delta = hz - cw_pitch

        if abs(delta) > 5:
            # Shift VFO frequency to zero-beat
            freq = state.main.freq
            q = self._server.command_queue
            q.put(SetFreq(freq + delta))

        return {
            "detected": hz,
            "cw_pitch": cw_pitch,
            "delta": delta,
            "applied": abs(delta) > 5,
        }

    # ------------------------------------------------------------------
    # Frequency / mode / band / VFO / RIT / split
    # ------------------------------------------------------------------

    def _enqueue_rc_frequency(
        self,
        name: str,
        params: dict[str, Any],
        q: Any,
        radio: "Radio | None",
    ) -> dict[str, Any] | None:
        match name:
            case "set_band":
                band = int(params["band"])
                q.put(SetBand(band))
                return {"band": band}
            case "set_freq":
                freq = int(params["freq"])
                rx = int(params.get("receiver", 0))
                self._ensure_receiver_supported(rx)
                q.put(SetFreq(freq, receiver=rx))
                return {"freq": freq, "receiver": rx}
            case "set_mode":
                mode = str(params["mode"])
                rx = int(params.get("receiver", 0))
                self._ensure_receiver_supported(rx)
                q.put(SetMode(mode, receiver=rx))
                return {"mode": mode, "receiver": rx}
            case "set_filter":
                fil_str = str(params.get("filter", "FIL1"))
                fil_num = int(fil_str[-1]) if fil_str[-1].isdigit() else 1
                rx = int(params.get("receiver", 0))
                self._ensure_receiver_supported(rx)
                q.put(SetFilter(fil_num, receiver=rx))
                return {"filter": fil_str, "receiver": rx}
            case "set_filter_width":
                width = int(params["width"])
                rx = int(params.get("receiver", 0))
                self._ensure_receiver_supported(rx)
                q.put(SetFilterWidth(width, receiver=rx))
                return {"width": width, "receiver": rx}
            case "set_filter_shape":
                shape = int(params["shape"])
                rx = int(params.get("receiver", 0))
                self._ensure_capability("filter_shape", "set_filter_shape")
                self._ensure_receiver_supported(rx)
                q.put(SetFilterShape(shape, receiver=rx))
                return {"shape": shape, "receiver": rx}
            case "set_if_shift":
                offset = int(params["offset"])
                rx = int(params.get("receiver", 0))
                self._ensure_capability("if_shift", "set_if_shift")
                self._ensure_receiver_supported(rx)
                q.put(SetIfShift(offset, receiver=rx))
                return {"offset": offset, "receiver": rx}
            case "set_rit_status":
                on = bool(params.get("on", False))
                self._ensure_capability("rit", "set_rit_status")
                q.put(SetRitStatus(on))
                return {"on": on}
            case "set_rit_tx_status":
                on = bool(params.get("on", False))
                self._ensure_capability("rit", "set_rit_tx_status")
                q.put(SetRitTxStatus(on))
                return {"on": on}
            case "set_rit_frequency":
                freq = int(params.get("freq", 0))
                self._ensure_capability("rit", "set_rit_frequency")
                q.put(SetRitFrequency(freq))
                return {"freq": freq}
            case "set_split":
                on = bool(params.get("on", False))
                self._ensure_capability("split", "set_split")
                q.put(SetSplit(on))
                return {"on": on}
            case "set_vfo" | "select_vfo":
                vfo = str(params.get("vfo", "A"))
                q.put(SelectVfo(vfo))
                return {"vfo": vfo}
            case "vfo_swap":
                q.put(VfoSwap())
                return {}
            case "vfo_equalize":
                q.put(VfoEqualize())
                return {}
            case "set_data_mode":
                dm = int(params["mode"])
                rx = int(params.get("receiver", 0))
                self._ensure_capability("data_mode", "set_data_mode")
                self._ensure_receiver_supported(rx)
                q.put(SetDataMode(dm, receiver=rx))
                return {"mode": dm, "receiver": rx}
            case _:
                return None

    # ------------------------------------------------------------------
    # Power / PTT / RF power
    # ------------------------------------------------------------------

    def _enqueue_rc_power(
        self,
        name: str,
        params: dict[str, Any],
        q: Any,
        radio: "Radio | None",
    ) -> dict[str, Any] | None:
        match name:
            case "ptt":
                if self._read_only:
                    raise PermissionError("read-only mode: PTT rejected")
                on = bool(params["state"])
                logger.info("handler: PTT %s received", "ON" if on else "OFF")
                q.put(PttOn() if on else PttOff())
                return {"state": on}
            case "ptt_on":
                if self._read_only:
                    raise PermissionError("read-only mode: PTT rejected")
                q.put(PttOn())
                return {}
            case "ptt_off":
                if self._read_only:
                    raise PermissionError("read-only mode: PTT rejected")
                q.put(PttOff())
                return {}
            case "set_rf_power" | "set_power":
                if radio is None:
                    raise RuntimeError("radio connection not available")
                if CAP_POWER_CONTROL not in radio.capabilities:
                    raise ValueError(
                        "command set_rf_power is not supported by this radio "
                        "(missing power_control capability)"
                    )
                level = int(params["level"])
                # Tag the unit per radio's wire-level scale. Icom CI-V
                # backends expose ``native_power_unit = "raw_255"``,
                # Yaesu CAT exposes ``"watts"`` — see
                # :class:`PowerControlCapable.native_power_unit`. Falls
                # back to ``"raw_255"`` when the attribute is missing
                # (e.g. legacy mocks) to preserve prior default
                # behaviour for Icom-shaped radios.
                unit: Literal["raw_255", "watts"] = getattr(
                    radio, "native_power_unit", "raw_255"
                )
                q.put(SetPower(level, unit=unit))
                return {"level": level}
            case "set_powerstat":
                if radio is None:
                    raise RuntimeError("radio connection not available")
                if CAP_POWER_CONTROL not in radio.capabilities:
                    raise ValueError(
                        "command set_powerstat is not supported by this radio "
                        "(missing power_control capability)"
                    )
                on = bool(params.get("on", True))
                q.put(SetPowerstat(on))
                return {"on": on}
            case "set_drive_gain":
                level = int(params["level"])
                self._ensure_capability("drive_gain", "set_drive_gain")
                q.put(SetDriveGain(level))
                return {"level": level}
            case _:
                return None

    # ------------------------------------------------------------------
    # DSP: NR / NB / AGC / notch / PBT / IF-shift / digisel / IP+
    # ------------------------------------------------------------------

    def _enqueue_rc_dsp(
        self,
        name: str,
        params: dict[str, Any],
        q: Any,
        radio: "Radio | None",
    ) -> dict[str, Any] | None:
        match name:
            case "set_nb":
                on = bool(params.get("on", False))
                rx = int(params.get("receiver", 0))
                self._ensure_capability("nb", "set_nb")
                self._ensure_receiver_supported(rx)
                q.put(SetNB(on, receiver=rx))
                return {"on": on, "receiver": rx}
            case "set_nr":
                on = bool(params.get("on", False))
                rx = int(params.get("receiver", 0))
                self._ensure_capability("nr", "set_nr")
                self._ensure_receiver_supported(rx)
                q.put(SetNR(on, receiver=rx))
                return {"on": on, "receiver": rx}
            case "set_nr_level":
                level = int(params["level"])
                rx = int(params.get("receiver", 0))
                self._ensure_capability("nr", "set_nr_level")
                self._ensure_receiver_supported(rx)
                q.put(SetNRLevel(level, receiver=rx))
                return {"level": level, "receiver": rx}
            case "set_nb_level":
                level = int(params["level"])
                rx = int(params.get("receiver", 0))
                self._ensure_capability("nb", "set_nb_level")
                self._ensure_receiver_supported(rx)
                q.put(SetNBLevel(level, receiver=rx))
                return {"level": level, "receiver": rx}
            case "set_nb_depth":
                level = int(params["level"])
                rx = int(params.get("receiver", 0))
                self._ensure_capability("nb", "set_nb_depth")
                self._ensure_receiver_supported(rx)
                q.put(SetNbDepth(level, receiver=rx))
                return {"level": level, "receiver": rx}
            case "set_nb_width":
                level = int(params["level"])
                rx = int(params.get("receiver", 0))
                self._ensure_capability("nb", "set_nb_width")
                self._ensure_receiver_supported(rx)
                q.put(SetNbWidth(level, receiver=rx))
                return {"level": level, "receiver": rx}
            case "set_auto_notch":
                on = bool(params.get("on", False))
                rx = int(params.get("receiver", 0))
                self._ensure_capability("notch", "set_auto_notch")
                self._ensure_receiver_supported(rx)
                q.put(SetAutoNotch(on, receiver=rx))
                return {"on": on, "receiver": rx}
            case "set_manual_notch":
                on = bool(params.get("on", False))
                rx = int(params.get("receiver", 0))
                self._ensure_capability("notch", "set_manual_notch")
                self._ensure_receiver_supported(rx)
                q.put(SetManualNotch(on, receiver=rx))
                return {"on": on, "receiver": rx}
            case "set_notch_filter":
                level = int(params["value"])
                self._ensure_capability("notch", "set_notch_filter")
                q.put(SetNotchFilter(level))
                return {"value": level}
            case "set_manual_notch_width":
                value = int(params["value"])
                rx = int(params.get("receiver", 0))
                self._ensure_capability("notch", "set_manual_notch_width")
                self._ensure_receiver_supported(rx)
                q.put(SetManualNotchWidth(value, receiver=rx))
                return {"value": value, "receiver": rx}
            case "set_digisel":
                on = bool(params.get("on", False))
                rx = int(params.get("receiver", 0))
                self._ensure_capability("digisel", "set_digisel")
                self._ensure_receiver_supported(rx)
                q.put(SetDigiSel(on, receiver=rx))
                return {"on": on, "receiver": rx}
            case "set_digisel_shift":
                level = int(params["level"])
                rx = int(params.get("receiver", 0))
                self._ensure_capability("digisel", "set_digisel_shift")
                self._ensure_receiver_supported(rx)
                q.put(SetDigiselShift(level, receiver=rx))
                return {"level": level, "receiver": rx}
            case "set_ip_plus" | "set_ipplus":
                on = bool(params.get("on", False))
                rx = int(params.get("receiver", 0))
                self._ensure_capability("ip_plus", "set_ip_plus")
                self._ensure_receiver_supported(rx)
                q.put(SetIpPlus(on, receiver=rx))
                return {"on": on, "receiver": rx}
            case "set_pbt_inner":
                level = int(params["value"])
                rx = int(params.get("receiver", 0))
                self._ensure_capability("pbt", "set_pbt_inner")
                self._ensure_receiver_supported(rx)
                q.put(SetPbtInner(level, receiver=rx))
                return {"value": level, "receiver": rx}
            case "set_pbt_outer":
                level = int(params["value"])
                rx = int(params.get("receiver", 0))
                self._ensure_capability("pbt", "set_pbt_outer")
                self._ensure_receiver_supported(rx)
                q.put(SetPbtOuter(level, receiver=rx))
                return {"value": level, "receiver": rx}
            case "set_agc_time_constant":
                value = int(params["value"])
                rx = int(params.get("receiver", 0))
                self._ensure_receiver_supported(rx)
                q.put(SetAgcTimeConstant(value, receiver=rx))
                return {"value": value, "receiver": rx}
            case "set_agc":
                agc_mode = int(params["mode"])
                rx = int(params.get("receiver", 0))
                self._ensure_receiver_supported(rx)
                q.put(SetAgc(agc_mode, receiver=rx))
                return {"mode": agc_mode, "receiver": rx}
            case "set_apf":
                apf_mode = int(params["mode"])
                rx = int(params.get("receiver", 0))
                self._ensure_capability("apf", "set_apf")
                self._ensure_receiver_supported(rx)
                q.put(SetApf(apf_mode, receiver=rx))
                return {"mode": apf_mode, "receiver": rx}
            case "set_audio_peak_filter":
                on = bool(params.get("on", False))
                rx = int(params.get("receiver", 0))
                self._ensure_capability("apf", "set_audio_peak_filter")
                self._ensure_receiver_supported(rx)
                q.put(SetAudioPeakFilter(on, receiver=rx))
                return {"on": on, "receiver": rx}
            case "set_twin_peak":
                on = bool(params.get("on", False))
                rx = int(params.get("receiver", 0))
                self._ensure_capability("twin_peak", "set_twin_peak")
                self._ensure_receiver_supported(rx)
                q.put(SetTwinPeak(on, receiver=rx))
                return {"on": on, "receiver": rx}
            case _:
                return None

    # ------------------------------------------------------------------
    # Audio: AF level / squelch / monitor / mute / mic / compressor
    # ------------------------------------------------------------------

    def _enqueue_rc_audio(
        self,
        name: str,
        params: dict[str, Any],
        q: Any,
        radio: "Radio | None",
    ) -> dict[str, Any] | None:
        match name:
            case "set_rf_gain":
                if radio is None:
                    raise RuntimeError("radio connection not available")
                if CAP_RF_GAIN not in radio.capabilities:
                    raise ValueError(
                        "command set_rf_gain is not supported by this radio "
                        "(missing rf_gain capability)"
                    )
                level = int(params["level"])
                rx = int(params.get("receiver", 0))
                self._ensure_capability("rf_gain", "set_rf_gain")
                self._ensure_receiver_supported(rx)
                q.put(SetRfGain(level, receiver=rx))
                return {"level": level, "receiver": rx}
            case "set_af_level":
                if radio is None:
                    raise RuntimeError("radio connection not available")
                if CAP_AF_LEVEL not in radio.capabilities:
                    raise ValueError(
                        "command set_af_level is not supported by this radio "
                        "(missing af_level capability)"
                    )
                level = int(params["level"])
                rx = int(params.get("receiver", 0))
                self._ensure_capability("af_level", "set_af_level")
                self._ensure_receiver_supported(rx)
                q.put(SetAfLevel(level, receiver=rx))
                return {"level": level, "receiver": rx}
            case "set_sql" | "set_squelch":
                if radio is None:
                    raise RuntimeError("radio connection not available")
                if CAP_SQUELCH not in radio.capabilities:
                    raise ValueError(
                        f"command {name!r} is not supported by this radio "
                        "(missing squelch capability)"
                    )
                level = int(params["level"])
                rx = int(params.get("receiver", 0))
                self._ensure_capability("squelch", name)
                self._ensure_receiver_supported(rx)
                q.put(SetSquelch(level, receiver=rx))
                return {"level": level, "receiver": rx}
            case "set_mic_gain":
                level = int(params["level"])
                q.put(SetMicGain(level))
                return {"level": level}
            case "set_compressor_level":
                level = int(params["level"])
                self._ensure_capability("compressor", "set_compressor_level")
                q.put(SetCompressorLevel(level))
                return {"level": level}
            case "set_comp" | "set_compressor":
                on = bool(params.get("on", True))
                q.put(SetCompressor(on))
                return {"on": on}
            case "set_monitor":
                on = bool(params.get("on", False))
                self._ensure_capability("monitor", "set_monitor")
                q.put(SetMonitor(on))
                return {"on": on}
            case "set_monitor_gain":
                level = int(params["level"])
                self._ensure_capability("monitor", "set_monitor_gain")
                q.put(SetMonitorGain(level))
                return {"level": level}
            case "set_af_mute":
                on = bool(params["on"])
                rx = int(params.get("receiver", 0))
                self._ensure_receiver_supported(rx)
                q.put(SetAfMute(on, receiver=rx))
                return {"on": on, "receiver": rx}
            case "set_acc1_mod_level":
                level = int(params["level"])
                q.put(SetAcc1ModLevel(level))
                return {"level": level}
            case "set_usb_mod_level":
                level = int(params["level"])
                q.put(SetUsbModLevel(level))
                return {"level": level}
            case "set_lan_mod_level":
                level = int(params["level"])
                q.put(SetLanModLevel(level))
                return {"level": level}
            case "set_data_off_mod_input":
                source = int(params["source"])
                q.put(SetDataOffModInput(source))
                return {"source": source}
            case "set_data1_mod_input":
                source = int(params["source"])
                q.put(SetData1ModInput(source))
                return {"source": source}
            case "set_data2_mod_input":
                source = int(params["source"])
                q.put(SetData2ModInput(source))
                return {"source": source}
            case "set_data3_mod_input":
                source = int(params["source"])
                q.put(SetData3ModInput(source))
                return {"source": source}
            case "set_ssb_tx_bw":
                value = int(params["value"])
                self._ensure_capability("ssb_tx_bw", "set_ssb_tx_bw")
                q.put(SetSsbTxBandwidth(value))
                return {"value": value}
            case _:
                return None

    # ------------------------------------------------------------------
    # Scope commands
    # ------------------------------------------------------------------

    def _enqueue_rc_scope(
        self,
        name: str,
        params: dict[str, Any],
        q: Any,
        radio: "Radio | None",
    ) -> dict[str, Any] | None:
        match name:
            case "switch_scope_receiver":
                receiver = int(params.get("receiver", 0))
                self._ensure_capability("scope", "switch_scope_receiver")
                self._ensure_receiver_supported(receiver)
                q.put(SwitchScopeReceiver(receiver))
                return {"receiver": receiver}
            case "set_scope_during_tx":
                on = bool(params["on"])
                self._ensure_capability("scope", "set_scope_during_tx")
                q.put(SetScopeDuringTx(on))
                return {"on": on}
            case "set_scope_center_type":
                center_type = int(params["center_type"])
                self._ensure_capability("scope", "set_scope_center_type")
                q.put(SetScopeCenterType(center_type))
                return {"center_type": center_type}
            case "set_scope_edge":
                edge = int(params["edge"])
                self._ensure_capability("scope", "set_scope_edge")
                q.put(SetScopeEdge(edge))
                return {"edge": edge}
            case "set_scope_fixed_edge":
                edge = int(params["edge"])
                start_hz = int(params["start_hz"])
                end_hz = int(params["end_hz"])
                self._ensure_capability("scope", "set_scope_fixed_edge")
                q.put(SetScopeFixedEdge(edge, start_hz, end_hz))
                return {"edge": edge, "start_hz": start_hz, "end_hz": end_hz}
            case "set_scope_vbw":
                narrow = bool(params.get("narrow", False))
                self._ensure_capability("scope", "set_scope_vbw")
                q.put(SetScopeVbw(narrow))
                return {"narrow": narrow}
            case "set_scope_rbw":
                rbw = int(params.get("rbw", 0))
                self._ensure_capability("scope", "set_scope_rbw")
                q.put(SetScopeRbw(rbw))
                return {"rbw": rbw}
            case "set_scope_dual":
                dual = bool(params["dual"])
                self._ensure_capability("scope", "set_scope_dual")
                q.put(SetScopeDual(dual))
                return {"dual": dual}
            case "set_scope_mode":
                scope_mode = int(params["mode"])
                self._ensure_capability("scope", "set_scope_mode")
                q.put(SetScopeMode(scope_mode))
                return {"mode": scope_mode}
            case "set_scope_span":
                span = int(params["span"])
                self._ensure_capability("scope", "set_scope_span")
                q.put(SetScopeSpan(span))
                return {"span": span}
            case "set_scope_speed":
                speed = int(params["speed"])
                self._ensure_capability("scope", "set_scope_speed")
                q.put(SetScopeSpeed(speed))
                return {"speed": speed}
            case "set_scope_ref":
                ref = int(params["ref"])
                self._ensure_capability("scope", "set_scope_ref")
                q.put(SetScopeRef(ref))
                return {"ref": ref}
            case "set_scope_hold":
                on = bool(params["on"])
                self._ensure_capability("scope", "set_scope_hold")
                q.put(SetScopeHold(on))
                return {"on": on}
            case _:
                return None

    # ------------------------------------------------------------------
    # Antenna: attenuator / preamp / antenna select
    # ------------------------------------------------------------------

    def _enqueue_rc_antenna(
        self,
        name: str,
        params: dict[str, Any],
        q: Any,
        radio: "Radio | None",
    ) -> dict[str, Any] | None:
        match name:
            case "set_att" | "set_attenuator":
                db = int(params.get("level", params.get("db", 0)))
                rx = int(params.get("receiver", 0))
                self._ensure_capability("attenuator", name)
                self._ensure_receiver_supported(rx)
                q.put(SetAttenuator(db, receiver=rx))
                return {"db": db, "receiver": rx}
            case "set_preamp":
                level = int(params["level"])
                rx = int(params.get("receiver", 0))
                self._ensure_capability("preamp", "set_preamp")
                self._ensure_receiver_supported(rx)
                q.put(SetPreamp(level, receiver=rx))
                return {"level": level, "receiver": rx}
            case "set_antenna_1":
                on = bool(params.get("on", False))
                q.put(SetAntenna1(on))
                return {"on": on}
            case "set_antenna_2":
                on = bool(params.get("on", False))
                q.put(SetAntenna2(on))
                return {"on": on}
            case "set_rx_antenna_ant1":
                on = bool(params.get("on", False))
                q.put(SetRxAntennaAnt1(on))
                return {"on": on}
            case "set_rx_antenna_ant2":
                on = bool(params.get("on", False))
                q.put(SetRxAntennaAnt2(on))
                return {"on": on}
            case "set_rx_antenna":
                antenna = int(params["antenna"])
                on = bool(params.get("on", False))
                self._ensure_capability("rx_antenna", "set_rx_antenna")
                q.put(SetRxAntenna(antenna, on))
                return {"antenna": antenna, "on": on}
            case "set_civ_output_ant":
                on = bool(params["on"])
                q.put(SetCivOutputAnt(on))
                return {"on": on}
            case _:
                return None

    # ------------------------------------------------------------------
    # System: date/time / CW / VOX / dial-lock / dual-watch / scan
    # ------------------------------------------------------------------

    def _enqueue_rc_system(
        self,
        name: str,
        params: dict[str, Any],
        q: Any,
        radio: "Radio | None",
    ) -> dict[str, Any] | None:
        match name:
            case "set_system_date":
                year = int(params["year"])
                month = int(params["month"])
                day = int(params["day"])
                q.put(SetSystemDate(year, month, day))
                return {"year": year, "month": month, "day": day}
            case "set_system_time":
                hour = int(params["hour"])
                minute = int(params["minute"])
                q.put(SetSystemTime(hour, minute))
                return {"hour": hour, "minute": minute}
            case "set_cw_pitch":
                value = int(params["value"])
                self._ensure_capability("cw", "set_cw_pitch")
                q.put(SetCwPitch(value))
                return {"value": value}
            case "set_key_speed":
                speed = int(params["speed"])
                self._ensure_capability("cw", "set_key_speed")
                q.put(SetKeySpeed(speed))
                return {"speed": speed}
            case "set_break_in":
                break_in_mode = int(params["mode"])
                self._ensure_capability("break_in", "set_break_in")
                q.put(SetBreakIn(break_in_mode))
                return {"mode": break_in_mode}
            case "set_break_in_delay":
                level = int(params["level"])
                self._ensure_capability("break_in", "set_break_in_delay")
                q.put(SetBreakInDelay(level))
                return {"level": level}
            case "set_dash_ratio":
                value = int(params["value"])
                self._ensure_capability("cw", "set_dash_ratio")
                q.put(SetDashRatio(value))
                return {"value": value}
            case "set_vox":
                on = bool(params.get("on", False))
                self._ensure_capability("vox", "set_vox")
                q.put(SetVox(on))
                return {"on": on}
            case "set_vox_gain":
                level = int(params["level"])
                self._ensure_capability("vox", "set_vox_gain")
                q.put(SetVoxGain(level))
                return {"level": level}
            case "set_anti_vox_gain":
                level = int(params["level"])
                self._ensure_capability("vox", "set_anti_vox_gain")
                q.put(SetAntiVoxGain(level))
                return {"level": level}
            case "set_vox_delay":
                level = int(params["level"])
                self._ensure_capability("vox", "set_vox_delay")
                q.put(SetVoxDelay(level))
                return {"level": level}
            case "speak":
                mode = int(params.get("mode", 0))
                q.put(Speak(mode))
                return {"mode": mode}
            case "set_dial_lock":
                on = bool(params.get("on", False))
                q.put(SetDialLock(on))
                return {"on": on}
            case "set_dual_watch":
                on = bool(params.get("on", False))
                self._ensure_capability("dual_rx", "set_dual_watch")
                q.put(SetDualWatch(on))
                return {"on": on}
            case "set_main_sub_tracking":
                on = bool(params.get("on", False))
                self._ensure_capability("main_sub_tracking", "set_main_sub_tracking")
                q.put(SetMainSubTracking(on))
                return {"on": on}
            case "scan_start":
                self._ensure_capability("scan", "scan_start")
                scan_type = int(params.get("type", 0x01))
                q.put(ScanStart(scan_type=scan_type))
                return {"type": scan_type}
            case "scan_stop":
                self._ensure_capability("scan", "scan_stop")
                q.put(ScanStop())
                return {}
            case "scan_set_df_span":
                self._ensure_capability("scan", "scan_set_df_span")
                span = int(params["span"])
                if span not in range(0xA1, 0xA8):
                    raise ValueError(
                        f"scan_set_df_span: span must be 0xA1-0xA7, got {span:#x}"
                    )
                q.put(ScanSetDfSpan(span=span))
                return {"span": span}
            case "scan_set_resume":
                self._ensure_capability("scan", "scan_set_resume")
                resume_mode = int(params["mode"])
                if resume_mode not in range(0xD0, 0xD4):
                    raise ValueError(
                        f"scan_set_resume: mode must be 0xD0-0xD3, got {resume_mode:#x}"
                    )
                q.put(ScanSetResume(mode=resume_mode))
                return {"mode": resume_mode}
            case "set_repeater_tone":
                on = bool(params.get("on", False))
                rx = int(params.get("receiver", 0))
                self._ensure_capability("repeater_tone", "set_repeater_tone")
                self._ensure_receiver_supported(rx)
                q.put(SetRepeaterTone(on, receiver=rx))
                return {"on": on, "receiver": rx}
            case "set_tone_freq":
                freq = int(params["freq"])
                rx = int(params.get("receiver", 0))
                self._ensure_capability("repeater_tone", "set_tone_freq")
                self._ensure_receiver_supported(rx)
                q.put(SetToneFreq(freq, receiver=rx))
                return {"freq": freq, "receiver": rx}
            case "set_repeater_tsql":
                on = bool(params.get("on", False))
                rx = int(params.get("receiver", 0))
                self._ensure_capability("tsql", "set_repeater_tsql")
                self._ensure_receiver_supported(rx)
                q.put(SetRepeaterTsql(on, receiver=rx))
                return {"on": on, "receiver": rx}
            case "set_tsql_freq":
                freq = int(params["freq"])
                rx = int(params.get("receiver", 0))
                self._ensure_capability("tsql", "set_tsql_freq")
                self._ensure_receiver_supported(rx)
                q.put(SetTsqlFreq(freq, receiver=rx))
                return {"freq": freq, "receiver": rx}
            case "set_ref_adjust":
                value = int(params["value"])
                q.put(SetRefAdjust(value))
                return {"value": value}
            case "set_civ_transceive":
                on = bool(params["on"])
                q.put(SetCivTransceive(on))
                return {"on": on}
            case "set_tuning_step":
                step = int(params["step"])
                q.put(SetTuningStep(step))
                return {"step": step}
            case "set_utc_offset":
                hours = int(params["hours"])
                minutes = int(params["minutes"])
                is_negative = bool(params["is_negative"])
                q.put(SetUtcOffset(hours, minutes, is_negative))
                return {"hours": hours, "minutes": minutes, "is_negative": is_negative}
            case _:
                return None

    # ------------------------------------------------------------------
    # Memory channels / BSR
    # ------------------------------------------------------------------

    def _enqueue_rc_memory(
        self,
        name: str,
        params: dict[str, Any],
        q: Any,
        radio: "Radio | None",
    ) -> dict[str, Any] | None:
        match name:
            case "set_memory_mode":
                if not isinstance(radio, MemoryCapable):
                    raise ValueError(
                        "command set_memory_mode is not supported by this radio "
                        "(missing MemoryCapable)"
                    )
                channel = int(params["channel"])
                if not 1 <= channel <= 101:
                    raise ValueError(f"channel must be 1-101, got {channel}")
                q.put(SetMemoryMode(channel))
                return {"channel": channel}
            case "memory_write":
                if not isinstance(radio, MemoryCapable):
                    raise ValueError(
                        "command memory_write is not supported by this radio "
                        "(missing MemoryCapable)"
                    )
                q.put(MemoryWrite())
                return {}
            case "memory_to_vfo":
                if not isinstance(radio, MemoryCapable):
                    raise ValueError(
                        "command memory_to_vfo is not supported by this radio "
                        "(missing MemoryCapable)"
                    )
                channel = int(params["channel"])
                if not 1 <= channel <= 101:
                    raise ValueError(f"channel must be 1-101, got {channel}")
                q.put(MemoryToVfo(channel))
                return {"channel": channel}
            case "memory_clear":
                if not isinstance(radio, MemoryCapable):
                    raise ValueError(
                        "command memory_clear is not supported by this radio "
                        "(missing MemoryCapable)"
                    )
                channel = int(params["channel"])
                if not 1 <= channel <= 101:
                    raise ValueError(f"channel must be 1-101, got {channel}")
                q.put(MemoryClear(channel))
                return {"channel": channel}
            case "set_memory_contents":
                if not isinstance(radio, MemoryCapable):
                    raise ValueError(
                        "command set_memory_contents is not supported by this radio "
                        "(missing MemoryCapable)"
                    )
                from ...types import MemoryChannel

                mem = MemoryChannel(**params)
                q.put(SetMemoryContents(mem))
                return {"channel": mem.channel}
            case "set_bsr":
                if not isinstance(radio, MemoryCapable):
                    raise ValueError(
                        "command set_bsr is not supported by this radio "
                        "(missing MemoryCapable)"
                    )
                from ...types import BandStackRegister

                bsr = BandStackRegister(**params)
                q.put(SetBsr(bsr))
                return {"band": bsr.band, "register": bsr.register}
            case _:
                return None

    # ------------------------------------------------------------------
    # Misc: XFC / TX freq monitor / quick split / quick dual watch
    # ------------------------------------------------------------------

    def _enqueue_rc_misc(
        self,
        name: str,
        params: dict[str, Any],
        q: Any,
        radio: "Radio | None",
    ) -> dict[str, Any] | None:
        match name:
            case "set_xfc_status":
                on = bool(params["on"])
                q.put(SetXfcStatus(on))
                return {"on": on}
            case "set_tx_freq_monitor":
                on = bool(params["on"])
                q.put(SetTxFreqMonitor(on))
                return {"on": on}
            case "get_quick_split" | "set_quick_split":
                q.put(QuickSplit())
                return {}
            case "get_quick_dual_watch" | "set_quick_dual_watch":
                q.put(QuickDualWatch())
                return {}
            case "quick_dualwatch":
                self._ensure_capability("dual_rx", "quick_dualwatch")
                q.put(QuickDwTrigger())
                return {}
            case "quick_split":
                self._ensure_capability("dual_rx", "quick_split")
                q.put(QuickSplitTrigger())
                return {}
            case _:
                return None

    @property
    def subscribed_streams(self) -> frozenset[str]:
        """Current subscribed stream names."""
        return frozenset(self._subscribed_streams)
