/// <reference types="vitest/config" />
import { defineConfig } from 'vitest/config'
import viteConfig from './vite.config'

// Test set for the Stryker run (stryker.config.mjs). Replaces the repo's
// two-project `test` block with the test files covering the mutated
// modules, so each mutant runs these files instead of the whole suite.
// The mod-input tests import the .svelte.ts wrappers, which call the
// mutated mod-input-gates.ts for each gate decision; the opt-in and
// empty-pending early returns stay in the wrappers.
// panel-props.no-fabricated-defaults.test.ts is deliberately excluded: it
// reads panel-props.ts as text (readFileSync), which under --inPlace is
// the instrumented source.
export default defineConfig({
  ...viteConfig,
  test: {
    environment: 'jsdom',
    setupFiles: ['./vitest.setup.ts'],
    include: [
      'src/lib/runtime/adapters/__tests__/radio-view-model-adapter.test.ts',
      'src/semantic/__tests__/radio-view-model.test.ts',
      'src/lib/runtime/props/__tests__/panel-props.test.ts',
      'src/lib/runtime/props/__tests__/notch-freq-domain.isolated.test.ts',
      'src/lib/runtime/props/__tests__/nr-level-projection.isolated.test.ts',
      'src/lib/runtime/adapters/__tests__/mod-input-auto.isolated.test.ts',
      'src/lib/runtime/adapters/__tests__/mod-input-tx-guard.isolated.test.ts',
      'src/lib/runtime/adapters/__tests__/filter-width-command-lifecycle.isolated.test.ts',
      'src/lib/runtime/adapters/__tests__/dsp-command-feedback.isolated.test.ts',
      'src/lib/runtime/adapters/__tests__/tx-aux-command-feedback.isolated.test.ts',
      'src/lib/runtime/adapters/__tests__/rf-sql-command-feedback.isolated.test.ts',
      'src/lib/runtime/adapters/__tests__/af-rf-power-command-feedback.isolated.test.ts',
      'src/lib/runtime/adapters/__tests__/cw-level-command-feedback.isolated.test.ts',
      'src/lib/runtime/adapters/__tests__/pbt-if-shift-command-feedback.isolated.test.ts',
      'src/lib/runtime/adapters/__tests__/pbt-if-shift-terminal-outcome-retention.isolated.test.ts',
      'src/lib/runtime/adapters/__tests__/mor1441-pending-discrete.isolated.test.ts',
      'src/lib/runtime/adapters/__tests__/mor1441-pending-frequency.isolated.test.ts',
      'src/lib/runtime/adapters/__tests__/mor1519-mode-armed.isolated.test.ts',
      'src/lib/runtime/adapters/__tests__/mor2111-repeater-conformance.isolated.test.ts',
    ],
    pool: 'threads',
  },
})
