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
  import { onDestroy, type Snippet } from 'svelte';
  import { t } from '$lib/i18n';
  import { runtime } from '$lib/runtime';
  import { toRadioViewModel } from '$lib/runtime/adapters/radio-view-model-adapter';
  import { getAppTxController } from '$lib/runtime/tx-controller/app-host';
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
    makeAgcHandlers, makeAntennaHandlers, makeAudioRoutingHandlers, makeBandHandlers,
    makeCwPanelHandlers, makeDspHandlers, makeFilterHandlers, makeModeHandlers,
    makeRfFrontEndHandlers, makeRitXitHandlers, makeRxAudioHandlers, makeScanHandlers,
    makeScopeControlsHandlers, makeTxHandlers, makeVfoHandlers, makeVoxHandlers,
  } from './command-bus';
  import {
    forReceiver, receiversOf, isActiveStrip, isOperationalStrip,
  } from './dual-receiver-strips';

  /**
   * `'single'` (default) is the exact pre-MOR-1067 markup — sdr-test's
   * behavior is untouched. `'dual'` is the dual-receiver-cockpit's per-
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
  }
  let { strips = 'single' }: Props = $props();

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

  const vfo = makeVfoHandlers();
  /** MOR-1307: the shipped band vocabulary, composed rather than forked. */
  const band = makeBandHandlers();
  /**
   * MOR-1265. The two v2 handler factories already carry every txAux intent
   * (`makeVoxHandlers` owns gain/anti-VOX/delay, `makeTxHandlers` the rest);
   * composing them keeps ONE command vocabulary rather than a v3 fork of it.
   * The two maps below exist so the pure surface can stay field-addressed —
   * agreement with the shipped command-bus names is pinned in
   * `__tests__/tx-aux-command-bus.isolated.test.ts` against the REAL module.
   */
  const txAuxIntents = { ...makeVoxHandlers(), ...makeTxHandlers() };
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
   * command bus rather than forked: monitor mode + AF level from
   * `makeRxAudioHandlers` (whose `onAfLevelChange` takes 0..1 — the contract's
   * own unit, so nothing rescales on the way out either), routing from
   * `makeAudioRoutingHandlers`, and the MOD-input remedy from
   * `makeModeHandlers().onModInputChange` — the SAME command
   * `ModInputTxWarning`'s "Set LAN" fires, so a mismatch has one fix, not two.
   * `ModInputTxWarning` itself is untouched and stays in the rx-tx zone.
   */
  const rxAudioIntents = makeRxAudioHandlers();
  const routingIntents = makeAudioRoutingHandlers();
  /**
   * MOR-1309. The antenna intent vocabulary, composed from the SHIPPED command
   * bus rather than forked — `set_antenna_1`/`set_antenna_2`/the two RX-ANT
   * commands are exactly what `AntennaPanel` fires today. The port map exists
   * so the pure surface can stay PORT-addressed while the bus stays
   * command-addressed; a port the bus cannot name is unreachable by
   * construction rather than by a silent no-op.
   */
  const antennaIntents = makeAntennaHandlers();
  const ANTENNA_PORT_INTENT: Record<number, () => void> = {
    1: antennaIntents.onSelectAnt1, 2: antennaIntents.onSelectAnt2,
  };
  const setModInputLan = () => makeModeHandlers().onModInputChange(LAN_MOD_INPUT_SOURCE);
  /**
   * MOR-1304. `makeModeHandlers` owns mode/dataMode intents, `makeFilterHandlers`
   * the rest — the SAME v2 command vocabulary `FilterPanel` already dispatches
   * through, composed once here rather than forked for the semantic surface.
   * Unlike `txAuxIntents` above, `FilterSurface`'s props already match these
   * names 1:1, so no per-field `Record` indirection is needed.
   */
  const filterIntents = { ...makeModeHandlers(), ...makeFilterHandlers() };
  /**
   * MOR-1305. `makeDspHandlers`/`makeAgcHandlers` already carry every dsp
   * intent; the two maps below keep the pure surface field-addressed, same
   * precedent as `TX_AUX_*_INTENT` above — agreement with the shipped
   * command-bus names is exercised against the REAL module in the component
   * test, not re-asserted here.
   */
  const dspIntents = makeDspHandlers();
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
  const agcIntents = makeAgcHandlers();
  /**
   * `agcLabels`/`nbLevelMax`/`nbLevelPercent` are pure caps-echo display
   * metadata, NOT `dsp` facts (MOR-1290/MOR-1305 carry-forward 1) — read
   * straight off `runtime.caps` here, at the one seam that already holds it
   * for the `toRadioViewModel` call below, verbatim `toDspProps`'s own
   * fallbacks (`lib/runtime/props/panel-props.ts`).
   */
  let agcLabels = $derived(runtime.caps?.agcLabels ?? { '1': 'FAST', '2': 'MID', '3': 'SLOW' });
  let nbLevelRange = $derived(runtime.caps?.controls?.nb_level ?? null);
  let nbLevelMax = $derived(nbLevelRange?.raw_max ?? 10);
  let nbLevelPercent = $derived(nbLevelRange !== null);
  /**
   * MOR-1306. The RF-front-end intent vocabulary, composed from the SHIPPED
   * command bus rather than forked — same discipline as `rxAudioIntents`
   * above. `RF_FRONT_END_LEVEL_INTENT` maps the surface's field-addressed
   * `onLevelChange` onto the two real level handlers, mirroring
   * `TX_AUX_LEVEL_INTENT`.
   */
  const rfFrontEndIntents = makeRfFrontEndHandlers();
  const RF_FRONT_END_LEVEL_INTENT: Record<RfFrontEndLevelField, (value: number) => void> = {
    rfGain: rfFrontEndIntents.onRfGainChange, squelch: rfFrontEndIntents.onSquelchChange,
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
  const ritXitIntents = makeRitXitHandlers();
  const scanIntents = makeScanHandlers();
  /**
   * MOR-1310 (slice 9B). The CW intent vocabulary, composed from the SHIPPED
   * `makeCwPanelHandlers` rather than forked. `onAutoTune` is deliberately NOT
   * wired: `cw_auto_tune` is a transmit-causing action and the surface offers no
   * control for it (MOR-1244 ATU-TUNE precedent). Nothing here keys — exactly
   * one `<RxTxSurface>` stays the key/unkey authority (decomposition R9).
   */
  const cwIntents = makeCwPanelHandlers();
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
  const scopeIntents = makeScopeControlsHandlers();
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

  // Belt-and-braces contract pin. The adapter now annotates its own return
  // type (MOR-1065 ruling 2), so this is the second of two compile-time links.
  // MOR-1262 slice 2A: the live authority snapshot is the THIRD argument — the
  // meter facts read their TX relevance from it and never from `state.ptt`
  // (invariant R9). Without it the adapter emits no `meters` group at all.
  // MOR-1279 slice 3B: the RX-audio snapshot is the FOURTH.
  // MOR-1312 slice 12B: the scope-display snapshot is the FIFTH.
  let view: RadioViewModel | null = $derived(
    toRadioViewModel(runtime.state, runtime.caps, txState, rxAudioSnapshot, scopeDisplaySnapshot),
  );

  // Bound once per instance, never per render — see `surfaceSeq` above.
  function requestKey(): void {
    tx.start(sourceId, `${sourceId}-${++leaseSeq}`, 'latched');
  }
  /** NEVER gated. Reads the LIVE authority guard and releases: no phase,
   *  permit, fault or view-model condition may stand between the operator and
   *  stopping transmission. */
  function requestUnkey(): void {
    const guard = tx.snapshot().guard;
    if (guard) tx.release(sourceId, guard);
  }
  /** App-owned fault recovery (MOR-1065 wiring decision, recorded on the
   *  ticket). The pure RX/TX surface deliberately has no `resetFault` intent,
   *  and a `failed` phase blocks the key action — without this affordance in
   *  the layout chrome the operator has no UI exit from a fault. */
  function clearFault(): void {
    tx.resetFault();
  }

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
   * command-bus handlers the legacy VfoHeader used (`onMainFreqChange` /
   * `onSubFreqChange`), which own the optimistic patch and the `set_freq`
   * command. No new key path and no TX semantics (R9): this moves a frequency,
   * it never keys the transmitter.
   */
  function tuneFrequency(receiver: 'MAIN' | 'SUB', frequencyHz: number): void {
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
    if (bsrCode !== null) band.onBandSelect(name, defaultHz, bsrCode);
    else tuneFrequency(active.receiver, defaultHz);
  }
  function enterFrequency(frequencyHz: number): void {
    const active = view?.activeReceiver;
    if (active?.status !== 'known') return;
    tuneFrequency(active.receiver, frequencyHz);
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
        onToggleSplit={vfo.onSplitToggle}
        onToggleDualWatch={toggleDualWatch}
        onEqualizeVfos={vfo.onEqual}
        onSwapVfos={vfo.onSwap}
        onQuickSplit={vfo.onQuickSplit}
        onQuickDualWatch={vfo.onQuickDw}
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
    to move between arrangements. The single/default path (sdr-test / LCD /
    mobile) renders the surface bare again, and that element shape is
    re-pinned in `__tests__/semantic-rx-tx-wiring.component.test.ts`.

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
    MOR-1258 (owner decision, 2026-08-04, gate item (b)). The three
    conditional TX-adjacent alerts — `tx-fault-reset` and the two
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
    {#if txState.phase === 'failed'}
      <button
        type="button" class="tx-fault-reset" data-testid="tx-fault-reset" onclick={clearFault}
      >Clear TX fault</button>
    {/if}
  {/snippet}

  <!--
    MOR-1265. STRUCTURAL gate: the surface mounts only when the view model
    actually carries the group, so a radio the MOR-1244 evidence gate
    declined renders the pre-1265 element shape exactly (pinned in
    `__tests__/semantic-tx-aux-wiring.component.test.ts`). Bare in BOTH
    compositions and with no `data-zone-id`: `'txAux'` is merely declarable
    by a manifest after this slice; no manifest declares a txAux zone yet,
    and binding one here would put a zone id in the DOM that no layout
    asked for (the MOR-1069 lesson). `view?.txAux` (rather than the caller
    nesting this under `{#if view}`) keeps the same "never renders while
    view is null" behavior now that the surrounding structure changed
    around it (MOR-1258).
  -->
  <!--
    MOR-1336 — the ONE zone-aware mount path, applied uniformly to every
    optional surface that used to render bare. Declared → a real zone element
    the layout's arrangement can place; undeclared → bare, exactly as before.

    Deliberately NOT applied to `vfo`/`rxTx`: those carry per-receiver slicing,
    the `showVfoList`/`showRadioWideFacts` split and the R6 TX-adjacent alerts,
    none of which a uniform wrapper can express. Genericity is applied where it
    is honest; the two bespoke arrangements stay bespoke and are documented as
    such rather than forced through this path.

    `present` is the surface's OWN structural gate, hoisted to the wrap
    decision. Without it a declared zone would render as an empty `<div>` for a
    radio whose view model carries no such group — an "empty promise" zone,
    exactly what MOR-1069 forbids. The snippet bodies keep their own `{#if}`
    as well: one decides whether the zone exists, the other whether the surface
    does, and they must agree.
  -->
  {#snippet zoned(surface: SemanticSurfaceName, present: boolean, body: Snippet)}
    {#if present}
      {@const zoneId = zoneOwning(surface)}
      {#if zoneId === null}{@render body()}
      {:else}
        <div class="surface-zone" data-zone-id={zoneId}>{@render body()}</div>
      {/if}
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
    Bare and unzoned in BOTH compositions: `'meters'` becomes declarable by a
    manifest with this slice, but no manifest declares a meters zone, and the
    zone schema stays config-free (risk R3).

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

    UNLIKE those two it is rendered in the SINGLE composition ONLY. This is the
    first semantic surface that carries interactive controls no manifest
    declares a zone for, and the cockpit has a hard MOR-1069 rule: every
    focusable control lives inside a declared zone, and the rx-tx zone is LAST
    in the tab order. A control-bearing surface mounted bare would break both
    clauses at once, and the two alternatives are worse — binding a zone id no
    layout asked for is the MOR-1069 lesson itself, and folding the controls
    into the rx-tx zone would put an AF slider between the operator and the
    unkey button. `'rxAudio'` became DECLARABLE with this slice, so the cockpit
    gains the surface the moment its manifest declares a zone for it — a layout
    decision, separately reviewed, exactly as txAux and meters left it.

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

    UNLIKE `txAuxSurface`/`metersSurface` (and like `rxAudioSurface` above) it
    is rendered in the SINGLE composition ONLY (fix round, verify-MOR-1304 F1).
    `FilterSurface` renders up to 14 focusable controls (mode/filter/shape
    buttons, width and passband-level sliders) and no manifest declares a
    `filter` zone, so mounting it bare in the dual cockpit would put every one
    of those controls outside every declared zone and after the `rx-tx` zone
    that MOR-1069 requires to end the tab order — exactly the shape the
    MOR-1279/MOR-1336 zone-mount ruling forbids for any control-bearing
    surface. `'filter'` became DECLARABLE with this slice, so the cockpit
    gains the surface the moment a rework slice's manifest declares a zone for
    it — a layout decision, separately reviewed, exactly as rxAudio left it.
  -->
  {#snippet filterSurface()}
    {#if view?.modeFilter || view?.filterPassband}
      <FilterSurface
        {view}
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
    the surfaces above, and the SAME single-composition-only rule `rxAudio`
    carries: this surface renders focusable controls and no manifest declares
    an `antenna` zone, so mounting it in the dual composition would put
    controls outside every declared zone — the MOR-1069 invariant the cockpit
    enforces, and `zoned()` does NOT grant that permission on its own
    (`zoneOwning()` returns null for an undeclared surface and renders bare,
    which IS the violating shape). Its absence from the dual composition is
    pinned by name in `__tests__/semantic-antenna-wiring.component.test.ts`.
    `'antenna'` became DECLARABLE with this slice, so a layout gains the
    surface the moment its manifest declares a zone — a layout decision,
    separately reviewed, exactly as txAux/meters/rxAudio left it.

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
    rendered in the SINGLE composition ONLY (MOR-1304/MOR-1305 zone-mount
    ruling). `DspSurface` renders up to 8 range inputs and 7 buttons — it is
    control-bearing, and the cockpit's MOR-1069 rule forbids mounting any
    control-bearing surface bare in the dual composition: every focusable
    control must live inside a declared zone, with rx-tx last in the tab
    order. `'dsp'` became DECLARABLE with this slice, so the cockpit gains the
    surface the moment its manifest declares a zone for it — a layout
    decision, separately reviewed, exactly as rxAudio left it.

    `agcLabels`/`nbLevelMax`/`nbLevelPercent` are the caps-echo metadata
    carry-forward (1) requires stay OUT of the view model — read at this seam,
    from `runtime.caps`, and handed down as plain props.
  -->
  {#snippet dspSurface()}
    {#if view?.dsp}
      <DspSurface
        {view} {agcLabels} {nbLevelMax} {nbLevelPercent}
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

    SINGLE COMPOSITION ONLY — the MOR-1304 mounting canon (`RfFrontEndSurface.
    svelte`'s file header): this surface carries focusable controls (preamp
    and attenuator choice buttons, RF-gain/squelch sliders, DIGI-SEL/IP+
    toggles) and no shipped manifest declares an `rfFrontEnd` zone yet, so a
    bare dual mount would put controls outside every declared zone — exactly
    the defect the MOR-1279 rxAudio precedent avoided. `'rfFrontEnd'` became
    DECLARABLE with this slice; the cockpit gains the surface the moment a
    rework slice declares a zone for it, same as `rxAudio` left it.
  -->
  {#snippet rfFrontEndSurface()}
    {#if view?.rfFrontEnd}
      <RfFrontEndSurface
        {view}
        onPreampChange={(level) => rfFrontEndIntents.onPreChange(level)}
        onAttenuatorChange={(db) => rfFrontEndIntents.onAttChange(db)}
        onLevelChange={(field, value) => RF_FRONT_END_LEVEL_INTENT[field](value)}
        onToggle={(field, next) => RF_FRONT_END_TOGGLE_INTENT[field](next)}
      />
    {/if}
  {/snippet}

  <!--
    MOR-1307 (vocabulary slice 7B). Same structural gate as the surfaces above,
    and the SAME single-composition-only mounting as `rxAudioSurface`, for the
    same MOR-1069 reason: this surface is control-bearing (band buttons, a
    frequency entry field and its Set button) and no manifest declares a `band`
    zone, so a dual mount would put focusable controls outside every declared
    zone and break the cockpit's "tab order ends in rx-tx" invariant. `zoned()`
    does not grant that permission by itself — `zoneOwning()` answers `null` for
    an undeclared surface and the mount renders BARE, which is the violating
    shape. `'band'` becomes DECLARABLE with this slice; the cockpit gains the
    surface the moment a rework slice declares a zone for it, and the dual
    absence is pinned by name in
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
    the second semantic surface carrying interactive controls no manifest
    declares a zone for, so — per the MOR-1304 mounting canon — it mounts in
    the SINGLE composition only, and its dual-composition absence is pinned in
    `__tests__/semantic-ritxit-scan-wiring.component.test.ts` with a view
    model that actually carries the `ritXit`/`scan` groups (a fixture that
    cannot see the surface would repeat the bug that canon exists to catch).
    `'ritXitScan'` becomes DECLARABLE with this slice; no manifest declares a
    zone for it yet.
  -->
  {#snippet ritXitScanSurface()}
    {#if view?.ritXit || view?.scan}
      <RitXitScanSurface
        {view}
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
    the surfaces above, and the SAME single-composition-only mounting as
    `rxAudioSurface`: this surface is control-bearing, no manifest declares a
    `cwKeyer` zone, and the MOR-1069 cockpit rule is that every focusable
    control lives inside a declared zone with rx-tx last in the tab order.
    Mounted bare in the dual composition it would break both clauses; folded
    into the rx-tx zone it would put a keyer slider between the operator and
    the unkey button. `'cwKeyer'` became DECLARABLE with this slice, so the
    cockpit gains the surface the moment a manifest declares a zone — a layout
    decision, separately reviewed, exactly as rxAudio left it. Its absence from
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
        onBreakInMode={(mode) => cwIntents.onBreakInModeChange(mode)}
        onLevelChange={(field, value) => CW_LEVEL_INTENT[field](value)}
        onApfOn={(on) => cwIntents.onApfChange(on ? 1 : 0)}
        onTwinPeakToggle={() => cwIntents.onTwinPeakToggle()}
        onReversePaddleToggle={() => cwIntents.onReversePaddleToggle()}
      />
    {/if}
  {/snippet}

  <!--
    MOR-1312 (vocabulary slice 12B). Same structural gate and same reasoning
    as `txAuxSurface`/`metersSurface` above: the surface mounts only when the
    view model actually carries the MOR-1301 `scopeDisplay` group, so a radio
    with neither a hardware scope nor an audio-FFT source renders the
    pre-1312 element shape exactly. Bare and unzoned in BOTH compositions,
    the `meters`/`txAux` shape, NOT `rxAudio`'s single-only shape:
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
    above: control-bearing, no manifest declares a `scopeControls` zone, so
    per the MOR-1304 ruling's option (i) it mounts in the SINGLE composition
    only, bare, and must render NOTHING in the DUAL composition — pinned by
    name in `__tests__/semantic-scope-controls-wiring.component.test.ts`.
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
      TxAuxSurface stays OUTSIDE — it declares no zone (see above) — so it
      renders after the zone rather than between RxTxSurface and the alerts,
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
      {@render txAdjacentAlerts()}
    </div>
    {@render zoned('txAux', view?.txAux !== undefined, txAuxSurface)}
    {@render zoned('meters', view?.meters !== undefined, metersSurface)}
    {@render zoned('scopeDisplay', view?.scopeDisplay !== undefined, scopeDisplaySurface)}
  {:else}
    <!--
      Single/default path (sdr-test / LCD / mobile): no bound zone exists
      here (MOR-1069), so containment is not possible — the alerts keep
      their pre-MOR-1258 position and order, unchanged.

      MOR-1082: the layout's single zone mounts both `vfo` and `rxTx`
      (sdr-test `main`, LCD `control-column`, mobile `portrait-deck`), so this
      is where a per-zone reorder actually lands. `singleOrder` is the plan
      flattened in zone-declaration order and falls back to the composed order
      whenever no plan is resolved — an unresolved plan renders exactly the
      sequence this path renders today.
    -->
    {#each singleOrder as surface (surface)}
      {#if surface === 'vfo'}{@render vfoSurface()}
      {:else if surface === 'rxTx'}{@render rxTxSurface()}{/if}
    {/each}
    {@render zoned('txAux', view?.txAux !== undefined, txAuxSurface)}
    {@render zoned('meters', view?.meters !== undefined, metersSurface)}
    {@render zoned('rxAudio', view?.rxAudio !== undefined, rxAudioSurface)}
    {@render zoned('filter', view?.modeFilter !== undefined || view?.filterPassband !== undefined, filterSurface)}
    {@render zoned('dsp', view?.dsp !== undefined, dspSurface)}
    {@render zoned('rfFrontEnd', view?.rfFrontEnd !== undefined, rfFrontEndSurface)}
    {@render zoned('band', view?.band !== undefined, bandSurface)}
    {@render zoned('antenna', view?.antenna !== undefined, antennaSurface)}
    {@render zoned(
      'ritXitScan', view?.ritXit !== undefined || view?.scan !== undefined, ritXitScanSurface,
    )}
    {@render zoned('cwKeyer', view?.cwKeyer !== undefined, cwKeyerSurface)}
    {@render zoned('scopeDisplay', view?.scopeDisplay !== undefined, scopeDisplaySurface)}
    {@render zoned('scopeControls', view?.scopeControls !== undefined, scopeControlsSurface)}
    {@render txAdjacentAlerts()}
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
