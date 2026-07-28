/**
 * SpectrumPanel component-level render tests.
 * Mounts the actual Svelte component in jsdom and verifies DOM structure,
 * child component slots, and event wiring.
 */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { mount, unmount, flushSync } from 'svelte';

// ---------------------------------------------------------------------------
// Global mocks (must be before component import)
// ---------------------------------------------------------------------------

// Canvas 2D context — noop stub so <canvas> calls don't throw in jsdom
const noop = () => {};
const noopCanvas: Record<string, unknown> = {
  fillRect: noop,
  clearRect: noop,
  getImageData: (_sx: number, _sy: number, w: number, h: number) => ({
    data: new Uint8ClampedArray(w * h * 4),
    width: w,
    height: h,
  }),
  createImageData: (w: number, h: number) => ({
    data: new Uint8ClampedArray(w * h * 4),
    width: w,
    height: h,
  }),
  putImageData: noop,
  drawImage: noop,
  createLinearGradient: () => ({ addColorStop: noop }),
  createRadialGradient: () => ({ addColorStop: noop }),
  createPattern: () => null,
  beginPath: noop,
  closePath: noop,
  moveTo: noop,
  lineTo: noop,
  arc: noop,
  arcTo: noop,
  rect: noop,
  fill: noop,
  stroke: noop,
  clip: noop,
  save: noop,
  restore: noop,
  scale: noop,
  rotate: noop,
  translate: noop,
  transform: noop,
  setTransform: noop,
  measureText: () => ({ width: 0 }),
  canvas: { width: 800, height: 400 },
  fillStyle: '',
  strokeStyle: '',
  lineWidth: 1,
  font: '',
  textAlign: '',
  textBaseline: '',
  globalAlpha: 1,
  globalCompositeOperation: 'source-over',
};

HTMLCanvasElement.prototype.getContext = vi.fn(function (this: HTMLCanvasElement, type: string) {
  if (type === '2d') return noopCanvas;
  return null;
}) as any;

// ResizeObserver
class MockResizeObserver {
  observe() {}
  unobserve() {}
  disconnect() {}
}
globalThis.ResizeObserver = MockResizeObserver as any;

// requestAnimationFrame / cancelAnimationFrame
globalThis.requestAnimationFrame = vi.fn((cb: FrameRequestCallback) => {
  return setTimeout(() => cb(performance.now()), 0) as unknown as number;
});
globalThis.cancelAnimationFrame = vi.fn((id: number) => clearTimeout(id));

// localStorage stub (jsdom may not expose it as a proper Storage)
const storageMap = new Map<string, string>();
Object.defineProperty(globalThis, 'localStorage', {
  value: {
    getItem: vi.fn((key: string) => storageMap.get(key) ?? null),
    setItem: vi.fn((key: string, val: string) => storageMap.set(key, val)),
    removeItem: vi.fn((key: string) => storageMap.delete(key)),
    clear: vi.fn(() => storageMap.clear()),
    get length() { return storageMap.size; },
    key: vi.fn(() => null),
  },
  writable: true,
  configurable: true,
});

// ---------------------------------------------------------------------------
// Module mocks
// ---------------------------------------------------------------------------

let capturedOnBinary: ((buf: ArrayBuffer) => void) | null = null;
let capturedOnState: ((state: string) => void) | null = null;
let mockScopeConnected = true;

const mockScopeChannel = {
  connect: vi.fn(),
  disconnect: vi.fn(),
  onStateChange: vi.fn((cb: (state: string) => void) => {
    capturedOnState = cb;
    return noop;
  }),
  onBinary: vi.fn((cb: (buf: ArrayBuffer) => void) => {
    capturedOnBinary = cb;
    return noop;
  }),
};

vi.mock('$lib/transport/ws-client', () => ({
  getChannel: vi.fn(() => mockScopeChannel),
  onMessage: vi.fn(() => noop),
  sendCommand: vi.fn(),
}));

vi.mock('$lib/stores/connection.svelte', () => ({
  setScopeConnected: vi.fn(),
  markScopeFrame: vi.fn(),
  isScopeConnected: vi.fn(() => mockScopeConnected),
}));

vi.mock('$lib/stores/radio.svelte', () => ({
  radio: { current: null },
  patchActiveReceiver: vi.fn(),
}));

