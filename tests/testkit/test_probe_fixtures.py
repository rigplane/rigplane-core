"""Probe captures as regression fixtures (MOR-4464 PR C).

Every customer capture becomes a regression test: the probe file converts
to ``civ_pairs``/``cat_pairs`` fixture rows, the rows mount as recorded
answer tables, and the real ``rigplane probe`` CLI runs against them.
"""

from __future__ import annotations

import asyncio
import json
from collections import Counter
from pathlib import Path

import pytest

from rigplane.probe.model import (
    CommandRow,
    ProbeFile,
    RadioInfo,
    IdentityReport,
    SkippedRead,
    Snapshot,
    TerminationInfo,
    ToolInfo,
)
from rigplane_testkit.probe_fixtures import probe_to_cat_pairs, probe_to_civ_pairs

REPO_ROOT = Path(__file__).resolve().parents[2]
SAMPLE = REPO_ROOT / "tests" / "fixtures" / "g7250" / "probe_usb_20261114.json"

CIV_ADDR = 0xA0


def _tool() -> ToolInfo:
    return ToolInfo(
        rigplane_version="test", python="test", os="test", created_utc="test"
    )


def _radio(protocol: str) -> RadioInfo:
    return RadioInfo(
        profile="TEST",
        profile_sha256="0" * 64,
        protocol=protocol,
        port_kind="usb",
        baud=115200,
        identity_reported=IdentityReport(raw="", parsed=""),
        identity_matches_profile=False,
    )


def _civ_row(
    name: str,
    outcome: str,
    request_hex: str,
    reply_hex: str | None,
    *,
    latency_ms: float = 10.0,
) -> CommandRow:
    return CommandRow(
        name=name,
        template=request_hex,
        request_hex=request_hex,
        request_text="",
        reply_hex=reply_hex,
        reply_text=None,
        outcome=outcome,
        latency_ms=latency_ms,
        expected="present",
    )


def _cat_row(name: str, outcome: str, line: str, reply: str | None) -> CommandRow:
    return CommandRow(
        name=name,
        template=line,
        request_hex=line.encode("ascii").hex(),
        request_text=line,
        reply_hex=reply.encode("ascii").hex() if reply is not None else None,
        reply_text=reply,
        outcome=outcome,
        latency_ms=10.0,
        expected="present",
    )


def _file(
    protocol: str, commands: list[CommandRow], skipped: list[SkippedRead] | None = None
) -> ProbeFile:
    return ProbeFile(
        tool=_tool(),
        mode="read",
        radio=_radio(protocol),
        private_reads_included=False,
        label="",
        commands=tuple(commands),
        skipped=tuple(skipped or []),
        snapshot=Snapshot(before={}, after={}, changed=[]),
        termination=TerminationInfo(status="completed"),
    )


# --- Unit: CI-V outcome -> rows -------------------------------------------


def test_civ_data_gives_request_plus_reply_pair() -> None:
    probe = _file(
        "civ", [_civ_row("get_af_level", "data", "1401", "fefee0a014010000fd")]
    )
    assert probe_to_civ_pairs(probe, radio_addr=CIV_ADDR, window="w") == [
        {
            "dir": "c2r",
            "frame": "fefea0e01401fd",
            "t": 0.0,
            "transport": "usb",
            "window": "w",
        },
        {
            "dir": "r2c",
            "frame": "fefee0a014010000fd",
            "t": 0.01,
            "transport": "usb",
            "window": "w",
        },
    ]


def test_civ_refused_and_ack_only_give_pairs() -> None:
    probe = _file(
        "civ",
        [
            _civ_row("get_break_in", "refused", "1647", "fefee0a0fafd"),
            _civ_row("set_freq", "ack_only", "05", "fefee0a0fbfd"),
        ],
    )
    rows = probe_to_civ_pairs(probe, radio_addr=CIV_ADDR, window="w")
    assert [row["dir"] for row in rows] == ["c2r", "r2c", "c2r", "r2c"]
    assert rows[1]["frame"] == "fefee0a0fafd"
    assert rows[3]["frame"] == "fefee0a0fbfd"


def test_civ_silent_gives_request_row_alone() -> None:
    probe = _file("civ", [_civ_row("unknown:7f", "silent", "7f", None)])
    assert probe_to_civ_pairs(probe, radio_addr=CIV_ADDR, window="w") == [
        {
            "dir": "c2r",
            "frame": "fefea0e07ffd",
            "t": 0.0,
            "transport": "usb",
            "window": "w",
        }
    ]


