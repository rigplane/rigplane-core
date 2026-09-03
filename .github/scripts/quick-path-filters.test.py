"""Pin docs-only, CI-only, backend, frontend, and visual selection."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import subprocess
import unittest


ROOT = Path(__file__).resolve().parents[2]
CLASSIFIER_PATH = Path(__file__).with_name("classify-quick-paths.py")
SPEC = importlib.util.spec_from_file_location("classify_quick_paths", CLASSIFIER_PATH)
assert SPEC and SPEC.loader
CLASSIFIER = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(CLASSIFIER)

QUICK_YML = ROOT / ".github" / "workflows" / "quick.yml"
DOCS_QUICK_YML = ROOT / ".github" / "workflows" / "docs-only-quick.yml"
DOCS_PATHS_JS = ROOT / ".github" / "scripts" / "docs-only-paths.js"
VISUAL_YML = ROOT / ".github" / "workflows" / "visual.yml"
DOC_CITATION_YML = ROOT / ".github" / "workflows" / "doc-citation-gate.yml"
REBRAND_YML = ROOT / ".github" / "workflows" / "rebrand-gate.yml"


class QuickPathFilterContractTest(unittest.TestCase):
    def docs_quick_script(self) -> str:
        workflow = DOCS_QUICK_YML.read_text(encoding="utf-8")
        marker = "          script: |\n"
        start = workflow.index(marker) + len(marker)
        lines = []
        for line in workflow[start:].splitlines():
            if line and not line.startswith("            "):
                break
            lines.append(line[12:] if line.startswith("            ") else "")
        return "\n".join(lines)

    def run_docs_quick_script(
        self, *, heads: list[str], files: list[dict[str, str]], changed_files: int | None = None
    ) -> dict[str, object]:
        runner = r"""
const script = process.argv[1];
const scenario = JSON.parse(process.argv[2]);
const statuses = [];
const info = [];
const github = {
  paginate: async () => scenario.files,
  rest: {
    pulls: {
      get: async () => ({data: {head: {sha: scenario.heads.shift()}, changed_files: scenario.changedFiles}}),
      listFiles: async () => undefined,
    },
    repos: {
      getContent: async () => ({data: {type: 'file', encoding: 'base64', content: Buffer.from(require('fs').readFileSync('.github/scripts/docs-only-paths.js')).toString('base64')}}),
      createCommitStatus: async (status) => statuses.push(status),
    },
  },
};
const context = {
  repo: {owner: 'rigplane', repo: 'rigplane-core'},
  payload: {pull_request: {number: 3132}},
  serverUrl: 'https://github.test',
  runId: 1,
};
const core = {info: (message) => info.push(message)};
const AsyncFunction = Object.getPrototypeOf(async function () {}).constructor;
new AsyncFunction('github', 'context', 'core', script)(github, context, core)
  .then(() => process.stdout.write(JSON.stringify({statuses, info})))
  .catch((error) => { console.error(error); process.exitCode = 1; });
