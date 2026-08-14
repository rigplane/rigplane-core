/**
 * MOR-1563 (C9) — Keyboard action fan-out conformance walk, over the same
 * profile-parameterized harness MOR-1428/MOR-1555 established
 * (`./conformance/harness.ts`, `./conformance/profiles.ts`).
 *
 * SCOPE: `dispatchKeyboardRadioAction` in `panel-commands.ts` has 29 radio
 * case labels. `toggle_split` was already claimed by MOR-1428's "keyboard
 * context" case. This walk claims the remaining 28 (`WAIVED_KEYBOARD_ACTIONS`
 * in `./conformance/waived.ts`, now empty — see `./conformance/claimed.ts`
 * for the landed 29-name `CLAIMED_KEYBOARD_ACTIONS`). The 3 non-radio
 * actions (`adjust_tuning_step`/`open_filter_settings`/`focus_target`) live
 * in a DIFFERENT switch (`makeKeyboardHandlers().dispatch`) and are a
 * deliberate scope boundary per `waived.ts`'s header, not covered here.
 *
 * FINDING (fixture-derived split): of the 28, 17 genuinely DISPATCH on this
 * IC-7300 fixture and 11 genuinely REFUSE. Every refusal below is an honest
 * fail-closed gate: an unobserved top-level/receiver field, a missing exact
 * `vfo_swap`/`vfo_equalize` capability tag, or a single-receiver/no-dual_rx
 * structural gate — never a test artifact. Each case's `gate` string names
 * the exact reason, cross-checked against the fixture's own
 * `fieldStatus`/`capabilities.keyboard.bindings` data, never invented.
 *
 * MOR-1577 (filed a prior review round, FIXED this round):
 * `capabilities.keyboard.bindings` declares FOUR bindings for
 * `adjust_af_level`/`adjust_rf_gain` (`af-level-up`/`-down`,
 * `rf-level-up`/`-down`, all on ArrowUp/ArrowDown), and every one carries
 * `{ delta: 5 }` or `{ delta: -5 }` — never `direction`. Before this fix,
 * `dispatchKeyboardRadioAction`'s `adjust_af_level`/`adjust_rf_gain` cases
 * read ONLY `params.direction` and never `params.delta` — a source-level
 * dead control on every profile, not fixture-specific: press Ctrl+ArrowUp
 * (or Ctrl+Shift+ArrowUp) and nothing was sent. Both cases now interpret a
 * declared `delta` as RAW units against the control's declared domain
 * (`caps.controls.af_level`/`rf_gain`, `raw_min`/`raw_max` — 0/255 on this
 * fixture), converted to the handler's normalized/raw wire shape as
 * appropriate, and dispatch below asserts the real fixture-derived target.
 * `direction`-style bindings still work unchanged (explicit `delta` wins
 * when both are present) — kept as two separately-labeled
 * HANDLER-CAPABILITY PROBES further down (NOT profile behavior, not
 * counted in the 17/11 split) proving the fallback path is intact.
 * STRENGTHENED (round-2 review): `af-level-up`/`rf-level-up` carry
 * `modifiers: ['CTRL']`/`['CTRL','SHIFT']` — `keyboard-map.ts`'s
 * `modifiersMatch()` (line ~206) resolves plain ArrowUp to `step-up`
 * (`adjust_tuning_step`) and only Ctrl+ArrowUp reaches `af-level-up`, so
 * this was a genuinely reachable, user-visible dead control on this
 * profile before the fix — not a theoretical gap.
 *
 * MOR-1604: (1) `vfo_swap`/`vfo_equalize` now dispatch only when the capability
 * payload declares their exact primitive tag. The byte-faithful IC-7300
 * capture declares neither, so both correctly refuse without consulting
 * active receiver, slot, or VFO readback. (2)
 * `switch_active_vfo` reads `context.state.active` RAW, bypassing the
 * observation-gated tautological-MAIN resolution every other action here
 * goes through; (3) `mode-psk`/`mode-pskr` bindings declare `{ mode: 'PSK'
 * }`/`{ mode: 'PSK-R' }`, but this fixture's `caps.modes` is `['USB','LSB',
 * 'CW','CW-R','AM','FM','RTTY','RTTY-R']` — no PSK/PSK-R — so those two
 * refuse here too, adjacent to (not the same case as) `mode_select` below,
 * which deliberately uses the declared-and-supported `LSB` binding.
 * RETRACTED (round-2 review, runtime-verified): an earlier draft of this
 * walk claimed `af-level-up`/`rf-level-up`/`step-up` share `ArrowUp` as a
 * three-way shadow, and that `scope_toggle_fst` (`F`) shadows
 * `open_filter_settings` — both FALSE. Every one of those bindings also
 * declares a `modifiers` array (`step-up`: none, `af-level-up`: `['CTRL']`,
 * `rf-level-up`: `['CTRL','SHIFT']`; `open-filter-settings`: none,
 * `scope-toggle-fst`: `['SHIFT']`) that `modifiersMatch()` discriminates
 * on exactly — each key+modifier combination resolves to its own single
 * binding, with no shadow. The direction in the original claim was also
 * backwards (plain `F` → `open-filter-settings`, Shift+`F` →
 * `scope-toggle-fst`, not the reverse). MOR-1578 has been amended to drop
 * this leg; the PSK/PSK-R leg above is unaffected and remains true.
 *
 * MOR-1454 ANGLE (the cycle family): `cycle_preamp`/`cycle_att`/
 * `cycle_agc` each wrap over a FIXTURE-DECLARED option list
 * (`caps.preValues`/`attValues`/`agcModes`) via the shared `keyboardCycle`
 * helper — this walk asserts the wrap target by indexing into those SAME
 * declared arrays (see `wrap()` below), never a hardcoded list, and on this
 * profile the current implementation correctly honors them (no MOR-1454
 * violation found on these three). `cycle_filter` wraps by INDEX COUNT
 * (`caps.filters.length`), not a value list, so MOR-1454 doesn't apply to
 * it the same way. `cycle_data_mode` has a domain of size 1
 * (`caps.dataModeCount === 1`) — the wrap target equals the current value,
 * the mathematically correct result of modulo-1, not evidence of
 * MOR-1454's "ignores declared options" failure mode. `scope_span_step`/
 * `scope_ref_step` use fixed clamp bounds hardcoded in
 * `dispatchKeyboardRadioAction` itself, with no scope-range capability on
 * this profile to check them against — an honest baseline, not a finding.
 * `adjust_af_level`/`adjust_rf_gain` were MOR-1577 above, not this angle:
 * the fixture DOES declare their domain (`{ delta }`), and the handler now
 * reads it and scales against `caps.controls.af_level`/`rf_gain`'s declared
 * `raw_min`/`raw_max` rather than a hardcoded step.
 *
 * RED-FIRST EVIDENCE, `cycle_filter` (MOR-1563 build, not part of this
 * diff): first authored with a deliberately wrong literal claim,
 * `frames: [['set_filter', { filter: 3, receiver: 0 }]]` (guessing the
 * wrap wrong). `vitest run` on that version failed with `expected [ [
 * 'set_filter', { filter: 2, receiver: 0 } ] ] to deeply equal [ [
 * 'set_filter', { filter: 3, receiver: 0 } ] ]` (RED — the real dispatch
 * is filter 2, not the guessed 3). Replacing the literal with the
 * fixture-derived expression below (`main.filter=1`,
 * `caps.filters.length=3` → `(((1-1)+1+3)%3)+1 === 2`) turned it GREEN.
 *
 * RED-FIRST EVIDENCE, `tune` (review-round fix, not part of this diff): the
 * case was rewritten to the real `nudge-right` binding shape (`ArrowRight`,
 * `{ direction: 'up', fine: false }`) with a deliberately wrong target,
 * `frames: [['set_freq', { freq: 14_190_000, receiver: 0 }]]`. `vitest
 * run` failed RED: `expected [ [ 'set_freq', { freq: 14189000, receiver: 0
 * } ] ] to deeply equal [ [ 'set_freq', { freq: 14190000, receiver: 0 } ]
 * ]` — the real target is base `main.freqHz` (14188000) plus the harness's
 * mocked 1000 Hz tuning step = 14189000, not the guessed 14190000. Fixing
 * the literal to the fixture-derived expression (`main.freqHz + 1000`)
 * turned it GREEN.
 *
 * DISCRIMINATION EVIDENCE (MOR-1563 build, not part of this diff): to
 * prove the `toggle_rit` refusal below isn't vacuous, its gate was
 * scratch-flipped locally — `h.state.fieldStatus['ritOn']` temporarily set
 * to `{ availability: 'available', observed: true, ... }` before calling
 * `dispatchKeyboardRadioAction({ action: 'toggle_rit' })` — and the refusal
 * assertion then FAILED (the handler dispatched `set_rit_status` with
 * `{ on: true }`, the fixture's own `ritOn === false` negated), confirming
 * the assertion genuinely depends on that one field-status gate. The
 * scratch edit was reverted with `git checkout --` (not `git stash`) after
 * observing the failure, restoring the green state before this file was
 * committed.
 *
 * TUNING-ACCUMULATOR ISOLATION (review-round fix): `tune`'s dispatch goes
 * through `makeVfoHandlers()`'s MODULE-SCOPE shared tuning accumulator
 * (`tuning-accumulator.ts`'s `getSharedTuningAccumulator`), which
 * `resetCommandLifecycle()` does NOT touch and which
 * `mor1428-ic7300-conformance.isolated.test.ts` also exercises on receiver
 * 0 in the same targeted-suite run. Left unreset, a hot burst carried over
 * from whichever file runs first would make the other's synchronous
 * `expectFrames` assertion flaky (a hot burst paces its flush via
 * `setTimeout` instead of emitting inline). This file calls the
 * already-exported test-only `resetSharedTuningAccumulatorForTests()` in
 * BOTH `beforeEach` and `afterEach` — the same convention
 * `tuning-accumulator.test.ts` itself uses — so `tune` always starts cold
 * here and never leaves hot state behind for a sibling file.
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
import { dispatchKeyboardRadioAction } from '../../commands/panel-commands';
import { resetSharedTuningAccumulatorForTests } from '../../commands/tuning-accumulator';
import { resetCommandLifecycle } from '$lib/stores/commands.svelte';

const profile = PROFILES.ic7300;
const IC7300_STATE = profile.state;
const IC7300_CAPABILITIES = profile.caps;
const main = IC7300_STATE.main!;
const scope = IC7300_STATE.scopeControls!;
const preValues = IC7300_CAPABILITIES.preValues!;
const attValues = IC7300_CAPABILITIES.attValues!;
const agcModes = IC7300_CAPABILITIES.agcModes!;
const filterCount = IC7300_CAPABILITIES.filters.length;
const dataModeCount = IC7300_CAPABILITIES.dataModeCount!;
const afLevelRange = IC7300_CAPABILITIES.controls!.af_level!;
const rfGainRange = IC7300_CAPABILITIES.controls!.rf_gain!;

/** Wraps `current` to the NEXT entry in a fixture-declared option list. */
function wrap(values: number[], current: number): number {
  return values[(values.indexOf(current) + 1) % values.length];
}

