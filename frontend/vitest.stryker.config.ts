/// <reference types="vitest/config" />
import { defineConfig } from 'vitest/config'
import viteConfig from './vite.config'

// Test set for the Stryker run (stryker.config.mjs). Replaces the repo's
// two-project `test` block with the two test files named after the mutated
// modules, so each mutant runs these files instead of the whole suite.
export default defineConfig({
  ...viteConfig,
  test: {
    environment: 'jsdom',
    setupFiles: ['./vitest.setup.ts'],
    include: [
      'src/lib/runtime/adapters/__tests__/radio-view-model-adapter.test.ts',
      'src/semantic/__tests__/radio-view-model.test.ts',
    ],
    pool: 'threads',
  },
})
