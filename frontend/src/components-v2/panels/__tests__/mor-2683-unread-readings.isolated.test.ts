/**
 * MOR-2683 — no invented monitor / compressor / filter values for unread
 * readings, pinned per consumer at the render boundary.
 *
 * `toTxProps` now projects `Number.NaN` for an unreported compressor /
 * monitor level (the file's level sentinel, `rfPower`'s MOR-2658 treatment)
 * and `toVfoProps` / `toFilterProps` project the empty string / `null` for
 * an unread filter. Every consumer renders the bare key ('MON', 'COMP',
 * 'PROC') or an empty reserved slot, and lights no filter choice — never an
 * invented number or the first filter's label.
 *
 * Each pin names the mutation it kills and is red on the pre-MOR-2683 code
 * (the fabricated `?? 128` / `?? 0` / `?? 1` defaults). The projection-level
 * red pins live in `panel-props.test.ts`'s MOR-2683 describes; this file
 * pins the rendered literals and the reserved slots.
 *
 * One file on purpose (the ticket's 10-file lease): the consumers share one
 * mocked adapter seam, and the mobile layout's reserved-width rules are
 * pinned structurally from source (the same source-regex convention
 * `MobileRadioLayout.honesty.isolated.test.ts` uses for
 * `.m-tx-power-value`).
 */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { mount, unmount, flushSync } from 'svelte';
import { readFileSync } from 'node:fs';
import type { ServerState } from '$lib/types/state';
import { setLocale } from '$lib/i18n';
import { toTxProps } from '$lib/runtime/props/panel-props';
import { ManagedAppTxHarness } from '$lib/runtime/tx-controller/__tests__/support/managed-app-tx-harness';
import type { CommandScalarFeedback } from '../../primitives/scalar/continuous-scalar.svelte';

// ── TxPanel seam (mirrors TxPanel.isolated.test.ts) ─────────────────────────
const txProps = vi.hoisted(() => ({ value: null as any }));
const txHandlers = vi.hoisted(() => ({
  onRfPowerChange: () => {}, onMicGainChange: () => {}, onAtuToggle: () => {},
  onAtuTune: () => {}, onVoxToggle: () => {}, onCompToggle: () => {},
  onCompLevelChange: () => {}, onMonToggle: () => {}, onMonLevelChange: () => {},
  onDriveGainChange: () => {},
}));

// ── Amber seam (mirrors AmberPanels.honesty-migration.isolated.test.ts) ─────
const cockpitProps = vi.hoisted(() => ({ value: null as any }));
const scopeProps = vi.hoisted(() => ({ value: null as any }));
const caps = {
  tx: true,
  capabilities: [
    'rit', 'xit', 'vox', 'compressor', 'tuner', 'split', 'dial_lock',
    'ip_plus', 'monitor',
  ],
} as any;

// ── FilterPanel seam (mirrors FilterPanel.isolated.test.ts) ─────────────────
const filterProps = vi.hoisted(() => ({
  value: {
    currentMode: 'USB',
    currentFilter: 2,
    filterShape: 0,
    hasFilterShape: true,
    filterLabels: ['FIL1', 'FIL2', 'FIL3'],
    filterWidth: 2400,
    filterWidthMin: 50,
    filterWidthMax: 9999,
    filterConfig: {
      defaults: [3000, 2400, 1800], fixed: false, minHz: 50, maxHz: 3600, stepHz: 50,
    } as { defaults: number[]; fixed: boolean; minHz: number; maxHz: number; stepHz: number } | null,
    ifShift: 0, hasIfShift: true, hasPbt: false,
    pbtInner: null as number | null, pbtOuter: null as number | null,
    pbtDomain: null as object | null, ifShiftDomain: null as object | null,
  },
}));
const filterHandlers = vi.hoisted(() => ({
  onFilterChange: () => {}, onFilterWidthChange: () => {}, onFilterShapeChange: () => {},
  onFilterPresetChange: () => {}, onFilterDefaults: () => {}, onIfShiftChange: () => {},
  onPbtInnerChange: () => {}, onPbtOuterChange: () => {}, onPbtReset: () => {},
}));

function idleFeedback(control: string): Readonly<CommandScalarFeedback> {
  return {
    confirmed: 2400, target: null, requestedTarget: null, phase: 'idle', busy: false,
    availability: 'available', outcome: null, lifecycleId: null, transitionId: null,
    providerGeneration: 1, sessionEpoch: 7,
    scope: { control, receiver: 0 }, repeatPolicy: 'latest-target-wins',
  } as Readonly<CommandScalarFeedback>;
}

