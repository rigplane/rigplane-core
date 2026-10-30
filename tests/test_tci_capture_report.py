"""Coverage-report and fixture-extraction tests (MOR-3821 C0, mini-only).

The coverage report answers, for every open item of design sections 5.2
and 8.1, whether a capture holds the events needed to answer it. An item
without those events stays OPEN, never guessed. A capture that lacks the
TX events must report "incomplete".
"""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest

SCRIPT = (
    Path(__file__).resolve().parent.parent
    / "scripts"
    / "tci_capture"
    / "proxy.py"
)


def load_proxy():  # type: ignore[no-untyped-def]
    """Load the proxy script as a module (fails until the script exists)."""
    spec = importlib.util.spec_from_file_location("tci_capture_proxy", SCRIPT)
    if spec is None or spec.loader is None:
        raise FileNotFoundError(f"proxy script missing: {SCRIPT}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture()
def proxy_module():  # type: ignore[no-untyped-def]
    return load_proxy()


def trec(
    direction: str, text: str, tick: float
) -> dict[str, object]:
    return {
        "t_mono": tick,
        "t_wall": "2026-10-30T00:00:00+00:00",
        "dir": direction,
        "opcode": 1,
        "text": text,
    }


def brec(
    direction: str,
    stream_type: int,
    tick: float,
    *,
    length: int = 4,
    channels: int = 1,
    sample_rate: int = 48000,
    sample_format: int = 0,
    count: int = 0,
    payload_len: int = 72,
) -> dict[str, object]:
    return {
        "t_mono": tick,
        "t_wall": "2026-10-30T00:00:00+00:00",
        "dir": direction,
        "opcode": 2,
        "bin": {
            "receiver": 0,
            "sample_rate": sample_rate,
            "format": sample_format,
            "codec": 0,
            "crc": 0,
            "length": length,
            "type": stream_type,
            "channels": channels,
            "count": count,
            "reserved": [0, 0, 0, 0, 0, 0, 0],
        },
        "payload_len": payload_len,
    }


def full_session() -> list[dict[str, object]]:
    """A synthetic capture holding every event the report needs."""
    tick = 100.0

    def nxt() -> float:
        nonlocal tick
        tick += 0.05
        return tick

    records: list[dict[str, object]] = [
        trec("s2c", "protocol:ExpertSDR3,1.0;", nxt()),
        trec("s2c", "device:Flex-6400;", nxt()),
        trec("s2c", "trx_count:1;", nxt()),
        trec("s2c", "ready;", nxt()),
        trec("c2s", "vfo:0,0,14074000;", nxt()),
        trec("s2c", "vfo:0,0,14074000;", nxt()),
        trec("c2s", "vfo:0,1,14074010;", nxt()),
        trec("s2c", "vfo:0,1,14074010;", nxt()),
        trec("c2s", "split_enable:0,true;", nxt()),
        trec("c2s", "modulation:0,DIGU;", nxt()),
        trec("c2s", "audio_start:0;", nxt()),
        trec("s2c", "drive:0,50;", nxt()),
        brec("s2c", 1, nxt(), length=2048, channels=1, payload_len=64 + 4096),
        trec("c2s", "trx:0,true;", nxt()),
        brec("s2c", 3, nxt(), length=2048, channels=1, payload_len=64),
        brec("s2c", 2, nxt(), length=2048, channels=1, payload_len=64 + 4096),
    ]
    return records


_RX_ONLY = ("init_burst", "protocol_device", "rx_header", "length_semantics")


def test_report_complete_on_full_capture(proxy_module: object) -> None:
    report = proxy_module.coverage_report(full_session())  # type: ignore[attr-defined]
    assert report.complete is True
    assert all(item.status == "COVERED" for item in report.items)
    assert len(report.items) == 13


def test_report_incomplete_without_tx_events(proxy_module: object) -> None:
    records = [
        r
        for r in full_session()
        if not (
            (r.get("dir") == "c2s" and str(r.get("text", "")).startswith("trx:"))
            or (r.get("opcode") == 2 and r.get("bin", {}).get("type") in (2, 3))  # type: ignore[union-attr]
        )
    ]
    report = proxy_module.coverage_report(records)  # type: ignore[attr-defined]
    assert report.complete is False
    by_id = {item.id: item for item in report.items}
    for item_id in ("trx_set", "tx_chrono", "tx_header"):
        assert by_id[item_id].status == "OPEN"
    for item_id in _RX_ONLY:
        assert by_id[item_id].status == "COVERED"
    text = proxy_module.format_report(report)  # type: ignore[attr-defined]
    assert "INCOMPLETE" in text
    assert "COMPLETE" not in text.replace("INCOMPLETE", "")


def test_report_empty_capture_all_open(proxy_module: object) -> None:
    report = proxy_module.coverage_report([])  # type: ignore[attr-defined]
    assert report.complete is False
    assert all(item.status == "OPEN" for item in report.items)


def test_audio_start_argc_reported(proxy_module: object) -> None:
    one_arg = [trec("c2s", "audio_start:0;", 1.0)]
    two_arg = [trec("c2s", "audio_start:0,1;", 1.0)]
    first = proxy_module.coverage_report(one_arg).by_id("audio_start")  # type: ignore[attr-defined]
    second = proxy_module.coverage_report(two_arg).by_id("audio_start")  # type: ignore[attr-defined]
    assert first.status == "COVERED"
    assert "argc=1" in first.evidence
    assert second.status == "COVERED"
    assert "argc=2" in second.evidence


def test_extract_fixtures_writes_only_c2s_text(
    proxy_module: object, tmp_path: Path
) -> None:
    records = full_session()
    dest = tmp_path / "tci" / "wsjtx.tci"
    count = proxy_module.extract_fixtures(records, dest)  # type: ignore[attr-defined]
    lines = dest.read_text().splitlines()
    assert count == len(lines)
    expected = [
        str(r["text"])
        for r in records
        if r.get("dir") == "c2s" and r.get("opcode") == 1
    ]
    assert lines == expected
    assert lines
    assert all("protocol:" not in line for line in lines)
    blob = dest.read_bytes()
    assert b"ExpertSDR3" not in blob


def test_extract_fixtures_round_trips_through_capture_file(
    proxy_module: object, tmp_path: Path
) -> None:
    capture = tmp_path / "s.jsonl"
    capture.write_text(
        "\n".join(json.dumps(r) for r in full_session()) + "\n"
    )
    records = proxy_module.load_capture(capture)  # type: ignore[attr-defined]
    dest = tmp_path / "out.tci"
    count = proxy_module.extract_fixtures(records, dest)  # type: ignore[attr-defined]
    assert count > 0
    assert dest.read_text().splitlines() == [
        str(r["text"])
        for r in full_session()
        if r.get("dir") == "c2s" and r.get("opcode") == 1
    ]
