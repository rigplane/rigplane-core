<script lang="ts">
  import type { PeerSplitDisplayModel } from '../../semantic/radio-display-model';
  import { SEGMENTLINE_SEGMENT_COUNT } from '../../presentation/languages/segmentline/meters-renderer';
  import { SEGMENTLINE_TOKENS } from '../../presentation/languages/segmentline/tokens';
  import { normalizePower, formatPowerWatts, swrLevel, formatSwr, alcLevel, formatAlc } from '../../components-v2/panels/meter-utils';
  import { telemetryText, telemetryDescription } from './lcd-display-helpers';

  let { power, swr, alc }: Pick<PeerSplitDisplayModel['telemetry'], 'power' | 'swr' | 'alc'> = $props();
  const count = SEGMENTLINE_SEGMENT_COUNT;
  const segmentWidth = Number.parseFloat(SEGMENTLINE_TOKENS.meters.trackWidth);
  const gap = Number.parseFloat(SEGMENTLINE_TOKENS.meters.segmentGap);
  const pitch = segmentWidth + gap;
  const width = count * pitch - gap;
  const items = $derived([
    { label: 'PWR', field: power, level: normalizePower, format: formatPowerWatts },
    { label: 'SWR', field: swr, level: swrLevel, format: formatSwr },
    { label: 'ALC', field: alc, level: alcLevel, format: formatAlc },
  ]);
</script>

<div class="lcd-tx-scales" data-testid="lcd-tx-scales">
  {#each items as item (item.label)}
    {@const tx = item.field.txDisplay}
    {#if tx?.supported}
      {@const readout = telemetryText(item.field, item.format)}
      {@const fraction = tx.relevance !== 'idle' && tx.observation.state === 'current' ? item.level(tx.observation.value) : 0}
      <div class="tx-scale" data-tx-scale={item.label} role="group"
        aria-label={telemetryDescription(item.label, item.field, item.format)}>
        <small>{item.label}</small> <span class="readout" class:long-readout={readout.length > 8}>{readout}</span>
        <svg viewBox={`0 0 ${width} 6`} preserveAspectRatio="none" aria-hidden="true">
          {#each Array(count) as _, index}
            {@const fill = Math.max(0, Math.min(1, fraction * count - index))}
            <rect data-tx-segment={index} x={index * pitch} y="0" width={segmentWidth} height="6" fill="var(--ink-ghost)" />
            {#if fill > 0}
              <rect data-tx-fill={index} x={index * pitch} y="0" width={segmentWidth * fill} height="6" fill="var(--ink-strong)" />
            {/if}
          {/each}
        </svg>
      </div>
    {/if}
  {/each}
</div>

<style>
  .lcd-tx-scales { display: grid; grid-template-columns: repeat(auto-fit, minmax(84px, 1fr)); gap: 2px; flex: 1; min-width: 0; max-width: 600px; grid-column: 1 / -1; }
  .tx-scale { display: grid; grid-template-columns: auto minmax(0, 1fr); grid-template-rows: 16px 6px; gap: 2px 0; min-width: 0; color: var(--ink-soft); font-size: 16px; line-height: 16px; }
  small { font: inherit; }
  .readout { text-align: right; white-space: nowrap; }
  .long-readout { font-size: 14px; }
  svg { display: block; width: 100%; height: 6px; grid-column: 1 / -1; outline: 1px solid var(--ink-ghost); }
</style>
