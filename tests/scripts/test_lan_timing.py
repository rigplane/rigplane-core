"""Unit tests for scripts/bench/lan_timing.py (MOR-3839, IC-705 over Wi-Fi).

No real socket is ever opened: the analyzer runs on inline datagram records,
the capture paths run against a fake LAN session double (scripted CI-V
replies), and the emergency-serial path against a fake serial module.
"""

from __future__ import annotations

import asyncio
import importlib.util
import json
import signal
import struct
import sys
import time
import tomllib
from pathlib import Path
from types import ModuleType

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[2]
_SCRIPT = _REPO_ROOT / "scripts" / "bench" / "lan_timing.py"


def _load(path: Path, name: str) -> ModuleType:
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


lt = _load(_SCRIPT, "bench_lan_timing")
# serial_timing reaches the tests through lan_timing's own import, so
# RefusalError/CivReply etc. are literally the same objects the module raises.
st = lt.st

from rigplane.commands import CONTROLLER_ADDR, build_civ_frame, parse_civ_frame  # noqa: E402
from rigplane_testkit.icom.replay import Record  # noqa: E402

RADIO_ADDR = 0xA4  # IC-705
CLIENT_ID = 0x10001
RADIO_ID = 0x20002

KEY_ON = bytes.fromhex("fefea4e01c0001fd")
KEY_OFF = bytes.fromhex("fefea4e01c0000fd")
PTT_READ = bytes.fromhex("fefea4e01c00fd")


# ── datagram builders (wire layouts from core/auth.py, core/transport.py) ──


def _header(length: int, ptype: int, seq: int, sender: int, receiver: int) -> bytearray:
    pkt = bytearray(length)
    struct.pack_into("<I", pkt, 0x00, length)
    struct.pack_into("<H", pkt, 0x04, ptype)
    struct.pack_into("<H", pkt, 0x06, seq)
    struct.pack_into("<I", pkt, 0x08, sender)
    struct.pack_into("<I", pkt, 0x0C, receiver)
    return pkt


def ping_packet(seq: int, *, reply: bool, from_radio: bool = False) -> bytes:
    """0x15-byte PING: flag 0x10 (0=request/1=reply), echo ms at 0x11."""
    sender, receiver = (RADIO_ID, CLIENT_ID) if from_radio else (CLIENT_ID, RADIO_ID)
    pkt = _header(0x15, 0x07, seq, sender, receiver)
    pkt[0x10] = 0x01 if reply else 0x00
    struct.pack_into("<I", pkt, 0x11, 12345)
    return bytes(pkt)


def data_packet(seq: int, size: int = 0x50, *, from_radio: bool = True) -> bytes:
    """ptype-0x00 tracked packet (status-sized by default), header seq at 0x06."""
    sender, receiver = (RADIO_ID, CLIENT_ID) if from_radio else (CLIENT_ID, RADIO_ID)
    return bytes(_header(size, 0x00, seq, sender, receiver))


def civ_data_packet(seq: int, civ_seq: int, frame: bytes, *, from_radio: bool) -> bytes:
    """CI-V DATA packet: 0x15 header (datalen <H @0x11, civ seq >H @0x13)."""
    total = 0x15 + len(frame)
    sender, receiver = (RADIO_ID, CLIENT_ID) if from_radio else (CLIENT_ID, RADIO_ID)
    pkt = _header(total, 0x00, seq, sender, receiver)
    pkt[0x10] = 0xC1
    struct.pack_into("<H", pkt, 0x11, len(frame))
    struct.pack_into(">H", pkt, 0x13, civ_seq)
    pkt[0x15:] = frame
    return bytes(pkt)


def retransmit_single(seq: int, *, from_radio: bool = True) -> bytes:
    sender, receiver = (RADIO_ID, CLIENT_ID) if from_radio else (CLIENT_ID, RADIO_ID)
    return bytes(_header(0x10, 0x01, seq, sender, receiver))


def retransmit_multi(pairs: list[tuple[int, int]], *, from_radio: bool = True) -> bytes:
    sender, receiver = (RADIO_ID, CLIENT_ID) if from_radio else (CLIENT_ID, RADIO_ID)
    pkt = _header(0x10 + 4 * len(pairs), 0x01, 0, sender, receiver)
    for i, (first, last) in enumerate(pairs):
        struct.pack_into("<HH", pkt, 0x10 + 4 * i, first, last)
    return bytes(pkt)


def rec(t: float, direction: str, port: str, data: bytes) -> Record:
    return Record(t=t, direction=direction, port=port, data=data)


def _req(command: int, sub: int | None = None, data: bytes | None = None) -> bytes:
    return build_civ_frame(RADIO_ADDR, CONTROLLER_ADDR, command, sub=sub, data=data)


def _rep(command: int, sub: int | None = None, data: bytes | None = None) -> bytes:
    return build_civ_frame(CONTROLLER_ADDR, RADIO_ADDR, command, sub=sub, data=data)


# ── analyzer: ping RTT pairing ─────────────────────────────────────────────


