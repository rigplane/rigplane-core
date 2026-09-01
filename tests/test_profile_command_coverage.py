"""State-3 guard for D1 (plan §4 Step 4,
``docs/plans/2026-08-29-profile-driven-command-bytes.md`` §8.1): a CI-V
profile that neither declares a command-map key nor records it as absent
leaves that name in the state D1 says "must not exist at release" --
reached at runtime it falls back to logging and refusing (pinned by
``tests/test_undeclared_command_policy.py``); this is the guard that keeps
that fallback from firing in production.

Method, shared with ``tests/test_command_map_parity.py`` (the parity
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

That baseline measures declaration completeness only: absent markers are not
manual proof. The MOR-2144 foundation inventories normalized specs independently
and reports implementation gaps without an allowlist.

Regenerate after an intentional change (a profile gains or loses a
declaration, or a builder's key changes)::

    RIGPLANE_REGEN_PROFILE_COMMAND_COVERAGE=1 uv run pytest \\
        tests/test_profile_command_coverage.py
"""

from __future__ import annotations

import ast
import enum
import functools
import inspect
import os
import pathlib
import textwrap
import typing
from collections import defaultdict
from dataclasses import dataclass

import pytest

from rigplane.backends.yaesu_cat.radio import YaesuCatRadio
from rigplane.backends.yaesu_cat.poller import YaesuCatPoller
from rigplane.commands._frame import decode_wire_tuple
from rigplane.commands.command_map import CommandMap, ReverseLookupResult
from rigplane.commands.command_spec import (
    AbsentCommandSpec,
    CatCommandSpec,
    CivCommandSpec,
)
from rigplane.core.radio_protocol import DualReceiverCapable
from rigplane.profiles.rig_loader import discover_rigs
from rigplane.runtime.radio import CoreRadio
from support.command_builders import (
    PythonParseHealth,
    parse_python_paths as _parse_python_paths,
    public_command_builders,
    repository_python_paths,
)

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
    """Return the shared builder inventory used by both coverage guards."""
    return public_command_builders(COMMANDS_DIR)


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


# ── implementation completeness foundation (MOR-2144) ──


class _MethodStatus(enum.Enum):
    IMPLEMENTED = "implemented"
    STUB = "stub"
    ABSENT = "absent"


class _DeclarationRelation(enum.Enum):
    IMPLEMENTED = "implemented"
    PARTIAL = "partial"
    STUB = "stub"
    ABSENT = "absent"
    COMPOSITE = "composite"
    NAME_MISMATCH = "name_mismatch"


class _ExecutionReachability(enum.Enum):
    REACHABLE = "reachable"
    UNREACHABLE = "unreachable"
    UNKNOWN = "unknown"
    NOT_APPLICABLE = "not_applicable"


@dataclass(frozen=True, slots=True)
class _ImplementationFinding:
    """One declaration result with implementation and reachability kept separate."""

    profile_id: str
    kind: str
    name: str
    relation: _DeclarationRelation
    reachability: _ExecutionReachability
    routes: tuple[str, ...] = ()
    method_statuses: tuple[tuple[str, _MethodStatus], ...] = ()
    declared_absent: bool = False
    diagnostics: tuple[str, ...] = ()

    @property
    def fully_complete(self) -> bool:
        return (
            self.relation is _DeclarationRelation.IMPLEMENTED
            and self.reachability
            in {
                _ExecutionReachability.REACHABLE,
                _ExecutionReachability.NOT_APPLICABLE,
            }
        )


def _finding_sort_key(finding: _ImplementationFinding) -> tuple[str, str, str]:
    return finding.profile_id, finding.kind, finding.name


@dataclass(frozen=True, slots=True)
class _ImplementationReport:
    """Deterministic census for later shipped-profile gate activation.

    The foundation reports every observed gap; no allowlist or baseline
    suppresses current shipped-profile failures.
    """

    parse_health: PythonParseHealth
    findings: tuple[_ImplementationFinding, ...]

    def find(self, profile_id: str, kind: str, name: str) -> _ImplementationFinding:
        matches = tuple(
            finding
            for finding in self.findings
            if (finding.profile_id, finding.kind, finding.name)
            == (profile_id, kind, name)
        )
        if len(matches) != 1:
            raise AssertionError(
                f"expected one finding for {profile_id} {kind} {name}, "
                f"found {len(matches)}"
            )
        return matches[0]

    @property
    def gaps(self) -> tuple[_ImplementationFinding, ...]:
        return tuple(
            sorted(
                (finding for finding in self.findings if not finding.fully_complete),
                key=_finding_sort_key,
            )
        )

    def render_gaps(self) -> str:
        return "\n".join(row for finding in self.gaps for row in finding.diagnostics)


