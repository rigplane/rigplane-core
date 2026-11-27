"""Public startup-failure wire contract and abort-path regressions."""

from __future__ import annotations

import errno
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from rigplane import cli
from rigplane.backends.config import SerialBackendConfig
from rigplane.core.exceptions import (
    AuthenticationError,
    ConnectionError as RadioConnectionError,
    RadioSessionHeldError,
)
from rigplane.core.startup_failure import (
    STARTUP_FAILURE_PREFIX,
    StartupCause,
    StartupPhase,
    parse_startup_failure,
)
from rigplane.profiles.rig_loader import RigLoadError
from rigplane.web.server import WebConfig, WebServer


def _failure(capsys, cause: str, phase: str):
    lines = [
        line
        for line in capsys.readouterr().err.splitlines()
        if line.startswith(STARTUP_FAILURE_PREFIX)
    ]
    assert len(lines) == 1
    assert "synthetic-secret" not in lines[0]
    assert set(json.loads(lines[0][len(STARTUP_FAILURE_PREFIX) :])) == {
        "schema",
        "cause",
        "phase",
        "detail",
    }
    failure = parse_startup_failure(lines[0])
    assert failure is not None
    assert failure.schema == 1
    assert failure.cause == cause
    assert failure.phase == phase
    assert failure.detail
    assert len(failure.detail) <= 160
    assert "synthetic-secret" not in failure.detail
    assert "/private/" not in failure.detail
    assert "\n" not in failure.detail
    return failure


@pytest.mark.parametrize(
    "line",
    [
        "unrelated log line",
        STARTUP_FAILURE_PREFIX + "{",
        STARTUP_FAILURE_PREFIX + "[]",
        STARTUP_FAILURE_PREFIX
        + '{"schema":true,"cause":"unknown","phase":"web_start","detail":"x"}',
        STARTUP_FAILURE_PREFIX
        + '{"schema":2,"cause":"unknown","phase":"web_start","detail":"x"}',
        STARTUP_FAILURE_PREFIX
        + '{"schema":1,"cause":[],"phase":"web_start","detail":"x"}',
        STARTUP_FAILURE_PREFIX
        + '{"schema":1,"cause":"unknown","phase":null,"detail":"x"}',
        STARTUP_FAILURE_PREFIX + '{"schema":1,"cause":"unknown","phase":"web_start"}',
    ],
)
def test_parser_tolerates_missing_or_unsupported_documents(line):
    assert parse_startup_failure(line) is None


def test_parser_preserves_future_codes_and_ignores_extra_fields():
    payload = {
        "schema": 1,
        "cause": "future_cause",
        "phase": "future_phase",
        "detail": "Safe description.",
        "future": True,
    }
    failure = parse_startup_failure(STARTUP_FAILURE_PREFIX + json.dumps(payload))
    assert failure is not None
    assert failure.cause == "future_cause"
    assert failure.phase == "future_phase"


@pytest.mark.parametrize("command", ["web", "station"])
@pytest.mark.parametrize("options", [["--port", "not-an-int"], ["--unknown-option"]])
def test_main_argument_abort_emits_once(command, options, monkeypatch, capsys):
    monkeypatch.setattr("sys.argv", ["rigplane", command, *options])
    with patch("rigplane.cli._run") as run:
        with pytest.raises(SystemExit) as caught:
            cli.main()
    assert caught.value.code == 2
    run.assert_not_called()
    _failure(capsys, "configuration_invalid", "configuration")


@pytest.mark.parametrize("command", ["web", "station"])
@pytest.mark.parametrize("variable", ["ICOM_PORT", "ICOM_SERIAL_BAUDRATE"])
@pytest.mark.parametrize("global_options", [[], ["--host", "127.0.0.1", "--json"]])
def test_main_parser_construction_abort_emits_once(
    command, variable, global_options, monkeypatch, capsys
):
    monkeypatch.setattr("sys.argv", ["rigplane", *global_options, command])
    monkeypatch.setenv(variable, "bad")
    with patch("rigplane.cli._run") as run:
        with pytest.raises(SystemExit) as caught:
            cli.main()
    assert caught.value.code == 1
    run.assert_not_called()
    _failure(capsys, "configuration_invalid", "configuration")


