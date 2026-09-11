<script lang="ts">
  import { onDestroy, onMount, tick, untrack, type Snippet } from 'svelte';
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
  import { getWsConnected, hasEverConnected } from '$lib/stores/connection.svelte';
  import { deriveLinkFault } from '$lib/runtime/adapters/link-fault';
  import { applyModeDefault } from '$lib/stores/tuning.svelte';
  import { getKeyboardConfig, getScopeSource, hasAnyScope, hasSpectrum } from '$lib/stores/capabilities.svelte';
  import type { SkinId } from '../../skins/registry';
  import { declaredSurfaces, getLayout } from '../../presentation/layouts/contract';
  // Side-effect import: populates the LAYOUT registry `getLayout` resolves
  // against — the same idiom App.svelte and `semantic/design-language-renderers`
  // use for their registries. Imported HERE, in the shell that reads the
  // manifest, so a presentation resolved through the v3 path is self-sufficient:
  // whether the legacy twins are suppressed can never depend on some other
  // module having pulled the barrel in first.
  import '../../presentation/layouts/declarations';
  import type { ManagedScopeRegion } from '$lib/runtime/adapters/scope-display-projection';
  import SpectrumPanel from '../../components/spectrum/SpectrumPanel.svelte';
  import LeftSidebar from './LeftSidebar.svelte';
  import RightSidebar from './RightSidebar.svelte';
  import VfoHeader from './VfoHeader.svelte';
  import type {
    InstrumentComposition, PanelChrome, PanelDragOwner, StandardTxLevelAvailability,
  } from '../wiring/instrument-composition';
  import { createDragReorder } from '$lib/drag-reorder.svelte';
  import type { DspFiniteHandles } from '../../semantic/dsp-instruments';
  import type { DspScalarHandles } from '../../semantic/dsp-scalars';
  import type { TxAuxFiniteHandles } from '../../semantic/tx-aux-finite';
  import type { TxAuxScalarHandles } from '../../semantic/tx-aux-scalar';
  import type { RfFrontEndFiniteHandles } from '../../semantic/rf-front-end-instruments';
  import type { RxAudioInstrumentHandles } from '../../semantic/rx-audio-instruments';
  import type { FilterInstrumentHandles } from '../../semantic/filter-instruments';
  import type { BandInstrumentHandles } from '../../semantic/band-instruments';
  import { ANTENNA_BLOCKED_LABEL } from '../../semantic/AntennaInstrumentHost.svelte';
  import { getManagedAppTxController } from '$lib/runtime/tx-controller/managed-app-host';
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
  import { toVfoProps, toVfoOpsProps } from '$lib/runtime/props/panel-props';
  import {
    getVfoHandlers, getKeyboardHandlers, getSystemHandlers,
  } from '$lib/runtime/adapters/panel-adapters';
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

  let { skinId = 'desktop-v2', instruments }: { skinId?: SkinId; instruments: InstrumentComposition } = $props();
  type StandardTxSettings = 'vox' | 'compressor' | 'monitor' | 'rfPower' | 'micGain';
  let txSettingsOpen = $state<StandardTxSettings | null>(null);
  let txSettingsTrigger = $state<HTMLButtonElement | null>(null);
  let txSettingsPopover = $state<HTMLDivElement | null>(null);
  let txSettingsPosition = $state('');

  function closeTxSettings(returnFocus = false): void {
    const trigger = txSettingsTrigger;
    txSettingsOpen = null;
    txSettingsTrigger = null;
    txSettingsPosition = '';
    if (returnFocus) void tick().then(() => trigger?.focus());
  }

  function positionTxSettings(): void {
    if (!txSettingsTrigger || !txSettingsPopover || typeof window === 'undefined') return;
    const margin = 8;
    const gap = 5;
    const anchor = txSettingsTrigger.getBoundingClientRect();
    const popover = txSettingsPopover.getBoundingClientRect();
    const left = Math.min(window.innerWidth - margin - popover.width,
      Math.max(margin, anchor.right - popover.width));
    const below = anchor.bottom + gap;
    const top = below + popover.height <= window.innerHeight - margin
      ? below
      : Math.max(margin, anchor.top - gap - popover.height);
    txSettingsPosition = `left:${left}px;top:${top}px;max-height:${Math.max(80, window.innerHeight - margin * 2)}px`;
  }

  async function toggleTxSettings(id: StandardTxSettings, event: MouseEvent): Promise<void> {
    const trigger = event.currentTarget as HTMLButtonElement;
    if (txSettingsOpen === id) {
      closeTxSettings(true);
      return;
    }
    txSettingsOpen = id;
    txSettingsTrigger = trigger;
    await tick();
    positionTxSettings();
    txSettingsPopover?.querySelector<HTMLElement>('[role="slider"], input, button')?.focus();
  }

  function handleTxSettingsPointerDown(event: PointerEvent): void {
    if (txSettingsOpen === null) return;
    const target = event.target as Node | null;
    if (target && (txSettingsPopover?.contains(target) || txSettingsTrigger?.contains(target))) return;
    closeTxSettings(true);
  }

  function handleTxSettingsKeydown(event: KeyboardEvent): void {
    if (event.key !== 'Escape' || txSettingsOpen === null) return;
    event.preventDefault();
    event.stopPropagation();
    closeTxSettings(true);
  }

  const standardFaceAtMount = untrack(() => skinId === 'desktop-v2');
  const STANDARD_PANEL_ID_REPLACEMENTS: Readonly<Record<string, readonly string[]>> = {
    'semantic-rit-xit-scan': ['semantic-rit-xit', 'semantic-scan'],
    'rit-xit': ['semantic-rit-xit'], scan: ['semantic-scan'], agc: ['semantic-agc'],
  };
  function migrateStandardPanelPreferences(): void {
    if (!standardFaceAtMount || typeof localStorage === 'undefined') return;
    const orderKeys = ['rigplane:panel-order', 'rigplane:right-panel-order'] as const;
    const migratedIds = new Set<string>();
    for (const key of orderKeys) {
      try {
        const raw = localStorage.getItem(key);
        if (raw === null) continue;
        const parsed: unknown = JSON.parse(raw);
        if (!Array.isArray(parsed)) continue;
        const seen = new Set<string>();
        const migrated: string[] = [];
        for (const entry of parsed) {
          if (typeof entry !== 'string') continue;
          const replacements = STANDARD_PANEL_ID_REPLACEMENTS[entry] ?? [entry];
          for (const id of replacements) if (!seen.has(id)) {
            seen.add(id); migrated.push(id); migratedIds.add(id);
          }
        }
        localStorage.setItem(key, JSON.stringify(migrated));
      } catch { /* retain unreadable preferences */ }
    }
    for (const key of orderKeys) {
      try {
        const knownKey = `${key}:known-defaults`;
        const raw = localStorage.getItem(knownKey);
        const parsed: unknown = raw === null ? [] : JSON.parse(raw);
        const known = new Set(Array.isArray(parsed)
          ? parsed.filter((entry): entry is string => typeof entry === 'string') : []);
        for (const [legacy, replacements] of Object.entries(STANDARD_PANEL_ID_REPLACEMENTS)) {
          if (known.delete(legacy)) replacements.forEach(id => known.add(id));
        }
        migratedIds.forEach(id => known.add(id));
        localStorage.setItem(knownKey, JSON.stringify([...known]));
      } catch { /* retain unreadable preferences */ }
    }
    try {
      const raw = localStorage.getItem('rigplane:panel-collapsed');
      if (raw === null) return;
      const parsed: unknown = JSON.parse(raw);
      if (parsed === null || typeof parsed !== 'object' || Array.isArray(parsed)) return;
      const collapsed = parsed as Record<string, unknown>;
      for (const [legacy, replacements] of Object.entries(STANDARD_PANEL_ID_REPLACEMENTS)) {
        if (typeof collapsed[legacy] !== 'boolean') continue;
        for (const id of replacements) if (!(id in collapsed)) collapsed[id] = collapsed[legacy];
        delete collapsed[legacy];
      }
      localStorage.setItem('rigplane:panel-collapsed', JSON.stringify(collapsed));
    } catch { /* retain unreadable preferences */ }
  }
  migrateStandardPanelPreferences();
  const standardLeftDrag: PanelDragOwner | null = standardFaceAtMount ? createDragReorder({
    storageKey: 'rigplane:panel-order',
    defaults: [
      'semantic-rf-front-end', 'semantic-filter', 'semantic-band', 'semantic-agc',
      'semantic-rit-xit', 'semantic-antenna', 'semantic-scan', 'band',
    ],
    containerSelector: '.standard-panel-owner-left',
  }) : null;
  const standardRightDrag: PanelDragOwner | null = standardFaceAtMount ? createDragReorder({
    storageKey: 'rigplane:right-panel-order',
    defaults: [
      'semantic-rx-tx', 'semantic-rx-audio', 'semantic-dsp', 'semantic-cw',
      'semantic-memory', 'semantic-tx-aux', 'audio-scope',
    ],
    containerSelector: '.standard-panel-owner-right',
  }) : null;
  const standardBottomDrag: PanelDragOwner | null = standardFaceAtMount ? createDragReorder({
    storageKey: 'rigplane:bottom-panel-order',
    defaults: ['semantic-meters'],
    containerSelector: '.standard-bottom-dock',
  }) : null;

  function panelChrome(owner: PanelDragOwner, panelId: string, title?: string): PanelChrome {
    return {
      panelId,
      draggable: true,
      onDragStart: owner.handleDragStart,
      style: owner.dragStyle(panelId),
      ...(title === undefined ? {} : { title }),
    };
  }

  // MOR-2425 C-R3 — the link-fault veil. It carries no words of its own: the
  // status bar already states both arms, and the veil's own selectors exempt
  // that chrome so it keeps its colour while the face loses its.
  //   ws-down      → StatusBar's `.control-link-lost` bar, on the same
  //                  `wsConnected` fact through `getConnectionStatus()`.
  //   radio-silent → StatusBar's bad-link chip, on the same staleness fact
  //                  at the same threshold.
  let linkFault = $derived(deriveLinkFault({
    everConnected: hasEverConnected(),
    wsConnected: getWsConnected(),
    connectionStale: runtime.connectionStale,
  }));
  let linkFaultAttribute = $derived(linkFault === 'none' ? undefined : linkFault);

  // MOR-1313 (v3-rework slice S2) — PER-ZONE suppression, replacing the
  // MOR-1065 `skinId === 'sdr-test'` boolean. This shell hosts two areas that
  // have a semantic twin: the receiver deck (legacy `<VfoHeader>` vs the `vfo`
  // surface) and the sidebars' `<TxPanel>` (vs the `rxTx` surface). Which one
  // the semantic vertical owns is read off the ACTIVE layout manifest's zone
  // declarations: a surface some declared zone mounts renders semantically and
  // its legacy twin does NOT also render; a surface no zone declares keeps its
  // legacy presentation untouched.
  //
  // Both resolving families declare the full pair, so both are fully semantic.
  // Since MOR-2231 both also split it across the same two zone ids
  // (`receiver-deck: [vfo]` + `rx-tx: [rxTx]`, MOR-1266 on desktop-v2), which
  // is why the `regions` prop below cannot be derived from the manifest.
  //
  // The MANIFEST is the authority, deliberately NOT the resolved surface plan
  // (`useSurfacePlan`, MOR-1082): the workspace may subtract a surface from a
  // zone, and letting a subtraction bring the legacy twin back would be
  // force-show through the back door — the one thing the plan may never do.
  let declared = $derived(declaredSurfaces(getLayout(skinId)));
  let scopeControlsInRegionContent = $derived(
    skinId === 'sdr-test' && declared.has('scopeControls') && hasSpectrum() && getScopeSource() === 'hardware',
  );
  let semanticDeck = $derived(declared.has('vfo'));
  // R9 — ONE key/unkey authority, and this line is where that count is decided.
  //
  // It follows the DECK, not the `rxTx` declaration, and the asymmetry is
  // deliberate: `SemanticRadioSurfaces` has a hardcoded single composition of
  // `['vfo', 'rxTx']` — mounting the semantic deck ALWAYS brings
  // exactly one `<RxTxSurface>` with it, whatever the manifest declared. Gating
  // the sidebars' TX twin on `declared.has('rxTx')` instead would therefore let
  // the two disagree: a manifest declaring `vfo` WITHOUT `rxTx` (which
  // `validateLayoutManifest` permits, and which the programme's additive
  // subset-declaration pattern positively invites) would render the semantic
  // RxTxSurface AND TxPanel — duplicate controls for the same App-root managed
  // intent facade. The single boolean keeps exactly one visible control surface.
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
  // Landed INERT, then activated by S7/S8/S9 (MOR-1366/1367/1368): the
  // desktop-v2 manifest now declares all of `filter`/`rfFrontEnd`/`band`/
  // `antenna`/`ritXitScan`/`rxAudio`/`dsp`/`cwKeyer`, so every one of those
  // predicates is live and the branches are reachable on that skin.
  // Separately, the modal's SPLIT/A↔B/A=B row gates on `semanticDeck`, not
  // on a `declared` zone (S10 §4 — a real, deliberate change, not inert
  // plumbing).
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
  let directFrequencyEntrySupported = $derived(
    caps?.capabilities.includes('vfo_freq_direct') === true
      && caps.receivers === 1
      && caps.vfoScheme === 'ab',
  );

  // MOR-1235. The meters dock's TX chrome takes its truth from the App-owned
  // TX controller — the SAME source as the authoritative global lamp
  // (MOR-1008/MOR-1059) — and never from `radioState.ptt`, a command/readback
  // echo that can read RX while the key is still down. The two disagree
  // exactly in the uncertain/confirming windows the lamp fails closed on, and
  // a meters panel that says RX there greys out the SWR/ALC fault tiles
  // mid-transmission. Predicate below is AppGlobalHost's own, verbatim.
  const txCtl = getManagedAppTxController();
  let txState = $state.raw(txCtl.snapshot());
  const stopWatchingTx = txCtl.subscribe((next) => { txState = next; });
  onDestroy(() => stopWatchingTx());
  let meterTxActive = $derived(
    txState.radioTx === 'on' || txState.txRisk === 'confirmed-on' || txState.txRisk === 'uncertain',
  );

  // MOR-1409 A13b (correction 5246842617): the scope-status bridge to
  // VfoHeader (issue #832) — a `$derived.by` digest plus two handler
  // functions that each called the runtime's typed-facade dispatcher for
  // 'set_scope_dual'/'switch_scope_receiver' — is deleted rather than
  // migrated. `VfoHeader` already self-wires scope handling through its own
  // `bindSemanticSurfaceHandlers().scopeControls` (the A07 idiom) and has
  // ignored these exact legacy props since; see
  // `vfo-header.isolated.test.ts`'s `VfoHeader source boundary` pin, which
  // asserts VfoHeader's source never dereferences either optional prop.
  // RadioLayout was the last production caller of that dispatcher; deleting
  // this dead bridge (rather than re-pointing it at the binder for a prop
  // VfoHeader still ignores) is what lets the dispatcher method itself
  // delete from `frontend-runtime.ts` below.
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

  // MOR-1409 A13b: `rfFrontEnd`/`agc`/`ritXit`/`band`/`dsp`/`cw` (and their
  // matching `make*Handlers()` constructions) are removed here rather than
  // re-pointed at the canonical modules — live inspection found none of
  // them referenced anywhere in this file's template. `RfFrontEnd`,
  // `AgcPanel`, `DspPanel`, `RitXitPanel`, `CwPanel`, and `BandSelector`
  // (below) now self-source their state through the semantic surfaces;
  // these were dead prop/handler constructions left over from before that
  // migration.

  // Command handlers via the sanctioned adapter layer
  const vfoHandlers = getVfoHandlers();
  const keyboardHandlers = getKeyboardHandlers();
  const systemHandlers = getSystemHandlers();

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

