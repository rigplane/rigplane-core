<script lang="ts">
  import '../controls/control-button.css';
  import BarGauge from '../meters/BarGauge.svelte';
  import NeedleGauge from '../meters/NeedleGauge.svelte';
  import {
    normalize,
    formatPowerWatts,
    formatSwr,
    formatAlc,
    formatSMeter,
    getNeedleMarks,
    sLevel,
    type MeterSource,
  } from './meter-utils';

  interface Props {
    sValue: number;
    rfPower: number;
    swr: number;
    alc: number;
    txActive: boolean;
    meterSource: MeterSource;
    hasTx: boolean;
    onMeterSourceChange: (v: string) => void;
  }

  let {
    sValue,
    rfPower,
    swr,
    alc,
    txActive,
    meterSource,
    hasTx,
    onMeterSourceChange,
  }: Props = $props();

  let needleValue = $derived(
    meterSource === 'S'
      ? sLevel(sValue)
      : meterSource === 'SWR'
        ? normalize(swr)
        : normalize(rfPower),
  );

  let needleDisplayValue = $derived(
    meterSource === 'S'
      ? formatSMeter(sValue)
      : meterSource === 'SWR'
        ? formatSwr(swr)
        : formatPowerWatts(rfPower),
  );

  let needleLabel = $derived(
    meterSource === 'S' ? 'S' : meterSource === 'SWR' ? 'SWR' : 'POWER',
  );

  let needleMarks = $derived(getNeedleMarks(meterSource));

  let needleDangerZone = $derived(meterSource === 'SWR' ? 0.6 : 0.8);
</script>

<div class="panel">
  <div class="panel-header">METERS</div>
  <div class="panel-body">

    <div class="needle-section">
      <NeedleGauge
        value={needleValue}
        label={needleLabel}
        displayValue={needleDisplayValue}
        marks={needleMarks}
        dangerZone={needleDangerZone}
      />
    </div>

    <div class="source-selector">
      <button
        type="button"
        class="source-btn v2-control-button"
        class:active={meterSource === 'S'}
        style="--control-accent:var(--v2-accent-cyan); --control-active-text:var(--v2-text-bright)"
        onclick={() => onMeterSourceChange('S')}
      >S</button>
      {#if hasTx}
        <button
          type="button"
          class="source-btn v2-control-button"
          class:active={meterSource === 'SWR'}
          style="--control-accent:var(--v2-accent-cyan); --control-active-text:var(--v2-text-bright)"
          onclick={() => onMeterSourceChange('SWR')}
        >SWR</button>
        <button
          type="button"
          class="source-btn v2-control-button"
          class:active={meterSource === 'POWER'}
          style="--control-accent:var(--v2-accent-cyan); --control-active-text:var(--v2-text-bright)"
          onclick={() => onMeterSourceChange('POWER')}
        >Po</button>
      {/if}
    </div>

    {#if txActive}
      <div class="tx-meters">
        <BarGauge
          value={normalize(rfPower)}
          label="Po"
          displayValue={formatPowerWatts(rfPower)}
        />
        <BarGauge
          value={normalize(swr)}
          label="SWR"
          displayValue={formatSwr(swr)}
        />
        <BarGauge
          value={normalize(alc)}
          label="ALC"
          displayValue={formatAlc(alc)}
        />
      </div>
    {/if}

  </div>
</div>

<style>
  .panel {
    min-width: 0;
    width: 100%;
  }

  .source-selector {
    display: flex;
    justify-content: center;
    gap: 6px;
  }

  .source-btn {
    font-family: 'Roboto Mono', monospace;
    font-size: 11px;
    font-weight: 700;
    letter-spacing: 0.08em;
    text-transform: uppercase;
    padding: 2px 10px;
    border-radius: 9999px;
    border: 1px solid var(--v2-border);
    background: transparent;
    color: var(--v2-text-muted);
    cursor: pointer;
    user-select: none;
    transition: color 150ms ease, border-color 150ms ease, background 150ms ease;
  }

  .source-btn:hover {
    color: var(--v2-text-header);
    border-color: var(--v2-text-muted);
  }

  .source-btn.active {
    color: var(--v2-accent-cyan);
    border-color: var(--v2-accent-cyan);
    background: var(--v2-meter-panel-glow);
  }

  .tx-meters {
    display: flex;
    flex-direction: column;
    gap: 4px;
  }
</style>
