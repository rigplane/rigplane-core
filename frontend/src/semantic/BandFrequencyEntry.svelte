<script lang="ts">
  import type {
    FrequencyEntryRendererLease,
  } from '../primitives/frequency/frequency-entry-renderer.svelte';

  let { lease }: { lease: FrequencyEntryRendererLease } = $props();
  let entry = $derived(lease.view);
</script>

{#if entry}
  <label class="band-row" data-testid="band-entry" data-bounds={entry.boundsAvailable}
    data-validation={entry.validation}>
    <span class="band-name">{entry.label}</span>
    <input
      type="text" inputmode="decimal" data-testid="band-entry-input" data-freq-entry
      value={entry.draft} disabled={!entry.inputAvailable}
      oninput={(event) => lease.setDraft(event.currentTarget.value)}
      onkeydown={(event) => lease.handleKeyDown(event)}
    />
    {#if entry.hint}<span data-testid="band-entry-hint">{entry.hint}</span>{/if}
    <span data-testid="band-entry-range">{entry.rangeText}</span>
    <button type="button" data-testid="band-entry-set" disabled={!entry.submitAvailable}
      onclick={() => lease.submit()}>Set</button>
    {#if entry.unavailableReason}
      <span data-testid="band-entry-reason">{entry.unavailableReason}</span>
    {/if}
  </label>
{/if}

<style>
  .band-row { display: flex; flex-wrap: wrap; align-items: baseline; gap: 0.5rem; margin: 0; }
  .band-name { min-width: 7ch; }
  button:disabled, input:disabled { cursor: not-allowed; }
</style>
