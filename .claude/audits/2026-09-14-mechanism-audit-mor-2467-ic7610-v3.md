# Mechanism audit: MOR-2467 — IC-7610 Standard v3 restoration

- Audited revision (exact HEAD): `479dd5871a78bffacc504a143b27a97b4f6c84b0` (2026-09-14).
- Exact base: `99f4bcd35cbad73955d9d99ab21d9db191392a8f`.
- Ticket: MOR-2467.
- Final verdict: **PASS** — no blockers.
- Read-only throughout; independently adjudicated at the exact HEAD named above.

## Scope

IC-7610 Standard v3 restoration: restoration of MAIN/SUB records and one
combined RF/SQL control.

## Method

Read-only mechanism audit per `.claude/skills/mechanism-audit/SKILL.md`,
executed on the exact HEAD `479dd5871a78bffacc504a143b27a97b4f6c84b0` with
base `99f4bcd35cbad73955d9d99ab21d9db191392a8f`. All evidence below is frozen
at that revision; `file:symbol` citations are point-in-time and are not
maintained afterwards.

**Tests were not run by the audit.** The audit is read-only by contract; test
execution and remote test evidence are separate from this adjudication and are
not claimed here.

## Findings preserved at adjudication

### D1 — `frontend/src/lib/stores/capabilities.svelte.ts: vfoLabel`: dead

Deletion candidate. `vfoLabel` is a tests-only dead production shim that is
past its removal date. Separating it from the restoration: this is a standalone
cleanup, not part of the IC-7610 v3 change.

### F1 — `frontend/src/lib/stores/panel-commands.ts`: main_sub slot residue

Follow-up. The `main_sub` slot residue remains:

- `supportsVfoSlot`'s `main_sub` branch is unreachable;
- `currentTuningContext` fails closed;
- `currentMemorySnapshot` still requires `activeSlot`/`vfoA`/`vfoB`, so memory
  features may remain unreachable for a real slot-less IC-7610.

### F2 — `frontend/src/lib/stores/capabilities.svelte.ts`: stale comment

Follow-up. A comment in `capabilities.svelte.ts` still claims that IC-7610 and
IC-9700 have per-receiver A/B, which no longer matches the restored topology.

### F3 — state-schema documentation: slot view described as canonical

Optional follow-up. The state-schema documentation still describes the slot
view as canonical, although MAIN/SUB snapshots are slot-less.

## Cleared

- TX normalization remains fail-closed.
- Fixtures now represent the MAIN/SUB topology.
- RF/SQL has one owner with projections; no conflicting parallel
  implementation remains.

## Verdict

**PASS.** No blockers. F1/F2/F3 are follow-ups and D1 is a separate deletion
cleanup; none blocks the MOR-2467 restoration at the audited revision. As
above, no tests were run by this read-only audit; remote test evidence is
separate.
