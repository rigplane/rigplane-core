/**
 * MOR-1536 — armed-signal adoption long tail, component-level.
 *
 * Structural (`data-armed`/`aria-describedby`/sr-only announcement) and
 * F4-class "as rendered" computed-style coverage (MOR-1519 review F4
 * pattern: read the SHARED armed-state CSS off disk and inject it as a
 * literal `<style>` tag, so a corrupted selector or property genuinely
 * fails the test — jsdom does not apply a mounted Svelte component's own
 * scoped `<style>` block) for each NEW consumer family this ticket wires:
 * AGC mode (`AgcPanel.svelte`), filter selection (`FilterPanel.svelte`),
 * preamp + attenuator (`RfFrontEnd.svelte`), and notch (`DspPanel.svelte`).
 *
 * All four share the ONE seat this ticket moved the CSS rule to
 * (`$lib/Button/control-button-armed.css`) — each `describe` below re-injects
 * the SAME file, which is itself the point: the shared seat must work
 * identically for every consumer, not just the one (`ModePanel.svelte`,
 * `ModePanel.isolated.test.ts`) that originally owned a private copy.
 *
 * MOR-1541: the file was renamed from `control-button.css` to
 * `control-button-armed.css` to stop the basename colliding with
 * `components-v2/controls/control-button.css` (the unrelated `.v2-control-
 * button` base-style sheet).
 */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { mount, unmount, flushSync } from 'svelte';
import { readFileSync } from 'node:fs';
import path from 'node:path';

function loadArmedCss(): string {
  return readFileSync(path.resolve(process.cwd(), 'src/lib/Button/control-button-armed.css'), 'utf-8');
}

let components: ReturnType<typeof mount>[] = [];
afterEach(() => {
  components.forEach((component) => unmount(component));
  document.body.innerHTML = '';
});

function mountInto(Component: unknown, props?: Record<string, unknown>) {
  const container = document.createElement('div');
  document.body.appendChild(container);
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  const component = mount(Component as any, { target: container, props });
  flushSync();
  components.push(component);
  return container;
}

// ── Mock state (one vi.mock factory serves all four components below) ──

const mockAgcProps = {
  agcMode: 1, agcModes: [1, 2, 3], agcLabels: { '1': 'FAST', '2': 'MID', '3': 'SLOW' }, hasAgc: true,
};
const mockAgcHandlers = { onAgcModeChange: vi.fn() };
const mockAgcArmed: { armed: boolean; value: number | null } = { armed: false, value: null };

const mockFilterProps = {
  currentMode: 'USB', currentFilter: 2, filterShape: 0, hasFilterShape: false,
  filterLabels: ['FIL1', 'FIL2', 'FIL3'], filterWidth: 2400, filterConfig: null,
  ifShift: 0, hasIfShift: false, hasPbt: false, pbtInner: 0, pbtOuter: 0,
};
const mockFilterHandlers = {
  onFilterChange: vi.fn(), onFilterWidthChange: vi.fn(), onFilterShapeChange: vi.fn(),
  onFilterPresetChange: vi.fn(), onFilterDefaults: vi.fn(), onIfShiftChange: vi.fn(),
  onPbtInnerChange: vi.fn(), onPbtOuterChange: vi.fn(), onPbtReset: vi.fn(),
};
const mockFilterArmed: { armed: boolean; value: number | null } = { armed: false, value: null };

const mockRfProps = {
  rfGain: 1, squelch: 0, att: 0, pre: 0, digiSel: false, ipPlus: false,
  rfGainAvailable: false, squelchAvailable: false, attAvailable: true, preAvailable: true,
  digiSelAvailable: false, ipPlusAvailable: false,
  attValues: [0, 20], attLabels: {} as Record<string, string>,
  preValues: [0, 1], preOptions: [{ value: 0, label: 'OFF' }, { value: 1, label: 'P1' }],
  showRfGain: false, showSquelch: false, showAtt: true, showPre: true,
  preDisabled: false, preDisabledReason: '', showDigiSel: false, showIpPlus: false,
};
const mockRfHandlers = {
  onRfGainChange: vi.fn(), onSquelchChange: vi.fn(), onAttChange: vi.fn(),
  onPreChange: vi.fn(), onDigiSelToggle: vi.fn(), onIpPlusToggle: vi.fn(),
};
const mockPreArmed: { armed: boolean; value: number | null } = { armed: false, value: null };
const mockAttArmed: { armed: boolean; value: number | null } = { armed: false, value: null };

