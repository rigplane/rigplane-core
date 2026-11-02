"""Collection-time guard against wall-clock dependence in tests (MOR-4075).

A test that sleeps for real, reads the real clock, or passes only if something finishes
inside a short real timeout fails under CI load. This pytest plugin refuses such tests at
collection time unless they carry ``@pytest.mark.realtime(reason="...")`` or are listed in
the shrink-only baseline of tests that still have to be converted.

Load it from the top-level ``conftest.py`` of a suite::

    pytest_plugins = ["rigplane_testkit.realtime_guard"]

and point the ini option ``realtime_baseline`` at the baseline file (one
``path::qualified_test_name`` per line, ``#`` comments allowed).

What counts (a static scan of the test, of the same-file helpers it calls and of the
same-file fixtures it requests, transitively; helpers in other files are not followed):

* ``time.sleep(...)``                                                  -> ``time.sleep``
* ``time.monotonic`` / ``perf_counter`` / ``time`` and ``_ns`` forms     -> ``clock-read``
* ``asyncio.sleep(x)`` unless ``x`` is the literal ``0``               -> ``asyncio.sleep``
* ``asyncio.wait_for`` / ``asyncio.timeout`` / ``asyncio.wait`` with a
  numeric literal timeout of 1 s or less                              -> ``asyncio-timeout``
* a numeric literal timeout of 1 s or less on a thread/socket/queue
  call (``join``, ``Event.wait``, ``Queue.get``, ``Future.result``,
  ``settimeout``, ``urlopen`` ...)                                    -> ``thread-timeout``

Imports are resolved, so ``import time as t`` or ``from time import monotonic`` count.

The virtual clock (:mod:`rigplane_testkit.timing`) replaces ``loop.time`` of the test's own
loop, so it covers exactly ``asyncio.sleep`` and ``asyncio-timeout``. A test that runs on it
(the ``@virtual_clock`` decorator, a ``clock`` / ``virtual_clock`` parameter,
``VirtualClock(...)``) is still refused for ``time.sleep``, ``clock-read`` and
``thread-timeout``. The real-hardware marks (``hardware``, ``integration``,
``validation_hardware``, ``benchmark``) exempt a test case; so does a ``realtime`` mark with
a non-empty ``reason``. Exemptions are judged per collected case of a parametrized test; a
function is an offender when any of its cases is not exempt.

The baseline can only shrink. A flagged test that is not in it fails the run; an entry whose
test no longer needs listing (converted, deleted, or its module gone) also fails the run.
Growth is checked against the previous revision (see :func:`base_revision`) at collection and
by ``python -m rigplane_testkit.realtime_guard BASELINE BASE_REF``.
"""

from __future__ import annotations

import ast
import os
import subprocess
import warnings
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

import pytest

__all__ = [
    "BaselineCheck",
    "REAL_HARDWARE_MARKS",
    "Report",
    "TestFacts",
    "analyze_source",
    "base_revision",
    "check_baseline_does_not_grow",
    "evaluate",
    "read_baseline",
]

REAL_HARDWARE_MARKS = frozenset(
    {"hardware", "integration", "validation_hardware", "benchmark"}
)

_ASYNC_KINDS = frozenset(
    {"asyncio.sleep", "asyncio-timeout"}
)  # what the virtual clock covers
_TIGHT_TIMEOUT_S = 1.0
_VIRTUAL_PARAMS = frozenset({"clock", "virtual_clock"})

_CLOCK_READS = frozenset(
    f"time.{name}"
    for name in (
        "monotonic",
        "perf_counter",
        "time",
        "monotonic_ns",
        "perf_counter_ns",
        "time_ns",
    )
)
_ASYNC_TIMEOUT_APIS = {
    "asyncio.wait_for": 1,
    "asyncio.timeout": 0,
    "asyncio.wait": None,
}
# method name -> index of the positional timeout (None: keyword only)
_THREAD_TIMEOUT_METHODS: dict[str, int | None] = {
    "join": 0,
    "wait": 0,
    "result": 0,
    "exception": 0,
    "settimeout": 0,
    "get": 1,
    "acquire": 1,
    "communicate": 1,
    "urlopen": 2,
    "create_connection": 1,
    "run": None,
    "check_output": None,
    "check_call": None,
}


