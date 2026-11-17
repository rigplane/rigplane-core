<script lang="ts">
  // The stacked-trace ("3D") view of the spectrum pane (MOR-4463). It is fed
  // the same frames and the same options as the flat canvas it replaces.
  import { onMount, untrack } from 'svelte';
  import { StackedTraceRenderer, StackedTraceRenderError } from '../../lib/renderers/stacked-trace-renderer';
  import { renderSpectrum, type SpectrumOptions } from '../../lib/renderers/spectrum-renderer';
  import { defaultWaterfallOptions, type ColorSchemeName, type WaterfallOptions } from '../../lib/renderers/waterfall-renderer';
  import type { CanvasSurface } from '../../lib/canvas/canvas-surface.svelte';
  import { ScopeRenderScheduler } from '../../lib/canvas/scope-render-scheduler';
  import type { ScopeRenderTrace, TraceToken } from '../../lib/runtime/scope-render-trace';
  import { stackedTraceTunables } from '../../lib/renderers/stacked-trace-geometry';
  import { setStackedTraceSettings, stackedTraceSettings } from '../../lib/stores/stacked-trace.svelte';
  import { t } from '$lib/i18n';

  interface Props {
    options: SpectrumOptions;
    colorScheme: ColorSchemeName;
    surface: CanvasSurface;
    onRegisterPush: (fn: (data: Uint8Array, token?: TraceToken | null) => void) => void;
    /** Diagnostic render trace of the host pane; records this view's geometry, paints and coalesced frames. */
    trace?: ScopeRenderTrace | null;
    /** The browser gave no usable WebGL2 context; the host falls back to the flat view. */
    onUnavailable: () => void;
  }

  let { options, colorScheme, surface, onRegisterPush, onUnavailable, trace = null }: Props = $props();

  let scene: HTMLCanvasElement;
  let chrome: HTMLCanvasElement;
  let renderer: StackedTraceRenderer | null = null;
  let historyRows = $state(0);
  // The look of the view is the user's to adjust; the values are kept per browser.
  let settings = $derived(stackedTraceSettings());
  let tuning = $state(false);
  let tuneKey: HTMLButtonElement;
  // The pane clips its content and can be a few dozen pixels tall (a phone),
  // so the adjustments open against the viewport, under the gear key, and
  // scroll inside themselves when the screen is shorter than the list.
  let panelPlace = $state('');
  function toggleTuning(): void {
    if (!tuning) {
      const key = tuneKey.getBoundingClientRect();
      const top = Math.max(8, Math.min(key.bottom + 4, window.innerHeight - 160));
      panelPlace = `top:${Math.round(top)}px;right:${Math.max(4, Math.round(window.innerWidth - key.right))}px;`
        + `max-height:${Math.round(window.innerHeight - top - 8)}px;`;
    }
    tuning = !tuning;
  }

  /** The sample placement and appearance the renderer needs, from the pane's own options. */
  function rowOptions(source: SpectrumOptions, target: WaterfallOptions): WaterfallOptions {
    target.coverage = source.coverage;
    target.colorScheme = colorScheme;
    target.refLevel = source.refLevel;
    target.centerHz = source.centerHz;
    target.spanHz = source.spanHz;
    target.sampleSpanHz = source.sampleSpanHz;
    target.sampleOffset = source.sampleOffset;
    target.sampleWindow = source.sampleWindow;
    target.sampleAmplitudeMax = source.sampleAmplitudeMax;
    target.panoramaShiftHz = source.panoramaShiftHz;
    return target;
  }

  const viewOptions = { ...defaultWaterfallOptions };
  const frame = { pixels: null as Uint8Array | null, options: { ...defaultWaterfallOptions }, pending: false,
    token: null as TraceToken | null };
  const scheduler = new ScopeRenderScheduler(paint, {
    active: () => surface.visible && surface.sized && renderer !== null,
  });

  /** A typed renderer failure ends the view: the host falls back to the flat trace. */
  function failed(error: unknown): void {
    if (!(error instanceof StackedTraceRenderError)) throw error;
    renderer?.destroy(); renderer = null;
    onUnavailable();
  }

  function paint(now: number): void {
    if (!renderer) return;
    const measuring = trace?.status === 'recording';
    if (measuring) trace?.recordGeometry('spectrum', surface.cssW, surface.cssH, scene.width, scene.height, window.devicePixelRatio || 1);
    const paintAt = measuring ? performance.now() : 0;
    try {
      renderer.setPixelScale(surface.pixelScale);
      renderer.updateOptions(rowOptions(options, viewOptions));
      if (frame.pending && frame.pixels) renderer.pushRow(frame.pixels, frame.options, now);
      frame.pending = false;
      // Rows glide back between frames: keep painting display frames meanwhile.
      if (renderer.draw(now)) scheduler.wake('view');
      if (renderer.historyRows !== historyRows) historyRows = renderer.historyRows;
      if (measuring) trace?.record('spectrum_render', frame.token, performance.now() - paintAt);
    } catch (error) { failed(error); }
  }

  function push(pixels: Uint8Array, token?: TraceToken | null): void {
    if (trace?.status === 'recording' && frame.pending && frame.token && token && frame.token !== token) {
      trace.record('coalesced', frame.token, 0, true);
    }
    frame.token = token ?? null;
    frame.pixels = pixels;
    rowOptions(options, frame.options);
    frame.pending = true;
    scheduler.wake('frame');
  }

  // The frequency labels, the tuning marker, the passband and the coverage
  // hatch are painted by the flat renderer itself over the scene, with its
  // trace and grid made transparent.
  const CHROME_SAMPLES = new Uint8Array(2);
  let chromeOptions = $derived<SpectrumOptions>({
    ...options, lineColor: 'transparent', fillColor: 'transparent', fillColorBottom: 'transparent',
    gridColor: 'transparent',
  });
  function paintChrome(): void {
    const ctx = chrome?.getContext('2d');
    if (!ctx || !surface.sized) return;
    renderSpectrum(ctx, CHROME_SAMPLES, Math.max(1, Math.floor(surface.cssW)), Math.max(1, Math.floor(surface.cssH)), chromeOptions);
  }

  $effect(() => {
    options; colorScheme;
    untrack(() => scheduler.wake('view'));
  });
  $effect(() => {
    chromeOptions;
    untrack(paintChrome);
  });
  $effect(() => {
    if (surface.visible) untrack(() => scheduler.wake('visibility'));
  });

  let builtRows = 0;
  // New settings apply to the rows already on screen; only another row
  // count needs a new ring, and with it a new history.
  $effect(() => {
    const next = settings;
    untrack(() => {
      if (!renderer) return;
      if (next.rows !== builtRows) createRenderer();
      else { renderer.setSettings(next); scheduler.wake('view'); }
    });
  });

  function createRenderer(): void {
    renderer?.destroy(); renderer = null;
    try {
      renderer = new StackedTraceRenderer(scene, rowOptions(options, viewOptions), settings);
      builtRows = settings.rows;
    } catch (error) { failed(error); return; }
    frame.pending = frame.pixels !== null;
    scheduler.wake('size');
  }

  /** A lost context ends the view like any other renderer failure. */
  function contextLost(): void {
    failed(new StackedTraceRenderError());
  }

  onMount(() => {
    scene.addEventListener('webglcontextlost', contextLost);
    // The WebGL2 context must exist before the surface sizes the canvas:
    // the surface's sizing asks a canvas for a 2D context.
    createRenderer();
    const unbindScene = surface.bindBacking(scene);
    const unbindChrome = surface.bindBacking(chrome);
    // Sizing a canvas clears it — on a new size and on a new pixel scale
    // alike — so the chrome is painted after the binding and after every
    // size notification, which the bindings above have already applied.
    paintChrome();
    const stopSize = surface.onSizeChanged(() => { paintChrome(); scheduler.wake('size'); });
    onRegisterPush(push);
    return () => {
      scene.removeEventListener('webglcontextlost', contextLost);
      unbindScene(); unbindChrome(); stopSize();
      scheduler.dispose();
      renderer?.destroy(); renderer = null;
    };
  });
