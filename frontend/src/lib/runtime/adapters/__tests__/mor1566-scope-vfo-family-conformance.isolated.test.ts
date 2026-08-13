/**
 * MOR-1566 (C12) — Scope-remainder + VFO-topology intent family conformance
 * walk, over the same profile-parameterized harness MOR-1428/MOR-1555
 * established (`./conformance/harness.ts`, `./conformance/profiles.ts`).
 *
 * Family (per `waived.ts`'s MOR-1566 tag, 12 intents): `set_scope_mode`,
 * `set_scope_edge`, `set_scope_dual`, `set_scope_during_tx`,
 * `set_scope_center_type`, `set_scope_vbw`, `set_scope_rbw`,
 * `switch_scope_receiver` — all from `makeScopeControlsHandlers()`
 * (`panel-commands.ts:1257-1321`); plus `set_dual_watch`,
 * `set_main_sub_tracking`, `quick_dualwatch`, `quick_split` — all from
 * `makeVfoHandlers()` (`panel-commands.ts:1075-1206`). All 12 are wired to
 * real UI (never dead controls): the 8 scope intents through
 * `SemanticRadioSurfaces.svelte`'s `SCOPE_CHOICE_INTENT`/`SCOPE_TOGGLE_INTENT`
 * tables (`:355-363`) into `ScopeControlsSurface.svelte`'s toolbar/popover;
 * the 4 VFO-topology intents through `RadioLayout.svelte`/
 * `MobileRadioLayout.svelte`'s dual-watch/tracking/split controls.
 *
 * NOT SKEWED TOWARD REFUSAL like C6/C7/C10/C11's walks: 7 of the 12
 * genuinely DISPATCH on the real IC-7300 bench fixture — every
 * `scopeControls.*` leaf this family reads is OBSERVED (`fieldStatus`) except
 * `rbw`, and the fixture's own current values (`mode=0`, `edge=1`,
 * `centerType=2`, `duringTx=true`, `vbwNarrow=false`) all fall inside the
 * handler's declared domain. 5 refuse, each an honest STRUCTURAL gate (never
 * an unobserved-field gate the way C6/C7/C10/C11 mostly were):
 * `set_scope_dual` and `switch_scope_receiver(receiver=1)` fail
 * `hasPhysicalSub` (`panel-commands.ts:1234-1237`: `caps.receivers===2 &&
 * dual_rx && state.sub` — this fixture has `receivers=1`, no `dual_rx`,
 * `state.sub=null`); `set_dual_watch`/`set_main_sub_tracking`/
 * `quick_dualwatch` all short-circuit on `context.caps.receivers < 2` before
 * ever reaching a capability or field-observation check; `set_scope_rbw` is
 * the ONE genuinely-unobserved field-status leaf in this family
 * (`fieldStatus['scopeControls.rbw'].observed === false`) — discrimination
 * case below proves that refusal is load-bearing, not vacuous.
 *
 * DECLARED-PARAM SHAPES, DATA-DRIVEN: `mode`/`edge`/`centerType`/`rbw`/
 * `receiver` domains are all read off the EXACT tables the production UI
 * itself renders from — `MODE_BUTTONS` (`components/spectrum/
 * spectrum-toolbar-logic.ts`) for mode, `CHOICES` (`semantic/
 * ScopeControlsSurface.svelte`'s module script) for edge/centerType/rbw/
 * receiver — imported below, never hand-copied, so a production domain edit
 * moves this walk's boundary values with it. NOT UNIFORMLY PINNED, though:
 * `mode`/`edge`/`centerType` are BEHAVIORALLY pinned — a full boundary-value
 * dispatch walk covers every declared value, so a narrower production domain
 * would turn one of those dispatch cases red. `rbw` and `receiver`'s upper
 * bound are only STRUCTURALLY pinned (`choiceDomain(...)` asserted to equal
 * the literal array) — `rbw` refuses on the field-observation gate for every
 * value in its domain, so its declared max (2) is never actually exercised
 * as a dispatch; `receiver`'s upper bound (1) refuses on `hasPhysicalSub`
 * before the domain check would even matter. Both splits are named at their
 * own `it` blocks below, not just here.
 *
 * ONE UI-SIDE CONDITION THE HANDLER LACKS (non-blocking, named for the
 * record): `set_scope_edge` is UI-unreachable at THIS fixture's own current
 * mode (`mode=0`, CTR) — both toolbar surfaces gate EDGE's visibility on
 * `isEdgeApplicable` (`spectrum-toolbar-logic.ts:44-46`: FIX(1)/S-F(3) only),
 * while `onEdgeChange` itself carries no such mode-conditional check at all
 * and dispatches for any in-range edge value regardless of the current mode
 * (walked below as-is). This is NOT a MOR-1576 finding: it is
 * pre-adjudicated as intentional in `radio-view-model.ts:851-856` — "EDGE's
 * applicability is UI-only, gated on the current MODE value ... that is a
 * rendering decision, not a fact-availability distinction" — so the handler
 * deliberately has no business reason to mirror a pure-presentation gate.
 *
 * RED-FIRST EVIDENCE (MOR-1566 build process, not part of this diff, first
 * as a deliberately wrong claim, then fixed): the `quick_split` dispatch case
 * below was first authored as `expectFrames(() =>
 * makeVfoHandlers().onQuickSplit(), [['quick_split', { on: true }]])` — a
 * fabricated non-empty params object (the real intent takes no params, per
 * `panel-commands.ts:1195`'s own `dispatchRadioIntent({ name: 'quick_split',
 * params: {} })`). `vitest run` on that version failed with (verbatim vitest
 * 4 output): `AssertionError: expected [ [ 'quick_split', {} ] ] to deeply
 * equal [ Array(1) ]` followed by a diff showing `- { "on": true }` / `+ {}`
 * for the params object (RED). Replacing the claim with `{}` turned it
 * GREEN — see that case below.
 *
 * DISCRIMINATION EVIDENCE (MOR-1566 build process, not part of this diff): to
 * prove the `set_scope_rbw` refusal below isn't vacuous, its gate was
 * scratch-flipped locally — `h.state.fieldStatus['scopeControls.rbw']`
 * temporarily set to `{ availability: 'available', observed: true,
 * freshness: 'fresh' }` before calling `onRbwChange(0)` — and the refusal
 * assertion then FAILED (the handler dispatched `set_scope_rbw` with `{ rbw:
 * 0 }`), confirming the assertion genuinely depends on that one field-status
 * gate. The scratch edit was staged (`git add`) in its clean form first,
 * then reverted with `git checkout --` (not `git stash`) after observing the
 * failure, restoring the green state before this file was committed.
 *
 * EXTENSION (per this ticket's instruction): `vfo_swap`/`vfo_equalize`/
 * `set_scope_hold` were already CLAIMED by MOR-1563's (C9) keyboard walk —
 * see `./conformance/claimed.ts`. That walk exercised them ONLY through
 * `dispatchKeyboardRadioAction`'s `vfo_swap`/`vfo_equalize`/
 * `scope_toggle_hold` cases (`panel-commands.ts:1520-1525`, `:1559-1565`).
 * NOT UNIFORM across the three: `vfo_swap`/`vfo_equalize`'s keyboard cases
 * are bare calls (`makeVfoHandlers().onSwap(); return true;`) with NO
 * additional gate of their own — the handler's own structural check
 * (`vfoScheme !== 'single'`) is the only gate in play, so per MOR-1576 it
 * cannot differ from a direct call. `scope_toggle_hold`'s keyboard case is
 * DIFFERENT: it DOES pre-gate, via `keyboardScopeField` (`:1371-1376`:
 * `scopeControls.hold` non-null + `isFieldAvailable`) plus its own
 * `caps.scope===true && has('scope') && typeof hold === 'boolean'` check
 * (`:1561`), before ever calling `onHoldChange`. That pre-gate is strictly
 * SUBSUMED by — not independent of — `onHoldChange`'s own gate
 * (`currentScopeContext()` re-checks the identical `caps.scope`/`has('scope')`
 * pair, and `acceptsScopeValue` re-checks the identical `isFieldAvailable` +
 * boolean-typed-current condition): whenever the pre-gate passes, the
 * handler's own gate passes too, so the conclusion still holds — no
 * MOR-1576-class split. But the correction cuts the other way for coverage:
 * the non-keyboard call site is strictly LOOSER (no pre-gate at all, only
 * `onHoldChange`'s own gate applies), so MOR-1563's keyboard-only coverage
 * genuinely did not exercise the FULL admissible set `onHoldChange` itself
 * allows — this walk's direct call does. Real, DIFFERENT, non-keyboard UI
 * call sites exist for all three: `VfoControlPanel.svelte:51-52`/
 * `RadioLayout.svelte:413,420` wire `vfoHandlers.onSwap`/`.onEqual` directly
 * to click handlers, and `SemanticRadioSurfaces.svelte:356,1113` wires
 * `scopeIntents.onHoldChange` through `SCOPE_TOGGLE_INTENT` to
 * `ScopeControlsSurface.svelte`'s HOLD toggle button. This walk's own 3
 * cases below claim conformance coverage of THOSE call sites directly (never
 * exercised as `expectFrames` targets before), while confirming — not
 * re-deriving — MOR-1578 leg 1's finding that `onSwap`/`onEqual`'s gate is
 * purely structural (`vfoScheme !== 'single'`) with NO field-observation
 * leaf read at all, unlike every other intent in this family.
 */
