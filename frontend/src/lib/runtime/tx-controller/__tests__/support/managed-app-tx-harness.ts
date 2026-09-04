import type { ManagedTransmitDocument, ManagedTransmitIntent } from '$lib/types/managed-transmit';
import type { ManagedAppTxController } from '../../managed-app-host';
import { projectManagedTx, type ManagedTxState } from '../../managed-state';

type Listener = (state: Readonly<ManagedTxState>) => void;
type TraceEvent = Readonly<{
  transport: 'ws' | 'http';
  operation: 'ptt_on' | 'ptt_off' | 'transmit_on' | 'force_off';
}>;

export type ManagedAppTxServerSnapshot = Readonly<{
  intent?: 'rx' | 'ptt' | 'transmit';
  observedPtt?: 'off' | 'on' | 'unknown';
  releaseRequired?: boolean;
  lastError?: string | null;
  lastActuation?: Readonly<{
    operation: 'ptt_on' | 'transmit_on' | 'force_receive';
    result: 'accepted' | 'rejected' | 'uncertain';
    attemptId: string;
  }> | null;
  remainingMs?: number | null;
  stale?: boolean;
}>;

const RX: ManagedAppTxServerSnapshot = Object.freeze({
  intent: 'rx', observedPtt: 'off', releaseRequired: false,
  lastError: null, lastActuation: null, remainingMs: null, stale: false,
});

const copyInput = (input: ManagedAppTxServerSnapshot): ManagedAppTxServerSnapshot => Object.freeze({
  intent: input.intent ?? 'rx',
  observedPtt: input.observedPtt ?? 'off',
  releaseRequired: input.releaseRequired ?? false,
  lastError: input.lastError ?? null,
  lastActuation: input.lastActuation ?? null,
  remainingMs: input.remainingMs ?? null,
  stale: input.stale ?? false,
});

const documentFrom = (input: ManagedAppTxServerSnapshot): ManagedTransmitDocument => {
  const intent: ManagedTransmitIntent = input.intent === 'ptt'
    ? { kind: 'ptt', owner: 'managed-app-tx-harness' }
    : input.intent === 'transmit' ? { kind: 'transmit' } : { kind: 'rx' };
  const document: ManagedTransmitDocument = {
    schemaVersion: 1,
    sampledAt: '1970-01-01T00:00:00.000Z',
    managedTransmit: {
      status: 'available',
      intent,
      releaseRequired: input.releaseRequired ?? false,
      lastError: input.lastError ?? null,
      lastActuation: input.lastActuation ? { ...input.lastActuation } : null,
      abortErrors: [],
      tot: {
        configuredSeconds: null,
        active: input.remainingMs !== null && input.remainingMs !== undefined,
        remainingMs: input.remainingMs ?? null,
        expiresAt: null,
      },
    },
    txObservation: { observedPtt: input.observedPtt ?? 'off' },
  };
  return Object.freeze(document);
};

/** Canonical server-projection harness for managed app TX component tests. */
export class ManagedAppTxHarness {
  readonly controller: ManagedAppTxController;
  private listeners = new Set<Listener>();
  private events: TraceEvent[] = [];
  private input = RX;
  private state: Readonly<ManagedTxState> = projectManagedTx(documentFrom(RX), false, null);

  constructor(initialServerSnapshot: ManagedAppTxServerSnapshot = RX) {
    this.reset(initialServerSnapshot);
    this.controller = Object.freeze({
      snapshot: () => this.state,
      subscribe: (listener) => this.subscribe(listener),
      pttOn: () => this.record('ws', 'ptt_on'),
      pttOff: () => this.record('ws', 'ptt_off'),
      transmitOn: () => this.record('http', 'transmit_on'),
      forceOff: () => this.record('http', 'force_off'),
    });
  }

  emitServerSnapshot(input: ManagedAppTxServerSnapshot): Readonly<ManagedTxState> {
    this.input = copyInput(input);
    this.state = projectManagedTx(
      documentFrom(this.input),
      this.input.stale ?? false,
      this.input.remainingMs ?? null,
    );
    for (const listener of this.listeners) listener(this.state);
    return this.state;
  }

  emitStale(): Readonly<ManagedTxState> {
    return this.emitServerSnapshot({ ...this.input, stale: true });
  }

  trace(): readonly TraceEvent[] {
    return Object.freeze(this.events.map((event) => Object.freeze({ ...event })));
  }

  clearTrace(): void {
    this.events = [];
  }

  listenerCount(): number {
    return this.listeners.size;
  }

  reset(initialServerSnapshot: ManagedAppTxServerSnapshot = RX): void {
    if (this.listeners.size !== 0) throw new Error('ManagedAppTxHarness reset requires zero listeners');
    this.events = [];
    this.input = copyInput(initialServerSnapshot);
    this.state = projectManagedTx(
      documentFrom(this.input),
      this.input.stale ?? false,
      this.input.remainingMs ?? null,
    );
  }

  private subscribe(listener: Listener): () => void {
    this.listeners.add(listener);
    let subscribed = true;
    return () => {
      if (!subscribed) return;
      subscribed = false;
      this.listeners.delete(listener);
    };
  }

  private record(transport: TraceEvent['transport'], operation: TraceEvent['operation']): void {
    this.events.push(Object.freeze({ transport, operation }));
  }
}
