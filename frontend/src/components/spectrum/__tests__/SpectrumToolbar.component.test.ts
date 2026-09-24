/**
 * Component-level authority tests for SpectrumToolbar.svelte.
 * Mounts the real component and proves that confirmed radio truth comes only
 * from the merged spectrum selector while actions use the bound scope family.
 */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { createRawSnippet, mount, unmount, flushSync, tick } from 'svelte';
import { readFileSync } from 'node:fs';
import { createHash } from 'node:crypto';
import { resolve } from 'node:path';

// ── Hoisted authority/intent harnesses ─────────────────────────────────────

const runtimeHarness = vi.hoisted(() => ({
  runtime: {
    state: Object.freeze({ identity: 'toolbar-state' }),
    caps: Object.freeze({ identity: 'toolbar-capabilities' }),
  },
}));

const authorityHarness = vi.hoisted(() => {
  const harness = {
    current: null as any,
    toSpectrumAuthority: vi.fn(() => harness.current),
  };
  return harness;
});

const binderHarness = vi.hoisted(() => {
  const scopeControls = Object.freeze({
    onModeChange: vi.fn(),
    onEdgeChange: vi.fn(),
    onSpanChange: vi.fn(),
    onSpeedChange: vi.fn(),
    onHoldChange: vi.fn(),
    onRefChange: vi.fn(),
    onDualChange: vi.fn(),
    onReceiverChange: vi.fn(),
    onDuringTxChange: vi.fn(),
    onCenterTypeChange: vi.fn(),
    onVbwChange: vi.fn(),
    onRbwChange: vi.fn(),
  });
  const unrelated = Object.freeze({
    agc: Object.freeze({ call: vi.fn() }),
    antenna: Object.freeze({ call: vi.fn() }),
    audioRouting: Object.freeze({ call: vi.fn() }),
    band: Object.freeze({ call: vi.fn() }),
    cw: Object.freeze({ call: vi.fn() }),
    dsp: Object.freeze({ call: vi.fn() }),
    filter: Object.freeze({ call: vi.fn() }),
    mode: Object.freeze({ call: vi.fn() }),
    rfFrontEnd: Object.freeze({ call: vi.fn() }),
    ritXit: Object.freeze({ call: vi.fn() }),
    rxAudio: Object.freeze({ call: vi.fn() }),
    scan: Object.freeze({ call: vi.fn() }),
    tx: Object.freeze({ call: vi.fn() }),
    vfo: Object.freeze({ call: vi.fn() }),
    vox: Object.freeze({ call: vi.fn() }),
  });
  const bound = Object.freeze({ ...unrelated, scopeControls });
  return {
    scopeControls,
    unrelated,
    bound,
    bindSemanticSurfaceHandlers: vi.fn(() => bound),
  };
});

const capabilityHarness = vi.hoisted(() => ({ scope: true, dual: true }));

// These legacy seams remain mocked as alarms. The exact-base component uses
// them, producing causal RED; the final component must never import/call them.
const radioStoreAlarm = vi.hoisted(() => ({
  current: {
    scopeControls: {
      mode: 0, edge: 1, span: 3, speed: 1, hold: false,
      refDb: 0, dual: false, receiver: 0,
    },
  } as any,
}));
const sendCommandAlarm = vi.hoisted(() => vi.fn());

vi.mock('$lib/runtime/frontend-runtime', () => ({ runtime: runtimeHarness.runtime }));

vi.mock('$lib/runtime/adapters/scope-adapter', () => ({
  toSpectrumAuthority: authorityHarness.toSpectrumAuthority,
}));

vi.mock('$lib/runtime/adapters/panel-adapters', () => ({
  bindSemanticSurfaceHandlers: binderHarness.bindSemanticSurfaceHandlers,
}));

vi.mock('$lib/stores/radio.svelte', () => ({
  radio: radioStoreAlarm,
  getRadioState: vi.fn(() => null),
  patchActiveReceiver: vi.fn(),
  patchRadioState: vi.fn(),
}));

vi.mock('$lib/transport/ws-client', () => ({ sendCommand: sendCommandAlarm }));

vi.mock('$lib/stores/capabilities.svelte', () => ({
  hasCapability: vi.fn((name: string) => name === 'scope' && capabilityHarness.scope),
  hasDualReceiver: vi.fn(() => capabilityHarness.dual),
}));

const tuningHarness = vi.hoisted(() => {
  const state = { autoStep: false };
  return {
    state,
    adjustTuningStep: vi.fn(),
    setAutoStep: vi.fn((on: boolean) => { state.autoStep = on; }),
    isAutoStep: vi.fn(() => state.autoStep),
  };
});

vi.mock('$lib/stores/tuning.svelte', () => ({
  getTuningStep: vi.fn(() => 1000),
  adjustTuningStep: tuningHarness.adjustTuningStep,
  isAutoStep: tuningHarness.isAutoStep,
  setAutoStep: tuningHarness.setAutoStep,
  formatStep: vi.fn(() => '1.0k'),
}));

vi.mock('../ScopeSettingsPopover.svelte', () => ({ default: vi.fn() }));

globalThis.fetch = vi.fn(() =>
  Promise.resolve({ ok: false, json: () => Promise.resolve({}) } as Response),
);
const fetchMock = vi.mocked(globalThis.fetch);

import SpectrumToolbar from '../SpectrumToolbar.svelte';

// ── Fact and mount helpers ─────────────────────────────────────────────────

type Field<T> = {
  reading: { status: 'known'; value: T } | { status: 'unknown' };
  availability: { structural: boolean; operational: boolean };
};

function field<T>(value: T, options: {
  known?: boolean; structural?: boolean; operational?: boolean;
} = {}): Field<T> {
  const { known = true, structural = true, operational = true } = options;
  return Object.freeze({
    reading: known ? Object.freeze({ status: 'known' as const, value })
      : Object.freeze({ status: 'unknown' as const }),
    availability: Object.freeze({ structural, operational }),
  });
}

function scopeFacts(overrides: Record<string, unknown> = {}) {
  return Object.freeze({
    mode: field(0),
    edge: field(1),
    span: field(3),
    speed: field(1),
    hold: field(false),
    refDb: field(0),
    dual: field(false),
    receiver: field(0),
    duringTx: field(false),
    centerType: field(0),
    vbwNarrow: field(false),
    rbw: field(0),
    ...overrides,
  });
}

function authority(overrides: Record<string, unknown> = {}) {
  return Object.freeze({
    providerGeneration: 17,
    receiver: 0,
    frequencyHz: 14_200_000,
    mode: 'USB',
    filter: 'FIL1',
    filterWidthHz: 2700,
    filterShape: 1,
    ifShiftHz: 0,
    pbtInnerHz: 0,
    pbtOuterHz: 0,
    dataMode: 0,
    rule: null,
    scopeControls: scopeFacts(),
    digest: 'toolbar-authority',
    ...overrides,
  });
}

