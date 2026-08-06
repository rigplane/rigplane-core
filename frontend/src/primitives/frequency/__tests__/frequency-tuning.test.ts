import { describe, it, expect } from 'vitest';
import { splitFrequencyToDigits, groupDigitsForDisplay, adjustFreqByDigit } from '../frequency-tuning';

describe('splitFrequencyToDigits', () => {
  it('splits 14.235.000 into 9 digits with correct multipliers', () => {
    const digits = splitFrequencyToDigits(14_235_000);
    expect(digits).toHaveLength(8); // Leading zeros stripped
    expect(digits[0]).toEqual({ char: '1', multiplier: 10_000_000, digitIndex: 1 });
    expect(digits[1]).toEqual({ char: '4', multiplier: 1_000_000, digitIndex: 2 });
    expect(digits[2]).toEqual({ char: '2', multiplier: 100_000, digitIndex: 3 });
    expect(digits[3]).toEqual({ char: '3', multiplier: 10_000, digitIndex: 4 });
    expect(digits[4]).toEqual({ char: '5', multiplier: 1_000, digitIndex: 5 });
    expect(digits[5]).toEqual({ char: '0', multiplier: 100, digitIndex: 6 });
    expect(digits[6]).toEqual({ char: '0', multiplier: 10, digitIndex: 7 });
    expect(digits[7]).toEqual({ char: '0', multiplier: 1, digitIndex: 8 });
  });

  it('handles low frequencies with leading zeros stripped', () => {
    const digits = splitFrequencyToDigits(7_074_000);
    expect(digits).toHaveLength(7);
    expect(digits[0].char).toBe('7');
    expect(digits[0].multiplier).toBe(1_000_000);
  });

  it('handles sub-MHz frequencies', () => {
    const digits = splitFrequencyToDigits(475_000);
    expect(digits).toHaveLength(6);
    expect(digits[0].char).toBe('4');
    expect(digits[0].multiplier).toBe(100_000);
  });
});

describe('groupDigitsForDisplay', () => {
  it('groups 14.235.000 into MHz/kHz/Hz', () => {
    const digits = splitFrequencyToDigits(14_235_000);
    const groups = groupDigitsForDisplay(digits);
    expect(groups.mhz).toHaveLength(2); // "14"
    expect(groups.khz).toHaveLength(3); // "235"
    expect(groups.hz).toHaveLength(3);  // "000"
    expect(groups.mhz.map(d => d.char).join('')).toBe('14');
    expect(groups.khz.map(d => d.char).join('')).toBe('235');
    expect(groups.hz.map(d => d.char).join('')).toBe('000');
  });

  it('groups sub-MHz frequency correctly', () => {
    const digits = splitFrequencyToDigits(475_000);
    const groups = groupDigitsForDisplay(digits);
    expect(groups.mhz).toHaveLength(0); // No MHz digits
    expect(groups.khz).toHaveLength(3); // "475"
    expect(groups.hz).toHaveLength(3);  // "000"
  });
});

describe('adjustFreqByDigit', () => {
  it('increments 10 kHz digit', () => {
    const newFreq = adjustFreqByDigit(14_235_000, 10_000, 1);
    expect(newFreq).toBe(14_245_000);
  });

  it('decrements 10 kHz digit', () => {
    const newFreq = adjustFreqByDigit(14_235_000, 10_000, -1);
    expect(newFreq).toBe(14_225_000);
  });

  it('carries over from 999 to 000 with kHz increment', () => {
    const newFreq = adjustFreqByDigit(14_999_000, 1_000, 1);
    expect(newFreq).toBe(15_000_000);
  });

  it('borrows from MHz when kHz underflows', () => {
    const newFreq = adjustFreqByDigit(14_000_000, 1_000, -1);
    expect(newFreq).toBe(13_999_000);
  });

  it('clamps to min frequency', () => {
    const newFreq = adjustFreqByDigit(100, 1_000, -1, 0, 999_000_000);
    expect(newFreq).toBe(0);
  });

  it('clamps to max frequency', () => {
    const newFreq = adjustFreqByDigit(998_999_000, 1_000_000, 1, 0, 999_000_000);
    expect(newFreq).toBe(999_000_000);
  });

  it('handles 1 Hz increments', () => {
    const newFreq = adjustFreqByDigit(14_235_123, 1, 1);
    expect(newFreq).toBe(14_235_124);
  });

  it('handles 10 MHz increments', () => {
    const newFreq = adjustFreqByDigit(14_235_000, 10_000_000, 1);
    expect(newFreq).toBe(24_235_000);
  });
});
