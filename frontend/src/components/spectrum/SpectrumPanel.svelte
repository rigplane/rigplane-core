<script lang="ts">
  import { untrack, type Snippet } from 'svelte';
  import SpectrumCanvas from './SpectrumCanvas.svelte';
  import WaterfallCanvas from './WaterfallCanvas.svelte';
  import DxOverlay from './DxOverlay.svelte';
  import {
    defaultSpectrumOptions,
    type SpectrumOptions,
  } from '../../lib/renderers/spectrum-renderer';
  import {
    defaultWaterfallOptions,
    type WaterfallOptions,
    type ColorSchemeName,
  } from '../../lib/renderers/waterfall-renderer';
  import { presentationResources, runtime } from '../../lib/runtime/frontend-runtime';
  import { type DxSpot } from '../../lib/types/protocol';
  import { getTuningStep } from '../../lib/stores/tuning.svelte';
  import {
    getFilterHandlers,
    getFilterWidthCommandLifecycle,
    getVfoHandlers,
  } from '../../lib/runtime/adapters/panel-adapters';
  import {
    snapSpectrumFilterWidth,
    toSpectrumAuthority,
    type SpectrumAuthority,
  } from '../../lib/runtime/adapters/scope-adapter';
  import SpectrumToolbar from './SpectrumToolbar.svelte';
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
  let { hideSourceControls = false, hideScopeControls = false, hideAutoStepToggle = false, scopeControls }: {
    hideSourceControls?: boolean; hideScopeControls?: boolean; hideAutoStepToggle?: boolean; scopeControls?: Snippet;
  } = $props();

  const vfoHandlers = getVfoHandlers();
  const filterHandlers = getFilterHandlers();
  // This lifecycle is presentation-only: it never replaces radio-confirmed
  // state. It preserves the release target while the existing command
  // lifecycle is busy, then returns to the confirmed radio observation.
  let filterWidthLifecycle = $derived(getFilterWidthCommandLifecycle());
  // --- Component state ---
  let audioFft = $derived(runtime.caps?.scopeSource === 'audio_fft');
  let scopeConnected = $derived(audioFft
    ? runtime.defaultScopeStatus.transport === 'connected'
    : runtime.scope.hardwareScopeConnected);
  let scopeDemandOn = $state(true);
  let scopeLease: ReturnType<typeof runtime.acquireHardwareScope> | null = null;
  let scopePixels = $state<Uint8Array | null>(null);
  let enableAvg = $state(true);
  let enablePeakHold = $state(true);
  let brtLevel = $state(0);
  let colorScheme = $state<ColorSchemeName>('classic');
  let spectrumPush: ((data: Uint8Array) => void) | null = null;
  let waterfallPush: ((data: Uint8Array) => void) | null = null;
  let startFreq = $state(0);
  let endFreq = $state(0);
  let frameScopeMode = $state(0);  // scope mode from binary frame header (authoritative)
  let fullscreen = $state(false);
  let showBandPlan = $state(true);
  let showEiBi = $state(false);
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
  }>;

  let resizeCapture = $state<GestureCapture | null>(null);
  let resizeCandidate = $state<number | null>(null);
  let dragCapture = $state<GestureCapture | null>(null);
  let dragSurface: HTMLElement | null = null;
  let dragCandidate: number | null = null;
  let dragging = $state(false);

  let centerHz = $derived(
    startFreq >= 0 && endFreq > startFreq ? (startFreq + endFreq) / 2 : 0,
  );
  let spanHz = $derived(endFreq > startFreq ? endFreq - startFreq : 0);
  let spectrumAuthority = $derived(audioFft ? null : toSpectrumAuthority(runtime.state, runtime.caps));
  // MOR-1497: the grab cursor must not promise a drag the gate will refuse.
  // Mirrors the FULL drag-start gate: frequency authority AND usable sample
  // geometry (before the first scope frame endFreq <= startFreq, so
  // handleDragStart bails — the cursor must not claim otherwise).
  let canPan = $derived(
    spectrumAuthority !== null && spectrumAuthority.frequencyHz !== null && spanHz > 0,
  );
  let scopeMode = $derived(frameScopeMode);
  // Tuning indicator: center for CTR/SCROLL-C, proportional for FIX/SCROLL-F
  let isFixedScope = $derived(isFixedScopeFn(scopeMode));
  let tuneVisible = $derived(
    spectrumAuthority?.frequencyHz !== null
      && spectrumAuthority?.frequencyHz !== undefined
      && (!isFixedScope || (
        spectrumAuthority.frequencyHz >= startFreq
        && spectrumAuthority.frequencyHz <= endFreq
      )),
  );
  let tuneHz = $derived(tuneVisible ? spectrumAuthority!.frequencyHz! : 0);
  let confirmedPassband = $derived(
    tuneVisible
      && spectrumAuthority?.mode !== null
      && spectrumAuthority?.mode !== undefined
      && spectrumAuthority.filterWidthHz !== null
      && spectrumAuthority.ifShiftHz !== null,
  );
  let rxMode = $derived(confirmedPassband ? spectrumAuthority!.mode! : '');
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
      && spectrumAuthority.providerGeneration === resizeCapture.authority.providerGeneration
      && spectrumAuthority.receiver === resizeCapture.authority.receiver
      && spectrumAuthority.digest === resizeCapture.authority.digest
      ? resizeCandidate
      : null,
  );
  // The active drag gets an immediate local projection. Once released, only
  // the command lifecycle's busy target may hold that projection; confirmed
  // radio state remains the source of truth for every other state.
  let passbandHz = $derived(
    confirmedPassband
      ? (activeResizePassbandHz ?? lifecyclePassbandHz ?? spectrumAuthority!.filterWidthHz!)
      : 0,
  );
  let passbandShiftHz = $derived(confirmedPassband ? spectrumAuthority!.ifShiftHz! : 0);
  let canResizePassband = $derived(
    confirmedPassband
      && spectrumAuthority !== null
      && spectrumAuthority.rule !== null
      && canResizeFromRightEdge(spectrumAuthority.mode!),
  );
  let tuneLinePct = $derived(
    isFixedScope && spanHz > 0 && tuneVisible
      ? ((tuneHz - startFreq) / spanHz) * 100
      : 50
  );

  // Local brightness only — the radio REF command (0x27/0x19) shifts the
  // scope data that the IC-7610 sends over LAN, so applying refDb here
  // would double-shift. BRT is the frontend-only display adjustment.
  let refLevel = $derived(brtLevel);

  let spectrumOptions = $derived<SpectrumOptions>({
    ...defaultSpectrumOptions,
    spanHz: audioFft || tuneVisible ? spanHz : 0,
    showRfOverlays: !audioFft,
    centerHz,
    tuneHz,
    passbandHz,
    passbandShiftHz,
    refLevel,
    mode: rxMode,
    scopeMode,
  });

  let waterfallOptions = $derived<WaterfallOptions>({
    ...defaultWaterfallOptions,
    spanHz,
    centerHz,
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
    : deriveFreqTicks(spanHz));

  // Passband overlay position derived from the same geometry as the spectrum renderer.
  // In FIX mode pass tuneLinePct so passband follows the carrier indicator.
  let passbandOverlay = $derived(
    getPassbandGeometry(rxMode, passbandHz, passbandShiftHz, spanHz, 100,
      isFixedScope ? tuneLinePct : undefined),
  );
  let pbWidthPct = $derived(passbandOverlay?.widthPx ?? 0);
  let pbLeftPct = $derived(passbandOverlay?.leftPx ?? 0);
  let pbRightPct = $derived(passbandOverlay?.rightPx ?? 0);

  function readAuthority(): SpectrumAuthority | null {
    return audioFft ? null : toSpectrumAuthority(runtime.state, runtime.caps);
  }

  function completeGestureAuthority(requireRule: boolean): SpectrumAuthority | null {
    const current = readAuthority();
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
    return current?.digest === capture.authority.digest
      && geometry?.frameMode === capture.geometry.frameMode
      && geometry.startFreq === capture.geometry.startFreq
      && geometry.endFreq === capture.geometry.endFreq
      && geometry.elementWidth === capture.geometry.elementWidth;
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
    return current?.providerGeneration === capture.authority.providerGeneration
      && current.receiver === capture.authority.receiver
      && current.frequencyHz === capture.authority.frequencyHz
      && geometry?.frameMode === capture.geometry.frameMode
      && geometry.startFreq === capture.geometry.startFreq
      && geometry.endFreq === capture.geometry.endFreq
      && geometry.elementWidth === capture.geometry.elementWidth;
  }

  function handlePassbandResizeStart(event: PointerEvent): void {
    if (!waterfallContent) return;
    const accepted = completeGestureAuthority(true);
    const geometry = readSampleGeometry(waterfallContent);
    if (!accepted || !accepted.rule || !geometry || !canResizeFromRightEdge(accepted.mode!)) return;
    if (isFixedScopeFn(geometry.frameMode)
      && (accepted.frequencyHz! < geometry.startFreq || accepted.frequencyHz! > geometry.endFreq)) return;
    const rect = waterfallContent.getBoundingClientRect();
    event.preventDefault();
    event.stopPropagation();
    resizeCandidate = null;
    resizeCapture = Object.freeze({
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
    const span = geometry.endFreq - geometry.startFreq;
    const sampleX = Math.max(0, Math.min(
      geometry.elementWidth,
      event.clientX - capture.elementLeft,
    ));
    const carrierX = isFixedScopeFn(geometry.frameMode)
      ? ((authority.frequencyHz! - geometry.startFreq) / span) * geometry.elementWidth
      : geometry.elementWidth / 2;
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
    const stable = captureStillCurrent(capture, waterfallContent);
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
    if (scopeLease) return;
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
    const current = readAuthority();
    const frequency = snapFrequency(Math.round(hz));
    if (current?.frequencyHz === null || current?.frequencyHz === undefined || frequency === null) return;
    vfoHandlers.onFreqChange(frequency, current.receiver);
  }

  // --- Scroll-to-tune (mouse wheel on spectrum/waterfall) ---
  function handleWheel(event: WheelEvent): void {
    event.preventDefault();
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
    if (event.target instanceof Element && event.target.closest('button, select, input')) return;
    const surface = event.currentTarget as HTMLElement | null;
    const accepted = completeFrequencyAuthority();
    const geometry = surface ? readSampleGeometry(surface) : null;
    if (!surface || !accepted || !geometry) return;
    const rect = surface.getBoundingClientRect();
    dragSurface = surface;
    dragCandidate = null;
    dragging = false;
    dragCapture = Object.freeze({
      pointerId: event.pointerId,
      startX: event.clientX,
      elementLeft: rect.left,
      authority: accepted,
      geometry,
    });
  }

  const DRAG_THRESHOLD_PX = 5;

  function handleDragMove(event: PointerEvent): void {
    const capture = dragCapture;
    if (!capture || capture.pointerId !== event.pointerId) return;
    const dx = event.clientX - capture.startX;
    if (!dragging) {
      if (Math.abs(dx) < DRAG_THRESHOLD_PX) return;
      dragging = true;
      dragSurface?.setPointerCapture?.(event.pointerId);
    }
    const hzPerPx = (capture.geometry.endFreq - capture.geometry.startFreq)
      / capture.geometry.elementWidth;
    const deltaHz = -dx * hzPerPx;
    const candidate = snapFrequency(Math.round(capture.authority.frequencyHz! + deltaHz));
    dragCandidate = candidate !== capture.authority.frequencyHz ? candidate : null;
  }

  function handleDragEnd(event: PointerEvent): void {
    const capture = dragCapture;
    if (!capture || capture.pointerId !== event.pointerId || !dragSurface) return;
    const candidate = dragCandidate;
    const stable = captureFrequencyStillCurrent(capture, dragSurface);
    dragging = false;
    dragCapture = null;
    dragSurface = null;
    dragCandidate = null;
    if (!stable || candidate === null || candidate === capture.authority.frequencyHz) return;
    vfoHandlers.onFreqChange(candidate, capture.authority.receiver);
  }

  function handleDragCancel(event: PointerEvent): void {
    if (!dragCapture || dragCapture.pointerId !== event.pointerId) return;
    dragging = false;
    dragCapture = null;
    dragSurface = null;
    dragCandidate = null;
  }

  const AUDIO_FFT_AMPLITUDE_MAX = 160;
  const CENTRAL_SCOPE_AMPLITUDE_MAX = 80;

  $effect(() => {
    const sourceIsAudio = audioFft;
    return untrack(() => {
      fullscreen = false;
      scopePixels = null;
      startFreq = 0;
      endFreq = 0;
      frameScopeMode = 0;
      resizeCapture = null;
      dragCapture = null;
      dragging = false;
      dxSpots = [];
      if (sourceIsAudio) runtime.scope.registerPresentationDriver(presentationResources);
      const receive = (frame: Parameters<Parameters<typeof runtime.scope.subscribe>[0]>[0]) => {
        if (!scopeDemandOn) return;
        if (sourceIsAudio && (frame.pixels.length < 3 || frame.pixels.length % 2 === 0
          || frame.endFreq <= frame.startFreq)) return;
        const pixels = sourceIsAudio
          ? frame.pixels.slice(Math.floor(frame.pixels.length / 2)).map(value =>
              Math.round(Math.min(value, AUDIO_FFT_AMPLITUDE_MAX)
                * CENTRAL_SCOPE_AMPLITUDE_MAX / AUDIO_FFT_AMPLITUDE_MAX))
          : frame.pixels;
        frameScopeMode = frame.mode;
        startFreq = sourceIsAudio ? 0 : frame.startFreq;
        endFreq = sourceIsAudio ? (frame.endFreq - frame.startFreq) / 2 : frame.endFreq;
        scopePixels = pixels;
        spectrumPush?.(pixels);
        waterfallPush?.(pixels);
      };
      const unsubscribe = sourceIsAudio
        ? runtime.scope.subscribe(receive)
        : runtime.scope.subscribeHardware(receive);
      if (scopeDemandOn) acquireScopeDemand();
      const unsubDx = sourceIsAudio ? () => {} : runtime.subscribeDx((msg) => {
        if (msg.type === 'dx_spot') {
          const spot = (msg as unknown as { spot: DxSpot }).spot;
          if (spot) dxSpots = [...dxSpots.slice(-49), spot];
        } else if (msg.type === 'dx_spots') {
          const list = (msg as unknown as { spots: DxSpot[] }).spots;
          if (Array.isArray(list)) dxSpots = list.slice(-50);
        }
      });
      return () => {
        unsubscribe();
        unsubDx();
        releaseScopeDemand();
      };
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
{#key audioFft}
<!-- svelte-ignore a11y_no_noninteractive_tabindex -->
<div class="spectrum-panel" class:audio-fft={audioFft} class:fullscreen data-waterfall tabindex="-1" onwheel={handleWheel}>
  {#if audioFft}
    <div class="audio-source-label">
      <span>Audio FFT · AF</span>
      <button type="button" aria-label="Scope viewer" aria-pressed={scopeDemandOn} onclick={() => setScopeDemand(!scopeDemandOn)}>
        Viewer {scopeDemandOn ? 'ON' : 'OFF'}
      </button>
    </div>
  {:else}
  <SpectrumToolbar bind:enableAvg bind:enablePeakHold bind:brtLevel bind:colorScheme bind:fullscreen bind:showBandPlan bind:hiddenLayers bind:showEiBi {scopeDemandOn} onScopeDemandChange={setScopeDemand} {hideSourceControls} {hideScopeControls} {hideAutoStepToggle} {scopeControls} />
  {/if}
  <div class="spectrum-with-scales">
    <div class="db-scale">
      {#each audioFft ? [] : DB_TICKS as tick}
        <div class="tick" style="top: {tick.position}%">{tick.label}</div>
      {/each}
    </div>
    <div class="spectrum-area" class:panning={dragging} class:draggable={canPan} bind:this={spectrumArea} onpointerdown={handleDragStart} role="presentation">
      {#if !scopeDemandOn}
        <div class="scope-disconnected-overlay scope-demand-off-overlay">Scope viewer OFF</div>
      {:else if !scopeConnected}
        <div class="scope-disconnected-overlay">{t('core.overlay.scopeDisconnected')}</div>
      {/if}
      {#if !audioFft}<BandPlanOverlay {startFreq} {endFreq} visible={showBandPlan} {hiddenLayers} />{/if}
      <SpectrumCanvas data={scopePixels} options={spectrumOptions} {spanHz} {enableAvg} {enablePeakHold} onRegisterPush={(fn) => spectrumPush = fn} />
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
  <div class="waterfall-area">
    <div class="waterfall-scale"></div>
    <div class="waterfall-content" class:panning={dragging} class:draggable={canPan} bind:this={waterfallContent} onpointerdown={handleDragStart} role="presentation">
      <WaterfallCanvas options={waterfallOptions} onFreqClick={audioFft ? undefined : handleTune} onRegisterPush={(fn) => waterfallPush = fn} />
      {#if !audioFft}<DxOverlay spots={dxSpots} {startFreq} {endFreq} onTune={handleTune} />{/if}
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
        <div class="tune-line" style="left:{tuneLinePct}%"></div>
      {/if}
    </div>
  </div>
</div>

{#if !audioFft}<EiBiBrowser bind:visible={showEiBi} />{/if}
{/key}

<style>
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
    z-index: 100;
    border-radius: 0;
    border: none;
  }

  .spectrum-with-scales {
    flex: 0 0 30%;
    min-height: 0;
    display: flex;
    border-bottom: 1px solid var(--panel-border);
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
    flex: 0 0 20px;
    position: relative;
    background: var(--panel);
    border-bottom: 1px solid var(--panel-border);
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
    flex: 1 1 70%;
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
    background: rgba(239, 68, 68, 0.75);
    pointer-events: none;
    z-index: 5;
    transform: translateX(-0.5px);
  }

  .passband-overlay {
    position: absolute;
    top: 0;
    bottom: 0;
    background: rgba(59, 130, 246, 0.15);
    border-left: 1px dashed rgba(59, 130, 246, 0.4);
    border-right: 1px dashed rgba(59, 130, 246, 0.4);
    pointer-events: none;
    z-index: 4;
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

  .passband-resize-zone:focus-visible {
    outline: none;
  }

  .scope-disconnected-overlay {
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
