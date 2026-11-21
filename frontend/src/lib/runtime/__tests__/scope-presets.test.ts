import profiles from "./fixtures/scope-profile-caps.json";
import { describe, expect, it } from 'vitest';
import type { FreqRange } from '$lib/types/capabilities';
import { scopeOverlayTargets, scopePresets } from '../scope-presets';


describe.each(["IC-705", "IC-7610"] as const)('%s profile presets', (profile) => {
  it('derives the current band, local US segments, and follow intent', () => {
    const presets = scopePresets(profiles[profile] as FreqRange[], 14_200_000, 'US', undefined, 100_000);
    expect(presets.map((p) => p.kind)).toEqual(['band', 'cw', 'digital', 'phone', 'follow']);
    expect(presets[0].view).toEqual({ startHz: 14_000_000, endHz: 14_350_000 });
    expect(presets.slice(1, 4).map(p => p.view)).toEqual([
      { startHz: 14_000_000, endHz: 14_070_000 },
      { startHz: 14_070_000, endHz: 14_150_000 },
      { startHz: 14_150_000, endHz: 14_350_000 },
    ]);
    expect(presets.at(-1)?.view).toEqual({ follow: 'vfo', spanHz: 100_000 });
  });
  it('does not use US fallback for an unknown region and accepts authoritative remote segments', () => {
    expect(scopePresets(profiles[profile] as FreqRange[], 14_200_000, null, undefined, 100_000).map(p => p.kind)).toEqual(['band', 'follow']);
    const segments = [{ start: 14_100_000, end: 14_250_000, mode: 'phone', label: 'Phone', color: '', opacity: 1, band: '20m', layer: 'ham', priority: 0 }];
    expect(scopePresets(profiles[profile] as FreqRange[], 14_200_000, 'other', segments, 100_000)[1].view).toEqual({ startHz: 14_100_000, endHz: 14_250_000 });
  });
});

describe.each(["IC-705", "IC-7610"] as const)('%s overlay targets (MOR-4386)', (profile) => {
  const presets = (hz: number) => scopePresets(profiles[profile] as FreqRange[], hz, 'US', undefined, 100_000);
  it('resolves B to the band and S to the band-plan segment under the frequency', () => {
    const phone = scopeOverlayTargets(presets(14_200_000), 14_200_000);
    expect(phone.band?.view).toEqual({ startHz: 14_000_000, endHz: 14_350_000 });
    expect([phone.segment?.kind, phone.segment?.view]).toEqual(['phone', { startHz: 14_150_000, endHz: 14_350_000 }]);
    expect(scopeOverlayTargets(presets(14_030_000), 14_030_000).segment?.kind).toBe('cw');
    expect(scopeOverlayTargets(presets(14_100_000), 14_100_000).segment?.kind).toBe('digital');
  });
  it('gives a shared segment edge to the upper segment', () => {
    expect(scopeOverlayTargets(presets(14_150_000), 14_150_000).segment?.kind).toBe('phone');
  });
  it('has no segment without segment data, and no band outside the profile ranges', () => {
    const noRegion = scopePresets(profiles[profile] as FreqRange[], 14_200_000, null, undefined, 100_000);
    expect(scopeOverlayTargets(noRegion, 14_200_000)).toMatchObject({ segment: null, band: { kind: 'band' } });
    const outside = scopePresets([], 14_200_000, 'US', undefined, 100_000);
    expect(scopeOverlayTargets(outside, 14_200_000)).toEqual({ band: null, segment: null });
    expect(scopeOverlayTargets(presets(14_200_000), null).segment).toBeNull();
  });
});
