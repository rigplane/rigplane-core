/**
 * Interim QA reachability for the dual-receiver cockpit (MOR-1257).
 *
 * Staple reachability arrives with the MOR-1081 workspace-owned layout
 * chain (layout selection migrates there). Until then, the ONLY way to
 * reach the cockpit skin is the exact `?layout=dual-receiver-cockpit`
 * query param — it is not a `CanonicalLayoutMode` (see
 * `lib/stores/layout.svelte.ts`), so it can never be persisted via
 * `setLayoutMode`/localStorage, and it is deliberately absent from
 * `components-v2/layout/StatusBar.svelte`'s hardcoded skin-selector
 * options, so it never appears as a normal layout choice.
 *
 * Kept in its own module — not `layout.svelte.ts` or `skins/registry.ts` —
 * so `App.svelte` importing it cannot be shadowed by the partial
 * `vi.mock('$lib/stores/layout.svelte', () => ({ getLayoutMode: ... }))` /
 * `vi.mock('../skins/registry', () => ({ resolveSkinId: ... }))` factories
 * several component-test suites already use for those two modules (e.g.
 * `src/__tests__/lazy-presentation.component.test.ts`) — a new named
 * export added to either would come back `undefined` under those mocks.
 *
 * Pure: reads only the given search string (or `window.location.search`
 * when none is given); touches no store and persists nothing. Safe to call
 * from tests.
 *
 * The param never overrides the mobile short-circuit in `resolveSkinId`
 * (`skins/registry.ts` checks `ctx.isMobile` first, unconditionally) —
 * that precedence is unchanged here. Below the same 640px minimum
 * dimension `App.svelte` uses to classify the viewport as mobile, the
 * param would otherwise be silently ignored with no signal, which reads
 * as "the param is broken" rather than "this viewport is mobile" — most
 * often on an ordinary narrow/short desktop window, not an actual phone.
 * `console.warn` makes that non-obvious no-op self-explaining.
 */
const QA_COCKPIT_QUERY_PARAM = 'layout';
const QA_COCKPIT_QUERY_VALUE = 'dual-receiver-cockpit';
const MOBILE_MIN_DIMENSION_PX = 640;

export function readQaCockpitLayoutOverride(search?: string): 'dual-receiver-cockpit' | null {
  const raw = search ?? (typeof window !== 'undefined' ? window.location.search : '');
  const matched = new URLSearchParams(raw).get(QA_COCKPIT_QUERY_PARAM) === QA_COCKPIT_QUERY_VALUE;
  if (matched && typeof window !== 'undefined'
    && Math.min(window.innerWidth, window.innerHeight) < MOBILE_MIN_DIMENSION_PX) {
    console.warn(
      '[rigplane] ?layout=dual-receiver-cockpit is set, but the viewport is under '
      + `${MOBILE_MIN_DIMENSION_PX}px — the mobile skin takes precedence and the cockpit will `
      + 'not show. Widen the window to at least 640x640 to view it.',
    );
  }
  return matched ? 'dual-receiver-cockpit' : null;
}
