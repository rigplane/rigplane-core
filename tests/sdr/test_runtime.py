"""Tests for the SDR scope runtime: config, GPL guard and the MOR-3201
read-only status surface + env overrides; the runtime lifecycle is
covered end-to-end by ``tests/web/test_sdr_scope_source.py``.
"""

from __future__ import annotations

import asyncio
import dataclasses
import logging

import pytest

from rigplane.core.env_config import get_sdr_env_overrides
from rigplane.sdr.controller import TX_HOLD_S
from rigplane.sdr.fake import FakeIqSource
from rigplane.sdr.iq_scope import IqFftScope
from rigplane.sdr.runtime import (
    SdrScopeRuntime,
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
# Read-only status surface (MOR-3201)
# ---------------------------------------------------------------------------

_VFO = 14_074_000


class _FakeClock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


class _FailingOpenSource(FakeIqSource):
    def open(self) -> None:
        raise RuntimeError("device busy")


def _make_runtime(
    source: FakeIqSource, clock: _FakeClock, *, stale_s: float = 2.0
) -> SdrScopeRuntime:
    return SdrScopeRuntime(
        SdrConfig("fake", sample_rate_hz=2_400_000),
        source_factory=lambda _config: source,
        scope_factory=lambda: IqFftScope(start_worker=False),
        clock=clock,
        frame_stale_s=stale_s,
    )


async def _start_streaming(runtime: SdrScopeRuntime, fake: FakeIqSource) -> None:
    """Start the runtime and drive it to the streaming state."""
    runtime.start(lambda _frame: None, freq_hz=_VFO)
    fake.pump(1)
    runtime._scope.process_pending()  # type: ignore[union-attr]
    await _drain_loop()
    assert runtime.state == "streaming"


async def _drain_loop() -> None:
    for _ in range(5):
        await asyncio.sleep(0)


def test_status_disabled_before_start() -> None:
    runtime = _make_runtime(FakeIqSource(), _FakeClock())
    assert runtime.state == "disabled"
    assert runtime.last_error is None
    assert runtime.overflow_count == 0
    assert runtime.tx_frozen is False
    assert runtime.span_hz == 0


async def test_status_starting_then_streaming() -> None:
    fake = FakeIqSource()
    runtime = _make_runtime(fake, _FakeClock())
    runtime.start(lambda _frame: None, freq_hz=_VFO)
    assert runtime.started
    assert runtime.state == "starting"  # started, no frame delivered yet
    assert runtime.span_hz == 2_400_000 // 4  # controller default span

    fake.pump(1)
    runtime._scope.process_pending()  # type: ignore[union-attr]
    await _drain_loop()
    assert runtime.state == "streaming"
    runtime.stop()
    assert runtime.state == "disabled"


async def test_status_reconnecting_when_frames_go_stale() -> None:
    fake = FakeIqSource()
    clock = _FakeClock()
    runtime = _make_runtime(fake, clock)
    await _start_streaming(runtime, fake)
    clock.advance(2.1)  # beyond frame_stale_s
    runtime.tick()
    assert runtime.state == "reconnecting"
    # Frames return: back to streaming after the next delivery.
    fake.pump(1)
    runtime._scope.process_pending()  # type: ignore[union-attr]
    await _drain_loop()
    runtime.tick()
    assert runtime.state == "streaming"


async def test_status_error_on_factory_failure() -> None:
    def _failing_factory(_config: SdrConfig) -> FakeIqSource:
        raise RuntimeError("no dev")

    runtime = SdrScopeRuntime(
        SdrConfig("fake"), source_factory=_failing_factory, clock=_FakeClock()
    )
    with pytest.raises(RuntimeError, match="no dev"):
        runtime.start(lambda _frame: None)
    assert runtime.started is False and runtime.state == "error"
    assert runtime.last_error == "no dev"
    runtime.stop()
    assert runtime.state == "disabled"  # a deliberate stop clears the error


async def test_status_error_on_open_failure_closes_pipeline() -> None:
    runtime = _make_runtime(_FailingOpenSource(), _FakeClock())
    with pytest.raises(RuntimeError, match="device busy"):
        runtime.start(lambda _frame: None, freq_hz=_VFO)
    assert runtime.started is False  # the half-built pipeline was closed
    assert runtime.state == "error" and runtime.last_error == "device busy"


async def test_status_tx_frozen_follows_controller_hold() -> None:
    fake = FakeIqSource()
    clock = _FakeClock()
    runtime = _make_runtime(fake, clock)
    await _start_streaming(runtime, fake)

    runtime.on_radio_state(freq_hz=None, tx_active=True)
    assert runtime.tx_frozen is True

    # Falling edge releases only after TX_HOLD_S (controller rule).
    runtime.on_radio_state(freq_hz=None, tx_active=False)
    clock.advance(TX_HOLD_S / 2)
    runtime.tick()
    assert runtime.tx_frozen is True
    clock.advance(TX_HOLD_S)
    runtime.tick()
    assert runtime.tx_frozen is False


async def test_status_overflow_count_counts_flagged_blocks() -> None:
    fake = FakeIqSource()
    runtime = _make_runtime(fake, _FakeClock())
    await _start_streaming(runtime, fake)
    assert runtime.overflow_count == 0

    fake.set_overflow(True)
    fake.pump(1)
    assert runtime.overflow_count == 1
    # Centre-0 blocks are dropped from the FFT feed, but their overflow
    # flag still counts (samples were lost regardless).
    fake.set_center_freq(0)
    fake.pump(1)
    assert runtime.overflow_count == 2
    # A fresh start resets the counter.
    runtime.stop()
    fake.set_overflow(False)
    await _start_streaming(runtime, fake)
    assert runtime.overflow_count == 0


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
