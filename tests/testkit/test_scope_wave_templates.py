"""Wave-template extraction over the recorded scope window.

Pins ``rigplane_testkit.icom.fixtures.scope_wave_templates`` against the
scope window of ``tests/fixtures/ic7610/civ_pairs.jsonl``: byte-for-byte
preservation and recording order per receiver, exclusion of controller
echoes/``c2r``/non-wave rows, empty and absent-receiver cases, and clear
failure on malformed/truncated qualifying wave rows. Expectations come
from the recorded rows plus the production parser/assembler; no wave
bytes are invented here.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from rigplane.commands import parse_civ_frame
from rigplane.scope import ScopeAssembler
from rigplane_testkit.icom.fixtures import scope_wave_templates

_FIXTURE = (
    Path(__file__).resolve().parent.parent / "fixtures" / "ic7610" / "civ_pairs.jsonl"
)


def _scope_rows() -> list[dict]:
    """Recorded rows of the scope window, in file order."""
    rows = [
        json.loads(line) for line in _FIXTURE.read_text().splitlines() if line.strip()
    ]
    return [row for row in rows if row["window"] == "scope"]


def _recorded_wave_frames(rows: list[dict]) -> dict[int, list[bytes]]:
    """Wave frames by raw bytes: r2c, radio-origin, 27 00, not an echo."""
    requests = {row["frame"] for row in rows if row["dir"] == "c2r"}
    out: dict[int, list[bytes]] = {}
    for row in rows:
        if row["dir"] != "r2c" or row["frame"] in requests:
            continue
        raw = bytes.fromhex(row["frame"])
        if len(raw) > 6 and raw[3] == 0x98 and raw[4:6] == b"\x27\x00":
            out.setdefault(raw[6], []).append(raw)
    return out


def test_scope_window_templates_preserve_bytes_and_order() -> None:
    rows = _scope_rows()
    got = scope_wave_templates(rows)
    assert got == _recorded_wave_frames(rows)
    # The recording holds five complete 27 00 wave frames in this window.
    assert sum(len(frames) for frames in got.values()) == 5
    # Each returned frame is complete through the production assembler.
    for receiver, frames in got.items():
        for frame in frames:
            parsed = parse_civ_frame(frame)
            assert (parsed.command, parsed.sub) == (0x27, 0x00)
            assert ScopeAssembler().feed(parsed.data[1:], receiver) is not None


def test_ignores_echo_c2r_and_non_wave_rows() -> None:
    rows = _scope_rows()
    wave = _recorded_wave_frames(rows)[0x01][0]
    receiver = parse_civ_frame(wave).data[0]
    read_13 = "fefe98e02713fd"  # recorded scope-window 27 13 bare read
    reply_13 = "fefee098271301fd"  # its recorded radio data reply
    mixed = [
        {"t": 0.0, "dir": "c2r", "frame": read_13},
        {"t": 0.001, "dir": "r2c", "frame": read_13},  # LAN echo
        {"t": 0.002, "dir": "r2c", "frame": reply_13},  # non-wave reply
        {"t": 0.003, "dir": "c2r", "frame": wave.hex()},  # client-shaped
        {"t": 0.004, "dir": "r2c", "frame": "fefee098fbfd"},  # ACK
        {"t": 0.005, "dir": "r2c", "frame": "fefee098fafd"},  # NAK
        {"t": 0.006, "dir": "r2c", "frame": wave.hex()},  # radio wave frame
    ]
    assert scope_wave_templates(mixed) == {receiver: [wave]}


def test_non_radio_source_address_ignored() -> None:
    rows = _scope_rows()
    wave = _recorded_wave_frames(rows)[0x01][0]
    foreign = bytearray(wave)
    foreign[3] = 0xE0  # same bytes, but not radio-origin
    row = {"t": 0.0, "dir": "r2c", "frame": bytes(foreign).hex()}
    assert scope_wave_templates([row]) == {}


def test_empty_input_and_absent_receiver() -> None:
    assert scope_wave_templates([]) == {}
    rows = _scope_rows()
    main_only = [
        row
        for row in rows
        if not (
            row["dir"] == "r2c" and bytes.fromhex(row["frame"])[4:7] == b"\x27\x00\x01"
        )
    ]
    got = scope_wave_templates(main_only)
    assert 0x01 not in got
    assert set(got) == {0x00}
    # No source address matches: nothing is radio-origin.
    assert scope_wave_templates(rows, radio_addr=0x00) == {}


def test_malformed_and_truncated_wave_rows_raise() -> None:
    rows = _scope_rows()
    wave = _recorded_wave_frames(rows)[0x01][0]
    bad = [
        wave[:-1],  # missing terminator: not parseable
        bytes.fromhex("fefee098270001010100fd"),  # parses, too short to complete
        bytes.fromhex("fefee0982700fd"),  # 27 00 with no receiver byte
    ]
    for frame in bad:
        with pytest.raises(ValueError):
            scope_wave_templates([{"t": 0.0, "dir": "r2c", "frame": frame.hex()}])
