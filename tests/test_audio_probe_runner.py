"""Tests for safe LAN audio probe execution helpers."""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from rigplane.audio.probe import (
    AudioProbeResult,
    AudioProbeStatus,
    build_stock_radio_lan_probe_matrix,
)
from rigplane.audio.probe_runner import (
    build_probe_artifact,
    dry_run_probe_results,
    run_audio_probe,
)
from rigplane.backends.config import LanBackendConfig, SerialBackendConfig
from rigplane.cli import _attempt_stock_radio_lan_audio_probe, _cmd_audio_probe


@pytest.mark.asyncio
async def test_run_audio_probe_executes_candidates_sequentially() -> None:
    candidates = build_stock_radio_lan_probe_matrix()[:2]
    seen: list[int] = []

    async def attempt(candidate):
        seen.append(candidate.sample_rate_hz)
        return AudioProbeResult(
            candidate=candidate,
            status=AudioProbeStatus.PASS,
            phase="rx",
            reason="rx-payload-stable",
            observed_packets=1,
            rx_payload_bytes=640,
        )

    results = await run_audio_probe(candidates, attempt)

    assert [result.status for result in results] == [
        AudioProbeStatus.PASS,
        AudioProbeStatus.PASS,
    ]
    assert seen == [candidate.sample_rate_hz for candidate in candidates]


@pytest.mark.asyncio
async def test_run_audio_probe_classifies_attempt_exceptions() -> None:
    candidate = build_stock_radio_lan_probe_matrix()[0]

    async def attempt(_candidate):
        raise RuntimeError("conninfo error=0xFFFFFFFF")

    result = (await run_audio_probe([candidate], attempt))[0]

    assert result.status is AudioProbeStatus.REJECTED
    assert result.phase == "conninfo"
    assert result.reason == "conninfo-rejected"
    assert "conninfo" in (result.error or "")


@pytest.mark.asyncio
async def test_run_audio_probe_applies_cooldown_between_candidates() -> None:
    candidates = build_stock_radio_lan_probe_matrix()[:3]
    events: list[str] = []

    async def attempt(candidate):
        events.append(f"attempt:{candidate.sample_rate_hz}")
        return AudioProbeResult(
            candidate=candidate,
            status=AudioProbeStatus.PASS,
            phase="rx",
            reason="rx-payload-stable",
        )

    async def sleep(seconds: float) -> None:
        events.append(f"sleep:{seconds:g}")

    await run_audio_probe(
        candidates,
        attempt,
        candidate_cooldown_s=35.0,
        sleep=sleep,
    )

    assert events == [
        "attempt:48000",
        "sleep:35",
        "attempt:24000",
        "sleep:35",
        "attempt:16000",
    ]


@pytest.mark.asyncio
async def test_run_audio_probe_retries_rejected_after_cooldown() -> None:
    candidate = build_stock_radio_lan_probe_matrix()[0]
    events: list[str] = []

    async def attempt(_candidate):
        events.append("attempt")
        if events.count("attempt") == 1:
            raise RuntimeError("conninfo error=0xFFFFFFFF")
        return AudioProbeResult(
            candidate=candidate,
            status=AudioProbeStatus.PASS,
            phase="rx",
            reason="rx-payload-stable",
        )

    async def sleep(seconds: float) -> None:
        events.append(f"sleep:{seconds:g}")

    results = await run_audio_probe(
        [candidate],
        attempt,
        candidate_cooldown_s=35.0,
        retry_rejected=1,
        sleep=sleep,
    )

    assert results[0].status is AudioProbeStatus.PASS
    assert events == ["attempt", "sleep:35", "attempt"]


def test_dry_run_probe_results_marks_candidates_skipped() -> None:
    candidates = build_stock_radio_lan_probe_matrix()[:3]

    results = dry_run_probe_results(candidates)

    assert len(results) == 3
    assert {result.status for result in results} == {AudioProbeStatus.SKIPPED}
    assert {result.phase for result in results} == {"dry-run"}


def test_build_probe_artifact_records_profile_and_summary() -> None:
    candidate = build_stock_radio_lan_probe_matrix()[0]
    artifact = build_probe_artifact(
        model="IC-7610",
        profile_id="icom_ic7610",
        transport="stock_radio_lan",
        results=[
            AudioProbeResult(
                candidate=candidate,
                status=AudioProbeStatus.PASS,
                phase="rx",
                reason="rx-payload-stable",
            )
        ],
        metadata={"duration_s": 0.1},
    )

    data = artifact.to_dict()

    assert data["model"] == "IC-7610"
    assert data["metadata"]["duration_s"] == 0.1
    assert data["metadata"]["summary"] == {
        "pass": 1,
        "rejected": 0,
        "failed": 0,
        "skipped": 0,
    }


