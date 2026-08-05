/**
 * MOR-1262 decomposition slice 3A (MOR-1274) — `rxAudio` optional fact group,
 * validator half, plus the SAFETY-CRITICAL MOD-input readiness parity pin.
 *
 * Companion to `tx-aux.test.ts` (1A) and `meters.test.ts` (2A), whose kill
 * tests this file mirrors for the RX-audio family:
 *  1. an rxAudio-absent model validates and round-trips byte-identically
 *  2. `"rxAudio": null` / `{}` throw (absent ≠ malformed — JSON has no undefined)
 *  3. a malformed inner field throws with a precise `$.rxAudio....` path
 *  5. an unknown extra key inside rxAudio (or a field) throws
 * (Kill-test 4, the adapter's evidence + snapshot gate, lives in
 * `lib/runtime/adapters/__tests__/rx-audio-adapter.test.ts`.)
 *
 * The last describe block is the safety-critical one: the contract's
 * `ModInputReadiness` is a copy of `tx-capabilities.ts`'s own union (a contract
 * must not depend on an adapter), so it is pinned member-for-member against the
 * original AND value-for-value against the real `deriveTxCapabilities` across
 * the readiness matrix. Drift is a red test, never a silent fork — this is the
 * recorded "web voice TX = noise/squeal" guard.
 */
import { describe, expect, it } from 'vitest';
import {
  validateRadioViewModel, type ModInputReadiness, type RxAudioViewModel,
} from '../radio-view-model';
import { topologyFixtures, withRxAudio } from '../fixtures/topologies';
import {
  deriveTxCapabilities, type ModInputReadiness as AdapterModInputReadiness,
  type ModInputSource,
} from '$lib/runtime/adapters/tx-capabilities';
import type { Capabilities } from '$lib/types/capabilities';

const AVAIL = { structural: true, operational: true } as const;
const base = topologyFixtures['1/single'];
const READINESS_STATUSES = ['not-applicable', 'ready', 'mismatch', 'unknown'] as const;

