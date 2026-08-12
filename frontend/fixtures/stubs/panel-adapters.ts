/** Verification-only semantic command binder: records through the existing fixture bus stub. */
import {
  makeAgcHandlers, makeAntennaHandlers, makeAudioRoutingHandlers, makeBandHandlers,
  makeCwPanelHandlers, makeDspHandlers, makeFilterHandlers, makeModeHandlers,
  makeRfFrontEndHandlers, makeRitXitHandlers, makeRxAudioHandlers, makeScanHandlers,
  makeScopeControlsHandlers, makeTxHandlers, makeVfoHandlers, makeVoxHandlers,
} from './command-bus';

export function bindSemanticSurfaceHandlers() {
  return Object.freeze({
    agc: makeAgcHandlers(), antenna: makeAntennaHandlers(), audioRouting: makeAudioRoutingHandlers(),
    band: makeBandHandlers(), cw: makeCwPanelHandlers(), dsp: makeDspHandlers(),
    filter: makeFilterHandlers(), mode: makeModeHandlers(), rfFrontEnd: makeRfFrontEndHandlers(),
    ritXit: makeRitXitHandlers(), rxAudio: makeRxAudioHandlers(), scan: makeScanHandlers(),
    scopeControls: makeScopeControlsHandlers(), tx: makeTxHandlers(), vfo: makeVfoHandlers(), vox: makeVoxHandlers(),
  });
}

/**
 * MOR-1441 — `SemanticRadioSurfaces.svelte` now also imports
 * `getPendingFrequencyHz` from the real `panel-adapters` module. Per this
 * file's own MOR-1271/MOR-1320 lesson, an export missing here fails at
 * MODULE RESOLUTION and takes the whole fixture harness dark, not merely
 * degraded — so the stub always needs it, even though the deterministic
 * offline harness never has a real in-flight command to report. `null`
 * ("nothing pending") is the correct and only honest answer here.
 */
export function getPendingFrequencyHz(_receiver: 0 | 1): number | null {
  return null;
}

/**
 * MOR-1441 leg 2 — same MOR-1271/MOR-1320 lesson as `getPendingFrequencyHz`
 * above, for the discrete-control pending accessors `SemanticRadioSurfaces.
 * svelte` now also imports (filter select, preamp, NB/NR). `null` is the
 * correct and only honest answer for the same reason: the deterministic
 * offline harness never has a real in-flight command to report.
 */
export function getPendingFilterSelection(_receiver: 0 | 1): number | null {
  return null;
}
export function getPendingPreampLevel(_receiver: 0 | 1): number | null {
  return null;
}
export function getPendingNbOn(_receiver: 0 | 1): boolean | null {
  return null;
}
export function getPendingNrOn(_receiver: 0 | 1): boolean | null {
  return null;
}
