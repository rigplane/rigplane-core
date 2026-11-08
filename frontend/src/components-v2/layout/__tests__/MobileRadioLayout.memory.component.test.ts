/**
 * MOR-4299 (beta report, item 3) — the phone layout exposes the EXISTING
 * memory surface.
 *
 * The desktop hosts the semantic Memory instrument (`instruments.memory` →
 * MemoryListHost + MemoryListController + `getMemoryHandlers().fetchPage`)
 * UNCONDITIONALLY — `memory_read` gates only the radio's own stored-channel
 * LIST inside the surface (`memoryReadable` in SemanticRadioSurfaces),
 * while the memory channels (store/recall/clear) work on every radio. The
 * fix mounts the same instrument behind a MEMORY chip on every radio
 * (explicit `allowBare`, the MOR-2895 SCOPE-tab precedent: the mobile
 * manifest declares no `memory` zone). These tests pin, through the REAL
 * MobileRadioLayout:
 *   1. a radio WITHOUT `memory_read` (the reporter's IC-7300 shape) still
 *      shows the MEMORY chip; the Memory button opens the shared dialog
 *      with the channel surface, and no `/api/v1/memories` fetch happens;
 *   2. a radio WITH `memory_read` additionally loads the first page through
 *      the REAL handler seam (panel-commands `makeMemoryHandlers().fetchPage`
 *      → `runtime.fetchMemoryPage` → `http-client`), replaying the golden
 *      page `tests/fixtures/ic705-memory-page.json`. The Python half of
 *      that seam is pinned against the same golden page by
 *      `tests/test_memory_list_api.py::test_memories_endpoint_serves_a_page_with_the_profile_bounds`.
 *
 * Harness doctrine: the radio/connection/capabilities stores, the runtime
 * singleton, the panel projections and the command handlers are all REAL —
 * driven by the producer-built fixtures (FTX1_CAPABILITIES,
 * `persistMemoryChannels`) instead of shaped fakes. Remaining doubles are
 * presentation stubs (panels/displays) or typed against their producer
 * modules (`satisfies` on the real export types), so a wrong field fails
 * type checking. The mount carries the App-resolved surface plan for the
 * mobile manifest (mountMobileWithAppPlan) so `zoned()` answers exactly as
 * in production.
 */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { mount, unmount, flushSync } from 'svelte';
import type { ManagedAppTxController } from '$lib/runtime/tx-controller/managed-app-host';
import { ManagedAppTxHarness } from '$lib/runtime/tx-controller/__tests__/support/managed-app-tx-harness';

// -- Child component stubs (presentation doubles only) --
// Mock specifiers resolve relative to THIS file, so panels/controls the
// layout imports one level up need two levels from __tests__ — the
// sibling suite's one-level panel mocks resolve nowhere and mock nothing
// (empirically: the real AgcPanel mounts when a sheet opens).
vi.mock('../../../components/spectrum/SpectrumPanel.svelte', async () => {
  const s = await import('./SpectrumPanelStub.svelte');
  return { default: s.default };
});
vi.mock('../../display/FrequencyDisplay.svelte', async () => {
  const s = await import('./fixtures/FrequencyDisplayEcho.svelte');
  return { default: s.default };
});
vi.mock('../../meters/LinearSMeter.svelte', async () => {
  const s = await import('./fixtures/LinearSMeterEcho.svelte');
  return { default: s.default };
});
vi.mock('../../controls/BandSelector.svelte', () => ({ default: function S() { return {}; } }));
vi.mock('../../panels/FilterPanel.svelte', () => ({ default: function S() { return {}; } }));
vi.mock('../../panels/RxAudioPanel.svelte', () => ({ default: function S() { return {}; } }));
// TxPanel stays stubbed: it is a lease source in its own right (MOR-1011) and
// mounting the real one inside the TX-settings sheet would put a second
// recognizer on the same controller, which is not what these tests are about.
vi.mock('../../panels/TxPanel.svelte', () => ({ default: function S() { return {}; } }));
vi.mock('../../panels/DspPanel.svelte', () => ({ default: function S() { return {}; } }));
vi.mock('../../panels/AgcPanel.svelte', () => ({ default: function S() { return {}; } }));
vi.mock('../../panels/RfFrontEnd.svelte', () => ({ default: function S() { return {}; } }));
vi.mock('../../panels/RitXitPanel.svelte', () => ({ default: function S() { return {}; } }));
vi.mock('../../panels/AntennaPanel.svelte', () => ({ default: function S() { return {}; } }));
vi.mock('../../panels/ScanPanel.svelte', () => ({ default: function S() { return {}; } }));
vi.mock('../../panels/CwPanel.svelte', () => ({ default: function S() { return {}; } }));
vi.mock('../../panels/DockMeterPanel.svelte', () => ({ default: function S() { return {}; } }));
vi.mock('../../panels/EssentialsPanel.svelte', () => ({ default: function S() { return {}; } }));

