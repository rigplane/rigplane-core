<!--
  AmberTelemetryStrip — 2 compact tiles (VD · ID) with inline
  sparklines showing the last ~30 samples. Mounted in the LCD aux
  grid-area (#894 reserved the slot; #887 twin-skin lays the cockpit).

  Data source: `ServerState.vdMeter` / `idMeter`. Missing fields
  produce a "—" placeholder tile but keep the strip visible so the
  grid row doesn't collapse under the user.

  The IC-7610 exposes NO temperature over CI-V (no 0x15 temp sub, no
  MetersCapable.get_temp, no RadioState/ServerState temp field), so the
  previously-blank TEMP tile was dropped (MOR-483).

  Numeric labels use the calibrated converters from `meter-utils`
  (piecewise IC-7610 knots) — the same math the desktop meters use —
  rather than a crude raw/255 linear map (MOR-483 part 2).

  Sample history is kept per-tile in a local ring buffer (no store).
  `$effect` watches the live values and pushes new samples when they
  change materially (avoids building buffers of identical readings).

  Part of #837 / epic #818 LCD telemetry strip.
-->
<script lang="ts">
  import { deriveAmberTelemetryProps } from '$lib/runtime/adapters/panel-adapters';
  import { formatVolts, formatAmps } from '../meter-utils';
  import AmberSparkline from './AmberSparkline.svelte';

  const BUFFER_SIZE = 30;
  // Minimum time between samples written to the ring buffer (codex P2 on
  // PR #929). The previous epsilon-delta gate silently dropped stable
  // values — rigs whose vdMeter rarely moves got blank sparklines. A time
  // gate keeps lines drawing even for flat inputs while still decimating
  // the incoming stream so every frame doesn't push through.
  const PUSH_MIN_INTERVAL_MS = 1000;

  let p = $derived(deriveAmberTelemetryProps());

  // Raw readings (nullable — backend may not provide both).
  let vdRaw = $derived<number | null>(p.vdRaw);
  let idRaw = $derived<number | null>(p.idRaw);

  // Local ring buffers — $state so Svelte tracks them as arrays.
  let vdHistory = $state<number[]>([]);
  let idHistory = $state<number[]>([]);

  function pushBuffer(buf: number[], value: number): number[] {
    const next = buf.length >= BUFFER_SIZE ? buf.slice(1) : [...buf];
    next.push(value);
    return next;
  }

  // Interval-driven sampling instead of reactive-on-change (codex P2 on
  // PR #929): if a meter value is stable (common for vdMeter), an effect
  // keyed on `$derived` only fires once, leaving the sparkline blank.
  // Reading the current raw values every PUSH_MIN_INTERVAL_MS guarantees
  // a line is drawn even when input is flat.
  $effect(() => {
    const interval = setInterval(() => {
      if (vdRaw !== null) vdHistory = pushBuffer(vdHistory, vdRaw);
      if (idRaw !== null) idHistory = pushBuffer(idHistory, idRaw);
    }, PUSH_MIN_INTERVAL_MS);
    return () => clearInterval(interval);
  });

  // Display conversions — rigs report raw 0..255; the calibrated piecewise
  // converters (shared with the desktop meters) turn that into engineering
  // units so the LCD strip agrees with the rest of the UI (MOR-483 part 2).
  function vdLabel(raw: number | null): string {
    return raw === null ? '—' : formatVolts(raw);
  }
  function idLabel(raw: number | null): string {
    return raw === null ? '—' : formatAmps(raw);
  }
</script>

<div class="amber-telemetry-strip">
  <div class="tile" class:tile-empty={vdRaw === null}>
    <div class="tile-head">
      <span class="tile-tag">VD</span>
      <span class="tile-value">{vdLabel(vdRaw)}</span>
    </div>
    <div class="tile-spark">
      <AmberSparkline data={vdHistory} min={0} max={255} />
    </div>
  </div>

  <div class="tile" class:tile-empty={idRaw === null}>
    <div class="tile-head">
      <span class="tile-tag">ID</span>
      <span class="tile-value">{idLabel(idRaw)}</span>
    </div>
    <div class="tile-spark">
      <AmberSparkline data={idHistory} min={0} max={255} />
    </div>
  </div>
</div>

<style>
  .amber-telemetry-strip {
    display: grid;
    grid-template-columns: repeat(2, minmax(0, 1fr));
    gap: 6px;
    width: 100%;
    height: 100%;
    min-height: 22px;
    /* Uses the ambient warm-amber ink via --lcd-alpha-active from .lcd-screen. */
    color: rgba(26, 16, 0, var(--lcd-alpha-active));
  }

  .tile {
    display: flex;
    align-items: center;
    gap: 4px;
    padding: 0 4px;
    min-width: 0;
    border: 1px solid rgba(26, 16, 0, calc(var(--lcd-alpha-active) * 0.2));
    border-radius: 3px;
    overflow: hidden;
  }

  .tile-empty {
    opacity: 0.45;
  }

  .tile-head {
    display: flex;
    align-items: baseline;
    gap: 3px;
    flex-shrink: 0;
    font-family: 'JetBrains Mono', 'Courier New', monospace;
  }

  .tile-tag {
    font-size: 9px;
    font-weight: 700;
    letter-spacing: 0.08em;
    color: rgba(26, 16, 0, calc(var(--lcd-alpha-active) * 0.6));
  }

  .tile-value {
    font-size: 11px;
    font-weight: 700;
    letter-spacing: 0.02em;
  }

  .tile-spark {
    flex: 1;
    min-width: 0;
    height: 14px;
  }
</style>
