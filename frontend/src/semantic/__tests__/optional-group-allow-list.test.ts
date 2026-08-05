/**
 * MOR-1244 pin round (`mor-1244-verify.md` §8, finding N1) — the top-level
 * allow-list must agree with the read set.
 *
 * `validateRadioViewModel`'s `exactKeys` allow-list is a plain string-array
 * literal; nothing before this pin proved that every key in it is actually
 * read/validated. The verifier's V2 mutant demonstrated the gap: adding a
 * stray key (`'txAuxLegacy'`) to the allow-list, with NO corresponding
 * validator or read, passed all 322 targeted tests untouched — a garbage
 * value under that key validated cleanly, because nothing ever looked at
 * it. Reproduced below (see the build report's "Pin round" section for the
 * mutate/kill/restore/sha256 proof).
 *
 * This file derives the REAL allow-list straight from the shipped
 * function's own source (`Function.prototype.toString()` + a narrow regex
 * on the one `exactKeys(v, [...], '$')` call `validateRadioViewModel`
 * makes) rather than hand-duplicating it — a hand-duplicated copy is
 * exactly the kind of thing that silently drifts and stops catching
 * anything. Two independent checks:
 *
 *  1. the derived list matches a maintained "expected" set exactly — this
 *     alone kills a stray-key mutant like the verifier's, regardless of
 *     whether the stray key happens to be read;
 *  2. EVERY non-required key in the *derived* list — whatever it is, no
 *     per-family list to keep in sync for this half — throws when given a
 *     garbage (non-object) value. This is the "declared but unread" catch:
 *     a future family that updates check #1's expected set but forgets to
 *     wire its validator fails check #2 automatically, with no edits here.
 *
 * Recipe for each of the 12 remaining MOR-1262 family slices: add your
 * group's key to `EXPECTED_OPTIONAL_GROUP_KEYS` below. Nothing else in this
 * file needs to change.
 */
import { describe, expect, it } from 'vitest';
import { validateRadioViewModel } from '../radio-view-model';
import { topologyFixtures } from '../fixtures/topologies';

const REQUIRED_KEYS = [
  'topologyId', 'vfoScheme', 'activeReceiver', 'vfos', 'split', 'dualWatch',
  'txTarget', 'txPermit', 'scope', 'disabledReasons',
] as const;

/** MOR-1244: txAux. MOR-1262 slice 2A: meters. Slice 3A: rxAudio. Slice 4A:
 *  modeFilter. Slice 4A′: filterPassband. Slice 5A: dsp. Add your key here
 *  when your family's slice lands. */
const EXPECTED_OPTIONAL_GROUP_KEYS = [
  'txAux', 'meters', 'rxAudio', 'modeFilter', 'filterPassband', 'dsp',
] as const;

function extractTopLevelAllowList(): string[] {
  // `.toString()` on a Vite/esbuild-transformed function returns the SSR-
  // transformed body: the call becomes `(0,__vite_ssr_import_N__.exactKeys)
  // (v, [...], "$")` (module-wrapped, double-quoted, pretty-printed one
  // entry per line) rather than the literal source text — the regex allows
  // for the wrapper prefix, either quote style, and embedded newlines.
  const source = validateRadioViewModel.toString();
  const match = source.match(/exactKeys[^(]*\(v,\s*\[([\s\S]*?)\]\s*,\s*["']\$["']\)/);
  if (!match) {
    throw new Error(
      'optional-group-allow-list pin: could not locate the top-level '
      + "exactKeys(v, [...], '$') allow-list in validateRadioViewModel's "
      + 'source — has its call shape changed? Update the regex above.',
    );
  }
  return match[1]
    .split(',')
    .map((entry) => entry.trim().replace(/^['"]|['"]$/g, ''))
    .filter((entry) => entry.length > 0);
}

const base = topologyFixtures['1/single'];

describe('validateRadioViewModel top-level allow-list agrees with the read set (MOR-1244 pin, N1)', () => {
  it('the shipped allow-list contains exactly the required keys plus the declared optional groups', () => {
    const allowList = extractTopLevelAllowList();
    expect(new Set(allowList)).toEqual(new Set([...REQUIRED_KEYS, ...EXPECTED_OPTIONAL_GROUP_KEYS]));
  });

  // Derived from the REAL source, not from EXPECTED_OPTIONAL_GROUP_KEYS — if
  // a stray key is added to the shipped allow-list, it appears here too,
  // and the it.each below automatically exercises it.
  const declaredOptionalKeys = extractTopLevelAllowList()
    .filter((key) => !(REQUIRED_KEYS as readonly string[]).includes(key));

  it('at least one declared optional key was found by the source-derivation harness (sanity on the harness itself)', () => {
    expect(declaredOptionalKeys.length).toBeGreaterThan(0);
  });

  it.each(declaredOptionalKeys)(
    'a garbage (non-object) value under the declared optional key "%s" throws — proves it is read, not merely allow-listed',
    (key) => {
      const model = { ...base, [key]: 'garbage-not-an-object' };
      expect(() => validateRadioViewModel(model)).toThrow(TypeError);
    },
  );
});
