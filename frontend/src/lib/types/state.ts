// BEGIN GENERATED — do not edit by hand (scripts/gen_state_types.py)
// This block is generated from the pydantic schema in
// src/rigplane/web/state_schema.py (the public state-payload contract,
// MOR-881). Regenerate with `python scripts/gen_state_types.py`; the CI
// state-types-gate fails on drift. Edit the pydantic model, not this block.

/**
 * The full public radio-state payload (server-sent portion).
 *
 * Excludes the client-only ``meterSource`` (never server-sent) and the
 * frontend-only ``UiState`` / ``PendingCommand`` types (MOR-881).
 * Leaves with a ``fieldStatus`` entry publish ``null`` while unobserved
 * and keep their value when stale; ``txTarget`` excepted — it encodes
 * absence in-band (an unknown-status object), not as null (MOR-2513;
 * pinned by ``tests/web/test_state_schema_conformance.py::
 * test_snapshot_path_unobserved_null_leaves_conform``).
 */
export interface ServerStatePublic {
  revision: number;
  stateRevision: number;
  freshnessRevision: number;
  observationSeq: number;
  healthRevision?: number;
  updatedAt: string;
  stateContractVersion?: 1;
  providerGeneration?: number;
  active: ("MAIN" | "SUB") | null;
  powerOn?: boolean | null;
  ptt: boolean | null;
  powerLevel?: number | null;
  split: boolean | null;
  dualWatch: boolean | null;
  scanning?: boolean | null;
  scanType?: number | null;
  scanResumeMode?: number | null;
  tuningStep?: number | null;
  overflow?: boolean | null;
  tunerStatus: number | null;
  ritFreq?: number | null;
  ritOn?: boolean | null;
  ritTx?: boolean | null;
  compMeter?: number | null;
  vdMeter?: number | null;
  idMeter?: number | null;
  powerMeter?: number | null;
  swrMeter?: number | null;
  alcMeter?: number | null;
  cwPitch?: number | null;
  micGain?: number | null;
  keySpeed?: number | null;
  mainSubTracking?: boolean | null;
  compressorOn?: boolean | null;
  compressorLevel?: number | null;
  monitorOn?: boolean | null;
  breakInDelay?: number | null;
  cwSpot?: boolean | null;
  breakIn?: number | null;
  dialLock?: boolean | null;
  driveGain?: number | null;
  monitorGain?: number | null;
  vfoSelect?: number | null;
  yaesu?: {
    [k: string]: number | null;
  } | null;
  voxOn?: boolean | null;
  voxGain?: number | null;
  antiVoxGain?: number | null;
  voxDelay?: number | null;
  ssbTxBandwidth?: number | null;
  refAdjust?: number | null;
  dashRatio?: number | null;
  nbDepth?: number | null;
  nbWidth?: number | null;
  txAntenna?: number | null;
  rxAntenna1?: boolean | null;
  rxAntenna2?: boolean | null;
  dataOffModInput?: number | null;
  data1ModInput?: number | null;
  data2ModInput?: number | null;
  data3ModInput?: number | null;
  txBandEdges?:
    | {
        [k: string]: number;
      }[]
    | null;
  scopeControls?: ScopeControlsPublic;
  txTarget: KnownTxTargetPublic | UnknownTxTargetPublic;
  main: ReceiverStatePublic;
  sub?: ReceiverStatePublic | null;
  connection: ConnectionPublic;
  radioDetail?: RadioDetailPublic;
  radioHealth?: RadioHealthPublic;
  wsClients?: WsClientsPublic;
  monitorMute?: MonitorMutePublic | null;
  fieldStatus?: {
    [k: string]: FieldStatusPublic;
  };
  publicStateSeq?: number;
}
/**
 * Spectrum-scope control state.
 */
export interface ScopeControlsPublic {
  receiver: number | null;
  dual: boolean | null;
  mode: number | null;
  span: number | null;
  edge: number | null;
  hold: boolean | null;
  refDb: number | null;
  speed: number | null;
  duringTx: boolean | null;
  centerType: number | null;
  vbwNarrow: boolean | null;
  rbw: number | null;
  fixedEdge: FixedEdgePublic | null;
}
/**
 * Scope fixed-edge sub-object.
 */
export interface FixedEdgePublic {
  rangeIndex: number;
  edge: number;
  startHz: number;
  endHz: number;
}
/**
 * Fresh backend-neutral transmit-target identity.
 */
export interface KnownTxTargetPublic {
  status: "known";
  receiver: "MAIN" | "SUB";
  slot: ("A" | "B") | null;
  frequencyHz: number | null;
}
/**
 * Fail-closed transmit target when current identity is unavailable.
 */