# Narrow by design: only an existing public Protocol may define a contract.
_CAPABILITY_PROTOCOLS: dict[str, type[typing.Any]] = {
    "dual_rx": DualReceiverCapable,
}

_IMPLEMENTATION_BY_PROTOCOL: dict[str, type[typing.Any]] = {
    "civ": CoreRadio,
    "yaesu_cat": YaesuCatRadio,
}


def _classify_method_node(
    node: ast.FunctionDef | ast.AsyncFunctionDef,
) -> _MethodStatus:
    """Classify only a direct, unconditional ``NotImplementedError`` body."""
    body = list(node.body)
    if (
        body
        and isinstance(body[0], ast.Expr)
        and isinstance(body[0].value, ast.Constant)
        and isinstance(body[0].value.value, str)
    ):
        body.pop(0)
    if len(body) != 1 or not isinstance(body[0], ast.Raise):
        return _MethodStatus.IMPLEMENTED
    exc = body[0].exc
    if isinstance(exc, ast.Name) and exc.id == "NotImplementedError":
        return _MethodStatus.STUB
    if (
        isinstance(exc, ast.Call)
        and isinstance(exc.func, ast.Name)
        and exc.func.id == "NotImplementedError"
    ):
        return _MethodStatus.STUB
    return _MethodStatus.IMPLEMENTED


def _function_nodes(
    parse_health: PythonParseHealth,
) -> dict[pathlib.Path, tuple[ast.FunctionDef | ast.AsyncFunctionDef, ...]]:
    return {
        parsed.path.resolve(): tuple(
            node
            for node in ast.walk(parsed.tree)
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        )
        for parsed in parse_health.parsed
    }


def _method_status(
    implementation: type[typing.Any] | None,
    method_name: str,
    nodes_by_path: dict[
        pathlib.Path, tuple[ast.FunctionDef | ast.AsyncFunctionDef, ...]
    ],
) -> _MethodStatus:
    if implementation is None:
        return _MethodStatus.ABSENT
    marker = object()
    descriptor = inspect.getattr_static(implementation, method_name, marker)
    if descriptor is marker:
        return _MethodStatus.ABSENT
    if isinstance(descriptor, (classmethod, staticmethod)):
        descriptor = descriptor.__func__
    if not inspect.isfunction(descriptor):
        return _MethodStatus.IMPLEMENTED
    target = inspect.unwrap(descriptor)
    source = inspect.getsourcefile(target)
    code = getattr(target, "__code__", None)
    if source is None or code is None:
        return _MethodStatus.IMPLEMENTED
    candidates = [
        node
        for node in nodes_by_path.get(pathlib.Path(source).resolve(), ())
        if node.name == target.__name__
    ]
    if not candidates:
        return _MethodStatus.IMPLEMENTED
    node = min(
        candidates, key=lambda candidate: abs(candidate.lineno - code.co_firstlineno)
    )
    return _classify_method_node(node)


def _protocol_method_names(protocol: type[typing.Any]) -> tuple[str, ...]:
    return tuple(
        sorted(
            name
            for name, value in protocol.__dict__.items()
            if not name.startswith("_") and inspect.isfunction(value)
        )
    )


def _capability_status(
    method_statuses: tuple[tuple[str, _MethodStatus], ...],
) -> _DeclarationRelation:
    states = tuple(status for _name, status in method_statuses)
    if states and all(status is _MethodStatus.IMPLEMENTED for status in states):
        return _DeclarationRelation.IMPLEMENTED
    if states and all(status is _MethodStatus.ABSENT for status in states):
        return _DeclarationRelation.ABSENT
    if states and all(status is _MethodStatus.STUB for status in states):
        return _DeclarationRelation.STUB
    return _DeclarationRelation.PARTIAL


