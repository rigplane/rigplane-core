<script module lang="ts">
  import type { DisplayObservedMeterField, MeterRfState, MeterField } from './radio-view-model';
  import { isSwrFault, swrLevel } from '../components-v2/panels/meter-utils';
  import {
    projectSignalMeter,
    type SignalMeterProjection,
  } from '../components-v2/meters/smeter-scale';
  import { renderSlot } from './design-language-renderers';
  import type { LowerScaleDescriptor } from '../components-v2/meters/LinearSMeter.svelte';
  import { projectTxMeterPresentation } from './bar-meter-projector';

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

  function swrLowerScale(f: DisplayObservedMeterField, rfState: MeterRfState): LowerScaleDescriptor {
    const state = projectTxMeterPresentation(f, rfState);
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
   * hands over at S9). The reading and crossover are the fractions from the
   * same `SignalMeterProjection` passed to `LinearSMeter`; an unobserved meter
   * keeps a null motion fraction and stays unknown rather than reading as zero.
   * Annotations only — availability, relevance and the gauge itself remain this
   * surface's decisions.
   *
   * MOR-2255: this single call also supplies the `BarGauge` tiles' `zones` —
   * the slot is called ONCE per render (see `display` below) and read twice,
   * never once per gauge.
   */
  const meterDisplay = (signalProjection: SignalMeterProjection): ReturnType<typeof renderSlot> =>
    renderSlot('meters', {
      value: signalProjection.motionFraction,
      max: 1,
      // Explicit raw/unknown domains cannot expose this fallback to the
      // S-meter: `signalDisplay` below withholds their S-specific descriptor.
      // The shared descriptor still owns the unrelated BarGauge palettes.
      s9: signalProjection.crossoverFraction ?? 0,
    });
</script>

<script lang="ts">
  import BarGauge from '../components-v2/meters/BarGauge.svelte';
  import LinearSMeter from '../components-v2/meters/LinearSMeter.svelte';
  import type { RadioViewModel } from './radio-view-model';
  import type { MeterContinuitySession } from '../primitives/meters/meter-ballistics.svelte';
  import { RF_LABEL, RF_MARK } from './rx-tx-surface';
  import { projectBarMeters } from './bar-meter-projector';

  interface Props {
    view: RadioViewModel;
    continuitySession?: MeterContinuitySession | null;
  }
  let { view, continuitySession }: Props = $props();

  /** Absent group ⇒ this surface renders nothing (S0 optional-group doctrine,
   *  risk R3): a radio that reports no meters gets no empty dock, and no zone
   *  schema had to learn about it. */
  let meters = $derived(view.meters);

  let signalProjection = $derived(projectSignalMeter(
    meters && observed(meters.signal) ? rawOf(meters.signal) : null,
    meters?.signal.domain,
  ));
  let barMeters = $derived(projectBarMeters(view));

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
  let display = $derived(meters ? meterDisplay(signalProjection) : null);
  /** Explicit raw/unknown domains keep the shared bar palette but receive no
   * S-specific language geometry or annotations. A non-null crossover also
   * preserves the pre-MOR-2425 omitted-domain compatibility path. */
  let signalDisplay = $derived(
    signalProjection.crossoverFraction === null ? null : display,
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
        {...signalDisplay?.attributes ?? {}}
      >
        <LinearSMeter
          projection={signalProjection} label="S" compact
          mainPresent={present(meters.signal)}
          display={signalDisplay?.display ?? undefined}
          lowerScale={present(meters.swr) ? swrLowerScale(meters.swr, meters.rfState) : undefined}
          relevant={meters.signal.relevant}
          source={meters.signal.source}
          session={continuitySession}
        />
      </div>
    {/if}

    {#each barMeters as bar (bar.key)}
        <div
          class="meter-tile" data-meter-tile data-meter={bar.key} data-testid={`meter-${bar.key}`}
          data-relevant={bar.relevant} data-observed={bar.observed} data-fault={bar.fault}
          role="group" aria-label={`${bar.label} meter`}
        >
          {#if bar.gauge}
            <!-- MOR-2255: `zones` comes from the SAME `display` descriptor
                 the S-meter above reads, so every gauge on this surface is
                 painted by one language. `undefined` (no language, or a
                 descriptor without the quintet) falls back to `BarGauge`'s
                 own `DEFAULT_ZONES`. -->
            <BarGauge
              value={bar.motionFraction} label={bar.label}
              displayValue={bar.displayText}
              accessibleDescription={bar.accessibleDescription}
              compact showPeak={bar.showPeak} fault={bar.fault}
              zones={display?.display?.zones}
              source={bar.source} session={continuitySession}
            />
          {:else}
            <span class="meter-unknown">{bar.displayText}</span>
          {/if}
        </div>
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
