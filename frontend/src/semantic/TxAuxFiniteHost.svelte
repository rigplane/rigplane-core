<script module lang="ts">
  import type { TxAuxField } from './radio-view-model';
  import { disabledReasonText } from './disabled-reason';

  const usable = (field: TxAuxField<unknown>): boolean =>
    field.availability.structural
    && field.availability.operational
    && field.reading.status === 'known';

  const reasonOf = (field: TxAuxField<unknown>): 'field-not-observed' | undefined =>
    usable(field) ? undefined : 'field-not-observed';

  const reasonTextOf = (field: TxAuxField<unknown>): string | undefined =>
    !field.availability.structural
      ? disabledReasonText(field.availability)
      : usable(field) ? undefined : disabledReasonText({ structural: true, operational: false });

  const textOf = (field: TxAuxField<unknown>): string =>
    field.reading.status !== 'known' ? '?'
      : typeof field.reading.value === 'boolean' ? (field.reading.value ? 'on' : 'off')
        : String(field.reading.value);

  let sequence = 0;
</script>

<script lang="ts">
  import { onDestroy, type Snippet } from 'svelte';
  import '../components-v2/controls/control-button.css';
  import {
    bindActionInstrument, bindToggleInstrument, type InstrumentField,
  } from '../primitives/control-instruments/control-instrument-behavior';
  import ControlInstrumentRendererHost from '../primitives/control-instruments/ControlInstrumentRendererHost.svelte';
  import {
    createActionRendererSeat, createToggleRendererSeat,
    type ActionRendererInput, type ActionRendererSeat, type FiniteControlAppearance,
    type FiniteRendererContext, type ToggleRendererInput, type ToggleRendererSeat,
  } from '../primitives/control-instruments/control-instrument-renderer.svelte';
  import type { AtuStatus, RadioViewModel } from './radio-view-model';
  import { blockedLabel, keyBlockedReasons, type TxAuthoritySnapshot } from './rx-tx-surface';
  import {
    TX_AUX_TOGGLES, type TxAuxFiniteHandles, type TxAuxToggleField,
  } from './tx-aux-finite';

  interface ExistingProps {
    view: RadioViewModel | null;
    tx: TxAuthoritySnapshot;
    onToggle?: (field: TxAuxToggleField) => void;
    onAtuTune?: () => void;
    children: Snippet<[TxAuxFiniteHandles]>;
  }
  type RendererSelection =
    | { finiteAppearance?: undefined; rendererContext?: undefined }
    | { finiteAppearance: FiniteControlAppearance<string | number>; rendererContext: FiniteRendererContext | null };
  type Props = ExistingProps & RendererSelection;

  let {
    view, tx, onToggle, onAtuTune, children, finiteAppearance, rendererContext,
  }: Props = $props();
  let txAux = $derived(view?.txAux);
  let tuneBlocked = $derived(view === null ? [] : keyBlockedReasons(view, tx));

  const blockedId = `tx-aux-blocked-${++sequence}`;
  const reasonIdPrefix = `tx-aux-reason-${sequence}`;
  const tuneAtuReasonId = `${reasonIdPrefix}-tune-atu`;

  function sourceField(field: TxAuxToggleField): TxAuxField<boolean | AtuStatus> | undefined {
    return txAux?.[field];
  }

  function booleanField(field: TxAuxToggleField): InstrumentField<boolean> | undefined {
    const source = sourceField(field);
    if (!source) return undefined;
    if (field !== 'atu') return source as TxAuxField<boolean>;
    return {
      availability: source.availability,
      reading: source.reading.status === 'known'
        ? { status: 'known', value: source.reading.value !== 'off' }
        : { status: 'unknown' },
    };
  }

  function toggleInput(field: TxAuxToggleField, label: string): ToggleRendererInput {
    const source = sourceField(field);
    const text = source ? textOf(source) : '?';
    const reason = source ? reasonTextOf(source) : undefined;
    return {
      context: rendererContext ?? null,
      field: booleanField(field),
      label,
      accessibleLabel: `${label}: ${text}`,
      ...(reason === undefined
        ? field === 'atu' && text === 'tuning' ? { title: 'ATU: tuning' } : {}
        : { title: reason }),
      invoke: () => onToggle?.(field),
    };
  }

  function tuneInput(): ActionRendererInput<AtuStatus> {
    const atuReason = txAux ? reasonTextOf(txAux.atu) : undefined;
    const authorityReason = tuneBlocked.length > 0
      ? tuneBlocked.map((code) => blockedLabel(code)).join('; ')
      : undefined;
    const title = atuReason ?? authorityReason;
    return {
      context: rendererContext ?? null,
      field: txAux?.atu,
      blocked: view === null || tuneBlocked.length > 0,
      label: 'TUNE',
      accessibleLabel: 'TUNE',
      ...(title === undefined ? {} : { title }),
      invoke: () => onAtuTune?.(),
    };
  }

  const toggleBehaviors = Object.fromEntries(TX_AUX_TOGGLES.map(([field, label]) => [
    field, bindToggleInstrument(() => toggleInput(field, label)),
  ])) as Record<TxAuxToggleField, ReturnType<typeof bindToggleInstrument>>;
  const tuneBehavior = bindActionInstrument(() => tuneInput());
  const toggleSeats = Object.fromEntries(TX_AUX_TOGGLES.map(([field, label]) => [
    field, createToggleRendererSeat(() => toggleInput(field, label)),
  ])) as unknown as Record<TxAuxToggleField, ToggleRendererSeat>;
  const tuneSeat: ActionRendererSeat = createActionRendererSeat(() => tuneInput());

  onDestroy(() => {
    for (const seat of Object.values(toggleSeats)) seat.destroy();
    tuneSeat.destroy();
  });

  const reasonIdOf = (field: TxAuxToggleField): string | undefined => {
    const source = sourceField(field);
    return source && reasonTextOf(source) !== undefined ? `${reasonIdPrefix}-${field}` : undefined;
  };
