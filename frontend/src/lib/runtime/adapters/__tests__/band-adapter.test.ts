/**
 * MOR-1262 decomposition slice 7A (MOR-1294) — `band` fact-group adapter
 * derivation. SAFETY-ADJACENT.
 *
 * Companion to `rf-front-end-adapter.test.ts` (MOR-1292/1293), which this
 * file does NOT modify. `band` is a SEPARATE optional group — see
 * `radio-view-model.ts`'s `BandViewModel` doc comment.
 *
 * The parity pins below call the REAL `getFrequencyPermit`/`getTxPermit`
 * (`$lib/utils/tx-permit`) and the REAL `flattenBands`/`findActiveBand`
 * (`$lib/radio/band-plan`) — never a reimplementation — so agreement is
 * against the shipped derivations themselves, not an assumption about them.
 * That is the whole point of the slice's safety constraint: ONE permit
 * derivation, consumed, never re-derived.
 *
 * None of this group's facts consume a capabilities-STORE-backed helper (both
 * band-plan lookups take `freqRanges` as an explicit parameter), so this file
 * never calls the real `setCapabilities` and does not need the isolated pool
 * (MOR-1272) — the determinism pin below asserts that property directly.
 */
import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { describe, expect, it } from 'vitest';
import type { Capabilities, FreqRange, TxBand } from '$lib/types/capabilities';
import type { FieldStatus, ServerState } from '$lib/types/state';
import { validateRadioViewModel, type RadioViewModel } from '../../../../semantic/radio-view-model';
import { toRadioViewModel } from '../radio-view-model-adapter';
import { getFrequencyPermit, getTxPermit } from '$lib/utils/tx-permit';
import { findActiveBand, flattenBands } from '$lib/radio/band-plan';

const HF_RANGES: FreqRange[] = [
  {
    start: 30000, end: 60000000, label: 'HF',
    bands: [
      { name: '40m', start: 7000000, end: 7300000, default: 7100000, bsrCode: 2 },
      { name: '20m', start: 14000000, end: 14350000, default: 14195000, bsrCode: 5 },
      { name: 'MW', start: 520000, end: 1710000, default: 1000000 },
    ],
  },
];
const HAM_TX_BANDS: TxBand[] = [
  { name: '40m', start: 7000000, end: 7300000 },
  { name: '20m', start: 14000000, end: 14350000 },
];

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
const stale: FieldStatus = { storePath: 'x', observed: true, freshness: 'stale', availability: 'stale' };
/** The two remaining ways `seen()` can fail on a field that HAS a status
 *  entry — never observed, and observed-but-unavailable (MOR-1356). */
const unobserved: FieldStatus = { storePath: 'x', observed: false, freshness: 'unknown', availability: 'missing' };
const missing: FieldStatus = { storePath: 'x', observed: true, freshness: 'fresh', availability: 'missing' };

