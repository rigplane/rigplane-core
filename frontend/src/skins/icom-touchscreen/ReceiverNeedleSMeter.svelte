<script lang="ts">
  import type {
    DisplayFact,
    NeedleMeterReading,
    NeedleMeterTick,
  } from '../../semantic/icom-touchscreen-display-model';

  interface Props {
    state: DisplayFact<NeedleMeterReading>;
    ticks: readonly NeedleMeterTick[];
  }

  let { state, ticks }: Props = $props();
  const startAngle = -58;
  const sweepAngle = 116;
  let needleAngle = $derived(
    state.value === undefined ? null : startAngle + state.value.fraction * sweepAngle,
  );

  function tickPosition(fraction: number, radius: number): { x: number; y: number } {
    const angle = (startAngle + fraction * sweepAngle - 90) * Math.PI / 180;
    return { x: 120 + Math.cos(angle) * radius, y: 91 + Math.sin(angle) * radius };
  }
</script>

<svg
  class="needle-meter"
  viewBox="0 0 240 112"
  role="img"
  aria-label={`S meter ${state.status}`}
  data-meter-state={state.status}
  data-structural-spacer={state.status === 'unsupported' ? 'true' : undefined}
>
  <path d="M25 91 A96 96 0 0 1 215 91" class="arc" />
  {#each ticks as tick}
    {@const outer = tickPosition(tick.fraction, 95)}
    {@const inner = tickPosition(tick.fraction, 87)}
    {@const label = tickPosition(tick.fraction, 73)}
    <line
      x1={inner.x}
      y1={inner.y}
      x2={outer.x}
      y2={outer.y}
      class:warning={tick.emphasis === 'warning'}
    />
    <text
      x={label.x}
      y={label.y}
      text-anchor="middle"
      class:warning-label={tick.emphasis === 'warning'}
    >{tick.label}</text>
  {/each}
  {#if needleAngle !== null}
    <line
      data-needle
      x1="120"
      y1="91"
      x2="120"
      y2="24"
      transform={`rotate(${needleAngle} 120 91)`}
      class="needle"
    />
    <circle cx="120" cy="91" r="5" class="pivot" />
  {:else}
    <text data-meter-unavailable x="120" y="76" text-anchor="middle" class="unavailable">
      {state.status === 'unsupported' ? 'N/A' : '—'}
    </text>
  {/if}
  <text x="12" y="104" class="meter-label">S</text>
  {#if state.value !== undefined}
    <text data-meter-label x="228" y="104" text-anchor="end" class="meter-label">
      {state.value.label}
    </text>
  {/if}
</svg>

<style>
  .needle-meter {
    display: block;
    width: 100%;
    height: 100%;
    min-height: 0;
    color: #d7e3e1;
    background: #020707;
  }

  .needle-meter[data-meter-state='inactive'] { opacity: 0.48; }
  .needle-meter[data-meter-state='unsupported'] { opacity: 0.28; }

  .arc, line {
    fill: none;
    stroke: currentColor;
    stroke-width: 1.5;
  }

  .warning { stroke: #ec8560; }
  text { fill: currentColor; font: 10px ui-monospace, monospace; }
  .warning-label { font-size: 7.5px; fill: #ec8560; }

  .needle {
    stroke: #f5fbf9;
    stroke-width: 2;
    transform-origin: 120px 91px;
  }

  .pivot { fill: #b7c2c1; }
  .unavailable { font-size: 18px; fill: #7f9190; }
  .meter-label { font-size: 11px; fill: #aebcba; }
</style>