@dataclass(frozen=True)
class TestFacts:
    """What the scan found for one test function."""

    __test__ = False  # not a test class, despite the name

    effects: frozenset[str]
    virtual: bool
    marked_realtime: bool = False

    def violations(self) -> frozenset[str]:
        """Effects no visible exemption covers."""
        if self.marked_realtime:
            return frozenset()
        return self.effects - _ASYNC_KINDS if self.virtual else self.effects

    def needs_conversion(self) -> bool:
        return bool(self.violations())


# -- the static scan -----------------------------------------------------------------------


def _number(node: ast.AST | None) -> float | None:
    if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)):
        return float(node.value)
    return None


def _import_map(tree: ast.AST) -> dict[str, str]:
    """Local name -> dotted origin, for every import anywhere in the module."""
    names: dict[str, str] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                local = alias.asname or alias.name.split(".")[0]
                names[local] = alias.name if alias.asname else alias.name.split(".")[0]
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            for alias in node.names:
                names[alias.asname or alias.name] = f"{node.module}.{alias.name}"
    return names


def _dotted(func: ast.AST, imports: dict[str, str]) -> str | None:
    """The imported dotted name a call target resolves to (``time.sleep``), if any."""
    if isinstance(func, ast.Name):
        return imports.get(func.id)
    if isinstance(func, ast.Attribute):
        base = _dotted(func.value, imports)
        return f"{base}.{func.attr}" if base else None
    return None


def _timeout_value(call: ast.Call, positional: int | None) -> float | None:
    for keyword in call.keywords:
        if keyword.arg == "timeout":
            return _number(keyword.value)
    if positional is not None and len(call.args) > positional:
        return _number(call.args[positional])
    return None


def _thread_timeout(call: ast.Call, name: str) -> float | None:
    """The literal timeout of a thread/socket/queue call, keyword or positional.

    ``get`` / ``acquire`` take ``(block, timeout)``: a positional number only counts after
    a literal boolean, so ``mapping.get(key, 0.5)`` is not read as a timeout.
    """
    for keyword in call.keywords:
        if keyword.arg == "timeout":
            return _number(keyword.value)
    position = _THREAD_TIMEOUT_METHODS[name]
    if position is None or len(call.args) <= position:
        return None
    if name in {"get", "acquire"}:
        first = call.args[0]
        if not (isinstance(first, ast.Constant) and isinstance(first.value, bool)):
            return None
    return _number(call.args[position])


def _tight(value: float | None) -> bool:
    return value is not None and 0 < value <= _TIGHT_TIMEOUT_S


@dataclass
class _Body:
    effects: set[str] = field(default_factory=set)
    module_calls: set[str] = field(default_factory=set)
    self_calls: set[str] = field(default_factory=set)


def _scan_body(fn: ast.AST, imports: dict[str, str]) -> _Body:
    body = _Body()
    for node in ast.walk(fn):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        dotted = _dotted(func, imports)
        method = func.attr if isinstance(func, ast.Attribute) else None
        if dotted == "time.sleep":
            body.effects.add("time.sleep")
        elif dotted == "asyncio.sleep":
            delay = node.args[0] if node.args else None
            for keyword in node.keywords:
                if keyword.arg == "delay":
                    delay = keyword.value
            value = _number(delay)
            if value is None or value > 0:
                body.effects.add("asyncio.sleep")
        elif dotted in _CLOCK_READS:
            body.effects.add("clock-read")
        elif dotted in _ASYNC_TIMEOUT_APIS:
            if _tight(_timeout_value(node, _ASYNC_TIMEOUT_APIS[dotted])):
                body.effects.add("asyncio-timeout")
        else:
            name = method or (dotted.rpartition(".")[2] if dotted else "")
            if name in _THREAD_TIMEOUT_METHODS and _tight(_thread_timeout(node, name)):
                body.effects.add("thread-timeout")
        if isinstance(func, ast.Name):
            body.module_calls.add(func.id)
        elif (
            isinstance(func, ast.Attribute)
            and isinstance(func.value, ast.Name)
            and func.value.id in {"self", "cls"}
        ):
            body.self_calls.add(func.attr)
    return body


