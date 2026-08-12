import { createHash } from 'node:crypto';
import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { afterEach, describe, expect, it, vi } from 'vitest';
import type { Capabilities, FilterModeConfig } from '$lib/types/capabilities';
import type { FieldStatus, ServerState } from '$lib/types/state';

const h = vi.hoisted(() => ({
  modelOverride: null as unknown,
  calls: 0,
}));

vi.mock('../radio-view-model-adapter', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../radio-view-model-adapter')>();
  return {
    ...actual,
    toRadioViewModel: (...args: Parameters<typeof actual.toRadioViewModel>) => {
      h.calls += 1;
      return h.modelOverride ?? actual.toRadioViewModel(...args);
    },
  };
});

import {
  parseScopeFrame, snapSpectrumFilterWidth, toSpectrumAuthority,
} from '../scope-adapter';
import { toRadioViewModel } from '../radio-view-model-adapter';
import { deriveIfShift, pbtRawToHz } from '$lib/radio/filter-controls';

const fresh: FieldStatus = {
  storePath: 'fixture', observed: true, freshness: 'fresh', availability: 'available',
};
const stale: FieldStatus = {
  storePath: 'fixture', observed: true, freshness: 'stale', availability: 'stale',
};
const SEGMENT_RULE: FilterModeConfig = {
  defaults: [3000, 2400, 1800], fixed: false, minHz: 50, maxHz: 3600,
  segments: [
    { hzMin: 50, hzMax: 500, stepHz: 50, indexMin: 0 },
    { hzMin: 600, hzMax: 3600, stepHz: 100, indexMin: 10 },
  ],
};
const TABLE_RULE: FilterModeConfig = {
  defaults: [2400, 1800, 300], fixed: false, table: [300, 600, 1200, 1800, 2400, 3000],
};
const STEP_RULE: FilterModeConfig = {
  defaults: [3050, 1750, 550], fixed: false, minHz: 250, maxHz: 3550, stepHz: 100,
};

function receiver(frequencyHz: number, mode = 'USB') {
  return {
    freqHz: frequencyHz, mode, filter: 2, dataMode: 1, filterWidth: 2400,
    filterShape: 1, pbtInner: 140, pbtOuter: 116, ifShift: 0,
    att: 0, preamp: 0, nb: false, nr: false, afLevel: 1, rfGain: 1, squelch: 0, sMeter: 0,
  };
}

function fieldStatus(): Record<string, FieldStatus> {
  const paths = [
    'active', 'split', 'dualWatch', 'txTarget',
    'main.freqHz', 'main.mode', 'main.filter', 'main.filterWidth', 'main.filterShape',
    'main.dataMode', 'main.pbtInner', 'main.pbtOuter', 'main.ifShift',
    'sub.freqHz', 'sub.mode', 'sub.filter', 'sub.filterWidth', 'sub.filterShape',
    'sub.dataMode', 'sub.pbtInner', 'sub.pbtOuter', 'sub.ifShift',
    ...[
      'mode', 'edge', 'span', 'speed', 'hold', 'refDb', 'dual', 'receiver',
      'duringTx', 'centerType', 'vbwNarrow', 'rbw',
    ].map((leaf) => `scopeControls.${leaf}`),
  ];
  return Object.fromEntries(paths.map((path) => [path, { ...fresh, storePath: path }]));
}

function state(overrides: Partial<ServerState> = {}): ServerState {
  return {
    stateContractVersion: 1, providerGeneration: 7,
    active: 'MAIN', split: false, dualWatch: false, ptt: false, tunerStatus: 0,
    txTarget: { status: 'known', receiver: 'MAIN', slot: null, frequencyHz: 14_074_000 },
    main: receiver(14_074_000), sub: receiver(7_074_000, 'LSB'),
    scopeControls: {
      mode: 0, edge: 1, span: 3, speed: 1, hold: false, refDb: -5,
      dual: false, receiver: 0, duringTx: false, centerType: 1, vbwNarrow: false, rbw: 1,
      fixedEdge: { rangeIndex: 0, edge: 1, startHz: 14_000_000, endHz: 14_350_000 },
    },
    fieldStatus: fieldStatus(),
    ...overrides,
  } as ServerState;
}