describe('rxAudio (MOR-1262 slice 3A)', () => {
  // ── Kill-test 1: absence is byte-identical, not a new default shape ──────
  it('validates an rxAudio-absent model and never adds the key', () => {
    expect(Object.keys(base)).not.toContain('rxAudio');
    const validated = validateRadioViewModel(base);
    expect(Object.keys(validated)).not.toContain('rxAudio');
    expect(validated.rxAudio).toBeUndefined();
    expect(JSON.parse(JSON.stringify(validated))).toEqual(JSON.parse(JSON.stringify(base)));
  });

  it('validates a fully-populated rxAudio group and returns it unchanged', () => {
    const withRx = withRxAudio(base);
    expect(validateRadioViewModel(withRx).rxAudio).toEqual(withRx.rxAudio);
  });

  // ── Kill-test 2: null / {} are not absent ────────────────────────────────
  it('rejects an explicit rxAudio: null', () => {
    expect(() => validateRadioViewModel({ ...base, rxAudio: null })).toThrow(TypeError);
  });

  it('rejects an explicit rxAudio: {} — present, not absent, must satisfy its own shape', () => {
    expect(() => validateRadioViewModel({ ...base, rxAudio: {} })).toThrow(TypeError);
  });

  // ── Kill-test 3: malformed inner field, precise path ─────────────────────
  it('rejects a monitor mode outside the shipped vocabulary', () => {
    const withRx = withRxAudio(base);
    const malformed = { ...withRx, rxAudio: { ...withRx.rxAudio, monitorMode: 'headphones' } };
    expect(() => validateRadioViewModel(malformed)).toThrow(/\$\.rxAudio\.monitorMode/);
  });

  it('rejects a non-numeric afLevel reading value with a precise error path', () => {
    const withRx = withRxAudio(base);
    const malformed = {
      ...withRx,
      rxAudio: {
        ...withRx.rxAudio,
        afLevel: { reading: { status: 'known', value: 'loud' }, availability: AVAIL },
      },
    };
    expect(() => validateRadioViewModel(malformed)).toThrow(/\$\.rxAudio\.afLevel\.reading\.value/);
  });

  it('rejects a routing focus outside the dual-RX vocabulary', () => {
    const withRx = withRxAudio(base);
    const malformed = {
      ...withRx,
      rxAudio: {
        ...withRx.rxAudio,
        routingFocus: { reading: { status: 'known', value: 'left' }, availability: AVAIL },
      },
    };
    expect(() => validateRadioViewModel(malformed)).toThrow(/\$\.rxAudio\.routingFocus\.reading\.value/);
  });

  it('rejects a readiness status outside the guard vocabulary', () => {
    const withRx = withRxAudio(base);
    const malformed = {
      ...withRx, rxAudio: { ...withRx.rxAudio, modInputReadiness: { status: 'probably-fine' } },
    };
    expect(() => validateRadioViewModel(malformed)).toThrow(/\$\.rxAudio\.modInputReadiness\.status/);
  });

  it('rejects a mismatch readiness with no source — the offending source is the whole point', () => {
    const withRx = withRxAudio(base);
    const malformed = {
      ...withRx, rxAudio: { ...withRx.rxAudio, modInputReadiness: { status: 'mismatch' } },
    };
    expect(() => validateRadioViewModel(malformed)).toThrow(TypeError);
  });

  it('rejects a not-applicable readiness that smuggles a source in', () => {
    const withRx = withRxAudio(base);
    const malformed = {
      ...withRx,
      rxAudio: { ...withRx.rxAudio, modInputReadiness: { status: 'not-applicable', source: 5 } },
    };
    expect(() => validateRadioViewModel(malformed)).toThrow(TypeError);
  });

  it('rejects a non-boolean field inside liveAudio availability', () => {
    const withRx = withRxAudio(base);
    const malformed = {
      ...withRx,
      rxAudio: { ...withRx.rxAudio, liveAudio: { structural: 'yes', operational: true } },
    };
    expect(() => validateRadioViewModel(malformed)).toThrow(/\$\.rxAudio\.liveAudio\.structural/);
  });

  it('accepts an unknown reading independently per fact, without degrading the rest', () => {
    const withRx = withRxAudio(base);
    const partial = {
      ...withRx,
      rxAudio: {
        ...withRx.rxAudio,
        routingFocus: { reading: { status: 'unknown' }, availability: { structural: true, operational: false } },
      },
    };
    const validated = validateRadioViewModel(partial);
    expect(validated.rxAudio?.routingFocus).toEqual({
      reading: { status: 'unknown' }, availability: { structural: true, operational: false },
    });
    expect(validated.rxAudio?.afLevel.reading).toEqual({ status: 'known', value: 0.42 });
  });

  // ── Kill-test 5: no speculative keys ─────────────────────────────────────
  it('rejects an unknown extra key inside rxAudio', () => {
    const withRx = withRxAudio(base);
    expect(() => validateRadioViewModel({ ...withRx, rxAudio: { ...withRx.rxAudio, squelch: 1 } }))
      .toThrow(TypeError);
  });

  it('rejects an unknown extra key inside a single rxAudio field', () => {
    const withRx = withRxAudio(base);
    const malformed = {
      ...withRx,
      rxAudio: {
        ...withRx.rxAudio,
        afLevel: { reading: { status: 'known', value: 0.5 }, availability: AVAIL, relevant: true },
      },
    };
    expect(() => validateRadioViewModel(malformed)).toThrow(TypeError);
  });

  it('rejects a reading that carries a value key while unknown', () => {
    const withRx = withRxAudio(base);
    const malformed = {
      ...withRx,
      rxAudio: {
        ...withRx.rxAudio,
        modInputSource: { reading: { status: 'unknown', value: 5 }, availability: AVAIL },
      },
    };
    expect(() => validateRadioViewModel(malformed)).toThrow(TypeError);
  });

  it.each(READINESS_STATUSES)('accepts the readiness status %s', (status) => {
    const readiness = (status === 'ready' || status === 'mismatch'
      ? { status, source: 0 } : { status }) as ModInputReadiness;
    const validated = validateRadioViewModel(withRxAudio(base, readiness));
    expect(validated.rxAudio?.modInputReadiness).toEqual(readiness);
  });

  it('every fact of a fully-populated group round-trips its own value', () => {
    const rxAudio = validateRadioViewModel(withRxAudio(base)).rxAudio as RxAudioViewModel;
    expect(rxAudio.monitorMode).toBe('live');
    expect(rxAudio.afLevel.reading).toEqual({ status: 'known', value: 0.42 });
    expect(rxAudio.routingFocus.reading).toEqual({ status: 'known', value: 'both' });
    expect(rxAudio.routingSplit.reading).toEqual({ status: 'known', value: false });
    expect(rxAudio.modInputSource.reading).toEqual({ status: 'known', value: 5 });
    expect(rxAudio.modInputReadiness).toEqual({ status: 'ready', source: 5 });
  });
});

