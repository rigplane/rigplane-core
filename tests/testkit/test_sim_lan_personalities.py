"""Declared Icom LAN personalities beside the recorded IC-7610 (MOR-3907/3910).

Customer scenarios C19 (IC-9700, dual receiver, LAN: connect, scope, VFO
tuning, RX audio, no flapping reconnect) and C22 (IC-7300MK2, single
receiver, LAN: WSJT-X split through rigctld keeps the CAT link) become
passing checks against honest "declared" personalities: one generic
profile-driven mechanism (``resolve_radio_profile`` -> ``MockIcomRadio``),
the recorded IC-7610 as the control case. No recording of either radio
exists, so fidelity is C for the personalities and A only for the IC-7610
(see ``docs/internals/sim-serve-contract.md``).
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

import pytest

from rigplane.profiles import resolve_radio_profile
from rigplane.radio import IcomRadio
from rigplane.core.radio_protocol import RadioIdentityStatus
from rigplane.rigctld.contract import RigctldConfig
from rigplane.rigctld.server import RigctldServer

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from _perf_helpers import fast_connect  # noqa: E402
from rigplane_testkit.icom.simulator import MockIcomRadio  # noqa: E402
from rigplane_testkit.sim.__main__ import build_parser  # noqa: E402
from rigplane_testkit.sim.serve import (  # noqa: E402
    DEFAULT_KEEPALIVE_HOLD_S,
    build_lan_personality,
    build_stack,
    startup_line,
)

# CLI id, capability model, banner fidelity: A only with a recording.
CASES = [
    ("ic7610", "IC-7610", "A"),
    ("ic9700", "IC-9700", "C"),
    ("ic7300mk2", "IC-7300MK2", "C"),
]


def _personality(model: str) -> MockIcomRadio:
    """The generic LAN personality for a capability model.

    Built by the same function the serve backend uses, so the CI-V address,
    receiver layout and ``19 00`` identity answer cannot drift from it.
    ``scope_stream`` is what Pro's bench launcher mounts on serve; here it
    exercises the same simulator mechanism directly.
    """
    return build_lan_personality(
        resolve_radio_profile(model=model),
        single_owner=True,
        # The serve hold (85 s): the production client renews its token only
        # every 60 s, and the constructor default (0.5 s) would free the
        # session — and with it the scope output — long before the first
        # renewal (the C19 "no flapping" scenario needs the real hold).
        keepalive_hold_s=DEFAULT_KEEPALIVE_HOLD_S,
        enable_audio=True,
        scope_stream=True,
    )


def _in_range_hz(model: str) -> int:
    """A tuning frequency inside this radio's own declared band plan."""
    profile = resolve_radio_profile(model=model)
    for freq_range in profile.freq_ranges:
        for band in freq_range.bands:
            if band.default:
                return band.default
    raise AssertionError(f"{model} profile declares no default band frequency")


# --- Banner and identity ------------------------------------------------------


@pytest.mark.parametrize(("cli_id", "model", "fidelity"), CASES)
async def test_banner_and_identity_from_profile(cli_id: str, model: str, fidelity: str):
    args = build_parser().parse_args(["serve", "--radio", cli_id])
    stack = build_stack(args)
    try:
        await stack.backend.start()
        profile = resolve_radio_profile(model=model)
        radio = stack.backend.radio
        assert isinstance(radio, MockIcomRadio)
        assert radio._radio_addr == profile.civ_addr
        assert radio._dual_receiver == (profile.receiver_count > 1)

        line = startup_line(stack.backend, args, 47000)
        assert line["radio"] == model
        assert line["fidelity"] == fidelity
        assert line["evidence"] == "simulated"
        assert line["transport"] == "lan"
        assert isinstance(line["control_port"], int) and line["control_port"] > 0

        # The 19 00 identity read answers the personality's token: the
        # profile's recorded one, or the declared-unverified convention token.
        reply = radio._dispatch_civ(0x19, b"\x00", 0xE0)
        assert reply == (
            bytes((0xFE, 0xFE, 0xE0, profile.civ_addr, 0x19, 0x00))
            + radio.model_id
            + b"\xfd"
        )
    finally:
        await stack.backend.stop()
        stack.trace.close()


# --- Identity: recorded token verifies, an unknown one stays unverified --------


