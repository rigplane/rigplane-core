"""Additive ``txSafety`` evidence for the Web runtime payload (MOR-1015).

A read-only projection of the managed TX supervisor's own snapshot into the
``/api/v1/runtime`` diagnostics document, in the shape every other block there
already uses (``_runtime_bridge_payload``, ``_state_acquisition_diagnostics_payload``).
It states what the supervisor knows and refuses to state anything else:

* **An ACK is not RF.** ``rfConfirmed`` is true only in :attr:`TxPhase.KEYED` —
  the phase the supervisor reaches when a causally-ordered PTT *observation*
  confirms the key. A request the supervisor merely answered ``ACCEPTED``, and
  a write the provider merely acknowledged, leave ``keyRequested`` true and
  ``rfConfirmed`` false; ``radioTx`` reports the last observation and is
  ``"unknown"`` when there has been none.
* **A watchdog nobody armed is not coverage.** ``watchdog.armed`` reports the
  live deadline, never ``TxSafetySnapshot.watchdog_enabled``: that flag means
  configured-and-driven, and it stays true across a release, which clears the
  deadline (``_begin_release``) while ``tick`` re-arms the max key-down branch
  only when no release is pending. A release-only lease therefore reads
  "enabled" with nothing that can fire (MOR-1204). The flag still ships, under
  its own name (``watchdog.driven``), because "is anything ticking this
  supervisor" is exactly what tells a stalled durable OFF apart from one that
  will be retried.
* **Unmanaged is stated, not implied.** A radio publishing no supervisor —
  Yaesu CAT, the rigctld-client backend, a radio outside a connect session, or
  one started with ``RIGPLANE_MANAGED_TX=0`` — reports ``status: "unmanaged"``
  and no safety fields at all, rather than a block of zeroes and ``false``\\ s
  that reads exactly like a supervised rig at rest (MOR-1225). A ``managed_tx``
  accessor that *raises* is reported as ``"unreadable"``, never resolved
  towards "unmanaged", per :class:`ManagedTxCapable`.
* **The uncertain shutdown is named.** A durable OFF obligation that nothing
  can advance — the terminal MOR-1014 leaves behind when a twice-cancelled
  rigctld handback strands a lease at ``RELEASE_REQUIRED`` with an unsettled
  ``WRITE_OFF`` and no watchdog — surfaces as ``uncertainShutdown`` with a
  reason, because it does not time itself out and nothing downstream reports
  it.

Additive only: this is a new key on an existing document; no existing field
changes name, type or meaning.
"""

from __future__ import annotations

import time
from typing import Any

from rigplane.core.state_pipeline_contracts import CommandSource
from rigplane.core.tx_safety import TxPhase, TxSafetySnapshot
from rigplane.runtime.managed_tx_ingress import (
    refuse_key_without_owner,
    resolve_supervisor,
)

__all__ = ["build_tx_safety_payload"]

#: Ingresses an operator can key from. ``internal_policy``/``diagnostics``/
#: ``test`` are omitted: they are not doors anyone transmits through.
_INGRESSES: tuple[CommandSource, ...] = ("websocket", "http", "rigctld", "public_api")

#: Stands in for a live session id so the probe below measures the *ingress*,
#: not the absence of a session. Never used to key anything.
_PROBE_SESSION = "txsafety-diagnostics-probe"


def build_tx_safety_payload(
    radio: object | None, *, now: float | None = None
) -> dict[str, Any]:
    """Build the additive ``txSafety`` block for one radio.

    ``now`` defaults to :func:`time.monotonic`, the clock a production
    ``ManagedRadioRuntime`` is built with; tests driving a supervisor on a fake
    clock pass their own so the deadline arithmetic stays comparable.
    """
    if radio is None:
        return {"status": "no_radio", "ingress": {}}
    try:
        supervisor = resolve_supervisor(radio)
    except Exception as exc:  # noqa: BLE001 — a broken accessor is a finding
        return {"status": "unreadable", "error": type(exc).__name__, "ingress": {}}
    if supervisor is None:
        return {"status": "unmanaged", "ingress": _ingress_view(radio, managed=False)}
    snapshot = getattr(supervisor, "tx_snapshot", None)
    ingress = _ingress_view(radio, managed=True)
    if not isinstance(snapshot, TxSafetySnapshot):
        return {"status": "no_snapshot", "ingress": ingress}
    view = _snapshot_view(snapshot, time.monotonic() if now is None else now)
    return {"status": "managed", **view, "ingress": ingress}


