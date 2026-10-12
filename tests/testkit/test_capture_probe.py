"""Tests for the IC-7610 capture probe list and probe driver.

``scripts/ic7610_capture/probe_list.py`` is pure: these tests pin its
exact profile-derived GET coverage, its cmd29 wrapping, and the
allow-list gate. ``scripts/ic7610_capture/probe.py`` is driven through
its ``run_probes`` coroutine against a real ``MockIcomRadio`` over
loopback UDP, the same fixture shape
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


def _eligible_get_names(profile_toml: dict) -> set[str]:
    """Eligible GET names computed straight from the raw profile TOML.

    Eligible: wire-declared (a list of command bytes, not an ``absent``
    marker), command byte not 0x1C / 0x1A 00 / 0x07.
    """
    commands = dict(profile_toml["commands"])
    overrides = commands.pop("overrides", {})
    effective = {**commands, **overrides}
    return {
        key
        for key, value in effective.items()
        if key.startswith("get_")
        and isinstance(value, list)
        and value[0] not in (0x07, 0x1C)
        and not (value[0] == 0x1A and len(value) > 1 and value[1] == 0x00)
    }


def test_probe_list_covers_profile_gets_and_excludes_forbidden() -> None:
    raw = tomllib.loads(PROFILE_PATH.read_text(encoding="utf-8"))
    profile = load_rig(PROFILE_PATH).to_profile()

    probes = probe_list.build_probe_list(profile)
    allowed = probe_list.build_allowed_bodies(profile)

    # Exact coverage: every eligible declared GET is probed, no others.
    assert {p.name for p in probes if p.kind == "get"} == _eligible_get_names(raw)
    assert all(not probe_list.is_forbidden(p.frame_body, allowed) for p in probes)

    # cmd29-wrapped PTT and power, and a GET body with a trailing value
    # byte (that would be a SET), are refused; declared GET bodies and
    # the declared 0x1A 0x05 control reads pass.
    refused = (
        "29001c0001",
        "29011800",
        "14010050",
        "1c0001",
        "1801",
        "1a000001",
        "0e01",
        "1701",
        "07",
        "07d0",
    )
    for hex_body in refused:
        assert probe_list.is_forbidden(bytes.fromhex(hex_body), allowed), hex_body
    for hex_body in ("03", "2500", "1a050092"):
        assert not probe_list.is_forbidden(bytes.fromhex(hex_body), allowed), hex_body

    # The 0x07 VFO-selection family is absent everywhere: not in the
    # allow-list, and no probe body (wrapped or bare) starts with 0x07.
    assert bytes.fromhex("07") not in allowed
    assert bytes.fromhex("07d0") not in allowed
    assert not any(p.frame_body[0] == 0x07 for p in probes)
    assert not any(body[0] == 0x07 for body in allowed)

    bodies = {p.frame_body for p in probes}
    for hex_body in ("2500", "2501", "2600", "2601", "1900"):
        assert bytes.fromhex(hex_body) in bodies, hex_body
    for hex_body in ("1a050999", "7f", "147e", "277f"):
        assert bytes.fromhex(hex_body) in bodies, hex_body
    # cmd29-wrapped frequency read for both receivers.
    assert bytes.fromhex("290003") in bodies
    assert bytes.fromhex("290103") in bodies

    # cmd29 wrapping matches the runtime's own builder byte for byte.
    sub_wrapped = next(p for p in probes if p.name == "cmd29:get_preamp:1")
    expected = build_cmd29_frame(
        profile.civ_addr, CONTROLLER_ADDR, 0x16, sub=0x02, receiver=1
    )[4:-1]
    assert sub_wrapped.frame_body == expected

    # The 0x1C / 0x1A 0x00 / 0x07 profile GETs are excluded from the list.
    names = {p.name for p in probes}
    assert "get_transceiver_status" not in names
    assert "get_tx_target" not in names
    assert "get_memory_contents" not in names
    assert "get_vfo" not in names
    assert "get_dual_watch" not in names
    assert "get_main_sub_band" not in names


def test_refused_command_bytes_are_refused_even_when_in_allowed() -> None:
    """The command-byte refusal fires even when the allowed set holds the body.

    The allow-list alone must never be the only gate for the refused
    families: deleting the ``_REFUSED_COMMANDS`` check in ``is_forbidden``
    fails here long before any real profile puts these bodies in the
    allowed set.
    """

    hex_bodies = ("1c00", "1c0001", "1800", "07", "07d0", "0e01")
    allowed = frozenset(bytes.fromhex(hex_body) for hex_body in hex_bodies)
    for hex_body in hex_bodies:
        assert probe_list.is_forbidden(bytes.fromhex(hex_body), allowed), hex_body


class _AckOneGetMock(MockIcomRadio):
    """Answers one GET (scope span, 0x27 0x15) with a bare ACK (0xFB)."""

    def __init__(self) -> None:
        super().__init__()
        self.ack_scope_span = False

    def _dispatch_civ(self, cmd: int, payload: bytes, from_addr: int) -> bytes | None:
        if self.ack_scope_span and cmd == 0x27 and payload == b"\x15":
            return self._civ_ack(from_addr, self._radio_addr)
        return super()._dispatch_civ(cmd, payload, from_addr)


async def test_driver_classifies_against_simulator() -> None:
    profile = load_rig(PROFILE_PATH).to_profile()
    allowed = probe_list.build_allowed_bodies(profile)
    server = _AckOneGetMock()
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
            results = await probe.run_probes(
                radio, subset, allowed, pace=0.0, timeout=1.0
            )

            # One probe with its reply dropped on the wire: silence.
            server.fault_plan = FaultPlan(
                civ_to_client=FaultRule(drop_probability=1.0, max_packets=1)
            )
            try:
                results += await probe.run_probes(
                    radio,
                    [probe.Probe("get_s_meter", bytes.fromhex("1502"), "get")],
                    allowed,
                    pace=0.0,
                    timeout=0.5,
                )
            finally:
                server.fault_plan = None

            # One GET answered with a bare ACK (FB): the data transaction
            # owns no ACK waiter, so the tracker parks it as an orphan.
            server.ack_scope_span = True
            try:
                results += await probe.run_probes(
                    radio,
                    [probe.Probe("get_scope_span", bytes.fromhex("2715"), "get")],
                    allowed,
                    pace=0.0,
                    timeout=1.0,
                )
            finally:
                server.ack_scope_span = False
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
    assert by_name["get_scope_span"]["class"] == "ack"
    assert by_name["get_scope_span"]["reply_hex"] is None

    assert probe.summarize(results) == {
        "data": 2,
        "ack": 1,
        "nak": 2,
        "silence": 1,
        "error": 1,
    }
