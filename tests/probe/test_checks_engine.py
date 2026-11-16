"""Write-check engine, journal and file model (MOR-4464 B1b).

Every check here runs through the real loader (a shipped profile plus a
``[probe]`` section), the production exchanges from ``cli/_probe.py`` and the
testkit fakes: ``FakeCatRadio`` (FT-991A) and ``CivSerialFake`` (IC-705,
IC-7610). The kill tests run the engine in a child process and SIGKILL it.
"""

from __future__ import annotations

import asyncio
import json
import os
import signal
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
RIGS = REPO / "rigs"

CHECK_HEAD = """
[probe]
[[probe.checks]]
id = "synthetic"
title = "Synthetic check"
explain_what = "Changes settings for a moment."
explain_after = "Puts them back."
"""


def check_toml(steps: list[str], restore: list[str], extra: str = "") -> str:
    """steps: TOML bodies of ``[[probe.checks.steps]]``; restore: entries."""
    out = [CHECK_HEAD, extra]
    out += [f"[[probe.checks.steps]]\n{body}" for body in steps]
    out += [f'[[probe.checks.restore]]\nentry = "{entry}"' for entry in restore]
    return "\n".join(out) + "\n"


def load(tmp_path: Path, name: str, toml: str):
    """The shipped profile plus a check, through the real loader."""
    from rigplane.profiles.rig_loader import load_rig

    for shared in RIGS.glob("_*.toml"):  # catalogs the profile refers to
        (tmp_path / shared.name).write_bytes(shared.read_bytes())
    path = tmp_path / f"{name}.toml"
    path.write_text((RIGS / f"{name}.toml").read_text(encoding="utf-8") + toml)
    return load_rig(path)


def bcd_le(hz: int) -> bytes:
    return bytes.fromhex(f"{hz:010d}")[::-1]


SPLIT = check_toml(
    [
        'kind = "write"\nentry = "freq_a"\nvalue = 7074000',
        'kind = "write"\nentry = "freq_b"\nvalue = 145000000',
        'kind = "write"\nentry = "split"\nvalue = "on"',
        'kind = "read"\nentry = "split"',
        'kind = "ask"\nquestion = "Does the display show TX on VFO-B?"',
    ],
    ["freq_a", "freq_b", "split", "mode_main"],
    'witness = ["freq_a", "freq_b"]',
)

CIV_POWER_FREQ = check_toml(
    [
        'kind = "write"\nentry = "rf_power"\nvalue = 64',
        'kind = "write"\nentry = "freq_selected"\nvalue = 7074000',
        'kind = "read"\nentry = "freq_selected"',
    ],
    ["rf_power", "freq_selected"],
    'witness = ["rf_power"]',
)


async def cat_radio():
    from rigplane_testkit.yaesu import FakeCatRadio

    radio = FakeCatRadio(profile="ft991a", frequency_hz=14_074_000, mode_code="2")
    radio.hold("FB", "014250000")
    radio.hold("FT", "0", {"2": "0", "3": "1"})
    await radio.start()
    return radio


async def cat_exchange(radio):
    from rigplane.cli._probe import _yaesu_exchange
    from rigplane.core.serial_open import open_serial_port

    reader, writer = await open_serial_port(radio.device_url, baudrate=38400)
    return await _yaesu_exchange(reader, writer)


async def civ_exchange(fake):
    from rigplane.cli._probe import _civ_exchange
    from rigplane.core.serial_open import open_serial_port

    reader, writer = await open_serial_port(
        "fake-usb-serial", baudrate=115200, opener=fake.opener
    )
    return await _civ_exchange(reader, writer, fake.civ_addr)


def civ_fake(model: str, power: bytes = b"\x01\x28"):
    from rigplane_testkit.serial import CivSerialFake

    # IC-705 declares no identity id; the engine never asks it anyway.
    fake = CivSerialFake(model=model, model_id=b"\xa4", echo=False)
    fake.hold(b"\x14\x0a", power)
    fake.hold(b"\x25\x00", bcd_le(14_074_000))
    return fake


