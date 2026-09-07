<script module lang="ts">
  import type { Snippet } from 'svelte';

  export interface RitXitScanInstrumentHandles {
    readonly rit: Snippet;
    readonly xit: Snippet;
    readonly clear: Snippet;
  }

  export type RitXitScanInstrumentLayout = Snippet<[
    handles: RitXitScanInstrumentHandles,
    offsetSlot: Snippet,
  ]>;
</script>

<script lang="ts">
  import { onDestroy } from 'svelte';
  import { bindActionInstrument, bindToggleInstrument } from '../primitives/control-instruments/control-instrument-behavior';
  import ControlInstrumentRendererHost from '../primitives/control-instruments/ControlInstrumentRendererHost.svelte';
  import {
    createActionRendererSeat, createToggleRendererSeat,
    type FiniteControlAppearance, type FiniteRendererContext,
  } from '../primitives/control-instruments/control-instrument-renderer.svelte';
  import type { RadioViewModel } from './radio-view-model';

  interface ExistingProps {
    view: RadioViewModel | null;
    onRitToggle?: () => void;
    onXitToggle?: () => void;
    onClear?: () => void;
    children: Snippet<[RitXitScanInstrumentHandles]>;
  }
  type RendererSelection = { finiteAppearance?: undefined; rendererContext?: undefined } | {
    finiteAppearance: FiniteControlAppearance<string | number>;
    rendererContext: FiniteRendererContext | null;
  };
  type Props = ExistingProps & RendererSelection;

  let {
    view, onRitToggle, onXitToggle, onClear,
    finiteAppearance, rendererContext, children,
  }: Props = $props();

  let ritXit = $derived(view?.ritXit);
  let activeKnown = $derived(view?.activeReceiver.status === 'known');
  const groupAvailability = { structural: true, operational: true } as const;
  const ritToggle = bindToggleInstrument(() => ({
    field: ritXit?.ritActive, blocked: !activeKnown, invoke: () => onRitToggle?.(),
  }));
  const xitToggle = bindToggleInstrument(() => ({
    field: ritXit?.xitActive, blocked: !activeKnown, invoke: () => onXitToggle?.(),
  }));
  const clearAction = bindActionInstrument(() => ({
    availability: ritXit ? groupAvailability : undefined,
    blocked: !activeKnown, invoke: () => onClear?.(),
  }));

  const ritSeat = createToggleRendererSeat(() => ({
    context: rendererContext ?? null, field: ritXit?.ritActive, blocked: !activeKnown,
    label: 'RIT', invoke: () => onRitToggle?.(),
  }));
  const xitSeat = createToggleRendererSeat(() => ({
    context: rendererContext ?? null, field: ritXit?.xitActive, blocked: !activeKnown,
    label: 'XIT', invoke: () => onXitToggle?.(),
  }));
  const clearSeat = createActionRendererSeat<boolean>(() => ({
    context: rendererContext ?? null,
    availability: ritXit ? groupAvailability : undefined,
    blocked: !activeKnown, label: 'CLEAR', invoke: () => onClear?.(),
  }));
  onDestroy(() => { ritSeat.destroy(); xitSeat.destroy(); clearSeat.destroy(); });
</script>

{#snippet toggle(kind: 'rit' | 'xit')}
  {@const field = kind === 'rit' ? ritXit?.ritActive : ritXit?.xitActive}
  {@const behavior = kind === 'rit' ? ritToggle : xitToggle}
  {@const seat = kind === 'rit' ? ritSeat : xitSeat}
  {@const label = kind === 'rit' ? 'RIT' : 'XIT'}
  {#if field?.availability.structural}
    {#if finiteAppearance}
      {#key rendererContext}{#key finiteAppearance.toggle}<ControlInstrumentRendererHost
        {seat} renderer={finiteAppearance.toggle}
      />{/key}{/key}
    {:else}
      <button type="button" data-testid={`ritxit-${kind}-toggle`}
        aria-pressed={behavior.confirmed} disabled={!behavior.available}
        onclick={() => behavior.invoke()}>{label}</button>
    {/if}
  {/if}
{/snippet}
{#snippet rit()}{@render toggle('rit')}{/snippet}
{#snippet xit()}{@render toggle('xit')}{/snippet}
{#snippet clear()}
  {#if ritXit}
    {#if finiteAppearance}
      {#key rendererContext}{#key finiteAppearance.action}<ControlInstrumentRendererHost
        seat={clearSeat} renderer={finiteAppearance.action}
      />{/key}{/key}
    {:else}
      <button type="button" data-testid="ritxit-clear" disabled={!clearAction.available}
        onclick={() => clearAction.invoke()}>CLEAR</button>
    {/if}
  {/if}
{/snippet}

{@render children({ rit, xit, clear })}

<style>
  button[aria-pressed='true'] { font-weight: 700; }
  button:disabled { cursor: not-allowed; }
</style>
