import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { mount, unmount, flushSync } from 'svelte';
import type { ComponentProps } from 'svelte';
import SegmentedButton from '../SegmentedButton.svelte';

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

const ATT_OPTIONS = [
  { value: 0,  label: '0dB'  },
  { value: 6,  label: '6dB'  },
  { value: 12, label: '12dB' },
  { value: 18, label: '18dB' },
];

const AGC_OPTIONS = [
  { value: 'OFF',  label: 'OFF'  },
  { value: 'FAST', label: 'FAST' },
  { value: 'MID',  label: 'MID'  },
  { value: 'SLOW', label: 'SLOW' },
];

function mountComponent(props: ComponentProps<typeof SegmentedButton>) {
  const target = document.createElement('div');
  document.body.appendChild(target);
  const component = mount(SegmentedButton, { target, props });
  flushSync();
  return { component, target };
}

function getContainer(target: HTMLElement) {
  return target.querySelector('.segmented-button') as HTMLElement;
}

function getSegments(target: HTMLElement) {
  return Array.from(target.querySelectorAll('.segment')) as HTMLButtonElement[];
}

function pressKey(el: HTMLElement, key: string) {
  el.dispatchEvent(new KeyboardEvent('keydown', { key, bubbles: true }));
}

// ---------------------------------------------------------------------------
// Setup / teardown
// ---------------------------------------------------------------------------

let cleanup: (() => void)[] = [];

beforeEach(() => { cleanup = []; });
afterEach(() => {
  cleanup.forEach(fn => fn());
  document.body.innerHTML = '';
});

function mountAndTrack(props: ComponentProps<typeof SegmentedButton>) {
  const result = mountComponent(props);
  cleanup.push(() => {
    unmount(result.component);
    result.target.remove();
  });
  return result;
}

// ---------------------------------------------------------------------------
// 1. Rendering
// ---------------------------------------------------------------------------

describe('rendering', () => {
  it('renders the correct number of segments', () => {
    const { target } = mountAndTrack({
      options: ATT_OPTIONS, selected: 0, onchange: vi.fn(),
    });
    expect(getSegments(target)).toHaveLength(4);
  });

  it('renders option labels as button text', () => {
    const { target } = mountAndTrack({
      options: ATT_OPTIONS, selected: 0, onchange: vi.fn(),
    });
    const labels = getSegments(target).map(b => b.textContent?.trim());
    expect(labels).toEqual(['0dB', '6dB', '12dB', '18dB']);
  });

  it('renders string-valued options correctly', () => {
    const { target } = mountAndTrack({
      options: AGC_OPTIONS, selected: 'OFF', onchange: vi.fn(),
    });
    const labels = getSegments(target).map(b => b.textContent?.trim());
    expect(labels).toEqual(['OFF', 'FAST', 'MID', 'SLOW']);
  });

  it('renders a single option with both rounded ends', () => {
    const { target } = mountAndTrack({
      options: [{ value: 'ON', label: 'ON' }],
      selected: 'ON', onchange: vi.fn(),
    });
    const segs = getSegments(target);
    expect(segs[0].classList.contains('first')).toBe(true);
    expect(segs[0].classList.contains('last')).toBe(true);
  });

  it('first segment has "first" class', () => {
    const { target } = mountAndTrack({
      options: ATT_OPTIONS, selected: 0, onchange: vi.fn(),
    });
    expect(getSegments(target)[0].classList.contains('first')).toBe(true);
  });

  it('last segment has "last" class', () => {
    const { target } = mountAndTrack({
      options: ATT_OPTIONS, selected: 0, onchange: vi.fn(),
    });
    const segs = getSegments(target);
    expect(segs[segs.length - 1].classList.contains('last')).toBe(true);
  });

  it('middle segments have neither "first" nor "last" class', () => {
    const { target } = mountAndTrack({
      options: ATT_OPTIONS, selected: 0, onchange: vi.fn(),
    });
    const segs = getSegments(target);
    expect(segs[1].classList.contains('first')).toBe(false);
    expect(segs[1].classList.contains('last')).toBe(false);
  });

  it('container has role="radiogroup"', () => {
    const { target } = mountAndTrack({
      options: ATT_OPTIONS, selected: 0, onchange: vi.fn(),
    });
    expect(getContainer(target).getAttribute('role')).toBe('radiogroup');
  });

  it('each segment has role="radio"', () => {
    const { target } = mountAndTrack({
      options: ATT_OPTIONS, selected: 0, onchange: vi.fn(),
    });
    getSegments(target).forEach(seg => {
      expect(seg.getAttribute('role')).toBe('radio');
    });
  });
});

// ---------------------------------------------------------------------------
// 2. Active state & ARIA
// ---------------------------------------------------------------------------