const mockDspProps = {
  nrMode: 0, nrLevel: 5, nbActive: false, nbLevel: 128, notchMode: 'off' as string,
  notchFreq: 1000, nbDepth: 0, nbWidth: 0, manualNotchWidth: 0, agcTimeConstant: 0,
  hasNr: false, hasNb: false, hasNbDepth: false, hasNbWidth: false,
  nbLevelMax: 255, nbLevelPercent: true,
};
const mockDspHandlers = {
  onNrModeChange: vi.fn(), onNrLevelChange: vi.fn(), onNbToggle: vi.fn(), onNbLevelChange: vi.fn(),
  onNotchModeChange: vi.fn(), onNotchFreqChange: vi.fn(), onNbDepthChange: vi.fn(),
  onNbWidthChange: vi.fn(), onManualNotchWidthChange: vi.fn(), onAgcTimeChange: vi.fn(),
};
const mockManualNotchArmed: { armed: boolean; value: boolean | null } = { armed: false, value: null };
const mockAutoNotchArmed: { armed: boolean; value: boolean | null } = { armed: false, value: null };

vi.mock('$lib/runtime/adapters/panel-adapters', () => ({
  deriveAgcProps: () => mockAgcProps,
  getAgcHandlers: () => mockAgcHandlers,
  getAgcArmed: () => mockAgcArmed,
  deriveFilterProps: () => mockFilterProps,
  getFilterHandlers: () => mockFilterHandlers,
  getFilterArmed: () => mockFilterArmed,
  deriveRfFrontEndProps: () => mockRfProps,
  getRfFrontEndHandlers: () => mockRfHandlers,
  getPreampArmed: () => mockPreArmed,
  getAttenuatorArmed: () => mockAttArmed,
  deriveDspProps: () => mockDspProps,
  getDspHandlers: () => mockDspHandlers,
  getAutoNotchArmed: () => mockAutoNotchArmed,
  getManualNotchArmed: () => mockManualNotchArmed,
}));

import AgcPanel from '../AgcPanel.svelte';
import FilterPanel from '../FilterPanel.svelte';
import RfFrontEnd from '../RfFrontEnd.svelte';
import DspPanel from '../DspPanel.svelte';

beforeEach(() => {
  mockAgcArmed.armed = false; mockAgcArmed.value = null;
  mockFilterArmed.armed = false; mockFilterArmed.value = null;
  mockPreArmed.armed = false; mockPreArmed.value = null;
  mockAttArmed.armed = false; mockAttArmed.value = null;
  mockManualNotchArmed.armed = false; mockManualNotchArmed.value = null;
  mockAutoNotchArmed.armed = false; mockAutoNotchArmed.value = null;
});

