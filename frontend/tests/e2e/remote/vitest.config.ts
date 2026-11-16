import { defineConfig } from 'vitest/config';

/**
 * Vitest config for the remote E2E harness's own unit tests. The frontend
 * root config (vite.config.ts) includes only "src" test files, so the
 * harness runs its tests explicitly:
 *
 *   npx vitest run --config tests/e2e/remote/vitest.config.ts
 *
 * Node environment: these tests exercise plain functions, no DOM.
 */
export default defineConfig({
  test: {
    environment: 'node',
    include: ['tests/e2e/remote/*.test.ts'],
  },
});