describe('active state', () => {
  it('selected segment has aria-checked="true"', () => {
    const { target } = mountAndTrack({
      options: ATT_OPTIONS, selected: 6, onchange: vi.fn(),
    });
    const segs = getSegments(target);
    expect(segs[1].getAttribute('aria-checked')).toBe('true');
  });

  it('non-selected segments have aria-checked="false"', () => {
    const { target } = mountAndTrack({
      options: ATT_OPTIONS, selected: 6, onchange: vi.fn(),
    });
    const segs = getSegments(target);
    expect(segs[0].getAttribute('aria-checked')).toBe('false');
    expect(segs[2].getAttribute('aria-checked')).toBe('false');
    expect(segs[3].getAttribute('aria-checked')).toBe('false');
  });

  it('selected segment has "active" class', () => {
    const { target } = mountAndTrack({
      options: ATT_OPTIONS, selected: 12, onchange: vi.fn(),
    });
    expect(getSegments(target)[2].classList.contains('active')).toBe(true);
  });

  it('non-selected segments do not have "active" class', () => {
    const { target } = mountAndTrack({
      options: ATT_OPTIONS, selected: 12, onchange: vi.fn(),
    });
    const segs = getSegments(target);
    expect(segs[0].classList.contains('active')).toBe(false);
    expect(segs[1].classList.contains('active')).toBe(false);
    expect(segs[3].classList.contains('active')).toBe(false);
  });

  it('works correctly with numeric value 0 (falsy)', () => {
    const { target } = mountAndTrack({
      options: ATT_OPTIONS, selected: 0, onchange: vi.fn(),
    });
    expect(getSegments(target)[0].getAttribute('aria-checked')).toBe('true');
    expect(getSegments(target)[1].getAttribute('aria-checked')).toBe('false');
  });

  it('works correctly with string values', () => {
    const { target } = mountAndTrack({
      options: AGC_OPTIONS, selected: 'MID', onchange: vi.fn(),
    });
    const segs = getSegments(target);
    expect(segs[2].getAttribute('aria-checked')).toBe('true');
    expect(segs[0].getAttribute('aria-checked')).toBe('false');
  });
});

// ---------------------------------------------------------------------------
// 3. Click events
// ---------------------------------------------------------------------------

describe('click events', () => {
  it('clicking a segment calls onchange with its value', () => {
    const onchange = vi.fn();
    const { target } = mountAndTrack({
      options: ATT_OPTIONS, selected: 0, onchange,
    });
    getSegments(target)[2].click();
    expect(onchange).toHaveBeenCalledOnce();
    expect(onchange).toHaveBeenCalledWith(12);
  });

  it('clicking the already-selected segment still calls onchange', () => {
    const onchange = vi.fn();
    const { target } = mountAndTrack({
      options: ATT_OPTIONS, selected: 6, onchange,
    });
    getSegments(target)[1].click();
    expect(onchange).toHaveBeenCalledWith(6);
  });

  it('clicking a string-valued segment calls onchange with that string', () => {
    const onchange = vi.fn();
    const { target } = mountAndTrack({
      options: AGC_OPTIONS, selected: 'OFF', onchange,
    });
    getSegments(target)[3].click();
    expect(onchange).toHaveBeenCalledWith('SLOW');
  });

  it('does not call onchange when disabled', () => {
    const onchange = vi.fn();
    const { target } = mountAndTrack({
      options: ATT_OPTIONS, selected: 0, onchange, disabled: true,
    });
    // pointer-events:none prevents real clicks, but simulate directly
    getSegments(target)[1].dispatchEvent(new MouseEvent('click', { bubbles: true }));
    expect(onchange).not.toHaveBeenCalled();
  });
});

// ---------------------------------------------------------------------------
// 4. Keyboard navigation
// ---------------------------------------------------------------------------

