"""MOR-2975: a ``1A 05`` menu reply is decoded through the profile's reverse
index, not through a per-model list of control numbers in code.

Before this change ``runtime/_civ_rx.py`` decoded a VOX delay reply only when
its control number was IC-7610's ``02 92`` or IC-7300's ``01 91``, whatever
radio sent it. The IC-705 declares ``get_vox_delay`` at ``03 59`` (CI-V guide
p.14) and the IC-9700 at ``03 30``, so their replies published nothing, while
the IC-705's own ``01 91`` (a scope FIX Edges setting, guide p.10) would have
been read as a VOX delay.
"""

from __future__ import annotations

import pytest

from rigplane.commands import CONTROLLER_ADDR
from rigplane.radio import IcomRadio
from rigplane.types import CivFrame
from test_radio import MockTransport

VOX_DELAY = "global.operator_controls.vox_delay"


def _radio(model: str) -> IcomRadio:
    radio = IcomRadio("192.168.1.100", model=model)
    radio._civ_transport = MockTransport()
    radio._ctrl_transport = radio._civ_transport
    radio._connected = True
    return radio


def _reply(addr: int, control: bytes, value: bytes) -> CivFrame:
    return CivFrame(
        to_addr=CONTROLLER_ADDR,
        from_addr=addr,
        command=0x1A,
        sub=0x05,
        data=control + value,
    )


@pytest.mark.parametrize(
    ("model", "addr", "control"),
    [
        pytest.param("IC-705", 0xA4, b"\x03\x59", id="ic705-0359"),
        pytest.param("IC-9700", 0xA2, b"\x03\x30", id="ic9700-0330"),
        pytest.param("IC-7300", 0x94, b"\x01\x91", id="ic7300-0191"),
        pytest.param("IC-7610", 0x98, b"\x02\x92", id="ic7610-0292"),
    ],
)
def test_vox_delay_reply_at_the_profiles_control_number(model, addr, control) -> None:
    radio = _radio(model)
    radio._civ_runtime._update_state_cache_from_frame(_reply(addr, control, b"\x15"))
    assert radio._state_store.snapshot().field(VOX_DELAY).value == 15
    radio._connected = False


def test_another_radios_control_number_is_not_a_vox_delay() -> None:
    """``01 91`` is the IC-7300's VOX delay, not the IC-705's."""
    radio = _radio("IC-705")
    radio._civ_runtime._update_state_cache_from_frame(
        _reply(0xA4, b"\x01\x91", b"\x15")
    )
    with pytest.raises(KeyError):
        radio._state_store.snapshot().field(VOX_DELAY)
    radio._connected = False