@pytest.mark.parametrize(("cli_id", "model", "fidelity"), CASES)
async def test_identity_is_verified_only_from_a_recorded_token(
    cli_id: str, model: str, fidelity: str
):
    profile = resolve_radio_profile(model=model)
    sim = _personality(model)
    if profile.expected_identity_ids:
        assert sim.model_id == bytes.fromhex(profile.expected_identity_ids[0])
        assert sim.identity_source == "recorded"
        expected_status = RadioIdentityStatus.VERIFIED
    else:
        # No recorded or documented token: the personality says so, and Core
        # must classify the answer UNVERIFIED (not VERIFIED, not a mismatch).
        assert sim.identity_source == "unverified"
        expected_status = RadioIdentityStatus.UNVERIFIED
    await sim.start()
    radio = IcomRadio(
        host="127.0.0.1",
        port=sim.control_port,
        username="personality",
        password="personality",
        timeout=2.0,
        model=model,
    )
    try:
        with fast_connect():
            await radio.connect()
        await asyncio.sleep(1.0)
        identity = radio.connection_identity
        assert identity is not None and identity.status is expected_status
    finally:
        await radio.disconnect()
        await sim.stop()


def test_the_personalities_cover_both_identity_cases():
    """The suite must keep one recorded and one unverified radio."""
    known = [
        bool(resolve_radio_profile(model=m).expected_identity_ids) for _, m, _ in CASES
    ]
    assert any(known) and not all(known)


# --- Scope template retarget (the loader unit) --------------------------------


def test_scope_templates_retarget_the_recording_source():
    from rigplane_testkit.icom.simulator import _load_scope_templates

    dual = _load_scope_templates(0xA2, dual_receiver=True)
    assert set(dual) == {0x00, 0x01}
    assert all(frame[3] == 0xA2 for waves in dual.values() for frame in waves), (
        "retargeted waves must carry the personality's source address"
    )

    mono = _load_scope_templates(0xB6, dual_receiver=False)
    assert set(mono) == {0x00}, "a single-receiver personality keeps MAIN only"

    recorded = _load_scope_templates(0x98, dual_receiver=True)
    assert [f[4:] for f in dual[0x00]] == [f[4:] for f in recorded[0x00]]
    assert [f[4:] for f in dual[0x01]] == [f[4:] for f in recorded[0x01]]


# --- Seam: real IcomRadio over real UDP ---------------------------------------


@pytest.mark.parametrize(("cli_id", "model", "fidelity"), CASES)
async def test_real_radio_connects_tunes_and_holds_one_session(
    cli_id: str, model: str, fidelity: str
):
    sim = _personality(model)
    await sim.start()
    radio = IcomRadio(
        host="127.0.0.1",
        port=sim.control_port,
        username="personality",
        password="personality",
        timeout=2.0,
        model=model,
    )
    try:
        with fast_connect():
            await radio.connect()
        assert radio.connected

        hz = _in_range_hz(model)
        await radio.set_freq(hz)
        assert await radio.get_freq() == hz
        mode_name, _filter = await radio.get_mode()
        assert mode_name

        # C19: ~10 s on the wire with keepalives, no flapping reconnect —
        # the simulator saw exactly one accepted login and still holds it.
        await asyncio.sleep(10.0)
        assert radio.connected
        assert sim.login_count == 1
        assert sim.session_held
    finally:
        await radio.disconnect()
        await sim.stop()


@pytest.mark.parametrize(("cli_id", "model", "fidelity"), CASES)
async def test_scope_frames_reach_the_consumer(cli_id: str, model: str, fidelity: str):
    sim = _personality(model)
    await sim.start()
    radio = IcomRadio(
        host="127.0.0.1",
        port=sim.control_port,
        username="personality",
        password="personality",
        timeout=2.0,
        model=model,
    )
    try:
        with fast_connect():
            await radio.connect()
        profile = resolve_radio_profile(model=model)
        frames = await radio.capture_scope_frames(count=8, timeout=20.0)
        receivers = {frame.receiver for frame in frames}
        expected = {0, 1} if profile.receiver_count > 1 else {0}
        assert receivers == expected
        assert all(frame.pixels for frame in frames)
    finally:
        await radio.disconnect()
        await sim.stop()


@pytest.mark.parametrize(("cli_id", "model", "fidelity"), CASES)
async def test_rx_audio_frames_arrive_non_silent(
    cli_id: str, model: str, fidelity: str
):
    sim = _personality(model)
    await sim.start()
    radio = IcomRadio(
        host="127.0.0.1",
        port=sim.control_port,
        username="personality",
        password="personality",
        timeout=2.0,
        model=model,
    )
    try:
        with fast_connect():
            await radio.connect()
        sub = await radio.audio_session.subscribe_rx("lan-personality")
        try:
            packet = await sub.get(timeout=10.0)
            assert packet is not None
            assert packet.data and any(packet.data), "RX audio must not be silent"
        finally:
            await sub.release()
    finally:
        await radio.disconnect()
        await sim.stop()


