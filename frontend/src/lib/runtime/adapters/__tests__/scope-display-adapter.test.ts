/**
 * MOR-1262 decomposition slice 12A (MOR-1301, the FINAL A-slice of the
 * vocabulary program) — `scopeDisplay` fact-group adapter derivation.
 *
 * Companion to `scope-controls-adapter.test.ts` (11A/11A′) and
 * `rx-audio-adapter.test.ts` (3A), neither of which this file modifies.
 * `scopeDisplay` is a SEPARATE optional group — see `radio-view-model.ts`'s
 * `ScopeDisplayViewModel` doc comment.
 *
 * PARITY — `lib/runtime` may not import `components-v2` (ADR 2026-04-12), so
 * `classifyScopeHealth` cannot call the shipped status-bar's
 * `deriveScopeIndicatorState` directly; this file imports it anyway (tests
 * are exempt from that boundary, `eslint.config.js`'s own carve-out) purely
 * to PIN agreement across a discriminating-combo matrix, the same
 * "agree with the real projector, don't assume it" discipline
 * `rx-audio-adapter.test.ts` uses for `projectModInputSource`. No `vi.mock`,
 * no global store mutation — `deriveScopeIndicatorState` is a pure function
 * of its two explicit arguments, so this file needs no isolated pool.
 */
import { describe, expect, it } from 'vitest';
import type { Capabilities } from '$lib/types/capabilities';
import type { FieldStatus, ServerState } from '$lib/types/state';
import { validateRadioViewModel, type RadioViewModel } from '../../../../semantic/radio-view-model';
import { toRadioViewModel, type ScopeDisplaySnapshot } from '../radio-view-model-adapter';
import { deriveScopeIndicatorState } from '../../../../components-v2/layout/StatusBar.svelte';

function caps(overrides: Partial<Capabilities> = {}): Capabilities {
  return {
    model: 'fixture', scope: true, audio: true, tx: true, capabilities: ['scope', 'audio', 'tx'],
    receivers: 1, vfoScheme: 'single', freqRanges: [], modes: [], filters: [],
    audioConfig: { sampleRate: 48000, channels: 1, codecs: ['pcm16'] },
    webrtc: { available: false, enabled: false },
    txBands: [], scopeSource: 'hardware', audioFftAvailable: false, ...overrides,
  } as Capabilities;
}

const fresh: FieldStatus = { storePath: 'x', observed: true, freshness: 'fresh', availability: 'available' };

function bareState(overrides: Partial<ServerState> = {}): ServerState {
  return {
    active: 'MAIN', split: false, dualWatch: false, ptt: false,
    txTarget: { status: 'unknown', reason: 'not-observed' },
    main: {
      freqHz: 14195000, mode: 'USB', filter: 1, dataMode: 0, att: 0, preamp: 0,
      nb: false, nr: false, afLevel: 1, rfGain: 1, squelch: 0, sMeter: 0,
    },
    fieldStatus: { active: fresh, split: fresh, dualWatch: fresh },
    ...overrides,
  } as ServerState;
}

const SNAP: ScopeDisplaySnapshot = {
  source: 'hardware', available: true, resourceSelected: true, demand: 1,
  lifecycle: 'streaming', transport: 'connected', frameSeen: true, isPoweredOff: false,
};

function model(
  state: ServerState | null, capabilities: Capabilities | null,
  snapshot?: ScopeDisplaySnapshot | null,
): RadioViewModel {
  const view = toRadioViewModel(state, capabilities, null, null, snapshot);
  expect(view).not.toBeNull();
  return validateRadioViewModel(view);
}

describe('scopeDisplay evidence gate (MOR-1301)', () => {
  it('emits no scopeDisplay without the App-owned snapshot — runtime status is not this layer\'s to guess', () => {
    for (const snapshot of [undefined, null]) {
      const view = model(bareState(), caps(), snapshot);
      expect(view.scopeDisplay).toBeUndefined();
      expect(Object.keys(view)).not.toContain('scopeDisplay');
    }
  });

  it('emits no scopeDisplay for a radio with neither scope nor an audio-FFT source', () => {
    const view = model(bareState(), caps({ scope: false, capabilities: ['audio', 'tx'], scopeSource: undefined }), SNAP);
    expect(view.scopeDisplay).toBeUndefined();
  });

  it('emits scopeDisplay once the scope capability alone is declared', () => {
    const view = model(bareState(), caps(), SNAP);
    expect(view.scopeDisplay).toBeDefined();
    expect(Object.keys(view.scopeDisplay!).sort()).toEqual(['health', 'source']);
  });

  it('emits scopeDisplay for an audio-FFT-only radio (no hardware scope at all)', () => {
    const audioOnly = caps({ scope: false, capabilities: ['audio', 'tx'], scopeSource: 'audio_fft' });
    const view = model(bareState(), audioOnly, { ...SNAP, source: 'audio_fft' });
    expect(view.scopeDisplay).toBeDefined();
  });
});

