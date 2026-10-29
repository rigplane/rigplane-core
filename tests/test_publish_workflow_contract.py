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
