<!--
  Semantic RF-front-end surface (MOR-1306, vocabulary slice 6B).

  Presentation only. It renders the MOR-1262 decomposition family 11
  `rfFrontEnd` fact group (MOR-1292/MOR-1293) — preamp, attenuator, RF gain,
  squelch, DIGI-SEL, IP+ — and emits finite-control intents as callbacks.
  Persistent RF/SQL behavior arrives through required host-owned handles;
  this remainder consults no controller and issues no command directly.

  CARRY-FORWARDS (binding, from the MOR-1292/MOR-1293 review rulings — see
  `radio-view-model.ts`'s `RfFrontEndViewModel` doc comment for the fact-layer
  half of each):

  (1) FRESHNESS. A stale/unobserved reading renders `UNKNOWN_TEXT`, never the
      last-known value. This is inherited "for free" from `usable`/`textOf`
      gating on `reading.status === 'known'` — the same idiom
      `TxAuxSurface`/`RxAudioSurface` use — as long as nothing here adds a
      fallback that reads `rf.<field>.reading.value` outside that gate. RF/SQL
      level truth is rendered by the required instrument handles.
  (2)+(3) THE PREAMP MUTEX. PRE is genuinely disabled while DIGI-SEL is
      unobserved, by design (MOR-479 hardware mutex, IC-7610). Rendered as a
      disabled control WITH AN EXPLANATION, read from `view.disabledReasons`
      matched on the DOTTED path `'rfFrontEnd.preamp'` — never a bespoke
      `preDisabled` boolean, and never `?? false` (the shipped v2 fallback
      that would silently re-enable PRE the moment DIGI-SEL goes unobserved).
      The mutex disables the control on TOP of its own field usability: a
      positively-known, positively-usable preamp reading is still inert while
      the mutex entry is present.
  (4) The explanation is keyed off `DisabledReasonCode`, not off "DIGI-SEL" —
      `'mutually-exclusive-control'` is deliberately generic (reusable by a
      future CW APF/TPF mutex, MOR-1293's own note), so `MUTEX_LABEL` names
      the SHAPE of the conflict, never this radio's specific peer control.

  Two-level availability (MOR-977/1256), same as every sibling surface:
  `structural: false` renders NOTHING for that field — "this radio has no
  squelch" is a different claim from "squelch was never observed", which
  renders present-and-disabled.

  PENDING AFFORDANCE (MOR-1441 leg 2). `pendingPreamp` is a plain, command-
  bus-blind display prop, same "read at the wiring seam" precedent as leg
  1's `pendingFrequencyHz`. It never touches the disjoint combined RF/SQL
  pair binding. Marks the
  targeted preamp CHOICE distinctly; the preamp binding's `aria-checked` keeps reading
  `rf.preamp`'s CONFIRMED reading exclusively, so a click while pending still
  dispatches the CLICKED (explicit) value.
-->
<script module lang="ts">
  import type { DisabledReasonCode, RfFrontEndField } from './radio-view-model';
  export {
    RF_FRONT_END_LEVELS,
    type RfFrontEndLevelField,
    type RfSqlControlModel,
  } from './rf-front-end-instruments';

  /** The one rendering of "not read". Never 0, never the last value. */
  export const UNKNOWN_TEXT = '?';

  /** Usable ⇔ the radio HAS it, it is readable NOW, and it was actually read. */
  export const usable = (f: RfFrontEndField<unknown>): boolean =>
    f.availability.structural && f.availability.operational && f.reading.status === 'known';
  /** Honest text: an unread fact reads as unknown, never as a stale value. */
  export const textOf = (f: RfFrontEndField<unknown>): string =>
    f.reading.status === 'known' ? String(f.reading.value) : UNKNOWN_TEXT;
  /** `[field, label]` on/off controls. */
  export const RF_FRONT_END_TOGGLES = [
    ['digiSel', 'DIGI-SEL'], ['ipPlus', 'IP+'],
  ] as const;
  export type RfFrontEndToggleField = (typeof RF_FRONT_END_TOGGLES)[number][0];

  /** Carry-forward 4: keyed by the generic CODE, never by a peer-control
   *  name — the mutex label must read the same whichever control triggers
   *  it. */
  export const DISABLED_REASON_LABEL: Partial<Record<DisabledReasonCode, string>> = {
    'mutually-exclusive-control': 'disabled: another control is active',
  };
</script>

<script lang="ts">
  import { t } from '$lib/i18n';
  import type { RadioViewModel } from './radio-view-model';
  import type { RfFrontEndLevelHandles } from './rf-front-end-instruments';
  import {
    bindChoiceInstrument,
    bindToggleInstrument,
  } from '../primitives/control-instruments/control-instrument-behavior';

  interface Props {
    view: RadioViewModel;
    levelHandles: RfFrontEndLevelHandles;
    /** MOR-1441 leg 2 — the freshest in-flight `set_preamp` target for the
     *  active receiver, DISPLAY ONLY (see the file header). `null` when
     *  nothing is pending. */
    pendingPreamp?: number | null;
    onPreampChange?: (level: number) => void;
    onAttenuatorChange?: (db: number) => void;
    /** `next` is the FLIPPED value, computed here from the observed reading —
     *  `makeRfFrontEndHandlers().onDigiSelToggle`/`onIpPlusToggle` take an
     *  explicit `on: boolean`, unlike the argument-less vox/comp/mon toggles
     *  `TxAuxSurface` composes, so the surface (which holds the fact) computes
     *  it rather than the wiring re-reading raw state to derive it. */
    onToggle?: (field: RfFrontEndToggleField, next: boolean) => void;
  }
  let {
    view, levelHandles, pendingPreamp = null,
    onPreampChange, onAttenuatorChange, onToggle,
  }: Props = $props();

  const pendingPreampId = $props.id();
  let rf = $derived(view.rfFrontEnd);
  /** Carry-forwards 2/3: matched on the DOTTED field path, never re-derived
   *  from a raw DIGI-SEL read — the fact layer already decided this. */
  let preMutex = $derived(
    view.disabledReasons.find((reason) => reason.field === 'rfFrontEnd.preamp') ?? null,
  );

  const preampBehavior = bindChoiceInstrument<number>(() => ({
    field: rf?.preamp,
    choices: rf?.preValues ?? [],
    blocked: preMutex !== null,
    invoke: (level) => onPreampChange?.(level),
  }));
  const attenuatorBehavior = bindChoiceInstrument<number>(() => ({
    field: rf?.attenuator,
    choices: rf?.attValues ?? [],
    invoke: (db) => onAttenuatorChange?.(db),
  }));
  const toggleBehaviors = {
    digiSel: bindToggleInstrument(() => ({
      field: rf?.digiSel,
      invoke: (next) => onToggle?.('digiSel', next),
    })),
    ipPlus: bindToggleInstrument(() => ({
      field: rf?.ipPlus,
      invoke: (next) => onToggle?.('ipPlus', next),
    })),
  } satisfies Record<RfFrontEndToggleField, ReturnType<typeof bindToggleInstrument>>;

</script>

{#if rf}
  <section class="rf-front-end-surface" data-testid="rf-front-end-surface" aria-label="RF front end">
    {#if rf.preamp.availability.structural}
      <div
        class="rf-front-end-row" role="radiogroup" aria-label="Preamp"
        data-testid="rf-front-end-preamp"
        data-observed={usable(rf.preamp)}
        data-disabled-reason={preMutex?.code}
        data-preamp-status={pendingPreamp !== null ? 'pending' : 'confirmed'}
        aria-describedby={pendingPreamp !== null ? pendingPreampId : undefined}
      >
        {#each rf.preValues as value (value)}
          <button
            type="button" role="radio" class="rf-front-end-choice"
            data-testid={`rf-front-end-preamp-${value}`}
            aria-checked={preampBehavior.isSelected(value)}
            data-pending={pendingPreamp === value}
            disabled={!preampBehavior.available}
            onclick={() => preampBehavior.invoke(value)}
          >{value}</button>
        {/each}
        <output data-testid="rf-front-end-preamp-value">{textOf(rf.preamp)}</output>
        {#if preMutex}
          <p data-testid="rf-front-end-preamp-mutex-reason">{DISABLED_REASON_LABEL[preMutex.code]}</p>
        {/if}
        {#if pendingPreamp !== null}
          <span id={pendingPreampId} class="sr-only">{t('core.rfFrontEnd.preamp.pendingAnnouncement')}</span>
        {/if}
      </div>
    {/if}

    {#if rf.attenuator.availability.structural}
      <div
        class="rf-front-end-row" role="radiogroup" aria-label="Attenuator"
        data-testid="rf-front-end-attenuator" data-observed={usable(rf.attenuator)}
      >
        {#each rf.attValues as value (value)}
          <button
            type="button" role="radio" class="rf-front-end-choice"
            data-testid={`rf-front-end-attenuator-${value}`}
            aria-checked={attenuatorBehavior.isSelected(value)}
            disabled={!attenuatorBehavior.available}
            onclick={() => attenuatorBehavior.invoke(value)}
          >{value} dB</button>
        {/each}
        <output data-testid="rf-front-end-attenuator-value">{textOf(rf.attenuator)}</output>
      </div>
    {/if}

    {#if levelHandles.kind === 'combined'}
      {@render levelHandles.rfSql()}
    {:else}
      {#if rf.rfGain.availability.structural}{@render levelHandles.rfGain()}{/if}
      {#if rf.squelch.availability.structural}{@render levelHandles.squelch()}{/if}
    {/if}

    {#each RF_FRONT_END_TOGGLES as [field, label] (field)}
      {#if rf[field].availability.structural}
        {@const behavior = toggleBehaviors[field]}
        <button
          type="button" class="rf-front-end-toggle"
          data-testid={`rf-front-end-${field}`} data-observed={usable(rf[field])}
          aria-pressed={behavior.confirmed}
          disabled={!behavior.available}
          onclick={() => behavior.invoke()}
        >{label}: {textOf(rf[field])}</button>
      {/if}
    {/each}
  </section>
{/if}

<style>
  /* Structure only — a design language owns colour and must never become the
     sole state channel (MOR-977, forced-colors). */
  .rf-front-end-surface { display: flex; flex-direction: column; gap: 0.25rem; }
  .rf-front-end-row { display: flex; flex-wrap: wrap; align-items: baseline; gap: 0.5rem; margin: 0; }
  .rf-front-end-choice[aria-checked='true'] { font-weight: 700; }
  [data-observed='false'] { font-style: italic; }
  button:disabled { cursor: not-allowed; }
  /* MOR-1441 leg 2 — same pending doctrine as `FilterSurface`'s
     `.filter-choice[data-pending='true']`: structural marker, never
     color-only. */
  .rf-front-end-choice[data-pending='true'] { font-style: italic; opacity: 0.75; }
  .sr-only {
    position: absolute; width: 1px; height: 1px; padding: 0; margin: -1px;
    overflow: hidden; clip: rect(0, 0, 0, 0); white-space: nowrap; border: 0;
  }
</style>
