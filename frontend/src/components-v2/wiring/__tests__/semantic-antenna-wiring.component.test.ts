/**
 * MOR-1309 — the semantic antenna surface wired into `SemanticRadioSurfaces`.
 *
 * `semantic/__tests__/AntennaSurface.test.ts` proves what the surface does with
 * a view model. This file proves what only the composed tree can prove, using
 * the REAL command bus, the REAL adapter and the REAL surface — only the
 * transport/runtime/authority SEAMS are spied:
 *
 *   (a) MOUNTING CANON (MOR-1304 ruling). The surface is control-bearing and
 *       no manifest declares an `antenna` zone, so it must NOT appear in the
 *       DUAL composition — bare OR through `zoned()`, which renders bare for an
 *       undeclared surface and is therefore not permission. The pin below
 *       renders `strips="dual"` with a view model that DOES carry the antenna
 *       group; a fixture that cannot see the surface would make it vacuous.
 *   (b) SAFETY, end to end: while the App-owned TX authority reports the
 *       transmitter keyed — or its RF state unknown — a forced click on a port
 *       reaches NO command. The gate is pinned at the transport seam, past both
 *       the disabled attribute and the surface's own handler.
 *   (c) The port intents route to the SHIPPED antenna commands, not a v3 fork.
 *   (d) The structural gate: a single-port radio renders the pre-1309 shape.
 *
 * Isolated pool by name (`*.component.test.ts`), per the MOR-1272 doctrine.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { flushSync, mount, unmount } from 'svelte';
import type { Capabilities } from '$lib/types/capabilities';
import type { ServerState } from '$lib/types/state';

type Snapshot = {
  phase: string; intent: string | null; guard: { leaseId: string } | null;
  radioTx: string; txRisk: string; mayOwnKey: boolean; fault: string | null;
};

const h = vi.hoisted(() => ({
  state: null as unknown,
  caps: null as unknown,
  snapshot: null as unknown,
  audio: { muted: false, rxEnabled: true, volume: 42 },
  audioConnected: true,
  listeners: new Set<(next: unknown) => void>(),
}));

vi.mock('$lib/transport/ws-client', () => ({ sendCommand: vi.fn() }));
vi.mock('$lib/stores/radio.svelte', () => ({
  getActiveReceiver: vi.fn(() => {
    const state = h.state as ServerState | null;
    return state?.active === 'SUB' ? state.sub ?? null : state?.main ?? null;
  }),
  getRadioState: vi.fn(() => h.state as ServerState | null),
  patchActiveReceiver: vi.fn(), patchRadioState: vi.fn(), patchReceiver: vi.fn(),
}));
vi.mock('$lib/stores/capabilities.svelte', () => ({
  getCapabilities: vi.fn(() => h.caps as Capabilities | null),
  getControlRange: vi.fn(() => null),
}));
vi.mock('$lib/runtime/commands/radio-intents', async () => {
  const { sendCommand } = await import('$lib/transport/ws-client');
  return { dispatchRadioIntent: ({ name, params }: { name: string; params: Record<string, unknown> }) => sendCommand(name, params) };
});
vi.mock('$lib/audio/audio-manager', () => ({
  audioManager: {
    get rxEnabled() { return true; },
    startRx: vi.fn(), stopRx: vi.fn(), setRxVolume: vi.fn(), setAudioConfig: vi.fn(),
  },
}));
vi.mock('$lib/runtime/frontend-runtime', () => ({
  runtime: {
    get state() { return h.state; },
    get caps() { return h.caps; },
    get audio() { return h.audio; },
    get connectionAudio() { return h.audioConnected; },
    get rxEnabled() { return true; },
    setVolume: vi.fn(), setMuted: vi.fn(), setRxLive: vi.fn(), setRxVolume: vi.fn(),
    // MOR-1312 slice 12B (rebase fix): the wiring now also hands the adapter
    // a scope-display snapshot (the FIFTH argument). This file tests
    // antenna, so this stays on its pre-1312 path regardless of these values.
    get defaultScopeStatus() {
      return {
        source: null, available: false, resourceSelected: false, demand: 0,
        lifecycle: 'inactive', transport: 'disconnected', frameSeen: false,
      };
    },
    get scope() { return { hardwareScopeConnected: false }; },
  },
}));
vi.mock('$lib/runtime', async () => ({
  runtime: (await import('$lib/runtime/frontend-runtime')).runtime,
}));
vi.mock('$lib/runtime/tx-controller/app-host', () => ({
  getAppTxController: () => ({
    snapshot: () => h.snapshot,
    subscribe: (listener: (next: unknown) => void) => {
      h.listeners.add(listener);
      return () => { h.listeners.delete(listener); };
    },
    start: vi.fn(), setIntent: vi.fn(), release: vi.fn(), resetFault: vi.fn(),
  }),
}));
vi.mock('$lib/runtime/adapters/mod-input-tx-guard.svelte', () => ({
  deriveModInputTxGuardProps: () => ({ visible: false, sourceLabel: 'MIC' }),
  getModInputTxGuardHandlers: () => ({ onSetLan: vi.fn(), onDismiss: vi.fn() }),
}));

import { sendCommand } from '$lib/transport/ws-client';
import SemanticRadioSurfaces from '../SemanticRadioSurfaces.svelte';
import { makeAntennaHandlers } from '../command-bus';

const RECEIVING: Snapshot = {
  phase: 'idle', intent: null, guard: null, radioTx: 'off', txRisk: 'none',
  mayOwnKey: false, fault: null,
};
const TRANSMITTING: Snapshot = {
  ...RECEIVING, phase: 'active', intent: 'latched', guard: { leaseId: 'x' },
  radioTx: 'on', txRisk: 'confirmed-on', mayOwnKey: true,
};
const RF_UNKNOWN: Snapshot = { ...RECEIVING, radioTx: 'unknown' };

const fresh = { storePath: 'x', observed: true, freshness: 'fresh', availability: 'available' };
const slot = (freqHz: number) => ({ freqHz, mode: 'USB', filterNum: 1, dataMode: 0 });

function liveState(over: Partial<ServerState> = {}): ServerState {
  const paths = [
    'active', 'split', 'dualWatch', 'txTarget', 'txAntenna', 'rxAntenna1', 'rxAntenna2',
    'tunerStatus',
  ];
  for (const rx of ['main', 'sub']) {
    paths.push(`${rx}.freqHz`, `${rx}.mode`, `${rx}.filter`, `${rx}.activeSlot`);
    for (const v of ['vfoA', 'vfoB']) {
      paths.push(`${rx}.${v}.freqHz`, `${rx}.${v}.mode`, `${rx}.${v}.filterNum`);
    }
  }
  const receiver = (hz: number) => ({
    ...slot(hz), vfoA: slot(hz), vfoB: slot(hz + 50000), activeSlot: 'A', filter: 1,
  });
  return {
    active: 'MAIN', split: false, dualWatch: false, ptt: false,
    txAntenna: 1, rxAntenna1: false, rxAntenna2: false, tunerStatus: 0,
    txTarget: { status: 'known', receiver: 'MAIN', slot: 'A', frequencyHz: 14250000 },
    main: receiver(14250000), sub: receiver(14300000),
    ...over,
    fieldStatus: Object.fromEntries(paths.map((p) => [p, fresh])),
  } as unknown as ServerState;
}

const liveCaps = (antennas: number, tags: readonly string[]): Capabilities => ({
  model: 'fixture', scope: false, audio: false, tx: true,
  capabilities: tags, antennas,
  receivers: 2, vfoScheme: 'main_sub', freqRanges: [], modes: [], filters: [],
  audioConfig: { sampleRate: 48000, channels: 1, codecs: ['pcm16'] },
  webrtc: { available: false, enabled: false },
  txBands: [{ start: 14000000, end: 14350000, name: '20m' }],
  scopeSource: null, audioFftAvailable: false,
} as unknown as Capabilities);

const ANTENNA_TAGS = ['tx', 'dual_rx', 'rx_antenna', 'tuner'] as const;

let target: HTMLDivElement;
let component: ReturnType<typeof mount> | null = null;

function render(props: { strips?: 'single' | 'dual' } = {}): void {
  target = document.createElement('div');
  document.body.appendChild(target);
  component = mount(SemanticRadioSurfaces, { target, props });
  flushSync();
}

const q = <T extends HTMLElement>(sel: string) => target.querySelector(sel) as T | null;
const el = (id: string) => q<HTMLElement>(`[data-testid="antenna-${id}"]`);
const btn = (id: string) => q<HTMLButtonElement>(`[data-testid="antenna-${id}"]`);
/** Past the `disabled` attribute, so a handler/command guard is proven alone. */
function forceClick(node: HTMLElement): void {
  node.dispatchEvent(new MouseEvent('click', { bubbles: true }));
  flushSync();
}

