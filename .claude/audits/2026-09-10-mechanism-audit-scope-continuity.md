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

## Subsequent installed evidence and follow-up

The PASS above remains the point-in-time mechanism verdict for the audited
revision. It established that the implementation met the then-reviewed
frequency-current display contract. It did not establish installed acceptance
under sustained alternating tuning.

A later installed IC-7300 acceptance run kept one arrow visible for all 80
sampled observations. A longer run with 140 alternating key actions instead
entered a continuous missing-marker/passband tail after about 25 seconds.
Recorded producer evidence changed to stale 23 ms before the first missing DOM
sample, which strongly aligns the observed-frequency freshness boundary with
the disappearance. Frequency freshness returned 2.49 seconds later, while the
DOM remained absent for at least another 10.059 seconds. Inspection of
`scope-passband-display.ts:projectScopePassbandDisplay` found that continued
same-context frequency/axis changes can keep advancing recovery floors after
the initial retirement, preventing recapture while alternation continues.

MOR-2439 separately owns the producer readback gap that allowed the qualified
frequency/mode/filter/DATA tuple to become stale. An in-progress MOR-2437
frontend proposal would retain the last qualified tuple as an explicitly stale
display value during that bounded same-context gap. It preserves the existing
provider, capability, session epoch, receiver, slot, mode, filter, DATA,
frame-mode, span, band, null/OFF and 500 ms frame fences; it does not change TX,
command or resize authority. That proposal is not yet implemented, reviewed,
or accepted on an installed build, so it does not amend the archived PASS or
clear the subsequent installed failure.

That historical proposal subsequently completed its gates in PR #3433. Exact
head `9758269e32ca74d855357cf8ba614218dec23d57` passed independent review
and merged as `4503010041e3e35949e18eb0522fbd2f87356d8e`; the reviewed and
merged trees both resolve to
`ae65825c110052e8a6df1f0d941d3ad79ddaaa94`. The implementation adds one
live `frequencyHoldRecovery` flag to the existing projector. It holds only
the last qualified tuple as explicitly stale and prevents recovery floors from
ratcheting while fresh evidence catches up. It adds no cache or authority
owner, changes no Core TTL, and changes no TX, command or resize authority.
The independent exact-head review passed, 302 focused projector and mounted
component tests passed, canonical quick run `34480785976` passed in 5m16s,
and the required blocking visual check passed in 1m12s.

Installed acceptance then exercised 140 native Right/Left key actions at
250 ms intervals. Across 604 DOM samples there were zero missing markers or
passbands. The paired API stream contained 264 samples during tuning,
including 37 stale-frequency samples. The accepted run also kept PTT false,
the KEY control enabled at full opacity, and the removed READY row at zero
height. A separate wheel run recorded nine samples with zero missing overlays
or errors. This closes the installed MOR-2437 display-continuity regression
for the exercised keyboard and wheel scenarios.

This evidence does not establish fully accepted tuning UX or causal physical
input provenance. The same 140 balanced key actions produced 140 enqueues with
zero command errors, but the initial 14,101,000 Hz became a final target and
readback of 14,102,000 Hz; the first wrong absolute target was command 7.
At that point, the numeric intent drift remained the existing MOR-1864 issue,
In Progress and High priority, rather than a change to this audit's original
mechanism verdict. MOR-2439 still owns producer freshness coverage, and
MOR-2440 separately owns recovery after a genuine hard retirement.

MOR-1864 subsequently completed its own independent gate in PR #3434. Exact
head `c356a85ab24389efe1b8320d1061035001e4b8ed` merged as
`08b650d3fb24207abf760f96a7346c5aab13efee` with equal tree
`0e4e4aba0359e5b96904c7f44e5504aa4442bf74`. Independent review, 469
tests, canonical quick run `34485949152`, visual CI, and installed
keyboard/wheel acceptance passed. The installed 140-action keyboard run
enqueued all 70 `[S+1000,S]` pairs exactly and returned to fresh S with zero
missing overlays; the ten-action wheel run also returned to fresh S with zero
missing overlays or command errors.

That later MOR-1864 acceptance remains separate from this audit's original
scope-continuity verdict. Its readback association intentionally cannot prove
that a physical dial observation equal to a still-relevant accepted target was
caused by that command. MOR-2439 and MOR-2440 remain separate Backlog work.
