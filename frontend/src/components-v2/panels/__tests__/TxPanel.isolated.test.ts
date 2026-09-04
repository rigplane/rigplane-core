import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { mount, unmount, flushSync } from 'svelte';
import { txStatusColor } from '../tx-utils';
import type { ManagedAppTxController } from '$lib/runtime/tx-controller/managed-app-host';
import { ManagedAppTxHarness } from '$lib/runtime/tx-controller/__tests__/support/managed-app-tx-harness';

// ---------------------------------------------------------------------------
// txStatusColor
// ---------------------------------------------------------------------------

describe('txStatusColor', () => {

  beforeEach(() => {
    vi.useFakeTimers();
  });

  afterEach(() => {
    vi.useRealTimers();
  });

it('returns danger red when tuning', () => {
    expect(txStatusColor(true, true)).toBe('var(--v2-tx-tuning)');
  });

  it('returns danger red when tuning even if active is false', () => {
    expect(txStatusColor(false, true)).toBe('var(--v2-tx-tuning)');
  });

  it('returns TX orange when active and not tuning', () => {
    expect(txStatusColor(true, false)).toBe('var(--v2-tx-active)');
  });

  it('returns muted color when inactive and not tuning', () => {
    expect(txStatusColor(false, false)).toBe('var(--v2-tx-idle)');
  });

  it('tuning takes priority over active', () => {
    // tuning=true always wins, regardless of active
    expect(txStatusColor(true, true)).toBe('var(--v2-tx-tuning)');
    expect(txStatusColor(false, true)).toBe('var(--v2-tx-tuning)');
  });
});

// ---------------------------------------------------------------------------
// TxPanel component
// ---------------------------------------------------------------------------

const mockProps = {
  txActive: false,
  txActiveAvailable: true,
  rfPower: 0.5,
  micGain: 128,
  atuActive: false,
  atuTuning: false,
  voxActive: false,
  compActive: false,
  compLevel: 64,
  monActive: false,
  monLevel: 64,
  driveGain: 128,
  hasTx: true,
  hasTuner: true,
  hasMonitor: true,
};

const mockHandlers = {
  onRfPowerChange: vi.fn(),
  onMicGainChange: vi.fn(),
  onAtuToggle: vi.fn(),
  onAtuTune: vi.fn(),
  onVoxToggle: vi.fn(),
  onCompToggle: vi.fn(),
  onCompLevelChange: vi.fn(),
  onMonToggle: vi.fn(),
  onMonLevelChange: vi.fn(),
  onDriveGainChange: vi.fn(),
};

vi.mock('$lib/runtime/adapters/panel-adapters', () => ({
  deriveTxProps: () => mockProps,
  getTxHandlers: () => mockHandlers,
}));

const txHost = vi.hoisted(() => ({ current: undefined as unknown as ManagedAppTxController }));

vi.mock('$lib/runtime/tx-controller/managed-app-host', () => ({
  getManagedAppTxController: () => txHost.current,
}));

// MOR-617: TxPanel mounts ModInputTxWarning, whose real adapter pulls in the
// real transport/stores. Mock it so this fast-pool test never pins those
// modules in the shared (isolate: false) cache — see vite.config.ts / #771.
vi.mock('$lib/runtime/adapters/mod-input-tx-guard.svelte', () => ({
  deriveModInputTxGuardProps: () => ({ visible: false, sourceLabel: null }),
  getModInputTxGuardHandlers: () => ({ onSetLan: vi.fn(), onDismiss: vi.fn() }),
}));

// MOR-618: same #771 rationale for the auto LAN MOD-input toggle adapter.
const mockAutoLanProps = vi.hoisted(() => ({ available: false, enabled: false }));
const mockSetAutoLan = vi.hoisted(() => vi.fn());

vi.mock('$lib/runtime/adapters/mod-input-auto.svelte', () => ({
  deriveAutoLanModInputProps: () => ({ ...mockAutoLanProps }),
  setAutoLanModInputEnabled: mockSetAutoLan,
}));

import TxPanel from '../TxPanel.svelte';
import txPanelSource from '../TxPanel.svelte?raw';

let tx: ManagedAppTxHarness;
let components: ReturnType<typeof mount>[] = [];

function mountPanel(overrides?: Partial<typeof mockProps>) {
  if (overrides) Object.assign(mockProps, overrides);
  const t = document.createElement('div');
  document.body.appendChild(t);
  const component = mount(TxPanel, { target: t });
  flushSync();
  components.push(component);
  return t;
}

