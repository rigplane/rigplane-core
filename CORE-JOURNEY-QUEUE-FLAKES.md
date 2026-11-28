# CORE-JOURNEY-QUEUE-FLAKES

Status: offline implementation complete; remote verification pending.
PR: none. Pushed head: none. Uncommitted changes remain on the verified
`claude/journey-queue-flakes` branch, based on
`d0b09bc4dc2bdb12d6d0f4cfd2ec1abd36371b7e` (#4855).

Changed four test files. Added regression cases before extending the shared
passband readiness helper to require optional expected bounds in both painted
and latest diagnostics. The FIX journey waits for those bounds before measuring
and for actual sample count after its retained one-second observation. The mixed
journey uses the shared render-aware navigation/click helpers and preserves the
configured Chromium flags when adding mute-audio. Existing assertions and their
thresholds remain. No product changes.

Confirmed causes of red checks are unavailable: network/log access is prohibited.
[46080562380](https://github.com/rigplane/rigplane-core/actions/runs/46080562380)
was supplied with an IC-7300 `toBeGreaterThanOrEqual` failure;
[46099649032](https://github.com/rigplane/rigplane-core/actions/runs/46099649032)
was supplied with the MOR-3513 test title only. Neither includes a job URL or
actual failing log here. Readiness gaps found in source are candidate causes,
not confirmed diagnoses. Details and evidence limitations are in `NOTES.md`.

Tests run: none, locally or remotely. Run URLs produced: none. Offline Node
syntax checks passed for all four changed TypeScript files; `git diff --check`
passed. Builder checklist self-review found no P0/P1 issue. Type-checking and
independent review remain pending.

Next: route runner commits/pushes; dispatcher resolves the Linear owner, reads
both job logs, runs remote focused Vitest and TypeScript checks, confirms
RED/GREEN, repeats both target journeys 20x on the Mac mini, and opens the PR.
The regression cases predictably fail against the old helper, but were not run.

This report stays inside the worktree under the final no-outside-files rule;
neither `/tmp` nor the shared notes directory was written. Keep both report files
out of the commit.