def _capability_finding(
    *,
    profile_id: str,
    tag: str,
    protocol: type[typing.Any],
    implementation: type[typing.Any] | None,
    nodes_by_path: dict[
        pathlib.Path, tuple[ast.FunctionDef | ast.AsyncFunctionDef, ...]
    ],
) -> _ImplementationFinding:
    method_statuses = tuple(
        (name, _method_status(implementation, name, nodes_by_path))
        for name in _protocol_method_names(protocol)
    )
    diagnostics = tuple(
        f"{profile_id} capability {tag}: "
        f"{'stub' if status is _MethodStatus.STUB else 'missing'} method {name}"
        for name, status in method_statuses
        if status is not _MethodStatus.IMPLEMENTED
    )
    return _ImplementationFinding(
        profile_id=profile_id,
        kind="capability",
        name=tag,
        relation=_capability_status(method_statuses),
        reachability=_ExecutionReachability.NOT_APPLICABLE,
        method_statuses=method_statuses,
        diagnostics=diagnostics,
    )


def _string_values(
    node: ast.AST,
    assignments: dict[str, set[str]],
) -> set[str]:
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return {node.value}
    if isinstance(node, ast.Name):
        return set(assignments.get(node.id, ()))
    if isinstance(node, ast.IfExp):
        return _string_values(node.body, assignments) | _string_values(
            node.orelse, assignments
        )
    return set()


def _cat_dispatch_routes(
    class_node: ast.ClassDef,
    method_names: frozenset[str] | None = None,
) -> dict[str, frozenset[str]]:
    """Trace selected radio methods transitively to canonical CAT wire calls."""
    routes: dict[str, set[str]] = defaultdict(set)
    methods = {
        node.name: node
        for node in class_node.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    }
    pending = list(methods if method_names is None else method_names)
    seen: set[str] = set()
    while pending:
        method_name = pending.pop()
        if method_name in seen or method_name not in methods:
            continue
        seen.add(method_name)
        method = methods[method_name]
        pending.extend(
            node.func.attr
            for node in ast.walk(method)
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and isinstance(node.func.value, ast.Name)
            and node.func.value.id == "self"
            and node.func.attr in methods
        )
        assignments: dict[str, set[str]] = defaultdict(set)
        for node in ast.walk(method):
            if isinstance(node, ast.Assign):
                values = _string_values(node.value, assignments)
                for target in node.targets:
                    if isinstance(target, ast.Name):
                        assignments[target.id].update(values)
            elif isinstance(node, ast.AnnAssign) and node.value is not None:
                if isinstance(node.target, ast.Name):
                    assignments[node.target.id].update(
                        _string_values(node.value, assignments)
                    )
        for node in ast.walk(method):
            if not (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and isinstance(node.func.value, ast.Name)
                and node.func.value.id == "self"
                and node.func.attr in {"_query", "_write"}
                and node.args
            ):
                continue
            route = "cat_query" if node.func.attr == "_query" else "cat_write"
            for name in _string_values(node.args[0], assignments):
                routes[name].add(route)
    return {name: frozenset(found) for name, found in routes.items()}


def _yaesu_executor_methods(class_node: ast.ClassDef) -> frozenset[str]:
    """Read radio-method roots from the real Yaesu queue executor."""
    for method in class_node.body:
        if (
            isinstance(method, ast.AsyncFunctionDef)
            and method.name == "_execute_command"
        ):
            return frozenset(
                node.func.attr
                for node in ast.walk(method)
                if isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and isinstance(node.func.value, ast.Name)
                and node.func.value.id == "radio"
            )
    raise AssertionError("YaesuCatPoller._execute_command AST missing")


def _class_node(
    parse_health: PythonParseHealth,
    implementation: type[typing.Any],
) -> ast.ClassDef:
    source = inspect.getsourcefile(implementation)
    if source is None:
        raise AssertionError(f"{implementation.__name__}: source file unavailable")
    source_path = pathlib.Path(source).resolve()
    for parsed in parse_health.parsed:
        if parsed.path.resolve() != source_path:
            continue
        for node in parsed.tree.body:
            if isinstance(node, ast.ClassDef) and node.name == implementation.__name__:
                return node
    raise AssertionError(
        f"{implementation.__name__}: class AST missing from {source_path}"
    )


