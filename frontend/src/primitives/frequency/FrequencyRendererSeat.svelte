<script lang="ts">
  import { onDestroy, untrack } from 'svelte';
  import type { FrequencyRenderer } from '../../../component-kit-api/src/index';
  import { getSelectedFrequencyReadout } from '../../component-kits/activation';
  import StandardFrequencyReadout from './StandardFrequencyReadout.svelte';
  import type { FrequencyInstrumentBinding } from './frequency-instrument.svelte';

  interface Props {
    binding: FrequencyInstrumentBinding;
    presentation: 'interactive' | 'passive';
    compact: boolean;
    active: boolean;
    receiver: 'main' | 'sub';
    vfoFreqHook: boolean;
  }

  let {
    binding,
    presentation,
    compact,
    active,
    receiver,
    vfoFreqHook,
  }: Props = $props();

  let attachedBinding = untrack(() => binding);
  let attachedContext = untrack(() => attachedBinding.context);
  let attachedRenderer = untrack(() => getSelectedFrequencyReadout());
  let selectedRenderer = $state.raw<FrequencyRenderer | undefined>(attachedRenderer);
  const attachLease = (
    owner: FrequencyInstrumentBinding,
    renderer: FrequencyRenderer | undefined,
  ) => owner.attachRenderer(
    () => Object.is(binding, owner) && getSelectedFrequencyReadout() === renderer,
  );
  let lease = $state.raw(attachLease(attachedBinding, attachedRenderer));

  $effect.pre(() => {
    const nextBinding = binding;
    const nextContext = nextBinding.context;
    const nextRenderer = getSelectedFrequencyReadout();
    if (Object.is(nextBinding, attachedBinding)
      && Object.is(nextContext, attachedContext)
      && nextRenderer === attachedRenderer
      && !lease.revoked) return;
    lease.revoke();
    attachedBinding = nextBinding;
    attachedContext = nextContext;
    attachedRenderer = nextRenderer;
    selectedRenderer = nextRenderer;
    lease = attachLease(nextBinding, nextRenderer);
  });

  onDestroy(() => lease.revoke());
</script>

{#if selectedRenderer}
  {@const Renderer = selectedRenderer}
  <Renderer
    model={binding.model}
    interaction={lease.interaction}
    {presentation}
    {compact}
    {active}
    {receiver}
    {vfoFreqHook}
  />
{:else}
  <StandardFrequencyReadout
    model={binding.model}
    interaction={lease.interaction}
    {presentation}
    {compact}
    {active}
    {receiver}
    {vfoFreqHook}
  />
{/if}
