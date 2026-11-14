<script lang="ts">
  import { resolveScopeSourceId, audioScopeFactsRevision, audioScopeProjection } from '$lib/runtime/adapters/scope-adapter';
  import { onDestroy, untrack, type Snippet } from 'svelte';
  import { ScopeRenderTrace, type TraceToken, type TraceSource, type TraceStatus } from '../../lib/runtime/scope-render-trace';
  import SpectrumCanvas from './SpectrumCanvas.svelte';
  import WaterfallCanvas from './WaterfallCanvas.svelte';
  import DxOverlay from './DxOverlay.svelte';
  import {
    defaultSpectrumOptions,
    resolveSpectrumColorRoles,
    spectrumColorRolesToOptions,
    type SpectrumColorRoles,
    type SpectrumOptions,
  } from '../../lib/renderers/spectrum-renderer';
  import {
    defaultWaterfallOptions,
    type WaterfallFrameIdentity,
    type WaterfallOptions,
    type ColorSchemeName,
  } from '../../lib/renderers/waterfall-renderer';
  import { presentationResources, runtime } from '../../lib/runtime/frontend-runtime';
  import { type DxSpot } from '../../lib/types/protocol';
  import { getTuningStep } from '../../lib/stores/tuning.svelte';
  import {
    getFilterHandlers,
    getFilterWidthCommandLifecycle,
    getPendingFrequencyHz,
    getTuningBurstFrequencyHz,
    isTuningBurstObservation,
    getVfoHandlers,
    bindSemanticSurfaceHandlers,
  } from '../../lib/runtime/adapters/panel-adapters';
  import {
    snapSpectrumFilterWidth,
    toSpectrumAuthority,
    type SpectrumAuthority,
  } from '../../lib/runtime/adapters/scope-adapter';
  import type { ManagedScopeRegion } from '../../lib/runtime/adapters/scope-display-projection';
  import type { ScopeFrame, ScopeGeometry } from '../../lib/runtime/scope-frame';
  import { projectScopeCoverage, type ScopeCoverageProjection } from '../../lib/runtime/scope-frame';
  import { scopeSlotKey } from '../../lib/runtime/adapters/scope-adapter';
  import {
    createScopeView, panView, viewCenterHz, zoomView, scopeSubscription, scopeSubscriptionSurface,
    type View,
  } from '../../lib/runtime/scope-view';
  import { ScopeRenderScheduler } from '../../lib/canvas/scope-render-scheduler';
  import { CanvasSurface } from '../../lib/canvas/canvas-surface.svelte';
  import { getStageScale } from '../../primitives/stage/stage-scale';
  import { scopePresets, type ScopePreset } from '../../lib/runtime/scope-presets';
  import { getManagedAppTxController } from '../../lib/runtime/tx-controller/managed-app-host';
  import type { ManagedTxState } from '../../lib/runtime/tx-controller/managed-state';
  import SpectrumToolbar from './SpectrumToolbar.svelte';
  import StatusIndicator from '../../lib/Button/StatusIndicator.svelte';
  import { getBandPlanRegion, loadBandPlanConfig } from '$lib/stores/band-plan-region.svelte';
  import BandPlanOverlay from './BandPlanOverlay.svelte';
  import EiBiBrowser from './EiBiBrowser.svelte';
  import { t } from '$lib/i18n';
  import {
    canResizeFromRightEdge,
    getFilterWidthFromRightEdgePx,
    getPassbandGeometry,
  } from './passband-geometry';
  import {
    formatFreqOffset,
    deriveFreqTicks,
    isFixedScope as isFixedScopeFn,
  } from './spectrum-logic';
  import { PanoramaViewportCenter } from './panorama-motion';
  import { hasCommandModifier } from '../../components-v2/layout/keyboard-map';
  import ScopeSourceBadge, { type ScopeSourceId } from '../../components-v2/spectrum/ScopeSourceBadge.svelte';
  import ScopeSourceSelector from '../../components-v2/spectrum/ScopeSourceSelector.svelte';
  import SdrTxFrozenIndicator from '../../components-v2/spectrum/SdrTxFrozenIndicator.svelte';

  // --- Props ---
  // `hideSourceControls` is forwarded to SpectrumToolbar so layouts that surface
  // the DUAL + MAIN/SUB scope-source controls elsewhere (v2 desktop VfoHeader
  // bridge, issue #832) can suppress the duplicate in the toolbar. Layouts that
  // do not surface them (v1 desktop/mobile, v2 mobile chip view) omit the prop
  // and keep the controls reachable (#832 mobile/v1 fallback).
  //
  // `hideScopeControls` is forwarded the same way (MOR-1369, v3-rework
  // S6b-1): it hides the toolbar's fact-backed `scopeControls.*` half once a
  // layout's manifest declares a `scopeControls` zone (S6b-2). Landed INERT
  // with S6b-1 and LIVE since MOR-1370 (S6b-2) declared that zone on
  // `desktop-v2`: `RadioLayout` forwards
  // `hideScopeControls={declared.has('scopeControls')}`, which is `true`
  // there. This prop stays a pure pass-through, no logic of its own.
  //
  // `hideAutoStepToggle` is forwarded the same way (MOR-1486 ruling B,
  // owner session 19): it hides the toolbar's AUTO (mode-follow) toggle on
  // layouts that have no `applyModeDefault()` driver for the shared
  // tuning-step store. `MobileRadioLayout` passes `true`; `RadioLayout`
  // omits it (defaults `false`, toggle shown) because it owns the driver.
  //
  // MOR-2545 PR2: `scopeControls` may take a second optional argument — the
  // toolbar's screen-only More group — and `scopeStatusIndicator` is the
  // compact scope-display indicator snippet the toolbar mounts at the row's
  // right end (the standalone status line row is gone). Both are pure
  // pass-throughs to `SpectrumToolbar`, no logic of their own.
  //
  // MOR-2851: `hideToolbar` suppresses the whole unhosted `SpectrumToolbar`
  // row above the panorama. Only the portrait phone passes it (`MobileRadioLayout`)
  // — its controls move to the SCOPE chip tab; every other layout keeps the
  // toolbar. Pure pass-through, no logic of its own.
  //
  // MOR-2851: `enableAvg`, `enablePeakHold` and `showBandPlan` were local
  // `$state` and become `$bindable` props with unchanged defaults, so the
  // phone layout can own them from its SCOPE chip tab. Callers that do not
  // bind them behave exactly as before. MOR-4383 adds `enableSmooth` (the
  // waterfall temporal-smoothing toggle, default ON) to the same mechanism.
  //
  // `colorRoles` is resolved over the defaults once, and the one resolved
  // record feeds the canvas renderer options, the CSS custom properties the
  // DOM overlay below reads, and the `data-scope-color-roles` JSON on the
  // panel root — one resolution for all three, pinned by `resolves the roles
  // once per mount for both the root attribute and the renderer options`.
  let { hideSourceControls = false, hideScopeControls = false, hideAutoStepToggle = false, hideToolbar = false,
    enableAvg = $bindable(true), enablePeakHold = $bindable(true), enableSmooth = $bindable(true), showBandPlan = $bindable(true), scopeControls,
    scopeStatusIndicator, scopeRegion, scopeDemanded = true, onScopeDemandChange, colorRoles,
    captureTraceTimeline = false, onTraceComplete }: {
    hideSourceControls?: boolean; hideScopeControls?: boolean; hideAutoStepToggle?: boolean; hideToolbar?: boolean;
    enableAvg?: boolean; enablePeakHold?: boolean; enableSmooth?: boolean; showBandPlan?: boolean;
    scopeControls?: Snippet<[allowBare?: boolean, screenGroup?: Snippet]>;
    scopeStatusIndicator?: Snippet;
    scopeRegion?: ManagedScopeRegion | null;
    scopeDemanded?: boolean; onScopeDemandChange?: (enabled: boolean) => void;
    colorRoles?: Partial<SpectrumColorRoles>;
    /** Diagnostic candidates only; ordinary rendering never invokes these hooks. */
    captureTraceTimeline?: boolean;
    onTraceComplete?: (contents: string) => void;
  } = $props();

  let resolvedColorRoles = $derived(resolveSpectrumColorRoles(colorRoles));

  const vfoHandlers = getVfoHandlers();
  const filterHandlers = getFilterHandlers();
  const scopeHandlers = bindSemanticSurfaceHandlers().scopeControls;
  // This lifecycle is presentation-only: it never replaces radio-confirmed
  // state. It preserves the release target while the existing command
  // lifecycle is busy, then returns to the confirmed radio observation.
  let filterWidthLifecycle = $derived(getFilterWidthCommandLifecycle());
  // --- Component state ---
  let selectedScopeId = $derived(resolveScopeSourceId(runtime.caps));
  let audioFft = $derived(selectedScopeId === 'audio_fft');
  // MOR-3158: the SDR source rides the same hardware rendering path —
  // RF-center geometry, the existing set-frequency click-to-tune, the
  // hardware scope stream and leases — because the backend feeds the
  // IQ-derived ScopeFrames through that channel. The SDR-specific surface
  // is additive: the toolbar badge/selector and the TX-frozen waterfall
  // hold. The public `sdr` leaf is the generated `SdrStatusPublic`
  // (MOR-3201); an absent leaf reads as null, never a fabricated default.
  let sdrSource = $derived(selectedScopeId === 'iq_fft');
  let sdrState = $derived(sdrSource ? runtime.state?.sdr ?? null : null);
  let sdrTxFrozen = $derived(sdrState?.txFrozen === true);
  // Every source the info payload advertises next to the selected SDR
  // source; the selector renders itself only when more than one exists.
  let sdrOtherSources = $derived.by(() => {
    if (!sdrSource) return [] as const;
    const others: ScopeSourceId[] = [];
    if (runtime.caps?.scope === true) others.push('hardware');
    if (runtime.caps?.audioFftAvailable === true) others.push('audio_fft');
    return others;
  });
  let scopeDemandOn = $state(true);
  let managed = $derived(scopeRegion !== undefined);
  // MOR-4270: the stream's geometry store is the one reactive scope surface;
  // pixels reach the renderers through the stream sinks, never `$state`.
  let frameGeometry = $state.raw<ScopeGeometry | null>(null);
  // One availability fact for overlays, gestures and the trace: frames for
  // THIS display are flowing (transport + liveness + qualified geometry).
  let scopeConnected = $derived((audioFft
    ? runtime.defaultScopeStatus.transport === 'connected'
    : runtime.scope.hardwareScopeConnected && runtime.scope.hardwareScopeFrameLive
      && frameGeometry !== null) && (!managed || (scopeDemandOn
      && scopeRegion?.geometryAdmitted !== false)));
  let managedUnavailable = $derived(managed && (!scopeDemandOn || !scopeConnected));
  // R53: when the scope drops the trace goes dark instead of freezing under
  // the disconnect overlay. The unmanaged audio-FFT source keeps its canvas:
  // there `scopeConnected` reads `runtime.defaultScopeStatus`, a different
  // observation than the frames this panel paints.
  let scopeTraceLive = $derived(scopeConnected || (!managed && audioFft));
  // Q3 (MOR-4241): out-of-range keeps its typed state end to end — an OOR
  // frame with retained pixels stays live content (SDR TX freeze).
  let scopeContentState = $derived(frameGeometry?.outOfRange === true && frameGeometry.hasPixels === false
    ? 'out-of-range' as const : undefined);
  // R53: the sample plane (window, mode, coverage) clears when the trace is
  // not live; the geometry store itself keeps the last facts for the store's
  // own consumers.
  let displayGeometry = $derived(scopeTraceLive ? frameGeometry : null);
  // MOR-2859: the managed TX snapshot is the only TX truth (invariant R9,
  // the same source as the global TX lamp) — never `state.ptt`. The lookup
  // is optional: mounts without the provider read as "TX not known".
  let txSnapshot = $state.raw<ManagedTxState | null>(null);
  try {
    const txCtl = getManagedAppTxController();
    txSnapshot = txCtl.snapshot();
    const stopWatchingTx = txCtl.subscribe((next) => { txSnapshot = next; });
    onDestroy(() => stopWatchingTx());
  } catch {
    // No provider — scopeTxKnown stays false.
  }
  // "TX known" is the `transmitting` case of `rfState` (rx-tx-surface.ts):
  // a positively observed ON or a confirmed-on risk. `uncertain` is doubt,
  // not knowledge, and keeps the disconnected overlay.
  let scopeTxKnown = $derived(
    txSnapshot !== null && (txSnapshot.radioTx === 'on' || txSnapshot.txRisk === 'confirmed-on'),
  );
  let scopeLease: ReturnType<typeof runtime.acquireHardwareScope> | null = null;
  let brtLevel = $state(0);
  let colorScheme = $state<ColorSchemeName>('classic');
  let spectrumPush: ((data: Uint8Array, token?: TraceToken | null) => void) | null = null;
  let waterfallPush: ((data: Uint8Array, options?: WaterfallOptions, token?: TraceToken | null, identity?: WaterfallFrameIdentity) => void) | null = null;
  const traceCandidate = import.meta.env.VITE_SCOPE_TRACE_CANDIDATE === '1';
  let trace: ScopeRenderTrace | null = $state(null);
  let traceStatus: 'idle' | TraceStatus = $state('idle');
  let traceError = $state('');
  let traceSource: TraceSource | null = null;
  type TraceSelection = Readonly<{
    selectedSource: string | null;
    providerGeneration: number | null;
    activeReceiver: string | null;
    managed: boolean;
    projectionSource: string | null;
    projectionReceiver: string | null;
  }>;
  let traceSelection: TraceSelection | null = null;
  let unsubscribeTrace: (() => void) | null = null;

  function selectedTraceIdentity(): TraceSelection {
    const caps = runtime.caps;
    const state = runtime.state;
    return {
      selectedSource: selectedScopeId ?? null,
      providerGeneration: typeof state?.providerGeneration === 'number' && Number.isSafeInteger(state.providerGeneration)
        ? state.providerGeneration : null,
      activeReceiver: typeof state?.active === 'string' ? state.active : null,
      managed,
      projectionSource: managed ? displayGeometry?.source === 'audio_fft' ? 'audio-fft'
        : displayGeometry?.source === 'radio' ? 'hardware' : null : null,
      projectionReceiver: managed ? displayGeometry?.receiver === 0 ? 'MAIN'
        : displayGeometry?.receiver === 1 ? 'SUB' : null : null,
    };
  }

  function startTrace(captureTransition = false): void {
    if (!traceCandidate || traceStatus === 'recording' || !scopeDemandOn || !scopeConnected) return;
    unsubscribeTrace?.();
    trace?.dispose();
    traceError = '';
    traceSource = audioFft ? 'audio_fft' : 'hardware';
    traceSelection = selectedTraceIdentity();
    // An already-hidden constructor can stop synchronously. Export that
    // session only after assignment, avoiding an uninitialized closure read.
    let completedDuringConstruction = false;
    let session: ScopeRenderTrace | null = null;
    const completed = () => {
      const contents = session?.export();
      if (contents && onTraceComplete) {
        try { onTraceComplete(contents); } catch { traceError = 'Trace export callback failed'; }
      }
    };
    session = new ScopeRenderTrace((status) => {
      traceStatus = status;
      traceSelection = null;
      unsubscribeTrace?.();
      unsubscribeTrace = null;
      if (session) completed();
      else completedDuringConstruction = true;
    }, undefined, undefined, { captureTimeline: captureTraceTimeline, captureTransition });
    trace = session;
    traceStatus = session.status;
    if (completedDuringConstruction) completed();
    if (session.status === 'recording') {
      const recordingSession = session;
      unsubscribeTrace = runtime.scope.subscribeTraceReceipt((receipt) => {
        const source = receipt.kind === 'accepted' ? receipt.token.source : receipt.source;
        if (source !== traceSource) return;
        try { recordingSession.observe(receipt); } catch {
          traceError = 'Trace collector stopped after an error';
          recordingSession.stop('observer_error');
        }
      });
    }
  }

  function stopTrace(): void { trace?.stop('operator'); }

  function downloadTrace(): void {
    const contents = trace?.export();
    if (!contents) return;
    try {
      const url = URL.createObjectURL(new Blob([contents], { type: 'application/json' }));
      const link = document.createElement('a');
      link.href = url;
      link.download = `scope-render-trace-${trace?.runId}.json`;
      document.body.appendChild(link);
      link.click();
      link.remove();
      setTimeout(() => URL.revokeObjectURL(url), 1_000);
    } catch {
      traceError = 'Local trace download failed';
    }
  }

  onDestroy(() => { unsubscribeTrace?.(); trace?.dispose(); panoramaScheduler.dispose(); });
  // RF geometry stays wire-owned. AF axes use observed carrier/polarity;
  // the legacy symmetric fallback is presentation-only.
  let audioProjection = $derived(audioFft ? audioScopeProjection(displayGeometry, runtime.state) : null);
  let audioSampleWindow = $derived(audioProjection ? { start: audioProjection.zero,
    end: audioProjection.zero + audioProjection.perHz * audioProjection.spanHz } : undefined);
  let startFreq = $derived(audioFft ? 0 : displayGeometry?.view.startHz ?? 0);
  let endFreq = $derived(audioFft
    ? audioProjection?.spanHz ?? ((displayGeometry?.view.endHz ?? 0) - (displayGeometry?.view.startHz ?? 0)) / 2
    : displayGeometry?.view.endHz ?? 0);
  // The runtime qualifies observed control mode into geometry; zero is a presentation fallback.
  let frameScopeMode = $derived(displayGeometry?.mode ?? 0);
  let fullscreen = $state(false);
  let showEiBi = $state(false);
  // Effective band-plan region: one shared state (band-plan-region store, read
  // from /api/v1/band-plan/config), loaded here so it exists even when the
  // toolbar is hidden (portrait layout). The SpectrumToolbar region selector
  // reads and changes the same state; the overlay gets it so a region switch
  // refetches segments even when the viewport did not move. `null` = unknown.
  let bandPlanRegion = $derived(getBandPlanRegion());
  void loadBandPlanConfig();
  let hiddenLayers = $state<string[]>(
    typeof localStorage !== 'undefined'
      ? JSON.parse(localStorage.getItem('rigplane-hidden-layers') || '[]')
      : []
  );
  // Persist hidden layers to localStorage
  $effect(() => {
    if (typeof localStorage !== 'undefined') {
      localStorage.setItem('rigplane-hidden-layers', JSON.stringify(hiddenLayers));
    }
  });
  let dxSpots = $state<DxSpot[]>([]);
  let spectrumArea = $state<HTMLDivElement | null>(null);
  let waterfallContent = $state<HTMLDivElement | null>(null);
  let splitRegion = $state<HTMLDivElement | null>(null);
  let splitRegionHeight = $state(0);
  const SPLIT_STORAGE_KEY = 'rigplane-spectrum-split-ratio';
  const DEFAULT_SPLIT_RATIO = 0.3;
  const MIN_SPLIT_RATIO = 0.2;
  const MAX_SPLIT_RATIO = 0.8;
  const SPLIT_KEY_STEP = 0.05;
  const SPLIT_AXIS_HEIGHT = 20;
  const SPLIT_SEPARATOR_HEIGHT = 8;
  const MIN_SPECTRUM_HEIGHT = 64;
  const MIN_WATERFALL_HEIGHT = 80;

  function normalizeSplitRatio(value: number): number {
    return Math.max(MIN_SPLIT_RATIO, Math.min(MAX_SPLIT_RATIO, value));
  }

  function readSplitRatio(): number {
    if (typeof localStorage === 'undefined') return DEFAULT_SPLIT_RATIO;
    try {
      const stored = localStorage.getItem(SPLIT_STORAGE_KEY);
      if (stored === null || stored.trim() === '') return DEFAULT_SPLIT_RATIO;
      const value = Number(stored);
      return Number.isFinite(value) ? normalizeSplitRatio(value) : DEFAULT_SPLIT_RATIO;
    } catch {
      return DEFAULT_SPLIT_RATIO;
    }
  }

  let splitRatio = $state(readSplitRatio());
  type SplitBounds = Readonly<{ min: number; max: number }>;

  function resolveSplitBounds(regionHeight: number): SplitBounds {
    const paneHeight = regionHeight - SPLIT_AXIS_HEIGHT - SPLIT_SEPARATOR_HEIGHT;
    if (!Number.isFinite(paneHeight) || paneHeight <= 0) {
      return Object.freeze({ min: MIN_SPLIT_RATIO, max: MAX_SPLIT_RATIO });
    }
    const floorTotal = MIN_SPECTRUM_HEIGHT + MIN_WATERFALL_HEIGHT;
    if (paneHeight < floorTotal) {
      const compressed = MIN_SPECTRUM_HEIGHT / floorTotal;
      return Object.freeze({ min: compressed, max: compressed });
    }
    return Object.freeze({
      min: Math.max(MIN_SPLIT_RATIO, MIN_SPECTRUM_HEIGHT / paneHeight),
      max: Math.min(MAX_SPLIT_RATIO, 1 - MIN_WATERFALL_HEIGHT / paneHeight),
    });
  }

  function clampSplitRatio(value: number, bounds: SplitBounds): number {
    return Math.max(bounds.min, Math.min(bounds.max, value));
  }

  let splitBounds = $derived(resolveSplitBounds(splitRegionHeight));
  let renderedSplitRatio = $derived(clampSplitRatio(splitRatio, splitBounds));
  let splitRows = $derived(
    `${renderedSplitRatio}fr ${SPLIT_AXIS_HEIGHT}px ${SPLIT_SEPARATOR_HEIGHT}px ${1 - renderedSplitRatio}fr`,
  );
  type SplitCapture = Readonly<{ pointerId: number; target: HTMLElement }>;
  let splitCapture = $state<SplitCapture | null>(null);

  function setSplitRatio(value: number): void {
    splitRatio = normalizeSplitRatio(value);
    if (typeof localStorage === 'undefined') return;
    try {
      localStorage.setItem(SPLIT_STORAGE_KEY, String(splitRatio));
    } catch {
      // Storage availability must not disable the separator.
    }
  }

  function updateSplitFromPointer(event: PointerEvent): void {
    if (!splitRegion) return;
    const regionRect = splitRegion.getBoundingClientRect();
    splitRegionHeight = regionRect.height;
    const availableHeight = regionRect.height - SPLIT_AXIS_HEIGHT - SPLIT_SEPARATOR_HEIGHT;
    if (!Number.isFinite(availableHeight) || availableHeight <= 0) return;
    const spectrumHeight = event.clientY - regionRect.top
      - SPLIT_AXIS_HEIGHT - SPLIT_SEPARATOR_HEIGHT / 2;
    setSplitRatio(clampSplitRatio(
      spectrumHeight / availableHeight,
      resolveSplitBounds(regionRect.height),
    ));
  }

  function handleSplitStart(event: PointerEvent): void {
    if (event.button !== 0) return;
    event.preventDefault();
    event.stopPropagation();
    const target = event.currentTarget as HTMLElement;
    splitCapture = Object.freeze({ pointerId: event.pointerId, target });
    target.setPointerCapture(event.pointerId);
    updateSplitFromPointer(event);
  }

  function handleSplitMove(event: PointerEvent): void {
    if (splitCapture?.pointerId !== event.pointerId) return;
    event.preventDefault();
    event.stopPropagation();
    updateSplitFromPointer(event);
  }

  function releaseSplitCapture(event: PointerEvent, update: boolean): void {
    const capture = splitCapture;
    if (!capture || capture.pointerId !== event.pointerId) return;
    event.preventDefault();
    event.stopPropagation();
    if (update) updateSplitFromPointer(event);
    splitCapture = null;
    try {
      capture.target.releasePointerCapture(event.pointerId);
    } catch {
      // A browser may already have released capture during cancellation.
    }
  }

  function handleSplitLostCapture(event: PointerEvent): void {
    if (splitCapture?.pointerId !== event.pointerId) return;
    event.stopPropagation();
    splitCapture = null;
  }

  function handleSplitKeydown(event: KeyboardEvent): void {
    if (hasCommandModifier(event)) return;
    if (splitRegion) splitRegionHeight = splitRegion.getBoundingClientRect().height;
    const bounds = resolveSplitBounds(splitRegionHeight);
    const current = clampSplitRatio(splitRatio, bounds);
    let next: number;
    if (event.key === 'ArrowUp') next = current - SPLIT_KEY_STEP;
    else if (event.key === 'ArrowDown') next = current + SPLIT_KEY_STEP;
    else if (event.key === 'Home') next = bounds.min;
    else if (event.key === 'End') next = bounds.max;
    else return;
    event.preventDefault();
    event.stopPropagation();
    setSplitRatio(clampSplitRatio(next, bounds));
  }

  function isolateSplitWheel(event: WheelEvent): void {
    event.preventDefault();
    event.stopPropagation();
  }
  type SampleGeometry = Readonly<{
    frameMode: number;
    startFreq: number;
    endFreq: number;
    elementWidth: number;
  }>;
  type GestureCapture = Readonly<{
    pointerId: number;
    startX: number;
    elementLeft: number;
    authority: SpectrumAuthority;
    geometry: SampleGeometry;
    view: View;
    carrierPx?: number;
    requestedView?: View | null;
  }>;

  let resizeCapture = $state<GestureCapture | null>(null);
  let resizeCandidate = $state<number | null>(null);
  let dragCapture = $state<GestureCapture | null>(null);
  let dragSurface: HTMLElement | null = null;
  let dragCandidate: number | null = null;
  let gestureCandidateHz = $state<number | null>(null);
  let dragging = $state(false);

  let centerHz = $derived(
    startFreq >= 0 && endFreq > startFreq ? (startFreq + endFreq) / 2 : 0,
  );
  let spanHz = $derived(endFreq > startFreq ? endFreq - startFreq : 0);
  let spectrumAuthority = $derived(audioFft ? null : toSpectrumAuthority(runtime.state, runtime.caps));
  let scopeControlsMode = $derived.by(() => {
    if (audioFft || sdrSource) return null;
    const reading = spectrumAuthority?.scopeControls?.mode.reading;
    return reading?.status === 'known' && typeof reading.value === 'number' ? reading.value : null;
  });
  // MOR-1497: the grab cursor must not promise a drag the gate will refuse.
  // Mirrors the FULL drag-start gate: frequency authority AND usable sample
  // geometry (before the first scope frame endFreq <= startFreq, so
  // handleDragStart bails — the cursor must not claim otherwise).
  let canPan = $derived(
    spectrumAuthority !== null && spectrumAuthority.frequencyHz !== null && spanHz > 0,
  );
  let scopeMode = $derived(frameScopeMode);
  let isFixedScope = $derived(isFixedScopeFn(scopeMode));
  let displayTuple = $derived(!managedUnavailable && scopeRegion
    && scopeContentState !== 'out-of-range'
    && (scopeRegion.passband.state === 'current' || scopeRegion.passband.state === 'stale')
    ? scopeRegion.passband.tuple : null);
  let displayStale = $derived(displayTuple !== null && scopeRegion?.passband.state === 'stale');
  // MOR-2464: in CENTER the anchor stays at 50%, the panorama glides under it.
  let explicitView = $state(false);
  let requestedView = $state.raw<View | null>(null);
  let proportionalIndicator = $derived(isFixedScope || requestedView !== null);
  let displayFrequencyHz = $derived(managed ? displayTuple?.frequencyHz : spectrumAuthority?.frequencyHz);
  // MOR-4256/MOR-4270: the preview survives only while the captured gesture
  // context still matches — a receiver/provider switch, a retune from
  // another client or a new frame window retires it immediately instead of
  // holding a stale target until release (the release-side
  // `captureFrequencyStillCurrent` already rejects the commit then).
  let activeDragPreviewHz = $derived.by(() => {
    const capture = dragCapture;
    if (gestureCandidateHz === null || capture === null) return null;
    const authority = spectrumAuthority;
    const ownReadback = authority !== null && authority.frequencyHz !== null
      && isTuningBurstObservation(authority.receiver, authority.frequencyHz);
    if (authority?.providerGeneration !== capture.authority.providerGeneration
      || authority.receiver !== capture.authority.receiver
      || (authority.frequencyHz !== capture.authority.frequencyHz
        && (authority.frequencyHz === null || !isTuningBurstObservation(authority.receiver, authority.frequencyHz)))
      || frameScopeMode !== capture.geometry.frameMode
      || (ownReadback
        ? endFreq - startFreq !== capture.geometry.endFreq - capture.geometry.startFreq
        : startFreq !== capture.geometry.startFreq || endFreq !== capture.geometry.endFreq)) return null;
    return gestureCandidateHz;
  });
  let tuneVisible = $derived(
    displayFrequencyHz !== null
      && displayFrequencyHz !== undefined
      && (!proportionalIndicator || (
        frequencyInView(displayFrequencyHz)
        // MOR-4256: a FIX drag pulling the candidate INTO the window shows
        // its marker/passband preview even while the confirmed frequency
        // still sits outside the window.
        || (activeDragPreviewHz !== null
          && frequencyInView(activeDragPreviewHz))
      )),
  );
  // MOR-4256: in FIX scope there is no panorama glide, so the drag preview
  // moves the tune marker and the passband through this shared input
  // instead (in CENTER the renderer anchors the marker at 50% and ignores
  // it — the preview reaches that mode through `panoramaTargetHz`).
  let tuneHz = $derived(tuneVisible ? (activeDragPreviewHz ?? displayFrequencyHz!) : 0);
  let confirmedPassband = $derived(
    tuneVisible
      && spectrumAuthority?.mode !== null
      && spectrumAuthority?.mode !== undefined
      && spectrumAuthority.filterWidthHz !== null
      && spectrumAuthority.ifShiftHz !== null,
  );
  let rxMode = $derived(managed ? displayTuple?.mode ?? '' : confirmedPassband ? spectrumAuthority!.mode! : '');
  let lifecyclePassbandHz = $derived(
    resizeCapture === null
      && filterWidthLifecycle.busy
      && filterWidthLifecycle.presentation?.receiver === spectrumAuthority?.receiver
      && typeof filterWidthLifecycle.target === 'number'
      && Number.isFinite(filterWidthLifecycle.target)
      ? filterWidthLifecycle.target
      : null,
  );
  let activeResizePassbandHz = $derived(
    resizeCapture !== null
      && resizeCandidate !== null
      && spectrumAuthority !== null
      && sameResizeAuthority(resizeCapture.authority, spectrumAuthority)
      ? resizeCandidate
      : null,
  );
  // The active drag gets an immediate local projection. Once released, only
  // the command lifecycle's busy target may hold that projection; confirmed
  // radio state remains the source of truth for every other state.
  let passbandHz = $derived(
    managed
      ? displayTuple ? ((!displayStale && confirmedPassband
        ? activeResizePassbandHz ?? lifecyclePassbandHz : null) ?? displayTuple.widthHz) : 0
      : confirmedPassband ? (activeResizePassbandHz ?? lifecyclePassbandHz ?? spectrumAuthority!.filterWidthHz!) : 0,
  );
  let passbandShiftHz = $derived(managed ? displayTuple?.shiftHz ?? 0 : confirmedPassband ? spectrumAuthority!.ifShiftHz! : 0);
  // MOR-3677: the Hz-less width states say WHY on the scope itself — a text
  // chip, never geometry. Known/unobserved/unsupported draw nothing (a known
  // width draws the real overlay; an unobserved one has nothing honest to
  // say that the disabled control does not). Not gated on `tuneVisible`:
  // the chip explains the passband's absence even when the carrier marker
  // itself is out of the sample plane. This works on the managed and
  // unmanaged paths alike — `spectrumAuthority` is computed for both.
  let passbandMarker = $derived.by(() => {
    const display = spectrumAuthority?.filterWidthDisplay ?? null;
    if (display === null) return null;
    if (display.state === 'radio-default') return 'radio-default';
    if (display.state === 'narrow') return 'narrow';
    if (display.state === 'unknown' && display.reason === 'not-reported') return 'not-reported';
    return null;
  });
  let passbandMarkerText = $derived(
    passbandMarker === 'radio-default' ? t('core.filter.width.value.radioDefault')
      : passbandMarker === 'narrow' ? t('core.filter.width.value.narrow')
      : passbandMarker === 'not-reported' ? t('core.filter.width.reason.notReported')
      : '',
  );
  let passbandMarkerTitle = $derived(
    passbandMarker === 'radio-default' ? t('core.filter.width.reason.radioDefault')
      : passbandMarker === 'narrow' ? t('core.filter.width.reason.narrow')
      : passbandMarker === 'not-reported' ? t('core.filter.width.reason.notReported')
      : undefined,
  );
  let canResizePassband = $derived(
    confirmedPassband && !displayStale
      && spectrumAuthority !== null
      && spectrumAuthority.rule !== null
      && canResizeFromRightEdge(spectrumAuthority.mode!),
  );
  // Clamped: the FIX-mode drag preview (MOR-4256) may point outside the
  // fixed sample window, where the marker pins to the edge exactly like the
  // canvas renderer's own clamp does.
  // MOR-2464 focus-gap defect: in CENTER scope mode the carrier reference
  // is stationary at 50% by construction — showing it needs neither a live
  // frame's span nor the evidence-bound passband tuple, only the radio's
  // own observed frequency. During a projection/tuple recovery gap
  // (browser refocus) the reference stays visible immediately instead of
  // returning >1s later with the frame. FIX keeps its frame-bound
  // proportional line, audio FFT shows none, and viewer OFF hides it.
  // Only observed scope-control facts establish CENTER. The numeric
  // presentation fallback on v2 geometry is not mode evidence.
  let centerReferenceMode = $derived(scopeControlsMode !== null && !isFixedScopeFn(scopeControlsMode));
  // Remember CENTER/FIX per identity; the static ruler carries no RF geometry.
  let provenCenterMode = $state<{ providerGeneration: number; receiver: 0 | 1; sessionEpoch: number } | null>(null);
  let rememberedCenterReference = $derived.by(() => {
    if (audioFft || sdrSource) return false;
    const memory = provenCenterMode;
    if (memory === null) return false;
    const state = runtime.state;
    if (!state || state.providerGeneration !== memory.providerGeneration) return false;
    if (runtime.controlSession.epoch !== memory.sessionEpoch) return false;
    // Receiver identity via the raw field; on a single-receiver radio
    // `active` is structurally unobservable and tautologically MAIN
    // (MOR-1418 doctrine shared with panel-commands).
    const active = state.active;
    const receiver = active === 'SUB' ? 1 : active === 'MAIN' ? 0
      : runtime.caps?.receivers === 1 ? 0 : null;
    return receiver === memory.receiver;
  });
  let tuneLineVisible = $derived(
    (tuneVisible && spanHz > 0)
    || (!audioFft && scopeDemandOn && !proportionalIndicator && (centerReferenceMode || rememberedCenterReference))
  );
  // The spectrum canvas draws its own carrier marker from renderer
  // options whenever the frame+tuple legs hold; this DOM reference stands
  // in only when they cannot (span 0 or tuple unavailable).
  let spectrumCenterReferenceVisible = $derived(tuneLineVisible && !(tuneVisible && spanHz > 0));

  // --- Center panorama motion (MOR-2464): display-only, absolute Hz ---
  let panoramaCenter = new PanoramaViewportCenter(0);  let visualCenterHz = $state(0);
  // MOR-4271: the one redraw scheduler paces the panorama glide too — woken
  // while the center is settling, cancelled when it settles or on retarget.
  // The document gate pauses the glide while hidden: no animation frame is
  // ever requested for a panorama nobody can see.
  const panoramaScheduler = new ScopeRenderScheduler((now) => {
    visualCenterHz = panoramaCenter.sample(now);
    if (panoramaCenter.settling(now)) panoramaScheduler.wake('view');
  }, { active: () => !document.hidden });
  let panoramaKeyApplied = $state<string | null>(null);
  let panoramaTargetApplied: number | null = null;

  let panoramaActive = $derived(!audioFft && !isFixedScope && spanHz > 0);
  // MOR-2464 follow-up: the panorama target takes the freshest display-only
  // leg available. (1) The local tuning burst — the accumulator's per-gesture
  // target, paced-unsent steps included — shields the glide from the
  // intermediate confirmed observations and the pending accessor's post-ack
  // drop that made rapid arrow tuning jerk. It self-retires shortly after
  // input ends. (2) A pending/acknowledged set_freq target. (3) Confirmed
  // `displayFrequencyHz`, which stays the authority. None of the legs emit
  // commands.
  // ── Source-independent View model (MOR-4270, scope-view.ts) ──
  // The effective view is `gesture > tuningBurst > pending > confirmed`;
  // the drag writes `gesture`, the tuning legs write their own. Pan/zoom
  // presets edit the View directly (display-only until R4 grants views).
  let followSpanHz = $state<number | null>(null);
  const scopeView = createScopeView({ startHz: 0, endHz: 0 });
  let coordinatingView = false;
  let effectiveView = $state.raw<View>({ startHz: 0, endHz: 0 });
  $effect(() => {
    const stop = scopeView.subscribe((view) => {
      effectiveView = view;
      if (!coordinatingView) untrack(() => syncPanoramaMotion(panoramaResetKey, panoramaTargetHz));
    });
    return stop;
  });
  $effect(() => {
    const geometry = frameGeometry;
    const frequency = displayFrequencyHz;
    const authority = spectrumAuthority;
    const nativeRadio = !audioFft && !sdrSource;
    const center = scopeControlsMode !== null ? !isFixedScopeFn(scopeControlsMode) : null;
    const sessionEpoch = runtime.controlSession.epoch;
    const fixed = isFixedScope;
    const follow = followSpanHz;
    const requested = requestedView;
    const active = panoramaActive;
    const burst = active && requested === null && authority !== null ? getTuningBurstFrequencyHz(authority.receiver) : null;
    const pending = active && requested === null && authority !== null ? getPendingFrequencyHz(authority.receiver) : null;
    const key = panoramaResetKey;
    untrack(() => {
      coordinatingView = true;
      try {
        if (!nativeRadio) provenCenterMode = null;
        else if (authority !== null && center !== null) {
          provenCenterMode = center ? {
            providerGeneration: authority.providerGeneration,
            receiver: authority.receiver,
            sessionEpoch,
          } : null;
        }
        if (geometry && geometry.view.endHz > geometry.view.startHz) {
          const span = follow ?? geometry.view.endHz - geometry.view.startHz;
          scopeView.set('confirmed', requested ?? ((fixed && follow === null) || frequency === null || frequency === undefined ? geometry.view
            : { startHz: frequency - span / 2, endHz: frequency + span / 2 }));
        }
        const confirmed = scopeView.confirmed;
        const panFrom = (hz: number | null) => hz === null || confirmed.endHz <= confirmed.startHz
          ? null : panView(confirmed, hz - viewCenterHz(confirmed));
        scopeView.set('tuningBurst', panFrom(burst));
        scopeView.set('pending', panFrom(pending));
      } finally {
        coordinatingView = false;
      }
      syncPanoramaMotion(key, panoramaTargetHz);
    });
  });
  // MOR-4256/MOR-4270: the View's effective center leads while the pointer
  // is down — the viewport follows the gesture from the cached last frame,
  let panoramaTargetHz = $derived(panoramaActive ? viewCenterHz(effectiveView) : null);
  // Receiver/provider/span/mode identity resets the motion on change.
  let panoramaResetKey = $derived(panoramaActive
    ? [spectrumAuthority?.providerGeneration ?? 'x', spectrumAuthority?.receiver ?? 'x',
      displayGeometry?.receiver ?? 'legacy', frameScopeMode, spanHz].join('|') : '');
  // Visibility restore: the document gate may have paused the glide while
  // hidden — resample the (now elapsed) motion and re-arm if it is still
  // settling, so the visual center never freezes mid-glide.
  $effect(() => {
    if (spectrumFacts?.visible) {
      untrack(() => syncPanoramaMotion(panoramaResetKey, panoramaTargetHz));
    }
  });
  function syncPanoramaMotion(key: string, target: number | null): void {
    const now = performance.now();
    if (key !== panoramaKeyApplied) {
      panoramaKeyApplied = key;
      panoramaTargetApplied = null;
      panoramaCenter.reset(panoramaActive ? centerHz : 0, now);
    }
    if (target !== null && target !== panoramaTargetApplied) {
      panoramaTargetApplied = target;
      if (Math.abs(target - centerHz) >= spanHz) panoramaCenter.reset(target, now);
      else panoramaCenter.retarget(target, now);
    }
    visualCenterHz = panoramaCenter.sample(now);
    if (panoramaCenter.settling(now)) {
      panoramaScheduler.wake('view');
    } else {
      panoramaScheduler.cancel();
    }
  }


  // Effective viewport: until the motion state synchronizes to the context
  // key (first frame or a context change), the frame's own center is the
  // viewport — a stale visual center never reaches the renderers.
  let panoramaViewportHz = $derived(
    panoramaActive && panoramaKeyApplied === panoramaResetKey ? visualCenterHz
      : !audioFft && spanHz > 0 && effectiveView.endHz > effectiveView.startHz ? viewCenterHz(effectiveView) : centerHz,
  );
  let viewportSpanHz = $derived(spanHz > 0 && !audioFft && effectiveView.endHz > effectiveView.startHz
    ? effectiveView.endHz - effectiveView.startHz : spanHz);
  let viewportShiftHz = $derived(!audioFft ? panoramaViewportHz - centerHz : 0);
  let viewportStartFreq = $derived(!audioFft && spanHz > 0 ? panoramaViewportHz - viewportSpanHz / 2 : startFreq);
  let viewportEndFreq = $derived(!audioFft && spanHz > 0 ? panoramaViewportHz + viewportSpanHz / 2 : endFreq);
  function frequencyInView(hz: number): boolean {
    return hz >= viewportStartFreq && hz <= viewportEndFreq;
  }
  let tuneLinePct = $derived(
    proportionalIndicator && viewportSpanHz > 0 && tuneVisible
      ? Math.min(100, Math.max(0, ((tuneHz - viewportStartFreq) / viewportSpanHz) * 100))
      : 50
  );
  let spectrumGeometryKey = $derived(`${spectrumAuthority?.providerGeneration ?? 'x'}|${displayGeometry?.receiver ?? 'legacy'}|${startFreq}|${endFreq}|${frameGeometry?.width ?? 0}|${audioProjection?.zero ?? 'legacy'}|${audioProjection?.perHz ?? 0}`);
  // MOR-4270: the coverage projection hatches the requested view's
  // uncovered columns with the typed reason — never stretched pixels.
  let coverageProjection = $derived(!audioFft && displayGeometry !== null && scopeDemandOn
    ? projectScopeCoverage(displayGeometry, effectiveView) : null);
  let coverageHatched = $derived(coverageProjection?.hatched.filter(
    (range) => range.end - range.start > 0.001) ?? []);
  function hatchReasonText(reason: ScopeCoverageProjection['hatched'][number]['reason']): string {
    if (reason === 'outOfRange') return t('core.scope.hatch.outOfRange');
    if (reason === 'no pixels') return t('core.scope.hatch.noPixels');
    return t('core.scope.hatch.widerThanCoverage');
  }
  const spectrumSurface = new CanvasSurface({ stageScale: getStageScale() });
  let spectrumFacts = $derived(spectrumSurface.facts);
  $effect(() => {
    const host = spectrumArea;
    if (host) return untrack(() => spectrumSurface.attach(host));
  });
  let scopeSubscriptionValue = $derived.by(() => {
    const stream = runtime.scope.stream(audioFft ? 'audio_fft' : 'hardware');
    const view = !audioFft && requestedView !== null
      ? requestedView
      : { follow: 'vfo' as const, spanHz: followSpanHz ?? (audioFft ? 48000 : 100000) };
    const facts = spectrumFacts;
    return scopeSubscription({
      sourceId: stream.sourceId,
      receiver: audioFft || sdrSource ? null : spectrumAuthority?.receiver ?? 0,
      view, width: facts?.backingW ?? 0, visible: facts?.visible ?? false, maxFps: 20,
    });
  });
  $effect(() => {
    const mode = spectrumAuthority?.scopeControls?.mode.reading;
    if (mode?.status === 'known') runtime.scope.stream(audioFft ? 'audio_fft' : 'hardware').setMode(mode.value);
  });
  $effect(() => {
    if (audioFft || sdrSource || !runtime.caps?.span_choices_hz?.length) return;
    const receiver = spectrumAuthority?.receiver ?? 0;
    const control = {
      get spanHz(): number | null {
        const geometry = frameGeometry;
        return geometry?.source === 'radio' && geometry.receiver === receiver
          ? geometry.view.endHz - geometry.view.startHz : null;
      },
      step(delta: -1 | 1): void {
        const current = requestedView !== null ? requestedView.endHz - requestedView.startHz
          : followSpanHz ?? control.spanHz;
        const choices = runtime.caps?.span_choices_hz ?? [];
        if (current === null) return;
        const next = delta > 0 ? choices.find(span => span > current)
          : [...choices].reverse().find(span => span < current);
        if (next === undefined) return;
        explicitView = false;
        requestedView = null;
        followSpanHz = next;
        scopeView.set('gesture', null);
      },
    };
    runtime.scope.spanControl = control;
    return () => { if (runtime.scope.spanControl === control) runtime.scope.spanControl = null; };
  });
  // Band-plan presets (MOR-4270 item 3) derived from profile data.
  let scopePresetOptions = $derived.by(() => {
    if (audioFft) return [];
    const frequencyHz = spectrumAuthority?.frequencyHz ?? displayFrequencyHz ?? null;
    if (frequencyHz === null) return [];
    return scopePresets(runtime.caps?.freqRanges ?? [], frequencyHz, bandPlanRegion, undefined,
      spanHz > 0 ? spanHz : 100_000);
  });
  function applyScopePreset(preset: ScopePreset): void {
    if (preset.kind === 'follow') {
      explicitView = false;
      requestedView = null;
      followSpanHz = 'spanHz' in preset.view ? preset.view.spanHz : spanHz;
      scopeView.set('gesture', null);
      return;
    }
    if ('startHz' in preset.view && 'endHz' in preset.view) {
      followSpanHz = null;
      explicitView = true;
      requestedView = { startHz: preset.view.startHz, endHz: preset.view.endHz };
      scopeView.set('gesture', null);
    }
  }
  function nudgeScopeZoom(factor: number): void {
    if (effectiveView.endHz <= effectiveView.startHz) return;
    followSpanHz = null;
    requestedView = zoomView(effectiveView, factor);
    scopeView.set('gesture', null);
  }

  // Local brightness only — the radio REF command (0x27/0x19) shifts the
  // scope data that the IC-7610 sends over LAN, so applying refDb here
  // would double-shift. BRT is the frontend-only display adjustment.
  let refLevel = $derived(brtLevel);

  let spectrumOptions = $derived<SpectrumOptions>({
    ...defaultSpectrumOptions,
    ...spectrumColorRolesToOptions(resolvedColorRoles),
    spanHz: viewportSpanHz,
    sampleSpanHz: spanHz,
    coverage: audioFft ? undefined : frameGeometry?.coverage,
    sampleOffset: audioFft && !audioProjection ? Math.floor((frameGeometry?.width ?? 0) / 2) : 0,
    sampleWindow: audioSampleWindow,
    sampleAmplitudeMax: audioFft ? 160 : 80,
    showRfOverlays: !audioFft,
    showTuneIndicator: !audioFft && tuneVisible,
    centerHz: panoramaViewportHz,
    panoramaShiftHz: viewportShiftHz,
    geometryKey: spectrumGeometryKey,
    tuneHz,
    passbandHz: tuneVisible ? passbandHz : 0,
    passbandShiftHz,
    refLevel,
    mode: rxMode,
    scopeMode: proportionalIndicator ? 1 : scopeMode,
  });

  let waterfallOptions = $derived<WaterfallOptions>({
    ...defaultWaterfallOptions,
    temporalSmoothing: enableSmooth,
    spanHz: viewportSpanHz,
    sampleSpanHz: spanHz,
    coverage: audioFft ? undefined : frameGeometry?.coverage,
    sampleOffset: audioFft && !audioProjection ? Math.floor((frameGeometry?.width ?? 0) / 2) : 0,
    sampleWindow: audioSampleWindow,
    sampleAmplitudeMax: audioFft ? 160 : 80,
    centerHz: panoramaViewportHz,
    panoramaShiftHz: viewportShiftHz,
    refLevel,
    colorScheme,
  });

  const DB_TICKS = [
    { position: 0, label: '0' },
    { position: 33, label: '-20' },
    { position: 67, label: '-40' },
    { position: 100, label: '-60' },
  ];

  let freqTicks = $derived(audioFft && spanHz > 0
    ? Array.from({ length: 5 }, (_, index) => ({
        position: index * 25,
        label: `${Number(((startFreq + spanHz * index / 4) / 1000).toFixed(2))} kHz`,
      }))
    : explicitView || isFixedScope
      ? viewportSpanHz > 0 ? Array.from({ length: 5 }, (_, index) => ({
          position: index * 25,
          label: ((viewportStartFreq + viewportSpanHz * index / 4) / 1e6).toFixed(3),
        })) : []
      : deriveFreqTicks(viewportSpanHz));

  // Passband overlay position derived from the same geometry as the spectrum renderer.
  let passbandOverlay = $derived(
    getPassbandGeometry(rxMode, passbandHz, passbandShiftHz, viewportSpanHz, 100,
      proportionalIndicator ? tuneLinePct : undefined),
  );
  let pbWidthPct = $derived(passbandOverlay?.widthPx ?? 0);
  let pbLeftPct = $derived(passbandOverlay?.leftPx ?? 0);
  let pbRightPct = $derived(passbandOverlay?.rightPx ?? 0);

  function readAuthority(): SpectrumAuthority | null {
    return audioFft ? null : toSpectrumAuthority(runtime.state, runtime.caps);
  }

  function completeGestureAuthority(requireRule: boolean): SpectrumAuthority | null {
    const current = readAuthority();
    if (managed && (managedUnavailable || scopeRegion?.passband.state !== 'current')) return null;
    if (!current || current.frequencyHz === null || current.mode === null
      || current.filterWidthHz === null || current.ifShiftHz === null
      || (requireRule && current.rule === null)) return null;
    return current;
  }

  // MOR-1497: plain drag-to-pan only moves frequency — it must not be gated
  // on passband fields (mode/filterWidthHz/ifShiftHz) it never reads. On
  // Icom radios ifShiftHz is structurally unobservable (no IF-shift command,
  // PBT-only), so completeGestureAuthority(false) returned null forever and
  // silently disabled the drag gesture while the grab cursor kept implying
  // it worked. Passband-resize (handlePassbandResizeStart) keeps the full
  // gate — it genuinely needs mode/filterWidthHz/ifShiftHz/rule.
  function completeFrequencyAuthority(): SpectrumAuthority | null {
    const current = readAuthority();
    return current && current.frequencyHz !== null ? current : null;
  }

  function readSampleGeometry(element: HTMLElement): SampleGeometry | null {
    if (managedUnavailable) return null;
    const { width } = element.getBoundingClientRect();
    if (!Number.isFinite(width) || width <= 0 || !Number.isFinite(startFreq)
      || !Number.isFinite(endFreq) || endFreq <= startFreq
      || !Number.isSafeInteger(frameScopeMode)) return null;
    return Object.freeze({
      frameMode: frameScopeMode,
      startFreq,
      endFreq,
      elementWidth: width,
    });
  }

  function captureStillCurrent(capture: GestureCapture, element: HTMLElement): boolean {
    const current = readAuthority();
    const geometry = readSampleGeometry(element);
    return current !== null && sameResizeAuthority(capture.authority, current)
      && geometry?.frameMode === capture.geometry.frameMode
      && geometry.startFreq === capture.geometry.startFreq
      && geometry.endFreq === capture.geometry.endFreq
      && geometry.elementWidth === capture.geometry.elementWidth
      && capture.view.startHz === viewportStartFreq
      && capture.view.endHz === viewportEndFreq
      && capture.carrierPx === (proportionalIndicator
        ? (current.frequencyHz! - viewportStartFreq) / viewportSpanHz * geometry.elementWidth
        : geometry.elementWidth / 2);
  }

  /**
   * Resizing depends on the active receiver/filter, its passband geometry,
   * and the mode/DATA-specific width rule.  The full spectrum digest also
   * carries filter shape, raw PBT leaves, and scope-toolbar state; none of
   * those changes the right-edge-to-width mapping, so polling them must not
   * cancel an otherwise valid gesture.
   */
  function sameResizeAuthority(
    captured: SpectrumAuthority,
    current: SpectrumAuthority,
  ): boolean {
    return current.providerGeneration === captured.providerGeneration
      && current.receiver === captured.receiver
      && current.frequencyHz === captured.frequencyHz
      && current.mode === captured.mode
      && current.filter === captured.filter
      && current.filterWidthHz === captured.filterWidthHz
      && current.ifShiftHz === captured.ifShiftHz
      && current.dataMode === captured.dataMode
      && JSON.stringify(current.rule) === JSON.stringify(captured.rule);
  }

  // MOR-1497: freq-only counterpart of captureStillCurrent for plain
  // drag-to-pan. The full digest includes passband fields (mode/
  // filterWidthHz/ifShiftHz/rule/scopeControls/...) the pan gesture never
  // reads, so re-checking it would spuriously abort a completed pan whenever
  // any of those unrelated fields changed mid-drag. Re-check only the
  // identity/geometry the gesture actually depends on.
  function captureFrequencyStillCurrent(capture: GestureCapture, element: HTMLElement): boolean {
    const current = readAuthority();
    const geometry = readSampleGeometry(element);
    const ownReadback = current !== null && current.frequencyHz !== null
      && isTuningBurstObservation(current.receiver, current.frequencyHz);
    return current?.providerGeneration === capture.authority.providerGeneration
      && current.receiver === capture.authority.receiver
      && (current.frequencyHz === capture.authority.frequencyHz
        || (current.frequencyHz !== null && isTuningBurstObservation(current.receiver, current.frequencyHz)))
      && geometry?.frameMode === capture.geometry.frameMode
      && (ownReadback
        ? geometry.endFreq - geometry.startFreq === capture.geometry.endFreq - capture.geometry.startFreq
        : geometry.startFreq === capture.geometry.startFreq && geometry.endFreq === capture.geometry.endFreq)
      && geometry.elementWidth === capture.geometry.elementWidth
      && capture.requestedView === requestedView;
  }

  function handlePassbandResizeStart(event: PointerEvent): void {
    if (!waterfallContent) return;
    const accepted = completeGestureAuthority(true);
    const geometry = readSampleGeometry(waterfallContent);
    if (!accepted || !accepted.rule || !geometry || !canResizeFromRightEdge(accepted.mode!)) return;
    if (!frequencyInView(accepted.frequencyHz!)) return;
    const rect = waterfallContent.getBoundingClientRect();
    event.preventDefault();
    event.stopPropagation();
    resizeCandidate = null;
    resizeCapture = Object.freeze({
      view: { startHz: viewportStartFreq, endHz: viewportEndFreq },
      carrierPx: proportionalIndicator
        ? (accepted.frequencyHz! - viewportStartFreq) / viewportSpanHz * geometry.elementWidth
        : geometry.elementWidth / 2,
      pointerId: event.pointerId,
      startX: event.clientX,
      elementLeft: rect.left,
      authority: accepted,
      geometry,
    });
    (event.currentTarget as HTMLElement | null)?.setPointerCapture?.(event.pointerId);
  }

  function handleResizeMove(event: PointerEvent): void {
    const capture = resizeCapture;
    if (!capture || capture.pointerId !== event.pointerId) return;
    const { authority, geometry } = capture;
    const span = capture.view.endHz - capture.view.startHz;
    const sampleX = Math.max(0, Math.min(
      geometry.elementWidth,
      event.clientX - capture.elementLeft,
    ));
    const carrierX = capture.carrierPx ?? geometry.elementWidth / 2;
    const normalizedX = sampleX - carrierX + geometry.elementWidth / 2;
    const raw = getFilterWidthFromRightEdgePx(
      authority.mode!,
      authority.ifShiftHz!,
      span,
      geometry.elementWidth,
      normalizedX,
      authority.rule!.maxHz,
    );
    const snapped = raw === null ? null : snapSpectrumFilterWidth(raw, authority.rule);
    resizeCandidate = snapped !== null && snapped !== authority.filterWidthHz ? snapped : null;
  }

  function handleResizeEnd(event: PointerEvent): void {
    const capture = resizeCapture;
    if (!capture || capture.pointerId !== event.pointerId || !waterfallContent) return;
    const candidate = resizeCandidate;
    const stable = completeGestureAuthority(true) !== null && captureStillCurrent(capture, waterfallContent);
    resizeCapture = null;
    resizeCandidate = null;
    if (!stable || candidate === null || candidate === capture.authority.filterWidthHz) return;
    filterHandlers.onFilterWidthCommit(
      candidate,
      capture.authority.receiver,
      capture.authority.providerGeneration,
    );
  }

  function handleResizeCancel(event: PointerEvent): void {
    if (!resizeCapture || resizeCapture.pointerId !== event.pointerId) return;
    resizeCapture = null;
    resizeCandidate = null;
  }

  function acquireScopeDemand(): void {
    if (managed || scopeLease) return;
    scopeLease = audioFft
      ? presentationResources.acquire('audio-fft', 'SpectrumPanel')
      : runtime.acquireHardwareScope('SpectrumPanel');
  }

  function releaseScopeDemand(): void {
    const lease = scopeLease;
    scopeLease = null;
    if (lease?.resource === 'audio-fft') presentationResources.release(lease);
    else if (lease) runtime.releaseHardwareScope(lease);
  }

  function setScopeDemand(enabled: boolean): void {
    if (scopeDemandOn === enabled) return;
    scopeDemandOn = enabled;
    if (managed) {
      if (!enabled) clearSample();
      onScopeDemandChange?.(enabled);
      return;
    }
    if (enabled) acquireScopeDemand();
    else releaseScopeDemand();
  }

  // --- Click-to-tune ---
  function snapFrequency(raw: number): number | null {
    const step = getTuningStep();
    if (!Number.isFinite(raw) || !Number.isSafeInteger(step) || step <= 0) return null;
    const candidate = Math.round(raw / step) * step;
    return Number.isSafeInteger(candidate) && candidate > 0 ? candidate : null;
  }

  function handleTune(hz: number): void {
    if (managedUnavailable) return;
    const current = readAuthority();
    const frequency = snapFrequency(Math.round(hz));
    if (current?.frequencyHz === null || current?.frequencyHz === undefined || frequency === null) return;
    vfoHandlers.onFreqChange(frequency, current.receiver);
  }

  // --- Scroll-to-tune (mouse wheel on spectrum/waterfall) ---
  function handleWheel(event: WheelEvent): void {
    event.preventDefault();
    if (managedUnavailable) return;
    const current = readAuthority();
    const step = getTuningStep();
    if (current?.frequencyHz === null || current?.frequencyHz === undefined
      || !Number.isSafeInteger(step) || step <= 0) return;
    const frequency = snapFrequency(current.frequencyHz + (event.deltaY > 0 ? -step : step));
    // MOR-1425 review B1: a fixed one-step relative gesture (like digit-tuning
    // wheel ticks), not an absolute target — keep the accumulate path.
    if (frequency !== null) vfoHandlers.onFreqChange(frequency, current.receiver, 'step');
  }

  // --- Drag-to-pan (grab and slide the spectrum window) ---
  function handleDragStart(event: PointerEvent): void {
    if (event.button !== 0 || resizeCapture) return;
    // Interactive controls and the band-plan popup dialog stay inert: a
    // press inside them must neither pan nor resolve as a click-to-tune.
    if (event.target instanceof Element
      && event.target.closest('button, select, input, [role="dialog"]')) return;
    const surface = event.currentTarget as HTMLElement | null;
    const accepted = completeFrequencyAuthority();
    const geometry = surface ? readSampleGeometry(surface) : null;
    if (!surface || !accepted || !geometry) return;
    const rect = surface.getBoundingClientRect();
    dragSurface = surface;
    dragCandidate = null;
    dragging = false;
    dragCapture = Object.freeze({
      view: { startHz: viewportStartFreq, endHz: viewportEndFreq },
      requestedView,
      pointerId: event.pointerId,
      startX: event.clientX,
      elementLeft: rect.left,
      authority: accepted,
      geometry,
    });
  }

  const DRAG_THRESHOLD_PX = 5;

  function clearDragPreview(): void {
    gestureCandidateHz = null;
    scopeView.set('gesture', null);
  }

  // MOR-4256 retirement rules on the View leg: a gesture whose captured
  // identity/geometry no longer matches publishes nothing (the same check
  // the release commit applies).
  $effect(() => {
    const capture = dragCapture;
    if (capture === null || gestureCandidateHz === null) return;
    const authority = spectrumAuthority;
    const geometry = frameGeometry;
    if (authority?.providerGeneration !== capture.authority.providerGeneration
      || authority.receiver !== capture.authority.receiver
      || authority.frequencyHz !== capture.authority.frequencyHz
      || geometry?.mode !== capture.geometry.frameMode
      || geometry?.view.startHz !== capture.geometry.startFreq
      || geometry?.view.endHz !== capture.geometry.endFreq) clearDragPreview();
  });

  function handleDragMove(event: PointerEvent): void {
    const capture = dragCapture;
    if (!capture || capture.pointerId !== event.pointerId) return;
    // MOR-4256: a gesture whose captured identity/geometry (element width
    // included) no longer matches publishes nothing — the same check the
    // release commit applies.
    if (dragSurface !== null && !captureFrequencyStillCurrent(capture, dragSurface)) {
      clearDragPreview();
      return;
    }
    const dx = event.clientX - capture.startX;
    if (!dragging) {
      if (Math.abs(dx) < DRAG_THRESHOLD_PX) return;
      dragging = true;
      dragSurface?.setPointerCapture?.(event.pointerId);
    }
    const hzPerPx = (capture.view ? capture.view.endHz - capture.view.startHz
      : capture.geometry.endFreq - capture.geometry.startFreq)
      / capture.geometry.elementWidth;
    const deltaHz = -dx * hzPerPx;
    const candidate = snapFrequency(Math.round(capture.authority.frequencyHz! + deltaHz));
    dragCandidate = candidate !== capture.authority.frequencyHz ? candidate : null;
    // One View update per move: the gesture leg pans the confirmed window
    // to the SNAPPED candidate (MOR-4256) and the candidate Hz leads the
    // FIX marker.
    const changed = dragCandidate !== null || gestureCandidateHz !== null;
    gestureCandidateHz = dragCandidate;
    if (changed && candidate !== null) vfoHandlers.onFreqChange(candidate, capture.authority.receiver, 'drag');
    if (panoramaActive) {
      const base = capture.view ?? untrack(() => scopeView.confirmed);
      scopeView.set('gesture', dragCandidate !== null && base.endHz > base.startHz
        ? panView(base, dragCandidate - capture.authority.frequencyHz!) : base);
    }
  }

  function handleDragEnd(event: PointerEvent): void {
    const capture = dragCapture;
    if (!capture || capture.pointerId !== event.pointerId || !dragSurface) return;
    const candidate = dragCandidate;
    const stable = captureFrequencyStillCurrent(capture, dragSurface);
    // MOR-2464 click-to-tune: a release that never crossed the drag
    // threshold on the spectrum area is a click. The upper canvas has no
    // tap recognizer of its own (unlike WaterfallCanvas, whose gesture
    // already tunes exactly once — hence the surface check), so the click
    // resolves here through the same release-driven path: map the release
    // point through the CURRENT visual viewport (the animated panorama
    // window; the fixed sample window in FIX) into the actual radio
    // command via handleTune. An above-threshold release is a pan and
    // must never also tune, and the browser's follow-up native click
    // event has no listener, so one physical click emits one command.
    const clickTune = !dragging && candidate === null && stable && dragSurface === spectrumArea;
    const clickView = capture.view;
    dragging = false;
    dragCapture = null;
    dragSurface = null;
    dragCandidate = null;
    // The pending set_freq leg (or the confirmed frequency) takes over the
    // viewport from here; the preview belongs to the gesture only.
    clearDragPreview();
    if (clickTune) {
      const fraction = Math.min(1, Math.max(0,
        (event.clientX - capture.elementLeft) / capture.geometry.elementWidth));
      handleTune(clickView ? clickView.startHz + fraction * (clickView.endHz - clickView.startHz)
        : viewportStartFreq + fraction * viewportSpanHz);
      return;
    }
    if (!stable || candidate === null || candidate === capture.authority.frequencyHz) return;
    vfoHandlers.onFreqChange(candidate, capture.authority.receiver);
  }

  function handleDragCancel(event: PointerEvent): void {
    if (!dragCapture || dragCapture.pointerId !== event.pointerId) return;
    dragging = false;
    dragCapture = null;
    dragSurface = null;
    dragCandidate = null;
    clearDragPreview();
  }

  // The waterfall canvas's own tap recognizer captures the pointer on
  // pointerdown; when the drag crosses its threshold the panel re-captures
  // the pointer on the drag surface and the canvas's `lostpointercapture`
  // BUBBLES through this handler. That transfer is not a loss — cancel
  // only when the capture the drag itself took on its surface is revoked.
  function handleDragLostCapture(event: PointerEvent): void {
    if (event.target !== dragSurface) return;
    handleDragCancel(event);
  }

  // R53: the panel keeps no pixels across a drop — replay may only paint a
  // frame the sink actually delivered to THIS display since the last drop.
  let samplePlaneLive = false;
  let scopeError = $state<string | null>(null);
  let cachedWhileOff = false;
  let acceptScopeFrame: ((frame: ScopeFrame, slotKey: string) => void) | null = null;

  function clearSample(): void {
    const hadSample = samplePlaneLive;
    samplePlaneLive = false;
    resizeCapture = null;
    resizeCandidate = null;
    dragCapture = null;
    dragSurface = null;
    dragCandidate = null;
    clearDragPreview();
    dragging = false;
    dxSpots = [];
    // A cleared LIVE sample plane keeps no spectrum registration (that
    // canvas unmounts with the trace); the waterfall registration survives
    // — its canvas only hides, and real frame silence stops the deliveries.
    if (hadSample) {
      spectrumPush = null;
    }
  }

  $effect(() => {
    if (scopeTraceLive) return;
    untrack(() => clearSample());
  });

  $effect(() => { if (managed) scopeDemandOn = scopeDemanded; });
  $effect(() => { if (!scopeDemandOn && traceStatus === 'recording') trace?.stop('source_changed'); });
  $effect(() => {
    if (traceStatus !== 'recording') return;
    const selected = selectedTraceIdentity();
    const initial = traceSelection;
    if (!initial) return;
    if (selected.selectedSource !== initial.selectedSource
      || selected.providerGeneration !== initial.providerGeneration
      || selected.activeReceiver !== initial.activeReceiver
      || selected.managed !== initial.managed
      || (selected.projectionSource !== null && selected.projectionSource !== initial.projectionSource)
      || (selected.projectionReceiver !== null && selected.projectionReceiver !== initial.projectionReceiver)) {
      trace?.stop('source_changed');
    }
  });
  // MOR-4270: ONE frame path for every mount — managed and raw alike.
  // Pixels flow from the shared stream sink straight into the renderer
  // pushes; the geometry store drives overlays. The managed-vs-raw dual
  // delivery is gone: a managed mount simply does not own the demand lease.
  $effect(() => {
    const sourceIsAudio = audioFft;
    const sourceIsManaged = managed;
    return untrack(() => {
      fullscreen = false;
      clearSample();
      if (sourceIsAudio) runtime.scope.registerPresentationDriver(presentationResources);
      const scopeStream = runtime.scope.stream(sourceIsAudio ? 'audio_fft' : 'hardware');
      const stopError = scopeStream.error.subscribe(error => { scopeError = error; });
      const stopGeometry = scopeStream.geometry.subscribe((geometry) => { frameGeometry = geometry; });
      cachedWhileOff = false;
      const acceptFrame = (frame: ScopeFrame, slotKey: string) => {
        const geometry = frameGeometry;
        if (geometry === null) return;
        // Follow the qualified display slot (the geometry's own slot).
        if (slotKey !== scopeSlotKey(geometry.sourceId, geometry.receiver)) return;
        if (geometry.outOfRange && !geometry.hasPixels) return;
        if (!scopeDemandOn) { cachedWhileOff = true; return; }
        const traceStart = trace?.status === 'recording' ? performance.now() : 0;
        if (traceStart && (sourceIsAudio ? 'audio_fft' : 'hardware') !== traceSource) trace?.stop('source_changed');
        const token = trace?.tokenForFrame(frame) ?? null;
        const pixels = frame.pixels;
        if (pixels === null) { clearSample(); return; }
        samplePlaneLive = true;
        spectrumPush?.(pixels, token);
        // MOR-3158: a TX-frozen SDR source adds no waterfall row — the
        // panorama holds its last pre-transmit image under the TX marker;
        // only the paint is withheld.
        // MOR-4383: the delivery carries the frame identity so the renderer
        // paints one row group per NEW frame (registration replays dedupe).
        // Field names come straight from the ScopeFrame contract.
        const identity: WaterfallFrameIdentity = { seq: frame.seq, epoch: frame.epoch };
        if (!sdrTxFrozen) waterfallPush?.(pixels, waterfallOptions, token, identity);
        else trace?.record('waterfall_suppressed', token, 0, true);
        if (traceStart) trace?.record('fanout', token, performance.now() - traceStart);
      };
      acceptScopeFrame = acceptFrame;
      const stopFrames = scopeStream.subscribe(acceptFrame);
      if (!sourceIsManaged && scopeDemandOn) acquireScopeDemand();
      const unsubDx = sourceIsAudio ? () => {} : runtime.subscribeDx((msg) => {
        if (managedUnavailable) return;
        if (msg.type === 'dx_spot') {
          const spot = (msg as unknown as { spot: DxSpot }).spot;
          if (spot) dxSpots = [...dxSpots.slice(-49), spot];
        } else if (msg.type === 'dx_spots') {
          const list = (msg as unknown as { spots: DxSpot[] }).spots;
          if (Array.isArray(list)) dxSpots = list.slice(-50);
        }
      });
      return () => {
        acceptScopeFrame = null;
        cachedWhileOff = false;
        stopError();
        stopGeometry();
        stopFrames();
        unsubDx();
        releaseScopeDemand();
        // The scheduler belongs to the component, not this effect: cancel
        // the pending frame so a rerun (audioFft/managed transition) starts
        // clean, but keep it usable — later CENTER retargets must still
        // receive animation ticks. dispose() is reserved for destruction.
        panoramaScheduler.cancel();
      };
    });
  });
  $effect(() => {
    if (!scopeDemandOn) return;
    untrack(() => {
      if (!cachedWhileOff) return;
      cachedWhileOff = false;
      const stream = runtime.scope.stream(audioFft ? 'audio_fft' : 'hardware');
      const slot = stream.selectedSlot;
      const frame = slot ? stream.latest(slot) : null;
      if (frame && slot) acceptScopeFrame?.(frame, slot);
    });
  });
