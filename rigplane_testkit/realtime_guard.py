"""Collection-time guard against wall-clock dependence in tests (MOR-4075).

A test that sleeps for real, reads the real clock, or passes only if something finishes
inside a short real timeout fails under CI load. This pytest plugin refuses such tests
at collection time unless they carry ``@pytest.mark.realtime(reason="...")``, run on the
virtual clock (:mod:`rigplane_testkit.timing`), or are listed in the shrink-only baseline
of tests that still have to be converted.

Load it from the top-level ``conftest.py`` of a suite::

    pytest_plugins = ["rigplane_testkit.realtime_guard"]

and point the ini option ``realtime_baseline`` at the baseline file (one
``path::qualified_test_name`` per line, ``#`` comments allowed).

What counts as real-time dependence (a static scan of the test and of the same-file
helper functions it calls, transitively; helpers in other files are not followed):

* ``time.sleep(...)``; ``asyncio.sleep(x)`` unless ``x`` is the literal ``0``;
* ``time.monotonic`` / ``perf_counter`` / ``time`` and their ``_ns`` forms;
* a numeric literal timeout of 1 s or less (``timeout=``, ``wait_for(x, 0.5)``,
  ``join(0.5)``, ``asyncio.timeout(0.5)``).

A test is exempt when it has a ``realtime`` mark with a non-empty ``reason``, when it
carries one of the real-hardware marks (``hardware``, ``integration``,
``validation_hardware``, ``benchmark``), or when it runs on the virtual clock (the
``@virtual_clock`` decorator, a ``clock`` / ``virtual_clock`` parameter,
``VirtualClock(...)`` or ``.scope_to(...)`` in its body).

The baseline can only shrink. A flagged test that is not in it fails the run; a baseline
entry whose test is no longer flagged also fails the run (delete the line). Growing it is
a visible diff that ``check_baseline_does_not_grow`` rejects against the base branch.
"""

from __future__ import annotations

import ast
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

import pytest

__all__ = [
    "REAL_HARDWARE_MARKS",
    "Report",
    "analyze_source",
    "check_baseline_does_not_grow",
    "evaluate",
    "read_baseline",
]

REAL_HARDWARE_MARKS = frozenset(
    {"hardware", "integration", "validation_hardware", "benchmark"}
)

_SYNC_SLEEP = {("time", "sleep")}
_CLOCK_READS = {
    ("time", name)
    for name in (
        "monotonic",
        "perf_counter",
        "time",
        "monotonic_ns",
        "perf_counter_ns",
        "time_ns",
    )
}
_TIMEOUT_CALLS = frozenset(
    {
        "wait_for",
        "timeout",
        "wait",
        "join",
        "get",
        "result",
        "acquire",
        "settimeout",
        "communicate",
        "recv",
        "urlopen",
        "create_connection",
        "run",
        "check_output",
    }
)
_TIGHT_TIMEOUT_S = 1.0
_VIRTUAL_PARAMS = frozenset({"clock", "virtual_clock"})


@dataclass(frozen=True)
class TestFacts:
    """What the scan found for one test function."""

    __test__ = False  # not a test class, despite the name

    effects: frozenset[str]
    virtual: bool
    marked_realtime: bool = False

    def needs_conversion(self) -> bool:
        """Real-time dependence with no exemption visible in the source."""
        return bool(self.effects) and not self.virtual and not self.marked_realtime


def _number(node: ast.AST | None) -> float | None:
    if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)):
        return float(node.value)
    return None


def _call_name(call: ast.Call) -> str:
    func = call.func
    if isinstance(func, ast.Attribute):
        return func.attr
    return getattr(func, "id", "")


def _attr_pair(node: ast.AST) -> tuple[str, str] | None:
    if isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name):
        return node.value.id, node.attr
    return None


def _own_effects(fn: ast.AST) -> tuple[set[str], set[str]]:
    """(effects, same-file callees) of one function body."""
    effects: set[str] = set()
    callees: set[str] = set()
    for node in ast.walk(fn):
        if not isinstance(node, ast.Call):
            continue
        name = _call_name(node)
        pair = _attr_pair(node.func)
        if pair in _SYNC_SLEEP:
            effects.add("time.sleep")
        elif name == "sleep":
            value = _number(node.args[0]) if node.args else None
            if value is None or value > 0:
                effects.add("asyncio.sleep")
        elif pair in _CLOCK_READS:
            effects.add("clock-read")
        if name in _TIMEOUT_CALLS:
            keywords = {k.arg: k.value for k in node.keywords}
            bound = keywords.get("timeout")
            if bound is None and name == "wait_for" and len(node.args) >= 2:
                bound = node.args[1]
            if bound is None and name in {"join", "wait", "timeout"} and node.args:
                bound = node.args[0]
            value = _number(bound)
            if value is not None and 0 < value <= _TIGHT_TIMEOUT_S:
                effects.add("tight-timeout")
        func = node.func
        if isinstance(func, ast.Name):
            callees.add(func.id)
        elif (
            isinstance(func, ast.Attribute)
            and isinstance(func.value, ast.Name)
            and func.value.id in {"self", "cls"}
        ):
            callees.add(func.attr)
    return effects, callees


