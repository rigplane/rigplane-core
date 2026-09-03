/**
 * MOR-2147 — component-level regression coverage for the ScaledStage
 * measure/write defect: `ScaledStage` used to measure its "holder" element
 * with `ResizeObserver` and then write `holder.style.height` back onto that
 * same element, desynchronizing the reported box from the host's real size.
 *
 * jsdom implements neither layout nor `ResizeObserver`, so this file rebuilds
 * the one piece of browser behavior the defect depends on: a real
 * `ResizeObserver` reports an observed element's OWN content box, not its
 * parent's. `effectiveHolderBox()` below models that box as CSS `100%` of
 * the fake "parent" UNLESS the component has pinned an axis with an inline
 * style — which is exactly the mechanism under test. `resizeContainer()`
 * only invokes the fake observer's callback when that modeled box actually
 * changed, mirroring a real `ResizeObserver`'s "no change, no callback"
 * contract.
 *
 * Mounts a real component and stubs two globals (`ResizeObserver`,
 * `getBoundingClientRect`), so this file is named `*.isolated.test.ts` per
 * the pool-membership convention in `vite.config.ts`.
 */
import { readFileSync } from 'node:fs';
import { describe, it, expect, beforeEach, afterEach } from 'vitest';
import { mount, unmount, flushSync, type Snippet } from 'svelte';
import ScaledStage from '../ScaledStage.svelte';

interface Box {
  readonly width: number;
  readonly height: number;
}

class FakeResizeObserver {
  static instances: FakeResizeObserver[] = [];
  readonly callback: ResizeObserverCallback;
  target: Element | null = null;

  constructor(callback: ResizeObserverCallback) {
    this.callback = callback;
    FakeResizeObserver.instances.push(this);
  }

  observe(target: Element) {
    this.target = target;
  }

  unobserve() {
    this.target = null;
  }

  disconnect() {
    this.target = null;
  }
}

let components: ReturnType<typeof mount>[] = [];
let containerSize: Box = { width: 0, height: 0 };
let lastReportedHolderBox: Box | null = null;
let holderEl: HTMLElement | null = null;
let originalGetBoundingClientRect: typeof HTMLElement.prototype.getBoundingClientRect;
let originalResizeObserver: typeof globalThis.ResizeObserver;

/** What a real browser would report for the holder element right now: each
 *  axis tracks the fake "parent" (`containerSize`) unless the component has
 *  pinned that axis with an inline style, in which case the pinned value
 *  wins regardless of the parent — the exact same-element measure/write bug
 *  under test. Takes the element explicitly (rather than reading the
 *  module-level `holderEl`) because it also runs from inside the
 *  `getBoundingClientRect` stub during mount, before `mountStage` has had a
 *  chance to capture `holderEl`. */
function effectiveHolderBox(el: HTMLElement): Box {
  const style = el.style;
  return {
    width: style.width ? parseFloat(style.width) : containerSize.width,
    height: style.height ? parseFloat(style.height) : containerSize.height,
  };
}

function stubRect(width: number, height: number): DOMRect {
  return {
    width,
    height,
    top: 0,
    left: 0,
    right: width,
    bottom: height,
    x: 0,
    y: 0,
    toJSON() {
      return {};
    },
  } as DOMRect;
}

beforeEach(() => {
  components = [];
  containerSize = { width: 0, height: 0 };
  lastReportedHolderBox = null;
  holderEl = null;
  FakeResizeObserver.instances = [];

  originalResizeObserver = globalThis.ResizeObserver;
  globalThis.ResizeObserver = FakeResizeObserver as unknown as typeof ResizeObserver;

  originalGetBoundingClientRect = HTMLElement.prototype.getBoundingClientRect;
  HTMLElement.prototype.getBoundingClientRect = function (this: HTMLElement) {
    if (this.classList.contains('scaled-stage-holder')) {
      const box = effectiveHolderBox(this);
      return stubRect(box.width, box.height);
    }
    return stubRect(0, 0);
  };
});

afterEach(() => {
  components.forEach((c) => unmount(c));
  while (document.body.firstChild) {
    document.body.removeChild(document.body.firstChild);
  }
  HTMLElement.prototype.getBoundingClientRect = originalGetBoundingClientRect;
  globalThis.ResizeObserver = originalResizeObserver;
});

const noopChildren = ((_anchor: unknown) => ({
  update: () => {},
  destroy: () => {},
})) as unknown as Snippet;