def _ingress_view(radio: object, *, managed: bool) -> dict[str, dict[str, bool]]:
    """Which ingresses reach the supervisor, and which are refused outright.

    Answered by the key path's own predicate
    (:func:`~rigplane.runtime.managed_tx_ingress.refuse_key_without_owner`), so
    an ingress cannot drift into looking supported here while the gate refuses
    it. On an unmanaged rig nothing is refused and nothing is supervised — the
    legacy unsupervised write is what those keys take.
    """
    view: dict[str, dict[str, bool]] = {}
    for source in _INGRESSES:
        refused = refuse_key_without_owner(radio, source, _PROBE_SESSION)
        view[source] = {"supervised": managed and not refused, "keyRefused": refused}
    return view


def _snapshot_view(snapshot: TxSafetySnapshot, now: float) -> dict[str, Any]:
    owed = snapshot.release_reason is not None
    attempt = snapshot.active_attempt
    overdue = attempt is not None and (
        now >= attempt.started_at_monotonic + attempt.timeout_seconds
    )
    deadline = snapshot.watchdog_deadline_monotonic
    owner = snapshot.owner
    return {
        "phase": snapshot.phase.value,
        "owner": (
            None
            if owner is None
            else {"source": owner.source.value, "sessionId": owner.session_id}
        ),
        "lease": {"held": snapshot.lease_id is not None, "id": snapshot.lease_id},
        # Intent and RF truth, deliberately two fields: the supervisor accepts
        # a key long before an observation confirms one.
        "keyRequested": snapshot.lease_id is not None and not owed,
        "rfConfirmed": snapshot.phase is TxPhase.KEYED,
        "radioTx": snapshot.radio_tx.value,
        "externalConflict": snapshot.external_conflict,
        "provider": {
            "generation": snapshot.provider_generation,
            "ready": snapshot.provider_ready,
        },
        "watchdog": {
            "armed": deadline is not None,
            "deadlineMonotonic": deadline,
            "secondsRemaining": None if deadline is None else round(deadline - now, 3),
            "driven": snapshot.watchdog_enabled,
        },
        "durableOff": {
            "owed": owed,
            "requestedReason": _reason(snapshot.release_reason),
            "terminalReason": _reason(snapshot.terminal_release_reason),
            "attempts": snapshot.release_attempt_count,
            "lastError": snapshot.release_last_error,
        },
        "activeAttempt": (
            None
            if attempt is None
            else {
                "kind": attempt.kind.value,
                "startedAtMonotonic": attempt.started_at_monotonic,
                "timeoutSeconds": attempt.timeout_seconds,
                "overdue": overdue,
            }
        ),
        "uncertainShutdown": (reason := _uncertain(snapshot, attempt, overdue, owed))
        is not None,
        "uncertainReason": reason,
    }


def _uncertain(
    snapshot: TxSafetySnapshot, attempt: Any, overdue: bool, owed: bool
) -> str | None:
    """Why an owed durable OFF has nothing left that can complete it.

    ``None`` while the obligation still has a path forward: an attempt inside
    its own deadline may yet settle, and a driven supervisor re-attempts a
    parked one on the next ``tick``. Both terms are load-bearing — the
    watchdog is not, because ``_begin_release`` has already cleared its
    deadline by the time any of this is reachable.
    """
    if not owed or snapshot.lease_id is None:
        return None
    if overdue:
        # The settlement the runtime owes this attempt never came, and
        # ``_service_release`` will not start another while it is active.
        return "unsettled_attempt"
    if attempt is None and not snapshot.watchdog_enabled:
        return "no_driver"
    return None


def _reason(value: object) -> str | None:
    return None if value is None else str(value)
