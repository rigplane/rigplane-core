"""Coverage tests for _civ_rx.py — CI-V receive pump, watchdog, state dispatch.

Covers missing lines:
- 64: cleanup_stale_civ_waiters debug log
- 89-90: _stop_civ_rx_pump CancelledError handling
- 112: _start_civ_data_watchdog early return
- 153: watchdog _last_civ_data_received is None → continue
- 157-175: watchdog Phase 1 recovery (OpenClose)
- 229-232: watchdog data resumed (recovering reset)
- 244: _ensure_civ_runtime raises ConnectionError
- 276-279: drain queue in civ_rx_loop
- 287: short packet skip
- 292-293: ValueError from parse_civ_frame
- 296-297: Exception from _route_civ_frame
- 307: from_addr mismatch → return
- 311: to_addr mismatch → return
- 401, 414-421: _update_state_cache_from_frame meter/level paths
- 449-450: sub 0x65 (IP+) handling
- 456-457: exception in cache update
- 470-622: _update_radio_state_from_frame all paths
- 628-632: _notify_change with callback
- 640-643: _publish_scope_frame queue full
- 655-658: _publish_civ_event queue full
- 726: _start_civ_worker commander already exists
- 759-768: _drain_ack_sinks_before_blocking
- 823: pre-send timeout check
- 833: ACK sink token error path
- 851: post-register remaining <= 0 timeout
"""

from __future__ import annotations

import asyncio
import time
from typing import Any
from unittest.mock import AsyncMock, MagicMock, PropertyMock, patch

import pytest
from test_radio import MockTransport, _wrap_civ_in_udp

from rigplane import IC_7610_ADDR
from rigplane.runtime._civ_rx import CIV_HEADER_SIZE
from rigplane.commands import CONTROLLER_ADDR, build_civ_frame
from rigplane.commands.tone import _encode_tone_freq
from rigplane.core.acquisition_scheduler import (
    AcquisitionScheduler,
    MeterObservationCoalescer,
)
from rigplane.core.state_acquisition_policy import (
    AcquisitionPolicy,
    FieldCapability,
    MeterCoalescingPolicy,
    RadioAcquisitionProfile,
)
from rigplane.core.state_diagnostics import StateDiagnosticsRecorder
from rigplane.core.state_pipeline_contracts import FieldPath
from rigplane.exceptions import ConnectionError
from rigplane.radio import IcomRadio
from rigplane.radio_state import RadioState
from rigplane.scope import ScopeFrame
from rigplane.core.state_store import FreshnessState, StateSnapshot
from rigplane.types import CivFrame, Mode, bcd_encode
from rigplane.web.radio_poller import CommandQueue, RadioPoller

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture  # type: ignore[untyped-decorator]
def transport() -> MockTransport:
    return MockTransport()


@pytest.fixture  # type: ignore[untyped-decorator]
def radio(transport: MockTransport) -> IcomRadio:
    r = IcomRadio("192.168.1.100")
    r._civ_transport = transport
    r._ctrl_transport = transport
    r._connected = True
    return r


def _make_frame(
    cmd: int,
    sub: int | None = None,
    data: bytes = b"",
    from_addr: int = IC_7610_ADDR,
    to_addr: int = CONTROLLER_ADDR,
    receiver: int | None = None,
) -> CivFrame:
    """Create a CivFrame directly for testing."""
    return CivFrame(
        to_addr=to_addr,
        from_addr=from_addr,
        command=cmd,
        sub=sub,
        data=data,
        receiver=receiver,
    )


def _bcd2(value: int) -> bytes:
    """Encode a 0-255 value as 2-byte BCD (like IC-7610 meter/level format)."""
    d = f"{value:04d}"
    b0 = (int(d[0]) << 4) | int(d[1])
    b1 = (int(d[2]) << 4) | int(d[3])
    return bytes([b0, b1])


def _acquisition_profile(
    *paths: FieldPath,
    policy: AcquisitionPolicy | None = None,
) -> RadioAcquisitionProfile:
    acquisition_policy = policy or AcquisitionPolicy(
        cadence_seconds=1.0,
        freshness_ttl_seconds=4.0,
    )
    return RadioAcquisitionProfile(
        provider="icom_civ",
        capabilities=tuple(
            FieldCapability(
                path=path, polling=True, stream_like=path.family.value == "meters"
            )
            for path in paths
        ),
        default_policy=acquisition_policy,
    )


# ---------------------------------------------------------------------------
# _cleanup_stale_civ_waiters (line 64)
# ---------------------------------------------------------------------------


def test_cleanup_stale_civ_waiters_logs_cleaned_count(radio: IcomRadio) -> None:
    """When cleanup returns > 0, debug log is emitted (line 64)."""
    radio._civ_last_waiter_gc_monotonic = 0.0
    radio._civ_waiter_ttl_gc_interval = 0.0
    radio._civ_request_tracker.cleanup_stale = MagicMock(return_value=3)
    radio._civ_runtime._cleanup_stale_civ_waiters()
    radio._civ_request_tracker.cleanup_stale.assert_called_once()


def test_cleanup_stale_civ_waiters_no_log_when_zero(radio: IcomRadio) -> None:
    """When cleanup returns 0, no debug log (branch not taken)."""
    radio._civ_last_waiter_gc_monotonic = 0.0
    radio._civ_waiter_ttl_gc_interval = 0.0
    radio._civ_request_tracker.cleanup_stale = MagicMock(return_value=0)
    radio._civ_runtime._cleanup_stale_civ_waiters()  # should not raise


# ---------------------------------------------------------------------------
# _stop_civ_rx_pump (lines 89-90)
# ---------------------------------------------------------------------------


async def test_stop_civ_rx_pump_handles_cancellation(radio: IcomRadio) -> None:
    """_stop_civ_rx_pump cancels the rx task and handles CancelledError (lines 89-90)."""

    async def _long_task() -> None:
        try:
            await asyncio.sleep(100.0)
        except asyncio.CancelledError:
            raise

    task = asyncio.create_task(_long_task())
    radio._civ_rx_task = task
    await radio._civ_runtime.stop_pump()
    assert radio._civ_rx_task is None
    assert task.cancelled()


async def test_stop_civ_rx_pump_when_task_is_none(radio: IcomRadio) -> None:
    """_stop_civ_rx_pump is a no-op when there is no task."""
    radio._civ_rx_task = None
    await radio._civ_runtime.stop_pump()  # should not raise
    assert radio._civ_rx_task is None


# ---------------------------------------------------------------------------
# _start_civ_data_watchdog (line 112)
# ---------------------------------------------------------------------------


def test_start_civ_data_watchdog_returns_early_when_already_running(
    radio: IcomRadio,
) -> None:
    """Returns early without creating a task when watchdog is already running (line 112)."""
    mock_task = MagicMock()
    mock_task.done.return_value = False
    radio._civ_data_watchdog_task = mock_task

    with patch("asyncio.create_task") as mock_create:
        radio._civ_runtime.start_data_watchdog()
        mock_create.assert_not_called()


def test_start_civ_data_watchdog_creates_task_when_done(radio: IcomRadio) -> None:
    """Creates a new task when previous watchdog task is done."""
    mock_task = MagicMock()
    mock_task.done.return_value = True
    radio._civ_data_watchdog_task = mock_task

    fake_new_task = MagicMock()

    def _create_task(coro: object, *args: object, **kwargs: object) -> MagicMock:
        close = getattr(coro, "close", None)
        if callable(close):
            close()
        return fake_new_task

    with patch("asyncio.create_task", side_effect=_create_task) as mock_create:
        radio._civ_runtime.start_data_watchdog()
        mock_create.assert_called_once()


# ---------------------------------------------------------------------------
# _civ_data_watchdog_loop (lines 153, 157-175, 229-232)
# ---------------------------------------------------------------------------


async def test_watchdog_loop_continues_when_no_data_received(radio: IcomRadio) -> None:
    """Watchdog continues when _last_civ_data_received is not set (line 153)."""
    # Ensure the attribute is not set so getattr returns None
    if hasattr(radio, "_last_civ_data_received"):
        delattr(radio, "_last_civ_data_received")

    iteration_count = [0]

    async def mock_sleep(delay: float) -> None:
        iteration_count[0] += 1
        if iteration_count[0] >= 3:
            raise asyncio.CancelledError()

    with patch("asyncio.sleep", side_effect=mock_sleep):
        await radio._civ_runtime._civ_data_watchdog_loop()

    assert iteration_count[0] >= 3


async def test_watchdog_loop_phase1_sends_open_close(radio: IcomRadio) -> None:
    """Phase 1 recovery: sends open_close when idle > timeout (lines 157-175)."""
    # Set last data received far in the past to trigger recovery
    radio._last_civ_data_received = time.monotonic() - 10.0

    open_close_calls: list[bool] = []

    async def mock_open_close(*, open_stream: bool) -> None:
        open_close_calls.append(open_stream)

    sleep_count = [0]

    async def mock_sleep(delay: float) -> None:
        sleep_count[0] += 1
        # After 2 iterations in recovery mode, stop
        if sleep_count[0] >= 2:
            raise asyncio.CancelledError()

    with (
        patch("asyncio.sleep", side_effect=mock_sleep),
        patch.object(radio, "_send_open_close", side_effect=mock_open_close),
    ):
        await radio._civ_runtime._civ_data_watchdog_loop()

    # open_close(open_stream=True) should have been called
    assert True in open_close_calls


async def test_watchdog_loop_data_resumed_resets_recovering(radio: IcomRadio) -> None:
    """When data resumes while recovering, recovering flag is reset (lines 229-232)."""
    radio._last_civ_data_received = time.monotonic() - 10.0

    sleep_count = [0]

    async def mock_sleep(delay: float) -> None:
        sleep_count[0] += 1
        if sleep_count[0] == 1:
            # After first sleep, reset last received to now (data resumed!)
            radio._last_civ_data_received = time.monotonic()
        elif sleep_count[0] >= 3:
            raise asyncio.CancelledError()

    open_close_calls: list[bool] = []

    async def mock_open_close(*, open_stream: bool) -> None:
        open_close_calls.append(open_stream)

    with (
        patch("asyncio.sleep", side_effect=mock_sleep),
        patch.object(radio, "_send_open_close", side_effect=mock_open_close),
    ):
        await radio._civ_runtime._civ_data_watchdog_loop()

    # The loop should have run without error and data resumed path was hit


async def test_watchdog_loop_phase1_open_close_exception_ignored(
    radio: IcomRadio,
) -> None:
    """Phase 1: exceptions from _send_open_close are silently ignored (line 177-180)."""
    radio._last_civ_data_received = time.monotonic() - 10.0

    async def failing_open_close(*, open_stream: bool) -> None:
        raise OSError("network error")

    sleep_count = [0]

    async def mock_sleep(delay: float) -> None:
        sleep_count[0] += 1
        if sleep_count[0] >= 2:
            raise asyncio.CancelledError()

    # Should not propagate the open_close exception
    with (
        patch("asyncio.sleep", side_effect=mock_sleep),
        patch.object(radio, "_send_open_close", side_effect=failing_open_close),
    ):
        await (
            radio._civ_runtime._civ_data_watchdog_loop()
        )  # should complete without raising


async def test_watchdog_loop_phase2_uses_long_reconnect_cooldown(
    radio: IcomRadio,
) -> None:
    """Phase 2 uses a long cooldown before reconnect to avoid reconnect churn.

    After the OpenClose deadline, the watchdog spawns a detached reconnect
    task that sleeps for the cooldown BEFORE calling soft_reconnect.
    """
    radio._last_civ_data_received = 0.0
    radio._civ_recovering = False
    radio._force_cleanup_civ = AsyncMock()
    radio.soft_reconnect = AsyncMock()

    delays: list[float] = []

    async def fake_sleep(delay: float) -> None:
        delays.append(delay)

    monotonic_values = iter([200.0, 200.0, 265.0, 265.1, 265.2])

    def _mono() -> float:
        try:
            return next(monotonic_values)
        except StopIteration:
            return 265.2

    with (
        patch("asyncio.sleep", side_effect=fake_sleep),
        patch("time.monotonic", side_effect=_mono),
        patch.object(radio, "_send_open_close", new=AsyncMock()),
    ):
        await radio._civ_runtime._civ_data_watchdog_loop()
        # Await the spawned reconnect task explicitly so its cooldown sleep
        # runs inside the patched scope.
        spawned = [
            t for t in asyncio.all_tasks() if t.get_name().startswith("civ-watchdog-")
        ]
        for task in spawned:
            await task

    # 45s cooldown was observed in the spawned task (first backoff entry).
    assert any(d >= 45.0 for d in delays)
    radio.soft_reconnect.assert_awaited_once()


async def test_watchdog_soft_reconnect_task_sleeps_before_reconnect(
    radio: IcomRadio,
) -> None:
    """Detached soft_reconnect task sleeps for the cooldown BEFORE calling
    soft_reconnect — regression guard for the self-cancel bug where the
    watchdog cancelled itself and the cooldown sleep never ran.
    """
    order: list[str] = []

    async def record_force_cleanup() -> None:
        order.append("force_cleanup")

    async def record_sleep(delay: float) -> None:
        order.append(f"sleep({delay})")

    async def record_soft_reconnect() -> None:
        order.append("soft_reconnect")

    radio._force_cleanup_civ = record_force_cleanup
    radio.soft_reconnect = record_soft_reconnect

    with patch("asyncio.sleep", side_effect=record_sleep):
        await radio._civ_runtime._watchdog_soft_reconnect(cooldown=45.0)

    assert order == ["force_cleanup", "sleep(45.0)", "soft_reconnect"]


async def test_stop_data_watchdog_cancels_pending_reconnect_task(
    radio: IcomRadio,
) -> None:
    """stop_data_watchdog() must cancel any pending detached reconnect task
    spawned by watchdog escalation, so disconnect during cooldown does not
    trigger a late soft_reconnect (Codex P1 on PR #851).
    """
    radio._force_cleanup_civ = AsyncMock()
    radio.soft_reconnect = AsyncMock()

    # Spawn the reconnect helper the same way the watchdog does, and
    # register it on the runtime so stop_data_watchdog can find it.
    task = asyncio.create_task(
        radio._civ_runtime._watchdog_soft_reconnect(cooldown=5.0),
        name="civ-watchdog-soft-reconnect",
    )
    radio._civ_runtime._reconnect_task = task

    # Let the task enter its cooldown sleep.
    await asyncio.sleep(0.01)
    assert not task.done(), "task should still be sleeping in cooldown"

    # Explicit disconnect during cooldown.
    await radio._civ_runtime.stop_data_watchdog()

    assert task.done()
    radio.soft_reconnect.assert_not_awaited()
    assert radio._civ_runtime._reconnect_task is None


