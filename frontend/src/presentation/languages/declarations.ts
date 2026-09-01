/**
 * The two v3 design languages frozen by MOR-977 §4.6: `studioline`
 * (reference) and `fieldline` (proof). RX/TX namespace chosen here
 * (MOR-1231 note): `--dl-rx-*`/`--dl-tx-*`, distinct from the legacy
 * `--v2-tx-*` family. Registering here does not hardcode "two families":
 * `segmentline` below is the real third.
 *
 * MOR-1073 landed studioline's real token set and its three renderers
 * (`./studioline`); MOR-1074 does the same for `fieldline` (`./fieldline`),
 * which retires the shared placeholder bundle that used to stand in for it.
 * The contract's `var(--accent)` default is deliberately left untouched.
 *
 * The two bundles are imported under family-qualified aliases because they
 * export the same three renderer names: that symmetry IS the proof — the
 * second language plugs into identical slots with no contract change.
 *
 * MOR-2148 registers `segmentline`, the amber-LCD instrument family, as
 * tokens + stylesheet + manifest only — `renderers: {}`, which
 * `resolveRenderer` (`./contract.ts`) falls back on safely. Renderers are
 * MOR-2149's job.
 *
 * `layoutCompatibility` declares `peer-split: true` and `desktop-v2:
 * false`. Activation matches the resolved `SkinId`, not a
 * `presentation/layouts/` manifest id: `App.svelte` calls
 * `designLanguageActivation(language, skinId)` (`../workspace/activation.ts`),
 * whose parameter is merely *named* `layoutId`. `peer-split` is a
 * registered `SkinId` with a loadable shell (MOR-2155), but `resolveSkinId`
 * (`../../skins/registry.ts`) has no branch that returns it and no
 * `presentation/layouts/` manifest names it (MOR-2151), so segmentline
 * cannot activate in production yet — that routing is later in MOR-1162's
 * delivery order (`docs/plans/2026-09-01-segmentline-peer-split.md` §7:
 * MOR-2151, MOR-2152), not an oversight here. `unified-instrument` and
 * `panadapter-first`, the handoff's other two proposed directions, are
 * named nowhere in this repository either — no `SkinId`, no layout
 * manifest — so they are absent from this list too. `desktop-v2: false`
 * is kept because segmentline's fixed-native glass and desktop-v2's fluid
 * chrome are a real, current incompatibility.
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
import { SEGMENTLINE_TOKENS } from './segmentline/tokens';

export const studioline: DesignLanguageManifest = {
  id: 'studioline',
  displayName: 'Studioline',
  tokens: STUDIOLINE_TOKENS,
  // Holds all three density steps without collision (MOR-977 §4.2.3).
  density: { kind: 'clamped', supported: ['comfortable', 'compact', 'dense'] },
  // The mirror of fieldline's declaration below: two borderless channel
  // strips sharing one optical margin is the natural dual-receiver form
  // (MOR-977 §4.2.2), so the reference language says so as a manifest fact.
  layoutCompatibility: [
    { layoutId: 'dual-receiver-cockpit', compatible: true },
    { layoutId: 'desktop-v2', compatible: true },
  ],
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
  layoutCompatibility: [
    {
      layoutId: 'dual-receiver-cockpit',
      compatible: false,
      reason: 'fieldline cannot serve as the desktop dual-receiver default at 0.6 relative density (MOR-977 §4.4).',
    },
    { layoutId: 'desktop-v2', compatible: true },
  ],
  renderers: {
    frequencyDisplay: fieldlineFrequency,
    meters: fieldlineMeter,
    stateFeedback: fieldlineStateFeedback,
  },
};

export const segmentline: DesignLanguageManifest = {
  id: 'segmentline',
  displayName: 'Segmentline',
  tokens: SEGMENTLINE_TOKENS,
  // Clamped out at dense — the outlined cells collide with the 7px meter
  // pitch (tokens.ts `meters`).
  density: { kind: 'clamped', supported: ['comfortable', 'compact'] },
  layoutCompatibility: [
    { layoutId: 'peer-split', compatible: true },
    {
      layoutId: 'desktop-v2',
      compatible: false,
      reason: 'segmentline assumes a fixed-native instrument glass; desktop-v2 is fluid chrome.',
    },
  ],
  // MOR-2149 fills these three slots; `resolveRenderer` falls back to a
  // safe no-op for an unfilled slot in the meantime.
  renderers: {},
};

registerDesignLanguage(studioline);
registerDesignLanguage(fieldline);
registerDesignLanguage(segmentline);
