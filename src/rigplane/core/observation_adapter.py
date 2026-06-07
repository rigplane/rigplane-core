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
    """Build observations using provider capability and policy metadata."""

    profile: RadioAcquisitionProfile
    source: ObservationSource
    transport: str | None = None
    clock: Clock = time.monotonic

    def observation(
        self,
        path: FieldPath,
        value: Any,
        *,
        native_id: str | None = None,
        timestamp_monotonic: float | None = None,
        max_age: float | None = None,
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
                self.profile.policy_for(path).freshness_ttl_seconds
                if max_age is None
                else max_age
            ),
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
        return Observation(
            path=observation.path,
            value=observation.value,
            source=observation.source,
            timestamp_monotonic=observation.timestamp_monotonic,
            quality=observation.quality,
            correlation_id=observation.correlation_id,
            max_age=self.profile.policy_for(intent.target).freshness_ttl_seconds,
        )
