<!--
  SdrVfoScreen — IC-7610-style dual VFO + central bridge, LCD/"SDR screen" look.

  Drop-in visual replacement for VfoHeader in the sdr-test skin. Consumes the
  same VfoStateProps as VfoHeader so it plugs directly into the existing
  runtime wiring (state-adapter + command-bus). Visual design ported from
  Claude Design handoff "VFO SDR Screen" (2026-04-20).

  Wired live: freq, mode, filter, active receiver, TX/RX, split, dual-watch,
  sValue, band label, SPLIT/A⇄B/A=B/M/S/SPEAK callbacks.
  Static placeholders: ANT/BW/SFT chips, DSP badges, RIT/XIT offsets, SWR —
  reserved for later wiring once the prototype is accepted.
-->
<script lang="ts">
  import { formatFrequency } from '../../components-v2/display/frequency-format';
  import type { VfoStateProps } from '../../components-v2/layout/layout-utils';
  import { HardwareButton } from '$lib/Button';

  /**
   * Subset of ServerState we read for per-receiver chips & badges.
   * Loosely typed — all fields optional, missing ones fall back to safe defaults.
   */
  interface VfoExtras {
    tunerStatus?: number;          // 0=off, 1=tuned, 2=tuning
    xitActive?: boolean;
    xitOffset?: number;            // Hz
    txAntenna?: number;            // ANT index (1-based in IC-7610)
    // Per-receiver raw state (for att/preamp/nb/nr/notch/digisel/ipplus/filterWidth/ifShift/levels)
    main?: Record<string, unknown> | null;
    sub?: Record<string, unknown> | null;
  }

  interface Props {
    mainVfo: VfoStateProps;
    subVfo: VfoStateProps;
    splitActive: boolean;
    dualWatchActive: boolean;
    txVfo: 'main' | 'sub';
    extras?: VfoExtras;
    onSwap?: () => void;
    onEqual?: () => void;
    onSplitToggle?: () => void;
    onDualWatchToggle?: (on: boolean) => void;
    onMainVfoClick?: () => void;
    onSubVfoClick?: () => void;
    onMainModeClick?: () => void;
    onSubModeClick?: () => void;
    onSpeak?: () => void;
  }

  let {
    mainVfo,
    subVfo,
    splitActive,
    dualWatchActive,
    txVfo,
    extras = {},
    onSwap = () => {},
    onEqual = () => {},
    onSplitToggle = () => {},
    onDualWatchToggle = (_on: boolean) => {},
    onMainVfoClick = () => {},
    onSubVfoClick = () => {},
    onMainModeClick = () => {},
    onSubModeClick = () => {},
    onSpeak = () => {},
  }: Props = $props();

  // ── Per-receiver derived state for chips/badges ──────────────────────────
  function num(rx: Record<string, unknown> | null | undefined, k: string, fallback = 0): number {
    const v = rx?.[k];
    return typeof v === 'number' ? v : fallback;
  }
  function bool(rx: Record<string, unknown> | null | undefined, k: string): boolean {
    return !!rx?.[k];
  }
  function formatBw(hz: number): string {
    if (!hz) return '—';
    if (hz >= 1000) return `${(hz / 1000).toFixed(hz % 1000 === 0 ? 0 : 1)}k`;
    return String(hz);
  }
  // IC-7610 ATT levels (dB): 0 / 12 / 18 / 24
  const ATT_DB = [0, 12, 18, 24];
  function attLabel(rx: Record<string, unknown> | null | undefined): string {
    const a = num(rx, 'att');
    const db = ATT_DB[a] ?? a;
    return db > 0 ? `ATT ${db}dB` : 'ATT';
  }
  function preampLabel(rx: Record<string, unknown> | null | undefined): string {
    const p = num(rx, 'preamp');
    return p > 0 ? `P.AMP ${p}` : 'P.AMP';
  }
  function nbLabel(rx: Record<string, unknown> | null | undefined): string {
    const on = bool(rx, 'nb');
    if (!on) return 'NB';
    const lvl = num(rx, 'nbLevel');
    return lvl > 0 ? `NB ${lvl}` : 'NB';
  }
  function notchOn(rx: Record<string, unknown> | null | undefined): boolean {
    return bool(rx, 'manualNotch') || bool(rx, 'autoNotch');
  }
  function xitFmt(hz: number): string {
    const khz = hz / 1000;
    const sign = khz > 0 ? '+' : khz < 0 ? '' : '+';
    return `${sign}${khz.toFixed(2)} kHz`;
  }
  // IC-7610 AGC: 1=FAST, 2=MID, 3=SLOW (per capabilities.agcLabels default)
  const AGC_LABELS: Record<number, string> = { 1: 'FAST', 2: 'MID', 3: 'SLOW' };
  function agcLabel(rx: Record<string, unknown> | null | undefined): string {
    const m = num(rx, 'agc');
    return AGC_LABELS[m] ? `AGC ${AGC_LABELS[m]}` : 'AGC';
  }
  // rfGain is raw 0..255 — convert to 0..100 for display
  function rfgPercent(rx: Record<string, unknown> | null | undefined): number {
    const raw = num(rx, 'rfGain', 255);
    return Math.round((raw / 255) * 100);
  }
  function rfgActive(rx: Record<string, unknown> | null | undefined): boolean {
    // Active = reduced from max (255 = 100% = off/neutral)
    return num(rx, 'rfGain', 255) < 255;
  }

  // Band label (e.g., "20M") derived from freq MHz — coarse, ham-band only.
  function bandOf(freqHz: number): string {
    const mhz = freqHz / 1_000_000;
    if (mhz >= 1.8 && mhz < 2)   return '160M';
    if (mhz >= 3.5 && mhz < 4)   return '80M';
    if (mhz >= 5.2 && mhz < 5.5) return '60M';
    if (mhz >= 7 && mhz < 7.4)   return '40M';
    if (mhz >= 10 && mhz < 10.2) return '30M';
    if (mhz >= 14 && mhz < 14.4) return '20M';
    if (mhz >= 18 && mhz < 18.2) return '17M';
    if (mhz >= 21 && mhz < 21.5) return '15M';
    if (mhz >= 24.8 && mhz < 25) return '12M';
    if (mhz >= 28 && mhz < 30)   return '10M';
    if (mhz >= 50 && mhz < 54)   return '6M';
    return `${mhz.toFixed(1)}MHz`;
  }

  // sMeter is RAW 0..255: S9 ≈ 120, S9+60dB ≈ 241 (IC-7610 calibration).
  // Map to 0..80 sub-segments: S0..S9 → 0..42, S9..S9+60 → 42..80.
  const S9_RAW = 120;
  const S9P60_RAW = 241;
  function sMeterFill(raw: number): number {
    if (!raw || raw < 0) return 0;
    if (raw <= S9_RAW) return Math.round((raw / S9_RAW) * 42);
    const over = Math.min(raw - S9_RAW, S9P60_RAW - S9_RAW);
    return Math.round(42 + (over / (S9P60_RAW - S9_RAW)) * 38);
  }
  function sUnitLabel(raw: number): string {
    if (!raw || raw < 0) return '0';
    if (raw <= S9_RAW) return String(Math.round((raw / S9_RAW) * 9));
    const db = Math.round(((raw - S9_RAW) / (S9P60_RAW - S9_RAW)) * 60);
    return `9+${db}`;
  }

  const SEGS = 40;
  const SUB_PER_SEG = 2;

  let mainFreqParts = $derived(formatFrequency(mainVfo.freq));
  let subFreqParts  = $derived(formatFrequency(subVfo.freq));
  let mainBand      = $derived(bandOf(mainVfo.freq));
  let subBand       = $derived(bandOf(subVfo.freq));
  let mainFill      = $derived(sMeterFill(mainVfo.sValue));
  let subFill       = $derived(sMeterFill(subVfo.sValue));
  let mainIsTx      = $derived(txVfo === 'main');
  let subIsTx       = $derived(txVfo === 'sub');

  let mainRx = $derived(extras.main ?? null);
  let subRx  = $derived(extras.sub ?? null);
  let antNum = $derived(extras.txAntenna ?? 1);
  // IC-7610 tunerStatus: 0=off, 1=on/tuned, 2=tuning in progress. Light the
  // badge whenever the tuner is enabled (1 or 2), not just while tuning.
  let tuneOn = $derived((extras.tunerStatus ?? 0) > 0);
  let xitActive = $derived(extras.xitActive ?? false);
  let xitOffset = $derived(extras.xitOffset ?? 0);
  let ritActive = $derived(mainVfo.rit?.active ?? false);
  let ritOffset = $derived(mainVfo.rit?.offset ?? 0);

  // Segment-bar geometry (mirrors design populateSegBars())
  const X0 = 14, BAR_W = 328, CELL = BAR_W / SEGS;
  const INNER_GAP = 0.5, OUTER_GAP = 2.0;
  const SUB_W = (CELL - OUTER_GAP - INNER_GAP) / 2;

  function subBarX(seg: number, sub: number): number {
    return X0 + seg * CELL + sub * (SUB_W + INNER_GAP);
  }

  function subBarFill(idx: number, fill: number, dim: boolean): string {
    const on = idx < fill;
    const overload = idx >= 42;
    if (dim) return on ? '#3a4654' : '#141a22';
    if (on) return overload ? '#FF3030' : '#4FB9EC';
    return overload ? '#2a1618' : '#1a2230';
  }
