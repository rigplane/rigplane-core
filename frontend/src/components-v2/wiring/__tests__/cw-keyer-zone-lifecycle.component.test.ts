/**
 * MOR-1368 (v3-rework S9) — THE ZONE LIFECYCLE TOUCHES NO COMMAND BUS.
 *
 * SAFETY-CRITICAL, and deliberately a SEPARATE file from
 * `semantic-cw-keyer-wiring.component.test.ts`: MOR-1310's rider requires that
 * the surface's own pins — the no-key-path sweep and the permit gates — stay
 * byte-unedited by this slice, so the new obligation lands beside them rather
 * than inside them.
 *
 * WHAT THIS PROVES. S9 declares `{ id: 'cw-keyer', surfaces: ['cwKeyer'] }` on
 * `desktop-v2`, which moves `CwKeyerSurface` from a bare mount into a
 * `zoned()` wrapper (`<div class="surface-zone" data-zone-id="cw-keyer">`).
 * That wrapper is new DOM around a SAFETY-CRITICAL surface, and the MOR-1339
 * trap class is exactly this: a zone lifecycle that fires an effect on mount,
 * on unmount, or twice on a re-mount. For break-in that would mean the act of
 * *arriving on a screen* — or of the plan re-resolving under the operator —
 * writing a keying setting into the radio.
 *
 * So the claim is not "no key command": it is ZERO commands of ANY kind, and
 * zero App-TX-authority calls, across mount → unmount → REMOUNT → unmount,
 * with the surface fully armed (permit `allowed`, break-in available, every
 * fact observed) so there is a live control tree for a lifecycle effect to
 * touch. The bus is asserted by exact emptiness, not by an allow-list, so a
 * command nobody thought to enumerate fails here too.
 *
 * The plan is built by the REAL `resolveSurfacePlan` over the REAL
 * `desktopV2Layout` — if S9's zone declaration is reverted, the zone sanity
 * assertion below fails and this pin cannot pass vacuously against a bare
 * mount.
 *
 * Isolated pool by name (`*.component.test.ts`), per the MOR-1272 doctrine.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { flushSync, mount, unmount } from 'svelte';
import type { Capabilities } from '$lib/types/capabilities';
import type { ServerState } from '$lib/types/state';

const h = vi.hoisted(() => ({
  state: null as unknown,
  caps: null as unknown,
  audio: { muted: false, rxEnabled: true, volume: 42 },
  txStart: vi.fn(),
  txRelease: vi.fn(),
  txSetIntent: vi.fn(),
  txResetFault: vi.fn(),
}));

vi.mock('$lib/transport/ws-client', () => ({ sendCommand: vi.fn() }));
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
    get connectionAudio() { return true; },
    get rxEnabled() { return true; },
    setVolume: vi.fn(), setMuted: vi.fn(), setRxLive: vi.fn(), setRxVolume: vi.fn(),
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
    snapshot: () => ({
      phase: 'idle', intent: null, guard: null, radioTx: 'off', txRisk: 'none',
      mayOwnKey: false, fault: null,
    }),
    subscribe: () => () => {},
    start: h.txStart, setIntent: h.txSetIntent, release: h.txRelease, resetFault: h.txResetFault,
  }),
}));
vi.mock('$lib/runtime/adapters/mod-input-tx-guard.svelte', () => ({
  deriveModInputTxGuardProps: () => ({ visible: false, sourceLabel: 'LAN' }),
  getModInputTxGuardHandlers: () => ({ onSetLan: vi.fn(), onDismiss: vi.fn() }),
}));

import { sendCommand } from '$lib/transport/ws-client';
import { resetRadioState, setRadioState } from '$lib/stores/radio.svelte';
import SemanticRadioSurfaces from '../SemanticRadioSurfaces.svelte';
import { desktopV2Layout } from '../../../presentation/layouts/declarations';
import { readWorkspace } from '../../../presentation/workspace/contract';
import {
  resolveSurfacePlan, SURFACE_PLAN_CONTEXT_KEY, type SurfacePlan,
} from '../../../presentation/workspace/resolution';

const fresh = { storePath: 'x', observed: true, freshness: 'fresh', availability: 'available' };
const slot = (freqHz: number) => ({ freqHz, mode: 'CW', filterNum: 1, dataMode: 0 });

/** Every CW fact observed, on both receivers, TX target inside 20m: ARMED. */
function liveState(): ServerState {
  const paths = [
    'active', 'split', 'dualWatch', 'txTarget', 'breakIn', 'breakInDelay', 'keySpeed',
    'cwPitch', 'dashRatio',
  ];
  for (const rx of ['main', 'sub']) {
    paths.push(
      `${rx}.freqHz`, `${rx}.mode`, `${rx}.filter`, `${rx}.activeSlot`,
      `${rx}.apfTypeLevel`, `${rx}.twinPeakFilter`,
    );
    for (const v of ['vfoA', 'vfoB']) {
      paths.push(`${rx}.${v}.freqHz`, `${rx}.${v}.mode`, `${rx}.${v}.filterNum`);
    }
  }
  const receiver = (hz: number) => ({
    ...slot(hz), vfoA: slot(hz), vfoB: slot(hz + 50000), activeSlot: 'A',
    filter: 1, apfTypeLevel: 0, twinPeakFilter: false,
  });
  return {
    active: 'MAIN', split: false, dualWatch: false, ptt: false,
    breakIn: 1, breakInDelay: 64, keySpeed: 24, cwPitch: 600, dashRatio: 0,
    txTarget: { status: 'known', receiver: 'MAIN', slot: 'A', frequencyHz: 14250000 },
    main: receiver(14250000), sub: receiver(14300000),
    fieldStatus: Object.fromEntries(paths.map((p) => [p, fresh])),
  } as unknown as ServerState;
}