</script>

{#snippet toggleControl(field: TxAuxToggleField, label: string)}
  {@const source = sourceField(field)}
  {#if source?.availability.structural}
    {@const behavior = toggleBehaviors[field]}
    {#if finiteAppearance}
      {#key rendererContext}{#key finiteAppearance.toggle}<ControlInstrumentRendererHost
        seat={toggleSeats[field]} renderer={finiteAppearance.toggle}
      />{/key}{/key}
    {:else}
      <button
        type="button" class="tx-aux-toggle v2-control-button v2-control-button--compact"
        data-testid={`tx-aux-${field}`} data-field={field}
        data-surface="hardware" data-indicator-style="dot" data-indicator-color="cyan"
        data-active={behavior.confirmed} data-disabled-reason={reasonOf(source)}
        title={reasonTextOf(source)} aria-label={`${label}: ${textOf(source)}`}
        aria-describedby={reasonIdOf(field)} aria-pressed={behavior.confirmed}
        disabled={!behavior.available} onclick={() => behavior.invoke()}
      >{label}: {textOf(source)}</button>
      {#if reasonTextOf(source) !== undefined}
        <span id={reasonIdOf(field)} class="sr-only">{reasonTextOf(source)}</span>
      {/if}
    {/if}
  {/if}
{/snippet}

{#snippet atu()}{@render toggleControl('atu', 'ATU')}{/snippet}
{#snippet vox()}{@render toggleControl('vox', 'VOX')}{/snippet}
{#snippet compressor()}{@render toggleControl('compressor', 'COMP')}{/snippet}
{#snippet monitor()}{@render toggleControl('monitor', 'MON')}{/snippet}
{#snippet atuTune()}
  {#if txAux?.atu.availability.structural}
    {@const atuReason = reasonTextOf(txAux.atu)}
    {@const tuneReason = tuneInput().title}
    {@const tuneDescribedBy = atuReason !== undefined
      ? tuneAtuReasonId : tuneBlocked.length > 0 ? blockedId : undefined}
    {#if finiteAppearance}
      {#key rendererContext}{#key finiteAppearance.action}<ControlInstrumentRendererHost
        seat={tuneSeat} renderer={finiteAppearance.action}
      />{/key}{/key}
    {:else}
      <button
        type="button" class="tx-aux-tune v2-control-button v2-control-button--compact"
        data-surface="hardware" data-testid="tx-aux-atu-tune"
        title={tuneReason} aria-describedby={tuneDescribedBy}
        disabled={!tuneBehavior.available} onclick={() => tuneBehavior.invoke()}
      >TUNE</button>
      {#if atuReason !== undefined}
        <span id={tuneAtuReasonId} class="sr-only">{atuReason}</span>
      {/if}
      <ul class="tx-aux-blocked" id={blockedId} data-testid="tx-aux-tune-blocked">
        {#each tuneBlocked as code (code)}<li data-reason={code}>{blockedLabel(code)}</li>{/each}
      </ul>
    {/if}
  {/if}
{/snippet}

{@render children({ atu, vox, compressor, monitor, atuTune })}

<style>
  .tx-aux-blocked { margin: 0; padding-inline-start: 1.2em; }
  .tx-aux-blocked:empty { display: none; }
  .sr-only { position: absolute; width: 1px; height: 1px; padding: 0; margin: -1px; overflow: hidden; clip: rect(0, 0, 0, 0); white-space: nowrap; border: 0; }
  .tx-aux-toggle[aria-pressed='true'] { font-weight: 700; }
  .tx-aux-toggle:disabled, .tx-aux-tune:disabled { cursor: not-allowed; }
</style>