async def test_watchdog_patient_openclose_before_escalation(
    radio: IcomRadio,
) -> None:
    """Watchdog sends open_close for a patient period before escalating to
    soft_reconnect. Matches wfview's recovery pattern (icomudpcivdata.cpp:31):
    persistent OpenClose every 100ms rather than aggressive escalation.
    """
    radio._last_civ_data_received = 0.0
    radio._civ_recovering = False
    radio.soft_reconnect = AsyncMock()
    radio._force_cleanup_civ = AsyncMock()

    # Monotonic advances 1 sec per call; recovery_start captured on 2nd call,
    # then each elapsed_recovery check stays under the 60-sec deadline for
    # well past the old 5-sec cutoff.
    mono_time = [200.0]

    def _mono() -> float:
        mono_time[0] += 1.0
        return mono_time[0]

    sleep_count = [0]

    async def fake_sleep(delay: float) -> None:
        sleep_count[0] += 1
        if sleep_count[0] >= 30:
            raise asyncio.CancelledError()

    oc_mock = AsyncMock()

    with (
        patch("asyncio.sleep", side_effect=fake_sleep),
        patch("time.monotonic", side_effect=_mono),
        patch.object(radio, "_send_open_close", new=oc_mock),
    ):
        await radio._civ_runtime._civ_data_watchdog_loop()

    # Crossed the old 5-sec deadline many times but NOT escalated.
    radio.soft_reconnect.assert_not_awaited()
    # open_close called repeatedly during the patient period.
    assert oc_mock.await_count >= 10


# ---------------------------------------------------------------------------
# _ensure_civ_runtime (line 244)
# ---------------------------------------------------------------------------


def test_ensure_civ_runtime_raises_when_no_transport(radio: IcomRadio) -> None:
    """_ensure_civ_runtime raises ConnectionError when civ_transport is None (line 244)."""
    radio._civ_transport = None
    with pytest.raises(ConnectionError, match="Not connected to radio"):
        radio._civ_runtime._ensure_civ_runtime()


def test_ensure_civ_runtime_ok_when_transport_present(radio: IcomRadio) -> None:
    """_ensure_civ_runtime does nothing when transport is set."""
    radio._civ_runtime._ensure_civ_runtime()  # should not raise


# ---------------------------------------------------------------------------
# _civ_rx_loop — drain queue (lines 276-279), short packet (287)
# ---------------------------------------------------------------------------


async def test_civ_rx_loop_drains_extra_packets_from_queue(
    radio: IcomRadio, transport: MockTransport
) -> None:
    """rx loop drains all packets from _packet_queue non-blocking (lines 276-279)."""
    # Put a valid CI-V packet directly in the queue (bypassing receive_packet)
    freq_data = bcd_encode(14_074_000)
    civ = build_civ_frame(CONTROLLER_ADDR, IC_7610_ADDR, 0x03, data=freq_data)
    udp_pkt = _wrap_civ_in_udp(civ)

    # Queue one packet via normal path
    transport.queue_response(udp_pkt)
    # Put another packet directly in _packet_queue to test draining
    transport._packet_queue.put_nowait(udp_pkt)

    frames_routed = [0]

    async def counting_route(frame: CivFrame, *, generation: int) -> None:
        frames_routed[0] += 1

    # Run rx loop briefly: get the queued packet, drain extra, then timeout and exit
    radio._civ_rx_task = None
    radio._civ_runtime.start_pump()
    assert radio._civ_rx_task is not None

    with patch.object(
        radio._civ_runtime, "_route_civ_frame", side_effect=counting_route
    ):
        # Let loop run for a short time, then cancel
        await asyncio.sleep(0.05)
        radio._civ_rx_task.cancel()
        try:
            await radio._civ_rx_task
        except asyncio.CancelledError:
            pass

    # At least the normal receive path ran
    assert radio._civ_rx_task is None or radio._civ_rx_task.done()


async def test_civ_rx_loop_sheds_stale_scope_backlog_but_keeps_control_packets(
    radio: IcomRadio, transport: MockTransport
) -> None:
    """Under heavy backlog, stale scope-only packets are shed but control survives."""
    runtime = radio._civ_runtime

    scope_data = b"\x00\x01\x02"
    scope_civ = build_civ_frame(
        CONTROLLER_ADDR, IC_7610_ADDR, 0x27, sub=0x00, data=scope_data
    )
    scope_udp = _wrap_civ_in_udp(scope_civ)

    freq_data = bcd_encode(14_074_000)
    freq_civ = build_civ_frame(CONTROLLER_ADDR, IC_7610_ADDR, 0x03, data=freq_data)
    freq_udp = _wrap_civ_in_udp(freq_civ)

    packets = [scope_udp] * 300 + [freq_udp]
    shed = runtime._shed_scope_backlog(packets)

    assert len(shed) < len(packets)
    assert freq_udp in shed
    assert sum(1 for pkt in shed if pkt == scope_udp) == 64


async def test_civ_rx_loop_skips_short_packets(
    radio: IcomRadio, transport: MockTransport
) -> None:
    """rx loop skips packets shorter than CIV_HEADER_SIZE (line 287)."""
    # Queue a packet that is too short to contain CI-V data
    transport.queue_response(b"\x00" * 5)  # Only 5 bytes, < 21

    # Also queue a sentinel to detect loop iteration
    freq_data = bcd_encode(7_074_000)
    civ = build_civ_frame(CONTROLLER_ADDR, IC_7610_ADDR, 0x03, data=freq_data)
    transport.queue_response(_wrap_civ_in_udp(civ))

    frames_routed = [0]

    async def counting_route(frame: CivFrame, *, generation: int) -> None:
        frames_routed[0] += 1

    with patch.object(
        radio._civ_runtime, "_route_civ_frame", side_effect=counting_route
    ):
        radio._civ_runtime.start_pump()
        await asyncio.sleep(0.1)
        if radio._civ_rx_task:
            radio._civ_rx_task.cancel()
            try:
                await radio._civ_rx_task
            except asyncio.CancelledError:
                pass

    # Short packet is skipped, but valid packet processed
    assert frames_routed[0] >= 1


async def test_civ_rx_loop_skips_invalid_civ_frames(
    radio: IcomRadio, transport: MockTransport
) -> None:
    """rx loop handles ValueError from parse_civ_frame gracefully (lines 292-293)."""
    # Build a UDP packet with valid header but garbage CI-V data (no 0xFE 0xFE preamble)
    import struct

    header_size = CIV_HEADER_SIZE
    garbage_civ = b"\xff\xff\xff\xff"  # Not a valid CI-V frame
    total_len = header_size + len(garbage_civ)
    pkt = bytearray(total_len)
    struct.pack_into("<I", pkt, 0, total_len)
    # Fill rest of header with zeros
    pkt[header_size:] = garbage_civ
    transport.queue_response(bytes(pkt))

    # Queue valid packet to confirm loop continues
    freq_data = bcd_encode(14_000_000)
    civ = build_civ_frame(CONTROLLER_ADDR, IC_7610_ADDR, 0x03, data=freq_data)
    transport.queue_response(_wrap_civ_in_udp(civ))

    frames_routed = [0]

    async def counting_route(frame: CivFrame, *, generation: int) -> None:
        frames_routed[0] += 1

    with patch.object(
        radio._civ_runtime, "_route_civ_frame", side_effect=counting_route
    ):
        radio._civ_runtime.start_pump()
        await asyncio.sleep(0.15)
        if radio._civ_rx_task:
            radio._civ_rx_task.cancel()
            try:
                await radio._civ_rx_task
            except asyncio.CancelledError:
                pass


async def test_civ_rx_loop_handles_route_exception(
    radio: IcomRadio, transport: MockTransport
) -> None:
    """rx loop continues when _route_civ_frame raises (lines 296-297)."""
    freq_data = bcd_encode(14_074_000)
    civ = build_civ_frame(CONTROLLER_ADDR, IC_7610_ADDR, 0x03, data=freq_data)
    transport.queue_response(_wrap_civ_in_udp(civ))

    async def exploding_route(frame: CivFrame, *, generation: int) -> None:
        raise RuntimeError("route exploded")

    with patch.object(
        radio._civ_runtime, "_route_civ_frame", side_effect=exploding_route
    ):
        radio._civ_runtime.start_pump()
        await asyncio.sleep(0.1)
        if radio._civ_rx_task:
            radio._civ_rx_task.cancel()
            try:
                await radio._civ_rx_task
            except asyncio.CancelledError:
                pass
    # Loop should have survived the exception


# ---------------------------------------------------------------------------
# _route_civ_frame — from_addr / to_addr checks (lines 307, 311)
# ---------------------------------------------------------------------------


async def test_route_civ_frame_wrong_from_addr(radio: IcomRadio) -> None:
    """Frame with wrong from_addr is silently dropped (line 307)."""
    frame = _make_frame(cmd=0x03, from_addr=0x00)  # wrong: not IC_7610_ADDR (0x94)
    await radio._civ_runtime._route_civ_frame(frame, generation=radio._civ_epoch)
    # No error, no state change expected


async def test_route_civ_frame_wrong_to_addr(radio: IcomRadio) -> None:
    """Frame addressed to unknown dest is silently dropped (line 311)."""
    frame = _make_frame(cmd=0x03, to_addr=0x12)  # not CONTROLLER_ADDR or 0x00
    await radio._civ_runtime._route_civ_frame(frame, generation=radio._civ_epoch)


async def test_route_civ_frame_broadcast_addr_accepted(radio: IcomRadio) -> None:
    """Frame addressed to 0x00 (broadcast) is accepted (line 310)."""
    freq_data = bcd_encode(14_074_000)
    frame = _make_frame(cmd=0x03, to_addr=0x00, data=freq_data)
    await radio._civ_runtime._route_civ_frame(frame, generation=radio._civ_epoch)


# ---------------------------------------------------------------------------
# _update_state_cache_from_frame — various command branches
# ---------------------------------------------------------------------------


def test_update_state_cache_s_meter_sub_02(radio: IcomRadio) -> None:
    """cmd 0x15 sub 0x02 updates s_meter cache (line 401)."""
    frame = _make_frame(cmd=0x15, sub=0x02, data=_bcd2(100))
    radio._civ_runtime._update_state_cache_from_frame(frame)
    # State cache should have been updated; no error expected


def test_update_state_cache_level_rf_power(radio: IcomRadio) -> None:
    """cmd 0x14 sub 0x0A updates power level (line 410-413)."""
    frame = _make_frame(cmd=0x14, sub=0x0A, data=_bcd2(128))
    radio._civ_runtime._update_state_cache_from_frame(frame)


def test_update_state_cache_level_rf_gain(radio: IcomRadio) -> None:
    """cmd 0x14 sub 0x02 updates RF gain (line 414-415)."""
    frame = _make_frame(cmd=0x14, sub=0x02, data=_bcd2(200))
    radio._civ_runtime._update_state_cache_from_frame(frame)


def test_update_state_cache_level_af_level(radio: IcomRadio) -> None:
    """cmd 0x14 sub 0x01 updates AF level (line 416-418)."""
    frame = _make_frame(cmd=0x14, sub=0x01, data=_bcd2(150))
    radio._civ_runtime._update_state_cache_from_frame(frame)


def test_update_state_cache_cmd29_sub_level_does_not_overwrite_main(
    radio: IcomRadio,
) -> None:
    """SUB cmd29 level responses must update SUB state only."""
    radio._radio_state = RadioState()
    radio._radio_state.main.af_level = 10
    radio._radio_state.sub.af_level = 20

    frame = _make_frame(cmd=0x14, sub=0x01, data=_bcd2(150), receiver=0x01)

    radio._civ_runtime._update_state_cache_from_frame(frame)

    assert radio._radio_state.main.af_level == 10
    assert radio._radio_state.sub.af_level == 150


def test_update_state_cache_level_squelch(radio: IcomRadio) -> None:
    """cmd 0x14 sub 0x03 updates squelch (line 419-421)."""
    frame = _make_frame(cmd=0x14, sub=0x03, data=_bcd2(50))
    radio._civ_runtime._update_state_cache_from_frame(frame)


def test_update_state_cache_cmd29_sub_bool_does_not_overwrite_main(
    radio: IcomRadio,
) -> None:
    """SUB cmd29 boolean responses must update SUB indicators only."""
    radio._radio_state = RadioState()
    radio._radio_state.main.nb = False
    radio._radio_state.sub.nb = False

    frame = _make_frame(cmd=0x16, sub=0x22, data=bytes([0x01]), receiver=0x01)

    radio._civ_runtime._update_state_cache_from_frame(frame)

    assert radio._radio_state.main.nb is False
    assert radio._radio_state.sub.nb is True


def test_update_state_cache_ip_plus(radio: IcomRadio) -> None:
    """cmd 0x16 sub 0x65 fires IP+ change notification (lines 449-450)."""
    notify_calls: dict[str, dict[str, object]] = {}

    def _on_change(name: str, data: dict[str, object]) -> None:
        notify_calls[name] = data

    radio._on_state_change = _on_change
    frame = _make_frame(cmd=0x16, sub=0x65, data=bytes([0x01]))
    radio._civ_runtime._update_state_cache_from_frame(frame)
    assert "ipplus_changed" in notify_calls


def test_update_state_cache_filter_width_decodes_index_to_hz(radio: IcomRadio) -> None:
    """Filter width response stores Hz decoded from the profile mapping.

    MOR-437: the RadioState mirror was removed; the value lands in the
    StateStore (and the private executor _state_cache fallback) instead.
    """
    radio._radio_state = RadioState()
    radio._radio_state.main.mode = "USB"
    frame = _make_frame(cmd=0x1A, sub=0x03, data=b"\x00\x19", receiver=0x00)

    radio._civ_runtime._update_state_cache_from_frame(frame)

    # Legacy RadioState mirror is no longer written.
    assert radio._radio_state.main.filter_width is None
    # Profile-dependent decode (USB, BCD index 0x19=19 → 1500 Hz) lands in store.
    field = radio._state_store.snapshot().field(
        "receiver.0.active.freq_mode.filter_width"
    )
    assert field.value == 1500
    # Executor cache fallback keeps the same decoded value.
    assert radio._state_cache.filter_width == 1500