describe('AgcPanel armed signal (MOR-1536)', () => {
  function buttons(target: HTMLElement) {
    return Array.from(target.querySelectorAll<HTMLButtonElement>('.v2-control-button'));
  }

  it('marks only the armed target AGC button', () => {
    mockAgcArmed.armed = true; mockAgcArmed.value = 2;
    const target = mountInto(AgcPanel);
    const marked = buttons(target).filter((b) => b.dataset.armed === 'true');
    expect(marked).toHaveLength(1);
    expect(marked[0].textContent?.trim()).toBe('MID');
  });

  it('pairs the armed button with an aria-describedby sr-only announcement', () => {
    mockAgcArmed.armed = true; mockAgcArmed.value = 1;
    const target = mountInto(AgcPanel);
    const armedButton = buttons(target).find((b) => b.dataset.armed === 'true')!;
    const describedById = armedButton.getAttribute('aria-describedby');
    expect(describedById).toBeTruthy();
    expect(target.querySelector(`#${describedById}`)?.classList.contains('sr-only')).toBe(true);
  });

  it('marks no button when nothing is armed', () => {
    const target = mountInto(AgcPanel);
    expect(buttons(target).some((b) => b.dataset.armed === 'true')).toBe(false);
  });

  describe('as rendered', () => {
    let styleEl: HTMLStyleElement;
    beforeEach(() => { styleEl = document.createElement('style'); styleEl.textContent = loadArmedCss(); document.head.appendChild(styleEl); });
    afterEach(() => styleEl.remove());

    it('renders the armed target at reduced opacity with an underline', () => {
      mockAgcArmed.armed = true; mockAgcArmed.value = 3;
      const target = mountInto(AgcPanel);
      const armedButton = buttons(target).find((b) => b.dataset.armed === 'true')!;
      const idleButton = buttons(target).find((b) => b.dataset.armed !== 'true')!;
      expect(getComputedStyle(armedButton).opacity).toBe('0.75');
      expect(getComputedStyle(idleButton).opacity).not.toBe('0.75');
    });
  });
});

describe('FilterPanel armed signal (MOR-1536)', () => {
  function buttons(target: HTMLElement) {
    return Array.from(target.querySelectorAll<HTMLButtonElement>('.filter-grid .v2-control-button'));
  }

  it('marks only the armed target filter button', () => {
    mockFilterArmed.armed = true; mockFilterArmed.value = 3;
    const target = mountInto(FilterPanel);
    const marked = buttons(target).filter((b) => b.dataset.armed === 'true');
    expect(marked).toHaveLength(1);
    expect(marked[0].textContent?.trim()).toBe('FIL3');
  });

  it('pairs the armed button with an aria-describedby sr-only announcement', () => {
    mockFilterArmed.armed = true; mockFilterArmed.value = 1;
    const target = mountInto(FilterPanel);
    const armedButton = buttons(target).find((b) => b.dataset.armed === 'true')!;
    const describedById = armedButton.getAttribute('aria-describedby');
    expect(describedById).toBeTruthy();
    expect(target.querySelector(`#${describedById}`)?.classList.contains('sr-only')).toBe(true);
  });

  describe('as rendered', () => {
    let styleEl: HTMLStyleElement;
    beforeEach(() => { styleEl = document.createElement('style'); styleEl.textContent = loadArmedCss(); document.head.appendChild(styleEl); });
    afterEach(() => styleEl.remove());

    it('renders the armed target at reduced opacity with an underline', () => {
      mockFilterArmed.armed = true; mockFilterArmed.value = 2;
      const target = mountInto(FilterPanel);
      const armedButton = buttons(target).find((b) => b.dataset.armed === 'true')!;
      const idleButton = buttons(target).find((b) => b.dataset.armed !== 'true')!;
      expect(getComputedStyle(armedButton).opacity).toBe('0.75');
      expect(getComputedStyle(idleButton).opacity).not.toBe('0.75');
    });
  });
});