"""
        completed = subprocess.run(
            [
                "node",
                "-e",
                runner,
                self.docs_quick_script(),
                json.dumps({"heads": heads, "files": files, "changedFiles": len(files) if changed_files is None else changed_files}),
            ],
            check=True,
            capture_output=True,
            text=True,
        )
        return json.loads(completed.stdout)

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
        }
        for name, paths in cases.items():
            with self.subTest(name=name):
                self.assert_docs_only(paths)
        for path in ("README.MD", "docs/guide.RsT", "mkdocs.yml"):
            with self.subTest(path=path):
                self.assert_docs_only([path])

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
        for unknown in ("CODEOWNERS", ".gitignore"):
            with self.subTest(unknown=unknown), self.assertRaises(CLASSIFIER.ClassificationError):
                CLASSIFIER.classify([unknown])

    def test_workflows_pin_docs_skip_ready_guard_and_visual_exclusions(self) -> None:
        quick = QUICK_YML.read_text(encoding="utf-8")
        visual = VISUAL_YML.read_text(encoding="utf-8")
        event_types = "types: [opened, reopened, synchronize, ready_for_review]"

        self.assertIn(event_types, quick)
        self.assertIn(event_types, visual)
        self.assertIn("needs: classify", quick)
        self.assertIn("needs.classify.outputs.docs != 'true'", quick)
        self.assertIn("needs.classify.outputs.ci == 'true'", quick)
        self.assertIn("needs.classify.outputs.core == 'true'", quick)
        self.assertIn("needs.classify.outputs.frontend == 'true'", quick)
        self.assertIn('      - "frontend/**"', visual)
        self.assertIn('      - "!frontend/**/*.md"', visual)
        self.assertIn('      - "!frontend/**/*.rst"', visual)
        self.assertNotIn('      - ".github/workflows/visual.yml"', visual)

    def test_docs_only_quick_status_is_api_only_and_quick_ignores_docs(self) -> None:
        quick = QUICK_YML.read_text(encoding="utf-8")
        docs_quick = DOCS_QUICK_YML.read_text(encoding="utf-8")
        docs_paths = DOCS_PATHS_JS.read_text(encoding="utf-8")
        for pattern in (
            '      - "docs/**"',
            '      - ".claude/**"',
            '      - "**/*.md"',
            '      - "**/*.mD"',
            '      - "**/*.Md"',
            '      - "**/*.MD"',
            '      - "**/*.rst"',
            '      - "**/*.rsT"',
            '      - "**/*.rSt"',
            '      - "**/*.rST"',
            '      - "**/*.RsT"',
            '      - "**/*.Rst"',
            '      - "**/*.RSt"',
            '      - "**/*.RST"',
            '      - ".github/scripts/doc-citation-baseline.txt"',
            '      - ".github/scripts/doc-citation-dangling-baseline.txt"',
            '      - ".github/scripts/doc-link-baseline.txt"',
            '      - "AUTHORS"',
            '      - "COPYING"',
            '      - "LICENSE"',
            '      - "LICENSE.txt"',
            '      - "NOTICE"',
            '      - "mkdocs.yml"',
        ):
            with self.subTest(pattern=pattern):
                self.assertEqual(quick.count(pattern), 2)
        for exact in (
            ".github/scripts/doc-citation-baseline.txt",
            ".github/scripts/doc-citation-dangling-baseline.txt",
            ".github/scripts/doc-link-baseline.txt",
            "AUTHORS",
            "COPYING",
            "LICENSE",
            "LICENSE.txt",
            "NOTICE",
            "mkdocs.yml",
        ):
            with self.subTest(exact=exact):
                self.assertIn(f'"{exact}"', docs_paths)
        self.assertIn("github.rest.pulls.listFiles", docs_quick)
        self.assertIn("github.paginate", docs_quick)
        self.assertIn("github.rest.repos.createCommitStatus", docs_quick)
        self.assertIn("files.length !== changedFiles", docs_quick)
        self.assertIn("changedFiles >= 3000", docs_quick)
        self.assertIn("docs-only-paths.js", docs_quick)
        self.assertIn("currentPull.head.sha !== headSha", docs_quick)
        self.assertIn("sha: headSha", docs_quick)
        self.assertNotIn("sha: github.sha", docs_quick)
        self.assertNotIn("actions/checkout", docs_quick)
        self.assertNotIn("setup-uv", docs_quick)

    def test_docs_only_publisher_rejects_renames_and_head_races(self) -> None:
        initial_head = "a" * 40
        later_head = "b" * 40
        stable = self.run_docs_quick_script(
            heads=[initial_head, initial_head],
            files=[{"filename": "docs/guide.md"}],
        )
        self.assertEqual(
            [status["sha"] for status in stable["statuses"]], [initial_head]
        )

        renamed_code = self.run_docs_quick_script(
            heads=[initial_head],
            files=[
                {
                    "filename": "docs/guide.md",
                    "previous_filename": "src/rigplane/radio.py",
                }
            ],
        )
        self.assertEqual(renamed_code["statuses"], [])

        raced = self.run_docs_quick_script(
            heads=[initial_head, later_head],
            files=[{"filename": "docs/guide.md"}],
        )
        self.assertEqual(raced["statuses"], [])
        self.assertIn("head changed", raced["info"][0])

        incomplete = self.run_docs_quick_script(
            heads=[initial_head], files=[{"filename": "docs/guide.md"}], changed_files=2
        )
        self.assertEqual(incomplete["statuses"], [])
        capped = self.run_docs_quick_script(
            heads=[initial_head], files=[{"filename": "docs/guide.md"}], changed_files=3000
        )
        self.assertEqual(capped["statuses"], [])

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
            '      - "!**/*.rst"',
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
