/**
 * MOR-1088 — reactive PTT-mode holder for the mobile gesture/orientation
 * harness. `PttFab.svelte`'s own `handlePointerDown` branches on
 * `mode === 'latched'` (tap-to-unlatch), so the fixture must feed it a
 * genuinely reactive value — same `$state` pattern
 * `$lib/stores/capabilities.svelte.ts` already uses for module-level runes
 * outside a component tree, not a plain object `ptt-main.ts` (no runes
 * there) could mutate silently.
 */
let mode = $state<'idle' | 'held' | 'latched'>('idle');

export function getPttMode(): 'idle' | 'held' | 'latched' {
  return mode;
}

export function setPttMode(next: 'idle' | 'held' | 'latched'): void {
  mode = next;
}
