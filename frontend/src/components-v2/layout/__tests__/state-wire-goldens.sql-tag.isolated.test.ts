/**
 * The SQL tag, driven from the wire payloads the backend serves (MOR-4223).
 *
 * Python counterpart: `tests/test_squelch_sql_wire_e2e.py`. It starts the real
 * local web stack (production startup and routing, a simulated radio on the
 * far end), sets the squelch level through the real `POST /api/v1/commands`
 * path (`set_squelch`), lets the production poller read it back, and proves
 * the receiver `squelch` value and its `fieldStatus` row arriving over real
 * HTTP `GET /api/v1/state` and the WebSocket stream equal the golden payloads
 * below, for a raised level, level 0 and a read the radio never answers.
 * `scripts/gen_state_wire_goldens.py` writes those payloads
 * (`polledSquelchRaised`, `polledSquelchZero`, `polledSquelchUnanswered`, with
 * the radio-backed `polledSquelchCapabilities`) into
 * `src/lib/transport/__tests__/fixtures/state-wire-goldens.json` for every
 * Icom CI-V profile that polls the squelch level;
 * `tests/web/test_state_wire_goldens.py` keeps the file current.
 *
 * Here the very same payloads go through the real chain — ws-client, radio
 * store, `toRadioViewModel` — and into the real desktop `VfoSurface` and the
 * real `MobileRadioLayout`. Nothing in this file writes a `squelch` value or a
 * `fieldStatus` row by hand.
 *
 * Mutations that turn it red: drop `squelch` from the adapter's indicator
 * (every case loses its source), drop 'sql' from the mobile `metaFacts` or the
 * SQL annunciator in `VfoSurface` (the tag disappears on that surface), or stop
 * the generator from ingesting the polled reply (the Raised/Zero payloads read
 * unobserved).
 *
 * MOR-4206: `polledSquelchLeafStreamRaised` is the level rising from 0 as a
 * control client that asked for leaf deltas receives it: the baseline
 * `WebServer.register_control_event_queue(..., leaf_deltas=True)` returns, then
 * the `deltaVersion: 2` frame the next broadcast queues for it. It turns red
 * when `connect` stops asking for `state_delta=2` or when the client stops
 * applying `set` operations (the tag stays empty).
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

type TagCase = 'polledSquelchRaised' | 'polledSquelchZero' | 'polledSquelchUnanswered';

interface GoldenEntry {
  capabilities: Record<string, unknown>;
  polledSquelchCapabilities?: Record<string, unknown>;
  polledSquelchRaised?: Record<string, unknown>;
  polledSquelchZero?: Record<string, unknown>;
  polledSquelchUnanswered?: Record<string, unknown>;
  polledSquelchLeafStreamRaised?: [Record<string, unknown>, Record<string, unknown>];
}

const goldens = goldenFile as unknown as Record<string, GoldenEntry>;
const polledModels = Object.keys(goldens).filter((model) => goldens[model].polledSquelchRaised).sort();

/** What each polled-reply case must look like on every surface. */
const TAG: Record<TagCase, { status: 'known' | 'unknown'; raised: boolean; text: string }> = {
  polledSquelchRaised: { status: 'known', raised: true, text: 'SQL' },
  polledSquelchZero: { status: 'known', raised: false, text: '' },
  // Unread: the empty slot, never a level of 0.
  polledSquelchUnanswered: { status: 'unknown', raised: false, text: '' },
};
const cases = Object.keys(TAG) as TagCase[];
const matrix: [string, TagCase][] = polledModels.flatMap(
  (model) => cases.map((tagCase): [string, TagCase] => [model, tagCase]));

let loadedWsClient: typeof import('$lib/transport/ws-client') | null = null;
let originalWebSocket: typeof WebSocket;
let mounted: (() => void)[] = [];

/** Deliver one golden envelope exactly as the websocket does, then build the
 *  view model from the store and the capabilities the client holds. */
async function deliver(
  model: string, tagCase: TagCase, capabilitiesKey: 'capabilities' | 'polledSquelchCapabilities',
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
    type: 'state_update', data: structuredClone(entry[tagCase]!) }));

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

