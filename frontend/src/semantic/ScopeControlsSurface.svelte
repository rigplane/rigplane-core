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
  than guessing a value. `aria-pressed`/`aria-checked` are OMITTED (never
  `"false"`) on an unread field — `TxAuxSurface.svelte`'s `pressedOf` shape.
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
  export const pressedOf = (f: ScopeControlsField<boolean>): boolean | undefined =>
    f.reading.status === 'known' ? f.reading.value : undefined;
</script>

<script lang="ts">
  import type { RadioViewModel } from './radio-view-model';

  interface Props {
    view: RadioViewModel;
    onToggleChange?: (field: ScopeToggleField, next: boolean) => void;
    onChoiceChange?: (field: ScopeChoiceField, value: number) => void;
    onSpanChange?: (span: number) => void;
    onSpeedChange?: (speed: number) => void;
    onRefChange?: (ref: number) => void;
  }
  let {
    view, onToggleChange, onChoiceChange, onSpanChange, onSpeedChange, onRefChange,
  }: Props = $props();

  /** Absent group ⇒ this surface renders nothing (S0 optional-group doctrine). */
  let sc = $derived(view.scopeControls);
  let modeKnown = $derived(
    sc?.mode.reading.status === 'known' ? sc.mode.reading.value : undefined,
  );
  let spanApplicable = $derived(isSpanApplicable(modeKnown));
  let edgeApplicable = $derived(isEdgeApplicable(modeKnown));

  function toggle(field: ScopeToggleField): void {
    const f = sc?.[field];
    if (f && usable(f) && f.reading.status === 'known') onToggleChange?.(field, !f.reading.value);
  }
  function choice(field: ScopeChoiceField, value: number): void {
    const f = sc?.[field];
    if (f && usable(f)) onChoiceChange?.(field, value);
  }
  function span(delta: -1 | 1): void {
    if (sc && usable(sc.span)) onSpanChange?.(clampSpan(numberOf(sc.span, 3), delta));
  }
  function speed(delta: -1 | 1): void {
    if (sc && usable(sc.speed)) onSpeedChange?.(clampSpeed(numberOf(sc.speed, 1), delta));
  }
  function ref(delta: -5 | 5): void {
    if (sc && usable(sc.refDb)) onRefChange?.(clampRef(numberOf(sc.refDb, 0), delta));
  }
</script>

{#if sc}
  <section class="scope-controls-surface" data-testid="scope-controls-surface" aria-label="Scope controls">
    {#if sc.mode.availability.structural}
      <div class="scope-row" role="radiogroup" aria-label="Scope mode" data-testid="scope-mode">
        {#each MODE_BUTTONS as [v, label] (v)}
          <button
            type="button" role="radio" class="scope-choice" data-testid={`scope-mode-${v}`}
            aria-checked={sc.mode.reading.status === 'known' && sc.mode.reading.value === v}
            disabled={!usable(sc.mode)} onclick={() => choice('mode', v)}
          >{label}</button>
        {/each}
      </div>
    {/if}

    {#each CHOICES as [field, label, options] (field)}
      {#if (field !== 'edge' || edgeApplicable) && sc[field].availability.structural}
        <div class="scope-row" role="radiogroup" aria-label={label} data-testid={`scope-${field}`}>
          {#each options as [v, optLabel] (v)}
            <button
              type="button" role="radio" class="scope-choice" data-testid={`scope-${field}-${v}`}
              aria-checked={sc[field].reading.status === 'known' && sc[field].reading.value === v}
              disabled={!usable(sc[field])} onclick={() => choice(field, v)}
            >{optLabel}</button>
          {/each}
        </div>
      {/if}
    {/each}

    {#if spanApplicable && sc.span.availability.structural}
      <div class="scope-stepper" data-testid="scope-span">
        <span class="scope-name">SPAN</span>
        <button type="button" disabled={!usable(sc.span)} onclick={() => span(-1)}>-</button>
        <output data-testid="scope-span-value">
          {usable(sc.span) ? (SPAN_LABELS[numberOf(sc.span, 3)] ?? '?') : UNKNOWN_TEXT}
        </output>
        <button type="button" disabled={!usable(sc.span)} onclick={() => span(1)}>+</button>
      </div>
    {/if}

    {#if sc.speed.availability.structural}
      <div class="scope-stepper" data-testid="scope-speed">
        <span class="scope-name">SPEED</span>
        <button type="button" disabled={!usable(sc.speed)} onclick={() => speed(-1)}>-</button>
        <output data-testid="scope-speed-value">
          {usable(sc.speed) ? (SPEED_LABELS[numberOf(sc.speed, 1)] ?? '?') : UNKNOWN_TEXT}
        </output>
        <button type="button" disabled={!usable(sc.speed)} onclick={() => speed(1)}>+</button>
      </div>
    {/if}

    {#if sc.refDb.availability.structural}
      <div class="scope-stepper" data-testid="scope-ref">
        <span class="scope-name">REF</span>
        <button type="button" disabled={!usable(sc.refDb)} onclick={() => ref(-5)}>-</button>
        <output data-testid="scope-ref-value">{textOf(sc.refDb)}</output>
        <button type="button" disabled={!usable(sc.refDb)} onclick={() => ref(5)}>+</button>
      </div>
    {/if}

    {#each TOGGLES as [field, label] (field)}
      {#if sc[field].availability.structural}
        <button
          type="button" class="scope-toggle" data-testid={`scope-${field}`}
          aria-pressed={pressedOf(sc[field])} disabled={!usable(sc[field])}
          onclick={() => toggle(field)}
        >{label}: {textOf(sc[field])}</button>
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
