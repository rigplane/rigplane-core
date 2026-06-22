"""Public SDK surface for the radio session lifecycle (contract-grade).

This module defines the **stable, contract-grade public API** for the unified
radio session lifecycle introduced to solve the livelock root cause described in
``docs/architecture/2026-06-22-radio-session-lifecycle.md``.

The module is divided into three sections:

1. **State and error taxonomy** – :class:`LifecycleState` (7-state enum) and
   :class:`LifecycleErrorReason` (cause codes for transitions to CLOSING or
   COOLDOWN).
2. **Rich observable types** (D1) – :class:`LifecycleEvent` (structured
   per-transition event) and :class:`LifecycleStatus` (point-in-time snapshot
   with progress/countdown for UI rendering).
3. **Controller interface** – :class:`RadioSessionLifecycle` (the resident
   policy layer; method signatures only, no state-machine implementation).

Stability contract (D6 — Tier 1, semver-stable)
------------------------------------------------
Every public symbol in this module is a **long-term contract** across the
``rigplane-core`` / ``rigplane-pro`` version boundary.  Changes MUST be
additive-only and versioned per
``rigplane-pro/docs/contracts/COMPATIBILITY.md`` (MOR-885).

* **Do not add provisional symbols here without the ``_PROVISIONAL`` note.**
* To add a field to a :func:`~dataclasses.dataclass`, use
  ``field(default=…)`` so existing callers do not break.
* To add a state or error-reason enum member, add it and document the version
  in which it appeared.

Canonical import
----------------
>>> from rigplane import RadioSessionLifecycle, LifecycleState, LifecycleStatus, LifecycleEvent

Or from the submodule directly (tier-1 in-package path)::

    from rigplane.runtime.session_lifecycle import (
        RadioSessionLifecycle,
        LifecycleState,
        LifecycleStatus,
        LifecycleEvent,
        LifecycleErrorReason,
        RadioPresence,
    )
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Callable
from contextlib import suppress
from dataclasses import dataclass
from enum import Enum
from typing import Protocol, runtime_checkable

from rigplane.core.exceptions import (
    AuthenticationError,
    ConnectionError as RigplaneConnectionError,
)

__all__ = [
    "LifecycleErrorReason",
    "LifecycleEvent",
    "LifecycleState",
    "LifecycleStatus",
    "RadioPresence",
    "RadioSessionLifecycle",
]

_logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# 1. State and error taxonomy
# ---------------------------------------------------------------------------


class LifecycleState(str, Enum):
    """The seven canonical states of a :class:`RadioSessionLifecycle`.

    The string value of each member is stable and may be serialised (e.g. to
    JSON for the UI).

    Transition diagram (§2.2 of the design doc)::

                  scan()                   connect()
        DISCONNECTED ──► SCANNING ──► DISCONNECTED
             │
             │ connect()
             ▼
         CONNECTING ──auth+civ_port>0──► CONNECTED
             │  │                           │
             │  │ civ_port==0 /             │ data stall / ctrl loss
             │  │ 0xFFFFFFFF reject         ▼
             │  ▼                        RECOVERING ──ok──► CONNECTED
             │  COOLDOWN ──wait──► CONNECTING   │ fail (max)
             │  (same process; token NOT held)  ▼
             │                               CLOSING
             └── disconnect() / SIGTERM ──► CLOSING ──► DISCONNECTED

    Version history:

    * ``v2.11`` — initial introduction.
    """

    DISCONNECTED = "disconnected"
    """No session exists; idle.  The lifecycle is ready to call :meth:`connect`."""

    SCANNING = "scanning"
    """A :meth:`scan` call is in progress (AYT broadcast, no session held)."""

    CONNECTING = "connecting"
    """A session establishment attempt is in progress (auth + CI-V port wait)."""

    COOLDOWN = "cooldown"
    """
    A previous attempt's session has been released and the lifecycle is waiting
    before the next retry.  The radio's keepalive window must expire before we
    re-claim the port.

    In normal operation (graceful disconnect) this state should almost never
    appear.  A cooldown following our *own* clean disconnect indicates a bug in
    the release path.  Cooldown is expected only when a **foreign** client holds
    the radio.
    """

    CONNECTED = "connected"
    """Session established; transports open; CI-V pump running."""

    RECOVERING = "recovering"
    """
    Data stall or control loss detected; soft-reconnect in progress.
    The existing token is reused (no new login).  If recovery exhausts
    :data:`_MAX_RECONNECTS` attempts the lifecycle transitions to CLOSING.
    """

    CLOSING = "closing"
    """
    Teardown in progress: token-remove + OpenClose-close + socket release.
    This state is transient; the lifecycle always ends in DISCONNECTED.
    """


class LifecycleErrorReason(str, Enum):
    """Structured cause codes carried by :class:`LifecycleEvent` and
    :class:`LifecycleStatus`.

    The string value is stable and may be serialised.

    Version history:

    * ``v2.11`` — initial introduction.
    """

    NONE = "none"
    """No error; used when a transition is not caused by a failure."""

    AUTH_CREDENTIALS = "auth_credentials"
    """
    Authentication rejected with error ``0xFEFFFFFF`` — credentials are wrong.
    This is a **hard failure** (D3): the lifecycle transitions to CLOSING and
    does NOT retry.
    """

    SESSION_BUSY_REJECT = "session_busy_reject"
    """
    The radio returned ``error=0xFFFFFFFF`` (previous session still active).
    A cooldown-aware resident retry follows (D3).
    """

    SESSION_NOT_READY = "session_not_ready"
    """
    The radio returned ``civ_port=0`` (port not yet allocated, radio not
    ready).  A cooldown-aware resident retry follows (D3).
    """

    DATA_WATCHDOG_STALL = "data_watchdog_stall"
    """
    The CI-V data watchdog detected a stall (no frames within the timeout
    window).  Triggers a transition from CONNECTED to RECOVERING.
    """

    CONTROL_LOSS = "control_loss"
    """
    The control-channel connection was lost.  Triggers CONNECTED → RECOVERING.
    """

    RECOVERY_EXHAUSTED = "recovery_exhausted"
    """
    :data:`_MAX_RECONNECTS` soft-reconnect attempts all failed.
    Transitions from RECOVERING to CLOSING.
    """

    CANCELLED = "cancelled"
    """
    The lifecycle was cancelled (e.g. ``disconnect()`` called or SIGTERM
    received) during CONNECTING, COOLDOWN, or RECOVERING.
    """


# ---------------------------------------------------------------------------
# 2. Rich observable types (D1 — first-class requirement)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class RadioPresence:
    """Result of a single :meth:`RadioSessionLifecycle.scan` response.

    Attributes:
        host:      IPv4 address of the responding radio.
        remote_id: The radio's 32-bit network identity (from the IAH packet).

    Version history:

    * ``v2.11`` — initial introduction.
    """

    host: str
    remote_id: int


@dataclass(frozen=True)
class LifecycleEvent:
    """A single structured state-transition event emitted by the lifecycle.

    Emitted on **every** state transition; consumed by the observable
    callback registered via :meth:`RadioSessionLifecycle.add_event_listener`.

    Design note (D1): the event stream is first-class — observers should not
    poll :attr:`RadioSessionLifecycle.status`; events are the authoritative
    notification surface.

    Attributes:
        from_state:      State before the transition.
        to_state:        State after the transition.
        reason:          Structured cause code for the transition.
        cooldown_remaining_s:
            For transitions **into** :attr:`LifecycleState.COOLDOWN`:
            estimated seconds until the next connection attempt.
            ``None`` for all other transitions.
        recovery_attempt:
            For transitions **into** :attr:`LifecycleState.RECOVERING`:
            current attempt number (1-based).  ``None`` for other transitions.
        recovery_max:
            The maximum number of recovery attempts for the current session
            (``_MAX_RECONNECTS``).  ``None`` when ``recovery_attempt`` is
            ``None``.

    Version history:

    * ``v2.11`` — initial introduction.
    """

    from_state: LifecycleState
    to_state: LifecycleState
    reason: LifecycleErrorReason = LifecycleErrorReason.NONE
    cooldown_remaining_s: float | None = None
    recovery_attempt: int | None = None
    recovery_max: int | None = None


@dataclass(frozen=True)
class LifecycleStatus:
    """Point-in-time snapshot of the lifecycle state for UI rendering.

    Retrieved via :attr:`RadioSessionLifecycle.status`.  This snapshot is
    immutable; subscribe to events via
    :meth:`RadioSessionLifecycle.add_event_listener` for change notifications.

    Attributes:
        state:
            Current :class:`LifecycleState`.
        last_error:
            Most recent error reason, or :attr:`LifecycleErrorReason.NONE`.
        cooldown_remaining_s:
            Seconds remaining in the current cooldown wait (COOLDOWN state
            only).  ``None`` when not in COOLDOWN.
        cooldown_total_s:
            Total duration of the current cooldown window (COOLDOWN state
            only).  Allows rendering a progress bar.  ``None`` when not in
            COOLDOWN.
        recovery_attempt:
            Current soft-reconnect attempt number (1-based) when in RECOVERING.
            ``None`` otherwise.
        recovery_max:
            Maximum soft-reconnect attempts (``_MAX_RECONNECTS``) when in
            RECOVERING.  ``None`` otherwise.
        connecting_elapsed_s:
            Seconds elapsed since the current CONNECTING attempt began.  ``None``
            when not in CONNECTING.

    Version history:

    * ``v2.11`` — initial introduction.
    """

    state: LifecycleState
    last_error: LifecycleErrorReason = LifecycleErrorReason.NONE
    cooldown_remaining_s: float | None = None
    cooldown_total_s: float | None = None
    recovery_attempt: int | None = None
    recovery_max: int | None = None
    connecting_elapsed_s: float | None = None


# ---------------------------------------------------------------------------
# 3. Controller interface
# ---------------------------------------------------------------------------

#: Callback type for :meth:`RadioSessionLifecycle.add_event_listener`.
#:
#: A listener receives each :class:`LifecycleEvent` synchronously on the
#: event loop that drives the lifecycle.  Listeners MUST be cheap (no
#: blocking I/O); schedule expensive work with ``asyncio.create_task``.
#: Exceptions raised by a listener are logged and swallowed — they do NOT
#: abort the lifecycle.
EventListener = Callable[["LifecycleEvent"], None]


@runtime_checkable
class RadioSessionLifecycle(Protocol):
    """Protocol / interface for the resident radio session lifecycle controller.

    This is the **only** public lifecycle entry point.  All
    connect / disconnect / scan / recover intelligence lives here.

    Governing invariant
    -------------------
    A graceful ``connect()`` followed by a graceful ``disconnect()`` MUST NOT
    cause a cooldown.  Cooldown means the session was torn down *non-gracefully*
    (i.e. a foreign client held it).  Every exit path — normal, exceptional, or
    SIGTERM — MUST release the session (token-remove + OpenClose-close) before
    the socket or process goes away.

    Concurrency
    -----------
    The lifecycle is async-native.  All public methods are coroutines and MUST
    be awaited.  The lifecycle is NOT thread-safe; call it from the event loop
    that owns it.

    Context-manager usage
    ---------------------
    The lifecycle implements the async context-manager protocol.  ``__aexit__``
    *always* calls :meth:`disconnect` — even if ``__aenter__`` raised::

        async with lifecycle:
            # session established
            ...
        # session released

    Observable state
    ----------------
    Poll :attr:`status` for the current snapshot, or register a listener via
    :meth:`add_event_listener` for push notification.

    Stability
    ---------
    Tier 1, semver-stable (D6, v2.11+).  Breaking changes require a major
    version bump and a coordinated core ↔ Pro cadence per
    ``rigplane-pro/docs/contracts/COMPATIBILITY.md`` (MOR-885).

    Version history:

    * ``v2.11`` — initial introduction.
    """

    # ------------------------------------------------------------------
    # Observable state
    # ------------------------------------------------------------------

    @property
    def state(self) -> LifecycleState:
        """Current lifecycle state (observable, real-time).

        Returns:
            The current :class:`LifecycleState` of this lifecycle instance.

        .. note::
            For change notifications, prefer :meth:`add_event_listener` over
            polling this property.
        """
        ...

    @property
    def status(self) -> LifecycleStatus:
        """Rich point-in-time snapshot including progress and countdown fields.

        Suitable for rendering a UI status bar or tooltip.  Fields specific to
        the current state (``cooldown_remaining_s``, ``recovery_attempt``, etc.)
        are populated only when the lifecycle is in the relevant state.

        Returns:
            An immutable :class:`LifecycleStatus` snapshot.
        """
        ...

    # ------------------------------------------------------------------
    # Event subscription
    # ------------------------------------------------------------------

    def add_event_listener(
        self,
        listener: Callable[[LifecycleEvent], None],
    ) -> None:
        """Register a callback to receive every :class:`LifecycleEvent`.

        The listener is called synchronously on the event loop for each state
        transition.  It MUST NOT block or raise; exceptions are logged and
        swallowed.  Duplicate registrations are ignored (idempotent).

        Args:
            listener: A callable accepting a single :class:`LifecycleEvent`.
        """
        ...

    def remove_event_listener(
        self,
        listener: Callable[[LifecycleEvent], None],
    ) -> None:
        """Deregister a previously registered event listener.

        No-op if *listener* was never registered.

        Args:
            listener: The callable to remove.
        """
        ...

    # ------------------------------------------------------------------
    # Lifecycle operations
    # ------------------------------------------------------------------

    async def scan(
        self,
        targets: list[str] | None = None,
        *,
        timeout: float = 3.0,
    ) -> list[RadioPresence]:
        """Probe the LAN for responding radios (presence only; NO session opened).

        Sends AYT broadcasts and collects IAH responses.  ``scan()`` MUST NOT
        send login / token / conninfo / OpenClose packets and MUST NOT hold a
        session.  Calling ``scan()`` while a session is active is safe — it
        does not disturb the active session.

        The lifecycle transitions DISCONNECTED → SCANNING → DISCONNECTED around
        this call (or stays CONNECTED → (scan is fire-and-forget) → CONNECTED
        when already connected — exact in-session behaviour is implementation
        detail).

        Args:
            targets:
                Optional list of specific IP addresses to probe.  When
                ``None`` the implementation broadcasts to the default
                discovery address (``255.255.255.255:50001``).
            timeout:
                Seconds to wait for responses before returning.

        Returns:
            List of :class:`RadioPresence` records, one per responding radio.
            May be empty if no radios respond within *timeout*.
        """
        ...

    async def connect(self) -> None:
        """Establish a session; resident until connected or cancelled.

        This method is **resident**: it performs cooldown-aware retry entirely
        in-process.  It returns only when:

        * the session reaches CONNECTED state; or
        * the caller cancels the task (``asyncio.CancelledError``); or
        * a hard, non-transient failure occurs (``LifecycleErrorReason.AUTH_CREDENTIALS``).

        On transient failure (``civ_port==0`` / ``0xFFFFFFFF``):

        * the current partial session is **released** (token-remove +
          OpenClose-close) before entering COOLDOWN;
        * the lifecycle waits in-process until the cooldown window expires;
        * then retries CONNECTING — without ever leaving the process.

        This design guarantees that the radio's keepalive window expires before
        the port is re-claimed (Cause B fix).

        Raises:
            rigplane.exceptions.AuthenticationError:
                When credentials are rejected (``0xFEFFFFFF``, D3 hard-fail).
            asyncio.CancelledError:
                When the task is cancelled (e.g. ``disconnect()`` called
                concurrently or SIGTERM received).

        .. warning::
            Calling ``connect()`` when already CONNECTED or CONNECTING is
            coalesced / idempotent (T9) — the existing attempt is reused; a
            second connect does NOT claim a second session.
        """
        ...

    async def disconnect(self) -> None:
        """Release the session gracefully and transition to DISCONNECTED.

        Sends token-remove + OpenClose-close + closes the socket regardless of
        the current state.  **Idempotent**: calling on an unclaimed or already-
        disconnected lifecycle is a no-op.

        If called during CONNECTING or COOLDOWN, the resident runner is
        cancelled and the lifecycle transitions CLOSING → DISCONNECTED.

        After this method returns, the lifecycle is in DISCONNECTED and the
        radio's ownership lock is released.
        """
        ...

    async def soft_reconnect(self) -> None:
        """Attempt to recover the data path while reusing the existing session.

        Transitions CONNECTED → RECOVERING and re-opens only the data channel
        (no new login; the existing token and control connection are reused).
        On success, transitions back to CONNECTED and re-arms the data watchdog.

        This method is normally driven internally by the data watchdog.  External
        callers may invoke it to trigger an immediate recovery attempt.

        Raises:
            rigplane.exceptions.ConnectionError:
                When recovery exhausts all attempts (``_MAX_RECONNECTS``).  The
                lifecycle transitions to CLOSING and releases the session.
            asyncio.CancelledError:
                When cancelled during recovery.

        .. note::
            Concurrent calls while RECOVERING are coalesced — the second caller
            awaits the ongoing recovery attempt.
        """
        ...

    # ------------------------------------------------------------------
    # Async context manager
    # ------------------------------------------------------------------

    async def __aenter__(self) -> RadioSessionLifecycle:
        """Enter context: calls :meth:`connect` and returns *self*.

        If ``connect()`` raises, ``__aexit__`` is still called to release any
        partial session (Hole 2 fix).

        Returns:
            *self* for use in ``as`` clauses.
        """
        ...

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: object,
    ) -> bool | None:
        """Exit context: **always** calls :meth:`disconnect` regardless of
        whether an exception was raised.

        Returns:
            ``None`` / ``False`` — does not suppress exceptions.
        """
        ...


# ===========================================================================
# 4. Concrete implementation (task A2 — NOT part of the frozen Tier-1 contract)
# ===========================================================================
#
# Everything ABOVE this line is the frozen public-SDK contract (A2-api,
# commit 76fa48ed); do not alter it.  Everything BELOW is the concrete
# state-machine implementation introduced in task A2.  It is an internal
# runtime detail — Pro consumes only the :class:`RadioSessionLifecycle`
# Protocol and the rich types above, never these classes directly.
#
# Architecture: the lifecycle is the *policy* layer.  It drives a thin
# *mechanism* (the packet I/O — :class:`~rigplane.runtime._control_phase.
# ControlPhaseRuntime` in production, a fake in unit tests) via the
# :class:`SessionMechanism` protocol below.  This is what makes the retry /
# cooldown / recovery policy unit-testable without real sockets, and what lets
# ``_control_phase`` be demoted to pure packet mechanism (the old retry wrapper
# is re-expressed here as CONNECTING↔COOLDOWN state transitions).

# Policy constants — this is now the ONLY home for the retry/cooldown timing
# (they used to live on ``ControlPhaseRuntime``; A3 removes them there).
_STATUS_RETRY_PAUSE = 10.0  # civ_port==0 (not ready) cooldown, seconds
_STATUS_REJECT_COOLDOWN = 30.0  # 0xFFFFFFFF (busy reject) cooldown, seconds
_MAX_RECONNECTS = 3  # soft-reconnect attempts before CLOSING
_RECONNECT_BACKOFF: tuple[float, ...] = (45.0, 60.0, 60.0)
# A bounded resident-retry cap so connect() cannot truly spin forever in a
# pathological foreign-hold scenario while still being "resident" for the
# normal transient window.  Generous because cooldown should almost never fire
# after our own clean disconnect (D3).
_MAX_CONNECT_ATTEMPTS = 1000


class AttemptOutcome(Enum):
    """Classification of a single :meth:`SessionMechanism.connect_attempt`.

    This mirrors the radio's status response after a conninfo exchange:

    * ``CONNECTED`` — auth succeeded and ``civ_port > 0``; the data path is up.
    * ``SESSION_NOT_READY`` — auth succeeded but ``civ_port == 0`` (radio not
      ready yet).  Transient → cooldown-aware resident retry (D3).
    * ``SESSION_BUSY_REJECT`` — status ``error == 0xFFFFFFFF`` (previous session
      still active).  Transient → cooldown-aware resident retry (D3).
    * ``AUTH_CREDENTIALS`` — auth rejected with ``error == 0xFEFFFFFF``.  Hard,
      non-transient failure → no retry (D3).
    """

    CONNECTED = "connected"
    SESSION_NOT_READY = "session_not_ready"
    SESSION_BUSY_REJECT = "session_busy_reject"
    AUTH_CREDENTIALS = "auth_credentials"


@dataclass(frozen=True)
class AttemptResult:
    """Result of one :meth:`SessionMechanism.connect_attempt`.

    ``raises`` is a test/seam affordance: a mechanism may raise instead of
    returning, but a fake mechanism can carry an exception to inject one *after*
    the session has been claimed (proving Hole 1 release-on-exception).
    """

    outcome: AttemptOutcome
    raises: BaseException | None = None


@runtime_checkable
class SessionMechanism(Protocol):
    """The packet-I/O mechanism the lifecycle policy drives.

    In production this is satisfied by an adapter over
    :class:`~rigplane.runtime._control_phase.ControlPhaseRuntime` (wired by task
    A3).  In unit tests it is a fake with queued outcomes.

    Implementations MUST:

    * register a release obligation the instant the session is *claimed* (auth
      success), so that :meth:`release` discharges it even if
      :meth:`connect_attempt` later raises;
    * make :meth:`release` idempotent (safe to call when nothing is claimed).
    """

    async def connect_attempt(self) -> AttemptResult:
        """Perform ONE connect attempt (auth + CI-V port).  No retry, no sleep."""
        ...

    async def release(self) -> None:
        """Release the session: token-remove (0x01) + OpenClose-close + sockets.

        Idempotent; a no-op when no session is claimed.
        """
        ...

    async def soft_reconnect_once(self) -> None:
        """Re-open the data path reusing the existing control + token.

        Raises on failure; the policy layer counts attempts and decides when to
        give up (→ CLOSING).
        """
        ...

    async def scan(
        self, targets: list[str] | None, *, timeout: float
    ) -> list[RadioPresence]:
        """Presence-probe only (AYT/IAH); never opens or holds a session."""
        ...


class CoreRadioSessionLifecycle:
    """Concrete :class:`RadioSessionLifecycle` — the resident policy layer.

    Owns the state machine, the observable surface (state/status/events), the
    resident CONNECTING↔COOLDOWN runner, recovery attempt accounting, the
    release obligation, and the SIGTERM graceful-close hook.  Drives a
    :class:`SessionMechanism` for all packet I/O.

    Not thread-safe; call from the event loop that owns it.
    """

    def __init__(
        self,
        mechanism: SessionMechanism,
        *,
        not_ready_cooldown_s: float = _STATUS_RETRY_PAUSE,
        reject_cooldown_s: float = _STATUS_REJECT_COOLDOWN,
        max_recovery_attempts: int = _MAX_RECONNECTS,
        recovery_backoff_s: tuple[float, ...] = _RECONNECT_BACKOFF,
        max_connect_attempts: int = _MAX_CONNECT_ATTEMPTS,
    ) -> None:
        self._mech = mechanism
        self._not_ready_cooldown_s = not_ready_cooldown_s
        self._reject_cooldown_s = reject_cooldown_s
        self._max_recovery_attempts = max_recovery_attempts
        self._recovery_backoff_s = recovery_backoff_s
        self._max_connect_attempts = max_connect_attempts

        self._state: LifecycleState = LifecycleState.DISCONNECTED
        self._last_error: LifecycleErrorReason = LifecycleErrorReason.NONE
        self._listeners: list[EventListener] = []

        # Resident connect runner + coalescing.
        self._connect_task: asyncio.Task[None] | None = None
        self._recover_task: asyncio.Task[None] | None = None
        # True once a session has been claimed (auth succeeded) and not yet
        # released — drives idempotent disconnect.
        self._claimed = False

        # Progress/countdown bookkeeping for the status snapshot (D1).
        self._cooldown_deadline: float | None = None
        self._cooldown_total_s: float | None = None
        self._connecting_started: float | None = None
        self._recovery_attempt: int | None = None

    # ------------------------------------------------------------------
    # Observable surface
    # ------------------------------------------------------------------

    @property
    def state(self) -> LifecycleState:
        return self._state

    @property
    def status(self) -> LifecycleStatus:
        now = time.monotonic()
        cooldown_remaining: float | None = None
        if (
            self._state is LifecycleState.COOLDOWN
            and self._cooldown_deadline is not None
        ):
            cooldown_remaining = max(0.0, self._cooldown_deadline - now)
        connecting_elapsed: float | None = None
        if (
            self._state is LifecycleState.CONNECTING
            and self._connecting_started is not None
        ):
            connecting_elapsed = max(0.0, now - self._connecting_started)
        recovery_attempt = (
            self._recovery_attempt if self._state is LifecycleState.RECOVERING else None
        )
        recovery_max = (
            self._max_recovery_attempts
            if self._state is LifecycleState.RECOVERING
            else None
        )
        return LifecycleStatus(
            state=self._state,
            last_error=self._last_error,
            cooldown_remaining_s=cooldown_remaining,
            cooldown_total_s=(
                self._cooldown_total_s
                if self._state is LifecycleState.COOLDOWN
                else None
            ),
            recovery_attempt=recovery_attempt,
            recovery_max=recovery_max,
            connecting_elapsed_s=connecting_elapsed,
        )

    # ------------------------------------------------------------------
    # Event subscription
    # ------------------------------------------------------------------

    def add_event_listener(self, listener: EventListener) -> None:
        if listener not in self._listeners:
            self._listeners.append(listener)

    def remove_event_listener(self, listener: EventListener) -> None:
        with suppress(ValueError):
            self._listeners.remove(listener)

    def _emit(
        self,
        to_state: LifecycleState,
        *,
        reason: LifecycleErrorReason = LifecycleErrorReason.NONE,
        cooldown_remaining_s: float | None = None,
        recovery_attempt: int | None = None,
        recovery_max: int | None = None,
    ) -> None:
        from_state = self._state
        self._state = to_state
        if reason is not LifecycleErrorReason.NONE:
            self._last_error = reason
        event = LifecycleEvent(
            from_state=from_state,
            to_state=to_state,
            reason=reason,
            cooldown_remaining_s=cooldown_remaining_s,
            recovery_attempt=recovery_attempt,
            recovery_max=recovery_max,
        )
        for listener in list(self._listeners):
            try:
                listener(event)
            except Exception:  # noqa: BLE001 — listeners must never abort us
                _logger.debug(
                    "lifecycle.listener.error", exc_info=True, extra={"event": event}
                )

    # ------------------------------------------------------------------
    # connect() — resident, cooldown-aware
    # ------------------------------------------------------------------

    async def connect(self) -> None:
        # T9: coalesce a concurrent/duplicate connect onto the in-flight one.
        if self._connect_task is not None and not self._connect_task.done():
            await self._connect_task
            return
        if self._state is LifecycleState.CONNECTED:
            return
        self._connect_task = asyncio.ensure_future(self._run_connect())
        try:
            await self._connect_task
        finally:
            if self._connect_task is not None and self._connect_task.done():
                self._connect_task = None

    async def _run_connect(self) -> None:
        """Resident CONNECTING↔COOLDOWN loop; release before every cooldown wait."""
        try:
            await self._connect_loop()
        except asyncio.CancelledError:
            # Cancellation (disconnect/SIGTERM during CONNECTING/COOLDOWN): the
            # release already ran in ``_attempt_with_release``/teardown; just
            # propagate so the awaiter sees the cancel.
            raise
        except (AuthenticationError, RigplaneConnectionError):
            # Hard-fail / exhaustion paths already drove the machine to
            # DISCONNECTED; just re-raise.
            raise
        except BaseException:
            # Any other post-claim exception: release ran inside
            # ``_attempt_with_release``; drive the observable machine home.
            self._connecting_started = None
            if self._state not in (
                LifecycleState.CLOSING,
                LifecycleState.DISCONNECTED,
            ):
                self._emit(
                    LifecycleState.CLOSING, reason=LifecycleErrorReason.CANCELLED
                )
                self._emit(LifecycleState.DISCONNECTED)
            raise

    async def _connect_loop(self) -> None:
        attempt = 0
        while True:
            attempt += 1
            self._connecting_started = time.monotonic()
            self._emit(LifecycleState.CONNECTING)
            outcome = await self._attempt_with_release()

            if outcome is AttemptOutcome.CONNECTED:
                self._connecting_started = None
                self._emit(LifecycleState.CONNECTED)
                return

            if outcome is AttemptOutcome.AUTH_CREDENTIALS:
                # D3 hard-fail: NO resident retry.  Release (idempotent) +
                # CLOSING + raise.
                self._connecting_started = None
                await self._mech.release()
                self._claimed = False
                self._emit(
                    LifecycleState.CLOSING,
                    reason=LifecycleErrorReason.AUTH_CREDENTIALS,
                )
                self._emit(LifecycleState.DISCONNECTED)
                raise AuthenticationError(
                    "Authentication failed (error=0xFEFFFFFF); credentials rejected"
                )

            # Transient: SESSION_NOT_READY (civ_port==0) or SESSION_BUSY_REJECT
            # (0xFFFFFFFF).  The session was already released inside
            # ``_attempt_with_release`` (release BEFORE the wait — Hole 4 / Cause
            # A inversion).  Now enter COOLDOWN and wait in-process.
            if attempt >= self._max_connect_attempts:
                self._connecting_started = None
                self._emit(LifecycleState.DISCONNECTED)
                raise RigplaneConnectionError(
                    "Radio session never became ready after "
                    f"{attempt} resident attempts; a foreign client may hold it."
                )

            if outcome is AttemptOutcome.SESSION_BUSY_REJECT:
                reason = LifecycleErrorReason.SESSION_BUSY_REJECT
                cooldown = self._reject_cooldown_s
            else:
                reason = LifecycleErrorReason.SESSION_NOT_READY
                cooldown = self._not_ready_cooldown_s

            self._connecting_started = None
            self._cooldown_total_s = cooldown
            self._cooldown_deadline = time.monotonic() + cooldown
            self._emit(
                LifecycleState.COOLDOWN,
                reason=reason,
                cooldown_remaining_s=cooldown,
            )
            try:
                await asyncio.sleep(cooldown)
            finally:
                self._cooldown_deadline = None
                self._cooldown_total_s = None
            # loop → CONNECTING again, inside the same resident process.

    async def _attempt_with_release(self) -> AttemptOutcome:
        """One connect attempt; on any non-CONNECTED outcome RELEASE first.

        Guarantees the release obligation is discharged before we leave this
        coroutine on a transient outcome OR on an exception (Holes 1/5/8) —
        BEFORE any cooldown wait happens (Hole 4 / Cause A).
        """
        try:
            result = await self._mech.connect_attempt()
        except BaseException:
            # Post-auth/pre-CONNECTED exception (or cancellation): release the
            # (possibly partial) claim, then re-raise.  ``release`` is idempotent
            # so this is safe even if nothing was claimed.
            await self._mech.release()
            self._claimed = False
            raise

        if result.outcome is AttemptOutcome.CONNECTED:
            self._claimed = True
            return AttemptOutcome.CONNECTED

        if result.outcome is AttemptOutcome.AUTH_CREDENTIALS:
            # Pre-claim hard failure — release defensively (no-op) and report.
            await self._mech.release()
            self._claimed = False
            return AttemptOutcome.AUTH_CREDENTIALS

        # Transient: civ_port==0 or 0xFFFFFFFF.  Auth DID succeed, so a session
        # is claimed — RELEASE it now, before the caller enters the cooldown
        # wait.  This is the structural inversion of the old held-cooldown bug.
        await self._mech.release()
        self._claimed = False
        return result.outcome

    # ------------------------------------------------------------------
    # disconnect() — always releases (idempotent)
    # ------------------------------------------------------------------

    async def disconnect(self) -> None:
        await self._teardown(reason=LifecycleErrorReason.CANCELLED, shutdown=False)

    async def request_shutdown(self) -> None:
        """SIGTERM-style graceful close: release the session BEFORE process exit.

        This is the awaitable hook the CLI signal handler MUST call instead of
        ``os._exit`` (design §2.6 / Hole 3).  It cancels any resident runner or
        cooldown wait, runs the full release, and ends in DISCONNECTED.

        CLI wiring (A3/Phase B follow-up, outside this task's files): install an
        asyncio SIGTERM handler that schedules ``await lifecycle.request_shutdown()``
        on the running loop, with a bounded ~2-3 s deadline (D2), then stops the
        loop and exits 0.  Do NOT call ``os._exit`` before this awaitable
        completes.
        """
        await self._teardown(reason=LifecycleErrorReason.CANCELLED, shutdown=True)

    async def _teardown(self, *, reason: LifecycleErrorReason, shutdown: bool) -> None:
        # Cancel any in-flight resident runner / recovery task first so their
        # own ``finally`` release does not race ours.
        for task_attr in ("_connect_task", "_recover_task"):
            task = getattr(self, task_attr)
            if task is not None and not task.done():
                task.cancel()
                with suppress(asyncio.CancelledError, Exception):
                    await task
            setattr(self, task_attr, None)

        if self._state is LifecycleState.DISCONNECTED and not self._claimed:
            # Idempotent no-op: nothing claimed, nothing to release.
            return

        self._emit(LifecycleState.CLOSING, reason=reason)
        try:
            await self._mech.release()
        except Exception:  # noqa: BLE001 — release must not raise out of teardown
            _logger.debug("lifecycle.release.error", exc_info=True)
        finally:
            self._claimed = False
            self._cooldown_deadline = None
            self._cooldown_total_s = None
            self._connecting_started = None
            self._recovery_attempt = None
            self._emit(LifecycleState.DISCONNECTED)

    # ------------------------------------------------------------------
    # soft_reconnect() — CONNECTED → RECOVERING → CONNECTED (#1217)
    # ------------------------------------------------------------------

    async def soft_reconnect(self) -> None:
        # Coalesce concurrent recovery onto the in-flight one.
        if self._recover_task is not None and not self._recover_task.done():
            await self._recover_task
            return
        self._recover_task = asyncio.ensure_future(self._run_recover())
        try:
            await self._recover_task
        finally:
            if self._recover_task is not None and self._recover_task.done():
                self._recover_task = None

    async def _run_recover(self) -> None:
        last_exc: BaseException | None = None
        for attempt in range(1, self._max_recovery_attempts + 1):
            self._recovery_attempt = attempt
            self._emit(
                LifecycleState.RECOVERING,
                reason=LifecycleErrorReason.DATA_WATCHDOG_STALL,
                recovery_attempt=attempt,
                recovery_max=self._max_recovery_attempts,
            )
            if attempt > 1:
                backoff = self._recovery_backoff_s[
                    min(attempt - 2, len(self._recovery_backoff_s) - 1)
                ]
                await asyncio.sleep(backoff)
            try:
                await self._mech.soft_reconnect_once()
            except Exception as exc:  # noqa: BLE001 — count + retry/exhaust
                last_exc = exc
                _logger.warning(
                    "lifecycle.soft_reconnect.attempt_failed",
                    extra={"attempt": attempt, "max": self._max_recovery_attempts},
                )
                continue
            else:
                self._recovery_attempt = None
                self._emit(LifecycleState.CONNECTED)
                return

        # Exhausted: route through CLOSING with full release (T11).
        self._recovery_attempt = None
        self._emit(
            LifecycleState.CLOSING,
            reason=LifecycleErrorReason.RECOVERY_EXHAUSTED,
        )
        with suppress(Exception):
            await self._mech.release()
        self._claimed = False
        self._emit(LifecycleState.DISCONNECTED)
        raise RigplaneConnectionError(
            f"Soft reconnect exhausted after {self._max_recovery_attempts} attempts"
        ) from last_exc

    # ------------------------------------------------------------------
    # scan() — presence-probe only; no session
    # ------------------------------------------------------------------

    async def scan(
        self,
        targets: list[str] | None = None,
        *,
        timeout: float = 3.0,
    ) -> list[RadioPresence]:
        # Scan is fire-and-forget w.r.t. the session: if we are connected we do
        # NOT change the observable session state, we just probe.  If idle, we
        # surface a SCANNING → DISCONNECTED blip for the UI.
        was_connected = self._state is LifecycleState.CONNECTED
        if not was_connected:
            self._emit(LifecycleState.SCANNING)
        try:
            return await self._mech.scan(targets, timeout=timeout)
        finally:
            if not was_connected:
                self._emit(LifecycleState.DISCONNECTED)

    # ------------------------------------------------------------------
    # Async context manager
    # ------------------------------------------------------------------

    async def __aenter__(self) -> "CoreRadioSessionLifecycle":
        try:
            await self.connect()
        except BaseException:
            # Hole 2: __aenter__ failure must still release any partial claim.
            await self.disconnect()
            raise
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: object,
    ) -> bool | None:
        await self.disconnect()
        return None
