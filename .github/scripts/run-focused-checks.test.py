from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch


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
        self.assertEqual(calls[0][0], ["uv", "run", "pytest", *plan["pytest_targets"]])
        self.assertNotIn("diagnostics", plan)

    def test_diagnostics_uses_fixed_flags_and_trusted_plugin_without_shell(
        self,
    ) -> None:
        plan = self.build(diagnostics_raw="true", ruff_raw="[]", vitest_raw="[]")
        calls = []

        def fake_run(argv, **kwargs):
            calls.append((argv, kwargs))
            return subprocess.CompletedProcess(argv, 1)

        output = self.repo / "diagnostics"
        with patch.dict("os.environ", {"PYTHONPATH": "untrusted", "UV_PYTHON": "3.13"}):
            results = MODULE.execute_plan(
                plan, repo=self.repo, runner=fake_run, diagnostics_dir=output
            )
        argv, kwargs = calls[0]
        self.assertEqual(
            argv,
            [
                "uv",
                "run",
                "pytest",
                *plan["pytest_targets"],
                "-n",
                "4",
                "--timeout=300",
                "--timeout-method=thread",
                "--max-worker-restart=0",
                "--tb=long",
                "-ra",
                "-p",
                "pytest_worker_diagnostics",
            ],
        )
        self.assertEqual(kwargs["cwd"], self.repo)
        self.assertFalse(kwargs["check"])
        self.assertNotIn("shell", kwargs)
        self.assertEqual(kwargs["env"]["UV_PYTHON"], "3.11.17")
        self.assertEqual(kwargs["env"]["PYTHONPATH"], str(SCRIPT.resolve().parent))
        self.assertEqual(
            kwargs["env"]["RIGPLANE_WORKER_DIAG_DIR"], str(output.resolve())
        )
        self.assertEqual(results[0]["returncode"], 1)

    def test_diagnostics_rejects_invalid_input_missing_pytest_and_output(self) -> None:
        for value in ("yes", "TRUE", "--timeout=0", "true;echo"):
            with self.subTest(value=value), self.assertRaises(MODULE.InputError):
                self.build(diagnostics_raw=value)
        with self.assertRaises(MODULE.InputError):
            self.build(diagnostics_raw="true", pytest_raw="[]")
        with self.assertRaises(MODULE.InputError):
            MODULE.execute_plan(self.build(diagnostics_raw="true"), repo=self.repo)

    def test_workflow_diagnostics_is_opt_in_bounded_and_uses_control_plugin(
        self,
    ) -> None:
        workflow = SCRIPT.parents[1] / "workflows/focused.yml"
        text = workflow.read_text()
        mode = text.split("      diagnostics:", 1)[1].split("      pytest_targets:", 1)[
            0
        ]
        self.assertIn("default: false", mode)
        self.assertIn("type: boolean", mode)
        self.assertIn("timeout-minutes: 15", text)
        self.assertEqual(text.count("inputs.diagnostics && '3.11.17' || '3.11'"), 3)
        self.assertIn("permissions:\n  contents: read", text)
        finalize = text.split("- name: Bound worker diagnostics", 1)[1].split(
            "- name:", 1
        )[0]
        self.assertIn("if: always() && inputs.diagnostics", finalize)
        self.assertIn("control/.github/scripts/pytest_worker_diagnostics.py", finalize)
        self.assertIn(
            '--finalize-strict "${{ runner.temp }}/focused-worker-diagnostics"',
            finalize,
        )
        upload = text.split("- name: Upload worker diagnostics", 1)[1].split(
            "- name:", 1
        )[0]
        self.assertIn("steps.bound_diagnostics.outcome == 'success'", upload)
        self.assertIn("retention-days: 7", upload)
        for leaf in (
            "events-*.jsonl",
            "phases-*.jsonl",
            "controller-*.jsonl",
            "dump-*.txt",
            "fatal-*.log",
            "timeout-*.log",
            "bounds.json",
        ):
            self.assertIn("focused-worker-diagnostics/" + leaf, upload)

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
            for mode in ("false", "true"):
                with (
                    self.subTest(override=override, mode=mode),
                    self.assertRaises(MODULE.InputError),
                ):
                    self.build(diagnostics_raw=mode, **override)

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
