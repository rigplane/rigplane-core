/**
 * MOR-1556 — completeness ledger: CLAIMED intent registry.
 *
 * An intent name is "claimed" when a conformance case asserts the exact WS
 * frame `panel-commands.ts` dispatches for it (an `expectFrames(...)` call
 * in a `*-conformance.isolated.test.ts` file, per `./harness.ts`). This
 * file is that registry — the completeness meta-test
 * (`../../../commands/__tests__/panel-commands-completeness.test.ts`) unions
 * it with `./waived.ts` and asserts every LITERAL-NAMED intent
 * `panel-commands.ts` dispatches (i.e. every `dispatchRadioIntent({ name:
 * '<literal>', ... })` call site) falls in exactly one of the two sets.
 * The 4 `modInputCommand(...)`-derived names are real emissions outside
 * this 87-name literal universe — see `./waived.ts`'s header and the
 * meta-test's "dynamic mod-input call site" block for how those are
 * tracked instead.
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

/**
 * Intent names claimed by MOR-1428's IC-7300 fixture conformance suite,
 * plus MOR-1560's (C6) DSP family walk
 * (`../mor1560-dsp-family-conformance.isolated.test.ts`) — 9 intents:
 * `set_nr_level`, `set_nb_level`, `set_nb_depth`, `set_nb_width`,
 * `set_auto_notch`, `set_manual_notch`, `set_manual_notch_width`,
 * `set_notch_filter`, `set_agc_time_constant`. Every one of the 9 is
 * claimed via `expectRefusal` (not `expectFrames`) — the real IC-7300
 * fixture never observed any of these DSP sub-parameter leaves, so honest
 * fail-closed refusal IS the conformant behavior on this profile (see that
 * file's header for the fixture-derived evidence per intent).
 *
 * Plus MOR-1561's (C7) filter/PBT family walk
 * (`../mor1561-filter-pbt-family-conformance.isolated.test.ts`) — 5 intents:
 * `set_filter_width`, `set_filter_shape`, `set_if_shift`, `set_pbt_inner`,
 * `set_pbt_outer`. NOT uniform like C6: `set_filter_shape`/`set_if_shift`/
 * `set_pbt_inner`/`set_pbt_outer` are claimed via `expectRefusal` (each
 * genuinely unobserved/undeclared on this fixture), but `set_filter_width`
 * is claimed via a real `expectFrames` — `onFilterPresetChange` dispatches
 * it on this fixture through a gate (`main.filter`, observed) that is
 * DIFFERENT from the field the intent itself writes (`main.filterWidth`,
 * unobserved) — see that file's header for the full finding and red-first
 * evidence.
 */
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
  // MOR-1560 (C6) — DSP family walk
  'set_nr_level',
  'set_nb_level',
  'set_nb_depth',
  'set_nb_width',
  'set_auto_notch',
  'set_manual_notch',
  'set_manual_notch_width',
  'set_notch_filter',
  'set_agc_time_constant',
  // MOR-1561 (C7) — Filter, PBT and IF-shift family walk
  'set_filter_width',
  'set_filter_shape',
  'set_if_shift',
  'set_pbt_inner',
  'set_pbt_outer',
]);

/** Pinned so a removal (or an undocumented addition) shows up in review. */
export const CLAIMED_INTENTS_COUNT = 34;

/**
 * `dispatchKeyboardRadioAction` case labels claimed by a conformance case.
 * `toggle_split` was claimed by MOR-1428's "keyboard context" case (over
 * `dispatchKeyboardRadioAction({ action: 'toggle_split' })`). MOR-1563 (C9)
 * walked the remaining 28 in
 * `../mor1563-keyboard-fanout-conformance.isolated.test.ts` — 18 genuinely
 * DISPATCH on this fixture, 10 REFUSE (each case names its firing gate; see
 * that file's header for the full per-action table and the MOR-1454-related
 * findings on the cycle/step cases). `WAIVED_KEYBOARD_ACTIONS` in
 * `./waived.ts` is now empty — this ledger's keyboard half is closed.
 */
export const CLAIMED_KEYBOARD_ACTIONS: ReadonlySet<string> = new Set([
  'toggle_split',
  // MOR-1563 (C9) — keyboard action fan-out walk, 28 actions
  'tune', 'band_select', 'mode_select', 'cycle_data_mode', 'cycle_filter',
  'cycle_preamp', 'cycle_att', 'cycle_agc', 'toggle_nr', 'toggle_nb',
  'toggle_auto_notch', 'toggle_ip_plus', 'toggle_rit', 'toggle_xit',
  'clear_rit_xit', 'adjust_af_level', 'adjust_rf_gain', 'toggle_monitor',
  'vfo_swap', 'vfo_equalize', 'switch_active_vfo', 'set_active_vfo',
  'toggle_dial_lock', 'scope_span_step', 'scope_ref_step',
  'scope_toggle_hold', 'scope_toggle_dual', 'scope_toggle_fst',
]);

/** Pinned so a removal (or an undocumented addition) shows up in review. */
export const CLAIMED_KEYBOARD_ACTIONS_COUNT = 29;
