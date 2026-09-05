<script lang="ts">
  import type { PeerSplitDisplayModel } from '../../semantic/radio-display-model';
  import LcdAfFft from './LcdAfFft.svelte';
  import LcdFilterScope from './LcdFilterScope.svelte';
  import LcdFrequencyReadout from './LcdFrequencyReadout.svelte';
  import LcdLinearSMeter from './LcdLinearSMeter.svelte';
  import LcdRfPanadapter, {
    type LcdRfPanadapterPassband,
  } from './LcdRfPanadapter.svelte';
  import { stateText, telemetryText } from './lcd-display-helpers';

  interface Props {
    model: PeerSplitDisplayModel;
    normalizedFftBins?: Partial<Record<'MAIN' | 'SUB', readonly number[]>>;
    rfFrame?: unknown;
  }

  let { model, normalizedFftBins = {}, rfFrame }: Props = $props();

  const activeReceiver = $derived(model.activeReceiver);
  const activeAfBins = $derived(
    activeReceiver === null ? undefined : normalizedFftBins[activeReceiver.receiver],
  );
  const activeCarrierHz = $derived(
    activeReceiver?.frequency.state === 'known' ? activeReceiver.frequency.value : undefined,
  );
  const activePassband: LcdRfPanadapterPassband | undefined = $derived.by(() => {
    if (activeReceiver?.mode.state !== 'known'
      || activeReceiver.bandwidthHz.state !== 'known'
      || activeReceiver.ifShiftHz.state !== 'known') return undefined;
    return {
      mode: activeReceiver.mode.value,
      widthHz: activeReceiver.bandwidthHz.value,
      shiftHz: activeReceiver.ifShiftHz.value,
    };
  });
  const telemetryItems = $derived([
    { label: 'VD', field: model.telemetry.drainVoltage },
    { label: 'ID', field: model.telemetry.drainCurrent },
    { label: 'PWR', field: model.telemetry.power },
    { label: 'SWR', field: model.telemetry.swr },
    { label: 'ALC', field: model.telemetry.alc },
    { label: 'COMP', field: model.telemetry.compression },
  ]);
</script>

<div
  class="panadapter-display"
  data-testid="panadapter-display"
  data-native-stage="1280x594"
  data-rf-state={model.rfState}
