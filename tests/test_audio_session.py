"""AudioSession skeleton tests (MOR-576, ADR §3.2/§3.3 — epic MOR-562 step 8).

Falsification-first: the demand-driven RX×TX state machine must

- converge demand permutations to the same final state,
- honor the backend-declared ``audio_setup_order`` (MOR-575) so the
  strict ``FakeAudioBackend`` never raises the -50-shaped OSError and the
  shared order-sensitive stubs (MOR-566) never see the silent RX kill,
- refcount RX subscriptions / TX leases (no double-start),
- stop TX BEFORE dropping RX on teardown (the MOR-574 lesson — never
  ``stop_rx`` from a TRANSMITTING transport),
- leak no demand and no bus subscription when a start fails.

The session is consumed by NOTHING in src yet (steps 9/11/12 wire it).
"""

from __future__ import annotations

import pytest
from _order_sensitive_radios import ExclusiveUsbRadio, LanLikeRadio

from rigplane.audio.backend import AudioDeviceId, AudioDeviceInfo, FakeAudioBackend
from rigplane.audio.bus import AudioBus
from rigplane.audio.lan_stream import AudioPacket
from rigplane.audio.session import AudioSession, AudioSessionState
from rigplane.core.types import AudioCodec

_PACKET = AudioPacket(ident=0x0080, send_seq=1, data=b"\x01\x00" * 160)

# ── Radio stubs ──────────────────────────────────────────────────────────────
#
# The shared MOR-566 stubs predate the MOR-575 descriptor; LanLikeRadio's
# implicit order is the ``getattr`` default ("rx_first"), the exclusive stub
# declares "atomic" here exactly as the shipping backend derivation does
# ("exclusive" → "atomic").


class _AtomicExclusiveRadio(ExclusiveUsbRadio):
    """Shared exclusive stub + the MOR-575 ``audio_setup_order`` descriptor."""

    audio_setup_order = "atomic"


class _RecordingLanRadio(LanLikeRadio):
    """LAN stub recording the state each stop call was made from (MOR-574)."""

    async def stop_rx(self) -> None:
        self.calls.append(f"stop_rx@{self.state}")
        await super().stop_rx()

    async def stop_tx(self) -> None:
        self.calls.append(f"stop_tx@{self.state}")
        await super().stop_tx()


class _FailingTxLanRadio(LanLikeRadio):
    """LAN stub whose TX arm fails once (induced start failure)."""

    fail_start_tx = True

    async def start_tx(self) -> None:
        if self.fail_start_tx:
            self.calls.append("start_tx")
            raise RuntimeError("induced TX start failure")
        await super().start_tx()


class _FailingRxLanRadio(LanLikeRadio):
    """LAN stub whose RX arm fails until repaired (induced start failure)."""

    fail_start_rx = True

    async def start_rx(
        self, callback: object, *, jitter_depth: int | None = None
    ) -> None:
        if self.fail_start_rx:
            self.calls.append("start_rx")
            raise ConnectionError("induced RX start failure")
        await super().start_rx(callback, jitter_depth=jitter_depth)


class _FailingTxAtomicRadio(_AtomicExclusiveRadio):
    """Atomic stub whose TX arm fails — the session must re-arm RX."""

    async def start_tx(self) -> None:
        self.calls.append("start_tx")
        raise RuntimeError("induced TX start failure")


_CODEC_DEV = AudioDeviceInfo(
    id=AudioDeviceId(7), name="FTX-1 USB CODEC", input_channels=2, output_channels=2
)