@pytest.mark.parametrize("command", ["web", "station"])
@pytest.mark.parametrize(
    "options",
    [["--timeout", "bad"], ["--timeout=bad"], ["--radio-addr", "bad"]],
)
def test_main_global_argument_abort_emits_once(command, options, monkeypatch, capsys):
    monkeypatch.setattr("sys.argv", ["rigplane", *options, command])
    with patch("rigplane.cli._run") as run:
        with pytest.raises(SystemExit) as caught:
            cli.main()
    assert caught.value.code == 2
    run.assert_not_called()
    _failure(capsys, "configuration_invalid", "configuration")


@pytest.mark.parametrize(
    "argv",
    [
        ["--host", "web", "models"],
        ["--model", "station", "models"],
        ["--user=web", "models"],
        ["models", "web"],
        ["--help", "web"],
        ["--version", "station"],
    ],
)
def test_main_non_startup_parser_abort_does_not_report(argv, monkeypatch, capsys):
    monkeypatch.setattr("sys.argv", ["rigplane", *argv])
    monkeypatch.setenv("ICOM_PORT", "bad")
    with pytest.raises(SystemExit) as caught:
        cli.main()
    assert caught.value.code == 1
    assert STARTUP_FAILURE_PREFIX not in capsys.readouterr().err


@pytest.mark.parametrize("command", ["models", "station"])
def test_main_startup_intent_skips_global_option_values(command, monkeypatch, capsys):
    parser = cli._build_parser()
    monkeypatch.setenv("ICOM_PORT", "bad")
    for action in parser._actions:
        if action.nargs == 0:
            continue
        for option in action.option_strings:
            monkeypatch.setattr("sys.argv", ["rigplane", option, "web", command])
            with pytest.raises(SystemExit) as caught:
                cli.main()
            assert caught.value.code == 1
            if command == "station":
                _failure(capsys, "configuration_invalid", "configuration")
            else:
                assert STARTUP_FAILURE_PREFIX not in capsys.readouterr().err, option


@pytest.mark.parametrize("command", ["web", "station"])
def test_main_log_initialization_abort_emits_once(
    command, monkeypatch, tmp_path, capsys
):
    monkeypatch.setattr("sys.argv", ["rigplane", command])
    monkeypatch.setenv("ICOM_LOG_FILE", str(tmp_path))
    with patch("rigplane.cli._run") as run:
        with pytest.raises(OSError):
            cli.main()
    run.assert_not_called()
    _failure(capsys, "configuration_invalid", "configuration")


@pytest.mark.parametrize(
    "argv", [["web", "--help"], ["station", "--help"], ["models", "--unknown-option"]]
)
def test_main_help_and_other_commands_do_not_report(argv, monkeypatch, capsys):
    monkeypatch.setattr("sys.argv", ["rigplane", *argv])
    with pytest.raises(SystemExit) as caught:
        cli.main()
    assert caught.value.code == (0 if "--help" in argv else 2)
    assert STARTUP_FAILURE_PREFIX not in capsys.readouterr().err


@pytest.mark.parametrize("exit_code", [0, 1])
def test_main_reports_nested_abort_before_forced_exit(exit_code, monkeypatch, capsys):
    from rigplane.core.startup_failure import startup_failure_scope

    monkeypatch.setattr("sys.argv", ["rigplane", "web", "--no-rigctld"])
    monkeypatch.setenv("ICOM_LOG_FILE", "off")

    async def abort(_args):
        with startup_failure_scope(StartupCause.UNKNOWN, StartupPhase.CONFIGURATION):
            if exit_code:
                cli.record_startup_failure(
                    None,
                    StartupCause.PROFILE_UNRESOLVED,
                    StartupPhase.PROFILE_RESOLUTION,
                )
            return exit_code

    def forced_exit(code):
        if exit_code:
            _failure(capsys, "profile_unresolved", "profile_resolution")
        else:
            assert STARTUP_FAILURE_PREFIX not in capsys.readouterr().err
        raise SystemExit(code)

    with (
        patch("rigplane.cli._run", new=abort),
        patch("logging.basicConfig"),
        patch("rigplane.diagnostics._logging.detach_diagnostic_logging"),
        patch("rigplane.cli.signal.signal"),
        patch("rigplane.cli.os._exit", side_effect=forced_exit),
    ):
        with pytest.raises(SystemExit) as caught:
            cli.main()
    assert caught.value.code == exit_code
    assert STARTUP_FAILURE_PREFIX not in capsys.readouterr().err