// -- Typed doubles against the producer modules --
vi.mock('$lib/audio/audio-manager', () => ({
  audioManager: {
    onChange: () => () => {}, getAppliedAudioConfig: () => null,
    start: vi.fn(), stop: vi.fn(), setVolume: vi.fn(), toggleMute: vi.fn(), setOperatorNotifier: vi.fn(),
  } satisfies Partial<(typeof import('$lib/audio/audio-manager'))['audioManager']>,
}));
vi.mock('$lib/stores/audio.svelte', () => ({
  getAudioState: vi.fn(() => ({
    volume: 50, muted: false, rxEnabled: false, txEnabled: false,
    micEnabled: false, bridgeRunning: false,
    rxError: false, txError: false, txCodecFallback: false,
  })),
  getRxAudioTargetSnapshot: vi.fn(() => Object.freeze({ muted: false, rxEnabled: false })),
  subscribeRxAudioTarget: vi.fn((handler: (target: { muted: boolean; rxEnabled: boolean }) => void) => {
    handler(Object.freeze({ muted: false, rxEnabled: false }));
    return () => {};
  }),
} satisfies { [K in keyof typeof import('$lib/stores/audio.svelte')]?: (typeof import('$lib/stores/audio.svelte'))[K] }));
vi.mock('$lib/transport/ws-client', async (importOriginal) => ({
  ...await importOriginal<typeof import('$lib/transport/ws-client')>(),
  sendCommand: vi.fn<(typeof import('$lib/transport/ws-client'))['sendCommand']>(() => true),
}));
vi.mock('$lib/utils/tx-permit', async (importOriginal) => ({
  ...await importOriginal<typeof import('$lib/utils/tx-permit')>(),
  getTxPermit: vi.fn<(typeof import('$lib/utils/tx-permit'))['getTxPermit']>(() => 'allowed'),
}));

const txHost = vi.hoisted(() => ({ current: undefined as unknown as ManagedAppTxController }));
vi.mock('$lib/runtime/tx-controller/managed-app-host', () => ({
  getManagedAppTxController: () => txHost.current,
}));

import MobileRadioLayout from '../MobileRadioLayout.svelte';
import type { Capabilities } from '$lib/types/capabilities';
import type { MemoryPageDto } from '$lib/types/memory-list';
import { FTX1_CAPABILITIES } from '$lib/runtime/adapters/__tests__/fixtures/ftx1-profile';
import ic705MemoryPage from '../../../../../tests/fixtures/ic705-memory-page.json';
// The REAL stores under test: capabilities drive SemanticRadioSurfaces'
// `memoryReadable` gate.
import { setCapabilities, clearCapabilities } from '$lib/stores/capabilities.svelte';
import { setRadioPowerOn } from '$lib/stores/connection.svelte';
import { resetRadioState } from '$lib/stores/radio.svelte';
import { resetCommandLifecycle } from '$lib/stores/commands.svelte';
import { persistMemoryChannels } from '../../../semantic/memory-channels';
import { mobileLayout } from '../../../presentation/layouts/declarations';
import { readWorkspace } from '../../../presentation/workspace/contract';
import {
  resolveSurfacePlan, SURFACE_PLAN_CONTEXT_KEY,
} from '../../../presentation/workspace/resolution';

let tx: ManagedAppTxHarness;
let components: ReturnType<typeof mount>[] = [];

/** The fixture radio plus the one varied fact: its capability list. */
function capsWith(extra: string[] = []): Capabilities {
  return {
    ...FTX1_CAPABILITIES,
    capabilities: [...(FTX1_CAPABILITIES.capabilities ?? []), ...extra],
  };
}

function setViewport(landscape: boolean) {
  Object.defineProperty(window, 'innerWidth', { writable: true, configurable: true, value: landscape ? 844 : 390 });
  Object.defineProperty(window, 'innerHeight', { writable: true, configurable: true, value: landscape ? 390 : 844 });
}

/** The App mount: the phone layout with the surface plan App resolves for
 *  the active mobile manifest provided through the workspace context (the
 *  MOR-2895 seam — without it `zoned()` cannot answer as in production). */
