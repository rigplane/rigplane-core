/**
 * MOR-1556 (C2) — conformance completeness ledger.
 *
 * 67 of panel-commands.ts's 87 distinct dispatched intent names have no
 * conformance assertion (MOR-1428/MOR-1555 claims 20), and nothing made
 * that visible — the exact failure shape the whole MOR-1426 program exists
 * to kill (green tests, dead controls). This meta-test is the visibility:
 * every intent name the command layer can emit, and every
 * `dispatchKeyboardRadioAction` case label, must be either claimed
 * (`../../adapters/__tests__/conformance/claimed.ts`) or explicitly waived
 * with an owning ticket (`.../conformance/waived.ts`) — adding a new
 * intent or case label without touching one of those two files fails.
 *
 * SOURCE PARSING, AND ITS FRAGILITY: an exported runtime registry
 * (panel-commands.ts declaring its own intent list) was rejected — every
 * intent is already a literal at its one call site, so a second list
 * would exist only to satisfy this test. Instead this file parses the
 * SOURCE TEXT (`fs.readFileSync`, never imported/executed) for two shapes:
 *
 *   1. `dispatchRadioIntent({ name: '<intent>', ... })` — `INTENT_CALL_RE`.
 *      Requires `name:` to be the FIRST property (true at all 119 literal
 *      call sites today, including the multi-line one at ~line 877-880) —
 *      a call putting `params` before `name` (`{ params: {...}, name: 'x'
 *      }`) does NOT match this regex, or `INTENT_CALL_HEADER_RE` below.
 *      That shape is NOT silently invisible, though: the "total
 *      accounting" describe block asserts every `dispatchRadioIntent(`
 *      occurrence in the file is either a literal match or a known-dynamic
 *      call, so a params-first (or any other unparseable) call site fails
 *      that check loudly instead of slipping past every other assertion
 *      unclaimed and unwaived. Handles both quote styles and any
 *      whitespace/newlines before the string (`\s` is newline-inclusive).
 *
 *   2. `case '<action>':` inside `dispatchKeyboardRadioAction`'s switch
 *      body only — sliced between that function and the next
 *      `export function` (`makeKeyboardHandlers`), so that function's OWN
 *      switch (`adjust_tuning_step`/`open_filter_settings`/`focus_target`)
 *      is deliberately excluded — see `waived.ts`'s header. The
 *      `KEYBOARD_RADIO_ACTIONS` set literal (the dispatcher's actual entry
 *      gate) is parsed independently and checked for set-equality against
 *      these case labels, so an action added to the set with no matching
 *      `case` — a silent dead control, since the gate passes and the
 *      switch falls to `default` — fails loudly instead of never
 *      surfacing (it would produce no `case` label to claim or waive).
 *
 * DYNAMIC NAME, VERIFIED AND HANDLED EXPLICITLY: one call site,
 * `onModInputChange`, computes its name at runtime via
 * `modInputCommand(dataMode)` — not a literal, so `INTENT_CALL_RE`
 * correctly skips it and it is not counted toward 87/67/20. Its 4-value
 * domain is pinned separately by `$lib/radio/mod-input.test.ts`; the
 * `describe('dynamic mod-input call site...')` block below asserts the
 * source still contains EXACTLY one such non-literal call, so a second
 * one introduced later fails loudly instead of silently escaping both
 * this parse and mod-input.test.ts's coverage.
 *
 * KNOWN OVER-STRICT FALSE POSITIVES (would fail this suite even though
 * production behavior is fine — none exist in the source today, but both
 * are plain string/regex parsing against text, not an AST, so neither is
 * filtered): a `dispatchRadioIntent(...)` call sitting inside a `//` or
 * `/* * /` comment counts toward the total in the "total accounting"
 * check; and a `case '<label>':` inside a SECOND, nested `switch`
 * statement within `dispatchKeyboardRadioAction`'s body (there isn't one
 * today) would count toward `extractKeyboardActions` and
 * `KEYBOARD_RADIO_ACTIONS` set-equality as if it were a top-level case.
 */
import { describe, expect, it } from 'vitest';
import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import {
  CLAIMED_INTENTS,
  CLAIMED_KEYBOARD_ACTIONS,
} from '../../adapters/__tests__/conformance/claimed';
import {
  WAIVED_INTENTS,
  WAIVED_INTENTS_COUNT,
  WAIVED_KEYBOARD_ACTIONS,
  WAIVED_KEYBOARD_ACTIONS_COUNT,
} from '../../adapters/__tests__/conformance/waived';

