"""Observation adapters for the external rigctld client backend."""

from __future__ import annotations

import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Protocol, TypeVar

from rigplane.core.acquisition_scheduler import (
    AcquisitionScheduler,
    DeclaredCommandDefect,
)
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
    startup_critical_path,
)
from rigplane.core.tx_observation import OBSERVED_PTT_PATH, normalize_observed_ptt
from rigplane.exceptions import ConnectionError as RadioConnectionError
from rigplane.exceptions import TimeoutError as RadioTimeoutError

Clock = Callable[[], float]
_T = TypeVar("_T")

#: MOR-2749 (owner decision, 2026-09-27 14:20 EDT) / MOR-2757: a
#: safety-critical path (:func:`startup_critical_path`) the radio has not
#: answered after this many consecutive reads ends startup through the
#: declared-command defect record — the same number the web gate's
#: ``_STARTUP_GATE_CRITICAL_ATTEMPTS`` (``web/web_startup.py``) carries for
#: the Icom sweep branch and the Yaesu CAT backend carries at its own read
#: sites (``backends/yaesu_cat/observations.py``); the backends cannot
#: import the web layer, so the owner's number is spelled out here with
#: this cross-reference.
_CRITICAL_READ_TIMEOUT_ATTEMPTS = 3

__all__ = [
    "RigctldClientObservationAdapter",
    "build_external_rigctld_acquisition_profile",
    "resolve_external_rigctld_poll_intervals",
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
_NORMALIZED_LEVEL_PATHS = frozenset({_RF_GAIN, _AF_LEVEL})
_SLOW_CONTROL_POLICY = AcquisitionPolicy(
    cadence_seconds=30.0,
    freshness_ttl_seconds=120.0,
)
#: The medium read loop's interval. Declared per path (MOR-2576) so the
#: external-rigctld profile keeps its own contract: an unowned pollable
#: path resolves to its acquisition class since MOR-2574 step 2, and this
#: provider's medium loop — not the class table — sets its cadence.
_MEDIUM_READ_POLICY = AcquisitionPolicy(
    cadence_seconds=2.0,
    freshness_ttl_seconds=8.0,
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
        FieldCapability(path=OBSERVED_PTT_PATH, polling=True),
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
            _FREQ: _MEDIUM_READ_POLICY,
            _MODE: _MEDIUM_READ_POLICY,
            _FILTER: _MEDIUM_READ_POLICY,
            _PTT: _MEDIUM_READ_POLICY,
            OBSERVED_PTT_PATH: _MEDIUM_READ_POLICY,
            _ACTIVE_VFO: _MEDIUM_READ_POLICY,
            _RF_GAIN: _SLOW_CONTROL_POLICY,
            _AF_LEVEL: _SLOW_CONTROL_POLICY,
            _PREAMP: _SLOW_CONTROL_POLICY,
            _ATT: _SLOW_CONTROL_POLICY,
            _NB: _SLOW_CONTROL_POLICY,
            _NR: _SLOW_CONTROL_POLICY,
        },
    )


def resolve_external_rigctld_poll_intervals(
    profile: RadioAcquisitionProfile,
) -> tuple[float, float]:
    """Return the (medium, slow) poll periods ``profile`` declares, in seconds."""
    medium = _require_cadence(profile.default_policy, label="default policy")
    # Witness path for the slow loop: ``_RF_GAIN`` and ``_AF_LEVEL`` hold the
    # same policy but ``RigctldClientObservationAdapter.read_freq_mode_controls``
    # also reads them on the medium loop, so their cadence does not describe the
    # slow period. ``_PREAMP`` is read on a cadence only by ``read_slow_controls``.
    slow = _require_cadence(profile.policy_for(_PREAMP), label=str(_PREAMP))
    return medium, slow


