import { scopeWire } from '$lib/runtime/__tests__/scope-wire-fixture';
import { describe, expect, it, vi } from 'vitest';
import type { Capabilities } from '$lib/types/capabilities';
import type { FieldStatus, ServerState } from '$lib/types/state';
import { deriveIfShift, pbtRangeFromCaps } from '$lib/radio/filter-controls';
import { getPassbandEdgesHz } from '../../../../components/spectrum/passband-geometry';
import { ingressScopeFrame, toSpectrumAuthority } from '../scope-adapter';
import { toRadioViewModel } from '../radio-view-model-adapter';
import { IC7300_CAPABILITIES } from './fixtures/ic7300-profile';
import { editCaps, editState } from '../../../transport/__tests__/support/state-edit';
import { EMPTY_SCOPE_PASSBAND_DISPLAY, projectScopePassbandDisplay,
  type ScopePassbandDisplayInput, type ScopePassbandDisplayState } from '../scope-passband-display';

vi.mock('$lib/stores/capabilities.svelte', async (original) => ({
  ...await original<typeof import('$lib/stores/capabilities.svelte')>(),
  getControlRange: () => { throw new Error('global PBT scale must not be read'); },
}));

function fixture(): ScopePassbandDisplayInput {
  const rx = {
    freqHz: 14_074_000, mode: 'USB', filter: 1, dataMode: 0, filterWidth: 2400,
    ifShift: 0, pbtInner: 128, pbtOuter: 128, activeSlot: 'A',
    vfoA: { freqHz: 14_074_000, mode: 'USB', filterNum: 1, dataMode: 0 },
    vfoB: { freqHz: 14_074_000, mode: 'USB', filterNum: 1, dataMode: 0 },
    att: 0, preamp: 0, nb: false, nr: false, afLevel: 1, rfGain: 1, squelch: 0, sMeter: 0,
  };
  const paths = ['active', ...['main', 'sub'].flatMap((key) => [key,
    ...Object.keys(rx).map((leaf) => `${key}.${leaf}`),
    ...['vfoA', 'vfoB'].flatMap((slot) => Object.keys(rx.vfoA).map((leaf) => `${key}.${slot}.${leaf}`)),
  ])];
  const state = {
    stateContractVersion: 1, providerGeneration: 1, active: 'MAIN',
    split: false, dualWatch: false, ptt: false, tunerStatus: 0,
    txTarget: { status: 'known', receiver: 'MAIN', slot: null, frequencyHz: rx.freqHz },
    main: structuredClone(rx), sub: structuredClone(rx),
    fieldStatus: Object.fromEntries(paths.map((path) => [path, {
      storePath: path, observed: true, freshness: 'fresh', availability: 'available', lastObservedMonotonic: 10,
    }])),
  } as ServerState;
  const caps = {
    model: 'fixture', scope: true, audio: false, tx: false,
    stateContractVersion: 1, providerGeneration: 1,
    capabilities: ['scope', 'filter_width', 'if_shift', 'data_mode'],
    receivers: 1, vfoScheme: 'single', freqRanges: [], modes: ['USB', 'LSB', 'AM'], filters: ['FIL1', 'FIL2'],
    // MOR-2497 (post-#3519): the PBT derived path also needs the per-mode
    // lattice step — declared here at the SSB value the pinned literals below
    // (raw 140 → +100 Hz) are written against. Ignored by the pre-change
    // conversion, load-bearing after it. LSB because the mode-change tests
    // retune there.
    filterConfig: {
      USB: { defaults: [2400], fixed: true, pbtStepHz: 50 },
      LSB: { defaults: [2400], fixed: true, pbtStepHz: 50 },
    },
    controls: { pbt_inner: { raw_min: 0, raw_max: 255, raw_center: 128, display_min: -1200, display_max: 1200 } },
    txBands: [], audioConfig: { sampleRate: 48000, channels: 1, codecs: [] },
    webrtc: { available: false, enabled: false },
  } as Capabilities;
  const input: ScopePassbandDisplayInput = { state, caps,
    selection: { receiver: 'MAIN', slot: 'single' }, session: { state: 'connected', epoch: 1 }, frame: null };
  receipt(input); return input;
}
function receipt(input: ScopePassbandDisplayInput, sequence = 1, overrides: Record<string, unknown> = {}): void {
  const receiver = input.selection?.receiver === 'SUB' ? 1 : 0;
  const generation = input.caps!.providerGeneration;
  if (typeof generation !== 'number') throw new Error('fixture generation required');
  const pixels = overrides.pixels instanceof Uint8Array ? overrides.pixels : new Uint8Array([10, 128, 200]);
  const buffer = scopeWire({ sourceId: 'scope', receiver: typeof overrides.receiver === 'number' ? overrides.receiver : receiver,
    viewStartHz: typeof overrides.startFreq === 'number' ? overrides.startFreq : 14_000_000,
    viewEndHz: typeof overrides.endFreq === 'number' ? overrides.endFreq : 14_100_000,
    outOfRange: overrides.outOfRange === true, pixels: pixels.length > 0 ? pixels : null });
  const ingress = ingressScopeFrame(buffer, { source: 'radio', sourceId: 'scope', epoch: 1,
    mode: typeof overrides.mode === 'number' ? overrides.mode : 0 });
  if (!ingress.ok) throw new Error(`invalid fixture: ${ingress.reason}`);
  input.frame = Object.freeze({ source: 'hardware', receiver, providerGeneration: generation,
    transportEpoch: 1, demanded: true, transport: 'connected', geometry: ingress.geometry, receipt: sequence });

}
/**
 * MOR-4213: a change reaches the projector the way production delivers it —
 * a NEW state snapshot built by the client's delta applier, a NEW capabilities
 * body — never an in-place edit of the snapshot it already saw.
 */
function edit(input: ScopePassbandDisplayInput, change: (state: ServerState) => void): void {
  input.state = editState(input.state!, change);
}
function editCapabilities(input: ScopePassbandDisplayInput, change: (caps: Capabilities) => void): void {
  input.caps = editCaps(input.caps!, change);
}
function status(input: ScopePassbandDisplayInput, path: string, patch: Partial<FieldStatus>): void {
  edit(input, (s) => { Object.assign(s.fieldStatus![path], patch); });
}
function stale(input: ScopePassbandDisplayInput, path: string): void {
  status(input, path, { freshness: 'stale', availability: 'stale' });
}
function renew(input: ScopePassbandDisplayInput, marker = 11, sequence = 2): void {
  edit(input, (s) => {
    for (const leaf of Object.values(s.fieldStatus!)) Object.assign(leaf,
      { observed: true, freshness: 'fresh', availability: 'available', lastObservedMonotonic: marker });
  });
  const f = input.frame?.geometry;
  receipt(input, sequence, f ? { mode: f.mode, startFreq: f.view.startHz, endFreq: f.view.endHz } : {});
}
const project = (input: ScopePassbandDisplayInput, previous = EMPTY_SCOPE_PASSBAND_DISPLAY) =>
  projectScopePassbandDisplay(previous, input);
function tuple(result: ScopePassbandDisplayState) {
  if (result.display.state !== 'current' && result.display.state !== 'stale') throw new Error('no tuple');
  return result.display.tuple;
}
function pbt(input: ScopePassbandDisplayInput): void {
  editCapabilities(input, (c) => {
    c.capabilities = c.capabilities.filter((tag) => tag !== 'if_shift').concat('pbt');
  });
}
/** MOR-2513: wire leaves are nullable; the transition fixtures carry an
 * observed frequency — throw (never fabricate) if that ever changes. */
function nudgeFrequency(input: ScopePassbandDisplayInput, delta = 100): void {
  edit(input, (s) => {
    const rx = s.main;
    if (typeof rx?.freqHz !== 'number') throw new Error('fixture main.freqHz observed');
    rx.freqHz += delta;
  });
}
function slotted(input: ScopePassbandDisplayInput): void {
  editCapabilities(input, (c) => { c.vfoScheme = 'ab'; }); input.selection = { receiver: 'MAIN', slot: 'A' };
}

function dual(input: ScopePassbandDisplayInput): void {
  editCapabilities(input, (c) => {
    c.vfoScheme = 'ab_shared'; c.receivers = 2; c.capabilities = [...c.capabilities, 'dual_rx'];
  });
}

function banded(): ScopePassbandDisplayInput {
  const input = fixture(); slotted(input); pbt(input);
  editCapabilities(input, (c) => { c.freqRanges = structuredClone(IC7300_CAPABILITIES.freqRanges); });
  receipt(input, 1, { startFreq: 14_024_000, endFreq: 14_124_000 });
  return input;
}
function tune(input: ScopePassbandDisplayInput, frequency: number, marker: number, mirror = true): void {
  edit(input, (s) => { s.main!.vfoA!.freqHz = frequency; });
  status(input, 'main.vfoA.freqHz', { lastObservedMonotonic: marker });
  if (mirror) {
    edit(input, (s) => { s.main!.freqHz = frequency; });
    status(input, 'main.freqHz', { lastObservedMonotonic: marker });
  }
}

