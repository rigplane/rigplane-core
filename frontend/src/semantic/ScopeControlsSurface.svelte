<!--
  Semantic scope-controls surface (MOR-1311, vocabulary slice 11B — the scope
  toolbar, the LAST B-slice of the vocabulary program).

  Presentation only. Renders the MOR-1298/1299/1330 `scopeControls` fact
  group — all twelve toolbar/popover leaves (mode, edge, span, speed, hold,
  refDb, dual, receiver, duringTx, centerType, vbwNarrow, rbw) — and emits
  control intents as callbacks. It holds no state and consults no controller
  (v3 ADR invariant 11), the same discipline every other semantic surface in
  this directory follows.

  BINDING CARRY-FORWARDS (11A/11A′/11A″ verify reports):
  (1) Renders ONLY from `view.scopeControls`. It never reaches into raw
      state for any leaf — that layering violation is exactly what this
      program removes.
  (2) "receiver/source" is ONE field (`scopeControls.receiver`) — the
      MAIN/SUB button below, never a second invented source control.
  (3) EDGE (`isEdgeApplicable`, modes FIX/S-F) and SPAN (`isSpanApplicable`,
      modes CTR/S-C) are always structurally available; their conditional
      VISIBILITY is a rendering decision layered on top, using the real
      `spectrum-toolbar-logic.ts` predicates (do-not-re-derive doctrine) —
      never a new gate. Both predicates return `false` on an unobserved
      `mode`, so the rows are HIDDEN rather than rendered with a fabricated
      CTR/S-C guess.
  (5) The four popover-only leaves (duringTx/centerType/vbwNarrow/rbw) are
      rendered from facts exactly like the eight toolbar leaves.
      `ScopeSettingsPopover.svelte` exports nothing, so its `CENTER_TYPE`/
      `RBW` label tables are reproduced here verbatim as UI convenience, not
      a fact; `fixedEdge` stays excluded (no fact-layer home, MOR-1354).

  Two-level availability (MOR-977/1256): `structural: false` renders
  NOTHING; a present-but-unusable control stays visible and disabled rather
  than guessing a value. An unread toggle omits `aria-pressed`; unselected
  radio choices expose `aria-checked="false"`.
-->
<script module lang="ts">
  import type { ScopeControlsField } from './radio-view-model';
  import {
    MODE_BUTTONS, SPAN_LABELS, SPEED_LABELS,
    isSpanApplicable, isEdgeApplicable, clampSpan, clampSpeed, clampRef,
  } from '../components/spectrum/spectrum-toolbar-logic';

  /** On/off leaves, `[field, label]`. */
  export const TOGGLES = [
    ['hold', 'HOLD'], ['dual', 'DUAL'], ['duringTx', 'During TX'], ['vbwNarrow', 'VBW narrow'],
  ] as const;
  export type ScopeToggleField = (typeof TOGGLES)[number][0];

  /** Choice-group leaves (excluding `mode`, which uses the imported
   *  `MODE_BUTTONS` table directly), `[field, ariaLabel, choices]`. */
  export const CHOICES = [
    ['edge', 'Scope edge', [[1, '1'], [2, '2'], [3, '3'], [4, '4']]],
    ['centerType', 'Scope center type', [[0, 'Filter'], [1, 'Carrier'], [2, 'Abs.Freq']]],
    ['rbw', 'Scope RBW', [[0, 'Wide'], [1, 'Mid'], [2, 'Narrow']]],
    ['receiver', 'Scope receiver', [[0, 'MAIN'], [1, 'SUB']]],
  ] as const;
  export type ScopeChoiceField = 'mode' | (typeof CHOICES)[number][0];

  export const UNKNOWN_TEXT = '—';
  /** Usable ⇔ the radio HAS it, it is readable NOW, and it was observed. */
  export const usable = (f: ScopeControlsField<unknown>): boolean =>
    f.availability.structural && f.availability.operational && f.reading.status === 'known';
  export const numberOf = (f: ScopeControlsField<number>, fallback: number): number =>
    f.reading.status === 'known' ? f.reading.value : fallback;
  export const textOf = (f: ScopeControlsField<unknown>): string =>
    f.reading.status === 'known' ? String(f.reading.value) : UNKNOWN_TEXT;