<svelte:window
  onpointerdown={handleTxSettingsPointerDown}
  onkeydown={handleTxSettingsKeydown}
  onresize={positionTxSettings}
  onscroll={positionTxSettings}
/>

{#snippet scopeRegion(scopeControls: Snippet | undefined, managedScope: ManagedScopeRegion | undefined)}
  <section class="content-row">
    <main class="content-center center-column">
      {#if hasAnyScope()}
        <div class="spectrum-slot">
          <div class="spectrum-frame">
            <SpectrumPanel hideSourceControls={true} hideScopeControls={declared.has('scopeControls')} {scopeControls}
              scopeProjection={managedScope?.projection} scopeDemanded={managedScope?.demanded}
              onScopeDemandChange={managedScope?.setDemand} />
          </div>
        </div>
      {/if}
    </main>

  </section>
{/snippet}

{#snippet txAuxScalars()}
  <div class="tx-aux-scalar-grid">
    <div class="tx-aux-scalar-seat" data-field="rfPower">{@render instruments.txAuxScalars.rfPower()}</div>
    <div class="tx-aux-scalar-seat" data-field="micGain">{@render instruments.txAuxScalars.micGain()}</div>
    <div class="tx-aux-scalar-seat" data-field="driveGain">{@render instruments.txAuxScalars.driveGain()}</div>
    <div class="tx-aux-scalar-seat" data-field="voxGain">{@render instruments.txAuxScalars.voxGain()}</div>
    <div class="tx-aux-scalar-seat" data-field="antiVoxGain">{@render instruments.txAuxScalars.antiVoxGain()}</div>
    <div class="tx-aux-scalar-seat" data-field="voxDelay">{@render instruments.txAuxScalars.voxDelay()}</div>
    <div class="tx-aux-scalar-seat" data-field="compressorLevel">{@render instruments.txAuxScalars.compressorLevel()}</div>
    <div class="tx-aux-scalar-seat" data-field="monitorLevel">{@render instruments.txAuxScalars.monitorLevel()}</div>
  </div>
{/snippet}

{#snippet txAuxInstrumentLayout()}
  <div class="tx-aux-finite-grid">
    <div class="tx-aux-finite-seat" data-field="atu">{@render instruments.txAuxInstruments.atu()}</div>
    <div class="tx-aux-finite-seat" data-field="vox">{@render instruments.txAuxInstruments.vox()}</div>
    <div class="tx-aux-finite-seat" data-field="compressor">{@render instruments.txAuxInstruments.compressor()}</div>
    <div class="tx-aux-finite-seat" data-field="monitor">{@render instruments.txAuxInstruments.monitor()}</div>
    <div class="tx-aux-finite-seat" data-field="atuTune">{@render instruments.txAuxInstruments.atuTune()}</div>
  </div>
  {@render txAuxScalars()}
{/snippet}

{#snippet standardTxLayout(
  finite: TxAuxFiniteHandles,
  scalars: TxAuxScalarHandles,
  available: StandardTxLevelAvailability,
)}
  <div class="standard-tx-controls" data-testid="standard-tx-controls">
    <div class="standard-tx-button-grid">
      <div class="standard-tx-seat" data-field="atu">{@render finite.atu()}</div>
      <div class="standard-tx-seat" data-field="atuTune">{@render finite.atuTune()}</div>
      <div class="standard-tx-compound" data-field="vox">
        <div class="standard-tx-seat">{@render finite.vox()}</div>
        {#if available.voxGain || available.antiVoxGain || available.voxDelay}
          <button type="button" class="standard-tx-settings-trigger" aria-label="VOX settings"
            aria-haspopup="dialog" aria-expanded={txSettingsOpen === 'vox'}
            aria-controls="standard-tx-settings" onclick={(event) => toggleTxSettings('vox', event)}>▾</button>
        {/if}
      </div>
      <div class="standard-tx-compound" data-field="compressor">
        <div class="standard-tx-seat">{@render finite.compressor()}</div>
        {#if available.compressorLevel}
          <button type="button" class="standard-tx-settings-trigger" aria-label="COMP settings"
            aria-haspopup="dialog" aria-expanded={txSettingsOpen === 'compressor'}
            aria-controls="standard-tx-settings" onclick={(event) => toggleTxSettings('compressor', event)}>▾</button>
        {/if}
      </div>
      <div class="standard-tx-compound" data-field="monitor">
        <div class="standard-tx-seat">{@render finite.monitor()}</div>
        {#if available.monitorLevel}
          <button type="button" class="standard-tx-settings-trigger" aria-label="MON settings"
            aria-haspopup="dialog" aria-expanded={txSettingsOpen === 'monitor'}
            aria-controls="standard-tx-settings" onclick={(event) => toggleTxSettings('monitor', event)}>▾</button>
        {/if}
      </div>
      {#if available.rfPower || available.driveGain}
        <button type="button" class="standard-tx-disclosure" aria-label="RF POWER settings"
          aria-haspopup="dialog" aria-expanded={txSettingsOpen === 'rfPower'}
          aria-controls="standard-tx-settings" onclick={(event) => toggleTxSettings('rfPower', event)}>RF POWER ▾</button>
      {/if}
      {#if available.micGain}
        <button type="button" class="standard-tx-disclosure" aria-label="MIC GAIN settings"
          aria-haspopup="dialog" aria-expanded={txSettingsOpen === 'micGain'}
          aria-controls="standard-tx-settings" onclick={(event) => toggleTxSettings('micGain', event)}>MIC GAIN ▾</button>
      {/if}
    </div>
    {#if txSettingsOpen}
      <div class="standard-tx-settings-popover" id="standard-tx-settings"
        data-testid="standard-tx-settings-popover" data-settings={txSettingsOpen}
        role="dialog" aria-label={`${txSettingsOpen} settings`} tabindex="-1"
        bind:this={txSettingsPopover} style={txSettingsPosition}>
        {#if txSettingsOpen === 'vox'}
          {#if available.voxGain}<div class="standard-tx-scalar-seat" data-field="voxGain">{@render scalars.voxGain({ form: 'hbar', compact: false, showLabel: true, showValue: true, variant: 'hardware-illuminated' })}</div>{/if}
          {#if available.antiVoxGain}<div class="standard-tx-scalar-seat" data-field="antiVoxGain">{@render scalars.antiVoxGain({ form: 'hbar', compact: false, showLabel: true, showValue: true, variant: 'hardware-illuminated' })}</div>{/if}
          {#if available.voxDelay}<div class="standard-tx-scalar-seat" data-field="voxDelay">{@render scalars.voxDelay({ form: 'hbar', compact: false, showLabel: true, showValue: true, variant: 'hardware-illuminated' })}</div>{/if}
        {:else if txSettingsOpen === 'compressor' && available.compressorLevel}
          <div class="standard-tx-scalar-seat" data-field="compressorLevel">{@render scalars.compressorLevel({ form: 'hbar', compact: false, showLabel: true, showValue: true, variant: 'hardware-illuminated' })}</div>
        {:else if txSettingsOpen === 'monitor' && available.monitorLevel}
          <div class="standard-tx-scalar-seat" data-field="monitorLevel">{@render scalars.monitorLevel({ form: 'hbar', compact: false, showLabel: true, showValue: true, variant: 'hardware-illuminated' })}</div>
        {:else if txSettingsOpen === 'rfPower'}
          {#if available.rfPower}<div class="standard-tx-scalar-seat" data-field="rfPower">{@render scalars.rfPower({ form: 'hbar', compact: false, showLabel: true, showValue: true, variant: 'hardware-illuminated' })}</div>{/if}
          {#if available.driveGain}<div class="standard-tx-scalar-seat" data-field="driveGain">{@render scalars.driveGain({ form: 'hbar', compact: false, showLabel: true, showValue: true, variant: 'hardware-illuminated' })}</div>{/if}
        {:else if txSettingsOpen === 'micGain' && available.micGain}
          <div class="standard-tx-scalar-seat" data-field="micGain">{@render scalars.micGain({ form: 'hbar', compact: false, showLabel: true, showValue: true, variant: 'hardware-illuminated' })}</div>
        {/if}
      </div>
    {/if}
  </div>
{/snippet}

{#snippet dspFiniteLayout(dspInstruments: DspFiniteHandles)}
  <div class="dsp-finite-grid">
    {#if dspInstruments.compactNb}<div class="dsp-finite-seat" data-field="nbActive">{@render dspInstruments.compactNb()}</div>{/if}
    {#if dspInstruments.compactNr}<div class="dsp-finite-seat" data-field="nrActive">{@render dspInstruments.compactNr()}</div>{/if}
    {#if dspInstruments.compactManualNotch}<div class="dsp-finite-seat" data-field="manualNotch">{@render dspInstruments.compactManualNotch()}</div>{/if}
    {#if dspInstruments.compactAutoNotch}<div class="dsp-finite-seat" data-field="autoNotch">{@render dspInstruments.compactAutoNotch()}</div>{/if}
  </div>
{/snippet}

{#snippet agcFiniteLayout(dspInstruments: DspFiniteHandles)}
  <div class="dsp-finite-grid agc-finite-grid">
    <div class="dsp-finite-seat" data-field="agcMode">{@render dspInstruments.agcMode()}</div>
  </div>
{/snippet}

{#snippet rfFrontEndFiniteLayout(rfFrontEndInstruments: RfFrontEndFiniteHandles)}
  <div class="rf-front-end-finite-grid">
    <div class="rf-front-end-finite-seat" data-field="attenuator">{@render rfFrontEndInstruments.attenuator()}</div>
    <div class="rf-front-end-finite-seat" data-field="preamp">{@render rfFrontEndInstruments.preamp()}</div>
    <div class="rf-front-end-finite-seat" data-field="digiSel">{@render rfFrontEndInstruments.digiSel()}</div>
    <div class="rf-front-end-finite-seat" data-field="ipPlus">{@render rfFrontEndInstruments.ipPlus()}</div>
  </div>
{/snippet}

{#snippet rxAudioFiniteLayout(rxAudioInstruments: RxAudioInstrumentHandles)}
  <div class="rx-audio-finite-grid">
    <div class="rx-audio-finite-seat" data-field="monitorMode">{@render rxAudioInstruments.monitorMode()}</div>
    {#if rxAudioInstruments.afLevelRow}<div class="rx-audio-finite-seat" data-field="afLevel">{@render rxAudioInstruments.afLevelRow()}</div>{/if}
    {#if rxAudioInstruments.monitorStatus}<div class="rx-audio-finite-seat" data-field="monitorStatus">{@render rxAudioInstruments.monitorStatus()}</div>{/if}
    <div class="rx-audio-finite-seat" data-field="routingFocus">{@render rxAudioInstruments.routingFocus()}</div>
    {#if rxAudioInstruments.routingSplitToggle}<div class="rx-audio-finite-seat" data-field="routingSplit">{@render rxAudioInstruments.routingSplitToggle()}</div>{/if}
    {#if rxAudioInstruments.mainGain}<div class="rx-audio-finite-seat" data-field="mainGain">{@render rxAudioInstruments.mainGain()}</div>{/if}
    {#if rxAudioInstruments.subGain}<div class="rx-audio-finite-seat" data-field="subGain">{@render rxAudioInstruments.subGain()}</div>{/if}
    <div class="rx-audio-finite-seat" data-field="modInputSource">{@render rxAudioInstruments.modInputSource()}</div>
    <div class="rx-audio-finite-seat" data-field="setModInputLan">{@render rxAudioInstruments.setModInputLan()}</div>
  </div>
{/snippet}

{#snippet dspScalarLayout(dspScalars: DspScalarHandles)}
  <div class="dsp-scalar-grid">
    <div class="dsp-scalar-seat" data-field="nbLevel">{@render dspScalars.nbLevel()}</div>
    <div class="dsp-scalar-seat" data-field="nbWidth">{@render dspScalars.nbWidth()}</div>
  </div>
{/snippet}

{#snippet standardModeLayout(filterInstruments: FilterInstrumentHandles)}
  {#if filterInstruments.standardMode && filterInstruments.standardDataMode}
    <div class="standard-mode-layout" data-testid="standard-mode-layout">
      {@render filterInstruments.standardMode()}
      {@render filterInstruments.standardDataMode()}
    </div>
  {/if}
{/snippet}

{#snippet standardFilterLayout(filterInstruments: FilterInstrumentHandles)}
  <div class="filter-finite-grid" data-testid="filter-finite-grid">
    <div class="filter-finite-seat" data-field="filter">{@render filterInstruments.filter()}</div>
    <div class="filter-finite-seat" data-field="shape">{@render filterInstruments.shape()}</div>
  </div>
{/snippet}

{#snippet bandControlLayout(bandInstruments: BandInstrumentHandles)}
  <div class="band-control-grid" data-testid="band-control-grid">
    <div class="band-control-seat" data-field="bandChoice">{@render bandInstruments.bandChoice()}</div>
    <div class="band-control-seat" data-field="frequencyEntry">
      {@render bandInstruments.frequencyEntry()}
    </div>
  </div>
{/snippet}

<!--
  MOR-2425. The Standard face arranges the two persistent antenna seats itself
  instead of mounting the grouped `AntennaSurface`, so the blocked-reason list
  the seats' `aria-describedby` points at has to be rendered here too — the
  host hands both the id and the reason codes on `instruments.antennaLayout`.
-->
{#snippet antennaControlLayout()}
  {#if runtime.caps?.antennas === 1}
    <div class="antenna-fixed-port" data-testid="antenna-fixed-port">
      <span>TX</span><output>ANT 1</output>
    </div>
  {:else}<div class="antenna-control-grid" data-testid="antenna-control-grid">
    <div class="antenna-control-seat" data-field="txPort">
      {@render instruments.antennaInstruments.txPort()}
    </div>
    <div class="antenna-control-seat" data-field="rxAnt">
      {@render instruments.antennaInstruments.rxAnt()}
    </div>
  </div>{/if}
  {#if runtime.caps?.antennas !== 1}<ul class="antenna-blocked" id={instruments.antennaLayout.blockedId} data-testid="antenna-blocked">
    {#each instruments.antennaLayout.blocked as code (code)}
      <li data-reason={code}>{ANTENNA_BLOCKED_LABEL[code]}</li>
    {/each}
  </ul>{/if}
{/snippet}

{#snippet vfoOperationControls()}
  <div class="vfo-operation-instrument-grid" data-testid="vfo-operation-instrument-grid">
    {#if instruments.vfoOperations.split}<div class="vfo-operation-instrument-seat" data-field="split">{@render instruments.vfoOperations.split()}</div>{/if}
    {#if instruments.vfoOperations.dualWatch}<div class="vfo-operation-instrument-seat" data-field="dualWatch">{@render instruments.vfoOperations.dualWatch()}</div>{/if}
    {#if instruments.vfoOperations.activeReceiver}<div class="vfo-operation-instrument-seat" data-field="activeReceiver">{@render instruments.vfoOperations.activeReceiver()}</div>{/if}
    {#if instruments.vfoOperations.equalize}<div class="vfo-operation-instrument-seat" data-field="equalize">{@render instruments.vfoOperations.equalize()}</div>{/if}
    {#if instruments.vfoOperations.swap}<div class="vfo-operation-instrument-seat" data-field="swap">{@render instruments.vfoOperations.swap()}</div>{/if}
    {#if instruments.vfoOperations.quickSplit}<div class="vfo-operation-instrument-seat" data-field="quickSplit">{@render instruments.vfoOperations.quickSplit()}</div>{/if}
    {#if instruments.vfoOperations.quickDualWatch}<div class="vfo-operation-instrument-seat" data-field="quickDualWatch">{@render instruments.vfoOperations.quickDualWatch()}</div>{/if}
    {#if instruments.vfoOperations.speak}<div class="vfo-operation-instrument-seat" data-field="speak">{@render instruments.vfoOperations.speak()}</div>{/if}
  </div>
{/snippet}

{#snippet standardServicePanels(owner: PanelDragOwner, showReset = false)}
  {#if owner.order.includes('semantic-rf-front-end')}
    {@render instruments.rfFrontEnd(
      undefined, rfFrontEndFiniteLayout, panelChrome(owner, 'semantic-rf-front-end'),
    )}
  {/if}
  {#if owner.order.includes('semantic-filter')}
    {@render instruments.filter(
      undefined, standardModeLayout, panelChrome(owner, 'semantic-filter'),
      standardFilterLayout, {
        panelId: 'semantic-filter-controls', draggable: false,
        onDragStart: owner.handleDragStart, style: owner.dragStyle('semantic-filter'),
      },
    )}
  {/if}
  {#if owner.order.includes('semantic-band') && !directFrequencyEntrySupported}
    {@render instruments.band(
      undefined, bandControlLayout, panelChrome(owner, 'semantic-band'),
    )}
  {/if}
  {#if owner.order.includes('semantic-antenna')}
    {@render instruments.antenna(
      undefined, antennaControlLayout, panelChrome(owner, 'semantic-antenna'),
    )}
  {/if}
  {#if owner.order.includes('semantic-agc')}
    {@render instruments.dsp(
      undefined, agcFiniteLayout, undefined, panelChrome(owner, 'semantic-agc', 'AGC'), 'agc',
    )}
  {/if}
  {#if owner.order.includes('semantic-rit-xit')}
    {@render instruments.ritXitScan(
      undefined, panelChrome(owner, 'semantic-rit-xit', 'RIT / XIT'), 'rit-xit',
    )}
  {/if}
  {#if owner.order.includes('semantic-scan')}
    {@render instruments.ritXitScan(
      undefined, panelChrome(owner, 'semantic-scan', 'SCAN'), 'scan',
    )}
  {/if}
  {#if owner.order.includes('semantic-rx-tx')}
    {@render instruments.rxTx(
      undefined, panelChrome(owner, 'semantic-rx-tx', 'TX'), standardTxLayout,
    )}
  {/if}
  {#if owner.order.includes('semantic-rx-audio')}
    {@render instruments.rxAudio(
      undefined, rxAudioFiniteLayout, panelChrome(owner, 'semantic-rx-audio'),
    )}
  {/if}
  {#if owner.order.includes('semantic-dsp')}
    {@render instruments.dsp(
      undefined, dspFiniteLayout, dspScalarLayout, panelChrome(owner, 'semantic-dsp'), 'dsp', true,
    )}
  {/if}
  {#if owner.order.includes('semantic-cw')}
    {@render instruments.cwKeyer(
      undefined, true, panelChrome(owner, 'semantic-cw', 'CW'), undefined, true,
    )}
  {/if}
  {#if owner.order.includes('semantic-memory')}
    {@render instruments.memory(undefined, panelChrome(owner, 'semantic-memory'))}
  {/if}
  {#if owner.order.includes('semantic-meters')}
    {@render instruments.meters(undefined, panelChrome(owner, 'semantic-meters'))}
  {/if}
  <div class="content-left">
    <LeftSidebar
      hideTxPanel={semanticRxTx} {declared} dragOwner={owner} {showReset}
      semanticHamBands={instruments.bandInstruments?.bandChoice}
    />
  </div>
  <div class="content-right">
    <RightSidebar hideTxPanel={semanticRxTx} {declared} dragOwner={owner} />
  </div>
{/snippet}

{#snippet semanticDeckContent(appearance: 'standard' | 'sdr' | 'semantic', allowBare = false)}
  {@render instruments.vfo(appearance, allowBare, vfoOperationControls)}
  {@render instruments.rxTx(allowBare)}
  {@render instruments.txFaultRecovery()}
  {@render instruments.modInputTxWarning()}
  {@render instruments.rxAudio(allowBare)}
  {@render instruments.rfFrontEnd(allowBare)}
  {@render instruments.filter(allowBare)}
  {@render instruments.dsp(allowBare)}
  {@render instruments.band(allowBare)}
  {@render instruments.antenna(allowBare)}
  {@render instruments.ritXitScan(allowBare)}
  {@render instruments.cwKeyer(allowBare)}
  {@render instruments.scopeControls(allowBare)}
  {@render instruments.scopeDisplay(allowBare)}
  {@render instruments.txAuxControls(txAuxInstrumentLayout, allowBare)}
  {@render instruments.meters(allowBare)}
{/snippet}

{#if skinId === 'mobile'}
  <MobileRadioLayout />
{:else if skinId === 'lcd-cockpit'}
  <LcdLayout variant="cockpit" showManagedTotControl={true} />
{:else if skinId === 'lcd-scope'}
  <LcdLayout variant="scope" showManagedTotControl={true} />
{:else if (skinId === 'sdr-test' || skinId === 'desktop-v2') && semanticDeck}
  <div class="radio-layout desktop-control-face semantic-deck"
    class:standard-face={skinId === 'desktop-v2'} class:sdr-test={skinId === 'sdr-test'}
    data-link-fault={linkFaultAttribute}>
    <StatusBar onSettings={() => (settingsOpen = true)} {declared} showManagedTotControl={true} />
    <KeyboardHandler config={keyboardConfig} onAction={keyboardHandlers.dispatch} />

    <section class="receiver-deck" bind:this={receiverDeckElement} style={receiverDeckStyle}>
      {@render instruments.vfo(
        skinId === 'sdr-test' ? 'sdr' : 'standard', undefined, vfoOperationControls,
      )}

      <div
        class="desktop-controls-left"
        class:standard-panel-owner={skinId === 'desktop-v2'}
        class:standard-panel-owner-left={skinId === 'desktop-v2'}
        class:cross-drop-target={standardLeftDrag?.isDropTarget}
      >
        {#if skinId === 'desktop-v2' && standardLeftDrag}
          {@render standardServicePanels(standardLeftDrag, true)}
        {:else}
          {@render instruments.rfFrontEnd()}
          {@render instruments.filter()}
          {@render instruments.band()}
          {@render instruments.antenna()}
          {@render instruments.ritXitScan()}
          <div class="content-left"><LeftSidebar hideTxPanel={semanticRxTx} {declared} /></div>
        {/if}
      </div>

      <div class="desktop-controls-center">
        {#if !scopeControlsInRegionContent}{@render instruments.scopeControls()}{/if}
        {@render instruments.scopeDisplay()}
        {@render scopeRegion(scopeControlsInRegionContent ? instruments.scopeControls : undefined, instruments.managedScope)}
      </div>

      <div
        class="desktop-controls-right"
        class:standard-panel-owner={skinId === 'desktop-v2'}
        class:standard-panel-owner-right={skinId === 'desktop-v2'}
        class:cross-drop-target={standardRightDrag?.isDropTarget}
      >
        {#if skinId === 'desktop-v2' && standardRightDrag}
          {@render instruments.txFaultRecovery()}
          {@render instruments.modInputTxWarning()}
          {@render standardServicePanels(standardRightDrag)}
        {:else}
          {@render instruments.rxTx()}
          {@render instruments.txFaultRecovery()}
          {@render instruments.modInputTxWarning()}
          {@render instruments.rxAudio()}
          {@render instruments.dsp()}
          {@render instruments.cwKeyer()}
          {@render instruments.txAuxControls(txAuxInstrumentLayout)}
          <div class="content-right"><RightSidebar hideTxPanel={semanticRxTx} {declared} /></div>
        {/if}
      </div>

      {#if skinId === 'desktop-v2' && standardBottomDrag}
        <div
          class="standard-bottom-dock"
          class:cross-drop-target={standardBottomDrag.isDropTarget}
        >
          {@render standardServicePanels(standardBottomDrag)}
        </div>
      {:else}
        {@render instruments.meters()}
      {/if}
    </section>
  </div>
{:else}
<div class="radio-layout" class:sdr-test={skinId === 'sdr-test'} class:semantic-deck={semanticDeck}
  data-link-fault={linkFaultAttribute}>
  <StatusBar onSettings={() => (settingsOpen = true)} {declared} showManagedTotControl={true} />
  <KeyboardHandler config={keyboardConfig} onAction={keyboardHandlers.dispatch} />

  <section class="receiver-deck" bind:this={receiverDeckElement} style={receiverDeckStyle}>
    {#if semanticDeck}
      {@render semanticDeckContent(skinId === 'desktop-v2' ? 'standard' : 'semantic', true)}
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
                 the toolbar retains them (#832 fallback).
                 `hideScopeControls` is the MOR-1369 (S6b-1) suppression
                 channel: reuses the SAME `declared` set as `LeftSidebar`/
                 `RightSidebar`/`StatusBar` above (S6-pre, MOR-1364) rather
                 than a second derivation. Landed INERT, then activated by
                 S6b-2 (MOR-1370): the desktop-v2 manifest now declares a
                 `scopeControls` zone, so this predicate is live too. -->
            <SpectrumPanel hideSourceControls={true} hideScopeControls={declared.has('scopeControls')} />
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
               their 16 broadcast presets are deliberately not facts and have no
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

  /* MOR-2425 C-R3 — the link-fault veil.

     One rule set, keyed on the face root's `data-link-fault`, with no
     per-skin variant: a desaturation plus a contrast reduction, so it reads
     the same in every palette and cannot collide with an accent colour. The
     brightness term is there because reducing contrast on a dark face raises
     its blacks — without it the veiled face reads as brighter, not deader.

     `.status-bar` and `.control-link-lost` are exempt, for two reasons that
     point the same way. They carry the words for both arms of the fault —
     the disconnect bar for ws-down, the bad-link chip inside the bar for
     radio-silent — so they must keep their colour while the face loses its.
     And a CSS filter makes the element it is set on a containing block for
     its `position: fixed` descendants: filtering `.status-bar` re-anchors
     the popovers it hosts to that 28px strip (row 2 of the grid below).

     The receiver deck is skipped on its own line and its children taken
     instead, because on the two desktop faces the deck is `display: contents`
     (`.desktop-control-face > .receiver-deck` below), where a filter would
     generate no box at all and veil nothing. On the generic root the deck IS
     a box, so skipping it leaves that box's own background and border
     unveiled; that root is reached only when the active manifest declares no
     `vfo` surface, and the manifests of the two skins that mount this shell
     (`desktop-v2`, `sdr-test`) both declare it. */
  :global(.radio-layout[data-link-fault] > *:not(.status-bar, .control-link-lost, .receiver-deck)),
  :global(.radio-layout[data-link-fault] > .receiver-deck > *) {
    filter: saturate(0.08) contrast(0.5) brightness(0.62);
  }

  .radio-layout.semantic-deck:not(.desktop-control-face) {
    grid-template-rows: 28px minmax(240px, auto) minmax(320px, 1fr) auto;
  }
  .radio-layout.semantic-deck:not(.desktop-control-face) > .receiver-deck { overflow-y: auto; }
  /* Retain the legacy promotion for other shells; Standard and SDR use the
     grouped desktop grid below. */
  /* Wide-viewport promotion: sidebars move up to flank the VFO row.
     Below 1680px we keep the stacked layout (VFO full-width, sidebars below). */
  @media (min-width: 1680px) {
    .radio-layout.semantic-deck:not(.desktop-control-face) {
      grid-template-columns: 228px minmax(0, 1fr) 228px;
      grid-template-rows: 28px minmax(240px, auto) minmax(320px, 1fr) auto;
      grid-template-areas:
        "status status status"
        "left   deck   right"
        "left   center right"
        "dock   dock   dock";
    }
    .radio-layout.semantic-deck:not(.desktop-control-face) > :global(.status-bar) { grid-area: status; }
    .radio-layout.semantic-deck:not(.desktop-control-face) > .receiver-deck { grid-area: deck; }
    .radio-layout.semantic-deck:not(.desktop-control-face) > .bottom-dock { grid-area: dock; }
    /* Flatten content-row so its children become direct grid items. */
    .radio-layout.semantic-deck:not(.desktop-control-face) > .content-row {
      display: contents;
    }
    .radio-layout.semantic-deck:not(.desktop-control-face) > .content-row > .content-left { grid-area: left; }
    .radio-layout.semantic-deck:not(.desktop-control-face) > .content-row > .content-right { grid-area: right; }
    .radio-layout.semantic-deck:not(.desktop-control-face) > .content-row > .content-center { grid-area: center; }
  }

  /* Both desktop faces arrange the existing semantic zones into instrument,
     control and scope regions. Side columns scroll without clipping the deck. */
  .radio-layout.desktop-control-face {
    grid-template-columns: 228px minmax(0, 1fr) 228px;
    /* Wrapped scope controls must contribute to the row above station meters. */
    grid-template-rows: auto 28px auto minmax(min-content, 1fr) auto;
    gap: 4px;
    overflow-y: auto;
  }
  .radio-layout.desktop-control-face.standard-face {
    grid-template-columns: 228px minmax(0, 1fr) 228px;
    grid-template-rows: auto 28px minmax(200px, auto) minmax(0, 1fr) auto;
    gap: 5px;
  }
  .desktop-control-face > .receiver-deck,
  .desktop-control-face :global(.semantic-surfaces) { display: contents; }
  .desktop-control-face > :global(.control-link-lost) { grid-area: 1 / 1 / 2 / -1; }
  .desktop-control-face > :global(.status-bar) { grid-area: 2 / 1 / 3 / -1; }
  .desktop-control-face :global([data-zone-id='receiver-deck']) { grid-area: 3 / 1 / 4 / -1; }
  .desktop-control-face :global(.desktop-controls-left) { grid-area: 4 / 1 / 5 / 2; }
  .desktop-control-face :global(.desktop-controls-center) {
    grid-area: 4 / 2 / 5 / 3;
    display: flex;
    flex-direction: column;
    min-width: 0;
    min-height: 320px;
  }
  .desktop-control-face :global(.desktop-controls-right) { grid-area: 4 / 3 / 5 / 4; }
  .desktop-control-face :global(.desktop-controls-left),
  .desktop-control-face :global(.desktop-controls-right) {
    overflow-y: auto; min-height: 0;
    /* Scrollable sidebars must not contribute their full content height. */
    contain: size;
  }
  .desktop-control-face .content-row { display: flex; flex: 1; min-height: 280px; contain: size; }
  .desktop-control-face .content-center { width: 100%; }
  .desktop-control-face :global(.spectrum-toolbar) { height: auto; min-height: 32px; flex-wrap: wrap; }
  .desktop-control-face :global([data-zone-id='meters']),
  .desktop-control-face .standard-bottom-dock { grid-area: 5 / 1 / 6 / -1; }
  .standard-panel-owner,
  .standard-bottom-dock {
    display: flex;
    flex-direction: column;
    gap: 8px;
    min-width: 0;
  }
  .standard-bottom-dock { min-height: 48px; }
  .standard-panel-owner.cross-drop-target,
  .standard-bottom-dock.cross-drop-target {
    outline: 2px solid var(--v2-accent, #4af);
    outline-offset: -2px;
  }
  .standard-panel-owner :global(.semantic-control-panel),
  .standard-panel-owner :global(.left-sidebar > .collapsible-panel),
  .standard-panel-owner :global(.right-sidebar > .collapsible-panel) {
    flex-shrink: 0;
  }
  .standard-panel-owner > :global(.surface-zone),
  .standard-bottom-dock > :global(.surface-zone),
  .standard-panel-owner > .content-left,
  .standard-panel-owner > .content-right,
  .standard-bottom-dock > .content-left,
  .standard-bottom-dock > .content-right { display: contents; }
  .tx-aux-finite-grid { display: flex; flex-wrap: wrap; gap: 0.5rem; }
  .standard-tx-controls {
    display: flex; flex-direction: column; gap: 6px; padding: 0 8px 8px;
  }
  .standard-tx-button-grid {
    display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 6px;
  }
  .standard-tx-seat { display: contents; }
  .standard-tx-compound {
    display: grid; grid-template-columns: minmax(0, 1fr) 28px; min-width: 0;
  }
  .standard-tx-settings-trigger,
  .standard-tx-disclosure {
    min-width: 0; min-height: 30px;
    border: 1px solid var(--v2-border-subtle, rgba(255,255,255,.18));
    border-radius: 3px; color: var(--v2-text-primary, #e7eef7);
    background: var(--v2-bg-raised, #202932); font: inherit; font-weight: 700;
    cursor: pointer;
  }
  .standard-tx-settings-trigger {
    border-inline-start: 0; border-start-start-radius: 0; border-end-start-radius: 0;
  }
  .standard-tx-disclosure { padding: 0 8px; }
  .standard-tx-settings-trigger:hover,
  .standard-tx-disclosure:hover,
  .standard-tx-settings-trigger[aria-expanded='true'],
  .standard-tx-disclosure[aria-expanded='true'] {
    border-color: var(--v2-accent, #4af); color: var(--v2-accent, #4af);
  }
  .standard-tx-settings-trigger:focus-visible,
  .standard-tx-disclosure:focus-visible {
    outline: 2px solid var(--v2-accent, #4af); outline-offset: 1px;
  }
  .standard-tx-settings-popover {
    position: fixed; z-index: 1200; box-sizing: border-box;
    display: flex; flex-direction: column; gap: 8px;
    width: min(360px, calc(100vw - 16px)); padding: 10px; overflow: auto;
    border: 1px solid var(--v2-accent, #4af); border-radius: 4px;
    background: var(--v2-bg-panel, #111820); box-shadow: 0 10px 28px rgba(0,0,0,.55);
  }
  .standard-tx-scalar-seat { min-width: 0; }
  .standard-tx-scalar-seat :global(.vc-hbar) { width: 100%; min-width: 0; }
  /* Stable Standard RX/TX composition: reserve the larger TX meter footprint.
     Individual readings remain absent while irrelevant; only the shell is sized. */
  .standard-bottom-dock :global([data-panel-id='semantic-meters']) { height: 160px; }
  .standard-bottom-dock :global([data-panel-id='semantic-meters'] .collapsible-content) {
    min-height: 0; overflow: hidden;
  }
  /* Routine authority explanations remain in the key's accessible description,
     without growing the Standard panel during an already-active TX session. */
  .desktop-control-face.standard-face :global(.rx-tx-blocked [data-reason='tx-busy']),
  .desktop-control-face.standard-face :global(.rx-tx-blocked [data-reason='radio-transmitting']),
  .desktop-control-face.standard-face :global([data-testid='tx-aux-tune-blocked'] [data-reason='tx-busy']),
  .desktop-control-face.standard-face :global([data-testid='tx-aux-tune-blocked'] [data-reason='radio-transmitting']) {
    display: none;
  }
  .dsp-finite-grid {
    display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 6px;
  }
  .dsp-finite-seat { display: contents; }
  .agc-finite-grid { display: block; width: 100%; }
  .agc-finite-grid .dsp-finite-seat { display: contents; }
  .agc-finite-grid :global([role='radiogroup']) {
    display: grid; grid-template-columns: repeat(auto-fit, minmax(0, 1fr)); width: 100%;
  }
  .rf-front-end-finite-grid { display: flex; flex-direction: column; gap: 0.5rem; min-width: 0; }
  .rf-front-end-finite-seat { display: contents; }
  /* The `.filter-finite-*` shape, not the siblings' wrap row: a wrap row let
     each seat shrink to its content, and the desktop-v2 skin stretches
     `.rx-audio-row` buttons with `flex: 1 1 0`, which needs the row to span
     the panel. Box-less seats keep a structurally absent handle from
     spending a gap.
     0.25rem is the gap `RxAudioSurface.svelte` puts between these same rows
     when it groups them itself. */
  .rx-audio-finite-grid { display: flex; flex-direction: column; gap: 0.25rem; }
  .rx-audio-finite-seat { display: contents; }
  .rx-audio-finite-grid :global([data-testid='rx-audio-monitor']),
  .rx-audio-finite-grid :global([data-testid='rx-audio-focus']) {
    display: grid; grid-template-columns: repeat(auto-fit, minmax(0, 1fr)); gap: 4px;
  }
  .dsp-scalar-grid {
    display: grid;
    grid-template-columns: repeat(2, minmax(0, 1fr));
    gap: 4px 8px;
  }
  .dsp-scalar-seat { min-width: 0; }
  .filter-finite-grid { display: flex; flex-direction: column; gap: 0.5rem; }
  .filter-finite-seat { display: contents; }
  .standard-mode-layout { display: flex; flex-direction: column; gap: 0.75rem; }
  .filter-finite-grid .filter-finite-seat[data-field='filter'] :global(.filter-choice-group) {
    display: flex;
  }
  .band-control-grid, .band-control-seat { display: contents; }
  .antenna-control-grid { display: flex; flex-direction: column; gap: 0.25rem; }
  .antenna-fixed-port {
    display: flex; align-items: baseline; justify-content: space-between; gap: 0.5rem;
    padding: 0.25rem 0.5rem;
  }
  .antenna-control-seat { display: contents; }
  .antenna-blocked { margin: 0; padding-inline-start: 1.2em; }
  .antenna-blocked:empty { display: none; }
  .vfo-operation-instrument-grid { display: flex; flex-wrap: wrap; gap: 0.5rem; }
  .tx-aux-scalar-grid {
    display: grid;
    grid-template-columns: repeat(2, minmax(0, 1fr));
    gap: 4px 8px;
  }
  .tx-aux-scalar-seat { min-width: 0; }
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
  .content-right,
  .desktop-control-face.standard-face :global(.desktop-controls-left),
  .desktop-control-face.standard-face :global(.desktop-controls-right) {
    min-height: 0;
    max-height: 100%;
    overflow-y: auto;
    overflow-x: hidden;
    padding-bottom: 4px;
    /* Hide scrollbar but keep scroll functionality */
    scrollbar-width: none; /* Firefox */
    -ms-overflow-style: none; /* IE/Edge */
  }

  .radio-layout.desktop-control-face.standard-face {
    scrollbar-width: none;
  }

  .radio-layout.desktop-control-face.standard-face::-webkit-scrollbar,
  .content-left::-webkit-scrollbar,
  .content-right::-webkit-scrollbar,
  .desktop-control-face.standard-face :global(.desktop-controls-left)::-webkit-scrollbar,
  .desktop-control-face.standard-face :global(.desktop-controls-right)::-webkit-scrollbar {
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
    .radio-layout.desktop-control-face.standard-face {
      grid-template-columns: 208px minmax(0, 1fr) 208px;
    }

    .content-row {
      grid-template-columns: 208px minmax(0, 1fr) 208px;
    }
  }

  @media (max-width: 1024px) {
    .standard-bottom-dock :global([data-panel-id='semantic-meters']) { height: 260px; }
    .radio-layout {
      grid-template-rows: 28px auto minmax(0, auto) auto auto;
    }

    .radio-layout.desktop-control-face { grid-template-columns: 190px minmax(0, 1fr) 190px; }
    .radio-layout.desktop-control-face.standard-face {
      grid-template-columns: minmax(0, 1fr);
      grid-template-rows: auto 28px auto auto minmax(320px, auto) auto auto;
    }
    .desktop-control-face.standard-face :global([data-zone-id='receiver-deck']) {
      grid-area: 3 / 1 / 4 / 2;
    }
    .desktop-control-face.standard-face :global(.desktop-controls-left) {
      grid-area: 4 / 1 / 5 / 2;
    }
    .desktop-control-face.standard-face :global(.desktop-controls-center) {
      grid-area: 5 / 1 / 6 / 2;
    }
    .desktop-control-face.standard-face :global(.desktop-controls-right) {
      grid-area: 6 / 1 / 7 / 2;
    }
    .desktop-control-face.standard-face :global([data-zone-id='meters']),
    .desktop-control-face.standard-face .standard-bottom-dock {
      grid-area: 7 / 1 / 8 / 2;
    }
    .desktop-control-face.standard-face :global(.desktop-controls-left),
    .desktop-control-face.standard-face :global(.desktop-controls-right) {
      contain: inline-size;
      max-height: 360px;
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
