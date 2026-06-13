<script lang="ts">
  import {
    deriveAmberCockpitProps, getAmberCockpitHandlers,
  } from '$lib/runtime/adapters/panel-adapters';
  import {
    toTxProps, toRitXitProps, toVfoOpsProps, toMeterProps,
    toDspProps, toFilterProps,
  } from '../../wiring/state-adapter';
  import AmberFrequency from './AmberFrequency.svelte';
  import AmberSmeter from './AmberSmeter.svelte';
  import AmberAfScope from './AmberAfScope.svelte';
  import AmberFilterGhost from './AmberFilterGhost.svelte';
  import AmberIndStrip from './AmberIndStrip.svelte';
  import AmberTelemetryStrip from './AmberTelemetryStrip.svelte';
  import AmberMemoryStrip from './AmberMemoryStrip.svelte';
  import type { IndToken } from './AmberIndStrip.svelte';
  import { runtime } from '$lib/runtime';
  import { isFieldAvailable } from '$lib/state/field-status';
  import { formatOffsetKHz } from '../rit-utils';

  const handlers = getAmberCockpitHandlers();

  // MOR-429: gate per-receiver indicators on fieldStatus availability so an
  // unobserved/stale/default value is never presented as a confirmed reading.
  // The cockpit's VFO A strip always renders MAIN, VFO B always SUB, so each
  // token gates on its own receiver path (e.g. `main.agc` / `sub.agc`).
  function rxAvailable(rxKey: 'main' | 'sub', field: string): boolean {
    return isFieldAvailable(radioState, `${rxKey}.${field}`);
  }

  // Band lookup by frequency (LCD-specific)
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

  let cockpitProps = $derived(deriveAmberCockpitProps());
  let radioState = $derived(cockpitProps.radioState);
  let caps = $derived(cockpitProps.caps);
  let hasCap = $derived(cockpitProps.hasCapability);

  // ── Adapter-derived state ──
  let tx = $derived(toTxProps(radioState, null));
  let ritXit = $derived(toRitXitProps(radioState, null));
  let vfoOps = $derived(toVfoOpsProps(radioState, null));
  let meter = $derived(toMeterProps(radioState));
  let dsp = $derived(toDspProps(radioState, null));
  let filterProps = $derived(toFilterProps(radioState, caps));

  // ── LCD-specific derivations (no adapter equivalent) ──
  let rx = $derived(radioState?.active === 'SUB' ? radioState?.sub : radioState?.main);
  let subSValue = $derived(radioState?.sub?.sMeter ?? 0);

  type MeterSource = 'S' | 'PO' | 'SWR' | 'ALC' | 'COMP';
  const METER_SOURCES: MeterSource[] = ['S', 'PO', 'SWR', 'ALC', 'COMP'];
  let userMeterSource = $state<MeterSource>('S');

  // Auto-switch to PO during TX if user hasn't selected a TX meter
  let activeMeterSource = $derived<MeterSource>(
    tx.txActive && userMeterSource === 'S' ? 'PO' : userMeterSource
  );

  let meterValue = $derived.by(() => {
    switch (activeMeterSource) {
      case 'PO': return meter.rfPower;
      case 'SWR': return meter.swr;
      case 'ALC': return meter.alc;
      case 'COMP': return meter.comp;
      default: return rx?.sMeter ?? 0;  // active receiver, not always main
    }
  });

  function cycleMeterSource() {
    const idx = METER_SOURCES.indexOf(userMeterSource);
    userMeterSource = METER_SOURCES[(idx + 1) % METER_SOURCES.length];
  }

  // LCD-specific derivations
  let notchActive = $derived(dsp.notchMode === 'manual');
  let lockActive = $derived(radioState?.dialLock ?? false);
  let contourLevel = $derived(rx?.contour ?? 0);
  let dataActive = $derived(!!rx?.dataMode);

  let fftPixels = $state<Uint8Array | null>(null);
  let fftBandwidth = $state<number | undefined>(undefined);
  let fftPush: ((data: Uint8Array) => void) | null = null;
  let showFft = $derived(cockpitProps.hasAudioFft);

  // FTX-1 AGC: 0=OFF, 1=FAST, 2=MID, 3=SLOW, 4=AUTO-F, 5=AUTO-M, 6=AUTO-S
  const AGC_LABELS: Record<number, string> = {
    0: 'OFF', 1: 'FAST', 2: 'MID', 3: 'SLOW',
    4: 'A-F', 5: 'A-M', 6: 'A-S',
  };

  // ── Indicator token arrays ──
  // globalTokens: radio-wide status indicators for the top global strip
  let globalTokens = $derived<IndToken[]>([
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
    ...(hasCap('tuner') ? [{
      id: 'atu' as const,
      label: tx.atuTuning ? 'TUNE' : 'ATU',
      active: tx.atuActive,
      variant: tx.atuTuning ? ('tuning' as const) : undefined,
    }] : []),
    ...(hasCap('split') ? [{ id: 'split' as const, label: 'SPLIT', active: vfoOps.splitActive }] : []),
    ...(hasCap('dial_lock') ? [{ id: 'lock' as const, label: 'LOCK', active: lockActive }] : []),
    ...(dataActive ? [{ id: 'data' as const, label: 'DATA', active: true }] : []),
    ...(hasCap('ip_plus') ? [{
      // IP+ is a per-receiver setting on IC-7610 (codex P2 on PR #906).
      // Bind to the active RX so SUB's IP+ state is reflected when SUB is active.
      id: 'ipPlus' as const, label: 'IP+', active: rx?.ipplus ?? false,
    }] : []),
  ]);

  // Helper: AGC label from raw AGC mode number
  function agcLabelFor(agcMode: number): string {
    return AGC_LABELS[agcMode] ?? `${agcMode}`;
  }

  // Per-receiver token builder — gates every indicator on fieldStatus
  // availability (MOR-429). Unavailable fields are suppressed entirely rather
  // than shown as confirmed defaults; AGC in particular no longer emits
  // `active: true` when `${rxKey}.agc` is missing/stale.
  function vfoTokens(rxKey: 'main' | 'sub'): IndToken[] {
    const rxState = radioState?.[rxKey];
    return [
      ...(hasCap('attenuator') && rxAvailable(rxKey, 'att') ? [{
        id: 'att' as const, label: 'ATT', active: (rxState?.att ?? 0) > 0,
      }] : []),
      ...(hasCap('preamp') && rxAvailable(rxKey, 'preamp') ? [{
        id: 'pre' as const,
        label: (rxState?.preamp ?? 0) === 0 ? 'IPO'
             : (rxState?.preamp ?? 0) === 1 ? 'AMP1' : 'AMP2',
        active: true,
      }] : []),
      ...(hasCap('digisel') && rxAvailable(rxKey, 'digisel') ? [{
        id: 'digisel' as const, label: 'DIGI-SEL', active: rxState?.digisel ?? false,
      }] : []),
      ...(hasCap('nb') && rxAvailable(rxKey, 'nb') ? [{
        id: 'nb' as const,
        label: (rxState?.nb ?? false) || (rxState?.nbLevel ?? 0) > 0
          ? `NB ${rxState?.nbLevel ?? 0}` : 'NB',
        active: (rxState?.nb ?? false) || (rxState?.nbLevel ?? 0) > 0,
      }] : []),
      ...(hasCap('nr') && rxAvailable(rxKey, 'nr') ? [{
        id: 'nr' as const,
        label: (rxState?.nr ?? false) || (rxState?.nrLevel ?? 0) > 0
          ? `NR ${rxState?.nrLevel ?? 0}` : 'NR',
        active: (rxState?.nr ?? false) || (rxState?.nrLevel ?? 0) > 0,
      }] : []),
      ...(hasCap('contour') && rxAvailable(rxKey, 'contour') ? [{
        id: 'cont' as const, label: 'CONT',
        active: (rxState?.contour ?? 0) > 0,
      }] : []),
      ...(hasCap('notch') ? [
        ...(rxAvailable(rxKey, 'manualNotch')
          ? [{ id: 'notch' as const, label: 'NOTCH', active: rxState?.manualNotch ?? false }]
          : []),
        ...(rxAvailable(rxKey, 'autoNotch')
          ? [{ id: 'anf' as const, label: 'ANF', active: rxState?.autoNotch ?? false }]
          : []),
      ] : []),
      ...(rxAvailable(rxKey, 'agc')
        ? [{ id: 'agc' as const, label: `AGC ${agcLabelFor(rxState?.agc ?? 2)}`, active: true }]
        : []),
      ...(hasCap('rf_gain') && rxAvailable(rxKey, 'rfGain') ? [{
        id: 'rfg' as const, label: 'RFG', active: (rxState?.rfGain ?? 1) < 1,
      }] : []),
      ...(hasCap('squelch') && rxAvailable(rxKey, 'squelch') ? [{
        id: 'sql' as const, label: 'SQL', active: (rxState?.squelch ?? 0) > 0,
      }] : []),
    ];
  }

  // vfoATokens: per-receiver indicators for VFO A (main)
  let vfoATokens = $derived<IndToken[]>([
    ...vfoTokens('main'),
    ...(hasCap('rit') ? [{
      id: 'rit' as const, label: 'RIT', active: ritXit.ritActive,
    }] : []),
  ]);

  // vfoBTokens: per-receiver indicators for VFO B (sub)
  let vfoBTokens = $derived<IndToken[]>(vfoTokens('sub'));

  // Scope subscription — delegates lifecycle to ScopeController (ADR INV-2, INV-5)
  $effect(() => {
    if (!cockpitProps.hasAudioFft) return;

    return runtime.scope.subscribe((frame) => {
      fftPixels = frame.pixels;
      fftBandwidth = frame.endFreq > frame.startFreq ? frame.endFreq - frame.startFreq : undefined;
      fftPush?.(frame.pixels);
    });
  });

  // Record active-receiver frequency changes into the local QSY history
  // ring buffer (#836). The handler delegates to `recordQsy()` in the
  // adapter, which preserves the store-internal debounce + Δ ≥ 500 Hz
  // filter so dial-hunting doesn't pollute the buffer.
  $effect(() => {
    const freq = rx?.freqHz ?? 0;
    const mode = rx?.mode ?? '';
    handlers.onTuningChange(freq, mode);
  });

  function handleQsyRecall(freqHz: number, mode: string): void {
    // Route through runtime — same CI-V path as other frequency changes.
    runtime.send('set_freq', { freq: freqHz });
    if (mode) runtime.send('set_mode', { mode });
  }

  // ── Peer cockpit derivations ──
  // In the dual-cockpit peer layout, column A always = main VFO, column B always = sub VFO.
  // We derive main/sub data directly so each column has stable data regardless of active state.
  let mainFreqHz = $derived(radioState?.main?.freqHz ?? 0);
  let mainMode = $derived(radioState?.main?.mode ?? '---');
  let mainFilter = $derived(radioState?.main?.filter ?? '');
  let mainBand = $derived(freqToBand(mainFreqHz));
  let mainSMeter = $derived(radioState?.main?.sMeter ?? 0);

  let subVfoFreqHz = $derived(radioState?.sub?.freqHz ?? 0);
  let subVfoMode = $derived(radioState?.sub?.mode ?? '');
  let subVfoFilter = $derived(radioState?.sub?.filter ?? '');
  let subVfoBand = $derived(freqToBand(subVfoFreqHz));

  // Active state per column: A active when main is the active receiver
  let vfoAActive = $derived(radioState?.active !== 'SUB');
  let vfoBActive = $derived(radioState?.active === 'SUB');

  // Meter for main VFO cockpit (always main, but source follows active-meter logic).
  // During TX, the meter shows radio-global TX telemetry (PO/SWR/ALC/COMP) — this
  // must win over the receiver-active check so the cockpit never loses TX feedback
  // when SUB is the active RX (VFO B's meter is suppressed during TX by design).
  let mainMeterValue = $derived.by(() => {
    if (tx.txActive) return meterValue;          // TX: radio-global telemetry
    if (vfoAActive) return meterValue;           // RX + A active: use adapter-derived meter
    return mainSMeter;                           // RX + B active: show A's own S-meter
  });
  let mainMeterSource = $derived<MeterSource>(
    tx.txActive || vfoAActive ? activeMeterSource : 'S',
  );
