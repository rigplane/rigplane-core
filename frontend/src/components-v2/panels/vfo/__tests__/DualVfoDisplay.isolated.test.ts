import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { mount, unmount, flushSync } from 'svelte';
import type { ComponentProps } from 'svelte';

// Mock capabilities for VfoPanel internals.
vi.mock('$lib/stores/capabilities.svelte', () => ({
  vfoLabel: vi.fn((slot: 'A' | 'B') => (slot === 'A' ? 'MAIN' : 'SUB')),
  receiverLabel: vi.fn((id: 'MAIN' | 'SUB') => id),
  vfoSlotLabel: vi.fn((slot: 'A' | 'B') => (slot === 'A' ? 'VFO A' : 'VFO B')),
  getCapabilities: vi.fn(() => ({
    freqRanges: [
      {
        start: 14000000,
        end: 14350000,
        bands: [{ name: '20m', start: 14000000, end: 14350000, default: 14074000 }],
      },
    ],
  })),
  hasDualReceiver: vi.fn(() => true),
  getSmeterCalibration: vi.fn(() => null),
  getSmeterRedline: vi.fn(() => null),
}));

import DualVfoDisplay from '../DualVfoDisplay.svelte';
import type { VfoStateProps } from '../../../layout/layout-utils';

const mainVfo: VfoStateProps = {
  receiver: 'main',
  freq: 14074000,
  mode: 'USB',
  filter: 'FIL1',
  sValue: 50,
  isActive: true,
  badges: {},
};

const subVfo: VfoStateProps = {
  receiver: 'sub',
  freq: 7074000,
  mode: 'LSB',
  filter: 'FIL1',
  sValue: 30,
  isActive: false,
  badges: {},
};

let components: ReturnType<typeof mount>[] = [];

function mountDisplay(props: ComponentProps<typeof DualVfoDisplay>) {
  const t = document.createElement('div');
  document.body.appendChild(t);
  const component = mount(DualVfoDisplay, { target: t, props });
  flushSync();
  components.push(component);
  return t;
}

beforeEach(() => {
  components = [];
});

afterEach(() => {
  components.forEach((c) => unmount(c));
  document.body.innerHTML = '';
});

describe('DualVfoDisplay', () => {
  describe('structure', () => {
    it('mounts with two tiles', () => {
      const t = mountDisplay({ main: mainVfo, sub: subVfo, active: 'MAIN' });
      const tiles = t.querySelectorAll('.dual-vfo-tile');
      expect(tiles.length).toBe(2);
    });

    it('renders MAIN and SUB tiles with correct data-receiver', () => {
      const t = mountDisplay({ main: mainVfo, sub: subVfo, active: 'MAIN' });
      expect(t.querySelector('[data-receiver="main"]')).not.toBeNull();
      expect(t.querySelector('[data-receiver="sub"]')).not.toBeNull();
    });

    it('outer tiles are NOT role="button" (WCAG 4.1.2 — no nested interactive content)', () => {
      const t = mountDisplay({ main: mainVfo, sub: subVfo, active: 'MAIN' });
      const tiles = t.querySelectorAll('.dual-vfo-tile');
      tiles.forEach((tile) => {
        expect(tile.getAttribute('role')).toBeNull();
        expect(tile.getAttribute('tabindex')).toBeNull();
        expect(tile.getAttribute('aria-pressed')).toBeNull();
      });
    });
  });

  describe('active state', () => {
    it('active tile has is-active class (MAIN active)', () => {
      const t = mountDisplay({ main: mainVfo, sub: subVfo, active: 'MAIN' });
      const mainTile = t.querySelector('[data-receiver="main"]');
      const subTile = t.querySelector('[data-receiver="sub"]');
      expect(mainTile?.classList.contains('is-active')).toBe(true);
      expect(subTile?.classList.contains('is-active')).toBe(false);
    });

    it('active tile has is-active class (SUB active)', () => {
      const t = mountDisplay({
        main: { ...mainVfo, isActive: false },
        sub: { ...subVfo, isActive: true },
        active: 'SUB',
      });
      const mainTile = t.querySelector('[data-receiver="main"]');
      const subTile = t.querySelector('[data-receiver="sub"]');
      expect(mainTile?.classList.contains('is-active')).toBe(false);
      expect(subTile?.classList.contains('is-active')).toBe(true);
    });
  });

  describe('activate-chip removal (issue #825 Option B)', () => {
    it('no activate-chip button is rendered on either tile when MAIN is active', () => {
      const t = mountDisplay({ main: mainVfo, sub: subVfo, active: 'MAIN' });
      expect(t.querySelector('button[data-activate="main"]')).toBeNull();
      expect(t.querySelector('button[data-activate="sub"]')).toBeNull();
      expect(t.querySelector('.activate-chip')).toBeNull();
    });

    it('no activate-chip button is rendered on either tile when SUB is active', () => {
      const t = mountDisplay({
        main: { ...mainVfo, isActive: false },
        sub: { ...subVfo, isActive: true },
        active: 'SUB',
      });
      expect(t.querySelector('button[data-activate="main"]')).toBeNull();
      expect(t.querySelector('button[data-activate="sub"]')).toBeNull();
      expect(t.querySelector('.activate-chip')).toBeNull();
    });

    it('clicking the outer tile does NOT fire onActivate', () => {
      // Outer tile has never been interactive, and the inner chip is gone.
      const onActivate = vi.fn();
      const t = mountDisplay({ main: mainVfo, sub: subVfo, active: 'MAIN', onActivate });
      const subTile = t.querySelector<HTMLElement>('[data-receiver="sub"]');
      subTile?.click();
      expect(onActivate).not.toHaveBeenCalled();
    });
  });
});
