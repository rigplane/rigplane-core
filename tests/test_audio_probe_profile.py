"""Tests for guarded profile patch proposals from audio probe artifacts."""

from __future__ import annotations

import json

import pytest

from rigplane.audio.probe_profile import (
    AudioProfileProposalError,
    propose_audio_profile_patch,
)
from rigplane.cli import _build_parser, _cmd_audio_probe_profile


def _artifact(results: list[dict[str, object]]) -> dict[str, object]:
    return {
        "schema_version": 1,
        "tool": "rigplane-audio-probe",
        "model": "IC-7610",
        "profile_id": "icom_ic7610",
        "transport": "stock_radio_lan",
        "metadata": {"firmware_version": "1.42"},
        "results": results,
    }


def _result(
    *,
    status: str,
    rx_codec: str,
    sample_rate_hz: int,
    tx_codec: str = "PCM_1CH_16BIT",
) -> dict[str, object]:
    return {
        "status": status,
        "phase": "rx",
        "reason": "rx-payload-stable" if status == "pass" else "conninfo-rejected",
        "candidate": {
            "rx_codec": rx_codec,
            "tx_codec": tx_codec,
            "sample_rate_hz": sample_rate_hz,
            "rx_channels": 2 if "_2CH" in rx_codec else 1,
            "tx_channels": 1,
            "frame_ms": 20,
            "mode": "rx-only",
        },
    }


def test_propose_audio_profile_patch_uses_only_passed_results() -> None:
    proposal = propose_audio_profile_patch(
        _artifact(
            [
                _result(status="rejected", rx_codec="ULAW_1CH", sample_rate_hz=48000),
                _result(
                    status="pass",
                    rx_codec="PCM_2CH_16BIT",
                    sample_rate_hz=48000,
                ),
                _result(
                    status="pass",
                    rx_codec="PCM_1CH_16BIT",
                    sample_rate_hz=16000,
                ),
            ]
        )
    )

    assert proposal.toml == "\n".join(
        [
            "[audio]",
            'codec_preference = ["PCM_2CH_16BIT", "PCM_1CH_16BIT"]',
            'tx_codec = "PCM_1CH_16BIT"',
            "default_sample_rate_hz = 48000",
            "sample_rate_by_codec = { PCM_2CH_16BIT = 48000, PCM_1CH_16BIT = 16000 }",
            "",
        ]
    )
    assert proposal.warnings == []


def test_propose_audio_profile_patch_rejects_no_pass_artifacts() -> None:
    with pytest.raises(AudioProfileProposalError, match="No passed probe results"):
        propose_audio_profile_patch(
            _artifact(
                [
                    _result(
                        status="rejected",
                        rx_codec="PCM_2CH_16BIT",
                        sample_rate_hz=48000,
                    )
                ]
            )
        )


def test_propose_audio_profile_patch_warns_when_model_or_firmware_missing() -> None:
    artifact = _artifact(
        [_result(status="pass", rx_codec="PCM_2CH_16BIT", sample_rate_hz=48000)]
    )
    artifact["model"] = "unknown"
    artifact["metadata"] = {}

    proposal = propose_audio_profile_patch(artifact)

    assert proposal.warnings == [
        "artifact model/profile_id metadata is missing or unknown",
        "artifact firmware/version metadata is missing",
    ]


def test_audio_probe_profile_parser() -> None:
    args = _build_parser().parse_args(
        ["audio", "probe-profile", "--artifact", "probe.json"]
    )

    assert args.command == "audio"
    assert args.audio_command == "probe-profile"
    assert args.artifact == "probe.json"


@pytest.mark.asyncio
async def test_cmd_audio_probe_profile_prints_patch(tmp_path, capsys) -> None:
    artifact_path = tmp_path / "probe.json"
    artifact_path.write_text(
        json.dumps(
            _artifact(
                [
                    _result(
                        status="pass",
                        rx_codec="PCM_2CH_16BIT",
                        sample_rate_hz=48000,
                    )
                ]
            )
        ),
        encoding="utf-8",
    )
    args = _build_parser().parse_args(
        ["audio", "probe-profile", "--artifact", str(artifact_path)]
    )

    rc = await _cmd_audio_probe_profile(args)

    assert rc == 0
    assert '[audio]\ncodec_preference = ["PCM_2CH_16BIT"]' in capsys.readouterr().out
