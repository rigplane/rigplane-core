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
    base_revision,
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

        def test_f(worker, sock, fut):
            worker.join(0.5)
            sock.settimeout(0.5)
            fut.result(0.5)

        def test_g(q):
            q.get(True, 0.5)
        """
    )
    assert facts["test_a"].effects == {"asyncio.sleep"}
    assert facts["test_b"].effects == {"time.sleep"}
    assert facts["test_c"].effects == {"clock-read"}
    assert facts["test_d"].effects == {"asyncio-timeout"}
    assert facts["test_e"].effects == {"thread-timeout"}
    assert facts["test_f"].effects == {"thread-timeout"}
    assert facts["test_g"].effects == {"thread-timeout"}
    assert all(f.needs_conversion() for f in facts.values())


def test_a_yield_a_generous_bound_and_ordinary_code_are_not_flagged() -> None:
    facts = _facts(
        """
        import asyncio

        async def test_yield():
            await asyncio.sleep(0)
            await asyncio.sleep(delay=0)

        async def test_generous():
            await asyncio.wait_for(thing(), 30.0)

        def test_plain(mapping):
            assert mapping.get("key", 0.5) == 0.5
            assert ", ".join(["a"]) == "a"
        """
    )
    assert all(not f.effects for f in facts.values())


def test_a_keyword_delay_is_read_like_a_positional_one() -> None:
    facts = _facts(
        """
        import asyncio

        async def test_zero():
            await asyncio.sleep(delay=0)

        async def test_positive():
            await asyncio.sleep(delay=0.5)
        """
    )
    assert not facts["test_zero"].effects
    assert facts["test_positive"].effects == {"asyncio.sleep"}


def test_import_aliases_are_resolved() -> None:
    facts = _facts(
        """
        import time as clockwork
        from time import monotonic, sleep as nap
        from asyncio import sleep as pause

        def test_alias():
            clockwork.sleep(1)

        def test_from_import():
            monotonic()

        def test_renamed_sleep():
            nap(1)

        async def test_renamed_async_sleep():
            await pause(2)

        def test_unrelated_sleep(player):
            player.sleep(5)
        """
    )
    assert facts["test_alias"].effects == {"time.sleep"}
    assert facts["test_from_import"].effects == {"clock-read"}
    assert facts["test_renamed_sleep"].effects == {"time.sleep"}
    assert facts["test_renamed_async_sleep"].effects == {"asyncio.sleep"}
    assert not facts["test_unrelated_sleep"].effects


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
        """
    )
    assert facts["test_uses_helper"].effects == {"time.sleep"}


def test_the_same_names_in_different_classes_do_not_share_effects() -> None:
    facts = _facts(
        """
        import time

        class TestA:
            def _tick(self):
                pass

            def test_same(self):
                self._tick()

        class TestB:
            def _tick(self):
                time.sleep(0.1)

            def test_same(self):
                self._tick()

        class TestC:
            def test_other(self):
                pass
        """
    )
    assert not facts["TestA.test_same"].effects
    assert facts["TestB.test_same"].effects == {"time.sleep"}
    assert not facts["TestC.test_other"].effects


def test_a_same_file_fixture_counts_for_the_tests_that_request_it() -> None:
    facts = _facts(
        """
        import time
        import pytest

        @pytest.fixture
        def slow():
            time.sleep(0.1)

        @pytest.fixture
        def wrapped(slow):
            return 1

        @pytest.fixture(autouse=True)
        def everywhere():
            time.monotonic()

        def test_direct(slow):
            pass

        def test_indirect(wrapped):
            pass
        """
    )
    assert facts["test_direct"].effects == {"time.sleep", "clock-read"}
    assert facts["test_indirect"].effects == {"time.sleep", "clock-read"}
    assert "slow" not in facts  # a fixture is not a test