@pytest.mark.asyncio
@pytest.mark.parametrize("value", ["0", "not-a-number"])
async def test_real_constructor_environment_error_is_configuration_invalid(
    value, monkeypatch, capsys
):
    monkeypatch.setenv("ICOM_SERIAL_CIV_MIN_INTERVAL_MS", value)
    args = cli._build_parser().parse_args(["web", "--no-rigctld"])
    config = SerialBackendConfig(device="/private/device", model="IC-7300")
    with (
        patch("rigplane.cli._build_backend_config", new=AsyncMock(return_value=config)),
        patch("rigplane.cli.guards_backend", return_value=False),
        patch("rigplane.cli.check_ports_available") as preflight,
    ):
        assert await cli._run_with_startup_reporting(args) == 1
    preflight.assert_not_called()
    _failure(capsys, "configuration_invalid", "configuration")


@pytest.mark.asyncio
@pytest.mark.parametrize("model", [None, "unsupported-synthetic-model"])
async def test_factory_model_refusal_is_profile_unresolved(model, capsys):
    args = cli._build_parser().parse_args(["web", "--no-rigctld"])
    config = SerialBackendConfig(device="/private/device", model=model)
    with (
        patch("rigplane.cli._build_backend_config", new=AsyncMock(return_value=config)),
        patch("rigplane.cli.guards_backend", return_value=False),
    ):
        assert await cli._run_with_startup_reporting(args) == 1
    _failure(capsys, "profile_unresolved", "profile_resolution")


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "error_type, expected_cause, exit_code",
    [
        (RadioSessionHeldError, "radio_in_use", 3),
        (AuthenticationError, "radio_open_failed", 1),
        (RadioConnectionError, "radio_open_failed", 1),
        (ConnectionError, "radio_open_failed", 1),
    ],
)
@pytest.mark.parametrize("annotated", [False, True])
async def test_pending_radio_ownership_abort_wins_over_web_scope(
    error_type, expected_cause, exit_code, annotated, capsys
):
    import asyncio

    from rigplane.backends.config import LanBackendConfig
    from rigplane.core.startup_failure import startup_operation

    args = cli._build_parser().parse_args(["web", "--no-rigctld", "--no-discovery"])
    args.web_bridge = None
    web_starting = asyncio.Event()
    web_stopped = asyncio.Event()
    error = error_type("synthetic-secret")
    radio = AsyncMock()
    radio.startup_busy_hold_event = asyncio.Event()
    radio.startup_busy_hold_event.set()

    async def enter():
        await web_starting.wait()
        if annotated:
            with startup_operation(StartupCause.PORT_BUSY, StartupPhase.RADIO_OPEN):
                raise error
        raise error

    async def serve(**_kwargs):
        web_starting.set()
        try:
            await asyncio.Event().wait()
        finally:
            web_stopped.set()

    radio.__aenter__.side_effect = enter
    server = AsyncMock()
    server.serve_forever.side_effect = serve
    with (
        patch(
            "rigplane.cli._build_backend_config",
            new=AsyncMock(return_value=LanBackendConfig(host="127.0.0.1")),
        ),
        patch("rigplane.cli.guards_backend", return_value=False),
        patch("rigplane.cli.create_radio", return_value=radio),
        patch("rigplane.cli.check_ports_available"),
        patch("rigplane.cli.ManagedTxComposition", return_value=None),
        patch("rigplane.cli.install_managed_tx_composition"),
        patch("rigplane._platformdirs_migration.migrate_legacy_platformdirs"),
        patch("rigplane.web.server.WebServer", return_value=server),
    ):
        assert await cli._run_with_startup_reporting(args) == exit_code
    assert web_stopped.is_set()
    server.finish_startup_busy_hold.assert_not_awaited()
    radio.__aexit__.assert_awaited_once()
    assert radio.__aexit__.await_args.args[1] is error
    _failure(capsys, "port_busy" if annotated else expected_cause, "radio_open")