describe('confirmed VFO selection recovery (MOR-2441)', () => {
  function select(input: ScopePassbandDisplayInput, slot: 'A' | 'B', marker: number): void {
    input.selection!.slot = slot; edit(input, (s) => { s.main!.activeSlot = slot; });
    status(input, 'main.activeSlot', { lastObservedMonotonic: marker });
  }
  it.each(['batched', 'width-first', 'tuple-first'])(
    'captures the first complete post-selection geometry: %s', (order) => {
      const input = banded(); let result = project(input);
      select(input, 'B', 20);
      edit(input, (s) => { s.main!.vfoB!.freqHz = 14_090_000; });
      status(input, 'main.vfoB.freqHz', { observed: false });
      if (order !== 'tuple-first') {
        edit(input, (s) => { s.main!.filterWidth = 1800; });
        status(input, 'main.filterWidth', { lastObservedMonotonic: 20.1 });
      }
      if (order !== 'batched') {
        receipt(input, 2); result = project(input, result);
        expect(result.display.state).toBe('unknown');
      }
      edit(input, (s) => { s.main!.freqHz = s.main!.vfoB!.freqHz; });
      status(input, 'main.vfoB.freqHz', { observed: true, lastObservedMonotonic: 20.2 });
      status(input, 'main.freqHz', { lastObservedMonotonic: 20.2 });
      if (order === 'batched') {
        for (const leaf of ['pbtInner', 'pbtOuter']) status(input, `main.${leaf}`, { lastObservedMonotonic: 20.3 });
      }
      receipt(input, 3, { startFreq: 14_040_000, endFreq: 14_140_000 });
      result = project(input, result);
      if (order !== 'batched') {
        expect(result.display.state).toBe('unknown');
        status(input, 'main.activeSlot', { lastObservedMonotonic: 21 });
        edit(input, (s) => { s.main!.filterWidth = 1800; });
        if (order === 'tuple-first') status(input, 'main.filterWidth', { lastObservedMonotonic: 20.4 });
        for (const [index, leaf] of ['pbtInner', 'pbtOuter'].entries()) {
          status(input, `main.${leaf}`, { lastObservedMonotonic: 20.5 + index / 10 });
          receipt(input, 4 + index, { startFreq: 14_040_000, endFreq: 14_140_000 });
          result = project(input, result);
          if (index === 0) expect(result.display.state).toBe('unknown');
        }
      }
      expect(result.display.state).toBe('current');
      expect(tuple(result)).toMatchObject({ frequencyHz: 14_090_000, widthHz: 1800 });
    });
  it.each(['mode', 'filter', 'DATA', 'session', 'provider', 'frame', 'regression'])(
    'retires an interrupted selection recovery on %s change', (change) => {
      const input = banded(); let result = project(input);
      select(input, 'B', 20); receipt(input, 2); result = project(input, result);
      status(input, 'main.filterWidth', { lastObservedMonotonic: 20.1 });
      receipt(input, 3); result = project(input, result);
      if (change === 'mode') { edit(input, (s) => { s.main!.mode = s.main!.vfoB!.mode = 'LSB'; }); status(input, 'main.mode', { lastObservedMonotonic: 21 }); status(input, 'main.vfoB.mode', { lastObservedMonotonic: 21 }); }
      if (change === 'filter') { edit(input, (s) => { s.main!.filter = s.main!.vfoB!.filterNum = 2; }); status(input, 'main.filter', { lastObservedMonotonic: 21 }); status(input, 'main.vfoB.filterNum', { lastObservedMonotonic: 21 }); }
      if (change === 'DATA') { edit(input, (s) => { s.main!.dataMode = 1; }); status(input, 'main.dataMode', { lastObservedMonotonic: 21 }); }
      if (change === 'session') input.session!.epoch = 2;
      if (change === 'provider') {
        edit(input, (s) => { s.providerGeneration = 2; });
        editCapabilities(input, (c) => { c.providerGeneration = 2; });
      }
      if (change === 'regression') status(input, 'main.activeSlot', { lastObservedMonotonic: 19 });
      receipt(input, 4, change === 'frame' ? { mode: 1 } : {});
      result = project(input, result); expect(result.display.state).toBe('unknown');
      expect(result.selectionRecovery).toBeNull();
      for (const leaf of ['pbtInner', 'pbtOuter']) status(input, `main.${leaf}`, { lastObservedMonotonic: 22 });
      receipt(input, 5, change === 'frame' ? { mode: 1 } : {});
      expect(project(input, result).display.state).toBe('unknown');
    });
  it('keeps an interrupted selection recovery across band-edge tuning', () => {
    const input = banded(); let result = project(input);
    select(input, 'B', 20); receipt(input, 2); result = project(input, result);
    status(input, 'main.filterWidth', { lastObservedMonotonic: 20.1 });
    receipt(input, 3); result = project(input, result);
    expect(result.selectionRecovery).not.toBeNull();
    edit(input, (s) => { s.main!.freqHz = s.main!.vfoB!.freqHz = 14_367_000; });
    status(input, 'main.freqHz', { lastObservedMonotonic: 21 });
    status(input, 'main.vfoB.freqHz', { lastObservedMonotonic: 21 });
    receipt(input, 4, { startFreq: 14_317_000, endFreq: 14_417_000 });
    result = project(input, result);
    expect(result.display.state).toBe('unknown');
    expect(result.selectionRecovery).not.toBeNull();
    for (const leaf of ['pbtInner', 'pbtOuter']) status(input, `main.${leaf}`, { lastObservedMonotonic: 22 });
    receipt(input, 5, { startFreq: 14_317_000, endFreq: 14_417_000 });
    result = project(input, result);
    expect(result.display.state).toBe('current');
    expect(tuple(result)).toMatchObject({ frequencyHz: 14_367_000, widthHz: 2400 });
  });
  it('requires the selected axis and retires invalid frames during recovery', () => {
    const input = banded(); let result = project(input);
    select(input, 'B', 20);
    edit(input, (s) => { s.main!.freqHz = s.main!.vfoB!.freqHz = 14_090_000; });
    for (const path of ['main.freqHz', 'main.vfoB.freqHz', 'main.filterWidth', 'main.pbtInner', 'main.pbtOuter']) {
      status(input, path, { lastObservedMonotonic: 20.1 });
    }
    receipt(input, 2); result = project(input, result);
    expect(result.display.state).toBe('unknown');
    input.frame = { ...input.frame!, geometry: null };
    result = project(input, result); expect(result.selectionRecovery).toBeNull();
    receipt(input, 3, { startFreq: 14_040_000, endFreq: 14_140_000 });
    result = project(input, result); expect(result.display.state).toBe('unknown');
    for (const leaf of ['filterWidth', 'pbtInner', 'pbtOuter']) status(input, `main.${leaf}`, { lastObservedMonotonic: 21 });
    receipt(input, 4, { startFreq: 14_040_000, endFreq: 14_140_000 });
    expect(project(input, result).display.state).toBe('current');
  });
  it('does not let a contradicted slot marker replace the accepted selection boundary', () => {
    const input = banded(); let result = project(input);
    select(input, 'B', 10); receipt(input, 2); result = project(input, result);
    expect(result.selection?.slot).toBe('A');
    for (const leaf of ['filterWidth', 'pbtInner', 'pbtOuter']) status(input, `main.${leaf}`, { lastObservedMonotonic: 11 });
    select(input, 'B', 12); receipt(input, 3, { startFreq: 14_024_000, endFreq: 14_124_000 });
    result = project(input, result); expect(result.display.state).toBe('unknown');
    for (const leaf of ['filterWidth', 'pbtInner', 'pbtOuter']) status(input, `main.${leaf}`, { lastObservedMonotonic: 13 });
    receipt(input, 4, { startFreq: 14_024_000, endFreq: 14_124_000 });
    expect(project(input, result).display.state).toBe('current');
  });
  it('rejects pre-selection geometry and geometry from a superseded selection', () => {
    const input = banded(); let result = project(input);
    select(input, 'B', 20); receipt(input, 2); result = project(input, result);
    expect(result.display.state).toBe('unknown');
    for (const leaf of ['filterWidth', 'pbtInner', 'pbtOuter']) status(input, `main.${leaf}`, { lastObservedMonotonic: 20.1 });
    select(input, 'A', 21); receipt(input, 3); result = project(input, result);
    expect(result.display.state).toBe('unknown');
    receipt(input, 4); result = project(input, result);
    expect(result.display.state).toBe('unknown');
    for (const leaf of ['filterWidth', 'pbtInner', 'pbtOuter']) status(input, `main.${leaf}`, { lastObservedMonotonic: 21.1 });
    receipt(input, 5, { startFreq: 14_024_000, endFreq: 14_124_000 }); result = project(input, result);
    expect(result.display.state).toBe('current');
  });
});

