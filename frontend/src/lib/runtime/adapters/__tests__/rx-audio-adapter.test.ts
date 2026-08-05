/**
 * MOR-1262 decomposition slice 3A (MOR-1274) — `rxAudio` fact-group adapter
 * derivation.
 *
 * Companion to `radio-view-model-adapter.test.ts` (MOR-1065),
 * `tx-aux-adapter.test.ts` (1A) and `meters-adapter.test.ts` (2A), none of
 * which this file modifies. Those files never pass an RX-audio snapshot, so
 * `deriveRxAudio` declines to emit for them and their exact-key-list
 * assertions stand unchanged.
 *
 * Three safety blocks:
 *  1. MOD-input readiness IS `deriveTxCapabilities`'s conclusion, and the
 *     source projection IS the App TX authority's — both pinned against the
 *     shipped originals, not against a hand-copied expectation.
 *  2. TX truth (R9): the whole group is invariant to `radioState.ptt`.
 *  3. Honest degradation: the group never fabricates the shipped panel's
 *     0.5 AF / 'both' focus defaults.
 * (Purity — no transport/audio-manager/AudioContext contact — is pinned
 * separately in `rx-audio-purity.isolated.test.ts`, which needs the isolated pool.)
 */
import { describe, expect, it } from 'vitest';
import type { Capabilities } from '$lib/types/capabilities';
import type { FieldStatus, ServerState } from '$lib/types/state';
import { validateRadioViewModel, type RadioViewModel } from '../../../../semantic/radio-view-model';
import { toRadioViewModel, type RxAudioSnapshot } from '../radio-view-model-adapter';
import { deriveTxCapabilities } from '../tx-capabilities';
import { createAppAuthorityProjector } from '../../tx-controller/app-authority';
import { toRxAudioProps } from '../../props/panel-props';

const fresh: FieldStatus = { storePath: 'x', observed: true, freshness: 'fresh', availability: 'available' };
const stale: FieldStatus = { storePath: 'x', observed: true, freshness: 'stale', availability: 'stale' };
const missing: FieldStatus = { storePath: 'x', observed: false, freshness: 'fresh', availability: 'missing' };

function caps(overrides: Partial<Capabilities> = {}): Capabilities {
  return {
    model: 'fixture', scope: false, audio: true, tx: true,
    capabilities: ['audio', 'tx', 'mod_input_routing', 'af_level'],
    receivers: 1, vfoScheme: 'single', freqRanges: [], modes: [], filters: [],
    audioConfig: { sampleRate: 48000, channels: 1, codecs: ['pcm16'] },
    webrtc: { available: false, enabled: false },
    txBands: [{ start: 14000000, end: 14350000, name: '20m' }],
    scopeSource: 'hardware', audioFftAvailable: false,
    audioTxRequiredModInputSource: 5, ...overrides,
  } as Capabilities;
}

/** The App-owned snapshot: live browser RX at 42 %, audio link up, routing restored. */
const SNAP: RxAudioSnapshot = {
  muted: false, rxEnabled: true, volume: 42, connected: true,
  routing: { focus: 'both', splitStereo: false },
};

function audioState(overrides: Partial<ServerState> = {}): ServerState {
  return {
    active: 'MAIN', split: false, dualWatch: false, ptt: false,
    txTarget: { status: 'known', receiver: 'MAIN', slot: null, frequencyHz: 14195000 },
    main: {
      freqHz: 14195000, mode: 'USB', filter: 1, dataMode: 0, att: 0, preamp: 0,
      nb: false, nr: false, afLevel: 0.31, rfGain: 1, squelch: 0, sMeter: 120,
    },
    dataOffModInput: 5,
    fieldStatus: {
      active: fresh, split: fresh, dualWatch: fresh, txTarget: fresh,
      'main.freqHz': fresh, 'main.mode': fresh, 'main.filter': fresh,
      'main.afLevel': fresh, dataOffModInput: fresh,
    },
    ...overrides,
  } as unknown as ServerState;
}

function model(
  state: ServerState | null, capabilities: Capabilities | null,
  snapshot?: RxAudioSnapshot | null,
): RadioViewModel {
  const view = toRadioViewModel(state, capabilities, null, snapshot);
  expect(view).not.toBeNull();
  return validateRadioViewModel(view);
}

