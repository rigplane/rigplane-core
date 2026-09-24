/**
 * Pool: `isolated` — module-scope `vi.mock` of `ws-client` and `tx-adapter`
 * for the real browser dependencies in the presentation-tick block.
 *
 * MOR-2551: the controller notifies subscribers only when the projected
 * managed-TX state differs in some field from the one it last delivered.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import type { ManagedTransmitDocument, ManagedTransmitIntent } from '$lib/types/managed-transmit';
import {
  invalidateManagedTransmit, managedTransmitAppliedRevision, receiveManagedTransmitSnapshot,
} from '$lib/stores/managed-transmit.svelte';
import { createManagedBrowserDependencies } from '../browser-dependencies';
import { ManagedTxController } from '../managed-controller';
import { projectManagedTx, type ManagedTxState } from '../managed-state';

vi.mock('$lib/transport/ws-client', () => ({
  sendCommand: () => true,
  onCommandDelivery: () => () => {},
  onControlSessionTransition: () => () => {},
  onMessage: () => () => {},
  getControlSession: () => ({ state: 'connected', epoch: 4 }),
}));
vi.mock('$lib/runtime/adapters/tx-adapter', () => ({ getTxAudioControl: () => ({
  onTxAudioDied: () => () => {},
  startManagedTx: async () => null,
  stopLocalAudio: () => {},
}) }));

type DocumentInput = {
  intent?: ManagedTransmitIntent; observedPtt?: 'on' | 'off' | 'unknown';
  releaseRequired?: boolean; lastError?: string | null;
  lastOperation?: 'ptt_on' | 'transmit_on' | 'force_receive';
  configuredSeconds?: number | null; remainingMs?: number | null;
};
let sampled = 0;
/** A new document object per call, with a strictly later `sampledAt` so the store accepts it. */
const document = (input: DocumentInput = {}): ManagedTransmitDocument => {
  const remainingMs = input.remainingMs ?? null;
  return {
    schemaVersion: 1,
    sampledAt: new Date(Date.UTC(2026, 8, 24, 0, 0, 0, ++sampled)).toISOString(),
    managedTransmit: {
      status: 'available', intent: input.intent ?? { kind: 'rx' },
      releaseRequired: input.releaseRequired ?? false, lastError: input.lastError ?? null,
      lastActuation: input.lastOperation === undefined
        ? null
        : { operation: input.lastOperation, result: 'accepted', attemptId: 'attempt-1' },
      abortErrors: [],
      tot: {
        configuredSeconds: input.configuredSeconds === undefined ? 180 : input.configuredSeconds,
        active: remainingMs !== null, remainingMs, expiresAt: null,
      },
    },
    txObservation: { observedPtt: input.observedPtt ?? 'off' },
  };
};
const PTT_ON: DocumentInput = {
  intent: { kind: 'ptt', owner: 'operator' }, observedPtt: 'on', releaseRequired: true,
  lastOperation: 'ptt_on',
};

type Server = { document: ManagedTransmitDocument; stale: boolean; remainingMs: number | null };
const server = (input: DocumentInput = {}, stale = false, remainingMs: number | null = null): Server => ({
  document: document(input), stale, remainingMs,
});
const project = (s: Server) => projectManagedTx(s.document, s.stale, s.remainingMs);

function rig(snapshot: () => ManagedTxState, invalidate: () => void = () => {}) {
  let refreshes = 0;
  const controller = new ManagedTxController({
    snapshot, invalidate,
    refresh: async () => { refreshes += 1; },
    sendPtt: async () => 'accepted', submit: async () => 'accepted', setTot: async () => {},
    startAudio: async () => null, stopLocalAudio: () => {}, onAudioDied: () => () => {},
  });
  const seen: ManagedTxState[] = [];
  controller.subscribe((state) => { seen.push(state); });
  return { controller, seen, refreshes: () => refreshes };
}