describe('confirmed tuning display continuity (MOR-2437)', () => {
  it('holds the last qualified tuple through a stale frequency gap and recovers while tuning continues', () => {
    const input = banded();
    let result = project(input);
    tune(input, 14_100_000, 11);
    receipt(input, 2, { startFreq: 14_050_000, endFreq: 14_150_000 });
    result = project(input, result);
    renew(input, 16, 3);
    result = project(input, result);
    expect(result.display.state).toBe('current');
    expect(tuple(result).frequencyHz).toBe(14_100_000);

    for (const path of ['main.freqHz', 'main.vfoA.freqHz', 'main.mode', 'main.vfoA.mode']) stale(input, path);
    let sequence = 3;
    let holdFloors: ScopePassbandDisplayState['floors'] = null;
    for (let step = 0; step < 10; step += 1) {
      const frequency = step % 2 === 0 ? 14_101_000 : 14_100_000;
      tune(input, frequency, 20 + step);
      if (step === 3) status(input, 'main.filterWidth', { lastObservedMonotonic: 24 });
      if (step === 7) status(input, 'main.pbtInner', { lastObservedMonotonic: 28 });
      receipt(input, ++sequence, { startFreq: frequency - 50_000, endFreq: frequency + 50_000 });
      result = project(input, result);
      expect(result.display).toMatchObject({ state: 'stale', translated: true,
        tuple: { frequencyHz: 14_100_000, startHz: frequency - 50_000, endHz: frequency + 50_000 } });
      if (step === 0) {
        holdFloors = result.floors;
        const disconnected = structuredClone(input);
        disconnected.session = { state: 'disconnected', epoch: 1 };
        expect(project(disconnected, result).display.state).toBe('unknown');
        const frameLost = structuredClone(input); frameLost.frame = null;
        expect(project(frameLost, result).display.state).toBe('unknown');
      } else expect(result.floors).toEqual(holdFloors);
    }

    for (const path of ['main.freqHz', 'main.vfoA.freqHz', 'main.mode', 'main.vfoA.mode']) {
      status(input, path, { freshness: 'fresh', availability: 'available', lastObservedMonotonic: 40 });
    }
    for (let step = 0; step < 8; step += 1) {
      const frequency = step % 2 === 0 ? 14_101_000 : 14_100_000;
      tune(input, frequency, 41 + step);
      if (step === 7) status(input, 'main.pbtOuter', { lastObservedMonotonic: 48 });
      receipt(input, ++sequence, { startFreq: frequency - 50_000, endFreq: frequency + 50_000 });
      result = project(input, result);
      expect(result.display.state).toBe(step === 7 ? 'current' : 'stale');
      expect(tuple(result).frequencyHz).toBe(frequency);
      if (step < 7) expect(result.floors).toEqual(holdFloors);
    }
    expect(result.floors).toBeNull();
  });

  it('keeps one translated stale shape through repeated steps and staggered 5s geometry observations', () => {
    const input = banded(); let result = project(input);
    for (const [frequency, marker] of [[14_075_000, 11], [14_076_000, 12]]) {
      tune(input, frequency, marker); result = project(input, result);
      expect(result.display.state).toBe('stale');
      expect(tuple(result)).toMatchObject({ frequencyHz: frequency, widthHz: 2400, shiftHz: 0 });
      receipt(input, marker, { startFreq: frequency - 50_000, endFreq: frequency + 50_000 });
      result = project(input, result); expect(result.display.state).toBe('stale');
    }
    for (const [path, marker] of [['main.filterWidth', 15], ['main.pbtInner', 15.1], ['main.pbtOuter', 15.2]] as const) {
      status(input, path, { lastObservedMonotonic: marker }); result = project(input, result);
      expect(result.display.state).toBe(path === 'main.pbtOuter' ? 'current' : 'stale');
      expect(tuple(result)).toMatchObject({ frequencyHz: 14_076_000, widthHz: 2400, shiftHz: 0 });
    }
    expect(result.floors).toBeNull();
  });
  it.each(['slot-first', 'active-first'])('survives %s frequency acknowledgments and lagging CENTER frames', (order) => {
    const input = banded(); let result = project(input);
    if (order === 'slot-first') tune(input, 14_075_000, 11, false);
    else { edit(input, (s) => { s.main!.freqHz = 14_075_000; }); status(input, 'main.freqHz', { lastObservedMonotonic: 11 }); }
    result = project(input, result);
    expect(result.display.state).toBe('stale'); expect(tuple(result).frequencyHz).toBe(14_075_000);
    tune(input, 14_075_000, 12); result = project(input, result);
    expect(result.display.state).toBe('stale');
    receipt(input, 2, { startFreq: 14_025_000, endFreq: 14_125_000 }); result = project(input, result);
    expect(result.display.state).toBe('stale'); expect(tuple(result).frequencyHz).toBe(14_075_000);
    renew(input, 16, 3); expect(project(input, result).display.state).toBe('current');
  });
  it('ignores pending targets and holds rather than follows a stale frequency observation', () => {
    const input = banded(); const current = project(input);
    edit(input, (s) => { Object.assign(s, { pending: { frequencyHz: 14_090_000 } }); });
    expect(tuple(project(input, current)).frequencyHz).toBe(14_074_000);
    tune(input, 14_075_000, 11); stale(input, 'main.vfoA.freqHz');
    const held = project(input, current);
    expect(held.display).toMatchObject({ state: 'stale', translated: true });
    expect(tuple(held).frequencyHz).toBe(14_074_000);
  });
  const hard: [string, (i: ScopePassbandDisplayInput) => void][] = [
    ['provider', (i) => {
      edit(i, (s) => { s.providerGeneration = 2; });
      editCapabilities(i, (c) => { c.providerGeneration = 2; }); receipt(i, 3);
    }],
    ['caps', (i) => { editCapabilities(i, (c) => { c.filterWidthMax = 9000; }); }],
    ['control epoch', (i) => { i.session!.epoch = 2; }],
    ['scope epoch', (i) => { i.frame = { ...i.frame!, transportEpoch: 2 }; }],
    ['receiver', (i) => { edit(i, (s) => { s.active = 'SUB'; }); }],
    ['slot', (i) => { i.selection!.slot = 'B'; edit(i, (s) => { s.main!.activeSlot = 'B'; }); status(i, 'main.activeSlot', { lastObservedMonotonic: 12 }); }],
    ['mode', (i) => { edit(i, (s) => { s.main!.mode = 'LSB'; s.main!.vfoA!.mode = 'LSB'; }); status(i, 'main.mode', { lastObservedMonotonic: 12 }); status(i, 'main.vfoA.mode', { lastObservedMonotonic: 12 }); }],
    ['filter', (i) => { edit(i, (s) => { s.main!.filter = 2; s.main!.vfoA!.filterNum = 2; }); status(i, 'main.filter', { lastObservedMonotonic: 12 }); status(i, 'main.vfoA.filterNum', { lastObservedMonotonic: 12 }); }],
    ['DATA', (i) => { edit(i, (s) => { s.main!.dataMode = 1; }); status(i, 'main.dataMode', { lastObservedMonotonic: 12 }); }],
    ['frame mode', (i) => receipt(i, 3, { mode: 1 })],
    ['span', (i) => receipt(i, 3, { endFreq: 14_200_000 })],
    ['band definitions', (i) => { editCapabilities(i, (c) => { c.freqRanges[0].bands![5].end += 100; }); }],
    ['null frame', (i) => { i.frame = null; }],
    ['500ms frame silence', (i) => { i.frame = { ...i.frame!, geometry: { ...i.frame!.geometry!, freshness: 'stale' } }; }],
    ['off', (i) => { i.frame = { ...i.frame!, demanded: false }; }],
  ];
  it.each(hard)('retires a translated tuple on %s without replay after return', (_name, change) => {
    const input = banded(); let result = project(input); tune(input, 14_075_000, 11); result = project(input, result);
    expect(result.display.state).toBe('stale');
    const saved = structuredClone(input); change(input); result = project(input, result);
    expect(result.display.state).toBe('unknown');
    result = project(saved, result); expect(result.display.state).toBe('unknown');
    result = project(saved, result); expect(result.display.state).toBe('unknown');
    renew(saved, 20, 4);
    if (_name === 'slot') {
      status(saved, 'main.activeSlot', { lastObservedMonotonic: 19 });
      receipt(saved, 4, { startFreq: 14_025_000, endFreq: 14_125_000 });
    }
    expect(project(saved, result).display.state).toBe('current');
  });
  it.each(['filterWidth', 'pbtInner', 'pbtOuter'] as const)('never mixes a partial changed %s with retained shape', (leaf) => {
    const input = banded(); let result = project(input); tune(input, 14_075_000, 11); result = project(input, result);
    // MOR-2497 step 2: the filter width is quantised now -- a PBT reading only
    // exists on a lattice of 50 Hz steps -- so the perturbation is one legal
    // step rather than an arbitrary +10, which would leave 2410 Hz, a width no
    // lattice can be formed from. The subject here is continuity across a
    // partial change, not the width domain; the width domain is pinned by
    // 'refuses a PBT reading at a width off the 50 Hz lattice' below.
    edit(input, (s) => { s.main![leaf]! += leaf === 'filterWidth' ? 50 : 10; });
    status(input, `main.${leaf}`, { lastObservedMonotonic: 15 });
    result = project(input, result); expect(result.display.state).toBe('unknown');
    receipt(input, 3); result = project(input, result); expect(result.display.state).toBe('unknown');
    renew(input, 16, 4); expect(project(input, result).display.state).toBe('current');
  });
  it.each([0, 1, 2, 3])('waits for matching CENTER axis but allows observed frequency within fixed mode %s', (mode) => {
    const input = banded(); receipt(input, 1, { mode, startFreq: 14_024_000, endFreq: 14_124_000 });
    let result = project(input); tune(input, 14_075_000, 11); result = project(input, result);
    renew(input, 16, 2); result = project(input, result);
    expect(result.display.state).toBe(mode === 1 || mode === 3 ? 'current' : 'stale');
    if (mode === 0 || mode === 2) {
      receipt(input, 3, { mode, startFreq: 14_025_000, endFreq: 14_125_000 });
      result = project(input, result); expect(result.display.state).toBe('current');
    }
  });
  it('keeps frequency source-backed when the next CENTER frame precedes both frequency acknowledgments', () => {
    const input = banded(); let result = project(input);
    receipt(input, 2, { startFreq: 14_025_000, endFreq: 14_125_000 }); result = project(input, result);
    expect(result.display.state).toBe('stale'); expect(tuple(result).frequencyHz).toBe(14_074_000);
    tune(input, 14_075_000, 11, false); result = project(input, result);
    expect(result.display.state).toBe('stale'); expect(tuple(result).frequencyHz).toBe(14_075_000);
    tune(input, 14_075_000, 12); result = project(input, result); expect(result.display.state).toBe('stale');
    renew(input, 16, 3); expect(project(input, result).display.state).toBe('current');
  });
});

