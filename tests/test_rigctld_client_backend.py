"""Tests for the external Hamlib ``rigctld`` client backend."""

from __future__ import annotations

import asyncio

import pytest

from fake_rigctld import FakeRigctldBehavior, FakeRigctldServer
from rigplane.backends.config import RigctldBackendConfig
from rigplane.backends.factory import create_radio
from rigplane.backends.rigctld_client import RigctldClientRadio, RigctldTransport
from rigplane.backends.rigctld_client.radio import (
    _float_to_level_255,
    _level_255_to_float,
)
from rigplane.exceptions import CommandError
from rigplane.exceptions import ConnectionError as RadioConnectionError
from rigplane.exceptions import TimeoutError as RadioTimeoutError


async def test_transport_connect_query_and_close() -> None:
    async with FakeRigctldServer() as server:
        transport = RigctldTransport(host=server.host, port=server.port)

        await transport.connect()
        try:
            assert transport.connected
            assert await transport.query("f", response_lines=1) == ["14074000"]
        finally:
            await transport.close()

        assert not transport.connected


async def test_transport_serializes_requests() -> None:
    behavior = FakeRigctldBehavior(command_delays={"f": 0.02})

    async with FakeRigctldServer(behavior=behavior) as server:
        transport = RigctldTransport(host=server.host, port=server.port)
        await transport.connect()
        try:
            results = await asyncio.gather(
                transport.query("f", response_lines=1),
                transport.query("t", response_lines=1),
            )
        finally:
            await transport.close()

    assert results == [["14074000"], ["0"]]
    assert server.commands_seen == ["f", "t"]


async def test_transport_timeout_eof_malformed_and_negative_rprt() -> None:
    timeout_behavior = FakeRigctldBehavior(command_delays={"f": 0.2})
    async with FakeRigctldServer(behavior=timeout_behavior) as server:
        transport = RigctldTransport(
            host=server.host,
            port=server.port,
            timeout=0.01,
        )
        await transport.connect()
        try:
            with pytest.raises(RadioTimeoutError, match="timed out"):
                await transport.query("f", response_lines=1)
        finally:
            await transport.close()

    eof_behavior = FakeRigctldBehavior(disconnect_commands={"f"})
    async with FakeRigctldServer(behavior=eof_behavior) as server:
        transport = RigctldTransport(host=server.host, port=server.port)
        await transport.connect()
        try:
            with pytest.raises(RadioConnectionError, match="closed"):
                await transport.query("f", response_lines=1)
        finally:
            await transport.close()

    malformed_behavior = FakeRigctldBehavior(malformed_responses={"F": b"nope\n"})
    async with FakeRigctldServer(behavior=malformed_behavior) as server:
        transport = RigctldTransport(host=server.host, port=server.port)
        await transport.connect()
        try:
            with pytest.raises(CommandError, match="malformed"):
                await transport.command("F 14074000")
        finally:
            await transport.close()

    unsupported_behavior = FakeRigctldBehavior(unsupported_commands={"F"})
    async with FakeRigctldServer(behavior=unsupported_behavior) as server:
        transport = RigctldTransport(host=server.host, port=server.port)
        await transport.connect()
        try:
            with pytest.raises(CommandError, match="unsupported"):
                await transport.command("F 14074000")
        finally:
            await transport.close()

    unsupported_query = FakeRigctldBehavior(unsupported_commands={"m"})
    async with FakeRigctldServer(behavior=unsupported_query) as server:
        transport = RigctldTransport(host=server.host, port=server.port)
        await transport.connect()
        try:
            with pytest.raises(CommandError, match="unsupported"):
                await transport.query("m", response_lines=2)
        finally:
            await transport.close()


