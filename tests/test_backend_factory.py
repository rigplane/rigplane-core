"""Smoke tests for backend config/contracts/factory foundation layer."""

from __future__ import annotations

from dataclasses import dataclass

import pytest

from rigplane import IcomRadio, create_radio
from rigplane.backends.config import (
    LanBackendConfig,
    RigctldBackendConfig,
    SerialBackendConfig,
    YaesuCatBackendConfig,
)
from rigplane.backends.ic7300.serial import Ic7300SerialRadio
from rigplane.backends.icom7610 import Icom7610SerialRadio
from rigplane.backends.yaesu_cat.radio import YaesuCatRadio
from rigplane.backends.rigctld_client.radio import RigctldClientRadio
from rigplane.backends.icom7610.drivers.contracts import (
    AudioDriver,
    CivLink,
    SessionDriver,
)


class TestBackendConfigValidation:
    def test_lan_backend_defaults(self) -> None:
        config = LanBackendConfig(host="192.168.55.40")
        assert config.backend == "lan"
        assert config.port == 50001
        assert config.radio_addr is None

    def test_lan_backend_host_required(self) -> None:
        with pytest.raises(ValueError, match="host"):
            LanBackendConfig(host="")

    def test_serial_backend_port_required(self) -> None:
        with pytest.raises(ValueError, match="device"):
            SerialBackendConfig(device="")

    def test_serial_backend_default_baudrate_matches_runtime_default(self) -> None:
        config = SerialBackendConfig(device="/dev/tty.usbmodem-IC7610")
        assert config.baudrate == 115200

    def test_serial_backend_audio_device_overrides_are_optional(self) -> None:
        config = SerialBackendConfig(
            device="/dev/tty.usbmodem-IC7610",
            rx_device="IC-7610 USB Audio",
            tx_device="BlackHole 2ch",
        )
        assert config.rx_device == "IC-7610 USB Audio"
        assert config.tx_device == "BlackHole 2ch"

    def test_serial_backend_scope_low_baud_override_is_typed_bool(self) -> None:
        config = SerialBackendConfig(
            device="/dev/tty.usbmodem-IC7610",
            allow_low_baud_scope=True,
        )
        assert config.allow_low_baud_scope is True

    def test_serial_backend_ptt_mode_validation_rejects_unknown_values(self) -> None:
        with pytest.raises(ValueError, match="ptt_mode"):
            SerialBackendConfig(
                device="/dev/tty.usbmodem-IC7610",
                ptt_mode="rts",  # type: ignore[arg-type]
            )


