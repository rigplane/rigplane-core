"""FTX-1 conformance: the CAT fake against a real-radio recording.

``tests/fixtures/ftx1/cat_pairs.jsonl`` holds the request/reply pairs of a
real FTX-1 (USB CAT, RX only; provenance in the fixtures README). The fake
runs with that recording mounted as its recorded-answer table and the
state its own handlers hold seeded from it, then every recorded request is
sent over the real TCP/``socket://`` path. Every request whose reply
differs from the recorded bytes is a divergence and must be listed in
``conformance_ledger.json`` — and a ledger entry that no longer diverges
must be deleted (the ratchet works in both directions).
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

from rigplane.core.serial_open import open_serial_port

from rigplane_testkit.yaesu import FakeCatRadio
from rigplane_testkit.yaesu.replay import RecordedCatAnswerTable

FIXTURES = Path(__file__).parents[1] / "fixtures" / "ftx1"
LEDGER = FIXTURES / "conformance_ledger.json"


def _table() -> RecordedCatAnswerTable:
    rows = [
        json.loads(line)
        for line in (FIXTURES / "cat_pairs.jsonl").read_text().splitlines()
    ]
    return RecordedCatAnswerTable.from_cat_pairs(rows)


async def _ask(reader, writer, request: str) -> str | None:
    writer.write(request.encode())
    await writer.drain()
    try:
        return (await asyncio.wait_for(reader.readuntil(b";"), timeout=0.5)).decode()
    except asyncio.TimeoutError:
        return None


async def _live_divergences() -> dict[str, dict[str, str | None]]:
    table = _table()
    frequency = int(table.lookup("FA;").reply[2:-1])  # type: ignore[union-attr]
    mode = table.lookup("MD0;").reply[3:-1]  # type: ignore[union-attr]
    radio = FakeCatRadio(frequency_hz=frequency, mode_code=mode, recorded_answers=table)
    await radio.start()
    reader, writer = await open_serial_port(radio.device_url, baudrate=38400)
    found: dict[str, dict[str, str | None]] = {}
    try:
        for request in table.requests():
            recorded = table.lookup(request)
            assert recorded is not None
            got = await _ask(reader, writer, request)
            if got != recorded.reply:
                found[request] = {"recorded": recorded.reply, "fake": got}
    finally:
        writer.close()
        await radio.stop()
    return found


async def test_fake_diverges_from_the_recording_only_where_the_ledger_says() -> None:
    live = await _live_divergences()
    ledger = json.loads(LEDGER.read_text())
    new = {k: v for k, v in live.items() if k not in ledger}
    stale = sorted(k for k in ledger if k not in live)
    assert not new, (
        f"divergences missing from the ledger (fix the fake or list them): {new}"
    )
    assert not stale, f"ledger entries that no longer diverge (delete them): {stale}"
    for request, entry in ledger.items():
        assert {k: live[request][k] for k in ("recorded", "fake")} == {
            k: entry[k] for k in ("recorded", "fake")
        }, request


def test_recording_covers_the_commands_the_core_session_sends() -> None:
    table = _table()
    # The 57 distinct requests of a 10-minute Core server session (MOR-3596).
    session = {
        r["line"]
        for r in map(
            json.loads, (FIXTURES / "cat_pairs.jsonl").read_text().splitlines()
        )
        if r["window"] == "server_session" and r["dir"] == "c2r"
    }
    assert len(session) == 57
    assert session <= set(table.requests())


def test_the_recording_pins_the_radio_identity_and_the_one_refusal() -> None:
    table = _table()
    assert table.lookup("ID;").reply == "ID0840;"  # type: ignore[union-attr]
    refusals = [r for r in table.requests() if table.lookup(r).kind == "refusal"]  # type: ignore[union-attr]
    assert refusals == ["RA0;"]
    assert not any(table.lookup(r).kind == "silence" for r in table.requests())  # type: ignore[union-attr]


@pytest.mark.parametrize("request_line", ["FA;", "MD0;", "TX;", "PS;", "AI;", "ID;"])
def test_state_the_fake_holds_is_seeded_from_the_recording(request_line) -> None:
    assert _table().lookup(request_line) is not None


def test_recording_pins_smeter_zero_with_main_agc_off() -> None:
    table = _table()
    assert table.lookup("GT0;").reply == "GT00;"  # type: ignore[union-attr]  # MAIN AGC OFF
    assert table.lookup("SM0;").reply == "SM0000;"  # type: ignore[union-attr]
