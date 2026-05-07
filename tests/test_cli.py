"""Tests for CLI module — parser, frequency parsing, main entry."""

import argparse
import io
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from rigplane.cli import (
    _build_backend_config,
    _build_parser,
    _parse_frequency,
    _resolve_password,
    check_ports_available,
    main,
)
from rigplane.backends.config import LanBackendConfig, SerialBackendConfig


class TestParseFrequency:
    def test_hz(self):
        assert _parse_frequency("14074000") == 14_074_000

    def test_khz(self):
        assert _parse_frequency("14074k") == 14_074_000

    def test_khz_suffix(self):
        assert _parse_frequency("14074khz") == 14_074_000

    def test_mhz(self):
        assert _parse_frequency("14.074m") == 14_074_000

    def test_mhz_suffix(self):
        assert _parse_frequency("14.074mhz") == 14_074_000

    def test_float_hz(self):
        assert _parse_frequency("7074000.0") == 7_074_000

    def test_whitespace(self):
        assert _parse_frequency("  14074000  ") == 14_074_000


class TestBuildParser:
    def test_parser_created(self):
        p = _build_parser()
        assert isinstance(p, argparse.ArgumentParser)

    def test_status_command(self):
        p = _build_parser()
        args = p.parse_args(["status"])
        assert args.command == "status"

    def test_freq_get(self):
        p = _build_parser()
        args = p.parse_args(["freq"])
        assert args.command == "freq"
        assert args.value is None

    def test_freq_set(self):
        p = _build_parser()
        args = p.parse_args(["freq", "14074000"])
        assert args.command == "freq"
        assert args.value == "14074000"

    def test_mode_get(self):
        p = _build_parser()
        args = p.parse_args(["mode"])
        assert args.command == "mode"
        assert args.value is None

    def test_mode_set(self):
        p = _build_parser()
        args = p.parse_args(["mode", "USB"])
        assert args.command == "mode"
        assert args.value == "USB"

    def test_power_get(self):
        p = _build_parser()
        args = p.parse_args(["power"])
        assert args.command == "power"
        assert args.value is None

    def test_power_set(self):
        p = _build_parser()
        args = p.parse_args(["power", "100"])
        assert args.command == "power"
        assert args.value == 100

    def test_meter(self):
        p = _build_parser()
        args = p.parse_args(["meter"])
        assert args.command == "meter"

    def test_audio_caps(self):
        p = _build_parser()
        args = p.parse_args(["audio", "caps"])
        assert args.command == "audio"
        assert args.audio_command == "caps"

    def test_audio_caps_json(self):
        p = _build_parser()
        args = p.parse_args(["audio", "caps", "--json"])
        assert args.command == "audio"
        assert args.audio_command == "caps"
        assert args.json is True

    def test_audio_caps_stats(self):
        p = _build_parser()
        args = p.parse_args(["audio", "caps", "--stats"])
        assert args.command == "audio"
        assert args.audio_command == "caps"
        assert args.stats is True

    def test_audio_rx(self):
        p = _build_parser()
        args = p.parse_args(["audio", "rx", "--out", "rx.wav", "--seconds", "10"])
        assert args.command == "audio"
        assert args.audio_command == "rx"
        assert args.output_file == "rx.wav"
        assert args.seconds == 10.0

    def test_audio_rx_common_flags(self):
        p = _build_parser()
        args = p.parse_args(
            [
                "audio",
                "rx",
                "--out",
                "rx.wav",
                "--sample-rate",
                "24000",
                "--channels",
                "2",
                "--json",
                "--stats",
            ]
        )
        assert args.sample_rate == 24000
        assert args.channels == 2
        assert args.json is True
        assert args.stats is True

    def test_audio_tx(self):
        p = _build_parser()
        args = p.parse_args(["audio", "tx", "--in", "tx.wav"])
        assert args.command == "audio"
        assert args.audio_command == "tx"
        assert args.input_file == "tx.wav"

    def test_audio_loopback(self):
        p = _build_parser()
        args = p.parse_args(["audio", "loopback", "--seconds", "3"])
        assert args.command == "audio"
        assert args.audio_command == "loopback"
        assert args.seconds == 3.0

    def test_ptt_on(self):
        p = _build_parser()
        args = p.parse_args(["ptt", "on"])
        assert args.command == "ptt"
        assert args.state == "on"

    def test_ptt_off(self):
        p = _build_parser()
        args = p.parse_args(["ptt", "off"])
        assert args.state == "off"

    def test_cw(self):
        p = _build_parser()
        args = p.parse_args(["cw", "CQ CQ DE KN4KYD"])
        assert args.command == "cw"
        assert args.text == "CQ CQ DE KN4KYD"

    def test_power_on(self):
        p = _build_parser()
        args = p.parse_args(["power-on"])
        assert args.command == "power-on"

    def test_power_off(self):
        p = _build_parser()
        args = p.parse_args(["power-off"])
        assert args.command == "power-off"

    def test_discover(self):
        p = _build_parser()
        args = p.parse_args(["discover"])
        assert args.command == "discover"

    def test_json_flag(self):
        p = _build_parser()
        args = p.parse_args(["status", "--json"])
        assert args.json is True

    def test_control_port_default(self):
        p = _build_parser()
        args = p.parse_args(["status"])
        assert args.control_port == 50001

    def test_control_port_override(self):
        p = _build_parser()
        args = p.parse_args(["--control-port", "50002", "status"])
        assert args.control_port == 50002

    def test_deprecated_port_sets_control_port(self):
        p = _build_parser()
        with patch("sys.stderr", new_callable=io.StringIO) as mock_stderr:
            args = p.parse_args(["--port", "9999", "status"])
        assert args.control_port == 9999
        assert "deprecated" in mock_stderr.getvalue().lower()


