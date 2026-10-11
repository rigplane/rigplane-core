"""Pin the consumer-contracts gate ``paths:`` filter to the runner's sources.

``.github/workflows/consumer-contracts-gate.yml`` runs
``scripts/run_consumer_contracts.py`` only when a filtered path changes. The
runner's ``web_surface`` artifact (``_build_web_surface``) exposes
``ControlHandler._COMMANDS`` from ``src/rigplane/web/handlers/control.py``,
which is the literal handler command list unioned with
``frozenset(command_descriptors())`` imported from
``rigplane.core.command_dispatch``. This test therefore requires
``src/rigplane/core/command_dispatch.py`` plus every ``rigplane.*`` module the
runner imports to appear in BOTH the ``push:`` and ``pull_request:`` ``paths:``
blocks.
"""

from __future__ import annotations

import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
WORKFLOW_PATH = REPO_ROOT / ".github" / "workflows" / "consumer-contracts-gate.yml"
RUNNER_PATH = REPO_ROOT / "scripts" / "run_consumer_contracts.py"
CONTROL_HANDLER_PATH = (
    REPO_ROOT / "src" / "rigplane" / "web" / "handlers" / "control.py"
)
COMMAND_DISPATCH_PATH = "src/rigplane/core/command_dispatch.py"
TRIGGERS = ("push", "pull_request")

DISPATCH_SYMBOLS = ("command_descriptors",)


def _workflow_path_blocks(text: str) -> dict[str, set[str]]:
    """Map each workflow trigger to the quoted entries of its ``paths:`` block."""
    lines = text.splitlines()
    blocks: dict[str, set[str]] = {}
    for index, line in enumerate(lines):
        if line.strip() != "paths:":
            continue
        trigger = None
        for previous in reversed(lines[:index]):
            match = re.fullmatch(r"\s{2}(push|pull_request):\s*", previous)
            if match:
                trigger = match.group(1)
                break
        assert trigger is not None, f"paths: entry at line {index + 1} has no trigger"
        entries: set[str] = set()
        for entry_line in lines[index + 1 :]:
            match = re.fullmatch(r'\s*-\s+"([^"]+)"\s*', entry_line)
            if not match:
                break
            entries.add(match.group(1))
        assert trigger not in blocks, f"duplicate paths: block for trigger {trigger!r}"
        blocks[trigger] = entries
    return blocks


def _runner_source_paths(text: str) -> set[str]:
    """Map every ``from rigplane.<module> import`` in the runner to its file."""
    paths: set[str] = set()
    for module in re.findall(
        r"^\s*from\s+(rigplane\.[\w.]+)\s+import\s+", text, re.MULTILINE
    ):
        source = f"src/{module.replace('.', '/')}.py"
        assert (REPO_ROOT / source).is_file(), (
            f"runner imports {module} but {source} does not exist in the repo"
        )
        paths.add(source)
    return paths


def _dispatch_imports(text: str) -> set[str]:
    """Names that ``control.py`` imports from ``core.command_dispatch``."""
    match = re.search(
        r"from\s+\.\.\.core\.command_dispatch\s+import\s+\(([^)]*)\)", text
    )
    assert match is not None, "control.py no longer imports core.command_dispatch"
    return {name.strip() for name in match.group(1).split(",") if name.strip()}


def test_gate_paths_cover_runner_sources_and_command_dispatch() -> None:
    blocks = _workflow_path_blocks(WORKFLOW_PATH.read_text(encoding="utf-8"))
    assert set(blocks) == set(TRIGGERS), (
        f"expected exactly one paths: block each for push and pull_request, "
        f"found blocks for {sorted(blocks)}"
    )

    imported = _dispatch_imports(CONTROL_HANDLER_PATH.read_text(encoding="utf-8"))
    missing_symbols = sorted(set(DISPATCH_SYMBOLS) - imported)
    assert not missing_symbols, (
        f"control.py no longer imports {missing_symbols} from "
        f"core.command_dispatch; update DISPATCH_SYMBOLS and the module "
        f"docstring rationale"
    )

    required = _runner_source_paths(RUNNER_PATH.read_text(encoding="utf-8")) | {
        COMMAND_DISPATCH_PATH
    }
    missing = [
        f"{path} is missing from the {trigger} paths: filter of "
        f"{WORKFLOW_PATH.relative_to(REPO_ROOT).as_posix()}"
        for trigger in TRIGGERS
        for path in sorted(required - blocks[trigger])
    ]
    assert not missing, "; ".join(missing)
