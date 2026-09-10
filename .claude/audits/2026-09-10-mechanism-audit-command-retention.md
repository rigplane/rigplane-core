# Command retention mechanism audit

Audited revision: `50c053f3db8c5b03ddafbb5c976ee71ce7e6d9c4` against `30619db65839fa877fa171e08cd3061b1b9bb6f8`.

Method: `.claude/skills/mechanism-audit/SKILL.md`. Independent read-only collection and adjudication; no tests or product edits performed by the auditors. Verdict: **PASS**, limited to mechanism ownership/duplication, not installed acceptance.

## Inventory and liveness

`command_service.py` AST census: 63 function/method definitions, seven module constants and ten `CommandService` instance slots. The changed ledger fields, lifecycle emission and purge functions have live consumers. `CommandService` is the shared definition site consumed by Web, rigctld and runtime command ingress.

`_pending_value_for_intent` has one definition and zero literal production/test callers and predates the change. It is private and absent from `__all__`; the inspected Pro repository has no literal reference. Dynamic/downstream consumption remains undetermined.

## Prior rulings and steelman

`docs/plans/2026-08-14-state-backed-command-lifecycle.md` states: "Transport acceptance is never radio confirmation." Safety semantics remain separate from generic command lifecycle.

The strongest case for the current arrangement wins: ACK retention belongs in shared Core, where all ingresses use the same ledger. Reusing existing overlay/readback windows avoids inventing another timeout or declaring ACK to be physical confirmation. Queue coalescing, frontend pending display and retained callback history serve distinct purposes.

## Deletions

None certified. The pre-existing `_pending_value_for_intent` candidate remains **undetermined** because dynamic/downstream guards cannot conclusively establish deletability. It does not block this change. Do not merge a potentially dead helper into active behavior.

## Consolidations

None required. `CommandService.emit_lifecycle` / `_purge_expired` are **already shared** and reuse the existing pending/readback windows. Ownership is correct; consolidation is not actionable. Depends on: none. Confidence: high. Falsifier: a second live implementation accounting for the same active command ledger or a consumer treating bookkeeping retirement as confirmed execution.

## Cleared

- Shared active ACK bookkeeping and reuse of existing confirmation metadata windows.
- Separation from queue event coalescing and frontend pending-state presentation.
- Retained late-callback history is live, not a duplicate active ledger.

## Weakest link

The private helper's downstream liveness is unknown, so no deletion is certified. Also, `_events` remains append-only history used by execution results, lifecycle inspection and `_last_event`: the patch bounds active bookkeeping, **not total command-history memory**. That pre-existing limitation is outside this change.
