/**
 * MOR-1262 decomposition slice 2A — `meters` optional fact group (validator
 * half), plus the R9 authority-parity pin.
 *
 * Companion to `__tests__/tx-aux.test.ts` (slice 1A), whose five kill-tests
 * this file mirrors for the meters family:
 *  1. a meters-absent model validates and round-trips byte-identically
 *  2. `"meters": null` throws (absent ≠ malformed — JSON has no undefined)
 *  3. a malformed inner field throws with a precise `$.meters....` path
 *  5. an unknown extra key inside meters throws
 * (Kill-test 4, the adapter's evidence + TX-authority gate, lives in
 * `lib/runtime/adapters/__tests__/meters-adapter.test.ts` next to the code
 * it mutates.)
 *
 * The last describe block is the safety-critical one: `MeterRfState` and the
 * adapter's private `meterRfState` are copies of the RX/TX surface's own
 * `RfState`/`rfState` (the eslint seam forbids the adapter a value import),
 * so this file pins the copies against the ORIGINALS across every state the
 * REAL TX reducer can reach. Drift is a red test, never a silent fork.
 */
import { describe, expect, it } from 'vitest';
import {
  validateRadioViewModel, type MeterField, type MeterRfState, type MetersViewModel,
} from '../radio-view-model';
import { topologyFixtures, withMeters } from '../fixtures/topologies';
import { rfState, type RfState, type TxAuthoritySnapshot } from '../rx-tx-surface';
import { toRadioViewModel } from '$lib/runtime/adapters/radio-view-model-adapter';
import type { Capabilities } from '$lib/types/capabilities';
import type { ServerState } from '$lib/types/state';

const AVAIL = { structural: true, operational: true } as const;
const base = topologyFixtures['1/single'];
const RF_STATES: readonly MeterRfState[] = ['receiving', 'transmitting', 'uncertain', 'unknown'];

describe('meters (MOR-1262 slice 2A)', () => {
  // ── Kill-test 1: absence is byte-identical, not a new default shape ──────
  it('validates a meters-absent model and never adds the key', () => {
    expect(Object.keys(base)).not.toContain('meters');
    const validated = validateRadioViewModel(base);
    expect(Object.keys(validated)).not.toContain('meters');
    expect(validated.meters).toBeUndefined();
    expect(JSON.parse(JSON.stringify(validated))).toEqual(JSON.parse(JSON.stringify(base)));
  });

  it('validates a fully-populated meters group and returns it unchanged', () => {
    const withM = withMeters(base);
    expect(validateRadioViewModel(withM).meters).toEqual(withM.meters);
  });

  // ── Kill-test 2: null / {} are not absent ────────────────────────────────
  it('rejects an explicit meters: null', () => {
    expect(() => validateRadioViewModel({ ...base, meters: null })).toThrow(TypeError);
  });

  it('rejects an explicit meters: {} — present, not absent, must satisfy its own shape', () => {
    expect(() => validateRadioViewModel({ ...base, meters: {} })).toThrow(TypeError);
  });

  // ── Kill-test 3: malformed inner field, precise path ─────────────────────
  it('rejects a non-numeric swr reading value with a precise error path', () => {
    const withM = withMeters(base);
    const malformed = {
      ...withM,
      meters: {
        ...withM.meters,
        swr: { reading: { status: 'known', value: 'high' }, availability: AVAIL, relevant: true },
      },
    };
    expect(() => validateRadioViewModel(malformed)).toThrow(/\$\.meters\.swr\.reading\.value/);
  });

  it('rejects a non-boolean relevant with a precise error path', () => {
    const withM = withMeters(base);
    const malformed = {
      ...withM,
      meters: {
        ...withM.meters,
        alc: { reading: { status: 'unknown' }, availability: AVAIL, relevant: 'yes' },
      },
    };
    expect(() => validateRadioViewModel(malformed)).toThrow(/\$\.meters\.alc\.relevant/);
  });

  it('rejects an rfState outside the authority vocabulary', () => {
    const withM = withMeters(base);
    const malformed = { ...withM, meters: { ...withM.meters, rfState: 'keyed' } };
    expect(() => validateRadioViewModel(malformed)).toThrow(/\$\.meters\.rfState/);
  });

  it.each(RF_STATES)('accepts the authoritative rfState %s', (state) => {
    const withM = withMeters(base, state);
    expect(validateRadioViewModel(withM).meters?.rfState).toBe(state);
  });

  it('rejects a reading with neither known nor unknown status', () => {
    const withM = withMeters(base);
    const malformed = {
      ...withM,
      meters: {
        ...withM.meters,
        power: { reading: { status: 'stale' }, availability: AVAIL, relevant: true },
      },
    };
    expect(() => validateRadioViewModel(malformed)).toThrow(TypeError);
  });

  it('rejects a non-boolean field inside a meter Availability', () => {
    const withM = withMeters(base);
    const malformed = {
      ...withM,
      meters: {
        ...withM.meters,
        signal: {
          reading: { status: 'unknown' },
          availability: { structural: 'yes', operational: true },
          relevant: true,
        },
      },
    };
    expect(() => validateRadioViewModel(malformed)).toThrow(TypeError);
  });

  it('accepts an unknown reading independently per meter, without degrading the rest', () => {
    const withM = withMeters(base);
    const partial = {
      ...withM,
      meters: {
        ...withM.meters,
        drainCurrent: {
          reading: { status: 'unknown' },
          availability: { structural: true, operational: false },
          relevant: false,
        },
      },
    };
    const validated = validateRadioViewModel(partial);
    expect(validated.meters?.drainCurrent).toEqual({
      reading: { status: 'unknown' },
      availability: { structural: true, operational: false },
      relevant: false,
    });
    expect(validated.meters?.signal.reading).toEqual({ status: 'known', value: 120 });
  });

  // ── Kill-test 5: no speculative keys ─────────────────────────────────────
  it('rejects an unknown extra key inside meters', () => {
    const withM = withMeters(base);
    expect(() => validateRadioViewModel({ ...withM, meters: { ...withM.meters, temperature: 1 } }))
      .toThrow(TypeError);
  });

  it('rejects an unknown extra key inside a single meter field', () => {
    const withM = withMeters(base);
    const malformed = {
      ...withM,
      meters: {
        ...withM.meters,
        power: { reading: { status: 'known', value: 1 }, availability: AVAIL, relevant: true, peak: 2 },
      },
    };
    expect(() => validateRadioViewModel(malformed)).toThrow(TypeError);
  });

  it('rejects a reading that carries a value key while unknown', () => {
    const withM = withMeters(base);
    const malformed = {
      ...withM,
      meters: {
        ...withM.meters,
        drainVoltage: { reading: { status: 'unknown', value: 1 }, availability: AVAIL, relevant: true },
      },
    };
    expect(() => validateRadioViewModel(malformed)).toThrow(TypeError);
  });

  it('every meter of a fully-populated group round-trips its own value', () => {
    const validated = validateRadioViewModel(withMeters(base)).meters as MetersViewModel;
    const knownValue = (f: MeterField): number | undefined =>
      f.reading.status === 'known' ? f.reading.value : undefined;
    expect(knownValue(validated.signal)).toBe(120);
    expect(knownValue(validated.power)).toBe(0.6);
    expect(knownValue(validated.swr)).toBe(20);
    expect(knownValue(validated.alc)).toBe(40);
    expect(knownValue(validated.compression)).toBe(10);
    expect(knownValue(validated.drainVoltage)).toBe(200);
    expect(knownValue(validated.drainCurrent)).toBe(80);
  });
});