def _literal_frame_value(node: ast.AST, field: str) -> int | None:
    if not isinstance(node, ast.Compare) or len(node.ops) != 1:
        return None
    if not isinstance(node.ops[0], ast.Eq) or len(node.comparators) != 1:
        return None
    pairs = ((node.left, node.comparators[0]), (node.comparators[0], node.left))
    for candidate, value in pairs:
        if (
            isinstance(candidate, ast.Attribute)
            and candidate.attr == field
            and isinstance(value, ast.Constant)
            and isinstance(value.value, int)
        ):
            return value.value
    return None


def _civ_inbound_pairs(parse_health: PythonParseHealth) -> frozenset[tuple[int, int]]:
    pairs: set[tuple[int, int]] = set()
    for parsed in parse_health.parsed:
        if parsed.path.name != "_civ_rx.py" or "src" not in parsed.path.parts:
            continue
        handlers = (
            node
            for node in ast.walk(parsed.tree)
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
            and any(word in node.name for word in ("handle", "observation", "route"))
        )
        for node in (child for handler in handlers for child in ast.walk(handler)):
            if not isinstance(node, ast.BoolOp) or not isinstance(node.op, ast.And):
                continue
            command: int | None = None
            sub: int | None = None
            for value in node.values:
                command = (
                    command
                    if command is not None
                    else _literal_frame_value(value, "command")
                )
                sub = sub if sub is not None else _literal_frame_value(value, "sub")
            if command is not None and sub is not None:
                pairs.add((command, sub))
    return frozenset(pairs)


def _reverse_result_names(result: ReverseLookupResult) -> frozenset[str]:
    if result.name is not None:
        return frozenset({result.name})
    return typing.cast(frozenset[str], result.candidates)


def _civ_routes(
    config: typing.Any,
    profile: typing.Any,
    inbound_pairs: frozenset[tuple[int, int]],
) -> dict[str, frozenset[str]]:
    command_map = profile.command_map or CommandMap({})
    reverse_index = profile.reverse_index
    if reverse_index is None:
        raise AssertionError(f"{profile.id}: loaded profile has no reverse index")
    static_cache: dict[Key, str] = {}
    builder_names = {
        _key_for(key, builder, command_map, static_cache)
        for key, builder in _builders().items()
    }
    routes: dict[str, set[str]] = defaultdict(set)
    for name in builder_names:
        if name in profile.command_names:
            command, sub, prefix = decode_wire_tuple(command_map.get(name))
            resolved = _reverse_result_names(
                reverse_index.resolve(command, sub, prefix)
            )
            if name in resolved:
                routes[name].add("civ_builder")
        elif name in config.commands:
            # Keep builder discovery independent from an untrusted absent marker.
            routes[name].add("civ_builder")
    for command, sub in inbound_pairs:
        result = reverse_index.resolve(command, sub, b"")
        for name in _reverse_result_names(result):
            routes[name].add("civ_inbound")
    return {name: frozenset(found) for name, found in routes.items()}


