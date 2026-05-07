"""Additional coverage tests for rigplane.cli."""

from __future__ import annotations

import argparse
import asyncio
import json
import types
import wave
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from _caps import FULL_ICOM_CAPS
from rigplane.backends.config import SerialBackendConfig
from rigplane.cli import (
    _build_parser,
    _cmd_att,
    _cmd_audio_loopback,
    _cmd_audio_rx,
    _cmd_audio_tx,
    _cmd_discover,
    _cmd_preamp,
    _cmd_scope,
    _cmd_serve,
    _cmd_tuner,
    _cmd_web,
    _emit_audio_result,
    _run,
    _validate_audio_format_args,
    main,
)
from rigplane.discovery import RadioDiscoveryResult
from rigplane.radio_protocol import (
    AdvancedControlCapable,
    AudioCapable,
    PowerControlCapable,
    ScopeCapable,
)
from rigplane.scope import ScopeFrame


class _CapableRadio(SimpleNamespace):
    async def __aenter__(self) -> "_CapableRadio":
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:
        _ = (exc_type, exc, tb)


AudioCapable.register(_CapableRadio)
ScopeCapable.register(_CapableRadio)
AdvancedControlCapable.register(_CapableRadio)
PowerControlCapable.register(_CapableRadio)


def _run_args(**overrides: object) -> argparse.Namespace:
    base = {
        "host": "127.0.0.1",
        "control_port": 50001,
        "user": "",
        "password": "",
        "timeout": 1.0,
        "json": False,
        "stats": False,
        "command": "status",
    }
    base.update(overrides)
    return argparse.Namespace(**base)


def _make_full_capable_mock() -> _CapableRadio:
    """Return explicit test double that satisfies capability protocols.

    All attrs must be explicitly set; Python 3.12+ runtime_checkable Protocol uses
    inspect.getattr_static which bypasses MagicMock.__getattr__.
    """
    radio = _CapableRadio()
    radio.capabilities = set(FULL_ICOM_CAPS)
    # AudioCapable
    radio.audio_bus = MagicMock()
    radio.start_audio_rx_opus = AsyncMock()
    radio.stop_audio_rx_opus = AsyncMock()
    radio.push_audio_tx_opus = AsyncMock()
    radio.start_audio_rx_pcm = AsyncMock()
    radio.stop_audio_rx_pcm = AsyncMock()
    radio.start_audio_tx_pcm = AsyncMock()
    radio.push_audio_tx_pcm = AsyncMock()
    radio.stop_audio_tx_pcm = AsyncMock()
    radio.get_audio_stats = AsyncMock(return_value={})
    radio.start_audio_tx_opus = AsyncMock()
    radio.stop_audio_tx_opus = AsyncMock()
    # ScopeCapable
    radio.enable_scope = AsyncMock()
    radio.disable_scope = AsyncMock()
    radio.on_scope_data = MagicMock()
    radio.capture_scope_frame = AsyncMock()
    radio.capture_scope_frames = AsyncMock()
    radio.set_scope_during_tx = AsyncMock()
    radio.set_scope_center_type = AsyncMock()
    radio.set_scope_fixed_edge = AsyncMock()
    # AdvancedControlCapable
    radio.send_cw_text = AsyncMock()
    radio.set_attenuator = AsyncMock()
    radio.set_attenuator_level = AsyncMock()
    radio.get_attenuator_level = AsyncMock(return_value=0)
    radio.set_preamp = AsyncMock()
    radio.get_preamp = AsyncMock(return_value=0)
    radio.set_antenna_1 = AsyncMock()
    radio.set_antenna_2 = AsyncMock()
    radio.set_rx_antenna_ant1 = AsyncMock()
    radio.set_rx_antenna_ant2 = AsyncMock()
    radio.get_antenna_1 = AsyncMock(return_value=0)
    radio.get_antenna_2 = AsyncMock(return_value=0)
    radio.get_rx_antenna_ant1 = AsyncMock(return_value=0)
    radio.get_rx_antenna_ant2 = AsyncMock(return_value=0)
    radio.set_system_date = AsyncMock()
    radio.get_system_date = AsyncMock(return_value=(2026, 1, 1))
    radio.set_system_time = AsyncMock()
    radio.get_system_time = AsyncMock(return_value=(0, 0))
    radio.set_dual_watch = AsyncMock()
    radio.get_dual_watch = AsyncMock(return_value=False)
    radio.set_tuner_status = AsyncMock()
    radio.get_tuner_status = AsyncMock(return_value=False)
    radio.set_acc1_mod_level = AsyncMock()
    radio.set_usb_mod_level = AsyncMock()
    radio.set_lan_mod_level = AsyncMock()
    radio.set_compressor = AsyncMock()
    radio.set_nb = AsyncMock()
    radio.set_nr = AsyncMock()
    radio.set_ip_plus = AsyncMock()
    radio.set_digisel = AsyncMock()
    radio.set_filter = AsyncMock()
    return radio