class TestPasswordResolution:
    """Verify password resolution: --pass (deprecated) > --pass-file > $ICOM_PASS."""

    def test_env_var_used_when_no_cli_flags(self, monkeypatch):
        monkeypatch.setenv("ICOM_PASS", "env-secret")
        p = _build_parser()
        args = p.parse_args(["status"])
        assert _resolve_password(args) == "env-secret"

    def test_empty_when_nothing_set(self, monkeypatch):
        monkeypatch.delenv("ICOM_PASS", raising=False)
        p = _build_parser()
        args = p.parse_args(["status"])
        assert _resolve_password(args) == ""

    def test_pass_file_overrides_env(self, monkeypatch, tmp_path):
        monkeypatch.setenv("ICOM_PASS", "env-secret")
        pw_file = tmp_path / "pw.txt"
        pw_file.write_text("file-secret\n", encoding="utf-8")
        p = _build_parser()
        args = p.parse_args(["--pass-file", str(pw_file), "status"])
        assert _resolve_password(args) == "file-secret"

    def test_cli_pass_overrides_pass_file_and_env(self, monkeypatch, tmp_path):
        monkeypatch.setenv("ICOM_PASS", "env-secret")
        pw_file = tmp_path / "pw.txt"
        pw_file.write_text("file-secret", encoding="utf-8")
        p = _build_parser()
        with patch("sys.stderr", new_callable=io.StringIO):
            args = p.parse_args(
                ["--pass", "cli-secret", "--pass-file", str(pw_file), "status"]
            )
        assert _resolve_password(args) == "cli-secret"

    def test_deprecation_warning_on_cli_pass(self, monkeypatch):
        monkeypatch.delenv("ICOM_PASS", raising=False)
        p = _build_parser()
        with patch("sys.stderr", new_callable=io.StringIO) as mock_stderr:
            p.parse_args(["--pass", "secret", "status"])
        err = mock_stderr.getvalue()
        assert "DeprecationWarning" in err
        assert "--pass" in err
        assert "ICOM_PASS" in err

    def test_no_warning_without_cli_pass(self, monkeypatch, tmp_path):
        monkeypatch.setenv("ICOM_PASS", "env-secret")
        p = _build_parser()
        with patch("sys.stderr", new_callable=io.StringIO) as mock_stderr:
            p.parse_args(["status"])
        assert "DeprecationWarning" not in mock_stderr.getvalue()

    def test_pass_file_missing_exits(self, tmp_path):
        p = _build_parser()
        args = p.parse_args(["--pass-file", str(tmp_path / "nope.txt"), "status"])
        with patch("sys.stderr", new_callable=io.StringIO):
            with pytest.raises(SystemExit):
                _resolve_password(args)

    def test_cli_pass_empty_string_overrides_env(self, monkeypatch):
        """--pass '' (explicit empty string) must win over $ICOM_PASS (#984)."""
        monkeypatch.setenv("ICOM_PASS", "env-secret")
        p = _build_parser()
        with patch("sys.stderr", new_callable=io.StringIO):
            args = p.parse_args(["--pass", "", "status"])
        assert _resolve_password(args) == ""

    def test_pass_file_first_line_only(self, tmp_path):
        """--pass-file returns only the first line, ignoring trailing content (#985)."""
        pw_file = tmp_path / "pw.txt"
        pw_file.write_text("real-secret\n# comment\nextra-line\n", encoding="utf-8")
        p = _build_parser()
        args = p.parse_args(["--pass-file", str(pw_file), "status"])
        assert _resolve_password(args) == "real-secret"

    def test_deprecated_port_prints_warning(self):
        p = _build_parser()
        with patch("sys.stderr", new_callable=io.StringIO) as mock_stderr:
            p.parse_args(["--port", "9999", "status"])
        assert "--control-port" in mock_stderr.getvalue()

    def test_host_override(self):
        p = _build_parser()
        args = p.parse_args(["--host", "10.0.0.1", "status"])
        assert args.host == "10.0.0.1"

    def test_timeout_override(self):
        p = _build_parser()
        args = p.parse_args(["--timeout", "10", "status"])
        assert args.timeout == 10.0

    def test_no_command_prints_help(self):
        p = _build_parser()
        args = p.parse_args([])
        assert args.command is None