def test_update_state_cache_exception_suppressed(radio: IcomRadio) -> None:
    """Exception in cache update is suppressed (lines 456-457)."""
    # StateCache uses slots=True, so replace the whole object with a MagicMock
    mock_cache = MagicMock()
    mock_cache.update_freq = MagicMock(side_effect=RuntimeError("oops"))
    radio._state_cache = mock_cache
    freq_data = bcd_encode(14_074_000)
    frame = _make_frame(cmd=0x03, data=freq_data)
    # Should NOT raise, exception is swallowed
    radio._civ_runtime._update_state_cache_from_frame(frame)


@pytest.mark.parametrize(  # type: ignore[untyped-decorator]
    ("frame", "path", "expected", "expected_source"),
    [
        (
            _make_frame(cmd=0x00, data=bcd_encode(7_074_000)),
            "receiver.0.active.freq_mode.freq_hz",
            7_074_000,
            "civ_unsolicited",
        ),
        (
            _make_frame(cmd=0x03, data=bcd_encode(14_074_000)),
            "receiver.0.active.freq_mode.freq_hz",
            14_074_000,
            "command_response",
        ),
        (
            _make_frame(cmd=0x01, data=bytes([Mode.USB.value, 1])),
            "receiver.0.active.freq_mode.mode",
            "USB",
            "civ_unsolicited",
        ),
        (
            _make_frame(cmd=0x04, data=bytes([Mode.LSB.value, 2])),
            "receiver.0.active.freq_mode.mode",
            "LSB",
            "command_response",
        ),
        (
            _make_frame(cmd=0x04, data=bytes([Mode.LSB.value, 2])),
            "receiver.0.active.freq_mode.filter_num",
            2,
            "command_response",
        ),
        (
            _make_frame(cmd=0x01, data=bytes([Mode.USB.value, 1])),
            "receiver.0.active.freq_mode.filter_num",
            1,
            "civ_unsolicited",
        ),
        (
            _make_frame(cmd=0x25, data=bytes([0x01]) + bcd_encode(21_074_000)),
            "receiver.1.active.freq_mode.freq_hz",
            21_074_000,
            "command_response",
        ),
        (
            _make_frame(cmd=0x26, data=bytes([0x01, Mode.CW.value, 0x00, 3])),
            "receiver.1.active.freq_mode.mode",
            "CW",
            "command_response",
        ),
        (
            _make_frame(cmd=0x26, data=bytes([0x01, Mode.CW.value, 0x00, 3])),
            "receiver.1.active.freq_mode.filter_num",
            3,
            "command_response",
        ),
        (
            _make_frame(cmd=0x15, sub=0x02, data=_bcd2(150), receiver=0x01),
            "receiver.1.meters.s_meter",
            150,
            "command_response",
        ),
        (
            _make_frame(cmd=0x15, sub=0x11, data=_bcd2(120)),
            "global.meters.power",
            120,
            "command_response",
        ),
        (
            _make_frame(cmd=0x15, sub=0x12, data=_bcd2(48)),
            "global.meters.swr",
            48,
            "command_response",
        ),
        (
            _make_frame(cmd=0x15, sub=0x13, data=_bcd2(98)),
            "global.meters.alc",
            98,
            "command_response",
        ),
        # PA-telemetry meters promoted to neutral observations (MOR-460).
        (
            _make_frame(cmd=0x15, sub=0x14, data=_bcd2(42)),
            "global.meters.comp",
            42,
            "command_response",
        ),
        (
            _make_frame(cmd=0x15, sub=0x15, data=_bcd2(130)),
            "global.meters.vd",
            130,
            "command_response",
        ),
        (
            _make_frame(cmd=0x15, sub=0x16, data=_bcd2(55)),
            "global.meters.id",
            55,
            "command_response",
        ),
        (
            _make_frame(cmd=0x14, sub=0x01, data=_bcd2(87), receiver=0x01),
            "receiver.1.operator_controls.af_level",
            87,
            "command_response",
        ),
        (
            _make_frame(cmd=0x14, sub=0x02, data=_bcd2(111), receiver=0x01),
            "receiver.1.operator_controls.rf_gain",
            111,
            "command_response",
        ),
        (
            _make_frame(cmd=0x14, sub=0x07, data=_bcd2(142), receiver=0x01),
            "receiver.1.operator_controls.pbt_inner",
            142,
            "command_response",
        ),
        (
            _make_frame(cmd=0x14, sub=0x08, data=_bcd2(143), receiver=0x01),
            "receiver.1.operator_controls.pbt_outer",
            143,
            "command_response",
        ),
        (
            _make_frame(cmd=0x16, sub=0x22, data=b"\x01", receiver=0x01),
            "receiver.1.operator_toggles.nb",
            True,
            "command_response",
        ),
        (
            _make_frame(cmd=0x16, sub=0x40, data=b"\x00", receiver=0x01),
            "receiver.1.operator_toggles.nr",
            False,
            "command_response",
        ),
        (
            _make_frame(cmd=0x18, data=b"\x01"),
            "global.tx_state.power_on",
            True,
            "command_response",
        ),
        (
            _make_frame(cmd=0x1C, sub=0x00, data=b"\x01"),
            "global.tx_state.ptt",
            True,
            "command_response",
        ),
        (
            _make_frame(cmd=0x07, data=bytes([0xD2, 0x01])),
            "global.slow_state.active",
            "SUB",
            "command_response",
        ),
        (
            _make_frame(cmd=0x21, sub=0x00, data=b"\x00\x02\x01"),
            "global.operator_controls.rit_freq",
            -200,
            "command_response",
        ),
        (
            _make_frame(cmd=0x21, sub=0x01, data=b"\x01"),
            "global.tx_state.rit_on",
            True,
            "command_response",
        ),
        (
            _make_frame(cmd=0x21, sub=0x02, data=b"\x01"),
            "global.tx_state.rit_tx",
            True,
            "command_response",
        ),
    ],
)
def test_update_state_cache_projects_supported_fields_into_state_store(
    radio: IcomRadio,
    frame: CivFrame,
    path: str,
    expected: object,
    expected_source: str,
) -> None:
    radio._civ_runtime._update_state_cache_from_frame(frame)

    field = radio._state_store.snapshot().field(path)

    assert field.value == expected
    assert field.source.source == expected_source


@pytest.mark.parametrize(  # type: ignore[untyped-decorator]
    "frame",
    [
        _make_frame(cmd=0x04, data=bytes([Mode.USB.value])),
        _make_frame(cmd=0x26, data=bytes([0x00, Mode.USB.value])),
        _make_frame(cmd=0x26, data=bytes([0x00, Mode.USB.value, 0x00])),
    ],
)
def test_update_state_cache_omits_filter_num_when_byte_absent(
    radio: IcomRadio,
    frame: CivFrame,
) -> None:
    """MOR-478: mode-only frames must not emit a filter_num observation."""
    radio._civ_runtime._update_state_cache_from_frame(frame)

    target_receiver = "1" if frame.command == 0x26 and frame.data[0] else "0"
    snapshot = radio._state_store.snapshot()
    # The mode observation still lands.
    snapshot.field(f"receiver.{target_receiver}.active.freq_mode.mode")
    # No filter_num observation is emitted (no crash, KeyError on lookup).
    with pytest.raises(KeyError):
        snapshot.field(f"receiver.{target_receiver}.active.freq_mode.filter_num")


# ---------------------------------------------------------------------------
# MOR-437: slow-state bool/status families observation-backed
# ---------------------------------------------------------------------------

# (frame, store path, public status path, expected value)
_SLOW_STATE_TOGGLE_CASES = (
    (
        _make_frame(cmd=0x16, sub=0x41, data=b"\x01", receiver=0x00),
        "receiver.0.operator_toggles.auto_notch",
        "main.autoNotch",
        True,
    ),
    (
        _make_frame(cmd=0x16, sub=0x48, data=b"\x01", receiver=0x00),
        "receiver.0.operator_toggles.manual_notch",
        "main.manualNotch",
        True,
    ),
    (
        _make_frame(cmd=0x16, sub=0x42, data=b"\x01", receiver=0x00),
        "receiver.0.operator_toggles.repeater_tone",
        "main.repeaterTone",
        True,
    ),
    (
        _make_frame(cmd=0x16, sub=0x43, data=b"\x01", receiver=0x00),
        "receiver.0.operator_toggles.repeater_tsql",
        "main.repeaterTsql",
        True,
    ),
    (
        _make_frame(cmd=0x16, sub=0x4F, data=b"\x01", receiver=0x00),
        "receiver.0.operator_toggles.twin_peak_filter",
        "main.twinPeakFilter",
        True,
    ),
    # DIGI-SEL (0x4E) and IP+ (0x65) receiver toggles — previously had no
    # StateStore observation, so they were invisible on IC-7610 (MOR-477).
    (
        _make_frame(cmd=0x16, sub=0x4E, data=b"\x01", receiver=0x00),
        "receiver.0.operator_toggles.digisel",
        "main.digisel",
        True,
    ),
    (
        _make_frame(cmd=0x16, sub=0x65, data=b"\x01", receiver=0x00),
        "receiver.0.operator_toggles.ipplus",
        "main.ipplus",
        True,
    ),
    (
        _make_frame(cmd=0x16, sub=0x44, data=b"\x01"),
        "global.tx_state.compressor_on",
        "compressorOn",
        True,
    ),
    (
        _make_frame(cmd=0x16, sub=0x45, data=b"\x01"),
        "global.tx_state.monitor_on",
        "monitorOn",
        True,
    ),
    (
        _make_frame(cmd=0x16, sub=0x46, data=b"\x01"),
        "global.tx_state.vox_on",
        "voxOn",
        True,
    ),
    (
        _make_frame(cmd=0x0F, data=b"\x01"),
        "global.tx_state.split",
        "split",
        True,
    ),
    (
        _make_frame(cmd=0x07, data=bytes([0xC2, 0x01])),
        "global.tx_state.dual_watch",
        "dualWatch",
        True,
    ),
    (
        _make_frame(cmd=0x1C, sub=0x01, data=b"\x02"),
        "global.operator_controls.tuner_status",
        "tunerStatus",
        2,
    ),
    (
        _make_frame(cmd=0x1C, sub=0x03, data=b"\x01"),
        "global.tx_state.tx_freq_monitor",
        "txFreqMonitor",
        True,
    ),
    # 0x12 0x00 0x01 RX-ANT for ANT1 ON → global slow-state bool (MOR-462).
    (
        _make_frame(cmd=0x12, sub=0x00, data=b"\x01"),
        "global.slow_state.rx_antenna_1",
        "rxAntenna1",
        True,
    ),
    # 0x12 0x01 0x01 RX-ANT for ANT2 ON → global slow-state bool (MOR-462).
    (
        _make_frame(cmd=0x12, sub=0x01, data=b"\x01"),
        "global.slow_state.rx_antenna_2",
        "rxAntenna2",
        True,
    ),
)


@pytest.mark.parametrize(  # type: ignore[untyped-decorator]
    ("frame", "store_path", "public_path", "expected"),
    _SLOW_STATE_TOGGLE_CASES,
    ids=[entry[1] for entry in _SLOW_STATE_TOGGLE_CASES],
)
def test_slow_state_toggle_observation_backed(
    radio: IcomRadio,
    frame: CivFrame,
    store_path: str,
    public_path: str,
    expected: object,
) -> None:
    """MOR-437: 0x16/0x0F/0x07/0x1C slow-state families emit StateStore observations."""
    radio._civ_runtime._update_state_cache_from_frame(frame)

    field = radio._state_store.snapshot().field(store_path)
    assert field.value == expected
    # Slow-state toggles never expire — no max_age, so the freshness service
    # cannot mark them stale and re-gate the frontend ``missing`` (MOR-437).
    assert field.max_age is None
    assert field.freshness is FreshnessState.FRESH


@pytest.mark.parametrize(  # type: ignore[untyped-decorator]
    ("frame", "store_path", "public_path", "expected"),
    _SLOW_STATE_TOGGLE_CASES,
    ids=[entry[1] for entry in _SLOW_STATE_TOGGLE_CASES],
)
def test_slow_state_toggle_survives_unrelated_poll_cycle(
    radio: IcomRadio,
    frame: CivFrame,
    store_path: str,
    public_path: str,
    expected: object,
) -> None:
    """The observed value must not snap back when an unrelated frame arrives."""
    radio._civ_runtime._update_state_cache_from_frame(frame)
    # An unrelated S-meter poll on the next cycle must not disturb the toggle.
    radio._civ_runtime._update_state_cache_from_frame(
        _make_frame(cmd=0x15, sub=0x02, data=_bcd2(120), receiver=0x00)
    )

    field = radio._state_store.snapshot().field(store_path)
    assert field.value == expected
    assert field.freshness is FreshnessState.FRESH


@pytest.mark.parametrize(  # type: ignore[untyped-decorator]
    ("frame", "store_path", "public_path", "expected"),
    _SLOW_STATE_TOGGLE_CASES,
    ids=[entry[1] for entry in _SLOW_STATE_TOGGLE_CASES],
)
def test_slow_state_toggle_projects_available(
    radio: IcomRadio,
    frame: CivFrame,
    store_path: str,
    public_path: str,
    expected: object,
) -> None:
    """Once observed, the public key projects to availability=available."""
    from rigplane.web.runtime_helpers import (
        build_public_state_payload_from_snapshot,
    )

    radio._civ_runtime._update_state_cache_from_frame(frame)
    payload = build_public_state_payload_from_snapshot(
        radio._state_store.snapshot(),
        radio=None,
        receiver_count=2,
    )

    status = payload["fieldStatus"][public_path]
    assert status["observed"] is True
    assert status["availability"] == "available"


