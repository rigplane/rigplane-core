<script lang="ts">
  import type {
    DisplayIndicator,
    DisplayValue,
    PeerSplitDisplayModel,
  } from '../../semantic/radio-display-model';
  import LcdFilterScope from './LcdFilterScope.svelte';
  import LcdFlagRail, { type LcdFlagRailItem } from './LcdFlagRail.svelte';
  import LcdFrequencyReadout from './LcdFrequencyReadout.svelte';
  import LcdLinearSMeter from './LcdLinearSMeter.svelte';
  import LcdOffsetRail from './LcdOffsetRail.svelte';
  import LcdTelemetryRail from './LcdTelemetryRail.svelte';
  import { notchIndicators, stateText } from './lcd-display-helpers';

  interface Props {
    model: PeerSplitDisplayModel;
    normalizedFftBins?: Partial<Record<'MAIN' | 'SUB', readonly number[]>>;
  }

  let { model, normalizedFftBins = {} }: Props = $props();

  const main = $derived(model.receivers[0]);
  const sub = $derived(model.receivers[1]);

  const activeDsp = $derived(model.activeReceiver?.dsp ?? {
    agc: { state: 'unknown' } as DisplayValue<number | string>,
    nb: { state: 'unknown' } as DisplayIndicator,
    nr: { state: 'unknown' } as DisplayIndicator,
    notch: { state: 'unknown' } as DisplayValue<'off' | 'auto' | 'manual'>,
  });

  const activeFront = $derived(model.activeReceiver?.front ?? {
    preamp: { state: 'unknown' } as DisplayValue<number>,
    attenuator: { state: 'unknown' } as DisplayValue<number>,
    rfGain: { state: 'unknown' } as DisplayValue<number>,
    digiSel: { state: 'unknown' } as DisplayIndicator,
    ipPlus: { state: 'unknown' } as DisplayIndicator,
  });

  const notchFlags = $derived(notchIndicators(activeDsp.notch));

  const operationFlags: readonly LcdFlagRailItem[] = $derived([
    { label: 'VOX', icon: 'VOX', field: model.top.vox },
    { label: 'PROC', icon: 'PROC', field: model.top.compressor },
    { label: 'SPLIT', icon: 'SPLIT', field: model.top.split },
    { label: 'RIT', icon: 'RIT', field: model.top.rit },
  ]);

  const transmitFlags: readonly LcdFlagRailItem[] = $derived([
    { label: 'TX', icon: 'TX', field: model.top.tx },
    { label: 'TUNE', icon: 'TUNE', field: model.top.tune },
    { label: 'ATU', icon: 'ATU', field: model.top.atu },
  ]);

  const dspFlags: readonly LcdFlagRailItem[] = $derived([
    { label: 'NB', icon: 'NB', field: activeDsp.nb, statusLabel: 'NB' },
    { label: 'NR', icon: 'NR', field: activeDsp.nr, statusLabel: 'NR' },
    { label: 'NOTCH', icon: 'NOTCH', field: notchFlags.notch, statusLabel: 'NOTCH' },
    { label: 'ANF', icon: 'ANF', field: notchFlags.anf, statusLabel: 'ANF' },
    {
      label: 'AGC',
      icon: 'AGC',
      field: activeDsp.agc.state === 'known'
        ? { state: 'active' }
        : { state: activeDsp.agc.state },
      value: stateText(activeDsp.agc),
      dataState: activeDsp.agc.state,
    },
  ]);

  const frontFlags: readonly LcdFlagRailItem[] = $derived([
    {
      label: 'PRE',
      icon: 'PRE',
      value: stateText(activeFront.preamp),
      field: activeFront.preamp.state === 'known'
        ? { state: activeFront.preamp.value > 0 ? 'active' : 'inactive' }
        : { state: activeFront.preamp.state },
    },
    {
      label: 'ATT',
      icon: 'ATT',
      value: stateText(activeFront.attenuator),
      field: activeFront.attenuator.state === 'known'
        ? { state: activeFront.attenuator.value > 0 ? 'active' : 'inactive' }
        : { state: activeFront.attenuator.state },
    },
    { label: 'DIGI', icon: null, field: activeFront.digiSel },
    { label: 'IP+', icon: 'IPP', field: activeFront.ipPlus },
  ]);
</script>

<div
  class="dominant-display"
  data-testid="dominant-unified-display"
  data-native-stage="1280x540"
  data-rf-state={model.rfState}
