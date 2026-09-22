/** Utility functions for VFO operation button labels. */

/** Returns the swap button label for the given VFO scheme. */
export function vfoSwapLabel(scheme: string): string {
  return scheme === 'main_sub' ? 'M⇄S' : 'A↔B';
}

/** Returns the copy button label for the given VFO scheme. */
export function vfoCopyLabel(scheme: string): string {
  return scheme === 'main_sub' ? 'M→S' : 'A→B';
}

/** Returns the equal button label for the given VFO scheme. */
export function vfoEqualLabel(scheme: string): string {
  return scheme === 'main_sub' ? 'M=S' : 'A=B';
}

/** Returns the TX indicator label for a given VFO slot and scheme. */
export function vfoTxLabel(scheme: string, slot: 'main' | 'sub'): string {
  if (scheme === 'main_sub') {
    return slot === 'main' ? 'TX→M' : 'TX→S';
  }
  return slot === 'main' ? 'TX→A' : 'TX→B';
}
