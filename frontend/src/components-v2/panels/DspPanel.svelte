<script lang="ts">
  import { ValueControl, rawToPercentDisplay } from '../controls/value-control';
  import { HardwareButton } from '$lib/Button';
  import { buildNrOptions, buildNotchOptions } from './dsp-utils';
  import {
    AGC_TIME_LABELS,
    LONG_PRESS_MS,
    formatAgcTime,
    toggleNrMode,
    toggleNotchMode,
    isNrActive,
    isNotchActive,
  } from './dsp-panel-logic';

  import { deriveDspProps, getDspHandlers } from '$lib/runtime/adapters/panel-adapters';

  const handlers = getDspHandlers();
  let p = $derived(deriveDspProps());

  let nrMode = $derived(p.nrMode);
  let nrLevel = $derived(p.nrLevel);
  let nbActive = $derived(p.nbActive);
  let nbLevel = $derived(p.nbLevel);
  let nbDepth = $derived(p.nbDepth ?? 0);
  let nbWidth = $derived(p.nbWidth ?? 0);
  let notchMode = $derived(p.notchMode);
  let notchFreq = $derived(p.notchFreq);
  let manualNotchWidth = $derived(p.manualNotchWidth ?? 0);
  let agcTimeConstant = $derived(p.agcTimeConstant ?? 0);
  const onNrModeChange = handlers.onNrModeChange;
  const onNrLevelChange = handlers.onNrLevelChange;
  const onNbToggle = handlers.onNbToggle;
  const onNbLevelChange = handlers.onNbLevelChange;
  const onNbDepthChange = handlers.onNbDepthChange ?? (() => {});
  const onNbWidthChange = handlers.onNbWidthChange ?? (() => {});
  const onNotchModeChange = handlers.onNotchModeChange;
  const onNotchFreqChange = handlers.onNotchFreqChange;
  const onManualNotchWidthChange = handlers.onManualNotchWidthChange ?? (() => {});
  const onAgcTimeChange = handlers.onAgcTimeChange ?? (() => {});

  /* NOTCH_WIDTH_LABELS, AGC_TIME_LABELS imported from dsp-panel-logic */

  let showNr = $derived(p.hasNr);
  let showNb = $derived(p.hasNb);
  let showNotch = $derived(p.hasNotch ?? true);
  let showAutoNotch = $derived(p.hasAutoNotch ?? true);
  let showAgcTime = $derived(p.hasAgcTime ?? true);

  let nrOptions = $derived(buildNrOptions());
  let notchOptions = $derived(buildNotchOptions());

  let nrActive = $derived(isNrActive(nrMode));
  let notchToggleActive = $derived(isNotchActive(notchMode));

  type ModalId = 'nr' | 'nb' | 'notch' | 'agc';
  let openModal = $state<ModalId | null>(null);
  let nrModalStyle = $state('');
  let nbModalStyle = $state('');
  let notchModalStyle = $state('');
  let agcModalStyle = $state('');

  let nrAnchorEl: HTMLDivElement | undefined = $state();
  let nbAnchorEl: HTMLDivElement | undefined = $state();
  let notchAnchorEl: HTMLDivElement | undefined = $state();
  let agcAnchorEl: HTMLDivElement | undefined = $state();

  /** Local NR mode for modal (supports 2 when server only reports on/off). */
  let nrModalMode = $state(0);
  let notchModalMode = $state<'off' | 'auto' | 'manual'>('off');

  function computeModalStyle(anchor: HTMLElement | undefined): string {
    if (!anchor) {
      return 'top: 8px; left: 8px; width: 220px;';
    }
    const rect = anchor.getBoundingClientRect();
    const menuWidth = 220;
    let left = rect.left;
    if (left + menuWidth > window.innerWidth - 8) {
      left = window.innerWidth - 8 - menuWidth;
    }
    if (left < 8) {
      left = 8;
    }
    const top = rect.bottom + 6;
    return `top: ${top}px; left: ${left}px; width: ${menuWidth}px;`;
  }

  function openModalFor(kind: ModalId): void {
    if (kind === 'nr') {
      nrModalMode = nrMode;
      nrModalStyle = computeModalStyle(nrAnchorEl);
    } else if (kind === 'nb') {
      nbModalStyle = computeModalStyle(nbAnchorEl);
    } else if (kind === 'agc') {
      agcModalStyle = computeModalStyle(agcAnchorEl);
    } else {
      notchModalMode = notchMode;
      notchModalStyle = computeModalStyle(notchAnchorEl);
    }
    openModal = kind;
  }

  function closeModal(): void {
    openModal = null;
  }

  function toggleNrShort(): void {
    onNrModeChange(toggleNrMode(nrMode));
  }

  function toggleNotchShort(): void {
    onNotchModeChange(toggleNotchMode(notchMode));
  }

  function handleNrModalMode(v: string | number): void {
    const n = v as number;
    nrModalMode = n;
    onNrModeChange(n);
  }

  function handleNotchModalMode(v: string | number): void {
    const m = v as 'off' | 'auto' | 'manual';
    notchModalMode = m;
    onNotchModeChange(m);
  }

  $effect(() => {
    if (openModal === 'nr') {
      nrModalStyle = computeModalStyle(nrAnchorEl);
    } else if (openModal === 'nb') {
      nbModalStyle = computeModalStyle(nbAnchorEl);
    } else if (openModal === 'notch') {
      notchModalStyle = computeModalStyle(notchAnchorEl);
    } else if (openModal === 'agc') {
      agcModalStyle = computeModalStyle(agcAnchorEl);
    }
  });

  /* LONG_PRESS_MS imported from dsp-panel-logic */
  let longPressTimer: ReturnType<typeof setTimeout> | null = null;
  let suppressNextToggle: ModalId | null = null;

  function startLongPress(kind: ModalId): void {
    if (longPressTimer) {
      clearTimeout(longPressTimer);
    }
    longPressTimer = setTimeout(() => {
      longPressTimer = null;
      suppressNextToggle = kind;
      openModalFor(kind);
    }, LONG_PRESS_MS);
  }

  function endLongPressPointer(): void {
    if (longPressTimer) {
      clearTimeout(longPressTimer);
      longPressTimer = null;
    }
  }

  function onNrClick(): void {
    if (suppressNextToggle === 'nr') {
      suppressNextToggle = null;
      return;
    }
    toggleNrShort();
  }

  function onNbClick(): void {
    if (suppressNextToggle === 'nb') {
      suppressNextToggle = null;
      return;
    }
    onNbToggle(!nbActive);
  }

  function onNotchClick(): void {
    if (suppressNextToggle === 'notch') {
      suppressNextToggle = null;
      return;
    }
    toggleNotchShort();
  }
