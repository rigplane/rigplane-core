export type AppResource = 'hardware-scope' | 'audio-fft' | 'rx-audio';
export type ResourceHealth = 'inactive' | 'starting' | 'streaming' | 'failed';
declare const leaseBrand: unique symbol;
export interface ResourceLease {
  readonly resource: AppResource;
  readonly sessionEpoch: string;
  readonly [leaseBrand]: never;
}
export type ResourceOperation<H> =
  | { kind: 'start'; resource: AppResource; sessionEpoch: string }
  | { kind: 'stop'; resource: AppResource; sessionEpoch: string; handle: H }
  | { kind: 'dispose'; resource: AppResource; sessionEpoch: string; handle: H };
interface State<H> {
  available: boolean;
  selected: boolean;
  demand: number;
  health: ResourceHealth;
  activeHandle?: H;
  pending?: ResourceOperation<H>;
}
/** Pure foundation model. Callers execute operations; this class has no side effects. */
export class ResourceDemand<H> {
  private readonly states = new Map<AppResource, State<H>>();
  private readonly leases = new Set<ResourceLease>();
  private readonly issuedStarts = new WeakSet<object>();
  private readonly cleanupLedger: [AppResource, H][] = [];
  private readonly adoptedLedger: [AppResource, H][] = [];
  private operations: ResourceOperation<H>[] = [];
  constructor(readonly sessionEpoch: string) {}
  configure(resource: AppResource, config: { available: boolean; selected: boolean }): void {
    const state = this.state(resource);
    state.available = config.available;
    state.selected = config.selected;
    this.reconcile(resource, state);
  }
  acquire(resource: AppResource, _consumer: string): ResourceLease {
    const lease = Object.freeze({ resource, sessionEpoch: this.sessionEpoch }) as ResourceLease;
    this.leases.add(lease);
    const state = this.state(resource);
    state.demand++;
    this.reconcile(resource, state);
    return lease;
  }
  release(lease: ResourceLease): boolean {
    if (lease.sessionEpoch !== this.sessionEpoch || !this.leases.delete(lease)) return false;
    const state = this.state(lease.resource);
    state.demand--;
    this.reconcile(lease.resource, state);
    return true;
  }
  takeOperations(): ResourceOperation<H>[] {
    return this.operations.splice(0).filter((operation) => {
      if (operation.kind !== 'dispose') return true;
      const state = this.state(operation.resource);
      if (state.pending?.kind !== 'start')
        return !this.adoptedLedger.some(([r, h]) => r === operation.resource && Object.is(h, operation.handle));
      this.operations.push(operation);
      return false;
    });
  }
  completeStart(op: ResourceOperation<H>, result: { handle: H } | { error: string }): boolean {
    if (op.kind !== 'start' || op.sessionEpoch !== this.sessionEpoch || !this.issuedStarts.delete(op))
      return false;
    const state = this.state(op.resource);
    if (state.pending !== op) {
      if ('handle' in result && this.claimCleanup(op.resource, result.handle))
        this.operations.push({ ...op, kind: 'dispose', handle: result.handle });
      return false;
    }
    state.pending = undefined;
    if ('error' in result) state.health = 'failed';
    else {
      this.claimCleanup(op.resource, result.handle);
      this.adoptedLedger.push([op.resource, result.handle]);
      state.activeHandle = result.handle;
      state.health = 'streaming';
    }
    return true;
  }
  completeStop(op: ResourceOperation<H>): boolean {
    const state = this.state(op.resource);
    if (op.kind !== 'stop' || op.sessionEpoch !== this.sessionEpoch || state.pending !== op)
      return false;
    state.pending = undefined;
    state.health = 'inactive';
    return true;
  }
  snapshot(resource: AppResource): Readonly<State<H>> {
    return { ...this.state(resource) };
  }
  private state(resource: AppResource): State<H> {
    let state = this.states.get(resource);
    if (!state) {
      state = { available: false, selected: false, demand: 0, health: 'inactive' };
      this.states.set(resource, state);
    }
    return state;
  }
  private claimCleanup(resource: AppResource, handle: H): boolean {
    const claimed = this.cleanupLedger.some(([r, h]) => r === resource && Object.is(h, handle));
    if (!claimed) this.cleanupLedger.push([resource, handle]);
    return !claimed;
  }
  private reconcile(resource: AppResource, state: State<H>): void {
    const wanted = state.demand > 0 && state.selected && state.available;
    if (!wanted) {
      if (state.pending?.kind === 'start') state.pending = undefined;
      if (state.activeHandle !== undefined) {
        const stop = this.operation('stop', resource, state.activeHandle);
        state.activeHandle = undefined;
        state.pending = stop;
        this.operations.push(stop);
      } else if (state.health !== 'failed') state.health = 'inactive';
      return;
    }
    if (state.health === 'failed' || state.activeHandle !== undefined || state.pending?.kind === 'start')
      return;
    const start = this.operation('start', resource);
    state.pending = start;
    state.health = 'starting';
    this.operations.push(start);
  }
  private operation(kind: 'start' | 'stop', resource: AppResource, handle?: H): ResourceOperation<H> {
    const base = { kind, resource, sessionEpoch: this.sessionEpoch };
    const operation = (kind === 'start' ? base : { ...base, handle: handle as H }) as ResourceOperation<H>;
    if (kind === 'start') this.issuedStarts.add(operation);
    return operation;
  }
}
