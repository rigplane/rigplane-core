/**
 * MOR-1560 (C6) — DSP intent family conformance walk, over the same
 * profile-parameterized harness MOR-1428/MOR-1555 established
 * (`./conformance/harness.ts`, `./conformance/profiles.ts`).
 *
 * Family (per `waived.ts`'s MOR-1560 tag, 9 intents): `set_nr_level`,
 * `set_nb_level`, `set_nb_depth`, `set_nb_width`, `set_auto_notch`,
 * `set_manual_notch`, `set_manual_notch_width`, `set_notch_filter`,
 * `set_agc_time_constant` — all dispatched from `makeDspHandlers()` in
 * `panel-commands.ts`.
 *
 * FINDING: every one of the 9 REFUSES on the real IC-7300 fixture, not
 * because a capability is undeclared (the base `nr`/`nb`/`notch`/`agc`
 * flags are all present in `IC7300_CAPABILITIES.capabilities`) but because
 * the live capture session's `fieldStatus` never confirmed any of these
 * FINE-GRAINED sub-parameters — only the coarse on/off toggles
 * (`main.nr`/`main.nb`) were ever queried on the bench. This is the exact
 * MOR-988 §3.2 fail-closed shape the exemplar's Section 3 already pins for
 * `micGain`/`nbLevel`/`ritOn` — this walk is the same doctrine applied to
 * the rest of the DSP family, not a deviation from it. Each case below
 * names the SPECIFIC field/gate that blocks it, verified against the
 * fixture's own `fieldStatus`/`capabilities.controls` data (never invented).
 *
 * TWO INTENTS (`set_nr_level`, `set_nb_level`) have a capability-declared
 * numeric domain (`IC7300_CAPABILITIES.controls.nr_level` /
 * `.nb_level`) and are walked at representative min/mid/max boundary
 * inputs from that domain — proving the refusal holds across the whole
 * range, not just one lucky value. The remaining 7 have NO declared range
 * on this profile (verified via `expect(...controls.X).toBeUndefined()`
 * below) — those get one representative case, using either the profile's
 * own generic fallback range (`controlRangeFromCapsOrDefault`, the same
 * fallback production code would apply, not a test-invented number) or the
 * fixture's own captured value for that leaf.
 *
 * RED-FIRST EVIDENCE (MOR-1560 build process, not part of this diff): the
 * first case below (`set_nr_level`) was authored as
 * `expectFrames(() => makeDspHandlers().onNrLevelChange(0), [['set_nr_level',
 * { level: 999, receiver: 0 }]])` — a deliberately wrong claim that the
 * intent dispatches. `vitest run` on that version failed with
 * `expected [] to deeply equal [ [ 'set_nr_level', …(1) ] ]` (RED — no
 * frame was ever sent, proving the wrong claim false). Replacing it with
 * `expectRefusal(...)` (the real, fixture-derived behavior) turned it GREEN.
 *
 * DISCRIMINATION EVIDENCE (MOR-1560 build process, not part of this diff):
 * to prove the `set_agc_time_constant` refusal below isn't vacuous, its
 * gate was scratch-flipped locally — `h.state.fieldStatus['main.agcTimeConstant']`
 * temporarily set to `{ availability: 'available', observed: true, ... }`
 * before calling `onAgcTimeChange` — and the refusal assertion then FAILED
 * (the handler dispatched `set_agc_time_constant` with the fixture's own
 * captured value), confirming the assertion genuinely depends on that one
 * field-status gate. The scratch edit was reverted with `git checkout --`
 * before this file was committed.
 */
import { afterEach, beforeEach, describe, expect, it } from 'vitest';
import {
  expectRefusal,
  fixtureCaps,
  fixtureState,
  h,
} from './conformance/harness';
import { PROFILES } from './conformance/profiles';
import { makeDspHandlers } from '../../commands/panel-commands';
import { resetCommandLifecycle } from '$lib/stores/commands.svelte';
import { controlRangeFromCapsOrDefault } from '$lib/radio/filter-controls';

const profile = PROFILES.ic7300;
const IC7300_STATE = profile.state;
const IC7300_CAPABILITIES = profile.caps;