// `import.meta.url` isn't a reliable `file://` URL under every vitest
// transform pipeline in this repo — resolve from the repo-relative path
// instead, anchored on `process.cwd()` (always `frontend/` for this suite).
const PANEL_COMMANDS_PATH = resolve(process.cwd(), 'src/lib/runtime/commands/panel-commands.ts');
const SOURCE = readFileSync(PANEL_COMMANDS_PATH, 'utf8');

/** Every `dispatchRadioIntent({ name: '<literal>', ... })` call site. */
const INTENT_CALL_RE = /dispatchRadioIntent\(\s*\{\s*name:\s*(['"])([A-Za-z0-9_]+)\1/g;

/**
 * `dispatchRadioIntent({ name:` header, without requiring what follows —
 * walked by hand below to classify literal vs. dynamic call sites. (A
 * single regex with a `(?!['"])` lookahead was tried first and rejected:
 * `\s*` before the lookahead backtracks to a zero-width match, so the
 * lookahead sees a plain space instead of the quote and false-positives on
 * every literal call — a backtracking pitfall worth documenting.)
 */
const INTENT_CALL_HEADER_RE = /dispatchRadioIntent\(\s*\{\s*name:/g;

function extractSourceIntents(source: string): Set<string> {
  const names = new Set<string>();
  for (const match of source.matchAll(INTENT_CALL_RE)) names.add(match[2]);
  return names;
}

/** Call sites whose `name:` value is not a plain quoted string literal. */
function findDynamicIntentCalls(source: string): number[] {
  const indices: number[] = [];
  for (const match of source.matchAll(INTENT_CALL_HEADER_RE)) {
    let i = match.index + match[0].length;
    while (i < source.length && /\s/.test(source[i])) i++;
    if (source[i] !== "'" && source[i] !== '"') indices.push(match.index);
  }
  return indices;
}

/** Slice bounding `dispatchKeyboardRadioAction`'s body — see file header. */
function extractKeyboardActionBody(source: string): string {
  const start = source.indexOf('export function dispatchKeyboardRadioAction');
  if (start === -1) throw new Error('dispatchKeyboardRadioAction not found — renamed or removed?');
  const nextFn = source.indexOf('\nexport function ', start + 1);
  if (nextFn === -1) throw new Error('no end boundary (next export function) after dispatchKeyboardRadioAction');
  return source.slice(start, nextFn);
}

const CASE_LABEL_RE = /case\s+(['"])([A-Za-z0-9_]+)\1\s*:/g;

function extractKeyboardActions(source: string): Set<string> {
  const names = new Set<string>();
  for (const match of extractKeyboardActionBody(source).matchAll(CASE_LABEL_RE)) names.add(match[2]);
  return names;
}

/**
 * The `KEYBOARD_RADIO_ACTIONS` set literal that gates
 * `dispatchKeyboardRadioAction`'s entry (`if (!KEYBOARD_RADIO_ACTIONS.has(
 * action)) return false;`) — parsed independently of the `case` labels so a
 * name added to the set with no matching `case` (a silent dead control:
 * the gate passes, the switch falls to `default`, nothing dispatches) is
 * detectable by set-equality against `extractKeyboardActions` below.
 */
function extractKeyboardRadioActionsSet(source: string): Set<string> {
  const start = source.indexOf('const KEYBOARD_RADIO_ACTIONS = new Set([');
  if (start === -1) throw new Error('KEYBOARD_RADIO_ACTIONS not found — renamed or removed?');
  const end = source.indexOf(']);', start);
  if (end === -1) throw new Error('KEYBOARD_RADIO_ACTIONS literal not closed with ]);');
  const names = new Set<string>();
  for (const match of source.slice(start, end).matchAll(/(['"])([A-Za-z0-9_]+)\1/g)) names.add(match[2]);
  return names;
}

/**
 * Shared completeness assertions, parameterized over intents vs. keyboard
 * case labels — the same seven checks both ledgers need (see MOR-1556's
 * acceptance criteria): every source name accounted for, no double-booking,
 * no stale waivers/claims, and both `*_COUNT` pins verified.
 */
function completenessSuite(opts: {
  label: string;
  sourceNames: Set<string>;
  claimed: ReadonlySet<string>;
  waived: Readonly<Record<string, unknown>>;
  waivedCount: number;
  claimedCount: number;
}): void {
  const { label, sourceNames, claimed, waived, waivedCount, claimedCount } = opts;
  const claimedAndWaived = new Set([...claimed, ...Object.keys(waived)]);

  describe(label, () => {
    it('extracted at least one name — parse sanity (zero means the regex broke)', () => {
      expect(sourceNames.size).toBeGreaterThan(0);
    });

    it('every name in the source is claimed or waived', () => {
      expect([...sourceNames].filter((n) => !claimedAndWaived.has(n))).toEqual([]);
    });

    it('no name is both claimed and waived (a landed walk must delete the waiver)', () => {
      expect([...claimed].filter((n) => n in waived)).toEqual([]);
    });

    it('every waived name still exists in source (no stale waivers)', () => {
      expect(Object.keys(waived).filter((n) => !sourceNames.has(n))).toEqual([]);
    });

    it('every claimed name still exists in source', () => {
      expect([...claimed].filter((n) => !sourceNames.has(n))).toEqual([]);
    });

    it('pinned waiver/claimed counts — a removal or undocumented addition shows in the diff', () => {
      expect(Object.keys(waived)).toHaveLength(waivedCount);
      expect(claimed.size).toBe(claimedCount);
    });

    it('claimed + waived accounts for every name, with no extras either side', () => {
      expect(claimedAndWaived.size).toBe(sourceNames.size);
    });
  });
}

completenessSuite({
  label: 'panel-commands.ts intent completeness ledger (MOR-1556)',
  sourceNames: extractSourceIntents(SOURCE),
  claimed: CLAIMED_INTENTS,
  waived: WAIVED_INTENTS,
  waivedCount: WAIVED_INTENTS_COUNT,
  claimedCount: 29,
});

completenessSuite({
  label: 'dispatchKeyboardRadioAction case-label completeness ledger (MOR-1556, owner MOR-1563)',
  sourceNames: extractKeyboardActions(SOURCE),
  claimed: CLAIMED_KEYBOARD_ACTIONS,
  waived: WAIVED_KEYBOARD_ACTIONS,
  waivedCount: WAIVED_KEYBOARD_ACTIONS_COUNT,
  claimedCount: 1,
});

describe('dynamic mod-input call site (verified, handled explicitly — MOR-1567 scope)', () => {
  const dynamicCalls = findDynamicIntentCalls(SOURCE);

  it('exactly one dispatchRadioIntent call has a non-literal name', () => {
    expect(dynamicCalls).toHaveLength(1);
  });

  it('that one dynamic call site is onModInputChange\'s modInputCommand(...) construction', () => {
    const [start] = dynamicCalls;
    expect(SOURCE.slice(start, start + 120)).toContain('modInputCommand(dataMode as number)');
  });
});

describe('total accounting (no unparseable dispatchRadioIntent shape slips through)', () => {
  // A call written `{ params: {...}, name: 'x' }` (params before name)
  // matches neither `INTENT_CALL_RE` (requires `name:` first) nor
  // `INTENT_CALL_HEADER_RE` (same requirement) — it would silently vanish
  // from BOTH the literal parse and the dynamic-call count, passing every
  // completeness assertion above while never being claimed or waived. This
  // is the total-accounting invariant that catches that shape (and any
  // other unparseable one): every `dispatchRadioIntent(` occurrence in the
  // file must be EITHER a literal match OR a known-dynamic call — no third
  // category.
  it('every call site is either a literal match or a known dynamic call', () => {
    const total = [...SOURCE.matchAll(/dispatchRadioIntent\(/g)].length;
    const literal = [...SOURCE.matchAll(INTENT_CALL_RE)].length;
    const dynamic = findDynamicIntentCalls(SOURCE).length;
    expect(total).toBe(literal + dynamic);
  });
});

describe('KEYBOARD_RADIO_ACTIONS set vs. case labels (no case-less dead control)', () => {
  // The dispatcher's entry gate is `KEYBOARD_RADIO_ACTIONS.has(action)` —
  // an action added to that set with no matching `case` passes the gate,
  // falls through the switch to `default`, and silently no-ops forever.
  // Set-equality against the parsed `case` labels catches it (and the
  // inverse: a `case` with no matching set entry, which is unreachable
  // since the gate would already have returned `false`).
  it('KEYBOARD_RADIO_ACTIONS and the switch\'s case labels are set-equal', () => {
    const declared = [...extractKeyboardRadioActionsSet(SOURCE)].sort();
    const cased = [...extractKeyboardActions(SOURCE)].sort();
    expect(declared).toEqual(cased);
  });
});
