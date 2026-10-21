"""IC-7300 conformance: the serial fake against the recorded bench radio.

``tests/fixtures/ic7300/`` holds what the real bench IC-7300 answered over
its USB CI-V port (2026-10-21, 19200 baud, read-only; README there has the
provenance). These tests pin the recorded wire facts and measure
``CivSerialFake`` with the recorded answer table mounted
(``tests/testkit/ic7300_fakes.py: ic7300_usb_fake``) against them, classifying
each request's reply from the wire (nak / ack / data with receiver, command,
sub and payload length), never comparing live state values.

Known divergences live in ``conformance_ledger.json`` next to the fixtures,
keyed ``usb:<request hex>``; the ratchet is the IC-7610 one
(``test_ic7610_conformance._assert_ledger_exact``): an observed divergence
missing from the ledger fails, and so does a ledger entry no longer observed.
"""

from __future__ import annotations

import asyncio
import importlib.util
import json
import statistics
import sys
import time
from pathlib import Path

import pytest

from rigplane.commands import parse_civ_frame
from rigplane.profiles import resolve_radio_profile
from rigplane.profiles.rig_loader import discover_rigs

from testkit.ic7300_fakes import FIXTURES, ic7300_answer_table, ic7300_usb_fake
from testkit.test_ic7610_conformance import (
    _assert_ledger_exact,
    _ask_serial_fake,
    _describe,
    _recorded_class,
)

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
RADIO_ADDR = 0x94
WINDOWS = (
    "probe",
    "server_poll",
)  # probe: allow-list GETs; server_poll: Core server polling

# Probes the real IC-7300 NAKed (probe_answers.json), by cause:
#   * bare scope reads: Core sends these with a selector byte, so the NAK is a
#     probe artefact, not a profile defect (27 00 is the wave stream itself);
#   * cmd29: the IC-7300 has no Command29 wrapper (single receiver);
#   * unknown: the deliberately unknown requests, NAKed as designed;
#   * get_bsr / get_tx_band_edge: declared by rigs/ic7300.toml, refused by the
#     radio in the bare form probed (needs its own capture to resolve).
_RECORDED_NAKS = frozenset(
    {
        "get_scope_edge", "get_scope_fixed_edge", "get_scope_hold", "get_scope_mode",
        "get_scope_ref", "get_scope_span", "get_scope_speed", "get_scope_vbw",
        "get_scope_wave",
        "cmd29:get_freq:0", "cmd29:get_freq:1",
        "unknown:1a050999", "unknown:7f", "unknown:147e", "unknown:277f",
        "get_bsr", "get_tx_band_edge",
    }
)  # fmt: skip


