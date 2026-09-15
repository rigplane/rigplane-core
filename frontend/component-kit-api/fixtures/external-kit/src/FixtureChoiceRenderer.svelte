<script lang="ts">
  import type { ChoiceRendererProps, FiniteChoiceValue } from '@rigplane/component-kit-api';

  let { lease }: ChoiceRendererProps<FiniteChoiceValue> = $props();
  const view = $derived(lease.view);
</script>

<fieldset data-fixture-choice disabled={!view?.available}>
  <legend>{view?.label ?? 'Unavailable choice'}</legend>
  {#each view?.options ?? [] as option}
    <button
      type="button"
      disabled={!view?.available || option.disabled}
      aria-label={option.disabledReason === undefined
        ? option.label
        : `${option.label}: ${option.disabledReason}`}
      aria-pressed={Object.is(view?.selected, option.value)}
      title={view?.title}
      onclick={() => lease.invoke(option.value)}
    >{option.label}</button>
  {/each}
</fieldset>
