"""IC-705 tone mode is read from the exclusive 0x16 0x5D selector."""

from __future__ import annotations

import pytest

from rigplane.commands import CONTROLLER_ADDR
from rigplane.core.state_pipeline_contracts import FieldPath
from rigplane.profiles import get_radio_profile
from rigplane.radio import IcomRadio
from rigplane.runtime._state_queries import (
    acquisition_query_resolver_for_profile,
    build_state_queries,
)
from rigplane.types import CivFrame
from test_radio import MockTransport

_IC705_ADDR = 0xA4


def _frame(data: bytes) -> CivFrame:
    return CivFrame(
        to_addr=CONTROLLER_ADDR,
        from_addr=_IC705_ADDR,
        command=0x16,
        sub=0x5D,
        data=data,
        receiver=0x00,
    )


def _radio() -> IcomRadio:
    radio = IcomRadio("192.168.1.100", model="IC-705")
    radio._civ_transport = MockTransport()
    radio._ctrl_transport = radio._civ_transport
    radio._connected = True
    return radio


def test_ic705_declares_no_repeater_shift() -> None:
    profile = get_radio_profile("IC-705")
    assert "repeater_shift" not in profile.capabilities
    assert not profile.command_map.has("get_repeater_shift")
    assert not profile.command_map.has("set_repeater_shift")


def test_ic705_tone_poll_uses_tone_squelch_type() -> None:
    profile = get_radio_profile("IC-705")
    resolve = acquisition_query_resolver_for_profile(profile)
    tone = FieldPath.receiver("main", "operator_toggles", "repeater_tone")
    tsql = FieldPath.receiver("main", "operator_toggles", "repeater_tsql")
    assert resolve(tone) == resolve(tsql)
    query = resolve(tone)
    assert query is not None
    assert (query.command, query.sub) == (0x16, 0x5D)
    sent = {
        (query.command, query.sub)
        for query in build_state_queries(profile)
        if (query.command, query.sub) in {(0x16, 0x42), (0x16, 0x43), (0x16, 0x5D)}
    }
    assert sent == {(0x16, 0x5D)}


@pytest.mark.parametrize(
    ("data", "tone", "tsql"),
    [
        (b"\x00", False, False),
        (b"\x01", True, False),
        (b"\x02", True, True),
        (b"\x03", None, None),
        (b"\x09", None, None),
    ],
)
def test_ic705_tone_squelch_type_derives_both_booleans(
    data: bytes,
    tone: bool | None,
    tsql: bool | None,
) -> None:
    radio = _radio()
    radio._civ_runtime._update_state_cache_from_frame(_frame(data))
    snapshot = radio._state_store.snapshot()
    assert snapshot.field("receiver.0.operator_toggles.repeater_tone").value is tone
    assert snapshot.field("receiver.0.operator_toggles.repeater_tsql").value is tsql
    radio._connected = False
