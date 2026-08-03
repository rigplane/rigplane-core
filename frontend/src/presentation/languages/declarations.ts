/**
 * The two v3 design languages frozen by MOR-977 §4.6: `studioline`
 * (reference) and `fieldline` (proof). Placeholder tokens, shared between
 * both — no visual implementation yet (MOR-1073/MOR-1074). RX/TX namespace
 * chosen here (MOR-1231 note): `--dl-rx-*`/`--dl-tx-*`, distinct from the
 * legacy `--v2-tx-*` family. Registering here does not hardcode "two
 * families" — contract.test.ts registers a third the same way.
 */
import { registerDesignLanguage, type DesignLanguageManifest } from './contract';

const placeholderTokens: DesignLanguageManifest['tokens'] = {
  typography: { fontFamily: 'system-ui, sans-serif', weight: 400, fontVariantNumeric: 'tabular-nums' },
  geometry: { radius: '0px', borderWidth: '0px' },
  meters: { trackWidth: '2px', segmentGap: '0px' },
  frequency: { digitWeight: 400, rankedGroups: true },
  motion: { durationMs: 150, reducedMotionSafe: true },
  focusRing: '0 0 0 2px var(--accent)',
  rx: { idle: 'var(--dl-rx-idle)', active: 'var(--dl-rx-active)', tuning: 'var(--dl-rx-tuning)' },
  tx: { idle: 'var(--dl-tx-idle)', active: 'var(--dl-tx-active)', tuning: 'var(--dl-tx-tuning)' },
};

export const studioline: DesignLanguageManifest = {
  id: 'studioline',
  displayName: 'Studioline',
  tokens: placeholderTokens,
  // Holds all three density steps without collision (MOR-977 §4.2.3).
  density: { kind: 'clamped', supported: ['comfortable', 'compact', 'dense'] },
  layoutCompatibility: [],
  renderers: {},
};

export const fieldline: DesignLanguageManifest = {
  id: 'fieldline',
  displayName: 'Fieldline',
  tokens: placeholderTokens,
  // dense clamped out — fieldline runs at 0.6 relative density (MOR-977 §4.4).
  density: { kind: 'clamped', supported: ['comfortable', 'compact'] },
  layoutCompatibility: [{
    layoutId: 'dual-receiver-cockpit',
    compatible: false,
    reason: 'fieldline cannot serve as the desktop dual-receiver default at 0.6 relative density (MOR-977 §4.4).',
  }],
  renderers: {},
};

registerDesignLanguage(studioline);
registerDesignLanguage(fieldline);
