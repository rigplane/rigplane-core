import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { flushSync, mount, unmount } from 'svelte';
import DragReorderFixture from './DragReorderFixture.svelte';

const KEYS = {
  left: 'test:drag:left',
  bottom: 'test:drag:bottom',
  right: 'test:drag:right',
} as const;

const rects = {
  left: { left: 0, top: 0, right: 100, bottom: 180, width: 100, height: 180 },
  bottom: { left: 150, top: 200, right: 250, bottom: 300, width: 100, height: 100 },
  right: { left: 300, top: 0, right: 400, bottom: 180, width: 100, height: 180 },
} as const;

let component: ReturnType<typeof mount> | undefined;
let target: HTMLElement;

function domRect(rect: Omit<DOMRect, 'x' | 'y' | 'toJSON'>): DOMRect {
  return { ...rect, x: rect.left, y: rect.top, toJSON: () => ({}) };
}

function panelIds(zone: keyof typeof KEYS): string[] {
  return [...target.querySelectorAll(`[data-zone="${zone}"] [data-panel-id]`)]
    .map((panel) => panel.getAttribute('data-panel-id')!);
}

function zone(zoneName: keyof typeof KEYS): HTMLElement {
  return target.querySelector(`[data-zone="${zoneName}"]`)!;
}

function handle(panelId: string): HTMLElement {
  return target.querySelector(`[data-drag-handle="${panelId}"]`)!;
}

function pointer(
  element: EventTarget, type: string, clientX: number, clientY: number, buttons = 0,
): void {
  element.dispatchEvent(new PointerEvent(type, {
    bubbles: true,
    pointerId: 1,
    clientX,
    clientY,
    buttons,
  }));
  flushSync();
}

function prepareRects(): void {
  for (const zoneName of Object.keys(KEYS) as (keyof typeof KEYS)[]) {
    const zoneElement = zone(zoneName);
    zoneElement.getBoundingClientRect = () => domRect(rects[zoneName]);
    [...zoneElement.querySelectorAll<HTMLElement>('[data-panel-id]')].forEach((panel, index) => {
      const container = rects[zoneName];
      panel.getBoundingClientRect = () => domRect({
        left: container.left,
        right: container.right,
        top: container.top + index * 50,
        bottom: container.top + (index + 1) * 50,
        width: container.width,
        height: 50,
      });
    });
  }
}

function render(): void {
  component = mount(DragReorderFixture, { target });
  flushSync();
  prepareRects();
}

beforeEach(() => {
  localStorage.clear();
  target = document.createElement('div');
  document.body.appendChild(target);
  Object.defineProperty(HTMLElement.prototype, 'setPointerCapture', {
    configurable: true,
    value: vi.fn(),
  });
  if (!globalThis.PointerEvent) {
    vi.stubGlobal('PointerEvent', class extends MouseEvent {
      pointerId: number;

      constructor(type: string, init: PointerEventInit = {}) {
        super(type, init);
        this.pointerId = init.pointerId ?? 0;
      }
    });
  }
});

afterEach(() => {
  if (component) unmount(component);
  component = undefined;
  target.remove();
  Reflect.deleteProperty(HTMLElement.prototype, 'setPointerCapture');
  vi.unstubAllGlobals();
});

