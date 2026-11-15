import asyncio
import json
import time
from pathlib import Path

import pytest

from _perf_helpers import fast_connect
from e2e.panorama_web_server import PanoramaSimulator, ScopeWriteReceipt
from rigplane.commands import parse_civ_frame
from rigplane.commands.scope import _span_index_for_hz, parse_scope_span_response
from rigplane.radio import IcomRadio
from rigplane.scope import ScopeAssembler


@pytest.mark.parametrize("model", ["IC-705", "IC-7300"])
def test_panorama_waveform_and_control_span_agree(model: str, tmp_path: Path) -> None:
    sim = PanoramaSimulator(model, tmp_path / "radio.jsonl")
    sim.pump_scope_stream()
    reply = sim._dispatch_scope(b"\x15\x00", 0xE0)
    assert reply is not None
    _, index = parse_scope_span_response(
        parse_civ_frame(reply), sim.profile.scope_span_presets_hz
    )
    assembler = ScopeAssembler()
    for raw in sim._scope_templates[0]:
        civ = parse_civ_frame(raw)
        frame = assembler.feed(civ.data[1:], civ.data[0])
        if frame is not None:
            span = sim.profile.scope_span_presets_hz[index]
            assert (frame.start_freq_hz, frame.end_freq_hz) == (
                14_250_000 - span,
                14_250_000 + span,
            )
            assert (
                _span_index_for_hz(
                    (frame.end_freq_hz - frame.start_freq_hz) // 2,
                    sim.profile.scope_span_presets_hz,
                )
                == index
            )
            return
    pytest.fail("simulator produced no complete waveform")


@pytest.mark.parametrize(
    "receipt",
    [
        ScopeWriteReceipt(**item)
        for item in json.loads(
            (Path(__file__).parent / "fixtures/panorama-receipt.json").read_text()
        )
    ],
    ids=lambda receipt: f"{receipt.sub:#x}",
)
def test_panorama_receipt_contract(receipt: ScopeWriteReceipt, tmp_path: Path) -> None:
    evidence = tmp_path / f"{receipt.sub}.jsonl"
    sim = PanoramaSimulator("IC-7610", evidence)
    data = bytes(receipt.data)
    read = (
        data[:2]
        if receipt.sub == 0x1E
        else data[:1]
        if receipt.sub in (0x14, 0x15, 0x16)
        else b""
    )
    for count in (1, 2):
        assert sim._dispatch_scope(bytes([receipt.sub]) + data, 0xE0) is not None
        assert evidence.exists(), f"missing write receipt: {receipt.sub:#x}"
        assert sim._dispatch_scope(bytes([receipt.sub]) + read, 0xE0) is not None
        assert [
            ScopeWriteReceipt(**json.loads(line))
            for line in evidence.read_text().splitlines()
        ] == [receipt] * count
    assert ScopeWriteReceipt(**json.loads(receipt.to_json())) == receipt


@pytest.mark.parametrize("model", ["IC-705", "IC-7300", "IC-7610"])
def test_scope_span_capability_fixture(model: str) -> None:
    from test_web_server_coverage import _scope_radio
    from rigplane.web.server import WebConfig, WebServer

    server = WebServer(
        _scope_radio(), WebConfig(radio_model=model, scope_source="hardware")
    )
    caps = server._scope_source_capabilities(server._get_profile())
    fixture = Path(__file__).parent / "fixtures/scope-span-capabilities.json"
    assert caps == json.loads(fixture.read_text())


@pytest.mark.parametrize("model", ["IC-705", "IC-7300", "IC-7610"])
def test_scope_span_choices_publish_full_view_widths(model: str) -> None:
    """MOR-4451: the published span_choices_hz fact states FULL view widths
    (end - start) — twice an Icom ±half-width preset. Fails if the server
    goes back to publishing the radio's half-width presets."""
    from test_web_server_coverage import _scope_radio
    from rigplane.web.server import WebConfig, WebServer

    server = WebServer(
        _scope_radio(), WebConfig(radio_model=model, scope_source="hardware")
    )
    profile = server._get_profile()
    assert profile is not None
    assert profile.scope_span_preset_semantics == "half_width"
    caps = server._scope_source_capabilities(profile)
    assert caps["span_choices_hz"] == [
        2 * preset for preset in profile.scope_span_presets_hz
    ]


def test_latency_harness_uses_the_shared_civ_plan(tmp_path: Path) -> None:
    """The latency knobs configure the shared outbound CI-V plan — nothing else.

    The removed per-harness queue doubled the simulator's own mechanisms: it
    stacked a second delay on top of the inherited ``response_delay``
    dispatch delay (a configured 100 ms cost ~200 ms on CI-V replies) and
    delayed control-port traffic too. Now the plan carries the delay once,
    on CI-V only, and the inherited dispatch delay stays unset.
    """
    sim = PanoramaSimulator(
        "IC-705",
        tmp_path / "radio.jsonl",
        response_delay=0.1,
        response_jitter=0.05,
        seed=7,
    )
    rule = sim.fault_plan.civ_to_client
    assert (rule.delay_s, rule.jitter_s, rule.preserve_order) == (0.1, 0.05, True)
    assert sim.fault_plan.seed == 7
    assert sim.fault_plan.is_noop("control", "to_client")
    assert sim.fault_plan.is_noop("control", "from_client")
    assert sim.fault_plan.is_noop("civ", "from_client")
    assert sim.response_delay == 0.0


def test_latency_harness_default_is_passthrough(tmp_path: Path) -> None:
    """No knobs, no faults: the default harness passes CI-V through untouched."""
    sim = PanoramaSimulator("IC-705", tmp_path / "radio.jsonl")
    assert sim.fault_plan.is_noop("civ", "to_client")
    assert sim.response_delay == 0.0


@pytest.mark.realtime(reason="measures a real client round trip against the sim")
async def test_configured_delay_applies_once_over_the_wire(
    tmp_path: Path,
) -> None:
    """A real client against the latency harness: one delay, not two."""
    sim = PanoramaSimulator(
        "IC-705", tmp_path / "radio.jsonl", response_delay=0.2, seed=1
    )
    await sim.start()
    try:
        client = IcomRadio(
            host="127.0.0.1",
            port=sim.control_port,
            username="testuser",
            password="testpass",
            model="IC-705",
            timeout=5.0,
        )
        with fast_connect():
            await client.connect()
        try:
            started = time.monotonic()
            await asyncio.wait_for(client.get_freq(), 10.0)
            elapsed = time.monotonic() - started
        finally:
            await asyncio.wait_for(client.disconnect(), 10.0)
    finally:
        await sim.stop()
    assert elapsed >= 0.2, "the configured delay must apply"
    assert elapsed < 0.35, f"one delay, not two: {elapsed:.3f}s for delay 0.2"
