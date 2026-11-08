import asyncio
import threading
from pathlib import Path

import pytest

from rigplane.profiles.rig_loader import load_rig
from rigplane.runtime.radio_scope_source import RadioScopeSource, RadioScopeWriter
from rigplane.scope import ScopeFrame
from rigplane.scope.hub import ScopeHub
from rigplane.scope.models import (
    Coverage,
    FollowView,
    GrantedView,
    NativeFrame,
    ScopeSourcePlugin,
    ScopeWireFrame,
    SourceCapabilities,
    Subscription,
    View,
)
from rigplane.testing.timing import await_until


class FakeSource:
    kind = "iq_fft"

    def __init__(self):
        self.callbacks = []
        self.starts = self.stops = 0
        self.fail_start = self.fail_view = False
        self.start_gate = None
        self.stop_gate = None
        self.fail_detach = False
        self.fail_stop = False
        self.set_views = []
        self.granted_view = None
        self.coverage = Coverage(0, 4096)

    def capabilities(self):
        return SourceCapabilities(
            receivers=(None, 0, 1),
            view_modes=("fixed", "center", "free"),
            span_choices_hz=(),
            native_width=4096,
            max_fps=30,
            coverage_rule="native",
        )

    def resolve_view(self, view, receiver=None):
        return (
            View(self.coverage.start_hz, self.coverage.start_hz + view.span_hz)
            if isinstance(view, FollowView)
            else view
        )

    def coverage_for(self, view, receiver=None):
        return self.coverage

    async def set_view(self, view, receiver=None):
        self.set_views.append((view, receiver))
        if self.fail_view:
            raise RuntimeError("view failed")
        return GrantedView(
            self.granted_view or self.resolve_view(view, receiver),
            self.coverage_for(view, receiver),
        )

    async def start(self):
        self.starts += 1
        if self.start_gate:
            await self.start_gate.wait()
        if self.fail_start:
            raise RuntimeError("start failed")

    async def stop(self):
        self.stops += 1
        if self.stop_gate is not None:
            await self.stop_gate.wait()
        if self.fail_stop:
            raise RuntimeError("stop failed")

    def on_frame(self, callback):
        self.callbacks.append(callback)

        def detach():
            self.callbacks.remove(callback)
            if self.fail_detach:
                raise RuntimeError("detach failed")

        return detach

    def emit(self, frame):
        self.coverage = frame.coverage
        for callback in tuple(self.callbacks):
            callback(frame)


class Encoder:
    def __init__(self):
        self.frames = []
        self.threads = []

    def __call__(self, frame: ScopeWireFrame):
        self.frames.append(frame)
        self.threads.append(threading.get_ident())
        return str(len(self.frames)).encode()


def request(width=1024, fps=10, view=View(0, 4096), source="test", receiver=0):
    return Subscription(source, receiver, view, width, fps)


def setup():
    source: ScopeSourcePlugin = FakeSource()
    encoder = Encoder()
    hub = ScopeHub(encoder=encoder)
    hub.register("test", source)
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
    assert source.stops == 1
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
    assert len(encoder.frames) == 60
    assert {frame.width for frame in encoder.frames} == {1024, 2800}
    assert encoder.frames[-1].seq == 30
    await slow.close()
    await fast.close()
    before = len(encoder.frames)
    await virtual_clock.elapse(1)
    assert len(encoder.frames) == before
    await asyncio.gather(*tasks)


@pytest.mark.asyncio
async def test_pending_frames_coalesce_until_client_deadline(virtual_clock):
    hub, source, encoder = setup()
    sub = await hub.subscribe(request(fps=10))
    source.emit(NativeFrame(0, Coverage(0, 4096), bytes(4096)))
    assert await sub.receive() == b"1"
    await virtual_clock.elapse(0.01)
    source.emit(NativeFrame(0, Coverage(0, 4096), bytes([1]) * 4096))
    await virtual_clock.elapse(0.01)
    source.emit(NativeFrame(0, Coverage(0, 4096), bytes([2]) * 4096))
    pending = asyncio.create_task(sub.receive())
    await virtual_clock.elapse(0.07)
    assert not pending.done()
    await virtual_clock.elapse(0.02)
    assert await pending == b"3"
    await sub.close()


