<script lang="ts">
  import { onDestroy, type Snippet } from 'svelte';
  import { bindAbsoluteChoiceInstrument } from '../primitives/control-instruments/control-instrument-behavior';
  import ControlInstrumentRendererHost from '../primitives/control-instruments/ControlInstrumentRendererHost.svelte';
  import {
    createAbsoluteChoiceRendererSeat,
    type FiniteControlAppearance,
    type FiniteRendererContext,
  } from '../primitives/control-instruments/control-instrument-renderer.svelte';
  import { defaultPermitLabel, type BandInstrumentHandles } from './band-instruments';
  import type { RadioViewModel } from './radio-view-model';

  interface ExistingProps {
    view: RadioViewModel | null;
    onSelectBand?: (name: string) => void;
    children: Snippet<[BandInstrumentHandles]>;
  }
  type RendererSelection = { finiteAppearance?: undefined; rendererContext?: undefined } | {
    finiteAppearance: FiniteControlAppearance<string>;
    rendererContext: FiniteRendererContext | null;
  };
  type Props = ExistingProps & RendererSelection;

  let { view, onSelectBand, finiteAppearance, rendererContext, children }: Props = $props();
  let band = $derived(view?.band);
  let receiverKnown = $derived(view?.activeReceiver.status === 'known');

  const selectBand = (name: string): void => {
    const choice = band?.bandChoices.find(candidate => candidate.name === name);
    if (receiverKnown && choice) onSelectBand?.(choice.name);
  };
  const bandChoiceBehavior = bindAbsoluteChoiceInstrument<string>(() => ({
    choices: band?.bandChoices.map(choice => choice.name) ?? [],
    selected: band?.currentBand.reading.status === 'known' ? band.currentBand.reading.value : undefined,
    available: receiverKnown && band !== undefined,
    invoke: selectBand,
  }));
  const bandChoiceSeat = createAbsoluteChoiceRendererSeat<string>(() => ({
    context: band === undefined ? null : rendererContext ?? null,
    label: 'Band',
    reading: band?.currentBand.reading ?? { status: 'unknown' },
    available: receiverKnown && band !== undefined,
    options: (band?.bandChoices ?? []).map(choice => ({
      value: choice.name,
      label: `${choice.name} ${defaultPermitLabel(choice)}`,
    })),
    invoke: selectBand,
  }));
  onDestroy(() => bandChoiceSeat.destroy());
</script>

{#snippet externalChoice()}
  {#if finiteAppearance}
    {#key rendererContext}{#key finiteAppearance.choice}<ControlInstrumentRendererHost
      seat={bandChoiceSeat} renderer={finiteAppearance.choice}
    />{/key}{/key}
  {/if}
{/snippet}

{#snippet bandChoice()}
  {#if band?.bandChoices.length}
    {#if finiteAppearance}{@render externalChoice()}{:else}
      <div class="band-row" role="group" aria-label="Band select" data-testid="band-choices">
        {#each band.bandChoices as choice (choice.name)}
          <button
            type="button" class="band-choice" data-testid={`band-choice-${choice.name}`}
            data-default-permit={choice.defaultHzTxPermit.status}
            aria-pressed={bandChoiceBehavior.isSelected(choice.name)}
            disabled={!bandChoiceBehavior.available}
            onclick={() => bandChoiceBehavior.invoke(choice.name)}
          >{choice.name}<small data-testid={`band-choice-permit-${choice.name}`}
          >{defaultPermitLabel(choice)}</small></button>
        {/each}
      </div>
    {/if}
  {/if}
{/snippet}

{@render children({ bandChoice })}

<style>
  .band-row { display: flex; flex-wrap: wrap; align-items: baseline; gap: 0.5rem; margin: 0; }
  .band-choice[aria-pressed='true'] { font-weight: 700; }
  button:disabled { cursor: not-allowed; }
</style>
