"""Pins two properties of `publish.yml` that a release depends on.

Text-level checks, like `test_ci_pytest_invocation.py`: this repo declares no
YAML parser for tests. They read the workflow text only; they do not run it.
"""

from __future__ import annotations

import re
from pathlib import Path

PUBLISH = (
    Path(__file__).resolve().parent.parent / ".github" / "workflows" / "publish.yml"
)


def _job(name: str) -> str:
    text = PUBLISH.read_text()
    match = re.search(rf"^  {name}:\n(.*?)(?=^  \w[\w-]*:\n|\Z)", text, re.S | re.M)
    assert match, f"publish.yml has no `{name}` job"
    return match.group(1)


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
    assert re.search(r'run: uv publish .* "\$DIST_DIR"/\*$', publish, re.M)
    assert "dist/*" not in publish.replace('"$DIST_DIR"/*', "")


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
