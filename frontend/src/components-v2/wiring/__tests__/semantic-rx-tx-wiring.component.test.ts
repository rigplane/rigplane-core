/**
 * MOR-1065 — the semantic RX/TX surface wired to the REAL App TX authority.
 *
 * SAFETY-CRITICAL. This is the first slice where the pure surface's key/unkey
 * intents reach a controller, so the failure modes are operational, not
 * cosmetic:
 *   (a) a lease owner identity that changes between `start` and `release`,
 *       which makes unkey a silent no-op and strands a key DOWN
 *       (the MOR-1221/1226 identity discipline);
 *   (b) any condition inserted in front of the unkey path;
 *   (c) a `failed` phase with no App-owned way out (the RX/TX surface has no
 *       `resetFault` intent by design — recorded on the ticket).
 *
 * The controller here is a spy that records the exact `sourceId` and guard it
 * is handed; the surfaces are the real ones.
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
  listeners: new Set<(next: unknown) => void>(),
  start: vi.fn(),
  release: vi.fn(),
  setIntent: vi.fn(),
  resetFault: vi.fn(),
  selectVfo: vi.fn(),
  splitToggle: vi.fn(),
  dualWatchToggle: vi.fn(),
  setLan: vi.fn(),
  dismissWarning: vi.fn(),
  modInputGuard: { visible: false, sourceLabel: null } as { visible: boolean; sourceLabel: string | null },
  subscribeCalls: 0,
  unsubscribeCalls: 0,
  /** `listeners.size` sampled at the instant each `release` re-enters. */
  listenersAtRelease: [] as number[],
  /** MOR-1265: stand-in for every txAux intent. These fixtures declare no
   *  txAux capability and carry no txAux state, so the MOR-1244 evidence gate
   *  omits the group and none of these is ever reachable here. */
  txAuxNoop: vi.fn(),
}));

vi.mock('$lib/runtime', () => ({
  runtime: {
    get state() { return h.state; },
    get caps() { return h.caps; },
    // MOR-1279 slice 3B: the wiring now also hands the adapter an
    // App-owned RX-audio snapshot (the FOURTH argument). Muted with no
    // browser stream keeps every fixture below on its pre-1279 path.
    get audio() { return { muted: true, rxEnabled: false, volume: 0 }; },
    get connectionAudio() { return false; },
  },
}));
vi.mock('$lib/runtime/tx-controller/app-host', () => ({
  getAppTxController: () => ({
    snapshot: () => h.snapshot,
    subscribe: (listener: (next: unknown) => void) => {
      h.subscribeCalls += 1;
      h.listeners.add(listener);
      return () => { h.unsubscribeCalls += 1; h.listeners.delete(listener); };
    },
    start: h.start,
    setIntent: h.setIntent,
    // Sample the live subscription count at the instant release re-enters —
    // this is what makes the teardown ORDER observable (see the teardown suite).
    release: (...args: unknown[]) => {
      h.listenersAtRelease.push(h.listeners.size);
      return (h.release as (...a: unknown[]) => unknown)(...args);
    },
    resetFault: h.resetFault,
  }),
}));
// The MOR-617 preflight's own adapter — stubbed so the test drives the
// warning's real trigger condition (`visible`) without a live TX guard.
vi.mock('$lib/runtime/adapters/mod-input-tx-guard.svelte', () => ({
  deriveModInputTxGuardProps: () => h.modInputGuard,
  getModInputTxGuardHandlers: () => ({ onSetLan: h.setLan, onDismiss: h.dismissWarning }),
}));
vi.mock('../command-bus', () => ({
  makeVfoHandlers: () => ({
    onVfoSelect: h.selectVfo,
    onSplitToggle: h.splitToggle,
    onDualWatchToggle: h.dualWatchToggle,
  }),
  // MOR-1265 — the wiring now also composes the txAux intent vocabulary.
  makeVoxHandlers: () => ({
    onVoxToggle: h.txAuxNoop, onVoxGainChange: h.txAuxNoop,
    onAntiVoxGainChange: h.txAuxNoop, onVoxDelayChange: h.txAuxNoop,
  }),
  makeTxHandlers: () => ({
    onRfPowerChange: h.txAuxNoop, onMicGainChange: h.txAuxNoop, onAtuToggle: h.txAuxNoop,
    onAtuTune: h.txAuxNoop, onVoxToggle: h.txAuxNoop, onCompToggle: h.txAuxNoop,
    onCompLevelChange: h.txAuxNoop, onMonToggle: h.txAuxNoop,
    onMonLevelChange: h.txAuxNoop, onDriveGainChange: h.txAuxNoop,
  }),
  // MOR-1279 slice 3B: the RX-audio intent vocabulary.
  makeRxAudioHandlers: () => ({ onMonitorModeChange: h.txAuxNoop, onAfLevelChange: h.txAuxNoop }),
  makeAudioRoutingHandlers: () => ({ onFocusChange: h.txAuxNoop, onSplitStereoChange: h.txAuxNoop }),
  // MOR-1304 — the wiring now also composes the modeFilter/filterPassband
  // intent vocabulary; `makeModeHandlers` is composed at both call sites
  // (rxAudio's MOD-input remedy and filterIntents), so the stub carries both.
  makeModeHandlers: () => ({
    onModInputChange: h.txAuxNoop, onModeChange: h.txAuxNoop, onDataModeChange: h.txAuxNoop,
  }),
  makeFilterHandlers: () => ({
    onFilterChange: h.txAuxNoop, onFilterWidthChange: h.txAuxNoop, onFilterShapeChange: h.txAuxNoop,
    onIfShiftChange: h.txAuxNoop, onPbtInnerChange: h.txAuxNoop, onPbtOuterChange: h.txAuxNoop,
  }),
}));

