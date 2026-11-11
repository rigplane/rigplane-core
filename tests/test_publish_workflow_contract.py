"""Pins properties of `publish.yml` that a release depends on.

The first tests are text-level checks, like `test_ci_pytest_invocation.py`:
this repo declares no YAML parser as a direct test dependency. The
MOR-4375 rehearsal pins below parse the workflow instead: reachability
(no step of the file may reach `uv publish` from a non-release event) and
trigger shape are structural facts that substring pins cannot express.
PyYAML is present in every environment that runs these tests (the dev
group pulls it in via the docs tooling; quick-python and validate both
`uv sync --all-extras`), so if it ever disappears these tests fail at
import — visibly, not silently.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import yaml

PUBLISH = (
    Path(__file__).resolve().parent.parent / ".github" / "workflows" / "publish.yml"
)
QUICK = PUBLISH.with_name("quick.yml")


def _job(name: str) -> str:
    text = PUBLISH.read_text()
    match = re.search(rf"^  {name}:\n(.*?)(?=^  \w[\w-]*:\n|\Z)", text, re.S | re.M)
    assert match, f"publish.yml has no `{name}` job"
    return match.group(1)


def _parsed() -> dict[str, Any]:
    workflow: dict[str, Any] = yaml.safe_load(PUBLISH.read_text())
    assert isinstance(workflow, dict), "publish.yml did not parse to a mapping"
    return workflow


def _triggers(workflow: dict[str, Any]) -> dict[str, Any]:
    # YAML 1.1 parses the bare key `on:` as boolean True; accept the quoted
    # string form too so the pin survives a future `on:` quoting change.
    triggers = workflow.get(True, workflow.get("on"))
    assert isinstance(triggers, dict), "publish.yml has no `on:` trigger map"
    return triggers


def _cron_entries(
    node: Any, path: tuple[str, ...] = ()
) -> list[tuple[tuple[str, ...], Any]]:
    """Every `cron:` key in the parsed workflow, with its key path."""
    entries: list[tuple[tuple[str, ...], Any]] = []
    if isinstance(node, dict):
        for key, value in node.items():
            key_path = (*path, str(key))
            if key == "cron":
                entries.append((key_path, value))
            else:
                entries.extend(_cron_entries(value, key_path))
    elif isinstance(node, list):
        for index, value in enumerate(node):
            entries.extend(_cron_entries(value, (*path, str(index))))
    return entries


def test_validate_pins_uv_to_the_matrix_interpreter() -> None:
    validate = _job("validate")
    assert re.search(
        r"^      UV_PYTHON: \$\{\{ matrix\.python-version \}\}$", validate, re.M
    ), (
        "validate must set UV_PYTHON to the matrix interpreter; unpinned, every leg tests the newest one"
    )


def test_publish_uploads_only_from_a_directory_unique_to_the_run_attempt() -> None:
    publish = _job("publish")
    unique = "${{ runner.temp }}/dist-${{ github.run_id }}-${{ github.run_attempt }}"
    assert publish.count(unique) == 2, (
        "download path and DIST_DIR must both use the per-attempt directory"
    )
    # MOR-4408: the dist artifact also carries core-wheel.json, so the upload
    # names the wheel and sdist explicitly instead of "$DIST_DIR"/*.
    assert re.search(
        r'run: uv publish .*"\$DIST_DIR"/\*\.whl "\$DIST_DIR"/\*\.tar\.gz$',
        publish,
        re.M,
    )
    assert "dist/*" not in publish


def test_validate_frontend_step_sets_the_node_heap_svelte_check_needs() -> None:
    # Core 3.2.0rc2 failed to publish: `npm run check` (svelte-check) died with
    # "JavaScript heap out of memory" at ~1 GB in this step (run 42565818574).
    # quick.yml already gives its frontend step this value; publish must match.
    validate = _job("validate")
    step = validate.split(
        "      - name: Frontend install + check + tests + build\n", 1
    )[1]
    step = step.split("\n      - name:", 1)[0]
    assert "npm run check" in step
    assert re.search(
        r"^        env:\n(?:          #.*\n)*          NODE_OPTIONS: --max-old-space-size=3072$",
        step,
        re.M,
    ), "the publish frontend step must set NODE_OPTIONS=--max-old-space-size=3072"


def test_validate_runs_on_the_pool_quick_runs_its_frontend_on() -> None:
    # The 2 GiB gate pool cannot hold svelte-check (~2 GiB of heap plus Node
    # overhead), whatever NODE_OPTIONS says. quick.yml moved its frontend job to
    # the 8 GiB build pool (MOR-4214); validate runs the same `npm run check`.
    quick = QUICK.read_text()
    frontend = re.search(
        r"^  quick-frontend:\n(.*?)(?=^  \w[\w-]*:\n|\Z)", quick, re.S | re.M
    )
    assert frontend, "quick.yml has no `quick-frontend` job"
    quick_pool = re.search(r"^    runs-on: (\[.*\])$", frontend.group(1), re.M)
    assert quick_pool, "quick-frontend declares no list-form `runs-on`"
    validate_pool = re.search(r"^    runs-on: (\[.*\])$", _job("validate"), re.M)
    assert validate_pool, "validate declares no list-form `runs-on`"
    assert validate_pool.group(1) == quick_pool.group(1)


# --- Nightly untagged rehearsal (MOR-4375): structural pins on the parsed
# workflow. schedule/workflow_dispatch runs exercise the release pipeline
# with no release; the assertions below are what keeps that a rehearsal
# rather than an accidental publish path.


def test_rehearsal_triggers_exist_and_schedule_is_the_only_cron() -> None:
    workflow = _parsed()
    triggers = _triggers(workflow)
    assert triggers.get("release") == {"types": ["published"]}, (
        "the release trigger itself must stay exactly `published`"
    )
    assert "workflow_dispatch" in triggers, (
        "publish.yml needs a `workflow_dispatch` trigger so the active "
        "release branch can be rehearsed on demand (MOR-4375)"
    )
    assert triggers.get("schedule") == [{"cron": "0 6 * * *"}], (
        "the nightly rehearsal must be exactly one schedule entry, daily 06:00 UTC"
    )
    crons = _cron_entries(workflow)
    assert len(crons) == 1, (
        f"the schedule entry must be the only cron in the file; found {crons!r}"
    )
    [(path, value)] = crons
    assert value == "0 6 * * *"
    assert path[-3:] == ("schedule", "0", "cron"), (
        f"the only cron must live under the schedule trigger; path {path!r}"
    )


def test_publish_job_is_release_only_and_keeps_needing_build() -> None:
    publish = _parsed()["jobs"]["publish"]
    assert publish.get("needs") == "build", (
        "the publish job must need build; the textual `needs: build` first "
        "line is pinned by test_publish_radio_smoke_gate"
        ".test_needs_chain_unchanged"
    )
    assert publish.get("if") == "github.event_name == 'release'", (
        "the nightly rehearsal (schedule/workflow_dispatch) must never "
        "reach the PyPI upload: only a published release may. The exact "
        "string also rules out an always()/failure() escape hatch."
    )


def test_the_pypi_upload_step_cannot_be_reached_from_a_non_release_event() -> None:
    workflow = _parsed()
    uploading = {
        name
        for name, job in workflow["jobs"].items()
        for step in job.get("steps") or []
        if "uv publish" in str(step.get("run") or "")
    }
    assert uploading == {"publish"}, (
        f"only the publish job may run `uv publish`; found it in {sorted(uploading)}"
    )
    publish = workflow["jobs"]["publish"]
    assert publish.get("if") == "github.event_name == 'release'", (
        "the job holding the only `uv publish` step must itself be "
        "release-only: a skipped job also skips everything that needs it, "
        "so the `if` is the barrier that keeps schedule/dispatch runs away "
        "from PyPI"
    )
    assert publish.get("needs") == "build", (
        "the upload job must sit behind build, never beside it"
    )


def test_build_job_delegates_to_the_core_wheel_workflow() -> None:
    # MOR-4408: one wheel builder. The build job calls the reusable
    # core-wheel.yml instead of keeping a second copy of frontend build +
    # uv build + installed-profile smoke; the reusable workflow's build job
    # carries the 15-minute timeout (pinned in .github/scripts/
    # core-wheel.test.py).
    build = _parsed()["jobs"]["build"]
    assert build.get("uses") == "./.github/workflows/core-wheel.yml", (
        "build must call the reusable Core wheel workflow; a second inline "
        "copy of the build steps is a parallel builder"
    )
    assert build.get("needs") == "validate", (
        "the delegated build must still sit behind validate"
    )
    assert not build.get("steps"), (
        "the build job must not keep build steps of its own next to the call"
    )
    with_map = build.get("with") or {}
    assert with_map.get("core_ref") == "${{ github.sha }}", (
        "the release build pins the exact triggering commit"
    )
    assert with_map.get("controls_ref") == "${{ github.sha }}", (
        "Core's own caller pins the workflow controls to the triggering "
        "commit — the reviewed revision — not the workflow's main default"
    )
    assert with_map.get("artifact_name") == "dist", (
        "the publish job downloads the wheel artifact under its historical `dist` name"
    )


def test_validate_job_timeout_covers_the_full_suite() -> None:
    validate = _parsed()["jobs"]["validate"]
    timeout = validate.get("timeout-minutes")
    assert isinstance(timeout, int) and timeout >= 60, (
        "validate always runs the full suite (release and rehearsal); on the "
        "ARM64 build runner it outgrew 25 minutes (run 44109071644, v3.2.0rc9), "
        "so it needs the same 60-minute budget full.yml gives tag/rc runs"
    )


def test_validate_reuses_a_passed_full_matrix_only_after_the_receipts_gate() -> None:
    """MOR-4544: the suite is skipped only through the `reuse` step, never the receipts."""
    steps = yaml.safe_load(PUBLISH.read_text())["jobs"]["validate"]["steps"]
    names = [str(step.get("name")) for step in steps]
    receipts = names.index("Verify radio smoke receipts")
    reuse = names.index("Reuse a passed full-matrix run for the tested commit")
    assert receipts < reuse
    # the receipts check itself is never conditional on the reuse decision
    assert "reuse" not in str(steps[receipts].get("if", ""))
    gate = "steps.reuse.outputs.reuse != 'true'"
    gated = {
        str(step["name"]) for step in steps if str(step.get("if", "")).strip() == gate
    }
    assert {"Install dependencies", "Ruff", "Run tests"} <= gated
    # a rehearsal has no release: the reuse step is release-only, so it always runs the suite
    assert steps[reuse]["if"] == "github.event_name == 'release'"
    # MOR-4544's wheel-version-equals-tag guard survives the MOR-4408
    # delegation: build calls core-wheel.yml, whose version step applies
    # core_wheel_version.py — it fails the build when the tag at the commit
    # differs from pyproject (pinned in .github/scripts/core-wheel.test.py:
    # test_tag_mismatch_fails, and the version step's presence).
    build = yaml.safe_load(PUBLISH.read_text())["jobs"]["build"]
    assert build.get("uses") == "./.github/workflows/core-wheel.yml"


def _reuse_script() -> str:
    steps = yaml.safe_load(PUBLISH.read_text())["jobs"]["validate"]["steps"]
    return next(s for s in steps if s.get("id") == "reuse")["run"]


def _run_reuse(tmp_path: Path, gh_body: str) -> str:
    import os
    import subprocess

    bin_dir = tmp_path / "bin"
    bin_dir.mkdir(parents=True)
    gh = bin_dir / "gh"
    gh.write_text("#!/bin/sh\n" + gh_body + "\n", encoding="utf-8")
    gh.chmod(0o755)
    out = tmp_path / "out"
    env = dict(os.environ)
    env.update(
        PATH=f"{bin_dir}{os.pathsep}{env['PATH']}",
        GITHUB_OUTPUT=str(out),
        GITHUB_REPOSITORY="o/r",
        TESTED_SHA="a" * 40,
        GH_TOKEN="t",
    )
    done = subprocess.run(
        ["bash", "-c", _reuse_script()], env=env, capture_output=True, text=True
    )
    assert done.returncode == 0, done.stderr
    return out.read_text().strip()


def test_reuse_decision_follows_the_lookup_and_never_blocks_a_release(
    tmp_path: Path,
) -> None:
    assert _run_reuse(tmp_path / "a", "echo 1") == "reuse=true"
    assert _run_reuse(tmp_path / "b", "echo 0") == "reuse=false"
    # an unavailable Actions API is "no evidence", not a failed release
    assert _run_reuse(tmp_path / "c", "exit 1") == "reuse=false"


def test_reuse_reads_the_commit_the_receipts_checker_validated() -> None:
    steps = yaml.safe_load(PUBLISH.read_text())["jobs"]["validate"]["steps"]
    receipts = next(s for s in steps if s.get("id") == "receipts")
    assert '--tested-sha-output "$GITHUB_OUTPUT"' in receipts["run"]
    reuse = next(s for s in steps if s.get("id") == "reuse")
    assert reuse["env"]["TESTED_SHA"] == "${{ steps.receipts.outputs.tested_sha }}"
    assert "release download" not in reuse["run"]