def _mock_radio_ctx() -> tuple[MagicMock, _CapableRadio]:
    radio_cls = MagicMock()
    radio = _make_full_capable_mock()
    radio_cls.return_value = radio
    return radio_cls, radio


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("command", "handler_name"),
    [
        ("status", "_cmd_status"),
        ("freq", "_cmd_freq"),
        ("mode", "_cmd_mode"),
        ("power", "_cmd_power"),
        ("meter", "_cmd_meter"),
        ("ptt", "_cmd_ptt"),
        ("cw", "_cmd_cw"),
        ("att", "_cmd_att"),
        ("preamp", "_cmd_preamp"),
        ("web", "_cmd_web"),
        ("scope", "_cmd_scope"),
        ("serve", "_cmd_serve"),
    ],
)
async def test_run_dispatches_non_audio_commands(
    command: str, handler_name: str
) -> None:
    args = _run_args(command=command)
    if command == "ptt":
        args.state = "on"
    if command == "cw":
        args.text = "CQ"
    if command in {"freq", "mode", "power", "att", "preamp"}:
        args.value = None
    if command == "web":
        args.web_host = "127.0.0.1"
        args.web_port = 8080
        args.web_static_dir = None
    if command == "scope":
        args.frames = 1
        args.width = 800
        args.capture_timeout = 1.0
        args.spectrum_only = True
        args.output = "x.png"
        args.theme = "classic"
    if command == "serve":
        args.serve_host = "127.0.0.1"
        args.serve_port = 4532
        args.read_only = False
        args.max_clients = 1
        args.cache_ttl = 0.1
        args.wsjtx_compat = False
        args.log_level = "INFO"
        args.audit_log = None
        args.rate_limit = None

    _, radio = _mock_radio_ctx()
    with (
        patch("rigplane.cli.create_radio", return_value=radio),
        patch("rigplane.cli.check_ports_available"),
        patch(f"rigplane.cli.{handler_name}", new_callable=AsyncMock) as handler,
    ):
        handler.return_value = 7
        rc = await _run(args)

    assert rc == 7
    handler.assert_awaited_once_with(radio, args)


@pytest.mark.asyncio
async def test_run_web_uses_serial_backend_factory_config() -> None:
    args = _run_args(
        command="web",
        backend="serial",
        serial_port="/dev/tty.usbmodem-IC7610",
        serial_baud=115200,
        serial_ptt_mode="civ",
        rx_device=None,
        tx_device=None,
        web_host="127.0.0.1",
        web_port=8080,
        web_static_dir=None,
        web_bridge=None,
        web_bridge_tx_device=None,
        web_bridge_rx_only=False,
        web_rigctld=False,
        dx_cluster=None,
        callsign=None,
    )
    _, radio = _mock_radio_ctx()
    with (
        patch("rigplane.cli.create_radio", return_value=radio) as create_radio,
        patch("rigplane.cli.check_ports_available"),
        patch("rigplane.cli._cmd_web", new_callable=AsyncMock) as cmd_web,
    ):
        cmd_web.return_value = 0
        rc = await _run(args)

    assert rc == 0
    create_radio.assert_called_once_with(
        SerialBackendConfig(
            device="/dev/tty.usbmodem-IC7610",
            baudrate=115200,
            timeout=1.0,
            rx_device=None,
            tx_device=None,
            ptt_mode="civ",
        )
    )
    cmd_web.assert_awaited_once_with(radio, args)


@pytest.mark.asyncio
async def test_run_serve_uses_serial_backend_factory_config() -> None:
    args = _run_args(
        command="serve",
        backend="serial",
        serial_port="/dev/tty.usbmodem-IC7610",
        serial_baud=115200,
        serial_ptt_mode="civ",
        rx_device="IC-7610 USB Audio RX",
        tx_device="IC-7610 USB Audio TX",
        serve_host="127.0.0.1",
        serve_port=4532,
        read_only=False,
        max_clients=4,
        cache_ttl=0.1,
        wsjtx_compat=False,
        log_level="INFO",
        audit_log=None,
        rate_limit=None,
    )
    _, radio = _mock_radio_ctx()
    with (
        patch("rigplane.cli.create_radio", return_value=radio) as create_radio,
        patch("rigplane.cli.check_ports_available"),
        patch("rigplane.cli._cmd_serve", new_callable=AsyncMock) as cmd_serve,
    ):
        cmd_serve.return_value = 0
        rc = await _run(args)

    assert rc == 0
    create_radio.assert_called_once_with(
        SerialBackendConfig(
            device="/dev/tty.usbmodem-IC7610",
            baudrate=115200,
            timeout=1.0,
            rx_device="IC-7610 USB Audio RX",
            tx_device="IC-7610 USB Audio TX",
            ptt_mode="civ",
        )
    )
    cmd_serve.assert_awaited_once_with(radio, args)


@pytest.mark.asyncio
async def test_run_dispatches_power_on_off_and_unknown_paths(
    capsys: pytest.CaptureFixture[str],
) -> None:
    _, radio = _mock_radio_ctx()
    radio.get_powerstat = AsyncMock(return_value=True)
    radio.set_powerstat = AsyncMock()
    radio.set_rf_power = AsyncMock()
    with patch("rigplane.cli.create_radio", return_value=radio):
        rc_on = await _run(_run_args(command="power-on"))
        rc_off = await _run(_run_args(command="power-off"))
    assert rc_on == 0
    assert rc_off == 0
    radio.set_powerstat.assert_any_await(True)
    radio.set_powerstat.assert_any_await(False)

    bad_audio = _run_args(command="audio", audio_command="invalid")
    with patch("rigplane.cli.create_radio", return_value=radio):
        rc_bad = await _run(bad_audio)
    assert rc_bad == 1
    assert "unknown audio command" in capsys.readouterr().err.lower()

    with (
        patch("rigplane.cli.create_radio", return_value=radio),
        patch("rigplane.cli._cmd_status", new_callable=AsyncMock) as status_cmd,
    ):
        status_cmd.return_value = 9
        rc_fallback = await _run(_run_args(command="something-else"))
    assert rc_fallback == 9
    status_cmd.assert_awaited()


def test_validate_audio_format_negative_values() -> None:
    assert _validate_audio_format_args(0, 1) == "--sample-rate must be > 0."
    assert _validate_audio_format_args(48000, 0) == "--channels must be > 0."


def test_emit_audio_result_text_stats(capsys: pytest.CaptureFixture[str]) -> None:
    args = argparse.Namespace(json=False, stats=True)
    _emit_audio_result(args, message="done", payload={"tx_frames": 3, "bytes": 100})
    out = capsys.readouterr().out
    assert "done" in out
    assert "tx_frames: 3" in out
    assert "bytes: 100" in out