import SemanticRadioSurfaces from '../SemanticRadioSurfaces.svelte';

const IDLE: Snapshot = {
  phase: 'idle', intent: null, guard: null, radioTx: 'off', txRisk: 'none',
  mayOwnKey: false, fault: null,
};

const fresh = { storePath: 'x', observed: true, freshness: 'fresh', availability: 'available' };
const slot = (freqHz: number) => ({ freqHz, mode: 'USB', filterNum: 1, dataMode: 0 });

function liveState(): ServerState {
  const paths = ['active', 'split', 'dualWatch', 'txTarget'];
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
    txTarget: { status: 'known', receiver: 'MAIN', slot: 'A', frequencyHz: 14250000 },
    main: receiver(14250000), sub: receiver(14300000),
    fieldStatus: Object.fromEntries(paths.map((p) => [p, fresh])),
  } as unknown as ServerState;
}

const liveCaps = (): Capabilities => ({
  model: 'fixture', scope: false, audio: true, tx: true,
  capabilities: ['audio', 'tx', 'dual_rx'], receivers: 2, vfoScheme: 'main_sub',
  freqRanges: [], modes: [], filters: [],
  audioConfig: { sampleRate: 48000, channels: 1, codecs: ['pcm16'] },
  webrtc: { available: false, enabled: false },
  txBands: [{ start: 14000000, end: 14350000, name: '20m' }],
  scopeSource: null, audioFftAvailable: false,
} as unknown as Capabilities);

let target: HTMLDivElement;
let component: ReturnType<typeof mount> | null = null;

function render(props: { strips?: 'single' | 'dual' } = {}): void {
  target = document.createElement('div');
  document.body.appendChild(target);
  component = mount(SemanticRadioSurfaces, { target, props });
  flushSync();
}

/** Push a new authority snapshot exactly as the real controller would. */
function push(next: Partial<Snapshot>): void {
  h.snapshot = { ...(h.snapshot as Snapshot), ...next };
  for (const listener of h.listeners) listener(h.snapshot);
  flushSync();
}

const q = <T extends HTMLElement>(sel: string) => target.querySelector(sel) as T | null;

beforeEach(() => {
  h.state = liveState();
  h.caps = liveCaps();
  h.snapshot = { ...IDLE };
  h.listeners.clear();
  h.subscribeCalls = 0;
  h.unsubscribeCalls = 0;
  h.listenersAtRelease = [];
  h.start.mockReset();
  h.release.mockReset();
  h.setIntent.mockReset();
  h.resetFault.mockReset();
  h.selectVfo = vi.fn();
  h.splitToggle = vi.fn();
  h.dualWatchToggle = vi.fn();
  h.setLan = vi.fn();
  h.dismissWarning = vi.fn();
  h.modInputGuard = { visible: false, sourceLabel: null };
});

afterEach(() => {
  if (component) unmount(component);
  component = null;
  document.body.innerHTML = '';
});

