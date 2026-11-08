import { scopeWire } from '$lib/runtime/__tests__/scope-wire-fixture';
import { describe, expect, it } from 'vitest';
import { get } from 'svelte/store';
import type { ScopeGeometry } from '../../../lib/runtime/scope-frame';
import { resolveLcdSpectrumFrame, type LcdSpectrumFrameExpectation } from '../lcd-display-contract';
import { lcdStreamFixture } from './scope-stream-fixture';
import { ingressScopeFrame } from '../../../lib/runtime/adapters/scope-adapter';

const EXPECT_HARDWARE_MAIN: LcdSpectrumFrameExpectation = { source: 'hardware', receiver: 'MAIN' };
function geometry(overrides: Partial<ScopeGeometry> = {}): ScopeGeometry {
  const stream = lcdStreamFixture({ source: 'hardware', receiver: 'MAIN', freshness: 'fresh',
    startHz: 14_200_000, endHz: 14_300_000, pixels: new Uint8Array([26, 179, 77]) })!;
  const facts = get(stream.geometry)!;
  stream.dispose();
  return { ...facts, ...overrides };
}

describe('LCD geometry contract', () => {
  it.each([[3, 'live'], [1, 'ghost'], [0, 'out-of-range']] as const)(
    'qualifies real flagged wire geometry with width%s as %s', (width, state) => {
      const buffer = scopeWire({ outOfRange: true, pixels: width > 0 ? Uint8Array.from([0, 128, 255].slice(0, width)) : null, viewStartHz: 14_200_000, viewEndHz: 14_300_000 });
      const result = ingressScopeFrame(buffer, { source: 'radio', sourceId: 'scope', epoch: 1 });
      expect(result.ok).toBe(true); if (!result.ok) throw new Error(result.reason);
      expect(result.geometry.outOfRange).toBe(true); expect(result.geometry.hasPixels).toBe(width > 0);
      const resolution = resolveLcdSpectrumFrame(result.geometry, EXPECT_HARDWARE_MAIN);
      expect(resolution.state).toBe(state);
      if (width === 1) expect(resolution).toEqual({ state: 'ghost', reason: 'invalid' });
    },
  );
  it.each(['hardware', 'audio-fft'] as const)('admits source-qualified %s geometry unchanged', source => {
    const candidate = geometry({ source: source === 'hardware' ? 'radio' : 'audio_fft' });
    const result = resolveLcdSpectrumFrame(candidate, { source, receiver: 'MAIN' });
    expect(result).toEqual({ state: 'live', geometry: candidate });
    if (result.state === 'live') {
      expect(result.geometry).toBe(candidate);
      expect(result.geometry.view).toBe(candidate.view);
    }
  });
  it.each([
    ['source-mismatch', geometry({ source: 'audio_fft' }), EXPECT_HARDWARE_MAIN],
    ['receiver-mismatch', geometry({ receiver: 1 }), EXPECT_HARDWARE_MAIN],
    ['stale', geometry({ freshness: 'stale' }), EXPECT_HARDWARE_MAIN],
    ['receiver-unknown', geometry(), { source: 'hardware', receiver: null }],
  ] as const)('fails a %s closed to ghost geometry', (reason, candidate, expectation) => {
    expect(resolveLcdSpectrumFrame(candidate, expectation)).toEqual({ state: 'ghost', reason });
  });
  it.each([undefined, null])('treats absent input as missing', candidate => {
    expect(resolveLcdSpectrumFrame(candidate, EXPECT_HARDWARE_MAIN)).toEqual({ state: 'ghost', reason: 'missing' });
  });
  it.each([
    geometry({ view: { startHz: Number.NaN, endHz: 14_300_000 } }),
    geometry({ view: { startHz: 14_200_000, endHz: Number.NaN } }),
    geometry({ view: { startHz: Number.NEGATIVE_INFINITY, endHz: 14_300_000 } }),
    geometry({ view: { startHz: 14_200_000, endHz: Number.POSITIVE_INFINITY } }),
    geometry({ view: { startHz: 14_300_000, endHz: 14_300_000 } }),
    geometry({ view: { startHz: 14_300_000, endHz: 14_200_000 } }),
    geometry({ hasPixels: false, width: 0 }),
    geometry({ hasPixels: true, width: 1 }),
    geometry({ hasPixels: true, width: 0 }),
    geometry({ hasPixels: false, width: 1 }),
    geometry({ hasPixels: false, width: 2 }),
    geometry({ hasPixels: false, width: 475 }),
  ])('rejects invalid render geometry without fabricating data', candidate => {
    const result = resolveLcdSpectrumFrame(candidate, EXPECT_HARDWARE_MAIN);
    expect(result).toEqual({ state: 'ghost', reason: 'invalid' });
    expect(result).not.toHaveProperty('geometry');
  });
  it('never borrows another receiver', () => {
    expect(resolveLcdSpectrumFrame(geometry(), { source: 'hardware', receiver: 'SUB' }))
      .toEqual({ state: 'ghost', reason: 'receiver-mismatch' });
  });
});


it('accepts receiver-neutral IQ source geometry through the same RF display contract', () => {
  const candidate = geometry({ source: 'iq_fft', receiver: null });
  expect(resolveLcdSpectrumFrame(candidate, EXPECT_HARDWARE_MAIN)).toEqual({ state: 'live', geometry: candidate });
});
