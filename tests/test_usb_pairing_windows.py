"""Replay the real W1 recording without opening audio or a serial port."""

import json
from pathlib import Path

import pytest

from rigplane.audio._usb_adapt_windows import adapt_windows
from rigplane.audio._usb_pairing import PairOk, PairRefused, pair
from rigplane.core.usb_identity import UsbAudioIdentity, UsbFunctionId


@pytest.fixture
def snapshot():
    session = json.loads(
        Path("tests/fixtures/usb_audio/windows/w1-gtr7-ic7300.json").read_text()
    )["sessions"][0]
    return adapt_windows(session)


@pytest.mark.parametrize(
    "identity", [None, UsbAudioIdentity(frozenset({UsbFunctionId("08BB", "2901")}))]
)
def test_s2_s5_w1_mme_pair_with_or_without_profile(snapshot, identity):
    result = pair(snapshot, "com3", identity)
    assert isinstance(result, PairOk)
    assert (result.rx_index, result.tx_index, result.hostapi) == (1, 5, "MME")
    assert result.codec_node.startswith("USB\\VID_08BB&PID_2901\\")
    assert result.rx_id.startswith("SWD\\MMDEVAPI\\{0.0.1.")
    assert result.tx_id.startswith("SWD\\MMDEVAPI\\{0.0.0.")
    assert result.evidence


def test_s2_w1_wrong_codec_identity_retains_nearby_mme_candidates(snapshot):
    result = pair(
        snapshot, "COM3", UsbAudioIdentity(frozenset({UsbFunctionId("0D8C", "0016")}))
    )
    assert isinstance(result, PairRefused)
    assert (result.reason, result.wire_reason) == (
        "codec_identity_mismatch",
        "usb_topology_unavailable",
    )
    assert {
        (candidate.portaudio_index, candidate.direction)
        for candidate in result.candidates
        if candidate.near_radio
    } == {(1, "capture"), (5, "render")}
    assert all(candidate.hostapi == "MME" for candidate in result.candidates)
    assert all(
        candidate.portaudio_index not in {0, 4} for candidate in result.candidates
    )
    assert result.candidates[0].near_radio
