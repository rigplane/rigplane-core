"""Demand-counted scope subscriptions with bounded latest-frame delivery."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field, replace

from .models import (
    Coverage,
    FollowView,
    GrantedSubscription,
    GrantedView,
    NativeFrame,
    ScopeSourcePlugin,
    ScopeDemand,
    ScopeWireFrame,
    SourceCapabilities,
    Subscription,
    View,
)
from .resample import max_hold


@dataclass(frozen=True)
class ScopeHealth:
    last_frame_at: float | None = None
    last_pixels_at: float | None = None
    out_of_range: bool = False
    coverage: Coverage | None = None


@dataclass
class _Source:
    plugin: ScopeSourcePlugin
    on_demand: Callable[[ScopeDemand], Awaitable[None]] | None = None
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    clients: set[HubSubscription] = field(default_factory=set)
    unsubscribe: Callable[[], None] | None = None
    stop_pending: bool = False
    generation: int = 0
    sequences: dict[int | None, int] = field(default_factory=dict)
    health: dict[int | None, ScopeHealth] = field(default_factory=dict)


async def _finish(operation: Awaitable[None]) -> None:
    task = asyncio.ensure_future(operation)
    cancelled = False
    while not task.done():
        try:
            await asyncio.shield(task)
        except asyncio.CancelledError:
            cancelled = True
    task.result()
    if cancelled:
        raise asyncio.CancelledError


def _intersection(view: View, coverage: Coverage | None) -> Coverage | None:
    if coverage is None:
        return None
    start = max(view.start_hz, coverage.start_hz)
    end = min(view.end_hz, coverage.end_hz)
    return Coverage(start, end) if start < end else None


class HubSubscription:
    def __init__(
        self,
        hub: ScopeHub,
        source: _Source,
        request: Subscription,
        granted: GrantedSubscription,
    ) -> None:
        self.granted = granted
        self._hub = hub
        self._source = source
        self._request = request
        self._ready = asyncio.Event()
        self._latest: tuple[GrantedSubscription, bytes] | None = None
        self._pending: tuple[GrantedSubscription, bytes] | None = None
        self._timer: asyncio.TimerHandle | None = None
        self._last_delivery: float | None = None
        self._closed = False

    async def receive(self) -> bytes:
        return (await self.receive_delivery())[1]

    async def receive_delivery(self) -> tuple[GrantedSubscription, bytes]:
        while not self._closed:
            await self._ready.wait()
            if self._latest is not None:
                data, self._latest = self._latest, None
                self._ready.clear()
                return data
        raise StopAsyncIteration

    async def close(self) -> None:
        await _finish(self._hub._unsubscribe(self))

    def _offer(self, data: bytes) -> None:
        if self._closed or self.granted.max_fps == 0:
            return
        self._pending = self.granted, data
        now = self._hub._loop.time()
        delay = (
            0
            if self._last_delivery is None
            else max(0, self._last_delivery + 1 / self.granted.max_fps - now)
        )
        if delay == 0:
            self._deliver()
        elif self._timer is None:
            self._timer = self._hub._loop.call_later(delay, self._deliver)

    def _deliver(self) -> None:
        if self._timer is not None:
            self._timer.cancel()
            self._timer = None
        if self._closed or self._pending is None:
            return
        self._latest, self._pending = self._pending, None
        self._last_delivery = self._hub._loop.time()
        self._ready.set()

    def _discard(self) -> None:
        self._closed = True
        if self._timer is not None:
            self._timer.cancel()
            self._timer = None
        self._latest = self._pending = None
        self._ready.set()


class ScopeHub:
    def __init__(
        self,
        *,
        encoder: Callable[[ScopeWireFrame], bytes],
        loop: asyncio.AbstractEventLoop | None = None,
    ) -> None:
        self._encoder = encoder
        self._loop = loop if loop is not None else asyncio.get_running_loop()
        self._sources: dict[str, _Source] = {}

    def register(
        self,
        source_id: str,
        plugin: ScopeSourcePlugin,
        *,
        on_demand: Callable[[ScopeDemand], Awaitable[None]] | None = None,
    ) -> None:
        if source_id in self._sources:
            raise ValueError(f"duplicate scope source: {source_id}")
        self._sources[source_id] = _Source(plugin, on_demand)

    def capabilities(self, source_id: str) -> SourceCapabilities:
        return self._sources[source_id].plugin.capabilities()

    def health(self, source_id: str, receiver: int | None = None) -> ScopeHealth:
        return self._sources[source_id].health.get(receiver, ScopeHealth())

    async def subscribe(self, request: Subscription) -> HubSubscription:
        source = self._sources[request.source_id]
        async with source.lock:
            caps = source.plugin.capabilities()
            if request.receiver not in caps.receivers:
                raise ValueError("receiver is not supported by this source")
            positive = request.max_fps > 0
            first = positive and not any(
                client.granted.max_fps > 0 for client in source.clients
            )
            if first and source.stop_pending:
                await _finish(self._stop(source))
            try:
                if first:
                    source.generation += 1
                    generation = source.generation
                    source.unsubscribe = source.plugin.on_frame(
                        lambda frame: self._receive_frame(
                            request.source_id, source, generation, frame
                        )
                    )
                    await source.plugin.start()
                grant = (
                    await source.plugin.set_view(request.view, request.receiver)
                    if positive
                    else GrantedView(
                        source.plugin.resolve_view(request.view, request.receiver),
                        source.plugin.coverage_for(request.view, request.receiver),
                    )
                )
                caps = source.plugin.capabilities()
                native_width = caps.native_width_for(request.receiver)
                subscription = HubSubscription(
                    self,
                    source,
                    request,
                    GrantedSubscription(
                        request.source_id,
                        request.receiver,
                        grant.view,
                        _intersection(grant.view, grant.coverage),
                        # No upsampling: a request wider than the source's
                        # native bins is granted at native resolution, and
                        # the granted message reports the actual width.
                        min(request.width, native_width)
                        if native_width is not None
                        else request.width,
                        min(request.max_fps, caps.max_fps),
                    ),
                )
                source.clients.add(subscription)
                try:
                    await self._notify_demand(source)
                except BaseException:
                    subscription._discard()
                    source.clients.remove(subscription)
                    await _finish(self._notify_demand(source))
                    raise
                return subscription
            except BaseException:
                if first:
                    try:
                        self._detach(source)
                    finally:
                        await _finish(self._stop(source))
                raise

    @staticmethod
    def _detach(source: _Source) -> None:
        source.generation += 1
        if source.unsubscribe is not None:
            unsubscribe, source.unsubscribe = source.unsubscribe, None
            unsubscribe()

    async def _unsubscribe(self, client: HubSubscription) -> None:
        source = client._source
        async with source.lock:
            if client._closed:
                if (
                    not any(c.granted.max_fps > 0 for c in source.clients)
                    and source.stop_pending
                ):
                    await _finish(self._stop(source))
                return
            client._discard()
            source.clients.remove(client)
            try:
                await self._notify_demand(source)
            finally:
                if not any(c.granted.max_fps > 0 for c in source.clients) and (
                    source.unsubscribe is not None or source.stop_pending
                ):
                    try:
                        self._detach(source)
                    finally:
                        await _finish(self._stop(source))

    @staticmethod
    async def _notify_demand(source: _Source) -> None:
        if source.on_demand is not None:
            await source.on_demand(
                ScopeDemand(
                    len(source.clients),
                    sum(c.granted.max_fps > 0 for c in source.clients),
                )
            )

    @staticmethod
    async def _stop(source: _Source) -> None:
        source.stop_pending = True
        await source.plugin.stop(release=not source.clients)
        source.stop_pending = False

    def _receive_frame(
        self, source_id: str, source: _Source, generation: int, frame: NativeFrame
    ) -> None:
        try:
            running_loop = asyncio.get_running_loop()
        except RuntimeError:
            running_loop = None
        if running_loop is self._loop:
            self._on_frame(source_id, source, generation, frame)
        else:
            self._loop.call_soon_threadsafe(
                self._on_frame, source_id, source, generation, frame
            )

    def _on_frame(
        self, source_id: str, source: _Source, generation: int, frame: NativeFrame
    ) -> None:
        if generation != source.generation or not source.clients:
            return
        now = self._loop.time()
        previous = source.health.get(frame.receiver, ScopeHealth())
        source.health[frame.receiver] = ScopeHealth(
            now,
            now if frame.pixels else previous.last_pixels_at,
            frame.out_of_range,
            frame.coverage,
        )
        seq = source.sequences.get(frame.receiver, 0) + 1
        source.sequences[frame.receiver] = seq
        encoded: dict[tuple[int, View, Coverage | None], bytes] = {}
        native_width = (
            frame.native_width
            if frame.native_width is not None
            else source.plugin.capabilities().native_width_for(frame.receiver)
        )
        for client in tuple(source.clients):
            if client.granted.receiver != frame.receiver:
                continue
            client.granted = replace(
                client.granted,
                width=min(client._request.width, native_width)
                if native_width is not None
                else client._request.width,
            )
            if client.granted.max_fps == 0:
                continue
            grant = client.granted
            if isinstance(client._request.view, FollowView):
                # MOR-4385: what a follow grant tracks is the source's own
                # follow semantics (ScopeSourcePlugin.resolve_follow) — the
                # radio adopts the streamed native coverage (an external
                # span, centre or VFO change), an FFT source keeps the
                # requested crop. Following never writes back. Frames
                # without real data keep the resolved view.
                if frame.out_of_range:
                    view = source.plugin.resolve_view(
                        client._request.view, frame.receiver
                    )
                else:
                    view = source.plugin.resolve_follow(
                        client._request.view, frame.coverage, frame.receiver
                    )
            else:
                view = grant.view
            coverage = _intersection(view, frame.coverage)
            client.granted = replace(grant, view=view, coverage=coverage)
            key = grant.width, view, coverage
            if key not in encoded:
                pixels = (
                    max_hold(frame.pixels, frame.coverage, view, grant.width)
                    if frame.pixels is not None and not frame.out_of_range
                    else None
                )
                encoded[key] = self._encoder(
                    ScopeWireFrame(
                        source.plugin.kind,
                        source_id,
                        frame.receiver,
                        view.start_hz,
                        view.end_hz,
                        coverage.start_hz if coverage else view.start_hz,
                        coverage.end_hz if coverage else view.start_hz,
                        grant.width,
                        pixels,
                        frame.out_of_range,
                        frame.epoch,
                        seq % (2**32),
                        None,
                    )
                )
            client._offer(encoded[key])