describe('scopeDisplay per-field derivation (MOR-1301)', () => {
  it('reports the known source straight from the snapshot', () => {
    const view = model(bareState(), caps(), { ...SNAP, source: 'audio_fft' });
    expect(view.scopeDisplay!.source.reading).toEqual({ status: 'known', value: 'audio_fft' });
    expect(view.scopeDisplay!.source.availability).toEqual({ structural: true, operational: true });
  });

  it('reports source unknown, but still structurally present, before any source resolves', () => {
    const view = model(bareState(), caps(), { ...SNAP, source: null });
    expect(view.scopeDisplay!.source.reading).toEqual({ status: 'unknown' });
    expect(view.scopeDisplay!.source.availability).toEqual({ structural: true, operational: false });
  });

  it('reports a known health reading for a fully live snapshot', () => {
    const view = model(bareState(), caps(), SNAP);
    expect(view.scopeDisplay!.health.reading).toEqual({ status: 'known', value: 'connected' });
  });

  it('emits a validator-clean model carrying the scopeDisplay group (round-trip proof)', () => {
    const view = model(bareState(), caps(), SNAP);
    expect(JSON.parse(JSON.stringify(view))).toEqual(view);
  });
});

/**
 * PARITY MATRIX — every branch of `deriveScopeIndicatorState`
 * (`StatusBar.svelte`), each isolated by flipping exactly the input that
 * branch decides on, run through BOTH `classifyScopeHealth` (indirectly, via
 * the adapter) and the real shipped function. Disagreement here is the
 * reimplementation drift this whole discipline exists to catch.
 */
describe('scopeDisplay health classification agrees with the real status-bar indicator (MOR-1301 parity)', () => {
  const live: ScopeDisplaySnapshot = SNAP;

  const CASES: Array<[string, ScopeDisplaySnapshot]> = [
    ['radio powered off overrides everything', { ...live, isPoweredOff: true }],
    ['no source selected', { ...live, source: null }],
    ['source not available', { ...live, available: false }],
    ['resource not selected', { ...live, resourceSelected: false }],
    ['zero demand', { ...live, demand: 0 }],
    ['lifecycle failed', { ...live, lifecycle: 'failed' }],
    ['lifecycle starting', { ...live, lifecycle: 'starting' }],
    ['transport connecting', { ...live, lifecycle: 'inactive', transport: 'connecting' }],
    ['transport reconnecting', { ...live, lifecycle: 'inactive', transport: 'reconnecting' }],
    ['transport disconnected', { ...live, lifecycle: 'inactive', transport: 'disconnected' }],
    ['connected but no frame seen yet', { ...live, frameSeen: false }],
    ['connected, frame seen, streaming', live],
    ['connected, frame seen, lifecycle not streaming (fallback)', { ...live, lifecycle: 'inactive' }],
    ['audio_fft source, otherwise identical to the live case', { ...live, source: 'audio_fft' }],
  ];

  it.each(CASES)('%s', (_label, snapshot) => {
    const view = model(bareState(), caps(), snapshot);
    const expected = deriveScopeIndicatorState(snapshot, snapshot.isPoweredOff);
    expect(view.scopeDisplay!.health.reading).toEqual({ status: 'known', value: expected });
  });
});

describe('scopeDisplay determinism in (caps, snapshot) (MOR-1301)', () => {
  it('is stable across repeated derivations of the same inputs', () => {
    expect(model(bareState(), caps(), SNAP).scopeDisplay).toEqual(model(bareState(), caps(), SNAP).scopeDisplay);
  });

  it('changes with the snapshot, and with caps, independently', () => {
    const withHealthDiff = model(bareState(), caps(), { ...SNAP, frameSeen: false });
    expect(withHealthDiff.scopeDisplay!.health).not.toEqual(model(bareState(), caps(), SNAP).scopeDisplay!.health);
    const noScope = caps({ scope: false, capabilities: ['audio', 'tx'], scopeSource: undefined });
    expect(model(bareState(), noScope, SNAP).scopeDisplay).toBeUndefined();
  });
});
