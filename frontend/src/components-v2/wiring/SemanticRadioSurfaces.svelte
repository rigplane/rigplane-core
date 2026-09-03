<!--
  Wiring for the semantic VFO + RX/TX reference vertical (MOR-1065).

  This is the ONLY place the two pure surfaces meet live state: it derives the
  MOR-1062 view model from the real runtime through
  `lib/runtime/adapters/radio-view-model-adapter`, hands the RX/TX surface a
  snapshot of the App-owned TX authority (v3 ADR invariant 11 — the controller
  in `lib/runtime/tx-controller/app-host`, provided once by App.svelte), and
  turns the surfaces' callback intents into commands. The surfaces themselves
  stay presentation-only; the authoritative global TX lamp stays in
  AppGlobalHost (MOR-1059) and is not duplicated here.
-->
<script module lang="ts">
  /**
   * Per-instance TX lease identity. The App TX controller keys lease ownership
   * by `sourceId`: an id recomputed per render — or shared with another mounted
   * source — makes `release()` a silent no-op and can strand a key DOWN. Same
   * identity discipline as TxPanel (MOR-1011) and the MOR-1221/1226 audits.
   */
  let surfaceSeq = 0;
</script>

<script lang="ts">
  import { onDestroy, untrack, type Snippet } from 'svelte';
  import { t } from '$lib/i18n';
  import { runtime } from '$lib/runtime';
  import { toRadioViewModel } from '$lib/runtime/adapters/radio-view-model-adapter';
  import {
    EMPTY_PBT_PRESENTATION, projectPbtPresentation, type PbtField,
    type PbtPresentationEvidence, type PbtPresentationState,
  } from '../../semantic/pbt-presentation-continuity';
  import { getAppTxController } from '$lib/runtime/tx-controller/app-host';
  import { txFaultObligation, type TxFaultObligation } from '$lib/runtime/tx-controller/model';
  import {
    bindSemanticSurfaceHandlers, getBreakInDelayControlFeedback, getPendingFrequencyHz,
    getPendingFilterSelection, getPendingNbOn, getPendingNrOn, getPendingPreampLevel,
  } from '$lib/runtime/adapters/panel-adapters';
  import { toRitXitProps } from '$lib/runtime/props/panel-props';
  import type { SemanticSurfaceName } from '../../presentation/layouts/contract';
  import {
    compositionSurfaces, useSurfacePlan, zoneShowsSurface,
  } from '../../presentation/workspace/resolution';
  import { LAN_MOD_INPUT_SOURCE } from '$lib/radio/mod-input';
  import AntennaSurface from '../../semantic/AntennaSurface.svelte';
  import BandSurface from '../../semantic/BandSurface.svelte';
  import DspSurface, {
    type DspLevelField, type DspToggleField,
  } from '../../semantic/DspSurface.svelte';
  import FilterSurface from '../../semantic/FilterSurface.svelte';
  import MetersSurface from '../../semantic/MetersSurface.svelte';
  import type { RadioViewModel } from '../../semantic/radio-view-model';
  import RfFrontEndSurface, {
    type RfFrontEndLevelField, type RfFrontEndToggleField,
  } from '../../semantic/RfFrontEndSurface.svelte';
  import RitXitScanSurface from '../../semantic/RitXitScanSurface.svelte';
  import RxAudioSurface from '../../semantic/RxAudioSurface.svelte';
  import RxTxSurface from '../../semantic/RxTxSurface.svelte';
  import ScopeDisplaySurface from '../../semantic/ScopeDisplaySurface.svelte';
  import TxAuxSurface, {
    type TxAuxLevelField, type TxAuxToggleField,
  } from '../../semantic/TxAuxSurface.svelte';
  import { keyBlockedReasons } from '../../semantic/rx-tx-surface';
  import VfoSurface, { type VfoSelection } from '../../semantic/VfoSurface.svelte';
  import ModInputTxWarning from '../panels/ModInputTxWarning.svelte';
  import CwKeyerSurface, { type CwLevelField } from '../../semantic/CwKeyerSurface.svelte';
  import ScopeControlsSurface, {
    type ScopeChoiceField, type ScopeToggleField,
  } from '../../semantic/ScopeControlsSurface.svelte';
  import {
    forReceiver, receiversOf, isActiveStrip, isOperationalStrip,
  } from './dual-receiver-strips';
  import { guardRadioViewModel } from './radio-view-model-guard';

  /**
   * `'single'` (default) is the pre-MOR-1067 composition, wrapped per zone
   * when `regions` says so. `'dual'` is the dual-receiver-cockpit's per-
   * receiver channel strips (MOR-1067): still ONE shared RxTxSurface and
   * ONE TX lease below — ONLY the VFO half splits, so there is still exactly
   * one authoritative key/unkey action surface regardless of `strips`.
   *
   * MOR-1068: in `'dual'` the composed blocks carry `data-zone-id` values
   * drawn from the `dual-receiver-cockpit` layout manifest's declared zones
   * (`presentation/layouts/dual-receiver-cockpit.ts`). The manifest is the
   * authority and is NOT imported here — importing it would close a cycle
   * (manifest -> loader -> skin -> wiring) and let a wiring change register a
   * layout. The two descriptions are held together by a test that reads the
   * ids out of the real registry and requires exactly these in the rendered
   * tree (MOR-1067 verification F6).
   */
  interface Props {
    strips?: 'single' | 'dual';
    regions?: boolean;
    regionContent?: Snippet;
  }
  /**
   * MOR-2231 — `regions` routes `vfo`/`rxTx` through the generic `zoned()`
   * path in the SINGLE composition, so each gains the zone element its
   * layout's plan names. `false` renders both exactly as before: no wrapper,
   * no `data-zone-id`.
   *
   * `RadioLayout.svelte` is the only site that passes it. Five take the
   * default, and `regions` is read on three of them — `LcdLayout`,
   * `MobileRadioLayout` and `frontend/fixtures/ReferenceLayout.svelte` —
   * because it is read only in the `{:else}` single branch.
   * `DualReceiverCockpit` and `PeerSplitLayout` mount `strips="dual"`, where
   * it is never evaluated. (Enumerated with `git grep -n
   * "<SemanticRadioSurfaces" -- .`, unscoped: a path-scoped search cannot see
   * `frontend/fixtures/`, which is how an earlier revision of this comment
   * came to name four.)
   *
   * A PROP rather than a plan lookup because the plan cannot answer the
   * question: `desktop-v2` and `sdr-test` declare the same two zone ids, so
   * `zoneOwning()` returns non-null on both faces.
   */
  let { strips = 'single', regions = false, regionContent }: Props = $props();

  /**
   * MOR-1082 — the workspace's per-zone `visibleSurfaces`/`zoneOrder`, resolved
   * by the composition root (App) against the ACTIVE layout manifest and read
   * here through a context getter. The manifest itself is deliberately still
   * NOT imported (see `strips` below): App owns the lookup, this file owns only
   * the consultation.
   *
   * It can ONLY subtract. `zoneShows` answers "unchanged" whenever there is no
   * plan (a standalone mount) or the active layout declares no such zone, the
   * plan can never contain a surface the manifest did not declare, and the
   * self-gates below (`view`, `view?.txAux`, `view?.meters`) are untouched — so
   * "the workspace says visible" can never mount a surface whose view-model
   * group is absent. `singleOrder` likewise falls back to the composed order,
   * so the vertical can never resolve to a screen with no RX/TX surface.
   */
  const surfacePlan = useSurfacePlan();
  const SINGLE_COMPOSITION: readonly SemanticSurfaceName[] = ['vfo', 'rxTx'];
  let singleOrder = $derived(compositionSurfaces(surfacePlan(), SINGLE_COMPOSITION));
  function zoneShows(zoneId: string, surface: SemanticSurfaceName): boolean {
    return zoneShowsSurface(surfacePlan(), zoneId, surface);
  }
  /**
   * MOR-2231 (step 1, batch 5) — whether the single composition's OPTIONAL
   * surfaces may still render outside a zone. See their render sites below.
   *
   * `regions` means the caller lays the zone boxes out as grid items, so a
   * surface reaching that path with no zone would be an unplaced item. The
   * reachable way for `zoneOwning()` to answer null there is a workspace
   * SUBTRACTION that emptied the zone (an imported or persisted document;
   * `resolveSurfacePlan` force-restores anything in `requiredSemanticSurfaces`).
   *
   * A mount with NO plan at all is excluded deliberately: no zone element
   * exists then for ANY surface, so there is no arrangement to be unplaced
   * beside — withholding the body would cost a readout and prevent nothing.
   */
  let allowBareSurfaces = $derived(!regions || surfacePlan() === null);
  /**
   * MOR-1336 (v3-rework S4) — the DECLARED zone that mounts `surface`, or
   * `null` when no zone does.
   *
   * This is the whole zone-ownership mechanism, and it is GENERIC: any
   * `SEMANTIC_SURFACE_NAMES` member a layout declares mounts through the one
   * `zoned` path below, so the next declarable surface needs no new code and
   * `txAux` is not a third hardcoded special case beside `vfo`/`rxTx`.
   *
   * It reads the SURFACE PLAN, never a manifest — this file stays manifest-blind
   * (the MOR-1068 cycle). That works because `resolveSurfacePlan` keys its map
   * by `manifest.zones`, so the plan's KEY SET is exactly the active layout's
   * declared-zone set. App already computes it; this only consults it.
   *
   * `null` → the caller renders BARE, byte-identical to the pre-S4 DOM. That is
   * MOR-1069 enforced by construction: a zone element exists only where a
   * layout actually declared one, never as an empty promise.
   *
   * LIMITATION, recorded rather than hidden: the plan is POST-subtraction, so
   * "no zone declares this" and "a zone declared it and the workspace hid it"
   * are indistinguishable here, and both render bare. That matches today
   * exactly (these surfaces consult no plan at all before this slice), so no
   * force-show is introduced and nothing regresses — but the workspace still
   * cannot hide a zoned optional surface. Fixing that needs `SurfacePlan` to
   * carry declaration and visibility separately (own ticket).
   */
  function zoneOwning(surface: SemanticSurfaceName): string | null {
    const plan = surfacePlan();
    if (plan === null) return null;
    for (const [zoneId, surfaces] of plan) if (surfaces.includes(surface)) return zoneId;
    return null;
  }
  /** The dual composition's per-receiver strips, after the workspace. Filtering
   *  the `{#each}` SOURCE rather than wrapping its body keeps ONE place that
   *  decides a strip's zone id — the id the DOM carries is the id consulted. */
  function visibleStrips(model: RadioViewModel) {
    return receiversOf(model)
      .map((receiverId, index) => ({
        receiverId, zoneId: index === 0 ? 'primary-vfo' : 'secondary-vfo',
      }))
      .filter(({ zoneId }) => zoneShows(zoneId, 'vfo'));
  }

  const semanticHandlers = bindSemanticSurfaceHandlers();
  const vfo = semanticHandlers.vfo;
  /** MOR-1307: the shipped band vocabulary, composed rather than forked. */
  const band = semanticHandlers.band;
  /**
   * MOR-1265. The two v2 handler factories already carry every txAux intent
   * (`makeVoxHandlers` owns gain/anti-VOX/delay, `makeTxHandlers` the rest);
   * composing them keeps ONE command vocabulary rather than a v3 fork of it.
   * The two maps below exist so the pure surface can stay field-addressed —
   * agreement with the shipped canonical handler names is pinned against the
   * real command module.
   */
  const txAuxIntents = { ...semanticHandlers.vox, ...semanticHandlers.tx };
  const TX_AUX_TOGGLE_INTENT: Record<TxAuxToggleField, () => void> = {
    atu: txAuxIntents.onAtuToggle, vox: txAuxIntents.onVoxToggle,
    compressor: txAuxIntents.onCompToggle, monitor: txAuxIntents.onMonToggle,
  };
  const TX_AUX_LEVEL_INTENT: Record<TxAuxLevelField, (value: number) => void> = {
    rfPower: txAuxIntents.onRfPowerChange, micGain: txAuxIntents.onMicGainChange,
    driveGain: txAuxIntents.onDriveGainChange, voxGain: txAuxIntents.onVoxGainChange,
    antiVoxGain: txAuxIntents.onAntiVoxGainChange, voxDelay: txAuxIntents.onVoxDelayChange,
    compressorLevel: txAuxIntents.onCompLevelChange, monitorLevel: txAuxIntents.onMonLevelChange,
  };
  /**
   * MOR-1279. The RX-audio intent vocabulary, composed from the SHIPPED
   * canonical handlers rather than forked: monitor mode + AF level from
   * `makeRxAudioHandlers` (whose `onAfLevelChange` takes 0..1 — the contract's
   * own unit, so nothing rescales on the way out either), routing from
   * `makeAudioRoutingHandlers`, and the MOD-input remedy from
   * `makeModeHandlers().onModInputChange` — the SAME command
   * `ModInputTxWarning`'s "Set LAN" fires, so a mismatch has one fix, not two.
   * `ModInputTxWarning` itself is untouched and stays in the rx-tx zone.
   */
  const rxAudioIntents = semanticHandlers.rxAudio;
  const routingIntents = semanticHandlers.audioRouting;
  /**
   * MOR-1309. The antenna intent vocabulary, composed from the SHIPPED command
   * bus rather than forked — `set_antenna_1`/`set_antenna_2`/the two RX-ANT
   * commands are exactly what `AntennaPanel` fires today. The port map exists
   * so the pure surface can stay PORT-addressed while the bus stays
   * command-addressed; a port the bus cannot name is unreachable by
   * construction rather than by a silent no-op.
   */
  const antennaIntents = semanticHandlers.antenna;
  const ANTENNA_PORT_INTENT: Record<number, () => void> = {
    1: antennaIntents.onSelectAnt1, 2: antennaIntents.onSelectAnt2,
  };
  const setModInputLan = () => semanticHandlers.mode.onModInputChange(LAN_MOD_INPUT_SOURCE);
  /**
   * MOR-1304. `makeModeHandlers` owns mode/dataMode intents, `makeFilterHandlers`
   * the rest — the SAME v2 command vocabulary `FilterPanel` already dispatches
   * through, composed once here rather than forked for the semantic surface.
   * Unlike `txAuxIntents` above, `FilterSurface`'s props already match these
   * names 1:1, so no per-field `Record` indirection is needed.
   */
  const filterIntents = { ...semanticHandlers.mode, ...semanticHandlers.filter };
  /**
   * MOR-1305. `makeDspHandlers`/`makeAgcHandlers` already carry every dsp
   * intent; the two maps below keep the pure surface field-addressed, same
   * precedent as `TX_AUX_*_INTENT` above — agreement with the shipped
   * canonical handler names is exercised against the real module in the component
   * test, not re-asserted here.
   */
  const dspIntents = semanticHandlers.dsp;
  const DSP_TOGGLE_INTENT: Record<DspToggleField, (next: boolean) => void> = {
    nrActive: (next) => dspIntents.onNrModeChange(next ? 1 : 0),
    nbActive: (next) => dspIntents.onNbToggle(next),
  };
  const DSP_LEVEL_INTENT: Record<DspLevelField, (value: number) => void> = {
    nrLevel: dspIntents.onNrLevelChange, nbLevel: dspIntents.onNbLevelChange,
    nbDepth: dspIntents.onNbDepthChange, nbWidth: dspIntents.onNbWidthChange,
    notchFreq: dspIntents.onNotchFreqChange, manualNotchWidth: dspIntents.onManualNotchWidthChange,
    agcTimeConstant: dspIntents.onAgcTimeChange,
  };
  const agcIntents = semanticHandlers.agc;
  /**
   * `agcLabels`/`nbLevelMax`/`nbLevelPercent` are pure caps-echo display
   * metadata, NOT `dsp` facts (MOR-1290/MOR-1305 carry-forward 1) — read
   * straight off `runtime.caps` here, at the one seam that already holds it
   * for the `toRadioViewModel` call below, verbatim `toDspProps`'s own
   * fallbacks (`lib/runtime/props/panel-props.ts`).
   *
   * MOR-1547: no fabricated IC-7610-shaped FAST/MID/SLOW dict when the
   * profile declares no agcLabels — `{}` lets `buildAgcOptions`
   * (agc-utils.ts) fall back to the honest raw mode number per-entry.
   */
  let agcLabels = $derived(runtime.caps?.agcLabels ?? {});
  let nbLevelRange = $derived(runtime.caps?.controls?.nb_level ?? null);
  let nbLevelMax = $derived(nbLevelRange?.raw_max ?? 10);
  let nbLevelPercent = $derived(nbLevelRange !== null);
  /**
   * MOR-1421 — a plain presentation gate for `VfoSurface`'s radio-wide
   * dual-receiver chrome (the active-receiver readout, the dual-watch
   * toggle): both ask a question that has only one possible answer on a
   * single-receiver radio, so the operator preference is to hide them
   * entirely rather than show a permanent "MAIN" readout or a permanently
   * disabled toggle. Read straight off `runtime.caps` at this seam — same
   * precedent as `agcLabels`/`nbLevelMax` above — never inside `VfoSurface`
   * itself, which stays capability-blind by ADR (v3, MOR-1063) and receives
   * only this plain boolean.
   */
  let hasDualReceiver = $derived((runtime.caps?.receivers ?? 1) > 1);
  /**
   * MOR-1441 (review B5) — memoized across `pendingFrequencyHzCache`: this
   * `$derived` re-runs on ANY command-lifecycle mutation (`getCommandLifecycles()`
   * is one shared reactive array covering every intent, not just `set_freq`),
   * so an unrelated command's ack/timeout would otherwise mint a brand-new
   * `{MAIN?,SUB?}` object every time — per-keystroke prop-identity churn for
   * every consumer down the `VfoSurface`/`FrequencyDisplayInteractive` chain,
   * even when neither receiver's pending value actually changed. Returning
   * the SAME object when both values are unchanged lets Svelte's own
   * reference-equality check skip that downstream work.
   */
  type PendingFrequencyHzCache = {
    main: number | null; sub: number | null; value: Partial<Record<'MAIN' | 'SUB', number>>;
  };
  let pendingFrequencyHzCache: PendingFrequencyHzCache | null = null;
  function computePendingFrequencyHz(): Partial<Record<'MAIN' | 'SUB', number>> {
    const main = getPendingFrequencyHz(0);
    const sub = getPendingFrequencyHz(1);
    const cache: PendingFrequencyHzCache | null = pendingFrequencyHzCache;
    if (cache !== null && cache.main === main && cache.sub === sub) return cache.value;
    const result: Partial<Record<'MAIN' | 'SUB', number>> = {};
    if (main !== null) result.MAIN = main;
    if (sub !== null) result.SUB = sub;
    pendingFrequencyHzCache = { main, sub, value: result };
    return result;
  }
  /**
   * MOR-1441 — the pending (not-yet-confirmed) frequency target per
   * receiver, for `VfoSurface`'s digit control to render distinctly from
   * confirmed radio truth while a hot tuning burst is in flight. Read at
   * this seam (same "caps-echo display metadata" precedent as
   * `hasDualReceiver` above) so `VfoSurface` stays command-bus-blind,
   * receiving only the plain per-receiver value.
   */
  let pendingFrequencyHz = $derived(computePendingFrequencyHz());
  /**
   * MOR-1306. The RF-front-end intent vocabulary, composed from the SHIPPED
   * command bus rather than forked — same discipline as `rxAudioIntents`
   * above. `RF_FRONT_END_LEVEL_INTENT` maps the surface's field-addressed
   * `onLevelChange` onto the two real level handlers, mirroring
   * `TX_AUX_LEVEL_INTENT`.
   *
   * MOR-1447: `RfFrontEndSurface`'s `onLevelChange` reports the radio's own
   * normalized 0..1 reading verbatim (its own "RAW wire units, no rescale"
   * contract — `RfFrontEndSurface.svelte`'s file header) but the REAL
   * `onRfGainChange`/`onSquelchChange` dispatch a raw 0-255 wire integer and
   * refuse anything else (`Number.isSafeInteger` guard, `panel-commands.ts`).
   * Converting at THIS seam, not inside `semanticHandlers` itself, keeps
   * `bindSemanticSurfaceHandlers()`'s pinned "exact factory object, unreshaped"
   * contract (`semantic-surface-handler-binder.isolated.test.ts`) intact.
   * Unconverted, this was the MOR-1447 regression: an intermediate slider
   * drag (e.g. 0.34) silently failed the integer guard, so dragging only ever
   * landed on 0%/100% (the two values that already happen to be safe
   * integers).
   */
  const rfFrontEndIntents = semanticHandlers.rfFrontEnd;
  /**
   * MOR-1447 leg 2. The profile-declared RF/SQL control model — data-driven
   * from `[capabilities].rf_sql_control_model` in the rig TOML, read at this
   * seam straight off `runtime.caps`, same "caps-echo display metadata"
   * precedent as `hasDualReceiver`/`agcLabels` above. `RfFrontEndSurface`
   * itself renders whichever model this resolves to and stays otherwise
   * capability-blind; there is no vendor/model-name branch anywhere in this
   * file or in the surface.
   */
  let rfSqlControlModel = $derived(runtime.caps?.rfSqlControlModel ?? 'separate');
  const RF_FRONT_END_LEVEL_INTENT: Record<RfFrontEndLevelField, (value: number) => void> = {
    rfGain: (value) => rfFrontEndIntents.onRfGainChange(Math.round(value * 255)),
    squelch: (value) => rfFrontEndIntents.onSquelchChange(Math.round(value * 255)),
  };
  const RF_FRONT_END_TOGGLE_INTENT: Record<RfFrontEndToggleField, (next: boolean) => void> = {
    digiSel: rfFrontEndIntents.onDigiSelToggle, ipPlus: rfFrontEndIntents.onIpPlusToggle,
  };
  /**
   * MOR-1308 (vocabulary slice 8B). The shipped RIT/XIT and scan command
   * vocabularies, composed unmodified — the O1 "one register, two gates"
   * behavior lives entirely in `makeRitXitHandlers()` already (both offset
   * handlers write `ritFreq` via the same `set_rit_frequency` command); this
   * wiring adds no re-derivation, only 1:1 plumbing.
   */
  const ritXitIntents = semanticHandlers.ritXit;
  const scanIntents = semanticHandlers.scan;
  /** MOR-1731: consume the shared validated tri-state boundary. `undefined`
   * keeps legacy servers compatible; `null` is the adapter's fail-closed
   * result for present-but-unusable metadata. */
  let ritDomain = $derived(toRitXitProps(runtime.state, runtime.caps).ritDomain);
  /**
   * MOR-1310 (slice 9B). The CW intent vocabulary, composed from the SHIPPED
   * `makeCwPanelHandlers` rather than forked. MOR-1606 wires its existing
   * RX-assisted frequency-correction intent only when the same CW + audio +
   * audio-FFT facts that guard the handler are present. Nothing here keys —
   * exactly one `<RxTxSurface>` stays the key/unkey authority (decomposition R9).
   */
  const cwIntents = semanticHandlers.cw;
  let breakInDelayFeedback = $derived(getBreakInDelayControlFeedback());
  let autoTuneAvailable = $derived(
    runtime.caps?.capabilities.includes('cw') === true
      && runtime.caps?.capabilities.includes('audio') === true
      && runtime.caps?.audioFftAvailable === true,
  );
  const CW_LEVEL_INTENT: Record<CwLevelField, (value: number) => void> = {
    keyerSpeed: cwIntents.onKeySpeedChange, pitchHz: cwIntents.onCwPitchChange,
    breakInDelay: cwIntents.onBreakInDelayChange,
  };
  /**
   * MOR-1311 (slice 11B, LAST of the vocabulary program). The shipped
   * scope-toolbar/popover command vocabulary, composed unmodified — the two
   * maps keep the pure surface field-addressed, same precedent as
   * `TX_AUX_*_INTENT`/`DSP_*_INTENT` above.
   */
  const scopeIntents = semanticHandlers.scopeControls;
  const SCOPE_TOGGLE_INTENT: Record<ScopeToggleField, (next: boolean) => void> = {
    hold: scopeIntents.onHoldChange, dual: scopeIntents.onDualChange,
    duringTx: scopeIntents.onDuringTxChange, vbwNarrow: scopeIntents.onVbwChange,
  };
  const SCOPE_CHOICE_INTENT: Record<ScopeChoiceField, (value: number) => void> = {
    mode: scopeIntents.onModeChange, edge: scopeIntents.onEdgeChange,
    centerType: scopeIntents.onCenterTypeChange, rbw: scopeIntents.onRbwChange,
    receiver: scopeIntents.onReceiverChange,
  };
  const tx = getAppTxController();
  const sourceId = `semantic-rx-tx-${++surfaceSeq}`;
  let leaseSeq = 0;

  let txState = $state.raw(tx.snapshot());
  const stopWatchingTx = tx.subscribe((next) => { txState = next; });

  onDestroy(() => {
    // Unsubscribe FIRST: the release below is fail-closed and must run to
    // completion inside the controller, not bounce back into a component that
    // is already being destroyed (TxPanel's documented teardown order).
    stopWatchingTx();
    // `requestKey` starts a LATCHED lease — it outlives this component. The
    // App TX controller keeps the lease across a presentation swap (MOR-1060
    // destroys this subtree on any skinId change), the model refuses a release
    // from any other sourceId, and `start` is a no-op off-idle: without this,
    // swapping away while keyed STRANDS the transmitter with no UI exit.
    // Same fail-safe direction as MobileRadioLayout's recognizer teardown
    // ("rotating away while keyed or latched drops TX rather than stranding
    // it") and TxPanel's `ptt.destroy()`. Blind release is safe — the model's
    // owner check makes it inert when another source holds the lease.
    const guard = tx.snapshot().guard;
    if (guard) tx.release(sourceId, guard);
  });

  /**
   * MOR-1279. The App-owned RX-audio snapshot the `rxAudio` facts are read
   * against (MOR-1274's FOURTH adapter argument). Every member is state this
   * layer ALREADY holds — nothing here opens, starts or probes the audio path
   * (MOR-972 P0); audio lifetime stays App-owned (MOR-1058).
   *
   * `routing: null` is deliberate and load-bearing. The browser routing prefs
   * live in `localStorage` and are applied by `AudioRoutingControl`'s
   * `onMount` via `audioManager.setAudioConfig` — a transport-touching call
   * this layer must not make — while `audioManager.getAudioConfig()` would
   * report the RxPlayer's CONSTRUCTION-TIME defaults ('both' / false) as
   * though they had been observed: the exact fabrication slice 3A degraded
   * away from. So the routing facts read `unknown` until whoever owns the
   * restore hands it in, and the surface says so honestly rather than
   * guessing. Ownership of that restore is an App concern, out of scope here.
   */
  let rxAudioSnapshot = $derived({
    muted: runtime.audio.muted,
    rxEnabled: runtime.audio.rxEnabled,
    volume: runtime.audio.volume,
    connected: runtime.connectionAudio,
    routing: null,
  });

  /**
   * MOR-1312 (slice 12B) — the App-owned scope-display snapshot the
   * `scopeDisplay` facts are read against (MOR-1301's FIFTH adapter
   * argument). Every member is state this layer already holds — nothing
   * here opens, starts or probes the scope resource (MOR-972 P0); scope
   * lifetime stays App-owned (MOR-1058), same discipline `rxAudioSnapshot`
   * above uses for audio.
   *
   * `...runtime.defaultScopeStatus` spreads `source`/`available`/
   * `resourceSelected`/`demand`/`lifecycle`/`transport`/`frameSeen` verbatim
   * — the field-name mirroring `ScopeDisplaySnapshot`'s own doc comment
   * documents. `isPoweredOff` is the status bar's own override input
   * (`StatusBar.svelte`'s `isPoweredOff`), read from the SAME
   * `runtime.radioPowerOn` getter rather than re-derived. `hardwareConnected`
   * is the MOR-1312 addition (MOR-1352 finding) — `runtime.scope`'s own
   * hardware-transport flag, independent of `source`.
   */
  let scopeDisplaySnapshot = $derived({
    ...runtime.defaultScopeStatus,
    isPoweredOff: runtime.radioPowerOn === false,
    hardwareConnected: runtime.scope.hardwareScopeConnected,
  });

  // Belt-and-braces contract pin: two compile-time links (the adapter's own
  // return-type annotation, MOR-1065 ruling 2, and this variable's own type)
  // plus one dev-only runtime link, MOR-2040's `guardRadioViewModel` — it
  // runs the real `validateRadioViewModel` whenever `import.meta.env.DEV` is
  // true, so a value that type-checks but breaks a structural or cross-field
  // invariant still throws here instead of reaching the surfaces below
  // silently. Dead code in a production build; see `radio-view-model-guard.ts`.
  // MOR-1262 slice 2A: the live authority snapshot is the THIRD argument — the
  // meter facts read their TX relevance from it and never from `state.ptt`
  // (invariant R9). Without it the adapter emits no `meters` group at all.
  // MOR-1279 slice 3B: the RX-audio snapshot is the FOURTH.
  // MOR-1312 slice 12B: the scope-display snapshot is the FIFTH.
  let canonicalView: RadioViewModel | null = $derived(
    guardRadioViewModel(
      toRadioViewModel(runtime.state, runtime.caps, txState, rxAudioSnapshot, scopeDisplaySnapshot),
    ),
  );
  let pbtPresentation: PbtPresentationState = $state(EMPTY_PBT_PRESENTATION);
  // A disconnect makes every currently-held PBT observation prior-session
  // evidence. Floors are per provider/receiver/field: monotonic markers are
  // only comparable inside one provider generation and receiver stream.
  let pbtObservationFloors = $state(new Map<string, number>());
  let view = $state<RadioViewModel | null>(null);
  const readControlSession = 'controlSession' in runtime ? () => runtime.controlSession : undefined;
  const subscribeControlSession = 'subscribeControlSession' in runtime ? runtime.subscribeControlSession : undefined;
  let controlSession = $state(readControlSession?.() ?? { state: 'disconnected', epoch: -1 });
  const pbtFloorKey = (generation: number, receiver: 'MAIN' | 'SUB', field: PbtField) =>
    `${generation}:${receiver}:${field}`;
  const unsubscribePbtSession = subscribeControlSession?.((next) => {
    if (next.state !== 'connected' || next.epoch !== controlSession.epoch) {
      pbtPresentation = EMPTY_PBT_PRESENTATION;
      const generation = runtime.state?.providerGeneration;
      if (Number.isSafeInteger(generation)) for (const receiver of ['MAIN', 'SUB'] as const) {
        for (const field of ['pbtInner', 'pbtOuter'] as const) {
          const marker = runtime.state?.fieldStatus?.[`${receiver.toLowerCase()}.${field}`]?.lastObservedMonotonic;
          if (typeof marker === 'number' && Number.isSafeInteger(marker)) {
            const key = pbtFloorKey(generation as number, receiver, field);
            pbtObservationFloors.set(key, Math.max(pbtObservationFloors.get(key) ?? -1, marker as number));
          }
        }
      }
    }
    controlSession = next;
  });
  onDestroy(() => unsubscribePbtSession?.());
  const pbtEvidence = (): PbtPresentationEvidence => {
    const state = runtime.state, session = controlSession, generation = state?.providerGeneration;
    const receiver = canonicalView?.activeReceiver;
    const boundary = session.state === 'connected'
      && receiver?.status === 'known'
      && Number.isSafeInteger(generation)
      && generation === runtime.caps?.providerGeneration
      ? { providerGeneration: generation as number, receiver: receiver.receiver,
        controlSession: session.state, epoch: session.epoch } : null;
    const field = (name: PbtField) => {
      const path = `${receiver?.status === 'known' && receiver.receiver === 'SUB' ? 'sub' : 'main'}.${name}`;
      const status = state?.fieldStatus?.[path];
      const observedAt = status?.lastObservedMonotonic;
      return status?.observed && status.freshness === 'fresh' && status.availability === 'available'
        && typeof observedAt === 'number' && Number.isSafeInteger(observedAt)
        && observedAt > (typeof generation === 'number' && receiver?.status === 'known'
          ? pbtObservationFloors.get(pbtFloorKey(generation, receiver.receiver, name)) ?? -1 : -1)
        ? { status: 'fresh' as const, marker: { source: 'field' as const, value: observedAt as number } }
        : { status: status?.freshness === 'stale' ? 'stale' as const : 'unavailable' as const };
    };
    return { boundary, fields: { pbtInner: field('pbtInner'), pbtOuter: field('pbtOuter') } };
  };
  $effect(() => {
    const evidence = pbtEvidence();
    const retainedMatchesCanonical = (field: PbtField) => {
      const retained = untrack(() => pbtPresentation)[field];
      const incoming = evidence.fields[field];
      const current = canonicalView?.filterPassband?.[field];
      return retained !== null && incoming.status === 'fresh'
        && incoming.marker.source === retained.marker.source && incoming.marker.value === retained.marker.value
        && current?.reading.status === 'known' && current.reading.value === retained.value
        && current.availability.operational;
    };
    const previous = retainedMatchesCanonical('pbtInner') || retainedMatchesCanonical('pbtOuter') ? {
      ...untrack(() => pbtPresentation),
      ...(retainedMatchesCanonical('pbtInner') ? { pbtInner: null } : {}),
      ...(retainedMatchesCanonical('pbtOuter') ? { pbtOuter: null } : {}),
    } : untrack(() => pbtPresentation);
    const pbtCanonical = canonicalView?.filterPassband && (
      evidence.fields.pbtInner.status !== 'fresh' || evidence.fields.pbtOuter.status !== 'fresh'
    ) ? {
      ...canonicalView,
      filterPassband: {
        ...canonicalView.filterPassband,
        ...(evidence.fields.pbtInner.status !== 'fresh' ? {
          pbtInner: { reading: { status: 'unknown' as const }, availability: { ...canonicalView.filterPassband.pbtInner.availability, operational: false } },
        } : {}),
        ...(evidence.fields.pbtOuter.status !== 'fresh' ? {
          pbtOuter: { reading: { status: 'unknown' as const }, availability: { ...canonicalView.filterPassband.pbtOuter.availability, operational: false } },
        } : {}),
      },
    } : canonicalView;
    const projected = pbtCanonical === null
      ? { state: EMPTY_PBT_PRESENTATION, view: null }
      : projectPbtPresentation(previous, pbtCanonical, evidence);
    pbtPresentation = projected.state;
    view = projected.view;
  });

  /**
   * MOR-1441 leg 2 — active receiver's wire index (0=MAIN, 1=SUB) for the
   * discrete-control pending accessors below, or `null` while unobserved.
   * Same conversion `tuneFrequency` below already uses.
   */
  let activeReceiverIndex: 0 | 1 | null = $derived(
    view?.activeReceiver.status === 'known'
      ? (view.activeReceiver.receiver === 'MAIN' ? 0 : 1)
      : null,
  );
  /**
   * MOR-1441 leg 2 — pending targets for `FilterSurface`/`DspSurface`/
   * `RfFrontEndSurface`, read off the command-bus lifecycle list like
   * `pendingFrequencyHz` below. Unlike that memoized `{MAIN?,SUB?}` object
   * (review B5), these are plain scalars — Svelte's own reactivity already
   * skips downstream work on an unchanged primitive, so no cache is needed
   * for the same effect. `null` while the active receiver is unobserved.
   */
  let pendingFilter = $derived(
    activeReceiverIndex === null ? null : getPendingFilterSelection(activeReceiverIndex),
  );
  let pendingPreamp = $derived(
    activeReceiverIndex === null ? null : getPendingPreampLevel(activeReceiverIndex),
  );
  let pendingNb = $derived(activeReceiverIndex === null ? null : getPendingNbOn(activeReceiverIndex));
  let pendingNr = $derived(activeReceiverIndex === null ? null : getPendingNrOn(activeReceiverIndex));

  // Bound once per instance, never per render — see `surfaceSeq` above.
  function requestKey(): void {
    tx.start(sourceId, `${sourceId}-${++leaseSeq}`, 'latched');
  }
  /**
   * NEVER gated. Reads the LIVE authority guard and releases: no phase,
   * permit, fault or view-model condition may stand between the operator and
   * stopping transmission.
   *
   * MOR-1906 — and never SWALLOWED either. `if (guard) release()` alone left
   * the button live-looking and completely inert after a refused key press:
   * the refusal branch of the reducer's `start` clears the lease, so there was
   * no guard to release, and the press produced no command, no reset and no
   * feedback while the key sat disabled behind the latched fault. To be exact
   * about what was and was not missing: the MOR-1784 dismiss affordance was
   * present and working the whole time (it renders unconditionally on a failed
   * phase, and the wiring tests prove it). What was dead was THIS control —
   * the one an operator reaches for when the transmitter will not let go — and
   * an operator who does not connect the two reloads the page.
   *
   * With no lease held there is nothing to de-key — the transmitter is not
   * this surface's to stop — so the intent falls through to the only thing
   * left between the operator and the transmitter: the latched fault. That
   * discharges nothing and commands nothing. `reset-fault` issues no effects
   * and the reducer re-checks `txFaultObligation` at the moment of the event,
   * so it can neither key a radio nor stand in for an outstanding de-key; it
   * simply hands the key control back. The release path is untouched and stays
   * first: while a lease is live, stopping transmission is the whole job.
   */
  function requestUnkey(): void {
    const live = tx.snapshot();
    if (live.guard) { tx.release(sourceId, live.guard); return; }
    if (live.phase === 'failed') tx.resetFault();
  }
  /** App-owned fault recovery (MOR-1065 wiring decision, recorded on the
   *  ticket). The pure RX/TX surface deliberately has no `resetFault` intent,
   *  and a `failed` phase blocks the key action — without this affordance in
   *  the layout chrome the operator has no UI exit from a fault. */
  function clearFault(): void {
    tx.resetFault();
  }
  /**
   * MOR-1784 — is the latched fault dismissable right now, and if not, which
   * obligation is in the way?
   *
   * `txFaultObligation` is the reducer's OWN `reset-fault` guard (exported from
   * `model.ts` for exactly this), not a copy of it: this wiring can never offer
   * a reset the controller would refuse, nor withhold one it would accept. The
   * bench finding (owner session, IC-7300): a fault latched, the key read
   * "an unresolved TX fault is blocking the key", and the only recovery found
   * was reloading the page — the reset was permitted the whole time and simply
   * unreachable. Dismissing clears the surface's latch and nothing else: it
   * issues no command, so it can neither key a radio nor discharge a pending
   * de-key obligation, and while one is outstanding the reducer refuses it and
   * this reads out WHY instead of offering a button that silently no-ops.
   */
  let faultObligation = $derived(txState.phase === 'failed' ? txFaultObligation(txState) : null);
  let faultDismissable = $derived(txState.phase === 'failed' && faultObligation === null);
  const FAULT_OBLIGATION_KEY: Record<TxFaultObligation, string> = {
    'dekey-pending': 'core.rxTx.fault.reset.reason.dekeyPending',
    'key-held': 'core.rxTx.fault.reset.reason.keyHeld',
    'mod-restore': 'core.rxTx.fault.reset.reason.modRestore',
    cleanup: 'core.rxTx.fault.reset.reason.cleanup',
  };

  /**
   * ATU TUNE emits a CARRIER — a transmit-causing action (MOR-1262 §2 slice 1
   * safety note i). Routing decision, recorded on MOR-1265: the *gate* is the
   * App-owned TX authority — the shared `keyBlockedReasons` predicate on the
   * LIVE snapshot, exactly what blocks the key intent — while the *carrier*
   * stays radio-owned (the ATU's own tune cycle, started by the backend
   * command). No TX lease is taken, so this never becomes a second key path
   * (safety note iii); it can only be more restrictive than keying, never
   * less. The snapshot is read HERE rather than from `txState` because the
   * transmitter can start between the last render and the click — the same
   * live-read discipline `requestUnkey` uses for the guard.
   */
  function requestAtuTune(): void {
    if (!view || keyBlockedReasons(view, tx.snapshot()).length > 0) return;
    txAuxIntents.onAtuTune();
  }

  /**
   * MOR-1322 (S3b) — per-digit tuning, routed to the SAME per-receiver
   * canonical VFO handlers the legacy VfoHeader used (`onMainFreqChange` /
   * `onSubFreqChange`), which own the optimistic patch and the `set_freq`
   * command. No new key path and no TX semantics (R9): this moves a frequency,
   * it never keys the transmitter.
   *
   * MOR-1425 review round 2 (B1 residual): this function funnels TWO
   * different gesture shapes into the same per-receiver VFO handlers —
   * `VfoSurface`'s per-digit wheel/arrow tuning below (a genuine RELATIVE
   * step, `kind` defaults to `'step'` so every existing digit-path call site
   * needs no change) and `enterFrequency`/`selectBand`'s bandless fallback
   * (both an ABSOLUTE typed/default target). `onMainFreqChange`/
   * `onSubFreqChange` themselves stay unconditional-`step()` (unchanged, so
   * every other caller — `RadioLayout`'s legacy `VfoHeader` wiring,
   * `MobileRadioLayout`'s `tuneBy` — is unaffected); an absolute source
   * routes around them entirely, through `vfo.onFreqChange`'s `'jump'` path
   * (review B1's original fix, already proven for spectrum click-to-tune /
   * EiBi / QSY recall), which clears any in-flight burst and emits the exact
   * target immediately, unpaced.
   */
  function tuneFrequency(
    receiver: 'MAIN' | 'SUB', frequencyHz: number, kind: 'jump' | 'step' = 'step',
  ): void {
    if (kind === 'jump') { vfo.onFreqChange(frequencyHz, receiver === 'MAIN' ? 0 : 1, 'jump'); return; }
    if (receiver === 'SUB') vfo.onSubFreqChange(frequencyHz);
    else vfo.onMainFreqChange(frequencyHz);
  }

  /**
   * MOR-1307 (vocabulary slice 7B) — band select and frequency entry, both
   * routed through the SHIPPED command vocabulary and both gated on a KNOWN
   * active receiver.
   *
   * The gate is the MOR-1322 B1 wrong-VFO dispatch class: `set_freq` /
   * `set_band` write the ACTIVE receiver's VFO, and the `band` facts are
   * themselves derived from the active receiver — so with `activeReceiver`
   * unobserved there is no honest target and neither intent may leave. The
   * surface disables the controls on the same condition; this is the second,
   * independent mechanism.
   *
   * A band WITHOUT a band-stacking register falls back to a plain frequency
   * set, and that fallback goes through `tuneFrequency` — the SAME per-receiver
   * path the VFO tuning uses — rather than `makeBandHandlers`' own fallback,
   * which hardcodes `receiver: 0` and would tune MAIN while SUB is active.
   * The BSR path stays the shipped handler untouched.
   */
  function selectBand(name: string, defaultHz: number, bsrCode: number | null): void {
    const active = view?.activeReceiver;
    if (active?.status !== 'known') return;
    // MOR-1425 review round 2 (B1 residual): this bandless fallback is an
    // ABSOLUTE band default, not a step from the current frequency — 'jump'
    // so a hot digit-tuning burst on this receiver never absorbs it.
    if (bsrCode !== null) band.onBandSelect(name, defaultHz, bsrCode);
    else tuneFrequency(active.receiver, defaultHz, 'jump');
  }
  function enterFrequency(frequencyHz: number): void {
    const active = view?.activeReceiver;
    if (active?.status !== 'known') return;
    // MOR-1425 review round 2 (B1 residual): a typed frequency is the most
    // explicitly ABSOLUTE gesture in the UI — 'jump', same reasoning as
    // `selectBand` above.
    tuneFrequency(active.receiver, frequencyHz, 'jump');
  }

  function selectVfo(target: VfoSelection): void {
    vfo.onVfoSelect(target.receiver, target.slot.kind === 'slotted' ? target.slot.id : null);
  }
  function toggleDualWatch(): void {
    if (view?.dualWatch.status === 'known') vfo.onDualWatchToggle(!view.dualWatch.value);
  }
