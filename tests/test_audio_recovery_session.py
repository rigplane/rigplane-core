"""Reconnect recovery re-establishes the SESSION's live demand (MOR-586).

Epic MOR-562 step 20, ADR §3.4 rule 4 ("one recovery loop"): on a radio
transport reconnect ``AudioRecoveryRuntime.recover`` used to replay the
LEGACY ``*_opus``/``*_pcm`` start methods from a pre-disconnect snapshot.
After the MOR-562 migration the AudioSession owns the radio RX/TX legs,
so the legacy replay re-armed the radio BEHIND the session:

- demand dropped during the outage was resurrected (a re-armed radio leg
  with no consumer draining it),
- the snapshot's stale TX leg desynced the actual transport state from
  the session's demand-derived state,
- the replay order was hardcoded rx-then-tx, ignoring the MOR-575
  ``audio_setup_order`` descriptor.

Falsification-first: the desync tests below FAIL on the legacy replay and
pass only when recovery routes through ``AudioSession.reestablish()``
(live demand, transport-declared order, under the session lock). Radios
with NO live session demand keep the legacy replay — pinned both here and
in ``tests/test_audio_recovery.py``.
"""

from __future__ import annotations

import asyncio
from unittest.mock import MagicMock

import pytest
from _audio_stream_fake import FakeAudioStream
from _order_sensitive_radios import ExclusiveUsbRadio, LanLikeRadio

from rigplane.audio import AudioPacket, AudioState
from rigplane.audio.session import AudioSession, AudioSessionState
from rigplane.audio.usb_driver import AudioAlreadyStartedError
from rigplane.runtime._audio_recovery import AudioRecoveryState
from rigplane.runtime.radio import IcomRadio

_PACKET = AudioPacket(ident=0x0080, send_seq=1, data=b"\x01\x00" * 160)


# ── Stubs / helpers ──────────────────────────────────────────────────────────


def _wipe(radio: LanLikeRadio) -> None:
    """Simulate the radio-side transport rebuild: the radio's RX callback
    and stream state are gone; the session/bus handles survive."""
    radio.state = "idle"
    radio.rx_callback = None
    radio.calls.clear()


class _RxFailsOnRearmRadio(LanLikeRadio):
    fail_rx = False

    async def start_rx(
        self, callback: object, *, jitter_depth: int | None = None
    ) -> None:
        if self.fail_rx:
            self.calls.append("start_rx")
            raise ConnectionError("induced RX re-arm failure")
        await super().start_rx(callback, jitter_depth=jitter_depth)


class _TxFailsOnRearmRadio(LanLikeRadio):
    fail_tx = False

    async def start_tx(self) -> None:
        if self.fail_tx:
            self.calls.append("start_tx")
            raise RuntimeError("induced TX re-arm failure")
        await super().start_tx()


class _StatefulFakeStream(FakeAudioStream):
    """FakeAudioStream + the LAN transition graph the snapshot/replay reads
    (MOR-556 AlreadyStarted teeth and the MOR-574 stop_rx early-return)."""

    async def start_rx(
        self, callback: object, *, jitter_depth: int | None = None
    ) -> None:
        if self.state is not AudioState.IDLE:
            raise AudioAlreadyStartedError(f"Cannot start RX in state {self.state}")
        await super().start_rx(callback, jitter_depth=jitter_depth)
        self.state = AudioState.RECEIVING

    async def stop_rx(self) -> None:
        await super().stop_rx()
        if self.state is AudioState.RECEIVING:
            self.state = AudioState.IDLE

    async def start_tx(self) -> None:
        if self.state is AudioState.TRANSMITTING:
            raise AudioAlreadyStartedError("Already transmitting")
        await super().start_tx()
        self.state = AudioState.TRANSMITTING

    async def stop_tx(self) -> None:
        await super().stop_tx()
        if self.state is AudioState.TRANSMITTING:
            self.state = (
                AudioState.RECEIVING
                if self.last_start_rx_callback is not None
                else AudioState.IDLE
            )


def _make_radio(**kwargs: object) -> IcomRadio:
    radio = IcomRadio("192.168.1.100", **kwargs)  # type: ignore[arg-type]
    radio._ctrl_transport = MagicMock()
    radio._civ_transport = MagicMock()
    radio._connected = True
    return radio


def _install_stream(radio: IcomRadio) -> _StatefulFakeStream:
    stream = _StatefulFakeStream()
    radio._audio_stream = stream  # type: ignore[assignment]
    radio._audio_transport = MagicMock()
    return stream


# ── AudioSession.reestablish — live demand, declared order, no leaks ─────────


