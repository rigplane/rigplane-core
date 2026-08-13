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
 * FINDING (fixture-derived split): of the 28, 18 genuinely DISPATCH on this
 * IC-7300 fixture and 10 genuinely REFUSE. Every refusal below is an
 * honest fail-closed gate (an unobserved top-level or receiver field, or a
 * single-receiver/no-dual_rx structural gate) — never a test artifact. Each
 * case's `gate` string names the exact reason, cross-checked against the
 * fixture's own `fieldStatus`/`capabilities` data below, never invented.
 *
 * MOR-1454 ANGLE (the cycle/step family): `cycle_preamp`/`cycle_att`/
 * `cycle_agc` each wrap over a FIXTURE-DECLARED option list
 * (`caps.preValues`/`attValues`/`agcModes`) via the shared `keyboardCycle`
 * helper — this walk asserts the wrap target by indexing into those SAME
 * declared arrays (see `wrap()` below), never a hardcoded list, and on this
 * profile the current implementation correctly honors them (no MOR-1454
 * violation found on these three). `cycle_filter` wraps by INDEX COUNT
 * (`caps.filters.length`), not a value list, so MOR-1454 doesn't apply to
 * it the same way. `cycle_data_mode` has a domain of size 1
 * (`caps.dataModeCount === 1`) — the wrap target equals the current value,
 * which is the mathematically correct result of modulo-1, not evidence of
 * MOR-1454's "ignores declared options" failure mode. `adjust_af_level`/
 * `adjust_rf_gain` and `scope_span_step`/`scope_ref_step` use a FIXED step
 * (5%, or 1/5 units) and FIXED clamp bounds hardcoded in
 * `dispatchKeyboardRadioAction` itself — this profile declares no
 * step-size or scope-range capability to check them against, so there is
 * no fixture-declared domain for these four to honor or violate; noted as
 * an honest baseline, not a finding.
 *
 * RED-FIRST EVIDENCE (MOR-1563 build process, not part of this diff): the
 * `cycle_filter` case below was first authored with a deliberately wrong
 * literal claim, `frames: [['set_filter', { filter: 3, receiver: 0 }]]`
 * (guessing the wrap wrong). `vitest run` on that version failed with
 * `expected [ [ 'set_filter', { filter: 2, receiver: 0 } ] ] to deeply
 * equal [ [ 'set_filter', { filter: 3, receiver: 0 } ] ]` (RED — the real
 * dispatch is filter 2, not the guessed 3). Replacing the literal with the
 * fixture-derived expression below (`main.filter=1`,
 * `caps.filters.length=3` → `(((1-1)+1+3)%3)+1 === 2`) turned it GREEN.
 *
 * DISCRIMINATION EVIDENCE (MOR-1563 build process, not part of this diff):
 * to prove the `toggle_rit` refusal below isn't vacuous, its gate was
 * scratch-flipped locally — `h.state.fieldStatus['ritOn']` temporarily set
 * to `{ availability: 'available', observed: true, ... }` before calling
 * `dispatchKeyboardRadioAction({ action: 'toggle_rit' })` — and the refusal
 * assertion then FAILED (the handler dispatched `set_rit_status` with
 * `{ on: true }`, the fixture's own `ritOn === false` negated), confirming
 * the assertion genuinely depends on that one field-status gate. The
 * scratch edit was reverted with `git checkout --` (not `git stash`) after
 * observing the failure, restoring the green state before this file was
 * committed.
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
  { action: 'tune', frames: [],
    gate: 'no direction/deltaHz in params — argument-shape gate, never reaches the tuning accumulator' },
  { action: 'band_select', params: { index: 5 }, frames: [['set_band', { band: 5 }]],
    gate: 'bsr capability declared + main.freqHz observed' },
  { action: 'mode_select', params: { mode: 'LSB' }, frames: [['set_mode', { mode: 'LSB', receiver: 0 }]],
    gate: 'main.mode observed + LSB in caps.modes' },
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
  { action: 'adjust_af_level', params: { direction: 'up' },
    frames: [['set_af_level', { level: Math.max(0, Math.min(1, main.afLevel + 0.05)), receiver: 0 }]],
    gate: 'main.afLevel observed; fixed 5% step (no declared step-size domain, see MOR-1454 note)' },
  { action: 'adjust_rf_gain', params: { direction: 'down' },
    frames: [['set_rf_gain', { level: Math.round(Math.max(0, Math.min(1, main.rfGain - 0.05)) * 255), receiver: 0 }]],
    gate: 'main.rfGain observed; fixed 5% step, hardcoded *255 scale (see MOR-1454 note)' },
  { action: 'toggle_monitor', frames: [], gate: 'top-level monitorOn unobserved' },
  { action: 'vfo_swap', frames: [['vfo_swap', {}]],
    gate: 'vfoScheme !== "single" — unconditional, no field-observation gate at all' },
  { action: 'vfo_equalize', frames: [['vfo_equalize', {}]],
    gate: 'vfoScheme !== "single" — unconditional, no field-observation gate at all' },
  { action: 'switch_active_vfo', frames: [],
    gate: 'single receiver / no dual_rx — active is tautologically MAIN so target computes to SUB, nothing to switch to' },
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
  });

  afterEach(() => {
    resetCommandLifecycle();
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
});
