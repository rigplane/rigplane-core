# Audit: LCD UI

Analyze the LCD UI subsystem (frontend + backend integration) and create GitHub issues for real problems.

**Read-only workflow. Do NOT modify any source files.**

## Input

`$ARGUMENTS` (optional): focus area — `rendering`, `state-sync`, `websocket`, `audio`, `scope`, `controls`, or empty for full audit.

## Phase 1: SCOPE

Identify relevant files only — do NOT scan the entire codebase.

Frontend:
- `frontend/src/lib/components/lcd/` — LCD components
- `frontend/src/lib/stores/` — state stores
- `frontend/src/lib/types/` — type definitions
- `frontend/src/lib/utils/` — helpers used by LCD

Backend integration:
- `src/icom_lan/web/handlers/control.py` — REST endpoints serving LCD data
- `src/icom_lan/web/websocket.py` — WebSocket push (state updates)
- `src/icom_lan/web/radio_poller.py` — what gets polled
- `src/icom_lan/web/_delta_encoder.py` — state diff encoding
- `src/icom_lan/radio_state.py` — canonical state shape

If `$ARGUMENTS` specifies a focus area, narrow scope further:
- `rendering` → components only
- `state-sync` → stores + websocket + delta encoder
- `websocket` → websocket.py + frontend WS handler
- `audio` → audio components + audio handler
- `scope` → scope components + scope handler
- `controls` → interactive controls + REST handlers

Use subagents for large file reads — keep main session lean.

## Phase 2: ANALYZE

For each file in scope, check:

**UI issues:**
- Broken or missing elements (conditional rendering gaps)
- Inconsistent state rendering (shows stale value after update)
- UI state vs backend data mismatch (field names, types, units)
- Race conditions in UI updates (rapid state changes)

**Logic issues:**
- Missing edge cases (null, undefined, empty, disconnected)
- Incorrect state transitions
- Unhandled error paths

**Integration issues:**
- Frontend expects field that backend doesn't send
- Backend sends field that frontend ignores
- Stale data after reconnect
- Async timing (WebSocket message arrives before component mounted)

**Code quality (only if clearly problematic):**
- Duplicated logic across components
- Overly complex conditionals
- Unclear naming that causes bugs

## Phase 3: FINDINGS

Write to `.claude/workflow/audit-findings.md`:

```markdown
# LCD UI Audit — YYYY-MM-DD
## Focus: <area or "full">
## Files analyzed: N

### Finding F1: <title>
- **Type:** UI | logic | integration | quality
- **Severity:** low | medium | high
- **File:** path:line
- **Description:** what's wrong
- **Reproduction:** how to trigger
- **Confidence:** high | medium

### Finding F2: ...
```

## Phase 4: FILTER

Remove findings that are:
- Speculative (confidence < medium)
- Stylistic only (formatting, naming preference)
- Already tracked in existing issues (`gh issue list --search "<keyword>"`)
- Not actionable (no clear fix direction)

## Phase 5: GROUP

- Merge duplicate findings into single entries
- Cluster related findings (e.g., 3 components with same null-check gap → one issue)
- Prefer fewer high-quality issues over many small ones

## Phase 6: CREATE ISSUES

For each group, create a GitHub issue:

```bash
gh issue create --title "[LCD] <short description>" --body "$(cat <<'EOF'
## Problem

<what's wrong — 2-3 sentences>

## Impact

<what breaks or degrades for the user>

## Reproduction

<steps to trigger, or "code inspection">

## Location

<file:line references>

## Suspected cause

<1-2 sentences>

## Suggested direction

<optional — brief hint, not a full plan>

---
Source: `.claude/workflow/audit-findings.md` findings F1, F3
EOF
)" --label "bug,ui"
```

Label mapping:
- UI issues → `bug,ui`
- Logic issues → `bug`
- Integration issues → `bug,integration`
- Code quality → `refactor`

## Limits

- Max 20 issues per audit run
- Max 15 file reads in scope phase
- Prefer 5–10 high-signal issues over 20 noisy ones

## Rules

- Do NOT modify any source files
- Do NOT fix any issues found
- Do NOT create issues for things that are clearly intentional design choices
- Do NOT create duplicate issues — check existing issues first
- Every issue must reference specific file:line locations
- Every issue must be actionable (clear what needs to change)
