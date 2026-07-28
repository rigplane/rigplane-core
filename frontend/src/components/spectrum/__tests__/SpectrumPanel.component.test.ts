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

type TestLease = Readonly<{ resource: 'hardware-scope'; sessionEpoch: string; id: number }>;
type TestScopeFrame = Readonly<{
  receiver: number;
  mode: number;
  startFreq: number;
  endFreq: number;
  pixels: Uint8Array;
}>;

const runtimeHarness = vi.hoisted(() => {
  const state = {
    capturedHardwareFrame: null as ((frame: TestScopeFrame) => void) | null,
    capturedDxMessage: null as ((message: unknown) => void) | null,
    mockScopeConnected: true,
    mockTuneBy: 0,
    nextLeaseId: 0,
    hardwareUnsubscribe: vi.fn(),
    dxUnsubscribe: vi.fn(),
  };
  const runtime = {
    scope: {
      get hardwareScopeConnected() { return state.mockScopeConnected; },
      subscribeHardware: vi.fn((handler: (frame: TestScopeFrame) => void) => {
        state.capturedHardwareFrame = handler;
        return state.hardwareUnsubscribe;
      }),
    },
    acquireHardwareScope: vi.fn(() => Object.freeze({
      resource: 'hardware-scope' as const,
      sessionEpoch: 'test',
      id: ++state.nextLeaseId,
    })),
    releaseHardwareScope: vi.fn((_lease: TestLease) => true),
    subscribeDx: vi.fn((handler: (message: unknown) => void) => {
      state.capturedDxMessage = handler;
      return state.dxUnsubscribe;
    }),
    send: vi.fn(),
  };
  return { state, runtime };
});

const mockRuntime = runtimeHarness.runtime;

vi.mock('$lib/runtime/frontend-runtime', () => ({
  runtime: runtimeHarness.runtime,
}));

vi.mock('$lib/stores/radio.svelte', () => ({
  radio: { current: null },
  patchActiveReceiver: vi.fn(),
  patchReceiver: vi.fn(),
}));