</script>

<div class="stacked-trace" data-testid="stacked-trace" data-stacked-trace data-history-rows={historyRows}>
  <canvas bind:this={scene}></canvas>
  <canvas bind:this={chrome} class="chrome"></canvas>
  <button type="button" class="tune-key" data-testid="stacked-trace-tune" aria-expanded={tuning} bind:this={tuneKey}
    title={t('core.scope.stackedTrace.tune.title')} onclick={toggleTuning}>⚙</button>
  {#if tuning}
    <!-- A wheel over the adjustments scrolls or moves them; it never reaches the pane's scroll-to-tune. -->
    <div class="tune-panel" role="dialog" aria-label={t('core.scope.stackedTrace.tune.title')} style={panelPlace}
      onwheel={(event) => event.stopPropagation()}>
      {#each stackedTraceTunables as item (item.key)}
        <label>
          <span>{t(`core.scope.stackedTrace.tune.${item.key}`)}</span>
          <input type="range" min={item.min} max={item.max} step={item.step} value={settings[item.key]} data-setting={item.key}
            oninput={(event) => setStackedTraceSettings({ [item.key]: Number(event.currentTarget.value) })} />
          <output>{Number(settings[item.key].toFixed(2))}</output>
        </label>
      {/each}
      <button type="button" data-testid="stacked-trace-tune-reset" onclick={() => setStackedTraceSettings(null)}>
        {t('core.scope.stackedTrace.tune.reset')}</button>
    </div>
  {/if}
</div>

<style>
  .stacked-trace {
    position: absolute;
    inset: 0;
    background: #001020;
  }

  canvas {
    position: absolute;
    inset: 0;
    display: block;
    width: 100%;
    height: 100%;
  }

  .chrome {
    pointer-events: none;
  }

  .tune-key {
    position: absolute;
    top: 22px;
    right: 4px;
    z-index: 7;
    width: 22px;
    height: 22px;
    padding: 0;
    border: 1px solid var(--panel-border, #24303c);
    border-radius: 4px;
    background: var(--panel, #10161d);
    color: var(--text-muted, #8b98a6);
    cursor: pointer;
    opacity: 0.6;
  }

  .tune-key:hover, .tune-key[aria-expanded='true'] {
    opacity: 1;
  }

  .tune-panel {
    position: fixed;
    z-index: 1000;
    box-sizing: border-box;
    max-width: calc(100vw - 8px);
    overflow-y: auto;
    overscroll-behavior: contain;
    display: grid;
    gap: 4px;
    padding: 8px;
    border: 1px solid var(--panel-border, #24303c);
    border-radius: 6px;
    background: var(--panel, #10161d);
    color: var(--text, #d6dde5);
    font-size: 11px;
    cursor: default;
  }

  .tune-panel label {
    display: grid;
    grid-template-columns: minmax(0, 110px) minmax(60px, 120px) 34px;
    align-items: center;
    gap: 6px;
  }

  .tune-panel output {
    text-align: right;
    font-variant-numeric: tabular-nums;
  }

</style>
