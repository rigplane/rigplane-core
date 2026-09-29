/**
 * MOR-2978 — legacy choice keys expose the confirmed selection and refuse
 * unread readings: mode, AGC, antenna and filter keys.
 *
 * Per key: a known reading renders `aria-pressed` "true" on the confirmed
 * key and "false" on the others (the omit-never-"false" rule `pressedOf`
 * pins for semantic surfaces); an unread reading renders no `aria-pressed`
 * and a disabled key, matching the shared handlers' refusal. One file on
 * purpose (the ticket's 10-file lease): the panels share one mocked adapter
 * seam, the same convention as `mor1536-armed-adoption.isolated.test.ts`.
 */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { mount, unmount, flushSync } from 'svelte';
import { setLocale } from '$lib/i18n';
import type { CommandScalarFeedback } from '../../../primitives/scalar/continuous-scalar.svelte';

// ── Mock state (one vi.mock factory serves all four panels) ──

const mockModeProps = {
  currentMode: 'USB',
  modes: ['USB', 'LSB'],
  dataMode: 1 as number | null,
  hasDataMode: true,
  dataModeCount: 1,
  dataModeLabels: { '0': 'OFF', '1': 'D1' } as Record<string, string>,
  modInputSource: null as number | null,
  modInputChoices: [] as readonly { readonly value: number; readonly label: string }[],
  hasModInput: false,
};
const mockModeHandlers = { onModeChange: vi.fn(), onDataModeChange: vi.fn(), onModInputChange: vi.fn() };

const mockAgcProps = {
  agcMode: 2,
  agcModes: [1, 2, 3],
  agcLabels: { '1': 'FAST', '2': 'MID', '3': 'SLOW' } as Record<string, string>,
  hasAgc: true,
};
const mockAgcHandlers = { onAgcModeChange: vi.fn() };

const mockAntennaProps = {
  txAntenna: 2 as number | null,
  rxAnt: true as boolean | null,
  antennaCount: 2,
  hasRxAntenna: true,
};
const mockAntennaHandlers = { onSelectAnt1: vi.fn(), onSelectAnt2: vi.fn(), onToggleRxAnt: vi.fn() };

const mockFilterProps = {
  currentMode: 'USB',
  currentFilter: 2 as number | null,
  filterShape: 1 as number | null,
  hasFilterShape: true,
  filterLabels: ['FIL1', 'FIL2', 'FIL3'],
  filterWidth: 2400,
  filterWidthMin: 50,
  filterWidthMax: 9999,
  filterConfig: {
    defaults: [3000, 2400, 1800], fixed: false, minHz: 50, maxHz: 3600, stepHz: 50,
  } as { defaults: number[]; fixed: boolean; minHz: number; maxHz: number; stepHz: number } | null,
  ifShift: 0,
  ifShiftDomain: null as object | null,
  hasIfShift: false,
  hasPbt: false,
  pbtInner: null as number | null,
  pbtOuter: null as number | null,
  pbtDomain: null as object | null,
};
const mockFilterHandlers = {
  onFilterChange: vi.fn(), onFilterWidthChange: vi.fn(), onFilterShapeChange: vi.fn(),
  onFilterPresetChange: vi.fn(), onFilterDefaults: vi.fn(), onIfShiftChange: vi.fn(),
  onPbtInnerChange: vi.fn(), onPbtOuterChange: vi.fn(), onPbtReset: vi.fn(),
};

function idleFeedback(control: string): Readonly<CommandScalarFeedback> {
  return {
    confirmed: 2400, target: null, requestedTarget: null, phase: 'idle', busy: false,
    availability: 'available', outcome: null, lifecycleId: null, transitionId: null,
    providerGeneration: 1, sessionEpoch: 7,
    scope: { control, receiver: 0 }, repeatPolicy: 'latest-target-wins',
  } as Readonly<CommandScalarFeedback>;
}

vi.mock('$lib/runtime/adapters/panel-adapters', () => ({
  deriveModeProps: () => mockModeProps,
  getModeHandlers: () => mockModeHandlers,
  getModeArmed: () => ({ armed: false, value: null }),
  getDataModeArmed: () => ({ armed: false, value: null }),
  deriveAgcProps: () => mockAgcProps,
  getAgcHandlers: () => mockAgcHandlers,
  getAgcArmed: () => ({ armed: false, value: null }),
  deriveAntennaProps: () => mockAntennaProps,
  getAntennaHandlers: () => mockAntennaHandlers,
  deriveFilterProps: () => ({ ...mockFilterProps }),
  getFilterHandlers: () => mockFilterHandlers,
  getFilterArmed: () => ({ armed: false, value: null }),
  getFilterShapeArmed: () => ({ armed: false, value: null }),
  getFilterShapeControlFeedback: () => idleFeedback('filter-shape'),
  getFilterWidthControlFeedback: () => idleFeedback('filter-width'),
  getPbtInnerHzControlFeedback: () => idleFeedback('pbt-inner'),
  getPbtOuterHzControlFeedback: () => idleFeedback('pbt-outer'),
  getIfShiftControlFeedback: () => idleFeedback('if-shift'),
}));

