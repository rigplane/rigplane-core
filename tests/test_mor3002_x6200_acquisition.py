"""MOR-3002: the X6200 reads the controls its 2.x state sweep read.

MOR-1983 derived the state sweep from each profile's declarations, and a
field the profile does not declare is never read on a v3 session
(``core/acquisition_scheduler.py: AcquisitionScheduler._availability_for``;
``prime_unobserved`` reads only ``field_policies`` paths). The X6200
declared only frequency, mode, filter width and its two meters. Each read
below is a GET row of the Xiegu X6200 CI-V implementation V1.0.6, Table 1,
page noted per group.
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

X6200_PATH = Path(__file__).resolve().parent.parent / "rigs" / "x6200.toml"

_p = FieldPath.parse

# Polled on a cadence.
_POLLED: dict[FieldPath, tuple[int, int | None, bytes]] = {
    # p.5: 11 attenuator.
    _p("receiver.main.operator_controls.att"): (0x11, None, b""),
    # p.6: 14 01 AF, 14 02 RF gain, 14 03 squelch.
    _p("receiver.main.operator_controls.af_level"): (0x14, 0x01, b""),
    _p("receiver.main.operator_controls.rf_gain"): (0x14, 0x02, b""),
    _p("receiver.main.operator_controls.squelch"): (0x14, 0x03, b""),
    # p.7: 16 02 preamp, 16 12 AGC, 16 22 noise blanker.
    _p("receiver.main.operator_controls.preamp"): (0x16, 0x02, b""),
    _p("receiver.main.operator_controls.agc"): (0x16, 0x12, b""),
    _p("receiver.main.operator_toggles.nb"): (0x16, 0x22, b""),
    # p.6: 14 0A TX power, 14 0B mic gain, 14 15 monitor.
    _p("global.operator_controls.power_level"): (0x14, 0x0A, b""),
    _p("global.operator_controls.mic_gain"): (0x14, 0x0B, b""),
    _p("global.operator_controls.monitor_gain"): (0x14, 0x15, b""),
    # p.9: 1C 00 PTT, 1C 01 antenna tuner.
    _p("global.tx_state.ptt"): (0x1C, 0x00, b""),
    _p("global.operator_controls.tuner_status"): (0x1C, 0x01, b""),
}

# Read on demand, as rigs/ic705.toml reads them. p.6: 14 09 CW sidetone,
# 14 0C keyer speed.
_ON_DEMAND: dict[FieldPath, tuple[int, int | None, bytes]] = {
    _p("global.operator_controls.cw_pitch"): (0x14, 0x09, b""),
    _p("global.operator_controls.key_speed"): (0x14, 0x0C, b""),
}

# Not read:
# - NR on/off and COMP on/off: V1.0.6 documents only their sets (p.8);
# - NR and NB level: time out on the live radio (MOR-699);
# - RIT: write-only on the live radio (MOR-207);
# - split: V1.0.6 documents only the 0F sets (p.5).
_NOT_READ = (
    _p("receiver.main.operator_toggles.nr"),
    _p("global.tx_state.compressor_on"),
    _p("receiver.main.operator_controls.nr_level"),
    _p("receiver.main.operator_controls.nb_level"),
    _p("global.operator_controls.rit_freq"),
    _p("global.tx_state.split"),
)


@pytest.fixture(scope="module")
def profile():
    return load_rig(X6200_PATH).to_profile()


def _wire(query: Any) -> tuple[int, int | None, bytes]:
    return (query.command, query.sub, query.data)


@pytest.mark.parametrize("path", sorted(_POLLED, key=str), ids=str)
def test_polled_field_is_pollable_and_reads_the_table_row(profile, path) -> None:
    acquisition = profile.state_acquisition
    assert path in acquisition.pollable_paths()
    query = acquisition_query_resolver_for_profile(profile)(path)
    assert query is not None
    assert _wire(query) == _POLLED[path]


@pytest.mark.parametrize("path", sorted(_ON_DEMAND, key=str), ids=str)
def test_on_demand_field_reads_the_table_row(profile, path) -> None:
    acquisition = profile.state_acquisition
    capability = acquisition.capability_for(path)
    assert capability.is_unavailable is False
    assert capability.command_response_observable is True
    assert path not in acquisition.pollable_paths()
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


@pytest.mark.parametrize("path", _NOT_READ, ids=str)
def test_a_read_the_radio_is_not_known_to_answer_stays_unread(profile, path) -> None:
    acquisition = profile.state_acquisition
    assert path not in acquisition.pollable_paths()
    assert path not in acquisition.field_policies