class TestServeSubcommand:
    def test_serve_registered(self):
        p = _build_parser()
        args = p.parse_args(["serve"])
        assert args.command == "serve"

    def test_serve_default_host(self):
        p = _build_parser()
        args = p.parse_args(["serve"])
        assert args.serve_host == "0.0.0.0"

    def test_serve_default_port(self):
        p = _build_parser()
        args = p.parse_args(["serve"])
        assert args.serve_port == 4532

    def test_serve_host_override(self):
        p = _build_parser()
        args = p.parse_args(["serve", "--host", "127.0.0.1"])
        assert args.serve_host == "127.0.0.1"

    def test_serve_port_override(self):
        p = _build_parser()
        args = p.parse_args(["serve", "--port", "14532"])
        assert args.serve_port == 14532

    def test_serve_read_only_default(self):
        p = _build_parser()
        args = p.parse_args(["serve"])
        assert args.read_only is False

    def test_serve_read_only_flag(self):
        p = _build_parser()
        args = p.parse_args(["serve", "--read-only"])
        assert args.read_only is True

    def test_serve_max_clients(self):
        p = _build_parser()
        args = p.parse_args(["serve", "--max-clients", "5"])
        assert args.max_clients == 5

    def test_serve_max_clients_default(self):
        p = _build_parser()
        args = p.parse_args(["serve"])
        assert args.max_clients == 10

    def test_serve_cache_ttl(self):
        p = _build_parser()
        args = p.parse_args(["serve", "--cache-ttl", "1.0"])
        assert args.cache_ttl == 1.0

    def test_serve_cache_ttl_default(self):
        p = _build_parser()
        args = p.parse_args(["serve"])
        assert args.cache_ttl == 0.2

    def test_serve_radio_host_unaffected(self):
        """Radio --host is independent of serve --host."""
        p = _build_parser()
        args = p.parse_args(["--host", "192.168.1.200", "serve"])
        assert args.host == "192.168.1.200"
        assert args.serve_host == "0.0.0.0"


class TestMainEntryPoint:
    def test_no_args_prints_help(self):
        with patch("sys.argv", ["rigplane"]):
            with pytest.raises(SystemExit) as exc_info:
                main()
            assert exc_info.value.code == 0


def _run_main_serve(env_overrides: dict[str, str] | None = None) -> int:
    """Run main() for 'serve' command, intercepting os._exit and the event loop.

    main() uses asyncio.new_event_loop() + loop.run_until_complete() for serve,
    then calls os._exit(). We must intercept os._exit to capture the exit code
    and prevent process termination.
    """
    exit_code = -1

    def fake_exit(code: int) -> None:
        nonlocal exit_code
        exit_code = code
        raise SystemExit(code)

    env = {"ICOM_PID_FILE": "", **(env_overrides or {})}
    with patch.dict("os.environ", env, clear=False):
        with patch("sys.argv", ["rigplane", "--host", "127.0.0.1", "serve"]):
            with patch("rigplane.cli._run", new_callable=AsyncMock, return_value=0):
                with patch("rigplane.cli.os._exit", side_effect=fake_exit):
                    with pytest.raises(SystemExit):
                        main()
    return exit_code


class TestPidFile:
    """PID file is optional and only used for daemon-like commands (web, serve)."""

    def test_pid_file_not_created_when_icom_pid_file_unset(self):
        """Without ICOM_PID_FILE, Path.write_text is not called for PID."""
        code = _run_main_serve({"ICOM_PID_FILE": ""})
        assert code == 0

    def test_pid_file_created_for_serve_when_icom_pid_file_set(self, tmp_path):
        """With ICOM_PID_FILE set, serve writes PID to that path and removes it on exit."""
        pid_path = tmp_path / "icom.pid"
        code = _run_main_serve({"ICOM_PID_FILE": str(pid_path)})
        assert code == 0
        assert not pid_path.exists()  # removed before os._exit

    def test_pid_file_not_created_for_status_even_when_icom_pid_file_set(
        self, tmp_path
    ):
        """With ICOM_PID_FILE set, status (non-daemon) does not write a PID file."""
        pid_path = tmp_path / "icom.pid"
        with patch.dict("os.environ", {"ICOM_PID_FILE": str(pid_path)}, clear=False):
            with patch("sys.argv", ["rigplane", "--host", "127.0.0.1", "status"]):
                with patch("rigplane.cli._run", new_callable=AsyncMock, return_value=0):
                    with patch(
                        "rigplane.cli.os._exit",
                        side_effect=lambda c: (_ for _ in ()).throw(SystemExit(c)),
                    ):
                        with pytest.raises(SystemExit) as exc:
                            main()
                        assert exc.value.code == 0
        assert not pid_path.exists()