vi.mock('$lib/runtime/adapters/panel-adapters', () => ({
  // TxPanel
  deriveTxProps: () => txProps.value,
  getTxHandlers: () => txHandlers,
  getTxAuxControlFeedback: (field: string) => ({
    confirmed: field === 'compressorLevel' || field === 'monitorGain' ? 64 : 128,
    target: null, requestedTarget: null, phase: 'idle', busy: false,
    availability: 'available', outcome: null, lifecycleId: null, transitionId: null,
    sessionEpoch: 1, scope: { control: field, receiver: 0 },
    repeatPolicy: 'latest-target-wins',
  }),
  // Amber faces
  deriveAmberCockpitProps: () => cockpitProps.value,
  deriveAmberScopeProps: () => scopeProps.value,
  deriveAmberTelemetryProps: () => ({ vdRaw: null, idRaw: null }),
  getAmberCockpitHandlers: () => ({ onTuningChange: () => {} }),
  getVfoHandlers: () => ({ onFreqChange: () => {}, onModeChange: () => {} }),
  bindVfoTunerContext: () => ({ read: () => ({ view: null }) }),
  // FilterPanel
  deriveFilterProps: () => ({ ...filterProps.value }),
  getFilterHandlers: () => filterHandlers,
  getFilterArmed: () => ({ armed: false, value: null }),
  getFilterShapeArmed: () => ({ armed: false, value: null }),
  getFilterWidthControlFeedback: () => idleFeedback('filter-width'),
  getPbtInnerHzControlFeedback: () => idleFeedback('pbt-inner'),
  getPbtOuterHzControlFeedback: () => idleFeedback('pbt-outer'),
  getIfShiftControlFeedback: () => idleFeedback('if-shift'),
}));

vi.mock('$lib/runtime/adapters/qsy-history-adapter', () => ({
  deriveQsyRecent: () => [],
}));

vi.mock('$lib/runtime/frontend-runtime', () => ({
  presentationResources: { acquire: vi.fn(() => ({})), release: vi.fn() },
  runtime: {
    send: vi.fn(),
    scope: {
      registerPresentationDriver: vi.fn(),
      subscribe: vi.fn(() => vi.fn()),
    },
  },
}));

const txHost = vi.hoisted(() => ({ current: undefined as any }));
vi.mock('$lib/runtime/tx-controller/managed-app-host', () => ({
  getManagedAppTxController: () => txHost.current,
}));

// #771 fast-pool isolation for TxPanel's ModInputTxWarning children
// (same rationale as TxPanel.isolated.test.ts).
vi.mock('$lib/runtime/adapters/mod-input-tx-guard.svelte', () => ({
  deriveModInputTxGuardProps: () => ({ visible: false, sourceLabel: null }),
  getModInputTxGuardHandlers: () => ({ onSetLan: () => {}, onDismiss: () => {} }),
}));
vi.mock('$lib/runtime/adapters/mod-input-auto.svelte', () => ({
  deriveAutoLanModInputProps: () => ({ available: false, enabled: false }),
  setAutoLanModInputEnabled: () => {},
}));

import TxPanel from '../TxPanel.svelte';
import FilterPanel from '../FilterPanel.svelte';
import EssentialsPanel from '../EssentialsPanel.svelte';
import AmberCockpit from '../lcd/AmberCockpit.svelte';
import AmberScope from '../lcd/AmberScope.svelte';

// ── Shared mounts ───────────────────────────────────────────────────────────

let components: ReturnType<typeof mount>[] = [];
let tx: ManagedAppTxHarness;

function baseReceiver(overrides: Record<string, unknown> = {}) {
  return {
    freqHz: 14_074_000, mode: 'USB', filter: 1, dataMode: 0, sMeter: 0,
    att: 0, preamp: 0, nb: false, nr: false, afLevel: 128, rfGain: 255,
    squelch: 0, agc: 2,
    ...overrides,
  };
}

function stateWith(top: Record<string, unknown>): ServerState {
  return {
    active: 'MAIN', main: baseReceiver(), sub: baseReceiver(),
    ...top,
  } as unknown as ServerState;
}

function mountTxPanel(top: Record<string, unknown>) {
  // The real projection feeds the panel, so the pin is red on any code that
  // re-fabricates the level at the source (the old `?? 128` / `?? 0`).
  txProps.value = toTxProps(stateWith(top), caps);
  const target = document.createElement('div');
  document.body.appendChild(target);
  components.push(mount(TxPanel, { target }));
  flushSync();
  return target;
}

function txButton(target: HTMLElement, text: string): HTMLButtonElement {
  const button = Array.from(target.querySelectorAll<HTMLButtonElement>('button'))
    .find((b) => b.textContent?.trim() === text);
  if (!button) throw new Error(`TxPanel button "${text}" not found`);
  return button;
}

