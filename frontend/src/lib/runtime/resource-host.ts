import { ResourceDemand, type AppResource, type ResourceLease, type ResourceOperation } from './resource-demand';
export interface PresentationResourceDriver<H> {
  start(): Promise<H> | H;
  stop(handle: H): Promise<void> | void;
  dispose?(handle: H): Promise<void> | void;
}
type ResourceConfig<H> = { available: boolean; selected: boolean; driver?: PresentationResourceDriver<H> };
type Binding<H> = { resource: AppResource; handle: H; driver: PresentationResourceDriver<H>; adopted: boolean };
type Listener<H> = (resource: AppResource, state: ReturnType<ResourceDemand<H>['snapshot']>) => void;
/** Executes one pure ResourceDemand model for an App session. */
export class PresentationResourceHost<H> {
  private readonly demand: ResourceDemand<H>;
  private readonly drivers = new Map<AppResource, PresentationResourceDriver<H>>();
  private readonly bindings: Binding<H>[] = [];
  private readonly listeners = new Set<Listener<H>>();
  private readonly inFlight = new Set<Promise<void>>();
  private final?: Promise<void>;
  constructor(readonly sessionEpoch: string) { this.demand = new ResourceDemand(sessionEpoch); }
  configure(resource: AppResource, config: ResourceConfig<H>): void {
    if ('driver' in config) {
      if (config.driver) this.drivers.set(resource, config.driver);
      else this.drivers.delete(resource);
    }
    this.demand.configure(resource, {
      available: config.available && this.drivers.has(resource),
      selected: config.selected,
    });
    this.refresh(resource);
  }
  acquire(resource: AppResource, consumer: string): ResourceLease {
    const lease = this.demand.acquire(resource, consumer);
    this.refresh(resource);
    return lease;
  }
  release(lease: ResourceLease): boolean {
    const released = this.demand.release(lease);
    if (released) this.refresh(lease.resource);
    return released;
  }
  retry(resource: AppResource): void { this.demand.retry(resource); this.refresh(resource); }
  snapshot(resource: AppResource) { return this.demand.snapshot(resource); }
  subscribe(listener: Listener<H>): () => void {
    this.listeners.add(listener);
    return () => { this.listeners.delete(listener); };
  }
  teardown(): Promise<void> {
    if (this.final) return this.final;
    this.listeners.clear();
    this.final = (async () => {
      this.pump(this.demand.teardown());
      while (this.inFlight.size) await Promise.all([...this.inFlight]);
    })();
    return this.final;
  }
  private refresh(resource: AppResource): void {
    this.pump();
    for (const listener of this.listeners) listener(resource, this.snapshot(resource));
  }
  private pump(operations = this.demand.takeOperations()): void {
    for (const operation of operations) {
      let task!: Promise<void>;
      task = this.execute(operation).catch(() => {}).finally(() => { this.inFlight.delete(task); });
      this.inFlight.add(task);
    }
  }
  private async execute(operation: ResourceOperation<H>): Promise<void> {
    if (operation.kind === 'start') {
      const driver = this.drivers.get(operation.resource);
      if (!driver) this.demand.completeStart(operation, { error: 'driver unavailable' });
      else try {
        const handle = await driver.start();
        const adopted = this.demand.completeStart(operation, { handle });
        this.bindings.push({ resource: operation.resource, handle, driver, adopted });
      } catch (error) {
        this.demand.completeStart(operation, {
          error: error instanceof Error ? error.message : String(error),
        });
      }
    } else {
      const adopted = operation.kind === 'stop';
      const binding = [...this.bindings].reverse().find(
        (item) => item.resource === operation.resource
          && Object.is(item.handle, operation.handle) && item.adopted === adopted,
      );
      try {
        if (binding) {
          const cleanup = operation.kind === 'dispose'
            ? binding.driver.dispose ?? binding.driver.stop : binding.driver.stop;
          await cleanup.call(binding.driver, operation.handle);
        }
      } finally {
        if (operation.kind === 'stop') this.demand.completeStop(operation);
      }
    }
    this.refresh(operation.resource);
  }
}
