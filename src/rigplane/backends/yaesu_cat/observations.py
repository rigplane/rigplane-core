"""Observation adapters for Yaesu CAT polling reads."""

from __future__ import annotations

import logging
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING, Protocol, TypeVar

from rigplane.core.observation_adapter import ProviderObservationAdapter
from rigplane.core.state_acquisition_policy import RadioAcquisitionProfile
from rigplane.core.state_pipeline_contracts import FieldPath, Observation
from rigplane.runtime.meter_cal import interpolate_meter

from .parser import CatFormatError, CatParseError
from .radio import _ctcss_index_to_centihz
from .transport import CatCommandRejected

if TYPE_CHECKING:
    from rigplane.core.types import BreakInMode

logger = logging.getLogger(__name__)

Clock = Callable[[], float]
_T = TypeVar("_T")

__all__ = ["YAESU_PTT_PATH", "YaesuObservationAdapter"]

_MAIN_FREQ = FieldPath.active("main", "freq_mode", "freq_hz")
_MAIN_MODE = FieldPath.active("main", "freq_mode", "mode")
_SUB_FREQ = FieldPath.active("sub", "freq_mode", "freq_hz")
_SUB_MODE = FieldPath.active("sub", "freq_mode", "mode")
_PTT = FieldPath.global_("tx_state", "ptt")
_MAIN_AF = FieldPath.receiver("main", "operator_controls", "af_level")
_MAIN_RF = FieldPath.receiver("main", "operator_controls", "rf_gain")
_MAIN_SQL = FieldPath.receiver("main", "operator_controls", "squelch")
_SUB_AF = FieldPath.receiver("sub", "operator_controls", "af_level")
_SUB_RF = FieldPath.receiver("sub", "operator_controls", "rf_gain")
_SUB_SQL = FieldPath.receiver("sub", "operator_controls", "squelch")
# RF front-end + AGC controls (MOR-443). MAIN-only: the FTX-1 has no
# per-receiver CAT command for these (no RA1/PA1/GT1), so emitting them for
# the sub receiver would mislabel the shared front-end read. This mirrors the
# legacy poller, which only writes ``main.{att,preamp,agc}``.
_MAIN_ATT = FieldPath.receiver("main", "operator_controls", "att")
_MAIN_PREAMP = FieldPath.receiver("main", "operator_controls", "preamp")
_MAIN_AGC = FieldPath.receiver("main", "operator_controls", "agc")
_MAIN_S_METER = FieldPath.receiver("main", "meters", "s_meter")
_SUB_S_METER = FieldPath.receiver("sub", "meters", "s_meter")
_ALC_METER = FieldPath.global_("meters", "alc")
_POWER_METER = FieldPath.global_("meters", "power")
_SWR_METER = FieldPath.global_("meters", "swr")
# PA compression meter (MOR-460). Cross-vendor neutral meter (also Icom CI-V
# 0x15 0x14); the FTX-1 reads it via RM3. Emitted as a stream-like TX meter in
# the same lane and under the same freshness/coalescing policy as alc/power/swr.
_COMP_METER = FieldPath.global_("meters", "comp")
# Global TX / operator-control setpoints (MOR-447). ``power_level`` is the
# watt SETPOINT (CAT ``PC``), distinct from the ``global.meters.power`` meter.
_POWER_LEVEL = FieldPath.global_("operator_controls", "power_level")
_MIC_GAIN = FieldPath.global_("operator_controls", "mic_gain")
_COMPRESSOR_ON = FieldPath.global_("tx_state", "compressor_on")
_COMPRESSOR_LEVEL = FieldPath.global_("operator_controls", "compressor_level")
_VOX_ON = FieldPath.global_("tx_state", "vox_on")
# Filter / IF-shift / narrow DSP controls (MOR-445). MAIN-only: the FTX-1 has
# no per-receiver CAT command for IF-shift/narrow (no IS1/NA1), and the legacy
# poller only writes ``main.{filter_width,if_shift,narrow}``. ``filter_width``
# is a ``freq_mode`` ACTIVE-slot field emitted in the freq/mode lane; IF-shift
# and narrow are per-receiver operator controls emitted in the slow lane.
_MAIN_FILTER_WIDTH = FieldPath.active("main", "freq_mode", "filter_width")
_MAIN_IF_SHIFT = FieldPath.receiver("main", "operator_controls", "if_shift")
_MAIN_NARROW = FieldPath.receiver("main", "operator_toggles", "narrow")
# NB/NR levels + derived toggles, auto/manual notch DSP controls (MOR-444).
# MAIN-only: the FTX-1 has no per-receiver CAT command for these (no NL1/RL1/
# BC1/BP10/BP11), so emitting them for the sub receiver would mislabel the
# shared read. This mirrors the legacy poller, which only writes
# ``main.{nb_level, nb, nr_level, nr, auto_notch, manual_notch,
# manual_notch_freq}``. The ``nb``/``nr`` toggles are DERIVED from the level
# (``level > 0``) read in the same cycle — a single CAT read each, never a
# second query — exactly as the legacy poller derives them.
_MAIN_NB_LEVEL = FieldPath.receiver("main", "operator_controls", "nb_level")
_MAIN_NB = FieldPath.receiver("main", "operator_toggles", "nb")
_MAIN_NR_LEVEL = FieldPath.receiver("main", "operator_controls", "nr_level")
_MAIN_NR = FieldPath.receiver("main", "operator_toggles", "nr")
_MAIN_AUTO_NOTCH = FieldPath.receiver("main", "operator_toggles", "auto_notch")
_MAIN_MANUAL_NOTCH = FieldPath.receiver("main", "operator_toggles", "manual_notch")
_MAIN_MANUAL_NOTCH_FREQ = FieldPath.receiver(
    "main", "operator_controls", "manual_notch_freq"
)
# Split + active-slot controls (MOR-446). ``split`` is a GLOBAL tx_state bool
# (CAT ``ST``), emitted in the global TX-control lane alongside compressor/VOX.
# ``active`` is the GLOBAL "which receiver is active" field (CAT ``VS``): the
# backend-neutral target is ``global.slow_state.active`` — a ``"MAIN"``/``"SUB"``
# str consumed by the rigctld VFOA/VFOB mapping and the dual-RX runtime. The
# ``VS`` index (0/1) is coerced to that str; the per-receiver
# ``receiver.<rx>.vfo.active_slot`` ("A"/"B") field is a DIFFERENT concept
# (which VFO slot within a receiver) and is NOT the target. FR/FT routing
# (``get_rx_func``/``get_tx_func``) has no backend-neutral FieldPath and stays
# vendor-namespaced compat-only per the promotion-criterion ADR — not observed.
_SPLIT = FieldPath.global_("tx_state", "split")
_ACTIVE = FieldPath.global_("slow_state", "active")
_ACTIVE_INDEX_TO_STR = {0: "MAIN", 1: "SUB"}
# Clarifier (RIT/XIT) controls (MOR-454). GLOBAL slow-changing operator/TX
# controls (CAT ``CF000``/``CF001``): ``rit_on``/``rit_tx`` are global tx_state
# bools (RX/TX clarifier flags), ``rit_freq`` is the global operator-control
# signed Hz offset. Emitted in the global TX-control lane alongside split/VOX,
# gated on the ``rit`` runtime capability, mirroring the legacy poller's
# ``"rit" in caps`` gate and its single ``get_clarifier``/``get_clarifier_freq``
# read pair. The signed Hz offset is emitted on the device scale (cross-vendor
# calibration is MOR-453).
_RIT_ON = FieldPath.global_("tx_state", "rit_on")
_RIT_TX = FieldPath.global_("tx_state", "rit_tx")
_RIT_FREQ = FieldPath.global_("operator_controls", "rit_freq")
# Tuner + dial-lock controls (MOR-455). GLOBAL slow-changing operator/TX
# controls: ``tuner_status`` is the global operator-control antenna-tuner state
# (CAT ``AC``: 0=OFF, 1=ON, 2=tuning, 3=tune-start) emitted as the raw device
# int (cross-vendor calibration is MOR-453); ``dial_lock`` is a global tx_state
# bool (CAT ``LK``). Both are emitted in the global TX-control lane alongside
# split/VOX, gated on the ``tuner``/``dial_lock`` runtime capabilities, mirroring
# the legacy poller's ``"tuner" in caps``/``"dial_lock" in caps`` gates and its
# single ``get_tuner``/``get_lock`` reads.
_TUNER = FieldPath.global_("operator_controls", "tuner_status")
_DIAL_LOCK = FieldPath.global_("tx_state", "dial_lock")
# CW keyer family (MOR-456). GLOBAL slow-changing operator/slow controls, all
# gated on the legacy poller's single ``"cw" in caps`` gate: ``key_speed`` is
# the keyer WPM (CAT ``KS``), ``cw_pitch`` is the sidetone pitch in Hz (CAT
# ``KP`` idx → ``300 + idx * 10``), ``break_in`` is the break-in mode emitted as
# the device int (CAT ``BI``: 0=OFF, 1=SEMI — FTX-1 is binary only, matching the
# legacy poller's ``1 if get_break_in() else 0`` int store), ``break_in_delay``
# is the QSK delay in ms (CAT ``SD``). All four are operator_controls and ride
# the global TX-control lane alongside RIT/tuner. ``cw_spot`` is a global
# slow_state bool (CAT ``CS``) emitted in the slow-control lane beside
# ``slow_state.active``. Raw device scale (cross-vendor calibration is MOR-453).
_KEY_SPEED = FieldPath.global_("operator_controls", "key_speed")
_CW_PITCH = FieldPath.global_("operator_controls", "cw_pitch")
_BREAK_IN = FieldPath.global_("operator_controls", "break_in")
_BREAK_IN_DELAY = FieldPath.global_("operator_controls", "break_in_delay")
_CW_SPOT = FieldPath.global_("slow_state", "cw_spot")
# Tone / CTCSS squelch-type (MOR-457). The FTX-1 CAT ``CT`` "SQL TYPE" command
# (FTX-1_CAT_OM_ENG_2507) is a single MAIN-only read (CT0); its P2 code is
# mapped onto the neutral, mutually-exclusive CTCSS booleans, matching the
# Hamlib/Icom convention where ``repeater_tone`` = CTCSS tone ENCODE ("TONE")
# and ``repeater_tsql`` = CTCSS tone SQUELCH (decode):
#   P2 code 1 (ENC ON / DEC OFF, "TONE")  -> repeater_tone=True,  repeater_tsql=False
#   P2 code 2 (ENC ON / DEC ON,  "TSQL")  -> repeater_tone=False, repeater_tsql=True
#   P2 codes 0/3/4/5 (OFF / DCS / PR-FREQ / REV-TONE) -> both False (these have no
#       neutral CTCSS-boolean representation, so they collapse to "neither on").
# Both paths are emitted every cycle (including the False derivations) so the
# store always reflects current state. Per-receiver ``operator_toggles`` like
# nb/nr/auto_notch, emitted in the slow-control lane. MAIN only (CT0): the SUB
# receiver would need CT1, which is out of scope here. The CTCSS tone FREQUENCY
# (CAT ``CN`` by index → ``tone_freq``/``tsql_freq``) is intentionally NOT
# emitted here: that is a deferred follow-up (wiring ``CN``), NOT a hardware
# limitation. Raw device code (cross-vendor calibration is MOR-453).
_MAIN_REPEATER_TONE = FieldPath.receiver("main", "operator_toggles", "repeater_tone")
_MAIN_REPEATER_TSQL = FieldPath.receiver("main", "operator_toggles", "repeater_tsql")
# CTCSS tone FREQUENCY (MOR-458). The FTX-1 CAT ``CN`` "CTCSS TONE FREQUENCY"
# command (FTX-1_CAT_OM_ENG_2507) reports the MAIN tone as a 0-49 INDEX into
# the standard 50-tone EIA chart (NOT an absolute frequency; cf. Icom 0x1B
# BCD-Hz). The radio maps that index → Hz → centiHz (the index→Hz Tone Chart
# is verbatim from the manual; see ``radio._CTCSS_TONE_CENTIHZ``). The neutral
# value is centiHz = round(Hz * 100), matching the Icom MOR-451 convention
# (``round(_decode_tone_freq(...) * 100)``) so consumers see one unit.
# SINGLE-YAESU-TONE: unlike Icom (which can carry distinct TONE/TSQL freqs),
# the FTX-1 has ONE CTCSS tone frequency (CN P2=0) used for BOTH encode (TONE)
# and decode (TSQL). So the SAME centiHz value is emitted to BOTH tone_freq and
# tsql_freq from a SINGLE CN read. Emitted ALWAYS (the configured setpoint,
# like Icom's tone_freq readback) — independent of whether CTCSS is currently
# active. MAIN only (CN P1=0): the SUB receiver would need CN10, out of scope.
# DCS (CN P2=1) is a documented limitation — NO neutral DCS path is emitted.
_MAIN_TONE_FREQ = FieldPath.receiver("main", "operator_controls", "tone_freq")
_MAIN_TSQL_FREQ = FieldPath.receiver("main", "operator_controls", "tsql_freq")
YAESU_PTT_PATH = _PTT