def _make_audio_capable_mock(base: MagicMock | None = None) -> MagicMock:
    """Return a mock that satisfies isinstance(..., AudioCapable) in CLI.

    All attrs must be explicitly set; Python 3.12+ runtime_checkable Protocol uses
    inspect.getattr_static which bypasses MagicMock.__getattr__.
    """
    radio = base if base is not None else AsyncMock()
    radio.capabilities = set(FULL_ICOM_CAPS)
    radio.audio_bus = MagicMock()
    radio.start_audio_rx_opus = AsyncMock()
    radio.stop_audio_rx_opus = AsyncMock()
    radio.push_audio_tx_opus = AsyncMock()
    radio.start_audio_rx_pcm = AsyncMock()
    radio.stop_audio_rx_pcm = AsyncMock()
    radio.start_audio_tx_pcm = AsyncMock()
    radio.push_audio_tx_pcm = AsyncMock()
    radio.stop_audio_tx_pcm = AsyncMock()
    radio.get_audio_stats = AsyncMock(return_value={})
    radio.start_audio_tx_opus = AsyncMock()
    radio.stop_audio_tx_opus = AsyncMock()
    return radio


async def test_cmd_audio_rx_stop_failure_and_write_failure(
    tmp_path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    radio = _make_audio_capable_mock(AsyncMock())

    async def start_rx(cb, **_kwargs):
        cb(b"\x01\x02" * 960)

    radio.start_audio_rx_pcm = AsyncMock(side_effect=start_rx)
    radio.stop_audio_rx_pcm = AsyncMock(side_effect=RuntimeError("stop failed"))
    args_ok = argparse.Namespace(
        output_file=str(tmp_path / "rx.wav"),
        seconds=0.001,
        sample_rate=48000,
        channels=1,
        json=False,
        stats=False,
    )
    with patch("rigplane.cli.logger.debug") as log_debug:
        rc = await _cmd_audio_rx(radio, args_ok)
    assert rc == 0
    assert "Saved RX audio" in capsys.readouterr().out
    assert log_debug.called

    args_bad = argparse.Namespace(
        output_file=str(tmp_path / "bad.wav"),
        seconds=0.001,
        sample_rate=48000,
        channels=1,
        json=False,
        stats=False,
    )
    with patch("rigplane.cli.wave.open", side_effect=OSError("nope")):
        rc_bad = await _cmd_audio_rx(radio, args_bad)
    assert rc_bad == 1
    assert "failed to write wav file" in capsys.readouterr().err.lower()


@pytest.mark.asyncio
async def test_cmd_audio_tx_error_branches_and_padding(
    capsys: pytest.CaptureFixture[str],
) -> None:
    radio = _make_audio_capable_mock(AsyncMock())
    radio.start_audio_tx_pcm = AsyncMock()
    radio.stop_audio_tx_pcm = AsyncMock()
    radio.push_audio_tx_pcm = AsyncMock()

    bad_fmt = argparse.Namespace(
        input_file="x.wav",
        sample_rate=0,
        channels=1,
        json=False,
        stats=False,
    )
    assert await _cmd_audio_tx(radio, bad_fmt) == 1

    args = argparse.Namespace(
        input_file="x.wav",
        sample_rate=48000,
        channels=1,
        json=False,
        stats=False,
    )
    with patch("rigplane.cli._load_wav_pcm", side_effect=wave.Error("bad wav")):
        assert await _cmd_audio_tx(radio, args) == 1
    with patch("rigplane.cli._load_wav_pcm", side_effect=RuntimeError("boom")):
        assert await _cmd_audio_tx(radio, args) == 1
    with patch("rigplane.cli._load_wav_pcm", return_value=(48000, 1, 1, b"\x00")):
        assert await _cmd_audio_tx(radio, args) == 1
    with patch("rigplane.cli._load_wav_pcm", return_value=(48000, 1, 2, b"")):
        assert await _cmd_audio_tx(radio, args) == 1

    short_pcm = b"\x01\x02" * 100
    with (
        patch("rigplane.cli._load_wav_pcm", return_value=(48000, 1, 2, short_pcm)),
        patch("rigplane.cli.asyncio.sleep", new=AsyncMock()),
    ):
        rc_ok = await _cmd_audio_tx(radio, args)
    assert rc_ok == 0
    sent_frame = radio.push_audio_tx_pcm.await_args.args[0]
    assert len(sent_frame) == 1920  # padded to one full 20ms frame

    radio.stop_audio_tx_pcm = AsyncMock(side_effect=RuntimeError("stop"))
    with (
        patch("rigplane.cli._load_wav_pcm", return_value=(48000, 1, 2, short_pcm)),
        patch("rigplane.cli.asyncio.sleep", new=AsyncMock()),
        patch("rigplane.cli.logger.debug") as log_debug,
    ):
        rc_stop_err = await _cmd_audio_tx(radio, args)
    assert rc_stop_err == 0
    assert log_debug.called
    assert "transmitted wav audio" in capsys.readouterr().out.lower()


@pytest.mark.asyncio
async def test_cmd_audio_loopback_queue_full_and_worker_error(
    capsys: pytest.CaptureFixture[str],
) -> None:
    radio = _make_audio_capable_mock(AsyncMock())
    frame = b"\x01\x02" * 960

    async def start_rx(cb, **_kwargs):
        for _ in range(600):
            cb(frame)

    radio.start_audio_rx_pcm = AsyncMock(side_effect=start_rx)
    radio.stop_audio_rx_pcm = AsyncMock()
    radio.start_audio_tx_pcm = AsyncMock()
    radio.stop_audio_tx_pcm = AsyncMock()
    radio.push_audio_tx_pcm = AsyncMock()
    args = argparse.Namespace(
        seconds=0.01,
        sample_rate=48000,
        channels=1,
        json=True,
        stats=False,
    )
    # Patch asyncio.sleep to skip tx_worker frame-interval delays (0.02s × N frames)
    _real_sleep = asyncio.sleep

    async def _no_frame_sleep(delay, *a, **kw):
        if delay <= 0.02:
            return
        return await _real_sleep(delay, *a, **kw)

    with patch("asyncio.sleep", _no_frame_sleep):
        rc = await _cmd_audio_loopback(radio, args)
    assert rc == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["dropped_frames"] > 0

    args_bad = argparse.Namespace(
        seconds=0.0,
        sample_rate=48000,
        channels=1,
        json=False,
        stats=False,
    )
    assert await _cmd_audio_loopback(radio, args_bad) == 1

    async def start_rx_one(cb, **_kwargs):
        cb(frame)

    radio.start_audio_rx_pcm = AsyncMock(side_effect=start_rx_one)
    radio.push_audio_tx_pcm = AsyncMock(side_effect=RuntimeError("tx failed"))
    radio.stop_audio_rx_pcm = AsyncMock(side_effect=RuntimeError("rx stop failed"))
    radio.stop_audio_tx_pcm = AsyncMock(side_effect=RuntimeError("tx stop failed"))
    with patch("rigplane.cli.logger.debug") as log_debug:
        with pytest.raises(RuntimeError, match="tx failed"):
            await _cmd_audio_loopback(radio, args)
    assert log_debug.call_count >= 2


@pytest.mark.asyncio
async def test_cmd_att_and_preamp_all_paths(capsys: pytest.CaptureFixture[str]) -> None:
    radio = AsyncMock()
    radio.get_attenuator_level = AsyncMock(return_value=0)
    radio.get_preamp = AsyncMock(return_value=2)

    assert await _cmd_att(radio, argparse.Namespace(value="on", json=False)) == 0
    assert await _cmd_att(radio, argparse.Namespace(value="off", json=False)) == 0
    assert await _cmd_att(radio, argparse.Namespace(value="12", json=False)) == 0
    assert await _cmd_att(radio, argparse.Namespace(value=None, json=False)) == 0

    radio.get_attenuator_level = AsyncMock(return_value=6)
    assert await _cmd_att(radio, argparse.Namespace(value=None, json=True)) == 0

    assert await _cmd_preamp(radio, argparse.Namespace(value="off", json=False)) == 0
    assert await _cmd_preamp(radio, argparse.Namespace(value="1", json=False)) == 0
    assert await _cmd_preamp(radio, argparse.Namespace(value=None, json=False)) == 0
    assert await _cmd_preamp(radio, argparse.Namespace(value=None, json=True)) == 0
    output = capsys.readouterr().out
    assert "Attenuator" in output
    assert "Preamp" in output


def _make_scope_capable_mock(base: MagicMock | None = None) -> MagicMock:
    """Return a mock that satisfies isinstance(..., ScopeCapable) in CLI.

    All attrs must be explicitly set; Python 3.12+ runtime_checkable Protocol uses
    inspect.getattr_static which bypasses MagicMock.__getattr__.
    """
    radio = base if base is not None else AsyncMock()
    radio.capabilities = set(FULL_ICOM_CAPS)
    radio.enable_scope = AsyncMock()
    radio.disable_scope = AsyncMock()
    radio.on_scope_data = MagicMock()
    radio.capture_scope_frame = AsyncMock()
    radio.capture_scope_frames = AsyncMock()
    radio.set_scope_during_tx = AsyncMock()
    radio.set_scope_center_type = AsyncMock()
    radio.set_scope_fixed_edge = AsyncMock()
    return radio


async def test_cmd_scope_json_image_and_error_paths(
    monkeypatch, capsys: pytest.CaptureFixture[str]
) -> None:
    radio = _make_scope_capable_mock(AsyncMock())
    frame = ScopeFrame(0, 1, 14_000_000, 14_350_000, b"\x01\x02", False)
    radio.capture_scope_frame = AsyncMock(return_value=frame)
    radio.capture_scope_frames = AsyncMock(return_value=[frame, frame])
    radio.disable_scope = AsyncMock()

    json_spectrum = argparse.Namespace(
        frames=1,
        width=800,
        capture_timeout=None,
        json=True,
        spectrum_only=True,
        output="scope.png",
        theme="classic",
    )
    assert await _cmd_scope(radio, json_spectrum) == 0
    assert "start_freq_hz" in capsys.readouterr().out

    mod = types.ModuleType("rigplane.scope_render")
    img = MagicMock()
    mod.render_spectrum = MagicMock(return_value=img)
    mod.render_scope_image = MagicMock()
    monkeypatch.setitem(__import__("sys").modules, "rigplane.scope_render", mod)
    image_spectrum = argparse.Namespace(
        frames=1,
        width=800,
        capture_timeout=0.5,
        json=False,
        spectrum_only=True,
        output="out.png",
        theme="classic",
    )
    assert await _cmd_scope(radio, image_spectrum) == 0
    img.save.assert_called_once_with("out.png", "PNG")

    json_waterfall = argparse.Namespace(
        frames=2,
        width=800,
        capture_timeout=0.5,
        json=True,
        spectrum_only=False,
        output="out.png",
        theme="classic",
    )
    assert await _cmd_scope(radio, json_waterfall) == 0
    waterfall_out = capsys.readouterr().out.strip().splitlines()[-1]
    assert waterfall_out.startswith("[")

    image_waterfall = argparse.Namespace(
        frames=2,
        width=800,
        capture_timeout=0.5,
        json=False,
        spectrum_only=False,
        output="wf.png",
        theme="grayscale",
    )
    assert await _cmd_scope(radio, image_waterfall) == 0
    mod.render_scope_image.assert_called_once()

    bad = argparse.Namespace(
        frames=1,
        width=800,
        capture_timeout=0.5,
        json=True,
        spectrum_only=True,
        output="x.png",
        theme="classic",
    )
    radio.capture_scope_frame = AsyncMock(side_effect=RuntimeError("capture failed"))
    radio.disable_scope = AsyncMock(side_effect=RuntimeError("disable failed"))
    with patch("rigplane.cli.logger.debug") as log_debug:
        assert await _cmd_scope(radio, bad) == 1
    assert log_debug.called


@pytest.mark.asyncio
async def test_cmd_scope_validation_and_import_error(
    monkeypatch, capsys: pytest.CaptureFixture[str]
) -> None:
    radio = _make_scope_capable_mock(AsyncMock())
    radio.disable_scope = AsyncMock()

    assert (
        await _cmd_scope(
            radio,
            argparse.Namespace(
                frames=0,
                width=800,
                capture_timeout=None,
                json=True,
                spectrum_only=True,
                output="x.png",
                theme="classic",
            ),
        )
        == 1
    )
    assert (
        await _cmd_scope(
            radio,
            argparse.Namespace(
                frames=1,
                width=10,
                capture_timeout=None,
                json=True,
                spectrum_only=True,
                output="x.png",
                theme="classic",
            ),
        )
        == 1
    )
    assert (
        await _cmd_scope(
            radio,
            argparse.Namespace(
                frames=1,
                width=800,
                capture_timeout=0,
                json=True,
                spectrum_only=True,
                output="x.png",
                theme="classic",
            ),
        )
        == 1
    )

    real_import = __import__

    def bad_import(name, *args, **kwargs):
        if name.endswith("scope_render"):
            raise ImportError("missing pillow")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr("builtins.__import__", bad_import)
    rc = await _cmd_scope(
        radio,
        argparse.Namespace(
            frames=1,
            width=800,
            capture_timeout=0.5,
            json=False,
            spectrum_only=True,
            output="x.png",
            theme="classic",
        ),
    )
    assert rc == 1
    assert "missing pillow" in capsys.readouterr().err


@pytest.mark.asyncio
async def test_cmd_serve_and_cmd_web_paths(
    tmp_path, capsys: pytest.CaptureFixture[str]
) -> None:
    radio = AsyncMock()

    class FakeRigctldServer:
        def __init__(self, _radio, _cfg):
            self.radio = _radio
            self.cfg = _cfg

        async def serve_forever(self):
            raise asyncio.CancelledError

    audit_logger = MagicMock()
    icom_logger = MagicMock()
    with (
        patch("rigplane.rigctld.server.RigctldServer", FakeRigctldServer),
        patch("logging.FileHandler", return_value=MagicMock()),
        patch("logging.getLogger", side_effect=[icom_logger, audit_logger]),
    ):
        args = argparse.Namespace(
            serve_host="127.0.0.1",
            serve_port=5555,
            read_only=True,
            max_clients=5,
            cache_ttl=0.2,
            wsjtx_compat=True,
            log_level="DEBUG",
            audit_log=str(tmp_path / "audit.jsonl"),
            rate_limit=10.0,
        )
        assert await _cmd_serve(radio, args) == 0
    assert audit_logger.addHandler.called
    assert "127.0.0.1:5555" in capsys.readouterr().out

    class FakeWebServer:
        def __init__(self, _radio, cfg):
            self.radio = _radio
            self.cfg = cfg

        async def serve_forever(self):
            raise asyncio.CancelledError

    with patch("rigplane.web.server.WebServer", FakeWebServer):
        args = argparse.Namespace(
            web_host="127.0.0.1", web_port=9090, web_static_dir=None
        )
        assert await _cmd_web(radio, args) == 0
        args2 = argparse.Namespace(
            web_host="127.0.0.1",
            web_port=9091,
            web_static_dir=str(tmp_path),
        )
        assert await _cmd_web(radio, args2) == 0


def _web_cmd_args(*, web_bridge: str | None) -> argparse.Namespace:
    """Build a minimal Namespace for _cmd_web with bridge-related fields."""
    return argparse.Namespace(
        web_host="127.0.0.1",
        web_port=9092,
        web_static_dir=None,
        web_bridge=web_bridge,
        web_bridge_tx_device=None,
        web_bridge_rx_only=False,
        web_bridge_label=None,
        web_bridge_max_retries=1,
        web_bridge_retry_delay=0.1,
        web_rigctld=False,
        dx_cluster=None,
        callsign=None,
    )


def test_web_managed_parser_defaults_to_loopback_and_auth_required() -> None:
    parser = _build_parser()

    args = parser.parse_args(["web", "--managed", "--auth-token-file", "token.txt"])

    assert args.command == "web"
    assert args.managed_runtime is True
    assert args.web_host == "127.0.0.1"
    assert args.web_rigctld is True
    assert args.auth_token == ""
    assert args.auth_token_file == "token.txt"


def test_station_parser_is_managed_web_runtime() -> None:
    parser = _build_parser()

    args = parser.parse_args(["station", "--port", "0"])

    assert args.command == "station"
    assert args.managed_runtime is True
    assert args.web_host == "127.0.0.1"
    assert args.web_port == 0
    assert args.web_rigctld is True


@pytest.mark.asyncio
async def test_cmd_web_managed_requires_auth_token(
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    radio = AsyncMock()
    args = _web_cmd_args(web_bridge=None)
    args.managed_runtime = True
    args.auth_token = ""
    monkeypatch.delenv("RIGPLANE_AUTH_TOKEN", raising=False)

    rc = await _cmd_web(radio, args)

    assert rc == 1
    assert (
        "Set RIGPLANE_AUTH_TOKEN or pass --auth-token-file" in capsys.readouterr().err
    )


@pytest.mark.asyncio
async def test_cmd_web_managed_uses_auth_token_file(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    radio = AsyncMock()
    captured: dict[str, object] = {}
    monkeypatch.setenv("RIGPLANE_AUTH_TOKEN", "env-token")
    token_file = tmp_path / "token.txt"
    token_file.write_text(" file-token \n", encoding="utf-8")

    class FakeWebServer:
        def __init__(self, _radio, cfg):
            captured["cfg"] = cfg
            self._runtime_log_path = None

        async def serve_forever(self):
            raise asyncio.CancelledError

    args = _web_cmd_args(web_bridge=None)
    args.managed_runtime = True
    args.auth_token = ""
    args.auth_token_file = str(token_file)

    with patch("rigplane.web.server.WebServer", FakeWebServer):
        assert await _cmd_web(radio, args) == 0

    assert captured["cfg"].auth_token == "file-token"


@pytest.mark.asyncio
async def test_cmd_web_auth_token_precedence_over_file_and_env(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    radio = AsyncMock()
    captured: dict[str, object] = {}
    monkeypatch.setenv("RIGPLANE_AUTH_TOKEN", "env-token")
    token_file = tmp_path / "token.txt"
    token_file.write_text("file-token\n", encoding="utf-8")

    class FakeWebServer:
        def __init__(self, _radio, cfg):
            captured["cfg"] = cfg
            self._runtime_log_path = None

        async def serve_forever(self):
            raise asyncio.CancelledError

    args = _web_cmd_args(web_bridge=None)
    args.managed_runtime = True
    args.auth_token = "argv-token"
    args.auth_token_file = str(token_file)

    with patch("rigplane.web.server.WebServer", FakeWebServer):
        assert await _cmd_web(radio, args) == 0

    assert captured["cfg"].auth_token == "argv-token"


@pytest.mark.asyncio
async def test_cmd_web_auth_token_file_failure_surfaces(
    capsys: pytest.CaptureFixture[str],
    tmp_path,
) -> None:
    radio = AsyncMock()
    missing = tmp_path / "missing-token.txt"

    args = _web_cmd_args(web_bridge=None)
    args.managed_runtime = True
    args.auth_token = ""
    args.auth_token_file = str(missing)

    rc = await _cmd_web(radio, args)

    assert rc == 1
    assert "failed to read --auth-token-file" in capsys.readouterr().err


@pytest.mark.asyncio
async def test_cmd_web_managed_uses_env_auth_and_loopback_embedded_rigctld(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    radio = AsyncMock()
    captured: dict[str, object] = {}
    monkeypatch.setenv("RIGPLANE_AUTH_TOKEN", "env-token")

    class FakeWebServer:
        def __init__(self, _radio, cfg):
            captured["cfg"] = cfg
            self._runtime_log_path = None

        async def serve_forever(self):
            captured["runtime_log_path"] = self._runtime_log_path
            raise asyncio.CancelledError

    class FakeRigctldServer:
        def __init__(self, _radio, cfg):
            captured["rigctld_cfg"] = cfg

        async def start(self):
            return None

        async def stop(self):
            return None

    args = _web_cmd_args(web_bridge=None)
    args.managed_runtime = True
    args.auth_token = ""
    args.web_rigctld = True
    args.runtime_log_path = "/tmp/rigplane-managed.log"

    with (
        patch("rigplane.web.server.WebServer", FakeWebServer),
        patch("rigplane.rigctld.server.RigctldServer", FakeRigctldServer),
    ):
        assert await _cmd_web(radio, args) == 0

    cfg = captured["cfg"]
    assert cfg.auth_token == "env-token"
    assert cfg.host == "127.0.0.1"
    assert cfg.emit_startup_event is True
    assert captured["runtime_log_path"] == "/tmp/rigplane-managed.log"
    rigctld_cfg = captured["rigctld_cfg"]
    assert rigctld_cfg.host == "127.0.0.1"


@pytest.mark.asyncio
async def test_cmd_web_plain_runtime_does_not_emit_startup_event() -> None:
    radio = AsyncMock()
    captured: dict[str, object] = {}

    class FakeWebServer:
        def __init__(self, _radio, cfg):
            captured["cfg"] = cfg
            self._runtime_log_path = None

        async def serve_forever(self):
            raise asyncio.CancelledError

    args = _web_cmd_args(web_bridge=None)
    args.managed_runtime = False
    args.auth_token = ""
    args.runtime_log_path = None

    with patch("rigplane.web.server.WebServer", FakeWebServer):
        assert await _cmd_web(radio, args) == 0

    assert captured["cfg"].emit_startup_event is False


@pytest.mark.asyncio
async def test_cli_web_no_loopback_graceful(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Auto bridge with no loopback device: warn and continue (no exit 1)."""
    from rigplane.audio_bridge import LoopbackNotFoundError

    radio = AsyncMock()

    class FakeWebServer:
        def __init__(self, _radio, cfg):
            self.radio = _radio
            self.cfg = cfg

        async def start_audio_bridge(self, **_kwargs):
            raise LoopbackNotFoundError("no loopback device found")

        async def serve_forever(self):
            raise asyncio.CancelledError

    args = _web_cmd_args(web_bridge="auto")
    with patch("rigplane.web.server.WebServer", FakeWebServer):
        rc = await _cmd_web(radio, args)

    assert rc == 0
    out = capsys.readouterr().out
    assert "loopback not found, bridge disabled" in out


@pytest.mark.asyncio
async def test_cli_web_unrelated_bridge_failure_surfaces(
    capsys: pytest.CaptureFixture[str],
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Auto bridge with a non-loopback failure: surface the real error.

    Issue #1146 — generic ``Exception`` previously masked unrelated
    failures (unsupported radio audio, missing audio backend, runtime
    errors) as misleading "loopback not found" messages. The narrowed
    handler must keep serving (graceful) but expose the actual cause
    in logs and the bridge_info banner.
    """
    radio = AsyncMock()

    class FakeWebServer:
        def __init__(self, _radio, cfg):
            self.radio = _radio
            self.cfg = cfg

        async def start_audio_bridge(self, **_kwargs):
            raise RuntimeError(
                "Audio bridge is unavailable: active radio does not support "
                "audio streaming."
            )

        async def serve_forever(self):
            raise asyncio.CancelledError

    args = _web_cmd_args(web_bridge="auto")
    with (
        caplog.at_level("ERROR", logger="rigplane.cli"),
        patch("rigplane.web.server.WebServer", FakeWebServer),
    ):
        rc = await _cmd_web(radio, args)

    # Web server keeps running.
    assert rc == 0
    out = capsys.readouterr().out
    # Must NOT pretend the loopback driver is missing.
    assert "loopback not found, bridge disabled" not in out
    # Must mention the actual cause via logs and/or bridge banner.
    assert any(
        "non-loopback" in rec.message
        and "does not support audio streaming" in rec.message
        for rec in caplog.records
    ), f"expected non-loopback error log, got: {[r.message for r in caplog.records]}"


@pytest.mark.asyncio
async def test_cli_web_explicit_bridge_still_fails(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Explicit --bridge=NonExistent must fail fast with clear error."""
    from rigplane.audio_bridge import LoopbackNotFoundError

    radio = AsyncMock()

    class FakeWebServer:
        def __init__(self, _radio, cfg):
            self.radio = _radio
            self.cfg = cfg

        async def start_audio_bridge(self, **_kwargs):
            raise LoopbackNotFoundError("device 'NonExistent' not found")

        async def serve_forever(self):
            raise asyncio.CancelledError

    args = _web_cmd_args(web_bridge="NonExistent")
    with patch("rigplane.web.server.WebServer", FakeWebServer):
        rc = await _cmd_web(radio, args)

    assert rc == 1
    captured = capsys.readouterr()
    assert "audio bridge failed" in captured.err
    assert "explicitly requested" in captured.err


@pytest.mark.asyncio
async def test_cmd_discover_found_and_not_found(
    capsys: pytest.CaptureFixture[str],
) -> None:
    # Mock discover functions to return immediately instead of waiting for real network timeouts
    with (
        patch(
            "rigplane.discovery.discover_lan_radios",
            AsyncMock(return_value=[{"host": "192.168.1.9", "remote_id": 0x1234ABCD}]),
        ),
        patch("rigplane.discovery.discover_serial_radios", AsyncMock(return_value=[])),
    ):
        rc_found = await _cmd_discover(None, None)
    assert rc_found == 0
    out = capsys.readouterr().out
    assert "192.168.1.9:" in out
    assert "LAN: 192.168.1.9" in out
    assert "Found 1 radio with 1 connection method:" in out

    with (
        patch("rigplane.discovery.discover_lan_radios", AsyncMock(return_value=[])),
        patch("rigplane.discovery.discover_serial_radios", AsyncMock(return_value=[])),
    ):
        rc_none = await _cmd_discover(None, None)
    assert rc_none == 0
    assert "No radios found." in capsys.readouterr().out


@pytest.mark.asyncio
async def test_cmd_discover_json_outputs_setup_payload(
    capsys: pytest.CaptureFixture[str],
) -> None:
    args = SimpleNamespace(serial_only=False, lan_only=False, timeout=0.1, json=True)
    with (
        patch(
            "rigplane.discovery.discover_lan_radios",
            AsyncMock(return_value=[{"host": "192.168.55.40", "remote_id": 42}]),
        ),
        patch(
            "rigplane.discovery.discover_serial_radios",
            AsyncMock(
                return_value=[
                    RadioDiscoveryResult(
                        port="/dev/cu.usbmodem7610",
                        protocol="civ",
                        model="IC-7610",
                        profile_id="icom_ic7610",
                        baudrate=115200,
                        address=0x98,
                        description="IC-7610 USB",
                        hwid="USB VID:PID=10C4:EA60",
                    )
                ]
            ),
        ),
    ):
        rc = await _cmd_discover(None, args)

    assert rc == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["schema"] == "rigplane.discovery.v1"
    assert payload["radios"][0]["connections"][0]["type"] == "lan"
    assert payload["radios"][1]["connections"][0]["type"] == "serial"


def test_main_branches(monkeypatch, capsys: pytest.CaptureFixture[str]) -> None:
    class DummyParser:
        def __init__(self, args):
            self._args = args
            self.help_called = False

        def parse_args(self):
            return self._args

        def print_help(self):
            self.help_called = True

    def fake_run(coro):
        coro.close()
        return 3

    parser_help = DummyParser(SimpleNamespace(command=None))
    with (
        patch("rigplane.cli._build_parser", return_value=parser_help),
        patch("rigplane.cli.logging.basicConfig") as basic_cfg,
        patch("rigplane.cli.sys.exit", side_effect=SystemExit) as sys_exit,
    ):
        monkeypatch.setenv("ICOM_DEBUG", "1")
        with pytest.raises(SystemExit):
            main()
    assert parser_help.help_called
    assert basic_cfg.call_args.kwargs["level"] == __import__("logging").DEBUG
    sys_exit.assert_called_once_with(0)

    parser_discover = DummyParser(SimpleNamespace(command="discover"))
    with (
        patch("rigplane.cli._build_parser", return_value=parser_discover),
        patch("rigplane.cli.asyncio.run", side_effect=fake_run),
        patch("rigplane.cli.sys.exit", side_effect=SystemExit) as sys_exit,
    ):
        with pytest.raises(SystemExit):
            main()
    sys_exit.assert_called_once_with(3)

    parser_proxy = DummyParser(
        SimpleNamespace(command="proxy", radio="1.2.3.4", listen="0.0.0.0", port=50001),
    )
    with (
        patch("rigplane.cli._build_parser", return_value=parser_proxy),
        patch("rigplane.proxy.run_proxy", new_callable=AsyncMock),
        patch("rigplane.cli.asyncio.run", side_effect=fake_run),
        patch("rigplane.cli.sys.exit", side_effect=SystemExit) as sys_exit,
    ):
        with pytest.raises(SystemExit):
            main()
    sys_exit.assert_called_once_with(0)

    # Test KeyboardInterrupt handling — main() for 'status' uses
    # loop.run_until_complete(_run(args)) → os._exit(), so we mock
    # _run to raise KeyboardInterrupt and os._exit to capture the exit code.
    async def _raise_interrupt(*a, **kw):
        raise KeyboardInterrupt

    parser_run = DummyParser(SimpleNamespace(command="status", timeout=5))
    captured_exit = []
    with (
        patch("rigplane.cli._build_parser", return_value=parser_run),
        patch("rigplane.cli._run", side_effect=_raise_interrupt),
        patch(
            "rigplane.cli.os._exit",
            side_effect=lambda c: (
                captured_exit.append(c) or (_ for _ in ()).throw(SystemExit(c))
            ),
        ),
    ):
        with pytest.raises(SystemExit):
            main()
    assert captured_exit == [130]


@pytest.mark.asyncio
async def test_cmd_tuner_get(capsys: pytest.CaptureFixture[str]) -> None:
    radio = AsyncMock()
    radio.get_tuner_status = AsyncMock(return_value=1)
    assert await _cmd_tuner(radio, argparse.Namespace(action=None, json=False)) == 0
    assert "ON" in capsys.readouterr().out


@pytest.mark.asyncio
async def test_cmd_tuner_set_on_off_tune(capsys: pytest.CaptureFixture[str]) -> None:
    radio = AsyncMock()
    radio.set_tuner_status = AsyncMock()

    assert await _cmd_tuner(radio, argparse.Namespace(action="on", json=False)) == 0
    radio.set_tuner_status.assert_awaited_with(1)
    assert await _cmd_tuner(radio, argparse.Namespace(action="off", json=False)) == 0
    radio.set_tuner_status.assert_awaited_with(0)
    assert await _cmd_tuner(radio, argparse.Namespace(action="tune", json=False)) == 0
    radio.set_tuner_status.assert_awaited_with(2)
    out = capsys.readouterr().out
    assert "ON" in out
    assert "OFF" in out
    assert "TUNING" in out


@pytest.mark.asyncio
async def test_cmd_tuner_json_output(capsys: pytest.CaptureFixture[str]) -> None:
    radio = AsyncMock()
    radio.get_tuner_status = AsyncMock(return_value=0)
    radio.set_tuner_status = AsyncMock()

    assert await _cmd_tuner(radio, argparse.Namespace(action=None, json=True)) == 0
    data = json.loads(capsys.readouterr().out)
    assert data == {"tuner_status": 0, "label": "OFF"}

    assert await _cmd_tuner(radio, argparse.Namespace(action="on", json=True)) == 0
    data = json.loads(capsys.readouterr().out)
    assert data == {"tuner_status": 1, "label": "ON"}

    assert await _cmd_tuner(radio, argparse.Namespace(action="tune", json=True)) == 0
    data = json.loads(capsys.readouterr().out)
    assert data == {"tuner_status": 2, "label": "TUNING"}


# ---------------------------------------------------------------------------
# _cmd_audio_bridge — Task 3
# ---------------------------------------------------------------------------

import rigplane.audio_bridge as _ab_mod  # noqa: E402
from rigplane.cli import _cmd_audio_bridge  # noqa: E402


def _fake_bridge_cls(start_err: Exception | None = None) -> type:
    """Return a fake AudioBridge class for testing."""
    start_mock = AsyncMock(side_effect=start_err)
    stop_mock = AsyncMock()
    _stats = {
        "running": False,
        "rx_frames": 5,
        "tx_frames": 3,
        "rx_drops": 0,
        "uptime_seconds": 1.0,
        "rx_interval_ms": 20.0,
        "tx_interval_ms": 20.0,
        "buffer_size": 5,
    }

    class _FakeBridge:
        start = start_mock
        stop = stop_mock

        def __init__(self, *_a: object, **_kw: object) -> None:
            pass

        @property
        def stats(self) -> dict:
            return _stats

    _FakeBridge._start_mock = start_mock  # type: ignore[attr-defined]
    _FakeBridge._stop_mock = stop_mock  # type: ignore[attr-defined]
    return _FakeBridge


@pytest.mark.asyncio
async def test_cmd_audio_bridge_list_devices() -> None:
    """--list-devices prints available audio devices and exits 0."""
    radio = MagicMock()
    args = argparse.Namespace(list_devices=True, device=None, rx_only=False)
    fake_devices = [
        {
            "name": "Built-in Output",
            "index": 0,
            "max_input_channels": 0,
            "max_output_channels": 2,
        },
        {
            "name": "BlackHole 2ch",
            "index": 1,
            "max_input_channels": 2,
            "max_output_channels": 2,
        },
    ]
    with patch.object(_ab_mod, "list_audio_devices", return_value=fake_devices):
        result = await _cmd_audio_bridge(radio, args)
    assert result == 0


@pytest.mark.asyncio
async def test_cmd_audio_bridge_list_devices_import_error() -> None:
    """--list-devices returns 1 when sounddevice is not installed."""
    radio = MagicMock()
    args = argparse.Namespace(list_devices=True, device=None, rx_only=False)

    with patch.object(
        _ab_mod, "list_audio_devices", side_effect=ImportError("sounddevice required")
    ):
        result = await _cmd_audio_bridge(radio, args)
    assert result == 1


@pytest.mark.asyncio
async def test_cmd_audio_bridge_standalone_runs_until_cancelled() -> None:
    """Bridge starts, runs, and stops cleanly when cancelled (standalone mode)."""
    radio = _make_audio_capable_mock(MagicMock())
    args = argparse.Namespace(list_devices=False, device="BlackHole 2ch", rx_only=False)

    fake_cls = _fake_bridge_cls()
    with patch.object(_ab_mod, "AudioBridge", fake_cls):
        task = asyncio.create_task(_cmd_audio_bridge(radio, args))
        await asyncio.sleep(0.05)
        task.cancel()
        rc = await asyncio.gather(task, return_exceptions=True)

    assert rc[0] == 0
    fake_cls._start_mock.assert_called_once()
    fake_cls._stop_mock.assert_called_once()


@pytest.mark.asyncio
async def test_cmd_audio_bridge_device_not_found() -> None:
    """Returns exit code 1 when virtual audio device is not found."""
    radio = _make_audio_capable_mock(MagicMock())
    args = argparse.Namespace(list_devices=False, device="NonExistent", rx_only=False)

    fake_cls = _fake_bridge_cls(
        start_err=RuntimeError("Virtual audio device not found")
    )
    with patch.object(_ab_mod, "AudioBridge", fake_cls):
        result = await _cmd_audio_bridge(radio, args)
    assert result == 1