describe('the surfaces render from the live adapter output', () => {
  it('mounts both semantic surfaces with the derived view model', () => {
    render();
    expect(q('[data-testid="vfo-surface"]')).not.toBeNull();
    expect(q('[data-testid="rx-tx-surface"]')).not.toBeNull();
    expect(target.querySelectorAll('[data-vfo-tile]')).toHaveLength(4);
    expect(q('[data-testid="rx-tx-target"]')?.dataset.target).toBe('known');
  });

  // MUTATION KILLED: flipping the `strips` default from 'single' to 'dual'
  // (MOR-1067 review cycle 1, F4). The default is what every existing consumer
  // gets — RadioLayout.svelte mounts this with no `strips` prop — so a flipped
  // default silently re-composes sdr-test/desktop into per-receiver channel
  // strips. Nothing else in the suite distinguishes the two modes: this file's
  // fixtures render the same inner surface either way.
  it('defaults to the single unsliced surface — no channel-strip wrapper at all', () => {
    render();
    expect(q('[data-testid="channel-strips"]')).toBeNull();
    expect(target.querySelectorAll('[data-testid="vfo-surface"]')).toHaveLength(1);
  });

  // MOR-1069, finding N1 (routed from the MOR-1068 verification). MOR-1068
  // wrapped the RX/TX surface in an inert `display: contents` zone shell on
  // EVERY path, so the single/default composition — the one sdr-test, the LCD
  // layouts and MOBILE all mount — stopped being element-identical to its
  // pre-cockpit shape. Layout was preserved, nothing queried the old position,
  // and it was accepted as a trade-off; MOR-1069 collapses it back out, and
  // this is the element-shape expectation re-pinned so it cannot drift back.
  // MUTATION KILLED: reintroducing an always-on wrapper (or extending the
  // cockpit's zone binding to the default path, which would put a zone id on
  // a layout whose manifest never declared one).
  it('mounts the RX/TX surface bare — no zone wrapper on the default path', () => {
    render();
    const root = q('[data-testid="semantic-radio-surfaces"]')!;
    const surface = q('[data-testid="rx-tx-surface"]')!;
    expect(surface.parentElement).toBe(root);
    expect(root.querySelector('.rx-tx-zone')).toBeNull();
    expect(target.querySelectorAll('[data-zone-id]')).toHaveLength(0);
    // Still exactly one TX action surface — the branch must not duplicate it.
    expect(target.querySelectorAll('[data-testid="rx-tx-surface"]')).toHaveLength(1);
    expect(target.querySelectorAll('[data-testid="rx-tx-key"]')).toHaveLength(1);
  });

  it('renders no surfaces at all rather than guessing when capabilities are absent', () => {
    h.caps = null;
    render();
    expect(q('[data-testid="vfo-surface"]')).toBeNull();
    expect(q('[data-testid="rx-tx-surface"]')).toBeNull();
  });

  it('routes the VFO selection intent to the command bus with the real slot id', () => {
    render();
    const buttons = [...target.querySelectorAll<HTMLButtonElement>('[data-vfo-select]')];
    buttons[0].click();
    flushSync();
    expect(h.selectVfo).toHaveBeenCalledWith('MAIN', 'B');
  });
});

describe('TX intents reach the App controller under one stable owner identity', () => {
  // MUTATION KILLED: deriving `sourceId` per render (or per call) instead of
  // once per instance. `release` would carry an id the controller never
  // registered, the model's `event.sourceId === state.sourceId` check would
  // reject it, and the key would stay DOWN with the UI showing "unkeyed".
  it('releases under the exact sourceId it started with, across re-renders', () => {
    render();
    q<HTMLButtonElement>('[data-testid="rx-tx-key"]')!.click();
    flushSync();
    expect(h.start).toHaveBeenCalledTimes(1);
    const [startSource, leaseId, intent] = h.start.mock.calls[0];
    expect(intent).toBe('latched');
    expect(leaseId).toContain(startSource);

    // Everything the component renders from changes underneath it: a new
    // authority snapshot AND a new radio state. Any per-render binding is
    // rebuilt here.
    const guard = { leaseId };
    push({ phase: 'active', intent: 'latched', guard, radioTx: 'on', txRisk: 'confirmed-on', mayOwnKey: true });
    h.state = { ...liveState(), split: true };
    flushSync();

    q<HTMLButtonElement>('[data-testid="rx-tx-unkey"]')!.click();
    flushSync();
    expect(h.release).toHaveBeenCalledTimes(1);
    expect(h.release).toHaveBeenCalledWith(startSource, guard);
  });

  // MUTATION KILLED: re-subscribing (or re-reading a cached snapshot) per
  // render — a leaked subscription per render both grows without bound and
  // lets a stale listener overwrite the live snapshot.
  it('subscribes to the authority exactly once and unsubscribes on destroy', () => {
    render();
    push({ radioTx: 'unknown' });
    h.state = { ...liveState(), dualWatch: true };
    flushSync();
    expect(h.subscribeCalls).toBe(1);

    unmount(component!);
    component = null;
    expect(h.unsubscribeCalls).toBe(1);
  });

  // MUTATION KILLED: two mounted wiring instances sharing one lease id — one
  // would be able to release the other's lease (the TxPanel precedent).
  it('gives a second mounted instance a different lease owner', () => {
    render();
    q<HTMLButtonElement>('[data-testid="rx-tx-key"]')!.click();
    flushSync();
    const first = h.start.mock.calls[0][0];

    const second = document.createElement('div');
    document.body.appendChild(second);
    const other = mount(SemanticRadioSurfaces, { target: second });
    flushSync();
    (second.querySelector('[data-testid="rx-tx-key"]') as HTMLButtonElement).click();
    flushSync();
    expect(h.start.mock.calls[1][0]).not.toBe(first);
    unmount(other);
  });

  it('passes the authority snapshot straight through to the surface', () => {
    render();
    push({ phase: 'key-confirm-pending', txRisk: 'uncertain', mayOwnKey: true, radioTx: 'off' });
    expect(q('[data-testid="rx-tx-state"]')?.dataset.rf).toBe('uncertain');
    expect(h.start).not.toHaveBeenCalled();
  });
});

