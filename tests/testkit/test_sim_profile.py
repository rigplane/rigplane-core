"""Tests for the ``rigplane-sim-profile/1`` loader (MOR-3600).

A case is "<exact message prefix>|<TOML body>"; a body without ``[sim]``
gets BASE prepended, a body starting with ``!`` is a whole document.
"""

from dataclasses import FrozenInstanceError
from pathlib import Path

import pytest

from rigplane_testkit.sim.profiles import SimProfileError, load_sim_profile

REPO = Path(__file__).resolve().parents[2]
SHIPPED = REPO / "rigplane_testkit" / "sim" / "profiles" / "ic7610.toml"
BASE = "[sim]\nid = 'x'\nradio = 'IC-7300'\nfidelity = 'C'\ngaps = ['g']"

CASES = (
    "transport: exactly one of serial, lan or yaesu_cat required (none)|",
    "sim: required|!schema = 'rigplane-sim-profile/1'\n[transport.serial]\nbaud = 19200",
    "schema: must be 'rigplane-sim-profile/1', got 'rigplane-sim-profile/2'|!schema = 'rigplane-sim-profile/2'\n"
    + BASE,
    "sim.civ_addr: unknown key|!schema = 'rigplane-sim-profile/1'\n[sim]\nid = 'x'\nradio = 'IC-7300'\nfidelity = 'C'\nciv_addr = 0x98\ngaps = ['g']",
    "sim.radio: Unknown radio profile 'IC-999'|!schema = 'rigplane-sim-profile/1'\n[sim]\nid = 'x'\nradio = 'IC-999'\nfidelity = 'C'\ngaps = ['g']",
    "sim.evidence: tier A requires a path under tests/fixtures/|!schema = 'rigplane-sim-profile/1'\n[sim]\nid = 'x'\nradio = 'IC-7300'\nfidelity = 'A'\nevidence = ['tests/golden/wire_oracle/g.toml']\ngaps = ['g']",
    "sim.evidence: tier A requires a path under tests/fixtures/|!schema = 'rigplane-sim-profile/1'\n[sim]\nid = 'x'\nradio = 'IC-7300'\nfidelity = 'A'\nevidence = ['tests/fixtures/../golden/wire_oracle/g.toml']\ngaps = ['g']",
    "sim.evidence: tier B requires a path under tests/golden/wire_oracle/|!schema = 'rigplane-sim-profile/1'\n[sim]\nid = 'x'\nradio = 'IC-7300'\nfidelity = 'B'\nevidence = ['tests/fixtures/e.jsonl']\ngaps = ['g']",
    "sim.evidence: 'tests/fixtures/missing.jsonl' missing under root|!schema = 'rigplane-sim-profile/1'\n[sim]\nid = 'x'\nradio = 'IC-7300'\nfidelity = 'C'\nevidence = ['tests/fixtures/missing.jsonl']\ngaps = ['g']",
    "sim.gaps: must be non-empty for fidelity tier 'B'|!schema = 'rigplane-sim-profile/1'\n[sim]\nid = 'x'\nradio = 'IC-7300'\nfidelity = 'B'\nevidence = ['tests/golden/wire_oracle/g.toml']\ngaps = []",
    "sim.evidence: duplicate entry 'tests/fixtures/e.jsonl'|!schema = 'rigplane-sim-profile/1'\n[sim]\nid = 'x'\nradio = 'IC-7300'\nfidelity = 'C'\nevidence = ['tests/fixtures/e.jsonl', 'tests/fixtures/e.jsonl']\ngaps = ['g']",
    "sim.gaps: entry 1 must be a non-empty string, got ''|[sim]\nid = 'x'\nradio = 'IC-7300'\nfidelity = 'C'\ngaps = ['ok', '']\n[transport.serial]\nbaud = 19200",
    "transport.serial.frob: unknown key|[transport.serial]\nbaud = 19200\nfrob = 1",
    "transport.serial.baud: required|[transport.serial]",
    "transport.serial.baud: must be a positive integer, got 'fast'|[transport.serial]\nbaud = 'fast'",
    "transport.lan.echo: unknown key|!schema = 'rigplane-sim-profile/1'\n[sim]\nid = 'x'\nradio = 'IC-7610'\nfidelity = 'C'\ngaps = ['g']\n[transport.lan]\necho = true",
    "transport.lan: 'IC-7300' capability profile declares no LAN|[transport.lan]",
    "transport.yaesu_cat: 'IC-7300' speaks 'civ', not yaesu_cat|[transport.yaesu_cat]\nbaud = 9600",
    "transport.serial.identity: must be one of ('94',), got '98'|[transport.serial]\nbaud = 19200\nidentity = '98'",
    "transport.serial.identity: required, no expected identity ids declared|!schema = 'rigplane-sim-profile/1'\n[sim]\nid = 'x'\nradio = 'IC-7300MK2'\nfidelity = 'C'\ngaps = ['g']\n[transport.serial]\nbaud = 19200",
    "initial.frequency_hz: must be an integer, got True|[transport.serial]\nbaud = 19200\n[initial]\nfrequency_hz = true",
    "initial.mode: must be one of ('USB',|[transport.serial]\nbaud = 19200\n[initial]\nmode = 'BOGUS'",
    "initial.filter: must be 0..3, got 99|[transport.serial]\nbaud = 19200\n[initial]\nfilter = 99",
    "initial.frequency_hz: 14074000000 outside the profile frequency ranges|!schema = 'rigplane-sim-profile/1'\n[sim]\nid = 'x'\nradio = 'IC-7610'\nfidelity = 'C'\ngaps = ['g']\n[transport.serial]\nbaud = 19200\n[initial]\nfrequency_hz = 14_074_000_000",
    "initial.sub: unknown key|[transport.serial]\nbaud = 19200\n[initial.sub]\nfrequency_hz = 7_000_000",
    "scope: 'FTX-1' capability profile declares no scope|!schema = 'rigplane-sim-profile/1'\n[sim]\nid = 'x'\nradio = 'FTX-1'\nfidelity = 'C'\ngaps = ['g']\n[transport.yaesu_cat]\nbaud = 9600\n[scope]\nbins = 100",
    "answers.recorded[0].file: 'rec.jsonl' missing under root|[transport.serial]\nbaud = 19200\n[answers]\nrecorded = [{ file = 'rec.jsonl', transport = 'lan' }]",
    "answers.recorded[0].transport: required non-empty string, got None|[transport.serial]\nbaud = 19200\n[answers]\nrecorded = [{ file = 'e.jsonl' }]",
    "audio.rx.transport: lan_stream needs a LAN radio, 'IC-7300' has none|[transport.serial]\nbaud = 19200\n[audio.rx]\ntransport = 'lan_stream'",
)


