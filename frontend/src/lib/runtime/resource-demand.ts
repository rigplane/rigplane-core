export type AppResource = 'hardware-scope' | 'audio-fft' | 'rx-audio';
export type ResourceHealth = 'inactive' | 'starting' | 'streaming' | 'failed';
declare const leaseBrand: unique symbol;
export interface ResourceLease {
  readonly resource: AppResource; readonly sessionEpoch: string; readonly leaseId: number; readonly [leaseBrand]: never;
}
export type ResourceOperation<H> =
  | { kind: 'start'; resource: AppResource; sessionEpoch: string; generation: number }
  | { kind: 'stop' | 'dispose'; resource: AppResource; sessionEpoch: string; generation: number; handle: H };
interface State<H> {
  available: boolean; selected: boolean; demand: number; generation: number;
  health: ResourceHealth; activeHandle?: H; pending?: ResourceOperation<H>;
}
/** Pure App-session demand model; callers execute the returned operations. */
export class ResourceDemand<H> {
  private readonly states = new Map<AppResource, State<H>>();
  private readonly leases = new Set<ResourceLease>();
  private operations: ResourceOperation<H>[] = []; private nextLeaseId = 0; private ended = false;
  constructor(readonly sessionEpoch: string) {}
  configure(resource: AppResource, config: { available: boolean; selected: boolean }): void {
    const state = this.state(resource), newlySelected = !state.selected && config.selected;
    Object.assign(state, config);
    if (newlySelected && state.health === 'failed') state.health = 'inactive';
    this.reconcile(resource, state);
  }
  acquire(resource: AppResource, consumer: string): ResourceLease {
    if (this.ended) throw new Error('resource demand session is torn down');
    void consumer; // Diagnostic only; never lease identity.
    const lease = Object.freeze({
      resource, sessionEpoch: this.sessionEpoch, leaseId: ++this.nextLeaseId,
    }) as ResourceLease;
    this.leases.add(lease); const state = this.state(resource);
    state.demand++;
    this.reconcile(resource, state);
    return lease;
  }
  release(lease: ResourceLease): boolean {
    if (this.ended || !this.leases.delete(lease)) return false;
    const state = this.state(lease.resource); state.demand--;
    this.reconcile(lease.resource, state);
    return true;
  }
  retry(resource: AppResource): void {
    const state = this.state(resource);
    if (state.health === 'failed') {
      state.health = 'inactive';
      this.reconcile(resource, state);
    }
  }
  takeOperations(): ResourceOperation<H>[] {
    const result = this.operations; this.operations = [];
    return result;
  }
  completeStart(op: ResourceOperation<H>, result: { handle: H } | { error: string }): boolean {
    const state = this.state(op.resource);
    if (op.kind !== 'start' || state.pending !== op || this.ended) {
      if (op.kind === 'start' && 'handle' in result)
        this.operations.push({ ...op, kind: 'dispose', handle: result.handle });
      return false;
    }
    state.pending = undefined;
    if ('error' in result) state.health = 'failed';
    else {
      state.activeHandle = result.handle;
      state.health = 'streaming';
    }
    return true;
  }
  completeStop(op: ResourceOperation<H>): boolean {
    const state = this.state(op.resource);
    if (op.kind !== 'stop' || state.pending !== op || this.ended) return false;
    state.pending = undefined; state.health = 'inactive';
    return true;
  }
  snapshot(resource: AppResource): Readonly<State<H>> { return { ...this.state(resource) }; }
  teardown(): ResourceOperation<H>[] {
    if (this.ended) return [];
    this.ended = true; this.leases.clear(); this.operations = [];
    const stops: ResourceOperation<H>[] = [];
    for (const [resource, state] of this.states) {
      state.demand = 0; state.pending = undefined; state.generation++;
      if (state.activeHandle !== undefined) {
        stops.push(this.op('stop', resource, state, state.activeHandle));
        state.activeHandle = undefined;
      }
      state.health = 'inactive';
    }
    return stops;
  }
  private state(resource: AppResource): State<H> {
    let state = this.states.get(resource);
    if (!state) {
      state = { available: false, selected: false, demand: 0, generation: 0, health: 'inactive' };
      this.states.set(resource, state);
    }
    return state;
  }
  private reconcile(resource: AppResource, state: State<H>): void {
    const wanted = !this.ended && state.demand > 0 && state.selected && state.available;
    if (!wanted) {
      if (state.pending?.kind === 'start') { state.generation++; state.pending = undefined; }
      if (state.activeHandle !== undefined) {
        const handle = state.activeHandle;
        state.activeHandle = undefined;
        const stop = this.op('stop', resource, state, handle);
        state.pending = stop;
        this.operations.push(stop);
      } else if (state.health !== 'failed') state.health = 'inactive';
      return;
    }
    if (state.health === 'failed' || state.activeHandle !== undefined || state.pending?.kind === 'start') return;
    const start = this.op('start', resource, state);
    state.pending = start; state.health = 'starting';
    this.operations.push(start);
  }
  private op(kind: 'start', resource: AppResource, state: State<H>): ResourceOperation<H>;
  private op(kind: 'stop', resource: AppResource, state: State<H>, handle: H): ResourceOperation<H>;
  private op(kind: 'start' | 'stop', resource: AppResource, state: State<H>, handle?: H): ResourceOperation<H> {
    const base = { kind, resource, sessionEpoch: this.sessionEpoch, generation: ++state.generation };
    return (kind === 'start' ? base : { ...base, handle: handle as H }) as ResourceOperation<H>;
  }
}
