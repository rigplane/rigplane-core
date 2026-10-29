"""The managed-key submission choreography shared by every keying ingress.

Extracted from ``rigctld`` (MOR-3821 P0c): the per-connection key ordering
gate (``ManagedPttReady``), the key/unkey submission start
(``ManagedPttTracker.begin_key``), the managed-PTT / managed-tuner execution
through the ordered command queue, and the fail-soft session release.
``rigctld`` calls this module; the old copies there are deleted. A future
TCI adapter uses the same entry points with ``source="tci"`` — so no
``rigctld`` type appears in any signature here: the caller supplies the
owner id, the command-queue handle, the expiry, and (from P0b-capability
on) ``before_on``, and maps the neutral outcomes below to its own wire
errors.

It lives in ``runtime`` next to ``managed_tx_ingress``: the ingress module
answers whether an ingress may hold a lease, this one drives one
key/unkey through the queue and the authority and releases it.
"""

from __future__ import annotations

import asyncio
import inspect
import logging
from collections.abc import Awaitable, Callable
from typing import Any

from ..core.command_dispatch import prepare_command_intent
from ..core.state_pipeline_contracts import CommandSource
from ._poller_types import CommandQueue
from .managed_tx_authority import ManagedTxAuthority, ManagedTxSubmission
from .managed_tx_state import ManagedTxOutcome

logger = logging.getLogger(__name__)

__all__ = [
    "ManagedKeyRefused",
    "ManagedKeySetupError",
    "ManagedPttReady",
    "ManagedPttTracker",
    "execute_managed_ptt",
    "execute_managed_tuner",
    "release_managed_session_tx",
]


class ManagedKeyRefused(Exception):
    """A managed key/unkey was refused before any RF effect.

    Nothing reached the wire. The caller answers its requester with a
    refusal (``rigctld`` ``ERJCTED``, TCI ``trx:0,false;``).
    """


class ManagedKeySetupError(Exception):
    """The managed submission was built wrongly: no submission, no releasable
    ready, no expiry, or no connection generation.

    A programming error in the caller, never a radio outcome.
    """


class ManagedPttReady(asyncio.Future[None]):
    """One key-down's admission gate against a later unkey (MOR-1014).

    The connection's revocation event marks a key superseded when an unkey
    lands before the key validated; the queue turn releases the queue side.
    Both halves must arrive before the admission resolves.
    """

    def __init__(
        self,
        revocation: asyncio.Event,
        *,
        expires_at_monotonic: float,
        connection_generation: object,
    ) -> None:
        super().__init__()
        self._revocation = revocation
        self.expires_at_monotonic = expires_at_monotonic
        self.connection_generation = connection_generation
        self.validation_committed = False
        self.queue_released = False

    @property
    def superseded(self) -> bool:
        return self._revocation.is_set() and not self.validation_committed

    def set_result(self, result: None) -> None:
        if self.superseded:
            return
        self.validation_committed = True
        self._resolve_if_ready()

    def release_queue(self) -> None:
        """Release the shared ordering gate without bypassing validation."""
        self.queue_released = True
        self._resolve_if_ready()

    def _resolve_if_ready(self) -> None:
        if self.validation_committed and self.queue_released and not self.done():
            super().set_result(None)

    def commit_failure(self) -> bool:
        if self.superseded:
            return False
        self.validation_committed = True
        return True


class _PttQueueReady(asyncio.Future[None]):
    """Translate the queue turn into the pre-registered ingress readiness."""

    def __init__(self, release: Callable[[], None]) -> None:
        super().__init__()
        self._release = release

    def set_result(self, result: None) -> None:
        self._release()
        super().set_result(result)


