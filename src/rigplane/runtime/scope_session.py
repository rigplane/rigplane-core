"""One acknowledged scope-control session shared by SDK and web consumers."""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, replace
from typing import Literal, Protocol, TypeAlias, cast

from rigplane.core.types import ScopeFixedEdge
from rigplane.scope import ScopeFrame
from rigplane.scope.models import ScopeDemand

logger = logging.getLogger(__name__)

ScopeSetting: TypeAlias = Literal[
    "scope_on", "output_on", "mode", "span", "edge", "fixed_edge", "dual"
]
ScopeValue: TypeAlias = bool | int | ScopeFixedEdge


@dataclass(frozen=True)
class ScopeState:
    scope_on: bool
    output_on: bool
    mode: int = 0
    span: int = 2
    edge: int = 1
    dual: bool = False


@dataclass(frozen=True)
class ScopeSessionHealth:
    hidden: bool | None
    last_frame_at: float | None
    out_of_range: bool


class ScopeSession:
    def __init__(
        self,
        read: Callable[[], Awaitable[ScopeState]],
        write: Callable[[ScopeSetting, ScopeValue, int], Awaitable[None]],
        read_fixed: Callable[[int, int], Awaitable[ScopeFixedEdge]] | None = None,
        read_receiver: Callable[[int], Awaitable[ScopeState]] | None = None,
        frequency: Callable[[int], float] | None = None,
        spans: tuple[int, ...] = (),
        tx_probe: Callable[[], bool] | None = None,
        *,
        ready: bool = False,
        read_dual: Callable[[], Awaitable[bool]] | None = None,
        validate: Callable[[ScopeSetting, ScopeValue, int], object] | None = None,
        clock: Callable[[], float] = time.monotonic,
        disable_grace_seconds: float = 0,
        silence_seconds: float = 5,
        hidden_grace_seconds: float = 30,
    ) -> None:
        self._validate = validate
        self._read_dual = read_dual
        self._read = read
        self._write = write
        self._read_fixed = read_fixed
        self._read_receiver = read_receiver
        self._frequency = frequency
        self._spans = spans
        self.tx_probe = tx_probe
        self._lock = asyncio.Lock()
        self._ready = ready
        self._tx = bool(tx_probe and tx_probe())
        self._demands: dict[str, ScopeDemand] = {}
        self._clock = clock
        self._grace = disable_grace_seconds
        self._silence = silence_seconds
        self.hidden_grace_seconds = hidden_grace_seconds
        self._hidden_at: float | None = None
        self._connection_epoch: int | None = None
        self._release_at: float | None = None
        self._release_task: asyncio.Task[None] | None = None
        self._last_frame_at: float | None = None
        self._out_of_range = False
        self._enabled_at: float | None = None
        self._paused = False
        self.generation = 0
        self.observed: ScopeState | None = None
        self._original: ScopeState | None = None
        self._desired: dict[tuple[int, ScopeSetting], ScopeValue] = {}
        self._previous: dict[tuple[int, ScopeSetting], ScopeValue] = {}
        self._views: dict[int, ScopeState] = {}
        self._fixed: dict[tuple[int, int], ScopeFixedEdge] = {}
        self._applied: dict[tuple[int, ScopeSetting], ScopeValue] = {}
        self._uncertain: set[tuple[int, ScopeSetting]] = set()
        self._restore_pending = False
        self._event_tasks: set[asyncio.Task[None]] = set()

    @property
    def enabled(self) -> bool:
        return self.observed is not None and self.observed.output_on

    @property
    def restoring(self) -> bool:
        return self._restore_pending

    @property
    def active(self) -> bool:
        return (
            any(d.subscribers for d in self._demands.values())
            or self._original is not None
        )

    def tuned_frequency(self, receiver: int) -> float:
        if self._frequency is None:
            raise RuntimeError("scope frequency observation is unavailable")
        return self._frequency(receiver)

    async def set_output(self, enabled: bool, *, release: bool = True) -> None:
        current = self._demands.get("web", ScopeDemand(0, 0))
        subscribers = max(1, current.subscribers) if enabled or not release else 0
        active = max(1, current.active) if enabled else 0
        await self.set_demand(ScopeDemand(subscribers, active))

    async def set_center(self, center_hz: float, span_hz: float, receiver: int) -> None:
        await self.configure(
            receiver=receiver,
            mode=0,
            span=self._spans.index(int(span_hz)),
            **({"dual": True} if receiver else {}),
        )

    async def set_fixed(self, edges: ScopeFixedEdge, receiver: int) -> None:
        await self.configure(
            receiver=receiver,
            fixed_edge=edges,
            edge=edges.edge,
            mode=1,
            **({"dual": True} if receiver else {}),
        )

    def suspend(self) -> None:
        self._ready = False

    async def set_ready(self, ready: bool) -> None:
        async with self._lock:
            self._ready = ready
            await self._reconcile()

    async def set_tx(self, active: bool) -> None:
        async with self._lock:
            self._tx = active
            await self._reconcile()

    def invalidate_now(self, *, connection_epoch: int | None = None) -> None:
        if connection_epoch is not None and connection_epoch == self._connection_epoch:
            return
        self._connection_epoch = connection_epoch
        self.generation += 1
        if not self.menu_guard_active():
            self.observed = None
            self._applied.clear()
        self._ready = False

    async def invalidate(self, *, connection_epoch: int | None = None) -> None:
        async with self._lock:
            self.invalidate_now(connection_epoch=connection_epoch)

    def observe(self, name: ScopeSetting, value: ScopeValue, receiver: int = 0) -> None:
        if name == "fixed_edge" and isinstance(value, ScopeFixedEdge):
            matching = [
                key
                for key, prior in self._applied.items()
                if key[1] == name
                and isinstance(prior, ScopeFixedEdge)
                and (prior.range_index, prior.edge) == (value.range_index, value.edge)
            ]
            if matching:
                for key in matching:
                    self._applied[key] = value
                return
        self._applied[receiver, name] = value
        if name == "dual":
            if self.observed is not None:
                self.observed = replace(self.observed, dual=bool(value))
            return
        if name != "fixed_edge" and receiver == 0:
            base = self.observed or self._original
            if base is not None:
                if name == "scope_on":
                    self.observed = replace(base, scope_on=bool(value))
                elif name == "output_on":
                    self.observed = replace(base, output_on=bool(value))
                elif name == "mode":
                    self.observed = replace(base, mode=cast(int, value))
                elif name == "span":
                    self.observed = replace(base, span=cast(int, value))
                else:
                    self.observed = replace(base, edge=cast(int, value))

    def notify_tx(self, tx: bool) -> None:
        previous, self._tx = self._tx, tx
        if previous and not tx:
            task = asyncio.create_task(self.reconcile())
            self._event_tasks.add(task)

            def finished(done: asyncio.Task[None]) -> None:
                self._event_tasks.discard(done)
                if not done.cancelled() and done.exception() is not None:
                    logger.warning(
                        "scope event reconciliation remains pending: %s",
                        done.exception(),
                    )

            task.add_done_callback(finished)

    def menu_guard_active(self) -> bool:
        return (
            self._hidden_at is not None
            and self._clock() - self._hidden_at <= self.hidden_grace_seconds
        )

    async def set_inputs(self, *, ready: bool, tx: bool) -> None:
        async with self._lock:
            self._ready, self._tx = ready, tx
            await self._reconcile()

    async def set_demand(self, demand: ScopeDemand, *, owner: str = "web") -> None:
        async with self._lock:
            previous = self._demands.get(owner, ScopeDemand(0, 0))
            self._demands[owner] = demand
            if demand != previous:
                self.generation += 1
            self._paused = not any(d.active for d in self._demands.values())
            if any(d.subscribers for d in self._demands.values()):
                self._restore_pending = False
                self._release_at = None
                if self._release_task is not None:
                    self._release_task.cancel()
                    self._release_task = None
            elif self._original is not None and not self._restore_pending:
                self._restore_pending = True
                self._release_at = self._clock() + self._grace
                if self._grace:
                    self._release_task = asyncio.create_task(
                        self._release_after_grace()
                    )
            elif self._original is None:
                self._desired.clear()
            await self._reconcile()

    async def _release_after_grace(self) -> None:
        await asyncio.sleep(self._grace)
        await self.reconcile()

    async def reconcile(self) -> None:
        async with self._lock:
            await self._reconcile()

    def on_frame(self, frame: ScopeFrame) -> None:
        self._last_frame_at = self._clock()
        self._out_of_range = frame.out_of_range

    def health(
        self,
        *,
        civ_flowing: bool,
        silence_seconds: float | None = None,
        link_ready: bool = True,
    ) -> ScopeSessionHealth:
        now = self._clock()
        silence = self._silence if silence_seconds is None else silence_seconds
        if (
            not self._ready
            or not link_ready
            or self._tx
            or (self.tx_probe is not None and self.tx_probe())
        ):
            self._enabled_at = now
            return ScopeSessionHealth(None, self._last_frame_at, self._out_of_range)
        if not self.enabled or not any(d.active for d in self._demands.values()):
            return ScopeSessionHealth(None, self._last_frame_at, self._out_of_range)
        fresh = self._last_frame_at is not None and now - self._last_frame_at <= silence
        if fresh:
            self._hidden_at = None
            return ScopeSessionHealth(False, self._last_frame_at, self._out_of_range)
        last = max(t for t in (self._last_frame_at, self._enabled_at) if t is not None)
        if not civ_flowing:
            return ScopeSessionHealth(None, self._last_frame_at, self._out_of_range)
        hidden = now - last > silence
        self._hidden_at = now if hidden else None
        return ScopeSessionHealth(hidden, self._last_frame_at, self._out_of_range)

    async def configure(self, *, receiver: int = 0, **settings: ScopeValue) -> None:
        async with self._lock:
            for name, value in settings.items():
                if name not in (
                    "scope_on",
                    "output_on",
                    "mode",
                    "span",
                    "edge",
                    "fixed_edge",
                    "dual",
                ):
                    raise ValueError(f"unknown scope setting {name}")
                if self._validate is not None:
                    self._validate(cast(ScopeSetting, name), value, receiver)
            previous_desired = dict(self._desired)
            if settings.get("mode") == 1 and "fixed_edge" in settings:
                self._desired.pop((receiver, "span"), None)
            elif settings.get("mode") == 0 and "span" in settings:
                self._desired.pop((receiver, "fixed_edge"), None)
                self._desired.pop((receiver, "edge"), None)
            for name, value in settings.items():
                if name == "fixed_edge" and isinstance(value, ScopeFixedEdge):
                    for key, prior in tuple(self._desired.items()):
                        if (
                            key[1] == "fixed_edge"
                            and isinstance(prior, ScopeFixedEdge)
                            and (prior.range_index, prior.edge)
                            == (value.range_index, value.edge)
                        ):
                            del self._desired[key]
                self._desired[receiver, cast(ScopeSetting, name)] = value
            try:
                await self._reconcile()
            except ValueError:
                self._desired = previous_desired
                raise

    async def _put(
        self, name: ScopeSetting, value: ScopeValue, receiver: int = 0
    ) -> None:
        if not self._ready:
            raise RuntimeError("scope control is deferred until radio readiness")
        if self._tx or (self.tx_probe is not None and self.tx_probe()):
            raise RuntimeError("scope control is deferred during transmit")
        self._uncertain.add((receiver, name))
        await self._write(name, value, receiver)
        self._uncertain.discard((receiver, name))
        if name == "fixed_edge" and isinstance(value, ScopeFixedEdge):
            for key, prior in tuple(self._applied.items()):
                if (
                    key[1] == "fixed_edge"
                    and isinstance(prior, ScopeFixedEdge)
                    and (prior.range_index, prior.edge)
                    == (value.range_index, value.edge)
                ):
                    del self._applied[key]
        self._applied[receiver, name] = value
        if name == "output_on" and value:
            self._enabled_at = self._clock()
            self._last_frame_at = None
            self._out_of_range = False
        self.observe(name, value, receiver)

    async def _reconcile(self) -> None:
        if (
            not self._ready
            or self._tx
            or (self.tx_probe is not None and self.tx_probe())
        ):
            return
        if self._restore_pending:
            if self._release_at is not None and self._clock() < self._release_at:
                return
            if self._original is None:
                self._restore_pending = False
                self._desired.clear()
                return
            failures: list[BaseException] = []
            for (receiver, name), value in tuple(self._previous.items()):
                try:
                    await self._put(name, value, receiver)
                except BaseException as exc:
                    failures.append(exc)
            for value in self._fixed.values():
                try:
                    await self._put("fixed_edge", value)
                except BaseException as exc:
                    failures.append(exc)
            for name in ("scope_on", "output_on"):
                try:
                    value = getattr(self._original, name)
                    if (
                        (0, name) in self._uncertain
                        or self.observed is None
                        or getattr(self.observed, name) != value
                    ):
                        await self._put(name, value)
                except BaseException as exc:
                    failures.append(exc)
            if failures:
                raise failures[0]
            self.observed = self.observed or self._original
            self._original = None
            self._previous.clear()
            self._fixed.clear()
            self._views.clear()
            self._desired.clear()
            self._uncertain.clear()
            self._restore_pending = self._paused = False
            self._connection_epoch = None
            return
        if (
            any(d.subscribers for d in self._demands.values())
            and self._original is None
        ):
            self._applied.clear()
            self._original = await self._read()
            self.observed = self._original
            if self._original.output_on:
                self._enabled_at = self._clock()
            for setting in ("scope_on", "output_on"):
                self._applied[0, setting] = getattr(self._original, setting)
        if any(d.subscribers for d in self._demands.values()):
            assert self._original is not None
            panel = (
                True if not self._paused else (self.observed or self._original).scope_on
            )
            if (0, "scope_on") in self._desired:
                panel = bool(self._desired[0, "scope_on"])
            for name, value in (("scope_on", panel), ("output_on", not self._paused)):
                if (
                    self._applied.get((0, name)) != value
                    or (0, name) in self._uncertain
                ):
                    await self._put(name, value)
        for (receiver, name), value in sorted(
            self._desired.items(),
            key=lambda item: (
                "dual",
                "fixed_edge",
                "edge",
                "mode",
                "span",
                "scope_on",
                "output_on",
            ).index(item[0][1]),
        ):
            if name in ("scope_on", "output_on") and any(
                d.subscribers for d in self._demands.values()
            ):
                continue
            if (
                self._applied.get((receiver, name)) == value
                and (receiver, name) not in self._uncertain
            ):
                continue
            if name == "fixed_edge":
                assert isinstance(value, ScopeFixedEdge)
                slot = (value.range_index, value.edge)
                if self._original is not None and slot not in self._fixed:
                    if self._read_fixed is None:
                        raise RuntimeError("scope fixed edge cannot be captured")
                    captured_fixed = await self._read_fixed(*slot)
                    if (
                        captured_fixed == value
                        and (receiver, name) not in self._uncertain
                    ):
                        self._applied[receiver, name] = value
                        continue
                    self._fixed[slot] = captured_fixed
            elif name == "dual" and self._original is not None:
                if (receiver, name) not in self._previous:
                    self._previous[receiver, name] = (
                        await self._read_dual()
                        if self._read_dual is not None
                        else self._original.dual
                    )
            elif self._original is not None:
                key = (receiver, name)
                if key not in self._previous:
                    if receiver not in self._views:
                        self._views[receiver] = (
                            await self._read_receiver(receiver)
                            if self._read_receiver is not None
                            else self._original
                        )
                        for setting in ("mode", "span", "edge"):
                            self._applied[receiver, cast(ScopeSetting, setting)] = (
                                getattr(self._views[receiver], setting)
                            )
                    captured = self._views[receiver]
                    if self._applied.get(key) == value and key not in self._uncertain:
                        self._applied[key] = value
                        continue
                    self._previous[key] = getattr(captured, name)
            if (
                self._applied.get((receiver, name)) != value
                or (receiver, name) in self._uncertain
            ):
                await self._put(name, value, receiver)

        if not any(d.subscribers for d in self._demands.values()):
            self._desired = {
                key: value
                for key, value in self._desired.items()
                if key in self._uncertain or self._applied.get(key) != value
            }


class ScopeSessionProvider(Protocol):
    def scope_session(self) -> ScopeSession: ...