async def test_radio_core_frequency_mode_ptt_and_vfo() -> None:
    async with FakeRigctldServer() as server:
        radio = RigctldClientRadio(host=server.host, port=server.port)
        await radio.connect()
        try:
            assert radio.connected
            assert radio.radio_ready
            assert radio.backend_id == "rigctld"
            assert radio.model == "External rigctld"
            assert radio.capabilities == {
                "tx",
                "vfo",
                "rf_gain",
                "af_level",
                "preamp",
                "attenuator",
                "nb",
                "nr",
            }
            assert radio.supports_command("set_freq")
            assert radio.supports_command("get_vfo_slot")
            assert not radio.supports_command("start_audio_rx_opus")

            assert await radio.get_freq() == 14_074_000
            await radio.set_freq(7_050_000)
            assert radio.radio_state.main.freq == 7_050_000

            assert await radio.get_mode() == ("USB", 2400)
            await radio.set_mode("LSB", 1800)
            assert radio.radio_state.main.mode == "LSB"
            assert radio.radio_state.main.filter_width == 1800

            assert await radio.get_ptt() is False
            await radio.set_ptt(True)
            assert radio.radio_state.ptt is True

            assert await radio.get_vfo_slot() == "A"
            await radio.set_vfo_slot("B")
            assert radio.radio_state.main.active_slot == "B"
        finally:
            await radio.disconnect()


async def test_radio_reports_actionable_connection_failure() -> None:
    radio = RigctldClientRadio(host="127.0.0.1", port=9, timeout=0.01)

    with pytest.raises(RadioConnectionError, match="127.0.0.1:9"):
        await radio.connect()


async def test_radio_rejects_unsupported_data_mode() -> None:
    async with FakeRigctldServer() as server:
        radio = RigctldClientRadio(host=server.host, port=server.port)
        await radio.connect()
        try:
            assert await radio.get_data_mode() is False
            with pytest.raises(CommandError, match="data mode"):
                await radio.set_data_mode(True)
        finally:
            await radio.disconnect()


def test_config_factory_builds_rigctld_client_backend() -> None:
    config = RigctldBackendConfig(host="localhost")

    radio = create_radio(config)

    assert isinstance(radio, RigctldClientRadio)
    assert config.backend == "rigctld"
    assert config.port == 4532
    assert radio.backend_id == "rigctld"


def test_config_validates_rigctld_client_backend() -> None:
    with pytest.raises(ValueError, match="host"):
        RigctldBackendConfig(host="")
    with pytest.raises(ValueError, match="port"):
        RigctldBackendConfig(host="localhost", port=0)
    with pytest.raises(ValueError, match="timeout"):
        RigctldBackendConfig(host="localhost", timeout=0)


async def test_rigctld_levels_roundtrip() -> None:
    async with FakeRigctldServer() as server:
        radio = RigctldClientRadio(host=server.host, port=server.port)
        await radio.connect()
        try:
            await radio.set_rf_gain(200)
            assert abs(await radio.get_rf_gain() - 200) <= 2

            await radio.set_af_level(120)
            assert abs(await radio.get_af_level() - 120) <= 2
        finally:
            await radio.disconnect()


async def test_rigctld_preamp_attenuator_nb_nr() -> None:
    async with FakeRigctldServer() as server:
        radio = RigctldClientRadio(host=server.host, port=server.port)
        await radio.connect()
        try:
            assert await radio.get_preamp() == 0
            await radio.set_preamp(1)
            assert await radio.get_preamp() == 1

            assert await radio.get_attenuator() is False
            await radio.set_attenuator(True)
            assert await radio.get_attenuator() is True
            assert await radio.get_attenuator_level() == 6

            assert await radio.get_nb() is False
            await radio.set_nb(True)
            assert await radio.get_nb() is True

            assert await radio.get_nr() is False
            await radio.set_nr(True)
            assert await radio.get_nr() is True
        finally:
            await radio.disconnect()


async def test_rigctld_unsupported_level_raises_command_error() -> None:
    behavior = FakeRigctldBehavior(unsupported_commands={"l RF"})
    async with FakeRigctldServer(behavior=behavior) as server:
        radio = RigctldClientRadio(host=server.host, port=server.port)
        await radio.connect()
        try:
            with pytest.raises(CommandError):
                await radio.get_rf_gain()
        finally:
            await radio.disconnect()