def _command_finding(
    *,
    profile_id: str,
    name: str,
    spec: typing.Any,
    routes: frozenset[str],
    executor_routes: frozenset[str],
    implementation: type[typing.Any] | None,
    nodes_by_path: dict[
        pathlib.Path, tuple[ast.FunctionDef | ast.AsyncFunctionDef, ...]
    ],
) -> _ImplementationFinding:
    expected_routes: frozenset[str]
    if isinstance(spec, CatCommandSpec):
        expected_routes = frozenset(
            route
            for present, route in (
                (spec.read is not None, "cat_query"),
                (spec.write is not None, "cat_write"),
            )
            if present
        )
    elif isinstance(spec, CivCommandSpec):
        expected_routes = frozenset({"civ_builder", "civ_inbound"})
    else:
        expected_routes = frozenset()

    method_status = _method_status(implementation, name, nodes_by_path)
    if isinstance(spec, CivCommandSpec):
        complete = bool(routes & expected_routes)
        partial = False
    elif isinstance(spec, CatCommandSpec):
        complete = expected_routes <= routes
        partial = bool(expected_routes & routes) and not complete
    else:
        complete = bool(routes)
        partial = False

    if complete:
        relation = _DeclarationRelation.IMPLEMENTED
    elif partial:
        relation = _DeclarationRelation.PARTIAL
    elif method_status is _MethodStatus.STUB:
        relation = _DeclarationRelation.STUB
    else:
        relation = _DeclarationRelation.ABSENT

    if relation in {
        _DeclarationRelation.ABSENT,
        _DeclarationRelation.PARTIAL,
        _DeclarationRelation.STUB,
    }:
        reachability = _ExecutionReachability.UNREACHABLE
    elif not isinstance(spec, CatCommandSpec):
        reachability = _ExecutionReachability.UNKNOWN
    elif spec.write is None:
        reachability = _ExecutionReachability.NOT_APPLICABLE
    elif "cat_write" in executor_routes:
        reachability = _ExecutionReachability.REACHABLE
    else:
        reachability = _ExecutionReachability.UNREACHABLE

    diagnostics: tuple[str, ...] = ()
    if relation is _DeclarationRelation.PARTIAL:
        missing = sorted(expected_routes - routes)
        diagnostics = tuple(
            f"{profile_id} command {name}: missing method route {route}"
            for route in missing
        )
    elif relation is _DeclarationRelation.STUB:
        diagnostics = (f"{profile_id} command {name}: stub method {name}",)
    elif relation is _DeclarationRelation.ABSENT:
        diagnostics = (f"{profile_id} command {name}: missing method {name}",)
    elif reachability is _ExecutionReachability.UNREACHABLE:
        diagnostics = (
            f"{profile_id} command {name}: backend execution route unreachable",
        )
    elif reachability is _ExecutionReachability.UNKNOWN:
        diagnostics = (
            f"{profile_id} command {name}: backend execution reachability unknown",
        )

    return _ImplementationFinding(
        profile_id=profile_id,
        kind="command",
        name=name,
        relation=relation,
        reachability=reachability,
        routes=tuple(sorted(routes)),
        declared_absent=isinstance(spec, AbsentCommandSpec),
        diagnostics=diagnostics,
    )


def implementation_completeness_report(
    configs: typing.Mapping[str, typing.Any],
    *,
    repo_root: pathlib.Path,
) -> _ImplementationReport:
    """Return deterministic gaps without suppressing shipped failures."""
    parse_health = _parse_python_paths(repository_python_paths(repo_root))
    parse_health.require_clean()
    nodes_by_path = _function_nodes(parse_health)
    inbound_pairs = _civ_inbound_pairs(parse_health)
    yaesu_class = _class_node(parse_health, YaesuCatRadio)
    yaesu_routes = _cat_dispatch_routes(yaesu_class)
    yaesu_executor_routes = _cat_dispatch_routes(
        yaesu_class,
        _yaesu_executor_methods(_class_node(parse_health, YaesuCatPoller)),
    )
    findings: list[_ImplementationFinding] = []

    for _model, config in sorted(configs.items()):
        profile = config.to_profile()
        implementation = _IMPLEMENTATION_BY_PROTOCOL.get(config.protocol_type)
        if config.protocol_type == "civ":
            protocol_routes = _civ_routes(config, profile, inbound_pairs)
        elif config.protocol_type == "yaesu_cat":
            protocol_routes = yaesu_routes
        else:
            protocol_routes = {}

        for tag, protocol in _CAPABILITY_PROTOCOLS.items():
            if tag in profile.capabilities:
                findings.append(
                    _capability_finding(
                        profile_id=profile.id,
                        tag=tag,
                        protocol=protocol,
                        implementation=implementation,
                        nodes_by_path=nodes_by_path,
                    )
                )
        for name, spec in sorted(config.commands.items()):
            findings.append(
                _command_finding(
                    profile_id=profile.id,
                    name=name,
                    spec=spec,
                    routes=protocol_routes.get(name, frozenset()),
                    executor_routes=yaesu_executor_routes.get(name, frozenset()),
                    implementation=implementation,
                    nodes_by_path=nodes_by_path,
                )
            )

    return _ImplementationReport(
        parse_health=parse_health,
        findings=tuple(sorted(findings, key=_finding_sort_key)),
    )