def _is_virtual(fn: ast.FunctionDef | ast.AsyncFunctionDef, source: str) -> bool:
    if any("virtual_clock" in ast.unparse(d) for d in fn.decorator_list):
        return True
    if {a.arg for a in fn.args.args + fn.args.kwonlyargs} & _VIRTUAL_PARAMS:
        return True
    return "VirtualClock(" in (ast.get_source_segment(source, fn) or "")


def _marked_realtime(fn: ast.FunctionDef | ast.AsyncFunctionDef) -> bool:
    """A decorator that IS ``@pytest.mark.realtime(reason="...")`` (not a mark nested in
    ``parametrize``: those exempt single cases and are judged per collected case)."""
    for decorator in fn.decorator_list:
        if not isinstance(decorator, ast.Call):
            continue
        if not ast.unparse(decorator.func).endswith("mark.realtime"):
            continue
        for keyword in decorator.keywords:
            if (
                keyword.arg == "reason"
                and isinstance(keyword.value, ast.Constant)
                and isinstance(keyword.value.value, str)
                and keyword.value.value.strip()
            ):
                return True
    return False


def _fixture_info(fn: ast.FunctionDef | ast.AsyncFunctionDef) -> tuple[bool, bool]:
    """(is a pytest fixture, is autouse)."""
    for decorator in fn.decorator_list:
        text = ast.unparse(decorator)
        if text.startswith(("pytest.fixture", "fixture", "pytest_asyncio.fixture")):
            return True, "autouse=True" in text
    return False, False


def analyze_source(source: str) -> dict[str, TestFacts]:
    """Facts for every ``test*`` function of one module, keyed by qualified name.

    Definitions are keyed by qualified scope (``Class.method``): a ``self.helper()`` call
    resolves inside its own class, a bare call to a module-level function, so repeated names
    in different classes never share effects.
    """
    tree = ast.parse(source)
    imports = _import_map(tree)
    defs: dict[str, ast.FunctionDef | ast.AsyncFunctionDef] = {}

    def visit(node: ast.AST, prefix: str) -> None:
        for child in ast.iter_child_nodes(node):
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                defs[prefix + child.name] = child
            elif isinstance(child, ast.ClassDef):
                visit(child, prefix + child.name + ".")

    visit(tree, "")
    bodies = {qual: _scan_body(fn, imports) for qual, fn in defs.items()}
    fixtures: dict[str, str] = {}  # fixture name -> qualified definition (last wins)
    autouse: list[str] = []
    for qual, fn in defs.items():
        is_fixture, is_autouse = _fixture_info(fn)
        if is_fixture:
            fixtures[fn.name] = qual
            if is_autouse and "." not in qual:
                autouse.append(qual)

    def closure(root: str) -> set[str]:
        effects: set[str] = set()
        seen: set[str] = set()
        todo = [root, *autouse]
        while todo:
            qual = todo.pop()
            if qual in seen or qual not in bodies:
                continue
            seen.add(qual)
            body = bodies[qual]
            effects |= body.effects
            owner = qual.rpartition(".")[0]
            for name in body.module_calls:
                if name in bodies:
                    todo.append(name)
            for name in body.self_calls:
                local = f"{owner}.{name}" if owner else name
                if local in bodies:
                    todo.append(local)
            for arg in defs[qual].args.args + defs[qual].args.kwonlyargs:
                if arg.arg in fixtures:
                    todo.append(fixtures[arg.arg])
        return effects

    facts: dict[str, TestFacts] = {}
    for qual, fn in defs.items():
        if fn.name.startswith("test") and not _fixture_info(fn)[0]:
            facts[qual] = TestFacts(
                frozenset(closure(qual)), _is_virtual(fn, source), _marked_realtime(fn)
            )
    return facts


# -- judging a collection --------------------------------------------------------------------


