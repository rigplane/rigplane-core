import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { mount, unmount, flushSync } from 'svelte';
import { txStatusColor } from '../tx-utils';
import { TxController } from '$lib/runtime/tx-controller/controller';
import type { TxControllerDependencies } from '$lib/runtime/tx-controller/controller';

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

// MOR-1011: the panel owns no PTT state any more — it renders the App TX
// controller and feeds it gesture intent. Only the *host* (the context lookup)
// is mocked; behind it sits a REAL TxController over stub dependencies, so
// these tests exercise the production state machine instead of a double.
const txHost = vi.hoisted(() => ({ current: undefined as unknown as TxHostFacade }));

vi.mock('$lib/runtime/tx-controller/app-host', () => ({
  getAppTxController: () => txHost.current,
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

// ---------------------------------------------------------------------------
// Real-controller harness (pattern: tx-controller/__tests__/controller-contract)
// ---------------------------------------------------------------------------

type TxEvent = Parameters<TxController['dispatch']>[0];
type StartEvent = Extract<TxEvent, { type: 'start' }>;
type Eligibility = StartEvent['eligibility'];
type Observation = StartEvent['ptt'];
type Intent = StartEvent['intent'];
type Guard = Extract<TxEvent, { type: 'intent' }>['guard'];
type Command = Parameters<TxControllerDependencies['sendPtt']>[0];
type Report = Parameters<TxControllerDependencies['sendPtt']>[3];
type TxHostFacade = ReturnType<typeof createTxHarness>['facade'];

const marker = (seq: number) => ({
  authorityEpoch: 1, pttObservationSeq: seq, pttLastObservedMonotonic: seq,
});
const allowed: Eligibility = {
  catPtt: true, browserTxAudio: true, controlLive: true, permit: 'allowed',
  target: { receiver: 'MAIN', slot: 'A', frequencyHz: 14_074_000 },
};
const observe = (value: boolean, seq: number): Observation =>
  ({ value, observed: true, fresh: true, source: 'radio-readback', marker: marker(seq) });

/** Mirrors the deep-frozen snapshot/subscribe payloads app-host hands out. */
function deepFreeze<T>(value: T): T {
  if (value === null || typeof value !== 'object') return value;
  const copy = Object.fromEntries(Object.entries(value as Record<string, unknown>)
    .map(([key, child]) => [key, deepFreeze(child)]));
  return Object.freeze(copy) as unknown as T;
}

function createTxHarness() {
  const sends: Array<{ command: Command; report: Report }> = [];
  const audio: { next: Promise<string | null> } = { next: Promise.resolve(null) };
  const eligibility = { current: allowed };
  let id = 0;
  let seq = 0;
  const dependencies: TxControllerDependencies = {
    startAudio: vi.fn(() => audio.next),
    sendPtt: vi.fn((command, _commandId, _correlation, report) => { sends.push({ command, report }); }),
    stopLocalAudio: vi.fn(),
    restoreMod: vi.fn(),
    commandId: vi.fn((command) => `${command}-${++id}`),
    schedule: vi.fn((callback, delay) => setTimeout(callback, delay)),
    cancel: vi.fn((handle) => clearTimeout(handle as ReturnType<typeof setTimeout>)),
    // Far beyond the 300 ms gesture window so controller deadlines never race it.
    timeoutMs: { 'audio-start': 60_000, 'on-confirmation': 60_000, 'off-confirmation': 60_000 },
  };
  const controller = new TxController(1, marker(0), dependencies);
  const facade = {
    snapshot: () => deepFreeze(controller.snapshot()),
    subscribe: (listener: (state: unknown) => void) =>
      controller.subscribe((state) => listener(deepFreeze(state))),
    start: (sourceId: string, leaseId: string, intent: Intent) => controller.dispatch({
      type: 'start', sourceId, leaseId, intent,
      eligibility: eligibility.current, ptt: observe(false, ++seq),
    }),
    setIntent: (sourceId: string, guard: Guard, intent: Intent) =>
      controller.dispatch({ type: 'intent', sourceId, guard: { ...guard }, intent }),
    release: (sourceId: string, guard: Guard) => controller.dispatch({
      type: 'release', sourceId, guard: { ...guard }, commandId: dependencies.commandId('off'),
    }),
    resetFault: () => controller.dispatch({ type: 'reset-fault' }),
  };
  return {
    controller, dependencies, sends, facade, audio, eligibility,
    /** Feed an authoritative PTT readback (what the App host does on session updates). */
    authority: (value: boolean) => controller.dispatch({
      type: 'authority', epoch: 1, ptt: observe(value, ++seq),
      eligibility: eligibility.current, offCommandId: dependencies.commandId('off'),
    }),
    /** Report the most recent command of `command` as delivered. */
    confirm: (command: Command) => {
      const send = [...sends].reverse().find((item) => item.command === command);
      expect(send).toBeDefined();
      send!.report({ outcome: 'sent', eventEpoch: 1, barrier: marker(++seq) });
    },
    /** A competing lease source (e.g. the mobile layout or a second panel). */
    startOther: (leaseId: string) => controller.dispatch({
      type: 'start', sourceId: 'other-panel', leaseId, intent: 'momentary',
      eligibility: eligibility.current, ptt: observe(false, ++seq),
    }),
  };
}

let tx: ReturnType<typeof createTxHarness>;
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
  tx = createTxHarness();
  txHost.current = tx.facade;
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
  document.body.innerHTML = '';
});

describe('panel structure', () => {
  it('renders TX IDLE badge when txActive is false', () => {
    const t = mountPanel();
    const strip = t.querySelector('.tx-strip');
    expect(strip?.textContent?.trim()).toBe('○ RX');
  });

  it('renders TX ACTIVE badge when txActive is true', () => {
    const t = mountPanel({ txActive: true });
    const strip = t.querySelector('.tx-strip');
    expect(strip?.textContent?.trim()).toBe('● TX');
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
  const offs = () => tx.sends.filter((item) => item.command === 'off').length;
  const flushAudio = async () => { await Promise.resolve(); await Promise.resolve(); flushSync(); };

  /** Press and hold until the radio has acknowledged the ON command. */
  async function hold(t: HTMLElement) {
    down(t);
    await flushAudio();
    tx.confirm('on');
    flushSync();
  }

  /** Hold, then double-tap into the latched lock. */
  async function latch(t: HTMLElement) {
    await hold(t);
    up(t);
    vi.advanceTimersByTime(100);
    down(t);
    flushSync();
  }

  it('keys a hold through audio → ON and stays keyed', async () => {
    const t = mountPanel();
    down(t);
    expect(tx.dependencies.startAudio).toHaveBeenCalledOnce();
    expect(label(t)).toBe('MIC…');
    expect(button(t).getAttribute('aria-disabled')).toBe('true');
    await flushAudio();
    tx.confirm('on');
    flushSync();
    expect(label(t)).toBe('TX');
    expect(button(t).classList.contains('ptt-held')).toBe(true);
    expect(button(t).getAttribute('aria-disabled')).toBe('false');
    tx.authority(true);
    flushSync();
    expect(tx.controller.snapshot().phase).toBe('active');
    vi.advanceTimersByTime(1000);
    expect(offs()).toBe(0);
  });

  it('holds the lease for 299 ms after release and drops it at 300 ms', async () => {
    const t = mountPanel();
    await hold(t);
    up(t);
    vi.advanceTimersByTime(299);
    flushSync();
    expect(offs()).toBe(0);
    expect(tx.controller.snapshot().phase).toBe('key-confirm-pending');
    vi.advanceTimersByTime(1);
    flushSync();
    expect(offs()).toBe(1);
    expect(tx.controller.snapshot().phase).toBe('releasing');
    expect(label(t)).toBe('UNKEYING…');
  });

  it('latches on a double tap without starting a second lease', async () => {
    const t = mountPanel();
    await latch(t);
    expect(tx.controller.snapshot().intent).toBe('latched');
    expect(tx.dependencies.startAudio).toHaveBeenCalledOnce();
    expect(label(t)).toBe('TX 🔒');
    expect(button(t).classList.contains('ptt-latched')).toBe(true);
    vi.advanceTimersByTime(1000);
    expect(offs()).toBe(0);
  });

  it('unlatches on the next press instead of starting a new lease', async () => {
    const t = mountPanel();
    await latch(t);
    up(t);
    down(t);
    expect(offs()).toBe(1);
    expect(tx.controller.snapshot().phase).toBe('releasing');
    expect(tx.dependencies.startAudio).toHaveBeenCalledOnce();
  });

  it('drops a quick tap taken while audio is still pending, and never keys late', async () => {
    let resolveAudio!: (value: string | null) => void;
    tx.audio.next = new Promise((resolve) => { resolveAudio = resolve; });
    const t = mountPanel();
    down(t);
    expect(label(t)).toBe('MIC…');
    up(t);
    vi.advanceTimersByTime(300);
    flushSync();
    expect(tx.controller.snapshot().phase).toBe('releasing');
    resolveAudio(null);
    await flushAudio();
    expect(tx.sends.filter((item) => item.command === 'on')).toHaveLength(0);
  });

  it('releases the lease when the panel unmounts', async () => {
    const t = mountPanel();
    await hold(t);
    expect(tx.controller.snapshot().phase).toBe('key-confirm-pending');
    unmount(components.pop()!);
    expect(offs()).toBe(1);
    expect(tx.controller.snapshot().phase).toBe('releasing');
  });

  it('cannot disturb a newer owner when its stale release window fires', async () => {
    const t = mountPanel();
    await hold(t);
    tx.authority(true);
    flushSync();
    up(t); // arms a 300 ms window against the guard live RIGHT NOW
    // Meanwhile the lease is torn down and re-taken by a different source.
    tx.controller.dispatch({
      type: 'release', guard: tx.controller.snapshot().guard!, commandId: 'off-external',
    });
    tx.confirm('off');
    tx.authority(false);
    flushSync();
    expect(tx.controller.snapshot().phase).toBe('idle');
    tx.startOther('other-lease');
    flushSync();
    const before = offs();
    vi.advanceTimersByTime(300);
    flushSync();
    expect(offs()).toBe(before);
    expect(tx.controller.snapshot()).toMatchObject({
      sourceId: 'other-panel', phase: 'audio-start-pending',
    });
  });

  it('renders a controller fault and clears it on the next press', async () => {
    tx.eligibility.current = { ...allowed, permit: 'denied' };
    const t = mountPanel();
    down(t);
    const fault = t.querySelector('[data-testid="tx-fault"]');
    expect(fault).not.toBeNull();
    expect(fault!.getAttribute('data-fault')).toBe('not-eligible');
    expect(label(t)).toBe('PTT');
    up(t);
    tx.eligibility.current = allowed;
    down(t);
    await flushAudio();
    expect(t.querySelector('[data-testid="tx-fault"]')).toBeNull();
    expect(tx.controller.snapshot()).toMatchObject({ fault: null, phase: 'audio-start-pending' });
  });

  it('takes RF state from controller authority and falls back to panel props', async () => {
    let t = mountPanel({ txActive: false, txActiveAvailable: false });
    expect(t.querySelector('.tx-strip')!.getAttribute('data-rf')).toBe('unknown');
    unmount(components.pop()!);

    t = mountPanel({ txActive: true, txActiveAvailable: true });
    expect(t.querySelector('.tx-strip')!.getAttribute('data-rf')).toBe('on');
    expect(t.querySelector('[data-testid="tx-strip"]')!.textContent?.trim()).toBe('● TX');
    unmount(components.pop()!);

    // Controller authority wins over a props snapshot that still says RX.
    t = mountPanel({ txActive: false, txActiveAvailable: true });
    expect(t.querySelector('.tx-strip')!.getAttribute('data-rf')).toBe('off');
    await hold(t);
    tx.authority(true);
    flushSync();
    expect(t.querySelector('.tx-strip')!.getAttribute('data-rf')).toBe('on');
  });

  // Both sidebars list a draggable "tx" panel, so two instances can be mounted
  // at once against one controller. Every guarantee below rests on each
  // instance owning a DISTINCT sourceId: the guard alone always matches (it is
  // the single live lease), so sourceId is the only thing stopping the idle
  // panel from releasing, latching or tearing down the busy panel's TX.
  describe('two mounted panels', () => {
    it('gives each instance its own lease identity', async () => {
      const a = mountPanel();
      const b = mountPanel();
      await hold(a);
      const first = tx.controller.snapshot().sourceId;
      unmount(components.shift()!); // A releases its own lease, then drains
      tx.confirm('off');
      tx.authority(false);
      flushSync();
      expect(tx.controller.snapshot().phase).toBe('idle');
      down(b);
      expect(tx.controller.snapshot().sourceId).not.toBe(first);
    });

    it('does not let an idle panel release the other panel lease', async () => {
      const a = mountPanel();
      const b = mountPanel();
      await hold(a);
      const owner = tx.controller.snapshot().sourceId;
      down(b);
      up(b); // arms B's window against A's live guard
      vi.advanceTimersByTime(1000);
      flushSync();
      expect(offs()).toBe(0);
      expect(tx.controller.snapshot()).toMatchObject({
        sourceId: owner, phase: 'key-confirm-pending',
      });
      expect(tx.dependencies.startAudio).toHaveBeenCalledOnce();
    });

    it('does not let an idle panel unmount release the other panel lease', async () => {
      const a = mountPanel();
      mountPanel();
      await hold(a);
      const owner = tx.controller.snapshot().sourceId;
      unmount(components.pop()!); // unmount B, which never owned anything
      flushSync();
      expect(offs()).toBe(0);
      expect(tx.controller.snapshot()).toMatchObject({
        sourceId: owner, phase: 'key-confirm-pending',
      });
    });

    it('does not let an idle panel latch the other panel lease', async () => {
      const a = mountPanel();
      const b = mountPanel();
      await hold(a);
      down(b);
      up(b);
      vi.advanceTimersByTime(100);
      down(b); // a double tap that would latch if the sourceId were shared
      flushSync();
      expect(tx.controller.snapshot().intent).toBe('momentary');
      expect(offs()).toBe(0);
    });
  });

  it('no longer references the retired local PTT machinery', () => {
    expect(txPanelSource).not.toContain('tx-adapter');
    expect(txPanelSource).not.toContain('getTxAudioControl');
    expect(txPanelSource).not.toContain('onPtt');
    expect(txPanelSource).toContain('getAppTxController');
  });
});