# ---------------------------------------------------------------------------
# MOR-437 (BE-2): value-scaled level/control families observation-backed.
#
# Each case feeds a known raw CI-V payload and asserts the EXACT decoded value
# (compared against the same decode the removed mirror used), so a wrong scale
# would fail here rather than silently projecting a bad number.
# (frame, store path, public status path, expected decoded value)
# ---------------------------------------------------------------------------
_VALUE_CONTROL_CASES = (
    # 0x11 attenuator: BCD nibble 0x18 → 18 dB.
    (
        _make_frame(cmd=0x11, data=bytes([0x18]), receiver=0x00),
        "receiver.0.operator_controls.att",
        "main.att",
        18,
    ),
    # 0x16 0x02 preamp: raw byte verbatim.
    (
        _make_frame(cmd=0x16, sub=0x02, data=b"\x01", receiver=0x00),
        "receiver.0.operator_controls.preamp",
        "main.preamp",
        1,
    ),
    # 0x16 0x12 agc: BCD nibble 0x03 → 3.
    (
        _make_frame(cmd=0x16, sub=0x12, data=b"\x03", receiver=0x00),
        "receiver.0.operator_controls.agc",
        "main.agc",
        3,
    ),
    # 0x14 levels (4-digit BCD pair).
    (
        _make_frame(cmd=0x14, sub=0x03, data=_bcd2(50), receiver=0x00),
        "receiver.0.operator_controls.squelch",
        "main.squelch",
        50,
    ),
    (
        _make_frame(cmd=0x14, sub=0x06, data=_bcd2(91), receiver=0x00),
        "receiver.0.operator_controls.nr_level",
        "main.nrLevel",
        91,
    ),
    (
        _make_frame(cmd=0x14, sub=0x12, data=_bcd2(94), receiver=0x00),
        "receiver.0.operator_controls.nb_level",
        "main.nbLevel",
        94,
    ),
    (
        _make_frame(cmd=0x14, sub=0x0B, data=_bcd2(101)),
        "global.operator_controls.mic_gain",
        "micGain",
        101,
    ),
    (
        _make_frame(cmd=0x14, sub=0x0E, data=_bcd2(103)),
        "global.operator_controls.compressor_level",
        "compressorLevel",
        103,
    ),
    (
        _make_frame(cmd=0x14, sub=0x15, data=_bcd2(106)),
        "global.operator_controls.monitor_gain",
        "monitorGain",
        106,
    ),
    # 0x14 0x09 cw_pitch: non-linear raw level → Hz (level 128 → 600 Hz).
    (
        _make_frame(cmd=0x14, sub=0x09, data=_bcd2(128)),
        "global.operator_controls.cw_pitch",
        "cwPitch",
        600,
    ),
    # 0x1A 0x03 filter_width: profile-dependent index → Hz (USB index 19 → 1500).
    (
        _make_frame(cmd=0x1A, sub=0x03, data=b"\x00\x19", receiver=0x00),
        "receiver.0.active.freq_mode.filter_width",
        "main.filterWidth",
        1500,
    ),
    # 0x1A 0x06 data_mode: raw byte.
    (
        _make_frame(cmd=0x1A, sub=0x06, data=b"\x02", receiver=0x00),
        "receiver.0.active.freq_mode.data_mode",
        "main.dataMode",
        2,
    ),
    # 0x1A 0x04 agc_time_constant: 1-byte BCD 0x13 → 13.
    (
        _make_frame(cmd=0x1A, sub=0x04, data=b"\x13", receiver=0x00),
        "receiver.0.operator_controls.agc_time_constant",
        "main.agcTimeConstant",
        13,
    ),
    # 0x1B 0x00 tone_freq: BCD freq-encoded 88.5 Hz → 8850 centiHz (MOR-451).
    (
        _make_frame(cmd=0x1B, sub=0x00, data=_encode_tone_freq(88.5), receiver=0x00),
        "receiver.0.operator_controls.tone_freq",
        "main.toneFreq",
        8850,
    ),
    # 0x1B 0x01 tsql_freq: BCD freq-encoded 88.5 Hz → 8850 centiHz (MOR-451).
    (
        _make_frame(cmd=0x1B, sub=0x01, data=_encode_tone_freq(88.5), receiver=0x00),
        "receiver.0.operator_controls.tsql_freq",
        "main.tsqlFreq",
        8850,
    ),
    # 0x16 0x32 audio_peak_filter: BCD-nibble mode 0x02 → 2 (MID) (MOR-452).
    (
        _make_frame(cmd=0x16, sub=0x32, data=b"\x02", receiver=0x00),
        "receiver.0.operator_controls.audio_peak_filter",
        "main.audioPeakFilter",
        2,
    ),
    # 0x14 0x05 apf_type_level: 4-digit BCD pair (interim 0-255 device scale,
    # re-scaling tracked in MOR-453) (MOR-452).
    (
        _make_frame(cmd=0x14, sub=0x05, data=_bcd2(90), receiver=0x00),
        "receiver.0.operator_controls.apf_type_level",
        "main.apfTypeLevel",
        90,
    ),
    # 0x14 0x16 vox_gain: 4-digit BCD pair (interim device scale — MOR-453)
    # promoted to a global operator-control int (MOR-459).
    (
        _make_frame(cmd=0x14, sub=0x16, data=_bcd2(50)),
        "global.operator_controls.vox_gain",
        "voxGain",
        50,
    ),
    # 0x14 0x17 anti_vox_gain: 4-digit BCD pair (interim device scale — MOR-453)
    # promoted to a global operator-control int (MOR-459).
    (
        _make_frame(cmd=0x14, sub=0x17, data=_bcd2(30)),
        "global.operator_controls.anti_vox_gain",
        "antiVoxGain",
        30,
    ),
    # 0x1A 0x05 0x02 0x92 vox_delay: 1-byte BCD ctl-mem level (0-20) promoted to
    # a global operator-control int (MOR-459).
    (
        _make_frame(cmd=0x1A, sub=0x05, data=b"\x02\x92\x12"),
        "global.operator_controls.vox_delay",
        "voxDelay",
        12,
    ),
    # 0x10 tuning_step: device step index (0-8), BCD nibble-pair byte 0x05 → 5;
    # NOT Hz. Promoted to a global slow-state int (MOR-461).
    (
        _make_frame(cmd=0x10, data=b"\x05"),
        "global.slow_state.tuning_step",
        "tuningStep",
        5,
    ),
    # 0x12 0x01 antenna selection: sub 0x01 → ANT2 (value 2). Promoted to a
    # global operator-control int (MOR-462). Data byte 0x00 keeps RX-ANT off.
    (
        _make_frame(cmd=0x12, sub=0x01, data=b"\x00"),
        "global.operator_controls.tx_antenna",
        "txAntenna",
        2,
    ),
)


@pytest.mark.parametrize(  # type: ignore[untyped-decorator]
    ("frame", "store_path", "public_path", "expected"),
    _VALUE_CONTROL_CASES,
    ids=[entry[1] for entry in _VALUE_CONTROL_CASES],
)
def test_value_control_observation_value(
    radio_with_state: IcomRadio,
    frame: CivFrame,
    store_path: str,
    public_path: str,
    expected: object,
) -> None:
    """MOR-437 (BE-2): level/value CI-V families emit the exact decoded value."""
    # A real mode is required so the profile-dependent filter_width decode runs.
    radio_with_state._radio_state.main.mode = "USB"
    radio_with_state._civ_runtime._update_state_cache_from_frame(frame)

    field = radio_with_state._state_store.snapshot().field(store_path)
    assert field.value == expected
    # These level/value families never expire — no max_age, so the freshness
    # service cannot re-gate them ``missing`` (MOR-437).
    assert field.max_age is None
    assert field.freshness is FreshnessState.FRESH


@pytest.mark.parametrize(  # type: ignore[untyped-decorator]
    ("frame", "store_path", "public_path", "expected"),
    _VALUE_CONTROL_CASES,
    ids=[entry[1] for entry in _VALUE_CONTROL_CASES],
)
def test_value_control_survives_unrelated_poll_cycle(
    radio_with_state: IcomRadio,
    frame: CivFrame,
    store_path: str,
    public_path: str,
    expected: object,
) -> None:
    """The observed value must not snap back when an unrelated frame arrives."""
    radio_with_state._radio_state.main.mode = "USB"
    radio_with_state._civ_runtime._update_state_cache_from_frame(frame)
    # An unrelated S-meter poll on the next cycle must not disturb the value.
    radio_with_state._civ_runtime._update_state_cache_from_frame(
        _make_frame(cmd=0x15, sub=0x02, data=_bcd2(120), receiver=0x00)
    )

    field = radio_with_state._state_store.snapshot().field(store_path)
    assert field.value == expected
    assert field.freshness is FreshnessState.FRESH


@pytest.mark.parametrize(  # type: ignore[untyped-decorator]
    ("frame", "store_path", "public_path", "expected"),
    _VALUE_CONTROL_CASES,
    ids=[entry[1] for entry in _VALUE_CONTROL_CASES],
)
def test_value_control_projects_available(
    radio_with_state: IcomRadio,
    frame: CivFrame,
    store_path: str,
    public_path: str,
    expected: object,
) -> None:
    """Once observed, the public key projects available with the right value."""
    from rigplane.web.runtime_helpers import (
        build_public_state_payload_from_snapshot,
    )

    radio_with_state._radio_state.main.mode = "USB"
    radio_with_state._civ_runtime._update_state_cache_from_frame(frame)
    payload = build_public_state_payload_from_snapshot(
        radio_with_state._state_store.snapshot(),
        radio=None,
        receiver_count=2,
    )

    status = payload["fieldStatus"][public_path]
    assert status["observed"] is True
    assert status["availability"] == "available"

    # The decoded value also lands in the main public state payload at the
    # camelCase public path (top-level for globals, ``main.*`` for receiver).
    node: Any = payload
    for segment in public_path.split("."):
        node = node[segment]
    assert node == expected


def test_civ_projected_active_rit_xit_fields_become_stale(
    radio: IcomRadio,
) -> None:
    try:
        frames_and_paths = (
            (
                _make_frame(cmd=0x07, data=bytes([0xD2, 0x01])),
                "global.slow_state.active",
            ),
            (
                _make_frame(cmd=0x21, sub=0x00, data=b"\x00\x02\x01"),
                "global.operator_controls.rit_freq",
            ),
            (_make_frame(cmd=0x21, sub=0x01, data=b"\x01"), "global.tx_state.rit_on"),
            (_make_frame(cmd=0x21, sub=0x02, data=b"\x01"), "global.tx_state.rit_tx"),
        )

        for frame, _path in frames_and_paths:
            radio._civ_runtime._update_state_cache_from_frame(frame)

        fields = tuple(
            radio._state_store.snapshot().field(path)
            for _frame, path in frames_and_paths
        )
        max_age = max(field.max_age or 0.0 for field in fields)

        assert all(field.freshness is FreshnessState.FRESH for field in fields)
        assert all(field.max_age is not None for field in fields)

        stale = radio._state_store.mark_stale_due(now=time.monotonic() + max_age + 1.0)

        expected_paths = {path for _frame, path in frames_and_paths}
        assert {
            str(transition.path) for transition in stale.freshness
        } >= expected_paths
        assert {
            str(request.path) for request in stale.reconciliation_requests
        } >= expected_paths
    finally:
        radio._connected = False


def test_update_state_cache_records_scheduler_result_for_matching_pending_request(
    radio: IcomRadio,
) -> None:
    class _SpyScheduler(AcquisitionScheduler):
        def __init__(self, *, profile: RadioAcquisitionProfile) -> None:
            super().__init__(profile=profile)
            self.recorded_count = 0

        def record_acquisition_result(self, request, change_set):  # type: ignore[no-untyped-def]
            self.recorded_count += 1
            return super().record_acquisition_result(request, change_set)

    path = FieldPath.active("main", "freq_mode", "freq_hz")
    scheduler = _SpyScheduler(profile=_acquisition_profile(path))
    scheduler.due_requests(now=0.0)
    radio._acquisition_scheduler = scheduler

    radio._civ_runtime._update_state_cache_from_frame(
        _make_frame(cmd=0x03, data=bcd_encode(14_074_000))
    )

    assert scheduler.recorded_count == 1
    assert scheduler.pending_requests() == ()


@pytest.mark.parametrize(  # type: ignore[untyped-decorator]
    ("path", "cmd", "receiver", "payload", "stored_path", "expected"),
    [
        (
            FieldPath.active("main", "freq_mode", "freq_hz"),
            0x25,
            0x00,
            bcd_encode(14_074_000),
            "receiver.0.active.freq_mode.freq_hz",
            14_074_000,
        ),
        (
            FieldPath.active("sub", "freq_mode", "freq_hz"),
            0x25,
            0x01,
            bcd_encode(7_100_000),
            "receiver.1.active.freq_mode.freq_hz",
            7_100_000,
        ),
        (
            FieldPath.active("main", "freq_mode", "mode"),
            0x26,
            0x00,
            bytes([Mode.USB.value, 0x00, 1]),
            "receiver.0.active.freq_mode.mode",
            "USB",
        ),
        (
            FieldPath.active("sub", "freq_mode", "mode"),
            0x26,
            0x01,
            bytes([Mode.CW.value, 0x00, 3]),
            "receiver.1.active.freq_mode.mode",
            "CW",
        ),
    ],
)
async def test_scheduler_active_freq_mode_request_completes_from_civ_rx_loop(
    radio: IcomRadio,
    transport: MockTransport,
    path: FieldPath,
    cmd: int,
    receiver: int,
    payload: bytes,
    stored_path: str,
    expected: object,
) -> None:
    scheduler = AcquisitionScheduler(profile=_acquisition_profile(path))
    radio._acquisition_scheduler = scheduler
    poller = RadioPoller(radio, CommandQueue(), radio_state=RadioState())

    await poller._send_query()  # noqa: SLF001

    assert scheduler.pending_requests()[0].paths == (path,)

    civ = build_civ_frame(
        CONTROLLER_ADDR,
        IC_7610_ADDR,
        cmd,
        data=bytes([receiver]) + payload,
    )
    transport.queue_response(_wrap_civ_in_udp(civ))

    radio._civ_runtime.start_pump()
    try:
        for _ in range(20):
            if scheduler.pending_requests() == ():
                break
            await asyncio.sleep(0.01)
    finally:
        await radio._civ_runtime.stop_pump()
        radio._connected = False

    assert scheduler.pending_requests() == ()
    assert radio._state_store.snapshot().field(stored_path).value == expected


