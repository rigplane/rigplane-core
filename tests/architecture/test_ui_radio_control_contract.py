"""Finite structural ownership contract for UI-visible radio facts (MOR-1406).

The contract inventories every control surface and enforces declared module
capabilities plus authority-sensitive sinks. It intentionally does not claim
whole-program TypeScript or Python provenance.
"""

from __future__ import annotations

import ast
import copy
import json
import os
import subprocess
import tomllib
from collections import Counter
from pathlib import Path
from typing import Any

import pytest

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
PLUGIN_PATH = ROOT / "frontend/scripts/radio-authority-eslint-plugin.mjs"
PROVIDER_IDS = {"icom_civ", "yaesu_cat", "xiegu_civ", "kenwood_cat"}
RADIO_CALL_PREFIXES = ("get_", "set_", "read_", "write_", "poll_", "refresh_")


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
        assert set(head_counts) <= set(base_counts), f"{guard_id}: new owner/debt path"
        assert all(count <= base_counts[path] for path, count in head_counts.items()), (
            f"{guard_id}: owner/debt count increased"
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
    head = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=True,
    ).stdout.strip()
    for candidate in dict.fromkeys(candidates):
        if not candidate or candidate == head:
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
            return None
        return tomllib.loads(shown.stdout.decode())
    raise AssertionError("cannot resolve a reachable PR base for monotonic comparison")


def _plugin_owners() -> dict[str, list[str]]:
    script = (
        "import {radioAuthorityOwners as o} from "
        + json.dumps(PLUGIN_PATH.as_uri())
        + "; console.log(JSON.stringify(o));"
    )
    completed = subprocess.run(
        ["node", "--input-type=module", "--eval", script],
        cwd=ROOT / "frontend",
        text=True,
        capture_output=True,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr
    value = json.loads(completed.stdout)
    assert isinstance(value, dict)
    return {str(key): [str(path) for path in paths] for key, paths in value.items()}


def _call_name(node: ast.Call) -> str:
    target = node.func
    if isinstance(target, ast.Name):
        return target.id.lower()
    if isinstance(target, ast.Attribute):
        return target.attr.lower()
    return ""


def _python_recurring_radio_paths(
    overrides: dict[str, str] | None = None,
) -> set[str]:
    sources = {
        path.relative_to(ROOT).as_posix(): path.read_text()
        for path in (ROOT / "src/rigplane").rglob("*.py")
        if not _is_test_path(path.relative_to(ROOT).as_posix())
    }
    sources.update(overrides or {})
    terms = {
        command.removeprefix("get_")
        .removeprefix("set_")
        .removeprefix("read_")
        .removeprefix("write_")
        for command in _control_handler_commands()
    }

    class FunctionBody(ast.NodeVisitor):
        def __init__(self) -> None:
            self.recurring = False
            self.sleeps = False
            self.radio = False

        def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
            return

        def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
            return

        def visit_While(self, node: ast.While) -> None:
            self.recurring = True
            self.generic_visit(node)

        def visit_For(self, node: ast.For) -> None:
            self.recurring = True
            self.generic_visit(node)

        def visit_AsyncFor(self, node: ast.AsyncFor) -> None:
            self.recurring = True
            self.generic_visit(node)

        def visit_Call(self, node: ast.Call) -> None:
            name = _call_name(node)
            if name in {"sleep", "wait", "wait_for"}:
                self.sleeps = True
            for prefix in RADIO_CALL_PREFIXES:
                if name.startswith(prefix) and name.removeprefix(prefix) in terms:
                    self.radio = True
            self.generic_visit(node)

    found: set[str] = set()
    for path, source in sources.items():
        tree = ast.parse(source)
        for function in (
            node
            for node in ast.walk(tree)
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        ):
            summary = FunctionBody()
            for statement in function.body:
                summary.visit(statement)
            if summary.recurring and summary.sleeps and summary.radio:
                found.add(path)
                break
    return found


def _run_frontend(*command: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        list(command),
        cwd=ROOT / "frontend",
        text=True,
        capture_output=True,
        check=False,
    )


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


def test_declared_frontend_authority_owners_match_the_executable_plugin() -> None:
    contract = _contract()
    declared = {
        guard["kind"]: sorted(_declared_baseline(guard)) for guard in contract["guards"]
    }
    assert declared == _plugin_owners()
    for paths in declared.values():
        for path in paths:
            assert (ROOT / "frontend" / path).is_file()


def test_authority_boundary_and_ten_class_adversarial_corpus_pass() -> None:
    completed = _run_frontend(
        "npx",
        "vitest",
        "run",
        "src/__tests__/architecture-boundaries.test.ts",
        "--testNamePattern=radio authority boundary",
        "--reporter=dot",
    )
    assert completed.returncode == 0, completed.stdout + completed.stderr
    assert "38 passed" in completed.stdout


def test_production_frontend_satisfies_structural_boundary() -> None:
    completed = _run_frontend("npm", "run", "lint")
    assert completed.returncode == 0, completed.stdout + completed.stderr


def test_python_recurring_radio_reads_stay_inside_declared_owners() -> None:
    declared = set(_contract()["authority_boundaries"]["python_acquisition_owners"])
    found = _python_recurring_radio_paths()
    assert found <= declared
    forbidden = {
        "src/rigplane/feature/per_control.py": (
            "import asyncio\nasync def run(radio):\n"
            "    while True:\n        await asyncio.sleep(1)\n"
            "        await radio.get_freq()\n"
        )
    }
    assert "src/rigplane/feature/per_control.py" in _python_recurring_radio_paths(
        forbidden
    )
    benign = {
        "src/rigplane/feature/animation.py": (
            "import asyncio\nasync def run(view):\n"
            "    while True:\n        await asyncio.sleep(1)\n        view.draw()\n"
        )
    }
    assert "src/rigplane/feature/animation.py" not in _python_recurring_radio_paths(
        benign
    )


def test_known_owner_inventory_is_exact_and_monotonic() -> None:
    contract = _contract()
    base = _base_contract()
    if base is not None:
        _assert_monotonic(_guard_map(base), _guard_map(contract))

    expanded = copy.deepcopy(contract)
    expanded["guards"][0]["exceptions"].append(
        {"path": "src/semantic/NewWriter.ts", "count": 1, "owner": "MOR-1409"}
    )
    with pytest.raises(AssertionError, match="new owner/debt path"):
        _assert_monotonic(_guard_map(contract), _guard_map(expanded))


def test_design_has_explicit_finite_stop_rule_and_no_obsolete_analyzer() -> None:
    stop_rule = str(_contract()["authority_boundaries"]["stop_rule"])
    assert "new equivalence class" in stop_rule
    assert not (ROOT / "frontend/scripts/ui-radio-semantic-analyzer.ts").exists()
    assert not (ROOT / "frontend/scripts/run-ui-radio-semantic-analyzer.mjs").exists()
    assert not (ROOT / "frontend/tsconfig.analyzer.json").exists()
