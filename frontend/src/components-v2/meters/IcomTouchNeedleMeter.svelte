<script module lang="ts">
  export type IcomTouchMeterScale = 'S' | 'Po' | 'SWR' | 'ALC' | 'COMP' | 'Id' | 'Vd';
</script>

<script lang="ts">
  interface Props {
    /** Profile-calibrated, domain-free position on the selected scale. */
    value: number | null;
    /** Caller-formatted reading; the component never derives engineering units. */
    displayValue: string | null;
    selectedScale?: IcomTouchMeterScale;
    label?: string;
    structural?: boolean;
    operational?: boolean;
    relevant?: boolean;
  }

  let {
    value,
    displayValue,
    selectedScale = 'Po',
    label = 'MET',
    structural = true,
    operational = true,
    relevant = true,
  }: Props = $props();

  type State = 'known' | 'unknown' | 'unsupported';
  const finiteValue = $derived(value !== null && Number.isFinite(value));
  const state = $derived<State>(!structural ? 'unsupported' : operational && finiteValue ? 'known' : 'unknown');
  const fraction = $derived(finiteValue ? Math.min(1, Math.max(0, value as number)) : 0);
  const needleAngle = $derived(-160 + fraction * 135);

  interface Tick {
    readonly x1: number;
    readonly y1: number;
    readonly x2: number;
    readonly y2: number;
    readonly major: boolean;
  }

  interface Point { readonly x: number; readonly y: number }
  interface Curve { readonly start: Point; readonly control: Point; readonly end: Point }
  interface ScaleLabel extends Point {
    readonly text: string;
    readonly accent?: 'red';
  }

  const OUTER_CURVE: Curve = {
    start: { x: 3, y: 112 }, control: { x: 300, y: -12 }, end: { x: 600, y: 112 },
  };
  const POWER_CURVE: Curve = {
    start: { x: 22, y: 128 }, control: { x: 305, y: 12 }, end: { x: 590, y: 126 },
  };
  const INNER_CURVE: Curve = {
    start: { x: 78, y: 158 }, control: { x: 310, y: 55 }, end: { x: 545, y: 154 },
  };

  const curveTicks = (
    count: number, { start, control, end }: Curve, majorEvery: number,
  ): Tick[] =>
    Array.from({ length: count }, (_, index) => {
      const t = (index + 0.5) / count;
      const inverse = 1 - t;
      const x1 = inverse * inverse * start.x + 2 * inverse * t * control.x + t * t * end.x;
      const y1 = inverse * inverse * start.y + 2 * inverse * t * control.y + t * t * end.y;
      const dx = 2 * inverse * (control.x - start.x) + 2 * t * (end.x - control.x);
      const dy = 2 * inverse * (control.y - start.y) + 2 * t * (end.y - control.y);
      const magnitude = Math.hypot(dx, dy);
      const major = index % majorEvery === 0;
      const inset = major ? 13 : 7;
      return {
        x1,
        y1,
        x2: x1 + (-dy / magnitude) * inset,
        y2: y1 + (dx / magnitude) * inset,
        major,
      };
    });

  const curveLabel = (
    text: string, curve: Curve, t: number, offset: number, side: 'outside' | 'inside',
    accent?: 'red',
  ): ScaleLabel => {
    const inverse = 1 - t;
    const x = inverse * inverse * curve.start.x
      + 2 * inverse * t * curve.control.x + t * t * curve.end.x;
    const y = inverse * inverse * curve.start.y
      + 2 * inverse * t * curve.control.y + t * t * curve.end.y;
    const dx = 2 * inverse * (curve.control.x - curve.start.x)
      + 2 * t * (curve.end.x - curve.control.x);
    const dy = 2 * inverse * (curve.control.y - curve.start.y)
      + 2 * t * (curve.end.y - curve.control.y);
    const magnitude = Math.hypot(dx, dy);
    const direction = side === 'outside' ? 1 : -1;
    return {
      text,
      x: x + direction * (dy / magnitude) * offset,
      // Six pixels account for the text baseline while keeping every glyph
      // center at the same normal distance from its own curved scale.
      y: y - direction * (dx / magnitude) * offset + 6,
      accent,
    };
  };

  const OUTER_TICKS = curveTicks(46, OUTER_CURVE, 5);
  const POWER_TICKS = curveTicks(36, POWER_CURVE, 5);
  const INNER_TICKS = curveTicks(29, INNER_CURVE, 4);
  const OUTER_LABELS = [
    curveLabel('1', OUTER_CURVE, 0.16, 30, 'outside'),
    curveLabel('5', OUTER_CURVE, 0.32, 30, 'outside'),
    curveLabel('9', OUTER_CURVE, 0.50, 30, 'outside'),
    curveLabel('+20', OUTER_CURVE, 0.61, 30, 'outside', 'red'),
    curveLabel('+40', OUTER_CURVE, 0.77, 30, 'outside', 'red'),
    curveLabel('+60dB', OUTER_CURVE, 0.94, 30, 'outside', 'red'),
  ];
  const POWER_LABELS = [
    curveLabel('0', POWER_CURVE, 0.11, 18, 'inside'),
    curveLabel('10', POWER_CURVE, 0.33, 18, 'inside'),
    curveLabel('50', POWER_CURVE, 0.57, 18, 'inside'),
    curveLabel('100', POWER_CURVE, 0.79, 18, 'inside'),
    curveLabel('W', POWER_CURVE, 0.96, 18, 'inside'),
  ];
  const INNER_LABELS = [
    curveLabel('1', INNER_CURVE, 0.17, 20, 'inside'),
    curveLabel('1.5', INNER_CURVE, 0.30, 20, 'inside'),
    curveLabel('2', INNER_CURVE, 0.43, 20, 'inside'),
    curveLabel('3', INNER_CURVE, 0.56, 20, 'inside'),
    curveLabel('∞', INNER_CURVE, 0.80, 20, 'inside'),
  ];