def _is_virtual(fn: ast.FunctionDef | ast.AsyncFunctionDef, source: str) -> bool:
    if any("virtual_clock" in ast.unparse(d) for d in fn.decorator_list):
        return True
    names = {a.arg for a in fn.args.args + fn.args.kwonlyargs}
    if names & _VIRTUAL_PARAMS:
        return True
    segment = ast.get_source_segment(source, fn) or ""
    return "VirtualClock(" in segment or ".scope_to(" in segment


def _marked_realtime(fn: ast.FunctionDef | ast.AsyncFunctionDef) -> bool:
    for decorator in fn.decorator_list:
        text = ast.unparse(decorator)
        if "mark.realtime(" in text and "reason=" in text:
            return True
    return False


def analyze_source(source: str) -> dict[str, TestFacts]:
    """Facts for every ``test*`` function of one module, keyed by qualified name."""
    tree = ast.parse(source)
    by_name: dict[str, tuple[set[str], set[str]]] = {}
    tests: dict[str, ast.FunctionDef | ast.AsyncFunctionDef] = {}

    def visit(node: ast.AST, prefix: str) -> None:
        for child in ast.iter_child_nodes(node):
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                by_name.setdefault(child.name, _own_effects(child))
                if child.name.startswith("test"):
                    tests[prefix + child.name] = child
            elif isinstance(child, ast.ClassDef):
                visit(child, prefix + child.name + ".")

    visit(tree, "")
    facts: dict[str, TestFacts] = {}
    for qualname, fn in tests.items():
        seen: set[str] = set()
        todo = [fn.name]
        effects: set[str] = set()
        while todo:
            name = todo.pop()
            if name in seen or name not in by_name:
                continue
            seen.add(name)
            own, callees = by_name[name]
            effects |= own
            todo.extend(callees)
        facts[qualname] = TestFacts(
            frozenset(effects), _is_virtual(fn, source), _marked_realtime(fn)
        )
    return facts


def read_baseline(path: Path) -> set[str]:
    if not path.is_file():
        return set()
    lines = (line.strip() for line in path.read_text(encoding="utf-8").splitlines())
    return {line for line in lines if line and not line.startswith("#")}


@dataclass
class Report:
    """The guard's verdict for one collection."""

    new: dict[str, frozenset[str]] = field(default_factory=dict)
    stale: list[str] = field(default_factory=list)
    bad_marks: list[str] = field(default_factory=list)
    flagged: set[str] = field(default_factory=set)

    def ok(self) -> bool:
        return not (self.new or self.stale or self.bad_marks)

    def message(self) -> str:
        parts: list[str] = []
        if self.bad_marks:
            parts.append(
                "realtime mark without a reason (use @pytest.mark.realtime(reason='...')):\n  "
                + "\n  ".join(sorted(self.bad_marks))
            )
        if self.new:
            parts.append(
                "tests that depend on the real clock or a real sleep (run them on the "
                "virtual clock from rigplane_testkit.timing, wait for events, or mark "
                "@pytest.mark.realtime(reason='...')):\n  "
                + "\n  ".join(
                    f"{k}  [{', '.join(sorted(v))}]"
                    for k, v in sorted(self.new.items())
                )
            )
        if self.stale:
            parts.append(
                "baseline entries that no longer need listing (delete these lines):\n  "
                + "\n  ".join(sorted(self.stale))
            )
        return "realtime guard (MOR-4075):\n" + "\n".join(parts)


def evaluate(
    scans: dict[str, dict[str, TestFacts]],
    selected: dict[str, TestFacts],
    exempt: set[str],
    baseline: set[str],
) -> Report:
    """Judge a collection.

    ``scans`` holds the static facts of every collected module (path -> qualname -> facts);
    ``selected`` the tests this run will execute (``path::qualname`` -> facts); ``exempt`` the
    selected tests whose runtime marks exempt them (real hardware, valid ``realtime``).
    A new offender is judged on the selected tests; a stale baseline entry on the whole
    module scan, so ``-k`` or a node id cannot make an entry look stale.
    """
    report = Report()
    for key, facts in selected.items():
        if facts.needs_conversion() and key not in exempt:
            report.flagged.add(key)
            if key not in baseline:
                report.new[key] = facts.effects
    for entry in baseline:
        path, _, qualname = entry.partition("::")
        if path not in scans:
            continue
        facts = scans[path].get(qualname)
        if facts is None or not facts.needs_conversion():
            report.stale.append(entry)
    return report


