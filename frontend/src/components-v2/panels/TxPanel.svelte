<script lang="ts">
  import { HardwareButton } from '$lib/Button';
  import { ValueControl, rawToPercentDisplay } from '../controls/value-control';
  import { txStatusColor } from './tx-utils';
  import { getTxAudioControl } from '$lib/runtime/adapters/tx-adapter';
  import { deriveTxProps, getTxHandlers } from '$lib/runtime/adapters/panel-adapters';

  const txAudio = getTxAudioControl();
  const handlers = getTxHandlers();
  let p = $derived(deriveTxProps());

  let txActive = $derived(p.txActive);
  let rfPower = $derived(p.rfPower);
  let micGain = $derived(p.micGain);
  let atuActive = $derived(p.atuActive);
  let atuTuning = $derived(p.atuTuning);
  let voxActive = $derived(p.voxActive);
  let compActive = $derived(p.compActive);
  let compLevel = $derived(p.compLevel);
  let monActive = $derived(p.monActive);
  let monLevel = $derived(p.monLevel);
  let driveGain = $derived(p.driveGain);
  const onRfPowerChange = handlers.onRfPowerChange;
  const onMicGainChange = handlers.onMicGainChange;
  const onAtuToggle = handlers.onAtuToggle;
  const onAtuTune = handlers.onAtuTune;
  const onVoxToggle = handlers.onVoxToggle;
  const onCompToggle = handlers.onCompToggle;
  const onCompLevelChange = handlers.onCompLevelChange;
  const onMonToggle = handlers.onMonToggle;
  const onMonLevelChange = handlers.onMonLevelChange;
  const onDriveGainChange = handlers.onDriveGainChange;
  const onPttOn = handlers.onPttOn;
  const onPttOff = handlers.onPttOff;

  let tuneButtonColor = $derived(txStatusColor(atuActive, atuTuning));
  let showTx = $derived(p.hasTx);
  let showTuner = $derived(p.hasTuner);
  let showMon = $derived(p.hasMonitor);

  // ── PTT (hold-to-talk + double-tap latch) ──
  const PTT_DOUBLE_TAP_MS = 300;
  const PTT_SAFETY_MS = 3 * 60 * 1000;
  let pttMode = $state<'idle' | 'held' | 'latched'>('idle');
  let lastPttDown = 0;
  let pttStarting = $state(false);
  let txError = $state('');
  let pttPressActive = false;
  let txStartToken = 0;
  let pttSafetyTimer: ReturnType<typeof setTimeout> | null = null;

  function startPttSafety() {
    clearPttSafety();
    pttSafetyTimer = setTimeout(() => { pttMode = 'idle'; onPttOff?.(); txAudio.stopTx(); }, PTT_SAFETY_MS);
  }
  function clearPttSafety() {
    if (pttSafetyTimer) { clearTimeout(pttSafetyTimer); pttSafetyTimer = null; }
  }

  async function engageTx(token: number): Promise<boolean> {
    pttStarting = true;
    txError = '';
    const err = await txAudio.startTx();
    pttStarting = false;
    if (token !== txStartToken || pttMode !== 'held' || !pttPressActive) {
      if (!err) txAudio.stopTx();
      return false;
    }
    if (err) {
      txError = err;
      pttMode = 'idle';
      lastPttDown = 0;
      return false;
    }
    onPttOn?.();
    return true;
  }

  async function pttDown() {
    if (pttStarting) return;
    const now = Date.now();
    if (pttMode === 'latched') { pttPressActive = false; txStartToken += 1; pttMode = 'idle'; onPttOff?.(); txAudio.stopTx(); clearPttSafety(); return; }
    if (now - lastPttDown < PTT_DOUBLE_TAP_MS && pttMode === 'held') {
      pttPressActive = false; pttMode = 'latched'; startPttSafety(); lastPttDown = 0; return;
    }
    lastPttDown = now;
    pttPressActive = true;
    pttMode = 'held';
    const token = ++txStartToken;
    if (await engageTx(token)) {
      startPttSafety();
    }
  }

  function pttUp() {
    pttPressActive = false;
    txStartToken += 1;
    if (pttMode === 'held') {
      setTimeout(() => {
        if (pttMode === 'held') { pttMode = 'idle'; onPttOff?.(); txAudio.stopTx(); clearPttSafety(); }
      }, PTT_DOUBLE_TAP_MS);
    }
  }

  // Settings modal
  let settingsOpen = $state(false);
  let modalStyle = $state('');
  let modalAnchor: HTMLElement | undefined = $state();

  function openSettings(): void {
    if (modalAnchor) {
      const rect = modalAnchor.getBoundingClientRect();
      const w = 240;
      let left = rect.left;
      if (left + w > window.innerWidth - 8) left = window.innerWidth - 8 - w;
      if (left < 8) left = 8;
      modalStyle = `top: ${rect.bottom + 6}px; left: ${left}px; width: ${w}px;`;
    }
    settingsOpen = true;
  }

  // Long-press to open settings
  const LONG_PRESS_MS = 500;
  let lpTimer: ReturnType<typeof setTimeout> | null = null;
  let lpSuppressClick = false;

  function lpStart(): void {
    lpSuppressClick = false;
    lpTimer = setTimeout(() => {
      lpTimer = null;
      lpSuppressClick = true;
      openSettings();
    }, LONG_PRESS_MS);
  }
  function lpEnd(): void {
    if (lpTimer) { clearTimeout(lpTimer); lpTimer = null; }
  }