class TestCreateRadioFactory:
    def test_create_radio_builds_lan_backend(self) -> None:
        radio = create_radio(
            LanBackendConfig(host="192.168.55.40", username="u", password="p")
        )
        assert isinstance(radio, IcomRadio)
        assert radio.model == "IC-7610"

    def test_create_radio_uses_profile_civ_addr_when_model_provided(self) -> None:
        radio = create_radio(LanBackendConfig(host="192.168.55.40", model="IC-7300"))
        assert isinstance(radio, IcomRadio)
        assert radio.model == "IC-7300"
        assert radio._radio_addr == 0x94

    def test_create_radio_builds_serial_backend(self) -> None:
        radio = create_radio(SerialBackendConfig(device="/dev/ttyUSB0"))
        assert isinstance(radio, Icom7610SerialRadio)
        assert radio.model == "IC-7610"

    def test_create_radio_passes_serial_audio_overrides(self) -> None:
        radio = create_radio(
            SerialBackendConfig(
                device="/dev/tty.usbmodem-IC7610",
                rx_device="IC-7610 USB Audio",
                tx_device="BlackHole 2ch",
            )
        )
        assert isinstance(radio, Icom7610SerialRadio)
        assert radio._serial_rx_device_override == "IC-7610 USB Audio"
        assert radio._serial_tx_device_override == "BlackHole 2ch"

    def test_create_radio_passes_serial_scope_low_baud_override(self) -> None:
        radio = create_radio(
            SerialBackendConfig(
                device="/dev/tty.usbmodem-IC7610",
                allow_low_baud_scope=True,
            )
        )
        assert isinstance(radio, Icom7610SerialRadio)
        assert radio._allow_low_baud_scope is True

    def test_create_radio_passes_serial_ptt_mode(self) -> None:
        radio = create_radio(
            SerialBackendConfig(
                device="/dev/tty.usbmodem-IC7610",
                ptt_mode="civ",
            )
        )
        assert isinstance(radio, Icom7610SerialRadio)
        assert radio._serial_ptt_mode == "civ"

    def test_lan_backend_has_icom_lan_backend_id(self) -> None:
        radio = create_radio(LanBackendConfig(host="192.168.55.40"))
        assert radio.backend_id == "rigplane"

    def test_serial_icom_backend_has_icom_serial_backend_id(self) -> None:
        radio = create_radio(SerialBackendConfig(device="/dev/ttyUSB0"))
        assert radio.backend_id == "icom_serial"

    def test_yaesu_cat_backend_config_has_yaesu_cat_backend_id(self) -> None:
        radio = create_radio(YaesuCatBackendConfig(device="/dev/ttyUSB0"))
        assert isinstance(radio, YaesuCatRadio)
        assert radio.backend_id == "yaesu_cat"

    def test_rigctld_backend_config_has_rigctld_backend_id(self) -> None:
        radio = create_radio(RigctldBackendConfig(host="localhost"))
        assert isinstance(radio, RigctldClientRadio)
        assert radio.backend_id == "rigctld"

    def test_create_radio_serial_unknown_model_raises(self) -> None:
        """Regression for MOR-174: an unknown serial --model must ERROR, not
        silently impersonate an IC-7610 against real hardware."""
        with pytest.raises(ValueError, match="Unsupported serial model") as excinfo:
            create_radio(SerialBackendConfig(device="/dev/ttyUSB0", model="TX-500"))
        message = str(excinfo.value)
        assert "TX-500" in message
        for supported in ("IC-705", "IC-7300", "IC-7610", "IC-9700", "X6200"):
            assert supported in message

    def test_create_radio_serial_known_model_dispatches_to_its_class(self) -> None:
        radio = create_radio(
            SerialBackendConfig(device="/dev/ttyUSB0", model="IC-7300")
        )
        assert isinstance(radio, Ic7300SerialRadio)
        assert radio.model == "IC-7300"

    def test_create_radio_serial_explicit_ic7610_no_unknown_model_warning(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        """Explicit --model IC-7610 is a supported model, not a fallback;
        it must dispatch without any 'Unknown model' warning."""
        with caplog.at_level("WARNING", logger="rigplane.backends.factory"):
            radio = create_radio(
                SerialBackendConfig(device="/dev/ttyUSB0", model="IC-7610")
            )
        assert isinstance(radio, Icom7610SerialRadio)
        assert not any("Unknown model" in rec.getMessage() for rec in caplog.records)

    def test_create_radio_rejects_unknown_backend(self) -> None:
        @dataclass(slots=True)
        class _UnknownConfig:
            backend: str = "bluetooth"

        with pytest.raises(ValueError, match="Unsupported backend"):
            create_radio(_UnknownConfig())  # type: ignore[arg-type]


class TestContracts:
    def test_minimal_session_contract_shape(self) -> None:
        class _Session:
            async def connect(self) -> None:
                return None

            async def disconnect(self) -> None:
                return None

            @property
            def connected(self) -> bool:
                return True

        assert isinstance(_Session(), SessionDriver)

    def test_minimal_civ_contract_shape(self) -> None:
        class _Civ:
            async def send(self, frame: bytes) -> None:
                return None

            async def receive(self, timeout: float | None = None) -> bytes | None:
                return frame if (frame := b"x") else None

        assert isinstance(_Civ(), CivLink)

    def test_minimal_audio_contract_shape(self) -> None:
        class _Audio:
            async def start_rx(self) -> None:
                return None

            async def stop_rx(self) -> None:
                return None

            async def start_tx(self) -> None:
                return None

            async def stop_tx(self) -> None:
                return None

        assert isinstance(_Audio(), AudioDriver)
