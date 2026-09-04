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

  const radialTicks = (count: number, cx: number, cy: number, rx: number, ry: number): Tick[] =>
    Array.from({ length: count }, (_, index) => {
      const angle = (-160 + (135 * index) / (count - 1)) * Math.PI / 180;
      const major = index % 5 === 0;
      const inset = major ? 14 : 8;
      return {
        x1: cx + rx * Math.cos(angle),
        y1: cy + ry * Math.sin(angle),
        x2: cx + (rx - inset) * Math.cos(angle),
        y2: cy + (ry - inset * 0.6) * Math.sin(angle),
        major,
      };
    });

  const OUTER_TICKS = radialTicks(36, 320, 225, 304, 172);
  const POWER_TICKS = radialTicks(26, 320, 225, 281, 139);
  const INNER_TICKS = radialTicks(21, 320, 225, 238, 105);
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
        <feGaussianBlur stdDeviation="1.1" result="blur" />
        <feMerge><feMergeNode in="blur" /><feMergeNode in="SourceGraphic" /></feMerge>
      </filter>
      <linearGradient id="meter-needle" x1="0" x2="1">
        <stop offset="0" stop-color="#73cfff" />
        <stop offset="0.45" stop-color="#f5fbff" />
        <stop offset="1" stop-color="#ffffff" />
      </linearGradient>
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

      <path class="scale white heavy" d="M 12 110 Q 318 -38 626 102" />
      <path class="scale red heavy" d="M 322 24 Q 478 13 626 102" />
      {#each OUTER_TICKS as tick}
        <line
          class:major={tick.major}
          class="tick outer"
          x1={tick.x1} y1={tick.y1} x2={tick.x2} y2={tick.y2}
        />
      {/each}

      <g class="outer-labels">
        <text x="0" y="111">S</text>
        <text x="67" y="68">1</text>
        <text x="188" y="31">5</text>
        <text x="300" y="22">9</text>
        <text class="red-text" x="357" y="30">+20</text>
        <text class="red-text" x="451" y="45">+40</text>
        <text class="red-text" x="548" y="78">+60dB</text>
      </g>

      <path class="scale white" d="M 27 132 Q 318 -5 607 124" />
      {#each POWER_TICKS as tick}
        <line
          class:major={tick.major}
          class="tick power"
          x1={tick.x1} y1={tick.y1} x2={tick.x2} y2={tick.y2}
        />
      {/each}
      <g class="power-labels">
        <text x="54" y="136">0</text>
        <text x="198" y="101">10</text>
        <text x="356" y="96">50</text>
        <text x="493" y="124">100</text>
        <text x="592" y="139">W</text>
        <text x="42" y="151">Po</text>
      </g>

      <path class="scale white" d="M 84 178 Q 318 55 550 165" />
      {#each INNER_TICKS as tick}
        <line
          class:major={tick.major}
          class="tick inner"
          x1={tick.x1} y1={tick.y1} x2={tick.x2} y2={tick.y2}
        />
      {/each}
      <g class="inner-labels">
        <text x="75" y="185">SWR</text>
        <text x="151" y="165">1</text>
        <text x="205" y="149">1.5</text>
        <text x="294" y="141">2</text>
        <text x="374" y="143">3</text>
        <text x="486" y="163">∞</text>
      </g>

      <path class="scale blue heavy" d="M 126 210 Q 317 118 511 202" />
      <path class="scale red heavy" d="M 160 222 Q 255 167 337 169" />
      <path class="scale red heavy" d="M 334 169 Q 389 164 423 178" />
      <g class="lower-labels">
        <text class="blue-text" x="77" y="207">COMP</text>
        <text class="blue-text" x="150" y="190">0</text>
        <text class="blue-text" x="336" y="174">20</text>
        <text class="blue-text" x="505" y="205">dB</text>
        <text class="red-text" x="261" y="220">ALC</text>
        <text x="333" y="225">Id</text>
        <text x="384" y="225">Vd</text>
      </g>

      {#if state === 'known'}
        <g data-meter-pointer transform={`rotate(${needleAngle} 280 224)`}>
          <line class="needle-shadow" x1="274" y1="226" x2="490" y2="226" />
          <line class="needle" x1="274" y1="224" x2="490" y2="224" />
        </g>
        <circle cx="280" cy="224" r="6" fill="#e8f6ff" />
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
  .tick { stroke: #f7fafc; stroke-width: 2; }
  .tick.major { stroke-width: 4; }
  .outer-labels text { font-size: 22px; }
  .power-labels text, .inner-labels text { font-size: 15px; }
  .lower-labels text { font-size: 14px; }
  text.red-text { fill: #ff2f2f; }
  text.blue-text { fill: #2daaff; }
  .needle-shadow { stroke: #163d54; stroke-width: 7; opacity: 0.8; }
  .needle { stroke: url(#meter-needle); stroke-width: 3; }
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