</script>

{#if showTx}
  <div class="tx-panel" bind:this={modalAnchor}>
    <!-- TX indicator strip -->
    <div class="tx-strip" class:tx-active={txActive}>
      {txActive ? '● TX' : '○ RX'}
    </div>

    <button
      class="ptt-button"
      class:ptt-held={pttMode === 'held'}
      class:ptt-latched={pttMode === 'latched'}
      aria-disabled={pttStarting}
      onpointerdown={(e) => { e.preventDefault(); pttDown(); }}
      onpointerup={(e) => { e.preventDefault(); pttUp(); }}
      onpointerleave={() => { if (pttMode === 'held') pttUp(); }}
    >
      {pttStarting ? 'MIC...' : pttMode === 'latched' ? 'TX 🔒' : pttMode === 'held' ? 'TX' : 'PTT'}
    </button>
    {#if txError}
      <div class="tx-error">{txError}</div>
    {/if}

    <div class="tx-button-grid">
      {#if showTuner}
        <HardwareButton
          active={atuActive}
          indicator="edge-left"
          color={atuActive ? 'green' : 'gray'}
          onclick={onAtuToggle}
        >
          ATU
        </HardwareButton>
        <HardwareButton
          active={atuTuning}
          indicator="edge-left"
          color={atuTuning ? 'red' : 'gray'}
          onclick={onAtuTune}
        >
          {atuTuning ? 'TUNING…' : 'TUNE'}
        </HardwareButton>
      {/if}

      <HardwareButton active={voxActive} indicator="edge-left" color="amber" onclick={onVoxToggle}>
        VOX
      </HardwareButton>
      <HardwareButton active={compActive} indicator="edge-left" color="amber" onclick={onCompToggle}>
        COMP{compActive && compLevel > 0 ? ` ${Math.round(compLevel / 2.55)}%` : ''}
      </HardwareButton>
      {#if showMon}
        <HardwareButton active={monActive} indicator="edge-left" color="amber" onclick={onMonToggle}>
          MON{monActive && monLevel > 0 ? ` ${Math.round(monLevel / 2.55)}%` : ''}
        </HardwareButton>
      {/if}
      <HardwareButton
        indicator="edge-left"
        color="gray"
        onclick={() => { if (!lpSuppressClick) openSettings(); lpSuppressClick = false; }}
        onpointerdown={lpStart}
        onpointerup={lpEnd}
        onpointercancel={lpEnd}
        onpointerleave={lpEnd}
      >
        ⚙ LEVELS
      </HardwareButton>
    </div>
  </div>
{/if}

{#if settingsOpen}
  <button type="button" class="modal-backdrop" aria-label="Close TX settings" onclick={() => (settingsOpen = false)}></button>
  <div class="tx-modal" role="dialog" aria-label="TX level settings" style={modalStyle}>
    <div class="modal-header">
      <span class="modal-title">TX LEVELS</span>
      <button class="modal-close" onclick={() => (settingsOpen = false)}>✕</button>
    </div>
    <div class="modal-body">
      <ValueControl label="RF Power" value={rfPower} min={0} max={255} step={1}
        renderer="hbar" displayFn={rawToPercentDisplay} accentColor="var(--v2-accent-red)"
        onChange={onRfPowerChange} variant="hardware-illuminated" />
      <ValueControl label="Mic Gain" value={micGain} min={0} max={255} step={1}
        renderer="hbar" displayFn={rawToPercentDisplay} accentColor="var(--v2-accent-orange)"
        onChange={onMicGainChange} variant="hardware-illuminated" />
      {#if compActive}
        <ValueControl label="Comp Level" value={compLevel} min={0} max={255} step={1}
          renderer="hbar" displayFn={rawToPercentDisplay} accentColor="var(--v2-accent-orange)"
          onChange={onCompLevelChange} variant="hardware-illuminated" />
      {/if}
      {#if showMon && monActive}
        <ValueControl label="Mon Level" value={monLevel} min={0} max={255} step={1}
          renderer="hbar" displayFn={rawToPercentDisplay} accentColor="var(--v2-accent-orange)"
          onChange={onMonLevelChange} variant="hardware-illuminated" />
      {/if}
      <ValueControl label="Drive Gain" value={driveGain} min={0} max={255} step={1}
        renderer="hbar" displayFn={rawToPercentDisplay} accentColor="var(--v2-accent-orange)"
        onChange={onDriveGainChange} variant="hardware-illuminated" />
    </div>
  </div>
{/if}

<style>
  .tx-panel {
    padding: 8px;
    display: flex;
    flex-direction: column;
    gap: 8px;
  }

  .tx-strip {
    text-align: center;
    font-family: 'Roboto Mono', monospace;
    font-size: 11px;
    font-weight: 700;
    letter-spacing: 0.1em;
    color: var(--v2-text-dim);
    padding: 3px 0;
    border-radius: 3px;
    border: 1px solid var(--v2-border);
    transition: all 0.15s;
  }

  .tx-strip.tx-active {
    color: var(--v2-accent-red, #ef4444);
    border-color: var(--v2-accent-red, #ef4444);
    background: rgba(239, 68, 68, 0.1);
    box-shadow: 0 0 8px rgba(239, 68, 68, 0.3);
  }

  .tx-button-grid {
    display: grid;
    grid-template-columns: 1fr 1fr;
    gap: 6px;
  }

  .ptt-button {
    width: 100%;
    padding: 10px 0;
    border: 2px solid var(--v2-accent-red, #ef4444);
    border-radius: 6px;
    background: transparent;
    color: var(--v2-accent-red, #ef4444);
    font-family: 'Roboto Mono', monospace;
    font-size: 14px;
    font-weight: 700;
    letter-spacing: 0.1em;
    cursor: pointer;
    user-select: none;
    touch-action: none;
    transition: all 0.15s;
  }

  .ptt-button:hover {
    background: rgba(239, 68, 68, 0.08);
  }

  .ptt-button[aria-disabled="true"] {
    cursor: wait;
    opacity: 0.7;
  }

  .ptt-button.ptt-held,
  .ptt-button.ptt-latched {
    background: var(--v2-accent-red, #ef4444);
    color: #fff;
    box-shadow: 0 0 12px rgba(239, 68, 68, 0.4);
  }

  .tx-error {
    color: var(--v2-accent-red, #ef4444);
    font-size: 11px;
    line-height: 1.35;
  }

  .tx-button-grid > :global(button) {
    min-height: 34px;
    font-size: 12px;
  }

  .modal-backdrop {
    position: fixed;
    inset: 0;
    z-index: 10000;
    background: rgba(0, 0, 0, 0.3);
    border: 0;
    padding: 0;
    margin: 0;
  }

  .tx-modal {
    position: fixed;
    z-index: 10001;
    box-sizing: border-box;
    min-width: 220px;
    max-width: 280px;
    background: var(--v2-bg-darkest);
    border: 1px solid var(--v2-border-darker);
    border-radius: 6px;
    box-shadow: 0 12px 32px rgba(0, 0, 0, 0.5);
  }

  .modal-header {
    display: flex;
    justify-content: space-between;
    align-items: center;
    padding: 8px 10px;
    border-bottom: 1px solid var(--v2-border);
  }

  .modal-title {
    font-family: 'Roboto Mono', monospace;
    font-size: 10px;
    font-weight: 700;
    letter-spacing: 0.08em;
    color: var(--v2-text-subdued);
    text-transform: uppercase;
  }

  .modal-close {
    background: none;
    border: none;
    color: var(--v2-text-dim);
    cursor: pointer;
    font-size: 14px;
    padding: 0 2px;
  }

  .modal-close:hover {
    color: var(--v2-accent-red);
  }

  .modal-body {
    display: flex;
    flex-direction: column;
    gap: 10px;
    padding: 10px;
  }
</style>
