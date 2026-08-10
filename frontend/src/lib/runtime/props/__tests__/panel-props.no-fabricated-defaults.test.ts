/**
 * A11 (MOR-1409, Core #2317) — static ownership scan + frozen-fact regression
 * pins for the batch-A projection fix in `panel-props.ts`.
 *
 * Companion to `panel-props.test.ts`'s per-function behavioral RED tests.
 * This file asserts two things a per-function unit test cannot:
 *
 *  1. none of the SPECIFIC fabricated-default literal source patterns the
 *     A11 re-anchor plan's live audit named (14.074 MHz / USB / FIL1 / 2400
 *     Hz / AGC MID / one antenna) remain anywhere in `panel-props.ts`'s
 *     source text — a belt-and-suspenders sweep on top of the behavioral
 *     tests, so a mutant that restores the literal in a place no existing
 *     behavioral test happens to probe is still caught;
 *  2. two frozen facts this gate must NOT disturb: `panel-adapters.ts` still
 *     makes zero `runtime.send()` calls (§3.4 of the plan — it only reads
 *     `runtime.state`/`runtime.caps`), and `RadioLayout.svelte` still has
 *     exactly the same two documented `send()` call sites (correction
 *     5241395868's "only two production callers remain" premise).
 */
import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { describe, expect, it } from 'vitest';

const PANEL_PROPS_PATH = resolve(__dirname, '../panel-props.ts');
const PANEL_ADAPTERS_PATH = resolve(__dirname, '../../adapters/panel-adapters.ts');
const RADIO_LAYOUT_PATH = resolve(
  __dirname,
  '../../../../components-v2/layout/RadioLayout.svelte',
);

function readSource(path: string): string {
  return readFileSync(path, 'utf8');
}

/**
 * Slices a single `export function <name>(...) { ... }` body out of the
 * module source, up to (not including) the next top-level `export`. Scoping
 * the scan to one function at a time is deliberate: several batch-B/A12
 * functions this gate must NOT touch (`toCwProps`, `toAudioSpectrumProps`)
 * share IDENTICAL fallback source text with batch-A functions this gate DOES
 * fix (e.g. `filterWidth: rx?.filterWidth ?? 2400,` appears verbatim in both
 * `toFilterProps`, an A11 owner, and `toAudioSpectrumProps`, an A12 owner) —
 * a whole-file substring scan cannot tell those apart without producing a
 * false positive against code this gate must leave alone.
 */
function functionBody(source: string, name: string): string {
  const start = source.indexOf(`export function ${name}(`);
  if (start === -1) throw new Error(`function ${name} not found in panel-props.ts`);
  const next = source.indexOf('\nexport ', start + 1);
  return source.slice(start, next === -1 ? source.length : next);
}

describe('panel-props.ts batch-A functions carry no fabricated-default literal (MOR-1409 A11)', () => {
  const source = readSource(PANEL_PROPS_PATH);

  // One row per batch-A function (plan §3.3) this gate fixes, and the exact
  // source substring the live re-anchor audit named as its plausible-looking
  // fabricated default. On exact base every one of these is present in its
  // named function; after the fix none may remain THERE — batch-B/A12
  // functions sharing the same substring elsewhere in the file are exempt
  // (they are a later gate's own owner, per the plan's function-level split).
  const forbidden: ReadonlyArray<readonly [fn: string, literal: string]> = [
    ['toVfoProps', 'freq: 14074000,'],
    ['toVfoProps', "mode: 'USB',"],
    ['toVfoProps', "filter: 'FIL1',"],
    ['toVfoProps', '?? 14074000'],
    ['toVfoProps', "?? 'USB'"],
    ['toBandSelectorProps', '?? 14074000'],
    ['toFilterProps', "?? 'USB'"],
    ['toFilterProps', '?? 2400'],
    ['toFilterProps', "?? ['FIL1', 'FIL2', 'FIL3']"],
    ['toAgcProps', 'agcMode: rx?.agc ?? 2,'],
    ['toAgcProps', 'agcModes: caps?.agcModes ?? [1, 2, 3]'],
    ['toModeProps', "currentMode: rx?.mode ?? 'USB',"],
    ['toAntennaProps', 'antennaCount: caps?.antennas ?? 1,'],
  ];

  it.each(forbidden)('%s does not contain %j', (fn, literal) => {
    expect(functionBody(source, fn)).not.toContain(literal);
  });

  // Companion positive checks: the batch-B/A12 siblings that share the same
  // fallback TEXT as a batch-A function above are explicitly confirmed
  // UNCHANGED — proving the scan above is scoped correctly and this gate did
  // not silently widen into A12's own owner functions.
  const stillPresentOutOfScope: ReadonlyArray<readonly [fn: string, literal: string]> = [
    ['toCwProps', "?? 'USB'"],
    ['toAudioSpectrumProps', '?? 2400'],
  ];

  it.each(stillPresentOutOfScope)(
    '%s (batch-B/A12, out of A11 scope) is untouched — still contains %j',
    (fn, literal) => {
      expect(functionBody(source, fn)).toContain(literal);
    },
  );
});

describe('frozen facts this gate must not disturb (MOR-1409 A11 regression pins)', () => {
  it('panel-adapters.ts makes zero runtime.send() calls (plan §3.4)', () => {
    const source = readSource(PANEL_ADAPTERS_PATH);
    expect(source).not.toContain('runtime.send(');
  });

  it('RadioLayout.svelte still has exactly the two documented send() call sites (correction 5241395868)', () => {
    const source = readSource(RADIO_LAYOUT_PATH);
    const matches = source.match(/runtime\.send\(/g) ?? [];
    expect(matches).toHaveLength(2);
    expect(source).toContain("runtime.send('set_scope_dual', { dual: !current });");
    expect(source).toContain("runtime.send('switch_scope_receiver', { receiver });");
  });
});
