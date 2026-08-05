/**
 * MOR-1262 decomposition slice 7A (MOR-1294) — `band` optional fact group
 * (validator half). SAFETY-ADJACENT: the group carries a TX permit per band.
 *
 * Facts only: current band, the capability-derived band choice set (each
 * entry carrying the EXISTING `FrequencyPermit` tri-state), the fail-closed
 * `currentBandTx` collapse, and the tuning envelope. A SEPARATE group from
 * `rfFrontEnd` (MOR-1293, slice 6A′) — see `radio-view-model.ts`'s
 * `BandViewModel` doc comment for the group-shape rationale, and
 * `radio-view-model-adapter.ts`'s `deriveBand` for the live derivation
 * (covered by the companion `band-adapter.test.ts`).
 *
 * Mirrors the companion families' (`rf-front-end.test.ts`, `dsp.test.ts`)
 * kill-tests for this family:
 *  1. a band-absent model validates and round-trips byte-identically
 *  2. `"band": null` / `{}` throw (absent ≠ malformed)
 *  3. a malformed inner field throws with a precise `$.band....` path
 *  5. an unknown extra key inside band (or a choice entry) throws
 * (Kill-test 4, the adapter's evidence gate, lives in the adapter test file.)
 * Plus the fail-closed cross-field invariant this family adds: `currentBandTx`
 * may never read `'allowed'` while `currentBand` is unknown.
 */
import { describe, expect, it } from 'vitest';
import { validateRadioViewModel, type BandViewModel } from '../radio-view-model';
import { topologyFixtures, withBand } from '../fixtures/topologies';

const AVAIL = { structural: true, operational: true } as const;
const base = topologyFixtures['1/single'];

