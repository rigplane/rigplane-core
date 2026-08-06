/**
 * MOR-1082 — the adoption pins for the workspace's own COARSE fields:
 * `density`, `visibleSurfaces`, `zoneOrder` and `pinnedCommands`.
 *
 * Scope, per the MOR-1289 owner decision (which post-dates the ticket text):
 * the fine-grained legacy panel keys (`rigplane:panel-collapsed`,
 * `panel-order`, `right-panel-order`, `lcd-*`, `vfo-theme`) have NO v1 home
 * and stay RETAIN-OUTSIDE with their current owners — nothing here migrates
 * them, derives them, or writes them. What is adopted is the four fields the
 * MOR-1076 freeze already put in the v1 contract and MOR-1079 already gave
 * setters.
 *
 * Everything below runs against the REAL registered manifests (through the
 * `declarations` barrels, never a local copy of a manifest), so a manifest
 * edit that breaks an adoption invariant fails HERE rather than at the
 * cutover.
 *
 * Pool: `fast`. No `vi.mock`, no `vi.stubGlobal`, no `vi.resetModules()` —
 * the resolution seam is pure and the store takes its storage as an argument,
 * so none of the order-dependent shapes that force the workspace purity
 * suites into the isolated pool (MOR-1272) appear here. `afterEach` returns
 * the shared store state to defaults for sibling files under `isolate: false`.
 */
import { afterEach, describe, expect, it } from 'vitest';
import { readFileSync, readdirSync } from 'node:fs';
import type { DensityLevel, DesignLanguageManifest } from '../../languages/contract';
import type { LayoutManifest, SemanticSurfaceName } from '../../layouts/contract';
import { fieldline, studioline } from '../../languages/declarations';
import {
  desktopV2Layout, dualReceiverCockpitLayout, lcdCockpitLayout, mobileLayout, sdrTestLayout,
} from '../../layouts/declarations';
import { DEFAULT_WORKSPACE, readWorkspace, type WorkspaceV1 } from '../contract';
import { classifyLegacyKey } from '../legacy-readers';
import {
  compositionSurfaces, densityActivation, resolveSurfacePlan,
} from '../resolution';
import {
  getWorkspace, initWorkspaceStore, setDensity, setPinnedCommands,
} from '../store.svelte';

/** A validated workspace carrying `fields` — the ONLY way one is built here:
 *  the resolution seam consumes an already-validated object and must not
 *  re-validate (that is the store's job, MOR-1077/1079). */
function workspace(fields: Record<string, unknown>): WorkspaceV1 {
  return readWorkspace({ version: 1, ...fields }).workspace;
}

function plan(manifest: LayoutManifest, fields: Record<string, unknown> = {}) {
  return resolveSurfacePlan(manifest, workspace(fields));
}

afterEach(() => {
  initWorkspaceStore(null);
});

// ── 1. Density: DL default + workspace override, clamped by the ACTIVE
//       language at RESOLUTION time (MOR-1076 decision 4, hybrid arm) ───────

