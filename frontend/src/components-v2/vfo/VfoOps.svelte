<script lang="ts">
  import '../controls/control-button.css';
  import { hasDualReceiver, getVfoScheme } from '$lib/stores/capabilities.svelte';
  import { vfoSwapLabel, vfoCopyLabel, vfoTxLabel } from './vfo-ops-utils';
  import ActiveReceiverToggle from './ActiveReceiverToggle.svelte';

  interface Props {
    splitActive: boolean;
    dualWatchActive: boolean;
    /** Read-only indicator: which receiver transmits right now.
     *  Derived from split flag — see `toVfoOpsProps`. */
    txVfo: 'main' | 'sub';
    /** Currently active receiver (MAIN or SUB).  Drives segmented toggle. */
    activeVfo?: 'MAIN' | 'SUB';
    /** Fired when user selects a different receiver via the segmented toggle. */
    onActiveVfoChange?: (next: 'MAIN' | 'SUB') => void;
    onSwap: () => void;
    /** Equalize MAIN→SUB (copy). Backend sends `0x07 0xB1`. */
    onEqual: () => void;
    /** Toggle SPLIT on/off. */
    onSplitToggle: () => void;
    /** Toggle Dual Watch on/off. */
    onDualWatchToggle: () => void;
  }

  let {
    splitActive,
    dualWatchActive,
    txVfo,
    activeVfo = 'MAIN',
    onActiveVfoChange,
    onSwap,
    onEqual,
    onSplitToggle,
    onDualWatchToggle,
  }: Props = $props();

  let scheme = $derived(getVfoScheme());
  let swapLabel = $derived(vfoSwapLabel(scheme));
  let copyLabel = $derived(vfoCopyLabel(scheme));
  let txBadgeLabel = $derived(vfoTxLabel(scheme, txVfo));
  let dualRx = $derived(hasDualReceiver());

</script>

<div class:dual={dualRx} class="vfo-ops">
  {#if dualRx}
    <div class="active-receiver-slot">
      <ActiveReceiverToggle
        active={activeVfo}
        onChange={(next) => onActiveVfoChange?.(next)}
      />
    </div>
  {/if}
  <button
    type="button"
    class="bridge-button v2-control-button"
    data-op="copy"
    data-active="false"
    data-color="muted"
    onclick={onEqual}
    title="Equalize — copy MAIN to SUB"
  >{copyLabel}</button>
  <button
    type="button"
    class="bridge-button v2-control-button"
    data-op="split"
    data-active={splitActive}
    data-color="cyan"
    onclick={onSplitToggle}
    title="Toggle SPLIT"
  >SPLIT</button>
  {#if dualRx}
    <button
      type="button"
      class="bridge-button v2-control-button"
      data-op="dw"
      data-active={dualWatchActive}
      data-color="green"
      onclick={onDualWatchToggle}
      title="Toggle DUAL WATCH"
    >DW</button>
  {/if}
  <button
    type="button"
    class="bridge-button v2-control-button"
    data-op="swap"
    data-active="false"
    data-color="muted"
    onclick={onSwap}
    title="Exchange — swap MAIN and SUB"
  >{swapLabel}</button>
  {#if dualRx}
    <!-- Read-only TX indicator; derives from split flag via state adapter. -->
    <div
      class="tx-indicator"
      data-testid="tx-indicator"
      data-tx={txVfo}
      aria-live="polite"
      title="Transmit receiver (read-only; follows SPLIT flag)"
    >{txBadgeLabel}</div>
  {/if}
</div>

<style>
  .vfo-ops {
    display: grid;
    grid-template-columns: repeat(2, minmax(0, 1fr));
    align-items: center;
    justify-items: stretch;
    gap: var(--vfo-ops-gap, 4px);
    padding: var(--vfo-ops-padding-y, 4px) 0;
    font-family: 'Roboto Mono', monospace;
  }

  .bridge-button {
    width: 100%;
    min-width: var(--vfo-ops-badge-width, 56px);
    min-height: var(--vfo-ops-badge-height, 18px);
    padding: 4px var(--vfo-ops-badge-padding-x, 6px);
    border-radius: var(--vfo-ops-badge-radius, 4px);
    font-size: var(--vfo-ops-badge-font-size, 10px);
    box-sizing: border-box;
  }

  .bridge-button[data-color='cyan'] {
    --control-accent: var(--v2-accent-cyan);
    --control-active-text: var(--v2-text-bright);
  }

  .bridge-button[data-color='green'] {
    --control-accent: var(--v2-accent-green-bright);
    --control-active-text: var(--v2-text-bright);
  }

  .tx-indicator {
    grid-column: 1 / -1;
    display: flex;
    align-items: center;
    justify-content: center;
    min-height: var(--vfo-ops-badge-height, 18px);
    padding: 4px var(--vfo-ops-badge-padding-x, 6px);
    border-radius: var(--vfo-ops-badge-radius, 4px);
    font-size: var(--vfo-ops-badge-font-size, 10px);
    font-weight: 600;
    color: var(--v2-accent-orange-alt, #ffa500);
    background: var(--v2-surface-muted, rgba(255, 255, 255, 0.04));
    border: 1px solid var(--v2-border-muted, rgba(255, 255, 255, 0.08));
    user-select: none;
    cursor: default;
  }

  .vfo-ops:not(.dual) {
    grid-auto-flow: row;
  }

  /* Dual-rx layout:
     Row 1: ACTIVE-RX toggle [M|S] (span 2)
     Row 2: COPY   | SPLIT
     Row 3: DW     | SWAP
     Row 4: TX-indicator (span 2) */
  .vfo-ops.dual .active-receiver-slot { grid-column: 1 / -1; grid-row: 1; }
  .vfo-ops.dual .bridge-button[data-op='copy']  { grid-column: 1; grid-row: 2; }
  .vfo-ops.dual .bridge-button[data-op='split'] { grid-column: 2; grid-row: 2; }
  .vfo-ops.dual .bridge-button[data-op='dw']    { grid-column: 1; grid-row: 3; }
  .vfo-ops.dual .bridge-button[data-op='swap']  { grid-column: 2; grid-row: 3; }
  .vfo-ops.dual .tx-indicator { grid-row: 4; }
</style>
