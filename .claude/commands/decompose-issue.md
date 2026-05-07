# Decompose Issue

Break a large issue or epic into atomic, guardrail-compliant tasks.

## Input

`$ARGUMENTS`: GitHub issue number.

## When to use

- Issue exceeds guardrails (>3 files, >200 LOC)
- Issue is labeled "epic" or contains a task checklist
- `/scan-issues` identified it as too large for direct execution

## Pipeline: EXPLORE → DECOMPOSE → ENQUEUE

### Phase 1: EXPLORE

1. Fetch issue: `gh issue view $ARGUMENTS --json number,title,body,labels`
2. Read issue body fully — identify all requirements
3. Identify:
   - Components involved (backend / frontend / both)
   - Modules affected (map to `src/icom_lan/` structure)
   - Dependencies between requirements
   - Hardware requirements (if any)
4. Read relevant source files to assess scope

### Phase 2: DECOMPOSE

Break into 3–10 tasks. Each task MUST:

- Be independently executable and testable
- Fit guardrails: ≤3 files, ≤200 LOC, no architecture changes
- Have a clear expected outcome (not vague)
- Include estimated files and LOC

Quality checks:
- No vague tasks ("improve system", "refactor everything")
- No oversized tasks (>200 LOC)
- No tightly coupled pairs (task B unusable without task A's uncommitted code)
- Each task is meaningful on its own (not "rename variable")

Define execution order:
- Independent tasks can run in any order
- Dependent tasks must list their prerequisites

### Phase 3: OUTPUT

Write to `.claude/workflow/decomposition.md`:

```markdown
# Decomposition: #N — <title>

## Parent issue: #N
## Total tasks: X
## Date: YYYY-MM-DD

| ID | Description | Files (est) | LOC (est) | Depends on | Type |
|----|-------------|-------------|-----------|------------|------|
| T1 | ... | 2 | ~80 | — | feat |
| T2 | ... | 1 | ~40 | T1 | test |
| T3 | ... | 2 | ~60 | — | feat |

## Execution order
1. T1, T3 (parallel — independent)
2. T2 (after T1)

## Notes
<any context needed for execution>
```

### Phase 4: ENQUEUE

Append tasks to `.claude/queue/queue.json` with:

```json
{
  "number": "N-T1",
  "title": "task description",
  "type": "feature",
  "difficulty": "low",
  "requires_hardware": false,
  "score": 4,
  "status": "pending",
  "parent_issue": N,
  "decomposition": ".claude/workflow/decomposition.md",
  "reason": "decomposed from #N"
}
```

## Failure handling

If decomposition fails (issue too ambiguous, unclear scope):
- Mark issue as BLOCKED in queue
- Log reason to `.claude/knowledge/failures.md`
- Classification: `decomposition_failed`

## Rules

- Do NOT execute any tasks — only decompose and enqueue
- Do NOT modify source code
- Do NOT create tasks that violate guardrails
- Executor must never process the parent epic directly — only decomposed tasks
- Each task must reference parent issue for traceability