describe('three-zone panel drag', () => {
  it('reorders upward on a window release and persists the result', () => {
    render();
    const beta = handle('beta');

    pointer(beta, 'pointerdown', 10, 75, 1);
    pointer(window, 'pointermove', 10, 20, 1);
    pointer(window, 'pointerup', -20, -20);

    expect(panelIds('left')).toEqual(['beta', 'alpha']);
    expect(JSON.parse(localStorage.getItem(KEYS.left)!)).toEqual(['beta', 'alpha']);
    expect(handle('beta').closest('[data-panel-id]')?.getAttribute('style')).not.toContain('opacity');
  });

  it('finishes from window after Svelte replaces the captured handle', () => {
    render();
    const beta = handle('beta');

    pointer(beta, 'pointerdown', 10, 75, 1);
    pointer(window, 'pointermove', 10, 20, 1);
    target.querySelector<HTMLElement>('[data-replace-handles]')!.click();
    flushSync();
    expect(handle('beta')).not.toBe(beta);
    pointer(beta, 'lostpointercapture', 10, 20, 1);
    pointer(window, 'pointerup', 10, 20);

    expect(panelIds('left')).toEqual(['beta', 'alpha']);
    expect(JSON.parse(localStorage.getItem(KEYS.left)!)).toEqual(['beta', 'alpha']);
  });

  it('cancels without reordering and clears every drag preview', () => {
    render();
    const alpha = handle('alpha');

    pointer(alpha, 'pointerdown', 10, 25, 1);
    pointer(window, 'pointermove', 350, 75, 1);
    expect(zone('right').dataset.dropTarget).toBe('true');
    pointer(window, 'pointercancel', 350, 75);

    expect(panelIds('left')).toEqual(['alpha', 'beta']);
    expect(panelIds('right')).toEqual(['gamma', 'delta']);
    expect(zone('right').dataset.dropTarget).toBe('false');
    expect(handle('alpha').closest('[data-panel-id]')?.getAttribute('style')).not.toContain('opacity');
  });

  it('cleans up terminal lost capture without committing', () => {
    render();
    const alpha = handle('alpha');

    pointer(alpha, 'pointerdown', 10, 25, 1);
    pointer(window, 'pointermove', 350, 75, 1);
    pointer(alpha, 'lostpointercapture', 350, 75, 0);

    expect(panelIds('left')).toEqual(['alpha', 'beta']);
    expect(panelIds('right')).toEqual(['gamma', 'delta']);
    expect(zone('right').dataset.dropTarget).toBe('false');
    expect(handle('alpha').closest('[data-panel-id]')?.getAttribute('style')).not.toContain('opacity');
  });

  it('tracks left to bottom to right and transfers only to the active target', () => {
    render();
    const alpha = handle('alpha');

    pointer(alpha, 'pointerdown', 10, 25);
    pointer(alpha, 'pointermove', 200, 250);
    expect(zone('bottom').dataset.dropTarget).toBe('true');

    pointer(alpha, 'pointermove', 350, 75);
    expect(zone('bottom').dataset.dropTarget).toBe('false');
    expect(zone('right').dataset.dropTarget).toBe('true');

    pointer(alpha, 'pointerup', 350, 75);
    expect(panelIds('left')).toEqual(['beta']);
    expect(panelIds('bottom')).toEqual([]);
    expect(panelIds('right')).toEqual(['gamma', 'alpha', 'delta']);
    expect(zone('right').dataset.dropTarget).toBe('false');
  });

  it('inserts at index zero when the active target dock is empty', () => {
    render();
    const alpha = handle('alpha');

    pointer(alpha, 'pointerdown', 10, 25);
    pointer(alpha, 'pointermove', 200, 250);
    pointer(alpha, 'pointerup', 200, 250);

    expect(panelIds('left')).toEqual(['beta']);
    expect(panelIds('bottom')).toEqual(['alpha']);
    expect(JSON.parse(localStorage.getItem(KEYS.bottom)!)).toEqual(['alpha']);
  });

  it('resets all three registered zones', () => {
    localStorage.setItem(KEYS.left, JSON.stringify(['beta']));
    localStorage.setItem(KEYS.bottom, JSON.stringify(['alpha']));
    localStorage.setItem(KEYS.right, JSON.stringify(['delta', 'gamma']));
    render();

    target.querySelector<HTMLElement>('[data-reset-all]')!.click();
    flushSync();

    expect(panelIds('left')).toEqual(['alpha', 'beta']);
    expect(panelIds('bottom')).toEqual([]);
    expect(panelIds('right')).toEqual(['gamma', 'delta']);
  });

  it('does not duplicate an id already present in the target and persists the transfer', () => {
    localStorage.setItem(KEYS.right, JSON.stringify(['gamma', 'alpha', 'delta']));
    render();
    const alpha = handle('alpha');

    pointer(alpha, 'pointerdown', 10, 25);
    pointer(alpha, 'pointermove', 350, 75);
    pointer(alpha, 'pointerup', 350, 75);

    expect(panelIds('right').filter((id) => id === 'alpha')).toHaveLength(1);
    expect(JSON.parse(localStorage.getItem(KEYS.left)!)).toEqual(['beta']);
    expect(JSON.parse(localStorage.getItem(KEYS.right)!))
      .toEqual(['gamma', 'alpha', 'delta']);
  });
});