</script>

<script lang="ts">
  import { onDestroy } from 'svelte';
  import {
    bindActionInstrument, bindChoiceInstrument, bindToggleInstrument,
  } from '../primitives/control-instruments/control-instrument-behavior';
  import ControlInstrumentRendererHost from '../primitives/control-instruments/ControlInstrumentRendererHost.svelte';
  import {
    createActionRendererSeat, createChoiceRendererSeat, createToggleRendererSeat,
    type ActionRendererSeat, type ChoiceRendererSeat, type FiniteControlAppearance,
    type FiniteRendererContext, type ToggleRendererSeat,
  } from '../primitives/control-instruments/control-instrument-renderer.svelte';
  import type { RadioViewModel } from './radio-view-model';

  interface ExistingProps {
    view: RadioViewModel;
    onToggleChange?: (field: ScopeToggleField, next: boolean) => void;
    onChoiceChange?: (field: ScopeChoiceField, value: number) => void;
    onSpanChange?: (span: number) => void;
    onSpeedChange?: (speed: number) => void;
    onRefChange?: (ref: number) => void;
  }
  type RendererSelection =
    | { finiteAppearance?: undefined; rendererContext?: undefined }
    | { finiteAppearance: FiniteControlAppearance<number>; rendererContext: FiniteRendererContext | null };
  type Props = ExistingProps & RendererSelection;
  let {
    view, onToggleChange, onChoiceChange, onSpanChange, onSpeedChange, onRefChange,
    finiteAppearance, rendererContext,
  }: Props = $props();

  /** Absent group ⇒ this surface renders nothing (S0 optional-group doctrine). */
  let sc = $derived(view.scopeControls);
  let modeKnown = $derived(
    sc?.mode.reading.status === 'known' ? sc.mode.reading.value : undefined,
  );
  let spanApplicable = $derived(isSpanApplicable(modeKnown));
  let edgeApplicable = $derived(isEdgeApplicable(modeKnown));

  function toggleInstrument(field: ScopeToggleField) {
    return bindToggleInstrument(() => ({
      field: sc?.[field],
      invoke: (next) => onToggleChange?.(field, next),
    }));
  }
  function choiceInstrument(field: ScopeChoiceField, choices: readonly number[]) {
    return bindChoiceInstrument(() => ({
      field: sc?.[field], choices,
      invoke: (value) => onChoiceChange?.(field, value),
    }));
  }
  function spanInstrument(delta: -1 | 1) {
    return bindActionInstrument(() => {
      const field = sc?.span;
      return { field, invoke: () => onSpanChange?.(clampSpan(numberOf(field!, 3), delta)) };
    });
  }
  function speedInstrument(delta: -1 | 1) {
    return bindActionInstrument(() => {
      const field = sc?.speed;
      return { field, invoke: () => onSpeedChange?.(clampSpeed(numberOf(field!, 1), delta)) };
    });
  }
  function refInstrument(delta: -5 | 5) {
    return bindActionInstrument(() => {
      const field = sc?.refDb;
      return { field, invoke: () => onRefChange?.(clampRef(numberOf(field!, 0), delta)) };
    });
  }

  const toggleSeats = new Map<ScopeToggleField, ToggleRendererSeat>();
  const choiceSeats = new Map<ScopeChoiceField, ChoiceRendererSeat<number>>();
  const actionSeats = new Map<string, ActionRendererSeat>();
  function toggleSeat(field: ScopeToggleField, label: string): ToggleRendererSeat {
    let seat = toggleSeats.get(field);
    if (!seat) {
      seat = createToggleRendererSeat(() => ({
        context: rendererContext ?? null, field: sc?.[field], label,
        invoke: (next) => onToggleChange?.(field, next),
      }));
      toggleSeats.set(field, seat);
    }
    return seat;
  }
  function choiceSeat(field: ScopeChoiceField, label: string, options: readonly (readonly [number, string])[]) {
    let seat = choiceSeats.get(field);
    if (!seat) {
      seat = createChoiceRendererSeat(() => ({
        context: rendererContext ?? null, field: sc?.[field], label,
        options: options.map(([value, optionLabel]) => ({ value, label: optionLabel })),
        invoke: (value) => onChoiceChange?.(field, value),
      }));
      choiceSeats.set(field, seat);
    }
    return seat;
  }
  function actionSeat(field: 'span' | 'speed' | 'refDb', delta: -5 | -1 | 1 | 5, label: string) {
    const key = `${field}:${delta}`;
    let seat = actionSeats.get(key);
    if (!seat) {
      seat = createActionRendererSeat(() => ({
        context: rendererContext ?? null, field: sc?.[field], label: delta < 0 ? '−' : '+',
        accessibleLabel: `${delta < 0 ? 'Decrease' : 'Increase'} ${label}`,
        invoke: () => {
          const current = numberOf(sc![field], field === 'span' ? 3 : field === 'speed' ? 1 : 0);
          if (field === 'span') onSpanChange?.(clampSpan(current, delta as -1 | 1));
          else if (field === 'speed') onSpeedChange?.(clampSpeed(current, delta as -1 | 1));
          else onRefChange?.(clampRef(current, delta as -5 | 5));
        },
      }));
      actionSeats.set(key, seat);
    }
    return seat;
  }
  onDestroy(() => {
    for (const seat of [...toggleSeats.values(), ...choiceSeats.values(), ...actionSeats.values()]) seat.destroy();
  });