def test_meter_coalescing_applies_latest_due_sample_and_records_diagnostics(
    radio: IcomRadio,
) -> None:
    path = FieldPath.receiver("main", "meters", "s_meter")
    policy = AcquisitionPolicy(
        cadence_seconds=1.0,
        freshness_ttl_seconds=4.0,
        meter_coalescing=MeterCoalescingPolicy(window_seconds=0.2, max_samples=2),
    )
    radio._acquisition_scheduler = AcquisitionScheduler(
        profile=_acquisition_profile(path, policy=policy)
    )
    radio._meter_observation_coalescer = MeterObservationCoalescer()
    radio._state_diagnostics = StateDiagnosticsRecorder(enabled=True)
    events: list[tuple[str, dict[str, object]]] = []
    radio._on_state_change = lambda name, data: events.append((name, data))

    with patch(
        "rigplane.runtime._civ_rx.time.monotonic",
        side_effect=[100.0, 100.0, 100.1, 100.1],
    ):
        radio._civ_runtime._apply_state_store_observations(
            _make_frame(cmd=0x15, sub=0x02, data=_bcd2(111))
        )
        radio._civ_runtime._apply_state_store_observations(
            _make_frame(cmd=0x15, sub=0x02, data=_bcd2(222))
        )

    with pytest.raises(KeyError):
        radio._state_store.snapshot().field("receiver.0.meters.s_meter")

    radio._civ_runtime.flush_due_meter_observations(now=100.3)

    assert radio._state_store.snapshot().field("receiver.0.meters.s_meter").value == 222
    assert events == [
        (
            "state_store_changed",
            {"coalesced": True, "paths": ["receiver.0.meters.s_meter"]},
        )
    ]
    meter_events = [
        event
        for event in radio._state_diagnostics.events()
        if event.kind == "meter_coalescing"
    ]
    assert meter_events[-1].details == {
        "pendingSampleCount": 0,
        "pendingPaths": [],
        "droppedSampleCount": 0,
        "coalescedSampleCount": 1,
        "nextFlushMonotonic": None,
    }


def test_same_value_coalesced_meter_flush_completes_scheduler_request(
    radio: IcomRadio,
) -> None:
    path = FieldPath.receiver("main", "meters", "s_meter")
    stored_path = FieldPath.receiver("0", "meters", "s_meter")
    policy = AcquisitionPolicy(
        cadence_seconds=1.0,
        freshness_ttl_seconds=4.0,
        meter_coalescing=MeterCoalescingPolicy(window_seconds=0.2),
    )
    radio._state_store.apply(
        radio._civ_runtime._observation(
            stored_path,
            111,
            frame=_make_frame(cmd=0x15, sub=0x02, data=_bcd2(111)),
        )
    )
    scheduler = AcquisitionScheduler(profile=_acquisition_profile(path, policy=policy))
    scheduler.due_requests(now=100.0)
    radio._acquisition_scheduler = scheduler
    radio._meter_observation_coalescer = MeterObservationCoalescer()
    radio._state_diagnostics = StateDiagnosticsRecorder(enabled=True)

    with patch("rigplane.runtime._civ_rx.time.monotonic", return_value=100.0):
        radio._civ_runtime._apply_state_store_observations(
            _make_frame(cmd=0x15, sub=0x02, data=_bcd2(111))
        )
    changeset = radio._civ_runtime.flush_due_meter_observations(now=100.2)

    assert changeset is not None
    assert changeset.changes == ()
    assert scheduler.pending_requests() == ()
    assert (
        scheduler.diagnostics()["cadenceByPath"][str(path)]["nextDueMonotonic"] == 101.0
    )
    assert any(
        event.kind == "acquisition_result"
        and event.details["paths"] == [str(path)]
        and event.details["changed"] is False
        for event in radio._state_diagnostics.events()
    )


def test_same_value_stale_refresh_notifies_state_store_changed(
    radio: IcomRadio,
) -> None:
    path = FieldPath.receiver("main", "meters", "s_meter")
    events: list[tuple[str, dict[str, object]]] = []
    radio._on_state_change = lambda name, data: events.append((name, data))

    radio._state_store.apply(
        radio._civ_runtime._observation(
            path,
            111,
            frame=_make_frame(cmd=0x15, sub=0x02, data=_bcd2(111)),
        )
    )
    radio._state_store.mark_stale_due(now=time.monotonic() + 1.0)

    radio._civ_runtime._apply_state_store_observations(
        _make_frame(cmd=0x15, sub=0x02, data=_bcd2(111))
    )

    assert events == [
        (
            "state_store_changed",
            {"coalesced": False, "paths": ["receiver.0.meters.s_meter"]},
        )
    ]


@pytest.mark.parametrize(  # type: ignore[untyped-decorator]
    ("frame", "field_name", "initial_value"),
    [
        (_make_frame(cmd=0x03, data=bcd_encode(14_074_000)), "freq", 0),
        (_make_frame(cmd=0x04, data=bytes([Mode.LSB.value, 2])), "mode", "USB"),
        (_make_frame(cmd=0x1C, sub=0x00, data=b"\x01"), "ptt", False),
        (_make_frame(cmd=0x18, data=b"\x01"), "powerstat", False),
    ],
)
def test_update_state_cache_applies_state_store_before_legacy_cache_mirror(
    radio: IcomRadio,
    frame: CivFrame,
    field_name: str,
    initial_value: object,
) -> None:
    setattr(radio._state_cache, field_name, initial_value)
    seen_legacy_values: list[object] = []
    original_apply = type(radio._state_store).apply

    def apply_and_record(store: object, observation: object) -> object:
        seen_legacy_values.append(getattr(radio._state_cache, field_name))
        return original_apply(store, observation)

    with patch.object(
        type(radio._state_store),
        "apply",
        autospec=True,
        side_effect=apply_and_record,
    ):
        radio._civ_runtime._update_state_cache_from_frame(frame)

    # The 0x04 mode frame now also emits a filter_num observation (MOR-478), so
    # ``apply`` is invoked once per observation. Every apply must still observe
    # the *pre-mirror* legacy value, proving the store projection precedes the
    # legacy cache mutation regardless of how many observations a frame yields.
    assert seen_legacy_values
    assert all(value == initial_value for value in seen_legacy_values)


def test_update_state_cache_uses_slot_specific_observation_path_when_override_active(
    radio: IcomRadio,
) -> None:
    radio._vfo_slot_override = {"MAIN": "B"}  # noqa: SLF001
    radio._civ_runtime._update_state_cache_from_frame(
        _make_frame(cmd=0x03, data=bcd_encode(14_250_000))
    )

    assert (
        radio._state_store.snapshot().field("receiver.0.slot.B.freq_mode.freq_hz").value
        == 14_250_000
    )


def test_update_state_cache_applies_command_response_observation_once(
    radio: IcomRadio,
) -> None:
    frame = _make_frame(cmd=0x03, data=bcd_encode(14_074_000))

    radio._civ_runtime._update_state_cache_from_frame(frame)

    snapshot = radio._state_store.snapshot()
    delta = radio._state_store.delta_since(StateSnapshot.empty())

    assert snapshot.state_revision == 1
    assert snapshot.observation_seq == 1
    assert len(delta.changes) == 1
    assert delta.changes[0].path == snapshot.fields[0].path
    assert radio._radio_state.main.freq == 14_074_000


@pytest.mark.parametrize(  # type: ignore[untyped-decorator]
    "frame",
    [
        _make_frame(cmd=0x03, data=b"\x12\x34"),
        _make_frame(cmd=0x04, data=b""),
        _make_frame(cmd=0x04, data=bytes([0xFF, 0x01])),
        _make_frame(cmd=0x26, data=bytes([0x00, 0xFF])),
    ],
)
async def test_route_civ_frame_contains_observation_decode_failures(
    radio: IcomRadio,
    frame: CivFrame,
) -> None:
    published: list[Any] = []
    radio._civ_request_tracker.resolve = MagicMock()

    with patch.object(
        radio._civ_runtime,
        "_publish_civ_event",
        side_effect=published.append,
    ):
        await radio._civ_runtime._route_civ_frame(frame, generation=17)

    assert len(published) == 1
    assert published[0].frame == frame
    radio._civ_request_tracker.resolve.assert_called_once_with(
        published[0], generation=17
    )
    assert radio._state_store.snapshot().observation_seq == 0


def test_update_state_cache_cmd07_d2_projects_active_receiver_and_legacy_mirror(
    radio: IcomRadio,
) -> None:
    radio._radio_state = RadioState()

    radio._civ_runtime._update_state_cache_from_frame(
        _make_frame(cmd=0x07, data=bytes([0xD2, 0x01]))
    )

    assert radio._radio_state.active == "SUB"
    field = radio._state_store.snapshot().field("global.slow_state.active")
    assert field.value == "SUB"
    assert field.source.source == "command_response"


# ---------------------------------------------------------------------------
# _update_radio_state_from_frame (lines 470-622)
# ---------------------------------------------------------------------------


@pytest.fixture  # type: ignore[untyped-decorator]
def radio_with_state(radio: IcomRadio) -> IcomRadio:
    """Radio with RadioState set for testing _update_radio_state_from_frame."""
    radio._radio_state = RadioState()
    return radio


def test_update_radio_state_freq_cmd03(radio_with_state: IcomRadio) -> None:
    """cmd 0x03 updates receiver frequency (line 482-483)."""
    rs = radio_with_state._radio_state
    frame = _make_frame(cmd=0x03, data=bcd_encode(14_074_000))
    radio_with_state._civ_runtime._update_radio_state_from_frame(frame)
    assert rs.main.freq == 14_074_000


def test_update_radio_state_freq_cmd00(radio_with_state: IcomRadio) -> None:
    """cmd 0x00 (unsolicited transceive) also updates frequency (line 481)."""
    rs = radio_with_state._radio_state
    frame = _make_frame(cmd=0x00, data=bcd_encode(7_074_000))
    radio_with_state._civ_runtime._update_radio_state_from_frame(frame)
    assert rs.main.freq == 7_074_000


def test_update_radio_state_mode_cmd04(radio_with_state: IcomRadio) -> None:
    """cmd 0x04 updates receiver mode and filter (lines 485-490)."""
    rs = radio_with_state._radio_state
    frame = _make_frame(cmd=0x04, data=bytes([Mode.LSB.value, 2]))
    radio_with_state._civ_runtime._update_radio_state_from_frame(frame)
    assert rs.main.mode == "LSB"
    assert rs.main.filter == 2


def test_update_radio_state_mode_cmd01(radio_with_state: IcomRadio) -> None:
    """cmd 0x01 (unsolicited) updates mode (line 485)."""
    rs = radio_with_state._radio_state
    frame = _make_frame(cmd=0x01, data=bytes([Mode.USB.value, 1]))
    radio_with_state._civ_runtime._update_radio_state_from_frame(frame)
    assert rs.main.mode == "USB"


def test_update_radio_state_cmd25_rx_frequency(radio_with_state: IcomRadio) -> None:
    """cmd 0x25 updates dual-receiver frequency (lines 493-502)."""
    rs = radio_with_state._radio_state
    # data[0]=0x00 (MAIN), data[1:6]=freq BCD
    freq_bcd = bcd_encode(21_000_000)
    frame = _make_frame(cmd=0x25, data=bytes([0x00]) + freq_bcd)
    radio_with_state._civ_runtime._update_radio_state_from_frame(frame)
    assert rs.main.freq == 21_000_000


def test_update_radio_state_cmd25_sub_receiver(radio_with_state: IcomRadio) -> None:
    """cmd 0x25 with receiver=0x01 updates SUB receiver (line 501)."""
    rs = radio_with_state._radio_state
    freq_bcd = bcd_encode(28_000_000)
    frame = _make_frame(cmd=0x25, data=bytes([0x01]) + freq_bcd)
    radio_with_state._civ_runtime._update_radio_state_from_frame(frame)
    assert rs.sub.freq == 28_000_000


def test_update_radio_state_cmd25_short_data_ignored(
    radio_with_state: IcomRadio,
) -> None:
    """cmd 0x25 with short data is ignored (condition: len >= 6)."""
    frame = _make_frame(cmd=0x25, data=bytes([0x00, 0x01, 0x02]))
    radio_with_state._civ_runtime._update_radio_state_from_frame(
        frame
    )  # should not raise


def test_update_radio_state_cmd26_rx_mode(radio_with_state: IcomRadio) -> None:
    """cmd 0x26 updates dual-receiver mode (lines 504-518)."""
    rs = radio_with_state._radio_state
    frame = _make_frame(cmd=0x26, data=bytes([0x01, Mode.CW.value, 0x00, 3]))
    radio_with_state._civ_runtime._update_radio_state_from_frame(frame)
    assert rs.sub.mode == "CW"
    assert rs.sub.filter == 3


def test_update_radio_state_cmd26_minimal(radio_with_state: IcomRadio) -> None:
    """cmd 0x26 with minimal 2-byte data."""
    frame = _make_frame(cmd=0x26, data=bytes([0x00, Mode.FM.value]))
    radio_with_state._civ_runtime._update_radio_state_from_frame(frame)
    # No crash


def test_update_radio_state_cmd26_with_data_mode(radio_with_state: IcomRadio) -> None:
    """cmd 0x26 with data_mode byte (line 516)."""
    rs = radio_with_state._radio_state
    frame = _make_frame(cmd=0x26, data=bytes([0x00, Mode.USB.value, 0x03]))
    radio_with_state._civ_runtime._update_radio_state_from_frame(frame)
    assert rs.main.data_mode == 3


def test_update_radio_state_cmd15_smeter(radio_with_state: IcomRadio) -> None:
    """cmd 0x15 sub 0x02 updates s_meter on active receiver (lines 521-528)."""
    rs = radio_with_state._radio_state
    frame = _make_frame(cmd=0x15, sub=0x02, data=_bcd2(150))
    radio_with_state._civ_runtime._update_radio_state_from_frame(frame)
    assert rs.main.s_meter == 150


def test_update_radio_state_cmd14_af_level(radio_with_state: IcomRadio) -> None:
    """cmd 0x14 sub 0x01 updates AF level (lines 531-541)."""
    rs = radio_with_state._radio_state
    frame = _make_frame(cmd=0x14, sub=0x01, data=_bcd2(200))
    radio_with_state._civ_runtime._update_radio_state_from_frame(frame)
    assert rs.main.af_level == 200


def test_update_radio_state_cmd14_rf_gain(radio_with_state: IcomRadio) -> None:
    """cmd 0x14 sub 0x02 RF gain is observation-backed (MOR-437)."""
    rs = radio_with_state._radio_state
    frame = _make_frame(cmd=0x14, sub=0x02, data=_bcd2(180))
    radio_with_state._civ_runtime._update_state_cache_from_frame(frame)
    # Legacy mirror removed: StateStore is the source of truth.
    assert rs.main.rf_gain == 0
    field = radio_with_state._state_store.snapshot().field(
        "receiver.0.operator_controls.rf_gain"
    )
    assert field.value == 180


