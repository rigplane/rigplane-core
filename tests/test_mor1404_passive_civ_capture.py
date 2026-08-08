"""MOR-1404 passive raw CI-V capture and deterministic replay gates."""

from __future__ import annotations

import ast
import json
from collections.abc import Iterator
from pathlib import Path
from types import SimpleNamespace

import pytest

from support.mor1404_passive_civ_capture import (
    CAPTURE_SCHEMA,
    PassiveCivCapture,
    install_capture,
    replay_capture,
)


def _capture(
    path: Path,
    frames: tuple[str, ...],
    *,
    wall: tuple[int, ...] | None = None,
    monotonic: tuple[int, ...] | None = None,
) -> Path:
    wall = wall or tuple(range(1, len(frames) + 1))
    monotonic = monotonic or tuple(range(10, 10 * len(frames) + 1, 10))
    sink = PassiveCivCapture(
        path,
        wall_time_ns=iter(wall).__next__,
        monotonic_ns=iter(monotonic).__next__,
    )
    for frame in frames:
        sink.record(bytes.fromhex(frame))
    sink.close()
    return path


@pytest.fixture
def selector_capture(tmp_path: Path) -> Path:
    return _capture(
        tmp_path / "selector.jsonl",
        (
            "FE FE 00 94 00 00 40 42 14 00 FD",
            "FE FE 00 94 07 01 FD",
            "FE FE E0 94 25 00 00 40 42 14 00 FD",
        ),
        wall=(10, 20, 30),
        monotonic=(100, 200, 350),
    )


def test_capture_and_replay_preserve_exact_selector_evidence(
    selector_capture: Path,
) -> None:
    rows = [json.loads(line) for line in selector_capture.read_text().splitlines()]
    assert rows[0] == {"kind": "session", "schema": CAPTURE_SCHEMA}
    assert [row["seq"] for row in rows[1:]] == [0, 1, 2]
    assert [row["wall_time_ns"] for row in rows[1:]] == [10, 20, 30]
    assert [row["monotonic_ns"] for row in rows[1:]] == [100, 200, 350]

    replay = replay_capture(selector_capture)
    assert replay["verdict"] == "selector_07_present"
    assert replay["selector_07_present"] is True
    evidence = replay["selector_07_frames"][0]
    assert evidence["frame_hex"] == "fefe00940701fd"
    assert evidence["data_hex"] == "01"
    assert evidence["delta_ns"] == 100
    assert (evidence["from_addr"], evidence["to_addr"]) == ("94", "00")
    assert [row["frame_hex"] for row in replay["timeline"]] == [
        row["frame_hex"] for row in rows[1:]
    ]
    with pytest.raises(FileExistsError):
        PassiveCivCapture(selector_capture)


def test_frequency_frames_cannot_invent_selector_identity(tmp_path: Path) -> None:
    path = _capture(
        tmp_path / "frequency-only.jsonl",
        (
            "FE FE 00 94 00 00 40 42 14 00 FD",
            "FE FE E0 94 25 00 00 40 42 14 00 FD",
        ),
    )
    replay = replay_capture(path)
    assert replay["verdict"] == "selector_07_absent"
    assert replay["command_07_frames"] == replay["selector_07_frames"] == []
    assert "frequency" not in json.dumps(replay).lower()


@pytest.mark.parametrize(
    ("raw", "data"),
    [
        ("FE FE E0 94 07 D1 01 FD", "d101"),
        ("FE FE 94 E0 07 01 FD", "01"),
    ],
)
def test_non_selector_or_echoed_07_stays_blocked(
    tmp_path: Path, raw: str, data: str
) -> None:
    replay = replay_capture(_capture(tmp_path / "other-07.jsonl", (raw,)))
    assert replay["command_07_present"] is True
    assert replay["selector_07_present"] is False
    assert replay["command_07_frames"][0]["data_hex"] == data


@pytest.mark.parametrize(
    "mutation",
    [
        lambda rows: rows.__setitem__(2, {**rows[2], "seq": 7}),
        lambda rows: rows.__setitem__(2, {**rows[2], "monotonic_ns": 50}),
        lambda rows: rows.__setitem__(2, {**rows[2], "frame_hex": "00"}),
    ],
)
def test_replay_rejects_corrupt_order_or_bytes(
    selector_capture: Path, mutation
) -> None:
    rows = [json.loads(line) for line in selector_capture.read_text().splitlines()]
    mutation(rows)
    selector_capture.write_text("\n".join(json.dumps(row) for row in rows) + "\n")
    with pytest.raises(ValueError):
        replay_capture(selector_capture)


def test_installation_tees_inbound_iterator_without_changing_bytes(
    tmp_path: Path,
) -> None:
    frames = [bytes.fromhex("FE FE 00 94 07 00 FD"), bytes.fromhex("FE FE E0 94 FB FD")]

    def original(payload: bytes) -> Iterator[bytes]:
        assert payload == b"payload"
        yield from frames

    module = SimpleNamespace(iter_civ_frames=original)
    installation = install_capture(tmp_path / "capture.jsonl", civ_rx_module=module)
    assert list(module.iter_civ_frames(b"payload")) == frames
    installation.close()
    assert module.iter_civ_frames is original


def test_capture_and_sitecustomize_have_no_radio_write_surface() -> None:
    forbidden_imports = {"serial", "serial_asyncio"}
    forbidden_calls = {
        "send",
        "send_civ",
        "send_civ_raw_fire_and_forget",
        "write_packet",
        "set_dtr",
        "set_rts",
        "poll",
        "select",
    }
    for path in (
        Path("tests/support/mor1404_passive_civ_capture.py"),
        Path("tests/support/mor1404_sitecustomize/sitecustomize.py"),
    ):
        tree = ast.parse(path.read_text(), filename=str(path))
        imports = {
            alias.name.split(".", 1)[0]
            for node in ast.walk(tree)
            if isinstance(node, (ast.Import, ast.ImportFrom))
            for alias in node.names
        }
        calls = {
            node.func.attr
            for node in ast.walk(tree)
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
        }
        assert imports.isdisjoint(forbidden_imports)
        assert calls.isdisjoint(forbidden_calls)
