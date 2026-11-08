"""Audio RF geometry follows canonical Icom and Yaesu observations."""

import pytest

from rigplane.web.scope_sources import WebScopePcmTap
from rigplane.web.server import WebServer, WebConfig
from test_audio_scope_dispatch import _seed_audio_facts


@pytest.mark.parametrize("receiver", ["0", "main"])
@pytest.mark.parametrize(
    "mode,low,high,sideband",
    [
        ("USB", 0, 2400, "upper"),
        ("LSB", -2400, 0, "lower"),
        ("AM", -1200, 1200, "upper"),
        ("FM", -1200, 1200, "upper"),
    ],
)
def test_audio_passband_reads_observed_provider_geometry(
    receiver, mode, low, high, sideband
):
    server = WebServer(None, WebConfig(radio_model="IC-7610"))
    _seed_audio_facts(server, receiver, mode)
    facts = WebScopePcmTap(server).facts()
    assert facts.tuned_hz == 14_074_000
    assert (facts.low_offset_hz, facts.high_offset_hz, facts.sideband) == (
        low,
        high,
        sideband,
    )


def test_audio_passband_refuses_missing_facts():
    server = WebServer(None)
    with pytest.raises(ValueError, match="observed"):
        WebScopePcmTap(server).facts()


@pytest.mark.parametrize("mode", ["RTTY", "RTTY-R", "DV"])
def test_audio_passband_does_not_invent_carrier_offsets(mode):
    server = WebServer(None)
    _seed_audio_facts(server, mode=mode)
    with pytest.raises(ValueError, match="mapping unavailable"):
        WebScopePcmTap(server).facts()


@pytest.mark.parametrize("receiver", ["0", "main"])
@pytest.mark.parametrize("mode,sideband,offset", [("CW", "upper", -650), ("CW-R", "lower", 650)])
def test_cw_passband_uses_observed_pitch(receiver, mode, sideband, offset):
    from rigplane.audio.scope_source import AudioPassband
    from rigplane.core.state_pipeline_contracts import FieldPath, Observation, SourceMetadata
    server = WebServer(None)
    _seed_audio_facts(server, receiver=receiver, mode=mode, width=500)
    server.command_state_store.apply_current(Observation(
        path=FieldPath.global_("operator_controls", "cw_pitch"), value=650,
        source=SourceMetadata(source="poll_response", provider="test"), timestamp_monotonic=0,
    ))
    assert WebScopePcmTap(server).facts() == AudioPassband(14_074_000, -250, 250, sideband, offset)


@pytest.mark.parametrize("mode", ["CW", "CW-R"])
def test_cw_passband_requires_observed_pitch(mode):
    server = WebServer(None)
    _seed_audio_facts(server, mode=mode)
    with pytest.raises(ValueError, match="observed CW pitch"):
        WebScopePcmTap(server).facts()
