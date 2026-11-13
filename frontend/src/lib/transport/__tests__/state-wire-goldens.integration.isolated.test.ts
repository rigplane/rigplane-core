import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { validateCapabilities } from '../../types/capabilities';
import type { RadioViewModel } from '../../../semantic/radio-view-model';
import { toRadioViewModel } from '../../runtime/adapters/radio-view-model-adapter';
import { MockWebSocket, instances } from './support/fake-ws-backend';
import goldenFile from './fixtures/state-wire-goldens.json';
import fixtureNarrowCapsJson from '../../runtime/adapters/__tests__/fixtures/fixture-narrow-capabilities.json';

const fetchCapabilities = vi.hoisted(() => vi.fn());
vi.mock('../http-client', async (importOriginal) => ({
  ...await importOriginal<typeof import('../http-client')>(), fetchCapabilities }));

// ─── Golden wire payloads through the REAL client chain ──────────────────────
//
// `fixtures/state-wire-goldens.json` is written by
// `scripts/gen_state_wire_goldens.py` from the Python producers: per profile,
// the capabilities body and the `full` state-update envelope the web server
// builds, for an empty store (`allUnobserved`) and for every declared field
// observed (`allObserved`). The generator's `_EXCLUDED_MODELS` (IC-7300MK2,
// X6100) contribute a capabilities entry only, which still goes through
// `validateCapabilities` in the `capabilities pass validateCapabilities` case
// below. `tests/web/test_state_wire_goldens.py` keeps the file equal to the
// producers' output. Each payload goes here through the real
// ws-client → radio store → `toRadioViewModel`, so a payload the client's
// validators reject fails this suite.

// Named `*.isolated.test.ts` for the same reason as
// `ws-client-store.integration.isolated.test.ts`: it mocks `http-client` and
// needs a fresh module graph per test.

type CaseName = 'allUnobserved' | 'allObserved';

interface GoldenEntry {
  capabilities: Record<string, unknown>;
  allUnobserved?: Record<string, unknown>;
  allObserved?: Record<string, unknown>;
}

const goldens = goldenFile as unknown as Record<string, GoldenEntry>;
const models = Object.keys(goldens).sort();
const cases: [string, CaseName][] = models.flatMap((model) =>
  (['allUnobserved', 'allObserved'] as const)
    .filter((caseName) => goldens[model][caseName] !== undefined)
    .map((caseName): [string, CaseName] => [model, caseName]));

function sendStateUpdate(socket: MockWebSocket, data: Record<string, unknown>): void {
  socket.simulateMessage(JSON.stringify({ type: 'state_update', data }));
}

function currentDisplayCount(viewModel: RadioViewModel): number {
  return viewModel.vfos
    .flatMap((vfo) => (vfo.display
      ? [vfo.display.frequencyHz, vfo.display.mode, vfo.display.filter]
      : []))
    .filter((observation) => observation.state === 'current').length;
}

let loadedWsClient: typeof import('../ws-client') | null = null;

async function loadModules() {
  const wsClient = await import('../ws-client');
  loadedWsClient = wsClient;
  const store = await import('../../stores/radio.svelte');
  const capabilities = await import('../../stores/capabilities.svelte');
  return { wsClient, store, capabilities };
}

