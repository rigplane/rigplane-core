"""IC-7610 conformance: the simulators against recorded real-radio traffic.

The four fixture files in ``tests/fixtures/ic7610/`` were extracted from
private recordings of the real bench IC-7610 (2026-10-11/12).  These tests
pin the recorded wire facts and measure ``MockIcomRadio`` (LAN/UDP) and
``CivSerialFake(model="IC-7610")`` (serial) against them, classifying each
request's reply from the wire rather than comparing state values.

Known divergences live in ``conformance_ledger.json`` next to the fixtures.
Every divergence a test still observes must have a ledger entry whose
``recorded``/``simulator`` strings match the observation, and every ledger
entry must still be observed (the ratchet: fix the simulator, then delete
the entry).  Ledger keys are the request hex on LAN, ``usb:`` + request hex
for the serial fake, ``handshake_<n>`` for positional handshake steps
(1-based), and stable names for structural divergences.  Entries carry the
fix ticket MOR-3310.

Recorded reply classes: ``nak`` (0xFA), ``ack`` (0xFB), ``none`` (the
fixture keeps no reply row for that request — the recording shows no radio
answer before the next client request), or ``data`` with the
``(receiver, cmd, sub, payload_len)`` signature, where ``receiver`` is the
0x29 Command29 receiver byte (``None`` for plain frames); a simulator that
answers nothing within 0.3 s is ``silence``. Classification runs through
the production ``rigplane.commands.parse_civ_frame`` — the same unwrap the
radio stack routes on — so a 0x29 frame yields its receiver and inner
command, and a simulator answer for the wrong receiver or the wrong inner
command no longer matches.

The scope wave-on/off SETs (``27 10``/``27 11`` with a value byte) are
excluded from the answer-class check: their answer is the wave stream
itself, which the ``scope_wave`` cadence check covers.
"""

from __future__ import annotations

import asyncio
import json
import struct
import sys
from contextlib import asynccontextmanager
from itertools import zip_longest
from pathlib import Path

import pytest

from rigplane.commands import (
    CONTROLLER_ADDR,
    build_civ_frame,
    parse_civ_frame,
)
from rigplane.profiles import resolve_radio_profile
from rigplane.radio import IcomRadio
from rigplane.rig_loader import load_rig
from rigplane.scope import ScopeAssembler

from rigplane_testkit.icom import (
    _CMD_ACK,
    _CMD_NAK,
    _PT_DATA,
    _TOKEN_RENEW,
    IcomSessionRecorder,
    MockIcomRadio,
)
from rigplane_testkit.icom.fixtures import control_structure
from rigplane_testkit.icom.replay import Recording, extract_civ_frame
from rigplane_testkit.serial import CivSerialFake

from _perf_helpers import fast_connect
from testkit.test_simulator_drop_session import _login_and_claim
from testkit.test_simulator_faults import (
    _UdpClient,
    _conninfo,
    _header,
    _login,
    _openclose,
)

FIXTURES = Path(__file__).resolve().parent.parent / "fixtures" / "ic7610"
REPO_ROOT = Path(__file__).resolve().parent.parent.parent
_HOST = "127.0.0.1"

_LAN_WINDOWS = ("probe", "readonly_session", "rx_write_restore", "scope")
_USB_WINDOW = "usb_session"
_ID_REQUEST = bytes.fromhex("fefe98e01900fd")


# ---------------------------------------------------------------------------
# Fixture loading and recorded-fact helpers
# ---------------------------------------------------------------------------


def _load_civ_pairs() -> list[dict]:
    """Rows of civ_pairs.jsonl (patchable for mutation tests)."""
    return [
        json.loads(line)
        for line in (FIXTURES / "civ_pairs.jsonl").read_text().splitlines()
        if line
    ]


def _load_control_windows() -> list[dict]:
    return [
        json.loads(line)
        for line in (FIXTURES / "control_windows.jsonl").read_text().splitlines()
        if line
    ]


