"""Observation adapters for the external rigctld client backend."""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Protocol

from rigplane.core.observation_adapter import ProviderObservationAdapter
from rigplane.core.state_acquisition_policy import (
    AcquisitionPolicy,
    FieldAvailability,
    FieldCapability,
    RadioAcquisitionProfile,
)
from rigplane.core.state_pipeline_contracts import (
    CommandIntent,
    FieldPath,
    FieldScope,
    Observation,
)

Clock = Callable[[], float]

__all__ = [
    "RigctldClientObservationAdapter",
    "build_external_rigctld_acquisition_profile",
]

_FREQ = FieldPath.active("main", "freq_mode", "freq_hz")
_MODE = FieldPath.active("main", "freq_mode", "mode")
_FILTER = FieldPath.active("main", "freq_mode", "filter_width")
_PTT = FieldPath.global_("tx_state", "ptt")
_ACTIVE_VFO = FieldPath.active_slot("main")
_RF_GAIN = FieldPath.receiver("main", "operator_controls", "rf_gain")
_AF_LEVEL = FieldPath.receiver("main", "operator_controls", "af_level")
_PREAMP = FieldPath.receiver("main", "operator_controls", "preamp")
_ATT = FieldPath.receiver("main", "operator_controls", "att")
_NB = FieldPath.receiver("main", "operator_toggles", "nb")
_NR = FieldPath.receiver("main", "operator_toggles", "nr")
_POWER = FieldPath.global_("tx_state", "power_on")
_SLOW_CONTROL_POLICY = AcquisitionPolicy(
    cadence_seconds=30.0,
    freshness_ttl_seconds=120.0,
)


class RigctldObservationRadio(Protocol):
    _vfo_supported: bool

    async def get_freq(self, receiver: int = 0) -> int: ...

    async def get_mode(self, receiver: int = 0) -> tuple[str, int | None]: ...

    async def get_ptt(self) -> bool: ...

    async def get_rf_gain(self, receiver: int = 0) -> int: ...

    async def get_af_level(self, receiver: int = 0) -> int: ...

    async def get_preamp(self, receiver: int = 0) -> int: ...

    async def get_attenuator_level(self, receiver: int = 0) -> int: ...

    async def get_nb(self) -> bool: ...

    async def get_nr(self) -> bool: ...

    async def get_vfo_slot(self, receiver: int = 0) -> str: ...


def build_external_rigctld_acquisition_profile(
    *,
    vfo_supported: bool,
) -> RadioAcquisitionProfile:
    capabilities = [
        FieldCapability(
            path=_FREQ,
            polling=True,
            command_response_observable=True,
            supported_controls=("set_freq",),
        ),
        FieldCapability(
            path=_MODE,
            polling=True,
            command_response_observable=True,
            supported_controls=("set_mode",),
        ),
        FieldCapability(
            path=_FILTER,
            polling=True,
            command_response_observable=False,
            supported_controls=("set_mode",),
            diagnostic=(
                "External rigctld confirms filter width through get_mode polling "
                "readback, not a direct command response"
            ),
        ),
        FieldCapability(
            path=_PTT,
            polling=True,
            command_response_observable=True,
            supported_controls=("set_ptt",),
        ),
        FieldCapability(
            path=_RF_GAIN,
            polling=True,
            command_response_observable=True,
            supported_controls=("set_rf_gain",),
        ),
        FieldCapability(
            path=_AF_LEVEL,
            polling=True,
            command_response_observable=True,
            supported_controls=("set_af_level",),
        ),
        FieldCapability(
            path=_PREAMP,
            polling=True,
            command_response_observable=True,
            supported_controls=("set_preamp",),
        ),
        FieldCapability(
            path=_ATT,
            polling=True,
            command_response_observable=True,
            supported_controls=("set_attenuator", "set_attenuator_level"),
        ),
        FieldCapability(
            path=_NB,
            polling=True,
            command_response_observable=True,
            supported_controls=("set_nb",),
        ),
        FieldCapability(
            path=_NR,
            polling=True,
            command_response_observable=True,
            supported_controls=("set_nr",),
        ),
        FieldCapability(
            path=_ACTIVE_VFO,
            availability=(
                FieldAvailability.SUPPORTED
                if vfo_supported
                else FieldAvailability.UNSUPPORTED
            ),
            polling=vfo_supported,
            command_response_observable=vfo_supported,
            supported_controls=("set_vfo_slot",) if vfo_supported else (),
            diagnostic=(
                ""
                if vfo_supported
                else "External rigctld does not expose VFO slot commands"
            ),
        ),
        FieldCapability(
            path=_POWER,
            availability=FieldAvailability.UNSUPPORTED,
            diagnostic="External rigctld does not expose power state",
        ),
    ]
    return RadioAcquisitionProfile(
        provider="external_rigctld",
        capabilities=tuple(capabilities),
        default_policy=AcquisitionPolicy(
            cadence_seconds=2.0,
            freshness_ttl_seconds=8.0,
        ),
        field_policies={
            _RF_GAIN: _SLOW_CONTROL_POLICY,
            _AF_LEVEL: _SLOW_CONTROL_POLICY,
            _PREAMP: _SLOW_CONTROL_POLICY,
            _ATT: _SLOW_CONTROL_POLICY,
            _NB: _SLOW_CONTROL_POLICY,
            _NR: _SLOW_CONTROL_POLICY,
        },
    )