function mountAmber(component: typeof AmberCockpit | typeof AmberScope, slot: { value: any }, state: ServerState) {
  slot.value = {
    radioState: state,
    caps,
    hasCapability: (name: string) => caps.capabilities.includes(name),
    hasAudioFft: false,
    hasDualReceiver: false,
  };
  const target = document.createElement('div');
  document.body.appendChild(target);
  components.push(mount(component, { target }));
  flushSync();
  return target;
}

function amberIndicator(target: HTMLElement, label: string): HTMLElement | undefined {
  return [...target.querySelectorAll('.lcd-ind')]
    .find((element) => element.textContent === label) as HTMLElement | undefined;
}

function mountFilterPanel(currentFilter: number | null) {
  filterProps.value = { ...filterProps.value, currentFilter };
  const target = document.createElement('div');
  document.body.appendChild(target);
  components.push(mount(FilterPanel, { target }));
  flushSync();
  return target;
}

const noop = () => {};

function mountEssentials(currentFilter: number | null) {
  const target = document.createElement('div');
  document.body.appendChild(target);
  components.push(mount(EssentialsPanel, {
    target,
    props: {
      vfoOps: { splitActive: false },
      mode: { currentMode: 'USB', modes: [] },
      filter: { currentFilter, filterLabels: ['FIL1', 'FIL2', 'FIL3'] },
      rxAudio: { monitorMode: 'local', afLevel: Number.NaN },
      dsp: { nbActive: false, nrMode: 0, notchMode: 'off' },
      quickModes: [],
      onSplitToggle: noop, onSwap: noop, onEqual: noop, onModeChange: noop,
      onModeMore: noop, onFilterChange: noop, onFilterMore: noop,
      onMonitorModeChange: noop, onAfLevelChange: noop, onNbToggle: noop,
      onNrModeChange: noop, onNotchModeChange: noop,
    } as any,
  }));
  flushSync();
  return target;
}

beforeEach(() => {
  components = [];
  setLocale('en-US');
  tx = new ManagedAppTxHarness();
  txHost.current = tx.controller as any;
});

afterEach(() => {
  components.forEach((c) => unmount(c));
  document.body.innerHTML = '';
});

// ── TxPanel: MON / COMP buttons ─────────────────────────────────────────────

describe('TxPanel MON / COMP buttons (MOR-2683)', () => {
  it('renders the bare MON key for a known-ON monitor with no reported level, never "MON 50%"', () => {
    const target = mountTxPanel({ monitorOn: true, monitorGain: null });
    expect(txButton(target, 'MON').textContent).toBe('MON');
  });

  it('renders "MON 50%" for a real monitor gain of 128 (spaces exact)', () => {
    const target = mountTxPanel({ monitorOn: true, monitorGain: 128 });
    expect(txButton(target, 'MON 50%').textContent).toBe('MON 50%');
  });

  it('renders the bare COMP key for a known-ON compressor with no reported level, never "COMP 0"', () => {
    const target = mountTxPanel({ compressorOn: true, compressorLevel: null });
    expect(txButton(target, 'COMP').textContent).toBe('COMP');
  });

  it('renders "COMP 50%" for a real compressor level of 128 (spaces exact)', () => {
    const target = mountTxPanel({ compressorOn: true, compressorLevel: 128 });
    expect(txButton(target, 'COMP 50%').textContent).toBe('COMP 50%');
  });

  // Geometry: the COMP/MON buttons live in `.tx-button-grid`, whose
  // `1fr 1fr` columns size every cell from the container, never from the
  // button text — so 'MON' → 'MON 100%' (the widest text it prints) cannot
  // move a neighbour. Structural pin on the reserving rule.
  it('keeps the button box reserved by the grid rule in every state', () => {
    const source = readFileSync('src/components-v2/panels/TxPanel.svelte', 'utf-8');
    expect(source).toMatch(/\.tx-button-grid \{[^}]*grid-template-columns: 1fr 1fr;/s);
  });
});

// ── Amber faces: the PROC chip ──────────────────────────────────────────────

const amberFaces = [
  ['AmberCockpit', AmberCockpit, cockpitProps],
  ['AmberScope', AmberScope, scopeProps],
] as const;