@pytest.mark.asyncio
async def test_geometry_health_null_oor_and_receivers(virtual_clock):
    hub, source, encoder = setup()
    a = await hub.subscribe(request(width=4, view=View(0, 4)))
    b = await hub.subscribe(request(width=4, view=View(2, 6)))
    source.emit(NativeFrame(1, Coverage(0, 4), b"abcd"))
    assert not encoder.frames
    source.emit(NativeFrame(0, Coverage(0, 4), b"abcd"))
    by_view = {frame.view_start_hz: frame for frame in encoder.frames}
    assert by_view[0].pixels == b"abcd"
    assert by_view[2].pixels == b"cd\0\0"
    assert by_view[2].coverage_start_hz == 2
    assert by_view[2].coverage_end_hz == 4
    assert encoder.frames[0].seq == encoder.frames[1].seq == 1
    health = hub.health("test", 0)
    pixels_at = health.last_pixels_at
    await virtual_clock.elapse(0.1)
    source.emit(NativeFrame(0, Coverage(0, 4), None, out_of_range=True))
    health = hub.health("test", 0)
    assert health.out_of_range and health.last_frame_at > pixels_at
    assert health.last_pixels_at == pixels_at
    assert encoder.frames[-1].pixels is None
    await a.close()
    await b.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["fail_start", "fail_view", "cancel"])
async def test_failed_or_cancelled_first_subscriber_cleans_up(failure, virtual_clock):
    hub, source, encoder = setup()
    if failure == "cancel":
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
    sub = await hub.subscribe(request(width=4, view=FollowView("vfo", 4), fps=30))
    source.emit(NativeFrame(0, Coverage(0, 4), b"abcd"))
    await virtual_clock.elapse(0.04)
    source.emit(NativeFrame(0, Coverage(10, 14), b"efgh"))
    assert encoder.frames[-1].view_start_hz == 10
    assert await sub.receive() == b"2"
    await sub.close()


@pytest.mark.asyncio
async def test_stop_serializes_new_demand_and_survives_close_cancellation(
    virtual_clock,
):
    hub, source, encoder = setup()
    sub = await hub.subscribe(request())
    stale_callback = source.callbacks[0]
    source.stop_gate = asyncio.Event()
    closing = asyncio.create_task(sub.close())
    await virtual_clock.elapse(0)
    assert source.stops == 1 and not source.callbacks
    opening = asyncio.create_task(hub.subscribe(request()))
    closing.cancel()
    await virtual_clock.elapse(0)
    assert source.starts == 1 and not opening.done() and not closing.done()
    closing.cancel()
    await virtual_clock.elapse(0)
    assert not opening.done() and not closing.done()
    source.stop_gate.set()
    with pytest.raises(asyncio.CancelledError):
        await closing
    fresh = await opening
    stale_callback(NativeFrame(0, Coverage(0, 4), b"abcd"))
    assert not encoder.frames
    source.emit(NativeFrame(0, Coverage(0, 4096), bytes(4096)))
    assert encoder.frames[-1].seq == 1
    await fresh.close()
    assert source.starts == source.stops == 2


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["fail_detach", "fail_stop"])
async def test_cleanup_failure_does_not_retain_demand(failure, virtual_clock):
    hub, source, encoder = setup()
    sub = await hub.subscribe(request())
    setattr(source, failure, True)
    with pytest.raises(RuntimeError):
        await sub.close()
    assert source.stops == 1 and not source.callbacks
    with pytest.raises(StopAsyncIteration):
        await sub.receive()
    setattr(source, failure, False)
    fresh = await hub.subscribe(request())
    await fresh.close()
    assert source.starts == 2
    assert source.stops == (3 if failure == "fail_stop" else 2)