describe('the unkey path is ungated end to end', () => {
  // MUTATION KILLED: gating unkey on phase/permit/fault or on the view model
  // agreeing that transmission is happening. Stopping TX must never depend on
  // this layer's opinion.
  it.each([
    ['a fault is latched', { phase: 'failed', fault: 'on-timeout' }],
    ['the radio reports RX', { radioTx: 'off', txRisk: 'none' }],
    ['the RF state is unknown', { radioTx: 'unknown' }],
    ['a release is already in flight', { phase: 'releasing' }],
  ] as const)('still releases while %s', (_label, over) => {
    render();
    const guard = { leaseId: 'lease-x' };
    push({ ...over, guard, mayOwnKey: true } as Partial<Snapshot>);

    const unkey = q<HTMLButtonElement>('[data-testid="rx-tx-unkey"]')!;
    expect(unkey.disabled).toBe(false);
    unkey.click();
    flushSync();
    expect(h.release).toHaveBeenCalledWith(expect.any(String), guard);
  });

  // MUTATION KILLED: caching the guard at render time. A guard captured before
  // the lease generation bumped is rejected by the controller, so the unkey
  // silently does nothing.
  it('releases the guard the authority holds NOW, not the one seen at render', () => {
    render();
    push({ phase: 'active', guard: { leaseId: 'gen-1' }, mayOwnKey: true, radioTx: 'on' });
    // A regeneration the component never re-rendered for.
    h.snapshot = { ...(h.snapshot as Snapshot), guard: { leaseId: 'gen-2' } };

    q<HTMLButtonElement>('[data-testid="rx-tx-unkey"]')!.click();
    expect(h.release).toHaveBeenCalledWith(expect.any(String), { leaseId: 'gen-2' });
  });
});

describe('a failed phase has an App-owned way out', () => {
  // MUTATION KILLED: relying on the RX/TX surface for recovery. It has no
  // `resetFault` intent by design, and `keyBlockedReasons` disables the key
  // action while `fault !== null` — so without this affordance the operator is
  // stuck behind a failed phase with no UI exit at all.
  it('offers a recovery control only while the authority phase is failed', () => {
    render();
    expect(q('[data-testid="tx-fault-reset"]')).toBeNull();

    push({ phase: 'failed', fault: 'audio-failed' });
    expect(q<HTMLButtonElement>('[data-testid="rx-tx-key"]')!.disabled).toBe(true);
    const reset = q<HTMLButtonElement>('[data-testid="tx-fault-reset"]');
    expect(reset).not.toBeNull();

    reset!.click();
    flushSync();
    expect(h.resetFault).toHaveBeenCalledTimes(1);

    push({ phase: 'idle', fault: null });
    expect(q('[data-testid="tx-fault-reset"]')).toBeNull();
  });

  // MUTATION KILLED: an unconditional `tx.resetFault()` at the top of
  // `requestKey`. The failed-phase variant below CANNOT kill it — the key
  // button is disabled there, so the click never reaches the handler and the
  // assertion is vacuous (proven: the mutation left the suite 14/14 green).
  // This one drives the handler through the ENABLED path, where the mutation
  // has to show. "No implicit reset" is a deliberate contract: recovery is an
  // explicit operator action, per the ticket's recorded decision.
  it('never resets the fault as a side effect of a key request (enabled path)', () => {
    render();
    const key = q<HTMLButtonElement>('[data-testid="rx-tx-key"]')!;
    expect(key.disabled).toBe(false);

    key.click();
    flushSync();

    expect(h.start).toHaveBeenCalledTimes(1);
    expect(h.resetFault).not.toHaveBeenCalled();
  });

  it('leaves the key action disabled in a failed phase, so no reset can leak through it', () => {
    render();
    push({ phase: 'failed', fault: 'audio-failed' });
    q<HTMLButtonElement>('[data-testid="rx-tx-key"]')!.click();
    flushSync();
    expect(h.resetFault).not.toHaveBeenCalled();
    expect(h.start).not.toHaveBeenCalled();
  });
});