describe('IC-7300 fixture — DSP family conformance (MOR-1560)', () => {
  beforeEach(() => {
    h.state = fixtureState(profile);
    h.caps = fixtureCaps(profile);
    h.sendCommand.mockClear();
  });

  afterEach(() => {
    resetCommandLifecycle();
  });

  describe('set_nr_level — boundary walk over the declared nr_level range', () => {
    // RED-FIRST: see file header. The declared wire range comes straight
    // from the fixture (`raw_min`/`raw_max`); no `display_min`/`display_max`
    // is declared, so the display domain is the SAME generic fallback
    // `nrDisplayToRaw` itself would fall back to at runtime
    // (`controlRangeFromCapsOrDefault`) — not a test-invented number.
    const { displayMin, displayMax } = controlRangeFromCapsOrDefault('nr_level', IC7300_CAPABILITIES);
    const mid = Math.round((displayMin + displayMax) / 2);

    it('nr_level declares a wire range (raw_min/raw_max) but no display range on this profile', () => {
      expect(IC7300_CAPABILITIES.controls?.nr_level).toEqual({ raw_min: 0, raw_max: 255 });
    });

    for (const level of [displayMin, mid, displayMax]) {
      it(`level=${level} (display domain [${displayMin},${displayMax}]): REFUSES — main.nrLevel is unobserved (main.nr IS observed)`, () => {
        expect(IC7300_CAPABILITIES.capabilities).toContain('nr');
        expect(IC7300_STATE.fieldStatus?.['main.nrLevel']?.observed).toBe(false);
        expect(IC7300_STATE.fieldStatus?.['main.nr']?.observed).toBe(true);
        expectRefusal(() => makeDspHandlers().onNrLevelChange(level));
      });
    }
  });

  describe('set_nb_level — boundary walk over the declared nb_level range (direct wire passthrough, no display scaling)', () => {
    const rawMin = IC7300_CAPABILITIES.controls!.nb_level!.raw_min;
    const rawMax = IC7300_CAPABILITIES.controls!.nb_level!.raw_max;
    const mid = Math.round((rawMin + rawMax) / 2);

    it('nb_level declares a wire range on this profile', () => {
      expect(IC7300_CAPABILITIES.controls?.nb_level).toEqual({ raw_min: 0, raw_max: 255 });
    });

    for (const level of [rawMin, mid, rawMax]) {
      it(`level=${level} (wire domain [${rawMin},${rawMax}]): REFUSES — main.nbLevel is unobserved (main.nb IS observed)`, () => {
        expect(IC7300_CAPABILITIES.capabilities).toContain('nb');
        expect(IC7300_STATE.fieldStatus?.['main.nbLevel']?.observed).toBe(false);
        expect(IC7300_STATE.fieldStatus?.['main.nb']?.observed).toBe(true);
        expectRefusal(() => makeDspHandlers().onNbLevelChange(level));
      });
    }
  });

  describe('set_nb_depth — no declared range on this profile; boundary walk over the generic fallback', () => {
    // This profile's `capabilities.controls` has NO `nb_depth` entry at
    // all — verified below — so `onNbDepthChange`'s OWN capability gate
    // (`getCapabilities()?.controls?.nb_depth`) already refuses before any
    // value is examined. The walked domain is the same generic
    // `CONTROL_DEFAULTS.nb_depth` fallback `nbDepthDisplayToRaw` would use
    // if the gate ever passed — not a number invented for this test.
    const { displayMin, displayMax } = controlRangeFromCapsOrDefault('nb_depth', IC7300_CAPABILITIES);
    const mid = Math.round((displayMin + displayMax) / 2);

    it('nb_depth is not a declared control on this profile', () => {
      expect(IC7300_CAPABILITIES.controls?.nb_depth).toBeUndefined();
    });

    for (const level of [displayMin, mid, displayMax]) {
      it(`level=${level}: REFUSES — nb_depth is not a declared control on this profile`, () => {
        expectRefusal(() => makeDspHandlers().onNbDepthChange(level));
      });
    }
  });

  it('set_nb_width: REFUSES — gated on the same undeclared nb_depth control, and topLevel nbWidth is unobserved', () => {
    // `onNbWidthChange` shares `onNbDepthChange`'s capability gate
    // (`controls?.nb_depth`, not its own `nb_width` key — panel-commands.ts
    // reads it that way) and has no display/raw scaling of its own, so
    // there is no declared domain to walk here at all. One representative
    // case, using the fixture's own captured top-level value.
    expect(IC7300_CAPABILITIES.controls?.nb_depth).toBeUndefined();
    expect(IC7300_STATE.fieldStatus?.nbWidth?.observed).toBe(false);
    expectRefusal(() => makeDspHandlers().onNbWidthChange(IC7300_STATE.nbWidth as number));
  });

  describe('set_auto_notch / set_manual_notch — walked across onNotchModeChange\'s full mode enum, plus onAutoNotchToggle', () => {
    for (const mode of ['auto', 'manual', 'off'] as const) {
      it(`onNotchModeChange('${mode}'): REFUSES — main.autoNotch is unobserved`, () => {
        expect(IC7300_CAPABILITIES.capabilities).toContain('notch');
        expect(IC7300_STATE.fieldStatus?.['main.autoNotch']?.observed).toBe(false);
        expect(IC7300_STATE.fieldStatus?.['main.manualNotch']?.observed).toBe(false);
        expectRefusal(() => makeDspHandlers().onNotchModeChange(mode));
      });
    }

    it("onAutoNotchToggle: REFUSES — same main.autoNotch gate, independent call site for set_auto_notch", () => {
      expect(IC7300_STATE.fieldStatus?.['main.autoNotch']?.observed).toBe(false);
      expectRefusal(() => makeDspHandlers().onAutoNotchToggle());
    });
  });

  it('set_notch_filter: REFUSES — fixture artifact, not a production structural gap: this capture predates MOR-1548 (eabd506b); production defines main.notchFilter receiver-scoped and the gate matches it. Allow-listed drift, re-pin after the MOR-1558/C4 bench re-capture.', () => {
    // MOR-1548 reclassified `onNotchFreqChange` as receiver-scoped
    // (`knownReceiverField('notchFilter')`, i.e. it reads `main.notchFilter`)
    // but this fixture's `main` object has no `notchFilter` key at all.
    // MOR-1575 confirmed the PRODUCTION mapping (backend routing +
    // frontend gate) has been correct and consistent since MOR-1548 — this
    // fixture is simply a byte-faithful capture (backendHeadSha e0b19814)
    // taken before that fix landed, so it still carries the pre-migration
    // top-level `notchFilter` shape. This is NOT proof `set_notch_filter`
    // is structurally dead on real IC-7300 hardware: the capture also
    // predates the MOR-1492 membership wave, so `notch_filter` had no
    // acquisition membership at capture time (never polled, honestly
    // missing) — today `ic7300.toml` polls it every ~25s, so a genuine
    // bench re-capture at HEAD may show it observed, i.e. the control
    // DISPATCHES. Only the bench can settle that (see
    // `fixture-provenance.guard.test.ts`'s `KNOWN_STALE_FIELDS.ic7300`,
    // held pending MOR-1558/MOR-1410) — this pin must NOT be hand-edited to
    // assert a different fieldStatus shape in the meantime. The input value
    // below is a neutral literal (same convention as the exemplar's
    // `onMicGainChange(100)` refusal case), not a fixture- or radio-derived
    // number.
    expect(IC7300_CAPABILITIES.capabilities).toContain('notch');
    expect(IC7300_STATE.main).not.toHaveProperty('notchFilter');
    expectRefusal(() => makeDspHandlers().onNotchFreqChange(0));
  });

  it('set_manual_notch_width: REFUSES — main.manualNotchWidth is unobserved, no declared control range on this profile', () => {
    expect(IC7300_CAPABILITIES.capabilities).toContain('notch');
    expect(IC7300_CAPABILITIES.controls?.manual_notch_width).toBeUndefined();
    expect(IC7300_STATE.fieldStatus?.['main.manualNotchWidth']?.observed).toBe(false);
    expectRefusal(
      () => makeDspHandlers().onManualNotchWidthChange(IC7300_STATE.main!.manualNotchWidth as number),
    );
  });

  it('set_agc_time_constant: REFUSES — main.agcTimeConstant is unobserved, no declared control range on this profile (discrimination case, see file header)', () => {
    expect(IC7300_CAPABILITIES.capabilities).toContain('agc');
    expect(IC7300_CAPABILITIES.controls?.agc_time_constant).toBeUndefined();
    expect(IC7300_STATE.fieldStatus?.['main.agcTimeConstant']?.observed).toBe(false);
    expectRefusal(
      () => makeDspHandlers().onAgcTimeChange(IC7300_STATE.main!.agcTimeConstant as number),
    );
  });
});
