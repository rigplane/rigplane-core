/**
 * Utility functions for RIT/XIT panel.
 */

/**
 * Formats a frequency offset in Hz for display.
 * Positive: '+120 Hz', negative: '−50 Hz' (Unicode minus), zero: '±0 Hz'.
 */
export function formatOffset(hz: number): string {
  if (hz === 0) return '±0 Hz';
  if (hz > 0) return `+${hz} Hz`;
  return `\u2212${Math.abs(hz)} Hz`;
}

/**
 * Formats a frequency offset (given in Hz) as kHz for display.
 * Positive: '+5.00 kHz', negative: '−5.00 kHz' (Unicode minus), zero: '±0 kHz'.
 *
 * The underlying value stays in Hz everywhere (slider bounds, set/read
 * round-trip); only the displayed text is converted to kHz with 2 decimals.
 * Mirrors the sign convention of {@link formatOffset}. (MOR-480)
 */
export function formatOffsetKHz(hz: number): string {
  if (hz === 0) return '±0 kHz';
  const khz = (Math.abs(hz) / 1000).toFixed(2);
  if (hz > 0) return `+${khz} kHz`;
  return `−${khz} kHz`;
}

/**
 * Returns true when the RIT/XIT panel should be shown.
 */
export function shouldShowPanel(hasRit: boolean, hasXit: boolean): boolean {
  return hasRit || hasXit;
}
