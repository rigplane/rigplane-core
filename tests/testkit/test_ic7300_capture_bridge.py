"""IC-7300 capture bridge guard: only profile GETs and scope switches pass."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

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


async def test_probe_driver_through_the_guarded_bridge_is_never_blocked(
    tmp_path,
) -> None:
    """The production client + the probe list pass the guard untouched.

    Rehearsal of the bench path on a fake: ``probe.py``'s client connects
    to the guarded recorder over ``socket://`` and every probe plus the
    client's own connect traffic reaches the radio; a blocked frame here
    would mean the bench session silently loses requests.
    """
    from rigplane.backends.config import SerialBackendConfig
    from rigplane.backends.factory import create_radio
    from rigplane_testkit.serial import CivSerialFake, SerialCivRecorder

    probe = bridge.PROBE  # the IC-7610 probe driver, loaded by bridge.py
    build_allowed_bodies = bridge.build_allowed_bodies
    fake = CivSerialFake(echo=False)
    out = tmp_path / "rec.jsonl"
    rec = SerialCivRecorder(
        "fake-tty",
        out,
        opener=fake.opener,
        client_filter=bridge.make_guard("IC-7300", 0x94).new_filter,
    )
    await rec.start()
    radio = create_radio(
        SerialBackendConfig(
            device=f"socket://127.0.0.1:{rec.listen_port}",
            baudrate=115200,
            radio_addr=0x94,
            model="IC-7300",
        )
    )
    try:
        await radio.connect()
        profile = probe.load_profile("IC-7300")
        results = await probe.run_probes(
            radio,
            probe.build_probe_list(profile)[:25],
            build_allowed_bodies(profile),
            pace=0.0,
            timeout=0.5,
        )
    finally:
        await radio.disconnect()
        await rec.stop()
    assert rec.blocked_lines == 0
    assert {r["class"] for r in results} <= {"data", "nak", "ack"}


def test_loading_the_bridge_leaves_other_radios_probe_modules_alone() -> None:
    """FTX-1's capture scripts use the same bare module names (probe, probe_list)."""
    sentinel_probe, sentinel_list = object(), object()
    saved = {n: sys.modules.get(n) for n in ("probe", "probe_list")}
    sys.modules["probe"], sys.modules["probe_list"] = sentinel_probe, sentinel_list
    try:
        fresh = _load("bridge")  # a second load while those names are taken
        assert fresh.PROBE is not sentinel_probe
        assert sys.modules["probe"] is sentinel_probe
        assert sys.modules["probe_list"] is sentinel_list
    finally:
        for name, mod in saved.items():
            if mod is None:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = mod
