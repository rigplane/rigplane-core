import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { mount, unmount, flushSync } from 'svelte';
import type { ComponentProps } from 'svelte';
import type { Capabilities } from '$lib/types/capabilities';
import { clearCapabilities, setCapabilities } from '$lib/stores/capabilities.svelte';
import LinearSMeter from '../LinearSMeter.svelte';
import { projectSignalMeter } from '../smeter-scale';
import { readFileSync } from 'node:fs';

// MOR-2509 R2-2: every number below is re-measured from the owner's mock-up
// `/tmp/vfo-deck-final-mockup-v8.html` (kept in the worktree as
// `tmp/vfo-deck-final-mockup-v8.html`, 2026-09-22) — its `.meter` block, CSS
// lines 64–96 and 120–122, markup lines 187–193. Each pin says which change
// would turn it red; a pin that cannot fail is not a pin.

// The FTX-1 profile's own `[meters.s_meter]` table (rigs/ftx1.toml, verbatim
// raw/actual/label) — the radio the owner measured the mock-up against.
const FTX1_CAL = [
  { raw: 0, actual: -54, label: 'S0' },
  { raw: 13, actual: -51, label: 'S0.5' },
  { raw: 26, actual: -48, label: 'S1' },
  { raw: 39, actual: -45, label: 'S2' },
  { raw: 52, actual: -42, label: 'S3' },
  { raw: 65, actual: -39, label: 'S4' },
  { raw: 78, actual: -36, label: 'S5' },
  { raw: 91, actual: -33, label: 'S6' },
  { raw: 103, actual: -18, label: 'S7' },
  { raw: 117, actual: -9, label: 'S8' },
  { raw: 130, actual: 0, label: 'S9' },
  { raw: 165, actual: 10, label: 'S9+10' },
  { raw: 200, actual: 20, label: 'S9+20' },
  { raw: 240, actual: 40, label: 'S9+40' },
];
// A dense + ladder: every knot from +10 to +60 declared. The collision rule
// must thin it to the mock-up's own +20/+40/+60 set.
const DENSE_PLUS_CAL = [
  { raw: 0, actual: -54, label: 'S0' },
  { raw: 120, actual: 0, label: 'S9' },
  { raw: 144, actual: 10, label: 'S9+10' },
  { raw: 168, actual: 20, label: 'S9+20' },
  { raw: 192, actual: 30, label: 'S9+30' },
  { raw: 216, actual: 40, label: 'S9+40' },
  { raw: 240, actual: 50, label: 'S9+50' },
  { raw: 255, actual: 60, label: 'S9+60' },
];

function makeCaps(cal: typeof FTX1_CAL): Capabilities {
  return {
    model: 'FTX-1',
    scope: true,
    audio: true,
    tx: true,
    capabilities: ['scope', 'tx'],
    receivers: 2,
    vfoScheme: 'main_sub',
    freqRanges: [{ start: 1800000, end: 30000000, label: 'HF' }],
    modes: ['USB', 'LSB', 'CW', 'AM', 'FM'],
    filters: ['FIL1'],
    audioConfig: { sampleRate: 48000, channels: 1, codecs: ['opus'] },
    webrtc: { available: true, enabled: false },
    txBands: null,
    stateContractVersion: 1,
    providerGeneration: 0,
    meterCalibrations: { s_meter: cal },
  };
}

const FIXTURE_WIDTH = 606;
// The mock-up's flex model, verbatim: the track is the exact width minus
// the 58px value column (10px gap + 48px cell) — 548px here, NOT floored
// to the dash pitch. The reading cell's left edge (and the Po label's) is
// the width minus the 48px cell = 558. The S9 numeral sits at the exact
// 4/7 share of 548 = 313.142857px; the blue→red fill split is the one
// number on the dash RASTER, snapped to the whole-dash grid at 312px.
const TRACK_W = FIXTURE_WIDTH - 58;
const READOUT_X = FIXTURE_WIDTH - 48;
const S9_LABEL_X = (4 / 7) * TRACK_W;
const S9_RASTER_X = 312;

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

function stubReducedMotion(): void {
  vi.stubGlobal('matchMedia', (query: string): MediaQueryList => ({
    matches: query === '(prefers-reduced-motion: reduce)', media: query, onchange: null,
    addEventListener: vi.fn(), removeEventListener: vi.fn(),
    addListener: vi.fn(), removeListener: vi.fn(), dispatchEvent: vi.fn(() => false),
  }) as unknown as MediaQueryList);
}

