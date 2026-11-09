<script module lang="ts">
  export interface LcdRfPanadapterPassband {
    readonly mode: string;
    readonly widthHz: number;
    readonly shiftHz: number;
  }
</script>

<script lang="ts">
  import { untrack } from 'svelte';
  import { CanvasSurface } from '$lib/canvas/canvas-surface.svelte';
  import { getStageScale } from '../../primitives/stage/stage-scale';
  import { getPassbandGeometry } from '../../components/spectrum/passband-geometry';
  import { spectrumDisplayAmplitude } from '../../lib/renderers/spectrum-renderer';
  import type { PeerSplitReceiverDisplay } from '../../semantic/radio-display-model';
  import { scopeSubscriptionSurface } from '$lib/runtime/scope-view';
  import type { ScopeStream } from '$lib/runtime/scope-stream';
  import type { ScopeGeometry } from '$lib/runtime/scope-frame';
  import { resolveLcdSpectrumFrame } from './lcd-display-contract';

  interface Props {
    receiver: PeerSplitReceiverDisplay['receiver'] | null;
    scopeStream?: ScopeStream | null;
    carrierHz?: number;
    passband?: LcdRfPanadapterPassband;
  }

  interface AxisTick {
    readonly frequencyHz: number;
    readonly x: number;
    readonly label: string;
  }

  const PLOT_WIDTH = 600;
  const PLOT_TOP = 18;
  const PLOT_BOTTOM = 116;
  const PLOT_HEIGHT = PLOT_BOTTOM - PLOT_TOP;

  let { receiver, scopeStream = null, carrierHz, passband }: Props = $props();
  let surfaceElement: SVGSVGElement;
  const surface = new CanvasSurface({ stageScale: getStageScale() });
  $effect(() => untrack(() => surface.attach(surfaceElement)));
  let geometry = $state.raw<ScopeGeometry | null>(null);
  let bars = $state<SVGGElement>();
  let pixels: Uint8Array | null = null;

  function formatFrequency(hz: number): string {
    const mhz = hz / 1_000_000;
    return mhz >= 1 ? mhz.toFixed(3) : `${Math.round(hz / 1_000)}k`;
  }

  const resolution = $derived(resolveLcdSpectrumFrame(geometry, { source: 'hardware', receiver }));
  const frameReason = $derived(resolution.state === 'ghost' ? resolution.reason : resolution.state);
  const liveFrame = $derived(frameReason === 'live' && geometry !== null
    ? { startHz: geometry.view.startHz, endHz: geometry.view.endHz } : null);
  const spanHz = $derived(liveFrame === null ? 0 : liveFrame.endHz - liveFrame.startHz);
  function paint() {
    if (!bars) return;
    bars.replaceChildren();
    if (liveFrame === null || pixels === null || pixels.length < 2) return;
    for (let index = 0; index < pixels.length; index += 1) {
      const sample = pixels[index];
      const width = PLOT_WIDTH / pixels.length;
      const height = spectrumDisplayAmplitude(sample, 0) * (PLOT_HEIGHT - 4);
      const node = document.createElementNS('http://www.w3.org/2000/svg', 'rect');
      for (const [key, value] of Object.entries({ class: 'rf-bin', 'data-rf-bin': index,
        'data-rf-sample': sample, x: index * width, y: PLOT_BOTTOM - height,
        width: Math.max(0.5, width - 0.5), height })) node.setAttribute(key, String(value));
      bars.append(node);
    }
  }
  $effect(() => {
    const stream = scopeStream;
    pixels = null; geometry = null;
    if (!stream) return;
    const stopGeometry = stream.geometry.subscribe(value => { geometry = value; });
    const stopFrames = stream.subscribe((frame, slot) => {
      if (slot !== stream.selectedSlot) return;
      pixels = frame.pixels;
      paint();
    });
    return () => { stopFrames(); stopGeometry(); pixels = null; };
  });
  $effect(() => {
    void liveFrame;
    const slot = scopeStream?.selectedSlot;
    pixels = slot ? scopeStream?.latest(slot)?.pixels ?? null : null;
    paint();
  });
  const axisTicks: readonly AxisTick[] = $derived.by(() => {
    if (liveFrame === null) return [];
    return [0, 0.25, 0.5, 0.75, 1].map((ratio) => {
      const frequencyHz = liveFrame.startHz + spanHz * ratio;
      return { frequencyHz, x: PLOT_WIDTH * ratio, label: formatFrequency(frequencyHz) };
    });
  });
  const carrierX = $derived.by(() => {
    if (liveFrame === null || carrierHz === undefined || !Number.isFinite(carrierHz)
      || carrierHz < liveFrame.startHz || carrierHz > liveFrame.endHz) return null;
    return ((carrierHz - liveFrame.startHz) / spanHz) * PLOT_WIDTH;
  });
  const passbandGeometry = $derived.by(() => {
    if (carrierX === null || passband === undefined
      || !Number.isFinite(passband.widthHz) || passband.widthHz <= 0
      || !Number.isFinite(passband.shiftHz)) return null;
    return getPassbandGeometry(
      passband.mode,
      passband.widthHz,
      passband.shiftHz,
      spanHz,
      PLOT_WIDTH,
      carrierX,
    );
  });
