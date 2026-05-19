<script lang="ts">
  import { onMount } from 'svelte';
  import '../theme/index';
  import { setTheme, getTheme, setVfoTheme, getVfoTheme } from '../theme/theme-switcher';
  
  // Apply saved themes immediately (before any component renders)
  if (typeof window !== 'undefined') {
    setTheme(getTheme());
    const vfoTheme = getVfoTheme();
    if (vfoTheme) {
      setVfoTheme(vfoTheme);
    }
  }
  
  import { runtime } from '$lib/runtime';
  import { applyModeDefault } from '$lib/stores/tuning.svelte';
  import { getKeyboardConfig, hasCapability, hasAnyScope, hasSpectrum } from '$lib/stores/capabilities.svelte';
  import { getLayoutMode } from '$lib/stores/layout.svelte';
  import { resolveSkinId, type SkinId } from '../../skins/registry';
  import SpectrumPanel from '../../components/spectrum/SpectrumPanel.svelte';
  import LeftSidebar from './LeftSidebar.svelte';
  import RightSidebar from './RightSidebar.svelte';
  import VfoHeader from './VfoHeader.svelte';
  import SdrVfoScreen from '../../skins/sdr-test/SdrVfoScreen.svelte';
  import KeyboardHandler from './KeyboardHandler.svelte';
  import StatusBar from './StatusBar.svelte';
  import MetersDockPanel from '../panels/MetersDockPanel.svelte';
  import {
    parseVfoLayoutScaleOverrides,
    resolveVfoLayoutProfile,
    vfoLayoutStyleVars,
    type VfoLayoutScaleOverrides,
  } from './vfo-layout-tokens';
  import {
    toVfoProps, toVfoOpsProps,
    toRfFrontEndProps, toAgcProps, toRitXitProps,
    toBandSelectorProps, toDspProps, toCwProps,
  } from '../wiring/state-adapter';
  import {
    makeKeyboardHandlers, makeVfoHandlers,
    makeRfFrontEndHandlers, makeAgcHandlers, makeRitXitHandlers,
    makeBandHandlers, makePresetHandlers, makeDspHandlers, makeCwPanelHandlers,
    makeSystemHandlers,
  } from '../wiring/command-bus';
  import MobileRadioLayout from './MobileRadioLayout.svelte';
  import LcdLayout from './LcdLayout.svelte';
  import CollapsiblePanel from '../controls/CollapsiblePanel.svelte';
  import BandSelector from '../controls/BandSelector.svelte';
  import LanguageSelector from '../controls/LanguageSelector.svelte';
  import DspPanel from '../panels/DspPanel.svelte';
  import AgcPanel from '../panels/AgcPanel.svelte';
  import RfFrontEnd from '../panels/RfFrontEnd.svelte';
  import RitXitPanel from '../panels/RitXitPanel.svelte';
  import CwPanel from '../panels/CwPanel.svelte';
  import { HardwareButton } from '$lib/Button';
  import Toast from '../../components/shared/Toast.svelte';

  // Reactive state + capabilities — via runtime
  let radioState = $derived(runtime.state);
  let caps = $derived(runtime.caps);

  // Scope digest for VfoHeader bridge (issue #832).  Gate on `scope` capability;
  // VfoHeader treats null as "hide the block".
  let scopeStatus = $derived.by(() => {
    if (!hasCapability('scope')) return null;
    const sc = (radioState as { scopeControls?: { dual?: boolean; receiver?: number; span?: number; speed?: number } } | null)?.scopeControls;
    if (!sc) return null;
    return {
      dual: sc.dual ?? false,
      receiver: sc.receiver ?? 0,
      span: sc.span ?? 3,
      speed: sc.speed ?? 1,
    };
  });

  function handleScopeDualToggle(): void {
    const current = (radioState as { scopeControls?: { dual?: boolean } } | null)?.scopeControls?.dual ?? false;
    runtime.send('set_scope_dual', { dual: !current });
  }

  function handleScopeReceiverChange(receiver: 0 | 1): void {
    runtime.send('switch_scope_receiver', { receiver });
  }
  let keyboardConfig = $derived(getKeyboardConfig());
  let activeMode = $derived(radioState?.active === 'SUB' ? radioState?.sub?.mode : radioState?.main?.mode);

  // Derived props via state adapter
  let mainVfo = $derived(toVfoProps(radioState, 'main'));
  let subVfo = $derived(toVfoProps(radioState, 'sub'));
  let vfoOps = $derived(toVfoOpsProps(radioState, caps));
  let windowWidth = $state(typeof window !== 'undefined' ? window.innerWidth : 1200);
  let windowHeight = $state(typeof window !== 'undefined' ? window.innerHeight : 800);
  // Mobile = narrow portrait OR short landscape (touch device rotated)
  let isMobile = $derived(
    Math.min(windowWidth, windowHeight) < 640 ||
    ('ontouchstart' in globalThis && Math.min(windowWidth, windowHeight) < 500)
  );
  let isLandscape = $state(false);
  let landscapeSpectrumDismissed = $state(false);
  let landscapeAutoLocked = $state(false);
  let spectrumLandscape = $derived(isMobile && isLandscape && !landscapeSpectrumDismissed && !landscapeAutoLocked);
  let connectionStatus = $derived(runtime.connectionStatus);

  // Skin resolution — determines which layout to render
  let skinId = $derived<SkinId>(resolveSkinId({
    capabilities: caps,
    layoutPreference: getLayoutMode(),
    isMobile,
    hasAnyScope: hasAnyScope(),
  }));

  let activeReceiverLabel = $derived(radioState?.active === 'SUB' ? 'SUB' : 'MAIN');
  let activeModeLabel = $derived(radioState?.active === 'SUB' ? (radioState?.sub?.mode ?? '') : (radioState?.main?.mode ?? ''));
  let activeFilterLabel = $derived(radioState?.active === 'SUB' ? (radioState?.sub?.filter ?? '') : (radioState?.main?.filter ?? ''));
  let activeFreq = $derived(radioState?.active === 'SUB' ? (radioState?.sub?.freqHz ?? 0) : (radioState?.main?.freqHz ?? 0));
  let receiverDeckElement = $state<HTMLElement | null>(null);
  let receiverDeckWidth = $state<number | null>(null);
  let manualVfoScaleOverrides = $state<VfoLayoutScaleOverrides>({});
  let vfoLayoutProfile = $derived(resolveVfoLayoutProfile(receiverDeckWidth));
  let receiverDeckStyle = $derived(vfoLayoutStyleVars(vfoLayoutProfile, {
    width: receiverDeckWidth,
    overrides: manualVfoScaleOverrides,
  }));

  // Derived props for settings modal
  let rfFrontEnd = $derived(toRfFrontEndProps(radioState, caps));
  let agc = $derived(toAgcProps(radioState, caps));
  let ritXit = $derived(toRitXitProps(radioState, caps));
  let band = $derived(toBandSelectorProps(radioState));
  let dsp = $derived(toDspProps(radioState, caps));
  let cw = $derived(toCwProps(radioState, caps));

  // Command handlers via command-bus
  const vfoHandlers = makeVfoHandlers();
  const keyboardHandlers = makeKeyboardHandlers();
  const rfHandlers = makeRfFrontEndHandlers();
  const agcHandlers = makeAgcHandlers();
  const ritXitHandlers = makeRitXitHandlers();
  const bandHandlers = makeBandHandlers();
  const presetHandlers = makePresetHandlers();
  const systemHandlers = makeSystemHandlers();
  const dspHandlers = makeDspHandlers();
  const cwHandlers = makeCwPanelHandlers();

  // Settings modal state
  let settingsOpen = $state(false);

  $effect(() => {
    if (activeMode) {
      applyModeDefault(activeMode);
    }
  });

  $effect(() => {
    if (!isLandscape) {
      landscapeSpectrumDismissed = false;
    }
  });

  onMount(() => {
    // Theme already applied at module load
    manualVfoScaleOverrides = parseVfoLayoutScaleOverrides(window.location.search);

    const handleResize = () => {
      windowWidth = window.innerWidth;
      windowHeight = window.innerHeight;
    };
    window.addEventListener('resize', handleResize);

    const mql = window.matchMedia?.('(orientation: landscape)');
    const handleOrientationChange = (e: MediaQueryListEvent) => {
      isLandscape = e.matches;
    };

    if (mql) {
      isLandscape = mql.matches;
      mql.addEventListener('change', handleOrientationChange);
    }

    if (!receiverDeckElement) {
      return () => {
        window.removeEventListener('resize', handleResize);
        mql?.removeEventListener('change', handleOrientationChange);
      };
    }

    receiverDeckWidth = receiverDeckElement.getBoundingClientRect().width || receiverDeckElement.clientWidth || null;

    if (typeof ResizeObserver === 'undefined') {
      return () => {
        window.removeEventListener('resize', handleResize);
        mql?.removeEventListener('change', handleOrientationChange);
      };
    }

    const observer = new ResizeObserver((entries) => {
      const entry = entries[0];
      if (!entry) {
        return;
      }

      receiverDeckWidth = entry.contentRect.width;
    });

    observer.observe(receiverDeckElement);
    return () => {
      window.removeEventListener('resize', handleResize);
      mql?.removeEventListener('change', handleOrientationChange);
      observer.disconnect();
    };
  });