function mountMeter(props: ComponentProps<typeof LinearSMeter>): SVGSVGElement {
  const target = document.createElement('div');
  document.body.appendChild(target);
  roots.push(target);
  const component = mount(LinearSMeter, { target, props });
  flushSync();
  FakeResizeObserver.fire();
  flushSync();
  components.push(component);
  return target.querySelector('svg[data-variant="vfo"]')!;
}

const PO_TICKS = [
  { value: 0, label: '0' },
  { value: 0.25, label: '25' },
  { value: 0.5, label: '50' },
  { value: 0.75, label: '75' },
  { value: 1, label: '100' },
];

function withPo(value: number | null = -18): ComponentProps<typeof LinearSMeter> {
  return {
    value, variant: 'vfo', compact: true,
    lowerScale: {
      label: 'Po', ticks: PO_TICKS, valueFraction: 0.4, fault: false, relevant: true,
      accessibleDescription: 'Transmit power',
    },
  };
}

// ── 1. The mock-up's own geometry, read off the rendered DOM ────────────────

describe('MOR-2509 R2-2 — mock-up v8 S-meter geometry', () => {
  beforeEach(() => { stubReducedMotion(); setCapabilities(makeCaps(FTX1_CAL)); });

  it('reserves the mock-up row stack: 68px of content, ticks row on top', () => {
    const svg = mountMeter(withPo());
    // 20px ticks + 3+16 value row + 7+14 Po ticks + 1+7 Po bar = 68; red if
    // any row height or inter-row margin drifts from the mock-up's box model.
    expect(Number(svg.getAttribute('height'))).toBe(68);
    // The tick marks sit in the LAST 5px of the 20px ticks row (top 15px,
    // height 4px) — a v7-era y would put them at 11..15.
    const tick = svg.querySelector('[data-scale-tick="1"]')!;
    expect(Number(tick.getAttribute('y1'))).toBe(15);
    expect(Number(tick.getAttribute('y2'))).toBe(19);
    expect(Number(tick.getAttribute('stroke-width'))).toBe(1);
    // The mock-up's ::after tick is currentColor at full opacity; the v7
    // face dimmed its ticks to 0.7.
    expect(tick.getAttribute('opacity')).toBeNull();
  });

  it('draws 12px weight-400 numerals: first left-anchored, the rest centred', () => {
    const svg = mountMeter(withPo());
    const labels = [...svg.querySelectorAll('[data-scale-label]')];
    // Red if the label font size/weight leave the mock-up's 12px/400, or if
    // the first numeral stops being left-anchored at its slot.
    for (const label of labels) {
      expect(Number(label.getAttribute('font-size'))).toBe(12);
      expect(Number(label.getAttribute('font-weight'))).toBe(400);
      expect(Number(label.getAttribute('y'))).toBe(0);
    }
    expect(labels[0]!.getAttribute('text-anchor')).toBe('start');
    expect(Number(labels[0]!.getAttribute('x'))).toBe(0);
    expect(labels[1]!.getAttribute('text-anchor')).toBe('middle');
    expect(Number(labels[1]!.getAttribute('x'))).toBeCloseTo(TRACK_W / 7, 5);
  });

  it('tightens only the + numerals by the mock-up letter-spacing and keeps one red', () => {
    const svg = mountMeter(withPo());
    const labels = [...svg.querySelectorAll('[data-scale-label]')];
    const plus = labels.find((label) => label.textContent === '+20')!;
    // -.05em at 12px = -0.6px; red if + numerals lose the tightening or the
    // odd-unit numerals gain it.
    expect(plus.getAttribute('letter-spacing')).toBe('-0.6');
    expect(plus.getAttribute('fill')).toBe('var(--v2-meter-red)');
    const odd = labels.find((label) => label.textContent === '3')!;
    expect(odd.getAttribute('letter-spacing')).toBeNull();
    expect(odd.getAttribute('fill')).toBe('var(--dl-vfo-meter-tick-label, #e6edf4)');
  });

  it('gives the track 12px of height in the 16px value row, exact flex remainder', () => {
    const svg = mountMeter(withPo());
    const track = svg.querySelector('[data-meter-track]')!;
    expect(Number(track.getAttribute('stroke-width'))).toBe(12);
    // Value row starts 3px under the 20px ticks row and is 16px tall, so the
    // 12px track centres at y = 20 + 3 + 8 = 31. Red if the row rhythm drifts.
    expect(Number(track.getAttribute('y1'))).toBe(31);
    expect(Number(track.getAttribute('x1'))).toBe(0);
    // The track is the flex:1 remainder after the 58px column — the exact
    // width minus 58, no dash-pitch floor. Red if the v7 floor returns.
    expect(Number(track.getAttribute('x2'))).toBe(FIXTURE_WIDTH - 58);
    expect(track.getAttribute('stroke-dasharray')).toBe('2 1');
    expect(track.getAttribute('stroke')).toBe('var(--v2-meter-unlit)');
    // The reading cell's left edge is the width minus the 48px CELL (not
    // the floored fixture): a 49px or 50px cell moves it to 557/556 and
    // this pin goes red. The 10px gap is the cell edge minus the track end.
    const reading = svg.querySelector('[data-meter-reading]')!;
    expect(Number(reading.getAttribute('x'))).toBe(FIXTURE_WIDTH - 48);
    expect(Number(reading.getAttribute('x')) - Number(track.getAttribute('x2'))).toBe(10);
    expect(Number(reading.getAttribute('y'))).toBe(31);
    expect(Number(reading.getAttribute('font-size'))).toBe(13);
    expect(Number(reading.getAttribute('font-weight'))).toBe(400);
    expect(reading.getAttribute('fill')).toBe('var(--dl-vfo-meter-value, #f4f8fc)');
    expect(svg.textContent).toContain('S7');
    // The mock-up has no dBm line anywhere in the meter.
    expect(svg.textContent).not.toMatch(/dBm/);
    expect(svg.querySelectorAll('[data-meter-reading]')).toHaveLength(1);
  });

  it('splits lit colour at the S9 share, numeral exact and fill on the raster', () => {
    const svg = mountMeter(withPo());
    // The S9 numeral is LAYOUT: the exact 4/7 share of the 548px track,
    // 313.142857px. Red if the share moves or inherits a raster floor.
    const s9 = [...svg.querySelectorAll('[data-scale-label]')]
      .find((label) => label.textContent === '9')!;
    expect(Number(s9.getAttribute('x'))).toBeCloseTo(S9_LABEL_X, 5);
    // The blue→red fill split is the RASTER: snapped to the whole-dash grid
    // (104 × 3 = 312) so no dash renders half blue and half red — within
    // one pitch of the numeral's exact share.
    expect(Number(svg.querySelector('[data-meter-fill-red]')!.getAttribute('x1')))
      .toBe(S9_RASTER_X);
  });

  it('peaks with a constant 2px light marker and .38 afterglow, in each zone', () => {
    // Two ARMED frames with the marker on OPPOSITE sides of the S9 raster
    // split (312px). Why the previous pair was red on the build host: the
    // marker arms only while peak − smoothed > 0.3 on the CALIBRATED axis —
    // the +20 reading's min(1, mf+0.4) peak clamped to 1 against mf 0.836,
    // a delta of 0.164, so the marker stayed hidden; and the −36 reading's
    // +0.4 peak lerped through the knots to ≈356px, already past the 312px
    // split, so even the 'below' marker sat over S9. The frames below keep
    // a 0.4 delta under the clamp: −48 (S1, mf 0.061, peak 0.461) places
    // the marker at 257px; S9 (mf 0.55, peak 0.95) places it at 446px.
    const frameAt = (value: number, peakFraction: number) => {
      const projection = projectSignalMeter(value);
      return {
        projection,
        smoothedFraction: projection.motionFraction!,
        peakFraction,
        afterglowFraction: null,
        reducedMotion: false,
      };
    };
    const marker = (svg: SVGSVGElement) => svg.querySelector('[data-meter-peak]')!;
    const below = mountMeter({ frame: frameAt(-48, 0.4611), variant: 'vfo', compact: true });
    expect(marker(below).getAttribute('visibility')).toBe('visible');
    expect(Number(marker(below).getAttribute('x1'))).toBeLessThan(S9_RASTER_X);
    const past = mountMeter({ frame: frameAt(0, 0.95), variant: 'vfo', compact: true });
    expect(marker(past).getAttribute('visibility')).toBe('visible');
    expect(Number(marker(past).getAttribute('x1'))).toBeGreaterThan(S9_RASTER_X);
    // One constant light tone at every zone — the v7 cyan/red zone colours
    // would turn either marker's stroke red and go red here.
    for (const svg of [below, past]) {
      expect(marker(svg).getAttribute('stroke')).toBe('var(--dl-vfo-meter-peak, #e8f1ff)');
      expect(Number(marker(svg).getAttribute('stroke-width'))).toBe(2);
      expect(marker(svg).getAttribute('opacity')).toBeNull();
    }
    // .after { opacity: .38 } — red if the v7 0.35 afterglow returns.
    const settled = mountMeter(withPo());
    expect(settled.querySelector('[data-meter-glow]')!.getAttribute('stroke-opacity')).toBe('0.38');
    expect(settled.querySelector('[data-meter-glow-red]')!.getAttribute('stroke-opacity')).toBe('0.38');
  });

  it('draws the Po stack: 12px numerals at y 46, 7px bar at y 61, Po label in the column', () => {
    const svg = mountMeter(withPo());
    const poLabels = [...svg.querySelectorAll('[data-lower-tick-label]')];
    // .poticks: margin 7px 58px 1px 0 — numerals start 46px down (7px under
    // the 39px value row). Red if the Po rows drift up or down.
    expect(poLabels.map((label) => label.textContent)).toEqual(['0', '25', '50', '75', '100']);
    for (const label of poLabels) {
      expect(Number(label.getAttribute('y'))).toBe(46);
      expect(Number(label.getAttribute('font-size'))).toBe(12);
      expect(label.getAttribute('fill')).toBe('var(--dl-vfo-meter-po-label, #c3ced9)');
    }
    // The mock-up draws NO tick marks under the Po numerals.
    expect(svg.querySelector('[data-lower-tick-mark]')).toBeNull();
    // .po: 7px tall, 1px under the 14px label row → centre at 61 + 3.5.
    const poTrack = svg.querySelector('[data-lower-track]')!;
    expect(Number(poTrack.getAttribute('stroke-width'))).toBe(7);
    expect(Number(poTrack.getAttribute('y1'))).toBe(64.5);
    expect(poTrack.getAttribute('stroke-dasharray')).toBe('2 1');
    expect(poTrack.getAttribute('stroke')).toBe('var(--v2-meter-unlit)');
    // .polab: the 48px value column, bottom-anchored 2px past the content
    // box (bottom:5px of the 79px well). Red if the label leaves the value
    // column or the v7 7px/38px placement returns.
    const rowLabel = svg.querySelector('[data-lower-row-label]')!;
    expect(rowLabel.textContent).toBe('Po');
    expect(Number(rowLabel.getAttribute('x'))).toBe(READOUT_X);
    expect(Number(rowLabel.getAttribute('y'))).toBe(70);
    expect(Number(rowLabel.getAttribute('font-size'))).toBe(12);
  });

  it('keeps the accessible name to the S-unit — no dBm, no placeholder', () => {
    const svg = mountMeter(withPo());
    expect(svg.getAttribute('aria-label')).toBe('S meter S7');
    expect(svg.getAttribute('aria-label')).not.toMatch(/dBm/);
    const unknown = mountMeter(withPo(null));
    expect(unknown.getAttribute('aria-label')).toBe('S meter reading unknown');
    expect(unknown.textContent).not.toContain('?');
  });

  it('never changes the SVG size with the reading', () => {
    // "Ничего не меняет размер и не сдвигается": two readings at the bottom
    // and top of the scale render the identical root box — same width, same
    // fixed height, no viewBox that could re-scale geometry under a value.
    // Red if any value-driven path resizes the face.
    const bottom = mountMeter(withPo(-54));
    const top = mountMeter(withPo(40));
    for (const svg of [bottom, top]) {
      expect(svg.getAttribute('width')).toBe('100%');
      expect(svg.getAttribute('height')).toBe('68');
      expect(svg.getAttribute('viewBox')).toBeNull();
    }
    expect(top.getAttribute('height')).toBe(bottom.getAttribute('height'));
    expect(top.getAttribute('width')).toBe(bottom.getAttribute('width'));
  });
});