beforeEach(() => {
  h.state = liveState();
  h.caps = liveCaps(2, ANTENNA_TAGS);
  h.snapshot = { ...RECEIVING };
  h.listeners.clear();
  vi.mocked(sendCommand).mockClear();
});

afterEach(() => {
  if (component) unmount(component);
  component = null;
  document.body.innerHTML = '';
});

/* ── (a) the mounting canon ────────────────────────────────────── */

describe('the antenna surface never mounts in the dual composition (MOR-1304 canon)', () => {
  /**
   * MUTATION KILLED: mounting this surface in the cockpit, bare or through
   * `zoned()`. It renders focusable controls and no manifest declares an
   * `antenna` zone, so `zoneOwning()` returns null and `zoned` renders BARE —
   * outside every declared zone, breaking the MOR-1069 invariant that every
   * focusable control sits inside a declared zone with rx-tx last in the tab
   * order. The view model here DOES carry the group (asserted below), so this
   * pin cannot pass because the fixture is blind — the failure mode that made
   * `DualReceiverCockpit.component.test.ts` non-discriminating.
   */
  it('renders NO antenna surface in the dual composition, zoned or unzoned', () => {
    render({ strips: 'single' });
    expect(el('surface')).not.toBeNull();
    unmount(component!);
    component = null;
    document.body.innerHTML = '';

    render({ strips: 'dual' });
    expect(el('surface')).toBeNull();
    expect(target.innerHTML).not.toContain('antenna');
  });

  /**
   * The same claim stated as a DIFFERENCE, which is what MOR-1069 actually
   * cares about: carrying the antenna group must not add one focusable control
   * to the cockpit. Asserting "nothing outside a zone" outright would fail for
   * reasons this slice does not own (a standalone mount resolves no surface
   * plan, so every optional surface renders bare here) and would therefore be
   * a broken pin rather than a strict one.
   */
  it('adds no focusable control to the cockpit compared with a single-port radio', () => {
    const controls = () => [...target
      .querySelectorAll<HTMLElement>('button, input, select, [tabindex]')]
      .map((node) => node.dataset.testid ?? node.tagName);

    h.caps = liveCaps(1, ANTENNA_TAGS);
    render({ strips: 'dual' });
    const withoutGroup = controls();
    unmount(component!);
    component = null;
    document.body.innerHTML = '';

    h.caps = liveCaps(2, ANTENNA_TAGS);
    render({ strips: 'dual' });
    expect(controls()).toEqual(withoutGroup);
  });

  it('renders it bare in the single composition, outside every zone', () => {
    render();
    expect(el('surface')!.closest('[data-zone-id]')).toBeNull();
  });
});

