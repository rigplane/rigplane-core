"""AudioSession — demand-driven RX×TX state machine (MOR-576, ADR §3.2/§3.3).

Skeleton of the desired-state component that will become the SOLE caller of
the radio's ``start_rx``/``stop_rx``/``start_tx``/``stop_tx`` (target audio
architecture ADR, ``docs/plans/2026-06-09-target-audio-architecture.md``).
Consumers declare *demand* instead of issuing transport calls:

- :meth:`AudioSession.subscribe_rx` → RX demand (delegated to the existing
  :class:`~rigplane.audio.bus.AudioBus` refcount — the bus subscription IS
  the session's RX demand; bus public API unchanged);
- :meth:`AudioSession.acquire_tx` → a refcounted :class:`TxLease`.

The session computes the desired state from ``(rx_demand>0, tx_demand>0)``
and reconciles under ONE asyncio lock, so concurrent/repeated consumer
actions can never interleave arming calls (deletes the double-start class
at the source — MOR-556/MOR-559).

State machine (ADR §3.3)::

    IDLE ⇄ RX_ONLY ⇄ RX_TX ⇄ RECOVERING        FAILED defined, not yet entered
    IDLE ⇄ TX_ONLY ⇄ RX_TX                      full-duplex tx-only (lazy arm)

RECOVERING (MOR-581, ADR §3.4, tenet T3 "no silent audio death"): a ~1 s
watchdog task — decoupled from the keep-alives — reads the bus RX heartbeat
(MOR-564); ~3 s of silence ⇒ RECOVERING + a local
:class:`AudioSessionEvent`; frames resuming return the demand-derived
state. Step 14 only SURFACES the death; step 20 (MOR-586) adds
:meth:`AudioSession.reestablish` — the radio-side reconnect re-arms the
session's LIVE demand instead of replaying the legacy snapshot.

Arming order is transport-owned via the MOR-575 descriptor
``audio_setup_order`` (read with ``getattr(radio, ..., "rx_first")``):

- ``"rx_first"`` (LAN, separate-device USB): RX up, then arm TX.
- ``"tx_first"`` / ``"atomic"`` (same-device exclusive USB): the TX leg
  must be up before RX joins the device — entering RX_TX from RX_ONLY
  stops RX, arms TX, then re-arms RX through the bus (the MOR-559
  live-validated order). Step 11 (MOR-546) moves the same-device duplex
  topology behind ``UsbAudioDriver.ensure()`` inside the backend's
  ``start_tx``; the session's sequencing here is that seam's caller.

Teardown always stops TX BEFORE dropping RX (the MOR-574 lesson): RX is
never stopped from a TRANSMITTING transport. On genuinely full-duplex
transports (LAN, ``"rx_first"``) TX MAY run without RX: a digital client
(WSJT-X/FT8 over the companion) holding only a TX lease lazily arms the TX
leg on its first ``TxLease.push`` and enters the ``TX_ONLY`` state, so its
modulation actually reaches the radio. Exclusive/atomic USB transports keep
deferring tx-only demand (their TX leg requires the co-armed duplex stream).
Leases held across an RX gap are re-armed when RX demand returns.

As-built, the session is consumed via the radio-owned singleton
``radio.audio_session`` (MOR-579) by the AudioBridge (MOR-577), the web
TX handler (MOR-580), and the reconnect/recovery path (MOR-586).
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import time
from dataclasses import dataclass
from enum import Enum
from typing import TYPE_CHECKING, Any, AsyncIterator, Callable, Literal, cast

from rigplane.audio.bus import AudioBus, AudioSubscription
from rigplane.audio.usb_driver import AudioAlreadyStartedError

if TYPE_CHECKING:
    from rigplane.audio import AudioPacket

logger = logging.getLogger(__name__)

__all__ = [
    "AudioSession",
    "AudioSessionEvent",
    "AudioSessionState",
    "RxSubscription",
    "TxLease",
]

_SetupOrder = Literal["rx_first", "tx_first", "atomic"]
_VALID_SETUP_ORDERS: frozenset[str] = frozenset({"rx_first", "tx_first", "atomic"})

# RX_TX → RX_ONLY re-arm policy seam (step 12, MOR-554): after dropping TX,
# re-arm RX via the bus for these setup orders. Defaults to ALL orders —
# today's MOR-506 unconditional post-TX re-arm semantics (the bus swallows
# the redundant re-arm on full-duplex transports, exactly like the poller
# path does). MOR-554 narrows this to {"tx_first", "atomic"} once skipping
# the re-arm on "rx_first" transports is hardware-validated.
_REARM_RX_AFTER_TX_DROP: frozenset[str] = _VALID_SETUP_ORDERS

# Health watchdog (MOR-581, ADR §3.4): cadence fully DECOUPLED from the keep-
# alive loops (~500 ms control / ~100 ms audio) — it only READS the bus
# heartbeat (MOR-564) and never touches the radio link.
WATCHDOG_INTERVAL_S: float = 1.0
#: Bus heartbeat older than this while RX should be live ⇒ RECOVERING.
RX_LIVENESS_TIMEOUT_S: float = 3.0


class AudioSessionState(Enum):
    """Session lifecycle states (ADR §3.3)."""

    IDLE = "idle"
    RX_ONLY = "rx_only"
    RX_TX = "rx_tx"
    #: TX armed with NO RX demand on the session — only valid on genuinely
    #: full-duplex transports (LAN UDP; ``audio_setup_order == "rx_first"``).
    #: A digital client (WSJT-X/FT8 over the companion) can hold a TX lease
    #: without ever subscribing the session to RX; the radio still keys via
    #: CAT and the LAN audio stream must be transitioned to TX so pushed
    #: frames are accepted instead of rejected with ``AudioNotStartedError``
    #: ("Cannot push TX in state receiving"). Excluded from the RX liveness
    #: watchdog (no RX frames are expected). Exclusive/atomic USB transports
    #: never enter this state — TX there still requires the co-armed duplex
    #: leg, so their tx-only demand stays deferred exactly as before.
    TX_ONLY = "tx_only"
    #: Watchdog-detected silent RX death (MOR-581). Surface-only as built:
    #: recovery currently rides the transport reconnect (MOR-586
    #: :meth:`AudioSession.reestablish`); a session-owned retry loop is a
    #: follow-up (MOR-609).
    RECOVERING = "recovering"
    #: Defined but never entered yet — no transitions target FAILED. The
    #: MOR-609 follow-up tracks adding a session-owned retry/FAILED path.
    FAILED = "failed"


#: States in which RX frames are supposed to be flowing (watchdog runs).
#: TX_ONLY is excluded: no RX subscription exists, so RX silence is expected
#: and must NOT false-positive the liveness watchdog into RECOVERING.
_WATCHED_STATES: frozenset[AudioSessionState] = frozenset(AudioSessionState) - {
    AudioSessionState.IDLE,
    AudioSessionState.TX_ONLY,
    AudioSessionState.FAILED,
}


@dataclass(frozen=True, slots=True)
class AudioSessionEvent:
    """Local liveness event (NO telemetry — open-core). ``timestamp`` comes
    from the session's monotonic clock (``time.monotonic`` by default) and is
    comparable to ``AudioBus.last_rx_frame_monotonic`` — see the
    ``AudioSession`` ``monotonic`` argument for the clock-injection caveat."""

    state: AudioSessionState
    reason: str
    leg: str
    timestamp: float


class RxSubscription:
    """Session-owned RX demand handle wrapping a bus subscription.

    Releasing goes through the session (NOT the underlying bus handle) so
    teardown ordering stays under the session lock.
    """

    def __init__(self, session: AudioSession, inner: AudioSubscription) -> None:
        self._session = session
        self._inner = inner

    @property
    def name(self) -> str:
        return self._inner.name

    async def get(self, timeout: float | None = None) -> AudioPacket | None:
        """Get the next packet (blocks until available or timeout)."""
        return await self._inner.get(timeout=timeout)

    def get_nowait(self) -> AudioPacket | None:
        return self._inner.get_nowait()

    def __aiter__(self) -> AsyncIterator[AudioPacket | None]:
        return self._inner

    async def release(self) -> None:
        """Drop this RX demand (idempotent)."""
        await self._session._release_rx(self)


class TxLease:
    """Refcounted TX demand handle; ``push`` forwards to the radio."""

    def __init__(self, session: AudioSession, owner: str) -> None:
        self._session = session
        self.owner = owner
        self._released = False

    @property
    def released(self) -> bool:
        return self._released

    async def push(self, audio_data: bytes) -> None:
        """Push TX audio (encoded per the radio's ``audio_tx_codec``)."""
        if self._released:
            raise RuntimeError(f"TX lease {self.owner!r} already released")
        # A held lease IS TX demand: converge the session to its armed state
        # before forwarding. Idempotent — a no-op when the TX leg is already
        # live. Converges, never rejects: the digital-TX (FT8/WSJT-X over the
        # companion) path where only a TX lease is held and the transport was
        # rebuilt (so the LAN stream is RECEIVING and ``push_tx`` would be
        # rejected) is re-armed here instead of failing.
        await self._session._converge_for_push()
        await self._session._radio.push_tx(audio_data)

    async def release(self) -> None:
        """Drop this TX demand (idempotent)."""
        await self._session._release_tx(self)


class AudioSession:
    """Per-radio demand-driven audio session (skeleton — ADR §3.2).

    Args:
        radio: AudioTransport-shaped duck type (same contract as
            :class:`AudioBus` — no import-matrix change).
        bus: Existing bus to wrap; defaults to ``radio.audio_bus`` or a
            fresh :class:`AudioBus`.
        monotonic: Monotonic clock used for the liveness watchdog and the
            :class:`AudioSessionEvent` timestamps; defaults to
            :func:`time.monotonic` — production behavior is unchanged.
            Test-only seam (MOR-607): liveness compares this clock against
            ``AudioBus.last_rx_frame_monotonic``, and the bus stamps real
            ``time.monotonic()`` on its own — callers injecting a fake
            clock MUST also feed the bus heartbeat from that SAME clock.
    """

    def __init__(
        self,
        radio: Any,
        *,
        bus: AudioBus | None = None,
        watchdog_interval: float = WATCHDOG_INTERVAL_S,
        rx_liveness_timeout: float = RX_LIVENESS_TIMEOUT_S,
        monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        self._radio = radio
        self._bus: AudioBus = (
            bus if bus is not None else getattr(radio, "audio_bus", None)
        ) or AudioBus(radio)
        self._lock = asyncio.Lock()
        self._state = AudioSessionState.IDLE
        self._rx_subs: list[RxSubscription] = []
        self._tx_leases: list[TxLease] = []
        self._watchdog_interval = watchdog_interval
        self._rx_liveness_timeout = rx_liveness_timeout
        self._monotonic: Callable[[], float] = monotonic
        self._watchdog_task: asyncio.Task[None] | None = None
        self._rx_armed_at: float = 0.0
        self._recovering_from: AudioSessionState | None = None
        self._listeners: list[Callable[[AudioSessionEvent], None]] = []
        self._last_event: AudioSessionEvent | None = None

    @property
    def state(self) -> AudioSessionState:
        return self._state

    @property
    def rx_demand(self) -> int:
        return len(self._rx_subs)

    @property
    def tx_demand(self) -> int:
        return len(self._tx_leases)

    @property
    def bus(self) -> AudioBus:
        """The wrapped AudioBus (fan-out; public API unchanged)."""
        return self._bus

    @property
    def last_event(self) -> AudioSessionEvent | None:
        """Most recent liveness event, or None (MOR-581)."""
        return self._last_event

    @property
    def stats(self) -> dict[str, Any]:
        return {
            "state": self._state.value,
            "rx_demand": len(self._rx_subs),
            "tx_demand": len(self._tx_leases),
            "setup_order": self._setup_order(),
            "bus": self._bus.stats,
        }

    # ── Liveness events (local listeners only — no telemetry) ───────────

    def add_listener(self, listener: Callable[[AudioSessionEvent], None]) -> None:
        """Register a local :class:`AudioSessionEvent` listener (idempotent)."""
        if listener not in self._listeners:
            self._listeners.append(listener)

    def remove_listener(self, listener: Callable[[AudioSessionEvent], None]) -> None:
        """Unregister a listener. No-op if not registered."""
        with contextlib.suppress(ValueError):
            self._listeners.remove(listener)

    def _emit(self, reason: str, leg: str = "rx") -> None:
        event = AudioSessionEvent(
            state=self._state, reason=reason, leg=leg, timestamp=self._monotonic()
        )
        self._last_event = event
        for listener in list(self._listeners):
            try:
                listener(event)
            except Exception:
                logger.warning("audio-session: event listener error", exc_info=True)

    # ── Demand API ───────────────────────────────────────────────────────

    async def subscribe_rx(self, name: str = "") -> RxSubscription:
        """Add RX demand; first subscriber arms RX (via the bus refcount).

        Raises if the radio RX leg fails to start — with the failed
        subscription fully unwound (no leaked demand, no leaked bus
        subscription).
        """
        async with self._lock:
            sub = RxSubscription(self, self._bus.subscribe(name=name))
            self._rx_subs.append(sub)
            try:
                await self._apply()
            except BaseException:
                self._rx_subs.remove(sub)
                await sub._inner.aclose()
                raise
            return sub

    async def acquire_tx(self, owner: str = "") -> TxLease:
        """Add TX demand; arms TX in the transport-declared order.

        A held lease IS TX demand. On a full-duplex ("rx_first") transport
        with no RX demand the TX leg arm is DEFERRED at the bare-acquire edge
        (intent-gated; see ``_desired()``); it arms on the push / reestablish
        edges when TX intent is active. With RX demand present the lease
        converges to RX_TX in the transport-declared order. Exclusive/atomic
        USB transports keep deferring tx-only demand (their TX leg requires the
        co-armed duplex stream) — ``_desired()`` maps that demand shape to IDLE.
        A TX start failure unwinds the lease and re-raises.
        """
        async with self._lock:
            lease = TxLease(self, owner)
            self._tx_leases.append(lease)
            try:
                await self._apply()
            except BaseException:
                self._tx_leases.remove(lease)
                lease._released = True
                raise
            return lease

    async def _converge_for_push(self) -> None:
        """Converge the TX leg for a held-lease :meth:`TxLease.push`.

        A held lease IS TX demand: if the observed transport TX leg is not
        live, drive convergence (which arms TX) so the frame is accepted —
        converges, never rejects. Guarded on a held lease so a push from a
        fully-released session still hits the transport's own
        ``AudioNotStartedError`` rather than silently arming.
        """
        async with self._lock:
            if self._tx_leases and not self._tx_leg_live():
                await self._converge(tx_active=True)
                await self._sync_watchdog()

    async def _release_rx(self, sub: RxSubscription) -> None:
        async with self._lock:
            if sub not in self._rx_subs:
                return
            self._rx_subs.remove(sub)
            # Hand the just-removed subscription to reconcile so its bus
            # ``aclose`` (which stops radio RX when it is the last subscriber)
            # is ordered relative to TX disarm/arm — never closing RX from a
            # TRANSMITTING transport (MOR-574), including the RX_TX → TX_ONLY
            # edge where TX is briefly re-armed.
            try:
                # Orders TX-down-before-RX when demand hits zero (MOR-574).
                await self._apply(closing_rx=sub)
            finally:
                # Idempotent: reconcile already closed it at the correct
                # point for TX-held transitions; this covers every other path.
                await sub._inner.aclose()

    async def _release_tx(self, lease: TxLease) -> None:
        async with self._lock:
            if lease not in self._tx_leases:
                lease._released = True
                return
            self._tx_leases.remove(lease)
            lease._released = True
            await self._apply()

    # ── Desired-state reconciliation (single lock holder) ────────────────

    def _desired(self, *, tx_active: bool = False) -> AudioSessionState:
        """Desired session state — a PURE function of declared demand.

        Reads ONLY the demand counters, the per-transport order flag, and the
        OBSERVED TX leg; NEVER ``self._state`` (so it cannot lie after a
        transport rebuild). A held ``TxLease`` IS TX demand.

        | rx | tx | full_duplex | desired  |
        |----|----|-------------|----------|
        |  0 |  0 |  any        | IDLE     |
        | >0 |  0 |  any        | RX_ONLY  |
        | >0 | >0 |  any        | RX_TX    |
        |  0 | >0 |  True       | TX_ONLY  |  (when TX intent is active*)
        |  0 | >0 |  False      | IDLE     |  (exclusive/atomic USB defers)

        \\*TX-only is full-duplex digital TX (FT8/WSJT-X over the companion):
        a lease with no RX. Its arm is intent-gated by ``tx_active`` — the
        observed TX leg liveness, OR forced True on the push / reestablish
        edges (a held lease that has pushed / survived an outage IS active
        intent). This preserves the bridge/poller "TX-lease-first, then RX"
        order (MOR-556): a bare ``acquire_tx`` with no RX yet does not arm a
        lone TX leg (``tx_active`` False), so RX arriving next converges
        straight to RX_TX without a TX flap. It is still PURE — a function of
        the demand counters, the per-transport order flag, and the OBSERVED
        TX leg (never ``self._state``) — so it cannot lie after a rebuild.

        RECOVERING / FAILED are transient transport-owned overlays and are
        never returned here.
        """
        rx = bool(self._rx_subs)
        tx = bool(self._tx_leases)
        if rx and tx:
            return AudioSessionState.RX_TX
        if rx:
            return AudioSessionState.RX_ONLY
        active = tx_active or self._tx_leg_live()
        if tx and active and self._setup_order() == "rx_first":
            return AudioSessionState.TX_ONLY
        return AudioSessionState.IDLE

    def _tx_leg_live(self) -> bool:
        """Observed transport TX state — NEVER ``self._state``.

        Reads the transport's coarse TX state, never ``self._state``. The
        production radio wraps the stream, so the observed state lives on
        ``radio._audio_stream.state`` (``AudioState.TRANSMITTING``); the test
        doubles expose it directly (``LanLikeRadio.state == "transmitting"``,
        ``ExclusiveUsbRadio.tx_running``, ``_StrictExclusiveRadio.tx_live``).
        This is what makes ``_converge`` recovery-safe: after a transport
        rebuild the leg reads down even when ``self._state`` still says
        TX_ONLY.
        """
        st = getattr(self._radio, "state", None)
        if st is None:
            stream = getattr(self._radio, "_audio_stream", None)
            st = getattr(stream, "state", None)
        if st is not None:
            return str(getattr(st, "value", st)).lower() == "transmitting"
        if hasattr(self._radio, "tx_live"):
            return bool(self._radio.tx_live)
        if hasattr(self._radio, "tx_running"):
            return bool(self._radio.tx_running)
        # Serial / USB backends route audio through a UsbAudioDriver that owns
        # the coarse TX-leg state; the radio itself exposes no stream. Observe
        # the driver's tx_running directly (read-only).
        for attr in ("_serial_audio_driver", "_audio_driver"):
            driver = getattr(self._radio, attr, None)
            if driver is not None:
                return bool(getattr(driver, "tx_running", False))
        return False

    def _setup_order(self) -> _SetupOrder:
        order = getattr(self._radio, "audio_setup_order", "rx_first")
        if order not in _VALID_SETUP_ORDERS:
            logger.warning(
                "audio-session: unknown audio_setup_order %r — using rx_first",
                order,
            )
            return "rx_first"
        return cast(_SetupOrder, order)

    async def _apply(self, *, closing_rx: RxSubscription | None = None) -> None:
        try:
            await self._converge(closing_rx=closing_rx)
        finally:
            if self._state is not AudioSessionState.RECOVERING:
                self._recovering_from = None
            await self._sync_watchdog()

    async def _converge(
        self,
        *,
        closing_rx: RxSubscription | None = None,
        recover: bool = False,
        tx_active: bool = False,
    ) -> None:
        """Drive the transport to ``_desired()``. Idempotent; the SOLE arming
        site for every edge (acquire, release, subscribe, push, mode-change,
        reestablish, recovery).

        Reconciles against OBSERVED transport leg liveness — ``bus.rx_active``
        and ``_tx_leg_live()`` — never ``self._state``, so it is correct after
        a transport rebuild (reestablish) and after any prior partial
        transition. Preserves the MOR-556/559/574 ordering by delegating
        RX_TX entry to ``_enter_rx_tx`` and stopping TX before RX on teardown.

        ``tx_active=True`` (push / reestablish edges): a held lease that has
        pushed or survived an outage IS active TX intent, so the lone TX leg
        is (re-)armed into TX_ONLY. Demand edges leave it False, deferring a
        bare-lease tx-only arm so the bridge's lease-then-RX order does not
        flap TX (MOR-556).

        ``recover=True`` (the reestablish edge): the transport was rebuilt
        underneath the bus, so its ``rx_active`` flag is stale — force a fresh
        RX re-attach via ``bus.restart_rx`` instead of the join-if-live
        ``_arm_rx``, surfacing a dead-RX re-arm to the caller.
        """
        desired = self._desired(tx_active=tx_active or recover)
        order = self._setup_order()
        # Teardown disarm hint: the TX leg is down only when OBSERVED down AND
        # the session never recorded a TX-bearing state. ``self._state`` is a
        # reliable record of what WE armed (never read for ARMING decisions —
        # that stays pure/observed — only to drive an idempotent stop_tx on
        # transports whose TX leg cannot be observed, e.g. a bare mock radio).
        tx_up = self._tx_leg_live() or self._state in (
            AudioSessionState.RX_TX,
            AudioSessionState.TX_ONLY,
        )

        if desired is AudioSessionState.IDLE:
            if tx_up:
                await self._disarm_tx()  # MOR-574: TX down BEFORE RX drops
            await self._drop_rx(closing_rx)
            self._state = AudioSessionState.IDLE
        elif desired is AudioSessionState.TX_ONLY:
            # Full-duplex, TX demand, no RX. Shed any RX first (TX-down then
            # RX-drop if a TX leg is up — MOR-574), then arm TX alone.
            if self._rx_subs or closing_rx is not None:
                if tx_up:
                    await self._disarm_tx()
                await self._drop_rx(closing_rx)
            await self._ensure_tx_live()
            self._state = AudioSessionState.TX_ONLY
        elif desired is AudioSessionState.RX_ONLY:
            if tx_up:
                await self._disarm_tx()  # MOR-574: TX down before RX work
                if order in _REARM_RX_AFTER_TX_DROP:
                    await self._bus.restart_rx()
            elif recover:
                await self._rearm_rx()  # forced re-attach after a rebuild
            else:
                await self._arm_rx()  # may raise → caller unwinds
            self._state = AudioSessionState.RX_ONLY
        else:  # RX_TX
            # _enter_rx_tx owns its terminal state: it settles at RX_ONLY on a
            # deferred-arm TX failure (no demanding caller), else RX_TX.
            await self._enter_rx_tx(order, recover=recover)

    async def _ensure_tx_live(self) -> None:
        """Idempotent ``start_tx`` against the OBSERVED transport TX leg."""
        if self._tx_leg_live():
            return
        try:
            await self._radio.start_tx()
        except AudioAlreadyStartedError:
            logger.debug("audio-session: TX already live on converge", exc_info=True)

    async def _enter_rx_tx(self, order: _SetupOrder, *, recover: bool = False) -> None:
        # Derive the entry sub-case from OBSERVED liveness (not self._state),
        # so the preserved MOR-556/559/574 call sequences are correct after a
        # transport rebuild. The actual start_*/stop_* sequences below are
        # copied verbatim from the pre-reconciler _enter_rx_tx.
        tx_live = self._tx_leg_live()
        # On the recover edge the bus rx_active flag is stale (transport was
        # rebuilt underneath it): treat RX as down and force a fresh re-arm,
        # so RX_TX recovery runs the clean from-scratch ordering.
        rx_live = self._bus.rx_active and not recover
        arm_rx = self._rearm_rx if recover else self._arm_rx
        if tx_live and rx_live:
            # Already RX_TX: a repeat demand edge (a second sub/lease) joins
            # the live legs idempotently — no re-arm, no double-start.
            await self._arm_rx()  # new subscribers join the live RX leg
            self._state = AudioSessionState.RX_TX
            return
        if tx_live and not rx_live:
            # Full-duplex only (TX_ONLY is unreachable elsewhere): the TX leg
            # is already up and RX demand just arrived. The LAN ``start_rx``
            # requires the stream's IDLE baseline, so RX cannot join while the
            # stream is TRANSMITTING — drop TX, arm RX, then re-arm TX (the
            # same clean rx_first ordering used from IDLE). RX is brief-gap
            # free for the operator since no RX subscriber existed yet anyway.
            await self._disarm_tx()
            await arm_rx()
            await self._radio.start_tx()
            self._state = AudioSessionState.RX_TX
            return
        if not tx_live and not rx_live:
            # Reached only via subscribe_rx (TX demand was deferred at
            # IDLE), so a TX arm failure here has no demanding caller to
            # raise to: log, settle at RX_ONLY, keep the leases — the next
            # demand edge retries (the step-20 recovery loop owns more).
            if order == "rx_first":
                await arm_rx()
                try:
                    await self._radio.start_tx()
                except Exception:
                    logger.warning(
                        "audio-session: deferred TX arm failed — RX_ONLY",
                        exc_info=True,
                    )
                    self._state = AudioSessionState.RX_ONLY
                    return
            else:  # "tx_first" / "atomic": TX leg up before RX joins
                try:
                    await self._radio.start_tx()
                except Exception:
                    logger.warning(
                        "audio-session: deferred TX arm failed — RX_ONLY",
                        exc_info=True,
                    )
                    await arm_rx()
                    self._state = AudioSessionState.RX_ONLY
                    return
                try:
                    await arm_rx()
                except BaseException:
                    await self._disarm_tx()  # never leave TX without RX
                    raise
        elif order == "rx_first":  # RX_ONLY → RX_TX, RX already up
            await self._radio.start_tx()
        else:
            # RX_ONLY → RX_TX on an exclusive/tx-first transport: tear the
            # RX leg down, arm TX, re-arm RX onto the running TX leg (the
            # MOR-559 live-validated order). The bus keeps its subscribers
            # throughout; ``restart_rx`` re-arms with the bus's own
            # callback — also on TX-arm failure, so RX demand never
            # strands (the failure propagates to the acquiring caller).
            await self._radio.stop_rx()
            try:
                await self._radio.start_tx()
            finally:
                await self._bus.restart_rx()
        self._state = AudioSessionState.RX_TX

    async def _arm_rx(self) -> None:
        """Start any not-yet-active bus subscriptions; verify RX is live.

        The bus swallows radio RX-start failures (``rx_active`` stays
        False) — surface them to the demanding subscriber instead.
        """
        for sub in self._rx_subs:
            if not sub._inner.active:
                await sub._inner.start()
        if self._rx_subs and not self._bus.rx_active:
            raise RuntimeError(
                "radio RX failed to start for the session subscription "
                "(AudioBus.rx_active is False after subscribe); see "
                "'audio-bus: failed to start RX' in the log"
            )

    async def _rearm_rx(self) -> None:
        """Force a fresh RX re-attach after a transport rebuild (recover edge).

        Unlike :meth:`_arm_rx` (which joins an already-live leg), this drives
        ``bus.restart_rx`` so the bus callback is re-wired onto the rebuilt
        transport regardless of the bus's stale ``rx_active`` flag. Surfaces a
        dead-RX re-arm to the caller so recovery can report FAILED.
        """
        await self._bus.restart_rx()
        if self._rx_subs and not self._bus.rx_active:
            raise RuntimeError(
                "radio RX failed to re-establish after reconnect "
                "(AudioBus.rx_active is False after re-arm); see "
                "'audio-bus: failed to re-arm RX' in the log"
            )

    async def _disarm_tx(self) -> None:
        try:
            await self._radio.stop_tx()
        except Exception:
            logger.debug("audio-session: stop TX error", exc_info=True)

    async def _drop_rx(self, closing_rx: RxSubscription | None = None) -> None:
        # Close any still-registered subs AND the subscription handed in by
        # ``_release_rx`` (already removed from ``_rx_subs``), so the bus
        # ``stop_rx`` is ordered after TX disarm on TX-held transitions
        # rather than firing later from ``_release_rx``'s finally while the
        # transport is TRANSMITTING (MOR-574).
        for sub in list(self._rx_subs):
            await sub._inner.aclose()
        self._rx_subs.clear()
        if closing_rx is not None:
            await closing_rx._inner.aclose()

    # ── Reconnect re-establishment (MOR-586, ADR §3.4 rule 4) ────────────

    async def reestablish(self) -> None:
        """Re-arm the radio RX/TX legs from LIVE demand after a reconnect.

        The radio-side reconnect (``runtime/_audio_recovery.py``) rebuilds
        the audio transport underneath the session: demand handles (bus
        subscriptions, TX leases) survive the outage, but the radio's RX
        callback and TX leg do not. The target state is derived from the
        CURRENT demand counters — never from a pre-disconnect snapshot —
        so demand dropped during the outage stays dropped, and the re-arm
        runs in the transport-declared order under the session lock.
        Idempotent: already-live legs are benign typed no-ops.

        Raises:
            RuntimeError: when demanded RX fails to come back live, so the
                recovery caller can surface FAILED. Demand is preserved —
                the next reconnect or demand edge retries.
        """
        async with self._lock:
            self._recovering_from = None
            # Converge against the rebuilt (RECEIVING/idle) transport. recover
            # forces a fresh RX re-attach (the bus rx_active flag is stale).
            # TX_ONLY / RX_ONLY / RX_TX / IDLE recovery all fall out of this:
            # demand dropped during the outage stays dropped (IDLE), a held
            # lease re-arms TX (TX_ONLY) with no phantom RX, RX-bearing demand
            # re-arms in the declared order. A dead-RX re-arm surfaces as a
            # RuntimeError so the recovery caller can report FAILED.
            await self._converge(recover=True)
            # Fresh liveness reference: silence is measured from this re-arm,
            # not from the stale pre-outage heartbeat.
            self._rx_armed_at = self._monotonic()
            await self._sync_watchdog()

    # ── Health watchdog (MOR-581 — surface only, recovery is step 20) ────

    async def _sync_watchdog(self) -> None:
        """Run the watchdog while RX should flow; on return to IDLE the task
        is cancelled AND awaited — no leaked task (MOR-567 conformance)."""
        task = self._watchdog_task
        if self._state in _WATCHED_STATES:
            if task is None or task.done():
                # Silence is measured from this arm point until the first
                # heartbeat ("starting" — the MOR-559 verified-start idea).
                self._rx_armed_at = self._monotonic()
                self._watchdog_task = asyncio.get_running_loop().create_task(
                    self._watchdog_loop(), name="audio-session-watchdog"
                )
        elif task is not None:
            self._watchdog_task = None
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task

    async def _watchdog_loop(self) -> None:
        """Liveness loop — reads ONLY the bus heartbeat (MOR-564)."""
        while True:
            await asyncio.sleep(self._watchdog_interval)
            async with self._lock:
                self._check_liveness_locked()

    def _check_liveness_locked(self) -> None:
        now = self._monotonic()
        last = self._bus.last_rx_frame_monotonic
        if self._state in (AudioSessionState.RX_ONLY, AudioSessionState.RX_TX):
            # Reference = the later of the last heartbeat and the arm point, so
            # a stale stamp from a previous run never false-positives a freshly
            # armed (still "starting") RX leg.
            ref = self._rx_armed_at if last is None else max(last, self._rx_armed_at)
            if now - ref >= self._rx_liveness_timeout:
                self._recovering_from = self._state
                self._state = AudioSessionState.RECOVERING
                logger.warning("audio-session: RX silent %.1fs — RECOVERING", now - ref)
                self._emit("rx_silent")
        elif self._state is AudioSessionState.RECOVERING:
            if last is not None and now - last < self._rx_liveness_timeout:
                self._recovering_from = None
                self._state = self._desired()
                logger.info("audio-session: RX frames resumed — %s", self._state.value)
                self._emit("rx_resumed")
