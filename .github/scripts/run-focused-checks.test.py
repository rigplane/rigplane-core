from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import subprocess
import tempfile
import unittest


SCRIPT = Path(__file__).with_name("run-focused-checks.py")
SPEC = importlib.util.spec_from_file_location("run_focused_checks", SCRIPT)
assert SPEC and SPEC.loader
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


class FocusedChecksContractTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.repo = Path(self.tempdir.name)
        for relative in (
            "tests/test_radio.py",
            "src/rigplane/radio.py",
            "frontend/src/radio.test.ts",
        ):
            path = self.repo / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("# fixture\n", encoding="utf-8")

    def tearDown(self) -> None:
        self.tempdir.cleanup()

    def build(self, **overrides: str) -> dict[str, object]:
        values = {
            "revision": "a" * 40,
            "pytest_raw": '["tests/test_radio.py::test_ptt[on]"]',
            "ruff_raw": '["src/rigplane/radio.py", "tests/test_radio.py"]',
            "vitest_raw": '["frontend/src/radio.test.ts"]',
            "repo": self.repo,
        }
        values.update(overrides)
        return MODULE.build_plan(**values)

    def test_valid_targets_become_argv_without_shell(self) -> None:
        plan = self.build()
        calls: list[tuple[list[str], Path, bool]] = []

        def fake_run(argv: list[str], *, cwd: Path, check: bool):
            calls.append((argv, cwd, check))
            return subprocess.CompletedProcess(argv, 0)

        results = MODULE.execute_plan(plan, repo=self.repo, runner=fake_run)

        self.assertEqual(
            [result["name"] for result in results],
            ["pytest", "ruff-check", "ruff-format", "vitest"],
        )
        self.assertTrue(
            all(isinstance(argv, list) and check is False for argv, _, check in calls)
        )
        self.assertEqual(calls[-1][0][-1], "src/radio.test.ts")

    def test_rejects_flags_absolute_traversal_and_shell_metacharacters(self) -> None:
        unsafe = (
            {"pytest_raw": '["-k"]'},
            {
                "ruff_raw": json.dumps(
                    [str((self.repo / "tests/test_radio.py").resolve())]
                )
            },
            {"vitest_raw": '["frontend/../tests/test_radio.py"]'},
            {"pytest_raw": '["tests/test_radio.py;echo"]'},
        )
        for override in unsafe:
            with self.subTest(override=override), self.assertRaises(MODULE.InputError):
                self.build(**override)

    def test_rejects_missing_or_empty_targets_and_non_exact_revision(self) -> None:
        with self.assertRaises(MODULE.InputError):
            self.build(
                pytest_raw="[]",
                ruff_raw="[]",
                vitest_raw="[]",
            )
        with self.assertRaises(MODULE.InputError):
            self.build(revision="main")


if __name__ == "__main__":
    unittest.main()
