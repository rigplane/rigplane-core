<!--
  MOR-2299 slice 1 — pure presentation for one receiver-addressed indicator
  entry. The component reads only the semantic contract handed to it. Shared
  ANT/TUNE/RIT/XIT facts and DUAL actions belong to slice 2.
-->
<script lang="ts">
  import LinearSMeter from '../components-v2/meters/LinearSMeter.svelte';
  import type {
    RadioWideIndicatorsViewModel, ReceiverIndicatorField,
    ReceiverIndicatorViewModel, TxAuxField,
  } from './radio-view-model';

  interface Props {
    indicator?: ReceiverIndicatorViewModel;
    radioWide?: RadioWideIndicatorsViewModel;
  }

  let { indicator, radioWide }: Props = $props();

  const rfLabel = (state: RadioWideIndicatorsViewModel['rfState']): string =>
    state === 'transmitting' ? 'TX'
      : state === 'receiving' ? 'RX'
        : state === 'uncertain' ? 'TX?'
          : 'RF ?';

  function numeric(field: ReceiverIndicatorField<number>): string {
    return field.reading.status === 'known' ? String(field.reading.value) : '—';
  }

  function agc(field: ReceiverIndicatorViewModel['agcMode']): string {
    return field.reading.status === 'known' ? String(field.reading.value) : '—';
  }

  function booleanState(field: ReceiverIndicatorField<boolean>): 'on' | 'off' | 'unknown' {
    return field.reading.status === 'known' ? (field.reading.value ? 'on' : 'off') : 'unknown';
  }

  function booleanLabel(field: ReceiverIndicatorField<boolean>): string {
    const state = booleanState(field);
    return state === 'unknown' ? '—' : state.toUpperCase();
  }

  function sharedBoolean(field: TxAuxField<boolean>): string {
    return field.reading.status === 'known' ? (field.reading.value ? 'ON' : 'OFF') : '—';
  }

  function sharedNumber(field: TxAuxField<number>): string {
    return field.reading.status === 'known' ? String(field.reading.value) : '—';
  }

</script>

