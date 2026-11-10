import json
from pathlib import Path

import pytest

from e2e.panorama_web_server import PanoramaSimulator
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


def test_panorama_receipt_contract() -> None:
    from e2e.panorama_web_server import ScopeWriteReceipt

    wire = (Path(__file__).parent / "fixtures/panorama-receipt.json").read_text()
    receipt = ScopeWriteReceipt(**json.loads(wire))
    assert receipt == ScopeWriteReceipt(sub=21, data=[0, 0, 0, 16, 0, 0], span=100_000)
    assert json.loads(receipt.to_json()) == json.loads(wire)


@pytest.mark.parametrize("model", ["IC-705", "IC-7300"])
def test_scope_span_capability_fixture(model: str) -> None:
    from test_web_server_coverage import _scope_radio
    from rigplane.web.server import WebConfig, WebServer

    server = WebServer(
        _scope_radio(), WebConfig(radio_model=model, scope_source="hardware")
    )
    caps = server._scope_source_capabilities(server._get_profile())
    fixture = Path(__file__).parent / "fixtures/scope-span-capabilities.json"
    assert caps == json.loads(fixture.read_text())


def _nak_log(sim: PanoramaSimulator) -> list[dict[str, object]]:
    from e2e.panorama_web_server import NakReceipt  # noqa: PLC0415

    lines = (sim.evidence.with_suffix(".nak.jsonl")).read_text().splitlines()
    receipts = [json.loads(line) for line in lines]
    for receipt in receipts:
        NakReceipt(**receipt)  # typed contract holds on every line
    return receipts


def test_nak_cites_civ_frame_past_lan_header(tmp_path: Path) -> None:
    """D7: the refused frame is sliced past the 0x15-byte LAN header.

    Searching the whole packet for FE FE matched header bytes and invented
    refusals such as rc8's "cmd 0xe0". The header here deliberately contains
    FE FE; the receipt must still cite the real CI-V frame only.
    """
    from rigplane_testkit.icom.simulator import _CIV_HEADER_SIZE  # noqa: PLC0415

    sim = PanoramaSimulator("IC-705", tmp_path / "radio.jsonl")
    civ = bytes.fromhex("fefea4e01409fd")
    header = bytearray(_CIV_HEADER_SIZE)
    header[8:10] = b"\xfe\xfe"  # the D7 trap: header bytes mimicking a preamble
    header[16] = 0xE0  # the byte the old search reported as "cmd"
    sim._last_nak_payload = bytes(header) + civ
    sim._last_nak_was_civ = True
    sim._civ_nak(0xE0, 0xA4)
    (receipt,) = _nak_log(sim)
    assert receipt["civ_hex"] == civ.hex()


def test_nak_without_civ_frame_stays_empty(tmp_path: Path) -> None:
    """Handshake/control NAKs keep an empty frame (tolerated by invariant 5)."""
    sim = PanoramaSimulator("IC-705", tmp_path / "radio.jsonl")
    sim._last_nak_payload = b""
    sim._last_nak_was_civ = False
    sim._civ_nak(0xE0, 0xA4)
    (receipt,) = _nak_log(sim)
    assert receipt["civ_hex"] == ""


def test_refuse_once_fires_exactly_once(tmp_path: Path) -> None:
    """D6: the armed 1C 01 refusal answers one matching read, then disarms.

    The unit call bypasses _dispatch (which stamps the refused payload in
    production), so both NAKs carry an empty frame here — the exactly-once
    property under test is the disarm after the first match.
    """
    from rigplane.commands import parse_civ_frame  # noqa: PLC0415

    sim = PanoramaSimulator(
        "IC-705", tmp_path / "radio.jsonl", refuse_once=(0x1C, 0x01)
    )
    first = sim._dispatch_civ(0x1C, b"\x01", 0xE0)
    assert first is not None and parse_civ_frame(first).command == 0xFA
    assert sim.refuse_once is None
    second = sim._dispatch_civ(0x1C, b"\x01", 0xE0)
    assert second is not None and parse_civ_frame(second).command == 0xFA