def _load_ledger() -> dict:
    return json.loads((FIXTURES / "conformance_ledger.json").read_text())


def _window_rows(window: str) -> list[dict]:
    return [row for row in _load_civ_pairs() if row["window"] == window]


def _recorded_pairs(window: str, *, echo: bool) -> list[tuple[bytes, bytes | None]]:
    """First-occurrence (request, closing reply) pairs of one window.

    Walks the window in file order: each ``c2r`` row, the LAN echo row when
    ``echo`` (pinned byte-identical by the recorded-facts test), and the
    next ``r2c`` row before the following ``c2r`` — the reply that closes
    the request, or ``None`` when no radio answer preceded the next client
    request.  Repeated requests keep their first occurrence.
    """
    rows = _window_rows(window)
    pairs: list[tuple[bytes, bytes | None]] = []
    seen: set[bytes] = set()
    i = 0
    while i < len(rows):
        if rows[i]["dir"] != "c2r":
            i += 1
            continue
        request = bytes.fromhex(rows[i]["frame"])
        i += 1
        if echo:
            i += 1
        reply = None
        if i < len(rows) and rows[i]["dir"] == "r2c":
            reply = bytes.fromhex(rows[i]["frame"])
            i += 1
        if request not in seen:
            seen.add(request)
            pairs.append((request, reply))
    return pairs


def _classify(frame: bytes) -> tuple:
    """Reply class from the wire: nak / ack / data (receiver, cmd, sub, len).

    Runs through the production ``rigplane.commands.parse_civ_frame``: a
    0x29 Command29 frame unwraps to its receiver and inner command (with
    the inner sub split by the same ``command_carries_sub`` predicate the
    parser uses), so a simulator that answers a cmd29 request for the wrong
    receiver or with the wrong inner command classifies differently from
    the recording. ``receiver`` is ``None`` for plain frames.
    """
    parsed = parse_civ_frame(frame)
    if parsed.command == _CMD_NAK:
        return ("nak",)
    if parsed.command == _CMD_ACK:
        return ("ack",)
    return ("data", parsed.receiver, parsed.command, parsed.sub, len(parsed.data))


def _recorded_class(reply: bytes | None) -> tuple:
    return _classify(reply) if reply is not None else ("none",)


def _describe(cls: tuple) -> str:
    kind = cls[0]
    if kind != "data":
        return kind
    _, receiver, cmd, sub, length = cls
    rx_text = "-" if receiver is None else f"0x{receiver:02x}"
    sub_text = "-" if sub is None else f"0x{sub:02x}"
    return f"data rx={rx_text} cmd=0x{cmd:02x} sub={sub_text} len={length}"


def _is_scope_wave_switch(request: bytes) -> bool:
    """Scope wave-on/off SETs (``27 10``/``27 11`` with a value byte).

    Answered by the wave stream itself, not by a discrete reply: the
    ``scope`` window pairs ``27 10 01`` with no reply at all and
    ``27 11 01`` with the first ``27 00`` wave frame. They are excluded
    from the answer-class check; ``test_scope_wave_...`` covers them.
    """
    return (
        len(request) == 8
        and request[4] == 0x27
        and request[5] in (0x10, 0x11)
        and request[6] in (0x00, 0x01)
    )


def _recorded_lan_requests() -> list[tuple[bytes, tuple]]:
    """Distinct recorded LAN requests across the LAN windows, file order.

    Scope wave-on/off SETs are excluded (see :func:`_is_scope_wave_switch`).
    """
    out: list[tuple[bytes, tuple]] = []
    seen: set[bytes] = set()
    for window in _LAN_WINDOWS:
        for request, reply in _recorded_pairs(window, echo=True):
            if request in seen or _is_scope_wave_switch(request):
                continue
            seen.add(request)
            out.append((request, _recorded_class(reply)))
    return out


# ---------------------------------------------------------------------------
# Ledger namespaces and the ratchet
# ---------------------------------------------------------------------------