</script>

<svg
  bind:this={surfaceElement}
  use:scopeSubscriptionSurface={{ stream: scopeStream, receiver: receiver === 'SUB' ? 1 : 0,
    view: { follow: 'vfo', spanHz: 100000 }, facts: surface.facts }}
  data-testid="lcd-rf-panadapter"
  data-rf-mode={liveFrame === null ? 'ghost' : 'live'}
  data-frame-reason={frameReason}
  data-start-hz={liveFrame?.startHz}
  data-end-hz={liveFrame?.endHz}
  viewBox="0 0 600 132"
  preserveAspectRatio="none"
  aria-hidden="true"
>
  <rect class="rf-frame" x="0.5" y="0.5" width="599" height="131" />
  <g bind:this={bars}></g>
  {#if liveFrame !== null}
    {#each axisTicks as tick}
      <g class="rf-axis" data-axis-frequency={tick.frequencyHz}>
        <line class="rf-grid" x1={tick.x} x2={tick.x} y1={PLOT_TOP} y2={PLOT_BOTTOM} />
        <text
          class="rf-axis-label"
          x={tick.x}
          y="12"
          text-anchor={tick.x === 0 ? 'start' : tick.x === PLOT_WIDTH ? 'end' : 'middle'}
        >{tick.label}</text>
      </g>
    {/each}
    {#each [0.25, 0.5, 0.75] as ratio}
      <line
        class="rf-grid"
        x1="0"
        x2={PLOT_WIDTH}
        y1={PLOT_TOP + PLOT_HEIGHT * ratio}
        y2={PLOT_TOP + PLOT_HEIGHT * ratio}
      />
    {/each}
    {#if passbandGeometry !== null && passbandGeometry.widthPx > 0}
      <rect
        class="rf-passband"
        data-passband-mode={passband?.mode}
        data-passband-width-hz={passband?.widthHz}
        data-passband-shift-hz={passband?.shiftHz}
        x={passbandGeometry.leftPx}
        y={PLOT_TOP}
        width={passbandGeometry.widthPx}
        height={PLOT_HEIGHT}
      />
    {/if}
    {#if carrierX !== null}
      <line
        class="rf-carrier"
        data-carrier-hz={carrierHz}
        x1={carrierX}
        x2={carrierX}
        y1={PLOT_TOP}
        y2={PLOT_BOTTOM}
      />
    {/if}
  {/if}
</svg>

<style>
  svg { display: block; width: 100%; height: 100%; min-height: 0; }
  .rf-frame { fill: none; stroke: var(--ink-soft); stroke-width: 1; }
  .rf-grid { stroke: var(--ink-ghost); stroke-width: 0.5; vector-effect: non-scaling-stroke; }
  .rf-axis-label {
    fill: var(--ink-mid);
    font-family: 'Share Tech Mono', ui-monospace, monospace;
    font-size: 8px;
    font-variant-numeric: tabular-nums;
  }
  .rf-passband { fill: var(--ink-soft); opacity: 0.22; }
  svg :global(.rf-bin) { fill: var(--ink-mid); opacity: 0.86; }
  .rf-carrier {
    stroke: var(--ink-strong);
    stroke-width: 1.25;
    vector-effect: non-scaling-stroke;
  }
  svg[data-rf-mode='ghost'] .rf-frame { stroke: var(--ink-ghost); opacity: 0.55; }
</style>
