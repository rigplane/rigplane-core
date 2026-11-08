"""Integration tests for the SDR scope source in the web server (MOR-3157).

Drives :class:`WebServer` with a :class:`FakeIqSource` injected via
``WebConfig.sdr_source_factory`` through explicit hub subscriptions.
MOR-3201 adds the public ``sdr`` status object (schema conformance,
lifecycle, broadcast) and the ``RIGPLANE_SDR_*`` CLI/env wiring.
"""

from __future__ import annotations

import asyncio
import io
import os
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest

from rigplane.cli import _build_parser, _cmd_web

from rigplane.capabilities import CAP_AUDIO, CAP_SCOPE
from rigplane.radio_protocol import AudioCapable
from rigplane.radio_state import RadioState
from rigplane.sdr.fake import FakeIqSource
from rigplane.sdr.types import SdrConfig
from rigplane.types import AudioCodec
from rigplane.web.server import WebConfig, WebServer

_RATE = 2_400_000
_VFO1 = 14_074_000
_VFO2 = 14_124_000  # +50 kHz: view shift, no retune
_SPAN = _RATE // 4  # controller default span: 600 kHz


class _AudioRadio(AudioCapable):
    """Audio-capable radio without a hardware scope (audio-FFT fallback)."""

    # Class default shadows the protocol's read-only property
    # (tests/test_tap_surface pattern).
    audio_bus: object = None

    def __init__(self, *, hardware: bool = False) -> None:
        sub = SimpleNamespace(start=lambda: None, stop=lambda: None)
        self.audio_bus = SimpleNamespace(
            subscribe=lambda name="": sub, subscribe_rx=lambda name="": sub
        )
        self.capabilities = {CAP_AUDIO, CAP_SCOPE} if hardware else {CAP_AUDIO}
        self.radio_state = RadioState()

    @property
    def audio_codec(self):
        return AudioCodec.PCM_1CH_16BIT

    @property
    def audio_sample_rate(self):
        return 48_000


def _make_server(fake, *, scope_source: str = "auto", factory: Any = None) -> WebServer:
    return WebServer(
        _AudioRadio(),
        WebConfig(
            radio_model="IC-7300",
            scope_source=scope_source,
            sdr_config=SdrConfig(device_args="fake"),
            sdr_source_factory=factory or (lambda _config: fake),
        ),
    )


def _make_fake() -> FakeIqSource:
    return FakeIqSource(
        sample_rate_hz=_RATE,
        block_size=4096,
        tones=((600_000.0, -6.0),),  # +1/4 rate: lands on the tuned VFO
    )


# ---------------------------------------------------------------------------
# Runtime SDR↔hardware-scope fallback (MOR-3202)
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# Public ``sdr`` status object (MOR-3201)
# ---------------------------------------------------------------------------

_DISABLED_SDR = {
    "state": "disabled",
    "device": "",
    "sampleRateHz": 0,
    "spanHz": 0,
    "txFrozen": False,
    "overflowCount": 0,
    "lastError": None,
}


async def test_sdr_status_object_disabled_without_sdr() -> None:
    """Without the SDR runtime the state carries ``sdr.state ==
    "disabled"`` and nothing else in the payload changes: dropping the
    runtime yields an otherwise identical payload modulo the seq bump
    the delivery-key change causes and the wall-clock ``sinceMs``."""
    pytest.importorskip("pydantic")
    from rigplane.web.state_schema import ServerStatePublic

    server = _make_server(_make_fake())
    with_runtime = server.build_public_state(updated_at="t")
    assert with_runtime["sdr"] == {
        **_DISABLED_SDR,
        "device": "fake",
        "sampleRateHz": _RATE,
    }
    server._config.sdr_config = None
    without_runtime = server.build_public_state(updated_at="t")
    assert without_runtime["sdr"] == _DISABLED_SDR

    assert sorted(with_runtime) == sorted(without_runtime)
    for key in ("sdr", "publicStateSeq"):
        with_runtime.pop(key)
        without_runtime.pop(key)
    with_runtime["radioHealth"].pop("sinceMs")
    without_runtime["radioHealth"].pop("sinceMs")
    assert with_runtime == without_runtime
    # The intact (un-mangled) payload conforms to the canonical schema.
    ServerStatePublic.model_validate(server.build_public_state())


