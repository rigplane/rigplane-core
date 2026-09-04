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

  const topLeft: readonly LcdFlagRailItem[] = $derived([
    { label: 'VOX', icon: 'VOX', field: model.top.vox },
    { label: 'PROC', icon: 'PROC', field: model.top.compressor },
    { label: 'SPLIT', icon: 'SPLIT', field: model.top.split },
    { label: 'LOCK', icon: 'LOCK', field: { state: 'unsupported' } },
    { label: 'RIT', icon: 'RIT', field: model.top.rit },
  ]);

  const topRight: readonly LcdFlagRailItem[] = $derived([
    { label: 'TX', icon: 'TX', field: model.top.tx },
    { label: 'TUNE', icon: 'TUNE', field: model.top.tune },
    { label: 'ATU', icon: 'ATU', field: model.top.atu },
    { label: 'PROC', icon: 'PROC', field: model.top.compressor },
  ]);

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

  const dspFlags: readonly LcdFlagRailItem[] = $derived([
    { label: 'NB', icon: 'NB', field: activeDsp.nb, statusLabel: 'NB' },
    { label: 'NR', icon: 'NR', field: activeDsp.nr, statusLabel: 'NR' },
    {
      label: 'NOTCH',
      icon: 'NOTCH',
      field: notchFlags.notch,
      statusLabel: 'NOTCH',
    },
    {
      label: 'ANF',
      icon: 'ANF',
      field: notchFlags.anf,
      statusLabel: 'ANF',
    },
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

<div class="peer-display" data-testid="peer-split-display" data-rf-state={model.rfState}>
  <header class="status-rail">
    <LcdFlagRail label="OP" items={topLeft} />
    <div class="status-spacer" aria-hidden="true"></div>
    <LcdFlagRail label="TX" items={topRight} end />
  </header>

  <div class="receiver-deck">
    {#each model.receivers as receiver (receiver.receiver)}
      <section
        class="receiver-column"
        class:active={receiver.activity === 'active'}
        data-testid="lcd-peer-column"
        data-column-activity={receiver.activity}
        data-receiver={receiver.receiver}
      >
        <div class="column-accent" aria-hidden="true"></div>
        <div class="column-head">
          <span class="vfo-tag">{receiver.label}{receiver.activity === 'active' ? ' ●' : ''}</span>
          <div class="vfo-pills">
            <span class="lcd-pill">{stateText(receiver.mode)}</span>
            <span class="lcd-pill">{stateText(receiver.filter)}</span>
            <span class="lcd-pill">{stateText(receiver.band)}</span>
          </div>
        </div>

        <LcdFrequencyReadout receiver={receiver.receiver} field={receiver.frequency} />
        <LcdOffsetRail receiver={receiver.receiver} offsets={model.offsets} />
        <LcdLinearSMeter field={receiver.sMeter} />
        <LcdFilterScope
          {receiver}
          normalizedBins={normalizedFftBins[receiver.receiver]}
        />
      </section>
    {/each}
  </div>

  <div class="fact-rail">
    <LcdFlagRail label="DSP" items={dspFlags} />
    <LcdFlagRail label="FRONT" items={frontFlags} end />
  </div>

  <LcdTelemetryRail telemetry={model.telemetry} />
</div>

<style>
  .peer-display {
    --ink-strong: var(--dl-segmentline-ink-strong, rgba(26, 16, 0, 1));
    --ink-mid: var(--dl-segmentline-ink-mid, rgba(26, 16, 0, 0.65));
    --ink-soft: var(--dl-segmentline-ink-soft, rgba(26, 16, 0, 0.34));
    --ink-ghost: var(--dl-segmentline-ink-ghost, rgba(26, 16, 0, 0.09));
    --ink-telemetry: var(--dl-segmentline-ink-telemetry, rgba(26, 16, 0, 0.5));
    box-sizing: border-box;
    display: grid;
    grid-template-rows: 42px minmax(0, 1fr) 42px 31px;
    height: 100%;
    padding: 14px;
    color: var(--ink-strong);
    font-family: 'Share Tech Mono', ui-monospace, monospace;
    font-variant-numeric: tabular-nums;
  }

  .status-rail, .fact-rail {
    display: flex;
    align-items: center;
    justify-content: space-between;
    min-width: 0;
  }
  .status-rail { border-bottom: 1px solid var(--ink-soft); padding-bottom: 6px; }
  .fact-rail { border-top: 1px solid var(--ink-soft); padding-top: 6px; }
  .status-spacer { min-width: 120px; }

  .receiver-deck { display: grid; grid-template-columns: 1fr 1fr; min-height: 0; }
  .receiver-column {
    position: relative;
    display: grid;
    grid-template-rows: 38px 88px 49px 47px minmax(0, 1fr);
    gap: 6px;
    min-width: 0;
    padding: 8px 22px;
    opacity: 0.3;
  }
  .receiver-column:first-child { border-right: 1px solid var(--ink-soft); }
  .receiver-column.active { opacity: 1; }
  .column-accent { position: absolute; inset-block: 12px; width: 3px; background: var(--ink-strong); opacity: 0; }
  .receiver-column:first-child .column-accent { left: 6px; }
  .receiver-column:last-child .column-accent { right: 6px; }
  .receiver-column.active .column-accent { opacity: 0.85; }
  .column-head { display: flex; align-items: center; justify-content: space-between; gap: 10px; }
  .vfo-tag, .lcd-pill {
    border: 1.75px solid var(--ink-mid);
    border-radius: 3px;
    padding: 2px 9px;
    font-size: 17px;
    font-weight: 700;
    letter-spacing: 0.08em;
    line-height: 1.2;
    white-space: nowrap;
  }
  .vfo-tag { font-size: 18px; }
  .vfo-pills { display: flex; gap: 5px; min-width: 0; }
</style>
