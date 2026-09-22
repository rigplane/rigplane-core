import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { mount, unmount, flushSync } from 'svelte';
import type { ComponentProps } from 'svelte';
// @ts-expect-error -- Svelte does not publish types for its reactive test harness.
import { proxy } from 'svelte/internal/client';
import type { Capabilities } from '$lib/types/capabilities';
import { clearCapabilities, setCapabilities } from '$lib/stores/capabilities.svelte';
import LinearSMeter from '../LinearSMeter.svelte';
import { projectSignalMeter } from '../smeter-scale';
import type { SignalMeterFrame } from '../signal-meter-motion.svelte';
import { readFileSync } from 'node:fs';

// MOR-2509 (mock-up v7): the VFO-panel S-meter face. The fixture curves are
// the two calibration shapes the rest of this directory already exercises —
// the IC-7610-like dense table and the IC-7300-like three-knot table — so the
// asserted positions below read as "what the radio's own table says about
// this level", not as a borrowed curve.
const IC7610_LIKE_CAL = [
  { raw: 0, actual: -54, label: 'S0' },
  { raw: 26, actual: -48, label: 'S1' },
  { raw: 52, actual: -36, label: 'S3' },
  { raw: 78, actual: -24, label: 'S5' },
  { raw: 103, actual: -12, label: 'S7' },
  { raw: 130, actual: 0, label: 'S9' },
  { raw: 165, actual: 10, label: 'S9+10' },
  { raw: 200, actual: 20, label: 'S9+20' },
  { raw: 240, actual: 40, label: 'S9+40' },
];
const IC7300_LIKE_CAL = [
  { raw: 0, actual: -54, label: 'S0' },
  { raw: 120, actual: 0, label: 'S9' },
  { raw: 241, actual: 60, label: 'S9+60' },
];

// Seeded into the real capabilities store, not vi.mock'd, for the same
// module-cache race reason `LinearSMeter.test.ts` documents (fast pool,
// isolate: false).
function makeCaps(cal: typeof IC7610_LIKE_CAL): Capabilities {
  return {
    model: 'IC-7610',
    scope: true,
    audio: true,
    tx: true,
    capabilities: ['scope', 'tx'],
    receivers: 2,
    vfoScheme: 'main_sub',
    freqRanges: [{ start: 1800000, end: 30000000, label: 'HF' }],
    modes: ['USB', 'LSB', 'CW', 'AM', 'FM'],
    filters: ['FIL1', 'FIL2', 'FIL3'],
    audioConfig: { sampleRate: 48000, channels: 1, codecs: ['opus'] },
    webrtc: { available: true, enabled: false },
    txBands: null,
    stateContractVersion: 1,
    providerGeneration: 0,
    meterCalibrations: { s_meter: cal },
  };
}

// ── jsdom harness: give bind:clientWidth a real width ───────────────────────
// Svelte binds element size through ResizeObserver; jsdom has none and every
// layout box is 0. The fake observer records targets and lets the test fire
// the callback, and clientWidth is overridden on the prototype so every
// observed element reports the same fixture width.
const FIXTURE_WIDTH = 606;
// mock-up v8 value column: 10px gap + 48px cell = 58px (the ticks row, Po
// ticks and Po bar all reserve margin-right: 58px in the mock-up). The track
// is the flex:1 remainder — the exact width minus 58, not floored to the
// dash pitch; only the lit extent and the S9 split snap to the 3px raster.
const TRACK_X = 0;
const TRACK_W = FIXTURE_WIDTH - 58;
const READOUT_X = TRACK_X + TRACK_W + 10;

class FakeResizeObserver {
  private static callback?: (entries: { target: Element }[]) => void;
  private static targets = new Set<Element>();
  constructor(callback: (entries: { target: Element }[]) => void) {
    FakeResizeObserver.callback = callback;
  }
  observe(target: Element): void { FakeResizeObserver.targets.add(target); }
  unobserve(target: Element): void { FakeResizeObserver.targets.delete(target); }
  disconnect(): void { FakeResizeObserver.targets.clear(); }
  static fire(): void {
    const entries = [...FakeResizeObserver.targets].map((target) => ({ target }));
    FakeResizeObserver.callback?.(entries);
  }
}

let originalResizeObserver: unknown;
let originalClientWidth: PropertyDescriptor | undefined;
let components: ReturnType<typeof mount>[] = [];
let roots: HTMLElement[] = [];

beforeEach(() => {
  originalResizeObserver = globalThis.ResizeObserver;
  globalThis.ResizeObserver = FakeResizeObserver as unknown as typeof ResizeObserver;
  originalClientWidth = Object.getOwnPropertyDescriptor(Element.prototype, 'clientWidth');
  Object.defineProperty(Element.prototype, 'clientWidth', {
    configurable: true,
    get: () => FIXTURE_WIDTH,
  });
});