describe('MOR-1082 — density resolves against the ACTIVE design language', () => {
  it('honours the operator override where the language supports it', () => {
    expect(densityActivation(studioline, 'dual-receiver-cockpit', 'dense')).toBe('dense');
    expect(densityActivation(studioline, 'dual-receiver-cockpit', 'compact')).toBe('compact');
  });

  it('is inert wherever the language itself is not active (no cutover here)', () => {
    // Same gate as `[data-design-language]` (MOR-1081/MOR-1278): density can
    // never activate on a layout the language has not declared, so every
    // shipped v2 skin keeps its current, density-free presentation.
    for (const layoutId of ['desktop-v2', 'lcd-cockpit', 'lcd-scope', 'mobile', 'sdr-test']) {
      expect(densityActivation(studioline, layoutId, 'dense')).toBeNull();
    }
    expect(densityActivation(fieldline, 'dual-receiver-cockpit', 'compact')).toBeNull();
    expect(densityActivation(undefined, 'dual-receiver-cockpit', 'compact')).toBeNull();
  });

  /**
   * The pin the ticket names: FIELDLINE HAS NO `dense`. The clamp applied is
   * the ACTIVE language's own `DensityClamp`, read live off its manifest —
   * not the `WORKSPACE_DENSITY_CLAMP` mirror, and not the clamp of whatever
   * language the workspace happens to have stored.
   *
   * `fieldline` declares `dual-receiver-cockpit` INCOMPATIBLE, so it is never
   * active on any registered layout today and the clamp cannot be exercised
   * through it directly. This borrows its real, frozen clamp onto a manifest
   * that IS active somewhere — a language with fieldline's density policy.
   */
  const activeFieldline: DesignLanguageManifest = {
    ...fieldline,
    layoutCompatibility: [{ layoutId: 'dual-receiver-cockpit', compatible: true }],
  };

  it('clamps an out-of-clamp override down to the active language default', () => {
    expect(fieldline.density).toEqual({ kind: 'clamped', supported: ['comfortable', 'compact'] });
    expect(densityActivation(activeFieldline, 'dual-receiver-cockpit', 'dense')).toBe('comfortable');
    expect(densityActivation(activeFieldline, 'dual-receiver-cockpit', 'compact')).toBe('compact');
  });

  it('clamps against the ACTIVE language even when the stored one allows the value', () => {
    // The workspace stores `studioline` + `dense`; the language actually
    // ACTIVE on screen is a fieldline-clamped one. Kills: resolving the clamp
    // from `workspace.designLanguage` (or from the pinned mirror) instead of
    // from the manifest handed in.
    const stored = workspace({ designLanguage: 'studioline', density: 'dense' });
    expect(stored.density).toBe('dense');
    expect(densityActivation(activeFieldline, 'dual-receiver-cockpit', stored.density)).toBe('comfortable');
  });

  it('passes an override straight through for a language that declares no clamp', () => {
    const free: DesignLanguageManifest = {
      ...studioline,
      density: { kind: 'not-applicable' },
    };
    expect(densityActivation(free, 'dual-receiver-cockpit', 'dense')).toBe('dense');
  });

  it('refuses an out-of-clamp density at the STORE too, so nothing unclamped persists', () => {
    initWorkspaceStore(null);
    setDensity('dense');
    expect(getWorkspace().density).toBe('dense'); // studioline is stored → allowed
    expect(DEFAULT_WORKSPACE.density).toBe('comfortable');
    // …and a fieldline workspace cannot even hold `dense` (MOR-1077 pickDensity).
    expect(workspace({ designLanguage: 'fieldline', density: 'dense' }).density).toBe('comfortable');
  });
});

// ── 2. visibleSurfaces / zoneOrder: the workspace may only FURTHER hide and
//       reorder WITHIN a zone the active layout already declares ────────────

