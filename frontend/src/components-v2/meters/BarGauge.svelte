<script lang="ts">
  import { onMount, untrack } from 'svelte';
  import type {
    MeterContinuitySession,
    MeterSourceIdentity,
  } from '../../primitives/meters/meter-ballistics.svelte';
  import { DEFAULT_ZONES, getSegmentZone, dimColor, valueFontSize } from './bar-gauge-utils';
  import type { Zone } from './bar-gauge-utils';
  import {
    createBarMeterMotion,
    type BarMeterFrame,
  } from './bar-meter-motion.svelte';

  interface CommonProps {
    label: string;         // 'Po' | 'SWR' | 'ALC' | 'COMP'
    displayValue: string;  // '35W' | '1.2' | '-8'
    accessibleDescription?: string;
    /**
     * Segment palette. MOR-2255 gave this prop a real writer:
     * `semantic/MetersSurface.svelte` passes the active design language's own
     * `MeterDisplay.zones`. The `DEFAULT_ZONES` default below stays and is not
     * dead — it is the "no design language active" fallback, the same role
     * `DEFAULT_METER_DISPLAY` (`./meter-display.ts`) plays for
     * `LinearSMeter.svelte`'s `display` prop.
     */
    zones?: readonly Zone[];
    compact?: boolean;
    fault?: boolean;       // MOR-1345: SWR/ALC over-threshold fault highlight
  }

  type LiveValueInput = {
    value: number | null;         // 0–1 normalized
    frame?: never;
    showPeak?: boolean;    // MOR-1282: optional peak-hold marker
    source?: MeterSourceIdentity | null;
    session?: MeterContinuitySession | null;
    onResetPeak?: never;
  };
  type HostedFrameInput = {
    frame: BarMeterFrame;
    value?: never;
    showPeak?: never;
    source?: never;
    session?: never;
    onResetPeak?: () => void;
  };
  type Props = CommonProps & (LiveValueInput | HostedFrameInput);

  type InputMode = 'value' | 'frame';

  function hasOwn(value: object, key: string): boolean {
    return Object.prototype.hasOwnProperty.call(value, key);
  }

  function resolveInputMode(current: Props): InputMode {
    const hasFrame = hasOwn(current, 'frame');
    const hasValue = hasOwn(current, 'value');
    if (Number(hasFrame) + Number(hasValue) !== 1) {
      throw new TypeError('BarGauge requires exactly one of frame or value');
    }
    if (hasFrame && current.frame === undefined) {
      throw new TypeError('BarGauge frame must be defined when supplied');
    }
    if (hasValue && current.value === undefined) {
      throw new TypeError('BarGauge value must be a number or null when supplied');
    }
    if (hasFrame && (hasOwn(current, 'source') || hasOwn(current, 'session') || hasOwn(current, 'showPeak'))) {
      throw new TypeError('BarGauge frame owns peak and continuity state');
    }
    return hasFrame ? 'frame' : 'value';
  }

  let props: Props = $props();
  const initialInputMode = untrack(() => resolveInputMode(props));
  const inputMode = $derived.by(() => {
    const current = resolveInputMode(props);
    if (current !== initialInputMode) {
      throw new TypeError('BarGauge input mode cannot change after mount');
    }
    return current;
  });
  const label = $derived(props.label);
  const displayValue = $derived(props.displayValue);
  const zones = $derived(props.zones ?? DEFAULT_ZONES);
  const compact = $derived(props.compact ?? false);
  const fault = $derived(props.fault ?? false);
  const accessibleDescription = $derived(props.accessibleDescription);
  const liveValue = $derived(inputMode === 'value' ? (props as LiveValueInput).value : null);

  // ── Segment geometry ────────────────────────────────────────────────────────
  const SEG_COUNT = 10;
  const BAR_X = 44;
  const BAR_WIDTH = 210;
  const SEG_GAP = 2;
  const SEG_W = (BAR_WIDTH - (SEG_COUNT - 1) * SEG_GAP) / SEG_COUNT; // 19.2

  const VALUE_X = BAR_X + BAR_WIDTH + 6; // 260

  function segX(i: number): number {
    return BAR_X + i * (SEG_W + SEG_GAP);
  }

  // ── Layout (switches between full / compact) ────────────────────────────────
  const TRACK_Y     = $derived(compact ? 6  : 8);
  const TRACK_H     = $derived(compact ? 10 : 14);
  const TOTAL_HEIGHT = $derived(compact ? 22 : 30);
  const LABEL_FS    = $derived(compact ? 7  : 8);
  // MOR-1535: steps down from the base size when `displayValue` is too long
  // to fit the fixed value column at that size (e.g. "158 raw") — SVG text
  // clips silently on overflow rather than wrapping or ellipsizing.
  const VALUE_FS    = $derived(valueFontSize(displayValue, compact ? 9 : 11));
  const TEXT_Y      = $derived(TRACK_Y + TRACK_H / 2);

  const localMotion = initialInputMode === 'value'
    ? untrack(() => createBarMeterMotion({
        value: (props as LiveValueInput).value,
        peakEnabled: (props as LiveValueInput).showPeak ?? false,
        source: (props as LiveValueInput).source,
        session: (props as LiveValueInput).session,
      }))
    : null;
  const meterFrame = $derived(
    inputMode === 'frame' ? (props as HostedFrameInput).frame : localMotion!.frame,
  );

  $effect(() => {
    if (localMotion === null) return;
    const currentValue = (props as LiveValueInput).value;
    const peakEnabled = (props as LiveValueInput).showPeak ?? false;
    const source = (props as LiveValueInput).source;
    const session = (props as LiveValueInput).session;
    untrack(() => localMotion.sync({
      value: currentValue,
      peakEnabled,
      source,
      session,
    }));
  });

  onMount(() => {
    if (localMotion === null) return;
    localMotion.start();
    return () => localMotion.stop();
  });

  // ── Peak-hold marker (MOR-1282) ─────────────────────────────────────────────
  let peakPct = $derived.by(() => {
    const level = meterFrame.peakFraction;
    if (level === null) return undefined;
    return Math.max(0, Math.min(100, level * 100));
  });

  function resetPeak() {
    if (inputMode === 'frame') {
      (props as HostedFrameInput).onResetPeak?.();
    } else if ((props as LiveValueInput).showPeak) {
      localMotion!.resetPeak();
    }
  }

  // ── Reactive display values ─────────────────────────────────────────────────
  const measuredFault = $derived((inputMode === 'frame' || liveValue !== null) && fault);
  let smoothedSegs = $derived(meterFrame.smoothedFraction * SEG_COUNT);
  let fullSegs = $derived(liveValue === null && inputMode === 'value' ? 0 : Math.floor(smoothedSegs));
  let fracSeg  = $derived(
    liveValue === null && inputMode === 'value'
      ? 0
      : smoothedSegs - Math.floor(smoothedSegs),
  );
