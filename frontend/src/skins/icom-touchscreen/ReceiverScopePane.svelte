<script lang="ts">
  import { tick } from 'svelte';
  import type {
    DisplayFact,
    ReceiverScopeDisplay,
  } from '../../semantic/icom-touchscreen-display-model';
  import {
    defaultSpectrumOptions,
    renderSpectrum,
  } from '../../lib/renderers/spectrum-renderer';

  interface Props {
    receiver: 'MAIN' | 'SUB';
    state: DisplayFact<ReceiverScopeDisplay>;
  }

  let { receiver, state }: Props = $props();
  let canvas: HTMLCanvasElement;

  $effect(() => {
    const next = state;
    void tick().then(() => {
      if (!canvas) return;
      const context = canvas.getContext('2d');
      if (!context) return;
      const width = Math.max(1, Math.floor(canvas.clientWidth));
      const height = Math.max(1, Math.floor(canvas.clientHeight));
      const dpr = window.devicePixelRatio || 1;
      canvas.width = Math.round(width * dpr);
      canvas.height = Math.round(height * dpr);
      context.setTransform(dpr, 0, 0, dpr, 0, 0);
      context.clearRect(0, 0, width, height);
      if (next.status !== 'known') return;

      renderSpectrum(context, next.value.pixels, width, height, {
        ...defaultSpectrumOptions,
        bgColor: '#020707',
        lineColor: '#c9e6df',
        fillColor: 'rgba(35, 152, 164, 0.24)',
        gridColor: 'rgba(116, 151, 151, 0.2)',
        textColor: 'rgba(202, 221, 218, 0.68)',
        // A frame carries range bounds but no confirmed tune/mode fact.
        // Keep the shared renderer's carrier/passband overlay disabled;
        // backed start/end labels are rendered separately below.
        spanHz: 0,
        centerHz: 0,
      });
    });
  });

  function formatHz(value: number): string {
    return `${(value / 1_000_000).toFixed(3)} MHz`;
  }
</script>

<section
  class="scope-pane"
  aria-label={`${receiver} receiver spectrum`}
  data-scope-receiver={receiver}
  data-scope-state={state.status}
>
  <canvas bind:this={canvas} data-supplied-pixels={state.value?.pixels.length ?? 0}></canvas>
  {#if state.status !== 'known'}
    <output data-scope-empty>
      {state.status === 'unsupported' ? 'scope unsupported' : state.status === 'inactive' ? 'scope inactive' : 'scope unavailable'}
    </output>
  {:else if state.value.startHz !== undefined && state.value.endHz !== undefined}
    <div class="axis" data-scope-axis>
      <span>{formatHz(state.value.startHz)}</span>
      <span>{formatHz(state.value.endHz)}</span>
    </div>
  {/if}
</section>

<style>
  .scope-pane {
    position: relative;
    min-width: 0;
    min-height: 11rem;
    overflow: hidden;
    border: 1px solid #526465;
    background: #020707;
  }

  canvas {
    display: block;
    width: 100%;
    height: 100%;
    min-height: 11rem;
  }

  output {
    position: absolute;
    inset: 0;
    display: grid;
    place-items: center;
    color: #849594;
    background: #020707;
    font: 0.78rem ui-monospace, monospace;
    letter-spacing: 0.08em;
    text-transform: uppercase;
  }

  .axis {
    position: absolute;
    right: 0.35rem;
    bottom: 0.2rem;
    left: 0.35rem;
    display: flex;
    justify-content: space-between;
    color: #82918f;
    font: 0.58rem ui-monospace, monospace;
    pointer-events: none;
  }
</style>