@pytest.mark.asyncio
@pytest.mark.parametrize("command", ["web", "station"])
@pytest.mark.parametrize("stage", ["config", "factory"])
async def test_profile_abort_emits_one_document(command, stage, capsys):
    args = cli._build_parser().parse_args([command])
    error = RigLoadError("/private/profile.toml synthetic-secret")
    with (
        patch("rigplane.cli.guards_backend", return_value=False),
        patch(
            "rigplane.cli._build_backend_config",
            new=AsyncMock(side_effect=error) if stage == "config" else AsyncMock(),
        ),
        patch("rigplane.cli.create_radio", side_effect=error),
    ):
        assert await cli._run_with_startup_reporting(args) == 1
    _failure(capsys, "profile_unresolved", "profile_resolution")


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("number", "cause"),
    [(errno.EADDRINUSE, "port_busy"), (errno.EACCES, "listener_bind_failed")],
)
async def test_listener_preflight_abort_uses_errno(number, cause, capsys):
    args = cli._build_parser().parse_args(["web", "--no-rigctld"])
    with (
        patch("rigplane.cli._build_backend_config", new=AsyncMock()),
        patch("rigplane.cli.guards_backend", return_value=False),
        patch("rigplane.cli.create_radio"),
        patch("socket.socket") as socket,
        patch("rigplane.cli._find_port_pid", return_value=None),
    ):
        socket.return_value.__enter__.return_value.bind.side_effect = OSError(
            number, "synthetic-secret"
        )
        assert await cli._run_with_startup_reporting(args) == 1
    _failure(capsys, cause, "listener_preflight")


@pytest.mark.asyncio
async def test_serial_open_abort_emits_one_document(capsys):
    args = cli._build_parser().parse_args(["web", "--no-rigctld"])
    config = SerialBackendConfig(device="/private/device", baudrate=19200)
    radio = AsyncMock()
    radio.__aenter__.side_effect = OSError(errno.EACCES, "synthetic-secret")
    with (
        patch("rigplane.cli._build_backend_config", new=AsyncMock(return_value=config)),
        patch("rigplane.cli.guards_backend", return_value=False),
        patch("rigplane.cli.create_radio", return_value=radio),
        patch("rigplane.cli.check_ports_available"),
        patch("rigplane.cli.ManagedTxComposition", return_value=None),
        patch("rigplane.cli.install_managed_tx_composition"),
        patch("rigplane._platformdirs_migration.migrate_legacy_platformdirs"),
    ):
        assert await cli._run_with_startup_reporting(args) == 1
    _failure(capsys, "serial_open_failed", "radio_open")