describe('rxAudio group gate (MOR-1262 slice 3A, kill-test 4)', () => {
  it('emits NO group without the App-owned audio snapshot — audio lifetime is not this layer\'s', () => {
    for (const snapshot of [undefined, null]) {
      const view = model(audioState(), caps(), snapshot);
      expect(view.rxAudio).toBeUndefined();
      expect(Object.keys(view)).not.toContain('rxAudio');
    }
  });

  it('emits no group for a radio with no AF, no live audio, no dual RX and no MOD routing', () => {
    const bare = caps({ audio: false, capabilities: ['tx'] });
    expect(model(audioState(), bare, SNAP).rxAudio).toBeUndefined();
  });

  it('emits the group once a single piece of evidence exists (MOD-input routing alone)', () => {
    const routingOnly = caps({ audio: false, capabilities: ['tx', 'mod_input_routing'] });
    const rxAudio = model(audioState(), routingOnly, SNAP).rxAudio!;
    expect(rxAudio.modInputSource.reading).toEqual({ status: 'known', value: 5 });
    expect(rxAudio.liveAudio).toEqual({ structural: false, operational: false });
  });

  it('emits no model at all when capabilities are absent', () => {
    expect(toRadioViewModel(audioState(), null, null, SNAP)).toBeNull();
  });

  it('leaves the pre-3A families untouched when rxAudio is present', () => {
    const view = model(audioState(), caps(), SNAP);
    expect(view.topologyId).toBe('1/single');
    expect(view.meters).toBeUndefined();
    expect(view.txAux).toBeUndefined();
    expect(view.txPermit).toEqual({ status: 'allowed', band: '20m' });
    expect(JSON.parse(JSON.stringify(view))).toEqual(view);
  });
});

/**
 * SAFETY CONSTRAINT 2. The readiness fact is the shipped `deriveTxCapabilities`
 * conclusion and the source projection is the App TX authority's own — both
 * asserted against the REAL functions, so re-deriving either with new logic in
 * the adapter goes red instead of quietly disagreeing with the TX guard.
 */
describe('MOD-input readiness mirrors the shipped derivation (web-voice-TX guard)', () => {
  const SOURCES = [0, 1, 2, 3, 4, 5];

  it.each(SOURCES)('agrees with deriveTxCapabilities for an observed source %i', (source) => {
    const state = audioState({ dataOffModInput: source });
    const expected = deriveTxCapabilities(caps(), {
      txTarget: { status: 'known', receiver: 'MAIN', slot: null, frequencyHz: 14195000 },
      modInputSource: { status: 'known', source },
    }).modInputReadiness;
    expect(model(state, caps(), SNAP).rxAudio!.modInputReadiness).toEqual(expected);
  });

  it('reports the recorded failure as a mismatch carrying the offending source', () => {
    // DATA OFF MOD = MIC while the web UI streams over LAN: the exact
    // "web voice TX = noise/squeal" configuration.
    const rxAudio = model(audioState({ dataOffModInput: 0 }), caps(), SNAP).rxAudio!;
    expect(rxAudio.modInputReadiness).toEqual({ status: 'mismatch', source: 0 });
    expect(rxAudio.modInputSource.reading).toEqual({ status: 'known', value: 0 });
  });

  it('follows the active receiver\'s DATA group, not always DATA OFF', () => {
    const onData1 = audioState({
      main: { ...audioState().main, dataMode: 1 } as ServerState['main'],
      data1ModInput: 0,
      fieldStatus: { ...audioState().fieldStatus, data1ModInput: fresh },
    });
    expect(model(onData1, caps(), SNAP).rxAudio!.modInputReadiness)
      .toEqual({ status: 'mismatch', source: 0 });
  });

  it.each([
    ['unobserved', missing],
    ['stale', stale],
  ])('degrades a %s source to unknown readiness — never assumed ready', (_label, status) => {
    const state = audioState({
      fieldStatus: { ...audioState().fieldStatus, dataOffModInput: status },
    });
    const rxAudio = model(state, caps(), SNAP).rxAudio!;
    expect(rxAudio.modInputReadiness).toEqual({ status: 'unknown' });
    expect(rxAudio.modInputSource.reading).toEqual({ status: 'unknown' });
    expect(rxAudio.modInputSource.availability).toEqual({ structural: true, operational: false });
  });

  it('marks the source structurally absent on a radio without MOD-input routing', () => {
    const noRouting = caps({ capabilities: ['audio', 'tx', 'af_level'] });
    const rxAudio = model(audioState(), noRouting, SNAP).rxAudio!;
    expect(rxAudio.modInputSource.availability).toEqual({ structural: false, operational: false });
    expect(rxAudio.modInputReadiness).toEqual({ status: 'not-applicable' });
  });

  it.each([
    ['LAN', 5], ['MIC', 0], ['USB', 3],
  ])('projects the %s source exactly as the App TX authority does', (_label, source) => {
    const state = audioState({ dataOffModInput: source });
    const project = createAppAuthorityProjector();
    const authority = project(state, caps(), { state: 'connected', epoch: 0 });
    const reading = model(state, caps(), SNAP).rxAudio!.modInputSource.reading;
    expect(authority.modInputSource.status).toBe('known');
    expect(reading).toEqual({
      status: 'known',
      value: (authority.modInputSource as { status: 'known'; source: number }).source,
    });
    expect(model(state, caps(), SNAP).rxAudio!.modInputReadiness)
      .toEqual(authority.facts!.modInputReadiness);
  });

  it('projects an unobserved source as unknown, exactly as the App TX authority does', () => {
    const state = audioState({
      fieldStatus: { ...audioState().fieldStatus, dataOffModInput: missing },
    });
    const project = createAppAuthorityProjector();
    const authority = project(state, caps(), { state: 'connected', epoch: 0 });
    expect(authority.modInputSource).toEqual({ status: 'unknown' });
    expect(model(state, caps(), SNAP).rxAudio!.modInputSource.reading).toEqual({ status: 'unknown' });
  });
});

