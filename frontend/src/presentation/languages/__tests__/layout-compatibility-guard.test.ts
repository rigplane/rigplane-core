/**
 * MOR-2054 — `layoutCompatibility` is a manifest's own declaration of which
 * layouts it can activate under (`designLanguageActivation`,
 * `../../workspace/activation.ts`, MOR-1081). A manifest whose
 * `layoutCompatibility` holds no `compatible: true` entry can never
 * activate for any layout, and until this ticket nothing said so.
 *
 * Manifest ids below are namespaced `mor-2054-guard-*` and never registered
 * (these tests call `guardLayoutCompatibility` directly, not through
 * `registerDesignLanguage`) — deliberately distinct from `fixtures.ts`'s
 * `validManifest()` default id (`testline`) and from the real families, so
 * this file's assertions cannot be affected by, or affect, what sibling
 * files in the shared `fast` pool (`vite.config.ts`, `isolate: false`)
 * register under those ids.
 */
import { describe, expect, it, vi } from 'vitest';
import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { declaresNoLayoutCompatibility, guardLayoutCompatibility } from '../layout-compatibility-guard';
import { validManifest } from './fixtures';

describe('declaresNoLayoutCompatibility', () => {
  // The literal trap scenario: an author leaves the field empty.
  it('is true for an empty layoutCompatibility array', () => {
    const manifest = validManifest({ id: 'mor-2054-guard-empty', layoutCompatibility: [] });
    expect(declaresNoLayoutCompatibility(manifest)).toBe(true);
  });

  // The "opt-out" misreading: the author declares only exceptions, expecting
  // unlisted/excepted layouts to default to compatible. designLanguageActivation
  // has no such default, so this hits the identical dead end as the empty case.
  it('is true when every entry is compatible: false', () => {
    const manifest = validManifest({
      id: 'mor-2054-guard-all-false',
      layoutCompatibility: [
        { layoutId: 'dual-receiver-cockpit', compatible: false },
        { layoutId: 'desktop-v2', compatible: false, reason: 'not ready yet' },
      ],
    });
    expect(declaresNoLayoutCompatibility(manifest)).toBe(true);
  });

  // At least one compatible: true entry means designLanguageActivation CAN
  // return this manifest's id for that layout — not a trap.
  it('is false when at least one entry is compatible: true', () => {
    const manifest = validManifest({
      id: 'mor-2054-guard-has-true',
      layoutCompatibility: [{ layoutId: 'desktop-v2', compatible: true }],
    });
    expect(declaresNoLayoutCompatibility(manifest)).toBe(false);
  });

  // Mixed case: a real false opt-out alongside a real true entry is a normal,
  // intentional manifest (this is fieldline's actual shape) — must stay false.
  it('is false for a mix of compatible: false and compatible: true entries', () => {
    const manifest = validManifest({
      id: 'mor-2054-guard-mixed',
      layoutCompatibility: [
        { layoutId: 'dual-receiver-cockpit', compatible: false, reason: 'too dense' },
        { layoutId: 'desktop-v2', compatible: true },
      ],
    });
    expect(declaresNoLayoutCompatibility(manifest)).toBe(false);
  });
});

describe('guardLayoutCompatibility', () => {
  it('passes a manifest through unchanged (same reference), whether or not it warns', () => {
    const empty = validManifest({ id: 'mor-2054-guard-passthrough-empty', layoutCompatibility: [] });
    const normal = validManifest({
      id: 'mor-2054-guard-passthrough-normal',
      layoutCompatibility: [{ layoutId: 'desktop-v2', compatible: true }],
    });
    expect(guardLayoutCompatibility(empty)).toBe(empty);
    expect(guardLayoutCompatibility(normal)).toBe(normal);
  });

  it('warns once, naming the manifest id, for a manifest that declares nothing', () => {
    const warn = vi.spyOn(console, 'warn').mockImplementation(() => {});
    const manifest = validManifest({ id: 'mor-2054-guard-warns', layoutCompatibility: [] });

    guardLayoutCompatibility(manifest);

    expect(warn).toHaveBeenCalledTimes(1);
    expect(warn.mock.calls[0][0]).toContain('mor-2054-guard-warns');
    expect(warn.mock.calls[0][0]).toContain('will not activate for any layout');
    warn.mockRestore();
  });

  it('stays silent for a normal manifest that declares at least one compatible: true entry', () => {
    const warn = vi.spyOn(console, 'warn').mockImplementation(() => {});
    const manifest = validManifest({
      id: 'mor-2054-guard-silent',
      layoutCompatibility: [{ layoutId: 'desktop-v2', compatible: true }],
    });

    guardLayoutCompatibility(manifest);

    expect(warn).not.toHaveBeenCalled();
    warn.mockRestore();
  });

  // "One-time": kills a regression that re-warns on every call for the same
  // id (e.g. a re-registration during HMR, or a sibling test file
  // re-registering the same id under the shared `fast`-pool registry).
  it('warns only once per manifest id across repeated calls', () => {
    const warn = vi.spyOn(console, 'warn').mockImplementation(() => {});
    const manifest = validManifest({ id: 'mor-2054-guard-dedup', layoutCompatibility: [] });

    guardLayoutCompatibility(manifest);
    guardLayoutCompatibility(manifest);
    guardLayoutCompatibility({ ...manifest }); // same id, different object identity

    expect(warn).toHaveBeenCalledTimes(1);
    warn.mockRestore();
  });

  // Proves the guard is actually called from registerDesignLanguage — not
  // just correct in isolation. Without this, a future edit could drop the
  // `guardLayoutCompatibility(...)` call from `contract.ts` and every test
  // above would keep passing while production went back to silent.
  it('is wired into registerDesignLanguage', () => {
    const source = readFileSync(resolve(process.cwd(), 'src/presentation/languages/contract.ts'), 'utf8');
    expect(source).toMatch(/registry\.set\(\s*manifest\.id,\s*guardLayoutCompatibility\(\s*manifest\s*\)\s*\)/);
  });
});
