"""Public and queue-level CTCSS centiHz contract for MOR-2129."""

from __future__ import annotations

from dataclasses import FrozenInstanceError, fields
from inspect import signature
from typing import get_type_hints

import pytest

from rigplane.core.radio_protocol import RepeaterControlCapable
from rigplane.runtime._poller_types import SetToneFreq, SetTsqlFreq


@pytest.mark.parametrize("method_name", ["set_tone_freq", "set_tsql_freq"])
def test_repeater_setter_uses_explicit_centihz_name(method_name: str) -> None:
    method = getattr(RepeaterControlCapable, method_name)
    params = signature(method).parameters
    assert list(params) == ["self", "freq_centihz", "receiver"]
    assert get_type_hints(method)["freq_centihz"] is int


@pytest.mark.parametrize("method_name", ["get_tone_freq", "get_tsql_freq"])
def test_repeater_getter_returns_exact_int(method_name: str) -> None:
    method = getattr(RepeaterControlCapable, method_name)
    assert get_type_hints(method)["return"] is int


@pytest.mark.parametrize("command_type", [SetToneFreq, SetTsqlFreq])
def test_queue_command_uses_frozen_slotted_centihz_contract(command_type) -> None:
    command = command_type(freq_centihz=8850, receiver=1)
    assert [(field.name, field.type) for field in fields(command)] == [
        ("freq_centihz", "int"),
        ("receiver", "int"),
    ]
    assert command.freq_centihz == 8850
    assert command.receiver == 1
    assert not hasattr(command, "__dict__")
    with pytest.raises(FrozenInstanceError):
        command.freq_centihz = 10000


@pytest.mark.parametrize("command_type", [SetToneFreq, SetTsqlFreq])
@pytest.mark.parametrize("invalid", [True, 8850.0, "8850"])
def test_queue_command_rejects_non_int_centihz(command_type, invalid: object) -> None:
    with pytest.raises(TypeError, match="freq_centihz must be an int"):
        command_type(freq_centihz=invalid)