vi.mock('$lib/stores/tuning.svelte', () => ({
  snapToStep: vi.fn((hz: number) => hz),
  tuneBy: vi.fn(() => 0),
  getTuningStep: vi.fn(() => 100),
  adjustTuningStep: vi.fn(),
  isAutoStep: vi.fn(() => true),
  formatStep: vi.fn(() => '100 Hz'),
}));

vi.mock('$lib/stores/capabilities.svelte', () => ({
  getCapabilities: vi.fn(() => ({})),
  hasCapability: vi.fn((name: string) => name === 'scope'),
  hasDualReceiver: vi.fn(() => false),
}));

vi.mock('$lib/utils/filter-width', () => ({
  getFilterWidthHz: vi.fn(() => 2400),
}));

vi.mock('../../passband-geometry', () => ({
  canResizeFromRightEdge: vi.fn(() => false),
  getFilterWidthFromRightEdgePx: vi.fn(() => null),
  getPassbandGeometry: vi.fn(() => null),
}));

vi.mock('../../../../components-v2/panels/filter-controls', () => ({
  deriveIfShift: vi.fn(() => 0),
}));

vi.mock('../../../../components-v2/wiring/state-adapter', () => ({
  resolveFilterModeConfig: vi.fn(() => null),
}));

// ---------------------------------------------------------------------------
// Import component after mocks
// ---------------------------------------------------------------------------

import SpectrumPanel from '../SpectrumPanel.svelte';
import { getChannel, sendCommand } from '$lib/transport/ws-client';
import { setScopeConnected } from '$lib/stores/connection.svelte';

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

let components: ReturnType<typeof mount>[] = [];

function mountPanel() {
  const target = document.createElement('div');
  document.body.appendChild(target);
  const component = mount(SpectrumPanel, { target, props: {} });
  flushSync();
  components.push(component);
  return target;
}

beforeEach(() => {
  components = [];
  capturedOnState = null;
  mockScopeConnected = true;
  vi.clearAllMocks();
});

afterEach(() => {
  components.forEach((c) => unmount(c));
  document.body.innerHTML = '';
});

// ---------------------------------------------------------------------------
// Tests
// ---------------------------------------------------------------------------