</script>

<svelte:window
  onpointermove={(e) => { handleResizeMove(e); handleDragMove(e); }}
  onpointerup={(e) => { handleResizeEnd(e); handleDragEnd(e); }}
  onpointercancel={(e) => { handleResizeCancel(e); handleDragCancel(e); }}
/>

<!--
  MOR-1456: `data-waterfall` + `tabindex="-1"` make this region a real,
  program-focusable landing spot for the "Go to Waterfall" (`g w`) keyboard
  shortcut (`panel-commands.ts`'s `focus_target` dispatch). Neither the
  spectrum `<canvas>` nor the waterfall `<canvas>` (`WaterfallCanvas.svelte`)
  is natively focusable, and `ScopeDisplaySurface.svelte` is a bare readout
  with ZERO focusable elements by construction (MOR-1069) — this panel root
  is the one stable anchor the shortcut can jump to. `tabindex="-1"` keeps it
  OUT of normal Tab order (it is a jump target, not a tab stop) — unlike the
  other `g <key>` targets, which land on a real form control that was already
  in the tab order.
-->
{#key `${audioFft}:${managed}`}
<!-- svelte-ignore a11y_no_noninteractive_tabindex -->
<div
  class="spectrum-panel"
  class:audio-fft={audioFft}
  class:fullscreen
  data-waterfall
  data-scope-color-roles={JSON.stringify(resolvedColorRoles)}
  data-passband-state={managed ? scopeRegion?.passband?.state ?? 'unavailable' : 'legacy'}
  data-scope-subscription={JSON.stringify(scopeSubscriptionValue)}
  data-passband-resizable={canResizePassband}
  data-filter-rule={spectrumAuthority?.rule?.kind ?? 'none'}
  tabindex="-1"
  onwheel={handleWheel}
  style:--scope-tune-line={resolvedColorRoles.tuneLine}
  style:--scope-passband-fill={resolvedColorRoles.passbandFill}
  style:--scope-passband-edge={resolvedColorRoles.passbandEdge}
>
  <!-- MOR-3158: the SDR toolbar status wraps whatever indicator snippet the
       layout supplied (unchanged for every other source) and adds the
       source badge plus, when more than one source is advertised, the
       display-only source selector — mounted only for `scopeSource ===
       'sdr'` so hardware and audio-FFT sessions render unchanged. -->
  {#snippet sdrToolbarStatus()}
    {#if scopeStatusIndicator}{@render scopeStatusIndicator()}{/if}
    <ScopeSourceBadge source="sdr" sdr={sdrState} />
    <ScopeSourceSelector sources={[...sdrOtherSources, 'sdr']} active="sdr" />
  {/snippet}
  {#snippet panelScopeStatus()}
    {#if sdrSource}{@render sdrToolbarStatus()}
    {:else if scopeStatusIndicator}{@render scopeStatusIndicator()}{/if}
    {#if scopeError}
      <span class="scope-refusal" role="status" title={scopeError}>
        <StatusIndicator active color="amber" label={t('core.scope.viewRefused', { reason: scopeError })} />
      </span>
    {/if}
  {/snippet}
  {#if traceCandidate}
    <div class="scope-trace-controls" aria-label="Scope render trace">
      <button type="button" onclick={() => startTrace()} disabled={traceStatus === 'recording' || !scopeDemandOn || !scopeConnected}>Start trace</button>
      <button type="button" onclick={() => startTrace(true)} disabled={traceStatus === 'recording' || !scopeDemandOn || !scopeConnected}>Start transition trace</button>
      {#if traceStatus === 'recording'}<button type="button" onclick={stopTrace}>Stop trace</button>{/if}
      {#if traceStatus === 'stopped' || traceStatus === 'overflow'}<button type="button" onclick={downloadTrace}>Download trace</button>{/if}
      <span role="status">{traceStatus}{trace?.reason ? ` (${trace.reason})` : ''}{traceError ? ` · ${traceError}` : ''}</span>
    </div>
  {/if}
  {#if audioFft}
    <div class="audio-source-label">
      <span>Audio FFT · AF</span>
      <button type="button" aria-label="Scope viewer" aria-pressed={scopeDemandOn} onclick={() => setScopeDemand(!scopeDemandOn)}>
        Viewer {scopeDemandOn ? 'ON' : 'OFF'}
      </button>
    </div>
  <!-- MOR-2851: `hideToolbar` (portrait phone only) suppresses the toolbar
       for non-audio sources — the controls live in the phone's SCOPE chip
       tab instead. An audio-FFT source keeps its original no-toolbar
       branch. -->
  {:else if !hideToolbar}
    <SpectrumToolbar bind:enableAvg bind:enablePeakHold bind:enableSmooth bind:brtLevel bind:colorScheme bind:fullscreen bind:showBandPlan bind:hiddenLayers bind:showEiBi {scopeDemandOn} onScopeDemandChange={setScopeDemand} {hideSourceControls} {hideScopeControls} {hideAutoStepToggle} {scopeControls} scopePresets={scopePresetOptions} onScopePresetSelect={applyScopePreset} onScopeZoom={nudgeScopeZoom} scopeStatusIndicator={sdrSource || scopeStatusIndicator || scopeError ? panelScopeStatus : undefined} />
  {/if}
  {#if (audioFft || hideToolbar) && scopeError}{@render panelScopeStatus()}{/if}
  <div
    class="spectrum-split-region"
    bind:this={splitRegion}
    bind:clientHeight={splitRegionHeight}
    style:grid-template-rows={splitRows}
  >
  <div class="spectrum-with-scales">
    <div class="db-scale">
      {#each audioFft ? [] : DB_TICKS as tick}
        <div class="tick" style="top: {tick.position}%">{tick.label}</div>
      {/each}
    </div>
    <div class="spectrum-area" class:panning={dragging} class:draggable={canPan} bind:this={spectrumArea} use:scopeSubscriptionSurface={{
      stream: runtime.scope.stream(audioFft ? 'audio_fft' : 'hardware'),
      receiver: scopeSubscriptionValue.receiver, view: scopeSubscriptionValue.view, facts: spectrumFacts,
      revision: audioFft ? audioScopeFactsRevision(runtime.state, runtime.caps) : spectrumAuthority?.digest,
      frequencyHz: spectrumAuthority?.frequencyHz,
    }} onpointerdown={handleDragStart} onlostpointercapture={handleDragLostCapture} role="presentation">
      {#if !scopeDemandOn}
        <div class="scope-disconnected-overlay scope-demand-off-overlay">Scope viewer OFF</div>
      {:else if scopeTxKnown && !scopeConnected}
        <div class="scope-tx-overlay" data-testid="scope-tx-overlay">{t('core.overlay.scopeTransmitting')}</div>
      {:else if !scopeConnected}
        <div class="scope-disconnected-overlay">{t('core.overlay.scopeDisconnected')}</div>
      {:else if scopeContentState === 'out-of-range'}
        <div class="scope-disconnected-overlay scope-out-of-range" data-testid="scope-out-of-range">
          <span>{t('core.overlay.scopeOutOfRange', {
            start: (startFreq / 1e6).toFixed(3), end: (endFreq / 1e6).toFixed(3),
          })}</span>
          <button type="button" onpointerdown={(event) => event.stopPropagation()}
            onclick={() => scopeHandlers.onModeChange(0)}>{t('core.overlay.scopeSwitchToCtr')}</button>
        </div>
      {/if}
      {#if !audioFft}<BandPlanOverlay startFreq={viewportStartFreq} endFreq={viewportEndFreq} visible={showBandPlan} {hiddenLayers} region={bandPlanRegion} />{/if}
      {#if scopeTraceLive && scopeContentState !== 'out-of-range' && frameGeometry?.hasPixels !== false}
      <SpectrumCanvas data={null} options={spectrumOptions} spanHz={viewportSpanHz} absoluteLabels={explicitView || isFixedScope} {enableAvg} {enablePeakHold} trace={traceStatus === 'recording' ? trace : null} surface={spectrumSurface} onRegisterPush={(fn) => {
        spectrumPush = fn;
        const geometry = frameGeometry;
        const frame = geometry && scopeDemandOn && samplePlaneLive && geometry.freshness === 'fresh'
          ? runtime.scope.stream(audioFft ? 'audio_fft' : 'hardware')
            .latest(scopeSlotKey(geometry.sourceId, geometry.receiver)) : null;
        if (frame?.pixels) fn(frame.pixels);
      }} />
      {/if}
      {#if scopeTraceLive && scopeContentState !== 'out-of-range'}
      {#each coverageHatched as hatched (hatched.reason + hatched.start)}
        <div
          class="scope-hatch"
          data-reason={hatched.reason}
          title={hatchReasonText(hatched.reason)}
          style="left:{hatched.start * 100}%;width:{(hatched.end - hatched.start) * 100}%"
        ></div>
      {/each}
      {/if}
      {#if spectrumCenterReferenceVisible}
        <div class="tune-line" style="left:{tuneLinePct}%"></div>
      {/if}
      {#if tuneVisible && spanHz > 0 && pbWidthPct > 0 && canResizePassband}
        <button
          type="button"
          class="passband-resize-zone"
          class:active={resizeCapture !== null}
          style="left:{pbRightPct}%"
          onpointerdown={handlePassbandResizeStart}
          aria-label="Resize filter width"
          title="Drag to resize filter width"
        ></button>
      {/if}
    </div>
  </div>
  {#if freqTicks.length > 0}
    <div class="freq-axis">
      {#each freqTicks as tick}
        <div class="tick" style="left: {tick.position}%">{tick.label}</div>
      {/each}
    </div>
  {/if}
  <!-- svelte-ignore a11y_no_noninteractive_element_interactions -->
  <div
    class="spectrum-split-separator"
    class:active={splitCapture !== null}
    role="separator"
    data-owns-arrows="vertical"
    aria-label="Resize spectrum and waterfall"
    aria-orientation="horizontal"
    aria-valuemin={Math.round(splitBounds.min * 100)}
    aria-valuemax={Math.round(splitBounds.max * 100)}
    aria-valuenow={Math.round(renderedSplitRatio * 100)}
    tabindex="0"
    onpointerdown={handleSplitStart}
    onpointermove={handleSplitMove}
    onpointerup={(event) => releaseSplitCapture(event, true)}
    onpointercancel={(event) => releaseSplitCapture(event, false)}
    onlostpointercapture={handleSplitLostCapture}
    onkeydown={handleSplitKeydown}
    onwheel={isolateSplitWheel}
  ></div>
  <div class="waterfall-area">
    <div class="waterfall-scale"></div>
    <div class="waterfall-content" class:panning={dragging} class:draggable={canPan} bind:this={waterfallContent} onpointerdown={handleDragStart} onlostpointercapture={handleDragLostCapture} role="presentation">
      {#key runtime.state?.providerGeneration}
      <div class="waterfall-history" style:visibility={managedUnavailable || scopeContentState === 'out-of-range' ? 'hidden' : 'visible'} aria-hidden={managedUnavailable || scopeContentState === 'out-of-range'}>
        <WaterfallCanvas
          options={waterfallOptions}
          trace={traceStatus === 'recording' ? trace : null}
          onFreqClick={audioFft || managedUnavailable ? undefined : handleTune}
          onRegisterPush={(fn) => {
            // MOR-4206: backlog handling is consolidated into the canvas's
            // latest-wins rAF pump — every delivery reaches the pump
            // unfiltered and replaces the pending frame, so one animation
            // frame paints only the latest delivery. No spacing-based
            // suppression sits upstream of the pump (review round 2: the
            // MOR-3471 freshness gate painted the FIRST frame of a fresh
            // registration or post-gap burst, defeating latest-wins). This
            // wiring is pinned by the 'consolidates backlog handling into
            // the rAF pump' case in SpectrumPanel.component.test.ts; the
            // delayed case of scope-hidden-delivery.characterization.test.ts
            // mirrors this registration. The registration replay below is a
            // canvas repaint of the current sample, not a delivery.
            waterfallPush = fn;
            const geometry = frameGeometry;
            const frame = geometry && scopeDemandOn && samplePlaneLive && geometry.freshness === 'fresh' && !sdrTxFrozen
              ? runtime.scope.stream(audioFft ? 'audio_fft' : 'hardware')
                .latest(scopeSlotKey(geometry.sourceId, geometry.receiver)) : null;
            if (frame?.pixels) fn(frame.pixels, waterfallOptions, null, { seq: frame.seq, epoch: frame.epoch });
          }}
        />
        <!-- MOR-3158: the TX-frozen veil rides inside the history wrapper so
             it dims the painted rows without hiding the tune-line/passband
             overlays stacked above the canvas; it hides together with the
             history while the sample plane is unavailable. -->
        {#if sdrSource}<SdrTxFrozenIndicator active={sdrTxFrozen} />{/if}
      </div>
      {/key}
      {#if !audioFft}<DxOverlay spots={dxSpots} startFreq={viewportStartFreq} endFreq={viewportEndFreq} onTune={handleTune} />{/if}
      <!-- Tuning + passband indicator overlays the waterfall -->
      {#if tuneVisible && spanHz > 0}
        {#if pbWidthPct > 0}
          <div class="passband-overlay" style="left:{pbLeftPct}%;width:{pbWidthPct}%"></div>
          {#if canResizePassband}
            <button
              type="button"
              class="passband-resize-zone"
              class:active={resizeCapture !== null}
              style="left:{pbRightPct}%"
              onpointerdown={handlePassbandResizeStart}
              aria-label="Resize filter width"
              title="Drag to resize filter width"
            ></button>
          {/if}
        {/if}
      {/if}
      {#if tuneLineVisible}
        <div class="tune-line" style="left:{tuneLinePct}%"></div>
      {/if}
      <!-- MOR-3677: the Hz-less width chip — the spectrum's honest "why
           there is no passband marker", next to the overlay block and
           independent of it: no geometry, no shaded region, no full-width
           fill is ever drawn for these states. -->
      {#if passbandMarker !== null}
        <div
          class="passband-unknown"
          data-testid="passband-unknown"
          data-state={passbandMarker}
          title={passbandMarkerTitle}
        >{passbandMarkerText}</div>
      {/if}
    </div>
  </div>
  </div>
</div>

{#if !audioFft}<EiBiBrowser bind:visible={showEiBi} />{/if}
{/key}

<style>
  .scope-refusal { min-width: 0; max-width: 280px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
  .waterfall-history { position: absolute; inset: 0; }
  .scope-hatch {
    position: absolute;
    top: 0;
    bottom: 0;
    pointer-events: none;
  }
  .scope-hatch[data-reason="no pixels"] {
    background: repeating-linear-gradient(45deg, transparent 0 6px, rgba(255, 255, 255, 0.08) 6px 12px);
    border-inline: 1px dashed rgba(255, 255, 255, 0.25);
  }
  .audio-source-label { display: flex; align-items: center; justify-content: space-between; padding: 6px 12px; color: var(--text-muted); font-size: 12px; }
  .audio-fft :global(canvas) { cursor: default; }
  .spectrum-panel {
    position: relative;
    display: flex;
    flex-direction: column;
    width: 100%;
    height: 100%;
    background: var(--panel);
    border: 1px solid var(--panel-border);
    border-radius: var(--radius);
    overflow: hidden;
  }

  .spectrum-panel.fullscreen {
    position: fixed;
    inset: 0;
    top: var(--rp-chrome-top, 0px); /* MOR-3845 */
    height: auto;
    z-index: 100;
    border-radius: 0;
    border: none;
  }

  .spectrum-split-region {
    flex: 1 1 auto;
    min-height: 0;
    display: grid;
    overflow: hidden;
  }

  .spectrum-with-scales {
    grid-row: 1;
    min-height: 0;
    display: flex;
    overflow: hidden;
  }

  .db-scale {
    flex: 0 0 44px;
    position: relative;
    background: var(--panel);
    border-right: 1px solid var(--panel-border);
  }

  .db-scale .tick {
    position: absolute;
    right: 6px;
    transform: translateY(-50%);
    font-size: 10px;
    color: var(--text-muted);
    line-height: 1;
    white-space: nowrap;
  }

  .spectrum-area {
    flex: 1;
    min-width: 0;
    min-height: 0;
    position: relative;
    cursor: default;
  }

  .spectrum-area.draggable {
    cursor: grab;
  }

  .spectrum-area.panning {
    cursor: grabbing;
  }

  .freq-axis {
    grid-row: 2;
    position: relative;
    background: var(--panel);
  }

  .spectrum-split-separator {
    grid-row: 3;
    position: relative;
    z-index: 20;
    width: 100%;
    min-height: 8px;
    padding: 0;
    border: 0;
    background: linear-gradient(
      to bottom,
      transparent 3px,
      var(--panel-border) 3px,
      var(--panel-border) 5px,
      transparent 5px
    );
    cursor: ns-resize;
    touch-action: none;
    user-select: none;
  }

  /* MOR-2562: hover-intent overlay. A thin translucent accent line appears
     only after the pointer rests on the divider (~200 ms) and brightens
     instantly while dragging. The delay lives on the hover state only, so
     entering waits and leaving is immediate. The faint rest stripe above
     stays as the structural boundary. */
  .spectrum-split-separator::after {
    content: '';
    position: absolute;
    left: 0;
    right: 0;
    /* Pinned to the rest stripe (3px-5px) on whole pixels, no transform:
       top: 50% + translateY(-50%) landed the 1px line on a half pixel and
       rendered two blurry rows on standard-density screens. */
    top: 4px;
    height: 1px;
    background: var(--accent, var(--panel-border));
    opacity: 0;
    transition: opacity 0.15s ease;
  }

  .spectrum-split-separator:hover::after {
    opacity: 0.45;
    transition-delay: 200ms;
  }

  .spectrum-split-separator.active::after {
    top: 3px;
    height: 2px;
    opacity: 0.8;
    transition-delay: 0s;
  }

  .freq-axis .tick {
    position: absolute;
    transform: translateX(-50%);
    font-size: 10px;
    color: var(--text-muted);
    line-height: 20px;
    white-space: nowrap;
    padding-left: 44px; /* offset for db-scale width */
  }

  .freq-axis .tick:first-child {
    transform: translateX(0);
  }

  .freq-axis .tick:last-child {
    transform: translateX(-100%);
  }

  .waterfall-area {
    grid-row: 4;
    min-height: 0;
    position: relative;
    display: flex;
    overflow: hidden;
  }

  .waterfall-scale {
    flex: 0 0 44px;
    background: var(--panel);
    border-right: 1px solid var(--panel-border);
  }

  .waterfall-content {
    flex: 1 1 auto;
    min-width: 0;
    position: relative;
    cursor: default;
  }

  .waterfall-content.draggable {
    cursor: grab;
  }

  .waterfall-content.panning {
    cursor: grabbing;
  }

  /* Tuning line + passband overlay on waterfall */
  .tune-line {
    position: absolute;
    top: 0;
    bottom: 0;
    width: 1px;
    background: var(--scope-tune-line, rgba(239, 68, 68, 0.75));
    pointer-events: none;
    z-index: 5;
    transform: translateX(-0.5px);
  }

  .passband-overlay {
    position: absolute;
    top: 0;
    bottom: 0;
    background: var(--scope-passband-fill, rgba(59, 130, 246, 0.15));
    border-left: 1px dashed var(--scope-passband-edge, rgba(59, 130, 246, 0.4));
    border-right: 1px dashed var(--scope-passband-edge, rgba(59, 130, 246, 0.4));
    pointer-events: none;
    z-index: 4;
  }

  /* MOR-3677: the Hz-less width chip — a text label in the scope overlay
     area, structurally reserved and inert; it never draws passband
     geometry. */
  .passband-unknown {
    position: absolute;
    top: 4px;
    right: 8px;
    z-index: 5;
    padding: 1px 6px;
    font-size: 11px;
    color: var(--text-muted);
    background: var(--panel);
    border: 1px solid var(--panel-border);
    border-radius: var(--radius);
    pointer-events: none;
  }

  .passband-resize-zone {
    position: absolute;
    top: 0;
    bottom: 0;
    width: 14px;
    transform: translateX(-50%);
    cursor: ew-resize;
    z-index: 6;
    pointer-events: auto;
    padding: 0;
    margin: 0;
    border: 0;
    background: transparent;
  }

  /* MOR-2562: nothing drawn at rest — the passband overlay's dashed edges
     already mark the filter edge. The 14 px hit zone and the ew-resize
     cursor stay; the line lights only after hover intent (~200 ms) and
     while dragging. No outline, glow or brightness filter. */
  .passband-resize-zone::before {
    content: '';
    position: absolute;
    top: 0;
    bottom: 0;
    left: 50%;
    width: 1px;
    transform: translateX(-50%);
    background: var(--scope-passband-edge, rgba(59, 130, 246, 0.75));
    opacity: 0;
    transition: opacity 0.15s ease;
  }

  .passband-resize-zone:hover::before {
    opacity: 0.45;
    transition-delay: 200ms;
  }

  .passband-resize-zone.active::before {
    width: 2px;
    opacity: 0.8;
    transition-delay: 0s;
  }

  .scope-disconnected-overlay, .scope-tx-overlay {
    position: absolute;
    inset: 0;
    z-index: 10;
    display: flex;
    align-items: center;
    justify-content: center;
    background: rgba(10, 10, 15, 0.72);
    color: var(--text-muted, #888);
    font-size: 12px;
    font-family: 'Roboto Mono', monospace;
    letter-spacing: 0.05em;
    pointer-events: none;
  }

  .scope-out-of-range {
    flex-direction: column;
    gap: 8px;
  }
  .scope-out-of-range button {
    pointer-events: auto;
  }

  /* Mobile: hide dB scale and waterfall scale to maximize spectrum width */
  @media (max-width: 640px) {
    .db-scale {
      flex: 0 0 0px;
      width: 0;
      overflow: hidden;
      border: none;
    }

    .waterfall-scale {
      flex: 0 0 0px;
      width: 0;
      overflow: hidden;
      border: none;
    }

    .freq-axis .tick {
      padding-left: 0;
    }
  }
</style>
