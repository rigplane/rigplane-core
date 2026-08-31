/**
 * MOR-2035/MOR-2034 — the `accept-probe` skin's layout manifest.
 *
 * `accept-probe` is a temporary acceptance-experiment skin: it proves a
 * skin author can build its own dedicated layout, own S-meter, and own
 * VFO/RX indicator using only `presentation/` and above, following
 * `docs/architecture/building-a-skin.md` alone. It does not delegate into
 * `RadioLayout` or `LcdLayout` — `AcceptProbeSkin.svelte` renders its own
 * bespoke composition, reading state via `lib/runtime/props/panel-props.ts`
 * (see that file's `toVfoProps`/`toMeterProps`) rather than mounting the
 * semantic vertical. `zones`/`requiredSemanticSurfaces` still declare
 * `vfo`/`meters` as a topology/compatibility fact, the same way
 * `lcd-cockpit`'s manifest does despite `LcdLayout.svelte` never reading it
 * back (`lcd-declarations.ts`) — the manifest records what facts this skin
 * surfaces, independent of which mechanism renders them.
 *
 * Kept in its own sibling file, re-exported from `./declarations`, matching
 * the `lcd-declarations.ts`/`mobile-declarations.ts` convention that file's
 * own barrel comment describes.
 *
 * `meters` sits ALONE in its own zone, id `'meters'`, and is declared but
 * NEVER required — neither rule is in the guide's own prose. Both are
 * hand-reviewed shape checks in
 * `presentation/layouts/__tests__/meters-declarability.test.ts`: "%s
 * declares it under the stable `meters` id, alone in its zone" (mirroring
 * `desktop-v2`'s own declaration, the only other manifest that test's
 * hand-maintained `DECLARES_METERS` literal lists) and "%s does not
 * require the meters surface" (run against EVERY barrel-derived manifest,
 * not just meters-declaring ones — a radio reporting no meter fields at
 * all must still resolve this layout; the surface self-gates on
 * `view.meters` instead).
 */
import { registerLayout, type LayoutManifest } from './contract';

export const acceptProbeLayout: LayoutManifest = {
  schemaVersion: 1,
  id: 'accept-probe',
  displayName: 'Accept Probe',
  loader: () => import('../../skins/accept-probe/AcceptProbeSkin.svelte'),
  zones: [
    { id: 'main', surfaces: ['vfo'] },
    { id: 'meters', surfaces: ['meters'] },
  ],
  compatibleTopologies: ['1/single', '1/ab', '2/ab_shared', '2/main_sub'],
  requiredSemanticSurfaces: ['vfo'],
  stageSizing: { mode: 'fluid', responsiveBreakpoints: [] },
  fallbackLayoutId: null,
};

registerLayout(acceptProbeLayout);
