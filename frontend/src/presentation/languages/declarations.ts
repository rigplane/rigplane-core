/**
 * The two v3 design languages frozen by MOR-977 §4.6: `studioline`
 * (reference) and `fieldline` (proof). RX/TX namespace chosen here
 * (MOR-1231 note): `--dl-rx-*`/`--dl-tx-*`, distinct from the legacy
 * `--v2-tx-*` family. Registering here does not hardcode "two families":
 * contract.test.ts registers a third the same way.
 *
 * MOR-1073 landed studioline's real token set and its three renderers
 * (`./studioline`). `fieldline` still carries the placeholder bundle below
 * until MOR-1074 gives it a grammar of its own.
 */
import { registerDesignLanguage, type DesignLanguageManifest } from './contract';
import { renderFrequency } from './studioline/frequency-renderer';
import { renderMeter } from './studioline/meters-renderer';
import { renderStateFeedback } from './studioline/state-feedback-renderer';
import { STUDIOLINE_TOKENS } from './studioline/tokens';

const placeholderTokens: DesignLanguageManifest['tokens'] = {
  typography: { fontFamily: 'system-ui, sans-serif', weight: 400, fontVariantNumeric: 'tabular-nums' },
  geometry: { radius: '0px', borderWidth: '0px' },
  meters: { trackWidth: '2px', segmentGap: '0px' },
  frequency: { digitWeight: 400, rankedGroups: true },
  motion: { durationMs: 150, reducedMotionSafe: true },
  // Outline shorthand, not a box-shadow spread list (MOR-1232 D5): consumers
  // assign this straight to `outline`, where `0 0 0 2px …` is silently
  // invalid and the ring never appears. The `var(--accent)` COLOUR is
  // untouched here on purpose — it carries no contrast guarantee (it fails
  // 3:1 on a light skin), and whether that is fixed per language or in the
  // contract is an open owner question, not this slice's call. `studioline`
  // pins and proves its own ring colour instead (see ./studioline/tokens.ts).
  focusRing: '2px solid var(--accent)',
  rx: { idle: 'var(--dl-rx-idle)', active: 'var(--dl-rx-active)', tuning: 'var(--dl-rx-tuning)' },
  tx: { idle: 'var(--dl-tx-idle)', active: 'var(--dl-tx-active)', tuning: 'var(--dl-tx-tuning)' },
};

export const studioline: DesignLanguageManifest = {
  id: 'studioline',
  displayName: 'Studioline',
  tokens: STUDIOLINE_TOKENS,
  // Holds all three density steps without collision (MOR-977 §4.2.3).
  density: { kind: 'clamped', supported: ['comfortable', 'compact', 'dense'] },
  // The mirror of fieldline's declaration below: two borderless channel
  // strips sharing one optical margin is the natural dual-receiver form
  // (MOR-977 §4.2.2), so the reference language says so as a manifest fact.
  layoutCompatibility: [{ layoutId: 'dual-receiver-cockpit', compatible: true }],
  renderers: {
    frequencyDisplay: renderFrequency,
    meters: renderMeter,
    stateFeedback: renderStateFeedback,
  },
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
