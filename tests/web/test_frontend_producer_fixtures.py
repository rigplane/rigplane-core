"""The committed frontend producer fixtures are current against the producers.

Three vitest suites import committed JSON documents built by Core's own
producers instead of spawning ``uv run python`` at test time (MOR-4214: on a
saturated 4-CPU slot the interpreter start alone outlasted the subprocess
wall budget — run 44869233407 failed all three suites with ``spawnSync uv
ETIMEDOUT``):

* ``frontend/src/lib/transport/__tests__/fixtures/liveness-ws-trace.json`` —
  the HTTP-prewarmed WS subscription baselines captured by
  ``tests/liveness_ws_trace.py`` through the real web server (its
  wall/monotonic-derived fields are pinned to sentinels; the suite asserts
  only the patched-clock liveness ages).
* ``frontend/src/components-v2/layout/__tests__/fixtures/eibi-identify-replies.json``
  — the ``EiBiStation`` serializer's ``to_dict()`` wrapped as the
  ``/api/v1/eibi/identify`` route wraps it.
* ``frontend/src/lib/local-extensions/__tests__/fixtures/host-api-v3-documents.json``
  — the station reply plus the web server's ``build_public_state``
  ``main.freqHz`` field status.

``scripts/gen_frontend_producer_fixtures.py`` writes all three. A producer
change fails these tests until the fixtures are regenerated and the diff
reviewed:

    uv run python scripts/gen_frontend_producer_fixtures.py
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from typing import Any

import pytest

_ROOT = Path(__file__).resolve().parents[2]
_GENERATOR_PATH = _ROOT / "scripts" / "gen_frontend_producer_fixtures.py"


def _load_generator() -> Any:
    spec = importlib.util.spec_from_file_location(
        "gen_frontend_producer_fixtures", _GENERATOR_PATH
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


_GENERATOR = _load_generator()
_RENDERED = _GENERATOR.render_fixtures()


@pytest.mark.parametrize("name", sorted(_RENDERED))
def test_frontend_producer_fixture_is_current(name: str) -> None:
    path = _GENERATOR._FIXTURES[name]
    existing = path.read_text(encoding="utf-8") if path.exists() else None
    assert existing == _RENDERED[name], (
        f"{path.relative_to(_ROOT)} is stale. Run `uv run python "
        "scripts/gen_frontend_producer_fixtures.py` and commit the result."
    )
