/**
 * MOR-1060 — lazy presentation loading with stale-resolution cancellation.
 *
 * These tests pin the composition-root contract, not any skin's markup:
 *   1. only the selected presentation's module is ever loaded;
 *   2. the committed presentation stays mounted while the next one resolves,
 *      and a stale resolution never mounts and never writes state;
 *   3. A -> B -> A leaves exactly one instance of the latest request, with
 *      every bridge lease a distinct object released exactly once;
 *   4. a switch keeps bootstrap, TX authority and the App-global host
 *      identical — nothing above the presentation boundary is replayed;
 *   5. loader failure keeps last-known-good; an initial failure yields an
 *      inert App-owned surface; a late failure after teardown is inert.
 */
import { readFileSync } from 'node:fs';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { flushSync, mount, tick, unmount } from 'svelte';
import type { SkinId } from '../skins/registry';

type Pending = { id: SkinId; resolve: (component: unknown) => void; reject: (err: unknown) => void };
type LeaseEvent = { op: 'acquire' | 'release'; resource: string; consumer: string; mounted: string | null };

const h = vi.hoisted(() => ({
  pending: [] as Pending[],
  loadSkin: vi.fn(),
  resolveSkinId: vi.fn(),
  plan: vi.fn(),
  demand: new Map<string, number>(),
  leaseEvents: [] as LeaseEvent[],
  issuedLeases: [] as object[],
  releasedLeases: [] as object[],
  resourcesEnded: false,
  bootstrap: vi.fn(),
  bootstrapCleanup: vi.fn(),
  initBattery: vi.fn(),
  provide: vi.fn(),
  registerBarrier: vi.fn(),
  txHost: undefined as { refreshAuthority: ReturnType<typeof vi.fn>; dispose: ReturnType<typeof vi.fn> } | undefined,
}));

// The presentation subtree under test is the *loader result*, so the skin
// registry is fully faked: `loadSkin` hands back a deferred promise per call
// and the test decides when — and whether — each one resolves.
vi.mock('../skins/registry', () => ({
  resolveSkinId: h.resolveSkinId,
  loadSkin: h.loadSkin,
  presentationResourcePlan: h.plan,
}));

vi.mock('../lib/runtime/frontend-runtime', () => ({
  runtime: {
    get state() { return { stateRevision: 1, freshnessRevision: 1, observationSeq: 1, ptt: false }; },
    get caps() { return { tx: true, capabilities: ['tx'] }; },
    bootstrap: h.bootstrap,
  },
  presentationResources: {
    snapshot: (resource: string) => ({ demand: h.demand.get(resource) ?? 0 }),
    acquire: (resource: string, consumer: string) => {
      if (h.resourcesEnded) throw new Error('resource demand session is torn down');
      h.leaseEvents.push({ op: 'acquire', resource, consumer, mounted: mountedSkin() });
      h.demand.set(resource, (h.demand.get(resource) ?? 0) + 1);
      // A distinct, frozen object per acquisition — never a reused handle.
      const lease = Object.freeze({ resource, consumer });
      h.issuedLeases.push(lease);
      return lease;
    },
    release: (lease: { resource: string; consumer: string }) => {
      h.leaseEvents.push({ op: 'release', resource: lease.resource, consumer: lease.consumer, mounted: mountedSkin() });
      h.demand.set(lease.resource, (h.demand.get(lease.resource) ?? 0) - 1);
      h.releasedLeases.push(lease);
      return true;
    },
  },
}));

vi.mock('$lib/runtime/system-controller', () => ({
  systemController: { registerPreDisconnectBarrier: h.registerBarrier },
}));
vi.mock('$lib/runtime/tx-controller/app-host', () => ({ provideAppTxControllerHost: h.provide }));
vi.mock('$lib/i18n', () => ({ t: (key: string) => key }));
vi.mock('$lib/stores/capabilities.svelte', () => ({ hasAnyScope: () => false }));
vi.mock('$lib/stores/layout.svelte', () => ({ getLayoutMode: () => 'standard' }));
vi.mock('../lib/utils/battery', () => ({ initBatteryMonitor: h.initBattery }));
vi.mock('../lib/media/media-session', () => ({ initMediaSession: vi.fn(), destroyMediaSession: vi.fn() }));
// The App-global host and local-extensions host have their own suites; here
// they only need a stable, identifiable node so a switch can be shown not to
// disturb them.
vi.mock('../AppGlobalHost.svelte', async () => {
  const stub = await import('../components-v2/layout/__tests__/SpectrumPanelStub.svelte');
  return { default: stub.default };
});
vi.mock('../lib/local-extensions/LocalExtensionsHost.svelte', async () => {
  const stub = await import('../components-v2/layout/__tests__/SpectrumPanelStub.svelte');
  return { default: stub.default };
});