describe('MOR-1082 — the surface plan starts from what the manifest declares', () => {
  it('is the manifest, verbatim, for a workspace that expresses no preference', () => {
    expect([...plan(dualReceiverCockpitLayout)]).toEqual([
      ['primary-vfo', ['vfo']], ['secondary-vfo', ['vfo']], ['global', ['vfo']], ['rx-tx', ['rxTx']],
      // MOR-1336 (S4): the cockpit now declares a tx-aux zone too.
      ['tx-aux', ['txAux']],
    ]);
    expect([...plan(sdrTestLayout)]).toEqual([['main', ['vfo', 'rxTx']]]);
  });

  it('applies ONE contract to every layout family — mobile does not fork', () => {
    // Kills: a mobile-specific preference path. The same function, the same
    // workspace object, and every registered family answers in its own zone
    // vocabulary with no special case.
    for (const manifest of [mobileLayout, lcdCockpitLayout, desktopV2Layout]) {
      expect([...plan(manifest).keys()]).toEqual(manifest.zones.map((zone) => zone.id));
      for (const zone of manifest.zones) {
        expect(plan(manifest).get(zone.id)).toEqual([...zone.surfaces]);
      }
    }
    // The same preference, expressed once, lands identically on mobile and on
    // the LCD — including the REFUSAL: both mount their required surfaces in a
    // single zone, so hiding one there is refused in exactly the same way.
    expect(plan(mobileLayout, { zoneOrder: { 'portrait-deck': ['rxTx'] } }).get('portrait-deck'))
      .toEqual(['rxTx', 'vfo']);
    expect(plan(lcdCockpitLayout, { zoneOrder: { 'control-column': ['rxTx'] } }).get('control-column'))
      .toEqual(['rxTx', 'vfo']);
    expect(plan(mobileLayout, { visibleSurfaces: { 'portrait-deck': ['rxTx'] } }).get('portrait-deck'))
      .toEqual(['vfo', 'rxTx']);
    expect(plan(lcdCockpitLayout, { visibleSurfaces: { 'control-column': ['rxTx'] } }).get('control-column'))
      .toEqual(['vfo', 'rxTx']);
  });

  it('hides a surface the operator switched off in that zone', () => {
    const hidden = plan(dualReceiverCockpitLayout, { visibleSurfaces: { 'secondary-vfo': [] } });
    expect(hidden.get('secondary-vfo')).toEqual([]);
    // …and touches no other zone: `vfo` is still mounted where it was.
    expect(hidden.get('primary-vfo')).toEqual(['vfo']);
    expect(hidden.get('global')).toEqual(['vfo']);
  });

  it('cannot FORCE-SHOW a surface into a zone that does not declare it', () => {
    // The manifest is the ceiling. Kills: treating `visibleSurfaces` as the
    // list of what to render rather than as an allow-list over the declared
    // set — which would mount `vfo` in the RX/TX zone on the operator's say-so.
    const forced = plan(dualReceiverCockpitLayout, {
      visibleSurfaces: { 'rx-tx': ['vfo', 'rxTx'] },
    });
    expect(forced.get('rx-tx')).toEqual(['rxTx']);
  });

  it('cannot move a surface ACROSS zones through the plan either', () => {
    // The contract already refuses a duplicate across zones on READ; the
    // adoption must not open a second door. `txAux` is a real surface id that
    // no shipped zone declares, so naming it is the purest cross-zone probe.
    const moved = plan(sdrTestLayout, {
      visibleSurfaces: { main: ['vfo', 'rxTx', 'txAux'] },
      zoneOrder: { main: ['txAux', 'rxTx', 'vfo'] },
    });
    expect(moved.get('main')).toEqual(['rxTx', 'vfo']);
    expect(moved.get('main')).not.toContain('txAux');
  });

  it('reorders within a zone, and normalizes a partial or padded order', () => {
    expect(plan(sdrTestLayout, { zoneOrder: { main: ['rxTx', 'vfo'] } }).get('main'))
      .toEqual(['rxTx', 'vfo']);
    // Partial: what the operator named leads, the rest follows in DECLARED order.
    expect(plan(sdrTestLayout, { zoneOrder: { main: ['rxTx'] } }).get('main'))
      .toEqual(['rxTx', 'vfo']);
    // An order naming nothing the zone declares is inert, never destructive.
    expect(plan(sdrTestLayout, { zoneOrder: { main: ['meters'] } }).get('main'))
      .toEqual(['vfo', 'rxTx']);
  });

  it('refuses a hide that would leave a REQUIRED surface mounted by no zone', () => {
    // `requiredSemanticSurfaces` is the layout's own invariant — the validator
    // already refuses a manifest whose zones do not mount one. A persisted
    // preference may not break what a manifest may not declare. For `rxTx`
    // this is also the TX-safety arm: the RX/TX surface carries the ONLY
    // unkey affordance, and exactly one zone in every shipped layout mounts it.
    const stripped = plan(dualReceiverCockpitLayout, { visibleSurfaces: { 'rx-tx': [] } });
    expect(stripped.get('rx-tx')).toEqual(['rxTx']);

    // Same rule, the vfo arm: hiding it in ONE of the three zones that mount
    // it is honoured, because two still do.
    const partial = plan(dualReceiverCockpitLayout, { visibleSurfaces: { 'secondary-vfo': [] } });
    expect(partial.get('secondary-vfo')).toEqual([]);

    // …but hiding it in ALL of them is refused, in every one.
    const total = plan(dualReceiverCockpitLayout, {
      visibleSurfaces: { 'primary-vfo': [], 'secondary-vfo': [], global: [] },
    });
    expect(total.get('primary-vfo')).toEqual(['vfo']);
    expect(total.get('secondary-vfo')).toEqual(['vfo']);
    expect(total.get('global')).toEqual(['vfo']);
    // The rx-tx zone was never touched by that write.
    expect(total.get('rx-tx')).toEqual(['rxTx']);
  });

  it('never re-validates: an invalid zone or id is already gone before it arrives', () => {
    // MOR-1082 acceptance "invalid order/size is normalized" is the STORE's
    // behaviour (MOR-1077), and the plan must not duplicate it. Proof that the
    // validation happened upstream: the invalid names are absent from the
    // validated object the plan is handed, and rejections were reported.
    const read = readWorkspace({
      version: 1,
      visibleSurfaces: { 'not-a-zone': ['vfo'], 'rx-tx': ['not-a-surface', 'rxTx'] },
      zoneOrder: 'not-an-object',
    });
    expect(read.outcome).toBe('repaired');
    expect(read.workspace.visibleSurfaces).toEqual({ 'rx-tx': ['rxTx'] });
    expect(read.workspace.zoneOrder).toEqual({});
    expect(read.rejections.map((r) => r.reason)).toEqual(
      expect.arrayContaining(['unknown-id', 'malformed']),
    );
    expect([...resolveSurfacePlan(dualReceiverCockpitLayout, read.workspace)]).toEqual([
      ['primary-vfo', ['vfo']], ['secondary-vfo', ['vfo']], ['global', ['vfo']], ['rx-tx', ['rxTx']],
      // MOR-1336 (S4): the cockpit now declares a tx-aux zone too.
      ['tx-aux', ['txAux']],
    ]);
  });
});

