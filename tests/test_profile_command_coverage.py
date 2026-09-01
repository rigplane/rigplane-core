"""State-3 guard for D1 (plan §4 Step 4,
``docs/plans/2026-08-29-profile-driven-command-bytes.md`` §8.1): a CI-V
profile that neither declares a command-map key nor records it as absent
leaves that name in the state D1 says "must not exist at release" --
reached at runtime it falls back to logging and refusing (pinned by
``tests/test_undeclared_command_policy.py``); this is the guard that keeps
that fallback from firing in production.

Method, mirrored from ``tests/test_command_map_parity.py`` (the parity
harness) per the plan's own instruction to derive keys "the way the parity
harness does": every public, ``cmd_map``-taking builder resolves its
command-map key by exactly one of three routes -- statically, from a
direct ``_build_from_map(cmd_map, "literal", ...)`` call or a
``cmd_name="literal"`` keyword forwarded to a ``commands/_builders.py``
shared template (most builders, e.g. ``antenna.py: get_antenna_1``
resolves ``"get_antenna"``); via one hop of delegation for the one
compatibility wrapper with no literal of its own (``vfo.py:
set_dual_watch`` -- MOR-2086 deleted the other one, ``dsp.py:
set_attenuator``, a boolean wrapper that could not resolve a correct
value without the profile); or via the exposed ``cmd_map_key`` callable
for the one builder whose key is a function of the map's own contents
(``speech.py: get_speech``).
``test_every_builder_resolves_by_exactly_one_route`` below ties the split
to an assertion rather than a count that would rot silently here.

A resolved key is a gap for a profile when it is neither in
``RadioProfile.command_names`` nor in ``RadioProfile.absent_command_names``.
Known gaps live in ``tests/profile_command_coverage_gaps.txt``, one row per
command-map key -- deduplicated by key, per the plan's own phrasing
("enumerate every public builder KEY") -- with the profiles missing that
key comma-separated on the row, the same compact shape
``tests/command_map_parity_uncovered.txt``'s own ``gap`` rows use. The
baseline started large by construction (D2, plan §8.1: at that point no rig
TOML used the ``{ absent = "<source>" }`` spelling at all). Which profiles
have since been filled with it used to be tracked by
``tests/test_rig_loader.py::TestNoShippedProfileUsesAbsentSpellingYet``,
narrowed as each profile's own D2 pass landed; MOR-2008 batch 4's
``ftx1.toml``/``tx500.toml`` pass was the last shipped profile to be
filled, narrowing that pin's parametrize set to empty, so it was deleted
per its own docstring's contingency rather than kept as a vacuous test.
Each profile's per-model ``TestXDeclaresAbsentCommands`` class in that same
file is the durable record now, not restated here; this file's baseline is
meant to shrink only, never grow silently.

Regenerate after an intentional change (a profile gains or loses a
declaration, or a builder's key changes)::

    RIGPLANE_REGEN_PROFILE_COMMAND_COVERAGE=1 uv run pytest \\
        tests/test_profile_command_coverage.py
"""

from __future__ import annotations

import ast
import functools
import importlib
import inspect
import os
import pathlib
import typing
from collections import defaultdict

import pytest

from rigplane.commands.command_map import CommandMap
from rigplane.profiles.rig_loader import discover_rigs

REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]
COMMANDS_DIR = REPO_ROOT / "src" / "rigplane" / "commands"
RIGS_DIR = REPO_ROOT / "rigs"
GAPS_FILE = pathlib.Path(__file__).with_name("profile_command_coverage_gaps.txt")
REGEN_ENV = "RIGPLANE_REGEN_PROFILE_COMMAND_COVERAGE"

Key = tuple[str, str]  # (module file name, function name)


# ── the callable surface, and its command-map key ──


@functools.lru_cache(maxsize=1)
def _ast_index() -> dict[Key, ast.FunctionDef]:
    index: dict[Key, ast.FunctionDef] = {}
    for path in sorted(COMMANDS_DIR.glob("*.py")):
        if path.stem.startswith("_") or path.stem == "__init__":
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in tree.body:
            if isinstance(node, ast.FunctionDef) and not node.name.startswith("_"):
                index[(path.name, node.name)] = node
    return index


