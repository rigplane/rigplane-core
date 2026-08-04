/**
 * MOR-1092 — the two LCD/scope presentation entrypoints as v1 layout
 * manifests (schema, validator and registry: `./contract.ts`, MOR-1066).
 *
 * Kept in their own file so `./declarations.ts` carries one aggregation line
 * per family: the remaining migrations (MOR-1067/93/94) land beside this one
 * without three tickets editing the same declaration block.
 *
 * A manifest is a DECLARATION, never behaviour. `loader` names the existing
 * skin entrypoint with no change to it; `sizing` records the assignment
 * MOR-1160 froze without implementing it — the shared ScaledStage primitive
 * owns measurement and the transform (MOR-1160 constraint 1), never a layout.
 */
import { registerLayout, type LayoutManifest } from './contract';

/**
 * MOR-1160: the incoming LCD directions are authored on a 1280x540 native
 * stage and scaled as one uniform, letterboxed block — the LCD glass is the
 * archetype instrument surface. `minScale` 0.5 is what excludes portrait
 * mobile arithmetically (constraint 4): an iPhone-class 390x844 viewport
 * achieves min(390/1280, 844/540) ~= 0.30 and fails, with no
 * mobile-detection branch anywhere in the resolution path.
 */
const LCD_NATIVE_STAGE = {
  mode: 'fixed-native', nativeW: 1280, nativeH: 540, minScale: 0.5,
} as const;

/**
 * Both variants mount the same single semantic zone — `LcdLayout`'s right-
 * hand control column, where `SemanticRadioSurfaces` now owns the VFO facts
 * and the RX/TX action. The amber glass itself declares no zone: it stays
 * legacy presentation for this slice and is redesigned by MOR-1162.
 */
const LCD_ZONES = [{ id: 'control-column', surfaces: ['vfo', 'rxTx'] }] as const;

/**
 * All four canonical classes. Each variant renders VFO A unconditionally and
 * gates the second receiver on `hasDualReceiver`, so single- and dual-receiver
 * topologies are both structurally supported — the declaration is what the
 * layout does, not an aspiration.
 */
const LCD_TOPOLOGIES = ['1/single', '1/ab', '2/ab_shared', '2/main_sub'] as const;

export const lcdCockpitLayout: LayoutManifest = {
  schemaVersion: 1,
  id: 'lcd-cockpit',
  displayName: 'LCD Cockpit',
  loader: () => import('../../skins/lcd-cockpit/LcdCockpitSkin.svelte'),
  zones: LCD_ZONES,
  compatibleTopologies: LCD_TOPOLOGIES,
  requiredSemanticSurfaces: ['vfo', 'rxTx'],
  sizing: LCD_NATIVE_STAGE,
  // The family's universal variant — nothing left to fall back to.
  fallbackLayoutId: null,
};

/**
 * The scope-dominant variant names the cockpit as its single fallback hop:
 * the cockpit is where the persisted `amber-lcd` preference already routes.
 * The registry re-validates that hop, so a viewport below the shared
 * `minScale` resolves to `undefined` rather than silently landing on a
 * sibling that fails the same gate (MOR-1066 review cycle 1, F1).
 */
export const lcdScopeLayout: LayoutManifest = {
  schemaVersion: 1,
  id: 'lcd-scope',
  displayName: 'LCD Scope',
  loader: () => import('../../skins/lcd-scope/LcdScopeSkin.svelte'),
  zones: LCD_ZONES,
  compatibleTopologies: LCD_TOPOLOGIES,
  requiredSemanticSurfaces: ['vfo', 'rxTx'],
  sizing: LCD_NATIVE_STAGE,
  fallbackLayoutId: 'lcd-cockpit',
};

registerLayout(lcdCockpitLayout);
registerLayout(lcdScopeLayout);