afterEach(() => {
  components.forEach((component) => unmount(component));
  roots.forEach((root) => root.remove());
  components = [];
  roots = [];
  globalThis.ResizeObserver = originalResizeObserver as typeof ResizeObserver;
  if (originalClientWidth) {
    Object.defineProperty(Element.prototype, 'clientWidth', originalClientWidth);
  }
  vi.unstubAllGlobals();
  clearCapabilities();
  vi.restoreAllMocks();
});

// Settled-geometry mounts run under prefers-reduced-motion so the local
// motion snaps to the reading synchronously — the same determinism trick
// `LinearSMeter.display.test.ts` uses for its color reads.
function stubReducedMotion(): void {
  vi.stubGlobal('matchMedia', (query: string): MediaQueryList => ({
    matches: query === '(prefers-reduced-motion: reduce)', media: query, onchange: null,
    addEventListener: vi.fn(), removeEventListener: vi.fn(),
    addListener: vi.fn(), removeListener: vi.fn(), dispatchEvent: vi.fn(() => false),
  }) as unknown as MediaQueryList);
}

function mountMeter(props: ComponentProps<typeof LinearSMeter>): HTMLElement {
  const target = document.createElement('div');
  document.body.appendChild(target);
  roots.push(target);
  const component = mount(LinearSMeter, { target, props });
  flushSync();
  FakeResizeObserver.fire();
  flushSync();
  components.push(component);
  return target;
}

function mountReactiveMeter(props: ComponentProps<typeof LinearSMeter>) {
  const state = proxy({ ...props });
  const target = document.createElement('div');
  document.body.appendChild(target);
  roots.push(target);
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  const component = mount(LinearSMeter as any, { target, props: state });
  flushSync();
  FakeResizeObserver.fire();
  flushSync();
  components.push(component);
  return { target, state };
}

// ── reading helpers over the v7 face's permanent nodes ──────────────────────

const svgOf = (target: HTMLElement): SVGSVGElement =>
  target.querySelector('svg[data-variant="vfo"]')!;

// The zone past S9 is a separate line; a hidden zone line carries its
// boundary coordinates regardless, so only visible ones count as fill.
function litLineEnd(root: ParentNode, selector: string): number | null {
  const line = root.querySelector(selector)!;
  if (line.getAttribute('visibility') === 'hidden') return null;
  return Number(line.getAttribute('x2'));
}

function fillFraction(root: ParentNode): number {
  const track = root.querySelector('[data-meter-track]')!;
  const x1 = Number(track.getAttribute('x1'));
  const trackW = Number(track.getAttribute('x2')) - x1;
  const end = Math.max(
    litLineEnd(root, '[data-meter-fill]') ?? x1,
    litLineEnd(root, '[data-meter-fill-red]') ?? x1,
  );
  return (end - x1) / trackW;
}

function glowFraction(root: ParentNode): number {
  const track = root.querySelector('[data-meter-track]')!;
  const x1 = Number(track.getAttribute('x1'));
  const trackW = Number(track.getAttribute('x2')) - x1;
  const end = Math.max(
    litLineEnd(root, '[data-meter-glow]') ?? x1,
    litLineEnd(root, '[data-meter-glow-red]') ?? x1,
  );
  return (end - x1) / trackW;
}

function peakFraction(root: ParentNode): number {
  const track = root.querySelector('[data-meter-track]')!;
  const x1 = Number(track.getAttribute('x1'));
  const trackW = Number(track.getAttribute('x2')) - x1;
  return (Number(root.querySelector('[data-meter-peak]')!.getAttribute('x1')) - x1) / trackW;
}

function peakVisible(root: ParentNode): boolean {
  return root.querySelector('[data-meter-peak]')?.getAttribute('visibility') === 'visible';
}

// Levels on the IC-7610-like table, with the fraction the evenly spaced
// 1..9/+60 scale places them at: S-units linear over 0..4/7, dB-over-S9
// linear over 4/7..1. The levels are per-radio table values; the fractions
// are the mock-up v7 scale geometry shared by every radio.
const VFO_FRACTIONS = {
  s1: 0, s3: 1 / 7, s5: 2 / 7, s7: 3 / 7, s9: 4 / 7, over20: 5 / 7, over40: 6 / 7,
} as const;