@pytest.mark.parametrize("outcome", ["malformed", "unsolicited", "transport_error"])
def test_civ_unpaired_outcomes_emit_no_rows(outcome: str) -> None:
    probe = _file("civ", [_civ_row("odd", outcome, "1401", "fe00")])
    assert probe_to_civ_pairs(probe, radio_addr=CIV_ADDR, window="w") == []


def test_civ_skipped_reads_are_never_emitted() -> None:
    probe = _file(
        "civ",
        [_civ_row("get_freq", "data", "03", "fefee0a0030000fd")],
        skipped=[
            SkippedRead(
                "get_memory_contents", None, "private", "needs --include-private"
            )
        ],
    )
    rows = probe_to_civ_pairs(probe, radio_addr=CIV_ADDR, window="w")
    assert len(rows) == 2


def test_civ_first_occurrence_wins() -> None:
    probe = _file(
        "civ",
        [
            _civ_row("get_bsr", "refused", "1a01", "fefee0a0fafd"),
            _civ_row("get_band_spectrum_display", "refused", "1a01", "fefee0a0fafd"),
        ],
    )
    rows = probe_to_civ_pairs(probe, radio_addr=CIV_ADDR, window="w")
    assert [row["dir"] for row in rows] == ["c2r", "r2c"]


# --- Unit: CAT outcome -> rows --------------------------------------------


def test_cat_data_and_refused_give_pairs_silent_gives_request() -> None:
    probe = _file(
        "yaesu_cat",
        [
            _cat_row("get_freq", "data", "FA;", "FA14074000;"),
            _cat_row("get_keyer_speed", "refused", "KS;", "?;"),
            _cat_row("get_key_pitch", "silent", "KP;", None),
        ],
    )
    assert probe_to_cat_pairs(probe, window="w") == [
        {"dir": "c2r", "line": "FA;", "t": 0.0, "transport": "usb", "window": "w"},
        {
            "dir": "r2c",
            "line": "FA14074000;",
            "t": 0.01,
            "transport": "usb",
            "window": "w",
        },
        {"dir": "c2r", "line": "KS;", "t": 0.01, "transport": "usb", "window": "w"},
        {"dir": "r2c", "line": "?;", "t": 0.02, "transport": "usb", "window": "w"},
        {"dir": "c2r", "line": "KP;", "t": 0.02, "transport": "usb", "window": "w"},
    ]


@pytest.mark.parametrize("outcome", ["malformed", "unsolicited", "transport_error"])
def test_cat_unpaired_outcomes_emit_no_rows(outcome: str) -> None:
    probe = _file("yaesu_cat", [_cat_row("odd", outcome, "FA;", "FA14074000;")])
    assert probe_to_cat_pairs(probe, window="w") == []


def test_cat_first_occurrence_wins() -> None:
    probe = _file(
        "yaesu_cat",
        [
            _cat_row("get_freq", "data", "FA;", "FA14074000;"),
            _cat_row("get_freq", "data", "FA;", "FA14075000;"),
        ],
    )
    rows = probe_to_cat_pairs(probe, window="w")
    assert [row["line"] for row in rows] == ["FA;", "FA14074000;"]


def test_cat_trailing_silence_replays_as_silence() -> None:
    from rigplane_testkit.yaesu.replay import RecordedCatAnswerTable

    probe = _file(
        "yaesu_cat",
        [
            _cat_row("get_freq", "data", "FA;", "FA14074000;"),
            _cat_row("get_key_pitch", "silent", "KP;", None),
        ],
    )
    rows = probe_to_cat_pairs(probe, window="w")
    assert [row["dir"] for row in rows] == ["c2r", "r2c", "c2r"]
    table = RecordedCatAnswerTable.from_cat_pairs(rows, window="w")
    data = table.lookup("FA;")
    assert data is not None and data.kind == "data" and data.reply == "FA14074000;"
    silent = table.lookup("KP;")
    assert silent is not None and silent.kind == "silence" and silent.reply is None


def test_cat_mid_plan_silence_replays_as_silence() -> None:
    from rigplane_testkit.yaesu.replay import RecordedCatAnswerTable

    probe = _file(
        "yaesu_cat",
        [
            _cat_row("get_freq", "data", "FA;", "FA14074000;"),
            _cat_row("get_key_pitch", "silent", "KP;", None),
            _cat_row("get_mode", "data", "MD0;", "MD02;"),
        ],
    )
    rows = probe_to_cat_pairs(probe, window="w")
    table = RecordedCatAnswerTable.from_cat_pairs(rows, window="w")
    silent = table.lookup("KP;")
    assert silent is not None and silent.kind == "silence"
    mode = table.lookup("MD0;")
    assert mode is not None and mode.kind == "data" and mode.reply == "MD02;"


