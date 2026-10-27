"""Native FTX-1 DATA selection through a real rigctld TCP connection."""

import asyncio
import sys

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
        stdout, stderr = await _communicate_and_reap(process, 5)
        assert process.returncode == 0, (stdout, stderr)
        assert stderr == b"", (stdout, stderr)
        assert "MD0C;" in peer.writes
        assert await radio.read_mode() == ("DATA-U", None)
        assert peer.modes == {0: "C", 1: "B"}


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("packet", "base", "target", "initial"),
    [
        ("PKTLSB", "1", "8", "2"),
        ("PKTLSB", "1", "8", "C"),
        ("PKTUSB", "2", "C", "1"),
        ("PKTUSB", "2", "C", "8"),
    ],
)
async def test_tcp_packet_waits_for_preceding_plain_mode(packet, base, target, initial):
    async with tcp_peer() as (radio, peer, reader, writer):
        peer.modes[0] = initial
        original_query = peer.query
        stale = 2

        async def settle(frame, **kwargs):
            nonlocal stale
            if frame == "MD0;" and f"MD0{base};" in peer.writes and stale:
                stale -= 1
                return f"MD0{initial}"
            return await original_query(frame, **kwargs)

        radio._transport.query = AsyncMock(side_effect=settle)
        assert (
            await _exchange(writer, reader, f"M VFOA {packet} 0\n".encode())
            == b"RPRT 0\n"
        )
        assert peer.modes == {0: target, 1: "B"}
        assert stale == 0


@pytest.mark.asyncio
async def test_native_data_toggle_serializes_with_sideband_setter():
    import asyncio

    radio, peer = make_radio()
    captured = asyncio.Event()
    release = asyncio.Event()
    original_query = peer.query

    async def pause_initial_read(frame, **kwargs):
        answer = await original_query(frame, **kwargs)
        if frame == "MD0;" and not captured.is_set():
            captured.set()
            await release.wait()
        return answer

    radio._transport.query = AsyncMock(side_effect=pause_initial_read)
    toggle = asyncio.create_task(radio.set_data_mode(True))
    try:
        await asyncio.wait_for(captured.wait(), 1)
        sideband = asyncio.create_task(radio.set_mode("LSB"))
        await asyncio.sleep(0)
    finally:
        release.set()
    await asyncio.wait_for(asyncio.gather(toggle, sideband), 2)
    assert peer.modes == {0: "1", 1: "B"}
    assert peer.writes == ["MD0C;", "MD01;"]


@pytest.mark.asyncio
@pytest.mark.parametrize("receiver", [-1, 2, True, "1", None])
@pytest.mark.parametrize("setter", ["set_mode", "set_data_mode"])
async def test_mode_setters_reject_invalid_receiver_before_cat(receiver, setter):
    radio, peer = make_radio()
    with pytest.raises((TypeError, ValueError)):
        await getattr(radio, setter)(
            "USB" if setter == "set_mode" else True, receiver=receiver
        )
    radio._transport.query.assert_not_awaited()
    radio._transport.write.assert_not_awaited()


async def _communicate_and_reap(process, timeout):
    try:
        return await asyncio.wait_for(process.communicate(), timeout)
    finally:
        if process.returncode is None:
            process.kill()
        await process.wait()


@pytest.mark.asyncio
@pytest.mark.parametrize("cancel", [False, True])
async def test_rigctl_process_cleanup_on_timeout_or_cancel(cancel):
    process = await asyncio.create_subprocess_exec(
        sys.executable,
        "-c",
        "import time; time.sleep(60)",
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    if cancel:
        task = asyncio.create_task(_communicate_and_reap(process, 5))
        await asyncio.sleep(0)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
    else:
        with pytest.raises(TimeoutError):
            await _communicate_and_reap(process, 0.01)
    assert process.returncode is not None


@pytest.mark.asyncio
@pytest.mark.parametrize("observed", ["LSB", "AM"])
async def test_validator_distinguishes_ignored_mode_from_wrong_readback(observed):
    from rigplane.core.exceptions import ModeReadbackError
    from rigplane.core.radio_protocol import Radio
    from rigplane.validation.hardware import _check_mode_set
    from rigplane.validation.schema import (
        CapabilityDeclaration,
        CapabilityDeclarationEntry,
        CheckStatus,
        FailureDomain,
        ValidationLevel,
    )

    radio = MagicMock(spec=Radio)
    radio.get_mode = AsyncMock(return_value=("LSB", None))
    radio.set_mode = AsyncMock(side_effect=[ModeReadbackError("USB", observed), None])
    entry = CapabilityDeclarationEntry(
        check_id="mode.set",
        capability="",
        level=ValidationLevel.CAPABILITY_MATRIX,
        declaration=CapabilityDeclaration.SUPPORTED,
        summary="mode readback",
    )
    check = await _check_mode_set(radio, entry, allow_writes=True, per_check_timeout=2)
    assert check.status is CheckStatus.FAIL
    assert check.failure_domain is FailureDomain.READBACK
    assert check.evidence["readback_mode"] == observed
    if observed == "LSB":
        assert check.evidence["outcome"] == "ignored"
        assert check.error == "control did not react: readback equals original"
    else:
        assert "outcome" not in check.evidence
        assert "AM" in check.error
    assert radio.set_mode.await_args_list[-1].args == ("LSB", None, 0)
