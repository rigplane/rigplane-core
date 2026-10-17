"""IC-9700 preamp domain: CI-V 0x16 0x02 combines the internal and external preamp.

IC-9700 CI-V Reference Guide p.5, command 16 02: 00 = both OFF, 01 = P.AMP ON,
02 = EXT-P.AMP ON, 03 = both ON.
"""

from __future__ import annotations

import pytest

from rigplane.profiles import resolve_radio_profile
from rigplane.radio import IcomRadio

from test_radio import MockTransport


@pytest.fixture
def mock_transport() -> MockTransport:
    return MockTransport()


@pytest.fixture
def radio(mock_transport: MockTransport):
    r = IcomRadio("192.168.1.103", timeout=0.05, model="IC-9700")
    r._civ_transport = mock_transport
    r._ctrl_transport = mock_transport
    r._connected = True
    yield r
    r._connected = False


def test_profile_declares_internal_and_external_preamp_combinations() -> None:
    profile = resolve_radio_profile(model="IC-9700")
    assert profile.pre_values == (0, 1, 2, 3)
    assert profile.pre_labels == {"0": "OFF", "1": "INT", "2": "EXT", "3": "INT+EXT"}


async def test_set_preamp_sends_both_on(
    radio: IcomRadio, mock_transport: MockTransport
) -> None:
    await radio.set_preamp(3)
    assert any(b"\x16\x02\x03\xfd" in packet for packet in mock_transport.sent_packets)