>
  <div class="frequency-deck">
    {#each model.receivers as receiver (receiver.vfoSlot ?? receiver.receiver)}
      <section
        class="frequency-column"
        class:active={receiver.activity === 'active'}
        data-testid="panadapter-frequency-column"
        data-column-activity={receiver.activity}
        data-receiver={receiver.receiver}
        data-display-slot={receiver.vfoSlot ?? receiver.receiver}
      >
        <div class="frequency-head">
          <span class="receiver-label">{receiver.label}{receiver.activity === 'active' ? ' ●' : ''}</span>
          <div class="receiver-facts">
            <span data-state={receiver.mode.state}>{stateText(receiver.mode)}</span>
            <span data-state={receiver.filter.state}>{stateText(receiver.filter)}</span>
            <span data-state={receiver.band.state}>{stateText(receiver.band)}</span>
          </div>
        </div>
        <LcdFrequencyReadout receiver={receiver.vfoSlot ?? receiver.receiver} field={receiver.frequency} />
        <LcdLinearSMeter field={receiver.sMeter} />
      </section>
    {/each}
  </div>

  <div class="work-area">
    <section class="rf-block">
      <div class="instrument-head">
        <span>RF SPECTRUM · PANADAPTER</span>
        {#if activeReceiver !== null}<small>{activeReceiver.label}</small>{/if}
      </div>
      <div class="rf-plot">
        <LcdRfPanadapter
          receiver={activeReceiver?.receiver ?? null}
          frame={rfFrame}
          carrierHz={activeCarrierHz}
          passband={activePassband}
        />
      </div>
    </section>

    <aside class="active-inset">
      <div class="af-block">
        {#if activeReceiver !== null}
          <LcdFilterScope receiver={activeReceiver} normalizedBins={activeAfBins} />
        {:else}
          <div class="unknown-af">
            <span>AF · BANDPASS</span>
            <div class="unknown-af-plot">
              <LcdAfFft inputState="unknown" receiverActivity="unknown" />
            </div>
          </div>
        {/if}
      </div>

      <div class="telemetry" data-testid="panadapter-telemetry">
        {#each telemetryItems as item}
          {#if item.field.state !== 'unsupported'}
            <span class:irrelevant={!item.field.relevant} data-state={item.field.state}>
              <small>{item.label}</small> {telemetryText(item.field)}
            </span>
          {/if}
        {/each}
      </div>
    </aside>
  </div>
</div>

<style>
  .panadapter-display {
    --ink-strong: var(--dl-segmentline-ink-strong, rgba(26, 16, 0, 1));
    --ink-mid: var(--dl-segmentline-ink-mid, rgba(26, 16, 0, 0.65));
    --ink-soft: var(--dl-segmentline-ink-soft, rgba(26, 16, 0, 0.34));
    --ink-ghost: var(--dl-segmentline-ink-ghost, rgba(26, 16, 0, 0.09));
    box-sizing: border-box;
    display: grid;
    grid-template-rows: minmax(158px, 0.72fr) minmax(0, 1fr);
    height: 100%;
    padding: 14px;
    color: var(--ink-strong);
    font-family: 'Share Tech Mono', ui-monospace, monospace;
    font-variant-numeric: tabular-nums;
  }

  .frequency-deck {
    display: grid;
    grid-template-columns: minmax(0, 1fr) minmax(0, 1fr);
    min-height: 0;
    border-bottom: 1px solid var(--ink-soft);
  }
  .frequency-column {
    display: grid;
    grid-template-rows: 36px 78px 38px;
    gap: 4px;
    min-width: 0;
    padding: 0 18px 8px;
    opacity: 0.3;
  }
  .frequency-column:first-child { border-right: 1px solid var(--ink-soft); }
  .frequency-column.active { opacity: 1; }
  .frequency-head { display: flex; align-items: center; justify-content: space-between; gap: 10px; }
  .receiver-label, .receiver-facts span {
    border: 1.25px solid var(--ink-mid);
    border-radius: 2px;
    padding: 2px 7px;
    font-size: 12px;
    font-weight: 700;
    letter-spacing: 0.08em;
    white-space: nowrap;
  }
  .receiver-label { font-size: 14px; }
  .receiver-facts { display: flex; gap: 4px; min-width: 0; }
  .receiver-facts [data-state='unknown'] { opacity: 0.34; }
  .receiver-facts [data-state='unsupported'] { visibility: hidden; }

  .work-area {
    display: grid;
    grid-template-columns: minmax(0, 1fr) minmax(200px, 28%);
    gap: 12px;
    min-height: 0;
    padding-top: 10px;
  }
  .rf-block { display: grid; grid-template-rows: 18px minmax(0, 1fr); min-width: 0; min-height: 0; }
  .instrument-head {
    display: flex;
    justify-content: space-between;
    color: var(--ink-mid);
    font-size: 11px;
    font-weight: 700;
    letter-spacing: 0.15em;
  }
  .instrument-head small { color: var(--ink-soft); font: inherit; }
  .rf-plot { min-height: 0; padding-top: 4px; }

  .active-inset {
    display: grid;
    grid-template-rows: minmax(86px, 1fr) auto;
    gap: 12px;
    min-width: 0;
    min-height: 0;
    border-left: 1px solid var(--ink-soft);
    padding-left: 12px;
  }
  .af-block { min-height: 0; }
  .unknown-af { display: grid; grid-template-rows: 16px minmax(0, 1fr); height: 100%; min-height: 0; }
  .unknown-af > span { color: var(--ink-mid); font-size: 11px; font-weight: 700; letter-spacing: 0.18em; }
  .unknown-af-plot { min-height: 0; }
  .telemetry {
    display: grid;
    grid-template-columns: repeat(2, minmax(0, 1fr));
    gap: 4px 10px;
    border-top: 1px solid var(--ink-soft);
    padding-top: 8px;
    color: var(--ink-mid);
    font-size: 10px;
  }
  .telemetry span { white-space: nowrap; }
  .telemetry small { opacity: 0.7; font-size: inherit; }
  .telemetry .irrelevant { opacity: 0.34; }
</style>
