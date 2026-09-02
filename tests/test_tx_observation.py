"""Unit contracts for the canonical transmit-observation vocabulary."""

import re
import subprocess
from dataclasses import MISSING, FrozenInstanceError, fields
from importlib import import_module, util
from pathlib import Path
from typing import get_type_hints

import pytest

from rigplane.core.tx_observation import (
    RADIO_READBACK_SOURCES,
    TX_READ_DEADLINE_SECONDS,
    TxStateReading,
)

_REPO_ROOT = Path(__file__).resolve().parents[1]
_LIVE_ROOTS = ("src/rigplane", "tests", "rigs")


def _retired_reference_patterns() -> tuple[bytes, ...]:
    retired_module = "tx_" + "authority"
    return (
        f"core/{retired_module}.py".encode(),
        f"core.{retired_module}".encode(),
        ("Transmit" + "Authority").encode(),
    )


def _retired_references(content: bytes) -> tuple[bytes, ...]:
    return tuple(
        pattern for pattern in _retired_reference_patterns() if pattern in content
    )


def _tracked_live_paths() -> tuple[Path, ...]:
    result = subprocess.run(
        ("git", "ls-files", "-z", "--", *_LIVE_ROOTS),
        cwd=_REPO_ROOT,
        check=True,
        capture_output=True,
    )
    return tuple(
        _REPO_ROOT / raw_path.decode()
        for raw_path in result.stdout.split(b"\0")
        if raw_path
    )


def test_live_tree_has_no_retired_transmit_authority_references() -> None:
    violations = [
        f"{path.relative_to(_REPO_ROOT)}: {pattern.decode()}"
        for path in _tracked_live_paths()
        for pattern in _retired_references(path.read_bytes())
    ]
    assert violations == [], "retired TX authority references found:\n" + "\n".join(
        violations
    )


def test_retired_reference_guard_allows_observation_and_generic_authority() -> None:
    valid_references = b"authority tx_observation rigplane.core.tx_observation"
    assert _retired_references(valid_references) == ()


def test_retired_tx_authority_module_does_not_resolve() -> None:
    retired_module = ".".join(("rigplane", "core", "tx_" + "authority"))
    assert util.find_spec(retired_module) is None
    with pytest.raises(ModuleNotFoundError, match=re.escape(retired_module)):
        import_module(retired_module)


def test_tx_observation_contract_is_pinned() -> None:
    reading_fields = fields(TxStateReading)
    expected_fields = "value attributed source verified_readback failure".split()
    assert [field.name for field in reading_fields] == expected_fields
    assert get_type_hints(TxStateReading) == {
        "value": bool | None,
        "attributed": str | None,
        "source": str | None,
        "verified_readback": bool,
        "failure": str | None,
    }
    assert [field.default for field in reading_fields] == [
        MISSING,
        None,
        None,
        False,
        None,
    ]

    reading = TxStateReading(value=False)
    assert not hasattr(reading, "__dict__")
    with pytest.raises(FrozenInstanceError):
        reading.value = True  # type: ignore[misc]

    assert TX_READ_DEADLINE_SECONDS == 0.3
    assert RADIO_READBACK_SOURCES == frozenset(
        {"poll_response", "civ_unsolicited", "hamlib_response", "yaesu_poll_response"}
    )