</script>

<svg
  viewBox="0 0 300 {TOTAL_HEIGHT}"
  width="100%"
  height="auto"
  preserveAspectRatio="xMidYMid meet"
  role="group"
  aria-label={accessibleDescription}
  data-fault={measuredFault ? 'true' : 'false'}
  ondblclick={resetPeak}
>
  <!-- Container background. MOR-1345: an over-threshold SWR/ALC reading
       (the same `isSwrFault`/`isAlcFault` predicates the legacy dock's
       border comes from) swaps the outline for the shared red accent — the
       one place this gauge already owns colour (zone segments, above). -->
  <rect
    x="0" y="0" width="300" height={TOTAL_HEIGHT}
    rx="6"
    fill="var(--v2-bg-darkest)"
    stroke={measuredFault ? 'var(--v2-accent-red, #ff4040)' : 'var(--v2-bg-panel)'}
    stroke-width={measuredFault ? 2 : 1}
  />

  <!-- Label -->
  <text
    x="6"
    y={TEXT_Y}
    font-family="'Roboto Mono', monospace"
    font-size={LABEL_FS}
    font-weight="700"
    letter-spacing="0.8"
    fill="var(--v2-text-dim)"
    text-anchor="start"
    dominant-baseline="central"
  >{label}</text>

  <!-- Bar track background -->
  <rect
    data-gauge-track
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
    {@const zone = getSegmentZone(i, SEG_COUNT, zones)}

    <!-- Inactive (dim) -->
    <rect
      {x} y={TRACK_Y + 1}
      width={SEG_W} height={TRACK_H - 2}
      fill={dimColor(zone.color)}
    />

    <!-- Active -->
    {#if i < fullSegs}
      <rect
        data-gauge-fill={i}
        {x} y={TRACK_Y + 1}
        width={SEG_W} height={TRACK_H - 2}
        fill={zone.color}
      />
    {:else if i === fullSegs && fracSeg > 0.01}
      <rect
        data-gauge-fill={i}
        {x} y={TRACK_Y + 1}
        width={Math.max(1, SEG_W * fracSeg)} height={TRACK_H - 2}
        fill={zone.color}
      />
    {/if}
  {/each}

  <!-- Peak-hold marker (MOR-1282) -->
  {#if peakPct !== undefined}
    <rect
      x={BAR_X + (peakPct / 100) * BAR_WIDTH - 1}
      y={TRACK_Y}
      width="2" height={TRACK_H}
      fill="var(--v2-accent-yellow, #f2cf4a)"
      data-testid="bar-gauge-peak-marker"
    />
  {/if}

  <!-- Display value -->
  <text
    x={VALUE_X}
    y={TEXT_Y}
    font-family="'Roboto Mono', monospace"
    font-size={VALUE_FS}
    font-weight="700"
    fill="var(--v2-text-bright)"
    text-anchor="start"
    dominant-baseline="central"
  >{displayValue}</text>
</svg>

<style>
  svg {
    display: block;
  }
</style>