let components: ReturnType<typeof mount>[] = [];

function mountToolbar(props: Record<string, unknown> = {}) {
  const target = document.createElement('div');
  document.body.appendChild(target);
  const component = mount(SpectrumToolbar, {
    target,
    props: {
      enableAvg: true,
      enablePeakHold: true,
      brtLevel: 0,
      colorScheme: 'classic',
      fullscreen: false,
      showBandPlan: true,
      hiddenLayers: [],
      showEiBi: false,
      ...props,
    },
  });
  flushSync();
  components.push(component);
  return target;
}

function buttons(root: HTMLElement) {
  return Array.from(root.querySelectorAll<HTMLButtonElement>('button'));
}

function button(root: HTMLElement, text: string) {
  return buttons(root).find((item) => item.textContent?.trim() === text);
}

function scopeSpies() {
  return Object.entries(binderHarness.scopeControls)
    .filter(([, value]) => typeof value === 'function') as [string, ReturnType<typeof vi.fn>][];
}

function expectOnlyScopeCall(name: string, ...args: unknown[]) {
  for (const [candidate, spy] of scopeSpies()) {
    if (candidate === name) expect(spy).toHaveBeenCalledTimes(1);
    else expect(spy).not.toHaveBeenCalled();
  }
  expect((binderHarness.scopeControls as any)[name]).toHaveBeenCalledWith(...args);
  for (const family of Object.values(binderHarness.unrelated)) expect(family.call).not.toHaveBeenCalled();
  expect(sendCommandAlarm).not.toHaveBeenCalled();
}

function clearIntentSpies() {
  for (const [, spy] of scopeSpies()) spy.mockClear();
  for (const family of Object.values(binderHarness.unrelated)) family.call.mockClear();
  sendCommandAlarm.mockClear();
}

beforeEach(() => {
  components = [];
  localStorage.clear();
  vi.clearAllMocks();
  fetchMock.mockImplementation(() =>
    Promise.resolve({ ok: false, json: () => Promise.resolve({}) } as Response),
  );
  tuningHarness.state.autoStep = false;
  capabilityHarness.scope = true;
  capabilityHarness.dual = true;
  authorityHarness.current = authority();
  radioStoreAlarm.current = {
    scopeControls: {
      mode: 0, edge: 1, span: 3, speed: 1, hold: false,
      refDb: 0, dual: false, receiver: 0,
    },
  };
});

afterEach(() => {
  components.forEach((component) => unmount(component));
  document.body.innerHTML = '';
  localStorage.clear();
});

// ── Canonical selector and one-time binder ─────────────────────────────────

describe('canonical spectrum authority and binding', () => {
  it('projects exactly runtime state/caps and binds the broad facade once per mount', () => {
    const target = mountToolbar();
    expect(target.querySelector('.spectrum-toolbar')).not.toBeNull();
    expect(authorityHarness.toSpectrumAuthority).toHaveBeenCalledWith(
      runtimeHarness.runtime.state,
      runtimeHarness.runtime.caps,
    );
    expect(binderHarness.bindSemanticSurfaceHandlers).toHaveBeenCalledTimes(1);
    expect(sendCommandAlarm).not.toHaveBeenCalled();
  });

  it('keeps VIEW reachable and every fact control neutral when the selector rejects the epoch/topology', () => {
    authorityHarness.current = null;
    const onScopeDemandChange = vi.fn();
    const target = mountToolbar({ onScopeDemandChange });
    const view = buttons(target).find((item) => item.textContent?.trim().startsWith('VIEW'))!;
    expect(view).toBeDefined();
    view.click();
    flushSync();
    expect(onScopeDemandChange).toHaveBeenCalledWith(false);
    for (const label of ['CTR', 'FIX', 'S-C', 'S-F', 'HOLD', 'DUAL']) {
      expect(button(target, label)?.disabled).toBe(true);
      expect(button(target, label)?.classList.contains('active')).toBe(false);
    }
    expect(button(target, '—')?.disabled).toBe(true);
    for (const [, spy] of scopeSpies()) expect(spy).not.toHaveBeenCalled();
  });
});

// ── Exactly one matching intent ────────────────────────────────────────────

describe('scope-control intents', () => {
  it.each([
    ['CTR', 0], ['FIX', 1], ['S-C', 2], ['S-F', 3],
  ] as const)('routes mode %s through one mode intent', (label, value) => {
    const target = mountToolbar();
    button(target, label)!.click();
    flushSync();
    expectOnlyScopeCall('onModeChange', value);
  });

  it.each([1, 2, 3, 4])('routes edge %i through one edge intent', (value) => {
    authorityHarness.current = authority({ scopeControls: scopeFacts({ mode: field(1), edge: field(value) }) });
    const target = mountToolbar();
    button(target, String(value))!.click();
    flushSync();
    expectOnlyScopeCall('onEdgeChange', value);
  });

  it('routes span down/up through exactly one clamped intent', () => {
    const target = mountToolbar();
    buttons(target).find((item) => item.title === 'Decrease span')!.click();
    flushSync();
    expectOnlyScopeCall('onSpanChange', 2);
    clearIntentSpies();
    buttons(target).find((item) => item.title === 'Increase span')!.click();
    flushSync();
    expectOnlyScopeCall('onSpanChange', 4);
  });

  it('routes speed down/up through exactly one clamped intent', () => {
    const target = mountToolbar();
    buttons(target).find((item) => item.title === 'Decrease speed')!.click();
    flushSync();
    expectOnlyScopeCall('onSpeedChange', 2);
    clearIntentSpies();
    buttons(target).find((item) => item.title === 'Increase speed')!.click();
    flushSync();
    expectOnlyScopeCall('onSpeedChange', 0);
  });

  it('routes hold, dual and receiver toggles through their matching family members', () => {
    const target = mountToolbar();
    button(target, 'HOLD')!.click();
    flushSync();
    expectOnlyScopeCall('onHoldChange', true);
    clearIntentSpies();
    button(target, 'DUAL')!.click();
    flushSync();
    expectOnlyScopeCall('onDualChange', true);
    clearIntentSpies();
    button(target, 'MAIN')!.click();
    flushSync();
    expectOnlyScopeCall('onReceiverChange', 1);
  });

  it('routes desktop and mobile REF minus/plus/reset with no default', () => {
    authorityHarness.current = authority({ scopeControls: scopeFacts({ refDb: field(5) }) });
    const target = mountToolbar();
    const desktop = Array.from(target.querySelectorAll<HTMLElement>('.toolbar-group.hide-mobile'))
      .find((group) => group.querySelector('.toolbar-label')?.textContent?.trim() === 'REF')!;
    const desktopButtons = Array.from(desktop.querySelectorAll<HTMLButtonElement>('button'));
    desktopButtons[0].click();
    flushSync();
    expectOnlyScopeCall('onRefChange', 0);
    clearIntentSpies();
    desktopButtons[1].click();
    flushSync();
    expectOnlyScopeCall('onRefChange', 10);
    clearIntentSpies();

    target.querySelector<HTMLButtonElement>('[aria-label="Display settings"]')!.click();
    flushSync();
    target.querySelector<HTMLButtonElement>('[aria-label="Decrease reference"]')!.click();
    flushSync();
    expectOnlyScopeCall('onRefChange', 0);
    clearIntentSpies();
    target.querySelector<HTMLButtonElement>('[aria-label="Increase reference"]')!.click();
    flushSync();
    expectOnlyScopeCall('onRefChange', 10);
    clearIntentSpies();
    target.querySelector<HTMLButtonElement>('[aria-label="Reset reference"]')!.click();
    flushSync();
    expectOnlyScopeCall('onRefChange', 0);
  });
});

