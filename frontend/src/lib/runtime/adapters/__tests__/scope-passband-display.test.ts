import { describe, expect, it, vi } from 'vitest';
import type { Capabilities } from '$lib/types/capabilities';
import type { FieldStatus, ServerState } from '$lib/types/state';
import { deriveIfShift, pbtRangeFromCaps, pbtRawToHz } from '$lib/radio/filter-controls';
import { getPassbandEdgesHz } from '../../../../components/spectrum/passband-geometry';
import { resolveLcdSpectrumFrame } from '../../../../skins/segmentline/lcd-display-contract';
import { qualifyScopeFrameEnvelope, toScopeDisplayFrame, toSpectrumAuthority } from '../scope-adapter';
import { toRadioViewModel } from '../radio-view-model-adapter';
import { IC7300_CAPABILITIES } from './fixtures/ic7300-profile';
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
    filterConfig: { USB: { defaults: [2400], fixed: true } },
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
  const envelope = qualifyScopeFrameEnvelope({ receiver, mode: 0,
    startFreq: 14_000_000, endFreq: 14_100_000, pixels: new Uint8Array([10, 128, 200]), ...overrides,
  }, { source: 'hardware', receiver, providerGeneration: generation,
    transportEpoch: 1, receivedAt: 0, acceptedSequence: sequence }, 9)!;
  const authority = Object.freeze({ source: envelope.source, receiver,
    providerGeneration: envelope.providerGeneration, transportEpoch: envelope.transportEpoch,
    demanded: true, transport: 'connected' as const, nowMonotonic: 0 });
  input.frame = Object.freeze({ envelope, authority, resolution: resolveLcdSpectrumFrame(
    toScopeDisplayFrame(envelope, authority), { source: 'hardware', receiver: input.selection!.receiver }) });
}
function status(input: ScopePassbandDisplayInput, path: string, patch: Partial<FieldStatus>): void {
  Object.assign(input.state!.fieldStatus![path], patch);
}
function stale(input: ScopePassbandDisplayInput, path: string): void {
  status(input, path, { freshness: 'stale', availability: 'stale' });
}
function renew(input: ScopePassbandDisplayInput, marker = 11, sequence = 2): void {
  for (const leaf of Object.values(input.state!.fieldStatus!)) Object.assign(leaf,
    { observed: true, freshness: 'fresh', availability: 'available', lastObservedMonotonic: marker });
  const f = input.frame?.envelope?.frame;
  receipt(input, sequence, f ? { mode: f.mode, startFreq: f.startFreq, endFreq: f.endFreq } : {});
}
const project = (input: ScopePassbandDisplayInput, previous = EMPTY_SCOPE_PASSBAND_DISPLAY) =>
  projectScopePassbandDisplay(previous, input);
function tuple(result: ScopePassbandDisplayState) {
  if (result.display.state !== 'current' && result.display.state !== 'stale') throw new Error('no tuple');
  return result.display.tuple;
}
function pbt(input: ScopePassbandDisplayInput): void {
  input.caps!.capabilities = input.caps!.capabilities.filter((tag) => tag !== 'if_shift').concat('pbt');
}
function slotted(input: ScopePassbandDisplayInput): void {
  input.caps!.vfoScheme = 'ab'; input.selection = { receiver: 'MAIN', slot: 'A' };
}

