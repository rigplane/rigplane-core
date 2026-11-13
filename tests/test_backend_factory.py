"""Smoke tests for backend config/contracts/factory foundation layer."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

import pytest

from rigplane import IcomRadio, create_radio
from rigplane.backends.config import (
    LanBackendConfig,
    RigctldBackendConfig,
    SerialBackendConfig,
    YaesuCatBackendConfig,
)
from rigplane.backends.factory import _yaesu_profile_for
from rigplane.cli import _build_backend_config, _build_parser
from rigplane.backends.ic7300.serial import Ic7300SerialRadio
from rigplane.backends.icom7610 import Icom7610SerialRadio
from rigplane.backends.yaesu_cat.radio import YaesuCatRadio
from rigplane.backends.rigctld_client.radio import RigctldClientRadio
from rigplane.backends.icom7610.drivers.contracts import (
    AudioDriver,
    CivLink,
    SessionDriver,
)
from rigplane.exceptions import CommandError


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
    def test_create_radio_lan_backend_refuses_without_a_model(self) -> None:
        """A LAN backend with no model/profile/radio_addr no longer
        silently impersonates IC-7610 (plan §8.1 Q5) — it refuses."""
        with pytest.raises(ValueError, match="Cannot resolve a radio profile"):
            create_radio(
                LanBackendConfig(host="192.168.55.40", username="u", password="p")
            )

    def test_create_radio_uses_profile_civ_addr_when_model_provided(self) -> None:
        radio = create_radio(LanBackendConfig(host="192.168.55.40", model="IC-7300"))
        assert isinstance(radio, IcomRadio)
        assert radio.model == "IC-7300"
        assert radio._radio_addr == 0x94

    def test_create_radio_builds_serial_backend(self) -> None:
        radio = create_radio(
            SerialBackendConfig(device="/dev/ttyUSB0", model="IC-7610")
        )
        assert isinstance(radio, Icom7610SerialRadio)
        assert radio.model == "IC-7610"

    def test_create_radio_serial_without_model_refuses(self) -> None:
        """R59/MOR-2425: no --model must refuse, not assume the IC-7610.

        The old default drove an IC-7300 as an IC-7610: the server then
        waited on sub-receiver fields the rig does not have.
        """
        with pytest.raises(ValueError) as excinfo:
            create_radio(SerialBackendConfig(device="/dev/ttyUSB0"))
        message = str(excinfo.value)
        assert "--model" in message
        assert "IC-7610" not in message

    def test_create_radio_passes_serial_audio_overrides(self) -> None:
        radio = create_radio(
            SerialBackendConfig(
                device="/dev/tty.usbmodem-IC7610",
                model="IC-7610",
                rx_device="IC-7610 USB Audio",
                tx_device="BlackHole 2ch",
            )
        )
        assert isinstance(radio, Icom7610SerialRadio)
        audio_config = radio._serial_audio_driver._config
        assert audio_config.rx_device == "IC-7610 USB Audio"
        assert audio_config.tx_device == "BlackHole 2ch"

    def test_capture_mode_defaults_to_shared_and_rejects_unknown_values(self) -> None:
        assert SerialBackendConfig(device="COM3").capture_mode == "shared"
        assert YaesuCatBackendConfig(device="COM3").capture_mode == "shared"
        with pytest.raises(ValueError, match="capture_mode"):
            SerialBackendConfig(device="COM3", capture_mode="exclusive")
        with pytest.raises(ValueError, match="capture_mode"):
            YaesuCatBackendConfig(device="COM3", capture_mode="")

    @pytest.mark.parametrize("mode", ["shared", "exclusive-preferred"])
    def test_create_radio_passes_serial_capture_mode(self, mode: str) -> None:
        radio = create_radio(
            SerialBackendConfig(device="COM3", model="IC-7300", capture_mode=mode)
        )
        driver = radio._serial_audio_driver
        assert driver._config.capture_mode == mode
        assert driver._backend._capture_policy.mode == mode

    @pytest.mark.parametrize("mode", ["shared", "exclusive-preferred"])
    def test_create_radio_passes_yaesu_capture_mode(self, mode: str) -> None:
        radio = create_radio(
            YaesuCatBackendConfig(device="COM3", model="FTX-1", capture_mode=mode)
        )
        driver = radio._audio_driver
        assert driver._config.capture_mode == mode
        assert driver._backend._capture_policy.mode == mode

    def test_create_radio_passes_serial_scope_low_baud_override(self) -> None:
        radio = create_radio(
            SerialBackendConfig(
                device="/dev/tty.usbmodem-IC7610",
                model="IC-7610",
                allow_low_baud_scope=True,
            )
        )
        assert isinstance(radio, Icom7610SerialRadio)
        assert radio._allow_low_baud_scope is True

    def test_create_radio_passes_serial_ptt_mode(self) -> None:
        radio = create_radio(
            SerialBackendConfig(
                device="/dev/tty.usbmodem-IC7610",
                model="IC-7610",
                ptt_mode="civ",
            )
        )
        assert isinstance(radio, Icom7610SerialRadio)
        assert radio._serial_ptt_mode == "civ"

    def test_lan_backend_has_icom_lan_backend_id(self) -> None:
        radio = create_radio(LanBackendConfig(host="192.168.55.40", model="IC-7610"))
        assert radio.backend_id == "rigplane"

    def test_serial_icom_backend_has_icom_serial_backend_id(self) -> None:
        radio = create_radio(
            SerialBackendConfig(device="/dev/ttyUSB0", model="IC-7610")
        )
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


class TestYaesuModelForwarding:
    """MOR-4434: the CLI-selected Yaesu model reaches the rig profile.

    ``--model FTX-1`` (or no model) resolves the ``yaesu_ftx1`` profile; a
    Yaesu model with no shipped profile (FT-710) keeps the long-standing
    ``ftx1`` default instead of raising. Both Yaesu backend branches
    (``YaesuCatBackendConfig`` and ``SerialBackendConfig``) forward the
    same way. ``--model FT-710`` cannot travel the parser chain:
    ``_resolve_model`` rejects unprofiled models before the factory runs,
    so the FT-710 cases below construct the backend config directly.
    """

    @pytest.mark.parametrize(
        ("model", "expected"),
        [(None, "ftx1"), ("FTX-1", "FTX-1"), ("FT-710", "ftx1")],
    )
    def test_yaesu_profile_for(self, model: str | None, expected: str) -> None:
        assert _yaesu_profile_for(model) == expected

    async def _cli_chain_profile_id(self, argv: list[str]) -> str:
        args = _build_parser().parse_args(argv)
        config = await _build_backend_config(args)
        radio = create_radio(config)
        assert isinstance(radio, YaesuCatRadio)
        return radio._config.id  # noqa: SLF001

    async def test_cli_chain_without_model_selects_ftx1_default(self) -> None:
        profile_id = await self._cli_chain_profile_id(
            ["--backend", "yaesu-cat", "--serial-port", "/dev/ttyUSB0", "status"]
        )
        assert profile_id == "yaesu_ftx1"

    async def test_cli_chain_with_ftx1_selects_ftx1(self) -> None:
        profile_id = await self._cli_chain_profile_id(
            ["--model", "FTX-1", "--serial-port", "/dev/ttyUSB0", "status"]
        )
        assert profile_id == "yaesu_ftx1"

    async def test_cli_chain_serial_branch_with_ftx1_selects_ftx1(self) -> None:
        profile_id = await self._cli_chain_profile_id(
            [
                "--model",
                "FTX-1",
                "--backend",
                "serial",
                "--serial-port",
                "/dev/ttyUSB0",
                "status",
            ]
        )
        assert profile_id == "yaesu_ftx1"

    @pytest.mark.parametrize(
        "config",
        [
            YaesuCatBackendConfig(device="/dev/ttyUSB0", model="FT-710"),
            SerialBackendConfig(device="/dev/ttyUSB0", model="FT-710"),
        ],
    )
    def test_unprofiled_yaesu_model_falls_back_to_ftx1(self, config) -> None:
        radio = create_radio(config)
        assert isinstance(radio, YaesuCatRadio)
        assert radio._config.id == "yaesu_ftx1"  # noqa: SLF001


class TestYaesuFixtureAliasForwarding:
    """The factory forwards a resolved Yaesu model, not the default.

    A TEMPORARY alias registers the single-receiver fixture under an
    FT-prefixed model in a test-only ``tmp_path`` rigs dir (mirroring
    ``test_sim_cw_text_entry_points.py``). Both factory routes and the CLI
    chain must yield the FIXTURE profile; removing either
    ``profile=_yaesu_profile_for(...)`` argument falls back to
    ``yaesu_ftx1`` and fails every assertion here.
    """

    FIXTURE = (
        Path(__file__).resolve().parents[1]
        / "tests/fixtures/rigs/yaesu_single_rx_fixture.toml"
    )
    MODEL = "FT-Fixture"

    def _rigs_dir(self, tmp_path: Path) -> Path:
        text = self.FIXTURE.read_text(encoding="utf-8")
        anchor = 'model = "Yaesu single RX fixture"'
        assert anchor in text
        rigs = tmp_path / "rigs"
        rigs.mkdir()
        (rigs / "ft_fixture.toml").write_text(
            text.replace(anchor, f'model = "{self.MODEL}"', 1),
            encoding="utf-8",
        )
        return rigs

    def _patch_dirs(self, monkeypatch: pytest.MonkeyPatch, rigs: Path) -> None:
        monkeypatch.setattr(
            "rigplane.backends.yaesu_cat.radio._RIGS_DIR", rigs
        )
        monkeypatch.setattr("rigplane.cli._rigs_dir", lambda: rigs)

    @pytest.mark.parametrize("backend", ["yaesu-cat", "serial"])
    def test_factory_routes_forward_fixture_profile(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, backend: str
    ) -> None:
        rigs = self._rigs_dir(tmp_path)
        self._patch_dirs(monkeypatch, rigs)
        if backend == "yaesu-cat":
            config = YaesuCatBackendConfig(device="/dev/ttyUSB0", model=self.MODEL)
        else:
            config = SerialBackendConfig(device="/dev/ttyUSB0", model=self.MODEL)
        radio = create_radio(config)
        assert isinstance(radio, YaesuCatRadio)
        assert radio._config.id == "yaesu_single_rx_fixture"  # noqa: SLF001

    @pytest.mark.parametrize("backend", ["yaesu-cat", "serial"])
    async def test_cli_chain_forwards_fixture_profile(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, backend: str
    ) -> None:
        rigs = self._rigs_dir(tmp_path)
        self._patch_dirs(monkeypatch, rigs)
        args = _build_parser().parse_args(
            [
                "--model",
                self.MODEL,
                "--backend",
                backend,
                "--serial-port",
                "/dev/ttyUSB0",
                "status",
            ]
        )
        config = await _build_backend_config(args)
        radio = create_radio(config)
        assert isinstance(radio, YaesuCatRadio)
        assert radio._config.id == "yaesu_single_rx_fixture"  # noqa: SLF001


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

            async def send_written(
                self, frame: bytes, *, is_current: Callable[[], bool] | None = None
            ) -> None:
                if is_current is not None and not is_current():
                    raise CommandError("Serial CI-V write is no longer current.")
                await self.send(frame)

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
