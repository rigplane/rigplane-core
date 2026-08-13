/**
 * MOR-1556 — completeness ledger: CLAIMED intent registry.
 *
 * An intent name is "claimed" when a conformance case asserts the exact WS
 * frame `panel-commands.ts` dispatches for it (an `expectFrames(...)` call
 * in a `*-conformance.isolated.test.ts` file, per `./harness.ts`). This
 * file is that registry — the completeness meta-test
 * (`../../../commands/__tests__/panel-commands-completeness.test.ts`) unions
 * it with `./waived.ts` and asserts every intent `panel-commands.ts` can
 * emit falls in exactly one of the two sets.
 *
 * Convention for future family walks (C6-C13, MOR-1560..1567): when a walk
 * adds real `expectFrames` assertions for a previously-waived intent, add
 * its name here (one line) and DELETE the matching entry from
 * `WAIVED_INTENTS` in `./waived.ts` — the completeness test enforces both
 * (an intent claimed AND waived at once fails, same as one claimed nowhere).
 *
 * CLAIMED_INTENTS below are exactly the 20 distinct `sendCommand` frame
 * names asserted by `expectFrames` calls in
 * `../mor1428-ic7300-conformance.isolated.test.ts` (MOR-1428) as of this
 * writing — read off that file's `describe('... handler dispatch ...')`
 * block, not guessed.
 */

/** Intent names claimed by MOR-1428's IC-7300 fixture conformance suite. */
export const CLAIMED_INTENTS: ReadonlySet<string> = new Set([
  'set_mode',
  'set_filter',
  'set_band',
  'set_attenuator',
  'set_preamp',
  'set_rf_gain',
  'set_squelch',
  'set_agc',
  'set_nb',
  'set_nr',
  'set_af_level',
  'set_memory_mode',
  'memory_to_vfo',
  'memory_write',
  'set_split',
  'set_vfo',
  'set_freq',
  'set_scope_span',
  'set_scope_speed',
  'set_scope_ref',
]);

/**
 * `dispatchKeyboardRadioAction` case labels claimed by a conformance case.
 * Only `toggle_split` is asserted today (MOR-1428's "keyboard context"
 * case, over `dispatchKeyboardRadioAction({ action: 'toggle_split' })`).
 * MOR-1563 (C9) walks the remaining 28.
 */
export const CLAIMED_KEYBOARD_ACTIONS: ReadonlySet<string> = new Set([
  'toggle_split',
]);