function caps(overrides: Partial<Capabilities> = {}): Capabilities {
  return {
    model: 'fixture', scope: true, audio: true, tx: true,
    stateContractVersion: 1, providerGeneration: 7,
    capabilities: ['scope', 'dual_rx', 'filter_width', 'data_mode', 'pbt', 'if_shift'],
    receivers: 2, vfoScheme: 'main_sub', freqRanges: [], modes: ['USB', 'LSB'], filters: ['FIL1'],
    filterConfig: { USB: SEGMENT_RULE, LSB: SEGMENT_RULE },
    audioConfig: { sampleRate: 48_000, channels: 1, codecs: ['pcm16'] },
    webrtc: { available: false, enabled: false }, txBands: [],
    ...overrides,
  } as Capabilities;
}

function withStatus(base: ServerState, path: string, status: FieldStatus | undefined): ServerState {
  const statuses = { ...base.fieldStatus } as Record<string, FieldStatus>;
  if (status) statuses[path] = { ...status, storePath: path };
  else delete statuses[path];
  return { ...base, fieldStatus: statuses } as ServerState;
}

afterEach(() => {
  h.modelOverride = null;
  h.calls = 0;
});

describe('MOR-1409 A06a1 canonical spectrum authority selector', () => {
  it('delegates once, returns deterministic deeply immutable facts, and leaves inputs untouched', () => {
    const inputState = state();
    const inputCaps = caps();
    const beforeState = structuredClone(inputState);
    const beforeCaps = structuredClone(inputCaps);

    const first = toSpectrumAuthority(inputState, inputCaps);
    const second = toSpectrumAuthority(inputState, inputCaps);

    expect(h.calls).toBe(2);
    expect(first).toEqual(second);
    expect(first).toMatchObject({
      providerGeneration: 7, receiver: 0, frequencyHz: 14_074_000,
      mode: 'USB', filter: 'FIL2', filterWidthHz: 2400,
      filterShape: 1, ifShiftHz: 0, pbtInnerHz: 113, pbtOuterHz: -112, dataMode: 1,
      rule: { kind: 'segments', minHz: 50, maxHz: 3600 },
    });
    expect(first?.digest).toBe(second?.digest);
    expect(Object.isFrozen(first)).toBe(true);
    expect(Object.isFrozen(first?.rule)).toBe(true);
    expect(Object.isFrozen(first?.rule?.kind === 'segments' ? first.rule.segments : null)).toBe(true);
    expect(Object.isFrozen(first?.scopeControls)).toBe(true);
    expect(Object.isFrozen(first?.scopeControls?.mode)).toBe(true);
    expect(Object.isFrozen(first?.scopeControls?.mode.reading)).toBe(true);
    expect(inputState).toEqual(beforeState);
    expect(inputCaps).toEqual(beforeCaps);
    expect(Object.isFrozen(inputState)).toBe(false);
    expect(Object.isFrozen(inputCaps)).toBe(false);
    expect(Object.isFrozen(inputCaps.capabilities)).toBe(false);

    inputState.scopeControls!.mode = 3;
    inputCaps.capabilities.push('later');
    expect(first?.scopeControls?.mode.reading).toEqual({ status: 'known', value: 0 });
  });

  it.each([
    [state({ stateContractVersion: undefined }), caps()],
    [state(), caps({ stateContractVersion: undefined })],
    [state({ providerGeneration: -1 }), caps()],
    [state({ providerGeneration: 8 }), caps()],
    [state(), caps({ providerGeneration: 8 })],
    [state(), caps({ receivers: 1 })],
    [state(), caps({ vfoScheme: 'single' })],
  ] as Array<[ServerState, Capabilities]>)('rejects invalid epoch/topology row %#', (s, c) => {
    expect(toSpectrumAuthority(s, c)).toBeNull();
  });

  it('rejects unknown active identity and physical SUB without valid dual topology', () => {
    expect(toSpectrumAuthority(withStatus(state(), 'active', stale), caps())).toBeNull();
    const sub = state({ active: 'SUB' });
    expect(toSpectrumAuthority(sub, caps({ capabilities: ['scope', 'filter_width', 'data_mode'] }))).toBeNull();
  });

  it('maps valid dual MAIN/SUB to physical 0/1 with their canonical VFO facts', () => {
    const main = toSpectrumAuthority(state(), caps());
    const sub = toSpectrumAuthority(state({ active: 'SUB' }), caps());
    expect(main).toMatchObject({ receiver: 0, frequencyHz: 14_074_000, mode: 'USB' });
    expect(sub).toMatchObject({ receiver: 1, frequencyHz: 7_074_000, mode: 'LSB' });
  });

  it.each([
    ['single', undefined],
    ['ab', 'selected_unselected'],
  ] as const)('maps one-receiver %s/relative identity to physical MAIN/0', (vfoScheme, vfoReadback) => {
    const s = state({ active: 'MAIN', sub: receiver(7_074_000) as ServerState['sub'] });
    const c = caps({
      receivers: 1, vfoScheme, vfoReadback,
      capabilities: ['scope', 'filter_width', 'data_mode', 'pbt', 'if_shift'],
    });
    expect(toSpectrumAuthority(s, c)).toMatchObject({ receiver: 0, frequencyHz: 14_074_000 });
  });

  /**
   * MOR-1421 — the live IC-7300 stand's `active` field reads
   * observed:false/availability:'missing' FOREVER; `main.activeSlot` is
   * equally never observed. Before the fix, `activeReceiver` stayed
   * `unknown` forever on that class of radio and this whole authority was
   * permanently null — no spectrum, no filter-width snapping, nothing. The
   * capability-aware fix resolves `activeReceiver` to `'MAIN'` on a
   * single-receiver topology regardless of `active`'s observedness, which is
   * what revives this authority.
   */
  it('revives the spectrum authority on a single-receiver radio even though active was never observed', () => {
    const s = withStatus(withStatus(state({ active: 'MAIN' }), 'active', undefined), 'main.activeSlot', undefined);
    const c = caps({
      receivers: 1, vfoScheme: 'ab', vfoReadback: 'selected_unselected',
      capabilities: ['scope', 'filter_width', 'data_mode', 'pbt', 'if_shift'],
    });
    expect(toSpectrumAuthority(s, c)).toMatchObject({ receiver: 0, frequencyHz: 14_074_000, mode: 'USB' });
  });

  // Dual-RX guard (byte-identical to pre-MOR-1421 behaviour): a radio whose
  // capabilities declare a SECOND receiver still needs a genuinely observed
  // `active` reading — the tautology is single-receiver-only.
  it('leaves a dual-receiver radio unaffected — null when active is unobserved, not stale (MOR-1421)', () => {
    const s = withStatus(state(), 'active', undefined);
    expect(toSpectrumAuthority(s, caps())).toBeNull();
  });

  it('rejects zero active canonical VFOs in an unobserved absolute A/B slot view', () => {
    const s = state({
      main: {
        ...receiver(14_074_000), activeSlot: 'A',
        vfoA: { freqHz: 14_074_000, mode: 'USB', filterNum: 2, dataMode: 1 },
        vfoB: { freqHz: 7_074_000, mode: 'LSB', filterNum: 1, dataMode: 0 },
      },
    } as Partial<ServerState>);
    expect(toSpectrumAuthority(s, caps({
      receivers: 1, vfoScheme: 'ab', vfoReadback: 'absolute',
      capabilities: ['scope', 'filter_width', 'data_mode', 'pbt', 'if_shift'],
    }))).toBeNull();
  });

  it('rejects a delegated model with multiple active canonical VFOs', () => {
    const model = toRadioViewModel(state(), caps());
    expect(model).not.toBeNull();
    h.modelOverride = {
      ...model!,
      vfos: model!.vfos.map((vfo, index) => ({ ...vfo, isActive: index < 2 })),
    };
    expect(toSpectrumAuthority(state(), caps())).toBeNull();
  });

  it('degrades an independently stale frequency to null without losing receiver identity', () => {
    const result = toSpectrumAuthority(withStatus(state(), 'main.freqHz', stale), caps());
    expect(result).toMatchObject({ receiver: 0, frequencyHz: null, mode: 'USB' });
  });

  it.each([
    ['main.mode', 'mode'], ['main.filterWidth', 'filterWidthHz'],
    ['main.dataMode', 'dataMode'], ['main.filterShape', 'filterShape'],
    ['main.ifShift', 'ifShiftHz'], ['main.pbtInner', 'pbtInnerHz'], ['main.pbtOuter', 'pbtOuterHz'],
  ] as const)('degrades stale %s only through its projected fact/rule', (path, key) => {
    const result = toSpectrumAuthority(withStatus(state(), path, stale), caps());
    expect(result).not.toBeNull();
    expect(result?.[key]).toBeNull();
    if (path === 'main.mode' || path === 'main.filterWidth' || path === 'main.dataMode') {
      expect(result?.rule).toBeNull();
    }
  });

  it('requires active-VFO and modeFilter parity before constructing a rule', () => {
    const model = toRadioViewModel(state(), caps());
    expect(model?.modeFilter).toBeDefined();
    h.modelOverride = {
      ...model!, modeFilter: {
        ...model!.modeFilter!, currentMode: {
          ...model!.modeFilter!.currentMode, reading: { status: 'known', value: 'CW' },
        },
      },
    };
    expect(toSpectrumAuthority(state(), caps())).toMatchObject({ mode: 'USB', rule: null });
  });

  it.each([
    { defaults: [2400], fixed: true, table: [2400] },
    { defaults: [2400], fixed: false },
    { defaults: [], fixed: false, table: [] },
    { defaults: [], fixed: false, table: [600, 300] },
    { defaults: [], fixed: false, table: [300], segments: SEGMENT_RULE.segments },
    { ...SEGMENT_RULE, segments: [{ hzMin: 50, hzMax: 525, stepHz: 50, indexMin: 0 }] },
    { ...STEP_RULE, stepHz: 0 },
  ] as FilterModeConfig[])('normalizes fixed/unresolved/malformed rule %# to null', (rule) => {
    expect(toSpectrumAuthority(state(), caps({ filterConfig: { USB: rule } }))?.rule).toBeNull();
  });

  it.each([
    [TABLE_RULE, 'table'], [SEGMENT_RULE, 'segments'], [STEP_RULE, 'step'],
  ] as const)('normalizes valid %s metadata without sharing input arrays', (rule, kind) => {
    const inputCaps = caps({ filterConfig: { USB: rule } });
    const normalized = toSpectrumAuthority(state(), inputCaps)?.rule;
    expect(normalized?.kind).toBe(kind);
    expect(Object.isFrozen(normalized)).toBe(true);
    expect(normalized).not.toBe(rule);
  });

  it('uses structural DATA-OFF only when data_mode is unsupported', () => {
    const s = withStatus(state(), 'main.dataMode', stale);
    const withoutData = caps({
      capabilities: ['scope', 'dual_rx', 'filter_width', 'pbt', 'if_shift'],
      filterConfig: { USB: STEP_RULE },
    });
    expect(toSpectrumAuthority(s, withoutData)).toMatchObject({ dataMode: null, rule: { kind: 'step' } });
    expect(toSpectrumAuthority(s, caps({ filterConfig: { USB: STEP_RULE } }))).toMatchObject({ rule: null });
  });

  it('changes the digest with canonical facts but not with mutable input identity', () => {
    const first = toSpectrumAuthority(state(), caps());
    const equal = toSpectrumAuthority(structuredClone(state()), structuredClone(caps()));
    const changed = toSpectrumAuthority(state({ main: { ...receiver(14_074_000), filterWidth: 1800 } }), caps());
    expect(first?.digest).toBe(equal?.digest);
    expect(first?.digest).not.toBe(changed?.digest);
  });

  /**
   * MOR-1494 review round — the trap. `FilterSurface.svelte`'s IF-shift ROW
   * now hides for an IC-7300-shaped radio (pbt, no if_shift capability), via
   * a NEW `ifShiftControlStructural` presentation flag. This authority's
   * `ifShiftHz` must be UNAFFECTED: it reads `filterPassband.ifShift`
   * directly (`toSpectrumAuthority`'s own `knownReading` call, keyed on the
   * field's `reading.status`, never on `ifShiftControlStructural`), which
   * still derives from PBT for exactly this radio shape. A fix that gated
   * the DERIVED fact instead of adding a separate presentation flag would
   * have silently broken this passband-center overlay.
   */
  it('IC-7300-shaped caps (pbt, no if_shift): ifShiftHz still derives from PBT (MOR-1494 trap)', () => {
    const icomCaps = caps({
      capabilities: ['scope', 'dual_rx', 'filter_width', 'data_mode', 'pbt'],
    });
    const result = toSpectrumAuthority(state(), icomCaps);
    const expectedIfShiftHz = deriveIfShift(pbtRawToHz(140), pbtRawToHz(116));
    expect(result?.ifShiftHz).toBe(expectedIfShiftHz);
    expect(result?.ifShiftHz).not.toBeNull();
  });
});