def test_update_radio_state_cmd14_squelch(radio_with_state: IcomRadio) -> None:
    """cmd 0x14 sub 0x03 squelch is observation-backed (MOR-437)."""
    rs = radio_with_state._radio_state
    frame = _make_frame(cmd=0x14, sub=0x03, data=_bcd2(50))
    radio_with_state._civ_runtime._update_state_cache_from_frame(frame)
    assert rs.main.squelch == 0
    field = radio_with_state._state_store.snapshot().field(
        "receiver.0.operator_controls.squelch"
    )
    assert field.value == 50


def test_update_radio_state_cmd14_power_level(radio_with_state: IcomRadio) -> None:
    """cmd 0x14 sub 0x0A updates global power level (line 545-546)."""
    rs = radio_with_state._radio_state
    frame = _make_frame(cmd=0x14, sub=0x0A, data=_bcd2(128))
    radio_with_state._civ_runtime._update_radio_state_from_frame(frame)
    assert rs.power_level == 128


@pytest.mark.parametrize(  # type: ignore[untyped-decorator]
    ("sub", "value", "field"),
    [
        (0x07, 92, "pbt_inner"),
        (0x08, 93, "pbt_outer"),
        (0x13, 95, "digisel_shift"),
    ],
)
def test_update_radio_state_cmd14_receiver_dsp_levels(
    radio_with_state: IcomRadio,
    sub: int,
    value: int,
    field: str,
) -> None:
    rs = radio_with_state._radio_state
    frame = _make_frame(cmd=0x14, sub=sub, data=_bcd2(value), receiver=0x01)
    radio_with_state._civ_runtime._update_radio_state_from_frame(frame)
    assert getattr(rs.sub, field) == value


@pytest.mark.parametrize(  # type: ignore[untyped-decorator]
    ("sub", "value", "field"),
    [
        (0x05, 90, "apf_type_level"),
        (0x06, 91, "nr_level"),
        (0x12, 94, "nb_level"),
    ],
)
def test_update_radio_state_cmd14_receiver_dsp_levels_observation_backed(
    radio_with_state: IcomRadio,
    sub: int,
    value: int,
    field: str,
) -> None:
    """MOR-437/MOR-452: apf_type_level/nr_level/nb_level mirror removed; store is truth."""
    rs = radio_with_state._radio_state
    frame = _make_frame(cmd=0x14, sub=sub, data=_bcd2(value), receiver=0x01)
    radio_with_state._civ_runtime._update_state_cache_from_frame(frame)
    assert getattr(rs.sub, field) == 0
    store_field = radio_with_state._state_store.snapshot().field(
        f"receiver.1.operator_controls.{field}"
    )
    assert store_field.value == value


@pytest.mark.parametrize(  # type: ignore[untyped-decorator]
    ("sub", "raw", "field", "expected"),
    [
        (0x0C, 146, "key_speed", 30),
        (0x0D, 102, "notch_filter", 102),
        (0x0F, 104, "break_in_delay", 104),
        (0x14, 105, "drive_gain", 105),
    ],
)
def test_update_radio_state_cmd14_global_dsp_levels(
    radio_with_state: IcomRadio,
    sub: int,
    raw: int,
    field: str,
    expected: int,
) -> None:
    rs = radio_with_state._radio_state
    frame = _make_frame(cmd=0x14, sub=sub, data=_bcd2(raw))
    radio_with_state._civ_runtime._update_radio_state_from_frame(frame)
    assert getattr(rs, field) == expected


@pytest.mark.parametrize(  # type: ignore[untyped-decorator]
    ("sub", "raw", "field", "expected"),
    [
        # cw_pitch raw level 128 → 600 Hz via the non-linear set_cw_pitch map.
        (0x09, 128, "cw_pitch", 600),
        (0x0B, 101, "mic_gain", 101),
        (0x0E, 103, "compressor_level", 103),
        (0x15, 106, "monitor_gain", 106),
        # VOX gain trio promoted to neutral observations (MOR-459); device scale.
        (0x16, 107, "vox_gain", 107),
        (0x17, 108, "anti_vox_gain", 108),
    ],
)
def test_update_radio_state_cmd14_global_dsp_levels_observation_backed(
    radio_with_state: IcomRadio,
    sub: int,
    raw: int,
    field: str,
    expected: int,
) -> None:
    """MOR-437/MOR-459: cw_pitch/mic_gain/compressor_level/monitor_gain and the
    VOX gain pair (vox_gain/anti_vox_gain) are observation-backed; the legacy
    global RadioState mirror was removed.

    cw_pitch in particular asserts the exact non-linear raw→Hz mapping
    (level 128 → 600 Hz) the mirror and ``set_cw_pitch`` use.
    """
    rs = radio_with_state._radio_state
    frame = _make_frame(cmd=0x14, sub=sub, data=_bcd2(raw))
    radio_with_state._civ_runtime._update_state_cache_from_frame(frame)
    assert getattr(rs, field) == 0
    store_field = radio_with_state._state_store.snapshot().field(
        f"global.operator_controls.{field}"
    )
    assert store_field.value == expected


def test_update_radio_state_cmd11_attenuator(radio_with_state: IcomRadio) -> None:
    """cmd 0x11 attenuator is observation-backed (MOR-437)."""
    rs = radio_with_state._radio_state
    frame = _make_frame(cmd=0x11, data=bytes([0x18]))  # 0x18 BCD = 18 dB
    radio_with_state._civ_runtime._update_state_cache_from_frame(frame)
    # Legacy mirror removed from _handle_11; StateStore is the source of truth.
    assert rs.main.att == 0
    field = radio_with_state._state_store.snapshot().field(
        "receiver.0.operator_controls.att"
    )
    assert field.value == 18


def test_update_radio_state_cmd12_antenna_ant1_mirror_removed(
    radio_with_state: IcomRadio,
) -> None:
    """MOR-462: cmd 0x12 sub 0x00 mirror removed; ``_handle_12`` is a no-op.

    The mirror path (``_update_radio_state_from_frame`` → ``_handle_12``) no
    longer writes ``RadioState``; the fields stay at their defaults. The
    StateStore observation pipeline is the source of truth (covered by
    ``test_update_radio_state_cmd12_tx_antenna_observation_backed``).
    """
    rs = radio_with_state._radio_state
    frame = _make_frame(cmd=0x12, sub=0x00, data=bytes([0x01]))
    radio_with_state._civ_runtime._update_radio_state_from_frame(frame)
    assert rs.tx_antenna == 1
    assert rs.rx_antenna_1 is False


def test_update_radio_state_cmd12_antenna_ant2_mirror_removed(
    radio_with_state: IcomRadio,
) -> None:
    """MOR-462: cmd 0x12 sub 0x01 mirror removed; ``_handle_12`` is a no-op.

    The mirror path no longer writes ``RadioState``: ``tx_antenna`` stays at its
    default 1 (not 2) and ``rx_antenna_2`` stays ``False`` even though the frame
    selects ANT2 with RX-ANT ON. The StateStore observation pipeline carries the
    real values.
    """
    rs = radio_with_state._radio_state
    frame = _make_frame(cmd=0x12, sub=0x01, data=bytes([0x01]))
    radio_with_state._civ_runtime._update_radio_state_from_frame(frame)
    assert rs.tx_antenna == 1
    assert rs.rx_antenna_2 is False


def test_update_radio_state_cmd16_preamp(radio_with_state: IcomRadio) -> None:
    """cmd 0x16 sub 0x02 preamp is observation-backed (MOR-437)."""
    rs = radio_with_state._radio_state
    frame = _make_frame(cmd=0x16, sub=0x02, data=bytes([0x01]))
    radio_with_state._civ_runtime._update_state_cache_from_frame(frame)
    # Legacy mirror removed: preamp lands in the StateStore as a raw byte value.
    assert rs.main.preamp == 0
    field = radio_with_state._state_store.snapshot().field(
        "receiver.0.operator_controls.preamp"
    )
    assert field.value == 1


def test_update_radio_state_cmd16_nb(radio_with_state: IcomRadio) -> None:
    """cmd 0x16 sub 0x22 updates noise blanker."""
    rs = radio_with_state._radio_state
    frame = _make_frame(cmd=0x16, sub=0x22, data=bytes([0x01]))
    radio_with_state._civ_runtime._update_radio_state_from_frame(frame)
    assert rs.main.nb is True


def test_update_radio_state_cmd16_nr(radio_with_state: IcomRadio) -> None:
    """cmd 0x16 sub 0x40 updates noise reduction."""
    rs = radio_with_state._radio_state
    frame = _make_frame(cmd=0x16, sub=0x40, data=bytes([0x01]))
    radio_with_state._civ_runtime._update_radio_state_from_frame(frame)
    assert rs.main.nr is True


def test_update_radio_state_cmd16_digisel(radio_with_state: IcomRadio) -> None:
    """cmd 0x16 sub 0x4E updates DIGI-SEL."""
    rs = radio_with_state._radio_state
    frame = _make_frame(cmd=0x16, sub=0x4E, data=bytes([0x01]))
    radio_with_state._civ_runtime._update_radio_state_from_frame(frame)
    assert rs.main.digisel is True


def test_update_radio_state_cmd16_ipplus(radio_with_state: IcomRadio) -> None:
    """cmd 0x16 sub 0x65 updates IP+ (line 572-573)."""
    rs = radio_with_state._radio_state
    frame = _make_frame(cmd=0x16, sub=0x65, data=bytes([0x01]))
    radio_with_state._civ_runtime._update_radio_state_from_frame(frame)
    assert rs.main.ipplus is True


@pytest.mark.parametrize(  # type: ignore[untyped-decorator]
    ("cmd", "sub", "data", "receiver", "target", "field", "expected"),
    [
        (0x15, 0x07, b"\x01", None, "radio", "overflow", True),
        # 0x15/0x01 + 0x15/0x05 s_meter_sql_open promoted to the neutral ``dcd``
        # receiver toggle (MOR-466); 0x41 auto_notch, 0x44 compressor_on,
        # 0x45 monitor_on, 0x46 vox_on, 0x48 manual_notch (MOR-437); 0x12 agc +
        # 0x1A/0x04 agc_time_constant (BE-2); and 0x32 audio_peak_filter +
        # 0x4F twin_peak_filter (MOR-452) are now observation-backed — the legacy
        # RadioState mirror was removed, so they no longer belong here.
        (0x16, 0x47, b"\x02", None, "radio", "break_in", 2),
        (0x16, 0x50, b"\x01", None, "radio", "dial_lock", True),
        (0x16, 0x56, b"\x01", 0x01, "sub", "filter_shape", 1),
        (0x16, 0x58, b"\x02", None, "radio", "ssb_tx_bandwidth", 2),
    ],
)
def test_update_radio_state_operator_toggle_family(
    radio_with_state: IcomRadio,
    cmd: int,
    sub: int,
    data: bytes,
    receiver: int | None,
    target: str,
    field: str,
    expected: object,
) -> None:
    rs = radio_with_state._radio_state
    frame = _make_frame(cmd=cmd, sub=sub, data=data, receiver=receiver)
    radio_with_state._civ_runtime._update_radio_state_from_frame(frame)

    owner = {"radio": rs, "sub": rs.sub}[target]
    assert getattr(owner, field) == expected


def test_update_radio_state_cmd16_agc_observation_backed(
    radio_with_state: IcomRadio,
) -> None:
    """MOR-437: cmd 0x16/0x12 agc mirror removed; StateStore is source of truth."""
    rs = radio_with_state._radio_state
    frame = _make_frame(cmd=0x16, sub=0x12, data=b"\x03", receiver=0x01)
    radio_with_state._civ_runtime._update_state_cache_from_frame(frame)
    # BCD-nibble decode of 0x03 == 3, matching the removed legacy mirror.
    assert rs.sub.agc == 0
    field = radio_with_state._state_store.snapshot().field(
        "receiver.1.operator_controls.agc"
    )
    assert field.value == 3


def test_update_radio_state_cmd16_audio_peak_filter_observation_backed(
    radio_with_state: IcomRadio,
) -> None:
    """MOR-452: cmd 0x16/0x32 mirror removed; StateStore is source of truth."""
    rs = radio_with_state._radio_state
    frame = _make_frame(cmd=0x16, sub=0x32, data=b"\x02", receiver=0x01)
    radio_with_state._civ_runtime._update_state_cache_from_frame(frame)
    # BCD-nibble decode of 0x02 == 2 (MID), matching the removed legacy mirror.
    assert rs.sub.audio_peak_filter == 0
    field = radio_with_state._state_store.snapshot().field(
        "receiver.1.operator_controls.audio_peak_filter"
    )
    assert field.value == 2


def test_update_radio_state_cmd16_twin_peak_filter_observation_backed(
    radio_with_state: IcomRadio,
) -> None:
    """MOR-452: cmd 0x16/0x4F mirror removed; StateStore is source of truth."""
    rs = radio_with_state._radio_state
    frame = _make_frame(cmd=0x16, sub=0x4F, data=b"\x01", receiver=0x01)
    radio_with_state._civ_runtime._update_state_cache_from_frame(frame)
    # Legacy ReceiverState mirror stays at its default; the store carries truth.
    assert rs.sub.twin_peak_filter is False
    field = radio_with_state._state_store.snapshot().field(
        "receiver.1.operator_toggles.twin_peak_filter"
    )
    assert field.value is True


@pytest.mark.parametrize(  # type: ignore[untyped-decorator]
    ("sub", "field"),
    [(0x42, "repeater_tone"), (0x43, "repeater_tsql")],
)
def test_update_radio_state_cmd16_repeater_tone_observation_backed(
    radio_with_state: IcomRadio,
    sub: int,
    field: str,
) -> None:
    """MOR-451: cmd 0x16/0x42-0x43 mirror removed; StateStore is source of truth."""
    rs = radio_with_state._radio_state
    frame = _make_frame(cmd=0x16, sub=sub, data=b"\x01", receiver=0x01)
    radio_with_state._civ_runtime._update_state_cache_from_frame(frame)
    # Legacy ReceiverState mirror stays at its default; the store carries truth.
    assert getattr(rs.sub, field) is False
    store_field = radio_with_state._state_store.snapshot().field(
        f"receiver.1.operator_toggles.{field}"
    )
    assert store_field.value is True