describe('teardown releases the lease rather than stranding the transmitter', () => {
  // MUTATION KILLED: `onDestroy(() => stopWatchingTx())` alone. `requestKey`
  // starts a LATCHED lease that outlives this component; MOR-1060 destroys the
  // presentation subtree on any skinId change (resize across 640px, a skin
  // preference change, a capability update). The App TX controller keeps the
  // lease, `model.ts` refuses a release from any other sourceId, `start` is a
  // no-op off-idle, and AppGlobalHost exposes no unkey — so without a release
  // here the key is DOWN with no UI exit anywhere.
  it('releases the live lease exactly once when the subtree is destroyed', () => {
    render();
    q<HTMLButtonElement>('[data-testid="rx-tx-key"]')!.click();
    flushSync();
    const [owner, leaseId] = h.start.mock.calls[0];
    const guard = { leaseId };
    push({
      phase: 'active', intent: 'latched', guard, radioTx: 'on',
      txRisk: 'confirmed-on', mayOwnKey: true,
    });

    unmount(component!);
    component = null;

    expect(h.release).toHaveBeenCalledTimes(1);
    expect(h.release).toHaveBeenCalledWith(owner, guard);
    expect(h.unsubscribeCalls).toBe(1);
    // MUTATION KILLED: swapping the teardown order (release BEFORE
    // unsubscribe). The release is fail-closed and re-enters the controller
    // synchronously, which re-emits state to every live listener — into a
    // component Svelte is mid-way through destroying. Sampling the live
    // subscription count at the instant release re-enters is what makes the
    // ORDER observable at all: [0] means we were already detached, [1] means
    // the release can still bounce back into the dying component. Same
    // documented order as TxPanel's `stopWatchingTx(); ptt.destroy();`.
    expect(h.listenersAtRelease).toEqual([0]);
  });

  // MUTATION KILLED: releasing the render-time `txState.guard` instead of the
  // live snapshot — a lease regenerated after the last render (the model bumps
  // `generation` on every release/authority churn) would be released under a
  // stale guard, which `sameGuard` rejects. Same failure as never releasing.
  it('releases the guard the authority holds at teardown, not the last rendered one', () => {
    render();
    q<HTMLButtonElement>('[data-testid="rx-tx-key"]')!.click();
    flushSync();
    const [owner] = h.start.mock.calls[0];
    push({ phase: 'active', guard: { leaseId: 'gen-1' }, mayOwnKey: true, radioTx: 'on' });
    // A regeneration the component never re-rendered for.
    h.snapshot = { ...(h.snapshot as Snapshot), guard: { leaseId: 'gen-2' } };

    unmount(component!);
    component = null;

    expect(h.release).toHaveBeenCalledWith(owner, { leaseId: 'gen-2' });
  });

  it('issues no release when it holds no lease', () => {
    render();
    unmount(component!);
    component = null;
    expect(h.release).not.toHaveBeenCalled();
  });

  // The operator-visible half of F1: after the swap, the incoming subtree is
  // usable again. Under the un-released mutation the controller is stuck
  // off-idle and this key request can never take a lease.
  it('lets a remounted instance key again, under its own owner identity', () => {
    render();
    q<HTMLButtonElement>('[data-testid="rx-tx-key"]')!.click();
    flushSync();
    const [firstOwner, leaseId] = h.start.mock.calls[0];
    push({ phase: 'active', intent: 'latched', guard: { leaseId }, mayOwnKey: true, radioTx: 'on' });

    unmount(component!);            // the swap
    component = null;
    expect(h.release).toHaveBeenCalledTimes(1);

    h.snapshot = { ...IDLE };       // the release lands; the authority is idle
    render();                       // the incoming presentation
    q<HTMLButtonElement>('[data-testid="rx-tx-key"]')!.click();
    flushSync();

    expect(h.start).toHaveBeenCalledTimes(2);
    expect(h.start.mock.calls[1][0]).not.toBe(firstOwner);
  });
});

