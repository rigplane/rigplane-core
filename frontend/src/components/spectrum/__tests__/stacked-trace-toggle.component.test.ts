/**
 * MOR-4463: the spectrum pane shows the flat trace by default and the
 * stacked-trace ("3D") view only after the user's own toggle; the same
 * frames feed whichever view is mounted, and a browser without WebGL2 keeps
 * the flat view and marks the toggle unavailable.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { webglFixture } from '$lib/renderers/__tests__/webgl-fixture';
import { RecordedPath } from '$lib/renderers/__tests__/canvas-layers-fixture';

type Push = (pixels: Uint8Array) => void;
type Fixture = ReturnType<typeof webglFixture>;

// The preference store is a module singleton, so every test loads a fresh
// module graph — Svelte itself included, so the mount and the components
// share one runtime.
let svelte: typeof import('svelte');
let target: HTMLDivElement;
let component: Record<string, unknown> | undefined;
let frames: ((now: number) => void)[];
let gl: Fixture[];
let flatDraws: number;
let webglAvailable: boolean;

class BoxResizeObserver {
  constructor(private readonly callback: ResizeObserverCallback) {}
  observe(element: Element): void {
    this.callback(
      [{ target: element, contentRect: { width: 400, height: 200 } } as unknown as ResizeObserverEntry],
      this as unknown as ResizeObserver,
    );
  }
  disconnect(): void {}
}

function flushFrames(now: number): void {
  const due = frames; frames = [];
  for (const frame of due) frame(now);
}

async function mountPane(props: { colorScheme?: 'classic'; showRfOverlays?: boolean } = { colorScheme: 'classic' }) {
  svelte = await import('svelte');
  const store = await import('$lib/stores/stacked-trace.svelte');
  const { default: Harness } = await import('./StackedTraceHarness.svelte');
  let push: Push = () => {};
  component = svelte.mount(Harness, { target, props: { ...props, onPush: (fn: Push) => { push = fn; } } });
  svelte.flushSync();
  return {
    store,
    set: (enabled: boolean) => { store.setStackedTraceEnabled(enabled); svelte.flushSync(); },
    push: (pixels: Uint8Array) => push(pixels),
    stacked: () => target.querySelector('[data-testid="stacked-trace"]'),
    flat: () => target.querySelector('.spectrum-container > canvas'),
  };
}

beforeEach(() => {
  vi.resetModules();
  localStorage.clear();
  frames = []; gl = []; flatDraws = 0; webglAvailable = true;
  target = document.createElement('div');
  document.body.appendChild(target);
  vi.stubGlobal('requestAnimationFrame', (callback: (now: number) => void) => frames.push(callback));
  vi.stubGlobal('cancelAnimationFrame', () => {});
  vi.stubGlobal('ResizeObserver', BoxResizeObserver);
  vi.stubGlobal('Path2D', RecordedPath);
  vi.spyOn(HTMLCanvasElement.prototype, 'getContext').mockImplementation(function (this: HTMLCanvasElement, kind: string) {
    if (kind === 'webgl2') {
      if (!webglAvailable) return null;
      const fixture = webglFixture(this.width, this.height);
      gl.push(fixture);
      return fixture.gl;
    }
    const owner = this;
    return new Proxy({ canvas: owner } as Record<string, unknown>, {
      get(context, key: string) {
        if (key in context) return context[key];
        if (key === 'createLinearGradient') return () => ({ addColorStop: () => {} });
        if (key === 'measureText') return () => ({ width: 10 });
        if (key === 'stroke') return () => { if (owner.parentElement?.classList.contains('spectrum-container')) flatDraws++; };
        return () => {};
      },
      set(context, key: string, value) { context[key] = value; return true; },
    }) as unknown as CanvasRenderingContext2D;
  });
});

afterEach(async () => {
  if (component) await svelte.unmount(component);
  component = undefined;
  target.remove();
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});

describe('MOR-4463 stacked-trace toggle', () => {
  it('shows the flat trace by default and never asks for a WebGL context', async () => {
    const pane = await mountPane();
    expect(pane.store.isStackedTraceEnabled()).toBe(false);
    expect(pane.flat()).not.toBeNull();
    expect(pane.stacked()).toBeNull();
    pane.push(new Uint8Array(475).fill(40));
    flushFrames(16);
    expect(flatDraws).toBeGreaterThan(0);
    expect(gl).toHaveLength(0);
  });

  it('switches to the stacked view on the toggle, feeds it the same frames, and switches back', async () => {
    const pane = await mountPane();
    pane.push(new Uint8Array(475).fill(40));
    flushFrames(16);

    pane.set(true);
    expect(pane.stacked()).not.toBeNull();
    expect(pane.flat()).toBeNull();
    expect(localStorage.getItem('rigplane-spectrum-stacked-trace')).toBe('1');
    expect(gl).toHaveLength(1);
    // The frame delivered before the switch is replayed into the new view.
    flushFrames(32);
    // 475 samples plus their pairwise-maximum levels: the first history row
    // and the live trace.
    expect(gl[0].named('texSubImage2D').map(call => call.args[4])).toEqual([952, 952]);

    // A later frame of another size goes to the stacked view, not the flat one.
    const flatBefore = flatDraws;
    pane.push(new Uint8Array(2048).fill(20));
    flushFrames(200);
    expect(gl[0].named('texSubImage2D').map(call => call.args[4])).toEqual([952, 952, 4095, 4095]);
    expect(flatDraws).toBe(flatBefore);
    svelte.flushSync();
    expect(pane.stacked()!.getAttribute('data-history-rows')).toBe('2');

    pane.set(false);
    expect(pane.stacked()).toBeNull();
    expect(pane.flat()).not.toBeNull();
    expect(localStorage.getItem('rigplane-spectrum-stacked-trace')).toBe('0');
    flushFrames(300);
    expect(flatDraws).toBeGreaterThan(flatBefore);
  });

  it('restores the stacked view from the stored preference on the next load', async () => {
    localStorage.setItem('rigplane-spectrum-stacked-trace', '1');
    const pane = await mountPane();
    expect(pane.store.isStackedTraceEnabled()).toBe(true);
    expect(pane.stacked()).not.toBeNull();
  });

  it('keeps the flat trace for a host without a palette and for a pane without RF overlays', async () => {
    localStorage.setItem('rigplane-spectrum-stacked-trace', '1');
    const bare = await mountPane({});
    expect(bare.stacked()).toBeNull();
    expect(bare.flat()).not.toBeNull();
    await svelte.unmount(component!);
    const audio = await mountPane({ colorScheme: 'classic', showRfOverlays: false });
    expect(audio.stacked()).toBeNull();
    expect(audio.flat()).not.toBeNull();
  });

  it('falls back to the flat trace and marks the view unavailable without WebGL2', async () => {
    webglAvailable = false;
    const pane = await mountPane();
    pane.set(true);
    expect(pane.store.isStackedTraceUnavailable()).toBe(true);
    expect(pane.stacked()).toBeNull();
    expect(pane.flat()).not.toBeNull();
    pane.push(new Uint8Array(475).fill(40));
    flushFrames(16);
    expect(flatDraws).toBeGreaterThan(0);
  });
});