import { afterEach, beforeEach, describe, expect, it } from 'vitest';
import {
  expectFrames,
  expectRefusal,
  fixtureCaps,
  fixtureState,
  h,
} from './conformance/harness';
import { PROFILES } from './conformance/profiles';
import { makeScopeControlsHandlers, makeVfoHandlers } from '../../commands/panel-commands';
import { resetCommandLifecycle } from '$lib/stores/commands.svelte';
import { MODE_BUTTONS } from '../../../../components/spectrum/spectrum-toolbar-logic';
import { CHOICES } from '../../../../semantic/ScopeControlsSurface.svelte';

const profile = PROFILES.ic7300;
const IC7300_STATE = profile.state;
const IC7300_CAPABILITIES = profile.caps;
const scope = IC7300_STATE.scopeControls!;

/** Reads a `CHOICES` entry's declared numeric domain, in table order. */
function choiceDomain(field: string): number[] {
  const entry = CHOICES.find(([f]) => f === field);
  return entry ? entry[2].map(([v]) => v as number) : [];
}

describe('IC-7300 fixture — scope-remainder/VFO-topology family conformance (MOR-1566)', () => {
  beforeEach(() => {
    h.state = fixtureState(profile);
    h.caps = fixtureCaps(profile);
    h.sendCommand.mockClear();
  });

  afterEach(() => {
    resetCommandLifecycle();
  });

  describe("set_scope_mode — boundary walk over MODE_BUTTONS, the production UI's own domain table (matches acceptsScopeValue's hardcoded 0-3)", () => {
    it('mode domain is [0,1,2,3], matching the handler gate; scopeControls.mode is observed and in-range', () => {
      expect(MODE_BUTTONS.map(([v]) => v)).toEqual([0, 1, 2, 3]);
      expect(IC7300_STATE.fieldStatus?.['scopeControls.mode']?.observed).toBe(true);
      expect(scope.mode).toBe(0);
    });

    for (const mode of MODE_BUTTONS.map(([v]) => v)) {
      it(`mode=${mode}: DISPATCHES set_scope_mode`, () => {
        expectFrames(() => makeScopeControlsHandlers().onModeChange(mode), [['set_scope_mode', { mode }]]);
      });
    }
  });

  describe("set_scope_edge — boundary walk over CHOICES['edge'], the production UI's own domain table (matches acceptsScopeValue's hardcoded 1-4)", () => {
    it('edge domain is [1,2,3,4], matching the handler gate; scopeControls.edge is observed and in-range', () => {
      expect(choiceDomain('edge')).toEqual([1, 2, 3, 4]);
      expect(IC7300_STATE.fieldStatus?.['scopeControls.edge']?.observed).toBe(true);
      expect(scope.edge).toBe(1);
    });

    for (const edge of choiceDomain('edge')) {
      it(`edge=${edge}: DISPATCHES set_scope_edge`, () => {
        expectFrames(() => makeScopeControlsHandlers().onEdgeChange(edge), [['set_scope_edge', { edge }]]);
      });
    }
  });

  it("set_scope_dual: REFUSES — hasPhysicalSub fails (structural: caps.receivers===1, no dual_rx, state.sub is null; NOT a field-observation gate — scopeControls.dual is itself observed)", () => {
    expect(IC7300_CAPABILITIES.receivers).toBe(1);
    expect(IC7300_CAPABILITIES.capabilities).not.toContain('dual_rx');
    expect(IC7300_STATE.sub).toBeFalsy();
    expect(IC7300_STATE.fieldStatus?.['scopeControls.dual']?.observed).toBe(true);
    expectRefusal(() => makeScopeControlsHandlers().onDualChange(true));
  });

  it('set_scope_during_tx: DISPATCHES — scopeControls.duringTx is observed boolean', () => {
    expect(IC7300_STATE.fieldStatus?.['scopeControls.duringTx']?.observed).toBe(true);
    expect(scope.duringTx).toBe(true);
    expectFrames(() => makeScopeControlsHandlers().onDuringTxChange(false), [['set_scope_during_tx', { on: false }]]);
  });

  describe("set_scope_center_type — boundary walk over CHOICES['centerType'], the production UI's own domain table (matches acceptsScopeValue's hardcoded 0-2)", () => {
    it('centerType domain is [0,1,2], matching the handler gate; scopeControls.centerType is observed and in-range', () => {
      expect(choiceDomain('centerType')).toEqual([0, 1, 2]);
      expect(IC7300_STATE.fieldStatus?.['scopeControls.centerType']?.observed).toBe(true);
      expect(scope.centerType).toBe(2);
    });

    for (const center_type of choiceDomain('centerType')) {
      it(`center_type=${center_type}: DISPATCHES set_scope_center_type`, () => {
        expectFrames(
          () => makeScopeControlsHandlers().onCenterTypeChange(center_type),
          [['set_scope_center_type', { center_type }]],
        );
      });
    }
  });

  it('set_scope_vbw: DISPATCHES — scopeControls.vbwNarrow is observed boolean', () => {
    expect(IC7300_STATE.fieldStatus?.['scopeControls.vbwNarrow']?.observed).toBe(true);
    expect(scope.vbwNarrow).toBe(false);
    expectFrames(() => makeScopeControlsHandlers().onVbwChange(true), [['set_scope_vbw', { narrow: true }]]);
  });

  describe("set_scope_rbw — the ONE genuinely-unobserved scope leaf in this family (discrimination evidence, see file header); domain is CHOICES['rbw'] = [0,1,2], matching acceptsScopeValue's hardcoded 0-2 STRUCTURALLY only — observation blocks dispatch for every value, so the domain's max (2) is never behaviorally exercised (see file header)", () => {
    it('rbw domain is [0,1,2]; scopeControls.rbw is UNOBSERVED (the lone gap in this family)', () => {
      expect(choiceDomain('rbw')).toEqual([0, 1, 2]);
      expect(IC7300_STATE.fieldStatus?.['scopeControls.rbw']?.observed).toBe(false);
    });

    it('rbw=0: REFUSES', () => {
      expectRefusal(() => makeScopeControlsHandlers().onRbwChange(0));
    });
  });

  describe("switch_scope_receiver — receiver=0 DISPATCHES (own leaf observed, no sub required); receiver=1 REFUSES (hasPhysicalSub fails, same structural gate as set_scope_dual) — so the declared domain's upper bound (1) is pinned STRUCTURALLY (choiceDomain below) but never behaviorally exercised as a dispatch (see file header)", () => {
    it('scopeControls.receiver is observed and in-range', () => {
      expect(choiceDomain('receiver')).toEqual([0, 1]);
      expect(IC7300_STATE.fieldStatus?.['scopeControls.receiver']?.observed).toBe(true);
      expect(scope.receiver).toBe(0);
    });

    it('receiver=0: DISPATCHES switch_scope_receiver', () => {
      expectFrames(
        () => makeScopeControlsHandlers().onReceiverChange(0),
        [['switch_scope_receiver', { receiver: 0 }]],
      );
    });

    it('receiver=1: REFUSES — hasPhysicalSub fails', () => {
      expectRefusal(() => makeScopeControlsHandlers().onReceiverChange(1));
    });
  });

  it('set_dual_watch: REFUSES — caps.receivers < 2, short-circuits before dual_rx/dual_watch capability or field-observation checks', () => {
    expect(IC7300_CAPABILITIES.receivers).toBeLessThan(2);
    expectRefusal(() => makeVfoHandlers().onDualWatchToggle(true));
  });

  it('set_main_sub_tracking: REFUSES — same caps.receivers < 2 structural gate as set_dual_watch', () => {
    expect(IC7300_CAPABILITIES.receivers).toBeLessThan(2);
    expectRefusal(() => makeVfoHandlers().onTrackingToggle(true));
  });

  it('quick_dualwatch: REFUSES — same caps.receivers < 2 structural gate', () => {
    expect(IC7300_CAPABILITIES.receivers).toBeLessThan(2);
    expectRefusal(() => makeVfoHandlers().onQuickDw());
  });

  it('quick_split: DISPATCHES with an empty params object — vfoScheme !== "single", split capability declared, top-level split field observed (RED-FIRST target, see file header)', () => {
    expect(IC7300_CAPABILITIES.vfoScheme).not.toBe('single');
    expect(IC7300_CAPABILITIES.capabilities).toContain('split');
    expect(IC7300_STATE.fieldStatus?.split?.observed).toBe(true);
    expectFrames(() => makeVfoHandlers().onQuickSplit(), [['quick_split', {}]]);
  });

  describe('EXTENSION — vfo_swap/vfo_equalize/set_scope_hold: real non-keyboard UI call sites, gate re-confirmed (see file header)', () => {
    it("vfo_swap via makeVfoHandlers().onSwap() — VfoControlPanel.svelte's A<->B button call site: DISPATCHES with empty params, gate is structural-only (vfoScheme !== 'single'), MOR-1578 leg 1", () => {
      expect(IC7300_CAPABILITIES.vfoScheme).not.toBe('single');
      expectFrames(() => makeVfoHandlers().onSwap(), [['vfo_swap', {}]]);
    });

    it("vfo_equalize via makeVfoHandlers().onEqual() — VfoControlPanel.svelte's A=B button call site: DISPATCHES with empty params, same structural-only gate", () => {
      expect(IC7300_CAPABILITIES.vfoScheme).not.toBe('single');
      expectFrames(() => makeVfoHandlers().onEqual(), [['vfo_equalize', {}]]);
    });

    it("set_scope_hold via makeScopeControlsHandlers().onHoldChange() — SemanticRadioSurfaces.svelte's SCOPE_TOGGLE_INTENT['hold'] call site (ScopeControlsSurface's HOLD button): DISPATCHES, same acceptsScopeValue gate MOR-1563's keyboard scope_toggle_hold case already exercised — not a MOR-1576-class split, positive finding", () => {
      expect(IC7300_STATE.fieldStatus?.['scopeControls.hold']?.observed).toBe(true);
      expectFrames(
        () => makeScopeControlsHandlers().onHoldChange(!scope.hold),
        [['set_scope_hold', { on: !scope.hold }]],
      );
    });
  });
});