def test_the_virtual_clock_exempts_only_what_it_virtualizes() -> None:
    facts = _facts(
        """
        import asyncio, time

        @virtual_clock
        async def test_timers(clock):
            await asyncio.sleep(5)
            await asyncio.wait_for(thing(), 0.5)

        async def test_fixture_timers(virtual_clock):
            await asyncio.sleep(5)

        async def test_real_sleep_on_a_virtual_loop(virtual_clock):
            time.sleep(0.1)

        async def test_real_clock_read_on_a_virtual_loop(virtual_clock):
            time.monotonic()

        async def test_thread_wait_on_a_virtual_loop(virtual_clock, worker):
            worker.join(0.5)
        """
    )
    assert not facts["test_timers"].needs_conversion()
    assert not facts["test_fixture_timers"].needs_conversion()
    assert facts["test_real_sleep_on_a_virtual_loop"].violations() == {"time.sleep"}
    assert facts["test_real_clock_read_on_a_virtual_loop"].violations() == {
        "clock-read"
    }
    assert facts["test_thread_wait_on_a_virtual_loop"].violations() == {
        "thread-timeout"
    }


def test_a_reasoned_mark_exempts_and_a_bare_mark_does_not() -> None:
    facts = _facts(
        """
        import time
        import pytest

        @pytest.mark.realtime(reason="measures the OS timer")
        def test_marked():
            time.sleep(1)

        @pytest.mark.realtime
        def test_mark_without_reason():
            time.sleep(1)
        """
    )
    assert not facts["test_marked"].needs_conversion()
    assert facts["test_mark_without_reason"].needs_conversion()


def _sleeper() -> TestFacts:
    return TestFacts(frozenset({"time.sleep"}), False)


def _clean() -> TestFacts:
    return TestFacts(frozenset(), False)


def test_a_new_offender_fails_and_a_baselined_one_does_not() -> None:
    report = evaluate(
        scans={"t.py": {"test_old": _sleeper(), "test_new": _sleeper()}},
        selected={
            "t.py::test_old": (_sleeper(), [False]),
            "t.py::test_new": (_sleeper(), [False]),
        },
        baseline={"t.py::test_old"},
    )
    assert list(report.new) == ["t.py::test_new"]
    assert not report.ok()
    assert "t.py::test_new" in report.message()


def test_one_unmarked_case_of_a_parametrized_test_is_enough_to_offend() -> None:
    sleeper = _sleeper()
    scans = {"t.py": {"test_p": sleeper}}
    mixed = evaluate(scans, {"t.py::test_p": (sleeper, [True, False])}, baseline=set())
    assert list(mixed.new) == ["t.py::test_p"]
    all_exempt = evaluate(
        scans, {"t.py::test_p": (sleeper, [True, True])}, baseline=set()
    )
    assert all_exempt.ok()
    baselined = evaluate(
        scans, {"t.py::test_p": (sleeper, [True, False])}, baseline={"t.py::test_p"}
    )
    assert baselined.ok()


def test_a_baseline_entry_that_no_longer_needs_listing_is_stale() -> None:
    report = evaluate(
        scans={"t.py": {"test_converted": _clean()}},
        selected={"t.py::test_converted": (_clean(), [False])},
        baseline={
            "t.py::test_converted",
            "t.py::test_gone",
            "other.py::test_elsewhere",
            "deleted.py::test_x",
        },
        file_exists=lambda path: path != "deleted.py",
    )
    # `other.py` exists but was not collected in this run, so its entry is left alone;
    # `deleted.py` is gone, so its entry is stale even in a partial run.
    assert sorted(report.stale) == [
        "deleted.py::test_x",
        "t.py::test_converted",
        "t.py::test_gone",
    ]
    assert not report.ok()


def test_selecting_one_test_does_not_make_the_rest_of_the_baseline_stale() -> None:
    sleeper = _sleeper()
    report = evaluate(
        scans={"t.py": {"test_a": sleeper, "test_b": sleeper}},
        selected={
            "t.py::test_a": (sleeper, [False])
        },  # e.g. `t.py::test_a` on the command line
        baseline={"t.py::test_a", "t.py::test_b"},
    )
    assert report.ok()


