<!--
  Semantic CW-keyer surface (MOR-1310, vocabulary slice 9B) — SAFETY-CRITICAL.

  Presentation only. It renders the MOR-1296 `cwKeyer` fact group — break-in
  posture (+delay), keyer speed, CW pitch, reverse paddle, APF and the twin-peak
  filter — and emits control intents as callbacks. It holds no state, consults
  no controller and owns no TX authority (v3 ADR invariant 11).

  SAFETY. Five rules govern this file and nothing may relax them:

  (1) NOT A KEY PATH. Break-in KEYS THE TRANSMITTER, but this surface never
      keys it: exactly one `<RxTxSurface>` remains the key/unkey authority
      (MOR-1262 decomposition R9). Every intent below is a SETTING intent
      (`set_break_in`, `set_cw_pitch`, …); nothing here takes a TX lease, sends
      a PTT command or asks for a carrier. `CwPanel.svelte`'s AUTO TUNE button
      (`cw_auto_tune`) is deliberately ABSENT for the same reason ATU TUNE is
      absent from the facts (MOR-1244 precedent): state may be carried, a
      transmit-causing control may not. Pinned behaviourally, not asserted.

  (2) BREAK-IN OBEYS THE ONE PERMIT, FAIL-CLOSED. Arming break-in is gated on
      `view.txPermit` — the model's SINGLE authoritative live-TX-target permit,
      the same one `deriveTxCapabilities` gives the App TX authority. It is
      READ, never re-derived: no `getFrequencyPermit` call, no band-plan
      lookup, no second permit. Anything other than a positively `'allowed'`
      permit — denied, ranges-unconfigured, tx-target-unknown — disables every
      break-in choice, `unknown` INCLUDED. That over-disable is deliberate
      (MOR-1296 O2) and must never be "fixed" back to v2's optimism.

      Including "break-in OFF". Unlike RxTxSurface's unkey — which is never
      gated because it STOPS transmission — `set_break_in 0` is an ordinary
      setting command, not an emergency stop: this UI holds no key line, and
      the operator's emergency exit stays the ungated unkey action.

  (3) THE REASON IS RENDERED, NOT SWALLOWED. `validateRadioViewModel` refuses a
      model that carries a structurally-available `breakIn` under a non-allowed
      permit with no recorded `disabledReasons` entry, so the explanation always
      exists — this surface reads it (`out-of-band` / `capability-unavailable` /
      `tx-target-unknown`) rather than re-deriving it or leaving the operator a
      dead control with no cause.

  (4) THIS GROUP IS NOT UNIFORMLY "CW". `twinPeak` is an RTTY control living in
      the CW family for v2 reasons (MOR-1296 O1), and its
      `mutually-exclusive-control` reason is rendered with RTTY named — as is
      APF's with CW named. Presenting the block as plain "CW" would leave the
      operator a permanently-disabled control with no explanation.

  (5) UNKNOWN IS RENDERED AS UNKNOWN. `formatBreakIn` in v2 falls back to 'OFF'
      for an unrecognised mode; slice 9A degrades it to `unknown` instead,
      because an unreadable break-in state must never present as "the key is
      safe". `breakInPosture` therefore groups `unknown` WITH `armed`, never
      with `off`.
-->
<script module lang="ts">
  import type { BreakInMode, CwKeyerField, DisabledReasonCode } from './radio-view-model';
  import { pressedOf } from './pressed-of';

  /** Break-in as THREE ABSOLUTE choices, `[label, wire mode]`. Absolute, not a
   *  toggle: a toggle computed from an unread reading arms a guess, and here
   *  the guess would be about the transmitter. The wire ints are v2's own
   *  (`cw-panel-logic.ts`'s `BREAK_IN_LABELS`), consumed not reinvented. */
  export const BREAK_IN_CHOICES = [['off', 0], ['semi', 1], ['full', 2]] as const;
  /** APF as two ABSOLUTE choices over its ordinal, `[label, on]`. */
  export const APF_CHOICES = [['off', false], ['on', true]] as const;
  /** `[field, label, min, max, step, unit]` in the RAW wire units `CwPanel`
   *  has always used — rescaling here would silently move a setting. */
  export const CW_LEVELS = [
    ['keyerSpeed', 'Keyer speed', 6, 48, 1, 'WPM'],
    ['pitchHz', 'CW pitch', 300, 900, 5, 'Hz'],
    ['breakInDelay', 'Break-in delay', 0, 255, 1, ''],
  ] as const;
  export type CwLevelField = (typeof CW_LEVELS)[number][0];
  /** The ONE rendering of "not measured". Never 'OFF', never 0. */
  export const UNKNOWN_TEXT = '—';
  /** Break-in as the operator must read it. `unknown` is NOT 'off' (rule 5). */
  export type BreakInPosture = 'off' | 'armed' | 'unknown';
  export const POSTURE_LABEL: Record<BreakInPosture, string> = {
    off: 'break-in off — the key does not transmit',
    armed: 'break-in ARMED — the key transmits',
    unknown: 'break-in state unknown — assume the key transmits',
  };
  /** Why break-in is blocked, in the permit's own vocabulary (rule 3). */
  export const BREAK_IN_BLOCKED_LABEL: Partial<Record<DisabledReasonCode, string>> = {
    'out-of-band': 'TX not permitted: frequency outside the configured TX ranges',
    'capability-unavailable': 'TX not permitted: TX ranges are not configured',
    'tx-target-unknown': 'TX not permitted: the TX target is not observed',
  };
  /** Rule 4 — the mutex reason, per field, with the OTHER mode named. The
   *  `mutually-exclusive-control` code is generic by design (MOR-1293), so the
   *  words have to come from here. */
  export const MUTEX_LABEL = {
    apf: 'audio peak filter works only in CW / CW-R',
    twinPeak: 'twin-peak filter is an RTTY control — works only in RTTY / RTTY-R',
  } as const;

  /** Usable ⇔ the radio HAS it, it is readable NOW, and it was actually read. */
  export const usable = (f: CwKeyerField<unknown>): boolean =>
    f.availability.structural && f.availability.operational && f.reading.status === 'known';
  /** Honest text: an unread fact reads as unknown, never as a v2 default. */
  export const textOf = (f: CwKeyerField<unknown>): string =>
    f.reading.status !== 'known' ? UNKNOWN_TEXT
      : typeof f.reading.value === 'boolean' ? (f.reading.value ? 'on' : 'off')
        : String(f.reading.value);
  /** Rule 5: only a positively-read `'off'` is `'off'`. */
  export const breakInPosture = (f: CwKeyerField<BreakInMode>): BreakInPosture =>
    f.reading.status !== 'known' ? 'unknown' : f.reading.value === 'off' ? 'off' : 'armed';