def _load_probe_list():
    path = REPO_ROOT / "scripts" / "ic7610_capture" / "probe_list.py"
    spec = importlib.util.spec_from_file_location("ic7300_conf_probe_list", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _rows() -> list[dict]:
    return [
        json.loads(line)
        for line in (FIXTURES / "civ_pairs.jsonl").read_text().splitlines()
        if line
    ]


def _ledger() -> dict:
    return json.loads((FIXTURES / "conformance_ledger.json").read_text())


def _pairs(windows: tuple[str, ...] = WINDOWS) -> list[tuple[bytes, bytes | None]]:
    """First-occurrence (request, closing reply) pairs, file order, no echo."""
    rows = [r for r in _rows() if r["window"] in windows]
    pairs: list[tuple[bytes, bytes | None]] = []
    seen: set[bytes] = set()
    i = 0
    while i < len(rows):
        if rows[i]["dir"] != "c2r":
            i += 1
            continue
        request = bytes.fromhex(rows[i]["frame"])
        i += 1
        reply = None
        if i < len(rows) and rows[i]["dir"] == "r2c":
            reply = bytes.fromhex(rows[i]["frame"])
            i += 1
        if request not in seen:
            seen.add(request)
            pairs.append((request, reply))
    return pairs


_NAMED_IDS = frozenset({"latency", "baud_gate"})


def _evidence_pairs() -> list[tuple[bytes, bytes | None]]:
    """The scope-enable attempt at 19200 baud (evidence.json), as pairs."""
    return [
        (bytes.fromhex(p["request"]), bytes.fromhex(p["reply"]))
        for p in _evidence()["scope_enable_at_19200"]["pairs"]
    ]


def _is_usb_key(key: str) -> bool:
    return key.startswith("usb:")


def _is_named_key(key: str) -> bool:
    return key in _NAMED_IDS


def _evidence() -> dict:
    return json.loads((FIXTURES / "evidence.json").read_text())


# ---------------------------------------------------------------------------
# Recorded facts
# ---------------------------------------------------------------------------


def test_fixture_framing_addressing_and_no_echo() -> None:
    rows = _rows()
    assert {row["window"] for row in rows} == set(WINDOWS)
    for row in rows:
        assert set(row) == {"window", "transport", "t", "dir", "frame"}
        assert row["transport"] == "usb"
        frame = bytes.fromhex(row["frame"])
        assert frame[:2] == b"\xfe\xfe" and frame[-1] == 0xFD
        to_addr, from_addr = (0x94, 0xE0) if row["dir"] == "c2r" else (0xE0, 0x94)
        assert (frame[2], frame[3]) == (to_addr, from_addr)
    requests = {row["frame"] for row in rows if row["dir"] == "c2r"}
    # The recorded USB CI-V port does not echo: no radio frame equals a request.
    assert all(row["frame"] not in requests for row in rows if row["dir"] == "r2c")
    table = ic7300_answer_table()
    assert table.echo is False
    assert ic7300_usb_fake().echo is False


def test_recorded_request_set_is_the_profile_probe_list() -> None:
    probe_list = _load_probe_list()
    profile = discover_rigs(REPO_ROOT / "rigs")["IC-7300"].to_profile()
    expected = {
        bytes(probe.frame_body) for probe in probe_list.build_probe_list(profile)
    }
    recorded = {request[4:-1] for request, _ in _pairs(("probe",))}
    assert recorded == expected, (
        "fixture drifted from rigs/ic7300.toml: "
        f"only recorded {sorted(h.hex() for h in recorded - expected)}, "
        f"only in profile {sorted(h.hex() for h in expected - recorded)}"
    )


def test_pairs_agree_with_probe_answers_and_naks_are_pinned() -> None:
    answers = json.loads((FIXTURES / "probe_answers.json").read_text())
    by_frame = {a["frame_hex"]: a for a in answers["probes"]}
    for request, reply in _pairs(("probe",)):
        entry = by_frame[request.hex()]
        assert reply is not None
        assert (entry["class"] == "nak") == (parse_civ_frame(reply).command == 0xFA), (
            request.hex()
        )
    naked = {a["name"] for a in answers["probes"] if a["class"] == "nak"}
    assert naked == _RECORDED_NAKS
    assert answers["summary"] == {
        "data": 151, "ack": 0, "nak": 17, "silence": 0, "error": 0,
    }  # fmt: skip


async def test_identity_answer_is_the_profile_id() -> None:
    profile = resolve_radio_profile(model="IC-7300")
    reply = dict(_pairs())[bytes.fromhex("fefe94e01900fd")]
    assert reply is not None
    assert parse_civ_frame(reply).data == bytes.fromhex(
        profile.expected_identity_ids[0]
    )
    fake = ic7300_usb_fake()
    reader, writer = await fake.opener(url="socket://conformance", baudrate=19200)
    try:
        writer.write(bytes.fromhex("fefe94e01900fd"))
        await writer.drain()
        sim_class, _ = await _ask_serial_fake(reader, b"")
    finally:
        writer.close()
    assert sim_class == _recorded_class(reply)


# ---------------------------------------------------------------------------
# Replay and ratchet
# ---------------------------------------------------------------------------


async def _collect_usb_divergences(table_rows=None) -> dict[str, tuple[str, str]]:
    fake = ic7300_usb_fake()
    reader, writer = await fake.opener(url="socket://conformance", baudrate=19200)
    observed: dict[str, tuple[str, str]] = {}
    try:
        for request, reply in [*_pairs(), *_evidence_pairs()]:
            writer.write(request)
            await writer.drain()
            sim_class, _ = await _ask_serial_fake(reader, request)
            recorded_class = _recorded_class(reply)
            if sim_class != recorded_class:
                observed["usb:" + request.hex()] = (
                    _describe(recorded_class),
                    _describe(sim_class),
                )
    finally:
        writer.close()
    return observed


async def test_usb_replay_divergences_match_ledger() -> None:
    _assert_ledger_exact(
        await _collect_usb_divergences(), _is_usb_key, ledger=_ledger()
    )


async def _collect_structural_divergences() -> dict[str, tuple[str, str]]:
    observed: dict[str, tuple[str, str]] = {}
    answers = json.loads((FIXTURES / "probe_answers.json").read_text())
    lat = sorted(a["latency_ms"] for a in answers["probes"] if a["class"] == "data")
    recorded_latency = (
        f"median {statistics.median(lat)} ms (min {lat[0]}, max {lat[-1]})"
    )

    fake = ic7300_usb_fake()
    reader, writer = await fake.opener(url="socket://conformance", baudrate=115200)
    try:
        timings = []
        for request, _ in _pairs(("probe",))[:25]:
            started = time.monotonic()
            writer.write(request)
            await writer.drain()
            await _ask_serial_fake(reader, request)
            timings.append((time.monotonic() - started) * 1000)
        sim_median = statistics.median(timings)
        if sim_median < 0.5 * lat[0]:
            observed["latency"] = (
                recorded_latency,
                "answers instantly (no reply delay)",
            )
    finally:
        writer.close()

    baud = _evidence()["baud"]
    try:
        reader2, writer2 = await ic7300_usb_fake().opener(
            url="socket://conformance", baudrate=baud["silent_at"][0]
        )
        writer2.close()
        observed["baud_gate"] = (
            f"answers only at {baud['answers_at'][0]} baud, silent at "
            + ", ".join(str(b) for b in baud["silent_at"]),
            "answers at any baud",
        )
    except OSError:
        pass
    return observed


async def test_structural_divergences_match_ledger() -> None:
    _assert_ledger_exact(
        await _collect_structural_divergences(), _is_named_key, ledger=_ledger()
    )


def test_every_ledger_key_has_exactly_one_owner_and_a_ticket() -> None:
    for key, value in _ledger().items():
        assert _is_usb_key(key) != _is_named_key(key), key
        assert set(value) == {"recorded", "simulator", "ticket"}


async def test_ratchet_fails_when_a_ledger_entry_is_deleted() -> None:
    ledger = _ledger()
    ledger.pop("latency")
    with pytest.raises(AssertionError, match="missing from the ledger"):
        _assert_ledger_exact(
            await _collect_structural_divergences(), _is_named_key, ledger=ledger
        )


async def test_ratchet_fails_on_a_bogus_ledger_entry() -> None:
    ledger = _ledger()
    ledger["usb:fefe94e07ffd"] = {
        "recorded": "nak", "simulator": "silence", "ticket": "MOR-3597",
    }  # fmt: skip
    with pytest.raises(AssertionError):
        _assert_ledger_exact(
            await _collect_usb_divergences(), _is_usb_key, ledger=ledger
        )
