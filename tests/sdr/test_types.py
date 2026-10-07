"""Tests for rigplane.sdr.types — IqBlock and SdrConfig.from_mapping (MOR-3151)."""

from __future__ import annotations

import dataclasses

import numpy as np
import pytest

from rigplane.sdr import IqBlock, SdrConfig


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


def test_from_mapping_missing_device_args() -> None:
    with pytest.raises(ValueError, match="device_args"):
        SdrConfig.from_mapping({"sample_rate_hz": 48000})


def test_from_mapping_device_args_not_a_string() -> None:
    with pytest.raises(ValueError, match="device_args"):
        SdrConfig.from_mapping({"device_args": 123})


def test_from_mapping_unknown_field() -> None:
    with pytest.raises(ValueError, match="bogus_field"):
        SdrConfig.from_mapping({"device_args": "driver=rtlsdr", "bogus_field": 1})


def test_from_mapping_bad_sample_rate() -> None:
    with pytest.raises(ValueError, match="sample_rate_hz"):
        SdrConfig.from_mapping({"device_args": "d", "sample_rate_hz": 0})

    with pytest.raises(ValueError, match="sample_rate_hz"):
        SdrConfig.from_mapping({"device_args": "d", "sample_rate_hz": "fast"})

    with pytest.raises(ValueError, match="sample_rate_hz"):
        # bool is an int subclass but is not a sample rate
        SdrConfig.from_mapping({"device_args": "d", "sample_rate_hz": True})


def test_from_mapping_bad_gain() -> None:
    with pytest.raises(ValueError, match="gain_db"):
        SdrConfig.from_mapping({"device_args": "d", "gain_db": "loud"})

    with pytest.raises(ValueError, match="gain_db"):
        SdrConfig.from_mapping({"device_args": "d", "gain_db": True})


def test_from_mapping_gain_none_allowed() -> None:
    config = SdrConfig.from_mapping({"device_args": "d", "gain_db": None})

    assert config.gain_db is None


def test_from_mapping_bad_ppm() -> None:
    with pytest.raises(ValueError, match="ppm"):
        SdrConfig.from_mapping({"device_args": "d", "ppm": "high"})


def test_from_mapping_bad_freq_offset() -> None:
    with pytest.raises(ValueError, match="freq_offset_hz"):
        SdrConfig.from_mapping({"device_args": "d", "freq_offset_hz": 2.5})

    with pytest.raises(ValueError, match="freq_offset_hz"):
        SdrConfig.from_mapping({"device_args": "d", "freq_offset_hz": "8M"})


def test_from_mapping_bad_invert_spectrum() -> None:
    with pytest.raises(ValueError, match="invert_spectrum"):
        SdrConfig.from_mapping({"device_args": "d", "invert_spectrum": 1})


def test_from_mapping_bad_span() -> None:
    with pytest.raises(ValueError, match="span_hz"):
        SdrConfig.from_mapping({"device_args": "d", "span_hz": -1000})

    with pytest.raises(ValueError, match="span_hz"):
        SdrConfig.from_mapping({"device_args": "d", "span_hz": "wide"})


def test_from_mapping_bad_extra_settings() -> None:
    with pytest.raises(ValueError, match="extra_settings"):
        SdrConfig.from_mapping({"device_args": "d", "extra_settings": "direct_samp=2"})

    with pytest.raises(ValueError, match="extra_settings"):
        SdrConfig.from_mapping(
            {"device_args": "d", "extra_settings": {"direct_samp": 2}}
        )


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