@pytest.mark.asyncio
async def test_failed_additional_view_preserves_existing_subscriber(virtual_clock):
    hub, source, encoder = setup()
    sub = await hub.subscribe(request(width=4, view=View(0, 4)))
    source.fail_view = True
    with pytest.raises(RuntimeError):
        await hub.subscribe(request(view=View(10, 14)))
    assert source.starts == 1 and source.stops == 0 and len(source.callbacks) == 1
    source.emit(NativeFrame(0, Coverage(0, 4), b"abcd"))
    assert await sub.receive()
    await sub.close()


@pytest.mark.asyncio
async def test_distinct_views_keep_settled_geometry_when_source_moves(virtual_clock):
    hub, source, encoder = setup()
    source.granted_view = View(0, 4)
    first = await hub.subscribe(request(width=4, view=View(0, 5)))
    assert first.granted.coverage == Coverage(0, 4)
    source.granted_view = View(10, 14)
    second = await hub.subscribe(request(width=4, view=View(10, 15)))
    source.emit(NativeFrame(0, Coverage(10, 14), b"abcd"))
    by_view = {frame.view_start_hz: frame for frame in encoder.frames}
    assert by_view[0].view_end_hz == 4 and by_view[0].pixels is None
    assert by_view[0].coverage_start_hz == by_view[0].coverage_end_hz == 0
    assert by_view[10].view_end_hz == 14 and by_view[10].pixels == b"abcd"
    assert first.granted.coverage is None
    assert second.granted.coverage == Coverage(10, 14)
    await first.close()
    await second.close()


@pytest.mark.asyncio
async def test_registry_receiver_sequences_and_zero_pixels_health(virtual_clock):
    hub, source, encoder = setup()
    other = FakeSource()
    hub.register("other", other)
    a = await hub.subscribe(request(width=4, view=View(0, 4)))
    b = await hub.subscribe(request(width=4, view=View(0, 4), receiver=1))
    c = await hub.subscribe(request(width=4, view=View(0, 4), source="other"))
    for receiver in (0, 1, 0):
        source.emit(NativeFrame(receiver, Coverage(0, 4), bytes(4)))
    other.emit(NativeFrame(0, Coverage(0, 4), bytes(4)))
    assert [(f.source_id, f.receiver, f.seq) for f in encoder.frames] == [
        ("test", 0, 1),
        ("test", 1, 1),
        ("test", 0, 2),
        ("other", 0, 1),
    ]
    assert hub.health("test", 0).last_pixels_at is not None
    assert hub.capabilities("other") == other.capabilities()
    for sub in (a, b, c):
        await sub.close()


@pytest.mark.asyncio
async def test_worker_thread_frame_is_delivered_on_hub_loop(virtual_clock):
    hub, source, encoder = setup()
    sub = await hub.subscribe(request(width=4, view=View(0, 4)))
    callback = source.callbacks[0]
    worker_done = threading.Event()

    def produce():
        callback(NativeFrame(0, Coverage(0, 4), b"abcd"))
        worker_done.set()

    worker = threading.Thread(target=produce)
    worker.start()
    await await_until(worker_done.is_set, timeout=1)
    worker.join()
    await virtual_clock.elapse(0)
    assert await sub.receive() == b"1"
    assert encoder.frames[0].pixels == b"abcd"
    assert encoder.threads == [threading.get_ident()]
    await sub.close()


class CleanupProbe:
    def __init__(self, failures):
        self.failures = failures
        self.starts = self.stops = 0
        self.active = False

    def start(self):
        self.starts += 1
        self.active = True

    def stop(self):
        self.stops += 1
        if self.failures:
            self.failures -= 1
            raise RuntimeError("resource release failed")
        self.active = False


def real_flaky_source(probe):
    view = View(10_000_300, 10_002_700)

    class FlakyWriter(RadioScopeWriter):
        def tuned_frequency(self, receiver):
            return 10_000_000

        async def set_center(self, center_hz, span_hz, receiver):
            pass

        async def set_output(self, enabled):
            probe.start() if enabled else probe.stop()

    source = RadioScopeSource(
        load_rig(Path("rigs/ic7300.toml")).to_profile(), FlakyWriter()
    )

    def emit():
        source.feed_frame(
            ScopeFrame(0, 1, 10_000_300, 10_002_700, bytes([160]) * 8, False)
        )

    return source, Subscription("real", 0, view, 8, 20), emit


