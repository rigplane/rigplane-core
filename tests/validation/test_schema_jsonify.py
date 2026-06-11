"""Regression tests for JSON-safe coercion of check evidence (MOR-663).

The live IC-7610 matrix crashed when writing its artifact because a
``ScopeFixedEdge`` dataclass landed in a check's ``evidence`` dict and
``json.dumps(artifact.to_dict())`` had no way to serialize it. These tests
pin the recursive coercion in ``CheckResult.to_dict`` (and confirm the whole
``ValidationArtifact`` round-trips through ``json.dumps``), while guarding
that primitive-only evidence keeps its exact prior shape so goldens do not
shift.
"""

from __future__ import annotations

import json

from rigplane.core.types import AgcMode, ScopeFixedEdge
from rigplane.validation.schema import (
    CapabilityDeclaration,
    CheckResult,
    CheckStatus,
    LevelResult,
    OperatorSafetyBlock,
    RadioTarget,
    TransportInfo,
    ValidationArtifact,
    ValidationLevel,
)


def _make_check(evidence: dict[str, object]) -> CheckResult:
    return CheckResult(
        check_id="scope.fixed_edge.read",
        capability="scope",
        level=ValidationLevel.CAPABILITY_MATRIX,
        status=CheckStatus.PASS,
        declaration=CapabilityDeclaration.SUPPORTED,
        summary="scope fixed-edge read",
        evidence=evidence,
    )


def test_to_dict_coerces_dataclass_enum_and_bytes() -> None:
    edge = ScopeFixedEdge(range_index=1, edge=2, start_hz=14_000_000, end_hz=14_350_000)
    check = _make_check(
        {
            "edge": edge,
            "mode": AgcMode.FAST,
            "raw": b"\xab\xcd",
        }
    )

    payload = check.to_dict()
    evidence = payload["evidence"]
    assert isinstance(evidence, dict)

    # dataclass -> plain dict of its fields (exact keys/values).
    assert evidence["edge"] == {
        "range_index": 1,
        "edge": 2,
        "start_hz": 14_000_000,
        "end_hz": 14_350_000,
    }
    # Enum -> its .value.
    assert evidence["mode"] == AgcMode.FAST.value
    # bytes -> hex string.
    assert evidence["raw"] == b"\xab\xcd".hex()

    # The whole payload must be JSON-serializable without a default= handler.
    json.dumps(payload)


def test_to_dict_coerces_nested_containers() -> None:
    edge = ScopeFixedEdge(range_index=0, edge=1, start_hz=1, end_hz=2)
    check = _make_check(
        {
            "edges": [edge, {"nested_bytes": b"\x01"}],
            "tags": {"a", "b"},
            "pair": (1, AgcMode.SLOW),
        }
    )

    evidence = check.to_dict()["evidence"]
    assert isinstance(evidence, dict)

    assert evidence["edges"][0] == {
        "range_index": 0,
        "edge": 1,
        "start_hz": 1,
        "end_hz": 2,
    }
    assert evidence["edges"][1] == {"nested_bytes": b"\x01".hex()}
    # set -> list; order is not guaranteed, so compare as a set.
    assert isinstance(evidence["tags"], list)
    assert set(evidence["tags"]) == {"a", "b"}
    # tuple -> list, with elements recursed (Enum -> value).
    assert evidence["pair"] == [1, AgcMode.SLOW.value]

    json.dumps(check.to_dict())


def test_artifact_with_dataclass_evidence_is_json_serializable() -> None:
    edge = ScopeFixedEdge(range_index=1, edge=2, start_hz=10, end_hz=20)
    check = _make_check({"edge": edge})
    artifact = ValidationArtifact(
        radio=RadioTarget(model="IC-7610", profile_id="ic7610"),
        transport=TransportInfo(backend="udp", host="192.168.55.40", port=50001),
        safety=OperatorSafetyBlock(),
        levels=[LevelResult(level=ValidationLevel.CAPABILITY_MATRIX, checks=[check])],
        core_version="2.9.0",
    )

    # The live-run crash was here: this must not raise.
    text = json.dumps(artifact.to_dict())
    assert "start_hz" in text


def test_primitive_evidence_shape_is_unchanged() -> None:
    """Primitive-only evidence must serialize identically to a plain dict copy.

    Guards against goldens shifting: only non-primitive values change shape.
    """
    evidence: dict[str, object] = {
        "freq_hz": 14_074_000,
        "mode": "USB",
        "ok": True,
        "ratio": 1.5,
        "missing": None,
        "list": [1, 2, 3],
        "nested": {"k": "v", "n": 7},
    }
    check = _make_check(dict(evidence))

    assert check.to_dict()["evidence"] == evidence
    # Byte-identical JSON to a direct dump of the original primitive evidence.
    assert json.dumps(check.to_dict()["evidence"], sort_keys=True) == json.dumps(
        evidence, sort_keys=True
    )