/** Mounts `ScaledStage` at the given native size inside the current
 *  `containerSize`, and captures the holder element for later resizes. */
function mountStage(
  nativeW: number,
  nativeH: number,
  opts?: { anchor?: 'top-left' | 'center'; minScale?: number },
): HTMLElement {
  const target = document.createElement('div');
  document.body.appendChild(target);
  const component = mount(ScaledStage, {
    target,
    props: { nativeW, nativeH, anchor: opts?.anchor, minScale: opts?.minScale, children: noopChildren },
  });
  flushSync();
  components.push(component);
  holderEl = target.querySelector<HTMLElement>('.scaled-stage-holder');
  lastReportedHolderBox = effectiveHolderBox(holderEl!);
  return target;
}

function readScale(target: HTMLElement): number {
  const stage = target.querySelector<HTMLElement>('.scaled-stage');
  const transform = stage?.style.transform ?? '';
  const match = transform.match(/scale\(([-\d.]+)\)/);
  return match ? Number(match[1]) : NaN;
}

/** Reads the raw `transform` inline style, for tests that care about the
 *  exact string (whether a `translate()` prefix is present at all), not
 *  just the scale factor. */
function readTransform(target: HTMLElement): string {
  const stage = target.querySelector<HTMLElement>('.scaled-stage');
  return stage?.style.transform ?? '';
}

/** Resizes the fake "parent" and, only if the holder's modeled box actually
 *  changed as a result, fires the fake `ResizeObserver` callback for it. */
function resizeContainer(width: number, height: number) {
  containerSize = { width, height };
  const box = effectiveHolderBox(holderEl!);
  const unchanged =
    lastReportedHolderBox &&
    box.width === lastReportedHolderBox.width &&
    box.height === lastReportedHolderBox.height;
  if (unchanged) return;
  lastReportedHolderBox = box;
  for (const observer of FakeResizeObserver.instances) {
    if (observer.target === holderEl) {
      observer.callback(
        [{ target: holderEl, contentRect: box } as unknown as ResizeObserverEntry],
        observer as unknown as ResizeObserver,
      );
    }
  }
  flushSync();
}

describe('ScaledStage — measure/write regression (MOR-2147)', () => {
  it('scale returns to 1 after the host grows from 100x100 to 4000x4000', () => {
    containerSize = { width: 100, height: 100 };
    const target = mountStage(200, 200);
    expect(readScale(target)).toBe(0.5);

    resizeContainer(4000, 4000);

    expect(readScale(target)).toBe(1);
  });

  it('scale drops when only the host HEIGHT shrinks', () => {
    containerSize = { width: 100, height: 100 };
    const target = mountStage(200, 200);
    expect(readScale(target)).toBe(0.5);

    resizeContainer(100, 20);

    expect(readScale(target)).toBeLessThan(0.5);
  });
});

describe('ScaledStage — measure() does not self-trigger its own effect', () => {
  // Regression pin: `measure()` used to read the `scale` `$state` binding as
  // the third argument to `computeStageCenterOffset`, right after writing
  // it. That read registers `scale` as a dependency of the enclosing
  // `$effect`, and since the write just above just changed it, the effect
  // invalidated and re-ran itself once per mount — tearing down the first
  // `ResizeObserver` and constructing a second one. `FakeResizeObserver`
  // (declared above, shared with the MOR-2147 describe block) already
  // records every instance constructed; this test reuses that same list
  // rather than adding a second stub.
  it('constructs exactly one ResizeObserver per mount', () => {
    containerSize = { width: 100, height: 100 };
    mountStage(200, 200);

    expect(FakeResizeObserver.instances.length).toBe(1);
  });
});

