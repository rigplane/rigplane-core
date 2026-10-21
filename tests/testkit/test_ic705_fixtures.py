"""Structure and safety pins for the recorded IC-705 fixtures (MOR-3609).

``tests/fixtures/ic705/`` holds the bench IC-705's USB CI-V traffic, recorded
receive-side only (see its README).  These tests pin what a later conformance
replay may rely on: the files parse, every client frame is a well-formed CI-V
frame for the IC-705, and no recorded client frame is a transmit-side or
state-changing request except the four documented scope switches.
"""

from __future__ import annotations

import json
from pathlib import Path

from rigplane_testkit.redaction_gate import scan

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "ic705"
RADIO = 0xA4
CONTROLLER = 0xE0

# Request bodies the capture may contain besides the probe's GET list: reads
# the production client makes on its own, and the few writes it makes itself
# (VFO A select at start, PTT release and CW cancel at shutdown, scope output
# switches). Anything else, including any setter, fails the safety pin.
_CLIENT_READS = {
    bytes.fromhex(s)
    for s in (
        "1c00 1c01 2710 2711 271400 271500 271600 271700 271900 271a00 271d00 271e0101"
    ).split()
}
_CLIENT_WRITES = {
    bytes.fromhex(s) for s in ("0700 17ff 1c0000 271001 271101 271100").split()
}
_SCOPE_SWITCHES = {bytes.fromhex(s) for s in ("271001", "271101", "271100")}


def _rows() -> list[dict]:
    path = FIXTURES / "civ_pairs.jsonl"
    return [json.loads(line) for line in path.read_text().splitlines() if line]


def test_civ_pairs_are_wellformed_usb_frames() -> None:
    rows = _rows()
    assert {r["window"] for r in rows} == {"probe", "readonly_session", "scope"}
    for row in rows:
        assert row["transport"] == "usb"
        assert row["dir"] in ("c2r", "r2c")
        frame = bytes.fromhex(row["frame"])
        assert frame[:2] == b"\xfe\xfe" and frame[-1:] == b"\xfd"
        to, src = (RADIO, CONTROLLER) if row["dir"] == "c2r" else (CONTROLLER, RADIO)
        assert (frame[2], frame[3]) == (to, src)


def test_no_recorded_request_transmits_or_writes() -> None:
    answers = json.loads((FIXTURES / "probe_answers.json").read_text())["answers"]
    probe_gets = {bytes.fromhex(a["frame"])[4:-1] for a in answers}
    allowed = probe_gets | _CLIENT_READS | _CLIENT_WRITES
    for row in _rows():
        if row["dir"] == "c2r":
            body = bytes.fromhex(row["frame"])[4:-1]
            assert body in allowed, body.hex()


def test_scope_window_has_whole_sweeps_and_the_documented_switches() -> None:
    scope = [r for r in _rows() if r["window"] == "scope"]
    switches = {
        bytes.fromhex(r["frame"])[4:-1]
        for r in scope
        if r["dir"] == "c2r"
        and bytes.fromhex(r["frame"])[4:6] in (b"\x27\x10", b"\x27\x11")
        and len(bytes.fromhex(r["frame"])) == 8
    }
    assert switches == _SCOPE_SWITCHES
    waves = [bytes.fromhex(r["frame"]) for r in scope if r["dir"] == "r2c"]
    waves = [f for f in waves if f[4:6] == b"\x27\x00"]
    assert len(waves) == 5 * 11  # five complete sweeps of eleven segments


def test_probe_answers_summary_matches_rows() -> None:
    data = json.loads((FIXTURES / "probe_answers.json").read_text())
    classes: dict[str, int] = {}
    for item in data["answers"]:
        classes[item["class"]] = classes.get(item["class"], 0) + 1
    assert classes == {k: v for k, v in data["summary"].items() if v}


def test_scope_cadence_is_a_per_transport_baseline() -> None:
    data = json.loads((FIXTURES / "scope_cadence.json").read_text())
    assert data["transport"] == "usb_civ"
    assert 3.5 < data["web_scope_ws"]["fps"] < 4.8
    assert data["radio_wave"]["segments_per_sweep"] == 11


def test_redaction_gate_clean_without_a_deny_list() -> None:
    paths = [str(p) for p in sorted(FIXTURES.glob("*.json*"))]
    assert scan(paths) == []
