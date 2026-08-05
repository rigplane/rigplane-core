/**
 * The two v3 design languages frozen by MOR-977 §4.6: `studioline`
 * (reference) and `fieldline` (proof). RX/TX namespace chosen here
 * (MOR-1231 note): `--dl-rx-*`/`--dl-tx-*`, distinct from the legacy
 * `--v2-tx-*` family. Registering here does not hardcode "two families":
 * contract.test.ts registers a third the same way.
 *
 * MOR-1073 landed studioline's real token set and its three renderers
 * (`./studioline`); MOR-1074 does the same for `fieldline` (`./fieldline`),
 * which retires the shared placeholder bundle that used to stand in for it.
 * Both families now supply their own contrast-proven ring tone, so nothing
 * here inherits `var(--accent)` — which carries no such guarantee — any more.
 * The contract's `var(--accent)` default is deliberately left untouched.
 *
 * The two bundles are imported under family-qualified aliases because they
 * export the same three renderer names: that symmetry IS the proof — the
 * second language plugs into identical slots with no contract change.
 */
import { registerDesignLanguage, type DesignLanguageManifest } from './contract';
import { renderFrequency as studiolineFrequency } from './studioline/frequency-renderer';
import { renderMeter as studiolineMeter } from './studioline/meters-renderer';
import { renderStateFeedback as studiolineStateFeedback } from './studioline/state-feedback-renderer';
import { STUDIOLINE_TOKENS } from './studioline/tokens';
import { renderFrequency as fieldlineFrequency } from './fieldline/frequency-renderer';
import { renderMeter as fieldlineMeter } from './fieldline/meters-renderer';
import { renderStateFeedback as fieldlineStateFeedback } from './fieldline/state-feedback-renderer';
import { FIELDLINE_TOKENS } from './fieldline/tokens';

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
    frequencyDisplay: studiolineFrequency,
    meters: studiolineMeter,
    stateFeedback: studiolineStateFeedback,
  },
};

export const fieldline: DesignLanguageManifest = {
  id: 'fieldline',
  displayName: 'Fieldline',
  tokens: FIELDLINE_TOKENS,
  // dense clamped out — fieldline runs at 0.6 relative density (MOR-977 §4.4).
  density: { kind: 'clamped', supported: ['comfortable', 'compact'] },
  layoutCompatibility: [{
    layoutId: 'dual-receiver-cockpit',
    compatible: false,
    reason: 'fieldline cannot serve as the desktop dual-receiver default at 0.6 relative density (MOR-977 §4.4).',
  }],
  renderers: {
    frequencyDisplay: fieldlineFrequency,
    meters: fieldlineMeter,
    stateFeedback: fieldlineStateFeedback,
  },
};

registerDesignLanguage(studioline);
registerDesignLanguage(fieldline);
