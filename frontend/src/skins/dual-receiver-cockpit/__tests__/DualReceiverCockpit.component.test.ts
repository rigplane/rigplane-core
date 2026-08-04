/**
 * MOR-1067 — the compiled dual-receiver-cockpit SHELL, mounted end to end
 * against the REAL adapter and REAL semantic surfaces (only the runtime
 * singleton and the TX authority are mocked, same pattern as
 * `components-v2/wiring/__tests__/semantic-rx-tx-wiring.component.test.ts`).
 *
 * Covers the ticket's discriminating requirements: per-receiver VFO strips
 * driven by the dual-receiver topologies, single-receiver exclusion (proved
 * separately at the registry in `presentation/layouts/__tests__/
 * dual-receiver-cockpit.test.ts`), exactly one TX authority surface, the
 * scope/controls/global zones staying inert, and no shell-level fabrication
 * of an active strip. Each test's doc line names the mutation it kills.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { flushSync, mount, unmount } from 'svelte';
import type { Capabilities } from '$lib/types/capabilities';
import type { ServerState } from '$lib/types/state';

const h = vi.hoisted(() => ({
  state: null as unknown,
  caps: null as unknown,
  snapshot: null as unknown,
  listeners: new Set<(next: unknown) => void>(),
  start: vi.fn(),
  release: vi.fn(),
  setIntent: vi.fn(),
  resetFault: vi.fn(),
  selectVfo: vi.fn(),
  splitToggle: vi.fn(),
  dualWatchToggle: vi.fn(),
  modInputGuard: { visible: false, sourceLabel: null } as { visible: boolean; sourceLabel: string | null },
}));

vi.mock('$lib/runtime', () => ({
  runtime: {
    get state() { return h.state; },
    get caps() { return h.caps; },
  },
}));
vi.mock('$lib/runtime/tx-controller/app-host', () => ({
  getAppTxController: () => ({
    snapshot: () => h.snapshot,
    subscribe: (listener: (next: unknown) => void) => {
      h.listeners.add(listener);
      return () => h.listeners.delete(listener);
    },
    start: h.start,
    setIntent: h.setIntent,
    release: h.release,
    resetFault: h.resetFault,
  }),
}));
vi.mock('$lib/runtime/adapters/mod-input-tx-guard.svelte', () => ({
  deriveModInputTxGuardProps: () => h.modInputGuard,
  getModInputTxGuardHandlers: () => ({ onSetLan: vi.fn(), onDismiss: vi.fn() }),
}));
vi.mock('../../../components-v2/wiring/command-bus', () => ({
  makeVfoHandlers: () => ({
    onVfoSelect: h.selectVfo,
    onSplitToggle: h.splitToggle,
    onDualWatchToggle: h.dualWatchToggle,
  }),
}));

import DualReceiverCockpit from '../DualReceiverCockpit.svelte';

type Snapshot = {
  phase: string; intent: string | null; guard: { leaseId: string } | null;
  radioTx: string; txRisk: string; mayOwnKey: boolean; fault: string | null;
};
const IDLE: Snapshot = {
  phase: 'idle', intent: null, guard: null, radioTx: 'off', txRisk: 'none',
  mayOwnKey: false, fault: null,
};
const fresh = { storePath: 'x', observed: true, freshness: 'fresh', availability: 'available' };

/** 2/main_sub: MAIN and SUB each carry A/B slots (4 vfo tiles total). */
function mainSubState(active: 'MAIN' | 'SUB' = 'MAIN'): ServerState {
  const paths = ['active', 'split', 'dualWatch', 'txTarget'];
  for (const rx of ['main', 'sub']) {
    paths.push(`${rx}.activeSlot`);
    for (const v of ['vfoA', 'vfoB']) paths.push(`${rx}.${v}.freqHz`, `${rx}.${v}.mode`, `${rx}.${v}.filterNum`);
  }
  const slot = (hz: number) => ({ freqHz: hz, mode: 'USB', filterNum: 1 });
  const receiver = (hz: number) => ({ vfoA: slot(hz), vfoB: slot(hz + 30000), activeSlot: 'A' });
  return {
    active, split: true, dualWatch: true, ptt: false,
    txTarget: { status: 'known', receiver: 'MAIN', slot: 'A', frequencyHz: 14250000 },
    main: receiver(14250000), sub: receiver(21295000),
    fieldStatus: Object.fromEntries(paths.map((p) => [p, fresh])),
  } as unknown as ServerState;
}
const mainSubCaps = (): Capabilities => ({
  model: 'fixture', scope: false, audio: true, tx: true,
  capabilities: ['audio', 'tx', 'dual_rx'], receivers: 2, vfoScheme: 'main_sub',
  freqRanges: [], modes: [], filters: [],
  audioConfig: { sampleRate: 48000, channels: 1, codecs: ['pcm16'] },
  webrtc: { available: false, enabled: false },
  txBands: [{ start: 14000000, end: 14350000, name: '20m' }],
  scopeSource: null, audioFftAvailable: false,
} as unknown as Capabilities);

