/**
 * Component-level authority tests for SpectrumToolbar.svelte.
 * Mounts the real component and proves that confirmed radio truth comes only
 * from the merged spectrum selector while actions use the bound scope family.
 */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { mount, unmount, flushSync } from 'svelte';
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

const tuningHarness = vi.hoisted(() => ({
  adjustTuningStep: vi.fn(),
}));

vi.mock('$lib/stores/tuning.svelte', () => ({
  getTuningStep: vi.fn(() => 1000),
  adjustTuningStep: tuningHarness.adjustTuningStep,
  isAutoStep: vi.fn(() => false),
  formatStep: vi.fn(() => '1.0k'),
}));

vi.mock('../ScopeSettingsPopover.svelte', () => ({ default: vi.fn() }));

globalThis.fetch = vi.fn(() =>
  Promise.resolve({ ok: false, json: () => Promise.resolve({}) } as Response),
);

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
  vi.clearAllMocks();
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

  it('removes only Toolbar and A07 presentation/writer exceptions', () => {
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
    expect(plugin).toContain("  'src/components-v2/layout/StatusBar.svelte',");
    expect(plugin).toContain("  'src/components/spectrum/EiBiBrowser.svelte',");
    expect(contract).toContain('  { path = "src/components-v2/layout/StatusBar.svelte", count = 1, owner = "MOR-1409" },');
    expect(contract).toContain('  { path = "src/components/spectrum/EiBiBrowser.svelte", count = 1, owner = "MOR-1409" },');
  });

  it('releases the old popover hash pin while keeping all Toolbar CSS byte-frozen', () => {
    const popover = readFileSync(popoverPath, 'utf8');
    expect(popover).toContain('toSpectrumAuthority(runtime.state, runtime.caps)');
    expect(popover).toContain('bindSemanticSurfaceHandlers().scopeControls');
    expect(popover).not.toMatch(/stores\/radio\.svelte|sendCommand|\?\? false/);
    const source = readFileSync(sourcePath, 'utf8');
    const cssHash = createHash('sha256').update(source.slice(source.indexOf('<style>'))).digest('hex');
    expect(cssHash).toBe('eb9e75ed2988082d6966d6727f73d3d77651086df38b1458aed3e9c274725fb7');
  });
});
