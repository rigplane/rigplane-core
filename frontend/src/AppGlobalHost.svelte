<!--
  App-global status host (MOR-1059).

  Owns the operator's global surfaces — feedback (toasts), power/health, and
  the authoritative TX/fault indication — at the App composition root, above
  the presentation boundary. Layouts and skins must not host these: a
  presentation swap replaces the layout subtree, and a global surface mounted
  inside it would be recreated, drop its subscription, or duplicate itself.

  See docs/plans/2026-07-25-ui-composition-architecture-v3.md
  ("render safety/status overlays outside the selected layout").
-->
<script lang="ts">
  import { onDestroy } from 'svelte';
  import Toast from './components/shared/Toast.svelte';
  import ConfirmDialog from './components-v2/dialogs/ConfirmDialog.svelte';
  import { runtime } from '$lib/runtime';
  import { getManagedAppTxController } from '$lib/runtime/tx-controller/managed-app-host';
  import { t } from '$lib/i18n';

  let {
    showTxIndication = true,
  }: { showTxIndication?: boolean } = $props();

  // The App-owned TX controller (MOR-1008/MOR-982) is the ONLY legitimate
  // source for this lamp. Radio-state PTT is a command/readback echo that can
  // read RX while the key is still down, so it is never consulted here.
  const tx = getManagedAppTxController();
  let txState = $state.raw(tx.snapshot());
  const stopWatchingTx = tx.subscribe((next) => { txState = next; });
  onDestroy(() => stopWatchingTx());

  // Fail closed: server-reported intent/debt without confirmed readback stays lit —
  // MOR-2671: it reads `TX`, distinguished from confirmed TX by the hollow lamp and
  // the unconfirmed accessible sentence, never by a `?` in the text.
  let txIndication = $derived(
    txState.radioTx === 'on' || txState.txRisk === 'confirmed-on'
      ? 'on'
      : txState.txRisk === 'uncertain'
        ? 'uncertain'
        : null,
  );

  // A failed power-on from the powered-off screen is reported inside
  // the page — never via a native alert. The status bar moved off
  // native dialogs after one RigPlane Pro build where the native
  // confirm did not appear and DISCONNECT did nothing; this surface
  // follows the same rule, reusing ConfirmDialog's error state.
  let powerOnFailed = $state<string | null>(null);

  // MOR-1240: while powered off, the overlay's top edge follows the
  // layout's real status bar through CSS alone: StatusBar publishes its
  // bottom edge as a document-level custom property
  // (`--rp-status-bar-bottom`, see StatusBar.svelte) while mounted, and the
  // overlay consumes it with `top: var(--rp-status-bar-bottom, 0px)`. A
  // layout without a bar (phone, dual-receiver-cockpit, flagship-probe)
  // never sets the property, so the fallback keeps the overlay full-screen
  // with no per-layout list here. The host measures nothing: a host-side
  // lookup raced the lazily loaded presentation and missed bar moves that
  // resize nothing (the link-lost row above the bar).

  // MOR-2841 (owner decision 2026-09-28, option (a)): a radio that answers
  // nothing at startup is the powered-off rig, and the server now serves in
  // a radio-not-answering state instead of aborting. The server's own
  // verdict is radioHealth.likelyCause 'radio_powered_off_likely' — held
  // from the gate's silent release until the radio's first observation,
  // whatever the live link state does in between (the watchdog reopens the
  // silent port to 'connected' within seconds, which round 2's
  // radioLink 'reconnecting' condition never saw). The overlay follows
  // that cause while power is not known ON, so Power ON stays reachable;
  // no reading is fabricated for the overlay. powerOn known-true or
  // known-false keeps its existing meanings.
  let radioNotAnswering = $derived(
    runtime.radioPowerOn !== true
      && runtime.radioHealth?.likelyCause === 'radio_powered_off_likely',
  );
  // MOR-2876: the server started while the radio's serial port could not be
  // opened and keeps retrying it ('radio_not_connected'). The same overlay
  // says so, without Power ON: there is no port to send it through, and the
  // server refuses power-on in that state.
  let radioNotConnected = $derived(
    runtime.radioPowerOn !== true
      && runtime.radioHealth?.likelyCause === 'radio_not_connected',
  );
  let overlayVisible = $derived(
    runtime.radioPowerOn === false || radioNotAnswering || radioNotConnected,
  );
  let overlayLabel = $derived(
    radioNotConnected
      ? 'core.overlay.poweredOff.notConnectedLabel'
      : radioNotAnswering
        ? 'core.overlay.poweredOff.notAnsweringLabel'
        : 'core.overlay.poweredOff.label',
  );
  // The Power ON action exists only where the profile binds a CI-V
  // power-on command (`power_on` in rigs/*.toml, published as
  // capabilities.powerOnCommand). Elsewhere the operator gets the plain
  // "cannot power on from here" sentence instead of a dead button.
  let powerOnCommand = $derived(runtime.caps?.powerOnCommand === true);

  async function handlePowerOn(): Promise<void> {
    try {
      await runtime.system.powerOn();
      powerOnFailed = null;
    } catch (err) {
      powerOnFailed = t('core.overlay.poweredOff.failedPowerOn', { detail: String(err) });
    }
  }
</script>

