<!--
  Semantic RF-front-end surface (MOR-1306, vocabulary slice 6B).

  Presentation only. It renders the MOR-1262 decomposition family 11
  `rfFrontEnd` fact group (MOR-1292/MOR-1293) — preamp, attenuator, RF gain,
  squelch, DIGI-SEL, IP+ — and emits control intents as callbacks. It holds
  no state, consults no controller and issues no command directly (v3 ADR
  invariant 11), same doctrine as `TxAuxSurface`/`RxAudioSurface`.

  CARRY-FORWARDS (binding, from the MOR-1292/MOR-1293 review rulings — see
  `radio-view-model.ts`'s `RfFrontEndViewModel` doc comment for the fact-layer
  half of each):

  (1) FRESHNESS. A stale/unobserved reading renders `UNKNOWN_TEXT`, never the
      last-known value. This is inherited "for free" from `usable`/`textOf`
      gating on `reading.status === 'known'` — the same idiom
      `TxAuxSurface`/`RxAudioSurface` use — as long as nothing here adds a
      fallback that reads `rf.<field>.reading.value` outside that gate. There
      is deliberately no "show it anyway, marked stale" branch: the fact
      layer already collapses a stale reading to `unknown` before this file
      ever sees it (`radio-view-model-adapter.ts`'s `txAuxField`), so widening
      here would silently reopen exactly the gate MOR-1292 closed.
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
-->
<script module lang="ts">
  import type { DisabledReasonCode, RfFrontEndField } from './radio-view-model';
  import { pressedOf } from './pressed-of';
  import { formatKnownLevel } from './format-level';

  /** The one rendering of "not read". Never 0, never the last value. */
  export const UNKNOWN_TEXT = '?';

  /** Usable ⇔ the radio HAS it, it is readable NOW, and it was actually read. */
  export const usable = (f: RfFrontEndField<unknown>): boolean =>
    f.availability.structural && f.availability.operational && f.reading.status === 'known';
  /** Honest text: an unread fact reads as unknown, never as a stale value. */
  export const textOf = (f: RfFrontEndField<unknown>): string =>
    f.reading.status === 'known' ? String(f.reading.value) : UNKNOWN_TEXT;
  /** Same freshness discipline as `textOf`, but a KNOWN level reading is
   *  formatted against its declared `[min, max]` domain (MOR-1447) instead of
   *  `String()`-ing the raw wire fraction. */
  const levelTextOf = (f: RfFrontEndField<number>, min: number, max: number): string =>
    f.reading.status === 'known' ? formatKnownLevel(f.reading.value, min, max) : UNKNOWN_TEXT;
  const isValue = (f: RfFrontEndField<unknown>, value: unknown): boolean =>
    f.reading.status === 'known' && f.reading.value === value;

  /** `[field, label, min, max, step]`, RAW wire units (0..1 fractions, no
   *  rescale) — same discipline as `TxAuxSurface.TX_AUX_LEVELS`. */
  export const RF_FRONT_END_LEVELS = [
    ['rfGain', 'RF gain', 0, 1, 0.01],
    ['squelch', 'Squelch', 0, 1, 0.01],
  ] as const;
  /** `[field, label]` on/off controls. */
  export const RF_FRONT_END_TOGGLES = [
    ['digiSel', 'DIGI-SEL'], ['ipPlus', 'IP+'],
  ] as const;
  export type RfFrontEndLevelField = (typeof RF_FRONT_END_LEVELS)[number][0];
  export type RfFrontEndToggleField = (typeof RF_FRONT_END_TOGGLES)[number][0];

  /** Carry-forward 4: keyed by the generic CODE, never by a peer-control
   *  name — the mutex label must read the same whichever control triggers
   *  it. */
  export const DISABLED_REASON_LABEL: Partial<Record<DisabledReasonCode, string>> = {
    'mutually-exclusive-control': 'disabled: another control is active',
  };
</script>

<script lang="ts">
  import type { RadioViewModel } from './radio-view-model';

  interface Props {
    view: RadioViewModel;
    onPreampChange?: (level: number) => void;
    onAttenuatorChange?: (db: number) => void;
    onLevelChange?: (field: RfFrontEndLevelField, value: number) => void;
    /** `next` is the FLIPPED value, computed here from the observed reading —
     *  `makeRfFrontEndHandlers().onDigiSelToggle`/`onIpPlusToggle` take an
     *  explicit `on: boolean`, unlike the argument-less vox/comp/mon toggles
     *  `TxAuxSurface` composes, so the surface (which holds the fact) computes
     *  it rather than the wiring re-reading raw state to derive it. */
    onToggle?: (field: RfFrontEndToggleField, next: boolean) => void;
  }
  let { view, onPreampChange, onAttenuatorChange, onLevelChange, onToggle }: Props = $props();

  let rf = $derived(view.rfFrontEnd);
  /** Carry-forwards 2/3: matched on the DOTTED field path, never re-derived
   *  from a raw DIGI-SEL read — the fact layer already decided this. */
  let preMutex = $derived(
    view.disabledReasons.find((reason) => reason.field === 'rfFrontEnd.preamp') ?? null,
  );

  function changePreamp(level: number): void {
    if (rf && usable(rf.preamp) && preMutex === null) onPreampChange?.(level);
  }
  function changeAttenuator(db: number): void {
    if (rf && usable(rf.attenuator)) onAttenuatorChange?.(db);
  }
  function changeLevel(field: RfFrontEndLevelField, value: number): void {
    if (rf && usable(rf[field])) onLevelChange?.(field, value);
  }
  function toggle(field: RfFrontEndToggleField): void {
    const f = rf?.[field];
    if (f && usable(f) && f.reading.status === 'known') onToggle?.(field, !f.reading.value);
  }
</script>

{#if rf}
  <section class="rf-front-end-surface" data-testid="rf-front-end-surface" aria-label="RF front end">
    {#if rf.preamp.availability.structural}
      <div
        class="rf-front-end-row" role="radiogroup" aria-label="Preamp"
        data-testid="rf-front-end-preamp"
        data-observed={usable(rf.preamp)}
        data-disabled-reason={preMutex?.code}
      >
        {#each rf.preValues as value (value)}
          <button
            type="button" role="radio" class="rf-front-end-choice"
            data-testid={`rf-front-end-preamp-${value}`}
            aria-checked={isValue(rf.preamp, value)}
            disabled={!usable(rf.preamp) || preMutex !== null}
            onclick={() => changePreamp(value)}
          >{value}</button>
        {/each}
        <output data-testid="rf-front-end-preamp-value">{textOf(rf.preamp)}</output>
        {#if preMutex}
          <p data-testid="rf-front-end-preamp-mutex-reason">{DISABLED_REASON_LABEL[preMutex.code]}</p>
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
            aria-checked={isValue(rf.attenuator, value)}
            disabled={!usable(rf.attenuator)}
            onclick={() => changeAttenuator(value)}
          >{value} dB</button>
        {/each}
        <output data-testid="rf-front-end-attenuator-value">{textOf(rf.attenuator)}</output>
      </div>
    {/if}

    {#each RF_FRONT_END_LEVELS as [field, label, min, max, step] (field)}
      {#if rf[field].availability.structural}
        <label
          class="rf-front-end-level" data-testid={`rf-front-end-${field}`}
          data-observed={usable(rf[field])}
        >
          <span class="rf-front-end-name">{label}</span>
          <input
            type="range" {min} {max} {step}
            value={rf[field].reading.status === 'known' ? rf[field].reading.value : 0}
            disabled={!usable(rf[field])}
            oninput={(event) => changeLevel(field, event.currentTarget.valueAsNumber)}
          />
          <output>{levelTextOf(rf[field], min, max)}</output>
        </label>
      {/if}
    {/each}

    {#each RF_FRONT_END_TOGGLES as [field, label] (field)}
      {#if rf[field].availability.structural}
        <button
          type="button" class="rf-front-end-toggle"
          data-testid={`rf-front-end-${field}`} data-observed={usable(rf[field])}
          aria-pressed={pressedOf(rf[field])}
          disabled={!usable(rf[field])}
          onclick={() => toggle(field)}
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
  .rf-front-end-level { display: flex; align-items: baseline; gap: 0.5rem; }
  .rf-front-end-name { min-width: 6ch; }
  .rf-front-end-choice[aria-checked='true'] { font-weight: 700; }
  [data-observed='false'] { font-style: italic; }
  button:disabled, input:disabled { cursor: not-allowed; }
</style>
