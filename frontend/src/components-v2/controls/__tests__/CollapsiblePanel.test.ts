import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest';
import { mount, unmount, flushSync } from 'svelte';
import CollapsiblePanel from '../CollapsiblePanel.svelte';

// Mock localStorage
const localStorageMock = (() => {
  let store: Record<string, string> = {};
  return {
    getItem: (key: string) => store[key] || null,
    setItem: (key: string, value: string) => {
      store[key] = value;
    },
    removeItem: (key: string) => {
      delete store[key];
    },
    clear: () => {
      store = {};
    },
  };
})();

Object.defineProperty(globalThis, 'localStorage', {
  value: localStorageMock,
  writable: true,
});

let components: ReturnType<typeof mount>[] = [];

function mountPanel(props: { title: string; panelId: string; collapsible?: boolean }) {
  const target = document.createElement('div');
  document.body.appendChild(target);
  const component = mount(CollapsiblePanel, { target, props });
  flushSync();
  components.push(component);
  return target;
}

beforeEach(() => {
  components = [];
  localStorageMock.clear();
});

afterEach(() => {
  components.forEach((c) => unmount(c));
  document.body.innerHTML = '';
});

describe('CollapsiblePanel', () => {
  it('renders the title', () => {
    const target = mountPanel({ title: 'Test Panel', panelId: 'test' });
    const title = target.querySelector('.title');
    expect(title?.textContent).toBe('Test Panel');
  });

  it('renders children in the content area', () => {
    const target = document.createElement('div');
    document.body.appendChild(target);

    const component = mount(CollapsiblePanel, {
      target,
      props: {
        title: 'Test',
        panelId: 'test',
        children: (anchor: any, props: any) => {
          const div = document.createElement('div');
          div.className = 'test-child';
          div.textContent = 'Child content';
          anchor.before(div);
          return {
            update: () => {},
            destroy: () => div.remove(),
          };
        },
      },
    });
    flushSync();
    components.push(component);

    const child = target.querySelector('.test-child');
    expect(child?.textContent).toBe('Child content');
  });

  it('shows expanded chevron (▾) by default', () => {
    const target = mountPanel({ title: 'Test', panelId: 'test' });
    const chevron = target.querySelector('.chevron');
    expect(chevron?.textContent).toBe('▾');
  });

  it('has aria-expanded=true by default', () => {
    const target = mountPanel({ title: 'Test', panelId: 'test' });
    const header = target.querySelector('.panel-header') as HTMLButtonElement;
    expect(header?.getAttribute('aria-expanded')).toBe('true');
  });

  it('toggles collapsed state on header click', () => {
    const target = mountPanel({ title: 'Test', panelId: 'test' });
    const header = target.querySelector('.panel-header') as HTMLButtonElement;
    const chevron = target.querySelector('.chevron');

    // Initially expanded
    expect(chevron?.textContent).toBe('▾');
    expect(header?.getAttribute('aria-expanded')).toBe('true');

    // Click to collapse
    header?.click();
    flushSync();

    expect(chevron?.textContent).toBe('▸');
    expect(header?.getAttribute('aria-expanded')).toBe('false');

    // Click to expand
    header?.click();
    flushSync();

    expect(chevron?.textContent).toBe('▾');
    expect(header?.getAttribute('aria-expanded')).toBe('true');
  });

  it('persists collapsed state to localStorage', () => {
    const target = mountPanel({ title: 'Test', panelId: 'test-panel' });
    const header = target.querySelector('.panel-header') as HTMLButtonElement;

    header?.click();
    flushSync();

    const stored = localStorage.getItem('rigplane:panel-collapsed');
    expect(stored).not.toBeNull();
    const data = JSON.parse(stored!);
    expect(data['test-panel']).toBe(true);
  });

  it('restores collapsed state from localStorage', () => {
    localStorage.setItem('rigplane:panel-collapsed', JSON.stringify({ 'test-panel': true }));

    const target = mountPanel({ title: 'Test', panelId: 'test-panel' });
    const chevron = target.querySelector('.chevron');
    const header = target.querySelector('.panel-header') as HTMLButtonElement;

    expect(chevron?.textContent).toBe('▸');
    expect(header?.getAttribute('aria-expanded')).toBe('false');
  });

  it('shows no chevron when collapsible=false', () => {
    const target = mountPanel({ title: 'Test', panelId: 'test', collapsible: false });
    const chevron = target.querySelector('.chevron');
    expect(chevron).toBeNull();
  });

  it('does not toggle when collapsible=false', () => {
    const target = mountPanel({ title: 'Test', panelId: 'test', collapsible: false });
    const header = target.querySelector('.panel-header') as HTMLButtonElement;
    const initialExpanded = header?.getAttribute('aria-expanded');

    header?.click();
    flushSync();

    expect(header?.getAttribute('aria-expanded')).toBe(initialExpanded);
  });

  it('has disabled attribute when collapsible=false', () => {
    const target = mountPanel({ title: 'Test', panelId: 'test', collapsible: false });
    const header = target.querySelector('.panel-header') as HTMLButtonElement;
    expect(header?.disabled).toBe(true);
  });

  it('adds collapsed class to content when collapsed', () => {
    const target = mountPanel({ title: 'Test', panelId: 'test' });
    const header = target.querySelector('.panel-header') as HTMLButtonElement;
    const content = target.querySelector('.panel-content');

    expect(content?.classList.contains('collapsed')).toBe(false);

    header?.click();
    flushSync();

    expect(content?.classList.contains('collapsed')).toBe(true);
  });

  it('sets data-collapsed attribute on root element', () => {
    const target = mountPanel({ title: 'Test', panelId: 'test' });
    const panel = target.querySelector('.collapsible-panel');
    const header = target.querySelector('.panel-header') as HTMLButtonElement;

    expect(panel?.getAttribute('data-collapsed')).toBe('false');

    header?.click();
    flushSync();

    expect(panel?.getAttribute('data-collapsed')).toBe('true');
  });

  it('has collapsible class on header when collapsible=true', () => {
    const target = mountPanel({ title: 'Test', panelId: 'test', collapsible: true });
    const header = target.querySelector('.panel-header');
    expect(header?.classList.contains('collapsible')).toBe(true);
  });

  it('does not have collapsible class on header when collapsible=false', () => {
    const target = mountPanel({ title: 'Test', panelId: 'test', collapsible: false });
    const header = target.querySelector('.panel-header');
    expect(header?.classList.contains('collapsible')).toBe(false);
  });

  it('renders drag handle when draggable=true', () => {
    const target = document.createElement('div');
    document.body.appendChild(target);
    const component = mount(CollapsiblePanel, {
      target,
      props: { title: 'Test', panelId: 'test', draggable: true },
    });
    flushSync();
    components.push(component);

    const handle = target.querySelector('.drag-handle');
    expect(handle).not.toBeNull();
    expect(handle?.textContent).toBe('⠿');
    expect(handle?.getAttribute('aria-label')).toBe('Drag to reorder');
  });

  it('does not render drag handle when draggable is not set', () => {
    const target = mountPanel({ title: 'Test', panelId: 'test' });
    const handle = target.querySelector('.drag-handle');
    expect(handle).toBeNull();
  });

  it('calls onDragStart when drag handle receives pointerdown', () => {
    const onDragStart = vi.fn();
    const target = document.createElement('div');
    document.body.appendChild(target);
    const component = mount(CollapsiblePanel, {
      target,
      props: { title: 'Test', panelId: 'drag-test', draggable: true, onDragStart },
    });
    flushSync();
    components.push(component);

    const handle = target.querySelector('.drag-handle') as HTMLElement;
    const event = new PointerEvent('pointerdown', { bubbles: true });
    handle.setPointerCapture = vi.fn();
    handle.dispatchEvent(event);

    expect(onDragStart).toHaveBeenCalledWith('drag-test', expect.any(PointerEvent));
  });

  it('applies style attribute to root element', () => {
    const target = document.createElement('div');
    document.body.appendChild(target);
    const component = mount(CollapsiblePanel, {
      target,
      props: { title: 'Test', panelId: 'test', style: 'order:3' },
    });
    flushSync();
    components.push(component);

    const panel = target.querySelector('.collapsible-panel') as HTMLElement;
    expect(panel?.style.order).toBe('3');
  });

  describe('autoCollapseWhen', () => {
    it('force-collapses when autoCollapseWhen=true', () => {
      const target = document.createElement('div');
      document.body.appendChild(target);
      const component = mount(CollapsiblePanel, {
        target,
        props: { title: 'CW', panelId: 'auto-cw', autoCollapseWhen: true },
      });
      flushSync();
      components.push(component);

      const header = target.querySelector('.panel-header') as HTMLButtonElement;
      const chevron = target.querySelector('.chevron');
      expect(chevron?.textContent).toBe('▸');
      expect(header?.getAttribute('aria-expanded')).toBe('false');
    });

    it('clicking while auto-collapsed expands (user override)', () => {
      const target = document.createElement('div');
      document.body.appendChild(target);
      const component = mount(CollapsiblePanel, {
        target,
        props: { title: 'CW', panelId: 'auto-cw', autoCollapseWhen: true },
      });
      flushSync();
      components.push(component);

      const header = target.querySelector('.panel-header') as HTMLButtonElement;
      header.click();
      flushSync();

      const chevron = target.querySelector('.chevron');
      expect(chevron?.textContent).toBe('▾');
      expect(header?.getAttribute('aria-expanded')).toBe('true');
    });

    it('clicking while expanded under auto-collapse re-collapses', () => {
      const target = document.createElement('div');
      document.body.appendChild(target);
      const component = mount(CollapsiblePanel, {
        target,
        props: { title: 'CW', panelId: 'auto-cw', autoCollapseWhen: true },
      });
      flushSync();
      components.push(component);

      const header = target.querySelector('.panel-header') as HTMLButtonElement;
      // Expand via user click
      header.click();
      flushSync();
      expect(header?.getAttribute('aria-expanded')).toBe('true');

      // Click again — should collapse (auto-collapse re-asserts)
      header.click();
      flushSync();
      expect(header?.getAttribute('aria-expanded')).toBe('false');
    });

    it('expands on a single click when persisted-collapsed AND autoCollapseWhen=true', () => {
      // Regression: previously, loading collapsed=true from localStorage while
      // autoCollapseWhen=true required TWO clicks — the first flipped
      // ``collapsed`` to false but left ``userExpanded`` false, so the derived
      // ``effectiveCollapsed`` stayed true.
      localStorage.setItem(
        'rigplane:panel-collapsed',
        JSON.stringify({ 'auto-cw-persisted': true }),
      );

      const target = document.createElement('div');
      document.body.appendChild(target);
      const component = mount(CollapsiblePanel, {
        target,
        props: { title: 'CW', panelId: 'auto-cw-persisted', autoCollapseWhen: true },
      });
      flushSync();
      components.push(component);

      const header = target.querySelector('.panel-header') as HTMLButtonElement;
      expect(header?.getAttribute('aria-expanded')).toBe('false');

      // One click should expand it.
      header.click();
      flushSync();

      expect(header?.getAttribute('aria-expanded')).toBe('true');
      const chevron = target.querySelector('.chevron');
      expect(chevron?.textContent).toBe('▾');
    });

    it('does not force-collapse when autoCollapseWhen=false', () => {
      const target = document.createElement('div');
      document.body.appendChild(target);
      const component = mount(CollapsiblePanel, {
        target,
        props: { title: 'CW', panelId: 'auto-cw', autoCollapseWhen: false },
      });
      flushSync();
      components.push(component);

      const header = target.querySelector('.panel-header') as HTMLButtonElement;
      expect(header?.getAttribute('aria-expanded')).toBe('true');
    });
  });

  describe('swipe gestures', () => {
    function simulateSwipe(header: HTMLElement, dy: number, dx = 0) {
      const startX = 100;
      const startY = 100;

      header.dispatchEvent(
        new PointerEvent('pointerdown', { clientX: startX, clientY: startY, bubbles: true }),
      );
      header.dispatchEvent(
        new PointerEvent('pointermove', {
          clientX: startX + dx,
          clientY: startY + dy,
          bubbles: true,
        }),
      );
      // pointerup is not strictly needed since toggle happens on pointermove
    }

    it('swipe-down collapses an expanded panel', () => {
      const target = mountPanel({ title: 'Test', panelId: 'swipe-test' });
      const header = target.querySelector('.panel-header') as HTMLElement;
      const chevron = target.querySelector('.chevron');

      // Initially expanded
      expect(chevron?.textContent).toBe('▾');

      // Swipe down (dy=40 > threshold of 30)
      simulateSwipe(header, 40);
      flushSync();

      expect(chevron?.textContent).toBe('▸');
    });

    it('swipe-up expands a collapsed panel', () => {
      const target = mountPanel({ title: 'Test', panelId: 'swipe-test' });
      const header = target.querySelector('.panel-header') as HTMLElement;
      const chevron = target.querySelector('.chevron');

      // Collapse first via click
      header.click();
      flushSync();
      expect(chevron?.textContent).toBe('▸');

      // Swipe up (dy=-40)
      simulateSwipe(header, -40);
      flushSync();

      expect(chevron?.textContent).toBe('▾');
    });

    it('small movements do not trigger swipe (click still works)', () => {
      const target = mountPanel({ title: 'Test', panelId: 'swipe-test' });
      const header = target.querySelector('.panel-header') as HTMLElement;
      const chevron = target.querySelector('.chevron');

      // Small movement below threshold
      simulateSwipe(header, 10);
      flushSync();

      // Should still be expanded (no swipe triggered)
      expect(chevron?.textContent).toBe('▾');

      // Click should still work normally
      header.click();
      flushSync();
      expect(chevron?.textContent).toBe('▸');
    });

    it('non-collapsible panels ignore swipe', () => {
      const target = mountPanel({ title: 'Test', panelId: 'swipe-test', collapsible: false });
      const header = target.querySelector('.panel-header') as HTMLElement;

      simulateSwipe(header, 40);
      flushSync();

      // aria-expanded should remain true (no toggle)
      expect(header.getAttribute('aria-expanded')).toBe('true');
    });

    it('swipe-down on already-collapsed panel does nothing', () => {
      const target = mountPanel({ title: 'Test', panelId: 'swipe-test' });
      const header = target.querySelector('.panel-header') as HTMLElement;
      const chevron = target.querySelector('.chevron');

      // Collapse first
      header.click();
      flushSync();
      expect(chevron?.textContent).toBe('▸');

      // Swipe down on collapsed panel should not toggle
      simulateSwipe(header, 40);
      flushSync();

      expect(chevron?.textContent).toBe('▸');
    });

    it('swipe-up on already-expanded panel does nothing', () => {
      const target = mountPanel({ title: 'Test', panelId: 'swipe-test' });
      const chevron = target.querySelector('.chevron');
      const header = target.querySelector('.panel-header') as HTMLElement;

      // Initially expanded
      expect(chevron?.textContent).toBe('▾');

      // Swipe up should not toggle (already expanded)
      simulateSwipe(header, -40);
      flushSync();

      expect(chevron?.textContent).toBe('▾');
    });

    it('horizontal swipe does not trigger collapse', () => {
      const target = mountPanel({ title: 'Test', panelId: 'swipe-test' });
      const header = target.querySelector('.panel-header') as HTMLElement;
      const chevron = target.querySelector('.chevron');

      // Mostly horizontal movement (dx=40, dy=10)
      simulateSwipe(header, 10, 40);
      flushSync();

      // Should remain expanded
      expect(chevron?.textContent).toBe('▾');
    });

    it('swipe prevents click from also firing', () => {
      const target = mountPanel({ title: 'Test', panelId: 'swipe-test' });
      const header = target.querySelector('.panel-header') as HTMLElement;
      const chevron = target.querySelector('.chevron');

      // Swipe down to collapse
      simulateSwipe(header, 40);
      flushSync();
      expect(chevron?.textContent).toBe('▸');

      // Simulate click that would follow a swipe — should be suppressed
      header.click();
      flushSync();

      // Should remain collapsed (click was suppressed by swipeHandled flag)
      expect(chevron?.textContent).toBe('▸');
    });

    it('hover motion without pointerdown does not collapse the panel', () => {
      // Regression for the phantom-swipe bug: pointermove fires during plain
      // mouse-over, and the handler used to compute ``dy = clientY - 0`` on
      // uninitialised swipe state, tripping the collapse threshold on the
      // first frame.  Now pointermove is gated on ``swipeActive``.
      const target = mountPanel({ title: 'Test', panelId: 'hover-test' });
      const header = target.querySelector('.panel-header') as HTMLElement;
      const chevron = target.querySelector('.chevron');

      expect(chevron?.textContent).toBe('▾');

      // Hover: several pointermove events at varying Y, no pointerdown.
      for (const y of [100, 150, 200, 400, 600]) {
        header.dispatchEvent(
          new PointerEvent('pointermove', { clientX: 50, clientY: y, bubbles: true }),
        );
      }
      flushSync();

      // Must still be expanded — hover must not toggle.
      expect(chevron?.textContent).toBe('▾');
    });

    it('pointerup clears swipe state so next hover is inert', () => {
      const target = mountPanel({ title: 'Test', panelId: 'reset-test' });
      const header = target.querySelector('.panel-header') as HTMLElement;
      const chevron = target.querySelector('.chevron');

      // Complete a successful swipe (down → collapse)
      simulateSwipe(header, 40);
      header.dispatchEvent(new PointerEvent('pointerup', { bubbles: true }));
      flushSync();
      expect(chevron?.textContent).toBe('▸');

      // Now hover: moves should not expand the collapsed panel.
      for (const y of [20, 60, 120, 300]) {
        header.dispatchEvent(
          new PointerEvent('pointermove', { clientX: 50, clientY: y, bubbles: true }),
        );
      }
      flushSync();

      // Still collapsed — hover did not re-toggle.
      expect(chevron?.textContent).toBe('▸');
    });
  });
});