# --- C22: MK2 WSJT-X split through rigctld over the real link -----------------


async def _exchange(writer, reader, payload: bytes, *, lines: int = 1) -> bytes:
    writer.write(payload)
    await writer.drain()
    return b"".join(
        [await asyncio.wait_for(reader.readline(), 5) for _ in range(lines)]
    )


async def test_mk2_wsjtx_split_keeps_the_cat_link():
    sim = _personality("IC-7300MK2")
    await sim.start()
    radio = IcomRadio(
        host="127.0.0.1",
        port=sim.control_port,
        username="personality",
        password="personality",
        timeout=2.0,
        model="IC-7300MK2",
    )
    await radio.connect()
    server = RigctldServer(
        radio, RigctldConfig(host="127.0.0.1", port=0, command_timeout=5.0)
    )
    server._state_store = radio.state_store
    await server.start()
    try:
        host, port = server._server.sockets[0].getsockname()[:2]
        reader, writer = await asyncio.open_connection(host, port)
        try:
            freq_before = await radio.get_freq()
            mode_before = await radio.get_mode()

            # What WSJT-X polls before it engages split: the RX VFO.
            rx_freq = (await _exchange(writer, reader, b"f\n")).splitlines()
            rx_mode = (await _exchange(writer, reader, b"m\n", lines=2)).splitlines()
            assert rx_freq == [str(freq_before).encode()]
            assert rx_mode[0] and b"RPRT" not in rx_mode[0]

            # Split=Rig: TX on VFOB in another mode (CW) while f/m poll at 1 s.
            assert await _exchange(writer, reader, b"S 1 VFOB\n") == b"RPRT 0\n"
            assert await _exchange(writer, reader, b"I 14080100\n") == b"RPRT 0\n"
            assert await _exchange(writer, reader, b"X CW 600\n") == b"RPRT 0\n"
            # ``s`` is get_split_vfo: two lines (split flag, TX VFO label).
            assert (await _exchange(writer, reader, b"s\n", lines=2)).splitlines()[
                0
            ] == b"1"
            # The writes took effect on the TX side: the requested split
            # frequency and mode read back, not merely "RPRT 0".
            assert (await _exchange(writer, reader, b"i\n")).splitlines() == [
                b"14080100"
            ]
            split_mode = (await _exchange(writer, reader, b"x\n", lines=2)).splitlines()
            assert split_mode[0] == b"CW" and split_mode[1].isdigit()

            for _ in range(5):
                # The RX side must not move while the TX VFO is in CW: every
                # poll returns the original RX frequency and mode.
                assert (await _exchange(writer, reader, b"f\n")).splitlines() == rx_freq
                assert (
                    await _exchange(writer, reader, b"m\n", lines=2)
                ).splitlines() == rx_mode
                # The production poller re-solicits transmit state every
                # cycle: without it the connect-time PTT observation goes
                # stale (TTL, provider generation) and the rigctld write
                # gate truthfully refuses DEFER writes with ERJCTED — so
                # the later ``S 0`` would fail for want of fresh RF truth,
                # not for any CAT/link defect. Never keyed here.
                reading = await radio.read_transmit_state()
                assert reading.value is False
                await asyncio.sleep(1.0)

            # Restore: split off, the original RX state reads back, no PTT
            # was ever keyed (no T command above), and one session served
            # the whole exchange.
            # Fresh RF truth for the write gate: the PTT observation TTL
            # is 1 s, so the last in-loop read aged out during the final
            # sleep (the production poller re-solicits every cycle).
            assert (await radio.read_transmit_state()).value is False
            assert await _exchange(writer, reader, b"S 0 VFOB\n") == b"RPRT 0\n"
            assert (await _exchange(writer, reader, b"s\n", lines=2)).splitlines()[
                0
            ] == b"0"
            assert await radio.get_freq() == freq_before
            assert await radio.get_mode() == mode_before
            assert sim.login_count == 1
            assert radio.connected
        finally:
            writer.close()
            await writer.wait_closed()
    finally:
        await server.stop()
        await radio.disconnect()
        await sim.stop()