</script>

<div class="semantic-surfaces" data-testid="semantic-radio-surfaces">
  {#if view}
    {#if strips === 'dual'}
      <div class="channel-strips" data-testid="channel-strips">
        {#each visibleStrips(view) as { receiverId, zoneId } (receiverId)}
          <!--
            `data-zone-id`: `ReceiverId` is `'MAIN' | 'SUB'`, so the index is
            total over the manifest's two per-receiver zones. A degraded
            single-receiver view model renders `primary-vfo` and NO
            `secondary-vfo` — an absent zone, never an empty promise.
          -->
          <div
            class="channel-strip"
            data-testid={`channel-strip-${receiverId}`}
            data-zone-id={zoneId}
            data-strip-receiver={receiverId}
            data-strip-active={isActiveStrip(view, receiverId)}
            data-strip-operational={isOperationalStrip(view, receiverId)}
          >
            <!--
              `selectionPoolSize`: the slice below holds this receiver's VFOs
              only, but the operator can still choose across the WHOLE radio —
              without the pool the MOR-977 gate reads a 1-VFO slice as
              "structurally nothing to choose" and an `ab_shared` cockpit
              silently loses its only receiver-selection control.
              `showRadioWideFacts={false}`: split / dual-watch / active-receiver
              are radio-wide and now live in the global zone below (MOR-1068);
              a strip owns nothing but its own receiver.
              `groupLabel`: without it all three mounted surfaces share one
              generic accessible name and assistive tech cannot tell the
              strips apart.
              `disabled` (MOR-1256): a structurally-dual, operationally-
              degraded receiver (`dual-rx-unavailable`) keeps its strip
              PRESENT but forces its select controls inert — the shared
              RxTxSurface below is untouched, so single TX authority stays
              radio-wide regardless of which strip this gates.
            -->
            <VfoSurface
              viewModel={forReceiver(view, receiverId)}
              selectionPoolSize={view.vfos.length}
              showRadioWideFacts={false}
              groupLabel={t('core.vfo.receiverGroupLabel', { receiver: receiverId })}
              onSelectVfo={selectVfo}
              onTuneFrequency={tuneFrequency}
              disabled={!isOperationalStrip(view, receiverId)}
              {pendingFrequencyHz}
            />
          </div>
        {/each}
      </div>
      <!--
        The radio-wide row, rendered ONCE and outside every strip. It used to
        ride in the first strip's column, which put "Active receiver: SUB"
        inside the column without the active border (MOR-1067 verification
        #4). Binding it to the ACTIVE strip instead is proven worse — a
        verifier mutant made split/dual-watch vanish whenever `activeReceiver`
        was unobserved. It is not `aria-disabled`: it holds live switches that
        already gate themselves on their own observed facts (F7).
      -->
      {#if zoneShows('global', 'vfo')}
        <div class="cockpit-global-row" data-testid="cockpit-zone-global" data-zone-id="global">
          <!--
            MOR-1321: the VFO ops ride with the radio-wide facts, so in the dual
            composition they land HERE — once, in the global row — and not in the
            per-receiver strips above, which set `showRadioWideFacts={false}`.
            Same placement rule split/dual-watch already follow, for the same
            reason: one radio-wide action must not appear once per receiver.
          -->
          <VfoSurface
            viewModel={view}
            showVfoList={false}
            groupLabel={t('core.vfo.radioWideGroupLabel')}
            {hasDualReceiver}
            onToggleSplit={vfo.onSplitToggle}
            onToggleDualWatch={toggleDualWatch}
            onEqualizeVfos={vfo.onEqual}
            onSwapVfos={vfo.onSwap}
            onQuickSplit={vfo.onQuickSplit}
            onQuickDualWatch={vfo.onQuickDw}
          />
        </div>
      {/if}
    {/if}
  {/if}

  <!--
    MOR-1082: the single composition's VFO half, moved into a snippet for the
    same reason `rxTxSurface` is one — so the two surfaces the layout's single
    zone mounts can be rendered in the ORDER that zone resolves to, from one
    place, with exactly one `<VfoSurface>` tag per composition. Its render
    site below is where the default path's VFO already was, so an unresolved
    or default plan reproduces today's element sequence exactly.
  -->
  {#snippet vfoSurface()}
    {#if view}
      <VfoSurface
        viewModel={view}
        onSelectVfo={selectVfo}
        onTuneFrequency={tuneFrequency}
        {hasDualReceiver}
        onToggleSplit={vfo.onSplitToggle}
        onToggleDualWatch={toggleDualWatch}
        onEqualizeVfos={vfo.onEqual}
        onSwapVfos={vfo.onSwap}
        onQuickSplit={vfo.onQuickSplit}
        onQuickDualWatch={vfo.onQuickDw}
        {pendingFrequencyHz}
      />
    {/if}
  {/snippet}
  <!--
    MOR-1069 (finding N1, routed from the MOR-1068 verification). The
    wrapper used to render on EVERY path as an inert `display: contents`
    naming shell, which left the single/default path no longer
    element-identical to its pre-cockpit shape. It is now rendered ONLY in
    the dual composition, where it is a real, bound zone element the
    cockpit's responsive rules can place — an inert wrapper cannot be a
    grid/flex item, so "leave it inert" was not an option once the zone had
    to move between arrangements. `__tests__/semantic-rx-tx-wiring.component.test.ts`
    re-pins that bare shape under NO_PLAN: it supplies no surface plan, so
    `zoneOwning()` is null and the surface renders bare there whatever
    `regions` says. The plan-resolved `regions === false` case is a separate
    claim, pinned in `semantic-desktop-migration.component.test.ts` by
    `leaves the desktop-v2 surfaces bare, with no zone element around either`.

    The snippet is deliberate: it keeps exactly ONE `<RxTxSurface>` tag in
    this file, so single TX authority stays a property of the SOURCE rather
    than of which branch happens to be taken. Pinned as such by
    `presentation/layouts/__tests__/cockpit-responsive-composition.test.ts`.
    MOR-1258 moves the invocation below out from under the outer
    `{#if view}` (the rx-tx zone and the TX-adjacent alerts it now carries
    must exist even while `view` is still null — see the alerts comment
    below), so the view-model gate now lives on the snippet itself.
  -->
  {#snippet rxTxSurface()}
    {#if view}
      <RxTxSurface {view} tx={txState} onRequestKey={requestKey} onRequestUnkey={requestUnkey} />
    {/if}
  {/snippet}

  <!--
    MOR-1784. The fault-recovery block, split out of `txAdjacentAlerts` below
    so it can render where the fault is READ — directly behind the surface
    that prints "TX fault: <code>" and the blocked list — in both
    compositions. It stayed an rx-tx zone member either way (MOR-1258 owner
    ruling, gate item (b)); what changed is that in the single/default path
    (sdr-test / LCD / mobile) it no longer sits at the far end of a scrolling
    column, which is how the owner's IC-7300 session concluded there was no
    way out of a fault at all.

    Deliberately NOT under `{#if view}` or `zoneShows` (see the render sites):
    a fault latched by another lease source — the mobile PTT surface, TxPanel,
    a keyboard key — must stay dismissable even on a screen whose view model
    or workspace plan has no RX/TX surface. Recovery availability only ever
    widens here; it never narrows.

    The refusal branch is not decoration: while the reducer would refuse the
    reset (`txFaultObligation` above), a button would silently no-op, which is
    the same dead end one click further in. It states the obligation instead,
    and what discharges it. `role="status"` because the swap between the two
    branches is a live change an operator may be reading rather than watching.
  -->
  {#snippet txFaultRecovery()}
    {#if txState.phase === 'failed'}
      <div class="tx-fault-recovery" data-testid="tx-fault-recovery" data-dismissable={faultDismissable}>
        {#if faultDismissable}
          <button
            type="button" class="tx-fault-reset" data-testid="tx-fault-reset" onclick={clearFault}
          >{t('core.rxTx.fault.reset.action')}</button>
          <span class="tx-fault-note" data-testid="tx-fault-reset-note"
          >{t('core.rxTx.fault.reset.note')}</span>
        {:else}
          <p
            class="tx-fault-note" data-testid="tx-fault-reset-blocked" role="status"
            data-reason={faultObligation}
          >{t('core.rxTx.fault.reset.blocked', { reason: t(FAULT_OBLIGATION_KEY[faultObligation!]) })}</p>
        {/if}
      </div>
    {/if}
  {/snippet}

  <!--
    MOR-1258 (owner decision, 2026-08-04, gate item (b)). The conditional
    TX-adjacent alerts — the fault recovery above and the two
    ModInputTxWarning buttons ("Set LAN" / dismiss) — are formal members of
    the rx-tx zone: they render beside RxTxSurface (inside its bound zone
    element in the dual composition) rather than in a new `alerts` zone or
    as a pinned outside-by-design exception. Grouping them in one snippet
    keeps ONE place that decides where they render, mirroring `rxTxSurface`
    above.

    MOR-617 network-voice-TX preflight. It ships inside TxPanel, which this
    layout suppresses (`hideTxPanel`) — but this layout can now key network
    voice TX, and the controller's `start-audio` effect IS that path. Without
    the banner the operator keys with a mis-routed MOD input, no warning and
    no one-click "Set LAN" fix: the exact shape of the "web voice TX = noise"
    bug. Self-gating on `deriveModInputTxGuardProps().visible`, so the
    trigger conditions are byte-identical to the panel's, and — same as
    before MOR-1258 — NOT gated on the view model: it must warn even before
    capabilities load.
  -->
  {#snippet txAdjacentAlerts()}
    <ModInputTxWarning />
  {/snippet}

  <!--
    MOR-1265. STRUCTURAL gate: the surface mounts only when the view model
    actually carries the group, so a radio the MOR-1244 evidence gate
    declined renders the pre-1265 element shape exactly (pinned in
    `__tests__/semantic-tx-aux-wiring.component.test.ts`).

    CORRECTION: the sentences that stood here described a bare, unzoned mount
    in BOTH compositions because `'txAux'` was merely DECLARABLE. That held
    only until MOR-1336 (S4), which routed this surface through the generic
    `zoned()` path below AND declared the zone on both sides of the
    composition split — `desktop-declarations.ts` (single, the flagship skin)
    and `dual-receiver-cockpit.ts` (dual) each carry
    `{ id: 'tx-aux', surfaces: ['txAux'] }`. It renders bare wherever
    `zoneOwning()` finds no zone carrying it: under a layout that declares no
    `tx-aux` zone, on a standalone mount that resolves no plan, OR when a
    workspace subtraction has emptied the zone — see `zoneOwning`'s own
    LIMITATION note above, which records that the plan is POST-subtraction and
    that this last case is indistinguishable from the first two here.
    `view?.txAux` (rather than the caller
    nesting this under `{#if view}`) keeps the same "never renders while
    view is null" behavior now that the surrounding structure changed
    around it (MOR-1258).
  -->
  <!--
    MOR-1336 — the ONE zone-aware mount path, applied uniformly to every
    optional surface that used to render bare. Declared → a real zone element
    the layout's arrangement can place; undeclared → bare by default, exactly
    as before — unless the caller passes `allowBare={false}` (MOR-2150 below).

    `vfo`/`rxTx` reach it only through the single composition's `regions`
    branch (MOR-2231), and only for the WRAPPER. Their bodies stay bespoke
    snippets — per-receiver slicing, the `showVfoList`/`showRadioWideFacts`
    split and the R6 TX-adjacent alerts are arrangements no uniform wrapper can
    express, which is why the DUAL composition still builds its own
    `.rx-tx-zone` rather than calling this.

    `present` is the surface's OWN structural gate, hoisted to the wrap
    decision. Without it a declared zone would render as an empty `<div>` for a
    radio whose view model carries no such group — an "empty promise" zone,
    exactly what MOR-1069 forbids. The snippet bodies keep their own `{#if}`
    as well: one decides whether the zone exists, the other whether the surface
    does, and they must agree.

    MOR-2150 — `allowBare` (default `true`, unchanged for every call site that
    existed before this ticket). The DUAL composition's MOR-1069 rule is
    stricter than the single composition's: no focusable control may sit
    outside a declared zone, with `rx-tx` last in tab order, so "no zone →
    bare" is not a safe fallback there for a control-bearing surface the way
    it is in `single`. Passing `false` turns "no zone → bare" into "no zone →
    nothing", still through this one path and the same `zoneOwning()` lookup —
    not a second mount mechanism, a different answer to "and if nobody owns
    it?".
  -->
  {#snippet zoned(
    surface: SemanticSurfaceName, present: boolean, body: Snippet, allowBare = true,
  )}
    {#if present}
      {@const zoneId = zoneOwning(surface)}
      {#if zoneId !== null}
        <div class="surface-zone" data-zone-id={zoneId}>{@render body()}</div>
      {:else if allowBare}{@render body()}{/if}
    {/if}
  {/snippet}

  {#snippet txAuxSurface()}
    {#if view?.txAux}
      <TxAuxSurface
        {view} tx={txState}
        onToggle={(field) => TX_AUX_TOGGLE_INTENT[field]()}
        onLevelChange={(field, value) => TX_AUX_LEVEL_INTENT[field](value)}
        onAtuTune={requestAtuTune}
      />
    {/if}
  {/snippet}

  <!--
    MOR-1273 (vocabulary slice 2B). Same structural gate, same reasoning as
    `txAuxSurface` above: the surface mounts only when the view model actually
    carries the MOR-1269 `meters` group, so a radio that reports no meters —
    or one for which no App TX authority was supplied, and therefore no honest
    TX relevance could be stated — renders the pre-1273 element shape exactly.
    CORRECTION: `'meters'` became DECLARABLE with this slice, and MOR-1341
    (S5) declared it — `desktop-declarations.ts` carries `{ id: 'meters',
    surfaces: ['meters'] }`, and `RadioLayout.svelte` retires the legacy
    `<MetersDockPanel>` on that declaration. The "bare and unzoned in BOTH
    compositions" shape this paragraph described held only until S5. It still
    renders bare in the DUAL composition, whose only layout
    (`dual-receiver-cockpit.ts`) declares no `meters` zone. The zone schema
    stays config-free (risk R3) either way.

    It takes NO authority snapshot and no intent callbacks. That is the R9
    boundary made structural: the meters are a readout, their TX truth is
    already decided inside `view.meters` by the App-owned authority the
    adapter was handed, and this component has nothing else to give them.
  -->
  {#snippet metersSurface()}
    {#if view?.meters}
      <MetersSurface {view} />
    {/if}
  {/snippet}

  <!--
    MOR-1279 (vocabulary slice 3B). Same structural gate and same reasoning as
    `txAuxSurface`/`metersSurface` above: the surface mounts only when the view
    model actually carries the MOR-1274 `rxAudio` group, so a radio with no
    audio chain renders the pre-1279 element shape exactly.

    UNLIKE those two, it is control-bearing — the first semantic surface with
    interactive controls the DUAL composition's only layout
    (`dual-receiver-cockpit.ts`) declares no zone for — and the cockpit has a
    hard MOR-1069 rule: every focusable control lives inside a declared zone,
    and the rx-tx zone is LAST in the tab order. A control-bearing surface
    mounted bare would break both clauses at once, and the two alternatives
    are worse — binding a zone id no layout asked for is the MOR-1069 lesson
    itself, and folding the controls into the rx-tx zone would put an AF
    slider between the operator and the unkey button. MOR-2150 closes the
    third option — `allowBare={false}` below — so it mounts through `zoned()`
    in BOTH compositions, same as `txAux`/`meters`, and dual just never falls
    back to bare. `'rxAudio'` became DECLARABLE with this slice, so the
    cockpit gains the surface the moment its manifest declares a zone for it —
    a layout decision, separately reviewed, exactly as txAux and meters left
    it. `desktop-v2` HAS since made that decision (MOR-1368, S9), which is why
    the single composition mounts this surface zoned UNDER THAT LAYOUT — not
    under `sdr-test`/`mobile`/`lcd-*`, which declare no such zone. The cockpit
    has made no such decision either, so it still renders nothing there.

    It takes NO authority snapshot: nothing here is TX truth. The intents are
    the shipped command bus, wired above.
  -->
  {#snippet rxAudioSurface()}
    {#if view?.rxAudio}
      <RxAudioSurface
        {view}
        onMonitorMode={(mode) => rxAudioIntents.onMonitorModeChange(mode)}
        onAfLevel={(level) => rxAudioIntents.onAfLevelChange(level)}
        onRoutingFocus={(focus) => routingIntents.onFocusChange(focus)}
        onRoutingSplit={(split) => routingIntents.onSplitStereoChange(split)}
        onSetModInputLan={setModInputLan}
      />
    {/if}
  {/snippet}

  <!--
    MOR-1304 (vocabulary slice 4B). Same structural gate as `metersSurface`
    above: the surface mounts only when the view model carries EITHER of the
    two fact groups it renders (`view.modeFilter` from MOR-1280, `view.
    filterPassband` from MOR-1284) — a radio the evidence gate declined on
    both renders the pre-1304 element shape exactly.

    UNLIKE `txAuxSurface`/`metersSurface` it is control-bearing (fix round,
    verify-MOR-1304 F1). `FilterSurface` renders up to 14 focusable controls
    (mode/filter/shape buttons, width and passband-level sliders), and the
    DUAL COCKPIT manifest (`dual-receiver-cockpit.ts`) declares no `filter`
    zone, so a bare mount there would put every one of those controls outside
    every declared zone and after the `rx-tx` zone that MOR-1069 requires to
    end the tab order — exactly the shape the MOR-1279/MOR-1336 zone-mount
    ruling forbids for any control-bearing surface. MOR-2150 (`allowBare=
    false` below) closes that off structurally rather than by convention:
    dual never falls back to bare, so `'filter'` mounts there the moment
    whichever layout's manifest declares a zone for it — a layout decision,
    separately reviewed, exactly as rxAudio left it.

    CORRECTION (MOR-1494 review round): the SINGLE composition (`desktop-v2`)
    already made that layout decision — `desktop-declarations.ts:71` declares
    `{ id: 'filter', surfaces: ['filter'] }` (landed with MOR-1366, S7). Since
    then `FilterSurface`, not `LeftSidebar`'s legacy `FilterPanel`, has been
    the LIVE renderer on `desktop-v2` (`RadioLayout.svelte`'s
    `!declared.has('filter')` suppression). This paragraph previously read as
    if no manifest anywhere had declared the zone yet; it hadn't been updated
    after S7 landed, and that staleness led a later review round to
    misdiagnose which component renders the IF-shift control on desktop-v2.
    The dual cockpit remains the one manifest still undeclared for this zone
    (`sdr-test`, `mobile` and the two `lcd-*` layouts declare no `filter` zone
    either), so it still renders nothing there.
  -->
  {#snippet filterSurface()}
    {#if view?.modeFilter || view?.filterPassband}
      <FilterSurface
        {view}
        {pendingFilter}
        onModeChange={filterIntents.onModeChange}
        onFilterChange={filterIntents.onFilterChange}
        onFilterWidthChange={filterIntents.onFilterWidthChange}
        onFilterShapeChange={filterIntents.onFilterShapeChange}
        onIfShiftChange={filterIntents.onIfShiftChange}
        onPbtInnerChange={filterIntents.onPbtInnerChange}
        onPbtOuterChange={filterIntents.onPbtOuterChange}
      />
    {/if}
  {/snippet}

  <!--
    MOR-1309 (vocabulary slice 8C, SAFETY-ADJACENT). Same structural gate as
    the surfaces above, and the SAME control-bearing status `rxAudio` carries:
    this surface renders focusable controls and the DUAL composition's only
    layout (`dual-receiver-cockpit.ts`) declares no `antenna` zone. MOR-2150
    passes `allowBare={false}` below for exactly this reason: a plain
    `zoned()` call would grant nothing (`zoneOwning()` returns null for a
    surface the ACTIVE layout has not declared, and the default renders
    bare — the violating shape MOR-1069 forbids); `allowBare={false}` makes
    the mount render nothing instead. Its absence from the dual composition
    is pinned by name in `__tests__/semantic-antenna-wiring.component.test.ts`.
    `'antenna'` became DECLARABLE with this slice, and `desktop-v2` declared
    it in MOR-1367 (S8) — which is why the SINGLE composition mounts it zoned
    under THAT layout (not under `sdr-test`/`mobile`/`lcd-*`). The cockpit has
    made no such decision, so it still renders nothing there.

    It DOES take the App-owned TX authority snapshot: unlike `rxAudio`,
    switching a TX antenna under power is a hazard, and the surface gates on
    the SHARED `keyBlockedReasons` predicate applied to this snapshot. It
    still takes no lease and keys nothing — exactly one `<RxTxSurface>`
    remains the key/unkey authority (R9).
  -->
  {#snippet antennaSurface()}
    {#if view?.antenna}
      <AntennaSurface
        {view} tx={txState}
        onSelectPort={(port) => ANTENNA_PORT_INTENT[port]?.()}
        onToggleRxAnt={antennaIntents.onToggleRxAnt}
      />
    {/if}
  {/snippet}

  <!--
    MOR-1305 (vocabulary slice 5B). Same structural gate and same reasoning as
    `rxAudioSurface` above: the surface mounts only when the view model
    actually carries the MOR-1290 `dsp` group, so a radio the evidence gate
    declined renders the pre-1305 element shape exactly.

    Like `rxAudioSurface` and UNLIKE `txAuxSurface`/`metersSurface`, it is
    control-bearing (MOR-1304/MOR-1305 zone-mount ruling). `DspSurface`
    renders up to 8 range inputs and 7 buttons, and `desktop-v2` declared a
    `dsp` zone in MOR-1368 (S9) while the cockpit still declares none; the
    cockpit's MOR-1069 rule forbids mounting any control-bearing surface bare
    in the dual composition: every focusable control must live inside a
    declared zone, with rx-tx last in the tab order — which is exactly why
    MOR-2150 mounts it there with `allowBare={false}` below rather than the
    default. `'dsp'` became DECLARABLE with this slice, so the cockpit gains
    the surface the moment its manifest declares a zone for it — a layout
    decision, separately reviewed, exactly as rxAudio left it. It still
    renders nothing there today.

    `agcLabels`/`nbLevelMax`/`nbLevelPercent` are the caps-echo metadata
    carry-forward (1) requires stay OUT of the view model — read at this seam,
    from `runtime.caps`, and handed down as plain props.
  -->
  {#snippet dspSurface()}
    {#if view?.dsp}
      <DspSurface
        {view} {agcLabels} {nbLevelMax} {nbLevelPercent} {pendingNb} {pendingNr}
        onToggle={(field, next) => DSP_TOGGLE_INTENT[field](next)}
        onLevelChange={(field, value) => DSP_LEVEL_INTENT[field](value)}
        onNotchModeChange={dspIntents.onNotchModeChange}
        onAgcModeChange={agcIntents.onAgcModeChange}
      />
    {/if}
  {/snippet}

  <!--
    MOR-1306 (vocabulary slice 6B). Same structural gate and same reasoning as
    `rxAudioSurface` above: the surface mounts only when the view model
    actually carries the MOR-1292/MOR-1293 `rfFrontEnd` group, so a radio with
    no preamp/attenuator/RF-gain/squelch/DIGI-SEL/IP+ capability renders the
    pre-1306 element shape exactly.

    CONTROL-BEARING — the MOR-1304 mounting canon (`RfFrontEndSurface.svelte`'s
    file header): this surface carries focusable controls (preamp and
    attenuator choice buttons, RF-gain/squelch sliders, DIGI-SEL/IP+ toggles)
    and the DUAL composition's only layout (`dual-receiver-cockpit.ts`)
    declares no `rfFrontEnd` zone, so a bare dual mount would put controls
    outside every declared zone — exactly the defect the MOR-1279 rxAudio
    precedent avoided, and exactly why MOR-2150 mounts it with
    `allowBare={false}` below. `'rfFrontEnd'` became DECLARABLE with this
    slice, and `desktop-v2` declared it in MOR-1366 (S7); the cockpit gains
    the surface the moment a rework slice declares a zone for it there, same
    as `rxAudio` left it — it still renders nothing there today.
  -->
  {#snippet rfFrontEndSurface()}
    {#if view?.rfFrontEnd}
      <RfFrontEndSurface
        {view}
        controlModel={rfSqlControlModel}
        {pendingPreamp}
        onPreampChange={(level) => rfFrontEndIntents.onPreChange(level)}
        onAttenuatorChange={(db) => rfFrontEndIntents.onAttChange(db)}
        onLevelChange={(field, value) => RF_FRONT_END_LEVEL_INTENT[field](value)}
        onToggle={(field, next) => RF_FRONT_END_TOGGLE_INTENT[field](next)}
      />
    {/if}
  {/snippet}

  <!--
    MOR-1307 (vocabulary slice 7B). Same structural gate as the surfaces above,
    and the SAME control-bearing status as `rxAudioSurface`, for the same
    MOR-1069 reason: this surface is control-bearing (band buttons, a
    frequency entry field and its Set button) and the DUAL composition's only
    layout (`dual-receiver-cockpit.ts`) declares no `band` zone, so a bare
    dual mount would put focusable controls outside every declared zone and
    break the cockpit's "tab order ends in rx-tx" invariant. A plain `zoned()`
    call does not prevent that by itself — `zoneOwning()` answers `null` for a
    surface the ACTIVE layout has not declared, and the default renders BARE,
    which is the violating shape — so MOR-2150 passes `allowBare={false}`
    below instead. `'band'` becomes DECLARABLE with this slice, and
    `desktop-v2` declared it in MOR-1367 (S8) — retiring `BandSelector`'s HAM
    half only, through `hamBands={!declared.has('band')}`. The cockpit gains
    the surface the moment a rework slice declares a zone for it there, and
    the dual absence is pinned by name in
    `__tests__/semantic-band-wiring.component.test.ts`.

    It takes NO authority snapshot: the TX permit it renders is already decided
    inside `view.band` by the one shipped derivation, and this component has no
    second one to offer.
  -->
  {#snippet bandSurface()}
    {#if view?.band}
      <BandSurface {view} onSelectBand={selectBand} onEnterFrequency={enterFrequency} />
    {/if}
  {/snippet}

  <!--
    MOR-1308 (vocabulary slice 8B). Same reasoning as `rxAudioSurface` above:
    the second semantic surface carrying interactive controls the DUAL
    composition's only layout (`dual-receiver-cockpit.ts`) declares no zone
    for, so — per the MOR-1304 mounting canon — MOR-2150 mounts it with
    `allowBare={false}` below rather than the bare default, and its
    dual-composition absence today is pinned in
    `__tests__/semantic-ritxit-scan-wiring.component.test.ts` with a view
    model that actually carries the `ritXit`/`scan` groups (a fixture that
    cannot see the surface would repeat the bug that canon exists to catch).
    `'ritXitScan'` becomes DECLARABLE with this slice, and `desktop-v2`
    declared it in MOR-1367 (S8); the cockpit still declares none.
  -->
  {#snippet ritXitScanSurface()}
    {#if view?.ritXit || view?.scan}
      <RitXitScanSurface
        {view} {ritDomain}
        onRitToggle={ritXitIntents.onRitToggle}
        onXitToggle={ritXitIntents.onXitToggle}
        onRitOffsetChange={ritXitIntents.onRitOffsetChange}
        onXitOffsetChange={ritXitIntents.onXitOffsetChange}
        onClear={ritXitIntents.onClear}
        onScanStart={(type) => scanIntents.onScanStart(type)}
        onScanStop={scanIntents.onScanStop}
        onResumeModeChange={(mode) => scanIntents.onResumeChange(mode)}
      />
    {/if}
  {/snippet}

  <!--
    MOR-1310 (vocabulary slice 9B) — SAFETY-CRITICAL. Same structural gate as
    the surfaces above, and the SAME control-bearing status as
    `rxAudioSurface`: this surface is control-bearing, the DUAL composition's
    only layout (`dual-receiver-cockpit.ts`) declares no `cwKeyer` zone, and
    the MOR-1069 cockpit rule is that every focusable
    control lives inside a declared zone with rx-tx last in the tab order.
    Mounted bare in the dual composition it would break both clauses — which
    is why MOR-2150 mounts it with `allowBare={false}` below instead; folded
    into the rx-tx zone it would put a keyer slider between the operator and
    the unkey button. `'cwKeyer'` became DECLARABLE with this slice, and
    `desktop-v2` declared it in MOR-1368 (S9) — which, per that manifest's own
    SAFETY note, makes `CwKeyerSurface` the sole break-in affordance on the
    flagship skin. The cockpit gains the surface the moment ITS manifest
    declares a zone — a layout decision, separately reviewed, exactly as
    rxAudio left it. Its absence from
    the dual composition is pinned by name in
    `__tests__/semantic-cw-keyer-wiring.component.test.ts`.

    It takes NO authority snapshot and no key intent: break-in is a SETTING,
    gated inside the surface on the model's one `txPermit`, and the key/unkey
    authority stays the single `<RxTxSurface>` above (decomposition R9).
  -->
  {#snippet cwKeyerSurface()}
    {#if view?.cwKeyer}
      <CwKeyerSurface
        {view}
        {breakInDelayFeedback}
        {autoTuneAvailable}
        onBreakInMode={(mode) => cwIntents.onBreakInModeChange(mode)}
        onLevelChange={(field, value) => CW_LEVEL_INTENT[field](value)}
        onApfOn={(on) => cwIntents.onApfChange(on ? 1 : 0)}
        onTwinPeakToggle={() => cwIntents.onTwinPeakToggle()}
        onReversePaddleToggle={() => cwIntents.onReversePaddleToggle()}
        onAutoTune={cwIntents.onAutoTune}
      />
    {/if}
  {/snippet}

  <!--
    MOR-1312 (vocabulary slice 12B). Same structural gate and same reasoning
    as `txAuxSurface`/`metersSurface` above: the surface mounts only when the
    view model actually carries the MOR-1301 `scopeDisplay` group, so a radio
    with neither a hardware scope nor an audio-FFT source renders the
    pre-1312 element shape exactly. Mounted in BOTH compositions with the
    DEFAULT `allowBare` (unlike `rxAudio`'s MOR-2150 `allowBare={false}`) —
    the `meters`/`txAux` shape — and zoned wherever `zoneOwning()` finds a
    zone carrying `scopeDisplay`, which
    `desktop-v2` has declared as `scope-display` since MOR-1365 (S6a); bare
    otherwise, including the dual composition, whose only layout
    (`dual-receiver-cockpit.ts`) declares none:
    `ScopeDisplaySurface` renders zero focusable elements (pinned in
    `__tests__/ScopeDisplaySurface.test.ts` and re-pinned below at the
    composed-tree level), so it carries none of the MOR-1069 tab-order risk a
    control-bearing surface would. `'scopeDisplay'` becomes DECLARABLE with
    this slice, so a manifest gains the surface the moment it declares a zone
    for it — a layout decision, separately reviewed, exactly as
    txAux/meters/rxAudio left it.

    It takes NO intent callbacks: a source/health/hardware readout has no
    action to offer (v3 ADR invariant 11).
  -->
  {#snippet scopeDisplaySurface()}
    {#if view?.scopeDisplay}
      <ScopeDisplaySurface {view} />
    {/if}
  {/snippet}

  <!--
    MOR-1311 (vocabulary slice 11B, the LAST B-slice of the vocabulary
    program). Same mounting canon as `ritXitScanSurface`/`cwKeyerSurface`
    above: control-bearing, and the DUAL composition's only layout
    (`dual-receiver-cockpit.ts`) declares no `scopeControls` zone, so per the
    MOR-1304 ruling's option (i) MOR-2150 mounts it with `allowBare={false}`
    below — never bare in the dual composition — and it currently renders
    NOTHING there, pinned by name in
    `__tests__/semantic-scope-controls-wiring.component.test.ts` and in
    `skins/dual-receiver-cockpit/__tests__/DualReceiverCockpit.component
    .test.ts`'s MOR-2150 describe block. No longer bare under `desktop-v2`,
    which declared this zone in MOR-1370 (S6b-2) as the last surface in the
    vocabulary to graduate; still bare under the single-composition layouts
    that declare none (`sdr-test`/`mobile`/`lcd-*`).
  -->
  {#snippet scopeControlsSurface()}
    {#if view?.scopeControls}
      <ScopeControlsSurface
        {view}
        onToggleChange={(field, next) => SCOPE_TOGGLE_INTENT[field](next)}
        onChoiceChange={(field, value) => SCOPE_CHOICE_INTENT[field](value)}
        onSpanChange={scopeIntents.onSpanChange}
        onSpeedChange={scopeIntents.onSpeedChange}
        onRefChange={scopeIntents.onRefChange}
      />
    {/if}
  {/snippet}

  {#if strips === 'dual'}
    <!--
      MOR-1258: the zone now carries RxTxSurface AND the two TX-adjacent
      alerts that used to sit at the bottom of this component, unzoned.
      TxAuxSurface stays OUTSIDE this zone — it mounts through `zoned()` in
      its OWN `tx-aux` zone, which the cockpit manifest has declared since
      MOR-1336 — so it renders after the rx-tx zone rather than between
      RxTxSurface and the alerts,
      which is the one DOM-order change this ticket makes: the alerts move
      up to sit beside RxTxSurface instead of after TxAuxSurface.
    -->
    <!--
      MOR-1082: only the SURFACE is workspace-gated. The TX-adjacent alerts
      stay unconditional — they are not a semantic surface, and the MOD-input
      preflight in particular must warn regardless of any presentation
      preference. In every shipped layout `rxTx` is `requiredSemanticSurfaces`
      and exactly one zone mounts it, so the plan refuses to hide it anyway;
      the gate is here so the rule is enforced where it is read, not assumed.
    -->
    <div class="rx-tx-zone" data-zone-id="rx-tx">
      {#if zoneShows('rx-tx', 'rxTx')}{@render rxTxSurface()}{/if}
      <!-- MOR-1784: behind the surface that prints the fault, ahead of the
           MOD-input banner; outside the workspace gate, like the alerts. -->
      {@render txFaultRecovery()}
      {@render txAdjacentAlerts()}
    </div>
    {@render zoned('txAux', view?.txAux !== undefined, txAuxSurface)}
    {@render zoned('meters', view?.meters !== undefined, metersSurface)}
    <!--
      MOR-2150. The nine remaining optional surfaces, mounted zone-only
      (`allowBare=false`): a control-bearing surface must never render bare
      here (MOR-1069 above `zoned()`), so each renders only where a layout's
      manifest declares a zone for it. `dual-receiver-cockpit.ts` declares
      none of the nine, so every one of these currently renders nothing —
      pinned in `skins/dual-receiver-cockpit/__tests__/
      DualReceiverCockpit.component.test.ts`'s MOR-2150 describe block, which
      also proves the positive case (a zone that DOES declare one) against a
      synthetic plan, since no shipped manifest does yet.
    -->
    {@render zoned('rxAudio', view?.rxAudio !== undefined, rxAudioSurface, false)}
    {@render zoned(
      'filter', view?.modeFilter !== undefined || view?.filterPassband !== undefined, filterSurface,
      false,
    )}
    {@render zoned('dsp', view?.dsp !== undefined, dspSurface, false)}
    {@render zoned('rfFrontEnd', view?.rfFrontEnd !== undefined, rfFrontEndSurface, false)}
    {@render zoned('band', view?.band !== undefined, bandSurface, false)}
    {@render zoned('antenna', view?.antenna !== undefined, antennaSurface, false)}
    {@render zoned(
      'ritXitScan', view?.ritXit !== undefined || view?.scan !== undefined, ritXitScanSurface, false,
    )}
    {@render zoned('cwKeyer', view?.cwKeyer !== undefined, cwKeyerSurface, false)}
    {@render zoned('scopeDisplay', view?.scopeDisplay !== undefined, scopeDisplaySurface)}
    {@render zoned('scopeControls', view?.scopeControls !== undefined, scopeControlsSurface, false)}
  {:else}
    {#if regions}
      {#if singleOrder.includes('vfo')}
        {@render zoned('vfo', view !== null, vfoSurface)}
      {/if}
      {@render zoned('rfFrontEnd', view?.rfFrontEnd !== undefined, rfFrontEndSurface, allowBareSurfaces)}
      {@render zoned(
        'filter', view?.modeFilter !== undefined || view?.filterPassband !== undefined, filterSurface,
        allowBareSurfaces,
      )}
      {@render zoned('band', view?.band !== undefined, bandSurface, allowBareSurfaces)}
      {@render zoned('antenna', view?.antenna !== undefined, antennaSurface, allowBareSurfaces)}
      {@render zoned(
        'ritXitScan', view?.ritXit !== undefined || view?.scan !== undefined, ritXitScanSurface,
        allowBareSurfaces,
      )}
      {@render zoned(
        'scopeControls', view?.scopeControls !== undefined, scopeControlsSurface,
        allowBareSurfaces,
      )}
      {@render zoned('scopeDisplay', view?.scopeDisplay !== undefined, scopeDisplaySurface, allowBareSurfaces)}
      {#if regionContent}{@render regionContent()}{/if}
      {@render zoned('rxAudio', view?.rxAudio !== undefined, rxAudioSurface, allowBareSurfaces)}
      {@render zoned('dsp', view?.dsp !== undefined, dspSurface, allowBareSurfaces)}
      {@render zoned('cwKeyer', view?.cwKeyer !== undefined, cwKeyerSurface, allowBareSurfaces)}
      {@render zoned('txAux', view?.txAux !== undefined, txAuxSurface, allowBareSurfaces)}
      {#if singleOrder.includes('rxTx')}
        {@render zoned('rxTx', view !== null, rxTxSurface)}
      {/if}
      {@render txFaultRecovery()}
      {@render txAdjacentAlerts()}
      {@render zoned('meters', view?.meters !== undefined, metersSurface, allowBareSurfaces)}
    {:else}
    <!--
      Single/default path (sdr-test / LCD / mobile). No zone CONTAINS the
      alerts here (MOR-1069 — the dual composition's `.rx-tx-zone` has no twin
      on this path), so they keep their pre-MOR-1258 position and order,
      unchanged.

      MOR-1082: `singleOrder` is the plan flattened in zone-declaration order,
      falling back to the composed order whenever no plan is resolved — so an
      unresolved plan renders exactly the sequence this path renders today, and
      a per-zone reorder lands here. On a layout whose zones hold one surface
      each that flattening is what ORDERS the zones; on one whose single zone
      mounts both (LCD `control-column`, mobile `portrait-deck`) it is what
      reorders within it.

      This branch keeps the pre-MOR-2231 single/default sequence with bare
      required surfaces when `regions` is unset.
    -->
    {#each singleOrder as surface (surface)}
      {#if surface === 'vfo'}
        {@render vfoSurface()}
      {:else if surface === 'rxTx'}
        <!-- Preserve the pre-region compiled anchor topology on this literal path. -->
        {#if !regions}
          {@render rxTxSurface()}
        {/if}
      {/if}
    {/each}
    <!-- MOR-1784: `singleOrder` ends in `rxTx` in every shipped layout
         (`SINGLE_COMPOSITION`, and a plan can only reorder or subtract), so
         this lands directly behind the fault line instead of at the bottom of
         the column — while still rendering unconditionally, so a fault raised
         by any lease source keeps a way out even if `rxTx` is absent. -->
    {@render txFaultRecovery()}
    <!-- MOR-2231 (step 1, batch 5): the twelve OPTIONAL surfaces take
         `allowBareSurfaces` (see its declaration above) instead of the bare
         default. `vfo`/`rxTx` above keep the default: they are in
         `requiredSemanticSurfaces`, which `resolveSurfacePlan` force-restores,
         so no plan can leave either without a zone. -->
    {@render zoned('txAux', view?.txAux !== undefined, txAuxSurface, allowBareSurfaces)}
    {@render zoned('meters', view?.meters !== undefined, metersSurface, allowBareSurfaces)}
    {@render zoned('rxAudio', view?.rxAudio !== undefined, rxAudioSurface, allowBareSurfaces)}
    {@render zoned(
      'filter', view?.modeFilter !== undefined || view?.filterPassband !== undefined, filterSurface,
      allowBareSurfaces,
    )}
    {@render zoned('dsp', view?.dsp !== undefined, dspSurface, allowBareSurfaces)}
    {@render zoned('rfFrontEnd', view?.rfFrontEnd !== undefined, rfFrontEndSurface, allowBareSurfaces)}
    {@render zoned('band', view?.band !== undefined, bandSurface, allowBareSurfaces)}
    {@render zoned('antenna', view?.antenna !== undefined, antennaSurface, allowBareSurfaces)}
    {@render zoned(
      'ritXitScan', view?.ritXit !== undefined || view?.scan !== undefined, ritXitScanSurface,
      allowBareSurfaces,
    )}
    {@render zoned('cwKeyer', view?.cwKeyer !== undefined, cwKeyerSurface, allowBareSurfaces)}
    {@render zoned('scopeDisplay', view?.scopeDisplay !== undefined, scopeDisplaySurface, allowBareSurfaces)}
    {@render zoned(
      'scopeControls', view?.scopeControls !== undefined, scopeControlsSurface,
      allowBareSurfaces,
    )}
    {@render txAdjacentAlerts()}
    {/if}
  {/if}
</div>

<style>
  /* Layout only — the surfaces own their own presentation. */
  .semantic-surfaces {
    display: flex;
    flex-direction: column;
    gap: 8px;
    height: 100%;
    overflow: auto;
    font-family: 'Roboto Mono', monospace;
    color: var(--v2-text-primary, #e8e8e8);
  }
  /* MOR-1784: structure only — the action and the sentence that explains it
     stack as one block, so neither can be read without the other. */
  .tx-fault-recovery {
    display: flex;
    flex-direction: column;
    align-items: flex-start;
    gap: 4px;
  }
  .tx-fault-note {
    margin: 0;
    font-size: 0.85em;
  }
  .tx-fault-reset {
    align-self: flex-start;
    padding: 3px 8px;
    border: 1px solid var(--v2-accent-red, #ef4444);
    border-radius: 4px;
    background: transparent;
    color: var(--v2-accent-red, #ef4444);
    font: inherit;
    cursor: pointer;
  }
  /* MOR-1067: two borderless channel strips sharing one optical left margin —
     the RxTxSurface below stays a single shared block, outside this grid. */
  .channel-strips {
    display: grid;
    grid-template-columns: repeat(auto-fit, minmax(0, 1fr));
    gap: 8px;
  }
  /* MOR-1069: no `display: contents` any more — the zone only exists in the
     dual composition now, and it must be a real box there so the cockpit can
     place it. MOR-1258: the zone now stacks RxTxSurface with the two
     TX-adjacent alerts it gained, so it owns this minimal layout — the
     alerts and RxTxSurface still each own their own internal presentation. */
  .rx-tx-zone {
    display: flex;
    flex-direction: column;
    gap: 8px;
  }
  /* MOR-1336: a declared zone must be a real box the arrangement can place —
     the MOR-1069 lesson that an inert `display: contents` wrapper cannot be a
     grid/flex item. Layout only; the surface owns its own presentation. */
  .surface-zone {
    display: flex;
    flex-direction: column;
    gap: 8px;
  }

  .channel-strip[data-strip-active='true'] {
    border-left: 2px solid var(--v2-accent-cyan, #00d4ff);
    padding-left: 6px;
  }
</style>
