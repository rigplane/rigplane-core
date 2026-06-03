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
_MAIN_S_METER = FieldPath.receiver("main", "meters", "s_meter")
_SUB_S_METER = FieldPath.receiver("sub", "meters", "s_meter")
_POWER_METER = FieldPath.global_("meters", "power")
_SWR_METER = FieldPath.global_("meters", "swr")
YAESU_PTT_PATH = _PTT


class YaesuObservationRadio(Protocol):
    capabilities: set[str]

    async def read_freq(self, receiver: int = 0) -> int: ...

    async def read_mode(self, receiver: int = 0) -> tuple[str, int | None]: ...

    async def read_ptt(self) -> bool: ...

    async def read_af_level(self, receiver: int = 0) -> int: ...

    async def read_rf_gain(self, receiver: int = 0) -> int: ...

    async def read_squelch(self, receiver: int = 0) -> int: ...

    async def read_s_meter(self, receiver: int = 0) -> int: ...

    async def read_power_meter(self) -> int: ...

    async def read_swr_meter(self) -> int: ...


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
