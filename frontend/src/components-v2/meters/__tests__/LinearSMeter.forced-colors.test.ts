import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { flushSync, mount, unmount } from 'svelte';
import { readFileSync } from 'node:fs';
import { clearCapabilities, setCapabilities } from '$lib/stores/capabilities.svelte';
import { mockCapabilities } from '../../../../tests/e2e/i18n/fixtures';
import LinearSMeter from '../LinearSMeter.svelte';

// MOR-1250: forced-colors (Windows High Contrast Mode) does not force SVG
// presentation attributes, so the S-meter's own palette — the 20-entry
// ACTIVE_COLORS list (three of the entries are CSS custom-property
// tokens), the dim/lower hex literals and sdrColor — rendered
// unchanged under it (the MOR-1233 verify probe). This file pins the fix:
// a component-scoped forced-colors block re-paints every face onto system
// colours. Author CSS overrides presentation attributes in the cascade, so
// those paints are what a WHCM user actually sees.

const SOURCE = readFileSync('src/components-v2/meters/LinearSMeter.svelte', 'utf8');
const STYLE = SOURCE.match(/<style>([\s\S]*)<\/style>/)?.[1] ?? '';

// The forced-colors block verbatim, and its body with the media wrapper
// removed. Both come from the same extraction, so the cascade assertions
// below cannot drift from the shipped rules.
const FORCED_MATCH = STYLE.match(/@media \(forced-colors: active\) \{([\s\S]*)\}\s*$/);
const FORCED_BODY = FORCED_MATCH?.[1] ?? '';

// ── Source pins: the palette and its media condition ───────────────────────

describe('MOR-1250 — forced-colors palette (source pins)', () => {
  it('defines the system-colour palette inside @media (forced-colors: active)', () => {
    expect(FORCED_MATCH).not.toBeNull();
    // The meter opts out of engine-dependent forcing and paints exactly
    // these system colours itself.
    expect(FORCED_BODY).toMatch(/forced-color-adjust:\s*none/);
    // Ink — text and tick marks — follows the system text colour.
    expect(FORCED_BODY).toMatch(/text\s*\{\s*fill:\s*CanvasText/);
    expect(FORCED_BODY).toMatch(/line\s*\{\s*stroke:\s*CanvasText/);
    // Lit state reads Highlight, unlit structure GrayText — every face.
    expect(FORCED_BODY).toMatch(/rect\[data-meter-fill\][^}]*fill:\s*Highlight/);
    expect(FORCED_BODY).toMatch(/rect\[data-sdr-segment\]\[data-lit='true'\][^}]*fill:\s*Highlight/);
    expect(FORCED_BODY).toMatch(/line\[data-meter-track\][^}]*stroke:\s*GrayText/);
  });

  it('carries the SDR lit state as an attribute, not colour alone', () => {
    // Under forced-colors the palette is overridden, so the SDR face's
    // lit/unlit split — colour-only in sdrColor — must also live in the DOM.
    expect(SOURCE).toMatch(/data-sdr-segment=\{index\}[\s\S]{0,120}data-lit=/);
  });
});

// ── Computed-style evidence (F4-pattern injection) ──────────────────────────

