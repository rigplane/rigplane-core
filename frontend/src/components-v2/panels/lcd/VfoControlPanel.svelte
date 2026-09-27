<script lang="ts">
  /**
   * VfoControlPanel — sidebar soft-button panel for the LCD skin.
   *
   * Evicted from the LCD surface per issue #844 / plan §9.1 (P5):
   * the display surfaces state, not command entry. These buttons
   * (A↔B, A=B, DW, SPLIT, XIT, CLR, TUNE, BK-OFF) were previously
   * rendered inside `AmberLcdDisplay` (`.lcd-vfo-ctrl-row`) and are
   * relocated here unchanged. Commands dispatched via the same
   * adapter-bound handlers as before.
   */
  import {
    deriveVfoControlProps,
    deriveRitXitProps,
    getVfoHandlers,
    getRitXitHandlers,
    getCwHandlers,
    getTxHandlers,
    bindVfoTunerContext,
  } from '$lib/runtime/adapters/panel-adapters';
  import { deriveVfoOps } from '$lib/runtime/adapters/vfo-adapter';
  import { keyBlockedReasons } from '../../../semantic/rx-tx-surface';

  /**
   * MOR-1092: in the migrated LCD entrypoints the semantic VFO surface owns
   * the split and dual-watch facts (as tri-state switches that can render
   * "unknown"), so this panel suppresses its own copies — one fact, one
   * affordance, the same scoping as `hideTxPanel` (MOR-1065). The remaining
   * buttons have no semantic equivalent yet and are retained unchanged.
   */
  let { hideVfoFacts = false }: { hideVfoFacts?: boolean } = $props();

  const vfoHandlers = getVfoHandlers();
  const ritXitHandlers = getRitXitHandlers();
  const cwHandlers = getCwHandlers();
  const txHandlers = getTxHandlers();
  const tunerContext = bindVfoTunerContext();

  function requestAtuTune(): void {
    const { view, tx } = tunerContext.read();
    if (!view || keyBlockedReasons(view, tx).length > 0) return;
    txHandlers.onAtuTune();
  }

  let p = $derived(deriveVfoControlProps());
  let ritXit = $derived(deriveRitXitProps());
  let vfoOps = $derived(deriveVfoOps());
</script>

<div class="vfo-ctrl-panel">
  <button class="lcd-btn" onclick={vfoHandlers.onSwap}>A↔B</button>
  <button class="lcd-btn" onclick={vfoHandlers.onEqual}>A=B</button>
  {#if p.hasDualRx && !hideVfoFacts}
    <button class="lcd-btn" class:active={vfoOps.dualWatch} onclick={() => vfoHandlers.onDualWatchToggle(!vfoOps.dualWatch)}>DW</button>
  {/if}
  {#if p.hasSplit && !hideVfoFacts}
    <button class="lcd-btn" class:active={vfoOps.splitActive} onclick={vfoHandlers.onSplitToggle}>SPLIT</button>
  {/if}
  {#if p.hasRit}
    <button class="lcd-btn" class:active={ritXit.xitActive} onclick={ritXitHandlers.onXitToggle}>XIT</button>
    <button class="lcd-btn" onclick={ritXitHandlers.onClear}>CLR</button>
  {/if}
  {#if p.hasTuner}
    <button class="lcd-btn" onclick={requestAtuTune}>TUNE</button>
  {/if}
  <!-- MOR-2729: the break-in key cycles to the NEXT published value from
       the current one; `[]` (X6100, X6200) renders no key at all — the
       FTX-1 fix for the stuck-ON cycle. The key KEEPS ITS NAME ("BK") in
       every state: while break-in is unread (null) it is disabled and
       unlit, displaying exactly "BK", and once read it shows
       `BK-<published label>` — the operator always sees which function
       the key controls. The name is the visible text; a separate
       aria-label would only duplicate it. -->
  {#if p.isCwMode && p.hasCw && p.hasBreakIn && p.breakInChoices.length > 0}
    {@const choices = p.breakInChoices}
    {@const current = p.breakInMode}
    {@const next = current === null
      ? null
      : choices[(choices.findIndex((c) => c.value === current) + 1) % choices.length]}
    {@const label = current === null
      ? 'BK'
      : `BK-${choices.find((c) => c.value === current)?.label ?? ''}`}
    <!-- MOR-2729 (GLM-5.3 delta review): no `ch` reserve — a hidden sizer
         holds every text the key can show, stacked with the visible span
         in one grid cell, so the key is always as wide as its widest
         possible text. The sizer is aria-hidden: the accessible name stays
         exactly the visible text. -->
    <button
      class="lcd-btn lcd-btn-bk" class:active={current !== null && current > 0}
      disabled={current === null}
      onclick={() => { if (next !== null) cwHandlers.onBreakInModeChange(next.value); }}
    >
      <span class="lcd-btn-bk-sizer" aria-hidden="true">
        <span>BK</span>
        {#each choices as choice}
          <span>BK-{choice.label}</span>
        {/each}
      </span>
      <span class="lcd-btn-bk-text">{label}</span>
    </button>
  {/if}
</div>

<style>
  .vfo-ctrl-panel {
    display: flex;
    flex-wrap: wrap;
    gap: 6px;
    padding: 8px;
    border: 1px solid var(--v2-border-panel);
    border-radius: 4px;
    background:
      linear-gradient(180deg, var(--v2-panel-bg-gradient-top) 0%, var(--v2-panel-bg-gradient-bottom) 100%);
    box-shadow: var(--v2-shadow-sm);
    box-sizing: border-box;
  }

  .lcd-btn {
    font-family: 'JetBrains Mono', 'Courier New', monospace;
    font-size: 11px;
    font-weight: 700;
    letter-spacing: 0.5px;
    color: var(--v2-text-dim);
    background: transparent;
    border: 1.5px solid var(--v2-border-panel);
    border-radius: 3px;
    padding: 3px 8px;
    cursor: pointer;
    user-select: none;
  }
  .lcd-btn:hover {
    color: var(--v2-text);
    border-color: var(--v2-border);
  }
  .lcd-btn:active,
  .lcd-btn.active {
    color: var(--v2-text);
    border-color: var(--v2-accent, var(--v2-border));
  }
  /* MOR-2729: the BK key's width comes from the hidden sizer above —
     every possible text stacked in one grid cell, so the layout never
     moves when the first reading arrives. */
  .lcd-btn-bk {
    display: inline-grid;
    text-align: center;
  }
  .lcd-btn-bk > span {
    grid-area: 1 / 1;
  }
  .lcd-btn-bk-sizer {
    visibility: hidden;
    display: grid;
  }
  .lcd-btn-bk-sizer > span {
    grid-area: 1 / 1;
  }
</style>
