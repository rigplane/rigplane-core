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