class ManagedPttTracker:
    """Per-connection managed-key ordering for one keying ingress.

    A key-down registers its admission against the connection's current
    revocation; an unkey marks every still-unvalidated key superseded and
    opens a fresh revocation for the next key. One tracker lives as long
    as the connection whose keys it orders: ``rigctld`` mints one per
    accepted socket, a future TCI adapter one per client connection.
    """

    def __init__(self) -> None:
        self._revocation = asyncio.Event()

    def begin_key(
        self,
        authority: ManagedTxAuthority,
        *,
        owner: str,
        on: bool,
        expires_at_monotonic: float,
        connection_generation: object,
        before_on: Callable[[], Awaitable[bool]] | None = None,
    ) -> tuple[asyncio.Task[ManagedTxSubmission], ManagedPttReady | None]:
        """Start one managed key/unkey submission with membership installed.

        ``before_on`` (MOR-3821, spec 7.3a) runs inside the admitted key,
        after the intent commits to ``PTT(owner)`` and before the PTT_ON
        effect dispatches; a keying ingress that prepares the route (TCI)
        passes it, ``rigctld`` passes nothing.
        """
        if not on:
            self._revocation.set()
            self._revocation = asyncio.Event()
            ready: ManagedPttReady | None = None
        else:
            ready = ManagedPttReady(
                self._revocation,
                expires_at_monotonic=expires_at_monotonic,
                connection_generation=connection_generation,
            )
        expires_at = None if ready is None else ready.expires_at_monotonic
        extra: dict[str, Any] = {}
        if before_on is not None:
            extra["before_on"] = before_on
        # One call site: without a hook (the rigctld path) the authority
        # sees exactly the origin/main arguments (MOR-3821 fix round).
        submission = authority.start_ptt_submission(
            on,
            owner,
            ready=ready,
            expires_at_monotonic=expires_at,
            **extra,
        )
        return submission, ready


async def execute_managed_ptt(
    *,
    authority: ManagedTxAuthority,
    queue: CommandQueue,
    owner: str,
    on: bool,
    submission: asyncio.Task[Any] | None,
    ready: asyncio.Future[Any] | None,
    source: CommandSource,
    provider_generation: int | None,
) -> ManagedTxSubmission:
    """Drive one managed key/unkey through the ordered command queue.

    Mirrors the ``rigctld`` managed-PTT shape (MOR-1014): the unkey joins
    the in-flight submission when the connection tracts one, else submits
    directly; the key-down enqueues an ordered entry whose queue turn
    releases the pre-registered admission, then joins the submission and
    the entry. A refused key raises :class:`ManagedKeyRefused`; a
    wrongly-built submission raises :class:`ManagedKeySetupError`;
    ``CommandError``/``ControllerError`` from the authority propagate for
    the caller to map. Cancellation of this task itself propagates; a
    cancellation inherited from an awaited submission is a refusal.
    """
    assert authority is not None
    assert queue is not None
    if not isinstance(owner, str) or not owner:
        raise ManagedKeySetupError("managed PTT requires a non-empty str owner")
    completion: asyncio.Future[None] | None = None
    task = asyncio.current_task()
    cancelling = 0 if task is None else task.cancelling()
    try:
        if not on:
            receipt = (
                await submission
                if submission is not None
                else await authority.submit_ptt(False, owner)
            )
        else:
            loop = asyncio.get_running_loop()
            if submission is None:
                raise ManagedKeySetupError("managed PTT key requires a submission")
            release_queue = getattr(ready, "release_queue", None)
            if not callable(release_queue):
                raise ManagedKeySetupError("managed PTT key requires a releasable ready")
            queue_ready = _PttQueueReady(release_queue)
            completion = loop.create_future()
            expires_at = getattr(ready, "expires_at_monotonic", None)
            if not isinstance(expires_at, float):
                raise ManagedKeySetupError("managed PTT key requires an expiry")
            connection_generation = getattr(ready, "connection_generation", None)
            if connection_generation is None:
                raise ManagedKeySetupError(
                    "managed PTT key requires a connection generation"
                )
            queue.put_ordered(
                None,
                future=completion,
                source=source,
                session_id=owner,
                expires_at_monotonic=expires_at,
                provider_generation=provider_generation,
                connection_generation=connection_generation,
                positive_tx_ready=queue_ready,
                positive_tx_submission=submission,
            )
            if ready is not None and not ready.done():
                ready.set_result(None)
            receipt = await submission
            await completion
    except asyncio.CancelledError as exc:
        if task is not None and task.cancelling() > cancelling:
            raise
        raise ManagedKeyRefused("managed PTT submission was cancelled") from exc
    finally:
        if completion is not None:
            if not completion.done():
                completion.cancel()
            await asyncio.gather(completion, return_exceptions=True)

    if on and receipt.outcome is ManagedTxOutcome.REJECTED:
        raise ManagedKeyRefused(f"managed PTT ON refused: {receipt.outcome}")
    return receipt


