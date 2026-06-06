<script lang="ts">
  import { onMount, untrack } from 'svelte';
  import { createSmoother } from '$lib/utils/smoothing.svelte';
  import {
    alcLevel,
    compLevel,
    formatAlc,
    formatAmps,
    formatCompDb,
    formatPowerWatts,
    formatSMeter,
    formatSwr,
    formatVolts,
    idLevel,
    isAlcFault,
    isSwrFault,
    normalizePower,
    peakHoldDisplay,
    sLevel,
    swrLevel,
    updatePeakHold,
    vdLevel,
    type PeakHoldState,
  } from './meter-utils';

  /**
   * Unified TX / station-health meters dashboard for the desktop-v2 bottom
   * dock. Renders an auto-fit grid of tiles for each meter whose raw value
   * is defined on the runtime state (capability gating by `!== undefined`).
   *
   * Scope: issue #820 shipped Po / SWR / ALC / S tiles. Issue #822 adds
   * Id / Vd / COMP (COMP additionally gated on `compressorOn === true`).
   * Peak-hold and fault highlighting land with #823.
   *
   * The panel is pure presentation — no store / transport imports.
   */
  interface Props {
    sValue?: number;
    powerMeter?: number;
    swrMeter?: number;
    alcMeter?: number;
    idMeter?: number;
    vdMeter?: number;
    compMeter?: number;
    compressorOn?: boolean;
    txActive: boolean;
  }

  let {
    sValue,
    powerMeter,
    swrMeter,
    alcMeter,
    idMeter,
    vdMeter,
    compMeter,
    compressorOn,
    txActive,
  }: Props = $props();

  type PeakKey = 'po' | 'swr' | 'alc';

  interface Tile {
    key: 'po' | 'swr' | 'alc' | 's' | 'id' | 'vd' | 'comp';
    label: string;
    display: string;
    fillPct: number;
    fill: string;
    track: string;
    relevant: boolean;
    fault?: boolean;
  }

  // Peak-hold state for Po/SWR/ALC. Stores the latched peak + timestamp only;
  // the displayed decay is computed per-render from `now` so it stays linear
  // across the 2 s window instead of compounding tick-by-tick.
  let peaks = $state<Partial<Record<PeakKey, PeakHoldState>>>({});
  let now = $state(Date.now());

  function steppeak(key: PeakKey, current: number | undefined, t: number) {
    if (current === undefined) {
      if (peaks[key] !== undefined) peaks[key] = undefined;
      return;
    }
    const next = updatePeakHold(peaks[key], current, t);
    // Only write back on a re-latch / anchor reset — otherwise skip to avoid
    // flagging a reactive read-then-write cycle. The display recomputes from
    // `now` regardless.
    if (peaks[key] !== next) peaks[key] = next;
  }

  function stepAllPeaks() {
    const t = Date.now();
    now = t;
    steppeak('po', powerMeter !== undefined ? normalizePower(powerMeter) * 100 : undefined, t);
    steppeak('swr', swrMeter !== undefined ? swrLevel(swrMeter) * 100 : undefined, t);
    steppeak('alc', alcMeter !== undefined ? alcLevel(alcMeter) * 100 : undefined, t);
  }

  // A 100ms interval drives both the decay and the latch from fresh
  // prop samples. Driving everything off a timer (not a reactive $effect)
  // avoids the read-then-write cycle on `peaks` that Svelte 5 flags.
  $effect(() => {
    untrack(() => stepAllPeaks());
    const id = setInterval(() => untrack(() => stepAllPeaks()), 100);
    return () => clearInterval(id);
  });

  function resetPeak(key: PeakKey) {
    peaks[key] = undefined;
  }

  // Priority order (plan §3): Po → SWR → ALC → S. Tiles with undefined
  // source values are omitted entirely so the grid re-flows.
  let tiles = $derived.by<Tile[]>(() => {
    const out: Tile[] = [];
    // TX-only meters (Po/SWR/ALC/Id/COMP) are gated on `txActive`: on RX they
    // are NOT rendered (hidden), rather than dimmed, so a stale last-TX reading
    // can never linger as garbage (MOR-483 part-1). The S tile (RX indicator)
    // and Vd tile (continuous supply rail) stay rendered in both states.
    if (powerMeter !== undefined && txActive) {
      out.push({
        key: 'po',
        label: 'Po',
        display: formatPowerWatts(powerMeter),
        fillPct: normalizePower(powerMeter) * 100,
        fill: 'var(--v2-meter-power-fill)',
        track: 'var(--v2-meter-power-track)',
        relevant: txActive,
      });
    }
    if (swrMeter !== undefined && txActive) {
      out.push({
        key: 'swr',
        label: 'SWR',
        display: formatSwr(swrMeter),
        fillPct: swrLevel(swrMeter) * 100,
        fill: 'var(--v2-meter-swr-fill)',
        track: 'var(--v2-meter-swr-track)',
        relevant: txActive,
        fault: txActive && isSwrFault(swrMeter),
      });
    }
    if (alcMeter !== undefined && txActive) {
      out.push({
        key: 'alc',
        label: 'ALC',
        display: formatAlc(alcMeter),
        fillPct: alcLevel(alcMeter) * 100,
        fill: 'var(--v2-meter-alc-fill)',
        track: 'var(--v2-meter-alc-track)',
        relevant: txActive,
        fault: txActive && isAlcFault(alcMeter),
      });
    }
    if (idMeter !== undefined && txActive) {
      out.push({
        key: 'id',
        label: 'Id',
        display: formatAmps(idMeter),
        fillPct: idLevel(idMeter) * 100,
        fill: 'var(--v2-meter-id-fill)',
        track: 'var(--v2-meter-id-track)',
        relevant: txActive,
      });
    }
    if (vdMeter !== undefined) {
      out.push({
        key: 'vd',
        label: 'Vd',
        display: formatVolts(vdMeter),
        fillPct: vdLevel(vdMeter) * 100,
        fill: 'var(--v2-meter-vd-fill)',
        track: 'var(--v2-meter-vd-track)',
        // Vd (drain voltage) is a continuous supply/PSU health metric,
        // readable in both RX and TX — unlike TX-only Po/SWR/ALC/Id/COMP.
        relevant: true,
      });
    }
    if (compMeter !== undefined && compressorOn === true && txActive) {
      out.push({
        key: 'comp',
        label: 'COMP',
        display: formatCompDb(compMeter),
        fillPct: compLevel(compMeter) * 100,
        fill: 'var(--v2-meter-comp-fill)',
        track: 'var(--v2-meter-comp-track)',
        relevant: txActive,
      });
    }
    if (sValue !== undefined) {
      out.push({
        key: 's',
        label: 'S',
        display: formatSMeter(sValue),
        fillPct: sLevel(sValue) * 100,
        fill: 'var(--v2-meter-s-fill)',
        track: 'var(--v2-meter-s-track)',
        relevant: !txActive,
      });
    }
    return out;
  });

  // Issue #938 — per-tile rAF-driven bar smoothing. Smoothers are keyed by
  // tile.key and reused across renders so the bar carries fractional state
  // between updates. Peak-hold (`peakPct`) and digit text (`tile.display`)
  // continue to read raw props; only the bar-fill width is smoothed. Each
  // smoother is seeded with the tile's current fillPct on first creation so
  // the initial synchronous render matches the raw target (no flash from 0).
  type Smoother = ReturnType<typeof createSmoother>;
  const smoothers = new Map<Tile['key'], Smoother>();

  function getSmoother(key: Tile['key'], initial: number): Smoother {
    let s = smoothers.get(key);
    if (!s) {
      s = createSmoother(0.05, 0.15, initial);
      s.start();
      smoothers.set(key, s);
    }
    return s;
  }

  $effect(() => {
    const activeKeys = new Set<Tile['key']>();
    for (const tile of tiles) {
      activeKeys.add(tile.key);
      getSmoother(tile.key, tile.fillPct).update(tile.fillPct);
    }
    // Prune smoothers for tiles that disappeared (e.g. COMP when
    // compressorOn toggles off). Without this, a re-entered tile would
    // briefly render its stale last value before the rAF loop converged.
    for (const [key, s] of smoothers) {
      if (!activeKeys.has(key)) {
        s.stop();
        smoothers.delete(key);
      }
    }
  });

  onMount(() => {
    return () => {
      for (const s of smoothers.values()) s.stop();
      smoothers.clear();
    };
  });
