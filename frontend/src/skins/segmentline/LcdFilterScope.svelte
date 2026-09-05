<script lang="ts">
  import type { PeerSplitReceiverDisplay } from '../../semantic/radio-display-model';
  import LcdAfFft from './LcdAfFft.svelte';
  import {
    fftInputState,
    filterEnvelopes,
    formatBandwidth,
  } from './lcd-display-helpers';

  interface Props {
    receiver: PeerSplitReceiverDisplay;
    normalizedBins?: readonly number[];
  }

  let { receiver, normalizedBins }: Props = $props();

  const envelopes = $derived(filterEnvelopes(receiver));
</script>

<div class="scope-block" data-scope-state={receiver.spectrum}>
  <span class="scope-label" data-testid={`lcd-scope-label-${receiver.vfoSlot ?? receiver.receiver}`}>
    {receiver.spectrum === 'unsupported' ? 'BANDPASS' : 'AF SCOPE · BANDPASS'}
  </span>
  <div class="scope-plot">
    <LcdAfFft
      {normalizedBins}
      inputState={fftInputState(receiver, normalizedBins)}
      receiverActivity={receiver.activity}
    />
    <svg
      class="filter-overlay"
      data-testid="lcd-filter-envelope"
      viewBox="0 0 500 100"
      preserveAspectRatio="none"
      aria-label={`${receiver.vfoSlot ?? receiver.receiver} passive filter envelope`}
    >
      {#if envelopes.length > 0}
        {#each envelopes as item}
          <polyline class="filter-envelope {item.kind}" points={item.points} fill="none" />
        {/each}
        {@const centerX = envelopes.reduce((sum, item) => sum + item.centerX, 0) / envelopes.length}
        <line class="filter-center" x1={centerX} x2={centerX} y1="4" y2="12" />
        <text class="filter-label" x={centerX} y="16" text-anchor="middle">
          {formatBandwidth(receiver.bandwidthHz)} Hz{envelopes.length > 1 ? ' PBT' : ''}
        </text>
      {/if}
    </svg>
  </div>
</div>

<style>
  .scope-block { display: grid; grid-template-rows: 16px minmax(0, 1fr); min-height: 0; }
  .scope-label { color: var(--ink-mid); font-size: 11px; font-weight: 700; letter-spacing: 0.18em; }
  .scope-plot { position: relative; min-height: 0; }
  .scope-plot :global(.filter-overlay) { position: absolute; inset: 0; width: 100%; height: 100%; overflow: visible; }
  .filter-envelope { stroke: var(--ink-strong); stroke-width: 1.6; stroke-linejoin: miter; }
  .filter-envelope.inner { stroke-dasharray: 6 3; stroke-width: 1.4; }
  .filter-envelope.outer { stroke-dasharray: 0.1 4; stroke-linecap: round; stroke-width: 2.2; }
  .filter-center { stroke: var(--ink-strong); stroke-width: 1.2; }
  .filter-label, .scope-state { fill: var(--ink-strong); font-family: 'Share Tech Mono', monospace; font-size: 11px; font-weight: 700; }
</style>