@pytest.mark.asyncio
async def test_cmd_audio_probe_dry_run_writes_machine_readable_artifact(
    tmp_path,
    capsys,
) -> None:
    output = tmp_path / "probe.json"
    args = SimpleNamespace(
        dry_run=True,
        json=True,
        output=str(output),
        duration=0.01,
        limit=2,
        candidate_cooldown=35.0,
        retry_rejected=1,
    )
    config = LanBackendConfig(host="127.0.0.1", model="IC-7610")

    rc = await _cmd_audio_probe(config, args)

    assert rc == 0
    stdout = capsys.readouterr().out
    payload = json.loads(stdout)
    written = json.loads(output.read_text())
    assert payload == written
    assert payload["transport"] == "stock_radio_lan"
    assert payload["model"] == "IC-7610"
    assert payload["metadata"]["candidate_cooldown_s"] == 35.0
    assert payload["metadata"]["retry_rejected"] == 1
    assert len(payload["results"]) == 2
    assert {result["status"] for result in payload["results"]} == {"skipped"}


@pytest.mark.asyncio
async def test_cmd_audio_probe_rejects_non_lan_backend(capsys) -> None:
    args = SimpleNamespace(
        dry_run=True,
        json=False,
        output=None,
        duration=0.01,
        limit=None,
        candidate_cooldown=0.0,
        retry_rejected=0,
    )
    config = SerialBackendConfig(device="/dev/ttyUSB0")

    rc = await _cmd_audio_probe(config, args)

    assert rc == 1
    assert "LAN backend" in capsys.readouterr().err


@pytest.mark.asyncio
async def test_stock_radio_lan_attempt_is_rx_only(monkeypatch) -> None:
    candidate = build_stock_radio_lan_probe_matrix(sample_rates_hz=(16_000,))[0]
    calls: list[str] = []

    class Packet:
        data = b"\x00" * 1280

    class FakeRadio:
        async def __aenter__(self):
            calls.append("enter")
            return self

        async def __aexit__(self, *_exc_info):
            calls.append("exit")

        async def start_audio_rx_opus(self, callback):
            calls.append("start-rx")
            callback(Packet())

        async def stop_audio_rx_opus(self):
            calls.append("stop-rx")

        async def start_audio_tx_opus(self, *_args, **_kwargs):
            raise AssertionError("TX must not be started by RX-only probe")

        async def start_audio_tx_pcm(self, *_args, **_kwargs):
            raise AssertionError("TX must not be started by RX-only probe")

    monkeypatch.setattr("rigplane.cli.create_radio", lambda _config: FakeRadio())
    config = LanBackendConfig(host="127.0.0.1", model="IC-7610")

    result = await _attempt_stock_radio_lan_audio_probe(
        config,
        candidate,
        duration_s=0,
    )

    assert result.status is AudioProbeStatus.PASS
    assert result.observed_packets == 1
    assert result.rx_payload_bytes == 1280
    assert result.expected_rx_payload_bytes == 1280
    assert calls == ["enter", "start-rx", "stop-rx", "exit"]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("sample_rate_hz", "payload_bytes", "expected_payload_bytes"),
    [
        (48_000, 1112, 3840),
        (24_000, 556, 1920),
    ],
)
async def test_stock_radio_lan_attempt_fails_pcm_payload_size_mismatch(
    monkeypatch,
    sample_rate_hz,
    payload_bytes,
    expected_payload_bytes,
) -> None:
    candidate = build_stock_radio_lan_probe_matrix(
        sample_rates_hz=(sample_rate_hz,),
    )[0]

    class Packet:
        data = b"\x00" * payload_bytes

    class FakeRadio:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_exc_info):
            pass

        async def start_audio_rx_opus(self, callback):
            callback(Packet())

        async def stop_audio_rx_opus(self):
            pass

    monkeypatch.setattr("rigplane.cli.create_radio", lambda _config: FakeRadio())
    config = LanBackendConfig(host="127.0.0.1", model="IC-7610")

    result = await _attempt_stock_radio_lan_audio_probe(
        config,
        candidate,
        duration_s=0,
    )

    assert result.status is AudioProbeStatus.FAILED
    assert result.phase == "rx"
    assert result.reason == "pcm-payload-size-mismatch"
    assert result.rx_payload_bytes == payload_bytes
    assert result.expected_rx_payload_bytes == expected_payload_bytes
    assert result.observed_packets == 1
