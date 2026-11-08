import asyncio

import pytest

from rigplane.scope.hub import ScopeHub
from rigplane.scope.models import (
    Coverage, FollowView, GrantedView, NativeFrame, ScopeSourcePlugin,
    ScopeWireFrame, SourceCapabilities, Subscription, View,
)


class FakeSource:
    kind = 'iq_fft'

    def __init__(self):
        self.callbacks = []
        self.starts = self.stops = 0
        self.fail_start = self.fail_view = False
        self.start_gate = None
        self.coverage = Coverage(0, 4096)

    def capabilities(self):
        return SourceCapabilities(
            receivers=(None, 0, 1), view_modes=('fixed', 'center', 'free'),
            span_choices_hz=(), edge_limits_hz=None, native_width=4096,
            max_fps=30, coverage_rule='native',
        )

    def resolve_view(self, view, receiver=None):
        return View(self.coverage.start_hz, self.coverage.start_hz + view.span_hz) if isinstance(view, FollowView) else view

    def coverage_for(self, view, receiver=None):
        return self.coverage

    async def set_view(self, view, receiver=None):
        if self.fail_view:
            raise RuntimeError('view failed')
        return GrantedView(self.resolve_view(view, receiver), self.coverage_for(view, receiver))

    async def start(self):
        self.starts += 1
        if self.start_gate:
            await self.start_gate.wait()
        if self.fail_start:
            raise RuntimeError('start failed')

    async def stop(self):
        self.stops += 1

    def on_frame(self, callback):
        self.callbacks.append(callback)
        return lambda: self.callbacks.remove(callback)

    def emit(self, frame):
        self.coverage = frame.coverage
        for callback in tuple(self.callbacks):
            callback(frame)


class Encoder:
    def __init__(self):
        self.frames = []

    def __call__(self, frame: ScopeWireFrame):
        self.frames.append(frame)
        return str(len(self.frames)).encode()


def request(width=1024, fps=10, view=View(0, 4096), source='test', receiver=0):
    return Subscription(source, receiver, view, width, fps)


def setup():
    source: ScopeSourcePlugin = FakeSource()
    encoder = Encoder()
    hub = ScopeHub(encoder=encoder)
    hub.register('test', source)
    return hub, source, encoder


@pytest.mark.asyncio
async def test_demand_paused_and_shared_bytes(virtual_clock):
    hub, source, encoder = setup()
    a = await hub.subscribe(request())
    b = await hub.subscribe(request())
    desktop = await hub.subscribe(request(2800, 20))
    paused = await hub.subscribe(request(fps=0))
    assert source.starts == 1
    source.emit(NativeFrame(0, Coverage(0, 4096), bytes([160]) * 4096))
    assert len(encoder.frames) == 2
    assert await a.receive() is await b.receive()
    await desktop.receive()
    pending = asyncio.create_task(paused.receive())
    await virtual_clock.elapse(0)
    assert not pending.done()
    for subscription in (a, b, desktop):
        await subscription.close()
    assert source.stops == 0
    await paused.close()
    with pytest.raises(StopAsyncIteration):
        await pending
    assert source.stops == 1 and source.callbacks == []


@pytest.mark.asyncio
async def test_latest_wins_rate_limits_and_no_timers_after_close(virtual_clock):
    hub, source, encoder = setup()
    slow = await hub.subscribe(request(fps=10))
    fast = await hub.subscribe(request(2800, 20))
    counts = [0, 0]
    async def drain(index, subscription):
        try:
            while True:
                await subscription.receive()
                counts[index] += 1
        except StopAsyncIteration:
            pass
    tasks = [asyncio.create_task(drain(i, sub)) for i, sub in enumerate((slow, fast))]
    for i in range(30):
        source.emit(NativeFrame(0, Coverage(0, 4096), bytes([i]) * 4096))
        await virtual_clock.elapse(1 / 30)
    assert 10 <= counts[0] <= 11 and 20 <= counts[1] <= 21
    assert encoder.frames[-1].seq == 30
    await slow.close()
    await fast.close()
    before = len(encoder.frames)
    await virtual_clock.elapse(1)
    assert len(encoder.frames) == before
    await asyncio.gather(*tasks)


@pytest.mark.asyncio
async def test_geometry_health_null_oor_and_receivers(virtual_clock):
    hub, source, encoder = setup()
    a = await hub.subscribe(request(width=4, view=View(0, 4)))
    b = await hub.subscribe(request(width=4, view=View(2, 6)))
    source.emit(NativeFrame(1, Coverage(0, 4), b'abcd'))
    assert not encoder.frames
    source.emit(NativeFrame(0, Coverage(0, 4), b'abcd'))
    assert encoder.frames[0].pixels == b'abcd'
    assert encoder.frames[1].pixels == b'cd\0\0'
    assert encoder.frames[1].coverage_start_hz == 2
    assert encoder.frames[1].coverage_end_hz == 4
    assert encoder.frames[0].seq == encoder.frames[1].seq == 1
    health = hub.health('test', 0)
    pixels_at = health.last_pixels_at
    await virtual_clock.elapse(.1)
    source.emit(NativeFrame(0, Coverage(0, 4), None, out_of_range=True))
    health = hub.health('test', 0)
    assert health.out_of_range and health.last_frame_at > pixels_at
    assert health.last_pixels_at == pixels_at
    assert encoder.frames[-1].pixels is None
    await a.close()
    await b.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('failure', ['fail_start', 'fail_view', 'cancel'])
async def test_failed_or_cancelled_first_subscriber_cleans_up(failure, virtual_clock):
    hub, source, encoder = setup()
    if failure == 'cancel':
        source.start_gate = asyncio.Event()
        task = asyncio.create_task(hub.subscribe(request()))
        await virtual_clock.elapse(0)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
    else:
        setattr(source, failure, True)
        with pytest.raises(RuntimeError):
            await hub.subscribe(request())
    assert source.stops == 1 and source.callbacks == []
    source.start_gate = None
    source.fail_start = source.fail_view = False
    sub = await hub.subscribe(request())
    await sub.close()
    assert source.starts == 2 and source.stops == 2


@pytest.mark.asyncio
async def test_follow_view_refreshes_and_slow_consumer_keeps_only_latest(virtual_clock):
    hub, source, encoder = setup()
    sub = await hub.subscribe(request(width=4, view=FollowView('vfo', 4), fps=30))
    source.emit(NativeFrame(0, Coverage(0, 4), b'abcd'))
    await virtual_clock.elapse(.04)
    source.emit(NativeFrame(0, Coverage(10, 14), b'efgh'))
    assert encoder.frames[-1].view_start_hz == 10
    assert await sub.receive() == b'2'
    await sub.close()