function mountMobileWithAppPlan(): HTMLElement {
  const plan = resolveSurfacePlan(mobileLayout, readWorkspace({ version: 1 }).workspace);
  const t = document.createElement('div');
  document.body.appendChild(t);
  components.push(mount(MobileRadioLayout, {
    target: t,
    context: new Map([[SURFACE_PLAN_CONTEXT_KEY, () => plan]]),
  }));
  flushSync();
  return t;
}

function chipByLabel(host: HTMLElement, label: string): HTMLButtonElement | undefined {
  return Array.from(host.querySelectorAll<HTMLButtonElement>('.m-chip'))
    .find((chip) => chip.textContent?.trim() === label);
}

/** Flushes the memory page fetch and the controller's state publications. */
async function settleList(): Promise<void> {
  for (let turn = 0; turn < 50; turn++) { await Promise.resolve(); flushSync(); }
}

beforeEach(() => {
  components = [];
  setViewport(false);
  tx = new ManagedAppTxHarness();
  txHost.current = tx.controller;
  resetRadioState();
  resetCommandLifecycle();
  setCapabilities(capsWith());
  setRadioPowerOn(null);
  localStorage.clear();
});

afterEach(() => {
  components.forEach((c) => unmount(c));
  expect(tx.listenerCount()).toBe(0);
  vi.unstubAllGlobals();
  clearCapabilities();
  document.body.innerHTML = '';
  localStorage.clear();
});

describe('MOR-4299 item 3 — the memory surface is reachable in the phone layout', () => {
  it('a radio WITHOUT memory_read (the reporter’s IC-7300 shape) still gets the MEMORY chip; the dialog shows the channel surface and no radio list is fetched', async () => {
    // IC-7300: rigs/ic7300.toml declares no `memory_read` (the 1A 00 read
    // reply is undocumented), but the desktop still mounts the Memory
    // instrument unconditionally — so the phone must too.
    setCapabilities(capsWith());
    persistMemoryChannels(new Map([[5, { freq: 14_074_000, mode: 'USB', name: '' }]]));
    const fetchMock = vi.fn();
    vi.stubGlobal('fetch', fetchMock);
    const host = mountMobileWithAppPlan();

    const chip = chipByLabel(host, 'MEMORY');
    expect(chip, 'the MEMORY chip on a radio without memory_read (the desktop rule)').toBeDefined();
    chip!.click();
    flushSync();
    await settleList();
    // No memory_read: the radio-side list stays unfetched…
    expect(fetchMock).not.toHaveBeenCalled();

    const open = host.querySelector<HTMLButtonElement>('[data-testid="memory-open"]');
    expect(open, 'the shared Memory button of the desktop memory wiring').not.toBeNull();
    open!.click();
    flushSync();
    const dialog = host.querySelector('[role="dialog"]');
    expect(dialog, 'the shared memory dialog').not.toBeNull();
    // …while the channel surface itself works on every radio.
    expect(dialog!.querySelector('[data-testid="memory-surface"]')).not.toBeNull();
    expect(dialog!.querySelector('[data-testid="memory-channel-5-recall"]')).not.toBeNull();
    expect(dialog!.querySelector('[data-testid="radio-memories"]')).toBeNull();
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it('a memory_read radio additionally loads the first radio-memory page through the real handler seam', async () => {
    setCapabilities(capsWith(['memory_read']));
    const requests: URL[] = [];
    vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL) => {
      const url = new URL(String(input), 'http://localhost');
      expect(url.pathname).toBe('/api/v1/memories');
      requests.push(url);
      return { ok: true, status: 200, json: async () => ic705MemoryPage as MemoryPageDto } as Response;
    }));
    const host = mountMobileWithAppPlan();

    const chip = chipByLabel(host, 'MEMORY');
    expect(chip, 'the MEMORY chip on a memory_read radio').toBeDefined();
    chip!.click();
    flushSync();

    // Selecting the chip mounts the shared MemoryListHost, whose controller
    // fetches the first page via getMemoryHandlers().fetchPage →
    // runtime.fetchMemoryPage → http-client (the desktop's exact path).
    await vi.waitFor(() => expect(requests.length).toBe(1));
    expect(Object.fromEntries(requests[0].searchParams)).toEqual({ count: '25' });
    await settleList();

    const open = host.querySelector<HTMLButtonElement>('[data-testid="memory-open"]');
    expect(open, 'the shared Memory button of the desktop memory wiring').not.toBeNull();
    open!.click();
    flushSync();
    const dialog = host.querySelector('[role="dialog"]');
    expect(dialog, 'the shared memory dialog').not.toBeNull();
    expect(dialog!.querySelector('[data-testid="radio-memory-4"]')).not.toBeNull();
  });
});
