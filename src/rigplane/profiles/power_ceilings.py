"""Rated-power ceilings resolved from profile data (MOR-3634).

Pure profile-data arithmetic in the spirit of ``profiles/control_domain.py``:
no backend, model name or ``if model == X`` branch lives here. A profile
declares per-band ceilings in ``[power.ceilings_w]`` (band name -> watts, the
band names being the ``[[freq_ranges.ranges.bands]]`` entries) and per-source
ceilings in ``[power.sources]`` (source name -> watts). The loader validates
both against ``[power].max_watts`` and the declared bands; this module turns
them into the single effective watt ceiling that the existing power-limit
seats consume: ``command_service.py: resolve_power_level_target`` (admission)
and the Yaesu backend's ``set_power`` (the wire).

The ceiling only CAPS a write. The normalized 0.0-1.0 power domain stays a
fraction of the flat ``[power].max_watts`` on every band, so a stored,
echoed or projected level never changes meaning when the band does.

Resolution rules, shared by band and source:

- a known band (a frequency inside a declared band that carries a row) or a
  known source names its own row;
- anything unknown (no frequency evidence, a frequency no declared band
  covers, a source the state has not observed) resolves to the LOWEST
  declared row of that table, never the widest: an over-rated write is the
  failure this mechanism exists to prevent;
- band and source tables combine as their minimum;
- a profile declaring neither table resolves ``None`` and every caller keeps
  today's flat-``max_watts`` behaviour.

Which frequencies define the band (:func:`band_frequencies_hz_from_snapshot`
for the canonical store, :func:`observed_band_freqs_hz` for the radio):

- a KNOWN, fresh ``global.tx_state.tx_target`` names the transmitting
  receiver. Its band is the NEWEST fresh evidence about that receiver's
  transmit frequency: the frequency the target read embedded, or (only when
  the target does not name a VFO slot) the receiver's own polled frequency.
  Equal timestamps keep both, so the lower ceiling applies;
- a ``tx_target`` that is observed but unknown or stale, or a transmit
  frequency nothing fresh supports, is unknown;
- a snapshot that never observed ``tx_target`` proves nothing about the
  transmit route, so every candidate transmitting receiver counts: MAIN
  alone on a single-receiver profile, MAIN and SUB otherwise, and a
  receiver with no fresh frequency is unknown.

The result is a tuple of frequencies; the ceiling is the minimum over them.
Stale frequency fields count as unobserved. The canonical store is the only
authority when attached (production polling feeds it); the compatibility
mirror serves store-less radios. No shipped observation names the power
source yet, so callers pass ``power_source=None`` and the lower source row
applies.

These readings are an admission-time ESTIMATE (they decide the expected
readback). The authority at the wire is the backend, which re-reads the
transmit band from the radio under its own serialization lock.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from ..core.state_pipeline_contracts import FieldFamily, FieldScope, VfoSlot
from ..core.state_store import FreshnessState

__all__ = [
    "band_frequencies_hz_from_snapshot",
    "has_band_ceilings",
    "has_power_ceilings",
    "observed_band_freqs_hz",
    "resolve_power_ceiling_watts",
]


def _watts_table(profile: Any, attr: str) -> dict[str, int] | None:
    table = getattr(profile, attr, None)
    if not isinstance(table, dict) or not table:
        return None
    return {
        name: watts
        for name, watts in table.items()
        if isinstance(watts, int) and not isinstance(watts, bool) and watts > 0
    }


def has_band_ceilings(profile: Any) -> bool:
    """True when the profile declares a per-band ceiling table.

    Only a band table needs the transmit band: a source-only profile
    resolves its ceiling from the source row alone.
    """
    return _watts_table(profile, "power_ceilings_w") is not None


def has_power_ceilings(profile: Any) -> bool:
    """True when the profile declares a band or a source ceiling table."""
    return (
        _watts_table(profile, "power_ceilings_w") is not None
        or _watts_table(profile, "power_sources_w") is not None
    )


def _band_ceiling_watts(profile: Any, ceilings: dict[str, int], freq_hz: Any) -> int:
    if isinstance(freq_hz, bool) or not isinstance(freq_hz, int) or freq_hz <= 0:
        return min(ceilings.values())
    matched: list[int] = []
    for freq_range in getattr(profile, "freq_ranges", ()) or ():
        for band in getattr(freq_range, "bands", ()) or ():
            name = getattr(band, "name", None)
            if name not in ceilings:
                continue
            start = getattr(band, "start", 0)
            end = getattr(band, "end", 0)
            if (
                isinstance(start, int)
                and isinstance(end, int)
                and start <= freq_hz <= end
            ):
                matched.append(ceilings[name])
    return min(matched) if matched else min(ceilings.values())


def resolve_power_ceiling_watts(
    profile: Any,
    *,
    freqs_hz: Sequence[int | None] = (),
    power_source: str | None = None,
) -> int | None:
    """The rated-power ceiling in watts for the given bands and source.

    *freqs_hz* holds every frequency that may define the transmit band
    (``None`` for a candidate with no evidence); the band ceiling is the
    minimum over them, and an empty sequence is an unknown band. ``None``
    when the profile declares neither ``[power.ceilings_w]`` nor
    ``[power.sources]`` -- today's flat-``max_watts`` behaviour. See the
    module docstring for the unknown-band/unknown-source rules.
    """
    ceilings = _watts_table(profile, "power_ceilings_w")
    sources = _watts_table(profile, "power_sources_w")
    if ceilings is None and sources is None:
        return None

    candidates: list[int] = []
    if ceilings is not None:
        candidates.extend(
            _band_ceiling_watts(profile, ceilings, freq_hz)
            for freq_hz in (tuple(freqs_hz) or (None,))
        )
    if sources is not None:
        if isinstance(power_source, str) and power_source in sources:
            candidates.append(sources[power_source])
        else:
            candidates.append(min(sources.values()))

    max_watts = getattr(profile, "max_watts", None)
    if isinstance(max_watts, int) and not isinstance(max_watts, bool):
        if max_watts > 0:
            candidates.append(max_watts)
    return min(candidates)


def _positive_int(value: Any) -> int | None:
    if isinstance(value, int) and not isinstance(value, bool) and value > 0:
        return value
    return None


def _known_tx_target(value: Any) -> tuple[str, str | None, int | None] | None:
    """``(receiver key, slot, embedded frequency)`` of a KNOWN ``tx_target``."""
    if isinstance(value, Mapping):
        status = value.get("status")
        receiver = value.get("receiver")
        slot = value.get("slot")
        frequency = value.get("frequencyHz")
    else:
        status = getattr(value, "status", None)
        receiver = getattr(value, "receiver", None)
        slot = getattr(value, "slot", None)
        frequency = getattr(value, "frequency_hz", None)
    if status != "known" or receiver not in ("MAIN", "SUB"):
        return None
    return (
        "sub" if receiver == "SUB" else "main",
        slot if isinstance(slot, str) else None,
        _positive_int(frequency),
    )


def _receiver_keys(receiver_count: int | None) -> tuple[str, ...]:
    """The receivers that may transmit when the TX route is not proven."""
    if isinstance(receiver_count, int) and receiver_count == 1:
        return ("main",)
    return ("main", "sub")


def band_frequencies_hz_from_snapshot(
    snapshot: Any, *, receiver_count: int | None = None
) -> tuple[int | None, ...]:
    """The frequencies that may define the transmit band in one snapshot.

    See the module docstring for the rules. ``(None,)`` / a ``None`` entry
    marks a candidate with no fresh evidence (the lowest ceiling applies).
    """
    tx_observed = False
    tx_known: tuple[str, str | None, int | None] | None = None
    tx_observed_at = 0.0
    freqs: dict[str, tuple[int, float]] = {}
    for field in getattr(snapshot, "fields", ()) or ():
        path = getattr(field, "path", None)
        if path is None:
            continue
        stale = getattr(field, "freshness", None) is FreshnessState.STALE
        observed_at = getattr(field, "last_observed_monotonic", 0.0)
        if (
            path.scope is FieldScope.GLOBAL
            and path.family is FieldFamily.TX_STATE
            and path.name == "tx_target"
        ):
            tx_observed = True
            if not stale:
                tx_known = _known_tx_target(getattr(field, "value", None))
                tx_observed_at = observed_at
            continue
        if (
            path.scope is FieldScope.RECEIVER
            and path.family is FieldFamily.FREQ_MODE
            and path.name == "freq_hz"
            and path.slot in (None, VfoSlot.ACTIVE)
        ):
            freq_hz = _positive_int(getattr(field, "value", None))
            receiver_id = str(getattr(path, "receiver_id", ""))
            receiver_key = "sub" if receiver_id in ("1", "sub") else "main"
            if freq_hz is not None and not stale:
                freqs[receiver_key] = (freq_hz, observed_at)
    if tx_observed:
        if tx_known is None:
            return (None,)
        receiver_key, slot, embedded = tx_known
        evidence: list[tuple[int, float]] = []
        if embedded is not None:
            evidence.append((embedded, tx_observed_at))
        if slot is None and receiver_key in freqs:
            # Only an unnamed slot means "the receiver's own VFO", which is
            # what the receiver's polled frequency describes.
            evidence.append(freqs[receiver_key])
        if not evidence:
            return (None,)
        newest = max(observed_at for _, observed_at in evidence)
        return tuple(sorted({hz for hz, at in evidence if at == newest}))
    return tuple(
        freqs[key][0] if key in freqs else None
        for key in _receiver_keys(receiver_count)
    )


def _mirror_band_frequencies_hz(
    radio: Any, receiver_count: int | None
) -> tuple[int | None, ...]:
    state = getattr(radio, "radio_state", None)
    return tuple(
        _positive_int(getattr(getattr(state, key, None), "freq", None))
        for key in _receiver_keys(receiver_count)
    )


def observed_band_freqs_hz(radio: Any) -> tuple[int | None, ...]:
    """The transmit-band candidate frequencies from a radio's observations.

    The canonical StateStore the web layer attaches as ``radio._state_store``
    (the only thing production polling feeds) is the sole authority when
    attached: its answer stands even when empty (an empty or provider-reset
    store must apply the unknown-band ceiling, not resurrect the
    compatibility mirror, which may hold the previous connection's
    frequency). Store-less radios fall back to the ``radio_state`` mirror.
    An admission-time estimate; the backend re-reads the band at the wire.
    """
    receiver_count = getattr(getattr(radio, "profile", None), "receiver_count", None)
    store = getattr(radio, "_state_store", None)
    snapshot = getattr(store, "snapshot", None)
    if callable(snapshot):
        return band_frequencies_hz_from_snapshot(
            snapshot(), receiver_count=receiver_count
        )
    return _mirror_band_frequencies_hz(radio, receiver_count)
