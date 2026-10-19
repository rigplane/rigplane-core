// @ts-check
/** @type {import('@stryker-mutator/api/core').PartialStrykerOptions} */
export default {
  mutate: [
    'src/lib/transport/controller-client.ts',
    'src/lib/runtime/adapters/radio-view-model-adapter.ts',
    'src/semantic/radio-view-model.ts',
    'src/lib/runtime/props/panel-props.ts',
    'src/lib/runtime/adapters/ptt-restore-gate.ts',
    'src/lib/runtime/adapters/mod-input-gates.ts',
    'src/lib/runtime/adapters/control-feedback-projection.ts',
    'src/lib/runtime/adapters/pending-intent-table.ts',
    'src/lib/runtime/adapters/receiver-feedback.ts',
    'src/lib/runtime/adapters/level-feedback.ts',
    'src/lib/runtime/adapters/if-shift-feedback.ts',
  ],
  testRunner: 'vitest',
  vitest: {
    configFile: 'vitest.stryker.config.ts',
  },
  tempDirName: '.stryker-tmp',
  // The default prepends `// @ts-nocheck` to JS/TS files under src/, lib/, test/.
  // vitest does not type-check, so that buys nothing here.
  disableTypeChecks: false,
  timeoutMS: 5000,
  concurrency: 4,
  reporters: ['html', 'json', 'clear-text'],
  thresholds: { high: 80, low: 60, break: null },
}