/**
 * SAFETY CONSTRAINT 2. The contract carries a copy of the adapter's
 * `ModInputReadiness`; these tests prove the copy is not a fork, and that the
 * shipped `deriveTxCapabilities` derivation is the ONE source of readiness.
 */
describe('MOD-input readiness is the tx-capabilities derivation, verbatim (web-voice-TX guard)', () => {
  it('the contract union has exactly the members tx-capabilities declares', () => {
    // EXHAUSTIVE both ways at compile time, same technique as the meters
    // `RfState` pin (MOR-1269 finding F3): `Record<K, …>` demands a key per
    // member, so widening EITHER union breaks `npm run check`.
    const adapterToContract: Record<AdapterModInputReadiness['status'], ModInputReadiness['status']> = {
      'not-applicable': 'not-applicable', ready: 'ready', mismatch: 'mismatch', unknown: 'unknown',
    };
    const contractToAdapter: Record<ModInputReadiness['status'], AdapterModInputReadiness['status']> =
      adapterToContract;
    expect(new Set(READINESS_STATUSES)).toEqual(new Set(Object.keys(contractToAdapter)));
    expect(Object.entries(contractToAdapter).every(([key, value]) => key === value)).toBe(true);
  });

  function caps(overrides: Partial<Capabilities> = {}): Capabilities {
    return {
      model: 'fixture', scope: false, audio: true, tx: true,
      capabilities: ['audio', 'tx', 'mod_input_routing'],
      receivers: 1, vfoScheme: 'single', freqRanges: [], modes: [], filters: [],
      audioConfig: { sampleRate: 48000, channels: 1, codecs: ['pcm16'] },
      webrtc: { available: false, enabled: false },
      txBands: [], scopeSource: 'hardware', audioFftAvailable: false,
      audioTxRequiredModInputSource: 5, ...overrides,
    } as unknown as Capabilities;
  }

  // The full readiness matrix: routing capability × required-source config ×
  // observed source. Each row states the fact the group must carry.
  const MATRIX: ReadonlyArray<{
    name: string; caps: Capabilities; source: ModInputSource;
  }> = [
    { name: 'LAN observed, LAN required → ready', caps: caps(), source: { status: 'known', source: 5 } },
    { name: 'MIC observed, LAN required → mismatch (the noise failure)', caps: caps(), source: { status: 'known', source: 0 } },
    { name: 'USB observed, LAN required → mismatch', caps: caps(), source: { status: 'known', source: 3 } },
    { name: 'source unread → unknown, never assumed ready', caps: caps(), source: { status: 'unknown' } },
    {
      name: 'no required source configured → not-applicable',
      caps: caps({ audioTxRequiredModInputSource: null }), source: { status: 'known', source: 0 },
    },
    {
      name: 'radio without mod_input_routing → not-applicable',
      caps: caps({ capabilities: ['audio', 'tx'] }), source: { status: 'known', source: 0 },
    },
  ];

  it.each(MATRIX)('$name', ({ caps: capabilities, source }) => {
    const derived = deriveTxCapabilities(capabilities, {
      txTarget: { status: 'unknown', reason: 'not-observed' }, modInputSource: source,
    }).modInputReadiness;
    // The contract accepts exactly what the shipped derivation produces —
    // no adaptation, no re-derivation, no widening.
    const validated = validateRadioViewModel(withRxAudio(base, derived));
    expect(validated.rxAudio?.modInputReadiness).toEqual(derived);
  });

  it('the matrix actually exercises every readiness status (no silent coverage gap)', () => {
    const statuses = MATRIX.map(({ caps: capabilities, source }) => deriveTxCapabilities(capabilities, {
      txTarget: { status: 'unknown', reason: 'not-observed' }, modInputSource: source,
    }).modInputReadiness.status);
    expect(new Set(statuses)).toEqual(new Set(READINESS_STATUSES));
  });
});