</script>

<script lang="ts">
  import type { RadioViewModel } from './radio-view-model';

  interface Props {
    view: RadioViewModel;
    onBreakInMode?: (mode: number) => void;
    onLevelChange?: (field: CwLevelField, value: number) => void;
    onApfOn?: (on: boolean) => void;
    onTwinPeakToggle?: () => void;
    onReversePaddleToggle?: () => void;
  }
  let {
    view, onBreakInMode, onLevelChange, onApfOn, onTwinPeakToggle, onReversePaddleToggle,
  }: Props = $props();

  /** Absent group ⇒ this surface renders nothing (S0 optional-group doctrine). */
  let cw = $derived(view.cwKeyer);
  /** Rule 2. The model's ONE permit, READ. No second derivation exists here —
   *  `getFrequencyPermit`, `txBands` and `band` are not imported at all. */
  let permitAllowed = $derived(view.txPermit.status === 'allowed');
  /** Rule 3. Guaranteed present by the validator whenever break-in is blocked. */
  let breakInReason = $derived(
    view.disabledReasons.find((r) => r.field === 'cwKeyer.breakIn')?.code,
  );
  const mutexed = (field: 'apf' | 'twinPeak'): boolean =>
    view.disabledReasons.some((r) => r.field === `cwKeyer.${field}`);

  /** The handler half of every gate. `disabled` alone is not enough: a design
   *  language may restyle these controls, and a programmatic click must not
   *  set what the widget refused. */
  function setBreakIn(mode: number): void {
    if (cw && usable(cw.breakIn) && permitAllowed) onBreakInMode?.(mode);
  }
  function setLevel(field: CwLevelField, value: number): void {
    if (cw && usable(cw[field])) onLevelChange?.(field, value);
  }
  function setApf(on: boolean): void {
    if (cw && usable(cw.apf) && !mutexed('apf')) onApfOn?.(on);
  }
  function toggleTwinPeak(): void {
    if (cw && usable(cw.twinPeak) && !mutexed('twinPeak')) onTwinPeakToggle?.();
  }
  function toggleReversePaddle(): void {
    if (cw && usable(cw.reversePaddle)) onReversePaddleToggle?.();
  }
</script>