_NAMED_IDS = frozenset(
    {"lan_civ_echo", "usb_echo_default", "token_renew_reply", "scope_wave"}
)


def _is_lan_request_key(key: str) -> bool:
    return not key.startswith(("usb:", "handshake_")) and key not in _NAMED_IDS


def _is_usb_key(key: str) -> bool:
    return key.startswith("usb:") or key == "usb_echo_default"


def _is_lan_echo_key(key: str) -> bool:
    return key == "lan_civ_echo"


def _is_handshake_key(key: str) -> bool:
    return key.startswith("handshake_")


def _is_token_renew_key(key: str) -> bool:
    return key == "token_renew_reply"


def _is_scope_wave_key(key: str) -> bool:
    return key == "scope_wave"


_NAMESPACES: tuple[tuple[str, object], ...] = (
    ("lan_request", _is_lan_request_key),
    ("usb", _is_usb_key),
    ("lan_civ_echo", _is_lan_echo_key),
    ("handshake", _is_handshake_key),
    ("token_renew_reply", _is_token_renew_key),
    ("scope_wave", _is_scope_wave_key),
)


def _assert_ledger_exact(
    observed: dict[str, tuple[str, str]],
    owns,
    *,
    ledger: dict | None = None,
) -> None:
    """Ratchet: ``observed`` divergences and the owned ledger slice match.

    Fails naming every observed divergence missing from the ledger, every
    owned ledger entry no longer observed (stale), and every entry whose
    recorded/simulator strings disagree with the observation.
    """
    if ledger is None:
        ledger = _load_ledger()
    owned = {key: value for key, value in ledger.items() if owns(key)}
    missing = {k: v for k, v in observed.items() if k not in owned}
    mismatched = {
        k: (owned[k], v)
        for k, v in observed.items()
        if k in owned and (owned[k]["recorded"], owned[k]["simulator"]) != v
    }
    stale = {k: v for k, v in owned.items() if k not in observed}
    assert not missing, "divergences missing from the ledger:\n" + "\n".join(
        f"{k}|{recorded}|{simulator}"
        for k, (recorded, simulator) in sorted(missing.items())
    )
    assert not mismatched, "ledger entries disagreeing with observation:\n" + "\n".join(
        f"{k}|{entry}|{observed}" for k, (entry, observed) in sorted(mismatched.items())
    )
    assert not stale, "stale ledger entries (fix landed; delete them):\n" + "\n".join(
        f"{k}|{value}" for k, value in sorted(stale.items())
    )


# ---------------------------------------------------------------------------
# Simulator driving
# ---------------------------------------------------------------------------


def _civ_packet(frame: bytes) -> bytes:
    """Wrap a CI-V frame in the 0x15-byte DATA header the CI-V port speaks."""
    pkt = _header(0x15 + len(frame), _PT_DATA, seq=1)
    pkt[0x10] = 0xC1
    struct.pack_into("<H", pkt, 0x11, len(frame))
    pkt[0x15:] = frame
    return bytes(pkt)


def _token_renew() -> bytes:
    """A 0x40-byte client token-renew control packet."""
    pkt = _header(0x40, _PT_DATA, seq=1)
    pkt[0x15] = _TOKEN_RENEW
    return bytes(pkt)


@asynccontextmanager
async def _lan_sim():
    """A fresh MockIcomRadio plus a logged-in, stream-opened raw client."""
    server = MockIcomRadio()
    await server.start()
    client = _UdpClient()
    await asyncio.get_running_loop().create_datagram_endpoint(
        lambda: client, local_addr=(_HOST, 0)
    )
    try:
        client.send(_login(), server.control_port)
        await client.recv()
        client.send(_conninfo(), server.control_port)
        await client.recv()
        client.send(_openclose(), server.civ_port)
        await client.recv()  # the simulator's unsolicited open marker
        yield server, client
    finally:
        client.close()
        await server.stop()


