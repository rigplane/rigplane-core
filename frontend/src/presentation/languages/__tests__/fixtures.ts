/** Shared valid-manifest fixture for MOR-1072 contract tests (not itself a test file). */
import type { DesignLanguageManifest } from '../contract';

export function validManifest(overrides: Partial<DesignLanguageManifest> = {}): DesignLanguageManifest {
  return {
    id: 'testline',
    displayName: 'Testline',
    tokens: {
      typography: { fontFamily: 'sans-serif', weight: 400, fontVariantNumeric: 'tabular-nums' },
      geometry: { radius: '0px', borderWidth: '0px' },
      meters: { trackWidth: '2px', segmentGap: '0px' },
      frequency: { digitWeight: 400, rankedGroups: true },
      motion: { durationMs: 100, reducedMotionSafe: true },
      focusRing: '0 0 0 2px var(--accent)',
      rx: { idle: 'var(--dl-rx-idle)', active: 'var(--dl-rx-active)', tuning: 'var(--dl-rx-tuning)' },
      tx: { idle: 'var(--dl-tx-idle)', active: 'var(--dl-tx-active)', tuning: 'var(--dl-tx-tuning)' },
    },
    density: { kind: 'clamped', supported: ['comfortable', 'compact', 'dense'] },
    layoutCompatibility: [],
    renderers: {},
    ...overrides,
  };
}