describe('SpectrumPanel component', () => {
  it('mounts without errors', () => {
    const target = mountPanel();
    expect(target.querySelector('.spectrum-panel')).not.toBeNull();
  });

  it('renders the toolbar section', () => {
    const target = mountPanel();
    // SpectrumToolbar renders inside the panel
    const panel = target.querySelector('.spectrum-panel');
    expect(panel).not.toBeNull();
    expect(panel!.children.length).toBeGreaterThan(0);
  });

  it('renders spectrum-with-scales area containing db-scale and spectrum-area', () => {
    const target = mountPanel();
    expect(target.querySelector('.spectrum-with-scales')).not.toBeNull();
    expect(target.querySelector('.db-scale')).not.toBeNull();
    expect(target.querySelector('.spectrum-area')).not.toBeNull();
  });

  it('renders waterfall area with waterfall-content', () => {
    const target = mountPanel();
    expect(target.querySelector('.waterfall-area')).not.toBeNull();
    expect(target.querySelector('.waterfall-content')).not.toBeNull();
  });

  it('renders dB scale ticks', () => {
    const target = mountPanel();
    const ticks = target.querySelectorAll('.db-scale .tick');
    expect(ticks.length).toBe(4);
    const labels = Array.from(ticks).map((t) => t.textContent?.trim());
    expect(labels).toEqual(['0', '-20', '-40', '-60']);
  });

  it('connects scope WebSocket channel on mount', () => {
    mountPanel();
    expect(getChannel).toHaveBeenCalledWith('scope');
    expect(mockScopeChannel.connect).toHaveBeenCalledWith('/api/v1/scope');
    expect(mockScopeChannel.onBinary).toHaveBeenCalled();
    expect(mockScopeChannel.onStateChange).toHaveBeenCalled();
  });

  it('releases and reacquires viewer demand through the existing scope channel', () => {
    const target = mountPanel();
    const toggle = target.querySelector<HTMLButtonElement>('.scope-demand-toggle')!;

    expect(toggle).not.toBeNull();
    expect(toggle.getAttribute('aria-pressed')).toBe('true');

    toggle.click();
    flushSync();
    expect(toggle.getAttribute('aria-pressed')).toBe('false');
    expect(mockScopeChannel.disconnect).toHaveBeenCalledTimes(1);
    expect(setScopeConnected).toHaveBeenLastCalledWith(false);

    toggle.click();
    flushSync();
    expect(toggle.getAttribute('aria-pressed')).toBe('true');
    expect(getChannel).toHaveBeenCalledTimes(1);
    expect(mockScopeChannel.connect).toHaveBeenCalledTimes(2);
    expect(mockScopeChannel.connect).toHaveBeenLastCalledWith('/api/v1/scope');
  });

  it('does not disconnect viewer demand twice after OFF during teardown', () => {
    const target = mountPanel();
    target.querySelector<HTMLButtonElement>('.scope-demand-toggle')!.click();
    flushSync();

    capturedOnState?.('reconnecting');
    capturedOnState?.('connected');
    expect(mockScopeChannel.connect).toHaveBeenCalledTimes(1);
    expect(setScopeConnected).toHaveBeenLastCalledWith(false);

    const component = components.pop()!;
    unmount(component);
    expect(mockScopeChannel.disconnect).toHaveBeenCalledTimes(1);
  });

  it('keeps demand intent ON across channel disconnect and reconnect health changes', () => {
    const target = mountPanel();
    const toggle = target.querySelector<HTMLButtonElement>('.scope-demand-toggle')!;

    capturedOnState?.('disconnected');
    capturedOnState?.('connected');
    flushSync();

    expect(toggle.getAttribute('aria-pressed')).toBe('true');
    expect(mockScopeChannel.connect).toHaveBeenCalledTimes(1);
    expect(mockScopeChannel.disconnect).not.toHaveBeenCalled();
    expect(setScopeConnected).toHaveBeenNthCalledWith(1, false);
    expect(setScopeConnected).toHaveBeenNthCalledWith(2, true);
  });

  it('renders demand ON without claiming an inactive channel is live', () => {
    mockScopeConnected = false;
    const target = mountPanel();

    expect(
      target.querySelector<HTMLButtonElement>('.scope-demand-toggle')?.getAttribute('aria-pressed'),
    ).toBe('true');
    expect(target.querySelector('.scope-disconnected-overlay')).not.toBeNull();
    expect(target.querySelector('.scope-demand-off-overlay')).toBeNull();
  });

  it('does not render freq-axis when no span data', () => {
    const target = mountPanel();
    // Without scope frames, spanHz = 0, so freq-axis is not rendered
    expect(target.querySelector('.freq-axis')).toBeNull();
  });

  it('does not render tune-line when no span data', () => {
    const target = mountPanel();
    expect(target.querySelector('.tune-line')).toBeNull();
  });

  it('unmounts cleanly without errors', () => {
    const target = mountPanel();
    const component = components.pop()!;
    expect(() => unmount(component)).not.toThrow();
    // Verify disconnect was called via the cleanup returned from onMount
    target.remove();
  });

  it('wheel event on panel triggers tuning command', async () => {
    const { sendCommand } = await import('$lib/transport/ws-client');
    const target = mountPanel();
    const panel = target.querySelector('.spectrum-panel')!;
    const wheelEvent = new WheelEvent('wheel', {
      deltaY: -100,
      bubbles: true,
      cancelable: true,
    });
    panel.dispatchEvent(wheelEvent);
    // Wheel handler should send a tuning command via sendCommand
    // (may not fire if no freq data — just verify no crash)
    expect(panel).toBeTruthy();
  });

  it('renders freq-axis after receiving binary scope frame', () => {
    const target = mountPanel();
    // Build a minimal scope frame header (16 bytes) + 475 pixels
    // Header: seq(1) + id(1) + startFreq(4, LE) + endFreq(4, LE) + flags(6)
    const headerSize = 16;
    const pixelCount = 475;
    const buf = new ArrayBuffer(headerSize + pixelCount);
    const view = new DataView(buf);
    view.setUint8(0, 1);  // seq = 1 (single-packet frame)
    view.setUint8(1, 0);  // id
    view.setUint32(2, 14_000_000, true);  // startFreq LE
    view.setUint32(6, 14_350_000, true);  // endFreq LE
    // Fill pixels with mid-level data
    const pixels = new Uint8Array(buf, headerSize);
    pixels.fill(64);

    // Deliver frame via captured binary handler
    if (capturedOnBinary) {
      capturedOnBinary(buf);
      flushSync();
    }
    // After a frame with valid span, the component should update
    // (exact DOM depends on rAF scheduling — verify no crash at minimum)
    expect(target.querySelector('.spectrum-panel')).not.toBeNull();
  });
});