@pytest.mark.parametrize(  # type: ignore[untyped-decorator]
    ("sub", "field"),
    [(0x00, "tone_freq"), (0x01, "tsql_freq")],
)
def test_update_radio_state_cmd1b_tone_freq_observation_backed(
    radio_with_state: IcomRadio,
    sub: int,
    field: str,
) -> None:
    """MOR-451: cmd 0x1B/0x00-0x01 mirror removed; StateStore is source of truth."""
    rs = radio_with_state._radio_state
    frame = _make_frame(cmd=0x1B, sub=sub, data=_encode_tone_freq(88.5), receiver=0x01)
    radio_with_state._civ_runtime._update_state_cache_from_frame(frame)
    # Legacy ReceiverState mirror stays at its default 0; the store carries truth.
    assert getattr(rs.sub, field) == 0
    store_field = radio_with_state._state_store.snapshot().field(
        f"receiver.1.operator_controls.{field}"
    )
    assert store_field.value == 8850


def test_update_radio_state_cmd1a_agc_time_constant_observation_backed(
    radio_with_state: IcomRadio,
) -> None:
    """MOR-437: cmd 0x1A/0x04 agc_time_constant is observation-backed."""
    rs = radio_with_state._radio_state
    frame = _make_frame(cmd=0x1A, sub=0x04, data=b"\x13", receiver=0x01)
    radio_with_state._civ_runtime._update_state_cache_from_frame(frame)
    # 1-byte BCD decode of 0x13 == 13, matching the removed legacy mirror.
    assert rs.sub.agc_time_constant == 0
    field = radio_with_state._state_store.snapshot().field(
        "receiver.1.operator_controls.agc_time_constant"
    )
    assert field.value == 13


def test_update_radio_state_cmd16_via_data_sub(radio_with_state: IcomRadio) -> None:
    """cmd 0x16 with sub=0x00 reads sub-code from data[0] (observation path)."""
    rs = radio_with_state._radio_state
    # sub=None, data[0]=0x02 (preamp), data[1]=0x01
    frame = _make_frame(cmd=0x16, sub=None, data=bytes([0x02, 0x01]))
    radio_with_state._civ_runtime._update_state_cache_from_frame(frame)
    # MOR-437: preamp mirror removed; the embedded-sub frame still observes it.
    assert rs.main.preamp == 0
    field = radio_with_state._state_store.snapshot().field(
        "receiver.0.operator_controls.preamp"
    )
    assert field.value == 1


def test_update_radio_state_cmd1a_sub03_ignored(radio_with_state: IcomRadio) -> None:
    """cmd 0x1A sub 0x03 (filter width code) is intentionally ignored (lines 576-581)."""
    rs = radio_with_state._radio_state
    old_filter = rs.main.filter
    frame = _make_frame(cmd=0x1A, sub=0x03, data=bytes([0x34]))
    radio_with_state._civ_runtime._update_radio_state_from_frame(frame)
    assert rs.main.filter == old_filter  # unchanged


def test_update_radio_state_cmd1a_sub06_data_mode(radio_with_state: IcomRadio) -> None:
    """cmd 0x1A sub 0x06 data_mode is observation-backed (MOR-437)."""
    rs = radio_with_state._radio_state
    frame = _make_frame(cmd=0x1A, sub=0x06, data=bytes([0x02]))
    radio_with_state._civ_runtime._update_state_cache_from_frame(frame)
    # Legacy mirror removed: data_mode lands in the active-slot StateStore path.
    assert rs.main.data_mode == 0
    field = radio_with_state._state_store.snapshot().field(
        "receiver.0.active.freq_mode.data_mode"
    )
    assert field.value == 2


@pytest.mark.parametrize(  # type: ignore[untyped-decorator]
    ("data", "field", "expected"),
    [
        (b"\x00\x70\x05\x11", "ref_adjust", 511),
        (b"\x02\x28\x45", "dash_ratio", 45),
        (b"\x02\x90\x09", "nb_depth", 9),
        (b"\x02\x91\x02\x55", "nb_width", 255),
    ],
)
def test_update_radio_state_cmd1a_ctl_mem_dsp_levels(
    radio_with_state: IcomRadio,
    data: bytes,
    field: str,
    expected: int,
) -> None:
    rs = radio_with_state._radio_state
    frame = _make_frame(cmd=0x1A, sub=0x05, data=data)
    radio_with_state._civ_runtime._update_radio_state_from_frame(frame)
    assert getattr(rs, field) == expected


def test_update_radio_state_cmd1a_vox_delay_observation_backed(
    radio_with_state: IcomRadio,
) -> None:
    """MOR-459: cmd 0x1A/0x05 0x02 0x92 vox_delay mirror removed; store is truth.

    VOX hang delay is promoted to a neutral global operator-control int; the
    legacy ``RadioState.vox_delay`` mirror is no longer written and stays at its
    default 0 while the StateStore carries the decoded value.
    """
    rs = radio_with_state._radio_state
    frame = _make_frame(cmd=0x1A, sub=0x05, data=b"\x02\x92\x12")
    radio_with_state._civ_runtime._update_state_cache_from_frame(frame)
    assert rs.vox_delay == 0
    store_field = radio_with_state._state_store.snapshot().field(
        "global.operator_controls.vox_delay"
    )
    assert store_field.value == 12


def test_update_radio_state_cmd10_tuning_step_observation_backed(
    radio_with_state: IcomRadio,
) -> None:
    """MOR-461: cmd 0x10 tuning_step mirror removed; the StateStore is truth.

    The tuning step is a device step *index* (0-8, BCD byte 0x05 → 5), NOT Hz.
    It is promoted to a neutral global slow-state int; the legacy
    ``RadioState.tuning_step`` mirror is no longer written and stays at its
    default 0 while the StateStore carries the decoded value. The legacy
    ``tuning_step_changed`` notify event is retained for back-compat.
    """
    rs = radio_with_state._radio_state
    frame = _make_frame(cmd=0x10, data=b"\x05")
    radio_with_state._civ_runtime._update_state_cache_from_frame(frame)
    assert rs.tuning_step == 0
    store_field = radio_with_state._state_store.snapshot().field(
        "global.slow_state.tuning_step"
    )
    assert store_field.value == 5


def test_update_radio_state_cmd12_tx_antenna_observation_backed(
    radio_with_state: IcomRadio,
) -> None:
    """MOR-462: cmd 0x12 tx_antenna mirror removed at BOTH decode sites.

    Antenna selection (sub 0x01 → ANT2) is promoted to a neutral global
    operator-control int; the legacy ``RadioState.tx_antenna`` mirror — written
    by both the inline ``_update_state_cache_from_frame`` branch and ``_handle_12``
    — is no longer written and stays at its default 1 while the StateStore
    carries the decoded value 2.
    """
    rs = radio_with_state._radio_state
    frame = _make_frame(cmd=0x12, sub=0x01, data=b"\x00")
    radio_with_state._civ_runtime._update_state_cache_from_frame(frame)
    assert rs.tx_antenna == 1
    store_field = radio_with_state._state_store.snapshot().field(
        "global.operator_controls.tx_antenna"
    )
    assert store_field.value == 2


def test_update_radio_state_cmd12_rx_antenna_observation_backed(
    radio_with_state: IcomRadio,
) -> None:
    """MOR-462: cmd 0x12 rx_antenna_1/2 mirror removed at BOTH decode sites.

    The per-connector RX-ANT toggle (0x12 0x00 data byte) is promoted to a
    neutral global slow-state bool; the legacy ``RadioState.rx_antenna_1`` mirror
    — written by both the inline ``_update_state_cache_from_frame`` branch and
    ``_handle_12`` — is no longer written and stays at its default ``False`` while
    the StateStore carries the decoded ``True``.
    """
    rs = radio_with_state._radio_state
    frame = _make_frame(cmd=0x12, sub=0x00, data=b"\x01")
    radio_with_state._civ_runtime._update_state_cache_from_frame(frame)
    assert rs.rx_antenna_1 is False
    store_field = radio_with_state._state_store.snapshot().field(
        "global.slow_state.rx_antenna_1"
    )
    assert store_field.value is True


def test_update_radio_state_cmd1a_af_mute(radio_with_state: IcomRadio) -> None:
    rs = radio_with_state._radio_state
    frame = _make_frame(cmd=0x1A, sub=0x09, data=b"\x01", receiver=0x01)
    radio_with_state._civ_runtime._update_radio_state_from_frame(frame)
    assert rs.sub.af_mute is True


def test_update_radio_state_cmd1c_ptt(radio_with_state: IcomRadio) -> None:
    """cmd 0x1C sub 0x00 updates global PTT (lines 585-588)."""
    rs = radio_with_state._radio_state
    frame = _make_frame(cmd=0x1C, sub=0x00, data=bytes([0x01]))
    radio_with_state._civ_runtime._update_radio_state_from_frame(frame)
    assert rs.ptt is True


def test_update_radio_state_cmd0f_split(radio_with_state: IcomRadio) -> None:
    """cmd 0x0F split is now observation-backed (MOR-437) → StateStore."""
    frame = _make_frame(cmd=0x0F, data=bytes([0x01]))
    radio_with_state._civ_runtime._update_state_cache_from_frame(frame)
    field = radio_with_state._state_store.snapshot().field("global.tx_state.split")
    assert field.value is True


def test_update_radio_state_cmd07_active_receiver(radio_with_state: IcomRadio) -> None:
    """cmd 0x07 sub 0xD2 updates active receiver (lines 595-608)."""
    rs = radio_with_state._radio_state
    assert rs.active == "MAIN"
    # data[0]=0xD2 (active receiver sub), data[1]=0x01 (SUB)
    frame = _make_frame(cmd=0x07, data=bytes([0xD2, 0x01]))
    radio_with_state._civ_runtime._update_radio_state_from_frame(frame)
    assert rs.active == "SUB"


def test_update_radio_state_cmd07_dual_watch(radio_with_state: IcomRadio) -> None:
    """cmd 0x07 sub 0xC2 dual watch is observation-backed (MOR-437)."""
    frame = _make_frame(cmd=0x07, data=bytes([0xC2, 0x01]))
    radio_with_state._civ_runtime._update_state_cache_from_frame(frame)
    field = radio_with_state._state_store.snapshot().field("global.tx_state.dual_watch")
    assert field.value is True


def test_update_radio_state_cmd07_active_receiver_main(
    radio_with_state: IcomRadio,
) -> None:
    """cmd 0x07 with val=0x00 sets active to MAIN."""
    rs = radio_with_state._radio_state
    rs.active = "SUB"
    frame = _make_frame(cmd=0x07, data=bytes([0xD2, 0x00]))
    radio_with_state._civ_runtime._update_radio_state_from_frame(frame)
    assert rs.active == "MAIN"


def test_update_radio_state_advanced_scope_family(
    radio_with_state: IcomRadio,
) -> None:
    """Readable scope-control responses project into RadioState.scope_controls."""
    rs = radio_with_state._radio_state

    for frame in (
        _make_frame(cmd=0x27, sub=0x12, data=b"\x01"),
        _make_frame(cmd=0x27, sub=0x13, data=b"\x01"),
        _make_frame(cmd=0x27, sub=0x14, data=b"\x00\x03"),
        _make_frame(cmd=0x27, sub=0x15, data=b"\x00" + bcd_encode(250_000)),
        _make_frame(cmd=0x27, sub=0x16, data=b"\x00\x04"),
        _make_frame(cmd=0x27, sub=0x17, data=b"\x00\x01"),
        _make_frame(cmd=0x27, sub=0x19, data=b"\x00\x10\x50\x01"),
        _make_frame(cmd=0x27, sub=0x1A, data=b"\x00\x02"),
        _make_frame(cmd=0x27, sub=0x1B, data=b"\x01"),
        _make_frame(cmd=0x27, sub=0x1C, data=b"\x00\x02"),
        _make_frame(cmd=0x27, sub=0x1D, data=b"\x00\x01"),
        _make_frame(
            cmd=0x27,
            sub=0x1E,
            data=b"\x06\x04" + bcd_encode(14_000_000) + bcd_encode(14_350_000),
        ),
        _make_frame(cmd=0x27, sub=0x1F, data=b"\x01\x02"),
    ):
        radio_with_state._civ_runtime._update_radio_state_from_frame(frame)

    assert rs.scope_controls.receiver == 1
    assert rs.scope_controls.dual is True
    assert rs.scope_controls.mode == 3
    assert rs.scope_controls.span == 6
    assert rs.scope_controls.edge == 4
    assert rs.scope_controls.hold is True
    assert rs.scope_controls.ref_db == -10.5
    assert rs.scope_controls.speed == 2
    assert rs.scope_controls.during_tx is True
    assert rs.scope_controls.center_type == 2
    assert rs.scope_controls.vbw_narrow is True
    assert rs.scope_controls.fixed_edge.range_index == 6
    assert rs.scope_controls.fixed_edge.edge == 4
    assert rs.scope_controls.fixed_edge.start_hz == 14_000_000
    assert rs.scope_controls.fixed_edge.end_hz == 14_350_000
    assert rs.scope_controls.rbw == 2


def test_civ_expects_response_scope_get(
    radio_with_state: IcomRadio,
) -> None:
    """Scope GET (empty data) expects a response."""
    frame = _make_frame(cmd=0x27, sub=0x14, data=b"")
    assert radio_with_state._civ_runtime._civ_expects_response(frame) is True


def test_civ_expects_response_scope_set(
    radio_with_state: IcomRadio,
) -> None:
    """Scope SET (non-empty data) does not expect a data response."""
    frame = _make_frame(cmd=0x27, sub=0x14, data=b"\x00\x03")
    assert radio_with_state._civ_runtime._civ_expects_response(frame) is False

    # Single-byte SET (scope_on 0x27 0x10 0x01)
    frame = _make_frame(cmd=0x27, sub=0x10, data=b"\x01")
    assert radio_with_state._civ_runtime._civ_expects_response(frame) is False


def test_update_radio_state_exception_suppressed(radio_with_state: IcomRadio) -> None:
    """Exception in _update_radio_state_from_frame is suppressed (line 621-622)."""
    # RadioState uses slots=True, so replace the whole object with a MagicMock
    mock_state = MagicMock()
    mock_state.receiver = MagicMock(side_effect=RuntimeError("oops"))
    radio_with_state._radio_state = mock_state
    frame = _make_frame(cmd=0x03, data=bcd_encode(14_000_000))
    radio_with_state._civ_runtime._update_radio_state_from_frame(
        frame
    )  # should not raise