class YaesuObservationRadio(Protocol):
    @property
    def capabilities(self) -> set[str]: ...

    async def read_freq(self, receiver: int = 0) -> int: ...

    async def read_mode(self, receiver: int = 0) -> tuple[str, int | None]: ...

    async def read_ptt(self) -> bool: ...

    async def read_af_level(self, receiver: int = 0) -> int: ...

    async def read_rf_gain(self, receiver: int = 0) -> int: ...

    async def read_squelch(self, receiver: int = 0) -> int: ...

    async def read_attenuator(self, receiver: int = 0) -> bool: ...

    async def read_preamp(self, band: int = 0) -> int: ...

    async def read_agc(self, receiver: int = 0) -> int: ...

    async def read_filter_width(
        self, receiver: int = 0, mode: str | None = None
    ) -> int: ...

    async def read_if_shift(self, receiver: int = 0) -> int: ...

    async def read_narrow(self, receiver: int = 0) -> bool: ...

    async def read_nb_level(self, receiver: int = 0) -> int: ...

    async def read_nr_level(self, receiver: int = 0) -> int: ...

    async def read_auto_notch(self, receiver: int = 0) -> bool: ...

    async def read_manual_notch(self, receiver: int = 0) -> bool: ...

    async def read_manual_notch_freq(self, receiver: int = 0) -> int: ...

    async def read_s_meter(self, receiver: int = 0) -> int: ...

    async def read_alc_meter(self) -> int: ...

    async def read_comp_meter(self) -> int: ...

    async def read_power_meter(self) -> int: ...

    async def read_swr_meter(self) -> int: ...

    async def read_power(self) -> tuple[int, int]: ...

    async def read_mic_gain(self) -> int: ...

    async def read_processor(self) -> bool: ...

    async def read_processor_level(self) -> int: ...

    async def read_vox(self) -> bool: ...

    async def read_split(self) -> bool: ...

    async def read_vfo_select(self) -> int: ...

    async def read_clarifier(self, receiver: int = 0) -> tuple[bool, bool]: ...

    async def read_clarifier_freq(self, receiver: int = 0) -> int: ...

    async def read_tuner(self) -> int: ...

    async def read_lock(self) -> bool: ...

    async def read_keyer_speed(self) -> int: ...

    async def read_cw_pitch(self) -> int: ...

    async def read_break_in(self) -> BreakInMode: ...

    async def read_break_in_delay(self) -> int: ...

    async def read_cw_spot(self) -> bool: ...

    async def read_sql_type(self, receiver: int = 0) -> int: ...

    async def read_ctcss_tone_index(self, receiver: int = 0) -> int: ...


