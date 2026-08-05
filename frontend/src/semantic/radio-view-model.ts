/**
 * Radio semantic view-model contract (MOR-1062).
 *
 * The seam between adapters (which consume runtime state and capabilities —
 * see `lib/runtime/adapters/*`) and semantic UI (which renders only this
 * shape and nothing else — no transport, store, or manufacturer knowledge).
 * A design language may change how a fact looks; it may never change which
 * facts exist. See docs/plans/2026-07-25-ui-composition-architecture-v3.md
 * and the MOR-977 shared semantic skeleton (Linear comment, 2026-08-03).
 *
 * `ActiveRx` and the scheme-conditioned slot/target shapes below mirror the
 * exact identity types frozen by the MOR-988 "Accepted capability and
 * presentation semantics" decision (§3.2, §4) — this contract reuses that
 * vocabulary rather than reinventing it. Every "unknown"/"denied" branch is
 * intentional and must survive round-tripping — collapsing it into a
 * boolean or a default is the failure mode this contract exists to prevent.
 */
import type { VfoScheme } from '$lib/types/capabilities';
import type { FrequencyPermit, TxPermit } from '$lib/utils/tx-permit';
import { invalid, record, exactKeys, str } from './validator-primitives';

export type ReceiverId = 'MAIN' | 'SUB';
export type VfoSlotId = 'A' | 'B';

/**
 * Whether a VFO/target position has an addressable A/B slot at all, distinct
 * from whether that slot was actually observed. `unslotted` = the scheme has
 * no A/B concept here (`single`, `ab_shared`); `unknown` = a slotted scheme
 * (`ab`, `main_sub`) whose slot could not be observed — MOR-988 §3.2/§4:
 * missing/stale never synthesizes `A`.
 */
export type VfoSlot =
  | { kind: 'slotted'; id: VfoSlotId }
  | { kind: 'unslotted' }
  | { kind: 'unknown' };

/** MOR-988 §3.2 `ActiveRx`, verbatim: an adapter with no observation must never fabricate 'MAIN'. */
export type ActiveRx =
  | { status: 'known'; receiver: ReceiverId }
  | { status: 'unknown' };

/** A boolean radio fact (`split`, `dualWatch`) that can itself be unobserved. */
export type BooleanFact =
  | { status: 'known'; value: boolean }
  | { status: 'unknown' };

/**
 * Structural = the radio model supports this. Operational = usable right
 * now, given live capability AND field-observed state. MOR-977 two-level
 * gating: a control that fails the STRUCTURAL half is absent (nothing to
 * render); a control that fails only the OPERATIONAL half stays present but
 * disabled, degrading to an explicit unknown/disabled state rather than a
 * guessed default — MOR-988 §11.3: "Old v1 servers lacking additive fields
 * degrade to `unknown`/disabled in v3, not guessed behavior."
 */
export interface Availability {
  structural: boolean;
  operational: boolean;
}

export interface VfoViewModel {
  receiver: ReceiverId;
  slot: VfoSlot;
  label: string;
  frequencyHz: number | null;
  mode: string | null;
  filter: string | null;
  isActive: boolean;
  isTxTarget: boolean;
}

export type TxTargetViewModel =
  | { status: 'known'; receiver: ReceiverId; slot: VfoSlot; frequencyHz: number | null }
  | { status: 'unknown'; reason: 'not-observed' | 'stale' | 'unsupported' | 'contradiction' };

export interface ScopeAvailabilityViewModel {
  hardwareScope: Availability;
  audioFftScope: Availability;
}

export type DisabledReasonCode =
  | 'capability-unavailable'
  | 'field-not-observed'
  | 'tx-target-unknown'
  | 'out-of-band'
  /** MOR-1293: a hardware mutex with another control's CURRENT state
   *  disables this one (e.g. PREAMP while DIGI-SEL is on/unknown, MOR-479).
   *  Distinct from `capability-unavailable` (the control doesn't exist) and
   *  `field-not-observed` (this control's OWN reading is unobserved) —
   *  here the control itself is fine, a PEER control's state disables it. */
  | 'mutually-exclusive-control';

export interface DisabledReason {
  field: string;
  code: DisabledReasonCode;
}

/**
 * A single TX-adjacent fact (MOR-1244): a value that is either known (with
 * its type-checked reading) or unknown, paired with the MOR-977 two-level
 * `Availability` — structural (does this radio model have the control at
 * all) and operational (is it currently readable). The two are independent:
 * `structural: false` means nothing to render; `structural: true,
 * operational: false` means present-but-disabled, degrading to `unknown`
 * rather than a guessed value, same doctrine as every other fact here.
 */
export type TxAuxReading<T> = { status: 'known'; value: T } | { status: 'unknown' };
export interface TxAuxField<T> {
  reading: TxAuxReading<T>;
  availability: Availability;
}
export type AtuStatus = 'off' | 'on' | 'tuning';

/**
 * TX-adjacent facts (MOR-1244, MOR-1262 decomposition slice 1A): ATU/TUNE,
 * VOX (+gain/anti-vox/delay), COMP (+level), MON (+level), RF power, mic
 * gain, drive gain. Facts only — no action/dispatch. ATU TUNE is a
 * transmit-causing action; this contract carries only its honestly-gated
 * *state*, never a control to trigger it (MOR-1262 §2 slice 1 safety note i).
 */
export interface TxAuxViewModel {
  atu: TxAuxField<AtuStatus>;
  vox: TxAuxField<boolean>;
  voxGain: TxAuxField<number>;
  antiVoxGain: TxAuxField<number>;
  voxDelay: TxAuxField<number>;
  compressor: TxAuxField<boolean>;
  compressorLevel: TxAuxField<number>;
  monitor: TxAuxField<boolean>;
  monitorLevel: TxAuxField<number>;
  rfPower: TxAuxField<number>;
  micGain: TxAuxField<number>;
  driveGain: TxAuxField<number>;
}

/**
 * A single meter fact (MOR-1262 decomposition slice 2A): a numeric reading
 * that is either known or unknown, the MOR-977 two-level `Availability`, and
 * whether the meter reads meaningfully in the CURRENT RF state.
 *
 * `relevant` is the one field of this contract that must NOT be derived from
 * radio state: TX-gated meters (Po/SWR/ALC/COMP/Id) are relevant exactly when
 * the App-owned TX authority — the same source as the AppGlobalHost lamp
 * (MOR-1008/MOR-1059) — says the transmitter may be live. Deriving it from
 * `radioState.ptt` is the open disagreement MOR-1235 reports, and safety
 * invariant R9 forbids reintroducing it here: this contract EXPOSES the
 * authority's conclusion, it never computes one.
 */
export type MeterReading = { status: 'known'; value: number } | { status: 'unknown' };
export interface MeterField {
  reading: MeterReading;
  availability: Availability;
  relevant: boolean;
}

/**
 * The authoritative RF state the meters are read against. Member-for-member
 * the RX/TX surface's own `RfState` (`rx-tx-surface.ts`, MOR-1064) — declared
 * again here rather than imported because that module already imports this
 * one, and a contract must not depend on a surface. Agreement is pinned in
 * `__tests__/meters.test.ts` against the real union and the real reducer.
 */
export type MeterRfState = 'receiving' | 'transmitting' | 'uncertain' | 'unknown';

/**
 * Meter facts (MOR-1262 decomposition slice 2A): S / Po / SWR / ALC / COMP /
 * Vd / Id — the seven meters the shipped v2 dock renders. Facts only: no
 * ballistics, no peak-hold, no formatting (those are presentation, slice 2B).
 */
export interface MetersViewModel {
  rfState: MeterRfState;
  signal: MeterField;
  power: MeterField;
  swr: MeterField;
  alc: MeterField;
  compression: MeterField;
  drainVoltage: MeterField;
  drainCurrent: MeterField;
}

/**
 * A single RX-audio fact (MOR-1262 decomposition slice 3A, MOR-1274). Shape-
 * identical to `TxAuxField` — `{reading, availability}`, no third member —
 * so it is declared as an alias rather than a near-duplicate: one field shape
 * for every non-meter fact family, and slice 1A's declaration is left
 * untouched. The validator is shared for the same reason.
 */
export type RxAudioReading<T> = TxAuxReading<T>;
export type RxAudioField<T> = TxAuxField<T>;

/** What the operator is listening to, verbatim the shipped `RxAudioProps`
 *  vocabulary (`lib/runtime/props/panel-props.ts::toRxAudioProps`): `local` =
 *  the rig's own speaker, `live` = the browser RX stream, `mute` = silenced. */
export type MonitorMode = 'local' | 'live' | 'mute';

/** Dual-receiver audio routing focus, verbatim `AudioRoutingControl.svelte`. */
export type AudioFocus = 'main' | 'sub' | 'both';

/**
 * MOD-input readiness — the recorded "web voice TX = noise/squeal" guard.
 * Member-for-member `lib/runtime/adapters/tx-capabilities.ts`'s own
 * `ModInputReadiness`, declared again here for the same reason `MeterRfState`
 * is (a contract must not depend on an adapter); the adapter EXPOSES
 * `deriveTxCapabilities(...).modInputReadiness` rather than re-deriving it,
 * and both the union and the derivation are pinned in `__tests__/rx-audio.test.ts`.
 * `mismatch` is the failure the whole family exists to make visible: the rig
 * is modulating from MIC/ACC/USB while the web UI streams audio over LAN.
 */
export type ModInputReadiness =
  | { status: 'not-applicable' }
  | { status: 'ready'; source: number }
  | { status: 'mismatch'; source: number }
  | { status: 'unknown' };

/**
 * RX audio-chain facts (MOR-1262 decomposition slice 3A). Facts only — no
 * transport, no AudioContext, no lifetime: audio lifetime is App-owned
 * (MOR-1058, MOR-972 P0) and this group is a pure read-model over a snapshot
 * the App hands in. Constructing or serializing it must never start a stream.
 *
 * `monitorMode` and `liveAudio` come from that App-owned snapshot (the same
 * relationship `meters.rfState` has to the TX authority); `afLevel` /
 * `routingFocus` / `routingSplit` / `modInputSource` are two-level-gated facts
 * that degrade to `unknown` rather than to the shipped panel's fabricated
 * defaults (0.5 AF, 'both' focus).
 */