function banded(): ScopePassbandDisplayInput {
  const input = fixture(); slotted(input); pbt(input);
  input.caps!.freqRanges = structuredClone(IC7300_CAPABILITIES.freqRanges);
  receipt(input, 1, { startFreq: 14_024_000, endFreq: 14_124_000 });
  return input;
}
function tune(input: ScopePassbandDisplayInput, frequency: number, marker: number, mirror = true): void {
  input.state!.main!.vfoA!.freqHz = frequency;
  status(input, 'main.vfoA.freqHz', { lastObservedMonotonic: marker });
  if (mirror) {
    input.state!.main!.freqHz = frequency;
    status(input, 'main.freqHz', { lastObservedMonotonic: marker });
  }
}

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
    else { input.state!.main!.freqHz = 14_075_000; status(input, 'main.freqHz', { lastObservedMonotonic: 11 }); }
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
    Object.assign(input.state!, { pending: { frequencyHz: 14_090_000 } });
    expect(tuple(project(input, current)).frequencyHz).toBe(14_074_000);
    tune(input, 14_075_000, 11); stale(input, 'main.vfoA.freqHz');
    const held = project(input, current);
    expect(held.display).toMatchObject({ state: 'stale', translated: true });
    expect(tuple(held).frequencyHz).toBe(14_074_000);
  });
  const hard: [string, (i: ScopePassbandDisplayInput) => void][] = [
    ['provider', (i) => { i.state!.providerGeneration = 2; i.caps!.providerGeneration = 2; receipt(i, 3); }],
    ['caps', (i) => { i.caps!.filterWidthMax = 9000; }],
    ['control epoch', (i) => { i.session!.epoch = 2; }],
    ['scope epoch', (i) => { i.frame = { ...i.frame!, authority: { ...i.frame!.authority, transportEpoch: 2 } }; }],
    ['receiver', (i) => { i.state!.active = 'SUB'; }],
    ['slot', (i) => { i.selection!.slot = 'B'; i.state!.main!.activeSlot = 'B'; status(i, 'main.activeSlot', { lastObservedMonotonic: 12 }); }],
    ['mode', (i) => { i.state!.main!.mode = 'LSB'; i.state!.main!.vfoA!.mode = 'LSB'; status(i, 'main.mode', { lastObservedMonotonic: 12 }); status(i, 'main.vfoA.mode', { lastObservedMonotonic: 12 }); }],
    ['filter', (i) => { i.state!.main!.filter = 2; i.state!.main!.vfoA!.filterNum = 2; status(i, 'main.filter', { lastObservedMonotonic: 12 }); status(i, 'main.vfoA.filterNum', { lastObservedMonotonic: 12 }); }],
    ['DATA', (i) => { i.state!.main!.dataMode = 1; status(i, 'main.dataMode', { lastObservedMonotonic: 12 }); }],
    ['frame mode', (i) => receipt(i, 3, { mode: 1 })],
    ['span', (i) => receipt(i, 3, { endFreq: 14_200_000 })],
    ['band', (i) => tune(i, 18_100_000, 12)],
    ['outside profile bands', (i) => tune(i, 15_000_000, 12)],
    ['band definitions', (i) => { i.caps!.freqRanges[0].bands![5].end += 100; }],
    ['null frame', (i) => { i.frame = null; }],
    ['500ms frame silence', (i) => { i.frame = { ...i.frame!, authority: { ...i.frame!.authority, nowMonotonic: 500 } }; }],
    ['off', (i) => { i.frame = { ...i.frame!, authority: { ...i.frame!.authority, demanded: false } }; }],
  ];
  it.each(hard)('retires a translated tuple on %s without replay after return', (_name, change) => {
    const input = banded(); let result = project(input); tune(input, 14_075_000, 11); result = project(input, result);
    expect(result.display.state).toBe('stale');
    const saved = structuredClone(input); change(input); result = project(input, result);
    expect(result.display.state).toBe('unknown');
    result = project(saved, result); expect(result.display.state).toBe('unknown');
    result = project(saved, result); expect(result.display.state).toBe('unknown');
    renew(saved, 20, 4); expect(project(saved, result).display.state).toBe('current');
  });
  it.each(['filterWidth', 'pbtInner', 'pbtOuter'] as const)('never mixes a partial changed %s with retained shape', (leaf) => {
    const input = banded(); let result = project(input); tune(input, 14_075_000, 11); result = project(input, result);
    input.state!.main![leaf]! += 10; status(input, `main.${leaf}`, { lastObservedMonotonic: 15 });
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

describe('coherent RF passband display', () => {
  it.each(['USB', 'LSB', 'AM'])('retains complete %s edges through leaf/ancestor stale and equal current', (mode) => {
    const input = fixture(); input.state!.main!.mode = mode;
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
  const invalid: [string, (i: ScopePassbandDisplayInput) => void][] = [
    ['zero width', (i) => { i.state!.main!.filterWidth = 0; }],
    ['zero frequency', (i) => { i.state!.main!.freqHz = 0; }],
    ['NaN shift', (i) => { i.state!.main!.ifShift = NaN; }],
    ['empty mode', (i) => { i.state!.main!.mode = ' '; }],
    ['empty filter', (i) => { i.state!.main!.filter = null; }],
    ['missing leaf', (i) => { delete i.state!.fieldStatus!['main.ifShift']; }],
    ['unobserved', (i) => status(i, 'main.ifShift', { observed: false })],
    ['ancestor veto', (i) => status(i, 'main', { observed: false })],
    ['NaN marker', (i) => status(i, 'main.ifShift', { lastObservedMonotonic: NaN })],
    ['negative marker', (i) => status(i, 'main.ifShift', { lastObservedMonotonic: -1 })],
    ['missing marker', (i) => status(i, 'main.ifShift', { lastObservedMonotonic: undefined })],
    ['invalid freshness', (i) => status(i, 'main.ifShift', { freshness: 'unknown' })],
    ['missing selection', (i) => { i.selection = null; }],
    ['unknown slot', (i) => { slotted(i); i.state!.main!.activeSlot = '?'; }],
    ['topology mismatch', (i) => { i.caps!.receivers = 2; }],
    ['missing generation', (i) => { delete i.state!.providerGeneration; }],
    ['caps mismatch', (i) => { i.caps!.providerGeneration = 2; }],
    ['missing session', (i) => { i.session = null; }],
    ['null frame', (i) => { i.frame = null; }],
  ];
  it.each(invalid)('rejects %s and retires existing tuple', (_name, change) => {
    const input = fixture(); const current = project(input); change(input);
    expect(project(input).display.state).toBe('unknown'); expect(project(input, current).display.state).toBe('unknown');
  });
  it.each(['filter_width', 'if_shift'])('reports absent %s as unsupported', (tag) => {
    const input = fixture(); const current = project(input);
    input.caps!.capabilities = input.caps!.capabilities.filter((value) => value !== tag);
    expect(project(input, current).display.state).toBe('unsupported');
  });
  it.each([128, 140])('uses explicit canonical PBT conversion including raw %s', (raw) => {
    const input = fixture(); pbt(input); input.state!.main!.pbtInner = raw; input.state!.main!.pbtOuter = raw;
    const scale = pbtRangeFromCaps(input.caps)!;
    const expected = deriveIfShift(pbtRawToHz(raw, scale), pbtRawToHz(raw, scale));
    const current = project(input); expect(tuple(current).shiftHz).toBe(expected);
    stale(input, 'main.pbtOuter'); expect(tuple(project(input, current))).toEqual(tuple(current));
    input.state!.main!.pbtInner = raw + 1; status(input, 'main.pbtInner', { lastObservedMonotonic: 11 });
    const retired = project(input, current); expect(retired.display.state).toBe('unknown');
    renew(input, 12, 2); expect(project(input, retired).display.state).toBe('current');
  });
  it.each([undefined, { raw_min: 0, raw_max: 255, raw_center: 0, display_min: -1200, display_max: 1200 }])('rejects missing/invalid PBT scale', (scale) => {
    const input = fixture(); pbt(input); input.caps!.controls = scale ? { pbt_inner: scale } : {};
    expect(project(input).display.state).toBe('unsupported');
  });
  const transitions: [string, (i: ScopePassbandDisplayInput) => void][] = [
    ['generation', (i) => { i.state!.providerGeneration = 2; i.caps!.providerGeneration = 2; receipt(i, 2); }],
    ['capability shape', (i) => { i.caps!.filterConfig!.USB.defaults = [1800]; }],
    ['control epoch', (i) => { i.session = { state: 'connected', epoch: 2 }; }],
    ['receiver', (i) => { i.caps!.receivers = 2; i.caps!.vfoScheme = 'ab_shared'; i.caps!.capabilities.push('dual_rx'); i.state!.active = 'SUB'; i.selection = { receiver: 'SUB', slot: 'single' }; receipt(i, 2); }],
    ['slot', (i) => { i.state!.main!.activeSlot = 'B'; i.selection = { receiver: 'MAIN', slot: 'B' }; }],
    ['frequency', (i) => { i.state!.main!.freqHz += 100; }],
    ['mode', (i) => { i.state!.main!.mode = 'LSB'; }],
    ['DATA', (i) => { i.state!.main!.dataMode = 1; }],
    ['filter', (i) => { i.state!.main!.filter = 2; }],
    ['frame mode', (i) => receipt(i, 2, { mode: 1 })],
    ['frame start', (i) => receipt(i, 2, { startFreq: 14_010_000 })],
    ['frame end', (i) => receipt(i, 2, { endFreq: 14_110_000 })],
  ];
  it.each(transitions)('retires %s until newer geometry AND post-boundary receipt', (name, change) => {
    const input = fixture(); if (name === 'slot') slotted(input);
    if (name === 'receiver') {
      input.caps!.receivers = 2; input.caps!.vfoScheme = 'ab_shared'; input.caps!.capabilities.push('dual_rx');
    }
    const current = project(input); expect(current.display.state).toBe('current'); change(input);
    const retired = project(input, current); expect(retired.display.state).toBe('unknown');
    expect(project(input, retired).display.state).toBe('unknown');
    const oldFrame = input.frame; renew(input, 11, 3); const nextFrame = input.frame; input.frame = oldFrame;
    expect(project(input, retired).display.state).toBe('unknown');
    input.frame = nextFrame; expect(project(input, retired).display.state).toBe('current');
  });
  it('rejects A→B→A replay even with renewed receipt and old geometry markers', () => {
    const input = fixture(); slotted(input); const a = project(input);
    input.state!.main!.activeSlot = 'B'; input.selection = { receiver: 'MAIN', slot: 'B' };
    const b = project(input, a);
    input.state!.main!.activeSlot = 'A'; input.selection = { receiver: 'MAIN', slot: 'A' };
    const back = project(input, b); receipt(input, 3);
    expect(project(input, back).display.state).toBe('unknown');
    renew(input, 11, 4); expect(project(input, back).display.state).toBe('current');
  });
  it('rejects partial width updates and regressing leaf/ancestor markers', () => {
    for (const path of ['main.ifShift', 'main']) {
      const input = fixture(); const current = project(input); status(input, path, { lastObservedMonotonic: 9 });
      expect(project(input, current).display.state).toBe('unknown');
    }
    const input = fixture(); const current = project(input); stale(input, 'main.ifShift');
    input.state!.main!.filterWidth = 1800; status(input, 'main.filterWidth', { lastObservedMonotonic: 11 });
    const retired = project(input, current); expect(retired.display.state).toBe('unknown');
    renew(input, 12, 2); expect(tuple(project(input, retired)).widthHz).toBe(1800);
  });
  it('distinguishes unsupported DATA from observed zero and ignores unrelated metadata', () => {
    const input = fixture(); input.caps!.capabilities = input.caps!.capabilities.filter((tag) => tag !== 'data_mode');
    delete input.state!.fieldStatus!['main.dataMode']; const current = project(input);
    expect(current.display.state).toBe('current');
    input.caps = { ...input.caps!, model: 'renamed', capabilities: [...input.caps!.capabilities].reverse() };
    expect(project(input, current).display.state).toBe('current');
    input.caps!.capabilities.push('data_mode');
    input.state!.fieldStatus!['main.dataMode'] = { ...input.state!.fieldStatus!['main.mode'] };
    expect(project(input, current).display.state).toBe('unknown');
  });
  it.each([499, 500, -1, NaN])('honors frame age %s without another timer', (age) => {
    const input = fixture(); const current = project(input);
    input.frame = { ...input.frame!, authority: { ...input.frame!.authority, nowMonotonic: age } };
    expect(project(input, current).display.state).toBe(age === 499 ? 'current' : 'unknown');
  });
  it.each(['ghost', 'receiver', 'source', 'transport', 'demand', 'scope epoch', 'disconnect'])(
    'invalidates %s evidence without changing command authority', (kind) => {
      const input = fixture(); const current = project(input); const strict = toSpectrumAuthority(input.state, input.caps);
      const f = input.frame!;
      if (kind === 'ghost') input.frame = { ...f, resolution: { state: 'ghost', reason: 'stale' } };
      if (kind === 'receiver') input.frame = { ...f, authority: { ...f.authority, receiver: 1 } };
      if (kind === 'source') input.frame = { ...f, authority: { ...f.authority, source: 'audio_fft' } };
      if (kind === 'transport') input.frame = { ...f, authority: { ...f.authority, transport: 'reconnecting' } };
      if (kind === 'demand') input.frame = { ...f, authority: { ...f.authority, demanded: false } };
      if (kind === 'scope epoch') input.frame = { ...f, authority: { ...f.authority, transportEpoch: 2 } };
      if (kind === 'disconnect') input.session = { state: 'disconnected', epoch: 1 };
      expect(project(input, current).display.state).toBe('unknown');
      expect(toSpectrumAuthority(input.state, input.caps)).toEqual(strict);
    });
  it('does not mutate input, prior tuple, strict model, or shared pixels', () => {
    const input = fixture(); const before = JSON.stringify(input); const model = toRadioViewModel(input.state, input.caps);
    const pixels = input.frame!.envelope!.frame.pixels;
    const samples = Array.from(pixels);
    const current = project(input); const previous = structuredClone(current); project(input, current);
    expect(JSON.stringify(input)).toBe(before); expect(current).toEqual(previous);
    expect(input.frame!.envelope!.frame.pixels).toBe(pixels); expect(Array.from(pixels)).toEqual(samples);
    expect(toRadioViewModel(input.state, input.caps)).toEqual(model);
    expect(Object.isFrozen(tuple(current))).toBe(true); expect(current).not.toHaveProperty('frame');
  });

  it('requires each geometry marker and a newer accepted receipt after retirement', () => {
    const input = fixture(); const current = project(input);
    input.state!.main!.mode = 'LSB';
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
    input.state!.providerGeneration = 2; input.caps!.providerGeneration = 2;
    renew(input, 1, 2); const retired = project(input, old);
    expect(retired.display.state).toBe('unknown');
    renew(input, 2, 3); expect(project(input, retired).display.state).toBe('current');
  });

  it('baselines incoming receiver markers across MAIN→SUB→MAIN without keeping receiver tuples', () => {
    const input = fixture(); input.caps!.vfoScheme = 'ab_shared'; input.caps!.receivers = 2;
    input.caps!.capabilities.push('dual_rx');
    let previous = project(input);
    for (const [receiver, marker, sequence] of [['SUB', 11, 3], ['MAIN', 12, 5]] as const) {
      input.state!.active = receiver; input.selection = { receiver, slot: 'single' }; receipt(input, sequence - 1);
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
      envelope: { ...frame.envelope!, transportEpoch: 2 }, authority: { ...frame.authority, transportEpoch: 2 } }; };
    epoch(); const retired = project(input, current); expect(retired.display.state).toBe('unknown');
    renew(input, 11, 3); epoch(); const renewed = project(input, retired);
    expect(renewed.display.state).toBe('current');
    receipt(input, 2); epoch(); expect(project(input, renewed).display.state).toBe('unknown');
  });

  it.each(['MAIN', 'SUB'] as const)('requires observed active receiver in two-receiver %s context', (receiver) => {
    const input = fixture(); input.caps!.vfoScheme = 'ab_shared'; input.caps!.receivers = 2;
    input.caps!.capabilities.push('dual_rx'); input.state!.active = receiver;
    input.selection = { receiver, slot: 'single' }; receipt(input);
    expect(project(input).display.state).toBe('current');
    status(input, 'active', { observed: false }); expect(project(input).display.state).toBe('unknown');
  });

  it('does not fabricate an absolute slot from relative selected/unselected readback', () => {
    const input = fixture(); slotted(input); input.caps!.vfoReadback = 'selected_unselected';
    status(input, 'main.activeSlot', { observed: false });
    expect(project(input).display.state).toBe('unknown');
  });

  it.each(['freqHz', 'mode', 'filter'])('denies strict alias disagreement for %s', (field) => {
    const input = fixture(); slotted(input);
    if (field === 'freqHz') input.state!.main!.freqHz += 100;
    if (field === 'mode') input.state!.main!.mode = 'LSB';
    if (field === 'filter') input.state!.main!.filter = 2;
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
    input.state!.main!.filterWidth = 1800;
    expect(project(input, current).display.state).toBe('unknown');
    status(input, 'main.filterWidth', { lastObservedMonotonic: 11 });
    expect(tuple(project(input, current)).widthHz).toBe(1800);
  });

  it('retains stale scalars for long periods with live frames, ignoring pending targets and wire sequence', () => {
    const input = fixture(); const current = project(input); stale(input, 'main.filterWidth');
    Object.assign(input, { pending: { frequencyHz: 7_000_000, widthHz: 900 } });
    const f = input.frame!; input.frame = { ...f,
      envelope: { ...f.envelope!, acceptedSequence: 2, receivedAt: 100_000, wireSequence: 999 },
      authority: { ...f.authority, nowMonotonic: 100_001 } };
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
    native.state!.main!.pbtInner = NaN; native.caps!.controls = {};
    expect(tuple(project(native, current)).shiftHz).toBe(0);
    const derived = fixture(); pbt(derived); const previous = project(derived);
    derived.caps!.controls = { pbt_inner: { raw_min: 0, raw_max: 255,
      raw_center: 128, display_min: -1000, display_max: 1000 } };
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
    if (topology === 'dual') {
      input.caps!.vfoScheme = 'ab_shared'; input.caps!.receivers = 2; input.caps!.capabilities.push('dual_rx');
    }
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
      if (gap === 'leaf') delete input.state!.fieldStatus![path];
      if (gap === 'unobserved') status(input, path, { observed: false });
      if (gap === 'malformed') status(input, path, { lastObservedMonotonic: NaN });
      const missing = project(input, current);
      const optionalAncestor = gap === 'leaf' && (path === 'main' || path === 'main.vfoA');
      expect(missing.display.state).toBe(optionalAncestor ? 'current' : 'unknown');
      Object.assign(input, savedInput); input.state!.fieldStatus![path] = savedStatus;
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
    const rx = input.state!.main!;
    const path = field === 'frequency' ? 'main.vfoA.freqHz' : field === 'mode' ? 'main.vfoA.mode'
      : field === 'filter' ? 'main.vfoA.filterNum' : field === 'DATA' ? 'main.dataMode' : 'main.activeSlot';
    if (field === 'frequency') rx.freqHz = rx.vfoA!.freqHz = 14_075_000;
    if (field === 'mode') rx.mode = rx.vfoA!.mode = 'LSB';
    if (field === 'filter') rx.filter = rx.vfoA!.filterNum = 2;
    if (field === 'DATA') rx.dataMode = 1;
    if (field === 'slot') { rx.activeSlot = 'B'; input.selection = { receiver: 'MAIN', slot: 'B' }; }
    renew(input, 11, 2); status(input, path, { lastObservedMonotonic: 10 });
    let previous = project(input, missing);
    expect(previous.display.state).toBe('unknown');
    renew(input, 12, 3); status(input, path, { lastObservedMonotonic: 10 });
    previous = project(input, previous);
    expect(previous.display.state).toBe('unknown');
    expect(previous.observations[path]).toEqual(current.observations[path]);
    renew(input, 13, 4); status(input, path, { lastObservedMonotonic: 11 });
    expect(project(input, previous).display.state).toBe('current');
  });

  it('keeps independently advancing high-water markers while other evidence is missing or regressing', () => {
    const input = fixture(); let previous = project(input);
    input.state!.main!.mode = 'LSB'; status(input, 'main.mode', { lastObservedMonotonic: 11 });
    previous = project(input, previous);
    const shift = input.state!.fieldStatus!['main.ifShift']; delete input.state!.fieldStatus!['main.ifShift'];
    status(input, 'main.mode', { lastObservedMonotonic: 30 });
    status(input, 'main.freqHz', { lastObservedMonotonic: 9 });
    previous = project(input, previous);
    expect(previous.observations['main.mode'].marker).toBe(30);
    expect(previous.observations['main.freqHz'].marker).toBe(10);
    input.state!.fieldStatus!['main.ifShift'] = shift;
    renew(input, 21, 3); previous = project(input, previous);
    expect(previous.display.state).toBe('unknown');
    renew(input, 31, 4); expect(project(input, previous).display.state).toBe('current');
  });

  it('does not treat an unobserved receiver switch as a positively established new domain', () => {
    const input = historyFixture('dual'); let previous = project(input);
    input.state!.active = 'SUB'; input.selection = { receiver: 'SUB', slot: 'single' };
    status(input, 'active', { observed: false }); receipt(input, 2);
    previous = project(input, previous);
    expect(previous.display.state).toBe('unknown'); expect(previous.domain).toBe('[1,"MAIN"]');
    input.state!.active = 'MAIN'; input.selection = { receiver: 'MAIN', slot: 'single' };
    renew(input, 11, 3); status(input, 'main.freqHz', { lastObservedMonotonic: 9 });
    previous = project(input, previous);
    renew(input, 12, 4); status(input, 'main.freqHz', { lastObservedMonotonic: 9 });
    expect(project(input, previous).display.state).toBe('unknown');
  });

  it('drops foreign receiver marker comparisons only after positive receiver qualification', () => {
    const input = historyFixture('dual'); renew(input, 1000, 1); const current = project(input);
    input.state!.active = 'SUB'; input.selection = { receiver: 'SUB', slot: 'single' };
    renew(input, 1, 2); const retired = project(input, current);
    expect(retired.domain).toBe('[1,"SUB"]');
    expect(retired.observations).not.toHaveProperty('main.freqHz');
    renew(input, 2, 3); expect(project(input, retired).display.state).toBe('current');
  });
});
