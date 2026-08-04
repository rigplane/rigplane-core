/**
 * MOR-1244 — `txAux` fact-group adapter derivation.
 *
 * Companion to `radio-view-model-adapter.test.ts` (MOR-1065), which this
 * file does NOT modify. That file's fixtures all declare `caps.tx: true`
 * with no txAux-specific capability tag and no txAux field ever set on
 * `state` — under a naive "emit whenever hasTx" gate, `txAux` would have
 * appeared on every model that file builds, changing its hard-coded
 * "carries only contract data" exact-key-list assertion. `deriveTxAux`'s
 * evidence gate (N3, "capability/observed fields") requires MORE than
 * generic TX capability — see the doc comment on `deriveTxAux` — so that
 * file's fixtures correctly still emit no txAux, and this file separately
 * pins that a radio with real txAux evidence DOES get the group.
 */
import { describe, expect, it } from 'vitest';
import type { Capabilities } from '$lib/types/capabilities';
import type { FieldStatus, ServerState } from '$lib/types/state';
import { validateRadioViewModel, type RadioViewModel } from '../../../../semantic/radio-view-model';
import { toRadioViewModel } from '../radio-view-model-adapter';

function caps(overrides: Partial<Capabilities> = {}): Capabilities {
  return {
    model: 'fixture', scope: true, audio: true, tx: true, capabilities: ['scope', 'audio', 'tx'],
    receivers: 1, vfoScheme: 'single', freqRanges: [], modes: [], filters: [],
    audioConfig: { sampleRate: 48000, channels: 1, codecs: ['pcm16'] },
    webrtc: { available: false, enabled: false },
    txBands: [{ start: 14000000, end: 14350000, name: '20m' }],
    scopeSource: 'hardware', audioFftAvailable: false, ...overrides,
  } as Capabilities;
}

const fresh: FieldStatus = { storePath: 'x', observed: true, freshness: 'fresh', availability: 'available' };
const stale: FieldStatus = { storePath: 'x', observed: true, freshness: 'stale', availability: 'stale' };

/** No txAux field ever set, no txAux field status entry — the exact shape
 *  `radio-view-model-adapter.test.ts`'s own `observedState()` uses. */
function bareState(overrides: Partial<ServerState> = {}): ServerState {
  return {
    active: 'MAIN', split: false, dualWatch: false, ptt: false,
    txTarget: { status: 'known', receiver: 'MAIN', slot: null, frequencyHz: 14195000 },
    main: {
      freqHz: 14195000, mode: 'USB', filter: 1, dataMode: 0, att: 0, preamp: 0,
      nb: false, nr: false, afLevel: 1, rfGain: 1, squelch: 0, sMeter: 0,
    },
    fieldStatus: {
      active: fresh, split: fresh, dualWatch: fresh, txTarget: fresh,
      'main.freqHz': fresh, 'main.mode': fresh, 'main.filter': fresh,
    },
    ...overrides,
  } as ServerState;
}

function model(state: ServerState | null, capabilities: Capabilities | null): RadioViewModel {
  const view = toRadioViewModel(state, capabilities);
  expect(view).not.toBeNull();
  return validateRadioViewModel(view);
}

describe('txAux evidence gate (MOR-1244, N3)', () => {
  // Kill-test 4: sever the gate (see the build report's mutation-kill
  // protocol) and this is the test that goes red.
  it('emits no txAux when capabilities are absent', () => {
    expect(toRadioViewModel(bareState(), null)).toBeNull();
  });

  it('emits no txAux for a non-TX radio, even with a vox tag present', () => {
    const view = model(bareState(), caps({ tx: false, capabilities: ['scope', 'audio', 'vox'] }));
    expect(view.txAux).toBeUndefined();
  });

  // The exact scenario `radio-view-model-adapter.test.ts`'s fixtures hit:
  // tx:true, no txAux-specific capability tag, no txAux field ever set.
  it('emits no txAux for a TX radio with zero txAux-specific evidence (regression pin)', () => {
    const view = model(bareState(), caps());
    expect(view.txAux).toBeUndefined();
    expect(Object.keys(view)).not.toContain('txAux');
  });

  it('emits txAux once a single sub-capability tag is present, even with no field observed', () => {
    const view = model(bareState(), caps({ capabilities: ['scope', 'audio', 'tx', 'vox'] }));
    expect(view.txAux).toBeDefined();
  });

  it('emits txAux once a single txAux field is actually observed, even with no sub-capability tag', () => {
    const view = model(bareState({
      powerLevel: 0.75,
      fieldStatus: { ...bareState().fieldStatus, powerLevel: fresh },
    }), caps());
    expect(view.txAux).toBeDefined();
  });

  it('never emits txAux for a receive-only radio (tx capability absent, nothing observed)', () => {
    const view = model(bareState(), caps({ tx: false, capabilities: ['scope', 'audio'] }));
    expect(view.txAux).toBeUndefined();
  });
});