// ── Exact known domains and fail-closed fields ─────────────────────────────

describe('known-value rendering', () => {
  it.each([
    [0, 'CTR'], [1, 'FIX'], [2, 'S-C'], [3, 'S-F'],
  ] as const)('renders exact mode %i as %s', (value, label) => {
    authorityHarness.current = authority({ scopeControls: scopeFacts({ mode: field(value) }) });
    const target = mountToolbar();
    expect(button(target, label)?.classList.contains('active')).toBe(true);
  });

  it.each([
    [0, '±2.5k'], [1, '±5k'], [2, '±10k'], [3, '±25k'],
    [4, '±50k'], [5, '±100k'], [6, '±250k'], [7, '±500k'],
  ] as const)('renders exact span %i label %s', (value, label) => {
    authorityHarness.current = authority({ scopeControls: scopeFacts({ mode: field(0), span: field(value) }) });
    const target = mountToolbar();
    expect(target.textContent).toContain(label);
  });

  it.each([[0, 'FST'], [1, 'MID'], [2, 'SLO']] as const)(
    'renders exact speed %i label %s', (value, label) => {
      authorityHarness.current = authority({ scopeControls: scopeFacts({ speed: field(value) }) });
      const target = mountToolbar();
      expect(target.textContent).toContain(label);
    },
  );

  it.each([-30, -1, 0, 1, 10])('renders exact reference %i', (value) => {
    authorityHarness.current = authority({ scopeControls: scopeFacts({ refDb: field(value) }) });
    const target = mountToolbar();
    expect(target.textContent).toContain(value > 0 ? `+${value}` : String(value));
  });

  it('renders exact edge, hold, dual and physical MAIN/SUB values', () => {
    authorityHarness.current = authority({ scopeControls: scopeFacts({
      mode: field(1), edge: field(4), hold: field(true), dual: field(true), receiver: field(1),
    }) });
    const target = mountToolbar();
    expect(button(target, '4')?.classList.contains('active')).toBe(true);
    expect(button(target, 'HOLD')?.classList.contains('active')).toBe(true);
    expect(button(target, 'DUAL')?.classList.contains('active')).toBe(true);
    expect(button(target, 'SUB')).toBeDefined();
  });
});

type FieldCase = Readonly<{
  name: string;
  valid: unknown;
  invalid: unknown;
  base?: Record<string, unknown>;
  find: (root: HTMLElement) => HTMLButtonElement | undefined;
}>;

const failClosedCases: readonly FieldCase[] = [
  { name: 'mode', valid: 0, invalid: 4, find: (root) => button(root, 'CTR') },
  { name: 'edge', valid: 1, invalid: 0, base: { mode: field(1) }, find: (root) => button(root, '1') },
  { name: 'span', valid: 3, invalid: 8, base: { mode: field(0) },
    find: (root) => buttons(root).find((item) => item.title === 'Decrease span') },
  { name: 'speed', valid: 1, invalid: 3,
    find: (root) => buttons(root).find((item) => item.title === 'Decrease speed') },
  { name: 'hold', valid: false, invalid: 0, find: (root) => button(root, 'HOLD') },
  { name: 'refDb', valid: 0, invalid: 11,
    find: (root) => Array.from(root.querySelectorAll<HTMLElement>('.toolbar-group.hide-mobile'))
      .find((group) => group.querySelector('.toolbar-label')?.textContent?.trim() === 'REF')
      ?.querySelector<HTMLButtonElement>('button') ?? undefined },
  { name: 'dual', valid: false, invalid: 0, find: (root) => button(root, 'DUAL') },
  { name: 'receiver', valid: 0, invalid: 2, find: (root) => button(root, '—') },
];

describe('fail-closed field handling', () => {
  for (const testCase of failClosedCases) {
    it.each([
      ['unknown', { known: false }],
      ['structural false', { structural: false }],
      ['operational false', { operational: false }],
      ['invalid value', {}],
    ] as const)(`${testCase.name}: %s is neutral, disabled and zero-shot`, (_label, flags) => {
      const value = _label === 'invalid value' ? testCase.invalid : testCase.valid;
      authorityHarness.current = authority({ scopeControls: scopeFacts({
        ...testCase.base,
        [testCase.name]: field(value, flags),
      }) });
      const target = mountToolbar();
      const control = testCase.find(target);
      expect(control).toBeDefined();
      expect(control?.disabled).toBe(true);
      expect(control?.classList.contains('active')).toBe(false);
      control?.click();
      flushSync();
      for (const [, spy] of scopeSpies()) expect(spy).not.toHaveBeenCalled();
      expect(sendCommandAlarm).not.toHaveBeenCalled();
    });
  }

  it('unknown mode exposes neither span nor edge and never fabricates CTR', () => {
    authorityHarness.current = authority({ scopeControls: scopeFacts({ mode: field(0, { known: false }) }) });
    const target = mountToolbar();
    expect(button(target, 'CTR')?.classList.contains('active')).toBe(false);
    expect(target.textContent).not.toContain('SPAN');
    expect(target.textContent).not.toContain('EDGE');
  });
});

// ── Structural/local preservation ──────────────────────────────────────────