{#if cw}
  <!-- Rule 4: named for what it actually holds, not "CW". -->
  <section
    class="cw-keyer-surface" data-testid="cw-keyer-surface"
    aria-label="CW keyer and audio peak filters"
  >
    {#if cw.breakIn.availability.structural}
      <div
        class="cw-keyer-row" role="radiogroup" aria-label="Break-in"
        data-testid="cw-keyer-break-in"
        data-posture={breakInPosture(cw.breakIn)}
        data-permitted={permitAllowed}
      >
        {#each BREAK_IN_CHOICES as [label, mode] (mode)}
          <button
            type="button" role="radio" class="cw-keyer-choice"
            data-testid={`cw-keyer-break-in-${label}`}
            aria-checked={cw.breakIn.reading.status === 'known'
              && cw.breakIn.reading.value === label}
            disabled={!usable(cw.breakIn) || !permitAllowed}
            onclick={() => setBreakIn(mode)}
          >{label}</button>
        {/each}
        <!-- Rule 5: the posture is TEXT, so it survives forced-colors and so
             "armed but not permitted" reads differently from "off and not
             permitted" — the operator's radio can still key from its own
             paddle while this UI refuses to change the setting. -->
        <output data-testid="cw-keyer-posture">{POSTURE_LABEL[breakInPosture(cw.breakIn)]}</output>
        {#if !permitAllowed && breakInReason}
          <output data-testid="cw-keyer-break-in-blocked" data-reason={breakInReason}
          >{BREAK_IN_BLOCKED_LABEL[breakInReason]}</output>
        {/if}
      </div>
    {/if}

    {#each CW_LEVELS as [field, label, min, max, step, unit] (field)}
      {@const f = cw[field]}
      {#if f.availability.structural}
        <label class="cw-keyer-level" data-testid={`cw-keyer-${field}`} data-observed={usable(f)}>
          <span class="cw-keyer-name">{label}</span>
          <input
            type="range" {min} {max} {step}
            value={f.reading.status === 'known' ? f.reading.value : min}
            disabled={!usable(f)}
            oninput={(event) => setLevel(field, event.currentTarget.valueAsNumber)}
          />
          <output data-testid={`cw-keyer-${field}-value`}>{textOf(f)} {unit}</output>
        </label>
      {/if}
    {/each}

    {#if cw.reversePaddle.availability.structural}
      <button
        type="button" class="cw-keyer-toggle" data-testid="cw-keyer-reverse-paddle"
        aria-pressed={pressedOf(cw.reversePaddle)}
        disabled={!usable(cw.reversePaddle)}
        onclick={toggleReversePaddle}
      >Reverse paddle: {textOf(cw.reversePaddle)}</button>
    {/if}

    {#if cw.apf.availability.structural}
      <!-- `apf` is an ORDINAL (0 = off, >0 = a filter type this contract does
           not enumerate). The two choices below are ABSOLUTE on/off over that
           ordinal and the ordinal itself is shown verbatim; "which type" would
           need an `apfOn`/`apfType` fact that slice 9A deliberately did not
           promote (MOR-1296 open question 2) — flagged, not guessed. -->
      <div
        class="cw-keyer-row" role="radiogroup" aria-label="Audio peak filter"
        data-testid="cw-keyer-apf" data-observed={usable(cw.apf)}
      >
        {#each APF_CHOICES as [label, on] (label)}
          <button
            type="button" role="radio" class="cw-keyer-choice"
            data-testid={`cw-keyer-apf-${label}`}
            aria-checked={cw.apf.reading.status === 'known'
              && (cw.apf.reading.value > 0) === on}
            disabled={!usable(cw.apf) || mutexed('apf')}
            onclick={() => setApf(on)}
          >APF {label}</button>
        {/each}
        <output data-testid="cw-keyer-apf-value">{textOf(cw.apf)}</output>
        {#if mutexed('apf')}
          <output data-testid="cw-keyer-apf-mutex" data-reason="mutually-exclusive-control"
          >{MUTEX_LABEL.apf}</output>
        {/if}
      </div>
    {/if}

    {#if cw.twinPeak.availability.structural}
      <div class="cw-keyer-row" data-testid="cw-keyer-twin-peak" data-observed={usable(cw.twinPeak)}>
        <button
          type="button" class="cw-keyer-toggle" data-testid="cw-keyer-twin-peak-toggle"
          aria-pressed={pressedOf(cw.twinPeak)}
          disabled={!usable(cw.twinPeak) || mutexed('twinPeak')}
          onclick={toggleTwinPeak}
        >TPF: {textOf(cw.twinPeak)}</button>
        {#if mutexed('twinPeak')}
          <!-- Rule 4: RTTY is named, so a permanently-disabled control in a
               block the operator reads as "CW" is never unexplained. -->
          <output data-testid="cw-keyer-twin-peak-mutex" data-reason="mutually-exclusive-control"
          >{MUTEX_LABEL.twinPeak}</output>
        {/if}
      </div>
    {/if}

    {#if view.txAux}
      <!-- Sidetone level IS `txAux.monitorLevel` (MOR-1296 §4): read there,
           never duplicated as a second fact and never given a second control —
           the one control lives in `TxAuxSurface`. Readout only. -->
      <p class="cw-keyer-row" data-testid="cw-keyer-sidetone" data-observed={usable(view.txAux.monitorLevel)}>
        Sidetone level: {textOf(view.txAux.monitorLevel)}
      </p>
    {/if}
  </section>
{/if}

<style>
  /* Structure only — a design language owns colour and must never become the
     sole state channel (MOR-977, forced-colors). Nothing here animates. */
  .cw-keyer-surface { display: flex; flex-direction: column; gap: 0.25rem; }
  .cw-keyer-row { display: flex; flex-wrap: wrap; align-items: baseline; gap: 0.5rem; margin: 0; }
  .cw-keyer-level { display: flex; align-items: baseline; gap: 0.5rem; }
  .cw-keyer-name { min-width: 12ch; }
  .cw-keyer-choice[aria-checked='true'], .cw-keyer-toggle[aria-pressed='true'] { font-weight: 700; }
  /* Second channel beside the unknown TEXT, never the only one. */
  [data-observed='false'] { font-style: italic; }
  button:disabled, input:disabled { cursor: not-allowed; }
</style>
