"""Split command errors and live relative reads through real rigctld TCP."""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from dataclasses import replace

import pytest

from rigplane.commands import parse_civ_frame
from rigplane.commands.command_map import CommandMap
from rigplane.commands.commander import IcomCommander
from rigplane.core.exceptions import CommandError
from rigplane.core.state_pipeline_contracts import (
    FieldPath,
    Observation,
    SourceMetadata,
)
from rigplane.profiles import get_radio_profile
from rigplane.types import Mode
from test_controller_authority import Safety
from test_rigctld_wsjtx_split_mk2 import (
    MODELS,
    _assert_no_relative_overlays,
    _client,
    _exchange,
    _peer,
    _rf_state,
)


@asynccontextmanager
async def _hold_commander_before_set(monkeypatch, radio, peer):
    commander = radio._commander = IcomCommander(radio._execute_civ_raw)
    commander.start()
    release = peer.relative_mode_read_gate = asyncio.Event()
    queued = asyncio.Event()
    blocker = asyncio.create_task(radio.send_civ(0x26, data=b"\x00"))
    send = commander.send

    async def observe_send(payload, **kwargs):
        frame = parse_civ_frame(payload)
        if frame.command in (0x25, 0x26) and len(frame.data) > 1:
            queued.set()
        return await send(payload, **kwargs)

    monkeypatch.setattr(commander, "send", observe_send)
    try:
        await asyncio.wait_for(peer.relative_mode_read.wait(), 1)
        yield queued, release
    finally:
        release.set()
        await asyncio.gather(blocker, return_exceptions=True)
        await commander.stop()
        radio._commander = None
        peer.relative_mode_read_gate = None


def _pause_mode_preservation(monkeypatch, radio):
    read_done, release = asyncio.Event(), asyncio.Event()
    read_relative_vfo = radio.read_relative_vfo

    async def paused_read(*, selected):
        observed = await read_relative_vfo(selected=selected)
        if not read_done.is_set():
            read_done.set()
            await release.wait()
        return observed

    monkeypatch.setattr(radio, "read_relative_vfo", paused_read)
    return read_done, release


@pytest.mark.asyncio
@pytest.mark.parametrize("model", MODELS)
@pytest.mark.parametrize("command", [b"X USB\n", b"X USB 0\n"])
async def test_split_selection_change_after_preservation_never_sets(
    monkeypatch, model, command
):
    async with _peer(model) as (radio, peer, server, reader, writer):
        before = dict(peer.slots)
        read_done, release = _pause_mode_preservation(monkeypatch, radio)
        reply = asyncio.create_task(_exchange(writer, reader, command))
        try:
            await asyncio.wait_for(read_done.wait(), 1)
            async with _client(server) as (other_reader, other_writer):
                assert (
                    await _exchange(other_writer, other_reader, b"V VFOB\n")
                    == b"RPRT 0\n"
                )
            assert peer.selected == "B"
            release.set()
            assert await reply == b"RPRT -9\n"
            assert not any(
                cmd == 0x26 and len(data) > 1 for cmd, _sub, data in peer.requests
            )
            assert peer.slots == before
            _assert_no_relative_overlays(server)
        finally:
            release.set()
            if not reply.done():
                reply.cancel()
            await asyncio.gather(reply, return_exceptions=True)


@pytest.mark.asyncio
@pytest.mark.parametrize("model", MODELS)
@pytest.mark.parametrize(
    ("command", "rejected"),
    [(b"I 14080100\n", "reject_frequency"), (b"X RTTY 1800\n", "reject_mode")],
)
async def test_split_wire_nak_returns_rejected_without_mutation(
    model, command, rejected
):
    async with _peer(model) as (_radio, peer, server, reader, writer):
        setattr(peer, rejected, True)
        before = dict(peer.slots)
        assert await _exchange(writer, reader, command) == b"RPRT -9\n"
        assert peer.slots == before
        assert peer.selected == "A"
        assert not any(cmd == 0x07 for cmd, _, _ in peer.requests)
        _assert_no_relative_overlays(server)


@pytest.mark.asyncio
@pytest.mark.parametrize("model", MODELS)
async def test_split_missing_mode_command_is_enavail_with_real_profile(model):
    profile = get_radio_profile(model)
    commands = profile.command_map
    assert commands is not None
    profile = replace(
        profile,
        command_map=CommandMap(
            {
                name: commands.get(name)
                for name in commands
                if name != "set_unselected_mode"
            }
        ),
    )
    async with _peer(model, profile=profile) as (_radio, peer, _server, reader, writer):
        before = dict(peer.slots)
        assert await _exchange(writer, reader, b"X USB 2400\n") == b"RPRT -11\n"
        assert peer.slots == before
        assert not peer.requests


