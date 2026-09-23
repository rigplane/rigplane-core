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
_SUB_NB_LEVEL = FieldPath.receiver("sub", "operator_controls", "nb_level")
_SUB_NB = FieldPath.receiver("sub", "operator_toggles", "nb")
_SUB_NR_LEVEL = FieldPath.receiver("sub", "operator_controls", "nr_level")
_SUB_NR = FieldPath.receiver("sub", "operator_toggles", "nr")
_SUB_AUTO_NOTCH = FieldPath.receiver("sub", "operator_toggles", "auto_notch")
_SUB_MANUAL_NOTCH = FieldPath.receiver("sub", "operator_toggles", "manual_notch")
_SUB_MANUAL_NOTCH_FREQ = FieldPath.receiver(
    "sub", "operator_controls", "manual_notch_freq"
)
_SUB_IF_SHIFT = FieldPath.receiver("sub", "operator_controls", "if_shift")
_SUB_NARROW = FieldPath.receiver("sub", "operator_toggles", "narrow")
_SUB_AGC = FieldPath.receiver("sub", "operator_controls", "agc")
_MAIN_NB_LEVEL = FieldPath.receiver("main", "operator_controls", "nb_level")
_MAIN_IF_SHIFT = FieldPath.receiver("main", "operator_controls", "if_shift")
_MAIN_NARROW = FieldPath.receiver("main", "operator_toggles", "narrow")
_MAIN_AGC = FieldPath.receiver("main", "operator_controls", "agc")

# SUB on 144.500 MHz USB, Table 5 width code 14 (2450 Hz — 2508-C codes
# count from 01), raw meter 78 (the -36 dBm / S5 calibration point).
# ``SM1;`` answers
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
    # Slow-tier answers. The AG/RG/SQ/OS/GT MAIN/SUB pairs are the
    # bench-probe rows of 2026-09-18 (MOR-2511 comment, 01:22Z): SUB differs
    # from MAIN on every pair, so an assertion on the SUB path cannot pass on
    # a MAIN echo. The remaining entries are minimal parseable answers for the
    # other reads ``poll_slow_controls`` issues on this profile.
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
    "GT1;": "GT11",
    "IS0;": "IS00+0000",
    "IS1;": "IS10+0200",
    "NA0;": "NA00",
    "NA1;": "NA11",
    "NL0;": "NL0000",
    "NL1;": "NL1003",
    "RL0;": "RL000",
    "RL1;": "RL105",
    "BC0;": "BC00",
    "BC1;": "BC11",
    "BP00;": "BP00000",
    "BP10;": "BP10001",
    "BP11;": "BP11120",
    "CT0;": "CT00",
    "CN00;": "CN00008",
    # Tone SUB pairs (MOR-2111): SUB answers its own state — CT1 reports
    # TSQL (2) where MAIN CT0 reports OFF, CN10 reports tone index 15
    # (110.9 Hz) where MAIN CN00 reports index 8 (88.5 Hz) — so an assertion
    # on a SUB tone path cannot pass on a MAIN echo.
    "CT1;": "CT12",
    "CN10;": "CN10015",
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
    """The bench condition: dual receive (``FR``) observed OFF and SUB in USB
    (the probe's mode), so the SUB notch-freq clause resolves permissively."""
    store = StateStore()
    store.apply(
        Observation(
            path=FieldPath.global_("tx_state", "dual_watch"),
            value=False,
            source=SourceMetadata(source="yaesu_poll_response", provider="yaesu_cat"),
            timestamp_monotonic=0.0,
        )
    )
    store.apply(
        Observation(
            path=FieldPath.active("sub", "freq_mode", "mode"),
            value="USB",
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
    assert snapshot.field(_SUB_FILTER_WIDTH).value == 2450
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


@pytest.mark.asyncio
async def test_sub_dsp_controls_are_acquired_in_single_receive() -> None:
    """The nine DSP paths slice C declares, plus the SUB AGC twin (slice B),
    are read on the slow tier with dual receive OFF, each answering SUB's
    own value (bench 2026-09-18 pairs in ``_CAT_ANSWERS``), not MAIN's."""

    radio = _bench_radio()
    store = _single_receive_store()
    radio._state_store = store

    adapter = YaesuObservationAdapter.from_radio(radio)
    for observation in await adapter.poll_slow_controls():
        store.apply(observation)

    snapshot = store.snapshot()
    assert snapshot.field(_SUB_NB_LEVEL).value == 3
    assert snapshot.field(_SUB_NB).value is True
    assert snapshot.field(_SUB_NR_LEVEL).value == 5
    assert snapshot.field(_SUB_NR).value is True
    assert snapshot.field(_SUB_AUTO_NOTCH).value is True
    assert snapshot.field(_SUB_MANUAL_NOTCH).value is True
    assert snapshot.field(_SUB_MANUAL_NOTCH_FREQ).value == 120
    assert snapshot.field(_SUB_IF_SHIFT).value == 200
    assert snapshot.field(_SUB_NARROW).value is True
    assert snapshot.field(_SUB_AGC).value == 1
    # MAIN contrasts from the same probe rows: a MAIN echo cannot pass.
    assert snapshot.field(_MAIN_NB_LEVEL).value == 0
    assert snapshot.field(_MAIN_IF_SHIFT).value == 0
    assert snapshot.field(_MAIN_NARROW).value is False
    assert snapshot.field(_MAIN_AGC).value == 6
