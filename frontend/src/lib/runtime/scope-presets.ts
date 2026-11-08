import { getVisibleSegments } from '$lib/data/arrl-band-plan';
import { flattenBands } from '$lib/radio/band-plan';
import type { FreqRange } from '$lib/types/capabilities';
import { localCopyServesRegion, type RemoteSegment } from '../../components/spectrum/band-plan-logic';
import type { ScopeViewRequest } from './scope-view';

export interface ScopePreset {
  readonly kind: 'band' | 'cw' | 'digital' | 'phone' | 'follow';
  readonly label: string;
  readonly view: ScopeViewRequest;
}

/** Derive presets for the current capability band and effective plan. */
export function scopePresets(
  ranges: FreqRange[], frequencyHz: number, region: string | null | undefined,
  remoteSegments: RemoteSegment[] | undefined, followSpanHz: number,
): ScopePreset[] {
  const presets: ScopePreset[] = [];
  const band = flattenBands(ranges).find((item) => frequencyHz >= item.start && frequencyHz <= item.end);
  if (band) {
    presets.push({ kind: 'band', label: band.name, view: { startHz: band.start, endHz: band.end } });
    const segments = remoteSegments !== undefined
      ? remoteSegments.filter((segment) => segment.layer === 'ham').map((segment) => ({
          startHz: segment.start, endHz: segment.end, mode: segment.mode, label: segment.label,
        }))
      : localCopyServesRegion(region)
        ? getVisibleSegments(band.start, band.end).map(({ segment }) => segment)
        : [];
    for (const segment of segments) {
      if (segment.mode !== 'cw' && segment.mode !== 'digital' && segment.mode !== 'phone') continue;
      const startHz = Math.max(band.start, segment.startHz);
      const endHz = Math.min(band.end, segment.endHz);
      if (startHz < endHz) presets.push({ kind: segment.mode, label: segment.label, view: { startHz, endHz } });
    }
  }
  presets.push({ kind: 'follow', label: 'Follow VFO', view: { follow: 'vfo', spanHz: followSpanHz } });
  return presets;
}