@pytest.mark.asyncio
@pytest.mark.parametrize("model", MODELS)
@pytest.mark.parametrize("field", ["freq", "mode"])
@pytest.mark.parametrize("selected", [False, True])
async def test_relative_read_missing_role_command_is_unavailable(
    model, field, selected
):
    profile = get_radio_profile(model)
    commands = profile.command_map
    assert commands is not None
    role = "selected" if selected else "unselected"
    missing = f"get_{role}_{field}"
    profile = replace(
        profile,
        command_map=CommandMap(
            {name: commands.get(name) for name in commands if name != missing}
        ),
    )
    async with _peer(model, profile=profile) as (
        radio,
        peer,
        _server,
        _reader,
        _writer,
    ):
        with pytest.raises(CommandError, match="unavailable"):
            await radio.read_relative_vfo(selected=selected)
        assert not peer.requests
        observed = await radio.read_relative_vfo(selected=not selected)
        other = peer.slots["B" if selected else "A"]
        assert (
            observed.freq_hz,
            observed.mode,
            observed.filter_num,
            observed.data_mode,
        ) == (
            other[0],
            other[1].name,
            other[2],
            other[3],
        )


@pytest.mark.asyncio
@pytest.mark.parametrize("model", MODELS)
@pytest.mark.parametrize("missing", ["get_unselected_freq", "get_unselected_mode"])
@pytest.mark.parametrize("command", [b"i\n", b"x\n"])
async def test_split_missing_read_command_is_enavail(model, missing, command):
    profile = get_radio_profile(model)
    commands = profile.command_map
    assert commands is not None
    profile = replace(
        profile,
        command_map=CommandMap(
            {name: commands.get(name) for name in commands if name != missing}
        ),
    )
    async with _peer(model, profile=profile) as (_radio, peer, _server, reader, writer):
        before = dict(peer.slots)
        assert await _exchange(writer, reader, command) == b"RPRT -11\n"
        assert not peer.requests
        assert peer.slots == before


@pytest.mark.asyncio
@pytest.mark.parametrize("command", [b"I 14080100\n", b"X RTTY 1800\n", b"i\n", b"x\n"])
async def test_split_dual_receiver_returns_enimpl(command):
    async with _peer("IC-7610") as (_radio, peer, _server, reader, writer):
        assert await _exchange(writer, reader, command) == b"RPRT -4\n"
        assert not peer.requests


@pytest.mark.asyncio
@pytest.mark.parametrize("model", MODELS)
@pytest.mark.parametrize(
    ("write", "read", "expected"),
    [
        (b"I 14080100\n", b"i\n", b"14074000\n"),
        (b"X RTTY 1800\n", b"x\n", b"USB\n3000\n"),
    ],
)
async def test_split_live_read_follows_selection_after_write(
    model, write, read, expected
):
    async with _peer(model) as (_radio, peer, server, reader, writer):
        server._config.cache_ttl = 60
        for name, value in (
            ("freq_hz", 7_076_000),
            ("mode", "LSB"),
            ("filter_num", 3),
            ("data_mode", 1),
        ):
            server._state_store.apply_current(
                Observation(
                    path=FieldPath.unselected("main", "freq_mode", name),
                    value=value,
                    source=SourceMetadata(source="test", provider="tests"),
                    timestamp_monotonic=asyncio.get_running_loop().time(),
                    max_age=60,
                )
            )
        assert await _exchange(writer, reader, write) == b"RPRT 0\n"
        immediate = b"14080100\n" if read == b"i\n" else b"PKTRTTY\n1800\n"
        lines = 1 if read == b"i\n" else 2
        assert await _exchange(writer, reader, read, lines=lines) == immediate
        assert await _exchange(writer, reader, b"V VFOB\n") == b"RPRT 0\n"
        assert peer.selected == "B"
        assert await _exchange(writer, reader, read, lines=lines) == expected
        assert peer.slots["A"] == (14_074_000, Mode.USB, 1, 0)
        _assert_no_relative_overlays(server)


@pytest.mark.asyncio
@pytest.mark.parametrize("model", MODELS)
@pytest.mark.parametrize("command", [b"I 14080100\n", b"X RTTY 1800\n"])
async def test_split_relative_write_is_tx_safe_but_topology_is_deferred(model, command):
    async with _peer(model) as (_radio, peer, server, reader, writer):
        _rf_state(server, True)
        assert await _exchange(writer, reader, command) == b"RPRT 0\n"
        if command.startswith(b"I"):
            assert peer.slots["B"][0] == 14_080_100
        else:
            assert peer.slots["B"][1:] == (Mode.RTTY, 3, 1)
        before = list(peer.requests)
        assert await _exchange(writer, reader, b"S 1 VFOB\n") == b"RPRT 0\n"
        assert not peer.split
        assert peer.requests == before


