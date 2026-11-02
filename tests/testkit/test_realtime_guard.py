"""``rigplane_testkit.realtime_guard``: refuse new wall-clock tests (MOR-4075)."""

from __future__ import annotations

import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

from rigplane_testkit.realtime_guard import (
    TestFacts,
    analyze_source,
    check_baseline_does_not_grow,
    evaluate,
    main,
    read_baseline,
)

ROOT = Path(__file__).resolve().parents[2]


def _facts(source: str) -> dict[str, TestFacts]:
    return analyze_source(textwrap.dedent(source))


def test_a_real_sleep_a_clock_read_and_a_tight_timeout_are_found() -> None:
    facts = _facts(
        """
        import asyncio, time

        async def test_a():
            await asyncio.sleep(0.5)

        def test_b():
            time.sleep(1)

        def test_c():
            started = time.monotonic()

        async def test_d():
            await asyncio.wait_for(thing(), 0.5)

        def test_e(q):
            q.get(timeout=0.2)
        """
    )
    assert facts["test_a"].effects == {"asyncio.sleep"}
    assert facts["test_b"].effects == {"time.sleep"}
    assert facts["test_c"].effects == {"clock-read"}
    assert facts["test_d"].effects == {"tight-timeout"}
    assert facts["test_e"].effects == {"tight-timeout"}
    assert all(f.needs_conversion() for f in facts.values())


def test_a_yield_a_generous_bound_and_plain_code_are_not_flagged() -> None:
    facts = _facts(
        """
        import asyncio

        async def test_yield():
            await asyncio.sleep(0)

        async def test_generous():
            await asyncio.wait_for(thing(), 30.0)

        def test_plain():
            assert 1 + 1 == 2
        """
    )
    assert all(not f.needs_conversion() for f in facts.values())
    assert all(not f.effects for f in facts.values())


def test_a_same_file_helper_counts_for_the_tests_that_call_it() -> None:
    facts = _facts(
        """
        import time

        def _settle():
            time.sleep(0.1)

        def _outer():
            _settle()

        def test_uses_helper():
            _outer()

        class TestGroup:
            def _tick(self):
                time.sleep(0.1)

            def test_method(self):
                self._tick()
        """
    )
    assert facts["test_uses_helper"].effects == {"time.sleep"}
    assert facts["TestGroup.test_method"].effects == {"time.sleep"}


def test_virtual_clock_and_a_reasoned_mark_exempt_a_test() -> None:
    facts = _facts(
        """
        import asyncio, time
        import pytest

        @virtual_clock
        async def test_decorated(clock):
            await asyncio.sleep(5)

        async def test_fixture(virtual_clock):
            await asyncio.sleep(5)

        def test_scoped(monkeypatch):
            clock = VirtualClock()
            clock.scope_to(monkeypatch, mod)
            time.sleep(1)

        @pytest.mark.realtime(reason="measures the OS timer")
        def test_marked():
            time.sleep(1)

        @pytest.mark.realtime
        def test_mark_without_reason():
            time.sleep(1)
        """
    )
    assert not facts["test_decorated"].needs_conversion()
    assert not facts["test_fixture"].needs_conversion()
    assert not facts["test_scoped"].needs_conversion()
    assert not facts["test_marked"].needs_conversion()
    assert facts["test_mark_without_reason"].needs_conversion()


def _facts_for(**effects: bool) -> TestFacts:
    return TestFacts(
        frozenset({"time.sleep"}) if effects.get("sleeps") else frozenset(), False
    )


def test_a_new_offender_fails_and_a_baselined_one_does_not() -> None:
    sleeper = _facts_for(sleeps=True)
    report = evaluate(
        scans={"t.py": {"test_old": sleeper, "test_new": sleeper}},
        selected={"t.py::test_old": sleeper, "t.py::test_new": sleeper},
        exempt=set(),
        baseline={"t.py::test_old"},
    )
    assert list(report.new) == ["t.py::test_new"]
    assert not report.ok()
    assert "t.py::test_new" in report.message()


def test_a_baseline_entry_that_no_longer_needs_listing_is_stale() -> None:
    clean = _facts_for()
    report = evaluate(
        scans={"t.py": {"test_converted": clean}},
        selected={"t.py::test_converted": clean},
        exempt=set(),
        baseline={
            "t.py::test_converted",
            "t.py::test_gone",
            "other.py::test_elsewhere",
        },
    )
    # `other.py` was not collected in this run, so its entries are left alone.
    assert sorted(report.stale) == ["t.py::test_converted", "t.py::test_gone"]
    assert not report.ok()


def test_selecting_one_test_does_not_make_the_rest_of_the_baseline_stale() -> None:
    sleeper = _facts_for(sleeps=True)
    report = evaluate(
        scans={"t.py": {"test_a": sleeper, "test_b": sleeper}},
        selected={"t.py::test_a": sleeper},  # e.g. `pytest t.py::test_a`
        exempt=set(),
        baseline={"t.py::test_a", "t.py::test_b"},
    )
    assert report.ok()


