<script module lang="ts">
  import type { PeerSplitReceiverDisplay } from '../../semantic/radio-display-model';

  export interface LcdRfPanadapterFrame {
    readonly receiver: PeerSplitReceiverDisplay['receiver'];
    readonly freshness: 'fresh' | 'stale';
    readonly normalizedBins: readonly number[];
  }
</script>

<script lang="ts">
  interface Props {
    receiver: PeerSplitReceiverDisplay['receiver'] | null;
    frame?: LcdRfPanadapterFrame;
  }

  let { receiver, frame }: Props = $props();

  type FrameReason = 'live' | 'missing' | 'stale' | 'receiver-unknown'
    | 'receiver-mismatch' | 'invalid';

  const frameReason: FrameReason = $derived.by(() => {
    if (receiver === null) return 'receiver-unknown';
    if (frame === undefined) return 'missing';
    if (frame.freshness !== 'fresh') return 'stale';
    if (frame.receiver !== receiver) return 'receiver-mismatch';
    if (frame.normalizedBins.length < 2
      || !frame.normalizedBins.every((sample) => (
        Number.isFinite(sample) && sample >= 0 && sample <= 1
      ))) return 'invalid';
    return 'live';
  });

  const renderBins = $derived(frameReason === 'live' ? frame!.normalizedBins : []);
</script>

<svg
  data-testid="lcd-rf-panadapter"
  data-rf-mode={frameReason === 'live' ? 'live' : 'ghost'}
  data-frame-reason={frameReason}
  viewBox="0 0 600 120"
  preserveAspectRatio="none"
  aria-hidden="true"
>
  <rect class="rf-frame" x="0.5" y="0.5" width="599" height="119" />
  {#each [20, 40, 60, 80, 100] as y}
    <line class="rf-grid" x1="0" x2="600" y1={y} y2={y} />
  {/each}
  {#each [60, 120, 180, 240, 300, 360, 420, 480, 540] as x}
    <line class="rf-grid" x1={x} x2={x} y1="0" y2="120" />
  {/each}
  {#each renderBins as sample, index}
    {@const binWidth = 600 / renderBins.length}
    {@const binHeight = sample * 108}
    <rect
      class="rf-bin"
      data-rf-bin={index}
      data-rf-sample={sample}
      x={index * binWidth}
      y={116 - binHeight}
      width={Math.max(0.5, binWidth - 0.5)}
      height={binHeight}
    />
  {/each}
</svg>

<style>
  svg { display: block; width: 100%; height: 100%; min-height: 0; }
  .rf-frame { fill: none; stroke: var(--ink-soft); stroke-width: 1; }
  .rf-grid { stroke: var(--ink-ghost); stroke-width: 0.5; vector-effect: non-scaling-stroke; }
  .rf-bin { fill: var(--ink-mid); opacity: 0.86; }
  svg[data-rf-mode='ghost'] .rf-grid { stroke: var(--ink-ghost); opacity: 0.55; }
</style>