def test_ping_rtt_pairs_client_requests_with_replies() -> None:
    records = [
        rec(1.000, "c2r", "control", ping_packet(7, reply=False)),
        rec(1.150, "r2c", "control", ping_packet(7, reply=True)),
        rec(2.000, "c2r", "control", ping_packet(8, reply=False)),
        rec(2.180, "r2c", "control", ping_packet(8, reply=True)),
        rec(1.000, "c2r", "civ", ping_packet(3, reply=False)),
        rec(1.160, "r2c", "civ", ping_packet(3, reply=True)),
    ]
    assert lt.ping_rtts_ms(records, "control") == [
        pytest.approx(150.0),
        pytest.approx(180.0),
    ]
    assert lt.ping_rtts_ms(records, "civ") == [pytest.approx(160.0)]
    assert len(lt.ping_rtts_ms(records)) == 3  # all ports combined


def test_ping_rtt_ignores_radio_originated_exchanges() -> None:
    # The radio pings us every ~100 ms and we reply: the request/reply delta
    # measures OUR turnaround, not the network RTT, so it is no sample.
    records = [
        rec(1.000, "r2c", "control", ping_packet(41, reply=False, from_radio=True)),
        rec(1.001, "c2r", "control", ping_packet(41, reply=True)),
    ]
    assert lt.ping_rtts_ms(records, "control") == []


def test_ping_rtt_drops_unmatched_requests() -> None:
    records = [
        rec(1.000, "c2r", "control", ping_packet(7, reply=False)),
        # reply lost on the wire: never recorded
        rec(2.000, "c2r", "control", ping_packet(8, reply=False)),
        rec(2.150, "r2c", "control", ping_packet(8, reply=True)),
    ]
    assert lt.ping_rtts_ms(records, "control") == [pytest.approx(150.0)]


# ── analyzer: sequence tracking (loss / reorder / duplicate) ──────────────


def test_track_sequence_counts_gap_as_loss() -> None:
    stats = lt.track_sequence([1, 2, 3, 5, 6])
    assert stats.arrivals == 5
    assert stats.lost == 1  # seq 4 never arrived
    assert stats.reordered == 0
    assert stats.duplicates == 0


def test_track_sequence_counts_late_fill_as_reorder_not_loss() -> None:
    stats = lt.track_sequence([1, 2, 4, 3, 5])
    assert stats.lost == 0
    assert stats.reordered == 1  # seq 3 filled its gap AFTER seq 4 arrived


def test_track_sequence_counts_duplicates() -> None:
    stats = lt.track_sequence([1, 2, 2, 3])
    assert stats.duplicates == 1
    assert stats.lost == 0


def test_track_sequence_wraparound() -> None:
    stats = lt.track_sequence([0xFFFE, 0xFFFF, 0x0000, 0x0001])
    assert stats.lost == 0
    assert stats.reordered == 0
    assert stats.duplicates == 0


def test_retransmit_requests_single_and_multi() -> None:
    records = [
        rec(1.0, "r2c", "civ", retransmit_single(7)),
        rec(2.0, "r2c", "civ", retransmit_multi([(9, 11)])),  # 9,10,11 -> 3 seqs
        rec(3.0, "c2r", "civ", retransmit_single(2, from_radio=False)),
    ]
    assert lt.retransmit_requests(records, "civ", "r2c") == 4
    assert lt.retransmit_requests(records, "civ", "c2r") == 1
    assert lt.retransmit_requests(records, "control", "r2c") == 0


def test_interarrival_ms() -> None:
    assert lt.interarrival_ms([1.0, 1.1, 1.25, 1.35]) == [
        pytest.approx(100.0),
        pytest.approx(150.0),
        pytest.approx(100.0),
    ]
    assert lt.interarrival_ms([1.0]) == []


# ── analyzer: idle/active split ────────────────────────────────────────────


def test_split_idle_active_marks_the_quiet_window_idle() -> None:
    records: list[Record] = []
    # client CI-V requests at 1/s for 10 s, then a 30 s quiet phase
    for i in range(10):
        records.append(
            rec(
                float(i),
                "c2r",
                "civ",
                civ_data_packet(i + 1, i, _req(0x03), from_radio=False),
            )
        )
    for k in range(45):  # control pings keep flowing the whole time
        t = 0.5 + k * 1.0
        records.append(rec(t, "c2r", "control", ping_packet(k, reply=False)))
        records.append(rec(t + 0.15, "r2c", "control", ping_packet(k, reply=True)))
    records.sort(key=lambda r: r.t)
    idle, active = lt.split_idle_active(records, idle_split_s=5.0)
    idle_ts = [r.t for r in idle]
    active_ts = [r.t for r in active]
    # the split starts 5 s after the last CI-V request (t=9.0): idle from 14.0
    assert idle_ts and min(idle_ts) >= 14.0
    assert all(t < 14.0 for t in active_ts)
    # ping RTTs land in the right window
    idle_rtts = lt.ping_rtts_ms(idle, "control")
    active_rtts = lt.ping_rtts_ms(active, "control")
    assert idle_rtts and active_rtts
    assert len(idle_rtts) + len(active_rtts) == 45


# ── analyzer: full document ────────────────────────────────────────────────