describe('keyboard navigation', () => {
  it('ArrowRight moves selection to the next option', () => {
    const onchange = vi.fn();
    const { target } = mountAndTrack({
      options: ATT_OPTIONS, selected: 0, onchange,
    });
    pressKey(getContainer(target), 'ArrowRight');
    expect(onchange).toHaveBeenCalledWith(6);
  });

  it('ArrowLeft moves selection to the previous option', () => {
    const onchange = vi.fn();
    const { target } = mountAndTrack({
      options: ATT_OPTIONS, selected: 12, onchange,
    });
    pressKey(getContainer(target), 'ArrowLeft');
    expect(onchange).toHaveBeenCalledWith(6);
  });

  it('ArrowRight wraps from last to first', () => {
    const onchange = vi.fn();
    const { target } = mountAndTrack({
      options: ATT_OPTIONS, selected: 18, onchange,
    });
    pressKey(getContainer(target), 'ArrowRight');
    expect(onchange).toHaveBeenCalledWith(0);
  });

  it('ArrowLeft wraps from first to last', () => {
    const onchange = vi.fn();
    const { target } = mountAndTrack({
      options: ATT_OPTIONS, selected: 0, onchange,
    });
    pressKey(getContainer(target), 'ArrowLeft');
    expect(onchange).toHaveBeenCalledWith(18);
  });

  it('ArrowDown behaves like ArrowRight', () => {
    const onchange = vi.fn();
    const { target } = mountAndTrack({
      options: ATT_OPTIONS, selected: 6, onchange,
    });
    pressKey(getContainer(target), 'ArrowDown');
    expect(onchange).toHaveBeenCalledWith(12);
  });

  it('ArrowUp behaves like ArrowLeft', () => {
    const onchange = vi.fn();
    const { target } = mountAndTrack({
      options: ATT_OPTIONS, selected: 12, onchange,
    });
    pressKey(getContainer(target), 'ArrowUp');
    expect(onchange).toHaveBeenCalledWith(6);
  });

  it('keyboard navigation works with string values', () => {
    const onchange = vi.fn();
    const { target } = mountAndTrack({
      options: AGC_OPTIONS, selected: 'FAST', onchange,
    });
    pressKey(getContainer(target), 'ArrowRight');
    expect(onchange).toHaveBeenCalledWith('MID');
  });

  it('unrelated keys do not call onchange', () => {
    const onchange = vi.fn();
    const { target } = mountAndTrack({
      options: ATT_OPTIONS, selected: 6, onchange,
    });
    pressKey(getContainer(target), 'Enter');
    pressKey(getContainer(target), ' ');
    pressKey(getContainer(target), 'Tab');
    expect(onchange).not.toHaveBeenCalled();
  });

  it('keyboard navigation is blocked when disabled', () => {
    const onchange = vi.fn();
    const { target } = mountAndTrack({
      options: ATT_OPTIONS, selected: 0, onchange, disabled: true,
    });
    pressKey(getContainer(target), 'ArrowRight');
    expect(onchange).not.toHaveBeenCalled();
  });

  // MOR-2512 — Ctrl/Alt/Meta+Arrow belongs to the global keyboard map
  // (Alt/Option+ArrowUp/Down adjust_af_level since MOR-2515); the widget
  // must neither step nor swallow the event so the window-level global
  // handler sees it.
  it('Ctrl+ArrowRight neither steps nor swallows the event', () => {
    const onchange = vi.fn();
    const { target } = mountAndTrack({
      options: ATT_OPTIONS, selected: 0, onchange,
    });
    const container = getContainer(target);
    container.focus();
    const event = new KeyboardEvent('keydown', {
      key: 'ArrowRight', ctrlKey: true, bubbles: true, cancelable: true,
    });
    container.dispatchEvent(event);

    expect(onchange).not.toHaveBeenCalled();
    expect(event.defaultPrevented).toBe(false);
  });

  it('Shift+ArrowRight still steps, and data-owns-arrows declares shift', () => {
    const onchange = vi.fn();
    const { target } = mountAndTrack({
      options: ATT_OPTIONS, selected: 0, onchange,
    });
    const container = getContainer(target);
    container.dispatchEvent(new KeyboardEvent('keydown', {
      key: 'ArrowRight', shiftKey: true, bubbles: true,
    }));

    expect(onchange).toHaveBeenCalledWith(6);
    expect(container.getAttribute('data-owns-arrows')).toBe('both shift');
  });
});

// ---------------------------------------------------------------------------
// 5. Disabled state
// ---------------------------------------------------------------------------

describe('disabled state', () => {
  it('container has "disabled" class when disabled', () => {
    const { target } = mountAndTrack({
      options: ATT_OPTIONS, selected: 0, onchange: vi.fn(), disabled: true,
    });
    expect(getContainer(target).classList.contains('disabled')).toBe(true);
  });

  it('container does not have "disabled" class when enabled', () => {
    const { target } = mountAndTrack({
      options: ATT_OPTIONS, selected: 0, onchange: vi.fn(), disabled: false,
    });
    expect(getContainer(target).classList.contains('disabled')).toBe(false);
  });

  it('container tabindex is -1 when disabled', () => {
    const { target } = mountAndTrack({
      options: ATT_OPTIONS, selected: 0, onchange: vi.fn(), disabled: true,
    });
    expect(getContainer(target).getAttribute('tabindex')).toBe('-1');
  });

  it('container tabindex is 0 when enabled', () => {
    const { target } = mountAndTrack({
      options: ATT_OPTIONS, selected: 0, onchange: vi.fn(),
    });
    expect(getContainer(target).getAttribute('tabindex')).toBe('0');
  });
});

// ---------------------------------------------------------------------------
// 6. Compact mode
// ---------------------------------------------------------------------------

