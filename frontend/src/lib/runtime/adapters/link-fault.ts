/**
 * MOR-2425 C-R3 — the link-fault veil's single derived state.
 *
 * One condition, stated in two ways: `StatusBar.svelte`'s bad-link chip
 * (MOR-2425 R29(3)) reports it in the status bar, and the face veil reports
 * it on the face. Both read the same two facts —
 * `connection.svelte.ts: getWsConnected()` and the staleness `isStale()`
 * exposes as `frontend-runtime.ts: get connectionStale()`.
 */
export type LinkFault = 'none' | 'ws-down' | 'radio-silent';

export interface LinkFaultInput {
  /** The control WebSocket's live transport state. */
  wsConnected: boolean;
  /** No accepted `state_update` for at least the store's stale threshold. */
  connectionStale: boolean;
}

/**
 * `ws-down` wins when both hold: with the transport down the staleness is a
 * consequence of it, not a second fault, and the operator needs the cause.
 */
export function deriveLinkFault({ wsConnected, connectionStale }: LinkFaultInput): LinkFault {
  if (!wsConnected) return 'ws-down';
  return connectionStale ? 'radio-silent' : 'none';
}
