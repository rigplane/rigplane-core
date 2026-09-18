/** True when ctrl, alt, or meta is held — the modified-arrow class the
 * global keyboard map owns. Lives in $lib/utils, below components-v2, so
 * primitives may use it. Every widget key handler that must leave a
 * modified arrow to the global map calls it first (grep the name); the
 * map re-exports it from components-v2/layout/keyboard-map.ts. Pinned by
 * the hasCommandModifier block in keyboard-map.test.ts. */
export function hasCommandModifier(event: {
  ctrlKey?: boolean;
  altKey?: boolean;
  metaKey?: boolean;
}): boolean {
  return Boolean(event.ctrlKey) || Boolean(event.altKey) || Boolean(event.metaKey);
}
