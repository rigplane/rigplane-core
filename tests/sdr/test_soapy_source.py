"""Tests for rigplane.sdr.soapy_source — SoapyIqSource (MOR-3155).

Fake ``SoapySDR`` module in ``sys.modules``; no install or hardware.
"""

from __future__ import annotations

import importlib
import logging
import sys
import threading
import time
import types
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any, Callable

import numpy as np
import pytest

from rigplane.sdr import IqBlock, IqSource
from rigplane.sdr.soapy_source import SoapyIqSource
from rigplane.sdr.types import SdrConfig

_FIXED_CLOCK_S = 4242.0


class _FakeRange:
    def __init__(self, low: float, high: float) -> None:
        self._low = low
        self._high = high

    def minimum(self) -> float:
        return self._low

    def maximum(self) -> float:
        return self._high


class _FakeStreamResult:
    def __init__(self, ret: int) -> None:
        self.ret = ret
        self.flags = 0
        self.timeNs = 0


class _FakeDevice:
    """Scriptable SoapySDR.Device double; records every call."""

    def __init__(self, harness: _FakeSoapyHarness, args: dict[str, str]) -> None:
        self._harness = harness
        self.args = args
        self.stream = object()
        attempt = harness.begin_device_attempt()
        harness.record("Device", args)
        if attempt in harness.failing_device_attempts:
            raise RuntimeError(f"no device on attempt {attempt}")
        with harness.lock:
            harness.devices.append(self)

    def _record(self, name: str, *call_args: Any) -> None:
        self._harness.record(name, *call_args)

    def writeSetting(self, key: str, value: str) -> None:
        self._record("writeSetting", key, value)

    def setSampleRate(self, direction: int, channel: int, rate: int) -> None:
        self._record("setSampleRate", direction, channel, rate)

    def setGainMode(self, direction: int, channel: int, automatic: bool) -> None:
        self._record("setGainMode", direction, channel, automatic)

    def setGain(self, direction: int, channel: int, gain: float) -> None:
        self._record("setGain", direction, channel, gain)

    def setFrequencyCorrection(self, direction: int, channel: int, ppm: float) -> None:
        self._record("setFrequencyCorrection", direction, channel, ppm)
        if not self._harness.ppm_supported:
            raise RuntimeError("setFrequencyCorrection not supported")

    def setFrequency(self, direction: int, channel: int, frequency: float) -> None:
        self._record("setFrequency", direction, channel, frequency)

    def setupStream(self, direction: int, fmt: int, channels: Any, kwargs: Any) -> Any:
        self._record("setupStream", direction, fmt, channels, kwargs)
        return self.stream

    def activateStream(self, stream: Any) -> None:
        self._record("activateStream", stream)

    def deactivateStream(self, stream: Any) -> None:
        self._record("deactivateStream", stream)

    def closeStream(self, stream: Any) -> None:
        self._record("closeStream", stream)

    def getFrequencyRange(self, direction: int, channel: int) -> list[_FakeRange]:
        self._record("getFrequencyRange", direction, channel)
        return [_FakeRange(low, high) for low, high in self._harness.frequency_ranges]

    def readStream(
        self, stream: Any, buffs: list[Any], num_elems: int, timeoutUs: int
    ) -> _FakeStreamResult:
        with self._harness.lock:
            self._harness.reads.append((num_elems, timeoutUs))
            script = self._harness.read_script
            item = script.pop(0) if script else num_elems
        if isinstance(item, Exception):
            raise item
        if item > 0:
            with self._harness.lock:
                value = float(self._harness.ok_reads)
                self._harness.ok_reads += 1
            buffs[0][:item] = complex(value, 0.0)
        return _FakeStreamResult(item)


