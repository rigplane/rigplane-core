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

/** Format frequency for human display: Hz → "X.XXX MHz" / "X.X kHz" / "X Hz". */
export function formatFreq(hz: number): string {
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
): boolean {
  if (lastStart === 0) return false;
  const span = end - start;
  return (
    Math.abs(start - lastStart) < span * 0.01 &&
    Math.abs(end - lastEnd) < span * 0.01
  );
}
