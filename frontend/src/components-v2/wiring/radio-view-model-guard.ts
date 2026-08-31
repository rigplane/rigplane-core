/**
 * Dev-only runtime guard for the MOR-1062 radio view model (MOR-2040).
 *
 * `validateRadioViewModel` (`semantic/radio-view-model.ts`) is the contract's
 * own structural + cross-field check — exercised on every fixture in
 * `semantic/__tests__/` and on every adapter output in
 * `lib/runtime/adapters/__tests__/radio-view-model-adapter.test.ts` — but
 * until now nothing reachable from a running app called it. Production got
 * only the compile-time return-type annotation on `toRadioViewModel`: real
 * (MOR-1065 ruling 2), but it cannot catch a value that type-checks yet
 * violates an invariant the type system can't express (an unsafe cast, a
 * careless spread, a field the adapter forgot to strip).
 *
 * `lib/runtime/adapters/**` cannot value-import `semantic/` without a new
 * eslint exception (invariant 1 — see `eslint.config.js`'s adapters block),
 * so the check cannot live next to `toRadioViewModel` itself. `components-v2/
 * wiring/` carries no such ban: `eslint.config.js` has no `files` block
 * naming this directory, and `SemanticRadioSurfaces.svelte` already
 * value-imports a real function (`keyBlockedReasons`) from
 * `semantic/rx-tx-surface` alongside many semantic Svelte components — so
 * the guard lives here instead, wrapping the adapter's output at the one
 * seam that hands it to those surfaces.
 *
 * That makes this guard live at exactly one of `toRadioViewModel`'s five
 * production call sites. The other four stay unguarded: the two in
 * `lib/runtime/adapters/panel-adapters.ts` and the one in
 * `lib/runtime/adapters/scope-adapter.ts` for the eslint-boundary reason just
 * given, and the one in `lib/media/media-session.ts` for a different reason —
 * its dedicated test suite (`lib/media/__tests__/media-session.isolated.test.ts`)
 * mocks `toRadioViewModel` outright and drives it with minimal two-key
 * doubles for that suite's own purposes. Those doubles are test scaffolding,
 * not evidence that a partial view model is a legal production shape there;
 * wiring the guard in would just break that suite instead of validating real
 * output, so this PR leaves the site unguarded rather than rewriting those
 * fixtures. `toRadioViewModel`'s return-type contract still applies at all
 * five call sites — only this runtime cross-check is not yet live at four of
 * them.
 *
 * `import.meta.env.DEV` is Vite's own dev/test-vs-production flag. Vitest
 * defaults `DEV` to `true`, so the check also runs under test — confirmed by
 * this file's own test throwing on a mangled fixture. The other direction
 * (dev-only, not merely
 * dev-preferred) is a build property, not something a `vitest` run can
 * observe: a one-off `vite build` for this change (MOR-2040 PR) found
 * neither `guardRadioViewModel` nor the validator's "Invalid radio view
 * model" error string anywhere under `dist/`, i.e. Vite's standard
 * `import.meta.env.DEV`-literal + dead-code-elimination treatment removed
 * this branch from the shipped bundle rather than merely skipping it at
 * runtime. That was a point-in-time check, not a standing CI gate — re-run
 * `npm run build && grep -r "Invalid radio view model" dist/` after touching
 * this file or the build config if that guarantee matters again.
 */
import { validateRadioViewModel, type RadioViewModel } from '../../semantic/radio-view-model';

/**
 * Pass-through: always returns `view` unchanged. `null` is a legitimate
 * `toRadioViewModel` result (nothing observed yet — see that function's own
 * doc comment) and is never handed to the validator, which treats `null` as
 * a structural failure; only a non-null shape can be "mangled". Re-throws
 * the validator's own `TypeError` when `view` fails a structural or
 * cross-field invariant.
 */
export function guardRadioViewModel(view: RadioViewModel | null): RadioViewModel | null {
  if (import.meta.env.DEV && view !== null) {
    validateRadioViewModel(view);
  }
  return view;
}
