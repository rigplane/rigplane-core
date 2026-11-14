"""IC-7300 capture bridge guard: only profile GETs and scope switches pass."""

from __future__ import annotations

import asyncio
import importlib.util
import sys
from pathlib import Path

from rigplane.commands import CONTROLLER_ADDR, build_civ_frame, command_carries_sub
from rigplane.core.serial_open import open_serial_port
from rigplane.probe import runner
from rigplane.probe.runner import Exchange

REPO_ROOT = Path(__file__).resolve().parents[2]


def _load(name: str):
    path = REPO_ROOT / "scripts" / "ic7300_capture" / f"{name}.py"
    spec = importlib.util.spec_from_file_location(f"ic7300_capture_{name}", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


bridge = _load("bridge")
guard = bridge.make_guard("IC-7300", 0x94).allows


def frame(body_hex: str) -> bytes:
    return bytes.fromhex("fefe94e0" + body_hex + "fd")


def test_reads_the_profile_declares_pass() -> None:
    for read in ("03", "04", "1900", "1502", "1513", "2712"):
        assert guard(frame(read)), read


def test_status_reads_pass_but_their_write_forms_do_not() -> None:
    for read in ("1c00", "1c01", "271400", "271500", "271a00", "271e0101"):
        assert guard(frame(read)), read
    for write in ("1c0001", "1c0100", "1c0101", "1c0102", "27140001", "271e010100"):
        assert not guard(frame(write)), write


def test_scope_switches_pass_and_other_scope_writes_do_not() -> None:
    assert guard(frame("271001")) and guard(frame("271100"))
    assert not guard(frame("271502"))  # scope span write-shaped
    assert not guard(frame("271002"))  # value outside the on/off pair


def test_every_write_family_is_refused() -> None:
    for write in (
        "050000071400",  # set frequency
        "0601",  # set mode
        "1c0001",  # PTT on
        "1c0101",  # tuner
        "180001",  # power on
        "07",  # VFO select
        "14010128",  # set AF level
        "0e01",  # scan
        "1a05000100",  # menu write-shaped
        "171234",  # CW send
    ):
        assert not guard(frame(write)), write


def test_wrong_addresses_and_junk_are_refused() -> None:
    assert not guard(bytes.fromhex("fefe00e003fd"))  # broadcast
    assert not guard(bytes.fromhex("fefe94e103fd"))  # not from the controller
    assert not guard(b"\xfe\xfe\xfd")


def _civ_exchange(reader, writer, radio_addr: int) -> Exchange:
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
                raw: bytes = await asyncio.wait_for(reader.readuntil(b"\xfd"), timeout)
                if sent is not None and raw == sent:
                    continue
                return raw
        except (asyncio.TimeoutError, asyncio.IncompleteReadError):
            return None

    return exchange


async def test_probe_driver_through_the_guarded_bridge_is_never_blocked(
    tmp_path,
) -> None:
    """The probe and the bridge guard pass each other untouched.

    Rehearsal of the bench path on a fake: ``rigplane probe`` connects
    to the guarded recorder over ``socket://`` and every read the
    profile lists reaches the radio; a blocked frame here would mean
    the bench session silently loses requests.
    """
    from rigplane.profiles.rig_loader import load_rig
    from rigplane_testkit.serial import CivSerialFake, SerialCivRecorder

    config = load_rig(REPO_ROOT / "rigs" / "ic7300.toml")
    fake = CivSerialFake(echo=False)
    out = tmp_path / "rec.jsonl"
    rec = SerialCivRecorder(
        "fake-tty",
        out,
        opener=fake.opener,
        client_filter=bridge.make_guard("IC-7300", 0x94).new_filter,
    )
    await rec.start()
    try:
        reader, writer = await open_serial_port(
            f"socket://127.0.0.1:{rec.listen_port}", baudrate=115200
        )
        try:
            report = await runner.run_probe(
                config,
                "0" * 64,
                exchange=_civ_exchange(reader, writer, 0x94),
                port_kind="usb",
                baud=115200,
                label="",
                include_private=False,
                gap_s=0.0,
                timeout_s=0.5,
            )
        finally:
            writer.close()
    finally:
        await rec.stop()
    assert rec.blocked_lines == 0
    assert {row.outcome for row in report.file.commands} <= {
        "data",
        "refused",
        "ack_only",
    }