async def execute_managed_tuner(
    *,
    radio: Any,
    queue: CommandQueue,
    source: CommandSource,
    session_id: str | None,
    command_id: str,
    on: bool,
    expires_at_monotonic: float,
    connection_generation: object,
    provider_generation: int | None,
) -> None:
    """Normalize one tuner function into the shared ordered leaf.

    The tuner cycle is admitted through the same ordered queue as a key,
    with the caller's expiry bounding the wait. ``TimeoutError`` and
    ``CommandError`` propagate for the caller to map.
    """
    assert queue is not None
    normalized = prepare_command_intent(
        radio,
        "set_tuner_status",
        {"value": 1 if on else 0},
        source=source,
        command_id=command_id,
        session_id=session_id,
    )
    loop = asyncio.get_running_loop()
    completion: asyncio.Future[None] = loop.create_future()
    queue.put_ordered(
        normalized,
        future=completion,
        command_id=normalized.id,
        source=normalized.source,
        session_id=session_id,
        expires_at_monotonic=expires_at_monotonic,
        provider_generation=provider_generation,
        connection_generation=connection_generation,
    )
    await asyncio.wait_for(
        completion, timeout=max(0.0, expires_at_monotonic - loop.time())
    )


async def release_managed_session_tx(
    release: Callable[[str], Awaitable[Any]] | None,
    session_id: str,
    *,
    handback_timeout_s: float,
) -> None:
    """Give a departing client's managed TX lease back at teardown.

    The connection id is the owner identity the handler keys under
    (MOR-1014), and this is the teardown hook that makes it releasable: a
    client that drops mid-over would otherwise leave its lease standing
    until the max key-down watchdog expires, blocking every other client
    meanwhile.

    Probed rather than called outright, and never allowed to raise an
    ordinary exception: ``release`` is an injected hook, and a release
    that predates this hook must keep working. Failure here cannot be
    reported to a socket that is already gone, and must not skip the
    rest of teardown.

    Cancellation is the one thing it does not absorb twice: the handback
    survives the cancellation a server stop sends, and a second one ends
    it. Nothing recovers from that by itself — the lease stays held with
    its release obligation unsettled (``RELEASE_REQUIRED``) and no
    watchdog re-arms behind it, so it is a condition for the
    uncertain-shutdown diagnostics to surface (MOR-1015), not one that
    times itself out. The caller's teardown is written to close the socket
    either way.

    Each wait on the handback lasts at most ``handback_timeout_s``
    (MOR-2875); past it the wait is abandoned, not the handback, and the
    unconfirmed unkey is logged at ERROR.
    """
    if release is None:
        return
    try:
        result = release(session_id)
        if not inspect.isawaitable(result):
            return
        handback = asyncio.ensure_future(result)
        try:
            await asyncio.wait_for(
                asyncio.shield(handback), timeout=handback_timeout_s
            )
        except asyncio.CancelledError:
            # Shutdown reached this session mid-handback. Every other step
            # of a teardown can be abandoned; this one cannot, and nothing
            # downstream would clean up after it. An abandoned handback
            # leaves the lease held with its release obligation unsettled
            # (``RELEASE_REQUIRED``, an unfinished WRITE_OFF) and no
            # watchdog behind it: ``_begin_release`` clears
            # ``watchdog_deadline``, and ``tick`` arms the max key-down
            # branch only while no release is pending. That state does not
            # time itself out — it is for the uncertain-shutdown
            # diagnostics to surface (MOR-1015). The release is in flight
            # anyway, so it gets a second wait with the same bound; a
            # second cancellation ends it. Swallowed rather than
            # re-raised so the socket close below still runs (MOR-1014).
            try:
                await asyncio.wait_for(
                    asyncio.shield(handback), timeout=handback_timeout_s
                )
            except asyncio.CancelledError:
                logger.error(
                    "session %s: managed TX handback cancelled a second time "
                    "before it settled; unkey not confirmed",
                    session_id,
                )
                handback.cancel()
                raise
    except TimeoutError:
        logger.error(
            "session %s: managed TX handback did not settle within %.1fs; "
            "unkey not confirmed",
            session_id,
            handback_timeout_s,
        )
    except Exception:
        logger.warning(
            "session %s: managed TX release failed", session_id, exc_info=True
        )