</script>

<div
  class="meter-shell"
  data-testid="icom-touch-needle-meter"
  data-state={state}
  data-selected-scale={selectedScale}
  data-relevant={relevant ? 'true' : 'false'}
  role={state === 'unsupported' ? undefined : 'img'}
  aria-hidden={state === 'unsupported' ? 'true' : undefined}
  aria-label={state === 'known'
    ? `${label} ${selectedScale}: ${displayValue ?? 'reading available'}`
    : state === 'unknown' ? `${label} ${selectedScale}: reading unavailable` : undefined}
>
  <svg viewBox="0 0 640 240" width="100%" height="100%" preserveAspectRatio="none">
    <defs>
      <filter id="meter-soft-glow" x="-20%" y="-20%" width="140%" height="140%">
        <feGaussianBlur in="SourceGraphic" stdDeviation="1.35" result="bloom" />
        <feComponentTransfer in="bloom" result="soft-bloom">
          <feFuncA type="linear" slope="0.58" />
        </feComponentTransfer>
        <feGaussianBlur in="SourceGraphic" stdDeviation="0.22" result="soft-core" />
        <feMerge><feMergeNode in="soft-bloom" /><feMergeNode in="soft-core" /></feMerge>
      </filter>
    </defs>

    <rect width="640" height="240" fill="#020303" />
    <g
      data-meter-artwork
      visibility={state === 'unsupported' ? 'hidden' : 'visible'}
      opacity={relevant ? 1 : 0.42}
      filter="url(#meter-soft-glow)"
    >
      <g class="selector">
        <text x="2" y="18">{label}</text>
        <rect x="52" y="1" width="108" height="24" rx="3" />
        <text x="106" y="18" text-anchor="middle">{selectedScale}</text>
      </g>

      <path class="scale white heavy" d="M 3 112 Q 300 -12 600 112" />
      <path class="scale red heavy" d="M 316 50 Q 458 53 600 112" />
      {#each OUTER_TICKS as tick}
        <line
          class:major={tick.major}
          class="tick outer"
          x1={tick.x1} y1={tick.y1} x2={tick.x2} y2={tick.y2}
        />
      {/each}

      <g class="outer-labels">
        <text x="0" y="111">S</text>
        {#each OUTER_LABELS as mark}
          <text class:red-text={mark.accent === 'red'} x={mark.x} y={mark.y} text-anchor="middle">{mark.text}</text>
        {/each}
      </g>

      <path class="scale white" d="M 22 128 Q 305 12 590 126" />
      {#each POWER_TICKS as tick}
        <line
          class:major={tick.major}
          class="tick power"
          x1={tick.x1} y1={tick.y1} x2={tick.x2} y2={tick.y2}
        />
      {/each}
      <g class="power-labels">
        {#each POWER_LABELS as mark}
          <text x={mark.x} y={mark.y} text-anchor="middle">{mark.text}</text>
        {/each}
        <text x="68" y="151">Po</text>
      </g>

      <path class="scale white" d="M 78 158 Q 310 55 545 154" />
      {#each INNER_TICKS as tick}
        <line
          class:major={tick.major}
          class="tick inner"
          x1={tick.x1} y1={tick.y1} x2={tick.x2} y2={tick.y2}
        />
      {/each}
      <path class="scale white" d="M 100 174 Q 310 84 520 168" />
      <g class="inner-labels">
        <text x="92" y="190">SWR</text>
        {#each INNER_LABELS as mark}
          <text x={mark.x} y={mark.y} text-anchor="middle">{mark.text}</text>
        {/each}
      </g>

      <path class="scale blue heavy" d="M 155 210 Q 310 145 465 207" />
      <path class="scale red heavy" d="M 198 212 Q 230 191 262 198" />
      <path class="scale red heavy" d="M 272 200 Q 295 189 318 196" />
      <g class="lower-labels">
        <text class="blue-text" x="92" y="216">COMP</text>
        <text class="blue-text" x="174" y="191">0</text>
        <text class="blue-text" x="350" y="177">20</text>
        <text class="blue-text" x="462" y="202">dB</text>
        <text class="red-text" x="258" y="230">ALC</text>
        <text x="334" y="230">Id</text>
        <text x="386" y="230">Vd</text>
      </g>

      {#if state === 'known'}
        <g data-meter-pointer transform={`rotate(${needleAngle} 280 224)`}>
          <line class="needle-shadow" x1="274" y1="226" x2="490" y2="226" />
          <line class="needle" x1="274" y1="224" x2="490" y2="224" />
        </g>
        <circle cx="280" cy="224" r="3.5" fill="#f6fbff" />
      {:else if state === 'unknown'}
        <text data-meter-unknown class="unknown" x="320" y="213" text-anchor="middle">?</text>
      {/if}
    </g>
  </svg>

  {#if state === 'known' && displayValue !== null}
    <span class="visually-hidden" data-meter-display-value>{displayValue}</span>
  {/if}
</div>

<style>
  .meter-shell {
    position: relative;
    display: block;
    width: 100%;
    aspect-ratio: 8 / 3;
    overflow: hidden;
    background: #020303;
    color: #f8fbff;
    font-family: "Arial Narrow", "Roboto Condensed", sans-serif;
  }

  svg { display: block; }
  text { fill: #f8fbff; font-size: 17px; font-weight: 700; }
  .selector text { font-size: 20px; letter-spacing: 0.5px; }
  .selector rect { fill: transparent; stroke: #f5f8fa; stroke-width: 3; }
  .scale { fill: none; stroke-linecap: square; stroke-width: 3; }
  .scale.heavy { stroke-width: 5; }
  .white { stroke: #f7fafc; }
  .red { stroke: #ff2f2f; }
  .blue { stroke: #24a6ff; }
  .tick { stroke: #f7fafc; stroke-width: 1.4; }
  .tick.major { stroke-width: 3; }
  .outer-labels text { font-size: 22px; }
  .power-labels text, .inner-labels text { font-size: 15px; }
  .lower-labels text { font-size: 14px; }
  text.red-text { fill: #ff2f2f; }
  text.blue-text { fill: #2daaff; }
  .needle-shadow { stroke: #72a9c6; stroke-width: 3.2; opacity: 0.32; }
  .needle { stroke: #f7fbff; stroke-width: 2.4; }
  .unknown { fill: #9ca7ad; font-size: 34px; }
  .visually-hidden {
    position: absolute;
    width: 1px;
    height: 1px;
    padding: 0;
    margin: -1px;
    overflow: hidden;
    clip: rect(0, 0, 0, 0);
    white-space: nowrap;
    border: 0;
  }
</style>
