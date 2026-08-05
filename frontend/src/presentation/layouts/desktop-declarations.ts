/**
 * MOR-1266 (v3-rework slice S1, MOR-1263 §3 item 1) — the `desktop-v2`
 * presentation entrypoint's v1 layout manifest (schema, validator and
 * registry: `./contract`, MOR-1066), following the MOR-1092/94/67
 * declaration idiom.
 *
 * MANIFEST FIRST, NOT DOM FIRST (MOR-1263 decomposition §3, item 1 vs item
 * 2). Every existing family's zones bind something a real DOM tree already
 * mounts: `sdr-test` renders `<SemanticRadioSurfaces />` in place of the
 * legacy VFO/TX block (`sdr-registration.test.ts`), and LCD/mobile/cockpit
 * each own their entrypoint outright. `desktop-v2` is different: it shares
 * `components-v2/layout/RadioLayout.svelte`'s "else" branch with `sdr-test`,
 * gated by `let semanticSurfaces = $derived(skinId === 'sdr-test')` — for
 * `skinId === 'desktop-v2'` that is `false`, so TODAY it still mounts the
 * legacy `<VfoHeader>` in `.receiver-deck` and the sidebars' legacy
 * `<TxPanel>` (`hideTxPanel={semanticSurfaces}` is `false` here), pinned by
 * the pre-existing `RadioLayout.isolated.test.ts` ("renders .vfo-header inside
 * .receiver-deck", default `skinId: 'desktop-v2'`). Registering this
 * manifest does not change that — `validateLayoutManifest` only requires
 * internal self-consistency (every `requiredSemanticSurfaces` entry covered
 * by some zone); it "does not require the layout to be fully semantic"
 * (MOR-1263 decomposition §3). Actually wiring `RadioLayout`'s
 * `semanticSurfaces` flag to mount these zones for `desktop-v2` for real is
 * MOR-1263 step 2, a later slice — this ticket registers the manifest half
 * only, deliberately ahead of the DOM, per the programme's "manifest first"
 * ordering argument.
 */
import { registerLayout, type LayoutManifest } from './contract';

/**
 * `receiver-deck` names the real DOM section (`RadioLayout.svelte`'s
 * `<section class="receiver-deck">`) that will host the `vfo` surface once
 * MOR-1263 step 2 lands; `rx-tx` names the future replacement for the
 * sidebars' legacy `<TxPanel>`. Both are forward-declared zone ids, not yet
 * bound to a `data-zone-id` in the DOM — unlike the dual-receiver-cockpit's
 * `rx-tx` zone, which IS bound today (`SemanticRadioSurfaces.svelte`,
 * `strips="dual"`).
 */
const DESKTOP_V2_ZONES = [
  { id: 'receiver-deck', surfaces: ['vfo'] },
  { id: 'rx-tx', surfaces: ['rxTx'] },
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
