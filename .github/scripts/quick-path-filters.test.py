"""Pin the cheap CI, backend, frontend, and visual path contracts."""

from __future__ import annotations

from pathlib import Path
import re
import unittest


ROOT = Path(__file__).resolve().parents[2]
QUICK_YML = ROOT / ".github" / "workflows" / "quick.yml"
VISUAL_YML = ROOT / ".github" / "workflows" / "visual.yml"

EXPECTED_FILTERS = {
    "core": {
        "src/**",
        "tests/**",
        "!tests/test_ci_path_filters.py",
        "rigs/**",
        "contracts/**",
        "pyproject.toml",
        "uv.lock",
        ".importlinter",
    },
    "frontend": {"frontend/**", "src/rigplane/web/**"},
    "ci": {".github/scripts/**", ".github/workflows/**"},
}

_GLOB_RE = re.compile(r"- '([^']*)'")


def _filter_globs(name: str) -> set[str]:
    text = QUICK_YML.read_text(encoding="utf-8")
    block = re.compile(
        rf"^ {{12}}{re.escape(name)}:\n((?:^ {{14}}- .*\n)+)",
        re.MULTILINE,
    ).search(text)
    if block is None:
        raise AssertionError(f"could not locate the `{name}:` filter block")
    return set(_GLOB_RE.findall(block.group(1)))


class QuickPathFilterContractTest(unittest.TestCase):
    def test_filters_have_exact_nonduplicated_ownership(self) -> None:
        for name, expected in EXPECTED_FILTERS.items():
            with self.subTest(name=name):
                self.assertEqual(_filter_globs(name), expected)

        self.assertNotIn("frontend/**", _filter_globs("core"))
        self.assertFalse(
            any(value.startswith(".github/") for value in _filter_globs("core"))
        )
        self.assertFalse(
            any(value.startswith(".github/") for value in _filter_globs("frontend"))
        )

    def test_ready_only_job_guards_and_event_types_are_pinned(self) -> None:
        quick = QUICK_YML.read_text(encoding="utf-8")
        visual = VISUAL_YML.read_text(encoding="utf-8")
        event_types = "types: [opened, reopened, synchronize, ready_for_review]"

        self.assertIn(event_types, quick)
        self.assertIn(event_types, visual)
        self.assertIn(
            "if: github.event_name == 'push' || "
            "github.event.pull_request.draft == false",
            quick,
        )
        self.assertIn("needs.changes.outputs.frontend == 'true'", visual)
        self.assertIn("github.event_name == 'workflow_dispatch' ||", visual)
        self.assertIn("github.event.pull_request.draft == false", visual)

    def test_visual_pr_paths_only_select_product_frontend(self) -> None:
        visual = VISUAL_YML.read_text(encoding="utf-8")
        paths = re.search(
            r'^    paths:\n((?:^      - ".*"\n)+)',
            visual,
            re.MULTILINE,
        )
        self.assertIsNotNone(paths)
        assert paths is not None
        self.assertEqual(
            set(re.findall(r'- "([^"]*)"', paths.group(1))),
            {"frontend/**"},
        )
        self.assertIn("needs.changes.outputs.frontend == 'true'", visual)
        self.assertIn("- 'frontend/**'", visual)


if __name__ == "__main__":
    unittest.main()
