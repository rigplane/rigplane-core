"""Composite packet-mode commands must retain the caller's passband."""

import pytest

from rigplane.types import Mode
from test_rigctld_installed_hamlib import _rigctl
from test_rigctld_wsjtx_split_mk2 import _client, _exchange, _peer


@pytest.mark.asyncio
@pytest.mark.parametrize("model", ["IC-7300", "IC-7300MK2"])
@pytest.mark.parametrize("width,filter_num", [(2400, 2), (1800, 3)])
@pytest.mark.parametrize("installed_client", [False, True])
async def test_packet_mode_preserves_requested_filter(
    model, width, filter_num, installed_client
):
    async with _peer(model) as (_radio, peer, server, reader, writer):
        other_before = peer.slots["B"]
        for mode, data in (("PKTUSB", 1), ("USB", 0)):
            if installed_client:
                await _rigctl(server, False, "M", mode, str(width))
            else:
                assert (
                    await _exchange(writer, reader, f"M {mode} {width}\n".encode())
                    == b"RPRT 0\n"
                )
            assert peer.slots["A"][1:] == (Mode.USB, filter_num, data), peer.requests
            if installed_client:
                stdout, stderr = await _rigctl(server, False, "m")
                assert stdout.splitlines() == [mode.encode(), str(width).encode()], (
                    stdout,
                    stderr,
                )
            else:
                async with _client(server) as (fresh_reader, fresh_writer):
                    assert (
                        await _exchange(fresh_writer, fresh_reader, b"m\n", lines=2)
                        == f"{mode}\n{width}\n".encode()
                    )
            assert peer.slots["B"] == other_before
            assert peer.selected == "A"
            assert not peer.ptt_writes


@pytest.mark.parametrize("model", ["IC-7300", "IC-7300MK2"])
def test_explicit_data_filter_keeps_profile_domain_and_standalone_bytes(model):
    from rigplane.commands.bound import BoundCommands
    from rigplane.profiles import get_radio_profile

    profile = get_radio_profile(model)
    commands = BoundCommands(profile.command_map)
    assert (
        commands.set_data_mode(True, to_addr=profile.civ_addr)[-3:] == b"\x01\x01\xfd"
    )
    assert (
        commands.set_data_mode(True, to_addr=profile.civ_addr, filter_width=2)[-3:]
        == b"\x01\x02\xfd"
    )
    for invalid_data in (0, 2, 3):
        with pytest.raises(ValueError):
            commands.set_data_mode(
                invalid_data, to_addr=profile.civ_addr, filter_width=2
            )
    for invalid_filter in (0, 4, True):
        with pytest.raises(ValueError):
            commands.set_data_mode(
                True, to_addr=profile.civ_addr, filter_width=invalid_filter
            )


@pytest.mark.asyncio
async def test_plain_mode_ack_with_data_still_enabled_is_rejected(monkeypatch):
    from rigplane.commands import parse_civ_frame

    async with _peer("IC-7300") as (_radio, peer, server, reader, writer):
        original = peer.send_tracked

        async def retain_data(packet, *, is_current=None):
            frame = parse_civ_frame(packet[packet.index(b"\xfe\xfe") :])
            await original(packet, is_current=is_current)
            if frame.command == 0x1A and frame.sub == 0x06 and frame.data:
                freq, mode, filt, _ = peer.slots["A"]
                peer.slots["A"] = (freq, mode, filt, 1)

        monkeypatch.setattr(peer, "send_tracked", retain_data)
        assert await _exchange(writer, reader, b"M USB 2400\n") == b"RPRT -11\n"
        stdout, _ = await _rigctl(server, False, "m")
        assert stdout.splitlines() == [b"PKTUSB", b"2400"]
        assert peer.slots["A"][3] == 1
        assert not peer.ptt_writes
