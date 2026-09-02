"""Unit contracts for the canonical transmit-observation vocabulary."""

from dataclasses import MISSING, FrozenInstanceError, fields
from importlib import import_module, util
from typing import get_type_hints

import pytest

from rigplane.core.tx_observation import (
    RADIO_READBACK_SOURCES,
    TX_READ_DEADLINE_SECONDS,
    TxStateReading,
)


def test_retired_tx_authority_module_does_not_resolve() -> None:
    assert util.find_spec("rigplane.core.tx_authority") is None
    with pytest.raises(ModuleNotFoundError, match="rigplane.core.tx_authority"):
        import_module("rigplane.core.tx_authority")


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