@pytest.mark.asyncio
@pytest.mark.parametrize("component", ["rigctld", "tci"])
@pytest.mark.parametrize("busy", [False, True])
async def test_bundled_listener_abort_emits_once(component, busy, capsys):
    args = cli._build_parser().parse_args(["web", "--no-rigctld"])
    args.web_rigctld = component == "rigctld"
    args.web_tci = component == "tci"
    target = (
        "rigplane.rigctld.server.RigctldServer.start"
        if component == "rigctld"
        else "rigplane.tci_adapter.server.TciAdapterServer.start"
    )
    with patch(
        target,
        new=AsyncMock(
            side_effect=OSError(
                errno.EADDRINUSE if busy else errno.EACCES, "synthetic-secret"
            )
        ),
    ):
        with pytest.raises(OSError):
            await cli._cmd_web(None, args)
    _failure(
        capsys, "port_busy" if busy else f"{component}_bind_failed", f"{component}_bind"
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "case",
    [
        "static",
        "cluster_address",
        "cluster_callsign",
        "tls_pair",
        "tls_cert",
        "tls_key",
        "sdr",
    ],
)
async def test_web_configuration_abort_emits_once(case, tmp_path, capsys):
    args = cli._build_parser().parse_args(["web", "--no-rigctld"])
    if case == "static":
        args.web_static_dir = str(tmp_path / "synthetic-secret")
    elif case == "cluster_address":
        args.dx_cluster = "synthetic-secret"
    elif case == "cluster_callsign":
        args.dx_cluster = "localhost:7300"
        args.callsign = ""
    elif case == "tls_pair":
        args.tls_cert = str(tmp_path / "synthetic-secret")
    elif case in ("tls_cert", "tls_key"):
        cert = tmp_path / "cert.pem"
        if case == "tls_key":
            cert.write_text("synthetic certificate", encoding="utf-8")
        args.tls_cert = str(cert)
        args.tls_key = str(tmp_path / "synthetic-secret")
    else:
        args.scope_source = "sdr"
    assert await cli._cmd_web(None, args) == 1
    _failure(capsys, "configuration_invalid", "configuration")


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("error", "cause"),
    [
        (OSError(errno.EADDRINUSE, "synthetic-secret"), "port_busy"),
        (OSError(errno.EACCES, "synthetic-secret"), "web_bind_failed"),
    ],
)
async def test_web_bind_abort_emits_once(error, cause, capsys):
    server = WebServer(None, WebConfig(discovery=False))
    with patch(
        "rigplane.web.web_startup.asyncio.start_server",
        new=AsyncMock(side_effect=error),
    ):
        with pytest.raises(OSError):
            await server.start()
    _failure(capsys, cause, "web_bind")


@pytest.mark.asyncio
async def test_web_startup_defect_emits_once(capsys):
    from rigplane.web.web_startup import _abort_on_startup_defect

    async def acquire(*_args, **_kwargs):
        _abort_on_startup_defect(SimpleNamespace(startup_defect="synthetic-secret"))

    server = WebServer(None, WebConfig(discovery=False))
    with patch(
        "rigplane.web.web_startup._await_initial_state_acquisition",
        new=acquire,
    ):
        with pytest.raises(RuntimeError):
            await server.start()
    _failure(capsys, "web_start_failed", "web_start")


@pytest.mark.asyncio
async def test_nested_cli_web_abort_does_not_duplicate_document(capsys):
    args = cli._build_parser().parse_args(["web", "--no-rigctld"])
    with patch(
        "rigplane.web.web_startup.asyncio.start_server",
        new=AsyncMock(side_effect=OSError(errno.EACCES, "synthetic-secret")),
    ):
        with pytest.raises(OSError):
            await cli._cmd_web(None, args)
    _failure(capsys, "web_bind_failed", "web_bind")


def test_producer_codes_are_closed():
    assert "unknown" in {cause.value for cause in StartupCause}
    assert "web_start" in {phase.value for phase in StartupPhase}


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("number", "cause"),
    [(errno.EBUSY, "port_busy"), (errno.EACCES, "serial_open_failed")],
)
async def test_shared_serial_opener_attributes_before_backend_wrapping(
    number, cause, capsys
):
    from rigplane.core.serial_open import open_serial_port
    from rigplane.core.startup_failure import startup_failure_scope

    original = OSError(number, "/private/device synthetic-secret")
    opener = AsyncMock(side_effect=original)
    with pytest.raises(RuntimeError):
        with startup_failure_scope(StartupCause.UNKNOWN, StartupPhase.RADIO_OPEN):
            try:
                await open_serial_port("/private/device", baudrate=19200, opener=opener)
            except OSError as exc:
                assert exc is original
                raise RuntimeError("backend wrapper synthetic-secret") from exc
    _failure(capsys, cause, "radio_open")


