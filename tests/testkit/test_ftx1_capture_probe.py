"""FTX-1 capture probe: profile-derived read list and the abort rules.

``scripts/ftx1_capture/probe_list.py`` is pure; ``probe.py`` is driven
against the existing ``FakeCatRadio`` behind a guarded ``SerialCivRecorder``
bridge, the shape the real capture uses.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

from rigplane.core.serial_open import open_serial_port
from rigplane.profiles.rig_loader import load_rig

from rigplane_testkit.serial import SerialCivRecorder
from rigplane_testkit.yaesu import CatReadOnlyGuard, FakeCatRadio

REPO = Path(__file__).resolve().parents[2]
CAPTURE_DIR = REPO / "scripts" / "ftx1_capture"
PROFILE = REPO / "rigs" / "ftx1.toml"


def _load(name: str):
    spec = importlib.util.spec_from_file_location(name, CAPTURE_DIR / f"{name}.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    sys.path.insert(0, str(CAPTURE_DIR))
    spec.loader.exec_module(module)
    return module


probe_list = _load("probe_list")
probe = _load("probe")


def test_probe_list_is_every_profile_read_expanded() -> None:
    config = load_rig(PROFILE)
    lines = [p.line for p in probe_list.build_probe_list(config)]
    assert len(lines) == len(set(lines))
    assert {"ID;", "IF;", "FA;", "MD0;", "CN00;", "CN10;", "RM1;", "RM8;"} <= set(lines)
    assert not [line for line in lines if "{" in line]


def test_every_probe_passes_the_independent_guard_and_no_write_does() -> None:
    config = load_rig(PROFILE)
    guard = CatReadOnlyGuard.from_config(config)
    assert all(
        guard.allows(p.line.encode()) for p in probe_list.build_probe_list(config)
    )
    assert not guard.allows(b"TX1;")


def test_classify() -> None:
    assert probe.classify("FA;", "FA014074000;") == "data"
    assert probe.classify("KS;", "?;") == "refusal"
    assert probe.classify("KS;", None) == "silence"
    with pytest.raises(probe.Abort):
        probe.classify("FA;", "MD02;")


async def _bridge(tmp_path, radio):
    rec = SerialCivRecorder(
        radio.device_url,
        tmp_path / "bridge.jsonl",
        baudrate=38400,
        client_filter=CatReadOnlyGuard.from_config(load_rig(PROFILE)).new_filter,
    )
    await rec.start()
    reader, writer = await open_serial_port(
        f"socket://127.0.0.1:{rec.listen_port}", baudrate=38400
    )
    return rec, reader, writer


async def test_run_probes_records_data_refusal_and_never_changes_the_radio(
    tmp_path,
) -> None:
    radio = FakeCatRadio(frequency_hz=7_074_000)
    await radio.start()
    rec, reader, writer = await _bridge(tmp_path, radio)
    try:
        probes = [
            p
            for p in probe_list.build_probe_list(load_rig(PROFILE))
            if p.line in ("ID;", "FA;", "KS;")
        ]
        results = await probe.run_probes(reader, writer, probes, pace=0.0, timeout=0.5)
    finally:
        writer.close()
        await rec.stop()
        await radio.stop()
    assert {(r["line"], r["class"]) for r in results} == {
        ("ID;", "data"),
        ("FA;", "data"),
        ("KS;", "refusal"),
    }
    assert radio.frequency_hz == 7_074_000
    assert rec.blocked_lines == 0


async def test_garbled_reply_aborts_the_run(tmp_path) -> None:
    radio = FakeCatRadio()
    await radio.start()
    radio.garble_next_reply("FA")
    rec, reader, writer = await _bridge(tmp_path, radio)
    try:
        probes = [
            p
            for p in probe_list.build_probe_list(load_rig(PROFILE))
            if p.line in ("FA;", "ID;")
        ]
        with pytest.raises(probe.Abort):
            await probe.run_probes(reader, writer, probes, pace=0.0, timeout=0.5)
    finally:
        writer.close()
        await rec.stop()
        await radio.stop()


async def test_main_probes_the_whole_profile_and_reports_an_unchanged_snapshot(
    tmp_path,
) -> None:
    import argparse

    radio = FakeCatRadio(frequency_hz=7_074_000)
    await radio.start()
    rec = SerialCivRecorder(
        radio.device_url,
        tmp_path / "bridge.jsonl",
        baudrate=38400,
        client_filter=CatReadOnlyGuard.from_config(load_rig(PROFILE)).new_filter,
    )
    await rec.start()
    out = tmp_path / "results.json"
    args = argparse.Namespace(
        device=f"socket://127.0.0.1:{rec.listen_port}",
        profile=str(PROFILE),
        baud=38400,
        pace=0.0,
        timeout=0.3,
        out=str(out),
    )
    try:
        code = await probe.main_async(args)
    finally:
        await rec.stop()
        await radio.stop()
    import json

    report = json.loads(out.read_text())
    assert code == 0
    assert report["changed"] == {}
    assert len(report["results"]) == len(probe_list.build_probe_list(load_rig(PROFILE)))
    assert rec.blocked_lines == 0


async def test_main_exits_2_when_a_snapshot_read_is_garbled(tmp_path) -> None:
    import argparse

    radio = FakeCatRadio()
    await radio.start()
    radio.garble_next_reply("IF")  # the first snapshot read
    rec = SerialCivRecorder(
        radio.device_url,
        tmp_path / "bridge.jsonl",
        baudrate=38400,
        client_filter=CatReadOnlyGuard.from_config(load_rig(PROFILE)).new_filter,
    )
    await rec.start()
    args = argparse.Namespace(
        device=f"socket://127.0.0.1:{rec.listen_port}",
        profile=str(PROFILE),
        baud=38400,
        pace=0.0,
        timeout=0.3,
        out=str(tmp_path / "results.json"),
    )
    try:
        code = await probe.main_async(args)
    finally:
        await rec.stop()
        await radio.stop()
    assert code == probe.EXIT_ABORT
    assert not (tmp_path / "results.json").exists()