/** 2/ab_shared: MAIN and SUB are each a single unslotted VFO (2 vfo tiles total). */
function abSharedState(): ServerState {
  const paths = ['active', 'split', 'dualWatch', 'txTarget', 'main.freqHz', 'main.mode', 'main.filter',
    'sub.freqHz', 'sub.mode', 'sub.filter'];
  const receiver = (hz: number) => ({ freqHz: hz, mode: 'CW', filter: 1 });
  return {
    active: 'SUB', split: false, dualWatch: true, ptt: false,
    txTarget: { status: 'known', receiver: 'SUB', slot: null, frequencyHz: 3573000 },
    main: receiver(3573000), sub: receiver(3573000),
    fieldStatus: Object.fromEntries(paths.map((p) => [p, fresh])),
  } as unknown as ServerState;
}
/** Same shape as `mainSubState`, minus the 'active' fieldStatus entry — activeReceiver stays unknown. */
function mainSubStateUnknownActive(): ServerState {
  const state = mainSubState();
  const { active: _dropped, ...fieldStatus } = state.fieldStatus as Record<string, unknown>;
  return { ...state, fieldStatus } as unknown as ServerState;
}
const abSharedCaps = (): Capabilities => ({
  ...mainSubCaps(), vfoScheme: 'ab_shared',
} as unknown as Capabilities);

let target: HTMLDivElement;
let component: ReturnType<typeof mount> | null = null;

function render(): void {
  target = document.createElement('div');
  document.body.appendChild(target);
  component = mount(DualReceiverCockpit, { target });
  flushSync();
}

const q = <T extends HTMLElement>(sel: string) => target.querySelector(sel) as T | null;
const qa = <T extends HTMLElement>(sel: string) => [...target.querySelectorAll<T>(sel)];

beforeEach(() => {
  h.snapshot = { ...IDLE };
  h.listeners.clear();
  h.start.mockReset();
  h.release.mockReset();
  h.setIntent.mockReset();
  h.resetFault.mockReset();
  h.selectVfo = vi.fn();
  h.splitToggle = vi.fn();
  h.dualWatchToggle = vi.fn();
  h.modInputGuard = { visible: false, sourceLabel: null };
});

afterEach(() => {
  if (component) unmount(component);
  component = null;
  document.body.innerHTML = '';
});

describe('per-receiver VFO strips, driven by the dual-receiver topologies', () => {
  it('2/main_sub: renders one strip per receiver, each holding only that receiver\'s tiles', () => {
    h.state = mainSubState('MAIN');
    h.caps = mainSubCaps();
    render();

    expect(qa('[data-testid^="channel-strip-"]')).toHaveLength(2);
    const mainStrip = q('[data-testid="channel-strip-MAIN"]')!;
    const subStrip = q('[data-testid="channel-strip-SUB"]')!;
    expect(mainStrip.querySelectorAll('[data-vfo-tile]')).toHaveLength(2);
    expect(subStrip.querySelectorAll('[data-vfo-tile]')).toHaveLength(2);
    // Kills: strips sharing one unfiltered vfo list instead of splitting.
    expect([...mainStrip.querySelectorAll('[data-vfo-tile]')].every(
      (el) => (el as HTMLElement).dataset.vfoReceiver === 'MAIN',
    )).toBe(true);
    expect([...subStrip.querySelectorAll('[data-vfo-tile]')].every(
      (el) => (el as HTMLElement).dataset.vfoReceiver === 'SUB',
    )).toBe(true);
  });

  it('2/ab_shared: renders one strip per receiver with exactly one tile each', () => {
    h.state = abSharedState();
    h.caps = abSharedCaps();
    render();

    expect(qa('[data-testid^="channel-strip-"]')).toHaveLength(2);
    expect(q('[data-testid="channel-strip-MAIN"]')!.querySelectorAll('[data-vfo-tile]')).toHaveLength(1);
    expect(q('[data-testid="channel-strip-SUB"]')!.querySelectorAll('[data-vfo-tile]')).toHaveLength(1);
  });

  // Review cycle 1, F1. `2/ab_shared` gives each receiver exactly ONE
  // unslotted VFO, so a strip that gates selection on its own slice sees
  // `vfos.length === 1` and renders the control ABSENT — the operator loses
  // every way to change the active receiver, on a topology this layout
  // declares itself compatible with. The gate must read the whole radio.
  it('2/ab_shared: offers exactly one control, and it selects the NON-active receiver', () => {
    h.state = abSharedState();          // SUB is the active receiver
    h.caps = abSharedCaps();
    render();

    const selects = qa<HTMLButtonElement>('[data-vfo-select]');
    expect(selects).toHaveLength(1);
    // Present, enabled, and living in the strip the operator would switch TO.
    expect(selects[0].disabled).toBe(false);
    expect(q('[data-testid="channel-strip-MAIN"]')!.contains(selects[0])).toBe(true);

    selects[0].click();
    flushSync();
    expect(h.selectVfo).toHaveBeenCalledTimes(1);
    expect(h.selectVfo).toHaveBeenCalledWith('MAIN', null);
  });

  it('marks the positively-observed active receiver\'s strip, and only that one', () => {
    h.state = mainSubState('SUB');
    h.caps = mainSubCaps();
    render();

    expect(q('[data-testid="channel-strip-SUB"]')!.dataset.stripActive).toBe('true');
    expect(q('[data-testid="channel-strip-MAIN"]')!.dataset.stripActive).toBe('false');
  });

  // The mutation this ticket names explicitly: a shell that fabricates an
  // active strip when activeReceiver was never observed.
  it('fabricates NO active strip when activeReceiver is unknown', () => {
    h.state = mainSubStateUnknownActive();
    h.caps = mainSubCaps();
    render();

    expect(q('[data-testid="channel-strip-MAIN"]')!.dataset.stripActive).toBe('false');
    expect(q('[data-testid="channel-strip-SUB"]')!.dataset.stripActive).toBe('false');
  });
});

