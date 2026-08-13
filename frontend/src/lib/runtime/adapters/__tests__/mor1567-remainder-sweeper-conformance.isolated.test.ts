/**
 * MOR-1567 (C13) — Remainder-sweeper family walk, over the same
 * profile-parameterized harness MOR-1428/MOR-1555 established
 * (`./conformance/harness.ts`, `./conformance/profiles.ts`). This is the
 * FINAL Tier-2 family walk of the MOR-1426 conformance program — its own
 * acceptance criterion is `WAIVED_INTENTS` in `./conformance/waived.ts`
 * going to zero (17 → 0; `CLAIMED_INTENTS_COUNT` 70 → 87).
 *
 * FAMILY (per `waived.ts`'s SWEEPER tag, 17 intents, spanning 5 factories):
 * `scan_start`/`scan_stop`/`scan_set_df_span`/`scan_set_resume`
 * (`makeScanHandlers()`, `panel-commands.ts:392-410`); `set_dial_lock`/
 * `set_powerstat`/`speak` (`makeSystemHandlers()`, `:1239-1255`);
 * `set_antenna_1`/`set_antenna_2`/`set_rx_antenna_ant1`/
 * `set_rx_antenna_ant2` (`makeAntennaHandlers()`, `:286-316`);
 * `set_digisel`/`set_ip_plus` (`makeRfFrontEndHandlers()`, `:342-351`);
 * `memory_clear` (`makeMemoryHandlers()`, `:205-228`); plus the 3 orphaned
 * RIT/XIT intents `set_rit_status`/`set_rit_tx_status`/`set_rit_frequency`
 * (`makeRitXitHandlers()`, `:357-388`) that `waived.ts`'s own header
 * documents as a genuine hole in MOR-1426's per-family prose (no C6-C13
 * ticket names them in text; they land here because C13 is the closing
 * sweeper). All 17 dispatch through `dispatchRadioIntent`, which validates
 * every call against `radio-intents.ts`'s `intentSpecs` table (declared
 * param shapes — a mismatched shape THROWS, not just silently diverges) —
 * every frame claimed below is asserted against that exact shape.
 *
 * SKEWED TOWARD REFUSAL, but for THREE distinct reasons, not one: (1) 8
 * genuinely REFUSE on an unobserved field this fixture never confirmed —
 * `dialLock`, `rxAntenna1`/`rxAntenna2`/`txAntenna` (antenna family, though
 * a STRUCTURAL gate fires first — see below), `main.digisel`,
 * `main.ipplus`, `ritOn`, `ritTx`, `ritFreq` — same honest fail-closed shape
 * as C6/C7/C10/C11's walks; (2) `set_antenna_1`/`set_antenna_2`/
 * `set_rx_antenna_ant1`/`set_rx_antenna_ant2` all REFUSE on
 * `caps.antennas === 1 < 2`, a STRUCTURAL gate that fires before the
 * (also-unobserved) field check ever runs — `onToggleRxAnt` is the SOLE
 * call site for both `set_rx_antenna_ant1` and `set_rx_antenna_ant2` (the
 * `state.txAntenna` value picks which name it would dispatch), so both
 * names refuse identically here, before that branch is ever reached; (3) 6
 * genuinely DISPATCH: all 4 scan intents and `speak` dispatch UNCONDITIONALLY
 * — no capability check (not even `hasCapability('scan')`, though `scan` IS
 * declared on this profile) and no field-observation gate at all, the same
 * ungated shape MOR-1578 already flagged for `vfo_swap`/`vfo_equalize`
 * (leg 1) — cited here as the same class, not re-filed; `set_powerstat`
 * DOES check a capability (`power_control`, declared) but has no field
 * gate either (RED-FIRST target, see below). `memory_clear` is the one
 * DISPATCH that goes through a real multi-field resolution
 * (`currentMemorySnapshot()`, see its own case below).
 *
 * MOR-1574 CONTRAST (RIT/XIT, cited per this ticket's instruction): MOR-1574
 * is the ADAPTER-SEAM finding that `toRitXitProps` (the READ path,
 * `panel-props.ts:482`, walked in
 * `mor1562-adapter-seams-conformance.isolated.test.ts:311-323`) has NO
 * fieldStatus gate on `ritOn`/`ritFreq`/`ritTx` at all — an honesty gap on
 * the props side. The WRITE path walked here (`makeRitXitHandlers()`) is
 * DIFFERENT: `onRitToggle`/`onXitToggle`/`onRitOffsetChange`/
 * `onXitOffsetChange`/`onClear` all DO check `knownTopLevelField('ritOn'
 * | 'ritTx' | 'ritFreq')` before dispatching — on this fixture all three
 * leaves are genuinely unobserved (confirmed below), so all 3 write-side
 * intents correctly REFUSE. The two paths reach the opposite outcome
 * (read: silently reports stale zeros; write: fails closed) off the SAME
 * unobserved data — MOR-1574 is the read-side half of that asymmetry, not
 * re-derived here.
 *
 * RED-FIRST EVIDENCE (MOR-1567 build process, not part of this diff): the
 * `set_powerstat` case below was first authored with a deliberately wrong
 * claim, `[['set_powerstat', { on: true }]]` (guessing the toggle inverts
 * the current state, the shape most other boolean intents in this codebase
 * follow). `vitest run` on that version failed with (verbatim vitest 4
 * output): `AssertionError: expected [ [ 'set_powerstat', { on: false } ] ]
 * to deeply equal [ [ 'set_powerstat', { on: true } ] ]`, followed by an
 * `- Expected` / `+ Received` diff showing `- "on": true,` / `+ "on":
 * false,` (RED) — `onPowerOff` hardcodes `{ on: false }` unconditionally
 * (`panel-commands.ts:1251`), it does not read or negate any current-state
 * field. Replacing the claim with `{ on: false }` turned it GREEN — see
 * that case below.
 *
 * DISCRIMINATION EVIDENCE (MOR-1567 build process, not part of this diff):
 * to prove the `set_dial_lock` refusal below isn't vacuous, its `run` was
 * scratch-edited locally to set `h.state.fieldStatus.dialLock =
 * { availability: 'available', observed: true, freshness: 'fresh' }` before
 * calling `onDialLock(true)`, keeping the row's claim at `frames: []`
 * (REFUSES) — `vitest run` on that version failed with (verbatim vitest 4
 * output): `AssertionError: expected "vi.fn()" to not be called at all, but
 * actually been called 1 times` followed by the received call, `[
 * 'set_dial_lock', { on: true }, '<uuid>' ]` (RED — the handler dispatched
 * once the gate was forced open), confirming the refusal genuinely depends
 * on that one field-status gate, not a vacuous assertion. The scratch edit
 * was staged (`git add`) in its clean form first, then reverted with
 * `git checkout --` (not `git stash`) after observing the failure,
 * restoring the green state before this file was committed.
 *
 * DYNAMIC MOD-INPUT DISPATCH (per this ticket's explicit instruction, NOT
 * one of the 87-name literal universe — `onModInputChange` builds its
 * intent via `modInputCommand(dataMode)`; see `waived.ts`'s header and
 * `panel-commands-completeness.test.ts`'s "dynamic mod-input call site"
 * block, which already asserts exactly one such call exists and is this
 * one): this fixture's `main.dataMode` currently reads `0` (DATA OFF), and
 * `dataOffModInput` (that group's field) is UNOBSERVED — so THIS fixture's
 * OWN current reading refuses. But `data1ModInput`/`data2ModInput`/
 * `data3ModInput` (groups 1/2/3) are ALL observed on this same fixture — a
 * real, fixture-relevant, mixed reading, not a hypothetical. The 4-case
 * table below mutates only `h.state.main.dataMode` (never the underlying
 * fixture file) to walk all four DATA groups' observation state through the
 * real handler, covering every group this fixture can reach — group 0's
 * refusal is the ACTUAL current-state behavior; groups 1-3's dispatches
 * confirm the handler resolves the right per-group command
 * (`set_data1_mod_input`/`set_data2_mod_input`/`set_data3_mod_input`) once
 * observed. This coverage is ADDITIVE — it claims no new name in
 * `claimed.ts` and changes no count, since these 4 names live outside the
 * 87-name universe by design (see header cited above).
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
import {
  makeAntennaHandlers,
  makeMemoryHandlers,
  makeModeHandlers,
  makeRfFrontEndHandlers,
  makeRitXitHandlers,
  makeScanHandlers,
  makeSystemHandlers,
} from '../../commands/panel-commands';
import { resetCommandLifecycle } from '$lib/stores/commands.svelte';

const profile = PROFILES.ic7300;
const IC7300_STATE = profile.state;
const IC7300_CAPABILITIES = profile.caps;

interface IntentCase {
  readonly label: string;
  readonly run: () => void;
  /** Empty = REFUSES. Non-empty = exact frames dispatched, in order. */
  readonly frames: Array<[string, Record<string, unknown>]>;
  readonly gate: string;
}

