<script module lang="ts">
  import { projectTxMeterDisplay } from './tx-meter-display';
  import type { DisplayObservedMeterField, MeterRfState, MeterField, MetersViewModel } from './radio-view-model';
  import {
    alcLevel, compLevel, formatAlc, formatAmps, formatCompDb, formatPowerWatts,
    formatVolts, idLevel, isAlcFault, isSwrFault, normalizePower, sLevel,
    swrLevel, vdLevel,
  } from '../components-v2/panels/meter-utils';
  import { renderSlot } from './design-language-renderers';
  import type { LowerScaleDescriptor } from '../components-v2/meters/LinearSMeter.svelte';

  type BarKey = Exclude<keyof MetersViewModel, 'rfState' | 'signal'>;
  type Scale = readonly [BarKey, string, (raw: number) => number, (raw: number) => string, boolean];

  /** `[field, label, level, format, showPeak]` for the FIVE remaining bar
   *  meters, in the shipped dock's priority order. `swr` is absent as of
   *  MOR-2250 (PR 2 of 2) — it now renders on the shared lower scale row
   *  inside `LinearSMeter` (see `swrLowerScale` below) instead of through
   *  `BarGauge`, so it must appear exactly once, never zero or two times.
   *  The level/format pairs are `meter-utils`' own calibrated functions —
   *  the same ones `MetersDockPanel` uses — so the two can never disagree
   *  about what a raw sample means. `showPeak` (MOR-1282) mirrors the
   *  dock's own `PeakKey` set restricted to what remains here (Po/ALC/Id) —
   *  Vd (a continuous supply rail) and COMP were never peak-held there
   *  either. `signal` is absent because it has its own component
   *  (`LinearSMeter`, below). */
  export const METER_BARS = [
    ['power', 'Po', normalizePower, formatPowerWatts, true],
    ['alc', 'ALC', alcLevel, formatAlc, true],
    ['drainCurrent', 'Id', idLevel, formatAmps, true],
    ['drainVoltage', 'Vd', vdLevel, formatVolts, false],
    ['compression', 'COMP', compLevel, formatCompDb, false],
  ] as const satisfies readonly Scale[];

  /**
   * MOR-1345: fields with an over-threshold FAULT — the SAME `isAlcFault`
   * predicate `MetersDockPanel`'s border reads, imported (not copied) so the
   * two surfaces can never disagree about what counts as a fault. Only ALC
   * has a threshold among the remaining `METER_BARS` fields, and this
   * surface invents none — an absent entry means "never faults". SWR's own
   * fault predicate (`isSwrFault`) is still imported above, but is now
   * consumed directly by `swrLowerScale`, not through this map.
   */
  const FAULT_CHECKS: Partial<Record<BarKey, (raw: number) => boolean>> = {
    alc: isAlcFault,
  };

  /** Level 1 — does this radio HAVE the meter at all. */
  const present = (f: MeterField): boolean => f.availability.structural;
  /** Level 2 — is it readable now AND actually read. */
  const observed = (f: MeterField): boolean =>
    f.availability.operational && f.reading.status === 'known';
  const rawOf = (f: MeterField): number => (f.reading.status === 'known' ? f.reading.value : 0);

  /**
   * MOR-2250 (PR 2 of 2): the bottom scale row of the shared S-meter bar —
   * the real IC-7300's second, radio-selected TX-meter scale, fixed to SWR
   * only (owner ruling: no selector in this PR; a future instrument-group
   * selector swaps this out as a DATA change). Label/ticks are a fixed UI
   * constant, not radio-specific data — the same treatment `LinearSMeter`'s
   * own hardcoded S-unit scale marks get — matching the reference photo's
   * "SWR 1 1.5 2 2.5 3 ∞".
   */
  const SWR_LOWER_SCALE_TICKS = [
    { value: 0, label: '1' },
    { value: 0.2, label: '1.5' },
    { value: 0.4, label: '2' },
    { value: 0.6, label: '2.5' },
    { value: 0.8, label: '3' },
    { value: 1, label: '∞' },
  ] as const;

  function txPresentation(f: DisplayObservedMeterField, rfState: MeterRfState) {
    const projected = projectTxMeterDisplay(f, rfState);
    if (!projected.supported) return { value: null, text: '?', description: 'Not observed' };
    const { relevance, observation } = projected;
    if (relevance === 'idle') return { value: null, text: 'IDLE', description: 'Not measuring in RX' };
    const cue = relevance === 'indeterminate' ? 'RF relevance indeterminate. ' : '';
    return {
      value: observation.state === 'current' ? observation.value : null,
      text: observation.state === 'stale' ? 'STALE' : observation.state === 'current'
        ? (relevance === 'indeterminate' ? ' ?' : '') : '?',
      description: cue + (observation.state === 'stale' ? 'Stale observation'
        : observation.state === 'current' ? 'Current observation' : 'Not observed'),
    };
  }

  function swrLowerScale(f: DisplayObservedMeterField, rfState: MeterRfState): LowerScaleDescriptor {
    const state = txPresentation(f, rfState);
    return {
      label: 'SWR', ticks: SWR_LOWER_SCALE_TICKS,
      valueFraction: state.value === null ? 0 : swrLevel(state.value),
      fault: state.value !== null && f.relevant && isSwrFault(state.value),
      relevant: f.relevant, stateText: state.text,
      accessibleDescription: `SWR: ${state.description}`,
    };
  }

  /**
   * MOR-1275: the active design language's `meters` renderer, for the S meter —
   * the one gauge whose grammar those renderers describe (a two-tone track that
   * hands over at S9). The reading is handed over on the 0..1 UI scale
   * `meter-utils` already calibrates, with the S9 crossover expressed on the
   * same scale, so the descriptor's fractions mean what they say; an unobserved
   * meter passes `null` and stays unknown rather than reading as zero.
   * Annotations only — availability, relevance and the gauge itself remain this
   * surface's decisions.
   *
   * MOR-2255: this single call also supplies the `BarGauge` tiles' `zones` —
   * the slot is called ONCE per render (see `display` below) and read twice,
   * never once per gauge.
   */
  const signalDisplay = (f: MeterField): ReturnType<typeof renderSlot> =>
    renderSlot('meters', { value: observed(f) ? sLevel(rawOf(f)) : null, max: 1, s9: sLevel(0) });