# --- The committed G7250 sample -------------------------------------------


def _sample() -> ProbeFile:
    return ProbeFile.from_json(SAMPLE.read_text(encoding="utf-8"))


def test_sample_parses_with_expected_shape() -> None:
    probe = _sample()
    assert probe.schema == "rigplane-probe-v1"
    assert probe.radio.profile == "G7250"
    assert probe.radio.protocol == "civ"
    assert len(probe.commands) == 46
    assert Counter(row.outcome for row in probe.commands) == Counter(
        {"data": 33, "refused": 11, "silent": 2}
    )
    # The file predates the termination key: old files parse as completed.
    assert probe.termination.status == "completed"


def test_sample_redaction_gate_is_clean() -> None:
    from rigplane_testkit.redaction_gate import scan

    assert scan([str(SAMPLE)]) == []


def _request_frame(body_hex: str) -> bytes:
    from rigplane.commands import CONTROLLER_ADDR, build_civ_frame, command_carries_sub

    body = bytes.fromhex(body_hex)
    command, rest = body[0], body[1:]
    if command_carries_sub(command) and rest:
        sub, data = rest[0], rest[1:]
    else:
        sub, data = None, rest
    return build_civ_frame(
        CIV_ADDR, CONTROLLER_ADDR, command, sub=sub, data=data or None
    )


def test_sample_converts_to_answer_table() -> None:
    from rigplane_testkit.icom.replay import RecordedAnswerTable

    probe = _sample()
    rows = probe_to_civ_pairs(probe, radio_addr=CIV_ADDR, window="probe")
    assert rows
    assert probe_to_civ_pairs(probe, radio_addr=CIV_ADDR, window="probe") == rows
    table = RecordedAnswerTable.from_civ_pairs(rows, "usb")
    for row in probe.commands:
        answer = table.answer(_request_frame(row.request_hex))
        if row.outcome in ("data", "ack_only"):
            assert row.reply_hex is not None
            assert answer == bytes.fromhex(row.reply_hex), row.name
        elif row.outcome == "refused":
            assert row.reply_hex is not None
            assert answer == bytes.fromhex(row.reply_hex), row.name
            assert answer is not None and answer[4] == 0xFA, row.name
        elif row.outcome == "silent":
            assert answer is None, row.name
        else:  # pragma: no cover - the sample holds only these classes
            raise AssertionError(f"unexpected outcome {row.outcome!r}")


def _identity_model_id(probe: ProbeFile) -> bytes:
    """The model-id bytes the sample's ``19 00`` identity read reported."""
    for row in probe.commands:
        if row.request_hex == "1900" and row.reply_hex is not None:
            raw = bytes.fromhex(row.reply_hex)
            body, reply_body = bytes.fromhex(row.request_hex), raw[4:-1]
            assert reply_body.startswith(body)
            return reply_body[len(body) :]
    raise AssertionError("sample has no 19 00 identity row")


#: Reads the 2026-11-14 capture predates: declared-absent borrows
#: (``1e00``/``1e01``) the current plan asks and the capture never sent.
#: The replay answers them with the fake's NAK fallback (unrecorded),
#: so they compare as ``refused`` and are absent from the replay's
#: skips. A future plan change fails here on purpose: extend this set
#: consciously, never loosen the comparison.
EXTRA_SINCE_CAPTURE = ("get_tx_band_count", "get_tx_band_edge")


