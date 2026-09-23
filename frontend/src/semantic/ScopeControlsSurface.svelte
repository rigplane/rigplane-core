<!--
  Semantic scope-controls surface (MOR-1311, vocabulary slice 11B — the scope
  toolbar, the LAST B-slice of the vocabulary program; MOR-2545 PR1 — one row
  plus a More panel for the radio-held controls).

  Presentation only. Renders the MOR-1298/1299/1330 `scopeControls` fact
  group — all twelve toolbar/popover leaves (mode, edge, span, speed, hold,
  refDb, dual, receiver, duringTx, centerType, vbwNarrow, rbw) — and emits
  control intents as callbacks. It holds no radio state and consults no
  controller (v3 ADR invariant 11); the only local state is the More panel's
  open/closed UI flag.

  MOR-2545 PR1 SHAPE (owner-approved design, ticket comments 2026-09-23):
  the NATIVE presentation is ONE always-visible row —
  CTR/FIX (two flat keys of the mode choice; in S-C/S-F neither lights and
  the More panel's full mode choice shows the current mode), SPAN ‹ value ›,
  REF ‹ value ›, HOLD (flat toggle key), MAIN/SUB (only when the receiver
  choice is structural — dual-receiver radios), and a ⋯ key that opens MORE.
  More carries the rest of the radio-held controls: the full mode choice
  CTR/FIX/S-C/S-F, the FIX edge 1–4 (only when applicable), centre type
  Filter/Carrier/Abs, RBW W/M/N and VBW narrow, SPEED ‹ ›, DUAL, during TX.
  Every toggle is a flat key (`ScopeFlatKey.svelte`) whose LIGHT is its state
  — never `NAME: true/false` text. Unread stepper values are EMPTY with
  reserved width — never `—`/`?` placeholders. When an external finite
  appearance is selected (`finiteAppearance`), the surface delegates to the
  renderer hosts (the stacked layout below) — the
  row/More split is the NATIVE appearance; a kit's renderer owns its own layout.

  BINDING CARRY-FORWARDS (11A/11A′/11A″ verify reports):
  (1) Renders ONLY from `view.scopeControls`. It never reaches into raw
      state for any leaf — that layering violation is exactly what this
      program removes.
  (2) "receiver/source" is ONE field (`scopeControls.receiver`) — the
      MAIN/SUB keys below, never a second invented source control.
  (3) EDGE (`isEdgeApplicable`, modes FIX/S-F) and SPAN (`isSpanApplicable`,
      modes CTR/S-C) are always structurally available; their conditional
      VISIBILITY is a rendering decision layered on top, using the real
      `spectrum-toolbar-logic.ts` predicates (do-not-re-derive doctrine) —
      never a new gate. Both predicates return `false` on an unobserved
      `mode`, so EDGE stays out of More and SPAN out of the row rather than
      rendering with a fabricated CTR/S-C guess.
  (5) The four popover-only leaves (duringTx/centerType/vbwNarrow/rbw) are
      rendered from facts exactly like the eight toolbar leaves.
      `fixedEdge` stays excluded (no fact-layer home, MOR-1354).

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

  /** On/off leaves, `[field, label, reservedWidth]`. */
  export const TOGGLES = [
    ['hold', 'HOLD', '52px'], ['dual', 'DUAL', '52px'],
    ['duringTx', 'During TX', '78px'], ['vbwNarrow', 'VBW narrow', '88px'],
  ] as const;
  export type ScopeToggleField = (typeof TOGGLES)[number][0];

  /** Choice-group leaves (excluding `mode`, which uses the imported
   *  `MODE_BUTTONS` table directly), `[field, ariaLabel, shortName, choices]`. */
  export const CHOICES = [
    ['edge', 'Scope edge', 'EDGE', [[1, '1'], [2, '2'], [3, '3'], [4, '4']]],
    ['centerType', 'Scope center type', 'CENTRE', [[0, 'Filter'], [1, 'Carrier'], [2, 'Abs']]],
    ['rbw', 'Scope RBW', 'RBW', [[0, 'W'], [1, 'M'], [2, 'N']]],
    ['receiver', 'Scope receiver', '', [[0, 'MAIN'], [1, 'SUB']]],
  ] as const;
  export type ScopeChoiceField = 'mode' | (typeof CHOICES)[number][0];

  /** The More panel's choice leaves — `receiver` lives in the row. */
  const MORE_CHOICES = CHOICES.filter(([field]) => field !== 'receiver');
  /** The More panel's toggle leaves — `hold` lives in the row. */
  const MORE_TOGGLES = TOGGLES.filter(([field]) => field !== 'hold');
  /** The row's two-key mode choice (CTR/FIX); S-C/S-F live in More only. */
  const QUICK_MODE = MODE_BUTTONS.slice(0, 2);

  export const UNKNOWN_TEXT = '—';
  /** Usable ⇔ the radio HAS it, it is readable NOW, and it was observed. */
  export const usable = (f: ScopeControlsField<unknown>): boolean =>
    f.availability.structural && f.availability.operational && f.reading.status === 'known';
  export const numberOf = (f: ScopeControlsField<number>, fallback: number): number =>
    f.reading.status === 'known' ? f.reading.value : fallback;
  export const textOf = (f: ScopeControlsField<unknown>): string =>
    f.reading.status === 'known' ? String(f.reading.value) : UNKNOWN_TEXT;
  /** The observed value, or `undefined` when unread — drives a flat key's
   *  `lit` (`undefined` → `null` → drawn unlit with its label, no value). */
  const valueOf = <T>(f: ScopeControlsField<T> | undefined): T | undefined =>
    f !== undefined && f.reading.status === 'known' ? f.reading.value : undefined;
</script>

<script lang="ts">
  import { onDestroy } from 'svelte';
  import type { Snippet } from 'svelte';
  import ScopeFlatKey from '../components/spectrum/ScopeFlatKey.svelte';
  import ScopeMorePanel from '../components/spectrum/ScopeMorePanel.svelte';
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
    /**
     * MOR-2545 PR2 — the toolbar's screen-only control group (VIEW, AUTO,
     * AVG/PEAK, BRT, palette, band-plan layers, the STEP overflow copy),
     * rendered inside the More panel BELOW the radio-held group. The host
     * (SpectrumToolbar, via SemanticRadioSurfaces) hands it over through the
     * `scopeControls` snippet's second parameter; the markup and handlers
     * stay the host's — this surface only places the group. The snippet
     * receives a close callback so entries that open their own surface
     * (EiBi) can close the More panel, as the old layer dropdown closed
     * itself.
     */
    moreScreen?: Snippet<[closeMore?: () => void]>;
  }
  type RendererSelection =
    | { finiteAppearance?: undefined; rendererContext?: undefined }
    | { finiteAppearance: FiniteControlAppearance<number>; rendererContext: FiniteRendererContext | null };
  type Props = ExistingProps & RendererSelection;
  let {
    view, onToggleChange, onChoiceChange, onSpanChange, onSpeedChange, onRefChange,
    moreScreen, finiteAppearance, rendererContext,
  }: Props = $props();

  /** Absent group ⇒ this surface renders nothing (S0 optional-group doctrine). */
  let sc = $derived(view.scopeControls);
  let modeKnown = $derived(
    sc?.mode.reading.status === 'known' ? sc.mode.reading.value : undefined,
  );
  let spanApplicable = $derived(isSpanApplicable(modeKnown));
  let edgeApplicable = $derived(isEdgeApplicable(modeKnown));

  /** The ⋯ More panel — UI-local open flag (never radio state). */
  let moreOpen = $state(false);
  let moreKeyEl = $state<HTMLElement | null>(null);

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
    {#if finiteAppearance}
      <!-- External finite appearance: the kit's renderers own the layout —
           the pre-MOR-2545 stacked groups, hosts only, never native keys. -->
      {#if sc.mode.availability.structural}
        <div class="scope-row" aria-label="Scope mode" data-testid="scope-mode">
          {#key rendererContext}{#key finiteAppearance.choice}<ControlInstrumentRendererHost
            seat={choiceSeat('mode', 'Scope mode', MODE_BUTTONS)} renderer={finiteAppearance.choice}
          />{/key}{/key}
        </div>
      {/if}

      {#each CHOICES as [field, label, , options] (field)}
        {#if (field !== 'edge' || edgeApplicable) && sc[field].availability.structural}
          <div class="scope-row" aria-label={label} data-testid={`scope-${field}`}>
            {#key rendererContext}{#key finiteAppearance.choice}<ControlInstrumentRendererHost
              seat={choiceSeat(field, label, options)} renderer={finiteAppearance.choice}
            />{/key}{/key}
          </div>
        {/if}
      {/each}

      {#if spanApplicable && sc.span.availability.structural}
        <div class="scope-stepper" data-testid="scope-span">
          <span class="scope-name">SPAN</span>
          {#key rendererContext}{#key finiteAppearance.action}<ControlInstrumentRendererHost
            seat={actionSeat('span', -1, 'scope span')} renderer={finiteAppearance.action}
          />{/key}{/key}
          <output data-testid="scope-span-value">
            {usable(sc.span) ? (SPAN_LABELS[numberOf(sc.span, 3)] ?? UNKNOWN_TEXT) : UNKNOWN_TEXT}
          </output>
          {#key rendererContext}{#key finiteAppearance.action}<ControlInstrumentRendererHost
            seat={actionSeat('span', 1, 'scope span')} renderer={finiteAppearance.action}
          />{/key}{/key}
        </div>
      {/if}

      {#if sc.speed.availability.structural}
        <div class="scope-stepper" data-testid="scope-speed">
          <span class="scope-name">SPEED</span>
          {#key rendererContext}{#key finiteAppearance.action}<ControlInstrumentRendererHost
            seat={actionSeat('speed', -1, 'scope speed')} renderer={finiteAppearance.action}
          />{/key}{/key}
          <output data-testid="scope-speed-value">
            {usable(sc.speed) ? (SPEED_LABELS[numberOf(sc.speed, 1)] ?? UNKNOWN_TEXT) : UNKNOWN_TEXT}
          </output>
          {#key rendererContext}{#key finiteAppearance.action}<ControlInstrumentRendererHost
            seat={actionSeat('speed', 1, 'scope speed')} renderer={finiteAppearance.action}
          />{/key}{/key}
        </div>
      {/if}

      {#if sc.refDb.availability.structural}
        <div class="scope-stepper" data-testid="scope-ref">
          <span class="scope-name">REF</span>
          {#key rendererContext}{#key finiteAppearance.action}<ControlInstrumentRendererHost
            seat={actionSeat('refDb', -5, 'scope reference')} renderer={finiteAppearance.action}
          />{/key}{/key}
          <output data-testid="scope-ref-value">{textOf(sc.refDb)}</output>
          {#key rendererContext}{#key finiteAppearance.action}<ControlInstrumentRendererHost
            seat={actionSeat('refDb', 5, 'scope reference')} renderer={finiteAppearance.action}
          />{/key}{/key}
        </div>
      {/if}

      {#each TOGGLES as [field, label] (field)}
        {#if sc[field].availability.structural}
          {#key rendererContext}{#key finiteAppearance.toggle}<ControlInstrumentRendererHost
            seat={toggleSeat(field, label)} renderer={finiteAppearance.toggle}
          />{/key}{/key}
        {/if}
      {/each}
    {:else}
      <!-- Native appearance: ONE always-visible row + the ⋯ More panel. -->
      <div class="scope-controls-row" data-testid="scope-controls-row">
        {#if sc.mode.availability.structural}
          {@const modeQuick = choiceInstrument('mode', QUICK_MODE.map(([v]) => v))}
          <span class="scope-key-group" role="radiogroup" aria-label="Scope mode" data-testid="scope-mode-row">
            {#each QUICK_MODE as [v, label] (v)}
              <ScopeFlatKey kind="choice" {label} testid="scope-mode-row-{v}"
                lit={modeKnown === undefined ? null : modeKnown === v}
                disabled={!modeQuick.available} onclick={() => modeQuick.invoke(v)} width="42px" />
            {/each}
          </span>
        {/if}

        {#if spanApplicable && sc.span.availability.structural}
          {@const spanDown = spanInstrument(-1)}
          {@const spanUp = spanInstrument(1)}
          <span class="scope-stepper" data-overflow="span" data-testid="scope-span">
            <span class="scope-name">SPAN</span>
            <button type="button" class="scope-step-key" aria-label="Decrease scope span"
              disabled={!spanDown.available} onclick={() => spanDown.invoke()}>&#8249;</button>
            <output class="scope-step-value" data-testid="scope-span-value">{usable(sc.span) ? (SPAN_LABELS[numberOf(sc.span, 3)] ?? '') : ''}</output>
            <button type="button" class="scope-step-key" aria-label="Increase scope span"
              disabled={!spanUp.available} onclick={() => spanUp.invoke()}>&#8250;</button>
          </span>
        {/if}

        {#if sc.refDb.availability.structural}
          {@const refDown = refInstrument(-5)}
          {@const refUp = refInstrument(5)}
          <span class="scope-stepper" data-overflow="ref" data-testid="scope-ref">
            <span class="scope-name">REF</span>
            <button type="button" class="scope-step-key" aria-label="Decrease scope reference"
              disabled={!refDown.available} onclick={() => refDown.invoke()}>&#8249;</button>
            <output class="scope-step-value" data-testid="scope-ref-value">{sc.refDb.reading.status === 'known' ? String(sc.refDb.reading.value) : ''}</output>
            <button type="button" class="scope-step-key" aria-label="Increase scope reference"
              disabled={!refUp.available} onclick={() => refUp.invoke()}>&#8250;</button>
          </span>
        {/if}

        {#if sc.hold.availability.structural}
          {@const hold = toggleInstrument('hold')}
          <span class="scope-key-group" data-overflow="hold">
            <ScopeFlatKey label="HOLD" testid="scope-hold" width="52px"
              lit={hold.confirmed ?? null} disabled={!hold.available} onclick={() => hold.invoke()} />
          </span>
        {/if}

        {#if sc.receiver.availability.structural}
          {@const receiverChoice = choiceInstrument('receiver', [0, 1])}
          {@const receiverValue = valueOf(sc.receiver)}
          <span class="scope-key-group" role="radiogroup" aria-label="Scope receiver" data-overflow="receiver" data-testid="scope-receiver">
            {#each [[0, 'MAIN'], [1, 'SUB']] as const as [v, label] (v)}
              <ScopeFlatKey kind="choice" {label} testid="scope-receiver-{v}" width="48px"
                lit={receiverValue === undefined ? null : receiverValue === v}
                disabled={!receiverChoice.available} onclick={() => receiverChoice.invoke(v)} />
            {/each}
          </span>
        {/if}

        <span class="scope-more-anchor">
          <ScopeFlatKey kind="action" label="⋯" ariaLabel="More scope controls"
            ariaExpanded={moreOpen} lit={moreOpen ? true : null} testid="scope-more" width="30px"
            bind:element={moreKeyEl} onclick={() => { moreOpen = !moreOpen; }} />
          {#if moreOpen}
            <ScopeMorePanel onClose={() => { moreOpen = false; }} anchor={moreKeyEl}>
              {#snippet radioHeld()}
                <!-- Narrow-width overflow copies, shown by the container queries below. -->
                <div class="scope-more-overflow" data-testid="scope-more-overflow">
                  {#if sc.receiver.availability.structural}
                    {@const overflowReceiver = choiceInstrument('receiver', [0, 1])}{@const overflowReceiverValue = valueOf(sc.receiver)}
                    <div class="scope-more-row" role="radiogroup" aria-label="Scope receiver" data-overflow="receiver" data-testid="scope-overflow-receiver">
                      <span class="scope-name">MAIN/SUB</span>
                      {#each [[0, 'MAIN'], [1, 'SUB']] as const as [v, label] (v)}
                        <ScopeFlatKey kind="choice" {label} testid={`scope-overflow-receiver-${v}`} width="48px"
                          lit={overflowReceiverValue === undefined ? null : overflowReceiverValue === v}
                          disabled={!overflowReceiver.available} onclick={() => overflowReceiver.invoke(v)} />
                      {/each}
                    </div>
                  {/if}
                  {#if sc.hold.availability.structural}
                    {@const overflowHold = toggleInstrument('hold')}
                    <div class="scope-more-row" data-overflow="hold" data-testid="scope-overflow-hold">
                      <ScopeFlatKey label="HOLD" testid="scope-overflow-hold-key" width="52px"
                        lit={overflowHold.confirmed ?? null} disabled={!overflowHold.available} onclick={() => overflowHold.invoke()} />
                    </div>
                  {/if}
                  {#if sc.refDb.availability.structural}
                    {@const overflowRefDown = refInstrument(-5)}{@const overflowRefUp = refInstrument(5)}
                    <div class="scope-more-row scope-stepper" data-overflow="ref" data-testid="scope-overflow-ref">
                      <span class="scope-name">REF</span>
                      <button type="button" class="scope-step-key" aria-label="Decrease scope reference" disabled={!overflowRefDown.available} onclick={() => overflowRefDown.invoke()}>&#8249;</button>
                      <output class="scope-step-value" data-testid="scope-overflow-ref-value">{sc.refDb.reading.status === 'known' ? String(sc.refDb.reading.value) : ''}</output>
                      <button type="button" class="scope-step-key" aria-label="Increase scope reference" disabled={!overflowRefUp.available} onclick={() => overflowRefUp.invoke()}>&#8250;</button>
                    </div>
                  {/if}
                  {#if spanApplicable && sc.span.availability.structural}
                    {@const overflowSpanDown = spanInstrument(-1)}{@const overflowSpanUp = spanInstrument(1)}
                    <div class="scope-more-row scope-stepper" data-overflow="span" data-testid="scope-overflow-span">
                      <span class="scope-name">SPAN</span>
                      <button type="button" class="scope-step-key" aria-label="Decrease scope span" disabled={!overflowSpanDown.available} onclick={() => overflowSpanDown.invoke()}>&#8249;</button>
                      <output class="scope-step-value" data-testid="scope-overflow-span-value">{usable(sc.span) ? (SPAN_LABELS[numberOf(sc.span, 3)] ?? '') : ''}</output>
                      <button type="button" class="scope-step-key" aria-label="Increase scope span" disabled={!overflowSpanUp.available} onclick={() => overflowSpanUp.invoke()}>&#8250;</button>
                    </div>
                  {/if}
                </div>

                {#if sc.mode.availability.structural}
                  {@const modeChoice = choiceInstrument('mode', MODE_BUTTONS.map(([v]) => v))}
                  <div class="scope-more-row" role="radiogroup" aria-label="Scope mode" data-testid="scope-mode">
                    <span class="scope-name">MODE</span>
                    {#each MODE_BUTTONS as [v, label] (v)}
                      <ScopeFlatKey kind="choice" {label} testid="scope-mode-{v}" width="44px"
                        lit={modeKnown === undefined ? null : modeKnown === v}
                        disabled={!modeChoice.available} onclick={() => modeChoice.invoke(v)} />
                    {/each}
                  </div>
                {/if}

                {#each MORE_CHOICES as [field, ariaLabel, shortName, options] (field)}
                  {#if (field !== 'edge' || edgeApplicable) && sc[field].availability.structural}
                    {@const choice = choiceInstrument(field, options.map(([v]) => v))}
                    {@const current = valueOf(sc[field])}
                    <div class="scope-more-row" role="radiogroup" aria-label={ariaLabel} data-testid={`scope-${field}`}>
                      <span class="scope-name">{shortName}</span>
                      {#each options as [v, optLabel] (v)}
                        <ScopeFlatKey kind="choice" label={optLabel} testid={`scope-${field}-${v}`} width="52px"
                          lit={current === undefined ? null : current === v}
                          disabled={!choice.available} onclick={() => choice.invoke(v)} />
                      {/each}
                    </div>
                  {/if}
                {/each}

                {#if sc.speed.availability.structural}
                  {@const speedDown = speedInstrument(-1)}
                  {@const speedUp = speedInstrument(1)}
                  <div class="scope-more-row scope-stepper" data-testid="scope-speed">
                    <span class="scope-name">SPEED</span>
                    <button type="button" class="scope-step-key" aria-label="Decrease scope speed"
                      disabled={!speedDown.available} onclick={() => speedDown.invoke()}>&#8249;</button>
                    <output class="scope-step-value" data-testid="scope-speed-value">{usable(sc.speed) ? (SPEED_LABELS[numberOf(sc.speed, 1)] ?? '') : ''}</output>
                    <button type="button" class="scope-step-key" aria-label="Increase scope speed"
                      disabled={!speedUp.available} onclick={() => speedUp.invoke()}>&#8250;</button>
                  </div>
                {/if}

                {#each MORE_TOGGLES as [field, label, width] (field)}
                  {#if sc[field].availability.structural}
                    {@const toggle = toggleInstrument(field)}
                    <div class="scope-more-row" data-testid={`scope-${field}-row`}>
                      <ScopeFlatKey {label} testid={`scope-${field}`} {width}
                        lit={toggle.confirmed ?? null} disabled={!toggle.available} onclick={() => toggle.invoke()} />
                    </div>
                  {/if}
                {/each}

                <!-- MOR-2545 PR2: the host's screen-only group below the
                     radio-held group — placed here, owned by the toolbar. -->
                {#if moreScreen}
                  <div class="scope-more-screen" data-testid="scope-more-screen">
                    {@render moreScreen(() => { moreOpen = false; })}
                  </div>
                {/if}
              {/snippet}
            </ScopeMorePanel>
          {/if}
        </span>
      </div>
    {/if}
  </section>
{/if}

<style>
  /* Structure only — a design language owns colour (MOR-977, forced-colors). */
  /* Query container for the row's overflow below; contain-intrinsic-inline-size
     keeps shrink-to-fit hosts (the sdr-test toolbar) from collapsing to 0. */
  .scope-controls-surface { display: block; min-width: 0; container-type: inline-size; container-name: scope-controls; contain-intrinsic-inline-size: 530px; }

  /* The ONE always-visible row: never wraps, never reflows a key's box. */
  .scope-controls-row {
    display: flex;
    flex-wrap: nowrap;
    align-items: center;
    gap: 2px;
    min-width: 0;
  }

  /* Narrow-width overflow (MOR-2545, coordinator decision): below each band
     the row's DIRECT child (`>` — the More panel sits inside the row's ⋯
     anchor) with that hook hides and its More copy shows. Hide-first:
     MAIN/SUB, HOLD, REF, SPAN; CTR/FIX and ⋯ always stay. Bands measured in
     Chromium at the widest case (dual receiver, CTR). The REF band is 372:
     the no-HOLD row fits at 371.3 px, and a 390 px phone's content width is
     exactly 374 — 372 keeps REF on the row there. */
  .scope-more-overflow { display: contents; }
  .scope-more-overflow > [data-overflow] { display: none; }
  @container scope-controls (max-width: 529px) { .scope-controls-row > [data-overflow='receiver'] { display: none; } .scope-more-overflow > [data-overflow='receiver'] { display: flex; } }
  @container scope-controls (max-width: 429px) { .scope-controls-row > [data-overflow='hold'] { display: none; } .scope-more-overflow > [data-overflow='hold'] { display: flex; } }
  @container scope-controls (max-width: 372px) { .scope-controls-row > [data-overflow='ref'] { display: none; } .scope-more-overflow > [data-overflow='ref'] { display: flex; } }
  @container scope-controls (max-width: 299px) { .scope-controls-row > [data-overflow='span'] { display: none; } .scope-more-overflow > [data-overflow='span'] { display: flex; } }

  .scope-key-group { display: inline-flex; flex: none; align-items: center; }

  .scope-stepper {
    display: inline-flex;
    flex: none;
    align-items: center;
    gap: 1px;
    white-space: nowrap;
  }

  .scope-name {
    flex: none;
    padding: 0 3px;
    font-size: 10px;
    font-weight: 600;
    letter-spacing: 0.05em;
    white-space: nowrap;
    color: var(--dl-vfo-unlit-text, var(--v2-text-muted, #5a6875));
  }

  .scope-step-key {
    appearance: none;
    background: transparent;
    border: none;
    padding: 0;
    margin: 0;
    flex: none;
    width: 18px;
    height: 22px;
    font-family: inherit;
    font-size: 13px;
    font-variant-numeric: tabular-nums;
    color: var(--dl-vfo-unlit-text, var(--v2-text-muted, #5a6875));
    cursor: pointer;
  }

  .scope-step-key:disabled { cursor: not-allowed; }

  /* Reserved width in EVERY state: an unread value is EMPTY, never a
     placeholder, and the stepper's box does not change size. */
  .scope-step-value {
    display: inline-flex;
    align-items: center;
    justify-content: center;
    flex: none;
    min-width: 7ch;
    height: 22px;
    font-size: 12px;
    font-variant-numeric: tabular-nums;
    color: var(--vfo-lamp-color, var(--dl-vfo-red-text, var(--dl-vfo-red, #e2362c)));
  }

  /* The ⋯ key's anchor: the More panel positions itself against this. */
  .scope-more-anchor {
    position: relative;
    display: inline-flex;
    flex: none;
  }

  .scope-more-row {
    display: flex;
    align-items: center;
    gap: 2px;
    white-space: nowrap;
  }

  /* The host's screen-only group: visually a SECOND group below the
     radio-held one (divider + its own rows), inside the same panel. */
  .scope-more-screen {
    display: flex;
    flex-direction: column;
    gap: 4px;
    margin-top: 4px;
    padding-top: 8px;
    border-top: 1px solid var(--v2-border, #2a2a3e);
  }

  /* External finite appearance (pre-MOR-2545 stacked groups). */
  .scope-row { display: flex; flex-wrap: wrap; align-items: baseline; gap: 0.5rem; margin: 0; }
</style>
