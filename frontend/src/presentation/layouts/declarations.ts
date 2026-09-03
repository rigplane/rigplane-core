/**
 * The one real registration proof MOR-1066 acceptance requires: the
 * existing `sdr-test` skin, which already mounts the two live semantic
 * zones (MOR-1065 VFO + RX/TX reference vertical), registers as a v1
 * layout manifest with no change to `skins/sdr-test/SdrTestSkin.svelte` or
 * `components-v2/wiring/SemanticRadioSurfaces.svelte`. MOR-1092/93/94
 * migrate the remaining skins the same way, purely additively.
 */
import { registerLayout, type LayoutManifest } from './contract';
// MOR-1067 registration — see that file. Re-exported rather than imported for
// its side effect alone: this barrel is the ONLY thing that wires the cockpit
// into the app, and a named re-export gives every test a real binding to
// assert against, so dropping this line cannot pass unnoticed.
export { dualReceiverCockpitLayout } from './dual-receiver-cockpit';

export const sdrTestLayout: LayoutManifest = {
  schemaVersion: 1,
  id: 'sdr-test',
  displayName: 'SDR Test',
  loader: () => import('../../skins/sdr-test/SdrTestSkin.svelte'),
  // MOR-1346: `meters` is its own zone, never merged into another one, so a
  // persisted `visibleSurfaces` entry recorded for a zone before `meters`
  // joined it cannot silently hide it (`resolveZone`'s allow-list
  // intersection would otherwise treat an unlisted new member as hidden).
  // Declaring it is what lets RadioLayout.svelte's existing `semanticMeters`
  // gate (`declared.has('meters')`) retire `<MetersDockPanel>` here too — the
  // same S5/MOR-1341 mechanism `desktop-v2` already uses, not a second one.
  // Not `required`: the semantic surface self-gates on `view.meters`.
  //
  // MOR-2231 (step 1, batch 1): `vfo` and `rxTx` split out of the former
  // single `main` zone into `receiver-deck` and `rx-tx` — the ids
  // `desktop-declarations.ts` already uses — so this face names one host per
  // surface and `SemanticRadioSurfaces` can build a real element for each
  // (its `regions` prop, passed from `RadioLayout.svelte`). Every zone here is
  // now one-surface, the shape `desktop-v2` has used since MOR-1266.
  //
  // MOR-2231 (step 1, batch 2): `filter`, `rfFrontEnd`, `band`, `antenna` and
  // `ritXitScan` join, under the ids `desktop-declarations.ts` already uses.
  // Each already mounted here BARE, through the single composition's `zoned()`
  // calls in `SemanticRadioSurfaces.svelte` (whose `allowBare` defaults true),
  // so declaring the zone gives it a `data-zone-id` host. Declaring it ALSO
  // activates the MOR-1364 suppression channel on this face: `LeftSidebar`'s
  // RF FRONT END, MODE, FILTER, RIT / XIT, ANTENNA and SCAN panels and the
  // settings modal's `desktop-rf` and `desktop-rit` sections stop rendering,
  // and both `BandSelector` mounts drop their HAM half through
  // `hamBands={!declared.has('band')}`. That retirement is the point, not a
  // side effect — the same move MOR-1366/1367 made for `desktop-v2`, closing a
  // double presentation that was live on this face.
  //
  // MOR-2231 (step 1, batch 3): `rxAudio`, `dsp`, `cwKeyer` and `txAux` join,
  // under the ids `desktop-declarations.ts` already uses. Each already mounted
  // here BARE, through the single composition's `zoned()` calls in
  // `SemanticRadioSurfaces.svelte` (all four take the default `allowBare`), so
  // declaring the zone gives it a `data-zone-id` host.
  //
  // THREE of the four also activate the MOR-1364 suppression channel on this
  // face, and that retirement is the point rather than a side effect:
  // `LeftSidebar`'s RX AUDIO, AGC, DSP and CW panels, `RightSidebar`'s RX
  // AUDIO, DSP and CW panels, and the settings modal's `desktop-dsp`,
  // `desktop-agc` and `desktop-cw` sections stop rendering. `AgcPanel` retires
  // with `dsp` rather than on a zone of its own, because `DspSurface` owns the
  // AGC leaf (5A/MOR-1290) — the same move MOR-1368 (S9) made for `desktop-v2`.
  //
  // `txAux` is the EXCEPTION, and a sharper one than batch 2's `band`: `band`
  // at least flips a prop, while no `declared.has('txAux')` predicate exists
  // anywhere, so this declaration retires nothing at all and only places the
  // surface. The sidebars' `<TxPanel>` is a different channel (`hideTxPanel`,
  // which follows the semantic DECK for R9) and batch 1's `vfo` zone already
  // suppressed it here.
  //
  // MOR-2231 (step 1, batch 4): `scopeDisplay` and `scopeControls` join, under
  // the ids `desktop-declarations.ts` already uses. That completes the
  // fourteen — every name in `SEMANTIC_SURFACE_NAMES` (`contract.ts`) now has
  // a zone here. Both already mounted BARE through the single composition's
  // `zoned()` calls in `SemanticRadioSurfaces.svelte` (both take the default
  // `allowBare` on that path), so declaring the zone gives each a
  // `data-zone-id` host.
  //
  // The two differ in what else the declaration does, and NEITHER matches the
  // batch-3 shape:
  //
  //   `scopeDisplay` retires ONE host, and it is the first on this face that
  //   is neither a sidebar panel nor a settings-modal section: `StatusBar`'s
  //   scope indicator, `{#if hasAnyScope() && !declared.has('scopeDisplay')}`.
  //   The twin is capability-gated on `hasAnyScope()`, so a radio with neither
  //   a hardware scope nor an audio-FFT source has no indicator to retire.
  //
  //   `scopeControls` retires NO host — the `band` shape rather than the
  //   `txAux` one, since a predicate does exist. Its only consumer is a PROP:
  //   `RadioLayout.svelte` forwards `hideScopeControls={declared.has(
  //   'scopeControls')}` to `SpectrumPanel`, which keeps mounting and drops
  //   the toolbar's twelve fact-backed `scopeControls.*` leaves (the eight
  //   client-side view options are never gated on it, S10 category (b)). The
  //   host is the CENTRE column's legacy spectrum panel, inside
  //   `{#if hasSpectrum()}` — a different mechanism from the sidebars'
  //   `drag.order.includes(...)` mount gates.
  //
  // None is `required`, matching `desktop-v2`: each surface self-gates on its
  // own `view.*` group, so a radio whose evidence gate declined the group must
  // still resolve this layout.
  zones: [
    { id: 'receiver-deck', surfaces: ['vfo'] },
    { id: 'rx-tx', surfaces: ['rxTx'] },
    { id: 'meters', surfaces: ['meters'] },
    { id: 'filter', surfaces: ['filter'] },
    { id: 'rf-front-end', surfaces: ['rfFrontEnd'] },
    { id: 'band', surfaces: ['band'] },
    { id: 'antenna', surfaces: ['antenna'] },
    { id: 'rit-xit-scan', surfaces: ['ritXitScan'] },
    { id: 'rx-audio', surfaces: ['rxAudio'] },
    { id: 'dsp', surfaces: ['dsp'] },
    { id: 'cw-keyer', surfaces: ['cwKeyer'] },
    { id: 'tx-aux', surfaces: ['txAux'] },
    { id: 'scope-display', surfaces: ['scopeDisplay'] },
    { id: 'scope-controls', surfaces: ['scopeControls'] },
  ],
  compatibleTopologies: ['1/single', '1/ab', '2/ab_shared', '2/main_sub'],
  requiredSemanticSurfaces: ['vfo', 'rxTx'],
  stageSizing: { mode: 'fluid', responsiveBreakpoints: [] },
  fallbackLayoutId: null,
};

registerLayout(sdrTestLayout);

export { lcdCockpitLayout, lcdScopeLayout } from './lcd-declarations';
export { mobileLayout } from './mobile-declarations';
// MOR-1266 registration — see that file. Re-exported for the same reason as
// dualReceiverCockpitLayout above: this barrel is the ONLY thing that wires
// desktop-v2's manifest into the app, and a named re-export gives every test
// a real binding to assert against (the M7 lesson — a bare side-effect
// import would let this line be dropped without any test noticing).
export { desktopV2Layout } from './desktop-declarations';
// MOR-2151 registration — see that file. Re-exported for the same reason as
// the other family imports above: this barrel is the ONLY thing that wires
// peer-split's manifest into the app, and a named re-export gives every test
// a real binding to assert against, so dropping this line cannot pass
// unnoticed.
export { peerSplitLayout } from './segmentline-declarations';
