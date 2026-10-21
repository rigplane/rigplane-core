"""Yaesu CAT recording: ``;`` line reassembly and the read-only bridge guard.

``cat_lines`` is the CAT sibling of ``civ_frames`` (``test_serial_recorder``):
the same recorder JSONL, split on ``;`` instead of ``FE FE .. FD``.
``CatReadOnlyGuard`` is the RX-only capture safety: the bridge forwards only
lines the rig profile declares as reads, so a polling client can never key
the transmitter or change a setting through the recorder. The bridge test
puts the existing ``FakeCatRadio`` behind a ``SerialCivRecorder`` — one
bridge for both protocols, no second recorder.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

from rigplane.core.serial_open import open_serial_port
from rigplane.profiles.rig_loader import load_rig

from rigplane_testkit.serial import SerialCivRecorder
from rigplane_testkit.yaesu import CatReadOnlyGuard, FakeCatRadio, cat_lines

_RIGS = Path(__file__).parents[2] / "rigs"


def _write(path: Path, rows: list[dict]) -> Path:
    path.write_text("".join(json.dumps(r) + "\n" for r in rows))
    return path


def _row(t: float, direction: str, text: str) -> dict:
    return {"t": t, "dir": direction, "port": "serial", "hex": text.encode().hex()}


def test_cat_lines_reassembles_split_and_joined_chunks(tmp_path) -> None:
    out = _write(
        tmp_path / "cat.jsonl",
        [
            # One line split over two c2r chunks, radio traffic in between.
            _row(0.1, "c2r", "F"),
            _row(0.2, "r2c", "ID0840;"),
            _row(0.3, "c2r", "A;"),
            # Two complete lines in one r2c chunk, the second a refusal.
            _row(0.4, "r2c", "FA014074000;?;"),
            # Partial line at the end of the recording: never yielded.
            _row(0.5, "c2r", "MD"),
        ],
    )
    assert list(cat_lines(out)) == [
        (0.2, "r2c", "ID0840;"),
        (0.3, "c2r", "FA;"),
        (0.4, "r2c", "FA014074000;"),
        (0.4, "r2c", "?;"),
    ]


def test_cat_lines_keeps_directions_apart(tmp_path) -> None:
    out = _write(
        tmp_path / "cat.jsonl",
        [
            _row(0.1, "c2r", "I"),
            _row(0.2, "r2c", "P"),
            _row(0.3, "c2r", "F;"),
            _row(0.4, "r2c", "S1;"),
        ],
    )
    assert list(cat_lines(out)) == [(0.3, "c2r", "IF;"), (0.4, "r2c", "PS1;")]


def test_cat_lines_skips_the_bridge_blocked_rows(tmp_path) -> None:
    out = _write(
        tmp_path / "cat.jsonl",
        [
            _row(0.1, "c2r", "FA;"),
            _row(0.2, "blocked", "TX1;"),
            _row(0.3, "r2c", "FA014074000;"),
        ],
    )
    assert list(cat_lines(out)) == [
        (0.1, "c2r", "FA;"),
        (0.3, "r2c", "FA014074000;"),
    ]


@pytest.fixture(scope="module")
def guard_factory():
    """A new per-connection line filter of the FTX-1 profile's guard."""
    return CatReadOnlyGuard.from_config(load_rig(_RIGS / "ftx1.toml")).new_filter


@pytest.mark.parametrize(
    "line", ["ID;", "IF;", "FA;", "FB;", "MD0;", "MD1;", "TX;", "PS;", "SM0;", "RM1;"]
)
def test_guard_forwards_profile_reads(guard_factory, line) -> None:
    forward, blocked = guard_factory().feed(line.encode())
    assert (forward, blocked) == (line.encode(), [])


@pytest.mark.parametrize(
    "line",
    [
        "TX1;",  # key the transmitter
        "TX0;",
        "FA014074000;",  # tune
        "MD02;",  # mode change
        "AI0;",  # a setting write
        "AB;",  # VFO copy: a no-argument write
        "SV;",
        "RM11;",  # a read selector with one character too many
        "OS01;",  # write that shares its prefix with the OS read
        "XX;",  # unknown verb
        ";",
    ],
)
def test_guard_blocks_everything_that_is_not_a_profile_read(
    guard_factory, line
) -> None:
    forward, blocked = guard_factory().feed(line.encode())
    assert (forward, blocked) == (b"", [line.encode()])


def test_guard_handles_split_joined_and_partial_lines(guard_factory) -> None:
    guard = guard_factory()
    assert guard.feed(b"F") == (b"", [])  # partial line held back
    assert guard.feed(b"A;ID;TX") == (b"FA;ID;", [])
    assert guard.feed(b"1;PS;") == (b"PS;", [b"TX1;"])


def test_guard_blocks_unterminated_garbage_instead_of_buffering_forever(
    guard_factory,
) -> None:
    guard = guard_factory()
    forward, blocked = guard.feed(b"A" * 200)
    assert forward == b""
    assert blocked == [b"A" * 200]
    assert guard.feed(b"FA;") == (b"FA;", [])  # the stream recovers


def test_guard_filters_are_per_connection(guard_factory) -> None:
    first, second = guard_factory(), guard_factory()
    first.feed(b"F")
    assert second.feed(b"A;") == (b"", [b"A;"])  # no leftover from `first`


async def test_bridge_never_forwards_a_write_and_logs_it(tmp_path) -> None:
    radio = FakeCatRadio(frequency_hz=7_074_000)
    await radio.start()
    out = tmp_path / "cat.jsonl"
    rec = SerialCivRecorder(
        radio.device_url,
        out,
        baudrate=38400,
        client_filter=CatReadOnlyGuard.from_config(
            load_rig(_RIGS / "ftx1.toml")
        ).new_filter,
    )
    await rec.start()
    reader, writer = await open_serial_port(
        f"socket://127.0.0.1:{rec.listen_port}", baudrate=38400
    )
    try:
        writer.write(b"ID;FA014074000;TX1;FA;")
        await writer.drain()
        answers = [
            await asyncio.wait_for(reader.readuntil(b";"), timeout=2.0)
            for _ in range(2)
        ]
    finally:
        writer.close()
        await rec.stop()
        await radio.stop()

    assert answers == [b"ID0840;", b"FA007074000;"]  # tune and TX never applied
    assert radio.commands == ("ID;", "FA;")
    assert not radio.ptt
    assert rec.blocked_lines == 2
    rows = [json.loads(line) for line in out.read_text().splitlines()]
    assert [bytes.fromhex(r["hex"]) for r in rows if r["dir"] == "blocked"] == [
        b"FA014074000;",
        b"TX1;",
    ]
    lines = [(d, text) for _, d, text in cat_lines(out)]
    # Per direction: the interleaving of the two directions is timing.
    assert [t for d, t in lines if d == "c2r"] == ["ID;", "FA;"]
    assert [t for d, t in lines if d == "r2c"] == ["ID0840;", "FA007074000;"]
