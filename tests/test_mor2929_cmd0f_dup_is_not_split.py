"""MOR-2929: a DUP−/DUP+ reply to ``0F`` is not a split reading.

The IC-705 CI-V Reference Guide (A7560-8EX-1, Jul. 2020, and A7560-8EX-6,
Jan. 2023), p.3, reads command ``0F`` as 00 = Split OFF, 01 = Split ON,
11 = DUP− and 12 = DUP+. The reply is one byte, so a DUP reading is not a
split reading (an inference from the table's shape). Both decoders below
used to read every non-zero byte as split ON.
"""

from __future__ import annotations

import pytest

from rigplane.commands import CONTROLLER_ADDR, build_civ_frame
from rigplane.radio import IcomRadio
from rigplane.types import CivFrame
from test_radio import MockTransport, _wrap_civ_in_udp

IC705_ADDR = 0xA4

_READBACKS = [
    pytest.param(0x00, False, id="00-split-off"),
    pytest.param(0x01, True, id="01-split-on"),
    pytest.param(0x11, False, id="11-dup-minus"),
    pytest.param(0x12, False, id="12-dup-plus"),
]


@pytest.fixture
def transport() -> MockTransport:
    return MockTransport()


@pytest.fixture
def radio(transport: MockTransport) -> IcomRadio:
    r = IcomRadio("192.168.1.100", timeout=0.05, model="IC-705")
    r._civ_transport = transport
    r._ctrl_transport = transport
    r._connected = True
    return r


@pytest.mark.parametrize(("byte", "split_on"), _READBACKS)
def test_split_observation_is_on_only_for_01(
    radio: IcomRadio, byte: int, split_on: bool
) -> None:
    """``runtime/_civ_rx.py``: the ``0F`` branch of the observation decoder,
    which feeds the SPLIT indicator and the TX-target rule."""
    frame = CivFrame(
        to_addr=CONTROLLER_ADDR,
        from_addr=IC705_ADDR,
        command=0x0F,
        sub=None,
        data=bytes([byte]),
    )
    radio._civ_runtime._update_state_cache_from_frame(frame)
    field = radio._state_store.snapshot().field("global.tx_state.split")
    assert field.value is split_on


@pytest.mark.asyncio
@pytest.mark.parametrize(("byte", "split_on"), _READBACKS)
async def test_get_split_is_on_only_for_01(
    radio: IcomRadio, transport: MockTransport, byte: int, split_on: bool
) -> None:
    """``runtime/radio.py: CoreRadio.get_split``."""
    civ = build_civ_frame(CONTROLLER_ADDR, IC705_ADDR, 0x0F, data=bytes([byte]))
    transport.queue_response(_wrap_civ_in_udp(civ))
    assert await radio.get_split() is split_on