describe('ScaledStage — anchor prop (MOR-2251)', () => {
  it('defaults to top-left: the transform is exactly `scale(n)`, with no translate() prefix', () => {
    // Exact-string check, not the `readScale` regex above: a regex match on
    // `scale(...)` inside the string would still pass even if the default
    // accidentally grew a `translate(0px, 0px)` prefix, which is exactly the
    // "default preserves today's behaviour" claim this test exists to pin.
    containerSize = { width: 100, height: 100 };
    const target = mountStage(200, 200);
    expect(readTransform(target)).toBe('scale(0.5)');
  });

  it('anchor="top-left" (explicit) matches the default: no translate() prefix', () => {
    containerSize = { width: 100, height: 100 };
    const target = mountStage(200, 200, { anchor: 'top-left' });
    expect(readTransform(target)).toBe('scale(0.5)');
  });

  it('anchor="center" at scale 1 with a host that already matches native is a no-op translate (scale-1 control)', () => {
    // Every other case here is below scale 1; without this control there is
    // no evidence the offset formula also degrades correctly at scale 1.
    containerSize = { width: 200, height: 200 };
    const target = mountStage(200, 200, { anchor: 'center' });
    expect(readTransform(target)).toBe('translate(0px, 0px) scale(1)');
  });

  it('anchor="center" centers a shrunk stage inside a larger host', () => {
    // native 200x200, host 300x300 -> scale stays capped at 1 (host is
    // bigger on both axes), so the 200x200 box has 100px of leftover space
    // per axis to split evenly: translate(50px, 50px).
    containerSize = { width: 300, height: 300 };
    const target = mountStage(200, 200, { anchor: 'center' });
    expect(readTransform(target)).toBe('translate(50px, 50px) scale(1)');
  });

  it('anchor="center" recomputes the translate as the host resizes asymmetrically', () => {
    containerSize = { width: 100, height: 100 };
    const target = mountStage(200, 200, { anchor: 'center' });
    // scale 0.5 -> scaled box 100x100 exactly fills the 100x100 host.
    expect(readTransform(target)).toBe('translate(0px, 0px) scale(0.5)');

    resizeContainer(300, 100);
    // scale = min(300/200, 100/200, 1) = 0.5 -> scaled box stays 100x100,
    // now inside a 300x100 host: 200px leftover width (100 each side), 0
    // leftover height.
    expect(readTransform(target)).toBe('translate(100px, 0px) scale(0.5)');
  });
});