async def _ask_sim(
    client: _UdpClient, port: int, frame: bytes, timeout: float = 0.3
) -> tuple[tuple, bool]:
    """Send one request; classify its reply; skip a byte-identical echo."""
    client.send(_civ_packet(frame), port)
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    echoed = False
    while True:
        remaining = deadline - loop.time()
        if remaining <= 0:
            return ("silence",), echoed
        try:
            pkt = await client.recv(remaining)
        except TimeoutError:
            return ("silence",), echoed
        reply = extract_civ_frame(pkt)
        if reply is None:
            continue
        if reply == frame:
            echoed = True
            continue
        return _classify(reply), echoed


async def _ask_serial_fake(
    reader: asyncio.StreamReader, request: bytes, timeout: float = 0.3
) -> tuple[tuple, bool]:
    """Read the fake's answer to one written request; skip its echo."""
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    echoed = False
    while True:
        remaining = deadline - loop.time()
        if remaining <= 0:
            return ("silence",), echoed
        try:
            chunk = await asyncio.wait_for(reader.readuntil(b"\xfd"), remaining)
        except TimeoutError:
            return ("silence",), echoed
        frame = bytes(chunk)
        if frame == request:
            echoed = True
            continue
        return _classify(frame), echoed


async def _collect_lan_divergences() -> dict[str, tuple[str, str]]:
    """Replay the recorded LAN requests against a fresh simulator.

    Returns the request-keyed divergences between the recorded classes and
    the simulator's live classes.  Recorded classes are built through
    :func:`_load_civ_pairs`, so a monkeypatched loader changes the recorded
    side without touching the fixture file.
    """
    observed: dict[str, tuple[str, str]] = {}
    async with _lan_sim() as (server, client):
        for request, recorded_class in _recorded_lan_requests():
            sim_class, _ = await _ask_sim(client, server.civ_port, request)
            if sim_class != recorded_class:
                observed[request.hex()] = (
                    _describe(recorded_class),
                    _describe(sim_class),
                )
    return observed


# ---------------------------------------------------------------------------
# (a) Recorded facts about the fixture itself
# ---------------------------------------------------------------------------


async def test_fixture_recorded_echo_and_framing_facts() -> None:
    rows = _load_civ_pairs()
    assert {row["window"] for row in rows} == {*_LAN_WINDOWS, _USB_WINDOW}
    for row in rows:
        frame = bytes.fromhex(row["frame"])
        assert frame[:2] == b"\xfe\xfe"
        assert frame[-1] == 0xFD
        assert set(row) == {"window", "transport", "t", "dir", "frame"}
    for window in _LAN_WINDOWS:
        assert all(row["transport"] == "lan" for row in rows if row["window"] == window)
        window_rows = [row for row in rows if row["window"] == window]
        for i, row in enumerate(window_rows):
            if row["dir"] == "c2r":
                # On LAN the radio's echo of every request precedes its reply.
                assert window_rows[i + 1]["dir"] == "r2c"
                assert window_rows[i + 1]["frame"] == row["frame"]
    usb = [row for row in rows if row["window"] == _USB_WINDOW]
    assert all(row["transport"] == "usb" for row in usb)
    requests = {row["frame"] for row in usb if row["dir"] == "c2r"}
    # On the radio's USB CI-V port the echo is off: no radio frame ever
    # equals a client request.
    assert all(row["frame"] not in requests for row in usb if row["dir"] == "r2c")


# ---------------------------------------------------------------------------
# (b) Identity
# ---------------------------------------------------------------------------


