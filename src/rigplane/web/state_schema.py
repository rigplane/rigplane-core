"""Canonical pydantic schema for the public radio-state WIRE payload (MOR-881).

This module is the **single source of truth** for the shape of the public web
state payload emitted by
:func:`rigplane.web.runtime_helpers.build_public_state_payload` and
:func:`~rigplane.web.runtime_helpers.build_public_state_payload_from_snapshot`.

It models the *union / superset* of the two emit paths (dataclass + snapshot):
fields that only the snapshot path emits (``dcd``, ``fieldStatus``) are marked
optional, while fields that are always present in both paths are required.

Two uses, and ONLY two:

1. The conformance test
   (``tests/web/test_state_schema_conformance.py``) validates real payloads
   from both producers against :class:`ServerStatePublic`.
2. The codegen script (``scripts/gen_state_types.py``) reads
   ``ServerStatePublic.model_json_schema()`` and
   ``StateUpdateEnvelope.model_json_schema()`` to regenerate the server-sent
   portion of ``frontend/src/lib/types/state.ts``.

**Zero-runtime-dep guarantee:** pydantic is a DEV/optional dependency. This
module imports pydantic at load time, so it MUST NOT be imported by any live
request-path module (``runtime_helpers.py`` / ``server.py``). The producers
stay dict-based and pydantic-free. Do not wire ``model_validate`` into the
live request path.
"""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field

__all__ = [
    "VfoSlotPublic",
    "ReceiverStatePublic",
    "FixedEdgePublic",
    "ScopeControlsPublic",
    "FieldStatusPublic",
    "KnownTxTargetPublic",
    "UnknownTxTargetPublic",
    "TxTargetPublic",
    "ConnectionPublic",
    "RadioHealthPublic",
    "RadioDetailPublic",
    "WsClientsPublic",
    "ServerStatePublic",
    "StateUpdateEnvelope",
]


class _Strict(BaseModel):
    """Base model with ``extra="forbid"`` so generated TS has no index signature.

    ``additionalProperties: false`` in the emitted JSON Schema suppresses the
    ``[k: string]: unknown`` index signature ``json-schema-to-typescript`` would
    otherwise add (contract-spike REPORT, Path 3 caveat).
    """

    model_config = ConfigDict(extra="forbid")


class VfoSlotPublic(_Strict):
    """One VFO slot (A or B) within a receiver."""

    freqHz: int | None = None
    mode: str | None = None
    filterNum: int | None = None
    dataMode: int | None = None


class ReceiverStatePublic(_Strict):
    """Per-receiver (``main`` / ``sub``) public state.

    Carries BOTH the slot view (``vfoA`` / ``vfoB`` / ``activeSlot``) and the
    legacy active-slot scalars (``freqHz`` / ``mode`` / ``filter`` /
    ``dataMode``). The redundancy is intentional back-compat (radio_state.py
    ``_receiver_to_dict``); the slot view is the canonical source-of-truth and
    the scalars are derived from ``activeSlot``.
    """

    # Slot view (MOR-881: previously absent from state.ts).
    vfoA: VfoSlotPublic
    vfoB: VfoSlotPublic
    activeSlot: str | None = None
    # Relative inactive-VFO readback for providers that cannot prove absolute
    # A/B identity. Additive and optional for older providers/clients.
    unselectedVfo: VfoSlotPublic | None = None

    # Legacy active-slot scalars (derived from the active slot). ``freq`` is
    # renamed to ``freqHz`` by ``_RECEIVER_KEY_MAP``; the rest pass through.
    freqHz: int | None = None
    mode: str | None = None
    filter: int | None = None
    dataMode: int | None = None

    filterWidth: int | None = None
    att: int | None = None
    preamp: int | None = None
    nb: bool | None = None
    nr: bool | None = None
    digisel: bool | None = None
    ipplus: bool | None = None
    sMeterSqlOpen: bool | None = None
    agc: int | None = None
    audioPeakFilter: int | None = None
    autoNotch: bool | None = None
    manualNotch: bool | None = None
    twinPeakFilter: bool | None = None
    filterShape: int | None = None
    agcTimeConstant: int | None = None
    # These three are normalized to float in [0, 1] by the snapshot path
    # (_normalize_public_level_snapshot_value in runtime_helpers.py).  Pydantic
    # lax mode coerces the int(0) from the dataclass path, so float is the
    # correct canonical type for both producers.
    afLevel: float | None = None
    rfGain: float | None = None
    squelch: float | None = None
    sMeter: int | None = None
    apfTypeLevel: int | None = None
    apfOn: bool | None = None
    apfFreq: int | None = None
    nrLevel: int | None = None
    pbtInner: int | None = None
    pbtOuter: int | None = None
    nbLevel: int | None = None
    digiselShift: int | None = None
    afMute: bool | None = None
    contour: int | None = None
    ifShift: int | None = None
    narrow: bool | None = None
    manualNotchFreq: int | None = None
    manualNotchWidth: int | None = None
    # notch_filter (MOR-1548): reclassified from global to receiver-scoped,
    # matching the ic7610.toml cmd29 route's own per-receiver rationale.
    notchFilter: int | None = None
    repeaterTone: bool | None = None
    repeaterTsql: bool | None = None
    toneFreq: int | None = None
    tsqlFreq: int | None = None
    repeaterShift: int | None = None

    # Snapshot-path only: ``dcd`` is the canonical squelch-open status; it is
    # also dual-published as the deprecated ``sMeterSqlOpen`` alias (MOR-466).
    # Absent in the plain ``to_dict()`` path, so optional.
    dcd: bool | None = None