describe('MOR-1409 A06a1 deterministic filter-width snapping', () => {
  const rule = (config: FilterModeConfig) =>
    toSpectrumAuthority(state(), caps({ filterConfig: { USB: config } }))!.rule;

  it('snaps tables to the nearest exact entry and resolves ties low', () => {
    const table = rule(TABLE_RULE);
    expect(snapSpectrumFilterWidth(1700, table)).toBe(1800);
    expect(snapSpectrumFilterWidth(1500, table)).toBe(1200);
    expect(snapSpectrumFilterWidth(-1, table)).toBe(300);
    expect(snapSpectrumFilterWidth(99_999, table)).toBe(3000);
  });

  it('snaps segments from each hzMin, across gaps, with deterministic tie-low', () => {
    const segments = rule(SEGMENT_RULE);
    expect(snapSpectrumFilterWidth(624, segments)).toBe(600);
    expect(snapSpectrumFilterWidth(626, segments)).toBe(600);
    expect(snapSpectrumFilterWidth(550, segments)).toBe(500);
    expect(snapSpectrumFilterWidth(575, segments)).toBe(600);
    expect(snapSpectrumFilterWidth(75, segments)).toBe(50);
    expect(snapSpectrumFilterWidth(9_999, segments)).toBe(3600);
    const offset = rule({
      defaults: [275], fixed: false, minHz: 75, maxHz: 275,
      segments: [{ hzMin: 75, hzMax: 275, stepHz: 50, indexMin: 0 }],
    });
    expect(snapSpectrumFilterWidth(100, offset)).toBe(75);
  });

  it('snaps simple steps from the resolved minimum and rejects invalid inputs', () => {
    const step = rule(STEP_RULE);
    expect(snapSpectrumFilterWidth(299, step)).toBe(250);
    expect(snapSpectrumFilterWidth(300, step)).toBe(250);
    expect(snapSpectrumFilterWidth(301, step)).toBe(350);
    expect(snapSpectrumFilterWidth(Number.NaN, step)).toBeNull();
    expect(snapSpectrumFilterWidth(1000, null)).toBeNull();
    expect(snapSpectrumFilterWidth(1000, { kind: 'step', minHz: 0, maxHz: 10, stepHz: 0 })).toBeNull();
  });
});

