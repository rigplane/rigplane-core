<script lang="ts">
  export type LcdAfFftInputState = 'live' | 'missing' | 'stale' | 'unknown' | 'unsupported';

  interface Props {
    normalizedBins?: readonly number[] | null;
    inputState: LcdAfFftInputState;
    receiverActivity: 'active' | 'inactive' | 'unknown';
  }

  let { normalizedBins = null, inputState, receiverActivity }: Props = $props();

  const EMPTY_BINS = new Float32Array(64);
  let hasUsableInput = $derived(
    inputState === 'live'
      && receiverActivity === 'active'
      && normalizedBins !== null
      && normalizedBins.length > 1
      && normalizedBins.every(Number.isFinite),
  );
  let renderBins = $derived(hasUsableInput ? normalizedBins! : EMPTY_BINS);
  let path = $derived(Array.from(renderBins, (value, index) => {
    const x = (index / (renderBins.length - 1)) * 500;
    const y = 92 - Math.max(0, Math.min(1, value)) * 82;
    return `${index === 0 ? 'M' : 'L'}${x.toFixed(2)},${y.toFixed(2)}`;
  }).join(' '));
</script>

{#if inputState !== 'unsupported'}
  <svg
    class:ghosted={!hasUsableInput}
    data-testid="lcd-af-fft"
    data-fft-mode={hasUsableInput ? 'live' : 'safe-empty'}
    viewBox="0 0 500 100"
    preserveAspectRatio="none"
    aria-hidden="true"
  >
    {#each [25, 50, 75] as y}<line class="grid" x1="0" x2="500" y1={y} y2={y} />{/each}
    {#each [50, 100, 150, 200, 250, 300, 350, 400, 450] as x}
      <line class="grid" x1={x} x2={x} y1="0" y2="100" />
    {/each}
    <path class="fft-trace" d={path} fill="none" />
  </svg>
{/if}

<style>
  svg { width: 100%; height: 100%; min-height: 0; overflow: visible; }
  .grid { stroke: var(--ink-ghost); stroke-width: 0.5; }
  .fft-trace { stroke: var(--ink-strong); stroke-width: 1; vector-effect: non-scaling-stroke; }
  .ghosted .fft-trace { stroke: var(--ink-ghost); }
</style>
