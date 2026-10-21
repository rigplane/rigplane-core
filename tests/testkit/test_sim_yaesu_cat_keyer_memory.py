"""FTX-1 keyer memory in the Yaesu CAT model (MOR-3678).

Source: Yaesu FTX-1 CAT Operation Reference Manual, FTX-1_CAT_OM_ENG_2508-C.
- Command list (printed p.5, PDF p.6): KM KEYER MEMORY Set O / Read O /
  Answer O; KY CW KEYING MEMORY Set O / Read X / Answer X.
- KM KEYING MEMORY (printed p.17, PDF p.18): Set ``KM P1 P2 ... P2;``,
  Read ``KM P1;``, Answer ``KM P1 P2 ... P2;``; P1 ``1 - 5`` Keyer Memory
  Channel Number, P2 Message Characters (up to 50 characters).
- KY CW KEYING MEMORY PLAY (printed p.18, PDF p.19): Set ``KY P1 P2;``;
  P1 0 CW TEXT Memory, 1 CW MESSAGE Memory; P2 0 STOP, 1-5 Playback of
  memory 1-5. The Read and Answer rows are empty.

Where the manual is silent the model stays conservative (see the module
docstring): only the model's existing well-formed body charset reaches
KM, an unwritten channel is a coverage gap rather than an invented
answer, and KY records the request without linking it to KM's text.
Pure: no sockets, no radio, a ManualClock.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from rigplane.profiles.rig_loader import load_rig

from rigplane_testkit.sim.engine import ManualClock, Ok, Refused, Unsupported
from rigplane_testkit.sim.model.yaesu_cat import (
    NATIVE_SET_VERBS,
    YaesuCatModel,
    build_ftx1_engine,
)
from rigplane_testkit.sim.profiles import load_sim_profile

ROOT = Path(__file__).resolve().parents[2]
PROFILE_PATH = ROOT / "rigplane_testkit" / "sim" / "profiles" / "ftx1.toml"


def _model() -> YaesuCatModel:
    rig = load_rig(ROOT / "rigs" / "ftx1.toml")
    profile = load_sim_profile(PROFILE_PATH, root=ROOT)
    engine = build_ftx1_engine(profile, rig, clock=ManualClock(), seed=0)
    return YaesuCatModel(engine, identity=rig.expected_identity_ids[0])


# ── KM KEYING MEMORY (printed p.17) ──────────────────────────────


@pytest.mark.parametrize("channel", ["1", "2", "3", "4", "5"])
def test_km_write_then_read_round_trip(channel: str) -> None:
    model = _model()
    assert model.handle_with_outcome(f"KM{channel}CQTEST;") == (None, Ok())
    assert model.handle_with_outcome(f"KM{channel};") == (
        f"KM{channel}CQTEST;",
        Ok(),
    )
    assert model.keying_memory == {int(channel): "CQTEST"}


def test_km_accepts_exactly_fifty_characters() -> None:
    model = _model()
    text = "A" * 50
    assert model.handle(f"KM1{text};") is None
    assert model.handle("KM1;") == f"KM1{text};"


def test_km_refuses_more_than_fifty_characters_and_keeps_the_old_text() -> None:
    model = _model()
    assert model.handle("KM2OLD;") is None
    assert model.handle_with_outcome(f"KM2{'B' * 51};") == (
        "?;",
        Refused("native_refusal"),
    )
    assert model.handle("KM2;") == "KM2OLD;"


@pytest.mark.parametrize("line", ["KM0CQ;", "KM6CQ;", "KM9CQ;", "KM0;", "KM6;", "KMA;"])
def test_km_refuses_a_channel_outside_one_to_five(line: str) -> None:
    model = _model()
    assert model.handle_with_outcome(line) == ("?;", Refused("native_refusal"))
    assert model.keying_memory == {}


def test_km_without_a_channel_is_refused() -> None:
    assert _model().handle_with_outcome("KM;") == ("?;", Refused("native_refusal"))


def test_km_read_of_an_unwritten_channel_is_a_coverage_gap() -> None:
    # The manual gives no answer for a channel the sim never saw written;
    # the model does not invent one (empty, spaces or a default text).
    model = _model()
    assert model.handle_with_outcome("KM3;") == ("?;", Unsupported("not_modelled"))
    assert model.not_modelled == {"KM3;": 1}


def test_km_text_outside_the_wire_charset_is_malformed() -> None:
    # The manual does not list the KM character set; a space (or CW
    # punctuation such as '/', '?', '.') stays outside the model's
    # well-formed body shape until a source pins it.
    model = _model()
    for line in ("KM1CQ TEST;", "KM1DE/P;", "KM1QRL?;"):
        assert model.handle_with_outcome(line) == ("?;", Refused("malformed"))
    assert model.keying_memory == {}


# ── KY CW KEYING MEMORY PLAY (printed p.18) ──────────────────────


@pytest.mark.parametrize("p1", ["0", "1"])
@pytest.mark.parametrize("p2", ["1", "2", "3", "4", "5"])
def test_ky_play_is_accepted_and_recorded(p1: str, p2: str) -> None:
    model = _model()
    assert model.handle_with_outcome(f"KY{p1}{p2};") == (None, Ok())
    assert model.keyer_play_requests == ((int(p1), int(p2)),)


def test_ky_stop_is_accepted_and_recorded_in_order() -> None:
    model = _model()
    assert model.handle("KY03;") is None
    assert model.handle("KY00;") is None
    assert model.keyer_play_requests == ((0, 3), (0, 0))


def test_ky_read_is_refused() -> None:
    # Command list: KY Read X, Answer X.
    model = _model()
    assert model.handle_with_outcome("KY;") == ("?;", Refused("native_refusal"))
    assert model.keyer_play_requests == ()


@pytest.mark.parametrize("line", ["KY2;", "KY21;", "KY06;", "KY09;", "KY0;", "KY001;"])
def test_ky_refuses_out_of_table_parameters(line: str) -> None:
    model = _model()
    assert model.handle_with_outcome(line) == ("?;", Refused("native_refusal"))
    assert model.keyer_play_requests == ()


def test_ky_does_not_key_the_engine_transmitter() -> None:
    # Playback keying is not modelled: the engine PTT stays off.
    model = _model()
    assert model.handle("KM1CQ;") is None
    assert model.handle("KY01;") is None
    assert model.handle("TX;") == "TX0;"


def test_km_and_ky_are_natively_served_set_verbs() -> None:
    assert {"KM", "KY"} <= NATIVE_SET_VERBS