def read_baseline(path: Path) -> set[str]:
    if not path.is_file():
        return set()
    return _parse_baseline(path.read_text(encoding="utf-8"))


def _parse_baseline(text: str) -> set[str]:
    lines = (line.strip() for line in text.splitlines())
    return {line for line in lines if line and not line.startswith("#")}


@dataclass
class Report:
    """The guard's verdict for one collection."""

    new: dict[str, frozenset[str]] = field(default_factory=dict)
    stale: list[str] = field(default_factory=list)
    bad_marks: list[str] = field(default_factory=list)
    flagged: set[str] = field(default_factory=set)
    grown: list[str] = field(default_factory=list)

    def ok(self) -> bool:
        return not (self.new or self.stale or self.bad_marks or self.grown)

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
                    f"{key}  [{', '.join(sorted(kinds))}]"
                    for key, kinds in sorted(self.new.items())
                )
            )
        if self.stale:
            parts.append(
                "baseline entries that no longer need listing (delete these lines):\n  "
                + "\n  ".join(sorted(self.stale))
            )
        if self.grown:
            parts.append(
                "the baseline may only shrink, but gained these entries:\n  "
                + "\n  ".join(sorted(self.grown))
            )
        return "realtime guard (MOR-4075):\n" + "\n".join(parts)


def evaluate(
    scans: dict[str, dict[str, TestFacts]],
    selected: dict[str, tuple[TestFacts, list[bool]]],
    baseline: set[str],
    file_exists: Callable[[str], bool] = lambda path: True,
) -> Report:
    """Judge a collection.

    ``scans`` holds the static facts of every collected module (path -> qualname -> facts);
    ``selected`` maps each selected test function (``path::qualname``) to its facts and, per
    collected case, whether that case's runtime marks exempt it. A function offends when any
    case is not exempt. A stale baseline entry is judged on the whole module scan (so ``-k``
    or a node id cannot make an entry look stale) or, when its module is gone, on
    ``file_exists``.
    """
    report = Report()
    for key, (facts, exemptions) in selected.items():
        if facts.needs_conversion() and not all(exemptions):
            report.flagged.add(key)
            if key not in baseline:
                report.new[key] = facts.violations()
    for entry in baseline:
        path, _, qualname = entry.partition("::")
        if path in scans:
            facts = scans[path].get(qualname)
            if facts is None or not facts.needs_conversion():
                report.stale.append(entry)
        elif not file_exists(path):
            report.stale.append(entry)
    return report


# -- the baseline only shrinks -----------------------------------------------------------------


@dataclass(frozen=True)
class BaselineCheck:
    """Result of comparing a baseline with the same file at a base revision."""

    status: str  # "ok" | "bootstrap" | "grew" | "unavailable"
    grown: tuple[str, ...] = ()
    detail: str = ""


def _git(repo: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", *args], cwd=repo, capture_output=True, text=True, check=False
    )


def check_baseline_does_not_grow(
    baseline: Path, base_ref: str, repo: Path
) -> BaselineCheck:
    """Compare ``baseline`` with the same file at ``base_ref``.

    ``bootstrap``: the base is valid but has no baseline yet (the file is being introduced).
    ``unavailable``: the base ref cannot be resolved, so nothing was compared.
    """
    rel = baseline.resolve().relative_to(repo.resolve()).as_posix()
    if (
        _git(
            repo, "rev-parse", "--verify", "--quiet", f"{base_ref}^{{commit}}"
        ).returncode
        != 0
    ):
        return BaselineCheck(
            "unavailable", detail=f"cannot resolve base revision {base_ref!r}"
        )
    shown = _git(repo, "show", f"{base_ref}:{rel}")
    if shown.returncode != 0:
        return BaselineCheck("bootstrap")
    grown = tuple(sorted(read_baseline(baseline) - _parse_baseline(shown.stdout)))
    return BaselineCheck("grew" if grown else "ok", grown)