describe('managed TX controller publication (MOR-2551)', () => {
  it('after connect, equal canonical refreshes keep every GET and notify nobody', async () => {
    let s = server({}, true);
    const r = rig(() => project(s));
    s = server();
    await r.controller.refresh();
    expect(r.seen).toHaveLength(1);
    const delivered = r.seen[0];

    for (let push = 0; push < 5; push += 1) {
      s = server();
      await r.controller.refresh();
    }

    expect(r.refreshes()).toBe(6);
    expect(r.seen).toEqual([delivered]);
    expect(r.controller.snapshot()).toBe(delivered);
  });

  const BASE: ManagedTxState = {
    phase: 'idle', intent: null, radioTx: 'off', txRisk: 'none', fault: null, faultDetail: null,
    fresh: true, releaseRequired: false, configuredSeconds: 180, remainingMs: null,
    lastOperation: null,
  };
  // Typed `ManagedTxState`, so tsc requires a row for every field.
  const CHANGED: ManagedTxState = {
    phase: 'failed', intent: 'momentary', radioTx: 'on', txRisk: 'uncertain', fault: 'rejected',
    // `faultDetail` is typed `null`; the cast lets this row check that the comparison reads it.
    faultDetail: 'detail' as unknown as null,
    fresh: false, releaseRequired: true, configuredSeconds: 240, remainingMs: 900,
    lastOperation: 'ptt_on',
  };

  it.each(Object.keys(CHANGED) as Array<keyof ManagedTxState>)(
    'a change to %s alone notifies exactly once with the new value',
    async (field) => {
      expect(CHANGED[field]).not.toBe(BASE[field]);
      let current: ManagedTxState = { ...BASE };
      const r = rig(() => ({ ...current }));
      await r.controller.refresh();
      expect(r.seen).toEqual([]);

      current = { ...BASE, [field]: CHANGED[field] };
      await r.controller.refresh();
      await r.controller.refresh();

      expect(r.seen).toHaveLength(1);
      expect(r.seen[0][field]).toBe(CHANGED[field]);
      expect(r.seen[0]).toEqual(current);
      expect(r.controller.snapshot()).toBe(r.seen[0]);
    },
  );

  it.each<[string, () => Server, () => Server]>([
    ['PTT OFF -> ON', () => server(), () => server(PTT_ON)],
    ['PTT ON -> OFF', () => server(PTT_ON), () => server({ lastOperation: 'ptt_on' })],
    ['observed PTT OFF -> UNKNOWN', () => server(), () => server({ observedPtt: 'unknown' })],
    ['observed PTT UNKNOWN -> OFF', () => server({ observedPtt: 'unknown' }), () => server()],
    ['RX -> rejected fault', () => server(), () => server({ lastError: 'rejected' })],
    ['rejected fault -> RX', () => server({ lastError: 'rejected' }), () => server()],
    ['fresh -> stale', () => server(), () => server({}, true)],
    ['stale -> fresh', () => server({}, true), () => server()],
    ['TOT limit 180 s -> 240 s', () => server(), () => server({ configuredSeconds: 240 })],
    ['TOT limit 180 s -> disabled', () => server(), () => server({ configuredSeconds: null })],
    ['countdown 1000 ms -> 750 ms', () => server(PTT_ON, false, 1000), () => server(PTT_ON, false, 750)],
  ])('%s notifies exactly once', async (_name, from, to) => {
    let s = from();
    const r = rig(() => project(s));
    const before = r.controller.snapshot();

    s = to();
    await r.controller.refresh();
    s = to();
    await r.controller.refresh();

    expect(r.seen).toHaveLength(1);
    expect(r.seen[0]).not.toEqual(before);
    expect(r.seen[0]).toEqual(project(s));
  });

  it('provider or session invalidation notifies once; a repeated one notifies nobody', () => {
    const s = server();
    const r = rig(() => project(s), () => { s.stale = true; });

    r.controller.invalidate();
    r.controller.invalidate();

    expect(r.seen).toHaveLength(1);
    expect(r.seen[0]).toMatchObject({ fresh: false, radioTx: 'unknown' });
  });
});

describe('managed TX presentation tick with the real browser ticker and store (MOR-2551)', () => {
  let now = 0;
  let stop = () => {};
  beforeEach(() => {
    vi.useFakeTimers({ toFake: ['setInterval', 'clearInterval'] });
    now = 10_000;
    vi.spyOn(performance, 'now').mockImplementation(() => now);
    invalidateManagedTransmit();
  });
  afterEach(() => {
    stop();
    stop = () => {};
    vi.useRealTimers();
    vi.restoreAllMocks();
  });

  function start() {
    const browser = createManagedBrowserDependencies();
    const controller = new ManagedTxController(browser.dependencies);
    const seen: ManagedTxState[] = [];
    controller.subscribe((state) => { seen.push(state); });
    stop = () => { controller.dispose(); browser.dispose(); };
    return { controller, seen };
  }
  const receive = (input: DocumentInput) => {
    const revision = managedTransmitAppliedRevision();
    receiveManagedTransmitSnapshot(document(input));
    expect(managedTransmitAppliedRevision()).toBe(revision + 1);
  };
  const tick = (count: number) => {
    for (let i = 0; i < count; i += 1) {
      now += 250;
      vi.advanceTimersByTime(250);
    }
  };

  it('in RX without a countdown, ticks after the first fresh publication notify nobody', () => {
    const { controller, seen } = start();
    receive({});
    tick(1);
    expect(seen).toHaveLength(1);
    expect(seen[0]).toMatchObject({ fresh: true, radioTx: 'off', remainingMs: null });

    tick(40);

    expect(seen).toHaveLength(1);
    expect(controller.snapshot()).toBe(seen[0]);
  });

  it('an active countdown publishes on every tick until it reaches zero, then stops', () => {
    const { seen } = start();
    receive({ ...PTT_ON, remainingMs: 1000 });

    tick(12);

    expect(seen.map((state) => state.remainingMs)).toEqual([750, 500, 250, 0]);
  });

  it('a countdown the server clears publishes once more, then ticks stop', () => {
    const { seen } = start();
    receive({ ...PTT_ON, remainingMs: 5000 });
    tick(2);
    receive({ lastOperation: 'ptt_on' });

    tick(20);

    expect(seen.map((state) => [state.radioTx, state.remainingMs]))
      .toEqual([['on', 4750], ['on', 4500], ['off', null]]);
  });
});
