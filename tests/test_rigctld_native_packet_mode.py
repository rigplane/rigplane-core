"""Native backend DATA names are display aliases, never canonical mutations."""

import pytest

from rigplane.core.state_store import StateStore
from rigplane.rigctld.contract import RigctldConfig
from rigplane.rigctld.handler import (
    RigctldHandler,
    _mode_to_hamlib_str,
    _split_mode_values,
)
from test_rigctld_handler import _apply_store_value, get_cmd, make_mock_radio


@pytest.mark.asyncio
@pytest.mark.parametrize("native,packet", [("DATA-U", "PKTUSB"), ("DATA-L", "PKTLSB")])
async def test_native_packet_name_projection_preserves_canonical_truth(native, packet):
    radio = make_mock_radio()
    store = StateStore()
    radio.state_store = store
    handler = RigctldHandler(radio, RigctldConfig())
    for name, value in (("mode", native), ("filter_num", 2), ("data_mode", 0)):
        _apply_store_value(store, f"receiver.main.active.freq_mode.{name}", value)
    before = store.snapshot()
    response = await handler.execute(get_cmd("get_mode"))
    assert response.ok
    assert response.values == [packet, "2400"]
    assert store.snapshot().fields == before.fields
    assert store.snapshot().observation_seq == before.observation_seq
    assert _mode_to_hamlib_str(native) == packet
    assert _split_mode_values(native, 2, 0) == (packet, "2400")


@pytest.mark.asyncio
@pytest.mark.parametrize("receiver,packet", [(0, "PKTUSB"), (1, "PKTLSB")])
@pytest.mark.parametrize("installed_client", [False, True])
async def test_native_cat_packet_readback_over_tcp(receiver, packet, installed_client):
    from unittest.mock import AsyncMock, MagicMock

    from rigplane.backends.yaesu_cat.radio import YaesuCatRadio
    from rigplane.rigctld.server import RigctldServer
    from test_rigctld_installed_hamlib import _rigctl
    from test_rigctld_wsjtx_split_mk2 import _client, _exchange

    # Read-only fake CAT: real backend parses MD0C / MD18; no serial open.
    replies = {"ID;": "ID0840", "MD0;": "MD0C", "MD1;": "MD18", "TX;": "TX0"}
    radio = YaesuCatRadio("/dev/null", profile="ftx1", audio_driver=MagicMock())
    radio._transport._connected = True
    radio._transport.query = AsyncMock(
        side_effect=lambda frame, **kwargs: replies.get(frame, "")
    )
    radio._transport.write = AsyncMock()
    await radio.get_mode(receiver=receiver)
    server = RigctldServer(
        radio,
        RigctldConfig(host="127.0.0.1", port=0, cache_ttl=0),
        allow_not_ready=True,
    )
    await server.start()
    try:
        vfo = "VFOA" if receiver == 0 else "VFOB"
        if installed_client:
            stdout, stderr = await _rigctl(server, True, "m", vfo)
            assert stdout.splitlines()[0] == packet.encode(), (stdout, stderr)
        else:
            async with _client(server) as (reader, writer):
                response = await _exchange(
                    writer, reader, f"m {vfo}\n".encode(), lines=2
                )
                assert response.splitlines()[0] == packet.encode()
        radio._transport.write.assert_not_awaited()
    finally:
        await server.stop()
        radio._transport._connected = False
