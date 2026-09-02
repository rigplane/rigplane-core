<script lang="ts">
  import { onMount, untrack } from 'svelte';
  import { createSmoother, prefersReducedMotion, onReducedMotionChange } from '$lib/utils/smoothing.svelte';
  import type { MeterDisplay } from '../../presentation/languages/contract';
  import { DEFAULT_METER_DISPLAY } from './meter-display';
  import {
    calibratedToSegments,
    calibratedToSUnit,
    calibratedToDbm,
    formatDbm,
    getScaleMarks,
    getS9Raw,
    rawToSegments,
  } from './smeter-scale';

  interface Props {
    value: number;    // calibrated dB relative to S9 from backend state
    compact?: boolean;
    label?: string;
    variant?: string;
    display?: MeterDisplay;
  }

  let { value, compact = false, label, variant, display = DEFAULT_METER_DISPLAY }: Props = $props();

  const isVfoVariant = $derived(variant === 'vfo' || variant === 'vfo-wide');
  const isWideVfoVariant = $derived(variant === 'vfo-wide');

  // ── Segment geometry ────────────────────────────────────────────────────────
  // `smeter-scale.ts`'s rawToSegments/calibratedToSegments always report a
  // position on a fixed 0-20 domain (that file's `rawToSegments` tops out at
  // 20 for any input, regardless of caller) — independent of how many visual
  // segments this component draws. RAW_SEGMENT_DOMAIN names that fixed width
  // so a raw-domain reading can be rescaled onto the `display.segmentCount`
  // visual domain below.
  const RAW_SEGMENT_DOMAIN = 20;
  const SEG_COUNT = $derived(display.segmentCount);
  const SEG_GAP = $derived(display.segmentGapPx);
  const BAR_X = $derived(compact && isVfoVariant ? (isWideVfoVariant ? 14 : 12) : 8);
  const BAR_WIDTH = $derived(compact && isVfoVariant ? (isWideVfoVariant ? 498 : 492) : 484);
  const SEG_W = $derived((BAR_WIDTH - (SEG_COUNT - 1) * SEG_GAP) / SEG_COUNT);

  const READOUT_CX = $derived(BAR_X + BAR_WIDTH + (compact && isVfoVariant ? 36 : 54));

  function segX(i: number): number {
    return BAR_X + i * (SEG_W + SEG_GAP);
  }

  // x position (from bar left) for a given raw value — rawToSegments(raw) is
  // on the fixed RAW_SEGMENT_DOMAIN, rescaled here onto SEG_COUNT segments.
  function rawToX(raw: number): number {
    return BAR_X + (rawToSegments(raw) / RAW_SEGMENT_DOMAIN) * SEG_COUNT * (SEG_W + SEG_GAP);
  }

  // Index of the first visual segment at or above the calibrated S9 anchor.
  // `rawToSegments(getS9Raw())` is exactly 11 on the raw 0-20 domain — S9 is
  // the last S-unit knot, so `rawToSegments` (via `rawToSFloat`) resolves it
  // to exactly (9/9)*11 — rescaled here by SEG_COUNT so this index tracks a
  // non-20 segment count instead of the fixed literal 11 the
  // pre-display-prop code used.
  const s9SegmentIndex = $derived(
    Math.round((rawToSegments(getS9Raw()) / RAW_SEGMENT_DOMAIN) * SEG_COUNT),
  );

  // ── Colors ──────────────────────────────────────────────────────────────────
  const ACTIVE_COLORS: ReadonlyArray<string> = [
    '#0D633B', '#0F7445', '#118550', '#12935A', 'var(--v2-accent-green-dark)',
    '#16BA70', 'var(--v2-accent-green-medium)', '#1BE184', '#1EF18C', '#30F7A1',
    'var(--v2-accent-cyan-bright)',
    '#B8A430', '#C49A28', '#D08E20', '#DC7E18',
    '#E57010', '#EB6210', '#F05418', '#F44820', '#F83C28',
  ];

  // MOR-2250: a language-driven flat two-tone fill (IC-7300 look — one color
  // below the S9 crossover, one at/above it) replaces the 20-step gradient
  // below, but ONLY when the active design language actually supplied both
  // tones. `DEFAULT_METER_DISPLAY` (no language, or a renderer whose
  // descriptor didn't satisfy the widened `display` structural check) sets
  // `toneBelowS9`/`toneAboveS9` to `''` for exactly this reason: an empty
  // string is never a language's real tone, so `hasTone` is false and this
  // falls straight through to the untouched gradient below — the pre-MOR-2250
  // no-language render is byte-identical.
  const hasTone = $derived(display.toneBelowS9 !== '' && display.toneAboveS9 !== '');

  // Samples the 20-entry ramp above by fraction of SEG_COUNT, so a non-20
  // segment count still walks the same color progression start-to-end.
  // MOR-2214: at SEG_COUNT === 1 there is no second segment to interpolate
  // `i / (SEG_COUNT - 1)` against (division by zero). `smoother.value` is
  // already on the SEG_COUNT-wide domain (see its `.update()` call below),
  // so `smoother.value / SEG_COUNT` is the reading's own fill fraction —
  // the single segment samples the ramp by THAT instead of by index, so it
  // still reports strong/over-range readings in the ramp's hot colors
  // rather than collapsing every reading to one fixed color.
  function activeColor(i: number): string {
    if (hasTone) {
      return i < s9SegmentIndex ? display.toneBelowS9 : display.toneAboveS9;
    }
    const denom = SEG_COUNT - 1;
    const fraction = denom === 0 ? Math.min(1, Math.max(0, smoother.value / SEG_COUNT)) : i / denom;
    return ACTIVE_COLORS[Math.round(fraction * (ACTIVE_COLORS.length - 1))];
  }

  // The unlit (dim) segment color is NOT wired to a language: no field on
  // `DesignLanguageTokens` reaches this component for it. `rx.idle`/`tx.idle`
  // exist on the token set, but the only channel from tokens to
  // `LinearSMeter` is the flat `MeterDisplay` object `renderSlot` builds, and
  // that object structurally carries only `segmentCount`/`segmentGapPx`/
  // `toneBelowS9`/`toneAboveS9` — adding a fifth field to carry an idle tone
  // was out of scope for MOR-2250 (rule of three: no machinery for a need
  // this PR doesn't already have a caller for). So this stays the same two
  // hex literals it was before, language active or not.
  function dimColor(i: number): string {
    return i < s9SegmentIndex ? '#0A2415' : '#1A1008';
  }

  // ── Label marks ─────────────────────────────────────────────────────────────
  let labelMarks = $derived(getScaleMarks());

  // ── Tick marks ──────────────────────────────────────────────────────────────
  // Generate dense ticks: 9 subdivisions between each labeled S-unit position,
  // with the 5th tick (midpoint) slightly taller.
  type TickKind = 'major' | 'mid' | 'minor';
  interface Tick { raw: number; kind: TickKind; color: string }

  function generateTicks(): Tick[] {
    const ticks: Tick[] = [];
    const anchors = getScaleMarks().map((m) => ({ raw: m.raw, actual: m.actual }));
    const first = anchors[0];

    if (!first || first.raw > 0) {
      anchors.unshift({ raw: 0, actual: -54 });
    }

    function colorForActual(actual: number): string {
      if (actual <= 0) return 'var(--v2-text-bright)';
      if (actual <= 20) return 'var(--v2-accent-yellow)';
      if (actual <= 40) return 'var(--v2-accent-orange-alt)';
      return 'var(--v2-accent-red-alt)';
    }

    function addSubdivisions(startRaw: number, endRaw: number, startActual: number, endActual: number) {
      // Major tick at start
      ticks.push({ raw: startRaw, kind: 'major', color: colorForActual(startActual) });
      // 9 subdivision ticks between start and end
      const step = (endRaw - startRaw) / 10;
      const actualStep = (endActual - startActual) / 10;
      for (let j = 1; j <= 9; j++) {
        const raw = startRaw + step * j;
        const kind: TickKind = j === 5 ? 'mid' : 'minor';
        ticks.push({ raw, kind, color: colorForActual(startActual + actualStep * j) });
      }
    }

    for (let i = 0; i < anchors.length - 1; i++) {
      addSubdivisions(
        anchors[i].raw,
        anchors[i + 1].raw,
        anchors[i].actual,
        anchors[i + 1].actual,
      );
    }
    // Final tick at max
    const last = anchors[anchors.length - 1];
    ticks.push({ raw: last.raw, kind: 'major', color: colorForActual(last.actual) });

    return ticks;
  }

  let tickMarks = $derived(generateTicks());

  // ── Layout (switches between full / compact) ────────────────────────────────
  //   When label is present: label at top → meter shifted down
  //   Vertical stacking: [label] → scale labels → ticks → bar
  const LABEL_OFFSET  = $derived(label ? (compact ? (isVfoVariant ? 8 : 10) : 14) : 0);
  const TAG_Y         = $derived(compact ? (isVfoVariant ? 1 : 2) : 3);   // label "MAIN"/"SUB" Y
  const TAG_FS        = $derived(compact ? 7  : 8);
  const SCALE_LABEL_Y = $derived((compact ? (isVfoVariant ? 1 : 2) : 3) + LABEL_OFFSET);
  const SCALE_LABEL_FS = $derived(compact ? (isVfoVariant ? 9 : 8) : 9);
  const TICK_MAJOR_Y1 = $derived((compact ? (isVfoVariant ? 12 : 14) : 18) + LABEL_OFFSET);
  const TICK_MAJOR_Y2 = $derived((compact ? (isVfoVariant ? 27 : 26) : 38) + LABEL_OFFSET);
  const TICK_MID_Y1   = $derived((compact ? (isVfoVariant ? 16 : 17) : 22) + LABEL_OFFSET);
  const TICK_MID_Y2   = $derived((compact ? (isVfoVariant ? 27 : 26) : 38) + LABEL_OFFSET);
  const TICK_MINOR_Y1 = $derived((compact ? (isVfoVariant ? 20 : 20) : 28) + LABEL_OFFSET);
  const TICK_MINOR_Y2 = $derived((compact ? (isVfoVariant ? 27 : 26) : 38) + LABEL_OFFSET);
  const TRACK_Y       = $derived((compact ? (isVfoVariant ? 29 : 28) : 40) + LABEL_OFFSET);
  const TRACK_H       = $derived(compact ? (isVfoVariant ? (isWideVfoVariant ? 11 : 10) : 8) : 14);
  // Readout aligned to bar: S-unit centered on bar, dBm just below
  const S_UNIT_Y      = $derived(TRACK_Y - (compact ? (isVfoVariant ? 2 : 1) : 2));
  const S_UNIT_FS     = $derived(compact ? (isVfoVariant ? (isWideVfoVariant ? 15 : 14) : 12) : 15);
  const DBM_Y         = $derived(TRACK_Y + TRACK_H + (compact ? (isVfoVariant ? 0 : 1) : 2));
  const DBM_FS        = $derived(compact ? (isVfoVariant ? 9 : 8) : 9);
  // Bottom padding symmetric to top
  const TOTAL_HEIGHT  = $derived(TRACK_Y + TRACK_H + SCALE_LABEL_Y);

  // ── Smoother ────────────────────────────────────────────────────────────────
  // MOR-481: keep the fast attack (0.06) but shorten the release τ to ~100 ms
  // so the bar fill tracks the (raw) numeric readout instead of lagging it on
  // downward steps. The previous 0.25 (~250 ms) release was visibly behind the
  // number; AmberSmeter already uses a comparably snappy 0.15 release.
  const smoother = createSmoother(0.06, 0.1);

  $effect(() => {
    smoother.update((calibratedToSegments(value) / RAW_SEGMENT_DOMAIN) * SEG_COUNT);
  });

  onMount(() => {
    smoother.start();
    return () => smoother.stop();
  });

  // ── Peak hold ───────────────────────────────────────────────────────────────
  const PEAK_HOLD_MS = 1000;   // hold at peak for 1 second
  // Fraction of full scale to drop per frame once the hold window expires
  // (~30% faster), scaled by SEG_COUNT below — peakSegs lives on the
  // SEG_COUNT-wide visual domain (fed by the rescaled smoother.update()
  // above), not the fixed RAW_SEGMENT_DOMAIN.
  const PEAK_DECAY_FRACTION = 0.0195 / RAW_SEGMENT_DOMAIN;

  let peakSegs   = $state(0);  // peak position in segments (0-SEG_COUNT)
  let peakTime   = $state(0);  // timestamp when peak was set
  let peakFrameId = 0;

  $effect(() => {
    const current = smoother.value;

    if (prefersReducedMotion()) {
      // MOR-1252: static hold under reduced motion — the peak marker
      // latches at the highest observed value and stays put (no glide)
      // until either a higher value arrives or the hold window elapses, at
      // which point it resets INSTANTLY to the current value (a single
      // jump computed here, not a decay). This effect only re-runs when
      // `smoother.value` actually changes — MOR-1233 already makes that a
      // direct snap-to-target under reduce, so no rAF loop or interval is
      // scheduled to drive this hold/reset.
      //
      // J2: the condition/write below both reads and writes peakSegs and
      // peakTime, so it must run inside untrack() — otherwise the effect
      // depends on its own writes and self-invalidates (it still converges
      // today because the re-seat is idempotent and `||` short-circuits,
      // but that's incidental, not guaranteed; matches the untrack()
      // pattern MetersDockPanel's own latch-freshness effect already uses).
      untrack(() => {
        if (current >= peakSegs || performance.now() - peakTime > PEAK_HOLD_MS) {
          peakSegs = current;
          peakTime = performance.now();
        }
      });
      return;
    }

    if (current >= peakSegs) {
      // New peak — capture it (the decay-toward-current glide for an
      // expired hold is handled by the rAF loop below).
      peakSegs = current;
      peakTime = performance.now();
    }
  });

  onMount(() => {
    const tickPeak = (now: number) => {
      const current = smoother.value;
      const elapsed = now - peakTime;

      if (current >= peakSegs) {
        // Signal is at or above peak — update peak
        peakSegs = current;
        peakTime = now;
      } else if (elapsed > PEAK_HOLD_MS) {
        // Hold expired — decay toward current level
        peakSegs = Math.max(current, peakSegs - PEAK_DECAY_FRACTION * SEG_COUNT * 16.67); // ~1 seg/sec at 60fps
      }
      // else: holding — do nothing

      peakFrameId = requestAnimationFrame(tickPeak);
    };

    // MOR-1233: the hold/decay ballistics above are exactly the animation
    // prefers-reduced-motion asks us to skip — don't schedule the loop while
    // the preference is active. Fix cycle 1: react to it changing mid-
    // session too (start/stop alone only decide once, at mount).
    if (!prefersReducedMotion()) peakFrameId = requestAnimationFrame(tickPeak);

    const unsubscribe = onReducedMotionChange((reduced) => {
      if (reduced) {
        if (peakFrameId) {
          cancelAnimationFrame(peakFrameId);
          peakFrameId = 0;
        }
      } else if (!peakFrameId) {
        peakFrameId = requestAnimationFrame(tickPeak);
      }
    });

    return () => {
      if (peakFrameId) cancelAnimationFrame(peakFrameId);
      unsubscribe();
    };
  });

  // Peak X position for the vertical indicator line
  let peakX = $derived(BAR_X + peakSegs * (SEG_W + SEG_GAP));
  // Only show peak line if it's meaningfully ahead of current bar
  let showPeak = $derived(peakSegs - smoother.value > 0.3);

  // Peak-line color zones as fractions of the raw 20-segment domain — 15/20
  // and 18/20 are visual gradient stops with no calibration anchor (unlike
  // s9SegmentIndex above), rescaled the same way so they track SEG_COUNT.
  const peakZoneYellow = $derived(Math.round((15 / RAW_SEGMENT_DOMAIN) * SEG_COUNT));
  const peakZoneOrange = $derived(Math.round((18 / RAW_SEGMENT_DOMAIN) * SEG_COUNT));

  // Color of peak line based on zone
  let peakColor = $derived(peakSegs <= s9SegmentIndex ? 'var(--v2-accent-cyan-bright)' : peakSegs <= peakZoneYellow ? 'var(--v2-accent-yellow)' : peakSegs <= peakZoneOrange ? 'var(--v2-accent-orange-alt)' : 'var(--v2-accent-red-alt)');

  // ── Reactive display values ─────────────────────────────────────────────────
  let fullSegs = $derived(Math.floor(smoother.value));
  let fracSeg  = $derived(smoother.value - Math.floor(smoother.value));

  let displaySUnit = $derived(calibratedToSUnit(value));
  let displayDbm   = $derived(formatDbm(calibratedToDbm(value)));
