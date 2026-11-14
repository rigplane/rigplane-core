"""Runner behavior against the repo simulators (MOR-4464 PR A).

Yaesu runs go through ``FakeCatRadio`` over a real ``socket://`` serial
open; CI-V runs go through ``CivSerialFake`` with the production open.
Unit-level fault cases use tiny scripted exchanges so the run stays fast.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

from rigplane.commands import (
    CONTROLLER_ADDR,
    build_civ_frame,
    command_carries_sub,
)
from rigplane.core.serial_open import open_serial_port
from rigplane.profiles.rig_loader import RigConfig, load_rig
from rigplane.probe import runner
from rigplane.probe.readlist import build_read_list
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
    return [item for item in build_read_list(config) if item.name in wanted]


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


async def _run_yaesu(config: RigConfig, radio: FakeCatRadio, **kwargs) -> runner.ProbeRun:
    reader, writer = await open_serial_port(
        radio.device_url, baudrate=YAESU_BAUD
    )
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
    items = build_read_list(config)
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
    items = build_read_list(config)
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


async def test_silent_answer_is_recorded_not_fatal() -> None:
    config = _load("ftx1")
    radio = FakeCatRadio(profile="ftx1")
    await radio.start()
    try:
        radio.silence(30.0)
        reader, writer = await open_serial_port(
            radio.device_url, baudrate=YAESU_BAUD
        )
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
    assert [row.outcome for row in report.file.commands] == ["silent"]
    assert report.aborted is None


async def test_five_silent_in_a_row_aborts_with_plain_message() -> None:
    config = _load("ftx1")
    radio = FakeCatRadio(profile="ftx1")
    await radio.start()
    try:
        radio.silence(30.0)
        reader, writer = await open_serial_port(
            radio.device_url, baudrate=YAESU_BAUD
        )
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
    assert len(report.file.commands) == 5
    assert all(row.outcome == "silent" for row in report.file.commands)
    assert report.aborted == (
        "The radio stopped answering. "
        "Check it is on, and the port and speed are right."
    )


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
        report = await _run_yaesu(config, radio, items=_subset(config, "get_freq", "get_id"))
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
    assert [row.outcome for row in report.file.commands] == ["transport_error"]
    assert report.aborted is not None


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


async def test_runner_sends_only_what_readlist_produced() -> None:
    """No-TX-ever over the runner: every sent line is a declared read."""
    from rigplane.commands.command_spec import CatCommandSpec
    from rigplane.probe.readlist import (
        build_allowed_bodies,
        compile_cat_template,
        identity_requests,
        is_forbidden,
    )

    for toml_path in _probe_toml_paths():
        config = load_rig(toml_path)
        if config.protocol_type not in ("civ", "yaesu_cat"):
            continue
        items = build_read_list(config, include_private=True)
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
        )
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
                config.to_profile(), include_private=True
            )
            for body in seen:
                assert not is_forbidden(body, allowed), body.hex()
