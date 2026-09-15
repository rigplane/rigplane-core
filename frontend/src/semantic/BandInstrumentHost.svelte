<script lang="ts">
  import { onDestroy, type Component, type Snippet } from 'svelte';
  import { HardwareButton } from '$lib/Button';
  import { t } from '$lib/i18n';
  import { bindAbsoluteChoiceInstrument } from '../primitives/control-instruments/control-instrument-behavior';
  import ControlInstrumentRendererHost from '../primitives/control-instruments/ControlInstrumentRendererHost.svelte';
  import {
    createAbsoluteChoiceRendererSeat, createFiniteRendererContext,
    type FiniteControlAppearance,
    type FiniteRendererContext,
  } from '../primitives/control-instruments/control-instrument-renderer.svelte';
  import {
    createFrequencyEntryRendererSeat,
    type FrequencyEntryRendererProps,
  } from '../primitives/frequency/frequency-entry-renderer.svelte';
  import BandFrequencyEntry from './BandFrequencyEntry.svelte';
  import {
    defaultPermitLabel, interpretFrequencyEntry, mhz, UNKNOWN_TEXT,
    type BandInstrumentHandles,
  } from './band-instruments';
  import type { RadioViewModel } from './radio-view-model';

  interface ExistingProps {
    view: RadioViewModel | null;
    onSelectBand?: (name: string) => void;
    onEnterFrequency?: (frequencyHz: number) => void;
    entryRendererContext?: FiniteRendererContext | null;
    entryRenderer?: Component<FrequencyEntryRendererProps>;
    frequencyEntryEnabled?: boolean;
    frequencyEntryUnavailableReason?: string;
    /** Whether the fallback key PRINTS its permit sentence. Its accessible name
     *  carries that sentence, and `data-default-permit` the status, either way. */
    showPermitCaption?: boolean;
    children: Snippet<[BandInstrumentHandles]>;
  }
  type RendererSelection = { finiteAppearance?: undefined; rendererContext?: undefined } | {
    finiteAppearance: FiniteControlAppearance<string>;
    rendererContext: FiniteRendererContext | null;
  };
  type Props = ExistingProps & RendererSelection;

  let {
    view, onSelectBand, onEnterFrequency, finiteAppearance, rendererContext,
    entryRendererContext, entryRenderer, frequencyEntryEnabled = true,
    frequencyEntryUnavailableReason, showPermitCaption = true, children,
  }: Props = $props();
  let band = $derived(view?.band);
  let receiverKnown = $derived(view?.activeReceiver.status === 'known');
  let boundsKnown = $derived(
    band !== undefined && band.tuneMinHz !== null && band.tuneMaxHz !== null,
  );
  let entryAuthorityAvailable = $derived(entryRendererContext !== null);
  let entryText = $state('');
  let bandWasPresent = $state<boolean | undefined>(undefined);
  let interpretedHz = $derived(
    boundsKnown && band?.tuneMinHz != null && band?.tuneMaxHz != null
      ? interpretFrequencyEntry(entryText, band.tuneMinHz, band.tuneMaxHz) : null,
  );
  let entryReady = $derived(
    entryAuthorityAvailable && frequencyEntryEnabled && receiverKnown && boundsKnown && interpretedHz !== null,
  );
  let entryValidation = $derived(
    !entryAuthorityAvailable || !frequencyEntryEnabled || !receiverKnown || !boundsKnown ? 'unavailable' as const
      : entryText.trim() === '' ? 'empty' as const
        : interpretedHz === null ? 'rejected' as const : 'accepted' as const,
  );
  let entryHint = $derived(entryReady && interpretedHz !== null ? `→ ${mhz(interpretedHz)}` : '');
  let entryRangeText = $derived(boundsKnown && band?.tuneMinHz != null && band?.tuneMaxHz != null
    ? `${mhz(band.tuneMinHz)} … ${mhz(band.tuneMaxHz)}` : UNKNOWN_TEXT);
  let entryUnavailableReason = $derived(frequencyEntryUnavailableReason ?? (!boundsKnown
    ? t('core.band.entry.reason.boundsUnknown')
    : !receiverKnown ? t('core.band.entry.reason.receiverUnconfirmed') : undefined));

  $effect(() => {
    const present = band !== undefined;
    if (bandWasPresent === true && !present) entryText = '';
    bandWasPresent = present;
  });

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
      label: choice.name,
      caption: defaultPermitLabel(choice),
    })),
    invoke: selectBand,
  }));
  const fallbackEntryContext = createFiniteRendererContext();
  let entryContext = $derived(band === undefined ? null
    : entryRendererContext ?? fallbackEntryContext);
  const frequencyEntrySeat = createFrequencyEntryRendererSeat(() => ({
    context: entryContext,
    view: {
      label: 'FREQ', draft: entryText, validation: entryValidation,
      boundsAvailable: boundsKnown, interpretedHz,
      inputAvailable: entryAuthorityAvailable && frequencyEntryEnabled && receiverKnown && boundsKnown,
      submitAvailable: entryReady,
      hint: entryHint, rangeText: entryRangeText,
      ...(entryUnavailableReason === undefined ? {} : { unavailableReason: entryUnavailableReason }),
    },
    setDraft: (raw) => { if (entryIsAvailable()) entryText = raw; },
    submit: commitFrequency,
    cancel: cancelEntry,
    handleKeyDown: handleEntryKeydown,
  }));
  onDestroy(() => {
    bandChoiceSeat.destroy();
    frequencyEntrySeat.destroy();
  });

  function entryIsAvailable(): boolean {
    const currentBand = view?.band;
    return entryRendererContext !== null && frequencyEntryEnabled && view?.activeReceiver.status === 'known'
      && currentBand !== undefined
      && currentBand.tuneMinHz !== null && currentBand.tuneMaxHz !== null;
  }

  function commitFrequency(): void {
    const currentBand = view?.band;
    if (!entryIsAvailable() || currentBand?.tuneMinHz == null || currentBand.tuneMaxHz == null) return;
    const currentHz = interpretFrequencyEntry(entryText, currentBand.tuneMinHz, currentBand.tuneMaxHz);
    if (currentHz !== null) onEnterFrequency?.(currentHz);
  }

  function cancelEntry(): void {
    entryText = '';
  }

  function handleEntryKeydown(event: KeyboardEvent): void {
    if (event.key === 'Enter') {
      event.preventDefault();
      event.stopPropagation();
      commitFrequency();
    } else if (event.key === 'Escape') {
      event.preventDefault();
      event.stopPropagation();
      cancelEntry();
      if (event.currentTarget instanceof HTMLElement) event.currentTarget.blur();
    }
  }
