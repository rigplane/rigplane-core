<script lang="ts">
  import { onMount } from 'svelte';
  import { createSmoother } from '$lib/utils/smoothing.svelte';
  import { getCalibrationPoints, getS9Raw, rawToSUnit, rawToDbm } from '../../meters/smeter-scale';
  import { formatPowerWatts, formatSwr, formatAlc, formatCompDb } from '../meter-utils';

  type MeterSource = 'S' | 'PO' | 'SWR' | 'ALC' | 'COMP';

  interface Props {
    value: number;      // Raw meter 0-255
    txActive?: boolean;
    source?: MeterSource;
  }

  let { value, txActive = false, source = 'S' }: Props = $props();

  const MAX_RAW = 255;
  const SEGMENTS = 192;

  let s9Raw = $derived(getS9Raw());
  let s9Seg = $derived(Math.round((s9Raw / MAX_RAW) * SEGMENTS));

  // Build ticks from calibration
  let calPoints = $derived(getCalibrationPoints());

  let majorTicks = $derived(
    calPoints
      .filter(p => /^S[13579]$|^S9\+/.test(p.label))
      .map(p => ({ label: p.label.replace('S9+', '+').replace(/^S/, ''), raw: p.raw }))
  );

  let mediumTicks = $derived(
    calPoints
      .filter(p => /^S[2468]$/.test(p.label))
      .map(p => ({ raw: p.raw }))
  );

  // Minor ticks: subdivisions between cal points
  let minorTicks = $derived((() => {
    const ticks: number[] = [];
    for (let raw = 4; raw < MAX_RAW; raw += 4.5) {
      const r = Math.round(raw);
      const isMajor = majorTicks.some((t: any) => Math.abs(t.raw - r) < 3);
      const isMedium = mediumTicks.some((t: any) => Math.abs(t.raw - r) < 3);
      if (!isMajor && !isMedium) ticks.push(r);
    }
    return ticks;
  })());

  // Issue #938 — rAF-driven needle smoothing: asymmetric attack (50 ms) /
  // release (150 ms) so the bar punches in fast and glides down. Drives the
  // bar fill only; sReadout/ticks stay on the raw value. Seed the smoother
  // with the current computed segment count so the first synchronous render
  // matches the raw target (no flash to 0 on mount).
  function computeSegs(raw: number): number {
    return Math.min(SEGMENTS, Math.max(0, (raw / MAX_RAW) * SEGMENTS));
  }

  // svelte-ignore state_referenced_locally — intentional one-shot seed read
  const smoother = createSmoother(0.05, 0.15, computeSegs(value));
  $effect(() => {
    smoother.update(computeSegs(value));
  });
  onMount(() => {
    smoother.start();
    return () => smoother.stop();
  });

  let filledSegs = $derived(Math.round(smoother.value));

  // Sub-readouts use the calibrated piecewise converters from meter-utils
  // (shared with the desktop meters) instead of crude raw/255 maps, so the
  // LCD agrees with the rest of the UI (MOR-483 part 2).
  let sReadout = $derived.by(() => {
    if (source === 'S') return { label: rawToSUnit(value), sub: rawToDbm(value) + ' dBm' };
    if (source === 'PO') return { label: 'PO', sub: formatPowerWatts(value) };
    if (source === 'SWR') return { label: 'SWR', sub: formatSwr(value) };
    if (source === 'ALC') return { label: 'ALC', sub: formatAlc(value) };
    if (source === 'COMP') return { label: 'COMP', sub: formatCompDb(value) };
    return { label: rawToSUnit(value), sub: rawToDbm(value) + ' dBm' };
  });
</script>

