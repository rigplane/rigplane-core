# Scope continuity mechanism audit

Audited revision: `0cba091492d9c08f3e1173e222db9c05e6581af2` against `30619db65839fa877fa171e08cd3061b1b9bb6f8`; merged revision `d017c514b3fa34b9c87c5958e2328cf323bd6373`.

Method: `.claude/skills/mechanism-audit/SKILL.md`. Independent read-only collection and adjudication; no tests or product edits performed by the auditors. Verdict: **PASS**, limited to mechanism ownership and duplication, not installed acceptance.

## Inventory and liveness

Lexical/AST census: `scope-passband-display.ts` contains 17 function-like definitions, 10 types/interfaces and four module constants. `SpectrumPanel.svelte` contains 24 function-like definitions, 56 reactive declarations and seven types. No definition-only local candidate was identified.

The projector is defined in `frontend/src/lib/runtime/adapters/scope-passband-display.ts:projectScopePassbandDisplay`, consumed by `SemanticRadioSurfaces.svelte`, and reaches `SpectrumPanel.svelte` through the scope projection. Display qualification is defined in `display-observation.ts`; strict spectrum authority in `scope-adapter.ts:toSpectrumAuthority`; canvas geometry in `spectrum-renderer.ts` and `spectrum-logic.ts`. Importing these primitives is not an independent implementation.

## Prior rulings and steelman

`docs/internals/ui-radio-control-contract.toml` separates canonical metadata from scope pixels and renderer buffers. The approved behavior is observed-frequency translation of retained display geometry within the same context; command/resize authority remains strict.

The strongest case for the current location wins: display retention requires renderer/frame context that Core does not own. A shared universal freshness mechanism would conflate observation truth, visual continuity and command permission. No competing live projector was found.

## Deletions

None certified. No deletion recommendation depends on absent literal references alone.

## Consolidations

None required. `projectScopePassbandDisplay` is **C — legitimately local**; display qualification and renderer geometry are **already shared**. Ownership is correct and consolidation is not actionable. Dependency: none. Confidence: high. Falsifier: a second live projector performing the same retention/translation for the same consumer, or a path that uses retained display geometry as command authority.

## Cleared

- Same-context observed-frequency display retention and CENTER-axis translation.
- Shared display qualification, frame-silence constant and renderer geometry.
- Separation of stale display continuity from strict resize and command authority.

## Weakest link

The consumer census is lexical/AST plus inspected callers, not proof against arbitrary downstream dynamic access. Inspect any newly supplied extension consumer before extending the no-duplication verdict beyond this revision.
