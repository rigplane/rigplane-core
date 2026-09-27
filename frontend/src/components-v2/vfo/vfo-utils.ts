/**
 * Formats a RIT/XIT offset (given in Hz) as a signed whole-Hz string,
 * e.g. '+120'. The value stays in Hz; the display is Hz. (MOR-480, MOR-2509)
 */
export function formatRitOffset(offsetHz: number): string {
  const sign = offsetHz >= 0 ? '+' : '−';
  return `${sign}${Math.abs(Math.round(offsetHz))}`;
}