export interface RxAudioViewModel {
  monitorMode: MonitorMode;
  /** Structural = the radio streams audio at all; operational = the audio WS
   *  is up. Without it a surface cannot honestly offer the `live` mode. */
  liveAudio: Availability;
  /** 0..1. In `live` mode the browser volume; otherwise the radio's AF level —
   *  and `unknown` when that field was never observed, never 0.5. */
  afLevel: RxAudioField<number>;
  routingFocus: RxAudioField<AudioFocus>;
  routingSplit: RxAudioField<boolean>;
  /** The active DATA group's MOD-input source enum (`$lib/radio/mod-input`). */
  modInputSource: RxAudioField<number>;
  modInputReadiness: ModInputReadiness;
}

/**
 * A single mode/filter fact (MOR-1262 decomposition slice 4A, MOR-1280).
 * Shape-identical to `TxAuxField`, declared as an alias for the same reason
 * `RxAudioField` is: one field shape per fact family, no near-duplicate.
 */
export type ModeFilterField<T> = TxAuxField<T>;

/**
 * Mode/filter facts (MOR-1262 decomposition slice 4A). Facts only — no
 * command emission; choosing a mode or dragging the filter-width control
 * stays with the surface (slice 4B).
 *
 * `modeChoices`/`filterChoices` are the capability-derived choice sets
 * (`Capabilities.modes` / `.filters`, verbatim) — plain lists, not
 * `ModeFilterField`-wrapped, because a choice set is a structural fact about
 * the radio MODEL, not a live reading that can itself go stale. The current
 * selection and the width bounds ARE readings, and degrade to `unknown`
 * rather than to `toFilterProps`'s fabricated defaults ('USB', 2400 Hz,
 * 50..9999 Hz) — see `radio-view-model-adapter.ts`'s `deriveModeFilter`.
 *
 * `filterWidthMin`/`filterWidthMax` are the ONE remaining consumer of
 * `resolveFilterModeConfig`'s per-mode table lookup that this slice adds;
 * the X6200 CAT-audit lesson (filter-width codecs are radio-specific) is why
 * this group never re-derives that table itself — it reads the shipped
 * resolver's own output, like `modInputReadiness` reads `deriveTxCapabilities`.
 */
export interface ModeFilterViewModel {
  currentMode: ModeFilterField<string>;
  modeChoices: readonly string[];
  currentFilter: ModeFilterField<number>;
  filterChoices: readonly string[];
  filterWidth: ModeFilterField<number>;
  filterWidthMin: ModeFilterField<number>;
  filterWidthMax: ModeFilterField<number>;
}

/**
 * A single filter-passband fact (MOR-1262 decomposition slice 4A′, MOR-1284).
 * Shape-identical to `TxAuxField`, declared as an alias for the same reason
 * `ModeFilterField`/`RxAudioField` are: one field shape per fact family.
 */
export type FilterPassbandField<T> = TxAuxField<T>;

/**
 * Filter-passband facts (MOR-1262 decomposition slice 4A′, MOR-1284): filter
 * shape, IF-shift, passband tuning (PBT) inner/outer, and the DATA-mode
 * selector these controls key off. Facts only — no command emission, same
 * doctrine as `modeFilter` (MOR-1280).
 *
 * A SEPARATE group from `modeFilter` rather than five more keys on it: 4A
 * covers discrete SELECTION facts (which mode/filter is chosen, and the
 * capability-declared choice sets) gated by a single required-field-is-
 * signal-free capability check. This family covers continuous passband-
 * SHAPING facts (family 9 of the MOR-1262 decomposition — FilterPanel +
 * `filter-controls`) with a different, per-field evidence story (see
 * `deriveFilterPassband`). Folding both into one `exactKeys` list would mix
 * two evidence-gate shapes under one allow-list and force every 4B
 * passband-only consumer to import the selection keys too. One group per
 * family, same precedent as `txAux`/`meters`/`rxAudio`/`modeFilter`.
 *
 * `ifShift`/`pbtInner`/`pbtOuter` are the ONE remaining consumer of
 * `$lib/radio/filter-controls`'s `pbtRawToHz`/`deriveIfShift` — the exact
 * functions `toFilterProps` calls — never a re-derived formula (X6200
 * lesson: PBT/filter scaling is per-radio-model data). Unlike `toFilterProps`,
 * the adapter passes `pbtRawToHz` an explicit `PbtRange` derived from THIS
 * request's own `caps` argument (`pbtRangeFromCaps`, MOR-1284 F1) rather than
 * letting it fall back to the capabilities STORE singleton — a fact-layer
 * value must be a pure function of `(state, caps)`, never of module-global
 * state that can differ from the `caps` already in hand.
 */
export interface FilterPassbandViewModel {
  filterShape: FilterPassbandField<number>;
  ifShift: FilterPassbandField<number>;
  pbtInner: FilterPassbandField<number>;
  pbtOuter: FilterPassbandField<number>;
  dataMode: FilterPassbandField<number>;
}

/**
 * A single DSP fact (MOR-1262 decomposition slice 5A, MOR-1290).
 * Shape-identical to `TxAuxField`, declared as an alias for the same reason
 * `FilterPassbandField`/`ModeFilterField`/`RxAudioField` are: one field
 * shape per fact family.
 */
export type DspField<T> = TxAuxField<T>;

/**
 * DSP facts (MOR-1262 decomposition slice 5A, MOR-1290): noise reduction
 * (NR), noise blanker (NB, + depth/width), notch (auto/manual), and AGC.
 * Facts only — no command emission, same doctrine as `modeFilter`/
 * `filterPassband`. Family enumeration is explicit and CLOSED: filter shape,
 * IF-shift, and PBT are family 4 (`filterPassband`, MOR-1284) — this group
 * never duplicates them.
 *
 * `nrLevel`/`nbDepth` are the ONE remaining consumers of `$lib/radio/filter-
 * controls`'s `nrRawToDisplay`/`nbDepthRawToDisplay` — the exact functions
 * `toDspProps` calls — never a re-derived scale (X6200 lesson: control
 * scaling is per-radio-model data, `caps.controls.nr_level`/`.nb_depth`).
 * Like `filterPassband`'s `pbtInner`/`pbtOuter` (MOR-1284 F1), the adapter
 * passes both an explicit `ControlDisplayRange` derived from THIS request's
 * own `caps` argument (`controlRangeFromCaps`) rather than letting them fall
 * back to the capabilities STORE singleton — a fact-layer value must be a
 * pure function of `(state, caps)`, never of module-global state.
 *
 * `agcModes` is the capability-derived AGC choice set (`Capabilities.
 * agcModes`, verbatim) — a plain list, not `DspField`-wrapped, for the same
 * reason `ModeFilterViewModel`'s `modeChoices`/`filterChoices` aren't: a
 * choice set is a structural fact about the radio MODEL, not a live reading
 * that can itself go stale.
 */
export interface DspViewModel {
  nrActive: DspField<boolean>;
  nrLevel: DspField<number>;
  nbActive: DspField<boolean>;
  nbLevel: DspField<number>;
  nbDepth: DspField<number>;
  nbWidth: DspField<number>;
  notchMode: DspField<'off' | 'auto' | 'manual'>;
  notchFreq: DspField<number>;
  manualNotchWidth: DspField<number>;
  agcMode: DspField<number>;
  agcModes: readonly number[];
  agcTimeConstant: DspField<number>;
}

/**
 * A single RF-front-end fact (MOR-1262 decomposition slice 6A, MOR-1292).
 * Shape-identical to `TxAuxField`, declared as an alias for the same reason
 * `DspField`/`FilterPassbandField`/`ModeFilterField`/`RxAudioField` are: one
 * field shape per fact family.
 */
export type RfFrontEndField<T> = TxAuxField<T>;

/**
 * RF front-end facts (MOR-1262 decomposition slice 6A, MOR-1292; extended by
 * slice 6A′, MOR-1293): preamp, attenuator, RF gain, squelch, DIGI-SEL, IP+.
 * Facts only — no command emission, same doctrine as
 * `dsp`/`modeFilter`/`filterPassband`. Family enumeration is explicit and
 * CLOSED: NR/NB/notch/AGC are family 5 (`dsp`, MOR-1290) — this group never
 * duplicates them.
 *
 * `digiSel`/`ipPlus` were deliberately left out of 6A (MOR-1292 review
 * ruling — enumeration gap identical in kind to the filterShape/IF-shift/PBT
 * one from MOR-1284) because they are the shipped `RfFrontEndProps` panel's
 * OWN remaining controls, sourced from the same `toRfFrontEndProps`. This
 * slice (MOR-1293) closes the gap: EXTENDING this group rather than adding a
 * sibling, because they share the same panel, the same `rf-frontend-utils`
 * neighborhood, and the same per-field shape as the original four — a
 * sibling group would just be this one split for no reason.
 *
 * `preamp`/`attenuator`/`rfGain`/`squelch`/`digiSel`/`ipPlus` are plain
 * pass-through readings: unlike `dsp`'s `nrLevel`/`nbDepth`, the shipped
 * `toRfFrontEndProps` (`lib/runtime/props/panel-props.ts`) reads
 * `rx.preamp`/`rx.att`/`rx.rfGain`/`rx.squelch`/`rx.digisel`/`rx.ipplus`
 * verbatim, with no raw<->display scale conversion — the server already
 * reports these as display-ready values (dB steps, preamp-level ordinal,
 * 0-1 gain fractions, booleans). There is no separate "real function" to
 * consume for the VALUE; the parity surface here is the CAPABILITY gate and
 * the choice sets below, both copied verbatim from `toRfFrontEndProps`.
 *
 * `preValues`/`attValues` are the capability-derived preamp-level and
 * attenuator-dB choice sets (`Capabilities.preValues`/`.attValues`,
 * verbatim) — plain lists, not field-wrapped, same reasoning as `dsp`'s
 * `agcModes`: a choice set is a structural fact about the radio MODEL, not a
 * live reading that can itself go stale. Per the X6200 lesson, these are
 * read from the `caps` ARGUMENT only — never a radio-specific fallback table
 * (the shipped panel's own `[0, 6, 12, 18]`/`[0, 1, 2]` UI-convenience
 * defaults are presentation, not a fact — see `radio-view-model-adapter.ts`'s
 * `deriveRfFrontEnd`).
 *
 * THE MUTEX (MOR-479, MOR-1293): the shipped panel derives an IC-7610
 * hardware mutex from `digiSel` — the radio silently ignores a PREAMP set
 * while DIGI-SEL is ON, so `toRfFrontEndProps` disables the PRE control
 * rather than let it light optimistically. This contract does NOT add a
 * bespoke boolean for that (no `preDisabled` field here) — it expresses the
 * mutex through the existing `RadioViewModel.disabledReasons` home, exactly
 * like every other cross-field disable in this contract (`scope.*`,
 * `receiver.*`), with `field: 'rfFrontEnd.preamp'` and the new
 * `'mutually-exclusive-control'` code. See
 * `radio-view-model-adapter.ts`'s `deriveRfFrontEnd` for the derivation,
 * which reads the mutex condition off THIS group's own `digiSel` fact —
 * never off raw state again — so a stale/unobserved DIGI-SEL reading FAILS
 * CLOSED (the reason is present, disabling PRE) rather than silently
 * re-enabling the control the way a naive `rawDigisel ?? false` would.
 */
