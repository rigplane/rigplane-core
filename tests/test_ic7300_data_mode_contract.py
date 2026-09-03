"""IC-7300 DATA-mode profile wire/domain contract (MOR-2265)."""

from __future__ import annotations

import pytest

from rigplane.commands import set_data_mode
from rigplane.commands._frame import _build_from_map
from rigplane.commands.bound import BoundCommands
from rigplane.commands.command_map import CommandMap
from rigplane.profiles import resolve_radio_profile
from rigplane.radio import IcomRadio
from rigplane.types import CivFrame
from rigplane.web.handlers.control import ControlHandler
from rigplane.web.radio_poller import RadioPoller
from rigplane.web.server import WebServer


def _without(command_map: CommandMap, name: str) -> CommandMap:
    commands = {key: command_map.get(key) for key in command_map}
    variants = {
        1: (0x1A, 0x06, 0x01, 0x01),
    }
    assert name == "0"
    return CommandMap(commands, value_variants={"set_data_mode": variants})


def test_ic7300_data_off_has_documented_filter_byte() -> None:
    profile = resolve_radio_profile(model="IC-7300")

    assert BoundCommands(profile.command_map).set_data_mode(
        0, to_addr=profile.civ_addr
    ) == bytes.fromhex("fefe94e01a060000fd")


@pytest.mark.parametrize("mode", [2, 3])
def test_ic7300_refuses_undeclared_data_variants(mode: int) -> None:
    profile = resolve_radio_profile(model="IC-7300")

    with pytest.raises(ValueError, match="not declared"):
        BoundCommands(profile.command_map).set_data_mode(mode, to_addr=profile.civ_addr)


def test_ic7300_data1_uses_documented_filter_one() -> None:
    profile = resolve_radio_profile(model="IC-7300")

    assert BoundCommands(profile.command_map).set_data_mode(
        True, to_addr=profile.civ_addr
    ) == bytes.fromhex("fefe94e01a060101fd")


def test_ic7300_exposes_one_command_name_for_all_data_values() -> None:
    profile = resolve_radio_profile(model="IC-7300")

    assert profile.supports_command("set_data_mode")
    assert not profile.supports_command("set_data_mode_off")
    assert not profile.supports_command("set_data_mode_data1")
    assert len(profile.command_names) == 320
    assert len(profile.command_map) == 320
    assert list(profile.command_map).count("set_data_mode") == 1
    assert all(not name.startswith("set_data_mode_") for name in profile.command_map)


def test_declared_data_domain_fails_closed_when_variant_is_removed() -> None:
    profile = resolve_radio_profile(model="IC-7300")
    without_off = _without(profile.command_map, "0")

    with pytest.raises(ValueError, match="not declared"):
        BoundCommands(without_off).set_data_mode(0, to_addr=profile.civ_addr)


@pytest.mark.parametrize(
    ("variants", "expected"),
    [
        (
            {
                0: (0x1A, 0x06, 0x00),
                1: (0x1A, 0x06, 0x01, 0x01),
            },
            "fefe94e01a0600fd",
        ),
        (
            {
                0: (0x1A, 0x06, 0x00, 0x00),
                1: (0x1A, 0x06, 0x01, 0x02),
            },
            "fefe94e01a060102fd",
        ),
    ],
)
def test_wire_output_tracks_the_declared_variant_bytes(
    variants: dict[int, tuple[int, ...]], expected: str
) -> None:
    value = 0 if expected.endswith("0600fd") else 1
    command_map = CommandMap(
        {"set_data_mode": (0x1A, 0x06)},
        value_variants={"set_data_mode": variants},
    )

    assert set_data_mode(value, to_addr=0x94, cmd_map=command_map) == bytes.fromhex(
        expected
    )


def test_variant_builder_requires_value_and_forbids_appended_data() -> None:
    command_map = CommandMap(
        {"set_x": (0x1A, 0x06)},
        value_variants={"set_x": {0: (0x1A, 0x06, 0x00, 0x00)}},
    )

    with pytest.raises(ValueError, match="requires a declared value"):
        _build_from_map(command_map, "set_x", to_addr=0x94)
    with pytest.raises(ValueError, match="cannot combine"):
        _build_from_map(command_map, "set_x", to_addr=0x94, value=0, data=b"\xff")


@pytest.mark.parametrize("mode", range(4))
def test_profiles_without_data_variants_keep_legacy_domain(mode: int) -> None:
    command_map = CommandMap({"set_data_mode": (0x1A, 0x06)})

    assert set_data_mode(mode, to_addr=0x94, cmd_map=command_map) == bytes(
        [0xFE, 0xFE, 0x94, 0xE0, 0x1A, 0x06, mode, 0xFD]
    )


def test_data_mode_rejects_values_outside_the_legacy_wire_domain() -> None:
    with pytest.raises(ValueError, match="0-3"):
        set_data_mode(
            4, to_addr=0x94, cmd_map=CommandMap({"set_data_mode": (0x1A, 0x06)})
        )

    profile = resolve_radio_profile(model="IC-7300")
    with pytest.raises(ValueError, match="0-3"):
        BoundCommands(profile.command_map).set_data_mode(4, to_addr=profile.civ_addr)


@pytest.mark.asyncio
async def test_control_queue_poller_core_radio_bound_commands_send_data_off() -> None:
    profile = resolve_radio_profile(model="IC-7300")
    radio = IcomRadio("192.0.2.1", model=profile.model)
    radio._connected = True  # noqa: SLF001 -- inert transport boundary for this route test
    radio._civ_runtime._connected = True  # noqa: SLF001 -- same runtime ownership flag
    radio._civ_transport = object()  # type: ignore[assignment] # noqa: SLF001
    sent: list[bytes] = []

    async def send(civ_frame: bytes, **_: object) -> CivFrame:
        sent.append(civ_frame)
        return CivFrame(0xE0, profile.civ_addr, 0xFB)

    radio._send_civ_expect = send  # type: ignore[method-assign] # noqa: SLF001
    server = WebServer(radio)
    handler = ControlHandler(_Ws(), radio, "test", profile.model, server=server)
    await handler._enqueue_command("set_data_mode", {"mode": 0})  # noqa: SLF001
    entry = server.command_queue.drain_entries()
    assert len(entry) == 1

    poller = RadioPoller(
        radio, server.command_queue, state_store=server.command_state_store
    )
    try:
        await poller._execute(entry[0].command)  # noqa: SLF001
        assert sent == [bytes.fromhex("fefe94e01a060000fd")]
    finally:
        radio._connected = False  # noqa: SLF001
        radio._civ_runtime._connected = False  # noqa: SLF001
        radio._civ_transport = None  # noqa: SLF001


class _Ws:
    async def send_text(self, _: str) -> None:
        pass