class TestBackendArgs:
    def test_backend_default_is_none(self):
        p = _build_parser()
        args = p.parse_args(["status"])
        assert args.backend is None

    def test_backend_lan_explicit(self):
        p = _build_parser()
        args = p.parse_args(["--backend", "lan", "status"])
        assert args.backend == "lan"

    def test_backend_serial(self):
        p = _build_parser()
        args = p.parse_args(
            ["--backend", "serial", "--serial-port", "/dev/tty.test", "status"]
        )
        assert args.backend == "serial"
        assert args.serial_port == "/dev/tty.test"

    def test_serial_port_flag(self):
        p = _build_parser()
        args = p.parse_args(["--serial-port", "/dev/tty.usbmodem1", "status"])
        assert args.serial_port == "/dev/tty.usbmodem1"

    def test_serial_baud_default(self):
        p = _build_parser()
        args = p.parse_args(["status"])
        assert args.serial_baud is None  # resolved per-backend in _build_backend_config

    def test_serial_baud_override(self):
        p = _build_parser()
        args = p.parse_args(["--serial-baud", "57600", "status"])
        assert args.serial_baud == 57600

    def test_serial_ptt_mode_default(self):
        p = _build_parser()
        args = p.parse_args(["status"])
        assert args.serial_ptt_mode == "civ"

    def test_serial_ptt_mode_override(self):
        p = _build_parser()
        args = p.parse_args(
            ["--serial-ptt-mode", "civ", "--backend", "serial", "status"]
        )
        assert args.serial_ptt_mode == "civ"

    def test_rx_device_default_none(self):
        p = _build_parser()
        args = p.parse_args(["status"])
        assert args.rx_device is None

    def test_rx_device_override(self):
        p = _build_parser()
        args = p.parse_args(["--rx-device", "IC-7610 USB Audio", "status"])
        assert args.rx_device == "IC-7610 USB Audio"

    def test_tx_device_default_none(self):
        p = _build_parser()
        args = p.parse_args(["status"])
        assert args.tx_device is None

    def test_tx_device_override(self):
        p = _build_parser()
        args = p.parse_args(["--tx-device", "BlackHole 2ch", "status"])
        assert args.tx_device == "BlackHole 2ch"

    def test_list_audio_devices_flag(self):
        p = _build_parser()
        args = p.parse_args(["--list-audio-devices"])
        assert args.list_audio_devices is True

    def test_list_audio_devices_default_false(self):
        p = _build_parser()
        args = p.parse_args(["status"])
        assert args.list_audio_devices is False

    def test_backend_invalid_rejected(self):
        p = _build_parser()
        with pytest.raises(SystemExit):
            p.parse_args(["--backend", "zigbee", "status"])


class TestBuildBackendConfig:
    async def test_lan_default(self):
        p = _build_parser()
        args = p.parse_args(["--host", "192.168.1.1", "status"])
        config = await _build_backend_config(args)
        assert isinstance(config, LanBackendConfig)
        assert config.backend == "lan"
        assert config.host == "192.168.1.1"
        assert config.port == 50001

    async def test_lan_preserves_user_pass(self):
        p = _build_parser()
        args = p.parse_args(
            ["--host", "10.0.0.1", "--user", "admin", "--pass", "secret", "status"]
        )
        config = await _build_backend_config(args)
        assert isinstance(config, LanBackendConfig)
        assert config.username == "admin"
        assert config.password == "secret"

    async def test_lan_custom_port(self):
        p = _build_parser()
        args = p.parse_args(["--host", "10.0.0.1", "--control-port", "50010", "status"])
        config = await _build_backend_config(args)
        assert isinstance(config, LanBackendConfig)
        assert config.port == 50010

    async def test_serial_config_built(self):
        p = _build_parser()
        args = p.parse_args(
            ["--backend", "serial", "--serial-port", "/dev/tty.usb0", "status"]
        )
        config = await _build_backend_config(args)
        assert isinstance(config, SerialBackendConfig)
        assert config.backend == "serial"
        assert config.device == "/dev/tty.usb0"
        assert config.baudrate == 115200

    async def test_serial_baud_passed(self):
        p = _build_parser()
        args = p.parse_args(
            [
                "--backend",
                "serial",
                "--serial-port",
                "/dev/tty.usb0",
                "--serial-baud",
                "9600",
                "status",
            ]
        )
        config = await _build_backend_config(args)
        assert isinstance(config, SerialBackendConfig)
        assert config.baudrate == 9600

    async def test_serial_rx_tx_device(self):
        p = _build_parser()
        args = p.parse_args(
            [
                "--backend",
                "serial",
                "--serial-port",
                "/dev/tty.usb0",
                "--rx-device",
                "IC-7610 RX",
                "--tx-device",
                "IC-7610 TX",
                "status",
            ]
        )
        config = await _build_backend_config(args)
        assert isinstance(config, SerialBackendConfig)
        assert config.rx_device == "IC-7610 RX"
        assert config.tx_device == "IC-7610 TX"

    async def test_serial_ptt_mode_passed(self):
        p = _build_parser()
        args = p.parse_args(
            [
                "--backend",
                "serial",
                "--serial-port",
                "/dev/tty.usb0",
                "--serial-ptt-mode",
                "civ",
                "status",
            ]
        )
        config = await _build_backend_config(args)
        assert isinstance(config, SerialBackendConfig)
        assert config.ptt_mode == "civ"

    async def test_serial_missing_port_triggers_discovery(self):
        """When --backend serial is set without --serial-port, auto-discovery runs."""
        p = _build_parser()
        args = p.parse_args(["--backend", "serial", "status"])
        # Discovery finds nothing → sys.exit(1)
        with patch(
            "rigplane.discovery.discover_serial_radios", AsyncMock(return_value=[])
        ):
            with pytest.raises(SystemExit):
                await _build_backend_config(args)

    async def test_serial_inferred_from_serial_port(self):
        """--serial-port without --backend infers serial backend."""
        p = _build_parser()
        args = p.parse_args(["--serial-port", "/dev/tty.usb0", "status"])
        config = await _build_backend_config(args)
        assert isinstance(config, SerialBackendConfig)
        assert config.device == "/dev/tty.usb0"