import App from '../App.svelte';
import LayoutStub from './LayoutStub.svelte';

/**
 * A distinct presentation component per tag. Delegating to the compiled
 * `LayoutStub` keeps real Svelte mount/destroy semantics (so "one instance"
 * and "never mounted" are DOM facts), while the wrapper gives each tag its
 * own component identity — exactly what the commit path switches on.
 *
 * The tag is usually the skin id, but any string works: a test that has to
 * tell two resolutions of the SAME skin apart tags one of them separately
 * and reads the winner back off `data-skin`.
 */
type ClientComponent = (anchor: unknown, props: Record<string, unknown>) => void;
const stubFor = new Map<string, ClientComponent>();
function presentationStub(id: string): ClientComponent {
  let stub = stubFor.get(id);
  if (!stub) {
    stub = (anchor, props) => (LayoutStub as unknown as ClientComponent)(anchor, { ...props, skinId: id });
    stubFor.set(id, stub);
  }
  return stub;
}

const mountedSkin = (): string | null =>
  document.querySelector('.layout-stub')?.getAttribute('data-skin') ?? null;
const mountedCount = () => document.querySelectorAll('.layout-stub').length;
const globalHost = () => document.querySelector('.spectrum-panel-stub');
const errorSurface = () => document.querySelector('[data-testid="presentation-load-error"]');

/** Drain the microtask queue past App's post-commit `await tick()`. */
async function settle(): Promise<void> {
  for (let i = 0; i < 4; i++) await Promise.resolve();
  await tick();
  flushSync();
}

function mountApp() {
  const target = document.createElement('div');
  document.body.appendChild(target);
  const instance = mount(App, { target });
  flushSync();
  return instance;
}

/** Change the viewport so `resolveSkinId` returns the other skin. */
function selectSkin(id: SkinId): void {
  Object.defineProperty(window, 'innerWidth', { configurable: true, value: id === 'mobile' ? 390 : 1200 });
  window.dispatchEvent(new Event('resize'));
  flushSync();
}

/**
 * Resolve the oldest still-pending load for `id`. `as` overrides which stub
 * that resolution hands back, so two pending requests for the same skin can
 * be told apart in the DOM.
 */
function completeLoad(id: SkinId, as: string = id): void {
  const index = h.pending.findIndex((entry) => entry.id === id);
  if (index < 0) throw new Error(`no pending load for ${id}`);
  h.pending.splice(index, 1)[0].resolve(presentationStub(as));
}

function failLoad(id: SkinId, message = 'chunk load failed'): void {
  const index = h.pending.findIndex((entry) => entry.id === id);
  if (index < 0) throw new Error(`no pending load for ${id}`);
  h.pending.splice(index, 1)[0].reject(new Error(message));
}

const requestedSkins = () => h.loadSkin.mock.calls.map((call) => call[0] as SkinId);

let consoleError: ReturnType<typeof vi.spyOn>;

beforeEach(() => {
  vi.clearAllMocks();
  h.pending.length = 0;
  h.leaseEvents.length = 0;
  h.issuedLeases.length = 0;
  h.releasedLeases.length = 0;
  h.demand.clear();
  h.resourcesEnded = false;
  document.body.innerHTML = '';
  Object.defineProperty(window, 'innerWidth', { configurable: true, value: 1200 });
  Object.defineProperty(window, 'innerHeight', { configurable: true, value: 800 });
  h.bootstrap.mockResolvedValue(h.bootstrapCleanup);
  h.initBattery.mockResolvedValue(vi.fn());
  h.resolveSkinId.mockImplementation(({ isMobile }: { isMobile: boolean }) => (isMobile ? 'mobile' : 'desktop-v2'));
  h.loadSkin.mockImplementation(
    (id: SkinId) => new Promise((resolve, reject) => { h.pending.push({ id, resolve, reject }); }),
  );
  h.plan.mockImplementation((id: SkinId) => (id === 'mobile' ? ['hardware-scope'] : ['hardware-scope', 'audio-fft']));
  h.provide.mockImplementation(() => {
    h.txHost = { refreshAuthority: vi.fn(), dispose: vi.fn() };
    return { ...h.txHost, release: vi.fn() };
  });
  consoleError = vi.spyOn(console, 'error').mockImplementation(() => {});
});