@pytest.mark.parametrize("retry", ["close", "subscribe"])
@pytest.mark.timeout(10)
async def test_real_failed_final_stop_is_retried_before_fresh_demand(
    retry, virtual_clock
):
    probe = CleanupProbe(1 if retry == "close" else 2)
    source, requested, emit = real_flaky_source(probe)
    encoder = Encoder()
    hub = ScopeHub(encoder=encoder)
    hub.register("real", source)
    old = await hub.subscribe(requested)
    emit()
    assert len(encoder.frames) == 1
    assert await old.receive() == b"1"
    with pytest.raises(RuntimeError, match="resource release failed"):
        await old.close()
    assert probe.active and probe.starts == probe.stops == 1
    with pytest.raises(StopAsyncIteration):
        await old.receive()
    if retry == "close":
        await old.close()
        assert not probe.active and probe.stops == 2
    else:
        with pytest.raises(RuntimeError, match="resource release failed"):
            await hub.subscribe(requested)
        assert probe.active and probe.starts == 1 and probe.stops == 2
    fresh = await hub.subscribe(requested)
    assert probe.active and probe.starts == 2
    await old.close()
    assert probe.active
    emit()
    assert len(encoder.frames) == 2
    assert await fresh.receive() == b"2"
    assert encoder.frames[-1].pixels is not None
    await fresh.close()
    assert not probe.active and probe.stops == (3 if retry == "close" else 4)


async def test_failed_start_cleanup_is_retried_before_starting_again(virtual_clock):
    hub, source, encoder = setup()
    source.fail_start = source.fail_stop = True
    with pytest.raises(RuntimeError, match="stop failed"):
        await hub.subscribe(request())
    assert source.starts == source.stops == 1 and not source.callbacks
    source.fail_start = False
    with pytest.raises(RuntimeError, match="stop failed"):
        await hub.subscribe(request())
    assert source.starts == 1 and source.stops == 2 and not source.callbacks
    source.fail_stop = False
    fresh = await hub.subscribe(request())
    assert source.starts == 2 and source.stops == 3
    await fresh.close()


async def test_stop_retry_serializes_with_new_demand_and_repeated_cancellation(
    virtual_clock,
):
    hub, source, encoder = setup()
    old = await hub.subscribe(request())
    source.fail_stop = True
    with pytest.raises(RuntimeError, match="stop failed"):
        await old.close()
    source.fail_stop = False
    source.stop_gate = asyncio.Event()
    retrying = asyncio.create_task(old.close())
    await virtual_clock.elapse(0)
    assert source.stops == 2
    opening = asyncio.create_task(hub.subscribe(request()))
    for _ in range(2):
        retrying.cancel()
        await virtual_clock.elapse(0)
        assert not retrying.done() and not opening.done() and source.starts == 1
    source.stop_gate.set()
    with pytest.raises(asyncio.CancelledError):
        await retrying
    fresh = await opening
    assert source.starts == source.stops == 2
    await fresh.close()


class NativeWriter:
    """Minimal RadioScopeWriter for the width-cap tests."""

    def tuned_frequency(self, receiver):
        return 10_000_000

    async def set_output(self, enabled):
        pass

    async def set_center(self, center_hz, span_hz, receiver):
        pass


@pytest.mark.asyncio
async def test_width_requests_above_native_are_granted_at_native_resolution(
    virtual_clock,
):
    profile = load_rig(Path("rigs/ic7300.toml")).to_profile()
    native_source = RadioScopeSource(profile, NativeWriter())
    native_encoder = Encoder()
    native_hub = ScopeHub(encoder=native_encoder)
    native_hub.register("radio689", native_source)
    view = View(10_000_300, 10_002_700)
    wide = await native_hub.subscribe(Subscription("radio689", 0, view, 2800, 20))
    assert wide.granted.width == 2800
    native_source.feed_frame(
        ScopeFrame(0, 0, 10_000_300, 10_002_700, bytes([160]) * 689, False)
    )
    assert wide.granted.width == 689
    assert native_encoder.frames[-1].width == 689
    assert native_encoder.frames[-1].pixels == bytes([160]) * 689
    await wide.close()

    hub, source, encoder = setup()
    exact = await hub.subscribe(request(1024))
    assert exact.granted.width == 1024
    await exact.close()


