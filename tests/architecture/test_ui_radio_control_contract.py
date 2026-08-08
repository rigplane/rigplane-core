"""Executable ownership contract for UI-visible radio facts (MOR-1406).

Frontend findings come from the TypeScript Compiler API over real TS modules
and Svelte script ASTs. Python worker findings come from the Python AST and a
small inter-procedural call graph. Neither analyzer starts runtime machinery.
"""

from __future__ import annotations

import ast
import json
import os
import subprocess
import tomllib
from collections import Counter
from pathlib import Path
from typing import Any, cast

from rigplane.core.state_pipeline_contracts import DEFAULT_FIELD_REGISTRY, FieldPath
from rigplane.web.state_schema import (
    ConnectionPublic,
    FixedEdgePublic,
    RadioDetailPublic,
    RadioHealthPublic,
    ReceiverStatePublic,
    ScopeControlsPublic,
    ServerStatePublic,
    VfoSlotPublic,
)

ROOT = Path(__file__).resolve().parents[2]
CONTRACT_PATH = ROOT / "docs/internals/ui-radio-control-contract.toml"
ANALYZER = ROOT / "frontend/scripts/run-ui-radio-semantic-analyzer.mjs"
PROVIDER_IDS = {"icom_civ", "yaesu_cat", "xiegu_civ", "kenwood_cat"}
PYTHON_GUARD = "python_control_poll_loop"


def _contract() -> dict[str, Any]:
    with CONTRACT_PATH.open("rb") as stream:
        return tomllib.load(stream)


def _is_test_path(path: str) -> bool:
    candidate = Path(path)
    return (
        "__tests__" in candidate.parts
        or ".test." in candidate.name
        or ".spec." in candidate.name
    )


def _control_handler_commands() -> set[str]:
    source = (ROOT / "src/rigplane/web/handlers/control.py").read_text()
    module = ast.parse(source)
    for node in module.body:
        if not isinstance(node, ast.ClassDef) or node.name != "ControlHandler":
            continue
        for statement in node.body:
            if not isinstance(statement, ast.Assign):
                continue
            if not any(
                isinstance(target, ast.Name) and target.id == "_COMMANDS"
                for target in statement.targets
            ):
                continue
            assert isinstance(statement.value, ast.Call)
            return set(ast.literal_eval(statement.value.args[0]))
    raise AssertionError("ControlHandler._COMMANDS inventory not found")


def _model_fields(model: type[Any], prefix: str) -> set[str]:
    return {f"{prefix}.{field}" for field in model.model_fields}


def _derived_profile_provider(profile: dict[str, Any]) -> str:
    acquisition = profile.get("state_acquisition", {})
    if "provider" in acquisition:
        return str(acquisition["provider"])
    protocol = str(profile["protocol"]["type"])
    if protocol in {"yaesu_cat", "kenwood_cat"}:
        return protocol
    radio_id = str(profile["radio"]["id"])
    return "xiegu_civ" if radio_id.startswith("xiegu_") else "icom_civ"


def _truth_names(contract: dict[str, Any]) -> set[str]:
    names: set[str] = set()
    command_prefixes = {
        "get",
        "set",
        "select",
        "switch",
        "toggle",
        "read",
        "write",
        "start",
        "stop",
    }
    for family in contract["families"]:
        names.update(str(value) for value in family["capabilities"])
        for path in family["field_paths"]:
            names.add(str(path).split(".")[-1])
        for field in family["public_fields"]:
            names.add(str(field).split(".")[-1])
        for intent in family["intents"]:
            pieces = str(intent).split("_")
            while pieces and pieces[0] in command_prefixes:
                pieces.pop(0)
            if pieces:
                names.add("_".join(pieces))
    return names


def _scope_names(contract: dict[str, Any]) -> set[str]:
    names: set[str] = set()
    for family in contract["families"]:
        if family["id"] not in {"scope_controls_metadata", "spectrum_payload"}:
            continue
        names.update(str(value) for value in family["capabilities"])
        names.update(str(value).split(".")[-1] for value in family["field_paths"])
        names.update(str(value).split(".")[-1] for value in family["public_fields"])
    return names


def _frontend_analysis(
    scenarios: dict[str, dict[str, str]],
) -> dict[str, Any]:
    contract = _contract()
    request = {
        "root": str(ROOT),
        "truthNames": sorted(_truth_names(contract)),
        "scopeNames": sorted(_scope_names(contract)),
        "scenarios": scenarios,
    }
    completed = subprocess.run(
        ["node", str(ANALYZER)],
        input=json.dumps(request),
        text=True,
        capture_output=True,
        check=False,
        cwd=ROOT,
    )
    assert completed.returncode == 0, completed.stderr
    return cast(dict[str, Any], json.loads(completed.stdout))


def _frontend_counts(
    scenarios: dict[str, dict[str, str]],
) -> dict[str, dict[str, dict[str, int]]]:
    result = _frontend_analysis(scenarios)
    errors = {key: value for key, value in result["errors"].items() if value}
    assert not errors, f"semantic analyzer failed closed: {errors}"
    return cast(dict[str, dict[str, dict[str, int]]], result["scenarios"])


def _python_sources(overrides: dict[str, str] | None = None) -> dict[str, str]:
    sources = {
        path.relative_to(ROOT).as_posix(): path.read_text()
        for path in (ROOT / "src/rigplane").rglob("*.py")
        if not _is_test_path(path.relative_to(ROOT).as_posix())
    }
    for path, source in (overrides or {}).items():
        if path.startswith("src/rigplane/") and path.endswith(".py"):
            sources[path] = source
    return sources


def _normalized(value: str) -> str:
    return "".join(character.lower() for character in value if character.isalnum())