export interface RfFrontEndViewModel {
  preamp: RfFrontEndField<number>;
  preValues: readonly number[];
  attenuator: RfFrontEndField<number>;
  attValues: readonly number[];
  rfGain: RfFrontEndField<number>;
  squelch: RfFrontEndField<number>;
  digiSel: RfFrontEndField<boolean>;
  ipPlus: RfFrontEndField<boolean>;
}

/**
 * A single band fact (MOR-1262 decomposition slice 7A, MOR-1294).
 * Shape-identical to `TxAuxField`, declared as an alias for the same reason
 * `RfFrontEndField`/`DspField`/`ModeFilterField`/`RxAudioField` are: one
 * field shape per fact family.
 */
export type BandField<T> = TxAuxField<T>;

/**
 * One selectable band of the radio's own band plan — `$lib/radio/band-plan`'s
 * `FlatBand` (the shipped `flattenBands` output over `Capabilities.
 * freqRanges`) plus the band's TX permit.
 *
 * SAFETY (MOR-1294): `defaultHzTxPermit` is the EXISTING `FrequencyPermit`
 * tri-state, produced by the ONE derivation in this codebase
 * (`getFrequencyPermit`, `$lib/utils/tx-permit` — the same function
 * `deriveTxCapabilities` calls for the model's top-level `txPermit`). It is
 * NOT read off the band plan itself: `freqRanges` describes what the radio
 * can TUNE, `caps.txBands` describes where it may TRANSMIT, and treating a
 * band's presence in the plan as permission to key is precisely the fail-open
 * defect this slice forbids. A default outside `txBands` stays `denied`;
 * `txBands: null` stays `unknown`.
 *
 * It is a POINT SAMPLE at `defaultHz`, and the field name says so (MOR-1294
 * verify F1). It answers only "if I pick this band and land on its default
 * frequency, may I key THERE" — the picker label. It is NOT a statement about
 * the whole band: `txBands` segments are routinely NARROWER than a band-plan
 * band (WARC segments, regional sub-bands, 60m channels), so a band whose
 * default sits inside a permitted segment reads `allowed` here while the
 * operator's LIVE frequency elsewhere in the same band is denied.
 * `BandViewModel.currentBandTx` is the live-frequency answer and is NEVER
 * derived from this field; a 7B surface must not present this as "you may
 * transmit anywhere in this band".
 */
export interface BandChoice {
  name: string;
  startHz: number;
  endHz: number;
  defaultHz: number;
  /** Icom band-stacking-register index; `null` when the plan declares none. */
  bsrCode: number | null;
  defaultHzTxPermit: FrequencyPermit;
}

/**
 * Band facts (MOR-1262 decomposition slice 7A, MOR-1294): the current band,
 * the capability-derived band choice set with its per-band TX permits, and
 * the tuning envelope a frequency-entry surface validates against. Facts only
 * — no command emission; selecting a band or committing a typed frequency
 * stays with the surface (slice 7B).
 *
 * `bandChoices` is a plain list, not `BandField`-wrapped, for the same reason
 * `modeFilter`'s `modeChoices` and `dsp`'s `agcModes` aren't: a choice set is
 * a structural fact about the radio MODEL, not a live reading that can go
 * stale. Per the X6200 lesson it is read from the `caps` ARGUMENT only, via
 * the shipped `flattenBands` — never a frontend band table (the shipped
 * `BandSelector.svelte`'s hard-coded `BROADCAST_SW_BANDS`/`BROADCAST_LW_MW_BANDS`
 * presets are UI convenience, not radio facts, and are deliberately absent).
 *
 * `currentBand` is the shipped `findActiveBand` lookup over that same `caps`
 * argument, keyed on the ACTIVE receiver's observed frequency — `unknown`
 * when the frequency was never observed or when no band of the plan contains
 * it, where the shipped `toBandSelectorProps` substitutes a fabricated
 * 14.074 MHz.
 *
 * `currentBandTx` is the FAIL-CLOSED, LIVE-FREQUENCY answer to "may the
 * operator key right now" — see its own comment below. It is a different
 * question from `bandChoices[].defaultHzTxPermit`, which is a point sample at
 * a band's default frequency, and the two must never be conflated.
 *
 * `tuneMinHz`/`tuneMaxHz` are the frequency-entry constraint: the envelope of
 * the declared `freqRanges` (range bounds, not band bounds — the gaps between
 * bands are still tunable). `null` when the radio declares no range at all;
 * never `components-v2/display/frequency-tuning.ts::adjustFreqByDigit`'s
 * fabricated `0 … 999 MHz` defaults, which is the only bound v2 has (that
 * function has no production caller that supplies one).
 */
export interface BandViewModel {
  currentBand: BandField<string>;
  bandChoices: readonly BandChoice[];
  /**
   * SAFETY (MOR-1294, corrected by the verify F1 ruling). "May the operator
   * key RIGHT NOW, at the frequency the radio is actually on."
   *
   * Evaluated at the LIVE frequency — `getFrequencyPermit(observedFreqHz,
   * caps.txBands)`, the same single shipped derivation, just at the correct
   * argument — and NEVER inherited from `bandChoices[].defaultHzTxPermit`.
   * Inheriting the point sample is a demonstrated fail-OPEN whenever a
   * `txBands` segment is narrower than the band-plan band it sits in (live
   * 14.300 MHz reading `allowed` off a 14.000–14.150 allocation), which is
   * common, silent, and exactly what "out-of-band must stay denied, never
   * fail-open" forbids.
   *
   * Collapsed fail-closed exactly the way the shipped `getTxPermit` collapses
   * the tri-state (`$lib/utils/tx-permit`: "unknown fails closed").
   * `'allowed'` requires ALL of: a POSITIVELY known current band, a choice
   * entry for it, and a POSITIVELY `allowed` live-frequency permit. An
   * unobserved/stale/malformed frequency, an out-of-plan frequency, a band
   * absent from the choice set, an out-of-segment frequency and unconfigured
   * TX ranges all read `'denied'`. An unknown input must never enable a
   * TX-adjacent affordance — see `radio-view-model-adapter.ts`'s `deriveBand`,
   * and the cross-field invariant `validateRadioViewModel` enforces on this
   * pair.
   */
  currentBandTx: TxPermit;
  tuneMinHz: number | null;
  tuneMaxHz: number | null;
}

/**
 * A single RIT/XIT fact (MOR-1262 decomposition slice 8A, MOR-1295).
 * Shape-identical to `TxAuxField`, declared as an alias for the same reason
 * `BandField`/`RfFrontEndField`/`DspField`/`ModeFilterField`/`RxAudioField`
 * are: one field shape per fact family.
 */
export type RitXitField<T> = TxAuxField<T>;

/**
 * RIT/XIT facts (MOR-1262 decomposition slice 8A, MOR-1295): the RIT/XIT
 * enables and their frequency offset. Facts only — no command emission;
 * toggling RIT/XIT or dragging the offset stays with a future surface slice.
 *
 * `ritOffset`/`xitOffset` are DELIBERATELY two separate fields reading the
 * SAME underlying register (`ServerStatePublic.ritFreq` — one CI-V RIT/XIT
 * offset, shared), verbatim the shipped `toRitXitProps`
 * (`lib/runtime/props/panel-props.ts`): `ritOffset: state?.ritFreq`,
 * `xitOffset: state?.ritFreq`. This is not a duplicate-in-error; it is
 * parity with the one existing derivation, which the shipped `RitXitPanel`
 * itself resolves by displaying whichever is currently active
 * (`xitActive && !ritActive ? xitOffset : ritOffset`). A future surface
 * consuming this group makes that same choice from two honestly-identical
 * readings, not from a field this contract already collapsed for it.
 */
export interface RitXitViewModel {
  ritActive: RitXitField<boolean>;
  ritOffset: RitXitField<number>;
  xitActive: RitXitField<boolean>;
  xitOffset: RitXitField<number>;
}

/**
 * A single antenna fact (MOR-1262 decomposition slice 8A, MOR-1295).
 * Shape-identical to `TxAuxField`, declared as an alias for the same reason
 * `RitXitField`/`BandField`/`RfFrontEndField`/`DspField` are.
 */
export type AntennaField<T> = TxAuxField<T>;

/**
 * Antenna facts (MOR-1262 decomposition slice 8A, MOR-1295): the selected TX
 * antenna port, whether the (port-dependent) RX-antenna override is active,
 * and the capability-declared port count. ATU/tuner state is DELIBERATELY
 * absent here — it is family 1's `txAux.atu` (MOR-1244) already, and family
 * enumeration is explicit and CLOSED (same discipline `dsp`/`rfFrontEnd` use
 * for their own neighbors) — this group never duplicates it.
 *
 * `antennaCount` is a plain number, not an `AntennaField`-wrapped reading,
 * for the same reason `RfFrontEndViewModel.preValues` isn't: a port count is
 * a structural fact about the radio MODEL (from `caps.antennas`), not a live
 * reading that can itself go stale — see `radio-view-model-adapter.ts`'s
 * `deriveAntenna` for why it is also this group's WHOLE evidence gate (the
 * shipped `AntennaPanel`/`MobileRadioLayout` show nothing at all when
 * `antennaCount <= 1`, and RX-ANT only nested inside that same condition —
 * one v2 gate, not two).
 *
 * `rxAnt` reads the RX-antenna override for whichever port `txAntenna`
 * currently names (`ServerStatePublic.rxAntenna1`/`.rxAntenna2`) — the
 * shipped `toAntennaProps`'s own `txAntenna === 2 ? rxAntenna2 : rxAntenna1`
 * selection. Per the 4A′/5A "never derive from a half-observed pair" lesson,
 * it degrades to `unknown` whenever `txAntenna` itself is unobserved — an
 * honest reading of a port this contract cannot honestly name is not
 * possible, and silently falling back to port 1 is exactly the fabrication
 * this contract exists to forbid.
 */
