"""Contract tests for the PR timing ratchet, using only temporary Git trees."""

import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


GATE = Path(__file__).with_name("check-realtime-ratchet.py").resolve()
SDK = GATE.parents[2] / "src/rigplane/testing/realtime.py"
BASELINE = Path("timing-baseline.json")
PYTHON_TEST = "tests/test_example.py"
FRONTEND_TEST = "frontend/example.spec.ts"
FIX_COMMAND = (
    "python3 .github/scripts/check-realtime-ratchet.py --generate --base origin/main"
    " --head HEAD > timing-baseline.json"
)


class RatchetTest(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.repo = Path(directory.name)
        self.git("init", "-q")
        self.git("config", "user.name", "Ratchet Test")
        self.git("config", "user.email", "test@example.invalid")
        self.git("config", "commit.gpgsign", "false")
        self.write(BASELINE, "{}\n")
        self.commit()

    def git(self, *args):
        return subprocess.run(
            ["git", *args], cwd=self.repo, check=True, capture_output=True, text=True
        ).stdout.strip()

    def write(self, path, code):
        target = self.repo / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(code, encoding="utf-8")

    def commit(self):
        self.git("add", "--all")
        self.git("commit", "-qm", "fixture")

    def gate(self, base="HEAD^", *flags, summary=None, binary=False):
        env = os.environ.copy()
        env.pop("GITHUB_STEP_SUMMARY", None)
        if summary is not None:
            env["GITHUB_STEP_SUMMARY"] = str(summary)
        return subprocess.run(
            [
                sys.executable,
                str(GATE),
                "--sdk-source",
                str(SDK),
                "--baseline",
                str(BASELINE),
                "--base",
                base,
                *flags,
            ],
            cwd=self.repo,
            env=env,
            capture_output=True,
            text=not binary,
        )

    def test_new_offenders_point_to_the_line_and_helper(self):
        self.write(
            PYTHON_TEST,
            """import asyncio as aio
from time import sleep as pause, perf_counter

async def test_example(loop, tick):
    pause(0)
    await aio.sleep(delay=0.01)
    await aio.wait_for(aio.sleep(7), timeout=5)
    await aio.wait({tick}, timeout=3)
    started = perf_counter()
    assert perf_counter() - started < 1
    assert loop.time() - 10 > 0
""",
        )
        self.write(
            FRONTEND_TEST,
            """await page.waitForTimeout(100);
await new Promise(resolve => setTimeout(resolve, 100));
""",
        )
        self.commit()
        result = self.gate()
        self.assertEqual(result.returncode, 1, result.stderr)
        for line in (5, 6, 7, 8, 9, 10, 11):
            self.assertIn(f"{PYTHON_TEST}:{line}:", result.stdout)
        for line in (1, 2):
            self.assertIn(f"{FRONTEND_TEST}:{line}:", result.stdout)
        for helper in (
            "virtual_clock",
            "clock.elapse",
            "await_until",
            "expect.poll",
            "vi.useFakeTimers",
        ):
            self.assertIn(helper, result.stdout)

    def test_yields_helpers_real_event_markers_and_non_timing_reads_pass(self):
        self.write(
            PYTHON_TEST,
            """import asyncio
import time

async def test_example(virtual_clock, socket):
    await asyncio.sleep(0)
    await asyncio.sleep(delay=0.0)
    await virtual_clock.elapse(1)
    await await_until(socket.ready)
    time.sleep(1)  # real-time: exercises socket timeout against a live peer
    return {'timestamp': time.time()}
""",
        )
        self.write(
            FRONTEND_TEST,
            """// await page.waitForTimeout(100);
const code = 'setTimeout(resolve, 100)';
const fakeClock = { setTimeout(callback: () => void, delay: number) {} };
await page.waitForTimeout(100); // real-time: live socket injects a frame
""",
        )
        self.commit()
        self.write(BASELINE, self.gate("HEAD", "--generate").stdout)
        self.commit()
        self.assertEqual(self.gate("HEAD~2").returncode, 0)

    def test_added_markers_require_a_baseline_edit_and_appear_in_summary(self):
        self.write(PYTHON_TEST, "# real-time: already present\n")
        self.commit()
        self.write(BASELINE, self.gate("HEAD", "--generate").stdout)
        self.commit()
        self.write(
            PYTHON_TEST,
            "\n# real-time: already present\nimport time\n"
            "time.sleep(1)  # real-time: live socket\n"
            'example = "# real-time: sample string"\n',
        )
        self.write(
            FRONTEND_TEST,
            'await page.waitForTimeout(100); // real-time: live browser\n',
        )
        self.commit()
        summary = self.repo / "summary.md"
        result = self.gate(summary=summary)
        self.assertEqual(result.returncode, 1, result.stderr)
        self.assertIn("marker count raised", result.stdout)
        self.assertIn(
            f"edit the baseline with `{FIX_COMMAND}` only if the exemption is justified "
            "(adviser reads it)",
            result.stdout,
        )
        report = summary.read_text(encoding="utf-8")
        self.assertIn(f"{PYTHON_TEST}:4", report)
        self.assertIn("live socket", report)
        self.assertIn(f"{FRONTEND_TEST}:1", report)
        self.assertIn("live browser", report)
        self.assertNotIn("already present", report)
        self.assertNotIn("sample string", report)

        result = self.gate("HEAD^", "--generate", "--head", "HEAD")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.write(BASELINE, result.stdout)
        self.commit()
        self.assertEqual(self.gate("HEAD~2").returncode, 0)

    def test_marker_removal_needs_no_baseline_edit_but_restoration_does(self):
        marker = "# real-time: live socket\n"
        self.write(PYTHON_TEST, marker + "assert True\n")
        self.write(BASELINE, json.dumps({PYTHON_TEST: {"offenders": 0, "markers": 1}}))
        self.commit()
        self.write(PYTHON_TEST, "assert True\n")
        self.commit()
        self.assertEqual(self.gate().returncode, 0)
        self.write(PYTHON_TEST, marker + "assert True\n")
        self.commit()
        result = self.gate()
        self.assertEqual(result.returncode, 1, result.stderr)
        self.assertIn(FIX_COMMAND, result.stdout)

    def test_empty_markers_and_marker_text_inside_strings_fail(self):
        self.write(PYTHON_TEST, "import time\ntime.sleep(1)  # real-time:  \n")
        self.write(FRONTEND_TEST, 'page.waitForTimeout(100, "// real-time: socket");\n')
        self.commit()
        result = self.gate()
        self.assertEqual(result.returncode, 1)
        self.assertIn(f"{PYTHON_TEST}:2:", result.stdout)
        self.assertIn(f"{FRONTEND_TEST}:1:", result.stdout)

    def test_edited_baselined_file_passes_at_its_original_count(self):
        existing = "import time\ntime.sleep(1)\n"
        self.write(PYTHON_TEST, existing)
        self.write(BASELINE, json.dumps({PYTHON_TEST: {"offenders": 1, "markers": 0}}))
        self.commit()
        self.write(PYTHON_TEST, existing + "assert True\n")
        self.commit()
        self.assertEqual(self.gate().returncode, 0)

        self.write(PYTHON_TEST, existing + "time.sleep(2)\n")
        self.commit()
        result = self.gate()
        self.assertEqual(result.returncode, 1)
        self.assertIn(f"{PYTHON_TEST}:3:", result.stdout)
        self.assertIn("virtual_clock", result.stdout)

        self.write(BASELINE, json.dumps({PYTHON_TEST: {"offenders": 2, "markers": 0}}))
        self.commit()
        result = self.gate()
        self.assertEqual(result.returncode, 1)
        self.assertIn("baseline may only shrink", result.stdout)

    def test_clean_or_deleted_file_requires_baseline_entry_removal(self):
        self.write(PYTHON_TEST, "import time\ntime.sleep(1)\n")
        self.write(BASELINE, json.dumps({PYTHON_TEST: {"offenders": 1, "markers": 0}}))
        self.commit()
        self.git("rm", PYTHON_TEST)
        self.commit()
        result = self.gate()
        self.assertEqual(result.returncode, 1)
        self.assertIn(f"remove {PYTHON_TEST}", result.stdout)
        self.assertTrue(any(FIX_COMMAND in line for line in result.stdout.splitlines()))
        self.write(BASELINE, "{}\n")
        self.commit()
        self.assertEqual(self.gate("HEAD~2").returncode, 0)

    def test_bootstrap_uses_base_tree_and_rejects_inflation(self):
        self.git("rm", str(BASELINE))
        self.write(PYTHON_TEST, "import time\ntime.sleep(1)\n")
        self.commit()
        generated = self.gate("HEAD", "--generate")
        self.assertEqual(generated.returncode, 0, generated.stderr)
        self.assertEqual(
            json.loads(generated.stdout), {PYTHON_TEST: {"offenders": 1, "markers": 0}}
        )
        self.write(BASELINE, generated.stdout)
        self.commit()
        self.assertEqual(self.gate().returncode, 0)
        self.write(PYTHON_TEST, "import time\ntime.sleep(1)\ntime.sleep(2)\n")
        self.write(BASELINE, json.dumps({PYTHON_TEST: {"offenders": 2, "markers": 0}}))
        self.commit()
        self.assertEqual(self.gate("HEAD~2").returncode, 1)

    def test_generator_output_is_byte_stable_and_one_sorted_path_per_line(self):
        self.write(PYTHON_TEST, "import time\ntime.sleep(1)\n")
        self.write(FRONTEND_TEST, "// real-time: live browser\n")
        self.commit()
        expected = (
            "{\n"
            f'  "{FRONTEND_TEST}": {{"markers": 1, "offenders": 0}},\n'
            f'  "{PYTHON_TEST}": {{"markers": 0, "offenders": 1}}\n'
            "}\n"
        ).encode()
        first = self.gate("HEAD", "--generate", binary=True)
        second = self.gate("HEAD", "--generate", binary=True)
        self.assertEqual(first.returncode, 0, first.stderr)
        self.assertEqual(second.returncode, 0, second.stderr)
        self.assertEqual(first.stdout, expected)
        self.assertEqual(first.stdout, second.stdout)


if __name__ == "__main__":
    unittest.main()
