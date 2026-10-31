/**
 * Pure functions extracted from BandPlanOverlay.svelte.
 * No reactive ($state/$derived) dependencies — safe to import and test directly.
 */

export interface RemoteSegment {
  start: number;
  end: number;
  mode: string;
  label: string;
  color: string;
  opacity: number;
  band: string;
  layer: string;
  priority: number;
  url?: string | null;
  notes?: string | null;
  license?: string | null;
  station?: string | null;
  language?: string | null;
  schedule?: string | null;
}

/**
 * Interpret a `/api/v1/band-plan/segments` body. Returns the segments when
 * the answer is authoritative (including an authoritative EMPTY list: plans
 * are loaded and nothing falls in the window), or `null` when it is
 * unavailable (malformed body, or the install has no band-plan data) so the
 * caller uses its local copy instead.
 */
export function parseSegmentsResponse(data: unknown): RemoteSegment[] | null {
  if (typeof data !== 'object' || data === null) return null;
  const { segments, plansLoaded } = data as { segments?: unknown; plansLoaded?: unknown };
  if (!Array.isArray(segments)) return null;
  if (segments.length === 0 && plansLoaded === false) return null;
  return segments as RemoteSegment[];
}

/** Format frequency for human display: Hz → "X.XXXXXX GHz" / "X.XXX MHz" / "X.X kHz" / "X Hz". */
export function formatFreq(hz: number): string {
  // GHz keeps six decimals so narrow 23 cm segments (e.g. 1296.000–1296.050 MHz)
  // still render distinct endpoints at 1 kHz resolution.
  if (hz >= 1_000_000_000) return `${(hz / 1_000_000_000).toFixed(6)} GHz`;
  if (hz >= 1_000_000) return `${(hz / 1_000_000).toFixed(3)} MHz`;
  if (hz >= 1_000) return `${(hz / 1_000).toFixed(1)} kHz`;
  return `${hz} Hz`;
}

/** Convert "#RRGGBB" (or "RRGGBB") to "R,G,B" decimal string for rgba(). */
export function hexToRgb(hex: string): string {
  const h = hex.replace('#', '');
  const r = parseInt(h.substring(0, 2), 16);
  const g = parseInt(h.substring(2, 4), 16);
  const b = parseInt(h.substring(4, 6), 16);
  return `${r},${g},${b}`;
}

/** Compute segment position as left% and width%, clamped to [0, 100]. */
export function computePosition(
  segStart: number,
  segEnd: number,
  startFreq: number,
  endFreq: number,
): { leftPct: number; widthPct: number } {
  const span = endFreq - startFreq;
  const rawLeft = ((segStart - startFreq) / span) * 100;
  const rawRight = ((segEnd - startFreq) / span) * 100;
  const leftPct = Math.max(0, Math.min(100, rawLeft));
  const rightPct = Math.max(0, Math.min(100, rawRight));
  return { leftPct, widthPct: rightPct - leftPct };
}

/**
 * Filter remote segments: exclude hidden layers, exclude segments outside the
 * display range, and suppress non-ham segments that overlap >30% with any ham segment.
 */
export function filterOverlapping(
  segments: RemoteSegment[],
  startFreq: number,
  endFreq: number,
  hiddenLayers: string[] = [],
): RemoteSegment[] {
  const visible = segments.filter(
    (s) => s.end > startFreq && s.start < endFreq && !hiddenLayers.includes(s.layer),
  );
  const hamSegs = visible.filter((s) => s.layer === 'ham');
  return visible.filter((s) => {
    if (s.layer === 'ham') return true;
    return !hamSegs.some((h) => {
      const overlapStart = Math.max(s.start, h.start);
      const overlapEnd = Math.min(s.end, h.end);
      if (overlapEnd <= overlapStart) return false;
      const overlapRatio = (overlapEnd - overlapStart) / (s.end - s.start);
      return overlapRatio > 0.3;
    });
  });
}

/**
 * Should the debounced fetch be skipped?
 * Returns true when the viewport already lies inside the last fetched range
 * (the component fetches with a margin on each side) and false on first fetch.
 */
export function shouldSkipFetch(
  start: number,
  end: number,
  lastStart: number,
  lastEnd: number,
  region?: string | null,
  lastRegion?: string | null,
): boolean {
  if (lastStart === 0) return false;
  // The backend serves segments for the effective region, so a viewport
  // contained in the previous fetch range must still refetch after a switch.
  if (region !== undefined && region !== lastRegion) return false;
  return start >= lastStart && end <= lastEnd;
}

/**
 * Does the bundled local copy (`lib/data/arrl-band-plan.ts`, the ARRL HF plan)
 * describe this effective band-plan region? It does for the US plan and for
 * IARU Region 2, which the server also serves from the ARRL file, and for a
 * caller that passes no region at all (`undefined`, legacy behavior). Any
 * other region, and an effective region that is not known yet (`null`), must
 * never show it: after a failed or empty refresh the overlay renders nothing
 * rather than another region's segments.
 */
export function localCopyServesRegion(region: string | null | undefined): boolean {
  return region === undefined || region === 'US' || region === 'IARU-R2';
}
