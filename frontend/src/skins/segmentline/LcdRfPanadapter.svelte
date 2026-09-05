<script module lang="ts">
  import type { LcdSpectrumFrame } from './lcd-display-contract';

  export type LcdRfPanadapterFrame = LcdSpectrumFrame;

  export interface LcdRfPanadapterPassband {
    readonly mode: string;
    readonly widthHz: number;
    readonly shiftHz: number;
  }
</script>

<script lang="ts">
  import { getPassbandGeometry } from '../../components/spectrum/passband-geometry';
  import { spectrumDisplayAmplitude } from '../../lib/renderers/spectrum-renderer';
  import type { PeerSplitReceiverDisplay } from '../../semantic/radio-display-model';
  import { resolveLcdSpectrumFrame } from './lcd-display-contract';

  interface Props {
    receiver: PeerSplitReceiverDisplay['receiver'] | null;
    frame?: unknown;
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

  let { receiver, frame, carrierHz, passband }: Props = $props();

  function formatFrequency(hz: number): string {
    const mhz = hz / 1_000_000;
    return mhz >= 1 ? mhz.toFixed(3) : `${Math.round(hz / 1_000)}k`;
  }

  const resolution = $derived(resolveLcdSpectrumFrame(frame, {
    source: 'hardware',
    receiver,
  }));
  const liveFrame = $derived(resolution.state === 'live' ? resolution.frame : null);
  const frameReason = $derived(resolution.state === 'live' ? 'live' : resolution.reason);
  const spanHz = $derived(liveFrame === null ? 0 : liveFrame.endHz - liveFrame.startHz);
  const renderBins = $derived(liveFrame?.normalizedBins ?? []);
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
    {#each renderBins as sample, index}
      {@const binWidth = PLOT_WIDTH / renderBins.length}
      {@const displaySample = spectrumDisplayAmplitude(sample * 255, 0)}
      {@const binHeight = displaySample * (PLOT_HEIGHT - 4)}
      <rect
        class="rf-bin"
        data-rf-bin={index}
        data-rf-sample={sample}
        x={index * binWidth}
        y={PLOT_BOTTOM - binHeight}
        width={Math.max(0.5, binWidth - 0.5)}
        height={binHeight}
      />
    {/each}
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
  .rf-bin { fill: var(--ink-mid); opacity: 0.86; }
  .rf-carrier {
    stroke: var(--ink-strong);
    stroke-width: 1.25;
    vector-effect: non-scaling-stroke;
  }
  svg[data-rf-mode='ghost'] .rf-frame { stroke: var(--ink-ghost); opacity: 0.55; }
</style>