async def test_identity_answer_is_the_profile_id_everywhere() -> None:
    recorded = {
        transport: next(
            reply
            for request, reply in _recorded_pairs(window, echo=(transport == "lan"))
            if request == _ID_REQUEST
        )
        for transport, window in (("lan", "probe"), ("usb", _USB_WINDOW))
    }
    assert recorded["lan"] is not None and recorded["usb"] is not None
    assert recorded["lan"] == recorded["usb"], (
        "both transports answer the 19 00 read identically"
    )
    recorded_reply = recorded["lan"]
    profile = resolve_radio_profile(model="IC-7610")
    assert profile.expected_identity_ids == ("98",)
    model_id = bytes.fromhex(profile.expected_identity_ids[0])
    assert (
        build_civ_frame(
            CONTROLLER_ADDR, profile.civ_addr, 0x19, sub=0x00, data=model_id
        )
        == recorded_reply
    )
    assert MockIcomRadio().model_id == model_id
    async with _lan_sim() as (server, client):
        client.send(_civ_packet(_ID_REQUEST), server.civ_port)
        pkt = await client.recv()
        assert extract_civ_frame(pkt) == recorded_reply
    fake = CivSerialFake(model="IC-7610")
    reader, writer = await fake.opener(url="socket://conformance", baudrate=19200)
    writer.write(_ID_REQUEST)
    await writer.drain()
    assert bytes(await asyncio.wait_for(reader.readuntil(b"\xfd"), 0.3)) == (
        _ID_REQUEST
    )
    assert (
        bytes(await asyncio.wait_for(reader.readuntil(b"\xfd"), 0.3)) == recorded_reply
    )


# ---------------------------------------------------------------------------
# (c) Answer class per request, LAN
# ---------------------------------------------------------------------------


async def test_lan_answer_classes_match_ledger() -> None:
    observed = await _collect_lan_divergences()
    _assert_ledger_exact(observed, _is_lan_request_key)


# ---------------------------------------------------------------------------
# (d) LAN echo
# ---------------------------------------------------------------------------


async def test_lan_civ_echo_divergence() -> None:
    requests = [request for request, _ in _recorded_lan_requests()]
    missing = 0
    async with _lan_sim() as (server, client):
        for request in requests:
            _, echoed = await _ask_sim(client, server.civ_port, request)
            if not echoed:
                missing += 1
    total = len(requests)
    observed = (
        {}
        if missing == 0
        else {
            "lan_civ_echo": (
                f"all {total} replayed requests echoed byte-identical",
                f"{missing} of {total} replayed requests not echoed",
            )
        }
    )
    _assert_ledger_exact(observed, _is_lan_echo_key)


# ---------------------------------------------------------------------------
# (a2) Serial fake: echo default + USB replay
# ---------------------------------------------------------------------------


async def test_usb_replay_divergences_match_ledger() -> None:
    fake = CivSerialFake(model="IC-7610")
    reader, writer = await fake.opener(url="socket://conformance", baudrate=19200)
    observed: dict[str, tuple[str, str]] = {}
    echoed_any = False
    try:
        for request, reply in _recorded_pairs(_USB_WINDOW, echo=False):
            writer.write(request)
            await writer.drain()
            sim_class, echoed = await _ask_serial_fake(reader, request)
            echoed_any = echoed_any or echoed
            recorded_class = _recorded_class(reply)
            if sim_class != recorded_class:
                observed["usb:" + request.hex()] = (
                    _describe(recorded_class),
                    _describe(sim_class),
                )
    finally:
        writer.close()
    if echoed_any:
        observed["usb_echo_default"] = (
            "no r2c frame equals a request on USB",
            "CivSerialFake echoes every inbound frame by default",
        )
    _assert_ledger_exact(observed, _is_usb_key)


# ---------------------------------------------------------------------------
# (e) Handshake structure
# ---------------------------------------------------------------------------


