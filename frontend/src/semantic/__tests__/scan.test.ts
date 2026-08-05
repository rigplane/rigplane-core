/**
 * MOR-1262 decomposition slice 8A (MOR-1295) — `scan` optional fact group
 * (validator half).
 *
 * Facts only: scanning, scan type, and the masked resume mode. No
 * `scan` capability tag exists anywhere in v2 — see `radio-view-model.ts`'s
 * `ScanViewModel` doc comment, and `radio-view-model-adapter.ts`'s
 * `deriveScan` for the per-field "was this ever reported" evidence gate
 * (companion `scan-adapter.test.ts`).
 *
 * Mirrors the companion families' kill-tests:
 *  1. a scan-absent model validates and round-trips byte-identically
 *  2. `"scan": null` / `{}` throw (absent ≠ malformed)
 *  3. a malformed inner field throws with a precise `$.scan....` path
 *  5. an unknown extra key inside scan (or a field) throws
 * (Kill-test 4, the adapter's evidence gate, lives in the adapter test file.)
 */
import { describe, expect, it } from 'vitest';
import { validateRadioViewModel, type ScanField, type ScanViewModel } from '../radio-view-model';
import { topologyFixtures, withScan } from '../fixtures/topologies';

const AVAIL = { structural: true, operational: true } as const;
const base = topologyFixtures['1/single'];

describe('scan (MOR-1262 slice 8A)', () => {
  it('validates a scan-absent model and never adds the key', () => {
    expect(Object.keys(base)).not.toContain('scan');
    const validated = validateRadioViewModel(base);
    expect(Object.keys(validated)).not.toContain('scan');
    expect(validated.scan).toBeUndefined();
    expect(JSON.parse(JSON.stringify(validated))).toEqual(JSON.parse(JSON.stringify(base)));
  });

  it('validates a fully-populated scan group and returns it unchanged', () => {
    const withS = withScan(base);
    expect(validateRadioViewModel(withS).scan).toEqual(withS.scan);
  });

  it('rejects an explicit scan: null', () => {
    expect(() => validateRadioViewModel({ ...base, scan: null })).toThrow(TypeError);
  });

  it('rejects an explicit scan: {} — present, not absent, must satisfy its own shape', () => {
    expect(() => validateRadioViewModel({ ...base, scan: {} })).toThrow(TypeError);
  });

  it('rejects a non-boolean scanning reading value with a precise error path', () => {
    const withS = withScan(base);
    const malformed = {
      ...withS,
      scan: { ...withS.scan, scanning: { reading: { status: 'known', value: 1 }, availability: AVAIL } },
    };
    expect(() => validateRadioViewModel(malformed)).toThrow(/\$\.scan\.scanning\.reading\.value/);
  });

  it('rejects a non-numeric scanType reading value with a precise error path', () => {
    const withS = withScan(base);
    const malformed = {
      ...withS,
      scan: { ...withS.scan, scanType: { reading: { status: 'known', value: 'PROG' }, availability: AVAIL } },
    };
    expect(() => validateRadioViewModel(malformed)).toThrow(/\$\.scan\.scanType\.reading\.value/);
  });

  it('accepts an unknown scanResumeMode reading independent of a known scanning reading', () => {
    const withS = withScan(base);
    const partial = {
      ...withS,
      scan: { ...withS.scan, scanResumeMode: { reading: { status: 'unknown' }, availability: { structural: false, operational: false } } },
    };
    const validated = validateRadioViewModel(partial);
    expect(validated.scan?.scanResumeMode).toEqual({
      reading: { status: 'unknown' }, availability: { structural: false, operational: false },
    });
    expect(validated.scan?.scanning.reading).toEqual({ status: 'known', value: false });
  });

  it('rejects an unknown extra key inside scan', () => {
    const withS = withScan(base);
    expect(() => validateRadioViewModel({ ...base, scan: { ...withS.scan, bogus: 1 } })).toThrow(TypeError);
  });

  it('rejects an unknown extra key inside a single scan field', () => {
    const withS = withScan(base);
    const malformed = {
      ...withS,
      scan: { ...withS.scan, scanType: { reading: { status: 'known', value: 1 }, availability: AVAIL, extra: true } },
    };
    expect(() => validateRadioViewModel(malformed)).toThrow(TypeError);
  });

  it('rejects a reading that carries a value key while unknown', () => {
    const withS = withScan(base);
    const malformed = {
      ...withS,
      scan: { ...withS.scan, scanning: { reading: { status: 'unknown', value: true }, availability: AVAIL } },
    };
    expect(() => validateRadioViewModel(malformed)).toThrow(TypeError);
  });

  it('rejects a scan group missing a key (no partial 8A shape survives)', () => {
    const withS = withScan(base);
    const { scanResumeMode: _scanResumeMode, ...withoutResume } = withS.scan as ScanViewModel;
    expect(() => validateRadioViewModel({ ...withS, scan: withoutResume })).toThrow(TypeError);
  });

  it('every field of a fully-populated group round-trips its own value', () => {
    const validated = validateRadioViewModel(withScan(base)).scan as ScanViewModel;
    const knownValue = <T>(f: ScanField<T>): T | undefined =>
      f.reading.status === 'known' ? f.reading.value : undefined;
    expect(knownValue(validated.scanning)).toBe(false);
    expect(knownValue(validated.scanType)).toBe(0x01);
    expect(knownValue(validated.scanResumeMode)).toBe(0);
  });
});