describe('structural and browser-local behavior', () => {
  it('hides physical SUB controls for a normal one-receiver topology', () => {
    capabilityHarness.dual = false;
    authorityHarness.current = authority({ scopeControls: scopeFacts({
      dual: field(false, { structural: false }),
      receiver: field(0, { structural: false }),
    }) });
    const target = mountToolbar();
    expect(button(target, 'DUAL')).toBeUndefined();
    expect(button(target, 'MAIN')).toBeUndefined();
    expect(button(target, 'SUB')).toBeUndefined();
    expect(binderHarness.scopeControls.onReceiverChange).not.toHaveBeenCalled();
  });

  it('keeps hideSourceControls and hideScopeControls contracts exact', () => {
    const sourceHidden = mountToolbar({ hideSourceControls: true });
    expect(button(sourceHidden, 'DUAL')).toBeUndefined();
    expect(button(sourceHidden, 'MAIN')).toBeUndefined();
    unmount(components.pop()!);
    sourceHidden.remove();

    const factsHidden = mountToolbar({ hideScopeControls: true });
    for (const label of ['CTR', 'FIX', 'S-C', 'S-F', 'HOLD', 'DUAL', 'MAIN']) {
      expect(button(factsHidden, label)).toBeUndefined();
    }
    expect(factsHidden.querySelector('.toolbar-group-c')).toBeNull();
    expect(factsHidden.querySelector('.settings-group')).toBeNull();
    for (const label of ['AVG', 'PEAK', 'BANDS']) expect(button(factsHidden, label)).toBeDefined();
    expect(buttons(factsHidden).some((item) => item.textContent?.trim().startsWith('VIEW'))).toBe(true);
    expect(factsHidden.querySelector('.toolbar-select')).not.toBeNull();
    expect(factsHidden.querySelector('.icon-btn')).not.toBeNull();
  });

  it('keeps tuning, AVG, PEAK, BRT, color and fullscreen browser-local', () => {
    const target = mountToolbar();
    buttons(target).find((item) => item.title === 'Increase tuning step')!.click();
    button(target, 'AVG')!.click();
    button(target, 'PEAK')!.click();
    button(target, 'BANDS')!.click();
    const brtGroup = Array.from(target.querySelectorAll<HTMLElement>('.toolbar-group.hide-mobile'))
      .find((group) => group.querySelector('.toolbar-label')?.textContent?.trim() === 'BRT')!;
    brtGroup.querySelector<HTMLButtonElement>('button')!.click();
    target.querySelector<HTMLSelectElement>('.toolbar-select')!.value = 'thermal';
    target.querySelector<HTMLButtonElement>('.icon-btn')!.click();
    flushSync();
    expect(tuningHarness.adjustTuningStep).toHaveBeenCalledWith('up');
    for (const [, spy] of scopeSpies()) expect(spy).not.toHaveBeenCalled();
    expect(sendCommandAlarm).not.toHaveBeenCalled();
  });

  it('unmounts cleanly', () => {
    const target = mountToolbar();
    const component = components.pop()!;
    unmount(component);
    expect(target.querySelector('.spectrum-toolbar')).toBeNull();
  });
});

describe('band-plan credential-free HTTP', () => {
  it('omits retired credentials from GETs and the existing config POST', async () => {
    fetchMock.mockImplementation(async (input, init) => {
      const url = String(input);
      if (url.endsWith('/layers')) {
        return {
          ok: true,
          json: async () => ({ layers: [{ layer: 'ham', name: 'Ham' }, { layer: 'eibi', name: 'EiBi' }] }),
        } as Response;
      }
      if (url.endsWith('/config') && init?.method === 'POST') {
        return { ok: true, json: async () => ({}) } as Response;
      }
      if (url.endsWith('/config')) {
        return {
          ok: true,
          json: async () => ({ region: 'US', availableRegions: ['US', 'CA'] }),
        } as Response;
      }
      throw new Error(`unexpected fetch ${url}`);
    });
    localStorage.setItem('rigplane-auth-token', 'read-token');
    const target = mountToolbar();

    await vi.waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(2));
    expect(fetchMock).toHaveBeenCalledWith('/api/v1/band-plan/layers', {
      headers: {},
    });
    expect(fetchMock).toHaveBeenCalledWith('/api/v1/band-plan/config', {
      headers: {},
    });

    localStorage.setItem('rigplane-auth-token', 'write-token');
    await tick();
    await vi.waitFor(() => {
      expect(target.querySelector<HTMLButtonElement>('.layer-toggle-btn')).not.toBeNull();
    });
    target.querySelector<HTMLButtonElement>('.layer-toggle-btn')!.click();
    flushSync();
    const caButton = Array.from(target.querySelectorAll<HTMLButtonElement>('.region-btn'))
      .find((item) => item.textContent?.trim() === 'CA');
    expect(caButton).toBeDefined();
    caButton!.click();

    await vi.waitFor(() => {
      expect(fetchMock).toHaveBeenCalledWith('/api/v1/band-plan/config', {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
        },
        body: JSON.stringify({ region: 'CA' }),
      });
    });
  });
});

// ── Auto-step toggle (MOR-1486) ─────────────────────────────────────────────
//
// Prior to this ticket, `_autoStep` could only ever be re-enabled by wiping
// browser storage — there was no control that called `setAutoStep(true)`.
// The 'A' badge was a passive 9px glyph with no click handler. This suite
// pins the fix: the badge is now a real, keyboard-accessible toggle button
// (`aria-pressed` + a title in both states) that flips the store, and a
// manual step change (STEP click) still disables auto-step exactly as
// before — the toggle only restores the way *back*.
describe('auto-step toggle (MOR-1486)', () => {
  function autoToggle(root: HTMLElement) {
    return button(root, 'AUTO');
  }

  it('renders as a real button with aria-pressed, reflecting the store', () => {
    tuningHarness.state.autoStep = true;
    const target = mountToolbar();
    const toggle = autoToggle(target);
    expect(toggle).toBeDefined();
    expect(toggle?.tagName).toBe('BUTTON');
    expect(toggle?.getAttribute('aria-pressed')).toBe('true');
    expect(toggle?.classList.contains('active')).toBe(true);
  });

  it('reflects the off state with aria-pressed=false and no active class', () => {
    tuningHarness.state.autoStep = false;
    const target = mountToolbar();
    const toggle = autoToggle(target);
    expect(toggle?.getAttribute('aria-pressed')).toBe('false');
    expect(toggle?.classList.contains('active')).toBe(false);
  });

  it('clicking the toggle calls setAutoStep with the flipped value', () => {
    tuningHarness.state.autoStep = false;
    const target = mountToolbar();
    autoToggle(target)!.click();
    flushSync();
    expect(tuningHarness.setAutoStep).toHaveBeenCalledTimes(1);
    expect(tuningHarness.setAutoStep).toHaveBeenCalledWith(true);
  });

  it('clicking an already-on toggle disables it (round trip)', () => {
    tuningHarness.state.autoStep = true;
    const target = mountToolbar();
    autoToggle(target)!.click();
    flushSync();
    expect(tuningHarness.setAutoStep).toHaveBeenCalledWith(false);
  });

  it('carries a non-empty title in both states, distinct from one another', () => {
    tuningHarness.state.autoStep = true;
    const onTarget = mountToolbar();
    const onTitle = autoToggle(onTarget)?.title;
    expect(onTitle).toBeTruthy();

    tuningHarness.state.autoStep = false;
    const offTarget = mountToolbar();
    const offTitle = autoToggle(offTarget)?.title;
    expect(offTitle).toBeTruthy();

    expect(onTitle).not.toBe(offTitle);
  });

  it('a manual STEP click still disables auto-step (unaffected by the new toggle)', () => {
    const target = mountToolbar();
    buttons(target).find((item) => item.title === 'Increase tuning step')!.click();
    flushSync();
    expect(tuningHarness.adjustTuningStep).toHaveBeenCalledWith('up');
    // adjustTuningStep is the store's own internal responsibility for
    // disabling auto-step (covered by tuning.isolated.test.ts) — the
    // toolbar itself never calls setAutoStep(false) directly from a step
    // click, only from the toggle.
    expect(tuningHarness.setAutoStep).not.toHaveBeenCalled();
  });

  it('exists whether auto-step is on or off — not gated on the badge condition removed by this ticket', () => {
    tuningHarness.state.autoStep = false;
    const target = mountToolbar();
    expect(autoToggle(target)).toBeDefined();
  });
});