class _StrictExclusiveRadio:
    """Exclusive same-device radio over ``FakeAudioBackend(strict)``.

    Models the step-11 target arming on ONE physical device (MOR-531/546):
    when the TX leg comes up first it opens the single full-duplex stream
    and a later RX arm just joins it (the MOR-559 live-validated clean
    path). The naive order — TX onto an already-running RX-only stream —
    opens a second stream on the busy device and the strict fake raises
    the -50-shaped OSError, exactly like live CoreAudio.
    """

    audio_duplex_mode = "exclusive"
    audio_setup_order = "atomic"

    def __init__(self) -> None:
        self.backend = FakeAudioBackend([_CODEC_DEV], strict_device_exclusive=True)
        self.audio_bus = AudioBus(self)
        self.audio_tx_codec = AudioCodec.PCM_1CH_16BIT
        self.calls: list[str] = []
        self._rx_stream = None
        self._duplex = None
        self._rx_callback: object | None = None

    @property
    def rx_live(self) -> bool:
        if self._duplex is not None and self._duplex.running:
            return self._rx_callback is not None
        return self._rx_stream is not None and self._rx_stream.running

    @property
    def tx_live(self) -> bool:
        return self._duplex is not None and self._duplex.running

    async def start_rx(
        self, callback: object, *, jitter_depth: int | None = None
    ) -> None:
        self.calls.append("start_rx")
        self._rx_callback = callback
        if self._duplex is not None and self._duplex.running:
            return  # RX joins the running duplex stream — clean (MOR-531)
        self._rx_stream = self.backend.open_rx(_CODEC_DEV.id)
        await self._rx_stream.start(lambda frame: None)

    async def stop_rx(self) -> None:
        self.calls.append("stop_rx")
        self._rx_callback = None
        if self._rx_stream is not None:
            await self._rx_stream.stop()
            self._rx_stream = None

    async def start_tx(self) -> None:
        self.calls.append("start_tx")
        # Single full-duplex stream on the one device; with an RX-only
        # stream still open this raises the strict fake's -50 OSError.
        self._duplex = self.backend.open_duplex(_CODEC_DEV.id)
        await self._duplex.start(lambda frame: None)

    async def stop_tx(self) -> None:
        self.calls.append("stop_tx")
        if self._duplex is not None:
            await self._duplex.stop()
            self._duplex = None

    async def push_tx(self, audio_data: bytes) -> None:
        if self._duplex is None or not self._duplex.running:
            raise RuntimeError("TX not armed")
        await self._duplex.write(audio_data)


def _snapshot(session: AudioSession) -> dict[str, object]:
    return {
        "state": session.state,
        "rx_demand": session.rx_demand,
        "tx_demand": session.tx_demand,
        "bus_subscribers": session.bus.subscriber_count,
    }


# ── The strict fake's teeth (falsification baseline) ─────────────────────────


async def test_strict_fake_raises_minus_50_on_tx_over_running_rx() -> None:
    """Naive TX-onto-running-RX on the exclusive device raises -50."""
    radio = _StrictExclusiveRadio()
    await radio.start_rx(lambda p: None)
    with pytest.raises(OSError) as excinfo:
        await radio.start_tx()
    assert excinfo.value.errno == -50


# ── Skeleton shape ───────────────────────────────────────────────────────────


async def test_session_starts_idle_with_reserved_states() -> None:
    session = AudioSession(LanLikeRadio())
    assert session.state is AudioSessionState.IDLE
    assert session.rx_demand == 0
    assert session.tx_demand == 0
    # RECOVERING / FAILED are reserved members (recovery loop is step 14).
    assert AudioSessionState.RECOVERING is not None
    assert AudioSessionState.FAILED is not None


async def test_subscribe_rx_starts_rx_and_delivers_frames() -> None:
    radio = LanLikeRadio()
    session = AudioSession(radio)
    sub = await session.subscribe_rx("test-consumer")
    assert session.state is AudioSessionState.RX_ONLY
    assert radio.state == "receiving"
    assert radio.rx_callback is not None
    radio.rx_callback(_PACKET)  # type: ignore[operator]
    assert await sub.get(timeout=1.0) == _PACKET
    await sub.release()
    assert session.state is AudioSessionState.IDLE
    assert radio.state == "idle"
    assert session.bus.subscriber_count == 0


# ── Demand permutations converge ─────────────────────────────────────────────


@pytest.mark.parametrize("make_radio", [LanLikeRadio, _AtomicExclusiveRadio])
async def test_demand_permutations_converge(make_radio: type) -> None:
    """rx-then-tx and tx-then-rx both reach RX_TX with identical state."""
    snapshots = []
    for order in ("rx_then_tx", "tx_then_rx"):
        radio = make_radio()
        session = AudioSession(radio)
        if order == "rx_then_tx":
            await session.subscribe_rx("a")
            await session.acquire_tx("ptt")
        else:
            await session.acquire_tx("ptt")
            await session.subscribe_rx("a")
        snapshots.append(_snapshot(session))
    assert snapshots[0] == snapshots[1]
    assert snapshots[0]["state"] is AudioSessionState.RX_TX