class TestAutoDiscovery:
    """Tests for auto-discovery when --host / --serial-port not provided."""

    async def test_lan_discovery_single_radio(self):
        p = _build_parser()
        args = p.parse_args(["status"])
        with patch(
            "rigplane.discovery.discover_lan_radios",
            AsyncMock(return_value=[{"host": "10.0.0.42", "remote_id": 1}]),
        ):
            config = await _build_backend_config(args)
        assert isinstance(config, LanBackendConfig)
        assert config.host == "10.0.0.42"

    async def test_lan_discovery_no_radios_exits(self):
        p = _build_parser()
        args = p.parse_args(["status"])
        with patch(
            "rigplane.discovery.discover_lan_radios",
            AsyncMock(return_value=[]),
        ):
            with pytest.raises(SystemExit):
                await _build_backend_config(args)

    async def test_lan_discovery_multiple_radios_exits(self):
        p = _build_parser()
        args = p.parse_args(["status"])
        with patch(
            "rigplane.discovery.discover_lan_radios",
            AsyncMock(
                return_value=[
                    {"host": "10.0.0.1", "remote_id": 1},
                    {"host": "10.0.0.2", "remote_id": 2},
                ]
            ),
        ):
            with pytest.raises(SystemExit):
                await _build_backend_config(args)

    async def test_explicit_host_skips_discovery(self):
        p = _build_parser()
        args = p.parse_args(["--host", "192.168.1.1", "status"])
        # No mock needed — discovery should not be called.
        config = await _build_backend_config(args)
        assert config.host == "192.168.1.1"

    async def test_backend_inferred_from_serial_port(self):
        p = _build_parser()
        args = p.parse_args(["--serial-port", "/dev/ttyUSB0", "status"])
        config = await _build_backend_config(args)
        assert isinstance(config, SerialBackendConfig)


class TestPresets:
    """Tests for --preset flag expansion."""

    async def test_preset_digimode_enables_bridge_and_wsjtx(self):
        from rigplane.cli import _apply_preset

        p = _build_parser()
        args = p.parse_args(["--host", "1.2.3.4", "web"])
        _apply_preset(args, "digimode")
        assert args.web_bridge == "auto"
        assert args.wsjtx_compat is True
        assert args.web_rigctld is True

    async def test_preset_does_not_override_explicit_flags(self):
        from rigplane.cli import _apply_preset

        p = _build_parser()
        args = p.parse_args(["--host", "1.2.3.4", "web", "--bridge", "MyDevice"])
        _apply_preset(args, "digimode")
        # User's explicit --bridge should NOT be overridden.
        assert args.web_bridge == "MyDevice"

    def test_unknown_preset_exits(self):
        from rigplane.cli import _apply_preset

        p = _build_parser()
        args = p.parse_args(["--host", "1.2.3.4", "web"])
        with pytest.raises(SystemExit):
            _apply_preset(args, "nonexistent")


