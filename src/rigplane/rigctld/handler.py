"""Rigctld command handler — dispatches parsed commands to IcomRadio.

Responsibilities:
- Command dispatch table (long_cmd → async handler method)
- Read-only gate (reject set commands with RPRT -22)
- RadioState-first reads with a small handler-local fallback cache
- Error translation (rigplane exceptions → Hamlib error codes)

This module receives RigctldCommand from protocol.py and returns
RigctldResponse. It calls IcomRadio methods but knows nothing about
TCP or wire format.
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Literal, cast

from ..commands import build_civ_frame
from ..exceptions import ConnectionError, TimeoutError
from ..radio_state import RadioState, ReceiverState
from ..types import Mode
from .contract import (  # noqa: TID251
    CIV_TO_HAMLIB_MODE,
    HAMLIB_MODE_MAP,
    HamlibError,
    RigctldCommand,
    RigctldConfig,
    RigctldResponse,
)
from .utils import get_mode_reader  # noqa: TID251

if TYPE_CHECKING:
    from ..radio_protocol import Radio

from ..capabilities import CAP_METERS, CAP_RIT
from .routing import create_routing  # noqa: TID251

__all__ = ["RigctldHandler"]

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# IC-7610 hardcoded dump_state (hamlib protocol v0 positional format)
# ---------------------------------------------------------------------------
# hamlib netrigctl.c parses dump_state as POSITIONAL bare values via atol/sscanf.
# NO 'key: value' pairs — just bare numbers/strings, one per list entry.
#
# Mode bits (RIG_MODE_*): AM=0x01 CW=0x02 USB=0x04 LSB=0x08 RTTY=0x10
#                          FM=0x20 WFM=0x40 CWR=0x80 RTTYR=0x100
# 0x1ff = all nine modes above
#
# has_get_level (32-bit):
#   RIG_LEVEL_PREAMP(0x1) | RIG_LEVEL_ATT(0x2) | RIG_LEVEL_AF(0x8)
#   | RIG_LEVEL_RF(0x10) | RIG_LEVEL_NR(0x100) | RIG_LEVEL_CWPITCH(0x800)
#   | RIG_LEVEL_RFPOWER(0x1000) | RIG_LEVEL_MICGAIN(0x2000)
#   | RIG_LEVEL_KEYSPD(0x4000) | RIG_LEVEL_COMP(0x10000)
#   | RIG_LEVEL_RAWSTR(0x04000000) | RIG_LEVEL_SWR(0x10000000)
#   | RIG_LEVEL_STRENGTH(0x40000000)
#   = 0x5401791B
#   (NB, MONITOR_GAIN, RFPOWER_METER, COMP_METER, ID_METER, VD_METER use
#    64-bit level bits above bit 31 and are handled but not declared here)
#
# has_get_func (32-bit):
#   RIG_FUNC_NB(0x2) | RIG_FUNC_COMP(0x4) | RIG_FUNC_VOX(0x8)
#   | RIG_FUNC_TONE(0x10) | RIG_FUNC_TSQL(0x20) | RIG_FUNC_ANF(0x100)
#   | RIG_FUNC_NR(0x200) | RIG_FUNC_APF(0x800) | RIG_FUNC_MON(0x1000)
#   | RIG_FUNC_LOCK(0x10000)
#   = 0x00011B3E
_IC7610_DUMP_STATE: list[str] = [
    "0",  # protocol version
    "3078",  # rig model (IC-7610)
    "1",  # ITU region
    "100000.000000 60000000.000000 0x1ff -1 -1 0x3 0xf",  # RX range
    "0 0 0 0 0 0 0",  # end of RX ranges
    "1800000.000000 60000000.000000 0x1ff 5000 100000 0x3 0xf",  # TX range
    "0 0 0 0 0 0 0",  # end of TX ranges
    "0x1ff 1",  # tuning step (all modes, 1 Hz)
    "0 0",  # end of tuning steps
    "0x1ff 3000",  # filter: wide 3000 Hz
    "0x1ff 2400",  # filter: normal 2400 Hz
    "0x1ff 1800",  # filter: narrow 1800 Hz
    "0 0",  # end of filters
    "9999",  # max_rit (±9999 Hz, CI-V 0x21)
    "9999",  # max_xit (±9999 Hz, shared register)
    "0",  # max_ifshift
    "0",  # announces
    "12 20 0",  # preamp (dB values, 0-terminated)
    "6 12 18 0",  # attenuator (dB values, 0-terminated)
    "0x00011B3E",  # has_get_func
    "0x00011B3E",  # has_set_func
    "0x5401791B",  # has_get_level
    "0x0001791B",  # has_set_level
    "0",  # has_get_parm
    "0",  # has_set_parm
]


# ---------------------------------------------------------------------------
# Level and function
# ---------------------------------------------------------------------------
# Level and function lookup tables
# ---------------------------------------------------------------------------

# Levels that return raw/255.0 as float (0.0-1.0)
_GET_LEVEL_FLOAT: dict[str, str] = {
    "AF": "get_af_level",
    "RF": "get_rf_gain",
    "SQL": "get_squelch",
    "NR": "get_nr_level",
    "NB": "get_nb_level",
    "COMP": "get_compressor_level",
    "MICGAIN": "get_mic_gain",
    "MONITOR_GAIN": "get_monitor_gain",
    "RFPOWER_METER": "get_power_meter",
    "COMP_METER": "get_comp_meter",
    "ID_METER": "get_id_meter",
    "VD_METER": "get_vd_meter",
}

# Levels that return an integer value as-is (WPM, Hz)
_GET_LEVEL_INT: dict[str, str] = {
    "KEYSPD": "get_key_speed",
    "CWPITCH": "get_cw_pitch",
}

# Writable float levels: hamlib 0.0-1.0 → raw 0-255
_SET_LEVEL_FLOAT: dict[str, str] = {
    "AF": "set_af_level",
    "RF": "set_rf_gain",
    "SQL": "set_squelch",
    "NR": "set_nr_level",
    "NB": "set_nb_level",
    "COMP": "set_compressor_level",
    "MICGAIN": "set_mic_gain",
    "MONITOR_GAIN": "set_monitor_gain",
}

# Preamp: level index → dB (matches dump_state "12 20 0")
_PREAMP_IDX_TO_DB: list[int] = [0, 12, 20]

# Functions: hamlib name → get/set method names
_FUNC_GET: dict[str, str] = {
    "NB": "get_nb",
    "NR": "get_nr",
    "COMP": "get_compressor",
    "VOX": "get_vox",
    "TONE": "get_repeater_tone",
    "TSQL": "get_repeater_tsql",
    "ANF": "get_auto_notch",
    "LOCK": "get_dial_lock",
    "MON": "get_monitor",
    "APF": "get_audio_peak_filter",
}

_FUNC_SET: dict[str, str] = {
    "NB": "set_nb",
    "NR": "set_nr",
    "COMP": "set_compressor",
    "VOX": "set_vox",
    "TONE": "set_repeater_tone",
    "TSQL": "set_repeater_tsql",
    "ANF": "set_auto_notch",
    "LOCK": "set_dial_lock",
    "MON": "set_monitor",
    "APF": "set_audio_peak_filter",
}

# Filter number → approximate passband in Hz (IC-7610 USB defaults)
_FILTER_TO_PASSBAND: dict[int, int] = {1: 3000, 2: 2400, 3: 1800}


def _filter_to_passband(filt: int | None) -> int:
    """Convert IC-7610 filter number to passband Hz (0 = radio default)."""
    if filt is None:
        return 0
    return _FILTER_TO_PASSBAND.get(filt, 0)


def _passband_to_filter(passband_hz: int) -> int | None:
    """Convert passband in Hz to the nearest IC-7610 filter number."""
    if passband_hz <= 0:
        return None
    if passband_hz >= 2800:
        return 1
    if passband_hz >= 2000:
        return 2
    return 3


def _ok() -> RigctldResponse:
    return RigctldResponse(error=HamlibError.OK)


def _err(code: HamlibError) -> RigctldResponse:
    return RigctldResponse(error=code)


def _profile_data_mode_count(radio: Any) -> int:
    profile = getattr(radio, "profile", None)
    count = getattr(profile, "data_mode_count", 1)
    return count if isinstance(count, int) and count > 0 else 1


def _mode_to_hamlib_str(mode: object) -> str:
    """Normalize backend mode values to a hamlib-compatible string."""
    if isinstance(mode, Mode):
        return str(CIV_TO_HAMLIB_MODE.get(mode.value, mode.name))
    if isinstance(mode, str):
        return mode.upper()
    name = getattr(mode, "name", None)
    if isinstance(name, str):
        return name.upper()
    value = getattr(mode, "value", None)
    if isinstance(value, int):
        return str(CIV_TO_HAMLIB_MODE.get(value, "USB"))
    return str(mode).upper()


@dataclass(slots=True)
class _PendingRigState:
    """Local optimistic write-through state until RadioState catches up."""

    freq: int | None = None
    mode: str | None = None
    filter_width: int | None = None
    data_mode: bool | None = None


@dataclass(slots=True)
class _FallbackRigState:
    """Handler-local fallback values used only until RadioState becomes valid."""

    freq: int = 0
    freq_ts: float = 0.0
    mode: str = "USB"
    filter_width: int | None = None
    mode_ts: float = 0.0
    data_mode: bool = False
    data_mode_ts: float = 0.0
    ptt: bool = False
    ptt_ts: float = 0.0
    s_meter: int | None = None
    s_meter_ts: float = 0.0
    rf_power: float | None = None
    rf_power_ts: float = 0.0
    swr: float | None = None
    swr_ts: float = 0.0

    def is_fresh(self, field: str, ttl: float | None) -> bool:
        if ttl is None or ttl <= 0.0:
            return False
        ts = getattr(self, f"{field}_ts", 0.0)
        return ts > 0.0 and (time.monotonic() - ts) < ttl

    def update_freq(self, freq: int) -> None:
        self.freq = freq
        self.freq_ts = time.monotonic()

    def update_mode(self, mode: str, filter_width: int | None) -> None:
        self.mode = mode
        self.filter_width = filter_width
        self.mode_ts = time.monotonic()

    def update_data_mode(self, on: bool) -> None:
        self.data_mode = on
        self.data_mode_ts = time.monotonic()

    def update_ptt(self, on: bool) -> None:
        self.ptt = on
        self.ptt_ts = time.monotonic()

    def update_s_meter(self, raw: int) -> None:
        self.s_meter = raw
        self.s_meter_ts = time.monotonic()

    def update_rf_power(self, value: float) -> None:
        self.rf_power = value
        self.rf_power_ts = time.monotonic()

    def update_swr(self, value: float) -> None:
        self.swr = value
        self.swr_ts = time.monotonic()


# ---------------------------------------------------------------------------
# Raw CI-V helpers
# ---------------------------------------------------------------------------


def _parse_raw_hex(args: tuple[str, ...]) -> bytes:
    """Parse hex bytes from hamlib 'w' command args.

    Supports two input formats:
    - Space-separated tokens: args = ("FE", "FE", "98", "E0", "03", "FD")
    - Backslash-escaped single arg: args = ("\\xFE\\xFE\\x98\\xE0\\x03\\xFD",)
    """
    if len(args) == 1 and "\\x" in args[0]:
        # Backslash-escaped: \xFE\xFE\x98\xE0\x03\xFD
        raw = args[0]
        result = bytearray()
        i = 0
        while i < len(raw):
            if raw[i : i + 2] == "\\x":
                result.append(int(raw[i + 2 : i + 4], 16))
                i += 4
            else:
                raise ValueError(f"Unexpected char at position {i} in {raw!r}")
        return bytes(result)
    # Space-separated hex tokens: FE FE 98 E0 03 FD
    return bytes(int(h, 16) for h in args)


def _civ_frame_to_bytes(frame: Any) -> bytes:
    """Reconstruct raw CI-V frame bytes from a parsed CivFrame."""
    return bytes(
        build_civ_frame(
            frame.to_addr,
            frame.from_addr,
            frame.command,
            sub=frame.sub,
            data=frame.data if frame.data else None,
        )
    )


# ---------------------------------------------------------------------------
# Handler
# ---------------------------------------------------------------------------


class RigctldHandler:
    """Dispatches parsed rigctld commands to IcomRadio.

    Args:
        radio: Connected IcomRadio instance.
        config: Server configuration (read_only, cache_ttl, etc.).
    """

    def __init__(
        self,
        radio: "Radio",
        config: RigctldConfig,
    ) -> None:
        self._radio = radio
        self._config = config
        self._ptt_state: bool | None = None
        # Hamlib-protocol concept: TX VFO label tracked across S/s commands.
        # Not radio state (CI-V has no per-VFO TX-routing register on most
        # Icoms — set_vfo_split routing is via active receiver). Initial
        # value mirrors the Hamlib default ("VFOA"). Updated by
        # ``_cmd_set_split_vfo`` on every ``S`` request. (Issue #1345.)
        self._split_tx_vfo: Literal["VFOA", "VFOB"] = "VFOA"
        self._cache = _FallbackRigState()
        self._pending = _PendingRigState()
        self._routing = create_routing(
            radio, self._cache, getattr(config, "max_power_w", 100.0)
        )

    def _packet_data_mode_value(self) -> int | bool:
        value = self._config.wsjtx_data_mode
        if value is None:
            return True
        if value > _profile_data_mode_count(self._radio):
            return True
        return value

    async def _apply_packet_data_mode(self, *, receiver: int = 0) -> int | bool:
        data_mode = self._packet_data_mode_value()
        source = self._config.wsjtx_data_mod_input
        if source is not None and type(data_mode) is int:
            setter = getattr(self._radio, f"set_data{data_mode}_mod_input", None)
            if setter is not None:
                await setter(source)
            else:
                logger.debug(
                    "rigctld: radio has no set_data%d_mod_input, skipping",
                    data_mode,
                )
        if receiver == 0:
            await self._radio.set_data_mode(data_mode)
        else:
            await self._radio.set_data_mode(data_mode, receiver=receiver)
        return data_mode

    def _radio_state(self) -> RadioState | None:
        state = getattr(self._radio, "radio_state", None)
        return state if isinstance(state, RadioState) else None

    def _main_receiver_state(self) -> ReceiverState | None:
        state = self._radio_state()
        if state is None or state.main.freq <= 0:
            return None
        return state.main

    def _effective_pending_freq(self, main_state: ReceiverState | None) -> int | None:
        pending_freq = self._pending.freq
        if pending_freq is None:
            return None
        if main_state is not None and main_state.freq == pending_freq:
            self._pending.freq = None
            return None
        return pending_freq

    def _effective_pending_mode(
        self, main_state: ReceiverState | None
    ) -> tuple[str, int, int] | None:
        pending_mode = self._pending.mode
        if pending_mode is None:
            return None

        pending_filter = self._pending.filter_width
        pending_data_mode = self._pending.data_mode

        if main_state is not None:
            state_mode = main_state.mode.upper()
            state_filter = main_state.filter
            state_data_mode = main_state.data_mode
            if (
                state_mode == pending_mode
                and state_filter == pending_filter
                and (pending_data_mode is None or state_data_mode == pending_data_mode)
            ):
                self._pending.mode = None
                self._pending.filter_width = None
                self._pending.data_mode = None
                return None

        data_mode = (
            pending_data_mode
            if pending_data_mode is not None
            else (
                main_state.data_mode
                if main_state is not None
                else self._cache.data_mode
            )
        )
        return pending_mode, _filter_to_passband(pending_filter), data_mode

    # ------------------------------------------------------------------
    # Public entry point
    # ------------------------------------------------------------------

    async def execute(self, cmd: RigctldCommand) -> RigctldResponse:
        """Execute a parsed rigctld command and return the response.

        Args:
            cmd: Parsed command from the client.

        Returns:
            Response to send back.
        """
        # Read-only gate
        if self._config.read_only and cmd.is_set:
            logger.debug("read-only: rejecting set command %s", cmd.long_cmd)
            return _err(HamlibError.EACCESS)

        handler_fn = self._DISPATCH.get(cmd.long_cmd)
        if handler_fn is None:
            logger.debug("unimplemented command: %s", cmd.long_cmd)
            return _err(HamlibError.ENIMPL)

        try:
            return cast(RigctldResponse, await handler_fn(self, cmd))
        except ConnectionError:
            logger.warning("I/O error executing %s", cmd.long_cmd)
            return _err(HamlibError.EIO)
        except TimeoutError:
            logger.warning("Timeout executing %s", cmd.long_cmd)
            return _err(HamlibError.ETIMEOUT)
        except ValueError:
            logger.warning("Invalid value in %s", cmd.long_cmd)
            return _err(HamlibError.EINVAL)
        except Exception:
            logger.exception("Internal error executing %s", cmd.long_cmd)
            return _err(HamlibError.EINTERNAL)

    # ------------------------------------------------------------------
    # Frequency commands
    # ------------------------------------------------------------------

    async def _cmd_get_freq(self, cmd: RigctldCommand) -> RigctldResponse:
        try:
            target = self._resolve_target_vfo(cmd.vfo_arg)
        except ValueError:
            return _err(HamlibError.EVFO)

        # Per-VFO routing for VFOB on dual-RX: read SUB receiver state directly.
        # Skip pending/cache (those track MAIN only — see _PendingRigState).
        # Gate on _receiver_index_for so single-RX active_slot="B" falls through
        # to the MAIN path (slot selection is via set_vfo_slot, not receiver=).
        if self._receiver_index_for(target) == 1:
            state = self._radio_state()
            if state is not None:
                return RigctldResponse(values=[str(state.sub.freq)])
            # State unavailable. For an EXPLICIT VFOB request under chk_vfo=1
            # surface ENIMPL rather than silently returning MAIN data labelled
            # as VFOB (issue #1355). For implicit/legacy requests (vfo_arg
            # is None or "currVFO") the legacy MAIN fall-through is more
            # forgiving and remains the documented behaviour.
            if cmd.vfo_arg == "VFOB":
                return _err(HamlibError.ENIMPL)

        main_state = self._main_receiver_state()
        pending_freq = self._effective_pending_freq(main_state)
        if pending_freq is not None:
            self._cache.update_freq(pending_freq)
            return RigctldResponse(values=[str(pending_freq)])
        if main_state is not None:
            self._cache.update_freq(main_state.freq)
            return RigctldResponse(values=[str(main_state.freq)])
        if self._cache.is_fresh("freq", self._config.cache_ttl):
            return RigctldResponse(values=[str(self._cache.freq)])
        freq = await self._radio.get_freq()
        self._cache.update_freq(freq)
        return RigctldResponse(values=[str(freq)])

    async def _cmd_set_freq(self, cmd: RigctldCommand) -> RigctldResponse:
        if not cmd.args:
            return _err(HamlibError.EINVAL)
        try:
            freq = int(float(cmd.args[0]))
        except ValueError:
            return _err(HamlibError.EINVAL)
        try:
            target = self._resolve_target_vfo(cmd.vfo_arg)
        except ValueError:
            return _err(HamlibError.EVFO)

        receiver = self._receiver_index_for(target)
        await self._radio.set_freq(freq, receiver=receiver)
        # Pending/cache track MAIN only — only update on the MAIN path so
        # subsequent ``f VFOA`` reads coalesce against the just-written value.
        if receiver == 0:
            self._pending.freq = freq
            self._cache.update_freq(freq)
        return _ok()

    # ------------------------------------------------------------------
    # Mode commands
    # ------------------------------------------------------------------

    async def _cmd_get_mode(self, cmd: RigctldCommand) -> RigctldResponse:
        try:
            target = self._resolve_target_vfo(cmd.vfo_arg)
        except ValueError:
            return _err(HamlibError.EVFO)

        # Per-VFO routing for VFOB on dual-RX: read SUB receiver state directly.
        # Skip pending/cache (those track MAIN only — see _PendingRigState).
        # Gate on _receiver_index_for so single-RX active_slot="B" falls through
        # to the MAIN path (slot selection is via set_vfo_slot, not receiver=).
        if self._receiver_index_for(target) == 1:
            state = self._radio_state()
            if state is not None:
                sub = state.sub
                mode_str = sub.mode.upper()
                passband = _filter_to_passband(sub.filter)
                data_mode = sub.data_mode
                if data_mode:
                    if mode_str == "USB":
                        mode_str = "PKTUSB"
                    elif mode_str == "LSB":
                        mode_str = "PKTLSB"
                    elif mode_str == "RTTY":
                        mode_str = "PKTRTTY"
                return RigctldResponse(values=[mode_str, str(passband)])
            # State unavailable. For an EXPLICIT VFOB request under chk_vfo=1
            # surface ENIMPL rather than silently returning MAIN data labelled
            # as VFOB (issue #1355). For implicit/legacy requests (vfo_arg
            # is None or "currVFO") the legacy MAIN fall-through is more
            # forgiving and remains the documented behaviour.
            if cmd.vfo_arg == "VFOB":
                return _err(HamlibError.ENIMPL)

        main_state = self._main_receiver_state()
        pending_mode = self._effective_pending_mode(main_state)
        if pending_mode is not None:
            mode_str, passband, data_mode = pending_mode
            self._cache.update_mode(mode_str, self._pending.filter_width)
            self._cache.update_data_mode(bool(data_mode))
        elif main_state is not None:
            mode_str = main_state.mode.upper()
            passband = _filter_to_passband(main_state.filter)
            data_mode = main_state.data_mode
            self._cache.update_mode(mode_str, main_state.filter)
            self._cache.update_data_mode(bool(data_mode))
        elif self._cache.is_fresh("mode", self._config.cache_ttl):
            mode_str = self._cache.mode
            passband = _filter_to_passband(self._cache.filter_width)
            data_mode = self._cache.data_mode
        else:
            get_mode = get_mode_reader(self._radio, _mode_to_hamlib_str)
            if get_mode is None:
                return _err(HamlibError.ENIMPL)
            mode_str, filt = await get_mode()
            self._cache.update_mode(mode_str, filt)
            passband = _filter_to_passband(filt)
            # Fetch data mode alongside mode to keep them in sync.
            try:
                data_mode = await self._radio.get_data_mode()
                self._cache.update_data_mode(data_mode)
            except Exception:
                logger.debug("get_data_mode failed, using cache", exc_info=True)
                data_mode = self._cache.data_mode

        # Map DATA overlays to packet modes where hamlib expects them.
        if data_mode:
            if mode_str == "USB":
                mode_str = "PKTUSB"
            elif mode_str == "LSB":
                mode_str = "PKTLSB"
            elif mode_str == "RTTY":
                mode_str = "PKTRTTY"

        return RigctldResponse(values=[mode_str, str(passband)])

    async def _cmd_set_mode(self, cmd: RigctldCommand) -> RigctldResponse:
        if not cmd.args:
            return _err(HamlibError.EINVAL)
        requested_mode = cmd.args[0].upper()
        if requested_mode not in HAMLIB_MODE_MAP:
            return _err(HamlibError.EINVAL)
        civ_val = HAMLIB_MODE_MAP[requested_mode]
        try:
            mode = Mode(civ_val)
        except ValueError:
            return _err(HamlibError.EINVAL)
        base_mode_str = CIV_TO_HAMLIB_MODE.get(mode.value, "USB")
        passband_hz = 0
        if len(cmd.args) >= 2:
            try:
                passband_hz = int(cmd.args[1])
            except ValueError:
                return _err(HamlibError.EINVAL)
        filter_width = _passband_to_filter(passband_hz)
        packet_modes = {"PKTUSB", "PKTLSB", "PKTRTTY"}

        try:
            target = self._resolve_target_vfo(cmd.vfo_arg)
        except ValueError:
            return _err(HamlibError.EVFO)

        # Per-VFO routing for VFOB on dual-RX: dispatch to SUB receiver and
        # skip the MAIN-only pending/cache + read-back sync (those track MAIN).
        # Gate on _receiver_index_for so single-RX active_slot="B" falls through
        # to the MAIN path (slot selection is via set_vfo_slot, not receiver=).
        if self._receiver_index_for(target) == 1:
            await self._radio.set_mode(
                base_mode_str, filter_width=filter_width, receiver=1
            )
            if requested_mode in packet_modes:
                await self._apply_packet_data_mode(receiver=1)
            return _ok()

        await self._radio.set_mode(base_mode_str, filter_width=filter_width)

        # Only set DATA mode explicitly for packet modes.
        # For non-packet modes, avoid hidden side-effects (do not force DATA off).
        if requested_mode in packet_modes:
            data_mode = await self._apply_packet_data_mode()

            # Read-back sync: keep next get_mode deterministic for CAT clients.
            # Some radios acknowledge set-data quickly but reflect packet mode
            # with a short delay. We wait briefly to reduce client-side stalls.
            synced = False
            get_mode = get_mode_reader(self._radio, _mode_to_hamlib_str)
            if get_mode is not None:
                for _ in range(5):
                    try:
                        read_mode, _ = await get_mode()
                        read_data = await self._radio.get_data_mode()
                        if read_mode == base_mode_str and read_data:
                            synced = True
                            break
                    except Exception:
                        logger.debug("rigctld: sync poll failed", exc_info=True)
                    await asyncio.sleep(0.05)

            # Cache optimistic final state even if read-back lagged.
            self._cache.update_mode(base_mode_str, filter_width)
            self._cache.update_data_mode(True)
            self._pending.mode = base_mode_str
            self._pending.filter_width = filter_width
            self._pending.data_mode = True
            logger.debug("set_mode(%s): DATA%s selected", requested_mode, data_mode)
            if not synced:
                logger.debug(
                    "set_mode(%s): packet read-back not fully synced yet; cached optimistic state",
                    requested_mode,
                )
        else:
            # For non-packet mode changes update mode cache, but preserve DATA
            # state (no forced DATA off side-effect).
            self._cache.update_mode(base_mode_str, filter_width)
            self._pending.mode = base_mode_str
            self._pending.filter_width = filter_width
            self._pending.data_mode = None

        return _ok()

    # ------------------------------------------------------------------
    # PTT commands
    # ------------------------------------------------------------------

    async def _cmd_get_ptt(self, cmd: RigctldCommand) -> RigctldResponse:
        # Validate VFO arg (rejects ``t VFOB`` on single-RX, unknown labels).
        # The Icom radio exposes a single global PTT state — there is no
        # per-VFO PTT register — so the answer is the same for VFOA / VFOB
        # / currVFO once the request is accepted. (See PR #1349 / issue
        # #1344 for the rationale.)
        try:
            self._resolve_target_vfo(cmd.vfo_arg)
        except ValueError:
            return _err(HamlibError.EVFO)

        state = self._radio_state()
        if state is not None:
            self._cache.update_ptt(state.ptt)
            if self._ptt_state is None:
                return RigctldResponse(values=[str(int(state.ptt))])
            if state.ptt == self._ptt_state:
                self._ptt_state = None
                return RigctldResponse(values=[str(int(state.ptt))])
            return RigctldResponse(values=[str(int(self._ptt_state))])
        return RigctldResponse(values=[str(int(bool(self._ptt_state)))])

    async def _cmd_set_ptt(self, cmd: RigctldCommand) -> RigctldResponse:
        if not cmd.args:
            return _err(HamlibError.EINVAL)
        try:
            on = bool(int(cmd.args[0]))
        except ValueError:
            return _err(HamlibError.EINVAL)
        # Validate VFO arg — radio PTT is global so the VFO label is honoured
        # only insofar as it must name a receiver this profile actually has.
        try:
            self._resolve_target_vfo(cmd.vfo_arg)
        except ValueError:
            return _err(HamlibError.EVFO)
        await self._radio.set_ptt(on)
        self._ptt_state = on
        self._cache.update_ptt(on)
        return _ok()

    # ------------------------------------------------------------------
    # VFO commands
    # ------------------------------------------------------------------

    def _profile_vfo_info(self) -> tuple[int, str] | None:
        """Return (receiver_count, vfo_scheme) when the radio exposes a
        real profile, else ``None``.

        Safely handles mocked radios: ``AsyncMock.profile`` auto-generates
        sub-attributes which are not usable integers/strings.
        """
        profile = getattr(self._radio, "profile", None)
        if profile is None:
            return None
        rc = getattr(profile, "receiver_count", None)
        scheme = getattr(profile, "vfo_scheme", None)
        if isinstance(rc, int) and isinstance(scheme, str):
            return rc, scheme
        return None

    def _active_vfo_name(self) -> str:
        """Return ``"VFOA"`` or ``"VFOB"`` reflecting current radio state.

        Dual-RX: maps ``radio_state.active`` (``MAIN``/``SUB``) → VFOA/VFOB.
        1-Rx: maps ``radio_state.main.active_slot`` (``A``/``B``) → VFOA/VFOB.
        Falls back to ``VFOA`` when state is missing or profile is unknown.
        """
        info = self._profile_vfo_info()
        state = self._radio_state()
        if info is None or state is None:
            return "VFOA"
        rc, _ = info
        if rc >= 2:
            return "VFOB" if state.active == "SUB" else "VFOA"
        return "VFOB" if state.main.active_slot == "B" else "VFOA"

    def _receiver_index_for(self, target: Literal["VFOA", "VFOB"]) -> int:
        """Map a resolved VFO target to a backend receiver index.

        Dual-RX profiles (``receiver_count >= 2``) honour the per-VFO
        routing introduced in #1344 — ``VFOB`` -> ``receiver=1``.

        Single-RX profiles only have ``receiver=0``; per-VFO state is
        selected via ``set_vfo_slot("A"/"B")`` on the *same* receiver
        (issue #1354). ``_active_vfo_name`` may legitimately return
        ``VFOB`` on single-RX when ``state.main.active_slot == "B"`` —
        the slot is already selected, so the caller must still route
        the I/O to ``receiver=0`` rather than to a non-existent
        ``receiver=1``.
        """
        info = self._profile_vfo_info()
        if info is not None and info[0] >= 2:
            return 1 if target == "VFOB" else 0
        return 0

    def _resolve_target_vfo(self, vfo_arg: str | None) -> Literal["VFOA", "VFOB"]:
        """Map a Hamlib VFO arg to the canonical VFO name for routing.

        Used by handlers that need per-VFO routing (freq, mode, PTT) when
        the client sends a leading VFO token under ``chk_vfo=1``.

        Returns:
            ``"VFOA"`` if the request targets MAIN (or no arg / ``currVFO``
            and the active receiver is MAIN). ``"VFOB"`` if the request
            targets SUB.

        Raises:
            ValueError: ``vfo_arg`` is unrecognised, OR ``"VFOB"`` was
                requested on a single-receiver profile. Callers map this
                to ``HamlibError.EVFO``.
        """
        if vfo_arg is None or vfo_arg == "currVFO":
            return cast(Literal["VFOA", "VFOB"], self._active_vfo_name())
        if vfo_arg == "VFOA":
            return "VFOA"
        if vfo_arg == "VFOB":
            info = self._profile_vfo_info()
            if info is None or info[0] < 2:
                raise ValueError(
                    f"VFOB requested on single-receiver profile (info={info!r})"
                )
            return "VFOB"
        raise ValueError(f"Unknown VFO arg: {vfo_arg!r}")

    async def _cmd_get_vfo(self, cmd: RigctldCommand) -> RigctldResponse:
        return RigctldResponse(values=[self._active_vfo_name()])

    async def _cmd_set_vfo(self, cmd: RigctldCommand) -> RigctldResponse:
        if not cmd.args:
            return _err(HamlibError.EINVAL)
        vfo = cmd.args[0].upper()
        info = self._profile_vfo_info()
        # Backwards-compat: unknown VFO names or profile-less radios → no-op
        if vfo not in ("VFOA", "VFOB") or info is None:
            return _ok()
        rc, _ = info
        # Issue #1172: route by capability, not by string-overloaded
        # ``set_vfo``.  Dual-RX rigs use ``select_receiver`` (the
        # Transceiver→Receiver tier from #1170); single-RX rigs use
        # ``set_vfo_slot`` for the per-receiver A/B switch.  Both are
        # part of the public protocol surface (``ReceiverBankCapable`` /
        # ``VfoSlotCapable``) so the legacy MAIN/SUB↔A/B mapping that
        # used to live here is gone.
        if rc >= 2:
            select_receiver = getattr(self._radio, "select_receiver", None)
            if select_receiver is not None:
                target = "MAIN" if vfo == "VFOA" else "SUB"
                await select_receiver(target)
                return _ok()
            target = "MAIN" if vfo == "VFOA" else "SUB"
        else:
            set_vfo_slot = getattr(self._radio, "set_vfo_slot", None)
            if set_vfo_slot is not None:
                slot = "A" if vfo == "VFOA" else "B"
                await set_vfo_slot(slot)
                return _ok()
            target = "A" if vfo == "VFOA" else "B"
        # Issue #1189: legacy backends (e.g. SerialMockRadio,
        # 3rd-party Radio implementers) predate ``ReceiverBankCapable`` /
        # ``VfoSlotCapable`` and only expose the legacy ``set_vfo``
        # overload.  Fall back to it so ``V VFOA`` / ``V VFOB`` actually
        # reach the radio instead of returning a silent ``RPRT 0``.  The
        # ``DeprecationWarning`` from ``IcomRadio.set_vfo`` (#1187) is
        # intentional — it signals migration.
        legacy_set_vfo = getattr(self._radio, "set_vfo", None)
        if legacy_set_vfo is None:
            return _err(HamlibError.ENAVAIL)
        await legacy_set_vfo(target)
        return _ok()

    # ------------------------------------------------------------------
    # Level commands
    # ------------------------------------------------------------------

    async def _cmd_get_level(self, cmd: RigctldCommand) -> RigctldResponse:
        if not cmd.args:
            return _err(HamlibError.EINVAL)
        level = cmd.args[0].upper()

        # Validate the (optional) leading VFO label up-front — even for
        # globally-scoped levels, an unknown label or VFOB on a single-RX
        # profile must error before any radio call (issue #1345).
        try:
            target = self._resolve_target_vfo(cmd.vfo_arg)
        except ValueError:
            return _err(HamlibError.EVFO)
        # Single-RX profiles only have receiver=0; per-VFO state is selected
        # via set_vfo_slot, not receiver= (issue #1354).
        receiver = self._receiver_index_for(target)

        if self._routing is not None:
            return await self._routing.get_level(level, vfo=cmd.vfo_arg)

        all_levels = (
            {"STRENGTH", "RFPOWER", "SWR", "PREAMP", "ATT", "KEYSPD", "CWPITCH"}
            | set(_GET_LEVEL_FLOAT)
            | set(_GET_LEVEL_INT)
        )
        if level not in all_levels:
            return _err(HamlibError.EINVAL)
        main_state = self._main_receiver_state()

        # STRENGTH — per-receiver on dual-RX. VFOB reads SUB s_meter
        # directly from RadioState (don't update _cache — it tracks MAIN
        # only, mirroring the freq routing in #1344).
        if level == "STRENGTH":
            if receiver == 1:
                state = self._radio_state()
                if state is not None:
                    raw = state.sub.s_meter
                    return RigctldResponse(
                        values=[str(round((raw / 241.0) * 114.0 - 54.0))]
                    )
                if CAP_METERS in self._radio.capabilities:
                    raw = await self._radio.get_s_meter(receiver=1)
                    return RigctldResponse(
                        values=[str(round((raw / 241.0) * 114.0 - 54.0))]
                    )
                return _err(HamlibError.ENIMPL)
            if main_state is not None:
                raw = main_state.s_meter
                self._cache.update_s_meter(raw)
                return RigctldResponse(
                    values=[str(round((raw / 241.0) * 114.0 - 54.0))]
                )
            if CAP_METERS not in self._radio.capabilities:
                if self._cache.s_meter is not None:
                    raw = self._cache.s_meter
                    return RigctldResponse(
                        values=[str(round((raw / 241.0) * 114.0 - 54.0))]
                    )
                return _err(HamlibError.ENIMPL)
            raw = await self._radio.get_s_meter()
            self._cache.update_s_meter(raw)
            # IC-7610 S-meter: 0→S0(−54 dB), 120→S9(0 dB), 241→S9+60 dB
            return RigctldResponse(values=[str(round((raw / 241.0) * 114.0 - 54.0))])

        # RFPOWER — prefer RadioState, then meter call
        if level == "RFPOWER":
            state = self._radio_state()
            if state is not None and main_state is not None:
                raw_power = state.power_level / 255.0
                self._cache.update_rf_power(raw_power)
                return RigctldResponse(values=[f"{raw_power:.6f}"])
            if CAP_METERS not in self._radio.capabilities:
                if self._cache.rf_power is not None:
                    return RigctldResponse(values=[f"{self._cache.rf_power:.6f}"])
                return _err(HamlibError.ENIMPL)
            raw = await self._radio.get_rf_power()
            normalized = raw / 255.0
            self._cache.update_rf_power(normalized)
            return RigctldResponse(values=[f"{normalized:.6f}"])

        # SWR — meter call. ``get_swr`` already returns a calibrated
        # ratio (>= 1.0) per ``MetersCapable``; pass the float through
        # without re-mapping (issue #1173).
        if level == "SWR":
            if CAP_METERS not in self._radio.capabilities:
                if self._cache.swr is not None:
                    return RigctldResponse(values=[f"{self._cache.swr:.6f}"])
                return _err(HamlibError.ENIMPL)
            swr = float(await self._radio.get_swr())
            self._cache.update_swr(swr)
            return RigctldResponse(values=[f"{swr:.6f}"])

        # Simple 0-255 → 0.0-1.0 float levels
        if level in _GET_LEVEL_FLOAT:
            method = getattr(self._radio, _GET_LEVEL_FLOAT[level])
            # NB / NR are per-receiver on dual-RX Icoms — pass receiver=
            # when targeting SUB. Other levels are radio-global.
            if level in ("NB", "NR") and receiver == 1:
                raw = await method(receiver=receiver)
            else:
                raw = await method()
            return RigctldResponse(values=[f"{raw / 255.0:.6f}"])

        # Integer levels (WPM, Hz)
        if level in _GET_LEVEL_INT:
            val = await getattr(self._radio, _GET_LEVEL_INT[level])()
            return RigctldResponse(values=[str(val)])

        # PREAMP — returns dB (0, 12, 20)
        if level == "PREAMP":
            idx = await self._radio.get_preamp()
            db = _PREAMP_IDX_TO_DB[idx] if 0 <= idx < len(_PREAMP_IDX_TO_DB) else 0
            return RigctldResponse(values=[str(db)])

        # ATT — returns dB directly (0, 6, 12, 18)
        if level == "ATT":
            db = await self._radio.get_attenuator_level()
            return RigctldResponse(values=[str(db)])

        return _err(HamlibError.EINVAL)

    async def _cmd_set_level(self, cmd: RigctldCommand) -> RigctldResponse:
        if len(cmd.args) < 2:
            return _err(HamlibError.EINVAL)
        level = cmd.args[0].upper()
        try:
            value = float(cmd.args[1])
        except ValueError:
            return _err(HamlibError.EINVAL)

        # Validate the (optional) leading VFO label up-front (issue #1345).
        try:
            target = self._resolve_target_vfo(cmd.vfo_arg)
        except ValueError:
            return _err(HamlibError.EVFO)
        # Single-RX profiles only have receiver=0; per-VFO state is selected
        # via set_vfo_slot, not receiver= (issue #1354).
        receiver = self._receiver_index_for(target)

        if self._routing is not None:
            return await self._routing.set_level(level, value, vfo=cmd.vfo_arg)

        if level == "RFPOWER":
            await self._radio.set_rf_power(round(value * 255))
            return _ok()

        if level in _SET_LEVEL_FLOAT:
            raw = max(0, min(255, round(value * 255)))
            method = getattr(self._radio, _SET_LEVEL_FLOAT[level])
            # NB / NR are per-receiver on dual-RX Icoms.
            if level in ("NB", "NR") and receiver == 1:
                await method(raw, receiver=receiver)
            else:
                await method(raw)
            return _ok()

        if level == "KEYSPD":
            await self._radio.set_key_speed(round(value))
            return _ok()

        if level == "CWPITCH":
            await self._radio.set_cw_pitch(round(value))
            return _ok()

        if level == "PREAMP":
            db = round(value)
            # Find nearest supported dB (0, 12, 20)
            idx = min(
                range(len(_PREAMP_IDX_TO_DB)),
                key=lambda i: abs(_PREAMP_IDX_TO_DB[i] - db),
            )
            await self._radio.set_preamp(idx)
            return _ok()

        if level == "ATT":
            # Find nearest supported dB (0, 6, 12, 18)
            _att_steps = [0, 6, 12, 18]
            db = round(value)
            nearest = min(_att_steps, key=lambda x: abs(x - db))
            await self._radio.set_attenuator_level(nearest)
            return _ok()

        return _err(HamlibError.EINVAL)

    # ------------------------------------------------------------------
    # Function commands
    # ------------------------------------------------------------------

    async def _cmd_get_func(self, cmd: RigctldCommand) -> RigctldResponse:
        if not cmd.args:
            return _err(HamlibError.EINVAL)
        func = cmd.args[0].upper()

        # Validate the (optional) leading VFO label (issue #1345).
        try:
            target = self._resolve_target_vfo(cmd.vfo_arg)
        except ValueError:
            return _err(HamlibError.EVFO)
        # Single-RX profiles only have receiver=0; per-VFO state is selected
        # via set_vfo_slot, not receiver= (issue #1354).
        receiver = self._receiver_index_for(target)

        if self._routing is not None:
            return await self._routing.get_func(func, vfo=cmd.vfo_arg)

        if func not in _FUNC_GET:
            return _err(HamlibError.EINVAL)
        method = getattr(self._radio, _FUNC_GET[func])
        # NB / NR are per-receiver on dual-RX Icoms.  Other funcs are
        # radio-global — VFO arg is validated above but ignored.
        if func in ("NB", "NR") and receiver == 1:
            result = await method(receiver=receiver)
        else:
            result = await method()
        # APF returns AudioPeakFilter int enum (0=off); others return bool
        return RigctldResponse(values=[str(int(bool(result)))])

    async def _cmd_set_func(self, cmd: RigctldCommand) -> RigctldResponse:
        if len(cmd.args) < 2:
            return _err(HamlibError.EINVAL)
        func = cmd.args[0].upper()
        try:
            on = bool(int(cmd.args[1]))
        except ValueError:
            return _err(HamlibError.EINVAL)

        # Validate the (optional) leading VFO label (issue #1345).
        try:
            target = self._resolve_target_vfo(cmd.vfo_arg)
        except ValueError:
            return _err(HamlibError.EVFO)
        # Single-RX profiles only have receiver=0; per-VFO state is selected
        # via set_vfo_slot, not receiver= (issue #1354).
        receiver = self._receiver_index_for(target)

        if self._routing is not None:
            return await self._routing.set_func(func, on, vfo=cmd.vfo_arg)

        if func not in _FUNC_SET:
            return _err(HamlibError.EINVAL)
        if func == "APF":
            # APF takes an int mode: 0=off, 1=soft
            await self._radio.set_audio_peak_filter(1 if on else 0)
        elif func in ("NB", "NR") and receiver == 1:
            # Per-receiver NB / NR on dual-RX.
            await getattr(self._radio, _FUNC_SET[func])(on, receiver=receiver)
        else:
            await getattr(self._radio, _FUNC_SET[func])(on)
        return _ok()

    # ------------------------------------------------------------------
    # Split VFO commands
    # ------------------------------------------------------------------

    async def _cmd_get_split_vfo(self, cmd: RigctldCommand) -> RigctldResponse:
        # Validate the (optional) leading VFO label. Split is a global
        # CI-V concept on Icom — the answer is the same for VFOA / VFOB —
        # but the request must still name a receiver this profile has.
        try:
            self._resolve_target_vfo(cmd.vfo_arg)
        except ValueError:
            return _err(HamlibError.EVFO)
        state = self._radio_state()
        split = state.split if state is not None else False
        # ``_split_tx_vfo`` is handler-local Hamlib-protocol state, set by
        # the most recent ``S`` command (issue #1345 — fixes #1319 finding
        # #2 where this used to leak the active VFO instead of TX_VFO).
        return RigctldResponse(values=[str(int(split)), self._split_tx_vfo])

    async def _cmd_set_split_vfo(self, cmd: RigctldCommand) -> RigctldResponse:
        # Validate the (optional) leading VFO label first — VFOB on a
        # single-RX profile must error before any radio call.
        try:
            self._resolve_target_vfo(cmd.vfo_arg)
        except ValueError:
            return _err(HamlibError.EVFO)
        # Args after vfo_arg strip: <split 0|1> <tx_vfo>
        if len(cmd.args) < 2:
            return _ok()
        try:
            on = bool(int(cmd.args[0]))
        except ValueError:
            return _err(HamlibError.EINVAL)
        tx_vfo = cmd.args[1].upper()
        info = self._profile_vfo_info()
        set_split = getattr(self._radio, "set_split", None)
        if set_split is not None:
            await set_split(on)
        # Dual-RX: when enabling split, ensure TX is routed to the requested
        # receiver (WSJT-X sends VFOB as the split-TX source).  If the
        # receiver-select call fails we must roll back the split-enable —
        # otherwise the radio is left in a half-configured state (split on
        # but TX not routed to SUB), silently diverging from WSJT-X's
        # expectations.
        if on and info is not None and info[0] >= 2 and tx_vfo in ("VFOA", "VFOB"):
            set_vfo = getattr(self._radio, "set_vfo", None)
            if set_vfo is not None:
                target = "SUB" if tx_vfo == "VFOB" else "MAIN"
                try:
                    await set_vfo(target)
                except ConnectionError as exc:
                    logger.warning(
                        "set_split_vfo: set_vfo(%s) failed with "
                        "ConnectionError (%s); rolling back split",
                        target,
                        exc,
                    )
                    await self._rollback_split(set_split)
                    return _err(HamlibError.EIO)
                except TimeoutError as exc:
                    logger.warning(
                        "set_split_vfo: set_vfo(%s) timed out (%s); rolling back split",
                        target,
                        exc,
                    )
                    await self._rollback_split(set_split)
                    return _err(HamlibError.ETIMEOUT)
                except Exception:
                    logger.exception(
                        "set_split_vfo: set_vfo(%s) failed unexpectedly; "
                        "rolling back split",
                        target,
                    )
                    await self._rollback_split(set_split)
                    return _err(HamlibError.EINTERNAL)
        # Record the requested TX VFO for the next ``s`` (get_split_vfo)
        # query — Hamlib protocol expects the TX VFO label round-trip
        # (issue #1345). Validate the label so a malformed request never
        # poisons the cached value.
        if tx_vfo in ("VFOA", "VFOB"):
            self._split_tx_vfo = cast(Literal["VFOA", "VFOB"], tx_vfo)
        return _ok()

    async def _rollback_split(self, set_split: Any) -> None:
        """Best-effort rollback: disable split that was just enabled.

        Called when the follow-up ``set_vfo`` in ``set_split_vfo`` fails,
        so the radio does not end up with split-enabled but TX not
        routed to SUB.  Rollback errors are logged and swallowed — the
        original failure takes precedence in the response.
        """
        if set_split is None:
            return
        try:
            await set_split(False)
            logger.info("set_split_vfo: rollback disabled split successfully")
        except Exception:
            logger.exception(
                "set_split_vfo: rollback set_split(False) also failed; "
                "radio may be in inconsistent state"
            )

    # ------------------------------------------------------------------
    # RIT
    # ------------------------------------------------------------------

    async def _cmd_get_rit(self, cmd: RigctldCommand) -> RigctldResponse:
        # Validate the (optional) leading VFO label. Most Icom radios
        # expose a single global RIT register (CI-V 0x21 0x00) — the
        # answer is the same for VFOA / VFOB. Hamlib clients tolerate
        # this. Per-VFO RIT would require a RadioState extension; out of
        # scope for #1345 (tracked as a follow-up under epic #1341).
        try:
            self._resolve_target_vfo(cmd.vfo_arg)
        except ValueError:
            return _err(HamlibError.EVFO)
        state = self._radio_state()
        rit = state.rit_freq if state is not None else 0
        return RigctldResponse(values=[str(rit)])

    async def _cmd_set_rit(self, cmd: RigctldCommand) -> RigctldResponse:
        if not cmd.args:
            return _err(HamlibError.EINVAL)
        try:
            hz = int(cmd.args[0])
        except ValueError:
            return _err(HamlibError.EINVAL)
        if CAP_RIT not in self._radio.capabilities:
            return _err(HamlibError.ENIMPL)
        await self._radio.set_rit_frequency(hz)
        await self._radio.set_rit_status(hz != 0)
        return _ok()

    async def _cmd_get_xit(self, cmd: RigctldCommand) -> RigctldResponse:
        # IC-7610 shares one RIT/XIT frequency register (CI-V 0x21 0x00).
        # Return the same value as get_rit.
        try:
            self._resolve_target_vfo(cmd.vfo_arg)
        except ValueError:
            return _err(HamlibError.EVFO)
        state = self._radio_state()
        rit = state.rit_freq if state is not None else 0
        return RigctldResponse(values=[str(rit)])

    async def _cmd_set_xit(self, cmd: RigctldCommand) -> RigctldResponse:
        if not cmd.args:
            return _err(HamlibError.EINVAL)
        try:
            hz = int(cmd.args[0])
        except ValueError:
            return _err(HamlibError.EINVAL)
        if CAP_RIT not in self._radio.capabilities:
            return _err(HamlibError.ENIMPL)
        await self._radio.set_rit_frequency(hz)
        await self._radio.set_rit_tx_status(hz != 0)
        return _ok()

    # ------------------------------------------------------------------
    # Info / control commands
    # ------------------------------------------------------------------

    async def _cmd_dump_state(self, cmd: RigctldCommand) -> RigctldResponse:
        if self._routing is not None:
            return RigctldResponse(values=self._routing.dump_state())
        return RigctldResponse(values=list(_IC7610_DUMP_STATE))

    async def _cmd_dump_caps(self, cmd: RigctldCommand) -> RigctldResponse:
        return await self._cmd_dump_state(cmd)

    async def _cmd_get_info(self, cmd: RigctldCommand) -> RigctldResponse:
        if self._routing is not None:
            return RigctldResponse(values=[self._routing.get_info()])
        raw_model = getattr(self._radio, "model", "IC-7610")
        model = raw_model if isinstance(raw_model, str) and raw_model else "IC-7610"
        return RigctldResponse(values=[f"Icom {model} (rigplane)"])

    async def _cmd_chk_vfo(self, cmd: RigctldCommand) -> RigctldResponse:
        """Hamlib chk_vfo handshake.

        Returns ``"1"`` for dual-RX profiles (advertises ``vfo_opt``
        support); ``"0"`` for single-RX (legacy behaviour).

        Re-enabled in Variant A 5/5 (#1346) after the full ``vfo_opt``
        stack landed: parser support (#1343), per-VFO routing for
        freq/mode/PTT (#1344), per-VFO split/RIT/level/func (#1345).
        Variant B's unconditional ``"0"`` (PR #1340) is now correctly
        superseded.
        """
        info = self._profile_vfo_info()
        dual = info is not None and info[0] >= 2
        return RigctldResponse(values=["1" if dual else "0"])

    async def _cmd_get_powerstat(self, cmd: RigctldCommand) -> RigctldResponse:
        on = await self._radio.get_powerstat()
        return RigctldResponse(values=[str(int(bool(on)))])

    async def _cmd_quit(self, cmd: RigctldCommand) -> RigctldResponse:
        # Return OK; server.py detects cmd_echo == "quit" and closes the connection
        return RigctldResponse(values=[], error=HamlibError.OK, cmd_echo="quit")

    # ------------------------------------------------------------------
    # Power conversion (WSJT-X needs these)
    # ------------------------------------------------------------------

    _MAX_POWER_W: int = 100  # IC-7610 max power

    async def _cmd_power2mw(self, cmd: RigctldCommand) -> RigctldResponse:
        """Convert normalized power (0.0-1.0) to milliwatts.

        Args from rigctl: power_float freq mode (freq/mode ignored).
        """
        if not cmd.args:
            return _err(HamlibError.EINVAL)
        try:
            power = float(cmd.args[0])
        except ValueError:
            return _err(HamlibError.EINVAL)
        mw = int(power * self._MAX_POWER_W * 1000)
        return RigctldResponse(values=[str(mw)])

    async def _cmd_mw2power(self, cmd: RigctldCommand) -> RigctldResponse:
        """Convert milliwatts to normalized power (0.0-1.0).

        Args from rigctl: mw freq mode (freq/mode ignored).
        """
        if not cmd.args:
            return _err(HamlibError.EINVAL)
        try:
            mw = float(cmd.args[0])
        except ValueError:
            return _err(HamlibError.EINVAL)
        power = mw / (self._MAX_POWER_W * 1000)
        return RigctldResponse(values=[f"{power:.6f}"])

    async def _cmd_get_lock_mode(self, cmd: RigctldCommand) -> RigctldResponse:
        """Get lock mode — always unlocked."""
        return RigctldResponse(values=["0"])

    # ------------------------------------------------------------------
    # Raw CI-V
    # ------------------------------------------------------------------
    # Raw CI-V passthrough (hamlib 'w' command)
    # ------------------------------------------------------------------

    async def _cmd_send_raw(self, cmd: RigctldCommand) -> RigctldResponse:
        """Send raw CI-V bytes to the radio and return the raw response.

        Input: space-separated hex tokens or a single backslash-escaped hex string.
        Output: space-separated uppercase hex bytes of the radio's response,
                or an empty response on timeout.
        """
        if not cmd.args:
            return _err(HamlibError.EINVAL)

        try:
            frame_bytes = _parse_raw_hex(cmd.args)
        except (ValueError, IndexError):
            return _err(HamlibError.EINVAL)

        send_fn = getattr(self._radio, "_send_civ_raw", None)
        if send_fn is None:
            return _err(HamlibError.ENIMPL)

        try:
            resp = await send_fn(frame_bytes)
        except (TimeoutError, asyncio.TimeoutError):
            logger.debug("send_raw: timeout — returning empty response")
            return RigctldResponse(values=[])

        if resp is None:
            return RigctldResponse(values=[])

        raw = _civ_frame_to_bytes(resp)
        hex_str = " ".join(f"{b:02X}" for b in raw)
        return RigctldResponse(values=[hex_str])

    # ------------------------------------------------------------------
    # Dispatch table (populated after method definitions)
    # ------------------------------------------------------------------

    _DISPATCH: dict[str, Any] = {}  # filled below


# Build the dispatch table after the class is defined so all methods exist.
RigctldHandler._DISPATCH = {
    "get_freq": RigctldHandler._cmd_get_freq,
    "set_freq": RigctldHandler._cmd_set_freq,
    "get_mode": RigctldHandler._cmd_get_mode,
    "set_mode": RigctldHandler._cmd_set_mode,
    "get_ptt": RigctldHandler._cmd_get_ptt,
    "set_ptt": RigctldHandler._cmd_set_ptt,
    "get_vfo": RigctldHandler._cmd_get_vfo,
    "set_vfo": RigctldHandler._cmd_set_vfo,
    "get_level": RigctldHandler._cmd_get_level,
    "set_level": RigctldHandler._cmd_set_level,
    "get_func": RigctldHandler._cmd_get_func,
    "set_func": RigctldHandler._cmd_set_func,
    "get_split_vfo": RigctldHandler._cmd_get_split_vfo,
    "set_split_vfo": RigctldHandler._cmd_set_split_vfo,
    "get_rit": RigctldHandler._cmd_get_rit,
    "set_rit": RigctldHandler._cmd_set_rit,
    "get_xit": RigctldHandler._cmd_get_xit,
    "set_xit": RigctldHandler._cmd_set_xit,
    "dump_state": RigctldHandler._cmd_dump_state,
    "dump_caps": RigctldHandler._cmd_dump_caps,
    "get_info": RigctldHandler._cmd_get_info,
    "chk_vfo": RigctldHandler._cmd_chk_vfo,
    "get_powerstat": RigctldHandler._cmd_get_powerstat,
    "quit": RigctldHandler._cmd_quit,
    "power2mW": RigctldHandler._cmd_power2mw,
    "mW2power": RigctldHandler._cmd_mw2power,
    "get_lock_mode": RigctldHandler._cmd_get_lock_mode,
    "send_raw": RigctldHandler._cmd_send_raw,
}