</script>

<div class="sdr-host" role="group" aria-label="VFO display">
  <div class="sdr-panel">
    <!-- MAIN VFO -->
    <div class="vfo main" data-side="main" data-split={splitActive ? 'on' : 'off'}>
      <div class="topline">
        <div class="left">
          <span class="txrx-pill" class:tx={mainIsTx} class:rx={!mainIsTx}>
            {mainIsTx ? 'TX' : 'RX'}
          </span>
          <span class="ant">ANT <b>{antNum}</b></span>
          <span class="bw-chip">BW <b>{formatBw(num(mainRx, 'filterWidth'))}</b></span>
          {#if num(mainRx, 'ifShift') !== 0}
            <span class="bw-chip">SFT <b>{num(mainRx, 'ifShift') > 0 ? '+' : ''}{num(mainRx, 'ifShift')}</b></span>
          {/if}
        </div>
        <div class="right">
          <span>MAIN · {mainBand}</span>
        </div>
      </div>

      <div class="smeter-linear" class:dimmed={!mainVfo.isActive}>
        <svg viewBox="0 0 420 50" preserveAspectRatio="none">
          <g font-family="Roboto Mono" font-size="11" fill="#C8D4E0" font-weight="700">
            <text x="4" y="14">S</text>
            <text x="58" y="14" text-anchor="middle">1</text>
            <text x="98" y="14" text-anchor="middle">3</text>
            <text x="138" y="14" text-anchor="middle">5</text>
            <text x="178" y="14" text-anchor="middle">7</text>
            <text x="218" y="14" text-anchor="middle">9</text>
            <text x="252" y="14" text-anchor="middle" fill="#FF4040">+20</text>
            <text x="286" y="14" text-anchor="middle" fill="#FF4040">+40</text>
            <text x="320" y="14" text-anchor="middle" fill="#FF4040">+60</text>
          </g>
          <g>
            {#each Array(SEGS) as _, i}
              {#each Array(SUB_PER_SEG) as _, s}
                <rect
                  x={subBarX(i, s).toFixed(2)}
                  y="22"
                  width={SUB_W.toFixed(2)}
                  height="18"
                  fill={subBarFill(i * SUB_PER_SEG + s, mainFill, false)}
                />
              {/each}
            {/each}
          </g>
        </svg>
        <div class="overlay"><span class="read">S {sUnitLabel(mainVfo.sValue)}</span></div>
      </div>

      <div class="freq-stack">
        <div class="info-row">
          <div class="mode-info">
            <div class="mode-row vfo-row">
              <span class="mode-label">VFO</span>
              <span class="mode-value">A</span>
              <button
                type="button"
                class="mode-chip"
                onclick={onMainModeClick}
              >{mainVfo.mode}</button>
              <span class="mode-label" style="margin-left:4px;">FIL</span>
              <span class="mode-value">{mainVfo.filter.replace(/^FIL/, '')}</span>
            </div>
            <div class="mode-row">
              <span class="status-chip green" class:on={dualWatchActive}>DUAL-W</span>
            </div>
            <div class="mode-row">
              <span class="status-chip red" class:on={tuneOn}>TUNE</span>
            </div>
            <div class="mode-row">
              <span class="status-chip cyan" class:on={num(mainRx, 'agc') > 0}>{agcLabel(mainRx)}</span>
            </div>
          </div>
          <div class="aux-info">
            <div class="aux-row rit" class:on={ritActive}>
              <span class="aux-label">RIT</span>
              <span class="aux-value">{xitFmt(ritOffset)}</span>
            </div>
            <div class="aux-row xit" class:on={xitActive}>
              <span class="aux-label">XIT</span>
              <span class="aux-value">{xitFmt(xitOffset)}</span>
            </div>
            <div class="aux-row split" class:on={splitActive}>
              <span class="aux-label">SPLIT TX</span>
              <span class="aux-value">
                {#if splitActive}
                  <span>{subFreqParts.mhz}</span><span class="dot">.</span><span>{subFreqParts.khz}</span><span class="dot">.</span><span>{subFreqParts.hz}</span>
                {:else}
                  —
                {/if}
              </span>
            </div>
          </div>
        </div>
        <!-- svelte-ignore a11y_click_events_have_key_events -->
        <!-- svelte-ignore a11y_no_static_element_interactions -->
        <div class="freq-wrap" onclick={onMainVfoClick}>
          <span class="freq" class:active={mainVfo.isActive} class:inactive={!mainVfo.isActive}>
            <span>{mainFreqParts.mhz}</span><span class="dot">.</span><span>{mainFreqParts.khz}</span><span class="dot">.</span><span>{mainFreqParts.hz}</span>
          </span>
          <span class="unit">MHz</span>
        </div>
      </div>

      <div class="status-strip">
        <span class="badge-group">
          <span class="badge red" class:on={num(mainRx, 'att') > 0}>{attLabel(mainRx)}</span>
          <span class="badge green" class:on={num(mainRx, 'preamp') > 0}>{preampLabel(mainRx)}</span>
          <span class="badge cyan" class:on={bool(mainRx, 'ipplus')}>IP+</span>
        </span>
        <span class="badge amber" class:on={bool(mainRx, 'nb')}>{nbLabel(mainRx)}</span>
        <span class="badge amber" class:on={bool(mainRx, 'nr')}>NR</span>
        <span class="badge amber" class:on={notchOn(mainRx)}>NOTCH</span>
        <span class="badge green" class:on={bool(mainRx, 'digisel')}>DIGI-SEL</span>
        <span class="badge amber" class:on={rfgActive(mainRx)}>RFG {rfgPercent(mainRx)}</span>
      </div>
    </div>

    <!-- BRIDGE -->
    <div class="bridge">
      <div class="b-title">— Dual —</div>
      <div class="row">
        <HardwareButton
          active={mainVfo.isActive}
          indicator="edge-left"
          color="cyan"
          onclick={onMainVfoClick}
        >MAIN</HardwareButton>
        <HardwareButton
          active={subVfo.isActive}
          indicator="edge-left"
          color="orange"
          onclick={onSubVfoClick}
        >SUB</HardwareButton>
      </div>

      <div class="row">
        <HardwareButton indicator="edge-left" color="cyan" onclick={onSwap} title="Swap VFOs">A⇄B</HardwareButton>
        <HardwareButton indicator="edge-left" color="cyan" onclick={onEqual} title="Copy A to B">A=B</HardwareButton>
      </div>

      <div class="sep"></div>

      <div class="row single">
        <HardwareButton
          active={splitActive}
          indicator="edge-left"
          color={splitActive ? 'amber' : 'gray'}
          onclick={onSplitToggle}
        >SPLIT</HardwareButton>
      </div>
      <div class="row single">
        <HardwareButton
          active={dualWatchActive}
          indicator="edge-left"
          color={dualWatchActive ? 'green' : 'gray'}
          onclick={() => onDualWatchToggle(!dualWatchActive)}
        >DUAL-W</HardwareButton>
      </div>

      <div class="sep"></div>

      <button type="button" class="speak" onclick={onSpeak} title="Speak frequency aloud">🔈 SPEAK</button>
    </div>

    <!-- SUB VFO -->
    <div class="vfo sub" data-side="sub">
      <div class="topline">
        <div class="left">
          <span class="txrx-pill" class:tx={subIsTx} class:rx={!subIsTx}>
            {subIsTx ? 'TX' : 'RX'}
          </span>
          <span class="ant">ANT <b>{antNum}</b></span>
          <span class="bw-chip">BW <b>{formatBw(num(subRx, 'filterWidth'))}</b></span>
          {#if num(subRx, 'ifShift') !== 0}
            <span class="bw-chip">SFT <b>{num(subRx, 'ifShift') > 0 ? '+' : ''}{num(subRx, 'ifShift')}</b></span>
          {/if}
        </div>
        <div class="right">
          <span>SUB · {subBand}</span>
        </div>
      </div>

      <div class="smeter-linear" class:dimmed={!subVfo.isActive}>
        <svg viewBox="0 0 420 50" preserveAspectRatio="none">
          <g font-family="Roboto Mono" font-size="11" fill="#6a7a8c" font-weight="700">
            <text x="4" y="14">S</text>
            <text x="58" y="14" text-anchor="middle">1</text>
            <text x="98" y="14" text-anchor="middle">3</text>
            <text x="138" y="14" text-anchor="middle">5</text>
            <text x="178" y="14" text-anchor="middle">7</text>
            <text x="218" y="14" text-anchor="middle">9</text>
            <text x="252" y="14" text-anchor="middle">+20</text>
            <text x="286" y="14" text-anchor="middle">+40</text>
            <text x="320" y="14" text-anchor="middle">+60</text>
          </g>
          <g>
            {#each Array(SEGS) as _, i}
              {#each Array(SUB_PER_SEG) as _, s}
                <rect
                  x={subBarX(i, s).toFixed(2)}
                  y="22"
                  width={SUB_W.toFixed(2)}
                  height="18"
                  fill={subBarFill(i * SUB_PER_SEG + s, subFill, !subVfo.isActive)}
                />
              {/each}
            {/each}
          </g>
        </svg>
        <div class="overlay"><span class="read" style="color:var(--v2-text-muted); text-shadow:none;">S {sUnitLabel(subVfo.sValue)}</span></div>
      </div>

      <div class="freq-stack">
        <div class="info-row">
          <div class="mode-info">
            <div class="mode-row vfo-row">
              <span class="mode-label">VFO</span>
              <span class="mode-value">B</span>
              <button
                type="button"
                class="mode-chip"
                onclick={onSubModeClick}
              >{subVfo.mode}</button>
              <span class="mode-label" style="margin-left:4px;">FIL</span>
              <span class="mode-value">{subVfo.filter.replace(/^FIL/, '')}</span>
            </div>
            <div class="mode-row">
              <span class="status-chip green" class:on={dualWatchActive}>DUAL-W</span>
            </div>
            <div class="mode-row">
              <span class="status-chip red" class:on={tuneOn}>TUNE</span>
            </div>
            <div class="mode-row">
              <span class="status-chip cyan" class:on={num(subRx, 'agc') > 0}>{agcLabel(subRx)}</span>
            </div>
          </div>
          <div class="aux-info">
            <div class="aux-row rit"><span class="aux-label">RIT</span><span class="aux-value">+0.00 kHz</span></div>
            <div class="aux-row xit"><span class="aux-label">XIT</span><span class="aux-value">+0.00 kHz</span></div>
            <div class="aux-row split"><span class="aux-label">SPLIT TX</span><span class="aux-value">—</span></div>
          </div>
        </div>
        <!-- svelte-ignore a11y_click_events_have_key_events -->
        <!-- svelte-ignore a11y_no_static_element_interactions -->
        <div class="freq-wrap" onclick={onSubVfoClick}>
          <span class="freq" class:active={subVfo.isActive} class:inactive={!subVfo.isActive}>
            <span>{subFreqParts.mhz}</span><span class="dot">.</span><span>{subFreqParts.khz}</span><span class="dot">.</span><span>{subFreqParts.hz}</span>
          </span>
          <span class="unit">MHz</span>
        </div>
      </div>

      <div class="status-strip">
        <span class="badge-group">
          <span class="badge red" class:on={num(subRx, 'att') > 0}>{attLabel(subRx)}</span>
          <span class="badge green" class:on={num(subRx, 'preamp') > 0}>{preampLabel(subRx)}</span>
          <span class="badge cyan" class:on={bool(subRx, 'ipplus')}>IP+</span>
        </span>
        <span class="badge amber" class:on={bool(subRx, 'nb')}>{nbLabel(subRx)}</span>
        <span class="badge amber" class:on={bool(subRx, 'nr')}>NR</span>
        <span class="badge amber" class:on={notchOn(subRx)}>NOTCH</span>
        <span class="badge green" class:on={bool(subRx, 'digisel')}>DIGI-SEL</span>
        <span class="badge amber" class:on={rfgActive(subRx)}>RFG {rfgPercent(subRx)}</span>
      </div>
    </div>
  </div>
</div>

<style>
  :global(.sdr-host) {
    --btn-min-height: 28px;
    --btn-padding-block: 4px;
    --btn-padding-inline: 10px;
    --btn-border-radius: 3px;
    --btn-font-size: 10px;
    --btn-font-weight: 700;
    --btn-letter-spacing: 0.04em;
    --btn-compact-min-height: 22px;
    --btn-compact-padding-block: 2px;
    --btn-compact-padding-inline: 6px;
    --btn-compact-font-size: 9px;

    --ctrl-top: rgba(18, 24, 33, 0.98);
    --ctrl-bot: rgba(11, 16, 23, 0.98);
    --ctrl-hl: rgba(255, 255, 255, 0.03);
    --hw-top-1: color-mix(in srgb, var(--v2-text-white, #fff) 10%, var(--ctrl-top));
    --hw-top-2: color-mix(in srgb, var(--v2-text-white, #fff) 4%, var(--ctrl-top));
    --hw-mid:   color-mix(in srgb, var(--v2-bg-card, #0E1420) 28%, var(--ctrl-bot));
    --hw-bot:   color-mix(in srgb, var(--v2-bg-darkest, #07090D) 18%, var(--ctrl-bot));
  }

  .sdr-host {
    width: 100%;
    height: 100%;
    font-family: var(--v2-font-mono, 'Roboto Mono', monospace);
    color: var(--v2-text-primary, #F5F8FC);
  }

  .sdr-panel {
    /* Flex-elastic composition: VFO columns clamped to content, bridge absorbs slack.
       At <1680px viewport (no sidebar promotion) bridge grows up to 360px to eat
       the outer whitespace; beyond that, max-width caps the panel and sidebars
       move up via RadioLayout media query. */
    display: flex;
    align-items: stretch;
    background: linear-gradient(180deg, #0a0e14 0%, #05080c 100%);
    border: 1px solid var(--v2-border-panel, #18222d);
    border-radius: 4px;
    overflow: hidden;
    height: 100%;
    min-height: 0;
    max-width: 1480px;
    margin: 0 auto;
    position: relative;
  }
  .vfo {
    flex: 1 1 500px;
    min-width: 490px;
    max-width: 560px;
  }
  .bridge {
    flex: 1 1 180px;
    min-width: 180px;
    max-width: 360px;
  }
  .sdr-panel::before {
    content: ""; position: absolute; inset: 0; pointer-events: none;
    background:
      linear-gradient(180deg, rgba(255,255,255,.015) 0 1px, transparent 1px 2px) repeat-y,
      radial-gradient(ellipse at 50% -10%, rgba(124, 252, 229, 0.04), transparent 60%);
    background-size: 100% 2px, auto;
    mix-blend-mode: screen;
    opacity: .4;
  }

  /* VFO half */
  .vfo {
    padding: 6px 12px;
    display: grid;
    grid-template-rows: auto auto 1fr auto;
    gap: 3px;
    min-width: 0;
    position: relative;
  }
  .vfo.sub { border-left: 1px solid var(--v2-border-panel, #18222d); }

  .topline {
    display: flex; justify-content: space-between; align-items: center;
    gap: 10px; font-size: 13px; letter-spacing: 0.14em;
    color: var(--v2-text-muted, #607890); text-transform: uppercase;
  }
  .topline .left, .topline .right { display: flex; gap: 6px; align-items: center; }
  .topline .right { color: var(--v2-text-bright, #F0F5FA); font-weight: 700; }

  .ant, .bw-chip {
    display: inline-flex; align-items: center; justify-content: center;
    height: 24px; padding: 0 8px; border-radius: 3px;
    font-size: 12px; font-weight: 700;
    letter-spacing: 0.08em; text-transform: uppercase;
    color: var(--v2-text-secondary, #A0B4C8);
    border: 1px solid rgba(72,96,122,0.24);
    background: transparent;
    white-space: nowrap;
  }
  .ant b, .bw-chip b { color: var(--v2-text-bright, #F0F5FA); font-weight: 700; }

  .txrx-pill {
    display: inline-flex; align-items: center; justify-content: center;
    height: 24px; padding: 0 10px;
    font-size: 12px; font-weight: 700; letter-spacing: 0.12em;
    border: 1px solid; border-radius: 3px; text-transform: uppercase;
    min-width: 34px;
  }
  .txrx-pill.tx {
    color: #0a0a0a;
    border-color: var(--v2-accent-red, #FF2020);
    background: var(--v2-accent-red, #FF2020);
    box-shadow: 0 0 10px rgba(255,32,32,.55), 0 0 0 1px rgba(255,32,32,.25);
  }
  .txrx-pill.rx {
    color: var(--v2-text-dim, #6F8196);
    border-color: rgba(72,96,122,0.24);
    background: transparent;
  }

  /* Meter */
  .smeter-linear { position: relative; width: 100%; height: 40px; }
  .smeter-linear svg { display: block; width: 100%; height: 100%; overflow: visible; }
  .smeter-linear.dimmed svg { opacity: 0.55; }
  .smeter-linear .overlay {
    position: absolute; top: 20%; right: 4px;
    display: flex; align-items: center;
    pointer-events: none;
  }
  .smeter-linear .read {
    font-family: var(--v2-font-mono, 'Roboto Mono', monospace);
    font-size: 12px; font-weight: 700;
    color: var(--v2-text-bright, #F0F5FA); letter-spacing: 0.06em;
    padding: 2px 6px; background: rgba(0,0,0,.85);
    border: 1px solid rgba(72,96,122,0.42); border-radius: 3px;
    text-shadow: 0 0 6px rgba(124,252,229,.5);
  }

  /* Freq stack */
  .freq-stack { display: flex; flex-direction: column; gap: 2px; min-width: 0; }
  .info-row { display: flex; justify-content: space-between; align-items: flex-start; gap: 8px; }

  .mode-info { display: flex; flex-direction: column; gap: 3px; }
  .mode-row {
    display: flex; align-items: center; gap: 6px;
    min-height: 22px; height: 22px;
  }
  .mode-label {
    font-size: 12px; font-weight: 700; letter-spacing: 0.14em;
    color: var(--v2-text-muted, #607890); text-transform: uppercase;
  }
  .mode-value {
    font-size: 15px; font-weight: 700; letter-spacing: 0.1em;
    color: var(--v2-text-bright, #F0F5FA);
  }
  .vfo-row .mode-value { font-size: 16px; letter-spacing: 0.18em; }

  /* Neutral mode chip (USB/LSB/CW/…): white text on subtle grey, sits in the
     VFO row instead of the previous cyan/orange "active button" look. Still
     clickable — keeps onMainModeClick / onSubModeClick. */
  .mode-chip {
    display: inline-flex; align-items: center; justify-content: center;
    height: 22px; padding: 0 8px; border-radius: 3px;
    font-family: inherit;
    font-size: 12px; font-weight: 700; letter-spacing: 0.1em;
    text-transform: uppercase; white-space: nowrap;
    color: var(--v2-text-white, #FFFFFF);
    background: rgba(255, 255, 255, 0.08);
    border: 1px solid rgba(160, 180, 200, 0.28);
    cursor: pointer;
    transition: background-color 140ms ease, border-color 140ms ease;
  }
  .mode-chip:hover {
    background: rgba(255, 255, 255, 0.12);
    border-color: rgba(160, 180, 200, 0.5);
  }

  .status-chip {
    display: inline-flex; align-items: center; justify-content: center;
    height: 22px; padding: 0 8px; border-radius: 3px;
    font-size: 12px; font-weight: 700; letter-spacing: 0.08em;
    text-transform: uppercase; white-space: nowrap;
    transition: opacity 140ms ease, filter 140ms ease;
  }
  .status-chip:not(.on) { opacity: 0.25; filter: saturate(0.3); }
  .status-chip.green {
    color: var(--v2-accent-green, #00CC66);
    border: 1px solid rgba(0,204,102,.55);
    background: rgba(0,204,102,.08);
  }
  .status-chip.green.on { text-shadow: 0 0 6px rgba(0,204,102,.35); }
  .status-chip.red {
    color: var(--v2-accent-red, #FF2020);
    border: 1px solid rgba(255,32,32,.55);
    background: rgba(255,32,32,.08);
  }
  .status-chip.cyan {
    color: var(--v2-accent-cyan-bright, #7CFCE5);
    border: 1px solid rgba(0,212,255,.5);
    background: rgba(0,212,255,.1);
  }
  .status-chip.cyan.on { text-shadow: 0 0 6px rgba(0,212,255,.35); }

  .aux-info { display: flex; flex-direction: column; align-items: flex-end; gap: 4px; }
  .aux-row {
    display: grid; grid-template-columns: 86px 1fr;
    gap: 6px; align-items: center;
    min-height: 24px; height: 24px;
    transition: opacity 140ms ease, filter 140ms ease;
  }
  .aux-row:not(.on) { opacity: 0.22; filter: saturate(0.25); }

  .aux-label {
    display: inline-flex; align-items: center; justify-content: center;
    height: 24px; padding: 0 8px; border-radius: 3px;
    font-size: 12px; font-weight: 700; letter-spacing: 0.08em;
    text-transform: uppercase; white-space: nowrap;
    justify-self: start;
  }
  .aux-value {
    font-size: 15px; font-weight: 500; font-variant-numeric: tabular-nums;
    white-space: nowrap; line-height: 1; text-align: right;
    justify-self: end; letter-spacing: 0.03em;
  }
  .aux-row.rit .aux-label { color: var(--v2-accent-green, #00CC66); border: 1px solid rgba(0,204,102,.55); background: rgba(0,204,102,.08); }
  .aux-row.rit .aux-value { color: var(--v2-accent-green, #00CC66); }
  .aux-row.xit .aux-label { color: var(--v2-accent-red, #FF2020); border: 1px solid rgba(255,32,32,.55); background: rgba(255,32,32,.08); }
  .aux-row.xit .aux-value { color: var(--v2-accent-red, #FF2020); }
  .aux-row.split .aux-label {
    color: var(--v2-accent-orange, #FF6A00);
    border: 1px solid rgba(255,106,0,.55);
    background: rgba(255,106,0,.08);
  }
  .aux-row.split .aux-value {
    color: var(--v2-accent-orange, #FF6A00);
    font-size: 13px; font-weight: 400;
    text-shadow: 0 0 6px rgba(255,106,0,.35);
  }

  .freq-wrap {
    position: relative;
    display: flex; align-items: baseline; justify-content: flex-end;
    gap: 8px; min-width: 0; cursor: pointer;
  }
  .freq {
    font-weight: 700; font-size: 52px; line-height: 1;
    letter-spacing: 0.01em; font-variant-numeric: tabular-nums;
    text-align: right; white-space: nowrap;
  }
  .freq .dot { opacity: 0.5; margin: 0 .01em; }
  .unit {
    font-size: 11px; letter-spacing: 0.18em;
    color: var(--v2-text-muted, #607890); font-weight: 700;
  }
  .vfo.main .freq.active {
    color: var(--v2-vfo-main-freq-active, #7CFCE5);
    text-shadow: 0 0 12px rgba(124, 252, 229, 0.5);
  }
  .vfo.sub .freq.active {
    color: var(--v2-vfo-sub-freq-active, #FFFFFF);
    text-shadow: 0 0 12px rgba(255, 255, 255, 0.42);
  }
  .freq.inactive { color: var(--v2-text-dim, #6F8196); filter: saturate(0.3); }

  /* Status strip */
  .status-strip {
    display: flex; gap: 6px; align-items: center;
    min-height: 24px; flex-wrap: wrap;
  }
  .badge {
    display: inline-flex; align-items: center; justify-content: center;
    height: 22px; padding: 0 8px; border-radius: 3px;
    font-size: 11px; font-weight: 700; letter-spacing: 0.08em;
    /* No text-transform: labels are uppercase in source; leave case intact so
       unit suffixes like "dB" render correctly. */
    border: 1px solid rgba(72,96,122,0.24);
    color: var(--v2-text-muted, #607890); background: transparent;
    white-space: nowrap;
  }
  .badge.cyan.on {
    color: var(--v2-accent-cyan-bright, #7CFCE5);
    border-color: rgba(0,212,255,.5);
    background: rgba(0,212,255,.1);
  }
  .badge.amber.on {
    color: var(--v2-accent-yellow, #F2CF4A);
    border-color: rgba(242,207,74,.5);
    background: rgba(242,207,74,.08);
  }
  .badge.green.on {
    color: var(--v2-accent-green, #00CC66);
    border-color: rgba(0,204,102,.5);
    background: rgba(0,204,102,.08);
  }
  .badge.red.on {
    color: var(--v2-accent-red, #FF2020);
    border-color: rgba(255,32,32,.5);
    background: rgba(255,32,32,.08);
  }
  .badge-group { display: inline-flex; gap: 4px; align-items: center; }

  /* Bridge */
  .bridge {
    background: linear-gradient(180deg, rgba(14,20,32,.98) 0%, rgba(7,9,13,.98) 100%);
    border-left: 1px solid var(--v2-border-panel, #18222d);
    border-right: 1px solid var(--v2-border-panel, #18222d);
    padding: 10px 8px;
    display: flex; flex-direction: column; gap: 6px;
    justify-content: space-between;
  }
  .bridge .b-title {
    font-size: 8px; letter-spacing: 0.2em;
    color: var(--v2-text-muted, #607890);
    text-transform: uppercase; text-align: center;
  }
  .bridge .row {
    display: grid; grid-template-columns: 1fr 1fr; gap: 4px;
    /* Centered content inside an elastic bridge — prevents buttons from
       stretching to huge widths when bridge is wider than its ideal. */
    max-width: 148px; width: 100%; align-self: center;
  }
  .bridge .row.single { grid-template-columns: 1fr; }
  .bridge .sep { align-self: stretch; }
  .bridge .b-title, .bridge .speak { align-self: center; min-width: 140px; }
  .bridge .sep {
    height: 1px; background: var(--v2-border-dark, #18202A);
    margin: 2px 0;
  }
  .bridge .speak {
    font-size: 8px; color: var(--v2-text-muted, #607890);
    letter-spacing: 0.1em; text-align: center;
    cursor: pointer; background: transparent; border: none;
    padding: 4px; font-family: inherit;
  }
  .bridge .speak:hover { color: var(--v2-accent-cyan, #00D4FF); }

  /* Stacked layout for narrow viewports */
  @media (max-width: 900px) {
    .sdr-panel { grid-template-columns: 1fr; }
    .vfo.sub { border-left: none; border-top: 1px solid var(--v2-border-panel, #18222d); }
    .bridge { border-left: none; border-right: none; flex-direction: row; }
    .bridge .row { grid-template-columns: repeat(2, 1fr); }
  }
</style>