</script>

<div class="dsp-panel">
  <div class="dsp-button-grid">
    {#if showNb}
      <div class="dsp-btn-wrap" bind:this={nbAnchorEl}>
        <HardwareButton
          active={nbActive}
          indicator="edge-left"
          color="orange"
          title="NB — click to toggle; long-press for settings"
          onclick={onNbClick}
          onpointerdown={() => startLongPress('nb')}
          onpointerup={endLongPressPointer}
          onpointercancel={endLongPressPointer}
          onpointerleave={endLongPressPointer}
        >NB{nbActive ? ` ${rawToPercentDisplay(nbLevel)}` : ''}</HardwareButton>
      </div>
    {/if}

    {#if showNr}
      <div class="dsp-btn-wrap" bind:this={nrAnchorEl}>
        <HardwareButton
          active={nrActive}
          indicator="edge-left"
          color="cyan"
          title="NR — click to toggle; long-press for settings"
          onclick={onNrClick}
          onpointerdown={() => startLongPress('nr')}
          onpointerup={endLongPressPointer}
          onpointercancel={endLongPressPointer}
          onpointerleave={endLongPressPointer}
        >NR{nrActive ? ` ${nrLevel}` : ''}</HardwareButton>
      </div>
    {/if}

    {#if showNotch}
      <div class="dsp-btn-wrap" bind:this={notchAnchorEl}>
        <HardwareButton
          active={notchMode === 'manual'}
          indicator="edge-left"
          color="cyan"
          title="Manual Notch — click to toggle; long-press for settings"
          onclick={onNotchClick}
          onpointerdown={() => startLongPress('notch')}
          onpointerup={endLongPressPointer}
          onpointercancel={endLongPressPointer}
          onpointerleave={endLongPressPointer}
        >NOTCH</HardwareButton>
      </div>
    {/if}

    {#if showAutoNotch}
      <div class="dsp-btn-wrap">
        <HardwareButton
          active={notchMode === 'auto'}
          indicator="edge-left"
          color="green"
          title="Auto Notch"
          onclick={() => onNotchModeChange(notchMode === 'auto' ? 'off' : 'auto')}
        >A-NOTCH</HardwareButton>
      </div>
    {/if}

    {#if showAgcTime}
      <div class="dsp-btn-wrap" bind:this={agcAnchorEl}>
        <HardwareButton
          indicator="edge-left"
          color="gray"
          title="AGC Time — click for settings"
          onclick={() => openModalFor('agc')}
        >AGC-T {formatAgcTime(agcTimeConstant)}s</HardwareButton>
      </div>
    {/if}
  </div>
</div>

{#if openModal}
  <button
    type="button"
    class="menu-backdrop"
    aria-label="Close DSP settings"
    onclick={closeModal}
  ></button>
{/if}

{#if openModal === 'nr'}
  <div
    class="dsp-modal"
    role="dialog"
    aria-label="Noise reduction settings"
    style={nrModalStyle}
  >
    <div class="menu-title">Noise reduction</div>
    <div class="dsp-modal-block dsp-mode-grid">
      {#each nrOptions as option}
        <HardwareButton
          active={nrModalMode === option.value}
          indicator="edge-left"
          color="cyan"
          onclick={() => handleNrModalMode(option.value)}
        >
          {option.label}
        </HardwareButton>
      {/each}
    </div>
    <ValueControl
      label="NR Level"
      value={nrLevel}
      min={0}
      max={15}
      step={1}
      renderer="discrete"
      tickStyle="notch"
      accentColor="var(--v2-accent-cyan)"
      onChange={onNrLevelChange}
      variant="hardware-illuminated"
    />
  </div>
{/if}

{#if openModal === 'nb'}
  <div class="dsp-modal" role="dialog" aria-label="Noise blanker settings" style={nbModalStyle}>
    <div class="menu-title">Noise blanker</div>
    <div class="dsp-modal-block dsp-modal-row">
      <span class="dsp-modal-inline-label">NB</span>
      <HardwareButton indicator="edge-left" active={nbActive} color="orange" onclick={() => onNbToggle(!nbActive)}>
        {nbActive ? 'ON' : 'OFF'}
      </HardwareButton>
    </div>
    <ValueControl
      label="NB Level"
      value={nbLevel}
      min={0}
      max={255}
      step={1}
      renderer="hbar"
      displayFn={rawToPercentDisplay}
      accentColor="var(--v2-accent-yellow)"
      onChange={onNbLevelChange}
      variant="hardware-illuminated"
    />
    <ValueControl
      label="NB Depth"
      value={nbDepth}
      min={1}
      max={10}
      step={1}
      renderer="discrete"
      tickStyle="notch"
      accentColor="var(--v2-accent-orange)"
      onChange={onNbDepthChange}
      variant="hardware-illuminated"
    />
    <ValueControl
      label="NB Width"
      value={nbWidth}
      min={0}
      max={255}
      step={1}
      renderer="hbar"
      displayFn={rawToPercentDisplay}
      accentColor="var(--v2-accent-orange)"
      onChange={onNbWidthChange}
      variant="hardware-illuminated"
    />
  </div>
{/if}

{#if openModal === 'notch'}
  <div class="dsp-modal" role="dialog" aria-label="Notch filter settings" style={notchModalStyle}>
    <div class="menu-title">Notch</div>
    <div class="dsp-modal-block dsp-mode-grid">
      {#each notchOptions as option}
        <HardwareButton
          active={notchModalMode === option.value}
          indicator="edge-left"
          color="cyan"
          onclick={() => handleNotchModalMode(option.value)}
        >
          {option.label}
        </HardwareButton>
      {/each}
    </div>
    {#if notchModalMode === 'manual'}
      <ValueControl
        label="Notch Freq"
        value={notchFreq}
        min={0}
        max={3000}
        step={1}
        unit="Hz"
        renderer="hbar"
        accentColor="var(--v2-accent-cyan)"
        onChange={onNotchFreqChange}
        variant="hardware-illuminated"
      />
      <div class="dsp-modal-block dsp-mode-grid">
        <HardwareButton active={manualNotchWidth === 0} indicator="edge-left" color="cyan" onclick={() => onManualNotchWidthChange(0)}>WIDE</HardwareButton>
        <HardwareButton active={manualNotchWidth === 1} indicator="edge-left" color="cyan" onclick={() => onManualNotchWidthChange(1)}>MID</HardwareButton>
        <HardwareButton active={manualNotchWidth === 2} indicator="edge-left" color="cyan" onclick={() => onManualNotchWidthChange(2)}>NAR</HardwareButton>
      </div>
    {/if}
  </div>
{/if}

{#if openModal === 'agc'}
  <div class="dsp-modal" role="dialog" aria-label="AGC time settings" style={agcModalStyle}>
    <div class="menu-title">AGC Time Constant</div>
    <ValueControl
      label="AGC Time"
      value={agcTimeConstant}
      min={0}
      max={9}
      step={1}
      renderer="discrete"
      tickStyle="notch"
      displayFn={formatAgcTime}
      unit="s"
      accentColor="var(--v2-accent-cyan)"
      onChange={onAgcTimeChange}
      variant="hardware-illuminated"
    />
  </div>
{/if}

<style>
  .dsp-panel {
    padding: 8px 8px;
  }

  .dsp-button-grid {
    display: grid;
    grid-template-columns: 1fr 1fr;
    gap: 6px;
  }

  .dsp-btn-wrap {
    display: flex;
    min-width: 0;
  }

  .dsp-btn-wrap > :global(button) {
    flex: 1;
    min-height: 34px;
    font-size: 12px;
  }

  .menu-backdrop {
    position: fixed;
    inset: 0;
    z-index: 10000;
    background: var(--v2-attenuator-bg);
    border: 0;
    padding: 0;
    margin: 0;
  }

  .dsp-modal {
    position: fixed;
    z-index: 10001;
    box-sizing: border-box;
    min-width: min(200px, calc(100vw - 32px));
    max-width: min(240px, calc(100vw - 32px));
    padding: 8px;
    background: var(--v2-bg-darkest);
    border: 1px solid var(--v2-border-darker);
    border-radius: 4px;
    box-shadow: 0 10px 24px var(--v2-attenuator-shadow);
  }

  .menu-title {
    margin-bottom: 8px;
    color: var(--v2-text-subdued);
    font-family: 'Roboto Mono', monospace;
    font-size: 10px;
    font-weight: 700;
    letter-spacing: 0.08em;
    text-transform: uppercase;
  }

  .dsp-modal-block {
    margin-bottom: 8px;
  }

  .dsp-modal-block:last-child {
    margin-bottom: 0;
  }

  .dsp-modal-row {
    display: flex;
    flex-direction: row;
    align-items: center;
    gap: 8px;
  }

  .dsp-modal-inline-label {
    flex: 0 0 auto;
    color: var(--v2-text-header);
    font-family: 'Roboto Mono', monospace;
    font-size: 9px;
    font-weight: 700;
    letter-spacing: 0.08em;
  }

  .dsp-mode-grid {
    display: flex;
    gap: 4px;
  }

  .dsp-mode-grid > :global(button) {
    flex: 1 1 0;
    min-width: 0;
  }
</style>
