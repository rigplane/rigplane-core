"""Tests for Yaesu CAT CLI/factory integration (#444)."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from rigplane.backends.config import (
    SerialBackendConfig,
    YaesuCatBackendConfig,
)
from rigplane.backends.factory import create_radio
from rigplane.backends.yaesu_cat.radio import YaesuCatRadio
from rigplane.cli import _build_backend_config, _build_parser


# ---------------------------------------------------------------------------
# YaesuCatBackendConfig validation
# ---------------------------------------------------------------------------


class TestYaesuCatBackendConfig:
    def test_defaults(self):
        cfg = YaesuCatBackendConfig(device="/dev/ttyUSB0")
        assert cfg.backend == "yaesu-cat"
        assert cfg.baudrate == 38400
        assert cfg.audio_sample_rate == 48000
        assert cfg.rx_device is None
        assert cfg.tx_device is None
        assert cfg.model is None

    def test_device_required(self):
        with pytest.raises(ValueError, match="device"):
            YaesuCatBackendConfig(device="")

    def test_device_whitespace_rejected(self):
        with pytest.raises(ValueError, match="device"):
            YaesuCatBackendConfig(device="   ")

    def test_bad_baudrate(self):
        with pytest.raises(ValueError, match="baudrate"):
            YaesuCatBackendConfig(device="/dev/ttyUSB0", baudrate=0)

    def test_bad_audio_sample_rate(self):
        with pytest.raises(ValueError, match="audio_sample_rate"):
            YaesuCatBackendConfig(device="/dev/ttyUSB0", audio_sample_rate=-1)

    def test_empty_rx_device_rejected(self):
        with pytest.raises(ValueError, match="rx_device"):
            YaesuCatBackendConfig(device="/dev/ttyUSB0", rx_device="")

    def test_empty_tx_device_rejected(self):
        with pytest.raises(ValueError, match="tx_device"):
            YaesuCatBackendConfig(device="/dev/ttyUSB0", tx_device="")

    def test_audio_device_overrides(self):
        cfg = YaesuCatBackendConfig(
            device="/dev/ttyUSB0",
            rx_device="FTX-1 Audio",
            tx_device="BlackHole 2ch",
        )
        assert cfg.rx_device == "FTX-1 Audio"
        assert cfg.tx_device == "BlackHole 2ch"

    def test_custom_model(self):
        cfg = YaesuCatBackendConfig(device="/dev/ttyUSB0", model="FTX-1")
        assert cfg.model == "FTX-1"


# ---------------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------------


class TestFactoryYaesuCat:
    def test_creates_yaesu_cat_radio(self):
        cfg = YaesuCatBackendConfig(device="/dev/ttyUSB0")
        radio = create_radio(cfg)
        assert isinstance(radio, YaesuCatRadio)

    def test_passes_audio_overrides(self):
        cfg = YaesuCatBackendConfig(
            device="/dev/ttyUSB0",
            rx_device="FTX-1 Audio",
            tx_device="BlackHole 2ch",
        )
        radio = create_radio(cfg)
        assert isinstance(radio, YaesuCatRadio)

    def test_serial_ftx1_backward_compat(self):
        """--backend serial --model FTX-1 still works via SerialBackendConfig."""
        cfg = SerialBackendConfig(device="/dev/ttyUSB0", model="FTX-1")
        radio = create_radio(cfg)
        assert isinstance(radio, YaesuCatRadio)

    def test_serial_ft_model_is_passed_to_yaesu_radio(self):
        cfg = SerialBackendConfig(device="/dev/ttyUSB0", model="FTX-1")
        with patch("rigplane.backends.factory.YaesuCatRadio") as constructor:
            create_radio(cfg)

        constructor.assert_called_once()
        assert constructor.call_args.kwargs["profile"] == "FTX-1"


# ---------------------------------------------------------------------------
# CLI _build_backend_config
# ---------------------------------------------------------------------------


class TestCliBuildBackendConfig:
    async def test_yaesu_cat_returns_correct_config(self):
        p = _build_parser()
        args = p.parse_args(
            ["--backend", "yaesu-cat", "--serial-port", "/dev/ttyUSB0", "status"]
        )
        cfg = await _build_backend_config(args)
        assert isinstance(cfg, YaesuCatBackendConfig)
        assert cfg.backend == "yaesu-cat"
        assert cfg.device == "/dev/ttyUSB0"
        assert cfg.baudrate == 38400

    async def test_yaesu_cat_custom_baud(self):
        p = _build_parser()
        args = p.parse_args(
            [
                "--backend",
                "yaesu-cat",
                "--serial-port",
                "/dev/ttyUSB0",
                "--serial-baud",
                "9600",
                "status",
            ]
        )
        cfg = await _build_backend_config(args)
        assert isinstance(cfg, YaesuCatBackendConfig)
        assert cfg.baudrate == 9600

    async def test_yaesu_cat_missing_port_triggers_discovery(self):
        p = _build_parser()
        args = p.parse_args(["--backend", "yaesu-cat", "status"])
        with patch(
            "rigplane.discovery.discover_serial_radios", AsyncMock(return_value=[])
        ):
            with pytest.raises(SystemExit):
                await _build_backend_config(args)

    async def test_serial_ftx1_backward_compat(self):
        """--backend serial --model FTX-1 still returns SerialBackendConfig."""
        p = _build_parser()
        args = p.parse_args(
            [
                "--backend",
                "serial",
                "--serial-port",
                "/dev/ttyUSB0",
                "--model",
                "FTX-1",
                "status",
            ]
        )
        cfg = await _build_backend_config(args)
        assert isinstance(cfg, SerialBackendConfig)
        assert cfg.model == "FTX-1"

    async def test_yaesu_cat_passes_audio_devices(self):
        p = _build_parser()
        args = p.parse_args(
            [
                "--backend",
                "yaesu-cat",
                "--serial-port",
                "/dev/ttyUSB0",
                "--rx-device",
                "FTX-1 Audio",
                "--tx-device",
                "BlackHole 2ch",
                "status",
            ]
        )
        cfg = await _build_backend_config(args)
        assert isinstance(cfg, YaesuCatBackendConfig)
        assert cfg.rx_device == "FTX-1 Audio"
        assert cfg.tx_device == "BlackHole 2ch"


class TestCliHelp:
    def test_yaesu_cat_in_choices(self):
        p = _build_parser()
        # argparse stores choices on the action
        for action in p._actions:
            if action.dest == "backend":
                assert "yaesu-cat" in action.choices
                break
        else:
            pytest.fail("--backend argument not found in parser")
