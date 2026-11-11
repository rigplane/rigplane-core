"""Tests for rigplane.sdr.types — IqBlock and SdrConfig (MOR-3151)."""

from __future__ import annotations

import dataclasses
from collections.abc import Mapping

import numpy as np
import pytest

from rigplane.sdr import (
    IqBlock,
    IqSourceFacts,
    SdrConfig,
    SoapySourceSpec,
    UdpIqSourceSpec,
)


def test_iq_source_facts_defaults() -> None:
    facts = IqSourceFacts()

    assert facts.tuning == "independent"
    assert facts.center_offset_hz == 0
    assert facts.usable_bandwidth_hz is None


@pytest.mark.parametrize(
    ("kwargs", "field"),
    [
        ({"tuning": "independent", "center_offset_hz": 8000}, "center_offset_hz"),
        ({"usable_bandwidth_hz": 0}, "usable_bandwidth_hz"),
        ({"usable_bandwidth_hz": -1}, "usable_bandwidth_hz"),
        ({"tuning": "other"}, "tuning"),
        ({"center_offset_hz": True}, "center_offset_hz"),
        ({"usable_bandwidth_hz": True}, "usable_bandwidth_hz"),
    ],
)
def test_iq_source_facts_rejects_bad_values(
    kwargs: dict[str, object], field: str
) -> None:
    with pytest.raises(ValueError, match=field):
        IqSourceFacts(**kwargs)  # type: ignore[arg-type]


def test_soapy_source_spec_kind() -> None:
    spec = SoapySourceSpec(SdrConfig(device_args="driver=remote"))

    assert spec.kind == "soapy"


def _udp_spec_kwargs() -> dict[str, object]:
    return {
        "host": "127.0.0.1",
        "radio_port": 50002,
        "receive_port": 50001,
        "sample_rate_hz": 48_000,
        "samples_per_datagram": 192,
        "center_offset_hz": 8000,
        "usable_bandwidth_hz": 40_000,
        "tune_min_hz": 100_000,
        "tune_max_hz": 54_000_000,
    }


def test_udp_source_spec_kind_and_optional_bandwidth() -> None:
    values = _udp_spec_kwargs()
    values["usable_bandwidth_hz"] = None

    spec = UdpIqSourceSpec(**values)  # type: ignore[arg-type]

    assert spec.kind == "xiegu_udp"
    assert spec.usable_bandwidth_hz is None


def test_udp_source_spec_accepts_largest_ipv4_datagram() -> None:
    values = _udp_spec_kwargs()
    values["samples_per_datagram"] = 16_376

    spec = UdpIqSourceSpec(**values)  # type: ignore[arg-type]

    assert spec.samples_per_datagram * 4 == 65_504


@pytest.mark.parametrize("field", tuple(_udp_spec_kwargs())[1:])
def test_udp_source_spec_rejects_bool_for_integer_fields(field: str) -> None:
    values = _udp_spec_kwargs()
    values[field] = True

    with pytest.raises(ValueError, match=field):
        UdpIqSourceSpec(**values)  # type: ignore[arg-type]


@pytest.mark.parametrize(
    ("override", "field"),
    [
        ({"host": ""}, "host"),
        ({"host": 123}, "host"),
        ({"radio_port": 0}, "radio_port"),
        ({"radio_port": 65_536}, "radio_port"),
        ({"receive_port": 0}, "receive_port"),
        ({"receive_port": 65_536}, "receive_port"),
        ({"sample_rate_hz": 0}, "sample_rate_hz"),
        ({"sample_rate_hz": 48_000.0}, "sample_rate_hz"),
        ({"samples_per_datagram": 0}, "samples_per_datagram"),
        ({"samples_per_datagram": 16_377}, "samples_per_datagram"),
        ({"tune_min_hz": 54_000_000}, "tune_min_hz"),
        ({"tune_max_hz": 100_000}, "tune_max_hz"),
        ({"usable_bandwidth_hz": 0}, "usable_bandwidth_hz"),
        ({"usable_bandwidth_hz": 48_001}, "usable_bandwidth_hz"),
    ],
)
def test_udp_source_spec_rejects_bad_values(
    override: dict[str, object], field: str
) -> None:
    values = _udp_spec_kwargs()
    values.update(override)

    with pytest.raises(ValueError, match=field):
        UdpIqSourceSpec(**values)  # type: ignore[arg-type]


def test_sdr_config_defaults() -> None:
    config = SdrConfig(device_args="driver=rtlsdr")

    assert config.device_args == "driver=rtlsdr"
    assert config.sample_rate_hz == 2_400_000
    assert config.gain_db is None
    assert config.ppm == 0.0
    assert config.freq_offset_hz == 0
    assert config.invert_spectrum is False
    assert config.span_hz is None
    assert config.extra_settings == {}