export interface UnknownTxTargetPublic {
  status: "unknown";
  reason: "not-observed" | "stale" | "unsupported" | "contradiction";
}
/**
 * Per-receiver (``main`` / ``sub``) public state.
 *
 * Carries BOTH the slot view (``vfoA`` / ``vfoB`` / ``activeSlot``) and the
 * legacy active-slot scalars (``freqHz`` / ``mode`` / ``filter`` /
 * ``dataMode``). The redundancy is intentional back-compat (radio_state.py
 * ``_receiver_to_dict``); the slot view is the canonical source-of-truth and
 * the scalars are derived from ``activeSlot``.
 */
export interface ReceiverStatePublic {
  vfoA?: VfoSlotPublic;
  vfoB?: VfoSlotPublic;
  activeSlot?: string | null;
  unselectedVfo?: VfoSlotPublic | null;
  freqHz: number | null;
  mode: string | null;
  filter: number | null;
  dataMode: number | null;
  filterWidth?: number | null;
  att: number | null;
  preamp: number | null;
  nb: boolean | null;
  nr: boolean | null;
  digisel?: boolean | null;
  ipplus?: boolean | null;
  sMeterSqlOpen?: boolean | null;
  agc?: number | null;
  audioPeakFilter?: number | null;
  autoNotch?: boolean | null;
  manualNotch?: boolean | null;
  twinPeakFilter?: boolean | null;
  filterShape?: number | null;
  agcTimeConstant?: number | null;
  afLevel: number | null;
  rfGain: number | null;
  squelch: number | null;
  sMeter: number | null;
  apfTypeLevel?: number | null;
  apfOn?: boolean | null;
  apfFreq?: number | null;
  nrLevel?: number | null;
  pbtInner?: number | null;
  pbtOuter?: number | null;
  nbLevel?: number | null;
  digiselShift?: number | null;
  afMute?: boolean | null;
  contour?: number | null;
  ifShift?: number | null;
  narrow?: boolean | null;
  manualNotchFreq?: number | null;
  manualNotchWidth?: number | null;
  notchFilter?: number | null;
  repeaterTone?: boolean | null;
  repeaterTsql?: boolean | null;
  toneFreq?: number | null;
  tsqlFreq?: number | null;
  repeaterShift?: number | null;
  dcd?: boolean | null;
}
/**
 * One VFO slot (A or B) within a receiver.
 */
export interface VfoSlotPublic {
  freqHz: number | null;
  mode: string | null;
  filterNum: number | null;
  dataMode: number | null;
}
/**
 * Synthetic connection object injected from the backend connection scalars.
 */
export interface ConnectionPublic {
  rigConnected: boolean;
  radioReady: boolean;
  controlConnected: boolean;
}
/**
 * Radio connection detail. Carries ONLY ``status`` in the state payload.
 *
 * MOR-881 contract correction: the old TS declared a required
 * ``uptimeSeconds`` here, but that field belongs to ``/api/v1/runtime`` and
 * ``/api/v1/radio`` — it is NEVER in the state payload.
 */
export interface RadioDetailPublic {
  status: string;
}
/**
 * Classified server/radio health (``classify_radio_health``).
 */
export interface RadioHealthPublic {
  serverReachable: boolean;
  radioLink: "connected" | "reconnecting" | "disconnected" | "unknown";
  readiness: "ready" | "delayed" | "stalled" | "recovering";
  likelyCause:
    | "server_unreachable"
    | "radio_network_lost"
    | "radio_not_responding"
    | "radio_powered_off_likely"
    | "unknown";
  sinceMs: number;
  lastError: string | null;
}
/**
 * WebSocket client counts per channel.
 */
export interface WsClientsPublic {
  scope: number;
  control: number;
  audio: number;
}
/**
 * Server-owned monitor MUTE (MOR-2583).
 *
 * Process state, not a radio observation: it survives a page reload and a
 * radio reconnect. ``savedAf`` holds the levels unmute restores.
 */
export interface MonitorMutePublic {
  on: boolean;
  savedAf: MonitorMuteSavedAfPublic;
}
/**
 * AF levels monitor MUTE saved, one per receiver the radio has.
 *
 * ``sub`` is absent when the radio has one receiver, matching the
 * top-level ``sub`` rule (MOR-2583).
 */