def request(tmp_path: Path, *, answer="yes", events=None, ask_timeout_s=60.0):
    from rigplane.probe.checks import ChecksRequest

    async def ask(question: str) -> str | None:
        if answer is None:
            await asyncio.Event().wait()  # the person never answers
        return answer

    return ChecksRequest(
        journal_path=tmp_path / "out.json.checks-journal.json",
        consent=lambda check, originals: True,
        ask=ask,
        on_event=events,
        ask_timeout_s=ask_timeout_s,
    )


async def run(config, req, exchange):
    from rigplane.probe.checks import run_checks

    return await run_checks(config, req, exchange=exchange, gap_s=0.0, timeout_s=0.5)


def civ_writes(fake, command: int) -> list[bytes]:
    """Data of every frame with *command* that carries a value (a write)."""
    out = []
    for frame in fake.commands:
        body = (b"" if frame.sub is None else bytes((frame.sub,))) + bytes(frame.data)
        if frame.command == command and len(body) > 2 - (command == 0x14):
            out.append(body)
    return out


# --- happy path, failed step, retry, link loss --------------------------------------


async def test_ft991a_cross_band_split_restores_and_verifies(tmp_path: Path) -> None:
    config = load(tmp_path, "ft991a", SPLIT)
    radio = await cat_radio()
    try:
        req = request(tmp_path)
        result = await run(config, req, await cat_exchange(radio))
    finally:
        await radio.stop()
    (record,) = result.records
    assert (record.status, record.restored, record.verified) == ("ran", True, True)
    assert result.exit_code == 0 and result.termination is None
    assert record.error is None
    reads = [s.value for s in record.steps if s.entry == "split" and s.kind == "read"]
    assert reads == ["off", "off", "on", "off"]  # original, rehearsal, step, restore
    assert record.answers[0].answer == "yes" and not record.answers[0].skipped
    assert record.originals == {
        "VFO-A": "14.074 MHz",
        "VFO-B": "14.25 MHz",
        "split": "off",
        "mode": "USB",
    }
    wire = list(radio.commands)
    assert wire.index("FT3;") < wire.index("FT2;", wire.index("FT3;"))
    assert radio.frequency_hz == 14_074_000 and radio.mode_code == "2"
    assert record.witness.changed == []
    assert not req.journal_path.exists()


async def test_ic705_failed_step_restores_everything_in_reverse(tmp_path: Path) -> None:
    config = load(tmp_path, "ic705", CIV_POWER_FREQ)
    fake = civ_fake("IC-705")

    def events(name: str) -> None:
        if name == "steps_begin":
            fake.refuse_next(0x25)

    result = await run(
        config, request(tmp_path, events=events), await civ_exchange(fake)
    )
    (record,) = result.records
    assert record.restored and record.verified and result.exit_code == 0
    assert record.error is not None and "freq_selected" in record.error
    writes = [s for s in record.steps if s.kind == "write"]
    # rehearsal (rf_power, freq), steps (rf_power 64, freq refused), restore reversed
    assert [(s.entry, s.outcome) for s in writes] == [
        ("rf_power", "ack_only"),
        ("freq_selected", "ack_only"),
        ("rf_power", "ack_only"),
        ("freq_selected", "refused"),
        ("freq_selected", "ack_only"),
        ("rf_power", "ack_only"),
    ]
    assert civ_writes(fake, 0x14)[-1] == b"\x0a\x01\x28"


async def test_ic705_refused_restore_write_is_retried_once(tmp_path: Path) -> None:
    config = load(tmp_path, "ic705", CIV_POWER_FREQ)
    fake = civ_fake("IC-705")

    def events(name: str) -> None:
        if name == "restore_begin":
            fake.refuse_next(0x25)

    result = await run(
        config, request(tmp_path, events=events), await civ_exchange(fake)
    )
    (record,) = result.records
    assert record.restored and record.verified, record.error
    restore = [s for s in record.steps if s.kind == "write"][-3:]
    assert [(s.entry, s.outcome) for s in restore] == [
        ("freq_selected", "refused"),
        ("freq_selected", "ack_only"),
        ("rf_power", "ack_only"),
    ]


