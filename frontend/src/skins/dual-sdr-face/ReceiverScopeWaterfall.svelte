<script lang="ts">
  import { scopeSubscriptionSurface } from '$lib/runtime/scope-view';
  import { untrack } from 'svelte';
  import { CanvasSurface } from '$lib/canvas/canvas-surface.svelte';
  import { ScopeRenderScheduler } from '$lib/canvas/scope-render-scheduler';
  import { getStageScale } from '../../primitives/stage/stage-scale';
  import { t } from '$lib/i18n';
  import { scopeFrameDisplayFacts, scopeSlotKey } from '../../lib/runtime/adapters/scope-adapter';
  import type { ScopeFrameSource } from './DualSdrFace.svelte';

  interface Props { scopeSource: ScopeFrameSource; slotKey: string; receiver: 0 | 1; }
  let { scopeSource, slotKey, receiver }: Props = $props();
  let canvas = $state<HTMLCanvasElement>();
  let surfaceElement = $state<HTMLDivElement>();
  const surface = new CanvasSurface({ stageScale: getStageScale() });
  $effect(() => {
    const element = canvas;
    if (element) return untrack(() => surface.attach(element));
  });
  let axis = $state<HTMLDivElement>();
  let notice = $state<HTMLDivElement>();

  $effect(() => {
    const source = scopeSource, selected = slotKey;
    let pixels: Uint8Array | null = null;
    let facts: ReturnType<typeof scopeFrameDisplayFacts> | null = null;
    const scheduler = new ScopeRenderScheduler(() => {
      if (!canvas) return;
      const context = canvas.getContext('2d');
      if (!context) return;
      const width = surface.cssW;
      const height = surface.cssH;
      context.clearRect(0, 0, width, height);
      if (pixels === null || pixels.length === 0) return;
      context.beginPath();
      for (let x = 0; x < width; x += 1) {
        const value = pixels[Math.min(pixels.length - 1, Math.floor((x / width) * pixels.length))];
        const y = height - 1 - (value / 255) * (height - 1);
        x === 0 ? context.moveTo(x, y) : context.lineTo(x, y);
      }
      context.strokeStyle = '#dcefeb'; context.lineWidth = 1; context.stroke();
    }, { active: () => surface.visible && surface.sized });
    const stopSize = surface.onSizeChanged(() => scheduler.wake('size'));
    const stopVisibility = surface.onVisibilityChanged(() => scheduler.wake('visibility'));
    function display() {
      if (!surfaceElement || !canvas || !axis || !notice) return;
      const outOfRange = facts?.outOfRange === true && !facts.hasPixels;
      const live = pixels !== null && pixels.length > 0;
      surfaceElement.dataset.scopeState = outOfRange ? 'out-of-range' : live ? 'frame' : 'unknown';
      canvas.dataset.suppliedPixels = String(pixels?.length ?? 0);
      axis.hidden = !facts || (!live && !outOfRange);
      axis.textContent = axis.hidden ? '' : `${facts!.view.startHz} · ${facts!.view.endHz}`;
      notice.hidden = !outOfRange;
      notice.textContent = outOfRange ? t('core.overlay.scopeOutOfRange', {
        start: (facts!.view.startHz / 1e6).toFixed(3), end: (facts!.view.endHz / 1e6).toFixed(3),
      }) : '';
      untrack(() => scheduler.wake('frame'));
    }
    display();
    const unsubscribe = source.subscribe((frame, key) => {
      const stream = source.subscriptionStream;
      const currentSlot = stream ? scopeSlotKey(stream.sourceId, stream.kind === 'radio' ? receiver : null) : selected;
      if (key !== currentSlot) return;
      pixels = frame.pixels;
      facts = scopeFrameDisplayFacts(frame);
      display();
    });
    const unsubscribeHealth = source.subscribeHealth?.(live => {
      if (live) return;
      pixels = null; facts = null; display();
    });
    const unsubscribeGeometry = source.subscriptionStream?.geometry.subscribe(geometry => {
      if (geometry === null) { pixels = null; facts = null; display(); }
    });
    return () => {
      scheduler.dispose(); stopSize(); stopVisibility();
      unsubscribe(); unsubscribeHealth?.(); unsubscribeGeometry?.();
    };
  });
</script>

<div bind:this={surfaceElement} class="scope" data-scope-state="unknown">
  <canvas bind:this={canvas} use:scopeSubscriptionSurface={{ stream: scopeSource.subscriptionStream ?? null,
    receiver, view: { follow: 'vfo', spanHz: 100000 }, facts: surface.facts }}
    aria-label="Receiver spectrum" data-supplied-pixels="0"></canvas>
  <div bind:this={axis} class="axis" hidden></div>
  <div bind:this={notice} class="out-of-range" role="status" hidden></div>
</div>

<style>
  .scope { min-height: 150px; position: relative; background: repeating-linear-gradient(0deg, #05090b 0 18px, #1f3539 19px 20px), repeating-linear-gradient(90deg, transparent 0 46px, #1f3539 47px 48px); border: 2px solid #657277; }
  canvas { display: block; width: 100%; height: 150px; }.axis, .out-of-range { color: #c5d0d0; font: 11px ui-monospace, monospace; text-align: center; }
</style>