def _control_steps(rows: list[dict]) -> list[tuple]:
    """Ordered (dir, len, type, reqtype) control steps, pings/idle dropped.

    The same rule shapes both sides of the handshake comparison: drops
    exactly what the fixture dropped (ping: type 0x07 len 0x15; idle:
    type 0x00 len 0x10) and cuts before the first client token renewal
    (c2r, 0x40 bytes, reqtype 0x05). On the wire the renewals follow the
    first CI-V data, and the recorded window keeps no other post-data
    control rows, so this cut is the recorded session's first-CI-V-data
    boundary — the same event the simulator side is cut at (see
    :func:`test_handshake_structure_matches_ledger`).
    """
    steps = []
    for row in rows:
        if row["dir"] == "c2r" and row["len"] == 0x40 and row["reqtype"] == 0x05:
            break
        if (row["len"] == 0x15 and row["type"] == 0x07) or (
            row["len"] == 0x10 and row["type"] == 0x00
        ):
            continue
        steps.append((row["dir"], row["len"], row["type"], row["reqtype"]))
    return steps


def _describe_step(step: tuple | None) -> str:
    if step is None:
        return "absent"
    direction, length, ptype, reqtype = step
    ptype_text = "-" if ptype is None else f"0x{ptype:02x}"
    reqtype_text = "-" if reqtype is None else f"0x{reqtype:02x}"
    return f"{direction} len=0x{length:02x} type={ptype_text} reqtype={reqtype_text}"


def _compare_handshake(recorded_rows: list[dict], sim_rows: list[dict]) -> dict:
    recorded = _control_steps(recorded_rows)
    simulator = _control_steps(sim_rows)
    observed: dict[str, tuple[str, str]] = {}
    for position, (rec_step, sim_step) in enumerate(
        zip_longest(recorded, simulator), start=1
    ):
        if rec_step != sim_step:
            observed[f"handshake_{position}"] = (
                _describe_step(rec_step),
                _describe_step(sim_step),
            )
    return observed


async def test_handshake_structure_matches_ledger(tmp_path) -> None:
    mock = MockIcomRadio()
    await mock.start()
    out = tmp_path / "handshake.jsonl"
    recorder = IcomSessionRecorder(_HOST, mock.control_port, out)
    await recorder.start()
    radio = IcomRadio(
        host=_HOST,
        port=recorder.control_port,
        username="testuser",
        password="testpass",
        model="IC-7610",
    )
    try:
        with fast_connect():
            await radio.connect()
        await radio.disconnect()
    finally:
        await recorder.stop()
        await mock.stop()

    records = Recording.load(out).records
    civ_start = min(
        record.t
        for record in records
        if record.port == "civ" and extract_civ_frame(record.data) is not None
    )
    # Both sides are cut at the same event: the session's first CI-V data
    # frame. The simulator side is cut here, at civ_start; the recorded
    # side is cut by _control_steps at the first token renewal, which is
    # the same boundary — on the wire the renewals follow the first CI-V
    # data, and the recorded window keeps no other post-data control rows.
    # The t-cut below also drops this session's own post-data rows (the
    # disconnect exchange); pings/idle are dropped by _control_steps.
    sim_rows = [row for row in control_structure(records) if row["t"] < civ_start]
    recorded_rows = [
        row for row in _load_control_windows() if row["window"] == "handshake"
    ]
    observed = _compare_handshake(recorded_rows, sim_rows)
    _assert_ledger_exact(observed, _is_handshake_key)


# ---------------------------------------------------------------------------
# (f) Token renew reply
# ---------------------------------------------------------------------------