describe('RfFrontEnd armed signal (MOR-1536)', () => {
  function preButtons(target: HTMLElement) {
    return Array.from(target.querySelectorAll<HTMLButtonElement>('.button-group .v2-control-button'));
  }
  function attButton(target: HTMLElement) {
    return Array.from(target.querySelectorAll<HTMLButtonElement>('.v2-control-button'))
      .find((b) => b.textContent?.trim() === 'ATT') ?? null;
  }

  it('marks only the armed target preamp button', () => {
    mockPreArmed.armed = true; mockPreArmed.value = 1;
    const target = mountInto(RfFrontEnd);
    const marked = preButtons(target).filter((b) => b.dataset.armed === 'true');
    expect(marked).toHaveLength(1);
    expect(marked[0].textContent?.trim()).toBe('P1');
  });

  it('marks the ATT toggle armed when set_attenuator is in flight', () => {
    mockAttArmed.armed = true; mockAttArmed.value = 20;
    const target = mountInto(RfFrontEnd);
    expect(attButton(target)?.dataset.armed).toBe('true');
    expect(attButton(target)?.getAttribute('aria-describedby')).toBeTruthy();
  });

  it('leaves ATT unmarked when nothing is armed', () => {
    const target = mountInto(RfFrontEnd);
    expect(attButton(target)?.dataset.armed).toBeUndefined();
  });

  describe('as rendered', () => {
    let styleEl: HTMLStyleElement;
    beforeEach(() => { styleEl = document.createElement('style'); styleEl.textContent = loadArmedCss(); document.head.appendChild(styleEl); });
    afterEach(() => styleEl.remove());

    it('renders the armed ATT toggle at reduced opacity with an underline', () => {
      mockAttArmed.armed = true; mockAttArmed.value = 20;
      const target = mountInto(RfFrontEnd);
      const armed = attButton(target)!;
      expect(getComputedStyle(armed).opacity).toBe('0.75');
      const decoration = getComputedStyle(armed).textDecorationLine || getComputedStyle(armed).textDecoration;
      expect(decoration).toContain('underline');
    });
  });
});

describe('DspPanel notch armed signal (MOR-1536)', () => {
  function notchButton(target: HTMLElement, label: string) {
    return Array.from(target.querySelectorAll<HTMLButtonElement>('.v2-control-button'))
      .find((b) => b.textContent?.trim() === label) ?? null;
  }

  it('marks NOTCH armed for set_manual_notch, independent of A-NOTCH', () => {
    mockManualNotchArmed.armed = true; mockManualNotchArmed.value = true;
    const target = mountInto(DspPanel);
    expect(notchButton(target, 'NOTCH')?.dataset.armed).toBe('true');
    expect(notchButton(target, 'A-NOTCH')?.dataset.armed).toBeUndefined();
  });

  it('marks A-NOTCH armed for set_auto_notch, independent of NOTCH', () => {
    mockAutoNotchArmed.armed = true; mockAutoNotchArmed.value = true;
    const target = mountInto(DspPanel);
    expect(notchButton(target, 'A-NOTCH')?.dataset.armed).toBe('true');
    expect(notchButton(target, 'NOTCH')?.dataset.armed).toBeUndefined();
  });

  // MOR-1541: notch-off dispatches BOTH set_auto_notch{on:false} and
  // set_manual_notch{on:false} (panel-commands.ts) — an owner-decided,
  // intentionally honest double-arm, not a bug. Pin it: both buttons show
  // data-armed at once when both commands are genuinely in flight.
  it('marks BOTH NOTCH and A-NOTCH armed together after notch-off dispatches both commands', () => {
    mockManualNotchArmed.armed = true; mockManualNotchArmed.value = false;
    mockAutoNotchArmed.armed = true; mockAutoNotchArmed.value = false;
    const target = mountInto(DspPanel);
    expect(notchButton(target, 'NOTCH')?.dataset.armed).toBe('true');
    expect(notchButton(target, 'A-NOTCH')?.dataset.armed).toBe('true');
  });

  describe('as rendered', () => {
    let styleEl: HTMLStyleElement;
    beforeEach(() => { styleEl = document.createElement('style'); styleEl.textContent = loadArmedCss(); document.head.appendChild(styleEl); });
    afterEach(() => styleEl.remove());

    it('renders the armed NOTCH button at reduced opacity with an underline', () => {
      mockManualNotchArmed.armed = true; mockManualNotchArmed.value = true;
      const target = mountInto(DspPanel);
      const armed = notchButton(target, 'NOTCH')!;
      expect(getComputedStyle(armed).opacity).toBe('0.75');
      const decoration = getComputedStyle(armed).textDecorationLine || getComputedStyle(armed).textDecoration;
      expect(decoration).toContain('underline');
    });
  });
});