{#if indicator}
<section
  class="indicator-row"
  data-testid="vfo-indicator-row"
  data-indicator-receiver={indicator.receiver}
  data-indicator-operational={indicator.availability.operational}
  aria-label={`${indicator.receiver} receiver indicators`}
>
  <header>
    <strong>{indicator.receiver}</strong>
    {#if indicator.bandwidthHz.availability.structural}
      <span
        class="fact"
        data-indicator-fact="bandwidth"
        data-state={indicator.bandwidthHz.reading.status}
      >BW {numeric(indicator.bandwidthHz)}{indicator.bandwidthHz.reading.status === 'known' ? ' Hz' : ''}</span>
    {/if}
  </header>

  <div class="s-meter" data-testid="receiver-s-meter" data-receiver={indicator.receiver}>
    {#if indicator.sMeter.reading.status === 'known' && Number.isFinite(indicator.sMeter.reading.value)}
      <LinearSMeter value={indicator.sMeter.reading.value} compact variant="vfo-wide" />
    {:else}
      <div
        class="s-meter-unknown"
        data-testid="receiver-s-meter-unknown"
        role="img"
        aria-label={`${indicator.receiver} S meter unknown`}
      >S —</div>
    {/if}
  </div>

  <div class="facts" aria-label={`${indicator.receiver} receiver facts`}>
    {#if indicator.agcMode.availability.structural}
      <span class="fact" data-indicator-fact="agc" data-state={indicator.agcMode.reading.status}>
        AGC {agc(indicator.agcMode)}
      </span>
    {/if}
    {#if indicator.nbActive.availability.structural}
      <span class="fact" data-indicator-fact="nb" data-state={booleanState(indicator.nbActive)}>
        NB {booleanLabel(indicator.nbActive)}
      </span>
    {/if}
    {#if indicator.nrActive.availability.structural}
      <span class="fact" data-indicator-fact="nr" data-state={booleanState(indicator.nrActive)}>
        NR {booleanLabel(indicator.nrActive)}
      </span>
    {/if}
    {#if indicator.notchMode.availability.structural}
      <span class="fact" data-indicator-fact="notch" data-state={indicator.notchMode.reading.status}>
        NOTCH {indicator.notchMode.reading.status === 'known' ? indicator.notchMode.reading.value.toUpperCase() : '—'}
      </span>
    {/if}
    {#if indicator.attenuator.availability.structural}
      <span class="fact" data-indicator-fact="attenuator" data-state={indicator.attenuator.reading.status}>
        ATT {numeric(indicator.attenuator)}{indicator.attenuator.reading.status === 'known' ? ' dB' : ''}
      </span>
    {/if}
    {#if indicator.preamp.availability.structural}
      <span class="fact" data-indicator-fact="preamp" data-state={indicator.preamp.reading.status}>
        P.AMP {numeric(indicator.preamp)}
      </span>
    {/if}
    {#if indicator.ipPlus.availability.structural}
      <span class="fact" data-indicator-fact="ip-plus" data-state={booleanState(indicator.ipPlus)}>
        IP+ {booleanLabel(indicator.ipPlus)}
      </span>
    {/if}
    {#if indicator.digiSel.availability.structural}
      <span class="fact" data-indicator-fact="digi-sel" data-state={booleanState(indicator.digiSel)}>
        DIGI-SEL {booleanLabel(indicator.digiSel)}
      </span>
    {/if}
    {#if indicator.rfGain.availability.structural}
      <span class="fact" data-indicator-fact="rf-gain" data-state={indicator.rfGain.reading.status}>
        RFG {numeric(indicator.rfGain)}
      </span>
    {/if}
  </div>
</section>
{/if}

{#if radioWide}
  <section
    class="indicator-row shared-indicators"
    data-testid="vfo-shared-indicators"
    aria-label="Radio-wide indicators"
  >
    <div class="facts" aria-label="Radio-wide facts">
      <span
        class:tx={radioWide.rfState === 'transmitting'}
        class:rx={radioWide.rfState === 'receiving'}
        class="rf-lamp"
        data-indicator-fact="rf-authority"
        data-indicator-rf={radioWide.rfState}
      >{rfLabel(radioWide.rfState)}</span>
      {#if radioWide.antenna.availability.structural}
        <span class="fact" data-indicator-fact="antenna" data-state={radioWide.antenna.reading.status}>
          ANT {sharedNumber(radioWide.antenna)}
        </span>
      {/if}
      {#if radioWide.atu.availability.structural}
        <span class="fact" data-indicator-fact="atu" data-state={radioWide.atu.reading.status}>
          TUNE {radioWide.atu.reading.status === 'known' ? radioWide.atu.reading.value.toUpperCase() : '—'}
        </span>
      {/if}
      {#if radioWide.ritActive.availability.structural || radioWide.ritOffset.availability.structural}
        <span class="fact" data-indicator-fact="rit" data-state={radioWide.ritActive.reading.status}>
          RIT {sharedBoolean(radioWide.ritActive)} {sharedNumber(radioWide.ritOffset)} Hz
        </span>
      {/if}
      {#if radioWide.xitActive.availability.structural || radioWide.xitOffset.availability.structural}
        <span class="fact" data-indicator-fact="xit" data-state={radioWide.xitActive.reading.status}>
          XIT {sharedBoolean(radioWide.xitActive)} {sharedNumber(radioWide.xitOffset)} Hz
        </span>
      {/if}
    </div>
  </section>
{/if}

<style>
  .indicator-row {
    display: grid;
    gap: 4px;
    min-width: 0;
    padding: 5px 7px;
    border: 1px solid var(--v2-border-panel, rgba(255, 255, 255, 0.12));
    border-radius: 4px;
    background: var(--v2-bg-panel, rgba(255, 255, 255, 0.03));
  }
  .indicator-row[data-indicator-operational='false'] { opacity: 0.56; }
  header, .facts { display: flex; align-items: center; gap: 5px; flex-wrap: wrap; }
  header strong { color: var(--v2-text-secondary, rgba(255, 255, 255, 0.8)); }
  .rf-lamp, .fact {
    padding: 1px 4px;
    border: 1px solid var(--v2-border-panel, rgba(255, 255, 255, 0.12));
    border-radius: 3px;
    font-size: 10px;
    line-height: 1.4;
  }
  .rf-lamp.tx { color: var(--v2-accent-red, #ff4545); border-color: currentColor; }
  .rf-lamp.rx { color: var(--v2-accent-cyan, #00d4ff); border-color: currentColor; }
  .fact[data-state='on'], .fact[data-state='known'] { color: var(--v2-text-primary, #e8e8e8); }
  .fact[data-state='off'], .fact[data-state='unknown'] { color: var(--v2-text-subdued, rgba(255, 255, 255, 0.55)); }
  .s-meter { min-width: 0; overflow: hidden; }
  .s-meter-unknown {
    display: grid;
    min-height: 30px;
    place-items: center;
    border: 1px solid var(--v2-border-panel, rgba(255, 255, 255, 0.12));
    color: var(--v2-text-subdued, rgba(255, 255, 255, 0.55));
    font-size: 11px;
  }
</style>
