/**
 * Band-plan lookups (MOR-1262 decomposition slice 7A, MOR-1294).
 *
 * The bodies below are `components-v2/controls/band-utils.ts` MOVED here
 * verbatim, not reimplemented: `lib/runtime/**` may not import from
 * `components-v2/**` (eslint zone, ADR circular-dependency rule), and the
 * `band` fact group must consume the ONE shipped band-plan derivation rather
 * than fork a second copy of it — the same "consume, never re-derive"
 * discipline `filterPassband` follows for `pbtRawToHz` and `dsp` for
 * `nrRawToDisplay`. `band-utils.ts` is now a re-export shim, so every v2 call
 * site (`BandSelector.svelte`, `VfoPanel.svelte`, their tests) is untouched.
 *
 * Both functions take `freqRanges` as an EXPLICIT parameter and read no
 * store — a fact-layer value must be a pure function of `(state, caps)`, and
 * the v2 callers' own `getCapabilities()?.freqRanges ?? []` store read stays
 * on the v2 side of the seam.
 */
import type { FreqRange } from '$lib/types/capabilities';

export interface FlatBand {
  name: string;
  defaultFreq: number;
  start: number;
  end: number;
  bsrCode?: number;
}

/**
 * Flatten all bands from all freq ranges into a single ordered array.
 * Ranges and bands within each range appear in the order defined in the config.
 */
export function flattenBands(freqRanges: FreqRange[]): FlatBand[] {
  const result: FlatBand[] = [];
  for (const range of freqRanges) {
    for (const band of range.bands ?? []) {
      result.push({
        name: band.name,
        defaultFreq: band.default,
        start: band.start,
        end: band.end,
        bsrCode: band.bsrCode,
      });
    }
  }
  return result;
}

/**
 * Find the band name whose [start, end] range contains `freq`.
 * Returns null if no band matches.
 */
export function findActiveBand(freq: number, freqRanges: FreqRange[]): string | null {
  for (const range of freqRanges) {
    for (const band of range.bands ?? []) {
      if (freq >= band.start && freq <= band.end) {
        return band.name;
      }
    }
  }
  return null;
}

/**
 * True when `freq` falls inside a range flagged `repeater = true`.
 * `findActiveBand` matches only NAMED sub-bands (`range.bands`), so a
 * repeater band described as a bare range (the FTX-1 2 m / 70 cm carries
 * no `bands` table) needs this range-level match instead (MOR-2111). The
 * membership comes from the profile's own `freqRanges` data, never from a
 * frequency written in code.
 */
export function receiverInRepeaterBand(freq: number, freqRanges: FreqRange[]): boolean {
  for (const range of freqRanges) {
    if (range.repeater && freq >= range.start && freq <= range.end) {
      return true;
    }
  }
  return false;
}
