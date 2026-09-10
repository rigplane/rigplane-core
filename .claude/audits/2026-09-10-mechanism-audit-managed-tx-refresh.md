# Managed TX refresh mechanism audit

Audited revision: `f73f6a013d286f9db96253175caac53e6f5f2cc0` against `30619db65839fa877fa171e08cd3061b1b9bb6f8`.

Method: `.claude/skills/mechanism-audit/SKILL.md`. Independent read-only collection and adjudication; no tests or product edits performed by the auditors. Verdict: **BLOCKED — F1 required before merge**. No demonstrated TX authority bypass.

## Inventory and liveness

Lexical/AST census: `managed-app-host.ts` has eight function/arrow definitions and nine mutable locals; `managed-transmit.svelte.ts` has ten function/arrow definitions, three reactive variables and two revision counters; `RxTxSurface.svelte` has 16 reactive declarations. Both counters are read. App owns the host; semantic/TOT controls consume its facade. Both asynchronous document-ingestion paths have production consumers.

Document mutation is defined in `managed-transmit.svelte.ts:applyManagedTransmitSnapshot`, reached through `receiveManagedTransmitSnapshot`. Its production callers are `refreshManagedTransmit` and `setManagedTransmitTot`. GET consumers include the App host, PTT delivery refresh and managed-command follow-up. TOT is reached through the controller facade and TOT controls.

## Prior rulings and steelman

The accepted 2026-07-25 discovery/parity decisions in `docs/plans/2026-07-25-ui-composition-discovery-and-parity.md` assign one persistent controller to a browser tab and durable radio/de-key ownership to the managed-radio supervisor. `docs/internals/ui-radio-control-contract.toml` states that observed PTT remains StateStore-only. The approved change requires obsolete document responses to respect provider/control-session boundaries and removes idle READY presentation.

Browser refresh scheduling correctly belongs with tab/session lifecycle. The server continues owning actual TX operations. Scope display continuity, HTTP document acceptance and command accounting should not be merged into one generic confirmation mechanism. The flaw is narrower: the GET fence has not been adopted by the other live document producer.

## Deletions

None certified. `rx-tx-surface.ts:SESSION_LABEL.idle` is unreachable as displayed text after the idle guard, but its key participates in the exhaustive vocabulary test through `Object.keys(SESSION_LABEL)`. Do not blindly delete the key. Neither store revision counter is dead.

## Consolidations

### F1 — Incomplete migration of managed-document context fencing

- **Verdict:** B — missing common context-fenced acceptance/error surface inside the shared frontend store.
- **Rank:** diverged.
- **Elements:** `managed-transmit.svelte.ts:refreshManagedTransmit`, `setManagedTransmitTot`, `receiveManagedTransmitSnapshot`; `browser-dependencies.ts:createManagedBrowserDependencies`; `managed-controller.ts:ManagedTxController.refresh`.
- **Consumers:** GET serves App refresh, PTT delivery and managed-command follow-up; TOT serves the controller facade and TOT controls. Both paths are live.
- **Definition site:** GET fencing is defined in `refreshManagedTransmit`; document mutation in `applyManagedTransmitSnapshot`.
- **Divergence:** GET captures and checks context/request revisions. TOT accepts its awaited response directly and invalidates unconditionally on rejection. A late old-context success can clear stale state; a late rejection can invalidate a newer document. Timestamp ordering does not encode session identity.
- **Prior ruling:** approved all-obsolete-response context contract; browser/server ownership remains as recorded above.
- **In-flight:** GET fence exists; TOT has not adopted it. Store refresh invalidates and rethrows while browser/controller wrappers also invalidate. Duplicate invalidation calls are observed; harmful microtask interleaving was not executed in this audit.
- **Required surface:** one store-owned context acceptance/error rule for every asynchronous document producer, preserving operation-specific ordering and controller dependency semantics.
- **Depends on:** none.
- **Confidence:** high for TOT gap and duplicate error ownership; medium for the unexecuted wrapper-interleaving consequence.
- **Falsifier:** all TOT completions are context-fenced before mutation/invalidation, and obsolete refresh errors cannot invalidate newer accepted documents through production wrappers.
- **Fix class:** design/consolidate the existing store fence across its live ingestion paths.
- **Actionable:** yes, required before merge. The seam existed at baseline, but leaving it uncovered fails the approved scope. This is not a claim of TX authority bypass.

Verify delayed GET/TOT success and rejection across invalidation followed by a newer accepted document, including every production wrapper. Obsolete completion must neither publish nor invalidate the newer document.

### F2 — Revision counters: conditional redundancy

- **Verdict:** C — legitimately local context/request bookkeeping.
- **Rank:** parallel.
- **Elements / definition sites:** module-local `invalidationRevision` and `refreshRevision` in `managed-transmit.svelte.ts`, consumed by `invalidateManagedTransmit` and `refreshManagedTransmit`.
- **Consumers:** both counters are captured/read by GET refresh; invalidation increments both.
- **Divergence:** currently GET's request-revision comparison already rejects every invalidation rejected by its second comparison.
- **Prior ruling:** context boundaries must be preserved; no ruling requires deleting either counter.
- **In-flight:** F1's TOT consumer can give the context counter an independent purpose.
- **Required surface:** distinguish document-context identity from GET ordering after F1.
- **Depends on:** F1.
- **Confidence:** high.
- **Falsifier:** an independent context-counter consumer, including one added by F1.
- **Fix class:** none now.
- **Actionable:** no. Do not delete the counter before resolving F1, or merge unrelated host/session/gesture generations.

## Cleared

- App refresh coalescing and session cleanup are tab-local concerns.
- Idle READY visibility is presentation-only and changes no server authority.
- Store request ordering, host context revisions and controller gesture generations protect different lifetimes.

## Weakest link

F1's adverse wrapper-interleaving consequence was not executed. Controlled delayed rejection/new-context acceptance should test it first. The directly visible unfenced TOT success and rejection paths independently suffice to block this revision.

## Follow-up adjudication — 2026-09-10

Refresh-only revision `42746022fc17bac7ae98c7048bed8e5eed816952` against `c1030e09b6f7ea48da0b9c1a42aa7ea9c51f2648`: **PASS; F1 and F2 cleared** by an independent read-only delta review. The original blocked verdict above remains a point-in-time record.

`contextIsCurrent` now guards both GET and TOT success/error acceptance. Store-owned refresh failures are handled without rethrowing into wrapper invalidators. The controller publishes/rethrows TOT failures without a second invalidation; the dependency contract assigns that responsibility to the store. Context revision now has independent TOT consumers, while refresh revision orders GET requests. No new error class, authority owner or PTT freshness/admission gate was introduced.

Delta census: one helper added, zero state fields added, two live ingestion paths covered, one redundant invalidation removed, one dependency contract clarified. Production inspection confirms one managed-controller construction and one dependency factory. No new deletion/consolidation candidate.

Weakest link: this follow-up is source-level adjudication. The reviewer inspected regression tests but did not run them; test execution, CI and installed acceptance remain separate gates.
