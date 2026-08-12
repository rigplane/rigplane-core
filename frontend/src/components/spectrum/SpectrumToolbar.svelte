<script lang="ts">
  import { getTuningStep, adjustTuningStep, isAutoStep, setAutoStep, formatStep } from '../../lib/stores/tuning.svelte';
  import { t } from '$lib/i18n';
  import { type ColorSchemeName } from '../../lib/renderers/waterfall-renderer';
  import { hasCapability, hasDualReceiver } from '../../lib/stores/capabilities.svelte';
  import { runtime } from '$lib/runtime/frontend-runtime';
  import { toSpectrumAuthority } from '$lib/runtime/adapters/scope-adapter';
  import { bindSemanticSurfaceHandlers } from '$lib/runtime/adapters/panel-adapters';
  import ScopeSettingsPopover from './ScopeSettingsPopover.svelte';
  import {
    SPAN_LABELS, SPEED_LABELS, SPEED_STATIC_LABEL, MODE_BUTTONS,
    toggleLayer as toggleLayerFn, isLayerVisible,
    isSpanApplicable, isEdgeApplicable,
    clampSpan, clampSpeed, clampBrt, clampRef,
  } from './spectrum-toolbar-logic';

  interface LayerInfo {
    name: string;
    layer: string;
    file: string;
  }

  let {
    enableAvg = $bindable(true),
    enablePeakHold = $bindable(true),
    brtLevel = $bindable(0),
    colorScheme = $bindable('classic' as ColorSchemeName),
    fullscreen = $bindable(false),
    showBandPlan = $bindable(true),
    hiddenLayers = $bindable([] as string[]),
    showEiBi = $bindable(false),
    scopeDemandOn = true,
    onScopeDemandChange = (_enabled: boolean) => {},
    /**
     * Hide the DUAL + MAIN/SUB scope-source controls. Set by layouts that
     * surface these controls elsewhere (e.g. v2 desktop VfoHeader bridge,
     * issue #832). Other layouts (v1 desktop/mobile, v2 mobile chip view)
     * leave this `false` so the scope source remains reachable (#832 follow-up).
     */
    hideSourceControls = false,
    /**
     * MOR-1369 (v3-rework S6b-1) — hides the FACT-BACKED half of this
     * toolbar: the scope MODE/EDGE buttons, the SPAN/SPEED/HOLD/REF/DUAL/
     * receiver controls, and the settings-gear popover (the four
     * `ScopeSettingsPopover` leaves — centerType/rbw/duringTx/vbwNarrow).
     * Every one of those reads a `scopeControls.*` field the backend gives a
     * field-status entry for (the MOR-1311 `ScopeControlsSurface`
     * vocabulary). Set by a layout whose manifest declares a `scopeControls`
     * zone (S6b-2) — the semantic surface then owns that half.
     *
     * The client-side VIEW OPTIONS below — AVG/PEAK, BRT, color scheme,
     * fullscreen, BANDS/layers, EiBi — have no wire field and no
     * field-status entry (they configure the browser session, not the
     * radio). Per the S10 boundary ruling
     * (docs/plans/2026-08-06-settings-modal-boundary.md, category (b) — the
     * same category as `LANGUAGE`/`WORKSPACE`), they stay legacy and are
     * NEVER gated on this prop, in either direction. `VIEW ON/OFF`
     * (`scopeDemandOn`) is client-side scope-streaming demand, not a
     * `scopeControls.*` field either, and also stays unconditional.
     *
     * Landed INERT (MOR-1369, S6b-1): no manifest declares a `scopeControls`
     * zone yet, so this defaults `false` and nothing renders differently.
     * Safe only because an omitting caller keeps the prop `false` — the same
     * shape as `hideSourceControls` above and the MOR-1364 `hideTxPanel`/
     * `declared` channel (S5-N3: safe because the surface degrades to a bare
     * render when unzoned, a guarantee that lives in
     * `SemanticRadioSurfaces.svelte`, not here).
     */
    hideScopeControls = false,
    /**
     * MOR-1486 ruling B (owner, session 19): the AUTO toggle re-enables
     * mode-follow, which only does anything ongoing when the active layout
     * actually drives `applyModeDefault()` on mode changes (currently only
     * `RadioLayout.svelte` — see its `$effect` — and, per ruling A,
     * `LcdLayout.svelte`). `MobileRadioLayout.svelte` has no such driver
     * AND its own STEP picker is disconnected local state that can
     * disagree with this shared store on the same screen (MOR-1509,
     * design-research ticket owner-commissioned for the right mobile step
     * UX). Showing a toggle that silently does nothing on subsequent mode
     * changes would be exactly the invisible-state-change dishonesty this
     * ticket exists to close, so the gate is structural — a prop the
     * owning layout passes — NOT a skin-name string check. Defaults
     * `false` (toggle shown) so `RadioLayout`, which does have the driver,
     * needs no change; `MobileRadioLayout` passes `true` explicitly.
     */
    hideAutoStepToggle = false,
  } = $props();

  const scopeHandlers = bindSemanticSurfaceHandlers().scopeControls;

  let showSettings = $state(false);
  let showDisplayGear = $state(false);
  let layerDropdownOpen = $state(false);
  let layerToggleBtn = $state<HTMLElement | null>(null);
  let dropdownStyle = $derived.by(() => {
    if (!layerDropdownOpen || !layerToggleBtn) return '';
    const rect = layerToggleBtn.getBoundingClientRect();
    const top = rect.bottom + 4;
    const right = window.innerWidth - rect.right;
    return `top: ${top}px; right: ${right}px;`;
  });
  let availableLayers = $state<LayerInfo[]>([]);
  let currentRegion = $state('US');
  let availableRegions = $state<string[]>([]);

  // Fetch available layers and config from REST API
  async function fetchLayers() {
    try {
      const [layerResp, configResp] = await Promise.all([
        fetch('/api/v1/band-plan/layers'),
        fetch('/api/v1/band-plan/config'),
      ]);
      if (layerResp.ok) {
        const data = await layerResp.json();
        availableLayers = data.layers ?? [];
      }
      if (configResp.ok) {
        const config = await configResp.json();
        currentRegion = config.region ?? 'US';
        availableRegions = config.availableRegions ?? [];
      }
    } catch { /* ignore */ }
  }

  async function setRegion(region: string) {
    try {
      const resp = await fetch('/api/v1/band-plan/config', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ region }),
      });
      if (resp.ok) {
        currentRegion = region;
        // Refetch layers (they may change with region)
        await fetchLayers();
      }
    } catch { /* ignore */ }
  }

  // Load on mount
  if (typeof window !== 'undefined') {
    fetchLayers();
  }

  function toggleLayer(layer: string) {
    hiddenLayers = toggleLayerFn(hiddenLayers, layer);
  }

  let stepHz = $derived(getTuningStep());
  let stepLabel = $derived(formatStep(stepHz));
  let autoStep = $derived(isAutoStep());

  function cycleStep(e: MouseEvent) {
    e.preventDefault();
    adjustTuningStep('up');
  }

  function cycleStepDown(e: MouseEvent) {
    e.preventDefault();
    adjustTuningStep('down');
  }

  /**
   * MOR-1486: the 'A' badge used to be a passive, non-interactive glyph —
   * `setAutoStep(true)` was unreachable from any control once a manual step
   * change (this control, ArrowUp/Down, or the RC-28 companion) disabled
   * it; only a fresh browser profile ever restored it. This makes the
   * state a real toggle: `aria-pressed` + a title explaining both states
   * (see `core.spectrum.autoStep.*` in the i18n catalog) so the operator
   * can always find their way back.
   */
  function toggleAutoStep(e: MouseEvent) {
    e.preventDefault();
    setAutoStep(!autoStep);
  }

  let scopeFacts = $derived(toSpectrumAuthority(runtime.state, runtime.caps)?.scopeControls ?? null);

  type NumberScopeField = 'mode' | 'edge' | 'span' | 'speed' | 'refDb' | 'receiver';
  type BooleanScopeField = 'hold' | 'dual';

  function acceptedNumber(field: NumberScopeField, min: number, max: number): number | null {
    const fact = scopeFacts?.[field];
    if (!fact?.availability?.structural || !fact.availability.operational
      || fact.reading?.status !== 'known' || !Number.isSafeInteger(fact.reading.value)
      || fact.reading.value < min || fact.reading.value > max) return null;
    return fact.reading.value;
  }

  function acceptedBoolean(field: BooleanScopeField): boolean | null {
    const fact = scopeFacts?.[field];
    if (!fact?.availability?.structural || !fact.availability.operational
      || fact.reading?.status !== 'known' || typeof fact.reading.value !== 'boolean') return null;
    return fact.reading.value;
  }

  let scopeMode = $derived(acceptedNumber('mode', 0, 3));
  let scopeEdge = $derived(acceptedNumber('edge', 1, 4));
  let scopeSpan = $derived(acceptedNumber('span', 0, 7));
  let scopeSpeed = $derived(acceptedNumber('speed', 0, 2));
  let scopeHold = $derived(acceptedBoolean('hold'));
  let scopeRef = $derived(acceptedNumber('refDb', -30, 10));
  let scopeDual = $derived(acceptedBoolean('dual'));
  let scopeReceiver = $derived(acceptedNumber('receiver', 0, 1));

  let spanApplicable = $derived(scopeMode !== null && isSpanApplicable(scopeMode));
  let edgeApplicable = $derived(scopeMode !== null && isEdgeApplicable(scopeMode));

  function selectMode(mode: number) {
    if (scopeMode === null || !Number.isSafeInteger(mode) || mode < 0 || mode > 3) return;
    scopeHandlers.onModeChange(mode);
  }

  function selectEdge(edge: number) {
    if (scopeEdge === null || !Number.isSafeInteger(edge) || edge < 1 || edge > 4) return;
    scopeHandlers.onEdgeChange(edge);
  }

  function cycleSpan(delta: -1 | 1) {
    if (scopeSpan === null) return;
    scopeHandlers.onSpanChange(clampSpan(scopeSpan, delta));
  }

  function cycleSpeed(delta: -1 | 1) {
    if (scopeSpeed === null) return;
    scopeHandlers.onSpeedChange(clampSpeed(scopeSpeed, delta));
  }

  function toggleHold() {
    if (scopeHold === null) return;
    scopeHandlers.onHoldChange(!scopeHold);
  }

  function changeRef(delta: -5 | 0 | 5) {
    if (scopeRef === null) return;
    scopeHandlers.onRefChange(delta === 0 ? 0 : clampRef(scopeRef, delta));
  }

  function toggleDual() {
    if (scopeDual === null) return;
    scopeHandlers.onDualChange(!scopeDual);
  }

  function switchReceiver() {
    if (scopeReceiver === null) return;
    scopeHandlers.onReceiverChange(scopeReceiver === 1 ? 0 : 1);
  }