class _FakeSoapyHarness:
    """Owns the fake module, the call log, and the read script."""

    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.calls: list[tuple[Any, ...]] = []
        self.devices: list[_FakeDevice] = []
        self._device_attempts = 0
        self.failing_device_attempts: set[int] = set()
        self.reads: list[tuple[int, int]] = []
        self.read_script: list[Any] = []
        self.ok_reads = 0
        self.sleeps: list[float] = []
        self.frequency_ranges = [(24_000_000.0, 1_766_000_000.0)]
        self.ppm_supported = True

    def record(self, name: str, *call_args: Any) -> None:
        with self.lock:
            self.calls.append((name, *call_args))

    def named(self, name: str) -> list[tuple[Any, ...]]:
        with self.lock:
            return [call for call in self.calls if call[0] == name]

    def names(self) -> list[str]:
        with self.lock:
            return [call[0] for call in self.calls]

    def begin_device_attempt(self) -> int:
        with self.lock:
            attempt = self._device_attempts
            self._device_attempts += 1
            return attempt

    def device_count(self) -> int:
        with self.lock:
            return len(self.devices)

    def attempts(self) -> int:
        with self.lock:
            return self._device_attempts

    def kwargs_from_string(self, markup: str) -> dict[str, str]:
        self.record("KwargsFromString", markup)
        result: dict[str, str] = {}
        for part in markup.split(","):
            key, sep, value = part.strip().partition("=")
            if key:
                result[key.strip()] = value.strip() if sep else ""
        return result

    def device(self, args: dict[str, str]) -> _FakeDevice:
        return _FakeDevice(self, args)

    def fake_sleep(self, seconds: float) -> None:
        with self.lock:
            self.sleeps.append(seconds)

    def install(self) -> types.ModuleType:
        module = types.ModuleType("SoapySDR")
        module.SOAPY_SDR_TX = 0
        module.SOAPY_SDR_RX = 1
        module.SOAPY_SDR_CF32 = 0
        module.SOAPY_SDR_TIMEOUT = -1
        module.SOAPY_SDR_STREAM_ERROR = -2
        module.SOAPY_SDR_OVERFLOW = -4
        module.KwargsFromString = self.kwargs_from_string
        module.Device = self.device
        return module


@contextmanager
def _soapy_module(module: types.ModuleType | None) -> Iterator[None]:
    saved = sys.modules.get("SoapySDR")
    sys.modules["SoapySDR"] = module
    try:
        yield
    finally:
        if saved is None:
            sys.modules.pop("SoapySDR", None)
        else:
            sys.modules["SoapySDR"] = saved


@pytest.fixture
def soapy() -> Iterator[_FakeSoapyHarness]:
    harness = _FakeSoapyHarness()
    with _soapy_module(harness.install()):
        yield harness


