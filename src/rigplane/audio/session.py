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

    IDLE ⇄ RX_ONLY ⇄ RX_TX ⇄ RECOVERING        FAILED reserved (step 20)

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
never stopped from a TRANSMITTING transport. TX never runs without RX
(there is no TX_ONLY state); leases held across an RX gap are re-armed
when RX demand returns.

This module is consumed by NOTHING in src yet — wiring the bridge, the
poller PTT hooks, and the web handlers through the session is steps
9/11/12 of epic MOR-562.
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
    #: Watchdog-detected silent RX death (MOR-581; recovery loop = step 20).
    RECOVERING = "recovering"
    #: Reserved for the step-20 recovery loop (no transitions in yet).
    FAILED = "failed"


#: States in which RX frames are supposed to be flowing (watchdog runs).
_WATCHED_STATES: frozenset[AudioSessionState] = frozenset(AudioSessionState) - {
    AudioSessionState.IDLE,
    AudioSessionState.FAILED,
}


@dataclass(frozen=True, slots=True)
class AudioSessionEvent:
    """Local liveness event (NO telemetry — open-core). ``timestamp`` is
    ``time.monotonic()``, comparable to ``AudioBus.last_rx_frame_monotonic``."""

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
    """

    def __init__(
        self,
        radio: Any,
        *,
        bus: AudioBus | None = None,
        watchdog_interval: float = WATCHDOG_INTERVAL_S,
        rx_liveness_timeout: float = RX_LIVENESS_TIMEOUT_S,
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
            state=self._state, reason=reason, leg=leg, timestamp=time.monotonic()
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

        With no RX demand the lease is recorded but nothing is armed (no
        TX_ONLY state); arming happens when RX demand arrives. A TX start
        failure unwinds the lease and re-raises.
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

    async def _release_rx(self, sub: RxSubscription) -> None:
        async with self._lock:
            if sub not in self._rx_subs:
                return
            self._rx_subs.remove(sub)
            try:
                # Orders TX-down-before-RX when demand hits zero (MOR-574).
                await self._apply()
            finally:
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

    def _desired(self) -> AudioSessionState:
        if not self._rx_subs:
            return AudioSessionState.IDLE
        if self._tx_leases:
            return AudioSessionState.RX_TX
        return AudioSessionState.RX_ONLY

    def _setup_order(self) -> _SetupOrder:
        order = getattr(self._radio, "audio_setup_order", "rx_first")
        if order not in _VALID_SETUP_ORDERS:
            logger.warning(
                "audio-session: unknown audio_setup_order %r — using rx_first",
                order,
            )
            return "rx_first"
        return cast(_SetupOrder, order)

    async def _apply(self) -> None:
        try:
            await self._reconcile()
        finally:
            if self._state is not AudioSessionState.RECOVERING:
                self._recovering_from = None
            await self._sync_watchdog()

    async def _reconcile(self) -> None:
        desired = self._desired()
        # RECOVERING (watchdog-owned, MOR-581) shadows the live transport
        # state: demand transitions act on the shadowed state (TX still
        # disarms); the watchdog re-detects silence after any demand edge.
        effective = (
            (self._recovering_from or AudioSessionState.RX_ONLY)
            if self._state is AudioSessionState.RECOVERING
            else self._state
        )
        if desired is effective:
            if desired is not AudioSessionState.IDLE:
                await self._arm_rx()  # new subscribers join the live RX leg
            return
        if desired is AudioSessionState.RX_TX:
            await self._enter_rx_tx(self._setup_order(), effective)
        elif desired is AudioSessionState.RX_ONLY:
            if effective is AudioSessionState.RX_TX:
                await self._disarm_tx()
                if self._setup_order() in _REARM_RX_AFTER_TX_DROP:
                    await self._bus.restart_rx()
            else:
                await self._arm_rx()
            self._state = AudioSessionState.RX_ONLY
        else:  # IDLE
            if effective is AudioSessionState.RX_TX:
                await self._disarm_tx()  # MOR-574: TX down BEFORE RX drops
            await self._drop_rx()
            self._state = AudioSessionState.IDLE

    async def _enter_rx_tx(
        self, order: _SetupOrder, current: AudioSessionState
    ) -> None:
        if current is AudioSessionState.IDLE:
            # Reached only via subscribe_rx (TX demand was deferred at
            # IDLE), so a TX arm failure here has no demanding caller to
            # raise to: log, settle at RX_ONLY, keep the leases — the next
            # demand edge retries (the step-20 recovery loop owns more).
            if order == "rx_first":
                await self._arm_rx()
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
                    await self._arm_rx()
                    self._state = AudioSessionState.RX_ONLY
                    return
                try:
                    await self._arm_rx()
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

    async def _disarm_tx(self) -> None:
        try:
            await self._radio.stop_tx()
        except Exception:
            logger.debug("audio-session: stop TX error", exc_info=True)

    async def _drop_rx(self) -> None:
        for sub in list(self._rx_subs):
            await sub._inner.aclose()
        self._rx_subs.clear()

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
            desired = self._desired()
            if desired is AudioSessionState.IDLE:
                # Demand dropped during the outage — nothing to resurrect.
                self._state = AudioSessionState.IDLE
                await self._sync_watchdog()
                return
            order = self._setup_order()
            tx_wanted = desired is AudioSessionState.RX_TX
            tx_live = False
            if tx_wanted and order != "rx_first":
                # "tx_first"/"atomic": the TX leg must be up before RX
                # joins the device (the MOR-559 live-validated order).
                tx_live = await self._try_rearm_tx()
            await self._bus.restart_rx()
            if not self._bus.rx_active:
                if tx_live:
                    await self._disarm_tx()  # never leave TX without RX
                self._state = AudioSessionState.RX_ONLY
                self._rx_armed_at = time.monotonic()
                await self._sync_watchdog()
                raise RuntimeError(
                    "radio RX failed to re-establish after reconnect "
                    "(AudioBus.rx_active is False after re-arm); see "
                    "'audio-bus: failed to re-arm RX' in the log"
                )
            if tx_wanted and order == "rx_first":
                tx_live = await self._try_rearm_tx()
            self._state = (
                AudioSessionState.RX_TX if tx_live else AudioSessionState.RX_ONLY
            )
            # Fresh liveness reference: silence is measured from this
            # re-arm, not from the stale pre-outage heartbeat.
            self._rx_armed_at = time.monotonic()
            await self._sync_watchdog()

    async def _try_rearm_tx(self) -> bool:
        """Best-effort TX re-arm for :meth:`reestablish`; True when live.

        Mirrors the deferred-arm policy in :meth:`_enter_rx_tx`: a TX
        failure here has no demanding caller to raise to — settle at
        RX_ONLY, keep the leases, and let the next demand edge (or the
        next reconnect) retry.
        """
        try:
            await self._radio.start_tx()
        except AudioAlreadyStartedError:
            logger.debug("audio-session: TX already live on re-arm", exc_info=True)
        except Exception:
            logger.warning("audio-session: TX re-arm failed — RX_ONLY", exc_info=True)
            return False
        return True

    # ── Health watchdog (MOR-581 — surface only, recovery is step 20) ────

    async def _sync_watchdog(self) -> None:
        """Run the watchdog while RX should flow; on return to IDLE the task
        is cancelled AND awaited — no leaked task (MOR-567 conformance)."""
        task = self._watchdog_task
        if self._state in _WATCHED_STATES:
            if task is None or task.done():
                # Silence is measured from this arm point until the first
                # heartbeat ("starting" — the MOR-559 verified-start idea).
                self._rx_armed_at = time.monotonic()
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
        now = time.monotonic()
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