@dataclass(frozen=True, slots=True)
class _ReverseCensusRow:
    """One runtime name related back to normalized profile declarations."""

    profile_id: str
    runtime_name: str
    relation: _DeclarationRelation
    reachability: _ExecutionReachability
    profile_names: tuple[str, ...] = ()
    declared_absent: bool = False
    absent_source: str | None = None


def reverse_implementation_census(
    profile: typing.Any,
    runtime_names: typing.Iterable[str],
    *,
    composites: typing.Mapping[str, tuple[str, ...]] | None = None,
    name_mismatches: typing.Mapping[str, str] | None = None,
    executor_graph: typing.Mapping[str, _ExecutionReachability] | None = None,
) -> tuple[_ReverseCensusRow, ...]:
    """Relate runtime names to a profile without supplying the 52 rulings.

    Composite and mismatch evidence must point only to present declarations;
    explicit absent declarations remain distinct and retain their source.
    Scanner-produced ``executor_graph`` may be a superset; unused rows are ignored.
    """
    runtime_name_set = frozenset(runtime_names)
    composites = composites or {}
    name_mismatches = name_mismatches or {}
    executor_graph = executor_graph or {}
    for label, evidence in (
        ("composites", composites),
        ("name_mismatches", name_mismatches),
    ):
        extra = sorted(evidence.keys() - runtime_name_set)
        if extra:
            raise ValueError(f"{label} keys outside runtime names: {extra}")
    overlap = composites.keys() & name_mismatches.keys()
    if overlap:
        raise ValueError(f"runtime names have two relations: {sorted(overlap)}")
    declared = profile.command_names
    absent = profile.absent_command_names
    masked_absent = (composites.keys() | name_mismatches.keys()) & absent
    if masked_absent:
        raise ValueError(f"runtime names explicitly absent: {sorted(masked_absent)}")
    for runtime_name, targets in composites.items():
        if not targets:
            raise ValueError(f"{runtime_name}: composite evidence must be non-empty")
        unknown_targets = sorted(set(targets) - declared)
        if unknown_targets:
            raise ValueError(f"{runtime_name}: undeclared targets {unknown_targets}")
    for runtime_name, target in name_mismatches.items():
        if target not in declared:
            raise ValueError(f"{runtime_name}: undeclared target {target!r}")
    rows: list[_ReverseCensusRow] = []
    for runtime_name in sorted(runtime_name_set):
        if runtime_name in composites:
            relation = _DeclarationRelation.COMPOSITE
            profile_names = tuple(sorted(composites[runtime_name]))
        elif runtime_name in name_mismatches:
            relation = _DeclarationRelation.NAME_MISMATCH
            profile_names = (name_mismatches[runtime_name],)
        elif runtime_name in declared:
            relation = _DeclarationRelation.IMPLEMENTED
            profile_names = (runtime_name,)
        elif runtime_name in absent:
            relation = _DeclarationRelation.ABSENT
            profile_names = (runtime_name,)
        else:
            relation = _DeclarationRelation.ABSENT
            profile_names = ()
        rows.append(
            _ReverseCensusRow(
                profile_id=profile.id,
                runtime_name=runtime_name,
                relation=relation,
                reachability=executor_graph.get(
                    runtime_name, _ExecutionReachability.UNKNOWN
                ),
                profile_names=profile_names,
                declared_absent=runtime_name in absent,
                absent_source=profile.absent_command_sources.get(runtime_name),
            )
        )
    return tuple(rows)


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


# ── MOR-2144 implementation-completeness foundation ──


def test_parse_health_reports_syntax_errors(tmp_path: pathlib.Path) -> None:
    good = tmp_path / "good.py"
    bad = tmp_path / "bad.py"
    good.write_text("answer = 42\n", encoding="utf-8")
    bad.write_text("def broken(:\n", encoding="utf-8")

    health = _parse_python_paths((good, bad))

    assert health.parsed_count == 1
    assert health.syntax_error_count == 1
    with pytest.raises(
        AssertionError,
        match=r"parsed=1 syntax_errors=1.*bad\.py",
    ):
        health.require_clean()