</script>

<script lang="ts">
  import BarGauge from '../components-v2/meters/BarGauge.svelte';
  import LinearSMeter from '../components-v2/meters/LinearSMeter.svelte';
  import type { RadioViewModel } from './radio-view-model';
  import { RF_LABEL, RF_MARK } from './rx-tx-surface';

  interface Props { view: RadioViewModel }
  let { view }: Props = $props();

  /** Absent group ⇒ this surface renders nothing (S0 optional-group doctrine,
   *  risk R3): a radio that reports no meters gets no empty dock, and no zone
   *  schema had to learn about it. */
  let meters = $derived(view.meters);

  /**
   * The active design language's `meters` descriptor for this render, or
   * `null` when no language is active, the language declares no `meters`
   * renderer, or its descriptor is missing the `MeterDisplay` quintet.
   *
   * MOR-2255: hoisted out of the S-meter branch so the S-meter tile
   * (`attributes` + `display`) and every `BarGauge` tile (`display.zones`)
   * read the SAME descriptor from ONE `renderSlot` call. Each consumer falls
   * back to its own component default when this is `null`.
   */
  let display = $derived(meters ? signalDisplay(meters.signal) : null);

  /** The COMP gate is the MOR-1244 `txAux.compressor` FACT, deliberately NOT
   *  `meters.compression.availability`: a radio can keep reporting a
   *  compression meter while the compressor is switched off, and that reading
   *  measures nothing. Fail-closed — an unobserved compressor, or a radio with
   *  no txAux group at all, never opens the gate. */
  let compressorOn = $derived(
    view.txAux?.compressor.reading.status === 'known'
      && view.txAux.compressor.reading.value === true,
  );