def _wait_until(predicate: Callable[[], bool], timeout: float = 5.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(0.002)
    raise AssertionError(f"condition not met within {timeout:.1f}s")


class _Collector:
    def __init__(self) -> None:
        self.blocks: list[IqBlock] = []
        self.threads: list[str] = []
        self._lock = threading.Lock()

    def __call__(self, block: IqBlock) -> None:
        with self._lock:
            self.blocks.append(block)
            self.threads.append(threading.current_thread().name)

    def wait_for(self, n_blocks: int) -> list[IqBlock]:
        _wait_until(lambda: len(self.blocks) >= n_blocks)
        with self._lock:
            return list(self.blocks)


def _make_source(
    harness: _FakeSoapyHarness,
    *,
    block_size: int = 1024,
    read_timeout_us: int = 100_000,
    **config_kwargs: Any,
) -> SoapyIqSource:
    config = SdrConfig(device_args="driver=remote,remote=127.0.0.1", **config_kwargs)
    return SoapyIqSource(
        config,
        block_size=block_size,
        read_timeout_us=read_timeout_us,
        clock=lambda: _FIXED_CLOCK_S,
        sleep=harness.fake_sleep,
    )


def test_imports_without_soapysdr_and_construction_raises() -> None:
    with _soapy_module(None):
        assert importlib.import_module("rigplane")
        assert importlib.import_module("rigplane.sdr")
        module = importlib.import_module("rigplane.sdr.soapy_source")
        with pytest.raises(ImportError) as excinfo:
            module.SoapyIqSource(SdrConfig(device_args="driver=rtlsdr"))
        message = str(excinfo.value)
        assert "rigplane[sdr]" in message
        assert "python3-soapysdr" in message


@pytest.mark.parametrize(
    ("gain_db", "agc"),
    [(None, True), (19.5, False)],
)
def test_open_configure_call_sequence_and_blocks(
    soapy: _FakeSoapyHarness, gain_db: float | None, agc: bool
) -> None:
    collector = _Collector()
    source = _make_source(
        soapy,
        gain_db=gain_db,
        sample_rate_hz=2_400_000,
        extra_settings={"direct_samp": "2"},
    )
    assert isinstance(source, IqSource)
    source.set_center_freq(7_100_000)
    source.on_block(collector)
    source.open()
    try:
        blocks = collector.wait_for(2)
    finally:
        source.close()

    expected = [
        "KwargsFromString",
        "Device",
        "writeSetting",
        "setSampleRate",
        "setGainMode",
        *([] if agc else ["setGain"]),
        "setFrequency",
        "setupStream",
        "activateStream",
        "getFrequencyRange",
    ]
    names = soapy.names()
    assert names[: len(expected)] == expected
    assert "setFrequencyCorrection" not in names

    device = soapy.devices[0]
    assert device.args == {"driver": "remote", "remote": "127.0.0.1"}
    assert soapy.named("writeSetting") == [("writeSetting", "direct_samp", "2")]
    assert soapy.named("setSampleRate") == [("setSampleRate", 1, 0, 2_400_000)]
    assert soapy.named("setGainMode") == [("setGainMode", 1, 0, agc)]
    assert soapy.named("setGain") == ([] if agc else [("setGain", 1, 0, 19.5)])
    assert soapy.named("setFrequency") == [("setFrequency", 1, 0, 7_100_000)]
    assert soapy.named("setupStream") == [("setupStream", 1, 0, [0], {})]
    assert soapy.named("activateStream") == [("activateStream", device.stream)]
    assert soapy.named("deactivateStream") == [("deactivateStream", device.stream)]
    assert soapy.named("closeStream") == [("closeStream", device.stream)]

    first, second = blocks[0], blocks[1]
    assert first.samples.shape == (1024,)
    assert first.samples.dtype == np.complex64
    assert first.center_freq_hz == 7_100_000
    assert first.sample_rate_hz == 2_400_000
    assert first.timestamp_s == _FIXED_CLOCK_S
    assert np.all(first.samples == 0.0)
    assert np.all(second.samples == 1.0)
    assert set(collector.threads) == {"SoapyIqSource-reader"}


def test_no_tune_before_center_requested(soapy: _FakeSoapyHarness) -> None:
    collector = _Collector()
    source = _make_source(soapy, gain_db=19.5, sample_rate_hz=2_400_000)
    source.on_block(collector)
    source.open()
    try:
        blocks = collector.wait_for(1)
        source.set_center_freq(7_100_000)
        _wait_until(lambda: bool(soapy.named("setFrequency")))
    finally:
        source.close()

    assert soapy.names()[: soapy.names().index("setupStream")] == [
        "KwargsFromString",
        "Device",
        "setSampleRate",
        "setGainMode",
        "setGain",
    ]
    assert soapy.named("setFrequency") == [("setFrequency", 1, 0, 7_100_000)]
    assert blocks[0].center_freq_hz == 0


@pytest.mark.parametrize(
    ("ppm", "ppm_supported", "center", "expected_tune"),
    [
        (1.5, True, 7_100_000, 7_100_000),
        (2.0, False, 100_000_000, int(100_000_000 / (1.0 + 2.0e-6))),
    ],
)
def test_ppm_correction_paths(
    soapy: _FakeSoapyHarness,
    ppm: float,
    ppm_supported: bool,
    center: int,
    expected_tune: int,
) -> None:
    soapy.ppm_supported = ppm_supported
    collector = _Collector()
    source = _make_source(soapy, ppm=ppm)
    source.set_center_freq(center)
    source.on_block(collector)
    source.open()
    try:
        blocks = collector.wait_for(1)
    finally:
        source.close()

    assert soapy.named("setFrequencyCorrection") == [
        ("setFrequencyCorrection", 1, 0, ppm)
    ]
    assert soapy.named("setFrequency") == [("setFrequency", 1, 0, expected_tune)]
    assert blocks[0].center_freq_hz == center


def test_timeout_and_overflow_handling(soapy: _FakeSoapyHarness) -> None:
    soapy.read_script = [-1, -4]
    collector = _Collector()
    source = _make_source(soapy, read_timeout_us=250_000)
    source.on_block(collector)
    source.open()
    try:
        blocks = collector.wait_for(2)
    finally:
        source.close()

    assert blocks[0].overflow is True
    assert blocks[1].overflow is False
    assert soapy.device_count() == 1
    assert all(num == 1024 and timeout == 250_000 for num, timeout in soapy.reads)


def test_stream_error_reconnects_without_leaking_threads(
    soapy: _FakeSoapyHarness, caplog: pytest.LogCaptureFixture
) -> None:
    soapy.read_script = [-2]
    threads_before = threading.active_count()
    source = _make_source(soapy)
    with caplog.at_level(logging.WARNING, logger="rigplane.sdr.soapy_source"):
        source.open()
        try:
            _wait_until(lambda: soapy.device_count() == 2)
        finally:
            source.close()

    assert soapy.attempts() == 2
    warnings = [
        record
        for record in caplog.records
        if record.levelno == logging.WARNING and "reconnecting" in record.message
    ]
    assert len(warnings) == 1
    assert threading.active_count() == threads_before
    assert not [
        thread
        for thread in threading.enumerate()
        if thread.name == "SoapyIqSource-reader"
    ]


def test_reconnect_backoff_after_device_error(
    soapy: _FakeSoapyHarness, caplog: pytest.LogCaptureFixture
) -> None:
    soapy.read_script = [1024, RuntimeError("device unplugged")]
    soapy.failing_device_attempts = {1}
    collector = _Collector()
    source = _make_source(soapy)
    source.on_block(collector)
    with caplog.at_level(logging.INFO, logger="rigplane.sdr.soapy_source"):
        source.open()
        try:
            collector.wait_for(2)
        finally:
            source.close()

    assert soapy.attempts() == 3
    assert soapy.device_count() == 2
    first, second = soapy.devices[0], soapy.devices[1]
    assert soapy.named("deactivateStream") == [
        ("deactivateStream", first.stream),
        ("deactivateStream", second.stream),
    ]
    assert soapy.named("closeStream") == [
        ("closeStream", first.stream),
        ("closeStream", second.stream),
    ]
    assert soapy.sleeps == [0.25] * 12

    blocks = collector.blocks
    assert len(blocks) >= 2
    assert np.all(blocks[0].samples == 0.0)
    assert np.all(blocks[1].samples == 1.0)

    warnings = [
        record
        for record in caplog.records
        if record.levelno == logging.WARNING and "reconnecting" in record.message
    ]
    recovered = [
        record
        for record in caplog.records
        if record.levelno == logging.INFO and "recovered" in record.message
    ]
    assert len(warnings) == 1
    assert len(recovered) == 1


def test_live_retune_and_gain_apply_to_device(soapy: _FakeSoapyHarness) -> None:
    collector = _Collector()
    source = _make_source(soapy, gain_db=None)
    source.on_block(collector)
    source.open()
    try:
        collector.wait_for(1)
        source.set_gain(29.0)
        source.set_center_freq(14_074_000)
        last = ("setFrequency", 1, 0, 14_074_000)
        _wait_until(lambda: soapy.named("setFrequency")[-1:] == [last])
        blocks = collector.wait_for(2)
    finally:
        source.close()

    assert soapy.named("setGain")[-1] == ("setGain", 1, 0, 29.0)
    assert blocks[-1].center_freq_hz == 14_074_000
    gain_modes = soapy.named("setGainMode")
    assert gain_modes[0] == ("setGainMode", 1, 0, True)
    assert gain_modes[-1] == ("setGainMode", 1, 0, False)


def test_clean_close_is_idempotent_and_quiet(
    soapy: _FakeSoapyHarness, caplog: pytest.LogCaptureFixture
) -> None:
    collector = _Collector()
    source = _make_source(soapy)
    source.on_block(collector)
    with caplog.at_level(logging.WARNING, logger="rigplane.sdr.soapy_source"):
        source.open()
        source.open()
        collector.wait_for(1)
        source.close()
        source.close()

    assert source.is_open is False
    device = soapy.devices[0]
    assert soapy.named("deactivateStream") == [("deactivateStream", device.stream)]
    assert soapy.named("closeStream") == [("closeStream", device.stream)]
    assert soapy.device_count() == 1
    assert soapy.attempts() == 1
    assert [
        record for record in caplog.records if record.levelno >= logging.WARNING
    ] == []


def test_frequency_range_default_then_device(soapy: _FakeSoapyHarness) -> None:
    soapy.frequency_ranges = [
        (24_000_000.0, 1_766_000_000.0),
        (60_000_000.0, 2_400_000_000.0),
    ]
    source = _make_source(soapy)
    assert source.frequency_range_hz() == (0, 6_000_000_000)

    source.open()
    try:
        _wait_until(lambda: source.frequency_range_hz() == (24_000_000, 2_400_000_000))
    finally:
        source.close()


@pytest.mark.parametrize(
    ("source_kwargs", "match"),
    [
        ({"block_size": 0}, "block_size"),
        ({"min_retry_s": 0.0}, "min_retry_s"),
        ({"min_retry_s": 1.0, "max_retry_s": 0.5}, "max_retry_s"),
    ],
)
def test_constructor_validates_arguments(
    soapy: _FakeSoapyHarness, source_kwargs: dict[str, Any], match: str
) -> None:
    with pytest.raises(ValueError, match=match):
        SoapyIqSource(
            SdrConfig(device_args="driver=remote"),
            sleep=soapy.fake_sleep,
            **source_kwargs,
        )


def test_callback_exception_does_not_kill_reader(
    soapy: _FakeSoapyHarness, caplog: pytest.LogCaptureFixture
) -> None:
    blocks: list[IqBlock] = []
    raise_once = True

    def flaky(block: IqBlock) -> None:
        nonlocal raise_once
        if raise_once:
            raise_once = False
            raise RuntimeError("consumer bug")
        blocks.append(block)

    source = _make_source(soapy)
    source.on_block(flaky)
    with caplog.at_level(logging.ERROR, logger="rigplane.sdr.soapy_source"):
        source.open()
        try:
            _wait_until(lambda: len(blocks) >= 2)
        finally:
            source.close()

    assert soapy.device_count() == 1
    errors = [record for record in caplog.records if record.levelno >= logging.ERROR]
    assert len(errors) == 1
    assert "on_block" in errors[0].message