<div class="lcd-smeter">
  <div class="meter-left">
    <!-- Bargraph -->
    <div class="meter-bar">
      {#each Array(SEGMENTS) as _, i}
        <div
          class="seg"
          class:filled={i < filledSegs}
          class:over-s9={i >= s9Seg}
          class:tx={txActive}
        ></div>
      {/each}
    </div>

    <!-- Scale below bar -->
    <div class="meter-scale">
      {#each minorTicks as raw}
        <div class="tick tick-minor" style="left: {(raw / MAX_RAW) * 100}%"></div>
      {/each}
      {#each mediumTicks as tick}
        <div class="tick tick-medium" style="left: {(tick.raw / MAX_RAW) * 100}%"></div>
      {/each}
      {#each majorTicks as tick}
        <div
          class="tick tick-major"
          class:over-s9={tick.raw > s9Raw}
          style="left: {(tick.raw / MAX_RAW) * 100}%"
        >
          <span class="tick-label">{tick.label}</span>
        </div>
      {/each}
      <span class="scale-s-label">S</span>
      <span class="scale-db-zone" style="left: {(s9Raw / MAX_RAW) * 100}%">dB</span>
    </div>
  </div>

  <!-- Readout -->
  <div class="meter-readout">
    <span class="readout-s">{sReadout.label}</span>
    <span class="readout-dbm">{sReadout.sub}</span>
  </div>
</div>

<style>
  .lcd-smeter {
    display: flex;
    align-items: stretch;
    gap: 12px;
    width: 100%;
  }

  .meter-left {
    flex: 1 1 0;
    max-width: 88%;
    min-width: 0;
    display: flex;
    flex-direction: column;
    gap: 2px;
  }

  /* ── Bargraph ── */
  .meter-bar {
    display: flex;
    gap: 0.5px;
    height: 16px;
  }

  .seg {
    flex: 1;
    background: rgba(0, 0, 0, 0.06);
    border-radius: 1px;
  }

  .seg.filled {
    background: rgba(26, 16, 0, 0.8);
  }

  .seg.filled.over-s9 {
    background: rgba(80, 10, 0, 0.9);
  }

  .seg.filled.tx {
    background: rgba(80, 10, 0, 0.85);
  }

  .seg.filled.tx.over-s9 {
    background: rgba(120, 0, 0, 0.95);
  }

  /* ── Scale ── */
  .meter-scale {
    position: relative;
    height: 36px;
    border-top: 4px solid rgba(26, 16, 0, 0.5);
    margin-top: 1px;
  }

  .tick {
    position: absolute;
    top: 0;
  }

  .tick-minor {
    width: 1px;
    height: 6px;
    background: rgba(26, 16, 0, 0.25);
  }

  .tick-medium {
    width: 1.5px;
    height: 10px;
    background: rgba(26, 16, 0, 0.4);
  }

  .tick-major {
    width: 2px;
    height: 13px;
    background: rgba(26, 16, 0, 0.6);
  }

  .tick-major.over-s9 {
    background: rgba(80, 10, 0, 0.6);
  }

  .tick-label {
    position: absolute;
    top: 15px;
    left: 50%;
    transform: translateX(-50%);
    font-family: 'JetBrains Mono', 'Courier New', monospace;
    font-size: 12px;
    font-weight: 700;
    color: rgba(26, 16, 0, 0.6);
    white-space: nowrap;
  }

  .tick-major.over-s9 .tick-label {
    color: rgba(80, 10, 0, 0.65);
  }

  .scale-s-label {
    position: absolute;
    top: 18px;
    left: 0;
    font-family: 'JetBrains Mono', 'Courier New', monospace;
    font-size: 13px;
    font-weight: 700;
    color: rgba(26, 16, 0, 0.45);
  }

  .scale-db-zone {
    position: absolute;
    top: 6px;
    transform: translateX(6px);
    font-family: 'JetBrains Mono', 'Courier New', monospace;
    font-size: 10px;
    font-weight: 700;
    color: rgba(80, 10, 0, 0.4);
  }

  /* ── Readout (right, large) ── */
  .meter-readout {
    flex-shrink: 0;
    display: flex;
    flex-direction: column;
    align-items: flex-end;
    justify-content: center;
    min-width: 80px;
    padding-left: 4px;
  }

  .readout-s {
    font-family: 'DSEG7 Classic', monospace;
    font-weight: bold;
    font-size: 28px;
    color: #1A1000;
    line-height: 1;
  }

  .readout-dbm {
    font-family: 'JetBrains Mono', 'Courier New', monospace;
    font-size: 11px;
    color: rgba(26, 16, 0, 0.45);
    line-height: 1.3;
  }
</style>