async def test_reestablish_rearms_rx_only_from_live_demand() -> None:
    radio = LanLikeRadio()
    session = AudioSession(radio)
    sub = await session.subscribe_rx("web")
    _wipe(radio)
    assert session.bus.rx_active is True  # the stale flag the bus can't see

    await session.reestablish()

    assert radio.state == "receiving"
    # Re-armed THROUGH the session's bus — never a bypassing legacy replay.
    # (== — bound-method objects are recreated per attribute access.)
    assert radio.rx_callback == session.bus._on_opus_packet  # noqa: SLF001
    assert session.state is AudioSessionState.RX_ONLY
    assert session.bus.subscriber_count == 1  # no leaked/dropped subscription
    radio.rx_callback(_PACKET)  # type: ignore[operator]
    assert await sub.get(timeout=1.0) == _PACKET
    await session.reestablish()  # idempotent — already-live legs left alone
    assert radio.state == "receiving"
    await sub.release()
    assert session.state is AudioSessionState.IDLE


@pytest.mark.parametrize("make_radio", [LanLikeRadio, ExclusiveUsbRadio])
async def test_reestablish_rx_tx_in_transport_declared_order(
    make_radio: type,
) -> None:
    """The legacy replay hardcoded rx-then-tx; the session reads MOR-575."""
    radio = make_radio()
    session = AudioSession(radio)
    sub = await session.subscribe_rx("bridge")
    lease = await session.acquire_tx("bridge")
    if isinstance(radio, ExclusiveUsbRadio):
        radio.rx_running = False
        radio.tx_running = False
        radio.rx_callback = None
        radio.calls.clear()
        await session.reestablish()
        assert radio.calls == ["start_tx", "start_rx"]  # atomic order
        assert radio.rx_running is True  # no silent -50-shaped RX kill
        assert radio.tx_running is True
    else:
        _wipe(radio)
        await session.reestablish()
        assert radio.calls == ["start_rx", "start_tx"]  # rx_first order
        assert radio.state == "transmitting"
        assert radio.rx_callback is not None  # RX leg survived
    assert session.state is AudioSessionState.RX_TX
    await lease.release()
    await sub.release()


async def test_reestablish_with_idle_demand_arms_nothing() -> None:
    """Demand dropped during the outage stays dropped — no resurrection."""
    radio = LanLikeRadio()
    session = AudioSession(radio)
    sub = await session.subscribe_rx("web")
    await sub.release()
    radio.calls.clear()
    await session.reestablish()
    assert radio.calls == []
    assert session.state is AudioSessionState.IDLE
    assert session._watchdog_task is None  # noqa: SLF001


async def test_reestablish_returns_recovering_session_to_demand_state() -> None:
    radio = LanLikeRadio()
    session = AudioSession(radio, watchdog_interval=0.02, rx_liveness_timeout=0.06)
    sub = await session.subscribe_rx("web")
    deadline = asyncio.get_running_loop().time() + 2.0
    while (
        session.state is not AudioSessionState.RECOVERING
        and asyncio.get_running_loop().time() < deadline
    ):
        await asyncio.sleep(0.005)
    assert session.state is AudioSessionState.RECOVERING
    _wipe(radio)
    await session.reestablish()
    assert session.state is AudioSessionState.RX_ONLY
    assert session._recovering_from is None  # noqa: SLF001
    await sub.release()


async def test_reestablish_rx_failure_raises_and_keeps_demand() -> None:
    radio = _RxFailsOnRearmRadio()
    session = AudioSession(radio)
    sub = await session.subscribe_rx("web")
    _wipe(radio)
    radio.fail_rx = True
    with pytest.raises(RuntimeError):
        await session.reestablish()
    assert session.bus.rx_active is False  # reality surfaced, not masked
    assert session.rx_demand == 1  # demand preserved — next reconnect retries
    radio.fail_rx = False
    await session.reestablish()
    assert session.state is AudioSessionState.RX_ONLY
    await sub.release()


async def test_reestablish_tx_failure_settles_rx_only() -> None:
    radio = _TxFailsOnRearmRadio()
    session = AudioSession(radio)
    sub = await session.subscribe_rx("web")
    lease = await session.acquire_tx("ptt")
    _wipe(radio)
    radio.fail_tx = True
    await session.reestablish()  # no raise — same policy as the deferred arm
    assert session.state is AudioSessionState.RX_ONLY
    assert radio.state == "receiving"  # RX leg is back regardless
    assert session.tx_demand == 1  # lease kept; next demand edge retries
    await lease.release()
    await sub.release()


async def test_no_leaked_watchdog_task_across_reconnect_cycle() -> None:
    """MOR-567/MOR-581 hygiene: one watchdog task across the whole cycle."""
    radio = LanLikeRadio()
    session = AudioSession(radio)
    sub = await session.subscribe_rx("web")
    task = session._watchdog_task  # noqa: SLF001
    assert task is not None and not task.done()
    _wipe(radio)
    await session.reestablish()
    assert session._watchdog_task is task  # noqa: SLF001 — reused, not piled
    assert not task.done()
    await sub.release()
    assert session._watchdog_task is None  # noqa: SLF001
    assert task.done()  # cancelled AND awaited — nothing leaked


