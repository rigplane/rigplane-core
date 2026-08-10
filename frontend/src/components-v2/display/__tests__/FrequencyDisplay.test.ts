import { describe, it, expect } from 'vitest';
import { formatFrequency, formatFrequencyString } from '../frequency-format';

// ── formatFrequency — group splitting ──────────────────────────────────────

describe('formatFrequency', () => {
  it('returns "0", "000", "000" for 0 Hz', () => {
    expect(formatFrequency(0)).toEqual({ mhz: '0', khz: '000', hz: '000' });
  });

  it('returns "0", "000", "001" for 1 Hz', () => {
    expect(formatFrequency(1)).toEqual({ mhz: '0', khz: '000', hz: '001' });
  });

  it('returns "0", "000", "999" for 999 Hz', () => {
    expect(formatFrequency(999)).toEqual({ mhz: '0', khz: '000', hz: '999' });
  });

  it('returns "0", "001", "000" for 1000 Hz (1 kHz)', () => {
    expect(formatFrequency(1000)).toEqual({ mhz: '0', khz: '001', hz: '000' });
  });

  it('returns "0", "999", "999" for 999999 Hz', () => {
    expect(formatFrequency(999999)).toEqual({ mhz: '0', khz: '999', hz: '999' });
  });

  it('returns "1", "000", "000" for 1000000 Hz (1 MHz)', () => {
    expect(formatFrequency(1_000_000)).toEqual({ mhz: '1', khz: '000', hz: '000' });
  });

  it('returns "7", "074", "000" for 7074000 Hz (40m FT8)', () => {
    expect(formatFrequency(7_074_000)).toEqual({ mhz: '7', khz: '074', hz: '000' });
  });

  it('returns "14", "235", "000" for 14235000 Hz (20m SSB)', () => {
    expect(formatFrequency(14_235_000)).toEqual({ mhz: '14', khz: '235', hz: '000' });
  });

  it('returns "14", "074", "000" for 14074000 Hz (20m FT8)', () => {
    expect(formatFrequency(14_074_000)).toEqual({ mhz: '14', khz: '074', hz: '000' });
  });

  it('returns "144", "200", "000" for 144200000 Hz (2m calling)', () => {
    expect(formatFrequency(144_200_000)).toEqual({ mhz: '144', khz: '200', hz: '000' });
  });

  it('returns "435", "000", "000" for 435000000 Hz (70cm)', () => {
    expect(formatFrequency(435_000_000)).toEqual({ mhz: '435', khz: '000', hz: '000' });
  });

  it('returns "999", "999", "999" for 999999999 Hz (max)', () => {
    expect(formatFrequency(999_999_999)).toEqual({ mhz: '999', khz: '999', hz: '999' });
  });

  it('clamps negative input to 0', () => {
    expect(formatFrequency(-1)).toEqual({ mhz: '0', khz: '000', hz: '000' });
    expect(formatFrequency(-100_000)).toEqual({ mhz: '0', khz: '000', hz: '000' });
  });

  it('floors floating-point input', () => {
    expect(formatFrequency(14_235_000.9)).toEqual({ mhz: '14', khz: '235', hz: '000' });
    expect(formatFrequency(1_000_999.999)).toEqual({ mhz: '1', khz: '000', hz: '999' });
  });

  it('never produces leading zeros in the MHz group', () => {
    const { mhz } = formatFrequency(7_100_000);
    expect(mhz).toBe('7'); // not "07"
  });

  it('always zero-pads kHz group to 3 digits', () => {
    expect(formatFrequency(7_001_000).khz).toBe('001');
    expect(formatFrequency(7_010_000).khz).toBe('010');
  });

  it('always zero-pads Hz group to 3 digits', () => {
    expect(formatFrequency(14_235_001).hz).toBe('001');
    expect(formatFrequency(14_235_010).hz).toBe('010');
  });

  it('handles 1234567 Hz (1.234.567) correctly', () => {
    expect(formatFrequency(1_234_567)).toEqual({ mhz: '1', khz: '234', hz: '567' });
  });
});

// ── formatFrequencyString — full dot-separated string ─────────────────────

describe('formatFrequencyString', () => {
  it('formats 14235000 as "14.235.000"', () => {
    expect(formatFrequencyString(14_235_000)).toBe('14.235.000');
  });

  it('formats 0 as "0.000.000"', () => {
    expect(formatFrequencyString(0)).toBe('0.000.000');
  });

  it('formats 7074000 as "7.074.000"', () => {
    expect(formatFrequencyString(7_074_000)).toBe('7.074.000');
  });

  it('formats 144200000 as "144.200.000"', () => {
    expect(formatFrequencyString(144_200_000)).toBe('144.200.000');
  });

  it('formats 999999999 as "999.999.999"', () => {
    expect(formatFrequencyString(999_999_999)).toBe('999.999.999');
  });

  it('formats 1 Hz as "0.000.001"', () => {
    expect(formatFrequencyString(1)).toBe('0.000.001');
  });
});

// ── A11 non-finite guard (MOR-1409, Core #2317) ────────────────────────────
//
// Coordinator adjudication 5245817033 granted this file as A11's fourth
// production owner: `toVfoProps`/`toBandSelectorProps`
// (lib/runtime/props/panel-props.ts) deliberately return `NaN` for an
// unobserved frequency, and `FrequencyDisplay.svelte` — this module's sole
// consumer — renders `formatFrequency`'s output unguarded on the shipped
// mobile skin (`MobileRadioLayout.svelte`, both cited call sites) at cold
// start. Without a guard, that renders the literal string "NaN.NaN.NaN".
// These are the consumer-boundary proof the adjudication requires: no "NaN"
// substring anywhere in the output for non-finite input, and the populated
// (finite) path is provably byte-for-byte unaffected.
describe('formatFrequency non-finite guard (MOR-1409 A11, adjudication 5245817033)', () => {
  it('returns placeholder segments — never a "NaN" substring — for NaN input', () => {
    const parts = formatFrequency(Number.NaN);
    expect(parts).toEqual({ mhz: '--', khz: '---', hz: '---' });
    expect(parts.mhz).not.toContain('NaN');
    expect(parts.khz).not.toContain('NaN');
    expect(parts.hz).not.toContain('NaN');
  });

  it('formatFrequencyString(NaN) joins to "--.---.---" — never "NaN.NaN.NaN"', () => {
    const s = formatFrequencyString(Number.NaN);
    expect(s).toBe('--.---.---');
    expect(s).not.toContain('NaN');
  });

  it('also guards +/-Infinity (any non-finite input, not just NaN)', () => {
    expect(formatFrequency(Number.POSITIVE_INFINITY)).toEqual({
      mhz: '--', khz: '---', hz: '---',
    });
    expect(formatFrequency(Number.NEGATIVE_INFINITY)).toEqual({
      mhz: '--', khz: '---', hz: '---',
    });
  });

  it('the populated 14.074 MHz path is unchanged by the guard (regression pin)', () => {
    // Dedicated A11 pin, alongside the pre-existing "20m FT8" test above —
    // proves the finite branch this gate must not touch stays exactly
    // "14"/"074"/"000".
    expect(formatFrequency(14_074_000)).toEqual({ mhz: '14', khz: '074', hz: '000' });
  });
});
