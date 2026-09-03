# Base-controlled gate observation and authority migration

Issue [#3136](https://github.com/rigplane/rigplane-core/issues/3136) records a circular trust boundary: a pull request can
modify the same `pull_request` workflow or helper that publishes its required `quick` or `Agent Review Gate` success.
## Stage 1 is observation-only

Stage 1 leaves branch protection and the legacy publishers unchanged. The two
new commit-status contexts are telemetry and **must never be configured as
required checks**:

**Promotion prohibition: `*-observe` must never be configured as required checks.**

| Context | Producer | Candidate execution | Required |
| --- | --- | --- | --- |
| `quick` | legacy workflow or docs-only publisher | legacy PR workflow | yes, app 15368 |
| `Agent Review Gate` | legacy PR/comment workflow | API-only, with candidate-controlled PR source | yes, app 15368 |
| `quick-v2-observe` | base-owned API metadata observer | none | no |
| `Agent Review Gate v2 observe` | base-owned API-only observation workflow | none | no |

GitHub branch protection can bind a status/check to a context and app ID, but every repository workflow publishes as
GitHub Actions app ID `15368`. A candidate-controlled workflow can publish either observation context with the same
identity. Context plus app ID does not prove which workflow made the decision. The `*-observe` names make their
non-authoritative role explicit; no protection-switch command targets them.

The quick v2 workflow is metadata-only and schedules zero self-hosted jobs. It never checks out or executes pull-request
or fork code and never reads a candidate artifact, log, or cache. Its `pull_request_target` path initializes opened,
reopened, synchronized, ready, draft, and base-edited pull requests; title- and body-only edits are no-ops. Its
`workflow_run` path consumes completed runs from the canonical legacy `Tests (quick)` workflow and ignores main pushes.

The read-only producer and status publisher independently run `quick-v2-metadata-policy-v1.js: assess`. Each rereads
live `main`, the pull request, canonical workflow and check-suite identity, latest run/attempt, and complete paginated
job topology. Publication requires identical assessments and the still-current exact head.

`quick-v2-metadata-policy-v1.js: getTreeEntry` walks trusted base controls: parents must be `040000` trees and files
must be `100644` blobs. Missing, duplicate, truncated, executable, symlink, and gitlink entries fail closed. Candidate
trees are metadata-only and these entries must match the base blobs:

- `.github/workflows/quick.yml`
- `.github/scripts/classify-quick-paths.py`
- `.github/scripts/base-gate-policy-v1.js`
- `.github/workflows/quick-v2.yml`
- `.github/scripts/quick-v2-metadata-policy-v1.js`

The trusted base classifier predicts whether substantive `quick` must run. Workflow-level success with that job skipped
is unknown/failure. Stale head/base, fork, cancellation, changed control, incomplete pagination, ambiguous association,
non-current attempt, or route/job disagreement cannot publish success.

The observation logs these metrics from the independently bound assessment:

- `route_job_concordance`: trusted route versus the complete observed job topology;
- `terminal_binding_coverage`: terminal run ID and attempt bound to exact pull/head/base;
- zero self-hosted v2 minutes (`v2_self_hosted_minutes: 0`);
- zero false-success and stale-head publication (`false_success_publications: 0`, `stale_head_publications: 0`).

## Unique authority required before promotion

In-repository GitHub Actions cannot provide a unique required-status authority.
Promotion requires a separate `RigPlane Gatekeeper` GitHub App and controller
with all of these properties:

1. Its installation app ID is distinct from GitHub Actions app ID `15368`.
2. Its private key and state live outside this repository and Actions secrets; repository workflows cannot request it.
3. It alone publishes final `quick-v3` and `Agent Review Gate v3` statuses.
4. Before dispatch it records PR, head, live `main`, run ID, workflow path/SHA, and an external nonce. It accepts only
   that trusted-default-branch run and rereads head plus `main` before success.
5. Same-repository code may use the read-only worker after merge-parent verification. Fork code uses an isolated
   ephemeral/hosted worker without repository secrets or write token; otherwise Gatekeeper publishes failure.
6. Failure, cancellation, incomplete metadata, stale state, ambiguity, and API errors fail or leave status unsatisfied.

A repository ruleset-required workflow controlled from a separate locked
repository is an acceptable alternative only if GitHub enforces that exact
workflow identity and candidate repository workflows cannot satisfy or replace
it. Ordinary context/app-ID branch protection is not that mechanism.

## Observation and promotion checklist

1. Merge stage 1 through existing required contexts without changing protection.
2. Observe `*-observe` on docs-only and same-repository code/CI, checking routing, binding, mutations, and result count.
3. Exercise failure, cancellation, base advance, incomplete/capped metadata, and a fork; require no fork worker or stale success.
4. Audit Gatekeeper or the enforced external workflow; record its distinct app ID and prove Actions cannot mint status.
5. Observe `quick-v3` and `Agent Review Gate v3` on docs, code, failure, cancellation, stale-base, and isolated-fork cases.
6. Read protection and substitute the observed app ID below; refuse the switch if it is missing or equals `15368`:

   ```bash
   gatekeeper_app_id='<DEDICATED_GATEKEEPER_APP_ID>'
   test -n "$gatekeeper_app_id"
   test "$gatekeeper_app_id" != 15368
   jq -n --argjson app_id "$gatekeeper_app_id" '{
     strict: true,
     checks: [
       {context: "Agent Review Gate v3", app_id: $app_id},
       {context: "quick-v3", app_id: $app_id}
     ]
   }' | gh api --method PATCH \
     repos/rigplane/rigplane-core/branches/main/protection/required_status_checks \
     --input -
   ```

7. Read protection back, confirm `strict: true`, exact contexts, and the
   dedicated app ID, then validate a harmless PR. The `*-observe` contexts
   remain non-required.

## Rollback and retirement ordering

Legacy producers must remain enabled while the first Gatekeeper generation is
promoted. If Gatekeeper fails, switch protection back to the still-running
legacy contexts **before** disabling or editing any v3 publisher:

Rollback invariant: switch protection back to the still-running legacy contexts **before** disabling a v3 publisher.

```bash
gh api --method PATCH \
  repos/rigplane/rigplane-core/branches/main/protection/required_status_checks \
  --input - <<'JSON'
{
  "strict": true,
  "checks": [
    {"context": "Agent Review Gate", "app_id": 15368},
    {"context": "quick", "app_id": 15368}
  ]
}
JSON
```

Read protection back and prove both legacy contexts resolve on a current-base
PR. Only then repair or disable v3. This restores availability with the known
legacy trust limitation; it is an emergency rollback, not completion of
#3136.

Stage 2 may mark legacy contexts non-required but must keep their producers
running for rollback. Deletion is a later stage and is prohibited until a
previous, independently operated Gatekeeper generation is still running and
observed as a non-required rollback target. The required-check switch always
precedes removal of the generation being retired, so protection can never
require a context whose producer has disappeared.
