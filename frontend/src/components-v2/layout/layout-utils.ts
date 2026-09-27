/** Layout utility helpers for RadioLayout. */

/**
 * Returns true when radioState indicates audio capability is available.
 */
export function hasLiveAudioFromState(radioState: any): boolean {
  return radioState?.capabilities?.audio ?? false;
}