@pytest.fixture(autouse=True)
def no_optional_host_discovery(monkeypatch):
    monkeypatch.setattr(cli, "_detect_loopback_hint", lambda: None)
    monkeypatch.setattr(
        "rigplane.web.eibi.EiBiProvider.load_cache", AsyncMock(return_value={})
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("model", [None, "unknown-synthetic-profile"])
async def test_cli_profile_resolution_aborts_before_radio_creation(model, capsys):
    argv = ["web", "--no-rigctld"]
    if model is not None:
        argv = ["--model", model, *argv]
    args = cli._build_parser().parse_args(argv)
    with patch("rigplane.cli.create_radio") as create:
        assert await cli._run_with_startup_reporting(args) == 1
    create.assert_not_called()
    _failure(capsys, "profile_unresolved", "profile_resolution")


@pytest.mark.asyncio
@pytest.mark.parametrize("command", ["web", "station"])
async def test_direct_run_lock_refusal_preserves_legacy_stderr(command, capsys):
    from rigplane.cli._instance_guard import ResidentAlreadyRunningError

    args = cli._build_parser().parse_args([command])
    config = SerialBackendConfig(device="/private/device")
    error = ResidentAlreadyRunningError(None)
    with (
        patch("rigplane.cli._build_backend_config", new=AsyncMock(return_value=config)),
        patch("rigplane.cli.acquire_resident_lock", side_effect=error),
        patch("rigplane.cli.create_radio") as create,
        patch("rigplane.cli.check_ports_available") as preflight,
    ):
        assert await cli._run(args) == 1
    create.assert_not_called()
    preflight.assert_not_called()
    assert capsys.readouterr().err == f"Error: {error}\n"


@pytest.mark.parametrize("command", ["web", "station"])
def test_main_local_radio_ownership_abort_emits_once(command, monkeypatch, capsys):
    from rigplane.cli._instance_guard import ResidentAlreadyRunningError

    monkeypatch.setattr("sys.argv", ["rigplane", command])
    monkeypatch.setenv("ICOM_LOG_FILE", "off")
    config = SerialBackendConfig(device="/private/device")
    with (
        patch("rigplane.cli._build_backend_config", new=AsyncMock(return_value=config)),
        patch(
            "rigplane.cli.acquire_resident_lock",
            side_effect=ResidentAlreadyRunningError(None),
        ),
        patch("rigplane.cli.create_radio") as create,
        patch("rigplane.cli.check_ports_available") as preflight,
        patch("logging.basicConfig"),
        patch("rigplane.diagnostics._logging.detach_diagnostic_logging"),
        patch("rigplane.cli.signal.signal"),
        patch("rigplane.cli.os._exit", side_effect=SystemExit(1)) as forced_exit,
    ):
        with pytest.raises(SystemExit) as caught:
            cli.main()
    assert caught.value.code == 1
    forced_exit.assert_called_once_with(1)
    create.assert_not_called()
    preflight.assert_not_called()
    _failure(capsys, "radio_in_use", "radio_lock")


@pytest.mark.asyncio
@pytest.mark.parametrize("command", ["web", "station"])
async def test_local_radio_ownership_abort_emits_once(command, capsys):
    from rigplane.cli._instance_guard import ResidentAlreadyRunningError

    args = cli._build_parser().parse_args([command])
    config = SerialBackendConfig(device="/private/device")
    with (
        patch("rigplane.cli._build_backend_config", new=AsyncMock(return_value=config)),
        patch(
            "rigplane.cli.acquire_resident_lock",
            side_effect=ResidentAlreadyRunningError(None),
        ),
        patch("rigplane.cli.create_radio") as create,
    ):
        assert await cli._run_with_startup_reporting(args) == 1
    create.assert_not_called()
    _failure(capsys, "radio_in_use", "radio_lock")


@pytest.mark.asyncio
@pytest.mark.parametrize("held", [False, True])
async def test_lan_radio_open_abort_retains_exit_code(held, capsys):
    from rigplane.backends.config import LanBackendConfig
    from rigplane.core.exceptions import ConnectionError, RadioSessionHeldError

    args = cli._build_parser().parse_args(["web", "--no-rigctld"])
    config = LanBackendConfig(host="127.0.0.1")
    radio = AsyncMock()
    error_type = RadioSessionHeldError if held else ConnectionError
    radio.__aenter__.side_effect = error_type("synthetic-secret")
    with (
        patch("rigplane.cli._build_backend_config", new=AsyncMock(return_value=config)),
        patch("rigplane.cli.guards_backend", return_value=False),
        patch("rigplane.cli.create_radio", return_value=radio),
        patch("rigplane.cli.check_ports_available"),
        patch("rigplane.cli.ManagedTxComposition", return_value=None),
        patch("rigplane.cli.install_managed_tx_composition"),
        patch("rigplane._platformdirs_migration.migrate_legacy_platformdirs"),
    ):
        assert await cli._run_with_startup_reporting(args) == (3 if held else 1)
    _failure(capsys, "radio_in_use" if held else "radio_open_failed", "radio_open")


@pytest.mark.asyncio
async def test_managed_composition_abort_is_configuration_failure(capsys):
    args = cli._build_parser().parse_args(["web", "--no-rigctld"])
    with (
        patch("rigplane.cli._build_backend_config", new=AsyncMock()),
        patch("rigplane.cli.guards_backend", return_value=False),
        patch("rigplane.cli.create_radio"),
        patch("rigplane.cli.check_ports_available"),
        patch(
            "rigplane._platformdirs_migration.migrate_legacy_platformdirs",
            side_effect=OSError("synthetic-secret"),
        ),
    ):
        assert await cli._run_with_startup_reporting(args) == 1
    _failure(capsys, "configuration_invalid", "configuration")


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "original",
    [RuntimeError("synthetic-secret"), OSError(errno.EBUSY, "synthetic-secret")],
)
async def test_unclassified_abort_emits_unknown_and_preserves_exception(
    original, capsys
):
    args = cli._build_parser().parse_args(["station"])
    with patch(
        "rigplane.cli._build_backend_config", new=AsyncMock(side_effect=original)
    ):
        with pytest.raises(type(original)) as caught:
            await cli._run_with_startup_reporting(args)
    assert caught.value is original
    _failure(capsys, "unknown", "configuration")