</script>

{#if skinId === 'mobile'}
  <MobileRadioLayout />
{:else if skinId === 'lcd-cockpit'}
  <LcdLayout variant="cockpit" />
{:else if skinId === 'lcd-scope'}
  <LcdLayout variant="scope" />
{:else}
<div class="radio-layout" class:sdr-test={skinId === 'sdr-test'}>
  <StatusBar onSettings={() => (settingsOpen = true)} />
  <KeyboardHandler config={keyboardConfig} onAction={keyboardHandlers.dispatch} />

  <section class="receiver-deck" bind:this={receiverDeckElement} style={receiverDeckStyle}>
    {#if skinId === 'sdr-test'}
      <SdrVfoScreen
        {mainVfo}
        {subVfo}
        splitActive={vfoOps.splitActive}
        dualWatchActive={vfoOps.dualWatch}
        txVfo={vfoOps.txVfo}
        extras={{
          tunerStatus: (radioState as { tunerStatus?: number } | null)?.tunerStatus,
          xitActive: (radioState as { ritTx?: boolean } | null)?.ritTx,
          xitOffset: (radioState as { ritFreq?: number } | null)?.ritFreq,
          txAntenna: (radioState as { txAntenna?: number } | null)?.txAntenna,
          main: (radioState as { main?: Record<string, unknown> } | null)?.main ?? null,
          sub: (radioState as { sub?: Record<string, unknown> } | null)?.sub ?? null,
        }}
        onSwap={vfoHandlers.onSwap}
        onEqual={vfoHandlers.onEqual}
        onSplitToggle={vfoHandlers.onSplitToggle}
        onDualWatchToggle={vfoHandlers.onDualWatchToggle}
        onMainVfoClick={vfoHandlers.onMainVfoClick}
        onSubVfoClick={vfoHandlers.onSubVfoClick}
        onMainModeClick={vfoHandlers.onMainModeClick}
        onSubModeClick={vfoHandlers.onSubModeClick}
        onSpeak={systemHandlers.onSpeak}
      />
    {:else}
      <VfoHeader
        {mainVfo}
        {subVfo}
        layoutProfile={vfoLayoutProfile}
        splitActive={vfoOps.splitActive}
        dualWatchActive={vfoOps.dualWatch}
        txVfo={vfoOps.txVfo}
        onSwap={vfoHandlers.onSwap}
        onEqual={vfoHandlers.onEqual}
        onSplitToggle={vfoHandlers.onSplitToggle}
        onQuickSplit={vfoHandlers.onQuickSplit}
        onDualWatchToggle={vfoHandlers.onDualWatchToggle}
        onQuickDw={vfoHandlers.onQuickDw}
        onMainVfoClick={vfoHandlers.onMainVfoClick}
        onSubVfoClick={vfoHandlers.onSubVfoClick}
        onMainModeClick={vfoHandlers.onMainModeClick}
        onMainFreqChange={vfoHandlers.onMainFreqChange}
        onSubFreqChange={vfoHandlers.onSubFreqChange}
        onSubModeClick={vfoHandlers.onSubModeClick}
        onSpeak={systemHandlers.onSpeak}
        {scopeStatus}
        onScopeDualToggle={handleScopeDualToggle}
        onScopeReceiverChange={handleScopeReceiverChange}
      />
    {/if}
  </section>

  <section class="content-row">
    <div class="content-left">
      <LeftSidebar />
    </div>

    <main class="content-center center-column">
      {#if hasSpectrum()}
        <div class="spectrum-slot">
          <div class="spectrum-frame">
            <!-- Desktop: VfoHeader bridge owns DUAL + MAIN/SUB (#832); hide
                 the toolbar duplicate. Mobile/v1 layouts omit the prop so
                 the toolbar retains them (#832 fallback). -->
            <SpectrumPanel hideSourceControls={true} />
          </div>
        </div>
      {/if}
    </main>

    <div class="content-right">
      <RightSidebar />
    </div>
  </section>

  <section class="bottom-dock">
    <MetersDockPanel
      sValue={radioState?.active === 'SUB' ? radioState?.sub?.sMeter : radioState?.main?.sMeter}
      powerMeter={radioState?.powerMeter}
      swrMeter={radioState?.swrMeter}
      alcMeter={radioState?.alcMeter}
      idMeter={radioState?.idMeter}
      vdMeter={radioState?.vdMeter}
      compMeter={radioState?.compMeter}
      compressorOn={radioState?.compressorOn}
      txActive={radioState?.ptt ?? false}
    />
  </section>
</div>
{/if}

<!-- Toast notifications — rendered in fixed position overlay -->
<Toast />

{#if runtime.radioPowerOn === false}
  <div class="power-off-overlay" role="dialog" aria-modal="true" aria-label="Radio is powered off">
    <div class="power-off-content">
      <svg width="48" height="48" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
        <path d="M18.36 6.64a9 9 0 1 1-12.73 0" />
        <line x1="12" y1="2" x2="12" y2="12" />
      </svg>
      <span class="power-off-label">Radio is powered off</span>
      <span class="power-off-hint">Use the ON button in the status bar to power up</span>
    </div>
  </div>
{/if}

<!-- ═══ SETTINGS MODAL (outside power-off block so it works when radio is on) ═══ -->
{#if settingsOpen}
  <!-- svelte-ignore a11y_no_static_element_interactions -->
  <div class="settings-backdrop" onclick={() => (settingsOpen = false)} onkeydown={(e) => { if (e.key === 'Escape') settingsOpen = false; }}>
    <!-- svelte-ignore a11y_no_static_element_interactions -->
    <div class="settings-modal" role="dialog" aria-modal="true" aria-label="Settings" tabindex="-1" onclick={(e) => e.stopPropagation()} onkeydown={(e) => { if (e.key === 'Escape') settingsOpen = false; }}>
      <div class="settings-header">
        <span class="settings-title">SETTINGS</span>
        <button class="settings-close" onclick={() => (settingsOpen = false)}>✕</button>
      </div>
      <div class="settings-content">
        <CollapsiblePanel title="LANGUAGE" panelId="desktop-language">
          <LanguageSelector />
        </CollapsiblePanel>

        <CollapsiblePanel title="VFO / BAND" panelId="desktop-vfo-ops">
          <div class="settings-vfo-ops-row">
            <HardwareButton
              active={vfoOps.splitActive}
              indicator="edge-left"
              color={vfoOps.splitActive ? 'yellow' : 'gray'}
              onclick={vfoHandlers.onSplitToggle}
            >
              SPLIT
            </HardwareButton>
            <HardwareButton
              indicator="edge-left"
              color="cyan"
              onclick={vfoHandlers.onSwap}
            >
              A↔B
            </HardwareButton>
            <HardwareButton
              indicator="edge-left"
              color="cyan"
              onclick={vfoHandlers.onEqual}
            >
              A=B
            </HardwareButton>
          </div>
          <BandSelector />
        </CollapsiblePanel>

        <CollapsiblePanel title="DSP" panelId="desktop-dsp">
          <DspPanel />
        </CollapsiblePanel>

        <CollapsiblePanel title="AGC" panelId="desktop-agc">
          <AgcPanel />
        </CollapsiblePanel>

        <CollapsiblePanel title="RF FRONT END" panelId="desktop-rf">
          <RfFrontEnd />
        </CollapsiblePanel>

        <CollapsiblePanel title="RIT / XIT" panelId="desktop-rit">
          <RitXitPanel />
        </CollapsiblePanel>

        <CollapsiblePanel
          title="CW"
          panelId="desktop-cw"
          autoCollapseWhen={activeMode !== 'CW' && activeMode !== 'CW-R'}
        >
          <CwPanel />
        </CollapsiblePanel>
      </div>
    </div>
  </div>
{/if}

<style>
  .radio-layout {
    position: relative;
    display: grid;
    grid-template-rows: 28px 200px minmax(0, 1fr) auto;
  }
  .radio-layout.sdr-test {
    grid-template-rows: 28px 280px minmax(0, 1fr) auto;
  }
  /* Wide-viewport promotion: sidebars move up to flank the VFO row.
     Below 1680px we keep the stacked layout (VFO full-width, sidebars below). */
  @media (min-width: 1680px) {
    .radio-layout.sdr-test {
      grid-template-columns: 228px minmax(0, 1fr) 228px;
      grid-template-rows: 28px 280px minmax(0, 1fr) auto;
      grid-template-areas:
        "status status status"
        "left   deck   right"
        "left   center right"
        "dock   dock   dock";
    }
    .radio-layout.sdr-test > :global(.status-bar) { grid-area: status; }
    .radio-layout.sdr-test > .receiver-deck { grid-area: deck; }
    .radio-layout.sdr-test > .bottom-dock { grid-area: dock; }
    /* Flatten content-row so its children become direct grid items. */
    .radio-layout.sdr-test > .content-row {
      display: contents;
    }
    .radio-layout.sdr-test > .content-row > .content-left { grid-area: left; }
    .radio-layout.sdr-test > .content-row > .content-right { grid-area: right; }
    .radio-layout.sdr-test > .content-row > .content-center { grid-area: center; }
  }
  .radio-layout, .radio-layout.sdr-test {
    height: 100vh;
    background:
      linear-gradient(180deg, var(--v2-bg-gradient-start) 0%, var(--v2-bg-darkest) 100%),
      var(--v2-bg-app, var(--v2-bg-darker));
    gap: 5px;
    padding: 5px;
    box-sizing: border-box;
  }

  .receiver-deck,
  .content-left,
  .content-right,
  .spectrum-frame {
    border: 1px solid var(--v2-border-panel);
    border-radius: 4px;
    background:
      linear-gradient(180deg, var(--v2-panel-bg-gradient-top) 0%, var(--v2-panel-bg-gradient-bottom) 100%);
    box-shadow: var(--v2-shadow-sm);
  }

  .receiver-deck {
    position: relative;
    overflow: hidden;
    padding: 5px;
    min-height: 0;
    border-color: var(--v2-border-panel);
  }

  .receiver-deck :global(.vfo-header) {
    height: 100%;
  }

  .content-row {
    display: grid;
    grid-template-columns: 228px minmax(0, 1fr) 228px;
    grid-template-rows: minmax(0, 1fr);
    gap: 5px;
    min-height: 0;
    overflow: hidden;
  }

  .content-left,
  .content-right {
    min-height: 0;
    max-height: 100%;
    overflow-y: auto;
    overflow-x: hidden;
    padding-bottom: 4px;
    /* Hide scrollbar but keep scroll functionality */
    scrollbar-width: none; /* Firefox */
    -ms-overflow-style: none; /* IE/Edge */
  }

  .content-left::-webkit-scrollbar,
  .content-right::-webkit-scrollbar {
    display: none; /* Chrome/Safari/Opera */
  }

  .content-center {
    min-height: 0;
    min-width: 0;
    display: flex;
  }

  .spectrum-slot {
    flex: 1;
    min-height: 0;
    min-width: 0;
    display: flex;
  }

  .spectrum-frame {
    flex: 1;
    min-height: 0;
    min-width: 0;
    overflow: hidden;
    background: var(--v2-bg-card);
    border-color: var(--v2-border-darker);
  }

  .spectrum-frame :global(.spectrum-panel) {
    height: 100%;
    border: none;
    border-radius: 0;
    box-shadow: none;
  }



  .content-left :global(.left-sidebar),
  .content-right :global(.right-sidebar) {
    min-height: 0;
  }

  .bottom-dock {
    display: flex;
    align-items: stretch;
    gap: 6px;
    min-height: 112px;
    padding: 6px 8px;
    box-sizing: border-box;
  }

  @media (max-width: 1200px) {
    .content-row {
      grid-template-columns: 208px minmax(0, 1fr) 208px;
    }
  }

  @media (max-width: 1024px) {
    .radio-layout {
      grid-template-rows: 28px auto minmax(0, auto) auto auto;
    }

    .content-row {
      grid-template-columns: 1fr;
      overflow-y: auto;
    }

    .bottom-dock {
      flex-direction: column;
    }
  }

  /* Mobile layout is now in MobileRadioLayout.svelte */

  /* Power-off overlay */
  .power-off-overlay {
    position: fixed;
    inset: 28px 0 0 0; /* below status bar */
    z-index: 1000;
    background: rgba(0, 0, 0, 0.85);
    display: flex;
    align-items: center;
    justify-content: center;
    backdrop-filter: blur(4px);
  }

  .power-off-content {
    display: flex;
    flex-direction: column;
    align-items: center;
    gap: 16px;
    color: var(--v2-accent-red, #ef4444);
    animation: pulse-dim 2s ease-in-out infinite;
  }

  .power-off-label {
    font-size: 24px;
    font-weight: 700;
    letter-spacing: 0.1em;
    text-transform: uppercase;
    color: var(--v2-text-primary, #fff);
  }

  .power-off-hint {
    font-size: 13px;
    color: var(--v2-text-dim, #888);
    letter-spacing: 0.02em;
  }

  @keyframes pulse-dim {
    0%, 100% { opacity: 0.6; }
    50% { opacity: 1; }
  }

  @media (prefers-reduced-motion: reduce) {
    .power-off-content {
      animation: none;
    }
  }

  /* ── Settings Modal ── */
  .settings-backdrop {
    position: fixed;
    inset: 0;
    z-index: 200;
    background: rgba(0, 0, 0, 0.6);
    backdrop-filter: blur(3px);
    display: flex;
    align-items: center;
    justify-content: center;
  }

  .settings-modal {
    width: 90%;
    max-width: 700px;
    max-height: 85vh;
    background: var(--v2-bg-primary, #0f0f1a);
    border: 1px solid var(--v2-border-panel, #333);
    border-radius: 8px;
    overflow: hidden;
    display: flex;
    flex-direction: column;
    box-shadow: 0 8px 32px rgba(0, 0, 0, 0.5);
  }

  .settings-header {
    display: flex;
    align-items: center;
    justify-content: space-between;
    padding: 16px 20px;
    border-bottom: 1px solid var(--v2-border-darker, #222);
    background: var(--v2-bg-darker, #16162a);
  }

  .settings-title {
    font-family: 'Roboto Mono', monospace;
    font-size: 14px;
    font-weight: 700;
    letter-spacing: 0.12em;
    color: var(--v2-text-secondary, #aaa);
  }

  .settings-close {
    width: 32px;
    height: 32px;
    border: 1px solid var(--v2-border-panel, #333);
    border-radius: 4px;
    background: transparent;
    color: var(--v2-text-dim, #666);
    font-size: 18px;
    cursor: pointer;
    transition: all 150ms;
    display: flex;
    align-items: center;
    justify-content: center;
  }

  .settings-close:hover {
    background: var(--v2-accent-red, #ef4444);
    color: white;
    border-color: var(--v2-accent-red, #ef4444);
  }

  .settings-content {
    flex: 1;
    overflow-y: auto;
    padding: 16px;
    display: flex;
    flex-direction: column;
    gap: 12px;
  }

  .settings-vfo-ops-row {
    display: flex;
    gap: 8px;
    padding: 8px 0;
  }
</style>
