"""IC-705 conformance: the simulators against recorded real-radio traffic.

The fixture files in ``tests/fixtures/ic705/`` were extracted from private
recordings of the real bench IC-705 (2026-10-21; provenance in the
fixtures README). These tests pin the recorded wire facts and measure
``MockIcomRadio`` (LAN/UDP, with the recorded LAN answer table mounted
through ``tests/testkit/ic705_fakes.py: ic705_lan_fake``) and the fake
IC-705 over USB — ``CivSerialFake`` with the recorded USB answer table
mounted, built by ``tests/testkit/ic705_fakes.py: ic705_usb_fake`` —
against them, classifying each request's reply from the wire rather than
comparing state values. Only the recorded probe windows are replayed:
``probe`` (transport usb) and ``lan_probe`` (transport lan).

Known divergences live in ``conformance_ledger.json`` next to the
fixtures — currently empty: with the recorded answer tables mounted, both
fakes reproduce every replayed answer class of the probe windows. Every
divergence a test still observes must have a ledger entry whose
``recorded``/``simulator`` strings match the observation, and every
ledger entry must still be observed (the ratchet: fix the simulator,
then delete the entry). Ledger keys are the request hex on LAN and
``usb:`` + request hex for the serial fake, plus the stable names
``lan_civ_echo`` and ``usb_echo_default`` for the echo divergences.
Entries carry the fix ticket MOR-3605.

Recorded reply classes: ``nak`` (0xFA), ``ack`` (0xFB), ``none`` (the
fixture keeps no reply row for that request and the probe answers file
does not call it silence), ``silence`` (the radio never answered — the
two synthetic probes ``get_command_error_fa``/``get_command_ok_fb``,
class ``silence`` in ``probe_answers.json``/``probe_answers_lan.json``),
or ``data`` with the ``(receiver, cmd, sub, payload_len)`` signature,
where ``receiver`` is the 0x29 Command29 receiver byte (``None`` for
plain frames); a simulator that answers nothing within 0.3 s is
``silence``. Classification runs through the production
``rigplane.commands.parse_civ_frame`` — the same unwrap the radio stack
routes on. The one ``error`` probe (``get_comp_meter`` on USB, a
client-side failure) is excluded from the contract; the USB probe window
holds no row for it (whether the request reached the radio is not
established).
"""

from __future__ import annotations

import asyncio
import json
import struct
import sys
from contextlib import asynccontextmanager
from pathlib import Path

import pytest

from rigplane.commands import CONTROLLER_ADDR, build_civ_frame, parse_civ_frame

from rigplane_testkit.icom import _CMD_ACK, _CMD_NAK, _PT_DATA, MockIcomRadio
from rigplane_testkit.icom.replay import extract_civ_frame

from testkit.ic705_fakes import MODEL_ID, RADIO_ADDR, ic705_lan_fake, ic705_usb_fake
from testkit.test_simulator_faults import (
    _UdpClient,
    _conninfo,
    _header,
    _login,
    _openclose,
)

FIXTURES = Path(__file__).resolve().parent.parent / "fixtures" / "ic705"
_HOST = "127.0.0.1"

_USB_WINDOW = "probe"
_LAN_WINDOW = "lan_probe"
_ID_REQUEST = bytes.fromhex("fefea4e01900fd")


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


def _load_ledger() -> dict:
    return json.loads((FIXTURES / "conformance_ledger.json").read_text())


def _window_rows(window: str) -> list[dict]:
    return [row for row in _load_civ_pairs() if row["window"] == window]


def _probe_answer_classes(transport: str) -> dict[str, str]:
    """The probe answer class per request frame of one transport."""
    name = "probe_answers.json" if transport == "usb" else "probe_answers_lan.json"
    answers = json.loads((FIXTURES / name).read_text())["answers"]
    return {item["frame"]: item["class"] for item in answers}


