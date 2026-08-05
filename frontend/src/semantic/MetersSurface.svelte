<!--
  Semantic meters surface (MOR-1273, vocabulary slice 2B).

  Presentation only. It renders the MOR-1269 `meters` fact group — S / Po /
  SWR / ALC / COMP / Vd / Id — as SVG gauges. It holds no state, consults no
  controller and emits no intent (v3 ADR invariant 11): a meter is a readout,
  never an action surface.

  SAFETY (R9). Three rules govern this file:

  (1) TX truth arrives ALREADY DECIDED. `meters.rfState` and each field's
      `relevant` flag come from the App-owned TX authority via the adapter's
      evidence gate; this surface displays that conclusion and computes no
      second one. Deriving relevance from the raw transmit wire bit is the
      open disagreement MOR-1235 reported and MOR-1269 closed — the single
      `view` prop below is the whole reason it cannot come back here.
  (2) The RF word and its mark are the SHARED `RF_LABEL` / `RF_MARK` maps that
      spell the RX/TX surface's own state, imported rather than copied. A copy
      could drift and tell the operator "RX" beside a key button reading "TX".
      They are text AND shape, so the state survives forced-colors (MOR-977).
  (3) An unobserved meter renders as an explicit `?`, never as a gauge at
      zero. "0 W into the antenna" and "not measured" are different claims.

  Two-level availability (MOR-977/1256): `structural: false` renders NOTHING —
  "this radio has no SWR meter" is a different claim from "the SWR meter is
  unreadable right now", which renders present-but-unobserved. An irrelevant
  meter is DIMMED rather than hidden, so the dock geometry does not reflow
  under the operator at the moment they key up (MOR-485).

  BALLISTICS ARE NOT HERE. Smoothing and peak-hold — and, with them, the
  `prefers-reduced-motion` behaviour of MOR-1233/1249/1252 — belong to the
  shipped SVG components this file composes (`LinearSMeter`, `BarGauge`), both
  driving `$lib/utils/smoothing.svelte`'s `createSmoother`, which snaps to
  target instead of animating under `reduce`. A loop here would be a second,
  unaudited one. This surface's own styles carry structure only: no colour, no
  transition, so a reduced-motion cockpit page stays provably still.
-->
<script module lang="ts">
  import type { MeterField, MetersViewModel } from './radio-view-model';
  import {
    alcLevel, compLevel, formatAlc, formatAmps, formatCompDb, formatPowerWatts,
    formatSwr, formatVolts, idLevel, normalizePower, sLevel, swrLevel, vdLevel,
  } from '../components-v2/panels/meter-utils';
  import { renderSlot } from './design-language-renderers';

  type BarKey = Exclude<keyof MetersViewModel, 'rfState' | 'signal'>;
  type Scale = readonly [BarKey, string, (raw: number) => number, (raw: number) => string];

  /** `[field, label, level, format]` for the six bar meters, in the shipped
   *  dock's priority order. The level/format pairs are `meter-utils`' own
   *  calibrated functions — the same ones `MetersDockPanel` uses — so the two
   *  can never disagree about what a raw sample means. `signal` is absent
   *  because it has its own component (`LinearSMeter`, below). */
  export const METER_BARS = [
    ['power', 'Po', normalizePower, formatPowerWatts],
    ['swr', 'SWR', swrLevel, formatSwr],
    ['alc', 'ALC', alcLevel, formatAlc],
    ['drainCurrent', 'Id', idLevel, formatAmps],
    ['drainVoltage', 'Vd', vdLevel, formatVolts],
    ['compression', 'COMP', compLevel, formatCompDb],
  ] as const satisfies readonly Scale[];

  /** Level 1 — does this radio HAVE the meter at all. */
  const present = (f: MeterField): boolean => f.availability.structural;
  /** Level 2 — is it readable now AND actually read. */
  const observed = (f: MeterField): boolean =>
    f.availability.operational && f.reading.status === 'known';
  const rawOf = (f: MeterField): number => (f.reading.status === 'known' ? f.reading.value : 0);

  /**
   * MOR-1275: the active design language's `meters` renderer, for the S meter —
   * the one gauge whose grammar those renderers describe (a two-tone track that
   * hands over at S9). The reading is handed over on the 0..1 UI scale
   * `meter-utils` already calibrates, with the S9 crossover expressed on the
   * same scale, so the descriptor's fractions mean what they say; an unobserved
   * meter passes `null` and stays unknown rather than reading as zero (rule 3).
   * Annotations only — availability, relevance and the gauge itself remain this
   * surface's decisions.
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

    {#if present(meters.signal)}
      <div
        class="meter-tile" data-meter-tile data-meter="signal" data-testid="meter-signal"
        data-relevant={meters.signal.relevant} data-observed={observed(meters.signal)}
        role="group" aria-label="S meter"
        {...signalDisplay(meters.signal)?.attributes ?? {}}
      >
        {#if observed(meters.signal)}
          <LinearSMeter value={rawOf(meters.signal)} label="S" compact />
        {:else}
          <span class="meter-unknown">S ?</span>
        {/if}
      </div>
    {/if}

    {#each METER_BARS as [field, label, level, format] (field)}
      {#if present(meters[field]) && (field !== 'compression' || compressorOn)}
        <div
          class="meter-tile" data-meter-tile data-meter={field} data-testid={`meter-${field}`}
          data-relevant={meters[field].relevant} data-observed={observed(meters[field])}
          role="group" aria-label={`${label} meter`}
        >
          {#if observed(meters[field])}
            <BarGauge
              value={level(rawOf(meters[field]))} {label}
              displayValue={format(rawOf(meters[field]))} compact
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
  /* Dim, never hide: an irrelevant meter keeps its box so the dock cannot
     reflow across an RX/TX transition. Opacity survives forced-colors, and it
     is a second channel beside `data-relevant`, never the only one. */
  .meter-tile[data-relevant='false'] { opacity: 0.4; }
  .meter-unknown { font-weight: 700; }
</style>
