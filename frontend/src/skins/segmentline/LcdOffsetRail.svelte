<script lang="ts">
  import type {
    PeerSplitDisplayModel,
    PeerSplitReceiverDisplay,
  } from '../../semantic/radio-display-model';
  import { formatOffset } from './lcd-display-helpers';

  interface Props {
    receiver: PeerSplitReceiverDisplay['receiver'];
    offsets: PeerSplitDisplayModel['offsets'];
  }

  let { receiver, offsets }: Props = $props();
</script>

<div class="offsets">
  {#each [
    { label: 'RIT', field: offsets.rit },
    { label: 'XIT', field: offsets.xit },
    { label: 'SPLIT', field: offsets.split },
  ] as item}
    <span
      class="offset {item.field.state}"
      data-testid={`lcd-offset-${receiver}-${item.label.toLowerCase()}`}
      data-state={item.field.state}
    >
      <span class="offset-label">{item.label}</span>
      <span class="offset-value">{formatOffset(item.field)}<small>kHz</small></span>
    </span>
  {/each}
</div>

<style>
  .offsets { display: flex; gap: 6px; align-items: stretch; }
  .offset {
    display: inline-flex;
    flex: 1 1 0;
    flex-direction: column;
    min-width: 0;
    padding: 2px 8px;
    border: 1px solid var(--ink-soft);
    border-radius: 2px;
    color: var(--ink-mid);
    line-height: 1.15;
  }
  .offset.active { border-color: var(--ink-mid); color: var(--ink-strong); }
  .offset.unknown { color: var(--ink-soft); }
  .offset.unsupported { visibility: hidden; }
  .offset-label { font-size: 10px; font-weight: 700; letter-spacing: 0.18em; }
  .offset-value { font-family: 'DSEG7 Classic', monospace; font-size: 16px; font-weight: 700; letter-spacing: 0.02em; }
  .offset:not(.active) .offset-value { opacity: 0.25; }
  .offset-value small { margin-left: 4px; color: var(--ink-mid); font-family: 'Share Tech Mono', monospace; font-size: 9px; }
</style>
