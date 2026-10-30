# Platform subset workflow

`.github/workflows/platform-subset.yml` runs a small set of platform-sensitive
tests on a native, non-container runner.

## Legs

| Leg | `runs-on` labels |
|---|---|
| macOS arm64 | `[self-hosted, macOS, ARM64, platform]` |

There is no Windows or native Linux leg in this workflow. The Windows runners
are registered to the `rigplane-pro` repository only, so jobs from this
repository cannot reach them; MOR-3231 reconciles runner labels and the matrix.

Each leg runs `uv sync --all-extras`, prints the Python and `uv` versions and
the platform, runs `ioreg -l` as a live probe (printing only its line count,
because the job log of this public repository is public), then runs the test files listed
in the workflow's `Run platform tests` step with `--timeout 300 -n 2`, and
uploads `junit.xml` as an artifact.

## Triggers

- Nightly: cron `30 1 * * *` (01:30 UTC).
- Manual: `gh workflow run platform-subset.yml -f platform=macos`, optionally
  with `-f pytest_k='<expression>'`.

There is no `push` or `pull_request` trigger, so code from a fork never runs on
the self-hosted runner.

## Informational status

The workflow is not a required check. It stays informational until it has been
green for a week; a red run is a finding to triage, not a merge block.

## Signing runners

Nothing in this workflow may run on runners labelled `signing`. Its labels
include `platform`, which no signing runner carries.
