"""Pin docs-only, CI-only, backend, frontend, and visual selection."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import re
import subprocess
import tempfile
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
BASE_POLICY_JS = ROOT / ".github" / "scripts" / "base-gate-policy-v1.js"
VISUAL_YML = ROOT / ".github" / "workflows" / "visual.yml"
QUICK_WORKER = ROOT / ".github" / "scripts" / "quick-v2-worker-v1.sh"
DOC_CITATION_YML = ROOT / ".github" / "workflows" / "doc-citation-gate.yml"
REBRAND_YML = ROOT / ".github" / "workflows" / "rebrand-gate.yml"
BASE_GATES_TEST = ROOT / ".github" / "scripts" / "base-controlled-gates-v1.test.js"


class QuickPathFilterContractTest(unittest.TestCase):
    def test_base_controlled_gate_contracts(self) -> None:
        subprocess.run(
            ["node", "--test", str(BASE_GATES_TEST)],
            cwd=ROOT,
            check=True,
        )

    def quick_ignore_blocks(self) -> list[list[str]]:
        blocks: list[list[str]] = []
        lines = QUICK_YML.read_text(encoding="utf-8").splitlines()
        for index, line in enumerate(lines):
            if line != "    paths-ignore:":
                continue
            block: list[str] = []
            for candidate in lines[index + 1 :]:
                if candidate.startswith("      #"):
                    continue  # comments may sit between list entries
                if not candidate.startswith('      - "'):
                    break
                block.append(candidate.removeprefix('      - "').removesuffix('"'))
            blocks.append(block)
        return blocks

    @staticmethod
    def ignored_by_block(path: str, patterns: list[str]) -> bool:
        # Ordered semantics like GitHub path filters: every matching pattern
        # re-decides, a leading "!" re-includes, the last match wins.
        ignored = False
        for raw in patterns:
            negated = raw.startswith("!")
            pattern = raw[1:] if negated else raw
            matched = path == pattern
            if pattern.endswith("/**") and path.startswith(pattern[:-2]):
                matched = True
            if pattern.startswith("**/*.") and path.endswith(pattern[4:]):
                matched = True
            if "/**/" in pattern:
                prefix, _, rest = pattern.partition("/**/")
                if (
                    rest.startswith("*.")
                    and path.startswith(prefix + "/")
                    and path.endswith(rest[1:])
                ):
                    matched = True
            if matched:
                ignored = not negated
        return ignored

    @staticmethod
    def js_set_literals(source: str, name: str) -> set[str]:
        match = re.search(rf"const {name} = new Set\(\[(.*?)\]\)", source, re.DOTALL)
        assert match is not None
        return set(re.findall(r'["\']([^"\']+)["\']', match.group(1)))

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
        self,
        *,
        heads: list[str],
        files: list[dict[str, str]],
        changed_files: int | None = None,
        bases: list[str] | None = None,
    ) -> dict[str, object]:
        runner = r"""
const script = process.argv[1];
const scenario = JSON.parse(process.argv[2]);
const statuses = [];
const info = [];
const contents = [];
const github = {
  paginate: async () => scenario.files,
  rest: {
    pulls: {
      get: async () => ({data: {head: {sha: scenario.heads.shift()}, base: {sha: scenario.bases.shift()}, changed_files: scenario.changedFiles}}),
      listFiles: async () => undefined,
    },
    repos: {
      getContent: async (request) => { contents.push(request); return {data: {type: 'file', encoding: 'base64', content: Buffer.from(require('fs').readFileSync('.github/scripts/docs-only-paths.js')).toString('base64')}}; },
      createCommitStatus: async (status) => statuses.push(status),
    },
  },
};
const context = {repo: {owner: 'rigplane', repo: 'rigplane-core'}, payload: {pull_request: {number: 3132}}, serverUrl: 'https://github.test', runId: 1};
const core = {info: (message) => info.push(message)};
const AsyncFunction = Object.getPrototypeOf(async function () {}).constructor;
new AsyncFunction('github', 'context', 'core', script)(github, context, core)
  .then(() => process.stdout.write(JSON.stringify({statuses, info, contents})))
  .catch((error) => { console.error(error); process.exitCode = 1; });