# -- the plugin in a real collection ---------------------------------------------------------


_PLUGIN_CONFTEST = 'pytest_plugins = ["rigplane_testkit.realtime_guard"]\n'


def _collect(
    tmp_path: Path, env: dict[str, str] | None = None
) -> subprocess.CompletedProcess[str]:
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
        env={"PYTHONPATH": str(ROOT), "PATH": "/usr/bin:/bin", **(env or {})},
        capture_output=True,
        text=True,
        check=False,
    )


def _run_suite(
    tmp_path: Path,
    test_source: str,
    baseline: str = "",
    conftest: str = _PLUGIN_CONFTEST,
) -> subprocess.CompletedProcess[str]:
    (tmp_path / "conftest.py").write_text(conftest, encoding="utf-8")
    (tmp_path / "pytest.ini").write_text(
        "[pytest]\nrealtime_baseline = realtime_baseline.txt\nasyncio_mode = auto\n",
        encoding="utf-8",
    )
    (tmp_path / "realtime_baseline.txt").write_text(baseline, encoding="utf-8")
    (tmp_path / "test_sample.py").write_text(
        textwrap.dedent(test_source), encoding="utf-8"
    )
    return _collect(tmp_path)


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


def test_a_parametrized_sleeper_with_one_marked_case_is_still_refused(
    tmp_path: Path,
) -> None:
    source = """
    import time
    import pytest

    @pytest.mark.parametrize("n", [
        pytest.param(1, marks=pytest.mark.realtime(reason="OS timer")),
        2,
    ])
    def test_sleeps(n):
        time.sleep(0.1)
    """
    done = _run_suite(tmp_path, source)
    assert done.returncode != 0
    assert "test_sample.py::test_sleeps" in done.stderr + done.stdout


def test_a_real_sleep_on_a_virtual_loop_is_still_refused(tmp_path: Path) -> None:
    source = """
    import asyncio, time

    async def test_virtual_timers(virtual_clock):
        await asyncio.sleep(5)

    async def test_real_sleep(virtual_clock):
        time.sleep(0.1)
    """
    conftest = (
        "import pytest\n"
        + _PLUGIN_CONFTEST
        + "@pytest.fixture\nasync def virtual_clock():\n    yield None\n"
    )
    done = _run_suite(tmp_path, source, conftest=conftest)
    output = done.stderr + done.stdout
    assert done.returncode != 0
    assert "test_sample.py::test_real_sleep  [time.sleep]" in output
    assert "test_sample.py::test_virtual_timers  [" not in output


# -- the baseline can only shrink -------------------------------------------------------------


def _git(repo: Path, *args: str) -> None:
    subprocess.run(
        ["git", "-c", "user.email=t@example.com", "-c", "user.name=t", *args],
        cwd=repo,
        check=True,
        capture_output=True,
    )


def _repo_with_baseline(tmp_path: Path, text: str) -> Path:
    _git(tmp_path, "init", "-q", "-b", "main")
    baseline = tmp_path / "baseline.txt"
    baseline.write_text(text, encoding="utf-8")
    _git(tmp_path, "add", "baseline.txt")
    _git(tmp_path, "commit", "-q", "-m", "base")
    return baseline


def test_a_baseline_that_gained_an_entry_is_rejected_against_the_base(
    tmp_path: Path,
) -> None:
    baseline = _repo_with_baseline(tmp_path, "# header\na.py::test_a\nb.py::test_b\n")

    baseline.write_text("# header\na.py::test_a\n", encoding="utf-8")  # shrank
    assert check_baseline_does_not_grow(baseline, "HEAD", tmp_path).status == "ok"

    baseline.write_text(
        "# header\na.py::test_a\nc.py::test_c\n", encoding="utf-8"
    )  # grew
    check = check_baseline_does_not_grow(baseline, "HEAD", tmp_path)
    assert (check.status, check.grown) == ("grew", ("c.py::test_c",))
    args = [str(baseline), "HEAD", "--repo", str(tmp_path)]
    assert main(args) == 1  # the CI entry point fails on growth
    baseline.write_text("# header\na.py::test_a\n", encoding="utf-8")
    assert main(args) == 0