/* ── (d) the structural gate ───────────────────────────────────── */

describe('the surface mounts only when the view model carries the group', () => {
  // MUTATION KILLED: mounting unconditionally — a single-port radio would gain
  // an antenna panel with nothing to switch between.
  it('renders no antenna surface for a radio declaring one TX port', () => {
    h.caps = liveCaps(1, ANTENNA_TAGS);
    render();
    expect(el('surface')).toBeNull();
  });

  it('omits RX-ANT for a multi-port radio that does not declare the capability', () => {
    h.caps = liveCaps(2, ['tx', 'tuner']);
    render();
    expect(el('surface')).not.toBeNull();
    expect(el('rx-toggle')).toBeNull();
  });
});

/* ── (c) the shipped command vocabulary ────────────────────────── */

describe('the antenna intents reach the shipped command vocabulary', () => {
  it('composes the shipped antenna handler factory', () => {
    const handlers = makeAntennaHandlers() as Record<string, unknown>;
    for (const name of ['onSelectAnt1', 'onSelectAnt2', 'onToggleRxAnt']) {
      expect(typeof handlers[name]).toBe('function');
    }
  });

  it('routes a port pick to the shipped set_antenna command for that port', () => {
    render();
    btn('port-2')!.click();
    flushSync();
    expect(sendCommand).toHaveBeenCalledExactlyOnceWith('set_antenna_2', { on: false });
  });

  it('routes an RX-ANT toggle to the shipped RX-antenna command', () => {
    render();
    btn('rx-toggle')!.click();
    flushSync();
    expect(sendCommand).toHaveBeenCalledExactlyOnceWith('set_rx_antenna_ant1', { on: true });
  });
});

/* ── (b) the under-power gate, pinned at the transport seam ────── */

describe('no antenna command leaves the tree while the transmitter is not idle', () => {
  // MUTATION KILLED (end to end, past `disabled` AND past the surface handler):
  // inverting or dropping the under-power gate. This is the whole point of the
  // slice — a relay switched under power damages the radio.
  it.each([['transmitting', TRANSMITTING], ['RF-state unknown', RF_UNKNOWN]] as const)(
    'sends nothing on a forced port click while %s', (_label, snapshot) => {
      h.snapshot = { ...snapshot };
      render();
      forceClick(btn('port-2')!);
      forceClick(btn('rx-toggle')!);
      expect(sendCommand).not.toHaveBeenCalled();
      expect(btn('port-2')!.disabled).toBe(true);
    },
  );

  // MUTATION KILLED: letting an UNOBSERVED tunerStatus fall through to "idle"
  // (MOR-1295 §3). The ATU fact is `txAux.atu` — this pin proves the LIVE
  // adapter path produces the not-ready state, not just a hand-built fixture.
  it('sends nothing while the live tunerStatus was never observed', () => {
    h.state = liveState({ tunerStatus: undefined } as Partial<ServerState>);
    render();
    expect(el('blocked')!.textContent).toContain('ATU');
    forceClick(btn('port-2')!);
    expect(sendCommand).not.toHaveBeenCalled();
  });

  // The gate must open again — a surface that can never switch is not a gate.
  it('sends the command once the authority reports a positively receiving radio', () => {
    h.snapshot = { ...TRANSMITTING };
    render();
    forceClick(btn('port-2')!);
    expect(sendCommand).not.toHaveBeenCalled();
    h.snapshot = { ...RECEIVING };
    for (const listener of h.listeners) listener(h.snapshot);
    flushSync();
    btn('port-2')!.click();
    flushSync();
    expect(sendCommand).toHaveBeenCalledExactlyOnceWith('set_antenna_2', { on: false });
  });
});
