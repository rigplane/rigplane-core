/**
 * Web-storage isolation contract for the test suite.
 *
 * Node's `--localstorage-file` Storage (the repo-standard NODE_OPTIONS for
 * vitest) is ONE store shared by every worker thread in the process.
 * `isolate: true` gives each file a fresh module registry, but every file
 * still read and wrote the same backing store, concurrently — a sibling
 * file's `localStorage.setItem`/`clear` leaked into this worker's
 * module-load reads and flipped tests red with no production change
 * (`mod-input-tx-guard.isolated.test.ts` was the canonical victim:
 * `mod-input-auto.svelte.ts` latches its opt-in pref at module load, and a
 * leaked `rigplane:auto-lan-mod-input=true` silenced the MOR-617 guard).
 *
 * `vitest.setup.ts` shadows both storages with a per-environment in-memory
 * implementation. This file pins that contract from inside the isolated
 * pool: if the setup file is dropped from the config, the marker assertion
 * fails deterministically, and the empty-at-load assertions become flaky —
 * exactly the class of failure the setup exists to prevent.
 */
import { describe, expect, it } from 'vitest';

import { TEST_STORAGE_MARKER } from '../../vitest.setup';

describe('per-file web-storage isolation (vitest.setup.ts)', () => {
  it('localStorage is the test-owned in-memory stub, not a shared store', () => {
    expect(
      (localStorage as unknown as Record<string, unknown>)[TEST_STORAGE_MARKER],
    ).toBe(true);
    expect(
      (sessionStorage as unknown as Record<string, unknown>)[TEST_STORAGE_MARKER],
    ).toBe(true);
  });

  it('both storages start empty — no keys leak in from sibling test files', () => {
    expect(localStorage.length).toBe(0);
    expect(sessionStorage.length).toBe(0);
  });

  it('supports the full Storage surface the suite relies on', () => {
    localStorage.setItem('probe', 'a');
    expect(localStorage.getItem('probe')).toBe('a');
    expect(localStorage.length).toBe(1);
    expect(localStorage.key(0)).toBe('probe');
    localStorage.removeItem('probe');
    expect(localStorage.getItem('probe')).toBeNull();
    localStorage.setItem('probe', 'b');
    localStorage.clear();
    expect(localStorage.length).toBe(0);
  });
});
