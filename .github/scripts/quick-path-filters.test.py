"""Pin docs-only, CI-only, backend, frontend, and visual selection."""

from __future__ import annotations

import importlib.util
from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[2]
CLASSIFIER_PATH = Path(__file__).with_name("classify-quick-paths.py")
SPEC = importlib.util.spec_from_file_location("classify_quick_paths", CLASSIFIER_PATH)
assert SPEC and SPEC.loader
CLASSIFIER = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(CLASSIFIER)

QUICK_YML = ROOT / ".github" / "workflows" / "quick.yml"
VISUAL_YML = ROOT / ".github" / "workflows" / "visual.yml"
DOC_CITATION_YML = ROOT / ".github" / "workflows" / "doc-citation-gate.yml"
REBRAND_YML = ROOT / ".github" / "workflows" / "rebrand-gate.yml"


class QuickPathFilterContractTest(unittest.TestCase):
    def assert_docs_only(self, paths: list[str]) -> None:
        self.assertEqual(
            CLASSIFIER.classify(paths),
            {"core": False, "frontend": False, "ci": False, "docs": True},
        )

    def test_historical_docs_only_shapes_select_zero_product_checks(self) -> None:
        cases = {
            "pr3102": [
                ".github/scripts/doc-citation-baseline.txt",
                "docs/plans/2026-08-20-transmit-authority.md",
            ],
            "pr3116": ["frontend/fixtures/approved-baselines/README.md"],
            "pr3117": [".claude/agents/verifier.md"],
            "pr3118": ["frontend/fixtures/approved-baselines/README.md"],
            "mkdocs-config": ["mkdocs.yml"],
        }
        for name, paths in cases.items():
            with self.subTest(name=name):
                self.assert_docs_only(paths)

    def test_mixed_docs_and_code_select_only_relevant_product_classes(self) -> None:
        self.assertEqual(
            CLASSIFIER.classify(["docs/guide.md", "frontend/src/radio.test.ts"]),
            {"core": False, "frontend": True, "ci": False, "docs": False},
        )
        self.assertEqual(
            CLASSIFIER.classify(["README.md", "src/rigplane/web/server.py"]),
            {"core": True, "frontend": True, "ci": False, "docs": False},
        )
        self.assertEqual(
            CLASSIFIER.classify([".github/workflows/quick.yml"]),
            {"core": False, "frontend": False, "ci": True, "docs": False},
        )
        self.assertEqual(
            CLASSIFIER.classify(["tests/test_ci_path_filters.py"]),
            {"core": False, "frontend": False, "ci": True, "docs": False},
        )
        for unsafe in ("/tmp/README.md", "docs/../src/radio.py"):
            with (
                self.subTest(unsafe=unsafe),
                self.assertRaises(CLASSIFIER.ClassificationError),
            ):
                CLASSIFIER.classify([unsafe])

    def test_workflows_pin_docs_skip_ready_guard_and_visual_exclusions(self) -> None:
        quick = QUICK_YML.read_text(encoding="utf-8")
        visual = VISUAL_YML.read_text(encoding="utf-8")
        event_types = "types: [opened, reopened, synchronize, ready_for_review]"

        self.assertIn(event_types, quick)
        self.assertIn(event_types, visual)
        self.assertIn("runs-on: ubuntu-latest", quick)
        self.assertIn("needs: classify", quick)
        self.assertIn("needs.classify.outputs.docs != 'true'", quick)
        self.assertIn("needs.classify.outputs.ci == 'true'", quick)
        self.assertIn("needs.classify.outputs.core == 'true'", quick)
        self.assertIn("needs.classify.outputs.frontend == 'true'", quick)
        self.assertIn('      - "frontend/**"', visual)
        self.assertIn('      - "!frontend/**/*.md"', visual)
        self.assertIn('      - "!frontend/**/*.rst"', visual)
        self.assertNotIn('      - ".github/workflows/visual.yml"', visual)

    def test_docs_classification_is_api_only_and_quick_push_ignores_docs(self) -> None:
        quick = QUICK_YML.read_text(encoding="utf-8")

        self.assertIn("github.rest.pulls.listFiles", quick)
        self.assertIn("github.paginate", quick)
        self.assertNotIn("actions/checkout@v6\n        with:\n          # Pull-request merge checkouts", quick)
        self.assertIn('      - "mkdocs.yml"', quick)
        self.assertIn("skipped `quick` context, with no runner allocation", quick)

    def test_docs_only_does_not_trigger_citation_or_rebrand_jobs(self) -> None:
        citation = DOC_CITATION_YML.read_text(encoding="utf-8")
        rebrand = REBRAND_YML.read_text(encoding="utf-8")

        self.assertNotIn('      - "**/*.md"', citation)
        self.assertNotIn('      - "docs/**"', citation)
        self.assertEqual(
            citation.count('      - ".github/scripts/check-doc-citations.sh"'), 2
        )
        self.assertEqual(
            citation.count('      - ".github/workflows/doc-citation-gate.yml"'), 2
        )
        for ignored in (
            '      - "!docs/**"',
            '      - "!**/*.md"',
            '      - "!**/*.mD"',
            '      - "!**/*.Md"',
            '      - "!**/*.MD"',
            '      - "!**/*.rst"',
            '      - "!**/*.rsT"',
            '      - "!**/*.rSt"',
            '      - "!**/*.rST"',
            '      - "!**/*.RsT"',
            '      - "!**/*.Rst"',
            '      - "!**/*.RSt"',
            '      - "!**/*.RST"',
            '      - "!.claude/**"',
            '      - "!.github/**"',
            '      - "!tests/test_ci_path_filters.py"',
        ):
            with self.subTest(ignored=ignored):
                self.assertEqual(rebrand.count(ignored), 2)
        for control in (
            '      - ".github/scripts/check-rebrand-allowlist.sh"',
            '      - ".github/workflows/rebrand-gate.yml"',
        ):
            with self.subTest(control=control):
                self.assertEqual(rebrand.count(control), 2)


if __name__ == "__main__":
    unittest.main()
