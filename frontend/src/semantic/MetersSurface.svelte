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
      zero, for every top-level tile in this file — "0 W into the antenna"
      and "not measured" are different claims. SWR's shared lower row is
      the one exception (MOR-2250, PR 2 of 2, owner ruling): it renders a
      zero-fill row rather than a `?`, because the structural/absent
      distinction is already made once, at the `present(meters.swr)` check
      on the `<LinearSMeter>` mount below — the row itself never restates
      it.
  (4) FAULT (MOR-1345): SWR/ALC over-threshold highlighting is a FACT this
      surface computes from the SAME `isSwrFault`/`isAlcFault` predicates the
      legacy dock's border reads (`meter-utils`, imported not copied), gated
      on `relevant` (this field's own TX-authority conclusion, rule 1) AND
      "observed" (rule 3) — an unobserved reading is never a fault. The
      colour itself is drawn by the component this file hands the boolean
      to: `BarGauge` for ALC, `LinearSMeter`'s lower-scale row (MOR-2250, PR
      2 of 2) for SWR — this file only ever computes the boolean.

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

  /**
   * `valueFraction`/`fault` use the SAME present/observed/relevant gating
   * and the SAME `swrLevel`/`isSwrFault` predicates the removed SWR
   * `BarGauge` row used (`present`/`observed`/`rawOf` above and the
   * `FAULT_CHECKS` gating pattern this mirrors) — imported, not re-derived,
   * so the two can never disagree about what counts as a reading or a
   * fault. `valueFraction` is NOT additionally gated on `relevant`: an
   * irrelevant-but-observed reading still reports its real fill, the same
   * discipline every `METER_BARS` field's own level follows (SWR itself
   * left `METER_BARS` — see the note above it — but its lower row keeps the
   * same rule). This is unconditional on `rfState` — the caller passes it
   * every render (see the `<LinearSMeter>` mount below); a `0` fraction
   * while receiving is simply what `observed(meters.swr)` naturally
   * resolves to when the radio has no known SWR sample outside TX, not a
   * branch on TX state here.
   *
   * `relevant` is passed through unchanged as its own descriptor field —
   * NOT translated into a dim here. Fix cycle 2: `meters.swr.relevant` and
   * `meters.signal.relevant` are DIFFERENT facts (`meters.signal.relevant`
   * is roughly `!onTx`-shaped per the adapter's fail-closed TX-relevance
   * doctrine, `deriveMeters` in `radio-view-model-adapter.ts`, while
   * `meters.swr.relevant` fails CLOSED the other way), and each now drives
   * its own independent, non-nested `<g>` inside `LinearSMeter`: this field
   * feeds `LowerScaleDescriptor.relevant` → `<g data-lower-relevant>`, while
   * `meters.signal.relevant` feeds the separate `relevant` prop → the
   * `<g data-main-relevant>` groups (see the `<LinearSMeter>` mount below).
   * Neither is an ancestor of the other, so the two opacities can never
   * compound — each field's dim reaches exactly its own row.
   */
  function swrLowerScale(f: MeterField): LowerScaleDescriptor {
    const isObserved = observed(f);
    const raw = rawOf(f);
    return {
      label: 'SWR',
      ticks: SWR_LOWER_SCALE_TICKS,
      valueFraction: isObserved ? swrLevel(raw) : 0,
      fault: isObserved && f.relevant && isSwrFault(raw),
      relevant: f.relevant,
    };
  }

  /**
   * MOR-1275: the active design language's `meters` renderer, for the S meter —
   * the one gauge whose grammar those renderers describe (a two-tone track that
   * hands over at S9). The reading is handed over on the 0..1 UI scale
   * `meter-utils` already calibrates, with the S9 crossover expressed on the
   * same scale, so the descriptor's fractions mean what they say; an unobserved
   * meter passes `null` and stays unknown rather than reading as zero (rule 3).
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

    {#if present(meters.signal)}
      <div
        class="meter-tile" data-meter-tile data-meter="signal" data-testid="meter-signal"
        data-relevant={meters.signal.relevant} data-observed={observed(meters.signal)}
        role="group" aria-label="S meter"
        {...display?.attributes ?? {}}
      >
        {#if observed(meters.signal)}
          <!-- MOR-2250 (PR 2 of 2): `lowerScale` is built and passed on
               EVERY render this branch takes, never behind an `rfState`
               check — the shared bar's bottom row must occupy the same
               geometry whether receiving or transmitting (see the file's
               own layout-stability note on `swrLowerScale` above). Absent
               only when the radio structurally has no SWR meter at all. -->
          <LinearSMeter
            value={rawOf(meters.signal)} label="S" compact
            display={display?.display ?? undefined}
            lowerScale={present(meters.swr) ? swrLowerScale(meters.swr) : undefined}
            relevant={meters.signal.relevant}
          />
        {:else}
          <span class="meter-unknown">S ?</span>
        {/if}
      </div>
    {/if}

    {#each METER_BARS as [field, label, level, format, showPeak] (field)}
      {#if present(meters[field]) && (field !== 'compression' || compressorOn)}
        {@const isObserved = observed(meters[field])}
        {@const raw = rawOf(meters[field])}
        {@const fault = isObserved && meters[field].relevant
          && (FAULT_CHECKS[field]?.(raw) ?? false)}
        <div
          class="meter-tile" data-meter-tile data-meter={field} data-testid={`meter-${field}`}
          data-relevant={meters[field].relevant} data-observed={isObserved} data-fault={fault}
          role="group" aria-label={`${label} meter`}
        >
          {#if isObserved}
            <!-- MOR-2255: `zones` comes from the SAME `display` descriptor
                 the S-meter above reads, so every gauge on this surface is
                 painted by one language. `undefined` (no language, or a
                 descriptor without the quintet) falls back to `BarGauge`'s
                 own `DEFAULT_ZONES`. -->
            <BarGauge
              value={level(raw)} {label} displayValue={format(raw)} compact {showPeak} {fault}
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
  /* Dim, never hide: an irrelevant meter keeps its box so the dock cannot
     reflow across an RX/TX transition. Opacity survives forced-colors, and it
     is a second channel beside `data-relevant`, never the only one.
     The S-meter tile takes one of two dimming paths, never both (MOR-2250,
     fix cycles 2 and 4), which is why the `:not(...)` below carries two
     conditions. Observed: `LinearSMeter` is mounted and `relevant` reaches
     it as a prop, dimming that component's own `<g data-main-relevant>`
     groups — so this ancestor rule must skip the tile, or its opacity would
     compound with the independently-relevant SWR row inside the same svg
     (CSS opacity multiplies down the DOM). Unobserved: no `LinearSMeter` is
     mounted, the `{:else}` `<span class="meter-unknown">` renders instead,
     and nothing inside the tile dims — so this rule covers it like any
     other tile. */
  .meter-tile[data-relevant='false']:not([data-meter='signal'][data-observed='true']) {
    opacity: 0.4;
  }
  .meter-unknown { font-weight: 700; }
</style>
