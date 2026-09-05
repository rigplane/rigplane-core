/**
 * SpectrumPanel component-level render tests.
 * Mounts the actual Svelte component in jsdom and verifies DOM structure,
 * child component slots, and event wiring.
 */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { createRawSnippet, mount, unmount, flushSync } from 'svelte';
import { SvelteMap } from 'svelte/reactivity';
import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import type { ScopeDisplayProjection } from '$lib/runtime/adapters/scope-display-projection';
import type { ManagedAppTxController } from '$lib/runtime/tx-controller/managed-app-host';

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

// jsdom does not implement `Element.scrollIntoView` at all; MOR-1456's
// `focus_target` dispatch calls it unconditionally after `.focus()`.
Element.prototype.scrollIntoView = vi.fn();
HTMLElement.prototype.setPointerCapture = vi.fn();
HTMLElement.prototype.releasePointerCapture = vi.fn();

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
    currentState: Object.freeze({ source: 'test-state' }) as unknown,
    currentCaps: Object.freeze({ source: 'test-capabilities' }) as unknown,
    mockScopeConnected: true,
    tuningStep: 1_000,
    nextLeaseId: 0,
    hardwareUnsubscribe: vi.fn(),
    dxUnsubscribe: vi.fn(),
  };
  const runtime = {
    get state() { return state.currentState; },
    get caps() { return state.currentCaps; },
    onTxAudioDied: () => () => {},
    defaultScopeStatus: { transport: 'connected' },
    scope: {
      registerPresentationDriver: vi.fn(),
      subscribe: vi.fn(() => () => {}),
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

const authorityHarness = vi.hoisted(() => {
  const state = { current: null as any, useProductionSelector: false };
  return {
    state,
    toSpectrumAuthority: vi.fn((_state: unknown, _caps: unknown) => state.current),
    snapSpectrumFilterWidth: vi.fn((raw: number, rule: any) => {
      if (!rule) return null;
      if (rule.kind === 'table') {
        return rule.values.reduce((best: number, value: number) =>
          Math.abs(raw - value) < Math.abs(raw - best) ? value : best);
      }
      const bounded = Math.max(rule.minHz, Math.min(rule.maxHz, raw));
      const step = rule.kind === 'step' ? rule.stepHz : rule.segments[0].stepHz;
      return rule.minHz + Math.round((bounded - rule.minHz) / step) * step;
    }),
  };
});
const authorityRefreshState = new SvelteMap<string, number>([['value', 0]]);

function refreshSpectrumAuthority(): void {
  authorityRefreshState.set('value', (authorityRefreshState.get('value') ?? 0) + 1);
}

const handlerHarness = vi.hoisted(() => {
  const vfo = Object.freeze({ onFreqChange: vi.fn() });
  const filter = Object.freeze({ onFilterWidthCommit: vi.fn() });
  return {
    vfo,
    filter,
    getVfoHandlers: vi.fn(() => vfo),
    getFilterHandlers: vi.fn(() => filter),
  };
});

type TestFilterWidthLifecycle = Readonly<{
  confirmed: number | null;
  target: number | null;
  phase: 'unavailable' | 'idle' | 'pending' | 'acknowledged' | 'confirmed';
  busy: boolean;
  outcome: { phase: 'confirmed' | 'failed' | 'timed-out' | 'cancelled'; error?: string } | null;
  presentation: {
    lifecycleId: string;
    transitionId: string;
    receiver: 0 | 1;
    sessionEpoch: number;
    target: number;
    status: 'pending' | 'acknowledged' | 'confirmed' | 'failed' | 'timed-out' | 'cancelled';
  } | null;
}>;

const idleFilterWidthLifecycle: TestFilterWidthLifecycle = Object.freeze({
  confirmed: 2_400,
  target: null,
  phase: 'idle',
  busy: false,
  outcome: null,
  presentation: null,
});
const filterWidthLifecycleState = new SvelteMap<string, TestFilterWidthLifecycle>([
  ['value', idleFilterWidthLifecycle],
]);

function setFilterWidthLifecycle(
  overrides: Partial<TestFilterWidthLifecycle>,
): void {
  filterWidthLifecycleState.set('value', Object.freeze({
    ...idleFilterWidthLifecycle,
    ...overrides,
  }));
}

function pendingFilterWidthLifecycle(target: number, receiver: 0 | 1 = 0) {
  return {
    confirmed: 2_400,
    target,
    phase: 'acknowledged' as const,
    busy: true,
    outcome: null,
    presentation: {
      lifecycleId: `width-${target}`,
      transitionId: `width-${target}-acknowledged`,
      receiver,
      sessionEpoch: 17,
      target,
      status: 'acknowledged' as const,
    },
  };
}

const passbandHarness = vi.hoisted(() => ({
  rawWidth: 2_700 as number | null,
  getFilterWidthFromRightEdgePx: vi.fn(() => 2_700 as number | null),
}));

const spectrumRendererHarness = vi.hoisted(() => ({
  lastOptions: null as any,
  render: vi.fn((_ctx: unknown, _data: Uint8Array, _width: number, _height: number, options: any) => {
    spectrumRendererHarness.lastOptions = options;
  }),
  setAvgEnabled: vi.fn(),
  setPeakHoldEnabled: vi.fn(),
}));

const appTxHostHarness = vi.hoisted(() => ({
  controller: null as ManagedAppTxController | null,
}));

vi.mock('$lib/runtime/frontend-runtime', () => ({
  presentationResources: { acquire: vi.fn(() => ({ resource: 'audio-fft', id: 1 })), release: vi.fn() },
  runtime: runtimeHarness.runtime,
}));

vi.mock('$lib/runtime/adapters/scope-adapter', async (importOriginal) => {
  const actual = await importOriginal<typeof import('$lib/runtime/adapters/scope-adapter')>();
  return {
    ...actual,
    toSpectrumAuthority: (...args: Parameters<typeof actual.toSpectrumAuthority>) => {
      authorityRefreshState.get('value');
      return authorityHarness.state.useProductionSelector
        ? actual.toSpectrumAuthority(...args)
        : authorityHarness.toSpectrumAuthority(...args);
    },
    snapSpectrumFilterWidth: authorityHarness.snapSpectrumFilterWidth,
  };
});

vi.mock('$lib/runtime/adapters/panel-adapters', async (importOriginal) => {
  const actual = await importOriginal<typeof import('$lib/runtime/adapters/panel-adapters')>();
  return {
    ...actual,
    getVfoHandlers: handlerHarness.getVfoHandlers,
    getFilterHandlers: handlerHarness.getFilterHandlers,
    getFilterWidthCommandLifecycle: () => filterWidthLifecycleState.get('value')!,
  };
});

vi.mock('$lib/renderers/spectrum-renderer', async (importOriginal) => {
  const actual = await importOriginal<typeof import('$lib/renderers/spectrum-renderer')>();
  class SpectrumRenderer {
    setAvgEnabled = spectrumRendererHarness.setAvgEnabled;
    setPeakHoldEnabled = spectrumRendererHarness.setPeakHoldEnabled;
    render = spectrumRendererHarness.render;
  }
  return { ...actual, SpectrumRenderer };
});

vi.mock('$lib/runtime/tx-controller/managed-app-host', () => ({
  getManagedAppTxController: () => appTxHostHarness.controller,
}));

vi.mock('$lib/stores/radio.svelte', () => ({
  radio: { current: null },
  patchActiveReceiver: vi.fn(),
  patchReceiver: vi.fn(),
}));

vi.mock('$lib/stores/tuning.svelte', () => ({
  snapToStep: vi.fn((hz: number) => hz),
  tuneBy: vi.fn(() => 0),
  getTuningStep: vi.fn(() => runtimeHarness.state.tuningStep),
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

vi.mock('../passband-geometry', () => ({
  canResizeFromRightEdge: vi.fn((mode: string) => mode.toUpperCase() !== 'LSB'),
  getFilterWidthFromRightEdgePx: passbandHarness.getFilterWidthFromRightEdgePx,
  getPassbandGeometry: vi.fn((
    _mode: string,
    passbandHz: number,
    _shiftHz: number,
    spanHz: number,
    widthPx: number,
    tunePx?: number,
  ) => {
    if (passbandHz <= 0 || spanHz <= 0 || widthPx <= 0) return null;
    const projectedWidth = passbandHz / 100;
    const center = tunePx ?? widthPx / 2;
    return {
      leftPx: center - projectedWidth / 2,
      rightPx: center + projectedWidth / 2,
      widthPx: projectedWidth,
    };
  }),
}));

vi.mock('../../../../components-v2/panels/filter-controls', () => ({
  deriveIfShift: vi.fn(() => 0),
}));

vi.mock('$lib/runtime/props/panel-props', async (importOriginal) => ({
  ...await importOriginal<typeof import('$lib/runtime/props/panel-props')>(),
  resolveFilterModeConfig: vi.fn((caps: { filterConfig?: Record<string, unknown> }, mode: string) =>
    caps.filterConfig?.[mode] ?? null),
}));

// ---------------------------------------------------------------------------
// Import component after mocks
// ---------------------------------------------------------------------------

import { WaterfallRenderer } from '$lib/renderers/waterfall-renderer';
import SpectrumPanel from '../SpectrumPanel.svelte';
import spectrumPanelSource from '../SpectrumPanel.svelte?raw';
import { ManagedAppTxHarness } from '$lib/runtime/tx-controller/__tests__/support/managed-app-tx-harness';
const authorityPluginSource = readFileSync(
  resolve(process.cwd(), 'scripts/radio-authority-eslint-plugin.mjs'),
  'utf8',
);
const authorityContractSource = readFileSync(
  resolve(process.cwd(), '../docs/internals/ui-radio-control-contract.toml'),
  'utf8',
);
import {
  deriveScopeIndicatorState,
  indicatorTone,
} from '../../../components-v2/layout/StatusBar.svelte';
import statusBarSource from '../../../components-v2/layout/StatusBar.svelte?raw';
import { IC7300_CAPABILITIES, IC7300_STATE } from '../../../lib/runtime/adapters/__tests__/fixtures/ic7300-profile';

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

let components: ReturnType<typeof mount>[] = [];
let txHarness: ManagedAppTxHarness;

type TestRule = Readonly<{
  kind: 'step';
  minHz: number;
  maxHz: number;
  stepHz: number;
}>;

type TestAuthority = Readonly<{
  providerGeneration: number;
  receiver: 0 | 1;
  frequencyHz: number | null;
  mode: string | null;
  filter: string | null;
  filterWidthHz: number | null;
  filterShape: number | null;
  ifShiftHz: number | null;
  pbtInnerHz: number | null;
  pbtOuterHz: number | null;
  dataMode: number | null;
  rule: TestRule | null;
  scopeControls: Readonly<{ mode: number }>;
  digest: string;
}>;

const defaultRule: TestRule = Object.freeze({
  kind: 'step', minHz: 100, maxHz: 5_000, stepHz: 100,
});

function authority(overrides: Partial<Omit<TestAuthority, 'digest'>> = {}): TestAuthority {
  const core = {
    providerGeneration: 17,
    receiver: 0 as const,
    frequencyHz: 14_050_250,
    mode: 'USB',
    filter: 'FIL1',
    filterWidthHz: 2_400,
    filterShape: 1,
    ifShiftHz: 0,
    pbtInnerHz: 0,
    pbtOuterHz: 0,
    dataMode: 0,
    rule: defaultRule,
    scopeControls: Object.freeze({ mode: 2 }),
    ...overrides,
  };
  return Object.freeze({ ...core, digest: JSON.stringify(core) });
}

function freshPbtOnlyIc7300State() {
  const captured = structuredClone(IC7300_STATE);
  const { ifShift: _rawIfShift, ...mainWithoutRawIfShift } = captured.main!;
  const fieldStatus = { ...captured.fieldStatus };
  delete fieldStatus['main.ifShift'];
  const fresh = (path: string) => ({
    observed: true, freshness: 'fresh', availability: 'available', storePath: path,
  });
  return {
    ...captured,
    main: {
      ...mainWithoutRawIfShift,
      mode: 'AM',
      filterWidth: 10_000,
      pbtInner: 128,
      pbtOuter: 128,
    },
    fieldStatus: {
      ...fieldStatus,
      'main.mode': fresh('main.mode'),
      'main.filterWidth': fresh('main.filterWidth'),
      'main.pbtInner': fresh('main.pbtInner'),
      'main.pbtOuter': fresh('main.pbtOuter'),
    },
  };
}

function emitFrame(overrides: Partial<TestScopeFrame> = {}): TestScopeFrame {
  const frame = Object.freeze({
    receiver: 0,
    mode: 0,
    startFreq: 14_000_000,
    endFreq: 14_100_000,
    pixels: new Uint8Array(475).fill(64),
    ...overrides,
  });
  runtimeHarness.state.capturedHardwareFrame?.(frame);
  flushSync();
  return frame;
}

function rect(element: Element, left = 0, width = 200): void {
  Object.defineProperty(element, 'getBoundingClientRect', {
    configurable: true,
    value: vi.fn(() => ({
      x: left, y: 0, left, top: 0, right: left + width, bottom: 100,
      width, height: 100, toJSON: () => ({}),
    })),
  });
}

function pointer(
  element: EventTarget,
  type: 'pointerdown' | 'pointermove' | 'pointerup' | 'pointercancel',
  pointerId: number,
  clientX: number,
  init: Partial<PointerEventInit> = {},
): void {
  element.dispatchEvent(new PointerEvent(type, {
    bubbles: true,
    cancelable: true,
    button: 0,
    pointerId,
    clientX,
    clientY: 20,
    ...init,
  }));
  flushSync();
}

function prepareGeometry(target: HTMLElement, width = 200): {
  spectrum: HTMLElement;
  waterfall: HTMLElement;
} {
  const spectrum = target.querySelector<HTMLElement>('.spectrum-area')!;
  const waterfall = target.querySelector<HTMLElement>('.waterfall-content')!;
  rect(spectrum, 0, width);
  rect(waterfall, 0, width);
  const canvas = waterfall.querySelector('canvas');
  if (canvas) rect(canvas, 0, width);
  return { spectrum, waterfall };
}

function mountPanel(props: Record<string, unknown> = {}) {
  const target = document.createElement('div');
  document.body.appendChild(target);
  const component = mount(SpectrumPanel, { target, props });
  flushSync();
  components.push(component);
  return target;
}

beforeEach(() => {
  components = [];
  txHarness = new ManagedAppTxHarness();
  appTxHostHarness.controller = txHarness.controller;
  runtimeHarness.state.capturedHardwareFrame = null;
  runtimeHarness.state.capturedDxMessage = null;
  runtimeHarness.state.mockScopeConnected = true;
  runtimeHarness.state.tuningStep = 1_000;
  runtimeHarness.state.nextLeaseId = 0;
  runtimeHarness.state.hardwareUnsubscribe = vi.fn();
  runtimeHarness.state.dxUnsubscribe = vi.fn();
  runtimeHarness.state.currentState = Object.freeze({ source: 'test-state' });
  runtimeHarness.state.currentCaps = Object.freeze({ source: 'test-capabilities' });
  authorityHarness.state.current = authority();
  authorityHarness.state.useProductionSelector = false;
  setFilterWidthLifecycle({});
  passbandHarness.rawWidth = 2_700;
  passbandHarness.getFilterWidthFromRightEdgePx.mockImplementation(() => passbandHarness.rawWidth);
  spectrumRendererHarness.lastOptions = null;
  vi.clearAllMocks();
});

afterEach(() => {
  components.forEach((c) => unmount(c));
  expect(txHarness.trace()).toEqual([]);
  expect(txHarness.listenerCount()).toBe(0);
  appTxHostHarness.controller = null;
  document.body.innerHTML = '';
});

// ---------------------------------------------------------------------------
// Tests
// ---------------------------------------------------------------------------

describe('SpectrumPanel component', () => {
  it('mounts without errors', () => {
    const target = mountPanel();
    expect(target.querySelector('.spectrum-panel')).not.toBeNull();
    expect(appTxHostHarness.controller).toBe(txHarness.controller);
    expect(txHarness.trace()).toEqual([]);
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

  it('ignores shared runtime frames while VIEW is OFF', () => {
    const target = mountPanel();
    target.querySelector<HTMLButtonElement>('.scope-demand-toggle')!.click();
    runtimeHarness.state.capturedHardwareFrame?.({
      receiver: 0,
      mode: 1,
      startFreq: 14_000_000,
      endFreq: 14_350_000,
      pixels: new Uint8Array(475).fill(64),
    });
    flushSync();
    expect(target.querySelector('.freq-axis')).toBeNull();
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

  it('wheel snaps an off-grid Observation and emits one typed VFO intent', () => {
    authorityHarness.state.current = authority({ frequencyHz: 14_074_250 });
    const target = mountPanel();
    const panel = target.querySelector('.spectrum-panel')!;
    const wheelEvent = new WheelEvent('wheel', {
      deltaY: -100,
      bubbles: true,
      cancelable: true,
    });
    panel.dispatchEvent(wheelEvent);
    expect(handlerHarness.vfo.onFreqChange).toHaveBeenCalledOnce();
    // MOR-1425 review B1: scroll-to-tune is a fixed one-step relative
    // gesture — it opts into the accumulate path explicitly.
    expect(handlerHarness.vfo.onFreqChange).toHaveBeenCalledWith(14_075_000, 0, 'step');
    expect(mockRuntime.send).not.toHaveBeenCalled();
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
    expect(spectrumPanelSource).not.toContain('runtime.send');
    expect(spectrumPanelSource).not.toContain('patchActiveReceiver');
    expect(spectrumPanelSource).not.toContain('patchReceiver');
    expect(spectrumPanelSource).not.toContain("stores/radio.svelte");
    expect(spectrumPanelSource).not.toContain('getCapabilities');
    expect(spectrumPanelSource).not.toContain('resolveFilterModeConfig');
    expect(spectrumPanelSource).not.toContain('tuneBy');
    expect(spectrumPanelSource).not.toContain('getDragInterval');
  });
});

describe('SpectrumPanel Observation authority and final-gesture intents', () => {
  it('binds the existing handler singletons once and uses the merged selector with state and caps', () => {
    mountPanel();
    // EiBiBrowser binds the same shared singleton independently; the Panel's
    // own source call is asserted exactly below.
    expect(handlerHarness.getVfoHandlers).toHaveBeenCalledTimes(2);
    expect(handlerHarness.getFilterHandlers).toHaveBeenCalledOnce();
    expect(authorityHarness.toSpectrumAuthority).toHaveBeenCalledWith(
      mockRuntime.state,
      mockRuntime.caps,
    );
    expect(spectrumPanelSource.match(/getVfoHandlers\(\)/g)).toHaveLength(1);
    expect(spectrumPanelSource.match(/getFilterHandlers\(\)/g)).toHaveLength(1);
    expect(spectrumPanelSource).toContain('toSpectrumAuthority(runtime.state, runtime.caps)');
    expect(spectrumPanelSource).toContain('let scopeMode = $derived(frameScopeMode)');
    expect(spectrumPanelSource).not.toContain('function toSpectrumAuthority');
    expect(spectrumPanelSource).not.toContain('function snapSpectrumFilterWidth');
  });

  it('routes an actual WaterfallCanvas tap exactly once without a parent duplicate', () => {
    const target = mountPanel();
    emitFrame();
    const { waterfall } = prepareGeometry(target);
    const canvas = waterfall.querySelector('canvas')!;
    pointer(canvas, 'pointerdown', 4, 150);
    pointer(canvas, 'pointerup', 4, 150);
    expect(handlerHarness.vfo.onFreqChange).toHaveBeenCalledOnce();
    expect(handlerHarness.vfo.onFreqChange).toHaveBeenCalledWith(14_050_000, 0);
    expect(mockRuntime.send).not.toHaveBeenCalled();
  });

  it('routes a DX tune exactly once through the typed VFO singleton', () => {
    const target = mountPanel();
    emitFrame();
    runtimeHarness.state.capturedDxMessage?.({
      type: 'dx_spot',
      spot: {
        spotter: 'N0CALL', freq: 14_075_410, call: 'K1ABC', comment: 'test',
        time_utc: '1200', timestamp: 1,
      },
    });
    flushSync();
    target.querySelector<HTMLButtonElement>('.dx-badge')!.click();
    expect(handlerHarness.vfo.onFreqChange).toHaveBeenCalledOnce();
    expect(handlerHarness.vfo.onFreqChange).toHaveBeenCalledWith(14_075_000, 0);
  });

  it('rejects tap and wheel when current Observation frequency is missing', () => {
    authorityHarness.state.current = authority({ frequencyHz: null });
    const target = mountPanel();
    emitFrame();
    const { waterfall } = prepareGeometry(target);
    const canvas = waterfall.querySelector('canvas')!;
    pointer(canvas, 'pointerdown', 5, 150);
    pointer(canvas, 'pointerup', 5, 150);
    target.querySelector('.spectrum-panel')!.dispatchEvent(new WheelEvent('wheel', {
      deltaY: -100, bubbles: true, cancelable: true,
    }));
    expect(handlerHarness.vfo.onFreqChange).not.toHaveBeenCalled();
  });

  it('keeps drag moves local and emits one final snapped VFO intent on stable pointer-up', () => {
    const target = mountPanel();
    emitFrame();
    const { spectrum } = prepareGeometry(target);
    pointer(spectrum, 'pointerdown', 7, 100);
    pointer(spectrum, 'pointermove', 7, 120);
    expect(handlerHarness.vfo.onFreqChange).not.toHaveBeenCalled();
    pointer(spectrum, 'pointerup', 7, 120);
    expect(handlerHarness.vfo.onFreqChange).toHaveBeenCalledOnce();
    expect(handlerHarness.vfo.onFreqChange).toHaveBeenCalledWith(14_040_000, 0);
  });

  it('emits zero for below-threshold and no-change drags', () => {
    const target = mountPanel();
    emitFrame();
    const { spectrum } = prepareGeometry(target);
    pointer(spectrum, 'pointerdown', 8, 100);
    pointer(spectrum, 'pointermove', 8, 104);
    pointer(spectrum, 'pointerup', 8, 104);
    expect(handlerHarness.vfo.onFreqChange).not.toHaveBeenCalled();

    authorityHarness.state.current = authority({ frequencyHz: 14_050_000 });
    rect(spectrum, 0, 100_000);
    pointer(spectrum, 'pointerdown', 9, 100);
    pointer(spectrum, 'pointermove', 9, 106);
    pointer(spectrum, 'pointerup', 9, 106);
    expect(handlerHarness.vfo.onFreqChange).not.toHaveBeenCalled();
  });

  it('matching drag cancel emits zero and never finalizes', () => {
    const target = mountPanel();
    emitFrame();
    const { spectrum } = prepareGeometry(target);
    pointer(spectrum, 'pointerdown', 10, 100);
    pointer(spectrum, 'pointermove', 10, 120);
    pointer(spectrum, 'pointercancel', 10, 120);
    expect(handlerHarness.vfo.onFreqChange).not.toHaveBeenCalled();
  });

  it('wrong-pointer up emits zero and preserves the real drag completion', () => {
    const target = mountPanel();
    emitFrame();
    const { spectrum } = prepareGeometry(target);
    pointer(spectrum, 'pointerdown', 11, 100);
    pointer(spectrum, 'pointermove', 11, 120);
    pointer(spectrum, 'pointerup', 12, 120);
    expect(handlerHarness.vfo.onFreqChange).not.toHaveBeenCalled();
    pointer(spectrum, 'pointerup', 11, 120);
    expect(handlerHarness.vfo.onFreqChange).toHaveBeenCalledOnce();
  });

  it('keeps resize moves local and commits one snapped width with captured identity', () => {
    const captured = authority();
    authorityHarness.state.current = captured;
    const target = mountPanel();
    emitFrame();
    const { waterfall } = prepareGeometry(target);
    const zone = waterfall.querySelector<HTMLButtonElement>('.passband-resize-zone')!;
    expect(zone).not.toBeNull();
    pointer(zone, 'pointerdown', 21, 100);
    pointer(waterfall, 'pointermove', 21, 130);
    expect(handlerHarness.filter.onFilterWidthCommit).not.toHaveBeenCalled();
    pointer(waterfall, 'pointerup', 21, 130);
    expect(authorityHarness.snapSpectrumFilterWidth).toHaveBeenCalledWith(2_700, captured.rule);
    expect(handlerHarness.filter.onFilterWidthCommit).toHaveBeenCalledOnce();
    expect(handlerHarness.filter.onFilterWidthCommit).toHaveBeenCalledWith(2_700, 0, 17);
  });

  it('projects each resize move immediately and keeps only the busy lifecycle target after release (MOR-1649)', () => {
    const target = mountPanel();
    emitFrame();
    const { waterfall } = prepareGeometry(target);
    const zone = waterfall.querySelector<HTMLButtonElement>('.passband-resize-zone')!;
    const overlay = () => waterfall.querySelector<HTMLElement>('.passband-overlay')!;

    expect(overlay().style.width).toBe('24%');
    pointer(zone, 'pointerdown', 42, 100);
    passbandHarness.rawWidth = 2_500;
    pointer(waterfall, 'pointermove', 42, 120);
    expect(overlay().style.width).toBe('25%');
    expect(handlerHarness.filter.onFilterWidthCommit).not.toHaveBeenCalled();

    passbandHarness.rawWidth = 2_700;
    pointer(waterfall, 'pointermove', 42, 130);
    expect(overlay().style.width).toBe('27%');
    expect(handlerHarness.filter.onFilterWidthCommit).not.toHaveBeenCalled();

    setFilterWidthLifecycle(pendingFilterWidthLifecycle(2_700));
    flushSync();
    pointer(waterfall, 'pointerup', 42, 130);
    expect(handlerHarness.filter.onFilterWidthCommit).toHaveBeenCalledOnce();
    expect(overlay().style.width).toBe('27%');

    authorityHarness.state.current = authority({ filterWidthHz: 2_700 });
    refreshSpectrumAuthority();
    setFilterWidthLifecycle({ confirmed: 2_700, phase: 'confirmed', presentation: {
      lifecycleId: 'width-2700', transitionId: 'width-2700-confirmed', receiver: 0,
      sessionEpoch: 17, target: 2_700, status: 'confirmed',
    } });
    flushSync();
    expect(overlay().style.width).toBe('27%');

    authorityHarness.state.current = authority();
    refreshSpectrumAuthority();
    for (const outcome of ['failed', 'timed-out', 'cancelled'] as const) {
      setFilterWidthLifecycle({
        target: null,
        phase: 'idle',
        busy: false,
        outcome: { phase: outcome },
        presentation: {
          lifecycleId: `width-2700-${outcome}`,
          transitionId: `width-2700-${outcome}`,
          receiver: 0,
          sessionEpoch: 17,
          target: 2_700,
          status: outcome,
        },
      });
      flushSync();
      expect(overlay().style.width).toBe('24%');
    }
  });

  it('does not project a busy width lifecycle for another receiver', () => {
    const target = mountPanel();
    emitFrame();
    const { waterfall } = prepareGeometry(target);
    setFilterWidthLifecycle(pendingFilterWidthLifecycle(2_700, 1));
    flushSync();

    expect(waterfall.querySelector<HTMLElement>('.passband-overlay')?.style.width).toBe('24%');
  });

  it('abandons an active resize projection after receiver authority drifts', () => {
    const target = mountPanel();
    emitFrame();
    const { waterfall } = prepareGeometry(target);
    const zone = waterfall.querySelector<HTMLButtonElement>('.passband-resize-zone')!;
    const overlay = () => waterfall.querySelector<HTMLElement>('.passband-overlay')!;

    pointer(zone, 'pointerdown', 43, 100);
    passbandHarness.rawWidth = 2_700;
    pointer(waterfall, 'pointermove', 43, 130);
    expect(overlay().style.width).toBe('27%');

    authorityHarness.state.current = authority({ receiver: 1 });
    refreshSpectrumAuthority();
    flushSync();
    expect(overlay().style.width).toBe('24%');

    pointer(waterfall, 'pointerup', 43, 130);
    expect(handlerHarness.filter.onFilterWidthCommit).not.toHaveBeenCalled();
  });

  it('renders and resizes a fresh PBT-only IC-7300 passband via the production selector (MOR-1649)', () => {
    runtimeHarness.state.currentState = freshPbtOnlyIc7300State();
    runtimeHarness.state.currentCaps = IC7300_CAPABILITIES;
    authorityHarness.state.useProductionSelector = true;
    const target = mountPanel();
    emitFrame();
    const { waterfall } = prepareGeometry(target);
    const zone = waterfall.querySelector<HTMLButtonElement>('.passband-resize-zone');

    expect(target.querySelector('.passband-overlay')).not.toBeNull();
    expect(zone).not.toBeNull();
    pointer(zone!, 'pointerdown', 41, 100);
    pointer(waterfall, 'pointermove', 41, 130);
    pointer(waterfall, 'pointerup', 41, 130);
    expect(handlerHarness.filter.onFilterWidthCommit).toHaveBeenCalledWith(2_800, 0, 1);
  });

  it('normalizes fixed-frame resize X around the captured carrier rather than sample center', () => {
    authorityHarness.state.current = authority({ frequencyHz: 14_025_000 });
    const target = mountPanel();
    emitFrame({ mode: 1 });
    const { waterfall } = prepareGeometry(target);
    const zone = waterfall.querySelector<HTMLButtonElement>('.passband-resize-zone')!;
    pointer(zone, 'pointerdown', 22, 100);
    pointer(waterfall, 'pointermove', 22, 100);
    pointer(waterfall, 'pointerup', 22, 100);
    expect(passbandHarness.getFilterWidthFromRightEdgePx).toHaveBeenCalledWith(
      'USB', 0, 100_000, 200, 150, 5_000,
    );
  });

  it('matching resize cancel emits zero and a wrong pointer preserves the real completion', () => {
    const target = mountPanel();
    emitFrame();
    const { waterfall } = prepareGeometry(target);
    const zone = waterfall.querySelector<HTMLButtonElement>('.passband-resize-zone')!;
    pointer(zone, 'pointerdown', 23, 100);
    pointer(waterfall, 'pointermove', 23, 130);
    pointer(waterfall, 'pointercancel', 23, 130);
    expect(handlerHarness.filter.onFilterWidthCommit).not.toHaveBeenCalled();

    pointer(zone, 'pointerdown', 24, 100);
    pointer(waterfall, 'pointermove', 24, 130);
    pointer(waterfall, 'pointerup', 25, 130);
    expect(handlerHarness.filter.onFilterWidthCommit).not.toHaveBeenCalled();
    pointer(waterfall, 'pointerup', 24, 130);
    expect(handlerHarness.filter.onFilterWidthCommit).toHaveBeenCalledOnce();
  });

  it('rejects resize completion after authority or frame-geometry drift', () => {
    const target = mountPanel();
    emitFrame();
    const { waterfall } = prepareGeometry(target);
    const zone = waterfall.querySelector<HTMLButtonElement>('.passband-resize-zone')!;
    pointer(zone, 'pointerdown', 26, 100);
    pointer(waterfall, 'pointermove', 26, 130);
    authorityHarness.state.current = authority({ filterShape: 2 });
    pointer(waterfall, 'pointerup', 26, 130);
    expect(handlerHarness.filter.onFilterWidthCommit).not.toHaveBeenCalled();

    authorityHarness.state.current = authority();
    pointer(zone, 'pointerdown', 27, 100);
    pointer(waterfall, 'pointermove', 27, 130);
    emitFrame({ endFreq: 14_101_000 });
    pointer(waterfall, 'pointerup', 27, 130);
    expect(handlerHarness.filter.onFilterWidthCommit).not.toHaveBeenCalled();
  });

  it('rejects a resize candidate equal to the captured observed width', () => {
    passbandHarness.rawWidth = 2_400;
    const target = mountPanel();
    emitFrame();
    const { waterfall } = prepareGeometry(target);
    const zone = waterfall.querySelector<HTMLButtonElement>('.passband-resize-zone')!;
    pointer(zone, 'pointerdown', 28, 100);
    pointer(waterfall, 'pointermove', 28, 130);
    pointer(waterfall, 'pointerup', 28, 130);
    expect(handlerHarness.filter.onFilterWidthCommit).not.toHaveBeenCalled();
  });

  it.each([
    ['provider generation', { providerGeneration: 18 }],
    ['physical receiver', { receiver: 1 as const }],
    ['frequency', { frequencyHz: 14_050_300 }],
  ])('rejects final drag after %s drift', (_label, overrides) => {
    const target = mountPanel();
    emitFrame();
    const { spectrum } = prepareGeometry(target);
    pointer(spectrum, 'pointerdown', 30, 100);
    pointer(spectrum, 'pointermove', 30, 120);
    authorityHarness.state.current = authority(overrides);
    pointer(spectrum, 'pointerup', 30, 120);
    expect(handlerHarness.vfo.onFreqChange).not.toHaveBeenCalled();
  });

  // MOR-1497: plain drag-to-pan only moves frequency, so drift in passband
  // fields it never reads must NOT abort an otherwise-stable pan. Before the
  // fix these all shared one full-digest recheck with passband-resize, which
  // made irrelevant field churn spuriously cancel a completed drag.
  it.each([
    ['mode', { mode: 'LSB' }],
    ['filter', { filter: 'FIL2' }],
    ['width', { filterWidthHz: 2_500 }],
    ['IF shift', { ifShiftHz: 50 }],
    ['PBT inner', { pbtInnerHz: 50 }],
    ['PBT outer', { pbtOuterHz: 50 }],
    ['filter shape', { filterShape: 2 }],
    ['DATA', { dataMode: 1 }],
    ['rule', { rule: Object.freeze({ kind: 'step' as const, minHz: 200, maxHz: 5_000, stepHz: 100 }) }],
    ['scope controls', { scopeControls: Object.freeze({ mode: 3 }) }],
  ])('completes final drag despite %s drift, which plain panning does not depend on (MOR-1497)', (_label, overrides) => {
    const target = mountPanel();
    emitFrame();
    const { spectrum } = prepareGeometry(target);
    pointer(spectrum, 'pointerdown', 33, 100);
    pointer(spectrum, 'pointermove', 33, 120);
    authorityHarness.state.current = authority(overrides);
    pointer(spectrum, 'pointerup', 33, 120);
    expect(handlerHarness.vfo.onFreqChange).toHaveBeenCalledOnce();
    expect(handlerHarness.vfo.onFreqChange).toHaveBeenCalledWith(14_040_000, 0);
  });

  // MOR-1497 regression: drag-to-pan was gated on completeGestureAuthority(false),
  // which required mode/filterWidthHz/ifShiftHz to all be non-null. On Icom
  // radios ifShiftHz is structurally unobservable (PBT-only, no IF-shift
  // command) so the gate returned null forever and every drag silently
  // no-op'd behind a grab cursor that promised it would work.
  it('completes a plain drag when filter width and IF shift are unobserved (MOR-1497)', () => {
    authorityHarness.state.current = authority({ filterWidthHz: null, ifShiftHz: null });
    const target = mountPanel();
    emitFrame();
    const { spectrum } = prepareGeometry(target);
    pointer(spectrum, 'pointerdown', 40, 100);
    pointer(spectrum, 'pointermove', 40, 120);
    expect(handlerHarness.vfo.onFreqChange).not.toHaveBeenCalled();
    pointer(spectrum, 'pointerup', 40, 120);
    expect(handlerHarness.vfo.onFreqChange).toHaveBeenCalledOnce();
    expect(handlerHarness.vfo.onFreqChange).toHaveBeenCalledWith(14_040_000, 0);
  });

  // MOR-1497: the grab cursor is decorative CSS unless it reflects whether a
  // drag is actually possible — withhold it exactly when the drag-start gate
  // (frequency authority AND usable sample geometry) would refuse the gesture.
  it('withholds the draggable cursor class until both frequency authority and scope geometry exist (MOR-1497)', () => {
    authorityHarness.state.current = authority({ frequencyHz: null });
    const withoutFreq = mountPanel();
    expect(withoutFreq.querySelector('.spectrum-area')?.classList.contains('draggable')).toBe(false);
    expect(withoutFreq.querySelector('.waterfall-content')?.classList.contains('draggable')).toBe(false);

    authorityHarness.state.current = authority({ filterWidthHz: null, ifShiftHz: null });
    const withFreqOnly = mountPanel();
    // Before the first scope frame the sample geometry is unusable
    // (endFreq <= startFreq) and handleDragStart would bail — the cursor
    // must not promise a drag that cannot happen.
    expect(withFreqOnly.querySelector('.spectrum-area')?.classList.contains('draggable')).toBe(false);
    expect(withFreqOnly.querySelector('.waterfall-content')?.classList.contains('draggable')).toBe(false);

    emitFrame();
    expect(withFreqOnly.querySelector('.spectrum-area')?.classList.contains('draggable')).toBe(true);
    expect(withFreqOnly.querySelector('.waterfall-content')?.classList.contains('draggable')).toBe(true);
  });

  it.each([
    ['frame mode', { mode: 1 }],
    ['start edge', { startFreq: 13_999_000 }],
    ['end edge', { endFreq: 14_101_000 }],
  ])('rejects final drag after %s drift', (_label, frameOverride) => {
    const target = mountPanel();
    emitFrame();
    const { spectrum } = prepareGeometry(target);
    pointer(spectrum, 'pointerdown', 31, 100);
    pointer(spectrum, 'pointermove', 31, 120);
    emitFrame(frameOverride);
    pointer(spectrum, 'pointerup', 31, 120);
    expect(handlerHarness.vfo.onFreqChange).not.toHaveBeenCalled();
  });

  it('rejects final drag after measured element-width drift', () => {
    const target = mountPanel();
    emitFrame();
    const { spectrum } = prepareGeometry(target);
    pointer(spectrum, 'pointerdown', 32, 100);
    pointer(spectrum, 'pointermove', 32, 120);
    rect(spectrum, 0, 240);
    pointer(spectrum, 'pointerup', 32, 120);
    expect(handlerHarness.vfo.onFreqChange).not.toHaveBeenCalled();
  });

  it('hides confirmed carrier/passband for missing Observation while retaining sample plane pixels', async () => {
    authorityHarness.state.current = null;
    const target = mountPanel();
    emitFrame();
    expect(target.querySelector('.freq-axis')).not.toBeNull();
    expect(target.querySelector('.tune-line')).toBeNull();
    expect(target.querySelector('.passband-overlay')).toBeNull();
    expect(target.querySelector('.passband-resize-zone')).toBeNull();
    await vi.waitFor(() => expect(spectrumRendererHarness.render).toHaveBeenCalled());
    expect(spectrumRendererHarness.lastOptions.spanHz).toBe(0);
    expect(spectrumRendererHarness.render.mock.calls.at(-1)?.[1]).toHaveLength(475);
  });

  it('hides confirmed carrier/passband when Observation is outside fixed sample edges', async () => {
    authorityHarness.state.current = authority({ frequencyHz: 14_200_000 });
    const target = mountPanel();
    emitFrame({ mode: 1 });
    expect(target.querySelector('.tune-line')).toBeNull();
    expect(target.querySelector('.passband-overlay')).toBeNull();
    await vi.waitFor(() => expect(spectrumRendererHarness.render).toHaveBeenCalled());
    expect(spectrumRendererHarness.lastOptions.spanHz).toBe(0);
  });

  it('keeps frame mode and pixel geometry authoritative over canonical scope-control metadata', async () => {
    authorityHarness.state.current = authority({
      frequencyHz: 14_025_000,
      scopeControls: Object.freeze({ mode: 3 }),
    });
    const target = mountPanel();
    emitFrame({ mode: 0 });
    expect(target.querySelector<HTMLElement>('.tune-line')?.style.left).toBe('50%');
    await vi.waitFor(() => expect(spectrumRendererHarness.render).toHaveBeenCalled());
    expect(spectrumRendererHarness.lastOptions.scopeMode).toBe(0);
    expect(spectrumRendererHarness.render.mock.calls.at(-1)?.[1]).toHaveLength(475);
  });

  it.each([
    ['mode', { mode: null }],
    ['width', { filterWidthHz: null }],
    ['IF shift', { ifShiftHz: null }],
  ])('never defaults missing %s into a confirmed passband', (_label, overrides) => {
    authorityHarness.state.current = authority(overrides);
    const target = mountPanel();
    emitFrame();
    expect(target.querySelector('.passband-overlay')).toBeNull();
    expect(target.querySelector('.passband-resize-zone')).toBeNull();
  });

  it('allows a confirmed overlay but no resize permission when the rule is missing', () => {
    authorityHarness.state.current = authority({ rule: null });
    const target = mountPanel();
    emitFrame();
    expect(target.querySelector('.passband-overlay')).not.toBeNull();
    expect(target.querySelector('.passband-resize-zone')).toBeNull();
  });

  it('rejects fixed resize capture when confirmed frequency is outside the sample plane', () => {
    authorityHarness.state.current = authority({ frequencyHz: 14_200_000 });
    const target = mountPanel();
    emitFrame({ mode: 1 });
    expect(target.querySelector('.passband-resize-zone')).toBeNull();
    expect(handlerHarness.filter.onFilterWidthCommit).not.toHaveBeenCalled();
  });

  it('removes exactly the two Panel legacy exceptions while retaining sample ownership', () => {
    expect(authorityPluginSource.match(/src\/components\/spectrum\/SpectrumPanel\.svelte/g))
      .toHaveLength(1);
    expect(authorityContractSource.match(/src\/components\/spectrum\/SpectrumPanel\.svelte/g))
      .toHaveLength(2);
    expect(authorityPluginSource).toContain('const SCOPE_METADATA_OWNERS');
    expect(authorityContractSource).toContain('id = "spectrum_payload"');
    expect(authorityContractSource).toContain('id = "scope_metadata_owner"');
  });
});

// MOR-1369 (v3-rework S6b-1) — `hideScopeControls` is a pure pass-through to
// the real (unmocked) SpectrumToolbar; no logic of its own lives here. One
// direct DOM check per direction is enough to prove the wire is connected —
// SpectrumToolbar.component.test.ts owns the exhaustive fact-backed/
// view-option split.
describe('SpectrumPanel hideScopeControls pass-through (MOR-1369, S6b-1)', () => {
  it('omitting the prop keeps the toolbar fact-backed half reachable (default false)', () => {
    const target = mountPanel();
    const holdBtn = Array.from(target.querySelectorAll<HTMLButtonElement>('.toolbar-btn'))
      .find((b) => b.textContent?.trim() === 'HOLD');
    expect(holdBtn).toBeDefined();
  });

  it('forwards hideScopeControls=true to SpectrumToolbar, hiding the fact-backed half', () => {
    const target = mountPanel({ hideScopeControls: true });
    const buttons = Array.from(target.querySelectorAll<HTMLButtonElement>('.toolbar-btn'));
    const labels = buttons.map((b) => b.textContent?.trim());
    expect(labels).not.toContain('HOLD');
    // the view-options half is unaffected by the forwarded prop
    expect(labels).toContain('AVG');
    expect(labels).toContain('PEAK');
  });
});

// MOR-1456 — the "Go to Waterfall" (`g w`) keyboard shortcut. Neither the
// spectrum `<canvas>` nor the waterfall `<canvas>` (`WaterfallCanvas.svelte`)
// is natively focusable, and `ScopeDisplaySurface.svelte` (the semantic
// scope-display readout) is a bare readout with ZERO focusable elements by
// construction (MOR-1069) — the panel root's own `data-waterfall`/
// `tabindex="-1"` is the one stable anchor `focus_target` can resolve to.
// Lives here, not alongside the other `focus_target` shortcuts in
// `panel-commands/__tests__/focus-target-resolution.component.test.ts`,
// because only THIS file already carries the canvas/ResizeObserver/rAf
// mocking a real `SpectrumPanel` mount needs.
describe('"waterfall" focus_target shortcut resolves to the real SpectrumPanel root (MOR-1456)', () => {
  it('"g w" focuses the spectrum panel root', async () => {
    const { makeKeyboardHandlers } = await import('$lib/runtime/commands/panel-commands');
    const target = mountPanel();
    const root = target.querySelector('[data-waterfall]');
    expect(root).not.toBeNull();

    makeKeyboardHandlers().dispatch({ action: 'focus_target', params: { target: 'waterfall' } });

    expect(document.activeElement).toBe(root);
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
    ['power-off precedes inactive facts', scopeStatus({ source: null, demand: 0 }), true, 'disconnected'],
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
    ['non-streaming lifecycle is not green', scopeStatus({ lifecycle: 'inactive' }), false, 'inactive'],
  ] as const)('%s maps to %s', (_label, status, poweredOff, expected) => {
    expect(deriveScopeIndicatorState(status, poweredOff)).toBe(expected);
  });

  it.each([
    ['inactive', 'neutral'],
    ['starting', 'yellow'],
    ['connecting', 'yellow'],
    ['waiting', 'yellow'],
    ['reconnecting', 'yellow'],
    ['failed', 'red'],
    ['disconnected', 'red'],
    ['connected', 'green'],
  ] as const)('%s uses the %s tone', (state, expected) => {
    expect(indicatorTone(state)).toBe(expected);
  });
});


describe('opaque semantic scope snippet forwarding (MOR-2358)', () => {
  const scopeControls = createRawSnippet(() => ({ render: () => '<div data-testid="hosted-scope-probe">Semantic scope probe</div>' }));
  it('renders the opaque snippet once inside the hardware toolbar', () => {
    const target = mountPanel({ hideScopeControls: true, scopeControls });
    expect(target.querySelectorAll('[data-testid="hosted-scope-probe"]')).toHaveLength(1);
    expect(target.querySelector('[data-testid="hosted-scope-probe"]')?.closest('.spectrum-toolbar')).not.toBeNull();
    expect(target.querySelector('.settings-group')).toBeNull();
  });
  it('keeps audio FFT source label and Viewer without rendering the hardware snippet', () => {
    runtimeHarness.state.currentCaps = { scopeSource: 'audio_fft' };
    const target = mountPanel({ hideScopeControls: true, scopeControls });
    expect(target.querySelector('[data-testid="hosted-scope-probe"]')).toBeNull();
    expect(target.querySelector('.spectrum-toolbar')).toBeNull();
    expect(target.querySelector('.audio-source-label')?.textContent).toContain('Audio FFT · AF');
    const viewer = target.querySelector<HTMLButtonElement>('[aria-label="Scope viewer"]')!;
    expect(viewer.textContent).toContain('Viewer ON'); viewer.click(); flushSync(); expect(viewer.textContent).toContain('Viewer OFF');
    expect(mockRuntime.scope.subscribeHardware).not.toHaveBeenCalled();
  });
});

describe('managed scope projection (MOR-2367)', () => {
  function projection(state: 'current' | 'stale' | 'unknown' | 'unsupported' = 'current'): ScopeDisplayProjection {
    return Object.freeze({
      frame: Object.freeze({ source: 'hardware', receiver: 'MAIN', freshness: 'fresh',
        startHz: 14_000_000, endHz: 14_100_000, normalizedBins: Object.freeze([0, 0.5, 1]) }),
      frameMode: 0,
      passband: state === 'current' || state === 'stale'
        ? Object.freeze({ state, tuple: Object.freeze({ frequencyHz: 14_050_250, mode: 'USB',
          widthHz: 2_400, shiftHz: 0, frameMode: 0, startHz: 14_000_000, endHz: 14_100_000 }) })
        : { state, reason: 'not-observed' },
    });
  }
  function managed(initial: ReturnType<typeof projection> | null | undefined) {
    const props = new SvelteMap<string, unknown>([['projection', initial], ['demanded', true]]);
    const onScopeDemandChange = vi.fn((enabled: boolean) => props.set('demanded', enabled));
    const target = mountPanel({
      get scopeProjection() { return props.get('projection'); },
      get scopeDemanded() { return props.get('demanded'); }, onScopeDemandChange,
    });
    return { target, props, onScopeDemandChange };
  }
  function cleared(target: HTMLElement) {
    for (const selector of ['canvas', '.freq-axis', '.tune-line', '.passband-overlay', '.passband-resize-zone', '.draggable']) {
      expect(target.querySelector(selector), selector).toBeNull();
    }
  }
  it.each([null, 'current'] as const)('never acquires or subscribes raw scope for managed %s', (kind) => {
    const { target } = managed(kind === null ? null : projection());
    expect(mockRuntime.acquireHardwareScope).not.toHaveBeenCalled();
    expect(mockRuntime.scope.subscribeHardware).not.toHaveBeenCalled();
    emitFrame();
    if (kind === null) cleared(target);
    else expect(target.querySelector('.passband-overlay')).not.toBeNull();
    expect(handlerHarness.vfo.onFreqChange).not.toHaveBeenCalled();
  });
  it('keeps omitted and explicit undefined projections on the legacy acquisition path', () => {
    mountPanel(); mountPanel({ scopeProjection: undefined });
    expect(mockRuntime.acquireHardwareScope).toHaveBeenCalledTimes(2);
    expect(mockRuntime.scope.subscribeHardware).toHaveBeenCalledTimes(2);
  });
  it('clears after legacy-to-managed null and rejects a captured late raw callback', () => {
    const { target, props } = managed(undefined);
    emitFrame(); const oldCallback = runtimeHarness.state.capturedHardwareFrame!;
    expect(target.querySelector('.passband-overlay')).not.toBeNull();
    props.set('projection', null); flushSync(); cleared(target);
    oldCallback({ receiver: 0, mode: 0, startFreq: 14_000_000, endFreq: 14_100_000, pixels: new Uint8Array([90, 90]) });
    flushSync(); cleared(target);
    expect(runtimeHarness.state.hardwareUnsubscribe).toHaveBeenCalledOnce();
    expect(mockRuntime.releaseHardwareScope).toHaveBeenCalledOnce();
  });
  it.each(['null', 'off'] as const)('clears pixels, coordinates and active captures on %s while shared health stays true', async (boundary) => {
    const shared = mockRuntime.acquireHardwareScope();
    const { target, props, onScopeDemandChange } = managed(projection());
    await vi.waitFor(() => expect(spectrumRendererHarness.render).toHaveBeenCalled());
    expect([...spectrumRendererHarness.render.mock.calls.at(-1)![1]]).toEqual([0, 128, 255]);
    const { waterfall } = prepareGeometry(target);
    const zone = waterfall.querySelector('.passband-resize-zone')!;
    pointer(zone, 'pointerdown', 80, 100); pointer(waterfall, 'pointermove', 80, 130);
    const oldCanvas = target.querySelector('canvas');
    if (boundary === 'null') props.set('projection', null);
    else target.querySelector<HTMLButtonElement>('.scope-demand-toggle')!.click();
    flushSync(); cleared(target);
    expect(oldCanvas?.isConnected).toBe(false);
    expect(runtimeHarness.state.mockScopeConnected).toBe(true);
    expect(mockRuntime.acquireHardwareScope).toHaveBeenCalledOnce();
    expect(mockRuntime.releaseHardwareScope).not.toHaveBeenCalled();
    pointer(window, 'pointerup', 80, 130);
    const { spectrum } = prepareGeometry(target);
    pointer(spectrum, 'pointerdown', 81, 100); pointer(spectrum, 'pointermove', 81, 130); pointer(spectrum, 'pointerup', 81, 130);
    target.querySelector('.spectrum-panel')!.dispatchEvent(new WheelEvent('wheel', { bubbles: true, deltaY: 1 }));
    emitFrame(); cleared(target);
    expect(handlerHarness.vfo.onFreqChange).not.toHaveBeenCalled();
    expect(handlerHarness.filter.onFilterWidthCommit).not.toHaveBeenCalled();
    if (boundary === 'off') {
      expect(onScopeDemandChange).toHaveBeenCalledWith(false);
      props.set('projection', null); flushSync();
      target.querySelector<HTMLButtonElement>('.scope-demand-toggle')!.click(); flushSync();
      expect(onScopeDemandChange).toHaveBeenLastCalledWith(true); cleared(target);
    }
    props.set('projection', projection()); flushSync();
    expect(target.querySelector('.passband-overlay')).not.toBeNull();
    mockRuntime.releaseHardwareScope(shared);
  });
  it.each(['unknown', 'unsupported'] as const)('keeps the managed sample plane without raw RF fallback for %s passband', (state) => {
    const { target } = managed(projection(state));
    expect(target.querySelector('canvas')).not.toBeNull();
    expect(target.querySelector('.freq-axis')).not.toBeNull();
    expect(target.querySelector('.tune-line')).toBeNull();
    expect(target.querySelector('.passband-overlay')).toBeNull();
    expect(target.querySelector('.passband-resize-zone')).toBeNull();
  });
  it('retains whole stale geometry with a non-color cue while strict fresh frequency still pans', () => {
    const { target, props } = managed(projection()); const original = target.querySelector('.passband-overlay')!.getAttribute('style');
    authorityHarness.state.current = authority({ filterWidthHz: null, ifShiftHz: null }); refreshSpectrumAuthority();
    props.set('projection', projection('stale')); flushSync();
    expect(target.querySelector('.passband-overlay')!.getAttribute('style')).toBe(original);
    const cue = target.querySelector('.passband-freshness')!;
    expect(cue.textContent?.trim()).not.toBe(''); expect(cue.getAttribute('aria-label')).toBeTruthy();
    expect(target.querySelector('.passband-resize-zone')).toBeNull();
    const { spectrum } = prepareGeometry(target);
    pointer(spectrum, 'pointerdown', 82, 100); pointer(spectrum, 'pointermove', 82, 120); pointer(spectrum, 'pointerup', 82, 120);
    expect(handlerHarness.vfo.onFreqChange).toHaveBeenCalledOnce();
    expect(handlerHarness.vfo.onFreqChange).toHaveBeenCalledWith(14_040_000, 0);
    authorityHarness.state.current = authority({ frequencyHz: null }); refreshSpectrumAuthority(); flushSync();
    pointer(spectrum, 'pointerdown', 83, 100); pointer(spectrum, 'pointermove', 83, 120); pointer(spectrum, 'pointerup', 83, 120);
    expect(handlerHarness.vfo.onFreqChange).toHaveBeenCalledOnce();
    props.set('projection', projection()); flushSync();
    expect(target.querySelector('.passband-freshness')!.textContent?.trim()).toBe('');
  });
  it('does not grant stale resize even if a separate strict snapshot is current', () => {
    const { target, props } = managed(projection()); const { waterfall } = prepareGeometry(target);
    const zone = waterfall.querySelector('.passband-resize-zone')!;
    pointer(zone, 'pointerdown', 84, 100); pointer(waterfall, 'pointermove', 84, 130);
    props.set('projection', projection('stale')); flushSync();
    pointer(window, 'pointerup', 84, 130);
    expect(target.querySelector('.passband-resize-zone')).toBeNull();
    expect(handlerHarness.filter.onFilterWidthCommit).not.toHaveBeenCalled();
  });
  it('pushes normalized managed frames through existing waterfall registrations on mount and recovery', () => {
    const push = vi.spyOn(WaterfallRenderer.prototype, 'pushRow');
    try {
      const { target, props } = managed(projection());
      expect([...push.mock.calls.at(-1)![0]]).toEqual([0, 128, 255]);
      const firstCanvas = target.querySelector('.waterfall-content canvas');
      props.set('projection', null); flushSync(); const afterClear = push.mock.calls.length;
      emitFrame(); expect(push).toHaveBeenCalledTimes(afterClear);
      props.set('projection', projection()); flushSync();
      expect(target.querySelector('.waterfall-content canvas')).not.toBe(firstCanvas);
      expect([...push.mock.calls.at(-1)![0]]).toEqual([0, 128, 255]);
      expect(push.mock.calls.length).toBeGreaterThan(afterClear);
    } finally { push.mockRestore(); }
  });
  it('preserves raw waterfall push delivery in legacy mode', () => {
    const push = vi.spyOn(WaterfallRenderer.prototype, 'pushRow');
    try {
      mountPanel(); const frame = emitFrame();
      expect(push).toHaveBeenCalledWith(frame.pixels);
    } finally { push.mockRestore(); }
  });
  it.each(['tap', 'pan'] as const)('cancels an in-flight %s and DX coordinates when managed projection becomes null', (gesture) => {
    const { target, props } = managed(projection()); const { spectrum, waterfall } = prepareGeometry(target);
    const surface = gesture === 'tap' ? waterfall.querySelector('canvas')! : spectrum;
    const spot = { spotter: 'N0CALL', freq: 14_075_410, call: 'K1ABC', comment: 'test', time_utc: '1200', timestamp: 1 };
    runtimeHarness.state.capturedDxMessage?.({ type: 'dx_spot', spot }); flushSync();
    expect(target.querySelector('.dx-badge')).not.toBeNull();
    pointer(surface, 'pointerdown', 86, 100);
    if (gesture === 'pan') pointer(surface, 'pointermove', 86, 120);
    props.set('projection', null); flushSync();
    pointer(surface, 'pointerup', 86, 120);
    runtimeHarness.state.capturedDxMessage?.({ type: 'dx_spot', spot }); flushSync();
    expect(target.querySelector('.dx-badge')).toBeNull(); cleared(target);
    expect(handlerHarness.vfo.onFreqChange).not.toHaveBeenCalled();
  });
  it('routes current managed waterfall tap and wheel through canonical handlers', () => {
    const { target } = managed(projection()); const { waterfall } = prepareGeometry(target);
    const canvas = waterfall.querySelector('canvas')!;
    pointer(canvas, 'pointerdown', 87, 150); pointer(canvas, 'pointerup', 87, 150);
    expect(handlerHarness.vfo.onFreqChange).toHaveBeenCalledWith(14_050_000, 0);
    target.querySelector('.spectrum-panel')!.dispatchEvent(new WheelEvent('wheel', { bubbles: true, deltaY: 1 }));
    expect(handlerHarness.vfo.onFreqChange).toHaveBeenCalledTimes(2);
    expect(handlerHarness.vfo.onFreqChange).toHaveBeenLastCalledWith(14_049_000, 0, 'step');
  });
  it('obeys an external demanded=false even if the supplied projection is still live', () => {
    const { target, props } = managed(projection());
    props.set('demanded', false); flushSync(); cleared(target);
    props.set('projection', projection()); flushSync(); cleared(target);
    expect(mockRuntime.acquireHardwareScope).not.toHaveBeenCalled();
    props.set('demanded', true); flushSync();
    expect(target.querySelector('.passband-overlay')).not.toBeNull();
  });
  it.each(['failed', 'cancelled'] as const)('keeps pending resize temporary and returns to the qualified tuple after %s', (phase) => {
    const input = projection(); const { target } = managed(input); const { waterfall } = prepareGeometry(target);
    const zone = waterfall.querySelector('.passband-resize-zone')!;
    pointer(zone, 'pointerdown', 85, 100); pointer(waterfall, 'pointermove', 85, 130); pointer(waterfall, 'pointerup', 85, 130);
    expect(handlerHarness.filter.onFilterWidthCommit).toHaveBeenCalledWith(2_700, 0, 17);
    setFilterWidthLifecycle(pendingFilterWidthLifecycle(2_700)); flushSync();
    expect(target.querySelector<HTMLElement>('.passband-overlay')!.style.width).toBe('27%');
    setFilterWidthLifecycle({ outcome: { phase } }); flushSync();
    expect(target.querySelector<HTMLElement>('.passband-overlay')!.style.width).toBe('24%');
    expect(input.passband).toEqual(projection().passband);
  });
});