</script>

{#if sc}
  <section class="scope-controls-surface" data-testid="scope-controls-surface" aria-label="Scope controls">
    {#if sc.mode.availability.structural}
      {@const behavior = choiceInstrument('mode', MODE_BUTTONS.map(([value]) => value))}
      <div class="scope-row" role={finiteAppearance ? undefined : 'radiogroup'} aria-label="Scope mode" data-testid="scope-mode">
        {#if finiteAppearance}
          {#key rendererContext}{#key finiteAppearance.choice}<ControlInstrumentRendererHost
            seat={choiceSeat('mode', 'Scope mode', MODE_BUTTONS)} renderer={finiteAppearance.choice}
          />{/key}{/key}
        {:else}{#each MODE_BUTTONS as [v, label] (v)}
            <button type="button" role="radio" class="scope-choice" data-testid={`scope-mode-${v}`}
              aria-checked={behavior.isSelected(v)} disabled={!behavior.available}
              onclick={() => behavior.invoke(v)}>{label}</button>
          {/each}
        {/if}
      </div>
    {/if}

    {#each CHOICES as [field, label, options] (field)}
      {#if (field !== 'edge' || edgeApplicable) && sc[field].availability.structural}
        {@const behavior = choiceInstrument(field, options.map(([value]) => value))}
        <div class="scope-row" role={finiteAppearance ? undefined : 'radiogroup'} aria-label={label} data-testid={`scope-${field}`}>
          {#if finiteAppearance}
            {#key rendererContext}{#key finiteAppearance.choice}<ControlInstrumentRendererHost
              seat={choiceSeat(field, label, options)} renderer={finiteAppearance.choice}
            />{/key}{/key}
          {:else}{#each options as [v, optLabel] (v)}
              <button type="button" role="radio" class="scope-choice" data-testid={`scope-${field}-${v}`}
                aria-checked={behavior.isSelected(v)} disabled={!behavior.available}
                onclick={() => behavior.invoke(v)}>{optLabel}</button>
            {/each}
          {/if}
        </div>
      {/if}
    {/each}

    {#if spanApplicable && sc.span.availability.structural}
      {@const decrement = spanInstrument(-1)}
      {@const increment = spanInstrument(1)}
      <div class="scope-stepper" data-testid="scope-span">
        <span class="scope-name">SPAN</span>
        {#if finiteAppearance}
          {#key rendererContext}{#key finiteAppearance.action}<ControlInstrumentRendererHost
            seat={actionSeat('span', -1, 'scope span')} renderer={finiteAppearance.action}
          />{/key}{/key}
        {:else}<button type="button" disabled={!decrement.available} onclick={() => decrement.invoke()}>-</button>{/if}
        <output data-testid="scope-span-value">
          {usable(sc.span) ? (SPAN_LABELS[numberOf(sc.span, 3)] ?? '?') : UNKNOWN_TEXT}
        </output>
        {#if finiteAppearance}
          {#key rendererContext}{#key finiteAppearance.action}<ControlInstrumentRendererHost
            seat={actionSeat('span', 1, 'scope span')} renderer={finiteAppearance.action}
          />{/key}{/key}
        {:else}<button type="button" disabled={!increment.available} onclick={() => increment.invoke()}>+</button>{/if}
      </div>
    {/if}

    {#if sc.speed.availability.structural}
      {@const decrement = speedInstrument(-1)}
      {@const increment = speedInstrument(1)}
      <div class="scope-stepper" data-testid="scope-speed">
        <span class="scope-name">SPEED</span>
        {#if finiteAppearance}
          {#key rendererContext}{#key finiteAppearance.action}<ControlInstrumentRendererHost
            seat={actionSeat('speed', -1, 'scope speed')} renderer={finiteAppearance.action}
          />{/key}{/key}
        {:else}<button type="button" disabled={!decrement.available} onclick={() => decrement.invoke()}>-</button>{/if}
        <output data-testid="scope-speed-value">
          {usable(sc.speed) ? (SPEED_LABELS[numberOf(sc.speed, 1)] ?? '?') : UNKNOWN_TEXT}
        </output>
        {#if finiteAppearance}
          {#key rendererContext}{#key finiteAppearance.action}<ControlInstrumentRendererHost
            seat={actionSeat('speed', 1, 'scope speed')} renderer={finiteAppearance.action}
          />{/key}{/key}
        {:else}<button type="button" disabled={!increment.available} onclick={() => increment.invoke()}>+</button>{/if}
      </div>
    {/if}

    {#if sc.refDb.availability.structural}
      {@const decrement = refInstrument(-5)}
      {@const increment = refInstrument(5)}
      <div class="scope-stepper" data-testid="scope-ref">
        <span class="scope-name">REF</span>
        {#if finiteAppearance}
          {#key rendererContext}{#key finiteAppearance.action}<ControlInstrumentRendererHost
            seat={actionSeat('refDb', -5, 'scope reference')} renderer={finiteAppearance.action}
          />{/key}{/key}
        {:else}<button type="button" disabled={!decrement.available} onclick={() => decrement.invoke()}>-</button>{/if}
        <output data-testid="scope-ref-value">{textOf(sc.refDb)}</output>
        {#if finiteAppearance}
          {#key rendererContext}{#key finiteAppearance.action}<ControlInstrumentRendererHost
            seat={actionSeat('refDb', 5, 'scope reference')} renderer={finiteAppearance.action}
          />{/key}{/key}
        {:else}<button type="button" disabled={!increment.available} onclick={() => increment.invoke()}>+</button>{/if}
      </div>
    {/if}

    {#each TOGGLES as [field, label] (field)}
      {#if sc[field].availability.structural}
        {@const behavior = toggleInstrument(field)}
        {#if finiteAppearance}
          {#key rendererContext}{#key finiteAppearance.toggle}<ControlInstrumentRendererHost
            seat={toggleSeat(field, label)} renderer={finiteAppearance.toggle}
          />{/key}{/key}
        {:else}<button type="button" class="scope-toggle" data-testid={`scope-${field}`}
            aria-pressed={behavior.confirmed} disabled={!behavior.available}
            onclick={() => behavior.invoke()}>{label}: {textOf(sc[field])}</button>
        {/if}
      {/if}
    {/each}
  </section>
{/if}

<style>
  /* Structure only — a design language owns colour (MOR-977, forced-colors). */
  .scope-controls-surface { display: flex; flex-direction: column; gap: 0.25rem; }
  .scope-row, .scope-stepper { display: flex; flex-wrap: wrap; align-items: baseline; gap: 0.5rem; margin: 0; }
  .scope-name { min-width: 5ch; }
  .scope-choice[aria-checked='true'], .scope-toggle[aria-pressed='true'] { font-weight: 700; }
  button:disabled { cursor: not-allowed; }
</style>
