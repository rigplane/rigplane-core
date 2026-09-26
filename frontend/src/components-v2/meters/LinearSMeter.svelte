<script module lang="ts">
  // MOR-2250/MOR-2509: the lower-scale row's shape lives in `lower-scale.ts`
  // so semantic hosts can build descriptors without importing the component;
  // re-exported here because `MetersSurface` and the wiring already import
  // the type from this file.
  import type { LowerScaleTick, LowerScaleDescriptor } from './lower-scale';
  export type { LowerScaleTick, LowerScaleDescriptor };
</script>

<script lang="ts">
  import { onMount, untrack } from 'svelte';
  import type {
    MeterContinuitySession,
    MeterSourceIdentity,
  } from '../../primitives/meters/meter-ballistics.svelte';
  import type { MeterDisplay } from '../../presentation/languages/contract';
  import { DEFAULT_METER_DISPLAY } from './meter-display';
  import {
    createSignalMeterMotion,
    type SignalMeterFrame,
  } from './signal-meter-motion.svelte';
  import {
    projectSignalMeter,
    lerpScaleKnots,
    thinUniformScaleMarksForWidth,
    type SignalScaleMark,
    type SignalMeterProjection,
  } from './smeter-scale';

  interface CommonLinearMeterProps {
    mainPresent?: boolean;
    compact?: boolean;
    label?: string;
    variant?: string;
    display?: MeterDisplay;
    /**
     * MOR-2250: an optional second scale row below the bar. Structural
     * elements (label text, tick marks, tick label text) render whenever
     * this prop is present AT ALL, regardless of `valueFraction`/`fault` — a
     * reading of zero (e.g. not transmitting) must occupy the exact same
     * height as one reading full-scale, or the tile visibly resizes
     * crossing RX/TX (owner ruling, MOR-2250). Leaving the prop unset is a
     * different claim: this instrument has no second scale at all.
     */
    lowerScale?: LowerScaleDescriptor;
    /**
     * MOR-2250 fix cycle 2: independent dim for the MAIN bar content — the
     * label, scale labels, ticks, segments, peak line and value readout;
     * everything in this component that is NOT the `lowerScale` row.
     * Defaults to `true` so every caller that does not pass it (VfoPanel and
     * MobileRadioLayout — neither passes this prop) keeps rendering at full
     * opacity, unchanged. `MetersSurface.svelte` is the only caller that
     * passes `false`, sourced from `meters.signal.relevant`: the S-meter
     * tile's own dimming moved from that tile's CSS into this prop so it can
     * never compound with `lowerScale.relevant`'s independent dim — see the
     * sibling `data-main-relevant` / `data-lower-relevant` groups below.
     */
    relevant?: boolean;
  }

  type LocalSignalInput = (
    | { projection: SignalMeterProjection; value?: never }
    | { projection?: never; value: number | null }
  ) & {
    frame?: never;
    source?: MeterSourceIdentity | null;
    session?: MeterContinuitySession | null;
  };
  type HostFrameInput = {
    frame: SignalMeterFrame;
    projection?: never;
    value?: never;
    source?: never;
    session?: never;
  };
  type SignalInput = LocalSignalInput | HostFrameInput;
  type Props = CommonLinearMeterProps & SignalInput;

  type InputMode = 'local' | 'frame';

  function hasOwn(value: object, key: string): boolean {
    return Object.prototype.hasOwnProperty.call(value, key);
  }

  function resolveInputMode(current: Props): InputMode {
    const hasFrame = hasOwn(current, 'frame');
    const hasProjection = hasOwn(current, 'projection');
    const hasValue = hasOwn(current, 'value');
    if (Number(hasFrame) + Number(hasProjection) + Number(hasValue) !== 1) {
      throw new TypeError('LinearSMeter requires exactly one of frame, projection, or value');
    }
    if (hasFrame) {
      if (current.frame === undefined) {
        throw new TypeError('LinearSMeter frame must be defined when supplied');
      }
      if (hasOwn(current, 'source') || hasOwn(current, 'session')) {
        throw new TypeError('LinearSMeter frame owns source and session continuity');
      }
      return 'frame';
    }
    if (hasProjection && current.projection === undefined) {
      throw new TypeError('LinearSMeter projection must be defined when supplied');
    }
    if (hasValue && current.value === undefined) {
      throw new TypeError('LinearSMeter value must be a number or null when supplied');
    }
    return 'local';
  }

  let props: Props = $props();
  const initialInputMode = untrack(() => resolveInputMode(props));
  const inputMode = $derived.by(() => {
    const current = resolveInputMode(props);
    if (current !== initialInputMode) {
      throw new TypeError('LinearSMeter input mode cannot change after mount');
    }
    return current;
  });
  const compact = $derived(props.compact ?? false);
  const label = $derived(props.label);
  const variant = $derived(props.variant);
  const display = $derived(props.display ?? DEFAULT_METER_DISPLAY);
  const lowerScale = $derived(props.lowerScale);
  const relevant = $derived(props.relevant ?? true);
  const mainPresent = $derived(props.mainPresent ?? true);
  const signalProjection = $derived.by((): SignalMeterProjection => {
    if (inputMode === 'frame') return (props as HostFrameInput).frame.projection;
    if (hasOwn(props, 'projection')) {
      return (props as LocalSignalInput & { projection: SignalMeterProjection }).projection;
    }
    return projectSignalMeter((props as { value: number | null }).value);
  });

  const isVfoVariant = $derived(variant === 'vfo' || variant === 'vfo-wide');

  // ── Segment geometry ────────────────────────────────────────────────────────
  // Projected fractions are independent of how many visual segments this
  // component draws; this component only maps them onto its local geometry.
  const RAW_SEGMENT_DOMAIN = 20;
  const SEG_COUNT = $derived(display.segmentCount);
  const SEG_GAP = $derived(display.segmentGapPx);
  const BAR_X = 8;
  const BAR_WIDTH = 484;
  const SEG_W = $derived((BAR_WIDTH - (SEG_COUNT - 1) * SEG_GAP) / SEG_COUNT);
  // MOR-2613: the default face stretches viewBox "0 0 600 …" to the rendered
  // width, so a sub-pixel reading change rewrites x/width every frame and
  // forces layout. Geometry that moves continuously is snapped to whole
  // device pixels of the measured width. Before the first measure (and in
  // jsdom, which has no ResizeObserver) the fallback grid is 0.5 user units.
  const FACE_VIEWBOX_W = 600;
  const GEOMETRY_FALLBACK_STEP = 0.5;
  let faceWidth = $state(0);
  let faceSvgElement = $state.raw<SVGSVGElement | null>(null);
  $effect(() => {
    const element = faceSvgElement;
    if (element === null || typeof ResizeObserver === 'undefined') return;
    const observer = new ResizeObserver(() => { faceWidth = element.clientWidth; });
    observer.observe(element);
    faceWidth = element.clientWidth;
    return () => observer.disconnect();
  });
  function quantizeUserUnits(value: number): number {
    const step = faceWidth > 0 ? FACE_VIEWBOX_W / faceWidth : GEOMETRY_FALLBACK_STEP;
    return Math.round(value / step) * step;
  }

  const READOUT_CX = $derived(BAR_X + BAR_WIDTH + 54);

  function segX(i: number): number {
    return BAR_X + i * (SEG_W + SEG_GAP);
  }

  function fractionToX(fraction: number): number {
    return BAR_X + fraction * SEG_COUNT * (SEG_W + SEG_GAP);
  }

  // Index of the first visual segment at or above the projected S9 anchor,
  // rescaled by SEG_COUNT so it follows non-20 display geometry.
  const crossoverSegmentIndex = $derived(signalProjection.crossoverFraction === null
    ? null : Math.round(signalProjection.crossoverFraction * SEG_COUNT));

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
  // `i / (SEG_COUNT - 1)` against (division by zero). `smoothedSegs` is the
  // normalized smoother value projected onto the local visual domain, so
  // dividing it by SEG_COUNT recovers the reading's fill fraction. The
  // single segment samples the ramp by that value instead of by index, so it
  // still reports strong/over-range readings in the ramp's hot colors
  // rather than collapsing every reading to one fixed color.
  function activeColor(i: number): string {
    if (hasTone && crossoverSegmentIndex !== null) {
      return i < crossoverSegmentIndex ? display.toneBelowS9 : display.toneAboveS9;
    }
    const denom = SEG_COUNT - 1;
    const fraction = denom === 0 ? Math.min(1, Math.max(0, smoothedSegs / SEG_COUNT)) : i / denom;
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
    return crossoverSegmentIndex !== null
      ? (i < crossoverSegmentIndex ? '#0A2415' : '#1A1008') : '#0A2415';
  }

  // ── Lower scale row (MOR-2250, PR 2 of 2) ───────────────────────────────────
  // Position/tick geometry mirrors the upper scale-label/tick rendering above
  // (text elements for labels, line elements for ticks) rather than a new
  // drawing method. Fill segments reuse the SAME SEG_COUNT/segX geometry as
  // the main bar so the two rows' columns line up.
  //
  // Fault color: `BarGauge.svelte`'s own SWR-fault literal
  // (`fault ? 'var(--v2-accent-red, #ff4040)' : ...`), duplicated rather than
  // imported or routed through a new token. Checked first: nothing in
  // `DesignLanguageTokens` (typography/geometry/meters/frequency/motion/
  // focusRing/rx/tx) models a generic alarm/fault tone, and `MeterDisplay`'s
  // `toneBelowS9`/`toneAboveS9` name a different concept — position relative
  // to the S9 crossover, not "past a fault threshold" — so reusing that pair
  // would conflate two unrelated ideas. Three independent literals now carry
  // this exact value (`BarGauge.svelte`, `MetersDockPanel.svelte`, and this
  // site) — not consolidated into a shared constant here; that call is left
  // for a follow-up rather than folded into this fix.
  const LOWER_FAULT_COLOR = 'var(--v2-accent-red, #ff4040)';
  const LOWER_ACTIVE_COLOR = 'var(--v2-accent-green-medium)';
  const LOWER_DIM_COLOR = '#141414';
  // Matches the 0.4 that `MetersSurface.svelte`'s own `.meter-tile` dim rule
  // uses — same dim-not-hide value (MOR-977). Shared by BOTH independent
  // groups below (`data-main-relevant` and `data-lower-relevant`) so the two
  // dims can never drift apart into two different literals.
  const DIM_OPACITY = 0.4;

  function lowerTickX(fraction: number): number {
    return BAR_X + fraction * BAR_WIDTH;
  }

  function lowerActiveColor(): string {
    return lowerScale?.fault ? LOWER_FAULT_COLOR : LOWER_ACTIVE_COLOR;
  }

  const lowerFillSegs = $derived(
    lowerScale ? Math.max(0, Math.min(SEG_COUNT, lowerScale.valueFraction * SEG_COUNT)) : 0,
  );
  const lowerFullSegs = $derived(Math.floor(lowerFillSegs));
  const lowerFracSeg = $derived(lowerFillSegs - lowerFullSegs);

  // ── Label marks ─────────────────────────────────────────────────────────────
  let labelMarks = $derived(signalProjection.marks);

  // ── Layout (switches between full / compact) ────────────────────────────────
  //   When label is present: label at top → meter shifted down
  //   Vertical stacking: [label] → scale labels → ticks → bar
  const LABEL_OFFSET  = $derived(label ? (compact ? 10 : 14) : 0);
  const TAG_Y         = $derived(compact ? 2 : 3);   // label "MAIN"/"SUB" Y
  const TAG_FS        = $derived(compact ? 7  : 8);
  const SCALE_LABEL_Y = $derived((compact ? 2 : 3) + LABEL_OFFSET);
  const SCALE_LABEL_FS = $derived(compact ? 8 : 9);
  const TICK_MAJOR_Y1 = $derived((compact ? 14 : 18) + LABEL_OFFSET);
  const TICK_MAJOR_Y2 = $derived((compact ? 26 : 38) + LABEL_OFFSET);
  const TICK_MID_Y1   = $derived((compact ? 17 : 22) + LABEL_OFFSET);
  const TICK_MID_Y2   = $derived((compact ? 26 : 38) + LABEL_OFFSET);
  const TICK_MINOR_Y1 = $derived((compact ? 20 : 28) + LABEL_OFFSET);
  const TICK_MINOR_Y2 = $derived((compact ? 26 : 38) + LABEL_OFFSET);
  const TRACK_Y       = $derived((compact ? 28 : 40) + LABEL_OFFSET);
  const TRACK_H       = $derived(compact ? 8 : 14);
  // Readout aligned to bar: S-unit centered on bar, dBm just below
  const S_UNIT_Y      = $derived(TRACK_Y - (compact ? 1 : 2));
  const S_UNIT_FS     = $derived(compact ? 12 : 15);
  const DBM_Y         = $derived(TRACK_Y + TRACK_H + (compact ? 1 : 2));
  const DBM_FS        = $derived(compact ? 8 : 9);

  // Lower scale row (MOR-2250): stacked below the main bar, in the bar's own
  // x-range — it sits below TRACK_Y + TRACK_H the same way the S-unit/dBm
  // readout does, but at BAR_X..BAR_X+BAR_WIDTH rather than READOUT_CX, so
  // the two never overlap.
  const LOWER_GAP      = $derived(compact ? 4 : 6);
  const LOWER_LABEL_Y  = $derived(TRACK_Y + TRACK_H + LOWER_GAP);
  const LOWER_LABEL_FS = $derived(compact ? 8 : 9);
  const LOWER_TICK_Y1  = $derived(LOWER_LABEL_Y + (compact ? 8 : 10));
  const LOWER_TICK_Y2  = $derived(LOWER_TICK_Y1 + (compact ? 5 : 7));
  const LOWER_TRACK_Y  = $derived(LOWER_TICK_Y2 + 2);
  const LOWER_TRACK_H  = $derived(compact ? 6 : 8);

  // Bottom padding symmetric to top; the lower row (when present) pushes the
  // bottom edge down instead of the plain post-bar padding.
  const TOTAL_HEIGHT  = $derived(
    lowerScale ? LOWER_TRACK_Y + LOWER_TRACK_H + SCALE_LABEL_Y : TRACK_Y + TRACK_H + SCALE_LABEL_Y,
  );

  const localMotion = initialInputMode === 'local'
    ? untrack(() => createSignalMeterMotion({
        projection: signalProjection,
        present: mainPresent,
        source: (props as LocalSignalInput).source,
        session: (props as LocalSignalInput).session,
      }))
    : null;
  const meterFrame = $derived(
    inputMode === 'frame' ? (props as HostFrameInput).frame : localMotion!.frame,
  );

  $effect(() => {
    if (localMotion === null) return;
    const currentProjection = signalProjection;
    const currentPresent = mainPresent;
    const currentSource = (props as LocalSignalInput).source;
    const currentSession = (props as LocalSignalInput).session;
    untrack(() => localMotion.sync({
      projection: currentProjection,
      present: currentPresent,
      source: currentSource,
      session: currentSession,
    }));
  });

  onMount(() => {
    if (localMotion === null) return;
    localMotion.start();
    return () => localMotion.stop();
  });

  let smoothedSegs = $derived(meterFrame.smoothedFraction * SEG_COUNT);
  let peakSegs = $derived((meterFrame.peakFraction ?? 0) * SEG_COUNT);
  // Peak X position for the vertical indicator line, snapped to a whole
  // device pixel of the rendered face (MOR-2613).
  let peakX = $derived(quantizeUserUnits(BAR_X + peakSegs * (SEG_W + SEG_GAP)));
  // Only show peak line if it's meaningfully ahead of current bar
  let showPeak = $derived(
    signalProjection.scaleMode !== 'none' && signalProjection.motionFraction !== null
      && mainPresent && peakSegs - smoothedSegs > 0.3,
  );

  // Peak-line color zones as fractions of the raw 20-segment domain — 15/20
  // and 18/20 are visual gradient stops with no calibration anchor (unlike
  // projected crossover above), rescaled the same way so they track SEG_COUNT.
  const peakZoneYellow = $derived(Math.round((15 / RAW_SEGMENT_DOMAIN) * SEG_COUNT));
  const peakZoneOrange = $derived(Math.round((18 / RAW_SEGMENT_DOMAIN) * SEG_COUNT));

  // Color of peak line based on zone
  let peakColor = $derived(crossoverSegmentIndex === null
    ? 'var(--v2-accent-cyan-bright)'
    : peakSegs <= crossoverSegmentIndex ? 'var(--v2-accent-cyan-bright)'
      : peakSegs <= peakZoneYellow ? 'var(--v2-accent-yellow)'
        : peakSegs <= peakZoneOrange ? 'var(--v2-accent-orange-alt)' : 'var(--v2-accent-red-alt)');

  // ── Reactive display values ─────────────────────────────────────────────────
  let fullSegs = $derived(signalProjection.motionFraction === null ? 0 : Math.floor(smoothedSegs));
  let fracSeg  = $derived(
    signalProjection.motionFraction === null ? 0 : smoothedSegs - Math.floor(smoothedSegs),
  );

  let displaySUnit = $derived(signalProjection.primaryText);
  let displayDbm   = $derived(signalProjection.secondaryText);

  // ── MOR-2521: stable node set ───────────────────────────────────────────────
  // Fill rects (and the peak line) are permanent nodes: a reading changes
  // only their width/fill/visibility attributes, never their presence — the
  // node-count sweep in __tests__/LinearSMeter.test.ts fails if a value step
  // adds or removes a node. The `frac > 0.01` arm keeps the sub-1% partial
  // guard the conditional markup used to carry (pinned by the same sweep).
  function segLit(i: number, full: number, frac: number): boolean {
    return i < full || (i === full && frac > 0.01);
  }
  function segWidth(i: number, full: number, frac: number): number {
    // Full segments are a fixed grid; only the partial tail moves every frame.
    return i === full ? Math.max(1, quantizeUserUnits(SEG_W * frac)) : SEG_W;
  }

  // v2.11.1 SDR SVG geometry; the current calibrated scale still owns positions.
  const SDR_CELLS = 40;
  const SDR_CELL_WIDTH = 328 / SDR_CELLS;
  const SDR_SUB_WIDTH = (SDR_CELL_WIDTH - 2 - 0.5) / 2;
  const sdrFill = $derived(
    (signalProjection.motionFraction === null ? 0 : meterFrame.smoothedFraction) * SDR_CELLS * 2,
  );
  const sdrCrossover = $derived(signalProjection.crossoverFraction === null
    ? null : signalProjection.crossoverFraction * SDR_CELLS * 2);
  function sdrColor(index: number): string {
    if (sdrCrossover === null) {
      return index < sdrFill ? '#4FB9EC' : '#1a2230';
    }
    const aboveS9 = index >= sdrCrossover;
    return index < sdrFill ? (aboveS9 ? '#FF3030' : '#4FB9EC')
      : (aboveS9 ? '#2a1618' : '#1a2230');
  }

  // ── MOR-2509 v8 face (variants 'vfo' / 'vfo-wide') ─────────────────────────
  // Geometry pinned by `__tests__/LinearSMeter.mockup-v8.test.ts`: this SVG
  // carries no viewBox, so one user unit is one CSS pixel and the 2px-lit /
  // 1px-gap dash pattern renders in whole device-independent pixels at every
  // rendered width. The well around the SVG (padding 4px 8px 7px, radius 6px)
  // is VfoPanel's `.panel-meter`; the constants below are the content-box
  // geometry the test pins: ticks row 20px, value row 16px (track 12px
  // centred in it) at margin-top 3, Po tick row 14px at margin-top 7, Po bar
  // 7px at margin-top 1, and the 58px value column (10px gap + 48px cell)
  // the ticks row, Po ticks, Po bar and value cell all reserve on the right.
  const VFO_SEG_PITCH = 3;
  const VFO_SEG_LIT = 2;
  const VFO_SEG_DASH = `${VFO_SEG_LIT} ${VFO_SEG_PITCH - VFO_SEG_LIT}`;
  const S9_UNIFORM_FRACTION = 4 / 7;
  const VFO_VALUE_GAP = 10;
  const VFO_VALUE_CELL_W = 48;
  const VFO_VALUE_COLUMN_W = VFO_VALUE_GAP + VFO_VALUE_CELL_W;
  // The S-scale tick row: 12px numerals at the top edge, a 1x4px tick under
  // each numeral starting 15px down, first numeral left-anchored at its
  // slot, the rest centred; '+' numerals tighten by -.05em = -0.6px at
  // 12px; font weight 400 — all pinned by
  // `__tests__/LinearSMeter.mockup-v8.test.ts`.
  // Rounded to 2 decimals so the attribute serialises exactly '-0.6' —
  // the raw product is -0.6000000000000001 in IEEE doubles.
  const VFO_LABEL_FS = 12;
  const VFO_LABEL_Y = 0;
  const VFO_LABEL_WEIGHT = 400;
  const VFO_PLUS_LETTER_SPACING = Math.round(-0.05 * VFO_LABEL_FS * 100) / 100;
  const VFO_LABEL_GLYPH_ADVANCE = VFO_LABEL_FS * 0.6;
  const VFO_TICKS_ROW_H = 20;
  const VFO_TICK_MARK_Y1 = 15;
  const VFO_TICK_MARK_Y2 = 19;
  // The value row: 16px tall, 3px below the tick row; the 12px track
  // centres inside it. The single 13px reading line replaces the v7
  // two-line S-unit/dBm block — no dBm line, pinned by
  // `__tests__/LinearSMeter.mockup-v8.test.ts`.
  const VFO_VALUE_ROW_H = 16;
  const VFO_VALUE_FS = 13;
  const VFO_TRACK_H = 12;
  const VFO_MROW_TOP = VFO_TICKS_ROW_H + 3;
  const VFO_BAR_Y = VFO_MROW_TOP + (VFO_VALUE_ROW_H - VFO_TRACK_H) / 2;
  const VFO_BAR_H = VFO_TRACK_H;
  // The Po rows (.poticks/.po): 14px label row 7px below the value row, then
  // the 7px bar 1px below that. Labels only — the mock-up draws no tick marks
  // under the Po numerals. The `Po` row label hangs 2px past the content box
  // (bottom:5px of the 79px well), so the root SVG must not clip.
  const VFO_PO_TICKS_TOP = VFO_MROW_TOP + VFO_VALUE_ROW_H + 7;
  const VFO_PO_TICKS_H = 14;
  const VFO_PO_LABEL_FS = 12;
  const VFO_PO_Y = VFO_PO_TICKS_TOP + VFO_PO_TICKS_H + 1;
  const VFO_PO_H = 7;
  const VFO_PO_ROW_LABEL_BASELINE = VFO_PO_Y + VFO_PO_H + 2;
  const VFO_TOTAL_H = VFO_PO_Y + VFO_PO_H;
  // Ink tones the design language may vary: the mock-up's values are
  // studioline's; segment fills stay on the shared theme tokens below.
  const VFO_TONE_BLUE = 'var(--v2-meter-blue)';
  const VFO_TONE_RED = 'var(--v2-meter-red)';
  const VFO_TONE_UNLIT = 'var(--v2-meter-unlit)';
  const VFO_TONE_TICK_LABEL = 'var(--dl-vfo-meter-tick-label, #e6edf4)';
  const VFO_TONE_VALUE = 'var(--dl-vfo-meter-value, #f4f8fc)';
  const VFO_TONE_PEAK = 'var(--dl-vfo-meter-peak, #e8f1ff)';
  const VFO_TONE_PO_LABEL = 'var(--dl-vfo-meter-po-label, #c3ced9)';
  // Afterglow carries the bar's own tone at the mock-up's .after opacity.
  const VFO_AFTERGLOW_OPACITY = 0.38;

  let vfoWidth = $state(0);
  let vfoSvgElement = $state.raw<SVGSVGElement | null>(null);
  // Measured through a hand-rolled observer rather than bind:clientWidth so
  // environments without ResizeObserver (jsdom) render a degenerate but
  // error-free 0-width face instead of throwing at bind time.
  $effect(() => {
    const element = vfoSvgElement;
    if (element === null || typeof ResizeObserver === 'undefined') return;
    const observer = new ResizeObserver(() => { vfoWidth = element.clientWidth; });
    observer.observe(element);
    vfoWidth = element.clientWidth;
    return () => observer.disconnect();
  });
  const vfoTrackX = 0;
  // The flex model pinned by `__tests__/LinearSMeter.mockup-v8.test.ts`:
  // the track is the exact width minus the 58px value column (10px gap +
  // 48px cell), NOT floored to the dash pitch. Only the dash raster snaps:
  // the lit extent and the S9 colour split land on the 3px whole-dash grid
  // (see vfoLitExtentX/vfoS9X), so the unlit track itself may end on a
  // partial dash. clientWidth is an integer, so dash edges stay
  // whole-pixel.
  const vfoTrackW = $derived(Math.max(0, vfoWidth - VFO_VALUE_COLUMN_W));
  const vfoReadoutX = $derived(vfoTrackX + vfoTrackW + VFO_VALUE_GAP);
  // The blue→red handover is part of the dash RASTER, not the layout: it
  // snaps to the whole-dash grid so no dash renders half blue and half red,
  // staying within one pitch of the exact 4/7 share the S9 numeral sits at.
  const vfoS9X = $derived(signalProjection.crossoverFraction === null
    ? vfoTrackX + vfoTrackW
    : vfoTrackX + Math.round((S9_UNIFORM_FRACTION * vfoTrackW) / VFO_SEG_PITCH) * VFO_SEG_PITCH);

  /** The lit extent at a fractional fill position: whole segments only —
   *  a segment lights when the fill covers at least half of it, and the
   *  extent is that segment's right edge, so every rendered dash is a
   *  whole 2px dash. The cap is the last dash that FITS the track: the
   *  greatest 3n+2 ≤ trackW (548 itself on a 548px track), never a
   *  truncated terminal dash. */
  function vfoLitExtentX(fraction: number): number {
    const fillPx = fraction * vfoTrackW;
    const segment = Math.floor((fillPx - VFO_SEG_LIT / 2) / VFO_SEG_PITCH);
    if (segment < 0) return vfoTrackX;
    const lastSegment = Math.floor((vfoTrackW - VFO_SEG_LIT) / VFO_SEG_PITCH);
    if (lastSegment < 0) return vfoTrackX;
    return vfoTrackX + Math.min(lastSegment, segment) * VFO_SEG_PITCH + VFO_SEG_LIT;
  }

  const vfoFillFraction = $derived(signalProjection.motionFraction === null ? 0
    : lerpScaleKnots(signalProjection.uniformScaleKnots, meterFrame.smoothedFraction));
  const vfoPeakFraction = $derived(signalProjection.motionFraction === null ? 0
    : lerpScaleKnots(signalProjection.uniformScaleKnots, meterFrame.peakFraction ?? 0));
  // The glow is never allowed behind the bar: the max re-asserts the source
  // invariant for host-frame callers that build the frame themselves.
  const vfoGlowFraction = $derived(Math.max(vfoFillFraction,
    meterFrame.afterglowFraction === null ? 0
      : lerpScaleKnots(signalProjection.uniformScaleKnots, meterFrame.afterglowFraction)));
  const vfoFillEndX = $derived(vfoLitExtentX(vfoFillFraction));
  const vfoGlowEndX = $derived(vfoLitExtentX(vfoGlowFraction));
  // The marker sits on the same snapped grid as the bar's lit extent — the
  // snap is monotone, so the peak cannot fall below the displayed extent.
  const vfoPeakX = $derived(vfoLitExtentX(vfoPeakFraction));
  const vfoBarMidY = VFO_BAR_Y + VFO_BAR_H / 2;
  // An unread meter is the empty unlit face: the scale stays, the fill and
  // peak never move off the left end, and the readout slots stay empty —
  // no placeholder glyph may stand where a reading is not.
  const vfoReadingKnown = $derived(signalProjection.motionFraction !== null);

  // Peak marker: rises with the bar, holds ~1 s, then falls. Under
  // prefers-reduced-motion the v8 face shows the stepped bar only. The
  // mock-up's marker is one constant light tone at every zone — no red
  // variant past S9 (`.peak { background: #e8f1ff }`).
  const vfoShowPeak = $derived(
    signalProjection.scaleMode !== 'none' && vfoReadingKnown
      && mainPresent && !meterFrame.reducedMotion
      && (meterFrame.peakFraction ?? 0) - meterFrame.smoothedFraction > 0.3,
  );

  const vfoLowerFraction = $derived(
    lowerScale ? Math.min(1, Math.max(0, lowerScale.valueFraction)) : 0,
  );
  const vfoLowerFillEndX = $derived(vfoLitExtentX(vfoLowerFraction));
  // The lower row's height is part of the face's rhythm, reserved whether
  // or not a descriptor is present: the frequency row above must not move
  // when the TX target (and with it the Po row) flips MAIN↔SUB.
  const vfoTotalH = VFO_TOTAL_H;

  function vfoScaleLabelWidth(mark: SignalScaleMark): number {
    const interGlyphSpacing = Math.max(0, mark.text.length - 1)
      * (mark.overS9 ? VFO_PLUS_LETTER_SPACING : 0);
    return mark.text.length * VFO_LABEL_GLYPH_ADVANCE + interGlyphSpacing;
  }

  const vfoScaleMarks = $derived(thinUniformScaleMarksForWidth(
    signalProjection.uniformScaleMarks, vfoTrackW, vfoScaleLabelWidth,
  ));
  // The v8 face reads one line — the S-unit — so the CALIBRATED case alone
  // drops its second field (the dBm) from the projection's accessible name.
  // Every other wording ('raw, uncalibrated', the value itself with
  // 'unit unknown'/'scale unavailable' on unprojectable domains, the
  // reading-unknown fallback) is a fact the projection states honestly and
  // stays verbatim — pinned by ReceiverInstrumentHost.isolated.test.ts.
  const vfoAccessibleLabel = $derived.by(() => {
    if (signalProjection.scaleMode === 's' && signalProjection.motionFraction !== null) {
      return `S meter ${displaySUnit}`;
    }
    return signalProjection.accessibleDescription;
  });
