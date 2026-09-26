"""Witnesses for the MOR-2336 rows that had no named pytest node.

Each test asserts the frozen 2.11.1 row, not a neighbouring property.
Fakes only: no hardware, no network beyond a loopback rigctld socket, no RF.
"""

from __future__ import annotations

import asyncio
import importlib.metadata
import inspect
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from rigplane.backends.config import LanBackendConfig
from rigplane.backends.factory import create_radio
from rigplane.backends.icom7610.drivers.contracts import SessionDriver
from rigplane.core.radio_state import RadioState
from rigplane.exceptions import TimeoutError as RigplaneTimeoutError
from rigplane.radio import IcomRadio
from rigplane.rigctld.contract import ClientSession, RigctldConfig
from rigplane.rigctld.handler import RigctldHandler
from rigplane.rigctld.protocol import format_response, parse_line
from rigplane.rigctld.server import RigctldServer

_SYNC_PUBLIC = (
    "connect",
    "disconnect",
    "get_freq",
    "set_freq",
    "get_mode",
    "set_mode",
    "set_ptt",
)


def test_py3_transceiver_status_capable_stays_removed() -> None:
    """PY3: the removed name is not an import, a package attribute, or a protocol."""
    import rigplane

    with pytest.raises(ImportError):
        from rigplane import TransceiverStatusCapable  # noqa: F401

    with pytest.raises(AttributeError):
        rigplane.TransceiverStatusCapable  # noqa: B018

    assert "TransceiverStatusCapable" not in rigplane.__all__
    assert not hasattr(IcomRadio, "get_tx_freq_monitor")
    assert not hasattr(IcomRadio, "set_tx_freq_monitor")


def test_py4_tx_freq_monitor_stays_removed_and_is_not_tx_target() -> None:
    """PY4: absence is not False, and the removed field is not an alias of tx target."""
    with pytest.raises(TypeError):
        RadioState(tx_freq_monitor=False)  # type: ignore[call-arg]

    state = RadioState()
    with pytest.raises(AttributeError):
        state.tx_freq_monitor  # noqa: B018

    dumped = state.to_dict()
    assert "tx_freq_monitor" not in dumped
    assert dumped.get("tx_freq_monitor") is not False
    assert "txTarget" not in dumped
    assert not hasattr(state, "tx_target")


def test_py6_public_modules_and_sync_signatures_still_bind() -> None:
    """PY6: the three public modules import, and the sync methods keep their shape.

    Binding the signatures does not promise the old TX lifecycle.
    """
    import rigplane.radio
    import rigplane.radio_protocol
    import rigplane.sync

    assert rigplane.radio.IcomRadio is IcomRadio
    assert rigplane.radio_protocol.Radio is not None
    sync_radio = rigplane.sync.IcomRadio
    for name in _SYNC_PUBLIC:
        signature = inspect.signature(getattr(sync_radio, name))
        assert callable(signature.bind)
    assert list(inspect.signature(sync_radio.set_ptt).parameters) == ["self", "on"]
    assert list(inspect.signature(sync_radio.set_freq).parameters) == [
        "self",
        "freq_hz",
    ]
    assert list(inspect.signature(sync_radio.get_freq).parameters) == ["self"]


def test_py7_backend_config_and_session_construct_without_connecting() -> None:
    """PY7: a typed config builds a radio session and does not open hardware."""
    config = LanBackendConfig(host="192.0.2.1", model="IC-7300")
    radio = create_radio(config)

    assert isinstance(radio, IcomRadio)
    assert isinstance(radio, SessionDriver)
    assert radio.connected is False
    assert radio.model == "IC-7300"


def test_cli2_entrypoints_python_and_extras_stay_present() -> None:
    """CLI2: both scripts, the Python floor, and the audited extras are still declared."""
    scripts = importlib.metadata.entry_points(group="console_scripts")
    by_name = {item.name: item.value for item in scripts}
    assert by_name["rigplane"] == "rigplane.cli:main"
    assert by_name["icom-lan"] == "icom_lan._cli_shim:main"

    metadata = importlib.metadata.metadata("rigplane")
    assert metadata["Requires-Python"] == ">=3.11"
    requires = metadata.get_all("Requires-Dist") or []
    assert any(item.startswith("aiohttp") for item in requires)
    extras = set(metadata.get_all("Provides-Extra") or [])
    for extra in ("audio", "bridge", "scope", "dsp", "webrtc", "tls", "dev"):
        assert extra in extras

    from rigplane.cli import _build_parser

    commands = _build_parser()._subparsers._group_actions[0].choices
    for name in ("freq", "mode", "ptt", "serve", "web", "status"):
        assert name in commands


def _handler(radio: SimpleNamespace, *, read_only: bool) -> RigctldHandler:
    return RigctldHandler(radio, RigctldConfig(read_only=read_only, cache_ttl=0.0))


def _wire(radio: SimpleNamespace, line: bytes, *, read_only: bool) -> bytes:
    command = parse_line(line)
    response = asyncio.run(_handler(radio, read_only=read_only).execute(command))
    return format_response(command, response, ClientSession())


def test_wire1_raw_timeout_is_rprt_minus_5() -> None:
    """WIRE1: a raw rigctld timeout is RPRT -5, never an empty success."""
    radio = SimpleNamespace(
        capabilities=set(),
        _send_civ_raw=AsyncMock(side_effect=RigplaneTimeoutError("no reply")),
    )

    assert _wire(radio, b"w FE FE 94 E0 03 FD", read_only=False) == b"RPRT -5\n"


def test_wire2_raw_read_only_is_rprt_minus_22_and_structured_read_works() -> None:
    """WIRE2: raw w is RPRT -22 in read-only mode; a structured read still answers."""
    radio = SimpleNamespace(
        capabilities=set(),
        get_freq=AsyncMock(return_value=14_074_000),
        _send_civ_raw=AsyncMock(return_value=b"\xfe\xfe"),
    )

    assert _wire(radio, b"w FE FE 94 E0 03 FD", read_only=True) == b"RPRT -22\n"
    radio._send_civ_raw.assert_not_called()
    assert _wire(radio, b"f", read_only=True) == b"14074000\n"


async def _serve(radio: SimpleNamespace) -> tuple[str, int, RigctldServer]:
    config = RigctldConfig(
        host="127.0.0.1", port=0, client_timeout=2.0, command_timeout=1.0
    )
    server = RigctldServer(radio, config, _handler=RigctldHandler(radio, config))
    await server.start()
    assert server._server is not None
    host, port = server._server.sockets[0].getsockname()
    return host, port, server


async def _exchange(host: str, port: int, line: bytes) -> bytes:
    reader, writer = await asyncio.open_connection(host, port)
    writer.write(line)
    await writer.drain()
    data = await asyncio.wait_for(reader.read(256), timeout=2.0)
    writer.close()
    await writer.wait_closed()
    return data


@pytest.mark.asyncio
async def test_wire3_framing_and_structured_ops_keep_audited_grammar() -> None:
    """WIRE3: a fake transport still speaks the audited get, set, and error grammar."""
    radio = SimpleNamespace(
        capabilities=set(),
        connected=True,
        radio_ready=True,
        control_connected=True,
        get_freq=AsyncMock(return_value=14_074_000),
        set_freq=AsyncMock(return_value=None),
    )
    host, port, server = await _serve(radio)
    try:
        assert await _exchange(host, port, b"f\n") == b"14074000\n"
        assert await _exchange(host, port, b"F 7050000\n") == b"RPRT 0\n"
        assert await _exchange(host, port, b"no-such\n") == b"RPRT -4\n"
    finally:
        await server.stop()
