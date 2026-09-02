from __future__ import annotations

import json
import math
from pathlib import Path

import pytest

from rigplane.runtime.managed_tx_config import (
    DEFAULT_MANAGED_TX_TOT_SECONDS,
    ManagedTxTotConfig,
    ManagedTxTotConfigStore,
)


def test_missing_config_defaults_to_180_seconds(tmp_path: Path) -> None:
    store = ManagedTxTotConfigStore(tmp_path / "managed-tx.json")

    assert store.config == ManagedTxTotConfig(DEFAULT_MANAGED_TX_TOT_SECONDS)
    assert store.config.timeout_seconds == 180.0


def test_zero_normalizes_to_disabled_and_persists_only_config(tmp_path: Path) -> None:
    path = tmp_path / "managed-tx.json"
    store = ManagedTxTotConfigStore(path)

    assert store.set_timeout_seconds(0) == ManagedTxTotConfig(None)
    assert store.config == ManagedTxTotConfig(None)
    assert json.loads(path.read_text(encoding="utf-8")) == {
        "software_tot_seconds": None,
        "version": 1,
    }


@pytest.mark.parametrize(
    "invalid",
    [True, "180", -1, -0.5, math.inf, -math.inf, math.nan, object()],
)
def test_invalid_value_does_not_change_active_config(
    tmp_path: Path, invalid: object
) -> None:
    store = ManagedTxTotConfigStore(tmp_path / "managed-tx.json")
    previous = store.set_timeout_seconds(12.5)

    with pytest.raises(ValueError, match="finite-positive"):
        store.set_timeout_seconds(invalid)

    assert store.config == previous


def test_positive_int_and_float_values_are_stored_as_seconds(tmp_path: Path) -> None:
    store = ManagedTxTotConfigStore(tmp_path / "managed-tx.json")

    assert store.set_timeout_seconds(12) == ManagedTxTotConfig(12.0)
    assert store.set_timeout_seconds(12.5) == ManagedTxTotConfig(12.5)


def test_failed_persistence_leaves_live_config_unchanged(tmp_path: Path) -> None:
    def fail_replace(_source: Path, _destination: Path) -> None:
        raise OSError("disk failed")

    store = ManagedTxTotConfigStore(tmp_path / "managed-tx.json", replace=fail_replace)
    previous = store.config

    with pytest.raises(OSError, match="disk failed"):
        store.set_timeout_seconds(45)

    assert store.config == previous


def test_restart_restores_configuration_but_no_tx_intent_or_release_debt(
    tmp_path: Path,
) -> None:
    path = tmp_path / "managed-tx.json"
    first = ManagedTxTotConfigStore(path)
    first.set_timeout_seconds(45)

    restarted = ManagedTxTotConfigStore(path)

    assert restarted.config == ManagedTxTotConfig(45.0)
    assert set(json.loads(path.read_text(encoding="utf-8"))) == {
        "software_tot_seconds",
        "version",
    }


@pytest.mark.parametrize(
    "contents",
    ["not json", '{"software_tot_seconds": true, "version": 1}', "[]"],
)
def test_malformed_durable_config_fails_closed_on_load(
    tmp_path: Path, contents: str
) -> None:
    path = tmp_path / "managed-tx.json"
    path.write_text(contents, encoding="utf-8")

    with pytest.raises(ValueError, match="managed TX TOT config is invalid"):
        ManagedTxTotConfigStore(path)
