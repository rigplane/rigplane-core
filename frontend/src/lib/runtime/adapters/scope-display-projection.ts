import type { ScopePassbandDisplay } from './scope-passband-display';

/**
 * MOR-4270: the managed scope region carries no pixels — those flow through
 * the shared `ScopeStream` sinks. The region supplies the passband overlay
 * projection and the demand wiring; geometry facts come from the stream's
 * `geometry` store.
 */
export type ManagedScopeRegion = Readonly<{
  passband: ScopePassbandDisplay;
  demanded: boolean;
  geometryAdmitted?: boolean;
  controlSession?: Readonly<{ connected: boolean; revision: number }>;
  setDemand(enabled: boolean): void;
}>;

/** A frame gap or an incomplete readback leaves the last presentation in place. */
export function scopeDisplayDisposition({ bindingEnded, linkLost, contradicted }: {
  bindingEnded: boolean;
  linkLost: boolean;
  contradicted: boolean;
}): 'held' | 'cleared' | 'replaced' {
  if (bindingEnded || linkLost) return 'cleared';
  return contradicted ? 'replaced' : 'held';
}
