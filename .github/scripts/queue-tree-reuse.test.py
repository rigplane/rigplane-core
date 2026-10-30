#!/usr/bin/env python3
"""Contract tests for queue-tree-reuse.py and its wiring in quick.yml (MOR-3878)."""

from __future__ import annotations

import importlib.util
from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location(
    "queue_tree_reuse", Path(__file__).with_name("queue-tree-reuse.py")
)
assert SPEC and SPEC.loader
REUSE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(REUSE)

REPO = "rigplane/example"
HEAD = "a" * 40
TREE = "b" * 40
REF = "gh-readonly-queue/main/pr-42-" + "c" * 40


def fake_api(*, tree: str = TREE, runs: dict | None = None):
    found = {"quick": [("completed", "success")]} if runs is None else runs

    def get(path: str):
        if path == f"repos/{REPO}/pulls/42":
            return {"head": {"sha": HEAD}}
        if path == f"repos/{REPO}/git/commits/{HEAD}":
            return {"tree": {"sha": tree}}
        if f"/commits/{HEAD}/check-runs?" in path:
            for name, results in found.items():
                if f"check_name={name}" in path:
                    return {
                        "check_runs": [{"status": s, "conclusion": c} for s, c in results]
                    }
            return {"check_runs": []}
        raise AssertionError(path)

    return get


class QueueTreeReuseTest(unittest.TestCase):
    def decide(self, get, *, ref: str = REF, tree: str = TREE, checks=("quick",)):
        return REUSE.decide(get, REPO, ref, tree, checks)[0]

    def test_equal_tree_and_passed_checks_reuse_the_pull_request_run(self) -> None:
        self.assertTrue(self.decide(fake_api()))

    def test_a_different_tree_runs_the_tests(self) -> None:
        # Entries ahead in the queue, or a moved main, change the merged tree.
        self.assertFalse(self.decide(fake_api(tree="d" * 40)))

    def test_every_named_check_must_have_passed_on_the_head(self) -> None:
        for runs in (
            {},
            {"quick": []},
            {"quick": [("completed", "failure")]},
            {"quick": [("completed", "skipped")]},
            {"quick": [("in_progress", None)]},
            {"quick": [("completed", "success"), ("completed", "cancelled")]},
        ):
            with self.subTest(runs=runs):
                self.assertFalse(self.decide(fake_api(runs=runs)))
        two = fake_api(runs={"quick": [("completed", "success")]})
        self.assertFalse(self.decide(two, checks=("quick", "Pytest Gate")))

    def test_malformed_input_runs_the_tests(self) -> None:
        for ref in (
            "gh-readonly-queue/main/not-a-pr",
            "feature/pr-42-" + "c" * 40,
            "",
        ):
            with self.subTest(ref=ref):
                self.assertFalse(self.decide(fake_api(), ref=ref))
        self.assertFalse(self.decide(fake_api(), tree="not-a-tree"))
        self.assertFalse(self.decide(fake_api(), checks=()))

    def test_the_ref_may_carry_the_refs_heads_prefix(self) -> None:
        self.assertTrue(self.decide(fake_api(), ref="refs/heads/" + REF))

    def test_an_api_error_exits_cleanly_with_reuse_false(self) -> None:
        import os
        import tempfile

        with tempfile.TemporaryDirectory() as scratch:
            output = Path(scratch) / "out"
            os.environ.update(
                GH_TOKEN="x",
                QUEUE_REPO=REPO,
                QUEUE_REF=REF,
                QUEUE_TREE=TREE,
                GITHUB_OUTPUT=str(output),
                GITHUB_API_URL="http://127.0.0.1:9",
            )
            self.assertEqual(REUSE.main(["--check", "quick"]), 0)
            self.assertEqual(output.read_text(), "reuse=false\n")

    def test_quick_skips_its_heavy_job_only_through_the_trusted_classify_step(self) -> None:
        quick = (ROOT / ".github" / "workflows" / "quick.yml").read_text(encoding="utf-8")
        self.assertIn("      reuse: ${{ steps.reuse.outputs.reuse }}", quick)
        # The decision script comes from the base commit, never the queued one.
        self.assertIn("          ref: ${{ github.event.merge_group.base_sha }}\n          path: trusted", quick)
        self.assertIn("python3 trusted/.github/scripts/queue-tree-reuse.py --check quick", quick)
        self.assertIn("needs.classify.outputs.reuse != 'true' &&", quick)


if __name__ == "__main__":
    unittest.main()
