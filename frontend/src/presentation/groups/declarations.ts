/**
 * MOR-2253 slice 1 — `peer-split-glass`, the first `InstrumentGroup`
 * (instrument-group ADR §9/§10.8's migration order starts with peer-split).
 * Absorbs the native-canvas duplication (ADR §4, F2) between
 * `../layouts/segmentline-declarations.ts`'s `SEGMENTLINE_GLASS_STAGE` and
 * `../../skins/segmentline/PeerSplitLayout.svelte`'s own former
 * `NATIVE_W`/`NATIVE_H` constants — both now read this declaration by
 * reference instead of repeating the numbers.
 *
 * Declared directly here rather than in its own per-family file: this is the
 * first group, the same shape `../layouts/declarations.ts` used for its own
 * first manifest (`sdrTestLayout`) before later families split out.
 */
import { registerGroup, type InstrumentGroup } from './contract';

export const peerSplitGlassGroup = {
  schemaVersion: 1,
  id: 'peer-split-glass',
  canvas: { w: 1280, h: 540 },
  scaling: { mode: 'fixed-native', minScale: 0.5 },
} as const satisfies InstrumentGroup;

export const unifiedInstrumentGlassGroup = {
  schemaVersion: 1,
  id: 'unified-instrument-glass',
  canvas: { w: 1280, h: 540 },
  scaling: { mode: 'fixed-native', minScale: 0.5 },
} as const satisfies InstrumentGroup;

export const panadapterFirstGlassGroup = {
  schemaVersion: 1,
  id: 'panadapter-first-glass',
  canvas: { w: 1280, h: 594 },
  scaling: { mode: 'fixed-native', minScale: 0.5 },
} as const satisfies InstrumentGroup;

registerGroup(peerSplitGlassGroup);
registerGroup(unifiedInstrumentGlassGroup);
registerGroup(panadapterFirstGlassGroup);