"""
        completed = subprocess.run(
            [
                "node",
                "-e",
                runner,
                self.docs_quick_script(),
                json.dumps(
                    {
                        "heads": heads,
                        "files": files,
                        "changedFiles": len(files)
                        if changed_files is None
                        else changed_files,
                        "bases": bases or ["c" * 40] * len(heads),
                    }
                ),
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

    def test_docs_data_files_select_core_by_suffix_rule(self) -> None:
        # The rule covers any *.json/*.toml/*.yaml/*.yml under docs/, including
        # files not in the hand-maintained CORE_DOCS_TEST_INPUT_EXACT list: a
        # machine-readable docs data file must select core and must never take
        # the docs-only fast path (MOR-2580).
        for path in (
            "docs/internals/ui-radio-control-contract.toml",
            "docs/parity/ic7610_command_matrix.json",
            "docs/validation/templates/x6200.json",
            "docs/validation/templates/future-radio.json",
            "docs/internals/future-contract.yaml",
            "docs/guide/data.YML",
        ):
            with self.subTest(path=path):
                self.assertEqual(
                    CLASSIFIER.classify([path]),
                    {"core": True, "frontend": False, "ci": False, "docs": False},
                )
        # Prose and images under docs/ stay documentation-only, and the rule
        # is scoped to docs/: .claude data files keep the docs-only path.
        self.assert_docs_only(["docs/guide.md"])
        self.assert_docs_only(["docs/assets/front-panel.png"])
        self.assert_docs_only([".claude/settings.json"])
        # A Markdown sibling of a CORE_DOCS_TEST_INPUT_EXACT file that tests
        # do not read keeps the docs-only path (MOR-2589 keeps the list exact).
        self.assert_docs_only(["docs/api/unlisted.md"])
        self.assert_docs_only(["docs/internals/unlisted.md"])

    def test_docs_test_input_files_classify_as_core(self) -> None:
        # Every path here is read by name (Path(...)/open()/read_text()) by a
        # test under tests/ at test time — not merely cited in a comment or
        # docstring. A diff touching only one of these must not take the
        # docs-only fast path.
        test_input_paths = (
            "docs/internals/ui-radio-control-contract.toml",
            "docs/parity/ic7610_command_matrix.json",
            "docs/PROJECT.md",
            "docs/parity/README.md",
            "docs/operations/managed-runtime-packaging.md",
            "docs/api/web.md",
            "docs/guide/web-ui.md",
            "docs/api/command-catalog.md",
            "docs/api/radio.md",
            "docs/guide/connection.md",
            "docs/api/audio.md",
            "docs/guide/audio-recipes.md",
            "docs/guide/diagnostic-reports.md",
            "docs/internals/audio-capture-health.md",
            "docs/validation/templates/ftx1.json",
            "docs/validation/templates/ic7300.json",
            "docs/validation/templates/icom_ic7610.json",
            "docs/validation/templates/tx500.json",
            "docs/validation/templates/x6200.json",
            "docs/validation/templates/xiegu_x6200.json",
        )
        for path in test_input_paths:
            with self.subTest(path=path):
                self.assertEqual(
                    CLASSIFIER.classify([path]),
                    {"core": True, "frontend": False, "ci": False, "docs": False},
                )
        # A mix with an unrelated doc must not fall back to docs-only either.
        self.assertEqual(
            CLASSIFIER.classify(
                ["docs/internals/ui-radio-control-contract.toml", "docs/guide.md"]
            ),
            {"core": True, "frontend": False, "ci": False, "docs": False},
        )

    def test_rename_from_docs_data_to_markdown_selects_core(self) -> None:
        # git diff --name-only pairs renames and lists only the new name, so
        # the deleted docs data side of a rename escaped classification;
        # changed_paths must report both sides (MOR-2589).
        with tempfile.TemporaryDirectory() as scratch:
            repo = Path(scratch) / "repo"
            (repo / "docs" / "data").mkdir(parents=True)

            def git(*args: str) -> str:
                completed = subprocess.run(
                    ["git", "-C", str(repo), *args],
                    check=True,
                    capture_output=True,
                    text=True,
                )
                return completed.stdout.strip()

            git("init")
            git("config", "user.email", "ci@example.invalid")
            git("config", "user.name", "ci")
            (repo / "docs" / "data" / "x.json").write_text("payload", encoding="utf-8")
            git("add", "-A")
            git("commit", "-m", "base")
            base = git("rev-parse", "HEAD")
            git("mv", "docs/data/x.json", "docs/data/x.md")
            git("commit", "-m", "rename")
            head = git("rev-parse", "HEAD")

            paths = CLASSIFIER.changed_paths(repo, base=base, head=head)
            self.assertEqual(set(paths), {"docs/data/x.json", "docs/data/x.md"})
            self.assertEqual(
                CLASSIFIER.classify(paths),
                {"core": True, "frontend": False, "ci": False, "docs": False},
            )

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
        self.assertIn("    timeout-minutes: 25", quick)
        self.assertIn('      - "frontend/**"', visual)
        self.assertIn('      - "!frontend/**/*.md"', visual)
        self.assertIn('      - "!frontend/**/*.rst"', visual)
        self.assertNotIn('      - ".github/workflows/visual.yml"', visual)

    def test_component_kit_api_proof_is_frontend_selected_and_mirrored(self) -> None:
        quick = QUICK_YML.read_text(encoding="utf-8")
        worker = QUICK_WORKER.read_text(encoding="utf-8")
        command = "npm run verify:component-kit-api"

        for path in (
            "frontend/component-kit-api/src/index.ts",
            "frontend/src/components-v2/controls/value-control/skin.ts",
            "frontend/src/primitives/frequency/frequency-readout.ts",
        ):
            with self.subTest(path=path):
                self.assertEqual(
                    CLASSIFIER.classify([path]),
                    {"core": False, "frontend": True, "ci": False, "docs": False},
                )

        self.assertEqual(quick.count(command), 1)
        self.assertEqual(worker.count(command), 1)
        npm_install = quick.index("npm ci")
        browser_install = quick.index("npx playwright install chromium")
        system_dependencies = quick.index("npx playwright install-deps chromium")
        portable_verifier = quick.index(command)
        self.assertLess(npm_install, browser_install)
        self.assertLess(browser_install, system_dependencies)
        self.assertLess(system_dependencies, portable_verifier)
        self.assertLess(portable_verifier, quick.index("Frontend i18n visual smoke"))
        self.assertLess(worker.index("npm ci"), worker.index(command))

    def test_docs_only_routes_match_predicate_and_are_base_controlled(self) -> None:
        quick = QUICK_YML.read_text(encoding="utf-8")
        docs_quick = DOCS_QUICK_YML.read_text(encoding="utf-8")
        docs_paths = DOCS_PATHS_JS.read_text(encoding="utf-8")
        exact = (
            ".github/scripts/doc-citation-baseline.txt",
            ".github/scripts/doc-citation-dangling-baseline.txt",
            ".github/scripts/doc-link-baseline.txt",
            "AUTHORS",
            "COPYING",
            "LICENSE",
            "LICENSE.txt",
            "NOTICE",
            "mkdocs.yml",
        )
        suffix_patterns = (
            "**/*.md",
            "**/*.mD",
            "**/*.Md",
            "**/*.MD",
            "**/*.rst",
            "**/*.rsT",
            "**/*.rSt",
            "**/*.rST",
            "**/*.RsT",
            "**/*.Rst",
            "**/*.RSt",
            "**/*.RST",
        )
        for path in exact:
            with self.subTest(path=path):
                self.assertIn(f'"{path}"', docs_paths)
                self.assertEqual(quick.count(f'      - "{path}"'), 2)
        for pattern in ("docs/**", ".claude/**", *suffix_patterns):
            with self.subTest(pattern=pattern):
                self.assertEqual(quick.count(f'      - "{pattern}"'), 2)
        # docs/ data files are re-included after "docs/**" in both
        # paths-ignore blocks so a data-only diff still runs quick (MOR-2580).
        for pattern in (
            "!docs/**/*.toml",
            "!docs/**/*.json",
            "!docs/**/*.yaml",
            "!docs/**/*.yml",
        ):
            with self.subTest(pattern=pattern):
                self.assertEqual(quick.count(f'      - "{pattern}"'), 2)
        # The publisher predicate and the base route policy mirror the
        # classifier's docs data suffixes and test-read docs Markdown list;
        # comparing parsed set contents fails when any mirror diverges.
        base_policy = BASE_POLICY_JS.read_text(encoding="utf-8")
        for name, expected in (
            ("DOCS_DATA_SUFFIXES", set(CLASSIFIER.DOCS_DATA_SUFFIXES)),
            ("CORE_DOCS_TEST_INPUT_EXACT", set(CLASSIFIER.CORE_DOCS_TEST_INPUT_EXACT)),
        ):
            with self.subTest(mirror=name):
                self.assertEqual(self.js_set_literals(docs_paths, name), expected)
                self.assertEqual(self.js_set_literals(base_policy, name), expected)
        # quick.yml re-includes every test-read docs Markdown file in both
        # paths-ignore blocks so such a diff reaches the classifier (MOR-2589).
        for path in sorted(CLASSIFIER.CORE_DOCS_TEST_INPUT_EXACT):
            with self.subTest(test_read_negation=path):
                self.assertEqual(quick.count(f'      - "!{path}"'), 2)

        self.assertIn("pull_request_target:", docs_quick)
        self.assertNotIn("\n  pull_request:\n", docs_quick)
        self.assertIn("contents: read", docs_quick)
        self.assertIn("ref: baseSha", docs_quick)
        self.assertNotIn("github.workflow_sha", docs_quick)
        self.assertNotIn("actions/checkout", docs_quick)

    def test_docs_only_publisher_succeeds_only_for_complete_stable_docs(self) -> None:
        head = "a" * 40
        base = "c" * 40
        docs = [
            {"filename": "docs/guide.md"},
            {"filename": "docs/operations/unlisted.md"},
            {"filename": ".claude/agents/verifier.md"},
            {"filename": "frontend/README.MD"},
            {"filename": "guide.RsT"},
            {"filename": "mkdocs.yml"},
            {
                "filename": "docs/new.md",
                "previous_filename": "docs/old.md",
            },
        ]
        stable = self.run_docs_quick_script(
            heads=[head, head], bases=[base, base], files=docs
        )
        self.assertEqual([status["sha"] for status in stable["statuses"]], [head])
        self.assertEqual(stable["contents"][0]["ref"], base)

        scenarios = (
            ({"files": [{"filename": "CODEOWNERS"}]}, "unknown"),
            ({"files": [{"filename": "docs"}]}, "docs-root-file"),
            ({"files": [{"filename": ".claude"}]}, "claude-root-file"),
            ({"files": [{"filename": "frontend/README.rſt"}]}, "unicode-fold"),
            ({"files": [{"filename": "frontend/README.md\n"}]}, "newline-suffix"),
            ({"files": [{"filename": "frontend/README.rst\r\n"}]}, "crlf-suffix"),
            (
                {
                    "files": [
                        {"filename": "docs/guide.md"},
                        {"filename": "src/rigplane/radio.py"},
                    ]
                },
                "mixed",
            ),
            (
                {
                    "files": [
                        {
                            "filename": "docs/guide.md",
                            "previous_filename": "src/rigplane/radio.py",
                        }
                    ]
                },
                "rename",
            ),
            (
                {"files": [{"filename": "docs/guide.md"}], "changed_files": 2},
                "incomplete",
            ),
            (
                {
                    "files": [
                        {"filename": "docs/internals/ui-radio-control-contract.toml"}
                    ]
                },
                "docs-data-toml",
            ),
            (
                {"files": [{"filename": "docs/validation/templates/x6200.json"}]},
                "docs-data-json",
            ),
            (
                {
                    "files": [
                        {"filename": "docs/guide.md"},
                        {"filename": "docs/parity/ic7610_command_matrix.json"},
                    ]
                },
                "docs-mixed-data",
            ),
            (
                {"files": [{"filename": "docs/PROJECT.md"}]},
                "docs-test-input-md",
            ),
            (
                {
                    "files": [
                        {"filename": "docs/guide.md"},
                        {"filename": "docs/api/web.md"},
                    ]
                },
                "docs-mixed-test-input",
            ),
            (
                {
                    "files": [
                        {
                            "filename": "docs/guide.md",
                            "previous_filename": "docs/api/radio.md",
                        }
                    ]
                },
                "rename-old-side-test-input",
            ),
            (
                {
                    "files": [{"filename": "docs/guide.md"}],
                    "changed_files": 3000,
                },
                "capped",
            ),
            (
                {
                    "files": [{"filename": "docs/guide.md"}],
                    "heads": [head, "b" * 40],
                },
                "head-race",
            ),
            (
                {
                    "files": [{"filename": "docs/guide.md"}],
                    "bases": [base, "d" * 40],
                },
                "base-race",
            ),
        )
        for overrides, name in scenarios:
            with self.subTest(name=name):
                result = self.run_docs_quick_script(
                    heads=overrides.get("heads", [head, head]),
                    bases=overrides.get("bases", [base, base]),
                    files=overrides["files"],
                    changed_files=overrides.get("changed_files"),
                )
                self.assertEqual(result["statuses"], [])

    def test_rejected_publisher_paths_select_both_normal_quick_routes(self) -> None:
        blocks = self.quick_ignore_blocks()
        self.assertEqual(len(blocks), 2)
        for path in (
            "docs",
            ".claude",
            "frontend/README.rſt",
            "frontend/README.md\n",
            "frontend/README.rst\r\n",
            # docs/ data files are re-included by the ordered "!docs/**"
            # negations, so both quick.yml triggers fire for them (MOR-2580).
            # Test-read Markdown under docs/ is re-included the same way by
            # exact "!<path>" negations (MOR-2589).
            "docs/internals/ui-radio-control-contract.toml",
            "docs/parity/ic7610_command_matrix.json",
            "docs/validation/templates/x6200.json",
            "docs/internals/future-contract.yaml",
            "docs/PROJECT.md",
            "docs/parity/README.md",
            "docs/api/web.md",
            "docs/internals/audio-capture-health.md",
        ):
            with self.subTest(path=path):
                self.assertTrue(
                    all(not self.ignored_by_block(path, block) for block in blocks)
                )
        # Prose under docs/ that tests do not read stays ignored by both
        # triggers.
        for path in (
            "docs/guide.md",
            "docs/operations/other.md",
            "docs/internals/unlisted.md",
        ):
            with self.subTest(path=path):
                self.assertTrue(
                    all(self.ignored_by_block(path, block) for block in blocks)
                )

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
