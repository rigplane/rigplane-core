"""MOR-2915: the IC-705 acquires the fields its v3 controls show.

A field the profile does not declare is never read on a v3 session: not
at startup, not on a cadence and not after a write
(``core/acquisition_scheduler.py: AcquisitionScheduler._availability_for``
refuses it, and ``prime_unobserved`` reads only ``field_policies`` paths).
Each expected read below is a row of the IC-705 CI-V Reference Guide
(A7560-8EX-1, Jul. 2020), pages noted per group.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from rigplane.core.acquisition_scheduler import AcquisitionScheduler
from rigplane.core.state_pipeline_contracts import FieldPath
from rigplane.core.state_store import FreshnessClock
from rigplane.rig_loader import load_rig
from rigplane.runtime._state_queries import acquisition_query_resolver_for_profile

IC705_PATH = Path(__file__).resolve().parent.parent / "rigs" / "ic705.toml"

_p = FieldPath.parse

# Polled on a cadence, as rigs/ic7300.toml polls them (MOR-1452, MOR-2425).
_POLLED: dict[FieldPath, tuple[int, int | None, bytes]] = {
    # p.4: 14 0B MIC gain, 14 15 monitor, 14 16 VOX gain, 14 17 anti-VOX.
    _p("global.operator_controls.mic_gain"): (0x14, 0x0B, b""),
    _p("global.operator_controls.monitor_gain"): (0x14, 0x15, b""),
    _p("global.operator_controls.vox_gain"): (0x14, 0x16, b""),
    _p("global.operator_controls.anti_vox_gain"): (0x14, 0x17, b""),
    # p.5: 1A 03 IF filter width.
    _p("receiver.main.active.freq_mode.filter_width"): (
        0x1A,
        0x03,
        b"",
    ),
    # p.3: 14 06 NR level, 14 07/08 twin PBT; p.4: 14 12 NB level,
    # 14 0D notch, 16 57 notch width, 16 41 auto notch, 16 48 manual notch.
    _p("receiver.main.operator_controls.pbt_inner"): (0x14, 0x07, b""),
    _p("receiver.main.operator_controls.pbt_outer"): (0x14, 0x08, b""),
    _p("receiver.main.operator_controls.nr_level"): (0x14, 0x06, b""),
    _p("receiver.main.operator_controls.nb_level"): (0x14, 0x12, b""),
    _p("receiver.main.operator_controls.notch_filter"): (0x14, 0x0D, b""),
    _p("receiver.main.operator_controls.manual_notch_width"): (
        0x16,
        0x57,
        b"",
    ),
    _p("receiver.main.operator_toggles.auto_notch"): (0x16, 0x41, b""),
    _p("receiver.main.operator_toggles.manual_notch"): (0x16, 0x48, b""),
    # p.14: 21 00 RIT frequency.
    _p("global.operator_controls.rit_freq"): (0x21, 0x00, b""),
}

# Read on demand, as rigs/ic7300.toml reads them (MOR-1483, MOR-1491..1493,
# MOR-2234).
_ON_DEMAND: dict[FieldPath, tuple[int, int | None, bytes]] = {
    # p.4: 16 46 VOX, 16 45 monitor, 16 56 filter shape, 16 47 break-in,
    # 16 4F twin peak, 14 0F break-in delay, 14 0C key speed, 14 09 pitch.
    _p("global.tx_state.vox_on"): (0x16, 0x46, b""),
    _p("global.tx_state.monitor_on"): (0x16, 0x45, b""),
    _p("receiver.main.operator_controls.filter_shape"): (0x16, 0x56, b""),
    _p("global.operator_controls.break_in"): (0x16, 0x47, b""),
    _p("receiver.main.operator_toggles.twin_peak_filter"): (
        0x16,
        0x4F,
        b"",
    ),
    _p("global.operator_controls.break_in_delay"): (0x14, 0x0F, b""),
    _p("global.operator_controls.key_speed"): (0x14, 0x0C, b""),
    _p("global.operator_controls.cw_pitch"): (0x14, 0x09, b""),
    # p.14: 1A 05 0359 VOX delay.
    _p("global.operator_controls.vox_delay"): (0x1A, 0x05, b"\x03\x59"),
    # p.15: 26 00 selected VFO mode and filter; p.14: 1A 06 DATA mode.
    _p("receiver.main.active.freq_mode.filter_num"): (0x26, None, b"\x00"),
    _p("receiver.main.active.freq_mode.data_mode"): (
        0x1A,
        0x06,
        b"",
    ),
    # p.5: 1A 04 AGC time constant.
    _p("receiver.main.operator_controls.agc_time_constant"): (
        0x1A,
        0x04,
        b"",
    ),
    # p.14: 21 01 RIT, 21 02 dTX; 1B 00 tone, 1B 01 TSQL frequency.
    _p("global.tx_state.rit_on"): (0x21, 0x01, b""),
    _p("global.tx_state.rit_tx"): (0x21, 0x02, b""),
    _p("receiver.main.operator_controls.tone_freq"): (0x1B, 0x00, b""),
    _p("receiver.main.operator_controls.tsql_freq"): (0x1B, 0x01, b""),
}


@pytest.fixture(scope="module")
def profile():
    return load_rig(IC705_PATH).to_profile()


def _wire(query: Any) -> tuple[int, int | None, bytes]:
    return (query.command, query.sub, query.data)


@pytest.mark.parametrize("path", sorted(_POLLED, key=str), ids=str)
def test_polled_field_is_pollable_and_reads_the_guide_row(profile, path) -> None:
    acquisition = profile.state_acquisition
    assert path in acquisition.pollable_paths()
    query = acquisition_query_resolver_for_profile(profile)(path)
    assert query is not None
    assert _wire(query) == _POLLED[path]


@pytest.mark.parametrize("path", sorted(_ON_DEMAND, key=str), ids=str)
def test_on_demand_field_reads_the_guide_row(profile, path) -> None:
    capability = profile.state_acquisition.capability_for(path)
    assert capability.is_unavailable is False
    assert capability.command_response_observable is True
    query = acquisition_query_resolver_for_profile(profile)(path)
    assert query is not None
    assert _wire(query) == _ON_DEMAND[path]


def test_the_scheduler_primes_every_on_demand_field(profile) -> None:
    """``prime_unobserved`` caps each call and rotates through
    ``field_policies``, so ``len(field_policies)`` calls cover a sweep."""
    acquisition = profile.state_acquisition
    scheduler = AcquisitionScheduler(
        profile=acquisition, clock=FreshnessClock(start=400.0)
    )
    requested: set[FieldPath] = set()
    for _ in range(len(acquisition.field_policies)):
        for request in scheduler.prime_unobserved(observed_paths=()):
            requested.update(request.paths)
    assert set(_ON_DEMAND) <= requested
