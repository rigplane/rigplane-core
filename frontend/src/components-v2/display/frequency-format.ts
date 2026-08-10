export interface FrequencyParts {
  /** MHz group — no leading zeros (e.g. "14", "7", "0") */
  mhz: string;
  /** kHz group — always 3 digits, zero-padded (e.g. "235", "074", "000") */
  khz: string;
  /** Hz group  — always 3 digits, zero-padded (e.g. "000", "001", "999") */
  hz: string;
}

/**
 * Splits a frequency in Hz into display groups for XX.XXX.XXX formatting.
 *
 * @param freq - Frequency in Hz. Negative values are clamped to 0. Floats are floored.
 */
export function formatFrequency(freq: number): FrequencyParts {
  // MOR-1409 A11 (coordinator adjudication 5245817033, Core #2317):
  // `toVfoProps`/`toBandSelectorProps` (lib/runtime/props/panel-props.ts)
  // deliberately return `NaN` for an unobserved frequency (no fabricated
  // 14.074 MHz stand-in). `FrequencyDisplay.svelte` — this function's sole
  // consumer, rendered unguarded on the shipped mobile skin at cold start
  // (`MobileRadioLayout.svelte`) — would otherwise show the literal string
  // "NaN.NaN.NaN". Guard here, the single choke point, rather than in each
  // consumer. Placeholder segment widths match the sibling LCD-skin
  // formatter's already-shipped convention for the same unknown-frequency
  // case (`panels/lcd/AmberFrequency.svelte`'s `hz <= 0` branch): '--' for
  // the (normally 1-2 digit) MHz group, '---' for the always-3-digit
  // zero-padded kHz/Hz groups — never the literal "NaN" substring.
  if (!Number.isFinite(freq)) {
    return { mhz: '--', khz: '---', hz: '---' };
  }
  const absHz = Math.max(0, Math.floor(freq));
  const mhzPart = Math.floor(absHz / 1_000_000);
  const khzPart = Math.floor((absHz % 1_000_000) / 1_000);
  const hzPart  = absHz % 1_000;
  return {
    mhz: String(mhzPart),
    khz: String(khzPart).padStart(3, '0'),
    hz:  String(hzPart).padStart(3, '0'),
  };
}

/**
 * Returns the full dot-separated frequency string, e.g. "14.235.000".
 */
export function formatFrequencyString(freq: number): string {
  const { mhz, khz, hz } = formatFrequency(freq);
  return `${mhz}.${khz}.${hz}`;
}