describe('AmberCockpit / AmberScope PROC chip (MOR-2683)', () => {
  it.each(amberFaces)(
    '%s renders the bare PROC key for an unread level, never "PROC 0"',
    (_name, component, slot) => {
      const target = mountAmber(component, slot, stateWith({ compressorOn: true }));
      expect(amberIndicator(target, 'PROC')).toBeDefined();
      expect(amberIndicator(target, 'PROC 0')).toBeUndefined();
      expect(amberIndicator(target, 'PROC NaN')).toBeUndefined();
    },
  );

  it.each(amberFaces)(
    '%s renders "PROC 7" for a real level of 7 (spaces exact)',
    (_name, component, slot) => {
      const target = mountAmber(component, slot, stateWith({ compressorOn: true, compressorLevel: 7 }));
      expect(amberIndicator(target, 'PROC 7')).toBeDefined();
    },
  );

  // Geometry: the chip reserves 8ch — the widest text it prints is
  // `PROC 255` — so a level arriving cannot move a neighbouring chip.
  it.each(amberFaces)(
    '%s reserves the PROC chip slot at 8ch in every state',
    (_name, component, slot) => {
      for (const level of [undefined, 7] as const) {
        const target = mountAmber(component, slot, stateWith({ compressorOn: true, compressorLevel: level }));
        const chip = amberIndicator(target, level === undefined ? 'PROC' : 'PROC 7');
        expect(chip).toBeDefined();
        expect(chip?.style.minInlineSize).toBe('8ch');
      }
    },
  );
});

// ── FilterPanel: no lit choice, honest modal, for an unread filter ──────────

describe('FilterPanel unread filter (MOR-2683)', () => {
  it('lights no filter choice for an unread filter', () => {
    const target = mountFilterPanel(null);
    const lit = Array.from(target.querySelectorAll('[data-active="true"]'))
      .filter((el) => /^FIL\d$/.test(el.textContent ?? ''));
    expect(lit).toEqual([]);
  });

  it('still lights the read filter and keeps the known-state text exact', () => {
    const target = mountFilterPanel(2);
    const fil2 = Array.from(target.querySelectorAll('[data-active]'))
      .find((el) => el.textContent === 'FIL2');
    expect(fil2?.getAttribute('data-active')).toBe('true');
    const fil1 = Array.from(target.querySelectorAll('[data-active]'))
      .find((el) => el.textContent === 'FIL1');
    expect(fil1?.getAttribute('data-active')).toBe('false');
  });

  it('marks no modal row ACTIVE and borrows no width for an unread filter', () => {
    const target = mountFilterPanel(null);
    (target.querySelector('.settings-button') as HTMLButtonElement).click();
    flushSync();
    const modal = document.querySelector('.filter-modal');
    expect(modal).not.toBeNull();
    expect(modal?.textContent ?? '').not.toContain('ACTIVE');
    // Kills: the pre-MOR-2683 arithmetic — `null - 1` clamps to index 0, so
    // FIL1's row borrowed the current width (2400) instead of its factory
    // default (3000).
    const sliders = modal?.querySelectorAll<HTMLElement>('[role="slider"]') ?? [];
    expect(sliders[0]?.getAttribute('aria-valuenow')).toBe('3000');
  });
});

// ── EssentialsPanel: no lit filter choice for an unread filter ──────────────

describe('EssentialsPanel unread filter (MOR-2683)', () => {
  it('lights no filter quick button for an unread filter', () => {
    const target = mountEssentials(null);
    const lit = Array.from(target.querySelectorAll('[data-active="true"]'))
      .filter((el) => /^FIL\d$/.test(el.textContent ?? ''));
    expect(lit).toEqual([]);
  });

  it('still lights the read filter choice', () => {
    const target = mountEssentials(2);
    const lit = Array.from(target.querySelectorAll('[data-active="true"]'))
      .filter((el) => /^FIL\d$/.test(el.textContent ?? ''));
    expect(lit.map((el) => el.textContent)).toEqual(['FIL2']);
  });
});

// ── Mobile layout: the reserved filter slots (structural, from source) ──────

describe('MobileRadioLayout reserved filter slots (MOR-2683)', () => {
  // The spans print `activeVfo.filter` verbatim; the unread '' case of a
  // present receiver is pinned at the projection level in
  // `panel-props.test.ts`, and the no-state '' case is already pinned in
  // `MobileRadioLayout.honesty.isolated.test.ts`. What this file pins is
  // the reservation: both spans keep one width in every state (4ch covers
  // the widest label `toVfoProps` prints, 'FIL1'…), so the empty-string
  // sentinel cannot shift their rows. Same source-regex convention as the
  // honesty file's `.m-tx-power-value` pin.
  it('reserves the m-vfo-filter and m-ls-filter slots with a min-width rule', () => {
    const source = readFileSync('src/components-v2/layout/MobileRadioLayout.svelte', 'utf-8');
    expect(source).toMatch(/\.m-vfo-filter \{[^}]*min-width:/s);
    expect(source).toMatch(/\.m-ls-filter \{[^}]*min-width:/s);
  });
});