/** SAFETY CONSTRAINT 3 (R9). Nothing in this family may read `radioState.ptt`. */
describe('rxAudio carries no ptt-derived TX truth (R9)', () => {
  it('is byte-identical with ptt true and ptt false', () => {
    const keyedDown = model(audioState({ ptt: true }), caps(), SNAP).rxAudio;
    const keyedUp = model(audioState({ ptt: false }), caps(), SNAP).rxAudio;
    expect(keyedDown).toEqual(keyedUp);
  });

  it('is byte-identical whether or not a TX authority snapshot is supplied', () => {
    const withAuthority = toRadioViewModel(
      audioState(), caps(), { radioTx: 'on', txRisk: 'confirmed-on' }, SNAP,
    );
    const withoutAuthority = toRadioViewModel(audioState(), caps(), null, SNAP);
    expect(withAuthority?.rxAudio).toEqual(withoutAuthority?.rxAudio);
  });
});

describe('rxAudio degrades honestly rather than to the shipped panel defaults', () => {
  it('reports the browser volume as AF while monitoring live', () => {
    const rxAudio = model(audioState(), caps(), SNAP).rxAudio!;
    expect(rxAudio.monitorMode).toBe('live');
    expect(rxAudio.afLevel.reading).toEqual({ status: 'known', value: 0.42 });
  });

  it('reports the radio AF level while monitoring locally', () => {
    const local: RxAudioSnapshot = { ...SNAP, rxEnabled: false };
    const rxAudio = model(audioState(), caps(), local).rxAudio!;
    expect(rxAudio.monitorMode).toBe('local');
    expect(rxAudio.afLevel.reading).toEqual({ status: 'known', value: 0.31 });
  });

  it('reports AF unknown when the radio field is unobserved — where the panel substitutes 0.5', () => {
    const local: RxAudioSnapshot = { ...SNAP, rxEnabled: false };
    const state = audioState({
      main: { ...audioState().main, afLevel: undefined } as unknown as ServerState['main'],
      fieldStatus: { ...audioState().fieldStatus, 'main.afLevel': missing },
    });
    const rxAudio = model(state, caps(), local).rxAudio!;
    expect(rxAudio.afLevel.reading).toEqual({ status: 'unknown' });
    expect(rxAudio.afLevel.availability).toEqual({ structural: true, operational: false });
    // The discriminating half: the shipped panel prop fabricates a mid-scale
    // default from exactly this state. The contract must NOT.
    expect(toRxAudioProps(state, caps(), { muted: false, rxEnabled: false, volume: 42 }, true).afLevel)
      .toBe(0.5);
  });

  it.each([
    ['absent', undefined],
    ['malformed', 'loud'],
  ])('reports AF unknown for a %s value the field status claims is available — never 0.5', (_label, value) => {
    // The discriminating half of the pair above: here the gate says
    // "available", so ONLY the value check stands between the contract and the
    // panel's `?? 0.5` fabrication.
    const local: RxAudioSnapshot = { ...SNAP, rxEnabled: false };
    const state = audioState({
      main: { ...audioState().main, afLevel: value } as unknown as ServerState['main'],
    });
    const rxAudio = model(state, caps(), local).rxAudio!;
    expect(rxAudio.afLevel.reading).toEqual({ status: 'unknown' });
    expect(rxAudio.afLevel.availability).toEqual({ structural: true, operational: true });
  });

  it('reports routing unknown when the App never restored the prefs — not \'both\'/false', () => {
    const dualRx = caps({ capabilities: [...caps().capabilities, 'dual_rx'] });
    const noRouting: RxAudioSnapshot = { ...SNAP, routing: null };
    const rxAudio = model(audioState(), dualRx, noRouting).rxAudio!;
    expect(rxAudio.routingFocus.reading).toEqual({ status: 'unknown' });
    expect(rxAudio.routingSplit.reading).toEqual({ status: 'unknown' });
    expect(rxAudio.routingFocus.availability).toEqual({ structural: true, operational: false });
  });

  it('marks routing structurally absent on a single-receiver radio', () => {
    const rxAudio = model(audioState(), caps(), SNAP).rxAudio!;
    expect(rxAudio.routingFocus.availability).toEqual({ structural: false, operational: false });
    expect(rxAudio.routingSplit.reading).toEqual({ status: 'unknown' });
  });

  it('reports the restored routing prefs on a dual-receiver radio', () => {
    const dualRx = caps({ capabilities: [...caps().capabilities, 'dual_rx'] });
    const snapshot: RxAudioSnapshot = { ...SNAP, routing: { focus: 'sub', splitStereo: true } };
    const rxAudio = model(audioState(), dualRx, snapshot).rxAudio!;
    expect(rxAudio.routingFocus.reading).toEqual({ status: 'known', value: 'sub' });
    expect(rxAudio.routingSplit.reading).toEqual({ status: 'known', value: true });
  });

  it('reports the audio link as structurally present but not operational when the WS is down', () => {
    const offline: RxAudioSnapshot = { ...SNAP, connected: false };
    expect(model(audioState(), caps(), offline).rxAudio!.liveAudio)
      .toEqual({ structural: true, operational: false });
  });

  it('follows the active receiver for the AF field status', () => {
    const local: RxAudioSnapshot = { ...SNAP, rxEnabled: false };
    const onSub = audioState({
      active: 'SUB',
      sub: {
        freqHz: 7100000, mode: 'LSB', filter: 1, dataMode: 0, afLevel: 0.77, sMeter: 60,
      } as unknown as ServerState['sub'],
    });
    expect(model(onSub, caps(), local).rxAudio!.afLevel.reading)
      .toEqual({ status: 'known', value: 0.77 });
    const staleSub = audioState({
      ...onSub,
      fieldStatus: { ...audioState().fieldStatus, 'sub.afLevel': stale },
    });
    expect(model(staleSub, caps(), local).rxAudio!.afLevel.reading).toEqual({ status: 'unknown' });
  });
});

