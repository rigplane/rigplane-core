/**
 * memory-channels — the single owner of the 99-channel local memory catalog
 * (MOR-2425 phase A extraction).
 *
 * The IC-7610 class does not support reading memory channel contents over
 * CI-V (`components-v2/panels/MemoryPanel.svelte`'s own docstring), so the
 * catalog is tracked client-side in `localStorage`, never on the radio. This
 * module used to live inline inside `MemoryPanel.svelte`; it is pulled out
 * here so the semantic `MemorySurface.svelte` can read/write the identical
 * key without a second copy of the load/persist logic drifting out of sync
 * with the legacy panel's.
 */

export const MEMORY_STORAGE_KEY = 'rigplane:memory-channels';
export const MAX_MEMORY_CHANNELS = 99;

export interface MemoryEntry {
  freq: number;
  mode: string;
  name: string;
}

/** Reads the catalog from `localStorage`. Returns an empty map on a missing
 *  key, unparsable JSON, or a `localStorage` access failure — never throws. */
export function loadMemoryChannels(): Map<number, MemoryEntry> {
  if (typeof window === 'undefined') return new Map();
  try {
    const stored = localStorage.getItem(MEMORY_STORAGE_KEY);
    if (!stored) return new Map();
    const parsed = JSON.parse(stored) as Record<string, MemoryEntry>;
    const map = new Map<number, MemoryEntry>();
    for (const [k, v] of Object.entries(parsed)) {
      map.set(Number(k), v);
    }
    return map;
  } catch {
    return new Map();
  }
}

/** Writes the whole catalog back to `localStorage`. Swallows a write failure
 *  (quota, private-browsing) the same way the pre-extraction inline code did. */
export function persistMemoryChannels(channels: ReadonlyMap<number, MemoryEntry>): void {
  try {
    const obj: Record<string, MemoryEntry> = {};
    for (const [k, v] of channels) {
      obj[String(k)] = v;
    }
    localStorage.setItem(MEMORY_STORAGE_KEY, JSON.stringify(obj));
  } catch { /* ignore */ }
}
