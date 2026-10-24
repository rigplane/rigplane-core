import type { MemoryEntryDto } from '../lib/types/memory-list';

/** Short repeater text for one stored channel: shift and offset, e.g. `+0.600`. */
export function formatShift(entry: MemoryEntryDto): string {
  if (entry.duplex !== 'plus' && entry.duplex !== 'minus') return '';
  const sign = entry.duplex === 'plus' ? '+' : '-';
  if (entry.offsetHz === undefined) return sign;
  // The radio keeps 100 Hz: four decimals, trailing zero dropped, so 600 kHz reads `0.600`.
  const mhz = (entry.offsetHz / 1_000_000).toFixed(4);
  return `${sign}${mhz.endsWith('0') ? mhz.slice(0, -1) : mhz}`;
}

/** Tone text for one stored channel, e.g. `T 136.5`, `TSQL 88.5`, `DCS 023`; empty when off. */
export function formatTone(entry: MemoryEntryDto): string {
  const hz = (centi: number | undefined) => (centi === undefined ? '' : (centi / 100).toFixed(1));
  switch (entry.toneMode) {
    case 'tone': return `T ${hz(entry.toneCentiHz)}`.trim();
    case 'tsql': return `TSQL ${hz(entry.tsqlCentiHz)}`.trim();
    case 'dtcs': return entry.dtcsCode === undefined ? 'DCS' : `DCS ${String(entry.dtcsCode).padStart(3, '0')}`;
    default: return '';
  }
}
