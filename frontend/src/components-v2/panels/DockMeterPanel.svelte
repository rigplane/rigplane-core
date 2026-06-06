<script lang="ts">
  import {
    alcLevel,
    formatAlc,
    formatPowerWatts,
    formatSMeter,
    formatSwr,
    normalizePower,
    sLevel,
    swrLevel,
    type MeterSource,
  } from './meter-utils';

  interface Props {
    sValue: number;
    rfPower: number;
    swr: number;
    alc: number;
    txActive: boolean;
    meterSource: MeterSource;
    onMeterSourceChange: (v: string) => void;
  }

  let { sValue, rfPower, swr, alc, txActive, meterSource, onMeterSourceChange }: Props = $props();

  const scaleLabels = [
    { label: '0', left: '11%' },
    { label: '10', left: '27%' },
    { label: '25', left: '43%' },
    { label: '50', left: '63%' },
    { label: '100', left: '86%' },
    { label: '%', left: '97%' },
  ] as const;

  let sourceSummary = $derived(
    meterSource === 'S'
      ? { label: 'S', value: formatSMeter(sValue) }
      : meterSource === 'SWR'
        ? { label: 'SWR', value: formatSwr(swr) }
        : { label: 'Po', value: formatPowerWatts(rfPower) },
  );

  // Each row carries its own calibrated 0-1 bar-fill normalizer (MOR-482).
  // Previously every row used `normalize(value)` = raw/255, so the bar
  // disagreed with the calibrated number (e.g. SWR 3.0 → 47% bar instead of
  // ~100%). `fillPct` maps each meter to its matching calibrated normalizer:
  // S→sLevel, Po→normalizePower, SWR→swrLevel, ALC→alcLevel.
  let rows = $derived([
    {
      key: 'S',
      label: 'S',
      value: sValue,
      display: formatSMeter(sValue),
      fillPct: sLevel(sValue) * 100,
      fill: 'var(--v2-meter-s-fill)',
      track: 'var(--v2-meter-s-track)',
      valueClass: 's',
      relevant: !txActive,
    },
    {
      key: 'POWER',
      label: 'Po',
      value: rfPower,
      display: formatPowerWatts(rfPower),
      fillPct: normalizePower(rfPower) * 100,
      fill: 'var(--v2-meter-power-fill)',
      track: 'var(--v2-meter-power-track)',
      valueClass: 'po',
      relevant: txActive,
    },
    {
      key: 'SWR',
      label: 'SWR',
      value: swr,
      display: formatSwr(swr),
      fillPct: swrLevel(swr) * 100,
      fill: 'var(--v2-meter-swr-fill)',
      track: 'var(--v2-meter-swr-track)',
      valueClass: 'swr',
      relevant: txActive,
    },
    {
      key: 'alc',
      label: 'ALC',
      value: alc,
      display: formatAlc(alc).replace('%', ''),
      fillPct: alcLevel(alc) * 100,
      fill: 'var(--v2-meter-alc-fill)',
      track: 'var(--v2-meter-alc-track)',
      valueClass: 'alc',
      relevant: txActive,
    },
  ]);
</script>