@pytest.mark.asyncio
async def test_web_profile_resolution_failure_retains_source_classification(capsys):
    from rigplane.profiles import resolve_radio_profile

    async def acquire(*_args, **_kwargs):
        resolve_radio_profile(model="unknown-synthetic-profile")

    server = WebServer(None, WebConfig(discovery=False))
    with patch(
        "rigplane.web.web_startup._await_initial_state_acquisition", new=acquire
    ):
        with pytest.raises(KeyError):
            await server.start()
    _failure(capsys, "profile_unresolved", "profile_resolution")


@pytest.mark.asyncio
@pytest.mark.parametrize("cancelled", [False, True])
async def test_success_and_cancellation_do_not_emit_failure(cancelled, capsys):
    import asyncio

    server = WebServer(None, WebConfig(discovery=False))
    start = AsyncMock(side_effect=asyncio.CancelledError if cancelled else None)
    with patch("rigplane.web.web_startup.start_web_server", new=start):
        if cancelled:
            with pytest.raises(asyncio.CancelledError):
                await server.start()
        else:
            await server.start()
    assert STARTUP_FAILURE_PREFIX not in capsys.readouterr().err


@pytest.mark.asyncio
async def test_runtime_error_after_startup_is_not_startup_failure(capsys):
    from rigplane.web.server import run_web_server

    async def start(server):
        server._server = SimpleNamespace()

    with (
        patch.object(WebServer, "start", new=start),
        patch(
            "rigplane.web.server._install_shutdown_signal_handlers",
            side_effect=RuntimeError("late runtime error"),
        ),
    ):
        with pytest.raises(RuntimeError):
            await run_web_server(None, discovery=False)
    assert STARTUP_FAILURE_PREFIX not in capsys.readouterr().err


@pytest.mark.asyncio
async def test_public_web_runner_reports_configuration_abort(capsys):
    from rigplane.web.server import run_web_server

    with pytest.raises(ValueError):
        await run_web_server(None, auth_token="synthetic-secret")
    _failure(capsys, "configuration_invalid", "configuration")