def _recorded_pairs(window: str, *, echo: bool) -> list[tuple[bytes, bytes | None]]:
    """First-occurrence (request, closing reply) pairs of one window.

    Walks the window in file order: each ``c2r`` row, the LAN echo row when
    ``echo`` (pinned byte-identical by the recorded-facts test), and the
    next ``r2c`` row before the following ``c2r`` — the reply that closes
    the request, or ``None`` when no radio answer preceded the next client
    request. Repeated requests keep their first occurrence.
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
    0x29 Command29 frame unwraps to its receiver and inner command, so a
    simulator that answers a cmd29 request for the wrong receiver or with
    the wrong inner command classifies differently from the recording.
    ``receiver`` is ``None`` for plain frames.
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


def _recorded_requests(
    window: str, transport: str, *, echo: bool
) -> list[tuple[bytes, tuple]]:
    """Distinct recorded requests of a probe window, with recorded classes.

    The class comes from the closing reply row; a request with no reply row
    takes its class from the probe answers file — ``silence`` for the two
    synthetic probes the radio never answered. A probe answers row of class
    ``error`` (a client-side failure, not radio behaviour) excludes the
    request from the contract.
    """
    answer_classes = _probe_answer_classes(transport)
    out: list[tuple[bytes, tuple]] = []
    for request, reply in _recorded_pairs(window, echo=echo):
        if reply is not None:
            cls = _recorded_class(reply)
        else:
            probe_class = answer_classes.get(request.hex())
            if probe_class == "error":
                continue
            cls = ("silence",) if probe_class == "silence" else ("none",)
        out.append((request, cls))
    return out


def _recorded_lan_requests() -> list[tuple[bytes, tuple]]:
    return _recorded_requests(_LAN_WINDOW, "lan", echo=True)


def _recorded_usb_requests() -> list[tuple[bytes, tuple]]:
    return _recorded_requests(_USB_WINDOW, "usb", echo=False)


# ---------------------------------------------------------------------------
# Ledger namespaces and the ratchet
# ---------------------------------------------------------------------------


def _is_lan_request_key(key: str) -> bool:
    return not key.startswith("usb:") and key != "lan_civ_echo"


def _is_usb_key(key: str) -> bool:
    return key.startswith("usb:") or key == "usb_echo_default"


def _is_lan_echo_key(key: str) -> bool:
    return key == "lan_civ_echo"


_NAMESPACES: tuple[tuple[str, object], ...] = (
    ("lan_request", _is_lan_request_key),
    ("usb", _is_usb_key),
    ("lan_civ_echo", _is_lan_echo_key),
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


def _civ_packet(frame: bytes, *, seq: int = 1) -> bytes:
    """Wrap a CI-V frame in the 0x15-byte DATA header the CI-V port speaks.

    ``seq`` is the header sequence number. A real client numbers every
    tracked packet consecutively per connection, so the replay loop passes
    consecutive numbers from the first data seq 1 — the OpenClose consumed
    seq 0 — and single-shot callers keep the default.
    """
    pkt = _header(0x15 + len(frame), _PT_DATA, seq=seq)
    pkt[0x10] = 0xC1
    struct.pack_into("<H", pkt, 0x11, len(frame))
    pkt[0x15:] = frame
    return bytes(pkt)


@asynccontextmanager
async def _lan_sim(server: MockIcomRadio | None = None):
    """A fresh simulator (default: a plain ``MockIcomRadio``) plus a
    logged-in, stream-opened raw client."""
    if server is None:
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
    client: _UdpClient, port: int, frame: bytes, timeout: float = 0.3, *, seq: int = 1
) -> tuple[tuple, bool]:
    """Send one request; classify its reply; skip a byte-identical echo."""
    client.send(_civ_packet(frame, seq=seq), port)
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
    """Replay the recorded LAN probe requests against the LAN fake.

    The simulator is ``ic705_lan_fake()`` — ``MockIcomRadio`` with the
    recorded LAN answer table mounted — so the divergences below measure
    what the mount still misses, not what a plain simulator would NAK.

    Returns the request-keyed divergences between the recorded classes and
    the simulator's live classes. Recorded classes are built through
    :func:`_load_civ_pairs`, so a monkeypatched loader changes the recorded
    side without touching the fixture file.
    """
    observed: dict[str, tuple[str, str]] = {}
    async with _lan_sim(ic705_lan_fake()) as (server, client):
        for seq, (request, recorded_class) in enumerate(
            _recorded_lan_requests(), start=1
        ):
            sim_class, _ = await _ask_sim(client, server.civ_port, request, seq=seq)
            if sim_class != recorded_class:
                observed[request.hex()] = (
                    _describe(recorded_class),
                    _describe(sim_class),
                )
    return observed


# ---------------------------------------------------------------------------
# (a) Recorded facts about the fixture itself
# ---------------------------------------------------------------------------


async def test_fixture_recorded_echo_and_scope_facts() -> None:
    rows = _load_civ_pairs()
    windows = {
        "usb": {"probe", "scope", "readonly_session"},
        "lan": {"lan_probe", "lan_scope"},
    }
    assert {(row["transport"], row["window"]) for row in rows} == {
        (transport, window) for transport, names in windows.items() for window in names
    }
    for row in rows:
        frame = bytes.fromhex(row["frame"])
        assert frame[:2] == b"\xfe\xfe"
        assert frame[-1] == 0xFD
        assert set(row) == {"window", "transport", "t", "dir", "frame"}
    for window in ("lan_probe", "lan_scope"):
        window_rows = _window_rows(window)
        for i, row in enumerate(window_rows):
            if row["dir"] == "c2r":
                # On LAN the radio's echo of every request precedes its reply.
                assert window_rows[i + 1]["dir"] == "r2c"
                assert window_rows[i + 1]["frame"] == row["frame"]
    usb = [row for row in rows if row["transport"] == "usb"]
    requests = {row["frame"] for row in usb if row["dir"] == "c2r"}
    # On the radio's USB CI-V port the echo is off: no radio frame ever
    # equals a client request.
    assert all(row["frame"] not in requests for row in usb if row["dir"] == "r2c")
    # Scope wave shape: 11 segments per sweep on USB, one frame per sweep
    # on LAN (sequence 1 of 1).
    usb_waves = [
        frame
        for frame in (
            bytes.fromhex(row["frame"])
            for row in rows
            if row["window"] == "scope" and row["dir"] == "r2c"
        )
        if frame[4:6] == b"\x27\x00"
    ]
    assert len(usb_waves) == 5 * 11
    assert {frame[8] for frame in usb_waves} == {0x11}  # seq_max BCD 11
    lan_waves = [
        parse_civ_frame(frame)
        for frame in (
            bytes.fromhex(row["frame"])
            for row in rows
            if row["window"] == "lan_scope" and row["dir"] == "r2c"
        )
        if frame[4:6] == b"\x27\x00"
    ]
    assert len(lan_waves) == 5
    for parsed in lan_waves:
        assert parsed.command == 0x27 and parsed.sub == 0x00
        assert parsed.data[1] == 0x01 and parsed.data[2] == 0x01  # sequence 1 of 1


# ---------------------------------------------------------------------------
# (b) Identity
# ---------------------------------------------------------------------------


async def test_identity_answer_is_a4_everywhere() -> None:
    recorded = {
        transport: next(
            reply
            for request, reply in _recorded_pairs(window, echo=(transport == "lan"))
            if request == _ID_REQUEST
        )
        for transport, window in (("usb", _USB_WINDOW), ("lan", _LAN_WINDOW))
    }
    assert recorded["lan"] is not None and recorded["usb"] is not None
    assert recorded["lan"] == recorded["usb"], (
        "both transports answer the 19 00 read identically"
    )
    expected = build_civ_frame(
        CONTROLLER_ADDR, RADIO_ADDR, 0x19, sub=0x00, data=MODEL_ID
    )
    assert recorded["lan"] == expected
    # rigs/ic705.toml declares no [identity].expected_ids: the model id
    # comes from this recording, not from the profile.
    async with _lan_sim(ic705_lan_fake()) as (server, client):
        client.send(_civ_packet(_ID_REQUEST), server.civ_port)
        loop = asyncio.get_running_loop()
        deadline = loop.time() + 2.0
        while True:
            remaining = deadline - loop.time()
            assert remaining > 0, "no reply to the 19 00 read on LAN"
            frame = extract_civ_frame(await client.recv(remaining))
            if frame is None or frame == _ID_REQUEST:
                continue
            assert frame == expected
            break
    fake = ic705_usb_fake()
    reader, writer = await fake.opener(url="socket://conformance", baudrate=115200)
    writer.write(_ID_REQUEST)
    await writer.drain()
    loop = asyncio.get_running_loop()
    deadline = loop.time() + 2.0
    while True:
        remaining = deadline - loop.time()
        assert remaining > 0, "no reply to the 19 00 read on the serial fake"
        frame = bytes(await asyncio.wait_for(reader.readuntil(b"\xfd"), remaining))
        if frame == _ID_REQUEST:
            continue
        assert frame == expected
        break
    writer.close()


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
    async with _lan_sim(ic705_lan_fake()) as (server, client):
        for seq, request in enumerate(requests, start=1):
            _, echoed = await _ask_sim(client, server.civ_port, request, seq=seq)
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
# (e) Serial fake: echo default + USB replay
# ---------------------------------------------------------------------------


async def test_usb_replay_divergences_match_ledger() -> None:
    fake = ic705_usb_fake()
    reader, writer = await fake.opener(url="socket://conformance", baudrate=115200)
    observed: dict[str, tuple[str, str]] = {}
    echoed_any = False
    try:
        for request, recorded_class in _recorded_usb_requests():
            writer.write(request)
            await writer.drain()
            sim_class, echoed = await _ask_serial_fake(reader, request)
            echoed_any = echoed_any or echoed
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
# Ledger self-checks (mutation evidence, pinned as tests)
# ---------------------------------------------------------------------------


def test_every_ledger_key_has_exactly_one_owner() -> None:
    ledger = _load_ledger()
    assert isinstance(ledger, dict), "the ledger exists and is a flat map"
    for key, value in ledger.items():
        assert set(value) == {"recorded", "simulator", "ticket"}, key
        assert value["ticket"] == "MOR-3605", key
        owners = [name for name, owns in _NAMESPACES if owns(key)]
        assert len(owners) == 1, f"{key} owned by {owners}"


def test_ratchet_fails_when_a_ledger_entry_is_deleted() -> None:
    # The live ledger is empty (no divergences), so the deletion case is
    # exercised with a synthetic entry: observing a divergence whose entry
    # was deleted from the ledger must fail, naming the key.
    key = _ID_REQUEST.hex()
    observed = {key: ("nak", "ack")}
    with pytest.raises(AssertionError, match=key):
        _assert_ledger_exact(observed, lambda k: k == key, ledger={})


def test_ratchet_fails_on_a_bogus_ledger_entry() -> None:
    bogus = "fefea4e0deadbeefbeefd"
    ledger = dict(_load_ledger())
    ledger[bogus] = {
        "recorded": "nak",
        "simulator": "ack",
        "ticket": "MOR-3605",
    }
    with pytest.raises(AssertionError, match=bogus):
        _assert_ledger_exact({}, _is_lan_request_key, ledger=ledger)


async def test_ratchet_fails_on_a_corrupted_recorded_reply(monkeypatch) -> None:
    rows = [dict(row) for row in _load_civ_pairs()]
    recorded_reply = next(
        reply.hex()
        for request, reply in _recorded_pairs(_LAN_WINDOW, echo=True)
        if request == _ID_REQUEST and reply is not None
    )
    # Corrupt the recorded reply to the identity read: a NAK instead of the
    # model-id answer, in memory only — the fixture file is never edited.
    for row in rows:
        if (
            row["window"] == _LAN_WINDOW
            and row["dir"] == "r2c"
            and (row["frame"] == recorded_reply)
        ):
            row["frame"] = "fefee0a4fafd"
    monkeypatch.setattr(sys.modules[__name__], "_load_civ_pairs", lambda: rows)
    observed = await _collect_lan_divergences()
    with pytest.raises(AssertionError, match=_ID_REQUEST.hex()):
        _assert_ledger_exact(observed, _is_lan_request_key)