def _small_recording() -> list[Record]:
    records = [
        rec(0.100, "c2r", "control", ping_packet(0, reply=False)),
        rec(0.250, "r2c", "control", ping_packet(0, reply=True)),
        rec(0.600, "c2r", "control", ping_packet(1, reply=False)),
        rec(0.780, "r2c", "control", ping_packet(1, reply=True)),
        # radio periodic status packets, ~1/s, one seq gap (3 missing)
        rec(0.10, "r2c", "control", data_packet(1)),
        rec(1.10, "r2c", "control", data_packet(2)),
        rec(2.10, "r2c", "control", data_packet(4)),  # gap: 3 lost
        rec(3.10, "r2c", "control", data_packet(5)),
        rec(3.11, "r2c", "control", data_packet(5)),  # duplicate
        # a CI-V read pair with the LAN echo, 150 ms apart
        rec(1.000, "c2r", "civ", civ_data_packet(1, 0, _req(0x03), from_radio=False)),
        rec(
            1.005, "r2c", "civ", civ_data_packet(11, 0, _req(0x03), from_radio=True)
        ),  # echo
        rec(
            1.150,
            "r2c",
            "civ",
            civ_data_packet(
                12, 1, _rep(0x03, data=bytes.fromhex("0040071400")), from_radio=True
            ),
        ),
        # a set (1C 00 00 is NOT used; use af level set) answered by ACK
        rec(
            2.000,
            "c2r",
            "civ",
            civ_data_packet(
                2, 2, _req(0x14, 0x01, bytes([0x01, 0x28])), from_radio=False
            ),
        ),
        rec(2.150, "r2c", "civ", civ_data_packet(13, 2, _rep(0xFB), from_radio=True)),
        # radio asks for a retransmit of our seq 2 -> uplink loss evidence
        rec(2.010, "r2c", "civ", retransmit_single(2)),
    ]
    records.sort(key=lambda r: r.t)
    return records


def test_analyze_recording_document_shape() -> None:
    config = st.load_profiles()["ic705"]
    doc = lt.analyze_recording(
        _small_recording(),
        radio_id="icom_ic705",
        sigs=st._civ_signatures(config),
        idle_split_s=5.0,
    )
    assert doc["schema"] == "rigplane-serial-timing/1"
    assert doc["port_kind"] == "lan-wifi"
    assert doc["radio_id"] == "icom_ic705"
    classes = doc["classes"]
    assert classes["read"]["samples_ms"] == [pytest.approx(150.0)]
    assert classes["set"]["samples_ms"] == [pytest.approx(150.0)]
    net = doc["network"]
    for key in (
        "rtt_ms",
        "jitter_ms",
        "loss_pct",
        "reorder_pct",
        "duplicate_pct",
        "idle",
        "active",
    ):
        assert key in net, key
    assert net["rtt_ms"]["median"] == pytest.approx(165.0)
    assert net["jitter_ms"] == pytest.approx(
        net["rtt_ms"]["p95"] - net["rtt_ms"]["median"]
    )
    assert net["duplicate_pct"] > 0.0  # the duplicated status packet
    # per-stream detail drives the fault-plan printer
    streams = net["streams"]
    assert streams["control:to_client"]["lost"] == 1
    assert streams["control:to_client"]["duplicates"] == 1
    assert streams["civ:to_client"]["retransmit_requests"] == 1
    assert streams["civ:from_client"]["packets"] == 2


def test_analyze_via_main_reads_jsonl(tmp_path: Path) -> None:
    path = tmp_path / "recording.jsonl"
    with path.open("w") as fh:
        for r in _small_recording():
            fh.write(
                json.dumps(
                    {"t": r.t, "dir": r.direction, "port": r.port, "hex": r.data.hex()}
                )
                + "\n"
            )
    printed: list[str] = []
    code = lt.main(["analyze", str(path), "--radio", "ic705"], out=printed.append)
    assert code == 0
    doc = json.loads(printed[0])
    assert doc["port_kind"] == "lan-wifi"
    assert doc["network"]["rtt_ms"]["n"] == 2


# ── fake LAN session (no sockets) ──────────────────────────────────────────


