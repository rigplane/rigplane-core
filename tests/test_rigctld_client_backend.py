"""Tests for the external Hamlib ``rigctld`` client backend."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock

import pytest

from fake_rigctld import FakeRigctldBehavior, FakeRigctldServer
from rigplane.backends.config import RigctldBackendConfig
from rigplane.backends.factory import create_radio
from rigplane.backends.rigctld_client import RigctldClientRadio, RigctldTransport
from rigplane.backends.rigctld_client.radio import (
    _float_to_level_255,
    _level_255_to_float,
    _preamp_level_to_db,
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


async def test_radio_transport_loss_advances_provider_generation_once() -> None:
    behavior = FakeRigctldBehavior(disconnect_commands={"f"})
    async with FakeRigctldServer(behavior=behavior) as server:
        radio = RigctldClientRadio(host=server.host, port=server.port)
        await radio.connect()
        transitions: list[bool] = []
        radio.bind_provider_generation(
            advance=lambda: transitions.append(radio.connected) or len(transitions)
        )

        with pytest.raises(RadioConnectionError, match="closed"):
            await radio.get_freq()

        assert transitions == [False]
        with pytest.raises(RadioConnectionError, match="not connected"):
            await radio.get_freq()
        assert transitions == [False]
        await radio.disconnect()
        assert transitions == [False]


@pytest.mark.parametrize(
    ("stage", "read_result", "message"),
    (
        ("stale", b"", "closed"),
        ("stale", OSError("stale read lost"), "failed while reading"),
        ("resync", b"", "closed"),
        ("resync", OSError("resync read lost"), "failed while reading"),
    ),
)
async def test_radio_buffered_read_loss_retires_without_subsequent_write(
    stage: str,
    read_result: bytes | OSError,
    message: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async with FakeRigctldServer() as server:
        radio = RigctldClientRadio(host=server.host, port=server.port)
        await radio.connect()
        transitions: list[bool] = []
        radio.bind_provider_generation(
            advance=lambda: transitions.append(radio.connected) or len(transitions)
        )
        reader = radio._transport._reader  # noqa: SLF001
        writer = radio._transport._writer  # noqa: SLF001
        assert reader is not None and writer is not None
        writes: list[bytes] = []
        monkeypatch.setattr(writer, "write", writes.append)
        failing_read = (
            AsyncMock(side_effect=read_result)
            if isinstance(read_result, OSError)
            else AsyncMock(return_value=read_result)
        )
        if stage == "stale":
            monkeypatch.setattr(reader, "read", failing_read)
        else:
            monkeypatch.setattr(reader, "read", AsyncMock(side_effect=TimeoutError))
            monkeypatch.setattr(
                reader, "readline", AsyncMock(side_effect=[b"stray\n", read_result])
            )

        with pytest.raises(RadioConnectionError, match=message):
            await (radio.get_freq() if stage == "stale" else radio.set_freq(7_050_000))

        assert writes == ([] if stage == "stale" else [b"F 7050000\n"])
        assert transitions == [False]
        await radio.disconnect()
        assert transitions == [False]


@pytest.mark.parametrize("failure", ("read_timeout", "write_timeout", "oserror"))
async def test_radio_transport_failures_advance_provider_generation_once(
    failure: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    behavior = FakeRigctldBehavior(
        command_delays={"f": 0.2} if failure == "read_timeout" else {}
    )
    async with FakeRigctldServer(behavior=behavior) as server:
        radio = RigctldClientRadio(
            host=server.host,
            port=server.port,
            timeout=0.01 if failure == "read_timeout" else 5.0,
        )
        await radio.connect()
        transitions: list[bool] = []
        radio.bind_provider_generation(
            advance=lambda: transitions.append(radio.connected) or len(transitions)
        )
        writer = radio._transport._writer  # noqa: SLF001
        assert writer is not None
        if failure == "write_timeout":
            monkeypatch.setattr(writer, "drain", AsyncMock(side_effect=TimeoutError))
        elif failure == "oserror":
            monkeypatch.setattr(writer, "drain", AsyncMock(side_effect=OSError("lost")))

        error = RadioConnectionError if failure == "oserror" else RadioTimeoutError
        operation = (
            radio.set_freq(7_050_000) if failure != "read_timeout" else radio.get_freq()
        )
        with pytest.raises(error):
            await operation

        assert transitions == [False]
        await radio.disconnect()
        assert transitions == [False]


async def test_radio_reconnect_does_not_double_advance_transport_generation() -> None:
    behavior = FakeRigctldBehavior(disconnect_commands={"f"})
    async with FakeRigctldServer(behavior=behavior) as server:
        radio = RigctldClientRadio(host=server.host, port=server.port)
        await radio.connect()
        generations: list[int] = []
        radio.bind_provider_generation(
            advance=lambda: generations.append(len(generations) + 1) or generations[-1]
        )

        with pytest.raises(RadioConnectionError):
            await radio.get_freq()
        assert generations == [1]

        await radio.connect()
        assert radio.connected
        assert generations == [1]

        await radio.disconnect()
        assert generations == [1, 2]
        await radio.disconnect()
        assert generations == [1, 2]


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
            assert radio.radio_state.main.freq == 14_074_000
            assert await radio.get_freq() == 7_050_000
            assert radio.radio_state.main.freq == 7_050_000

            assert await radio.get_mode() == ("USB", 2400)
            await radio.set_mode("LSB", 1800)
            assert radio.radio_state.main.mode == "USB"
            assert radio.radio_state.main.filter_width == 2400
            assert await radio.get_mode() == ("LSB", 1800)
            assert radio.radio_state.main.mode == "LSB"
            assert radio.radio_state.main.filter_width == 1800

            assert await radio.get_ptt() is False
            await radio.set_ptt(True)
            assert radio.radio_state.ptt is False
            assert await radio.get_ptt() is True
            assert radio.radio_state.ptt is True

            assert await radio.get_vfo_slot() == "A"
            await radio.set_vfo_slot("B")
            assert radio.radio_state.main.active_slot == "A"
            assert await radio.get_vfo_slot() == "B"
            assert radio.radio_state.main.active_slot == "B"
        finally:
            await radio.disconnect()

    assert server.commands_seen == [
        "v",
        "f",
        "F 7050000",
        "f",
        "m",
        "M LSB 1800",
        "m",
        "t",
        "T 1",
        "t",
        "v",
        "V VFOB",
        "v",
    ]


async def test_failed_core_setters_leave_radio_state_unchanged() -> None:
    behavior = FakeRigctldBehavior(unsupported_commands={"F", "M", "T", "V"})
    async with FakeRigctldServer(behavior=behavior) as server:
        radio = RigctldClientRadio(host=server.host, port=server.port)
        await radio.connect()
        try:
            assert await radio.get_freq() == 14_074_000
            assert await radio.get_mode() == ("USB", 2400)
            assert await radio.get_ptt() is False
            assert await radio.get_vfo_slot() == "A"

            for setter in (
                radio.set_freq(7_050_000),
                radio.set_mode("LSB", 1800),
                radio.set_ptt(True),
                radio.set_vfo_slot("B"),
            ):
                with pytest.raises(CommandError, match="unsupported"):
                    await setter
                assert radio.radio_state.main.freq == 14_074_000
                assert radio.radio_state.main.mode == "USB"
                assert radio.radio_state.main.filter_width == 2400
                assert radio.radio_state.ptt is False
                assert radio.radio_state.main.active_slot == "A"
        finally:
            await radio.disconnect()

    assert server.commands_seen == [
        "v",
        "f",
        "m",
        "t",
        "v",
        "F 7050000",
        "M LSB 1800",
        "T 1",
        "V VFOB",
    ]


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


async def test_get_data_mode_does_not_synthesize_private_radio_state() -> None:
    """MOR-434: a public read returns a flat value, not synthesized state.

    ``get_data_mode`` is the representative public read with no live
    rigctld query: it returns a flat ``False`` and must not fabricate or
    mutate the private ``self._state`` ``RadioState`` mirror. The consumer
    pipeline is fed by ``RigctldClientObservationAdapter`` instead; the
    ``_state`` mirror is legacy compat only and stays untouched by reads
    that have no observation to apply.
    """
    async with FakeRigctldServer() as server:
        radio = RigctldClientRadio(host=server.host, port=server.port)
        await radio.connect()
        try:
            state_before = radio.radio_state
            data_mode_before = state_before.main.data_mode

            result = await radio.get_data_mode()

            assert result is False
            # No new RadioState synthesized — same object identity.
            assert radio.radio_state is state_before
            # No mutation of the legacy private mirror.
            assert radio.radio_state.main.data_mode == data_mode_before
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


def test_preamp_level_to_db_rejects_out_of_domain_level() -> None:
    """MOR-1529: an unrecognized preamp level must fail loud, not be
    silently coerced to OFF (0 dB) — this backend has no ``RigProfile`` to
    validate against (it talks to an already-running external rigctld
    daemon), so its own fixed 0/1/2 mapping must reject anything else."""
    for legal in (0, 1, 2):
        assert _preamp_level_to_db(legal) in {"0", "10", "20"}
    with pytest.raises(ValueError, match=r"preamp level must be one of"):
        _preamp_level_to_db(3)
    with pytest.raises(ValueError, match=r"preamp level must be one of"):
        _preamp_level_to_db(-1)


async def test_rigctld_set_preamp_out_of_domain_raises_not_silently_off() -> None:
    async with FakeRigctldServer() as server:
        radio = RigctldClientRadio(host=server.host, port=server.port)
        await radio.connect()
        try:
            with pytest.raises(ValueError, match=r"preamp level must be one of"):
                await radio.set_preamp(3)
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