interface KeyboardCase {
  readonly action: string;
  readonly params?: Record<string, unknown>;
  /** Empty = REFUSES. Non-empty = exact frames dispatched, in order. */
  readonly frames: Array<[string, Record<string, unknown>]>;
  /** Names the exact gate that produced the outcome above. */
  readonly gate: string;
}

const CASES: readonly KeyboardCase[] = [
  { action: 'tune', params: { direction: 'up', fine: false },
    frames: [['set_freq', { freq: main.freqHz + 1000, receiver: 0 }]],
    gate: 'declared nudge-right binding (ArrowRight); main.freqHz observed, tuning step > 0' },
  { action: 'band_select', params: { index: 5 }, frames: [['set_band', { band: 5 }]],
    gate: 'bsr capability declared + main.freqHz observed' },
  { action: 'mode_select', params: { mode: 'LSB' }, frames: [['set_mode', { mode: 'LSB', receiver: 0 }]],
    gate: 'main.mode observed + LSB in caps.modes (MOR-1578: mode-psk/mode-pskr bindings declare undeclared modes, adjacent case)' },
  { action: 'cycle_data_mode',
    frames: [['set_data_mode', { mode: (main.dataMode + 1) % dataModeCount, receiver: 0 }]],
    gate: 'main.dataMode observed; dataModeCount=1 (see MOR-1454 note above)' },
  { action: 'cycle_filter', params: { step: 1 },
    frames: [['set_filter', { filter: (((main.filter! - 1) + 1 + filterCount) % filterCount) + 1, receiver: 0 }]],
    gate: 'main.filter observed; wraps over caps.filters.length (fixture-declared)' },
  { action: 'cycle_preamp', frames: [['set_preamp', { level: wrap(preValues, main.preamp), receiver: 0 }]],
    gate: 'main.preamp observed; wraps over caps.preValues (fixture-declared, MOR-1454 angle: honored)' },
  { action: 'cycle_att', frames: [['set_attenuator', { db: wrap(attValues, main.att), receiver: 0 }]],
    gate: 'main.att observed; wraps over caps.attValues (fixture-declared, MOR-1454 angle: honored)' },
  { action: 'cycle_agc', frames: [['set_agc', { mode: wrap(agcModes, main.agc!), receiver: 0 }]],
    gate: 'main.agc observed; wraps over caps.agcModes (fixture-declared, MOR-1454 angle: honored)' },
  { action: 'toggle_nr', frames: [['set_nr', { on: !main.nr, receiver: 0 }]], gate: 'main.nr observed boolean' },
  { action: 'toggle_nb', frames: [['set_nb', { on: !main.nb, receiver: 0 }]], gate: 'main.nb observed boolean' },
  { action: 'toggle_auto_notch', frames: [], gate: 'main.autoNotch unobserved (notch capability IS declared)' },
  { action: 'toggle_ip_plus', frames: [], gate: 'main.ipplus unobserved (ip_plus capability IS declared)' },
  { action: 'toggle_rit', frames: [], gate: 'top-level ritOn unobserved (see discrimination evidence above)' },
  { action: 'toggle_xit', frames: [], gate: 'top-level ritTx unobserved' },
  { action: 'clear_rit_xit', frames: [], gate: 'top-level ritFreq unobserved' },
  { action: 'adjust_af_level', params: { delta: 5 },
    frames: [['set_af_level', {
      level: Math.max(0, Math.min(1, main.afLevel + 5 / (afLevelRange.raw_max - afLevelRange.raw_min))),
      receiver: 0,
    }]],
    gate: 'declared af-level-up binding ({delta:5}); main.afLevel observed; delta scaled against '
      + 'caps.controls.af_level raw domain (MOR-1577, fixed)' },
  { action: 'adjust_rf_gain', params: { delta: -5 },
    frames: [['set_rf_gain', {
      level: Math.round(Math.max(rfGainRange.raw_min, Math.min(rfGainRange.raw_max,
        rfGainRange.raw_min + main.rfGain * (rfGainRange.raw_max - rfGainRange.raw_min) - 5))),
      receiver: 0,
    }]],
    gate: 'declared rf-level-down binding ({delta:-5}); main.rfGain observed; delta scaled against '
      + 'caps.controls.rf_gain raw domain (MOR-1577, fixed)' },
  { action: 'toggle_monitor', frames: [], gate: 'top-level monitorOn unobserved' },
  { action: 'vfo_swap', frames: [],
    gate: 'captured IC-7300 capability payload omits exact vfo_swap primitive tag (MOR-1604)' },
  { action: 'vfo_equalize', frames: [],
    gate: 'captured IC-7300 capability payload omits exact vfo_equalize primitive tag (MOR-1604)' },
  { action: 'switch_active_vfo', frames: [],
    gate: 'single receiver / no dual_rx — no observed dual-RX receiver exists to toggle (MOR-1601)' },
  { action: 'set_active_vfo', params: { vfo: 'MAIN' }, frames: [['set_vfo', { vfo: 'MAIN' }]],
    gate: 'state.main exists — activateReceiver dispatches unconditionally once the receiver resolves' },
  { action: 'toggle_dial_lock', frames: [], gate: 'top-level dialLock unobserved' },
  { action: 'scope_span_step', params: { direction: 'up' },
    frames: [['set_scope_span', { span: Math.max(0, Math.min(7, scope.span + 1)) }]],
    gate: 'scopeControls.span observed; clamp bounds (0-7) are hardcoded, not caps-declared' },
  { action: 'scope_ref_step', params: { direction: 'up' },
    frames: [['set_scope_ref', { ref: Math.max(-30, Math.min(10, scope.refDb + 5)) }]],
    gate: 'scopeControls.refDb observed; clamp bounds (-30..10) are hardcoded, not caps-declared' },
  { action: 'scope_toggle_hold', frames: [['set_scope_hold', { on: !scope.hold }]],
    gate: 'scopeControls.hold observed boolean' },
  { action: 'scope_toggle_dual', frames: [],
    gate: 'single receiver / no dual_rx / no sub — hasPhysicalSub-equivalent gate fails' },
  { action: 'scope_toggle_fst', frames: [['set_scope_speed', { speed: scope.speed === 0 ? 1 : 0 }]],
    gate: 'scopeControls.speed observed' },
];

