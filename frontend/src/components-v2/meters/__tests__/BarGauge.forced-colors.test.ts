import { afterEach, beforeEach, describe, expect, it } from 'vitest';
import { flushSync, mount, unmount } from 'svelte';
import { readFileSync } from 'node:fs';
import BarGauge from '../BarGauge.svelte';
import type { BarMeterFrame } from '../bar-meter-motion.svelte';

// MOR-1250: forced-colors (Windows High Contrast Mode) does not force SVG
// presentation attributes, so BarGauge's own palette — DEFAULT_ZONES raw
// hexes, dimColor() blends, and the v2 token fills — rendered unchanged
// under it. Same mechanism as LinearSMeter.forced-colors.test.ts / #3717:
// a component-scoped forced-colors block re-paints every face onto system
// colours. Author CSS overrides presentation attributes in the cascade, so
// those paints are what a WHCM user actually sees.

const SOURCE = readFileSync('src/components-v2/meters/BarGauge.svelte', 'utf8');
const STYLE = SOURCE.match(/<style>([\s\S]*)<\/style>/)?.[1] ?? '';

// The forced-colors block verbatim, and its body with the media wrapper
// removed. Both come from the same extraction, so the cascade assertions
// below cannot drift from the shipped rules.
const FORCED_MATCH = STYLE.match(/@media \(forced-colors: active\) \{([\s\S]*)\}\s*$/);
const FORCED_BODY = FORCED_MATCH?.[1] ?? '';

// ── Source pins: the palette and its media condition ───────────────────────

describe('MOR-1250 — BarGauge forced-colors palette (source pins)', () => {
  it('defines the system-colour palette inside @media (forced-colors: active)', () => {
    expect(FORCED_MATCH).not.toBeNull();
    // The gauge opts out of engine-dependent forcing and paints exactly
    // these system colours itself.
    expect(FORCED_BODY).toMatch(/forced-color-adjust:\s*none/);
    // Ink — label and display value — follows the system text colour.
    expect(FORCED_BODY).toMatch(/text\s*\{\s*fill:\s*CanvasText/);
    // Lit state reads Highlight, unlit structure GrayText.
    expect(FORCED_BODY).toMatch(/rect\[data-gauge-fill\][^}]*fill:\s*Highlight/);
    expect(FORCED_BODY).toMatch(/rect\[data-gauge-dim\][^}]*fill:\s*GrayText/);
  });

  it('carries the dim/lit split as attributes, not colour alone', () => {
    // Under forced-colors the palette is overridden, so each segment's
    // dim/lit role must also live in the DOM.
    expect(SOURCE).toMatch(/data-gauge-dim/);
    expect(SOURCE).toMatch(/data-gauge-fill=\{i\}/);
  });
});

// ── Computed-style evidence (F4-pattern injection) ──────────────────────────

describe('MOR-1250 — BarGauge computed-style evidence (F4 injection)', () => {
  // jsdom applies a stylesheet's @media rules to computed style only when
  // the media list contains the literal item "screen", so
  // `(forced-colors: active)` never evaluates here. The media condition is
  // pinned by the source assertions above; what this block proves with a
  // genuine cascade is the other half: the very rules inside that block
  // match the real mounted SVG nodes and compute to the system colours.
  let styleEl: HTMLStyleElement;
  let component: ReturnType<typeof mount> | undefined;
  let target: HTMLDivElement;

  beforeEach(() => {
    styleEl = document.createElement('style');
    styleEl.textContent = FORCED_BODY;
    document.head.appendChild(styleEl);
  });
  afterEach(() => {
    styleEl.remove();
    if (component) unmount(component);
    target?.remove();
    component = undefined;
  });

  // Hosted-frame mode is fully deterministic: no motion loop, a fixed
  // smoothedFraction lights a known prefix of the ten fill rects.
  const FRAME: BarMeterFrame = { smoothedFraction: 0.45, peakFraction: 0.8 };

  function render(props: Record<string, unknown>): HTMLElement {
    target = document.createElement('div');
    document.body.appendChild(target);
    component = mount(BarGauge, {
      target,
      props: { frame: FRAME, label: 'Po', displayValue: '45W', onResetPeak: () => {}, ...props } as never,
    });
    flushSync();
    return target;
  }

  // cssstyle normalises stored property values to lower case, so every
  // computed colour keyword reads back lower-cased ('highlight').
  const cs = (el: Element, prop: string): string =>
    getComputedStyle(el).getPropertyValue(prop).toLowerCase();

  it('lit fills Highlight, dim GrayText, ink CanvasText, faces Canvas', () => {
    const root = render({});
    const svg = root.querySelector('svg')!;
    expect(cs(svg, 'forced-color-adjust')).toBe('none');

    // 0.45 * 10 = 4.5 → segs 0–3 full, seg 4 partial; both states present.
    const fills = [...root.querySelectorAll<SVGRectElement>('[data-gauge-fill]')];
    expect(fills).toHaveLength(10);
    const lit = fills.filter((rect) => rect.getAttribute('visibility') !== 'hidden');
    expect(lit.length).toBeGreaterThan(0);
    // Cascade override: the raw zone hex is still in the presentation
    // attribute, but the forced-colors CSS paint wins.
    expect(lit[0].getAttribute('fill')).toMatch(/^#/);
    expect(cs(lit[0], 'fill')).toBe('highlight');

    const dims = [...root.querySelectorAll<SVGRectElement>('[data-gauge-dim]')];
    expect(dims).toHaveLength(10);
    expect(cs(dims[0], 'fill')).toBe('graytext');
    // A dim rect under a lit fill is covered at runtime but still paints
    // GrayText — the same unlit structure colour as the exposed ones.
    expect(cs(dims[9], 'fill')).toBe('graytext');

    expect(cs(root.querySelector('svg text')!, 'fill')).toBe('canvastext');
    // Container and track: canvas faces with a text-colour outline.
    const container = svg.querySelector('rect')!;
    expect(cs(container, 'fill')).toBe('canvas');
    expect(cs(container, 'stroke')).toBe('canvastext');
    expect(cs(root.querySelector('[data-gauge-track]')!, 'fill')).toBe('canvas');
    expect(cs(root.querySelector('[data-gauge-track]')!, 'stroke')).toBe('canvastext');
  });

  it('peak-hold marker reads Highlight, never the yellow token', () => {
    const root = render({});
    const peak = root.querySelector<SVGRectElement>('[data-testid="bar-gauge-peak-marker"]')!;
    expect(peak).toBeDefined();
    expect(peak.getAttribute('fill')).toMatch(/accent-yellow|#f2cf4a/i);
    expect(cs(peak, 'fill')).toBe('highlight');
  });
});