</script>

{#if variant === 'sdr-screen'}
  <svg class="sdr-meter" viewBox="0 0 420 50" preserveAspectRatio="none"
    data-variant={variant} role="img" aria-label={signalProjection.accessibleDescription}>
    {#if mainPresent}
    <g data-main-relevant={relevant ? 'true' : 'false'} opacity={relevant ? 1 : DIM_OPACITY}>
      <g font-family="Roboto Mono, monospace" font-size="11" fill="var(--v2-text-primary, #C8D4E0)" font-weight="700">
        <text x="4" y="14">{signalProjection.scaleMode === 's' ? 'S' : signalProjection.scaleMode === 'raw' ? 'raw' : 'level'}</text>
        {#each labelMarks as mark}
          <text x={14 + mark.fraction * 328} y="14"
            text-anchor="middle" fill={mark.actual > 0 ? 'var(--v2-accent-red, #FF4040)' : 'var(--v2-text-primary, #C8D4E0)'}>
            {mark.text.replace(/^S/, '')}
          </text>
        {/each}
      </g>
      {#each Array(SDR_CELLS * 2) as _, index}
        <rect data-sdr-segment={index}
          x={14 + Math.floor(index / 2) * SDR_CELL_WIDTH + (index % 2) * (SDR_SUB_WIDTH + 0.5)}
          y="22" width={SDR_SUB_WIDTH} height="18" fill={sdrColor(index)} />
      {/each}
      <text x="412" y="31" text-anchor="end" fill="var(--v2-text-primary, #DFFCF5)"
        font-family="Roboto Mono, monospace" font-size="12" font-weight="700">{displaySUnit}</text>
      {#if signalProjection.scaleMode === 'raw'}
        <text x="412" y="46" text-anchor="end" fill="var(--v2-text-secondary, #A0B4C8)" font-size="10">uncalibrated</text>
      {/if}
    </g>
    {/if}
  </svg>
{:else if isVfoVariant}
  <!-- MOR-2509 v8 face: pixel-locked (no viewBox) so the 2/1 dash pattern is
       in whole device pixels; every element below is a permanent node whose
       geometry or visibility — never its presence — follows the reading. -->
  <svg
    bind:this={vfoSvgElement}
    width="100%"
    height={vfoTotalH}
    data-variant={variant}
    role="img"
    aria-label={vfoAccessibleLabel}
    data-lower-fault={lowerScale ? (lowerScale.fault ? 'true' : 'false') : undefined}
  >
    {#if mainPresent}
    <g data-main-relevant={relevant ? 'true' : 'false'} opacity={relevant ? 1 : DIM_OPACITY}>
      {#if signalProjection.scaleMode === 's'}
        {#each vfoScaleMarks as mark, index (mark.text)}
          {@const x = vfoTrackX + mark.slot * vfoTrackW}
          <text
            data-scale-label={index}
            x={x} y={VFO_LABEL_Y}
            font-family="'Roboto Mono', monospace"
            font-size={VFO_LABEL_FS}
            font-weight={VFO_LABEL_WEIGHT}
            letter-spacing={mark.overS9 ? VFO_PLUS_LETTER_SPACING : undefined}
            fill={mark.overS9 ? VFO_TONE_RED : VFO_TONE_TICK_LABEL}
            text-anchor={mark.slot === 0 ? 'start' : 'middle'}
            dominant-baseline="text-before-edge"
          >{mark.text}</text>
          <line
            data-scale-tick={index}
            x1={x} y1={VFO_TICK_MARK_Y1}
            x2={x} y2={VFO_TICK_MARK_Y2}
            stroke={mark.overS9 ? VFO_TONE_RED : VFO_TONE_TICK_LABEL}
            stroke-width="1"
          />
        {/each}
      {/if}

      <line
        data-meter-track
        x1={vfoTrackX} y1={vfoBarMidY}
        x2={vfoTrackX + vfoTrackW} y2={vfoBarMidY}
        stroke={VFO_TONE_UNLIT}
        stroke-width={VFO_BAR_H}
        stroke-dasharray={VFO_SEG_DASH}
      />
      <line
        data-meter-glow
        x1={Math.min(vfoFillEndX, vfoS9X)} y1={vfoBarMidY}
        x2={Math.min(vfoGlowEndX, vfoS9X)} y2={vfoBarMidY}
        stroke={VFO_TONE_BLUE}
        stroke-width={VFO_BAR_H}
        stroke-dasharray={VFO_SEG_DASH}
        stroke-opacity={VFO_AFTERGLOW_OPACITY}
        visibility={vfoGlowEndX > vfoFillEndX ? 'visible' : 'hidden'}
      />
      <line
        data-meter-glow-red
        x1={Math.max(vfoFillEndX, vfoS9X)} y1={vfoBarMidY}
        x2={Math.max(vfoGlowEndX, vfoS9X)} y2={vfoBarMidY}
        stroke={VFO_TONE_RED}
        stroke-width={VFO_BAR_H}
        stroke-dasharray={VFO_SEG_DASH}
        stroke-opacity={VFO_AFTERGLOW_OPACITY}
        visibility={vfoGlowEndX > Math.max(vfoFillEndX, vfoS9X) ? 'visible' : 'hidden'}
      />
      <line
        data-meter-fill
        x1={vfoTrackX} y1={vfoBarMidY}
        x2={Math.min(vfoFillEndX, vfoS9X)} y2={vfoBarMidY}
        stroke={VFO_TONE_BLUE}
        stroke-width={VFO_BAR_H}
        stroke-dasharray={VFO_SEG_DASH}
        visibility={vfoFillEndX > vfoTrackX ? 'visible' : 'hidden'}
      />
      <line
        data-meter-fill-red
        x1={vfoS9X} y1={vfoBarMidY}
        x2={vfoFillEndX > vfoS9X ? vfoFillEndX : vfoS9X} y2={vfoBarMidY}
        stroke={VFO_TONE_RED}
        stroke-width={VFO_BAR_H}
        stroke-dasharray={VFO_SEG_DASH}
        visibility={vfoFillEndX > vfoS9X ? 'visible' : 'hidden'}
      />
      <line
        data-meter-peak
        x1={vfoPeakX} y1={VFO_BAR_Y}
        x2={vfoPeakX} y2={VFO_BAR_Y + VFO_BAR_H}
        stroke={VFO_TONE_PEAK}
        stroke-width="2"
        visibility={vfoShowPeak ? 'visible' : 'hidden'}
      />
      <text
        data-meter-reading
        x={vfoReadoutX}
        y={VFO_MROW_TOP + VFO_VALUE_ROW_H / 2}
        font-family="'Roboto Mono', monospace"
        font-size={VFO_VALUE_FS}
        font-weight={VFO_LABEL_WEIGHT}
        fill={VFO_TONE_VALUE}
        text-anchor="start"
        dominant-baseline="central"
      >{vfoReadingKnown ? displaySUnit : ''}</text>
    </g>
    {/if}

    {#if lowerScale}
      <g
        role="group" aria-label={lowerScale.accessibleDescription}
        data-lower-relevant={lowerScale.relevant ? 'true' : 'false'}
        opacity={lowerScale.relevant ? 1 : DIM_OPACITY}
      >
        {#each lowerScale.ticks as t (t.value)}
          {@const x = vfoTrackX + t.value * vfoTrackW}
          <text
            data-lower-tick-label={t.value}
            x={x} y={VFO_PO_TICKS_TOP}
            font-family="'Roboto Mono', monospace"
            font-size={VFO_PO_LABEL_FS}
            font-weight={VFO_LABEL_WEIGHT}
            fill={VFO_TONE_PO_LABEL}
            text-anchor={t.value === 0 ? 'start' : 'middle'}
            dominant-baseline="text-before-edge"
          >{t.label}</text>
        {/each}
        <line
          data-lower-track
          x1={vfoTrackX} y1={VFO_PO_Y + VFO_PO_H / 2}
          x2={vfoTrackX + vfoTrackW} y2={VFO_PO_Y + VFO_PO_H / 2}
          stroke={VFO_TONE_UNLIT}
          stroke-width={VFO_PO_H}
          stroke-dasharray={VFO_SEG_DASH}
        />
        <line
          data-lower-fill
          x1={vfoTrackX} y1={VFO_PO_Y + VFO_PO_H / 2}
          x2={vfoLowerFillEndX} y2={VFO_PO_Y + VFO_PO_H / 2}
          stroke={VFO_TONE_BLUE}
          stroke-width={VFO_PO_H}
          stroke-dasharray={VFO_SEG_DASH}
          visibility={vfoLowerFillEndX > vfoTrackX ? 'visible' : 'hidden'}
        />
        <text
          data-lower-row-label
          x={vfoReadoutX}
          y={VFO_PO_ROW_LABEL_BASELINE}
          font-family="'Roboto Mono', monospace"
          font-size={VFO_PO_LABEL_FS}
          font-weight={VFO_LABEL_WEIGHT}
          fill={VFO_TONE_PO_LABEL}
          text-anchor="start"
          dominant-baseline="text-after-edge"
        >{lowerScale.label}</text>
      </g>
    {/if}
  </svg>
{:else}
<svg
  bind:this={faceSvgElement}
  viewBox="0 0 600 {TOTAL_HEIGHT}"
  width="100%"
  height="auto"
  preserveAspectRatio="xMidYMid meet"
  data-variant={variant}
  role="img"
  aria-label={signalProjection.accessibleDescription}
  data-lower-fault={lowerScale ? (lowerScale.fault ? 'true' : 'false') : undefined}
>
  <!-- Main-bar content (MOR-2250 fix cycle 2): everything that is NOT the
       lower-scale row lives in this `<g>` (and its sibling further below,
       split only because the lower row sits between them in the markup) —
       dimmed by the `relevant` PROP, independent of `lowerScale.relevant`.
       This group is never an ancestor of `data-lower-relevant` below (nor
       the reverse), so the two opacities cannot compose. -->
  {#if mainPresent}
  <g data-main-relevant={relevant ? 'true' : 'false'} opacity={relevant ? 1 : DIM_OPACITY}>
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
      x={fractionToX(m.fraction)}
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
  {#each signalProjection.ticks as t}
    {@const tx = fractionToX(t.fraction)}
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

  <!-- Segments (MOR-2521): every fill rect is a permanent node — a reading
       changes only its width/fill/visibility attributes, never its
       presence. -->
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
    <rect
      data-meter-fill={i}
      {x} y={TRACK_Y + 1}
      width={segWidth(i, fullSegs, fracSeg)} height={TRACK_H - 2}
      fill={activeColor(i)}
      visibility={segLit(i, fullSegs, fracSeg) ? 'visible' : 'hidden'}
    />
  {/each}
  </g>
  {/if}

  <!-- Lower scale row (MOR-2250, PR 2 of 2): label + ticks are structural —
       they render whenever `lowerScale` is present at all, independent of
       `valueFraction`/`fault`, so the tile never changes height crossing
       RX/TX. Only the fill segments below (and their color) vary with the
       reading. -->
  {#if lowerScale}
    <!-- MOR-2250 fix cycle 2: the row's OWN dim, scoped to this `<g>` only —
         a SIBLING of the main-bar `<g data-main-relevant>` above/below, never
         its ancestor or descendant. `lowerScale.relevant` and the `relevant`
         prop are independent facts (`meters.swr.relevant` vs
         `meters.signal.relevant`); each group reads only its own, so the two
         opacities can never multiply together. Structural elements below
         still render unconditionally on `lowerScale`'s presence per the
         layout-stability note above `lowerScale` in Props; only their
         opacity, not their presence, depends on `relevant`. -->
    <g
      role="group" aria-label={lowerScale.accessibleDescription}
      data-lower-relevant={lowerScale.relevant ? 'true' : 'false'}
      opacity={lowerScale.relevant ? 1 : DIM_OPACITY}
    >
      <text
        data-lower-row-label
        x={BAR_X} y={LOWER_LABEL_Y}
        font-family="'Roboto Mono', monospace"
        font-size={LOWER_LABEL_FS}
        font-weight="700"
        fill="var(--v2-text-dim)"
        text-anchor="start"
        dominant-baseline="text-before-edge"
      >{lowerScale.label}{lowerScale.unit ? ` ${lowerScale.unit}` : ''}</text>
      {#if lowerScale.stateText}
        <text x={READOUT_CX} y={LOWER_LABEL_Y} font-size={LOWER_LABEL_FS}
          fill="var(--v2-text-dim)" text-anchor="middle" dominant-baseline="text-before-edge">{lowerScale.stateText}</text>
      {/if}

      {#each lowerScale.ticks as t}
        {@const tx = lowerTickX(t.value)}
        <text
          data-lower-tick-label={t.value}
          x={tx} y={LOWER_LABEL_Y}
          font-family="'Roboto Mono', monospace"
          font-size={LOWER_LABEL_FS}
          font-weight="700"
          fill="var(--v2-text-dim)"
          text-anchor="middle"
          dominant-baseline="text-before-edge"
        >{t.label}</text>
        <line
          data-lower-tick-mark={t.value}
          x1={tx} y1={LOWER_TICK_Y1}
          x2={tx} y2={LOWER_TICK_Y2}
          stroke="var(--v2-text-dim)"
          stroke-width="0.9"
          opacity="0.6"
        />
      {/each}

      <!-- Lower row track background -->
      <rect
        x={BAR_X} y={LOWER_TRACK_Y}
        width={BAR_WIDTH} height={LOWER_TRACK_H}
        rx="1"
        fill="var(--v2-bg-darkest)"
        stroke="var(--v2-bg-panel)"
        stroke-width="1"
      />

      <!-- Lower row segments (MOR-2521: permanent fill nodes, as above) -->
      {#each Array(SEG_COUNT) as _, i}
        {@const x = segX(i)}

        <!-- Dim (inactive) — always present, same as the main bar's own dim rects -->
        <rect
          data-lower-segment={i}
          {x} y={LOWER_TRACK_Y + 1}
          width={SEG_W} height={LOWER_TRACK_H - 2}
          fill={LOWER_DIM_COLOR}
        />

        <!-- Fill — the one part of this row that depends on valueFraction/fault -->
        <rect
          data-lower-fill={i}
          {x} y={LOWER_TRACK_Y + 1}
          width={segWidth(i, lowerFullSegs, lowerFracSeg)} height={LOWER_TRACK_H - 2}
          fill={lowerActiveColor()}
          visibility={segLit(i, lowerFullSegs, lowerFracSeg) ? 'visible' : 'hidden'}
        />
      {/each}
    </g>
  {/if}

  <!-- Main-bar content, part 2 (MOR-2250 fix cycle 2): the peak line and
       value readout are main-bar content too — split from the group above
       only because `lowerScale`'s own `<g>` sits between them in the
       markup. Same `relevant` prop, same sibling relationship to
       `data-lower-relevant`. -->
  {#if mainPresent}
  <g data-main-relevant={relevant ? 'true' : 'false'} opacity={relevant ? 1 : DIM_OPACITY}>
  <!-- Peak hold indicator (MOR-2521: permanent node, hidden while unarmed) -->
  <line
    data-meter-peak
    x1={peakX} y1={TRACK_Y}
    x2={peakX} y2={TRACK_Y + TRACK_H}
    stroke={peakColor}
    stroke-width="2"
    opacity="0.9"
    visibility={showPeak ? 'visible' : 'hidden'}
  />

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
  </g>
  {/if}
</svg>
{/if}

<style>
  .sdr-meter { width: 100%; height: 40px; overflow: visible; }
  svg {
    display: block;
  }
  svg[data-variant='vfo'],
  svg[data-variant='vfo-wide'] {
    /* The Po row label hangs 2px past the content box — the mock-up's
       `.polab` is bottom:5px of the 79px well, 6px under the Po bar's
       bottom edge — so the root SVG must not clip it. */
    overflow: visible;
  }
  /* The lit filter is the S track's alone — the Po row shares the
     geometry, not the glow; pinned by
     `__tests__/LinearSMeter.mockup-v8.test.ts` ("carries no keyframes and
     filters no Po fill"). */
  [data-meter-fill],
  [data-meter-fill-red] {
    filter: var(--v2-meter-lit-filter, none);
  }
</style>