# ── Recovery runtime routing (the desync falsification) ──────────────────────


async def test_recover_reestablishes_live_demand_not_stale_snapshot() -> None:
    """THE MOR-586 desync: TX demand dropped during the outage.

    Legacy replay re-arms the snapshot's TX leg → the transport ends
    TRANSMITTING while the session (demand-derived) says RX_ONLY. The fix
    re-establishes from live demand: RX back via the session's bus, no TX.
    """
    radio = _make_radio()
    _install_stream(radio)
    session = radio.audio_session
    sub = await session.subscribe_rx("web")
    lease = await session.acquire_tx("ptt")
    assert session.state is AudioSessionState.RX_TX

    snapshot = radio._audio_runtime.capture_snapshot()
    assert snapshot is not None  # reconnect hooks gate recovery on this
    await lease.release()  # demand edge during the outage window
    assert session.state is AudioSessionState.RX_ONLY

    fresh = _install_stream(radio)  # transport rebuilt by the reconnect
    await radio._audio_runtime.recover(snapshot)

    assert fresh.state is AudioState.RECEIVING  # NOT TRANSMITTING (desync)
    assert fresh.start_tx_count == 0  # nobody holds a TX lease anymore
    assert session.state is AudioSessionState.RX_ONLY
    # RX re-armed through the session's bus — the same single fan-out.
    assert fresh.last_start_rx_callback == session.bus._on_opus_packet  # noqa: SLF001
    assert session.bus.subscriber_count == 1  # no leaked subscription
    await sub.release()


async def test_recover_with_all_demand_dropped_resurrects_nothing() -> None:
    radio = _make_radio()
    _install_stream(radio)
    session = radio.audio_session
    sub = await session.subscribe_rx("web")
    snapshot = radio._audio_runtime.capture_snapshot()
    assert snapshot is not None
    await sub.release()  # last consumer left during the outage

    fresh = _install_stream(radio)
    await radio._audio_runtime.recover(snapshot)

    assert fresh.start_rx_count == 0  # no re-armed leg without a consumer
    assert fresh.start_tx_count == 0
    assert session.state is AudioSessionState.IDLE


async def test_recover_without_session_demand_keeps_legacy_replay() -> None:
    """Direct legacy ``*_opus`` consumers (no session) keep the replay."""
    radio = _make_radio()
    stream = _install_stream(radio)
    legacy_cb = MagicMock()
    await radio.start_audio_rx_opus(legacy_cb, jitter_depth=3)
    assert stream.state is AudioState.RECEIVING

    snapshot = radio._audio_runtime.capture_snapshot()
    assert snapshot is not None
    fresh = _install_stream(radio)
    await radio._audio_runtime.recover(snapshot)

    assert fresh.start_rx_count == 1
    assert fresh.last_start_rx_callback is legacy_cb
    assert fresh.last_start_rx_jitter_depth == 3
    # Probing for a session must never instantiate one as a side effect.
    assert radio._audio_session is None


async def test_recover_session_path_emits_recovering_then_recovered() -> None:
    recovery_cb = MagicMock()
    radio = _make_radio(on_audio_recovery=recovery_cb)
    _install_stream(radio)
    sub = await radio.audio_session.subscribe_rx("web")
    snapshot = radio._audio_runtime.capture_snapshot()
    assert snapshot is not None

    _install_stream(radio)
    await radio._audio_runtime.recover(snapshot)

    states = [c.args[0] for c in recovery_cb.call_args_list]
    assert states == [AudioRecoveryState.RECOVERING, AudioRecoveryState.RECOVERED]
    await sub.release()


async def test_recover_session_path_emits_failed_on_dead_rx() -> None:
    class _DeadRxStream(_StatefulFakeStream):
        async def start_rx(
            self, callback: object, *, jitter_depth: int | None = None
        ) -> None:
            raise ConnectionError("audio port gone")

    recovery_cb = MagicMock()
    radio = _make_radio(on_audio_recovery=recovery_cb)
    _install_stream(radio)
    sub = await radio.audio_session.subscribe_rx("web")
    snapshot = radio._audio_runtime.capture_snapshot()
    assert snapshot is not None

    dead = _DeadRxStream()
    radio._audio_stream = dead  # type: ignore[assignment]
    await radio._audio_runtime.recover(snapshot)  # logged, never raises

    states = [c.args[0] for c in recovery_cb.call_args_list]
    assert states == [AudioRecoveryState.RECOVERING, AudioRecoveryState.FAILED]
    assert radio.audio_session.bus.rx_active is False  # reality, not masked
    await sub.release()