@pytest.mark.parametrize("cause", list(StartupCause))
def test_every_producer_cause_has_short_fixed_detail(cause, capsys):
    from rigplane.core.startup_failure import (
        record_startup_failure,
        startup_failure_scope,
    )

    with startup_failure_scope(StartupCause.UNKNOWN, StartupPhase.CONFIGURATION):
        record_startup_failure(None, cause, StartupPhase.CONFIGURATION)
    _failure(capsys, cause.value, "configuration")


@pytest.mark.asyncio
async def test_cli_and_server_abort_share_one_report(capsys):
    args = cli._build_parser().parse_args(["web", "--no-rigctld", "--no-discovery"])
    radio = AsyncMock()
    with (
        patch("rigplane.cli._build_backend_config", new=AsyncMock()),
        patch("rigplane.cli.guards_backend", return_value=False),
        patch("rigplane.cli.create_radio", return_value=radio),
        patch("rigplane.cli.check_ports_available"),
        patch("rigplane.cli.ManagedTxComposition", return_value=None),
        patch("rigplane.cli.install_managed_tx_composition"),
        patch("rigplane._platformdirs_migration.migrate_legacy_platformdirs"),
        patch(
            "rigplane.web.server.WebServer",
            side_effect=lambda _radio, config: WebServer(None, config),
        ),
        patch(
            "rigplane.web.web_startup.asyncio.start_server",
            new=AsyncMock(side_effect=OSError(errno.EACCES, "synthetic-secret")),
        ),
    ):
        assert await cli._run_with_startup_reporting(args) == 1
    _failure(capsys, "web_bind_failed", "web_bind")


@pytest.mark.asyncio
@pytest.mark.parametrize("serial", [False, True])
async def test_discovery_abort_emits_once_without_radio_creation(serial, capsys):
    argv = ["--model", "IC-7300", "--backend", "serial" if serial else "lan", "web"]
    args = cli._build_parser().parse_args(argv)
    target = (
        "rigplane.discovery.discover_serial_radios"
        if serial
        else "rigplane.discovery.discover_lan_radios"
    )
    with (
        patch(target, new=AsyncMock(return_value=[])),
        patch("rigplane.cli.create_radio") as create,
    ):
        with pytest.raises(SystemExit) as caught:
            await cli._run_with_startup_reporting(args)
    assert caught.value.code == 1
    create.assert_not_called()
    _failure(capsys, "discovery_failed", "discovery")


@pytest.mark.asyncio
@pytest.mark.parametrize("missing_device", [False, True])
async def test_explicit_audio_bridge_abort_emits_once(missing_device, capsys):
    from rigplane.audio.bridge import LoopbackNotFoundError

    args = cli._build_parser().parse_args(
        ["web", "--no-rigctld", "--bridge=synthetic-device"]
    )
    error_type = LoopbackNotFoundError if missing_device else RuntimeError
    with patch.object(
        WebServer,
        "start_audio_bridge",
        new=AsyncMock(side_effect=error_type("synthetic-secret")),
    ):
        assert await cli._cmd_web(None, args) == 1
    _failure(capsys, "audio_bridge_failed", "audio_bridge")


@pytest.mark.asyncio
async def test_web_not_ready_abort_emits_once_before_bind(capsys):
    server = WebServer(None, WebConfig(discovery=False))
    server._radio = SimpleNamespace(
        connected=False, control_connected=False, radio_ready=False, capabilities=set()
    )
    with patch(
        "rigplane.web.web_startup.asyncio.start_server", new=AsyncMock()
    ) as bind:
        with pytest.raises(RuntimeError):
            await server.start()
    bind.assert_not_awaited()
    _failure(capsys, "web_start_failed", "web_start")


@pytest.mark.asyncio
async def test_incomplete_listener_is_not_marked_started(capsys):
    from rigplane.web.server import run_web_server

    with patch.object(WebServer, "start", new=AsyncMock()):
        with pytest.raises(AssertionError):
            await run_web_server(None, discovery=False)
    _failure(capsys, "web_start_failed", "web_start")
