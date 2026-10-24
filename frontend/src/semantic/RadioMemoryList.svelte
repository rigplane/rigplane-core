<!--
  The radio's own stored memory channels, read-only (MOR-3627).

  Presentation only: the list, its status and the recall callback arrive as
  props. Recall is an explicit click and changes what the radio is set to; this
  component never writes to the radio's memory.
-->
<script module lang="ts">
  import type { MemoryEntryDto } from '../lib/types/memory-list';

  export interface RadioMemoryListProps {
    status: 'idle' | 'loading' | 'ready' | 'error';
    error: string | null;
    entries: readonly MemoryEntryDto[];
    group: number | null;
    groups: { first: number; last: number } | null;
    recall: boolean;
    hasMore?: boolean;
    onGroupChange?: (group: number) => void;
    onReload?: () => void;
    onLoadMore?: () => void;
    onRecall?: (group: number, channel: number) => boolean;
  }
</script>

<script lang="ts">
  import { formatFrequencyString } from '../components-v2/display/frequency-format';
  import { formatShift, formatTone } from './memory-list-format';

  let { status, error, entries, group, groups, recall, hasMore = false, onGroupChange, onReload, onLoadMore, onRecall }: RadioMemoryListProps = $props();

  let stored = $derived(entries.filter((entry) => !entry.empty));
  let hasGroups = $derived(groups !== null && groups.last > groups.first);
  let recalled = $state<number | null>(null);

  function recallEntry(entry: MemoryEntryDto): void {
    if (onRecall?.(entry.group, entry.channel)) recalled = entry.channel;
  }
</script>

<section class="radio-memories" data-testid="radio-memories" aria-label="Radio memory channels">
  <div class="radio-memories-toolbar">
    <strong>Radio memories</strong>
    {#if hasGroups && group !== null && groups}
      <label>
        Group
        <input
          type="number" min={groups.first} max={groups.last} value={group}
          data-testid="radio-memories-group"
          onchange={(e) => onGroupChange?.(Number(e.currentTarget.value))}
        />
      </label>
    {/if}
    <button type="button" data-testid="radio-memories-reload" onclick={() => onReload?.()} disabled={status === 'loading'}>
      Reload
    </button>
    {#if hasMore}
      <button type="button" data-testid="radio-memories-more" onclick={() => onLoadMore?.()} disabled={status === 'loading'}>
        More
      </button>
    {/if}
    <span data-testid="radio-memories-status" data-status={status}>
      {#if status === 'loading'}Reading… {stored.length}{:else if status === 'error'}{error}{:else if status === 'ready'}{stored.length} stored{/if}
    </span>
  </div>
  <div class="radio-memories-list" role="list">
    {#each stored as entry (`${entry.group}/${entry.channel}`)}
      <div
        class="radio-memory-row" class:selected={entry.channel === recalled} role="listitem"
        data-testid={`radio-memory-${entry.channel}`}
      >
        <span class="rm-channel">{String(entry.channel).padStart(2, '0')}</span>
        <span class="rm-name">{entry.name}</span>
        <span class="rm-freq">{entry.frequencyHz === undefined ? '' : formatFrequencyString(entry.frequencyHz)}</span>
        <span class="rm-mode">{entry.mode}{entry.dataMode ? ' DATA' : ''}</span>
        <span class="rm-shift">{formatShift(entry)}</span>
        <span class="rm-tone">{formatTone(entry)}</span>
        <button
          type="button" class="rm-recall" title="Recall this channel on the radio"
          data-testid={`radio-memory-${entry.channel}-recall`}
          disabled={!recall || !onRecall}
          onclick={() => recallEntry(entry)}
        >
          Recall
        </button>
      </div>
    {/each}
  </div>
</section>

<style>
  /* Structure only — a design language owns colour (MOR-977, forced-colors).
     The memory rail is narrow (208-228px): rows and the toolbar wrap so Recall
     and the controls stay visible, and a long list scrolls inside the panel. */
  .radio-memories { display: flex; flex-direction: column; gap: 0.25rem; min-width: 0; }
  .radio-memories-toolbar { display: flex; flex-wrap: wrap; align-items: center; gap: 0.25rem 0.5rem; }
  .radio-memories-list { display: flex; flex-direction: column; max-height: 24rem; overflow-y: auto; }
  .radio-memory-row { display: flex; flex-wrap: wrap; align-items: center; gap: 0.125rem 0.375rem; font-variant-numeric: tabular-nums; }
  .rm-name { min-width: 8ch; overflow-wrap: anywhere; }
  .rm-recall { margin-left: auto; }
  button:disabled { cursor: not-allowed; }
</style>
