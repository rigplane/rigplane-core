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

export interface ScopeOverlayTargets {
  /** The whole-band preset of the band under the frequency, or null. */
  readonly band: ScopePreset | null;
  /** The band-plan segment (CW / digital / phone) under the frequency, or null. */
  readonly segment: ScopePreset | null;
}

/**
 * MOR-4386: which of the already-derived presets the panorama overlay's
 * [B] and [S] buttons open. Pure selection over `scopePresets` output — no
 * second band-plan lookup. The segment is the one whose range holds the
 * frequency (start inclusive, end exclusive, so a shared edge belongs to the
 * upper segment); outside every segment, or with no segment data, it is null.
 */
export function scopeOverlayTargets(
  presets: readonly ScopePreset[], frequencyHz: number | null,
): ScopeOverlayTargets {
  const band = presets.find((preset) => preset.kind === 'band') ?? null;
  const segment = frequencyHz === null ? null : presets.find((preset) =>
    (preset.kind === 'cw' || preset.kind === 'digital' || preset.kind === 'phone')
    && 'startHz' in preset.view && 'endHz' in preset.view
    && frequencyHz >= preset.view.startHz && frequencyHz < preset.view.endHz) ?? null;
  return { band, segment };
}

export function followScopePreset(presets: readonly ScopePreset[]): ScopePreset | null {
  return presets.find((preset) => preset.kind === 'follow') ?? null;
}