class TestWebRigctldDefault:
    """Issue #1089: rigctld is on by default with --no-rigctld opt-out."""

    def test_parser_default_enables_rigctld(self):
        p = _build_parser()
        args = p.parse_args(["--host", "1.2.3.4", "web"])
        assert args.web_rigctld is True
        assert args.web_rigctld_port == 4532

    def test_parser_no_rigctld_flag_disables(self):
        p = _build_parser()
        args = p.parse_args(["--host", "1.2.3.4", "web", "--no-rigctld"])
        assert args.web_rigctld is False

    async def test_cli_web_default_rigctld(self, capsys):
        """rigctld starts by default (no flag passed) and banner shows it."""
        import asyncio

        from rigplane.cli import _cmd_web

        radio = AsyncMock()
        radio.model = "IC-7610"

        started = []

        class FakeRigctldServer:
            def __init__(self, _radio, cfg):
                self.cfg = cfg

            async def start(self):
                started.append(self.cfg.port)

            async def stop(self):
                pass

        class FakeWebServer:
            def __init__(self, _radio, _cfg):
                pass

            async def serve_forever(self):
                raise asyncio.CancelledError

        p = _build_parser()
        args = p.parse_args(["--host", "1.2.3.4", "web"])

        with (
            patch("rigplane.web.server.WebServer", FakeWebServer),
            patch("rigplane.rigctld.server.RigctldServer", FakeRigctldServer),
        ):
            rc = await _cmd_web(radio, args)

        assert rc == 0
        assert started == [4532], "rigctld should start on default port 4532"
        out = capsys.readouterr().out
        assert "rigctld:" in out
        assert "4532" in out

    async def test_cli_web_direct_lan_wsjtx_configures_data2_lan(self, capsys):
        """Direct Icom LAN backend maps WSJT-X packet mode to DATA2/LAN."""
        import asyncio

        from rigplane.cli import _cmd_web

        radio = AsyncMock()
        radio.model = "IC-7610"
        radio.backend_id = "rigplane"
        radio.profile = MagicMock(data_mode_count=3)
        captured = []

        class FakeRigctldServer:
            def __init__(self, _radio, cfg):
                self.cfg = cfg
                captured.append(cfg)

            async def start(self):
                pass

            async def stop(self):
                pass

        class FakeWebServer:
            def __init__(self, _radio, _cfg):
                pass

            async def start_audio_bridge(self, **_kwargs):
                pass

            async def serve_forever(self):
                raise asyncio.CancelledError

        p = _build_parser()
        args = p.parse_args(["--host", "1.2.3.4", "web", "--wsjtx-compat"])
        args.web_bridge = None

        with (
            patch("rigplane.web.server.WebServer", FakeWebServer),
            patch("rigplane.rigctld.server.RigctldServer", FakeRigctldServer),
            patch("rigplane.cli._detect_loopback_hint", return_value=None),
        ):
            rc = await _cmd_web(radio, args)

        assert rc == 0
        assert len(captured) == 1
        assert captured[0].wsjtx_compat is True
        assert captured[0].wsjtx_data_mode == 2
        assert captured[0].wsjtx_data_mod_input == 5
        capsys.readouterr()

    async def test_cli_web_serial_bridge_wsjtx_keeps_legacy_data1(self, capsys):
        """USB/serial backend keeps DATA1 even when local bridge is enabled."""
        import asyncio

        from rigplane.cli import _cmd_web

        radio = AsyncMock()
        radio.model = "IC-7610"
        radio.backend_id = "icom_serial"
        radio.profile = MagicMock(data_mode_count=3)
        captured = []

        class FakeRigctldServer:
            def __init__(self, _radio, cfg):
                self.cfg = cfg
                captured.append(cfg)

            async def start(self):
                pass

            async def stop(self):
                pass

        class FakeWebServer:
            def __init__(self, _radio, _cfg):
                pass

            async def start_audio_bridge(self, **_kwargs):
                pass

            async def serve_forever(self):
                raise asyncio.CancelledError

        p = _build_parser()
        args = p.parse_args(
            [
                "--backend",
                "serial",
                "--serial-port",
                "/dev/tty.usbmodem",
                "web",
                "--bridge",
                "BlackHole 16ch",
                "--wsjtx-compat",
            ]
        )

        with (
            patch("rigplane.web.server.WebServer", FakeWebServer),
            patch("rigplane.rigctld.server.RigctldServer", FakeRigctldServer),
        ):
            rc = await _cmd_web(radio, args)

        assert rc == 0
        assert len(captured) == 1
        assert captured[0].wsjtx_compat is True
        assert captured[0].wsjtx_data_mode is None
        assert captured[0].wsjtx_data_mod_input is None
        capsys.readouterr()

    async def test_cli_web_direct_lan_single_data_profile_keeps_legacy_data1(
        self, capsys
    ):
        """Single-DATA radios cannot be steered to DATA2."""
        import asyncio

        from rigplane.cli import _cmd_web

        radio = AsyncMock()
        radio.model = "IC-705"
        radio.backend_id = "rigplane"
        radio.profile = MagicMock(data_mode_count=1)
        captured = []

        class FakeRigctldServer:
            def __init__(self, _radio, cfg):
                self.cfg = cfg
                captured.append(cfg)

            async def start(self):
                pass

            async def stop(self):
                pass

        class FakeWebServer:
            def __init__(self, _radio, _cfg):
                pass

            async def start_audio_bridge(self, **_kwargs):
                pass

            async def serve_forever(self):
                raise asyncio.CancelledError

        p = _build_parser()
        args = p.parse_args(["--host", "1.2.3.4", "web", "--wsjtx-compat"])
        args.web_bridge = None

        with (
            patch("rigplane.web.server.WebServer", FakeWebServer),
            patch("rigplane.rigctld.server.RigctldServer", FakeRigctldServer),
            patch("rigplane.cli._detect_loopback_hint", return_value=None),
        ):
            rc = await _cmd_web(radio, args)

        assert rc == 0
        assert len(captured) == 1
        assert captured[0].wsjtx_compat is True
        assert captured[0].wsjtx_data_mode is None
        assert captured[0].wsjtx_data_mod_input is None
        capsys.readouterr()

    async def test_cli_web_no_rigctld_opt_out(self, capsys):
        """`--no-rigctld` disables rigctld; banner omits the rigctld line."""
        import asyncio

        from rigplane.cli import _cmd_web

        radio = AsyncMock()
        radio.model = "IC-7610"

        class ExplodingRigctldServer:
            def __init__(self, *_args, **_kwargs):
                raise AssertionError(
                    "rigctld must not be constructed when --no-rigctld is set"
                )

        class FakeWebServer:
            def __init__(self, _radio, _cfg):
                pass

            async def serve_forever(self):
                raise asyncio.CancelledError

        p = _build_parser()
        args = p.parse_args(["--host", "1.2.3.4", "web", "--no-rigctld"])

        with (
            patch("rigplane.web.server.WebServer", FakeWebServer),
            patch("rigplane.rigctld.server.RigctldServer", ExplodingRigctldServer),
        ):
            rc = await _cmd_web(radio, args)

        assert rc == 0
        out = capsys.readouterr().out
        assert "rigctld:" not in out

    async def test_cli_web_rigctld_port_busy_graceful(self, caplog, capsys):
        """EADDRINUSE on rigctld logs warning and continues serving the web."""
        import asyncio
        import errno
        import logging

        from rigplane.cli import _cmd_web

        radio = AsyncMock()
        radio.model = "IC-7610"

        class BusyRigctldServer:
            def __init__(self, _radio, cfg):
                self.cfg = cfg

            async def start(self):
                raise OSError(errno.EADDRINUSE, "Address already in use")

            async def stop(self):  # pragma: no cover - never called
                pass

        class FakeWebServer:
            def __init__(self, _radio, _cfg):
                pass

            async def serve_forever(self):
                raise asyncio.CancelledError

        p = _build_parser()
        args = p.parse_args(["--host", "1.2.3.4", "web"])

        with (
            patch("rigplane.web.server.WebServer", FakeWebServer),
            patch("rigplane.rigctld.server.RigctldServer", BusyRigctldServer),
            caplog.at_level(logging.WARNING, logger="rigplane.cli"),
        ):
            rc = await _cmd_web(radio, args)

        assert rc == 0, "web must keep running even if rigctld port is busy"
        # Warning must mention the port and surface the problem.
        assert any(
            "rigctld" in rec.message.lower() and "4532" in rec.message
            for rec in caplog.records
        ), (
            f"expected rigctld port-busy warning, got {[r.message for r in caplog.records]}"
        )
        out = capsys.readouterr().out
        assert "rigctld:" not in out, (
            "banner must not advertise rigctld when bind failed"
        )

    async def test_cli_web_rigctld_eacces_surfaces(self, caplog):
        """EACCES (privileged port) must NOT degrade silently — surfaces as error."""
        import errno
        import logging

        from rigplane.cli import _cmd_web

        radio = AsyncMock()
        radio.model = "IC-7610"

        class PrivilegedRigctldServer:
            def __init__(self, _radio, cfg):
                self.cfg = cfg

            async def start(self):
                raise OSError(errno.EACCES, "Permission denied")

            async def stop(self):  # pragma: no cover - never called
                pass

        class FakeWebServer:  # pragma: no cover - rigctld fails before web starts
            def __init__(self, _radio, _cfg):
                pass

            async def serve_forever(self):
                raise AssertionError("web must not start when rigctld fails hard")

        p = _build_parser()
        args = p.parse_args(["--host", "1.2.3.4", "web"])

        with (
            patch("rigplane.web.server.WebServer", FakeWebServer),
            patch("rigplane.rigctld.server.RigctldServer", PrivilegedRigctldServer),
            caplog.at_level(logging.ERROR, logger="rigplane.cli"),
        ):
            with pytest.raises(OSError) as exc_info:
                await _cmd_web(radio, args)

        assert exc_info.value.errno == errno.EACCES
        # ERROR-level log must surface the failure — no silent degrade.
        assert any(
            "rigctld" in rec.message.lower() and rec.levelno >= logging.ERROR
            for rec in caplog.records
        ), (
            "expected ERROR log for EACCES, got "
            f"{[(r.levelname, r.message) for r in caplog.records]}"
        )

    async def test_cli_web_rigctld_emfile_surfaces(self, caplog):
        """EMFILE (fd exhaustion) must NOT degrade silently — surfaces as error."""
        import errno
        import logging

        from rigplane.cli import _cmd_web

        radio = AsyncMock()
        radio.model = "IC-7610"

        class ExhaustedRigctldServer:
            def __init__(self, _radio, cfg):
                self.cfg = cfg

            async def start(self):
                raise OSError(errno.EMFILE, "Too many open files")

            async def stop(self):  # pragma: no cover - never called
                pass

        class FakeWebServer:  # pragma: no cover - rigctld fails before web starts
            def __init__(self, _radio, _cfg):
                pass

            async def serve_forever(self):
                raise AssertionError("web must not start when rigctld fails hard")

        p = _build_parser()
        args = p.parse_args(["--host", "1.2.3.4", "web"])

        with (
            patch("rigplane.web.server.WebServer", FakeWebServer),
            patch("rigplane.rigctld.server.RigctldServer", ExhaustedRigctldServer),
            caplog.at_level(logging.ERROR, logger="rigplane.cli"),
        ):
            with pytest.raises(OSError) as exc_info:
                await _cmd_web(radio, args)

        assert exc_info.value.errno == errno.EMFILE
        assert any(
            "rigctld" in rec.message.lower() and rec.levelno >= logging.ERROR
            for rec in caplog.records
        ), (
            "expected ERROR log for EMFILE, got "
            f"{[(r.levelname, r.message) for r in caplog.records]}"
        )


