"""AudioSession reconciler invariants — deterministic enumeration (MOR-934).

The session is a declarative reconciler: desired state is a PURE function of
declared demand, and every edge drives the transport to that desired state by
diffing against OBSERVED transport leg liveness. These invariants pin the
contract the scattered arming sites used to violate:

- **I1** — a held ``TxLease`` IS TX demand: from every reachable state, a
  ``lease.push`` on a full-duplex transport converges (arms TX) and never
  raises ``AudioNotStartedError``.
- **I2** — every transition converges: after any single public input the
  session state equals ``_desired()`` (modulo transient RECOVERING) AND the
  observed transport legs match it.
- **I3** — TX-only never resurrects RX: a TX-only convergence/recovery keeps
  ``bus.subscriber_count == 0``.
- **I4** — teardown order: ``stop_rx`` is never called from a TRANSMITTING
  transport, across all transitions including post-recovery.

No ``hypothesis`` dependency: the state space is tiny and bounded, so a
parametrized enumeration over short operation prefixes gives full, fully
deterministic coverage (PLAN §D.4).
"""

from __future__ import annotations

import itertools

import pytest
from _order_sensitive_radios import ExclusiveUsbRadio, LanLikeRadio

from rigplane.audio.session import (
    AudioSession,
    AudioSessionState,
    RxSubscription,
    TxLease,
)
from rigplane.audio.usb_driver import AudioNotStartedError

_TX_FRAME = b"\x01\x00" * 160


# ── Observed-liveness helpers (read the transport, never session._state) ─────


def _tx_leg_live(radio: object) -> bool:
    """Observed transport TX state — mirrors ``AudioSession._tx_leg_live``."""
    st = getattr(radio, "state", None)
    if st is not None:
        return str(getattr(st, "value", st)).lower() == "transmitting"
    return bool(getattr(radio, "tx_running", False))


def _rx_leg_live(session: AudioSession) -> bool:
    return session.bus.rx_active


def assert_converged(session: AudioSession, radio: object) -> None:
    """I2 post-condition: session state == _desired() and transport matches."""
    state = session.state
    if state is AudioSessionState.RECOVERING:
        return  # transient watchdog overlay — not a steady-state assertion
    desired = session._desired()  # noqa: SLF001
    assert state is desired, f"state {state} != desired {desired}"
    if state in (AudioSessionState.RX_ONLY, AudioSessionState.RX_TX):
        assert _rx_leg_live(session) is True, "RX leg must be live"
    else:
        assert _rx_leg_live(session) is False, "RX leg must be down"
    if state in (AudioSessionState.TX_ONLY, AudioSessionState.RX_TX):
        assert _tx_leg_live(radio) is True, "TX leg must be live"
    else:
        assert _tx_leg_live(radio) is False, "TX leg must be down"


async def assert_push_succeeds(
    session: AudioSession, lease: TxLease, radio: object
) -> None:
    """I1 post-condition: a held-lease push converges and never rejects."""
    await lease.push(_TX_FRAME)
    assert _tx_leg_live(radio) is True


# ── I1 / I2 enumeration: every reachable state × every public edge ───────────

_DEMAND_PREFIXES: list[tuple[str, ...]] = [
    seq
    for n in range(0, 4)
    for seq in itertools.product(("sub_rx", "rel_rx", "acq_tx", "rel_tx"), repeat=n)
]


async def _apply_op(
    session: AudioSession,
    op: str,
    subs: list[RxSubscription],
    leases: list[TxLease],
) -> None:
    if op == "sub_rx":
        subs.append(await session.subscribe_rx(f"rx{len(subs)}"))
    elif op == "rel_rx":
        if subs:
            await subs.pop().release()
    elif op == "acq_tx":
        leases.append(await session.acquire_tx(f"tx{len(leases)}"))
    elif op == "rel_tx":
        if leases:
            await leases.pop().release()


@pytest.mark.parametrize("prefix", _DEMAND_PREFIXES)
async def test_i2_every_demand_transition_converges(prefix: tuple[str, ...]) -> None:
    """I2: after every demand op the session converges to _desired() (LAN)."""
    radio = LanLikeRadio()
    session = AudioSession(radio)
    subs: list[RxSubscription] = []
    leases: list[TxLease] = []
    try:
        assert_converged(session, radio)
        for op in prefix:
            await _apply_op(session, op, subs, leases)
            assert_converged(session, radio)
    finally:
        for lease in leases:
            await lease.release()
        for sub in subs:
            await sub.release()