describe('lazy presentation loading', () => {
  // MUTATION KILLED: re-introducing a static `import RadioLayout from ...`
  // (or eagerly pre-loading every entrypoint) — the app would ship and
  // execute every presentation module regardless of the resolved skin.
  it('requests only the selected presentation entrypoint', async () => {
    const instance = mountApp();
    await settle();

    expect(requestedSkins()).toEqual(['desktop-v2']);
    completeLoad('desktop-v2');
    await settle();
    expect(mountedSkin()).toBe('desktop-v2');
    // Still exactly one module requested after the commit.
    expect(requestedSkins()).toEqual(['desktop-v2']);

    unmount(instance);
  });

  it('keeps App.svelte free of static presentation entrypoint imports', () => {
    const source = readFileSync('src/App.svelte', 'utf8');
    // A static import of a layout or skin entrypoint defeats code splitting
    // no matter what the loader does at runtime.
    expect(source).not.toMatch(/import\s+\w+\s+from\s+['"][^'"]*components-v2\/layout\/[^'"]*\.svelte['"]/);
    expect(source).not.toMatch(/import\s+\w+\s+from\s+['"][^'"]*skins\/[^'"]*\.svelte['"]/);
  });

  // MOR-1257 — this suite mocks `resolveSkinId` entirely (`h.resolveSkinId`
  // above only reads `isMobile`), so it cannot observe what `layoutPreference`
  // App.svelte actually passes through. Source-pinned instead, the same
  // technique as the static-import check above. Kills: reverting to
  // `layoutPreference: getLayoutMode()` (dropping the QA override), or
  // flipping the `??` precedence so the stored preference would win over an
  // explicit query param.
  it('wires the QA-only cockpit override ahead of the stored layout preference (MOR-1257)', () => {
    const source = readFileSync('src/App.svelte', 'utf8');
    expect(source).toMatch(
      /import\s*\{\s*readQaCockpitLayoutOverride\s*\}\s*from\s*['"]\.\/lib\/stores\/qa-cockpit-override['"]\s*;/,
    );
    expect(source).toMatch(/layoutPreference:\s*qaCockpitLayoutOverride\s*\?\?\s*getLayoutMode\(\)/);
  });
});

describe('stale-resolution cancellation', () => {
  // MUTATION KILLED: dropping the generation check before commit. Without it
  // the late mobile resolution mounts on top of (or instead of) the desktop
  // presentation the newest request selected — a stale mount, and with the
  // naive form a second live instance.
  it('discards a superseded loader completion without mounting it', async () => {
    const instance = mountApp();
    await settle();
    completeLoad('desktop-v2');
    await settle();
    expect(mountedSkin()).toBe('desktop-v2');

    selectSkin('mobile');                 // request 2 — in flight
    await settle();
    // The committed presentation stays mounted while the next one resolves.
    expect(mountedSkin()).toBe('desktop-v2');
    expect(mountedCount()).toBe(1);

    selectSkin('desktop-v2');             // request 3 — supersedes request 2
    await settle();

    completeLoad('mobile');               // request 2 lands late
    await settle();

    expect(mountedSkin()).toBe('desktop-v2');
    expect(mountedCount()).toBe(1);
    // The stale completion touched no resources at all.
    expect(h.leaseEvents.filter((e) => e.consumer === 'App:mobile')).toEqual([]);

    unmount(instance);
  });

  // MUTATION KILLED: committing on any resolution instead of the newest one.
  // Here the newest request (desktop) resolves FIRST and the superseded one
  // (mobile) resolves after — order of arrival must not decide the winner.
  it('mounts the latest request even when an older one resolves after it', async () => {
    const instance = mountApp();
    await settle();
    completeLoad('desktop-v2');
    await settle();

    selectSkin('mobile');                 // request 2
    await settle();
    selectSkin('desktop-v2');             // request 3 wins
    await settle();

    completeLoad('desktop-v2');
    await settle();
    expect(mountedSkin()).toBe('desktop-v2');

    completeLoad('mobile');
    await settle();
    expect(mountedSkin()).toBe('desktop-v2');
    expect(mountedCount()).toBe(1);

    unmount(instance);
  });

  // MUTATION KILLED: gating the commit on the SKIN ID rather than on the
  // loader generation (`if (id !== skinId) return`). Three requests are in
  // flight as [A, B, A]; the first and the last select the same skin, so an
  // id comparison cannot tell them apart and the long-superseded first
  // resolution commits. Only the generation distinguishes them — which is
  // why the gate must compare generations, not ids.
  it('discards a stale resolution whose skin id equals the newest request', async () => {
    h.demand.set('hardware-scope', 1);   // so a real commit would bridge
    const instance = mountApp();          // request 1: desktop-v2
    await settle();
    selectSkin('mobile');                 // request 2: mobile
    await settle();
    selectSkin('desktop-v2');             // request 3: desktop-v2 — same id as #1
    await settle();
    expect(requestedSkins()).toEqual(['desktop-v2', 'mobile', 'desktop-v2']);
    expect(h.pending).toHaveLength(3);

    // Resolve request 1, tagged so a commit would be visible in the DOM.
    completeLoad('desktop-v2', 'desktop-v2#stale');
    await settle();

    expect(mountedCount()).toBe(0);
    expect(mountedSkin()).toBeNull();
    // A discarded resolution touches no resources either.
    expect(h.leaseEvents).toEqual([]);

    completeLoad('desktop-v2');           // request 3 — the current one
    await settle();

    expect(mountedSkin()).toBe('desktop-v2');
    expect(mountedCount()).toBe(1);

    unmount(instance);
  });

  // MUTATION KILLED: not taking a fresh generation for a request that
  // returns to the currently committed skin (A -> B -> A). If A's request
  // reused the committed generation, B's in-flight completion would still
  // look current and would mount.
  it('takes a fresh loader generation for the A -> B -> A return leg', async () => {
    const instance = mountApp();
    await settle();
    completeLoad('desktop-v2');
    await settle();

    selectSkin('mobile');
    await settle();
    selectSkin('desktop-v2');
    await settle();

    expect(requestedSkins()).toEqual(['desktop-v2', 'mobile', 'desktop-v2']);

    completeLoad('mobile');               // stale — must not mount
    completeLoad('desktop-v2');           // current
    await settle();

    expect(mountedSkin()).toBe('desktop-v2');
    expect(mountedCount()).toBe(1);

    unmount(instance);
  });
});

describe('resource-demand continuity across a swap', () => {
  // MUTATION KILLED: releasing the bridge before the commit, or acquiring it
  // after the commit. Either ordering lets demand pass through zero while the
  // outgoing subtree is destroyed, which stops and restarts a live stream —
  // exactly the reconnect MOR-973 forbids.
  it('acquires the incoming demand before the commit and releases it after', async () => {
    h.demand.set('hardware-scope', 1);    // a live scope, owned by the desktop subtree
    const instance = mountApp();
    await settle();
    completeLoad('desktop-v2');
    await settle();
    h.leaseEvents.length = 0;

    selectSkin('mobile');
    await settle();
    completeLoad('mobile');
    await settle();

    const bridge = h.leaseEvents.filter((event) => event.consumer === 'App:mobile');
    expect(bridge.map((event) => event.op)).toEqual(['acquire', 'release']);
    expect(bridge[0].resource).toBe('hardware-scope');
    // The acquire happened while the OLD presentation was still on screen and
    // the release only after the NEW one had been committed and flushed.
    expect(bridge[0].mounted).toBe('desktop-v2');
    expect(bridge[1].mounted).toBe('mobile');

    unmount(instance);
  });

  // MUTATION KILLED: bridging every planned resource unconditionally. That
  // would START the audio FFT for a presentation nobody has asked it from —
  // a presentation choice manufacturing a live service (v3 ADR invariant 12).
  it('bridges only resources that are already demanded', async () => {
    // A live hardware scope, and an audio FFT nobody has asked for. The
    // incoming desktop presentation plans BOTH.
    h.demand.set('hardware-scope', 1);
    h.demand.set('audio-fft', 0);
    const instance = mountApp();
    await settle();
    completeLoad('desktop-v2');
    await settle();
    selectSkin('mobile');
    await settle();
    completeLoad('mobile');
    await settle();
    h.leaseEvents.length = 0;

    selectSkin('desktop-v2');
    await settle();
    completeLoad('desktop-v2');
    await settle();

    expect(h.plan).toHaveBeenCalledWith('desktop-v2');
    expect(h.leaseEvents.map((event) => event.resource)).toEqual(['hardware-scope', 'hardware-scope']);
    expect(h.demand.get('audio-fft')).toBe(0);

    unmount(instance);
  });

  // MUTATION KILLED: caching/reusing one lease object across switches. A
  // reused handle leaks a binding (the second release cancels the first
  // acquisition's lease, leaving the newer one held forever).
  it('issues a distinct lease per acquisition and releases each exactly once', async () => {
    h.demand.set('hardware-scope', 1);
    const instance = mountApp();
    await settle();
    completeLoad('desktop-v2');
    await settle();

    for (const id of ['mobile', 'desktop-v2', 'mobile'] as const) {
      selectSkin(id);
      await settle();
      completeLoad(id);
      await settle();
    }

    expect(h.issuedLeases.length).toBeGreaterThan(0);
    expect(new Set(h.issuedLeases).size).toBe(h.issuedLeases.length);
    // stop-count == start-count: nothing outstanding, nothing double-released.
    expect(h.releasedLeases).toEqual(h.issuedLeases);
    expect(new Set(h.releasedLeases).size).toBe(h.releasedLeases.length);
    expect(h.demand.get('hardware-scope')).toBe(1);

    unmount(instance);
  });

  // MUTATION KILLED: letting an acquire on a torn-down resource session throw
  // out of the commit path — the presentation would never mount.
  it('commits the presentation even when the resource session is torn down', async () => {
    h.demand.set('hardware-scope', 1);
    const instance = mountApp();
    await settle();
    completeLoad('desktop-v2');
    await settle();

    h.resourcesEnded = true;
    selectSkin('mobile');
    await settle();
    completeLoad('mobile');
    await settle();

    expect(mountedSkin()).toBe('mobile');
    expect(mountedCount()).toBe(1);

    unmount(instance);
  });
});

describe('identity preserved across a presentation switch', () => {
  // MUTATION KILLED: driving the switch through anything that re-runs the
  // App script — re-bootstrapping the control transport, re-providing the TX
  // controller, or remounting the App-global host (MOR-1059 swap survival).
  it('never replays bootstrap, TX authority or the App-global host', async () => {
    const instance = mountApp();
    await settle();
    completeLoad('desktop-v2');
    await settle();

    const hostBefore = globalHost();
    expect(hostBefore).not.toBeNull();
    expect(h.bootstrap).toHaveBeenCalledTimes(1);
    expect(h.provide).toHaveBeenCalledTimes(1);

    for (const id of ['mobile', 'desktop-v2'] as const) {
      selectSkin(id);
      await settle();
      completeLoad(id);
      await settle();
    }

    expect(mountedSkin()).toBe('desktop-v2');
    expect(h.bootstrap).toHaveBeenCalledTimes(1);
    expect(h.provide).toHaveBeenCalledTimes(1);
    expect(h.bootstrapCleanup).not.toHaveBeenCalled();
    expect(h.txHost!.dispose).not.toHaveBeenCalled();
    // Same DOM node — the host was never destroyed and recreated.
    expect(globalHost()).toBe(hostBefore);
    expect(document.querySelectorAll('.spectrum-panel-stub')).toHaveLength(2);

    unmount(instance);
  });

  // MUTATION KILLED: bootstrapping or tearing down per presentation instead
  // of per App instance.
  it('bootstraps and tears down exactly once per App instance', async () => {
    const first = mountApp();
    await settle();
    completeLoad('desktop-v2');
    await settle();
    selectSkin('mobile');
    await settle();
    completeLoad('mobile');
    await settle();

    expect(h.bootstrap).toHaveBeenCalledTimes(1);
    unmount(first);
    expect(h.bootstrapCleanup).toHaveBeenCalledTimes(1);

    // The viewport is still narrow, so the fresh instance resolves to mobile
    // and loads it from scratch — no presentation state survived the remount.
    const second = mountApp();
    await settle();
    expect(h.bootstrap).toHaveBeenCalledTimes(2);
    completeLoad('mobile');
    await settle();
    expect(mountedSkin()).toBe('mobile');
    expect(mountedCount()).toBe(1);

    unmount(second);
    expect(h.bootstrapCleanup).toHaveBeenCalledTimes(2);
  });
});

describe('presentation loader failure', () => {
  // MUTATION KILLED: clearing the committed presentation (or tearing the
  // runtime down) when a switch fails to load. The operator would lose the
  // working screen because an unrelated chunk 404'd.
  it('keeps the last-known-good presentation when a switch fails', async () => {
    const instance = mountApp();
    await settle();
    completeLoad('desktop-v2');
    await settle();

    selectSkin('mobile');
    await settle();
    failLoad('mobile');
    await settle();

    expect(mountedSkin()).toBe('desktop-v2');
    expect(mountedCount()).toBe(1);
    expect(errorSurface()).toBeNull();
    // Nothing above the presentation boundary was disturbed.
    expect(h.bootstrapCleanup).not.toHaveBeenCalled();
    expect(globalHost()).not.toBeNull();

    unmount(instance);
  });

  // MUTATION KILLED: an initial failure that renders nothing at all, or one
  // that arms the bootstrap retry/reload path — the surface must be inert.
  it('shows an inert App-owned surface when the initial load fails', async () => {
    const setTimeoutSpy = vi.spyOn(globalThis, 'setTimeout');
    const instance = mountApp();
    await settle();
    setTimeoutSpy.mockClear();

    failLoad('desktop-v2');
    await settle();

    expect(errorSurface()).not.toBeNull();
    expect(mountedCount()).toBe(0);
    expect(setTimeoutSpy).not.toHaveBeenCalled();
    expect(h.bootstrapCleanup).not.toHaveBeenCalled();
    // The App-global surfaces stay up: the radio is still connected.
    expect(globalHost()).not.toBeNull();

    setTimeoutSpy.mockRestore();
    unmount(instance);
  });

  // MUTATION KILLED: dropping the `presentationActive` guard in the catch. A
  // rejection arriving after unmount would still run the whole handler —
  // logging and writing component state for an App that no longer exists.
  it('makes a rejection that lands after teardown inert', async () => {
    const instance = mountApp();
    await settle();
    unmount(instance);
    consoleError.mockClear();

    expect(() => failLoad('desktop-v2')).not.toThrow();
    await settle();

    expect(consoleError).not.toHaveBeenCalled();
    expect(errorSurface()).toBeNull();
    expect(mountedCount()).toBe(0);
    expect(document.body.textContent).not.toContain('core.app.presentationError');
  });

  // MUTATION KILLED: letting a superseded rejection claim the error surface
  // and blank a presentation that loaded fine.
  it('makes a superseded rejection inert', async () => {
    const instance = mountApp();
    await settle();

    selectSkin('mobile');                 // supersedes the initial request
    await settle();
    consoleError.mockClear();
    failLoad('desktop-v2');               // stale rejection
    await settle();

    expect(consoleError).not.toHaveBeenCalled();
    expect(errorSurface()).toBeNull();
    completeLoad('mobile');
    await settle();
    expect(mountedSkin()).toBe('mobile');

    unmount(instance);
  });

  // PINS: an initial failure is recoverable — a later successful switch
  // replaces the error surface with a live presentation.
  //
  // No mutation claim here, honestly. The surface is gated behind "nothing
  // committed" (`{:else if presentationFailed}` sits after `{:else if
  // Presentation}`), so mutating the `presentationFailed = false` write on
  // the commit path — dropping it, or latching the flag true — is an
  // EQUIVALENT MUTANT: the template branch already hides the surface the
  // moment a presentation exists. The assignment stays as defence in depth
  // against a future template that renders the two independently; this test
  // pins the observable recovery, not that one statement.
  it('clears the initial error surface once a presentation finally commits', async () => {
    const instance = mountApp();
    await settle();
    failLoad('desktop-v2');
    await settle();
    expect(errorSurface()).not.toBeNull();

    selectSkin('mobile');
    await settle();
    completeLoad('mobile');
    await settle();

    expect(errorSurface()).toBeNull();
    expect(mountedSkin()).toBe('mobile');

    unmount(instance);
  });
});
