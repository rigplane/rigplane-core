"""FTX-1 SUB-receiver acquisition while the radio is in single receive.

Bench 2026-09-17 (MOR-2511, acceptance build 65c1fe05): with SUB selected, the
server's ``sub`` object carried no observed state -- ``freqHz: 0``,
``filterWidth: null``, meter 0, field status ``unavailable`` / ``undeclared``.

Bench 2026-09-18 (MOR-2511 comment, 01:22Z): in single receive (``FR01``)
each paired command probed answered SUB's own value with MAIN untouched --
mode, width, AF, RF gain, squelch and repeater shift among them.

The tests below drive the production observation adapter against the real
FTX-1 profile and a mock CAT transport with dual receive observed OFF -- the
bench condition.
"""
# mypy: disable-error-code=untyped-decorator

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from rigplane.core.state_pipeline_contracts import (
    FieldPath,
    Observation,
    SourceMetadata,
)
from rigplane.core.state_store import StateStore
from rigplane.rig_loader import load_rig

from rigplane.backends.yaesu_cat.observations import YaesuObservationAdapter
from rigplane.backends.yaesu_cat.radio import YaesuCatRadio

_RIGS_DIR = Path(__file__).parents[1] / "rigs"

_SUB_FREQ = FieldPath.active("sub", "freq_mode", "freq_hz")
_SUB_MODE = FieldPath.active("sub", "freq_mode", "mode")
_SUB_FILTER_WIDTH = FieldPath.active("sub", "freq_mode", "filter_width")
_SUB_S_METER = FieldPath.receiver("sub", "meters", "s_meter")
_SUB_AF = FieldPath.receiver("sub", "operator_controls", "af_level")
_SUB_RF = FieldPath.receiver("sub", "operator_controls", "rf_gain")
_SUB_SQL = FieldPath.receiver("sub", "operator_controls", "squelch")
_SUB_SHIFT = FieldPath.receiver("sub", "operator_controls", "repeater_shift")
_MAIN_AF = FieldPath.receiver("main", "operator_controls", "af_level")
_MAIN_RF = FieldPath.receiver("main", "operator_controls", "rf_gain")
_MAIN_SQL = FieldPath.receiver("main", "operator_controls", "squelch")
_MAIN_SHIFT = FieldPath.receiver("main", "operator_controls", "repeater_shift")

# SUB on 144.500 MHz USB, width table index 14 (2500 Hz in the profile's SSB
# table), raw meter 78 (the -36 dBm / S5 calibration point). ``SM1;`` answers
# with the side digit echoed 0 -- the firmware quirk from f18b397f that the
# profile's parse template absorbs.
# The mock transport answers WITHOUT the trailing ";" -- the radio's query
# path adds the frame terminator itself (same convention as
# tests/test_ftx1_radio.py).
_CAT_ANSWERS = {
    "FA;": "FA014043200",
    "FB;": "FB144500000",
    "MD0;": "MD02",
    "MD1;": "MD12",
    "SH0;": "SH0020",
    "SH1;": "SH1014",
    "SM0;": "SM0000",
    "SM1;": "SM0078",
    "FT;": "FT0",
    "TX;": "TX0",
    # Slow-tier answers. The AG/RG/SQ/OS MAIN/SUB pairs are the bench-probe
    # rows of 2026-09-18 (MOR-2511 comment, 01:22Z): SUB differs from MAIN on
    # every pair, so an assertion on the SUB path cannot pass on a MAIN echo.
    # The remaining entries are minimal parseable answers for the other reads
    # ``poll_slow_controls`` issues on this profile.
    "FR;": "FR01",
    "AG0;": "AG0000",
    "AG1;": "AG1020",
    "RG0;": "RG0255",
    "RG1;": "RG1225",
    "SQ0;": "SQ0000",
    "SQ1;": "SQ1025",
    "OS0;": "OS03",
    "OS1;": "OS11",
    "PA0;": "PA00",
    "GT0;": "GT06",
    "IS0;": "IS00+0000",
    "NA0;": "NA00",
    "NL0;": "NL0000",
    "RL0;": "RL000",
    "BC0;": "BC00",
    "BP00;": "BP00000",
    "CT0;": "CT00",
    "CN00;": "CN00008",
    "VS;": "VS0",
    "CS;": "CS0",
    "RM8;": "RM8080020",
    "RM7;": "RM7010000",
}


def _bench_radio() -> YaesuCatRadio:
    """A YaesuCatRadio on the existing mock-transport fake, answers above."""
    radio = YaesuCatRadio("/dev/null", profile=load_rig(_RIGS_DIR / "ftx1.toml"))
    radio._transport._connected = True
    radio._transport.query = AsyncMock(
        side_effect=lambda command: _CAT_ANSWERS[command]
    )
    return radio


def _single_receive_store() -> StateStore:
    """The bench condition: dual receive (``FR``) observed OFF."""
    store = StateStore()
    store.apply(
        Observation(
            path=FieldPath.global_("tx_state", "dual_watch"),
            value=False,
            source=SourceMetadata(source="yaesu_poll_response", provider="yaesu_cat"),
            timestamp_monotonic=0.0,
        )
    )
    return store


@pytest.mark.asyncio
async def test_sub_state_is_acquired_in_single_receive() -> None:
    radio = _bench_radio()
    store = _single_receive_store()
    radio._state_store = store

    adapter = YaesuObservationAdapter.from_radio(radio)
    for observation in await adapter.poll_medium():
        store.apply(observation)
    for observation in await adapter.poll_rx_meters():
        store.apply(observation)

    snapshot = store.snapshot()
    assert snapshot.field(_SUB_FREQ).value == 144_500_000
    assert snapshot.field(_SUB_MODE).value == "USB"
    assert snapshot.field(_SUB_FILTER_WIDTH).value == 2500
    assert snapshot.field(_SUB_S_METER).value == -36


@pytest.mark.asyncio
async def test_sub_operator_controls_are_acquired_in_single_receive() -> None:
    """The four controls MOR-2511 slice D ungated are read on the slow tier
    with dual receive OFF, each answering SUB's own value (bench 2026-09-18
    pairs in ``_CAT_ANSWERS``), not MAIN's."""

    radio = _bench_radio()
    store = _single_receive_store()
    radio._state_store = store

    adapter = YaesuObservationAdapter.from_radio(radio)
    for observation in await adapter.poll_slow_controls():
        store.apply(observation)

    snapshot = store.snapshot()
    assert snapshot.field(_SUB_AF).value == pytest.approx(20 / 255)
    assert snapshot.field(_MAIN_AF).value == pytest.approx(0 / 255)
    assert snapshot.field(_SUB_RF).value == pytest.approx(225 / 255)
    assert snapshot.field(_MAIN_RF).value == pytest.approx(255 / 255)
    assert snapshot.field(_SUB_SQL).value == pytest.approx(25 / 255)
    assert snapshot.field(_MAIN_SQL).value == pytest.approx(0 / 255)
    assert snapshot.field(_SUB_SHIFT).value == 1
    assert snapshot.field(_MAIN_SHIFT).value == 3