// ── AUTO toggle gate: hideAutoStepToggle (MOR-1486 ruling B) ────────────────
//
// The toggle re-enables mode-follow, which only keeps doing anything once
// the active layout drives `applyModeDefault()` on subsequent mode changes.
// `MobileRadioLayout` has no such driver (see MOR-1509) and passes
// `hideAutoStepToggle={true}`; `RadioLayout` owns the driver and omits the
// prop. This is deliberately a structural prop, not a skin-name check — the
// toolbar itself has no idea which layout mounted it.
describe('hideAutoStepToggle gate (MOR-1486 ruling B)', () => {
  function autoToggle(root: HTMLElement) {
    return button(root, 'AUTO');
  }

  it('shows the toggle by default (prop omitted)', () => {
    const target = mountToolbar();
    expect(autoToggle(target)).toBeDefined();
  });

  it('shows the toggle when hideAutoStepToggle is explicitly false', () => {
    const target = mountToolbar({ hideAutoStepToggle: false });
    expect(autoToggle(target)).toBeDefined();
  });

  it('hides the toggle when hideAutoStepToggle is true, regardless of store state', () => {
    tuningHarness.state.autoStep = true;
    const onTarget = mountToolbar({ hideAutoStepToggle: true });
    expect(autoToggle(onTarget)).toBeUndefined();

    tuningHarness.state.autoStep = false;
    const offTarget = mountToolbar({ hideAutoStepToggle: true });
    expect(autoToggle(offTarget)).toBeUndefined();
  });

  it('does not affect the manual STEP control when hidden', () => {
    const target = mountToolbar({ hideAutoStepToggle: true });
    const stepControl = buttons(target).find(
      (item) => item.title === 'Click to step up, right-click to step down'
    );
    expect(stepControl).toBeDefined();
  });
});

// ── Static boundary and freeze proof ───────────────────────────────────────

describe('source and enforcement boundary', () => {
  const sourcePath = resolve(process.cwd(), 'src/components/spectrum/SpectrumToolbar.svelte');
  const popoverPath = resolve(process.cwd(), 'src/components/spectrum/ScopeSettingsPopover.svelte');
  const pluginPath = resolve(process.cwd(), 'scripts/radio-authority-eslint-plugin.mjs');
  const contractPath = resolve(process.cwd(), '../docs/internals/ui-radio-control-contract.toml');

  it('has exactly one selector/binder path and no legacy or pending-value authority', () => {
    const source = readFileSync(sourcePath, 'utf8');
    expect(source).toContain('toSpectrumAuthority(runtime.state, runtime.caps)');
    expect(source.match(/bindSemanticSurfaceHandlers\(\)/g)).toHaveLength(1);
    expect(source).toContain('bindSemanticSurfaceHandlers().scopeControls');
    expect(source).not.toMatch(/stores\/radio\.svelte|sendCommand|isFieldAvailable/);
    expect(source).not.toMatch(/localStorage|\bACK\b|\bresult\b|\bpending\b/);
    expect(source).not.toContain('document.body.dataset.scopeAuthority');
    expect(source).not.toMatch(/\.agc\b|\.antenna\b|\.audioRouting\b|\.band\b|\.cw\b|\.dsp\b|\.filter\b|\.mode\b|\.rfFrontEnd\b|\.ritXit\b|\.rxAudio\b|\.scan\b|\.tx\b|\.vfo\b|\.vox\b/);
  });

  it('removes the Toolbar, A07 and A15 presentation/writer exceptions', () => {
    const plugin = readFileSync(pluginPath, 'utf8');
    const contract = readFileSync(contractPath, 'utf8');
    expect(plugin).not.toContain("  'src/components/spectrum/SpectrumToolbar.svelte',");
    expect(contract).not.toContain('  { path = "src/components/spectrum/SpectrumToolbar.svelte", count = 1, owner = "MOR-1409" },');
    for (const path of [
      'src/components-v2/layout/VfoHeader.svelte',
      'src/components/spectrum/ScopeSettingsPopover.svelte',
      'src/lib/media/media-session.ts',
    ]) {
      expect(plugin).not.toContain(`  '${path}',`);
      expect(contract).not.toContain(`  { path = "${path}", count = 1, owner = "MOR-1409" },`);
    }
    // MOR-1409 A15 emptied the presentation-authority exception set: StatusBar
    // lost its `getFrequency` edge and EiBiBrowser's row had been stale since
    // A05b. This assertion previously pinned those two rows as still PRESENT —
    // the A07-era statement that only the Toolbar row had been removed. The
    // ledger is now empty, so the same intent is expressed as absence.
    expect(plugin).not.toContain("  'src/components-v2/layout/StatusBar.svelte',");
    expect(plugin).not.toContain("  'src/components/spectrum/EiBiBrowser.svelte',");
    expect(plugin).toContain('const LEGACY_PRESENTATION_AUTHORITY = new Set([]);');
    expect(contract).not.toContain('  { path = "src/components-v2/layout/StatusBar.svelte", count = 1, owner = "MOR-1409" },');
    expect(contract).not.toContain('  { path = "src/components/spectrum/EiBiBrowser.svelte", count = 1, owner = "MOR-1409" },');
  });

  it('keeps the selector boundaries and pins the scoped toolbar CSS', () => {
    const popover = readFileSync(popoverPath, 'utf8');
    expect(popover).toContain('toSpectrumAuthority(runtime.state, runtime.caps)');
    expect(popover).toContain('bindSemanticSurfaceHandlers().scopeControls');
    expect(popover).not.toMatch(/stores\/radio\.svelte|sendCommand|\?\? false/);
    const source = readFileSync(sourcePath, 'utf8');
    const cssHash = createHash('sha256').update(source.slice(source.indexOf('<style>'))).digest('hex');
    // MOR-2358 adds host-scoped wrapping for the semantic scope surface.
    // MOR-2522 repin: the `.toolbar-select:focus` border frame is gone.
    // MOR-2545 repin: the host wraps the ONE nowrap `.scope-controls-row`;
    // PR2 adds the hosted container query, the STEP overflow copy pair and
    // the scope-status seat. PR2 review repin: the status slot stops
    // shrinking, the host hides the surface's readout text span, and the
    // STEP band moves to 424px (reviewer-measured on PR #3598). Round-3
    // repin: the STEP-band comment is marked as measured on desktop-v2.
    // MOR-2545 PR3 repin: the toolbar's own container query dies (the
    // surface container owns every band); round 2: the strip's ONE ground
    // is --v2-bg-card, the lamp-grammar glow/filter never reach the hosted
    // row, and STEP's More copy shows at the measured-derived 360px band.
    expect(cssHash).toBe('89b6d56e032492b41f0ac3901b25b4e36e624166a667c0c5248e469be8c06000');
  });
});