const liveCaps = (): Capabilities => ({
  model: 'fixture', scope: false, audio: false, tx: true,
  capabilities: ['tx', 'cw', 'break_in', 'apf', 'twin_peak'],
  receivers: 2, vfoScheme: 'main_sub', freqRanges: [],
  modes: ['CW', 'CW-R', 'RTTY', 'USB'], filters: [1, 2, 3],
  audioConfig: { sampleRate: 48000, channels: 1, codecs: ['pcm16'] },
  webrtc: { available: false, enabled: false },
  txBands: [{ start: 14000000, end: 14350000, name: '20m' }],
  scopeSource: null, audioFftAvailable: false,
} as unknown as Capabilities);

/** What App resolves for `desktop-v2` given a default (empty) workspace. */
function desktopPlan(): SurfacePlan {
  return resolveSurfacePlan(desktopV2Layout, readWorkspace({ version: 1 }).workspace);
}

let target: HTMLDivElement;
let component: ReturnType<typeof mount> | null = null;

function render(plan: SurfacePlan): HTMLDivElement {
  target = document.createElement('div');
  document.body.appendChild(target);
  component = mount(SemanticRadioSurfaces, {
    target,
    props: {},
    context: new Map<unknown, unknown>([[SURFACE_PLAN_CONTEXT_KEY, () => plan]]),
  });
  flushSync();
  return target;
}

function tearDown(): void {
  if (component) unmount(component);
  component = null;
  target.remove();
}

const commandNames = () => vi.mocked(sendCommand).mock.calls.map(([name]) => name);
const authorityCalls = () => h.txStart.mock.calls.length + h.txRelease.mock.calls.length
  + h.txSetIntent.mock.calls.length + h.txResetFault.mock.calls.length;

beforeEach(() => {
  h.state = liveState();
  resetRadioState();
  setRadioState(liveState());
  h.caps = liveCaps();
  vi.mocked(sendCommand).mockClear();
  h.txStart.mockClear();
  h.txRelease.mockClear();
  h.txSetIntent.mockClear();
  h.txResetFault.mockClear();
});

afterEach(() => {
  if (component) unmount(component);
  component = null;
  document.body.innerHTML = '';
});

describe('the zone-owned cwKeyer mount never touches the command bus (MOR-1368, S9)', () => {
  // SANITY, and the anti-vacuity gate: the plan really does put the surface
  // inside the `cw-keyer` zone S9 declares. Reverting the manifest entry makes
  // this fail, so the lifecycle pins below can never pass against a bare
  // (pre-S9) mount and quietly stop testing zone ownership.
  it('desktop-v2 mounts the surface inside the declared cw-keyer zone', () => {
    const t = render(desktopPlan());
    const zone = t.querySelector('[data-zone-id="cw-keyer"]');
    expect(zone).not.toBeNull();
    expect(zone!.querySelector('[data-testid="cw-keyer-surface"]')).not.toBeNull();
    expect(t.querySelectorAll('[data-testid="cw-keyer-surface"]').length).toBe(1);
    // ARMED — otherwise a lifecycle effect would have nothing live to touch
    // and "zero commands" would be a statement about a disabled tree.
    expect(t.querySelector('[data-testid="cw-keyer-break-in-full"]')!.hasAttribute('disabled'))
      .toBe(false);
  });

  /**
   * THE PIN (MOR-1339 trap class). Mount, unmount, REMOUNT, unmount — the
   * shape a plan re-resolution or a workspace change produces at runtime — and
   * the bus must be EXACTLY empty at every step, not merely free of key-class
   * names. `toEqual([])` is the assertion because an allow-list would let a
   * "harmless" mount-time write through, and for break-in there is no such
   * thing.
   */
  it('sends zero commands at mount, at unmount, and across a remount', () => {
    const first = render(desktopPlan());
    expect(first.querySelector('[data-zone-id="cw-keyer"]')).not.toBeNull();
    expect(commandNames()).toEqual([]);
    expect(authorityCalls()).toBe(0);

    tearDown();
    flushSync();
    expect(commandNames()).toEqual([]);
    expect(authorityCalls()).toBe(0);

    const second = render(desktopPlan());
    // The remount really did rebuild the zone — otherwise the step below
    // would be asserting emptiness over a tree that never came back.
    expect(second.querySelector('[data-zone-id="cw-keyer"] [data-testid="cw-keyer-surface"]'))
      .not.toBeNull();
    expect(commandNames()).toEqual([]);
    expect(authorityCalls()).toBe(0);

    tearDown();
    flushSync();
    expect(commandNames()).toEqual([]);
    expect(authorityCalls()).toBe(0);
  });

  // The double-mount half of the same trap: two live instances at once (what a
  // mis-keyed `{#each}` or a duplicated zone declaration produces) must still
  // write nothing. It also pins that each instance carries exactly one
  // surface, so a duplicate zone id would be visible rather than silent.
  it('sends zero commands with two zone-owned instances mounted simultaneously', () => {
    const a = render(desktopPlan());
    const aComponent = component;
    const aTarget = target;
    const b = render(desktopPlan());

    expect(a.querySelectorAll('[data-testid="cw-keyer-surface"]').length).toBe(1);
    expect(b.querySelectorAll('[data-testid="cw-keyer-surface"]').length).toBe(1);
    expect(commandNames()).toEqual([]);
    expect(authorityCalls()).toBe(0);

    unmount(aComponent!);
    aTarget.remove();
    flushSync();
    expect(commandNames()).toEqual([]);
    expect(authorityCalls()).toBe(0);
  });
});
