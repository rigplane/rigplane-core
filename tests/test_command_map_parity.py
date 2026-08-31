"""Pin every dual-implementation command builder against its profile map.

Builders in ``src/rigplane/commands/`` that carry both a ``cmd_map`` branch
and a hardcoded fallback hold the same CI-V knowledge twice, and only a
hand-written subset compared the two: ``test_command_map_integration.py``
calls a fixed list of builders against the IC-7610 map, almost all at a
single argument set with their optional arguments left at the default --
its ``TestCmd29Parity`` also probes four of them at ``command29=False`` --
and
``test_commands.py: test_speech_cmd_map_prefers_set_speech_key`` and
``test_rig_ic7300.py: test_get_speech_cmd_map_uses_set_speech`` each pin
``get_speech`` alone. Outside that subset the two copies could disagree
silently, and at least one pair does: the ``cmd_map`` branch of
``config.py: get_acc1_mod_level`` sends an extended-menu address --
``1A 05 00 64`` on IC-7300 and IC-9700, ``1A 05 00 88`` on IC-7610 -- where
the fallback sends the legacy command ``14 0B`` on all three -- two
different CI-V commands for the same control, not a byte-count mismatch.
(``scope.py: get_scope_center_type``
used to be this module's worked example here: its ``cmd_map`` branch sent
a bare ``27 1c`` where the fallback appended a receiver byte that turned
0x1C's read into a SET. MOR-2002 step 2b-vfo-scope closed that by
refusing the argument outright.)

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

Nothing here fixes a divergence: for every command whose ``cmd_map`` branch
and fallback both still accept a receiver, which ones take that prefix byte
remains MOR-1981, and adding one where the radio does not expect it is a
write, not a no-op — see ``commands/scope.py: SCOPE_RECEIVER_SELECTOR_SUBS``.
``get_scope_center_type`` is the one exception this sweep no longer needs to
watch: MOR-2002 step 2b-vfo-scope removed its ``receiver`` parameter
outright, so that builder has no argument left to get wrong.

What could not be compared is not skipped silently:
``command_map_parity_uncovered.txt`` records the commands a profile's map
omits, the profiles whose TOML defines every command as CAT so that
``profiles/rig_loader.py: RigConfig.to_command_map`` drops all of them, the
builders whose arguments no probe value satisfied, the sites no compared
builder reaches, and a census of how much was compared. Every number in it
is re-measured and asserted here, so none of it can go stale unnoticed.

That census also counts what this sweep cannot reach at all:
``hardcode_only_builders`` is the byte-emitting public builders that take no
``cmd_map`` — response parsers (``parse_*``) are excluded along with the
underscore-named kernel modules, since neither emits a command frame for a
``cmd_map`` to affect. They have no profile branch to disagree with, so no
divergence row can ever name them however wrong their bytes are — the count
is the only thing that makes a new one visible.

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
# ``noargs`` (or, if its own ``cmd_map`` is required, ``requires-map``)
# row, never given a bespoke argument here.
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


@functools.lru_cache(maxsize=1)
def _hardcode_only_builders() -> frozenset[Key]:
    """Byte-emitting command builders that take no ``cmd_map``.

    These are outside everything else this file measures. A builder with
    both branches can disagree with its profile, and a divergence row says
    so; a builder with no ``cmd_map`` parameter has no profile branch to
    disagree with, so it can never appear in the divergence baseline however
    wrong its bytes are. Counting them here is what makes a new one visible:
    the census is asserted for equality, so adding one fails until the
    baseline is regenerated in a reviewable commit.

    Two kinds of function are excluded, for the same reason: underscore-named
    modules, and any ``parse_*`` function regardless of module. Neither
    emits a command frame -- the underscore modules hold the framing and
    codec kernel (``_frame.py: build_civ_frame``, ``_codec.py:
    bcd_encode_value`` and their siblings) that every builder calls, and a
    ``parse_*`` function decodes a radio response instead of building one
    (``freq.py: parse_frequency_response`` and its siblings) -- so a
    ``cmd_map`` would have nothing to look up for either. Counting either
    would make this number move for a reason unrelated to what it tracks:
    the kernel gaining a public helper, or a module gaining a response
    parser. :func:`_builders` needs no module-level rule because its own
    ``__name__.startswith("_")`` filter already excludes every underscore
    module: the four kernel helpers that do take a ``cmd_map``
    (``_builders.py: _build_function_get`` and its two siblings,
    ``_frame.py: _build_from_map``) are all underscore-named -- down from
    eleven at MOR-2006 module 1 (config.py): module 2 (levels.py) deleted
    ``_build_level_get``, ``_build_level_set`` and ``_build_ctl_mem_set``,
    each left with zero callers once levels.py de-delegated from them,
    MOR-2008 batch 1 (system.py) deleted ``_build_ctl_mem_get`` the same
    way, and MOR-2008 batch 2 (mode.py/meters.py) deleted
    ``_build_meter_bool_get``/``_build_ctl_mem_single_bcd_get``/
    ``_build_ctl_mem_single_bcd_set`` the same way.
    A public
    ``cmd_map``-taking helper added to one of those modules would move
    ``public_builders`` -- this rule is what keeps it out of this count.
    """
    found: set[Key] = set()
    for path in sorted(COMMANDS_DIR.glob("*.py")):
        if path.stem.startswith("_"):
            continue
        module = importlib.import_module(f"rigplane.commands.{path.stem}")
        for value in vars(module).values():
            if not inspect.isfunction(value) or value.__name__.startswith("_"):
                continue
            if value.__module__ != module.__name__:
                continue
            if value.__name__.startswith("parse"):
                continue
            if "cmd_map" not in inspect.signature(value).parameters:
                found.add((path.name, value.__name__))
    return frozenset(found)


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


def _requires_cmd_map(fn: typing.Any) -> bool:
    """True if *fn*'s ``cmd_map`` parameter has no default.

    A migrated builder (MOR-2006/MOR-2007 Steps 5..N) can never accept this
    file's ``cmd_map=None`` probe (`_accepts`), whatever value its other
    parameters are given -- a purely structural fact about the signature,
    independent of why any particular probe call failed. Used in `_cases`
    to tell that population apart from a genuinely unsynthesisable builder,
    whose ``cmd_map`` is still optional but no probe value in `_INTS`
    satisfies its OTHER required argument's own validation (e.g.
    `vfo.py: scan_set_df_span`/`scan_set_resume` before MOR-2007 migrated
    them, whose 0xA1-0xA7/0xD0-0xD3 domains have no member in `_INTS`) --
    the "noargs" bucket in `command_map_parity_uncovered.txt` is empty as of
    MOR-2007, since that was the last such case in the tree, but the
    distinction stays live for whatever migrates next.
    """
    param = inspect.signature(fn).parameters.get("cmd_map")
    return param is not None and param.default is inspect.Parameter.empty


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
def _cases() -> tuple[
    dict[Key, list[dict[str, typing.Any]]], frozenset[Key], frozenset[Key]
]:
    """Argument cases per builder, plus the builders none could be found for.

    The first case passes only required arguments; one further case per
    optional argument overrides it with a non-default value, because a
    divergence can hide behind a default — until MOR-2002 step
    2b-vfo-scope, ``scope.py: get_scope_center_type`` built identical
    frames unless its ``receiver`` argument was given a value, which is
    why that probe exists rather than stopping at required arguments.
    Removing the argument closed that one case; this search still probes
    every remaining optional argument the same way, on the same reasoning.
    Argument validation happens before a builder reads ``to_addr``, so
    this search runs once and its result is reused for every profile.

    A builder with no accepted combination splits into two populations,
    per ``_requires_cmd_map``: one whose ``cmd_map`` is required, so this
    file's ``cmd_map=None`` probe can never be accepted regardless of any
    other argument's value (MOR-2006 Steps 5..N — a fact about the
    signature, not a failed search); and the genuine ``unsynthesisable``
    case, where ``cmd_map`` is still optional and no value in the probe
    ladders for its *other* required arguments satisfies its validation.
    """
    per_builder: dict[Key, list[dict[str, typing.Any]]] = {}
    unsynthesisable: set[Key] = set()
    requires_map: set[Key] = set()
    for key, fn in _builders().items():
        required, optional = _split_params(fn)
        base = next(
            (kw for kw in _candidate_kwargs(fn, required) if _accepts(fn, kw)), None
        )
        if base is None:
            (requires_map if _requires_cmd_map(fn) else unsynthesisable).add(key)
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
    return per_builder, frozenset(unsynthesisable), frozenset(requires_map)


# ── the comparison ──


class _Report(typing.NamedTuple):
    divergences: dict[tuple[str, str, str], str]
    map_gaps: dict[str, tuple[str, ...]]
    cat_only_profiles: dict[str, int]
    unsynthesisable: tuple[str, ...]
    requires_map: tuple[str, ...]
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
    per_builder, unsynthesisable, requires_map = _cases()
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
        requires_map=tuple(sorted(f"{m}:{f}" for m, f in requires_map)),
        uncompared_sites=tuple(
            sorted(f"{m}:{f}" for m, f in _graph().sites - compared)
        ),
        census={
            "dual_implementation_sites": len(_graph().sites),
            "sites_compared": len(compared),
            "public_builders": len(_builders()),
            "hardcode_only_builders": len(_hardcode_only_builders()),
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
        "# much it did. Tab separated, six row kinds:",
        "#   census        <name>              <count>",
        "#   cat-only      <profile>           <commands, every one of them CAT>",
        "#   gap           <command name>      <profiles whose map omits it>",
        "#   noargs        <module>:<builder>  no probe value satisfied its OTHER",
        "#                                     required arguments (cmd_map still",
        "#                                     optional there -- a genuinely",
        "#                                     unsynthesisable case)",
        "#   requires-map  <module>:<builder>  cmd_map has no default (MOR-2006",
        "#                                     Steps 5..N migrated it) -- this",
        "#                                     file's cmd_map=None probe can never",
        "#                                     be accepted, whatever else is tried",
        "#   unpinned      <module>:<builder>  no compared builder reaches it",
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
    rows += [f"requires-map\t{name}" for name in report.requires_map]
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
    """Census, CAT-only profiles, map gaps, noargs/requires-map builders,
    uncompared sites."""
    rows = _read_rows(UNCOVERED_FILE)
    assert {r[1]: int(r[2]) for r in rows if r[0] == "census"} == report.census
    assert {r[1]: int(r[2]) for r in rows if r[0] == "cat-only"} == (
        report.cat_only_profiles
    )
    assert {r[1]: tuple(r[2].split(",")) for r in rows if r[0] == "gap"} == (
        report.map_gaps
    )
    assert tuple(r[1] for r in rows if r[0] == "noargs") == report.unsynthesisable
    assert tuple(r[1] for r in rows if r[0] == "requires-map") == report.requires_map
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