async def test_token_renew_reply_divergence() -> None:
    cadence = json.loads((FIXTURES / "idle_cadence.json").read_text())
    renewals = cadence["token_renewals"]
    assert renewals, "idle_cadence.json records token renewals"
    # Recorded fact: the radio answered every renew with a 0x40-byte
    # control packet of request type 0x05. No time bound is asserted —
    # timings were recorded on a loaded host.
    assert all(entry["answered_after_s"] is not None for entry in renewals)
    count = len(renewals)
    recorded_text = (
        f"every token renew answered with a 0x40/0x05 control packet ({count}/{count})"
    )

    async with _lan_sim() as (server, client):
        client.send(_token_renew(), server.control_port)
        try:
            pkt = await client.recv(0.5)
        except TimeoutError:
            pkt = None
    if pkt is not None and len(pkt) == 0x40 and pkt[0x15] == _TOKEN_RENEW:
        observed: dict[str, tuple[str, str]] = {}
    elif pkt is None:
        observed = {"token_renew_reply": (recorded_text, "renew answered with silence")}
    else:
        reqtype = pkt[0x15] if len(pkt) > 0x15 else None
        reqtype_text = "-" if reqtype is None else f"0x{reqtype:02x}"
        observed = {
            "token_renew_reply": (
                recorded_text,
                f"renew answered with len=0x{len(pkt):02x} reqtype={reqtype_text}",
            )
        }
    _assert_ledger_exact(observed, _is_token_renew_key)


# ---------------------------------------------------------------------------
# (g) Scope wave
# ---------------------------------------------------------------------------


def _bcd_decode_byte(byte: int) -> int:
    """Decode one BCD byte to decimal; mirrors the production scope
    parser's own decoder (``rigplane.scope._bcd_byte_decode``)."""
    return ((byte >> 4) & 0x0F) * 10 + (byte & 0x0F)


async def test_scope_wave_decodes_and_simulator_sends_none() -> None:
    bounds = load_rig(REPO_ROOT / "rigs" / "ic7610.toml").spectrum
    assert set(bounds) == {"seq_max", "amp_max", "data_len_max"}
    wave_frames = [frame for _t, _direction, frame in _scope_radio_frames("scope")]
    assert len(wave_frames) == 5
    assembler = ScopeAssembler()
    for frame in wave_frames:
        parsed = parse_civ_frame(frame)
        receiver = parsed.data[0]
        seq = _bcd_decode_byte(parsed.data[1])
        seq_max = _bcd_decode_byte(parsed.data[2])
        assert 1 <= seq <= bounds["seq_max"]
        assert seq <= seq_max <= bounds["seq_max"]
        scope_frame = assembler.feed(parsed.data[1:], receiver)
        assert scope_frame is not None
        assert len(scope_frame.pixels) <= bounds["data_len_max"]
        assert max(scope_frame.pixels) <= bounds["amp_max"]

    expected = len(wave_frames)
    wave_on = [
        request
        for request, _reply in _recorded_pairs("scope", echo=True)
        if _is_scope_wave_switch(request)
    ]
    assert len(wave_on) == 2
    saw_wave = 0
    async with _lan_sim() as (server, client):
        for request in wave_on:
            client.send(_civ_packet(request), server.civ_port)
        loop = asyncio.get_running_loop()
        deadline = loop.time() + 0.5
        while True:
            remaining = deadline - loop.time()
            if remaining <= 0:
                break
            try:
                pkt = await client.recv(remaining)
            except TimeoutError:
                break
            frame = extract_civ_frame(pkt)
            if frame is None:
                continue
            try:
                parsed_wave = parse_civ_frame(frame)
            except ValueError:
                continue
            if parsed_wave.command == 0x27 and parsed_wave.sub == 0x00:
                saw_wave += 1
    observed = (
        {
            "scope_wave": (
                f"{expected} complete 27 00 wave frames follow the wave-on requests",
                f"{saw_wave} complete 27 00 wave frames after wave-on",
            )
        }
        if saw_wave < expected
        else {}
    )
    _assert_ledger_exact(observed, _is_scope_wave_key)


def _scope_radio_frames(window: str) -> list[tuple[float, str, bytes]]:
    """The recorded ``27 00`` wave frames of a window, in file order."""
    out = []
    for row in _window_rows(window):
        if row["dir"] == "r2c":
            frame = bytes.fromhex(row["frame"])
            if frame[4] == 0x27 and len(frame) > 5 and frame[5] == 0x00:
                out.append((row["t"], row["dir"], frame))
    return out


# ---------------------------------------------------------------------------
# (h) Power-off notice
# ---------------------------------------------------------------------------