# ---------------------------------------------------------------------------
# RIGPLANE_SDR_* env fallback through the CLI (MOR-3201)
# ---------------------------------------------------------------------------


class _CaptureWebServer:
    def __init__(self, _radio: Any, cfg: Any) -> None:
        self.captured_config = cfg
        self._runtime_log_path = None

    async def serve_forever(self, *, on_started: Any = None) -> None:
        on_started()
        raise asyncio.CancelledError


async def _run_cmd_web(
    monkeypatch: pytest.MonkeyPatch, env: dict[str, str], *cli_flags: str
) -> tuple[Any, dict[str, Any]]:
    """Run ``rigplane web`` with *env* and the WebServer class replaced
    by a config capturer; return ``(exit code, config)``."""
    for var in list(os.environ):
        if var.startswith("RIGPLANE_"):
            monkeypatch.delenv(var)
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    monkeypatch.setenv("ICOM_LOG_FILE", "off")
    with patch("sys.stderr", new_callable=io.StringIO):
        args = _build_parser().parse_args(["web", *cli_flags])
    args.web_rigctld = False
    captured: dict[str, Any] = {}

    def _capture(radio_arg: Any, cfg: Any) -> _CaptureWebServer:
        server = _CaptureWebServer(radio_arg, cfg)
        captured["config"] = cfg
        return server

    with patch("rigplane.web.server.WebServer", _capture):
        code = await _cmd_web(AsyncMock(), args)
    return code, captured.get("config")


async def test_cli_env_falls_back_and_flags_win(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Env alone configures the SDR; a given CLI flag wins over env."""
    code, cfg = await _run_cmd_web(
        monkeypatch,
        {
            "RIGPLANE_SCOPE_SOURCE": "hardware",
            "RIGPLANE_SDR_DEVICE": "driver=env",
            "RIGPLANE_SDR_SAMPLE_RATE": "1000000",
            "RIGPLANE_SDR_GAIN": "32.5",
            "RIGPLANE_SDR_PPM": "1.5",
            "RIGPLANE_SDR_SETTINGS": "direct_samp=2",
        },
        "--scope-source",
        "sdr",
        "--sdr-device",
        "driver=cli",
        "--sdr-sample-rate",
        "2400000",
    )
    assert code == 0
    assert cfg.sdr_config == SdrConfig(
        device_args="driver=cli",  # CLI wins over RIGPLANE_SDR_DEVICE
        sample_rate_hz=2_400_000,  # CLI wins over RIGPLANE_SDR_SAMPLE_RATE
        gain_db=32.5,  # env fallback (no flag given)
        ppm=1.5,
        extra_settings={"direct_samp": "2"},
    )
    assert cfg.scope_source == "sdr"  # CLI wins over RIGPLANE_SCOPE_SOURCE


async def test_cli_bad_env_value_fails_naming_the_variable(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    code, _cfg = await _run_cmd_web(monkeypatch, {"RIGPLANE_SDR_PPM": "lots"})
    assert code == 1
    assert "RIGPLANE_SDR_PPM" in capsys.readouterr().err


async def test_iq_source_requires_explicit_subscription_and_stops_provider():
    from rigplane.scope.models import Subscription, View

    provider = FakeIqSource(center_freq_hz=14_074_000, sample_rate_hz=_RATE)
    server = WebServer(
        None,
        WebConfig(
            sdr_config=SdrConfig(device_args="driver=fake", sample_rate_hz=_RATE),
            sdr_source_factory=lambda config: provider,
        ),
    )
    assert provider._is_open is False
    hub = server.scope_hub
    assert provider._is_open is False
    subscription = await hub.subscribe(
        Subscription("iq_fft", None, View(14_024_000, 14_124_000), 1024, 20)
    )
    assert provider._is_open is True
    await subscription.close()
    assert provider._is_open is False
