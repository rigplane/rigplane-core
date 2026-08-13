/**
 * MOR-1556 — completeness ledger: explicit WAIVED registries.
 *
 * Every intent `panel-commands.ts` can dispatch, and every
 * `dispatchKeyboardRadioAction` case label, must be either claimed
 * (`./claimed.ts`) or waived (here) — the completeness meta-test
 * (`../../../commands/__tests__/panel-commands-completeness.test.ts`)
 * enforces that split, so a name that is neither claimed nor waived (e.g.
 * a newly-added intent) fails vitest.
 *
 * THIS MAP IS THE BURN-DOWN. When a family walk (C6-C13, MOR-1560..1567)
 * lands real `expectFrames` coverage for a waived intent: delete it from
 * the matching `tag([...])` call below, add the name to `CLAIMED_INTENTS`
 * in `./claimed.ts`, and update the matching `*_COUNT` pin in the same
 * diff — pinned as a literal so a removal (or a silent re-add) shows up
 * as a diff / count-assertion break.
 *
 * Provenance: family assignment comes from the MOR-1426 decomposition's
 * per-family "Work" lists (session 19, 2026-08-13), cross-checked against
 * the actual `dispatchRadioIntent` call sites grouped by factory. One gap:
 * `makeRitXitHandlers`' 3 intents (`set_rit_status`/`set_rit_tx_status`/
 * `set_rit_frequency`) aren't named in ANY of MOR-1560..1567's prose — the
 * family table has a genuine hole there, not a parsing mistake: C6(9) +
 * C7(5) + C10(8) + C11(12) + C12(15) + 15 literal of C13's own itemized 16
 * (its 16th is the dynamic mod-input call, tracked separately — see
 * below) sum to 64, exactly 3 short of 67. They land on MOR-1567 here
 * because C13 is explicitly the closing sweeper — its own acceptance
 * criterion is "C2's waiver map is EMPTY ... after this lands".
 *
 * ONE DELIBERATE EXCLUSION (full account in the completeness meta-test's
 * header): `onModInputChange` builds its intent name at runtime via
 * `modInputCommand(dataMode)`, not a `name: '<literal>'`, so it can't
 * appear in a source-literal parse and is NOT one of the 67 below — its
 * 4-value domain is pinned separately by `$lib/radio/mod-input.test.ts`.
 * MOR-1567's prose calls out "the mod-input command" as in its scope, so
 * add a case for it there — do not fold it into this map, which would
 * perturb the pinned 87/67/20 baseline MOR-1426 measured.
 */

/** One-line justification for an unclaimed intent or keyboard action. */
export interface Waiver {
  /** Why no conformance case exists yet. */
  readonly reason: string;
  /** Linear ticket id that owns closing this waiver. */
  readonly owner: string;
}

/** Tags every name in `names` with the same waiver — one family, one call. */
function tag(names: readonly string[], waiver: Waiver): Record<string, Waiver> {
  return Object.fromEntries(names.map((name) => [name, waiver]));
}

const TX = { reason: 'TX-chain family walk not yet landed', owner: 'MOR-1564' } as const;
const VOX_CW = { reason: 'VOX/CW family walk not yet landed', owner: 'MOR-1565' } as const;
const SCOPE_VFO = { reason: 'Scope-remainder/VFO-topology family walk not yet landed', owner: 'MOR-1566' } as const;
const SWEEPER = { reason: 'Remainder-sweeper family walk not yet landed', owner: 'MOR-1567' } as const;

/**
 * The (67 - 9 - 5 - 4 = 49) intents with no conformance assertion, tagged
 * with the family child that owns closing them. MOR-1562 (C8, adapter-seam
 * parity) intentionally claims ZERO entries here — its scope is
 * `get*Handlers`/`derive*Props`/`get*Armed` SEAMS, not new intent names.
 *
 * MOR-1560 (C6)'s 9 DSP intents and MOR-1561 (C7)'s 5 filter/PBT intents
 * landed and were moved to `CLAIMED_INTENTS` in `./claimed.ts` — see
 * `../mor1560-dsp-family-conformance.isolated.test.ts` and
 * `../mor1561-filter-pbt-family-conformance.isolated.test.ts`. MOR-1563
 * (C9)'s keyboard walk additionally landed the FIRST real coverage for 4
 * intents that otherwise belong to MOR-1566/MOR-1567's still-unwalked
 * families (`set_data_mode`, `vfo_swap`, `vfo_equalize`, `set_scope_hold`)
 * — moved out of those two families' tags below per this file's own
 * burn-down rule (a landed `expectFrames` claims the intent regardless of
 * which walk lands it first); MOR-1566/MOR-1567 will extend coverage on
 * these four (more call sites, more profiles), not initiate it.
 */