async def test_queued_radio_frames_keep_their_observed_width_snapshot():
    import threading

    profile = load_rig(Path("rigs/ic7610.toml")).to_profile()
    source = RadioScopeSource(profile, NativeWriter())
    encoder = Encoder()
    hub = ScopeHub(encoder=encoder)
    hub.register("radio", source)
    sub = await hub.subscribe(
        Subscription("radio", 0, View(10_000_300, 10_002_700), 2800, 20)
    )

    def produce():
        for width in (475, 689):
            source.feed_frame(
                ScopeFrame(0, 1, 10_000_300, 10_002_700, bytes([160]) * width, False)
            )

    producer = threading.Thread(target=produce)
    producer.start()
    producer.join()
    await await_until(lambda: len(encoder.frames) == 2)
    assert [frame.width for frame in encoder.frames] == [475, 689]
    assert [len(frame.pixels) for frame in encoder.frames] == [475, 689]
    assert source.capabilities().native_width_for(0) == 689
    await sub.close()


async def test_initial_paused_demand_does_not_start_or_set_view():
    hub, source, _ = setup()
    paused = await hub.subscribe(request(fps=0))
    assert source.starts == source.stops == 0
    assert source.set_views == [] and source.callbacks == []
    await paused.close()
    assert source.starts == source.stops == 0


async def test_positive_demand_transitions_retain_paused_clients():
    hub, source, _ = setup()
    active = await hub.subscribe(request())
    paused = await hub.subscribe(request(fps=0))
    await active.close()
    assert source.stops == 1 and source.callbacks == []
    assert not paused._closed
    resumed = await hub.subscribe(request(fps=20))
    assert source.starts == 2 and len(source.callbacks) == 1
    sibling = await hub.subscribe(request(fps=10))
    await resumed.close()
    assert source.stops == 1
    await sibling.close()
    assert source.stops == 2 and not paused._closed
    await paused.close()
    assert source.stops == 2


@pytest.mark.parametrize("model", ["ic7610", "ic9700"])
async def test_observed_widths_are_receiver_scoped_for_existing_and_new_clients(model):
    profile = load_rig(Path("rigs") / f"{model}.toml").to_profile()
    source = RadioScopeSource(profile, NativeWriter())
    encoder = Encoder()
    hub = ScopeHub(encoder=encoder)
    hub.register("radio", source)
    view = View(10_000_300, 10_002_700)
    first = await hub.subscribe(Subscription("radio", 0, view, 2800, 20))
    other = await hub.subscribe(Subscription("radio", 1, view, 2800, 20))
    new = []
    try:
        source.feed_frame(
            ScopeFrame(0, 1, view.start_hz, view.end_hz, bytes([160]) * 475, False)
        )
        source.feed_frame(
            ScopeFrame(1, 1, view.start_hz, view.end_hz, bytes([160]) * 689, False)
        )
        for oor, pixels in ((False, b""), (True, b""), (True, b"\x01")):
            source.feed_frame(ScopeFrame(0, 1, view.start_hz, view.end_hz, pixels, oor))
            assert first.granted.width == 475
            assert other.granted.width == 689
        for receiver, width in ((0, 475), (1, 689)):
            new.append(
                await hub.subscribe(Subscription("radio", receiver, view, 2800, 20))
            )
            assert new[-1].granted.width == width
    finally:
        for sub in [first, other, *new]:
            await sub.close()


async def test_failed_stop_retries_with_retained_paused_subscription():
    hub, source, _ = setup()
    active = await hub.subscribe(request())
    paused = await hub.subscribe(request(fps=0))
    source.fail_stop = True
    with pytest.raises(RuntimeError, match="stop failed"):
        await active.close()
    source.fail_stop = False
    await active.close()
    assert source.stops == 2
    await paused.close()
    assert source.stops == 2
