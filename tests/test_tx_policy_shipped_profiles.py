"""Golden values for the measured [tx_policy] data shipped in rigs/*.toml.

MOR-1912, ADR row 3a: this pins the two profiles that carry measured
transmit-policy facts (ftx1, ic7300) and confirms every other shipped
profile still loads unchanged with the default (empty) policy — the row
must not disturb any rig that has not been bench-measured.
"""

from __future__ import annotations

from pathlib import Path

from rigplane.profiles import TxPolicy
from rigplane.profiles.rig_loader import discover_rigs

RIGS_DIR = Path(__file__).resolve().parent.parent / "rigs"


def test_ftx1_tx_policy_matches_the_bench_measurement():
    rigs = discover_rigs(RIGS_DIR)
    profile = rigs["FTX-1"].to_profile()

    assert profile.tx_policy == TxPolicy(
        refused_during_tx=frozenset({"mode", "vfo-topology"}),
        tx_state_map={"0": "rx", "1": "tx_cat", "2": "tx_other"},
    )


def test_ic7300_tx_policy_matches_the_bench_measurement():
    rigs = discover_rigs(RIGS_DIR)
    profile = rigs["IC-7300"].to_profile()

    assert profile.tx_policy == TxPolicy(
        refused_during_tx=frozenset(),
        tx_state_map={"0": "rx"},
    )


def test_every_other_shipped_profile_still_loads_with_default_policy():
    rigs = discover_rigs(RIGS_DIR)
    measured = {"FTX-1", "IC-7300"}

    assert len(rigs) > len(measured), "expected more than the two measured rigs"

    for model, rig in rigs.items():
        if model in measured:
            continue
        assert rig.to_profile().tx_policy == TxPolicy(), (
            f"{model}: unmeasured rig must keep the default empty tx_policy"
        )