>
  <section
    class="hero receiver-state-{main.activity}"
    data-testid="lcd-dominant-main"
    data-receiver-activity={main.activity}
    style:opacity={main.activity === 'active' ? 1 : main.activity === 'inactive' ? 0.62 : 0.48}
  >
    <div class="receiver-label">{main.label}{main.activity === 'active' ? ' ●' : ''}</div>
    <div
      class="hero-frequency"
      data-state={main.frequency.state}
      style:visibility={main.frequency.state === 'unsupported' ? 'hidden' : undefined}
    >
      <LcdFrequencyReadout receiver={main.receiver} field={main.frequency} />
    </div>
    <div class="hero-facts">
      <span
        class="fact"
        data-testid="lcd-main-mode"
        data-state={main.mode.state}
        style:visibility={main.mode.state === 'unsupported' ? 'hidden' : undefined}
      >
        {stateText(main.mode)}
      </span>
      <span
        class="fact"
        data-testid="lcd-main-filter"
        data-state={main.filter.state}
        style:visibility={main.filter.state === 'unsupported' ? 'hidden' : undefined}
      >
        {stateText(main.filter)}
      </span>
      <span
        class="fact"
        data-testid="lcd-main-band"
        data-state={main.band.state}
        style:visibility={main.band.state === 'unsupported' ? 'hidden' : undefined}
      >
        {stateText(main.band)}
      </span>
    </div>
  </section>

  <section
    class="sub-strip receiver-state-{sub.activity}"
    data-testid="lcd-dominant-sub"
    data-receiver-activity={sub.activity}
    style:opacity={sub.activity === 'active' ? 1 : sub.activity === 'inactive' ? 0.34 : 0.48}
  >
    <div class="receiver-label sub-label">{sub.label}{sub.activity === 'active' ? ' ●' : ''}</div>
    <div
      class="sub-frequency"
      data-state={sub.frequency.state}
      style:visibility={sub.frequency.state === 'unsupported' ? 'hidden' : undefined}
    >
      <LcdFrequencyReadout receiver={sub.receiver} field={sub.frequency} />
    </div>
    <div class="sub-facts">
      <span
        class="fact"
        data-testid="lcd-sub-mode"
        data-state={sub.mode.state}
        style:visibility={sub.mode.state === 'unsupported' ? 'hidden' : undefined}
      >
        {stateText(sub.mode)}
      </span>
      <span
        class="fact"
        data-testid="lcd-sub-filter"
        data-state={sub.filter.state}
        style:visibility={sub.filter.state === 'unsupported' ? 'hidden' : undefined}
      >
        {stateText(sub.filter)}
      </span>
      <span
        class="fact"
        data-testid="lcd-sub-band"
        data-state={sub.band.state}
        style:visibility={sub.band.state === 'unsupported' ? 'hidden' : undefined}
      >
        {stateText(sub.band)}
      </span>
    </div>
    <div class="sub-offsets">
      <LcdOffsetRail receiver={sub.receiver} offsets={model.offsets} />
    </div>
  </section>

  <div class="meter-row">
    {#each model.receivers as receiver (receiver.receiver)}
      <div
        class="meter-cell receiver-state-{receiver.activity}"
        data-testid="lcd-dominant-meter"
        data-receiver={receiver.receiver}
        data-receiver-activity={receiver.activity}
      >
        <span class="meter-receiver">{receiver.receiver}</span>
        <LcdLinearSMeter field={receiver.sMeter} />
      </div>
    {/each}
  </div>

  <div class="instrument-row">
    <section class="unified-scope">
      {#if model.activeReceiver}
        <LcdFilterScope
          receiver={model.activeReceiver}
          normalizedBins={normalizedFftBins[model.activeReceiver.receiver]}
        />
      {:else}
        <div
          class="scope-unknown"
          data-testid="lcd-dominant-scope-unknown"
          data-state="unknown"
        >
          <span>AF SCOPE · BANDPASS</span>
          <div class="scope-unknown-field" aria-hidden="true"></div>
        </div>
      {/if}
    </section>

    <aside class="status-block">
      <div class="status-groups">
        <LcdFlagRail label="OP" items={operationFlags} />
        <LcdFlagRail label="TX" items={transmitFlags} />
        <LcdFlagRail label="DSP" items={dspFlags} />
        <LcdFlagRail label="FRONT" items={frontFlags} />
      </div>
      <div
        class="antenna-fact fact"
        data-state={model.top.antenna.state}
        style:visibility={model.top.antenna.state === 'unsupported' ? 'hidden' : undefined}
      >
        <span>ANT</span> {stateText(model.top.antenna)}
      </div>
      <LcdTelemetryRail telemetry={model.telemetry} />
    </aside>
  </div>
</div>

<style>
  .dominant-display {
    --ink-strong: var(--dl-segmentline-ink-strong, rgba(26, 16, 0, 1));
    --ink-mid: var(--dl-segmentline-ink-mid, rgba(26, 16, 0, 0.65));
    --ink-soft: var(--dl-segmentline-ink-soft, rgba(26, 16, 0, 0.34));
    --ink-ghost: var(--dl-segmentline-ink-ghost, rgba(26, 16, 0, 0.09));
    --ink-telemetry: var(--dl-segmentline-ink-telemetry, rgba(26, 16, 0, 0.5));
    box-sizing: border-box;
    display: grid;
    grid-template-rows: 132px 56px 52px minmax(0, 1fr);
    width: 100%;
    height: 100%;
    min-width: 0;
    min-height: 0;
    padding: 13px 15px;
    overflow: hidden;
    color: var(--ink-strong);
    font-family: 'Share Tech Mono', ui-monospace, monospace;
    font-variant-numeric: tabular-nums;
  }

  .hero {
    display: grid;
    grid-template-columns: 104px minmax(0, 1fr) auto;
    align-items: center;
    gap: 14px;
    min-width: 0;
    border-bottom: 1px solid var(--ink-soft);
  }
  .receiver-label {
    justify-self: start;
    padding: 3px 8px;
    border: 1.5px solid currentColor;
    border-radius: 3px;
    color: var(--ink-mid);
    font-size: 16px;
    font-weight: 700;
    letter-spacing: 0.12em;
    white-space: nowrap;
  }
  .receiver-state-active > .receiver-label { color: var(--ink-strong); }
  .hero.receiver-state-inactive { opacity: 0.62; }
  .hero.receiver-state-unknown { opacity: 0.48; }
  .hero-frequency { min-width: 0; }
  .hero-frequency :global(.frequency) { font-size: 92px; }
  .hero-facts, .sub-facts { display: flex; align-items: center; gap: 6px; }
  .fact {
    box-sizing: border-box;
    min-width: 58px;
    padding: 3px 7px;
    border: 1.25px solid currentColor;
    border-radius: 2px;
    color: var(--ink-mid);
    font-size: 13px;
    font-weight: 700;
    letter-spacing: 0.08em;
    text-align: center;
    white-space: nowrap;
  }
  .fact[data-state='unknown'] { color: var(--ink-soft); }
  .fact[data-state='unsupported'] { visibility: hidden; }

  .sub-strip {
    display: grid;
    grid-template-columns: 78px 280px 180px minmax(0, 1fr);
    align-items: center;
    gap: 10px;
    min-width: 0;
    border-bottom: 1px solid var(--ink-soft);
    opacity: 0.34;
  }
  .sub-strip.receiver-state-active { opacity: 1; }
  .sub-strip.receiver-state-unknown { opacity: 0.48; }
  .sub-label { padding: 2px 6px; font-size: 11px; }
  .sub-frequency { min-width: 0; }
  .sub-frequency :global(.frequency) { font-size: 34px; }
  .sub-facts .fact { min-width: 50px; padding: 2px 5px; font-size: 10px; }
  .sub-offsets { justify-self: end; width: min(100%, 430px); }
  .sub-offsets :global(.offset) { padding-block: 1px; }
  .sub-offsets :global(.offset-value) { font-size: 13px; }

  .meter-row {
    display: grid;
    grid-template-columns: 1fr 1fr;
    gap: 18px;
    align-items: center;
    padding: 5px 2px 3px;
    border-bottom: 1px solid var(--ink-soft);
  }
  .meter-cell { display: grid; grid-template-columns: 38px minmax(0, 1fr); align-items: center; gap: 6px; }
  .meter-cell.receiver-state-inactive { opacity: 0.34; }
  .meter-cell.receiver-state-unknown { opacity: 0.48; }
  .meter-receiver { color: var(--ink-mid); font-size: 9px; font-weight: 700; letter-spacing: 0.14em; }
  .meter-cell :global(.s-meter) { grid-column: 2; }

  .instrument-row {
    display: grid;
    grid-template-columns: minmax(0, 1fr) 300px;
    gap: 12px;
    min-width: 0;
    min-height: 0;
    padding-top: 8px;
  }
  .unified-scope { min-width: 0; min-height: 0; padding-right: 2px; }
  .unified-scope :global(.scope-block) { height: 100%; }
  .scope-unknown {
    display: grid;
    grid-template-rows: 16px minmax(0, 1fr);
    height: 100%;
    color: var(--ink-soft);
    font-size: 11px;
    font-weight: 700;
    letter-spacing: 0.18em;
  }
  .scope-unknown-field {
    border: 1px solid var(--ink-ghost);
    background: repeating-linear-gradient(
      90deg,
      transparent 0 49px,
      var(--ink-ghost) 49px 50px
    );
  }

  .status-block {
    display: grid;
    grid-template-rows: minmax(0, 1fr) auto auto;
    gap: 5px;
    min-width: 0;
    min-height: 0;
    padding-left: 10px;
    border-left: 1px solid var(--ink-soft);
  }
  .status-groups { display: grid; align-content: start; gap: 5px; min-width: 0; overflow: hidden; }
  .status-block :global(.flag-zone) { gap: 3px; }
  .status-block :global(.zone-label) { width: 35px; margin-right: 1px; font-size: 9px; }
  .status-block :global(.status-flag) {
    min-height: 20px;
    gap: 3px;
    padding: 1px 4px;
    font-size: 9px;
    letter-spacing: 0.04em;
  }
  .status-block :global(.status-flag svg) { width: 12px; height: 12px; }
  .antenna-fact { justify-self: start; min-width: 68px; padding-block: 2px; font-size: 10px; }
  .antenna-fact span { color: var(--ink-mid); }
  .status-block :global(.aux-rail) { display: block; min-width: 0; }
  .status-block :global(.memory-seam) { display: none; }
  .status-block :global(.telemetry) {
    display: grid;
    grid-template-columns: repeat(3, minmax(0, 1fr));
    gap: 3px 9px;
    font-size: 9px;
  }
</style>