def test_a_runtime_exemption_keeps_a_test_out_of_the_report() -> None:
    sleeper = _facts_for(sleeps=True)
    report = evaluate(
        scans={"t.py": {"test_hw": sleeper}},
        selected={"t.py::test_hw": sleeper},
        exempt={"t.py::test_hw"},
        baseline=set(),
    )
    assert report.ok()


# -- the plugin in a real collection ---------------------------------------------------------


def _run_suite(
    tmp_path: Path, test_source: str, baseline: str = ""
) -> subprocess.CompletedProcess[str]:
    (tmp_path / "conftest.py").write_text(
        'pytest_plugins = ["rigplane_testkit.realtime_guard"]\n', encoding="utf-8"
    )
    (tmp_path / "pytest.ini").write_text(
        "[pytest]\nrealtime_baseline = realtime_baseline.txt\nasyncio_mode = auto\n",
        encoding="utf-8",
    )
    (tmp_path / "realtime_baseline.txt").write_text(baseline, encoding="utf-8")
    (tmp_path / "test_sample.py").write_text(
        textwrap.dedent(test_source), encoding="utf-8"
    )
    return subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            "-q",
            "-p",
            "no:cacheprovider",
            "--co",
            str(tmp_path),
        ],
        cwd=tmp_path,
        env={"PYTHONPATH": str(ROOT), "PATH": "/usr/bin:/bin"},
        capture_output=True,
        text=True,
        check=False,
    )


_SLEEPER = """
import time

def test_sleeps():
    time.sleep(0.1)
"""


def test_the_plugin_refuses_a_new_real_sleep_at_collection(tmp_path: Path) -> None:
    done = _run_suite(tmp_path, _SLEEPER)
    assert done.returncode != 0
    assert "test_sample.py::test_sleeps" in done.stderr + done.stdout
    assert "time.sleep" in done.stderr + done.stdout


def test_the_plugin_accepts_a_baselined_sleep_and_a_reasoned_mark(
    tmp_path: Path,
) -> None:
    done = _run_suite(tmp_path, _SLEEPER, baseline="test_sample.py::test_sleeps\n")
    assert done.returncode == 0, done.stderr + done.stdout
    marked = _SLEEPER.replace(
        "def test_sleeps", '@pytest.mark.realtime(reason="OS timer")\ndef test_sleeps'
    ).replace("import time", "import time\nimport pytest")
    assert _run_suite(tmp_path, marked).returncode == 0


def test_the_plugin_refuses_a_mark_without_a_reason(tmp_path: Path) -> None:
    source = _SLEEPER.replace(
        "def test_sleeps", "@pytest.mark.realtime\ndef test_sleeps"
    ).replace("import time", "import time\nimport pytest")
    done = _run_suite(tmp_path, source, baseline="test_sample.py::test_sleeps\n")
    assert done.returncode != 0
    assert "without a reason" in done.stderr + done.stdout


def test_the_plugin_refuses_a_stale_baseline_entry(tmp_path: Path) -> None:
    done = _run_suite(
        tmp_path, "def test_fine():\n    pass\n", baseline="test_sample.py::test_fine\n"
    )
    assert done.returncode != 0
    assert "no longer need listing" in done.stderr + done.stdout


# -- the baseline can only shrink -------------------------------------------------------------


def _git(repo: Path, *args: str) -> None:
    subprocess.run(
        ["git", "-c", "user.email=t@example.com", "-c", "user.name=t", *args],
        cwd=repo,
        check=True,
        capture_output=True,
    )


def test_a_baseline_that_gained_an_entry_is_rejected_against_the_base(
    tmp_path: Path,
) -> None:
    _git(tmp_path, "init", "-q", "-b", "main")
    baseline = tmp_path / "baseline.txt"
    baseline.write_text("# header\na.py::test_a\nb.py::test_b\n", encoding="utf-8")
    _git(tmp_path, "add", "baseline.txt")
    _git(tmp_path, "commit", "-q", "-m", "base")

    baseline.write_text("# header\na.py::test_a\n", encoding="utf-8")  # shrank
    assert check_baseline_does_not_grow(baseline, "HEAD", tmp_path) == []

    baseline.write_text(
        "# header\na.py::test_a\nc.py::test_c\n", encoding="utf-8"
    )  # grew
    assert check_baseline_does_not_grow(baseline, "HEAD", tmp_path) == ["c.py::test_c"]
    args = [str(baseline), "HEAD", "--repo", str(tmp_path)]
    assert main(args) == 1  # the CI entry point fails on growth
    baseline.write_text("# header\na.py::test_a\n", encoding="utf-8")
    assert main(args) == 0


def test_the_baseline_reader_skips_comments_and_blank_lines(tmp_path: Path) -> None:
    path = tmp_path / "baseline.txt"
    path.write_text("# c\n\na.py::t\n  b.py::u  \n", encoding="utf-8")
    assert read_baseline(path) == {"a.py::t", "b.py::u"}
    assert read_baseline(tmp_path / "missing.txt") == set()


@pytest.mark.parametrize("name", ["realtime_baseline.txt"])
def test_core_baseline_lives_next_to_the_tests(name: str) -> None:
    assert (ROOT / "tests" / name).is_file()
