<script lang="ts">
  import { calibratedToSegments } from '../../components-v2/meters/smeter-scale';
  import {
    DIGIT_CELL_EM, DOT_CELL_EM, renderFrequency,
  } from '../../presentation/languages/segmentline/frequency-renderer';
  import { SEGMENTLINE_TOKENS } from '../../presentation/languages/segmentline/tokens';
  import LcdAfFft, { type LcdAfFftInputState } from './LcdAfFft.svelte';
  import LcdStatusIcon, { type LcdStatusIconName } from './LcdStatusIcon.svelte';
  import type {
    DisplayIndicator, DisplayOffset, DisplayTelemetry, DisplayValue,
    PeerSplitDisplayModel, PeerSplitReceiverDisplay,
  } from '../../semantic/radio-display-model';

  interface Props {
    model: PeerSplitDisplayModel;
    normalizedFftBins?: Partial<Record<'MAIN' | 'SUB', readonly number[]>>;
  }
  let { model, normalizedFftBins = {} }: Props = $props();

  const stateText = <T,>(field: DisplayValue<T>): string =>
    field.state === 'known' ? String(field.value) : field.state === 'unknown' ? '?' : '—';

  const stateClass = (state: DisplayIndicator['state']): string => state;

  const flagText = (label: string, field: DisplayIndicator, value?: string): string =>
    `${label}${value ? ` ${value}` : ''}`;

  const formatBandwidth = (field: DisplayValue<number>): string => {
    if (field.state !== 'known') return stateText(field);
    if (field.value >= 1000) return `${Number((field.value / 1000).toFixed(2))}k`;
    return String(Math.round(field.value));
  };

  const formatOffset = (field: DisplayOffset): string => {
    if (field.state === 'unknown') return '?';
    if (field.state === 'unsupported') return '—';
    if (field.offsetHz === undefined) return '—';
    const sign = field.offsetHz < 0 ? '−' : '+';
    return `${sign}${(Math.abs(field.offsetHz) / 1000).toFixed(3)}`;
  };

  const frequency = (field: DisplayValue<number>) => renderFrequency(
    { kind: 'frequency', fields: { frequencyHz: field.state === 'known' ? field.value : null } },
    SEGMENTLINE_TOKENS,
  );

  const meterFill = (field: DisplayValue<number>): number => field.state === 'known'
    ? Math.max(0, Math.min(1, calibratedToSegments(field.value) / 20))
    : 0;

  const topLeft = $derived([
    { label: 'VOX', icon: 'VOX' as LcdStatusIconName, field: model.top.vox },
    { label: 'PROC', icon: 'PROC' as LcdStatusIconName, field: model.top.compressor },
    { label: 'SPLIT', icon: 'SPLIT' as LcdStatusIconName, field: model.top.split },
    { label: 'LOCK', icon: 'LOCK' as LcdStatusIconName, field: { state: 'unsupported' } as DisplayIndicator },
    { label: 'RIT', icon: 'RIT' as LcdStatusIconName, field: model.top.rit },
  ]);

  const topRight = $derived([
    { label: 'TX', icon: 'TX' as LcdStatusIconName, field: model.top.tx },
    { label: 'TUNE', icon: 'TUNE' as LcdStatusIconName, field: model.top.tune },
    { label: 'ATU', icon: 'ATU' as LcdStatusIconName, field: model.top.atu },
    { label: 'PROC', icon: 'PROC' as LcdStatusIconName, field: model.top.compressor },
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

  const notchFlags = $derived.by((): {
    notch: DisplayIndicator; anf: DisplayIndicator;
  } => {
    if (activeDsp.notch.state !== 'known') {
      const state = activeDsp.notch.state;
      return { notch: { state }, anf: { state } };
    }
    return {
      notch: { state: activeDsp.notch.value === 'manual' ? 'active' : 'inactive' },
      anf: { state: activeDsp.notch.value === 'auto' ? 'active' : 'inactive' },
    };
  });

  const dspFlags = $derived([
    { label: 'NB', icon: 'NB' as LcdStatusIconName, field: activeDsp.nb },
    { label: 'NR', icon: 'NR' as LcdStatusIconName, field: activeDsp.nr },
    {
      label: 'NOTCH', icon: 'NOTCH' as LcdStatusIconName,
      field: notchFlags.notch,
    },
    { label: 'ANF', icon: 'ANF' as LcdStatusIconName, field: notchFlags.anf },
  ]);

  const frontFlags = $derived([
    {
      label: 'PRE', icon: 'PRE' as LcdStatusIconName, value: stateText(activeFront.preamp),
      field: activeFront.preamp.state === 'known'
        ? { state: activeFront.preamp.value > 0 ? 'active' : 'inactive' } as DisplayIndicator
        : { state: activeFront.preamp.state } as DisplayIndicator,
    },
    {
      label: 'ATT', icon: 'ATT' as LcdStatusIconName, value: stateText(activeFront.attenuator),
      field: activeFront.attenuator.state === 'known'
        ? { state: activeFront.attenuator.value > 0 ? 'active' : 'inactive' } as DisplayIndicator
        : { state: activeFront.attenuator.state } as DisplayIndicator,
    },
    { label: 'DIGI', icon: null as LcdStatusIconName | null, field: activeFront.digiSel },
    { label: 'IP+', icon: 'IPP' as LcdStatusIconName, field: activeFront.ipPlus },
  ]);

  const telemetry = $derived([
    { label: 'VD', field: model.telemetry.drainVoltage },
    { label: 'ID', field: model.telemetry.drainCurrent },
    { label: 'PWR', field: model.telemetry.power },
    { label: 'SWR', field: model.telemetry.swr },
    { label: 'ALC', field: model.telemetry.alc },
    { label: 'COMP', field: model.telemetry.compression },
  ]);

  const telemetryText = (field: DisplayTelemetry): string =>
    field.state === 'known' ? String(Number(field.value.toFixed(2))) : '?';

  interface FilterEnvelope { points: string; kind: 'single' | 'inner' | 'outer'; centerX: number }

  const envelope = (
    field: DisplayValue<number>, centerHz: number, kind: FilterEnvelope['kind'],
  ): FilterEnvelope | null => {
    if (field.state !== 'known') return null;
    const width = 500;
    const height = 100;
    const top = 4;
    const bottom = height - 4;
    const center = Math.max(0, Math.min(width, width / 2 + (centerHz / 9000) * width));
    const half = Math.min(width * 0.45, (field.value / 9000) * width / 2);
    const slope = width * 0.08;
    return {
      kind, centerX: center,
      points: `${center - half - slope},${bottom} ${center - half},${top + 2} `
        + `${center + half},${top + 2} ${center + half + slope},${bottom}`,
    };
  };

  const filterEnvelopes = (receiver: PeerSplitReceiverDisplay): FilterEnvelope[] => {
    const inner = receiver.pbtInnerHz;
    const outer = receiver.pbtOuterHz;
    if (inner.state === 'unknown' || outer.state === 'unknown') return [];
    if (inner.state === 'known' && outer.state === 'known' && inner.value !== outer.value) {
      return [
        envelope(receiver.bandwidthHz, inner.value, 'inner'),
        envelope(receiver.bandwidthHz, outer.value, 'outer'),
      ].filter((item): item is FilterEnvelope => item !== null);
    }
    if (receiver.ifShiftHz.state === 'unknown') return [];
    const centerHz = receiver.ifShiftHz.state === 'known' ? receiver.ifShiftHz.value : 0;
    const single = envelope(receiver.bandwidthHz, centerHz, 'single');
    return single ? [single] : [];
  };

  const fftInputState = (receiver: PeerSplitReceiverDisplay): LcdAfFftInputState => {
    if (receiver.spectrum === 'unsupported') return 'unsupported';
    if (receiver.spectrum === 'unknown') return 'unknown';
    if (receiver.spectrum === 'inactive') return 'missing';
    return normalizedFftBins[receiver.receiver]?.length ? 'live' : 'missing';
  };
</script>

<div class="peer-display" data-testid="peer-split-display" data-rf-state={model.rfState}>
  <header class="status-rail">
    <div class="flag-zone">
      <span class="zone-label">OP</span>
      {#each topLeft as item}
        <span class="status-flag {stateClass(item.field.state)}" data-state={item.field.state}>
          <LcdStatusIcon name={item.icon} />{item.label}
        </span>
      {/each}
    </div>
    <div class="status-spacer" aria-hidden="true"></div>
    <div class="flag-zone end">
      <span class="zone-label">TX</span>
      {#each topRight as item}
        <span class="status-flag {stateClass(item.field.state)}" data-state={item.field.state}>
          <LcdStatusIcon name={item.icon} />{item.label}
        </span>
      {/each}
    </div>
  </header>

  <div class="receiver-deck">
    {#each model.receivers as receiver (receiver.receiver)}
      {@const freq = frequency(receiver.frequency)}
      {@const envelopes = filterEnvelopes(receiver)}
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

        <div class="frequency" data-testid={`lcd-frequency-${receiver.receiver}`} data-state={receiver.frequency.state}>
          {#if freq.groups.length > 0}
            {#each freq.groups as group}
              <span class:ranked={group.rank === 'ranked'} class="frequency-group">
                {#each group.cells as cell}
                  <span
                    class:separator={cell.isSeparator}
                    class="frequency-cell"
                    style:width={`${cell.isSeparator ? DOT_CELL_EM : DIGIT_CELL_EM}em`}
                  >{cell.char}</span>
                {/each}
              </span>
            {/each}
          {:else}
            <span class="frequency-unknown">—.———.———</span>
          {/if}
        </div>

        <div class="offsets">
          {#each [
            { label: 'RIT', field: model.offsets.rit },
            { label: 'XIT', field: model.offsets.xit },
            { label: 'SPLIT', field: model.offsets.split },
          ] as item}
            <span
              class="offset {item.field.state}"
              data-testid={`lcd-offset-${receiver.receiver}-${item.label.toLowerCase()}`}
              data-state={item.field.state}
            >
              <span class="offset-label">{item.label}</span>
              <span class="offset-value">{formatOffset(item.field)}<small>kHz</small></span>
            </span>
          {/each}
        </div>

        <div class="s-meter" data-state={receiver.sMeter.state}>
          <span class="meter-label">S</span>
          <div class="meter-track">
            <div class="meter-fill" style:width={`${meterFill(receiver.sMeter) * 100}%`}></div>
            <span class="meter-threshold"></span>
          </div>
          <div class="meter-scale" aria-hidden="true">
            <span>1</span><span>3</span><span>5</span><span>7</span><span>9</span><span>+20</span><span>+40</span><span>+60</span>
          </div>
        </div>

        <div class="scope-block" data-scope-state={receiver.spectrum}>
          <span class="scope-label" data-testid={`lcd-scope-label-${receiver.receiver}`}>
            {receiver.spectrum === 'unsupported' ? 'BANDPASS' : 'AF SCOPE · BANDPASS'}
          </span>
          <div class="scope-plot">
            <LcdAfFft
              normalizedBins={normalizedFftBins[receiver.receiver]}
              inputState={fftInputState(receiver)}
              receiverActivity={receiver.activity}
            />
            <svg
              class="filter-overlay"
              data-testid="lcd-filter-envelope"
              viewBox="0 0 500 100"
              preserveAspectRatio="none"
              aria-label={`${receiver.receiver} passive filter envelope`}
            >
              {#if envelopes.length > 0}
                {#each envelopes as item}
                  <polyline class="filter-envelope {item.kind}" points={item.points} fill="none" />
                {/each}
                {@const centerX = envelopes.reduce((sum, item) => sum + item.centerX, 0) / envelopes.length}
                <line class="filter-center" x1={centerX} x2={centerX} y1="4" y2="12" />
                <text class="filter-label" x={centerX} y="16" text-anchor="middle">
                  {formatBandwidth(receiver.bandwidthHz)} Hz{envelopes.length > 1 ? ' PBT' : ''}
                </text>
              {/if}
            </svg>
          </div>
        </div>
      </section>
    {/each}
  </div>

  <div class="fact-rail">
    <div class="flag-zone">
      <span class="zone-label">DSP</span>
      {#each dspFlags as item}
        <span
          class="status-flag {stateClass(item.field.state)}"
          data-state={item.field.state}
          data-status-label={item.label}
        >
          <LcdStatusIcon name={item.icon} />{item.label}
        </span>
      {/each}
      <span
        class="status-flag {activeDsp.agc.state === 'known' ? 'active' : activeDsp.agc.state}"
        data-state={activeDsp.agc.state}
      ><LcdStatusIcon name="AGC" />AGC {stateText(activeDsp.agc)}</span>
    </div>
    <div class="flag-zone end">
      <span class="zone-label">FRONT</span>
      {#each frontFlags as item}
        <span class="status-flag {stateClass(item.field.state)}" data-state={item.field.state}>
          {#if item.icon}<LcdStatusIcon name={item.icon} />{/if}
          {flagText(item.label, item.field, 'value' in item ? item.value : undefined)}
        </span>
      {/each}
    </div>
  </div>

  <footer class="aux-rail">
    <div class="memory-seam" data-state="unsupported" aria-hidden="true">
      {#each [0, 1, 2, 3] as slot}<span data-memory-slot={slot}>MEM</span>{/each}
    </div>
    <div class="telemetry">
      {#each telemetry as item}
        <span class:irrelevant={!item.field.relevant} data-state={item.field.state}>
          <small>{item.label}</small> {telemetryText(item.field)}
        </span>
      {/each}
    </div>
  </footer>
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

  .status-rail, .fact-rail, .aux-rail {
    display: flex;
    align-items: center;
    justify-content: space-between;
    min-width: 0;
  }
  .status-rail { border-bottom: 1px solid var(--ink-soft); padding-bottom: 6px; }
  .fact-rail { border-top: 1px solid var(--ink-soft); padding-top: 6px; }
  .aux-rail { border-top: 1px solid var(--ink-ghost); padding-top: 4px; }
  .status-spacer { min-width: 120px; }
  .flag-zone { display: flex; align-items: center; gap: 4px; min-width: 0; }
  .flag-zone.end { justify-content: flex-end; }
  .zone-label { margin-right: 4px; color: var(--ink-mid); font-size: 11px; font-weight: 700; letter-spacing: 0.2em; }
  .status-flag {
    display: inline-flex;
    align-items: center;
    gap: 5px;
    min-height: 23px;
    box-sizing: border-box;
    padding: 2px 8px;
    border: 1.25px solid currentColor;
    border-radius: 2px;
    color: var(--ink-ghost);
    font-size: 12px;
    font-weight: 700;
    letter-spacing: 0.1em;
    line-height: 1.2;
    white-space: nowrap;
  }
  .status-flag.active { color: var(--ink-strong); }
  .status-flag.inactive { color: var(--ink-ghost); }
  .status-flag.unknown { color: var(--ink-soft); }
  .status-flag.unsupported { visibility: hidden; }
  .status-flag[data-state='active'][data-state='active'] { background: rgba(26, 16, 0, 0.06); }
  .peer-display[data-rf-state='transmitting'] .status-flag.active { color: #7a1a0a; }

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

  .frequency {
    display: inline-flex;
    align-items: baseline;
    align-self: start;
    width: max-content;
    max-width: 100%;
    overflow: hidden;
    font-family: 'DSEG7 Classic', 'Share Tech Mono', ui-monospace, monospace;
    font-size: 78px;
    font-weight: 700;
    letter-spacing: 0.02em;
    line-height: 1;
    white-space: nowrap;
  }
  .frequency-group { display: inline-flex; align-items: baseline; font-size: 1em; }
  .frequency-group.ranked { color: var(--ink-mid); font-size: 62%; }
  .frequency-cell { display: inline-block; flex: 0 0 auto; text-align: center; }
  .frequency-unknown { color: var(--ink-soft); }

  .offsets { display: flex; gap: 6px; align-items: stretch; }
  .offset {
    display: inline-flex;
    flex: 1 1 0;
    flex-direction: column;
    min-width: 0;
    padding: 2px 8px;
    border: 1px solid var(--ink-soft);
    border-radius: 2px;
    color: var(--ink-mid);
    line-height: 1.15;
  }
  .offset.active { border-color: var(--ink-mid); color: var(--ink-strong); }
  .offset.unknown { color: var(--ink-soft); }
  .offset.unsupported { visibility: hidden; }
  .offset-label { font-size: 10px; font-weight: 700; letter-spacing: 0.18em; }
  .offset-value { font-family: 'DSEG7 Classic', monospace; font-size: 16px; font-weight: 700; letter-spacing: 0.02em; }
  .offset:not(.active) .offset-value { opacity: 0.25; }
  .offset-value small { margin-left: 4px; color: var(--ink-mid); font-family: 'Share Tech Mono', monospace; font-size: 9px; }

  .s-meter { display: grid; grid-template-columns: 30px minmax(0, 1fr); grid-template-rows: 28px 12px; align-items: center; column-gap: 7px; }
  .meter-label { font-size: 11px; font-weight: 700; letter-spacing: 0.1em; }
  .meter-track { position: relative; height: 28px; border: 1px solid var(--ink-soft); }
  .meter-fill { position: absolute; inset-block: 2px; left: 2px; max-width: calc(100% - 4px); background: repeating-linear-gradient(90deg, var(--ink-strong) 0 8px, transparent 8px 10px); }
  .meter-threshold { position: absolute; top: -4px; bottom: -4px; left: 56%; width: 1px; background: var(--ink-strong); }
  .meter-scale { grid-column: 2; display: flex; justify-content: space-between; color: var(--ink-mid); font-size: 8px; }
  .s-meter[data-state='unknown'], .s-meter[data-state='unsupported'] { opacity: 0.3; }

  .scope-block { display: grid; grid-template-rows: 16px minmax(0, 1fr); min-height: 0; }
  .scope-label { color: var(--ink-mid); font-size: 11px; font-weight: 700; letter-spacing: 0.18em; }
  .scope-plot { position: relative; min-height: 0; }
  .scope-plot :global(.filter-overlay) { position: absolute; inset: 0; width: 100%; height: 100%; overflow: visible; }
  .filter-envelope { stroke: var(--ink-strong); stroke-width: 1.6; stroke-linejoin: miter; }
  .filter-envelope.inner { stroke-dasharray: 6 3; stroke-width: 1.4; }
  .filter-envelope.outer { stroke-dasharray: 0.1 4; stroke-linecap: round; stroke-width: 2.2; }
  .filter-center { stroke: var(--ink-strong); stroke-width: 1.2; }
  .filter-label, .scope-state { fill: var(--ink-strong); font-family: 'Share Tech Mono', monospace; font-size: 11px; font-weight: 700; }

  .memory-seam { display: flex; gap: 5px; color: var(--ink-ghost); font-size: 9px; letter-spacing: 0.1em; }
  .memory-seam span { min-width: 42px; padding: 2px 5px; border: 1px solid currentColor; }
  .memory-seam[data-state='unsupported'] { visibility: hidden; }
  .telemetry { display: flex; gap: 14px; align-items: center; color: var(--ink-telemetry); font-size: 10px; }
  .telemetry small { opacity: 0.7; font-size: inherit; }
  .telemetry .irrelevant { opacity: 0.34; }
  .telemetry [data-state='unsupported'] { visibility: hidden; }
</style>
