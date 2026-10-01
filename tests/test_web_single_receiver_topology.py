"""MOR-2784: a single-receiver radio's public ``active`` is ``"MAIN"``.

``WebServer._publish_single_receiver_topology`` publishes the fact; these
tests check it through the production ``rigplane web`` startup and through the
provider-generation advances the web server makes or observes.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from fake_rigctld import FakeRigctldServer
from rigplane.audio.backend import FakeAudioBackend
from rigplane.audio.usb_driver import UsbAudioDriver
from rigplane.backends.ic7300.serial import Ic7300SerialRadio
from rigplane.backends.icom7610 import Icom7610SerialRadio
from rigplane.backends.rigctld_client.radio import RigctldClientRadio
from rigplane.cli import _ManagedTxRadioSession
from rigplane.runtime.managed_tx_composition import (
    ManagedTxComposition,
    install_managed_tx_composition,
)
from rigplane.web.radio_poller import RadioPoller
from rigplane.web.server import WebConfig, WebServer
from rigplane.web.web_startup import (
    attach_managed_tx_composition,
    prepare_managed_tx_observation_generation,
)
from test_icom7610_serial_radio import _FakeSerialCivLink


def _serial_radio(cls: Any, link: _FakeSerialCivLink) -> Any:
    link.model_id = 0x94 if cls is Ic7300SerialRadio else 0x98

    async def refuse_probe(port: str) -> int | None:
        raise AssertionError(f"unexpected CI-V identity probe on {port!r}")

    return cls(
        device="/dev/ttyUSB0",
        civ_link=link,
        audio_driver=UsbAudioDriver(serial_port=None, backend=FakeAudioBackend()),
        # No rediscovery candidates, so no real serial port is ever opened.
        _civ_identity_probe=refuse_probe,
        _enumerate_serial_ports_fn=lambda: [],
    )


def _config() -> WebConfig:
    # read_only skips the connect-time VFO A selection, whose PTT read the
    # fake links never answer.
    return WebConfig(host="127.0.0.1", port=0, discovery=False, read_only=True)


def _writer() -> MagicMock:
    return MagicMock(drain=AsyncMock())


@asynccontextmanager
async def _cli_web(radio: Any, tmp_path: Path) -> AsyncIterator[WebServer]:
    """``cli/__init__.py: _run`` and ``_cmd_web``: the managed session connects
    the radio before ``WebServer`` is constructed."""

    composition = ManagedTxComposition(radio, config_path=tmp_path / "managed-tx.json")
    install_managed_tx_composition(radio, composition)
    session = _ManagedTxRadioSession(radio, composition)
    await session.__aenter__()
    try:
        server = WebServer(radio, _config())
        prepare_managed_tx_observation_generation(server)
        await composition.bind_state_store(server.command_state_store)
        attach_managed_tx_composition(server, composition)
        # The poller loop reads the radio, and nothing here answers.
        with patch.object(RadioPoller, "start"):
            await server.start()
            try:
                yield server
            finally:
                await server.stop()
    finally:
        await session.__aexit__(None, None, None)


async def test_cli_web_startup_publishes_main_for_a_single_receiver_radio(
    tmp_path: Path,
) -> None:
    radio = _serial_radio(Ic7300SerialRadio, _FakeSerialCivLink())
    async with _cli_web(radio, tmp_path) as server:
        assert server.command_state_store is radio.state_store
        assert server.command_state_store.provider_generation > 0
        assert server.build_public_state()["active"] == "MAIN"


async def test_main_survives_the_operator_disconnect(tmp_path: Path) -> None:
    radio = _serial_radio(Ic7300SerialRadio, _FakeSerialCivLink())
    async with _cli_web(radio, tmp_path) as server:
        store = server.command_state_store
        before = store.provider_generation
        await server._handle_radio_control("/api/v1/radio/disconnect", _writer())
        assert store.provider_generation > before
        assert server.build_public_state()["active"] == "MAIN"


async def test_operator_connect_republishes_main(tmp_path: Path) -> None:
    radio = _serial_radio(Ic7300SerialRadio, _FakeSerialCivLink())
    async with _cli_web(radio, tmp_path) as server:
        store = server.command_state_store
        await server._handle_radio_control("/api/v1/radio/disconnect", _writer())
        before = store.provider_generation
        await server._handle_radio_control("/api/v1/radio/connect", _writer())
        assert radio.connected
        assert store.provider_generation > before
        assert server.build_public_state()["active"] == "MAIN"


async def test_main_survives_a_serial_soft_reconnect(tmp_path: Path) -> None:
    link = _FakeSerialCivLink()
    radio = _serial_radio(Ic7300SerialRadio, link)
    async with _cli_web(radio, tmp_path) as server:
        store = server.command_state_store
        await radio._stop_civ_data_watchdog()
        link.ready = False
        link.healthy = False
        before = store.provider_generation
        await radio.soft_reconnect()
        assert radio.connected
        assert store.provider_generation > before
        assert server.build_public_state()["active"] == "MAIN"


@pytest.mark.parametrize("managed", [True, False], ids=["managed", "unmanaged"])
async def test_main_survives_the_fallback_store_advance_at_startup(
    managed: bool, tmp_path: Path
) -> None:
    async with FakeRigctldServer() as fake:
        radio = RigctldClientRadio(host=fake.host, port=fake.port, model="IC-7300")
        if managed:
            async with _cli_web(radio, tmp_path) as server:
                assert server.command_state_store.provider_generation > 0
                assert server.build_public_state()["active"] == "MAIN"
            return
        await radio.connect()
        server = WebServer(radio, _config())
        try:
            await server.start()
            assert server.command_state_store.provider_generation > 0
            assert server.build_public_state()["active"] == "MAIN"
        finally:
            await server.stop()
            await radio.disconnect()


async def test_a_two_receiver_radio_gets_no_topology_main(tmp_path: Path) -> None:
    radio = _serial_radio(Icom7610SerialRadio, _FakeSerialCivLink())
    async with _cli_web(radio, tmp_path) as server:
        assert server.build_public_state()["active"] is None
        await server._handle_radio_control("/api/v1/radio/disconnect", _writer())
        assert server.build_public_state()["active"] is None