def test_update_radio_state_with_receiver_field_set(
    radio_with_state: IcomRadio,
) -> None:
    """When frame.receiver is not None, uses MAIN/SUB based on receiver byte (lines 473-476)."""
    rs = radio_with_state._radio_state
    freq_data = bcd_encode(7_000_000)
    # receiver=0x01 means SUB
    frame = _make_frame(cmd=0x03, data=freq_data, receiver=0x01)
    radio_with_state._civ_runtime._update_radio_state_from_frame(frame)
    assert rs.sub.freq == 7_000_000


def test_update_radio_state_returns_when_no_radio_state(radio: IcomRadio) -> None:
    """When _radio_state is None (not set), method returns immediately (line 469)."""
    radio._radio_state = None
    frame = _make_frame(cmd=0x03, data=bcd_encode(14_000_000))
    radio._civ_runtime._update_radio_state_from_frame(frame)  # should not raise


# ---------------------------------------------------------------------------
# _notify_change with callback (lines 628-632)
# ---------------------------------------------------------------------------


def test_notify_change_calls_callback(radio: IcomRadio) -> None:
    """_notify_change invokes _on_state_change callback (lines 628-632)."""
    calls: list[tuple[str, dict[str, object]]] = []

    def my_callback(event_name: str, data: dict[str, object]) -> None:
        calls.append((event_name, data))

    radio._on_state_change = my_callback
    radio._civ_runtime._notify_change("test_event", {"key": "value"})
    assert calls == [("test_event", {"key": "value"})]


def test_notify_change_callback_exception_suppressed(radio: IcomRadio) -> None:
    """Exception in callback is suppressed (not propagated)."""

    def failing_callback(event_name: str, data: dict[str, object]) -> None:
        raise RuntimeError("callback error")

    radio._on_state_change = failing_callback
    radio._civ_runtime._notify_change("test_event", {})  # should not raise


def test_notify_change_no_callback_debug_log(radio: IcomRadio) -> None:
    """When no callback set, logs debug message (line 634)."""
    radio._on_state_change = None
    radio._civ_runtime._notify_change("test_event", {})  # should not raise


# ---------------------------------------------------------------------------
# _publish_scope_frame — queue full (lines 640-643)
# ---------------------------------------------------------------------------


def test_publish_scope_frame_drops_oldest_when_full(radio: IcomRadio) -> None:
    """When scope_frame_queue is full, oldest frame is dropped (lines 640-643)."""
    # Fill the queue to capacity
    dummy_frame = ScopeFrame(
        receiver=0,
        mode=1,
        start_freq_hz=14_000_000,
        end_freq_hz=14_350_000,
        pixels=bytes([50] * 10),
        out_of_range=False,
    )
    while not radio._scope_frame_queue.full():
        radio._scope_frame_queue.put_nowait(dummy_frame)

    # Now publish a new frame — should drop oldest and add new
    radio._civ_runtime._publish_scope_frame(dummy_frame)
    # Queue should still be at max
    assert radio._scope_frame_queue.full()


def test_publish_scope_frame_invokes_callback(radio: IcomRadio) -> None:
    """_publish_scope_frame invokes the scope callback if set."""
    received = []
    radio._scope_callback = lambda f: received.append(f)

    dummy_frame = ScopeFrame(
        receiver=0,
        mode=1,
        start_freq_hz=14_000_000,
        end_freq_hz=14_350_000,
        pixels=bytes([50] * 10),
        out_of_range=False,
    )
    radio._civ_runtime._publish_scope_frame(dummy_frame)
    assert received == [dummy_frame]


# ---------------------------------------------------------------------------
# _publish_civ_event — queue full (lines 655-658)
# ---------------------------------------------------------------------------


def test_publish_civ_event_drops_oldest_when_full(radio: IcomRadio) -> None:
    """When civ_event_queue is full, oldest event is dropped (lines 655-658)."""
    from rigplane.civ import CivEvent, CivEventType

    event = CivEvent(type=CivEventType.ACK, frame=None)

    # Fill the queue
    while not radio._civ_event_queue.full():
        radio._civ_event_queue.put_nowait(event)

    # Publish one more — should succeed by dropping oldest
    radio._civ_runtime._publish_civ_event(event)
    assert radio._civ_event_queue.full()


# ---------------------------------------------------------------------------
# _start_civ_worker — commander already exists (line 726)
# ---------------------------------------------------------------------------


async def test_start_civ_worker_reuses_existing_commander(radio: IcomRadio) -> None:
    """_start_civ_worker reuses existing commander if already created (line 726)."""
    from rigplane.commander import IcomCommander

    # Pre-create a commander
    mock_commander = MagicMock(spec=IcomCommander)
    radio._commander = mock_commander

    with patch("rigplane._civ_rx.IcomCommander") as mock_cls:
        radio._civ_runtime.start_worker()
        mock_cls.assert_not_called()  # Commander should NOT be re-created


# ---------------------------------------------------------------------------
# _drain_ack_sinks_before_blocking (lines 759-768)
# ---------------------------------------------------------------------------


async def test_drain_ack_sinks_returns_early_when_no_sinks(radio: IcomRadio) -> None:
    """Returns immediately when ack_sink_count == 0."""
    # ack_sink_count is a read-only property; must patch via PropertyMock on the class
    tracker_type = type(radio._civ_request_tracker)
    with patch.object(
        tracker_type, "ack_sink_count", new_callable=PropertyMock, return_value=0
    ):
        # Should return instantly without any sleeping
        await radio._civ_runtime._drain_ack_sinks_before_blocking()


async def test_drain_ack_sinks_drains_and_drops(radio: IcomRadio) -> None:
    """Drains ack sinks and calls drop_ack_sinks if time runs out (lines 759-768)."""
    # ack_sink_count is a read-only property; must patch via PropertyMock on the class
    tracker_type = type(radio._civ_request_tracker)
    radio._civ_request_tracker.drop_ack_sinks = MagicMock(return_value=2)
    radio._civ_ack_sink_grace = 0.001  # very short grace period

    with patch.object(
        tracker_type, "ack_sink_count", new_callable=PropertyMock, return_value=2
    ):
        await radio._civ_runtime._drain_ack_sinks_before_blocking()

    radio._civ_request_tracker.drop_ack_sinks.assert_called_once()


# ---------------------------------------------------------------------------
# _check_connected (line 687-688)
# ---------------------------------------------------------------------------


def test_check_connected_raises_when_not_connected(radio: IcomRadio) -> None:
    """_check_connected raises when _connected is False."""
    radio._connected = False
    with pytest.raises(ConnectionError, match="Not connected to radio"):
        radio._civ_runtime._check_connected()


def test_check_connected_raises_when_civ_transport_none(radio: IcomRadio) -> None:
    """_check_connected raises when _civ_transport is None."""
    radio._civ_transport = None
    with pytest.raises(ConnectionError, match="Not connected to radio"):
        radio._civ_runtime._check_connected()


# ---------------------------------------------------------------------------
# Transceiver status family (#136) — state projection
# ---------------------------------------------------------------------------


def test_update_radio_state_tuner_status(radio_with_state: IcomRadio) -> None:
    """Tuner/ATU status (0x1C 0x01) is observation-backed (MOR-437)."""
    frame = CivFrame(0xE0, 0x98, 0x1C, 0x01, b"\x02")
    radio_with_state._civ_runtime._update_state_cache_from_frame(frame)
    field = radio_with_state._state_store.snapshot().field(
        "global.operator_controls.tuner_status"
    )
    assert field.value == 2


def test_update_radio_state_tx_freq_monitor(radio_with_state: IcomRadio) -> None:
    """TX freq monitor (0x1C 0x03) is observation-backed (MOR-437)."""
    frame = CivFrame(0xE0, 0x98, 0x1C, 0x03, b"\x01")
    radio_with_state._civ_runtime._update_state_cache_from_frame(frame)
    field = radio_with_state._state_store.snapshot().field(
        "global.tx_state.tx_freq_monitor"
    )
    assert field.value is True


def test_update_radio_state_rit_frequency(radio_with_state: IcomRadio) -> None:
    """RIT frequency (0x21 0x00) → RadioState.rit_freq."""
    # 150 Hz positive: d0=0x50, d1=0x01, sign=0x00
    frame = CivFrame(0xE0, 0x98, 0x21, 0x00, b"\x50\x01\x00")
    radio_with_state._civ_runtime._update_radio_state_from_frame(frame)
    assert radio_with_state._radio_state.rit_freq == 150


def test_update_radio_state_rit_frequency_negative(radio_with_state: IcomRadio) -> None:
    """RIT frequency negative (0x21 0x00) → RadioState.rit_freq."""
    frame = CivFrame(0xE0, 0x98, 0x21, 0x00, b"\x00\x02\x01")
    radio_with_state._civ_runtime._update_radio_state_from_frame(frame)
    assert radio_with_state._radio_state.rit_freq == -200


def test_update_radio_state_rit_status(radio_with_state: IcomRadio) -> None:
    """RIT status (0x21 0x01) → RadioState.rit_on."""
    frame = CivFrame(0xE0, 0x98, 0x21, 0x01, b"\x01")
    radio_with_state._civ_runtime._update_radio_state_from_frame(frame)
    assert radio_with_state._radio_state.rit_on is True


def test_update_radio_state_rit_tx_status(radio_with_state: IcomRadio) -> None:
    """RIT TX status (0x21 0x02) → RadioState.rit_tx."""
    frame = CivFrame(0xE0, 0x98, 0x21, 0x02, b"\x01")
    radio_with_state._civ_runtime._update_radio_state_from_frame(frame)
    assert radio_with_state._radio_state.rit_tx is True


def test_update_radio_state_comp_meter_observation_backed(
    radio_with_state: IcomRadio,
) -> None:
    """MOR-460: comp meter (0x15 0x14) mirror removed; the StateStore is truth.

    The PA compression meter is promoted to ``global.meters.comp``; the legacy
    ``RadioState.comp_meter`` mirror is no longer written and stays at its
    default 0 while the StateStore carries the decoded value.
    """
    rs = radio_with_state._radio_state
    # 42 BCD: 0x00 0x42
    frame = CivFrame(0xE0, 0x98, 0x15, 0x14, b"\x00\x42")
    radio_with_state._civ_runtime._update_state_cache_from_frame(frame)
    assert rs.comp_meter == 0
    field = radio_with_state._state_store.snapshot().field("global.meters.comp")
    assert field.value == 42


def test_update_radio_state_vd_meter_observation_backed(
    radio_with_state: IcomRadio,
) -> None:
    """MOR-460: vd meter (0x15 0x15) mirror removed; the StateStore is truth.

    The PA supply-voltage meter is promoted to ``global.meters.vd``; the legacy
    ``RadioState.vd_meter`` mirror is no longer written and stays at its default
    0 while the StateStore carries the decoded value.
    """
    rs = radio_with_state._radio_state
    frame = CivFrame(0xE0, 0x98, 0x15, 0x15, b"\x01\x30")
    radio_with_state._civ_runtime._update_state_cache_from_frame(frame)
    assert rs.vd_meter == 0
    field = radio_with_state._state_store.snapshot().field("global.meters.vd")
    assert field.value == 130


def test_update_radio_state_id_meter_observation_backed(
    radio_with_state: IcomRadio,
) -> None:
    """MOR-460: id meter (0x15 0x16) mirror removed; the StateStore is truth.

    The PA drain-current meter is promoted to ``global.meters.id``; the legacy
    ``RadioState.id_meter`` mirror is no longer written and stays at its default
    0 while the StateStore carries the decoded value.
    """
    rs = radio_with_state._radio_state
    frame = CivFrame(0xE0, 0x98, 0x15, 0x16, b"\x00\x55")
    radio_with_state._civ_runtime._update_state_cache_from_frame(frame)
    assert rs.id_meter == 0
    field = radio_with_state._state_store.snapshot().field("global.meters.id")
    assert field.value == 55


def test_update_radio_state_power_meter(radio_with_state: IcomRadio) -> None:
    """Power meter (0x15 0x11) → RadioState.power_meter."""
    frame = CivFrame(0xE0, 0x98, 0x15, 0x11, b"\x01\x50")
    radio_with_state._civ_runtime._update_radio_state_from_frame(frame)
    assert radio_with_state._radio_state.power_meter == 150


def test_update_radio_state_swr_meter(radio_with_state: IcomRadio) -> None:
    """SWR meter (0x15 0x12) → RadioState.swr_meter."""
    frame = CivFrame(0xE0, 0x98, 0x15, 0x12, b"\x00\x48")
    radio_with_state._civ_runtime._update_radio_state_from_frame(frame)
    assert radio_with_state._radio_state.swr_meter == 48


def test_update_radio_state_alc_meter(radio_with_state: IcomRadio) -> None:
    """ALC meter (0x15 0x13) → RadioState.alc_meter."""
    frame = CivFrame(0xE0, 0x98, 0x15, 0x13, b"\x01\x20")
    radio_with_state._civ_runtime._update_radio_state_from_frame(frame)
    assert radio_with_state._radio_state.alc_meter == 120


@pytest.mark.parametrize(  # type: ignore[untyped-decorator]
    "sub",
    [0x01, 0x05],
)
def test_update_radio_state_cmd15_dcd_observation_backed(
    radio_with_state: IcomRadio,
    sub: int,
) -> None:
    """MOR-466: cmd 0x15 sub 0x01/0x05 squelch-open mirror removed; store is truth.

    The squelch-open / DCD (RX-busy) status is promoted to the neutral
    ``receiver.<id>.operator_toggles.dcd`` bool; the legacy
    ``ReceiverState.s_meter_sql_open`` mirror is no longer written and stays at
    its default ``False`` while the StateStore carries the decoded bool. Both
    subs feed the same ``dcd`` path.
    """
    frame = CivFrame(0xE0, 0x98, 0x15, sub, b"\x01")
    radio_with_state._civ_runtime._update_state_cache_from_frame(frame)
    rs = radio_with_state._radio_state
    assert rs.receiver(rs.active).s_meter_sql_open is False
    field = radio_with_state._state_store.snapshot().field(
        "receiver.0.operator_toggles.dcd"
    )
    assert field.value is True


def test_update_radio_state_cmd15_dcd_false(radio_with_state: IcomRadio) -> None:
    """MOR-466: a zero squelch-open byte observes ``dcd`` as False."""
    frame = CivFrame(0xE0, 0x98, 0x15, 0x05, b"\x00")
    radio_with_state._civ_runtime._update_state_cache_from_frame(frame)
    field = radio_with_state._state_store.snapshot().field(
        "receiver.0.operator_toggles.dcd"
    )
    assert field.value is False
