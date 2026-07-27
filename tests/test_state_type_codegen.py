"""Focused contract tests for generated public-state TypeScript."""

from __future__ import annotations

import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[1]
_FRONTEND = _ROOT / "frontend"
_STATE_TYPES = _FRONTEND / "src/lib/types/state.ts"
_TSC = _FRONTEND / "node_modules/.bin/tsc"
_JSON2TS = _FRONTEND / "node_modules/.bin/json2ts"


def test_generated_server_state_requires_tx_target() -> None:
    generated = _STATE_TYPES.read_text()

    assert "txTarget: KnownTxTargetPublic | UnknownTxTargetPublic;" in generated
    assert "txTarget?: KnownTxTargetPublic | UnknownTxTargetPublic;" not in generated


@pytest.mark.skipif(not _TSC.exists(), reason="frontend dev dependencies not installed")
def test_required_tx_target_union_narrows_without_undefined_guard() -> None:
    source = """
import type { ServerStatePublic } from "./src/lib/types/state";

type MissingTxTarget = Omit<ServerStatePublic, "txTarget">;
declare const missing: MissingTxTarget;
// @ts-expect-error txTarget is required by the generated server contract.
const rejected: ServerStatePublic = missing;
void rejected;

declare const state: ServerStatePublic;
if (state.txTarget.status === "known") {
  const receiver: "MAIN" | "SUB" = state.txTarget.receiver;
  void receiver;
} else {
  const reason: "not-observed" | "stale" | "unsupported" | "contradiction" =
    state.txTarget.reason;
  void reason;
}
"""
    with tempfile.NamedTemporaryFile(
        mode="w",
        suffix=".ts",
        prefix=".mor1114-",
        dir=_FRONTEND,
        delete=False,
    ) as handle:
        handle.write(source)
        fixture = Path(handle.name)
    try:
        result = subprocess.run(
            [
                str(_TSC),
                "--strict",
                "--noEmit",
                "--skipLibCheck",
                "--moduleResolution",
                "bundler",
                "--module",
                "esnext",
                "--target",
                "es2022",
                str(fixture),
            ],
            cwd=_FRONTEND,
            capture_output=True,
            text=True,
            check=False,
        )
    finally:
        fixture.unlink(missing_ok=True)

    assert result.returncode == 0, result.stdout + result.stderr


@pytest.mark.skipif(
    not _JSON2TS.exists(), reason="frontend dev dependencies not installed"
)
def test_state_type_generation_is_deterministic_and_current() -> None:
    command = [sys.executable, "scripts/gen_state_types.py"]
    first = subprocess.run(
        [*command, "--stdout"],
        cwd=_ROOT,
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    second = subprocess.run(
        [*command, "--stdout"],
        cwd=_ROOT,
        capture_output=True,
        text=True,
        check=True,
    ).stdout

    assert first == second
    subprocess.run([*command, "--check"], cwd=_ROOT, check=True)