</script>

<div class="spectrum-toolbar">
  <!-- Group A: Tuning (no wash) -->
  <div class="toolbar-group step-group">
    <button
      class="toolbar-btn small step-arrow"
      onclick={cycleStepDown}
      title="Decrease tuning step"
    >◀</button>
    <button
      class="toolbar-btn step-control"
      onclick={cycleStep}
      oncontextmenu={cycleStepDown}
      title="Click to step up, right-click to step down"
    >
      <span class="toolbar-label">STEP</span>
      <span class="toolbar-value">{stepLabel}</span>
    </button>
    <button
      class="toolbar-btn small step-arrow"
      onclick={cycleStep}
      title="Increase tuning step"
    >▶</button>
    {#if !hideAutoStepToggle}
      <button
        class="toolbar-btn small auto-step-toggle"
        class:active={autoStep}
        aria-pressed={autoStep}
        onclick={toggleAutoStep}
        title={autoStep ? t('core.spectrum.autoStep.onTitle') : t('core.spectrum.autoStep.offTitle')}
      >AUTO</button>
    {/if}
  </div>
  {#if hasCapability('scope')}
    <div class="toolbar-separator"></div>
    <!-- Group B: Scope mode (cyan wash) -->
    <div class="toolbar-group-b">
      <button
        class="toolbar-btn scope-demand-toggle"
        class:active={scopeDemandOn}
        aria-pressed={scopeDemandOn}
        onclick={() => onScopeDemandChange(!scopeDemandOn)}
        title="Request scope viewer data"
      >VIEW {scopeDemandOn ? 'ON' : 'OFF'}</button>
      {#if !hideScopeControls}
        <div class="toolbar-sub-separator"></div>
        <div class="toolbar-group">
          {#each MODE_BUTTONS as [m, label]}
            <button
              class="toolbar-btn small"
              class:active={scopeMode !== null && scopeMode === m}
              disabled={scopeMode === null}
              onclick={() => selectMode(m)}
              title="Scope mode: {label}"
            >{label}</button>
          {/each}
        </div>
        {#if edgeApplicable}
          <div class="toolbar-sub-separator"></div>
          <div class="toolbar-group">
            <span class="toolbar-label">EDGE</span>
            {#each [1, 2, 3, 4] as e}
              <button
                class="toolbar-btn small"
                class:active={scopeEdge !== null && scopeEdge === e}
                disabled={scopeEdge === null}
                onclick={() => selectEdge(e)}
              >{e}</button>
            {/each}
          </div>
        {/if}
      {/if}
    </div>
    {#if !hideScopeControls}
      <div class="toolbar-separator"></div>
      <!-- Group C: Scope data (cyan wash) -->
      <div class="toolbar-group-c">
      {#if spanApplicable}
        <div class="toolbar-group step-group">
          <button class="toolbar-btn small step-arrow" disabled={scopeSpan === null} onclick={() => cycleSpan(-1)} title="Decrease span">◀</button>
          <button class="toolbar-btn step-control" disabled={scopeSpan === null} onclick={() => cycleSpan(1)} title="Scope span">
            <span class="toolbar-label">SPAN</span>
            <span class="toolbar-value">{scopeSpan === null ? '—' : (SPAN_LABELS[scopeSpan] ?? '—')}</span>
          </button>
          <button class="toolbar-btn small step-arrow" disabled={scopeSpan === null} onclick={() => cycleSpan(1)} title="Increase span">▶</button>
        </div>
        <div class="toolbar-sub-separator"></div>
      {/if}
      <div class="toolbar-group step-group">
        <button class="toolbar-btn small step-arrow" disabled={scopeSpeed === null} onclick={() => cycleSpeed(-1)} title="Decrease speed">◀</button>
        <button class="toolbar-btn step-control" disabled={scopeSpeed === null} onclick={() => cycleSpeed(1)} title="Scope sweep speed">
          <span class="toolbar-label">{SPEED_STATIC_LABEL}</span>
          <span class="toolbar-value">{scopeSpeed === null ? '—' : (SPEED_LABELS[scopeSpeed] ?? '—')}</span>
        </button>
        <button class="toolbar-btn small step-arrow" disabled={scopeSpeed === null} onclick={() => cycleSpeed(1)} title="Increase speed">▶</button>
      </div>
      <div class="toolbar-sub-separator"></div>
      <div class="toolbar-group">
        <button class="toolbar-btn" class:active={scopeHold === true} disabled={scopeHold === null} onclick={toggleHold} title="Scope hold">HOLD</button>
      </div>
      <div class="toolbar-sub-separator hide-mobile"></div>
      <div class="toolbar-group hide-mobile">
        <span class="toolbar-label">REF</span>
        <button class="toolbar-btn small" disabled={scopeRef === null} onclick={() => changeRef(-5)}>−</button>
        <span class="toolbar-value ref-value">{scopeRef === null ? '—' : `${scopeRef > 0 ? '+' : ''}${scopeRef}`}</span>
        <button class="toolbar-btn small" disabled={scopeRef === null} onclick={() => changeRef(5)}>+</button>
      </div>
      {#if !hideSourceControls && hasDualReceiver()}
        <div class="toolbar-sub-separator"></div>
        <div class="toolbar-group">
          <button class="toolbar-btn" class:active={scopeDual === true} disabled={scopeDual === null} onclick={toggleDual} title="Dual scope">DUAL</button>
          <button class="toolbar-btn" disabled={scopeReceiver === null} onclick={switchReceiver} title="Switch scope receiver">
            {scopeReceiver === null ? '—' : (scopeReceiver === 1 ? 'SUB' : 'MAIN')}
          </button>
        </div>
      {/if}
    </div>
    {/if}
  {/if}
  <div class="toolbar-separator"></div>
  <!-- Group D: Display (neutral wash) -->
  <div class="toolbar-group-d">
    <div class="toolbar-group">
      <button class="toolbar-btn" class:active={enableAvg} onclick={() => (enableAvg = !enableAvg)}>AVG</button>
      <button class="toolbar-btn" class:active={enablePeakHold} onclick={() => (enablePeakHold = !enablePeakHold)}>PEAK</button>
    </div>
    <div class="toolbar-sub-separator hide-mobile"></div>
    <div class="toolbar-group hide-mobile">
      <span class="toolbar-label">BRT</span>
      <button class="toolbar-btn small" onclick={() => (brtLevel = clampBrt(brtLevel, -5))}>−</button>
      <span class="toolbar-value ref-value">{brtLevel > 0 ? '+' : ''}{brtLevel}</span>
      <button class="toolbar-btn small" onclick={() => (brtLevel = clampBrt(brtLevel, 5))}>+</button>
    </div>
    <div class="toolbar-group display-gear-group show-mobile">
      <button
        class="toolbar-btn small"
        onclick={() => (showDisplayGear = !showDisplayGear)}
        title="Display settings (BRT / REF)"
        aria-label="Display settings"
      >&#9881;</button>
      {#if showDisplayGear}
        <button type="button" class="popover-backdrop" onclick={() => (showDisplayGear = false)} aria-label="Close display settings"></button>
        <div class="display-gear-popover">
          <div class="gear-header">
            <span>Display</span>
            <button class="gear-close" onclick={() => (showDisplayGear = false)} aria-label="Close">×</button>
          </div>
          <div class="gear-row">
            <span class="gear-label">BRT</span>
            <button class="gear-btn" onclick={() => (brtLevel = clampBrt(brtLevel, -5))} aria-label="Decrease brightness">−</button>
            <span class="gear-value">{brtLevel > 0 ? '+' : ''}{brtLevel}</span>
            <button class="gear-btn" onclick={() => (brtLevel = clampBrt(brtLevel, 5))} aria-label="Increase brightness">+</button>
            <button class="gear-btn gear-btn-zero" onclick={() => (brtLevel = 0)} aria-label="Reset brightness">0</button>
          </div>
          {#if hasCapability('scope') && !hideScopeControls}
            <div class="gear-row">
              <span class="gear-label">REF</span>
              <button class="gear-btn" disabled={scopeRef === null} onclick={() => changeRef(-5)} aria-label="Decrease reference">−</button>
              <span class="gear-value">{scopeRef === null ? '—' : `${scopeRef > 0 ? '+' : ''}${scopeRef}`}</span>
              <button class="gear-btn" disabled={scopeRef === null} onclick={() => changeRef(5)} aria-label="Increase reference">+</button>
              <button class="gear-btn gear-btn-zero" disabled={scopeRef === null} onclick={() => changeRef(0)} aria-label="Reset reference">0</button>
            </div>
          {/if}
        </div>
      {/if}
    </div>
    <div class="toolbar-sub-separator"></div>
    <div class="toolbar-group">
      <select class="toolbar-select" bind:value={colorScheme}>
        <option value="classic">Classic</option>
        <option value="thermal">Thermal</option>
        <option value="grayscale">Gray</option>
      </select>
    </div>
    <div class="toolbar-sub-separator"></div>
    <div class="toolbar-group bands-group">
    <button class="toolbar-btn" class:active={showBandPlan} onclick={() => (showBandPlan = !showBandPlan)} title="Show/hide band plan overlay">
      BANDS
    </button>
    {#if showBandPlan && availableLayers.length > 1}
      <button
        class="toolbar-btn small layer-toggle-btn"
        bind:this={layerToggleBtn}
        onclick={() => (layerDropdownOpen = !layerDropdownOpen)}
        title="Select visible layers"
      >▾</button>
      {#if layerDropdownOpen}
        <button type="button" class="layer-dropdown-backdrop" onclick={() => (layerDropdownOpen = false)} aria-label="Close layer menu"></button>
        <!-- svelte-ignore a11y_no_static_element_interactions -->
        <div class="layer-dropdown" style={dropdownStyle}>
          {#if availableRegions.length > 1}
            <div class="dropdown-section-label">Region</div>
            <div class="region-selector">
              {#each availableRegions as region}
                <button
                  class="region-btn"
                  class:active={region === currentRegion}
                  onclick={() => setRegion(region)}
                >{region}</button>
              {/each}
            </div>
            <div class="dropdown-divider"></div>
          {/if}
          <div class="dropdown-section-label">Layers</div>
          {#each availableLayers as layer}
            <label class="layer-option">
              <input
                type="checkbox"
                checked={isLayerVisible(hiddenLayers, layer.layer)}
                onchange={() => toggleLayer(layer.layer)}
              />
              <span class="layer-name">{layer.name}</span>
            </label>
          {/each}
          <div class="dropdown-divider"></div>
          <button
            class="eibi-browser-btn"
            onclick={() => { showEiBi = true; layerDropdownOpen = false; }}
          >📻 EiBi Stations...</button>
        </div>
      {/if}
    {/if}
    </div>
  </div>
  {#if hasCapability('scope') && !hideScopeControls}
    <div class="toolbar-separator"></div>
    <!-- Group E: Settings (no wash) -->
    <div class="toolbar-group settings-group">
      <button class="toolbar-btn small" onclick={() => showSettings = !showSettings} title="Scope settings">&#9881;</button>
      {#if showSettings}
        <ScopeSettingsPopover onClose={() => showSettings = false} />
      {/if}
    </div>
  {/if}
  <div class="toolbar-spacer"></div>
  <!-- Group F: Actions (no wash) -->
  <button class="toolbar-btn icon-btn" onclick={() => (fullscreen = !fullscreen)} title="Toggle fullscreen">
    {fullscreen ? '✕' : '⛶'}
  </button>
</div>

<style>
  .spectrum-toolbar {
    display: flex;
    align-items: center;
    height: 32px;
    padding: 0 8px;
    background: linear-gradient(180deg, #2a2a2a 0%, #1e1e1e 100%);
    border-bottom: 1px solid var(--panel-border);
    gap: 4px;
    flex-shrink: 0;
    font-family: 'Roboto Mono', monospace;
    font-size: 11px;
    user-select: none;
  }

  .toolbar-group {
    display: flex;
    align-items: center;
    gap: 3px;
  }

  /* Group containers with wash backgrounds (visual grouping) */
  .toolbar-group-b,
  .toolbar-group-c,
  .toolbar-group-d {
    display: flex;
    align-items: center;
    gap: 3px;
    padding: 0 4px;
    border-radius: 3px;
    height: 24px;
  }

  .toolbar-group-b,
  .toolbar-group-c {
    /* Scope state — cyan wash */
    background: rgba(0, 212, 255, 0.03);
  }

  .toolbar-group-d {
    /* Display only — neutral wash */
    background: rgba(255, 255, 255, 0.02);
  }

  .toolbar-separator {
    width: 2px;
    height: 20px;
    background: var(--panel-border);
    margin: 0 6px;
  }

  .toolbar-sub-separator {
    width: 1px;
    height: 14px;
    background: var(--panel-border);
    opacity: 0.5;
    margin: 0 4px;
  }

  .toolbar-spacer {
    flex: 1;
  }

  .toolbar-btn {
    display: flex;
    align-items: center;
    gap: 3px;
    padding: 2px 6px;
    background: transparent;
    border: 1px solid transparent;
    border-radius: 3px;
    color: var(--text-muted);
    font-family: inherit;
    font-size: inherit;
    cursor: pointer;
    white-space: nowrap;
    line-height: 1;
    height: 22px;
  }

  .toolbar-btn:hover {
    background: rgba(255, 255, 255, 0.08);
    color: var(--text);
  }

  .toolbar-btn.active {
    color: #00d4ff;
    border-color: rgba(0, 212, 255, 0.3);
    background: rgba(0, 212, 255, 0.1);
  }

  .toolbar-btn.small {
    padding: 2px 4px;
    min-width: 18px;
    justify-content: center;
  }

  .toolbar-label {
    color: var(--text-muted);
    text-transform: uppercase;
    letter-spacing: 0.05em;
    font-weight: 500;
  }

  .toolbar-value {
    color: var(--text);
    font-variant-numeric: tabular-nums;
    font-weight: 600;
  }

  .ref-value {
    min-width: 28px;
    text-align: center;
  }

  .step-control .toolbar-value {
    min-width: 48px;
    text-align: center;
  }

  /* Auto-step toggle keeps the amber tone the old passive badge used, so
     the "this is following the mode automatically" association carries
     over even though it is now a real, clickable control. */
  .auto-step-toggle.active {
    color: #fbbf24;
    border-color: rgba(251, 191, 36, 0.4);
    background: rgba(251, 191, 36, 0.12);
  }

  .step-group {
    gap: 0 !important;
  }

  .step-arrow {
    font-size: 8px !important;
    padding: 2px 3px !important;
    min-width: 16px !important;
    color: var(--text-muted) !important;
    opacity: 0.6;
    transition: opacity 0.15s;
  }

  .step-arrow:hover {
    opacity: 1;
    color: #00d4ff !important;
  }

  .toolbar-select {
    background: transparent;
    border: 1px solid transparent;
    border-radius: 3px;
    color: var(--text-muted);
    font-family: inherit;
    font-size: inherit;
    cursor: pointer;
    padding: 2px 4px;
    height: 22px;
  }

  .toolbar-select:hover {
    background: rgba(255, 255, 255, 0.08);
    color: var(--text);
  }

  .toolbar-select:focus {
    outline: none;
    border-color: var(--accent);
  }

  .icon-btn {
    font-size: 14px;
    width: 22px;
    height: 22px;
    justify-content: center;
    padding: 0;
  }

  .settings-group {
    position: relative;
  }

  .bands-group {
    position: relative;
  }

  .layer-toggle-btn {
    padding: 2px 3px !important;
    min-width: 16px !important;
    font-size: 9px !important;
  }

  .layer-dropdown-backdrop {
    position: fixed;
    inset: 0;
    z-index: 999;
    background: none;
    border: none;
    padding: 0;
    cursor: default;
  }

  .layer-dropdown {
    position: fixed;
    z-index: 1000;
    min-width: 180px;
    max-height: 70vh;
    overflow-y: auto;
    background: var(--v2-bg-darkest, #0a0a0f);
    border: 1px solid var(--v2-border, #2a2a3e);
    border-radius: 6px;
    padding: 4px 0;
    box-shadow: 0 8px 32px rgba(0, 0, 0, 0.6);
  }

  .layer-option {
    display: flex;
    align-items: center;
    gap: 6px;
    padding: 4px 10px;
    cursor: pointer;
    color: var(--v2-text-primary, #e0e0e0);
    font-size: 10px;
    white-space: nowrap;
  }

  .layer-option:hover {
    background: rgba(255, 255, 255, 0.08);
  }

  .layer-option input[type="checkbox"] {
    accent-color: #00d4ff;
    width: 12px;
    height: 12px;
  }

  .layer-name {
    font-family: 'Roboto Mono', monospace;
  }

  .dropdown-section-label {
    padding: 4px 10px 2px;
    color: var(--v2-text-dim, #666);
    font-size: 8px;
    text-transform: uppercase;
    letter-spacing: 0.1em;
    font-weight: 600;
  }

  .dropdown-divider {
    height: 1px;
    background: var(--v2-border, #2a2a3e);
    margin: 4px 0;
  }

  .region-selector {
    display: flex;
    gap: 2px;
    padding: 2px 8px 4px;
    flex-wrap: wrap;
  }

  .region-btn {
    padding: 2px 6px;
    font-size: 9px;
    font-family: 'Roboto Mono', monospace;
    background: transparent;
    border: 1px solid var(--v2-border, #2a2a3e);
    border-radius: 3px;
    color: var(--v2-text-dim, #888);
    cursor: pointer;
    white-space: nowrap;
  }

  .region-btn:hover {
    background: rgba(255, 255, 255, 0.08);
    color: var(--v2-text-primary, #e0e0e0);
  }

  .region-btn.active {
    color: #00d4ff;
    border-color: rgba(0, 212, 255, 0.4);
    background: rgba(0, 212, 255, 0.1);
  }

  .eibi-browser-btn {
    display: block;
    width: calc(100% - 16px);
    margin: 4px 8px;
    padding: 5px 8px;
    font-size: 11px;
    font-family: 'Roboto Mono', monospace;
    background: rgba(192, 132, 252, 0.1);
    border: 1px solid rgba(192, 132, 252, 0.3);
    border-radius: 4px;
    color: #C084FC;
    cursor: pointer;
    text-align: left;
  }

  .eibi-browser-btn:hover {
    background: rgba(192, 132, 252, 0.2);
  }

  /* ── Mobile: collapse BRT/REF into gear popover (issue #812) ── */
  .display-gear-group {
    position: relative;
  }

  .show-mobile {
    display: none;
  }

  @media (max-width: 640px) {
    .hide-mobile {
      display: none !important;
    }
    .show-mobile {
      display: flex;
    }
  }

  .popover-backdrop {
    position: fixed;
    inset: 0;
    z-index: 999;
    background: none;
    border: none;
    padding: 0;
    cursor: default;
  }

  .display-gear-popover {
    position: absolute;
    top: calc(100% + 4px);
    right: 0;
    z-index: 1000;
    min-width: 220px;
    background: var(--v2-bg-darkest, #0a0a0f);
    border: 1px solid var(--v2-border, #2a2a3e);
    border-radius: 6px;
    padding: 8px;
    box-shadow: 0 8px 32px rgba(0, 0, 0, 0.6);
    display: flex;
    flex-direction: column;
    gap: 8px;
  }

  .gear-header {
    display: flex;
    align-items: center;
    justify-content: space-between;
    padding: 2px 4px 6px;
    border-bottom: 1px solid var(--v2-border, #2a2a3e);
    color: var(--v2-text-primary, #e0e0e0);
    font-size: 11px;
    text-transform: uppercase;
    letter-spacing: 0.08em;
  }

  .gear-close {
    min-width: 44px;
    min-height: 44px;
    background: transparent;
    border: none;
    color: var(--v2-text-dim, #888);
    font-size: 20px;
    cursor: pointer;
    line-height: 1;
  }

  .gear-row {
    display: flex;
    align-items: center;
    gap: 6px;
  }

  .gear-label {
    flex: 0 0 40px;
    color: var(--text-muted);
    font-size: 11px;
    text-transform: uppercase;
    letter-spacing: 0.05em;
    font-weight: 500;
  }

  .gear-btn {
    min-width: 44px;
    min-height: 44px;
    background: transparent;
    border: 1px solid var(--v2-border, #2a2a3e);
    border-radius: 4px;
    color: var(--v2-text-primary, #e0e0e0);
    font-family: inherit;
    font-size: 14px;
    font-weight: 600;
    cursor: pointer;
  }

  .gear-btn:hover,
  .gear-btn:active {
    background: rgba(0, 212, 255, 0.1);
    border-color: rgba(0, 212, 255, 0.4);
    color: #00d4ff;
  }

  .gear-btn-zero {
    font-size: 12px;
    opacity: 0.75;
  }

  .gear-value {
    flex: 1;
    text-align: center;
    color: var(--v2-text-primary, #e0e0e0);
    font-variant-numeric: tabular-nums;
    font-weight: 600;
    font-size: 13px;
    min-width: 36px;
  }
</style>
