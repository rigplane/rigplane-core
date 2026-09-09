<script module lang="ts">
  export const retainedInvocations = new Map<string, (value?: unknown) => void>();
  export const resetRetainedInvocations = () => retainedInvocations.clear();
  let optionReasonSequence = 0;
</script>

<script lang="ts">
  import { onMount } from 'svelte';
  import type {
    ActionRendererLease, ChoiceRendererLease, ToggleRendererLease,
  } from '../../control-instrument-renderer.svelte';

  type Lease = ActionRendererLease | ToggleRendererLease | ChoiceRendererLease<unknown>;
  let { lease }: { lease: Lease } = $props();
  let view = $derived(lease.view);
  const optionReasonPrefix = `finite-option-reason-${++optionReasonSequence}`;

  function invoke(value?: unknown): void {
    const current = lease.view;
    if (current && 'options' in current) {
      (lease as ChoiceRendererLease<unknown>).invoke(value);
    } else {
      (lease as ActionRendererLease | ToggleRendererLease).invoke();
    }
  }

  onMount(() => {
    const label = lease.view?.label;
    if (label) retainedInvocations.set(label, invoke);
  });
</script>

{#if view}
  {#if 'options' in view}
    <div
      role="radiogroup" aria-label={view.accessibleLabel ?? view.label}
      data-reading={view.reading.status === 'known' ? String(view.reading.value) : 'unknown'}
      data-testid={`external-${view.label}`}
    >
      {#each view.options as option, index (option.value)}
        {@const reasonId = option.disabledReason
          ? `${optionReasonPrefix}-${index}`
          : undefined}
        <button
          type="button" role="radio" aria-checked={Object.is(view.selected, option.value)}
          aria-describedby={reasonId} title={option.disabledReason}
          disabled={!view.available || option.disabled === true}
          data-testid={`external-${view.label}-${option.value}`}
          data-option-label={option.label} data-option-caption={option.caption}
          onclick={() => invoke(option.value)}
        >{option.caption === undefined ? option.label : `${option.label} ${option.caption}`}</button>
        {#if reasonId}<span id={reasonId} class="sr-only">{option.disabledReason}</span>{/if}
      {/each}
    </div>
  {:else if 'confirmed' in view}
    <button
      type="button" aria-pressed={view.confirmed} disabled={!view.available}
      aria-label={view.accessibleLabel ?? view.label} data-testid={`external-${view.label}`}
      onclick={() => invoke()}
    >{view.label}</button>
  {:else}
    <button
      type="button" disabled={!view.available} aria-label={view.accessibleLabel ?? view.label}
      data-testid={`external-${view.label}`} onclick={() => invoke()}
    >{view.label}</button>
  {/if}
{/if}

<style>
  .sr-only {
    position: absolute;
    width: 1px;
    height: 1px;
    padding: 0;
    margin: -1px;
    overflow: hidden;
    clip: rect(0, 0, 0, 0);
    white-space: nowrap;
    border: 0;
  }
</style>
