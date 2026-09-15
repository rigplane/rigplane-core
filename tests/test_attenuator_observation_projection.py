"""Pure provider projection for a bound attenuator observation value."""

from inspect import iscoroutinefunction, signature
from pathlib import Path

import pytest

import rigplane.core.radio_protocol as canonical_radio_protocol
import rigplane.radio_protocol as compatibility_radio_protocol
from rigplane.backends.rigctld_client.radio import RigctldClientRadio
from rigplane.backends.yaesu_cat.radio import YaesuCatRadio
from rigplane.profiles.rig_loader import load_rig
from rigplane.runtime.radio import CoreRadio

RIGS = Path(__file__).parents[1] / "rigs"
PROJECTION_METHOD = "project_attenuator_observation_value"
PROJECTION_PROTOCOL = "AttenuatorObservationProjectable"


def icom(rig_name):
    return CoreRadio("127.0.0.1", profile=load_rig(RIGS / rig_name).to_profile())


def yaesu():
    return YaesuCatRadio("/dev/null", profile=load_rig(RIGS / "ftx1.toml"))


def rigctld():
    return RigctldClientRadio(host="127.0.0.1")


@pytest.mark.parametrize(
    ("provider", "factory", "db", "expected"),
    [
        ("ic7300", lambda: icom("ic7300.toml"), -7, -7),
        ("ic7300", lambda: icom("ic7300.toml"), 0, 0),
        ("ic7300", lambda: icom("ic7300.toml"), 1, 1),
        ("ic7300", lambda: icom("ic7300.toml"), 20, 20),
        ("ic7300", lambda: icom("ic7300.toml"), 48, 48),
        ("ic7610", lambda: icom("ic7610.toml"), -7, -7),
        ("ic7610", lambda: icom("ic7610.toml"), 0, 0),
        ("ic7610", lambda: icom("ic7610.toml"), 1, 1),
        ("ic7610", lambda: icom("ic7610.toml"), 20, 20),
        ("ic7610", lambda: icom("ic7610.toml"), 48, 48),
        ("ftx1", yaesu, -7, 0),
        ("ftx1", yaesu, 0, 0),
        ("ftx1", yaesu, 1, 1),
        ("ftx1", yaesu, 20, 1),
        ("ftx1", yaesu, 48, 1),
        ("rigctld", rigctld, -7, -7),
        ("rigctld", rigctld, 0, 0),
        ("rigctld", rigctld, 1, 1),
        ("rigctld", rigctld, 20, 20),
        ("rigctld", rigctld, 48, 48),
    ],
    ids=(
        "ic7300-minus-seven",
        "ic7300-zero",
        "ic7300-one",
        "ic7300-twenty",
        "ic7300-forty-eight",
        "ic7610-minus-seven",
        "ic7610-zero",
        "ic7610-one",
        "ic7610-twenty",
        "ic7610-forty-eight",
        "ftx1-minus-seven",
        "ftx1-zero",
        "ftx1-one",
        "ftx1-twenty",
        "ftx1-forty-eight",
        "rigctld-minus-seven",
        "rigctld-zero",
        "rigctld-one",
        "rigctld-twenty",
        "rigctld-forty-eight",
    ),
)
def test_attenuator_projection_preserves_provider_representation(
    provider, factory, db, expected
):
    result = getattr(factory(), PROJECTION_METHOD)(db)
    assert type(result) is int
    assert result == expected


@pytest.mark.parametrize(
    ("provider", "provider_class", "expected"),
    [
        ("icom", CoreRadio, 20),
        ("ftx1", YaesuCatRadio, 1),
        ("rigctld", RigctldClientRadio, 20),
    ],
    ids=("icom", "ftx1", "rigctld"),
)
def test_attenuator_projection_is_synchronous_and_stateless(
    provider, provider_class, expected
):
    method = getattr(provider_class, PROJECTION_METHOD)
    assert not iscoroutinefunction(method)
    assert tuple(signature(method).parameters) == ("self", "db")

    class PoisonSelf:
        def __getattribute__(self, name):
            raise AssertionError(f"projection read state through self: {name}")

        def __setattr__(self, name, value):
            raise AssertionError(f"projection mutated self: {name}")

    result = method(PoisonSelf(), 20)
    assert type(result) is int
    assert result == expected


def test_attenuator_projection_protocol_and_compatibility_export():
    assert PROJECTION_PROTOCOL in canonical_radio_protocol.__all__
    protocol = getattr(canonical_radio_protocol, PROJECTION_PROTOCOL)
    assert getattr(compatibility_radio_protocol, PROJECTION_PROTOCOL) is protocol
    assert isinstance(icom("ic7300.toml"), protocol)
    assert isinstance(icom("ic7610.toml"), protocol)
    assert isinstance(yaesu(), protocol)
    assert isinstance(rigctld(), protocol)

    class MinimalProjection:
        def project_attenuator_observation_value(self, db: int) -> int:
            return db

    assert isinstance(MinimalProjection(), protocol)
    assert not isinstance(object(), protocol)


@pytest.mark.parametrize(
    ("name", "protocol"),
    [
        ("Radio", canonical_radio_protocol.Radio),
        ("AntennaControlCapable", canonical_radio_protocol.AntennaControlCapable),
        ("AdvancedControlCapable", canonical_radio_protocol.AdvancedControlCapable),
    ],
    ids=("radio", "antenna", "advanced"),
)
def test_projection_does_not_expand_existing_protocols(name, protocol):
    assert PROJECTION_METHOD not in dir(protocol), name