class TestBackendAwareDiscover:
    def test_discover_serial_exits_with_error(self, capsys):
        with patch("sys.argv", ["rigplane", "--backend", "serial", "discover"]):
            with patch(
                "rigplane.discovery.discover_lan_radios", AsyncMock(return_value=[])
            ):
                with patch(
                    "rigplane.discovery.discover_serial_radios",
                    AsyncMock(return_value=[]),
                ):
                    with pytest.raises(SystemExit) as exc_info:
                        main()
        assert exc_info.value.code == 0
        captured = capsys.readouterr()
        assert "Scanning for Icom radios" in captured.out
        assert "serial" in captured.out.lower()

    def test_discover_serial_error_mentions_lan(self, capsys):
        with patch("sys.argv", ["rigplane", "--backend", "serial", "discover"]):
            with patch(
                "rigplane.discovery.discover_lan_radios", AsyncMock(return_value=[])
            ):
                with patch(
                    "rigplane.discovery.discover_serial_radios",
                    AsyncMock(return_value=[]),
                ):
                    with pytest.raises(SystemExit) as exc_info:
                        main()
        assert exc_info.value.code == 0
        captured = capsys.readouterr()
        assert "Scanning for Icom radios" in captured.out
        assert "lan" in captured.out.lower()


class TestListAudioDevices:
    def test_list_audio_devices_missing_sounddevice(self, capsys):
        import builtins

        real_import = builtins.__import__

        def mock_import(name, *args, **kwargs):
            if name == "sounddevice":
                raise ImportError("No module named 'sounddevice'")
            return real_import(name, *args, **kwargs)

        with patch("builtins.__import__", side_effect=mock_import):
            with patch("sys.argv", ["rigplane", "--list-audio-devices"]):
                with pytest.raises(SystemExit) as exc_info:
                    main()
        assert exc_info.value.code == 1
        captured = capsys.readouterr()
        assert "sounddevice" in captured.err

    def test_list_audio_devices_plain_output(self, capsys):
        async def fake_list_cmd(args):
            print("2 audio device(s):")
            print("  [0] IC-7610 USB Audio  (in=1, out=1)")
            print("  [1] Built-in Mic  (in=1, out=0)")
            return 0

        with patch("rigplane.cli._cmd_list_audio_devices", side_effect=fake_list_cmd):
            with patch("sys.argv", ["rigplane", "--list-audio-devices"]):
                with pytest.raises(SystemExit) as exc_info:
                    main()
        assert exc_info.value.code == 0
        captured = capsys.readouterr()
        assert "IC-7610 USB Audio" in captured.out

    def test_list_audio_devices_json_flag_is_parser_reachable(self):
        captured: dict[str, object] = {}

        async def fake_list_cmd(args):
            captured["json"] = getattr(args, "json", False)
            return 0

        with patch("rigplane.cli._cmd_list_audio_devices", side_effect=fake_list_cmd):
            with patch("sys.argv", ["rigplane", "--list-audio-devices", "--json"]):
                with pytest.raises(SystemExit) as exc_info:
                    main()
        assert exc_info.value.code == 0
        assert captured["json"] is True

    def test_list_audio_devices_json_output(self, capsys):
        import asyncio
        import argparse as _ap
        import json as json_module
        from rigplane.cli import _cmd_list_audio_devices

        mock_sd = MagicMock()
        mock_sd.query_devices.return_value = [
            {
                "index": 0,
                "name": "IC-7610 USB Audio",
                "max_input_channels": 1,
                "max_output_channels": 1,
                "default_samplerate": 48000,
            },
        ]
        mock_sd.default = MagicMock()
        mock_sd.default.device = [-1, -1]

        test_args = _ap.Namespace(json=True)

        with patch.dict("sys.modules", {"sounddevice": mock_sd}):
            result = asyncio.run(_cmd_list_audio_devices(test_args))

        assert result == 0
        captured = capsys.readouterr()
        data = json_module.loads(captured.out.strip())
        assert data[0]["name"] == "IC-7610 USB Audio"
        assert data[0]["index"] == 0