export const WAIVED_INTENTS: Readonly<Record<string, Waiver>> = {
  // MOR-1560 (C6) — DSP: NR/NB/notch/AGC time constant — CLAIMED, see
  // `./claimed.ts` and `../mor1560-dsp-family-conformance.isolated.test.ts`.
  // MOR-1561 (C7) — Filter, PBT and IF-shift — CLAIMED, see `./claimed.ts`
  // and `../mor1561-filter-pbt-family-conformance.isolated.test.ts`.
  // MOR-1564 (C10) — TX chain — 8
  ...tag([
    'set_rf_power', 'set_mic_gain', 'set_compressor', 'set_compressor_level',
    'set_monitor', 'set_monitor_gain', 'set_drive_gain', 'set_tuner_status',
  ], TX),
  // MOR-1565 (C11) — VOX + CW — 12
  ...tag([
    'set_vox', 'set_vox_gain', 'set_anti_vox_gain', 'set_vox_delay',
    'set_cw_pitch', 'set_key_speed', 'set_break_in', 'set_break_in_delay',
    'set_apf', 'set_twin_peak', 'cw_auto_tune', 'set_dash_ratio',
  ], VOX_CW),
  // MOR-1566 (C12) — scope remainder + VFO topology — 12 (set_scope_hold/
  // vfo_swap/vfo_equalize CLAIMED by MOR-1563's keyboard walk, see above)
  ...tag([
    'set_scope_mode', 'set_scope_edge', 'set_scope_dual',
    'set_scope_during_tx', 'set_scope_center_type', 'set_scope_vbw',
    'set_scope_rbw', 'switch_scope_receiver',
    'set_dual_watch', 'set_main_sub_tracking', 'quick_dualwatch', 'quick_split',
  ], SCOPE_VFO),
  // MOR-1567 (C13) — sweeper — 14 named in its own prose (set_data_mode
  // CLAIMED by MOR-1563's keyboard walk, see above) + 3 orphaned RIT/XIT
  // (see file header) — 17
  ...tag([
    'scan_start', 'scan_stop', 'scan_set_df_span', 'scan_set_resume',
    'set_dial_lock', 'set_powerstat', 'speak', 'set_antenna_1', 'set_antenna_2',
    'set_rx_antenna_ant1', 'set_rx_antenna_ant2',
    'set_digisel', 'set_ip_plus', 'memory_clear',
    'set_rit_status', 'set_rit_tx_status', 'set_rit_frequency',
  ], SWEEPER),
};

/** Pinned so a removal (or an undocumented addition) shows up in review. */
export const WAIVED_INTENTS_COUNT = 49;

/**
 * `dispatchKeyboardRadioAction` case labels with no conformance assertion.
 * MOR-1563 (C9) walked and CLAIMED all 28 that were waived here (17
 * dispatch, 11 refuse on the IC-7300 fixture — see `./claimed.ts`'s
 * `CLAIMED_KEYBOARD_ACTIONS` and
 * `../mor1563-keyboard-fanout-conformance.isolated.test.ts`, including the
 * MOR-1577 finding on `adjust_af_level`/`adjust_rf_gain`). This map is
 * now empty; kept (rather than deleted) as the live burn-down target for
 * any future keyboard action this ledger's completeness test would
 * otherwise fail on.
 *
 * ON THE PARENT TICKET'S "32 actions" FIGURE (still relevant context): it
 * is 29 radio-family cases in `dispatchKeyboardRadioAction` (all 29 now
 * claimed) PLUS 3 non-radio actions (`adjust_tuning_step`,
 * `open_filter_settings`, `focus_target`) that live in a DIFFERENT switch
 * (`makeKeyboardHandlers().dispatch`, panel-commands.ts:1588) which
 * `dispatchKeyboardRadioAction` falls through to on `false`. This ledger
 * (MOR-1556) names `dispatchKeyboardRadioAction` specifically, so those 3
 * are a deliberate SCOPE BOUNDARY, not a count discrepancy — they are
 * absent from `KEYBOARD_RADIO_ACTIONS` and out of scope here by design,
 * not because they were miscounted.
 */
export const WAIVED_KEYBOARD_ACTIONS: Readonly<Record<string, Waiver>> = {};

/** Pinned so a removal (or an undocumented addition) shows up in review. */
export const WAIVED_KEYBOARD_ACTIONS_COUNT = 0;
