"""MOR-4930 recovery through the CI-V watchdog and RX pump."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from unittest.mock import AsyncMock

import pytest
from _c24_link import Civ, Link, link_fixture  # noqa: F401 - pytest fixture
from _helpers import wrap_civ_in_udp
from test_radio_connect import ConnectMockTransport

from rigplane.audio.lan_stream import AudioNotStartedError, AudioState, AudioStream
from rigplane.commands import CONTROLLER_ADDR, build_civ_frame, parse_civ_frame
from rigplane.core import civ as tracker_module
from rigplane.core.exceptions import DiscoveryTimeout
from rigplane.runtime import _civ_rx
from rigplane.runtime.radio import CoreRadio
from rigplane.runtime.session_lifecycle import LifecycleState

pytestmark = pytest.mark.timeout(10)


async def _reboot_during_soft(link: Link, outage: float, *, flush_notice: bool) -> None:
    await link.hold_tx("ws-reboot-hold")
    supervisor = link.radio._managed_tx_runtime
    await link.clock.elapse(5.1)  # age out the existing CI-V stream
    assert link.ptt
    link.pause_soft = True
    repair = asyncio.create_task(link.radio.soft_reconnect())
    try:
        await link.clock.elapse(0.2)
        assert link.soft_discoveries == 1 and not repair.done()
        link.control.notice()
        if flush_notice:
            await link.clock.elapse(0)
        link.control.down = link.control.civ_dead = True
        await link.clock.elapse(outage)
        link.control.down = False
        writes_before_return = len(link.ptt_writes)
        assert link.ptt

        def finished() -> bool:
            # Pings come back on the old session. CI-V only returns after
            # the radio accepts a fresh login.
            if link.control.logins and link.control.civ_dead:
                link.control.civ_dead = False
                link.ports[-1].payload()
            return (
                repair.done()
                and link.radio.radio_ready
                and False in link.ptt_writes[writes_before_return:]
                and not link.recovery_state()[0]
            )

        await link.ping_until(finished)
        repair.result()
        link.assert_recovery_exits()
        assert link.radio.radio_ready, "CI-V never became ready after the reboot"
        assert False in link.ptt_writes[writes_before_return:], "owed PTT OFF missing"
        assert not link.ptt
        assert link.radio._managed_tx_runtime is supervisor
    finally:
        if not repair.done():
            repair.cancel()
        await asyncio.gather(repair, return_exceptions=True)


@pytest.mark.parametrize("outage", [1.5, 0.2], ids=["R1", "R1b"])
async def test_c24_notice_before_control_loss(link: Link, outage: float) -> None:
    await _reboot_during_soft(link, outage, flush_notice=True)


async def test_r2_ws_reboot_hold(link: Link) -> None:
    await _reboot_during_soft(link, 1.5, flush_notice=False)
    assert len(link.control.logins) == 1


async def test_r3_ping_only_after_stall(link: Link) -> None:
    original = link.ports[-1]
    await _flow_pings(link, 0.25)
    sent = await link.complete(
        link.radio.send_civ_transaction(0x03, expect="none"), budget=0.25
    )
    assert sent.status == "sent"
    await link.clock.elapse(2.71)
    assert link.radio._civ_recovering
    await link.ping_until(lambda: link.ports[-1] is not original)
    assert link.ports[-1] is not original, "ping-only port was never rebuilt"
    link.ports[-1].payload()
    await link.ping_until(lambda: not link.recovery_state()[0])
    link.assert_recovery_exits()
    assert link.radio.radio_ready


@pytest.mark.parametrize(
    ("stop_name", "task_name"),
    [("stop_data_watchdog", "_civ_data_watchdog_task"), ("stop_pump", "_civ_rx_task")],
    ids=["watchdog", "pump"],
)
async def test_r4_notice_at_join(
    link: Link, monkeypatch: pytest.MonkeyPatch, stop_name: str, task_name: str
) -> None:
    await link.hold_tx(f"join-{task_name}")
    runtime = link.radio._civ_runtime
    task = getattr(link.radio, task_name)
    assert isinstance(task, asyncio.Task)
    stop = getattr(runtime, stop_name)
    cancelling: list[int] = []

    def send_notice() -> None:
        cancelling.append(task.cancelling())
        link.control.notice()

    async def stop_at_join() -> None:
        if getattr(link.radio, task_name) is task:
            asyncio.get_running_loop().call_soon(send_notice)
        await stop()

    monkeypatch.setattr(runtime, stop_name, stop_at_join)
    await link.clock.elapse(5.1)
    await link.complete(link.radio.soft_reconnect(), budget=4)
    assert cancelling and all(cancelling)
    assert not link.control.logins
    link.ports[-1].payload()
    await link.clock.elapse(0)
    assert link.radio.radio_ready


async def test_r5_id_zero_notice_while_cached_ready(link: Link) -> None:
    await link.hold_tx("recipient-zero")
    link.ports[-1].payload()
    await link.clock.elapse(0)
    assert link.radio.radio_ready
    original = link.ports[-1]
    link.control.notice(recipient=0)
    for _ in range(28):
        original.rx_packet_count += 1
        await link.clock.elapse(0.5)
    assert len(link.control.logins) == 1
    assert link.ptt_writes[-1] is False
    assert not link.radio.radio_ready


async def test_r6_waiter_cancel_strands_recovery(link: Link) -> None:
    # follow-up: MOR-4938 (R6/R8 main-equivalent)
    await link.hold_tx("cancelled-waiter")
    lifecycle = link.radio._session_lifecycle
    link.control.silent = True
    link.control.notice()
    await link.clock.elapse(0.5)
    owner = lifecycle._recover_task
    assert owner is not None and not owner.done()
    waiter = asyncio.create_task(link.radio.soft_reconnect())
    await link.clock.elapse(0)
    assert lifecycle._recovery_waiters[waiter] is owner
    waiter.cancel()
    await asyncio.gather(waiter, return_exceptions=True)
    link.control.silent = False
    await link.clock.elapse(15)
    assert waiter.cancelled()
    assert owner.cancelled()
    assert not link.control.logins
    assert not link.radio.radio_ready
    assert link.ptt_writes == [True]
    assert link.recovery_state() == (False, False, False)
    assert lifecycle.state is LifecycleState.RECOVERING


@pytest.fixture
def audio_restarts(
    link: Link, monkeypatch: pytest.MonkeyPatch
) -> tuple[AsyncMock, AsyncMock]:
    radio = link.radio

    async def ensure_audio() -> None:
        if radio._audio_stream is None:
            radio._audio_stream = AudioStream(ConnectMockTransport())

    monkeypatch.setattr(radio, "_ensure_audio_transport", ensure_audio)
    monkeypatch.setattr(radio, "_auto_recover_audio", True)
    return (
        AsyncMock(wraps=radio.start_audio_rx_opus),
        AsyncMock(wraps=radio.start_audio_tx_opus),
    )


def _watch_audio_starts(
    radio: CoreRadio,
    monkeypatch: pytest.MonkeyPatch,
    starts: tuple[AsyncMock, AsyncMock],
) -> None:
    monkeypatch.setattr(radio, "start_audio_rx_opus", starts[0])
    monkeypatch.setattr(radio, "start_audio_tx_opus", starts[1])


async def test_r7_legacy_rx_restored_once(
    link: Link,
    monkeypatch: pytest.MonkeyPatch,
    audio_restarts: tuple[AsyncMock, AsyncMock],
) -> None:
    radio = link.radio
    frames: list[bytes] = []
    await radio.start_audio_rx_opus(frames.append, jitter_depth=0)
    callback = radio._opus_rx_user_callback
    assert callback is not None
    start_rx, start_tx = audio_restarts
    _watch_audio_starts(radio, monkeypatch, audio_restarts)
    await link.clock.elapse(5.1)
    link.pause_soft = True
    repair = asyncio.create_task(radio.soft_reconnect())
    try:
        await link.clock.elapse(0.25)
        assert link.soft_discoveries == 1 and not repair.done()
        assert radio._audio_stream is None
        link.control.notice()
        await link.clock.elapse(3)
        repair.result()
        link.ports[-1].payload()
        await link.clock.elapse(0)
        assert start_rx.await_count == 1
        assert start_rx.call_args.args[0] is callback
        start_tx.assert_not_awaited()
        assert radio._audio_stream is not None
        assert radio._audio_stream.state is AudioState.RECEIVING
        assert radio.radio_ready
    finally:
        if not repair.done():
            repair.cancel()
        await asyncio.gather(repair, return_exceptions=True)


async def test_r8_full_request_dropped_mid_soft(
    link: Link, monkeypatch: pytest.MonkeyPatch
) -> None:
    # follow-up: MOR-4938 (R6/R8 main-equivalent)
    await link.hold_tx("notice-during-soft")
    runtime = link.radio._civ_runtime
    requested = runtime.request_recovery_now
    results: list[bool] = []

    def record_request(reason: str) -> bool:
        started = requested(reason)
        results.append(started)
        return started

    monkeypatch.setattr(runtime, "request_recovery_now", record_request)
    await link.clock.elapse(5.1)
    link.pause_soft = True
    repair = asyncio.create_task(link.radio.soft_reconnect())
    try:
        await link.clock.elapse(0.25)
        assert link.soft_discoveries == 1 and not repair.done()
        link.control.notice()
        await link.clock.elapse(0)
        assert results == [False]
        assert not link.control.logins
        # A silent CI-V port lets the ordinary detector escalate on both heads.
        link.control.civ_dead = True
        await link.clock.elapse(60)
        repair.result()
        assert link.control.logins
    finally:
        if not repair.done():
            repair.cancel()
        await asyncio.gather(repair, return_exceptions=True)


@pytest.mark.parametrize("link", [False], indirect=True, ids=["never-had-data"])
async def test_r9_standby_pings_stay_quiet(link: Link) -> None:
    await link.ping_until(lambda: bool(link.soft_discoveries or link.control.logins))
    assert not link.soft_discoveries
    assert not link.control.logins
    assert not link.radio.radio_ready
    assert link.recovery_state() == (False, True, False)


@pytest.mark.parametrize("stop", ["rx", "tx"])
@pytest.mark.parametrize("retry", ["soft", "full"])
async def test_r10_stop_audio_then_retry_stays_idle(
    link: Link,
    monkeypatch: pytest.MonkeyPatch,
    audio_restarts: tuple[AsyncMock, AsyncMock],
    stop: str,
    retry: str,
) -> None:
    radio = link.radio
    if stop == "rx":
        await radio.start_audio_rx_opus(lambda _: None, jitter_depth=0)
    else:
        await radio.start_audio_tx_opus()
    _watch_audio_starts(radio, monkeypatch, audio_restarts)
    discovering = asyncio.Event()
    resume = asyncio.Event()
    connect = Civ.connect

    async def fail_once(
        port: Civ,
        host: str,
        remote_port: int,
        *,
        local_host: str | None = None,
        local_port: int = 0,
        sock: object | None = None,
    ) -> None:
        if sock is None and not discovering.is_set():
            discovering.set()
            await resume.wait()
            raise DiscoveryTimeout("failure from first SOFT discovery")
        await connect(
            port,
            host,
            remote_port,
            local_host=local_host,
            local_port=local_port,
            sock=sock,
        )

    monkeypatch.setattr(Civ, "connect", fail_once)
    await link.clock.elapse(5.1)
    repair = asyncio.create_task(radio.soft_reconnect())
    try:
        await link.clock.elapse(0.25)
        assert discovering.is_set() and radio._audio_stream is None
        if stop == "rx":
            await radio.stop_audio_rx_opus()
        else:
            await radio.stop_audio_tx_opus()
        if retry == "full":
            link.control.notice()
        resume.set()
        await link.clock.elapse(3)
        repair.result()
        link.ports[-1].payload()
        await link.clock.elapse(0)
        for start in audio_restarts:
            start.assert_not_awaited()
        assert radio._audio_stream is not None
        assert radio._audio_stream.state is AudioState.IDLE
        if stop == "tx":
            with pytest.raises(AudioNotStartedError):
                await radio.push_tx(b"example audio")
        assert not link.ptt_writes
        assert radio.radio_ready
    finally:
        if not repair.done():
            repair.cancel()
        await asyncio.gather(repair, return_exceptions=True)


@pytest.mark.parametrize("link", [False], indirect=True)
@pytest.mark.parametrize(
    "invalid", ["malformed", "echo", "wrong-recipient", "old-generation"]
)
async def test_readiness_requires_frame_from_current_radio(
    link: Link, invalid: str
) -> None:
    radio = link.radio
    port = link.ports[-1]
    last_data = radio._last_civ_data_received
    assert not radio._civ_stream_ready
    if invalid == "malformed":
        payload = b"malformed CI-V"
    elif invalid == "echo":
        payload = build_civ_frame(radio._radio_addr, CONTROLLER_ADDR, 0x03)
    elif invalid == "wrong-recipient":
        payload = build_civ_frame(0xE1, radio._radio_addr, 0xFB)
    else:
        radio._civ_runtime.advance_generation("stale source in test")
        payload = build_civ_frame(CONTROLLER_ADDR, radio._radio_addr, 0xFB)
    port.queue_response(wrap_civ_in_udp(payload))
    await link.clock.elapse(0)
    assert not radio._civ_stream_ready
    assert radio._last_civ_data_received == last_data


async def _flow_pings(link: Link, seconds: float) -> None:
    for _ in range(int(seconds * 4)):
        link.ports[-1].rx_packet_count += 1
        await link.clock.elapse(0.25)


@pytest.mark.parametrize("traffic", ["echo", "other-controller"])
async def test_rejected_frames_after_soft_do_not_prevent_full_and_ptt_off(
    link: Link, traffic: str
) -> None:
    await link.hold_tx("rejected-frames")
    radio = link.radio
    link.control.civ_dead = True
    for _ in range(60):
        if link.soft_discoveries:
            break
        await link.clock.elapse(0.25)
    assert link.soft_discoveries == 1
    assert not link.control.logins and not radio.radio_ready and link.ptt

    frame = (
        build_civ_frame(radio._radio_addr, CONTROLLER_ADDR, 0x19, sub=0)
        if traffic == "echo"
        else build_civ_frame(0xE1, radio._radio_addr, 0xFB)
    )
    packet = wrap_civ_in_udp(frame)
    for _ in range(360):
        if link.control.logins:
            break
        port = link.ports[-1]
        port.rx_packet_count += 1
        port.queue_response(packet)
        await link.clock.elapse(0.25)
    assert len(link.control.logins) == 1, "rejected CI-V frames prevented FULL"

    link.control.civ_dead = False
    link.ports[-1].payload()
    await link.ping_until(lambda: radio.radio_ready and not link.ptt)
    assert radio.radio_ready
    assert link.ptt_writes == [True, False]
    assert not link.ptt


@pytest.mark.parametrize("link", [False], indirect=True)
async def test_quiet_directed_reads_survive_transient_ping_gap(
    link: Link, monkeypatch: pytest.MonkeyPatch
) -> None:
    radio = link.radio
    original = link.ports[-1]
    send = Civ.send_tracked
    frequency = bytes.fromhex("0007401400")

    async def send_with_query_reply(
        port: Civ, data: bytes, *, is_current: Callable[[], bool] | None = None
    ) -> None:
        await send(port, data, is_current=is_current)
        payload = data[_civ_rx.CIV_HEADER_SIZE :]
        if payload.startswith(b"\xfe\xfe") and parse_civ_frame(payload).command == 0x03:
            port.queue_response(
                wrap_civ_in_udp(
                    build_civ_frame(
                        CONTROLLER_ADDR, radio._radio_addr, 0x03, data=frequency
                    )
                )
            )

    monkeypatch.setattr(Civ, "send_tracked", send_with_query_reply)
    first = await link.complete(
        radio.send_civ_transaction(0x03, expect="data"), budget=0.25
    )
    assert first.status == "response" and radio.radio_ready
    await link.clock.elapse(2.8)
    assert radio._civ_recovering
    await _flow_pings(link, 8)
    assert link.ports[-1] is original
    assert (link.soft_discoveries, len(link.control.logins)) == (0, 0)
    last = await link.complete(
        radio.send_civ_transaction(0x03, expect="data"), budget=0.25
    )
    assert last.status == "response" and radio.radio_ready


@pytest.mark.parametrize("link", [False], indirect=True)
async def test_quiet_without_unsolicited_frames_does_not_rebuild_after_ping_gap(
    link: Link,
) -> None:
    original = link.ports[-1]
    original.payload()
    await link.clock.elapse(0)
    assert link.radio.radio_ready
    await link.clock.elapse(2.8)
    assert link.radio._civ_recovering
    await _flow_pings(link, 90)
    assert (link.soft_discoveries, len(link.control.logins)) == (0, 0)
    assert link.ports[-1] is original and link.radio.connected


@pytest.mark.parametrize("link", [False], indirect=True)
async def test_live_port_delivers_delayed_reply_within_sdk_timeout(
    link: Link, monkeypatch: pytest.MonkeyPatch
) -> None:
    link.clock.scope_to(monkeypatch, tracker_module)
    radio = link.radio
    original = link.ports[-1]
    original.payload()
    await link.clock.elapse(0)
    assert radio.radio_ready
    send = Civ.send_tracked
    frequency = bytes.fromhex("0007401400")
    writes: list[float] = []
    deliveries: list[float] = []

    async def send_with_delayed_reply(
        port: Civ, data: bytes, *, is_current: Callable[[], bool] | None = None
    ) -> None:
        await send(port, data, is_current=is_current)
        payload = data[_civ_rx.CIV_HEADER_SIZE :]
        if port is not original or not payload.startswith(b"\xfe\xfe"):
            return
        if parse_civ_frame(payload).command != 0x03:
            return
        writes.append(link.clock.now)
        packet = wrap_civ_in_udp(
            build_civ_frame(CONTROLLER_ADDR, radio._radio_addr, 0x03, data=frequency)
        )

        def deliver() -> None:
            deliveries.append(link.clock.now)
            if original._udp_transport is not None:
                original.queue_response(packet)

        asyncio.get_running_loop().call_later(8.75, deliver)

    monkeypatch.setattr(Civ, "send_tracked", send_with_delayed_reply)
    await _flow_pings(link, 0.25)
    transaction = asyncio.create_task(
        radio.send_civ_transaction(0x03, expect="data", timeout=15.0)
    )
    try:
        await _flow_pings(link, 12)
        assert len(writes) == len(deliveries) == 1
        assert deliveries[0] - writes[0] == pytest.approx(8.75)
        assert transaction.done()
        assert transaction.result().status == "response"
        assert link.ports[-1] is original
    finally:
        if not transaction.done():
            transaction.cancel()
        await asyncio.gather(transaction, return_exceptions=True)