</script>

<svg
  viewBox="0 0 600 {TOTAL_HEIGHT}"
  width="100%"
  height="auto"
  preserveAspectRatio="xMidYMid meet"
  data-variant={variant}
>
  <!-- Container background -->
  <rect
    x="0" y="0" width="600" height={TOTAL_HEIGHT}
    rx="8"
    fill="var(--v2-bg-darkest)"
    stroke="var(--v2-bg-panel)"
    stroke-width="1"
  />

  <!-- Optional label (horizontal, top-left) -->
  {#if label}
    <text
      x="10" y={TAG_Y}
      font-family="'Roboto Mono', monospace"
      font-size={TAG_FS}
      font-weight="700"
      letter-spacing="1.2"
      fill="var(--v2-text-dim)"
      dominant-baseline="text-before-edge"
    >{label}</text>
  {/if}

  <!-- Scale labels -->
  {#each labelMarks as m}
    <text
      x={rawToX(m.raw)}
      y={SCALE_LABEL_Y}
      font-family="'Roboto Mono', monospace"
      font-size={SCALE_LABEL_FS}
      font-weight="700"
      fill={m.color}
      text-anchor="middle"
      dominant-baseline="text-before-edge"
    >{m.text}</text>
  {/each}

  <!-- Tick marks -->
  {#each tickMarks as t}
    {@const tx = rawToX(t.raw)}
    {@const y1 = t.kind === 'major' ? TICK_MAJOR_Y1 : t.kind === 'mid' ? TICK_MID_Y1 : TICK_MINOR_Y1}
    {@const y2 = t.kind === 'major' ? TICK_MAJOR_Y2 : t.kind === 'mid' ? TICK_MID_Y2 : TICK_MINOR_Y2}
    {@const sw = t.kind === 'major' ? 1.2 : t.kind === 'mid' ? 0.9 : 0.6}
    {@const op = t.kind === 'major' ? 0.9 : t.kind === 'mid' ? 0.6 : 0.35}
    <line
      x1={tx} y1={y1}
      x2={tx} y2={y2}
      stroke={t.color}
      stroke-width={sw}
      opacity={op}
    />
  {/each}

  <!-- Bar track background -->
  <rect
    x={BAR_X} y={TRACK_Y}
    width={BAR_WIDTH} height={TRACK_H}
    rx="1"
    fill="var(--v2-bg-darkest)"
    stroke="var(--v2-bg-panel)"
    stroke-width="1"
  />

  <!-- Segments -->
  {#each Array(SEG_COUNT) as _, i}
    {@const x = segX(i)}

    <!-- Dim (inactive) -->
    <rect
      data-segment={i}
      {x} y={TRACK_Y + 1}
      width={SEG_W} height={TRACK_H - 2}
      fill={dimColor(i)}
    />

    <!-- Active -->
    {#if i < fullSegs}
      <rect
        {x} y={TRACK_Y + 1}
        width={SEG_W} height={TRACK_H - 2}
        fill={activeColor(i)}
      />
    {:else if i === fullSegs && fracSeg > 0.01}
      <rect
        {x} y={TRACK_Y + 1}
        width={Math.max(1, SEG_W * fracSeg)} height={TRACK_H - 2}
        fill={activeColor(i)}
      />
    {/if}
  {/each}

  <!-- Peak hold indicator -->
  {#if showPeak}
    <line
      x1={peakX} y1={TRACK_Y}
      x2={peakX} y2={TRACK_Y + TRACK_H}
      stroke={peakColor}
      stroke-width="2"
      opacity="0.9"
    />
  {/if}

  <!-- Value readout: dBm aligned to bar center, S-unit above it -->
  <text
    x={READOUT_CX}
    y={TRACK_Y - (compact ? 2 : 3)}
    font-family="'Roboto Mono', monospace"
    font-size={S_UNIT_FS}
    font-weight="700"
    fill="var(--v2-text-lighter)"
    text-anchor="middle"
    dominant-baseline="text-after-edge"
  >{displaySUnit}</text>

  <text
    x={READOUT_CX}
    y={TRACK_Y + TRACK_H / 2}
    font-family="'Roboto Mono', monospace"
    font-size={DBM_FS}
    fill="var(--v2-text-dim)"
    text-anchor="middle"
    dominant-baseline="central"
  >{displayDbm}</text>
</svg>

<style>
  svg {
    display: block;
  }
</style>
