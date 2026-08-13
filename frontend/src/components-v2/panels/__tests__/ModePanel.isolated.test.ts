import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { mount, unmount, flushSync } from 'svelte';
import { readFileSync } from 'node:fs';
import path from 'node:path';

// MOR-1519 review F4: jsdom does NOT inject a mounted Svelte component's
// scoped/runtime-injected `<style>` block under this project's vitest
// config (documented precedent: `PullToRefresh.test.ts` — "jsdom doesn't
// compute scoped styles"). A plain `<style>` tag placed directly in the
// document, however, IS parsed and matched by jsdom's CSSOM (verified: a
// manually inserted rule targeting `[data-armed='true']` DOES resolve via
// `getComputedStyle`). So to get a test that actually fails if the visual
// affordance goes dark, this reads the REAL armed-state CSS off disk and
// injects it as a literal `<style>` element — a typo'd property, a deleted
// rule, or a wrong selector in the source file changes what gets injected
// here too, unlike a hand-copied expectation.
//
// MOR-1536: the rule moved from a `:global(...)` block inside
// `ModePanel.svelte` to the shared seat, `$lib/Button/control-button-armed.css`
// (imported once by `ControlButton.svelte`) — this now reads THAT file, a
// plain CSS file with no `:global(...)` wrapper to unwrap.
//
// MOR-1541: file renamed from `control-button.css` to
// `control-button-armed.css` (basename collided with the unrelated
// `components-v2/controls/control-button.css`).
function loadComponentCss(): string {
  // `process.cwd()` is `frontend/` under this project's vitest config (test
  // files run from the package root, not their own directory).
  return readFileSync(path.resolve(process.cwd(), 'src/lib/Button/control-button-armed.css'), 'utf-8');
}

const mockProps = {
  currentMode: 'USB',
  modes: ['USB', 'LSB', 'CW', 'CW-R', 'AM', 'FM', 'RTTY', 'RTTY-R', 'PSK', 'PSK-R'],
  dataMode: 0,
  hasDataMode: true,
  dataModeCount: 3,
  dataModeLabels: { '0': 'OFF', '1': 'D1', '2': 'D2', '3': 'D3' } as Record<string, string>,
  modInputSource: null as number | null,
  hasModInput: false,
};

const mockHandlers = {
  onModeChange: vi.fn(),
  onDataModeChange: vi.fn(),
  onModInputChange: vi.fn(),
};

// MOR-1519: the generic armed signal, default unarmed (matches
// `getModeArmed`'s real shape, `panel-adapters.ts`).
const mockArmed: { armed: boolean; value: string | null } = { armed: false, value: null };
// MOR-1536: DATA mode's own armed fact — a DIFFERENT intent (`set_data_mode`)
// than MODE's `set_mode` above, default unarmed.
const mockDataModeArmed: { armed: boolean; value: number | null } = { armed: false, value: null };

vi.mock('$lib/runtime/adapters/panel-adapters', () => ({
  deriveModeProps: () => mockProps,
  getModeHandlers: () => mockHandlers,
  getModeArmed: () => mockArmed,
  getDataModeArmed: () => mockDataModeArmed,
}));

import ModePanel from '../ModePanel.svelte';

let components: ReturnType<typeof mount>[] = [];

function mountPanel(
  overrides?: Partial<typeof mockProps>,
  armedOverride?: typeof mockArmed,
  dataModeArmedOverride?: typeof mockDataModeArmed,
) {
  if (overrides) Object.assign(mockProps, overrides);
  if (armedOverride) Object.assign(mockArmed, armedOverride);
  if (dataModeArmedOverride) Object.assign(mockDataModeArmed, dataModeArmedOverride);
  const target = document.createElement('div');
  document.body.appendChild(target);
  const component = mount(ModePanel, { target });
  flushSync();
  components.push(component);
  return target;
}

