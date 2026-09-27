"""Control-domain contract vectors: Python truth vs the committed fixture (MOR-2477).

``scripts/gen_control_domain_vectors.py`` regenerates
``tests/fixtures/control-domain-vectors.json`` from every normalized
``[controls.*]`` domain in ``rigs/*.toml`` through
:mod:`rigplane.profiles.control_domain`. The frontend vitest suite
``frontend/src/lib/radio/__tests__/control-domain.vectors.test.ts`` asserts
``control-domain.ts`` produces identical results from the same fixture, so
the two halves of the mechanism cannot drift apart silently. This test fails
when the committed fixture drifts from the Python generator, which turns a
backend rounding or tie-rule change red in CI until the fixture is
deliberately regenerated and reviewed.
"""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
GENERATOR_PATH = REPO_ROOT / "scripts" / "gen_control_domain_vectors.py"


def _load_generator():
    spec = importlib.util.spec_from_file_location(
        "gen_control_domain_vectors", GENERATOR_PATH
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_control_domain_vectors_fixture_is_fresh() -> None:
    generator = _load_generator()
    committed = generator.FIXTURE_PATH.read_text(encoding="utf-8")
    assert committed == generator.fixture_document(), (
        "tests/fixtures/control-domain-vectors.json is stale. Run "
        "`uv run python scripts/gen_control_domain_vectors.py` and commit "
        "the result."
    )


def test_control_domain_vectors_fixture_covers_real_domains() -> None:
    document = json.loads(
        (REPO_ROOT / "tests" / "fixtures" / "control-domain-vectors.json").read_text(
            encoding="utf-8"
        )
    )
    entries = document["domains"]
    assert entries, "no normalized control domains found under rigs/*.toml"
    for entry in entries:
        assert entry["decode"], (
            f"{entry['rig']}/{entry['control']} has no decode vectors"
        )
        assert entry["encode"], (
            f"{entry['rig']}/{entry['control']} has no encode vectors"
        )
        assert entry["quantize"], (
            f"{entry['rig']}/{entry['control']} has no quantize vectors"
        )
