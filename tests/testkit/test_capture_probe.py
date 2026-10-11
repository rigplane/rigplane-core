"""Tests for the IC-7610 capture probe list and probe driver.

``scripts/ic7610_capture/probe_list.py`` is pure: these tests pin its
profile-derived GET coverage, its cmd29 wrapping, and the hard
read-only exclusion rules. ``scripts/ic7610_capture/probe.py`` is driven
through its ``run_probes`` coroutine against a real ``MockIcomRadio``
over loopback UDP, the same fixture shape
``tests/testkit/test_simulator_faults.py`` uses.
"""

from __future__ import annotations

import importlib.util
import sys
import tomllib
from pathlib import Path

from rigplane.backends.config import LanBackendConfig
from rigplane.backends.factory import create_radio
from rigplane.commands import CONTROLLER_ADDR, build_cmd29_frame
from rigplane.profiles.rig_loader import load_rig
from rigplane_testkit.icom import FaultPlan, FaultRule, MockIcomRadio

from _perf_helpers import fast_connect

REPO_ROOT = Path(__file__).resolve().parents[2]
CAPTURE_DIR = REPO_ROOT / "scripts" / "ic7610_capture"
PROFILE_PATH = REPO_ROOT / "rigs" / "ic7610.toml"


def _load_script(name: str):
    spec = importlib.util.spec_from_file_location(name, CAPTURE_DIR / f"{name}.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


probe_list = _load_script("probe_list")
probe = _load_script("probe")


def _profile_get_count(profile_toml: dict) -> int:
    commands = dict(profile_toml["commands"])
    overrides = commands.pop("overrides", {})
    tables = (commands, overrides)
    return sum(
        1
        for table in tables
        for key, value in table.items()
        if key.startswith("get_") and isinstance(value, list)
    )


def test_probe_list_covers_profile_gets_and_excludes_forbidden() -> None:
    raw = tomllib.loads(PROFILE_PATH.read_text(encoding="utf-8"))
    profile = load_rig(PROFILE_PATH).to_profile()

    probes = probe_list.build_probe_list(profile)

    assert len(probes) >= _profile_get_count(raw)
    assert all(not probe_list.is_forbidden(p.frame_body) for p in probes)

    forbidden = ("1c0001", "1801", "1a000001", "0e01", "1701")
    for hex_body in forbidden:
        assert probe_list.is_forbidden(bytes.fromhex(hex_body)), hex_body
    allowed = ("03", "2500", "1a050092")
    for hex_body in allowed:
        assert not probe_list.is_forbidden(bytes.fromhex(hex_body)), hex_body

    bodies = {p.frame_body for p in probes}
    for hex_body in ("2500", "2501", "2600", "2601", "1900"):
        assert bytes.fromhex(hex_body) in bodies, hex_body
    for hex_body in ("1a050999", "7f", "147e", "277f"):
        assert bytes.fromhex(hex_body) in bodies, hex_body
    # The documented cmd29-freq refusal (CLAUDE.md: cmd29 does NOT work
    # for freq/mode on IC-7610), for both receivers.
    assert bytes.fromhex("290003") in bodies
    assert bytes.fromhex("290103") in bodies

    # cmd29 wrapping matches the runtime's own builder byte for byte.
    sub_wrapped = next(p for p in probes if p.name == "cmd29:get_preamp:1")
    expected = build_cmd29_frame(
        profile.civ_addr, CONTROLLER_ADDR, 0x16, sub=0x02, receiver=1
    )[4:-1]
    assert sub_wrapped.frame_body == expected

    # The 0x1C / 0x1A 0x00 profile GETs are excluded from the list.
    names = {p.name for p in probes}
    assert "get_transceiver_status" not in names
    assert "get_tx_target" not in names
    assert "get_memory_contents" not in names


async def test_driver_classifies_against_simulator() -> None:
    server = MockIcomRadio()
    await server.start()
    try:
        radio = create_radio(
            LanBackendConfig(
                host="127.0.0.1",
                port=server.control_port,
                username="testuser",
                password="testpass",
                radio_addr=0x98,
                model="IC-7610",
            )
        )
        with fast_connect():
            await radio.connect()
        try:
            subset = [
                probe.Probe("get_freq", bytes.fromhex("03"), "get"),
                probe.Probe("get_mode", bytes.fromhex("04"), "get"),
                probe.Probe("unknown:7f", bytes.fromhex("7f"), "unknown"),
                probe.Probe("unknown:147e", bytes.fromhex("147e"), "unknown"),
                probe.Probe("ptt_on", bytes.fromhex("1c0001"), "get"),
            ]
            results = await probe.run_probes(radio, subset, pace=0.0, timeout=1.0)

            # One probe with its reply dropped on the wire: silence.
            server.fault_plan = FaultPlan(
                civ_to_client=FaultRule(drop_probability=1.0, max_packets=1)
            )
            try:
                results += await probe.run_probes(
                    radio,
                    [probe.Probe("get_s_meter", bytes.fromhex("1502"), "get")],
                    pace=0.0,
                    timeout=0.5,
                )
            finally:
                server.fault_plan = None
        finally:
            await radio.disconnect()

    finally:
        await server.stop()

    by_name = {entry["name"]: entry for entry in results}
    freq = by_name["get_freq"]
    assert freq["class"] == "data"
    assert freq["reply_hex"] is not None
    assert freq["reply_hex"].startswith("fefee09803")
    assert freq["frame_hex"] == "fefe98e003fd"
    assert by_name["get_mode"]["class"] == "data"
    assert by_name["unknown:7f"]["class"] == "nak"
    assert by_name["unknown:7f"]["reply_hex"] == "fefee098fafd"
    assert by_name["unknown:147e"]["class"] == "nak"
    assert by_name["ptt_on"]["class"] == "error"
    assert by_name["ptt_on"]["reply_hex"] is None
    assert by_name["get_s_meter"]["class"] == "silence"
    assert by_name["get_s_meter"]["reply_hex"] is None

    assert probe.summarize(results) == {
        "data": 2,
        "ack": 0,
        "nak": 2,
        "silence": 1,
        "error": 1,
    }