class TestCheckPortsAvailable:
    def test_free_port_does_not_raise(self):
        import socket

        # Find a free port dynamically.
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.bind(("", 0))
            port = s.getsockname()[1]
        # Port is free now — should not raise.
        check_ports_available([port])

    def test_occupied_port_raises_runtime_error(self):
        import socket

        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            s.bind(("", 0))
            s.listen(1)
            occupied_port = s.getsockname()[1]
            with pytest.raises(
                RuntimeError, match=f"Port {occupied_port} already in use"
            ):
                check_ports_available([occupied_port])

    def test_error_message_includes_port_number(self):
        import socket

        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            s.bind(("", 0))
            s.listen(1)
            port = s.getsockname()[1]
            try:
                check_ports_available([port])
            except RuntimeError as exc:
                assert str(port) in str(exc)
            else:
                pytest.fail("Expected RuntimeError")

    def test_empty_list_does_not_raise(self):
        check_ports_available([])

    def test_web_command_preflight_blocks_on_occupied_port(self, capsys):
        """_run() exits 1 with error before connecting to radio when port is occupied."""
        import asyncio
        import socket

        from rigplane.cli import _run

        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            s.bind(("", 0))
            s.listen(1)
            occupied_port = s.getsockname()[1]

            args = _build_parser().parse_args(
                [
                    "--host",
                    "192.168.1.100",
                    "web",
                    "--port",
                    str(occupied_port),
                ]
            )

            mock_radio = MagicMock()
            mock_radio.__aenter__ = AsyncMock(return_value=mock_radio)
            mock_radio.__aexit__ = AsyncMock(return_value=False)
            with patch("rigplane.cli.create_radio", return_value=mock_radio):
                result = asyncio.run(_run(args))

        # Radio should never have connected — port check exits before async with.
        mock_radio.__aenter__.assert_not_called()
        assert result == 1
        captured = capsys.readouterr()
        assert str(occupied_port) in captured.err