beforeEach(() => {
  components = [];
  mockProps.currentMode = 'USB';
  mockProps.modes = ['USB', 'LSB', 'CW', 'CW-R', 'AM', 'FM', 'RTTY', 'RTTY-R', 'PSK', 'PSK-R'];
  mockProps.dataMode = 0;
  mockProps.hasDataMode = true;
  mockProps.dataModeCount = 3;
  mockProps.dataModeLabels = { '0': 'OFF', '1': 'D1', '2': 'D2', '3': 'D3' };
  mockProps.modInputSource = null;
  mockProps.hasModInput = false;
  mockHandlers.onModeChange = vi.fn();
  mockHandlers.onDataModeChange = vi.fn();
  mockHandlers.onModInputChange = vi.fn();
  mockArmed.armed = false;
  mockArmed.value = null;
  mockDataModeArmed.armed = false;
  mockDataModeArmed.value = null;
});

afterEach(() => {
  components.forEach((component) => unmount(component));
  document.body.innerHTML = '';
});

describe('ModePanel', () => {
  it('renders mode buttons from capabilities', () => {
    const target = mountPanel();
    const buttons = Array.from(target.querySelectorAll<HTMLButtonElement>('.mode-grid .v2-control-button')).map((button) => button.textContent?.trim());
    expect(buttons).toEqual(['USB', 'LSB', 'CW', 'CW-R', 'RTTY', 'RTTY-R', 'PSK', 'PSK-R', 'AM', 'FM']);
  });

  it('highlights the active mode button', () => {
    const target = mountPanel({ currentMode: 'CW' });
    const buttons = Array.from(target.querySelectorAll<HTMLButtonElement>('.mode-grid .v2-control-button'));
    const button = buttons.find((b) => b.textContent?.trim() === 'CW');
    expect(button?.dataset.active).toBe('true');
  });

  // ── MOR-1519: generic armed signal, structural marker on the mode grid ──
  describe('armed signal (MOR-1519)', () => {
    // Review F1: the marker must sit ON the button element itself (rendered
    // by `ControlButton`, no wrapper) — an attribute selector can't reach a
    // wrapper, and inherited `font-style` is beaten by the UA button
    // stylesheet.
    function buttonOf(target: HTMLElement, label: string): HTMLButtonElement | null {
      const buttons = Array.from(target.querySelectorAll<HTMLButtonElement>('.mode-grid .v2-control-button'));
      return buttons.find((b) => b.textContent?.trim() === label) ?? null;
    }

    it('marks only the armed target button with the structural data-armed attribute', () => {
      const target = mountPanel(undefined, { armed: true, value: 'CW' });
      expect(buttonOf(target, 'CW')?.dataset.armed).toBe('true');
      expect(buttonOf(target, 'USB')?.dataset.armed).toBeUndefined();
      expect(buttonOf(target, 'LSB')?.dataset.armed).toBeUndefined();
    });

    it('marks no button when nothing is armed', () => {
      const target = mountPanel(undefined, { armed: false, value: null });
      const marked = target.querySelectorAll('.mode-grid .v2-control-button[data-armed]');
      expect(marked).toHaveLength(0);
    });

    it('never presents the armed target as confirmed: active tracks currentMode only', () => {
      // currentMode stays USB (confirmed) while CW is armed (in flight) —
      // the armed button must NOT also read data-active='true'.
      const target = mountPanel({ currentMode: 'USB' }, { armed: true, value: 'CW' });
      const cwButton = buttonOf(target, 'CW');
      const usbButton = buttonOf(target, 'USB');
      expect(cwButton?.dataset.active).toBe('false');
      expect(usbButton?.dataset.active).toBe('true');
      expect(cwButton?.dataset.armed).toBe('true');
    });

    // Review F3: `data-*` carries no AT semantics on its own — the armed
    // button must be paired with an `aria-describedby` announcement (same
    // pattern as `DspSurface.svelte`'s pending-toggle `.sr-only` span).
    it('pairs the armed button with an aria-describedby sr-only announcement', () => {
      const target = mountPanel(undefined, { armed: true, value: 'CW' });
      const cwButton = buttonOf(target, 'CW')!;
      const describedById = cwButton.getAttribute('aria-describedby');
      expect(describedById).toBeTruthy();
      const announcement = target.querySelector(`#${describedById}`);
      expect(announcement).not.toBeNull();
      expect(announcement?.classList.contains('sr-only')).toBe(true);
      expect(announcement?.textContent).toBeTruthy();

      // Unarmed buttons carry no aria-describedby at all.
      expect(buttonOf(target, 'USB')?.getAttribute('aria-describedby')).toBeNull();
    });

    // Review F4 (test honesty): a test that FAILS if the visual channel is
    // invisible. `opacity` is the PRIMARY channel (review F2 — italic alone
    // computes but does not render given this build's vendored font +
    // `app.css`'s `font-synthesis: none`), so it must be the one this test
    // actually measures via `getComputedStyle`, not merely the attribute.
    describe('as rendered (real component CSS injected, see loadComponentCss above)', () => {
      let styleEl: HTMLStyleElement;

      beforeEach(() => {
        styleEl = document.createElement('style');
        styleEl.textContent = loadComponentCss();
        document.head.appendChild(styleEl);
      });

      afterEach(() => {
        styleEl.remove();
      });

      it('renders the armed target at reduced opacity', () => {
        const target = mountPanel(undefined, { armed: true, value: 'CW' });
        const cwButton = buttonOf(target, 'CW')!;
        const usbButton = buttonOf(target, 'USB')!;
        expect(getComputedStyle(cwButton).opacity).toBe('0.75');
        // Sibling, unarmed button must NOT pick up the same style.
        expect(getComputedStyle(usbButton).opacity).not.toBe('0.75');
      });

      it('renders the armed target with an underline (font-independent structural backstop)', () => {
        const target = mountPanel(undefined, { armed: true, value: 'CW' });
        const cwButton = buttonOf(target, 'CW')!;
        const usbButton = buttonOf(target, 'USB')!;
        const cwDecoration = getComputedStyle(cwButton).textDecorationLine || getComputedStyle(cwButton).textDecoration;
        const usbDecoration = getComputedStyle(usbButton).textDecorationLine || getComputedStyle(usbButton).textDecoration;
        expect(cwDecoration).toContain('underline');
        expect(usbDecoration).not.toContain('underline');
      });
    });
  });

  // ── MOR-1536: DATA mode's own armed signal (adoption long tail) ──
  describe('DATA mode armed signal (MOR-1536)', () => {
    function dataButtonOf(target: HTMLElement, label: string): HTMLButtonElement | null {
      const buttons = Array.from(target.querySelectorAll<HTMLButtonElement>('.data-grid .v2-control-button'));
      return buttons.find((b) => b.textContent?.trim() === label) ?? null;
    }

    it('marks only the armed target DATA option (>2-value grid)', () => {
      const target = mountPanel(undefined, undefined, { armed: true, value: 2 });
      expect(dataButtonOf(target, 'D2')?.dataset.armed).toBe('true');
      expect(dataButtonOf(target, 'OFF')?.dataset.armed).toBeUndefined();
      expect(dataButtonOf(target, 'D1')?.dataset.armed).toBeUndefined();
    });

    it('pairs the armed DATA option with an aria-describedby sr-only announcement', () => {
      const target = mountPanel(undefined, undefined, { armed: true, value: 2 });
      const button = dataButtonOf(target, 'D2')!;
      const describedById = button.getAttribute('aria-describedby');
      expect(describedById).toBeTruthy();
      const announcement = target.querySelector(`#${describedById}`);
      expect(announcement?.classList.contains('sr-only')).toBe(true);
      expect(announcement?.textContent).toBeTruthy();
    });

    it('marks the single DATA toggle button (2-value case) when armed', () => {
      const target = mountPanel({ dataModeCount: 1 }, undefined, { armed: true, value: 1 });
      const button = target.querySelector<HTMLButtonElement>('.panel-body > .v2-control-button');
      expect(button?.dataset.armed).toBe('true');
      expect(button?.getAttribute('aria-describedby')).toBeTruthy();
    });

    it('marks no DATA button when nothing is armed', () => {
      const target = mountPanel(undefined, undefined, { armed: false, value: null });
      expect(target.querySelectorAll('.data-grid .v2-control-button[data-armed]')).toHaveLength(0);
    });

    // Same F4-class test-honesty pattern as MODE's armed CSS above — reuses
    // the SAME shared `control-button-armed.css` rule (that is the point of
    // MOR-1536's styling-seat move), so this proves the shared seat truly
    // applies to a SECOND consumer within the same file, not just to MODE.
    describe('as rendered (real component CSS injected, see loadComponentCss above)', () => {
      let styleEl: HTMLStyleElement;

      beforeEach(() => {
        styleEl = document.createElement('style');
        styleEl.textContent = loadComponentCss();
        document.head.appendChild(styleEl);
      });

      afterEach(() => {
        styleEl.remove();
      });

      it('renders the armed DATA option at reduced opacity with an underline', () => {
        const target = mountPanel(undefined, undefined, { armed: true, value: 2 });
        const armedButton = dataButtonOf(target, 'D2')!;
        const unarmedButton = dataButtonOf(target, 'OFF')!;
        expect(getComputedStyle(armedButton).opacity).toBe('0.75');
        expect(getComputedStyle(unarmedButton).opacity).not.toBe('0.75');
        const decoration = getComputedStyle(armedButton).textDecorationLine || getComputedStyle(armedButton).textDecoration;
        expect(decoration).toContain('underline');
      });
    });
  });

  it('calls onModeChange when a mode button is clicked', () => {
    const target = mountPanel();
    const buttons = Array.from(target.querySelectorAll<HTMLButtonElement>('.mode-grid .v2-control-button'));
    buttons.find((b) => b.textContent?.trim() === 'LSB')?.click();
    flushSync();
    expect(mockHandlers.onModeChange).toHaveBeenCalledWith('LSB');
  });

  it('renders DATA mode controls when supported', () => {
    const target = mountPanel();
    const dataButtons = Array.from(target.querySelectorAll<HTMLButtonElement>('.data-grid .v2-control-button')).map((button) => button.textContent?.trim());
    expect(dataButtons).toEqual(['OFF', 'D1', 'D2', 'D3']);
  });

  it('does not render DATA mode controls when unsupported', () => {
    const target = mountPanel({ hasDataMode: false });
    expect(target.querySelector('.data-grid')).toBeNull();
  });

  it('calls onDataModeChange with numeric modes', () => {
    const target = mountPanel();
    const dataButtons = Array.from(target.querySelectorAll<HTMLButtonElement>('.data-grid .v2-control-button'));
    dataButtons.find((b) => b.textContent?.trim() === 'D3')?.click();
    flushSync();
    expect(mockHandlers.onDataModeChange).toHaveBeenCalledWith(3);
  });

  describe('MOD-input source control (MOR-616)', () => {
    function modInputSelect(target: HTMLElement): HTMLSelectElement | null {
      return target.querySelector<HTMLSelectElement>('[data-testid="mod-input-select"]');
    }

    it('renders the dropdown with all six sources and the current one selected', () => {
      const target = mountPanel({ hasModInput: true, modInputSource: 5 });
      const select = modInputSelect(target);
      expect(select).not.toBeNull();
      expect(select!.value).toBe('5');
      const labels = Array.from(select!.options)
        .filter((option) => option.value !== '')
        .map((option) => option.textContent?.trim());
      expect(labels).toEqual(['MIC', 'ACC', 'MIC+ACC', 'USB', 'MIC+USB', 'LAN']);
    });

    it('shows an empty placeholder before the first readback', () => {
      const target = mountPanel({ hasModInput: true, modInputSource: null });
      const select = modInputSelect(target);
      expect(select).not.toBeNull();
      expect(select!.value).toBe('');
    });

    it('is hidden when the radio does not expose MOD-input routing', () => {
      const target = mountPanel({ hasModInput: false, modInputSource: 3 });
      expect(modInputSelect(target)).toBeNull();
    });

    it('fires onModInputChange with the numeric source', () => {
      const target = mountPanel({ hasModInput: true, modInputSource: 0 });
      const select = modInputSelect(target)!;
      select.value = '5';
      select.dispatchEvent(new Event('change', { bubbles: true }));
      flushSync();
      expect(mockHandlers.onModInputChange).toHaveBeenCalledWith(5);
    });

    it('reflects external state changes in the selected value', () => {
      const target = mountPanel({ hasModInput: true, modInputSource: 0 });
      expect(modInputSelect(target)!.value).toBe('0');

      const updated = mountPanel({ modInputSource: 3 });
      expect(modInputSelect(updated)!.value).toBe('3');
    });
  });
});