</script>

{#snippet externalChoice()}
  {#if finiteAppearance}
    {#key rendererContext}{#key finiteAppearance.choice}<ControlInstrumentRendererHost
      seat={bandChoiceSeat} renderer={finiteAppearance.choice}
    />{/key}{/key}
  {/if}
{/snippet}

{#snippet bandChoice(compact = false)}
  {#if band?.bandChoices.length}
    {#if compact}
      <div role="group" aria-label="Band select" data-testid="band-choices-compact">
        {#each band.bandChoices as choice, index (choice.name)}
          {@const permit = defaultPermitLabel(choice)}
          {@const descriptionId = `band-choice-compact-permit-${index}`}
          <HardwareButton
            active={bandChoiceBehavior.isSelected(choice.name)}
            disabled={!bandChoiceBehavior.available}
            indicator="edge-left"
            color="cyan"
            title={permit}
            describedBy={descriptionId}
            onclick={() => bandChoiceBehavior.invoke(choice.name)}
          >{choice.name}</HardwareButton>
          <span id={descriptionId} class="visually-hidden" data-default-permit={choice.defaultHzTxPermit.status}>
            {permit}
          </span>
        {/each}
      </div>
    {:else if finiteAppearance}{@render externalChoice()}{:else}
      <div class="band-row" role="group" aria-label="Band select" data-testid="band-choices">
        {#each band.bandChoices as choice (choice.name)}
          <button
            type="button" class="band-choice" data-testid={`band-choice-${choice.name}`}
            data-default-permit={choice.defaultHzTxPermit.status}
            aria-label={`${choice.name} — ${defaultPermitLabel(choice)}`}
            aria-pressed={bandChoiceBehavior.isSelected(choice.name)}
            disabled={!bandChoiceBehavior.available}
            onclick={() => bandChoiceBehavior.invoke(choice.name)}
          >{choice.name}{#if showPermitCaption}<small
            data-testid={`band-choice-permit-${choice.name}`}
          >{defaultPermitLabel(choice)}</small>{/if}</button>
        {/each}
      </div>
    {/if}
  {/if}
{/snippet}

{#snippet frequencyEntry()}
  {#if band}
    {#key entryContext}{#key entryRenderer}<ControlInstrumentRendererHost
      seat={frequencyEntrySeat} renderer={entryRenderer ?? BandFrequencyEntry}
    />{/key}{/key}
  {/if}
{/snippet}

{@render children({ bandChoice, frequencyEntry, cancelFrequencyEntry: cancelEntry })}

<style>
  .band-row { display: flex; flex-wrap: wrap; align-items: baseline; gap: 0.5rem; margin: 0; }
  .band-choice[aria-pressed='true'] { font-weight: 700; }
  button:disabled { cursor: not-allowed; }
  .visually-hidden {
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