def test_civ_round_trip_through_real_cli(tmp_path, monkeypatch) -> None:
    """The customer capture replays as a regression test (CI-V).

    The converted sample mounts as recorded answers on a G7250 fake;
    the real ``rigplane probe`` CLI runs against it; every command the
    sample sent comes back with the same class, and every skip the
    current plan still withholds comes back with the same reason.
    """
    import socket

    import rigplane.cli._probe as probe_cli
    from rigplane.cli import main
    from rigplane.core.serial_open import open_serial_port as real_open
    from rigplane_testkit.icom.replay import RecordedAnswerTable
    from rigplane_testkit.serial import CivSerialFake

    probe = _sample()
    table = RecordedAnswerTable.from_civ_pairs(
        probe_to_civ_pairs(probe, radio_addr=CIV_ADDR, window="probe"), "usb"
    )
    fake = CivSerialFake(
        model="G7250",
        civ_addr=CIV_ADDR,
        model_id=_identity_model_id(probe),
        recorded_answers=table,
    )
    # civ_pairs rows cannot encode silence (a missing reply row is also
    # "never recorded"), so the sample's silence facts ride along as the
    # fake's scripted one-shot answer loss, derived from the sample itself.
    for row in probe.commands:
        if row.outcome == "silent":
            fake.lose_next_answer(bytes.fromhex(row.request_hex)[0])

    async def fake_open(device: str, baudrate: int | None = None, **kwargs):
        return await real_open(device, baudrate=baudrate, opener=fake.opener)

    monkeypatch.setattr(probe_cli, "open_serial_port", fake_open)

    def _refuse_network(*args, **kwargs):
        raise AssertionError("probe must not open network connections here")

    monkeypatch.setattr(socket, "create_connection", _refuse_network)
    out = tmp_path / "g7250-replay.json"
    monkeypatch.setattr(
        "sys.argv",
        [
            "rigplane",
            "probe",
            "G7250",
            "--port",
            "fake-usb-serial",
            "--yes",
            "--out",
            str(out),
            "--label",
            "replay-civ",
        ],
    )
    with pytest.raises(SystemExit) as exc_info:
        main()
    assert exc_info.value.code == 0
    replayed = ProbeFile.from_json(out.read_text(encoding="utf-8"))
    replayed_names = [row.name for row in replayed.commands]
    sample_names = [row.name for row in probe.commands]
    assert [name for name in replayed_names if name not in EXTRA_SINCE_CAPTURE] == (
        sample_names
    )
    assert {name for name in replayed_names if name not in sample_names} == set(
        EXTRA_SINCE_CAPTURE
    )
    replayed_by_name = {row.name: row.outcome for row in replayed.commands}
    for row in probe.commands:
        assert replayed_by_name[row.name] == row.outcome, row.name
    assert {replayed_by_name[name] for name in EXTRA_SINCE_CAPTURE} == {"refused"}
    assert [(row.name, row.reason) for row in replayed.skipped] == [
        (row.name, row.reason)
        for row in probe.skipped
        if row.name not in EXTRA_SINCE_CAPTURE
    ]
    assert replayed.termination.status == "completed"


async def _run_cat_probe(monkeypatch, port: str, out, label: str) -> ProbeFile:
    from rigplane.cli import main

    monkeypatch.setattr(
        "sys.argv",
        [
            "rigplane",
            "probe",
            "FT-991A",
            "--port",
            port,
            "--yes",
            "--out",
            str(out),
            "--label",
            label,
        ],
    )
    with pytest.raises(SystemExit) as exc_info:
        await asyncio.to_thread(main)
    assert exc_info.value.code == 0
    return ProbeFile.from_json(out.read_text(encoding="utf-8"))


async def _silence_one(
    radio, *, trigger: str, target: str, timeout_s: float = 120.0
) -> None:
    """Silence exactly the *target* request via the fake's silence window.

    Fires after *trigger* arrived (already answered) and closes the
    window once *target* arrived (already silenced): the request the
    watcher observes has always passed the fake's silence check, so
    only later requests are affected.
    """
    import time as _time

    deadline = _time.monotonic() + timeout_s
    while _time.monotonic() < deadline:
        if trigger in radio.commands:
            break
        await asyncio.sleep(0)
    else:  # pragma: no cover - the plan always sends the trigger
        raise AssertionError(f"trigger line {trigger!r} never sent")
    seen = sum(1 for line in radio.commands if line == target)
    radio.silence(30.0)
    while _time.monotonic() < deadline:
        if sum(1 for line in radio.commands if line == target) > seen:
            break
        await asyncio.sleep(0)
    else:  # pragma: no cover - the plan always sends the target next
        raise AssertionError(f"target line {target!r} never sent")
    radio.silence(0)


def _cat_plan_facts(first: ProbeFile, wire: tuple[str, ...]) -> tuple[str, str, str]:
    """Pick a silent target, its trigger line, and a refusal verb.

    Both ride on refusals of the clean run: refused lines have no
    native fake handler, so the recording (not the fake) replays them.
    The trigger fires the silence window and must be unique on the
    wire and outside the identity/snapshot lines.
    """
    snapshot_lines = {
        row.request_text
        for row in first.commands
        if row.name in set(first.snapshot.before)
    }
    refused = [row for row in first.commands if row.outcome == "refused"]
    assert len(refused) >= 2
    picked = None
    for index, row in enumerate(first.commands):
        if row.outcome != "refused" or index == 0:
            continue
        trigger = first.commands[index - 1].request_text
        if trigger == "ID;" or trigger in snapshot_lines or wire.count(trigger) != 1:
            continue
        picked = (row.request_text, trigger)
        break
    assert picked is not None, "no silent-target candidate with a unique trigger"
    target, trigger = picked
    verb = next(row.request_text[:2] for row in refused if row.request_text != target)
    assert verb != "ID"
    return target, trigger, verb