async def test_link_lost_during_restore_is_exit_4_and_keeps_the_journal(
    tmp_path: Path,
) -> None:
    config = load(tmp_path, "ic705", CIV_POWER_FREQ)
    fake = civ_fake("IC-705")

    def events(name: str) -> None:
        if name == "restore_begin":
            fake._vanish_now()

    req = request(tmp_path, events=events)
    result = await run(config, req, await civ_exchange(fake))
    (record,) = result.records
    assert result.exit_code == 4
    assert not record.restored and not record.verified
    assert result.termination is not None
    assert result.termination.status == "restore_failed"
    assert "please set it by hand" in (record.error or "")
    assert req.journal_path.exists()


# --- refusals before any write --------------------------------------------------------


async def test_cat_original_in_c4fm_refuses_to_start_without_a_write(
    tmp_path: Path,
) -> None:
    from rigplane_testkit.yaesu import FakeCatRadio

    config = load(tmp_path, "ft991a", SPLIT)
    radio = FakeCatRadio(profile="ft991a", mode_code="E")  # C4FM
    radio.hold("FB", "014250000")
    radio.hold("FT", "0", {"2": "0", "3": "1"})
    await radio.start()
    try:
        req = request(tmp_path)
        result = await run(config, req, await cat_exchange(radio))
    finally:
        await radio.stop()
    (record,) = result.records
    assert record.status == "refused" and "mode" in (record.error or "")
    assert all(line in ("FA;", "FB;", "FT;", "MD0;") for line in radio.commands)
    assert not req.journal_path.exists()
    assert result.exit_code == 0


async def test_civ_original_in_dv_refuses_to_start_without_a_write(
    tmp_path: Path,
) -> None:
    toml = check_toml(
        ['kind = "write"\nentry = "rf_power"\nvalue = 64'],
        ["rf_power", "mode_selected"],
    )
    config = load(tmp_path, "ic705", toml)
    fake = civ_fake("IC-705")
    fake.hold(b"\x26\x00", bytes((0x17, 0x00, 0x01)))  # DV
    result = await run(config, request(tmp_path), await civ_exchange(fake))
    (record,) = result.records
    assert record.status == "refused"
    assert civ_writes(fake, 0x14) == [] and civ_writes(fake, 0x26) == []


async def test_ic7610_write_above_original_power_is_refused_before_sending(
    tmp_path: Path,
) -> None:
    toml = check_toml(['kind = "write"\nentry = "rf_power"\nvalue = 128'], ["rf_power"])
    config = load(tmp_path, "ic7610", toml)
    fake = civ_fake("IC-7610", power=b"\x00\x64")
    result = await run(config, request(tmp_path), await civ_exchange(fake))
    (record,) = result.records
    assert "above the original power" in (record.error or "")
    assert b"\x0a\x01\x28" not in civ_writes(fake, 0x14)
    assert record.restored and record.verified


async def test_precondition_pattern_refuses_before_any_write(tmp_path: Path) -> None:
    toml = check_toml(
        ['kind = "write"\nentry = "freq_a"\nvalue = 7074000'],
        ["freq_a"],
        '[[probe.checks.preconditions]]\nentry = "split"\nequals = "FT1;"',
    )
    config = load(tmp_path, "ft991a", toml)
    radio = await cat_radio()
    try:
        result = await run(config, request(tmp_path), await cat_exchange(radio))
    finally:
        await radio.stop()
    (record,) = result.records
    assert record.status == "refused" and "precondition" in (record.error or "")
    assert list(radio.commands) == ["FT;"]