@dataclass(slots=True)
class RigctldClientObservationAdapter:
    """Collect backend-neutral observations from external rigctld reads."""

    radio: RigctldObservationRadio | None
    profile: RadioAcquisitionProfile
    clock: Clock = time.monotonic

    def __init__(
        self,
        radio: RigctldObservationRadio | None,
        *,
        profile: RadioAcquisitionProfile | None = None,
        clock: Clock = time.monotonic,
    ) -> None:
        self.radio = radio
        self.profile = profile or build_external_rigctld_acquisition_profile(
            vfo_supported=bool(getattr(radio, "_vfo_supported", True))
        )
        self.clock = clock

    async def read_freq_mode_controls(self) -> tuple[Observation, ...]:
        return (
            await self.read_freq(),
            *(await self.read_mode()),
            await self.read_rf_gain(),
            await self.read_af_level(),
        )

    async def read_ptt(self) -> Observation:
        radio = self._require_radio()
        return self._observation(
            _PTT,
            await radio.get_ptt(),
            native_id="t",
        )

    async def read_freq(self) -> Observation:
        radio = self._require_radio()
        return self._observation(
            _FREQ,
            await radio.get_freq(),
            native_id="f",
        )

    async def read_mode(self) -> tuple[Observation, Observation]:
        radio = self._require_radio()
        mode, filter_width = await radio.get_mode()
        adapter = self._adapter()
        return (
            adapter.observation(_MODE, mode, native_id="m"),
            adapter.observation(_FILTER, filter_width, native_id="m"),
        )

    async def read_rf_gain(self) -> Observation:
        radio = self._require_radio()
        return self._observation(
            _RF_GAIN,
            await radio.get_rf_gain(),
            native_id="l RF",
        )

    async def read_af_level(self) -> Observation:
        radio = self._require_radio()
        return self._observation(
            _AF_LEVEL,
            await radio.get_af_level(),
            native_id="l AF",
        )

    async def read_preamp(self) -> Observation:
        radio = self._require_radio()
        return self._observation(
            _PREAMP,
            await radio.get_preamp(),
            native_id="l PREAMP",
        )

    async def read_attenuator(self) -> Observation:
        radio = self._require_radio()
        return self._observation(
            _ATT,
            await radio.get_attenuator_level(),
            native_id="l ATT",
        )

    async def read_nb(self) -> Observation:
        radio = self._require_radio()
        return self._observation(
            _NB,
            await radio.get_nb(),
            native_id="u NB",
        )

    async def read_nr(self) -> Observation:
        radio = self._require_radio()
        return self._observation(
            _NR,
            await radio.get_nr(),
            native_id="u NR",
        )

    async def read_slow_controls(self) -> tuple[Observation, ...]:
        return (
            await self.read_rf_gain(),
            await self.read_af_level(),
            await self.read_preamp(),
            await self.read_attenuator(),
            await self.read_nb(),
            await self.read_nr(),
        )

    async def read_active_vfo(self) -> Observation | None:
        if not self.profile.capability_for(_ACTIVE_VFO).can_poll:
            return None
        radio = self._require_radio()
        return self._observation(
            _ACTIVE_VFO,
            await radio.get_vfo_slot(),
            native_id="v",
        )

    def command_response(
        self,
        intent: CommandIntent,
        *,
        value: object = None,
    ) -> Observation:
        observation = self._command_response_observation(intent, value=value)
        normalized_path = _normalize_command_path(observation.path)
        if normalized_path == observation.path:
            return observation
        return Observation(
            path=normalized_path,
            value=observation.value,
            source=observation.source,
            timestamp_monotonic=observation.timestamp_monotonic,
            quality=observation.quality,
            correlation_id=observation.correlation_id,
            max_age=self.profile.policy_for(normalized_path).freshness_ttl_seconds,
        )

    def _adapter(self) -> ProviderObservationAdapter:
        return ProviderObservationAdapter(
            profile=self.profile,
            source="hamlib_response",
            transport="rigctld",
            clock=self.clock,
        )

    def _observation(
        self,
        path: FieldPath,
        value: object,
        *,
        native_id: str | None = None,
    ) -> Observation:
        adapter = self._adapter()
        observation: Observation = adapter.observation(
            path,
            value,
            native_id=native_id,
        )
        return observation

    def _command_response_observation(
        self,
        intent: CommandIntent,
        *,
        value: object = None,
    ) -> Observation:
        adapter = self._adapter()
        observation: Observation = adapter.command_response(intent, value=value)
        return observation

    def _require_radio(self) -> RigctldObservationRadio:
        if self.radio is None:
            raise ValueError("radio is required for backend read observations")
        return self.radio


def _normalize_command_path(path: FieldPath) -> FieldPath:
    if path.scope is not FieldScope.RECEIVER or path.receiver_id != "0":
        return path
    if path.family.value == "freq_mode" and path.slot is None:
        return FieldPath.active("main", path.family.value, path.name)
    return FieldPath.receiver("main", path.family.value, path.name)
