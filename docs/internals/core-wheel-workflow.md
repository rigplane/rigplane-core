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
| `core_ref` | string | required | tag, branch or 40-hex object ID naming a commit to build |
| `controls_ref` | string | `main` | Core ref the workflow controls are checked out from |
| `artifact_name` | string | `''` | exact artifact name; overrides the prefix |
| `artifact_name_prefix` | string | `core-wheel` | artifact is `<prefix>-<sha12>` when `artifact_name` is empty |
| `retention_days` | number | 3 | wheel artifact retention (artifact-cleanup sweeps older ones anyway) |

`workflow_dispatch` takes the same inputs minus `artifact_name`. A branch is
resolved once, in the `resolve` job (`resolve_core_ref.py: main`); the build
job checks out the SHA, never the moving ref. Every raw 40-hex object ID
and every tag name is fetched and proven to be a commit: an annotated-tag
object resolves to its tagged commit, and a fetchable non-commit object
(a blob, a tree — including one a lightweight or annotated tag points at)
is refused — a wheel must be built from a commit, and the peeled commit SHA
is what propagates to the stamp, the manifest and the artifact name.
Anything that does not resolve to exactly one commit — globs, short SHAs,
`HEAD~2`, a name whose branch and tag point at different commits — fails
loudly.

## Controls and the caller

In a called workflow `github.repository` and `github.sha` identify the
CALLER, not Core: a private caller (Pro) invoking
`rigplane/rigplane-core/.github/workflows/core-wheel.yml@<ref>` would
otherwise check out, resolve against and load control files from its own
repository. Every `actions/checkout` in the workflow names
`repository: rigplane/rigplane-core`, and `CORE_REMOTE` is fixed to
`https://github.com/rigplane/rigplane-core.git` — never `github.repository`.

The control files (the resolver, version, stamp and manifest scripts, and
the contracts they load) are checked out at
`controls_ref`. Core's own callers — `publish.yml` and
`core-wheel-nightly.yml` — pass their triggering SHA (or `main`) explicitly.
An external caller that omits `controls_ref` gets Core's `main`: convenient,
but `main` can be newer than the workflow revision the caller referenced in
`uses:`, so a caller that needs exact reproducibility pins `controls_ref` to
the same SHA it calls the workflow at. That choice (an explicit input rather
than deriving "the called workflow's own commit" from GitHub context) is
deliberate: the `github.*` context cannot identify the called workflow's
commit, and an input is a reviewed, pin-able Core revision either way.

The installed-profile smoke script is the one control that does NOT come
from `controls_ref`: it is taken from the TARGET revision (see
"Installed-profile smoke" below), because the profile catalog it checks is
revision-specific.

## Outputs

`sha` (40-hex commit built), `version` (the wheel version), `wheel_name`,
`wheel_sha256`, `artifact_name`. These five are the workflow-call consumer
contract. The manifest inside the artifact, `core-wheel.json`, is a related
but distinct contract (below): it carries `sha`, `version`,
`requested_ref`, `wheel` (the file name the `wheel_name` output reports),
`wheel_sha256` and `profile_smoke`. `artifact_name` is a workflow output
only — it names the uploaded artifact and is not part of the manifest.

## Version rule

`core_wheel_version.py: wheel_version` decides the version from the
pyproject version, the SHA and the `v*` release tags at that SHA:

- exactly one release tag whose version equals the pyproject version → the
  pyproject version unchanged (a release build; a mismatch fails the build
  — a mismatched tag must never ship);
- otherwise (no release tag, or more than one) → `<pyproject>+g<sha12>`.

Versions compare through their canonical PEP 440 form: numeric epochs
normalize as integers with the zero epoch omitted (`v0!3.2.0` releases
`3.2.0`; `v01!3.2.0` releases `1!3.2.0`), numeric pre/post/dev suffixes
normalize as integers with the omitted number meaning zero (`v3.2.0rc01`
releases `3.2.0rc1`; a bare `rc` equals `rc0`), while release segments
stay literal (`v3.2` is not `3.2.0`).

The local form is unique per commit, satisfies a `==<pyproject>` pin, sorts
newer than the same public version, and PyPI rejects local versions, so an
untagged wheel can never be published by accident. The rule is applied only
to the throwaway checkout's `pyproject.toml` (`--write-pyproject`), never to
the repository.

## Artifact layout

One artifact named `<artifact_name>` (or `<prefix>-<sha12>`) containing the
wheel, the sdist, and `core-wheel.json`. The manifest goes through the typed
contract in `.github/scripts/core_wheel_manifest.py`
(`CoreWheelManifest` + `parse_manifest`): the workflow serializes it with a
JSON encoder, never by interpolating ref text into a heredoc — a legal ref
containing a quote must still produce valid JSON. The schema, six string
fields exactly:

| Field | Rule |
|---|---|
| `sha` | 40 lowercase hex — the commit built (peeled) |
| `version` | non-empty single line — the wheel version |
| `requested_ref` | non-empty single line — the `core_ref` input, verbatim |
| `wheel` | file name (no path separators) |
| `wheel_sha256` | 64 lowercase hex |
| `profile_smoke` | one of `target`, `not_available_for_target` — whether the target revision's installed-profile smoke ran (see below) |

Unknown keys are rejected by the parser; consumers read these fields through
the contract, not by re-guessing a raw dict.

## Installed-profile smoke

The profile smoke is run with the TARGET revision's own
`.github/scripts/installed_profile_smoke.py`, never the controls' copy: the
profile catalog is revision-specific (`v3.1.4` shipped nine radio profiles;
current Core ships eleven), so judging a historical wheel by the controls'
current catalog would reject a valid release.

A target revision older than the script (it first shipped in `v3.0.0b10`)
gets no silent pass and no silent failure: `core-wheel.json` records
`profile_smoke: "not_available_for_target"`, the job summary states the
skip, and the smoke-evidence artifact is not uploaded. The provenance
verification (the stamped `_build_info.json`) always stays with the
controls and runs for every target.

A second artifact, `<wheel artifact name>-installed-profile-smoke`, carries
the installed-wheel smoke evidence when the smoke ran; the name derives
from the wheel artifact's name so two invocations from one caller cannot
collide.

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

The installed-wheel smoke verifies the stamped resource through the
CONTROLS' contract reader (`stamp_build_info.py --verify-file
--expect-sha`), which works for a Core ref of any age — refs older than the
contract have no `rigplane.core.build_info` module of their own; the
controls carry it for them. The runtime reader is exercised additionally,
but only when the target package has the module.

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
