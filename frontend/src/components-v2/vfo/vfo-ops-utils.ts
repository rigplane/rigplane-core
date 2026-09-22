/** Utility functions for VFO operation button labels. */

/**
 * Receiver-level schemes (MAIN/SUB rigs): `main_sub` rigs have a per-receiver
 * VFO pair and `ab_shared` rigs (FTX-1) have two receivers with one VFO slot
 * each; both put their swap/copy/equalize labels in MAIN/SUB terms.
 */
function isReceiverLevelScheme(scheme: string): boolean {
  return scheme === 'main_sub' || scheme === 'ab_shared';
}

/** Returns the swap button label for the given VFO scheme. */
export function vfoSwapLabel(scheme: string): string {
  return isReceiverLevelScheme(scheme) ? 'M↔S' : 'A↔B';
}

/** Returns the copy button label for the given VFO scheme. */
export function vfoCopyLabel(scheme: string): string {
  return isReceiverLevelScheme(scheme) ? 'M→S' : 'A→B';
}

/** Returns the equal button label for the given VFO scheme. */
export function vfoEqualLabel(scheme: string): string {
  return isReceiverLevelScheme(scheme) ? 'M=S' : 'A=B';
}

/** Returns the TX indicator label for a given VFO slot and scheme. */
export function vfoTxLabel(scheme: string, slot: 'main' | 'sub'): string {
  if (isReceiverLevelScheme(scheme)) {
    return slot === 'main' ? 'TX→M' : 'TX→S';
  }
  return slot === 'main' ? 'TX→A' : 'TX→B';
}