describe('band-edge tuning continuity (MOR-2367)', () => {
  it('translates the last coherent geometry across the band edge instead of retiring', () => {
    const input = banded();
    let result = project(input);
    expect(result.display.state).toBe('current');
    tune(input, 14_318_000, 11);
    receipt(input, 2, { startFreq: 14_268_000, endFreq: 14_368_000 });
    result = project(input, result);
    expect(result.display.state).toBe('stale');
    tune(input, 14_367_000, 12);
    receipt(input, 3, { startFreq: 14_317_000, endFreq: 14_417_000 });
    result = project(input, result);
    expect(result.display).toMatchObject({ state: 'stale', translated: true });
    expect(tuple(result)).toMatchObject({ frequencyHz: 14_367_000, widthHz: 2400, shiftHz: 0 });
  });
  it('translates the geometry back into the band on the return tuning', () => {
    const input = banded();
    let result = project(input);
    tune(input, 14_318_000, 11);
    receipt(input, 2, { startFreq: 14_268_000, endFreq: 14_368_000 });
    result = project(input, result);
    tune(input, 14_367_000, 12);
    receipt(input, 3, { startFreq: 14_317_000, endFreq: 14_417_000 });
    result = project(input, result);
    tune(input, 14_200_000, 13);
    receipt(input, 4, { startFreq: 14_150_000, endFreq: 14_250_000 });
    result = project(input, result);
    expect(result.display).toMatchObject({ state: 'stale', translated: true });
    expect(tuple(result)).toMatchObject({ frequencyHz: 14_200_000, widthHz: 2400, shiftHz: 0 });
    renew(input, 16, 5);
    expect(project(input, result).display.state).toBe('current');
  });
  it('translates a tuning step taken entirely outside every profile band', () => {
    const input = banded();
    let result = project(input);
    tune(input, 14_360_000, 11);
    receipt(input, 2, { startFreq: 14_310_000, endFreq: 14_410_000 });
    result = project(input, result);
    expect(result.display).toMatchObject({ state: 'stale', translated: true });
    tune(input, 14_367_000, 12);
    receipt(input, 3, { startFreq: 14_317_000, endFreq: 14_417_000 });
    result = project(input, result);
    expect(result.display).toMatchObject({ state: 'stale', translated: true });
    expect(tuple(result)).toMatchObject({ frequencyHz: 14_367_000, widthHz: 2400, shiftHz: 0 });
  });
  it('translates the step back into the band after several out-of-band steps', () => {
    const input = banded();
    let result = project(input);
    tune(input, 14_360_000, 11);
    receipt(input, 2, { startFreq: 14_310_000, endFreq: 14_410_000 });
    result = project(input, result);
    tune(input, 14_367_000, 12);
    receipt(input, 3, { startFreq: 14_317_000, endFreq: 14_417_000 });
    result = project(input, result);
    tune(input, 14_345_000, 13);
    receipt(input, 4, { startFreq: 14_295_000, endFreq: 14_395_000 });
    result = project(input, result);
    expect(result.display).toMatchObject({ state: 'stale', translated: true });
    expect(tuple(result)).toMatchObject({ frequencyHz: 14_345_000, widthHz: 2400, shiftHz: 0 });
  });
  it('never blanks through a burst that crosses the edge and continues outside', () => {
    const input = banded();
    let result = project(input);
    const frequencies = [14_340_000, 14_345_000, 14_351_000, 14_358_000, 14_367_000];
    for (const [index, frequency] of frequencies.entries()) {
      tune(input, frequency, 11 + index);
      receipt(input, 2 + index, { startFreq: frequency - 50_000, endFreq: frequency + 50_000 });
      result = project(input, result);
      expect(result.display).toMatchObject({ state: 'stale', translated: true });
      expect(tuple(result)).toMatchObject({ frequencyHz: frequency, widthHz: 2400, shiftHz: 0 });
    }
  });
  it.each([['another band', 18_100_000], ['outside all bands', 15_000_000]])(
    'translates a single confirmed jump to %s when mode and filter are unchanged', (_name, frequency) => {
      const input = banded();
      let result = project(input);
      tune(input, frequency, 11);
      receipt(input, 2, { startFreq: frequency - 50_000, endFreq: frequency + 50_000 });
      result = project(input, result);
      expect(result.display).toMatchObject({ state: 'stale', translated: true });
      expect(tuple(result)).toMatchObject({ frequencyHz: frequency, widthHz: 2400, shiftHz: 0 });
    });
  it('recaptures once after a genuine hard retirement while 250ms tuning continues', () => {
    const input = banded();
    let result = project(input);
    expect(result.display.state).toBe('current');

    edit(input, (s) => { s.main!.mode = s.main!.vfoA!.mode = 'LSB'; });
    status(input, 'main.mode', { lastObservedMonotonic: 11 });
    status(input, 'main.vfoA.mode', { lastObservedMonotonic: 11 });
    result = project(input, result);
    expect(result.display).toEqual({ state: 'unknown', reason: 'retired' });

    edit(input, (s) => { s.main!.mode = s.main!.vfoA!.mode = 'USB'; });
    status(input, 'main.mode', { lastObservedMonotonic: 12 });
    status(input, 'main.vfoA.mode', { lastObservedMonotonic: 12 });
    receipt(input, 2, { startFreq: 14_024_000, endFreq: 14_124_000 });
    result = project(input, result);
    expect(result.display.state).toBe('unknown');

    const retiredFloors = result.floors;
    expect(retiredFloors).not.toBeNull();
    let sequence = 2;
    for (let step = 0; step < 8; step += 1) {
      const frequency = 14_074_000 + step * 250;
      tune(input, frequency, 20 + step * 0.25);
      if (step === 2) status(input, 'main.filterWidth', { lastObservedMonotonic: 25 });
      if (step === 4) status(input, 'main.pbtInner', { lastObservedMonotonic: 30 });
      if (step === 6) status(input, 'main.pbtOuter', { lastObservedMonotonic: 35 });
      receipt(input, ++sequence, { startFreq: frequency - 50_000, endFreq: frequency + 50_000 });
      result = project(input, result);
      if (step < 6) {
        expect(result.display.state).toBe('unknown');
        expect(result.floors).toEqual(retiredFloors);
      } else if (step === 6) {
        expect(result.display).toMatchObject({ state: 'current', tuple: {
          frequencyHz: frequency, widthHz: 2400, shiftHz: 0,
        } });
        expect(result.floors).toBeNull();
      }
    }
    expect(result.display).toMatchObject({ state: 'stale', translated: true, tuple: {
      frequencyHz: 14_075_750, widthHz: 2400, shiftHz: 0,
    } });
    expect(toSpectrumAuthority(input.state, input.caps)).toMatchObject({
      receiver: 0, mode: 'USB', filter: 'FIL1', filterWidthHz: 2400, ifShiftHz: 0, rule: null,
    });
  });

  it.each(['mode', 'filter'])('still retires a band-edge crossing when the %s changes', (change) => {
    const input = banded();
    let result = project(input);
    tune(input, 14_367_000, 11);
    if (change === 'mode') {
      edit(input, (s) => { s.main!.mode = s.main!.vfoA!.mode = 'LSB'; });
      status(input, 'main.mode', { lastObservedMonotonic: 11 });
      status(input, 'main.vfoA.mode', { lastObservedMonotonic: 11 });
    } else {
      edit(input, (s) => { s.main!.filter = 2; s.main!.vfoA!.filterNum = 2; });
      status(input, 'main.filter', { lastObservedMonotonic: 11 });
      status(input, 'main.vfoA.filterNum', { lastObservedMonotonic: 11 });
    }
    receipt(input, 2, { startFreq: 14_317_000, endFreq: 14_417_000 });
    result = project(input, result);
    expect(result.display.state).toBe('unknown');
  });
});