vi.mock('$lib/stores/tuning.svelte', () => ({
  snapToStep: vi.fn((hz: number) => hz),
  tuneBy: vi.fn(() => runtimeHarness.state.mockTuneBy),
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
import spectrumPanelSource from '../SpectrumPanel.svelte?raw';
import {
  deriveScopeIndicatorState,
} from '../../../components-v2/layout/StatusBar.svelte';
import statusBarSource from '../../../components-v2/layout/StatusBar.svelte?raw';

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
  runtimeHarness.state.capturedHardwareFrame = null;
  runtimeHarness.state.capturedDxMessage = null;
  runtimeHarness.state.mockScopeConnected = true;
  runtimeHarness.state.mockTuneBy = 0;
  runtimeHarness.state.nextLeaseId = 0;
  runtimeHarness.state.hardwareUnsubscribe = vi.fn();
  runtimeHarness.state.dxUnsubscribe = vi.fn();
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

  it('acquires one runtime hardware-scope lease and lifetime-neutral subscriptions on mount', () => {
    mountPanel();
    expect(mockRuntime.acquireHardwareScope).toHaveBeenCalledTimes(1);
    expect(mockRuntime.acquireHardwareScope).toHaveBeenCalledWith('SpectrumPanel');
    expect(mockRuntime.scope.subscribeHardware).toHaveBeenCalledTimes(1);
    expect(mockRuntime.subscribeDx).toHaveBeenCalledTimes(1);
  });

  it('releases the exact lease on OFF and acquires a fresh lease on ON', () => {
    const target = mountPanel();
    const toggle = target.querySelector<HTMLButtonElement>('.scope-demand-toggle')!;
    const firstLease = mockRuntime.acquireHardwareScope.mock.results[0].value;

    expect(toggle).not.toBeNull();
    expect(toggle.getAttribute('aria-pressed')).toBe('true');
    expect(toggle.textContent).toContain('VIEW ON');

    toggle.click();
    flushSync();
    expect(toggle.getAttribute('aria-pressed')).toBe('false');
    expect(toggle.textContent).toContain('VIEW OFF');
    expect(mockRuntime.releaseHardwareScope).toHaveBeenCalledTimes(1);
    expect(mockRuntime.releaseHardwareScope).toHaveBeenLastCalledWith(firstLease);

    toggle.click();
    flushSync();
    expect(toggle.getAttribute('aria-pressed')).toBe('true');
    expect(mockRuntime.acquireHardwareScope).toHaveBeenCalledTimes(2);
    const currentLease = mockRuntime.acquireHardwareScope.mock.results[1].value;
    expect(currentLease).not.toBe(firstLease);

    const component = components.pop()!;
    unmount(component);
    expect(mockRuntime.releaseHardwareScope).toHaveBeenCalledTimes(2);
    expect(mockRuntime.releaseHardwareScope).toHaveBeenLastCalledWith(currentLease);
    expect(runtimeHarness.state.hardwareUnsubscribe).toHaveBeenCalledTimes(1);
    expect(runtimeHarness.state.dxUnsubscribe).toHaveBeenCalledTimes(1);
  });

  it('keeps OFF intent inert across runtime health, teardown, and remount', () => {
    const target = mountPanel();
    const toggle = target.querySelector<HTMLButtonElement>('.scope-demand-toggle')!;
    const releasedLease = mockRuntime.acquireHardwareScope.mock.results[0].value;

    toggle.click();
    runtimeHarness.state.mockScopeConnected = false;
    runtimeHarness.state.mockScopeConnected = true;
    flushSync();

    expect(toggle.getAttribute('aria-pressed')).toBe('false');
    expect(mockRuntime.acquireHardwareScope).toHaveBeenCalledTimes(1);
    expect(mockRuntime.releaseHardwareScope).toHaveBeenCalledTimes(1);

    unmount(components.pop()!);
    expect(mockRuntime.releaseHardwareScope).toHaveBeenCalledTimes(1);
    expect(mockRuntime.releaseHardwareScope).toHaveBeenLastCalledWith(releasedLease);

    mountPanel();
    const remountLease = mockRuntime.acquireHardwareScope.mock.results[1].value;
    expect(remountLease).not.toBe(releasedLease);
    unmount(components.pop()!);
    expect(mockRuntime.releaseHardwareScope).toHaveBeenNthCalledWith(2, remountLease);
  });

  it('renders demand ON without claiming an inactive channel is live', () => {
    runtimeHarness.state.mockScopeConnected = false;
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

  it('wheel event routes tuning command through runtime.send', () => {
    runtimeHarness.state.mockTuneBy = 14_074_000;
    const target = mountPanel();
    const panel = target.querySelector('.spectrum-panel')!;
    const wheelEvent = new WheelEvent('wheel', {
      deltaY: -100,
      bubbles: true,
      cancelable: true,
    });
    panel.dispatchEvent(wheelEvent);
    expect(mockRuntime.send).toHaveBeenCalledWith('set_freq', {
      freq: 14_074_000,
      receiver: 0,
    });
  });

  it('renders runtime hardware frames and DX spots', () => {
    const target = mountPanel();
    runtimeHarness.state.capturedHardwareFrame?.({
      receiver: 0,
      mode: 1,
      startFreq: 14_000_000,
      endFreq: 14_350_000,
      pixels: new Uint8Array(475).fill(64),
    });
    flushSync();
    expect(target.querySelector('.freq-axis')).not.toBeNull();

    runtimeHarness.state.capturedDxMessage?.({
      type: 'dx_spot',
      spot: {
        spotter: 'N0CALL',
        freq: 14_175_000,
        call: 'K1ABC',
        comment: 'test',
        time_utc: '1200',
        timestamp: 1,
      },
    });
    flushSync();
    expect(target.querySelector('.dx-badge')?.textContent).toContain('K1ABC');
  });

  it('keeps all scope transport and health authority behind the runtime facade', () => {
    for (const legacyAuthority of [
      'getChannel',
      'onMessage',
      'sendCommand',
      'setScopeConnected',
      'markScopeFrame',
    ]) {
      expect(spectrumPanelSource).not.toContain(legacyAuthority);
    }
    expect(spectrumPanelSource).toContain('runtime.acquireHardwareScope');
    expect(spectrumPanelSource).toContain('runtime.scope.subscribeHardware');
    expect(spectrumPanelSource).toContain('runtime.subscribeDx');
    expect(spectrumPanelSource).toContain('runtime.send');
  });
});

type ScopeStatusProbe = Parameters<typeof deriveScopeIndicatorState>[0];

function scopeStatus(
  overrides: Partial<ScopeStatusProbe> = {},
): ScopeStatusProbe {
  return {
    source: 'hardware',
    available: true,
    resourceSelected: true,
    demand: 1,
    lifecycle: 'streaming',
    transport: 'connected',
    frameSeen: true,
    ...overrides,
  };
}

describe('StatusBar default scope status consumption', () => {
  it('uses only the canonical runtime default and removes the legacy reader', () => {
    expect(statusBarSource).toContain('runtime.defaultScopeStatus');
    expect(statusBarSource).not.toContain('isScopeConnected');
    expect(statusBarSource).not.toContain('hardwareScopeConnected');
    expect(statusBarSource).not.toContain('audioScopeFrame');
  });

  it.each([
    ['power-off override', scopeStatus(), true, 'disconnected'],
    ['no default source', scopeStatus({ source: null }), false, 'inactive'],
    ['unavailable default', scopeStatus({ available: false }), false, 'inactive'],
    ['unselected resource', scopeStatus({ resourceSelected: false }), false, 'inactive'],
    ['zero demand', scopeStatus({ demand: 0 }), false, 'inactive'],
    ['starting host', scopeStatus({ lifecycle: 'starting', transport: 'disconnected', frameSeen: false }), false, 'starting'],
    ['connecting transport', scopeStatus({ transport: 'connecting', frameSeen: false }), false, 'connecting'],
    ['reconnecting transport', scopeStatus({ transport: 'reconnecting', frameSeen: false }), false, 'reconnecting'],
    ['waiting for current frame', scopeStatus({ frameSeen: false }), false, 'waiting'],
    ['failed host under demand', scopeStatus({ lifecycle: 'failed' }), false, 'failed'],
    ['disconnected transport under demand', scopeStatus({ transport: 'disconnected', frameSeen: false }), false, 'disconnected'],
    ['healthy hardware default', scopeStatus(), false, 'connected'],
    ['healthy audio default uses identical facts', scopeStatus({ source: 'audio_fft' }), false, 'connected'],
    ['streaming host alone is not green', scopeStatus({ transport: 'connecting', frameSeen: false }), false, 'connecting'],
  ] as const)('%s maps to %s', (_label, status, poweredOff, expected) => {
    expect(deriveScopeIndicatorState(status, poweredOff)).toBe(expected);
  });
});
