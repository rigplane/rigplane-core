"""SDR config, GPL module guard and environment override tests."""

from __future__ import annotations

import dataclasses
import logging
import sys
from types import SimpleNamespace

import pytest

from rigplane.core.env_config import get_sdr_env_overrides
from rigplane.sdr import FakeIqSource
from rigplane.sdr import runtime as sdr_runtime
from rigplane.sdr.runtime import (
    apply_remote_only_env,
    build_source,
    register_source_kind,
    remote_only_env_updates,
    resolve_sdr_config,
)
from rigplane.sdr.types import SdrConfig, SoapySourceSpec, UdpIqSourceSpec
from rigplane.sdr.xiegu_udp_source import XieguUdpIqSource


def test_build_source_uses_registered_soapy_factory(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delitem(sys.modules, "SoapySDR", raising=False)
    provider = FakeIqSource()
    spec = SoapySourceSpec(SdrConfig(device_args="driver=remote"))
    seen: list[SoapySourceSpec] = []

    def factory(received: SoapySourceSpec) -> FakeIqSource:
        seen.append(received)
        return provider

    monkeypatch.setitem(sdr_runtime._SOURCE_FACTORIES, "soapy", factory)

    assert build_source(spec) is provider
    assert seen == [spec]
    assert "SoapySDR" not in sys.modules


def test_build_source_constructs_udp_source_without_opening_socket() -> None:
    spec = UdpIqSourceSpec(
        host="127.0.0.1",
        radio_port=50002,
        receive_port=50001,
        sample_rate_hz=48_000,
        samples_per_datagram=192,
        center_offset_hz=8000,
        usable_bandwidth_hz=40_000,
        tune_min_hz=100_000,
        tune_max_hz=54_000_000,
    )

    source = build_source(spec)

    assert isinstance(source, XieguUdpIqSource)
    assert source.is_open is False


def test_build_source_rejects_unknown_kind() -> None:
    spec = SimpleNamespace(kind="nope")

    with pytest.raises(ValueError) as excinfo:
        build_source(spec)  # type: ignore[arg-type]

    message = str(excinfo.value)
    assert "kind" in message
    assert "nope" in message
    assert "soapy" in message


def test_register_source_kind_rejects_duplicate() -> None:
    with pytest.raises(ValueError, match="soapy"):
        register_source_kind("soapy", lambda spec: FakeIqSource())


def test_register_and_build_new_source_kind() -> None:
    provider = FakeIqSource()
    kind = "test-only"
    register_source_kind(kind, lambda spec: provider)
    try:
        assert build_source(SimpleNamespace(kind=kind)) is provider  # type: ignore[arg-type]
    finally:
        sdr_runtime._SOURCE_FACTORIES.pop(kind, None)


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
            dict(
                device_args="d",
                ppm=1.5,
                freq_offset_hz=8_000_000,
                invert_spectrum=True,
                settings=["direct_samp=2", "biastee=true"],
            ),
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


# ---------------------------------------------------------------------------
# RIGPLANE_SDR_* env overrides (MOR-3201)
# ---------------------------------------------------------------------------


def _clear_sdr_env(monkeypatch: pytest.MonkeyPatch) -> None:
    import os

    for var in list(os.environ):
        if var.startswith("RIGPLANE_"):
            monkeypatch.delenv(var)


def test_env_overrides_unset_is_all_none(monkeypatch: pytest.MonkeyPatch) -> None:
    _clear_sdr_env(monkeypatch)
    assert dataclasses.astuple(get_sdr_env_overrides()) == (None,) * 9


def test_env_overrides_map_to_sdrconfig_fields(monkeypatch: pytest.MonkeyPatch) -> None:
    _clear_sdr_env(monkeypatch)
    monkeypatch.setenv("RIGPLANE_SCOPE_SOURCE", "sdr")
    monkeypatch.setenv("RIGPLANE_SDR_DEVICE", "driver=rtlsdr")
    monkeypatch.setenv("RIGPLANE_SDR_SAMPLE_RATE", "2400000")
    monkeypatch.setenv("RIGPLANE_SDR_GAIN", "auto")
    monkeypatch.setenv("RIGPLANE_SDR_PPM", "1.5")
    monkeypatch.setenv("RIGPLANE_SDR_OFFSET_HZ", "8000000")
    monkeypatch.setenv("RIGPLANE_SDR_SPAN_HZ", "600000")
    monkeypatch.setenv("RIGPLANE_SDR_INVERT", "1")
    monkeypatch.setenv("RIGPLANE_SDR_SETTINGS", "direct_samp=2,biastee=true")

    # Each variable lands in the matching SdrConfig field through
    # resolve_sdr_config; ``gain=auto`` stays AGC (the SdrConfig default).
    overrides = get_sdr_env_overrides()
    fields = dataclasses.asdict(overrides)
    assert fields.pop("scope_source") == "sdr"
    assert resolve_sdr_config(**fields) == SdrConfig(
        device_args="driver=rtlsdr",
        sample_rate_hz=2_400_000,
        ppm=1.5,
        freq_offset_hz=8_000_000,
        span_hz=600_000,
        invert_spectrum=True,
        extra_settings={"direct_samp": "2", "biastee": "true"},
    )


@pytest.mark.parametrize(
    ("var", "raw"),
    [
        ("RIGPLANE_SCOPE_SOURCE", "matrix"),
        ("RIGPLANE_SDR_SAMPLE_RATE", "fast"),
        ("RIGPLANE_SDR_PPM", "lots"),
        ("RIGPLANE_SDR_OFFSET_HZ", "8MHz"),
        ("RIGPLANE_SDR_SPAN_HZ", "wide"),
        ("RIGPLANE_SDR_INVERT", "maybe"),
        ("RIGPLANE_SDR_GAIN", "loud"),
        ("RIGPLANE_SDR_SETTINGS", "direct_samp"),
    ],
)
def test_env_overrides_bad_values_name_the_variable(
    monkeypatch: pytest.MonkeyPatch, var: str, raw: str
) -> None:
    _clear_sdr_env(monkeypatch)
    monkeypatch.setenv("RIGPLANE_SDR_DEVICE", "d")
    monkeypatch.setenv(var, raw)
    with pytest.raises(ValueError, match=var):
        get_sdr_env_overrides()
