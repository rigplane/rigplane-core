<!--
  AmberMemoryStrip — compact aux-row widget showing the last 3 auto-QSY
  entries.

  MOR-2659: the M1-M5 memory cells are NOT drawn. The repo has no
  frontend memory store (the only "memory store" in the tree is the
  rig-side store-into-memory command flow in `MemorySurface.svelte`),
  and what the app does not have is not drawn — an unlit cell would
  pretend a readout exists. The component keeps its name and mount
  point; when a per-slot memory store lands, the cells come back with
  real data.

  QSY entries come from `$lib/stores/qsy-history` — a local ring
  buffer that debounces frequency changes into intentional QSYs
  (≥ 500 Hz delta, 1.5s stability). Each entry is a clickable chip
  that fires `onQsy(freqHz, mode)` so the parent can call into
  `runtime.send('set_freq', ...)`. An empty history renders an
  unlit, EMPTY placeholder in a reserved slot — never a dash
  (MOR-2659).

  Part of #836 / epic #818 LCD aux-row content.
-->
<script lang="ts">
  import { deriveQsyRecent } from '$lib/runtime/adapters/qsy-history-adapter';

  interface Props {
    /** Invoked when a recent-QSY chip is tapped. Parent routes to runtime. */
    onQsy?: (freqHz: number, mode: string) => void;
  }

  let { onQsy }: Props = $props();

  // Show last 3 entries, newest-first.
  let recentQsy = $derived<{ freqHz: number; mode: string; at: number }[]>(
    deriveQsyRecent().slice(-3).reverse() as { freqHz: number; mode: string; at: number }[],
  );

  function formatFreqShort(hz: number): string {
    if (!hz || hz <= 0) return '';
    // Compact: "14.074" for HF, "144.52" for VHF.
    const mhz = hz / 1_000_000;
    return mhz >= 1000
      ? `${(mhz / 1000).toFixed(3)}G`
      : mhz >= 100
        ? `${mhz.toFixed(3)}`
        : `${mhz.toFixed(3)}`;
  }

  function handleQsyClick(freqHz: number, mode: string) {
    onQsy?.(freqHz, mode);
  }
</script>

<div class="amber-memory-strip">
  <div class="section qsy-section" class:qsy-empty={recentQsy.length === 0}>
    <span class="section-tag">QSY</span>
    {#if recentQsy.length === 0}
      <span class="qsy-placeholder" aria-hidden="true"></span>
    {:else}
      {#each recentQsy as entry (entry.at)}
        <button
          type="button"
          class="slot slot-qsy"
          onclick={() => handleQsyClick(entry.freqHz, entry.mode)}
          title={`Return to ${formatFreqShort(entry.freqHz)} MHz ${entry.mode}`}
        >
          <span class="slot-value">{formatFreqShort(entry.freqHz)}</span>
          <span class="slot-mode">{entry.mode}</span>
        </button>
      {/each}
    {/if}
  </div>
</div>

<style>
  .amber-memory-strip {
    display: flex;
    gap: 10px;
    align-items: center;
    width: 100%;
    min-height: 18px;
    color: rgba(26, 16, 0, var(--lcd-alpha-active));
    font-family: 'JetBrains Mono', 'Courier New', monospace;
  }

  .section {
    display: flex;
    gap: 4px;
    align-items: center;
    min-width: 0;
  }

  .qsy-section {
    flex: 1;
    min-width: 0;
    overflow: hidden;
  }

  .qsy-empty {
    opacity: 0.45;
  }

  .section-tag {
    font-size: 9px;
    font-weight: 700;
    letter-spacing: 0.1em;
    color: rgba(26, 16, 0, calc(var(--lcd-alpha-active) * 0.55));
    flex-shrink: 0;
  }

  .qsy-placeholder {
    font-size: 10px;
    color: rgba(26, 16, 0, calc(var(--lcd-alpha-active) * 0.4));
    /* MOR-2659: the empty placeholder keeps a reserved, unlit slot so the
       first QSY chip cannot move the layout. */
    min-inline-size: 6ch;
    box-sizing: content-box;
  }

  .slot {
    display: inline-flex;
    align-items: baseline;
    gap: 3px;
    padding: 0 4px;
    height: 16px;
    border: 1px solid rgba(26, 16, 0, calc(var(--lcd-alpha-active) * 0.2));
    border-radius: 2px;
    background: transparent;
    color: inherit;
    font: inherit;
    cursor: pointer;
    white-space: nowrap;
  }

  .slot-qsy {
    border-color: rgba(26, 16, 0, calc(var(--lcd-alpha-active) * 0.4));
  }

  .slot-qsy:hover {
    border-color: rgba(26, 16, 0, calc(var(--lcd-alpha-active) * 0.65));
    background: rgba(26, 16, 0, var(--lcd-alpha-ghost));
  }

  .slot-value {
    font-size: 10px;
    font-weight: 700;
    letter-spacing: 0.02em;
  }

  .slot-mode {
    font-size: 8px;
    font-weight: 700;
    letter-spacing: 0.08em;
    color: rgba(26, 16, 0, calc(var(--lcd-alpha-active) * 0.55));
  }
</style>
