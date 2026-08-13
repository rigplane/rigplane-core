/**
 * MOR-1564 (C10) — TX-chain intent family conformance walk, over the same
 * profile-parameterized harness MOR-1428/MOR-1555 established
 * (`./conformance/harness.ts`, `./conformance/profiles.ts`).
 *
 * Family (per `waived.ts`'s MOR-1564 tag, 8 intents): `set_rf_power`,
 * `set_mic_gain`, `set_compressor`, `set_compressor_level`, `set_monitor`,
 * `set_monitor_gain`, `set_drive_gain`, `set_tuner_status` — all dispatched
 * from `makeTxHandlers()` in `panel-commands.ts`, except `set_monitor_gain`,
 * which ALSO has a second dispatch site in `makeCwPanelHandlers()`
 * (`onSidetoneLevelChange` — writes the same wire field as the TX
 * monitor-gain handler, but per grep has ZERO UI consumers anywhere in this
 * codebase today: only its own definition at `panel-commands.ts:544`
 * exists, no CW sidetone-level slider or any other component calls it).
 * This family is TX-chain (RF power, mic gain, compressor, monitor, ATU),
 * so every refusal/dispatch below is read directly off the real IC-7300
 * fixture's `fieldStatus`/`capabilities`, never invented.
 *
 * NOT UNIFORM (4 dispatch, 4 refuse on this fixture): `powerLevel`,
 * `compressorOn`, `compressorLevel`, `tunerStatus` are all OBSERVED on the
 * real bench capture, so `set_rf_power`/`set_compressor`/
 * `set_compressor_level`/`set_tuner_status` genuinely dispatch. `micGain`,
 * `monitorOn`, `monitorGain`, `driveGain` are unobserved (or, for
 * `driveGain`, gated on a capability this profile never declares at all),
 * so `set_mic_gain`/`set_monitor`/`set_monitor_gain`/`set_drive_gain`
 * refuse — the same MOR-988 §3.2 fail-closed doctrine the exemplar's
 * `onMicGainChange(100)` case already established for this exact field.
 *
 * FINDING 1 (defense-in-depth / UI-hygiene gap at the frontend handler
 * layer — NOT a live safety issue; verified against the real backend
 * chain): `onRfPowerChange` has no bound check beyond `Number.isFinite` —
 * no reference to `IC7300_CAPABILITIES.controls.rf_power` (the WIRE-side
 * raw 0-255 range; irrelevant here, see below) or to `TxPanel.svelte`'s own
 * slider domain (`min={0} max={1} step={0.01}`, confirmed by reading that
 * component directly). The same shape repeats for `onCompLevelChange`
 * (dispatches `compressor_level` verbatim, no reference to its own
 * declared 0-255 range) — `radio-intents.ts`'s own `dispatchRadioIntent`
 * validation adds no additional bound either (`set_rf_power`'s spec is `{
 * level: 'number' }`, i.e. "any finite number", not the `'normalized'`
 * kind `set_af_level` uses for its own 0-1 domain). BUT the backend closes
 * this: `src/rigplane/web/handlers/control.py:257
 * _normalized_or_raw_level` and `src/rigplane/commands/_codec.py:21
 * _level_bcd_encode` were read directly — the wire-level BCD encoder
 * hard-REJECTS (raises `ValueError`) any value outside 0-255, so no
 * out-of-domain byte can ever reach the radio. The probe below (`level=5`)
 * is also NOT an over-power case: 5 falls outside `[0,1]`, so
 * `_normalized_or_raw_level` takes its RAW branch and interprets 5 as raw
 * units directly — raw 5 of 255 ≈ 2% power, a silent UNDER-power
 * misinterpretation, not an unclamped over-power dispatch. The genuine,
 * already-filed hazard is the normalized-vs-raw semantic switch itself
 * (0-1 is treated as normalized, anything else as raw units, with the
 * value `1` sitting exactly on the boundary between "100% normalized" and
 * "raw unit 1 ≈ 0.4% power") — see MOR-1579
 * ("`_normalized_or_raw_level`: silent normalized→raw semantic switch is a
 * TX-power foot-gun"), which also documents the `(1)→255=full power`
 * boundary trap. This test's finding is limited to the frontend handler
 * layer applying no domain clamp of its own — pinned here per this
 * ticket's instruction to report, not fix.
 *
 * FINDING 2 (latent adapter-layer gate inconsistency, MOR-1576 class —
 * "same intent alive on one path, dead on another"; backend-guarded, no
 * live operator path today): `set_monitor_gain` has two call sites with
 * DIFFERENT capability gates for the identical wire intent.
 * `onMonLevelChange` (TX family) requires `hasCapability('tx') &&
 * hasCapability('monitor')`; `onSidetoneLevelChange` (CW family,
 * `panel-commands.ts:544-547`) requires only `hasCapability('cw')` — it
 * never checks `monitor` at all. On the REAL IC-7300 fixture both refuse
 * for the same reason (`monitorGain` itself is unobserved), so the
 * divergence is invisible today — but the two gates are provably different
 * code paths: the discrimination case below removes only the `monitor`
 * capability (keeping `cw`) and marks `monitorGain` observed, and the CW
 * path DISPATCHES while the TX path still REFUSES. This asymmetry is
 * LATENT rather than operator-reachable, for two reasons: (1)
 * `onSidetoneLevelChange` has no UI caller anywhere in this codebase today
 * (grep finds only its own definition, see the family note above), so no
 * component can currently reach it; and (2) even if it were wired up, the
 * backend independently re-gates the SAME capability regardless of which
 * frontend call site dispatched it (`src/rigplane/web/handlers/control.py`,
 * `case "set_monitor_gain":` calls `self._ensure_capability("monitor",
 * "set_monitor_gain")` unconditionally) — a profile lacking `monitor`
 * would have its `set_monitor_gain` command rejected server-side either
 * way. Pinned per this ticket's instruction to report, not fix.
 *
 * RED-FIRST EVIDENCE (MOR-1564 build process, not part of this diff): the
 * `set_rf_power` `level=0.5` case below was first authored as
 * `expectFrames(() => makeTxHandlers().onRfPowerChange(0.5), [['set_rf_power',
 * { level: 999 }]])` — a deliberately wrong claimed dispatch value.
 * `vitest run` on that version failed with `expected [ [ 'set_rf_power', {
 * level: 0.5 } ] ] to deeply equal [ [ 'set_rf_power', { level: 999 } ] ]`
 * (RED — the real dispatch carries 0.5, the fixture/production value, not
 * the fabricated 999). Replacing the claim with `{ level: 0.5 }` turned it
 * GREEN.
 *
 * DISCRIMINATION EVIDENCE (MOR-1564 build process, not part of this diff):
 * to prove the `set_mic_gain` refusal below isn't vacuous, its gate was
 * scratch-flipped locally — `h.state.fieldStatus.micGain` temporarily set
 * to `{ availability: 'available', observed: true, freshness: 'fresh' }`
 * before calling `onMicGainChange(128)` — and the refusal assertion then
 * FAILED (the handler dispatched `set_mic_gain` with `{ level: 128 }`),
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
import { makeCwPanelHandlers, makeTxHandlers } from '../../commands/panel-commands';
import { resetCommandLifecycle } from '$lib/stores/commands.svelte';

const profile = PROFILES.ic7300;
const IC7300_STATE = profile.state;
const IC7300_CAPABILITIES = profile.caps;

describe('IC-7300 fixture — TX-chain family conformance (MOR-1564)', () => {
  beforeEach(() => {
    h.state = fixtureState(profile);
    h.caps = fixtureCaps(profile);
    h.sendCommand.mockClear();
  });

  afterEach(() => {
    resetCommandLifecycle();
  });

  describe('set_rf_power — production-domain walk (TxPanel.svelte slider: min=0 max=1 step=0.01), plus a no-clamp defense-in-depth probe (backend-guarded, see finding 1)', () => {
    it('powerLevel IS observed on this fixture; rf_power only declares a WIRE raw range (0-255), which onRfPowerChange never reads', () => {
      expect(IC7300_CAPABILITIES.capabilities).toContain('tx');
      expect(IC7300_STATE.fieldStatus?.powerLevel?.observed).toBe(true);
      expect(IC7300_CAPABILITIES.controls?.rf_power).toEqual({ raw_min: 0, raw_max: 255 });
    });

    for (const level of [0, 0.5, 1]) {
      it(`level=${level} (production domain [0,1]): CLAIMS — dispatches set_rf_power verbatim, no scaling (RED-FIRST evidence in file header)`, () => {
        expectFrames(() => makeTxHandlers().onRfPowerChange(level), [['set_rf_power', { level }]]);
      });
    }

    it("DEFENSE-IN-DEPTH: level=5 (past TxPanel.svelte's own max=1) still DISPATCHES verbatim — no domain clamp exists at the frontend handler layer, though the backend's BCD encoder hard-rejects true out-of-0-255 values, and 5 itself would be misinterpreted as raw ≈2% power (UNDER-power, not over) under _normalized_or_raw_level's normalized/raw switch — MOR-1579, not a live safety gap here (finding 1, see file header)", () => {
      expectFrames(() => makeTxHandlers().onRfPowerChange(5), [['set_rf_power', { level: 5 }]]);
    });
  });

  describe('set_mic_gain — boundary walk over the declared mic_gain range (also TxPanel.svelte\'s own slider domain, min=0 max=255)', () => {
    it('mic_gain declares a wire range on this profile; micGain itself is unobserved (discrimination case, see file header)', () => {
      expect(IC7300_CAPABILITIES.controls?.mic_gain).toEqual({ raw_min: 0, raw_max: 255 });
      expect(IC7300_STATE.fieldStatus?.micGain?.observed).toBe(false);
    });

    for (const level of [0, 128, 255]) {
      it(`level=${level}: REFUSES — micGain is unobserved`, () => {
        expectRefusal(() => makeTxHandlers().onMicGainChange(level));
      });
    }
  });

  it('set_compressor: DISPATCHES — compressorOn IS observed (true on this fixture), toggles off', () => {
    expect(IC7300_CAPABILITIES.capabilities).toContain('compressor');
    expect(IC7300_STATE.fieldStatus?.compressorOn?.observed).toBe(true);
    expect(IC7300_STATE.compressorOn).toBe(true);
    expectFrames(() => makeTxHandlers().onCompToggle(), [['set_compressor', { on: false }]]);
  });

  describe('set_compressor_level — boundary walk over the declared compressor_level range (TxPanel.svelte slider domain, min=0 max=255); same blind-passthrough shape as set_rf_power', () => {
    it('compressor_level declares a wire range on this profile; compressorLevel IS observed', () => {
      expect(IC7300_CAPABILITIES.controls?.compressor_level).toEqual({ raw_min: 0, raw_max: 255 });
      expect(IC7300_STATE.fieldStatus?.compressorLevel?.observed).toBe(true);
    });

    for (const level of [0, 128, 255]) {
      it(`level=${level}: DISPATCHES verbatim, no scaling`, () => {
        expectFrames(() => makeTxHandlers().onCompLevelChange(level), [['set_compressor_level', { level }]]);
      });
    }
  });

  it('set_monitor: REFUSES — monitorOn is unobserved (onMonToggle is the only dispatch site)', () => {
    expect(IC7300_CAPABILITIES.capabilities).toContain('monitor');
    expect(IC7300_STATE.fieldStatus?.monitorOn?.observed).toBe(false);
    expectRefusal(() => makeTxHandlers().onMonToggle());
  });

  describe('set_monitor_gain — dual call-site gate asymmetry: makeTxHandlers().onMonLevelChange vs makeCwPanelHandlers().onSidetoneLevelChange (finding 2, see file header)', () => {
    it('both REFUSE on the real fixture — monitorGain is unobserved (same proximate reason on both call sites today)', () => {
      expect(IC7300_STATE.fieldStatus?.monitorGain?.observed).toBe(false);
      expectRefusal(() => makeTxHandlers().onMonLevelChange(120));
      expectRefusal(() => makeCwPanelHandlers().onSidetoneLevelChange(120));
    });

    it("MOR-1576 class (latent, backend-guarded, no UI caller — see finding 2): with monitorGain observed and only the 'monitor' capability withdrawn (cw kept), the CW-family handler DISPATCHES set_monitor_gain while the TX-family handler still REFUSES — same wire intent, two different frontend capability gates", () => {
      h.caps!.capabilities = h.caps!.capabilities.filter((c) => c !== 'monitor');
      h.state!.fieldStatus!.monitorGain = {
        ...h.state!.fieldStatus!.monitorGain!,
        observed: true,
        availability: 'available',
        freshness: 'fresh',
      };
      expect(h.caps!.capabilities).toContain('cw');
      expect(h.caps!.capabilities).not.toContain('monitor');
      expectFrames(() => makeCwPanelHandlers().onSidetoneLevelChange(120), [['set_monitor_gain', { level: 120 }]]);
      h.sendCommand.mockClear();
      resetCommandLifecycle();
      expectRefusal(() => makeTxHandlers().onMonLevelChange(120));
    });
  });

  it("set_drive_gain: REFUSES — the 'drive_gain' capability itself is absent from this profile (fires before the driveGain field-status check even runs; TxPanel.svelte still renders a 0-255 slider for it as generic UI chrome)", () => {
    expect(IC7300_CAPABILITIES.capabilities).not.toContain('drive_gain');
    expect(IC7300_CAPABILITIES.controls?.drive_gain).toBeUndefined();
    expect(IC7300_STATE.fieldStatus?.driveGain?.observed).toBe(false);
    expectRefusal(() => makeTxHandlers().onDriveGainChange(IC7300_STATE.driveGain as number));
  });

  describe('set_tuner_status — two call sites sharing one consistent gate (unlike set_monitor_gain above)', () => {
    it('onAtuToggle: DISPATCHES — tunerStatus IS observed (value 0 on this fixture), toggles to 1', () => {
      expect(IC7300_CAPABILITIES.capabilities).toContain('tuner');
      expect(IC7300_STATE.fieldStatus?.tunerStatus?.observed).toBe(true);
      expect(IC7300_STATE.tunerStatus).toBe(0);
      expectFrames(() => makeTxHandlers().onAtuToggle(), [['set_tuner_status', { value: 1 }]]);
    });

    it('onAtuTune: DISPATCHES — same gate, always requests value=2 (start tune) regardless of the current tunerStatus', () => {
      expectFrames(() => makeTxHandlers().onAtuTune(), [['set_tuner_status', { value: 2 }]]);
    });
  });
});
