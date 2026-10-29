/**
 * MOR-3630 — the BUSY lamp, driven from the wire payloads the backend serves.
 *
 * Python counterpart: `tests/test_dcd_busy_wire_e2e.py`. It starts the real
 * local web stack (production startup and routing, a simulated radio on the
 * far end), lets the production poller read the squelch status (CI-V 0x15
 * 0x01) as open, then closed, then never answered, and proves the receiver
 * `dcd` value and its `fieldStatus` row arriving over real HTTP
 * `GET /api/v1/state` and the WebSocket stream equal the golden payloads
 * below. `scripts/gen_state_wire_goldens.py` writes those payloads
 * (`polledDcdOpen`, `polledDcdClosed`, `polledDcdUnanswered`, with the
 * radio-backed `polledDcdCapabilities`) into
 * `src/lib/transport/__tests__/fixtures/state-wire-goldens.json` for every
 * Icom CI-V profile that polls `dcd`; `tests/web/test_state_wire_goldens.py`
 * keeps the file current.
 *
 * Here the very same payloads go through the real chain — ws-client, radio
 * store, `toRadioViewModel` — and into the real desktop `VfoSurface` and the
 * real `MobileRadioLayout`. Nothing in this file writes a `dcd` value or a
 * `fieldStatus` row by hand.
 *
 * Mutations that turn it red: drop `squelchOpen` from the adapter's
 * indicator (lit/dark/unknown all lose their source), drop 'busy' from the
 * mobile `metaFacts` or the `frontLamp('busy', ...)` call in `VfoSurface`
 * (the lamp disappears on that surface), or stop the generator from
 * ingesting the polled reply (the Open/Closed payloads read unobserved).
 *
 * Named `*.isolated.test.ts`: it mocks `http-client` and needs a fresh module
 * graph per test, so the components and the stores share one graph.
 */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { validateCapabilities } from '$lib/types/capabilities';
import { MockWebSocket, instances } from '$lib/transport/__tests__/support/fake-ws-backend';
import goldenFile from '$lib/transport/__tests__/fixtures/state-wire-goldens.json';

// -- Children that are not under test (same set the mobile component suite stubs) --
// A plain function, not a compiled stub: a mock factory's result outlives
// `vi.resetModules()`, and a compiled component would keep the first test's
// Svelte runtime.
vi.mock('../../../components/spectrum/SpectrumPanel.svelte', () => ({ default: function S() { return {}; } }));
vi.mock('../controls/BandSelector.svelte', () => ({ default: function S() { return {}; } }));
vi.mock('../panels/FilterPanel.svelte', () => ({ default: function S() { return {}; } }));
vi.mock('../panels/RxAudioPanel.svelte', () => ({ default: function S() { return {}; } }));
vi.mock('../panels/TxPanel.svelte', () => ({ default: function S() { return {}; } }));
vi.mock('../panels/DspPanel.svelte', () => ({ default: function S() { return {}; } }));
vi.mock('../panels/AgcPanel.svelte', () => ({ default: function S() { return {}; } }));
vi.mock('../panels/RfFrontEnd.svelte', () => ({ default: function S() { return {}; } }));
vi.mock('../panels/RitXitPanel.svelte', () => ({ default: function S() { return {}; } }));
vi.mock('../panels/AntennaPanel.svelte', () => ({ default: function S() { return {}; } }));
vi.mock('../panels/ScanPanel.svelte', () => ({ default: function S() { return {}; } }));
vi.mock('../panels/CwPanel.svelte', () => ({ default: function S() { return {}; } }));
vi.mock('./KeyboardHandler.svelte', () => ({ default: function S() { return {}; } }));
vi.mock('lucide-svelte', () => {
  const S = function () { return {}; };
  return {
    Settings: S, ChevronLeft: S, ChevronRight: S, ChevronsLeft: S, ChevronsRight: S,
    Mic: S, MicOff: S, Sliders: S, Radio: S,
  };
});
vi.mock('../controls/value-control', () => ({
  ValueControl: function S() { return {}; },
  rawToPercentDisplay: vi.fn((v: number) => `${Math.round(v / 255 * 100)}%`),
}));
vi.mock('./vfo-layout-tokens', () => ({
  resolveVfoLayoutProfile: vi.fn(() => 'baseline'),
  vfoLayoutStyleVars: vi.fn(() => ''),
}));
vi.mock('$lib/audio/audio-manager', () => ({
  audioManager: {
    onChange: () => () => {}, getAppliedAudioConfig: () => null,
    onTxAudioDied: () => () => {}, setOperatorNotifier: vi.fn(),
    start: vi.fn(), stop: vi.fn(), setVolume: vi.fn(), toggleMute: vi.fn(),
  },
}));
const txHost = vi.hoisted(() => ({ current: undefined as unknown }));
vi.mock('$lib/runtime/tx-controller/managed-app-host', () => ({
  getManagedAppTxController: () => txHost.current,
}));
const fetchCapabilities = vi.hoisted(() => vi.fn());
vi.mock('$lib/transport/http-client', async (importOriginal) => ({
  ...await importOriginal<typeof import('$lib/transport/http-client')>(), fetchCapabilities }));