</script>

<!--
  Grid scaffold (issue #891 / plan §3.2 Variant B):
  - Dual-RX: two equal columns (vfo-a | vfo-b), full-width scope below, full-width aux below that.
  - Single-RX: single column (vfo-a), scope, aux.
  - VFO A = always MAIN receiver; VFO B = always SUB receiver.
  - Active receiver indicated by --lcd-alpha-active tokens; inactive by --lcd-alpha-inactive.
  - Font size identical for A and B — only ink alpha changes (no scaleY demotion).
  - Indicators (status tokens) placed in aux row, full-width in both modes.
-->
<div class="amber-lcd" class:tx-active={tx.txActive}>
  <div class="lcd-screen" class:dual={cockpitProps.hasDualReceiver}>
    <div class="lcd-scanlines"></div>

    <!-- ═══ Global indicator strip (TX/VOX/PROC/ATU/SPLIT/LOCK/DATA/IP+) ═══ -->
    <div style:grid-area="global">
      <AmberIndStrip zone="global" tokens={globalTokens} />
    </div>

    <!-- ═══ VFO A cockpit (main receiver — always left/full column) ═══ -->
    <div
      class="lcd-vfo-col lcd-vfo-a"
      class:inactive={!vfoAActive}
      style:grid-area="vfo-a"
    >
      <!-- VFO tag + freq + badges (subgrid row 1) -->
      <div class="lcd-vfo-row lcd-vfo-main">
        <span class="vfo-tag">
          [A]<span class="vfo-dot" class:active={vfoAActive}>●</span>
        </span>
        <div class="vfo-freq">
          <AmberFrequency freqHz={mainFreqHz} size="large" />
        </div>
        <div class="vfo-badges">
          <span class="vfo-mode-box">{mainMode}{mainFilter ? ` ${mainFilter}` : ''}</span>
          {#if mainBand}
            <span class="vfo-band-box">{mainBand}</span>
          {/if}
        </div>
      </div>

      <!-- Meter A (main receiver) -->
      <div class="lcd-meter-row">
        <AmberSmeter value={mainMeterValue} txActive={tx.txActive} source={mainMeterSource} />
        {#if vfoAActive}
          <button class="lcd-meter-src-btn" onclick={cycleMeterSource}>{mainMeterSource}</button>
        {/if}
      </div>

      <!-- RIT / XIT offset (inline within cockpit, collapses when inactive) -->
      {#if ritXit.ritActive || ritXit.xitActive}
        <div class="lcd-rit-row">
          <span class="rit-label">{ritXit.ritActive ? 'RIT' : 'XIT'}</span>
          <span class="rit-value">{formatOffsetKHz(ritXit.ritOffset)}</span>
        </div>
      {/if}

      <!-- Per-VFO indicator strip for VFO A (main receiver) -->
      <AmberIndStrip zone="perVfo" tokens={vfoATokens} />
    </div>

    <!-- ═══ VFO B cockpit (sub receiver — equal peer on dual-RX) ═══ -->
    {#if cockpitProps.hasDualReceiver}
      <div
        class="lcd-vfo-col lcd-vfo-b"
        class:inactive={!vfoBActive}
        style:grid-area="vfo-b"
      >
        <!-- VFO tag + freq + badges -->
        <div class="lcd-vfo-row lcd-vfo-main">
          <span class="vfo-tag">
            [B]<span class="vfo-dot" class:active={vfoBActive}>●</span>
          </span>
          <div class="vfo-freq">
            <AmberFrequency freqHz={subVfoFreqHz} size="large" />
          </div>
          <div class="vfo-badges">
            {#if subVfoMode}
              <span class="vfo-mode-box">{subVfoMode}{subVfoFilter ? ` ${subVfoFilter}` : ''}</span>
            {/if}
            {#if subVfoBand}
              <span class="vfo-band-box">{subVfoBand}</span>
            {/if}
          </div>
        </div>

        <!-- Meter B (sub receiver — full-size, same as A) -->
        <div class="lcd-meter-row">
          {#if !tx.txActive}
            <AmberSmeter value={subSValue} source="S" />
          {/if}
        </div>

        <!-- Per-VFO indicator strip for VFO B (sub receiver) -->
        <AmberIndStrip zone="perVfo" tokens={vfoBTokens} />
      </div>
    {/if}

    <!-- ═══ Scope / Filter-viz (full-width) ═══ -->
    <div class="lcd-filter-row" style:grid-area="scope">
      {#if showFft}
        <div class="lcd-scope-strip">
          <AmberAfScope
            data={fftPixels}
            onRegisterPush={(fn) => { fftPush = fn; }}
            filterWidth={filterProps.filterWidth}
            filterWidthMax={filterProps.filterWidthMax}
            ifShift={filterProps.ifShift}
            contour={contourLevel}
            manualNotch={dsp.notchMode === 'manual'}
            notchFreq={dsp.notchFreq}
            autoNotch={notchActive}
            bandwidth={fftBandwidth}
            compact
            mode="fill"
          />
        </div>
      {:else}
        <!-- Ghost fallback (#919 — mirrors AmberScope #900 integration).
             Prevents the 1fr scope row from rendering as empty amber
             when the radio has no AF-FFT capability. -->
        <AmberFilterGhost
          filterWidth={filterProps.filterWidth}
          filterWidthMax={filterProps.filterWidthMax}
        />
      {/if}
    </div>

    <!-- ═══ Aux row — memory/QSY (#836) + telemetry (#837) ═══
         Two compact rows stacked in the reserved aux grid-area. Memory
         strip on top (user-initiated recalls), telemetry below (passive
         sparklines). Each row is ~18px tall; the combined aux block
         stays inside `auto` track without encroaching on scope. -->
    <div class="lcd-aux-row" style:grid-area="aux">
      <AmberMemoryStrip onQsy={handleQsyRecall} />
      <AmberTelemetryStrip />
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
    /* Container for narrow-viewport collapse query (issue #894).
       inline-size tracks width only — avoids height-dependency loops. */
    container-type: inline-size;
  }

  .lcd-screen {
    /* Contrast tokens — see plan §2.4. Defaults match MID preset (today's
       visual). `applyLcdContrast()` overrides these inline on mount and
       on every preset change. */
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

    /* ── Grid scaffold (issue #892 per-VFO zones) ──
       Single-RX: 1 column, 4 rows (global / vfo-a / scope / aux).
       Dual-RX: 2 equal columns, 4 rows (global / vfo-a vfo-b / scope scope / aux aux).
       Global indicators at top; per-VFO indicators inside each cockpit column. */
    display: grid;
    gap: 6px;
    align-content: start;
    grid-template-columns: minmax(0, 1fr);
    grid-template-rows:
      auto               /* global indicator strip */
      auto               /* vfo-a cockpit */
      minmax(0, 1fr)     /* scope — fills remaining height */
      auto;              /* aux (reserved) */
    grid-template-areas:
      "global"
      "vfo-a"
      "scope"
      "aux";
  }

  .lcd-screen.dual {
    grid-template-columns: minmax(0, 1fr) minmax(0, 1fr);
    grid-template-rows:
      auto               /* global indicator strip full-width */
      auto               /* vfo-a + vfo-b cockpits */
      minmax(0, 1fr)     /* scope full-width — fills remaining height */
      auto;              /* aux (reserved) full-width */
    grid-template-areas:
      "global global"
      "vfo-a  vfo-b"
      "scope  scope"
      "aux    aux";
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

  /* ── VFO cockpit column ── */
  .lcd-vfo-col {
    display: flex;
    flex-direction: column;
    gap: 4px;
    position: relative;
    z-index: 2;
    min-height: 0;
  }

  /* Inactive column: override --lcd-alpha-active to inactive level.
     Using a CSS class (not inline style) so applyLcdContrast() can
     continue to write --lcd-alpha-active on .lcd-screen without being
     stomped by a more-specific inline style on the column div. */
  .lcd-vfo-col.inactive {
    --lcd-alpha-active: var(--lcd-alpha-inactive);
  }

  /* Separator between A and B cockpits */
  .lcd-screen.dual .lcd-vfo-b {
    border-left: 1px solid rgba(26, 16, 0, calc(var(--lcd-alpha-inactive) * 2));
    padding-left: 10px;
  }

  /* ── VFO rows (freq + badges) ── */
  .lcd-vfo-row {
    display: flex;
    align-items: center;
    gap: 10px;
    position: relative;
    z-index: 2;
  }

  .lcd-vfo-main {
    /* issue #860 pattern: 3-col grid so freq digits cannot overflow into badges.
       Applied to BOTH cockpit columns (A and B) — subgrid + overflow:hidden. */
    display: grid;
    grid-template-columns: auto minmax(0, 1fr) auto;
    gap: 10px;
    flex: 1;
    min-height: 0;
    align-items: center;
  }

  .vfo-badges {
    display: flex;
    align-items: center;
    gap: 6px;
    flex-shrink: 0;
  }

  .vfo-tag {
    font-family: 'JetBrains Mono', 'Courier New', monospace;
    font-size: 22px;
    font-weight: 700;
    color: rgba(26, 16, 0, var(--lcd-alpha-active));
    border: 2px solid rgba(26, 16, 0, calc(var(--lcd-alpha-active) * 0.4));
    border-radius: 4px;
    padding: 0 6px;
    line-height: 1.3;
    flex-shrink: 0;
    display: flex;
    align-items: center;
    gap: 2px;
  }

  .vfo-dot {
    font-size: 10px;
    color: rgba(26, 16, 0, var(--lcd-alpha-inactive));
    transition: color 0.1s;
  }

  .vfo-dot.active {
    color: rgba(26, 16, 0, var(--lcd-alpha-active));
  }

  .vfo-freq {
    flex: 0 1 auto;
    display: flex;
    align-items: center;
    min-width: 0;
    overflow: hidden;
  }

  .vfo-band-box {
    flex-shrink: 0;
    font-family: 'JetBrains Mono', 'Courier New', monospace;
    font-size: 18px;
    font-weight: 700;
    color: rgba(26, 16, 0, var(--lcd-alpha-active));
    letter-spacing: 1px;
    border: 2px solid rgba(26, 16, 0, calc(var(--lcd-alpha-active) * 0.4));
    border-radius: 4px;
    padding: 2px 8px;
    background: rgba(26, 16, 0, var(--lcd-alpha-ghost));
  }

  .vfo-mode-box {
    flex-shrink: 0;
    font-family: 'JetBrains Mono', 'Courier New', monospace;
    font-size: 18px;
    font-weight: 700;
    color: rgba(26, 16, 0, var(--lcd-alpha-active));
    letter-spacing: 1px;
    border: 2px solid rgba(26, 16, 0, calc(var(--lcd-alpha-active) * 0.4));
    border-radius: 4px;
    padding: 2px 8px;
  }

  /* ── S-Meter ── */
  .lcd-meter-row {
    position: relative;
    z-index: 2;
    display: flex;
    align-items: center;
    flex-shrink: 1;
    min-height: 0;
  }
  .lcd-meter-row :global(.lcd-smeter) {
    flex: 1;
  }
  .lcd-meter-src-btn {
    font-family: 'JetBrains Mono', monospace;
    font-size: 10px;
    font-weight: 700;
    color: rgba(26, 16, 0, var(--lcd-alpha-active));
    background: transparent;
    border: 1.5px solid rgba(26, 16, 0, calc(var(--lcd-alpha-active) * 0.3));
    border-radius: 3px;
    padding: 2px 6px;
    margin-right: 4px;
    cursor: pointer;
    min-width: 36px;
    text-align: center;
  }
  .lcd-meter-src-btn:hover {
    border-color: rgba(26, 16, 0, calc(var(--lcd-alpha-active) * 0.5));
  }

  /* ── RIT row (inline within cockpit) ── */
  .lcd-rit-row {
    display: flex;
    gap: 6px;
    align-items: baseline;
    position: relative;
    z-index: 2;
  }

  .rit-label {
    font-family: 'JetBrains Mono', 'Courier New', monospace;
    font-size: 12px;
    font-weight: 700;
    color: rgba(26, 16, 0, calc(var(--lcd-alpha-active) * 0.5));
  }

  .rit-value {
    font-family: 'DSEG7 Classic', monospace;
    font-weight: bold;
    font-size: 16px;
    color: rgba(26, 16, 0, calc(var(--lcd-alpha-active) * 0.6));
  }

  /* ── Filter / AF Scope row (full-width grid cell) ── */
  .lcd-filter-row {
    position: relative;
    z-index: 2;
    min-height: 0;
    min-width: 0;
    overflow: hidden;
  }
  .lcd-scope-strip {
    position: relative;
    z-index: 2;
    width: 100%;
    height: 100%;
    min-height: 0;
  }

  /* ── Aux row (#836 memory / #837 telemetry, stacked) ── */
  .lcd-aux-row {
    display: flex;
    flex-direction: column;
    gap: 3px;
    min-width: 0;
    z-index: 2;
    position: relative;
  }

  /* ── TX glow ── */
  .tx-active .lcd-screen {
    border-color: #5A2000;
    box-shadow:
      inset 0 0 40px rgba(180, 30, 0, 0.08),
      0 0 10px rgba(180, 30, 0, 0.15);
  }

  /* ── Narrow-viewport collapse (issue #894) ──
     When the .amber-lcd container is ≤ 640px wide, collapse the dual-cockpit
     grid to a single column with VFO B stacked below VFO A.
     Desktop (> 640px) is completely unaffected — the rule never fires. */
  @container (max-width: 640px) {
    .lcd-screen.dual {
      grid-template-columns: minmax(0, 1fr);
      /* Explicit 5-track rows — adding vfo-b to the area list without updating
         rows would leave scope on the default auto track and hand the flexible
         minmax(0, 1fr) to vfo-b instead (codex P1 on PR #912). */
      grid-template-rows:
        auto               /* global indicator strip */
        auto               /* vfo-a cockpit */
        auto               /* vfo-b cockpit (stacked) */
        minmax(0, 1fr)     /* scope — keeps the flexible track */
        auto;              /* aux (reserved) */
      grid-template-areas:
        "global"
        "vfo-a"
        "vfo-b"
        "scope"
        "aux";
    }

    /* Remove the side-by-side column separator — it reads as an artifact
       when VFO B is stacked below VFO A instead of beside it. */
    .lcd-screen.dual .lcd-vfo-b {
      border-left: none;
      padding-left: 0;
    }
  }
</style>
