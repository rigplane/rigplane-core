"""Transport-free lifecycle fence for managed provider PTT effects.

The adapter deliberately knows only how to identify the current physical port,
dispatch a PTT write, and perform a separate authoritative PTT read.  The
managed runtime remains the sole owner of leases, retries, watchdogs, and
effect ordering.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from rigplane.core.tx_safety import ProviderPttObservation, RadioTx

_Observer = Callable[[ProviderPttObservation], None]


@dataclass(frozen=True, slots=True)
class _Binding:
    provider_generation: int
    lifecycle_generation: int
    port_token: object
    observer: _Observer


class ProviderTxLifecycle:
    """Fence callable-based provider PTT I/O to one captured physical port."""

    def __init__(
        self,
        *,
        port_token: Callable[[], object | None],
        write_ptt: Callable[[bool], Awaitable[None]],
        read_ptt: Callable[[], Awaitable[bool]],
        clock: Callable[[], float] | None = None,
        drain_timeout_seconds: float = 3.0,
    ) -> None:
        if not 0 < drain_timeout_seconds < float("inf"):
            raise ValueError("drain_timeout_seconds must be positive and finite")
        self._port_token = port_token
        self._write_ptt = write_ptt
        self._read_ptt = read_ptt
        self._clock = clock or time.monotonic
        self._drain_timeout = drain_timeout_seconds
        self._binding: _Binding | None = None
        self._lifecycle_generation = 0
        self._observation_sequence = 0
        self._retired_provider_generations: set[int] = set()
        self._inflight: dict[asyncio.Task[object], _Binding] = {}

    def _live_port_token(self) -> object | None:
        try:
            return self._port_token()
        except Exception as exc:
            raise ConnectionError("managed TX physical port token unavailable") from exc

    def _require_current(
        self, provider_generation: int, observer: _Observer | None = None
    ) -> _Binding:
        binding = self._binding
        if (
            binding is None
            or binding.provider_generation != provider_generation
            or binding.lifecycle_generation != self._lifecycle_generation
            or self._live_port_token() is not binding.port_token
            or (observer is not None and observer is not binding.observer)
        ):
            raise ConnectionError("managed TX physical port is stale")
        return binding

    def _require_binding(self, binding: _Binding) -> None:
        if (
            self._require_current(binding.provider_generation, binding.observer)
            is not binding
        ):
            raise ConnectionError("managed TX physical port is stale")

    def _capture_managed_tx_port(
        self, provider_generation: int, observer: _Observer
    ) -> bool:
        if (
            not isinstance(provider_generation, int)
            or isinstance(provider_generation, bool)
            or provider_generation < 0
        ):
            raise ValueError("provider_generation must be a non-negative integer")
        if self._binding is not None:
            raise ConnectionError("managed TX physical port is already captured")
        if provider_generation in self._retired_provider_generations:
            raise ConnectionError("managed TX provider generation is terminal")
        token = self._live_port_token()
        if token is None:
            return False
        self._lifecycle_generation += 1
        self._binding = _Binding(
            provider_generation,
            self._lifecycle_generation,
            token,
            observer,
        )
        return True

    def _poison(self, binding: _Binding) -> tuple[asyncio.Task[object], ...]:
        if self._binding is not binding:
            return ()
        self._binding = None
        self._lifecycle_generation += 1
        self._retired_provider_generations.add(binding.provider_generation)
        current = asyncio.current_task()
        tasks = tuple(
            task
            for task, task_binding in self._inflight.items()
            if task_binding is binding and task is not current
        )
        for task in tasks:
            task.cancel()
        return tasks

    def _unbind_authoritative_ptt_observer(self) -> None:
        if (binding := self._binding) is not None:
            self._poison(binding)

    def _track_current_task(self, binding: _Binding) -> asyncio.Task[object]:
        task = asyncio.current_task()
        if task is None:
            raise RuntimeError("managed TX lifecycle requires an asyncio task")
        self._inflight[task] = binding
        return task

    def _untrack(self, task: asyncio.Task[object], binding: _Binding) -> None:
        if self._inflight.get(task) is binding:
            self._inflight.pop(task, None)

    async def _write_managed_ptt(self, provider_generation: int, on: bool) -> None:
        binding = self._require_current(provider_generation)
        task = self._track_current_task(binding)
        try:
            self._require_binding(binding)
            await self._write_ptt(on)
            self._require_binding(binding)
        finally:
            self._untrack(task, binding)

    async def _request_authoritative_ptt_read(
        self, *, provider_generation: int, observer: _Observer
    ) -> None:
        binding = self._require_current(provider_generation, observer)
        task = self._track_current_task(binding)
        try:
            self._require_binding(binding)
            value = await self._read_ptt()
            if type(value) is not bool:
                raise ValueError("authoritative PTT read must return bool")
            self._require_binding(binding)
            self._observation_sequence += 1
            observer(
                ProviderPttObservation(
                    RadioTx.ON if value else RadioTx.OFF,
                    provider_generation,
                    self._observation_sequence,
                    self._clock(),
                )
            )
        finally:
            self._untrack(task, binding)

    async def _retire_managed_tx_port(self, provider_generation: int) -> None:
        binding = self._binding
        if binding is None:
            if provider_generation in self._retired_provider_generations:
                return
            raise ConnectionError("managed TX physical port was not captured")
        if binding.provider_generation != provider_generation:
            raise ConnectionError("managed TX physical port is stale")

        # Poison and issue cancellation before retirement's first await.
        tasks = self._poison(binding)
        if tasks:
            await asyncio.wait(tasks, timeout=self._drain_timeout)
