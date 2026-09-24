import { afterEach, expect, it, vi } from 'vitest';
import type { ManagedTransmitDocument } from '$lib/types/managed-transmit';
import type { ManagedTxState } from '$lib/runtime/tx-controller/managed-state';
import type { ManagedTxDependencies } from '$lib/runtime/tx-controller/managed-controller';

afterEach(() => {
  vi.unstubAllEnvs();
  vi.useRealTimers();
  document.body.replaceChildren();
});

const documentSnapshot = (): ManagedTransmitDocument => ({
  schemaVersion: 1, sampledAt: '2026-09-24T00:00:00Z',
  managedTransmit: {
    status: 'available', intent: { kind: 'rx' }, releaseRequired: false,
    lastError: null, lastActuation: null, abortErrors: [],
    tot: { configuredSeconds: 180, active: false, remainingMs: null, expiresAt: null },
  },
  txObservation: { observedPtt: 'off' },
});

it('counts current, failed, and superseded GETs while preserving the store outcome', async () => {
  vi.useFakeTimers();
  vi.stubEnv('VITE_UI_DIAGNOSTICS', '1');
  vi.resetModules();
  const telemetry = await import('../ui-telemetry');
  const store = await import('$lib/stores/managed-transmit.svelte');
  let resolveFirst!: (value: ManagedTransmitDocument) => void;
  let resolveSecond!: (value: ManagedTransmitDocument) => void;
  const first = store.refreshManagedTransmit({
    snapshot: () => new Promise((resolve) => { resolveFirst = resolve; }),
  });
  const second = store.refreshManagedTransmit({
    snapshot: () => new Promise((resolve) => { resolveSecond = resolve; }),
  });
  expect(telemetry.uiTelemetrySummary()).toContain('txGetInFlight: 2; txGetMaxInFlight: 2');
  resolveFirst(documentSnapshot());
  await first;
  expect(store.managedTransmitSnapshot()).toBeNull();
  resolveSecond(documentSnapshot());
  await second;
  expect(store.managedTransmitSnapshot()?.sampledAt).toBe('2026-09-24T00:00:00Z');
  await store.refreshManagedTransmit({ snapshot: async () => { throw new Error('private-error'); } });
  expect(store.managedTransmitIsStale()).toBe(true);
  const report = telemetry.uiTelemetrySummary();
  expect(report).toContain('txGetStarted: 3 (');
  expect(report).toContain('txGetCompleted: 1 (');
  expect(report).toContain('txGetFailed: 1 (');
  expect(report).toContain('txGetDiscarded: 1 (');
  expect(report).toContain('txGetInFlight: 0; txGetMaxInFlight: 2');
  expect(report).not.toContain('private-error');
});

it('counts equal projections but still notifies listeners on every presentation tick', async () => {
  vi.useFakeTimers();
  vi.stubEnv('VITE_UI_DIAGNOSTICS', '1');
  vi.resetModules();
  const telemetry = await import('../ui-telemetry');
  const { ManagedTxController } = await import('$lib/runtime/tx-controller/managed-controller');
  let tick = () => {};
  let view: ManagedTxState = {
    phase: 'idle', intent: null, radioTx: 'off', txRisk: 'none',
    fault: null, faultDetail: null, fresh: true, releaseRequired: false,
    configuredSeconds: null, remainingMs: null, lastOperation: null,
  };
  const dependencies: ManagedTxDependencies = {
    snapshot: () => view, refresh: async () => {}, invalidate: () => {},
    sendPtt: async () => 'accepted', submit: async () => 'accepted',
    setTot: async () => {}, startAudio: async () => null,
    stopLocalAudio: () => {}, onAudioDied: () => () => {},
    onPresentationTick: (handler) => { tick = handler; return () => {}; },
  };
  const controller = new ManagedTxController(dependencies);
  const heard: ManagedTxState[] = [];
  controller.subscribe((state) => heard.push(state));
  tick(); tick();
  view = { ...view, remainingMs: 900 };
  tick();
  expect(heard).toHaveLength(3);
  expect(heard[0]).toBe(heard[1]);
  expect(heard[2].remainingMs).toBe(900);
  const report = telemetry.uiTelemetrySummary();
  expect(report).toContain('txPublicationAttempt: 3 (');
  expect(report).toContain('txPublicationEqual: 2 (');
  expect(report).toContain('txPublicationChanged: 1 (');
  controller.dispose();
});
