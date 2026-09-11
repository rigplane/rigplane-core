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
 */
import { isMobileViewport } from './viewport-classification';

const QA_COCKPIT_QUERY_PARAM = 'layout';
export type QaLayoutOverride = 'dual-receiver-cockpit' | 'flagship-probe';
/** Exact match only: anything not in this list falls through to the normal
 *  preference, so a guessed `?layout=...` cannot reach a QA-only skin. */
const QA_QUERY_VALUES: readonly QaLayoutOverride[] = ['dual-receiver-cockpit', 'flagship-probe'];

export function readQaCockpitLayoutOverride(search?: string): QaLayoutOverride | null {
  const raw = search ?? (typeof window !== 'undefined' ? window.location.search : '');
  const value = new URLSearchParams(raw).get(QA_COCKPIT_QUERY_PARAM);
  const matched = QA_QUERY_VALUES.find((id) => id === value) ?? null;
  if (matched !== null && typeof window !== 'undefined'
    && isMobileViewport(window.innerWidth, window.innerHeight, navigator.maxTouchPoints > 0)) {
    console.warn(
      `[rigplane] ?layout=${matched} is set, but the viewport is mobile — `
      + 'the mobile skin takes precedence and that skin will not show.',
    );
  }
  return matched;
}