@functools.lru_cache(maxsize=1)
def _builders() -> dict[Key, typing.Any]:
    """Every public builder in the package that takes a ``cmd_map``.

    Same filtering as ``tests/test_command_map_parity.py: _builders`` --
    reimplemented rather than imported, since importing that module would
    also run its own module-level baseline collection as a side effect of
    collecting this file.
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


def _static_literal_key(node: ast.FunctionDef) -> str | None:
    """The literal command-map key *node*'s body passes, if it is a constant.

    A direct ``_build_from_map(cmd_map, "name", ...)`` call, or a
    ``cmd_name="name"`` keyword forwarded to a ``commands/_builders.py``
    shared template. Not a literal for ``speech.py: get_speech``, whose key
    is a ``_speech_key(cmd_map)`` call -- not an ``ast.Constant``.
    """
    for child in ast.walk(node):
        if (
            isinstance(child, ast.keyword)
            and child.arg == "cmd_name"
            and isinstance(child.value, ast.Constant)
        ):
            return typing.cast(str, child.value.value)
        if (
            isinstance(child, ast.Call)
            and isinstance(child.func, ast.Name)
            and child.func.id == "_build_from_map"
            and len(child.args) >= 2
            and isinstance(child.args[1], ast.Constant)
        ):
            return typing.cast(str, child.args[1].value)
    return None


def _delegate_target_names(node: ast.FunctionDef) -> frozenset[str]:
    """Builder names *node* calls while forwarding ``cmd_map``.

    Resolves ``vfo.py: set_dual_watch`` (the other delegate this served,
    ``dsp.py: set_attenuator``, was deleted in MOR-2086): it dispatches to
    another public builder by its ``on: bool`` argument, so a single-hop,
    name-based lookup finds one of its branches without needing to know
    which one a given call would actually take.
    """
    return frozenset(
        child.func.id
        for child in ast.walk(node)
        if isinstance(child, ast.Call)
        and isinstance(child.func, ast.Name)
        and any(kw.arg == "cmd_map" for kw in child.keywords)
    )


def _resolve_static_key(key: Key, cache: dict[Key, str]) -> str:
    if key in cache:
        return cache[key]
    index = _ast_index()
    node = index[key]
    literal = _static_literal_key(node)
    if literal is not None:
        cache[key] = literal
        return literal
    for target_name in sorted(_delegate_target_names(node)):
        candidates = [k for k in index if k[1] == target_name and k != key]
        local = [k for k in candidates if k[0] == key[0]]
        chosen = (
            local[0] if local else (candidates[0] if len(candidates) == 1 else None)
        )
        if chosen is not None:
            resolved = _resolve_static_key(chosen, cache)
            cache[key] = resolved
            return resolved
    raise AssertionError(
        f"{key[0]}:{key[1]} resolves no static command-map key and has no "
        "exposed cmd_map_key -- give it one (@expose_command_key) or make "
        "its literal key resolvable before this guard can cover it"
    )


def _key_for(
    key: Key,
    builder: typing.Any,
    command_map: CommandMap,
    cache: dict[Key, str],
) -> str:
    key_fn = getattr(builder, "cmd_map_key", None)
    if key_fn is not None:
        return typing.cast(str, key_fn(command_map))
    return _resolve_static_key(key, cache)


def test_every_builder_resolves_by_exactly_one_route() -> None:
    """Ties the module docstring's classification claim to an assertion
    instead of a literal count that rots silently: static (a literal in the
    body) + delegated (one hop to another builder's key) +
    ``cmd_map_key``-exposed (checked last -- ``ptt.py: ptt_on``/``ptt_off``
    expose it too but also carry a static literal, so only
    ``speech.py: get_speech`` actually falls through to this branch) must
    sum to the full builder surface, and only the one named delegate and
    the one named ``cmd_map_key`` builder may be non-static.
    """
    builders = _builders()
    static_cache: dict[Key, str] = {}
    static: set[Key] = set()
    delegated: set[Key] = set()
    cmd_map_key: set[Key] = set()
    for key, builder in builders.items():
        if _static_literal_key(_ast_index()[key]) is not None:
            static.add(key)
        elif getattr(builder, "cmd_map_key", None) is not None:
            cmd_map_key.add(key)
        else:
            _resolve_static_key(key, static_cache)  # raises if unresolvable
            delegated.add(key)

    assert len(static) + len(delegated) + len(cmd_map_key) == len(builders)
    assert {f"{f}:{n}" for f, n in delegated} == {"vfo.py:set_dual_watch"}
    assert {f"{f}:{n}" for f, n in cmd_map_key} == {"speech.py:get_speech"}


# ── the comparison ──


class _Report(typing.NamedTuple):
    gaps: dict[str, tuple[str, ...]]  # command-map key -> profiles missing it
    census: dict[str, int]


@functools.lru_cache(maxsize=1)
def _civ_rigs() -> dict[str, typing.Any]:
    return {
        model: config
        for model, config in discover_rigs(RIGS_DIR).items()
        if config.protocol_type == "civ"
    }


@functools.lru_cache(maxsize=1)
def _report() -> _Report:
    builders = _builders()
    static_cache: dict[Key, str] = {}
    gaps: dict[str, set[str]] = defaultdict(set)
    total_pairs = 0
    for model, config in sorted(_civ_rigs().items()):
        profile = config.to_profile()
        command_map = profile.command_map or CommandMap({})
        keys = {
            _key_for(key, builder, command_map, static_cache)
            for key, builder in builders.items()
        }
        for name in keys:
            if (
                name not in profile.command_names
                and name not in profile.absent_command_names
            ):
                gaps[name].add(model)
                total_pairs += 1
    return _Report(
        gaps={name: tuple(sorted(models)) for name, models in gaps.items()},
        census={
            "civ_profiles": len(_civ_rigs()),
            "public_builders": len(builders),
            "distinct_gap_keys": len(gaps),
            "profile_key_gaps": total_pairs,
        },
    )


# ── baseline file ──


def _read_rows(path: pathlib.Path) -> list[tuple[str, ...]]:
    return [
        tuple(line.split("\t"))
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    ]


def _render(report: _Report) -> str:
    header = [
        "# State-3 guard baseline (plan §4 Step 4 / §8.1 D1), tab separated:",
        "#   census  <name>              <count>",
        "#   gap     <command-map key>   <profiles missing it, comma sep.>",
        "# One 'gap' row per key: neither declared nor declared absent for",
        "# every profile named -- D1's state 3. Fails on a pair missing here",
        "# or a row no longer a gap -- delete it, never keep it. Fill a gap",
        '# via { absent = "<source>" } (plan §8.1 D2) or a real command-byte',
        "# entry in the profile's own TOML. Regenerate, do not hand-edit.",
    ]
    rows = [f"census\t{name}\t{n}" for name, n in sorted(report.census.items())]
    rows += [
        f"gap\t{name}\t{','.join(models)}"
        for name, models in sorted(report.gaps.items())
    ]
    return "\n".join(header + rows) + "\n"


@pytest.fixture(scope="module")
def report() -> _Report:
    result = _report()
    # Not a truthiness test: ``REGEN_ENV=0`` must mean off, not on.
    if os.environ.get(REGEN_ENV, "") not in {"", "0"}:
        GAPS_FILE.write_text(_render(result), encoding="utf-8")
        pytest.skip(f"{REGEN_ENV} set: baseline rewritten, not checked")
    return result


def _allowlisted_pairs() -> set[tuple[str, str]]:
    return {
        (model, row[1])
        for row in _read_rows(GAPS_FILE)
        if row[0] == "gap"
        for model in row[2].split(",")
    }


def _observed_pairs(report: _Report) -> set[tuple[str, str]]:
    return {(model, name) for name, models in report.gaps.items() for model in models}


def test_every_gap_is_allowlisted(report: _Report) -> None:
    """No (profile, key) pair may become a state-3 gap unlisted."""
    unlisted = sorted(_observed_pairs(report) - _allowlisted_pairs())
    assert not unlisted, "\n".join(f"{p}\t{k}" for p, k in unlisted)


def test_allowlist_has_no_stale_entries(report: _Report) -> None:
    """A pair that is no longer a gap must be deleted, never kept."""
    stale = sorted(_allowlisted_pairs() - _observed_pairs(report))
    assert not stale, "\n".join(f"{p}\t{k}" for p, k in stale)


def test_census_matches(report: _Report) -> None:
    """Growth or shrinkage in the gap count must show up as a reviewable diff."""
    rows = _read_rows(GAPS_FILE)
    census = {r[1]: int(r[2]) for r in rows if r[0] == "census"}
    assert census == report.census
