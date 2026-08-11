/**
 * Per-environment web-storage isolation for vitest.
 *
 * The repo convention runs vitest with `NODE_OPTIONS="--localstorage-file=…"`
 * (without it, Node's `localStorage` global throws and ~50+ unrelated tests
 * fail as an env artifact). That file-backed Storage is ONE store shared by
 * every worker thread in the process: `isolate: true` gives each test file a
 * fresh module registry, but all files still read and write the same backing
 * store, concurrently. Any file's `localStorage.setItem`/`clear` raced every
 * other file's module-load reads and `beforeEach` seeding — the source of
 * order-dependent flakes with no production change (canonical victim:
 * `mod-input-tx-guard.isolated.test.ts`, silenced by a sibling file's leaked
 * `rigplane:auto-lan-mod-input=true`; also the panel-order seeding in the
 * semantic-desktop/lcd migration component tests).
 *
 * This setup file shadows `localStorage`/`sessionStorage` with an in-memory
 * Storage per test environment: genuinely per-file under `isolate: true`,
 * per-worker in the `fast` pool (strictly less shared than before). It uses
 * `Object.defineProperty`, NOT `vi.stubGlobal`, on purpose: tests that stub
 * storage themselves (e.g. the workspace purity suites) must have
 * `vi.unstubAllGlobals()` restore to THIS stub, never to the shared Node
 * store.
 *
 * Contract pinned by `src/__tests__/storage-isolation.isolated.test.ts`.
 */

import { afterEach } from 'vitest';
import { resetSharedTuningAccumulatorForTests } from './src/lib/runtime/commands/tuning-accumulator';

/** Marker property test code can use to assert the stub is installed. */
export const TEST_STORAGE_MARKER = '__rigplaneTestStorage';

function makeMemoryStorage(): Storage {
  const data = new Map<string, string>();
  const storage: Storage = {
    get length() {
      return data.size;
    },
    key(index: number): string | null {
      return [...data.keys()][index] ?? null;
    },
    getItem(key: string): string | null {
      return data.get(String(key)) ?? null;
    },
    setItem(key: string, value: string): void {
      data.set(String(key), String(value));
    },
    removeItem(key: string): void {
      data.delete(String(key));
    },
    clear(): void {
      data.clear();
    },
  };
  Object.defineProperty(storage, TEST_STORAGE_MARKER, {
    value: true,
    enumerable: false,
  });
  return storage;
}

for (const name of ['localStorage', 'sessionStorage'] as const) {
  Object.defineProperty(globalThis, name, {
    value: makeMemoryStorage(),
    writable: true,
    configurable: true,
  });
}

// MOR-1425 review B5: the tuning accumulator is now a module-level
// singleton shared by every `makeVfoHandlers()` caller (production
// correctness — see `tuning-accumulator.ts`). Left unreset, a "hot" burst
// left pending by one test's fake-timer clock is misread as still-hot by
// the next test in the same file (`isolate: true` is per-FILE, not
// per-test — same class of leak `localStorage` above was patched for).
afterEach(() => {
  resetSharedTuningAccumulatorForTests();
});
