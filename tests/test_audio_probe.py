"""Tests for audio capability probe evidence artifacts."""

from __future__ import annotations

from rigplane.audio.probe import (
    AudioProbeArtifact,
    AudioProbeCandidate,
    AudioProbeResult,
    AudioProbeStatus,
    build_stock_radio_lan_probe_matrix,
    classify_stock_radio_lan_probe_error,
    expected_pcm16_rx_payload_bytes,
    profile_policy_from_probe_results,
)
from rigplane.types import AudioCodec


def test_stock_radio_lan_probe_matrix_excludes_opus_and_stereo_tx() -> None:
    matrix = build_stock_radio_lan_probe_matrix(
        rx_codecs=(AudioCodec.PCM_2CH_16BIT, AudioCodec.OPUS_2CH),
        sample_rates_hz=(48_000, 16_000),
    )

    assert matrix
    assert {candidate.rx_codec for candidate in matrix} == {AudioCodec.PCM_2CH_16BIT}
    assert {candidate.tx_codec for candidate in matrix} == {AudioCodec.PCM_1CH_16BIT}
    assert {candidate.rx_channels for candidate in matrix} == {2}
    assert {candidate.tx_channels for candidate in matrix} == {1}
    assert {candidate.sample_rate_hz for candidate in matrix} == {48_000, 16_000}


def test_probe_artifact_serializes_machine_readable_evidence() -> None:
    candidate = AudioProbeCandidate(
        rx_codec=AudioCodec.PCM_1CH_16BIT,
        tx_codec=AudioCodec.PCM_1CH_16BIT,
        sample_rate_hz=16_000,
        rx_channels=1,
        tx_channels=1,
        frame_ms=20,
    )
    artifact = AudioProbeArtifact(
        model="IC-7610",
        profile_id="icom_ic7610",
        transport="stock_radio_lan",
        results=[
            AudioProbeResult(
                candidate=candidate,
                status=AudioProbeStatus.PASS,
                phase="rx",
                reason="rx-payload-stable",
                rx_payload_bytes=640,
                expected_rx_payload_bytes=640,
                observed_packets=50,
            )
        ],
    )

    data = artifact.to_dict()

    assert data["schema_version"] == 1
    assert data["model"] == "IC-7610"
    assert data["transport"] == "stock_radio_lan"
    assert data["results"][0]["candidate"]["rx_codec"] == "PCM_1CH_16BIT"
    assert data["results"][0]["status"] == "pass"
    assert data["results"][0]["rx_payload_bytes"] == 640
    assert data["results"][0]["expected_rx_payload_bytes"] == 640


def test_expected_pcm16_rx_payload_bytes_uses_frame_rate_channels_and_width() -> None:
    assert (
        expected_pcm16_rx_payload_bytes(
            AudioProbeCandidate(
                rx_codec=AudioCodec.PCM_2CH_16BIT,
                tx_codec=AudioCodec.PCM_1CH_16BIT,
                sample_rate_hz=48_000,
                rx_channels=2,
                tx_channels=1,
                frame_ms=20,
            )
        )
        == 3840
    )
    assert (
        expected_pcm16_rx_payload_bytes(
            AudioProbeCandidate(
                rx_codec=AudioCodec.PCM_1CH_16BIT,
                tx_codec=AudioCodec.PCM_1CH_16BIT,
                sample_rate_hz=16_000,
                rx_channels=1,
                tx_channels=1,
                frame_ms=20,
            )
        )
        == 640
    )
    assert (
        expected_pcm16_rx_payload_bytes(
            AudioProbeCandidate(
                rx_codec=AudioCodec.ULAW_2CH,
                tx_codec=AudioCodec.PCM_1CH_16BIT,
                sample_rate_hz=48_000,
                rx_channels=2,
                tx_channels=1,
                frame_ms=20,
            )
        )
        is None
    )


def test_stock_radio_lan_probe_error_classification_distinguishes_rejects() -> None:
    assert classify_stock_radio_lan_probe_error(
        RuntimeError("conninfo error=0xFFFFFFFF")
    ) == (
        AudioProbeStatus.REJECTED,
        "conninfo-rejected",
    )
    assert classify_stock_radio_lan_probe_error(RuntimeError("socket timeout")) == (
        AudioProbeStatus.FAILED,
        "runtime-error",
    )


def test_profile_policy_from_probe_results_uses_only_passed_evidence() -> None:
    passed = AudioProbeResult(
        candidate=AudioProbeCandidate(
            rx_codec=AudioCodec.PCM_2CH_16BIT,
            tx_codec=AudioCodec.PCM_1CH_16BIT,
            sample_rate_hz=16_000,
            rx_channels=2,
            tx_channels=1,
            frame_ms=20,
        ),
        status=AudioProbeStatus.PASS,
        phase="rx",
        reason="rx-payload-stable",
    )
    rejected = AudioProbeResult(
        candidate=AudioProbeCandidate(
            rx_codec=AudioCodec.ULAW_1CH,
            tx_codec=AudioCodec.PCM_1CH_16BIT,
            sample_rate_hz=48_000,
            rx_channels=1,
            tx_channels=1,
            frame_ms=20,
        ),
        status=AudioProbeStatus.REJECTED,
        phase="conninfo",
        reason="conninfo-rejected",
    )

    policy = profile_policy_from_probe_results([rejected, passed])

    assert policy == {
        "codec_preference": ["PCM_2CH_16BIT"],
        "tx_codec": "PCM_1CH_16BIT",
        "sample_rate_by_codec": {"PCM_2CH_16BIT": 16000},
    }
