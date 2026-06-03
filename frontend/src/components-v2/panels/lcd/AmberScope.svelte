<script lang="ts">
  import { deriveAmberScopeProps } from '$lib/runtime/adapters/panel-adapters';
  import {
    toTxProps, toRitXitProps, toVfoOpsProps, toDspProps, toFilterProps,
  } from '../../wiring/state-adapter';
  import AmberFrequency from './AmberFrequency.svelte';
  import AmberAfScope from './AmberAfScope.svelte';
  import AmberFilterGhost from './AmberFilterGhost.svelte';
  import AmberIndStrip from './AmberIndStrip.svelte';
  import type { IndToken } from './AmberIndStrip.svelte';
  import { runtime } from '$lib/runtime';
  import { isFieldAvailable } from '$lib/state/field-status';

  // Band lookup by frequency (LCD-specific, mirrors AmberCockpit)
  const BANDS: [string, number, number][] = [
    ['160m', 1800000, 2000000],
    ['80m',  3500000, 4000000],
    ['60m',  5351500, 5366500],
    ['40m',  7000000, 7300000],
    ['30m',  10100000, 10150000],
    ['20m',  14000000, 14350000],
    ['17m',  18068000, 18168000],
    ['15m',  21000000, 21450000],
    ['12m',  24890000, 24990000],
    ['10m',  28000000, 29700000],
    ['6m',   50000000, 54000000],
    ['4m',   70000000, 70500000],
    ['2m',   144000000, 148000000],
    ['70cm', 420000000, 450000000],
    ['MW',   530000, 1710000],
    ['SW',   2300000, 30000000],
  ];
  function freqToBand(hz: number): string {
    for (const [name, lo, hi] of BANDS) {
      if (hz >= lo && hz <= hi) return name;
    }
    if (hz >= 88000000 && hz <= 108000000) return 'FM';
    if (hz >= 108000000 && hz <= 137000000) return 'AIR';
    return '';
  }

  // AGC mode labels (mirrors AmberCockpit)
  const AGC_LABELS: Record<number, string> = {
    0: 'OFF', 1: 'FAST', 2: 'MID', 3: 'SLOW',
    4: 'A-F', 5: 'A-M', 6: 'A-S',
  };
  function agcLabelFor(agcMode: number): string {
    return AGC_LABELS[agcMode] ?? `${agcMode}`;
  }

  let scopeProps = $derived(deriveAmberScopeProps());
  let radioState = $derived(scopeProps.radioState);
  let caps = $derived(scopeProps.caps);
  let hasCap = $derived(scopeProps.hasCapability);

  let tx = $derived(toTxProps(radioState, null));
  let ritXit = $derived(toRitXitProps(radioState, null));
  let vfoOps = $derived(toVfoOpsProps(radioState, null));
  let dsp = $derived(toDspProps(radioState, null));
  let filterProps = $derived(toFilterProps(radioState, caps));

  // Active receiver for indicator zone data (unchanged from pre-#897)
  let rx = $derived(radioState?.active === 'SUB' ? radioState?.sub : radioState?.main);
  let activeRxKey = $derived<'main' | 'sub'>(radioState?.active === 'SUB' ? 'sub' : 'main');

  // MOR-429: gate active-receiver indicators on fieldStatus availability so an
  // unobserved/stale/default value is never presented as a confirmed reading.
  function rxAvailable(field: string): boolean {
    return isFieldAvailable(radioState, `${activeRxKey}.${field}`);
  }

  // ── VFO A (MAIN) — always top line ──
  // Read directly from radioState.main so the tag is statically correct (fixes codex P2).
  let mainFreqHz = $derived(radioState?.main?.freqHz ?? 0);
  let mainMode = $derived(radioState?.main?.mode ?? '---');
  let mainBand = $derived(freqToBand(mainFreqHz));
  // Fallback 2400 Hz matches `toFilterProps()` adapter default (codex P2 on
  // PR #916): keeps the VFO A filter badge visible with a stable width when
  // `main.filterWidth` is null (initial state or rig without filter reporting).
  let mainFilterWidth = $derived(radioState?.main?.filterWidth ?? 2400);
  let mainFilterWidthLabel = $derived.by(() => {
    const w = mainFilterWidth;
    if (w <= 0) return '';
    return w >= 1000 ? `${(w / 1000).toFixed(1)} kHz` : `${w} Hz`;
  });

  // ── VFO B (SUB) — compact second line on dual-RX only ──
  let subFreqHz = $derived(radioState?.sub?.freqHz ?? 0);
  let subMode = $derived(radioState?.sub?.mode ?? '---');
  let subBand = $derived(freqToBand(subFreqHz));

  // Active-state per VFO: A active when main is the active receiver
  let isAActive = $derived(radioState?.active !== 'SUB');
  let isBActive = $derived(radioState?.active === 'SUB');

  // Derived raw state helpers
  let lockActive = $derived(radioState?.dialLock ?? false);
  let notchActive = $derived(dsp.notchMode === 'manual');

  // ── Indicator token arrays ──

  // frontendTokens: TX-chain (TX/VOX/PROC/ATT/PRE)
  let frontendTokens = $derived<IndToken[]>([
    {
      id: 'tx', label: 'TX', active: tx.txActive,
      variant: tx.txActive ? 'tx' : undefined,
    },
    ...(hasCap('vox') ? [{ id: 'vox' as const, label: 'VOX', active: tx.voxActive }] : []),
    ...(hasCap('compressor') ? [{
      id: 'proc' as const,
      label: tx.compActive ? `PROC ${tx.compLevel}` : 'PROC',
      active: tx.compActive,
    }] : []),
    ...(hasCap('attenuator') && rxAvailable('att') ? [{
      id: 'att' as const, label: 'ATT', active: (rx?.att ?? 0) > 0,
    }] : []),
    ...(hasCap('preamp') && rxAvailable('preamp') ? [{
      id: 'pre' as const,
      label: (rx?.preamp ?? 0) === 0 ? 'IPO'
           : (rx?.preamp ?? 0) === 1 ? 'AMP1' : 'AMP2',
      active: true,
    }] : []),
  ]);

  // dspTokens: RX processing (NB/NR/NOTCH/ANF/RFG)
  let dspTokens = $derived<IndToken[]>([
    ...(hasCap('nb') && rxAvailable('nb') ? [{
      id: 'nb' as const,
      label: (rx?.nb ?? false) || (rx?.nbLevel ?? 0) > 0
        ? `NB ${rx?.nbLevel ?? 0}` : 'NB',
      active: (rx?.nb ?? false) || (rx?.nbLevel ?? 0) > 0,
    }] : []),
    ...(hasCap('nr') && rxAvailable('nr') ? [{
      id: 'nr' as const,
      label: (rx?.nr ?? false) || (rx?.nrLevel ?? 0) > 0
        ? `NR ${rx?.nrLevel ?? 0}` : 'NR',
      active: (rx?.nr ?? false) || (rx?.nrLevel ?? 0) > 0,
    }] : []),
    ...(hasCap('notch') ? [
      ...(rxAvailable('manualNotch')
        ? [{ id: 'notch' as const, label: 'NOTCH', active: rx?.manualNotch ?? false }]
        : []),
      ...(rxAvailable('autoNotch')
        ? [{ id: 'anf' as const, label: 'ANF', active: rx?.autoNotch ?? false }]
        : []),
    ] : []),
    ...(hasCap('rf_gain') && rxAvailable('rfGain') ? [{
      id: 'rfg' as const, label: 'RFG', active: (rx?.rfGain ?? 255) < 255,
    }] : []),
  ]);

  // globalTokens: AGC/SQL/LOCK/SPLIT/RIT
  let globalTokens = $derived<IndToken[]>([
    ...(rxAvailable('agc')
      ? [{ id: 'agc' as const, label: `AGC ${agcLabelFor(rx?.agc ?? 2)}`, active: true }]
      : []),
    ...(hasCap('squelch') && rxAvailable('squelch') ? [{
      id: 'sql' as const, label: 'SQL', active: (rx?.squelch ?? 0) > 0,
    }] : []),
    ...(hasCap('dial_lock') ? [{ id: 'lock' as const, label: 'LOCK', active: lockActive }] : []),
    ...(hasCap('split') ? [{ id: 'split' as const, label: 'SPLIT', active: vfoOps.splitActive }] : []),
    ...(hasCap('rit') ? [{
      id: 'rit' as const, label: 'RIT', active: ritXit.ritActive,
    }] : []),
  ]);

  // FFT scope connection — reactive to capabilities
  let fftPixels = $state<Uint8Array | null>(null);
  let fftBandwidth = $state<number | undefined>(undefined);
  let fftPush: ((data: Uint8Array) => void) | null = null;
  let showFft = $derived(scopeProps.hasAudioFft);

  // Scope subscription — delegates lifecycle to ScopeController (ADR INV-2, INV-5)
  $effect(() => {
    if (!scopeProps.hasAudioFft) return;

    return runtime.scope.subscribe((frame) => {
      fftPixels = frame.pixels;
      fftBandwidth = frame.endFreq > frame.startFreq ? frame.endFreq - frame.startFreq : undefined;
      fftPush?.(frame.pixels);
    });
  });
