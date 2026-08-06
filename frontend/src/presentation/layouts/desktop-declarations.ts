/**
 * MOR-1266 (v3-rework slice S1, MOR-1263 §3 item 1) — the `desktop-v2`
 * presentation entrypoint's v1 layout manifest (schema, validator and
 * registry: `./contract`, MOR-1066), following the MOR-1092/94/67
 * declaration idiom.
 *
 * MANIFEST FIRST, NOT DOM FIRST (MOR-1263 decomposition §3, item 1 vs item
 * 2). MOR-1266 registered these zones deliberately AHEAD of the DOM, while
 * `components-v2/layout/RadioLayout.svelte` still gated its semantic mount on
 * `skinId === 'sdr-test'` and `desktop-v2` therefore still rendered the legacy
 * `<VfoHeader>` and the sidebars' `<TxPanel>`.
 *
 * MOR-1263 STEP 2 (MOR-1313) CLOSED THAT GAP: RadioLayout no longer knows any
 * skin id — it reads the active manifest's zone declarations and suppresses
 * the legacy twin of every surface a declared zone mounts. The two zones below
 * are consequently DOM-backed now, not forward-declared
 * (`__tests__/forward-declaration-inventory.test.ts`), and this file is a
 * description of what `desktop-v2` composes rather than a promise about it.
 */
import { registerLayout, type LayoutManifest } from './contract';

/**
 * `receiver-deck` names the real DOM section (`RadioLayout.svelte`'s
 * `<section class="receiver-deck">`) that hosts the `vfo` surface; `rx-tx`
 * names the area the sidebars' legacy `<TxPanel>` used to occupy. Since
 * MOR-1313 both declarations are what actually decides the rendered tree.
 *
 * Neither is bound to a `data-zone-id` element in the single composition —
 * unlike the dual-receiver-cockpit's `rx-tx` zone, which IS
 * (`SemanticRadioSurfaces.svelte`, `strips="dual"`). That stays deliberate:
 * MOR-1069 established that a zone element exists only where an arrangement
 * must place it, and the single composition places nothing. The zone ids are
 * read here as DECLARATIONS — what this layout mounts where — which is exactly
 * what per-zone suppression consumes.
 */
const DESKTOP_V2_ZONES = [
  { id: 'receiver-deck', surfaces: ['vfo'] },
  { id: 'rx-tx', surfaces: ['rxTx'] },
  // MOR-1336 (S4): txAux becomes zone-OWNED here. Declared but deliberately not
  // `required` — a radio whose MOR-1244 evidence gate declined the group must
  // still resolve this layout, and the surface self-gates on `view.txAux`.
  { id: 'tx-aux', surfaces: ['txAux'] },
  // MOR-1341 (S5): meters becomes zone-OWNED here too, and RadioLayout.svelte
  // retires the legacy `<MetersDockPanel>` the moment this zone is declared
  // (mirroring `hideTxPanel`'s `tx-aux` precedent). Not `required` — a radio
  // reporting no meter fields at all must still resolve this layout, and the
  // surface self-gates on `view.meters`.
  { id: 'meters', surfaces: ['meters'] },
] as const;

export const desktopV2Layout: LayoutManifest = {
  schemaVersion: 1,
  id: 'desktop-v2',
  displayName: 'Desktop',
  loader: () => import('../../skins/desktop-v2/DesktopSkin.svelte'),
  zones: DESKTOP_V2_ZONES,
  /**
   * All four canonical classes. `VfoHeader` branches on `hasDualReceiver()`
   * (`components-v2/layout/VfoHeader.svelte`) between `DualVfoDisplay` (2
   * receivers) and a single-receiver `VfoPanel`, with `VfoOps` — the A/B
   * swap/equal controls — always mounted regardless, and TX is
   * receiver-count-agnostic. This is the flagship, already-shipped v2 skin
   * every real Icom radio uses today (`resolveSkinId`'s default `auto`
   * destination when any scope is available) — read off `VfoHeader.svelte`,
   * not assumed.
   */
  compatibleTopologies: ['1/single', '1/ab', '2/ab_shared', '2/main_sub'],
  requiredSemanticSurfaces: ['vfo', 'rxTx'],
  /**
   * Fluid, no recorded breakpoints — mirroring `sdr-test`'s declaration for
   * the SAME shared `RadioLayout.svelte` `.content-row`/`.radio-layout`
   * rules (both skins fall into the same non-`sdr-test`-scoped "else"
   * branch). The stylesheet does implement two shared reflows (`max-width:
   * 1200px`, `max-width: 1024px`) — the MOR-1261 chrome-breakpoints doctrine
   * would allow recording them declaratively while no instrument stage
   * exists — but they are not specific to `desktop-v2`: `sdr-test`'s
   * manifest already declares `[]` against the identical rules, and
   * recording a different set here for the one physical stylesheet would
   * just disagree with that sibling declaration rather than describe a real
   * difference. Left `[]`, consistent with `sdr-test`.
   */
  stageSizing: { mode: 'fluid', responsiveBreakpoints: [] },
  /**
   * No fallback: terminal by construction, same reasoning as `mobile` and
   * `lcd-cockpit`. `compatibleTopologies` above covers all four canonical
   * classes (no topology ever fails), and `fluid` sizing always fits a
   * viewport (`fitsViewport`) — so a fallback hop would be unreachable and
   * would only mask a real failure, never resolve one.
   */
  fallbackLayoutId: null,
};

registerLayout(desktopV2Layout);