import ModePanel from '../ModePanel.svelte';
import AgcPanel from '../AgcPanel.svelte';
import AntennaPanel from '../AntennaPanel.svelte';
import FilterPanel from '../FilterPanel.svelte';

let components: ReturnType<typeof mount>[] = [];

beforeEach(() => {
  components = [];
  setLocale('en-US');
  Object.assign(mockModeProps, {
    currentMode: 'USB', modes: ['USB', 'LSB'], dataMode: 1, hasDataMode: true,
    dataModeCount: 1, dataModeLabels: { '0': 'OFF', '1': 'D1' },
    modInputSource: null, modInputChoices: [], hasModInput: false,
  });
  Object.assign(mockAgcProps, {
    agcMode: 2, agcModes: [1, 2, 3],
    agcLabels: { '1': 'FAST', '2': 'MID', '3': 'SLOW' }, hasAgc: true,
  });
  Object.assign(mockAntennaProps, { txAntenna: 2, rxAnt: true, antennaCount: 2, hasRxAntenna: true });
  Object.assign(mockFilterProps, { currentFilter: 2, filterShape: 1, hasFilterShape: true });
});

afterEach(() => {
  components.forEach((component) => unmount(component));
  document.body.innerHTML = '';
});

function mountPanel(Component: unknown): HTMLElement {
  const target = document.createElement('div');
  document.body.appendChild(target);
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  components.push(mount(Component as any, { target }));
  flushSync();
  return target;
}

function button(target: ParentNode, text: string): HTMLButtonElement {
  const found = Array.from(target.querySelectorAll<HTMLButtonElement>('button'))
    .find((b) => b.textContent?.trim() === text);
  if (!found) throw new Error(`button "${text}" not found`);
  return found;
}

function openFilterSettings(target: HTMLElement): HTMLElement {
  (target.querySelector('.settings-button') as HTMLButtonElement).click();
  flushSync();
  const modal = document.querySelector('.filter-modal') as HTMLElement | null;
  if (!modal) throw new Error('filter settings modal did not open');
  return modal;
}

// ── ModePanel ──

describe('ModePanel choice keys (MOR-2978)', () => {
  it('exposes the confirmed mode: "true" on USB, "false" on LSB, keys enabled', () => {
    const target = mountPanel(ModePanel);
    const usb = button(target, 'USB');
    const lsb = button(target, 'LSB');
    expect(usb.getAttribute('aria-pressed')).toBe('true');
    expect(lsb.getAttribute('aria-pressed')).toBe('false');
    expect(usb.disabled).toBe(false);
    expect(lsb.disabled).toBe(false);
  });

  it('omits aria-pressed and disables the mode keys over an unread mode', () => {
    mockModeProps.currentMode = '';
    const target = mountPanel(ModePanel);
    for (const label of ['USB', 'LSB']) {
      const key = button(target, label);
      expect(key.hasAttribute('aria-pressed')).toBe(false);
      expect(key.disabled).toBe(true);
    }
  });

  it('exposes the DATA toggle: "true" when on, "false" when off', () => {
    mockModeProps.dataMode = 1;
    expect(button(mountPanel(ModePanel), 'DATA').getAttribute('aria-pressed')).toBe('true');
    mockModeProps.dataMode = 0;
    const target = mountPanel(ModePanel);
    const key = button(target, 'DATA');
    expect(key.getAttribute('aria-pressed')).toBe('false');
    expect(key.disabled).toBe(false);
  });

  it('omits aria-pressed and disables the DATA toggle over an unread dataMode', () => {
    mockModeProps.dataMode = null;
    const key = button(mountPanel(ModePanel), 'DATA');
    expect(key.hasAttribute('aria-pressed')).toBe(false);
    expect(key.disabled).toBe(true);
  });

  it('exposes the DATA grid selection and refuses it while unread', () => {
    Object.assign(mockModeProps, {
      dataMode: 2, dataModeCount: 3,
      dataModeLabels: { '0': 'OFF', '1': 'D1', '2': 'D2', '3': 'D3' },
    });
    const known = mountPanel(ModePanel);
    expect(button(known, 'D2').getAttribute('aria-pressed')).toBe('true');
    expect(button(known, 'D1').getAttribute('aria-pressed')).toBe('false');
    expect(button(known, 'D2').disabled).toBe(false);
    mockModeProps.dataMode = null;
    const unread = mountPanel(ModePanel);
    for (const label of ['OFF', 'D1', 'D2', 'D3']) {
      const key = button(unread, label);
      expect(key.hasAttribute('aria-pressed')).toBe(false);
      expect(key.disabled).toBe(true);
    }
  });
});

