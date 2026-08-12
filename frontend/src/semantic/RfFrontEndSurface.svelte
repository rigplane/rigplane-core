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

  PENDING AFFORDANCE (MOR-1441 leg 2). `pendingPreamp` is a plain, command-
  bus-blind display prop, same "read at the wiring seam" precedent as leg
  1's `pendingFrequencyHz`. It never touches the MOR-1447 combined-knob/
  change-guard machinery above (`combinedNormX`/`changeCombined` read only
  confirmed `rf.rfGain`/`rf.squelch`) — preamp is a disjoint field. Marks the
  targeted preamp CHOICE distinctly; `isValue`/`aria-checked` keep reading
  `rf.preamp`'s CONFIRMED reading exclusively, so a click while pending still
  dispatches the CLICKED (explicit) value.
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

  /** `[field, label, min, max, step]`. The radio's own normalized 0..1
   *  reading (a wire-protocol FRACTION, not the raw 0-255 wire unit) — same
   *  discipline as `TxAuxSurface.TX_AUX_LEVELS`. Rescaled to the raw 0-255
   *  integer `set_rf_gain`/`set_squelch` require at the wiring seam
   *  (`SemanticRadioSurfaces.svelte`'s `RF_FRONT_END_LEVEL_INTENT`, MOR-1447),
   *  never inside this presentation-only file. */
  export const RF_FRONT_END_LEVELS = [
    ['rfGain', 'RF gain', 0, 1, 0.01],
    ['squelch', 'Squelch', 0, 1, 0.01],
  ] as const;
  /** The combined-knob domain (MOR-1447 leg 2): both `rfGain` and `squelch`
   *  share this [0,1] range, an invariant of the combined control model —
   *  `dualParamValuesFromNormX`/`dualParamNormXFromValues` map ONE knob
   *  position onto both fields over the SAME domain. */
  const [, , RF_SQL_MIN, RF_SQL_MAX, RF_SQL_STEP] = RF_FRONT_END_LEVELS[0];
  /** Profile-declared control model (MOR-1447 leg 2, data-driven from
   *  `[capabilities].rf_sql_control_model` in the rig TOML — never a
   *  vendor/model-name branch in code). `'separate'` is the default: two
   *  independent sliders, unchanged from leg 1. */
  export type RfSqlControlModel = 'separate' | 'combined';
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
  import { t } from '$lib/i18n';
  import type { RadioViewModel } from './radio-view-model';
  import {
    dualParamValuesFromNormX,
    dualParamNormXFromValues,
  } from '../components-v2/controls/value-control/value-control-core';

  interface Props {
    view: RadioViewModel;
    /** Icom-style single-knob RF/SQL (MOR-1447 leg 2). Defaults to
     *  `'separate'` — the two independent sliders leg 1 fixed. */
    controlModel?: RfSqlControlModel;
    /** MOR-1441 leg 2 — the freshest in-flight `set_preamp` target for the
     *  active receiver, DISPLAY ONLY (see the file header). `null` when
     *  nothing is pending. */
    pendingPreamp?: number | null;
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
  let {
    view, controlModel = 'separate', pendingPreamp = null,
    onPreampChange, onAttenuatorChange, onLevelChange, onToggle,
  }: Props = $props();

  const pendingPreampId = $props.id();
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
  /** MOR-1447 leg 2: both fields structurally present AND the profile
   *  declares the combined knob — the gate for rendering ONE control instead
   *  of two. A profile that declares `'combined'` but only observes one of
   *  the pair structurally falls back to the two-slider rendering below
   *  (the per-field `{#if rf[field].availability.structural}` gates still
   *  apply), same "render what's actually there" discipline as every other
   *  field in this file. */
  let combinedUsable = $derived(
    controlModel === 'combined'
    && !!rf?.rfGain.availability.structural
    && !!rf?.squelch.availability.structural,
  );
  const knownOr = (f: RfFrontEndField<number> | undefined, fallback: number): number =>
    f && f.reading.status === 'known' ? f.reading.value : fallback;
  /** Readback projection (MOR-1447 leg 2): the honest inverse of the
   *  hardware knob. Ported verbatim from `dualParamNormXFromValues`
   *  (`components-v2/controls/value-control/value-control-core.ts`) — the
   *  same math `DualParamRenderer.svelte` already draws with. Ambiguity
   *  handling is inherited from that function: if SQL reads above its
   *  minimum, the knob is projected to the right leg (RF forced to max) —
   *  the physical knob genuinely cannot express "RF below max AND SQL above
   *  min" at once, so this is the one honest reading, not a guess.
   */
  let combinedNormX = $derived(
    dualParamNormXFromValues(
      knownOr(rf?.rfGain, RF_SQL_MIN),
      knownOr(rf?.squelch, RF_SQL_MIN),
      RF_SQL_MIN,
      RF_SQL_MAX,
    ),
  );
  function changeCombined(normX: number): void {
    // Both halves must be independently usable, not merely structurally
    // present (mirrors carry-forward 2/3's "the handler itself refuses to
    // emit, independent of `disabled`" discipline): one physical knob must
    // not silently half-write the pair — e.g. move RF while SQL is degraded
    // and stays untouched, desyncing what looks like a single control.
    if (!rf || !usable(rf.rfGain) || !usable(rf.squelch)) return;
    const { rf: nextRf, sql: nextSql } = dualParamValuesFromNormX(
      normX, RF_SQL_MIN, RF_SQL_MAX, RF_SQL_STEP,
    );
    // Per-field change guard, mirroring `DualParamRenderer.svelte`'s
    // `emitPair` (`value-control-core.ts`'s companion component — only emits
    // a field that actually moved). Without this, every input event
    // unconditionally re-sends BOTH fields — a left-leg drag spams redundant
    // `set_squelch(0)` and a right-leg drag spams redundant `set_rf_gain(255)`
    // on every tick, roughly doubling the CI-V write rate versus both the
    // real hardware knob and the leg-1 two-slider path. On the live serial
    // IC-7300 gate radio that write-rate doubling is the queue-lag/"Commander
    // stopped" hazard shape.
    if (rf.rfGain.reading.status === 'known' && nextRf !== rf.rfGain.reading.value) {
      changeLevel('rfGain', nextRf);
    }
    if (rf.squelch.reading.status === 'known' && nextSql !== rf.squelch.reading.value) {
      changeLevel('squelch', nextSql);
    }
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
        data-preamp-status={pendingPreamp !== null ? 'pending' : 'confirmed'}
        aria-describedby={pendingPreamp !== null ? pendingPreampId : undefined}
      >
        {#each rf.preValues as value (value)}
          <button
            type="button" role="radio" class="rf-front-end-choice"
            data-testid={`rf-front-end-preamp-${value}`}
            aria-checked={isValue(rf.preamp, value)}
            data-pending={pendingPreamp === value}
            disabled={!usable(rf.preamp) || preMutex !== null}
            onclick={() => changePreamp(value)}
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
            aria-checked={isValue(rf.attenuator, value)}
            disabled={!usable(rf.attenuator)}
            onclick={() => changeAttenuator(value)}
          >{value} dB</button>
        {/each}
        <output data-testid="rf-front-end-attenuator-value">{textOf(rf.attenuator)}</output>
      </div>
    {/if}

    {#if combinedUsable}
      <label
        class="rf-front-end-level" data-testid="rf-front-end-rf-sql"
        data-observed={usable(rf.rfGain) && usable(rf.squelch)}
      >
        <span class="rf-front-end-name">RF/SQL</span>
        <input
          type="range" min={RF_SQL_MIN} max={RF_SQL_MAX} step={RF_SQL_STEP}
          value={combinedNormX}
          disabled={!usable(rf.rfGain) || !usable(rf.squelch)}
          oninput={(event) => changeCombined(event.currentTarget.valueAsNumber)}
        />
        <output
          >{levelTextOf(rf.rfGain, RF_SQL_MIN, RF_SQL_MAX)} / {levelTextOf(rf.squelch, RF_SQL_MIN, RF_SQL_MAX)}</output
        >
      </label>
    {:else}
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
    {/if}

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
  /* MOR-1441 leg 2 — same pending doctrine as `FilterSurface`'s
     `.filter-choice[data-pending='true']`: structural marker, never
     color-only. */
  .rf-front-end-choice[data-pending='true'] { font-style: italic; opacity: 0.75; }
  .sr-only {
    position: absolute; width: 1px; height: 1px; padding: 0; margin: -1px;
    overflow: hidden; clip: rect(0, 0, 0, 0); white-space: nowrap; border: 0;
  }
</style>