it('routes the S track lit fill through one inherited saturation hook', () => {
  const source = readFileSync('src/components-v2/meters/LinearSMeter.svelte', 'utf8');
  const style = source.match(/<style>([\s\S]*)<\/style>/)?.[1] ?? '';
  // The filter rule covers the S track's lit lines and NOTHING else: the
  // mock-up filters `.lit` only; `.polit` stays the plain meter blue.
  expect(style).toMatch(/\[data-meter-fill\][\s\S]*\[data-meter-fill-red\][^{]*\{[^}]*filter:\s*var\(--v2-meter-lit-filter,\s*none\)/);
  expect(style).not.toMatch(/\[data-lower-fill\][^{]*\{[^}]*filter:/);
  expect(style).not.toMatch(/filter:[^;]*\[[^\]]*data-lower-fill/);
  expect((style.match(/--v2-meter-lit-filter/g) ?? [])).toHaveLength(1);
});

/** The lit extent the face draws for a fill fraction, mirroring the
 *  half-covered rule: a segment lights when the fill covers at least half
 *  of it, and the extent is that segment's right edge — capped at the
 *  last whole dash that FITS the track, the greatest 3n+2 ≤ 548, which is
 *  548 itself (n=182), so a full-scale reading lights the complete final
 *  dash flush with the track end. */
function litExtentFraction(fillFraction: number): number {
  const fillPx = fillFraction * TRACK_W;
  const segment = Math.floor((fillPx - 1) / 3);
  if (segment < 0) return 0;
  return (Math.min(Math.floor((TRACK_W - 2) / 3), segment) * 3 + 2) / TRACK_W;
}

// ── 1. Geometry: whole-pixel segments, fixed reading slot ───────────────────

describe('MOR-2509 v7 VFO face — segment geometry', () => {
  beforeEach(() => { stubReducedMotion(); setCapabilities(makeCaps(IC7610_LIKE_CAL)); });

  it('draws a 2px lit / 1px gap dash pattern over the exact flex remainder', () => {
    const target = mountMeter({ value: 0, variant: 'vfo', compact: true });
    const track = svgOf(target).querySelector('[data-meter-track]')!;
    expect(track.getAttribute('stroke-dasharray')).toBe('2 1');
    const x1 = Number(track.getAttribute('x1'));
    const trackW = Number(track.getAttribute('x2')) - x1;
    // The mock-up's `.track { flex: 1 }` next to the 58px column leaves the
    // exact width minus 58 — NOT a 3px-floored length. Red if the v7 floor
    // returns. clientWidth is integral, so dash edges stay whole-pixel.
    expect(x1).toBe(TRACK_X);
    expect(trackW).toBe(TRACK_W);
    expect(trackW).toBe(FIXTURE_WIDTH - 58);
    expect(Number.isInteger(trackW)).toBe(true);
  });

  it('reserves a fixed start-anchored slot for the reading; text never moves', () => {
    const s1 = mountMeter({ value: -48, variant: 'vfo', compact: true });
    const s960 = mountMeter({ value: 20, variant: 'vfo', compact: true });
    for (const target of [s1, s960]) {
      const readings = svgOf(target).querySelectorAll('[data-meter-reading]');
      // mock-up v8 `.sval`: ONE 13px line — the v7 dBm second line is gone.
      expect(readings).toHaveLength(1);
      const primary = readings[0]!;
      expect(primary.getAttribute('text-anchor')).toBe('start');
      expect(Number(primary.getAttribute('x'))).toBe(READOUT_X);
      expect(Number(primary.getAttribute('font-size'))).toBe(13);
      expect(svgOf(target).querySelector('[data-meter-reading-secondary]')).toBeNull();
    }
    expect(svgOf(s1).querySelector('[data-meter-reading]')!.getAttribute('x'))
      .toBe(svgOf(s960).querySelector('[data-meter-reading]')!.getAttribute('x'));
    expect(svgOf(s1).textContent).toContain('S1');
    expect(svgOf(s960).textContent).toContain('S9+20');
  });

  it('labels only the S-units and +dB marks this calibration declares, at their scale slots', () => {
    const target = mountMeter({ value: 0, variant: 'vfo', compact: true });
    const labels = [...svgOf(target).querySelectorAll('[data-scale-label]')];
    // The IC-7610-like table declares S1/S3/S5/S7/S9 and tops at +40, so
    // "+60" (past the table's max) is not drawn and nothing is invented.
    expect(labels.map((label) => label.textContent))
      .toEqual(['1', '3', '5', '7', '9', '+20', '+40']);
    const slots = [0, 1 / 7, 2 / 7, 3 / 7, 4 / 7, 5 / 7, 6 / 7];
    labels.forEach((label, index) => {
      expect(Number(label.getAttribute('x'))).toBeCloseTo(TRACK_X + slots[index] * TRACK_W, 1);
      const tick = svgOf(target).querySelector(`[data-scale-tick="${index}"]`)!;
      expect(Number(tick.getAttribute('x1'))).toBeCloseTo(Number(label.getAttribute('x')), 5);
      expect(Number(tick.getAttribute('y2'))).toBeGreaterThan(Number(tick.getAttribute('y1')));
    });
  });

  it('consumes theme tokens without re-stating their values, one red family past S9', () => {
    const target = mountMeter({ value: 20, variant: 'vfo', compact: true });
    expect(svgOf(target).querySelector('[data-meter-track]')!.getAttribute('stroke'))
      .toBe('var(--v2-meter-unlit)');
    expect(svgOf(target).querySelector('[data-meter-fill]')!.getAttribute('stroke'))
      .toBe('var(--v2-meter-blue)');
    expect(svgOf(target).querySelector('[data-meter-fill-red]')!.getAttribute('stroke'))
      .toBe('var(--v2-meter-red)');
    const labels = [...svgOf(target).querySelectorAll('[data-scale-label]')];
    for (const label of labels.slice(5)) {
      expect(label.getAttribute('fill')).toBe('var(--v2-meter-red)');
    }
    for (const label of labels.slice(0, 5)) {
      expect(label.getAttribute('fill')).toBe('var(--dl-vfo-meter-tick-label, #e6edf4)');
    }
    // The peak marker is the mock-up's one constant light tone at every
    // zone — no yellow, no orange, no red variant past S9.
    const peak = svgOf(target).querySelector('[data-meter-peak]')!;
    expect(peak.getAttribute('stroke')).toBe('var(--dl-vfo-meter-peak, #e8f1ff)');
  });
});

// ── 2. Truthful level→position mapping on the evenly spaced scale ───────────

describe('MOR-2509 v7 VFO face — the fill maps the reading onto the drawn scale', () => {
  beforeEach(() => { stubReducedMotion(); setCapabilities(makeCaps(IC7610_LIKE_CAL)); });

  it.each([
    ['S1 (-48)', -48, VFO_FRACTIONS.s1],
    ['S5 (-24)', -24, VFO_FRACTIONS.s5],
    ['S7 (-12)', -12, VFO_FRACTIONS.s7],
    ['S9 (0)', 0, VFO_FRACTIONS.s9],
    ['S9+20 (+20)', 20, VFO_FRACTIONS.over20],
    ['S9+40 (+40, the table top)', 40, VFO_FRACTIONS.over40],
    ['below S1 clamps at the left end', -54, 0],
    ['above the table top clamps at +40', 60, VFO_FRACTIONS.over40],
  ])('settled reading %s lights whole segments up to %f of the track', (_name, value, expected) => {
    const target = mountMeter({ value, variant: 'vfo', compact: true });
    expect(fillFraction(svgOf(target))).toBeCloseTo(litExtentFraction(expected), 6);
    // The FINAL lit end sits on the whole-segment grid: the right edge of a
    // lit dash, 2px past a pitch boundary. The blue zone line may instead
    // end on the snapped S9 zone boundary, so only the outermost visible
    // end is the grid claim.
    const visibleEnds = (['[data-meter-fill]', '[data-meter-fill-red]'] as const)
      .map((selector) => svgOf(target).querySelector(selector)!)
      .filter((line) => line.getAttribute('visibility') !== 'hidden')
      .map((line) => Number(line.getAttribute('x2')));
    // A zero reading lights nothing; any other reading's final end is on
    // the dash grid.
    if (visibleEnds.length > 0) {
      expect((Math.max(...visibleEnds) - TRACK_X - 2) % 3).toBe(0);
    }
  });

  it('lights a partial segment only when the fill covers at least half of it', () => {
    // Segment N lights when the fill reaches 3N+1px (half of its 2px dash
    // inside the 3px pitch), so segment 25 — cell [75, 77) — flips at 76px.
    // How the literals were computed for the 548px fixture track: the
    // uniform fill fraction is the knots lerp of the reading's motion
    // fraction (S1 knot at 0.0611 → S9 knot at 0.55 spans to 0..4/7); S3
    // exact (-36) sits at 1/7 = 78.29px, past the 76px half, so segment 25
    // lights and the extent is its right edge, 77px. -36.5 dB maps through
    // the same knots to 75.02px — past segment 24's half at 73px, short of
    // segment 25's at 76px — so the extent is segment 24's right edge, 74px.
    const s3 = mountMeter({ value: -36, variant: 'vfo', compact: true });
    expect(fillFraction(svgOf(s3))).toBeCloseTo(77 / 548, 6);
    const belowHalf = mountMeter({ value: -36.5, variant: 'vfo', compact: true });
    expect(fillFraction(svgOf(belowHalf))).toBeCloseTo(74 / 548, 6);
  });

  it('a raw domain keeps a linear bar with no S labels and no red split', () => {
    const projection = projectSignalMeter(53, { kind: 'raw' });
    const target = mountMeter({
      frame: {
        projection,
        smoothedFraction: projection.motionFraction!,
        peakFraction: null, afterglowFraction: null, reducedMotion: false,
      },
      variant: 'vfo', compact: true,
    });
    const svg = svgOf(target);
    expect(svg.querySelectorAll('[data-scale-label]')).toHaveLength(0);
    expect(fillFraction(svg)).toBeCloseTo(litExtentFraction(projection.motionFraction!), 6);
    expect(svg.querySelector('[data-meter-fill-red]')!.getAttribute('visibility')).toBe('hidden');
  });

  it('the three-knot table draws only its declared numerals — no invented S-units', () => {
    setCapabilities(makeCaps(IC7300_LIKE_CAL));
    const target = mountMeter({ value: -12, variant: 'vfo', compact: true });
    const labels = [...svgOf(target).querySelectorAll('[data-scale-label]')];
    // Only the S9 knot is declared (S0 carries no numeral on this scale) and
    // the table's only declared + knot is +60 — the v7 fixed +20/+40 set is
    // gone; a numeral the table does not declare is not drawn.
    expect(labels.map((label) => label.textContent)).toEqual(['9', '+60']);
    const slots = [4 / 7, 1];
    labels.forEach((label, index) => {
      expect(Number(label.getAttribute('x'))).toBeCloseTo(TRACK_X + slots[index] * TRACK_W, 1);
    });
    // Left-anchoring belongs to the slot-ZERO numeral (mock-up
    // `.ticks span:first-child` on the 1..+60 ladder); a sparse table's
    // first drawn numeral still centres on its slot.
    expect(labels[0]!.getAttribute('text-anchor')).toBe('middle');
  });

  it('the three-knot table maps S9+60 onto the full final dash and S1 to the left end', () => {
    setCapabilities(makeCaps(IC7300_LIKE_CAL));
    const over60 = mountMeter({ value: 60, variant: 'vfo', compact: true });
    // A full-scale reading lights the complete terminal dash: the greatest
    // 3n+2 ≤ 548 is 548 itself, flush with the track end — the old
    // trackW-1 cap rendered a truncated 1px dash here.
    expect(fillFraction(svgOf(over60))).toBeCloseTo(1, 6);
    expect(Number(svgOf(over60).querySelector('[data-meter-fill-red]')!.getAttribute('x2')))
      .toBe(TRACK_W);
    const s1 = mountMeter({ value: -48, variant: 'vfo', compact: true });
    expect(fillFraction(svgOf(s1))).toBeCloseTo(0, 4);
  });

  it('an unknown reading renders the empty unlit face — no placeholder text anywhere', () => {
    const target = mountMeter({ value: null, variant: 'vfo', compact: true });
    const svg = svgOf(target);
    // The scale stays drawn and in place…
    expect(svg.querySelectorAll('[data-scale-label]').length).toBeGreaterThan(0);
    // …the fill never leaves the left end, the peak is hidden…
    expect(fillFraction(svg)).toBe(0);
    expect(peakVisible(svg)).toBe(false);
    // …and the reading cell carries no glyph — the v8 face has one line
    // and no secondary node at all: no "?", no status text.
    expect(svg.querySelector('[data-meter-reading]')!.textContent).toBe('');
    expect(svg.querySelector('[data-meter-reading-secondary]')).toBeNull();
    expect(svg.textContent).not.toContain('?');
    expect(svg.textContent).not.toContain('unknown');
  });

  it('frame input positions the fill through the same scale', () => {
    const target = mountMeter({
      frame: {
        projection: projectSignalMeter(-12),
        smoothedFraction: projectSignalMeter(-12).motionFraction!,
        peakFraction: null, afterglowFraction: null, reducedMotion: false,
      },
      variant: 'vfo', compact: true,
    });
    expect(fillFraction(svgOf(target))).toBeCloseTo(litExtentFraction(VFO_FRACTIONS.s7), 6);
  });

  it('a reduced frame hides an armed peak marker and lights no glow tail', () => {
    // The stepped value itself is pinned by the settled tests above (they
    // mount under the same reduce preference and the smoother snaps). Here
    // the contract is the face's own: the SAME frame — a peak far ahead of
    // the bar, a null afterglow — arms the marker when motion is allowed
    // and hides it when the frame reports reduced motion; the glow never
    // lights without an afterglow fraction either way.
    const projection = projectSignalMeter(-12);
    const frame = (reducedMotion: boolean) => ({
      projection,
      smoothedFraction: projection.motionFraction!,
      peakFraction: Math.min(1, projection.motionFraction! + 0.4),
      afterglowFraction: null,
      reducedMotion,
    });

    const live = mountMeter({ frame: frame(false), variant: 'vfo', compact: true });
    expect(peakVisible(svgOf(live))).toBe(true);

    const reduced = mountMeter({ frame: frame(true), variant: 'vfo', compact: true });
    expect(peakVisible(svgOf(reduced))).toBe(false);
    expect(svgOf(reduced).querySelector('[data-meter-glow]')!.getAttribute('visibility'))
      .toBe('hidden');
    expect(svgOf(reduced).querySelector('[data-meter-glow-red]')!.getAttribute('visibility'))
      .toBe('hidden');
  });
});

// ── 3. The Po lower scale row ────────────────────────────────────────────────

describe('MOR-2509 v7 VFO face — the Po lower scale', () => {
  beforeEach(() => { stubReducedMotion(); setCapabilities(makeCaps(IC7610_LIKE_CAL)); });

  const PO_TICKS = [
    { value: 0, label: '0' },
    { value: 0.25, label: '25' },
    { value: 0.5, label: '50' },
    { value: 0.75, label: '75' },
    { value: 1, label: '100' },
  ];

  function mountPo(valueFraction: number): HTMLElement {
    return mountMeter({
      value: 0, variant: 'vfo', compact: true,
      lowerScale: {
        label: 'Po', ticks: PO_TICKS, valueFraction, fault: false, relevant: true,
        accessibleDescription: 'Transmit power',
      },
    });
  }

  it('draws labels 0/25/50/75/100 with no tick marks and the same 2/1 segment pattern', () => {
    const target = mountPo(0);
    const svg = svgOf(target);
    const labels = [...svg.querySelectorAll('[data-lower-tick-label]')];
    expect(labels.map((label) => label.textContent)).toEqual(['0', '25', '50', '75', '100']);
    // mock-up v8 `.poticks`: numerals only — no tick marks are drawn under
    // the Po labels (the S-scale row above keeps its 1x4px marks).
    expect(svg.querySelector('[data-lower-tick-mark]')).toBeNull();
    const track = svg.querySelector('[data-lower-track]')!;
    expect(track.getAttribute('stroke-dasharray')).toBe('2 1');
    expect(Number(track.getAttribute('stroke-width'))).toBe(7);
    expect(track.getAttribute('stroke')).toBe('var(--v2-meter-unlit)');
    expect(svg.querySelector('[data-lower-fill]')!.getAttribute('stroke'))
      .toBe('var(--v2-meter-blue)');
  });

  it('lights whole segments of the fraction fed, unlit at 0, and names the row', () => {
    const half = mountPo(0.5);
    const track = svgOf(half).querySelector('[data-lower-track]')!;
    const fill = svgOf(half).querySelector('[data-lower-fill]')!;
    const x1 = Number(track.getAttribute('x1'));
    const trackW = Number(track.getAttribute('x2')) - x1;
    expect(Number(fill.getAttribute('x1'))).toBe(x1);
    // 0.5 of 548px is 274px: segment 91 covers [273, 275], half-covered at
    // exactly 274px, so the extent snaps to that segment's right edge, 275px.
    expect(Number(fill.getAttribute('x2')) - x1).toBeCloseTo(275, 3);
    // The row's name sits in its own fixed slot in the readout column.
    expect(svgOf(half).querySelector('[data-lower-row-label]')?.textContent).toBe('Po');
    expect(Number(svgOf(half).querySelector('[data-lower-row-label]')!.getAttribute('x')))
      .toBe(READOUT_X);

    const none = mountPo(0);
    const emptyFill = svgOf(none).querySelector('[data-lower-fill]')!;
    expect(Number(emptyFill.getAttribute('x2'))).toBeCloseTo(Number(emptyFill.getAttribute('x1')), 5);

    // A full Po reading lights the complete final dash flush with the
    // track end — the same greatest-3n+2 cap as the S track (548 at this
    // width), not the truncated trackW-1.
    const full = mountPo(1);
    const fullFill = svgOf(full).querySelector('[data-lower-fill]')!;
    expect(Number(fullFill.getAttribute('x2')) - Number(fullFill.getAttribute('x1')))
      .toBe(TRACK_W);
  });

  it('reserves the lower-row height whether or not a descriptor is present', () => {
    // The row's height is part of the face's rhythm: without the reserve,
    // the frequency row above would jump when the TX target (and with it
    // the Po row) flips MAIN↔SUB.
    const withRow = mountPo(0.5).querySelector('svg[data-variant="vfo"]')!;
    const withoutRow = mountMeter({ value: 0, variant: 'vfo', compact: true })
      .querySelector('svg[data-variant="vfo"]')!;
    expect(withoutRow.getAttribute('height')).toBe(withRow.getAttribute('height'));
    // mock-up v8 content box: 20px ticks row + 3+16 value row + 7+14 Po
    // ticks + 1+7 Po bar = 68px, reserved whole.
    expect(Number(withoutRow.getAttribute('height'))).toBe(68);
  });
});

// ── 4. MOR-2521 structure: one constant node set across a full sweep ────────

describe('MOR-2509 v7 VFO face — value updates never change the node set', () => {
  beforeEach(() => { setCapabilities(makeCaps(IC7610_LIKE_CAL)); });

  it('one node count across fill, peak and afterglow fraction steps', () => {
    const projection = projectSignalMeter(0);
    const steps = [
      { smoothedFraction: 0, peakFraction: 0.95, afterglowFraction: null },
      { smoothedFraction: 0.2, peakFraction: 0.95, afterglowFraction: 0.4 },
      { smoothedFraction: 0.55, peakFraction: 0.95, afterglowFraction: 0.6 },
      { smoothedFraction: 0.3, peakFraction: 0.7, afterglowFraction: 0.7 },
      { smoothedFraction: 0.9, peakFraction: null, afterglowFraction: 0.9 },
      { smoothedFraction: 1, peakFraction: null, afterglowFraction: 1 },
    ];
    const state = proxy({
      frame: { projection, ...steps[0] } as SignalMeterFrame,
      variant: 'vfo', compact: true,
      lowerScale: {
        label: 'Po', ticks: [{ value: 0, label: '0' }, { value: 1, label: '100' }],
        valueFraction: 0.4, fault: false, relevant: true,
      },
    });
    const target = document.createElement('div');
    document.body.appendChild(target);
    roots.push(target);
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    const component = mount(LinearSMeter as any, { target, props: state });
    components.push(component);
    flushSync();
    FakeResizeObserver.fire();
    flushSync();

    // Node identity, not just counts: a destroy-and-recreate that happens
    // to land on the same totals must fail. Hold every element reference
    // from the first render and demand the very same node after each step.
    const svg = svgOf(target);
    const identities = [...svg.querySelectorAll('*')];
    const counts = steps.map((step) => {
      state.frame = { projection, ...step } as SignalMeterFrame;
      flushSync();
      expect([...svgOf(target).querySelectorAll('*')].every(
        (element, index) => element === identities[index],
      )).toBe(true);
      const current = svgOf(target);
      return {
        total: current.querySelectorAll('*').length,
        line: current.querySelectorAll('line').length,
        text: current.querySelectorAll('text').length,
        rect: current.querySelectorAll('rect').length,
      };
    });
    for (const count of counts) expect(count).toEqual(counts[0]);
    // Literally: 7 declared scale ticks + track + 2 glow + 2 fill + peak +
    // lower track + lower fill = 15 lines; 7 scale labels + the one reading
    // line + 2 lower labels + the Po row label = 11 texts (the Po numerals
    // carry no tick marks, and the v7 dBm second line is gone); the main
    // and lower <g> wrappers; zero rects — the segmented look is dash
    // patterns, not N rects.
    expect(counts[0]).toEqual({ total: 28, line: 15, text: 11, rect: 0 });
  });
});

// ── 5. Ballistics on the v7 scale (local mode, driven clock) ────────────────

describe('MOR-2509 v7 VFO face — ballistics, peak hold and afterglow', () => {
  let frames: Map<number, FrameRequestCallback>;
  let nextFrameId: number;
  let now: number;
  let rafSpy: ReturnType<typeof vi.spyOn>;
  let originalMatchMedia: typeof window.matchMedia;

  function step(dtMilliseconds: number): void {
    now += dtMilliseconds;
    // A real requestAnimationFrame callback fires exactly once: take the
    // pending set out of the map before invoking it, so a callback that
    // reschedules itself runs on the NEXT step and the map cannot grow
    // without bound across a long script.
    const pending = [...frames.values()];
    frames.clear();
    for (const callback of pending) callback(now);
    flushSync();
  }

  beforeEach(() => {
    setCapabilities(makeCaps(IC7610_LIKE_CAL));
    frames = new Map();
    nextFrameId = 1;
    now = 0;
    rafSpy = vi.spyOn(window, 'requestAnimationFrame').mockImplementation((callback) => {
      const id = nextFrameId++;
      frames.set(id, callback);
      return id;
    });
    vi.spyOn(window, 'cancelAnimationFrame').mockImplementation((id) => { frames.delete(id); });
    vi.spyOn(performance, 'now').mockImplementation(() => now);
    originalMatchMedia = window.matchMedia;
    window.matchMedia = vi.fn().mockReturnValue({
      matches: false,
      addEventListener: vi.fn(),
      removeEventListener: vi.fn(),
    }) as unknown as typeof window.matchMedia;
  });

  afterEach(() => {
    window.matchMedia = originalMatchMedia;
  });

  it('rises without overshoot, decays after a drop, and converges', () => {
    const { target, state } = mountReactiveMeter({ value: -48, variant: 'vfo', compact: true });
    for (let i = 0; i < 30; i += 1) step(16.7);
    expect(fillFraction(svgOf(target))).toBeLessThanOrEqual(0.005);

    // Rise to S3 (1/7 of the track): at every frame the fill stays at or
    // below the reading's settled position. The flush lands the sync
    // effect before the first frame runs.
    state.value = -36;
    flushSync();
    for (let i = 0; i < 60; i += 1) {
      step(16.7);
      expect(fillFraction(svgOf(target))).toBeLessThanOrEqual(VFO_FRACTIONS.s3 + 1e-6);
    }
    expect(fillFraction(svgOf(target))).toBeCloseTo(litExtentFraction(VFO_FRACTIONS.s3), 6);

    // Drop back to S1: the displayed level decays monotonically.
    state.value = -48;
    flushSync();
    let previous = fillFraction(svgOf(target));
    for (let i = 0; i < 30; i += 1) {
      step(16.7);
      const current = fillFraction(svgOf(target));
      expect(current).toBeLessThanOrEqual(previous + 1e-9);
      previous = current;
    }
    // …and converges to the new reading (release τ ≈ 300 ms).
    for (let i = 0; i < 30; i += 1) step(16.7);
    expect(fillFraction(svgOf(target))).toBeLessThanOrEqual(0.01);
  });

  it('keeps the peak at or above the displayed level through a full script', () => {
    const { target, state } = mountReactiveMeter({ value: -48, variant: 'vfo', compact: true });
    const script: { value: number; frames: number }[] = [
      { value: -36, frames: 40 },   // rise
      { value: -36, frames: 20 },   // hold
      { value: -48, frames: 40 },   // fall
      { value: 20, frames: 60 },    // second rise that overtakes the decaying peak
      { value: -48, frames: 60 },   // final fall
    ];
    for (const phase of script) {
      state.value = phase.value;
      flushSync();
      for (let i = 0; i < phase.frames; i += 1) {
        step(16.7);
        expect(peakFraction(svgOf(target))).toBeGreaterThanOrEqual(
          fillFraction(svgOf(target)) - 1e-6,
        );
      }
    }
  });

  it('holds the peak for ~1 s after the drop, then lets it fall', () => {
    const { target, state } = mountReactiveMeter({ value: -48, variant: 'vfo', compact: true });
    state.value = 20;
    flushSync();
    for (let i = 0; i < 60; i += 1) step(16.7);
    const highPeak = peakFraction(svgOf(target));
    expect(highPeak).toBeGreaterThan(VFO_FRACTIONS.s9);

    state.value = -48;
    flushSync();
    for (let i = 0; i < 50; i += 1) step(16.7); // ~0.84 s, inside the hold window
    expect(peakFraction(svgOf(target))).toBeCloseTo(highPeak, 3);
    for (let i = 0; i < 30; i += 1) step(16.7); // ~1.3 s: past the hold
    expect(peakFraction(svgOf(target))).toBeLessThan(highPeak - 0.05);
  });

  it('afterglow trails the falling bar and fades back onto it', () => {
    const { target, state } = mountReactiveMeter({ value: -48, variant: 'vfo', compact: true });
    state.value = 20;
    flushSync();
    for (let i = 0; i < 60; i += 1) step(16.7);
    state.value = -48;
    flushSync();
    let sawTrailingGlow = false;
    for (let i = 0; i < 20; i += 1) {
      step(16.7);
      const glow = glowFraction(svgOf(target));
      const fill = fillFraction(svgOf(target));
      expect(glow).toBeGreaterThanOrEqual(fill - 1e-6);
      if (glow > fill + 0.02) sawTrailingGlow = true;
    }
    expect(sawTrailingGlow).toBe(true);
    // The trail is the bar one fade window back, so once the bar has
    // settled the glow has collapsed onto it.
    for (let i = 0; i < 90; i += 1) step(16.7);
    expect(glowFraction(svgOf(target)) - fillFraction(svgOf(target))).toBeLessThanOrEqual(0.02);
  });

  it('keeps one node count across the whole animated script', () => {
    const { target, state } = mountReactiveMeter({ value: -48, variant: 'vfo', compact: true });
    const counts: number[] = [];
    for (const value of [-36, 20, -48, 20, -48]) {
      state.value = value;
      flushSync();
      for (let i = 0; i < 30; i += 1) {
        step(16.7);
        counts.push(svgOf(target).querySelectorAll('*').length);
      }
    }
    expect(new Set(counts).size).toBe(1);
  });
});
