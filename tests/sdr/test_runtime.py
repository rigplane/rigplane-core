"""Tests for the SDR scope runtime config and GPL guard (MOR-3157).

The :class:`~rigplane.sdr.runtime.SdrScopeRuntime` lifecycle is covered
end-to-end by ``tests/web/test_sdr_scope_source.py``.
"""

from __future__ import annotations

import logging

import pytest

from rigplane.sdr.runtime import (
    apply_remote_only_env,
    remote_only_env_updates,
    resolve_sdr_config,
)
from rigplane.sdr.types import SdrConfig


@pytest.mark.parametrize(
    ("cli", "expected"),
    [
        ({}, None),
        ({"device_args": "d"}, SdrConfig("d")),
        ({"device_args": "d", "gain": "auto"}, SdrConfig("d")),  # AGC default
        ({"device_args": "d", "gain": "32.5"}, SdrConfig("d", gain_db=32.5)),
        # Span at exactly 0.6 x rate is allowed; default span stays None.
        (
            {"device_args": "d", "sample_rate_hz": 2_400_000, "span_hz": 1_440_000},
            SdrConfig("d", sample_rate_hz=2_400_000, span_hz=1_440_000),
        ),
        # The remaining --sdr-* flags all reach SdrConfig.
        (
            {
                "device_args": "d",
                "ppm": 1.5,
                "freq_offset_hz": 8_000_000,
                "invert_spectrum": True,
                "settings": ["direct_samp=2", "biastee=true"],
            },
            SdrConfig(
                "d",
                ppm=1.5,
                freq_offset_hz=8_000_000,
                invert_spectrum=True,
                extra_settings={"direct_samp": "2", "biastee": "true"},
            ),
        ),
    ],
)
def test_resolve_builds_config(cli: dict, expected: SdrConfig | None) -> None:
    assert resolve_sdr_config(**cli) == expected


def test_resolve_rejects_bad_values_naming_the_flag() -> None:
    with pytest.raises(ValueError, match="--sdr-gain"):
        resolve_sdr_config(device_args="d", gain="loud")
    # A span wider than 0.6 x the rate cannot fit the guard band.
    with pytest.raises(ValueError, match=r"--sdr-span-hz.*--sdr-sample-rate"):
        resolve_sdr_config(device_args="d", sample_rate_hz=2_400_000, span_hz=1_440_001)
    with pytest.raises(ValueError, match="--sdr-setting"):
        resolve_sdr_config(device_args="d", settings=["direct_samp"])


def test_remote_only_env_updates_matrix() -> None:
    root, module_dir = "/tmp/empty-root", "/usr/lib/x/SoapySDR/modules0.8"
    happy = {"SOAPY_SDR_ROOT": root, "SOAPY_SDR_PLUGIN_PATH": module_dir}
    assert (
        remote_only_env_updates("driver=rtlsdr", {}, module_dir, empty_root=root) == {}
    )
    assert (
        remote_only_env_updates(
            "driver=remote",
            {"SOAPY_SDR_PLUGIN_PATH": "/opt/m"},
            module_dir,
            empty_root=root,
        )
        == {}
    )
    assert remote_only_env_updates("driver=remote", {}, None, empty_root=root) == {}
    assert (
        remote_only_env_updates("driver=remote", {}, module_dir, empty_root=root)
        == happy
    )


def test_apply_remote_only_env(caplog: pytest.LogCaptureFixture) -> None:
    env: dict[str, str] = {}
    assert (
        apply_remote_only_env(
            "driver=remote",
            env=env,
            find_dir=lambda: "/usr/lib/x/SoapySDR/modules0.8",
            create_root=lambda: "/created-root",
        )
        is True
    )
    assert env == {
        "SOAPY_SDR_ROOT": "/created-root",
        "SOAPY_SDR_PLUGIN_PATH": "/usr/lib/x/SoapySDR/modules0.8",
    }
    # Idempotent: an operator-set PLUGIN_PATH is honoured untouched.
    assert apply_remote_only_env("driver=remote", env=env) is True
    # Without the module found: warn and leave the environment untouched.
    fresh: dict[str, str] = {}
    with caplog.at_level(logging.WARNING, logger="rigplane.sdr.runtime"):
        assert (
            apply_remote_only_env("driver=remote", env=fresh, find_dir=lambda: None)
            is False
        )
    assert fresh == {}
    assert "cannot restrict" in caplog.text