def test_an_unresolvable_base_is_a_failure_and_a_missing_baseline_is_a_bootstrap(
    tmp_path: Path,
) -> None:
    baseline = _repo_with_baseline(tmp_path, "# header\n")
    assert (
        check_baseline_does_not_grow(baseline, "no-such-ref", tmp_path).status
        == "unavailable"
    )
    assert main([str(baseline), "no-such-ref", "--repo", str(tmp_path)]) == 1

    fresh = tmp_path / "fresh.txt"
    fresh.write_text("a.py::test_a\n", encoding="utf-8")  # introduced in this change
    assert check_baseline_does_not_grow(fresh, "HEAD", tmp_path).status == "bootstrap"
    assert main([str(fresh), "HEAD", "--repo", str(tmp_path)]) == 0


def test_a_new_offender_added_together_with_its_baseline_line_fails_collection(
    tmp_path: Path,
) -> None:
    # The mandatory path: collection compares the baseline with the previous revision.
    _git(tmp_path, "init", "-q", "-b", "main")
    (tmp_path / "conftest.py").write_text(
        'pytest_plugins = ["rigplane_testkit.realtime_guard"]\n', encoding="utf-8"
    )
    (tmp_path / "pytest.ini").write_text(
        "[pytest]\nrealtime_baseline = realtime_baseline.txt\n", encoding="utf-8"
    )
    (tmp_path / "realtime_baseline.txt").write_text("", encoding="utf-8")
    (tmp_path / "test_sample.py").write_text(
        "def test_fine():\n    pass\n", encoding="utf-8"
    )
    _git(tmp_path, "add", ".")
    _git(tmp_path, "commit", "-q", "-m", "base")

    (tmp_path / "test_sample.py").write_text(
        textwrap.dedent(_SLEEPER), encoding="utf-8"
    )
    (tmp_path / "realtime_baseline.txt").write_text(
        "test_sample.py::test_sleeps\n", encoding="utf-8"
    )
    _git(tmp_path, "add", ".")
    _git(tmp_path, "commit", "-q", "-m", "add a sleeper and baseline it")
    done = _collect(tmp_path, {"RIGPLANE_REALTIME_BASE_REF": "HEAD^1"})
    assert done.returncode != 0
    assert "may only shrink" in done.stderr + done.stdout


def test_the_base_revision_is_the_first_parent_in_ci_and_origin_main_elsewhere(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _repo_with_baseline(tmp_path, "# header\n")
    monkeypatch.delenv("RIGPLANE_REALTIME_BASE_REF", raising=False)
    monkeypatch.delenv("CI", raising=False)
    assert base_revision(tmp_path) is None  # a lone commit, no origin/main
    monkeypatch.setenv("CI", "true")
    assert (
        base_revision(tmp_path) is None
    )  # a root commit has no parent to compare with
    (tmp_path / "x.txt").write_text("x", encoding="utf-8")
    _git(tmp_path, "add", "x.txt")
    _git(tmp_path, "commit", "-q", "-m", "second")
    assert base_revision(tmp_path) == "HEAD^1"
    monkeypatch.setenv("RIGPLANE_REALTIME_BASE_REF", "feature-base")
    assert base_revision(tmp_path) == "feature-base"


def test_the_baseline_reader_skips_comments_and_blank_lines(tmp_path: Path) -> None:
    path = tmp_path / "baseline.txt"
    path.write_text("# c\n\na.py::t\n  b.py::u  \n", encoding="utf-8")
    assert read_baseline(path) == {"a.py::t", "b.py::u"}
    assert read_baseline(tmp_path / "missing.txt") == set()


def test_core_baseline_lives_next_to_the_tests() -> None:
    assert (ROOT / "tests" / "realtime_baseline.txt").is_file()
