<script lang="ts">
  import type {
    DisplayValue,
    PeerSplitDisplayModel,
    PeerSplitReceiverDisplay,
  } from '../../semantic/radio-display-model';
  import LcdFilterScope from './LcdFilterScope.svelte';
  import LcdFrequencyReadout from './LcdFrequencyReadout.svelte';
  import LcdLinearSMeter from './LcdLinearSMeter.svelte';
  import { resolveLcdSpectrumFrame } from './lcd-display-contract';
  import { stateText, telemetryText } from './lcd-display-helpers';

  interface Props {
    model: PeerSplitDisplayModel;
    audioFftFrame?: unknown;
  }

  interface OrbitItem {
    readonly label: 'MODE' | 'FILT' | 'BAND' | 'AGC';
    readonly field: DisplayValue<number | string>;
  }

  let { model, audioFftFrame }: Props = $props();

  const ghostReceiver: PeerSplitReceiverDisplay = {
    receiver: 'MAIN',
    label: 'ACTIVE',
    activity: 'unknown',
    operational: false,
    frequency: { state: 'unknown' },
    mode: { state: 'unknown' },
    filter: { state: 'unknown' },
    band: { state: 'unknown' },
    sMeter: { state: 'unknown' },
    bandwidthHz: { state: 'unknown' },
    ifShiftHz: { state: 'unknown' },
    pbtInnerHz: { state: 'unknown' },
    pbtOuterHz: { state: 'unknown' },
    spectrum: 'unknown',
    dsp: {
      agc: { state: 'unknown' },
      nb: { state: 'unknown' },
      nr: { state: 'unknown' },
      notch: { state: 'unknown' },
    },
    front: {
      preamp: { state: 'unknown' },
      attenuator: { state: 'unknown' },
      rfGain: { state: 'unknown' },
      digiSel: { state: 'unknown' },
      ipPlus: { state: 'unknown' },
    },
  };

  function receiverById(
    displayModel: PeerSplitDisplayModel,
    id: PeerSplitReceiverDisplay['receiver'],
  ): PeerSplitReceiverDisplay {
    return displayModel.receivers.find((receiver) => receiver.receiver === id)
      ?? (id === 'MAIN' ? displayModel.receivers[0] : displayModel.receivers[1]);
  }

  const active = $derived(model.activeReceiver ?? ghostReceiver);
  const activeIdentity = $derived(model.activeReceiver?.receiver ?? 'unknown');
  const fixedVfoSlots = $derived(model.receivers[0].vfoSlot !== undefined);
  const primary = $derived(fixedVfoSlots ? model.receivers[0] : active);
  const secondary = $derived(fixedVfoSlots ? model.receivers[1] : receiverById(
    model,
    model.activeReceiver?.receiver === 'SUB' ? 'MAIN' : 'SUB',
  ));
  const orbit: readonly OrbitItem[] = $derived([
    { label: 'MODE', field: active.mode },
    { label: 'FILT', field: active.filter },
    { label: 'BAND', field: active.band },
    { label: 'AGC', field: active.dsp.agc },
  ]);
  const activeAudioFft = $derived(
    resolveLcdSpectrumFrame(audioFftFrame, {
      source: 'audio-fft',
      receiver: model.activeReceiver?.receiver ?? null,
    }),
  );
  const activeFftBins = $derived(
    activeAudioFft.state === 'live' ? activeAudioFft.frame.normalizedBins : undefined,
  );
  const telemetry = $derived([
    { label: 'VD', field: model.telemetry.drainVoltage },
    { label: 'ID', field: model.telemetry.drainCurrent },
    { label: 'PWR', field: model.telemetry.power },
    { label: 'SWR', field: model.telemetry.swr },
    { label: 'ALC', field: model.telemetry.alc },
    { label: 'COMP', field: model.telemetry.compression },
  ]);
</script>

<div
  class="centerstage-display"
  data-testid="centerstage-display"
  data-rf-state={model.rfState}
  data-active-receiver={activeIdentity}