export interface AntennaViewModel {
  txAntenna: AntennaField<number>;
  rxAnt: AntennaField<boolean>;
  antennaCount: number;
}

/**
 * A single scan fact (MOR-1262 decomposition slice 8A, MOR-1295).
 * Shape-identical to `TxAuxField`, declared as an alias for the same reason
 * `AntennaField`/`RitXitField`/`BandField`/`RfFrontEndField` are.
 */
export type ScanField<T> = TxAuxField<T>;

/**
 * Scan facts (MOR-1262 decomposition slice 8A, MOR-1295): whether a scan is
 * running, its type, and its resume mode. Facts only — the shipped scan-type
 * / ΔF-span / resume-mode LABEL tables (`ScanPanel.svelte`'s `scanTypes`/
 * `dfSpans`/`resumeModes` constants) are UI convenience, not radio facts, and
 * are deliberately absent (X6200 lesson: no UI-only tables in the fact
 * layer).
 *
 * There is no `scan` capability tag anywhere in v2 — the shipped `ScanPanel`
 * renders unconditionally — so, like `MetersViewModel`, evidence is
 * per-field "was this ever reported" (`radio-view-model-adapter.ts`'s
 * `deriveScan`), not a capability check.
 *
 * `scanResumeMode` carries the shipped `& 0x0F` mask applied verbatim
 * (`toScanProps`'s own `(state?.scanResumeMode ?? 0) & 0x0f`) — the raw field
 * carries a direction bit this contract does not interpret, so the mask is
 * consumed, not re-derived.
 */
export interface ScanViewModel {
  scanning: ScanField<boolean>;
  scanType: ScanField<number>;
  scanResumeMode: ScanField<number>;
}

/**
 * A single CW-keyer fact (MOR-1262 decomposition slice 9A, MOR-1296).
 * Shape-identical to `TxAuxField`, declared as an alias for the same reason
 * `ScanField`/`AntennaField`/`RitXitField`/`BandField` are.
 */
export type CwKeyerField<T> = TxAuxField<T>;

/**
 * Break-in state as a THREE-VALUED fact, never a boolean and never an int.
 * `off` = the key does not transmit; `semi` = keying transmits with a
 * hang-time (the delay fact below applies); `full` = QSK, the key transmits
 * immediately. The shipped v2 wire encoding is an int (`ServerStatePublic.
 * breakIn`, 0/1/2 — `components-v2/panels/cw-panel-logic.ts`'s
 * `BREAK_IN_LABELS`), decoded ONCE in `radio-view-model-adapter.ts`'s
 * `breakInMode`; an int this contract does not recognise decodes to the
 * field's `unknown` reading, where v2's `formatBreakIn` falls back to 'OFF'.
 * That difference is deliberate and is the whole point of the type: an
 * unreadable break-in state must never present as "the key is safe".
 */
export type BreakInMode = 'off' | 'semi' | 'full';

/**
 * CW-keyer facts (MOR-1262 decomposition slice 9A, MOR-1296) — SAFETY-CRITICAL.
 *
 * FACTS ONLY, and here that phrase carries more weight than in any previous
 * family: break-in KEYS THE TRANSMITTER. This group carries the READ state of
 * the keyer and nothing else — no toggle, no action, no command vocabulary,
 * not even a label table. `CwPanel.svelte`'s AUTO TUNE button (`cw_auto_tune`,
 * a transmit-causing action) has no representation here at all, the same way
 * `txAux` carries ATU *state* but never an ATU TUNE control (MOR-1244).
 * Constructing, validating or serializing this group must not be able to key
 * the radio; that is pinned by `__tests__/cw-keyer-purity.isolated.test.ts`, not merely
 * asserted here.
 *
 * NO SECOND PERMIT. This group states no TX permission of its own. "May
 * arming break-in cause the transmitter to key" is answered by the model's
 * ONE existing `RadioViewModel.txPermit` (produced by `deriveTxCapabilities`,
 * the same derivation the App TX authority uses — safety invariant R9: TX
 * truth never comes from `radioState.ptt`), surfaced for this family through
 * the existing `disabledReasons` home with `field: 'cwKeyer.breakIn'` — the
 * MOR-1293 ruling that a cross-field disable is a `disabledReasons` entry, not
 * a bespoke boolean, applied to the safety-critical case. A second permit
 * field here — or any re-derivation of `getFrequencyPermit` in the CW path —
 * is the forbidden defect, and `validateRadioViewModel` enforces the
 * fail-closed half structurally: a model that carries a structurally-available
 * `breakIn` while `txPermit` is anything other than `'allowed'` is REJECTED
 * unless it also records that disabled reason.
 *
 * THE APF/TPF MUTEX (MOR-479 lineage, MOR-1293 precedent). `toCwProps`
 * (`lib/runtime/props/panel-props.ts`) disables APF outside CW/CW-R and TPF
 * outside RTTY/RTTY-R, in its own words "mirrors the MOR-479 preamp mutex".
 * Same treatment as the DIGI-SEL/PREAMP mutex: no bespoke `apfDisabled`/
 * `tpfDisabled` booleans here, one `disabledReasons` entry each with the
 * generic `'mutually-exclusive-control'` code, and FAIL-CLOSED on an unknown
 * mode (see `radio-view-model-adapter.ts`'s `deriveCwKeyerReasons`).
 *
 * Family enumeration is explicit and CLOSED. Three facts the shipped
 * `CwProps` exposes are deliberately ABSENT because they belong to families
 * this slice must not duplicate or fabricate:
 *  - `sidetoneLevel` IS `txAux.monitorLevel` (`state.monitorGain`, family 1);
 *  - `currentMode` IS `modeFilter.currentMode` (family 4) — the mutex above
 *    READS that fact rather than re-reading `rx.mode` a second time;
 *  - `keyerType` has no state field at all in v2 (`toCwProps` hard-codes 0
 *    while `set_keyer_type` is write-only), so there is no fact to state and
 *    a placeholder would be exactly the fabrication this contract forbids.
 * `pitchHz` covers `cwPitch` and `sidetonePitch` together: both shipped props
 * read the one `state.cwPitch` register, so this is one fact, not two.
 */
export interface CwKeyerViewModel {
  breakIn: CwKeyerField<BreakInMode>;
  breakInDelay: CwKeyerField<number>;
  /** Keyer speed in WPM (`state.keySpeed`); `unknown`, never v2's 12. */
  keyerSpeed: CwKeyerField<number>;
  /** CW pitch / sidetone pitch in Hz (`state.cwPitch`); `unknown`, never 600. */
  pitchHz: CwKeyerField<number>;
  reversePaddle: CwKeyerField<boolean>;
  /** Audio peak filter type/level ordinal (`rx.apfTypeLevel`, 0 = off). */
  apf: CwKeyerField<number>;
  twinPeak: CwKeyerField<boolean>;
}

/**
 * A single scope-control fact (MOR-1262 decomposition slice 11A, MOR-1298).
 * Shape-identical to `TxAuxField`, declared as an alias for the same reason
 * `CwKeyerField`/`ScanField`/`AntennaField` are.
 */
export type ScopeControlsField<T> = TxAuxField<T>;

/**
 * Scope-control facts (MOR-1262 decomposition slice 11A/MOR-1298, extended
 * by slice 11A′/MOR-1299): the eight operator-navigated facts off the
 * shipped spectrum toolbar's own `scopeControls` block
 * (`components/spectrum/SpectrumToolbar.svelte`) — SPAN preset index, sweep
 * SPEED, DUAL-scope on/off, which RECEIVER (MAIN=0/SUB=1) feeds the scope
 * (11A), plus scope MODE (CTR/FIX/S-C/S-F), EDGE, HOLD and REF level (11A′).
 * The design doc that named this toolbar
 * (`docs/plans/2026-04-18-spectrum-controls.md`) calls the MAIN/SUB selector
 * the "scope-source selector" and proposes labelling it "SCOPE SRC" — the
 * decomposition ticket's "receiver/source" pair names this ONE wire field
 * (`ScopeControlsPublic.receiver`), not two.
 *
 * FACTS ONLY, and now the COMPLETE set of the eight `scopeControls.*` leaves
 * the backend gives field-status entries for (`runtime_helpers.py`'s
 * `_SCOPE_CONTROL_PUBLIC_FIELDS`) — MODE/EDGE/HOLD/REF were a recorded
 * enumeration gap in 11A (they matched `SpectrumToolbar.svelte`'s own
 * `scopeModeAvailable`/`scopeEdgeAvailable`/`scopeHoldAvailable`/
 * `scopeRefAvailable` booleans but were left out of the first slice; MOR-1299
 * closes the gap, same group, same `scope` gate, same namespace — not a
 * separate "display" family). Live scope FRAMES/WATERFALL DATA remain a
 * wholly different, App-owned resource demand (12A) and are never carried
 * here — this group states facts about the scope's CONTROLS, never its
 * pixels.
 *
 * PARITY: values mirror the same `scopeControls.<leaf>` register the toolbar
 * reads (`radio.current?.scopeControls`), and availability reuses the exact
 * same `isFieldAvailable(state, 'scopeControls.<leaf>')` predicate the
 * toolbar calls for its own `scope{Mode,Edge,Span,Speed,Hold,Ref,Dual,
 * Receiver}Available` booleans — not a reimplementation. Where this contract
 * DIVERGES from v2: the toolbar's `scopeControls?.span ?? 3` / `?? 1` /
 * `?? false` / `?? 0` fallbacks (and the analogous `?.mode` / `?.edge`
 * / `?.hold ?? false` / `?.refDb ?? 0` reads) fabricate a value on an
 * unobserved field; here an absent raw reads `unknown`, never those
 * defaults. When `mode` is absent, the entire row is hidden rather than
 * fabricating CTR. EDGE's applicability is UI-only, gated on the current MODE
 * value (`isEdgeApplicable` in `spectrum-toolbar-logic.ts` shows EDGE only
 * in FIX/S-F modes) — that is a rendering decision, not a fact-availability
 * distinction, so `edge`'s structural gate here mirrors `span`/`speed`/
 * `mode`/`hold`/`refDb`, not a mode-conditional gate.
 *
 * STRUCTURAL gate, doubly per the X6200 lesson (scope command support
 * varies per radio — IC-7300/IC-9700 lack the `27 12`/`27 13` receiver-select
 * commands even though `scope` is declared, and dual-scope operation is
 * IC-7610-only in practice per MOR-664): `span`/`speed`/`mode`/`edge`/
 * `hold`/`refDb` are structurally available under `hasCap('scope')` alone
 * (every scope-bearing single-RX radio supports them — the backend spec
 * (`state_pipeline_contracts.py`) declares all six as read-only ingress
 * leaves with no additional capability distinction), while `dual`/`receiver`
 * ADDITIONALLY require `hasCap('dual_rx')` — the only generic capability tag
 * this contract may use, per "no radio-specific tables in the frontend".
 * This is a real strengthening beyond the shipped toolbar, which gates
 * DUAL/MAIN-SUB on field-availability alone; where `dual_rx` still
 * over-declares for a specific radio (e.g. IC-9700, whose VHF/UHF `dual_rx`
 * is unrelated to scope receiver-select), the OPERATIONAL half degrades
 * honestly because the backend never observes that leaf for that radio —
 * same structural/operational split `hardwareScope`/`audioFftScope` already
 * use.
 */
