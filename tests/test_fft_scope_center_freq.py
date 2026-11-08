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
        ("AM", -1200, 1200, "both"),
        ("FM", -1200, 1200, "both"),
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


@pytest.mark.parametrize(
    "model,mode,half",
    [
        ("IC-7610", "CW", 1800),
        ("IC-7610", "CW-R", 1800),
        ("IC-7610", "RTTY", 1350),
        ("IC-7610", "RTTY-R", 1350),
        ("IC-7300", "CW", 1800),
        ("IC-7300", "RTTY", 1350),
    ],
)
def test_ambiguous_modes_map_main_symmetric_crop(model, mode, half):
    """CW/CW-R/RTTY/RTTY-R keep main's symmetric VFO-centered RF placement.

    Main's AudioFftScope crops symmetrically to the mode filter rule
    (set_mode_bandwidth(rule.max_hz)); the new source must grant the same
    coverage instead of refusing the mapping.
    """
    server = WebServer(None, WebConfig(radio_model=model))
    _seed_audio_facts(server, mode=mode)
    facts = WebScopePcmTap(server).facts()
    assert facts.tuned_hz == 14_074_000
    assert (facts.low_offset_hz, facts.high_offset_hz, facts.sideband) == (
        -half,
        half,
        "both",
    )


@pytest.mark.parametrize(
    "model,mode",
    [
        ("IC-7610", "DV"),
        ("IC-7300", "DV"),
        ("FTX-1", "DV"),
        ("FTX-1", "CW"),
        ("FTX-1", "CW-R"),
        ("FTX-1", "RTTY"),
        ("FTX-1", "RTTY-R"),
    ],
)
def test_unruled_modes_map_full_spectrum(model, mode):
    """Modes with no profile filter rule keep main's full-spectrum placement."""
    from rigplane.audio.scope_source import AudioPassband

    server = WebServer(None, WebConfig(radio_model=model))
    _seed_audio_facts(server, mode=mode)
    assert WebScopePcmTap(server, sample_rate=48000).facts() == AudioPassband(
        14_074_000, -24000, 24000, "both"
    )


@pytest.mark.parametrize("receiver", ["0", "main"])
@pytest.mark.parametrize(
    "code,sideband,offset", [("3", "upper", -650), ("7", "lower", 650)]
)
def test_explicit_cw_side_from_producer_profile_maps_observed_pitch(
    receiver, code, sideband, offset
):
    from pathlib import Path
    from rigplane.audio.scope_source import AudioPassband
    from rigplane.profiles.rig_loader import load_rig
    from rigplane.core.state_pipeline_contracts import (
        FieldPath,
        Observation,
        SourceMetadata,
    )

    config = load_rig(Path(__file__).resolve().parents[1] / "rigs/ftx1.toml")
    mode = dict(zip(config.mode_codes, config.modes, strict=True))[code]
    server = WebServer(None)
    _seed_audio_facts(server, receiver=receiver, mode=mode, width=500)
    server.command_state_store.apply_current(
        Observation(
            path=FieldPath.global_("operator_controls", "cw_pitch"),
            value=650,
            source=SourceMetadata(source="poll_response", provider="test"),
            timestamp_monotonic=0,
        )
    )
    assert WebScopePcmTap(server).facts() == AudioPassband(
        14_074_000, -250, 250, sideband, offset
    )


@pytest.mark.parametrize("code", ["3", "7"])
def test_explicit_cw_side_still_requires_observed_pitch(code):
    from pathlib import Path
    from rigplane.profiles.rig_loader import load_rig

    config = load_rig(Path(__file__).resolve().parents[1] / "rigs/ftx1.toml")
    mode = dict(zip(config.mode_codes, config.modes, strict=True))[code]
    server = WebServer(None)
    _seed_audio_facts(server, receiver="main", mode=mode)
    with pytest.raises(ValueError, match="observed CW pitch"):
        WebScopePcmTap(server).facts()


@pytest.mark.parametrize(
    "code,low,high,sideband",
    [
        ("8", -2400, 0, "lower"),
        ("C", 0, 2400, "upper"),
        ("D", -1200, 1200, "both"),
        ("B", -1200, 1200, "both"),
        ("A", -1200, 1200, "both"),
        ("F", -1200, 1200, "both"),
    ],
)
def test_explicit_audio_alias_from_producer_profile_uses_existing_geometry(
    code, low, high, sideband
):
    from pathlib import Path
    from rigplane.audio.scope_source import AudioPassband
    from rigplane.profiles.rig_loader import load_rig

    config = load_rig(Path(__file__).resolve().parents[1] / "rigs/ftx1.toml")
    mode = dict(zip(config.mode_codes, config.modes, strict=True))[code]
    server = WebServer(None)
    _seed_audio_facts(server, receiver="main", mode=mode, width=2400)
    assert WebScopePcmTap(server).facts() == AudioPassband(
        14_074_000, low, high, sideband
    )