describe('coherent RF passband display', () => {
  it.each(['USB', 'LSB', 'AM'])('retains complete %s edges through leaf/ancestor stale and equal current', (mode) => {
    const input = fixture(); edit(input, (s) => { s.main!.mode = mode; });
    let result = project(input); const captured = tuple(result);
    const edges = getPassbandEdgesHz(captured.mode, captured.widthHz, captured.shiftHz);
    let sequence = 1;
    for (const path of ['main.filterWidth', 'main.ifShift', 'main']) {
      stale(input, path); receipt(input, ++sequence); result = project(input, result);
      expect(result.display.state).toBe('stale'); expect(tuple(result)).toBe(captured);
      expect(getPassbandEdgesHz(captured.mode, captured.widthHz, captured.shiftHz)).toEqual(edges);
      renew(input, 11, ++sequence); result = project(input, result);
      expect(result.display.state).toBe('current'); expect(tuple(result)).toBe(captured);
    }
    expect(captured.shiftHz).toBe(0); expect(toSpectrumAuthority(input.state, input.caps)!.rule).toBeNull();
  });
  it.each(['main.filterWidth', 'main.ifShift', 'main.mode', 'main'])('cannot seed first-stale %s', (path) => {
    const input = fixture(); stale(input, path); expect(project(input).display.state).toBe('unknown');
  });
  it('seeds from provider-confirmed held readback when poll freshness windows do not overlap', () => {
    const input = fixture(); pbt(input);
    for (const path of ['main.freqHz', 'main.mode', 'main.filterWidth', 'main.pbtInner', 'main.pbtOuter']) {
      stale(input, path);
      status(input, path, { quality: ['confirmed'] });
    }
    const result = project(input);
    expect(result.display).toMatchObject({
      state: 'current',
      tuple: { frequencyHz: 14_074_000, mode: 'USB', widthHz: 2400, shiftHz: 0 },
    });
  });
  const invalid: [string, (i: ScopePassbandDisplayInput) => void][] = [
    ['zero width', (i) => { edit(i, (s) => { s.main!.filterWidth = 0; }); }],
    ['zero frequency', (i) => { edit(i, (s) => { s.main!.freqHz = 0; }); }],
    ['NaN shift', (i) => { edit(i, (s) => { s.main!.ifShift = NaN; }); }],
    ['empty mode', (i) => { edit(i, (s) => { s.main!.mode = ' '; }); }],
    ['empty filter', (i) => { edit(i, (s) => { s.main!.filter = null; }); }],
    ['missing leaf', (i) => { edit(i, (s) => { delete s.fieldStatus!['main.ifShift']; }); }],
    ['unobserved', (i) => status(i, 'main.ifShift', { observed: false })],
    ['ancestor veto', (i) => status(i, 'main', { observed: false })],
    ['NaN marker', (i) => status(i, 'main.ifShift', { lastObservedMonotonic: NaN })],
    ['negative marker', (i) => status(i, 'main.ifShift', { lastObservedMonotonic: -1 })],
    ['missing marker', (i) => status(i, 'main.ifShift', { lastObservedMonotonic: undefined })],
    ['invalid freshness', (i) => status(i, 'main.ifShift', { freshness: 'unknown' })],
    ['missing selection', (i) => { i.selection = null; }],
    ['unknown slot', (i) => { slotted(i); edit(i, (s) => { s.main!.activeSlot = '?'; }); }],
    ['topology mismatch', (i) => { editCapabilities(i, (c) => { c.receivers = 2; }); }],
    ['missing generation', (i) => { edit(i, (s) => { delete s.providerGeneration; }); }],
    ['caps mismatch', (i) => { editCapabilities(i, (c) => { c.providerGeneration = 2; }); }],
    ['missing session', (i) => { i.session = null; }],
    ['null frame', (i) => { i.frame = null; }],
  ];
  it.each(invalid)('rejects %s and retires existing tuple', (_name, change) => {
    const input = fixture(); const current = project(input); change(input);
    expect(project(input).display.state).toBe('unknown'); expect(project(input, current).display.state).toBe('unknown');
  });
  it.each(['filter_width', 'if_shift'])('reports absent %s as unsupported', (tag) => {
    const input = fixture(); const current = project(input);
    editCapabilities(input, (c) => { c.capabilities = c.capabilities.filter((value) => value !== tag); });
    expect(project(input, current).display.state).toBe('unsupported');
  });
  // MOR-2497 step 2: the shift is read off the MEASURED lattice, not off the
  // profile's declared +/-1200 Hz span. The expected values are written out as
  // literals rather than recomputed through `measuredPbtRawToHz`, so that a
  // wrong model cannot satisfy this test by being wrong in both places: at the
  // fixture's 2400 Hz filter the lattice has 2400/50 + 1 = 49 positions, raw
  // 128 is the centre (0 Hz) and raw 140 snaps to the position two steps above
  // it (+100 Hz). Under the retired conversion raw 140 read 113 Hz, a value the
  // radio cannot produce -- it lies between two reachable positions.
  it.each([[128, 0], [140, 100]])('reads raw %s off the measured PBT lattice as %s Hz', (raw, expected) => {
    const input = fixture(); pbt(input); edit(input, (s) => { s.main!.pbtInner = raw; s.main!.pbtOuter = raw; });
    const current = project(input); expect(tuple(current).shiftHz).toBe(expected);
    stale(input, 'main.pbtOuter'); expect(tuple(project(input, current))).toEqual(tuple(current));
    edit(input, (s) => { s.main!.pbtInner = raw + 1; }); status(input, 'main.pbtInner', { lastObservedMonotonic: 11 });
    const retired = project(input, current); expect(retired.display.state).toBe('unknown');
    renew(input, 12, 2); expect(project(input, retired).display.state).toBe('current');
  });
  // MOR-2497 step 2: a width off the 50 Hz lattice has no PBT reading at all --
  // the measured model gives positions only at width/50 + 1 points, and 2410 Hz
  // forms none. The refusal is the same `invalid-observation` an unreadable PBT
  // leaf already produced, so nothing invents a plausible Hz for a width the
  // radio cannot actually be in.
  it('refuses a PBT reading at a width off the 50 Hz lattice', () => {
    const legal = fixture(); pbt(legal);
    expect(project(legal).display.state).toBe('current');
    const input = fixture(); pbt(input); edit(input, (s) => { s.main!.filterWidth = 2410; });
    expect(project(input).display.state).not.toBe('current');
  });
  // MOR-2497 (post-#3519): the lattice step is per MODE, read from
  // `filterConfig[mode].pbtStepHz` by BOTH this reducer and the view-model
  // adapter the strict authority derives from — `reaching current` below
  // requires `strict.ifShiftHz === shiftHz`, so each pin also proves the two
  // sites resolved the same step.
  it('reads AM off its declared 200 Hz step, not the 50 Hz SSB step, in lockstep with the view-model authority', () => {
    const input = fixture(); pbt(input);
    editCapabilities(input, (c) => {
      c.filterConfig = { ...c.filterConfig, AM: { defaults: [2400], fixed: true, pbtStepHz: 200 } };
    });
    edit(input, (s) => { s.main!.mode = 'AM'; s.main!.pbtInner = 200; s.main!.pbtOuter = 200; });
    const current = project(input);
    // Width 2400, raw 200: +800 Hz at the 200 Hz AM step (+700 at 50 Hz).
    expect(current.display.state).toBe('current');
    expect(tuple(current).shiftHz).toBe(800);
  });
  it('FM (mode without twin PBT): no PBT reading, but the passband display stays current with shift 0', () => {
    const input = fixture(); pbt(input);
    editCapabilities(input, (c) => {
      c.modes = [...c.modes!, 'FM'];
      c.filterConfig = { ...c.filterConfig, FM: { defaults: [15000], fixed: true } };
    });
    edit(input, (s) => { s.main!.mode = 'FM'; s.main!.pbtInner = 200; s.main!.pbtOuter = 200; });
    // filterWidth stays 2400 — a width that WOULD form a lattice at 50 Hz, so
    // the outcome is attributable only to the absent step, not to the width.
    // Owner ruling 2026-09-17: with no twin PBT there is no PBT to engage, so
    // the shift is the KNOWN zero of a passband that cannot be displaced —
    // the display and its overlay stay valid at the observed filter width.
    const current = project(input);
    expect(current.display.state).toBe('current');
    expect(tuple(current)).toMatchObject({ mode: 'FM', widthHz: 2400, shiftHz: 0 });
    // The PBT side of the ruling: no PBT reading or control exists in FM.
    const passband = toRadioViewModel(input.state, input.caps)?.filterPassband;
    expect(passband?.pbtInner.availability).toEqual({ structural: false, operational: false });
    expect(passband?.pbtInner.reading).toEqual({ status: 'unknown' });
    expect(passband?.ifShift.reading).toEqual({ status: 'unknown' });
  });
  it('refuses a PBT reading when no mode declares a step (legacy pre-pbtStepHz payload)', () => {
    const input = fixture(); pbt(input);
    editCapabilities(input, (c) => { for (const config of Object.values(c.filterConfig!)) delete config.pbtStepHz; });
    edit(input, (s) => { s.main!.pbtInner = 200; s.main!.pbtOuter = 200; });
    expect(project(input).display).toEqual({ state: 'unknown', reason: 'invalid-observation' });
  });
  it.each([undefined, { raw_min: 0, raw_max: 255, raw_center: 0, display_min: -1200, display_max: 1200 }])('rejects missing/invalid PBT scale', (scale) => {
    const input = fixture(); pbt(input); editCapabilities(input, (c) => { c.controls = scale ? { pbt_inner: scale } : {}; });
    expect(project(input).display.state).toBe('unsupported');
  });
  const transitions: [string, (i: ScopePassbandDisplayInput) => void][] = [
    ['generation', (i) => {
      edit(i, (s) => { s.providerGeneration = 2; });
      editCapabilities(i, (c) => { c.providerGeneration = 2; }); receipt(i, 2);
    }],
    ['capability shape', (i) => { editCapabilities(i, (c) => { c.filterConfig!.USB.defaults = [1800]; }); }],
    ['control epoch', (i) => { i.session = { state: 'connected', epoch: 2 }; }],
    ['receiver', (i) => { dual(i); edit(i, (s) => { s.active = 'SUB'; }); i.selection = { receiver: 'SUB', slot: 'single' }; receipt(i, 2); }],
    ['slot', (i) => { edit(i, (s) => { s.main!.activeSlot = 'B'; }); i.selection = { receiver: 'MAIN', slot: 'B' }; }],
    ['frequency', (i) => { nudgeFrequency(i); }],
    ['mode', (i) => { edit(i, (s) => { s.main!.mode = 'LSB'; }); }],
    ['DATA', (i) => { edit(i, (s) => { s.main!.dataMode = 1; }); }],
    ['filter', (i) => { edit(i, (s) => { s.main!.filter = 2; }); }],
    ['frame mode', (i) => receipt(i, 2, { mode: 1 })],
  ];
  it.each(transitions)('retires %s until newer geometry AND post-boundary receipt', (name, change) => {
    const input = fixture(); if (name === 'slot') slotted(input);
    if (name === 'receiver') dual(input);
    const current = project(input); expect(current.display.state).toBe('current'); change(input);
    const retired = project(input, current); expect(retired.display.state).toBe('unknown');
    expect(project(input, retired).display.state).toBe('unknown');
    const oldFrame = input.frame; renew(input, 11, 3);
    if (name === 'slot') {
      status(input, 'main.activeSlot', { lastObservedMonotonic: 10.5 });
      receipt(input, 3, { startFreq: 14_024_000, endFreq: 14_124_000 });
    }
    const nextFrame = input.frame; input.frame = oldFrame;
    expect(project(input, retired).display.state).toBe('unknown');
    input.frame = nextFrame; expect(project(input, retired).display.state).toBe('current');
  });
  // MOR-4387 (owner build-4): a ZOOM preset switch re-grants the frame
  // window while every RF fact the tuple carries (frequency, mode, width,
  // shift, frame scope mode) stays identical. The tuple's window legs
  // describe the sample plane the facts were proven against — the overlay
  // geometry reads only the RF legs — so a window-only change RESEATS the
  // tuple onto the new window instead of retiring it. Retiring cost a full
  // geometry re-observation cycle (crossedFloors waits for fresh
  // filterWidth/PBT markers), which was the owner's ~1 s passband blink.
  it.each([
    ['narrower (ZOOM+ preset switch)', { startFreq: 14_049_000, endFreq: 14_099_000 }],
    ['wider (ZOOM− preset switch)', { startFreq: 13_950_000, endFreq: 14_200_000 }],
    ['start-only', { startFreq: 14_010_000 }],
    ['end-only', { endFreq: 14_110_000 }],
  ])('reseats the tuple when the window changes %s with unchanged RF facts', (_name, window) => {
    const input = fixture();
    const current = project(input);
    expect(current.display.state).toBe('current');
    receipt(input, 2, window);
    const reseated = project(input, current);
    expect(reseated.display).toMatchObject({ state: 'current', tuple: {
      frequencyHz: 14_074_000, mode: 'USB', widthHz: 2400, shiftHz: 0,
      startHz: window.startFreq ?? 14_000_000, endHz: window.endFreq ?? 14_100_000,
    } });
    // Continuity is re-established on the new window: the next projection
    // holds without fresh geometry evidence.
    expect(project(input, reseated).display.state).toBe('current');
  });
  it.each([
    ['mode', (i: ScopePassbandDisplayInput) => { edit(i, (s) => { s.main!.mode = 'LSB'; }); }],
    ['DATA', (i: ScopePassbandDisplayInput) => { edit(i, (s) => { s.main!.dataMode = 1; }); }],
    ['filter width', (i: ScopePassbandDisplayInput) => {
      edit(i, (s) => { s.main!.filterWidth = 1800; });
      status(i, 'main.filterWidth', { lastObservedMonotonic: 11 });
    }],
    ['frame mode', undefined],
  ] as const)('still retires when %s changes together with the window', (_name, change) => {
    const input = fixture();
    const current = project(input);
    expect(current.display.state).toBe('current');
    if (change) change(input);
    receipt(input, 2, _name === 'frame mode'
      ? { startFreq: 14_049_000, endFreq: 14_099_000, mode: 1 }
      : { startFreq: 14_049_000, endFreq: 14_099_000 });
    expect(project(input, current).display.state).toBe('unknown');
  });
  it('rejects A→B→A replay even with renewed receipt and old geometry markers', () => {
    const input = fixture(); slotted(input); const a = project(input);
    edit(input, (s) => { s.main!.activeSlot = 'B'; }); input.selection = { receiver: 'MAIN', slot: 'B' };
    const b = project(input, a);
    edit(input, (s) => { s.main!.activeSlot = 'A'; }); input.selection = { receiver: 'MAIN', slot: 'A' };
    const back = project(input, b); receipt(input, 3);
    expect(project(input, back).display.state).toBe('unknown');
    renew(input, 11, 4); expect(project(input, back).display.state).toBe('current');
  });
  it.each([
    ['command_response', 'unknown'],
    ['poll_response', 'current'],
  ] as const)('treats a same-slot %s refresh as %s', (source, expected) => {
    const input = banded(); const current = project(input);
    edit(input, (s) => { s.main!.filterWidth = 1800; });
    for (const leaf of ['filterWidth', 'pbtInner', 'pbtOuter']) {
      status(input, `main.${leaf}`, { lastObservedMonotonic: 20.1 });
    }
    status(input, 'main.activeSlot', { lastObservedMonotonic: 21, source: { source } });
    for (const path of ['main.freqHz', 'main.mode', 'main.filter', 'main.dataMode',
      'main.vfoA.freqHz', 'main.vfoA.mode', 'main.vfoA.filterNum']) {
      status(input, path, { lastObservedMonotonic: 21.2 });
    }
    receipt(input, 2, { startFreq: 14_024_000, endFreq: 14_124_000 });
    const boundary = project(input, current); expect(boundary.display.state).toBe(expected);
    if (source === 'command_response') {
      for (const leaf of ['filterWidth', 'pbtInner', 'pbtOuter']) {
        status(input, `main.${leaf}`, { lastObservedMonotonic: 21.3 });
      }
      receipt(input, 3, { startFreq: 14_024_000, endFreq: 14_124_000 });
      const recovered = project(input, boundary);
      expect(recovered.display).toMatchObject({ state: 'current', tuple: { widthHz: 1800 } });
      expect(project(input, recovered).display.state).toBe('current');
    }
  });
  it('rejects partial width updates and regressing leaf/ancestor markers', () => {
    for (const path of ['main.ifShift', 'main']) {
      const input = fixture(); const current = project(input); status(input, path, { lastObservedMonotonic: 9 });
      expect(project(input, current).display.state).toBe('unknown');
    }
    const input = fixture(); const current = project(input); stale(input, 'main.ifShift');
    edit(input, (s) => { s.main!.filterWidth = 1800; }); status(input, 'main.filterWidth', { lastObservedMonotonic: 11 });
    const retired = project(input, current); expect(retired.display.state).toBe('unknown');
    renew(input, 12, 2); expect(tuple(project(input, retired)).widthHz).toBe(1800);
  });
  it('distinguishes unsupported DATA from observed zero and ignores unrelated metadata', () => {
    const input = fixture();
    editCapabilities(input, (c) => { c.capabilities = c.capabilities.filter((tag) => tag !== 'data_mode'); });
    edit(input, (s) => { delete s.fieldStatus!['main.dataMode']; }); const current = project(input);
    expect(current.display.state).toBe('current');
    input.caps = { ...input.caps!, model: 'renamed', capabilities: [...input.caps!.capabilities].reverse() };
    expect(project(input, current).display.state).toBe('current');
    editCapabilities(input, (c) => { c.capabilities.push('data_mode'); });
    edit(input, (s) => { s.fieldStatus!['main.dataMode'] = { ...s.fieldStatus!['main.mode'] }; });
    expect(project(input, current).display.state).toBe('unknown');
  });
  it.each([499, 500, -1, NaN])('honors frame age %s without another timer', (age) => {
    const input = fixture(); const current = project(input);
    input.frame = { ...input.frame!, geometry: { ...input.frame!.geometry!, freshness: age === 499 ? 'fresh' : 'stale' } };
    expect(project(input, current).display.state).toBe(age === 499 ? 'current' : 'unknown');
  });
  it.each(['ghost', 'receiver', 'source', 'transport', 'demand', 'scope epoch', 'disconnect'])(
    'invalidates %s evidence without changing command authority', (kind) => {
      const input = fixture(); const current = project(input); const strict = toSpectrumAuthority(input.state, input.caps);
      const f = input.frame!;
      if (kind === 'ghost') input.frame = { ...f, geometry: null };
      if (kind === 'receiver') input.frame = { ...f, receiver: 1 };
      if (kind === 'source') input.frame = { ...f, source: 'audio_fft' };
      if (kind === 'transport') input.frame = { ...f, transport: 'reconnecting' };
      if (kind === 'demand') input.frame = { ...f, demanded: false };
      if (kind === 'scope epoch') input.frame = { ...f, transportEpoch: 2 };
      if (kind === 'disconnect') input.session = { state: 'disconnected', epoch: 1 };
      expect(project(input, current).display.state).toBe('unknown');
      expect(toSpectrumAuthority(input.state, input.caps)).toEqual(strict);
    });
  it('does not mutate input, prior tuple, strict model, or shared pixels', () => {
    const input = fixture(); const before = JSON.stringify(input); const model = toRadioViewModel(input.state, input.caps);
    const geometry = input.frame!.geometry;
    const view = structuredClone(geometry!.view);
    const current = project(input); const previous = structuredClone(current); project(input, current);
    expect(JSON.stringify(input)).toBe(before); expect(current).toEqual(previous);
    expect(input.frame!.geometry).toBe(geometry); expect(geometry!.view).toEqual(view);
    expect(toRadioViewModel(input.state, input.caps)).toEqual(model);
    expect(Object.isFrozen(tuple(current))).toBe(true); expect(current).not.toHaveProperty('frame');
  });

  it.each([false, true])('retires unchanged pixel-less geometry (outOfRange=%s)', (outOfRange) => {
    const input = fixture(); const current = project(input);
    receipt(input, 2, { pixels: new Uint8Array(), outOfRange });
    expect(input.frame!.geometry).toMatchObject({ hasPixels: false, outOfRange });
    expect(project(input, current).display.state).toBe('unknown');
    expect(project(input).display.state).toBe('unknown');
  });

  it.each([false, true])('requires renewed readbacks and a newer selected receipt after a pixel gap (outOfRange=%s)', (outOfRange) => {
    const input = fixture(); let result = project(input);
    receipt(input, 2, { pixels: new Uint8Array(), outOfRange });
    result = project(input, result);
    receipt(input, 3); result = project(input, result);
    expect(result.display.state).toBe('unknown');
    status(input, 'main.filterWidth', { lastObservedMonotonic: 11 });
    result = project(input, result); expect(result.display.state).toBe('unknown');
    status(input, 'main.ifShift', { lastObservedMonotonic: 11 });
    receipt(input, 2); result = project(input, result);
    expect(result.display.state).toBe('unknown');
    receipt(input, 4);
    expect(project(input, result).display.state).toBe('current');
  });

  it('keeps retained-pixel out-of-range geometry admissible', () => {
    const input = fixture(); const current = project(input);
    receipt(input, 2, { outOfRange: true });
    expect(input.frame!.geometry).toMatchObject({ hasPixels: true, outOfRange: true });
    expect(project(input, current).display.state).toBe('current');
    expect(project(input).display.state).toBe('current');
  });

  it('requires each geometry marker and a newer accepted receipt after retirement', () => {
    const input = fixture(); const current = project(input);
    edit(input, (s) => { s.main!.mode = 'LSB'; });
    status(input, 'main.mode', { lastObservedMonotonic: 11 });
    const retired = project(input, current);
    receipt(input, 2);
    expect(project(input, retired).display.state).toBe('unknown');
    status(input, 'main.filterWidth', { lastObservedMonotonic: 11 });
    expect(project(input, retired).display.state).toBe('unknown');
    status(input, 'main.ifShift', { lastObservedMonotonic: 11 });
    status(input, 'main.mode', { lastObservedMonotonic: 11 });
    expect(project(input, retired).display.state).toBe('current');
  });

  it('starts a new provider marker domain without comparing its markers to the old provider', () => {
    const input = fixture(); renew(input, 1000, 1); const old = project(input);
    edit(input, (s) => { s.providerGeneration = 2; });
    editCapabilities(input, (c) => { c.providerGeneration = 2; });
    renew(input, 1, 2); const retired = project(input, old);
    expect(retired.display.state).toBe('unknown');
    renew(input, 2, 3); expect(project(input, retired).display.state).toBe('current');
  });

  it('baselines incoming receiver markers across MAIN→SUB→MAIN without keeping receiver tuples', () => {
    const input = fixture(); dual(input);
    let previous = project(input);
    for (const [receiver, marker, sequence] of [['SUB', 11, 3], ['MAIN', 12, 5]] as const) {
      edit(input, (s) => { s.active = receiver; }); input.selection = { receiver, slot: 'single' }; receipt(input, sequence - 1);
      previous = project(input, previous); expect(previous.display.state).toBe('unknown');
      renew(input, marker, sequence); previous = project(input, previous);
      expect(previous.display.state).toBe('current');
    }
    expect(previous.domain).toBe('[1,"MAIN"]');
    expect(previous).not.toHaveProperty('receivers');
  });

  it('retires a matching transport-epoch transition and rejects accepted-receipt regression', () => {
    const input = fixture(); const current = project(input);
    const epoch = () => { const frame = input.frame!; input.frame = { ...frame,
      transportEpoch: 2 }; };
    epoch(); const retired = project(input, current); expect(retired.display.state).toBe('unknown');
    renew(input, 11, 3); epoch(); const renewed = project(input, retired);
    expect(renewed.display.state).toBe('current');
    receipt(input, 2); epoch(); expect(project(input, renewed).display.state).toBe('unknown');
  });

  it.each(['MAIN', 'SUB'] as const)('requires observed active receiver in two-receiver %s context', (receiver) => {
    const input = fixture(); dual(input); edit(input, (s) => { s.active = receiver; });
    input.selection = { receiver, slot: 'single' }; receipt(input);
    expect(project(input).display.state).toBe('current');
    status(input, 'active', { observed: false }); expect(project(input).display.state).toBe('unknown');
  });

  it('does not fabricate an absolute slot from relative selected/unselected readback', () => {
    const input = fixture(); slotted(input); editCapabilities(input, (c) => { c.vfoReadback = 'selected_unselected'; });
    status(input, 'main.activeSlot', { observed: false });
    expect(project(input).display.state).toBe('unknown');
  });

  it.each(['freqHz', 'mode', 'filter'])('denies strict alias disagreement for %s', (field) => {
    const input = fixture(); slotted(input);
    if (field === 'freqHz') nudgeFrequency(input);
    if (field === 'mode') edit(input, (s) => { s.main!.mode = 'LSB'; });
    if (field === 'filter') edit(input, (s) => { s.main!.filter = 2; });
    expect(project(input).display.state).toBe('unknown');
  });

  it('recovers from session disconnect only after renewed geometry and receipt', () => {
    const input = fixture(); const current = project(input);
    input.session = { state: 'disconnected', epoch: 1 };
    let previous = project(input, current); expect(previous.display.state).toBe('unknown');
    input.session = { state: 'connected', epoch: 2 };
    previous = project(input, previous); expect(previous.display.state).toBe('unknown');
    renew(input, 11, 2); expect(project(input, previous).display.state).toBe('current');
  });

  it('updates complete current width, but rejects changed values under equal observation markers', () => {
    const input = fixture(); const current = project(input);
    edit(input, (s) => { s.main!.filterWidth = 1800; });
    expect(project(input, current).display.state).toBe('unknown');
    status(input, 'main.filterWidth', { lastObservedMonotonic: 11 });
    expect(tuple(project(input, current)).widthHz).toBe(1800);
  });

  it('retains stale scalars for long periods with live frames, ignoring pending targets and wire sequence', () => {
    const input = fixture(); const current = project(input); stale(input, 'main.filterWidth');
    Object.assign(input, { pending: { frequencyHz: 7_000_000, widthHz: 900 } });
    const f = input.frame!; input.frame = { ...f,
      receipt: 2 };
    const result = project(input, current);
    expect(result.display.state).toBe('stale'); expect(tuple(result)).toBe(tuple(current));
    // MOR-2425/R40: the strict authority holds the last observed width too.
    // 2400 is this fixture's own `main.filterWidth` (see `fixture()` above).
    expect(toSpectrumAuthority(input.state, input.caps)!.filterWidthHz).toBe(2400);
  });

  it.each(['main.pbtInner', 'main.pbtOuter'])('requires observed, current %s to seed derived shift', (path) => {
    const input = fixture(); pbt(input); stale(input, path);
    expect(project(input).display.state).toBe('unknown');
    status(input, path, { observed: false });
    expect(project(input).display.state).toBe('unknown');
  });

  it('uses native shift without PBT evidence and retires scale changes only for derived shift', () => {
    const native = fixture(); const current = project(native);
    edit(native, (s) => { s.main!.pbtInner = NaN; }); editCapabilities(native, (c) => { c.controls = {}; });
    expect(tuple(project(native, current)).shiftHz).toBe(0);
    const derived = fixture(); pbt(derived); const previous = project(derived);
    editCapabilities(derived, (c) => {
      c.controls = { pbt_inner: { raw_min: 0, raw_max: 255, raw_center: 128, display_min: -1000, display_max: 1000 } };
    });
    expect(project(derived, previous).display.state).toBe('unknown');
  });

  const historyPaths = [
    ...['main.freqHz', 'main.mode', 'main.filter', 'main.dataMode', 'main'].map((path) => ['single', path]),
    ...['main.activeSlot', 'main.vfoA', 'main.vfoA.freqHz', 'main.vfoA.mode', 'main.vfoA.filterNum']
      .map((path) => ['slotted', path]),
    ['dual', 'active'],
  ];
  function historyFixture(topology: string) {
    const input = fixture();
    if (topology === 'slotted') slotted(input);
    if (topology === 'dual') dual(input);
    return input;
  }
  const historyGaps = ['state', 'caps', 'selection', 'leaf', 'unobserved', 'malformed'] as const;
  it.each(historyPaths.flatMap(([topology, path]) => historyGaps.map((gap) => [topology, path, gap]))) (
    'preserves %s %s marker/value history across absent or invalid %s', (topology, path, gap) => {
      const input = historyFixture(topology); const current = project(input);
      expect(current.display.state).toBe('current');
      const savedStatus = { ...input.state!.fieldStatus![path] };
      const savedInput = { state: input.state, caps: input.caps, selection: input.selection };
      if (gap === 'state' || gap === 'caps' || gap === 'selection') input[gap] = null;
      if (gap === 'leaf') edit(input, (s) => { delete s.fieldStatus![path]; });
      if (gap === 'unobserved') status(input, path, { observed: false });
      if (gap === 'malformed') status(input, path, { lastObservedMonotonic: NaN });
      const missing = project(input, current);
      const optionalAncestor = gap === 'leaf' && (path === 'main' || path === 'main.vfoA');
      expect(missing.display.state).toBe(optionalAncestor ? 'current' : 'unknown');
      Object.assign(input, savedInput); edit(input, (s) => { s.fieldStatus![path] = savedStatus; });
      renew(input, 11, 2); status(input, path, { lastObservedMonotonic: 9 });
      const regressed = project(input, missing);
      expect(regressed.display.state).toBe('unknown');
      expect(regressed.observations[path]).toEqual({ value: current.observations[path].value, marker: 10 });
      renew(input, 12, 3); status(input, path, { lastObservedMonotonic: 10 });
      expect(project(input, regressed).display.state).toBe('current');
    },
  );

  it.each(['state', 'caps', 'selection'] as const)('allows equal identities after absent %s with renewed geometry/receipt', (gap) => {
    const input = fixture(); const current = project(input); const saved = input[gap];
    input[gap] = null; const missing = project(input, current); Object.assign(input, { [gap]: saved });
    status(input, 'main.filterWidth', { lastObservedMonotonic: 11 });
    status(input, 'main.ifShift', { lastObservedMonotonic: 11 }); receipt(input, 2);
    expect(project(input, missing).display.state).toBe('current');
  });

  it.each(['frequency', 'mode', 'filter', 'DATA', 'slot'])('rejects changed %s at an equal marker after missing state', (field) => {
    const input = fixture(); slotted(input); const current = project(input); const saved = input.state;
    input.state = null; const missing = project(input, current); input.state = saved;
    const path = field === 'frequency' ? 'main.vfoA.freqHz' : field === 'mode' ? 'main.vfoA.mode'
      : field === 'filter' ? 'main.vfoA.filterNum' : field === 'DATA' ? 'main.dataMode' : 'main.activeSlot';
    edit(input, (s) => {
      const rx = s.main!;
      if (field === 'frequency') rx.freqHz = rx.vfoA!.freqHz = 14_075_000;
      if (field === 'mode') rx.mode = rx.vfoA!.mode = 'LSB';
      if (field === 'filter') rx.filter = rx.vfoA!.filterNum = 2;
      if (field === 'DATA') rx.dataMode = 1;
      if (field === 'slot') rx.activeSlot = 'B';
    });
    if (field === 'slot') input.selection = { receiver: 'MAIN', slot: 'B' };
    renew(input, 11, 2); status(input, path, { lastObservedMonotonic: 10 });
    let previous = project(input, missing);
    expect(previous.display.state).toBe('unknown');
    renew(input, 12, 3); status(input, path, { lastObservedMonotonic: 10 });
    previous = project(input, previous);
    expect(previous.display.state).toBe('unknown');
    expect(previous.observations[path]).toEqual(current.observations[path]);
    renew(input, 13, 4); status(input, path, { lastObservedMonotonic: 11 });
    if (field === 'slot') receipt(input, 4, { startFreq: 14_024_000, endFreq: 14_124_000 });
    expect(project(input, previous).display.state).toBe('current');
  });

  it('keeps independently advancing high-water markers while other evidence is missing or regressing', () => {
    const input = fixture(); let previous = project(input);
    edit(input, (s) => { s.main!.mode = 'LSB'; }); status(input, 'main.mode', { lastObservedMonotonic: 11 });
    previous = project(input, previous);
    const shift = input.state!.fieldStatus!['main.ifShift'];
    edit(input, (s) => { delete s.fieldStatus!['main.ifShift']; });
    status(input, 'main.mode', { lastObservedMonotonic: 30 });
    status(input, 'main.freqHz', { lastObservedMonotonic: 9 });
    previous = project(input, previous);
    expect(previous.observations['main.mode'].marker).toBe(30);
    expect(previous.observations['main.freqHz'].marker).toBe(10);
    edit(input, (s) => { s.fieldStatus!['main.ifShift'] = shift; });
    renew(input, 21, 3); previous = project(input, previous);
    expect(previous.display.state).toBe('unknown');
    renew(input, 31, 4); expect(project(input, previous).display.state).toBe('current');
  });

  it('does not treat an unobserved receiver switch as a positively established new domain', () => {
    const input = historyFixture('dual'); let previous = project(input);
    edit(input, (s) => { s.active = 'SUB'; }); input.selection = { receiver: 'SUB', slot: 'single' };
    status(input, 'active', { observed: false }); receipt(input, 2);
    previous = project(input, previous);
    expect(previous.display.state).toBe('unknown'); expect(previous.domain).toBe('[1,"MAIN"]');
    edit(input, (s) => { s.active = 'MAIN'; }); input.selection = { receiver: 'MAIN', slot: 'single' };
    renew(input, 11, 3); status(input, 'main.freqHz', { lastObservedMonotonic: 9 });
    previous = project(input, previous);
    renew(input, 12, 4); status(input, 'main.freqHz', { lastObservedMonotonic: 9 });
    expect(project(input, previous).display.state).toBe('unknown');
  });

  it('drops foreign receiver marker comparisons only after positive receiver qualification', () => {
    const input = historyFixture('dual'); renew(input, 1000, 1); const current = project(input);
    edit(input, (s) => { s.active = 'SUB'; }); input.selection = { receiver: 'SUB', slot: 'single' };
    renew(input, 1, 2); const retired = project(input, current);
    expect(retired.domain).toBe('[1,"SUB"]');
    expect(retired.observations).not.toHaveProperty('main.freqHz');
    renew(input, 2, 3); expect(project(input, retired).display.state).toBe('current');
  });
});
