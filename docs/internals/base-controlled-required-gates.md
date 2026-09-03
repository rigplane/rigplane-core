# Base-controlled required-gate migration

Issue [#3136](https://github.com/rigplane/rigplane-core/issues/3136) removes a
circular trust boundary: a pull request can currently modify the same
`pull_request` workflow or helper that publishes its required `quick` or
`Agent Review Gate` success.

## Stage 1 topology

Stage 1 adds two non-required contexts and leaves branch protection unchanged.

| Context | Trusted producer | Candidate execution | Stage 1 required |
| --- | --- | --- | --- |
| `quick` | legacy `quick.yml` or docs-only API publisher | legacy PR workflow | yes, app 15368 |
| `Agent Review Gate` | legacy PR/comment workflow | API-only, but legacy PR source is candidate-controlled | yes, app 15368 |
| `quick-v2` | base-owned `pull_request_target` controller | separate self-hosted job with `contents: read`, no persisted credential, and no referenced secrets | no |
| `Agent Review Gate v2` | base-owned `pull_request_target`/comment controller | none; API metadata only | no |

The v2 quick controller reads complete pull-request file metadata through the
API, fetches its versioned policy from the exact PR base SHA, and records the
exact head and base. Documentation-only changes finish on a GitHub-hosted
API-only path. Other ready changes use the existing build-tier carrier after
checking out `refs/pull/<number>/merge` without persisted credentials and
verifying that its first and second parents are the admitted base and head.
Only the later GitHub-hosted publisher has `statuses: write`; it rereads the PR
before publishing success.

The review v2 controller fetches the review parser from the exact PR base SHA,
reads comments through the API, and rereads the PR before publishing. Neither
controller evaluates code from the candidate revision in a write-token job.
Fork PRs follow the same API and merge-ref path with a read-only worker token.

These security controls are immutable in place:

- `.github/workflows/quick-v2.yml`
- `.github/workflows/agent-review-gate-v2.yml`
- `.github/scripts/base-controlled-gates-v1.test.js`
- `.github/scripts/base-gate-policy-v1.js`
- `.github/scripts/quick-v2-worker-v1.sh`
- `.github/scripts/agent-review-gate.js`

The base-owned worker compares each candidate file byte-for-byte with the
trusted base before running candidate CI controls. A PR cannot weaken the
controller, publisher, policy, worker, or parser that evaluates it. A future
gate change must add a new version and non-required context, observe it, and
switch protection separately; it must not edit these controls in place while
v2 is required.

## Promotion checklist

1. Merge stage 1 through the existing required `quick` and
   `Agent Review Gate` contexts. Do not change branch protection in that PR.
2. Observe a natural documentation-only PR. Confirm exactly one `quick-v2`
   commit status from GitHub Actions app ID `15368`, no v2 self-hosted worker,
   an exact head/base binding, and the expected review-v2 failure then success
   around a fresh exact-head review directive.
3. Observe a natural code or CI-control PR. Confirm the base-controlled worker
   runs, its token is read-only, its merge parents equal the admitted base and
   head, and exactly one final `quick-v2` status is published by app `15368`.
4. Independently exercise and record a failing carrier, a superseded/cancelled
   run, a head or base race, and a fork PR. Failure and stale-state cases must
   never publish success; the fork must run with no secrets and no write token.
5. Recheck the live branch-protection payload and the observed status app IDs.
   Only then replace the required checks in one separate coordinator action:

   ```bash
   gh api --method PATCH \
     repos/rigplane/rigplane-core/branches/main/protection/required_status_checks \
     --input - <<'JSON'
   {
     "strict": false,
     "checks": [
       {"context": "Agent Review Gate v2", "app_id": 15368},
       {"context": "quick-v2", "app_id": 15368}
     ]
   }
   JSON
   ```

6. Immediately read branch protection back and open or update a harmless PR to
   confirm both new required contexts resolve. Keep the legacy producers in
   place during this confirmation.
7. In stage 2, after the rule switch is proven, retire the legacy publishers,
   candidate-controlled admission helpers, and duplicated old contexts in a
   separate reviewed PR.

## Rollback and lockout recovery

If either v2 context is absent, ambiguous, stale, or published by an unexpected
app, restore the old required checks before changing workflow code:

```bash
gh api --method PATCH \
  repos/rigplane/rigplane-core/branches/main/protection/required_status_checks \
  --input - <<'JSON'
{
  "strict": false,
  "checks": [
    {"context": "Agent Review Gate", "app_id": 15368},
    {"context": "quick", "app_id": 15368}
  ]
}
JSON
```

Read the protection payload back after rollback. Because stage 1 does not
remove or rename the old producers, this restores the pre-migration merge path
without a workflow commit. If stage 2 has already retired them, first restore
the old protection payload under administrator authority, then revert the
stage-2 retirement through an ordinary PR; do not manufacture statuses or
temporarily accept an unbound context.
