<script lang="ts">
  import { onDestroy, untrack } from 'svelte';
  import { HardwareButton } from '$lib/Button';
  import { ValueControl } from '../controls/value-control';
  import { normalizedPercentDisplay, rawToPercentDisplay } from '../../primitives/scalar/value-control-core';
  import { txStatusColor } from './tx-utils';
  import {
    deriveTxProps,
    getTxAuxControlFeedback,
    getTxHandlers,
  } from '$lib/runtime/adapters/panel-adapters';
  import {
    createContinuousScalar,
    createHBarContinuousScalarPolicy,
    type ContinuousScalarBinding,
  } from '../../primitives/scalar/continuous-scalar.svelte';
  import type {
    HBarIssuedStatusPresentation,
    HBarIssuedStatusSnapshot,
  } from '../controls/value-control/skin';
  import { getManagedAppTxController } from '$lib/runtime/tx-controller/managed-app-host';
  import { createManagedTxGesture } from '../wiring/managed-tx-gesture';
  import {
    deriveAutoLanModInputProps,
    setAutoLanModInputEnabled,
  } from '$lib/runtime/adapters/mod-input-auto.svelte';
  import { t } from '$lib/i18n';
  import ModInputTxWarning from './ModInputTxWarning.svelte';
  import ManagedTotControl from '../controls/ManagedTotControl.svelte';

  let { showManagedTotControl = false }: { showManagedTotControl?: boolean } = $props();

  const handlers = getTxHandlers();
  let p = $derived(deriveTxProps());
  // MOR-618: opt-in auto LAN MOD-input toggle (shown in the settings modal).
  let autoLan = $derived(deriveAutoLanModInputProps());

  let rfPower = $derived(p.rfPower);
  let atuActive = $derived(p.atuActive);
  let atuTuning = $derived(p.atuTuning);
  let voxActive = $derived(p.voxActive);
  let compActive = $derived(p.compActive);
  let compLevel = $derived(p.compLevel);
  let monActive = $derived(p.monActive);
  let monLevel = $derived(p.monLevel);
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

  let tuneButtonColor = $derived(txStatusColor(atuActive, atuTuning));
  let showTx = $derived(p.hasTx);
  let showTuner = $derived(p.hasTuner);
  let showMon = $derived(p.hasMonitor);
  let showVox = $derived(p.voxAvailable ?? true);
  let showComp = $derived(p.compAvailable ?? true);
  let rfPowerAvailable = $derived(p.rfPowerAvailable ?? true);
  let micGainAvailable = $derived(p.micGainAvailable ?? true);
  let compLevelAvailable = $derived(p.compLevelAvailable ?? true);
  let monLevelAvailable = $derived(p.monLevelAvailable ?? true);
  let driveGainAvailable = $derived(p.driveGainAvailable ?? true);

  let micGainFeedback = $derived(getTxAuxControlFeedback('micGain'));
  let driveGainFeedback = $derived(getTxAuxControlFeedback('driveGain'));
  let compLevelFeedback = $derived(getTxAuxControlFeedback('compressorLevel'));
  let monLevelFeedback = $derived(getTxAuxControlFeedback('monitorGain'));
  const rawTxLevelDisplay = (value: number): string =>
    Number.isFinite(value) ? rawToPercentDisplay(value) : '—';
  const txLevelPolicy = () => createHBarContinuousScalarPolicy({
    preview: 'optimistic', debounceMs: 50, describeTarget: rawToPercentDisplay,
  });
  const micGainBinding = createContinuousScalar(
    () => ({
      evidence: 'command-feedback', feedback: micGainFeedback, command: 'set_mic_gain',
      domain: { min: 0, max: 255, step: 1, defaultValue: null, fineStepDivisor: 10 },
      enabled: micGainAvailable, request: onMicGainChange,
    }),
    txLevelPolicy(),
  );
  const driveGainBinding = createContinuousScalar(
    () => ({
      evidence: 'command-feedback', feedback: driveGainFeedback, command: 'set_drive_gain',
      domain: { min: 0, max: 255, step: 1, defaultValue: null, fineStepDivisor: 10 },
      enabled: driveGainAvailable, request: onDriveGainChange,
    }),
    txLevelPolicy(),
  );
  const compLevelBinding = createContinuousScalar(
    () => ({
      evidence: 'command-feedback', feedback: compLevelFeedback, command: 'set_compressor_level',
      domain: { min: 0, max: 255, step: 1, defaultValue: null, fineStepDivisor: 10 },
      enabled: compLevelAvailable, request: onCompLevelChange,
    }),
    txLevelPolicy(),
  );
  const monLevelBinding = createContinuousScalar(
    () => ({
      evidence: 'command-feedback', feedback: monLevelFeedback, command: 'set_monitor_gain',
      domain: { min: 0, max: 255, step: 1, defaultValue: null, fineStepDivisor: 10 },
      enabled: monLevelAvailable, request: onMonLevelChange,
    }),
    txLevelPolicy(),
  );

  type TxLevelLane = 'micGain' | 'driveGain' | 'compressorLevel' | 'monitorGain';
  let issuedStatusText = $state<Record<TxLevelLane, string | null>>({
    micGain: null, driveGain: null, compressorLevel: null, monitorGain: null,
  });
  function formatIssuedStatus({ view, announcement }: Readonly<HBarIssuedStatusSnapshot>): string {
    return view.error === null
      ? announcement.message
      : `${announcement.message.replace(/[.!?]$/, '')}: ${view.error}`;
  }
  function issuedStatusPresentation(lane: TxLevelLane): Readonly<HBarIssuedStatusPresentation> {
    return {
      get text() { return issuedStatusText[lane]; },
      format: formatIssuedStatus,
      accept(text) { issuedStatusText[lane] = text; },
    };
  }
  const micGainStatus = issuedStatusPresentation('micGain');
  const driveGainStatus = issuedStatusPresentation('driveGain');
  const compLevelStatus = issuedStatusPresentation('compressorLevel');
  const monLevelStatus = issuedStatusPresentation('monitorGain');
  function consumeHiddenStatus(
    binding: ContinuousScalarBinding,
    presentation: Readonly<HBarIssuedStatusPresentation>,
  ): void {
    const view = binding.view;
    if (view.evidence !== 'command-feedback') return;
    const announcement = view.presentation.politeAnnouncement;
    if (announcement !== null) {
      presentation.accept(untrack(() => presentation.format({ view, announcement })));
    } else if (view.feedback.transitionId === null) {
      presentation.accept(null);
    }
  }
  const feedbackIntegratedControl = { 'feedback-policy': 'feedback-integrated' } as const;

  // Gesture timing is local; all displayed TX truth comes from the server snapshot.
  const tx = getManagedAppTxController();
  let txState = $state.raw(tx.snapshot());
  const stopWatchingTx = tx.subscribe((next) => { txState = next; });
  const ptt = createManagedTxGesture(
    {
      latched: () => tx.snapshot().intent === 'latched',
      transmitAvailable: () => tx.snapshot().fresh,
    },
    { pttOn: tx.pttOn, pttOff: tx.pttOff, transmitOn: tx.transmitOn, forceOff: tx.forceOff },
    {
      schedule: (callback, ms) => setTimeout(callback, ms),
      cancel: (handle) => clearTimeout(handle as ReturnType<typeof setTimeout>),
    },
  );
  const isPttKey = (event: KeyboardEvent) => event.key === ' ' || event.key === 'Enter';
  const pttKeyDown = (event: KeyboardEvent) => {
    if (!isPttKey(event) || event.repeat) return;
    event.preventDefault(); ptt.down();
  };
  const pttKeyUp = (event: KeyboardEvent) => {
    if (!isPttKey(event)) return;
    event.preventDefault(); ptt.up();
  };

  onDestroy(() => {
    stopWatchingTx();
    ptt.destroy();
    micGainBinding.destroy();
    driveGainBinding.destroy();
    compLevelBinding.destroy();
    monLevelBinding.destroy();
  });

  let owned = $derived(txState.intent === 'momentary');
  let starting = $derived(txState.phase === 'key-confirm-pending');
  let keyed = $derived(txState.phase === 'key-confirm-pending' || txState.phase === 'active');
  let latched = $derived(txState.intent === 'latched');
  let busy = $derived(txState.phase === 'releasing');
  let fault = $derived(txState.fault);
  // Only the managed projection may name RX/TX. Stale or uncertain stays unknown.
  let rf = $derived(
    txState.radioTx === 'on' || txState.txRisk === 'confirmed-on'
      ? 'on'
      : txState.fresh && txState.radioTx === 'off' && txState.txRisk === 'none'
        ? 'off'
        : 'unknown',
  );

  // Settings modal
  let settingsOpen = $state(false);
  let modalStyle = $state('');
  let modalAnchor: HTMLElement | undefined = $state();

  $effect(() => {
    if (!settingsOpen) {
      consumeHiddenStatus(micGainBinding, micGainStatus);
      consumeHiddenStatus(driveGainBinding, driveGainStatus);
      consumeHiddenStatus(compLevelBinding, compLevelStatus);
      consumeHiddenStatus(monLevelBinding, monLevelStatus);
      return;
    }
    if (!compActive) consumeHiddenStatus(compLevelBinding, compLevelStatus);
    if (!(showMon && monActive)) consumeHiddenStatus(monLevelBinding, monLevelStatus);
  });

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
    <div class="tx-strip" class:tx-active={rf === 'on'} data-testid="tx-strip" data-rf={rf}>
      {rf === 'on' ? '● TX' : rf === 'off' ? '○ RX' : '○ ---'}
    </div>

    <button
      class="ptt-button"
      class:ptt-held={owned && !latched}
      class:ptt-latched={latched}
      aria-disabled={starting || busy}
      onpointerdown={(e) => { e.preventDefault(); ptt.down(); }}
      onpointerup={(e) => { e.preventDefault(); ptt.up(); }}
      onpointerleave={() => ptt.cancel()}
      onpointercancel={() => ptt.cancel()}
      onlostpointercapture={() => ptt.cancel()}
      onkeydown={pttKeyDown}
      onkeyup={pttKeyUp}
      onblur={() => ptt.cancel()}
    >
      {latched ? 'TX 🔒' : starting ? 'MIC…' : keyed ? 'TX' : busy ? 'UNKEYING…' : 'PTT'}
    </button>
    {#if fault}
      <div class="tx-error" data-testid="tx-fault" data-fault={fault}>TX FAULT: {fault}</div>
    {/if}
    {#if showManagedTotControl}
      <ManagedTotControl />
    {/if}
    <!-- MOR-617: warn when TX was keyed with a non-LAN MOD input -->
    <ModInputTxWarning />

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

      {#if showVox}
        <HardwareButton active={voxActive} indicator="edge-left" color="amber" onclick={onVoxToggle}>
          VOX
        </HardwareButton>
      {/if}
      {#if showComp}
        <HardwareButton active={compActive} indicator="edge-left" color="amber" onclick={onCompToggle}>
          COMP{compActive && compLevel > 0 ? ` ${Math.round(compLevel / 2.55)}%` : ''}
        </HardwareButton>
      {/if}
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
      <ValueControl label="RF Power" value={rfPower} min={0} max={1} step={0.01}
        renderer="hbar" displayFn={normalizedPercentDisplay} accentColor="var(--v2-accent-red)"
        onChange={onRfPowerChange} variant="hardware-illuminated" disabled={!rfPowerAvailable} />
      <ValueControl {...feedbackIntegratedControl} label="Mic Gain" binding={micGainBinding}
        renderer="hbar" displayFn={rawTxLevelDisplay} accentColor="var(--v2-accent-orange)"
        issuedStatusPresentation={micGainStatus} variant="hardware-illuminated" />
      {#if compActive}
        <ValueControl {...feedbackIntegratedControl} label="Comp Level" binding={compLevelBinding}
          renderer="hbar" displayFn={rawTxLevelDisplay} accentColor="var(--v2-accent-orange)"
          issuedStatusPresentation={compLevelStatus} variant="hardware-illuminated" />
      {/if}
      {#if showMon && monActive}
        <ValueControl {...feedbackIntegratedControl} label="Mon Level" binding={monLevelBinding}
          renderer="hbar" displayFn={rawTxLevelDisplay} accentColor="var(--v2-accent-orange)"
          issuedStatusPresentation={monLevelStatus} variant="hardware-illuminated" />
      {/if}
      <ValueControl {...feedbackIntegratedControl} label="Drive Gain" binding={driveGainBinding}
        renderer="hbar" displayFn={rawTxLevelDisplay} accentColor="var(--v2-accent-orange)"
        issuedStatusPresentation={driveGainStatus} variant="hardware-illuminated" />
      {#if autoLan.available}
        <!-- MOR-618: opt-in auto LAN MOD-input for web TX (default OFF) -->
        <div class="auto-lan-section">
          <label class="auto-lan-row">
            <input
              type="checkbox"
              data-testid="auto-lan-toggle"
              checked={autoLan.enabled}
              onchange={(e) => setAutoLanModInputEnabled(e.currentTarget.checked)}
            />
            <span>{t('core.txGuard.autoLanLabel')}</span>
          </label>
          <p class="auto-lan-help">{t('core.txGuard.autoLanHelp')}</p>
        </div>
      {/if}
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

  .auto-lan-section {
    display: flex;
    flex-direction: column;
    gap: 4px;
    padding-top: 8px;
    border-top: 1px solid var(--v2-border);
  }

  .auto-lan-row {
    display: flex;
    align-items: center;
    gap: 6px;
    font-size: 11px;
    color: var(--v2-text-primary, #e5e7eb);
    cursor: pointer;
  }

  .auto-lan-row input {
    margin: 0;
    accent-color: var(--v2-accent-orange, #f59e0b);
  }

  .auto-lan-help {
    margin: 0;
    font-size: 10px;
    line-height: 1.35;
    color: var(--v2-text-dim, #888);
  }
</style>
