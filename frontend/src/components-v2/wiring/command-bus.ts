/**
 * Compatibility facade for legacy v2 wiring imports.
 *
 * Runtime panel commands are owned by `$lib/runtime/commands/panel-commands`;
 * this module contains identity-preserving named re-exports only.
 */
export {
  makeAgcHandlers,
  makeAudioRoutingHandlers,
  makeBandHandlers,
  makeCwPanelHandlers,
  makeDspHandlers,
  makeFilterHandlers,
  makeModeHandlers,
  makePresetHandlers,
  makeRfFrontEndHandlers,
  makeRitXitHandlers,
  makeRxAudioHandlers,
  makeScanHandlers,
  makeTxHandlers,
  makeAntennaHandlers,
  makeVfoHandlers,
  makeVoxHandlers,
  makeSystemHandlers,
  makeScopeControlsHandlers,
  makeKeyboardHandlers,
} from '$lib/runtime/commands/panel-commands';