>
  <header class="top-orbit" aria-label="Active receiver summary">
    {#each orbit as item}
      <div
        class="orbit-item"
        data-orbit-field={item.label}
        data-state={item.field.state}
        style:opacity={item.field.state === 'unknown' ? 0.34 : 1}
        style:visibility={item.field.state === 'unsupported' ? 'hidden' : 'visible'}
      >
        <span class="orbit-label">{item.label}</span>
        <span class="orbit-value">{stateText(item.field)}</span>
      </div>
    {/each}
  </header>

  <section
    class="hero-frequency"
    data-testid="centerstage-hero"
    data-receiver={fixedVfoSlots ? primary.receiver : activeIdentity}
    data-display-slot={primary.vfoSlot ?? primary.receiver}
    data-state={primary.frequency.state}
    style:opacity={primary.frequency.state === 'unknown' ? 0.34 : 1}
    style:visibility={primary.frequency.state === 'unsupported' ? 'hidden' : 'visible'}
  >
    <span class="receiver-tag">{primary.label}{primary.activity === 'active' ? ' ●' : ''}</span>
    <LcdFrequencyReadout receiver={primary.vfoSlot ?? primary.receiver} field={primary.frequency} />
  </section>

  <section
    class="secondary-frequency"
    data-testid="centerstage-secondary"
    data-receiver={secondary.receiver}
    data-state={secondary.frequency.state}
    style:opacity={secondary.frequency.state === 'unknown' ? 0.34 : 1}
    style:visibility={secondary.frequency.state === 'unsupported' ? 'hidden' : 'visible'}
  >
    <span class="secondary-tag">{secondary.label}{secondary.activity === 'active' ? ' ●' : ''}</span>
    <LcdFrequencyReadout receiver={secondary.vfoSlot ?? secondary.receiver} field={secondary.frequency} />
    <span
      class="secondary-mode"
      data-state={secondary.mode.state}
      style:visibility={secondary.mode.state === 'unsupported' ? 'hidden' : 'visible'}
    >{stateText(secondary.mode)}</span>
    <span
      class="secondary-filter"
      data-state={secondary.filter.state}
      style:visibility={secondary.filter.state === 'unsupported' ? 'hidden' : 'visible'}
    >{stateText(secondary.filter)}</span>
  </section>

  <div
    class="centerstage-meter"
    data-testid="centerstage-meter"
    data-state={active.sMeter.state}
    style:visibility={active.sMeter.state === 'unsupported' ? 'hidden' : 'visible'}
  >
    <LcdLinearSMeter field={active.sMeter} />
  </div>

  <section
    class="cinematic-scope"
    data-identity={activeIdentity}
    style:opacity={activeIdentity === 'unknown' ? 0.34 : 1}
  >
    <LcdFilterScope receiver={active} normalizedBins={activeFftBins} />
  </section>

  <footer class="telemetry-only" data-testid="centerstage-telemetry">
    {#each telemetry as item}
      <span
        class:irrelevant={!item.field.relevant}
        data-state={item.field.state}
        style:visibility={item.field.state === 'unsupported' ? 'hidden' : 'visible'}
      ><small>{item.label}</small> {telemetryText(item.field)}</span>
    {/each}
  </footer>
</div>

<style>
  .centerstage-display {
    --ink-strong: var(--dl-segmentline-ink-strong, rgba(26, 16, 0, 1));
    --ink-mid: var(--dl-segmentline-ink-mid, rgba(26, 16, 0, 0.65));
    --ink-soft: var(--dl-segmentline-ink-soft, rgba(26, 16, 0, 0.34));
    --ink-ghost: var(--dl-segmentline-ink-ghost, rgba(26, 16, 0, 0.09));
    --ink-telemetry: var(--dl-segmentline-ink-telemetry, rgba(26, 16, 0, 0.5));
    box-sizing: border-box;
    display: grid;
    grid-template-rows: 54px 126px 44px 48px minmax(0, 1fr) 31px;
    gap: 6px;
    height: 100%;
    min-height: 0;
    padding: 14px;
    color: var(--ink-strong);
    font-family: 'Share Tech Mono', ui-monospace, monospace;
    font-variant-numeric: tabular-nums;
  }

  .top-orbit {
    display: flex;
    align-items: center;
    gap: 30px;
    border-bottom: 1px solid var(--ink-soft);
    padding: 0 4px 7px;
  }
  .orbit-item {
    display: grid;
    grid-template-rows: 13px 24px;
    min-width: 86px;
  }
  .orbit-label {
    color: var(--ink-mid);
    font-size: 11px;
    font-weight: 700;
    letter-spacing: 0.2em;
  }
  .orbit-value {
    font-size: 20px;
    font-weight: 700;
    letter-spacing: 0.05em;
  }

  .hero-frequency {
    position: relative;
    display: flex;
    align-items: center;
    justify-content: center;
    min-width: 0;
    overflow: hidden;
  }
  .receiver-tag {
    position: absolute;
    top: 8px;
    left: 4px;
    color: var(--ink-mid);
    font-size: 11px;
    font-weight: 700;
    letter-spacing: 0.2em;
  }
  .hero-frequency :global(.frequency) { font-size: 104px; }

  .secondary-frequency {
    display: flex;
    align-items: baseline;
    justify-content: center;
    gap: 10px;
    min-width: 0;
    border-bottom: 1px solid var(--ink-soft);
    padding-bottom: 5px;
    color: var(--ink-soft);
    overflow: hidden;
  }
  .secondary-frequency :global(.frequency) { color: currentColor; font-size: 32px; }
  .secondary-tag {
    font-size: 10px;
    font-weight: 700;
    letter-spacing: 0.18em;
    white-space: nowrap;
  }
  .secondary-mode, .secondary-filter {
    color: var(--ink-mid);
    font-size: 10px;
    letter-spacing: 0.1em;
  }
  .secondary-mode::after { content: ' ·'; }

  .centerstage-meter { min-width: 0; }
  .cinematic-scope {
    min-height: 0;
    border-top: 2px solid var(--ink-strong);
    border-bottom: 2px solid var(--ink-strong);
    padding-block: 4px;
  }
  .cinematic-scope :global(.scope-block) { height: 100%; }
  .cinematic-scope[data-identity='unknown'] { opacity: 0.34; }

  .telemetry-only {
    display: flex;
    align-items: center;
    justify-content: flex-end;
    gap: 14px;
    min-width: 0;
    border-top: 1px solid var(--ink-ghost);
    padding-top: 4px;
    color: var(--ink-telemetry);
    font-size: 10px;
  }
  .telemetry-only small { opacity: 0.7; font-size: inherit; }
  .telemetry-only .irrelevant { opacity: 0.34; }

  .orbit-item[data-state='unknown'],
  .hero-frequency[data-state='unknown'],
  .secondary-frequency[data-state='unknown'] { opacity: 0.34; }
  .orbit-item[data-state='unsupported'],
  .hero-frequency[data-state='unsupported'],
  .secondary-frequency[data-state='unsupported'],
  .centerstage-meter[data-state='unsupported'],
  .secondary-mode[data-state='unsupported'],
  .secondary-filter[data-state='unsupported'] { visibility: hidden; }
  .secondary-mode[data-state='unknown'],
  .secondary-filter[data-state='unknown'] { color: var(--ink-soft); }

</style>