@pytest.mark.asyncio
@pytest.mark.parametrize("model", MODELS)
@pytest.mark.parametrize("context", ["memory", "vfo"])
async def test_split_live_getters_preserve_memory_context(model, context):
    async with _peer(model, selected="B") as (_radio, peer, _server, reader, writer):
        peer.context = context
        before = dict(peer.slots)
        assert await _exchange(writer, reader, b"i\n") == b"14074000\n"
        assert await _exchange(writer, reader, b"x\n", lines=2) == b"USB\n3000\n"
        assert peer.context == context
        assert peer.slots == before
        assert peer.selected == "B"
        assert not any(cmd == 0x07 for cmd, _, _ in peer.requests)


@pytest.mark.asyncio
@pytest.mark.parametrize("model", MODELS)
@pytest.mark.parametrize(
    "command",
    [
        b"I inf\n",
        b"I -inf\n",
        b"I 1e999\n",
        b"I\n",
        b"\\set_split_freq\n",
        b"I VFOB\n",
        b"I 14080100 extra\n",
    ],
)
async def test_split_invalid_frequency_never_reaches_wire(model, command):
    async with _peer(model) as (_radio, peer, _server, reader, writer):
        assert await _exchange(writer, reader, command) == b"RPRT -1\n"
        assert not peer.requests


@pytest.mark.asyncio
@pytest.mark.parametrize("model", MODELS)
async def test_parser_distinguishes_bad_arity_from_unknown_command(model):
    async with _peer(model) as (_radio, peer, _server, reader, writer):
        for command in (b"F\n", b"f extra\n", b"X\n", b"X USB 2400 extra\n"):
            assert await _exchange(writer, reader, command) == b"RPRT -1\n"
        assert await _exchange(writer, reader, b"\\get_bogus\n") == b"RPRT -4\n"
        assert not peer.requests


@pytest.mark.asyncio
@pytest.mark.parametrize("model", MODELS)
@pytest.mark.parametrize("name,mode", [("CWR", Mode.CW_R), ("RTTYR", Mode.RTTY_R)])
@pytest.mark.parametrize("getter", [b"x\n", b"m\n"])
async def test_split_reverse_mode_round_trip_keeps_selected_mode(
    model, name, mode, getter
):
    async with _peer(model) as (_radio, peer, _server, reader, writer):
        before = peer.slots["A"]
        assert (
            await _exchange(writer, reader, f"X {name} 1800\n".encode()) == b"RPRT 0\n"
        )
        assert peer.slots["B"] == (7_076_000, mode, 3, 1)
        expected = f"{name}\n1800\n".encode() if getter == b"x\n" else b"USB\n3000\n"
        assert await _exchange(writer, reader, getter, lines=2) == expected
        assert peer.slots["A"] == before
        assert peer.selected == "A"


@pytest.mark.asyncio
@pytest.mark.parametrize("model", MODELS)
@pytest.mark.parametrize("command", [b"X USB\n", b"X USB 0\n"])
async def test_split_mode_authority_change_during_preservation_never_sets(
    model, command
):
    controller = Safety().authority()

    def configure(radio):
        radio._controller_authority = controller
        return {}

    async with _peer(model, configure=configure) as (
        _radio,
        peer,
        _server,
        reader,
        writer,
    ):
        before = dict(peer.slots)
        gate = peer.relative_mode_read_gate = asyncio.Event()
        reply = asyncio.create_task(_exchange(writer, reader, command))
        try:
            await asyncio.wait_for(peer.relative_mode_read.wait(), 1)
            await controller.set_mode("remote")
            gate.set()
            assert await reply == b"RPRT -22\n"
            assert not any(
                cmd == 0x26 and len(data) > 1 for cmd, _sub, data in peer.requests
            )
            assert peer.slots == before
            assert peer.selected == "A"
        finally:
            gate.set()
            if not reply.done():
                reply.cancel()
            await asyncio.gather(reply, return_exceptions=True)


@pytest.mark.asyncio
@pytest.mark.parametrize("model", MODELS)
@pytest.mark.parametrize("command", [b"I 14080100\n", b"X PKTUSB 2400\n"])
async def test_split_authority_change_while_commander_queued_never_sets(
    monkeypatch, model, command
):
    controller = Safety().authority()

    def configure(radio):
        radio._controller_authority = controller
        return {}

    async with _peer(model, configure=configure) as (
        radio,
        peer,
        server,
        reader,
        writer,
    ):
        before = dict(peer.slots)
        async with _hold_commander_before_set(monkeypatch, radio, peer) as (
            queued,
            release,
        ):
            reply = asyncio.create_task(_exchange(writer, reader, command))
            try:
                await asyncio.wait_for(queued.wait(), 1)
                assert radio._commander._queue.qsize() == 1
                await controller.set_mode("remote")
                release.set()
                assert await reply == b"RPRT -22\n"
                assert not any(
                    cmd in (0x25, 0x26) and len(data) > 1
                    for cmd, _sub, data in peer.requests
                )
                assert peer.slots == before
                assert peer.selected == "A"
                _assert_no_relative_overlays(server)
            finally:
                release.set()
                if not reply.done():
                    reply.cancel()
                await asyncio.gather(reply, return_exceptions=True)