function openTxSettings(container: HTMLElement) {
  const btn = Array.from(container.querySelectorAll<HTMLButtonElement>('.v2-control-button'))
    .find((b) => b.textContent?.includes('LEVELS'));
  btn?.click();
  flushSync();
}

beforeEach(() => {
  components = [];
  tx = new ManagedAppTxHarness();
  txHost.current = tx.controller;
  Object.assign(mockProps, {
    txActive: false, txActiveAvailable: true, rfPower: 0.5, micGain: 128, atuActive: false,
    atuTuning: false, voxActive: false, compActive: false, compLevel: 64,
    monActive: false, monLevel: 64, driveGain: 128,
    hasTx: true, hasTuner: true, hasMonitor: true,
  });
  mockHandlers.onRfPowerChange = vi.fn();
  mockHandlers.onMicGainChange = vi.fn();
  mockHandlers.onAtuToggle = vi.fn();
  mockHandlers.onAtuTune = vi.fn();
  mockHandlers.onVoxToggle = vi.fn();
  mockHandlers.onCompToggle = vi.fn();
  mockHandlers.onCompLevelChange = vi.fn();
  mockHandlers.onMonToggle = vi.fn();
  mockHandlers.onMonLevelChange = vi.fn();
  mockHandlers.onDriveGainChange = vi.fn();
  mockAutoLanProps.available = false;
  mockAutoLanProps.enabled = false;
  mockSetAutoLan.mockReset();
});

afterEach(() => {
  components.forEach((c) => unmount(c));
  expect(tx.listenerCount()).toBe(0);
  document.body.innerHTML = '';
});

describe('panel structure', () => {
  it('renders managed TX unknown instead of legacy RX', () => {
    tx.emitStale();
    const t = mountPanel();
    const strip = t.querySelector('.tx-strip');
    expect(strip?.textContent?.trim()).toBe('○ ---');
  });

  it('does not source TX ACTIVE from legacy txActive', () => {
    tx.emitStale();
    const t = mountPanel({ txActive: true });
    const strip = t.querySelector('.tx-strip');
    expect(strip?.textContent?.trim()).toBe('○ ---');
  });

  it('renders Mic Gain slider', () => {
    const t = mountPanel();
    openTxSettings(t);
    const labels = Array.from(t.querySelectorAll('.vc-label'));
    expect(labels.some((el) => el.textContent === 'Mic Gain')).toBe(true);
  });

  it('renders ATU toggle', () => {
    const t = mountPanel();
    const buttons = Array.from(t.querySelectorAll('.v2-control-button'));
    expect(buttons.some((el) => el.textContent?.trim().startsWith('TUNE'))).toBe(true);
  });

  it('renders TUNE button', () => {
    const t = mountPanel();
    const buttons = Array.from(t.querySelectorAll('.v2-control-button'));
    expect(buttons.some((el) => el.textContent?.trim().startsWith('TUNE'))).toBe(true);
  });

  it('renders VOX toggle', () => {
    const t = mountPanel();
    const buttons = Array.from(t.querySelectorAll('.v2-control-button'));
    expect(buttons.some((el) => el.textContent?.trim() === 'VOX')).toBe(true);
  });

  it('renders COMP toggle', () => {
    const t = mountPanel();
    const buttons = Array.from(t.querySelectorAll('.v2-control-button'));
    expect(buttons.some((el) => el.textContent?.trim().startsWith('COMP'))).toBe(true);
  });

  it('renders MON toggle', () => {
    const t = mountPanel();
    const buttons = Array.from(t.querySelectorAll('.v2-control-button'));
    expect(buttons.some((el) => el.textContent?.trim().startsWith('MON'))).toBe(true);
  });
});

describe('hasTx gating', () => {
  it('renders panel content when hasTx prop is true', () => {
    const t = mountPanel({ hasTx: true });
    expect(t.querySelector('.tx-panel')).not.toBeNull();
  });

  it('hides panel content when hasTx prop is false', () => {
    const t = mountPanel({ hasTx: false });
    expect(t.querySelector('.tx-panel')).toBeNull();
  });
});

describe('COMP slider visibility', () => {
  it('does not render Comp Level slider when compActive is false', () => {
    const t = mountPanel();
    const labels = Array.from(t.querySelectorAll('.vc-label')).map((el) => el.textContent);
    expect(labels).not.toContain('Comp Level');
  });

  it('renders Comp Level slider when compActive is true', () => {
    const t = mountPanel({ compActive: true });
    openTxSettings(t);
    const labels = Array.from(t.querySelectorAll('.vc-label')).map((el) => el.textContent);
    expect(labels).toContain('Comp Level');
  });
});