type LampCase = 'polledDcdOpen' | 'polledDcdClosed' | 'polledDcdUnanswered';

interface GoldenEntry {
  capabilities: Record<string, unknown>;
  polledDcdCapabilities?: Record<string, unknown>;
  polledDcdOpen?: Record<string, unknown>;
  polledDcdClosed?: Record<string, unknown>;
  polledDcdUnanswered?: Record<string, unknown>;
}

const goldens = goldenFile as unknown as Record<string, GoldenEntry>;
const polledModels = Object.keys(goldens).filter((model) => goldens[model].polledDcdOpen).sort();

/** What each polled-reply case must look like on every surface. */
const LAMP: Record<LampCase, { reading: unknown; lit: string; mobile: string }> = {
  polledDcdOpen: { reading: { status: 'known', value: true }, lit: 'true', mobile: 'on' },
  polledDcdClosed: { reading: { status: 'known', value: false }, lit: 'false', mobile: 'off' },
  polledDcdUnanswered: { reading: { status: 'unknown' }, lit: 'false', mobile: 'unknown' },
};
const cases = Object.keys(LAMP) as LampCase[];
const matrix: [string, LampCase][] = polledModels.flatMap(
  (model) => cases.map((lampCase): [string, LampCase] => [model, lampCase]));

let loadedWsClient: typeof import('$lib/transport/ws-client') | null = null;
let originalWebSocket: typeof WebSocket;
let mounted: (() => void)[] = [];

/** Deliver one golden envelope exactly as the websocket does, then build the
 *  view model from the store and the capabilities the client holds. */
async function deliver(
  model: string, lampCase: LampCase, capabilitiesKey: 'capabilities' | 'polledDcdCapabilities',
) {
  const entry = goldens[model];
  const caps = validateCapabilities(structuredClone(entry[capabilitiesKey]!));
  fetchCapabilities.mockResolvedValue(caps);
  const wsClient = await import('$lib/transport/ws-client');
  loadedWsClient = wsClient;
  const store = await import('$lib/stores/radio.svelte');
  const capabilities = await import('$lib/stores/capabilities.svelte');
  const { toRadioViewModel } = await import('$lib/runtime/adapters/radio-view-model-adapter');
  expect(capabilities.setCapabilities(caps)).toBe(true);

  wsClient.connect('ws://test/api/v1/ws');
  instances[0].simulateOpen();
  instances[0].simulateMessage(JSON.stringify({
    type: 'state_update', data: structuredClone(entry[lampCase]!) }));

  const state = store.getRadioState();
  expect(state).not.toBeNull();
  const viewModel = toRadioViewModel(state, capabilities.getCapabilities());
  expect(viewModel).not.toBeNull();
  return { viewModel: viewModel!, state: state! };
}

// `svelte` itself is imported after the module reset, like the components: a
// component compiled against one runtime instance must be mounted by the same one.
async function mountComponent(
  loadComponent: () => Promise<{ default: unknown }>, props: Record<string, unknown>,
): Promise<HTMLElement> {
  const { mount, unmount, flushSync } = await import('svelte');
  const { default: component } = await loadComponent();
  const target = document.createElement('div');
  document.body.appendChild(target);
  const instance = mount(component as never, { target, props });
  mounted.push(() => unmount(instance));
  flushSync();
  return target;
}

const mountDesktop = (viewModel: unknown): Promise<HTMLElement> => mountComponent(
  () => import('../../../semantic/VfoSurface.svelte'), { viewModel, appearance: 'standard' });