export interface ScopeControlsViewModel {
  /** Scope mode ordinal: 0=CTR, 1=FIX, 2=S-C (scroll-center), 3=S-F
   *  (scroll-fixed) — `MODE_BUTTONS` in `spectrum-toolbar-logic.ts` maps the
   *  ordinal to a display label; that table is UI convenience, not a fact,
   *  and is deliberately absent here. */
  mode: ScopeControlsField<number>;
  /** Fixed-edge preset index 1..4. Applicable only in FIX/S-F modes per the
   *  toolbar's `isEdgeApplicable`, but that is a rendering decision — this
   *  fact is structurally available whenever the group is, same as `mode`. */
  edge: ScopeControlsField<number>;
  /** Span preset index 0..7 (`SPAN_LABELS` in `spectrum-toolbar-logic.ts`
   *  maps the ordinal to a display string; that table is UI convenience,
   *  not a fact, and is deliberately absent here). */
  span: ScopeControlsField<number>;
  /** Sweep-speed ordinal 0=FST/1=MID/2=SLO. */
  speed: ScopeControlsField<number>;
  hold: ScopeControlsField<boolean>;
  /** Reference level in dB, clamped [-30, 10] by the toolbar's `clampRef`
   *  (that clamp is a UI editing convenience, not a fact constraint — the
   *  raw value here is whatever the radio reports). */
  refDb: ScopeControlsField<number>;
  dual: ScopeControlsField<boolean>;
  /** 0=MAIN, 1=SUB. */
  receiver: ScopeControlsField<number>;
}

export interface RadioViewModel {
  topologyId: string;
  vfoScheme: VfoScheme;
  activeReceiver: ActiveRx;
  vfos: readonly VfoViewModel[];
  /** Orthogonal wire booleans (state.ts `split`/`dualWatch`; independent CI-V
   *  commands) — both may be true, false, or unobserved independently. */
  split: BooleanFact;
  dualWatch: BooleanFact;
  txTarget: TxTargetViewModel;
  txPermit: FrequencyPermit;
  scope: ScopeAvailabilityViewModel;
  disabledReasons: readonly DisabledReason[];
  /** Absent (MOR-1264 optional group) ⇒ structurally unavailable: this radio
   *  model has no TX-adjacent controls at all. Never emitted as a placeholder
   *  of all-unknowns — see `radio-view-model-adapter.ts`'s evidence gate. */
  readonly txAux?: TxAuxViewModel;
  /** Absent (MOR-1264 optional group) ⇒ this radio reports no meters at all,
   *  or the App TX authority was not supplied so no honest TX-relevance could
   *  be stated — see `radio-view-model-adapter.ts`'s `deriveMeters`. */
  readonly meters?: MetersViewModel;
  /** Absent (MOR-1264 optional group) ⇒ the App-owned RX-audio snapshot was
   *  not supplied, or this radio has no RX-audio chain to describe — see
   *  `radio-view-model-adapter.ts`'s `deriveRxAudio`. */
  readonly rxAudio?: RxAudioViewModel;
  /** Absent (MOR-1264 optional group) ⇒ this radio declares no modes and no
   *  filters and nothing mode/filter-shaped was ever observed — see
   *  `radio-view-model-adapter.ts`'s `deriveModeFilter`. */
  readonly modeFilter?: ModeFilterViewModel;
  /** Absent (MOR-1264 optional group) ⇒ this radio declares no filters, no
   *  PBT, no IF-shift and no DATA-mode capability — see
   *  `radio-view-model-adapter.ts`'s `deriveFilterPassband`. */
  readonly filterPassband?: FilterPassbandViewModel;
  /** Absent (MOR-1264 optional group) ⇒ this radio declares no NR, no NB, no
   *  notch and no AGC capability — see `radio-view-model-adapter.ts`'s
   *  `deriveDsp`. */
  readonly dsp?: DspViewModel;
  /** Absent (MOR-1264 optional group) ⇒ this radio declares no preamp, no
   *  attenuator, no RF-gain, no squelch, no DIGI-SEL and no IP+ capability
   *  (MOR-1293 added the latter two) — see
   *  `radio-view-model-adapter.ts`'s `deriveRfFrontEnd`. */
  readonly rfFrontEnd?: RfFrontEndViewModel;
  /** Absent (MOR-1264 optional group) ⇒ this radio declares no frequency
   *  range at all, so there is no band plan and no tuning envelope to state
   *  — see `radio-view-model-adapter.ts`'s `deriveBand`. */
  readonly band?: BandViewModel;
  /** Absent (MOR-1264 optional group) ⇒ this radio declares neither `rit`
   *  nor `xit` capability — see `radio-view-model-adapter.ts`'s
   *  `deriveRitXit`. */
  readonly ritXit?: RitXitViewModel;
  /** Absent (MOR-1264 optional group) ⇒ this radio declares one antenna
   *  port or fewer, so there is no port to select — see
   *  `radio-view-model-adapter.ts`'s `deriveAntenna`. */
  readonly antenna?: AntennaViewModel;
  /** Absent (MOR-1264 optional group) ⇒ this radio has never reported
   *  scanning/scanType/scanResumeMode — see `radio-view-model-adapter.ts`'s
   *  `deriveScan`. */
  readonly scan?: ScanViewModel;
  /** Absent (MOR-1264 optional group) ⇒ this radio declares no `cw`
   *  capability, so v2 renders no CW panel at all — see
   *  `radio-view-model-adapter.ts`'s `deriveCwKeyer`. */
  readonly cwKeyer?: CwKeyerViewModel;
  /** Absent (MOR-1264 optional group) ⇒ this radio declares no `scope`
   *  capability, so v2 renders no spectrum toolbar at all — see
   *  `radio-view-model-adapter.ts`'s `deriveScopeControls`. */
  readonly scopeControls?: ScopeControlsViewModel;
}

const RECEIVER_IDS: readonly ReceiverId[] = ['MAIN', 'SUB'];
const SLOT_IDS: readonly VfoSlotId[] = ['A', 'B'];
const VFO_SCHEMES: readonly VfoScheme[] = ['single', 'ab', 'ab_shared', 'main_sub'];
const DISABLED_REASON_CODES: readonly DisabledReasonCode[] = [
  'capability-unavailable', 'field-not-observed', 'tx-target-unknown', 'out-of-band',
  'mutually-exclusive-control',
];

function oneOf<T>(value: unknown, allowed: readonly T[], path: string): T {
  if (!allowed.includes(value as T)) invalid(path, allowed.join(' | '));
  return value as T;
}
function bool(value: unknown, path: string): boolean {
  if (typeof value !== 'boolean') invalid(path, 'a boolean');
  return value;
}
function nullableNumber(value: unknown, path: string): number | null {
  if (value !== null && (typeof value !== 'number' || !Number.isFinite(value))) {
    invalid(path, 'a finite number or null');
  }
  return value as number | null;
}
function nullableString(value: unknown, path: string): string | null {
  if (value !== null && typeof value !== 'string') invalid(path, 'a string or null');
  return value as string | null;
}
function num(value: unknown, path: string): number {
  if (typeof value !== 'number' || !Number.isFinite(value)) invalid(path, 'a finite number');
  return value;
}

/**
 * Declares a fact group as optional (MOR-1264, decision (b) of the MOR-1262
 * decomposition's slice 0): present ⇒ validated strictly by `validate` (the
 * group runs its own `exactKeys`/shape checks, unchanged); absent (`value`
 * is `undefined`) ⇒ the field stays `undefined` on the returned model and
 * the family is structurally unavailable — the MOR-988 §11.3 degrade-to-
 * unknown doctrine applied at the group level, not a new relaxation.
 *
 * A group is optional *only* because its key is listed in the containing
 * `exactKeys(...)` allow-list alongside the required keys — `exactKeys`
 * itself is untouched, so a key absent from that list is still rejected as
 * an extra. This is what keeps "optional" bounded rather than "anything
 * goes". A future slice declares and reads its group with one line each,
 * e.g. (MOR-1244, `txAux`) — `validateTxAux` built from the same
 * `record`/`exactKeys`/`str` imported from `./validator-primitives`:
 *   exactKeys(v, [...requiredKeys, 'txAux'], '$');
 *   txAux: optionalGroup(v.txAux, '$.txAux', validateTxAux),
 */
export function optionalGroup<T>(
  value: unknown,
  path: string,
  validate: (value: unknown, path: string) => T,
): T | undefined {
  return value === undefined ? undefined : validate(value, path);
}

