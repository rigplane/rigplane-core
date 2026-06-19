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
 */
export interface ServerStatePublic {
  revision: number;
  stateRevision: number;
  freshnessRevision: number;
  observationSeq: number;
  healthRevision?: number;
  updatedAt: string;
  active: "MAIN" | "SUB";
  powerOn?: boolean;
  ptt: boolean;
  powerLevel?: number;
  split: boolean;
  dualWatch: boolean;
  scanning?: boolean;
  scanType?: number;
  scanResumeMode?: number;
  tuningStep?: number;
  overflow?: boolean;
  tunerStatus: number;
  txFreqMonitor?: boolean;
  ritFreq?: number;
  ritOn?: boolean;
  ritTx?: boolean;
  compMeter?: number;
  vdMeter?: number;
  idMeter?: number;
  powerMeter?: number;
  swrMeter?: number;
  alcMeter?: number;
  cwPitch?: number;
  micGain?: number;
  keySpeed?: number;
  notchFilter?: number;
  mainSubTracking?: boolean;
  compressorOn?: boolean;
  compressorLevel?: number;
  monitorOn?: boolean;
  breakInDelay?: number;
  cwSpot?: boolean | null;
  breakIn?: number;
  dialLock?: boolean;
  driveGain?: number;
  monitorGain?: number;
  vfoSelect?: number;
  yaesu?: {
    [k: string]: number | null;
  } | null;
  voxOn?: boolean;
  voxGain?: number;
  antiVoxGain?: number;
  voxDelay?: number;
  ssbTxBandwidth?: number;
  refAdjust?: number;
  dashRatio?: number;
  nbDepth?: number;
  nbWidth?: number;
  txAntenna?: number;
  rxAntenna1?: boolean;
  rxAntenna2?: boolean;
  dataOffModInput?: number | null;
  data1ModInput?: number | null;
  data2ModInput?: number | null;
  data3ModInput?: number | null;
  txBandEdges?: {
    [k: string]: number;
  }[];
  scopeControls?: ScopeControlsPublic;
  main: ReceiverStatePublic;
  sub?: ReceiverStatePublic | null;
  connection: ConnectionPublic;
  radioDetail?: RadioDetailPublic;
  radioHealth?: RadioHealthPublic;
  wsClients?: WsClientsPublic;
  fieldStatus?: {
    [k: string]: FieldStatusPublic;
  };
  publicStateSeq?: number;
}
/**
 * Spectrum-scope control state.
 */
export interface ScopeControlsPublic {
  receiver: number;
  dual: boolean;
  mode: number;
  span: number;
  edge: number;
  hold: boolean;
  refDb: number;
  speed: number;
  duringTx: boolean;
  centerType: number;
  vbwNarrow: boolean;
  rbw: number;
  fixedEdge: FixedEdgePublic;
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
  activeSlot?: string;
  freqHz: number;
  mode: string;
  filter: number | null;
  dataMode: number;
  filterWidth?: number | null;
  att: number;
  preamp: number;
  nb: boolean;
  nr: boolean;
  digisel?: boolean;
  ipplus?: boolean;
  sMeterSqlOpen?: boolean;
  agc?: number;
  audioPeakFilter?: number;
  autoNotch?: boolean;
  manualNotch?: boolean;
  twinPeakFilter?: boolean;
  filterShape?: number;
  agcTimeConstant?: number;
  afLevel: number;
  rfGain: number;
  squelch: number;
  sMeter: number;
  apfTypeLevel?: number;
  apfOn?: boolean;
  apfFreq?: number;
  nrLevel?: number;
  pbtInner?: number;
  pbtOuter?: number;
  nbLevel?: number;
  digiselShift?: number;
  afMute?: boolean;
  contour?: number;
  ifShift?: number;
  narrow?: boolean;
  manualNotchFreq?: number;
  manualNotchWidth?: number;
  repeaterTone?: boolean;
  repeaterTsql?: boolean;
  toneFreq?: number;
  tsqlFreq?: number;
  dcd?: boolean | null;
}
/**
 * One VFO slot (A or B) within a receiver.
 */
export interface VfoSlotPublic {
  freqHz: number;
  mode: string;
  filterNum: number | null;
  dataMode: number;
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
 * Per-field freshness / availability entry (snapshot path only).
 *
 * ``observed=False`` entries carry only the first four fields; observed
 * entries add ``lastObservedMonotonic`` / ``maxAge`` / ``source`` /
 * ``quality``. All five extras are therefore optional. ``quality`` (a
 * ``string[]``) is emitted at runtime but was absent from the old TS
 * interface (MOR-881 contract correction).
 */
export interface FieldStatusPublic {
  storePath: string;
  observed: boolean;
  freshness: "unknown" | "fresh" | "stale";
  availability: "missing" | "available" | "stale";
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
 * fields that are NOT part of the wire payload but live on the merged
 * client-side state:
 *
 * - `meterSource`: pure client-only optimistic UI state. The server never
 *   sends it; it is patched locally by the command bus / state adapter
 *   (MOR-881 contract correction — it used to be declared on the server
 *   contract by mistake).
 * - `transportSeq`: a WS envelope-only sequence field that `ws-client.ts`
 *   hoists onto the accumulated state object for ordering. Not server-sent
 *   inside `data`/`changed`.
 */
export interface ServerState extends ServerStatePublic {
  meterSource?: 'S' | 'SWR' | 'POWER';
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