/**
 * R9 parity. The adapter cannot call `rx-tx-surface.rfState` (type-only seam),
 * so it carries a copy; these tests prove the copy is not a fork, and that
 * `MeterRfState` is member-for-member the surface's `RfState`.
 */
describe('meters RF state is the App TX authority vocabulary, verbatim (R9)', () => {
  const RADIO_TX = ['off', 'on', 'unknown'] as const;
  const TX_RISK = ['none', 'uncertain', 'confirmed-on'] as const;
  const snapshot = (
    radioTx: (typeof RADIO_TX)[number], txRisk: (typeof TX_RISK)[number],
  ): TxAuthoritySnapshot => ({
    phase: 'idle', intent: null, radioTx, txRisk, mayOwnKey: false, fault: null,
  });

  function caps(): Capabilities {
    return {
      model: 'fixture', scope: false, audio: false, tx: true, capabilities: ['tx'],
      receivers: 1, vfoScheme: 'single', freqRanges: [], modes: [], filters: [],
      audioConfig: { sampleRate: 48000, channels: 1, codecs: ['pcm16'] },
      webrtc: { available: false, enabled: false },
      txBands: [], scopeSource: 'hardware', audioFftAvailable: false,
    } as unknown as Capabilities;
  }
  const state = (): ServerState => ({
    active: 'MAIN', split: false, dualWatch: false, ptt: false,
    txTarget: { status: 'unknown', reason: 'not-observed' },
    main: { freqHz: 14195000, mode: 'USB', filter: 1, sMeter: 120 },
    powerMeter: 0.6, swrMeter: 20, alcMeter: 40, compMeter: 10, vdMeter: 200, idMeter: 80,
    fieldStatus: {},
  } as unknown as ServerState);

  it('the contract union has exactly the members the RX/TX surface declares', () => {
    // EXHAUSTIVE both ways, at compile time. A `satisfies` pair proves only
    // that one literal is in both unions — widening either side with a new
    // member still type-checks (verification finding F3). `Record<K, …>`
    // demands a key per member: a member added to `RfState` leaves `surfaceToMeter`
    // missing a key, and a member added to `MeterRfState` leaves `meterToSurface`
    // missing one. Either way `npm run check` fails.
    const surfaceToMeter: Record<RfState, MeterRfState> = {
      receiving: 'receiving', transmitting: 'transmitting',
      uncertain: 'uncertain', unknown: 'unknown',
    };
    const meterToSurface: Record<MeterRfState, RfState> = surfaceToMeter;
    // ...and the runtime list this file drives its cases from is that same set.
    expect(new Set(RF_STATES)).toEqual(new Set(Object.keys(meterToSurface)));
    expect(Object.entries(meterToSurface).every(([key, value]) => key === value)).toBe(true);
  });

  it.each(RADIO_TX.flatMap((radioTx) => TX_RISK.map((txRisk) => [radioTx, txRisk] as const)))(
    'agrees with the shipped rfState() for radioTx=%s txRisk=%s',
    (radioTx, txRisk) => {
      const tx = snapshot(radioTx, txRisk);
      const view = toRadioViewModel(state(), caps(), tx);
      expect(view?.meters?.rfState).toBe(rfState(tx));
    },
  );
});
