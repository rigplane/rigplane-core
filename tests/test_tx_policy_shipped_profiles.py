"""Golden values for the [tx_policy] data shipped in rigs/*.toml.

MOR-1912 (ADR row 3a) landed the section for the two bench-measured rigs.
MOR-1947 settles what the remaining six declare, so each shipped rig carries
a written ruling instead of an absence.

Current consumption is narrow. Only the Yaesu backend reads ``tx_state_map`` today
(``yaesu_cat/radio.py: YaesuCatRadio._interpret_ptt_token``); the Icom read
decodes its ``1C 00`` reply natively into the canonical
``core.tx_observation.TxStateReading`` contract
(``runtime/radio.py: CoreRadio.read_transmit_state``). ``refused_during_tx``
currently remains profile metadata. This test pins values, not consumers.

The owner ruling, per radio:

* The four Icom siblings that the factory routes onto the *same* Icom
  transmit-state read primitive as the measured IC-7300 **inherit** its
  one-entry map. The justification is the shared CI-V decode, not a new
  bench measurement, and the profiles say so.
* The two rigs with no serial backend at all (X6100, TX-500) declare an
  explicitly **empty** map. They are reachable only through the rigctld
  client, whose read is permanently ``verified_readback=False``, so they
  are fail-closed by provenance regardless of what the map says.

Nothing here may be left implicit: an absent ``[tx_policy]`` section loads to
the same empty policy as a deliberately empty one, so
:func:`test_every_shipped_profile_declares_a_tx_policy_section` parses the raw
TOML and requires a ``tx_policy`` table to be present in the document.
"""

from __future__ import annotations

import tomllib
from pathlib import Path

from rigplane.backends.config import SerialBackendConfig
from rigplane.backends.factory import create_radio
from rigplane.profiles import TxPolicy
from rigplane.profiles.rig_loader import discover_rigs
from rigplane.runtime.radio import CoreRadio

RIGS_DIR = Path(__file__).resolve().parent.parent / "rigs"

# Measured on the live bench (MOR-1912). Everything else is decided here.
MEASURED = {"FTX-1", "IC-7300"}

# The Icom transmit-state byte: 0x00 means receiving, and nothing else goes
# in the map, because that wire carries no keying attribution (§3.7).
ICOM_CIV_MAP = {"0": "rx"}


def _routes_onto_the_shared_icom_read(model: str) -> bool:
    """True if the serial factory hands ``model`` the shared Icom primitive.

    Construction is deliberate rather than a hard-coded model list: the
    point of the inherit ruling is that these radios decode transmit state
    through the *same* code as the measured IC-7300, so the test asks the
    factory instead of restating the answer. ``create_radio`` opens nothing
    — it assembles the object — so this is safe with no hardware present.

    Method identity, not class kinship: a subclass that overrode
    ``read_transmit_state`` would no longer share the decode the inherit
    ruling rests on, and must stop being treated as an inheritor.
    """
    try:
        radio = create_radio(SerialBackendConfig(device="/dev/null", model=model))
    except ValueError:
        return False  # no serial backend for this model at all
    return type(radio).read_transmit_state is CoreRadio.read_transmit_state


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
        tx_state_map=dict(ICOM_CIV_MAP),
    )


def test_every_shipped_profile_declares_a_tx_policy_section():
    """No shipped rig may leave the transmit policy to an absent section.

    An absent section and an empty one load to the same :class:`TxPolicy`,
    so no assertion on the loaded profile can separate them. This pin parses
    the raw TOML document instead and requires a ``tx_policy`` *table* to be
    in it. That, and only that, is what it guarantees: a mention of the
    section in a comment or a TODO does not satisfy it. Whether the declared
    contents are right is the business of the per-rig pins below.
    """
    rigs = discover_rigs(RIGS_DIR)
    assert len(rigs) == 8, "shipped rig count changed; re-run the MOR-1947 ruling"

    undeclared = [
        path.name
        for path in sorted(RIGS_DIR.glob("*.toml"))
        if not path.name.startswith("_")
        and not isinstance(
            tomllib.loads(path.read_text(encoding="utf-8")).get("tx_policy"), dict
        )
    ]

    assert undeclared == [], (
        f"{undeclared}: every shipped rig must declare a [tx_policy] table "
        "(MOR-1947) — once loaded, an absent section is indistinguishable "
        "from a deliberately empty one"
    )


def test_icom_siblings_inherit_the_measured_civ_receiving_byte():
    """The unmeasured Icom siblings carry the IC-7300's one-entry map.

    Derived from factory routing rather than a literal model list: an Icom
    model added later that decodes transmit state through the same
    primitive is caught here if it ships without the inherited map.
    """
    rigs = discover_rigs(RIGS_DIR)

    inheritors = {
        model for model in rigs if _routes_onto_the_shared_icom_read(model)
    } - MEASURED

    assert inheritors == {"IC-705", "IC-7610", "IC-9700", "X6200"}

    for model in sorted(inheritors):
        assert rigs[model].to_profile().tx_policy == TxPolicy(
            refused_during_tx=frozenset(),
            tx_state_map=dict(ICOM_CIV_MAP),
        ), f"{model}: must inherit the shared-decode receiving byte (MOR-1947)"


def test_rigs_without_a_serial_backend_declare_an_empty_map():
    """X6100 and TX-500 stay fail-closed, and say so in the profile.

    Neither has a serial backend — the factory refuses both — so the only
    path to them is the rigctld client, whose transmit-state read is
    permanently unverified. Inheriting a receiving byte would claim a
    freshness that path can never deliver.
    """
    rigs = discover_rigs(RIGS_DIR)

    for model in ("X6100", "TX-500"):
        assert not _routes_onto_the_shared_icom_read(model), (
            f"{model} gained a serial backend; its tx_policy ruling must be redone"
        )
        assert rigs[model].to_profile().tx_policy == TxPolicy(), (
            f"{model}: must declare an explicitly empty tx_policy (MOR-1947)"
        )