// ── 3. The single composition's view of the plan ───────────────────────────

describe('MOR-1082 — the single-composition order comes from the same plan', () => {
  const FALLBACK: readonly SemanticSurfaceName[] = ['vfo', 'rxTx'];

  it('falls back to the composed order when no plan is resolved', () => {
    // No App ancestor (a standalone component mount), or a layout id that
    // resolves to no manifest: the vertical renders exactly as it does today.
    expect(compositionSurfaces(null, FALLBACK)).toEqual(['vfo', 'rxTx']);
  });

  it('flattens the plan in zone-declaration order, deduped', () => {
    expect(compositionSurfaces(plan(sdrTestLayout), FALLBACK)).toEqual(['vfo', 'rxTx']);
    // desktop-v2 spreads the same two surfaces over two zones, plus its own
    // MOR-1336 (S4) tx-aux zone, MOR-1341 (S5) meters zone, MOR-1365 (S6a)
    // scope-display zone, the MOR-1366 (S7) filter/rf-front-end zones, the
    // three MOR-1367 (S8) zones and the MOR-1368 (S9) rx-audio/dsp/cw-keyer
    // zones — flattening never drops a later zone.
    //
    // §1.5 / MOR-1339 discipline: this list GROWS, but `singleOrder`'s `{#each}`
    // in `SemanticRadioSurfaces.svelte` gains NO branch for any of these
    // members — each renders exactly once, through `zoned()`. The
    // "presents … exactly ONCE" pin in
    // `components-v2/layout/__tests__/semantic-desktop-migration.component.test.ts`
    // is the counterpart that would catch a double mount.
    expect(compositionSurfaces(plan(desktopV2Layout), FALLBACK))
      .toEqual(['vfo', 'rxTx', 'txAux', 'meters', 'scopeDisplay', 'filter', 'rfFrontEnd', 'band', 'antenna', 'ritXitScan', 'rxAudio', 'dsp', 'cwKeyer']);
    expect(compositionSurfaces(plan(sdrTestLayout, { zoneOrder: { main: ['rxTx', 'vfo'] } }), FALLBACK))
      .toEqual(['rxTx', 'vfo']);
  });

  it('never composes to nothing — an empty plan yields the fallback', () => {
    // Belt and braces for the unkey path: whatever a future manifest or a
    // stored preference does, the single composition cannot resolve to a
    // vertical with no surfaces at all.
    expect(compositionSurfaces(new Map(), FALLBACK)).toEqual(['vfo', 'rxTx']);
  });
});

// ── 4. pinnedCommands: the store field is authoritative and validated, and
//       nothing else is wired to it yet (MOR-1076 decision 7) ──────────────