</script>

{#if meters}
  <section
    class="meters-surface" data-testid="meters-surface"
    data-rf-state={meters.rfState} aria-label="Station meters"
  >
    <p class="meters-rf" data-testid="meters-rf">
      <span data-testid="meters-rf-mark">{RF_MARK[meters.rfState]}</span>
      <span data-testid="meters-rf-label">{RF_LABEL[meters.rfState]}</span>
    </p>

    {#if present(meters.signal) || present(meters.swr)}
      <div
        class="meter-tile" data-meter-tile data-meter={present(meters.signal) ? "signal" : "swr"} data-testid={present(meters.signal) ? "meter-signal" : "meter-swr"}
        data-relevant={meters.signal.relevant} data-observed={observed(meters.signal)}
        role="group" aria-label={present(meters.signal) ? "S meter" : "SWR meter"}
        {...display?.attributes ?? {}}
      >
        <LinearSMeter
          value={observed(meters.signal) ? rawOf(meters.signal) : null} label="S" compact
          mainPresent={present(meters.signal)}
          display={display?.display ?? undefined}
          lowerScale={present(meters.swr) ? swrLowerScale(meters.swr, meters.rfState) : undefined}
          relevant={meters.signal.relevant}
        />
      </div>
    {/if}

    {#each METER_BARS as [field, label, level, format, showPeak] (field)}
      {#if present(meters[field]) && (field !== 'compression' || compressorOn)}
        {@const tx = field === 'power' || field === 'alc' ? txPresentation(meters[field], meters.rfState) : null}
        {@const isObserved = tx ? tx.value !== null : observed(meters[field])}
        {@const raw = tx ? tx.value ?? 0 : rawOf(meters[field])}
        {@const fault = isObserved && meters[field].relevant
          && (FAULT_CHECKS[field]?.(raw) ?? false)}
        <div
          class="meter-tile" data-meter-tile data-meter={field} data-testid={`meter-${field}`}
          data-relevant={meters[field].relevant} data-observed={isObserved} data-fault={fault}
          role="group" aria-label={`${label} meter`}
        >
          {#if isObserved || tx}
            <!-- MOR-2255: `zones` comes from the SAME `display` descriptor
                 the S-meter above reads, so every gauge on this surface is
                 painted by one language. `undefined` (no language, or a
                 descriptor without the quintet) falls back to `BarGauge`'s
                 own `DEFAULT_ZONES`. -->
            <BarGauge
              value={isObserved ? level(raw) : null} {label}
              displayValue={tx ? (isObserved ? format(raw) + tx.text : tx.text) : format(raw)}
              accessibleDescription={tx ? `${label}: ${tx.description}${isObserved ? `. ${format(raw)}` : ''}` : undefined}
              compact showPeak={showPeak && isObserved} {fault}
              zones={display?.display?.zones}
            />
          {:else}
            <span class="meter-unknown">{label} ?</span>
          {/if}
        </div>
      {/if}
    {/each}
  </section>
{/if}

<style>
  /* Structure only — a design language owns the palette and must never become
     the sole state channel (MOR-977). Nothing here moves: the gauges own their
     own ballistics and honour reduced motion themselves. */
  .meters-surface { display: flex; flex-direction: column; gap: 0.25rem; }
  .meters-rf { display: flex; align-items: baseline; gap: 0.4ch; margin: 0; font-weight: 700; }
  .meter-tile { display: block; }
  .meter-tile[data-relevant='false']:not([data-meter='signal']):not([data-meter='swr']) {
    opacity: 0.4;
  }
  .meter-unknown { font-weight: 700; }
</style>
