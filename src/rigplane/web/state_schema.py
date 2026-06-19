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

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

__all__ = [
    "VfoSlotPublic",
    "ReceiverStatePublic",
    "FixedEdgePublic",
    "ScopeControlsPublic",
    "FieldStatusPublic",
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

    freqHz: int = 0
    mode: str = "USB"
    filterNum: int | None = None
    dataMode: int = 0


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
    activeSlot: str = "A"

    # Legacy active-slot scalars (derived from the active slot). ``freq`` is
    # renamed to ``freqHz`` by ``_RECEIVER_KEY_MAP``; the rest pass through.
    freqHz: int = 0
    mode: str = "USB"
    filter: int | None = None
    dataMode: int = 0

    filterWidth: int | None = None
    att: int = 0
    preamp: int = 0
    nb: bool = False
    nr: bool = False
    digisel: bool = False
    ipplus: bool = False
    sMeterSqlOpen: bool = False
    agc: int = 0
    audioPeakFilter: int = 0
    autoNotch: bool = False
    manualNotch: bool = False
    twinPeakFilter: bool = False
    filterShape: int = 0
    agcTimeConstant: int = 0
    afLevel: int = 0
    rfGain: int = 0
    squelch: int = 0
    sMeter: int = 0
    apfTypeLevel: int = 0
    apfOn: bool = False
    apfFreq: int = 0
    nrLevel: int = 0
    pbtInner: int = 128
    pbtOuter: int = 128
    nbLevel: int = 0
    digiselShift: int = 0
    afMute: bool = False
    contour: int = 0
    ifShift: int = 0
    narrow: bool = False
    manualNotchFreq: int = 0
    manualNotchWidth: int = 0
    repeaterTone: bool = False
    repeaterTsql: bool = False
    toneFreq: int = 0
    tsqlFreq: int = 0

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

    receiver: int = 0
    dual: bool = False
    mode: int = 0
    span: int = 0
    edge: int = 0
    hold: bool = False
    refDb: float = 0.0
    speed: int = 0
    duringTx: bool = False
    centerType: int = 0
    vbwNarrow: bool = False
    rbw: int = 0
    fixedEdge: FixedEdgePublic


class FieldStatusPublic(_Strict):
    """Per-field freshness / availability entry (snapshot path only).

    ``observed=False`` entries carry only the first four fields; observed
    entries add ``lastObservedMonotonic`` / ``maxAge`` / ``source`` /
    ``quality``. All five extras are therefore optional. ``quality`` (a
    ``string[]``) is emitted at runtime but was absent from the old TS
    interface (MOR-881 contract correction).
    """

    storePath: str
    observed: bool
    freshness: Literal["unknown", "fresh", "stale"]
    availability: Literal["missing", "available", "stale"]
    lastObservedMonotonic: float | None = None
    maxAge: float | None = None
    source: dict[str, object] | None = None
    quality: list[str] = Field(default_factory=list)


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
    """

    # Revisions / sequence counters.
    revision: int
    stateRevision: int
    freshnessRevision: int
    observationSeq: int
    healthRevision: int = 0
    updatedAt: str

    # Global slow-state / TX flags.
    active: Literal["MAIN", "SUB"]
    powerOn: bool = True
    ptt: bool = False
    powerLevel: int = 0
    split: bool = False
    dualWatch: bool = False
    scanning: bool = False
    scanType: int = 0
    scanResumeMode: int = 0
    tuningStep: int = 0
    overflow: bool = False
    tunerStatus: int = 0
    txFreqMonitor: bool = False
    ritFreq: int = 0
    ritOn: bool = False
    ritTx: bool = False
    compMeter: int = 0
    vdMeter: int = 0
    idMeter: int = 0
    powerMeter: int = 0
    swrMeter: int = 0
    alcMeter: int = 0
    cwPitch: int = 0
    micGain: int = 0
    keySpeed: int = 0
    notchFilter: int = 0
    mainSubTracking: bool = False
    compressorOn: bool = False
    compressorLevel: int = 0
    monitorOn: bool = False
    breakInDelay: int = 0
    cwSpot: bool | None = None
    breakIn: int = 0
    dialLock: bool = False
    driveGain: int = 0
    monitorGain: int = 0
    vfoSelect: int = 0
    yaesu: dict[str, int | None] | None = None
    voxOn: bool = False
    voxGain: int = 0
    antiVoxGain: int = 0
    voxDelay: int = 0
    ssbTxBandwidth: int = 0
    refAdjust: int = 0
    dashRatio: int = 0
    nbDepth: int = 0
    nbWidth: int = 0
    txAntenna: int = 1
    rxAntenna1: bool = False
    rxAntenna2: bool = False
    dataOffModInput: int | None = None
    data1ModInput: int | None = None
    data2ModInput: int | None = None
    data3ModInput: int | None = None
    txBandEdges: list[dict[str, int]] = Field(default_factory=list)
    scopeControls: ScopeControlsPublic

    # Receivers. ``sub`` is dropped from the payload when ``receiver_count < 2``.
    main: ReceiverStatePublic
    sub: ReceiverStatePublic | None = None

    # Synthetic / injected objects.
    connection: ConnectionPublic
    radioDetail: RadioDetailPublic
    radioHealth: RadioHealthPublic
    wsClients: WsClientsPublic

    # Snapshot path only.
    fieldStatus: dict[str, FieldStatusPublic] | None = None

    # Added by the server seq counter, not the helper.
    publicStateSeq: int | None = None


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
    data: ServerStatePublic | None = None
    changed: dict[str, object] | None = None
    removed: list[str] | None = None
    stateRevision: int | None = None
    freshnessRevision: int | None = None
    observationSeq: int | None = None