async def test_acquire_tx_at_idle_defers_arming() -> None:
    """A bare TX lease at IDLE registers demand but arms nothing (no flap).

    MOR-934: ``_desired()`` is intent-gated by the OBSERVED TX leg — a bare
    ``acquire_tx`` (no RX, TX leg not yet live) defers, so the bridge's
    lease-then-RX order converges straight to RX_TX with a SINGLE start_tx
    and never flaps the TX leg (MOR-556). The lone TX leg is armed only when
    TX intent goes active (a push, or a reestablish of a session that was
    transmitting) — see TX_ONLY push / recovery tests.
    """
    radio = LanLikeRadio()
    session = AudioSession(radio)
    lease = await session.acquire_tx("ptt")
    assert session.state is AudioSessionState.IDLE
    assert session.tx_demand == 1
    assert radio.calls == []  # nothing armed — the MOR-556 trap avoided
    await session.subscribe_rx("a")
    assert session.state is AudioSessionState.RX_TX
    assert radio.calls == ["start_rx", "start_tx"]  # rx_first order, one arm
    await lease.release()


async def test_lease_push_arms_tx_only_on_full_duplex() -> None:
    """A held lease that pushes with no RX arms TX_ONLY (intent goes active).

    The digital-TX (FT8/WSJT-X over the companion) path: a bare lease defers,
    but the first ``push`` converges the lone TX leg into TX_ONLY so the frame
    reaches the radio instead of being rejected (push converges, never
    rejects). Exclusive/atomic transports keep deferring (no TX_ONLY mode)."""
    radio = LanLikeRadio()
    session = AudioSession(radio)
    lease = await session.acquire_tx("wsjtx")
    assert session.state is AudioSessionState.IDLE
    assert radio.calls == []
    await lease.push(b"\x00\x01")  # push converges → TX_ONLY
    assert session.state is AudioSessionState.TX_ONLY
    assert radio.state == "transmitting"
    assert session.bus.subscriber_count == 0  # no phantom RX
    await lease.release()
    assert session.state is AudioSessionState.IDLE


# ── audio_setup_order drives arming (descriptor-read, not hardcoded) ─────────


async def test_rx_first_order_honored_on_lan_graph() -> None:
    """LAN graph: RX must arm before TX or start_rx raises (MOR-556)."""
    radio = LanLikeRadio()  # descriptor default: "rx_first"
    session = AudioSession(radio)
    await session.subscribe_rx("a")
    await session.acquire_tx("ptt")
    assert session.state is AudioSessionState.RX_TX
    assert radio.state == "transmitting"
    assert radio.rx_callback is not None  # RX leg survived
    assert radio.calls == ["start_rx", "start_tx"]


async def test_atomic_order_honored_on_exclusive_graph() -> None:
    """Exclusive graph: TX onto running RX silently kills RX (MOR-559)."""
    radio = _AtomicExclusiveRadio()
    session = AudioSession(radio)
    await session.subscribe_rx("a")
    await session.acquire_tx("ptt")
    assert session.state is AudioSessionState.RX_TX
    assert radio.tx_running is True
    assert radio.rx_running is True  # silent kill avoided — RX re-armed
    # stop RX → arm TX → re-arm RX (the MOR-559 live-validated order)
    assert radio.calls == ["start_rx", "start_tx", "start_rx"]


@pytest.mark.parametrize("first", ["rx", "tx"])
async def test_atomic_order_no_minus_50_on_strict_fake(first: str) -> None:
    """Strict fake: the session's sequencing never opens a busy device."""
    radio = _StrictExclusiveRadio()
    session = AudioSession(radio)
    if first == "rx":
        await session.subscribe_rx("a")
        await session.acquire_tx("ptt")
    else:
        await session.acquire_tx("ptt")
        await session.subscribe_rx("a")
    assert session.state is AudioSessionState.RX_TX
    assert radio.rx_live is True
    assert radio.tx_live is True


# ── Refcounting (no double-start) ────────────────────────────────────────────


async def test_double_subscribe_rx_refcounted() -> None:
    radio = LanLikeRadio()
    session = AudioSession(radio)
    sub_a = await session.subscribe_rx("a")
    sub_b = await session.subscribe_rx("b")
    assert radio.calls.count("start_rx") == 1  # no double-start, no raise
    assert session.rx_demand == 2
    await sub_a.release()
    assert session.state is AudioSessionState.RX_ONLY
    assert radio.state == "receiving"
    await sub_b.release()
    assert session.state is AudioSessionState.IDLE
    assert radio.state == "idle"


async def test_double_acquire_tx_refcounted() -> None:
    radio = LanLikeRadio()
    session = AudioSession(radio)
    await session.subscribe_rx("a")
    lease_a = await session.acquire_tx("web")
    lease_b = await session.acquire_tx("ptt")
    assert radio.calls.count("start_tx") == 1  # no AlreadyStarted leaked
    assert session.tx_demand == 2
    await lease_a.release()
    assert session.state is AudioSessionState.RX_TX  # demand survives
    await lease_b.release()
    assert session.state is AudioSessionState.RX_ONLY
    assert radio.state == "receiving"