@pytest.mark.realtime(
    reason="real probe CLI against a local CAT fake: the silence-window "
    "override and the probe's own answer timeouts run on the real clock"
)
async def test_cat_round_trip_through_real_cli(tmp_path, monkeypatch) -> None:
    """The CAT capture replays as a regression test (FT-991A).

    A clean CLI run learns the wire order; a second run adds one
    refusal and one silence through the fake's own overrides; the
    converted file mounts as recorded answers for a third run, whose
    classes must match the second run's.
    """
    from rigplane_testkit.yaesu import FakeCatRadio
    from rigplane_testkit.yaesu.replay import RecordedCatAnswerTable

    clean_radio = FakeCatRadio(profile="ft991a")
    await clean_radio.start()
    try:
        first = await _run_cat_probe(
            monkeypatch, clean_radio.device_url, tmp_path / "ft991a-a.json", "cat-a"
        )
    finally:
        await clean_radio.stop()
    wire = tuple(clean_radio.commands)
    target, trigger, verb = _cat_plan_facts(first, wire)

    fault_radio = FakeCatRadio(profile="ft991a")
    fault_radio.reject(verb)
    await fault_radio.start()
    try:
        watcher = asyncio.create_task(
            _silence_one(fault_radio, trigger=trigger, target=target)
        )
        try:
            second = await _run_cat_probe(
                monkeypatch,
                fault_radio.device_url,
                tmp_path / "ft991a-b.json",
                "cat-b",
            )
        finally:
            watcher.cancel()
            try:
                await watcher
            except asyncio.CancelledError:
                pass
    finally:
        await fault_radio.stop()
    assert [row.name for row in second.commands] == [row.name for row in first.commands]
    for old, new in zip(first.commands, second.commands):
        if new.request_text == target:
            assert new.outcome == "silent", new.name
        elif new.request_text[:2] == verb:
            assert new.outcome == "refused", new.name
        else:
            assert new.outcome == old.outcome, new.name
    assert [row.outcome for row in second.commands].count("silent") == 1
    assert second.termination.status == "completed"

    rows = probe_to_cat_pairs(second, window="cat")
    table = RecordedCatAnswerTable.from_cat_pairs(rows, window="cat")
    replay_radio = FakeCatRadio(profile="ft991a", recorded_answers=table)
    await replay_radio.start()
    try:
        third = await _run_cat_probe(
            monkeypatch, replay_radio.device_url, tmp_path / "ft991a-c.json", "cat-c"
        )
    finally:
        await replay_radio.stop()
    assert [row.name for row in third.commands] == [row.name for row in second.commands]
    assert [row.outcome for row in third.commands] == [
        row.outcome for row in second.commands
    ]
    assert [(row.name, row.reason) for row in third.skipped] == [
        (row.name, row.reason) for row in second.skipped
    ]
    assert third.termination.status == "completed"


def test_convert_cli_merges_window(tmp_path) -> None:
    from rigplane_testkit.probe_fixtures import main as convert

    out_dir = tmp_path / "fixtures"
    assert (
        convert(
            [
                "convert",
                "--in",
                str(SAMPLE),
                "--window",
                "probe",
                "--out-dir",
                str(out_dir),
            ]
        )
        == 0
    )
    merged = out_dir / "civ_pairs.jsonl"
    first = merged.read_bytes()
    assert first.endswith(b"\n")
    assert all(
        list(json.loads(line)) == sorted(json.loads(line))
        for line in first.splitlines()
    )
    # Same input converts byte-identically and replaces the window's rows.
    assert (
        convert(
            [
                "convert",
                "--in",
                str(SAMPLE),
                "--window",
                "probe",
                "--out-dir",
                str(out_dir),
            ]
        )
        == 0
    )
    assert merged.read_bytes() == first
    rows = [json.loads(line) for line in first.splitlines()]
    assert rows and all(row["window"] == "probe" for row in rows)
    assert all(row["transport"] == "usb" for row in rows)
