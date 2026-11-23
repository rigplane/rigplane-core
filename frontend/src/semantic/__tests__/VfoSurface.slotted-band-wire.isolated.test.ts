/**
 * MOR-4711 — slotted A/B tray tabs from the PRODUCER wire document.
 *
 * The IC-705 `slottedAbBands` envelope in
 * `$lib/transport/__tests__/fixtures/state-wire-goldens.json` is written by
 * `scripts/gen_state_wire_goldens.py` from the web server's own compatibility
 * poller projection (`WebServer.sync_state_store_from_radio_state`): slot A
 * on 7.074 MHz (40m), slot B on 14.219 MHz (20m), active A, and an observed
 * 3000 Hz receiver filter width — the capture scenario, on the radio-backed
 * capabilities body that publishes the `filter_width` tag. Python producer
 * companion: `tests/web/test_state_wire_goldens.py` (drift, wire schema and
 * scenario pins for the same case).
 *
 * The envelope goes through the REAL ws-client → radio store →
 * `toRadioViewModel` chain and mounts the real `VfoSurface`. The capture's
 * defect: the inactive B panel repeated the ACTIVE slot's band ("40M") and
 * filter width ("BW 3000") next to its own 14.219 MHz, because both panels
 * read the receiver-scoped `receiverBands.main` / `bandwidthHz` leaves. Each
 * panel's tray must answer for the VFO it draws; the inactive BW tab stays
 * unlit because no per-slot width is observable (MOR-988 fail-closed).
 *
 * Named `*.isolated.test.ts` for the same reason as
 * `state-wire-goldens.integration.isolated.test.ts`: it mocks `http-client`
 * and needs a fresh module graph per test.
 */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { mount, flushSync } from 'svelte';
import { validateCapabilities } from '$lib/types/capabilities';
import { validateRadioViewModel, type RadioViewModel } from '../radio-view-model';
import { toRadioViewModel } from '$lib/runtime/adapters/radio-view-model-adapter';
import { MockWebSocket, instances } from '$lib/transport/__tests__/support/fake-ws-backend';
import goldenFile from '$lib/transport/__tests__/fixtures/state-wire-goldens.json';
import VfoSurface from '../VfoSurface.svelte';

const fetchCapabilities = vi.hoisted(() => vi.fn());
vi.mock('$lib/transport/http-client', async (importOriginal) => ({
  ...await importOriginal<typeof import('$lib/transport/http-client')>(), fetchCapabilities }));

const goldens = goldenFile as unknown as Record<string, {
  slottedAbBands?: Record<string, unknown>;
  slottedAbBandsCapabilities?: Record<string, unknown>;
}>;

let loadedWsClient: typeof import('$lib/transport/ws-client') | null = null;

async function loadModules() {
  const wsClient = await import('$lib/transport/ws-client');
  loadedWsClient = wsClient;
  const store = await import('$lib/stores/radio.svelte');
  const capabilities = await import('$lib/stores/capabilities.svelte');
  return { wsClient, store, capabilities };
}

describe('slotted A/B tray tabs from the producer wire document (MOR-4711)', () => {
  let originalWebSocket: typeof WebSocket;

  beforeEach(() => {
    instances.length = 0;
    originalWebSocket = globalThis.WebSocket;
    // @ts-expect-error install the mock as the global WebSocket constructor
    globalThis.WebSocket = MockWebSocket;
    vi.resetModules();
  });

  afterEach(() => {
    loadedWsClient?.disconnect();
    loadedWsClient = null;
    globalThis.WebSocket = originalWebSocket;
    while (document.body.firstChild) document.body.removeChild(document.body.firstChild);
    vi.restoreAllMocks();
    vi.resetModules();
  });

  /** Replay the producer envelope through the real client chain and mount. */
  async function mountFromWire(): Promise<{ root: HTMLElement; viewModel: RadioViewModel }> {
    const entry = goldens['IC-705'];
    const caps = validateCapabilities(structuredClone(entry.slottedAbBandsCapabilities!));
    fetchCapabilities.mockResolvedValue(caps);
    const { wsClient, store, capabilities } = await loadModules();
    expect(capabilities.setCapabilities(caps)).toBe(true);

    wsClient.connect('ws://test/api/v1/ws');
    instances[0].simulateOpen();
    instances[0].simulateMessage(JSON.stringify({
      type: 'state_update', data: structuredClone(entry.slottedAbBands!),
    }));

    const state = store.getRadioState();
    expect(state).not.toBeNull();
    const viewModel = validateRadioViewModel(
      toRadioViewModel(state, capabilities.getCapabilities())!,
    );
    const root = document.createElement('div');
    document.body.appendChild(root);
    mount(VfoSurface, { target: root, props: { viewModel, appearance: 'standard' } });
    flushSync();
    return { root, viewModel };
  }

  function trayTab(root: HTMLElement, slotId: 'A' | 'B', tab: string): HTMLElement {
    const panel = Array.from(root.querySelectorAll<HTMLElement>('[data-receiver-instrument]'))
      .find((p) => p.getAttribute('data-standard-vfo-slot') === slotId);
    expect(panel, `panel for VFO ${slotId}`).not.toBeUndefined();
    const found = panel!.querySelector(`[data-tray-tab="${tab}"]`);
    expect(found, `VFO ${slotId} tray ${tab} tab`).not.toBeNull();
    return found as HTMLElement;
  }

  it('the producer envelope yields the capture topology: two literal MAIN slots', async () => {
    const { viewModel } = await mountFromWire();
    expect(viewModel.vfos.map((vfo) => vfo.label)).toEqual(['MAIN A', 'MAIN B']);
    expect(viewModel.vfos.map((vfo) => vfo.slot.kind)).toEqual(['slotted', 'slotted']);
    expect(viewModel.vfos.map((vfo) => vfo.isActiveSlot)).toEqual([true, false]);
  });

  it('each panel\'s BAND tab names the band of ITS OWN VFO frequency', async () => {
    const { root } = await mountFromWire();
    expect(trayTab(root, 'A', 'band').textContent?.trim()).toBe('40M');
    expect(trayTab(root, 'A', 'band').getAttribute('data-lit')).toBe('true');
    // The capture's defect: B parked on 14.219 MHz read A's '40M'.
    expect(trayTab(root, 'B', 'band').textContent?.trim()).toBe('20M');
    expect(trayTab(root, 'B', 'band').getAttribute('data-lit')).toBe('true');
  });

  it('the inactive panel\'s BW tab never repeats the active VFO\'s filter width', async () => {
    const { root } = await mountFromWire();
    expect(trayTab(root, 'A', 'bw').textContent?.trim()).toBe('BW 3000');
    expect(trayTab(root, 'A', 'bw').getAttribute('data-lit')).toBe('true');
    // The receiver's `filterWidth` leaf describes the ACTIVE slot only and
    // the radio reports no per-slot width: B keeps its reserved slot unlit
    // instead of borrowing A's 3000 Hz.
    expect(trayTab(root, 'B', 'bw').textContent?.trim()).toBe('BW');
    expect(trayTab(root, 'B', 'bw').getAttribute('data-lit')).toBe('false');
  });
});
