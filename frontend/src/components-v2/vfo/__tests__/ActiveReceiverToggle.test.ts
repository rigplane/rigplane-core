import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { mount, unmount, flushSync } from 'svelte';
import type { ComponentProps } from 'svelte';
import ActiveReceiverToggle from '../ActiveReceiverToggle.svelte';

let components: ReturnType<typeof mount>[] = [];

function mountToggle(props: ComponentProps<typeof ActiveReceiverToggle>): HTMLElement {
  const target = document.createElement('div');
  document.body.appendChild(target);
  const component = mount(ActiveReceiverToggle, { target, props });
  flushSync();
  components.push(component);
  return target;
}

beforeEach(() => {
  components = [];
});

afterEach(() => {
  components.forEach((c) => unmount(c));
  while (document.body.firstChild) {
    document.body.removeChild(document.body.firstChild);
  }
});

describe('ActiveReceiverToggle', () => {
  describe('structure', () => {
    it('renders a radiogroup with two radio segments', () => {
      const t = mountToggle({ active: 'MAIN', onChange: vi.fn() });
      const group = t.querySelector('[role="radiogroup"]');
      expect(group).not.toBeNull();

      const radios = t.querySelectorAll('[role="radio"]');
      expect(radios.length).toBe(2);
    });

    it('segments expose MAIN and SUB via data attribute', () => {
      const t = mountToggle({ active: 'MAIN', onChange: vi.fn() });
      expect(t.querySelector('[data-active-receiver-segment="MAIN"]')).not.toBeNull();
      expect(t.querySelector('[data-active-receiver-segment="SUB"]')).not.toBeNull();
    });

    it('segments display short labels M and S', () => {
      const t = mountToggle({ active: 'MAIN', onChange: vi.fn() });
      const main = t.querySelector<HTMLButtonElement>('[data-active-receiver-segment="MAIN"]');
      const sub = t.querySelector<HTMLButtonElement>('[data-active-receiver-segment="SUB"]');
      expect(main?.textContent?.trim()).toBe('M');
      expect(sub?.textContent?.trim()).toBe('S');
    });

    it('each segment has a descriptive aria-label', () => {
      const t = mountToggle({ active: 'MAIN', onChange: vi.fn() });
      const main = t.querySelector<HTMLButtonElement>('[data-active-receiver-segment="MAIN"]');
      const sub = t.querySelector<HTMLButtonElement>('[data-active-receiver-segment="SUB"]');
      expect(main?.getAttribute('aria-label')).toBe('MAIN receiver');
      expect(sub?.getAttribute('aria-label')).toBe('SUB receiver');
    });
  });

  describe('aria-checked / tabindex', () => {
    it('active=MAIN: MAIN segment is checked and focusable', () => {
      const t = mountToggle({ active: 'MAIN', onChange: vi.fn() });
      const main = t.querySelector<HTMLButtonElement>('[data-active-receiver-segment="MAIN"]')!;
      const sub = t.querySelector<HTMLButtonElement>('[data-active-receiver-segment="SUB"]')!;
      expect(main.getAttribute('aria-checked')).toBe('true');
      expect(sub.getAttribute('aria-checked')).toBe('false');
      expect(main.tabIndex).toBe(0);
      expect(sub.tabIndex).toBe(-1);
    });

    it('active=SUB: SUB segment is checked and focusable', () => {
      const t = mountToggle({ active: 'SUB', onChange: vi.fn() });
      const main = t.querySelector<HTMLButtonElement>('[data-active-receiver-segment="MAIN"]')!;
      const sub = t.querySelector<HTMLButtonElement>('[data-active-receiver-segment="SUB"]')!;
      expect(main.getAttribute('aria-checked')).toBe('false');
      expect(sub.getAttribute('aria-checked')).toBe('true');
      expect(main.tabIndex).toBe(-1);
      expect(sub.tabIndex).toBe(0);
    });

    it('unknown active receiver checks neither segment without inventing MAIN', () => {
      const t = mountToggle({ active: null, onChange: vi.fn() });
      const main = t.querySelector<HTMLButtonElement>('[data-active-receiver-segment="MAIN"]')!;
      const sub = t.querySelector<HTMLButtonElement>('[data-active-receiver-segment="SUB"]')!;
      expect(main.getAttribute('aria-checked')).toBe('false');
      expect(sub.getAttribute('aria-checked')).toBe('false');
      expect([main.tabIndex, sub.tabIndex]).toEqual([0, -1]);
    });
  });

  describe('onChange via click', () => {
    it('clicking inactive segment fires onChange with new value', () => {
      const onChange = vi.fn();
      const t = mountToggle({ active: 'MAIN', onChange });
      t.querySelector<HTMLButtonElement>('[data-active-receiver-segment="SUB"]')?.click();
      expect(onChange).toHaveBeenCalledOnce();
      expect(onChange).toHaveBeenCalledWith('SUB');
    });

    it('clicking the already-active segment does NOT fire onChange', () => {
      const onChange = vi.fn();
      const t = mountToggle({ active: 'MAIN', onChange });
      t.querySelector<HTMLButtonElement>('[data-active-receiver-segment="MAIN"]')?.click();
      expect(onChange).not.toHaveBeenCalled();
    });
  });

  describe('keyboard', () => {
    it('ArrowRight on MAIN moves selection to SUB', () => {
      const onChange = vi.fn();
      const t = mountToggle({ active: 'MAIN', onChange });
      const main = t.querySelector<HTMLButtonElement>('[data-active-receiver-segment="MAIN"]')!;
      main.dispatchEvent(new KeyboardEvent('keydown', { key: 'ArrowRight', bubbles: true }));
      expect(onChange).toHaveBeenCalledWith('SUB');
    });

    it('ArrowLeft on SUB moves selection to MAIN', () => {
      const onChange = vi.fn();
      const t = mountToggle({ active: 'SUB', onChange });
      const sub = t.querySelector<HTMLButtonElement>('[data-active-receiver-segment="SUB"]')!;
      sub.dispatchEvent(new KeyboardEvent('keydown', { key: 'ArrowLeft', bubbles: true }));
      expect(onChange).toHaveBeenCalledWith('MAIN');
    });

    it('ArrowLeft on MAIN wraps selection to SUB', () => {
      const onChange = vi.fn();
      const t = mountToggle({ active: 'MAIN', onChange });
      const main = t.querySelector<HTMLButtonElement>('[data-active-receiver-segment="MAIN"]')!;
      main.dispatchEvent(new KeyboardEvent('keydown', { key: 'ArrowLeft', bubbles: true }));
      expect(onChange).toHaveBeenCalledWith('SUB');
    });

    it('ArrowRight on SUB wraps selection to MAIN', () => {
      const onChange = vi.fn();
      const t = mountToggle({ active: 'SUB', onChange });
      const sub = t.querySelector<HTMLButtonElement>('[data-active-receiver-segment="SUB"]')!;
      sub.dispatchEvent(new KeyboardEvent('keydown', { key: 'ArrowRight', bubbles: true }));
      expect(onChange).toHaveBeenCalledWith('MAIN');
    });

    it('ArrowUp on MAIN wraps selection to SUB', () => {
      const onChange = vi.fn();
      const t = mountToggle({ active: 'MAIN', onChange });
      const main = t.querySelector<HTMLButtonElement>('[data-active-receiver-segment="MAIN"]')!;
      main.dispatchEvent(new KeyboardEvent('keydown', { key: 'ArrowUp', bubbles: true }));
      expect(onChange).toHaveBeenCalledWith('SUB');
    });

    it('ArrowDown on SUB wraps selection to MAIN', () => {
      const onChange = vi.fn();
      const t = mountToggle({ active: 'SUB', onChange });
      const sub = t.querySelector<HTMLButtonElement>('[data-active-receiver-segment="SUB"]')!;
      sub.dispatchEvent(new KeyboardEvent('keydown', { key: 'ArrowDown', bubbles: true }));
      expect(onChange).toHaveBeenCalledWith('MAIN');
    });

    it('Home selects first segment (MAIN)', () => {
      const onChange = vi.fn();
      const t = mountToggle({ active: 'SUB', onChange });
      const sub = t.querySelector<HTMLButtonElement>('[data-active-receiver-segment="SUB"]')!;
      sub.dispatchEvent(new KeyboardEvent('keydown', { key: 'Home', bubbles: true }));
      expect(onChange).toHaveBeenCalledWith('MAIN');
    });

    it('End selects last segment (SUB)', () => {
      const onChange = vi.fn();
      const t = mountToggle({ active: 'MAIN', onChange });
      const main = t.querySelector<HTMLButtonElement>('[data-active-receiver-segment="MAIN"]')!;
      main.dispatchEvent(new KeyboardEvent('keydown', { key: 'End', bubbles: true }));
      expect(onChange).toHaveBeenCalledWith('SUB');
    });

    it('Enter on focused segment selects it', () => {
      const onChange = vi.fn();
      const t = mountToggle({ active: 'MAIN', onChange });
      const sub = t.querySelector<HTMLButtonElement>('[data-active-receiver-segment="SUB"]')!;
      sub.dispatchEvent(new KeyboardEvent('keydown', { key: 'Enter', bubbles: true }));
      expect(onChange).toHaveBeenCalledWith('SUB');
    });

    it('Space on focused segment selects it', () => {
      const onChange = vi.fn();
      const t = mountToggle({ active: 'MAIN', onChange });
      const sub = t.querySelector<HTMLButtonElement>('[data-active-receiver-segment="SUB"]')!;
      sub.dispatchEvent(new KeyboardEvent('keydown', { key: ' ', bubbles: true }));
      expect(onChange).toHaveBeenCalledWith('SUB');
    });

    it('keeps keyboard focus inside the instance that received the key', async () => {
      const first = mountToggle({ active: 'MAIN', onChange: vi.fn() });
      const second = mountToggle({ active: 'MAIN', onChange: vi.fn() });
      const secondMain = second.querySelector<HTMLButtonElement>('[data-active-receiver-segment="MAIN"]')!;
      const secondSub = second.querySelector<HTMLButtonElement>('[data-active-receiver-segment="SUB"]')!;
      secondMain.focus();
      secondMain.dispatchEvent(new KeyboardEvent('keydown', { key: 'ArrowRight', bubbles: true }));
      await Promise.resolve();
      expect(document.activeElement).toBe(secondSub);
      expect(document.activeElement).not.toBe(
        first.querySelector('[data-active-receiver-segment="SUB"]'),
      );
    });
  });

  describe('availability', () => {
    it('omits a structurally absent segment', () => {
      const t = mountToggle({
        active: 'MAIN',
        onChange: vi.fn(),
        availability: {
          MAIN: { structural: true, operational: true },
          SUB: { structural: false, operational: false },
        },
      });
      expect(t.querySelector('[data-active-receiver-segment="MAIN"]')).not.toBeNull();
      expect(t.querySelector('[data-active-receiver-segment="SUB"]')).toBeNull();
    });

    it('keeps an operationally unavailable segment reasoned and inert', () => {
      const onChange = vi.fn();
      const t = mountToggle({
        active: 'MAIN',
        onChange,
        availability: {
          MAIN: { structural: true, operational: true },
          SUB: { structural: true, operational: false, reason: 'SUB is unavailable' },
        },
      });
      const sub = t.querySelector<HTMLButtonElement>('[data-active-receiver-segment="SUB"]')!;
      const reasonId = sub.getAttribute('aria-describedby');
      expect(sub.disabled).toBe(true);
      expect(sub.title).toBe('SUB is unavailable');
      expect(t.querySelector(`#${reasonId}`)?.textContent).toBe('SUB is unavailable');
      sub.disabled = false;
      sub.click();
      expect(onChange).not.toHaveBeenCalled();
    });

    it('keeps disabled-reason ids unique across mounted instances', () => {
      const props = {
        active: 'MAIN' as const,
        onChange: vi.fn(),
        availability: {
          SUB: { structural: true, operational: false, reason: 'SUB is unavailable' },
        },
      };
      const first = mountToggle(props);
      const second = mountToggle(props);
      const firstId = first.querySelector('[data-active-receiver-segment="SUB"]')
        ?.getAttribute('aria-describedby');
      const secondId = second.querySelector('[data-active-receiver-segment="SUB"]')
        ?.getAttribute('aria-describedby');
      expect(firstId).toBeTruthy();
      expect(secondId).toBeTruthy();
      expect(firstId).not.toBe(secondId);
    });
  });
});
