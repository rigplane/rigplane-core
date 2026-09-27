"""MOR-2803: FTX-1 filter_width is declared absent while NARROW is on.

Owner bench 2026-09-27 19:38 EDT (MAIN in USB, RX only): four
``set_filter_width`` writes sent while NARROW was on (3050/3700/1700/3250 Hz)
were accepted by the radio and read back (3000/3500/1650/3200 Hz, snapped to
Table 5) with no audible change -- the FTX-1 CAT manual (2508-C) sets the
passband through the per-mode ``NAR WIDTH`` menu while NARROW is on, so ``SH``
is not the passband then. ``rigs/ftx1.toml`` declares the width field absent
while that receiver's ``narrow`` reads true; these tests pin the declaration,
the resolution, and the adapter's withheld read.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from rigplane.backends.yaesu_cat.observations import YaesuObservationAdapter
from rigplane.core.acquisition_scheduler import resolve_available_when
from rigplane.core.state_acquisition_policy import AvailabilityClause
from rigplane.core.state_pipeline_contracts import (
    FieldPath,
    Observation,
    SourceMetadata,
)
from rigplane.core.state_store import StateStore
from rigplane.core.tx_observation import TxStateReading
from rigplane.profiles import get_radio_profile

_MAIN_WIDTH = FieldPath.active("main", "freq_mode", "filter_width")
_SUB_WIDTH = FieldPath.active("sub", "freq_mode", "filter_width")
_MAIN_NARROW = FieldPath.receiver("main", "operator_toggles", "narrow")
_SUB_NARROW = FieldPath.receiver("sub", "operator_toggles", "narrow")


def _acquisition():
    acquisition = get_radio_profile("FTX-1").state_acquisition
    assert acquisition is not None
    return acquisition


@pytest.mark.parametrize(
    ("width", "narrow"),
    [(_MAIN_WIDTH, _MAIN_NARROW), (_SUB_WIDTH, _SUB_NARROW)],
)
def test_filter_width_is_declared_absent_while_narrow_is_on(
    width: FieldPath, narrow: FieldPath
) -> None:
    """Each receiver's width policy carries exactly its own narrow clause."""

    assert _acquisition().policy_for(width).available_when == (
        AvailabilityClause(field=narrow, operator="equals", value=False),
    )


def _narrow_store(main_narrow: bool, sub_narrow: bool) -> StateStore:
    store = StateStore()
    store.begin_provider_generation()
    for path, value in ((_MAIN_NARROW, main_narrow), (_SUB_NARROW, sub_narrow)):
        store.apply_current(
            Observation(
                path=path,
                value=value,
                source=SourceMetadata(source="poll_response", provider="yaesu_cat"),
                timestamp_monotonic=1.0,
            )
        )
    return store


@pytest.mark.parametrize(
    ("main_narrow", "sub_narrow", "expected_main", "expected_sub"),
    [
        (False, False, True, True),
        (True, False, False, True),
        (False, True, True, False),
    ],
)
def test_filter_width_availability_follows_each_receivers_own_narrow(
    main_narrow: bool,
    sub_narrow: bool,
    expected_main: bool,
    expected_sub: bool,
) -> None:
    """MAIN and SUB resolve independently: one receiver's NARROW never
    withholds the other's width."""

    availability = resolve_available_when(
        _acquisition(), _narrow_store(main_narrow, sub_narrow).snapshot()
    )

    assert availability[_MAIN_WIDTH] is expected_main
    assert availability[_SUB_WIDTH] is expected_sub


def _radio() -> MagicMock:
    radio = MagicMock()
    radio.profile = get_radio_profile("FTX-1")
    radio.capabilities = {"dual_rx", "filter_width", "tx"}
    radio.read_freq = AsyncMock(
        side_effect=lambda receiver=0: 14_074_000 if receiver == 0 else 7_074_000
    )
    radio.read_mode = AsyncMock(
        side_effect=lambda receiver=0: ("USB", None) if receiver == 0 else ("LSB", None)
    )
    radio.get_tx_func = AsyncMock(return_value=0)
    radio.read_transmit_state = AsyncMock(
        return_value=TxStateReading(False, "rx", "yaesu_poll_response", True)
    )
    radio.read_filter_width = AsyncMock(return_value=500)
    return radio


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("main_narrow", "expected_receivers"),
    [
        # MAIN's narrow on: no `SH0;` goes out, SUB's own read still does.
        (True, [(1, "LSB")]),
        # MAIN's narrow off: both reads go out as before.
        (False, [(0, "USB"), (1, "LSB")]),
    ],
)
async def test_medium_poll_withholds_only_the_narrow_receivers_width_read(
    main_narrow: bool, expected_receivers: list[tuple[int, str]]
) -> None:
    """While MAIN's NARROW is on, `SH0;` is never sent (no flicker against
    StateFreshnessService's declared-absent discard); SUB's read is
    unaffected."""

    radio = _radio()
    radio._state_store = _narrow_store(main_narrow, sub_narrow=False)
    adapter = YaesuObservationAdapter(
        radio, profile=_acquisition(), clock=lambda: 123.456
    )

    observations = await adapter.poll_medium()

    assert [
        (await_args.args[0], await_args.kwargs["mode"])
        for await_args in radio.read_filter_width.await_args_list
    ] == expected_receivers
    widths = {
        str(item.path)
        for item in observations
        if item.path in (_MAIN_WIDTH, _SUB_WIDTH)
    }
    if main_narrow:
        assert widths == {str(_SUB_WIDTH)}
    else:
        assert widths == {str(_MAIN_WIDTH), str(_SUB_WIDTH)}


@pytest.mark.asyncio
async def test_medium_poll_withholds_the_sub_width_read_while_subs_narrow_is_on() -> (
    None
):
    """The SUB twin: only `SH1;` is withheld while SUB's own NARROW is on."""

    radio = _radio()
    radio._state_store = _narrow_store(main_narrow=False, sub_narrow=True)
    adapter = YaesuObservationAdapter(
        radio, profile=_acquisition(), clock=lambda: 123.456
    )

    observations = await adapter.poll_medium()

    assert [
        (await_args.args[0], await_args.kwargs["mode"])
        for await_args in radio.read_filter_width.await_args_list
    ] == [(0, "USB")]
    widths = {
        str(item.path)
        for item in observations
        if item.path in (_MAIN_WIDTH, _SUB_WIDTH)
    }
    assert widths == {str(_MAIN_WIDTH)}
