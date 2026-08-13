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
 * Provenance: family assignment came from the MOR-1426 decomposition's
 * per-family "Work" lists, cross-checked against the actual
 * `dispatchRadioIntent` call sites grouped by factory. `makeRitXitHandlers`'
 * 3 intents were never named in ANY of MOR-1560..1567's prose (a genuine
 * hole in the family table, not a parsing mistake) — they landed on MOR-1567
 * as the closing sweeper; see `./claimed.ts`'s MOR-1567 paragraph.
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

/**
 * MOR-1560 (C6)'s 9 DSP intents, MOR-1561 (C7)'s 5 filter/PBT intents,
 * MOR-1564 (C10)'s 8 TX-chain intents, MOR-1565 (C11)'s 12 VOX/CW intents,
 * MOR-1566 (C12)'s 12 scope-remainder/VFO-topology intents, and MOR-1567
 * (C13)'s 17 remainder-sweeper intents (the 14 named in its own prose +
 * the 3 orphaned RIT/XIT intents this file previously documented as a
 * genuine gap in MOR-1426's per-family prose — `set_rit_status`,
 * `set_rit_tx_status`, `set_rit_frequency`) all landed and moved to
 * `CLAIMED_INTENTS` in `./claimed.ts` — see that file's header for the
 * per-walk breakdown and each walk's own `*-conformance.isolated.test.ts`
 * for the fixture-derived dispatch/refusal split. MOR-1563 (C9)'s keyboard
 * walk additionally landed the FIRST real coverage for 4 intents that
 * otherwise belonged to MOR-1566/MOR-1567's families (`set_data_mode`,
 * `vfo_swap`, `vfo_equalize`, `set_scope_hold`) — per this file's own
 * burn-down rule (a landed `expectFrames` claims the intent regardless of
 * which walk lands it first). MOR-1562 (C8, adapter-seam parity)
 * intentionally claimed ZERO entries here — its scope was
 * `get*Handlers`/`derive*Props`/`get*Armed` SEAMS, not new intent names.
 *
 * This map is now EMPTY (0 of the original 67 walked-family intents
 * remain) — MOR-1567's own acceptance criterion, and the closing state of
 * the whole MOR-1426 Tier-2 conformance program's `WAIVED_INTENTS`
 * ledger. Kept (rather than deleted) as the live burn-down target for any
 * future intent this ledger's completeness test would otherwise fail on.
 */
export const WAIVED_INTENTS: Readonly<Record<string, Waiver>> = {};

/** Pinned so a removal (or an undocumented addition) shows up in review. */
export const WAIVED_INTENTS_COUNT = 0;

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