def check_baseline_does_not_grow(
    baseline: Path, base_ref: str, repo: Path
) -> list[str]:
    """Entries present in ``baseline`` but absent from the same file at ``base_ref``.

    An empty list means the baseline only shrank (or ``base_ref`` has no baseline yet).
    """
    rel = baseline.resolve().relative_to(repo.resolve()).as_posix()
    shown = subprocess.run(
        ["git", "show", f"{base_ref}:{rel}"],
        cwd=repo,
        capture_output=True,
        text=True,
        check=False,
    )
    if shown.returncode != 0:
        return []
    before = {
        line.strip()
        for line in shown.stdout.splitlines()
        if line.strip() and not line.startswith("#")
    }
    return sorted(read_baseline(baseline) - before)


# -- the pytest plugin ---------------------------------------------------------------------


def pytest_addoption(parser: pytest.Parser) -> None:
    parser.addini(
        "realtime_baseline",
        "baseline file of tests still on the real clock",
        default="",
    )
    parser.addoption(
        "--realtime-write-baseline",
        action="store_true",
        help="rewrite the realtime baseline from a FULL collection, then exit",
    )


def pytest_configure(config: pytest.Config) -> None:
    config.addinivalue_line(
        "markers",
        "realtime(reason): this test legitimately depends on the real clock; the reason is required",
    )


def _key(item: pytest.Item, rootdir: Path) -> tuple[str, str] | None:
    function = getattr(item, "function", None)
    path = getattr(item, "path", None)
    if function is None or path is None:
        return None
    try:
        rel = Path(path).resolve().relative_to(rootdir.resolve()).as_posix()
    except ValueError:
        return None
    qual = function.__qualname__.replace(".<locals>.", ".")
    return rel, f"{rel}::{qual}"


def pytest_collection_modifyitems(
    config: pytest.Config, items: list[pytest.Item]
) -> None:
    rootdir = Path(str(config.rootpath))
    baseline_ini = str(config.getini("realtime_baseline"))
    baseline_path = (rootdir / baseline_ini) if baseline_ini else None
    write = bool(config.getoption("--realtime-write-baseline"))
    baseline = read_baseline(baseline_path) if baseline_path and not write else set()

    scans: dict[str, dict[str, TestFacts]] = {}
    selected: dict[str, TestFacts] = {}
    exempt: set[str] = set()
    bad_marks: set[str] = set()
    for item in items:
        located = _key(item, rootdir)
        if located is None:
            continue
        rel, key = located
        if rel not in scans:
            try:
                scans[rel] = analyze_source((rootdir / rel).read_text(encoding="utf-8"))
            except (OSError, SyntaxError):
                scans[rel] = {}
        facts = scans[rel].get(key.split("::", 1)[1])
        if facts is None:
            continue
        selected[key] = facts
        if any(item.get_closest_marker(name) for name in REAL_HARDWARE_MARKS):
            exempt.add(key)
        mark = item.get_closest_marker("realtime")
        if mark is not None:
            if str(mark.kwargs.get("reason") or "").strip():
                exempt.add(key)
            else:
                bad_marks.add(key)

    report = evaluate(scans, selected, exempt, baseline)
    report.bad_marks = sorted(bad_marks)
    if write:
        if baseline_path is None:
            raise pytest.UsageError(
                "--realtime-write-baseline needs the realtime_baseline ini"
            )
        header = (
            "# Tests that still depend on the real clock (MOR-4075). This file may only shrink:\n"
            "# convert a test, then delete its line. New real-clock tests are not allowed here.\n"
        )
        baseline_path.write_text(
            header + "".join(f"{k}\n" for k in sorted(report.flagged)), encoding="utf-8"
        )
        raise pytest.UsageError(
            f"wrote {len(report.flagged)} entries to {baseline_path}"
        )
    if not report.ok():
        raise pytest.UsageError(report.message())


def main(argv: list[str] | None = None) -> int:
    """``python -m rigplane_testkit.realtime_guard BASELINE BASE_REF``: fail if it grew.

    Run it in CI against the pull request's base ref (the base branch must be fetched).
    """
    import argparse

    parser = argparse.ArgumentParser(description=main.__doc__)
    parser.add_argument("baseline", type=Path)
    parser.add_argument("base_ref")
    parser.add_argument("--repo", type=Path, default=Path.cwd())
    args = parser.parse_args(argv)
    grown = check_baseline_does_not_grow(args.baseline, args.base_ref, args.repo)
    if grown:
        print(
            f"the realtime baseline may only shrink, but gained {len(grown)} entries:\n  "
            + "\n  ".join(grown)
        )
        return 1
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