class FixedEdgePublic(_Strict):
    """Scope fixed-edge sub-object."""

    rangeIndex: int = 0
    edge: int = 0
    startHz: int = 0
    endHz: int = 0


class ScopeControlsPublic(_Strict):
    """Spectrum-scope control state."""

    receiver: int | None = None
    dual: bool | None = None
    mode: int | None = None
    span: int | None = None
    edge: int | None = None
    hold: bool | None = None
    refDb: float | None = None
    speed: int | None = None
    duringTx: bool | None = None
    centerType: int | None = None
    vbwNarrow: bool | None = None
    rbw: int | None = None
    fixedEdge: FixedEdgePublic | None = None


class FieldStatusPublic(_Strict):
    """Per-field freshness / availability entry (snapshot path only).

    ``observed=False`` entries carry only the first four fields; observed
    entries add ``lastObservedMonotonic`` / ``maxAge`` / ``source`` /
    ``quality``. All five extras are therefore optional. ``quality`` (a
    ``string[]``) is emitted at runtime but was absent from the old TS
    interface (MOR-881 contract correction).

    ``available`` and ``stale`` come from an observed entry's freshness
    (``runtime_helpers._freshness_availability``). ``missing``,
    ``unavailable`` and ``undeclared`` are what
    ``runtime_helpers._absence_availability`` makes of a path with no
    observation: ``undeclared`` when the caller's declared set does not
    carry it, ``unavailable`` when the caller's resolved availability map
    reads its ``available_when`` clauses as ``False`` or ``None``
    (``acquisition_scheduler.resolve_available_when``), ``missing``
    otherwise. ``unavailable`` needs the availability argument and
    ``undeclared`` needs the declared argument at
    ``runtime_helpers.build_public_state_payload_from_snapshot``; either can
    be emitted when its corresponding argument is passed alone. A caller that
    passes neither gets ``missing``.
    """

    storePath: str
    observed: bool
    freshness: Literal["unknown", "fresh", "stale"]
    availability: Literal["missing", "available", "stale", "unavailable", "undeclared"]
    lastObservedMonotonic: float | None = None
    maxAge: float | None = None
    source: dict[str, object] | None = None
    quality: list[str] = Field(default_factory=list)


class KnownTxTargetPublic(_Strict):
    """Fresh backend-neutral transmit-target identity."""

    status: Literal["known"]
    receiver: Literal["MAIN", "SUB"]
    slot: Literal["A", "B"] | None
    frequencyHz: Annotated[int, Field(strict=True, gt=0)] | None


class UnknownTxTargetPublic(_Strict):
    """Fail-closed transmit target when current identity is unavailable."""

    status: Literal["unknown"] = "unknown"
    reason: Literal[
        "not-observed",
        "stale",
        "unsupported",
        "contradiction",
    ]


TxTargetPublic = Annotated[
    KnownTxTargetPublic | UnknownTxTargetPublic,
    Field(discriminator="status"),
]


class ConnectionPublic(_Strict):
    """Synthetic connection object injected from the backend connection scalars."""

    rigConnected: bool = False
    radioReady: bool = False
    controlConnected: bool = False


class RadioHealthPublic(_Strict):
    """Classified server/radio health (``classify_radio_health``)."""

    serverReachable: bool
    radioLink: Literal["connected", "reconnecting", "disconnected", "unknown"]
    readiness: Literal["ready", "delayed", "stalled", "recovering"]
    likelyCause: Literal[
        "server_unreachable",
        "radio_network_lost",
        "radio_not_responding",
        "radio_powered_off_likely",
        "unknown",
    ]
    sinceMs: int
    lastError: str | None = None


class RadioDetailPublic(_Strict):
    """Radio connection detail. Carries ONLY ``status`` in the state payload.

    MOR-881 contract correction: the old TS declared a required
    ``uptimeSeconds`` here, but that field belongs to ``/api/v1/runtime`` and
    ``/api/v1/radio`` — it is NEVER in the state payload.
    """

    status: str


class WsClientsPublic(_Strict):
    """WebSocket client counts per channel."""

    scope: int = 0
    control: int = 0
    audio: int = 0


