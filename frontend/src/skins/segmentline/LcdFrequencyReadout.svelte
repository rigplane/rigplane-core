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

  const frequency = $derived(field.state === 'known' ? renderFrequency(
    { kind: 'frequency', fields: { frequencyHz: field.value } },
    SEGMENTLINE_TOKENS,
  ) : null);

  // MOR-2650: the unread shape is derived from ONE known rendering, so the
  // empty cells keep the known layout's group/cell count and cell widths.
  // The donor is an 8-digit value on purpose: it renders the 2+3+3 digit
  // grid ('10.000.000' shape) the fixtures and tests exercise — the digits
  // are blanked, so it is a shape donor only, never a displayed value.
  // (A ≥100 MHz reading renders the wider 3+3+3 grid exactly as today —
  // `.frequency` is max-content in both states, unchanged by this ticket.)
  const unreadGroups = $derived.by(() => renderFrequency(
    { kind: 'frequency', fields: { frequencyHz: 10_000_000 } },
    SEGMENTLINE_TOKENS,
  ).groups.map((group) => ({
    rank: group.rank,
    cells: group.cells.map((cell) => (
      cell.isSeparator ? cell : { ...cell, char: '' }
    )),
  })));

  const readoutLabel = $derived(`Frequency ${receiver}`);

</script>

<div
  class="frequency"
  data-testid={`lcd-frequency-${receiver}`}
  data-state={field.state}
  role={frequency ? undefined : 'img'}
  aria-label={frequency ? undefined : readoutLabel}
>
  {#if frequency}
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
    {#each unreadGroups as group}
      <span class:ranked={group.rank === 'ranked'} class="frequency-group" aria-hidden="true">
        {#each group.cells as cell}
          <span
            class:separator={cell.isSeparator}
            class="frequency-cell"
            style:width={`${cell.isSeparator ? DOT_CELL_EM : DIGIT_CELL_EM}em`}
          >{cell.char}</span>
        {/each}
      </span>
    {/each}
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
  .frequency[data-state='unknown'] { opacity: 0.34; }
  .frequency[data-state='unsupported'] { visibility: hidden; }
</style>