async function mountMobile(): Promise<HTMLElement> {
  // The harness builds reactive state, so it joins the post-reset module graph too.
  const { ManagedAppTxHarness } = await import(
    '$lib/runtime/tx-controller/__tests__/support/managed-app-tx-harness');
  txHost.current = new ManagedAppTxHarness().controller;
  Object.defineProperty(window, 'innerWidth', { writable: true, configurable: true, value: 390 });
  Object.defineProperty(window, 'innerHeight', { writable: true, configurable: true, value: 844 });
  return mountComponent(() => import('../MobileRadioLayout.svelte'), {});
}

const mainPanel = (root: HTMLElement): HTMLElement =>
  root.querySelector<HTMLElement>('[data-receiver-instrument="MAIN"]')!;

beforeEach(() => {
  instances.length = 0;
  mounted = [];
  originalWebSocket = globalThis.WebSocket;
  // @ts-expect-error install the mock as the global WebSocket constructor
  globalThis.WebSocket = MockWebSocket;
  vi.resetModules();
});

afterEach(() => {
  for (const unmountComponent of mounted) unmountComponent();
  while (document.body.firstChild) document.body.removeChild(document.body.firstChild);
  loadedWsClient?.disconnect();
  loadedWsClient = null;
  globalThis.WebSocket = originalWebSocket;
  vi.restoreAllMocks();
  vi.resetModules();
});

describe('polled squelch-status wire goldens → BUSY lamp', () => {
  it('covers the IC-705 and the IC-7300 at least', () => {
    expect(polledModels).toEqual(expect.arrayContaining(['IC-705', 'IC-7300']));
  });

  it.each(polledModels)('%s: the radio-backed capabilities declare the squelch fact', (model) => {
    const caps = validateCapabilities(structuredClone(goldens[model].polledDcdCapabilities!));

    expect(caps.capabilities).toContain('squelch');
  });

  it.each(matrix)('%s %s: the view model reads the delivered dcd', async (model, lampCase) => {
    const { viewModel, state } = await deliver(model, lampCase, 'polledDcdCapabilities');

    const main = viewModel.receiverIndicators!.find((entry) => entry.receiver === 'MAIN')!;
    expect(main.squelchOpen?.reading).toEqual(LAMP[lampCase].reading);
    // The store holds what the server sent: a value once polled, absent
    // (never `false`) while the radio has not answered.
    expect(state.main.dcd).toBe(
      lampCase === 'polledDcdUnanswered' ? undefined : lampCase === 'polledDcdOpen');
  });

  it.each(matrix)('%s %s: desktop VfoSurface lamp', async (model, lampCase) => {
    const { viewModel } = await deliver(model, lampCase, 'polledDcdCapabilities');

    const busy = mainPanel(await mountDesktop(viewModel)).querySelector('[data-chip="busy"]');

    expect(busy?.textContent).toBe('BUSY');
    expect(busy?.getAttribute('data-lit')).toBe(LAMP[lampCase].lit);
  });

  it.each(matrix)('%s %s: mobile layout meta-row lamp', async (model, lampCase) => {
    await deliver(model, lampCase, 'polledDcdCapabilities');

    const busy = (await mountMobile())
      .querySelector('.m-vfo-meta [data-indicator-fact="busy"]');

    expect(busy?.textContent?.trim()).toBe('BUSY');
    expect(busy?.getAttribute('data-state')).toBe(LAMP[lampCase].mobile);
  });

  it.each(polledModels)('%s: no BUSY lamp when the server publishes no squelch fact', async (model) => {
    // The radio-less `capabilities` golden carries no `squelch` tag; the
    // same open-squelch envelope must then draw nothing on either surface.
    const entry = goldens[model];
    expect(validateCapabilities(structuredClone(entry.capabilities)).capabilities)
      .not.toContain('squelch');
    const { viewModel } = await deliver(model, 'polledDcdOpen', 'capabilities');

    expect(viewModel.receiverIndicators!.every((rx) => rx.squelchOpen === undefined)).toBe(true);
    expect(mainPanel(await mountDesktop(viewModel)).querySelector('[data-chip="busy"]'))
      .toBeNull();
    expect((await mountMobile()).querySelector('[data-indicator-fact="busy"]')).toBeNull();
  });
});
