# Process Next Issue

Select the highest-priority pending issue from the queue and run the full pipeline.

## Steps

1. Read `.claude/queue/queue.json`
2. Find the first issue with `"status": "pending"` (list is pre-sorted by score descending)
3. If no pending issues: run `/scan-issues` first, then retry
4. Set the issue to `"status": "processing"` in queue.json
5. Run `/solve-issue <number>` with the selected issue number

## Rules
- Process exactly ONE issue
- If queue is empty after scan, report "no actionable issues" and stop
- Never pick an issue that was previously FAILED (check history.json)
