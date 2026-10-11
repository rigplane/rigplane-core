// @ts-check
/** @type {import('@stryker-mutator/api/core').PartialStrykerOptions} */
export default {
  mutate: [
    'src/lib/runtime/adapters/radio-view-model-adapter.ts',
    'src/semantic/radio-view-model.ts',
    'src/lib/runtime/adapters/mod-input-gates.ts',
  ],
  testRunner: 'vitest',
  vitest: {
    configFile: 'vitest.stryker.config.ts',
  },
  tempDirName: '.stryker-tmp',
  // The default prepends `// @ts-nocheck` to JS/TS files under src/, lib/, test/.
  // vitest does not type-check, so that buys nothing here, and under --inPlace
  // it rewrites files that tests pin by sha256
  // (MobileRadioLayout.honesty.isolated.test.ts).
  disableTypeChecks: false,
  timeoutMS: 5000,
  concurrency: 4,
  reporters: ['html', 'json', 'clear-text'],
  thresholds: { high: 80, low: 60, break: null },
}
