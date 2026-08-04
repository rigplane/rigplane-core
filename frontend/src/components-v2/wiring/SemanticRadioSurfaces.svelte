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
  import type { RadioViewModel } from '../../semantic/radio-view-model';
  import RxTxSurface from '../../semantic/RxTxSurface.svelte';
  import VfoSurface, { type VfoSelection } from '../../semantic/VfoSurface.svelte';
  import ModInputTxWarning from '../panels/ModInputTxWarning.svelte';
  import { makeVfoHandlers } from './command-bus';
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

  const vfo = makeVfoHandlers();
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

  // Belt-and-braces contract pin. The adapter now annotates its own return
  // type (MOR-1065 ruling 2), so this is the second of two compile-time links.
  let view: RadioViewModel | null = $derived(toRadioViewModel(runtime.state, runtime.caps));

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
        {#each receiversOf(view) as receiverId, index (receiverId)}
          <!--
            `data-zone-id`: `ReceiverId` is `'MAIN' | 'SUB'`, so the index is
            total over the manifest's two per-receiver zones. A degraded
            single-receiver view model renders `primary-vfo` and NO
            `secondary-vfo` — an absent zone, never an empty promise.
          -->
          <div
            class="channel-strip"
            data-testid={`channel-strip-${receiverId}`}
            data-zone-id={index === 0 ? 'primary-vfo' : 'secondary-vfo'}
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
      <div class="cockpit-global-row" data-testid="cockpit-zone-global" data-zone-id="global">
        <VfoSurface
          viewModel={view}
          showVfoList={false}
          groupLabel={t('core.vfo.radioWideGroupLabel')}
          onToggleSplit={vfo.onSplitToggle}
          onToggleDualWatch={toggleDualWatch}
        />
      </div>
    {:else}
      <VfoSurface
        viewModel={view}
        onSelectVfo={selectVfo}
        onToggleSplit={vfo.onSplitToggle}
        onToggleDualWatch={toggleDualWatch}
      />
    {/if}
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
    -->
    {#snippet rxTxSurface()}
      <RxTxSurface {view} tx={txState} onRequestKey={requestKey} onRequestUnkey={requestUnkey} />
    {/snippet}
    {#if strips === 'dual'}
      <div class="rx-tx-zone" data-zone-id="rx-tx">{@render rxTxSurface()}</div>
    {:else}
      {@render rxTxSurface()}
    {/if}
  {/if}

  <!--
    MOR-617 network-voice-TX preflight. It ships inside TxPanel, which this
    layout suppresses (`hideTxPanel`) — but this layout can now key network
    voice TX, and the controller's `start-audio` effect IS that path. Without
    the banner the operator keys with a mis-routed MOD input, no warning and no
    one-click "Set LAN" fix: the exact shape of the "web voice TX = noise" bug.
    Self-gating on `deriveModInputTxGuardProps().visible`, so the trigger
    conditions are byte-identical to the panel's.
  -->
  <ModInputTxWarning />

  {#if txState.phase === 'failed'}
    <button
      type="button" class="tx-fault-reset" data-testid="tx-fault-reset" onclick={clearFault}
    >Clear TX fault</button>
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
     place it. It carries no presentation of its own; the surface inside owns
     that. */

  .channel-strip[data-strip-active='true'] {
    border-left: 2px solid var(--v2-accent-cyan, #00d4ff);
    padding-left: 6px;
  }
</style>