async def test_rigctld_capabilities_include_levels() -> None:
    async with FakeRigctldServer() as server:
        radio = RigctldClientRadio(host=server.host, port=server.port)
        await radio.connect()
        try:
            assert {
                "rf_gain",
                "af_level",
                "preamp",
                "attenuator",
                "nb",
                "nr",
            } <= radio.capabilities
            for command in (
                "get_rf_gain",
                "set_rf_gain",
                "get_af_level",
                "set_af_level",
                "get_preamp",
                "set_preamp",
                "get_attenuator",
                "set_attenuator",
                "get_nb",
                "set_nb",
                "get_nr",
                "set_nr",
            ):
                assert radio.supports_command(command)
        finally:
            await radio.disconnect()


def test_level_scale_conversions_roundtrip_and_clamp() -> None:
    # Round-trip: a 0..255 level survives encode->decode within rounding.
    for level in (0, 50, 128, 200, 255):
        encoded = _level_255_to_float(level)
        assert abs(_float_to_level_255(float(encoded)) - level) <= 1

    # Clamp at boundaries.
    assert _level_255_to_float(-10) == "0.000"
    assert _level_255_to_float(999) == "1.000"
    assert _float_to_level_255(-1.0) == 0
    assert _float_to_level_255(2.0) == 255
    assert _float_to_level_255(0.0) == 0
    assert _float_to_level_255(1.0) == 255


# ---------------------------------------------------------------------------
# Stale-buffer / re-sync hardening tests (MOR-182)
# ---------------------------------------------------------------------------


async def test_command_drains_stray_preceding_line() -> None:
    """SET command must succeed even when a stray value line precedes RPRT 0.

    Regression: L AF 0.784 → server sends "0.0392157\\nRPRT 0\\n"; transport
    used to read only one line, consuming the stray value and then
    _parse_rprt("0.0392157") raised CommandError.
    """
    behavior = FakeRigctldBehavior(extra_lines={"L AF 0.784": b"0.0392157\n"})
    async with FakeRigctldServer(behavior=behavior) as server:
        transport = RigctldTransport(host=server.host, port=server.port)
        await transport.connect()
        try:
            # Must NOT raise; real RPRT 0 follows the stray line.
            await transport.command("L AF 0.784")
        finally:
            await transport.close()


async def test_leftover_line_discarded_between_transactions() -> None:
    """A leftover line in the buffer from transaction A must not corrupt B.

    Simulates the U NB 1 → "0\\nRPRT 0\\n" scenario: if transaction A
    somehow leaves a line in the reader, the pre-drain in transaction B
    eats it so B reads its own RPRT 0.
    """
    behavior = FakeRigctldBehavior(extra_lines={"U NB 1": b"0\n"})
    async with FakeRigctldServer(behavior=behavior) as server:
        transport = RigctldTransport(host=server.host, port=server.port)
        await transport.connect()
        try:
            # First call: server sends "0\nRPRT 0\n".  With the re-sync loop
            # inside command() this should succeed.
            await transport.command("U NB 1")
            # Second call with a normal command must still work (no leftover
            # from the first lingering in the buffer).
            await transport.command("U NB 0")
        finally:
            await transport.close()


async def test_get_reads_value_after_drain() -> None:
    """GET (query) path is unaffected by the pre-drain (no leftover → no-op)."""
    async with FakeRigctldServer() as server:
        transport = RigctldTransport(host=server.host, port=server.port)
        await transport.connect()
        try:
            result = await transport.query("l AF", response_lines=1)
        finally:
            await transport.close()
    assert result == ["0.300"]


async def test_negative_rprt_still_raises() -> None:
    """l RF → RPRT -11 (unsupported) must still raise CommandError.

    The re-sync loop must not discard RPRT-shaped lines — it must accept
    them immediately so _raise_rprt can fire.
    """
    behavior = FakeRigctldBehavior(unsupported_commands={"l RF"})
    async with FakeRigctldServer(behavior=behavior) as server:
        transport = RigctldTransport(host=server.host, port=server.port)
        await transport.connect()
        try:
            with pytest.raises(CommandError, match="command failed|unsupported"):
                await transport.command("l RF")
        finally:
            await transport.close()
