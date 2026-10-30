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


BASE = "e" * 40
RUN_ID = 777


def fake_api(
    *,
    tree: str = TREE,
    runs: dict | None = None,
    compare: str = "ahead",
    run_event: str = "pull_request",
    run_head: str = HEAD,
    associated: list | None = None,
):
    found = {"quick": [("completed", "success")]} if runs is None else runs
    bound = [{"number": 42, "base": {"sha": BASE}}] if associated is None else associated

    def get(path: str):
        if path == f"repos/{REPO}/pulls/42":
            return {"head": {"sha": HEAD}}
        if path == f"repos/{REPO}/git/commits/{HEAD}":
            return {"tree": {"sha": tree}}
        if path == f"repos/{REPO}/actions/runs/{RUN_ID}":
            return {"event": run_event, "head_sha": run_head, "pull_requests": bound}
        if path == f"repos/{REPO}/compare/{BASE}...{HEAD}?per_page=1":
            return {"status": compare}
        if f"/commits/{HEAD}/check-runs?" in path:
            for name, results in found.items():
                if f"check_name={name}" in path:
                    return {
                        "check_runs": [
                            {
                                "status": s,
                                "conclusion": c,
                                "details_url": f"https://github.com/o/r/actions/runs/{RUN_ID}/job/1",
                            }
                            for s, c in results
                        ]
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

    def test_a_run_on_a_base_that_is_not_an_ancestor_of_the_head_is_not_reused(self) -> None:
        # The PR run tested the head merged onto an older base. If that base is
        # not an ancestor of the head, the tested merge tree can differ from the
        # head tree even though the head and queue trees are equal.
        for status in ("diverged", "behind"):
            with self.subTest(status=status):
                self.assertFalse(self.decide(fake_api(compare=status)))
        self.assertTrue(self.decide(fake_api(compare="identical")))

    def test_the_check_must_come_from_this_pull_requests_run_at_this_head(self) -> None:
        for kwargs in (
            {"run_event": "merge_group"},
            {"run_head": "f" * 40},
            {"associated": []},
            {"associated": [{"number": 7, "base": {"sha": BASE}}]},
            {"associated": [{"number": 42, "base": {}}]},
            {"associated": [{"number": 42, "base": {"sha": BASE}}] * 2},
        ):
            with self.subTest(kwargs=kwargs):
                self.assertFalse(self.decide(fake_api(**kwargs)))

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

    def test_the_workflow_token_covers_every_api_the_helper_calls(self) -> None:
        # pulls and git/commits/compare need pull-requests and contents, check-runs
        # needs checks, and the workflow run needs actions in a private repository.
        quick = (ROOT / ".github" / "workflows" / "quick.yml").read_text(encoding="utf-8")
        permissions = quick.split("\npermissions:\n", 1)[1].split("\n\n", 1)[0]
        for line in ("contents: read", "pull-requests: read", "checks: read", "actions: read"):
            self.assertIn(f"  {line}", permissions)
        source = (ROOT / ".github" / "scripts" / "queue-tree-reuse.py").read_text(encoding="utf-8")
        self.assertIn('f"repos/{repo}/actions/runs/', source)


if __name__ == "__main__":
    unittest.main()