def test_sdr_config_from_mapping_minimal() -> None:
    config = SdrConfig.from_mapping({"device_args": "driver=rtlsdr"})

    assert config == SdrConfig(device_args="driver=rtlsdr")


def test_sdr_config_from_mapping_full() -> None:
    config = SdrConfig.from_mapping(
        {
            "device_args": "driver=remote,remote=127.0.0.1,remote:driver=rtlsdr",
            "sample_rate_hz": 1_024_000,
            "gain_db": 30,
            "ppm": 1.5,
            "freq_offset_hz": -8_000_000,
            "invert_spectrum": True,
            "span_hz": 192_000,
            "extra_settings": {"direct_samp": "2"},
        }
    )

    assert config.device_args == ("driver=remote,remote=127.0.0.1,remote:driver=rtlsdr")
    assert config.sample_rate_hz == 1_024_000
    assert config.gain_db == 30.0
    assert config.ppm == 1.5
    assert config.freq_offset_hz == -8_000_000
    assert config.invert_spectrum is True
    assert config.span_hz == 192_000
    assert config.extra_settings == {"direct_samp": "2"}


def test_sdr_config_frozen() -> None:
    config = SdrConfig(device_args="driver=rtlsdr")

    with pytest.raises(dataclasses.FrozenInstanceError):
        config.sample_rate_hz = 48000  # type: ignore[misc]


def test_sdr_config_extra_settings_read_only() -> None:
    config = SdrConfig(device_args="driver=rtlsdr", extra_settings={"a": "1"})
    default = SdrConfig(device_args="driver=rtlsdr")

    with pytest.raises(TypeError):
        config.extra_settings["a"] = "2"  # type: ignore[index]

    with pytest.raises(TypeError):
        default.extra_settings["a"] = "2"  # type: ignore[index]


def test_sdr_config_hashable() -> None:
    a = SdrConfig(device_args="d", extra_settings={"a": "1"})
    b = SdrConfig.from_mapping({"device_args": "d", "extra_settings": {"a": "1"}})

    assert a == b
    assert hash(a) == hash(b)
    assert len({a, b}) == 1


@pytest.mark.parametrize(
    ("mapping", "field"),
    [
        ({"sample_rate_hz": 48000}, "device_args"),
        ({"device_args": 123}, "device_args"),
        ({"device_args": "d", "bogus_field": 1}, "bogus_field"),
        ({"device_args": "d", "sample_rate_hz": 0}, "sample_rate_hz"),
        ({"device_args": "d", "sample_rate_hz": "fast"}, "sample_rate_hz"),
        # bool is an int subclass but is not a sample rate
        ({"device_args": "d", "sample_rate_hz": True}, "sample_rate_hz"),
        ({"device_args": "d", "gain_db": "loud"}, "gain_db"),
        ({"device_args": "d", "gain_db": True}, "gain_db"),
        ({"device_args": "d", "ppm": "high"}, "ppm"),
        ({"device_args": "d", "freq_offset_hz": 2.5}, "freq_offset_hz"),
        ({"device_args": "d", "freq_offset_hz": "8M"}, "freq_offset_hz"),
        ({"device_args": "d", "invert_spectrum": 1}, "invert_spectrum"),
        ({"device_args": "d", "span_hz": -1000}, "span_hz"),
        ({"device_args": "d", "span_hz": "wide"}, "span_hz"),
        ({"device_args": "d", "extra_settings": "direct_samp=2"}, "extra_settings"),
        ({"device_args": "d", "extra_settings": {"direct_samp": 2}}, "extra_settings"),
    ],
)
def test_from_mapping_rejects_bad_values(
    mapping: Mapping[str, object], field: str
) -> None:
    with pytest.raises(ValueError, match=field):
        SdrConfig.from_mapping(mapping)


def test_from_mapping_gain_none_allowed() -> None:
    config = SdrConfig.from_mapping({"device_args": "d", "gain_db": None})

    assert config.gain_db is None


def test_iq_block_fields_and_default_overflow() -> None:
    samples = np.zeros(8, dtype=np.complex64)

    block = IqBlock(
        samples=samples,
        center_freq_hz=14_074_000,
        sample_rate_hz=48_000,
        timestamp_s=123.456,
    )

    assert block.samples is samples
    assert block.center_freq_hz == 14_074_000
    assert block.sample_rate_hz == 48_000
    assert block.timestamp_s == 123.456
    assert block.overflow is False


def test_iq_block_frozen() -> None:
    block = IqBlock(
        samples=np.zeros(8, dtype=np.complex64),
        center_freq_hz=0,
        sample_rate_hz=48_000,
        timestamp_s=0.0,
    )

    with pytest.raises(dataclasses.FrozenInstanceError):
        block.center_freq_hz = 1  # type: ignore[misc]