def make_ic705_radio(
    *,
    power_raw: int = 0,
    ptt_switch_s: float = 0.01,
    ptt_off_switch_s: float | None = None,
    reply_delay_s: float = 0.005,
    wrong_identity: bool = False,
    key_ack_lost: bool = False,
):
    """Minimal IC-705 CI-V peer over the fake session; returns (responder, state)."""
    off_switch_s = ptt_switch_s if ptt_off_switch_s is None else ptt_off_switch_s
    state = {"ptt": False, "switched_at": 0.0}

    def reply(command: int, sub: int | None, payload: bytes | None):
        return [
            (
                reply_delay_s,
                build_civ_frame(
                    CONTROLLER_ADDR, RADIO_ADDR, command, sub=sub, data=payload
                ),
            )
        ]

    def respond(req: bytes):
        frame = parse_civ_frame(req)
        cmd, sub, data = frame.command, frame.sub, frame.data
        if (cmd, sub) == (0x19, 0x00):
            return reply(0x19, 0x00, bytes([0x94 if wrong_identity else RADIO_ADDR]))
        if (cmd, sub) == (0x14, 0x0A):
            tens_units = ((power_raw % 100) // 10 << 4) | (power_raw % 10)
            return reply(0x14, 0x0A, bytes([power_raw // 100, tens_units]))
        if (cmd, sub) == (0x14, 0x01):
            if data:
                return [
                    (reply_delay_s, build_civ_frame(CONTROLLER_ADDR, RADIO_ADDR, 0xFB))
                ]
            return reply(0x14, 0x01, bytes([0x01, 0x28]))
        if (cmd, sub) == (0x1C, 0x00):
            if data:
                state["ptt"] = data == b"\x01"
                state["switched_at"] = time.monotonic()
                if key_ack_lost and data == b"\x01":
                    return []  # the radio keyed, but the ACK never came
                return [
                    (reply_delay_s, build_civ_frame(CONTROLLER_ADDR, RADIO_ADDR, 0xFB))
                ]
            switch_s = ptt_switch_s if state["ptt"] else off_switch_s
            if time.monotonic() - state["switched_at"] < switch_s:
                return reply(0x1C, 0x00, b"\x00" if state["ptt"] else b"\x01")
            return reply(0x1C, 0x00, b"\x01" if state["ptt"] else b"\x00")
        if cmd == 0x03:
            return reply(0x03, None, bytes.fromhex("0040071400"))
        if cmd == 0x04:
            return reply(0x04, None, bytes([0x01, 0x01]))
        if (cmd, sub) == (0x15, 0x02):
            return reply(0x15, 0x02, bytes([0x00, 0x80]))
        return [(reply_delay_s, build_civ_frame(CONTROLLER_ADDR, RADIO_ADDR, 0xFA))]

    return respond, state


class FakeLanSession:
    """Async stand-in for lt.LanCivSession: scripted replies, no sockets."""

    def __init__(
        self, responder=None, *, busy: bool = False, events: list | None = None
    ) -> None:
        self.sent: list[tuple[float, bytes]] = []
        self.connect_calls = 0
        self.closed = False
        self.notices: list[str] = []
        self._responder = responder
        self._busy = busy
        self._events = events  # shared ordering log ("lan_close", ...)

    async def connect(self, host, control_port, *, username, password) -> None:
        self.connect_calls += 1
        if self._busy:
            raise lt.LanSessionBusy(
                "radio reports a previous session still active (login error 0xFFFFFFFF)"
            )

    async def close(self) -> None:
        self.closed = True
        if self._events is not None:
            self._events.append("lan_close")

    async def transact(self, frame: bytes, *, timeout_s: float = 1.0):
        frame = bytes(frame)
        self.sent.append((time.monotonic(), frame))
        results = self._responder(frame) if self._responder else []
        if not results:
            raise TimeoutError(f"no reply to {frame.hex()}")
        delay_s, payload = results[0]
        await asyncio.sleep(delay_s)
        return st.CivReply(
            frame=parse_civ_frame(payload),
            raw=payload,
            write_drain_ms=0.05,
            ack_ms=delay_s * 1000.0,
            frame_ms=delay_s * 1000.0,
        )


class OrderTrackingSerial:
    """Minimal pyserial double recording dtr/rts/open ordering."""

    def __init__(self, responder=None, events: list | None = None) -> None:
        # __dict__.update bypasses the logging __setattr__: only EXTERNAL
        # dtr/rts writes (open_serial_port forcing them False) are logged.
        self.__dict__.update(
            {
                "log": [],
                "_events": events,
                "writes": [],
                "timeout": 0.05,
                "write_timeout": None,
                "dtr": True,
                "rts": True,
                "opened": False,
                "port": None,
                "baudrate": None,
                "_responder": responder,
                "_pending": [],
                "_buf": bytearray(),
            }
        )

    def __setattr__(self, name, value):
        if name in ("dtr", "rts"):
            self.log.append(("setattr", name, value, self.__dict__.get("opened")))
        super().__setattr__(name, value)

    def open(self) -> None:
        self.log.append(("open", None, None, None))
        self.opened = True
        if self._events is not None:
            self._events.append("serial_open")

    def close(self) -> None:
        self.opened = False

    def flush(self) -> None:
        pass

    def write(self, data: bytes) -> int:
        data = bytes(data)
        self.writes.append(data)
        if self._responder is not None:
            for delay_s, payload in self._responder(data):
                self._pending.append((time.perf_counter() + delay_s, payload))
        return len(data)

    def _materialize(self) -> None:
        now = time.perf_counter()
        self._pending, still = [], self._pending
        for at, payload in still:
            if at <= now:
                self._buf += payload
            else:
                self._pending.append((at, payload))

    @property
    def in_waiting(self) -> int:
        self._materialize()
        return len(self._buf)

    def read(self, n: int = 1) -> bytes:
        deadline = time.perf_counter() + (self.timeout or 0.05)
        while True:
            self._materialize()
            if len(self._buf) >= n or time.perf_counter() >= deadline:
                break
            time.sleep(0.001)
        out = bytes(self._buf[:n])
        del self._buf[:n]
        return out


class FakeSerialModule:
    def __init__(self, ser) -> None:
        self._ser = ser

    def Serial(self):  # noqa: N802 - mirrors pyserial's API
        return self._ser


# ── RX whitelist ───────────────────────────────────────────────────────────


def test_rx_whitelist_blocks_any_ptt_set_frame() -> None:
    config = st.load_profiles()["ic705"]
    reads, set_pair = lt.rx_whitelist(config)
    for ptt in (KEY_ON, KEY_OFF, _req(0x1C, 0x00, b"\x02")):
        with pytest.raises(st.RefusalError, match="PTT"):
            lt.check_rx_whitelisted(
                parse_civ_frame(ptt),
                reads=reads,
                set_pair=set_pair,
                expected_set_data=None,
            )
    # the PTT READ (empty data) is allowed
    lt.check_rx_whitelisted(
        parse_civ_frame(PTT_READ),
        reads=reads,
        set_pair=set_pair,
        expected_set_data=None,
    )


def test_rx_whitelist_allows_profile_reads_and_set_to_current_only() -> None:
    config = st.load_profiles()["ic705"]
    reads, set_pair = lt.rx_whitelist(config)
    # freq / mode / s-meter / id reads pass
    for frame in (_req(0x03), _req(0x04), _req(0x15, 0x02), _req(0x19, 0x00)):
        lt.check_rx_whitelisted(
            parse_civ_frame(frame),
            reads=reads,
            set_pair=set_pair,
            expected_set_data=None,
        )
    # set-to-current af level passes only with the value just read
    lt.check_rx_whitelisted(
        parse_civ_frame(_req(0x14, 0x01, bytes([0x01, 0x28]))),
        reads=reads,
        set_pair=set_pair,
        expected_set_data=bytes([0x01, 0x28]),
    )
    with pytest.raises(st.RefusalError):
        lt.check_rx_whitelisted(
            parse_civ_frame(_req(0x14, 0x01, bytes([0x01, 0x29]))),  # a REAL change
            reads=reads,
            set_pair=set_pair,
            expected_set_data=bytes([0x01, 0x28]),
        )
    # an undeclared / non-get command never passes
    with pytest.raises(st.RefusalError):
        lt.check_rx_whitelisted(
            parse_civ_frame(_req(0x05, data=bytes.fromhex("0040071400"))),  # set freq
            reads=reads,
            set_pair=set_pair,
            expected_set_data=None,
        )


# ── capture rx ─────────────────────────────────────────────────────────────


async def test_rx_capture_sends_only_whitelisted_frames() -> None:
    responder, _state = make_ic705_radio(reply_delay_s=0.001)
    session = FakeLanSession(responder)
    config = st.load_profiles()["ic705"]
    doc = await lt.run_rx_capture_lan(
        session=session,
        config=config,
        radio_key="ic705",
        host="192.168.55.253",
        minutes=0.01,  # 0.6 s
        pace_s=0.02,
        set_every_s=0.2,
    )
    assert doc["port_kind"] == "lan-wifi"
    reads, set_pair = lt.rx_whitelist(config)
    assert session.sent, "the capture sent nothing"
    for _t, frame_bytes in session.sent:
        frame = parse_civ_frame(frame_bytes)
        if (frame.command, frame.sub) == set_pair and frame.data:
            lt.check_rx_whitelisted(
                frame, reads=reads, set_pair=set_pair, expected_set_data=frame.data
            )
            continue
        lt.check_rx_whitelisted(
            frame, reads=reads, set_pair=set_pair, expected_set_data=None
        )
        assert not (frame.command == 0x1C and frame.data), "RX capture sent a PTT frame"
    classes = doc["classes"]
    assert "ack_read" in classes and "ptt_read_idle" in classes and "ack_set" in classes


async def test_rx_capture_quiet_phase_sends_no_civ() -> None:
    responder, _state = make_ic705_radio(reply_delay_s=0.001)
    session = FakeLanSession(responder)
    config = st.load_profiles()["ic705"]
    doc = await lt.run_rx_capture_lan(
        session=session,
        config=config,
        radio_key="ic705",
        host="192.168.55.253",
        minutes=0.03,  # 1.8 s; quiet window [0.72, 1.22]
        pace_s=0.02,
        set_every_s=0.2,
        quiet_s=0.5,
    )
    t0 = session.sent[0][0]
    quiet = [t - t0 for t, _f in session.sent]
    inside = [dt for dt in quiet if 0.73 < dt < 1.21]
    assert inside == [], f"CI-V frames during the quiet phase: {inside}"
    assert any("quiet" in note for note in doc["notes"])


def _rx_main(extra, *, session, env, printed, tmp_path, pass_mode=0o600):
    pass_file = tmp_path / "icom.pass"
    pass_file.write_text("s3cret-fake-password\n")
    pass_file.chmod(pass_mode)
    argv = [
        "capture",
        "rx",
        "--host",
        "192.168.55.253",
        "--radio",
        "ic705",
        "--pass-file",
        str(pass_file),
        "--minutes",
        "0.01",
    ] + extra
    return lt.main(argv, session_factory=lambda: session, env=env, out=printed.append)


def test_capture_rx_via_main_never_leaks_credentials(tmp_path, capsys) -> None:
    responder, _state = make_ic705_radio(reply_delay_s=0.001)
    session = FakeLanSession(responder)
    printed: list[str] = []
    password = "s3cret-fake-password"
    code = _rx_main(
        [],
        session=session,
        env={"ICOM_USER": "fake-user"},
        printed=printed,
        tmp_path=tmp_path,
    )
    assert code == 0
    doc = json.loads(printed[0])
    assert doc["port_kind"] == "lan-wifi"
    blob = json.dumps(doc) + "".join(printed) + capsys.readouterr().err
    assert password not in blob
    assert "fake-user" not in blob


def test_capture_refuses_group_readable_pass_file(tmp_path) -> None:
    responder, _state = make_ic705_radio()
    session = FakeLanSession(responder)
    printed: list[str] = []
    code = _rx_main(
        [],
        session=session,
        env={"ICOM_USER": "fake-user"},
        printed=printed,
        tmp_path=tmp_path,
        pass_mode=0o644,
    )
    assert code == 2
    assert printed == []
    assert session.connect_calls == 0


def test_capture_refuses_missing_user_env(tmp_path) -> None:
    responder, _state = make_ic705_radio()
    session = FakeLanSession(responder)
    printed: list[str] = []
    code = _rx_main([], session=session, env={}, printed=printed, tmp_path=tmp_path)
    assert code == 2
    assert printed == []
    assert session.connect_calls == 0


def test_capture_refuses_busy_session(tmp_path) -> None:
    session = FakeLanSession(busy=True)
    printed: list[str] = []
    code = _rx_main(
        [], session=session, env={"ICOM_USER": "u"}, printed=printed, tmp_path=tmp_path
    )
    assert code == 2
    assert printed == []  # refused, nothing written


# ── capture tx gating ──────────────────────────────────────────────────────


def _tx_main(extra, *, session=None, serial_module=None, env=None, printed, tmp_path):
    pass_file = tmp_path / "icom.pass"
    pass_file.write_text("s3cret-fake-password\n")
    pass_file.chmod(0o600)
    if session is None:
        responder, _state = make_ic705_radio(reply_delay_s=0.001)
        session = FakeLanSession(responder)
    argv = [
        "capture",
        "tx",
        "--host",
        "192.168.55.253",
        "--radio",
        "ic705",
        "--pass-file",
        str(pass_file),
    ] + extra
    return lt.main(
        argv,
        session_factory=lambda: session,
        serial_module=serial_module,
        env={"ICOM_USER": "u"} if env is None else env,
        out=printed.append,
    )


def test_tx_requires_arm_flags(tmp_path) -> None:
    responder, _state = make_ic705_radio()
    for extra in ([], ["--tx-armed"], ["--dummy-load-confirmed"]):
        session = FakeLanSession(responder)
        printed: list[str] = []
        code = _tx_main(extra, session=session, printed=printed, tmp_path=tmp_path)
        assert code == 2
        assert printed == []
        assert session.sent == []  # refused before any frame
        assert session.connect_calls == 0


def test_tx_refuses_non_ic705(tmp_path) -> None:
    pass_file = tmp_path / "icom.pass"
    pass_file.write_text("x\n")
    pass_file.chmod(0o600)
    session = FakeLanSession(make_ic705_radio()[0])
    printed: list[str] = []
    code = lt.main(
        [
            "capture",
            "tx",
            "--host",
            "192.168.55.253",
            "--radio",
            "ic7300",
            "--pass-file",
            str(pass_file),
            "--tx-armed",
            "--dummy-load-confirmed",
        ],
        session_factory=lambda: session,
        env={"ICOM_USER": "u"},
        out=printed.append,
    )
    assert code == 2
    assert printed == []
    assert session.connect_calls == 0


def test_tx_refuses_busy_session(tmp_path) -> None:
    session = FakeLanSession(busy=True)
    printed: list[str] = []
    code = _tx_main(
        ["--tx-armed", "--dummy-load-confirmed", "--reps", "1"],
        session=session,
        printed=printed,
        tmp_path=tmp_path,
    )
    assert code == 2
    assert printed == []


def test_tx_refuses_wrong_identity(tmp_path) -> None:
    responder, _state = make_ic705_radio(wrong_identity=True)
    session = FakeLanSession(responder)
    printed: list[str] = []
    code = _tx_main(
        ["--tx-armed", "--dummy-load-confirmed", "--reps", "1"],
        session=session,
        printed=printed,
        tmp_path=tmp_path,
    )
    assert code == 2
    assert printed == []
    assert session.closed  # session still cleaned up
    assert KEY_ON not in [f for _t, f in session.sent]


async def test_tx_refuses_power_above_profile_minimum() -> None:
    responder, _state = make_ic705_radio(power_raw=5, reply_delay_s=0.001)
    session = FakeLanSession(responder)
    config = st.load_profiles()["ic705"]
    with pytest.raises(st.RefusalError, match="power"):
        await lt.run_tx_capture_lan(
            session=session,
            config=config,
            radio_key="ic705",
            host="192.168.55.253",
            reps=1,
        )
    assert KEY_ON not in [f for _t, f in session.sent]


# ── capture tx paths ───────────────────────────────────────────────────────


def _fast_tx_kwargs() -> dict:
    return dict(
        reps=2, hold_s=0.02, poll_s=0.001, read_offset_s=0.005, watchdog_tick_s=0.001
    )


async def test_tx_happy_path_measures_switch_times() -> None:
    responder, state = make_ic705_radio(ptt_switch_s=0.01, reply_delay_s=0.001)
    session = FakeLanSession(responder)
    config = st.load_profiles()["ic705"]
    doc, code = await lt.run_tx_capture_lan(
        session=session,
        config=config,
        radio_key="ic705",
        host="192.168.55.253",
        **_fast_tx_kwargs(),
    )
    assert code == 0
    sent = [f for _t, f in session.sent]
    assert KEY_ON in sent and KEY_OFF in sent
    assert state["ptt"] is False
    classes = doc["classes"]
    for name in (
        "ack_read",
        "ack_set",
        "ptt_read_idle",
        "ptt_set_ack_on",
        "ptt_set_ack_off",
        "tx_on_switch",
        "tx_off_switch",
        "ack_read_while_switching",
    ):
        assert name in classes, name
    assert classes["tx_on_switch"]["n"] == 2
    assert classes["tx_off_switch"]["n"] == 2
    assert doc["schema"] == "rigplane-serial-timing/1"
    assert doc["port_kind"] == "lan-wifi"
    assert doc["baud"] is None


async def test_tx_watchdog_trip_unkeys_and_exits_1() -> None:
    # The fake never finishes switching (10 s): the 50 ms keyed-time cap trips
    # long before the 1.5 s switch deadline.
    responder, _state = make_ic705_radio(ptt_switch_s=10.0, reply_delay_s=0.001)
    session = FakeLanSession(responder)
    config = st.load_profiles()["ic705"]
    doc, code = await lt.run_tx_capture_lan(
        session=session,
        config=config,
        radio_key="ic705",
        host="192.168.55.253",
        reps=2,
        hold_s=0.02,
        poll_s=0.001,
        read_offset_s=0.005,
        watchdog_tick_s=0.001,
        cap_keyed_s=0.05,
    )
    assert code == 1
    sent = [f for _t, f in session.sent]
    assert KEY_ON in sent
    assert sent.count(KEY_OFF) >= 1  # emergency unkey went out over LAN
    assert any("ABORTED" in note for note in doc["notes"])
    assert any("emergency unkey" in note for note in doc["notes"])


async def test_tx_key_ack_lost_still_counts_keyed_time() -> None:
    # Keyed time counts from BEFORE the key frame is sent, so a lost ACK
    # cannot hide keyed time.
    responder, state = make_ic705_radio(
        ptt_switch_s=0.005, reply_delay_s=0.001, key_ack_lost=True
    )
    session = FakeLanSession(responder)
    config = st.load_profiles()["ic705"]
    doc, code = await lt.run_tx_capture_lan(
        session=session,
        config=config,
        radio_key="ic705",
        host="192.168.55.253",
        reps=1,
        poll_s=0.001,
        read_offset_s=0.005,
        watchdog_tick_s=0.001,
    )
    assert code == 1  # ACK timeout -> abort path
    sent = [f for _t, f in session.sent]
    assert KEY_ON in sent
    assert sent.count(KEY_OFF) >= 1
    assert state["ptt"] is False
    note = next(n for n in doc["notes"] if "total keyed time" in n)
    assert float(note.split()[-1].rstrip("s")) > 0.0


def test_tx_sigterm_during_keyed_write_aborts_and_restores_handler(tmp_path) -> None:
    previous = signal.getsignal(signal.SIGTERM)
    base_responder, state = make_ic705_radio(ptt_switch_s=0.005, reply_delay_s=0.001)

    def responder(req: bytes):
        frame = parse_civ_frame(req)
        if (frame.command, frame.sub) == (0x1C, 0x00) and frame.data == b"\x01":
            handler = signal.getsignal(signal.SIGTERM)
            assert callable(handler) and handler is not previous
            base_responder(req)  # the radio keys
            handler(signal.SIGTERM, None)  # deliver SIGTERM the way the OS would
        return base_responder(req)

    session = FakeLanSession(responder)
    printed: list[str] = []
    code = _tx_main(
        ["--tx-armed", "--dummy-load-confirmed", "--reps", "1"],
        session=session,
        printed=printed,
        tmp_path=tmp_path,
    )
    assert code == 1
    doc = json.loads(printed[0])
    assert any("KeyboardInterrupt" in note for note in doc["notes"])
    sent = [f for _t, f in session.sent]
    assert sent.count(KEY_OFF) >= 1  # emergency unkey over LAN
    assert state["ptt"] is False
    assert signal.getsignal(signal.SIGTERM) is previous  # restored in finally


def test_tx_emergency_serial_second_path_after_lan_close(tmp_path) -> None:
    # --emergency-serial: after the LAN session is closed, the USB CI-V port
    # is opened with dtr/rts False BEFORE open and the unkey frame goes out.
    events: list[str] = []
    lan_responder, _state = make_ic705_radio(reply_delay_s=0.001)

    def serial_responder(req: bytes):
        frame = parse_civ_frame(req)
        if (frame.command, frame.sub) == (0x1C, 0x00) and not frame.data:
            return [
                (
                    0.001,
                    build_civ_frame(
                        CONTROLLER_ADDR, RADIO_ADDR, 0x1C, sub=0x00, data=b"\x00"
                    ),
                )
            ]
        return [(0.001, build_civ_frame(CONTROLLER_ADDR, RADIO_ADDR, 0xFB))]

    ser = OrderTrackingSerial(serial_responder, events)
    session = FakeLanSession(lan_responder, events=events)
    printed: list[str] = []
    code = _tx_main(
        [
            "--tx-armed",
            "--dummy-load-confirmed",
            "--reps",
            "1",
            "--emergency-serial",
            "/dev/fake-usb-civ",
        ],
        session=session,
        serial_module=FakeSerialModule(ser),
        printed=printed,
        tmp_path=tmp_path,
    )
    assert code == 0
    # ordering: LAN session closed BEFORE the serial port opens
    assert events.index("lan_close") < events.index("serial_open")
    # dtr/rts forced False before open
    for entry in ser.log:
        if entry[0] != "setattr":
            continue
        _, _name, value, opened = entry
        assert value is False and opened in (None, False)
    assert KEY_OFF in [bytes(w) for w in ser.writes]
    assert ser.write_timeout == 1.0
    doc = json.loads(printed[0])
    assert any("emergency-serial" in note for note in doc["notes"])
    assert not ser.opened


# ── summarize ──────────────────────────────────────────────────────────────


def _doc_with_network() -> dict:
    records: list[Record] = []
    # control ping RTTs: 150 ms and 190 ms (median 170, p95 190)
    records += [
        rec(0.0, "c2r", "control", ping_packet(0, reply=False)),
        rec(0.150, "r2c", "control", ping_packet(0, reply=True)),
        rec(0.5, "c2r", "control", ping_packet(1, reply=False)),
        rec(0.690, "r2c", "control", ping_packet(1, reply=True)),
    ]
    # civ downlink: seqs 1,2,3,5 -> seq 4 lost; one duplicate of 5; 6 reordered late
    for i, seq in enumerate([1, 2, 3, 5, 5, 7, 6]):
        records.append(rec(1.0 + 0.1 * i, "r2c", "civ", data_packet(seq, size=0x1B)))
    # civ uplink: 20 tracked packets; the radio asks 7 and 9..10 back (3 lost)
    for seq in range(1, 21):
        records.append(
            rec(
                2.0 + 0.01 * seq,
                "c2r",
                "civ",
                civ_data_packet(seq, seq, _req(0x03), from_radio=False),
            )
        )
    records.append(rec(2.5, "r2c", "civ", retransmit_single(7)))
    records.append(rec(2.6, "r2c", "civ", retransmit_multi([(9, 10)])))
    records.sort(key=lambda r: r.t)
    config = st.load_profiles()["ic705"]
    return lt.analyze_recording(
        records,
        radio_id="icom_ic705",
        sigs=st._civ_signatures(config),
        idle_split_s=5.0,
    )


def test_fault_plan_toml_shape_and_values() -> None:
    doc = _doc_with_network()
    text = lt.fault_plan_toml([doc])
    parsed = tomllib.loads(text)
    civ_down = parsed["civ_to_client"]
    # delay = median one-way (RTT/2), jitter = p95 - p50 (brief's mapping)
    assert civ_down["delay_s"] == pytest.approx(0.170 / 2, abs=1e-6)
    assert civ_down["jitter_s"] == pytest.approx((0.190 - 0.170), abs=1e-6)
    # downlink loss: seq 4 never arrived -> 1 lost of 7 expected arrivals
    assert civ_down["drop_probability"] == pytest.approx(1 / 7, abs=1e-6)
    assert civ_down["duplicate_probability"] == pytest.approx(1 / 7, abs=1e-6)
    assert civ_down["reorder_window"] == 2  # seq 6 arrived after 7
    civ_up = parsed["civ_from_client"]
    # uplink loss: the radio retransmit-requested 3 of our 20 tracked packets
    assert civ_up["drop_probability"] == pytest.approx(3 / 20, abs=1e-6)
    assert civ_up["reorder_window"] == 0  # not observable client->radio
    for block in parsed.values():
        for field_name in (
            "delay_s",
            "jitter_s",
            "drop_probability",
            "duplicate_probability",
            "reorder_window",
        ):
            assert field_name in block, field_name


def test_timing_toml_mapping_works_for_lan_document(tmp_path) -> None:
    doc = {
        "schema": "rigplane-serial-timing/1",
        "radio_id": "icom_ic705",
        "protocol": "civ",
        "port_kind": "lan-wifi",
        "baud": None,
        "host": "bench",
        "generated_at": "2026-10-28T00:00:00+00:00",
        "git_sha": None,
        "classes": {
            "ack_read": {
                "n": 2,
                "samples_ms": [150.0, 170.0],
                "median": 160.0,
                "p95": 170.0,
                "max": 170.0,
            },
            "ptt_set_ack_on": {
                "n": 1,
                "samples_ms": [145.0],
                "median": 145.0,
                "p95": 145.0,
                "max": 145.0,
            },
            "tx_on_switch": {
                "n": 1,
                "samples_ms": [320.0],
                "median": 320.0,
                "p95": 320.0,
                "max": 320.0,
            },
        },
        "notes": [],
    }
    path = tmp_path / "doc.json"
    path.write_text(json.dumps(doc))
    printed: list[str] = []
    code = lt.main(["summarize", "--timing-toml", str(path)], out=printed.append)
    assert code == 0
    block = printed[0]
    assert "reply_latency_ms = 160.0" in block
    assert "ptt_set_ack_ms = 145.0" in block
    assert "tx_on_switch_ms = 320.0" in block


def test_summarize_fault_plan_via_main(tmp_path) -> None:
    doc = _doc_with_network()
    path = tmp_path / "doc.json"
    path.write_text(json.dumps(doc))
    printed: list[str] = []
    code = lt.main(["summarize", "--fault-plan", str(path)], out=printed.append)
    assert code == 0
    parsed = tomllib.loads("\n".join(printed))
    assert "civ_to_client" in parsed
