<script lang="ts">
  import { onMount } from 'svelte';
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
  import { getChannel, onMessage, sendCommand } from '../../lib/transport/ws-client';
  import { setScopeConnected, markScopeFrame, isScopeConnected } from '../../lib/stores/connection.svelte';
  import { type DxSpot } from '../../lib/types/protocol';
  import { patchActiveReceiver, radio } from '../../lib/stores/radio.svelte';
  import { getFilterWidthHz } from '../../lib/utils/filter-width';
  import { snapToStep, tuneBy } from '../../lib/stores/tuning.svelte';
  import SpectrumToolbar from './SpectrumToolbar.svelte';
  import BandPlanOverlay from './BandPlanOverlay.svelte';
  import EiBiBrowser from './EiBiBrowser.svelte';
  import { deriveIfShift } from '../../components-v2/panels/filter-controls';
  import { getCapabilities } from '../../lib/stores/capabilities.svelte';
  import { resolveFilterModeConfig } from '../../components-v2/wiring/state-adapter';
  import {
    canResizeFromRightEdge,
    getFilterWidthFromRightEdgePx,
    getPassbandGeometry,
  } from './passband-geometry';
  import {
    parseScopeFrame,
    formatFreqOffset,
    deriveFreqTicks,
    getDragInterval,
    isFixedScope as isFixedScopeFn,
    type ScopeFrame,
  } from './spectrum-logic';

  // --- Props ---
  // `hideSourceControls` is forwarded to SpectrumToolbar so layouts that surface
  // the DUAL + MAIN/SUB scope-source controls elsewhere (v2 desktop VfoHeader
  // bridge, issue #832) can suppress the duplicate in the toolbar. Layouts that
  // do not surface them (v1 desktop/mobile, v2 mobile chip view) omit the prop
  // and keep the controls reachable (#832 mobile/v1 fallback).
  let { hideSourceControls = false } = $props();

  // --- Component state ---
  let scopeConnected = $derived(isScopeConnected());
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
  let spectrumArea: HTMLDivElement | null = null;
  let waterfallContent: HTMLDivElement | null = null;
  let resizingPassband = $state(false);
  let resizingPointerId = $state<number | null>(null);

  let centerHz = $derived(
    startFreq > 0 && endFreq > startFreq ? (startFreq + endFreq) / 2 : 0,
  );
  let spanHz = $derived(endFreq > startFreq ? endFreq - startFreq : 0);

  // Active receiver data for passband overlay
  let rx = $derived(radio.current?.active === 'SUB' ? radio.current?.sub : radio.current?.main);
  let tuneHz = $derived(rx?.freqHz ?? 0);
  let rxMode = $derived(rx?.mode ?? '');
  let passbandHz = $derived(rx?.filterWidth ?? getFilterWidthHz(rxMode, rx?.filter ?? 1));
  let passbandShiftHz = $derived(deriveIfShift(rx?.pbtInner ?? 0, rx?.pbtOuter ?? 0));
  let canResizePassband = $derived(canResizeFromRightEdge(rxMode));
  // Scope mode from binary frame header — always in sync with pixel data.
  // Falls back to control state if no frame received yet.
  let scopeMode = $derived(frameScopeMode ?? (radio.current?.scopeControls?.mode ?? 0));
  // Tuning indicator: center for CTR/SCROLL-C, proportional for FIX/SCROLL-F
  let isFixedScope = $derived(isFixedScopeFn(scopeMode));
  let tuneLinePct = $derived(
    isFixedScope && spanHz > 0 && tuneHz > 0 && tuneHz >= startFreq && tuneHz <= endFreq
      ? ((tuneHz - startFreq) / spanHz) * 100
      : 50
  );
  let filterConfig = $derived(resolveFilterModeConfig(getCapabilities(), rxMode, rx?.dataMode));
  let filterMaxHz = $derived(filterConfig?.maxHz ?? 10000);
  let filterStepHz = $derived(filterConfig?.stepHz ?? filterConfig?.segments?.[0]?.stepHz ?? 100);

  // Local brightness only — the radio REF command (0x27/0x19) shifts the
  // scope data that the IC-7610 sends over LAN, so applying refDb here
  // would double-shift. BRT is the frontend-only display adjustment.
  let refLevel = $derived(brtLevel);

  let spectrumOptions = $derived<SpectrumOptions>({
    ...defaultSpectrumOptions,
    spanHz,
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

  let freqTicks = $derived(deriveFreqTicks(spanHz));

  // Passband overlay position derived from the same geometry as the spectrum renderer.
  // In FIX mode pass tuneLinePct so passband follows the carrier indicator.
  let passbandOverlay = $derived(
    getPassbandGeometry(rxMode, passbandHz, passbandShiftHz, spanHz, 100,
      isFixedScope ? tuneLinePct : undefined),
  );
  let pbWidthPct = $derived(passbandOverlay?.widthPx ?? 0);
  let pbLeftPct = $derived(passbandOverlay?.leftPx ?? 0);
  let pbRightPct = $derived(passbandOverlay?.rightPx ?? 0);

  function applyFilterWidth(width: number): void {
    if (width === passbandHz) {
      return;
    }

    patchActiveReceiver({ filterWidth: width }, true);
    sendCommand('set_filter_width', { width });
  }

  function handlePassbandResizeStart(event: PointerEvent): void {
    if (!canResizePassband || !waterfallContent) {
      return;
    }

    event.preventDefault();
    event.stopPropagation();
    resizingPassband = true;
    resizingPointerId = event.pointerId;
    (event.currentTarget as HTMLElement | null)?.setPointerCapture?.(event.pointerId);
  }

  function handleWindowPointerMove(event: PointerEvent): void {
    if (!resizingPassband || resizingPointerId !== event.pointerId || !waterfallContent) {
      return;
    }

    const rect = waterfallContent.getBoundingClientRect();
    const relativeX = Math.max(0, Math.min(rect.width, event.clientX - rect.left));
    const nextWidth = getFilterWidthFromRightEdgePx(
      rxMode,
      passbandShiftHz,
      spanHz,
      rect.width,
      relativeX,
      filterMaxHz,
      filterStepHz,
    );

    if (nextWidth !== null) {
      applyFilterWidth(nextWidth);
    }
  }

  function stopPassbandResize(event?: PointerEvent): void {
    if (event && resizingPointerId !== null && event.pointerId !== resizingPointerId) {
      return;
    }

    resizingPassband = false;
    resizingPointerId = null;
  }

  // --- Click-to-tune ---
  function handleTune(hz: number): void {
    const freq = snapToStep(Math.round(hz));
    if (freq <= 0) return;
    const receiver = radio.current?.active === 'SUB' ? 1 : 0;
    sendCommand('set_freq', { freq, receiver });
  }

  // --- Scroll-to-tune (mouse wheel on spectrum/waterfall) ---
  function handleWheel(event: WheelEvent): void {
    event.preventDefault();
    const freq = tuneBy(event.deltaY > 0 ? -1 : 1);
    if (freq > 0) handleTune(freq);
  }

  // --- Drag-to-pan (grab and slide the spectrum window) ---
  //
  // All visual feedback comes from the radio: set_freq → radio retunes →
  // scope sends new data → spectrum/waterfall redraws naturally.
  //
  // Adaptive rate limit based on drag speed:
  //   - Slow (<200 px/s): every 200ms — precise tuning feel
  //   - Medium (200–600 px/s): every 400ms — smooth panning
  //   - Fast (>600 px/s): every 700ms — coarse jumps, no flooding
  // Final freq always sent on release.
  //
  let dragging = $state(false);
  // dragEndTime removed — tap-to-tune handled by WaterfallCanvas gesture only
  let dragStartX = $state(0);
  let dragStartFreq = $state(0);
  let dragPointerId = $state<number | null>(null);
  let dragFreq = 0;
  let lastDragSendTime = 0;
  let lastDragSendFreq = 0;
  let lastMoveTime = 0;
  let lastMoveX = 0;
  let dragSpeed = 0; // px/sec, smoothed

  function handleDragStart(event: PointerEvent): void {
    if (event.button !== 0 || resizingPassband) return;
    if ((event.target as HTMLElement).closest('button, select, input')) return;

    dragStartX = event.clientX;
    dragStartFreq = tuneHz;
    dragPointerId = event.pointerId;
    lastDragSendTime = 0;
    lastDragSendFreq = 0;
    lastMoveTime = performance.now();
    lastMoveX = event.clientX;
    dragSpeed = 0;
  }

  const DRAG_THRESHOLD_PX = 5;

  function handleDragMove(event: PointerEvent): void {
    if (dragPointerId === null || dragPointerId !== event.pointerId) return;
    if (spanHz <= 0 || !spectrumArea) return;

    const dx = event.clientX - dragStartX;

    if (!dragging) {
      if (Math.abs(dx) < DRAG_THRESHOLD_PX) return;
      dragging = true;
      (event.target as HTMLElement)?.closest('.spectrum-area, .waterfall-content')
        ?.setPointerCapture?.(event.pointerId);
    }

    // Track drag speed (exponential smoothing)
    const now = performance.now();
    const dt = now - lastMoveTime;
    if (dt > 0) {
      const instantSpeed = (Math.abs(event.clientX - lastMoveX) / dt) * 1000;
      dragSpeed = dragSpeed * 0.7 + instantSpeed * 0.3;
    }
    lastMoveTime = now;
    lastMoveX = event.clientX;

    const rect = spectrumArea.getBoundingClientRect();
    const hzPerPx = spanHz / rect.width;
    const deltaHz = -dx * hzPerPx;
    const newFreq = snapToStep(Math.round(dragStartFreq + deltaHz));

    if (newFreq <= 0) return;
    dragFreq = newFreq;

    // Adaptive interval: slow = responsive, fast = coarse
    const intervalMs = getDragInterval(dragSpeed);

    if (now - lastDragSendTime < intervalMs) return;
    if (newFreq === lastDragSendFreq) return;

    lastDragSendTime = now;
    lastDragSendFreq = newFreq;
    const receiver = radio.current?.active === 'SUB' ? 1 : 0;
    sendCommand('set_freq', { freq: newFreq, receiver });
  }

  function handleDragEnd(event: PointerEvent): void {
    if (dragPointerId !== null && event.pointerId !== dragPointerId) return;

    if (dragging) {
      // Drag-to-pan: send final frequency on release
      if (dragFreq > 0 && dragFreq !== lastDragSendFreq) {
        const receiver = radio.current?.active === 'SUB' ? 1 : 0;
        sendCommand('set_freq', { freq: dragFreq, receiver });
      }
    }
    // Tap-to-tune is handled exclusively by WaterfallCanvas gesture onTap
    // (uses renderer.pixelToFreq with DPR awareness). No duplicate here.

    dragging = false;
    dragPointerId = null;
    dragFreq = 0;
    dragSpeed = 0;
  }

  // --- Lifecycle: connect scope WS + subscribe to DX spots ---
  onMount(() => {
    // Scope data arrives on its own WebSocket channel
    const scopeCh = getChannel('scope');
    scopeCh.connect('/api/v1/scope');
    const unsubState = scopeCh.onStateChange((s) => {
      setScopeConnected(s === 'connected');
    });
    const unsubBinary = scopeCh.onBinary((buf) => {
      markScopeFrame();
      const frame = parseScopeFrame(buf);
      if (!frame) return;
      if (frame.mode !== frameScopeMode) frameScopeMode = frame.mode;
      if (frame.startFreq !== startFreq || frame.endFreq !== endFreq) {
        startFreq = frame.startFreq;
        endFreq = frame.endFreq;
      }
      scopePixels = frame.pixels;
      // Direct push to both children — bypasses Svelte reactivity
      // which can't track Uint8Array changes at 100+ fps
      spectrumPush?.(frame.pixels);
      waterfallPush?.(frame.pixels);
    });

    // DX spots come through the main control WebSocket as JSON messages
    const unsubMsg = onMessage((msg) => {
      if (msg.type === 'dx_spot') {
        const spot = (msg as unknown as { spot: DxSpot }).spot;
        if (spot) dxSpots = [...dxSpots.slice(-49), spot];
      } else if (msg.type === 'dx_spots') {
        const list = (msg as unknown as { spots: DxSpot[] }).spots;
        if (Array.isArray(list)) dxSpots = list.slice(-50);
      }
    });

    return () => {
      unsubState();
      unsubBinary();
      unsubMsg();
      scopeCh.disconnect();
    };
  });
</script>

<svelte:window
  onpointermove={(e) => { handleWindowPointerMove(e); handleDragMove(e); }}
  onpointerup={(e) => { stopPassbandResize(e); handleDragEnd(e); }}
  onpointercancel={(e) => { stopPassbandResize(e); handleDragEnd(e); }}
/>

<div class="spectrum-panel" class:fullscreen onwheel={handleWheel}>
  <SpectrumToolbar bind:enableAvg bind:enablePeakHold bind:brtLevel bind:colorScheme bind:fullscreen bind:showBandPlan bind:hiddenLayers bind:showEiBi {hideSourceControls} />
  <div class="spectrum-with-scales">
    <div class="db-scale">
      {#each DB_TICKS as tick}
        <div class="tick" style="top: {tick.position}%">{tick.label}</div>
      {/each}
    </div>
    <div class="spectrum-area" class:panning={dragging} bind:this={spectrumArea} onpointerdown={handleDragStart} role="presentation">
      {#if !scopeConnected}
        <div class="scope-disconnected-overlay">Scope disconnected — reconnecting…</div>
      {/if}
      <BandPlanOverlay {startFreq} {endFreq} visible={showBandPlan} {hiddenLayers} />
      <SpectrumCanvas data={scopePixels} options={spectrumOptions} {spanHz} {enableAvg} {enablePeakHold} onRegisterPush={(fn) => spectrumPush = fn} />
      {#if spanHz > 0 && pbWidthPct > 0 && canResizePassband}
        <button
          type="button"
          class="passband-resize-zone"
          class:active={resizingPassband}
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
    <div class="waterfall-content" class:panning={dragging} bind:this={waterfallContent} onpointerdown={handleDragStart} role="presentation">
      <WaterfallCanvas options={waterfallOptions} onFreqClick={handleTune} onRegisterPush={(fn) => waterfallPush = fn} />
      <DxOverlay spots={dxSpots} {startFreq} {endFreq} onTune={handleTune} />
      <!-- Tuning + passband indicator overlays the waterfall -->
      {#if spanHz > 0}
        {#if pbWidthPct > 0}
          <div class="passband-overlay" style="left:{pbLeftPct}%;width:{pbWidthPct}%"></div>
          {#if canResizePassband}
            <button
              type="button"
              class="passband-resize-zone"
              class:active={resizingPassband}
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

<EiBiBrowser bind:visible={showEiBi} />

<style>
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
