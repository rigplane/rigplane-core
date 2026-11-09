import json
from pathlib import Path

import pytest

from e2e.panorama_web_server import PanoramaSimulator, ScopeWriteReceipt
from rigplane.commands import parse_civ_frame
from rigplane.commands.scope import _span_index_for_hz, parse_scope_span_response
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
