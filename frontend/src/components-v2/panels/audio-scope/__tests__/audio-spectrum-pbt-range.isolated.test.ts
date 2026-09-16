/**
 * MOR-2475 PR-4 — the audio-spectrum renderer converts PBT raw→Hz through
 * the range handed to it (`SpectrumState.pbtRange`, derived by the props
 * layer from its own `caps` argument via `pbtRangeFromCaps`), never through
 * the capabilities STORE singleton that `$lib/radio/filter-controls`'s
 * `pbtRawToHz` falls back to when called without a range. Each test first
 * installs a NON-default `pbt_inner` range in that store, so any renderer
 * path that consults the store produces visibly different geometry than the
 * passed range — the exact regression this PR exists to prevent.
 *
 * Pool: `isolated` (MOR-1272) — `setCapabilities` mutates module-global
 * store state; restored to a controls-less neutral set in `afterEach`, same
 * pattern as `filter-passband-adapter.isolated.test.ts`.
 */
import { afterEach, describe, expect, it } from 'vitest';

import type { Capabilities } from '$lib/types/capabilities';
import {
  renderAudioSpectrum,
  AudioSpectrumRendererState,
  type SpectrumState,
} from '../audio-spectrum-renderer';
import type { PbtRange } from '$lib/radio/filter-controls';
import { setCapabilities } from '$lib/stores/capabilities.svelte';

function caps(overrides: Partial<Capabilities> = {}): Capabilities {
  return {
    model: 'fixture', scope: true, audio: true, tx: true,
    capabilities: ['scope', 'audio', 'tx', 'pbt'],
    receivers: 1, vfoScheme: 'single', freqRanges: [], modes: [], filters: [],
    audioConfig: { sampleRate: 48000, channels: 1, codecs: ['pcm16'] },
    webrtc: { available: false, enabled: false },
    txBands: [], scopeSource: 'hardware', audioFftAvailable: false,
    stateContractVersion: 1, providerGeneration: 0, ...overrides,
  } as Capabilities;
}

/** The range every PBT-publishing profile declares (raw_center 128,
 *  display ±1200) — what the props layer hands the renderer. */
const PASSED_RANGE: PbtRange = { rawCenter: 128, displayMin: -1200, displayMax: 1200 };

/** A store range that must never steer the renderer: raw_center 100,
 *  ±900 Hz — deliberately different from every shipped profile. Under a
 *  store lookup, raw 200 would read (200−100)·9 = 900 Hz (inner center
 *  240.5) instead of the passed range's 675 Hz (230.375). */
const STORE_CAPS = caps({
  controls: {
    pbt_inner: { raw_min: 0, raw_max: 255, raw_center: 100, display_min: -900, display_max: 900 },
  } as Capabilities['controls'],
});

const NEUTRAL_STORE_CAPS = caps();
afterEach(() => setCapabilities(NEUTRAL_STORE_CAPS));

const TRAP_TOP = 18;
const INNER_PBT_STROKE = 'rgba(80, 180, 255, 0.7)';
const OUTER_PBT_STROKE = 'rgba(255, 160, 60, 0.7)';
const PLAIN_STROKE = 'rgba(240, 240, 240, 0.8)';

type Stroke = { style: string; points: [number, number][] };

function mockCtxWithStrokes() {
  const noop = () => {};
  const strokes: Stroke[] = [];
  let current: [number, number][] = [];
  let style = '';
  const ctx = {
    clearRect: noop,
    fillRect: noop,
    fillText: noop,
    beginPath: () => { current = []; },
    moveTo: (x: number, y: number) => { current.push([x, y]); },
    lineTo: (x: number, y: number) => { current.push([x, y]); },
    closePath: noop,
    stroke: () => { strokes.push({ style, points: current }); },
    fill: noop,
    clip: noop,
    save: noop,
    restore: noop,
    quadraticCurveTo: noop,
    createLinearGradient: () => ({ addColorStop: noop }),
    set fillStyle(_: unknown) {},
    set strokeStyle(v: string) { style = v; },
    set lineWidth(_: unknown) {},
    set font(_: unknown) {},
    set textAlign(_: unknown) {},
  } as unknown as CanvasRenderingContext2D;
  return { ctx, strokes };
}

/** Center x of a trapezoid stroke: the midpoint of its top edge. */
function centerXOf(strokes: Stroke[], color: string): number | undefined {
  const stroke = strokes.find((s) => s.style === color);
  if (!stroke) return undefined;
  const top = stroke.points.filter(([, y]) => y === TRAP_TOP);
  return (top[0][0] + top[top.length - 1][0]) / 2;
}

function baseState(overrides: Partial<SpectrumState> = {}): SpectrumState {
  return {
    pixels: null,
    bandwidth: 3600,
    filterWidth: 2400,
    filterWidthMax: 3600,
    pbtInner: 128,
    pbtOuter: 128,
    manualNotch: false,
    notchFreq: 128,
    contour: 0,
    contourFreq: 128,
    ...overrides,
  };
}

describe('renderer PBT conversion never reads the capabilities store (MOR-2475 PR-4)', () => {
  it('places the PBT trapezoids by the passed range even when the store disagrees', () => {
    setCapabilities(STORE_CAPS);
    const { ctx, strokes } = mockCtxWithStrokes();
    renderAudioSpectrum(ctx, 400, 160, baseState({
      pbtInner: 200, pbtOuter: 56, pbtRange: PASSED_RANGE,
    }), new AudioSpectrumRendererState());
    expect(centerXOf(strokes, INNER_PBT_STROKE)).toBeCloseTo(230.375, 9);
    expect(centerXOf(strokes, OUTER_PBT_STROKE)).toBeCloseTo(169.625, 9);
  });

  it('draws no PBT overlay and no passband shift when no range is passed, whatever the store holds', () => {
    setCapabilities(STORE_CAPS);
    const { ctx, strokes } = mockCtxWithStrokes();
    renderAudioSpectrum(ctx, 400, 160, baseState({
      pbtInner: 200, pbtOuter: 56,
    }), new AudioSpectrumRendererState());
    expect(strokes.some((s) => s.style === INNER_PBT_STROKE)).toBe(false);
    expect(strokes.some((s) => s.style === OUTER_PBT_STROKE)).toBe(false);
    expect(centerXOf(strokes, PLAIN_STROKE)).toBeCloseTo(200, 9);
  });
});
