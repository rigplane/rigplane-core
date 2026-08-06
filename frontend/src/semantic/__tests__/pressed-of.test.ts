/**
 * MOR-1358 — the shared `pressedOf` helper, extracted from `TxAuxSurface`
 * (MOR-1265, slice 1B) after three later slices (`DspSurface` 5B,
 * `RitXitScanSurface` 8B, `CwKeyerSurface` 9B) independently re-derived and
 * pinned the same rule, and a fourth (`RfFrontEndSurface` 6B) shipped the
 * same inline shape unpinned.
 *
 * ONE test file for the rule, replacing the class of per-slice
 * `aria-pressed`-omission pins: on an UNOBSERVED reading, `pressedOf` must
 * return `undefined` (Svelte OMITS the attribute), never `false`
 * (`aria-pressed="false"` would fabricate a positively-known OFF claim about
 * a reading the radio never reported). Every migrated surface
 * (`TxAuxSurface`, `DspSurface`, `RitXitScanSurface`, `RfFrontEndSurface`,
 * `CwKeyerSurface`) now imports this one function instead of a local copy —
 * the per-surface component tests that already exercise their own DOM
 * wiring (e.g. `RitXitScanSurface.test.ts`'s "omits aria-pressed" cases,
 * `CwKeyerSurface.test.ts`'s "disables and refuses … while unobserved")
 * keep passing unmodified because the wiring — `aria-pressed={pressedOf(f)}`
 * — is unchanged; only the definition moved.
 */
import { readFileSync } from 'node:fs';
import { describe, expect, it } from 'vitest';
import { pressedOf } from '../pressed-of';
import type { Availability, TxAuxField } from '../radio-view-model';

const ON: Availability = { structural: true, operational: true };

const unread = <T>(): TxAuxField<T> => ({ reading: { status: 'unknown' }, availability: ON });
const known = <T>(value: T): TxAuxField<T> => ({ reading: { status: 'known', value }, availability: ON });

describe('pressedOf (MOR-1358)', () => {
  it('returns undefined for an unobserved reading — never false', () => {
    expect(pressedOf(unread<boolean>())).toBeUndefined();
  });

  it('returns true for a known plain-boolean true reading', () => {
    expect(pressedOf(known(true))).toBe(true);
  });

  it('returns false for a known plain-boolean false reading', () => {
    expect(pressedOf(known(false))).toBe(false);
  });

  // The `AtuStatus` three-state enum (`'off' | 'on' | 'tuning'`) is the
  // reason the comparison is `!== false && !== 'off'` rather than a plain
  // boolean cast — TxAuxSurface's verbatim ATU shape.
  it('returns false for a known enum reading of "off"', () => {
    expect(pressedOf(known('off'))).toBe(false);
  });

  it.each(['on', 'tuning'] as const)('returns true for a known enum reading of "%s"', (value) => {
    expect(pressedOf(known(value))).toBe(true);
  });

  /** verify-MOR-1358 F1. `CwKeyerSurface` is SAFETY-CRITICAL (MOR-1310) and
   *  its "imports nothing but the fact contract" allow-list was widened to
   *  admit `./pressed-of`. That allow-list regexes `CwKeyerSurface.svelte`'s
   *  OWN specifiers only — it cannot see one level down — so the premise the
   *  widening rests on ("pure, dependency-free, imports only a type") was
   *  unpinned. This is that pin. Kills: any value import added here, which
   *  would become a runtime edge from the safety-critical surface into
   *  whatever it reaches (the TX controller, the transport, the permit
   *  utility) without any existing test noticing. */
  it('has no runtime import — the safety allow-lists that name it depend on this', () => {
    const source = readFileSync('src/semantic/pressed-of.ts', 'utf8');
    const statements = [...source.matchAll(/^import\b[^;]*;/gm)].map((m) => m[0]);
    expect(statements.length).toBeGreaterThan(0);
    for (const statement of statements) expect(statement.startsWith('import type ')).toBe(true);
    for (const forbidden of ['import(', 'require(']) expect(source).not.toContain(forbidden);
  });
});
