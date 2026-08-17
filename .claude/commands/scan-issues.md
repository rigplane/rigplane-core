# Scan and Score GitHub Issues

Scan open GitHub issues and populate the issue queue with scored candidates.

## Steps

1. Fetch open issues:
   ```
   gh issue list --state open --json number,title,body,labels --limit 30
   ```

2. For each issue, compute a priority score:

   ```
   score = 0
   + 2 if has reproduction steps or stacktrace
   + 2 if small scope (single file or function mentioned)
   + 1 if test exists or is easy to add
   + 1 if labeled "bug"
   - 2 if ambiguous (no clear ask)
   - 2 if touches many files or modules
   - 3 if requires hardware (integration test, real radio)
   - 1 if labeled "enhancement" or "feature"
   ```

3. Classify each issue:
   - **type**: bug | feature | refactor | question
   - **difficulty**: low | medium | high
   - **requires_hardware**: true | false

4. Filter and route:
   - If issue exceeds guardrails (>3 files, >400 LOC, or labeled "epic") → add to queue with `"status": "needs_decomposition"` and `"reason": "exceeds guardrails — use /decompose-issue N"`
   - Otherwise keep if: type is bug OR (type is refactor AND difficulty is low), difficulty is low or medium, requires_hardware is false, score > 0

5. Sort by score descending

6. Write to `.claude/queue/queue.json`:
   ```json
   {
     "version": 1,
     "issues": [
       {
         "number": 123,
         "title": "...",
         "type": "bug",
         "difficulty": "low",
         "requires_hardware": false,
         "score": 5,
         "status": "pending",
         "reason": "clear repro, single file fix"
       }
     ],
     "last_updated": "2026-04-10T..."
   }
   ```

7. Report summary: total scanned, qualified, top 5 candidates with scores.

## Rules
- Do NOT process any issues — only scan and score
- Do NOT modify any source code
- One issue per session for processing (use `/solve-issue N` after scanning)
- Update queue atomically (write complete file, not partial updates)