// ── 2. The numeral rule: odd S-units + declared + knots, collision-thinned ──

describe('MOR-2509 R2-2 — the mock-up v8 numeral rule', () => {
  beforeEach(() => { stubReducedMotion(); setCapabilities(makeCaps(FTX1_CAL)); });

  it('FTX-1 draws 1 3 5 7 9 +20 +40 — the declared +10 collides and is dropped', () => {
    const svg = mountMeter(withPo());
    const labels = [...svg.querySelectorAll('[data-scale-label]')];
    // The FTX-1 table declares EVERY S-unit plus +10/+20/+40. The mock-up
    // ladder is the odd units and the non-colliding + knots: +10 sits 1/14
    // of the track from the S9 numeral (11.43px at the 160px minimum track,
    // under a "+NN" numeral's 21.6px of ink), so only +20/+40 draw. Red if
    // even units, +10, or an invented +60 appear.
    expect(labels.map((label) => label.textContent))
      .toEqual(['1', '3', '5', '7', '9', '+20', '+40']);
    const slots = [0, 1 / 7, 2 / 7, 3 / 7, 4 / 7, 5 / 7, 6 / 7];
    labels.forEach((label, index) => {
      expect(Number(label.getAttribute('x'))).toBeCloseTo(slots[index] * TRACK_W, 5);
    });
  });

  it('a dense +10..+60 ladder thins to the mock-up +20/+40/+60 set', () => {
    setCapabilities(makeCaps(DENSE_PLUS_CAL));
    const svg = mountMeter(withPo());
    const labels = [...svg.querySelectorAll('[data-scale-label]')];
    // Only S9 and every +10 knot declared: the rule keeps one numeral per
    // mock-up rung. Red if the thinning stops dropping intermediate knots.
    expect(labels.map((label) => label.textContent))
      .toEqual(['9', '+20', '+40', '+60']);
    // Slot-zero anchoring, not first-index: this table's first DRAWN numeral
    // is '9' at slot 4/7 and still centres — only a numeral at share 0
    // (FTX-1's '1' above) left-anchors.
    expect(labels[0]!.getAttribute('text-anchor')).toBe('middle');
  });
});