</script>

<!--
  AmberScope — IC-7300-style scope-dominant layout.
  Grid: header (VFO A always, VFO B on dual-RX) / ind-strips (3 zones) / scope (dominant AfScope).
  Issue #899 adds frontend/dsp/global indicator zones.
  Issue #896 / epic #887 C-PR2 (single-RX).
  Issue #897 / epic #887 C-PR3 (dual-RX compact sub-VFO line).
  VFO A = always MAIN receiver; VFO B = always SUB receiver (fixes codex P2 tag semantics).
-->
<div class="amber-lcd amber-lcd-scope" class:tx-active={tx.txActive}>
  <div class="lcd-screen">
    <div class="lcd-scanlines"></div>

    <!-- ═══ Header: VFO A (always) + VFO B (dual-RX only) ═══ -->
    <div class="lcd-header" style:grid-area="header">
      <!-- VFO A row (main receiver — always shown) -->
      <div class="vfo-row" class:inactive={!isAActive}>
        <span class="vfo-tag">►A</span>
        <div class="vfo-freq">
          <AmberFrequency freqHz={mainFreqHz} size="large" />
        </div>
        <div class="vfo-badges">
          {#if mainBand}
            <span class="vfo-band-box">{mainBand}</span>
          {/if}
          <span class="vfo-mode-box">{mainMode}</span>
          {#if mainFilterWidthLabel}
            <span class="vfo-filter-box">{mainFilterWidthLabel}</span>
          {/if}
        </div>
      </div>

      <!-- VFO B row (sub receiver — compact, dual-RX only) -->
      {#if scopeProps.hasDualReceiver}
        <div class="vfo-row vfo-row-b" class:inactive={!isBActive}>
          <span class="vfo-tag vfo-tag-sub">►B</span>
          <div class="vfo-freq">
            <AmberFrequency freqHz={subFreqHz} size="small" />
          </div>
          <div class="vfo-badges">
            {#if subBand}
              <span class="vfo-band-box vfo-band-box-sub">{subBand}</span>
            {/if}
            <span class="vfo-mode-box vfo-mode-box-sub">{subMode}</span>
          </div>
        </div>
      {/if}
    </div>

    <!-- ═══ Indicator zones: FRONT / DSP / global ═══ -->
    <div class="lcd-ind-zones" style:grid-area="ind-strips">
      <AmberIndStrip zone="frontend" tokens={frontendTokens} />
      <div class="zone-sep"></div>
      <AmberIndStrip zone="dsp" tokens={dspTokens} />
      <div class="zone-sep"></div>
      <AmberIndStrip zone="global" tokens={globalTokens} />
    </div>

    <!-- ═══ Scope: dominant AfScope ═══ -->
    <div class="lcd-scope" style:grid-area="scope">
      {#if showFft}
        <AmberAfScope
          data={fftPixels}
          onRegisterPush={(fn) => { fftPush = fn; }}
          filterWidth={filterProps.filterWidth}
          filterWidthMax={filterProps.filterWidthMax}
          ifShift={filterProps.ifShift}
          bandwidth={fftBandwidth}
          mode="dominant"
        />
      {:else}
        <AmberFilterGhost
          filterWidth={filterProps.filterWidth}
          filterWidthMax={filterProps.filterWidthMax}
        />
      {/if}
    </div>
  </div>
</div>

<style>
  .amber-lcd {
    width: 100%;
    height: 100%;
    display: flex;
    align-items: stretch;
    padding: 4px;
    box-sizing: border-box;
    min-height: 0;
  }

  .lcd-screen {
    /* Contrast tokens — identical defaults to AmberCockpit. */
    --lcd-alpha-active: 1;
    --lcd-alpha-inactive: 0.08;
    --lcd-alpha-ghost: 0.06;

    position: relative;
    width: 100%;
    background: #C8A030;
    border: 2px solid #8A7020;
    border-radius: 8px;
    padding: 12px 18px;
    overflow: hidden;
    box-shadow:
      inset 0 0 50px rgba(0, 0, 0, 0.06),
      0 0 8px rgba(0, 0, 0, 0.5);
    min-height: 0;

    /* Grid: header / indicator-zones / scope (scope-dominant) */
    display: grid;
    grid-template-rows: auto auto minmax(0, 1fr);
    grid-template-areas:
      "header"
      "ind-strips"
      "scope";
    grid-template-columns: minmax(0, 1fr);
    gap: 4px;
  }

  .lcd-scanlines {
    position: absolute;
    inset: 0;
    pointer-events: none;
    z-index: 1;
    background: repeating-linear-gradient(
      to bottom,
      transparent 0px,
      transparent 3px,
      rgba(0, 0, 0, 0.03) 3px,
      rgba(0, 0, 0, 0.03) 6px
    );
  }

  /* ── Header: VFO rows ── */
  .lcd-header {
    display: flex;
    flex-direction: column;
    gap: 3px;
    position: relative;
    z-index: 2;
    min-height: 0;
    overflow: hidden;
  }

  /* One VFO row (A or B): tag + freq + badges in a flex line */
  .vfo-row {
    display: flex;
    align-items: center;
    gap: 10px;
    min-height: 0;
    overflow: hidden;
  }

  /* Inactive row: demote ink alpha (mirrors AmberCockpit pattern) */
  .vfo-row.inactive {
    --lcd-alpha-active: var(--lcd-alpha-inactive);
  }

  /* Compact B row sits below A; separator line above it */
  .vfo-row-b {
    border-top: 1px solid rgba(26, 16, 0, calc(var(--lcd-alpha-ghost) * 2));
    padding-top: 2px;
  }

  .vfo-tag {
    font-family: 'JetBrains Mono', 'Courier New', monospace;
    font-size: 20px;
    font-weight: 700;
    color: rgba(26, 16, 0, var(--lcd-alpha-active));
    border: 2px solid rgba(26, 16, 0, calc(var(--lcd-alpha-active) * 0.4));
    border-radius: 4px;
    padding: 0 6px;
    line-height: 1.3;
    flex-shrink: 0;
  }

  /* Sub-VFO tag is slightly smaller to match the compact B row */
  .vfo-tag-sub {
    font-size: 14px;
  }

  .vfo-freq {
    flex: 0 1 auto;
    display: flex;
    align-items: center;
    min-width: 0;
    overflow: hidden;
  }

  .vfo-badges {
    display: flex;
    align-items: center;
    gap: 6px;
    flex-shrink: 0;
  }

  .vfo-band-box,
  .vfo-mode-box,
  .vfo-filter-box {
    flex-shrink: 0;
    font-family: 'JetBrains Mono', 'Courier New', monospace;
    font-size: 16px;
    font-weight: 700;
    color: rgba(26, 16, 0, var(--lcd-alpha-active));
    letter-spacing: 1px;
    border: 2px solid rgba(26, 16, 0, calc(var(--lcd-alpha-active) * 0.4));
    border-radius: 4px;
    padding: 2px 8px;
  }

  .vfo-band-box {
    background: rgba(26, 16, 0, var(--lcd-alpha-ghost));
  }

  /* Compact badges for sub-VFO B row */
  .vfo-band-box-sub,
  .vfo-mode-box-sub {
    font-size: 12px;
    padding: 1px 6px;
  }

  .vfo-filter-box {
    font-size: 14px;
    color: rgba(26, 16, 0, calc(var(--lcd-alpha-active) * 0.7));
    border-color: rgba(26, 16, 0, calc(var(--lcd-alpha-active) * 0.25));
  }

  /* ── Indicator zones row ── */
  .lcd-ind-zones {
    display: flex;
    align-items: center;
    gap: 0;
    position: relative;
    z-index: 2;
    min-height: 0;
    overflow: hidden;
    flex-wrap: wrap;
    row-gap: 2px;
  }

  /* Vertical separator between zone strips */
  .zone-sep {
    width: 1px;
    height: 16px;
    background: rgba(26, 16, 0, calc(var(--lcd-alpha-ghost) * 2));
    flex-shrink: 0;
    margin: 0 6px;
    align-self: center;
  }

  /* ── Scope cell: fills remaining height ── */
  .lcd-scope {
    position: relative;
    z-index: 2;
    min-height: 0;
    min-width: 0;
    overflow: hidden;
  }

  /* ── TX glow ── */
  .tx-active .lcd-screen {
    border-color: #5A2000;
    box-shadow:
      inset 0 0 40px rgba(180, 30, 0, 0.08),
      0 0 10px rgba(180, 30, 0, 0.15);
  }
</style>