describe('radio-wide facts render once per cockpit, never once per strip', () => {
  // Review cycle 1, F2. split / dualWatch / activeReceiver are radio-WIDE
  // facts that `forReceiver` (correctly) passes through to every slice, so a
  // naive strip-per-receiver mount renders each of them TWICE: two
  // independent-looking `role="switch"` controls for one radio fact — a
  // duplicated aria-checked pair for assistive tech, and exactly the
  // "duplicated radio behavior" MOR-976 rules out. The shell got the TX half
  // of the one-control-per-radio-wide-authority rule right; this is the
  // split/dual-watch half.
  it.each([
    ['2/main_sub', () => mainSubState('MAIN'), mainSubCaps],
    ['2/ab_shared', abSharedState, abSharedCaps],
  ] as const)('%s: exactly one split switch, one dual-watch switch, one active-receiver line', (
    _topology, makeState, makeCaps,
  ) => {
    h.state = makeState();
    h.caps = makeCaps();
    render();

    expect(qa('[data-testid^="channel-strip-"]')).toHaveLength(2);
    expect(qa('[data-vfo-split]')).toHaveLength(1);
    expect(qa('[data-vfo-dual-watch]')).toHaveLength(1);
    expect(qa('[data-testid="vfo-active-receiver"]')).toHaveLength(1);
  });

  // Rendering the radio-wide row once must not cost the operator the control:
  // the surviving switch still reaches the same radio-wide handler.
  it('the single surviving dual-watch switch still reaches the radio-wide handler', () => {
    h.state = mainSubState('MAIN');
    h.caps = mainSubCaps();
    render();

    const dualWatch = q<HTMLButtonElement>('[data-vfo-dual-watch]')!;
    expect(dualWatch.disabled).toBe(false);
    dualWatch.click();
    flushSync();
    expect(h.dualWatchToggle).toHaveBeenCalledTimes(1);
  });
});

describe('exactly one authoritative TX action surface across both strips', () => {
  it('renders a single shared RX/TX surface and a single key button', () => {
    h.state = mainSubState('MAIN');
    h.caps = mainSubCaps();
    render();

    expect(qa('[data-testid="rx-tx-surface"]')).toHaveLength(1);
    expect(qa('[data-testid="rx-tx-key"]')).toHaveLength(1);
  });

  it('unkey stays ungated and reaches the same App TX authority the sdr-test wiring uses', () => {
    h.state = mainSubState('MAIN');
    h.caps = mainSubCaps();
    render();
    push({ phase: 'failed', fault: 'audio-failed', guard: { leaseId: 'x' }, mayOwnKey: true });

    const unkey = q<HTMLButtonElement>('[data-testid="rx-tx-unkey"]')!;
    expect(unkey.disabled).toBe(false);
    unkey.click();
    flushSync();
    expect(h.release).toHaveBeenCalledTimes(1);
  });
});

describe('scope/controls/global zones — placed, never falsely active', () => {
  it('all three are present and structurally disabled', () => {
    h.state = mainSubState('MAIN');
    h.caps = mainSubCaps();
    render();

    for (const zone of ['scope', 'controls', 'global']) {
      const el = q(`[data-testid="cockpit-zone-${zone}"]`)!;
      expect(el).not.toBeNull();
      expect(el.getAttribute('aria-disabled')).toBe('true');
      expect(el.dataset.zoneActive).toBe('false');
    }
  });
});

function push(next: Partial<Snapshot>): void {
  h.snapshot = { ...(h.snapshot as Snapshot), ...next };
  for (const listener of h.listeners) listener(h.snapshot);
  flushSync();
}