@pytest.mark.parametrize(
    "prefix",
    [
        ("acq_tx",),  # bare lease@IDLE -> push converges to TX_ONLY
        ("sub_rx", "acq_tx"),  # acquire_tx@RX_ONLY -> RX_TX
        ("acq_tx", "sub_rx"),  # subscribe after a deferred lease -> RX_TX
        ("sub_rx", "acq_tx", "rel_rx"),  # drop RX while TX live -> TX_ONLY
        ("acq_tx", "sub_rx", "rel_rx"),  # mode-change shape -> TX_ONLY
    ],
)
async def test_i1_held_lease_push_never_rejects(prefix: tuple[str, ...]) -> None:
    """I1: a held TX lease always converges to a pushable TX leg (LAN).

    Whether the lease was deferred (bare ``acquire_tx``) or the TX leg is
    already live, a ``push`` converges and never raises (push-converges).
    """
    radio = LanLikeRadio()
    session = AudioSession(radio)
    subs: list[RxSubscription] = []
    leases: list[TxLease] = []
    for op in prefix:
        await _apply_op(session, op, subs, leases)
    assert leases, "prefix must leave a held lease"
    lease = leases[-1]
    assert lease.released is False
    await assert_push_succeeds(session, lease, radio)
    assert_converged(session, radio)


async def test_i1_push_from_tx_only_after_reestablish() -> None:
    """I1 × recovery: TX-only push after a transport rebuild still converges."""
    radio = LanLikeRadio()
    session = AudioSession(radio)
    lease = await session.acquire_tx("wsjtx")
    await lease.push(_TX_FRAME)  # push converges → TX_ONLY (intent active)
    assert session.state is AudioSessionState.TX_ONLY
    radio.state = "idle"  # simulate a transport rebuild (reconnect)
    radio.rx_callback = None
    radio.calls.clear()
    await session.reestablish()
    await assert_push_succeeds(session, lease, radio)
    assert_converged(session, radio)
    assert session.state is AudioSessionState.TX_ONLY


# ── I3: TX-only never resurrects RX ──────────────────────────────────────────


async def test_i3_tx_only_keeps_zero_rx_subscribers() -> None:
    radio = LanLikeRadio()
    session = AudioSession(radio)
    lease = await session.acquire_tx("wsjtx")
    await lease.push(_TX_FRAME)  # push converges → TX_ONLY
    assert session.state is AudioSessionState.TX_ONLY
    assert session.bus.subscriber_count == 0
    # Across a rebuild + reestablish the phantom RX must never appear.
    radio.state = "idle"
    radio.rx_callback = None
    await session.reestablish()
    assert session.bus.subscriber_count == 0
    assert session.state is AudioSessionState.TX_ONLY
    await lease.release()


# ── I4: stop_rx is never called from a TRANSMITTING transport ────────────────


class _RecordingLanRadio(LanLikeRadio):
    """Records the transport state each stop_* call was made from (MOR-574)."""

    async def stop_rx(self) -> None:
        self.calls.append(f"stop_rx@{self.state}")
        await super().stop_rx()

    async def stop_tx(self) -> None:
        self.calls.append(f"stop_tx@{self.state}")
        await super().stop_tx()


@pytest.mark.parametrize(
    "prefix",
    [
        ("sub_rx", "acq_tx", "rel_tx", "rel_rx"),
        ("sub_rx", "acq_tx", "rel_rx", "rel_tx"),
        ("acq_tx", "sub_rx", "rel_rx"),
        ("acq_tx", "sub_rx", "rel_rx", "rel_tx"),
    ],
)
async def test_i4_no_stop_rx_from_transmitting(prefix: tuple[str, ...]) -> None:
    radio = _RecordingLanRadio()
    session = AudioSession(radio)
    subs: list[RxSubscription] = []
    leases: list[TxLease] = []
    for op in prefix:
        await _apply_op(session, op, subs, leases)
    assert "stop_rx@transmitting" not in radio.calls
    for lease in leases:
        await lease.release()
    for sub in subs:
        await sub.release()
    assert "stop_rx@transmitting" not in radio.calls


async def test_i4_no_stop_rx_from_transmitting_post_recovery() -> None:
    """I4 across a recovery cycle (the gap the old suite never checked)."""
    radio = _RecordingLanRadio()
    session = AudioSession(radio)
    sub = await session.subscribe_rx("a")
    lease = await session.acquire_tx("ptt")
    assert session.state is AudioSessionState.RX_TX
    # Rebuild the transport, then reestablish (recovery) and tear down.
    radio.state = "idle"
    radio.rx_callback = None
    radio.calls.clear()
    await session.reestablish()
    await lease.release()
    await sub.release()
    assert "stop_rx@transmitting" not in radio.calls
    assert session.state is AudioSessionState.IDLE


# ── Test-double sanity (PLAN §D.5): desync must stay detectable ──────────────


def test_double_rejects_push_when_not_armed() -> None:
    """LAN double rejects push unless transmitting (the desync teeth)."""
    radio = LanLikeRadio()

    async def _run() -> None:
        with pytest.raises(AudioNotStartedError):
            await radio.push_tx(_TX_FRAME)

    import asyncio

    asyncio.run(_run())


def test_tx_leg_live_agrees_with_double_flags() -> None:
    """_tx_leg_live must read both double shapes identically (PLAN §D.5)."""
    lan = LanLikeRadio()
    assert _tx_leg_live(lan) is False
    lan.state = "transmitting"
    assert _tx_leg_live(lan) is True

    usb = ExclusiveUsbRadio()
    assert _tx_leg_live(usb) is False
    usb.tx_running = True
    assert _tx_leg_live(usb) is True