</script>

<article class="meters-dock-panel" data-testid="meters-dock-panel">
  <header class="dock-header">
    <span class="dock-title">STATION METERS</span>
    <span class="dock-tx-state" data-active={txActive}>{txActive ? 'TX' : 'RX'}</span>
  </header>

  <div class="dock-grid">
    {#each tiles as tile (tile.key)}
      {@const peakPct =
        tile.key === 'po' || tile.key === 'swr' || tile.key === 'alc'
          ? peaks[tile.key] !== undefined
            ? peakHoldDisplay(peaks[tile.key], tile.fillPct, now)
            : undefined
          : undefined}
      {@const displayPct = smoothers.get(tile.key)?.value ?? tile.fillPct}
      <div
        class="dock-tile"
        role="group"
        aria-label={`${tile.label} meter`}
        data-meter={tile.key}
        data-relevant={tile.relevant}
        data-fault={tile.fault ? 'true' : 'false'}
        ondblclick={() => {
          if (tile.key === 'po' || tile.key === 'swr' || tile.key === 'alc') {
            resetPeak(tile.key);
          }
        }}
      >
        <div class="tile-header">
          <span class="tile-label">{tile.label}</span>
        </div>
        <div class="tile-value">{tile.display}</div>
        <div class="tile-bar" style:background={tile.track}>
          <div
            class="tile-bar-fill"
            style:width={`${Math.max(0, Math.min(100, displayPct))}%`}
            style:background={tile.fill}
          ></div>
          {#if peakPct !== undefined && tile.relevant}
            <div
              class="tile-bar-peak"
              data-testid="peak-marker"
              style:left={`${Math.max(0, Math.min(100, peakPct))}%`}
            ></div>
          {/if}
        </div>
      </div>
    {/each}
  </div>
</article>

<style>
  .meters-dock-panel {
    display: flex;
    flex-direction: column;
    gap: 8px;
    padding: 10px 12px;
    border: 1px solid var(--v2-border-darker);
    border-radius: 4px;
    background: linear-gradient(180deg, var(--v2-bg-gradient-start) 0%, var(--v2-bg-darkest) 100%);
    box-sizing: border-box;
    width: 100%;
  }

  .dock-header {
    display: flex;
    align-items: center;
    justify-content: space-between;
    font-family: 'Roboto Mono', monospace;
  }

  .dock-title {
    color: var(--v2-text-light);
    font-size: 11px;
    font-weight: 700;
    letter-spacing: 0.18em;
  }

  .dock-tx-state {
    color: var(--v2-text-muted);
    font-size: 11px;
    font-weight: 700;
    letter-spacing: 0.12em;
  }

  .dock-tx-state[data-active='true'] {
    color: var(--v2-accent-red, #ff4040);
  }

  .dock-grid {
    display: grid;
    grid-template-columns: repeat(auto-fit, minmax(132px, 1fr));
    gap: 8px;
  }

  .dock-tile {
    display: flex;
    flex-direction: column;
    gap: 4px;
    padding: 10px 12px;
    border: 1px solid var(--v2-border);
    border-radius: 4px;
    background: var(--v2-bg-card, transparent);
    font-family: 'Roboto Mono', monospace;
    transition: opacity 200ms ease, border-color 150ms ease;
  }

  .dock-tile[data-relevant='false'] {
    opacity: 0.35;
  }

  .dock-tile[data-fault='true'] {
    border-color: var(--v2-accent-red);
    box-shadow: 0 0 6px rgba(255, 32, 32, 0.45);
  }

  .dock-tile[data-fault='true'] .tile-value {
    color: var(--v2-accent-red-alt);
  }

  .tile-header {
    display: flex;
    align-items: center;
    justify-content: space-between;
  }

  .tile-label {
    color: var(--v2-text-light);
    font-size: 11px;
    font-weight: 700;
    letter-spacing: 0.12em;
  }

  .tile-value {
    color: var(--v2-text-white);
    font-size: 22px;
    font-weight: 700;
    line-height: 1.1;
  }

  .tile-bar {
    position: relative;
    height: 6px;
    border-radius: 1px;
    overflow: hidden;
  }

  .tile-bar-fill {
    height: 100%;
  }

  .tile-bar-peak {
    position: absolute;
    top: 0;
    width: 2px;
    height: 100%;
    background: var(--v2-accent-yellow, #f2cf4a);
    transform: translateX(-1px);
    pointer-events: none;
  }
</style>