describe('semantic scope host (MOR-2358)', () => {
  const scopeControls = createRawSnippet(() => ({ render: () => '<div data-testid="semantic-scope-probe">Semantic scope</div>' }));
  it.each([
    [true, true, true, true], [false, true, true, false],
    [true, false, true, false], [true, true, false, false],
  ])('scope=%s, suppression=%s, snippet=%s gates host=%s', (scope, hidden, snippet, hosted) => {
    capabilityHarness.scope = scope;
    const target = mountToolbar({ hideScopeControls: hidden, scopeControls: snippet ? scopeControls : undefined });
    expect(target.querySelectorAll('[data-testid="semantic-scope-probe"]')).toHaveLength(hosted ? 1 : 0);
    expect(target.querySelector('.semantic-scope-controls-host') !== null).toBe(hosted);
  });

  // MOR-2545 PR2 retarget / PR3 re-retarget: the HOSTED toolbar renders
  // ONLY the semantic host, the status seat and fullscreen — every other
  // control lives in the row tail or the More payload, both handed to the
  // semantic row through the snippet; the legacy radio subtree stays
  // suppressed exactly as before.
  it('keeps the hosted toolbar to the semantic host · status · fullscreen and suppresses the legacy radio subtree', () => {
    const target = mountToolbar({ hideScopeControls: true, scopeControls });
    expect(target.querySelectorAll('.semantic-scope-controls-host')).toHaveLength(1);
    for (const label of ['CTR', 'FIX', 'S-C', 'S-F', 'HOLD', 'DUAL', 'MAIN', 'BANDS', 'STEP']) expect(button(target, label)).toBeUndefined();
    expect(target.querySelector('.settings-group')).toBeNull();
    expect(target.querySelector('.toolbar-group-d')).toBeNull();
    expect(target.querySelector('.toolbar-separator')).toBeNull();
    for (const label of ['AUTO', 'AVG', 'PEAK', 'BRT']) expect(button(target, label)).toBeUndefined();
    expect(buttons(target).some((item) => item.textContent?.trim().startsWith('VIEW'))).toBe(false);
    expect(target.querySelector('.toolbar-select')).toBeNull();
    expect(target.querySelector('.icon-btn')).not.toBeNull();
    const step = buttons(target).find((item) => item.title === 'Increase tuning step');
    expect(step).toBeUndefined(); // STEP moved into the row-tail payload
    expect(sendCommandAlarm).not.toHaveBeenCalled();
  });
});

