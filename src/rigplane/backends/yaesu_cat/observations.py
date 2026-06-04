"""Observation adapters for Yaesu CAT polling reads."""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Protocol

from rigplane.core.observation_adapter import ProviderObservationAdapter
from rigplane.core.state_acquisition_policy import RadioAcquisitionProfile
from rigplane.core.state_pipeline_contracts import FieldPath, Observation

Clock = Callable[[], float]

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
YAESU_PTT_PATH = _PTT


class YaesuObservationRadio(Protocol):
    capabilities: set[str]

    async def read_freq(self, receiver: int = 0) -> int: ...

    async def read_mode(self, receiver: int = 0) -> tuple[str, int | None]: ...

    async def read_ptt(self) -> bool: ...

    async def read_af_level(self, receiver: int = 0) -> int: ...

    async def read_rf_gain(self, receiver: int = 0) -> int: ...

    async def read_squelch(self, receiver: int = 0) -> int: ...

    async def read_attenuator(self, receiver: int = 0) -> bool: ...

    async def read_preamp(self, band: int = 0) -> int: ...

    async def read_agc(self, receiver: int = 0) -> int: ...

    async def read_filter_width(self, receiver: int = 0) -> int: ...

    async def read_if_shift(self, receiver: int = 0) -> int: ...

    async def read_narrow(self, receiver: int = 0) -> bool: ...

    async def read_nb_level(self, receiver: int = 0) -> int: ...

    async def read_nr_level(self, receiver: int = 0) -> int: ...

    async def read_auto_notch(self, receiver: int = 0) -> bool: ...

    async def read_manual_notch(self, receiver: int = 0) -> bool: ...

    async def read_manual_notch_freq(self, receiver: int = 0) -> int: ...

    async def read_s_meter(self, receiver: int = 0) -> int: ...

    async def read_alc_meter(self) -> int: ...

    async def read_power_meter(self) -> int: ...

    async def read_swr_meter(self) -> int: ...

    async def read_power(self) -> tuple[int, int]: ...

    async def read_mic_gain(self) -> int: ...

    async def read_processor(self) -> bool: ...

    async def read_processor_level(self) -> int: ...

    async def read_vox(self) -> bool: ...


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
            raise ValueError("radio profile does not declare state_acquisition metadata")
        return cls(radio, profile=profile, clock=clock)

    async def poll_medium(self) -> tuple[Observation, ...]:
        adapter = self._adapter()
        observations: list[Observation] = []
        if self._can_poll(_MAIN_FREQ):
            observations.append(
                adapter.observation(
                    _MAIN_FREQ,
                    await self.radio.read_freq(0),
                    native_id="read_freq",
                )
            )
        if self._can_poll(_MAIN_MODE):
            mode, _ = await self.radio.read_mode(0)
            observations.append(
                adapter.observation(
                    _MAIN_MODE,
                    mode,
                    native_id="read_mode",
                )
            )
        if self._has_runtime_capability("dual_rx") and self._can_poll(_SUB_FREQ):
            observations.append(
                adapter.observation(
                    _SUB_FREQ,
                    await self.radio.read_freq(1),
                    native_id="read_freq",
                )
            )
        if self._has_runtime_capability("dual_rx") and self._can_poll(_SUB_MODE):
            mode, _ = await self.radio.read_mode(1)
            observations.append(
                adapter.observation(
                    _SUB_MODE,
                    mode,
                    native_id="read_mode",
                )
            )
        if self._can_poll(_PTT):
            observations.append(
                adapter.observation(
                    _PTT,
                    await self.radio.read_ptt(),
                    native_id="read_ptt",
                )
            )
        # filter_width (MOR-445) is a ``freq_mode`` ACTIVE-slot field, so it
        # belongs in the freq/mode lane — mirroring the legacy poller, which
        # reads it in ``_poll_medium`` for responsive knob tracking. MAIN-only
        # and gated on the ``filter_width`` runtime capability.
        if self._has_runtime_capability("filter_width") and self._can_poll(
            _MAIN_FILTER_WIDTH
        ):
            observations.append(
                adapter.observation(
                    _MAIN_FILTER_WIDTH,
                    await self.radio.read_filter_width(0),
                    native_id="read_filter_width",
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
            raw = await self.radio.read_s_meter(0)
            value = smooth_s_meter(0, raw) if smooth_s_meter is not None else raw
            observations.append(
                adapter.observation(
                    _MAIN_S_METER,
                    value,
                    native_id="read_s_meter",
                )
            )
        if (
            self._has_runtime_capability("meters")
            and self._has_runtime_capability("dual_rx")
            and self._can_poll(_SUB_S_METER)
        ):
            raw = await self.radio.read_s_meter(1)
            value = smooth_s_meter(1, raw) if smooth_s_meter is not None else raw
            observations.append(
                adapter.observation(
                    _SUB_S_METER,
                    value,
                    native_id="read_s_meter",
                )
            )
        return tuple(observations)

    async def poll_tx_meters(self) -> tuple[Observation, ...]:
        adapter = self._adapter()
        observations: list[Observation] = []
        # ALC is a stream-like TX meter (MOR-448), emitted in the same lane and
        # under the same meter freshness/coalescing policy as power/swr.
        if self._has_runtime_capability("meters") and self._can_poll(_ALC_METER):
            observations.append(
                adapter.observation(
                    _ALC_METER,
                    await self.radio.read_alc_meter(),
                    native_id="read_alc_meter",
                )
            )
        if self._has_runtime_capability("meters") and self._can_poll(_POWER_METER):
            observations.append(
                adapter.observation(
                    _POWER_METER,
                    await self.radio.read_power_meter(),
                    native_id="read_power_meter",
                )
            )
        if self._has_runtime_capability("meters") and self._can_poll(_SWR_METER):
            observations.append(
                adapter.observation(
                    _SWR_METER,
                    await self.radio.read_swr_meter(),
                    native_id="read_swr_meter",
                )
            )
        return tuple(observations)

    async def poll_slow_controls(self) -> tuple[Observation, ...]:
        adapter = self._adapter()
        observations: list[Observation] = []
        if self._has_runtime_capability("af_level") and self._can_poll(_MAIN_AF):
            observations.append(
                adapter.observation(
                    _MAIN_AF,
                    await self.radio.read_af_level(0),
                    native_id="read_af_level",
                )
            )
        if self._has_runtime_capability("rf_gain") and self._can_poll(_MAIN_RF):
            observations.append(
                adapter.observation(
                    _MAIN_RF,
                    await self.radio.read_rf_gain(0),
                    native_id="read_rf_gain",
                )
            )
        if self._has_runtime_capability("squelch") and self._can_poll(_MAIN_SQL):
            observations.append(
                adapter.observation(
                    _MAIN_SQL,
                    await self.radio.read_squelch(0),
                    native_id="read_squelch",
                )
            )
        if (
            self._has_runtime_capability("dual_rx")
            and self._has_runtime_capability("af_level")
            and self._can_poll(_SUB_AF)
        ):
            observations.append(
                adapter.observation(
                    _SUB_AF,
                    await self.radio.read_af_level(1),
                    native_id="read_af_level",
                )
            )
        if (
            self._has_runtime_capability("dual_rx")
            and self._has_runtime_capability("rf_gain")
            and self._can_poll(_SUB_RF)
        ):
            observations.append(
                adapter.observation(
                    _SUB_RF,
                    await self.radio.read_rf_gain(1),
                    native_id="read_rf_gain",
                )
            )
        if (
            self._has_runtime_capability("dual_rx")
            and self._has_runtime_capability("squelch")
            and self._can_poll(_SUB_SQL)
        ):
            observations.append(
                adapter.observation(
                    _SUB_SQL,
                    await self.radio.read_squelch(1),
                    native_id="read_squelch",
                )
            )
        # RF front-end + AGC (MOR-443) — MAIN-only. ATT/preamp gate on their
        # runtime capabilities (matching the legacy poller's ``attenuator`` /
        # ``preamp`` gates); AGC has no FTX-1 capability tag and is polled
        # unconditionally (gated by policy only), mirroring the legacy poller.
        # The ``RA0`` attenuator read returns a bool; the int registry path
        # receives the coerced ``int(on_off)`` (0/1) — no scaling beyond the
        # bool→int match (cross-vendor calibration is MOR-453).
        if self._has_runtime_capability("attenuator") and self._can_poll(_MAIN_ATT):
            observations.append(
                adapter.observation(
                    _MAIN_ATT,
                    int(await self.radio.read_attenuator(0)),
                    native_id="read_attenuator",
                )
            )
        if self._has_runtime_capability("preamp") and self._can_poll(_MAIN_PREAMP):
            observations.append(
                adapter.observation(
                    _MAIN_PREAMP,
                    await self.radio.read_preamp(0),
                    native_id="read_preamp",
                )
            )
        if self._can_poll(_MAIN_AGC):
            observations.append(
                adapter.observation(
                    _MAIN_AGC,
                    await self.radio.read_agc(0),
                    native_id="read_agc",
                )
            )
        # IF-shift / narrow (MOR-445) — MAIN-only DSP controls. IF-shift gates
        # on the ``if_shift`` capability (matching the legacy poller's
        # ``if_shift`` gate); narrow has no FTX-1 capability tag and is polled
        # unconditionally (gated by policy only), mirroring the legacy poller's
        # "always — lightweight query" treatment, like AGC.
        if self._has_runtime_capability("if_shift") and self._can_poll(_MAIN_IF_SHIFT):
            observations.append(
                adapter.observation(
                    _MAIN_IF_SHIFT,
                    await self.radio.read_if_shift(0),
                    native_id="read_if_shift",
                )
            )
        if self._can_poll(_MAIN_NARROW):
            observations.append(
                adapter.observation(
                    _MAIN_NARROW,
                    await self.radio.read_narrow(0),
                    native_id="read_narrow",
                )
            )
        # NB/NR levels + derived toggles, auto/manual notch (MOR-444) —
        # MAIN-only DSP controls. NB/NR gate on their runtime capabilities
        # (matching the legacy poller's ``nb``/``nr`` gates); both notch
        # controls gate on the ``notch`` capability (matching the poller's
        # ``notch`` gate). The ``nb``/``nr`` toggles are DERIVED from the level
        # read in the same cycle (``level > 0``), a single CAT read each —
        # exactly as the legacy poller derives them; no second query.
        if self._has_runtime_capability("nb"):
            nb_level = await self.radio.read_nb_level(0)
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
            nr_level = await self.radio.read_nr_level(0)
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
            observations.append(
                adapter.observation(
                    _MAIN_AUTO_NOTCH,
                    await self.radio.read_auto_notch(0),
                    native_id="read_auto_notch",
                )
            )
        if self._has_runtime_capability("notch") and self._can_poll(_MAIN_MANUAL_NOTCH):
            observations.append(
                adapter.observation(
                    _MAIN_MANUAL_NOTCH,
                    await self.radio.read_manual_notch(0),
                    native_id="read_manual_notch",
                )
            )
        if self._has_runtime_capability("notch") and self._can_poll(
            _MAIN_MANUAL_NOTCH_FREQ
        ):
            observations.append(
                adapter.observation(
                    _MAIN_MANUAL_NOTCH_FREQ,
                    await self.radio.read_manual_notch_freq(0),
                    native_id="read_manual_notch_freq",
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
            _, watts = await self.radio.read_power()
            observations.append(
                adapter.observation(
                    _POWER_LEVEL,
                    watts,
                    native_id="read_power",
                )
            )
        if self._can_poll(_MIC_GAIN):
            observations.append(
                adapter.observation(
                    _MIC_GAIN,
                    await self.radio.read_mic_gain(),
                    native_id="read_mic_gain",
                )
            )
        if self._has_runtime_capability("compressor") and self._can_poll(
            _COMPRESSOR_ON
        ):
            observations.append(
                adapter.observation(
                    _COMPRESSOR_ON,
                    await self.radio.read_processor(),
                    native_id="read_processor",
                )
            )
        if self._has_runtime_capability("compressor") and self._can_poll(
            _COMPRESSOR_LEVEL
        ):
            observations.append(
                adapter.observation(
                    _COMPRESSOR_LEVEL,
                    await self.radio.read_processor_level(),
                    native_id="read_processor_level",
                )
            )
        if self._has_runtime_capability("vox") and self._can_poll(_VOX_ON):
            observations.append(
                adapter.observation(
                    _VOX_ON,
                    await self.radio.read_vox(),
                    native_id="read_vox",
                )
            )
        return tuple(observations)

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
