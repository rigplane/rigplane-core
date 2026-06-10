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

    IDLE ⇄ RX_ONLY ⇄ RX_TX          RECOVERING / FAILED reserved (step 14)

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
import logging
from enum import Enum
from typing import TYPE_CHECKING, Any, AsyncIterator, Literal, cast

from rigplane.audio.bus import AudioBus, AudioSubscription

if TYPE_CHECKING:
    from rigplane.audio import AudioPacket

logger = logging.getLogger(__name__)

__all__ = ["AudioSession", "AudioSessionState", "RxSubscription", "TxLease"]

_SetupOrder = Literal["rx_first", "tx_first", "atomic"]
_VALID_SETUP_ORDERS: frozenset[str] = frozenset({"rx_first", "tx_first", "atomic"})

# RX_TX → RX_ONLY re-arm policy seam (step 12, MOR-554): after dropping TX,
# re-arm RX via the bus for these setup orders. Defaults to ALL orders —
# today's MOR-506 unconditional post-TX re-arm semantics (the bus swallows
# the redundant re-arm on full-duplex transports, exactly like the poller
# path does). MOR-554 narrows this to {"tx_first", "atomic"} once skipping
# the re-arm on "rx_first" transports is hardware-validated.
_REARM_RX_AFTER_TX_DROP: frozenset[str] = _VALID_SETUP_ORDERS


class AudioSessionState(Enum):
    """Session lifecycle states (ADR §3.3)."""

    IDLE = "idle"
    RX_ONLY = "rx_only"
    RX_TX = "rx_tx"
    #: Reserved — the health/recovery loop lands in step 14 (no transitions
    #: into these states yet).
    RECOVERING = "recovering"
    FAILED = "failed"


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

    def __init__(self, radio: Any, *, bus: AudioBus | None = None) -> None:
        self._radio = radio
        self._bus: AudioBus = (
            bus if bus is not None else getattr(radio, "audio_bus", None)
        ) or AudioBus(radio)
        self._lock = asyncio.Lock()
        self._state = AudioSessionState.IDLE
        self._rx_subs: list[RxSubscription] = []
        self._tx_leases: list[TxLease] = []

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
    def stats(self) -> dict[str, Any]:
        return {
            "state": self._state.value,
            "rx_demand": len(self._rx_subs),
            "tx_demand": len(self._tx_leases),
            "setup_order": self._setup_order(),
            "bus": self._bus.stats,
        }

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
        desired = self._desired()
        if desired is self._state:
            if desired is not AudioSessionState.IDLE:
                await self._arm_rx()  # new subscribers join the live RX leg
            return
        if desired is AudioSessionState.RX_TX:
            await self._enter_rx_tx(self._setup_order())
        elif desired is AudioSessionState.RX_ONLY:
            if self._state is AudioSessionState.RX_TX:
                await self._disarm_tx()
                if self._setup_order() in _REARM_RX_AFTER_TX_DROP:
                    await self._bus.restart_rx()
            else:
                await self._arm_rx()
            self._state = AudioSessionState.RX_ONLY
        else:  # IDLE
            if self._state is AudioSessionState.RX_TX:
                await self._disarm_tx()  # MOR-574: TX down BEFORE RX drops
            await self._drop_rx()
            self._state = AudioSessionState.IDLE

    async def _enter_rx_tx(self, order: _SetupOrder) -> None:
        if self._state is AudioSessionState.IDLE:
            # Reached only via subscribe_rx (TX demand was deferred at
            # IDLE), so a TX arm failure here has no demanding caller to
            # raise to: log, settle at RX_ONLY, keep the leases — the next
            # demand edge retries (the step-14 recovery loop owns more).
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