describe('the migrated layout keeps the MOR-617 network-voice-TX preflight', () => {
  // MUTATION KILLED: dropping <ModInputTxWarning /> from the wiring. It ships
  // inside TxPanel, which this layout suppresses — but this layout can now key
  // network voice TX (the controller's start-audio effect IS that path), so
  // without it the operator keys into a mis-routed MOD input with no warning
  // and no one-click fix.
  it('shows the warning under the same trigger condition the panel used', () => {
    h.modInputGuard = { visible: true, sourceLabel: 'MIC' };
    render();
    const warning = q('[data-testid="mod-input-tx-warning"]');
    expect(warning).not.toBeNull();
    expect(q('[data-testid="mod-input-set-lan"]')).not.toBeNull();

    q<HTMLButtonElement>('[data-testid="mod-input-set-lan"]')!.click();
    flushSync();
    expect(h.setLan).toHaveBeenCalledTimes(1);
  });

  it('stays out of the way when the preflight is satisfied', () => {
    render();
    expect(q('[data-testid="mod-input-tx-warning"]')).toBeNull();
  });

  it('is not gated on the view model — it shows even before capabilities load', () => {
    h.caps = null;
    h.modInputGuard = { visible: true, sourceLabel: 'MIC' };
    render();
    expect(q('[data-testid="rx-tx-surface"]')).toBeNull();
    expect(q('[data-testid="mod-input-tx-warning"]')).not.toBeNull();
  });
});

describe('MOR-1258 — the three TX-adjacent alerts join the rx-tx zone in the dual composition', () => {
  // The owner ruling (2026-08-04, gate item (b)): `tx-fault-reset` and the
  // two ModInputTxWarning buttons render inside the rx-tx zone's bound
  // element when `strips="dual"` — the only composition with a bound zone at
  // all (MOR-1069). Direct containment checks at the wiring level, one layer
  // below the full cockpit shell mount in
  // `skins/dual-receiver-cockpit/__tests__/DualReceiverCockpit.component.test.ts`.
  it('contains tx-fault-reset inside the rx-tx zone while a fault is latched', () => {
    render({ strips: 'dual' });
    push({ phase: 'failed', fault: 'audio-failed' });

    const zone = q('.rx-tx-zone')!;
    expect(zone).not.toBeNull();
    expect(zone.contains(q('[data-testid="tx-fault-reset"]'))).toBe(true);
  });

  it('contains both ModInputTxWarning buttons inside the rx-tx zone', () => {
    h.modInputGuard = { visible: true, sourceLabel: 'MIC' };
    render({ strips: 'dual' });

    const zone = q('.rx-tx-zone')!;
    expect(zone.contains(q('[data-testid="mod-input-set-lan"]'))).toBe(true);
    expect(zone.contains(q('[data-testid="mod-input-dismiss"]'))).toBe(true);
  });

  // The single/default path has no bound zone at all (MOR-1069) — the
  // ticket's explicit, honest carve-out (no containment is possible there).
  // The alerts keep their pre-MOR-1258 bare placement, unchanged.
  it('leaves the alerts bare on the single/default path — there is no zone to contain them in', () => {
    render();
    push({ phase: 'failed', fault: 'audio-failed' });
    expect(target.querySelectorAll('[data-zone-id]')).toHaveLength(0);
    expect(q('.rx-tx-zone')).toBeNull();
    expect(q('[data-testid="tx-fault-reset"]')).not.toBeNull();
  });

  // MOR-617's invariant survives the containment move even in the dual
  // composition: the warning still is not gated on the view model.
  it('still shows the MOD-input warning before capabilities load, in the dual composition too', () => {
    h.caps = null;
    h.modInputGuard = { visible: true, sourceLabel: 'MIC' };
    render({ strips: 'dual' });
    expect(q('[data-testid="rx-tx-surface"]')).toBeNull();
    const zone = q('.rx-tx-zone')!;
    expect(zone).not.toBeNull();
    expect(zone.contains(q('[data-testid="mod-input-tx-warning"]'))).toBe(true);
  });
});
