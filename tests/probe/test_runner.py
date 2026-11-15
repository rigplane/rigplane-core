"""Runner behavior against the repo simulators (MOR-4464 PR A).

Yaesu runs go through ``FakeCatRadio`` over a real ``socket://`` serial
open; CI-V runs go through ``CivSerialFake`` with the production open.
Unit-level fault cases use tiny scripted exchanges so the run stays fast.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from unittest.mock import patch

from rigplane.commands import (
    CONTROLLER_ADDR,
    build_civ_frame,
    build_cmd29_frame,
    command_carries_sub,
)
from rigplane.core.serial_open import open_serial_port
from rigplane.profiles.rig_loader import RigConfig, load_rig
from rigplane.probe import runner
from rigplane.probe.readlist import build_allowed_bodies, build_read_plan
from rigplane_testkit.serial import CivSerialFake
from rigplane_testkit.yaesu import FakeCatRadio

REPO_ROOT = Path(__file__).resolve().parents[2]
RIGS_DIR = REPO_ROOT / "rigs"

GAP = 0.0
TIMEOUT = 2.0
SHORT_TIMEOUT = 0.05
YAESU_BAUD = 38400
CIV_BAUD = 115200


def _load(name: str) -> RigConfig:
    return load_rig(RIGS_DIR / f"{name}.toml")


def _subset(config: RigConfig, *names: str) -> list:
    wanted = set(names)
    return [item for item in build_read_plan(config).asked if item.name in wanted]


async def _yaesu_exchange(reader, writer) -> runner.Exchange:
    async def exchange(request: bytes | None, timeout: float) -> bytes | None:
        if request:
            writer.write(request)
            await writer.drain()
        try:
            return await asyncio.wait_for(reader.readuntil(b";"), timeout)
        except (asyncio.TimeoutError, asyncio.IncompleteReadError):
            return None

    return exchange


def _civ_exchange(reader, writer, radio_addr: int) -> runner.Exchange:
    async def exchange(request: bytes | None, timeout: float) -> bytes | None:
        sent: bytes | None = None
        if request:
            command, rest = request[0], request[1:]
            if command_carries_sub(command) and rest:
                sub, data = rest[0], rest[1:]
            else:
                sub, data = None, rest
            sent = build_civ_frame(
                radio_addr, CONTROLLER_ADDR, command, sub=sub, data=data or None
            )
            writer.write(sent)
            await writer.drain()
        try:
            while True:
                frame = await asyncio.wait_for(reader.readuntil(b"\xfd"), timeout)
                if sent is not None and frame == sent:
                    continue  # USB echo of our own frame, not the answer
                return frame
        except (asyncio.TimeoutError, asyncio.IncompleteReadError):
            return None

    return exchange


def _nak_frame(civ_addr: int) -> bytes:
    return build_civ_frame(civ_addr, CONTROLLER_ADDR, 0xFA)


def _nak_except(
    seen: list[bytes],
    special: dict[bytes, list[bytes | None]],
    *,
    addr: int,
) -> runner.Exchange:
    """Scripted CI-V exchange: NAK everything except queued *special* reads.

    Identity and snapshot phases consume from the same exchange, so every
    non-targeted read gets an immediate NAK (fast, and a settled row);
    targeted reads drain their queue across the request and its
    continuations, then fall silent.
    """
    nak = build_civ_frame(CONTROLLER_ADDR, addr, 0xFA)
    pending: list[bytes | None] = []

    async def exchange(request: bytes | None, timeout: float) -> bytes | None:
        nonlocal pending
        if request is not None:
            seen.append(request)
            pending = list(special.get(request, []))
            if pending:
                chunk = pending.pop(0)
                return chunk
            return nak
        if pending:
            return pending.pop(0)
        return None

    return exchange


async def test_stale_menu_reply_is_not_taken_as_the_answer() -> None:
    # MOR-4464 finding 3: a delayed reply to another menu item must not
    # answer this read; the correct reply still settles the row.
    config = _load("ic7300")
    addr = config.civ_addr
    stale = build_civ_frame(
        CONTROLLER_ADDR, addr, 0x1A, sub=0x05, data=bytes.fromhex("000301")
    )
    correct = build_civ_frame(
        CONTROLLER_ADDR, addr, 0x1A, sub=0x05, data=bytes.fromhex("000201")
    )
    seen: list[bytes] = []
    report = await runner.run_probe(
        config,
        config_toml_sha256="0" * 64,
        exchange=_nak_except(
            seen, {bytes.fromhex("1a050002"): [stale, correct]}, addr=addr
        ),
        port_kind="usb",
        baud=CIV_BAUD,
        label="",
        include_private=False,
        gap_s=GAP,
        timeout_s=SHORT_TIMEOUT,
        items=_subset(config, "get_ssb_rx_bass"),
    )
    assert [row.outcome for row in report.file.commands] == ["data"]
    assert report.file.commands[0].reply_hex == correct.hex()
    assert [row.hex for row in report.file.unsolicited] == [stale.hex()]


async def test_wrong_receiver_reply_is_not_taken_as_the_answer() -> None:
    # MOR-4464 finding 3: a wrapped MAIN reply must not answer a SUB read.
    config = _load("ic7610")
    addr = config.civ_addr
    main_reply = build_cmd29_frame(
        CONTROLLER_ADDR, addr, 0x14, sub=0x01, data=bytes.fromhex("0010"),
        receiver=0x00,
    )
    sub_reply = build_cmd29_frame(
        CONTROLLER_ADDR, addr, 0x14, sub=0x01, data=bytes.fromhex("0010"),
        receiver=0x01,
    )
    items = [
        item
        for item in build_read_plan(config).asked
        if item.name == "cmd29:get_af_level:1"
    ]
    assert len(items) == 1
    seen: list[bytes] = []
    report = await runner.run_probe(
        config,
        config_toml_sha256="0" * 64,
        exchange=_nak_except(
            seen, {bytes.fromhex("29011401"): [main_reply, sub_reply]}, addr=addr
        ),
        port_kind="usb",
        baud=CIV_BAUD,
        label="",
        include_private=False,
        gap_s=GAP,
        timeout_s=SHORT_TIMEOUT,
        items=items,
    )
    assert [row.outcome for row in report.file.commands] == ["data"]
    assert report.file.commands[0].reply_hex == sub_reply.hex()
    assert [row.hex for row in report.file.unsolicited] == [main_reply.hex()]


async def test_plain_reply_to_wrapped_request_is_left_unsolicited() -> None:
    # MOR-4464 finding 3: the shared matcher cannot attribute an
    # unwrapped reply to a wrapped read (the receiver is unknown), so the
    # frame stays unsolicited instead of answering either receiver.
    config = _load("ic7610")
    addr = config.civ_addr
    plain = build_civ_frame(
        CONTROLLER_ADDR, addr, 0x14, sub=0x01, data=bytes.fromhex("0010")
    )
    items = [
        item
        for item in build_read_plan(config).asked
        if item.name == "cmd29:get_af_level:0"
    ]
    assert len(items) == 1
    seen: list[bytes] = []
    report = await runner.run_probe(
        config,
        config_toml_sha256="0" * 64,
        exchange=_nak_except(
            seen, {bytes.fromhex("29001401"): [plain]}, addr=addr
        ),
        port_kind="usb",
        baud=CIV_BAUD,
        label="",
        include_private=False,
        gap_s=GAP,
        timeout_s=SHORT_TIMEOUT,
        items=items,
    )
    assert [row.outcome for row in report.file.commands] == ["unsolicited"]
    assert report.file.commands[0].reply_hex is None
    assert [row.hex for row in report.file.unsolicited] == [plain.hex()]


async def _run_yaesu(
    config: RigConfig, radio: FakeCatRadio, **kwargs
) -> runner.ProbeRun:
    reader, writer = await open_serial_port(radio.device_url, baudrate=YAESU_BAUD)
    try:
        return await runner.run_probe(
            config,
            config_toml_sha256="0" * 64,
            exchange=await _yaesu_exchange(reader, writer),
            port_kind="usb",
            baud=YAESU_BAUD,
            label="",
            include_private=False,
            gap_s=GAP,
            timeout_s=TIMEOUT,
            **kwargs,
        )
    finally:
        writer.close()


async def test_all_data_run_yaesu_ft991a() -> None:
    # Scripted: FakeCatRadio requires commands the FT-991A profile does
    # not declare (get_powerstat, get_if_status), so the 991A pass is
    # covered here and the live-fake pass below stays on the FTX-1.
    config = _load("ft991a")
    seen: list[bytes] = []

    async def exchange(request: bytes | None, timeout: float) -> bytes | None:
        assert request is not None
        seen.append(request)
        if request == b"ID;":
            return b"ID0670;"
        return b"?;"

    report = await runner.run_probe(
        config,
        config_toml_sha256="0" * 64,
        exchange=exchange,
        port_kind="usb",
        baud=YAESU_BAUD,
        label="",
        include_private=False,
        gap_s=GAP,
        timeout_s=TIMEOUT,
    )
    items = list(build_read_plan(config).asked)
    assert seen
    assert len(report.file.commands) == len(items)
    assert report.aborted is None
    assert {row.outcome for row in report.file.commands} <= {"data", "refused"}
    assert report.file.radio.identity_matches_profile is True
    assert report.file.radio.identity_reported.parsed == "0670"
    assert report.file.unsolicited == ()


async def test_all_data_run_yaesu_ftx1() -> None:
    config = _load("ftx1")
    radio = FakeCatRadio(profile="ftx1", frequency_hz=7_074_000)
    await radio.start()
    try:
        report = await _run_yaesu(config, radio)
    finally:
        await radio.stop()
    items = list(build_read_plan(config).asked)
    assert len(report.file.commands) == len(items)
    assert report.aborted is None
    assert report.file.radio.identity_reported.parsed == "0840"
    assert radio.frequency_hz == 7_074_000


async def test_all_data_run_civ_ic7300() -> None:
    config = _load("ic7300")
    fake = CivSerialFake(model="IC-7300", echo=False)
    reader, writer = await open_serial_port(
        "ic7300", baudrate=CIV_BAUD, opener=fake.opener
    )
    report = await runner.run_probe(
        config,
        config_toml_sha256="0" * 64,
        exchange=_civ_exchange(reader, writer, config.civ_addr),
        port_kind="usb",
        baud=CIV_BAUD,
        label="",
        include_private=False,
        gap_s=GAP,
        timeout_s=TIMEOUT,
    )
    assert report.aborted is None
    assert report.file.commands
    assert {row.outcome for row in report.file.commands} <= {"data", "refused"}
    assert report.file.radio.identity_matches_profile is True


async def test_all_data_run_civ_g7250() -> None:
    # LIVE G7250 2026-11-09 (MOR-4352): the 19 00 reply payload is the
    # echoed asked address byte; 0x1D 0x19 answers 72 50.
    config = _load("g7250")
    fake = CivSerialFake(model="G7250", model_id=b"\xa0", echo=False)
    reader, writer = await open_serial_port(
        "g7250", baudrate=CIV_BAUD, opener=fake.opener
    )
    report = await runner.run_probe(
        config,
        config_toml_sha256="0" * 64,
        exchange=_civ_exchange(reader, writer, config.civ_addr),
        port_kind="usb",
        baud=CIV_BAUD,
        label="",
        include_private=False,
        gap_s=GAP,
        timeout_s=TIMEOUT,
    )
    assert report.aborted is None
    assert report.file.commands
    assert report.file.radio.identity_matches_profile is True


async def test_refused_and_silent_keep_the_run_going() -> None:
    config = _load("ftx1")
    radio = FakeCatRadio(profile="ftx1")
    radio.reject("KS")
    await radio.start()
    try:
        report = await _run_yaesu(
            config, radio, items=_subset(config, "get_freq", "get_keyer_speed")
        )
    finally:
        await radio.stop()
    by_name = {row.name: row for row in report.file.commands}
    assert by_name["get_freq"].outcome == "data"
    assert by_name["get_keyer_speed"].outcome == "refused"
    assert report.aborted is None


async def test_silent_radio_stops_before_the_main_pass() -> None:
    # MOR-4464 finding 5: identity and snapshot silence counts toward
    # the stop, so a fully silent radio never reaches the main pass.
    config = _load("ftx1")
    radio = FakeCatRadio(profile="ftx1")
    await radio.start()
    try:
        radio.silence(30.0)
        reader, writer = await open_serial_port(radio.device_url, baudrate=YAESU_BAUD)
        try:
            report = await runner.run_probe(
                config,
                config_toml_sha256="0" * 64,
                exchange=await _yaesu_exchange(reader, writer),
                port_kind="usb",
                baud=YAESU_BAUD,
                label="",
                include_private=False,
                gap_s=GAP,
                timeout_s=SHORT_TIMEOUT,
                items=_subset(config, "get_freq"),
            )
        finally:
            writer.close()
    finally:
        await radio.stop()
    assert [row.outcome for row in report.file.commands] == []
    assert report.aborted == runner.SILENT_ABORT_MESSAGE


async def test_five_silent_in_a_row_aborts_with_plain_message() -> None:
    # MOR-4464 finding 5: the stop already fires inside the snapshot
    # phase (identity plus four snapshot reads), so the main pass never
    # runs on a fully silent radio.
    config = _load("ftx1")
    radio = FakeCatRadio(profile="ftx1")
    await radio.start()
    try:
        radio.silence(30.0)
        reader, writer = await open_serial_port(radio.device_url, baudrate=YAESU_BAUD)
        try:
            report = await runner.run_probe(
                config,
                config_toml_sha256="0" * 64,
                exchange=await _yaesu_exchange(reader, writer),
                port_kind="usb",
                baud=YAESU_BAUD,
                label="",
                include_private=False,
                gap_s=GAP,
                timeout_s=SHORT_TIMEOUT,
            )
        finally:
            writer.close()
    finally:
        await radio.stop()
    assert [row.outcome for row in report.file.commands] == []
    assert report.aborted == runner.SILENT_ABORT_MESSAGE


async def test_garbled_reply_is_malformed_and_does_not_abort() -> None:
    # KS is not a snapshot read, so the one-shot garble lands on the pass row.
    config = _load("ftx1")
    radio = FakeCatRadio(profile="ftx1")
    await radio.start()
    radio.garble_next_reply("KS")
    try:
        report = await _run_yaesu(
            config, radio, items=_subset(config, "get_keyer_speed", "get_id")
        )
    finally:
        await radio.stop()
    by_name = {row.name: row for row in report.file.commands}
    assert by_name["get_keyer_speed"].outcome == "malformed"
    assert by_name["get_id"].outcome == "data"
    assert report.aborted is None


async def test_unsolicited_push_is_recorded_and_run_continues() -> None:
    config = _load("ftx1")
    radio = FakeCatRadio(profile="ftx1")
    await radio.start()
    # The TX push cannot be mistaken for the get_freq answer, so it is
    # recorded as unsolicited and the run reads the real answer next.
    radio.auto_info_burst_on_next_request("TX")
    try:
        report = await _run_yaesu(
            config, radio, items=_subset(config, "get_freq", "get_id")
        )
    finally:
        await radio.stop()
    assert [row.text for row in report.file.unsolicited] == ["TX0;"]
    assert {row.name: row.outcome for row in report.file.commands} == {
        "get_freq": "data",
        "get_id": "data",
    }
    assert report.aborted is None


def _scripted_yaesu(
    answers: dict[bytes, list[bytes | None]],
    seen: list[bytes],
) -> runner.Exchange:
    async def exchange(request: bytes | None, timeout: float) -> bytes | None:
        assert request is not None
        seen.append(request)
        queue = answers.get(request, [b"?;"])
        return queue.pop(0) if queue else b"?;"

    return exchange


async def test_snapshot_diff_is_reported() -> None:
    config = _load("ftx1")
    seen: list[bytes] = []
    answers: dict[bytes, list[bytes | None]] = {
        b"ID;": [b"ID0840;"],
        # before-snapshot, pass, after-snapshot: the frequency moved mid-run.
        b"FA;": [b"FA14074000;", b"FA14075000;", b"FA14075000;"],
    }
    report = await runner.run_probe(
        config,
        config_toml_sha256="0" * 64,
        exchange=_scripted_yaesu(answers, seen),
        port_kind="usb",
        baud=YAESU_BAUD,
        label="",
        include_private=False,
        gap_s=GAP,
        timeout_s=TIMEOUT,
        items=_subset(config, "get_freq"),
    )
    assert report.file.snapshot.before["get_freq"] == "FA14074000;"
    assert report.file.snapshot.after["get_freq"] == "FA14075000;"
    assert report.file.snapshot.changed == ["get_freq"]


async def test_private_reads_not_sent_by_default() -> None:
    config = _load("ftx1")
    radio = FakeCatRadio(profile="ftx1")
    await radio.start()
    try:
        report = await _run_yaesu(config, radio)
    finally:
        await radio.stop()
    sent = list(radio.commands)
    assert sent
    assert not [line for line in sent if line.startswith(("MR", "MT"))]
    assert report.file.private_reads_included is False


async def test_private_reads_sent_with_the_flag() -> None:
    config = _load("ftx1")
    seen: list[bytes] = []
    report = await runner.run_probe(
        config,
        config_toml_sha256="0" * 64,
        exchange=_scripted_yaesu({}, seen),
        port_kind="usb",
        baud=YAESU_BAUD,
        label="",
        include_private=True,
        gap_s=GAP,
        timeout_s=TIMEOUT,
    )
    assert b"MR00001;" in seen
    assert b"MT00001;" in seen
    assert report.file.private_reads_included is True


async def test_transport_error_ends_the_run() -> None:
    # MOR-4464 finding 5: a break in the before snapshot stops the run
    # before the main pass; nothing more is sent.
    config = _load("ftx1")

    async def exchange(request: bytes | None, timeout: float) -> bytes | None:
        if request == b"ID;":
            return b"ID0840;"
        raise OSError("device is gone")

    report = await runner.run_probe(
        config,
        config_toml_sha256="0" * 64,
        exchange=exchange,
        port_kind="usb",
        baud=YAESU_BAUD,
        label="",
        include_private=False,
        gap_s=GAP,
        timeout_s=TIMEOUT,
        items=_subset(config, "get_freq", "get_id"),
    )
    assert [row.outcome for row in report.file.commands] == []
    assert report.aborted is not None


async def _paced_run(config, exchange, *, gap_s: float, **kwargs):
    """Run the probe with the sleep calls recorded; returns (report, delays)."""
    delays: list[float] = []

    async def fake_sleep(delay: float) -> None:
        delays.append(delay)

    with patch("asyncio.sleep", side_effect=fake_sleep):
        report = await runner.run_probe(
            config,
            config_toml_sha256="0" * 64,
            exchange=exchange,
            port_kind="usb",
            baud=YAESU_BAUD,
            label="",
            include_private=False,
            gap_s=gap_s,
            timeout_s=SHORT_TIMEOUT,
            **kwargs,
        )
    return report, delays


async def test_fully_silent_radio_sends_exactly_five_requests() -> None:
    # MOR-4464 finding 5: identity plus snapshot reads count toward the
    # five-silence stop, and nothing more is sent afterwards.
    config = _load("ftx1")
    seen: list[bytes] = []

    async def exchange(request: bytes | None, timeout: float) -> bytes | None:
        if request is not None:
            seen.append(request)
        return None

    report, delays = await _paced_run(config, exchange, gap_s=0.25)
    assert seen == [b"ID;", b"IF;", b"FA;", b"MD0;", b"ST;"]
    assert delays == [0.25] * 5
    assert report.aborted == runner.SILENT_ABORT_MESSAGE
    assert [row.outcome for row in report.file.commands] == []


async def test_answering_radio_paces_every_send() -> None:
    # MOR-4464 finding 5: one gap before every transmitted read: identity,
    # before snapshot, main pass and after snapshot alike.
    from rigplane.probe.readlist import build_read_plan

    config = _load("ftx1")
    seen: list[bytes] = []

    async def exchange(request: bytes | None, timeout: float) -> bytes | None:
        assert request is not None
        seen.append(request)
        if request == b"ID;":
            return b"ID0840;"
        return request[:-1] + b"0;"

    report, delays = await _paced_run(config, exchange, gap_s=0.25)
    asked = len(build_read_plan(config).asked)
    assert report.aborted is None
    assert seen == (
        [b"ID;", b"IF;", b"FA;", b"MD0;", b"ST;", b"TX;", b"PS;", b"AI;"]
        + [item.body for item in build_read_plan(config).asked]
        + [b"IF;", b"FA;", b"MD0;", b"ST;", b"TX;", b"PS;", b"AI;"]
    )
    assert len(seen) == 1 + 7 + asked + 7
    assert delays == [0.25] * len(seen)


async def test_silence_mid_pass_stops_before_the_after_snapshot() -> None:
    # MOR-4464 finding 5: silence starting in the middle of the main pass
    # stops the run; the after snapshot is never sent.
    config = _load("ftx1")
    seen: list[bytes] = []

    async def exchange(request: bytes | None, timeout: float) -> bytes | None:
        assert request is not None
        seen.append(request)
        if request == b"ID;":
            return b"ID0840;"
        if request == b"FA;":
            return b"FA14074000;"
        if request == b"MD0;":
            return b"?;"
        if request == b"KS;":
            return None
        if request in {b"ST;", b"TX;", b"PS;", b"AI;"} and seen.count(request) > 1:
            # The snapshot answers stand; the main pass below goes silent.
            return None
        return request[:-1] + b"0;"

    items = _subset(
        config,
        "get_freq",
        "get_keyer_speed",
        "get_mode",
        "get_split",
        "get_ptt",
        "get_powerstat",
        "get_auto_info",
    )
    report, delays = await _paced_run(config, exchange, gap_s=0.25, items=items)
    assert seen == [
        b"ID;", b"IF;", b"FA;", b"MD0;", b"ST;", b"TX;", b"PS;", b"AI;",
        b"FA;", b"MD0;", b"ST;", b"TX;", b"KS;", b"PS;", b"AI;",
    ]
    assert delays == [0.25] * len(seen)
    assert report.aborted == runner.SILENT_ABORT_MESSAGE
    assert [row.outcome for row in report.file.commands] == [
        "data", "refused", "silent", "silent", "silent", "silent", "silent",
    ]


async def test_identity_reports_the_answer_that_matched() -> None:
    # MOR-4464 finding 4: G7250 answers 19 00 with the echoed address
    # and 1d 19 with the model id; the file must report the matching
    # answer (parsed 7250), not the first one (parsed a0).
    config = _load("g7250")
    addr = config.civ_addr
    addr_echo = build_civ_frame(
        CONTROLLER_ADDR, addr, 0x19, sub=0x00, data=bytes((addr,))
    )
    model = build_civ_frame(
        CONTROLLER_ADDR, addr, 0x1D, data=bytes.fromhex("197250")
    )
    seen: list[bytes] = []
    report = await runner.run_probe(
        config,
        config_toml_sha256="0" * 64,
        exchange=_nak_except(
            seen,
            {
                bytes.fromhex("1900"): [addr_echo],
                bytes.fromhex("1d19"): [model],
            },
            addr=addr,
        ),
        port_kind="usb",
        baud=CIV_BAUD,
        label="",
        include_private=False,
        gap_s=GAP,
        timeout_s=SHORT_TIMEOUT,
        items=_subset(config, "get_freq"),
    )
    assert report.aborted is None
    assert report.file.radio.identity_matches_profile is True
    assert report.file.radio.identity_reported.parsed == "7250"
    assert report.file.radio.identity_reported.raw == model.hex()


async def test_identity_falls_back_to_the_first_answer() -> None:
    # MOR-4464 finding 4: when no answer matches, or all stay silent,
    # the report keeps the first answer deterministically.
    config = _load("g7250")
    addr = config.civ_addr
    wrong_model = build_civ_frame(
        CONTROLLER_ADDR, addr, 0x1D, data=bytes.fromhex("199999")
    )
    addr_echo = build_civ_frame(
        CONTROLLER_ADDR, addr, 0x19, sub=0x00, data=bytes((addr,))
    )
    seen: list[bytes] = []
    report = await runner.run_probe(
        config,
        config_toml_sha256="0" * 64,
        exchange=_nak_except(
            seen,
            {
                bytes.fromhex("1900"): [addr_echo],
                bytes.fromhex("1d19"): [wrong_model],
            },
            addr=addr,
        ),
        port_kind="usb",
        baud=CIV_BAUD,
        label="",
        include_private=False,
        gap_s=GAP,
        timeout_s=SHORT_TIMEOUT,
        items=_subset(config, "get_freq"),
    )
    assert report.file.radio.identity_matches_profile is False
    assert report.file.radio.identity_reported.raw == addr_echo.hex()
    assert report.file.radio.identity_reported.parsed == format(addr, "02x")


async def test_identity_mismatch_does_not_stop_the_run() -> None:
    config = _load("ic7300")
    fake = CivSerialFake(model="IC-7300", model_id=b"\x99", echo=False)
    reader, writer = await open_serial_port(
        "ic7300", baudrate=CIV_BAUD, opener=fake.opener
    )
    report = await runner.run_probe(
        config,
        config_toml_sha256="0" * 64,
        exchange=_civ_exchange(reader, writer, config.civ_addr),
        port_kind="usb",
        baud=CIV_BAUD,
        label="",
        include_private=False,
        gap_s=GAP,
        timeout_s=TIMEOUT,
        items=_subset(config, "get_freq"),
    )
    assert report.file.radio.identity_matches_profile is False
    assert report.file.commands[0].outcome == "data"
    assert report.aborted is None


def _canned_stub(config: RigConfig, seen: list[bytes]) -> runner.Exchange:
    nak = _nak_frame(config.civ_addr)

    async def exchange(request: bytes | None, timeout: float) -> bytes | None:
        assert request is not None
        seen.append(request)
        if config.protocol_type == "yaesu_cat":
            return b"ID0840;" if request == b"ID;" else b"?;"
        return nak

    return exchange


def _probe_toml_paths() -> list[Path]:
    return [
        path
        for path in sorted(RIGS_DIR.glob("*.toml"))
        if not path.name.startswith("_") and not path.name.endswith(".draft.toml")
    ]


def _sibling_configs() -> list[RigConfig]:
    return [load_rig(path) for path in _probe_toml_paths()]


async def test_run_records_expected_and_skipped() -> None:
    """The file marks borrowed reads absent and lists what was not sent."""
    from rigplane.probe.readlist import build_read_plan

    config = _load("g7250")
    siblings = _sibling_configs()
    plan = build_read_plan(config, siblings=siblings)
    seen: list[bytes] = []
    report = await runner.run_probe(
        config,
        config_toml_sha256="0" * 64,
        exchange=_canned_stub(config, seen),
        port_kind="usb",
        baud=CIV_BAUD,
        label="",
        include_private=False,
        gap_s=GAP,
        timeout_s=TIMEOUT,
        siblings=siblings,
    )
    assert report.file.skipped == plan.skipped
    by_name = {row.name: row for row in report.file.commands}
    assert by_name["get_data_mode"].expected == "absent"
    assert "bytes from profile(s)" in by_name["get_data_mode"].note
    assert by_name["get_freq"].expected == "present"
    assert "get_memory_contents" not in by_name
    assert {entry.reason for entry in report.file.skipped} >= {
        "private",
        "response_code",
        "unsafe_family",
        "no_unambiguous_bytes",
    }


async def test_runner_sends_only_what_readlist_produced() -> None:
    """No-TX-ever over the runner: every sent body is allow-listed.

    Declared-absent reads are asked too (borrowed sibling bytes), so the
    allow-list carries the borrowed bodies and the refused families stay
    refused first.
    """
    from rigplane.commands.command_spec import CatCommandSpec
    from rigplane.probe.readlist import (
        compile_cat_template,
        identity_requests,
        is_forbidden,
    )

    siblings = _sibling_configs()
    for toml_path in _probe_toml_paths():
        config = load_rig(toml_path)
        if config.protocol_type not in ("civ", "yaesu_cat"):
            continue
        plan = build_read_plan(config, include_private=True, siblings=siblings)
        items = list(plan.asked)
        produced = {item.body for item in items}
        identity = {item.body for item in identity_requests(config)}
        seen: list[bytes] = []
        report = await runner.run_probe(
            config,
            config_toml_sha256="0" * 64,
            exchange=_canned_stub(config, seen),
            port_kind="usb",
            baud=YAESU_BAUD,
            label="",
            include_private=True,
            gap_s=GAP,
            timeout_s=TIMEOUT,
            siblings=siblings,
        )
        assert report.file.skipped == plan.skipped
        assert seen, toml_path.name
        assert set(seen) <= produced | identity, toml_path.name
        assert report.aborted is None, toml_path.name
        if config.protocol_type == "yaesu_cat":
            reads = [
                spec.read
                for spec in config.commands.values()
                if isinstance(spec, CatCommandSpec) and spec.read
            ]
            writes = [
                spec.write
                for spec in config.commands.values()
                if isinstance(spec, CatCommandSpec) and spec.write
            ]
            read_matchers = [compile_cat_template(t) for t in reads]
            write_matchers = [compile_cat_template(t) for t in writes]
            for body in seen:
                line = body.decode("ascii")
                assert any(p.fullmatch(line) for p in read_matchers), line
                assert not any(p.fullmatch(line) for p in write_matchers), line
        else:
            allowed = build_allowed_bodies(
                config.to_profile(),
                include_private=True,
                borrowed_bodies=[item.body for item in items],
            )
            for body in seen:
                assert not is_forbidden(body, allowed), body.hex()
