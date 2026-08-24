"""Pin every dual-implementation command builder against its profile map.

Builders in ``src/rigplane/commands/`` that carry both a ``cmd_map`` branch
and a hardcoded fallback hold the same CI-V knowledge twice, and only a
hand-written subset compared the two: ``test_command_map_integration.py``
calls a fixed list of builders against the IC-7610 map, each at one
argument set with every optional argument left at its default, and
``test_commands.py: test_speech_cmd_map_prefers_set_speech_key`` and
``test_rig_ic7300.py: test_get_speech_cmd_map_uses_set_speech`` each pin
``get_speech`` alone. Outside that subset the two copies could disagree
silently, and at least one pair does: the ``cmd_map`` branch of
``scope.py: get_scope_mode`` and its siblings drops the ``receiver``
argument that the fallback hands to ``scope.py: _scope_query``.

This test is the general form of that check: it builds every builder it can
reach both ways, for every profile the library loads out of ``rigs/``, and
requires the two frames to be byte-identical. It does not replace those
hand-written checks: they are kept, and whether a builder one of them
names is also swept here is answerable from
``command_map_parity_uncovered.txt``, which lists everything this sweep
could not compare.

Known disagreements live in ``command_map_parity_divergences.txt``, one row
per (profile, builder, argument case), each recording the two frames it
produced. A disagreement missing from that file fails, and so does a row
there that no longer disagrees, which must be deleted rather than kept.
That does not make the file physically unable to grow — regenerating it
would add a new row — but growth can only arrive as a committed, reviewable
edit to this file, never silently.

Nothing here fixes a divergence. Which commands take a receiver prefix byte
is MOR-1981, and adding one where the radio does not expect it is a write,
not a no-op — see ``commands/scope.py: SCOPE_RECEIVER_SELECTOR_SUBS``.

What could not be compared is not skipped silently:
``command_map_parity_uncovered.txt`` records the commands a profile's map
omits, the profiles whose TOML defines every command as CAT so that
``profiles/rig_loader.py: RigConfig.to_command_map`` drops all of them, the
builders whose arguments no probe value satisfied, the sites no compared
builder reaches, and a census of how much was compared. Every number in it
is re-measured and asserted here, so none of it can go stale unnoticed.

Regenerate both files after an intentional change::

    RIGPLANE_REGEN_COMMAND_MAP_PARITY=1 uv run pytest \
        tests/test_command_map_parity.py
"""

from __future__ import annotations

import ast
import enum
import functools
import importlib
import inspect
import itertools
import os
import pathlib
import typing
from collections import defaultdict

import pytest

from rigplane.commands.command_spec import CatCommandSpec
from rigplane.profiles import resolve_radio_profile
from rigplane.profiles.rig_loader import discover_rigs

REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]
COMMANDS_DIR = REPO_ROOT / "src" / "rigplane" / "commands"
RIGS_DIR = REPO_ROOT / "rigs"
DIVERGENCES_FILE = pathlib.Path(__file__).with_name(
    "command_map_parity_divergences.txt"
)
UNCOVERED_FILE = pathlib.Path(__file__).with_name("command_map_parity_uncovered.txt")
REGEN_ENV = "RIGPLANE_REGEN_COMMAND_MAP_PARITY"

# Never probed: the comparison itself varies ``cmd_map`` and supplies the
# profile's ``to_addr``, and it leaves ``from_addr`` at its default in both
# frames of every pair, so no value for it is ever synthesised or passed.
_HARNESS_PARAMS = frozenset({"to_addr", "from_addr", "cmd_map"})
# Used only while searching for arguments a builder accepts; the
# comparison below rebuilds every frame with the profile's own
# ``civ_addr``. A builder that rejected an argument depending on the
# address would raise there rather than pass quietly.
_PROBE_ADDR = 0x94
# Probe values, tried in order. The search that consumes them has no
# per-builder branch: a builder no value satisfies is reported as a
# ``noargs`` row, never given a bespoke argument here.
_INTS = (0, 1, 2, 3, 5, 10, 18, 30, 50, 100, 255, 600, 1000, 2026)
_FREQS = (14_074_000, 21_074_000)
_FLOATS = (0.0, 88.5, 1000.0, 14_074_000.0)
_SEARCH_BUDGET = 8000  # combinations enumerated before a builder is given up on