describe('MON slider visibility', () => {
  it('does not render Mon Level slider when monActive is false', () => {
    const t = mountPanel();
    const labels = Array.from(t.querySelectorAll('.vc-label')).map((el) => el.textContent);
    expect(labels).not.toContain('Mon Level');
  });

  it('renders Mon Level slider when monActive is true', () => {
    const t = mountPanel({ monActive: true });
    openTxSettings(t);
    const labels = Array.from(t.querySelectorAll('.vc-label')).map((el) => el.textContent);
    expect(labels).toContain('Mon Level');
  });
});

describe('auto LAN MOD-input toggle (MOR-618)', () => {
  it('is hidden when the adapter reports unavailable', () => {
    const t = mountPanel();
    openTxSettings(t);
    expect(t.querySelector('[data-testid="auto-lan-toggle"]')).toBeNull();
  });

  it('renders unchecked (opt-in default OFF) when available', () => {
    mockAutoLanProps.available = true;
    const t = mountPanel();
    openTxSettings(t);
    const toggle = t.querySelector<HTMLInputElement>('[data-testid="auto-lan-toggle"]');
    expect(toggle).not.toBeNull();
    expect(toggle!.checked).toBe(false);
  });

  it('calls setAutoLanModInputEnabled when toggled', () => {
    mockAutoLanProps.available = true;
    const t = mountPanel();
    openTxSettings(t);
    const toggle = t.querySelector<HTMLInputElement>('[data-testid="auto-lan-toggle"]')!;
    toggle.click();
    flushSync();
    expect(mockSetAutoLan).toHaveBeenCalledWith(true);
  });
});

describe('tuning state', () => {
  it('adds tuning class to TUNE button when atuTuning is true', () => {
    const t = mountPanel({ atuTuning: true });
    const buttons = Array.from(t.querySelectorAll('.v2-control-button'));
    expect(buttons.some((el) => el.textContent?.trim().startsWith('TUNING'))).toBe(true);
  });

  it('does not add tuning class when atuTuning is false', () => {
    const t = mountPanel();
    const buttons = Array.from(t.querySelectorAll('.v2-control-button'));
    expect(buttons.some((el) => el.textContent?.trim() === 'TUNE')).toBe(true);
  });
});

describe('callbacks', () => {

  beforeEach(() => {
    vi.useFakeTimers();
  });

  afterEach(() => {
    vi.useRealTimers();
  });

  it('calls onAtuTune when TUNE button is clicked', () => {
    const t = mountPanel();
    const buttons = Array.from(t.querySelectorAll<HTMLElement>('.v2-control-button'));
    const tuneBtn = buttons.find((el) => el.textContent?.trim().startsWith('TUNE'));
    tuneBtn?.click();
    expect(mockHandlers.onAtuTune).toHaveBeenCalledOnce();
  });

  it('calls onMicGainChange when Mic Gain slider changes', () => {
    const t = mountPanel();
    // Open the settings modal to reveal sliders
    const levelsBtn = Array.from(t.querySelectorAll<HTMLElement>('.v2-control-button'))
      .find((b) => b.textContent?.includes('LEVELS'));
    levelsBtn?.click();
    flushSync();
    // Find the Mic Gain slider (second [role="slider"], after RF Power)
    const sliders = t.querySelectorAll<HTMLElement>('[role="slider"]');
    const micSlider = sliders[1]; // RF Power is [0], Mic Gain is [1]
    if (micSlider) {
      micSlider.dispatchEvent(new KeyboardEvent('keydown', { key: 'ArrowRight', bubbles: true }));
    }
    vi.advanceTimersByTime(60);

    expect(mockHandlers.onMicGainChange).toHaveBeenCalled();
  });
});

// ---------------------------------------------------------------------------
// PTT — driven entirely by the App TX controller (MOR-1011)
// ---------------------------------------------------------------------------

