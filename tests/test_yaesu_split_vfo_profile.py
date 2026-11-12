"""MOR-4427: real-loader contracts for a non-product single-RX fixture."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from rigplane.backends.yaesu_cat.poller import YaesuCatPoller
from rigplane.backends.yaesu_cat.radio import YaesuCatRadio
from rigplane.commands.command_spec import AbsentCommandSpec
from rigplane.profiles.rig_loader import RigLoadError, load_rig
from rigplane.web.handlers.control import ControlHandler
from rigplane.web.radio_poller import VfoSwap
from rigplane.web.runtime_helpers import projected_vfo_capability_tags
from rigplane_testkit.sim.adapters.yaesu_cat import YaesuCatSerialFake

ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / "tests/fixtures/rigs/yaesu_single_rx_fixture.toml"
SIM_FIXTURE = ROOT / "rigplane_testkit/sim/profiles/yaesu-single-rx-fixture.toml"


@pytest.fixture
def radio():
    result = YaesuCatRadio("/dev/null", profile=load_rig(FIXTURE))
    result._transport._connected = True
    result._transport.write = AsyncMock()
    result._transport.query = AsyncMock()
    return result


@pytest.mark.asyncio
async def test_split_uses_separate_write_and_answer_maps(radio):
    await radio.set_split(True)
    await radio.set_split(False)
    assert [c.args[0] for c in radio._transport.write.call_args_list] == [
        "FT3;",
        "FT2;",
    ]
    radio._transport.query.side_effect = ["FT1", "FT0"]
    assert await radio.read_split() is True
    assert await radio.get_split() is False
    assert [c.args[0] for c in radio._transport.query.call_args_list] == ["FT;", "FT;"]
    assert radio.receiver_count == radio.transceiver_count == 1


@pytest.mark.asyncio
async def test_direct_vfo_commands_and_absent_selection(radio):
    await radio.swap_vfo_ab()
    await radio.vfo_a_to_b()
    await radio.vfo_b_to_a()
    await radio.set_freq(14_074_000)
    await radio.set_freq(14_080_000, receiver=1)
    assert [c.args[0] for c in radio._transport.write.call_args_list] == [
        "SV;",
        "AB;",
        "BA;",
        "FA014074000;",
        "FB014080000;",
    ]
    for operation in (
        radio.get_vfo_slot(),
        radio.set_vfo_slot("B"),
        radio.get_vfo_select(),
        radio.set_vfo_select(1),
    ):
        with pytest.raises(NotImplementedError):
            await operation
    assert await radio.get_active_receiver() == 0
    assert not radio.supports_command("get_vfo_select")
    assert not radio.supports_command("get_vfo_slot")
    assert radio.supports_command("swap_vfo_ab")
    assert radio._transport.query.call_count == 0


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "replacement", ["", 'vfo_swap = { absent = "Fixture variant" }']
)
async def test_missing_swap_follows_absent_convention(tmp_path, replacement):
    path = tmp_path / "fixture.toml"
    path.write_text(
        FIXTURE.read_text().replace(
            'vfo_swap = { cat = { write = "SV;" } }', replacement
        )
    )
    config = load_rig(path)
    if replacement:
        assert isinstance(config.commands["vfo_swap"], AbsentCommandSpec)
    radio = YaesuCatRadio("/dev/null", profile=config)
    with pytest.raises(NotImplementedError):
        await radio.swap_vfo_ab()
    assert not radio.supports_command("swap_vfo_ab")


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "profile,select_read", [(FIXTURE, False), (ROOT / "rigs/ftx1.toml", True)]
)
async def test_poller_only_reads_declared_vfo_selection(profile, select_read):
    radio = YaesuCatRadio("/dev/null", profile=load_rig(profile))
    radio._transport._connected = True
    wire: list[str] = []

    async def query(command, **kwargs):
        wire.append(command)
        return {"VS;": "VS0", "FT;": "FT0", "ST;": "ST0"}.get(command, "?")

    radio._transport.query = query
    await YaesuCatPoller(radio)._poll_slow()
    assert ("VS;" in wire) is select_read
    if not select_read:
        assert "FR;" not in wire


@pytest.mark.asyncio
async def test_ftx1_identity_maps_and_profile_transceiver_count():
    config = load_rig(ROOT / "rigs/ftx1.toml")
    radio = YaesuCatRadio("/dev/null", profile=config)
    radio._transport._connected = True
    radio._transport.write = AsyncMock()
    radio._transport.query = AsyncMock(side_effect=["ST1", "ST0"])
    assert config.transceiver_count == radio.transceiver_count == 2
    await radio.set_split(True)
    await radio.set_split(False)
    assert [c.args[0] for c in radio._transport.write.call_args_list] == [
        "ST1;",
        "ST0;",
    ]
    assert await radio.get_split() is True
    assert await radio.get_split() is False
    assert [c.args[0] for c in radio._transport.query.call_args_list] == ["ST;", "ST;"]
    with pytest.raises(NotImplementedError):
        await radio.swap_vfo_ab()


@pytest.mark.parametrize(
    "replacement,match",
    [
        ('write_map = { off = "2" }', "write_map"),
        ('write_map = { off = "2", on = 3 }', "write_map"),
        ('write_map = { off = "2", on = "2" }', "write_map"),
        ('read_map = { "0" = "off" }', "read_map"),
        ('read_map = { "0" = "off", "1" = "maybe" }', "read_map"),
        ('read_map = ["off", "on"]', "read_map"),
    ],
)
def test_split_validation(tmp_path, replacement, match):
    text = FIXTURE.read_text()
    key = replacement.split(" =", 1)[0]
    lines = [
        replacement if line.startswith(key + " =") else line
        for line in text.splitlines()
    ]
    path = tmp_path / "invalid.toml"
    path.write_text("\n".join(lines))
    with pytest.raises(RigLoadError, match=match):
        load_rig(path)


def test_split_contract_is_typed_and_consumed():
    config = load_rig(FIXTURE)
    assert config.split.write_map == {False: "2", True: "3"}
    assert config.split.read_map == {"0": False, "1": True}
    radio = YaesuCatRadio("/dev/null", profile=config)
    assert projected_vfo_capability_tags(radio, None) == {"vfo_swap"}


def test_transceiver_count_comes_from_profile_without_model_branch(tmp_path):
    path = tmp_path / "two-transceivers.toml"
    path.write_text(
        FIXTURE.read_text().replace("[radio]", "[radio]\ntransceiver_count = 2")
    )
    radio = YaesuCatRadio("/dev/null", profile=load_rig(path))
    assert radio.transceiver_count == 2


def test_web_admits_declared_cat_ab_swap(radio):
    handler = ControlHandler.__new__(ControlHandler)
    handler._radio = radio
    queued: list[object] = []
    queue = SimpleNamespace(put=queued.append)
    assert handler._enqueue_rc_frequency("vfo_swap", {}, queue, radio) == {}
    assert len(queued) == 1
    assert isinstance(queued[0], VfoSwap)
    with pytest.raises(ValueError, match="not supported"):
        handler._enqueue_rc_frequency("vfo_equalize", {}, queue, radio)


@pytest.mark.asyncio
async def test_yaesu_poller_dispatches_cat_ab_swap(radio):
    poller = YaesuCatPoller(radio)
    poller._post_write_freq_mode_readback = AsyncMock()
    await poller._execute_command(VfoSwap())
    radio._transport.write.assert_awaited_once_with("SV;")


@pytest.mark.asyncio
async def test_fixture_split_and_vfo_roundtrip_over_cat_transport(monkeypatch):
    fake = YaesuCatSerialFake.from_profile(ROOT, SIM_FIXTURE, rig_path=FIXTURE)
    wire: list[str] = []
    handle = fake.model.handle_with_outcome

    def logged(line):
        wire.append(line)
        return handle(line)

    monkeypatch.setattr(fake.model, "handle_with_outcome", logged)

    async def open_port(*args, **kwargs):
        return await fake.open_serial_connection()

    monkeypatch.setattr(
        "rigplane.backends.yaesu_cat.transport.open_serial_port", open_port
    )
    radio = YaesuCatRadio("/dev/fixture", profile=load_rig(FIXTURE))
    await radio._transport.connect()
    try:
        for enabled in (True, False):
            await radio.set_split(enabled)
            assert await radio.get_split() is enabled
        await radio.set_freq(14_074_000)
        await radio.set_freq(14_080_000, receiver=1)
        await radio.swap_vfo_ab()
        assert await radio.get_freq() == 14_080_000
        assert await radio.get_freq(1) == 14_074_000
        await radio.vfo_a_to_b()
        assert await radio.get_freq(1) == 14_080_000
        await radio.set_freq(14_090_000, receiver=1)
        await radio.vfo_b_to_a()
        assert await radio.get_freq() == 14_090_000
        await YaesuCatPoller(radio)._poll_slow()
        assert not any(cmd.startswith(("VS", "FR", "ST")) for cmd in wire)
        for command in ("VS;", "FR;", "ST;", "VS1;", "FR1;", "ST1;"):
            assert fake.model.handle(command) == "?;"
    finally:
        await radio._transport.close()
