/**
 * Interim QA reachability for the dual-receiver cockpit (MOR-1257) and, on
 * the same terms, the flagship geometry probe (T160 PR-1).
 *
 * Staple reachability arrives with the MOR-1081 workspace-owned layout
 * chain (layout selection migrates there). Until then, the ONLY way to
 * reach either skin is the exact `?layout=<id>` query param naming it —
 * neither id is a `CanonicalLayoutMode` (see
 * `lib/stores/layout.svelte.ts`), so neither can be persisted via
 * `setLayoutMode`/localStorage, and `components-v2/layout/StatusBar.svelte`
 * types its hardcoded skin-selector options `CanonicalLayoutMode`, so
 * listing either one there is a compile error rather than an omission.
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
export type QaLayoutOverride = 'dual-receiver-cockpit' | 'flagship-probe';
/** Exact match only: anything not in this list falls through to the normal
 *  preference, so a guessed `?layout=...` cannot reach a QA-only skin. */
const QA_QUERY_VALUES: readonly QaLayoutOverride[] = ['dual-receiver-cockpit', 'flagship-probe'];
const MOBILE_MIN_DIMENSION_PX = 640;

export function readQaCockpitLayoutOverride(search?: string): QaLayoutOverride | null {
  const raw = search ?? (typeof window !== 'undefined' ? window.location.search : '');
  const value = new URLSearchParams(raw).get(QA_COCKPIT_QUERY_PARAM);
  const matched = QA_QUERY_VALUES.find((id) => id === value) ?? null;
  if (matched !== null && typeof window !== 'undefined'
    && Math.min(window.innerWidth, window.innerHeight) < MOBILE_MIN_DIMENSION_PX) {
    console.warn(
      `[rigplane] ?layout=${matched} is set, but the viewport is under `
      + `${MOBILE_MIN_DIMENSION_PX}px — the mobile skin takes precedence and that skin will `
      + 'not show. Widen the window to at least 640x640 to view it.',
    );
  }
  return matched;
}