def _python_poll_counts(sources: dict[str, str]) -> Counter[str]:
    truth = {_normalized(name) for name in _truth_names(_contract())}
    functions: dict[str, tuple[str, ast.FunctionDef | ast.AsyncFunctionDef]] = {}
    module_names: dict[str, dict[str, str]] = {}
    sleep_names: dict[str, set[str]] = {}

    class Collector(ast.NodeVisitor):
        def __init__(self, source_path: str) -> None:
            self.source_path = source_path
            self.stack: list[str] = []

        def visit_ClassDef(self, node: ast.ClassDef) -> None:
            self.stack.append(node.name)
            self.generic_visit(node)
            self.stack.pop()

        def _function(self, node: ast.FunctionDef | ast.AsyncFunctionDef) -> None:
            key = f"{self.source_path}:{'.'.join([*self.stack, node.name])}"
            functions[key] = (self.source_path, node)
            self.stack.append(node.name)
            self.generic_visit(node)
            self.stack.pop()

        def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
            self._function(node)

        def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
            self._function(node)

    for source_path, source in sources.items():
        tree = ast.parse(source)
        aliases: dict[str, str] = {}
        direct_sleep: set[str] = set()
        for node in tree.body:
            if isinstance(node, ast.Import):
                for alias in node.names:
                    aliases[alias.asname or alias.name] = alias.name
            elif isinstance(node, ast.ImportFrom) and node.module:
                for alias in node.names:
                    local = alias.asname or alias.name
                    aliases[local] = f"{node.module}.{alias.name}"
                    if alias.name == "sleep" and node.module in {
                        "asyncio",
                        "time",
                        "anyio",
                    }:
                        direct_sleep.add(local)
            elif (
                isinstance(node, (ast.Assign, ast.AnnAssign)) and node.value is not None
            ):
                targets = (
                    node.targets if isinstance(node, ast.Assign) else [node.target]
                )
                if isinstance(node.value, ast.Name) and node.value.id == "getattr":
                    for target in targets:
                        if isinstance(target, ast.Name):
                            aliases[target.id] = "builtins.getattr"
        module_names[source_path] = aliases
        sleep_names[source_path] = direct_sleep
        Collector(source_path).visit(tree)

    summaries: dict[str, dict[str, Any]] = {}
    by_path_name = {
        (path, key.rsplit(".", 1)[-1].rsplit(":", 1)[-1]): key
        for key, (path, _) in functions.items()
    }
    for key, (source_path, node) in functions.items():
        aliases = module_names[source_path]
        sleeps = sleep_names[source_path]
        string_values: dict[str, ast.expr] = {}
        parents: dict[ast.AST, ast.AST] = {}
        invoked_names = {
            child.func.id
            for child in ast.walk(node)
            if isinstance(child, ast.Call) and isinstance(child.func, ast.Name)
        }
        getattr_aliases = {
            name
            for name, qualified in aliases.items()
            if qualified == "builtins.getattr"
        } | {"getattr"}
        for child in ast.walk(node):
            for nested in ast.iter_child_nodes(child):
                parents[nested] = child
            if isinstance(child, (ast.Assign, ast.AnnAssign)):
                value = child.value
                targets = (
                    child.targets if isinstance(child, ast.Assign) else [child.target]
                )
                if value is not None:
                    for target in targets:
                        if isinstance(target, ast.Name):
                            string_values[target.id] = value
        alias_changed = True
        while alias_changed:
            alias_changed = False
            for child in ast.walk(node):
                if (
                    not isinstance(child, (ast.Assign, ast.AnnAssign))
                    or child.value is None
                ):
                    continue
                value_is_getattr = (
                    isinstance(child.value, ast.Name)
                    and child.value.id in getattr_aliases
                ) or (
                    isinstance(child.value, ast.Attribute)
                    and child.value.attr == "getattr"
                    and isinstance(child.value.value, ast.Name)
                    and aliases.get(child.value.value.id, child.value.value.id)
                    == "builtins"
                )
                if not value_is_getattr:
                    continue
                targets = (
                    child.targets if isinstance(child, ast.Assign) else [child.target]
                )
                for target in targets:
                    if (
                        isinstance(target, ast.Name)
                        and target.id not in getattr_aliases
                    ):
                        getattr_aliases.add(target.id)
                        alias_changed = True

        def literal_strings(
            expression: ast.expr,
            seen: set[str] | None = None,
        ) -> set[str]:
            if isinstance(expression, ast.Constant) and isinstance(
                expression.value, str
            ):
                return {expression.value}
            if isinstance(expression, ast.IfExp):
                return literal_strings(expression.body, seen) | literal_strings(
                    expression.orelse, seen
                )
            if isinstance(expression, ast.Name):
                visited = set() if seen is None else set(seen)
                if expression.id in visited or expression.id not in string_values:
                    return set()
                visited.add(expression.id)
                return literal_strings(string_values[expression.id], visited)
            return set()

        def static_getattr_target(expression: ast.expr) -> bool:
            if isinstance(expression, ast.Name):
                return True
            if isinstance(expression, ast.Attribute):
                return static_getattr_target(expression.value)
            return False

        def is_getattr(expression: ast.expr) -> bool:
            if isinstance(expression, ast.Name):
                return expression.id in getattr_aliases
            if isinstance(expression, ast.Attribute):
                if expression.attr != "getattr" or not isinstance(
                    expression.value, ast.Name
                ):
                    return False
                return (
                    aliases.get(expression.value.id, expression.value.id) == "builtins"
                )
            return False

        def qualified_call_name(expression: ast.expr) -> str:
            if isinstance(expression, ast.Name):
                return aliases.get(expression.id, expression.id)
            if isinstance(expression, ast.Attribute) and isinstance(
                expression.value, ast.Name
            ):
                owner = aliases.get(expression.value.id, expression.value.id)
                return f"{owner}.{expression.attr}"
            return ""

        def is_unbounded_iterator(expression: ast.expr) -> bool:
            if not isinstance(expression, ast.Call):
                return False
            qualified = qualified_call_name(expression.func)
            if qualified in {"itertools.count", "itertools.cycle"}:
                return True
            return (
                qualified == "itertools.repeat"
                and len(expression.args) < 2
                and not any(keyword.arg == "times" for keyword in expression.keywords)
            )

        def getattr_is_invoked(call: ast.Call) -> bool:
            parent = parents.get(call)
            if isinstance(parent, ast.Call) and parent.func is call:
                return True
            if isinstance(parent, (ast.Assign, ast.AnnAssign)):
                targets = (
                    parent.targets
                    if isinstance(parent, ast.Assign)
                    else [parent.target]
                )
                return any(
                    isinstance(target, ast.Name) and target.id in invoked_names
                    for target in targets
                )
            return False

        calls: set[str] = set()
        has_sleep = False
        has_radio = False
        has_dynamic_getattr = False
        has_loop = any(
            isinstance(child, (ast.For, ast.AsyncFor, ast.While))
            for child in ast.walk(node)
        )
        has_periodic_loop = any(
            isinstance(child, ast.While)
            or (
                isinstance(child, (ast.For, ast.AsyncFor))
                and is_unbounded_iterator(child.iter)
            )
            for child in ast.walk(node)
        )
        for child in ast.walk(node):
            if not isinstance(child, ast.Call):
                continue
            if is_getattr(child.func):
                if not getattr_is_invoked(child):
                    continue
                if len(child.args) < 2:
                    has_dynamic_getattr = True
                    continue
                names = literal_strings(child.args[1])
                if not names:
                    has_dynamic_getattr = True
                    continue
                if not static_getattr_target(child.args[0]):
                    has_dynamic_getattr = True
                for name in names:
                    normalized = _normalized(name)
                    for prefix in ("get", "read", "query", "fetch", "poll"):
                        if normalized.startswith(prefix):
                            normalized = normalized.removeprefix(prefix)
                            break
                    if normalized in truth:
                        has_radio = True
                continue
            if isinstance(child.func, ast.Name):
                name = child.func.id
                qualified = aliases.get(name, "")
                if name in sleeps or qualified in {
                    "asyncio.sleep",
                    "time.sleep",
                    "anyio.sleep",
                }:
                    has_sleep = True
                call_target = by_path_name.get((source_path, name))
                if call_target:
                    calls.add(call_target)
            elif isinstance(child.func, ast.Attribute):
                name = child.func.attr
                if isinstance(child.func.value, ast.Name):
                    qualified = aliases.get(child.func.value.id, child.func.value.id)
                    if qualified in {"asyncio", "time", "anyio"} and name == "sleep":
                        has_sleep = True
                normalized = _normalized(name)
                for prefix in ("get", "read", "query", "fetch", "poll"):
                    if normalized.startswith(prefix):
                        normalized = normalized.removeprefix(prefix)
                        break
                if normalized in truth:
                    has_radio = True
                call_target = by_path_name.get((source_path, name))
                if call_target:
                    calls.add(call_target)
        summaries[key] = {
            "path": source_path,
            "loop": has_loop,
            "periodic": has_periodic_loop,
            "sleep": has_sleep,
            "radio": has_radio,
            "dynamic_getattr": has_dynamic_getattr,
            "calls": calls,
        }

    changed = True
    while changed:
        changed = False
        for summary in summaries.values():
            for called in summary["calls"]:
                target_summary = summaries[called]
                for flag in ("sleep", "radio", "dynamic_getattr"):
                    if target_summary[flag] and not summary[flag]:
                        summary[flag] = True
                        changed = True

    found: Counter[str] = Counter()
    for summary in summaries.values():
        if summary["loop"] and summary["sleep"] and summary["radio"]:
            found[summary["path"]] += 1
        elif summary["periodic"] and summary["sleep"] and summary["dynamic_getattr"]:
            found[summary["path"]] += 1
    return found


