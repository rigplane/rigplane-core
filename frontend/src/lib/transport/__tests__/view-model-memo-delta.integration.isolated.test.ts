import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { validateCapabilities } from '../../types/capabilities';
import { MockWebSocket, instances } from './support/fake-ws-backend';
import goldenFile from './fixtures/state-wire-goldens.json';

const fetchCapabilities = vi.hoisted(() => vi.fn());
vi.mock('../http-client', async (importOriginal) => ({
  ...await importOriginal<typeof import('../http-client')>(), fetchCapabilities }));

// ─── MOR-4213: the memoized view model follows the real delta-apply path ─────
//
// `toRadioViewModel` is memoized on the identity of the state and capability
// snapshots. That is only correct if every production writer hands it a new
// object whenever data changes. This file drives the producer's own output
// (`state-wire-goldens.json`, written by `scripts/gen_state_wire_goldens.py`
// from the Python encoders) through the real WebSocket client, its leaf-delta
// applier and the real radio store, and asserts the view model changes with
// the data — and stays the same object when only delivery metadata moved.
// Isolated for the same reason as `ws-client-store.integration.isolated.test.ts`.

type Envelope = Record<string, unknown>;
interface GoldenEntry {
  capabilities?: Record<string, unknown>;
  polledSquelchCapabilities?: Record<string, unknown>;
  polledSquelchLeafStreamRaised?: [Envelope, Envelope];
}
const goldens = goldenFile as unknown as Record<string, GoldenEntry>;
const models = Object.keys(goldens).filter((model) => goldens[model].polledSquelchLeafStreamRaised).sort();

let loadedWsClient: typeof import('../ws-client') | null = null;
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
  vi.resetModules();
});

/** A later leaf delta in the producer's format (`StateUpdateEnvelope`, deltaVersion 2). */
function followUp(previous: Envelope, seq: number, set: [string[], unknown][], semantic: boolean): Envelope {
  const revision = (previous.revision as number) + (semantic ? 1 : 0);
  const revisions = semantic ? [[['revision'], revision], [['stateRevision'], revision]] : [];
  return {
    ...previous,
    revision, stateRevision: revision,
    observationSeq: (previous.observationSeq as number) + 1,
    publicStateSeq: seq, transportSeq: seq,
    set: [
      ...set, ...revisions,
      [['observationSeq'], (previous.observationSeq as number) + 1],
      [['updatedAt'], `2026-01-01T00:00:0${seq}+00:00`],
      [['publicStateSeq'], seq],
    ],
  };
}

describe('memoized view model through the real delta-apply path (MOR-4213)', () => {
  it('lists the polled profiles', () => {
    expect(models.length).toBeGreaterThan(1);
  });

  it.each(models)('%s: the view changes with each data delta and only then', async (model) => {
    const entry = goldens[model];
    const [baseline, leafOpen] = structuredClone(entry.polledSquelchLeafStreamRaised!);
    const caps = validateCapabilities(structuredClone(entry.polledSquelchCapabilities!));
    fetchCapabilities.mockResolvedValue(caps);
    const wsClient = await import('../ws-client');
    loadedWsClient = wsClient;
    const store = await import('../../stores/radio.svelte');
    const capabilities = await import('../../stores/capabilities.svelte');
    const { toRadioViewModel } = await import('../../runtime/adapters/radio-view-model-adapter');
    expect(capabilities.setCapabilities(caps)).toBe(true);
    wsClient.connect('ws://test/api/v1/ws');
    instances[0].simulateOpen();
    const deliver = (data: Envelope) => instances[0].simulateMessage(
      JSON.stringify({ type: 'state_update', data }));
    const view = () => toRadioViewModel(store.getRadioState(), capabilities.getCapabilities())!;
    const squelch = (v: ReturnType<typeof view>) =>
      v.receiverIndicators!.find((rx) => rx.receiver === 'MAIN')!.squelch?.reading;

    deliver(baseline);
    const closed = view();
    expect(squelch(closed)).toEqual({ status: 'known', value: 0 });
    expect(view()).toBe(closed);

    // The producer's own leaf delta: the squelch level rises.
    const before = store.getRadioState();
    deliver(leafOpen);
    expect(store.getRadioState()).not.toBe(before);
    const open = view();
    expect(open).not.toBe(closed);
    const raised = squelch(open);
    expect(raised?.status).toBe('known');
    expect(raised?.status === 'known' && raised.value).toBeGreaterThan(0);
    // Groups the delta did not touch keep their identity.
    expect(open.vfos).toBe(closed.vfos);

    // Delivery metadata only: a new snapshot, the same view object.
    const metadataOnly = followUp(leafOpen, 3, [[['radioHealth', 'sinceMs'], 500]], false);
    const beforeMetadata = store.getRadioState();
    deliver(metadataOnly);
    expect(store.getRadioState()).not.toBe(beforeMetadata);
    expect(view()).toBe(open);

    // And back: the level returns to 0 in a later delta of the same shape.
    const leafZero = followUp(metadataOnly, 4, [
      [['main', 'squelch'], 0],
      [['fieldStatus', 'main.squelch', 'lastObservedMonotonic'], 1001.0],
    ], true);
    deliver(leafZero);
    expect(store.getRadioState()?.main.squelch).toBe(0);
    const closedAgain = view();
    expect(closedAgain).not.toBe(open);
    expect(squelch(closedAgain)).toEqual({ status: 'known', value: 0 });
  });

  it.each(models)('%s: a capability refresh re-derives against the same state', async (model) => {
    const entry = goldens[model];
    const [baseline] = structuredClone(entry.polledSquelchLeafStreamRaised!);
    const caps = validateCapabilities(structuredClone(entry.polledSquelchCapabilities!));
    fetchCapabilities.mockResolvedValue(caps);
    const wsClient = await import('../ws-client');
    loadedWsClient = wsClient;
    const store = await import('../../stores/radio.svelte');
    const capabilities = await import('../../stores/capabilities.svelte');
    const { toRadioViewModel } = await import('../../runtime/adapters/radio-view-model-adapter');
    capabilities.setCapabilities(caps);
    wsClient.connect('ws://test/api/v1/ws');
    instances[0].simulateOpen();
    instances[0].simulateMessage(JSON.stringify({ type: 'state_update', data: baseline }));
    const first = toRadioViewModel(store.getRadioState(), capabilities.getCapabilities())!;
    expect(first.receiverIndicators!.find((rx) => rx.receiver === 'MAIN')!.squelch).toBeDefined();

    // A refresh installs a new parsed body, as `fetchCapabilities` returns one:
    // here the radio-less profile body, without the squelch tag set.
    capabilities.setCapabilities(validateCapabilities(structuredClone(entry.capabilities!)));
    const next = toRadioViewModel(store.getRadioState(), capabilities.getCapabilities())!;
    expect(next).not.toBe(first);
    expect(next.receiverIndicators!.find((rx) => rx.receiver === 'MAIN')!.squelch).toBeUndefined();
  });
});