describe('band (MOR-1262 slice 7A)', () => {
  // ── Kill-test 1: absence is byte-identical, not a new default shape ──────
  it('validates a band-absent model and never adds the key', () => {
    expect(Object.keys(base)).not.toContain('band');
    const validated = validateRadioViewModel(base);
    expect(Object.keys(validated)).not.toContain('band');
    expect(validated.band).toBeUndefined();
    expect(JSON.parse(JSON.stringify(validated))).toEqual(JSON.parse(JSON.stringify(base)));
  });

  it('validates a fully-populated band group and returns it unchanged', () => {
    const withB = withBand(base);
    expect(validateRadioViewModel(withB).band).toEqual(withB.band);
  });

  // ── Kill-test 2: null / {} are not absent ────────────────────────────────
  it('rejects an explicit band: null', () => {
    expect(() => validateRadioViewModel({ ...base, band: null })).toThrow(TypeError);
  });

  it('rejects an explicit band: {} — present, not absent, must satisfy its own shape', () => {
    expect(() => validateRadioViewModel({ ...base, band: {} })).toThrow(TypeError);
  });

  // ── Kill-test 3: malformed inner fields, precise paths ───────────────────
  it('rejects a non-string currentBand reading value with a precise error path', () => {
    const withB = withBand(base);
    const malformed = {
      ...withB,
      band: {
        ...withB.band,
        currentBand: { reading: { status: 'known', value: 20 }, availability: AVAIL },
      },
    };
    expect(() => validateRadioViewModel(malformed)).toThrow(/\$\.band\.currentBand\.reading\.value/);
  });

  it('rejects a non-array bandChoices', () => {
    const withB = withBand(base);
    const malformed = { ...withB, band: { ...withB.band, bandChoices: '20m' } };
    expect(() => validateRadioViewModel(malformed)).toThrow(/\$\.band\.bandChoices/);
  });

  it('rejects a non-numeric band-choice boundary with a precise, indexed error path', () => {
    const withB = withBand(base);
    const choices = [...(withB.band as BandViewModel).bandChoices];
    choices[1] = { ...choices[1], endHz: '14.35 MHz' as unknown as number };
    const malformed = { ...withB, band: { ...withB.band, bandChoices: choices } };
    expect(() => validateRadioViewModel(malformed)).toThrow(/\$\.band\.bandChoices\[1\]\.endHz/);
  });

  it('rejects a non-numeric tuneMinHz while accepting an explicit null (no declared range)', () => {
    const withB = withBand(base);
    expect(() => validateRadioViewModel({ ...withB, band: { ...withB.band, tuneMinHz: '30 kHz' } }))
      .toThrow(/\$\.band\.tuneMinHz/);
    const noRange = { ...withB, band: { ...withB.band, tuneMinHz: null, tuneMaxHz: null } };
    expect(validateRadioViewModel(noRange).band?.tuneMinHz).toBeNull();
  });

  it('accepts a null bsrCode but rejects a non-numeric one — absence is null, never a fabricated 0', () => {
    const withB = withBand(base);
    expect(validateRadioViewModel(withB).band?.bandChoices[2].bsrCode).toBeNull();
    const choices = [...(withB.band as BandViewModel).bandChoices];
    choices[0] = { ...choices[0], bsrCode: '2' as unknown as number };
    expect(() => validateRadioViewModel({ ...withB, band: { ...withB.band, bandChoices: choices } }))
      .toThrow(/\$\.band\.bandChoices\[0\]\.bsrCode/);
  });

  // ── The per-band permit IS the existing tri-state (MOR-1294) ─────────────
  it('validates every branch of the per-band permit tri-state, including its reasons', () => {
    const withB = withBand(base);
    const choices = [
      { ...(withB.band as BandViewModel).bandChoices[0], defaultHzTxPermit: { status: 'allowed', band: '40m' } },
      {
        ...(withB.band as BandViewModel).bandChoices[1],
        defaultHzTxPermit: { status: 'unknown', reason: 'ranges-unconfigured' },
      },
      {
        ...(withB.band as BandViewModel).bandChoices[2],
        defaultHzTxPermit: { status: 'denied', reason: 'outside-configured-ranges' },
      },
    ];
    const model = validateRadioViewModel({
      ...withB,
      band: { ...withB.band, currentBand: { reading: { status: 'unknown' }, availability: AVAIL }, currentBandTx: 'denied', bandChoices: choices },
    });
    expect(model.band?.bandChoices.map((c) => c.defaultHzTxPermit.status)).toEqual(['allowed', 'unknown', 'denied']);
  });

  it('rejects a bespoke per-band permit shape — the band permit is the shared txPermit tri-state, not a boolean', () => {
    const withB = withBand(base);
    const choices = [...(withB.band as BandViewModel).bandChoices];
    choices[0] = { ...choices[0], defaultHzTxPermit: true as unknown as BandViewModel['bandChoices'][0]['defaultHzTxPermit'] };
    expect(() => validateRadioViewModel({ ...withB, band: { ...withB.band, bandChoices: choices } }))
      .toThrow(TypeError);
  });

  it('rejects an unrecognized per-band permit reason (the tri-state reasons are not a wildcard)', () => {
    const withB = withBand(base);
    const choices = [...(withB.band as BandViewModel).bandChoices];
    choices[2] = {
      ...choices[2],
      defaultHzTxPermit: { status: 'denied', reason: 'not-a-ham-band' } as unknown as BandViewModel['bandChoices'][0]['defaultHzTxPermit'],
    };
    expect(() => validateRadioViewModel({ ...withB, band: { ...withB.band, bandChoices: choices } }))
      .toThrow(TypeError);
  });

  // ── FAIL-CLOSED cross-field invariant (MOR-1294 safety constraint 2) ─────
  it('rejects currentBandTx: allowed while currentBand is unknown — an unknown band must never permit TX', () => {
    const withB = withBand(base);
    const failOpen = {
      ...withB,
      band: {
        ...withB.band,
        currentBand: { reading: { status: 'unknown' }, availability: AVAIL },
        currentBandTx: 'allowed',
      },
    };
    expect(() => validateRadioViewModel(failOpen)).toThrow(/\$\.band\.currentBandTx/);
  });

  it('accepts currentBandTx: denied while currentBand is unknown — the fail-closed pairing', () => {
    const withB = withBand(base, 'denied');
    const validated = validateRadioViewModel(withB);
    expect(validated.band?.currentBand.reading).toEqual({ status: 'unknown' });
    expect(validated.band?.currentBandTx).toBe('denied');
  });

  it('rejects a currentBandTx outside the shipped binary permit vocabulary', () => {
    const withB = withBand(base);
    expect(() => validateRadioViewModel({ ...withB, band: { ...withB.band, currentBandTx: 'unknown' } }))
      .toThrow(/\$\.band\.currentBandTx/);
  });

  // ── Kill-test 5: no speculative keys (N4) ────────────────────────────────
  it('rejects an unknown extra key inside band', () => {
    const withB = withBand(base);
    expect(() => validateRadioViewModel({ ...withB, band: { ...withB.band, bogus: 1 } })).toThrow(TypeError);
  });

  // The verify-F1 rename is enforced, not merely documented: `exactKeys`
  // rejects the old point-sample-agnostic `txPermit` key outright, so a 7B
  // consumer cannot reach for a name that reads like a live permit.
  it('rejects the pre-rename txPermit key on a band choice (the point-sample name is mandatory)', () => {
    const withB = withBand(base);
    const choices = (withB.band as BandViewModel).bandChoices.map(
      ({ defaultHzTxPermit, ...rest }) => ({ ...rest, txPermit: defaultHzTxPermit }),
    );
    expect(() => validateRadioViewModel({ ...withB, band: { ...withB.band, bandChoices: choices } }))
      .toThrow(TypeError);
  });

  it('rejects an unknown extra key inside a band choice', () => {
    const withB = withBand(base);
    const choices = [...(withB.band as BandViewModel).bandChoices];
    choices[0] = { ...choices[0], label: 'Forty' } as unknown as BandViewModel['bandChoices'][0];
    expect(() => validateRadioViewModel({ ...withB, band: { ...withB.band, bandChoices: choices } }))
      .toThrow(TypeError);
  });

  it('rejects a band group missing a key (no partial 7A shape survives)', () => {
    const withB = withBand(base);
    const { tuneMaxHz: _tuneMaxHz, ...withoutMax } = withB.band as BandViewModel;
    expect(() => validateRadioViewModel({ ...withB, band: withoutMax })).toThrow(TypeError);
  });

  it('rejects a currentBand reading that carries a value key while unknown', () => {
    const withB = withBand(base);
    const malformed = {
      ...withB,
      band: {
        ...withB.band,
        currentBand: { reading: { status: 'unknown', value: '20m' }, availability: AVAIL },
        currentBandTx: 'denied',
      },
    };
    expect(() => validateRadioViewModel(malformed)).toThrow(TypeError);
  });

  it('every field of a fully-populated group round-trips its own value', () => {
    const validated = validateRadioViewModel(withBand(base)).band as BandViewModel;
    expect(validated.currentBand.reading).toEqual({ status: 'known', value: '20m' });
    expect(validated.bandChoices.map((c) => c.name)).toEqual(['40m', '20m', 'MW']);
    expect(validated.bandChoices[1]).toEqual({
      name: '20m', startHz: 14000000, endHz: 14350000, defaultHz: 14195000, bsrCode: 5,
      defaultHzTxPermit: { status: 'allowed', band: '20m' },
    });
    expect(validated.currentBandTx).toBe('allowed');
    expect(validated.tuneMinHz).toBe(30000);
    expect(validated.tuneMaxHz).toBe(60000000);
  });
});