describe('IC-7300 fixture — keyboard action fan-out conformance (MOR-1563)', () => {
  beforeEach(() => {
    h.state = fixtureState(profile);
    h.caps = fixtureCaps(profile);
    h.sendCommand.mockClear();
    resetSharedTuningAccumulatorForTests();
  });

  afterEach(() => {
    resetCommandLifecycle();
    resetSharedTuningAccumulatorForTests();
  });

  it('covers exactly the 28 waived dispatchKeyboardRadioAction actions', () => {
    expect(CASES).toHaveLength(28);
  });

  for (const c of CASES) {
    const outcome = c.frames.length > 0 ? 'DISPATCHES' : 'REFUSES';
    it(`${c.action}: ${outcome} — ${c.gate}`, () => {
      let handled: boolean | undefined;
      const run = () => {
        handled = dispatchKeyboardRadioAction({ action: c.action, params: c.params });
      };
      if (c.frames.length > 0) {
        expectFrames(run, c.frames);
      } else {
        expectRefusal(run);
      }
      expect(handled).toBe(true);
    });
  }

  // MOR-1577 handler-capability probes: NOT this profile's declared binding
  // shape (real bindings send `{ delta }`, asserted as DISPATCH above) —
  // these prove the `direction`-based fallback path documented in the
  // header ("explicit delta wins") is still intact after the fix, exercised
  // with a synthetic `{ direction }` param no fixture binding actually sends.
  it('HANDLER-CAPABILITY PROBE (not profile behavior, MOR-1577): adjust_af_level dispatches set_af_level given {direction}', () => {
    expectFrames(
      () => dispatchKeyboardRadioAction({ action: 'adjust_af_level', params: { direction: 'up' } }),
      [['set_af_level', { level: Math.max(0, Math.min(1, main.afLevel + 0.05)), receiver: 0 }]],
    );
  });

  it('HANDLER-CAPABILITY PROBE (not profile behavior, MOR-1577): adjust_rf_gain dispatches set_rf_gain given {direction}', () => {
    expectFrames(
      () => dispatchKeyboardRadioAction({ action: 'adjust_rf_gain', params: { direction: 'down' } }),
      [['set_rf_gain', { level: Math.round(Math.max(0, Math.min(1, main.rfGain - 0.05)) * 255), receiver: 0 }]],
    );
  });
});