describe('MOR-1082 — pinnedCommands are validated command-bus intent names', () => {
  it('keeps intent names, drops malformed entries and duplicates', () => {
    initWorkspaceStore(null);
    setPinnedCommands(['set_compressor', 'set_compressor', 'Set Monitor', './panel.svelte', 'atu_tune']);
    expect(getWorkspace().pinnedCommands).toEqual(['set_compressor', 'atu_tune']);
  });

  it('refuses a forbidden class outright rather than persisting it', () => {
    initWorkspaceStore(null);
    setPinnedCommands(['tx_power', 'ptt_on', 'session_token', 'set_filter']);
    expect(getWorkspace().pinnedCommands).toEqual(['set_filter']);
  });

  it('is consumed by no production module yet — adoption is the field, not a UI', () => {
    // States the stance as a FACT instead of a claim: if a panel starts
    // reading pinned commands, this pin fails and the decision gets revisited
    // deliberately rather than by accident.
    const offenders = productionSources('src')
      .filter((path) => !path.startsWith('src/presentation/workspace/'))
      .filter((path) => stripComments(readFileSync(path, 'utf8')).includes('pinnedCommands'));
    expect(offenders).toEqual([]);
  });
});

// ── 5. The legacy stance, re-affirmed for the fine-grained panel keys ──────

describe('MOR-1082 — the retain-outside panel keys keep their owners', () => {
  /** MOR-1289: 0/13 surface-id overlap with the v1 schema — no v1 home, so
   *  they are neither migrated nor derived. This pin is the other direction
   *  from MOR-1081's F2 scan: those keys must NOT appear in the workspace. */
  const RETAINED_OUTSIDE = [
    'rigplane:panel-collapsed', 'rigplane:panel-order', 'rigplane:right-panel-order',
    'rigplane:vfo-theme',
  ];

  it('routes every one of them retain-outside, and nowhere else in the workspace', () => {
    for (const key of RETAINED_OUTSIDE) expect(classifyLegacyKey(key)).toBe('retain-outside');
    // `legacy-readers.ts` is the getItem-only routing table (MOR-1078) and the
    // one legitimate place a retained key is NAMED. Anywhere else in the
    // workspace zone would mean adoption started reaching for them.
    const offenders = productionSources('src/presentation/workspace')
      .filter((path) => path !== 'src/presentation/workspace/legacy-readers.ts')
      .filter((path) => RETAINED_OUTSIDE.some((key) => stripComments(readFileSync(path, 'utf8')).includes(key)));
    expect(offenders).toEqual([]);
  });

  it('the resolution seam touches no storage of its own', () => {
    const source = stripComments(readFileSync('src/presentation/workspace/resolution.ts', 'utf8'));
    for (const banned of ['localStorage', 'sessionStorage', 'document', 'window']) {
      expect(source).not.toContain(banned);
    }
  });
});

function productionSources(dir: string, out: string[] = []): string[] {
  for (const entry of readdirSync(dir, { withFileTypes: true })) {
    const path = `${dir}/${entry.name}`;
    if (entry.isDirectory()) {
      if (entry.name !== '__tests__') productionSources(path, out);
    } else if (/\.(ts|svelte)$/.test(entry.name) && !entry.name.endsWith('.test.ts')) {
      out.push(path);
    }
  }
  return out;
}

/** Prose about a prohibition must not read as a violation of it. */
function stripComments(source: string): string {
  return source.replace(/\/\*[\s\S]*?\*\//g, '').replace(/(^|[^:])\/\/.*$/gm, '$1');
}

/** Guards the two scans above against silently scanning nothing. */
describe('the source scans still see the tree', () => {
  it('finds production sources under both roots', () => {
    expect(productionSources('src').length).toBeGreaterThan(200);
    expect(productionSources('src/presentation/workspace').length).toBeGreaterThan(3);
  });
});

/** Type-only: `DensityLevel` is referenced so a rename cannot silently pass. */
const DENSITY_LEVELS: readonly DensityLevel[] = ['comfortable', 'compact', 'dense'];
describe('the density id space is the language contract\'s', () => {
  it('covers every level the pinned clamps can name', () => {
    expect(DENSITY_LEVELS).toContain(DEFAULT_WORKSPACE.density);
  });
});