describe('MOR-1250 — computed-style evidence (F4 injection)', () => {
  // jsdom applies a stylesheet's @media rules to computed style only when
  // the media list contains the literal item "screen", so
  // `(forced-colors: active)` never evaluates here. The media condition is
  // pinned by the source assertions above; what this block proves with a
  // genuine cascade is the other half: the very rules inside that block
  // match the real mounted SVG nodes and compute to the system colours.
  // In a browser, author CSS overrides presentation attributes, so under
  // WHCM these are the paints that win.
  let styleEl: HTMLStyleElement;
  let component: ReturnType<typeof mount> | undefined;
  let target: HTMLDivElement;

  // jsdom has no ResizeObserver and every layout box is 0, so the vfo
  // face's track width would be 0 and its fill line never lights — the
  // same harness the vfo-face tests use (a fake observer plus a
  // clientWidth override).
  const FIXTURE_WIDTH = 606;
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

  beforeEach(() => {
    styleEl = document.createElement('style');
    styleEl.textContent = FORCED_BODY;
    document.head.appendChild(styleEl);
    originalResizeObserver = globalThis.ResizeObserver;
    globalThis.ResizeObserver = FakeResizeObserver as unknown as typeof ResizeObserver;
    originalClientWidth = Object.getOwnPropertyDescriptor(Element.prototype, 'clientWidth');
    Object.defineProperty(Element.prototype, 'clientWidth', {
      configurable: true,
      get: () => FIXTURE_WIDTH,
    });
    setCapabilities({
      ...mockCapabilities,
      meterCalibrations: {
        s_meter: [
          { raw: 0, actual: -54, label: 'S0' },
          { raw: 26, actual: -48, label: 'S1' },
          { raw: 52, actual: -36, label: 'S3' },
          { raw: 78, actual: -24, label: 'S5' },
          { raw: 103, actual: -12, label: 'S7' },
          { raw: 130, actual: 0, label: 'S9' },
          { raw: 165, actual: 10, label: 'S9+10' },
          { raw: 200, actual: 20, label: 'S9+20' },
          { raw: 240, actual: 40, label: 'S9+40' },
        ],
      },
    });
    // Settled-geometry mount (the display.test.ts determinism trick):
    // under prefers-reduced-motion the local motion snaps to the reading
    // at once, so the lit segments are deterministic.
    vi.stubGlobal('matchMedia', (query: string): MediaQueryList => ({
      matches: query === '(prefers-reduced-motion: reduce)',
      media: query,
      onchange: null,
      addEventListener: vi.fn(),
      removeEventListener: vi.fn(),
      addListener: vi.fn(),
      removeListener: vi.fn(),
      dispatchEvent: vi.fn(() => false),
    }) as unknown as MediaQueryList);
  });
  afterEach(() => {
    styleEl.remove();
    if (component) unmount(component);
    target?.remove();
    component = undefined;
    globalThis.ResizeObserver = originalResizeObserver as typeof ResizeObserver;
    if (originalClientWidth) {
      Object.defineProperty(Element.prototype, 'clientWidth', originalClientWidth);
    }
    vi.unstubAllGlobals();
    clearCapabilities();
  });

  function render(props: Record<string, unknown>): HTMLElement {
    target = document.createElement('div');
    document.body.appendChild(target);
    component = mount(LinearSMeter, { target, props: props as never });
    flushSync();
    FakeResizeObserver.fire();
    flushSync();
    return target;
  }

  // cssstyle normalises stored property values to lower case, so every
  // computed colour keyword reads back lower-cased ('highlight').
  const cs = (el: Element, prop: string): string =>
    getComputedStyle(el).getPropertyValue(prop).toLowerCase();

  it('default face: lit segments Highlight, dim GrayText, ink CanvasText', () => {
    const root = render({ value: 20 });
    const svg = root.querySelector('svg')!;
    expect(cs(svg, 'forced-color-adjust')).toBe('none');
    const lit = [...root.querySelectorAll<SVGRectElement>('[data-meter-fill]')]
      .find((rect) => rect.getAttribute('visibility') !== 'hidden')!;
    expect(lit).toBeDefined();
    // Cascade override: the raw palette is still in the presentation
    // attribute, but the forced-colors CSS paint wins.
    expect(lit.getAttribute('fill')).toMatch(/^#|^var\(/);
    expect(cs(lit, 'fill')).toBe('highlight');
    expect(cs(root.querySelector('[data-segment="0"]')!, 'fill')).toBe('graytext');
    expect(cs(root.querySelector('svg text')!, 'fill')).toBe('canvastext');
    expect(cs(root.querySelector('svg line')!, 'stroke')).toBe('canvastext');
  });

  it('vfo face: track GrayText, lit dashes Highlight, reading CanvasText', () => {
    const root = render({ value: 20, variant: 'vfo' });
    expect(cs(root.querySelector('[data-meter-track]')!, 'stroke')).toBe('graytext');
    const lit = [...root.querySelectorAll<SVGLineElement>('[data-meter-fill]')]
      .find((line) => line.getAttribute('visibility') !== 'hidden');
    expect(lit).toBeDefined();
    expect(cs(lit!, 'stroke')).toBe('highlight');
    expect(cs(root.querySelector('[data-meter-reading]')!, 'fill')).toBe('canvastext');
  });

  it('sdr face: data-lit splits the bar and the forced palette follows it', () => {
    const root = render({ value: 20, variant: 'sdr-screen' });
    const segs = [...root.querySelectorAll<SVGRectElement>('[data-sdr-segment]')];
    expect(segs).toHaveLength(80);
    const litFlags = segs.map((seg) => seg.dataset.lit);
    // Lit cells are a prefix of the bar (index < sdrFill) and both
    // states are present at a mid-scale reading.
    const firstUnlit = litFlags.indexOf('false');
    expect(litFlags.includes('true')).toBe(true);
    expect(firstUnlit).toBeGreaterThan(0);
    expect(litFlags.slice(0, firstUnlit)).toEqual(Array<string>(firstUnlit).fill('true'));
    expect(litFlags.slice(firstUnlit)).toEqual(Array<string>(80 - firstUnlit).fill('false'));
    expect(cs(segs[0], 'fill')).toBe('highlight');
    expect(cs(segs[79], 'fill')).toBe('graytext');
  });
});