def _declared_baseline(guard: dict[str, Any]) -> Counter[str]:
    baseline: Counter[str] = Counter()
    for exception in guard["exceptions"]:
        assert exception["owner"] == guard["owner"]
        assert exception["path"] not in baseline
        count = exception["count"]
        assert isinstance(count, int) and count > 0
        baseline[exception["path"]] = count
    return baseline


def _guard_map(contract: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {guard["id"]: guard for guard in contract["guards"]}


def _assert_monotonic(
    base: dict[str, dict[str, Any]], head: dict[str, dict[str, Any]]
) -> None:
    assert set(head) == set(base), "guard set cannot be rewritten while debt exists"
    for guard_id, head_guard in head.items():
        base_guard = base[guard_id]
        assert head_guard["kind"] == base_guard["kind"]
        assert head_guard["owner"] == base_guard["owner"]
        head_counts = _declared_baseline(head_guard)
        base_counts = _declared_baseline(base_guard)
        assert set(head_counts) <= set(base_counts), f"{guard_id}: new debt path"
        assert all(count <= base_counts[path] for path, count in head_counts.items()), (
            f"{guard_id}: debt count increased"
        )


def _base_contract() -> dict[str, Any] | None:
    candidates: list[str] = []
    event_path = os.environ.get("GITHUB_EVENT_PATH")
    if event_path and Path(event_path).is_file():
        event = json.loads(Path(event_path).read_text())
        base_sha = event.get("pull_request", {}).get("base", {}).get("sha")
        if base_sha:
            candidates.append(str(base_sha))
    for command in (
        ["git", "merge-base", "HEAD", "origin/main"],
        ["git", "rev-parse", "HEAD^1"],
    ):
        completed = subprocess.run(
            command, cwd=ROOT, text=True, capture_output=True, check=False
        )
        if completed.returncode == 0:
            candidates.append(completed.stdout.strip())

    relative = CONTRACT_PATH.relative_to(ROOT).as_posix()
    for candidate in dict.fromkeys(candidates):
        if (
            not candidate
            or candidate
            == subprocess.run(
                ["git", "rev-parse", "HEAD"],
                cwd=ROOT,
                text=True,
                capture_output=True,
                check=True,
            ).stdout.strip()
        ):
            continue
        tree = subprocess.run(
            ["git", "cat-file", "-e", f"{candidate}^{{tree}}"],
            cwd=ROOT,
            capture_output=True,
            check=False,
        )
        if tree.returncode != 0:
            continue
        shown = subprocess.run(
            ["git", "show", f"{candidate}:{relative}"],
            cwd=ROOT,
            capture_output=True,
            check=False,
        )
        if shown.returncode != 0:
            return None  # deterministic bootstrap: reachable base has no contract
        return tomllib.loads(shown.stdout.decode())
    raise AssertionError("cannot resolve a reachable PR base for monotonic comparison")


def test_contract_header_and_family_rows_are_complete() -> None:
    contract = _contract()
    assert contract["contract_version"] == 1
    assert contract["authority"] == "StateStore"
    required = {
        "id",
        "capabilities",
        "intents",
        "command_route",
        "observation_route",
        "field_paths",
        "field_path_gaps",
        "public_fields",
        "ws_property",
        "selector",
        "pending",
        "surfaces",
        "automated_proof",
        "hardware_proof",
        "gap_owner",
    }
    ids = [family["id"] for family in contract["families"]]
    assert len(ids) == len(set(ids)) and len(ids) >= 20
    assert all(required <= family.keys() for family in contract["families"])


def test_every_control_handler_intent_is_registered_exactly_once() -> None:
    counts = Counter(
        command for family in _contract()["families"] for command in family["intents"]
    )
    assert not [command for command, count in counts.items() if count != 1]
    assert set(counts) == _control_handler_commands()


def test_every_manifest_field_path_is_canonical_registered_and_unique() -> None:
    paths = [
        path for family in _contract()["families"] for path in family["field_paths"]
    ]
    assert len(paths) == len(set(paths))
    registered = {str(path) for path in DEFAULT_FIELD_REGISTRY.paths()}
    for raw in paths:
        assert str(FieldPath.parse(raw)) == raw
        assert raw in registered
    assert set(paths) == registered


def test_generated_public_radio_fields_have_one_family_owner() -> None:
    contract = _contract()
    ownership = Counter(
        field for family in contract["families"] for field in family["public_fields"]
    )
    metadata = set(contract["public_metadata_fields"])
    expected = set().union(
        _model_fields(ServerStatePublic, "server"),
        _model_fields(ReceiverStatePublic, "receiver"),
        _model_fields(VfoSlotPublic, "vfo"),
        _model_fields(ScopeControlsPublic, "scope"),
        _model_fields(FixedEdgePublic, "fixed"),
        _model_fields(ConnectionPublic, "connection"),
        _model_fields(RadioDetailPublic, "radioDetail"),
        _model_fields(RadioHealthPublic, "radioHealth"),
    )
    assert expected == set(ownership) | metadata
    assert not [field for field, count in ownership.items() if count != 1]


def test_every_profile_provider_and_nested_surface_is_registered() -> None:
    contract = _contract()
    profile_paths = [entry["path"] for entry in contract["profiles"]]
    assert len(profile_paths) == len(set(profile_paths))
    actual_profiles = {
        path.relative_to(ROOT).as_posix()
        for path in (ROOT / "rigs").glob("*.toml")
        if not path.name.startswith("_")
    }
    assert set(profile_paths) == actual_profiles
    for entry in contract["profiles"]:
        assert entry["provider"] in PROVIDER_IDS
        with (ROOT / entry["path"]).open("rb") as stream:
            assert entry["provider"] == _derived_profile_provider(tomllib.load(stream))

    declared = {
        surface for family in contract["families"] for surface in family["surfaces"]
    }
    actual = {
        path.relative_to(ROOT).as_posix()
        for directory in (
            ROOT / "frontend/src/semantic",
            ROOT / "frontend/src/components-v2/panels",
        )
        for path in directory.rglob("*.svelte")
        if not _is_test_path(path.relative_to(ROOT).as_posix())
    }
    assert actual <= declared


def test_semantic_analyzer_compiles_strictly() -> None:
    completed = subprocess.run(
        ["npx", "tsc", "-p", "tsconfig.analyzer.json"],
        cwd=ROOT / "frontend",
        capture_output=True,
        text=True,
        check=False,
    )
    assert completed.returncode == 0, completed.stdout + completed.stderr


def test_known_architecture_debt_baselines_are_exact_and_monotonic() -> None:
    contract = _contract()
    frontend = _frontend_counts({"base": {}})["base"]
    python = _python_poll_counts(_python_sources())
    for guard in contract["guards"]:
        found = Counter(
            python if guard["kind"] == PYTHON_GUARD else frontend[guard["kind"]]
        )
        assert found == _declared_baseline(guard), (
            f"{guard['id']} baseline drift; found={dict(found)}, "
            f"declared={dict(_declared_baseline(guard))}"
        )
    base = _base_contract()
    if base is not None:
        _assert_monotonic(_guard_map(base), _guard_map(contract))


FRONTEND_ESCAPES: list[tuple[str, str, dict[str, str], str]] = [
    (
        "writer_wild",
        "truth_patch_calls",
        {
            "frontend/src/lib/wild-writer.ts": "export * from '$lib/stores/radio.svelte';",
            "frontend/src/lib/wild-consumer.ts": "import { patchRadioState as commit } from './wild-writer';\ncommit({ mode: 'USB' });",
        },
        "frontend/src/lib/wild-consumer.ts",
    ),
    (
        "writer_const",
        "truth_patch_calls",
        {
            "frontend/src/lib/const-writer.ts": "import { patchRadioState as p } from '$lib/stores/radio.svelte';\nexport const updateTruth = p;",
            "frontend/src/lib/const-consumer.ts": "import { updateTruth as commit } from './const-writer';\ncommit({ freqHz: 7100000 });",
        },
        "frontend/src/lib/const-consumer.ts",
    ),
    (
        "writer_namespace",
        "truth_patch_calls",
        {
            "frontend/src/lib/ns-writer.ts": "export { patchRadioState as updateTruth } from '$lib/stores/radio.svelte';",
            "frontend/src/lib/ns-consumer.ts": "import * as truth from './ns-writer';\ntruth.updateTruth({ mode: 'USB' });",
        },
        "frontend/src/lib/ns-consumer.ts",
    ),
    (
        "writer_optional",
        "truth_patch_calls",
        {
            "frontend/src/lib/optional-writer.ts": "import { patchRadioState as commit } from '$lib/stores/radio.svelte';\ncommit?.({ mode: 'USB' });",
        },
        "frontend/src/lib/optional-writer.ts",
    ),
    (
        "transport_wild",
        "presentation_transport",
        {
            "frontend/src/lib/wild-transport.ts": "export * from '$lib/transport/ws-client';",
            "frontend/src/semantic/WildTransportSurface.svelte": "<script>import { sendCommand } from '$lib/wild-transport'; sendCommand('set_freq');</script>",
        },
        "frontend/src/semantic/WildTransportSurface.svelte",
    ),
    (
        "transport_namespace",
        "presentation_transport",
        {
            "frontend/src/lib/ns-transport.ts": "export { sendCommand as transmit } from '$lib/transport/ws-client';",
            "frontend/src/semantic/NsTransportSurface.svelte": "<script>import * as bus from '$lib/ns-transport'; bus.transmit('set_freq');</script>",
        },
        "frontend/src/semantic/NsTransportSurface.svelte",
    ),
    (
        "transport_dynamic",
        "presentation_transport",
        {
            "frontend/src/semantic/VariableImportSurface.svelte": "<script>const modulePath = '$lib/transport/ws-client'; const tx = import(modulePath);</script>",
        },
        "frontend/src/semantic/VariableImportSurface.svelte",
    ),
    (
        "default_alias",
        "fabricated_defaults",
        {
            "frontend/src/lib/runtime/adapters/AliasDefault.ts": "const current = state.mode;\nconst shown = current ?? 'USB';",
        },
        "frontend/src/lib/runtime/adapters/AliasDefault.ts",
    ),
    (
        "default_const",
        "fabricated_defaults",
        {
            "frontend/src/lib/runtime/adapters/ConstDefault.ts": "const DEFAULT_FILTER = 'FIL1';\nconst filter = state.filter ?? DEFAULT_FILTER;",
        },
        "frontend/src/lib/runtime/adapters/ConstDefault.ts",
    ),
    (
        "default_ternary",
        "fabricated_defaults",
        {
            "frontend/src/lib/runtime/adapters/TernaryDefault.ts": "const freq = state.freqHz == null ? 7_100_000 : state.freqHz;",
        },
        "frontend/src/lib/runtime/adapters/TernaryDefault.ts",
    ),
    (
        "storage_window",
        "truth_persistence",
        {
            "frontend/src/lib/window-storage.ts": "const cache = window.localStorage;\ncache.setItem('activeVfo', state.activeSlot);",
        },
        "frontend/src/lib/window-storage.ts",
    ),
    (
        "storage_bracket",
        "truth_persistence",
        {
            "frontend/src/lib/bracket-storage.ts": "localStorage['setItem']('activeVfo', state.activeSlot);",
        },
        "frontend/src/lib/bracket-storage.ts",
    ),
    (
        "storage_contaminated",
        "truth_persistence",
        {
            "frontend/src/lib/contaminated-storage.ts": "const themePayload = state.activeSlot;\nlocalStorage.setItem('activeVfo', themePayload);",
        },
        "frontend/src/lib/contaminated-storage.ts",
    ),
    (
        "storage_mic",
        "truth_persistence",
        {
            "frontend/src/lib/mic-storage.ts": "localStorage.setItem('micGain', String(state.micGain));",
        },
        "frontend/src/lib/mic-storage.ts",
    ),
    (
        "frame_renamed",
        "spectrum_metadata",
        {
            "frontend/src/components/spectrum/RenamedFrame.svelte": "<script>function render(scopeFrame) { displayCenter = scopeFrame.centerHz; displayMode = scopeFrame.mode; }</script>",
        },
        "frontend/src/components/spectrum/RenamedFrame.svelte",
    ),
    (
        "frame_path",
        "spectrum_metadata",
        {
            "frontend/src/lib/runtime/adapters/geometry.ts": "displayCenter = frame.centerHz; displaySpan = frame.spanHz;",
        },
        "frontend/src/lib/runtime/adapters/geometry.ts",
    ),
    (
        "frame_bracket",
        "spectrum_metadata",
        {
            "frontend/src/components/spectrum/BracketFrame.svelte": "<script>displayCenter = packet['centerHz'];</script>",
        },
        "frontend/src/components/spectrum/BracketFrame.svelte",
    ),
    (
        "timer_alias",
        "frontend_timers",
        {
            "frontend/src/lib/rf-power-worker.ts": "const schedule = setInterval;\nschedule(refreshRfPower, 100);",
        },
        "frontend/src/lib/rf-power-worker.ts",
    ),
    (
        "timer_bracket",
        "frontend_timers",
        {
            "frontend/src/lib/comp-worker.ts": "globalThis['setTimeout'](refreshCompressor, 100);",
        },
        "frontend/src/lib/comp-worker.ts",
    ),
    (
        "store_mutation",
        "parallel_truth_store",
        {
            "frontend/src/lib/stores/live-facts.svelte.ts": "let live = $state({});\nlive.freqHz = 7_100_000; live.mode = 'USB';",
        },
        "frontend/src/lib/stores/live-facts.svelte.ts",
    ),
    (
        "store_shadow",
        "parallel_truth_store",
        {
            "frontend/src/lib/stores/shadow.svelte.ts": "let shadow = $state({ value: null });\nshadow.value = incomingServerState;",
        },
        "frontend/src/lib/stores/shadow.svelte.ts",
    ),
]


def test_all_23_verifier_escapes_are_rejected_and_allowed_plane_is_clean() -> None:
    scenarios = {name: sources for name, _, sources, _ in FRONTEND_ESCAPES}
    scenarios["allowed"] = {
        "frontend/src/lib/allowed-prefs.svelte.ts": "let prefs = $state({ theme: 'dark', layout: 'compact' });\nlocalStorage.setItem('theme', prefs.theme);\npendingIntent = { status: 'failed', error: 'timeout' };",
        "frontend/src/components/spectrum/AllowedPixels.svelte": "<script>renderSpectrumPixels(frame.pixels); renderAudioSamples(pcm);</script>",
    }
    results = _frontend_counts(scenarios)
    for name, guard, _, expected_path in FRONTEND_ESCAPES:
        assert results[name][guard].get(expected_path, 0) > 0, f"escaped: {name}"
    for guard, paths in results["allowed"].items():
        assert "frontend/src/lib/allowed-prefs.svelte.ts" not in paths, guard
        assert "frontend/src/components/spectrum/AllowedPixels.svelte" not in paths, (
            guard
        )

    python_cases = [
        (
            "src/rigplane/web/control_worker.py",
            "import asyncio\nasync def worker(radio):\n    while True:\n        await radio.get_rf_power()\n        await asyncio.sleep(1)\n",
        ),
        (
            "src/rigplane/web/control_refresh.py",
            "from asyncio import sleep as pause\nasync def refresh_rf_power(radio):\n    while True:\n        await radio.get_rf_power()\n        await pause(1)\n",
        ),
        (
            "src/rigplane/web/control_wrapped.py",
            "import asyncio\nasync def fetch_value(radio):\n    return await radio.get_rf_power()\nasync def delay():\n    await asyncio.sleep(1)\nasync def unrelated_name(radio):\n    while True:\n        await fetch_value(radio)\n        await delay()\n",
        ),
    ]
    for expected_path, source in python_cases:
        assert (
            _python_poll_counts(_python_sources({expected_path: source}))[expected_path]
            > 0
        )


def test_nested_authority_provenance_and_typed_scopeframe_are_rejected() -> None:
    scenarios = {
        "store_object_binding": {
            "frontend/src/lib/stores/object-binding.svelte.ts": (
                "export let live = $state({ controls: {} as Record<string, number> });\n"
                "const { controls } = live;\n"
                "controls.rfPower = 50;\n"
            ),
        },
        "store_array_binding": {
            "frontend/src/lib/stores/array-binding.svelte.ts": (
                "export let live = $state([{ controls: {} as Record<string, string> }]);\n"
                "const [{ controls }] = live;\n"
                "controls.mode = 'USB';\n"
            ),
        },
        "store_destructuring_assignment": {
            "frontend/src/lib/stores/destructuring-assignment.svelte.ts": (
                "declare const incoming: { rfPower: number };\n"
                "export let live = $state({ rfPower: 0 });\n"
                "({ rfPower: live.rfPower } = incoming);\n"
            ),
        },
        "store_nested_destructuring_assignment": {
            "frontend/src/lib/stores/nested-destructuring-assignment.svelte.ts": (
                "declare const incomingServerState: { receiver: { mode: string } };\n"
                "export let live = $state({ receiver: { mode: '' } });\n"
                "({ receiver: { mode: live.receiver.mode } } = incomingServerState);\n"
            ),
        },
        "typed_scopeframe": {
            "frontend/src/lib/runtime/adapters/typed-frame-consumer.ts": (
                "import type { ScopeFrame } from './scope-adapter';\n"
                "export function labels(frame: ScopeFrame) {\n"
                "  return [frame.mode, frame.receiver];\n"
                "}\n"
            ),
        },
        "typed_scopeframe_binding": {
            "frontend/src/lib/runtime/adapters/typed-frame-binding.ts": (
                "import type { ScopeFrame } from './scope-adapter';\n"
                "export function labels(frame: ScopeFrame) {\n"
                "  const { mode, receiver } = frame;\n"
                "  return [mode, receiver];\n"
                "}\n"
            ),
        },
        "allowed_typed_pixels": {
            "frontend/src/lib/runtime/adapters/typed-frame-pixels.ts": (
                "import type { ScopeFrame } from './scope-adapter';\n"
                "export function samples(frame: ScopeFrame) { return frame.pixels; }\n"
            ),
        },
    }
    results = _frontend_counts(scenarios)
    for name in (
        "store_object_binding",
        "store_array_binding",
        "store_destructuring_assignment",
        "store_nested_destructuring_assignment",
    ):
        path = next(iter(scenarios[name]))
        assert results[name]["parallel_truth_store"].get(path, 0) > 0, name
    for name in ("typed_scopeframe", "typed_scopeframe_binding"):
        path = next(iter(scenarios[name]))
        assert results[name]["spectrum_metadata"].get(path, 0) == 2, name
    allowed_path = next(iter(scenarios["allowed_typed_pixels"]))
    for guard in results["allowed_typed_pixels"].values():
        assert allowed_path not in guard


def test_restricted_authority_constructs_and_imports_fail_loud() -> None:
    scenarios = {
        "eval": {
            "frontend/src/semantic/EvalSurface.svelte": (
                "<script lang='ts'>declare const source: string; eval(source);</script>"
            ),
        },
        "function_call": {
            "frontend/src/components-v2/panels/FunctionPanel.svelte": (
                "<script>const build = Function('return 1');</script>"
            ),
        },
        "new_function": {
            "frontend/src/lib/runtime/commands/new-function.ts": (
                "export const build = new Function('return 1');"
            ),
        },
        "proxy_writer": {
            "frontend/src/lib/proxy-writer.ts": (
                "import { patchRadioState } from '$lib/stores/radio.svelte';\n"
                "export const wrapped = new Proxy(patchRadioState, {});\n"
            ),
        },
        "nonfinite_import": {
            "frontend/src/lib/stores/nonfinite-loader.svelte.ts": (
                "export function load(specifier: string) { return import(specifier); }"
            ),
        },
        "unresolved_static_import": {
            "frontend/src/lib/stores/unresolved-static.svelte.ts": (
                "import { missing } from '$lib/authority-does-not-exist';\n"
                "export const value = missing;\n"
            ),
        },
        "unresolved_dynamic_import": {
            "frontend/src/lib/runtime/commands/unresolved-dynamic.ts": (
                "export const value = import('$lib/authority-does-not-exist');"
            ),
        },
        "outside_authority_is_still_authority": {
            "frontend/src/plugin-loader.ts": (
                "export function load(specifier: string) { return import(specifier); }"
            ),
        },
    }
    result = _frontend_analysis(scenarios)
    for name in scenarios:
        assert result["errors"][name], f"did not fail loud: {name}"


def test_python_getattr_dispatch_is_resolved_or_conservatively_rejected() -> None:
    static_path = "src/rigplane/web/static_getattr_worker.py"
    static_source = (
        "import asyncio\n"
        "async def worker(radio):\n"
        "    while True:\n"
        "        await getattr(radio, 'get_rf_power')()\n"
        "        await asyncio.sleep(1)\n"
    )
    assert (
        _python_poll_counts(_python_sources({static_path: static_source}))[static_path]
        > 0
    )

    dynamic_path = "src/rigplane/web/dynamic_getattr_worker.py"
    dynamic_source = (
        "import asyncio\n"
        "async def read(radio, method):\n"
        "    return await getattr(radio, method)()\n"
        "async def worker(radio, method):\n"
        "    while True:\n"
        "        await read(radio, method)\n"
        "        await asyncio.sleep(1)\n"
    )
    assert (
        _python_poll_counts(_python_sources({dynamic_path: dynamic_source}))[
            dynamic_path
        ]
        > 0
    )

    aliased_path = "src/rigplane/web/aliased_getattr_worker.py"
    aliased_source = (
        "import asyncio\n"
        "from builtins import getattr as resolve\n"
        "async def read(radio, method):\n"
        "    return await resolve(radio, method)()\n"
        "async def worker(radio, method):\n"
        "    while True:\n"
        "        await read(radio, method)\n"
        "        await asyncio.sleep(1)\n"
    )
    assert (
        _python_poll_counts(_python_sources({aliased_path: aliased_source}))[
            aliased_path
        ]
        > 0
    )

    allowed_path = "src/rigplane/web/non_poll_getattr.py"
    allowed_source = "def lookup(obj, name):\n    return getattr(obj, name)\n"
    assert (
        _python_poll_counts(_python_sources({allowed_path: allowed_source}))[
            allowed_path
        ]
        == 0
    )


def test_fixed_point_store_and_scopeframe_provenance_variants_are_rejected() -> None:
    scenarios = {
        "object_wrapper": {
            "frontend/src/lib/stores/object-wrapper.svelte.ts": (
                "export let live = $state({ controls: {} as Record<string, number> });\n"
                "const wrapper = { alias: live };\n"
                "const { alias } = wrapper;\n"
                "alias.rfPower = 50;\n"
            )
        },
        "array_wrapper": {
            "frontend/src/lib/stores/array-wrapper.svelte.ts": (
                "export let live = $state([{} as Record<string, string>]);\n"
                "const wrapper = [live];\n"
                "const [alias] = wrapper;\n"
                "alias.mode = 'USB';\n"
            )
        },
        "later_assignment": {
            "frontend/src/lib/stores/later-assignment.svelte.ts": (
                "export let live = $state({} as Record<string, number>);\n"
                "let alias;\n"
                "alias = live;\n"
                "alias.rfPower = 50;\n"
            )
        },
        "identity_return": {
            "frontend/src/lib/stores/identity-return.svelte.ts": (
                "function identity<T>(value: T): T { return value; }\n"
                "export let live = $state({} as Record<string, string>);\n"
                "const alias = identity(live);\n"
                "alias.mode = 'USB';\n"
            )
        },
        "identity_chain": {
            "frontend/src/lib/stores/identity-chain.svelte.ts": (
                "function identity<T>(value: T): T { return value; }\n"
                "function second<T>(value: T): T { return identity(value); }\n"
                "export let live = $state({} as Record<string, number>);\n"
                "second(live).rfPower = 50;\n"
            )
        },
        "parameter_flow": {
            "frontend/src/lib/stores/parameter-flow.svelte.ts": (
                "export let live = $state({} as Record<string, number>);\n"
                "function mutate(value: Record<string, number>) { value.rfPower = 50; }\n"
                "mutate(live);\n"
            )
        },
        "readonly_scope": {
            "frontend/src/lib/runtime/adapters/readonly-scope.ts": (
                "import type { ScopeFrame } from './scope-adapter';\n"
                "export function labels(frame: Readonly<ScopeFrame>) {\n"
                "  return [frame.mode, frame.receiver];\n"
                "}\n"
            )
        },
        "utility_alias_scope": {
            "frontend/src/lib/runtime/adapters/utility-alias-scope.ts": (
                "import type { ScopeFrame } from './scope-adapter';\n"
                "type Wrapped = Readonly<ScopeFrame>;\n"
                "export function labels(frame: Wrapped) { return [frame.mode, frame.receiver]; }\n"
            )
        },
        "local_alias_return": {
            "frontend/src/lib/stores/local-alias-return.svelte.ts": (
                "function identity<T>(value: T): T { const result = value; return result; }\n"
                "export let live = $state({} as Record<string, number>);\n"
                "identity(live).rfPower = 50;\n"
            )
        },
        "conditional_return": {
            "frontend/src/lib/stores/conditional-return.svelte.ts": (
                "function identity<T>(value: T, flag: boolean): T { return flag ? value : value; }\n"
                "export let live = $state({} as Record<string, string>);\n"
                "identity(live, true).mode = 'USB';\n"
            )
        },
        "object_projection_return": {
            "frontend/src/lib/stores/object-projection-return.svelte.ts": (
                "function identity<T>(value: T): T { return { value }.value; }\n"
                "export let live = $state({} as Record<string, number>);\n"
                "identity(live).rfPower = 50;\n"
            )
        },
        "arrow_alias_return": {
            "frontend/src/lib/stores/arrow-alias-return.svelte.ts": (
                "const identity = <T>(value: T): T => { const alias = value; return alias; };\n"
                "export let live = $state({} as Record<string, string>);\n"
                "identity(live).mode = 'USB';\n"
            )
        },
        "array_projection_return": {
            "frontend/src/lib/stores/array-projection-return.svelte.ts": (
                "function identity<T>(value: T): T { return [value][0]; }\n"
                "export let live = $state({} as Record<string, number>);\n"
                "identity(live).rfPower = 50;\n"
            )
        },
        "delegated_alias_return": {
            "frontend/src/lib/stores/delegated-alias-return.svelte.ts": (
                "const inner = <T>(value: T): T => { const alias = value; return alias; };\n"
                "function outer<T>(value: T): T { return inner(value); }\n"
                "export let live = $state({} as Record<string, string>);\n"
                "outer(live).mode = 'USB';\n"
            )
        },
        "nested_object_projection": {
            "frontend/src/lib/stores/nested-object-projection.svelte.ts": (
                "function identity<T>(value: T): T { return { outer: { inner: value } }.outer.inner; }\n"
                "export let live = $state({} as Record<string, number>);\n"
                "identity(live).rfPower = 50;\n"
            )
        },
        "nested_array_projection": {
            "frontend/src/lib/stores/nested-array-projection.svelte.ts": (
                "function identity<T>(value: T): T { return [[value]][0][0]; }\n"
                "export let live = $state({} as Record<string, string>);\n"
                "identity(live).mode = 'USB';\n"
            )
        },
        "delegated_nested_projection": {
            "frontend/src/lib/stores/delegated-nested-projection.svelte.ts": (
                "function inner<T>(value: T): T { return ({ x: [value] }).x[0]; }\n"
                "function outer<T>(value: T): T { return inner(value); }\n"
                "export let live = $state({} as Record<string, number>);\n"
                "outer(live).rfPower = 50;\n"
            )
        },
        "identifier_nested_projection": {
            "frontend/src/lib/stores/identifier-nested-projection.svelte.ts": (
                "function identity<T>(value: T): T { const box = { outer: { inner: value } }; return box.outer.inner; }\n"
                "export let live = $state({} as Record<string, string>);\n"
                "identity(live).mode = 'USB';\n"
            )
        },
        "conditional_nested_projection": {
            "frontend/src/lib/stores/conditional-nested-projection.svelte.ts": (
                "function identity<T>(value: T, flag: boolean): T { const box = flag ? { outer: { inner: value } } : { outer: { inner: value } }; return box.outer.inner; }\n"
                "export let live = $state({} as Record<string, number>);\n"
                "identity(live, true).rfPower = 50;\n"
            )
        },
        "spread_nested_projection": {
            "frontend/src/lib/stores/spread-nested-projection.svelte.ts": (
                "function identity<T>(value: T): T { return ({ ...{ outer: { inner: value } } }).outer.inner; }\n"
                "export let live = $state({} as Record<string, string>);\n"
                "identity(live).mode = 'USB';\n"
            )
        },
        "array_spread_projection": {
            "frontend/src/lib/stores/array-spread-projection.svelte.ts": (
                "function identity<T>(value: T): T { return [...[[value]]][0][0]; }\n"
                "export let live = $state({} as Record<string, number>);\n"
                "identity(live).rfPower = 50;\n"
            )
        },
        "mixed_callsite_projection": {
            "frontend/src/lib/stores/mixed-callsite-projection.svelte.ts": (
                "function identity<T>(value: T): T { return { outer: { inner: value } }.outer.inner; }\n"
                "const preference = { theme: 'dark' }; identity(preference).theme = 'light';\n"
                "export let live = $state({} as Record<string, string>); identity(live).mode = 'USB';\n"
            )
        },
        "preference_only_projection": {
            "frontend/src/lib/runtime/preferences/projection.ts": (
                "function identity<T>(value: T): T { return { outer: { inner: value } }.outer.inner; }\n"
                "const preference = { theme: 'dark' }; identity(preference).theme = 'light';\n"
            )
        },
        "opaque_envelope_scope": {
            "frontend/src/lib/runtime/adapters/opaque-envelope-scope.ts": (
                "import type { ScopeFrame } from './scope-adapter';\n"
                "type Envelope<T> = { value: T; mode: string };\n"
                "export function labels(frame: Envelope<ScopeFrame>) { return frame.mode; }\n"
            )
        },
    }
    results = _frontend_counts(scenarios)
    for name in (
        "object_wrapper",
        "array_wrapper",
        "later_assignment",
        "identity_return",
        "identity_chain",
        "parameter_flow",
        "local_alias_return",
        "conditional_return",
        "object_projection_return",
        "arrow_alias_return",
        "array_projection_return",
        "delegated_alias_return",
        "nested_object_projection",
        "nested_array_projection",
        "delegated_nested_projection",
        "identifier_nested_projection",
        "conditional_nested_projection",
        "spread_nested_projection",
        "array_spread_projection",
        "mixed_callsite_projection",
    ):
        path = next(iter(scenarios[name]))
        assert results[name]["parallel_truth_store"].get(path, 0) > 0, name
    for name in ("readonly_scope", "utility_alias_scope"):
        path = next(iter(scenarios[name]))
        assert results[name]["spectrum_metadata"].get(path, 0) == 2, name
    opaque_path = next(iter(scenarios["opaque_envelope_scope"]))
    assert (
        results["opaque_envelope_scope"]["spectrum_metadata"].get(opaque_path, 0) == 0
    )
    preference_path = next(iter(scenarios["preference_only_projection"]))
    assert (
        results["preference_only_projection"]["parallel_truth_store"].get(
            preference_path, 0
        )
        == 0
    )


def test_restricted_wrapper_ui_roots_and_locale_template_boundary() -> None:
    scenarios = {
        "eval_call": {
            "frontend/src/components-v2/controls/EvalCall.svelte": (
                "<script lang='ts'>declare const source: string; eval.call(globalThis, source);</script>"
            )
        },
        "eval_bind": {
            "frontend/src/components-v2/controls/EvalBind.svelte": (
                "<script lang='ts'>declare const source: string; const run = eval.bind(globalThis); run(source);</script>"
            )
        },
        "function_call": {
            "frontend/src/components-v2/controls/FunctionCall.svelte": (
                "<script>Function.call(null, 'return 1');</script>"
            )
        },
        "proxy_revocable": {
            "frontend/src/components-v2/wiring/proxy-revocable.ts": (
                "import { patchRadioState } from '$lib/stores/radio.svelte';\n"
                "export const wrapped = Proxy.revocable(patchRadioState, {}).proxy;\n"
            )
        },
        "reflect_apply": {
            "frontend/src/App.svelte": (
                "<script lang='ts'>declare const source: string; Reflect.apply(eval, globalThis, [source]);</script>"
            )
        },
        "sequence_eval": {
            "frontend/src/components-v2/controls/SequenceEval.svelte": (
                "<script lang='ts'>declare const source: string; (0, eval)(source);</script>"
            )
        },
        "reflect_apply_alias": {
            "frontend/src/components-v2/controls/ReflectAlias.svelte": (
                "<script lang='ts'>declare const source: string; const invoke = Reflect.apply; "
                "invoke(eval, globalThis, [source]);</script>"
            )
        },
        "reflect_apply_destructure": {
            "frontend/src/components-v2/controls/ReflectDestructure.svelte": (
                "<script lang='ts'>declare const source: string; const { apply: invoke } = Reflect; "
                "invoke(eval, globalThis, [source]);</script>"
            )
        },
        "reflect_apply_alias_chain": {
            "frontend/src/components-v2/controls/ReflectAliasChain.svelte": (
                "<script lang='ts'>declare const source: string; const first = Reflect.apply; "
                "const invoke = first; invoke(eval, globalThis, [source]);</script>"
            )
        },
        "reflect_owner_alias_destructure": {
            "frontend/src/components-v2/controls/ReflectOwnerAlias.svelte": (
                "<script lang='ts'>declare const source: string; const R = Reflect; "
                "const { apply: invoke } = R; invoke(eval, globalThis, [source]);</script>"
            )
        },
        "reflect_object_carrier": {
            "frontend/src/components-v2/controls/ReflectObjectCarrier.svelte": (
                "<script lang='ts'>declare const source: string; const holder = { invoke: Reflect.apply }; "
                "holder.invoke(eval, globalThis, [source]);</script>"
            )
        },
        "reflect_array_carrier": {
            "frontend/src/components-v2/controls/ReflectArrayCarrier.svelte": (
                "<script lang='ts'>declare const source: string; "
                "[Reflect.apply][0](eval, globalThis, [source]);</script>"
            )
        },
        "reflect_conditional_carrier": {
            "frontend/src/components-v2/controls/ReflectConditionalCarrier.svelte": (
                "<script lang='ts'>declare const source: string; declare const flag: boolean; "
                "const invoke = flag ? Reflect.apply : Reflect.apply; "
                "invoke(eval, globalThis, [source]);</script>"
            )
        },
        "reflect_nested_carrier": {
            "frontend/src/components-v2/controls/ReflectNestedCarrier.svelte": (
                "<script lang='ts'>declare const source: string; "
                "const holder = { outer: { invoke: Reflect.apply } }; "
                "holder.outer.invoke(eval, globalThis, [source]);</script>"
            )
        },
        "reflect_spread_carrier": {
            "frontend/src/components-v2/controls/ReflectSpreadCarrier.svelte": (
                "<script lang='ts'>declare const source: string; "
                "const holder = { ...{ invoke: Reflect.apply } }; "
                "holder.invoke(eval, globalThis, [source]);</script>"
            )
        },
        "wiring_nonfinite": {
            "frontend/src/components-v2/wiring/nonfinite.ts": (
                "export function load(specifier: string) { return import(specifier); }"
            )
        },
        "app_unresolved": {
            "frontend/src/App.svelte": (
                "<script>import { missing } from '$lib/authority-does-not-exist'; void missing;</script>"
            )
        },
        "control_transport": {
            "frontend/src/components-v2/controls/direct-transport.ts": (
                "import { sendCommand } from '$lib/transport/ws-client';\n"
                "sendCommand('set_freq');\n"
            )
        },
        "allowed_locale_template": {
            "frontend/src/lib/i18n/locale-loader.ts": (
                "declare const locale: 'en-US' | 'ja-JP' | 'ru-RU';\n"
                "export const catalog = import(`./locales/${locale}.json`);\n"
            )
        },
        "unsafe_locale_template": {
            "frontend/src/lib/i18n/unsafe-locale-loader.ts": (
                "declare const locale: string;\n"
                "export const catalog = import(`./locales/${locale}.json`);\n"
            )
        },
        "traversal_locale_template": {
            "frontend/src/lib/i18n/traversal-locale-loader.ts": (
                "declare const locale: 'en-US' | '../secrets';\n"
                "export const catalog = import(`./locales/${locale}.json`);\n"
            )
        },
        "locale_template_outside_root": {
            "frontend/src/components-v2/controls/locale-loader.ts": (
                "declare const locale: 'en-US' | 'ja-JP';\n"
                "export const catalog = import(`./locales/${locale}.json`);\n"
            )
        },
        "local_eval_shadow": {
            "frontend/src/components-v2/controls/local-eval.ts": (
                "function eval(source: string) { return source; } eval('safe');"
            )
        },
        "local_reflect_shadow": {
            "frontend/src/components-v2/controls/LocalReflect.svelte": (
                "<script lang='ts'>const Reflect = { apply: (value: number) => value }; "
                "const holder = { invoke: Reflect.apply }; holder.invoke(1);</script>"
            )
        },
    }
    result = _frontend_analysis(scenarios)
    allowed = "allowed_locale_template"
    clean = {allowed, "control_transport", "local_eval_shadow", "local_reflect_shadow"}
    for name in scenarios.keys() - clean:
        assert result["errors"][name], f"did not fail loud: {name}"
    assert result["errors"][allowed] == []
    assert result["errors"]["local_eval_shadow"] == []
    assert result["errors"]["local_reflect_shadow"] == []
    restricted_eval = {
        "eval_call",
        "eval_bind",
        "reflect_apply",
        "sequence_eval",
        "reflect_apply_alias",
        "reflect_apply_destructure",
        "reflect_apply_alias_chain",
        "reflect_owner_alias_destructure",
        "reflect_object_carrier",
        "reflect_array_carrier",
        "reflect_conditional_carrier",
        "reflect_nested_carrier",
        "reflect_spread_carrier",
    }
    for name in restricted_eval:
        path = next(iter(scenarios[name]))
        expected = f"restricted eval construct in {path}"
        assert result["errors"][name] == [expected]
    transport_path = next(iter(scenarios["control_transport"]))
    assert result["errors"]["control_transport"] == []
    assert (
        result["scenarios"]["control_transport"]["presentation_transport"].get(
            transport_path, 0
        )
        > 0
    )


def test_python_dispatch_alias_and_unbounded_iterator_variants_are_rejected() -> None:
    cases = {
        "src/rigplane/web/device_dispatch.py": (
            "import asyncio\n"
            "async def worker(device, method):\n"
            "    while True:\n"
            "        await getattr(device, method)()\n"
            "        await asyncio.sleep(1)\n"
        ),
        "src/rigplane/web/assigned_getattr.py": (
            "import asyncio\n"
            "resolve = getattr\n"
            "async def worker(device):\n"
            "    while True:\n"
            "        await resolve(device, 'get_rf_power')()\n"
            "        await asyncio.sleep(1)\n"
        ),
        "src/rigplane/web/count_dispatch.py": (
            "import asyncio\n"
            "import itertools\n"
            "async def worker(device, method):\n"
            "    for _ in itertools.count():\n"
            "        await getattr(device, method)()\n"
            "        await asyncio.sleep(1)\n"
        ),
        "src/rigplane/web/count_alias_dispatch.py": (
            "import asyncio\n"
            "from itertools import count as forever\n"
            "async def worker(device, method):\n"
            "    for _ in forever():\n"
            "        await getattr(device, method)()\n"
            "        await asyncio.sleep(1)\n"
        ),
    }
    sources = _python_sources(cases)
    counts = _python_poll_counts(sources)
    for path in cases:
        assert counts[path] > 0, path


def test_matching_same_pr_manifest_expansion_is_still_rejected() -> None:
    contract = _contract()
    base = _guard_map(contract)
    head = json.loads(json.dumps(base))
    timer = next(guard for guard in head.values() if guard["kind"] == "frontend_timers")
    timer["exceptions"].append(
        {
            "path": "frontend/src/lib/new-radio-loop.ts",
            "count": 1,
            "owner": timer["owner"],
        }
    )
    base_timer = next(
        guard for guard in base.values() if guard["kind"] == "frontend_timers"
    )
    expanded_actual = _declared_baseline(base_timer)
    expanded_actual["frontend/src/lib/new-radio-loop.ts"] = 1
    assert expanded_actual == _declared_baseline(timer), (
        "current-source equality alone accepts a same-PR code+manifest expansion"
    )
    try:
        _assert_monotonic(base, head)
    except AssertionError:
        return
    raise AssertionError("same-PR violation plus matching exception expansion passed")