def test_method_classifier_distinguishes_unconditional_stub_from_real_body() -> None:
    tree = ast.parse(
        textwrap.dedent(
            '''
            class Example:
                async def stub(self) -> None:
                    """A docstring does not make the raise conditional."""
                    raise NotImplementedError("not supported")

                async def implemented(self) -> None:
                    if self.ready:
                        raise NotImplementedError("conditional fallback")
                    await self.run()
            '''
        )
    )
    example = typing.cast(ast.ClassDef, tree.body[0])
    methods = {
        node.name: node
        for node in example.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    }

    assert _classify_method_node(methods["stub"]) is _MethodStatus.STUB
    assert _classify_method_node(methods["implemented"]) is _MethodStatus.IMPLEMENTED


@pytest.fixture(scope="module")
def implementation_report() -> _ImplementationReport:
    configs = discover_rigs(RIGS_DIR)
    return implementation_completeness_report(configs, repo_root=REPO_ROOT)


def test_foundation_parse_health_is_visible_and_clean(
    implementation_report: _ImplementationReport,
) -> None:
    assert implementation_report.parse_health.parsed_count >= 700
    assert implementation_report.parse_health.syntax_error_count == 0


def test_capability_contract_map_is_deliberately_narrow() -> None:
    assert _CAPABILITY_PROTOCOLS == {"dual_rx": DualReceiverCapable}


def test_ftx1_dual_rx_reports_stub_and_absent_methods(
    implementation_report: _ImplementationReport,
) -> None:
    finding = implementation_report.find("yaesu_ftx1", "capability", "dual_rx")

    assert finding.relation is _DeclarationRelation.PARTIAL
    assert finding.reachability is _ExecutionReachability.NOT_APPLICABLE
    assert not finding.fully_complete
    assert finding.method_statuses == (
        ("equalize_main_sub", _MethodStatus.ABSENT),
        ("get_main_sub_tracking", _MethodStatus.STUB),
        ("set_main_sub_tracking", _MethodStatus.STUB),
        ("swap_main_sub", _MethodStatus.ABSENT),
    )
    assert finding.diagnostics == (
        "yaesu_ftx1 capability dual_rx: missing method equalize_main_sub",
        "yaesu_ftx1 capability dual_rx: stub method get_main_sub_tracking",
        "yaesu_ftx1 capability dual_rx: stub method set_main_sub_tracking",
        "yaesu_ftx1 capability dual_rx: missing method swap_main_sub",
    )


def test_known_command_routes_use_protocol_specific_truth(
    implementation_report: _ImplementationReport,
) -> None:
    civ = implementation_report.find("icom_ic7300", "command", "get_freq")
    cat_get = implementation_report.find("yaesu_ftx1", "command", "get_freq")
    cat_set = implementation_report.find("yaesu_ftx1", "command", "set_freq")

    assert civ.relation is _DeclarationRelation.IMPLEMENTED
    assert civ.routes == ("civ_builder",)
    assert civ.reachability is _ExecutionReachability.UNKNOWN
    assert cat_get.relation is _DeclarationRelation.IMPLEMENTED
    assert cat_get.routes == ("cat_query",)
    assert cat_get.reachability is _ExecutionReachability.NOT_APPLICABLE
    assert cat_set.relation is _DeclarationRelation.IMPLEMENTED
    assert cat_set.routes == ("cat_write",)
    assert cat_set.reachability is _ExecutionReachability.REACHABLE


def test_ic7300_scope_wave_inbound_uses_profile_reverse_index(
    implementation_report: _ImplementationReport,
) -> None:
    finding = implementation_report.find("icom_ic7300", "command", "get_scope_wave")

    assert finding.relation is _DeclarationRelation.IMPLEMENTED
    assert finding.routes == ("civ_inbound",)
    assert finding.reachability is _ExecutionReachability.UNKNOWN


def test_declared_absent_marker_does_not_hide_existing_code_route(
    implementation_report: _ImplementationReport,
) -> None:
    finding = implementation_report.find("icom_ic7300", "command", "get_powerstat")

    assert finding.declared_absent
    assert finding.relation is _DeclarationRelation.IMPLEMENTED
    assert finding.routes == ("civ_builder",)
    assert finding.reachability is _ExecutionReachability.UNKNOWN