// ── AgcPanel ──

describe('AgcPanel choice keys (MOR-2978)', () => {
  it('exposes the confirmed AGC mode: "true" on MID, "false" on the others, keys enabled', () => {
    const target = mountPanel(AgcPanel);
    expect(button(target, 'MID').getAttribute('aria-pressed')).toBe('true');
    expect(button(target, 'FAST').getAttribute('aria-pressed')).toBe('false');
    expect(button(target, 'SLOW').getAttribute('aria-pressed')).toBe('false');
    expect(button(target, 'MID').disabled).toBe(false);
  });

  it('omits aria-pressed and disables the AGC keys over an unread agcMode', () => {
    mockAgcProps.agcMode = Number.NaN;
    const target = mountPanel(AgcPanel);
    for (const label of ['FAST', 'MID', 'SLOW']) {
      const key = button(target, label);
      expect(key.hasAttribute('aria-pressed')).toBe(false);
      expect(key.disabled).toBe(true);
    }
  });
});

// ── AntennaPanel ──

describe('AntennaPanel choice keys (MOR-2978)', () => {
  it('exposes the confirmed TX port and RX state, keys enabled', () => {
    const target = mountPanel(AntennaPanel);
    expect(button(target, 'ANT2').getAttribute('aria-pressed')).toBe('true');
    expect(button(target, 'ANT1').getAttribute('aria-pressed')).toBe('false');
    expect(button(target, 'RX ANT').getAttribute('aria-pressed')).toBe('true');
    expect(button(target, 'ANT1').disabled).toBe(false);
    expect(button(target, 'RX ANT').disabled).toBe(false);
  });

  it('omits aria-pressed and disables the keys over unread readings', () => {
    mockAntennaProps.txAntenna = null;
    mockAntennaProps.rxAnt = null;
    const target = mountPanel(AntennaPanel);
    for (const label of ['ANT1', 'ANT2', 'RX ANT']) {
      const key = button(target, label);
      expect(key.hasAttribute('aria-pressed')).toBe(false);
      expect(key.disabled).toBe(true);
    }
  });
});

// ── FilterPanel ──

describe('FilterPanel choice keys (MOR-2978)', () => {
  it('exposes the confirmed filter: "true" on FIL2, "false" on the others, keys enabled', () => {
    const target = mountPanel(FilterPanel);
    expect(button(target, 'FIL2').getAttribute('aria-pressed')).toBe('true');
    expect(button(target, 'FIL1').getAttribute('aria-pressed')).toBe('false');
    expect(button(target, 'FIL3').getAttribute('aria-pressed')).toBe('false');
    expect(button(target, 'FIL2').disabled).toBe(false);
  });

  it('omits aria-pressed and disables the filter keys over an unread filter', () => {
    mockFilterProps.currentFilter = null;
    const target = mountPanel(FilterPanel);
    for (const label of ['FIL1', 'FIL2', 'FIL3']) {
      const key = button(target, label);
      expect(key.hasAttribute('aria-pressed')).toBe(false);
      expect(key.disabled).toBe(true);
    }
  });

  it('exposes the confirmed shape in the settings modal, keys enabled', () => {
    const modal = openFilterSettings(mountPanel(FilterPanel));
    const sharp = button(modal, 'SHARP');
    const soft = button(modal, 'SOFT');
    expect(soft.getAttribute('aria-pressed')).toBe('true');
    expect(sharp.getAttribute('aria-pressed')).toBe('false');
    expect(sharp.disabled).toBe(false);
    expect(soft.disabled).toBe(false);
  });

  it('omits aria-pressed and disables the shape keys over an unread filterShape', () => {
    mockFilterProps.filterShape = null;
    const modal = openFilterSettings(mountPanel(FilterPanel));
    for (const label of ['SHARP', 'SOFT']) {
      const key = button(modal, label);
      expect(key.hasAttribute('aria-pressed')).toBe(false);
      expect(key.disabled).toBe(true);
    }
  });
});