describe('compact mode', () => {
  it('container has "compact" class when compact=true', () => {
    const { target } = mountAndTrack({
      options: ATT_OPTIONS, selected: 0, onchange: vi.fn(), compact: true,
    });
    expect(getContainer(target).classList.contains('compact')).toBe(true);
  });

  it('container does not have "compact" class by default', () => {
    const { target } = mountAndTrack({
      options: ATT_OPTIONS, selected: 0, onchange: vi.fn(),
    });
    expect(getContainer(target).classList.contains('compact')).toBe(false);
  });
});

// ---------------------------------------------------------------------------
// 7. Accent color
// ---------------------------------------------------------------------------

describe('accent color', () => {
  it('sets --accent CSS custom property on container', () => {
    const { target } = mountAndTrack({
      options: ATT_OPTIONS, selected: 0, onchange: vi.fn(), accentColor: '#FF6B35',
    });
    expect(getContainer(target).getAttribute('style')).toContain('--accent: #FF6B35');
  });

  it('uses default cyan #00D4FF when accentColor is not provided', () => {
    const { target } = mountAndTrack({
      options: ATT_OPTIONS, selected: 0, onchange: vi.fn(),
    });
    expect(getContainer(target).getAttribute('style')).toContain('--accent: var(--v2-accent-cyan)');
  });

  it('applies a different accent color (green)', () => {
    const { target } = mountAndTrack({
      options: ATT_OPTIONS, selected: 0, onchange: vi.fn(), accentColor: '#00FF88',
    });
    expect(getContainer(target).getAttribute('style')).toContain('--accent: #00FF88');
  });
});

// ---------------------------------------------------------------------------
// 8. Indicator styles
// ---------------------------------------------------------------------------

describe('indicator styles', () => {
  it('defaults each segment to the backward-compatible ring indicator', () => {
    const { target } = mountAndTrack({
      options: ATT_OPTIONS, selected: 6, onchange: vi.fn(),
    });
    getSegments(target).forEach((segment) => {
      expect(segment.getAttribute('data-indicator-style')).toBe('ring');
    });
  });

  it('applies the configured indicator style to each segment', () => {
    const { target } = mountAndTrack({
      options: ATT_OPTIONS, selected: 6, onchange: vi.fn(), indicatorStyle: 'edge-bottom',
    });
    getSegments(target).forEach((segment) => {
      expect(segment.getAttribute('data-indicator-style')).toBe('edge-bottom');
    });
  });

  it('applies named indicator color tokens as data attributes', () => {
    const { target } = mountAndTrack({
      options: ATT_OPTIONS, selected: 6, onchange: vi.fn(), indicatorColor: 'amber',
    });
    getSegments(target).forEach((segment) => {
      expect(segment.getAttribute('data-indicator-color')).toBe('amber');
    });
  });

  it('uses a custom indicator color via --indicator-color CSS variable', () => {
    const { target } = mountAndTrack({
      options: ATT_OPTIONS,
      selected: 6,
      onchange: vi.fn(),
      indicatorColor: '#ff88cc',
    });
    expect(getContainer(target).getAttribute('style')).toContain('--indicator-color: #ff88cc');
    getSegments(target).forEach((segment) => {
      expect(segment.hasAttribute('data-indicator-color')).toBe(false);
    });
  });
});

// ---------------------------------------------------------------------------
// 9. Surface variants
// ---------------------------------------------------------------------------

describe('surface variants', () => {
  it('keeps the default flat surface backward-compatible', () => {
    const { target } = mountAndTrack({
      options: ATT_OPTIONS, selected: 0, onchange: vi.fn(),
    });
    getSegments(target).forEach((segment) => {
      expect(segment.hasAttribute('data-surface')).toBe(false);
    });
  });

  it('applies the hardware surface to each segment when requested', () => {
    const { target } = mountAndTrack({
      options: ATT_OPTIONS, selected: 0, onchange: vi.fn(), surface: 'hardware',
    });
    getSegments(target).forEach((segment) => {
      expect(segment.getAttribute('data-surface')).toBe('hardware');
    });
  });
});

// ---------------------------------------------------------------------------
// 10. Pill variant
// ---------------------------------------------------------------------------

describe('pill variant', () => {
  it('adds the pill class to each segment when pill=true', () => {
    const { target } = mountAndTrack({
      options: ATT_OPTIONS, selected: 0, onchange: vi.fn(), pill: true,
    });
    getSegments(target).forEach((segment) => {
      expect(segment.classList.contains('v2-control-button--pill')).toBe(true);
    });
  });

  it('does not add the pill class by default', () => {
    const { target } = mountAndTrack({
      options: ATT_OPTIONS, selected: 0, onchange: vi.fn(),
    });
    getSegments(target).forEach((segment) => {
      expect(segment.classList.contains('v2-control-button--pill')).toBe(false);
    });
  });
});
