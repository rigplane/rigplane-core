import { it, expect, vi } from 'vitest';
import { mount, unmount, flushSync } from 'svelte';
import type { ReceiverState, ServerState } from '../../types/state';
import type { Capabilities } from '../../types/capabilities';
import { MockWebSocket, instances } from './support/fake-ws-backend';
import Probe from './support/ReconnectConsumerProbe.svelte';
import * as wsClient from '../ws-client';
import * as store from '../../stores/radio.svelte';
import * as capabilities from '../../stores/capabilities.svelte';
import * as connection from '../../stores/connection.svelte';
const fetchCapabilities = vi.hoisted(() => vi.fn());
vi.mock('../http-client', async (importOriginal) => ({
  ...await importOriginal<typeof import('../http-client')>(), fetchCapabilities,
}));

type ServerStateWithObservation = ServerState & {
  observationSeq?: number;
  publicStateSeq?: number;
  fieldStatus?: Record<string, unknown>;
};

function makeCapabilities(
  providerGeneration = 0,
  overrides: Partial<Capabilities> = {},
): Capabilities {
  return {
    model: 'TEST', scope: false, audio: false, tx: false, capabilities: [],
    receivers: 1, vfoScheme: 'single', freqRanges: [], modes: [], filters: [],
    audioConfig: { sampleRate: 48_000, channels: 1, codecs: [] },
    webrtc: { available: false, enabled: false }, txBands: null,
    stateContractVersion: 1, providerGeneration,
    ...overrides,
  };
}

// ─── Envelope/state fixtures (shapes copied from ws-client.isolated.test.ts) ──────────

function makeReceiver(overrides: Partial<ReceiverState> = {}): ReceiverState {
  return {
    freqHz: 14074000,
    mode: 'USB',
    filter: 1,
    dataMode: 0,
    sMeter: 0,
    att: 0,
    preamp: 0,
    nb: false,
    nr: false,
    afLevel: 128,
    rfGain: 128,
    squelch: 0,
    ...overrides,
  };
}

function makeState(
  overrides: Partial<ServerStateWithObservation> & {
    main?: Partial<ReceiverState>;
    sub?: Partial<ReceiverState>;
    connection?: Partial<ServerState['connection']>;
  } = {},
): ServerStateWithObservation {
  const { main, sub, connection, txTarget, ...topLevel } = overrides;
  const revision = topLevel.stateRevision ?? topLevel.revision ?? 1;
  return {
    revision,
    stateRevision: revision,
    freshnessRevision: topLevel.freshnessRevision ?? 1,
    healthRevision: topLevel.healthRevision ?? 1,
    observationSeq: topLevel.observationSeq ?? revision,
    publicStateSeq: topLevel.publicStateSeq,
    updatedAt: '2026-06-03T00:00:00Z',
    active: 'MAIN',
    ptt: false,
    split: false,
    dualWatch: false,
    tunerStatus: 0,
    main: makeReceiver(main),
    sub: makeReceiver({ freqHz: 7074000, ...sub }),
    connection: {
      rigConnected: true,
      radioReady: true,
      controlConnected: true,
      ...connection,
    },
    stateContractVersion: 1,
    providerGeneration: 0,
    ...topLevel,
    txTarget: txTarget ?? { status: 'unknown', reason: 'not-observed' },
  };
}

function fullEnvelope(state: ServerStateWithObservation): Record<string, unknown> {
  return {
    type: 'full',
    data: state,
    revision: state.revision,
    stateRevision: state.stateRevision,
    freshnessRevision: state.freshnessRevision,
    healthRevision: state.healthRevision,
    observationSeq: state.observationSeq,
    publicStateSeq: state.publicStateSeq,
    transportSeq: state.transportSeq,
    stateContractVersion: state.stateContractVersion,
    providerGeneration: state.providerGeneration,
  };
}

function sendStateUpdate(socket: MockWebSocket, data: Record<string, unknown>): void {
  socket.simulateMessage(JSON.stringify({ type: 'state_update', data }));
}


it('keeps the mounted consumer updating after same-generation socket reconnect', async () => {
  vi.useFakeTimers();
  vi.stubGlobal('WebSocket', MockWebSocket);
  const caps = makeCapabilities(1, { audio: true });
  fetchCapabilities.mockResolvedValue(caps);
  capabilities.setCapabilities(caps);
  const target = document.createElement('div');
  document.body.appendChild(target);
  let component: ReturnType<typeof mount> | undefined;
  try {
    wsClient.connect('ws://test/api/v1/ws');
    instances[0].simulateOpen();
    sendStateUpdate(instances[0], fullEnvelope(makeState({
      providerGeneration: 1, revision: 100, breakInDelay: 128,
    })));
    await Promise.resolve();
    component = mount(Probe, { target });
    flushSync();
    expect(target.querySelector('[data-testid="connection"]')?.textContent).toBe('connected');
    const delay = () => target.querySelector<HTMLInputElement>('[data-testid="cw-keyer-breakInDelay"] input')!;
    expect(delay().value).toBe('128');
    expect(delay().disabled).toBe(false);
    instances[0].simulateClose();
    expect(() => flushSync()).not.toThrow();
    expect(delay().disabled).toBe(true);
    await vi.advanceTimersByTimeAsync(3000);
    instances[1].simulateOpen();
    sendStateUpdate(instances[1], fullEnvelope(makeState({
      providerGeneration: 1, revision: 1, breakInDelay: 180,
      main: makeReceiver({ freqHz: 14_200_000 }),
    })));
    await Promise.resolve();
    await Promise.resolve();
    flushSync();
    expect(store.getRadioState()?.main.freqHz).toBe(14_200_000);
    expect(connection.getConnectionStatus()).toBe('connected');
    expect(target.querySelector('[data-testid="connection"]')?.textContent).toBe('connected');
    expect(target.querySelector('[data-testid="frequency"]')?.textContent).toBe('14200000');
    expect(target.querySelector('[data-testid="audio"]')).not.toBeNull();
    expect(delay().value).toBe('180');
    expect(delay().disabled).toBe(false);
  } finally {
    if (component) await unmount(component);
    wsClient.disconnect();
    target.remove();
    vi.clearAllTimers();
    vi.useRealTimers();
    vi.unstubAllGlobals();
  }
});
