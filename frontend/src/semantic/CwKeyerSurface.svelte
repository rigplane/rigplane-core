<!--
  Semantic CW-keyer surface (MOR-1310, vocabulary slice 9B) — SAFETY-CRITICAL.

  Presentation only. It renders the MOR-1296 `cwKeyer` fact group — break-in
  posture (+delay), keyer speed, CW pitch, reverse paddle, APF and the twin-peak
  filter — and emits control intents as callbacks. It owns only local renderer
  interaction and issued presentation events; it consults no controller and
  owns no TX authority (v3 ADR invariant 11).

  SAFETY. Five rules govern this file and nothing may relax them:

  (1) NOT A KEY PATH. Break-in KEYS THE TRANSMITTER, but this surface never
      keys it: exactly one `<RxTxSurface>` remains the key/unkey authority
      (MOR-1262 decomposition R9). Every intent below is a SETTING intent
      (`set_break_in`, `set_cw_pitch`, …); nothing here takes a TX lease, sends
      a PTT command or asks for a carrier. RX frequency correction is a
      non-TX setting intent when its explicit availability is supplied; it
      carries no key, tuner, break-in, or TX-authority semantics.

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
  import { t } from '$lib/i18n';
  import { CW_CONTINUOUS_LEVELS } from './CwKeyerInstrumentHost.svelte';
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
    ...CW_CONTINUOUS_LEVELS.map(([field, label, min, max, step, unit]) =>
      [field, label, min, max, step, unit] as const),
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
  /**
   * MOR-1474: why break-in is blocked, in the permit's own vocabulary (rule
   * 3) — routed through the i18n catalog. The `{reason}` half REUSES the
   * exact `core.band.tx.reason.*` keys MOR-1448 established for BandSurface:
   * this field is gated on the SAME `view.txPermit` object BandSurface's
   * `txDeniedReason` reads (`deriveCwKeyerReasons` in
   * `radio-view-model-adapter.ts` derives `cwKeyer.breakIn`'s code from
   * `txPermit.status`/`txPermit.reason`, identically to how BandSurface's
   * `field: 'txPermit'` disabledReasons are populated) — same fact, same
   * words, including the out-of-band reason's explicit "your transmit
   * frequency" subject (never "this frequency", which would read as the
   * displayed band's frequency rather than the actual TX target). */
  export const BREAK_IN_REASON_KEY: Partial<Record<DisabledReasonCode, string>> = {
    'out-of-band': 'core.band.tx.reason.outOfBand',
    'capability-unavailable': 'core.band.tx.reason.rangesNotConfigured',
    'tx-target-unknown': 'core.band.tx.reason.targetUnknown',
  };
  export const breakInBlockedLabel = (code: DisabledReasonCode): string | undefined => {
    const reasonKey = BREAK_IN_REASON_KEY[code];
    return reasonKey === undefined
      ? undefined
      : t('core.cwKeyer.breakIn.blocked', { reason: t(reasonKey) });
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
  import { onDestroy, untrack } from 'svelte';
  import {
    type ControlFeedbackPresentationInput,
    type PresentationPhase,
  } from '../primitives/control-feedback/control-feedback-presentation';
  import { createCommittedScalar } from '../primitives/scalar/committed-scalar.svelte';
  import {
    createContinuousScalar, nativeRangeContinuousScalarPolicy,
    type CommandScalarFeedback, type ContinuousScalarInput,
    type ContinuousScalarRendererLease, type ContinuousScalarView,
  } from '../primitives/scalar/continuous-scalar.svelte';
  import { clamp, snapToStep } from '../primitives/scalar/value-control-core';
  import {
    bindChoiceInstrument,
    bindToggleInstrument,
  } from '../primitives/control-instruments/control-instrument-behavior';
  import type { CwKeyerInstrumentHandles } from './CwKeyerInstrumentHost.svelte';
  import type { RadioViewModel } from './radio-view-model';

  type BreakInDelayFeedback = ControlFeedbackPresentationInput<number> & {
    readonly sessionEpoch?: number;
    readonly scope?: Readonly<{ control: string; receiver: number; slot?: string }>;
  };

  interface Props {
    view: RadioViewModel;
    continuousHandles: CwKeyerInstrumentHandles;
    showKeyerSpeed?: boolean;
    onBreakInMode?: (mode: number) => void;
    onLevelChange?: (field: CwLevelField, value: number) => void;
    onApfOn?: (on: boolean) => void;
    onTwinPeakToggle?: () => void;
    onReversePaddleToggle?: () => void;
    breakInDelayFeedback?: Readonly<BreakInDelayFeedback>;
    cwPitchFeedback?: Readonly<CommandScalarFeedback>;
    autoTuneAvailable?: boolean;
    onAutoTune?: () => void;
  }
  let {
    view, continuousHandles, showKeyerSpeed = true,
    onBreakInMode, onLevelChange, onApfOn, onTwinPeakToggle, onReversePaddleToggle,
    breakInDelayFeedback, cwPitchFeedback,
    autoTuneAvailable = false, onAutoTune,
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
  const reversePaddleToggle = bindToggleInstrument(() => ({
    field: cw?.reversePaddle,
    // The handler owns its zero-argument inversion; the binding's next value
    // is intentionally not forwarded.
    invoke: () => onReversePaddleToggle?.(),
  }));
  const twinPeakToggle = bindToggleInstrument(() => ({
    field: cw?.twinPeak,
    blocked: mutexed('twinPeak'),
    // The handler owns its zero-argument inversion; the binding's next value
    // is intentionally not forwarded.
    invoke: () => onTwinPeakToggle?.(),
  }));
  const apfChoice = bindChoiceInstrument(() => {
    const field = cw?.apf;
    return {
      field: field === undefined ? undefined : {
        availability: field.availability,
        reading: field.reading.status === 'known'
          ? { status: 'known' as const, value: field.reading.value > 0 }
          : { status: 'unknown' as const },
      },
      blocked: mutexed('apf'),
      choices: APF_CHOICES.map(([, on]) => on),
      invoke: (on: boolean) => onApfOn?.(on),
    };
  });

  /** The handler half of every gate. `disabled` alone is not enough: a design
   *  language may restyle these controls, and a programmatic click must not
   *  set what the widget refused. */
  function setBreakIn(mode: number): void {
    if (cw && usable(cw.breakIn) && permitAllowed) onBreakInMode?.(mode);
  }
  function setLevel(field: CwLevelField, value: number): void {
    if (cw && usable(cw[field])) onLevelChange?.(field, value);
  }
  type FeedbackLevelField = 'pitchHz';
  const feedbackLevelDomain = {
    pitchHz: { min: 300, max: 900, step: 5, defaultValue: null, fineStepDivisor: 1 },
  } as const;
  function feedbackLevelInput(field: FeedbackLevelField): Readonly<ContinuousScalarInput> {
    const current = cw?.[field];
    const feedback = cwPitchFeedback;
    const common = {
      domain: feedbackLevelDomain[field],
      enabled: current !== undefined && usable(current),
      request: (value: number) => setLevel(field, value),
    } as const;
    if (feedback !== undefined) {
      return {
        ...common, evidence: 'command-feedback', feedback,
        command: 'set_cw_pitch',
      };
    }
    return {
      ...common, evidence: 'reading', ownerKey: `cw-keyer-${field}-reading`,
      reading: current?.reading.status === 'known'
        ? { status: 'known', value: current.reading.value } : { status: 'unknown' },
    };
  }
  const cwPitchScalar = createContinuousScalar(
    () => feedbackLevelInput('pitchHz'), nativeRangeContinuousScalarPolicy,
  );
  let cwPitchLease: ContinuousScalarRendererLease | null = $state(null);
  const initialCwPitchView = untrack(() => cwPitchScalar.view);
  let cwPitchView: Readonly<ContinuousScalarView> = $state(initialCwPitchView);
  type IssuedLevelAnnouncement = Readonly<{
    authorityKey: string;
    eventKey: string;
    text: string;
  }>;
  function feedbackAuthorityKey(current: Readonly<ContinuousScalarView>): string {
    const domain = current.domain;
    const shared = [
      current.evidence, current.editable, domain.min, domain.max, domain.step,
      domain.defaultValue, domain.fineStepDivisor, domain.keyboardStep,
    ];
    if (current.evidence === 'reading') {
      return JSON.stringify([...shared, current.reading.status]);
    }
    const feedback = current.feedback;
    return JSON.stringify([
      ...shared, feedback.providerGeneration ?? null, feedback.sessionEpoch,
      feedback.availability, feedback.scope.control, feedback.scope.receiver, feedback.scope.slot,
    ]);
  }
  function nextLevelAnnouncement(
    current: Readonly<ContinuousScalarView>,
    previous: IssuedLevelAnnouncement | null,
  ): IssuedLevelAnnouncement | null {
    const authorityKey = feedbackAuthorityKey(current);
    const issued = current.presentation?.politeAnnouncement;
    if (issued === null || issued === undefined || current.announcement === null) {
      return previous?.authorityKey === authorityKey ? previous : null;
    }
    return Object.freeze({
      authorityKey,
      eventKey: JSON.stringify([authorityKey, issued.transitionId]),
      text: current.error === null ? current.announcement : `${current.announcement}: ${current.error}`,
    });
  }
  let cwPitchAnnouncement: IssuedLevelAnnouncement | null = $state(
    nextLevelAnnouncement(initialCwPitchView, null),
  );
  $effect(() => {
    const lease = cwPitchScalar.attachRenderer();
    cwPitchLease = lease;
    return () => lease.dispose();
  });
  $effect(() => {
    const next = cwPitchLease === null ? cwPitchScalar.view : cwPitchLease.view;
    cwPitchView = next;
    cwPitchAnnouncement = nextLevelAnnouncement(
      next, untrack(() => cwPitchAnnouncement),
    );
  });
  onDestroy(() => {
    cwPitchScalar.destroy();
    breakInDelayScalar.destroy();
  });
  function feedbackLevelValueText(
    label: string, current: Readonly<ContinuousScalarView>, displayed: number | null, unit: string,
  ): string {
    if (displayed === null) return `${label} unavailable`;
    const phase = current.phase?.replaceAll('-', ' ');
    const error = current.error === null ? '' : `; ${current.error}`;
    return `${label} ${displayed}${unit === '' ? '' : ` ${unit}`}${phase ? `; ${phase}` : ''}${error}`;
  }
  const feedbackLevelDisplay = (current: Readonly<ContinuousScalarView>): number | null =>
    current.draft ?? (current.evidence === 'command-feedback' && current.busy
      ? current.target : null) ?? current.canonical;
  const BUSY_BREAK_IN_DELAY_PHASES: ReadonlySet<PresentationPhase> = new Set([
    'submitted', 'queued', 'dispatched', 'awaiting-confirmation',
  ]);
  const TERMINAL_BREAK_IN_DELAY_PHASES: ReadonlySet<PresentationPhase> = new Set([
    'confirmed', 'failed', 'timed-out', 'cancelled', 'superseded',
  ]);
  const BREAK_IN_DELAY_PHASES: ReadonlySet<PresentationPhase> = new Set([
    'unavailable', 'idle', ...BUSY_BREAK_IN_DELAY_PHASES, ...TERMINAL_BREAK_IN_DELAY_PHASES,
  ]);
  const unavailableFeedback: Readonly<ControlFeedbackPresentationInput<number>> = Object.freeze({
    confirmed: null, target: null, requestedTarget: null, phase: 'unavailable',
    transitionId: null, outcome: null,
  });
  const breakInDelayDomain = CW_LEVELS.find(([field]) => field === 'breakInDelay')!;
  const validLevel = (value: unknown): value is number =>
    typeof value === 'number' && Number.isSafeInteger(value)
      && value >= breakInDelayDomain[2] && value <= breakInDelayDomain[3]
      && (value - breakInDelayDomain[2]) % breakInDelayDomain[4] === 0;
  function validFeedback(value: Readonly<ControlFeedbackPresentationInput<number>>): boolean {
    const { confirmed, target, requestedTarget, phase, transitionId, outcome } = value;
    if (!BREAK_IN_DELAY_PHASES.has(phase)) return false;
    if (phase === 'unavailable') {
      return confirmed === null && target === null && requestedTarget === null && outcome === null;
    }
    if (!validLevel(confirmed) || (target !== null && !validLevel(target))
      || (requestedTarget !== null && !validLevel(requestedTarget))) return false;
    if (phase === 'idle') return target === null && requestedTarget === null && outcome === null;
    if (BUSY_BREAK_IN_DELAY_PHASES.has(phase)) {
      return target !== null && requestedTarget !== null && outcome === null
        && typeof transitionId === 'string' && transitionId.length > 0;
    }
    return target === null && requestedTarget !== null && outcome?.phase === phase
      && typeof transitionId === 'string' && transitionId.length > 0;
  }
  let effectiveBreakInDelayFeedback = $derived.by<Readonly<BreakInDelayFeedback>>(() => {
    let candidate: Readonly<BreakInDelayFeedback>;
    if (breakInDelayFeedback !== undefined) candidate = breakInDelayFeedback;
    else {
      const field = cw?.breakInDelay;
      const confirmed = field?.reading.status === 'known' ? field.reading.value : null;
      const phase = field !== undefined && usable(field) ? 'idle' : 'unavailable';
      candidate = {
        confirmed, target: null, requestedTarget: null, phase,
        transitionId: null, outcome: null,
      };
    }
    try { return validFeedback(candidate) ? candidate : unavailableFeedback; }
    catch { return unavailableFeedback; }
  });
  let hasBreakInDelayFeedback = $derived(breakInDelayFeedback !== undefined);
  let breakInDelayContextKey = $derived(JSON.stringify([
    'cw-keyer.break-in-delay',
    breakInDelayFeedback?.sessionEpoch ?? null,
    breakInDelayFeedback?.scope?.control ?? null,
    breakInDelayFeedback?.scope?.receiver ?? null,
    breakInDelayFeedback?.scope?.slot ?? null,
  ]));
  const INTEGRATED_RANGE_POLICY = { 'feedback-policy': 'feedback-integrated' } as const;
  let breakInDelayEditable = $derived(
    cw !== undefined && usable(cw.breakInDelay)
      && effectiveBreakInDelayFeedback.phase !== 'unavailable',
  );
  const breakInDelayScalar = createCommittedScalar(
    () => ({
      feedback: effectiveBreakInDelayFeedback,
      editable: breakInDelayEditable,
      contextKey: breakInDelayContextKey,
    }),
    {
      accepts: validLevel,
      normalize: (value) => snapToStep(clamp(
        value, breakInDelayDomain[2], breakInDelayDomain[3],
      ), breakInDelayDomain[4], breakInDelayDomain[2]),
      draftPolicy: 'reject-invalid',
      describeTarget: String,
    },
    (value) => setLevel('breakInDelay', value),
  );
  const breakInDelayLease = breakInDelayScalar.attachRenderer();
  let breakInDelayView = $derived(breakInDelayLease.view);
  let breakInDelayBusy = $derived(
    breakInDelayView.presentation.attributes['aria-busy'] === 'true',
  );
  let breakInDelayPhaseLabel = $derived(
    breakInDelayView.editing
      ? 'draft'
      : breakInDelayView.feedback.phase.replaceAll('-', ' '),
  );
  let breakInDelayValueText = $derived.by(() => {
    const confirmed = breakInDelayView.confirmed;
    if (confirmed === null) {
      return 'Break-in delay unavailable';
    }
    if (breakInDelayView.draft !== null) {
      return `Draft ${breakInDelayView.draft}; last confirmed ${confirmed}`;
    }
    if (breakInDelayBusy) {
      return `Requested ${breakInDelayView.displayed}; last confirmed ${confirmed}`;
    }
    const requested = breakInDelayView.feedback.requestedTarget;
    if (breakInDelayView.feedback.outcome !== null && requested !== null
      && breakInDelayView.feedback.outcome.phase !== 'confirmed') {
      return `Confirmed ${confirmed}; request ${requested} ${breakInDelayView.feedback.outcome.phase}`;
    }
    return `Confirmed ${confirmed}`;
  });
  function noteBreakInDelayInput(target: HTMLInputElement): void {
    breakInDelayLease.input(target.valueAsNumber);
  }
  function commitBreakInDelay(target: HTMLInputElement): void {
    const restored = breakInDelayLease.commit(target.valueAsNumber);
    if (restored !== null) target.value = String(restored);
  }
  function cancelBreakInDelay(target: HTMLInputElement): void {
    const restored = breakInDelayLease.cancel();
    if (restored !== null) target.value = String(restored);
  }
  function keyBreakInDelay(event: KeyboardEvent & { currentTarget: HTMLInputElement }): void {
    if (event.key !== 'Escape') return;
    event.preventDefault();
    cancelBreakInDelay(event.currentTarget);
  }
  function requestRxFrequencyCorrection(): void {
    if (autoTuneAvailable) onAutoTune?.();
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
          >{breakInBlockedLabel(breakInReason)}</output>
        {/if}
      </div>
    {/if}

    {#if showKeyerSpeed}
      {@render continuousHandles.keyerSpeed()}
    {/if}

    {#each CW_LEVELS.filter(([field]) => field !== 'keyerSpeed') as [field, label, min, max, step, unit] (field)}
      {@const f = cw[field]}
      {#if f.availability.structural}
        <label
          class="cw-keyer-level" data-testid={`cw-keyer-${field}`}
          data-observed={field === 'breakInDelay' ? usable(f)
            : cwPitchView.canonical !== null && cwPitchView.editable}
        >
          <span class="cw-keyer-name">{label}</span>
          {#if field === 'breakInDelay'}
            <input
              {...INTEGRATED_RANGE_POLICY} type="range" {min} {max} {step}
              value={breakInDelayView.displayed ?? (hasBreakInDelayFeedback ? undefined : min)}
              disabled={hasBreakInDelayFeedback ? !breakInDelayView.editable : !usable(f)}
              data-command-phase={hasBreakInDelayFeedback
                ? breakInDelayView.presentation.attributes['data-command-phase'] : undefined}
              aria-busy={hasBreakInDelayFeedback
                ? breakInDelayView.presentation.attributes['aria-busy'] : undefined}
              aria-valuenow={hasBreakInDelayFeedback && breakInDelayView.displayed !== null
                ? breakInDelayView.displayed : undefined}
              aria-valuetext={hasBreakInDelayFeedback ? breakInDelayValueText : undefined}
              oninput={(event) => noteBreakInDelayInput(event.currentTarget)}
              onchange={(event) => commitBreakInDelay(event.currentTarget)}
              onpointercancel={(event) => cancelBreakInDelay(event.currentTarget)}
              onkeydown={keyBreakInDelay}
            />
            {#if hasBreakInDelayFeedback}
            <output
              data-testid="cw-keyer-breakInDelay-value"
              data-command-phase={breakInDelayView.feedback.phase}
            >{breakInDelayView.displayed === null ? UNKNOWN_TEXT : breakInDelayView.displayed}
              <span class:command-pending={breakInDelayBusy}>{breakInDelayPhaseLabel}</span>
            </output>
            {#if breakInDelayView.announcement !== null}
              <span
                class="sr-only" role="status" aria-live="polite" aria-atomic="true"
                data-control-feedback-status
              >{breakInDelayView.announcement}</span>
            {/if}
            {:else}
              <output data-testid="cw-keyer-breakInDelay-value">{textOf(f)} {unit}</output>
            {/if}
          {:else}
            {@const levelView = cwPitchView}
            {@const levelLease = cwPitchLease}
            {@const hasFeedback = cwPitchFeedback !== undefined}
            {@const announcement = cwPitchAnnouncement}
            {@const levelDisplay = feedbackLevelDisplay(levelView)}
            <input
              {...INTEGRATED_RANGE_POLICY} type="range" {min} {max} {step}
              value={levelDisplay ?? min}
              disabled={!levelView.editable}
              data-command-phase={levelView.phase ?? undefined}
              aria-busy={hasFeedback ? levelView.busy : undefined}
              aria-valuenow={hasFeedback && levelDisplay !== null ? levelDisplay : undefined}
              aria-valuetext={hasFeedback
                ? feedbackLevelValueText(label, levelView, levelDisplay, unit) : undefined}
              oninput={(event) => levelLease?.nativeInput(event.currentTarget.valueAsNumber)}
            />
            <output data-testid={`cw-keyer-${field}-value`} data-command-phase={levelView.phase ?? undefined}
            >{levelDisplay === null ? UNKNOWN_TEXT : levelDisplay} {unit}
              {#if hasFeedback && levelView.phase !== null}
                <span class:command-pending={levelView.busy}>{levelView.phase.replaceAll('-', ' ')}</span>
                {#if levelView.error !== null}<span>{levelView.error}</span>{/if}
              {/if}
            </output>
            {#if hasFeedback && announcement !== null}
              {#key announcement.eventKey}
                <span
                  class="sr-only" role="status" aria-live="polite" aria-atomic="true"
                  data-control-feedback-status data-cw-feedback-status data-feedback-lane={field}
                >{announcement.text}</span>
              {/key}
            {/if}
          {/if}
        </label>
      {/if}
    {/each}

    {#if autoTuneAvailable}
      <button
        type="button" class="cw-keyer-toggle" data-testid="cw-keyer-auto-tune"
        onclick={requestRxFrequencyCorrection}
      >RX frequency correction</button>
    {/if}

    {#if cw.reversePaddle.availability.structural}
      <button
        type="button" class="cw-keyer-toggle" data-testid="cw-keyer-reverse-paddle"
        aria-pressed={pressedOf(cw.reversePaddle)}
        disabled={!reversePaddleToggle.available}
        onclick={() => reversePaddleToggle.invoke()}
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
            aria-checked={apfChoice.isSelected(on)}
            disabled={!apfChoice.available}
            onclick={() => apfChoice.invoke(on)}
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
          disabled={!twinPeakToggle.available}
          onclick={() => twinPeakToggle.invoke()}
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
  .command-pending { font-style: italic; }
  .sr-only {
    position: absolute; width: 1px; height: 1px; padding: 0; margin: -1px;
    overflow: hidden; clip: rect(0, 0, 0, 0); white-space: nowrap; border: 0;
  }
  button:disabled, input:disabled { cursor: not-allowed; }
</style>