const CASES: readonly IntentCase[] = [
  { label: 'scan_start', run: () => makeScanHandlers().onScanStart(1),
    frames: [['scan_start', { type: 1 }]],
    gate: 'no gate at all beyond Number.isSafeInteger(type) — no scan-capability check despite scan being declared (MOR-1578-class, see header)' },
  { label: 'scan_stop', run: () => makeScanHandlers().onScanStop(),
    frames: [['scan_stop', {}]],
    gate: 'no gate at all — unconditional (MOR-1578-class)' },
  { label: 'scan_set_df_span', run: () => makeScanHandlers().onDfSpanChange(5),
    frames: [['scan_set_df_span', { span: 5 }]],
    gate: 'no gate at all beyond Number.isSafeInteger(span) (MOR-1578-class)' },
  { label: 'scan_set_resume', run: () => makeScanHandlers().onResumeChange(2),
    frames: [['scan_set_resume', { mode: 2 }]],
    gate: 'no gate at all beyond Number.isSafeInteger(mode) (MOR-1578-class)' },
  { label: 'set_dial_lock', run: () => makeSystemHandlers().onDialLock(true),
    frames: [],
    gate: 'top-level dialLock unobserved (dial_lock capability IS declared) — discrimination evidence in file header' },
  { label: 'set_powerstat', run: () => makeSystemHandlers().onPowerOff(),
    frames: [['set_powerstat', { on: false }]],
    gate: 'power_control capability declared; no field-observation gate at all — RED-FIRST target, see file header' },
  { label: 'speak', run: () => makeSystemHandlers().onSpeak(),
    frames: [['speak', { mode: 0 }]],
    gate: 'no gate at all — unconditional, not even a capability check (MOR-1578-class)' },
  { label: 'set_antenna_1', run: () => makeAntennaHandlers().onSelectAnt1(),
    frames: [],
    gate: 'caps.antennas=1 < 2 structural gate (fires before the also-unobserved rxAntenna1 field check)' },
  { label: 'set_antenna_2', run: () => makeAntennaHandlers().onSelectAnt2(),
    frames: [],
    gate: 'caps.antennas=1 < 2 structural gate (fires before the also-unobserved rxAntenna2 field check)' },
  { label: 'set_rx_antenna_ant1', run: () => makeAntennaHandlers().onToggleRxAnt(),
    frames: [],
    gate: 'caps.antennas=1 < 2 structural gate — SHARED onToggleRxAnt() call site with set_rx_antenna_ant2; fires before the txAntenna branch that would pick between the two names is ever reached' },
  { label: 'set_rx_antenna_ant2', run: () => makeAntennaHandlers().onToggleRxAnt(),
    frames: [],
    gate: 'same shared onToggleRxAnt() call site and gate as set_rx_antenna_ant1 above — see that row' },
  { label: 'set_digisel', run: () => makeRfFrontEndHandlers().onDigiSelToggle(true),
    frames: [],
    gate: "main.digisel unobserved (via knownActiveReceiver('digisel')'s field check)" },
  { label: 'set_ip_plus', run: () => makeRfFrontEndHandlers().onIpPlusToggle(true),
    frames: [],
    gate: "main.ipplus unobserved (via knownActiveReceiver('ipplus')'s field check)" },
  { label: 'memory_clear', run: () => makeMemoryHandlers().onClear(1),
    frames: [['memory_clear', { channel: 1 }]],
    gate: 'currentMemorySnapshot() resolves via relativeVfoIdentityUnknown\'s selected_unselected fallback to main.freqHz/main.mode (both observed, see block below); validMemoryChannel(1) true' },
  { label: 'set_rit_status', run: () => makeRitXitHandlers().onRitToggle(),
    frames: [],
    gate: 'top-level ritOn unobserved (rit capability IS declared) — contrast MOR-1574, see file header' },
  { label: 'set_rit_tx_status', run: () => makeRitXitHandlers().onXitToggle(),
    frames: [],
    gate: 'top-level ritTx unobserved (xit capability IS declared)' },
  { label: 'set_rit_frequency', run: () => makeRitXitHandlers().onRitOffsetChange(100),
    frames: [],
    gate: 'top-level ritFreq unobserved — 2 more call sites for this SAME intent walked in the EXTENSION block below' },
];

