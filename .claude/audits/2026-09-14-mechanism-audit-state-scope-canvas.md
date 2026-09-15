# Mechanism audit: IC-7610 state, scope, and canvas stability

- Audited revision (exact HEAD): `de50644e9cd38a9041a68dbac2acafd8a1e767f3` (2026-09-14).
- Exact base: `601f27f71870e0f60e6872bb2c51495f97022cf3`.
- Ticket: MOR-2467.
- Final verdict: **PASS** — no blockers.
- Read-only throughout; independently adjudicated at the exact HEAD named above.

## Scope

The state producer and WebSocket delta path, client state reconstruction,
shared semantic radio-view projection, spectrum and audio-canvas scheduling,
station-meter continuity, and scope passband display/resize authority.

## Method

Read-only mechanism audit per `.claude/skills/mechanism-audit/SKILL.md`. The
auditor traced producers, consumers, writers, readers, and lifecycle owners;
searched for literal and dynamic alternate consumers; and compared the new
mechanisms with the existing canonical adapters and command path. All source
evidence below is frozen at the audited revision.

**Tests were not run by the audit.** Mac mini test, check, lint, build, live
RX-only sampling, and CPU measurements are separate implementation evidence.

## Deletions

No dead or vestigial mechanism introduced by this change was found.

## Consolidations

No blocking consolidation candidate was found.

The radio-state flow remains singular: `WebServer._broadcast_state_update`
coalesces updates before `DeltaEncoder`, the browser reconstructs the shallow
delta, and `setRadioState` accepts semantic, freshness, observation, and health
advances. `SemanticRadioSurfaces.svelte:projectRadioView` is the only
composition point that owns all inputs required by `toRadioViewModel`; sharing
that projection removes the previous repeated per-host work without creating
a competing canonical model.

Spectrum and audio canvases retain separate render contracts, but both use the
same appropriate lifecycle rule: schedule one animation frame after new data,
an option or size change, or visibility restoration. They no longer maintain
an unconditional animation loop.

## Authority and safety adjudication

`scope-passband-display.ts` confines provider-confirmed held-stale facts to the
scope display and resize projection. It does not feed transmit target, transmit
permission, PTT, ATU, or RF-state authority. Those remain independently
fail-closed in the TX capability path.

Immediately before a resize command is dispatched, `SpectrumPanel.svelte`
still rechecks provider generation, active receiver, observed frequency and
mode/data mode, selected filter, legal width rule, and current geometry.
Unrelated filter-shape, raw PBT, or scope-control polls no longer cancel an
otherwise valid drag. This is consistent with the pre-existing
`toSpectrumAuthority` policy that preserves observed stale readings as held
display facts.

## Residual non-blocking concern

The semantic-view signature still sorts and serializes the complete
`fieldStatus` map for each distinct received snapshot. This is materially
cheaper than the removed repeated full projections and continuous canvas
loops, so it is not a blocker for this tract. The next canonical optimization,
if measurements justify it, is a producer-side view-relevant revision or
per-path metadata delta rather than another frontend cache.

## Weakest link

The residual `fieldStatus` cost is the least completely cleared conclusion:
current evidence establishes that it is no longer the dominant cost, but does
not provide a long-duration allocation profile. Check this first if CPU rises
again after unrelated state-schema growth.

## Cleared

- State broadcast and client reconstruction have one owner each.
- Semantic radio-view projection is shared rather than repeated per surface.
- Spectrum and audio canvases have bounded, visibility-aware frame lifecycles.
- Station-meter surfaces remain mounted across freshness-only state updates.
- Confirmed held-stale scope facts remain presentation-only.
- Passband resize preserves all command-time identity and legal-width checks.
- TX, PTT, ATU, and RF authority remain fail-closed and independent.

## Verdict

**PASS.** The change repairs the mechanism at its ownership boundaries rather
than masking symptoms. No blocking deletion, displacement, or consolidation
finding remains at the audited revision.
