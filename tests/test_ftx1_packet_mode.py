"""Native FTX-1 DATA selection through a real rigctld TCP connection."""

from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, MagicMock

import pytest

from rigplane.backends.yaesu_cat.radio import YaesuCatRadio
from rigplane.backends.yaesu_cat.transport import CatCommandRejected
from rigplane.exceptions import CommandError
from rigplane.rigctld.contract import RigctldConfig
from rigplane.rigctld.server import RigctldServer
from test_rigctld_wsjtx_split_mk2 import _client, _exchange


class ModePeer:
    def __init__(self):
        self.modes = {0: "2", 1: "B"}
        self.writes = []
        self.reject = None

    async def write(self, frame, **kwargs):
        self.writes.append(frame)
        if frame == self.reject:
            raise CatCommandRejected("?;")
        assert frame.startswith(("MD0", "MD1")), frame
        self.modes[int(frame[2])] = frame[3]

    async def query(self, frame, **kwargs):
        if frame == "ID;":
            return "ID0840"
        if frame in ("MD0;", "MD1;"):
            return frame[:3] + self.modes[int(frame[2])]
        if frame in ("FA;", "FB;"):
            return frame[:2] + "014074000"
        if frame == "TX;":
            return "TX0"
        return ""


def make_radio():
    radio = YaesuCatRadio("/dev/null", profile="ftx1", audio_driver=MagicMock())
    radio._transport._connected = True
    peer = ModePeer()
    radio._transport.write = AsyncMock(side_effect=peer.write)
    radio._transport.query = AsyncMock(side_effect=peer.query)
    return radio, peer


@asynccontextmanager
async def tcp_peer():
    radio, peer = make_radio()
    config = RigctldConfig(host="127.0.0.1", port=0, cache_ttl=0, command_timeout=2)
    server = RigctldServer(radio, config, allow_not_ready=True)
    await server.start()
    try:
        async with _client(server) as (reader, writer):
            yield radio, peer, reader, writer
    finally:
        await server.stop()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("packet", "code", "plain", "plain_code"),
    [
        ("PKTUSB", "C", "USB", "2"),
        ("PKTLSB", "8", "LSB", "1"),
    ],
)
async def test_tcp_packet_mode_changes_cat_and_plain_clears(
    packet, code, plain, plain_code
):
    async with tcp_peer() as (radio, peer, reader, writer):
        assert (
            await _exchange(writer, reader, f"M VFOA {packet} 0\n".encode())
            == b"RPRT 0\n"
        )
        assert await peer.query("MD0;") == f"MD0{code}"
        assert await radio.read_mode() == ({"C": "DATA-U", "8": "DATA-L"}[code], None)
        assert await radio.get_data_mode() is True
        assert peer.modes[1] == "B"
        assert (
            await _exchange(writer, reader, f"M VFOA {plain} 0\n".encode())
            == b"RPRT 0\n"
        )
        assert await peer.query("MD0;") == f"MD0{plain_code}"
        assert await radio.get_data_mode() is False
        assert peer.modes[1] == "B"


@pytest.mark.asyncio
async def test_tcp_rejected_packet_cat_returns_negative_ack_without_mode_change():
    async with tcp_peer() as (radio, peer, reader, writer):
        peer.reject = "MD0C;"
        reply = await _exchange(writer, reader, b"M VFOA PKTUSB 0\n")
        assert reply.startswith(b"RPRT -"), reply
        assert "MD0C;" in peer.writes
        assert await peer.query("MD0;") == "MD02"
        assert await radio.read_mode() == ("USB", None)
        assert peer.modes[1] == "B"


@pytest.mark.asyncio
@pytest.mark.parametrize("receiver", [0, 1])
@pytest.mark.parametrize(
    ("plain", "packet"), [("2", "C"), ("1", "8"), ("4", "A"), ("B", "F")]
)
async def test_native_data_toggle_roundtrip_and_idempotence(receiver, plain, packet):
    radio, peer = make_radio()
    peer.modes[receiver] = plain
    await radio.set_data_mode(True, receiver=receiver)
    assert peer.modes[receiver] == packet
    count = len(peer.writes)
    await radio.set_data_mode(True, receiver=receiver)
    assert len(peer.writes) == count
    await radio.set_data_mode(False, receiver=receiver)
    assert peer.modes[receiver] == plain


@pytest.mark.asyncio
async def test_unpaired_mode_refuses_data_without_write():
    radio, peer = make_radio()
    peer.modes[0] = "3"
    with pytest.raises(CommandError):
        await radio.set_data_mode(True)
    assert peer.writes == []
    assert peer.modes[0] == "3"


@pytest.mark.asyncio
async def test_tcp_ignored_packet_write_returns_negative_ack_without_mode_change():
    async with tcp_peer() as (radio, peer, reader, writer):
        original_write = peer.write

        async def ignore_packet(frame, **kwargs):
            if frame == "MD0C;":
                peer.writes.append(frame)
                return
            await original_write(frame, **kwargs)

        radio._transport.write = AsyncMock(side_effect=ignore_packet)
        reply = await _exchange(writer, reader, b"M VFOA PKTUSB 0\n")
        assert reply.startswith(b"RPRT -"), reply
        assert "MD0C;" in peer.writes
        assert await peer.query("MD0;") == "MD02"
        assert await radio.read_mode() == ("USB", None)
        assert peer.modes[1] == "B"


@pytest.mark.asyncio
async def test_native_data_write_waits_for_settled_readback():
    radio, peer = make_radio()
    original_query = peer.query
    stale_answers = 2

    async def settle(frame, **kwargs):
        nonlocal stale_answers
        if frame == "MD0;" and "MD0C;" in peer.writes and stale_answers:
            stale_answers -= 1
            return "MD02"
        return await original_query(frame, **kwargs)

    radio._transport.query = AsyncMock(side_effect=settle)
    await radio.set_data_mode(True)
    assert stale_answers == 0
    assert peer.modes == {0: "C", 1: "B"}
    assert radio.radio_state.main.mode == "DATA-U"
    assert peer.writes == ["MD0C;"]


@pytest.mark.asyncio
async def test_native_data_off_already_plain_sends_no_write():
    radio, peer = make_radio()
    await radio.set_data_mode(False)
    assert peer.writes == []
    assert peer.modes == {0: "2", 1: "B"}


@pytest.mark.asyncio
async def test_installed_rigctl_packet_mode_reaches_native_cat():
    import asyncio
    import shutil

    binary = shutil.which("rigctl")
    if binary is None:
        pytest.skip("installed Hamlib rigctl is unavailable")
    async with tcp_peer() as (radio, peer, reader, writer):
        address = writer.get_extra_info("peername")
        process = await asyncio.create_subprocess_exec(
            binary,
            "-m",
            "2",
            "-r",
            f"{address[0]}:{address[1]}",
            "-P",
            "NONE",
            "-o",
            "M",
            "VFOA",
            "PKTUSB",
            "0",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await asyncio.wait_for(process.communicate(), 5)
        assert process.returncode == 0, (stdout, stderr)
        assert stderr == b"", (stdout, stderr)
        assert "MD0C;" in peer.writes
        assert await radio.read_mode() == ("DATA-U", None)
        assert peer.modes == {0: "C", 1: "B"}
