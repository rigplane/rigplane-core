/**
 * Single-source list of locale-invariant faceplate catalog keys (MOR-1450).
 *
 * Policy: `docs/i18n/faceplate-locale-invariance.md`. Instrument/faceplate
 * vocabulary — mode names, control abbreviations, and the value words that
 * pair with them (ON/OFF, SPLIT, ...) — must render identically in every
 * bundled locale, matching the en-US source verbatim. Auxiliary prose
 * (dialogs, settings, help text, tooltips, error/status messages, refusal
 * reasons) is NOT on this list and continues to localize normally.
 *
 * Enforcement: `__tests__/faceplate-invariant.test.ts` asserts every key
 * below resolves to the same string across all bundled locale catalogs.
 *
 * Adding a new faceplate-domain key: add it here. Either mirror the en-US
 * value verbatim in every locale file, or omit it from non-English catalogs
 * so it falls back to en-US (which also satisfies the invariant).
 */
export const FACEPLATE_INVARIANT_KEYS = [
  // Power toggle button text (the literal printed state of the button).
  'core.statusbar.power.labelOn',
  'core.statusbar.power.labelOff',
  // Now-playing "LIVE" marker — protocol/ecosystem glossary token.
  'core.statusbar.nowPlaying.live',
  // Mobile nav tabs / section chips that ARE the bare control token.
  'core.mobile.nav.tab.vfo',
  'core.mobile.nav.tab.tx',
  'core.mobile.chip.band',
  'core.mobile.chip.scan',
  'core.mobile.chip.rf',
  'core.mobile.chip.dsp',
  'core.mobile.chip.ritXit',
  'core.mobile.chip.tx',
  'core.mobile.sheet.setup',
  'core.mobile.setupButton',
  // Mode-input control label rendered directly on the mode panel.
  'core.modePanel.modInputLabel',
  // VFO surface readouts: label + value-word pairs (e.g. "Split: off").
  'core.vfo.split.label',
  'core.vfo.dualWatch.label',
  'core.vfo.state.on',
  'core.vfo.state.off',
  'core.vfo.state.unknown',
  'core.vfo.txTarget.label',
  'core.vfo.splitDigest.rx',
  'core.vfo.splitDigest.tx',
] as const;

export type FaceplateInvariantKey = (typeof FACEPLATE_INVARIANT_KEYS)[number];
