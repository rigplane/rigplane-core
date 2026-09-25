"""Helpers for translating backend reads into state observations."""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from rigplane.core.command_service import command_response_observation
from rigplane.core.state_acquisition_policy import RadioAcquisitionProfile
from rigplane.core.state_pipeline_contracts import (
    CommandIntent,
    FieldPath,
    Observation,
    ObservationSource,
    SourceMetadata,
)

Clock = Callable[[], float]

__all__ = ["ProviderObservationAdapter"]


@dataclass(frozen=True, slots=True)
class ProviderObservationAdapter:
    """Build observations using provider capability and policy metadata.

    ``observed_active_getter`` lets a seat supply the currently observed
    ``global.slow_state.active`` value (MOR-2599): on the only demoting
    profile flow today (the IC-7610 via ``icom_civ``) TTL stamping happens
    in ``runtime/_civ_rx.py``, which carries its own resolution; hamlib /
    Yaesu seats never demote (the FTX-1 is outside scheduler cadence), so
    the default ``None`` keeps every existing builder byte-identical.
    """

    profile: RadioAcquisitionProfile
    source: ObservationSource
    transport: str | None = None
    clock: Clock = time.monotonic
    observed_active_getter: Callable[[], str | None] | None = None

    def _observed_active(self) -> str | None:
        getter = self.observed_active_getter
        return getter() if getter is not None else None

    def observation(
        self,
        path: FieldPath,
        value: Any,
        *,
        native_id: str | None = None,
        timestamp_monotonic: float | None = None,
        max_age: float | None = None,
        quality: tuple[str, ...] = ("confirmed",),
    ) -> Observation:
        return Observation(
            path=path,
            value=value,
            source=SourceMetadata(
                source=self.source,
                provider=self.profile.provider,
                transport=self.transport,
                native_id=native_id,
                capability_id=str(path),
            ),
            timestamp_monotonic=(
                self.clock() if timestamp_monotonic is None else timestamp_monotonic
            ),
            max_age=(
                self.profile.policy_for(
                    path, observed_active=self._observed_active()
                ).freshness_ttl_seconds
                if max_age is None
                else max_age
            ),
            quality=quality,
        )

    def command_response(
        self,
        intent: CommandIntent,
        *,
        value: Any = None,
        timestamp_monotonic: float | None = None,
    ) -> Observation:
        observation = command_response_observation(
            intent,
            timestamp_monotonic=(
                self.clock() if timestamp_monotonic is None else timestamp_monotonic
            ),
            provider=self.profile.provider,
            transport=self.transport,
            value=value,
        )
        if intent.target is None:
            return observation
        freshness_ttl: float | None = self.profile.policy_for(
            intent.target, observed_active=self._observed_active()
        ).freshness_ttl_seconds
        return Observation(
            path=observation.path,
            value=observation.value,
            source=observation.source,
            timestamp_monotonic=observation.timestamp_monotonic,
            quality=observation.quality,
            correlation_id=observation.correlation_id,
            max_age=freshness_ttl,
        )
