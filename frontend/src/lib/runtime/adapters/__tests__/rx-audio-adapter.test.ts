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
 *  1. MOD-input readiness IS `deriveTxCapabilities`'s conclusion, and source
 *     projection follows the canonical observed field status.
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
    dataModeCount: 3,
    dataModeInputs: [0, 1, 2, 3, 4, 5].map(value => ({ value, label: String(value) })),
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

  it('hides MOD input when profile source metadata is absent', () => {
    const rxAudio = model(audioState(), caps({ dataModeInputs: undefined }), SNAP).rxAudio!;
    expect(rxAudio.modInputChoices).toEqual([]);
    expect(rxAudio.modInputSource.availability).toEqual({ structural: false, operational: false });
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
 * conclusion and source projection stays on the observed field contract.
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

  it('degrades an unobserved source to unknown readiness — never assumed ready', () => {
    const state = audioState({
      fieldStatus: { ...audioState().fieldStatus, dataOffModInput: missing },
    });
    const rxAudio = model(state, caps(), SNAP).rxAudio!;
    expect(rxAudio.modInputReadiness).toEqual({ status: 'unknown' });
    expect(rxAudio.modInputSource.reading).toEqual({ status: 'unknown' });
    expect(rxAudio.modInputSource.availability).toEqual({ structural: true, operational: false });
  });

  // MOR-2425/R40: a held source still answers "which input", so readiness derives.
  it('holds a stale source and keeps deriving readiness from it', () => {
    const state = audioState({
      fieldStatus: { ...audioState().fieldStatus, dataOffModInput: stale },
    });
    const rxAudio = model(state, caps(), SNAP).rxAudio!;
    const fresh = model(audioState(), caps(), SNAP).rxAudio!;
    expect(rxAudio.modInputSource.reading).toEqual(fresh.modInputSource.reading);
    expect(rxAudio.modInputSource.availability).toEqual({ structural: true, operational: true });
    expect(rxAudio.modInputReadiness).toEqual(fresh.modInputReadiness);
  });

  it('marks the source structurally absent on a radio without MOD-input routing', () => {
    const noRouting = caps({ capabilities: ['audio', 'tx', 'af_level'] });
    const rxAudio = model(audioState(), noRouting, SNAP).rxAudio!;
    expect(rxAudio.modInputSource.availability).toEqual({ structural: false, operational: false });
    expect(rxAudio.modInputReadiness).toEqual({ status: 'not-applicable' });
  });

  it.each([
    ['LAN', 5], ['MIC', 0], ['USB', 3],
  ])('projects the observed %s source without a browser TX authority', (_label, source) => {
    const state = audioState({ dataOffModInput: source });
    const rxAudio = model(state, caps(), SNAP).rxAudio!;
    expect(rxAudio.modInputSource.reading).toEqual({ status: 'known', value: source });
    expect(model(state, caps(), SNAP).rxAudio!.modInputReadiness)
      .toEqual(deriveTxCapabilities(caps(), {
        txTarget: { status: 'known', receiver: 'MAIN', slot: null, frequencyHz: 14195000 },
        modInputSource: { status: 'known', source },
      }).modInputReadiness);
  });

  it('projects an unobserved source as unknown', () => {
    const state = audioState({
      fieldStatus: { ...audioState().fieldStatus, dataOffModInput: missing },
    });
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

  it('reports AF unknown when the radio field is unobserved — matching the panel contract (MOR-1409 A12: no longer 0.5)', () => {
    const local: RxAudioSnapshot = { ...SNAP, rxEnabled: false };
    const state = audioState({
      main: { ...audioState().main, afLevel: undefined } as unknown as ServerState['main'],
      fieldStatus: { ...audioState().fieldStatus, 'main.afLevel': missing },
    });
    const rxAudio = model(state, caps(), local).rxAudio!;
    expect(rxAudio.afLevel.reading).toEqual({ status: 'unknown' });
    expect(rxAudio.afLevel.availability).toEqual({ structural: true, operational: false });
    // MOR-1409 A12 (Core #2317): the shipped panel prop used to fabricate a
    // mid-scale 0.5 default from exactly this state — the divergence this
    // test used to document. `toRxAudioProps.afLevel` now matches the
    // honest model's `unknown` reading with its own `NaN` sentinel.
    expect(toRxAudioProps(state, caps(), { muted: false, rxEnabled: false, volume: 42 }, true).afLevel)
      .toBeNaN();
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
    const dualRx = caps({ capabilities: [...caps().capabilities, 'dual_rx', 'lan_dual_rx_audio_routing'] });
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

  // MOR-2527: `dual_rx` alone (the FTX-1 shape) is NOT proof of dual-receiver
  // audio routing — the server refuses routing without the separate
  // `lan_dual_rx_audio_routing` capability, so the rows are structurally
  // ABSENT, never present-and-unreadable. Fails on origin/main, where the
  // gate was `dual_rx` itself.
  it('marks routing structurally absent with dual_rx but no lan_dual_rx_audio_routing (FTX-1 shape)', () => {
    const dualOnly = caps({ capabilities: [...caps().capabilities, 'dual_rx'] });
    const snapshot: RxAudioSnapshot = { ...SNAP, routing: { focus: 'sub', splitStereo: true } };
    const rxAudio = model(audioState(), dualOnly, snapshot).rxAudio!;
    expect(rxAudio.routingFocus.availability).toEqual({ structural: false, operational: false });
    expect(rxAudio.routingSplit.availability).toEqual({ structural: false, operational: false });
    expect(rxAudio.routingFocus.reading).toEqual({ status: 'unknown' });
    expect(rxAudio.routingSplit.reading).toEqual({ status: 'unknown' });
  });

  it('reports the restored routing prefs on a dual-receiver radio with the routing capability', () => {
    const dualRx = caps({ capabilities: [...caps().capabilities, 'dual_rx', 'lan_dual_rx_audio_routing'] });
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
    // MOR-2425/R29: a stale-but-observed afLevel carries its last value
    // (`onSub`'s `sub.afLevel` is 0.77) and is `available`, not degraded.
    const staleSub = audioState({
      ...onSub,
      fieldStatus: { ...audioState().fieldStatus, 'sub.afLevel': stale },
    });
    expect(model(staleSub, caps(), local).rxAudio!.afLevel.reading).toEqual({ status: 'known', value: 0.77 });
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

/**
 * MOR-2579 — outside `live`, a radio whose field status declares SUB AF gets
 * one AF field per receiver, read by path. Selecting MAIN/SUB moves neither.
 */
describe('rxAudio carries AF per receiver on a radio that declares SUB AF (MOR-2579)', () => {
  const local: RxAudioSnapshot = { ...SNAP, rxEnabled: false };
  const dualCaps = () => caps({
    receivers: 2, vfoScheme: 'main_sub',
    capabilities: ['audio', 'tx', 'mod_input_routing', 'af_level', 'dual_rx'],
  });
  const dualState = (over: Partial<ServerState> = {}, subAf: FieldStatus | null = fresh) => {
    const fieldStatus: Record<string, FieldStatus> = {
      ...audioState().fieldStatus, 'sub.freqHz': fresh, 'sub.mode': fresh,
    };
    if (subAf !== null) fieldStatus['sub.afLevel'] = subAf;
    return audioState({
      providerGeneration: 1,
      sub: {
        freqHz: 7100000, mode: 'LSB', filter: 1, dataMode: 0, afLevel: 0.77, sMeter: 60,
      } as unknown as ServerState['sub'],
      fieldStatus,
      ...over,
    });
  };
  const known = (value: number) => ({
    reading: { status: 'known', value }, availability: { structural: true, operational: true },
  });

  it.each(['MAIN', 'SUB'] as const)('reports MAIN 0.31 and SUB 0.77 whichever receiver is selected (%s)', (active) => {
    const rxAudio = model(dualState({ active }), dualCaps(), local).rxAudio!;
    expect(rxAudio.receiverAfLevels).toEqual({ main: known(0.31), sub: known(0.77) });
  });

  it('keeps an unread SUB AF present but unlit — never a stand-in value', () => {
    const unread: FieldStatus = { ...missing, freshness: 'unknown' };
    const rxAudio = model(dualState({}, unread), dualCaps(), local).rxAudio!;
    expect(rxAudio.receiverAfLevels?.sub).toEqual({
      reading: { status: 'unknown' }, availability: { structural: true, operational: false },
    });
    expect(rxAudio.receiverAfLevels?.main).toEqual(known(0.31));
  });

  it.each([
    ['SUB AF undeclared for this radio', () => model(dualState({}, {
      storePath: 'sub.afLevel', observed: false, freshness: 'unknown', availability: 'undeclared',
    }), dualCaps(), local)],
    ['no SUB AF field status at all', () => model(dualState({}, null), dualCaps(), local)],
    ['live monitoring (AF is the browser volume)', () => model(dualState(), dualCaps(), SNAP)],
    ['no radio AF control', () => model(dualState(), caps({
      receivers: 2, vfoScheme: 'main_sub', capabilities: ['audio', 'tx', 'mod_input_routing', 'dual_rx'],
    }), local)],
    ['a single-receiver radio', () => model(dualState(), caps(), local)],
  ])('carries no per-receiver AF with %s', (_label, build) => {
    const rxAudio = build().rxAudio;
    expect(rxAudio).toBeDefined();
    expect(Object.keys(rxAudio!)).not.toContain('receiverAfLevels');
  });
});