def test_real_yaesu_executor_exposes_missing_repeater_shift_route(
    implementation_report: _ImplementationReport,
) -> None:
    finding = implementation_report.find("yaesu_ftx1", "command", "set_repeater_shift")
    assert finding.relation is _DeclarationRelation.IMPLEMENTED
    assert finding.reachability is _ExecutionReachability.UNREACHABLE
    assert not finding.fully_complete
    assert finding.diagnostics == (
        "yaesu_ftx1 command set_repeater_shift: backend execution route unreachable",
    )


def test_real_yaesu_executor_follows_indirect_attenuator_helper(
    implementation_report: _ImplementationReport,
) -> None:
    finding = implementation_report.find("yaesu_ftx1", "command", "set_attenuator")

    assert finding.relation is _DeclarationRelation.IMPLEMENTED
    assert finding.routes == ("cat_write",)
    assert finding.reachability is _ExecutionReachability.REACHABLE
    assert finding.fully_complete


def test_reverse_census_hook_models_exact_composite_mismatch_and_gap() -> None:
    profile = discover_rigs(RIGS_DIR)["IC-7300"].to_profile()
    rows = reverse_implementation_census(
        profile,
        ("get_freq", "runtime_combo", "runtime_alias", "runtime_gap"),
        composites={"runtime_combo": ("get_freq", "get_mode")},
        name_mismatches={"runtime_alias": "set_freq"},
    )

    by_name = {row.runtime_name: row for row in rows}
    assert by_name["get_freq"].relation is _DeclarationRelation.IMPLEMENTED
    assert by_name["runtime_combo"].relation is _DeclarationRelation.COMPOSITE
    assert by_name["runtime_combo"].profile_names == ("get_freq", "get_mode")
    assert by_name["runtime_alias"].relation is _DeclarationRelation.NAME_MISMATCH
    assert by_name["runtime_alias"].profile_names == ("set_freq",)
    assert by_name["runtime_gap"].relation is _DeclarationRelation.ABSENT
    assert all(row.reachability is _ExecutionReachability.UNKNOWN for row in rows)


def test_reverse_census_rejects_unused_composite_evidence() -> None:
    profile = discover_rigs(RIGS_DIR)["IC-7300"].to_profile()
    with pytest.raises(ValueError, match=r"composites.*runtime_typo"):
        reverse_implementation_census(
            profile, ("runtime_gap",), composites={"runtime_typo": ("get_freq",)}
        )


def test_reverse_census_rejects_unused_mismatch_evidence() -> None:
    profile = discover_rigs(RIGS_DIR)["IC-7300"].to_profile()
    with pytest.raises(ValueError, match=r"name_mismatches.*runtime_typo"):
        reverse_implementation_census(
            profile, ("runtime_gap",), name_mismatches={"runtime_typo": "get_freq"}
        )


def test_reverse_census_preserves_absent_and_rejects_false_evidence() -> None:
    profile = discover_rigs(RIGS_DIR)["IC-7300"].to_profile()
    (absent,) = reverse_implementation_census(profile, ("get_powerstat",))

    assert absent.relation is _DeclarationRelation.ABSENT
    assert absent.profile_names == ("get_powerstat",)
    assert absent.declared_absent
    assert absent.absent_source

    invalid_evidence = (
        ({"composites": {"runtime_combo": ()}}, "non-empty"),
        ({"composites": {"runtime_combo": ("not_declared",)}}, "undeclared"),
        ({"name_mismatches": {"runtime_alias": "get_powerstat"}}, "undeclared"),
        ({"composites": {"get_powerstat": ("get_freq",)}}, "explicitly absent"),
    )
    runtime_names = ("runtime_gap", "runtime_combo", "runtime_alias", "get_powerstat")
    for kwargs, message in invalid_evidence:
        with pytest.raises(ValueError, match=message):
            reverse_implementation_census(profile, runtime_names, **kwargs)


def test_future_activation_api_returns_full_deterministic_gap_list(
    implementation_report: _ImplementationReport,
) -> None:
    gaps = implementation_report.gaps

    assert gaps
    assert gaps == tuple(sorted(gaps, key=_finding_sort_key))
    assert all(not gap.fully_complete for gap in gaps)
    assert (
        "yaesu_ftx1 capability dual_rx: missing method swap_main_sub"
        in implementation_report.render_gaps()
    )
