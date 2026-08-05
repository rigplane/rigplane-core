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
  import { onDestroy } from 'svelte';
  import { t } from '$lib/i18n';
  import { runtime } from '$lib/runtime';
  import { toRadioViewModel } from '$lib/runtime/adapters/radio-view-model-adapter';
  import { getAppTxController } from '$lib/runtime/tx-controller/app-host';
  import type { SemanticSurfaceName } from '../../presentation/layouts/contract';
  import {
    compositionSurfaces, useSurfacePlan, zoneShowsSurface,
  } from '../../presentation/workspace/resolution';
  import { LAN_MOD_INPUT_SOURCE } from '$lib/radio/mod-input';
  import MetersSurface from '../../semantic/MetersSurface.svelte';
  import type { RadioViewModel } from '../../semantic/radio-view-model';
  import RxAudioSurface from '../../semantic/RxAudioSurface.svelte';
  import RxTxSurface from '../../semantic/RxTxSurface.svelte';
  import TxAuxSurface, {
    type TxAuxLevelField, type TxAuxToggleField,
  } from '../../semantic/TxAuxSurface.svelte';
  import { keyBlockedReasons } from '../../semantic/rx-tx-surface';
  import VfoSurface, { type VfoSelection } from '../../semantic/VfoSurface.svelte';
  import ModInputTxWarning from '../panels/ModInputTxWarning.svelte';
  import {
    makeAudioRoutingHandlers, makeModeHandlers, makeRxAudioHandlers,
    makeTxHandlers, makeVfoHandlers, makeVoxHandlers,
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
  const setModInputLan = () => makeModeHandlers().onModInputChange(LAN_MOD_INPUT_SOURCE);
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

  // Belt-and-braces contract pin. The adapter now annotates its own return
  // type (MOR-1065 ruling 2), so this is the second of two compile-time links.
  // MOR-1262 slice 2A: the live authority snapshot is the THIRD argument — the
  // meter facts read their TX relevance from it and never from `state.ptt`
  // (invariant R9). Without it the adapter emits no `meters` group at all.
  // MOR-1279 slice 3B: the RX-audio snapshot is the FOURTH.
  let view: RadioViewModel | null = $derived(
    toRadioViewModel(runtime.state, runtime.caps, txState, rxAudioSnapshot),
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
          <VfoSurface
            viewModel={view}
            showVfoList={false}
            groupLabel={t('core.vfo.radioWideGroupLabel')}
            onToggleSplit={vfo.onSplitToggle}
            onToggleDualWatch={toggleDualWatch}
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
        onToggleSplit={vfo.onSplitToggle}
        onToggleDualWatch={toggleDualWatch}
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
    {@render txAuxSurface()}
    {@render metersSurface()}
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
    {@render txAuxSurface()}
    {@render metersSurface()}
    {@render rxAudioSurface()}
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

  .channel-strip[data-strip-active='true'] {
    border-left: 2px solid var(--v2-accent-cyan, #00d4ff);
    padding-left: 6px;
  }
</style>
