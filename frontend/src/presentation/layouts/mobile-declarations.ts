/**
 * MOR-1094 — the mobile presentation entrypoint as a v1 layout manifest
 * (schema, validator and registry: `./contract`, MOR-1066).
 *
 * Kept in its own file so `./declarations.ts` carries one aggregation line per
 * family, alongside `./lcd-declarations.ts` (MOR-1092).
 *
 * A manifest is a DECLARATION, never behaviour. `loader` names the existing
 * skin entrypoint with no change to it, and `stageSizing` records the
 * assignment MOR-1160 froze without implementing it — the shared ScaledStage
 * primitive owns measurement and the transform (MOR-1160 constraint 1), never
 * a layout.
 * In particular this manifest does NOT take over the shell's own orientation
 * handling: `isLandscape` still drives which PTT surface is mounted, and that
 * is live safety behaviour, not a sizing declaration.
 */
import { registerLayout, type LayoutManifest } from './contract';

/**
 * MOR-1160, fluid side. Mobile chrome reflows; it is not an instrument stage
 * scaled as one letterboxed block, so it declares no native size and no
 * `minScale` — and therefore always fits (`fitsViewport`: breakpoints are
 * reflow hints, not a hard gate in v1). That is what makes mobile the only
 * viable destination for a fixed-native layout's one validated fallback hop
 * off a portrait phone, where the LCD stage fails arithmetically.
 *
 * The single breakpoint is the one reflow the shell actually implements:
 * `MobileRadioLayout` switches to its spectrum-dominant landscape arrangement
 * when the viewport is wider than it is tall AND under 500px of HEIGHT. It is
 * recorded here as the honest threshold the layout uses, not as a width.
 */
const MOBILE_FLUID_SIZING = { mode: 'fluid', responsiveBreakpoints: [500] } as const;

/**
 * One zone: the portrait scroll deck, where `SemanticRadioSurfaces` owns the
 * VFO facts and the RX/TX status/action. The landscape arrangement is a
 * fullscreen spectrum with a compact control strip and mounts no semantic
 * zone in this slice — the manifest has no orientation axis, so that is
 * recorded here rather than declared.
 *
 * The press-and-hold PTT affordances (the portrait FAB, the landscape strip)
 * are NOT part of this zone: they stay wired to the App TX controller through
 * the MOR-1011/1012 gesture recognizer, unchanged by this migration.
 */
const MOBILE_ZONES = [{ id: 'portrait-deck', surfaces: ['vfo', 'rxTx'] }] as const;

export const mobileLayout: LayoutManifest = {
  schemaVersion: 1,
  id: 'mobile',
  displayName: 'Mobile',
  loader: () => import('../../skins/mobile/MobileSkin.svelte'),
  zones: MOBILE_ZONES,
  // All four canonical classes: the shell renders VFO A unconditionally and
  // gates the MAIN/SUB selector and the SUB readout on `hasDualReceiver`, so
  // single- and dual-receiver topologies are both structurally supported.
  compatibleTopologies: ['1/single', '1/ab', '2/ab_shared', '2/main_sub'],
  requiredSemanticSurfaces: ['vfo', 'rxTx'],
  stageSizing: MOBILE_FLUID_SIZING,
  // Terminal by construction: a fluid layout never fails a viewport, so a hop
  // off mobile would be unreachable and would only mask a real failure.
  fallbackLayoutId: null,
};

registerLayout(mobileLayout);