def _require_cadence(policy: AcquisitionPolicy, *, label: str) -> float:
    cadence = policy.cadence_seconds
    if cadence is None:
        raise ValueError(
            f"{label}: cadence_seconds is required to derive a poll period"
        )
    return cadence


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
            await self._read_critical("ptt", radio.get_ptt, _PTT, command="t"),
            native_id="t",
        )

    def observed_ptt_observation(
        self,
        value: object,
        *,
        timestamp_monotonic: float | None = None,
    ) -> Observation:
        """Build canonical diagnostic evidence without another radio read."""
        return self._adapter().observation(
            OBSERVED_PTT_PATH,
            normalize_observed_ptt(value),
            native_id="t",
            timestamp_monotonic=timestamp_monotonic,
        )

    async def read_freq(self) -> Observation:
        radio = self._require_radio()
        return self._observation(
            _FREQ,
            await self._read_critical("freq", radio.get_freq, _FREQ, command="f"),
            native_id="f",
        )

    async def read_mode(self) -> tuple[Observation, Observation]:
        radio = self._require_radio()
        mode, filter_width = await self._read_critical(
            "mode", radio.get_mode, _MODE, command="m"
        )
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
        if path in _NORMALIZED_LEVEL_PATHS:
            value = _normalize_level_255(value)
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

    async def _read_critical(
        self,
        label: str,
        read: Callable[[], Awaitable[_T]],
        path: FieldPath,
        *,
        command: str,
    ) -> _T:
        """Read one safety-critical path, counting an unanswered attempt.

        MOR-2757: a read that dies with the transport's unanswered-read
        failures — the read timeout, or the connection error the transport
        raises once that timeout has closed the connection — counts toward
        that path's consecutive-unread tally (any answer resets it), so
        the 3rd unanswered read records the declared-command defect that
        ends startup; the same record a refused read leaves. The re-raise
        keeps the poller's cycle-failure path unchanged. A read the radio
        *answers* — even with a malformed line — is an answer, not an
        unanswered attempt, and does not count.
        """

        try:
            value = await read()
        except (RadioTimeoutError, RadioConnectionError) as exc:
            self._count_unanswered_critical_reads(label, exc, path, command=command)
            raise
        self._note_critical_read_answers(path)
        return value

    def _critical_read_timeout_counts(self) -> dict[FieldPath, int] | None:
        """Return the radio's unanswered-critical-read tally, if it has one.

        The tally lives on the radio (``_critical_read_timeouts``) because
        this adapter is rebuilt every poll cycle while the count must span
        cycles — the same ``_poll_warned_fields`` idiom the Yaesu backend
        uses. A radio object that carries no tally (test doubles) simply
        does not count: the pre-MOR-2757 behaviour.
        """

        counts = getattr(self.radio, "_critical_read_timeouts", None)
        if isinstance(counts, dict):
            return counts
        return None

    def _note_critical_read_answers(self, path: FieldPath) -> None:
        """Reset the unanswered tally of the *path* that just answered."""

        counts = self._critical_read_timeout_counts()
        if counts is None:
            return
        counts.pop(path, None)

    def _count_unanswered_critical_reads(
        self,
        label: str,
        exc: Exception,
        path: FieldPath,
        *,
        command: str,
    ) -> None:
        """Count one unanswered read of a safety-critical path (MOR-2757).

        The medium poll cycle re-issues every read each interval, so one
        unanswered read per cycle is one unanswered attempt. Non-critical
        paths are not counted — the web gate's own 10 s deadline already
        stops them from blocking. On the attempt that reaches
        ``_CRITICAL_READ_TIMEOUT_ATTEMPTS`` the same
        :class:`DeclaredCommandDefect` a refused read records is recorded
        for that one path, naming the field and the rigctld command the
        radio never answered; the startup gate aborts on it. Later records
        keep only the first (``AcquisitionScheduler.record_startup_defect``).
        """

        counts = self._critical_read_timeout_counts()
        if counts is None or not startup_critical_path(path):
            return
        count = counts.get(path, 0) + 1
        counts[path] = count
        if count >= _CRITICAL_READ_TIMEOUT_ATTEMPTS:
            self._record_declared_defect(label, exc, path, command=command)

    def _record_declared_defect(
        self,
        label: str,
        exc: Exception,
        path: FieldPath,
        *,
        command: str,
    ) -> None:
        """Record the defect for a declared read the radio never answered.

        The recording is what the startup gate reads: it checks the
        scheduler's record before and during its wait, so the unanswered
        read that reaches the attempt limit refuses the bind — the same
        record and abort path a refused or unparseable read already uses;
        no second failure mechanism is added.
        """

        defect = DeclaredCommandDefect(
            label=label,
            paths=(path,),
            command=command,
            frame="",
            detail=str(exc),
        )
        scheduler = getattr(self.radio, "_acquisition_scheduler", None)
        if isinstance(scheduler, AcquisitionScheduler):
            scheduler.record_startup_defect(defect)


def _normalize_command_path(path: FieldPath) -> FieldPath:
    if path.scope is not FieldScope.RECEIVER or path.receiver_id != "0":
        return path
    if path.family.value == "freq_mode" and path.slot is None:
        return FieldPath.active("main", path.family.value, path.name)
    return FieldPath.receiver("main", path.family.value, path.name)


def _normalize_level_255(value: object) -> object:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return float(value) / 255.0
    return value