@pytest.mark.parametrize("case", CASES)
def test_invalid_profiles_name_the_key_path(tmp_path, case):
    prefix, body = case.split("|")
    if body.startswith("!"):
        text = body[1:]
    else:
        text = 'schema = "rigplane-sim-profile/1"\n'
        if "[sim]" not in body:
            text += BASE + "\n"
        text += body
    path = tmp_path / "sim.toml"
    path.write_text(text)
    fixtures = tmp_path / "tests" / "fixtures"
    golden = tmp_path / "tests" / "golden" / "wire_oracle"
    fixtures.mkdir(parents=True, exist_ok=True)
    golden.mkdir(parents=True, exist_ok=True)
    (fixtures / "e.jsonl").write_text("{}")
    (golden / "g.toml").write_text("")
    with pytest.raises(SimProfileError) as excinfo:
        load_sim_profile(path, root=tmp_path)
    assert str(excinfo.value).startswith(prefix)


def test_shipped_ic7610_profile_loads_against_the_repo_root():
    p = load_sim_profile(SHIPPED, root=REPO)
    assert p.schema == "rigplane-sim-profile/1"
    assert (p.sim.id, p.sim.radio, p.sim.fidelity) == ("ic7610-lan", "IC-7610", "A")
    assert p.sim.evidence[0].endswith("fixtures/ic7610/civ_pairs.jsonl")
    assert p.sim.evidence[1].endswith("golden/wire_oracle/ic7610.toml")
    assert len(p.sim.gaps) == 3
    assert (p.transport.kind, p.transport.identity) == ("lan", "98")
    assert (p.transport.keepalive_hold_s, p.transport.single_owner) == (85.0, True)
    assert (p.initial.frequency_hz, p.initial.mode) == (14_286_000, "USB")
    assert p.initial.filter == 1
    assert p.answers.recorded == (("tests/fixtures/ic7610/civ_pairs.jsonl", "lan"),)
    assert (p.audio_rx.transport, p.audio_rx.sample_rate_hz) == ("lan_stream", 48000)
    assert (p.audio_rx.channels, p.audio_rx.sample_format) == (2, "s16le")
    assert (p.scope.bins, p.scope.span_hz, p.scope.frame) == (689, 100_000, "27 00")
    with pytest.raises(FrozenInstanceError):
        p.sim = p.sim


def test_optional_sections_and_free_tables(tmp_path):
    (tmp_path / "rec.jsonl").write_text("{}")
    body = (
        "schema = 'rigplane-sim-profile/1'\n"
        + BASE
        + "\n[transport.serial]\nbaud = 19200\n"
        "[timing]\nreply_latency_ms = 2.5\n[unsolicited]\ntransceive = false\n"
        "[faults.defaults]\ndrop_rate = 0.01\n[state.extra]\nnote = 'bench'\n[answers]\n"
        "recorded = [{ file = 'rec.jsonl', transport = 'lan' }]"
    )
    (tmp_path / "sim.toml").write_text(body)
    p = load_sim_profile(tmp_path / "sim.toml", root=tmp_path)
    assert p.timing.reply_latency_ms == 2.5 and p.unsolicited == {"transceive": False}
    assert p.faults_defaults == {"drop_rate": 0.01} and p.state_extra == {
        "note": "bench"
    }
    assert p.answers.recorded == (("rec.jsonl", "lan"),)
    with pytest.raises(TypeError):
        p.unsolicited["x"] = 1
    with pytest.raises(FrozenInstanceError):
        p.transport.kind = "serial"


def test_identity_accepted_when_radio_declares_none(tmp_path):
    body = (
        "schema = 'rigplane-sim-profile/1'\n[sim]\nid = 'x'\n"
        "radio = 'IC-7300MK2'\nfidelity = 'C'\ngaps = ['g']\n"
        "[transport.serial]\nbaud = 19200\nidentity = 'A4'\n"
    )
    (tmp_path / "sim.toml").write_text(body)
    p = load_sim_profile(tmp_path / "sim.toml", root=tmp_path)
    assert p.transport.identity == "A4"
