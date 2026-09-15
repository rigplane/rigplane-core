/**
 * MOR-2425 C-R3 — the link-fault veil's single derived state.
 *
 * The veil carries no words of its own; the status bar states both arms, and
 * each arm here implies its statement is on screen. `ws-down` implies
 * `StatusBar.svelte`'s `.control-link-lost` bar, which is up whenever
 * `controlState === 'disconnected'` — `getConnectionStatus()` off the same
 * `wsConnected`. `radio-silent` implies its bad-link chip (MOR-2425 R29(3),
 * `getWsConnected() && runtime.connectionStale`), on the same two facts.
 * The bar is also up before the first connect — `wsConnected` is false then
 * too — where this returns `none`.
 *
 * The facts come from `connection.svelte.ts` — `getWsConnected()`,
 * `hasEverConnected()`, and the staleness `isStale()` exposes as
 * `frontend-runtime.ts: get connectionStale()`.
 */
export type LinkFault = 'none' | 'ws-down' | 'radio-silent';

export interface LinkFaultInput {
  /** The control WebSocket has been up at least once this page lifetime. */
  everConnected: boolean;
  /** The control WebSocket's live transport state. */
  wsConnected: boolean;
  /** No accepted `state_update` for at least the store's stale threshold. */
  connectionStale: boolean;
}

/**
 * No fault before the first connect: `wsConnected` is false then too, so
 * without this arm the face would load veiled and stay veiled until the WS
 * opened.
 *
 * `ws-down` wins when both hold: with the transport down the staleness is a
 * consequence of it, not a second fault, and the operator needs the cause.
 */
export function deriveLinkFault(
  { everConnected, wsConnected, connectionStale }: LinkFaultInput,
): LinkFault {
  if (!everConnected) return 'none';
  if (!wsConnected) return 'ws-down';
  return connectionStale ? 'radio-silent' : 'none';
}