describe('MOR-1409 A06a1 decoder and purity boundary', () => {
  it('keeps ScopeFrame/parseScopeFrame prefix byte-identical and decoding unchanged', () => {
    const source = readFileSync(resolve(process.cwd(), 'src/lib/runtime/adapters/scope-adapter.ts'), 'utf8');
    const prefixEnd = source.indexOf('\n}\n', source.indexOf('export function parseScopeFrame')) + 3;
    const prefix = source.slice(0, prefixEnd);
    expect(createHash('sha256').update(prefix).digest('hex'))
      .toBe('e1ab452f4ab31dbe68a883b87708a3b9942145e50ef2a3e9e33904aa5fc838c1');
    const buffer = new ArrayBuffer(17);
    const view = new DataView(buffer);
    view.setUint8(0, 0x01); view.setUint8(1, 1); view.setUint8(2, 2);
    view.setUint32(3, 14_000_000, true); view.setUint32(7, 14_350_000, true);
    view.setUint16(14, 1, true); view.setUint8(16, 0xff);
    expect(parseScopeFrame(buffer)).toEqual({
      receiver: 1, mode: 2, startFreq: 14_000_000, endFreq: 14_350_000,
      pixels: new Uint8Array([0xff]),
    });
  });

  it('keeps frame/pixel/runtime/store/transport inputs outside the selector permission path', () => {
    const source = readFileSync(resolve(process.cwd(), 'src/lib/runtime/adapters/scope-adapter.ts'), 'utf8');
    const selector = source.slice(source.indexOf('export function toSpectrumAuthority'));
    expect(selector).toMatch(/toSpectrumAuthority\(\s*state[^,]*,\s*caps/);
    expect(selector).not.toMatch(/ScopeFrame|pixels|Uint8Array|FrontendRuntime|radio\.svelte|transport|sendCommand/);
  });
});