describe('hosted one row + More screen group (MOR-2545 PR2)', () => {
  const TOOLBAR_SOURCE = readFileSync('src/components/spectrum/SpectrumToolbar.svelte', 'utf8')
    .replace(/<!--[\s\S]*?-->/g, '');

  /** Captures the payload the toolbar hands to the hosted snippet. */
  let capturedPayload: unknown[] = [];
  const payloadProbe = createRawSnippet((...args: unknown[]) => ({
    render: () => { capturedPayload = args; return '<div data-testid="semantic-scope-probe"></div>'; },
  }));

  it('renders the radio-held host, the status seat and fullscreen in ONE row container', () => {
    const target = mountToolbar({ hideScopeControls: true, scopeControls: payloadProbe });
    const row = target.querySelector<HTMLElement>('.spectrum-toolbar')!;
    expect(row).not.toBeNull();
    expect(row.classList.contains('hosted')).toBe(true);
    expect(row.querySelector('.semantic-scope-controls-host [data-testid="semantic-scope-probe"]')).not.toBeNull();
    expect(row.querySelector('.icon-btn')).not.toBeNull();
    // The radio-held keys and the toolbar keys share the one container: no
    // second row element splits them.
    expect(target.querySelectorAll('.spectrum-toolbar')).toHaveLength(1);
  });

  it('hands the screen group AND the row tail to the semantic row as the snippet payload — neither is in the closed DOM', () => {
    capturedPayload = [];
    const target = mountToolbar({ hideScopeControls: true, scopeControls: payloadProbe });
    // Svelte passes snippet arguments as getters: three slots, the first
    // reading back `undefined` (the untouched allowBare slot), then the
    // renderable screen group and the renderable row tail.
    expect(capturedPayload.length).toBe(3);
    expect((capturedPayload[0] as () => unknown)()).toBeUndefined();
    expect(typeof (capturedPayload[1] as () => unknown)()).toBe('function');
    expect(typeof (capturedPayload[2] as () => unknown)()).toBe('function');
    // The closed row itself carries none of the screen-only controls.
    for (const label of ['AUTO', 'AVG', 'PEAK', 'BRT', 'BANDS']) expect(button(target, label)).toBeUndefined();
  });

  // The payload's content is the toolbar's own `screenGroup`/`rowTail`
  // snippets — source-pinned here (the same instrument this file's "source
  // and enforcement boundary" block uses), because a raw stub cannot render
  // a real snippet. PR3's e2e spec opens the real panel end-to-end.
  it('builds the screen group and the row tail from the SAME handlers and state as the unhosted row', () => {
    expect(TOOLBAR_SOURCE).toMatch(/\{#snippet screenGroup\(closeMore\?: \(\) => void\)\}/);
    expect(TOOLBAR_SOURCE).toMatch(/\{#snippet rowTail\(\)\}/);
    // [binding, expected template occurrences]: AVG/PEAK have ONE capsule
    // definition (the shared `avgPeakKeys` snippet) rendered at TWO sites
    // (the row's quick keys + the More screen group) plus the UNHOSTED
    // group-D copy — the shared snippet is what makes the row's quick keys
    // and More's AVG/PEAK the same control, not a fork; the BRT steppers
    // also serve the mobile display-gear popover (issue #812) and the
    // unhosted row; STEP has three sites (unhosted, row tail, More copy).
    for (const [expr, count] of [
      ['onclick={() => (enableAvg = !enableAvg)}', 2],
      ['onclick={() => (enablePeakHold = !enablePeakHold)}', 2],
      ['{@render avgPeakKeys()}', 2],
      ['onclick={() => onScopeDemandChange(!scopeDemandOn)}', 2],
      ['onclick={() => (brtLevel = clampBrt(brtLevel, -5))}', 3],
      ['onclick={() => (brtLevel = clampBrt(brtLevel, 5))}', 3],
      ['onclick={toggleAutoStep}', 2],
      ['onclick={cycleStep}', 6],
      ['onclick={cycleStepDown}', 3],
      ['onchange={() => toggleLayer(layer.layer)}', 1],
      ['onclick={() => toggleLayer(layer.layer)}', 1],
    ] as const) {
      expect(TOOLBAR_SOURCE.split(expr).length - 1, expr).toBe(count);
    }
    expect(TOOLBAR_SOURCE).toMatch(/data-testid="scope-more-step"/);
    expect(TOOLBAR_SOURCE).toMatch(/data-testid="scope-more-layers"/);
    expect(TOOLBAR_SOURCE).toMatch(/data-testid="toolbar-row-step"/);
    expect(TOOLBAR_SOURCE).toMatch(/data-testid="toolbar-quick-keys"/);
  });

  it('keeps the MOR-1486 AUTO gate inside the screen group', () => {
    // MOR-2545 PR3: the unhosted step group now lives in the toolbar's
    // `{:else}` branch, where `hosted` is false by construction — the gate
    // no longer needs (and no longer carries) the `!hosted` term.
    expect(TOOLBAR_SOURCE).toMatch(/\{#if !hideAutoStepToggle\}/);
    expect(TOOLBAR_SOURCE.split('{#if !hideAutoStepToggle}').length - 1).toBe(2);
  });

  // PR #3598 report item 7: opening EiBi from the More panel must close the
  // panel, as the old layer dropdown closed itself. Source-pinned here (a
  // raw stub cannot render the real snippet); the composed click-through
  // lives in RadioLayout.audio-fft.component.test.ts.
  it('hands the screen group a close callback and the EiBi entry calls it', () => {
    expect(TOOLBAR_SOURCE).toMatch(/\{#snippet screenGroup\(closeMore\?: \(\) => void\)\}/);
    expect(TOOLBAR_SOURCE).toMatch(/onclick=\{\(\) => \{ showEiBi = true; closeMore\?\.\(\); \}\}/);
  });

  // MOR-2545 PR3: the 360px band is derived from the reviewer's MEASURED
  // capsule widths (scope-capsule.css carries the derivation); the geometry
  // e2e in tests/e2e/i18n/desktop-geometry.spec.ts verifies the bands in a
  // real browser. Round 3 added one LATER capsule-sheet band that retires
  // CTR|FIX last (into More's permanent MODE row) — it lives in
  // scope-capsule.css, not here; STEP stays the toolbar's own last copy.
  it('moves STEP into More at the surface container query\'s measured-derived 360px band', () => {
    expect(TOOLBAR_SOURCE).toMatch(/@container scope-controls \(max-width: 360px\)/);
    expect(TOOLBAR_SOURCE).toMatch(/\.toolbar-step-copy \{ display: flex; \}/);
    expect(TOOLBAR_SOURCE).not.toMatch(/spectrum-toolbar-row/);
    // BANDS, MORE and fullscreen never join the overflow set; only the
    // quick keys and STEP do (both from the row tail).
    expect(TOOLBAR_SOURCE).not.toMatch(/data-overflow="(?!step|quick)[a-z]+"/);
  });

  // MOR-2545 PR3: no bezel `toolbar-btn` in the row tail / screen group;
  // the capsule sheet carries no bezel chrome, no literal colour (MOR-977).
  it('the row tail and the More screen group use only capsule classes, never toolbar-btn', () => {
    const snippetBody = (name: string) => {
      const open = TOOLBAR_SOURCE.indexOf(`{#snippet ${name}(`);
      expect(open, name).toBeGreaterThanOrEqual(0);
      const close = TOOLBAR_SOURCE.indexOf('{/snippet}', open);
      expect(close, name).toBeGreaterThan(open);
      return TOOLBAR_SOURCE.slice(open, close);
    };
    for (const name of ['rowTail', 'screenGroup']) {
      expect(snippetBody(name)).not.toContain('toolbar-btn');
      expect(snippetBody(name)).not.toContain('toolbar-label');
      expect(snippetBody(name)).not.toContain('toolbar-value');
    }
  });

  it('the shared capsule stylesheet is chrome-free and token-only', () => {
    const cssRaw = readFileSync('src/components/spectrum/scope-capsule.css', 'utf8');
    // Comments may NAME the banned properties; the rules may not carry them.
    const css = cssRaw.replace(/\/\*[\s\S]*?\*\//g, '');
    // `box-shadow: none` is allowed — the ⛶ capsule kill resets the bezel's
    // inset shadow; any other value is the bezel family leaking back in.
    expect(css.match(/box-shadow:(?!\s*none\s*;)[^;}]+/)).toBeNull();
    expect(css).not.toContain('text-shadow');
    expect(css).not.toContain('linear-gradient');
    // MOR-977: no literal colours — only var() token references.
    expect(css.match(/#[0-9a-fA-F]{3,8}\b/)).toBeNull();
    // The bezel family is scoped out: the capsule key/step-key names sit in
    // control-button.css's :not() exclusion lists — pinned EXACTLY below.
    const bezel = readFileSync('src/components-v2/controls/control-button.css', 'utf8');
    expect(bezel).toContain('.scope-flat-key, .scope-step-key');
    // One family everywhere: the capsule bands live in the capsule sheet
    // with the measured-derivation comment (pinned exactly below).
    expect(cssRaw).toContain('THE BANDS ARE DERIVED FROM THE REVIEWER\'S MEASURED WIDTHS');
    expect(css).toMatch(/@container scope-controls \(max-width: 873px\)/);
  });

  // PR3 round 2 (finding 8): the round-1 pins were substring matches that
  // stayed green under real mutations. These pins assert EXACT structures.
  describe('the capsule sheet pins (round 2: mutation-killing)', () => {
    const cssRaw = readFileSync('src/components/spectrum/scope-capsule.css', 'utf8');
    // Comments may name colours; the rules may not carry literals.
    const css = cssRaw.replace(/\/\*[\s\S]*?\*\//g, '');

    // MUTATION KILLED: "removing the lit fill" — the lit rule must carry
    // the teal wash as its background, verbatim.
    it('lit = FILLED: the hosted lit rule carries the teal color-mix fill', () => {
      expect(css).toContain(
        ".spectrum-toolbar.hosted .scope-flat-key[data-lit='true'] {\n"
        + '  background: color-mix(in srgb, var(--v2-accent-cyan-teal, var(--v2-accent-cyan)) 50%, transparent);',
      );
    });

    // MUTATION KILLED: "STEP band 335→900" and "receiver and hold bands
    // swapped" — each band literal is asserted against ITS key (measured
    // basis: full row 865, CTR|FIX 97.6, MAIN|SUB 104.5, ja MORE 65.9; the
    // round-3 mode band is the measured 229 px never-hide remainder + 8).
    it('every overflow band matches its key and the measured derivation', () => {
      const bands: Record<string, number> = {
        quick: 873, receiver: 781, hold: 673, ref: 623, span: 495, step: 360, mode: 237,
      };
      for (const [key, px] of Object.entries(bands)) {
        const block = css.match(new RegExp(
          `@container scope-controls \\(max-width: ${px}px\\) \\{([\\s\\S]*?)\\n\\}`, ),
        );
        expect(block, `band ${px}px exists`).not.toBeNull();
        expect(block![1], `band ${px}px hides ${key}`).toContain(`[data-overflow='${key}'] { display: none; }`);
      }
      // Swaps are caught twice over: a band block may hide exactly ONE key.
      const hides = [...css.matchAll(/\[data-overflow='(quick|receiver|hold|ref|span|step|mode)'\] \{ display: none; \}/g)]
        .map((m) => m[1]);
      expect(hides).toEqual(['quick', 'receiver', 'hold', 'ref', 'span', 'step', 'mode']);
    });

    // MUTATION KILLED: "dropping .scope-flat-key, .scope-step-key from the
    // MAIN exclusion list" — the round-1 pin was a substring 12 other
    // lists satisfy; the MAIN rule's :not() list is now asserted verbatim.
    it('the main control-button.css exclusion list keeps the scope key names, verbatim', () => {
      const bezel = readFileSync('src/components-v2/controls/control-button.css', 'utf8');
      const firstNot = bezel.match(/\):not\(([^)]*)\)/);
      expect(firstNot).not.toBeNull();
      expect(firstNot![1]).toBe(
        '.panel-header, .drag-handle, .passband-resize-zone, .band-segment, .scope-flat-key, .scope-step-key',
      );
    });

    // Round-2 findings 4–5: ⛶ and the palette select join the capsule
    // family inside the hosted strip, by specificity — never !important.
    it('⛶ and the More-panel select are restyled as hosted capsules, without !important', () => {
      expect(css).toContain('.spectrum-toolbar.hosted .scope-more-panel .scope-capsule-select {');
      expect(css).toContain('.desktop-control-face .spectrum-toolbar.hosted > button.toolbar-btn.icon-btn {');
      expect(css).not.toContain('!important');
    });

    // Round-3 finding 1 (MUTATION KILLED: sizing the cycler 16px/13px — it
    // collapsed across both arrows and ‹ could not be clicked). The arrow
    // box/glyph sizing reaches ONLY the arrow keys; the cycler keeps
    // SpectrumToolbar's own `.step-cycler` rule (natural width, 11 px).
    it('the step-arrow sizing excludes the STEP cycler', () => {
      const arrow = css.match(/\.spectrum-toolbar\.hosted \.scope-step-key:not\(\.step-cycler\) \{([\s\S]*?)\n\}/);
      expect(arrow, 'the arrow-only rule exists').not.toBeNull();
      expect(arrow![1]).toContain('width: 16px;');
      expect(arrow![1]).toContain('font-size: 13px;');
      const base = css.match(/\.spectrum-toolbar\.hosted \.scope-step-key \{([\s\S]*?)\n\}/);
      expect(base, 'the shared step-key rule exists').not.toBeNull();
      expect(base![1]).not.toContain('width:');
      expect(base![1]).not.toContain('font-size:');
    });

    // Round-3 contrast (the verifier's per-theme measurement, row and More
    // panel): lit text is the white token on the teal fill, values the
    // bright token, unlit labels/arrows the lighter token.
    it('the measured text tokens: white on the lit fill, bright values, lighter labels', () => {
      const lit = css.match(/\.spectrum-toolbar\.hosted \.scope-flat-key\[data-lit='true'\] \{([\s\S]*?)\n\}/);
      expect(lit![1]).toContain('color: var(--v2-text-white, var(--text));');
      const value = css.match(/\.spectrum-toolbar\.hosted \.scope-step-value \{([\s\S]*?)\n\}/);
      expect(value![1]).toContain('color: var(--v2-text-bright, var(--text));');
      const key = css.match(/\.spectrum-toolbar\.hosted \.scope-flat-key \{([\s\S]*?)\n\}/);
      expect(key![1]).toContain('color: var(--v2-text-lighter, var(--text));');
      // A lit key keeps its white text on hover — the hover rule skips it.
      expect(css).toContain(".spectrum-toolbar.hosted .scope-flat-key:not([data-lit='true']):hover:not(:disabled)");
    });

    // Round-3 finding: ⛶ and the palette select measured 28 px tall — a
    // min-height from other stylesheets was never reset. Every capsule
    // rule that pins a height must also reset min-height.
    it('every rule that pins a capsule height also resets min-height', () => {
      const rules = [...css.matchAll(/([^{}]+)\{([^{}]*)\}/g)];
      const heightRules = rules.filter(([, , body]) => /height: 2\dpx/.test(body));
      expect(heightRules.length).toBeGreaterThan(0);
      for (const [selector, , body] of heightRules) {
        expect(body, `${selector.trim()} resets min-height`).toContain('min-height: 0;');
      }
    });
  });

  // PR #3598 findings 2/3 + the coordinator decision on the status text:
  // jsdom cannot compute the cascade, so pin the host rules themselves —
  // the seat hides the surface's visible readout span (compact form) and
  // keeps the status slot from shrinking.
  it('keeps the status seat compact and unshrinkable inside the row', () => {
    expect(TOOLBAR_SOURCE).toMatch(/\.scope-status-host :global\(\.scope-display-text\) \{ display: none; \}/);
    expect(TOOLBAR_SOURCE).toMatch(/\.scope-status-host :global\(\.semantic-control-panel\) \{ flex-shrink: 0; \}/);
  });

  it('mounts the compact scope status indicator inside the row, before fullscreen', () => {
    const statusProbe = createRawSnippet(() => ({ render: () => '<span data-testid="status-probe"></span>' }));
    const target = mountToolbar({ hideScopeControls: true, scopeControls: payloadProbe, scopeStatusIndicator: statusProbe });
    const seat = target.querySelector('[data-testid="toolbar-scope-status"]')!;
    expect(seat).not.toBeNull();
    expect(seat.querySelector('[data-testid="status-probe"]')).not.toBeNull();
    expect(seat.closest('.spectrum-toolbar')).not.toBeNull();
    const fullscreenBtn = target.querySelector<HTMLButtonElement>('.icon-btn')!;
    expect(fullscreenBtn.title).toBe('Toggle fullscreen');
    expect(fullscreenBtn.compareDocumentPosition(seat) & Node.DOCUMENT_POSITION_PRECEDING).toBeTruthy();
  });

  it('renders no scope status seat when no snippet is provided', () => {
    const target = mountToolbar({ hideScopeControls: true, scopeControls: payloadProbe });
    expect(target.querySelector('[data-testid="toolbar-scope-status"]')).toBeNull();
  });
});