describe('IC-7300 fixture — remainder-sweeper family conformance (MOR-1567)', () => {
  beforeEach(() => {
    h.state = fixtureState(profile);
    h.caps = fixtureCaps(profile);
    h.sendCommand.mockClear();
  });

  afterEach(() => {
    resetCommandLifecycle();
  });

  it('covers exactly the 17 SWEEPER-tagged intents', () => {
    // 17 distinct labels — set_rx_antenna_ant1/ant2 get their own labeled
    // rows (see above) even though they share one onToggleRxAnt() call site.
    expect(new Set(CASES.map((c) => c.label)).size).toBe(17);
    expect(CASES).toHaveLength(17);
  });

  it('caps/fieldStatus sanity underlying the gates above', () => {
    expect(IC7300_CAPABILITIES.antennas).toBe(1);
    expect(IC7300_CAPABILITIES.capabilities).toEqual(expect.arrayContaining([
      'scan', 'power_control', 'dial_lock', 'rit', 'xit',
    ]));
    for (const field of ['dialLock', 'ritOn', 'ritTx', 'ritFreq', 'rxAntenna1', 'rxAntenna2', 'txAntenna']) {
      expect(IC7300_STATE.fieldStatus?.[field as keyof typeof IC7300_STATE.fieldStatus]?.observed).toBe(false);
    }
    expect(IC7300_STATE.fieldStatus?.['main.digisel']?.observed).toBe(false);
    expect(IC7300_STATE.fieldStatus?.['main.ipplus']?.observed).toBe(false);
    expect(IC7300_STATE.fieldStatus?.['main.freqHz']?.observed).toBe(true);
    expect(IC7300_STATE.fieldStatus?.['main.mode']?.observed).toBe(true);
    expect(IC7300_STATE.fieldStatus?.['main.activeSlot']?.observed).toBe(false);
    expect(IC7300_CAPABILITIES.vfoScheme).toBe('ab');
    expect(IC7300_CAPABILITIES.vfoReadback).toBe('selected_unselected');
  });

  for (const c of CASES) {
    const outcome = c.frames.length > 0 ? 'DISPATCHES' : 'REFUSES';
    it(`${c.label}: ${outcome} — ${c.gate}`, () => {
      if (c.frames.length > 0) {
        expectFrames(c.run, c.frames);
      } else {
        expectRefusal(c.run);
      }
    });
  }

  describe('EXTENSION — set_rit_frequency: the other 2 call sites for the same wire intent (RIT and XIT share one offset register, per panel-commands.ts:377 comment)', () => {
    it('onXitOffsetChange(100): REFUSES — same ritFreq-unobserved gate as onRitOffsetChange above', () => {
      expectRefusal(() => makeRitXitHandlers().onXitOffsetChange(100));
    });

    it("onClear() (the RIT/XIT clear-both action): REFUSES — same ritFreq-unobserved gate, reached via '(!hasCapability('rit') && !hasCapability('xit'))' being false (both ARE declared) so the field check is what actually fires", () => {
      expectRefusal(() => makeRitXitHandlers().onClear());
    });
  });

  describe('EXTENSION — dynamic mod-input dispatch: onModInputChange over all 4 DATA groups (see file header)', () => {
    const GROUPS: ReadonlyArray<{
      readonly dataMode: number;
      readonly stateKey: string;
      readonly frames: Array<[string, Record<string, unknown>]>;
    }> = [
      { dataMode: 0, stateKey: 'dataOffModInput', frames: [] },
      { dataMode: 1, stateKey: 'data1ModInput', frames: [['set_data1_mod_input', { source: 5 }]] },
      { dataMode: 2, stateKey: 'data2ModInput', frames: [['set_data2_mod_input', { source: 5 }]] },
      { dataMode: 3, stateKey: 'data3ModInput', frames: [['set_data3_mod_input', { source: 5 }]] },
    ];

    for (const g of GROUPS) {
      const observed = IC7300_STATE.fieldStatus?.[g.stateKey as keyof typeof IC7300_STATE.fieldStatus]?.observed;
      const outcome = g.frames.length > 0 ? 'DISPATCHES' : 'REFUSES';
      it(`dataMode=${g.dataMode} (${g.stateKey}, observed=${observed}): ${outcome}`, () => {
        expect(h.state?.main).toBeTruthy();
        h.state!.main!.dataMode = g.dataMode;
        const run = () => makeModeHandlers().onModInputChange(5);
        if (g.frames.length > 0) {
          expectFrames(run, g.frames);
        } else {
          expectRefusal(run);
        }
      });
    }
  });
});
