<script module lang="ts">
  import type { MeterField } from './radio-view-model';
  import type { SignalMeterProjection } from '../components-v2/meters/smeter-scale';
  import { renderSlot } from './design-language-renderers';
  import type { LowerScaleDescriptor } from '../components-v2/meters/LinearSMeter.svelte';
  import type { StationLevelMeterFrame } from './StationMeterInstrumentHost.svelte';

  /** Level 1 — does this radio HAVE the meter at all. */
  const present = (f: MeterField): boolean => f.availability.structural;
  /** Level 2 — is it readable now AND actually read. */
  const observed = (f: MeterField): boolean =>
    f.availability.operational && f.reading.status === 'known';
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

  function swrLowerScale(frame: StationLevelMeterFrame<'swr'>): LowerScaleDescriptor {
    const projection = frame.projection;
    return {
      label: 'SWR',
      ticks: projection.ratioScale ? SWR_LOWER_SCALE_TICKS : [],
      valueFraction: frame.motion.smoothedFraction,
      fault: projection.fault,
      relevant: projection.relevant,
      stateText: (projection.state === 'current' || projection.state === 'stale')
        && !projection.ratioScale
        ? projection.displayText : projection.stateText,
      accessibleDescription: projection.accessibleDescription ?? 'SWR: Not observed',
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
  import LinearSMeter from '../components-v2/meters/LinearSMeter.svelte';
  import MeterRendererSeat from '../component-kits/MeterRendererSeat.svelte';
  import { RF_LABEL, RF_MARK } from './rx-tx-surface';
  import StationMeterBarPlacement from './StationMeterBarPlacement.svelte';
  import type {
    StationMeterInstrumentHandles, StationSignalMeterFrame,
  } from './StationMeterInstrumentHost.svelte';
  import type { ActionRendererSeat } from '../primitives/control-instruments/control-instrument-renderer.svelte';
  import type { MeterRfState } from './radio-view-model';

  interface Props {
    handles: StationMeterInstrumentHandles;
  }
  let { handles }: Props = $props();
</script>

{#snippet station(
  signalFrame: StationSignalMeterFrame | null, signalProjection: SignalMeterProjection | null,
  swrFrame: StationLevelMeterFrame<'swr'> | null,
  rfState: MeterRfState,
  presentGroup: boolean,
)}
{#if presentGroup}
  {@const display = signalProjection ? meterDisplay(signalProjection) : null}
  {@const signalDisplay = signalProjection?.crossoverFraction == null ? null : display}
  {#snippet nativeLevel(frame: StationLevelMeterFrame, resetPeakSeat?: ActionRendererSeat)}
    {@const bar = frame.projection}
    <div class="meter-tile" data-meter-tile data-meter={bar.key} data-testid={`meter-${bar.key}`}
      data-relevant={bar.relevant} data-observed={bar.observed} data-fault={bar.fault}
      data-meter-state={bar.state}
      role="group" aria-label={`${bar.label} meter`}>
      <div class="meter-native-caption" aria-hidden="true">
        <span class="meter-native-label">{bar.label}</span>
        <span class="meter-native-value">{bar.displayText}</span>
      </div>
      {#if bar.gauge}
        {#key resetPeakSeat}<StationMeterBarPlacement {frame} label={bar.label}
          displayValue={bar.displayText} accessibleDescription={bar.accessibleDescription}
          compact fault={bar.fault} zones={display?.display?.zones} {resetPeakSeat} />{/key}
      {:else}<span class="meter-unknown">{bar.displayText}</span>{/if}
    </div>
  {/snippet}
  {#snippet level(frame: StationLevelMeterFrame, resetPeakSeat?: ActionRendererSeat)}
    {#key resetPeakSeat}
      <MeterRendererSeat kind="level" {frame} {resetPeakSeat} fallback={nativeLevel} />
    {/key}
  {/snippet}
  {#snippet power(frame: StationLevelMeterFrame<'power'>, seat?: ActionRendererSeat)}{@render level(frame, seat)}{/snippet}
  {#snippet nativeSwr(_frame: StationLevelMeterFrame)}{/snippet}
  {#snippet swr(frame: StationLevelMeterFrame<'swr'>)}
    <MeterRendererSeat kind="level" {frame} fallback={nativeSwr} />
  {/snippet}
  {#snippet alc(frame: StationLevelMeterFrame<'alc'>, seat?: ActionRendererSeat)}{@render level(frame, seat)}{/snippet}
  {#snippet drainCurrent(frame: StationLevelMeterFrame<'drainCurrent'>, seat?: ActionRendererSeat)}{@render level(frame, seat)}{/snippet}
  {#snippet drainVoltage(frame: StationLevelMeterFrame<'drainVoltage'>)}{@render level(frame)}{/snippet}
  {#snippet compression(frame: StationLevelMeterFrame<'compression'>)}{@render level(frame)}{/snippet}
  <section
    class="meters-surface" data-testid="meters-surface"
    data-rf-state={rfState} aria-label="Station meters"
  >
    <p class="meters-rf" data-testid="meters-rf">
      <span data-testid="meters-rf-mark">{RF_MARK[rfState]}</span>
      <span data-testid="meters-rf-label">{RF_LABEL[rfState]}</span>
    </p>

    {#if signalFrame !== null}
      {@const field = signalFrame.field}
      {@const reading = observed(field) && field.reading.status === 'known'
        && Number.isFinite(field.reading.value) ? field.reading : { status: 'unknown' } as const}
      {#snippet nativeSignal(_frame: typeof signalFrame.motion)}
        <div
          class="meter-tile" data-meter-tile data-meter={present(field) ? "signal" : "swr"} data-testid={present(field) ? "meter-signal" : "meter-swr"}
          data-relevant={field.relevant} data-observed={observed(field)}
          role="group" aria-label={present(field) ? "S meter" : "SWR meter"}
          {...signalDisplay?.attributes ?? {}}
        >
          {#if present(field)}
            <div class="meter-native-caption" aria-hidden="true" data-relevant={field.relevant}>
              <span class="meter-native-label">S</span>
              <span class="meter-native-value">{signalProjection?.primaryText ?? ''}</span>
              {#if signalProjection?.secondaryText}
                <span class="meter-native-secondary">{signalProjection.secondaryText}</span>
              {/if}
            </div>
          {/if}
          {#if swrFrame}
            <div class="meter-native-caption" aria-hidden="true" data-relevant={swrFrame.projection.relevant}>
              <span class="meter-native-label">SWR</span>
              <span class="meter-native-value">{swrFrame.projection.displayText}</span>
            </div>
          {/if}
          <LinearSMeter
            frame={signalFrame.motion} label="S" compact
            mainPresent={present(field)}
            display={signalDisplay?.display ?? undefined}
            lowerScale={swrFrame ? swrLowerScale(swrFrame) : undefined}
            relevant={field.relevant}
          />
        </div>
      {/snippet}
      <MeterRendererSeat frame={signalFrame.motion} {reading} domain={field.domain}
        relevant={field.relevant} selectedPresent={present(field)} fallback={nativeSignal} />
    {/if}
    {@render handles.power(power)}
    {@render handles.swr(swr)}
    {@render handles.alc(alc)}
    {@render handles.drainCurrent(drainCurrent)}
    {@render handles.drainVoltage(drainVoltage)}
    {@render handles.compression(compression)}
  </section>
{/if}
{/snippet}

{@render handles.signal(station)}

<style>
  /* Structure only — a design language owns the palette and must never become
     the sole state channel (MOR-977). The persistent station host owns motion;
     these gauges only draw its passive frames. */
  .meters-surface { display: flex; flex-direction: column; gap: 0.25rem; }
  .meters-rf { display: flex; align-items: baseline; gap: 0.4ch; margin: 0; font-weight: 700; }
  .meter-tile { display: block; }
  .meter-native-caption { display: none; }
  .meter-tile[data-relevant='false']:not([data-meter='signal']):not([data-meter='swr']) {
    opacity: 0.4;
  }
  .meter-unknown { font-weight: 700; }
</style>
