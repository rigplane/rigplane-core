/** True when ctrl, alt, or meta is held — the modified-arrow class the
 * global keyboard map owns. Lives in $lib/utils, below components-v2,
 * so primitives may consume it too: re-exported by
 * components-v2/layout/keyboard-map.ts (which also applies it in
 * focusedElementOwnsArrowKey), called by the SegmentedButton,
 * ActiveReceiverToggle, and SpectrumPanel split-separator key handlers
 * (#3544) and by the frequency readout's digit-stepping guard (MOR-2512);
 * pinned by the "hasCommandModifier" block in keyboard-map.test.ts and
 * by the readout's modified-arrow pin. */
export function hasCommandModifier(event: {
  ctrlKey?: boolean;
  altKey?: boolean;
  metaKey?: boolean;
}): boolean {
  return Boolean(event.ctrlKey) || Boolean(event.altKey) || Boolean(event.metaKey);
}