async def test_double_release_is_noop() -> None:
    radio = LanLikeRadio()
    session = AudioSession(radio)
    sub = await session.subscribe_rx("a")
    lease = await session.acquire_tx("ptt")
    await lease.release()
    await lease.release()
    assert session.tx_demand == 0
    await sub.release()
    await sub.release()
    assert session.rx_demand == 0
    assert session.state is AudioSessionState.IDLE


# ── Teardown ordering (the MOR-574 lesson) ───────────────────────────────────


@pytest.mark.parametrize("release_order", ["tx_then_rx", "rx_then_tx"])
async def test_teardown_stops_tx_before_rx(release_order: str) -> None:
    """RX_TX → IDLE never calls stop_rx from a TRANSMITTING transport."""
    radio = _RecordingLanRadio()
    session = AudioSession(radio)
    sub = await session.subscribe_rx("a")
    lease = await session.acquire_tx("ptt")
    if release_order == "tx_then_rx":
        await lease.release()
        await sub.release()
    else:
        await sub.release()
        await lease.release()
    assert "stop_rx@transmitting" not in radio.calls
    assert radio.state == "idle"  # no leaked RX (the conformance-suite bug)
    assert session.state is AudioSessionState.IDLE
    assert session.bus.subscriber_count == 0


async def test_rx_demand_dropping_with_live_tx_keeps_tx_only() -> None:
    """RX_TX → drop RX while TX is live converges to TX_ONLY (mode change).

    MOR-934: TX intent is active (the TX leg is up), so dropping the RX sub
    sheds RX but keeps the lone TX leg — the SSB → FT8 excursion. No phantom
    RX is resurrected. RX returning converges back to RX_TX.
    """
    radio = LanLikeRadio()
    session = AudioSession(radio)
    sub = await session.subscribe_rx("a")
    await session.acquire_tx("ptt")
    assert session.state is AudioSessionState.RX_TX
    await sub.release()  # rx → 0 while the lease is held and TX is live
    assert session.state is AudioSessionState.TX_ONLY
    assert session.tx_demand == 1
    assert session.bus.subscriber_count == 0  # no phantom RX
    assert radio.state == "transmitting"
    await session.subscribe_rx("b")  # demand convergence
    assert session.state is AudioSessionState.RX_TX
    assert radio.state == "transmitting"


# ── Start failures leak nothing ──────────────────────────────────────────────


async def test_rx_start_failure_leaks_no_subscription() -> None:
    radio = _FailingRxLanRadio()
    session = AudioSession(radio)
    with pytest.raises(RuntimeError):
        await session.subscribe_rx("a")
    assert session.rx_demand == 0
    assert session.bus.subscriber_count == 0
    assert session.state is AudioSessionState.IDLE
    # No poisoned state: repair the radio and subscribe again.
    radio.fail_start_rx = False
    await session.subscribe_rx("a")
    assert session.state is AudioSessionState.RX_ONLY


async def test_tx_start_failure_leaks_no_lease_rx_first() -> None:
    radio = _FailingTxLanRadio()
    session = AudioSession(radio)
    await session.subscribe_rx("a")
    with pytest.raises(RuntimeError):
        await session.acquire_tx("ptt")
    assert session.tx_demand == 0
    assert session.state is AudioSessionState.RX_ONLY
    assert radio.state == "receiving"  # RX untouched


async def test_tx_start_failure_atomic_rearms_rx() -> None:
    """Atomic arm failure must not strand RX down (it was stopped first)."""
    radio = _FailingTxAtomicRadio()
    session = AudioSession(radio)
    await session.subscribe_rx("a")
    with pytest.raises(RuntimeError):
        await session.acquire_tx("ptt")
    assert session.tx_demand == 0
    assert session.state is AudioSessionState.RX_ONLY
    assert radio.rx_running is True  # re-armed by the unwind
    assert radio.tx_running is False


# ── TX lease push ────────────────────────────────────────────────────────────


async def test_lease_push_delegates_and_release_guards() -> None:
    radio = _StrictExclusiveRadio()
    session = AudioSession(radio)
    await session.subscribe_rx("a")
    lease = await session.acquire_tx("bridge")
    await lease.push(b"\x01\x00" * 160)
    assert radio._duplex is not None
    assert radio._duplex.written_frames == [b"\x01\x00" * 160]
    await lease.release()
    with pytest.raises(RuntimeError):
        await lease.push(b"\x00\x00")
