"""Shipped IC-705 simulation profiles (MOR-3605): USB and LAN, tier A.

Every asserted value is read back from the fixture it was sourced from
(tests/fixtures/ic705/*), not restated by hand.
"""

import json
from pathlib import Path

import pytest

from rigplane_testkit.sim.profiles import SimProfileError, load_sim_profile

REPO = Path(__file__).resolve().parents[2]
PROFILES = REPO / "rigplane_testkit" / "sim" / "profiles"
FIXTURES = REPO / "tests" / "fixtures" / "ic705"
USB = PROFILES / "ic705-usb.toml"
LAN = PROFILES / "ic705-lan.toml"


def _civ_rows(transport):
    with (FIXTURES / "civ_pairs.jsonl").open(encoding="utf-8") as fh:
        return [
            json.loads(line)
            for line in fh
            if json.loads(line)["transport"] == transport
        ]


@pytest.mark.parametrize("path", [USB, LAN])
def test_ic705_profiles_share_the_recorded_identity_and_state(path):
    p = load_sim_profile(path, root=REPO)
    assert p.schema == "rigplane-sim-profile/1"
    assert (p.sim.radio, p.sim.fidelity) == ("IC-705", "A")
    assert p.sim.gaps
    for entry in p.sim.evidence:
        assert (REPO / entry).is_file(), entry
    # rigs/ic705.toml declares no expected identity ids; the profile must
    # carry the recorded 19 00 reply A4 (probe_answers.json get_transceiver_id).
    assert p.transport.identity == "A4"
    # README "Radio state": HF, 14.074 MHz USB, filter 1 (probe get_mode
    # reply 04 01 01).
    assert (p.initial.frequency_hz, p.initial.mode, p.initial.filter) == (
        14_074_000,
        "USB",
        1,
    )
    for answer_file, transport in p.answers.recorded:
        assert (REPO / answer_file).is_file(), answer_file
        assert _civ_rows(transport), f"no {transport} rows in {answer_file}"
    assert (p.scope.frame, p.scope.bins, p.scope.span_hz) == (
        "27 00",
        475,
        50_000,
    )


def test_ic705_usb_profile_matches_the_usb_fixtures():
    p = load_sim_profile(USB, root=REPO)
    assert p.sim.id == "ic705-usb"
    assert (p.transport.kind, p.transport.baud, p.transport.echo) == (
        "serial",
        115200,
        False,
    )
    assert p.answers.recorded == (("tests/fixtures/ic705/civ_pairs.jsonl", "usb"),)
    # The claim behind echo = false: on USB the radio sent no byte-identical
    # copy of any request back (README trim rule; LAN rows do carry echoes).
    rows = _civ_rows("usb")
    requests = {row["frame"] for row in rows if row["dir"] == "c2r"}
    assert not any(row["dir"] == "r2c" and row["frame"] in requests for row in rows)
    device = json.loads((FIXTURES / "usb_audio.json").read_text(encoding="utf-8"))
    rx = device["coreaudio_format"]["rx_input"]
    assert (p.audio_rx.transport, p.audio_rx.sample_format) == ("usb_device", "s16le")
    assert (p.audio_rx.sample_rate_hz, p.audio_rx.channels) == (
        rx["sample_rate_hz"],
        rx["channels"],
    )
    cadence = json.loads((FIXTURES / "scope_cadence.json").read_text(encoding="utf-8"))
    assert cadence["transport"] == "usb_civ"
    assert p.scope.rate_hz == cadence["radio_wave"]["sweeps_per_s"]


def test_ic705_lan_profile_matches_the_lan_fixtures():
    p = load_sim_profile(LAN, root=REPO)
    assert p.sim.id == "ic705-lan"
    assert p.transport.kind == "lan"
    # Not measured for the IC-705: both keys must stay absent, never copied
    # from the IC-7610 profile.
    assert p.transport.keepalive_hold_s is None
    assert p.transport.single_owner is None
    assert p.answers.recorded == (("tests/fixtures/ic705/civ_pairs.jsonl", "lan"),)
    stream = json.loads((FIXTURES / "audio_stream.json").read_text(encoding="utf-8"))
    assert (
        stream["format"]
        == "PCM 1 channel 16-bit, 48 kHz (960 samples = 20 ms per burst)"
    )
    assert (p.audio_rx.transport, p.audio_rx.sample_format) == ("lan_stream", "s16le")
    assert (p.audio_rx.sample_rate_hz, p.audio_rx.channels) == (48_000, 1)
    cadence = json.loads((FIXTURES / "scope_cadence.json").read_text(encoding="utf-8"))
    assert p.scope.rate_hz == cadence["lan"]["radio_wave"]["sweeps_per_s"]


def test_tier_a_without_a_recording_is_refused(tmp_path):
    golden = tmp_path / "tests" / "golden" / "wire_oracle"
    golden.mkdir(parents=True)
    (golden / "g.toml").write_text("")
    body = (
        "schema = 'rigplane-sim-profile/1'\n[sim]\nid = 'x'\n"
        "radio = 'IC-705'\nfidelity = 'A'\n"
        "evidence = ['tests/golden/wire_oracle/g.toml']\ngaps = ['g']\n"
    )
    (tmp_path / "sim.toml").write_text(body)
    with pytest.raises(SimProfileError) as excinfo:
        load_sim_profile(tmp_path / "sim.toml", root=tmp_path)
    assert str(excinfo.value).startswith(
        "sim.evidence: tier A requires a path under tests/fixtures/"
    )
