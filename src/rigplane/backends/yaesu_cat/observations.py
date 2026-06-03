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

__all__ = ["YaesuObservationAdapter"]

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


class YaesuObservationRadio(Protocol):
    capabilities: set[str]

    async def get_freq(self, receiver: int = 0) -> int: ...

    async def get_mode(self, receiver: int = 0) -> tuple[str, int | None]: ...

    async def get_ptt(self) -> bool: ...

    async def get_af_level(self, receiver: int = 0) -> int: ...

    async def get_rf_gain(self, receiver: int = 0) -> int: ...

    async def get_squelch(self, receiver: int = 0) -> int: ...


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
            observations.append(
                adapter.observation(
                    _MAIN_FREQ,
                    await self.radio.get_freq(0),
                    native_id="get_freq",
                )
            )
        if self._can_poll(_MAIN_MODE):
            mode, _ = await self.radio.get_mode(0)
            observations.append(
                adapter.observation(
                    _MAIN_MODE,
                    mode,
                    native_id="get_mode",
                )
            )
        if self._has_runtime_capability("dual_rx") and self._can_poll(_SUB_FREQ):
            observations.append(
                adapter.observation(
                    _SUB_FREQ,
                    await self.radio.get_freq(1),
                    native_id="get_freq",
                )
            )
        if self._has_runtime_capability("dual_rx") and self._can_poll(_SUB_MODE):
            mode, _ = await self.radio.get_mode(1)
            observations.append(
                adapter.observation(
                    _SUB_MODE,
                    mode,
                    native_id="get_mode",
                )
            )
        if self._can_poll(_PTT):
            observations.append(
                adapter.observation(
                    _PTT,
                    await self.radio.get_ptt(),
                    native_id="get_ptt",
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
                    await self.radio.get_af_level(0),
                    native_id="get_af_level",
                )
            )
        if self._has_runtime_capability("rf_gain") and self._can_poll(_MAIN_RF):
            observations.append(
                adapter.observation(
                    _MAIN_RF,
                    await self.radio.get_rf_gain(0),
                    native_id="get_rf_gain",
                )
            )
        if self._has_runtime_capability("squelch") and self._can_poll(_MAIN_SQL):
            observations.append(
                adapter.observation(
                    _MAIN_SQL,
                    await self.radio.get_squelch(0),
                    native_id="get_squelch",
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
                    await self.radio.get_af_level(1),
                    native_id="get_af_level",
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
                    await self.radio.get_rf_gain(1),
                    native_id="get_rf_gain",
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
                    await self.radio.get_squelch(1),
                    native_id="get_squelch",
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
