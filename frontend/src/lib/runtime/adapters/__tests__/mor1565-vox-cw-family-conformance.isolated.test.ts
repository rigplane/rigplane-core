/**
 * MOR-1565 (C11) — VOX/CW intent family conformance walk, over the same
 * profile-parameterized harness MOR-1428/MOR-1555 established
 * (`./conformance/harness.ts`, `./conformance/profiles.ts`).
 *
 * Family (per `waived.ts`'s MOR-1565 tag, 12 intents): `set_vox`,
 * `set_vox_gain`, `set_anti_vox_gain`, `set_vox_delay`, `set_cw_pitch`,
 * `set_key_speed`, `set_break_in`, `set_break_in_delay`, `set_apf`,
 * `set_twin_peak`, `cw_auto_tune`, `set_dash_ratio` — dispatched from
 * `makeVoxHandlers()` and `makeCwPanelHandlers()` in `panel-commands.ts`
 * (plus `toggleVox`, a shared module-level function both `makeTxHandlers()`
 * and `makeVoxHandlers()` expose as `onVoxToggle`).
 *
 * NOT UNIFORM (11 refuse, 1 dispatches — even more skewed than MOR-1560's
 * DSP walk or MOR-1564's TX-chain walk): every field this family reads is
 * unobserved on the real IC-7300 bench capture (`voxOn`, `voxGain`,
 * `antiVoxGain`, `voxDelay`, `cwPitch`, `keySpeed`, `breakIn`,
 * `breakInDelay`, `main.apfTypeLevel`, `main.twinPeakFilter`, `dashRatio` —
 * all `fieldStatus.*.observed === false`), even though every base
 * capability the family needs (`tx`, `vox`, `cw`, `break_in`, `apf`,
 * `twin_peak`) IS declared on this profile — the same MOR-988 §3.2
 * fail-closed shape MOR-1560's DSP walk already established: capability
 * present, sub-parameter never confirmed on the bench, so the handler
 * refuses. `cw_auto_tune` is the sole exception: it is an empty-params RX
 * frequency-correction intent. Its gate requires `cw` and `audio` tags,
 * `audioFftAvailable === true`, and an observed active receiver frequency.
 * The IC-7300 fixture satisfies those RX-analysis prerequisites: its single
 * receiver makes an unobserved `active` tautologically MAIN (MOR-1418), and
 * `main.freqHz` is observed. On dual-RX profiles, both `active` and the
 * selected receiver frequency remain required through the same receiver-field
 * authority.
 *
 * Every refusal/dispatch below is read directly off the real IC-7300
 * fixture's `fieldStatus`/`capabilities`, never invented.
 *
 * GATE-CONSISTENCY FINDINGS (per this ticket's instruction to pin, not
 * fix — MOR-1576 class is "same intent alive on one path, dead on
 * another"; C10's `set_monitor_gain` walk is the precedent for a case that
 * WAS asymmetric): three intents in this family have two dispatch call
 * sites each, and unlike `set_monitor_gain`, all three are CONSISTENT —
 * (1) `set_vox`: `makeTxHandlers().onVoxToggle` and
 * `makeVoxHandlers().onVoxToggle` are both literally the module-level
 * `toggleVox` function reference (not two independent closures with
 * possibly-different gates) — asserted via `toBe` identity below, the
 * strongest form of "these can never drift apart" a test can state; (2)
 * `set_cw_pitch`: `onCwPitchChange` (CW panel's own pitch slider) and
 * `onSidetonePitchChange` (a sidetone-pitch alias) have byte-identical
 * bodies (`hasCapability('cw') && knownTopLevelField('cwPitch') &&
 * Number.isSafeInteger(value)`, read off `panel-commands.ts:494-497` and
 * `:540-543`) — PINNED below via `expect(String(hs.onCwPitchChange)).toBe(
 * String(hs.onSidetonePitchChange))`, not just matching observed refusal
 * behavior (a first draft of this file asserted refusal-only here, which
 * stays green even if the two gates later diverge — verifier-caught); (3)
 * `set_key_speed`: `onKeySpeedChange` and `onWpmChange` (`:498-501`,
 * `:531-534`) are likewise byte-identical and likewise PINNED via the same
 * `String(...)` identity pattern below. `set_break_in`'s two
 * call sites (`onBreakInToggle`/`onBreakInModeChange`) share the SAME
 * capability+field-observation gate (`hasCapability('cw') &&
 * hasCapability('break_in') && knownTopLevelField('breakIn')`) but differ
 * in which value they additionally range-check (`onBreakInToggle` checks
 * the CURRENT state value is a safe integer; `onBreakInModeChange` checks
 * the CALLER-SUPPLIED mode) — a cosmetic difference only, since on this
 * fixture `knownTopLevelField('breakIn')` alone already blocks both paths.
 * `set_monitor_gain`'s own CW-sidetone leg (`onSidetoneLevelChange`, the
 * ACTUAL MOR-1576-class asymmetry versus its TX-panel sibling) was already
 * claimed by MOR-1564/C10 — out of this family's 12-intent scope, per
 * `waived.ts`'s own tag list.
 *
 * DECLARED-DOMAIN WALKS: `set_cw_pitch` has a capability-declared numeric
 * range on this profile (`IC7300_CAPABILITIES.controls.cw_pitch`,
 * display 300-900 Hz) that happens to match `CwPanel.svelte`'s own pitch
 * slider domain exactly (`min={300} max={900}`) — walked at its min/mid/max.
 * `set_vox_gain`/`set_anti_vox_gain`/`set_key_speed`/`set_break_in_delay`
 * have NO declared range in this profile's `caps.controls` (verified via
 * `toBeUndefined()` below) and no `CONTROL_DEFAULTS` entry either (that
 * table only covers `nr_level`/`nb_depth` — `filter-controls.ts:122-125` —
 * so `controlRangeFromCapsOrDefault` would throw for these keys) — each is
 * walked at its own PRODUCTION fallback domain instead, read directly off
 * the real slider markup:
 * `VoxPanel.svelte` (`onVoxGainChange`/`onAntiVoxGainChange`: min=0 max=255;
 * `onVoxDelayChange`: min=0 max=20) and `CwPanel.svelte`
 * (`onKeySpeedChange`/`onWpmChange`: min=6 max=48;
 * `onBreakInDelayChange`: min=0 max=255) — never a test-invented number.
 * `set_apf`/`set_break_in`/`set_dash_ratio` are effectively BINARY
 * (mode/ratio toggles between two fixed values in their own handler body,
 * not a UI range slider) and get one representative case each; `set_vox`/
 * `set_twin_peak` are booleans (one case each); `cw_auto_tune` takes no
 * params at all.
 *
 * RED-FIRST EVIDENCE (MOR-1565 build process, not part of this diff): the
 * `cw_auto_tune` dispatch case below was first authored as
 * `expectFrames(() => makeCwPanelHandlers().onAutoTune(), [['cw_auto_tune',
 * { receiver: 0 }]])` — a deliberately wrong claimed params shape (the real
 * intent takes no params at all, per `radio-intents.ts`'s own
 * `{ names: ['cw_auto_tune', ...], params: {} }` spec). `vitest run` on
 * that version failed (verbatim vitest 4 output, not paraphrased):
 * `AssertionError: expected [ [ 'cw_auto_tune', {} ] ] to deeply equal [
 * Array(1) ]` followed by a diff showing `- { "receiver": 0 }` / `+ {}` for
 * the params object (RED — the real dispatch carries an empty params
 * object, not the fabricated `{ receiver: 0 }`). Replacing the claim with
 * `{}` turned it GREEN.
 *
 * DISCRIMINATION EVIDENCE (MOR-1565 build process, not part of this diff):
 * to prove the `set_vox_gain` refusal below isn't vacuous, its gate was
 * scratch-flipped locally — `h.state.fieldStatus.voxGain` temporarily set
 * to `{ availability: 'available', observed: true, freshness: 'fresh' }`
 * before calling `onVoxGainChange(128)` — and the refusal assertion then
 * FAILED (the handler dispatched `set_vox_gain` with `{ level: 128 }`),
 * confirming the assertion genuinely depends on that one field-status gate.
 * The scratch edit was staged (`git add`) in its clean form first, then
 * reverted with `git checkout --` (not `git stash`) after observing the
 * failure, restoring the green state before this file was committed.
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
import { makeCwPanelHandlers, makeTxHandlers, makeVoxHandlers } from '../../commands/panel-commands';
import { resetCommandLifecycle } from '$lib/stores/commands.svelte';

const profile = PROFILES.ic7300;
const IC7300_STATE = profile.state;
const IC7300_CAPABILITIES = profile.caps;

describe('IC-7300 fixture — VOX/CW family conformance (MOR-1565)', () => {
  beforeEach(() => {
    h.state = fixtureState(profile);
    h.caps = fixtureCaps(profile);
    h.sendCommand.mockClear();
  });

  afterEach(() => {
    resetCommandLifecycle();
  });

  describe('set_vox — shared toggleVox function across two call sites (gate-consistency finding 1, see file header)', () => {
    it("makeTxHandlers().onVoxToggle and makeVoxHandlers().onVoxToggle are the SAME function reference — not a MOR-1576-class split", () => {
      expect(makeTxHandlers().onVoxToggle).toBe(makeVoxHandlers().onVoxToggle);
    });

    it('voxOn is unobserved on this fixture: REFUSES via both call sites', () => {
      expect(IC7300_CAPABILITIES.capabilities).toContain('tx');
      expect(IC7300_CAPABILITIES.capabilities).toContain('vox');
      expect(IC7300_STATE.fieldStatus?.voxOn?.observed).toBe(false);
      expectRefusal(() => makeTxHandlers().onVoxToggle());
      expectRefusal(() => makeVoxHandlers().onVoxToggle());
    });
  });

  describe("set_vox_gain — boundary walk over VoxPanel.svelte's own slider domain (min=0 max=255; no declared caps range on this profile — discrimination case, see file header)", () => {
    it('vox_gain has no declared caps range on this profile; voxGain itself is unobserved', () => {
      expect(IC7300_CAPABILITIES.controls?.vox_gain).toBeUndefined();
      expect(IC7300_STATE.fieldStatus?.voxGain?.observed).toBe(false);
    });

    for (const level of [0, 128, 255]) {
      it(`level=${level}: REFUSES`, () => {
        expectRefusal(() => makeVoxHandlers().onVoxGainChange(level));
      });
    }
  });

  describe("set_anti_vox_gain — boundary walk over VoxPanel.svelte's own slider domain (min=0 max=255; same shape as set_vox_gain)", () => {
    it('anti_vox_gain has no declared caps range on this profile; antiVoxGain itself is unobserved', () => {
      expect(IC7300_CAPABILITIES.controls?.anti_vox_gain).toBeUndefined();
      expect(IC7300_STATE.fieldStatus?.antiVoxGain?.observed).toBe(false);
    });

    for (const level of [0, 128, 255]) {
      it(`level=${level}: REFUSES`, () => {
        expectRefusal(() => makeVoxHandlers().onAntiVoxGainChange(level));
      });
    }
  });

  describe("set_vox_delay — boundary walk over VoxPanel.svelte's own slider domain (min=0 max=20; no declared caps range on this profile)", () => {
    it('vox_delay has no declared caps range on this profile; voxDelay itself is unobserved', () => {
      expect(IC7300_CAPABILITIES.controls?.vox_delay).toBeUndefined();
      expect(IC7300_STATE.fieldStatus?.voxDelay?.observed).toBe(false);
    });

    for (const level of [0, 10, 20]) {
      it(`level=${level}: REFUSES`, () => {
        expectRefusal(() => makeVoxHandlers().onVoxDelayChange(level));
      });
    }
  });

  describe("set_cw_pitch — two call sites (gate-consistency finding 2, see file header), boundary walk over the DECLARED caps domain (matches CwPanel.svelte's own slider exactly)", () => {
    it('cw_pitch declares a usable range on this profile, matching the production slider domain (300-900 Hz)', () => {
      expect(IC7300_CAPABILITIES.controls?.cw_pitch).toEqual({
        raw_min: 0, raw_max: 255, display_min: 300, display_max: 900, display_unit: 'Hz',
      });
      expect(IC7300_STATE.fieldStatus?.cwPitch?.observed).toBe(false);
    });

    for (const value of [300, 600, 900]) {
      it(`value=${value} Hz: REFUSES via onCwPitchChange`, () => {
        expectRefusal(() => makeCwPanelHandlers().onCwPitchChange(value));
      });
    }

    it('onSidetonePitchChange: REFUSES via the same gate, and its source IS pinned byte-identical to onCwPitchChange (source-string identity, not just matching observed behavior — not a MOR-1576-class split)', () => {
      const hs = makeCwPanelHandlers();
      expect(String(hs.onCwPitchChange)).toBe(String(hs.onSidetonePitchChange));
      expectRefusal(() => hs.onSidetonePitchChange(600));
    });
  });

  describe("set_key_speed — two call sites (gate-consistency finding 3, see file header), boundary walk over CwPanel.svelte's own slider domain (min=6 max=48; no declared caps range on this profile)", () => {
    it('key_speed has no declared caps range on this profile; keySpeed itself is unobserved', () => {
      expect(IC7300_CAPABILITIES.controls?.key_speed).toBeUndefined();
      expect(IC7300_STATE.fieldStatus?.keySpeed?.observed).toBe(false);
    });

    for (const speed of [6, 27, 48]) {
      it(`speed=${speed} WPM: REFUSES via onKeySpeedChange`, () => {
        expectRefusal(() => makeCwPanelHandlers().onKeySpeedChange(speed));
      });
    }

    it('onWpmChange: REFUSES via the same gate, and its source IS pinned byte-identical to onKeySpeedChange (source-string identity, not just matching observed behavior — not a MOR-1576-class split)', () => {
      const hs = makeCwPanelHandlers();
      expect(String(hs.onKeySpeedChange)).toBe(String(hs.onWpmChange));
      expectRefusal(() => hs.onWpmChange(27));
    });
  });

  describe('set_break_in — two call sites sharing the same capability+field-observation gate, differing only in which value they additionally range-check (see file header)', () => {
    it('break_in capability is present; breakIn itself is unobserved: REFUSES via both call sites', () => {
      expect(IC7300_CAPABILITIES.capabilities).toContain('cw');
      expect(IC7300_CAPABILITIES.capabilities).toContain('break_in');
      expect(IC7300_STATE.fieldStatus?.breakIn?.observed).toBe(false);
      expectRefusal(() => makeCwPanelHandlers().onBreakInToggle());
      expectRefusal(() => makeCwPanelHandlers().onBreakInModeChange(1));
    });
  });

  describe("set_break_in_delay — boundary walk over CwPanel.svelte's own slider domain (min=0 max=255; no declared caps range on this profile)", () => {
    it('break_in_delay has no declared caps range on this profile; breakInDelay itself is unobserved', () => {
      expect(IC7300_CAPABILITIES.controls?.break_in_delay).toBeUndefined();
      expect(IC7300_STATE.fieldStatus?.breakInDelay?.observed).toBe(false);
    });

    for (const level of [0, 128, 255]) {
      it(`level=${level}: REFUSES`, () => {
        expectRefusal(() => makeCwPanelHandlers().onBreakInDelayChange(level));
      });
    }
  });

  it("set_apf: REFUSES — main.apfTypeLevel is unobserved (onApfChange is the only dispatch site; mode is a binary toggle in CwPanel.svelte's own handler, not a range slider)", () => {
    expect(IC7300_CAPABILITIES.capabilities).toContain('cw');
    expect(IC7300_CAPABILITIES.capabilities).toContain('apf');
    expect(IC7300_STATE.fieldStatus?.['main.apfTypeLevel']?.observed).toBe(false);
    expectRefusal(() => makeCwPanelHandlers().onApfChange(1));
  });

  it("set_twin_peak: REFUSES — main.twinPeakFilter is unobserved (onTwinPeakToggle is the only dispatch site for this intent; the CW-sidetone-gain leg of the CW panel, set_monitor_gain via onSidetoneLevelChange, is a DIFFERENT intent already claimed by MOR-1564/C10 — out of this family's scope)", () => {
    expect(IC7300_CAPABILITIES.capabilities).toContain('twin_peak');
    expect(IC7300_STATE.fieldStatus?.['main.twinPeakFilter']?.observed).toBe(false);
    expectRefusal(() => makeCwPanelHandlers().onTwinPeakToggle());
  });

  describe('cw_auto_tune — the one genuine RX correction dispatch in this family', () => {
    it('has cw/audio/FFT plus an observed MAIN frequency; single-RX active resolves tautologically to MAIN', () => {
      expect(IC7300_CAPABILITIES.capabilities).toContain('cw');
      expect(IC7300_CAPABILITIES.capabilities).toContain('audio');
      expect(IC7300_CAPABILITIES.audioFftAvailable).toBe(true);
      expect(IC7300_CAPABILITIES.vfoScheme).toBe('ab');
      expect(IC7300_CAPABILITIES.receivers).toBe(1);
      expect(IC7300_STATE.main).toBeTruthy();
      expect(IC7300_STATE.fieldStatus?.active?.observed).toBe(false);
      expect(IC7300_STATE.fieldStatus?.['main.freqHz']?.observed).toBe(true);
    });

    it('DISPATCHES cw_auto_tune with an empty params object', () => {
      expectFrames(() => makeCwPanelHandlers().onAutoTune(), [['cw_auto_tune', {}]]);
    });
  });

  it("set_dash_ratio: REFUSES — dashRatio is unobserved (onReversePaddleToggle is the only dispatch site; ratio toggles between two fixed values, 0/-1, in its own handler body, not a range slider)", () => {
    expect(IC7300_CAPABILITIES.capabilities).toContain('cw');
    expect(IC7300_STATE.fieldStatus?.dashRatio?.observed).toBe(false);
    expectRefusal(() => makeCwPanelHandlers().onReversePaddleToggle());
  });
});
