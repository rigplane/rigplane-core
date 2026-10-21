"""FTX-1 model conformance: ``YaesuCatModel`` on the engine vs the recording.

The model is built from ``rigplane_testkit/sim/profiles/ftx1.toml`` plus
``rigs/ftx1.toml`` with ``tests/fixtures/ftx1/cat_pairs.jsonl`` mounted as
its recorded-answer table, seeded from the recording, and then every
recorded request must answer with the recorded bytes — or be listed in
``model_conformance_ledger.json`` (a two-way ratchet like
``test_ftx1_conformance.py``; a ledger entry needs a reason and a ticket).
"""

from __future__ import annotations

import json
import tomllib
from pathlib import Path

from rigplane.profiles.rig_loader import load_rig

from rigplane_testkit.sim.engine import ManualClock
from rigplane_testkit.sim.model.yaesu_cat import (
    NATIVE_READS,
    NATIVE_SET_VERBS,
    YaesuCatModel,
    build_ftx1_engine,
    seed_from_recording,
)
from rigplane_testkit.sim.profiles import load_sim_profile
from rigplane_testkit.yaesu.replay import RecordedCatAnswerTable

ROOT = Path(__file__).resolve().parents[2]
FIXTURES = ROOT / "tests" / "fixtures" / "ftx1"
LEDGER = FIXTURES / "model_conformance_ledger.json"
PROFILE = ROOT / "rigplane_testkit" / "sim" / "profiles" / "ftx1.toml"
ORACLE = ROOT / "tests" / "golden" / "wire_oracle" / "ftx1.toml"


def _table() -> RecordedCatAnswerTable:
    rows = [
        json.loads(line)
        for line in (FIXTURES / "cat_pairs.jsonl").read_text().splitlines()
    ]
    return RecordedCatAnswerTable.from_cat_pairs(rows)


def _build(recorded: RecordedCatAnswerTable | None) -> YaesuCatModel:
    rig = load_rig(ROOT / "rigs" / "ftx1.toml")
    profile = load_sim_profile(PROFILE, root=ROOT)
    engine = build_ftx1_engine(profile, rig, clock=ManualClock(), seed=0)
    return YaesuCatModel(
        engine, identity=rig.expected_identity_ids[0], recorded=recorded
    )


def _seeded_model(table: RecordedCatAnswerTable) -> YaesuCatModel:
    model = _build(table)
    seed_from_recording(model, table)
    return model


def test_model_diverges_from_the_recording_only_where_the_ledger_says() -> None:
    table = _table()
    model = _seeded_model(table)
    live: dict[str, dict[str, str | None]] = {}
    for request in table.requests():
        recorded = table.lookup(request)
        assert recorded is not None
        got = model.handle(request)
        if got != recorded.reply:
            live[request] = {"recorded": recorded.reply, "model": got}
    ledger = json.loads(LEDGER.read_text())
    new = {k: v for k, v in live.items() if k not in ledger}
    stale = sorted(k for k in ledger if k not in live)
    assert not new, (
        f"divergences missing from the ledger (fix the model or list them): {new}"
    )
    assert not stale, f"ledger entries that no longer diverge (delete them): {stale}"
    for request, entry in ledger.items():
        assert entry.get("ticket") and entry.get("why"), request
        assert {k: live[request][k] for k in ("recorded", "model")} == {
            k: entry[k] for k in ("recorded", "model")
        }, request


def test_every_native_read_is_recorded_as_data() -> None:
    table = _table()
    missing = [
        request
        for request in NATIVE_READS
        if (answer := table.lookup(request)) is None or answer.kind != "data"
    ]
    assert not missing, f"native reads without a recorded data reply: {missing}"


def test_handle_and_handle_with_outcome_agree_on_every_recorded_request() -> None:
    # One code path: handle() is handle_with_outcome()[0], so the reply
    # must be identical whichever entry point is used.
    table = _table()
    model = _seeded_model(table)
    for request in table.requests():
        assert model.handle(request) == model.handle_with_outcome(request)[0], request


def test_oracle_set_frames_of_served_verbs_are_accepted() -> None:
    # Unseeded model: the recorded MEMORY-mode state would refuse FA SETs.
    model = _build(None)
    rows = tomllib.loads(ORACLE.read_text())["row"]
    served = 0
    for row in rows:
        if row["direction"] != "set":
            continue
        frame = row["frame"]
        if frame[:2] not in NATIVE_SET_VERBS:
            continue  # recorded-only verb: skipped, never failed
        served += 1
        assert model.handle(frame) is None, frame
    assert served, "no oracle SET row landed on a served verb"


def test_sim_profile_loads_and_its_evidence_exists() -> None:
    profile = load_sim_profile(PROFILE, root=ROOT)
    assert profile.sim.id == "ftx1-usb"
    assert profile.sim.fidelity == "A"
    for entry in profile.sim.evidence:
        assert (ROOT / entry).is_file(), entry