Key = tuple[str, str]  # (module file name, function name)


# ── static analysis: the dual-implementation sites and who reaches them ──


def _tests_cmd_map(node: ast.AST) -> bool:
    """True if *node* contains a literal ``cmd_map is not None`` test."""
    for child in ast.walk(node):
        if not (isinstance(child, ast.Compare) and isinstance(child.left, ast.Name)):
            continue
        if child.left.id != "cmd_map":
            continue
        for op, other in zip(child.ops, child.comparators):
            if isinstance(op, ast.IsNot) and (
                isinstance(other, ast.Constant) and other.value is None
            ):
                return True
    return False


class _Graph(typing.NamedTuple):
    sites: frozenset[Key]  # functions branching on ``cmd_map is not None``
    index: dict[str, frozenset[Key]]  # function name -> where it is defined
    calls: dict[Key, frozenset[str]]  # caller -> names it forwards cmd_map to


@functools.lru_cache(maxsize=1)
def _graph() -> _Graph:
    """Read the package's dual-implementation structure out of its source.

    ``calls`` is how a builder that only delegates reaches a shared
    template in ``commands/_builders.py``: it records the names each
    function calls while forwarding ``cmd_map``.
    """
    sites: set[Key] = set()
    index: dict[str, set[Key]] = defaultdict(set)
    calls: dict[Key, set[str]] = defaultdict(set)
    for path in sorted(COMMANDS_DIR.glob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in tree.body:
            if not isinstance(node, ast.FunctionDef):
                continue
            key = (path.name, node.name)
            index[node.name].add(key)
            if _tests_cmd_map(node):
                sites.add(key)
            for child in ast.walk(node):
                if (
                    isinstance(child, ast.Call)
                    and isinstance(child.func, ast.Name)
                    and any(kw.arg == "cmd_map" for kw in child.keywords)
                ):
                    calls[key].add(child.func.id)
    return _Graph(
        frozenset(sites),
        {name: frozenset(keys) for name, keys in index.items()},
        {key: frozenset(names) for key, names in calls.items()},
    )


def _reachable_sites(key: Key) -> frozenset[Key]:
    """Dual-implementation sites statically reachable from *key*.

    An over-approximation of what a call executes: ``_graph`` records a
    delegation wherever ``cmd_map`` is forwarded, so a delegation guarded
    by a condition counts here even on a call that does not take it.
    """
    graph = _graph()
    seen: set[Key] = set()
    found: set[Key] = set()
    stack = [key]
    while stack:
        current = stack.pop()
        if current in seen:
            continue
        seen.add(current)
        if current in graph.sites:
            found.add(current)
        for name in graph.calls.get(current, ()):
            defined = graph.index.get(name, frozenset())
            local = [d for d in defined if d[0] == current[0]]
            if local:
                stack.append(local[0])
            elif len(defined) == 1:
                stack.append(next(iter(defined)))
    return frozenset(found)


# ── the callable surface, and arguments it accepts ──


@functools.lru_cache(maxsize=1)
def _builders() -> dict[Key, typing.Any]:
    """Every public builder in the package that takes a ``cmd_map``.

    Keyed by the defining function's own name, so a module-level alias
    collapses onto the function it aliases instead of being compared twice
    under both names.
    """
    found: dict[Key, typing.Any] = {}
    for path in sorted(COMMANDS_DIR.glob("*.py")):
        if path.stem == "__init__":
            continue
        module = importlib.import_module(f"rigplane.commands.{path.stem}")
        for value in vars(module).values():
            if not inspect.isfunction(value) or value.__name__.startswith("_"):
                continue
            if value.__module__ != module.__name__:
                continue
            if "cmd_map" in inspect.signature(value).parameters:
                found[(path.name, value.__name__)] = value
    return found


def _values_for(fn: typing.Any, param: inspect.Parameter) -> tuple[typing.Any, ...]:
    """Probe values for *param*, derived from its annotation alone."""
    try:
        annotation = eval(param.annotation, fn.__globals__)  # noqa: S307
    except Exception:
        return ()
    args = typing.get_args(annotation)
    types = list(args) if args else [annotation]
    members = [
        member
        for candidate in types
        if isinstance(candidate, type) and issubclass(candidate, enum.Enum)
        for member in candidate
    ]
    if members:
        return tuple(members) + _INTS
    if bool in types:
        return (False, True)
    if str in types:
        return ("TEST",)
    if float in types:
        return _FLOATS + _FREQS
    if int in types:
        return _INTS + _FREQS
    return ()


def _split_params(
    fn: typing.Any,
) -> tuple[list[inspect.Parameter], list[inspect.Parameter]]:
    required: list[inspect.Parameter] = []
    optional: list[inspect.Parameter] = []
    for param in inspect.signature(fn).parameters.values():
        if param.name not in _HARNESS_PARAMS:
            (optional if param.default is not param.empty else required).append(param)
    return required, optional


def _candidate_kwargs(
    fn: typing.Any, required: list[inspect.Parameter]
) -> typing.Iterator[dict[str, typing.Any]]:
    """Argument dicts to try for *fn*, most likely first."""
    if not required:
        yield {}
        return
    ladders = [_values_for(fn, param) for param in required]
    if any(not ladder for ladder in ladders):
        return
    names = [param.name for param in required]
    seen: set[tuple[typing.Any, ...]] = set()
    for i in range(max(len(ladder) for ladder in ladders)):
        combo = tuple(ladder[i % len(ladder)] for ladder in ladders)
        if combo not in seen:
            seen.add(combo)
            yield dict(zip(names, combo))
    for budget, combo in enumerate(itertools.product(*ladders)):
        if budget >= _SEARCH_BUDGET:
            return
        if combo not in seen:
            seen.add(combo)
            yield dict(zip(names, combo))


def _accepts(fn: typing.Any, kwargs: dict[str, typing.Any]) -> bool:
    try:
        fn(to_addr=_PROBE_ADDR, cmd_map=None, **kwargs)
    except Exception:
        return False
    return True


def _format_case(kwargs: dict[str, typing.Any]) -> str:
    if not kwargs:
        return "(no arguments)"
    return ", ".join(
        f"{name}="
        + (f"{type(v).__name__}.{v.name}" if isinstance(v, enum.Enum) else repr(v))
        for name, v in sorted(kwargs.items())
    )


@functools.lru_cache(maxsize=1)
def _cases() -> tuple[dict[Key, list[dict[str, typing.Any]]], frozenset[Key]]:
    """Argument cases per builder, plus the builders none could be found for.

    The first case passes only required arguments; one further case per
    optional argument overrides it with a non-default value, because a
    divergence can hide behind a default — ``scope.py: get_scope_mode``
    builds identical frames until ``receiver`` is given a value. Argument
    validation happens before a builder reads ``to_addr``, so this search
    runs once and its result is reused for every profile.
    """
    per_builder: dict[Key, list[dict[str, typing.Any]]] = {}
    unsynthesisable: set[Key] = set()
    for key, fn in _builders().items():
        required, optional = _split_params(fn)
        base = next(
            (kw for kw in _candidate_kwargs(fn, required) if _accepts(fn, kw)), None
        )
        if base is None:
            unsynthesisable.add(key)
            continue
        cases = [base]
        for param in optional:
            for value in _values_for(fn, param):
                if type(value) is type(param.default) and value == param.default:
                    continue
                probe = dict(base, **{param.name: value})
                if _accepts(fn, probe):
                    cases.append(probe)
                    break
        per_builder[key] = cases
    return per_builder, frozenset(unsynthesisable)


# ── the comparison ──


class _Report(typing.NamedTuple):
    divergences: dict[tuple[str, str, str], str]
    map_gaps: dict[str, tuple[str, ...]]
    cat_only_profiles: dict[str, int]
    unsynthesisable: tuple[str, ...]
    uncompared_sites: tuple[str, ...]
    census: dict[str, int]


def _cat_only_profiles(rigs: dict[str, typing.Any]) -> dict[str, int]:
    """Profiles whose every command is CAT, and how many that is.

    ``CommandSpec`` is the union ``CivCommandSpec | CatCommandSpec`` and
    ``profiles/rig_loader.py: RigConfig.to_command_map`` keeps only the
    CI-V half, so such a profile ends up with an empty ``CommandMap`` and
    every lookup against it raises -- it is a gap for every command a
    builder asks for, whatever its TOML happens to define.
    """
    cat_only: dict[str, int] = {}
    for model, config in rigs.items():
        specs = list(config.commands.values())
        if specs and all(isinstance(spec, CatCommandSpec) for spec in specs):
            cat_only[model] = len(specs)
    return cat_only


@functools.lru_cache(maxsize=1)
def _report() -> _Report:
    per_builder, unsynthesisable = _cases()
    rigs = discover_rigs(RIGS_DIR)
    divergences: dict[tuple[str, str, str], str] = {}
    gaps: dict[str, set[str]] = defaultdict(set)
    gap_pairs: set[tuple[str, Key]] = set()
    compared: set[Key] = set()
    identical = 0

    for model, config in sorted(rigs.items()):
        cmd_map = config.to_command_map()
        to_addr = resolve_radio_profile(model=model).civ_addr
        for key, cases in sorted(per_builder.items()):
            fn = _builders()[key]
            for kwargs in cases:
                fallback = fn(to_addr=to_addr, cmd_map=None, **kwargs)
                try:
                    mapped = fn(to_addr=to_addr, cmd_map=cmd_map, **kwargs)
                except KeyError as exc:
                    # Only CommandMap.get should raise KeyError here;
                    # anything else is a real failure, so re-raise it.
                    if "Unknown command " not in str(exc):
                        raise
                    name = str(exc).split("Unknown command ")[1].split(".")[0]
                    gaps[name.strip("'\"")].add(model)
                    gap_pairs.add((model, key))
                    continue
                compared |= _reachable_sites(key)
                if mapped == fallback:
                    identical += 1
                else:
                    row = (model, f"{key[0]}:{key[1]}", _format_case(kwargs))
                    divergences[row] = f"map={mapped.hex()} fallback={fallback.hex()}"

    cat_only = _cat_only_profiles(rigs)
    return _Report(
        divergences=divergences,
        map_gaps={name: tuple(sorted(models)) for name, models in gaps.items()},
        cat_only_profiles=cat_only,
        unsynthesisable=tuple(sorted(f"{m}:{f}" for m, f in unsynthesisable)),
        uncompared_sites=tuple(
            sorted(f"{m}:{f}" for m, f in _graph().sites - compared)
        ),
        census={
            "dual_implementation_sites": len(_graph().sites),
            "sites_compared": len(compared),
            "public_builders": len(_builders()),
            "builders_compared": len(per_builder),
            "profiles": len(rigs),
            "cat_only_profiles": len(cat_only),
            "frames_identical": identical,
            "frames_diverged": len(divergences),
            "profile_builder_map_gaps": len(gap_pairs),
        },
    )


# ── baseline files ──


def _read_rows(path: pathlib.Path) -> list[tuple[str, ...]]:
    return [
        tuple(line.split("\t"))
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    ]


def _render_divergences(report: _Report) -> str:
    header = [
        "# Known cmd_map/fallback disagreements, tab separated:",
        "#   <profile>  <module>:<builder>  <arguments>  <observed frames>",
        "# tests/test_command_map_parity.py fails on a disagreement missing",
        "# here, and on a row here that no longer disagrees -- delete that",
        "# row, never keep it. Regenerating can still add rows, so read any",
        "# addition as a new divergence someone has to justify.",
    ]
    rows = [
        f"{a}\t{b}\t{c}\t{d}" for (a, b, c), d in sorted(report.divergences.items())
    ]
    return "\n".join(header + rows) + "\n"


def _render_uncovered(report: _Report) -> str:
    header = [
        "# What tests/test_command_map_parity.py could NOT compare, and how",
        "# much it did. Tab separated, five row kinds:",
        "#   census    <name>              <count>",
        "#   cat-only  <profile>           <commands, every one of them CAT>",
        "#   gap       <command name>      <profiles whose map omits it>",
        "#   noargs    <module>:<builder>  no probe value was accepted",
        "#   unpinned  <module>:<builder>  no compared builder reaches it",
        "# A 'gap' row means that profile's CommandMap holds no CI-V entry",
        "# for the command, so the cmd_map branch raises KeyError and there",
        "# is no frame to compare. Two causes land a command here: the rig",
        "# TOML does not define it, or it defines it as a CAT command, which",
        "# profiles/rig_loader.py: RigConfig.to_command_map drops. A",
        "# 'cat-only' profile has no CI-V command at all -- every command its",
        "# TOML defines is CAT, so its CommandMap is empty and every lookup",
        "# against it raises, whether or not the TOML names that command.",
        "# Read the gap rows as a follow-up worklist only for the profiles",
        "# they name that have no 'cat-only' row.",
        "# 'sites_compared' counts dual-implementation sites statically",
        "# reachable from a builder that was compared, not sites observed to",
        "# execute. Every number here is re-measured and asserted by that",
        "# test. Regenerate, do not edit.",
    ]
    rows = [f"census\t{name}\t{n}" for name, n in sorted(report.census.items())]
    rows += [
        f"cat-only\t{model}\t{n}"
        for model, n in sorted(report.cat_only_profiles.items())
    ]
    rows += [
        f"gap\t{name}\t{','.join(models)}"
        for name, models in sorted(report.map_gaps.items())
    ]
    rows += [f"noargs\t{name}" for name in report.unsynthesisable]
    rows += [f"unpinned\t{name}" for name in report.uncompared_sites]
    return "\n".join(header + rows) + "\n"


@pytest.fixture(scope="module")
def report() -> _Report:
    result = _report()
    # Not a truthiness test: ``REGEN_ENV=0`` must mean off, not on.
    if os.environ.get(REGEN_ENV, "") not in {"", "0"}:
        DIVERGENCES_FILE.write_text(_render_divergences(result), encoding="utf-8")
        UNCOVERED_FILE.write_text(_render_uncovered(result), encoding="utf-8")
        pytest.skip(f"{REGEN_ENV} set: baselines rewritten, not checked")
    return result


def test_every_divergence_is_allowlisted(report: _Report) -> None:
    """No builder may start disagreeing with its profile map unlisted."""
    allowed = {row[:3] for row in _read_rows(DIVERGENCES_FILE)}
    unlisted = sorted(set(report.divergences) - allowed)
    assert not unlisted, "\n".join(
        f"{p} {b} [{c}] {report.divergences[(p, b, c)]}" for p, b, c in unlisted
    )


def test_allowlist_has_no_stale_entries(report: _Report) -> None:
    """An entry that no longer diverges must be deleted, never kept."""
    rows = _read_rows(DIVERGENCES_FILE)
    stale = sorted(row[:3] for row in rows if row[:3] not in report.divergences)
    assert not stale, "\n".join(" ".join(row) for row in stale)


def test_allowlist_records_the_observed_frames(report: _Report) -> None:
    """Each row must show the two frames that were actually built."""
    wrong = [
        row
        for row in _read_rows(DIVERGENCES_FILE)
        if row[:3] in report.divergences and row[3:] != (report.divergences[row[:3]],)
    ]
    assert not wrong, "\n".join(
        f"{' '.join(r[:3])}: recorded {r[3:]}, observed {report.divergences[r[:3]]!r}"
        for r in wrong
    )


def test_uncovered_inventory_matches(report: _Report) -> None:
    """Census, CAT-only profiles, map gaps, noargs builders, uncompared sites."""
    rows = _read_rows(UNCOVERED_FILE)
    assert {r[1]: int(r[2]) for r in rows if r[0] == "census"} == report.census
    assert {r[1]: int(r[2]) for r in rows if r[0] == "cat-only"} == (
        report.cat_only_profiles
    )
    assert {r[1]: tuple(r[2].split(",")) for r in rows if r[0] == "gap"} == (
        report.map_gaps
    )
    assert tuple(r[1] for r in rows if r[0] == "noargs") == report.unsynthesisable
    assert tuple(r[1] for r in rows if r[0] == "unpinned") == report.uncompared_sites


def test_every_site_is_reached_by_some_builder() -> None:
    """No dual-implementation site may sit outside the callable surface.

    A site can still go uncompared because every profile's map omits its
    command, or because no probe value satisfied its builder; both are
    listed as ``unpinned`` rows. What must never happen is a site no public
    builder routes to at all, because then nothing could ever compare it.
    """
    reached: set[Key] = set()
    for key in _builders():
        reached |= _reachable_sites(key)
    assert sorted(_graph().sites - reached) == []