function validateVfoSlot(value: unknown, path: string): VfoSlot {
  const v = record(value, path);
  if (v.kind === 'slotted') {
    exactKeys(v, ['kind', 'id'], path);
    return { kind: 'slotted', id: oneOf(v.id, SLOT_IDS, `${path}.id`) };
  }
  if (v.kind === 'unslotted') {
    exactKeys(v, ['kind'], path);
    return { kind: 'unslotted' };
  }
  if (v.kind === 'unknown') {
    exactKeys(v, ['kind'], path);
    return { kind: 'unknown' };
  }
  invalid(`${path}.kind`, `'slotted' | 'unslotted' | 'unknown'`);
}
function slotEqual(a: VfoSlot, b: VfoSlot): boolean {
  return a.kind === 'slotted' && b.kind === 'slotted' ? a.id === b.id : a.kind === b.kind;
}

function validateActiveRx(value: unknown, path: string): ActiveRx {
  const v = record(value, path);
  if (v.status === 'known') {
    exactKeys(v, ['status', 'receiver'], path);
    return { status: 'known', receiver: oneOf(v.receiver, RECEIVER_IDS, `${path}.receiver`) };
  }
  if (v.status === 'unknown') {
    exactKeys(v, ['status'], path);
    return { status: 'unknown' };
  }
  invalid(`${path}.status`, `'known' | 'unknown'`);
}

function validateBooleanFact(value: unknown, path: string): BooleanFact {
  const v = record(value, path);
  if (v.status === 'known') {
    exactKeys(v, ['status', 'value'], path);
    return { status: 'known', value: bool(v.value, `${path}.value`) };
  }
  if (v.status === 'unknown') {
    exactKeys(v, ['status'], path);
    return { status: 'unknown' };
  }
  invalid(`${path}.status`, `'known' | 'unknown'`);
}

function validateAvailability(value: unknown, path: string): Availability {
  const v = record(value, path);
  exactKeys(v, ['structural', 'operational'], path);
  return { structural: bool(v.structural, `${path}.structural`), operational: bool(v.operational, `${path}.operational`) };
}

function validateVfo(value: unknown, path: string): VfoViewModel {
  const v = record(value, path);
  exactKeys(v, ['receiver', 'slot', 'label', 'frequencyHz', 'mode', 'filter', 'isActive', 'isTxTarget'], path);
  return {
    receiver: oneOf(v.receiver, RECEIVER_IDS, `${path}.receiver`),
    slot: validateVfoSlot(v.slot, `${path}.slot`),
    label: str(v.label, `${path}.label`),
    frequencyHz: nullableNumber(v.frequencyHz, `${path}.frequencyHz`),
    mode: nullableString(v.mode, `${path}.mode`),
    filter: nullableString(v.filter, `${path}.filter`),
    isActive: bool(v.isActive, `${path}.isActive`),
    isTxTarget: bool(v.isTxTarget, `${path}.isTxTarget`),
  };
}

function validateTxTarget(value: unknown, path: string): TxTargetViewModel {
  const v = record(value, path);
  if (v.status === 'known') {
    exactKeys(v, ['status', 'receiver', 'slot', 'frequencyHz'], path);
    return {
      status: 'known',
      receiver: oneOf(v.receiver, RECEIVER_IDS, `${path}.receiver`),
      slot: validateVfoSlot(v.slot, `${path}.slot`),
      frequencyHz: nullableNumber(v.frequencyHz, `${path}.frequencyHz`),
    };
  }
  if (v.status === 'unknown') {
    exactKeys(v, ['status', 'reason'], path);
    return {
      status: 'unknown',
      reason: oneOf(v.reason, ['not-observed', 'stale', 'unsupported', 'contradiction'] as const, `${path}.reason`),
    };
  }
  invalid(`${path}.status`, `'known' | 'unknown'`);
}

function validateTxPermit(value: unknown, path: string): FrequencyPermit {
  const v = record(value, path);
  if (v.status === 'allowed') {
    exactKeys(v, ['status', 'band'], path);
    return { status: 'allowed', band: nullableString(v.band, `${path}.band`) };
  }
  if (v.status === 'denied') {
    exactKeys(v, ['status', 'reason'], path);
    return { status: 'denied', reason: oneOf(v.reason, ['outside-configured-ranges'] as const, `${path}.reason`) };
  }
  if (v.status === 'unknown') {
    exactKeys(v, ['status', 'reason'], path);
    return {
      status: 'unknown',
      reason: oneOf(v.reason, ['ranges-unconfigured', 'tx-target-unknown'] as const, `${path}.reason`),
    };
  }
  invalid(`${path}.status`, `'allowed' | 'denied' | 'unknown'`);
}

function validateDisabledReason(value: unknown, path: string): DisabledReason {
  const v = record(value, path);
  exactKeys(v, ['field', 'code'], path);
  return { field: str(v.field, `${path}.field`), code: oneOf(v.code, DISABLED_REASON_CODES, `${path}.code`) };
}

const METER_RF_STATES: readonly MeterRfState[] = ['receiving', 'transmitting', 'uncertain', 'unknown'];

function validateMeterField(value: unknown, path: string): MeterField {
  const v = record(value, path);
  exactKeys(v, ['reading', 'availability', 'relevant'], path);
  const r = record(v.reading, `${path}.reading`);
  let reading: MeterReading;
  if (r.status === 'known') {
    exactKeys(r, ['status', 'value'], `${path}.reading`);
    reading = { status: 'known', value: num(r.value, `${path}.reading.value`) };
  } else if (r.status === 'unknown') {
    exactKeys(r, ['status'], `${path}.reading`);
    reading = { status: 'unknown' };
  } else {
    invalid(`${path}.reading.status`, "'known' | 'unknown'");
  }
  return {
    reading,
    availability: validateAvailability(v.availability, `${path}.availability`),
    relevant: bool(v.relevant, `${path}.relevant`),
  };
}

/** This `exactKeys` list is exactly the seven meters the adapter reads plus
 *  the authoritative `rfState` — no speculative keys (MOR-1244 finding N4).
 *  See `radio-view-model-adapter.ts::deriveMeters`. */
function validateMeters(value: unknown, path: string): MetersViewModel {
  const v = record(value, path);
  exactKeys(v, [
    'rfState', 'signal', 'power', 'swr', 'alc', 'compression', 'drainVoltage', 'drainCurrent',
  ], path);
  return {
    rfState: oneOf(v.rfState, METER_RF_STATES, `${path}.rfState`),
    signal: validateMeterField(v.signal, `${path}.signal`),
    power: validateMeterField(v.power, `${path}.power`),
    swr: validateMeterField(v.swr, `${path}.swr`),
    alc: validateMeterField(v.alc, `${path}.alc`),
    compression: validateMeterField(v.compression, `${path}.compression`),
    drainVoltage: validateMeterField(v.drainVoltage, `${path}.drainVoltage`),
    drainCurrent: validateMeterField(v.drainCurrent, `${path}.drainCurrent`),
  };
}

const ATU_STATUSES: readonly AtuStatus[] = ['off', 'on', 'tuning'];

function validateTxAuxField<T>(
  value: unknown, path: string, validateValue: (v: unknown, p: string) => T,
): TxAuxField<T> {
  const v = record(value, path);
  exactKeys(v, ['reading', 'availability'], path);
  const r = record(v.reading, `${path}.reading`);
  let reading: TxAuxReading<T>;
  if (r.status === 'known') {
    exactKeys(r, ['status', 'value'], `${path}.reading`);
    reading = { status: 'known', value: validateValue(r.value, `${path}.reading.value`) };
  } else if (r.status === 'unknown') {
    exactKeys(r, ['status'], `${path}.reading`);
    reading = { status: 'unknown' };
  } else {
    invalid(`${path}.reading.status`, "'known' | 'unknown'");
  }
  return { reading, availability: validateAvailability(v.availability, `${path}.availability`) };
}

/** N4: this `exactKeys` list is exactly the 12 fields the adapter reads —
 *  no speculative keys. See `radio-view-model-adapter.ts::deriveTxAux`. */
function validateTxAux(value: unknown, path: string): TxAuxViewModel {
  const v = record(value, path);
  exactKeys(v, [
    'atu', 'vox', 'voxGain', 'antiVoxGain', 'voxDelay',
    'compressor', 'compressorLevel', 'monitor', 'monitorLevel',
    'rfPower', 'micGain', 'driveGain',
  ], path);
  return {
    atu: validateTxAuxField(v.atu, `${path}.atu`, (val, p) => oneOf(val, ATU_STATUSES, p)),
    vox: validateTxAuxField(v.vox, `${path}.vox`, bool),
    voxGain: validateTxAuxField(v.voxGain, `${path}.voxGain`, num),
    antiVoxGain: validateTxAuxField(v.antiVoxGain, `${path}.antiVoxGain`, num),
    voxDelay: validateTxAuxField(v.voxDelay, `${path}.voxDelay`, num),
    compressor: validateTxAuxField(v.compressor, `${path}.compressor`, bool),
    compressorLevel: validateTxAuxField(v.compressorLevel, `${path}.compressorLevel`, num),
    monitor: validateTxAuxField(v.monitor, `${path}.monitor`, bool),
    monitorLevel: validateTxAuxField(v.monitorLevel, `${path}.monitorLevel`, num),
    rfPower: validateTxAuxField(v.rfPower, `${path}.rfPower`, num),
    micGain: validateTxAuxField(v.micGain, `${path}.micGain`, num),
    driveGain: validateTxAuxField(v.driveGain, `${path}.driveGain`, num),
  };
}

const MONITOR_MODES: readonly MonitorMode[] = ['local', 'live', 'mute'];
const AUDIO_FOCUSES: readonly AudioFocus[] = ['main', 'sub', 'both'];

/** The `source` carried by `ready`/`mismatch` is the offending/confirmed
 *  MOD-input enum value; a bare status must NOT carry one (that would make
 *  "not applicable" indistinguishable from a read). */
