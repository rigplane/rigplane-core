/**
 * Digit-based frequency tuning utilities.
 * Maps digit positions to Hz multipliers for scroll-on-digit interaction.
 */

export interface DigitInfo {
  char: string;
  multiplier: number;
  digitIndex: number;
}

/**
 * Split a frequency into individual interactive digits with multipliers.
 * Returns array in display order (left to right): MHz . kHz . Hz
 *
 * Example: 14235000 Hz → [
 *   {char: '1', multiplier: 10000000, digitIndex: 0},  // 10 MHz
 *   {char: '4', multiplier: 1000000,  digitIndex: 1},  // 1 MHz
 *   {char: '2', multiplier: 100000,   digitIndex: 2},  // 100 kHz
 *   {char: '3', multiplier: 10000,    digitIndex: 3},  // 10 kHz
 *   {char: '5', multiplier: 1000,     digitIndex: 4},  // 1 kHz
 *   {char: '0', multiplier: 100,      digitIndex: 5},  // 100 Hz
 *   {char: '0', multiplier: 10,       digitIndex: 6},  // 10 Hz
 *   {char: '0', multiplier: 1,        digitIndex: 7},  // 1 Hz
 * ]
 */
export function splitFrequencyToDigits(freq: number): DigitInfo[] {
  const absHz = Math.max(0, Math.floor(freq));
  const str = String(absHz).padStart(9, '0'); // Pad to 9 digits (max ~999 MHz)
  const digits: DigitInfo[] = [];

  // Parse from left to right
  for (let i = 0; i < 9; i++) {
    const char = str[i];
    const multiplier = Math.pow(10, 8 - i); // 10^8, 10^7, ..., 10^0
    digits.push({ char, multiplier, digitIndex: i });
  }

  // Remove leading zeros from MHz group (but keep at least 1 digit)
  while (digits.length > 1 && digits[0].char === '0' && digits[0].multiplier >= 1_000_000) {
    digits.shift();
  }

  return digits;
}

/**
 * Increment/decrement frequency at a specific digit position.
 *
 * @param currentFreq - Current frequency in Hz
 * @param multiplier - Digit multiplier (10^n)
 * @param direction - 1 for increment, -1 for decrement
 * @param minFreq - Minimum allowed frequency (default 0)
 * @param maxFreq - Maximum allowed frequency (default 999 MHz)
 * @returns New frequency, clamped to [minFreq, maxFreq]
 */
export function adjustFreqByDigit(
  currentFreq: number,
  multiplier: number,
  direction: 1 | -1,
  minFreq = 0,
  maxFreq = 999_000_000
): number {
  const delta = multiplier * direction;
  const newFreq = currentFreq + delta;
  return Math.max(minFreq, Math.min(maxFreq, Math.floor(newFreq)));
}

/**
 * Get display groups for rendering with separators.
 * Returns: { mhz: DigitInfo[], khz: DigitInfo[], hz: DigitInfo[] }
 */
export function groupDigitsForDisplay(digits: DigitInfo[]): {
  mhz: DigitInfo[];
  khz: DigitInfo[];
  hz: DigitInfo[];
} {
  // Find kHz and Hz boundaries (separators after 10^6 and 10^3)
  const mhzEnd = digits.findIndex(d => d.multiplier < 1_000_000);
  const khzEnd = digits.findIndex(d => d.multiplier < 1_000);

  return {
    mhz: digits.slice(0, mhzEnd === -1 ? digits.length : mhzEnd),
    khz: digits.slice(
      mhzEnd === -1 ? digits.length : mhzEnd,
      khzEnd === -1 ? digits.length : khzEnd
    ),
    hz: khzEnd === -1 ? [] : digits.slice(khzEnd),
  };
}
