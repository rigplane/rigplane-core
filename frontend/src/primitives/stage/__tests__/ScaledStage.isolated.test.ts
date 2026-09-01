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
function mountStage(nativeW: number, nativeH: number): HTMLElement {
  const target = document.createElement('div');
  document.body.appendChild(target);
  const component = mount(ScaledStage, {
    target,
    props: { nativeW, nativeH, children: noopChildren },
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