export interface MonitorMuteSavedAfPublic {
  main: number | null;
  sub: number | null;
}
/**
 * Per-field freshness / availability entry (snapshot path only).
 *
 * ``observed=False`` entries carry only the first four fields; observed
 * entries add ``lastObservedMonotonic`` / ``maxAge`` / ``source`` /
 * ``quality``. All five extras are therefore optional. ``quality`` (a
 * ``string[]``) is emitted at runtime but was absent from the old TS
 * interface (MOR-881 contract correction).
 *
 * ``available`` and ``stale`` come from an observed entry's freshness
 * (``runtime_helpers._freshness_availability``). ``missing``,
 * ``unavailable`` and ``undeclared`` are what
 * ``runtime_helpers._absence_availability`` makes of a path with no
 * observation: ``undeclared`` when the caller's declared set does not
 * carry it, ``unavailable`` when the caller's resolved availability map
 * reads its ``available_when`` clauses as ``False`` or ``None``
 * (``acquisition_scheduler.resolve_available_when``), ``missing``
 * otherwise. ``unavailable`` needs the availability argument and
 * ``undeclared`` needs the declared argument at
 * ``runtime_helpers.build_public_state_payload_from_snapshot``; either can
 * be emitted when its corresponding argument is passed alone. A caller that
 * passes neither gets ``missing``.
 */
export interface FieldStatusPublic {
  storePath: string;
  observed: boolean;
  freshness: "unknown" | "fresh" | "stale";
  availability: "missing" | "available" | "stale" | "unavailable" | "undeclared";
  lastObservedMonotonic?: number | null;
  maxAge?: number | null;
  source?: {
    [k: string]: unknown;
  } | null;
  quality?: string[];
}
/**
 * WS ``state_update`` delta/full envelope (``_delta_encoder.py``).
 *
 * The full frame carries ``data`` (a complete :class:`ServerStatePublic`); the
 * delta frame carries ``changed`` (a shallow top-level partial of the same
 * shape) and optional ``removed`` keys. Envelope-only sequence fields
 * (``transportSeq`` etc.) live here, not inside the state object.
 */
export interface StateUpdateEnvelope {
  type: "full" | "delta";
  revision: number;
  transportSeq: number;
  stateContractVersion?: 1;
  providerGeneration?: number;
  data?: ServerStatePublic | null;
  changed?: {
    [k: string]: unknown;
  } | null;
  removed?: string[] | null;
  stateRevision?: number | null;
  freshnessRevision?: number | null;
  observationSeq?: number | null;
}
// END GENERATED

// ---------------------------------------------------------------------------
// Hand-written UI section (MOR-881)
//
// Everything ABOVE the `// END GENERATED` marker is generated from the pydantic
// contract in `src/rigplane/web/state_schema.py` and reflects the REAL public
// wire payload. Everything below is hand-written: stable public aliases that
// keep existing consumers compiling, the client-only fields the server never
// sends, and the frontend-only UI types.
// ---------------------------------------------------------------------------

// Stable public aliases over the generated `*Public` contract interfaces.
// Consumers import these names; the generated interfaces are the source of
// truth for their shape.
export type ReceiverState = ReceiverStatePublic;
export type ScopeControls = ScopeControlsPublic;
export type FieldStatus = FieldStatusPublic;
export type FieldFreshness = FieldStatusPublic['freshness'];
export type FieldAvailability = FieldStatusPublic['availability'];

/**
 * The state object the frontend holds.
 *
 * Extends the generated, server-sent `ServerStatePublic` contract with the
 * envelope field that is NOT part of the wire payload but lives on the
 * merged client-side state:
 *
 * - `transportSeq`: a WS envelope-only sequence field that `ws-client.ts`
 *   hoists onto the accumulated state object for ordering. Not server-sent
 *   inside `data`/`changed`.
 */
export interface ServerState extends ServerStatePublic {
  transportSeq?: number;
  // Client-side invariant: the merged state always carries a `sub` receiver
  // (the single-receiver wire payload omits it, but consumers — e.g.
  // `activeRx` — treat it as present). Narrowed here rather than in the
  // generated wire contract, which honestly marks `sub` optional.
  sub: ReceiverState;
}

export interface UiState {
  layout: 'desktop' | 'mobile';
  activePanel: 'main' | 'audio' | 'memories' | 'settings';
  spectrumFullscreen: boolean;
  freqEntryOpen: boolean;
  theme: 'dark' | 'light';
  gestures: {
    tuning: boolean;
    draggingSpectrum: boolean;
  };
}

export interface PendingCommand {
  id: string;
  type: string;
  payload: unknown;
  createdAt: number;
  status: 'pending' | 'acked' | 'failed';
  timeoutMs: number;
}
