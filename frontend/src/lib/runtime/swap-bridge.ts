import { presentationResources } from './frontend-runtime';
import { scopeController } from './scope-controller.svelte';
import type { AppResource, ResourceLease } from './resource-demand';

/**
 * Bridges a subtree replacement — a presentation switch (App) or the phone
 * layout's orientation swap — so the outgoing subtree's release cannot bounce
 * what the incoming one is about to ask for again (MOR-1060, MOR-4534).
 *
 * Leases cover only the planned resources already demanded: a replacement
 * must not manufacture a live service (v3 ADR invariant 12). The scope
 * surfaces' hold keeps the outgoing requests on the wire until the incoming
 * panels send theirs, so the server never sees zero scope subscribers. Call
 * the returned release once the incoming subtree has mounted.
 */
export function acquireSwapBridge(consumer: string, resources: readonly AppResource[]): () => void {
  const leases: ResourceLease[] = [];
  for (const resource of resources) {
    try {
      if (presentationResources.snapshot(resource).demand <= 0) continue;
      // Every acquisition returns its own distinct lease object, so a
      // release can only ever cancel the binding it created.
      leases.push(presentationResources.acquire(resource, consumer));
    } catch {
      // The App resource session is torn down — there is nothing to bridge.
    }
  }
  const releaseSurfaces = scopeController.holdSurfaces();
  return () => {
    releaseSurfaces();
    while (leases.length > 0) presentationResources.release(leases.pop()!);
  };
}