@dataclass(slots=True)
class YaesuObservationAdapter:
    """Collect backend-neutral observations from Yaesu polling reads."""

    radio: YaesuObservationRadio
    profile: RadioAcquisitionProfile
    clock: Clock = time.monotonic

    @classmethod
    def from_radio(
        cls,
        radio: YaesuObservationRadio,
        *,
        clock: Clock = time.monotonic,
    ) -> YaesuObservationAdapter:
        profile = getattr(radio, "profile").state_acquisition
        if profile is None:
            raise ValueError(
                "radio profile does not declare state_acquisition metadata"
            )
        return cls(radio, profile=profile, clock=clock)

    async def poll_medium(self) -> tuple[Observation, ...]:
        adapter = self._adapter()
        observations: list[Observation] = []
        if self._can_poll(_MAIN_FREQ):
            ok, value = await self._safe_read("main.freq", self.radio.read_freq(0))
            if ok:
                observations.append(
                    adapter.observation(_MAIN_FREQ, value, native_id="read_freq")
                )
        # Capture the MAIN mode so filter_width (below) can resolve its
        # mode-specific width table from the freshly-read mode rather than
        # issuing a redundant CAT mode query on the hot poll path (MOR-507).
        main_mode: str | None = None
        if self._can_poll(_MAIN_MODE):
            ok, result = await self._safe_read("main.mode", self.radio.read_mode(0))
            if ok and result is not None:
                main_mode = result[0]
                observations.append(
                    adapter.observation(_MAIN_MODE, result[0], native_id="read_mode")
                )
        if self._has_runtime_capability("dual_rx") and self._can_poll(_SUB_FREQ):
            ok, value = await self._safe_read("sub.freq", self.radio.read_freq(1))
            if ok:
                observations.append(
                    adapter.observation(_SUB_FREQ, value, native_id="read_freq")
                )
        if self._has_runtime_capability("dual_rx") and self._can_poll(_SUB_MODE):
            ok, result = await self._safe_read("sub.mode", self.radio.read_mode(1))
            if ok and result is not None:
                observations.append(
                    adapter.observation(_SUB_MODE, result[0], native_id="read_mode")
                )
        if self._can_poll(_PTT):
            ok, value = await self._safe_read("ptt", self.radio.read_ptt())
            if ok:
                observations.append(
                    adapter.observation(_PTT, value, native_id="read_ptt")
                )
        # filter_width (MOR-445) is a ``freq_mode`` ACTIVE-slot field, so it
        # belongs in the freq/mode lane — mirroring the legacy poller, which
        # reads it in ``_poll_medium`` for responsive knob tracking. MAIN-only
        # and gated on the ``filter_width`` runtime capability.
        if self._has_runtime_capability("filter_width") and self._can_poll(
            _MAIN_FILTER_WIDTH
        ):
            ok, value = await self._safe_read(
                "main.filter_width", self.radio.read_filter_width(0, mode=main_mode)
            )
            if ok:
                observations.append(
                    adapter.observation(
                        _MAIN_FILTER_WIDTH, value, native_id="read_filter_width"
                    )
                )
        return tuple(observations)

    async def poll_rx_meters(
        self,
        *,
        smooth_s_meter: Callable[[int, int], int] | None = None,
    ) -> tuple[Observation, ...]:
        adapter = self._adapter()
        observations: list[Observation] = []
        if self._has_runtime_capability("meters") and self._can_poll(_MAIN_S_METER):
            ok, raw = await self._safe_read("main.s_meter", self.radio.read_s_meter(0))
            if ok and raw is not None:
                raw = smooth_s_meter(0, raw) if smooth_s_meter is not None else raw
                value, quality = self._calibrate_s_meter(raw)
                observations.append(
                    adapter.observation(
                        _MAIN_S_METER,
                        value,
                        native_id="read_s_meter",
                        quality=quality,
                    )
                )
        if (
            self._has_runtime_capability("meters")
            and self._has_runtime_capability("dual_rx")
            and self._can_poll(_SUB_S_METER)
        ):
            ok, raw = await self._safe_read("sub.s_meter", self.radio.read_s_meter(1))
            if ok and raw is not None:
                raw = smooth_s_meter(1, raw) if smooth_s_meter is not None else raw
                value, quality = self._calibrate_s_meter(raw)
                observations.append(
                    adapter.observation(
                        _SUB_S_METER,
                        value,
                        native_id="read_s_meter",
                        quality=quality,
                    )
                )
        return tuple(observations)

    def _calibrate_s_meter(self, raw: int) -> tuple[int, tuple[str, ...]]:
        profile = getattr(self.radio, "profile", None)
        meter_calibrations = getattr(profile, "meter_calibrations", None)
        if not isinstance(meter_calibrations, dict):
            return int(raw), ("confirmed", "uncalibrated")
        value, calibrated = interpolate_meter(raw, meter_calibrations, "s_meter")
        return (
            int(round(value)),
            ("confirmed", "calibrated" if calibrated else "uncalibrated"),
        )

    def _calibrate_meter(
        self, raw: int, meter_key: str
    ) -> tuple[int | float, tuple[str, ...]]:
        profile = getattr(self.radio, "profile", None)
        meter_calibrations = getattr(profile, "meter_calibrations", None)
        if not isinstance(meter_calibrations, dict):
            return int(raw), ("confirmed", "uncalibrated")
        value, calibrated = interpolate_meter(raw, meter_calibrations, meter_key)
        if not calibrated:
            return int(raw), ("confirmed", "uncalibrated")
        if meter_key == "alc":
            value = value / 100.0
        return value, ("confirmed", "calibrated")

    async def poll_tx_meters(self) -> tuple[Observation, ...]:
        adapter = self._adapter()
        observations: list[Observation] = []
        # ALC is a stream-like TX meter (MOR-448), emitted in the same lane and
        # under the same meter freshness/coalescing policy as power/swr.
        if self._has_runtime_capability("meters") and self._can_poll(_ALC_METER):
            ok, raw = await self._safe_read("alc", self.radio.read_alc_meter())
            if ok and raw is not None:
                value, quality = self._calibrate_meter(raw, "alc")
                observations.append(
                    adapter.observation(
                        _ALC_METER,
                        value,
                        native_id="read_alc_meter",
                        quality=quality,
                    )
                )
        if self._has_runtime_capability("meters") and self._can_poll(_POWER_METER):
            ok, raw = await self._safe_read("power", self.radio.read_power_meter())
            if ok and raw is not None:
                value, quality = self._calibrate_meter(raw, "power")
                observations.append(
                    adapter.observation(
                        _POWER_METER,
                        value,
                        native_id="read_power_meter",
                        quality=quality,
                    )
                )
        if self._has_runtime_capability("meters") and self._can_poll(_SWR_METER):
            ok, raw = await self._safe_read("swr", self.radio.read_swr_meter())
            if ok and raw is not None:
                value, quality = self._calibrate_meter(raw, "swr")
                observations.append(
                    adapter.observation(
                        _SWR_METER,
                        value,
                        native_id="read_swr_meter",
                        quality=quality,
                    )
                )
        # COMP is the cross-vendor PA meter (MOR-460), emitted in the same lane
        # and under the same meter freshness/coalescing policy as alc/power/swr.
        if self._has_runtime_capability("meters") and self._can_poll(_COMP_METER):
            ok, raw = await self._safe_read("comp", self.radio.read_comp_meter())
            if ok and raw is not None:
                value, quality = self._calibrate_meter(raw, "comp")
                observations.append(
                    adapter.observation(
                        _COMP_METER,
                        value,
                        native_id="read_comp_meter",
                        quality=quality,
                    )
                )
        return tuple(observations)

    async def poll_slow_controls(self) -> tuple[Observation, ...]:
        adapter = self._adapter()
        observations: list[Observation] = []
        if self._has_runtime_capability("af_level") and self._can_poll(_MAIN_AF):
            ok, value = await self._safe_read(
                "main.af_level", self.radio.read_af_level(0)
            )
            if ok:
                observations.append(
                    adapter.observation(_MAIN_AF, value, native_id="read_af_level")
                )
        if self._has_runtime_capability("rf_gain") and self._can_poll(_MAIN_RF):
            ok, value = await self._safe_read(
                "main.rf_gain", self.radio.read_rf_gain(0)
            )
            if ok:
                observations.append(
                    adapter.observation(_MAIN_RF, value, native_id="read_rf_gain")
                )
        if self._has_runtime_capability("squelch") and self._can_poll(_MAIN_SQL):
            ok, value = await self._safe_read(
                "main.squelch", self.radio.read_squelch(0)
            )
            if ok:
                observations.append(
                    adapter.observation(_MAIN_SQL, value, native_id="read_squelch")
                )
        if (
            self._has_runtime_capability("dual_rx")
            and self._has_runtime_capability("af_level")
            and self._can_poll(_SUB_AF)
        ):
            ok, value = await self._safe_read(
                "sub.af_level", self.radio.read_af_level(1)
            )
            if ok:
                observations.append(
                    adapter.observation(_SUB_AF, value, native_id="read_af_level")
                )
        if (
            self._has_runtime_capability("dual_rx")
            and self._has_runtime_capability("rf_gain")
            and self._can_poll(_SUB_RF)
        ):
            ok, value = await self._safe_read("sub.rf_gain", self.radio.read_rf_gain(1))
            if ok:
                observations.append(
                    adapter.observation(_SUB_RF, value, native_id="read_rf_gain")
                )
        if (
            self._has_runtime_capability("dual_rx")
            and self._has_runtime_capability("squelch")
            and self._can_poll(_SUB_SQL)
        ):
            ok, value = await self._safe_read("sub.squelch", self.radio.read_squelch(1))
            if ok:
                observations.append(
                    adapter.observation(_SUB_SQL, value, native_id="read_squelch")
                )
        # RF front-end + AGC (MOR-443) — MAIN-only. ATT/preamp gate on their
        # runtime capabilities (matching the legacy poller's ``attenuator`` /
        # ``preamp`` gates); AGC has no FTX-1 capability tag and is polled
        # unconditionally (gated by policy only), mirroring the legacy poller.
        # The ``RA0`` attenuator read returns a bool; the int registry path
        # receives the coerced ``int(on_off)`` (0/1) — no scaling beyond the
        # bool→int match (cross-vendor calibration is MOR-453).
        if self._has_runtime_capability("attenuator") and self._can_poll(_MAIN_ATT):
            ok, value = await self._safe_read("main.att", self.radio.read_attenuator(0))
            if ok and value is not None:
                observations.append(
                    adapter.observation(
                        _MAIN_ATT, int(value), native_id="read_attenuator"
                    )
                )
        if self._has_runtime_capability("preamp") and self._can_poll(_MAIN_PREAMP):
            ok, value = await self._safe_read("main.preamp", self.radio.read_preamp(0))
            if ok:
                observations.append(
                    adapter.observation(_MAIN_PREAMP, value, native_id="read_preamp")
                )
        if self._can_poll(_MAIN_AGC):
            ok, value = await self._safe_read("main.agc", self.radio.read_agc(0))
            if ok:
                observations.append(
                    adapter.observation(_MAIN_AGC, value, native_id="read_agc")
                )
        # IF-shift / narrow (MOR-445) — MAIN-only DSP controls. IF-shift gates
        # on the ``if_shift`` capability (matching the legacy poller's
        # ``if_shift`` gate); narrow has no FTX-1 capability tag and is polled
        # unconditionally (gated by policy only), mirroring the legacy poller's
        # "always — lightweight query" treatment, like AGC.
        if self._has_runtime_capability("if_shift") and self._can_poll(_MAIN_IF_SHIFT):
            ok, value = await self._safe_read(
                "main.if_shift", self.radio.read_if_shift(0)
            )
            if ok:
                observations.append(
                    adapter.observation(
                        _MAIN_IF_SHIFT, value, native_id="read_if_shift"
                    )
                )
        if self._can_poll(_MAIN_NARROW):
            ok, value = await self._safe_read("main.narrow", self.radio.read_narrow(0))
            if ok:
                observations.append(
                    adapter.observation(_MAIN_NARROW, value, native_id="read_narrow")
                )
        # NB/NR levels + derived toggles, auto/manual notch (MOR-444) —
        # MAIN-only DSP controls. NB/NR gate on their runtime capabilities
        # (matching the legacy poller's ``nb``/``nr`` gates); both notch
        # controls gate on the ``notch`` capability (matching the poller's
        # ``notch`` gate). The ``nb``/``nr`` toggles are DERIVED from the level
        # read in the same cycle (``level > 0``), a single CAT read each —
        # exactly as the legacy poller derives them; no second query.
        if self._has_runtime_capability("nb"):
            ok, nb_level = await self._safe_read(
                "main.nb_level", self.radio.read_nb_level(0)
            )
            if ok and nb_level is not None:
                if self._can_poll(_MAIN_NB_LEVEL):
                    observations.append(
                        adapter.observation(
                            _MAIN_NB_LEVEL,
                            nb_level,
                            native_id="read_nb_level",
                        )
                    )
                if self._can_poll(_MAIN_NB):
                    observations.append(
                        adapter.observation(
                            _MAIN_NB,
                            nb_level > 0,
                            native_id="read_nb_level",
                        )
                    )
        if self._has_runtime_capability("nr"):
            ok, nr_level = await self._safe_read(
                "main.nr_level", self.radio.read_nr_level(0)
            )
            if ok and nr_level is not None:
                if self._can_poll(_MAIN_NR_LEVEL):
                    observations.append(
                        adapter.observation(
                            _MAIN_NR_LEVEL,
                            nr_level,
                            native_id="read_nr_level",
                        )
                    )
                if self._can_poll(_MAIN_NR):
                    observations.append(
                        adapter.observation(
                            _MAIN_NR,
                            nr_level > 0,
                            native_id="read_nr_level",
                        )
                    )
        if self._has_runtime_capability("notch") and self._can_poll(_MAIN_AUTO_NOTCH):
            ok, value = await self._safe_read(
                "main.auto_notch", self.radio.read_auto_notch(0)
            )
            if ok:
                observations.append(
                    adapter.observation(
                        _MAIN_AUTO_NOTCH, value, native_id="read_auto_notch"
                    )
                )
        if self._has_runtime_capability("notch") and self._can_poll(_MAIN_MANUAL_NOTCH):
            ok, value = await self._safe_read(
                "main.manual_notch", self.radio.read_manual_notch(0)
            )
            if ok:
                observations.append(
                    adapter.observation(
                        _MAIN_MANUAL_NOTCH, value, native_id="read_manual_notch"
                    )
                )
        if self._has_runtime_capability("notch") and self._can_poll(
            _MAIN_MANUAL_NOTCH_FREQ
        ):
            ok, value = await self._safe_read(
                "main.manual_notch_freq", self.radio.read_manual_notch_freq(0)
            )
            if ok:
                observations.append(
                    adapter.observation(
                        _MAIN_MANUAL_NOTCH_FREQ,
                        value,
                        native_id="read_manual_notch_freq",
                    )
                )
        # Tone / CTCSS squelch-type (MOR-457) — MAIN-only per-receiver
        # ``operator_toggles``, grouped with the other receiver toggles
        # (nb/nr/auto_notch/manual_notch) above. A SINGLE ``read_sql_type(0)``
        # CAT ``CT`` read (FTX-1_CAT_OM_ENG_2507) yields the P2 "SQL TYPE" code,
        # from which the two mutually-exclusive neutral CTCSS booleans are
        # DERIVED (Hamlib/Icom convention; see the module-level mapping comment):
        # code 1 → tone only, code 2 → tsql only, codes 0/3/4/5 → both False.
        # Both paths are emitted every cycle (incl. the False derivations) so
        # the store always reflects current state. Gated on the ``sql_type``
        # runtime capability (``CAP_SQL_TYPE``), a dedicated readback capability:
        # ``"ctcss"`` is not a known capability tag (rejected by the rig loader),
        # and the Icom-style ``"repeater_tone"``/``"tsql"`` SET capabilities are
        # intentionally OFF on the FTX-1 because ``set_repeater_tone``/
        # ``set_repeater_tsql`` are not implemented (no false advertising, bug
        # #550). Each emission is gated independently by per-field policy. The
        # CTCSS tone FREQUENCY (CAT ``CN``) is a deferred follow-up, not emitted
        # here.
        if self._has_runtime_capability("sql_type"):
            ok, sql_type = await self._safe_read(
                "main.sql_type", self.radio.read_sql_type(0)
            )
            if ok and sql_type is not None:
                if self._can_poll(_MAIN_REPEATER_TONE):
                    observations.append(
                        adapter.observation(
                            _MAIN_REPEATER_TONE,
                            sql_type == 1,
                            native_id="read_sql_type",
                        )
                    )
                if self._can_poll(_MAIN_REPEATER_TSQL):
                    observations.append(
                        adapter.observation(
                            _MAIN_REPEATER_TSQL,
                            sql_type == 2,
                            native_id="read_sql_type",
                        )
                    )
        # CTCSS tone FREQUENCY (MOR-458) — MAIN-only per-receiver
        # ``operator_controls``, grouped with the CTCSS squelch-type toggles
        # above. A SINGLE ``read_ctcss_tone_index(0)`` CAT ``CN`` read
        # (FTX-1_CAT_OM_ENG_2507) yields the 0-49 standard-EIA tone-chart index,
        # which ``_ctcss_index_to_centihz`` maps index → Hz → centiHz (the
        # index→Hz Tone Chart is verbatim from the manual). The neutral unit is
        # centiHz = round(Hz * 100), matching the Icom MOR-451 convention so
        # consumers see one unit. SINGLE-YAESU-TONE: the FTX-1 has ONE CTCSS
        # tone frequency (CN P2=0) used for BOTH encode (TONE) and decode
        # (TSQL) — Icom can carry distinct freqs, Yaesu cannot — so the SAME
        # centiHz value is emitted to BOTH tone_freq and tsql_freq from one
        # read. Emitted ALWAYS (the configured setpoint, like Icom's tone_freq
        # readback), independent of whether CTCSS is currently active. Gated on
        # the same ``sql_type`` readback capability as the CTCSS toggles (the
        # FTX-1 CTCSS readback surface; the Icom-style ``tone_freq``/``tsql``
        # SET capabilities are intentionally off — no false advertising, bug
        # #550). MAIN only (CN P1=0). DCS (CN P2=1) is a documented limitation:
        # NO neutral DCS path is emitted.
        if self._has_runtime_capability("sql_type"):
            ok, tone_index = await self._safe_read(
                "main.ctcss_tone_index", self.radio.read_ctcss_tone_index(0)
            )
            if ok and tone_index is not None:
                tone_centihz = _ctcss_index_to_centihz(tone_index)
                if self._can_poll(_MAIN_TONE_FREQ):
                    observations.append(
                        adapter.observation(
                            _MAIN_TONE_FREQ,
                            tone_centihz,
                            native_id="read_ctcss_tone_index",
                        )
                    )
                if self._can_poll(_MAIN_TSQL_FREQ):
                    observations.append(
                        adapter.observation(
                            _MAIN_TSQL_FREQ,
                            tone_centihz,
                            native_id="read_ctcss_tone_index",
                        )
                    )
        # active-slot (MOR-446) — the GLOBAL "which receiver is active" field.
        # Polled unconditionally (gated by policy only), mirroring the legacy
        # poller's always-on ``get_vfo_select`` read, like AGC/narrow. The
        # ``VS`` index (0=MAIN, 1=SUB) is coerced to the neutral
        # ``global.slow_state.active`` ``"MAIN"``/``"SUB"`` str.
        if self._can_poll(_ACTIVE):
            ok, index = await self._safe_read("active", self.radio.read_vfo_select())
            if ok and index is not None:
                observations.append(
                    adapter.observation(
                        _ACTIVE,
                        _ACTIVE_INDEX_TO_STR.get(index, "MAIN"),
                        native_id="read_vfo_select",
                    )
                )
        # CW spot (MOR-456) — GLOBAL slow_state bool (CAT ``CS``), gated on the
        # legacy poller's single ``"cw" in caps`` gate (the same block that feeds
        # key_speed/cw_pitch/break_in). Emitted in the slow-control lane beside
        # ``slow_state.active``, the only other global slow_state observation.
        if self._has_runtime_capability("cw") and self._can_poll(_CW_SPOT):
            ok, value = await self._safe_read("cw_spot", self.radio.read_cw_spot())
            if ok:
                observations.append(
                    adapter.observation(
                        _CW_SPOT,
                        bool(value),
                        native_id="read_cw_spot",
                    )
                )
        return tuple(observations)

    async def poll_tx_controls(self) -> tuple[Observation, ...]:
        """Emit global TX / operator-control setpoints (MOR-447).

        Mirrors the per-receiver ``poll_slow_controls`` lane but covers
        the GLOBAL-scoped TX setpoints (power, mic gain, compressor,
        VOX). Each emission is gated by BOTH a runtime capability and the
        profile's per-field ``can_poll`` policy, matching the legacy
        poller's capability gates (``tx`` for power, unconditional mic
        gain, ``vox``, ``compressor`` for both compressor fields).

        ``power_level`` is the watt SETPOINT (CAT ``PC``), distinct from
        the ``global.meters.power`` meter handled by ``poll_tx_meters``.
        """
        adapter = self._adapter()
        observations: list[Observation] = []
        if self._has_runtime_capability("tx") and self._can_poll(_POWER_LEVEL):
            ok, result = await self._safe_read("power_level", self.radio.read_power())
            if ok and result is not None:
                observations.append(
                    adapter.observation(
                        _POWER_LEVEL,
                        result[1],
                        native_id="read_power",
                    )
                )
        if self._can_poll(_MIC_GAIN):
            ok, value = await self._safe_read("mic_gain", self.radio.read_mic_gain())
            if ok:
                observations.append(
                    adapter.observation(_MIC_GAIN, value, native_id="read_mic_gain")
                )
        if self._has_runtime_capability("compressor") and self._can_poll(
            _COMPRESSOR_ON
        ):
            ok, value = await self._safe_read(
                "compressor_on", self.radio.read_processor()
            )
            if ok:
                observations.append(
                    adapter.observation(
                        _COMPRESSOR_ON, value, native_id="read_processor"
                    )
                )
        if self._has_runtime_capability("compressor") and self._can_poll(
            _COMPRESSOR_LEVEL
        ):
            ok, value = await self._safe_read(
                "compressor_level", self.radio.read_processor_level()
            )
            if ok:
                observations.append(
                    adapter.observation(
                        _COMPRESSOR_LEVEL, value, native_id="read_processor_level"
                    )
                )
        if self._has_runtime_capability("vox") and self._can_poll(_VOX_ON):
            ok, value = await self._safe_read("vox", self.radio.read_vox())
            if ok:
                observations.append(
                    adapter.observation(_VOX_ON, value, native_id="read_vox")
                )
        # split (MOR-446) — GLOBAL tx_state bool (CAT ``ST``), gated on the
        # ``split`` runtime capability, mirroring the legacy poller's
        # ``"split" in caps`` gate.
        if self._has_runtime_capability("split") and self._can_poll(_SPLIT):
            ok, value = await self._safe_read("split", self.radio.read_split())
            if ok:
                observations.append(
                    adapter.observation(_SPLIT, value, native_id="read_split")
                )
        # Clarifier RIT/XIT (MOR-454) — GLOBAL slow-changing operator/TX
        # controls (CAT ``CF000``/``CF001``), gated on the ``rit`` runtime
        # capability, mirroring the legacy poller's ``"rit" in caps`` gate. The
        # ``rit_on``/``rit_tx`` flags come from a single ``read_clarifier`` read
        # (rx,tx), and ``rit_freq`` from a single ``read_clarifier_freq`` read —
        # exactly the poller's read pair, never an extra query. The signed Hz
        # offset is emitted on the device scale (cross-vendor calibration is
        # MOR-453); each emission is gated independently by per-field policy.
        if self._has_runtime_capability("rit"):
            ok, clar = await self._safe_read("clarifier", self.radio.read_clarifier(0))
            if ok and clar is not None:
                rx_clar, tx_clar = clar
                if self._can_poll(_RIT_ON):
                    observations.append(
                        adapter.observation(
                            _RIT_ON,
                            rx_clar,
                            native_id="read_clarifier",
                        )
                    )
                if self._can_poll(_RIT_TX):
                    observations.append(
                        adapter.observation(
                            _RIT_TX,
                            tx_clar,
                            native_id="read_clarifier",
                        )
                    )
            if self._can_poll(_RIT_FREQ):
                ok, freq = await self._safe_read(
                    "clarifier_freq", self.radio.read_clarifier_freq(0)
                )
                if ok:
                    observations.append(
                        adapter.observation(
                            _RIT_FREQ,
                            freq,
                            native_id="read_clarifier_freq",
                        )
                    )
        # Antenna tuner (MOR-455) — GLOBAL operator-control state (CAT ``AC``),
        # gated on the ``tuner`` runtime capability, mirroring the legacy
        # poller's ``"tuner" in caps`` gate. Emitted as the raw device int
        # (0-3); cross-vendor calibration is MOR-453.
        if self._has_runtime_capability("tuner") and self._can_poll(_TUNER):
            ok, value = await self._safe_read("tuner", self.radio.read_tuner())
            if ok and value is not None:
                observations.append(
                    adapter.observation(
                        _TUNER,
                        int(value),
                        native_id="read_tuner",
                    )
                )
        # Dial lock (MOR-455) — GLOBAL tx_state bool (CAT ``LK``), gated on the
        # ``dial_lock`` runtime capability, mirroring the legacy poller's
        # ``"dial_lock" in caps`` gate.
        if self._has_runtime_capability("dial_lock") and self._can_poll(_DIAL_LOCK):
            ok, value = await self._safe_read("dial_lock", self.radio.read_lock())
            if ok:
                observations.append(
                    adapter.observation(
                        _DIAL_LOCK,
                        bool(value),
                        native_id="read_lock",
                    )
                )
        # CW keyer family (MOR-456) — GLOBAL operator-control setpoints, all
        # gated on the legacy poller's single ``"cw" in caps`` gate, mirroring
        # its ``key_speed``/``cw_pitch``/``break_in``/``break_in_delay`` reads in
        # the same pass. ``key_speed`` is the keyer WPM (CAT ``KS``); ``cw_pitch``
        # is the sidetone in Hz (CAT ``KP`` idx → ``300 + idx * 10``);
        # ``break_in`` is emitted as the device int (CAT ``BI``: 0=OFF, 1=SEMI),
        # exactly the poller's ``1 if get_break_in() else 0`` int store;
        # ``break_in_delay`` is the QSK delay in ms (CAT ``SD``). Raw device scale
        # (cross-vendor calibration is MOR-453); each emission is gated
        # independently by per-field policy.
        if self._has_runtime_capability("cw"):
            if self._can_poll(_KEY_SPEED):
                ok, value = await self._safe_read(
                    "key_speed", self.radio.read_keyer_speed()
                )
                if ok and value is not None:
                    observations.append(
                        adapter.observation(
                            _KEY_SPEED,
                            int(value),
                            native_id="read_keyer_speed",
                        )
                    )
            if self._can_poll(_CW_PITCH):
                ok, value = await self._safe_read(
                    "cw_pitch", self.radio.read_cw_pitch()
                )
                if ok and value is not None:
                    observations.append(
                        adapter.observation(
                            _CW_PITCH,
                            int(value),
                            native_id="read_cw_pitch",
                        )
                    )
            if self._can_poll(_BREAK_IN):
                ok, value = await self._safe_read(
                    "break_in", self.radio.read_break_in()
                )
                if ok and value is not None:
                    observations.append(
                        adapter.observation(
                            _BREAK_IN,
                            int(value),
                            native_id="read_break_in",
                        )
                    )
            if self._can_poll(_BREAK_IN_DELAY):
                ok, value = await self._safe_read(
                    "break_in_delay", self.radio.read_break_in_delay()
                )
                if ok and value is not None:
                    observations.append(
                        adapter.observation(
                            _BREAK_IN_DELAY,
                            int(value),
                            native_id="read_break_in_delay",
                        )
                    )
        return tuple(observations)

    async def _safe_read(
        self, label: str, read: Awaitable[_T]
    ) -> tuple[bool, _T | None]:
        """Await one field read, tolerating FIELD-level CAT failures (MOR-473).

        Returns ``(ok, value)``. On a field-level malformed/unsupported answer
        the read is skipped: the warning is logged (the field ``label`` plus the
        exception, which already embeds the offending CAT template + frame) and
        ``(False, None)`` is returned so the caller drops just that field (or the
        whole derived group it feeds).

        CONNECTION/timeout errors are NOT caught — they RE-RAISE so the poller's
        ``_run_poll_cycle`` reconnect/backoff still fires; a dead link must never
        be masked as a skipped field. ``CatCommandRejected`` and
        ``CatTimeoutError`` both subclass ``CatTransportError``, so the SPECIFIC
        ``CatCommandRejected`` (a ``?;`` reject = unsupported command on this
        radio) is caught while the base/timeout propagates.

        MOR-561: a permanently unsupported field (e.g. the FTX-1 answering the
        SUB ``SM1;`` query with a main-form ``SM0000;`` frame) fails identically
        every poll cycle, several times a second. The FIRST failure for a given
        field warns; every repeat is demoted to DEBUG so the log is not flooded.
        """
        try:
            return True, await read
        except (CatParseError, CatFormatError, ValueError, KeyError) as exc:
            # ValueError covers _read_meter / int() malformed-frame failures;
            # CatParse/FormatError subclass ValueError but are listed for clarity.
            self._log_field_skip(
                label, "Skipping field %s — malformed CAT response: %s", exc
            )
            return False, None
        except CatCommandRejected as exc:
            # ``?;`` reject = command unsupported on this radio -> skip the field.
            self._log_field_skip(
                label, "Skipping field %s — command rejected (?;): %s", exc
            )
            return False, None

    def _log_field_skip(self, label: str, message: str, exc: Exception) -> None:
        """Warn once per field, then demote repeats to DEBUG (MOR-561).

        The warned-field set lives on the radio (persistent across poll cycles)
        rather than the adapter (rebuilt every cycle). A non-``set`` attribute —
        e.g. a ``MagicMock`` test double — falls back to always-warn.
        """
        warned = getattr(self.radio, "_poll_warned_fields", None)
        if isinstance(warned, set):
            if label in warned:
                logger.debug(message, label, exc)
                return
            warned.add(label)
        logger.warning(message, label, exc)

    def _adapter(self) -> ProviderObservationAdapter:
        return ProviderObservationAdapter(
            profile=self.profile,
            source="yaesu_poll_response",
            transport="serial",
            clock=self.clock,
        )

    def _can_poll(self, path: FieldPath) -> bool:
        capability = self.profile.capability_for(path)
        return bool(capability.can_poll)

    def _has_runtime_capability(self, capability: str) -> bool:
        raw: object = getattr(self.radio, "capabilities", set())
        return capability in raw if isinstance(raw, set) else False
