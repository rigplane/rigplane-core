# Base-controlled gate observation and authority migration

Issue [#3136](https://github.com/rigplane/rigplane-core/issues/3136) records a
circular trust boundary: a pull request can currently modify the same
`pull_request` workflow or helper that publishes its required `quick` or
`Agent Review Gate` success.

## Stage 1 is observation-only

Stage 1 leaves branch protection and the legacy publishers unchanged. The two
new commit-status contexts are telemetry and **must never be configured as
required checks**:

**Promotion prohibition: `*-observe` must never be configured as required checks.**

| Context | Producer | Candidate execution | Required |
| --- | --- | --- | --- |
| `quick` | legacy workflow or docs-only publisher | legacy PR workflow | yes, app 15368 |
| `Agent Review Gate` | legacy PR/comment workflow | API-only, with candidate-controlled PR source | yes, app 15368 |
| `quick-v2-observe` | base-owned observation workflow | same-repository PRs only, read-only self-hosted job | no |
| `Agent Review Gate v2 observe` | base-owned API-only observation workflow | none | no |

GitHub branch protection can bind a status/check to a context and an app ID,
but every workflow in this repository publishes as the same GitHub Actions app
ID `15368`. A candidate-controlled workflow can therefore publish either
observation context with the same identity. Context plus app ID does not prove
which workflow or helper made the decision. Renaming these statuses to
`*-observe` makes their non-authoritative role explicit; no protection-switch
command targets them.

The quick observation controller reads complete PR file metadata through the
API, loads its policy from the exact PR base SHA, and requires that SHA to equal
the live `main` branch head. Documentation-only changes remain API-only. A
same-repository code/CI PR uses a separate job with `contents: read`, no
persisted checkout credential, no referenced secrets, and no privileged
candidate command. Before candidate execution, the job proves that the merge
commit parents equal the admitted base and head. The hosted publisher rereads
both the PR and live `main` before it can publish success.

Fork PRs never execute on the shared self-hosted runner. Their metadata is
classified on GitHub-hosted infrastructure, the worker is skipped, and the
observation status fails with an explicit fork-withheld description. Promotion
cannot occur until a separately isolated fork execution path exists or policy
explicitly keeps fork PRs non-mergeable.

The worker uses `git ls-tree` to compare object ID, object type, and file mode
for every immutable control. This rejects content edits, deletion, regular-file
to symlink changes, and executable-bit changes:

- `.github/workflows/quick-v2.yml`
- `.github/workflows/agent-review-gate-v2.yml`
- `.github/scripts/base-controlled-gates-v1.test.js`
- `.github/scripts/base-gate-policy-v1.js`
- `.github/scripts/quick-v2-worker-v1.sh`
- `.github/scripts/verify-immutable-controls-v1.sh`
- `.github/scripts/agent-review-gate.js`

## Unique authority required before promotion

In-repository GitHub Actions cannot provide a unique required-status authority.
Promotion requires a separate `RigPlane Gatekeeper` GitHub App and controller
with all of these properties:

1. Its installation app ID is distinct from GitHub Actions app ID `15368`.
2. Its private key and decision state live outside this repository and outside
   GitHub Actions secrets. Repository workflows cannot request its token.
3. It alone publishes final `quick-v3` and `Agent Review Gate v3` statuses.
4. It records the PR number, exact head, exact live `main` SHA, worker run ID,
   workflow path/SHA, and an externally generated correlation nonce before
   dispatch. It accepts only the run it dispatched from trusted default-branch
   controls and rereads head plus live `main` before success.
5. Same-repository code may use the read-only worker after merge-parent
   verification. Fork code must use an isolated ephemeral/hosted worker with no
   repository secrets or write token; otherwise Gatekeeper publishes failure.
6. Failures, cancellation, missing/capped file metadata, stale head/base,
   ambiguous/multiple results, and publisher/API errors all publish failure or
   leave the required status unsatisfied.

A repository ruleset-required workflow controlled from a separate locked
repository is an acceptable alternative only if GitHub enforces that exact
workflow identity and candidate repository workflows cannot satisfy or replace
it. Ordinary context/app-ID branch protection is not that mechanism.

## Observation and promotion checklist

1. Merge stage 1 through the existing required contexts. Do not change branch
   protection in the stage-1 PR.
2. Observe `*-observe` on a docs-only PR and a same-repository code/CI PR.
   Verify routing, exact head/current-base binding, mode/type mutation failures,
   and a single expected observation result.
3. Exercise failure, cancellation, head/base advance, incomplete/capped API
   metadata, and a public fork. Confirm no fork worker allocation and no stale
   success.
4. Install and independently audit Gatekeeper (or the enforced external
   required workflow). Record its distinct app ID and prove repository Actions
   cannot mint its statuses.
5. Observe `quick-v3` and `Agent Review Gate v3` from that authority on docs,
   code/CI, failure, cancellation, stale-base, and isolated-fork cases.
6. Read the live protection payload and substitute the observed dedicated app
   ID below. Refuse the switch if it is missing or equals `15368`:

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