describe('txAux per-field derivation (MOR-1244)', () => {
  const fullCaps = caps({ capabilities: ['scope', 'audio', 'tx', 'vox', 'compressor', 'monitor', 'tuner', 'drive_gain'] });

  it('reports known readings for observed, fresh, capability-backed fields', () => {
    const view = model(bareState({
      tunerStatus: 2, voxOn: true, voxGain: 40, antiVoxGain: 25, voxDelay: 15,
      compressorOn: true, compressorLevel: 60, monitorOn: true, monitorGain: 90,
      powerLevel: 0.9, micGain: 200, driveGain: 210,
      fieldStatus: {
        ...bareState().fieldStatus,
        tunerStatus: fresh, voxOn: fresh, voxGain: fresh, antiVoxGain: fresh, voxDelay: fresh,
        compressorOn: fresh, compressorLevel: fresh, monitorOn: fresh, monitorGain: fresh,
        powerLevel: fresh, micGain: fresh, driveGain: fresh,
      },
    }), fullCaps);
    const txAux = view.txAux!;
    expect(txAux.atu).toEqual({ reading: { status: 'known', value: 'tuning' }, availability: { structural: true, operational: true } });
    expect(txAux.vox).toEqual({ reading: { status: 'known', value: true }, availability: { structural: true, operational: true } });
    expect(txAux.voxGain.reading).toEqual({ status: 'known', value: 40 });
    expect(txAux.antiVoxGain.reading).toEqual({ status: 'known', value: 25 });
    expect(txAux.voxDelay.reading).toEqual({ status: 'known', value: 15 });
    expect(txAux.compressor.reading).toEqual({ status: 'known', value: true });
    expect(txAux.compressorLevel.reading).toEqual({ status: 'known', value: 60 });
    expect(txAux.monitor.reading).toEqual({ status: 'known', value: true });
    expect(txAux.monitorLevel.reading).toEqual({ status: 'known', value: 90 });
    expect(txAux.rfPower.reading).toEqual({ status: 'known', value: 0.9 });
    expect(txAux.micGain.reading).toEqual({ status: 'known', value: 200 });
    expect(txAux.driveGain.reading).toEqual({ status: 'known', value: 210 });
  });

  it.each([[0, 'off'], [1, 'on'], [2, 'tuning']] as const)('maps tunerStatus %d to atu %s', (raw, expected) => {
    const view = model(bareState({
      tunerStatus: raw, fieldStatus: { ...bareState().fieldStatus, tunerStatus: fresh },
    }), fullCaps);
    expect(view.txAux!.atu.reading).toEqual({ status: 'known', value: expected });
  });

  it('degrades a stale field to unknown while keeping structural availability true', () => {
    const view = model(bareState({
      powerLevel: 0.9, fieldStatus: { ...bareState().fieldStatus, powerLevel: stale },
    }), fullCaps);
    expect(view.txAux!.rfPower).toEqual({
      reading: { status: 'unknown' }, availability: { structural: true, operational: false },
    });
  });

  it('marks a control structurally absent when its capability tag is missing, never known', () => {
    const view = model(bareState({
      voxOn: true, fieldStatus: { ...bareState().fieldStatus, voxOn: fresh },
    }), caps({ capabilities: ['scope', 'audio', 'tx', 'compressor'] }));
    // vox capability absent — must never surface a "known" reading even
    // though the (irrelevant) field status looks fresh and a value exists.
    expect(view.txAux!.vox).toEqual({
      reading: { status: 'unknown' }, availability: { structural: false, operational: false },
    });
  });

  it('always marks rfPower and micGain structurally available on any TX radio, no sub-capability required', () => {
    const view = model(bareState(), caps({ capabilities: ['scope', 'audio', 'tx', 'vox'] }));
    expect(view.txAux!.rfPower.availability.structural).toBe(true);
    expect(view.txAux!.micGain.availability.structural).toBe(true);
    // But vox is the only reason the group exists here — compressor/monitor/
    // tuner/driveGain stay structurally absent.
    expect(view.txAux!.compressor.availability.structural).toBe(false);
    expect(view.txAux!.monitor.availability.structural).toBe(false);
    expect(view.txAux!.atu.availability.structural).toBe(false);
    expect(view.txAux!.driveGain.availability.structural).toBe(false);
  });

  it('degrades a malformed raw value (wrong JS type) to unknown rather than throwing or coercing', () => {
    const view = model(bareState({
      compressorLevel: 'ten' as unknown as number,
      fieldStatus: { ...bareState().fieldStatus, compressorLevel: fresh },
    }), fullCaps);
    expect(view.txAux!.compressorLevel.reading).toEqual({ status: 'unknown' });
  });

  it('emits a validator-clean model carrying the txAux group (round-trip proof)', () => {
    const view = model(bareState({
      voxOn: true, voxGain: 10, fieldStatus: { ...bareState().fieldStatus, voxOn: fresh, voxGain: fresh },
    }), fullCaps);
    expect(JSON.parse(JSON.stringify(view))).toEqual(view);
  });
});
