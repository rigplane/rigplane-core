/**
 * MOR-1561 (C7) — Filter/PBT/IF-shift intent family conformance walk, over
 * the same profile-parameterized harness MOR-1428/MOR-1555 established
 * (`./conformance/harness.ts`, `./conformance/profiles.ts`).
 *
 * Family (per `waived.ts`'s MOR-1561 tag, 5 intents): `set_filter_width`,
 * `set_filter_shape`, `set_if_shift`, `set_pbt_inner`, `set_pbt_outer` — all
 * dispatched from `makeFilterHandlers()` in `panel-commands.ts`.
 *
 * UNLIKE MOR-1560's DSP walk (9/9 uniform refusals), `set_filter_width` has
 * FOUR distinct dispatch sites (`onFilterWidthChange`, `onFilterWidthCommit`,
 * `onFilterPresetChange`, `onFilterDefaults`). Before MOR-1576,
 * `onFilterWidthChange`/`onFilterDefaults` gated on
 * `knownActiveReceiver('filterWidth')` — using the WRITTEN field as a
 * receiver-identity proxy — while `onFilterPresetChange` gated on
 * `knownActiveReceiver('filter')` instead (`panel-commands.ts`'s own
 * choice — it only needs to know the CURRENT active filter slot to decide
 * whether a `set_filter` bracket is needed, not whether `filterWidth` itself
 * was ever confirmed). On this fixture `main.filterWidth` is unobserved but
 * `main.filter` IS observed, so the two gates disagreed: the slider and
 * "restore defaults" paths silently refused while the settings-modal preset
 * path dispatched fine for the identical write. MOR-1576 (verifier analysis,
 * PR #2481) relaxed the three strict sites to the preset path's gating —
 * receiver identity via `knownActiveReceiver('filter')` plus mode/dataMode
 * to resolve the quantization rule, not a confirmed prior `filterWidth`
 * reading — so all three now dispatch identically on this fixture (see the
 * "unified call-site gating" cases below, plus the discrimination case
 * proving they still correctly refuse when `main.filter` itself is
 * unobserved too). `set_filter_shape`/`set_if_shift`/`set_pbt_inner`/
 * `set_pbt_outer` have no such split — each refuses through its own single
 * gate, named per case below and verified against the fixture's own
 * `fieldStatus`/`capabilities.controls` data (never invented).
 *
 * RED-FIRST EVIDENCE (MOR-1561 build process, not part of this diff): the
 * `onFilterPresetChange` case below was first authored as
 * `expectFrames(() => { makeFilterHandlers().onFilterPresetChange(1, 3000);
 * vi.advanceTimersByTime(200); }, [['set_filter_width', { width: 9999,
 * receiver: 1 }]])` — deliberately wrong width/receiver. `vitest run` on
 * that version failed with `expected [ [ 'set_filter_width', { width: 3000,
 * receiver: 0 } ] ] to deeply equal [ [ 'set_filter_width', { width: 9999,
 * receiver: 1 } ] ]` (RED — the real dispatch is 3000/receiver 0, not the
 * fabricated 9999/1). Replacing the claim with the real fixture-derived
 * values (`IC7300_STATE.main.filter === 1`, `filterConfig.USB.defaults[0]
 * === 3000`, already on-grid so `quantizeFilterWidthToRule` is a no-op)
 * turned it GREEN.
 *
 * DISCRIMINATION EVIDENCE (MOR-1561 build process, not part of this diff):
 * to prove the `set_pbt_inner` refusal below isn't vacuous, its gate was
 * scratch-flipped locally — `h.state.fieldStatus['main.pbtInner']`
 * temporarily set to `{ availability: 'available', observed: true, ... }`
 * before calling `onPbtInnerChange` — and the refusal assertion then FAILED
 * (the handler dispatched `set_pbt_inner` with the fixture's own raw-mapped
 * value), confirming the assertion genuinely depends on that one
 * field-status gate. The scratch edit was staged (`git add`) in its clean
 * form first, then reverted with `git checkout --` (not `git stash`) after
 * observing the failure, restoring the green state before this file was
 * committed.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import {
  expectFrames,
  expectRefusal,
  fixtureCaps,
  fixtureState,
  h,
} from './conformance/harness';
import { PROFILES } from './conformance/profiles';
import { makeFilterHandlers } from '../../commands/panel-commands';
import { resetCommandLifecycle } from '$lib/stores/commands.svelte';
import { pbtRangeFromCaps } from '$lib/radio/filter-controls';

const profile = PROFILES.ic7300;
const IC7300_STATE = profile.state;
const IC7300_CAPABILITIES = profile.caps;

describe('IC-7300 fixture — filter/PBT family conformance (MOR-1561)', () => {
  beforeEach(() => {
    h.state = fixtureState(profile);
    h.caps = fixtureCaps(profile);
    h.sendCommand.mockClear();
  });

  afterEach(() => {
    resetCommandLifecycle();
  });

  describe('set_filter_width — unified call-site gating on this profile (MOR-1576, see file header)', () => {
    it('onFilterWidthChange: DISPATCHES — relaxed to knownActiveReceiver(\'filter\') like onFilterPresetChange; main.filterWidth stays unobserved on this fixture but is no longer the gate', () => {
      expect(IC7300_CAPABILITIES.capabilities).toContain('filter_width');
      expect(IC7300_STATE.fieldStatus?.['main.filterWidth']?.observed).toBe(false);
      expect(IC7300_STATE.fieldStatus?.['main.filter']?.observed).toBe(true);
      // Fixture's own USB filter-width rule (rigs/ic7300.toml via
      // filterConfig.USB): 1800 Hz already sits on the declared 100 Hz-step
      // segment grid (600-3600), so quantizeFilterWidthToRule is a no-op.
      vi.useFakeTimers();
      try {
        expectFrames(() => {
          makeFilterHandlers().onFilterWidthChange(1800);
          vi.advanceTimersByTime(200);
        }, [['set_filter_width', { width: 1800, receiver: 0 }]]);
      } finally {
        vi.useRealTimers();
      }
    });

    it('onFilterDefaults: DISPATCHES — same relaxed knownActiveReceiver(\'filter\') gate as onFilterWidthChange', () => {
      expect(IC7300_STATE.fieldStatus?.['main.filterWidth']?.observed).toBe(false);
      expect(IC7300_STATE.fieldStatus?.['main.filter']?.observed).toBe(true);
      expect(IC7300_STATE.main!.filter).toBe(1);
      // filter=1 equals the fixture's own active filter, so slot 1 needs no
      // bracketing set_filter, while slots 2/3 do (bracket-and-restore).
      expectFrames(() => makeFilterHandlers().onFilterDefaults([3000, 2400, 1800]), [
        ['set_filter_width', { width: 3000, receiver: 0 }],
        ['set_filter', { filter: 2, receiver: 0 }],
        ['set_filter_width', { width: 2400, receiver: 0 }],
        ['set_filter', { filter: 3, receiver: 0 }],
        ['set_filter_width', { width: 1800, receiver: 0 }],
        ['set_filter', { filter: 1, receiver: 0 }],
      ]);
    });

    it('onFilterPresetChange: DISPATCHES — gated on knownActiveReceiver(\'filter\') (main.filter IS observed), not on filterWidth at all (RED-FIRST evidence in file header)', () => {
      expect(IC7300_STATE.fieldStatus?.['main.filter']?.observed).toBe(true);
      expect(IC7300_STATE.main!.filter).toBe(1);
      // Fixture's own USB filter-width rule (rigs/ic7300.toml via
      // filterConfig.USB) — 3000 Hz is its first declared default AND
      // already sits on the declared 100 Hz-step segment grid (600-3600),
      // so quantizeFilterWidthToRule is a no-op here, not a coincidence.
      expect(IC7300_CAPABILITIES.filterConfig?.USB?.defaults?.[0]).toBe(3000);
      vi.useFakeTimers();
      try {
        // filter=1 equals the fixture's own active filter (main.filter===1),
        // so no bracketing set_filter calls fire — exactly one frame.
        expectFrames(() => {
          makeFilterHandlers().onFilterPresetChange(1, 3000);
          vi.advanceTimersByTime(200);
        }, [['set_filter_width', { width: 3000, receiver: 0 }]]);
      } finally {
        vi.useRealTimers();
      }
    });

    it('discrimination: with main.filter ALSO unobserved, onFilterWidthChange/onFilterDefaults/onFilterPresetChange all still refuse — receiver identity is genuinely unknown, not just filterWidth', () => {
      expect(IC7300_STATE.fieldStatus?.['main.filter']?.observed).toBe(true);
      h.state = {
        ...fixtureState(profile),
        fieldStatus: {
          ...IC7300_STATE.fieldStatus,
          'main.filter': {
            ...IC7300_STATE.fieldStatus!['main.filter'],
            observed: false,
            availability: 'missing',
            freshness: 'unknown',
          },
        },
      };

      expectRefusal(() => makeFilterHandlers().onFilterWidthChange(1800));
      expectRefusal(() => makeFilterHandlers().onFilterDefaults([3000, 2400, 1800]));
      // Both onFilterWidthChange and onFilterPresetChange re-check receiver
      // identity BEFORE scheduling their 200ms debounce, so the refusal is
      // synchronous — no fake timers needed to observe it.
      expectRefusal(() => makeFilterHandlers().onFilterPresetChange(1, 3000));
    });
  });

  it('set_filter_shape: REFUSES — main.filterShape is unobserved (onFilterShapeChange is the only dispatch site)', () => {
    expect(IC7300_CAPABILITIES.capabilities).toContain('filter_shape');
    expect(IC7300_CAPABILITIES.controls?.filter_shape).toBeUndefined();
    expect(IC7300_STATE.fieldStatus?.['main.filterShape']?.observed).toBe(false);
    expectRefusal(() => makeFilterHandlers().onFilterShapeChange(IC7300_STATE.main!.filterShape as number));
  });

  it('set_if_shift: REFUSES — if_shift is not a declared capability on this profile, so onIfShiftChange never enters its if_shift branch at all; it falls through to the pbt branch, which itself refuses (main.pbtInner unobserved)', () => {
    expect(IC7300_CAPABILITIES.capabilities).not.toContain('if_shift');
    expect(IC7300_CAPABILITIES.capabilities).toContain('pbt');
    expect(IC7300_STATE.fieldStatus?.['main.pbtInner']?.observed).toBe(false);
    expectRefusal(() => makeFilterHandlers().onIfShiftChange(0));
  });

  describe('set_pbt_inner — boundary walk over the declared pbt range (discrimination case, see file header)', () => {
    const pbtRange = pbtRangeFromCaps(IC7300_CAPABILITIES)!;

    it('pbt_inner declares a usable range on this profile', () => {
      expect(IC7300_CAPABILITIES.controls?.pbt_inner).toEqual({
        raw_min: 0, raw_max: 255, raw_center: 128, display_min: -1200, display_max: 1200, display_unit: 'Hz',
      });
      expect(pbtRange).toEqual({ rawCenter: 128, displayMin: -1200, displayMax: 1200 });
    });

    for (const hz of [pbtRange.displayMin, 0, pbtRange.displayMax]) {
      it(`value=${hz} Hz (declared domain [${pbtRange.displayMin},${pbtRange.displayMax}]): REFUSES — main.pbtInner is unobserved`, () => {
        expect(IC7300_STATE.fieldStatus?.['main.pbtInner']?.observed).toBe(false);
        expectRefusal(() => makeFilterHandlers().onPbtInnerChange(hz));
      });
    }
  });

  describe('set_pbt_outer — boundary walk over the declared pbt range (same domain and gate shape as pbt_inner)', () => {
    const pbtRange = pbtRangeFromCaps(IC7300_CAPABILITIES)!;

    it('pbt_outer declares a usable range on this profile', () => {
      expect(IC7300_CAPABILITIES.controls?.pbt_outer).toEqual({
        raw_min: 0, raw_max: 255, raw_center: 128, display_min: -1200, display_max: 1200, display_unit: 'Hz',
      });
    });

    for (const hz of [pbtRange.displayMin, 0, pbtRange.displayMax]) {
      it(`value=${hz} Hz (declared domain [${pbtRange.displayMin},${pbtRange.displayMax}]): REFUSES — main.pbtOuter is unobserved`, () => {
        expect(IC7300_STATE.fieldStatus?.['main.pbtOuter']?.observed).toBe(false);
        expectRefusal(() => makeFilterHandlers().onPbtOuterChange(hz));
      });
    }
  });
});