function validateModInputReadiness(value: unknown, path: string): ModInputReadiness {
  const v = record(value, path);
  if (v.status === 'ready') {
    exactKeys(v, ['status', 'source'], path);
    return { status: 'ready', source: num(v.source, `${path}.source`) };
  }
  if (v.status === 'mismatch') {
    exactKeys(v, ['status', 'source'], path);
    return { status: 'mismatch', source: num(v.source, `${path}.source`) };
  }
  if (v.status === 'not-applicable') {
    exactKeys(v, ['status'], path);
    return { status: 'not-applicable' };
  }
  if (v.status === 'unknown') {
    exactKeys(v, ['status'], path);
    return { status: 'unknown' };
  }
  invalid(`${path}.status`, "'not-applicable' | 'ready' | 'mismatch' | 'unknown'");
}

/** N4 again: exactly the seven facts the adapter reads, no speculative keys.
 *  The per-field validator is `validateTxAuxField` — `RxAudioField` IS
 *  `TxAuxField`, so sharing it is the alias's whole point, not a shortcut.
 *  See `radio-view-model-adapter.ts::deriveRxAudio`. */
function validateRxAudio(value: unknown, path: string): RxAudioViewModel {
  const v = record(value, path);
  exactKeys(v, [
    'monitorMode', 'liveAudio', 'afLevel', 'routingFocus', 'routingSplit',
    'modInputSource', 'modInputReadiness',
  ], path);
  return {
    monitorMode: oneOf(v.monitorMode, MONITOR_MODES, `${path}.monitorMode`),
    liveAudio: validateAvailability(v.liveAudio, `${path}.liveAudio`),
    afLevel: validateTxAuxField(v.afLevel, `${path}.afLevel`, num),
    routingFocus: validateTxAuxField(
      v.routingFocus, `${path}.routingFocus`, (val, p) => oneOf(val, AUDIO_FOCUSES, p),
    ),
    routingSplit: validateTxAuxField(v.routingSplit, `${path}.routingSplit`, bool),
    modInputSource: validateTxAuxField(v.modInputSource, `${path}.modInputSource`, num),
    modInputReadiness: validateModInputReadiness(v.modInputReadiness, `${path}.modInputReadiness`),
  };
}

/** A capability-derived choice set: plain strings, no field-shape wrapper —
 *  see `ModeFilterViewModel`'s doc comment for why. */
function strArray(value: unknown, path: string): string[] {
  if (!Array.isArray(value)) invalid(path, 'an array of strings');
  return value.map((item, i) => str(item, `${path}[${i}]`));
}

/** Same idea as `strArray`, for `DspViewModel.agcModes` — see its doc comment. */
function numArray(value: unknown, path: string): number[] {
  if (!Array.isArray(value)) invalid(path, 'an array of numbers');
  return value.map((item, i) => num(item, `${path}[${i}]`));
}

/** N4 again: exactly the seven facts the adapter reads, no speculative keys.
 *  See `radio-view-model-adapter.ts::deriveModeFilter`. */
function validateModeFilter(value: unknown, path: string): ModeFilterViewModel {
  const v = record(value, path);
  exactKeys(v, [
    'currentMode', 'modeChoices', 'currentFilter', 'filterChoices',
    'filterWidth', 'filterWidthMin', 'filterWidthMax',
  ], path);
  return {
    currentMode: validateTxAuxField(v.currentMode, `${path}.currentMode`, str),
    modeChoices: strArray(v.modeChoices, `${path}.modeChoices`),
    currentFilter: validateTxAuxField(v.currentFilter, `${path}.currentFilter`, num),
    filterChoices: strArray(v.filterChoices, `${path}.filterChoices`),
    filterWidth: validateTxAuxField(v.filterWidth, `${path}.filterWidth`, num),
    filterWidthMin: validateTxAuxField(v.filterWidthMin, `${path}.filterWidthMin`, num),
    filterWidthMax: validateTxAuxField(v.filterWidthMax, `${path}.filterWidthMax`, num),
  };
}

/** N4 again: exactly the five facts the adapter reads, no speculative keys.
 *  See `radio-view-model-adapter.ts::deriveFilterPassband`. */
function validateFilterPassband(value: unknown, path: string): FilterPassbandViewModel {
  const v = record(value, path);
  exactKeys(v, ['filterShape', 'ifShift', 'pbtInner', 'pbtOuter', 'dataMode'], path);
  return {
    filterShape: validateTxAuxField(v.filterShape, `${path}.filterShape`, num),
    ifShift: validateTxAuxField(v.ifShift, `${path}.ifShift`, num),
    pbtInner: validateTxAuxField(v.pbtInner, `${path}.pbtInner`, num),
    pbtOuter: validateTxAuxField(v.pbtOuter, `${path}.pbtOuter`, num),
    dataMode: validateTxAuxField(v.dataMode, `${path}.dataMode`, num),
  };
}

const NOTCH_MODES = ['off', 'auto', 'manual'] as const;

/** N4 again: exactly the twelve facts the adapter reads, no speculative keys.
 *  See `radio-view-model-adapter.ts::deriveDsp`. */
function validateDsp(value: unknown, path: string): DspViewModel {
  const v = record(value, path);
  exactKeys(v, [
    'nrActive', 'nrLevel', 'nbActive', 'nbLevel', 'nbDepth', 'nbWidth',
    'notchMode', 'notchFreq', 'manualNotchWidth', 'agcMode', 'agcModes', 'agcTimeConstant',
  ], path);
  return {
    nrActive: validateTxAuxField(v.nrActive, `${path}.nrActive`, bool),
    nrLevel: validateTxAuxField(v.nrLevel, `${path}.nrLevel`, num),
    nbActive: validateTxAuxField(v.nbActive, `${path}.nbActive`, bool),
    nbLevel: validateTxAuxField(v.nbLevel, `${path}.nbLevel`, num),
    nbDepth: validateTxAuxField(v.nbDepth, `${path}.nbDepth`, num),
    nbWidth: validateTxAuxField(v.nbWidth, `${path}.nbWidth`, num),
    notchMode: validateTxAuxField(v.notchMode, `${path}.notchMode`, (val, p) => oneOf(val, NOTCH_MODES, p)),
    notchFreq: validateTxAuxField(v.notchFreq, `${path}.notchFreq`, num),
    manualNotchWidth: validateTxAuxField(v.manualNotchWidth, `${path}.manualNotchWidth`, num),
    agcMode: validateTxAuxField(v.agcMode, `${path}.agcMode`, num),
    agcModes: numArray(v.agcModes, `${path}.agcModes`),
    agcTimeConstant: validateTxAuxField(v.agcTimeConstant, `${path}.agcTimeConstant`, num),
  };
}

/** N4 again: exactly the eight facts the adapter reads (MOR-1293 added
 *  `digiSel`/`ipPlus` to 6A's original six), no speculative keys.
 *  See `radio-view-model-adapter.ts::deriveRfFrontEnd`. */
function validateRfFrontEnd(value: unknown, path: string): RfFrontEndViewModel {
  const v = record(value, path);
  exactKeys(v, [
    'preamp', 'preValues', 'attenuator', 'attValues', 'rfGain', 'squelch', 'digiSel', 'ipPlus',
  ], path);
  return {
    preamp: validateTxAuxField(v.preamp, `${path}.preamp`, num),
    preValues: numArray(v.preValues, `${path}.preValues`),
    attenuator: validateTxAuxField(v.attenuator, `${path}.attenuator`, num),
    attValues: numArray(v.attValues, `${path}.attValues`),
    rfGain: validateTxAuxField(v.rfGain, `${path}.rfGain`, num),
    squelch: validateTxAuxField(v.squelch, `${path}.squelch`, num),
    digiSel: validateTxAuxField(v.digiSel, `${path}.digiSel`, bool),
    ipPlus: validateTxAuxField(v.ipPlus, `${path}.ipPlus`, bool),
  };
}

const TX_PERMITS: readonly TxPermit[] = ['allowed', 'denied'];

/** One band-plan entry plus its own default-frequency TX point sample — the
 *  permit is validated by the SAME `validateTxPermit` the top-level
 *  `txPermit` uses, because it IS the same tri-state (MOR-1294); a divergent
 *  per-band permit shape would be the second derivation this slice exists to
 *  prevent. */
function validateBandChoice(value: unknown, path: string): BandChoice {
  const v = record(value, path);
  exactKeys(v, ['name', 'startHz', 'endHz', 'defaultHz', 'bsrCode', 'defaultHzTxPermit'], path);
  return {
    name: str(v.name, `${path}.name`),
    startHz: num(v.startHz, `${path}.startHz`),
    endHz: num(v.endHz, `${path}.endHz`),
    defaultHz: num(v.defaultHz, `${path}.defaultHz`),
    bsrCode: nullableNumber(v.bsrCode, `${path}.bsrCode`),
    defaultHzTxPermit: validateTxPermit(v.defaultHzTxPermit, `${path}.defaultHzTxPermit`),
  };
}

/** N4 again: exactly the five facts the adapter reads, no speculative keys.
 *  See `radio-view-model-adapter.ts::deriveBand`. */
function validateBand(value: unknown, path: string): BandViewModel {
  const v = record(value, path);
  exactKeys(v, ['currentBand', 'bandChoices', 'currentBandTx', 'tuneMinHz', 'tuneMaxHz'], path);
  if (!Array.isArray(v.bandChoices)) invalid(`${path}.bandChoices`, 'an array');
  const currentBand = validateTxAuxField(v.currentBand, `${path}.currentBand`, str);
  const currentBandTx = oneOf(v.currentBandTx, TX_PERMITS, `${path}.currentBandTx`);
  // The fail-closed cross-field invariant (MOR-1294), the same shape as the
  // model-level "txPermit 'allowed' only when txTarget is known" pin below:
  // a band the radio could not identify must never carry TX permission.
  if (currentBandTx === 'allowed' && currentBand.reading.status === 'unknown') {
    invalid(`${path}.currentBandTx`, "'denied' whenever currentBand is unknown (fail-open otherwise)");
  }
  return {
    currentBand,
    bandChoices: v.bandChoices.map((b, i) => validateBandChoice(b, `${path}.bandChoices[${i}]`)),
    currentBandTx,
    tuneMinHz: nullableNumber(v.tuneMinHz, `${path}.tuneMinHz`),
    tuneMaxHz: nullableNumber(v.tuneMaxHz, `${path}.tuneMaxHz`),
  };
}

/** Exactly the four facts the adapter reads. See
 *  `radio-view-model-adapter.ts::deriveRitXit`. */