async def test_power_off_notice_shape_matches_recording() -> None:
    notices = [
        row
        for row in _load_control_windows()
        if row["window"] == "power_cycle" and row["dir"] == "r2c" and row["len"] == 0x90
    ]
    assert notices, "the power_cycle window records radio 0x90 notices"
    recorded_shape = (
        notices[-1]["len"],
        notices[-1]["type"],
        notices[-1]["reqtype"],
    )
    assert recorded_shape == (0x90, 0x00, 0x00)

    for trigger in ("reboot", "drop_session"):
        server = MockIcomRadio(single_owner=True, keepalive_hold_s=5.0)
        await server.start()
        client = _UdpClient()
        await asyncio.get_running_loop().create_datagram_endpoint(
            lambda: client, local_addr=(_HOST, 0)
        )
        try:
            await _login_and_claim(client, server)
            if trigger == "reboot":
                server.reboot(at=0.05, down_for=0.4, busy_for=0.0, notice=True)
            else:
                server.drop_session(at=0.05, notice=True)
            notice = await client.recv(timeout=2.0)
            assert len(notice) == 0x90
            assert struct.unpack_from("<H", notice, 0x04)[0] == 0x00
            assert notice[0x15] == 0x00
            assert (
                len(notice),
                struct.unpack_from("<H", notice, 0x04)[0],
                notice[0x15],
            ) == recorded_shape
        finally:
            client.close()
            await server.stop()


# ---------------------------------------------------------------------------
# Ledger self-checks (mutation evidence, pinned as tests)
# ---------------------------------------------------------------------------


def test_every_ledger_key_has_exactly_one_owner() -> None:
    ledger = _load_ledger()
    assert ledger, "the ledger exists and is non-empty"
    for key, value in ledger.items():
        assert set(value) == {"recorded", "simulator", "ticket"}, key
        assert value["ticket"] == "MOR-3310", key
        owners = [name for name, owns in _NAMESPACES if owns(key)]
        assert len(owners) == 1, f"{key} owned by {owners}"


def test_ratchet_fails_when_a_ledger_entry_is_deleted() -> None:
    ledger = _load_ledger()
    key = sorted(ledger)[0]
    observed = {
        key: (ledger[key]["recorded"], ledger[key]["simulator"]),
    }
    trimmed = {k: v for k, v in ledger.items() if k != key}
    with pytest.raises(AssertionError, match=key):
        _assert_ledger_exact(observed, lambda k: k == key, ledger=trimmed)


def test_ratchet_fails_on_a_bogus_ledger_entry() -> None:
    bogus = "fefe98e0deadbeefbeefd"
    ledger = dict(_load_ledger())
    ledger[bogus] = {
        "recorded": "nak",
        "simulator": "ack",
        "ticket": "MOR-3310",
    }
    with pytest.raises(AssertionError, match=bogus):
        _assert_ledger_exact({}, _is_lan_request_key, ledger=ledger)


async def test_ratchet_fails_on_a_corrupted_recorded_reply(monkeypatch) -> None:
    rows = [dict(row) for row in _load_civ_pairs()]
    recorded_reply = next(
        reply.hex()
        for request, reply in _recorded_pairs("probe", echo=True)
        if request == _ID_REQUEST and reply is not None
    )
    # Corrupt the recorded reply to the identity read: a NAK instead of the
    # model-id answer, in memory only — the fixture file is never edited.
    for row in rows:
        if (
            row["window"] == "probe"
            and row["dir"] == "r2c"
            and (row["frame"] == recorded_reply)
        ):
            row["frame"] = "fefee098fafd"
    monkeypatch.setattr(sys.modules[__name__], "_load_civ_pairs", lambda: rows)
    observed = await _collect_lan_divergences()
    with pytest.raises(AssertionError, match=_ID_REQUEST.hex()):
        _assert_ledger_exact(observed, _is_lan_request_key)