<article class="dock-meter-panel dock-meter-card">
  <div class="dock-topline">
    <span class="dock-title">METER</span>
    <div class="dock-scale">
      {#each scaleLabels as item (item.label)}
        <span class="dock-scale-label" style:left={item.left}>{item.label}</span>
      {/each}
    </div>
    <div class="dock-status">
      <span class="status-tag source" data-source={meterSource}>{sourceSummary.label} {sourceSummary.value}</span>
      <span class="status-tag tx" data-active={txActive}>{txActive ? 'TX' : 'RX'}</span>
    </div>
  </div>

  <div class="meter-source-selector" role="group" aria-label="Meter source selector">
    <button
      type="button"
      class="meter-source-btn"
      class:active={meterSource === 'S'}
      onclick={() => onMeterSourceChange('S')}
    >S</button>
    <button
      type="button"
      class="meter-source-btn"
      class:active={meterSource === 'SWR'}
      onclick={() => onMeterSourceChange('SWR')}
    >SWR</button>
    <button
      type="button"
      class="meter-source-btn"
      class:active={meterSource === 'POWER'}
      onclick={() => onMeterSourceChange('POWER')}
    >Po</button>
  </div>

  <div class="dock-rows">
    {#each rows as row (row.key)}
      <div class="dock-row" data-active={row.key === meterSource} data-relevant={row.relevant}>
        <span class="dock-row-label">{row.label}</span>
        <div class="dock-bar" style:background={row.track}>
          <div class="dock-bar-fill" style:width={`${row.fillPct}%`} style:background={row.fill}></div>
        </div>
        <span class={`dock-row-value ${row.valueClass}`}>{row.display}</span>
      </div>
    {/each}
  </div>
</article>

<style>
  .dock-meter-panel {
    flex: 0 0 420px;
    display: flex;
    flex-direction: column;
    gap: 6px;
    padding: 8px;
    border: 1px solid var(--v2-border-darker);
    border-radius: 4px;
    background: linear-gradient(180deg, var(--v2-bg-gradient-start) 0%, var(--v2-bg-darkest) 100%);
    box-sizing: border-box;
  }

  .dock-topline {
    display: grid;
    grid-template-columns: 30px minmax(0, 1fr) auto;
    align-items: center;
    gap: 10px;
  }

  .dock-title,
  .dock-scale-label,
  .status-tag,
  .dock-row-label,
  .dock-row-value {
    font-family: 'Roboto Mono', monospace;
  }

  .dock-title {
    color: var(--v2-text-light);
    font-size: 11px;
    font-weight: 700;
    letter-spacing: 0.16em;
  }

  .dock-scale {
    position: relative;
    height: 18px;
  }

  .dock-scale-label {
    position: absolute;
    top: 0;
    transform: translateX(-50%);
    color: var(--v2-text-light);
    font-size: 9px;
    font-weight: 700;
    opacity: 0.92;
  }

  .dock-status {
    display: flex;
    align-items: center;
    gap: 6px;
  }

  .status-tag {
    display: inline-flex;
    align-items: center;
    justify-content: center;
    min-height: 18px;
    padding: 0 8px;
    border: 1px solid transparent;
    border-radius: 4px;
    font-size: 9px;
    font-weight: 700;
    letter-spacing: 0.08em;
  }

  .status-tag.source {
    border-color: var(--v2-meter-source-s-border);
    color: var(--v2-meter-source-s-text);
    background: var(--v2-meter-source-s-bg);
  }

  .status-tag.source[data-source='SWR'] {
    border-color: var(--v2-meter-source-swr-border);
    color: var(--v2-meter-source-swr-text);
    background: var(--v2-meter-source-swr-bg);
  }

  .status-tag.source[data-source='POWER'] {
    border-color: var(--v2-meter-source-power-border);
    color: var(--v2-meter-source-power-text);
    background: var(--v2-meter-source-power-bg);
  }

  .status-tag.tx {
    border-color: var(--v2-meter-tx-border);
    color: var(--v2-meter-tx-text);
    background: var(--v2-meter-tx-bg);
  }

  .status-tag.tx[data-active='true'] {
    color: var(--v2-meter-tx-active-text);
    background: var(--v2-meter-tx-active-bg);
  }

  .dock-rows {
    display: flex;
    flex-direction: column;
    gap: 6px;
  }

  .meter-source-selector {
    display: inline-flex;
    align-self: flex-start;
    gap: 6px;
  }

  .meter-source-btn {
    min-height: 22px;
    padding: 0 10px;
    border: 1px solid var(--v2-border);
    border-radius: 999px;
    background: transparent;
    color: var(--v2-text-muted);
    font-family: 'Roboto Mono', monospace;
    font-size: 10px;
    font-weight: 700;
    letter-spacing: 0.08em;
    cursor: pointer;
    transition: border-color 150ms ease, color 150ms ease, background 150ms ease;
  }

  .meter-source-btn:hover {
    border-color: var(--v2-border-soft);
    color: var(--v2-text-secondary);
  }

  .meter-source-btn.active {
    border-color: var(--v2-accent-cyan);
    color: var(--v2-text-white);
    background: var(--v2-meter-active-glow);
  }

  .dock-row {
    display: grid;
    grid-template-columns: 30px minmax(0, 1fr) 48px;
    align-items: center;
    gap: 10px;
    transition: opacity 200ms ease;
  }

  .dock-row[data-relevant='false'] {
    opacity: 0.3;
  }

  .dock-row[data-active='true'] .dock-row-label,
  .dock-row[data-active='true'] .dock-row-value {
    color: var(--v2-text-white);
  }

  .dock-row[data-active='true'] .dock-bar {
    border-color: var(--v2-border);
    box-shadow: inset 0 0 0 1px var(--v2-meter-active-shadow);
  }

  .dock-row-label {
    color: var(--v2-text-primary);
    font-size: 11px;
    font-weight: 700;
  }

  .dock-bar {
    position: relative;
    height: 8px;
    border: 1px solid var(--v2-border-dark);
    border-radius: 1px;
    overflow: hidden;
    background-size: 100% 100%;
  }

  .dock-bar-fill {
    height: 100%;
    border-right: 1px solid var(--v2-meter-bar-divider);
  }

  .dock-row-value {
    text-align: right;
    font-size: 11px;
    font-weight: 700;
  }

  .dock-row-value.po {
    color: var(--v2-text-primary);
  }

  .dock-row-value.s {
    color: var(--v2-accent-cyan-bright);
  }

  .dock-row-value.swr {
    color: var(--v2-accent-green-bright);
  }

  .dock-row-value.alc {
    color: var(--v2-accent-yellow);
  }

  @media (max-width: 1200px) {
    .dock-meter-panel {
      flex-basis: 100%;
    }
  }
</style>