describe('ScaledStage — .scaled-stage declares flex-shrink: 0 (MOR-2251)', () => {
  // This is a TEXT pin on the component's own <style> block, the same
  // idiom `PeerSplitLayout.component.test.ts`'s `declarationsFor` uses
  // (read that file before touching this one). It verifies the
  // declaration SURVIVES in source; it cannot verify the BEHAVIOUR the
  // declaration produces, because jsdom computes no layout at all — a
  // finding both this file's own `getBoundingClientRect`/`offsetWidth`
  // probe and `PeerSplitLayout.component.test.ts`'s file header establish
  // independently (that file: "this Vitest/jsdom config injects no
  // component <style> during a mount ... every element's getComputedStyle
  // reads the UA default regardless of what any <style> block declares").
  // A comment claiming this test proves the stage cannot reflow would be
  // false. What it does prove: the declaration is the ENTIRE mechanism —
  // no other property makes `.scaled-stage` shrink-proof, and the grid
  // case (see the property's own comment in ScaledStage.svelte) needs no
  // second declaration — so deleting this line is the only regression path
  // for this fix, and this pin fails exactly on that deletion.
  const source = readFileSync('src/primitives/stage/ScaledStage.svelte', 'utf8');
  const styleBlock = source.match(/<style>([\s\S]*?)<\/style>/)?.[1] ?? '';
  const css = styleBlock.replace(/\/\*[\s\S]*?\*\//g, '');

  function declarationsFor(selector: string): Record<string, string> {
    for (const [, selectorList, body] of css.matchAll(/([^{}]+)\{([^{}]*)\}/g)) {
      for (const candidate of selectorList.split(',')) {
        if (candidate.trim() !== selector) continue;
        const declarations: Record<string, string> = {};
        for (const declaration of body.split(';')) {
          const colon = declaration.indexOf(':');
          if (colon > 0) declarations[declaration.slice(0, colon).trim()] = declaration.slice(colon + 1).trim();
        }
        return declarations;
      }
    }
    throw new Error(`no rule for selector "${selector}" in ScaledStage.svelte's <style> block`);
  }

  it('declares flex-shrink: 0', () => {
    const stage = declarationsFor('.scaled-stage');
    expect(stage['flex-shrink']).toBe('0');
  });
});

describe('ScaledStage — the minScale floor (MOR-2259)', () => {
  it('holds the floor when the host is too small for the fit', () => {
    containerSize = { width: 320, height: 135 };
    const target = mountStage(1280, 540, { minScale: 0.5 });
    expect(readTransform(target)).toBe('scale(0.5)');
  });

  it('omitting minScale leaves the same host at the unfloored fit, exactly', () => {
    // The default-preservation control, one mount apart from the floored
    // case above: same host, same native size, no floor prop.
    containerSize = { width: 320, height: 135 };
    const target = mountStage(1280, 540);
    expect(readTransform(target)).toBe('scale(0.25)');
  });

  it('releases the floor once the host grows enough to fit above it', () => {
    containerSize = { width: 320, height: 135 };
    const target = mountStage(1280, 540, { minScale: 0.5 });
    expect(readTransform(target)).toBe('scale(0.5)');

    resizeContainer(960, 405);

    expect(readTransform(target)).toBe('scale(0.75)');
  });

  it('anchor="center" translates to the host origin while the floor is active', () => {
    // native 1280x540 held at 0.5 is a 640x270 box in a 524x221 host — it
    // overflows on both axes, so centring has nothing to split and the box
    // must start at the origin for the overflow to be scrollable.
    containerSize = { width: 524, height: 221 };
    const target = mountStage(1280, 540, { anchor: 'center', minScale: 0.5 });
    expect(readTransform(target)).toBe('translate(0px, 0px) scale(0.5)');
  });
});

describe('ScaledStage — the holder scrolls only while the floor clamps (MOR-2259)', () => {
  // NOT the TEXT-pin idiom above: `overflow` is an inline value the
  // component computes per measured host box, so jsdom exposes the real
  // decision on `holder.style.overflow` even though it lays nothing out.
  //
  // What jsdom cannot show is the consequence — whether Chrome then
  // reserves a scroll area. The two host boxes below are the ones Chrome
  // actually gave `.scaled-stage-holder` under `LcdLayout
  // variant="peer-split"`: 802x337 at a 1280x800 viewport, 522x219 at
  // 1000x800. Each test asserts the geometry that makes `overflow`
  // load-bearing at that host — the stage's inline layout box exceeds the
  // host either way, so `hidden` versus `auto` is the whole difference —
  // alongside the value itself. A pin on the value alone would have passed
  // against the unconditional `overflow: auto` this replaces.
  const NATIVE_W = 1280;
  const NATIVE_H = 540;
  const FLOOR = 0.5;

  const holderOf = (target: HTMLElement) => target.querySelector<HTMLElement>('.scaled-stage-holder')!;
  const stageOf = (target: HTMLElement) => target.querySelector<HTMLElement>('.scaled-stage')!;

  it('a host the painted stage already fits inside gets `hidden`', () => {
    containerSize = { width: 802, height: 337 };
    const target = mountStage(NATIVE_W, NATIVE_H, { minScale: FLOOR });

    // The floor is idle here: height is the binding axis and its ratio is
    // above the floor, so the scale is that ratio exactly.
    expect(readScale(target)).toBe(337 / NATIVE_H);
    expect(readScale(target)).toBeGreaterThan(FLOOR);
    // The untransformed layout box still overflows the host on both axes...
    expect(parseFloat(stageOf(target).style.width)).toBeGreaterThan(802);
    expect(parseFloat(stageOf(target).style.height)).toBeGreaterThan(337);
    // ...while the painted box does not: its height equals the host's by the
    // equality above, and its width is short of it.
    expect(readScale(target) * NATIVE_W).toBeLessThan(802);

    expect(holderOf(target).style.overflow).toBe('hidden');
  });

  it('a host the floored stage overflows gets `auto`', () => {
    containerSize = { width: 522, height: 219 };
    const target = mountStage(NATIVE_W, NATIVE_H, { minScale: FLOOR });

    expect(readScale(target)).toBe(FLOOR);
    // The painted box is larger than the host on both axes, so the part
    // outside it is only reachable if the holder scrolls.
    expect(readScale(target) * NATIVE_W).toBeGreaterThan(522);
    expect(readScale(target) * NATIVE_H).toBeGreaterThan(219);

    expect(holderOf(target).style.overflow).toBe('auto');
  });

  it('follows the host box rather than being fixed at mount', () => {
    containerSize = { width: 522, height: 219 };
    const target = mountStage(NATIVE_W, NATIVE_H, { minScale: FLOOR });
    expect(holderOf(target).style.overflow).toBe('auto');

    resizeContainer(802, 337);

    expect(holderOf(target).style.overflow).toBe('hidden');
  });

  it('with no floor declared, even a host far too small for the fit gets `hidden`', () => {
    containerSize = { width: 100, height: 100 };
    const target = mountStage(NATIVE_W, NATIVE_H);

    expect(holderOf(target).style.overflow).toBe('hidden');
  });
});
