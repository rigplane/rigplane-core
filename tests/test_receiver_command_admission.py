"""Opt-in receiver admission for the three receive-level setters."""

from dataclasses import replace
from pathlib import Path

import pytest

from fake_rigctld import FakeRigctldServer
from rigplane.backends.rigctld_client.radio import RigctldClientRadio
from rigplane.backends.yaesu_cat.radio import YaesuCatRadio
from rigplane.commands.command_spec import CatCommandSpec
from rigplane.core.exceptions import CommandError
from rigplane.profiles.rig_loader import load_rig
from rigplane.runtime.radio import CoreRadio

RIGS = Path(__file__).parents[1] / "rigs"
LEVELS = ("set_af_level", "set_rf_gain", "set_squelch")


class CatSink:
    connected = True

    def __init__(self):
        self.writes = []

    async def write(self, command):
        self.writes.append(command)


def yaesu(config=None):
    radio = YaesuCatRadio("/dev/null", profile=config or load_rig(RIGS / "ftx1.toml"))
    radio._transport = CatSink()
    return radio


def icom(config=None):
    return CoreRadio(
        "127.0.0.1", profile=(config or load_rig(RIGS / "ic7300.toml")).to_profile()
    )


@pytest.mark.parametrize("command", LEVELS)
@pytest.mark.parametrize("receiver", [0, 1])
async def test_ftx1_selected_receiver_is_admitted_and_written(command, receiver):
    radio = yaesu()
    assert radio.supports_command(command, receiver=receiver)
    await getattr(radio, command)(128, receiver=receiver)
    prefix = {"set_af_level": "AG", "set_rf_gain": "RG", "set_squelch": "SQ"}[command]
    assert radio._transport.writes == [f"{prefix}{receiver}128;"]


@pytest.mark.parametrize("command", LEVELS)
@pytest.mark.parametrize("missing", ["entry", "write"])
async def test_missing_sub_write_preserves_main_only(command, missing):
    config = load_rig(RIGS / "ftx1.toml")
    commands = dict(config.commands)
    key = f"{command}_sub"
    if missing == "entry":
        del commands[key]
    else:
        commands[key] = CatCommandSpec(read="ZZ;")
    radio = yaesu(replace(config, commands=commands))
    assert radio.supports_command(command)
    assert radio.supports_command(command, receiver=0)
    assert not radio.supports_command(command, receiver=1)
    with pytest.raises(CommandError):
        await getattr(radio, command)(128, receiver=1)
    assert radio._transport.writes == []
    await getattr(radio, command)(128, receiver=0)
    assert len(radio._transport.writes) == 1


@pytest.mark.parametrize("command", LEVELS)
async def test_ftx1_single_receiver_topology_rejects_existing_sub_template(command):
    config = replace(load_rig(RIGS / "ftx1.toml"), receiver_count=1)
    radio = yaesu(config)
    assert radio.supports_command(command, receiver=0)
    assert not radio.supports_command(command, receiver=1)
    with pytest.raises(ValueError):
        await getattr(radio, command)(128, receiver=1)
    assert radio._transport.writes == []


@pytest.mark.parametrize("command", LEVELS)
@pytest.mark.parametrize("receiver", [-1, 2, True, False, 0.0, "0"])
def test_invalid_receiver_query_is_false(command, receiver):
    for radio in (icom(), yaesu(), RigctldClientRadio(host="127.0.0.1")):
        assert not radio.supports_command(command, receiver=receiver)


@pytest.mark.parametrize("command", LEVELS)
@pytest.mark.parametrize("receiver", [-1, 2, True, 0.0, "0"])
async def test_invalid_yaesu_receiver_never_writes(command, receiver):
    radio = yaesu()
    with pytest.raises((TypeError, ValueError)):
        await getattr(radio, command)(128, receiver=receiver)
    assert radio._transport.writes == []


@pytest.mark.parametrize("command", LEVELS)
async def test_ic7300_main_write_and_sub_rejection(command):
    radio = icom()
    writes = []

    async def write(frame, wait_response=True):
        writes.append(frame)

    radio._connected = True
    radio._send_civ_raw = write
    try:
        assert radio.supports_command(command, receiver=0)
        assert not radio.supports_command(command, receiver=1)
        with pytest.raises(CommandError):
            await getattr(radio, command)(128, receiver=1)
        assert writes == []
        await getattr(radio, command)(128, receiver=0)
        sub = {"set_af_level": 1, "set_rf_gain": 2, "set_squelch": 3}[command]
        assert writes == [bytes([0xFE, 0xFE, 0x94, 0xE0, 0x14, sub, 1, 0x28, 0xFD])]
    finally:
        radio._connected = False


@pytest.mark.parametrize("command", LEVELS)
@pytest.mark.parametrize("missing", ["command", "capability", "route"])
def test_icom_admission_honors_setter_profile_requirements(command, missing):
    config = load_rig(RIGS / "ic7610.toml")
    assert icom(config).supports_command(command, receiver=1)
    if missing == "command":
        config = replace(
            config, commands={k: v for k, v in config.commands.items() if k != command}
        )
    elif missing == "capability":
        config = replace(
            config,
            capabilities=tuple(
                c for c in config.capabilities if c != command.removeprefix("set_")
            ),
        )
    else:
        config = replace(config, cmd29_routes=())
    radio = icom(config)
    assert not radio.supports_command(command, receiver=1)
    assert radio.supports_command(command, receiver=0) == (missing == "route")


@pytest.mark.parametrize(
    "factory", [icom, yaesu, lambda: RigctldClientRadio(host="127.0.0.1")]
)
def test_name_only_is_unchanged_and_receiver_admission_is_opt_in(factory):
    radio = factory()
    for command in (*LEVELS, "get_freq", "set_preamp", "unknown", "set_af_level_sub"):
        assert radio.supports_command(command) == radio.supports_command(
            command, receiver=None
        )
    assert radio.supports_command("get_freq")
    for command in ("get_freq", "set_preamp", "unknown", "set_af_level_sub"):
        assert not radio.supports_command(command, receiver=0)
        assert not radio.supports_command(command, receiver=1)
    if isinstance(radio, YaesuCatRadio):
        assert not any(radio.supports_command(f"{command}_sub") for command in LEVELS)


@pytest.mark.parametrize("command", LEVELS)
def test_non_callable_level_cannot_be_receiver_admitted(command):
    for radio in (icom(), yaesu(), RigctldClientRadio(host="127.0.0.1")):
        setattr(radio, command, None)
        assert not radio.supports_command(command, receiver=0)


@pytest.mark.parametrize("command", LEVELS)
async def test_rigctld_main_only_and_sql_unsupported(command):
    async with FakeRigctldServer() as server:
        radio = RigctldClientRadio(host=server.host, port=server.port)
        await radio.connect()
        try:
            supported = command != "set_squelch"
            assert radio.supports_command(command) == supported
            assert radio.supports_command(command, receiver=0) == supported
            assert not radio.supports_command(command, receiver=1)
            before = list(server.commands_seen)
            if supported:
                with pytest.raises(CommandError):
                    await getattr(radio, command)(128, receiver=1)
                assert server.commands_seen == before
                await getattr(radio, command)(128, receiver=0)
                level = "AF" if command == "set_af_level" else "RF"
                assert server.commands_seen[len(before) :] == [
                    f"L {level} {128 / 255:.3f}"
                ]
            else:
                assert server.commands_seen == before
        finally:
            await radio.disconnect()