// ── 3. Source pins: the well and the language tokens carry the mock-up ──────

describe('MOR-2509 R2-2 — mock-up v8 source pins', () => {
  const componentSource = readFileSync('src/components-v2/meters/LinearSMeter.svelte', 'utf8');
  const panelSource = readFileSync('src/components-v2/vfo/VfoPanel.svelte', 'utf8');
  const studiolineSource
    = readFileSync('src/presentation/languages/studioline/studioline.css', 'utf8');

  it('the well is the mock-up .meter: padding 4/8/7, radius 6, unclipped SVG', () => {
    // Red if the well padding/radius drift from `padding: 4px 8px 7px;
    // border-radius: 6px` (the horizontal pad stays routable through the
    // layout token, now with the mock-up's 8px fallback).
    expect(panelSource).toMatch(
      /padding: 4px var\(--vfo-panel-meter-pad-x, 8px\) 7px;/,
    );
    expect(panelSource).toMatch(/border-radius: 6px;/);
    // The Po label hangs 2px past the content box; the root SVG must not
    // clip it.
    expect(componentSource).toMatch(
      /svg\[data-variant='vfo'\][\s\S]*?overflow: visible;/,
    );
  });

  it('studioline owns the mock-up ink values', () => {
    // Red if studioline stops carrying the mock-up's four ink tones.
    expect(studiolineSource).toMatch(/--dl-vfo-meter-tick-label:\s*#e6edf4;/);
    expect(studiolineSource).toMatch(/--dl-vfo-meter-value:\s*#f4f8fc;/);
    expect(studiolineSource).toMatch(/--dl-vfo-meter-peak:\s*#e8f1ff;/);
    expect(studiolineSource).toMatch(/--dl-vfo-meter-po-label:\s*#c3ced9;/);
  });

  it('carries no keyframes and filters no Po fill', () => {
    // The v7 rules stay: no CSS animation may sneak the bar into motion,
    // and the lit filter belongs to the S track's `.lit` alone — the
    // mock-up leaves `.polit` the plain meter blue.
    expect(componentSource).not.toMatch(/@keyframes/);
    const style = componentSource.match(/<style>([\s\S]*)<\/style>/)?.[1] ?? '';
    expect(style).not.toMatch(/\[data-lower-fill\][^{]*\{[^}]*filter:/);
    expect(style).toMatch(
      /\[data-meter-fill\],[^{]*\n?\s*\[data-meter-fill-red\][^{]*\{[^}]*filter:\s*var\(--v2-meter-lit-filter,\s*none\)/,
    );
  });
});