/**
 * Monitor-mode parity with the shipped `toRxAudioProps`: the contract adopts
 * the panel's vocabulary, it does not invent a second one.
 */
describe('monitor mode agrees with the shipped RxAudioProps derivation', () => {
  const MATRIX = [false, true].flatMap((muted) => [false, true].flatMap(
    (rxEnabled) => [false, true].map((hasAudio) => ({ muted, rxEnabled, hasAudio })),
  ));

  it.each(MATRIX)('muted=$muted rxEnabled=$rxEnabled audio=$hasAudio', ({ muted, rxEnabled, hasAudio }) => {
    const capabilities = hasAudio
      ? caps()
      : caps({ audio: false, capabilities: ['tx', 'mod_input_routing', 'af_level'] });
    const snapshot: RxAudioSnapshot = { ...SNAP, muted, rxEnabled };
    const state = audioState();
    const expected = toRxAudioProps(state, capabilities, { muted, rxEnabled, volume: 42 }, true);
    const rxAudio = model(state, capabilities, snapshot).rxAudio!;
    expect(rxAudio.monitorMode).toBe(expected.monitorMode);
    expect(rxAudio.liveAudio.structural).toBe(expected.hasLiveAudio);
    expect(rxAudio.afLevel.availability.structural).toBe(expected.hasAfLevel);
  });
});