class ServerStatePublic(_Strict):
    """The full public radio-state payload (server-sent portion).

    Excludes the client-only ``meterSource`` (never server-sent) and the
    frontend-only ``UiState`` / ``PendingCommand`` types (MOR-881).
    Leaves with a ``fieldStatus`` entry publish ``null`` while unobserved
    and keep their value when stale (MOR-2513; pinned by
    ``tests/web/test_state_schema_conformance.py::
    test_snapshot_path_unobserved_null_leaves_conform``).
    """

    # Revisions / sequence counters.
    revision: int
    stateRevision: int
    freshnessRevision: int
    observationSeq: int
    healthRevision: int = 0
    updatedAt: str
    # Additive wire-contract metadata. Runtime B2 emission requires both;
    # defaults preserve generated compile-time compatibility for old consumers.
    stateContractVersion: Literal[1] = 1
    providerGeneration: int = 0

    # Global slow-state / TX flags.
    # ``active`` is set to "MAIN"/"SUB" in exactly three places:
    #   _civ_rx.py (0xD2 frame), _dual_rx_runtime.py, and RadioState default.
    # No other values are produced; the Literal is safe.
    active: Literal["MAIN", "SUB"] | None = None
    powerOn: bool | None = None
    ptt: bool | None = None
    # Normalized to float in [0, 1] by the snapshot path via
    # _normalize_public_level_snapshot_value (runtime_helpers.py).
    powerLevel: float | None = None
    split: bool | None = None
    dualWatch: bool | None = None
    scanning: bool | None = None
    scanType: int | None = None
    scanResumeMode: int | None = None
    tuningStep: int | None = None
    overflow: bool | None = None
    tunerStatus: int | None = None
    ritFreq: int | None = None
    ritOn: bool | None = None
    ritTx: bool | None = None
    compMeter: int | None = None
    vdMeter: int | None = None
    idMeter: int | None = None
    powerMeter: int | None = None
    swrMeter: int | None = None
    alcMeter: int | None = None
    cwPitch: int | None = None
    micGain: int | None = None
    keySpeed: int | None = None
    mainSubTracking: bool | None = None
    compressorOn: bool | None = None
    compressorLevel: int | None = None
    monitorOn: bool | None = None
    breakInDelay: int | None = None
    cwSpot: bool | None = None
    breakIn: int | None = None
    dialLock: bool | None = None
    driveGain: int | None = None
    monitorGain: int | None = None
    # Compatibility alias derived from canonical ``active``; never Store truth.
    vfoSelect: int | None = None
    # Opaque compatibility only; generic controls must not treat it as truth.
    yaesu: dict[str, int | None] | None = None
    voxOn: bool | None = None
    voxGain: int | None = None
    antiVoxGain: int | None = None
    voxDelay: int | None = None
    ssbTxBandwidth: int | None = None
    refAdjust: int | None = None
    dashRatio: int | None = None
    nbDepth: int | None = None
    nbWidth: int | None = None
    txAntenna: int | None = None
    rxAntenna1: bool | None = None
    rxAntenna2: bool | None = None
    dataOffModInput: int | None = None
    data1ModInput: int | None = None
    data2ModInput: int | None = None
    data3ModInput: int | None = None
    txBandEdges: list[dict[str, int]] | None = None
    scopeControls: ScopeControlsPublic
    txTarget: TxTargetPublic

    # Receivers. ``sub`` is dropped from the payload when ``receiver_count < 2``.
    main: ReceiverStatePublic
    sub: ReceiverStatePublic | None = None

    # Synthetic / injected objects.
    connection: ConnectionPublic
    radioDetail: RadioDetailPublic
    radioHealth: RadioHealthPublic
    wsClients: WsClientsPublic

    # Snapshot path only — absent on the dataclass path, never null when
    # present (generated TS: ``fieldStatus?: Record<string, FieldStatusPublic>``).
    fieldStatus: dict[str, FieldStatusPublic] = Field(default_factory=dict)

    # Added by the server seq counter, not the helper. Always an int when
    # present (the producer omits the key otherwise), so the generated TS is
    # ``publicStateSeq?: number`` — never nullable.
    publicStateSeq: int = 0


class StateUpdateEnvelope(_Strict):
    """WS ``state_update`` delta/full envelope (``_delta_encoder.py``).

    The full frame carries ``data`` (a complete :class:`ServerStatePublic`); the
    delta frame carries ``changed`` (a shallow top-level partial of the same
    shape) and optional ``removed`` keys. Envelope-only sequence fields
    (``transportSeq`` etc.) live here, not inside the state object.
    """

    type: Literal["full", "delta"]
    revision: int
    transportSeq: int
    # Additive declarations; PR B begins emitting both on every frame.
    stateContractVersion: Literal[1] = 1
    providerGeneration: int = 0
    data: ServerStatePublic | None = None
    changed: dict[str, object] | None = None
    removed: list[str] | None = None
    stateRevision: int | None = None
    freshnessRevision: int | None = None
    observationSeq: int | None = None