describe('state wire goldens → real client chain', () => {
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
    vi.restoreAllMocks();
    vi.resetModules();
  });

  it('lists at least one profile', () => {
    expect(models.length).toBeGreaterThan(0);
  });

  it.each(models)('%s: capabilities pass validateCapabilities', (model) => {
    const caps = validateCapabilities(structuredClone(goldens[model].capabilities));

    expect(caps.model).toBe(model);
  });

  // MOR-3679: the profile's CW family reaches the client unchanged.
  it('FTX-1: modeFamilies survive validateCapabilities', () => {
    const caps = validateCapabilities(structuredClone(goldens['FTX-1'].capabilities));

    expect(caps.modeFamilies).toEqual({ cw: ['CW-U', 'CW-L'] });
  });

  it.each(models)('%s: every modeFamilies.cw member is a published mode', (model) => {
    const caps = validateCapabilities(structuredClone(goldens[model].capabilities));
    const cw = caps.modeFamilies?.cw ?? [];

    expect(cw.length).toBeGreaterThan(0);
    for (const mode of cw) expect(caps.modes).toContain(mode);
  });

  it.each(cases)('%s %s: the client accepts the envelope and builds a view model', async (model, caseName) => {
    const entry = goldens[model];
    const envelope = structuredClone(entry[caseName]!);
    const data = envelope.data as Record<string, unknown>;
    const caps = validateCapabilities(structuredClone(entry.capabilities));
    fetchCapabilities.mockResolvedValue(caps);
    const { wsClient, store, capabilities } = await loadModules();
    expect(capabilities.setCapabilities(caps)).toBe(true);

    wsClient.connect('ws://test/api/v1/ws');
    instances[0].simulateOpen();
    sendStateUpdate(instances[0], envelope);

    const state = store.getRadioState();
    expect(state).not.toBeNull();
    expect(state?.providerGeneration).toBe(envelope.providerGeneration);
    // The store holds what the server sent: a value for an observed leaf,
    // `null` for an unobserved one. `main.freqHz` is declared by every profile
    // that has an `allObserved` case.
    const observed = caseName === 'allObserved';
    expect(state?.main.freqHz).toBe((data.main as Record<string, unknown>).freqHz);
    expect(state?.main.freqHz === null).toBe(!observed);
    expect(store.isRadioFieldAvailable('main.freqHz')).toBe(observed);

    // The golden capabilities come from a server with no radio, so they carry
    // no capability tags beyond vfo_swap/vfo_equalize; a tag-gated fact such as
    // `nbActive` reads unknown in the view model even for `allObserved`. So only
    // the unobserved direction is asserted here: nothing may read as current.
    const viewModel = toRadioViewModel(state, capabilities.getCapabilities());
    expect(viewModel).not.toBeNull();
    expect(new Set(viewModel?.vfos.map((vfo) => vfo.receiver)).size).toBe(caps.receivers);
    if (!observed) expect(currentDisplayCount(viewModel!)).toBe(0);
  });

  // ─── MOR-4434: narrow/wide control on a NEW generated fixture ───────────
  //
  // `../../runtime/adapters/__tests__/fixtures/fixture-narrow-capabilities.json`
  // carries the single-RX fixture profile's capabilities projection with the
  // SSB/CW `narrowTable` the server publishes. The envelopes below are
  // hand-built in the committed goldens' envelope shape (the validator in
  // `stores/radio.svelte` only requires the contract fields); they assert the
  // browser seam end to end: server JSON → real ws-client → radio store →
  // `toRadioViewModel` bounds follow the narrow-selected table, and an
  // unobserved narrow reading keeps the wide table.
  function narrowEnvelope(
    seq: number, narrow: boolean | null, filterWidth: number, markNarrowMissing: boolean,
  ): Record<string, unknown> {
    const data: Record<string, unknown> = {
      stateContractVersion: 1,
      providerGeneration: 7,
      revision: seq,
      stateRevision: seq,
      freshnessRevision: 0,
      observationSeq: seq,
      updatedAt: '2026-01-01T00:00:00.000Z',
      active: 'MAIN',
      ptt: null,
      split: null,
      dualWatch: null,
      tunerStatus: null,
      main: {
        freqHz: 14_074_000,
        mode: 'USB',
        filter: null,
        dataMode: null,
        sMeter: null,
        att: null,
        preamp: null,
        nb: null,
        nr: null,
        afLevel: null,
        rfGain: null,
        squelch: null,
        filterWidth,
        narrow,
      },
      connection: { rigConnected: true, radioReady: true, controlConnected: true },
      txTarget: { status: 'unknown', reason: 'narrow-table seam' },
    };
    if (markNarrowMissing) {
      data.fieldStatus = {
        'main.narrow': {
          availability: 'missing', freshness: 'fresh', observed: false, storePath: 'main.narrow',
        },
      };
    }
    return data;
  }

  function widthBounds(viewModel: RadioViewModel | null): [unknown, unknown] {
    return [
      viewModel?.modeFilter?.filterWidthMin?.reading,
      viewModel?.modeFilter?.filterWidthMax?.reading,
    ];
  }

  it('narrow on selects the narrow table bounds; unknown narrow keeps the wide table', async () => {
    const caps = validateCapabilities(structuredClone(fixtureNarrowCapsJson));
    expect(caps.filterConfig?.SSB?.narrowTable).toEqual([1500, 1800, 2000]);
    fetchCapabilities.mockResolvedValue(caps);
    const { wsClient, store, capabilities } = await loadModules();
    expect(capabilities.setCapabilities(caps)).toBe(true);

    wsClient.connect('ws://test/api/v1/ws');
    instances[0].simulateOpen();

    sendStateUpdate(instances[0], narrowEnvelope(1, true, 1800, false));
    expect(widthBounds(toRadioViewModel(store.getRadioState(), capabilities.getCapabilities()))).toEqual([
      { status: 'known', value: 1500 },
      { status: 'known', value: 2000 },
    ]);

    sendStateUpdate(instances[0], narrowEnvelope(2, false, 2600, false));
    expect(widthBounds(toRadioViewModel(store.getRadioState(), capabilities.getCapabilities()))).toEqual([
      { status: 'known', value: 2400 },
      { status: 'known', value: 4000 },
    ]);

    sendStateUpdate(instances[0], narrowEnvelope(3, null, 2600, true));
    expect(widthBounds(toRadioViewModel(store.getRadioState(), capabilities.getCapabilities()))).toEqual([
      { status: 'known', value: 2400 },
      { status: 'known', value: 4000 },
    ]);
  });

  it('negative control: an envelope without the required `main` key is rejected', async () => {
    const [model, caseName] = cases[0];
    const entry = goldens[model];
    const envelope = structuredClone(entry[caseName]!);
    const { main: _main, ...dataWithoutMain } = envelope.data as Record<string, unknown>;
    const caps = validateCapabilities(structuredClone(entry.capabilities));
    fetchCapabilities.mockResolvedValue(caps);
    const consoleError = vi.spyOn(console, 'error').mockImplementation(() => undefined);
    const { wsClient, store, capabilities } = await loadModules();
    capabilities.setCapabilities(caps);

    wsClient.connect('ws://test/api/v1/ws');
    instances[0].simulateOpen();
    sendStateUpdate(instances[0], { ...envelope, data: dataWithoutMain });

    expect(store.getRadioState()).toBeNull();
    expect(consoleError).toHaveBeenCalledWith(
      expect.stringContaining('rejected a full state envelope'),
    );
  });
});
