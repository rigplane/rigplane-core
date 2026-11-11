# Core wheel from any ref (MOR-4408)

`core-wheel.yml` builds a Core wheel from any ref — tag, branch or 40-hex
SHA — with a unique version, a sha256, and provenance the server reports at
runtime. It is one reusable builder: `publish.yml`'s build job calls it for
releases and rehearsals, `core-wheel-nightly.yml` calls it nightly from
`main`, and external consumers (Pro, Station, RigDeck CI) call the workflow
or install its artifact.

## Inputs

| Input | Type | Default | Meaning |
|---|---|---|---|
| `core_ref` | string | required | tag, branch or 40-hex SHA to build |
| `artifact_name` | string | `''` | exact artifact name; overrides the prefix |
| `artifact_name_prefix` | string | `core-wheel` | artifact is `<prefix>-<sha12>` when `artifact_name` is empty |
| `retention_days` | number | 3 | wheel artifact retention (artifact-cleanup sweeps older ones anyway) |

`workflow_dispatch` takes the same inputs minus `artifact_name`. A branch is
resolved once, in the `resolve` job (`resolve_core_ref.py: main`); the build
job checks out the SHA, never the moving ref. Anything that does not resolve
to exactly one commit — globs, short SHAs, `HEAD~2`, a name whose branch and
tag point at different commits — fails loudly.

## Outputs

`sha` (40-hex commit built), `version` (the wheel version), `wheel_name`,
`wheel_sha256`, `artifact_name`. These five are the consumer contract; they
are also inside `core-wheel.json` in the artifact.

## Version rule

`core_wheel_version.py: wheel_version` decides the version from the
pyproject version, the SHA and the `v*` release tags at that SHA:

- exactly one release tag whose version equals the pyproject version → the
  pyproject version unchanged (a release build; a mismatch fails the build
  — a mismatched tag must never ship);
- otherwise (no release tag, or more than one) → `<pyproject>+g<sha12>`.

The local form is unique per commit, satisfies a `==<pyproject>` pin, sorts
newer than the same public version, and PyPI rejects local versions, so an
untagged wheel can never be published by accident. The rule is applied only
to the throwaway checkout's `pyproject.toml` (`--write-pyproject`), never to
the repository.

## Artifact layout

One artifact named `<artifact_name>` (or `<prefix>-<sha12>`) containing the
wheel, the sdist, and `core-wheel.json`:

```json
{
  "sha": "<40-hex commit>",
  "version": "<wheel version>",
  "requested_ref": "<the core_ref input>",
  "wheel": "<wheel file name>",
  "wheel_sha256": "<sha256 of the wheel file>"
}
```

A second artifact, `installed-profile-smoke`, carries the installed-wheel
smoke evidence, exactly as `publish.yml` always did.

## Build provenance

The build job stamps `src/rigplane/core/_build_info.json` into the throwaway
checkout before `uv build` (`stamp_build_info.py: main`), so the wheel
carries `git_sha` and `requested_ref`. The typed contract is
`build_info.py: BuildInfo`; the server reads it through
`build_info.py: load_build_info`, and the control WebSocket `hello` message
reports it as `core_sha` (string or null). Source checkouts and wheels built
before MOR-4408 have no file: the reader returns unknown and `core_sha` is
null. The reader never falls back to `git` at runtime — an installed package
inside an unrelated git repository would report the wrong repository.

`_build_info.json` is intentionally not gitignored: hatchling excludes
ignored files from the wheel. The repository itself must never carry the
file (a stale committed copy would misreport every build);
`tests/test_build_info.py: test_repo_checkout_is_not_stamped` guards that.

## Stamping a source checkout

Pro does not install the wheel. It freezes Core from a sibling source
checkout of the resolved SHA and calls, from that checkout:

```bash
python3 scripts/release/stamp_build_info.py \
  --sha <resolved-40-hex-sha> \
  --requested-ref <the ref Pro resolved>
```

`--root` defaults to the checkout the script lives in, so a source checkout
needs no extra arguments. The schema is owned by `build_info.py` — the one
producer for both the wheel job and Pro; an editable install then reports
the same `core_sha` as a wheel built from that commit.

## What a consumer's contract tests need

The wheel does not carry `contracts/`. A consumer that runs Core's
contract/conformance suites needs a source checkout of the same resolved SHA
alongside the wheel install; `resolve`'s `sha` output is the ref to check
out.

## Proof gaps only GitHub can close

Cross-workflow invocation, `publish.yml`'s delegation on a real run, and
the `workflow_call.outputs` plumbing are GitHub-side facts; the pinned
structure is enforced by `.github/scripts/core-wheel.test.py`.
