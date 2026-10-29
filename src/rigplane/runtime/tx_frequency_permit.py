"""PTT-on TX frequency permit gate (MOR-3632).

The adversary is an operator typo or an external client (rigctld, WSJT-X, a
tracker) that sets a TX frequency and keys up; the radio firmware stays the
last line of defence against everything else.  The permit reads only what
the server already knows and publishes — the split-aware ``tx_target``
field — and the profile's band rows (non-``rx_only`` rows permit TX).  No new
timestamps, TTLs, generations, or response matching: the field already
carries its own TTL, and the state after our own writes counts as current.
A front-panel retune inside one poll interval is NOT covered.

This lives apart from :mod:`rigplane.runtime.tx_interlock` on purpose: that
module is imported by the poller types that ``rigplane.core`` and
``rigplane.audio`` reach, so it may not depend on :mod:`rigplane.profiles`;
the gate needs :class:`~rigplane.profiles.RadioProfile`.
"""

from __future__ import annotations

from collections.abc import Mapping

from rigplane.core.state_pipeline_contracts import FieldPath
from rigplane.core.state_store import FreshnessState, StateSnapshot, StateStore
from rigplane.core.tx_target import (
    KnownTxTarget,
    TxTarget,
    UnknownTxTarget,
    evaluate_tx_frequency_permit,
    tx_target_from_dict,
)
from rigplane.profiles import RadioProfile
from rigplane.runtime.tx_interlock import TxInterlockRefusal

__all__ = ["current_tx_target", "tx_frequency_permit_refusal"]

_TX_TARGET_PATH = FieldPath.global_("tx_state", "tx_target")


def current_tx_target(snapshot: StateSnapshot) -> TxTarget | None:
    """Return the FRESH published ``tx_state.tx_target`` value, else None.

    None means "the server does not currently know the TX target": the field
    was never observed, has aged stale under its own TTL, or carries a value
    that is not a valid ``TxTarget``.  A stored mapping (wire/golden form)
    is parsed through the same ``tx_target_from_dict`` contract the pipeline
    uses; an unparseable value reads as unknown rather than raising.
    """

    try:
        field = snapshot.field(_TX_TARGET_PATH)
    except KeyError:
        return None
    if field.freshness is not FreshnessState.FRESH:
        return None
    value = field.value
    if isinstance(value, (KnownTxTarget, UnknownTxTarget)):
        return value
    if isinstance(value, Mapping):
        try:
            return tx_target_from_dict(value)
        except ValueError:
            return None
    return None


_PERMIT_REASON_CODES = {
    "receive-only-band": "receive_only_band",
    "outside-tx-range": "outside_tx_range",
}


def tx_frequency_permit_refusal(
    profile: object, state_store: object
) -> TxInterlockRefusal | None:
    """The one PTT-on frequency-permit gate both ingress paths call.

    Returns None when PTT-on may proceed, else a :class:`TxInterlockRefusal`
    carrying a stable ``reason_code`` (``receive_only_band`` or
    ``outside_tx_range``).  Band-only: it decides solely from a FRESH KNOWN
    TX target with a frequency.  Not applicable — returns None — when the
    radio has no resolved :class:`RadioProfile` (test doubles, profile-less
    providers), the profile declares no band rows at all, or the server does
    not know the target (unknown, stale, malformed, or a known target
    without a frequency); the unknown-target guard (MOR-3633) owns that
    case, so a radio that never publishes a target keeps keying.  PTT-off never reaches
    this gate: callers invoke it only on the key-down path.
    """

    if not isinstance(profile, RadioProfile):
        return None
    bands = tuple(band for rng in profile.freq_ranges for band in rng.bands)
    if not bands:
        return None
    target = (
        current_tx_target(state_store.snapshot())
        if isinstance(state_store, StateStore)
        else None
    )
    decision = evaluate_tx_frequency_permit(bands, target)
    if decision.permitted:
        return None
    assert decision.reason is not None
    reason_code = _PERMIT_REASON_CODES[decision.reason]
    if decision.reason == "receive-only-band":
        message = (
            f"PTT refused: transmit target {decision.frequency_hz} Hz is in "
            f"the receive-only band {decision.band_name!r}"
        )
    else:
        message = (
            f"PTT refused: transmit target {decision.frequency_hz} Hz is "
            "outside every transmit band of the active profile"
        )
    return TxInterlockRefusal(f"{message} ({reason_code})", reason_code=reason_code)