def base_revision(repo: Path) -> str | None:
    """The revision the current baseline is compared with, or ``None`` when there is none.

    An explicit ``RIGPLANE_REALTIME_BASE_REF`` wins. In CI (``CI`` set) it is the first
    parent of the checked-out commit: the base branch for a pull-request or merge-queue merge
    commit, the previous commit for a push to the base branch. Elsewhere (a local work branch)
    it is ``origin/main`` when that exists.
    """
    explicit = os.environ.get("RIGPLANE_REALTIME_BASE_REF")
    if explicit:
        return explicit
    parents = _git(repo, "rev-list", "--parents", "-n", "1", "HEAD")
    fields = parents.stdout.split() if parents.returncode == 0 else []
    if os.environ.get("CI"):
        # CI checks out a merge commit (pull request, merge queue: first parent = base
        # branch) or a single commit of the base branch itself (its previous commit).
        return "HEAD^1" if len(fields) >= 2 else None
    main = _git(repo, "rev-parse", "--verify", "--quiet", "origin/main^{commit}")
    return "origin/main" if main.returncode == 0 else None  # a local work branch


def main(argv: list[str] | None = None) -> int:
    """``python -m rigplane_testkit.realtime_guard BASELINE BASE_REF``: fail if it grew."""
    import argparse

    parser = argparse.ArgumentParser(description=main.__doc__)
    parser.add_argument("baseline", type=Path)
    parser.add_argument("base_ref")
    parser.add_argument("--repo", type=Path, default=Path.cwd())
    args = parser.parse_args(argv)
    check = check_baseline_does_not_grow(args.baseline, args.base_ref, args.repo)
    if check.status == "unavailable":
        print(f"realtime baseline not verified: {check.detail}")
        return 1
    if check.status == "grew":
        print(
            f"the realtime baseline may only shrink, but gained {len(check.grown)} entries:\n  "
            + "\n  ".join(check.grown)
        )
        return 1
    return 0


# -- the pytest plugin -------------------------------------------------------------------------


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
        "realtime(reason): this test legitimately depends on the real clock; the reason is "
        "required",
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
    return rel, f"{rel}::{function.__qualname__.replace('.<locals>.', '.')}"


def _case_exempt(item: pytest.Item) -> tuple[bool, bool]:
    """(exempt, realtime mark present without a reason)."""
    if any(item.get_closest_marker(name) for name in REAL_HARDWARE_MARKS):
        return True, False
    mark = item.get_closest_marker("realtime")
    if mark is None:
        return False, False
    has_reason = bool(str(mark.kwargs.get("reason") or "").strip())
    return has_reason, not has_reason


def _growth(rootdir: Path, baseline_path: Path) -> list[str]:
    base = base_revision(rootdir)
    if base is None:
        _warn_if_ci("no base revision to compare the realtime baseline with")
        return []
    try:
        check = check_baseline_does_not_grow(baseline_path, base, rootdir)
    except (OSError, ValueError) as exc:
        _warn_if_ci(f"realtime baseline growth not verified: {exc}")
        return []
    if check.status == "unavailable":
        _warn_if_ci(f"realtime baseline growth not verified: {check.detail}")
    return list(check.grown)


def _warn_if_ci(message: str) -> None:
    if os.environ.get("CI"):
        warnings.warn(message, stacklevel=2)


def pytest_collection_modifyitems(
    config: pytest.Config, items: list[pytest.Item]
) -> None:
    rootdir = Path(str(config.rootpath))
    baseline_ini = str(config.getini("realtime_baseline"))
    baseline_path = (rootdir / baseline_ini) if baseline_ini else None
    write = bool(config.getoption("--realtime-write-baseline"))
    baseline = read_baseline(baseline_path) if baseline_path and not write else set()

    scans: dict[str, dict[str, TestFacts]] = {}
    cases: dict[str, tuple[TestFacts, list[bool]]] = {}
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
        exempt, bad = _case_exempt(item)
        if bad:
            bad_marks.add(key)
        cases.setdefault(key, (facts, []))[1].append(exempt)

    report = evaluate(scans, cases, baseline, lambda path: (rootdir / path).is_file())
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
    if baseline_path is not None:
        report.grown = _growth(rootdir, baseline_path)
    if not report.ok():
        raise pytest.UsageError(report.message())


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
