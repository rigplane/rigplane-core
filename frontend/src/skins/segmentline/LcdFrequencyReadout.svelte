<script lang="ts">
  import {
    DIGIT_CELL_EM,
    DOT_CELL_EM,
    renderFrequency,
  } from '../../presentation/languages/segmentline/frequency-renderer';
  import { SEGMENTLINE_TOKENS } from '../../presentation/languages/segmentline/tokens';
  import type {
    DisplayValue,
    DisplaySlotId,
  } from '../../semantic/radio-display-model';

  interface Props {
    receiver: DisplaySlotId;
    field: DisplayValue<number>;
  }

  let { receiver, field }: Props = $props();

  const frequency = $derived(renderFrequency(
    { kind: 'frequency', fields: { frequencyHz: field.state === 'known' ? field.value : null } },
    SEGMENTLINE_TOKENS,
  ));
</script>

<div class="frequency" data-testid={`lcd-frequency-${receiver}`} data-state={field.state}>
  {#if frequency.groups.length > 0}
    {#each frequency.groups as group}
      <span class:ranked={group.rank === 'ranked'} class="frequency-group">
        {#each group.cells as cell}
          <span
            class:separator={cell.isSeparator}
            class="frequency-cell"
            style:width={`${cell.isSeparator ? DOT_CELL_EM : DIGIT_CELL_EM}em`}
          >{cell.char}</span>
        {/each}
      </span>
    {/each}
  {:else}
    <span class="frequency-unknown">—.———.———</span>
  {/if}
</div>

<style>
  .frequency {
    display: inline-flex;
    align-items: baseline;
    align-self: start;
    width: max-content;
    max-width: 100%;
    overflow: hidden;
    font-family: 'DSEG7 Classic', 'Share Tech Mono', ui-monospace, monospace;
    font-size: 78px;
    font-weight: 700;
    letter-spacing: 0.02em;
    line-height: 1;
    white-space: nowrap;
  }
  .frequency-group { display: inline-flex; align-items: baseline; font-size: 1em; }
  .frequency-group.ranked { color: var(--ink-mid); font-size: 62%; }
  .frequency-cell { display: inline-block; flex: 0 0 auto; text-align: center; }
  .frequency-unknown { color: var(--ink-soft); }
  .frequency[data-state='unknown'] { opacity: 0.34; }
  .frequency[data-state='unsupported'] { visibility: hidden; }
</style>
