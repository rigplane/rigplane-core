/**
 * Dev-only author-facing warning for MOR-2054.
 *
 * `designLanguageActivation` (`../workspace/activation.ts`) activates a
 * design language for a layout ONLY on an explicit `{compatible: true}`
 * entry in that language's `layoutCompatibility` for that layout id; any
 * other shape — no matching entry, or a matching `compatible: false` entry —
 * resolves to `null`, silently. A manifest whose `layoutCompatibility` has
 * no `compatible: true` entry AT ALL therefore can never activate for ANY
 * layout: every lookup `designLanguageActivation` ever performs against it
 * returns `null`. Nothing before this ticket told the author that.
 *
 * Owner ruling, 2026-08-31 (MOR-2054): keep the fail-closed behaviour —
 * `designLanguageActivation`'s return values are unchanged by this file —
 * but stop it being silent. Making the field mandatory was considered and
 * rejected.
 *
 * EMPTY VS. "NO TRUE ENTRY": `declaresNoLayoutCompatibility` below fires on
 * an empty `layoutCompatibility` array AND on a non-empty one containing
 * only `compatible: false` entries. Both shapes are the identical failure
 * under `designLanguageActivation`: `[].find(...)` and
 * `[{compatible:false}, ...].find((e) => e.layoutId === layoutId)` both
 * either miss or resolve to `compatible !== true`, so the language never
 * activates for any layout either way — there is no "compatible by default
 * unless excepted" fallback anywhere in `designLanguageActivation` for an
 * all-`false` list to fall back on. The ticket's own trap scenario is an
 * author reading `layoutCompatibility` as opt-out ("declare only your
 * exceptions") and expecting unlisted or excepted layouts to default to
 * compatible; a manifest that declares only `false` entries under that same
 * misreading hits exactly the same silent dead end as one that declares
 * nothing, so both must warn.
 *
 * WHERE THIS IS WIRED: `registerDesignLanguage` (`./contract.ts`) is the
 * one choke point every manifest passes through — the built-in families
 * (`./declarations.ts`) and any third-party skin's manifest alike — so
 * guarding there, once, covers every manifest that will ever
 * reach `designLanguageActivation`. Unlike `guardRadioViewModel`
 * (`components-v2/wiring/radio-view-model-guard.ts`, MOR-2040), no eslint
 * layering boundary forces this guard into a different directory than the
 * type and function it checks: `presentation/languages/` carries no import
 * restriction that `contract.ts` itself doesn't already satisfy, so this
 * file lives alongside it. The shape is otherwise the same: a thin,
 * dev-only, pass-through wrapper gated on `import.meta.env.DEV` (Vite's
 * dev/test-vs-production flag, defaulting to `true` under Vitest) around
 * the one production seam.
 *
 * PRODUCTION STRIPPING: a one-off `vite build` for this change (MOR-2054 PR)
 * found zero occurrences of this file's warning text or either exported
 * function name anywhere under `dist/` — the same `import.meta.env.DEV`-
 * literal + dead-code-elimination treatment MOR-2040 confirmed for
 * `guardRadioViewModel`. That was a point-in-time check, not a standing CI
 * gate — that guarantee is a property of the build config, not something a
 * `vitest` run can observe, so re-run `npm run build && grep -rn
 * "will not activate for any layout" dist/` after touching this file or the
 * build config if the guarantee matters again.
 */
import type { DesignLanguageManifest } from './contract';

/**
 * True iff `manifest.layoutCompatibility` contains no entry with
 * `compatible === true` — the exact condition under which
 * `designLanguageActivation` can never return this manifest's id, for any
 * layout. Pure; used both by `guardLayoutCompatibility` below and by the
 * repo-wide inventory test that checks every manifest actually shipped in
 * this repository.
 */
export function declaresNoLayoutCompatibility(manifest: DesignLanguageManifest): boolean {
  return !manifest.layoutCompatibility.some((entry) => entry.compatible === true);
}

/** Manifest ids already warned about, so a re-registration (e.g. HMR, or a
 *  test file re-registering the same id) does not spam the console. Warning
 *  state only — never consulted by `declaresNoLayoutCompatibility` or by
 *  activation, so it cannot change which manifests are flagged, only how
 *  many times each is reported. */
const warnedIds = new Set<string>();

/**
 * Pass-through: always returns `manifest` unchanged, and never throws.
 * In dev, warns once per manifest id when `declaresNoLayoutCompatibility`
 * is true. The message states only what `designLanguageActivation` actually
 * does today — no claim about future behaviour.
 */
export function guardLayoutCompatibility(manifest: DesignLanguageManifest): DesignLanguageManifest {
  if (import.meta.env.DEV && declaresNoLayoutCompatibility(manifest) && !warnedIds.has(manifest.id)) {
    warnedIds.add(manifest.id);
    console.warn(
      `[rigplane] design language "${manifest.id}" declares no layoutCompatibility entry with ` +
        'compatible: true — it will not activate for any layout. Declare the layouts it supports, ' +
        `e.g. layoutCompatibility: [{ layoutId: '<id>', compatible: true }].`,
    );
  }
  return manifest;
}
