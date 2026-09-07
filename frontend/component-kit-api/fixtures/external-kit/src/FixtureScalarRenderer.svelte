<script lang="ts">
  import { onDestroy, untrack } from 'svelte';
  import type {
    ScalarAppearance,
    ScalarRendererLease,
    ScalarRendererSeat,
    ScalarRendererView,
  } from '@rigplane/component-kit-api';
  import type { ComponentProps } from 'svelte';

  type Props = ComponentProps<NonNullable<ScalarAppearance['hbar']>>;

  let {
    binding,
    label,
    displayFn,
    unknownDisplay = 'unknown',
    showLabel = true,
    showValue = true,
    compact = false,
    unit,
    title,
  }: Props = $props();

  let attachedSeat: ScalarRendererSeat = untrack(() => binding);
  let lease: ScalarRendererLease = attachedSeat.attachRenderer();
  let view = $state<ScalarRendererView>(untrack(() => lease.view));
  const display = $derived(view.displayed === null
    ? unknownDisplay
    : (displayFn?.(view.displayed) ?? String(view.displayed)));

  $effect.pre(() => {
    if (binding !== attachedSeat) {
      lease.dispose();
      attachedSeat = binding;
      lease = binding.attachRenderer();
    }
    view = lease.view;
  });

  onDestroy(() => lease.dispose());

  function input(event: Event) {
    lease.nativeInput(Number((event.currentTarget as HTMLInputElement).value));
  }

  function keydown(event: KeyboardEvent) {
    if (lease.key({ key: event.key, fine: event.shiftKey })) event.preventDefault();
  }
</script>

<label
  data-fixture-scalar
  data-evidence={view.evidence}
  data-phase={view.phase ?? ''}
  data-interaction={view.interaction}
  data-compact={compact}
  aria-busy={view.busy}
  {title}
>
  {#if showLabel}<span>{label}</span>{/if}
  <input
    type="range"
    min={view.domain.min}
    max={view.domain.max}
    step={view.domain.step}
    value={view.displayed ?? view.domain.min}
    disabled={!view.editable}
    aria-label={label}
    aria-valuetext={display}
    oninput={input}
    onkeydown={keydown}
  />
  {#if showValue}<output>{display}{unit ?? ''}</output>{/if}
</label>