describe('PTT via the App TX controller (MOR-1011)', () => {
  beforeEach(() => vi.useFakeTimers());
  afterEach(() => vi.useRealTimers());

  const button = (t: HTMLElement) => t.querySelector<HTMLButtonElement>('.ptt-button')!;
  const label = (t: HTMLElement) => button(t).textContent?.trim();
  const down = (t: HTMLElement) => {
    button(t).dispatchEvent(new PointerEvent('pointerdown', { bubbles: true }));
    flushSync();
  };
  const up = (t: HTMLElement) => {
    button(t).dispatchEvent(new PointerEvent('pointerup', { bubbles: true }));
    flushSync();
  };
  it('emits the exact WS pair for an ordinary press/release', () => {
    const t = mountPanel();
    down(t);
    expect(tx.trace()).toEqual([{ transport: 'ws', operation: 'ptt_on' }]);
    up(t);
    vi.advanceTimersByTime(299);
    expect(tx.trace()).toHaveLength(1);
    vi.advanceTimersByTime(1);
    expect(tx.trace()).toEqual([
      { transport: 'ws', operation: 'ptt_on' },
      { transport: 'ws', operation: 'ptt_off' },
    ]);
  });

  it('routes keyboard hold/release through the same WS recognizer', () => {
    const t = mountPanel();
    button(t).dispatchEvent(new KeyboardEvent('keydown', { key: ' ', bubbles: true }));
    button(t).dispatchEvent(new KeyboardEvent('keyup', { key: ' ', bubbles: true }));
    vi.advanceTimersByTime(300);
    expect(tx.trace()).toEqual([
      { transport: 'ws', operation: 'ptt_on' },
      { transport: 'ws', operation: 'ptt_off' },
    ]);
  });

  it('double tap emits one HTTP transmit_on without a second WS lease', () => {
    const t = mountPanel();
    down(t); up(t); vi.advanceTimersByTime(100); down(t);
    expect(tx.trace()).toEqual([
      { transport: 'ws', operation: 'ptt_on' },
      { transport: 'http', operation: 'transmit_on' },
    ]);
    expect(label(t)).toBe('PTT');
  });

  it('canonical latched tap emits HTTP force_off', () => {
    tx.emitServerSnapshot({ intent: 'transmit', observedPtt: 'on', releaseRequired: true });
    const t = mountPanel();
    down(t);
    expect(tx.trace()).toEqual([{ transport: 'http', operation: 'force_off' }]);
  });

  it('releases pending momentary exactly once when the panel unmounts', () => {
    const t = mountPanel();
    down(t); up(t);
    unmount(components.pop()!);
    vi.advanceTimersByTime(600);
    expect(tx.trace()).toEqual([
      { transport: 'ws', operation: 'ptt_on' },
      { transport: 'ws', operation: 'ptt_off' },
    ]);
  });

  it('renders server fault and clears it only after a fresh canonical snapshot', () => {
    tx.emitServerSnapshot({ lastError: 'not-eligible' });
    const t = mountPanel();
    const fault = t.querySelector('[data-testid="tx-fault"]');
    expect(fault).not.toBeNull();
    expect(fault!.getAttribute('data-fault')).toBe('not-eligible');
    down(t);
    expect(t.querySelector('[data-testid="tx-fault"]')).not.toBeNull();
    tx.emitServerSnapshot({ intent: 'rx', observedPtt: 'off', lastError: null });
    flushSync();
    expect(t.querySelector('[data-testid="tx-fault"]')).toBeNull();
  });

  it('takes RF state only from fresh canonical authority', () => {
    tx.emitStale();
    let t = mountPanel({ txActive: false, txActiveAvailable: true });
    expect(t.querySelector('.tx-strip')!.getAttribute('data-rf')).toBe('unknown');
    unmount(components.pop()!);

    t = mountPanel({ txActive: true, txActiveAvailable: true });
    expect(t.querySelector('.tx-strip')!.getAttribute('data-rf')).toBe('unknown');
    unmount(components.pop()!);

    tx.emitServerSnapshot({ intent: 'rx', observedPtt: 'off' });
    t = mountPanel({ txActive: true, txActiveAvailable: true });
    expect(t.querySelector('.tx-strip')!.getAttribute('data-rf')).toBe('off');
    tx.emitServerSnapshot({ intent: 'ptt', observedPtt: 'on', releaseRequired: true });
    flushSync();
    expect(t.querySelector('.tx-strip')!.getAttribute('data-rf')).toBe('on');
  });

  describe('two mounted panels', () => {
    it('shares one App-root managed facade and keeps state server-owned', () => {
      const a = mountPanel();
      const b = mountPanel();
      down(a);
      expect(tx.trace()).toEqual([{ transport: 'ws', operation: 'ptt_on' }]);
      expect(label(a)).toBe('PTT');
      expect(label(b)).toBe('PTT');
      tx.emitServerSnapshot({ intent: 'ptt', observedPtt: 'on', releaseRequired: true });
      flushSync();
      expect(label(a)).toBe('TX');
      expect(label(b)).toBe('TX');
    });
  });

  it('no longer references the retired local PTT machinery', () => {
    expect(txPanelSource).not.toContain('tx-adapter');
    expect(txPanelSource).not.toContain('getTxAudioControl');
    expect(txPanelSource).not.toContain('onPtt');
    expect(txPanelSource).toContain('getManagedAppTxController');
  });
});