function validateRitXit(value: unknown, path: string): RitXitViewModel {
  const v = record(value, path);
  exactKeys(v, ['ritActive', 'ritOffset', 'xitActive', 'xitOffset'], path);
  return {
    ritActive: validateTxAuxField(v.ritActive, `${path}.ritActive`, bool),
    ritOffset: validateTxAuxField(v.ritOffset, `${path}.ritOffset`, num),
    xitActive: validateTxAuxField(v.xitActive, `${path}.xitActive`, bool),
    xitOffset: validateTxAuxField(v.xitOffset, `${path}.xitOffset`, num),
  };
}

/** Exactly the three facts the adapter reads. See
 *  `radio-view-model-adapter.ts::deriveAntenna`. */
function validateAntenna(value: unknown, path: string): AntennaViewModel {
  const v = record(value, path);
  exactKeys(v, ['txAntenna', 'rxAnt', 'antennaCount'], path);
  return {
    txAntenna: validateTxAuxField(v.txAntenna, `${path}.txAntenna`, num),
    rxAnt: validateTxAuxField(v.rxAnt, `${path}.rxAnt`, bool),
    antennaCount: num(v.antennaCount, `${path}.antennaCount`),
  };
}

/** Exactly the three facts the adapter reads. See
 *  `radio-view-model-adapter.ts::deriveScan`. */
function validateScan(value: unknown, path: string): ScanViewModel {
  const v = record(value, path);
  exactKeys(v, ['scanning', 'scanType', 'scanResumeMode'], path);
  return {
    scanning: validateTxAuxField(v.scanning, `${path}.scanning`, bool),
    scanType: validateTxAuxField(v.scanType, `${path}.scanType`, num),
    scanResumeMode: validateTxAuxField(v.scanResumeMode, `${path}.scanResumeMode`, num),
  };
}

const BREAK_IN_MODES: readonly BreakInMode[] = ['off', 'semi', 'full'];

/** Exactly the seven facts the adapter reads. See
 *  `radio-view-model-adapter.ts::deriveCwKeyer`. */
function validateCwKeyer(value: unknown, path: string): CwKeyerViewModel {
  const v = record(value, path);
  exactKeys(v, [
    'breakIn', 'breakInDelay', 'keyerSpeed', 'pitchHz', 'reversePaddle', 'apf', 'twinPeak',
  ], path);
  return {
    breakIn: validateTxAuxField(v.breakIn, `${path}.breakIn`, (val, p) => oneOf(val, BREAK_IN_MODES, p)),
    breakInDelay: validateTxAuxField(v.breakInDelay, `${path}.breakInDelay`, num),
    keyerSpeed: validateTxAuxField(v.keyerSpeed, `${path}.keyerSpeed`, num),
    pitchHz: validateTxAuxField(v.pitchHz, `${path}.pitchHz`, num),
    reversePaddle: validateTxAuxField(v.reversePaddle, `${path}.reversePaddle`, bool),
    apf: validateTxAuxField(v.apf, `${path}.apf`, num),
    twinPeak: validateTxAuxField(v.twinPeak, `${path}.twinPeak`, bool),
  };
}

/** Exactly the eight facts the adapter reads (11A's four plus 11A′'s
 *  mode/edge/hold/refDb). See
 *  `radio-view-model-adapter.ts::deriveScopeControls`. */
function validateScopeControls(value: unknown, path: string): ScopeControlsViewModel {
  const v = record(value, path);
  exactKeys(v, ['mode', 'edge', 'span', 'speed', 'hold', 'refDb', 'dual', 'receiver'], path);
  return {
    mode: validateTxAuxField(v.mode, `${path}.mode`, num),
    edge: validateTxAuxField(v.edge, `${path}.edge`, num),
    span: validateTxAuxField(v.span, `${path}.span`, num),
    speed: validateTxAuxField(v.speed, `${path}.speed`, num),
    hold: validateTxAuxField(v.hold, `${path}.hold`, bool),
    refDb: validateTxAuxField(v.refDb, `${path}.refDb`, num),
    dual: validateTxAuxField(v.dual, `${path}.dual`, bool),
    receiver: validateTxAuxField(v.receiver, `${path}.receiver`, num),
  };
}

/** Runtime validator (repo idiom: throws TypeError with a `$.path`, see `validateCapabilities`).
 *  Also enforces two cross-field invariants (review cycle 1, V1): `txPermit`
 *  cannot be 'allowed' while `txTarget` is unknown (no fail-open), and
 *  `isTxTarget` can be true only on the VFO a known `txTarget` names. */
export function validateRadioViewModel(value: unknown): RadioViewModel {
  const v = record(value, '$');
  exactKeys(v, [
    'topologyId', 'vfoScheme', 'activeReceiver', 'vfos', 'split', 'dualWatch',
    'txTarget', 'txPermit', 'scope', 'disabledReasons', 'txAux', 'meters', 'rxAudio', 'modeFilter',
    'filterPassband', 'dsp', 'rfFrontEnd', 'band', 'ritXit', 'antenna', 'scan', 'cwKeyer',
    'scopeControls',
  ], '$');
  if (!Array.isArray(v.vfos)) invalid('$.vfos', 'an array');
  if (!Array.isArray(v.disabledReasons)) invalid('$.disabledReasons', 'an array');
  const scope = record(v.scope, '$.scope');
  exactKeys(scope, ['hardwareScope', 'audioFftScope'], '$.scope');

  const vfos = v.vfos.map((vfo, i) => validateVfo(vfo, `$.vfos[${i}]`));
  const txTarget = validateTxTarget(v.txTarget, '$.txTarget');
  const txPermit = validateTxPermit(v.txPermit, '$.txPermit');

  if (txPermit.status === 'allowed' && txTarget.status === 'unknown') {
    invalid('$.txPermit', "'allowed' only when txTarget is known (fail-open otherwise)");
  }
  vfos.forEach((vfo, i) => {
    const matches = txTarget.status === 'known'
      && vfo.receiver === txTarget.receiver && slotEqual(vfo.slot, txTarget.slot);
    if (vfo.isTxTarget && !matches) {
      invalid(`$.vfos[${i}].isTxTarget`, 'true only on the VFO matching a known txTarget');
    }
  });

  // Conditional spread, not `txAux: optionalGroup(...)` directly: an absent
  // group must OMIT the key, not merely set it to `undefined` — a plain
  // property assignment still shows up in `Object.keys()`, which would make
  // "structurally unavailable" indistinguishable from "present" to any
  // consumer that inventories keys (see the adapter test's exact-key-list
  // assertion this was verified against).
  const txAux = optionalGroup(v.txAux, '$.txAux', validateTxAux);
  const meters = optionalGroup(v.meters, '$.meters', validateMeters);
  const rxAudio = optionalGroup(v.rxAudio, '$.rxAudio', validateRxAudio);
  const modeFilter = optionalGroup(v.modeFilter, '$.modeFilter', validateModeFilter);
  const filterPassband = optionalGroup(v.filterPassband, '$.filterPassband', validateFilterPassband);
  const dsp = optionalGroup(v.dsp, '$.dsp', validateDsp);
  const rfFrontEnd = optionalGroup(v.rfFrontEnd, '$.rfFrontEnd', validateRfFrontEnd);
  const band = optionalGroup(v.band, '$.band', validateBand);
  const ritXit = optionalGroup(v.ritXit, '$.ritXit', validateRitXit);
  const antenna = optionalGroup(v.antenna, '$.antenna', validateAntenna);
  const scan = optionalGroup(v.scan, '$.scan', validateScan);
  const cwKeyer = optionalGroup(v.cwKeyer, '$.cwKeyer', validateCwKeyer);
  const scopeControls = optionalGroup(v.scopeControls, '$.scopeControls', validateScopeControls);

  const disabledReasons = v.disabledReasons.map((r, i) => validateDisabledReason(r, `$.disabledReasons[${i}]`));
  // SAFETY, MOR-1296 — the fail-closed half of "no second permit", enforced
  // structurally rather than left to the adapter. Break-in keys the
  // transmitter, so a model that presents a structurally-available break-in
  // fact while the model's ONE `txPermit` is anything other than 'allowed'
  // must ALSO record that the affordance is disabled. Same shape as the
  // `band` invariant above (MOR-1294): a producer cannot ship the permissive
  // half of a TX-adjacent pair while silently dropping the restrictive half.
  if (cwKeyer !== undefined && cwKeyer.breakIn.availability.structural
    && txPermit.status !== 'allowed'
    && !disabledReasons.some((r) => r.field === 'cwKeyer.breakIn')) {
    invalid('$.disabledReasons', "a 'cwKeyer.breakIn' entry whenever txPermit is not 'allowed' (fail-open otherwise)");
  }

  return {
    topologyId: str(v.topologyId, '$.topologyId'),
    vfoScheme: oneOf(v.vfoScheme, VFO_SCHEMES, '$.vfoScheme'),
    activeReceiver: validateActiveRx(v.activeReceiver, '$.activeReceiver'),
    vfos,
    split: validateBooleanFact(v.split, '$.split'),
    dualWatch: validateBooleanFact(v.dualWatch, '$.dualWatch'),
    txTarget,
    txPermit,
    scope: {
      hardwareScope: validateAvailability(scope.hardwareScope, '$.scope.hardwareScope'),
      audioFftScope: validateAvailability(scope.audioFftScope, '$.scope.audioFftScope'),
    },
    disabledReasons,
    ...(txAux !== undefined ? { txAux } : {}),
    ...(meters !== undefined ? { meters } : {}),
    ...(rxAudio !== undefined ? { rxAudio } : {}),
    ...(modeFilter !== undefined ? { modeFilter } : {}),
    ...(filterPassband !== undefined ? { filterPassband } : {}),
    ...(dsp !== undefined ? { dsp } : {}),
    ...(rfFrontEnd !== undefined ? { rfFrontEnd } : {}),
    ...(band !== undefined ? { band } : {}),
    ...(ritXit !== undefined ? { ritXit } : {}),
    ...(antenna !== undefined ? { antenna } : {}),
    ...(scan !== undefined ? { scan } : {}),
    ...(cwKeyer !== undefined ? { cwKeyer } : {}),
    ...(scopeControls !== undefined ? { scopeControls } : {}),
  };
}
