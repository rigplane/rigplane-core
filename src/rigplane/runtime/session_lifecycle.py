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

from collections.abc import Callable
from dataclasses import dataclass
from enum import Enum
from typing import Protocol, runtime_checkable

__all__ = [
    "LifecycleErrorReason",
    "LifecycleEvent",
    "LifecycleState",
    "LifecycleStatus",
    "RadioPresence",
    "RadioSessionLifecycle",
]

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