async def test_every_original_is_judged_as_its_restore_write_before_any_write(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Contract line 2: the explicit pre-write encoding of every original, not
    only the rehearsal of the first one, happens before the first write byte."""
    from rigplane.probe import checks

    config = load(tmp_path, "ic705", CIV_POWER_FREQ)
    fake = civ_fake("IC-705")
    judged: list[tuple[str, str, object]] = []
    real = checks.judged_frame

    def spy(table, entry, form, value, receiver, restored):
        judged.append((entry, form, value))
        return real(table, entry, form, value, receiver, restored)

    monkeypatch.setattr(checks, "judged_frame", spy)
    inner = await civ_exchange(fake)
    at_first_write: list[set] = []

    async def exchange(body: bytes | None, timeout: float):
        if body and body not in (b"\x14\x0a", b"\x25\x00") and not at_first_write:
            at_first_write.append({(e, v) for e, f, v in judged if f == "write"})
        return await inner(body, timeout)

    result = await run(config, request(tmp_path), exchange)
    assert result.records[0].restored and result.records[0].verified
    assert at_first_write == [{("rf_power", 128), ("freq_selected", 14_074_000)}]


# --- ask timeout, cancel ----------------------------------------------------------------


async def test_ask_times_out_as_skipped_and_restores(tmp_path: Path) -> None:
    config = load(tmp_path, "ft991a", SPLIT)
    radio = await cat_radio()
    try:
        req = request(tmp_path, answer=None, ask_timeout_s=0.05)
        result = await run(config, req, await cat_exchange(radio))
    finally:
        await radio.stop()
    (record,) = result.records
    assert record.answers[0].skipped and record.answers[0].answer is None
    assert record.restored and record.verified
    assert radio.frequency_hz == 14_074_000


@pytest.mark.skipif(os.name != "posix", reason="POSIX signals (Windows: MOR-4497)")
async def test_sigterm_cancels_into_restore_and_ctrl_c_during_restore_is_ignored(
    tmp_path: Path,
) -> None:
    config = load(tmp_path, "ft991a", SPLIT)
    radio = await cat_radio()
    seen: list[str] = []

    def events(name: str) -> None:
        seen.append(name)
        if name == "steps_begin":
            os.kill(os.getpid(), signal.SIGTERM)
        if name == "restore_begin":
            os.kill(os.getpid(), signal.SIGINT)

    try:
        result = await run(
            config, request(tmp_path, events=events), await cat_exchange(radio)
        )
        for _ in range(5):  # a stray SIGINT would raise here
            await asyncio.sleep(0)
    finally:
        await radio.stop()
    (record,) = result.records
    assert result.termination is not None and result.termination.status == "cancelled"
    assert "cancelled" in (record.error or "")
    assert record.restored and record.verified
    assert seen == ["journal_written", "steps_begin", "restore_begin"]
    assert radio.frequency_hz == 14_074_000


# --- journal ------------------------------------------------------------------------------


async def test_journal_is_hand_settable_and_removed_after_a_verified_restore(
    tmp_path: Path,
) -> None:
    from rigplane.probe.journal import leftover_journal_line, read_journal

    config = load(tmp_path, "ft991a", SPLIT)
    radio = await cat_radio()
    req_box: list = []
    seen: list = []

    def events(name: str) -> None:
        if name == "journal_written":
            seen.append(read_journal(req_box[0].journal_path))
            seen.append(leftover_journal_line(req_box[0].journal_path))

    req_box.append(request(tmp_path, events=events))
    try:
        await run(config, req_box[0], await cat_exchange(radio))
    finally:
        await radio.stop()
    journal, line = seen
    assert journal.check_id == "synthetic" and journal.profile == "FT-991A"
    assert dict(journal.originals) == {
        "VFO-A": "14.074 MHz",
        "VFO-B": "14.25 MHz",
        "split": "off",
        "mode": "USB",
    }
    assert line == (
        "A check did not finish last time. Before it the radio had: VFO-A "
        "14.074 MHz, VFO-B 14.25 MHz, split off, mode USB. Please set these by hand."
    )
    assert leftover_journal_line(req_box[0].journal_path) is None


async def test_journal_and_its_folder_are_fsynced_before_the_first_write_byte(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = load(tmp_path, "ft991a", SPLIT)
    radio = await cat_radio()
    synced: list[int] = []
    real_fsync = os.fsync

    def fsync(fd: int) -> None:
        synced.append(os.fstat(fd).st_ino)
        real_fsync(fd)

    monkeypatch.setattr(os, "fsync", fsync)
    first_write: list[list[int]] = []
    req = request(tmp_path)
    try:
        inner = await cat_exchange(radio)

        async def exchange(body: bytes | None, timeout: float):
            if (
                body
                and body not in (b"FA;", b"FB;", b"FT;", b"MD0;")
                and not first_write
            ):
                first_write.append(list(synced))
                first_write.append(
                    [req.journal_path.stat().st_ino, tmp_path.stat().st_ino]
                )
            return await inner(body, timeout)

        await run(config, req, exchange)
    finally:
        await radio.stop()
    before, (journal_ino, folder_ino) = first_write
    assert journal_ino in before, "the journal file was not fsynced before the write"
    assert folder_ino in before, "the journal folder was not fsynced before the write"


CHILD = textwrap.dedent(
    """
    import asyncio, json, os, sys, time
    from pathlib import Path
    from rigplane.profiles.rig_loader import load_rig
    from rigplane.probe.checks import ChecksRequest, run_checks
    from rigplane.core.serial_open import open_serial_port
    from rigplane.cli._probe import _civ_exchange
    from rigplane_testkit.serial import CivSerialFake

    profile, journal, when = sys.argv[1:4]
    synced = []
    real_fsync = os.fsync
    def fsync(fd):
        synced.append(os.fstat(fd).st_ino)
        real_fsync(fd)
    os.fsync = fsync

    async def main():
        fake = CivSerialFake(model="IC-705", model_id=b"\\xa4", echo=False)
        fake.hold(b"\\x14\\x0a", b"\\x01\\x28")
        fake.hold(b"\\x25\\x00", bytes.fromhex("0014074000")[::-1])
        reader, writer = await open_serial_port("fake", baudrate=115200, opener=fake.opener)
        inner = await _civ_exchange(reader, writer, fake.civ_addr)
        reads = {b"\\x14\\x0a", b"\\x25\\x00"}

        async def exchange(body, timeout):
            if body and body not in reads:
                if when == "after":
                    await inner(body, timeout)
                print("READY " + json.dumps(synced), flush=True)
                time.sleep(3600)
            return await inner(body, timeout)

        async def ask(question):
            return "yes"

        request = ChecksRequest(Path(journal), lambda c, o: True, ask)
        await run_checks(load_rig(Path(profile)), request, exchange=exchange,
                         gap_s=0.0, timeout_s=0.5)

    asyncio.run(main())
    """
)


@pytest.mark.skipif(os.name != "posix", reason="SIGKILL is POSIX")
@pytest.mark.parametrize("when", ["before", "after"])
def test_sigkill_around_the_first_write_leaves_a_journal_with_exact_originals(
    tmp_path: Path, when: str
) -> None:
    from rigplane.probe.journal import leftover_journal_line

    load(tmp_path, "ic705", CIV_POWER_FREQ)  # writes the profile and its catalogs
    profile = tmp_path / "ic705.toml"
    journal = tmp_path / "out.json.checks-journal.json"
    env = {**os.environ, "PYTHONPATH": os.pathsep.join([str(REPO), str(REPO / "src")])}
    child = subprocess.Popen(
        [sys.executable, "-c", CHILD, str(profile), str(journal), when],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        cwd=REPO,
        env=env,
    )
    try:
        line = child.stdout.readline() if child.stdout else ""
    finally:
        child.kill()  # SIGKILL: no handler, no restore, no cleanup
        child.wait(timeout=30)
    assert line.startswith("READY "), child.stderr.read() if child.stderr else line
    assert child.returncode == -signal.SIGKILL
    synced = json.loads(line.split(" ", 1)[1])
    assert journal.stat().st_ino in synced, "journal not fsynced before the write"
    assert tmp_path.stat().st_ino in synced, "journal folder not fsynced"
    assert leftover_journal_line(journal) == (
        "A check did not finish last time. Before it the radio had: RF power 128, "
        "frequency of the selected VFO 14.074 MHz. Please set these by hand."
    )


# --- runner hook ----------------------------------------------------------------------------


async def test_runner_calls_the_engine_only_when_checks_are_requested(
    tmp_path: Path,
) -> None:
    from rigplane.probe.runner import run_probe

    config = load(tmp_path, "ft991a", SPLIT)
    radio = await cat_radio()
    try:
        plain = await run_probe(
            config,
            "0" * 64,
            exchange=await cat_exchange(radio),
            port_kind="usb",
            baud=38400,
            label="",
            include_private=False,
            gap_s=0.0,
            timeout_s=0.2,
        )
        assert not any(line.startswith("FT3") for line in radio.commands)
        both = await run_probe(
            config,
            "0" * 64,
            exchange=await cat_exchange(radio),
            port_kind="usb",
            baud=38400,
            label="",
            include_private=False,
            gap_s=0.0,
            timeout_s=0.2,
            checks=request(tmp_path),
        )
    finally:
        await radio.stop()
    assert plain.file.mode == "read" and plain.file.checks == ()
    assert both.file.mode == "read+checks" and both.exit_code == 0
    assert [r.status for r in both.file.checks] == ["ran"]
    assert "FT3;" in radio.commands


# --- file model -----------------------------------------------------------------------------


def _file_with_checks():
    from rigplane.probe.model import (
        CheckAnswerRow,
        CheckRecord,
        CheckStepRow,
        CheckWitness,
        IdentityReport,
        ProbeFile,
        RadioInfo,
        TerminationInfo,
        ToolInfo,
    )

    record = CheckRecord(
        "power-2m",
        "Power on 2 m",
        "ran",
        steps=(
            CheckStepRow("write", "power", "50433130303b", None, "silent", "100"),
            CheckStepRow("read", "power", "50433b", "50433035303b", "data", "50"),
        ),
        answers=(CheckAnswerRow("Did it change?", None, True),),
        originals={"RF power": "50"},
        witness=CheckWitness({"info": "IF0;"}, {"info": None}, ["info"]),
        restored=False,
        verified=False,
        error="could not put RF power back to 50; please set it by hand",
    )
    return ProbeFile(
        tool=ToolInfo("1", "3.12", "Linux", "2026-11-16T00:00:00+00:00"),
        mode="read+checks",
        radio=RadioInfo(
            "FT-991A",
            "0" * 64,
            "yaesu_cat",
            "usb",
            38400,
            IdentityReport("ID0670;", "0670"),
            True,
        ),
        private_reads_included=False,
        label="",
        termination=TerminationInfo("restore_failed", "RF power"),
        checks=(record, CheckRecord("other", "Other", "declined")),
    )


def test_check_records_round_trip_through_the_parser() -> None:
    from rigplane.probe.model import ProbeFile

    file = _file_with_checks()
    assert ProbeFile.from_json(file.to_json()) == file
    plain = json.loads(file.to_json())
    assert plain["checks"][0]["steps"][0]["class"] == "silent"


@pytest.mark.parametrize(
    ("path", "value", "message"),
    [
        (("mode",), "write", "mode must be one of"),
        (("checks", 0, "status"), "maybe", "status must be one of"),
        (("checks", 0, "steps", 0, "class"), "sent", "class must be one of"),
        (("checks", 0, "steps", 0, "kind"), "raw_write", "kind must be one of"),
        (("checks", 0, "restored"), "yes", "restored must be a boolean"),
        (("checks", 0, "bytes"), "00", "not part of the contract"),
        (("termination", "status"), "lost", "status must be one of"),
    ],
)
def test_the_parser_rejects_bad_check_fields(path, value, message) -> None:
    from rigplane.probe.model import ProbeFile

    plain = json.loads(_file_with_checks().to_json())
    node = plain
    for key in path[:-1]:
        node = node[key]
    node[path[-1]] = value
    with pytest.raises(ValueError, match=message):
        ProbeFile.from_json(json.dumps(plain))


def test_the_parser_rejects_a_check_without_a_field() -> None:
    from rigplane.probe.model import ProbeFile

    plain = json.loads(_file_with_checks().to_json())
    del plain["checks"][0]["verified"]
    with pytest.raises(ValueError, match=r"missing field checks\[0\].verified"):
        ProbeFile.from_json(json.dumps(plain))