/** The exact shape `rf-front-end-adapter.test.ts`'s own baseline uses. */
function bareState(overrides: Partial<ServerState> = {}): ServerState {
  return {
    active: 'MAIN', split: false, dualWatch: false, ptt: false,
    txTarget: { status: 'known', receiver: 'MAIN', slot: null, frequencyHz: 14195000 },
    main: {
      freqHz: 14195000, mode: 'USB', filter: 1, dataMode: 0, att: 0, preamp: 0,
      nb: false, nr: false, afLevel: 1, rfGain: 1, squelch: 0, sMeter: 0,
    },
    sub: {
      freqHz: 7100000, mode: 'LSB', filter: 2, dataMode: 0, att: 0, preamp: 0,
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

const hfCaps = caps({ freqRanges: HF_RANGES, txBands: HAM_TX_BANDS });

describe('band evidence gate (MOR-1294, N3)', () => {
  it('emits no band when capabilities are absent', () => {
    expect(toRadioViewModel(bareState(), null)).toBeNull();
  });

  it('emits no band for a radio declaring no freqRanges (regression pin on the baseline fixtures)', () => {
    const view = model(bareState(), caps());
    expect(view.band).toBeUndefined();
    expect(Object.keys(view)).not.toContain('band');
  });

  it('emits band once a freqRange is declared, even with no named bands in it', () => {
    const view = model(bareState(), caps({ freqRanges: [{ start: 30000, end: 60000000, label: 'HF' }] }));
    expect(view.band).toBeDefined();
    expect(view.band!.bandChoices).toEqual([]);
    // No band plan ⇒ no current-band concept at all: structurally absent.
    expect(view.band!.currentBand.availability.structural).toBe(false);
    expect(view.band!.currentBandTx).toBe('denied');
  });
});

describe('band choice set (MOR-1294)', () => {
  it('is the shipped flattenBands output over the caps argument, verbatim', () => {
    const view = model(bareState(), hfCaps);
    expect(view.band!.bandChoices.map((c) => ({ name: c.name, startHz: c.startHz, endHz: c.endHz, defaultHz: c.defaultHz })))
      .toEqual(flattenBands(HF_RANGES).map((b) => ({
        name: b.name, startHz: b.start, endHz: b.end, defaultHz: b.defaultFreq,
      })));
  });

  it('never invents a band table — a radio with ranges but no bands gets an empty choice set (X6200 lesson)', () => {
    const view = model(bareState(), caps({ freqRanges: [{ start: 30000, end: 60000000, label: 'HF' }] }));
    expect(view.band!.bandChoices).toEqual([]);
  });

  it('reports an absent bsrCode as null, never a fabricated 0 (0 is a real BSR index)', () => {
    const view = model(bareState(), hfCaps);
    expect(view.band!.bandChoices.find((c) => c.name === 'MW')!.bsrCode).toBeNull();
    expect(view.band!.bandChoices.find((c) => c.name === '40m')!.bsrCode).toBe(2);
  });

  it('omits a band with a non-finite boundary or default rather than emitting a fabricated one', () => {
    const broken: FreqRange[] = [{
      start: 30000, end: 60000000, label: 'HF',
      bands: [
        { name: '20m', start: 14000000, end: 14350000, default: 14195000 },
        { name: 'BAD', start: 21000000, end: 21450000, default: Number.NaN },
      ],
    }];
    const view = model(bareState(), caps({ freqRanges: broken, txBands: HAM_TX_BANDS }));
    expect(view.band!.bandChoices.map((c) => c.name)).toEqual(['20m']);
  });
});

/**
 * SAFETY — THE ONE PERMIT DERIVATION (MOR-1294). Every per-band permit must
 * deep-equal the REAL `getFrequencyPermit(defaultHz, caps.txBands)`, across
 * every discriminating combination of the tri-state. A re-derivation (or the
 * fail-open shortcut of reading permission off the band PLAN instead of
 * `txBands`) goes red here.
 */
describe('per-band TX permit parity with the shipped tri-state (MOR-1294)', () => {
  const PERMIT_CASES: ReadonlyArray<readonly [label: string, txBands: TxBand[] | null]> = [
    ['named TX allocations (allowed + denied mix)', HAM_TX_BANDS],
    ['an unnamed TX allocation (allowed with band: null)', [{ name: '', start: 7000000, end: 7300000 }]],
    ['an explicit empty list (deny-all)', []],
    ['unconfigured ranges (null)', null],
  ];

  it.each(PERMIT_CASES)('agrees with getFrequencyPermit for every band under %s', (_label, txBands) => {
    const view = model(bareState(), caps({ freqRanges: HF_RANGES, txBands }));
    for (const choice of view.band!.bandChoices) {
      expect(choice.defaultHzTxPermit).toEqual(getFrequencyPermit(choice.defaultHz, txBands));
    }
  });

  it('covers all three tri-state branches across the fixture matrix (the pin above is discriminating)', () => {
    const statuses = new Set<string>();
    for (const [, txBands] of PERMIT_CASES) {
      const view = model(bareState(), caps({ freqRanges: HF_RANGES, txBands }));
      view.band!.bandChoices.forEach((c) => statuses.add(c.defaultHzTxPermit.status));
    }
    expect(statuses).toEqual(new Set(['allowed', 'denied', 'unknown']));
  });

  it('denies an out-of-band plan entry even though the band plan itself lists it (never fail-open)', () => {
    const view = model(bareState(), hfCaps);
    const mw = view.band!.bandChoices.find((c) => c.name === 'MW')!;
    expect(mw.defaultHzTxPermit).toEqual({ status: 'denied', reason: 'outside-configured-ranges' });
  });

  it('reports unknown — never allowed — when TX ranges are unconfigured', () => {
    const view = model(bareState(), caps({ freqRanges: HF_RANGES, txBands: null }));
    expect(view.band!.bandChoices.every((c) => c.defaultHzTxPermit.status === 'unknown')).toBe(true);
    expect(view.band!.currentBandTx).toBe('denied');
  });
});

/**
 * SAFETY — THE LIVE-FREQUENCY PERMIT (MOR-1294 verify F1). `currentBandTx`
 * must be evaluated at the frequency the operator is ACTUALLY on, never
 * inherited from `bandChoices[].defaultHzTxPermit`'s point sample at the
 * band's default.
 *
 * The discriminator is the verifier's own: a `txBands` segment NARROWER than
 * the band-plan band it sits in, with the band's `defaultHz` INSIDE the
 * segment. The point sample then reads `allowed` while a live frequency
 * higher up the same band is denied — the fail-open the F1 ruling names, and
 * a common real shape (WARC segments, regional sub-bands, 60m channels).
 * Every assertion is against the REAL `getFrequencyPermit`/`getTxPermit`.
 */
describe('currentBandTx is the LIVE-frequency permit, not the defaultHz sample (MOR-1294 F1)', () => {
  /** 20m plan spans 14.000–14.350; the allocation stops at 14.250, and the
   *  plan's own defaultHz (14.195) sits INSIDE it. */
  const SEGMENT: TxBand[] = [{ name: '20m-lower', start: 14000000, end: 14250000 }];
  const segmentCaps = caps({ freqRanges: HF_RANGES, txBands: SEGMENT });

  function at(freqHz: number) {
    return model(bareState({ main: { ...bareState().main, freqHz } }), segmentCaps);
  }

  it('sanity: the fixture really produces the narrower-than-band shape (choice count + in-segment sample)', () => {
    const view = at(14195000);
    // Guard against the flat-freqRanges shape trap: a degenerate fixture
    // yields zero choices and fakes a PASS on every row below.
    expect(view.band!.bandChoices).toHaveLength(3);
    const twenty = view.band!.bandChoices.find((c) => c.name === '20m')!;
    expect(twenty.defaultHz).toBe(14195000);
    expect(twenty.defaultHzTxPermit).toEqual({ status: 'allowed', band: '20m-lower' });
    expect(twenty.endHz).toBeGreaterThan(SEGMENT[0].end);
  });

  it('DENIES a live frequency above the segment even though the band sample says allowed (the F1 probe)', () => {
    const view = at(14300000);
    expect(view.band!.currentBand.reading).toEqual({ status: 'known', value: '20m' });
    // The point sample still reads allowed — that is its correct, narrow meaning…
    expect(view.band!.bandChoices.find((c) => c.name === '20m')!.defaultHzTxPermit.status).toBe('allowed');
    // …and the live answer must NOT inherit it.
    expect(getFrequencyPermit(14300000, SEGMENT)).toEqual({ status: 'denied', reason: 'outside-configured-ranges' });
    expect(view.band!.currentBandTx).toBe('denied');
  });

  it('ALLOWS a live frequency inside the segment — the pin is not vacuously denying everything', () => {
    const view = at(14100000);
    expect(view.band!.currentBandTx).toBe('allowed');
  });

  it.each([14100000, 14195000, 14250000, 14250001, 14300000, 14349999])(
    'agrees with the shipped getTxPermit at the LIVE frequency %i (segment-boundary sweep)',
    (freqHz) => {
      expect(at(freqHz).band!.currentBandTx).toBe(getTxPermit(freqHz, SEGMENT));
    },
  );
});

/**
 * SAFETY — FAIL-CLOSED CURRENT-BAND PERMISSION (MOR-1294 constraint 2, the
 * MOR-1293 DIGI-SEL precedent). `currentBandTx` is 'allowed' only on a
 * positively known band, with a choice entry, and a positively allowed
 * LIVE-frequency permit; every unknown input reads 'denied'. Parity against
 * the shipped `getTxPermit` collapse ("unknown fails closed") is asserted,
 * not assumed.
 */
describe('currentBandTx fails closed (MOR-1294)', () => {
  it('allows only a known, in-band current band, and agrees with the shipped getTxPermit collapse', () => {
    const view = model(bareState(), hfCaps);
    expect(view.band!.currentBand.reading).toEqual({ status: 'known', value: '20m' });
    expect(view.band!.currentBandTx).toBe('allowed');
    expect(view.band!.currentBandTx).toBe(getTxPermit(14195000, HAM_TX_BANDS));
  });

  const CLOSED_CASES: ReadonlyArray<readonly [label: string, state: () => ServerState, capabilities: Capabilities]> = [
    [
      'the frequency was never observed',
      () => {
        const main = { ...bareState().main } as Record<string, unknown>;
        delete main.freqHz;
        const status = { ...bareState().fieldStatus };
        delete (status as Record<string, unknown>)['main.freqHz'];
        return bareState({ main: main as unknown as ServerState['main'], fieldStatus: status });
      },
      hfCaps,
    ],
    [
      'the frequency reading is stale',
      () => bareState({ fieldStatus: { ...bareState().fieldStatus, 'main.freqHz': stale } }),
      hfCaps,
    ],
    [
      'the frequency lies outside every band of the plan',
      () => bareState({ main: { ...bareState().main, freqHz: 9000000 } }),
      hfCaps,
    ],
    [
      'the current band is in the plan but outside the TX allocations',
      () => bareState({ main: { ...bareState().main, freqHz: 1000000 } }),
      hfCaps,
    ],
    [
      'the TX ranges are unconfigured',
      () => bareState(),
      caps({ freqRanges: HF_RANGES, txBands: null }),
    ],
    [
      'the live frequency is outside a TX segment narrower than its band (verify F1)',
      () => bareState({ main: { ...bareState().main, freqHz: 14300000 } }),
      caps({ freqRanges: HF_RANGES, txBands: [{ name: '20m-lower', start: 14000000, end: 14250000 }] }),
    ],
    [
      'the plan names the current band but its entry was omitted as malformed',
      () => bareState({ main: { ...bareState().main, freqHz: 21200000 } }),
      caps({
        freqRanges: [{
          start: 30000, end: 60000000, label: 'HF',
          bands: [{ name: '15m', start: 21000000, end: 21450000, default: Number.NaN }],
        }],
        txBands: [{ name: '15m', start: 21000000, end: 21450000 }],
      }),
    ],
  ];

  it.each(CLOSED_CASES)('reads denied when %s', (_label, makeState, capabilities) => {
    const view = model(makeState(), capabilities);
    expect(view.band!.currentBandTx).toBe('denied');
  });

  it('keeps the current band unknown — never the shipped 14.074 MHz stand-in — when the frequency is unobserved', () => {
    const main = { ...bareState().main } as Record<string, unknown>;
    delete main.freqHz;
    const view = model(bareState({ main: main as unknown as ServerState['main'] }), hfCaps);
    expect(view.band!.currentBand.reading).toEqual({ status: 'unknown' });
    expect(view.band!.currentBandTx).toBe('denied');
  });

  it('degrades a stale frequency to an unknown band while keeping structural availability true', () => {
    const view = model(bareState({
      fieldStatus: { ...bareState().fieldStatus, 'main.freqHz': stale },
    }), hfCaps);
    expect(view.band!.currentBand).toEqual({
      reading: { status: 'unknown' }, availability: { structural: true, operational: false },
    });
  });

  it('degrades a malformed raw frequency (wrong JS type) to unknown rather than coercing', () => {
    const view = model(bareState({
      main: { ...bareState().main, freqHz: '14.195' as unknown as number },
    }), hfCaps);
    expect(view.band!.currentBand.reading).toEqual({ status: 'unknown' });
    expect(view.band!.currentBandTx).toBe('denied');
  });
});

/**
 * SAFETY — THE PERMIT IS SCOPED TO AN OBSERVED RECEIVER (MOR-1356, 7A
 * follow-up). `deriveBand` picks the receiver it samples from the RAW
 * `state.active`, which carries no evidence of its own; `activeReceiver`
 * (the model's own answer to "which receiver is live") requires the
 * three-part `seen()` gate. The model could therefore report
 * `activeReceiver: unknown` and, in the same breath, a `currentBandTx:
 * 'allowed'` scoped to a receiver it never confirmed — a TX permit resting
 * on a guess.
 *
 * The gate added here is the SAME criterion `activeReceiver` uses, not a
 * parallel one: the sameness is asserted structurally below (every state in
 * the matrix must agree), so a future divergence in either direction goes
 * red. The fail-closed representation is `'denied'` — the shipped
 * `getTxPermit` collapse this group already uses everywhere else for an
 * unknown input ("unknown fails closed", `$lib/utils/tx-permit`), NOT a new
 * tri-state: `currentBandTx` is `TxPermit`, validated against
 * `TX_PERMITS = ['allowed', 'denied']` (`radio-view-model.ts`).
 *
 * SCOPE: only the permit. Every other band fact keeps the ordinary-fact
 * convention 7A landed with (pinned by the last case here).
 *
 * MOR-1421 note: this describe block used to run the whole matrix against
 * `hfCaps` (this file's default `caps()`, a SINGLE-receiver/`vfoScheme:
 * 'single'` fixture). That made the fixture accidentally self-contradicting
 * once `activeReceiverId` became capability-aware (MOR-1421): a radio whose
 * capabilities declare exactly one receiver has no "which receiver" question
 * left to leave unconfirmed — `activeReceiver` is a tautological `'MAIN'`
 * there regardless of the `active` field, by design, and `hfCaps` no longer
 * exercises "unconfirmed" at all. The safety property this block guards —
 * a permit must not rest on a GUESSED receiver — is real only where the
 * receiver identity is genuinely ambiguous, i.e. a multi-receiver radio, so
 * the matrix now runs against `dualCaps` (`main_sub`, `receivers: 2`). The
 * single-receiver tautology itself is pinned separately in
 * `radio-view-model-adapter.test.ts` ("single-receiver topology" describe,
 * MOR-1421).
 */
describe('currentBandTx fails closed on an unconfirmed active receiver (MOR-1356)', () => {
  /** MAIN at 14.195, inside the 20m TX allocation: the permit derivation
   *  itself says 'allowed', so every denial below is the GATE, not the
   *  frequency — the pin cannot pass vacuously. */
  const LIVE_HZ = 14195000;

  const dualCaps = caps({
    freqRanges: HF_RANGES, txBands: HAM_TX_BANDS, receivers: 2, vfoScheme: 'main_sub',
    capabilities: ['tx', 'dual_rx'],
  });

  const UNCONFIRMED: ReadonlyArray<readonly [label: string, state: () => ServerState]> = [
    [
      'the active-receiver field was never observed at all (no status entry)',
      () => {
        const status = { ...bareState().fieldStatus };
        delete (status as Record<string, unknown>).active;
        return bareState({ fieldStatus: status });
      },
    ],
    [
      'the active-receiver field carries an unobserved status',
      () => bareState({ fieldStatus: { ...bareState().fieldStatus, active: unobserved } }),
    ],
    [
      'the active-receiver reading is stale',
      () => bareState({ fieldStatus: { ...bareState().fieldStatus, active: stale } }),
    ],
    [
      'the active-receiver field is observed and fresh but not available',
      () => bareState({ fieldStatus: { ...bareState().fieldStatus, active: missing } }),
    ],
    [
      'the raw active value is not a receiver the model recognises',
      () => bareState({ active: 'BOTH' as unknown as ServerState['active'] }),
    ],
  ];

  it('sanity: the same fixture with a confirmed active receiver really reads allowed', () => {
    const view = model(bareState(), dualCaps);
    expect(view.activeReceiver).toEqual({ status: 'known', receiver: 'MAIN' });
    expect(view.band!.currentBandTx).toBe('allowed');
    expect(getTxPermit(LIVE_HZ, HAM_TX_BANDS)).toBe('allowed');
  });

  it.each(UNCONFIRMED)('reads denied when %s', (_label, makeState) => {
    const view = model(makeState(), dualCaps);
    // The model itself does not know which receiver is live…
    expect(view.activeReceiver).toEqual({ status: 'unknown' });
    // …so the frequency-only permit (which still says allowed) must not be
    // promoted into a permit for a receiver nobody confirmed.
    expect(getTxPermit(LIVE_HZ, HAM_TX_BANDS)).toBe('allowed');
    expect(view.band!.currentBandTx).toBe('denied');
  });

  it('uses the SAME criterion activeReceiver does — allowed implies a known active receiver', () => {
    const states: ServerState[] = [
      bareState(),
      bareState({ active: 'SUB', fieldStatus: { ...bareState().fieldStatus, 'sub.freqHz': fresh } }),
      ...UNCONFIRMED.map(([, makeState]) => makeState()),
    ];
    const seenPermits = new Set<string>();
    for (const state of states) {
      // hfCaps is single-receiver (MOR-1421 tautology: always 'known') and
      // dualCaps is the genuinely-ambiguous multi-receiver case — both sides
      // of the implication must still hold on each.
      for (const capabilities of [hfCaps, dualCaps]) {
        const view = model(state, capabilities);
        seenPermits.add(view.band!.currentBandTx);
        if (view.band!.currentBandTx === 'allowed') {
          expect(view.activeReceiver.status).toBe('known');
        }
      }
    }
    // Both branches actually occurred — the implication above is not vacuous.
    expect(seenPermits).toEqual(new Set(['allowed', 'denied']));
  });

  it('leaves every NON-permit band fact on the ordinary-fact convention (no scope creep)', () => {
    const status = { ...bareState().fieldStatus };
    delete (status as Record<string, unknown>).active;
    const gated = model(bareState({ fieldStatus: status }), dualCaps).band!;
    const confirmed = model(bareState(), dualCaps).band!;
    expect(gated.currentBand).toEqual(confirmed.currentBand);
    expect(gated.currentBand.reading).toEqual({ status: 'known', value: '20m' });
    expect(gated.bandChoices).toEqual(confirmed.bandChoices);
    expect(gated.tuneMinHz).toBe(confirmed.tuneMinHz);
    expect(gated.tuneMaxHz).toBe(confirmed.tuneMaxHz);
    // Only the permit differs.
    expect(confirmed.currentBandTx).toBe('allowed');
    expect(gated.currentBandTx).toBe('denied');
  });
});

describe('band current-band derivation (MOR-1294)', () => {
  it('is the shipped findActiveBand lookup over the caps argument, verbatim', () => {
    const view = model(bareState(), hfCaps);
    expect(view.band!.currentBand.reading).toEqual({
      status: 'known', value: findActiveBand(14195000, HF_RANGES),
    });
  });

  it('follows the SUB receiver once it is the active one', () => {
    const view = model(bareState({
      active: 'SUB',
      fieldStatus: { ...bareState().fieldStatus, 'sub.freqHz': fresh },
    }), caps({ freqRanges: HF_RANGES, txBands: HAM_TX_BANDS, receivers: 2, vfoScheme: 'ab_shared', capabilities: ['tx', 'dual_rx'] }));
    expect(view.band!.currentBand.reading).toEqual({ status: 'known', value: '40m' });
  });
});

describe('band tuning envelope (MOR-1294)', () => {
  it('reports the envelope of the declared ranges, never adjustFreqByDigit\'s 0..999 MHz stand-in', () => {
    const view = model(bareState(), caps({
      freqRanges: [
        { start: 1800000, end: 30000000, label: 'HF' },
        { start: 30000, end: 1700000, label: 'LF/MW' },
        { start: 50000000, end: 54000000, label: '6m' },
      ],
    }));
    expect(view.band!.tuneMinHz).toBe(30000);
    expect(view.band!.tuneMaxHz).toBe(54000000);
  });
});

/**
 * DETERMINISM (the 4A/5A binding lesson): every band fact is a pure function
 * of `(state, caps)`. Both band-plan lookups take `freqRanges` as an EXPLICIT
 * parameter — unlike `pbtRawToHz`/`nrRawToDisplay`, neither has a
 * capabilities-STORE fallback to fall through to — so there is no store path
 * to close here. Pinned two ways: identical arguments produce deep-equal
 * output, and the band-plan module's own source imports no store.
 */
describe('band determinism in (state, caps) (MOR-1294)', () => {
  it('produces deep-equal output for identical arguments', () => {
    const state = bareState();
    expect(model(state, hfCaps).band).toEqual(model(state, hfCaps).band);
  });

  it('tracks the caps ARGUMENT, not a module-global — a different caps yields different facts', () => {
    const state = bareState();
    const denied = model(state, caps({ freqRanges: HF_RANGES, txBands: [] }));
    expect(model(state, hfCaps).band!.currentBandTx).toBe('allowed');
    expect(denied.band!.currentBandTx).toBe('denied');
  });

  it('the shipped band-plan module reads no store', () => {
    const source = readFileSync(resolve(process.cwd(), 'src/lib/radio/band-plan.ts'), 'utf8');
    // Import statements only — the module's doc comment names the v2 callers'
    // own `getCapabilities()` store read, which is exactly what stays on the
    // far side of this seam.
    const imports = source.match(/^\s*import\s[\s\S]*?;$/gm) ?? [];
    expect(imports.length).toBeGreaterThan(0);
    expect(imports.join('\n')).not.toMatch(/stores|getCapabilities/);
  });
});

describe('band round-trip (MOR-1294)', () => {
  it('emits a validator-clean model carrying the band group', () => {
    const view = model(bareState(), hfCaps);
    expect(JSON.parse(JSON.stringify(view))).toEqual(view);
    expect(view.band).toBeDefined();
  });
});