<div class="app-global-host" data-testid="app-global-host">
  <Toast />

  {#if showTxIndication && txIndication}
    <div
      class="global-tx" data-testid="global-tx-indication" data-tx={txIndication} aria-live="assertive"
      aria-label={txIndication === 'uncertain' ? t('core.rxTx.rf.unconfirmed') : undefined}
    >
      <span class="global-tx-lamp" class:hollow={txIndication === 'uncertain'} aria-hidden="true"></span>
      <span>TX</span>
    </div>
  {/if}

  {#if txState.fault}
    <div class="global-tx-fault" role="alert" data-testid="global-tx-fault" data-fault={txState.fault}>
      TX FAULT: {txState.fault}
    </div>
  {/if}

  {#if overlayVisible}
    <div
      class="power-off-overlay"
      style:top="var(--rp-status-bar-bottom, 0px)"
      role="dialog"
      aria-modal="true"
      data-testid="global-power-off"
      data-state={radioNotConnected ? 'not-connected' : radioNotAnswering ? 'not-answering' : 'powered-off'}
      aria-label={t(overlayLabel)}
    >
      <div class="power-off-content">
        <svg width="48" height="48" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
          <path d="M18.36 6.64a9 9 0 1 1-12.73 0" />
          <line x1="12" y1="2" x2="12" y2="12" />
        </svg>
        <span class="power-off-label">{t(overlayLabel)}</span>
        {#if powerOnCommand && !radioNotConnected}
          <button class="power-on-btn" onclick={handlePowerOn}>
            {t('core.overlay.poweredOff.powerOnButton')}
          </button>
        {:else if radioNotAnswering}
          <span class="power-off-hint">{t('core.overlay.poweredOff.noRemotePowerOn')}</span>
        {/if}
      </div>
    </div>
  {/if}

  <ConfirmDialog
    open={powerOnFailed !== null}
    message={t('core.overlay.poweredOff.label')}
    error={powerOnFailed}
    confirmLabel={t('common.action.ok')}
    cancelLabel={t('common.action.cancel')}
    onConfirm={() => { powerOnFailed = null; }}
    onCancel={() => { powerOnFailed = null; }}
  />
</div>

<style>
  /* The wrapper is a grouping node only — it must not participate in any
     layout's box model, and must not create a containing block that would
     break `position: fixed` on its children. */
  .app-global-host {
    display: contents;
  }

  .global-tx,
  .global-tx-fault {
    position: fixed;
    left: 50%;
    transform: translateX(-50%);
    z-index: 10001;
    pointer-events: none;
    font-family: var(--font-mono, 'Roboto Mono', monospace);
    font-weight: 700;
    letter-spacing: 0.12em;
  }

  .global-tx {
    top: 0;
    display: flex;
    align-items: center;
    gap: 6px;
    padding: 3px 12px;
    border: 1px solid var(--v2-accent-red, #ef4444);
    border-top: none;
    border-radius: 0 0 6px 6px;
    font-size: 12px;
    color: var(--v2-accent-red, #ef4444);
    background: rgba(0, 0, 0, 0.72);
  }

  .global-tx[data-tx='uncertain'] {
    border-color: var(--warning, #f59e0b);
    color: var(--warning, #f59e0b);
  }

  .global-tx-lamp {
    width: 8px;
    height: 8px;
    border-radius: 50%;
    box-sizing: border-box;
    background: currentColor;
    box-shadow: 0 0 6px currentColor;
  }

  /* MOR-2671: the unconfirmed lamp keeps the warning but is HOLLOW — a border
     ring where confirmed TX is a filled dot. The geometry distinction survives
     forced-colors: a border stays drawn even when backgrounds are suppressed. */
  .global-tx-lamp.hollow {
    background: transparent;
    border: 2px solid currentColor;
    box-shadow: none;
  }

  /* The slot is reserved at the widest text this lamp row can show (`TX`),
     so a transition between the two states never moves the centred badge. */
  .global-tx > span:last-child { min-inline-size: 2ch; }

  .global-tx-fault {
    top: 28px;
    padding: 3px 12px;
    border-radius: 4px;
    font-size: 11px;
    color: #fff;
    background: var(--danger, #b91c1c);
  }

  /* `inset: 0` keeps the overlay full-screen; the inline `top` (the only
     MOR-1240 edge) starts it below a mounted status bar via the
     document-level `--rp-status-bar-bottom` StatusBar publishes. */
  .power-off-overlay {
    position: fixed;
    inset: 0;
    z-index: 9000;
    display: flex;
    align-items: center;
    justify-content: center;
    background: rgba(0, 0, 0, 0.85);
    backdrop-filter: blur(6px);
  }

  .power-off-content {
    display: flex;
    flex-direction: column;
    align-items: center;
    gap: 12px;
    color: var(--v2-text-dim, #888);
  }

  .power-off-label {
    font-family: var(--font-mono, 'Roboto Mono', monospace);
    font-size: 16px;
    font-weight: 700;
    letter-spacing: 0.1em;
    color: var(--v2-text-primary, #fff);
  }

  .power-on-btn {
    margin-top: 8px;
    padding: 10px 24px;
    font-family: var(--font-mono, 'Roboto Mono', monospace);
    font-size: 14px;
    font-weight: 700;
    letter-spacing: 0.08em;
    color: #fff;
    background: rgba(40, 160, 40, 0.25);
    border: 1.5px solid rgba(40, 160, 40, 0.6);
    border-radius: 6px;
    cursor: pointer;
    transition: background 0.15s, border-color 0.15s;
  }

  .power-on-btn:hover {
    background: rgba(40, 160, 40, 0.4);
    border-color: rgba(40, 160, 40, 0.8);
  }

  .power-off-hint {
    max-width: 32ch;
    font-size: 13px;
    text-align: center;
    color: var(--v2-text-dim, #888);
  }
</style>