describe('polled squelch-level wire goldens → SQL tag', () => {
  it('covers the IC-705 and the IC-7300 at least', () => {
    expect(polledModels).toEqual(expect.arrayContaining(['IC-705', 'IC-7300']));
  });

  it.each(polledModels)('%s: the radio-backed capabilities declare the squelch fact', (model) => {
    const caps = validateCapabilities(structuredClone(goldens[model].polledSquelchCapabilities!));

    expect(caps.capabilities).toContain('squelch');
  });

  it.each(matrix)('%s %s: the view model reads the delivered level', async (model, tagCase) => {
    const { viewModel, state } = await deliver(model, tagCase, 'polledSquelchCapabilities');

    const main = viewModel.receiverIndicators!.find((entry) => entry.receiver === 'MAIN')!;
    expect(main.squelch?.reading.status).toBe(TAG[tagCase].status);
    const level = main.squelch?.reading.status === 'known' ? main.squelch.reading.value : null;
    if (TAG[tagCase].status === 'known') {
      expect(level! > 0).toBe(TAG[tagCase].raised);
    }
    // The store holds what the server sent: a level once polled, null (never
    // 0) while the radio has not answered.
    expect(state.main.squelch ?? null).toBe(TAG[tagCase].status === 'known' ? level : null);
    expect(main).not.toHaveProperty('squelchOpen');
  });

  it.each(matrix)('%s %s: desktop VfoSurface tag', async (model, tagCase) => {
    const { viewModel } = await deliver(model, tagCase, 'polledSquelchCapabilities');

    const root = await mountDesktop(viewModel);
    const sql = mainPanel(root).querySelector('[data-chip="sql"]');

    expect(sql?.textContent?.trim()).toBe(TAG[tagCase].text);
    expect(sql?.getAttribute('data-lit')).toBe(String(TAG[tagCase].raised));
    expect(root.querySelector('[data-chip="busy"], [data-indicator-fact="busy"]')).toBeNull();
    expect(root.textContent ?? '').not.toContain('BUSY');
  });

  it.each(matrix)('%s %s: mobile layout meta-row tag', async (model, tagCase) => {
    await deliver(model, tagCase, 'polledSquelchCapabilities');

    const root = await mountMobile();
    const sql = root.querySelector('.m-vfo-meta [data-indicator-fact="sql"]');

    expect(sql?.textContent?.trim()).toBe(TAG[tagCase].text);
    expect(sql?.getAttribute('data-state')).toBe(TAG[tagCase].raised ? 'on' : 'off');
    expect(root.querySelector('[data-indicator-fact="busy"]')).toBeNull();
    expect(root.textContent ?? '').not.toContain('BUSY');
  });

  it.each(polledModels)('%s: no SQL slot when the server publishes no squelch fact', async (model) => {
    // The radio-less `capabilities` golden carries no `squelch` tag; the
    // same raised-level envelope must then draw nothing on either surface.
    const entry = goldens[model];
    expect(validateCapabilities(structuredClone(entry.capabilities)).capabilities)
      .not.toContain('squelch');
    const { viewModel } = await deliver(model, 'polledSquelchRaised', 'capabilities');

    expect(viewModel.receiverIndicators!.every((rx) => rx.squelch === undefined)).toBe(true);
    expect(mainPanel(await mountDesktop(viewModel)).querySelector('[data-chip="sql"]'))
      .toBeNull();
    expect((await mountMobile()).querySelector('[data-indicator-fact="sql"]')).toBeNull();
  });
});

describe('leaf state deltas (MOR-4206) → SQL tag', () => {
  it.each(polledModels)('%s: the client asks for leaf deltas and shows the tag from one', async (model) => {
    const entry = goldens[model];
    const [baseline, leaf] = structuredClone(entry.polledSquelchLeafStreamRaised!);
    const caps = validateCapabilities(structuredClone(entry.polledSquelchCapabilities!));
    fetchCapabilities.mockResolvedValue(caps);
    const wsClient = await import('$lib/transport/ws-client');
    loadedWsClient = wsClient;
    const store = await import('$lib/stores/radio.svelte');
    const capabilities = await import('$lib/stores/capabilities.svelte');
    const { toRadioViewModel } = await import('$lib/runtime/adapters/radio-view-model-adapter');
    expect(capabilities.setCapabilities(caps)).toBe(true);

    wsClient.connect('ws://test/api/v1/ws');
    expect(new URL(instances[0].url).searchParams.get('state_delta')).toBe('2');
    instances[0].simulateOpen();
    instances[0].simulateMessage(JSON.stringify({ type: 'state_update', data: baseline }));
    const before = store.getRadioState()!;
    expect(before.main.squelch).toBe(0);
    const beforeView = toRadioViewModel(before, capabilities.getCapabilities())!;
    expect(mainPanel(await mountDesktop(beforeView)).querySelector('[data-chip="sql"]')
      ?.textContent?.trim()).toBe('');

    instances[0].simulateMessage(JSON.stringify({ type: 'state_update', data: leaf }));
    const after = store.getRadioState()!;

    expect(after.main.squelch).toBeGreaterThan(0);
    const touched = new Set((leaf.set as [string[], unknown][])
      .filter(([path]) => path[0] === 'fieldStatus')
      .map(([path]) => path[1]));
    const untouched = Object.keys(before.fieldStatus!).filter((key) => !touched.has(key));
    expect(untouched.length).toBeGreaterThan(0);
    for (const key of untouched) expect(after.fieldStatus![key]).toBe(before.fieldStatus![key]);
    const viewModel = toRadioViewModel(after, capabilities.getCapabilities())!;
    const main = viewModel.receiverIndicators!.find((rx) => rx.receiver === 'MAIN')!;
    expect(main.squelch?.reading.status).toBe('known');
    const sql = mainPanel(await mountDesktop(viewModel)).querySelectorAll('[data-chip="sql"]');
    expect(sql[sql.length - 1]?.textContent?.trim()).toBe('SQL');
  });
});
