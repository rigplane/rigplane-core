<script lang="ts">
  import { onDestroy, onMount } from 'svelte';
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
  import { getKeyboardConfig, hasCapability, hasSpectrum } from '$lib/stores/capabilities.svelte';
  import type { SkinId } from '../../skins/registry';
  import { declaredSurfaces, getLayout } from '../../presentation/layouts/contract';
  // Side-effect import: populates the LAYOUT registry `getLayout` resolves
  // against — the same idiom App.svelte and `semantic/design-language-renderers`
  // use for their registries. Imported HERE, in the shell that reads the
  // manifest, so a presentation resolved through the v3 path is self-sufficient:
  // whether the legacy twins are suppressed can never depend on some other
  // module having pulled the barrel in first.
  import '../../presentation/layouts/declarations';
  import SpectrumPanel from '../../components/spectrum/SpectrumPanel.svelte';
  import LeftSidebar from './LeftSidebar.svelte';
  import RightSidebar from './RightSidebar.svelte';
  import VfoHeader from './VfoHeader.svelte';
  import SemanticRadioSurfaces from '../wiring/SemanticRadioSurfaces.svelte';
  import { getAppTxController } from '$lib/runtime/tx-controller/app-host';
  import KeyboardHandler from './KeyboardHandler.svelte';
  import StatusBar from './StatusBar.svelte';
  import MetersDockPanel from '../panels/MetersDockPanel.svelte';
  import { t } from '$lib/i18n';
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
  import WorkspaceSettingsPanel from '../controls/WorkspaceSettingsPanel.svelte';
  import WorkspaceImportExport from '../controls/WorkspaceImportExport.svelte';
  import DspPanel from '../panels/DspPanel.svelte';
  import AgcPanel from '../panels/AgcPanel.svelte';
  import RfFrontEnd from '../panels/RfFrontEnd.svelte';
  import RitXitPanel from '../panels/RitXitPanel.svelte';
  import CwPanel from '../panels/CwPanel.svelte';
  import { HardwareButton } from '$lib/Button';

  let { skinId = 'desktop-v2' }: { skinId?: SkinId } = $props();

  // MOR-1313 (v3-rework slice S2) — PER-ZONE suppression, replacing the
  // MOR-1065 `skinId === 'sdr-test'` boolean. This shell hosts two areas that
  // have a semantic twin: the receiver deck (legacy `<VfoHeader>` vs the `vfo`
  // surface) and the sidebars' `<TxPanel>` (vs the `rxTx` surface). Which one
  // the semantic vertical owns is read off the ACTIVE layout manifest's zone
  // declarations: a surface some declared zone mounts renders semantically and
  // its legacy twin does NOT also render; a surface no zone declares keeps its
  // legacy presentation untouched.
  //
  // Both resolving families declare the full pair, so both are fully semantic:
  // `sdr-test` through one zone (`main: [vfo, rxTx]`) — its all-semantic
  // behavior is the DEGENERATE case of this rule, byte-identical to MOR-1065 —
  // and `desktop-v2` through two (`receiver-deck: [vfo]` + `rx-tx: [rxTx]`,
  // MOR-1266), which is what puts desktop-v2 on the v3 path.
  //
  // The MANIFEST is the authority, deliberately NOT the resolved surface plan
  // (`useSurfacePlan`, MOR-1082): the workspace may subtract a surface from a
  // zone, and letting a subtraction bring the legacy twin back would be
  // force-show through the back door — the one thing the plan may never do.
  let declared = $derived(declaredSurfaces(getLayout(skinId)));
  let semanticDeck = $derived(declared.has('vfo'));
  // R9 — ONE key/unkey authority, and this line is where that count is decided.
  //
  // It follows the DECK, not the `rxTx` declaration, and the asymmetry is
  // deliberate: `SemanticRadioSurfaces` is manifest-BLIND by design (importing a
  // manifest there would close the MOR-1068 cycle), so its single composition is
  // a hardcoded `['vfo', 'rxTx']` — mounting the semantic deck ALWAYS brings
  // exactly one `<RxTxSurface>` with it, whatever the manifest declared. Gating
  // the sidebars' TX twin on `declared.has('rxTx')` instead would therefore let
  // the two disagree: a manifest declaring `vfo` WITHOUT `rxTx` (which
  // `validateLayoutManifest` permits, and which the programme's additive
  // subset-declaration pattern positively invites) would render the semantic
  // RxTxSurface AND the legacy TxPanel — two key/unkey authorities, each holding
  // its own TX lease `sourceId`, so keying from one cannot be released by the
  // other. That is the stranded-transmitter hazard R9 and MOR-1011 exist to
  // prevent; the pre-MOR-1313 single boolean made it structurally impossible and
  // this rule keeps it so.
  //
  // Truth table, all four quadrants: deck mounted → semantic surface 1 / legacy
  // 0; deck not mounted → semantic 0 / legacy 1. Exactly one, always. Should
  // `SINGLE_COMPOSITION` ever become manifest-driven, THAT is the change that
  // earns this line a second term — not a subset manifest landing.
  let semanticRxTx = $derived(semanticDeck);

  // MOR-1341 (v3-rework S5) — same per-zone rule as `semanticRxTx` above,
  // applied to the bottom dock: the legacy `<MetersDockPanel>` retires the
  // moment a declared zone mounts the `meters` surface, and survives
  // untouched for any layout that declares no such zone. Unlike `vfo`/`rxTx`
  // this area carries no R9 stranded-transmitter hazard (a meter is a
  // readout, never a key/unkey affordance), so there is no asymmetric
  // "follow the deck, not the zone" rule to restate here — `declared` is the
  // whole answer.
  let semanticMeters = $derived(declared.has('meters'));

  // MOR-1364 (v3-rework S6-pre) — the ONE legacy-twin suppression channel.
  //
  // `declared` above is already the whole answer for every remaining legacy
  // twin, so nothing new is derived here: the set itself is handed to
  // `LeftSidebar`, `RightSidebar` and `StatusBar` (which each gate their own
  // panels on `!declared.has('<surface>')`), and the settings modal below —
  // this shell's own third copy of six of those panels — gates in place.
  // Landed INERT: no manifest declares any of `filter`/`rfFrontEnd`/`band`/
  // `antenna`/`ritXitScan`/`rxAudio`/`dsp`/`cwKeyer` yet, so every predicate
  // is `false` and the rendered tree is unchanged until S6a/S7/S8/S9 declare
  // the zones. The ONE exception is the modal's SPLIT/A↔B/A=B row, which
  // gates on the ALREADY-TRUE `semanticDeck` (S10 §4 — a real, deliberate
  // change, not inert plumbing).
  //
  // Same two rules as `semanticRxTx`/`semanticMeters`, restated because this
  // channel now carries them to three more files:
  //   - the MANIFEST decides, never the resolved SurfacePlan (S5 ruling) —
  //     a workspace subtraction must cost the operator the zone, never
  //     resurrect the legacy twin through the back door;
  //   - it is safe ONLY because MOR-1336's `zoned()` degrades to a BARE
  //     render for an unzoned surface (S5-N3). That guarantee lives in
  //     `SemanticRadioSurfaces.svelte`; a change making `zoned` withhold its
  //     body instead would turn every suppression on this channel into a
  //     readout-losing bug.
  // AGC pairs with `dsp`, not a zone of its own: `DspSurface` owns the AGC
  // leaf (5A/MOR-1290), so `<AgcPanel>` retires on `declared.has('dsp')`.

  // Reactive state + capabilities — via runtime
  let radioState = $derived(runtime.state);
  let caps = $derived(runtime.caps);

  // MOR-1235. The meters dock's TX chrome takes its truth from the App-owned
  // TX controller — the SAME source as the authoritative global lamp
  // (MOR-1008/MOR-1059) — and never from `radioState.ptt`, a command/readback
  // echo that can read RX while the key is still down. The two disagree
  // exactly in the uncertain/confirming windows the lamp fails closed on, and
  // a meters panel that says RX there greys out the SWR/ALC fault tiles
  // mid-transmission. Predicate below is AppGlobalHost's own, verbatim.
  const txCtl = getAppTxController();
  let txState = $state.raw(txCtl.snapshot());
  const stopWatchingTx = txCtl.subscribe((next) => { txState = next; });
  onDestroy(() => stopWatchingTx());
  let meterTxActive = $derived(
    txState.radioTx === 'on' || txState.txRisk === 'confirmed-on' || txState.txRisk === 'uncertain',
  );

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
  let isLandscape = $state(false);
  let landscapeSpectrumDismissed = $state(false);
  let landscapeAutoLocked = $state(false);
  let connectionStatus = $derived(runtime.connectionStatus);

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
        mql?.removeEventListener('change', handleOrientationChange);
      };
    }

    receiverDeckWidth = receiverDeckElement.getBoundingClientRect().width || receiverDeckElement.clientWidth || null;

    if (typeof ResizeObserver === 'undefined') {
      return () => {
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
<!--
  `sdr-test` stays a pure IDENTITY hook (which entrypoint is on screen);
  `semantic-deck` is the PRESENTATIONAL one — the taller deck row and the
  wide-viewport promotion below belong to the semantic deck, not to one skin id,
  now that a second family resolves into it (MOR-1313).
-->
<div class="radio-layout" class:sdr-test={skinId === 'sdr-test'} class:semantic-deck={semanticDeck}>
  <StatusBar onSettings={() => (settingsOpen = true)} {declared} />
  <KeyboardHandler config={keyboardConfig} onAction={keyboardHandlers.dispatch} />

  <section class="receiver-deck" bind:this={receiverDeckElement} style={receiverDeckStyle}>
    {#if semanticDeck}
      <SemanticRadioSurfaces />
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
      <LeftSidebar hideTxPanel={semanticRxTx} {declared} />
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
      <RightSidebar hideTxPanel={semanticRxTx} {declared} />
    </div>
  </section>

  {#if !semanticMeters}
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
        txActive={meterTxActive}
      />
    </section>
  {/if}
</div>
{/if}

<!-- Global feedback / power-health / TX indication are hosted by
     AppGlobalHost at the App composition root (MOR-1059). -->

<!-- ═══ SETTINGS MODAL (outside power-off block so it works when radio is on) ═══ -->
{#if settingsOpen}
  <!-- svelte-ignore a11y_no_static_element_interactions -->
  <div class="settings-backdrop" onclick={() => (settingsOpen = false)} onkeydown={(e) => { if (e.key === 'Escape') settingsOpen = false; }}>
    <!-- svelte-ignore a11y_no_static_element_interactions -->
    <div class="settings-modal" role="dialog" aria-modal="true" aria-label={t('core.settings.dialogLabel')} tabindex="-1" onclick={(e) => e.stopPropagation()} onkeydown={(e) => { if (e.key === 'Escape') settingsOpen = false; }}>
      <div class="settings-header">
        <span class="settings-title">{t('core.settings.title')}</span>
        <button class="settings-close" onclick={() => (settingsOpen = false)}>✕</button>
      </div>
      <div class="settings-content">
        <CollapsiblePanel title="LANGUAGE" panelId="desktop-language">
          <LanguageSelector />
        </CollapsiblePanel>

        <CollapsiblePanel title="WORKSPACE" panelId="desktop-workspace">
          <WorkspaceSettingsPanel />
          <WorkspaceImportExport />
        </CollapsiblePanel>

        <CollapsiblePanel title="VFO / BAND" panelId="desktop-vfo-ops">
          <!-- S10 §4, the NAMED EXCEPTION to this slice's inertness: unlike
               every other predicate on this channel, `semanticDeck` is
               ALREADY true on desktop-v2 (MOR-1313 declared `receiver-deck`),
               so this row disappears the day S6-pre merges. Deliberate: the
               semantic `VfoSurface` has owned equivalent — and translated —
               split/swap/equalize controls since MOR-1321, and only the
               modal's third copy was never gated. Gated on `semanticDeck`,
               NOT on `semanticRxTx`: this is VFO-ops routing, not a
               key/unkey affordance, so it is not an R9 site (S10 §6). -->
          {#if !semanticDeck}
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
          {/if}
          <!-- MOR-1367 (S8) wires the BAND half, S10 §4a/§7: only the HAM tab
               is duplicated by `BandSurface`, while the LW/MW + SWL tabs and
               their 17 broadcast presets are deliberately not facts and have no
               other production host. So the split is a PROP, never a mount
               gate. This panel is the ONE section that must never be wrapped as
               a whole: row 10 (the LW/MW + SWL tabs) is permanent, so the panel
               can never be empty and an outer `{#if}` here would orphan the
               presets — the exact operator-affordance loss §4a exists to
               prevent. -->
          <BandSelector hamBands={!declared.has('band')} />
        </CollapsiblePanel>

        {#if !declared.has('dsp')}
          <CollapsiblePanel title="DSP" panelId="desktop-dsp">
            <DspPanel />
          </CollapsiblePanel>
        {/if}

        <!-- Same predicate as DSP above, on purpose (S10 row 2): AGC is a
             leaf of `DspSurface`, not a surface of its own. -->
        {#if !declared.has('dsp')}
          <CollapsiblePanel title="AGC" panelId="desktop-agc">
            <AgcPanel />
          </CollapsiblePanel>
        {/if}

        {#if !declared.has('rfFrontEnd')}
          <CollapsiblePanel title="RF FRONT END" panelId="desktop-rf">
            <RfFrontEnd />
          </CollapsiblePanel>
        {/if}

        {#if !declared.has('ritXitScan')}
          <CollapsiblePanel title="RIT / XIT" panelId="desktop-rit">
            <RitXitPanel />
          </CollapsiblePanel>
        {/if}

        {#if !declared.has('cwKeyer')}
          <CollapsiblePanel
            title="CW"
            panelId="desktop-cw"
            autoCollapseWhen={activeMode !== 'CW' && activeMode !== 'CW-R'}
          >
            <CwPanel />
          </CollapsiblePanel>
        {/if}
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
  .radio-layout.semantic-deck {
    grid-template-rows: 28px 280px minmax(0, 1fr) auto;
  }
  /* Wide-viewport promotion: sidebars move up to flank the VFO row.
     Below 1680px we keep the stacked layout (VFO full-width, sidebars below). */
  @media (min-width: 1680px) {
    .radio-layout.semantic-deck {
      grid-template-columns: 228px minmax(0, 1fr) 228px;
      grid-template-rows: 28px 280px minmax(0, 1fr) auto;
      grid-template-areas:
        "status status status"
        "left   deck   right"
        "left   center right"
        "dock   dock   dock";
    }
    .radio-layout.semantic-deck > :global(.status-bar) { grid-area: status; }
    .radio-layout.semantic-deck > .receiver-deck { grid-area: deck; }
    .radio-layout.semantic-deck > .bottom-dock { grid-area: dock; }
    /* Flatten content-row so its children become direct grid items. */
    .radio-layout.semantic-deck > .content-row {
      display: contents;
    }
    .radio-layout.semantic-deck > .content-row > .content-left { grid-area: left; }
    .radio-layout.semantic-deck > .content-row > .content-right { grid-area: right; }
    .radio-layout.semantic-deck > .content-row > .content-center { grid-area: center; }
  }
  .radio-layout, .radio-layout.semantic-deck {
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
