#!/usr/bin/env python3
"""MOR-3839 (brief C4): IC-705 over Wi-Fi LAN (Icom remote UDP) timing capture.

Same measurement as ``scripts/bench/serial_timing.py`` (which this module
IMPORTS — no second copy of the statistics/document/watchdog/safety logic),
but over the Icom LAN protocol: control port 50001, CI-V port 50002 (audio
port 50003 is never touched — no audio stream is ever opened).

Subcommands:

* ``analyze RECORDING.jsonl [--idle-split-s N]`` -- pure, offline: from an
  :class:`~rigplane_testkit.icom.recorder.IcomSessionRecorder` datagram
  recording derive ping/pong RTT distributions (control and civ), CI-V
  request->reply latency by class (serial_timing classes via
  ``derive_civ_latencies``), inter-arrival jitter of the radio's periodic
  packets, loss (sequence gaps per direction + retransmit-request counts),
  reorder (out-of-order arrivals), duplicates and per-second packet rates.
  Idle vs active windows are reported separately.
* ``capture rx`` -- RX-only session: whitelisted frames only (freq/mode/
  s-meter/PTT-READ/id reads plus set-to-current-value writes), reads paced
  at ~100 ms, one set-to-current every 2 s, a 20 s quiet phase for Wi-Fi
  power-save behaviour. Never sends 1C 00 with data or any PTT/TX/audio
  frame.
* ``capture tx`` -- same gating as the serial tool's ``capture tx``:
  IC-705 only (profile id ``icom_ic705`` AND live CI-V identity read-back
  == 0xA4), ``--tx-armed --dummy-load-confirmed``, RF power read-back must
  equal the profile minimum raw, 10 s keyed-time hard cap by a monitor
  thread, keyed time counted from BEFORE the key frame is sent until the
  radio is READ BACK as RX, ack/switch deadlines, SIGTERM -> interrupt,
  ``finally`` emergency unkey over LAN, plus an independent second path:
  ``--emergency-serial PORT`` opens that USB CI-V port after the LAN
  session is closed and repeats FE FE A4 E0 1C 00 00 FD (serial_timing
  helpers).
* ``summarize [--timing-toml] [--fault-plan] FILE...`` -- ``--timing-toml``
  is the serial tool's mapping (unchanged: the class names are identical);
  ``--fault-plan`` prints the measured network profile as TOML blocks in
  the shape of the LAN simulator's ``FaultRule`` fields. PRINT-only: this
  tool never edits profile TOMLs or faults.py.

Packet-level truth: ``capture`` tunnels the session through an in-process
:class:`IcomSessionRecorder` UDP proxy (control port rewritten on the
loopback), so every datagram is recorded and the ``network`` block of the
document comes from that recording, not from library-internal state.

Credentials: username from the environment variable named by ``--user-env``
(default ``ICOM_USER``), password from a 0600 file (``--pass-file``), like
the Core CLI. A password on argv is never accepted; neither credential is
ever printed or written to the output JSON (the recorder redacts the login
packet on disk regardless).

HARD SAFETY: identical rules to the serial tool. ``capture rx`` never keys;
``capture tx`` refuses (exit 2, nothing written) on any unmet precondition —
including the LAN lsof-equivalent: the radio's own "session held" answers
(login error 0xFFFFFFFF "previous session still active", status error
0xFDFFFFFF "foreign holder") — and unkeys in ``finally`` on any failure
(exit 1, evidence still written).
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import random
import signal
import socket
import stat
import struct
import sys
import tempfile
import time
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[2]
_SRC = _REPO_ROOT / "src"
_BENCH_DIR = Path(__file__).resolve().parent
for _p in (str(_SRC), str(_REPO_ROOT), str(_BENCH_DIR)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import serial_timing as st  # noqa: E402  the serial tool: stats/doc/watchdog/safety
from rigplane.commands import (  # noqa: E402
    CONTROLLER_ADDR,
    build_civ_frame,
    parse_civ_frame,
)
from rigplane.core.auth import (  # noqa: E402
    build_conninfo_packet,
    build_login_packet,
    parse_auth_response,
    parse_status_response,
)
from rigplane.core.types import CivFrame  # noqa: E402
from rigplane_testkit.icom.replay import (  # noqa: E402
    Record,
    Recording,
    extract_civ_frame,
)

__all__ = [
    "PORT_KIND",
    "LanCivSession",
    "LanSessionBusy",
    "SeqStats",
    "analyze_recording",
    "check_rx_whitelisted",
    "fault_plan_toml",
    "interarrival_ms",
    "main",
    "network_block",
    "per_second_rates",
    "ping_rtts_ms",
    "read_credentials",
    "retransmit_requests",
    "run_rx_capture_lan",
    "run_tx_capture_lan",
    "rx_whitelist",
    "split_idle_active",
    "st",
    "stream_stats",
    "track_sequence",
]

PORT_KIND = "lan-wifi"

EXIT_OK = st.EXIT_OK
EXIT_ABORT = st.EXIT_ABORT
EXIT_REFUSED = st.EXIT_REFUSED

# Packet constants (wire layouts: src/rigplane/core/transport.py and
# src/rigplane/runtime/_control_phase.py; CI-V DATA: runtime/_civ_rx.py:4118).
_HEADER_SIZE = 0x10
_PING_SIZE = 0x15
_PING_REQUEST = 0x00
_PING_REPLY = 0x01
_PT_DATA = 0x00
_PT_RETRANSMIT = 0x01
_PT_PING = 0x07
_CIV_DATA_HEADER = 0x15
_STATUS_SIZE = 0x50
_LOGIN_RESPONSE_SIZE = 0x60
_CONNINFO_SIZE = 0x90
_TOKEN_PACKET_SIZE = 0x40
_OPENCLOSE_SIZE = 0x16
# Same seq-gap bound the production transport uses before rebasing
# (core/transport.py MAX_MISSING).
_MAX_MISSING = 50
# Token magics (runtime/_control_phase.py): ack 0x02, remove 0x01, renew 0x05.
_TOKEN_ACK = 0x02
_TOKEN_REMOVE = 0x01
_TOKEN_RENEW = 0x05
_TOKEN_RENEWAL_S = 60.0
# Radio-side session-holder error codes (runtime/_control_phase.py and
# rigplane_testkit/icom/simulator.py _ERR_PREV_SESSION_ACTIVE).
_ERR_CREDENTIALS = 0xFEFFFFFF
_ERR_PREV_SESSION = 0xFFFFFFFF
_ERR_FOREIGN_HOLDER = 0xFDFFFFFF

_DEFAULT_REPS = 12
_KEYED_CAP_S = 10.0
_SWITCH_DEADLINE_S = 1.5
_HOLD_S = 0.3
_PTT_POLL_S = 0.02  # Wi-Fi RTT is 120-190 ms; the serial 3 ms poll is pointless
_READ_WHILE_SWITCHING_OFFSET_S = 0.02
_UNKEY_ATTEMPTS = 5
_UNKEY_GAP_S = 0.05
_QUIET_S = 20.0
_RX_PACE_S = 0.1
_RX_SET_EVERY_S = 2.0
_DEFAULT_IDLE_SPLIT_S = 5.0

#: CI-V read probes cycled by ``capture rx`` (profile command names).
_RX_PROBES = (
    "get_freq",
    "get_mode",
    "get_s_meter",
    "get_transceiver_status",  # 1C 00 PTT read
)


class LanSessionBusy(st.RefusalError):
    """The radio reports its LAN session held by another client.

    This is the LAN lsof-equivalent: the radio itself refuses the login
    (0xFFFFFFFF, previous session still active) or the conninfo status
    (0xFDFFFFFF, foreign holder), so the check works without local ``lsof``.
    """


# ── analyzer: pure datagram math ───────────────────────────────────────────


def _records_of(records: Recording | Iterable[Record]) -> list[Record]:
    if isinstance(records, Recording):
        return records.records
    return list(records)


def _hdr_type(data: bytes) -> int:
    return struct.unpack_from("<H", data, 0x04)[0]


def _hdr_seq(data: bytes) -> int:
    return struct.unpack_from("<H", data, 0x06)[0]


@dataclass(frozen=True, slots=True)
class SeqStats:
    """Sequence-space walk of one tracked (port, direction) stream."""

    arrivals: int = 0
    lost: int = 0  # gaps never filled before the recording ended
    reordered: int = 0  # gap fills that arrived AFTER a later seq
    duplicates: int = 0


def track_sequence(seqs: Iterable[int]) -> SeqStats:
    """Walk 16-bit sequence numbers; count loss, reorder, duplicates.

    A gap opens when a seq jumps forward and closes when the missing seq
    arrives later (reorder, not loss). Wrap-aware; a forward jump wider than
    ``_MAX_MISSING`` or a large backward jump rebases the stream (capture
    restart), mirroring core/transport.py ``_record_rx_seq``.
    """
    max_seq: int | None = None
    missing: set[int] = set()
    lost_base = 0
    reordered = 0
    duplicates = 0
    arrivals = 0
    for raw in seqs:
        seq = raw & 0xFFFF
        arrivals += 1
        if max_seq is None:
            max_seq = seq
            continue
        if seq in missing:
            missing.discard(seq)
            reordered += 1
            continue
        delta = (seq - max_seq) & 0xFFFF
        if delta == 0:
            duplicates += 1
        elif 1 <= delta <= _MAX_MISSING:
            for offset in range(1, delta):
                missing.add((max_seq + offset) & 0xFFFF)
            max_seq = seq
        elif delta <= 0x7FFF:
            # Large forward jump: treat the open gaps as settled loss and
            # rebase (same rule as the production transport).
            lost_base += len(missing)
            missing.clear()
            max_seq = seq
        elif delta >= 0x10000 - _MAX_MISSING:
            # Slightly behind the max but never gapped: an out-of-order
            # arrival of a packet sent before the current max.
            reordered += 1
        else:
            # Large backward jump: foreign stream / restart — rebase.
            lost_base += len(missing)
            missing.clear()
            max_seq = seq
    return SeqStats(
        arrivals=arrivals,
        lost=lost_base + len(missing),
        reordered=reordered,
        duplicates=duplicates,
    )


def ping_rtts_ms(records: Iterable[Record], port: str | None = None) -> list[float]:
    """Ping/pong RTTs (ms) from CLIENT-originated pings, per port or all.

    A sample is a c2r PING request (flag 0x00) paired with the r2c PING
    reply (flag 0x01) carrying the same sequence id. Radio-originated pings
    and our replies measure the client's turnaround, not the network, so
    they never produce a sample.
    """
    pending: dict[tuple[str, int], float] = {}
    out: list[float] = []
    for rec in records:
        if port is not None and rec.port != port:
            continue
        data = rec.data
        if len(data) != _PING_SIZE or _hdr_type(data) != _PT_PING:
            continue
        key = (rec.port, _hdr_seq(data))
        if rec.direction == "c2r" and data[0x10] == _PING_REQUEST:
            pending[key] = rec.t  # latest request with this seq wins
        elif rec.direction == "r2c" and data[0x10] == _PING_REPLY:
            sent_at = pending.pop(key, None)
            if sent_at is not None and rec.t >= sent_at:
                out.append((rec.t - sent_at) * 1000.0)
    return out


def retransmit_requests(records: Iterable[Record], port: str, direction: str) -> int:
    """Sequence ids asked back via ptype-0x01 packets on one stream.

    Single request (0x10 bytes) counts one; a multi request counts each seq
    of its (first, last) ranges, capped at ``_MAX_MISSING`` per range —
    the same reading as core/transport.py ``_handle_retransmit_packet``.
    """
    total = 0
    for rec in records:
        if rec.port != port or rec.direction != direction:
            continue
        data = rec.data
        if len(data) < _HEADER_SIZE or _hdr_type(data) != _PT_RETRANSMIT:
            continue
        if len(data) == _HEADER_SIZE:
            total += 1
            continue
        end = _HEADER_SIZE + (len(data) - _HEADER_SIZE) // 4 * 4
        for offset in range(_HEADER_SIZE, end, 4):
            first, last = struct.unpack_from("<HH", data, offset)
            span = (last - first) & 0xFFFF
            if span >= 0x8000:
                continue  # backward range: protocol says ignore
            total += min(span + 1, _MAX_MISSING)
    return total


def interarrival_ms(times: Sequence[float]) -> list[float]:
    """Successive deltas of a timestamp series, in ms."""
    return [(b - a) * 1000.0 for a, b in zip(times, times[1:])]


def per_second_rates(records: Iterable[Record]) -> dict[str, float]:
    """Mean packets/second per "port:direction" over the recording span."""
    records = _records_of(records)
    if not records:
        return {}
    span = max(max(r.t for r in records) - min(r.t for r in records), 1e-9)
    counts: dict[str, int] = {}
    for rec in records:
        key = f"{rec.port}:{rec.direction}"
        counts[key] = counts.get(key, 0) + 1
    return {key: round(n / span, 3) for key, n in sorted(counts.items())}


def split_idle_active(
    records: Iterable[Record], idle_split_s: float
) -> tuple[list[Record], list[Record]]:
    """Split records into (idle, active) windows by client CI-V traffic.

    A record is "idle" when no client CI-V request went out in the previous
    ``idle_split_s`` seconds; everything else (including the handshake
    before the first request) is "active". Wi-Fi power save shows up in the
    idle window: the only traffic then is keep-alive pings.
    """
    ordered = sorted(_records_of(records), key=lambda r: r.t)
    request_times = [
        r.t
        for r in ordered
        if r.direction == "c2r"
        and r.port == "civ"
        and extract_civ_frame(r.data) is not None
    ]
    idle: list[Record] = []
    active: list[Record] = []
    idx = 0
    last_req: float | None = None
    for rec in ordered:
        while idx < len(request_times) and request_times[idx] <= rec.t:
            last_req = request_times[idx]
            idx += 1
        if last_req is not None and rec.t - last_req >= idle_split_s:
            idle.append(rec)
        else:
            active.append(rec)
    return idle, active


def _pct(part: int, whole: int) -> float:
    return round(100.0 * part / whole, 3) if whole else 0.0


def stream_stats(records: Iterable[Record]) -> dict[str, dict[str, object]]:
    """Per "port:from_client|to_client" tracked-data stream statistics.

    Sequence math runs on ptype-0x00 packets with a nonzero header seq.
    ``retransmit_requests`` counts the ptype-0x01 requests SEEN on that
    stream — on a ``to_client`` stream those are the radio asking for our
    packets back, i.e. the only client->radio loss evidence a mid-link
    proxy can see.
    """
    records = _records_of(records)
    ports = sorted({rec.port for rec in records})
    out: dict[str, dict[str, object]] = {}
    for port in ports:
        for direction, label in (("c2r", "from_client"), ("r2c", "to_client")):
            seqs = [
                _hdr_seq(rec.data)
                for rec in records
                if rec.port == port
                and rec.direction == direction
                and len(rec.data) >= _HEADER_SIZE
                and _hdr_type(rec.data) == _PT_DATA
                and _hdr_seq(rec.data) != 0
            ]
            stats = track_sequence(seqs)
            distinct = stats.arrivals - stats.duplicates
            out[f"{port}:{label}"] = {
                "packets": stats.arrivals,
                "lost": stats.lost,
                "loss_pct": _pct(stats.lost, distinct + stats.lost),
                "reordered": stats.reordered,
                "reorder_pct": _pct(stats.reordered, stats.arrivals),
                "duplicates": stats.duplicates,
                "duplicate_pct": _pct(stats.duplicates, stats.arrivals),
                "retransmit_requests": retransmit_requests(records, port, direction),
            }
    return out


def _rtt_summary(records: Iterable[Record]) -> dict[str, object]:
    rtts = ping_rtts_ms(records)
    return st.summarize_samples(rtts) if rtts else {"n": 0}


def _net_subset(records: list[Record]) -> dict[str, object]:
    """Headline network quality for one (sub)set of records."""
    streams = stream_stats(records)
    down = {k: s for k, s in streams.items() if k.endswith(":to_client")}
    up = {k: s for k, s in streams.items() if k.endswith(":from_client")}
    down_arrivals = sum(int(s["packets"]) for s in down.values())
    down_lost = sum(int(s["lost"]) for s in down.values())
    down_dup = sum(int(s["duplicates"]) for s in down.values())
    down_reord = sum(int(s["reordered"]) for s in down.values())
    # Radio retransmit requests arrive on to_client streams but measure
    # client->radio (uplink) loss at the radio's receiver.
    up_lost = sum(int(s["retransmit_requests"]) for s in down.values())
    up_sent = sum(int(s["packets"]) for s in up.values())
    expected_down = down_arrivals - down_dup + down_lost
    total_expected = expected_down + up_sent
    rtt = _rtt_summary(records)
    jitter = (
        round(float(rtt["p95"]) - float(rtt["median"]), 3) if rtt.get("n") else None
    )
    span = 0.0
    if records:
        span = round(max(r.t for r in records) - min(r.t for r in records), 6)
    return {
        "rtt_ms": rtt,
        "jitter_ms": jitter,
        "loss_pct": _pct(down_lost + up_lost, total_expected),
        "reorder_pct": _pct(down_reord, down_arrivals),
        "duplicate_pct": _pct(down_dup, down_arrivals),
        "packets": len(records),
        "duration_s": span,
    }


def network_block(
    records: Recording | Iterable[Record],
    *,
    idle_split_s: float = _DEFAULT_IDLE_SPLIT_S,
) -> dict[str, object]:
    """The document's ``network`` block: RTT/jitter/loss/reorder/duplicate,
    idle vs active windows, per-stream detail and periodic-packet jitter."""
    records = _records_of(records)
    idle, active = split_idle_active(records, idle_split_s)
    block = _net_subset(records)
    block["streams"] = stream_stats(records)
    rtt_by_port: dict[str, object] = {}
    for port in sorted({rec.port for rec in records}):
        summary = _rtt_summary([r for r in records if r.port == port])
        if summary.get("n"):
            rtt_by_port[port] = summary
    block["rtt_by_port_ms"] = rtt_by_port
    block["idle"] = _net_subset(idle)
    block["active"] = _net_subset(active)
    block["idle_split_s"] = idle_split_s
    # Inter-arrival jitter of the radio's periodic packets: status packets
    # (0x50, control) and the radio-originated ping requests per port.
    periodic: dict[str, object] = {}
    status_times = [
        r.t
        for r in records
        if r.direction == "r2c"
        and r.port == "control"
        and len(r.data) == _STATUS_SIZE
        and _hdr_type(r.data) == _PT_DATA
    ]
    if len(status_times) >= 2:
        periodic["control_status"] = st.summarize_samples(interarrival_ms(status_times))
    for port in sorted({rec.port for rec in records}):
        ping_times = [
            r.t
            for r in records
            if r.direction == "r2c"
            and r.port == port
            and len(r.data) == _PING_SIZE
            and _hdr_type(r.data) == _PT_PING
            and r.data[0x10] == _PING_REQUEST
        ]
        if len(ping_times) >= 2:
            periodic[f"{port}_ping_request"] = st.summarize_samples(
                interarrival_ms(ping_times)
            )
    block["periodic_interarrival_ms"] = periodic
    return block


def analyze_recording(
    records: Recording | Iterable[Record],
    *,
    idle_split_s: float = _DEFAULT_IDLE_SPLIT_S,
    radio_id: str,
    sigs: Sequence[tuple[str, int, int | None, bytes]] = (),
) -> dict[str, object]:
    """One ``rigplane-serial-timing/1`` document (``port_kind: lan-wifi``)
    from a datagram recording: CI-V request->reply classes via the serial
    tool's pairing, plus the ``network`` block."""
    records = _records_of(records)
    rows: list[dict[str, object]] = []
    for rec in records:
        if rec.port != "civ":
            continue
        frame = extract_civ_frame(rec.data)
        if frame is not None:
            rows.append({"t": rec.t, "dir": rec.direction, "frame": frame.hex()})
    result = st.derive_civ_latencies(rows, sigs)
    net = network_block(records, idle_split_s=idle_split_s)
    rates = per_second_rates(records)
    notes = [
        f"{len(records)} datagrams, {len(rows)} CI-V frames; "
        f"pairing: echoes={result.echoes} unsolicited={result.unsolicited} "
        f"unpaired={result.unpaired}",
        f"idle/active split at {idle_split_s}s without a client CI-V request",
        "packet rates/s: "
        + (", ".join(f"{k}={v}" for k, v in rates.items()) or "none"),
    ]
    for name, summary in sorted(
        net["periodic_interarrival_ms"].items()  # type: ignore[union-attr]
    ):
        notes.append(
            f"periodic {name}: inter-arrival median {summary['median']} ms, "
            f"p95 {summary['p95']} ms (n={summary['n']})"
        )
    doc = st.build_document(
        radio_id=radio_id,
        protocol="civ",
        port_kind=PORT_KIND,
        baud=None,
        samples=result.samples,
        notes=notes,
    )
    doc["network"] = net
    return doc


# ── fault-plan printer (PRINT-only; never edits profile TOMLs/faults.py) ──


def fault_plan_toml(docs: Iterable[dict[str, object]]) -> str:
    """Measured network profile as TOML blocks named after ``FaultPlan``
    fields (``control_from_client`` ... ``civ_to_client``), values in the
    shape of ``rigplane_testkit/icom/faults.py FaultRule``.

    Mapping (brief C4): ``delay_s`` = median one-way (ping RTT/2,
    symmetric-link assumption), ``jitter_s`` = RTT p95 - p50,
    probabilities from the measured percentages. ``to_client`` loss comes
    from sequence gaps; ``from_client`` loss from the radio's retransmit
    requests (the only uplink-loss evidence a mid-link proxy can see, so it
    under-counts: it misses packets the radio never asked back).
    ``reorder_window`` is 2 when any reorder was measured, else 0 (a window
    has no probability knob; the measured ``reorder_pct`` rides in a
    comment).
    """
    out: list[str] = []
    for doc in docs:
        net = doc.get("network") or {}
        streams: dict[str, dict[str, object]] = net.get("streams") or {}  # type: ignore[assignment]
        out.append(
            f"# {doc.get('radio_id')} ({doc.get('port_kind')}) measured "
            f"{doc.get('generated_at')} host={doc.get('host')}"
        )
        if not net:
            out.append("# no network block in this document\n")
            continue
        out.append(
            "# delay_s = median ping RTT/2 (one-way, symmetric assumption); "
            "jitter_s = RTT p95 - p50"
        )
        rtt_by_port: dict[str, dict[str, object]] = net.get("rtt_by_port_ms") or {}  # type: ignore[assignment]
        headline_rtt: dict[str, object] = net.get("rtt_ms") or {}  # type: ignore[assignment]
        for port in ("control", "civ"):
            rtt = rtt_by_port.get(port) or headline_rtt
            median = rtt.get("median")
            p95 = rtt.get("p95")
            delay_s = round(float(median) / 2000.0, 6) if median is not None else 0.0
            jitter_s = (
                round((float(p95) - float(median)) / 1000.0, 6)
                if median is not None and p95 is not None
                else 0.0
            )
            down = streams.get(f"{port}:to_client") or {}
            up = streams.get(f"{port}:from_client") or {}
            down_arrivals = int(down.get("packets", 0))
            down_dup = int(down.get("duplicates", 0))
            down_lost = int(down.get("lost", 0))
            expected_down = down_arrivals - down_dup + down_lost
            down_drop = round(down_lost / expected_down, 6) if expected_down else 0.0
            down_dup_p = round(down_dup / down_arrivals, 6) if down_arrivals else 0.0
            down_reordered = int(down.get("reordered", 0))
            up_sent = int(up.get("packets", 0))
            up_asked = int(down.get("retransmit_requests", 0))
            up_drop = round(up_asked / up_sent, 6) if up_sent else 0.0
            for direction in ("from_client", "to_client"):
                out.append(f"[{port}_{direction}]")
                out.append(f"delay_s = {delay_s}")
                out.append(f"jitter_s = {jitter_s}")
                if direction == "to_client":
                    out.append(f"drop_probability = {down_drop}")
                    out.append(f"duplicate_probability = {down_dup_p}")
                    out.append(
                        f"reorder_window = {2 if down_reordered else 0}"
                        f"  # measured reorder_pct = {down.get('reorder_pct', 0.0)}"
                    )
                else:
                    out.append(
                        f"drop_probability = {up_drop}"
                        "  # from radio retransmit requests (under-counts)"
                    )
                    out.append(
                        "duplicate_probability = 0.0"
                        "  # client duplicates are retransmit answers, not network dups"
                    )
                    out.append(
                        "reorder_window = 0  # not observable client->radio at the proxy"
                    )
                out.append("")
    return "\n".join(out).rstrip() + "\n"


# ── credentials (never on argv, never printed, never in the JSON) ──────────


def read_credentials(
    *,
    user_env: str,
    pass_file: str | None,
    env: dict[str, str] | None = None,
) -> tuple[str, str]:
    """Username from env var *user_env*, password from a 0600 file.

    Mirrors the Core CLI (``--user`` defaulting to $ICOM_USER, password read
    from ``--pass-file`` first line, src/rigplane/cli/__init__.py:342) but
    refuses an argv password outright and enforces the 0600 mode.
    """
    environ = os.environ if env is None else env
    username = environ.get(user_env, "")
    if not username:
        raise st.RefusalError(
            f"username environment variable {user_env!r} is not set or empty"
        )
    if not pass_file:
        raise st.RefusalError(
            "--pass-file is required; a password on argv is never accepted"
        )
    path = Path(pass_file)
    try:
        mode = stat.S_IMODE(path.stat().st_mode)
    except OSError as exc:
        raise st.RefusalError(f"cannot stat --pass-file {pass_file!r}: {exc}")
    if mode != 0o600:
        raise st.RefusalError(
            f"--pass-file {pass_file!r} must have mode 0600, has {mode:04o}"
        )
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError as exc:
        raise st.RefusalError(f"cannot read --pass-file {pass_file!r}: {exc}")
    password = lines[0] if lines else ""
    if not password:
        raise st.RefusalError(f"--pass-file {pass_file!r} is empty")
    return username, password


# ── RX whitelist ───────────────────────────────────────────────────────────


def rx_whitelist(
    config: st.RigConfig,
) -> tuple[dict[tuple[int, int | None], bytes], tuple[int, int | None] | None]:
    """(reads, set_pair): every declared ``get_*`` (cmd, sub) -> expected
    request data (the declared prefix, empty for plain reads), plus the
    ``set_af_level`` pair that set-to-current-value writes use."""
    reads: dict[tuple[int, int | None], bytes] = {}
    set_pair: tuple[int, int | None] | None = None
    for name, cmd, sub, prefix in st._civ_signatures(config):
        if name.startswith("get_"):
            reads.setdefault((cmd, sub), prefix)
        elif name == "set_af_level" and set_pair is None:
            set_pair = (cmd, sub)
    return reads, set_pair


def check_rx_whitelisted(
    frame: CivFrame,
    *,
    reads: dict[tuple[int, int | None], bytes],
    set_pair: tuple[int, int | None] | None,
    expected_set_data: bytes | None,
) -> None:
    """Refuse any frame the RX capture must never send.

    Allowed: whitelisted reads (data == the declared get_* prefix — empty
    for plain reads, so 1C 00 passes ONLY as the PTT *read*) and
    set-to-current-value writes (the af-level pair with data equal to the
    value just read back). A 1C 00 with data is a PTT/TX frame and raises
    before anything else.
    """
    key = (frame.command, frame.sub)
    if frame.command == 0x1C and frame.sub == 0x00 and frame.data:
        raise st.RefusalError(
            f"RX capture never sends a PTT/TX frame: 1C 00 {frame.data.hex()}"
        )
    if set_pair is not None and key == set_pair and frame.data:
        if expected_set_data is None or frame.data != expected_set_data:
            raise st.RefusalError(
                "RX capture allows set-to-current-value writes only: "
                f"{frame.data.hex()} != {expected_set_data!r}"
            )
        return
    if key not in reads or frame.data != reads[key]:
        raise st.RefusalError(
            f"frame cmd=0x{frame.command:02X} sub={frame.sub} "
            f"data={frame.data.hex()!r} is not a whitelisted RX read"
        )


# ── the minimal CI-V-only LAN session (live capture only) ──────────────────


class LanCivSession:
    """Minimal Icom LAN client: control + CI-V channels, NO audio stream.

    Replicates the production handshake (runtime/_control_phase.py) with
    core/auth.py builders and core/transport.py ``IcomTransport`` (which
    owns the ping loop, retransmit requests and sequence tracking), but
    declares ``rxenable = txenable = 0`` and ``audio_local_port = 0`` in the
    conninfo packet and never binds an audio socket. The CI-V DATA wrapper
    mirrors runtime/_civ_rx.py ``_wrap_civ`` (0xC1 tag, datalen <H @0x11,
    stream seq >H @0x13).
    """

    def __init__(
        self,
        *,
        radio_name: str = "IC-705",
        handshake_timeout_s: float = 5.0,
        clock: Callable[[], int] = time.perf_counter_ns,
    ) -> None:
        self._radio_name = radio_name
        self._timeout = handshake_timeout_s
        self._clock = clock
        self._ctrl = None
        self._civ = None
        self._token = 0
        self._tok_request = 0
        self._auth_seq = 0
        self._civ_stream_seq = 0
        self._renewal_task: asyncio.Task[None] | None = None
        self.notices: list[str] = []
        self.civ_port = 0

    async def connect(
        self, host: str, control_port: int, *, username: str, password: str
    ) -> None:
        """Discovery, login, conninfo, CI-V channel. Refuses on any
        rejection — including the radio's own session-held answers."""
        from rigplane.transport import IcomTransport

        ctrl = IcomTransport()
        self._ctrl = ctrl
        await ctrl.connect(host, control_port)  # AYT/IAH + AYR, core/transport.py:189
        ctrl.start_ping_loop()
        ctrl.start_retransmit_loop()

        self._tok_request = random.getrandbits(16)
        await ctrl.send_tracked(
            build_login_packet(
                username,
                password,
                sender_id=ctrl.my_id,
                receiver_id=ctrl.remote_id,
                tok_request=self._tok_request,
            )
        )
        resp = await self._wait_for(
            ctrl, size=_LOGIN_RESPONSE_SIZE, label="login response"
        )
        auth = parse_auth_response(resp)
        if not auth.success:
            if auth.error == _ERR_CREDENTIALS:
                raise st.RefusalError(
                    "credentials rejected (login error 0xFEFFFFFF); "
                    "check --user-env / --pass-file"
                )
            raise LanSessionBusy(
                f"the radio reports its LAN session held "
                f"(login error 0x{auth.error:08X}); close the other client "
                "first (LAN lsof-equivalent)"
            )
        self._token = auth.token
        await self._send_token(_TOKEN_ACK)
        guid = await self._receive_guid(ctrl)

        civ_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        civ_sock.bind(("0.0.0.0", 0))
        civ_local_port = civ_sock.getsockname()[1]

        conninfo = bytearray(
            build_conninfo_packet(
                sender_id=ctrl.my_id,
                receiver_id=ctrl.remote_id,
                username=username,
                token=self._token,
                tok_request=self._tok_request,
                radio_name=self._radio_name,
                mac_address=b"\x00" * 6,
                auth_seq=self._auth_seq,
                guid=guid,
                civ_local_port=civ_local_port,
                audio_local_port=0,
            )
        )
        self._auth_seq += 1
        # No audio stream is ever opened: do not request one. If a firmware
        # rejects rxenable=0, the live run fails here — loudly, before any
        # measurement (see the report; unverifiable without the radio).
        conninfo[0x70] = 0x00  # rxenable
        conninfo[0x71] = 0x00  # txenable
        await ctrl.send_tracked(bytes(conninfo))

        status = await self._wait_for_status(ctrl)
        if status.error in (_ERR_PREV_SESSION, _ERR_FOREIGN_HOLDER):
            raise LanSessionBusy(
                f"the radio refused the session allocation "
                f"(status error 0x{status.error:08X}); another client holds "
                "the radio (LAN lsof-equivalent)"
            )
        if status.civ_port <= 0:
            raise st.RefusalError(
                f"the radio advertised no CI-V port (status error 0x{status.error:08X})"
            )
        self.civ_port = status.civ_port

        civ = IcomTransport()
        self._civ = civ
        try:
            await civ.connect(host, self.civ_port, sock=civ_sock)
        except Exception:
            civ_sock.close()
            raise
        civ.start_ping_loop()
        civ.start_retransmit_loop()
        civ.start_idle_loop()
        await self._send_open_close(open_stream=True)
        # Nobody consumes the control queue after setup; keep the conninfo
        # notice callback so a "session free" notice is at least recorded.
        ctrl._discard_data_packets = True
        ctrl._conninfo_notice_callback = self._on_conninfo_notice
        self._renewal_task = asyncio.create_task(self._token_renewal_loop())

    async def close(self) -> None:
        """Best-effort orderly close: CI-V close packet, token remove,
        DISCONNECT on both channels. Every step swallows errors."""
        if self._renewal_task is not None:
            self._renewal_task.cancel()
            self._renewal_task = None
        if self._civ is not None:
            try:
                await self._send_open_close(open_stream=False)
            except Exception:
                pass
            try:
                await self._civ.disconnect()
            except Exception:
                pass
            self._civ = None
        if self._ctrl is not None:
            try:
                await self._send_token(_TOKEN_REMOVE)
            except Exception:
                pass
            try:
                await self._ctrl.disconnect()
            except Exception:
                pass
            self._ctrl = None

    async def transact(self, frame: bytes, *, timeout_s: float = 1.0) -> st.CivReply:
        """Send one CI-V frame, await its reply; timings like civ_transact.

        A LAN reply is a single datagram, so ``ack_ms`` == ``frame_ms``:
        the first non-echo reply byte IS the complete frame. The LAN CI-V
        echo (our own request returned byte-identical) is skipped.
        """
        if self._civ is None:
            raise ConnectionError("CI-V channel is not open")
        request = parse_civ_frame(frame)
        pkt = self._wrap_civ(frame)
        t0 = self._clock()
        await self._civ.send_tracked(pkt)
        t1 = self._clock()
        deadline = time.monotonic() + timeout_s
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError(f"no reply to {frame.hex()} within {timeout_s:.3f}s")
            try:
                data = await self._civ.receive_packet(timeout=remaining)
            except asyncio.TimeoutError:
                raise TimeoutError(f"no reply to {frame.hex()} within {timeout_s:.3f}s")
            arrival_ns = self._clock()  # stamped as the datagram arrived
            reply_frame = extract_civ_frame(data)
            if reply_frame is None or reply_frame == frame:
                continue  # non-CI-V packet / LAN echo of our own request
            try:
                parsed = parse_civ_frame(reply_frame)
            except ValueError:
                continue
            if parsed.command in (0xFB, 0xFA) or (parsed.command, parsed.sub) == (
                request.command,
                request.sub,
            ):
                return st.CivReply(
                    frame=parsed,
                    raw=reply_frame,
                    write_drain_ms=(t1 - t0) / 1e6,
                    ack_ms=(arrival_ns - t1) / 1e6,
                    frame_ms=(arrival_ns - t1) / 1e6,
                )
            # Unsolicited frame (transceive broadcast etc.): not our reply.

    def _wrap_civ(self, frame: bytes) -> bytes:
        """CI-V frame in a UDP DATA packet (runtime/_civ_rx.py:4118 layout)."""
        assert self._civ is not None
        total = _CIV_DATA_HEADER + len(frame)
        pkt = bytearray(total)
        struct.pack_into("<I", pkt, 0x00, total)
        struct.pack_into("<H", pkt, 0x04, _PT_DATA)
        struct.pack_into("<I", pkt, 0x08, self._civ.my_id)
        struct.pack_into("<I", pkt, 0x0C, self._civ.remote_id)
        pkt[0x10] = 0xC1
        struct.pack_into("<H", pkt, 0x11, len(frame))
        struct.pack_into(">H", pkt, 0x13, self._civ_stream_seq)
        self._civ_stream_seq = (self._civ_stream_seq + 1) & 0xFFFF
        pkt[_CIV_DATA_HEADER:] = frame
        return bytes(pkt)

    async def _wait_for(self, transport, *, size: int, label: str) -> bytes:
        deadline = time.monotonic() + self._timeout
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError(f"{label} timed out")
            try:
                data = await transport.receive_packet(timeout=remaining)
            except asyncio.TimeoutError:
                raise TimeoutError(f"{label} timed out")
            if len(data) == size:
                return data

    async def _wait_for_status(self, transport):
        """First 0x50 status packet after conninfo (2 s bound)."""
        deadline = time.monotonic() + 2.0
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError("status packet timed out")
            try:
                data = await transport.receive_packet(timeout=min(remaining, 0.3))
            except asyncio.TimeoutError:
                continue
            if len(data) == _STATUS_SIZE:
                return parse_status_response(data)

    async def _receive_guid(self, transport) -> bytes | None:
        """Radio GUID from the 0x90 conninfo notice (0x20..0x30), if any."""
        await asyncio.sleep(0.3)
        guid = None
        for _ in range(30):
            try:
                data = await transport.receive_packet(timeout=0.1)
                if len(data) == _CONNINFO_SIZE:
                    guid = data[0x20:0x30]
            except asyncio.TimeoutError:
                break
        return guid

    async def _send_token(self, magic: int) -> None:
        """0x40 token packet (ack 0x02 / remove 0x01 / renew 0x05);
        layout from runtime/_control_phase.py ``_send_token``."""
        assert self._ctrl is not None
        pkt = bytearray(_TOKEN_PACKET_SIZE)
        struct.pack_into("<I", pkt, 0x00, _TOKEN_PACKET_SIZE)
        struct.pack_into("<H", pkt, 0x04, 0x00)
        struct.pack_into("<I", pkt, 0x08, self._ctrl.my_id)
        struct.pack_into("<I", pkt, 0x0C, self._ctrl.remote_id)
        struct.pack_into(">I", pkt, 0x10, _TOKEN_PACKET_SIZE - 0x10)
        pkt[0x14] = 0x01
        pkt[0x15] = magic
        struct.pack_into(">H", pkt, 0x16, self._auth_seq)
        self._auth_seq += 1
        struct.pack_into("<H", pkt, 0x1A, self._tok_request)
        struct.pack_into(">H", pkt, 0x24, 0x0798)
        struct.pack_into("<I", pkt, 0x1C, self._token)
        await self._ctrl.send_tracked(bytes(pkt))

    async def _send_open_close(self, *, open_stream: bool) -> None:
        """0x16 open/close packet on the CI-V channel
        (runtime/_control_phase.py ``_send_open_close_on_transport``)."""
        assert self._civ is not None
        pkt = bytearray(_OPENCLOSE_SIZE)
        struct.pack_into("<I", pkt, 0x00, _OPENCLOSE_SIZE)
        struct.pack_into("<I", pkt, 0x08, self._civ.my_id)
        struct.pack_into("<I", pkt, 0x0C, self._civ.remote_id)
        struct.pack_into("<H", pkt, 0x10, 0x01C0)
        struct.pack_into(">H", pkt, 0x13, self._civ_stream_seq)
        self._civ_stream_seq = (self._civ_stream_seq + 1) & 0xFFFF
        pkt[0x15] = 0x04 if open_stream else 0x00
        await self._civ.send_tracked(bytes(pkt))

    def _on_conninfo_notice(self, data: bytes) -> None:
        busy = struct.unpack_from("<I", data, 0x60)[0]
        if busy == 0:
            self.notices.append(
                "conninfo notice: radio reported the LAN session free (busy=0) "
                "mid-capture; timings after this point may be a reconnect"
            )

    async def _token_renewal_loop(self) -> None:
        try:
            while True:
                await asyncio.sleep(_TOKEN_RENEWAL_S)
                await self._send_token(_TOKEN_RENEW)
        except asyncio.CancelledError:
            pass
        except Exception as exc:  # a failed renewal must not kill the capture
            self.notices.append(f"token renewal failed: {exc}")


# ── capture rx ─────────────────────────────────────────────────────────────


async def run_rx_capture_lan(
    *,
    session,
    config: st.RigConfig,
    radio_key: str,
    host: str | None = None,
    minutes: float = 3.0,
    pace_s: float = _RX_PACE_S,
    set_every_s: float = _RX_SET_EVERY_S,
    quiet_s: float = _QUIET_S,
    clock: Callable[[], float] = time.monotonic,
    sleep: Callable[[float], "asyncio.Future[None]"] = asyncio.sleep,
) -> dict[str, object]:
    """RX-only LAN capture. Never TX: every frame passes the whitelist —
    reads (freq/mode/s-meter/PTT-READ/id) plus set-to-current-value writes.
    """
    reads, set_pair = rx_whitelist(config)
    samples: dict[str, list[float]] = {}
    notes: list[str] = []

    def add(cls: str, value_ms: float) -> None:
        samples.setdefault(cls, []).append(value_ms)

    async def guarded(frame: bytes, expected_set_data: bytes | None = None):
        check_rx_whitelisted(
            parse_civ_frame(frame),
            reads=reads,
            set_pair=set_pair,
            expected_set_data=expected_set_data,
        )
        return await session.transact(frame, timeout_s=1.0)

    # Live identity first (same rule as the serial tool's capture rx).
    try:
        identity = await guarded(
            st._civ_frame_from_config(config, "get_transceiver_id")
        )
    except TimeoutError as exc:
        raise st.RefusalError(f"identity read timed out: {exc}")
    if not identity.frame.data or identity.frame.data[0] != config.civ_addr:
        raise st.RefusalError(
            f"identity read answered {identity.raw.hex() or 'nothing'}: "
            f"expected CI-V address 0x{config.civ_addr:02X} for {config.id}"
        )

    probes = [
        (name, st._civ_frame_from_config(config, name))
        for name in _RX_PROBES
        if name in config.commands
    ]
    af_read = (
        st._civ_frame_from_config(config, "get_af_level")
        if "get_af_level" in config.commands and set_pair is not None
        else None
    )

    total_s = minutes * 60.0
    start = clock()
    deadline = start + total_s
    quiet_start = quiet_end = None
    if total_s >= 2.0 * quiet_s:
        quiet_start = start + 0.4 * total_s
        quiet_end = quiet_start + quiet_s
        notes.append(
            f"quiet phase {quiet_s:.0f}s at {0.4 * total_s:.0f}s in "
            "(Wi-Fi power-save behaviour; keep-alive pings continue)"
        )
    else:
        notes.append(
            f"quiet phase skipped: capture {total_s:.1f}s is shorter than "
            f"2x quiet {quiet_s:.0f}s"
        )

    def in_quiet(now: float) -> bool:
        return quiet_start is not None and quiet_start <= now < (quiet_end or 0.0)

    last_request_end = start
    next_set = start + set_every_s
    while clock() < deadline:
        now = clock()
        if in_quiet(now):
            await sleep(min(quiet_end or deadline, deadline) - now)
            continue
        for _name, frame in probes:
            now = clock()
            if now >= deadline or in_quiet(now):
                break
            wait = pace_s - (now - last_request_end)
            if wait > 0:
                await sleep(wait)
            if clock() >= deadline or in_quiet(clock()):
                # pacing carried us into the quiet window / past the deadline:
                # send nothing until the outer loop sleeps the quiet away.
                break
            reply = await guarded(frame)
            last_request_end = clock()
            add("write_drain", reply.write_drain_ms)
            add("ack_read", reply.ack_ms)
            add("frame_read", reply.frame_ms)
            if _name == "get_transceiver_status":
                add("ptt_read_idle", reply.ack_ms)
        now = clock()
        if (
            af_read is not None
            and now >= next_set
            and now < deadline
            and not in_quiet(now)
        ):
            read1 = await guarded(af_read)
            last_request_end = clock()
            add("write_drain", read1.write_drain_ms)
            add("ack_read", read1.ack_ms)
            add("frame_read", read1.frame_ms)
            set_frame = st._civ_frame_from_config(
                config, "set_af_level", data=read1.frame.data
            )
            acked = await guarded(set_frame, expected_set_data=read1.frame.data)
            last_request_end = clock()
            add("write_drain", acked.write_drain_ms)
            add("ack_set", acked.ack_ms)
            add("frame_set", acked.frame_ms)
            read2 = await guarded(af_read)
            last_request_end = clock()
            if read2.frame.data != read1.frame.data:
                raise st.RefusalError(
                    "set-to-current read-back differs: "
                    f"{read1.frame.data.hex()} -> {read2.frame.data.hex()}"
                )
            next_set = clock() + set_every_s

    return st.build_document(
        radio_id=config.id if radio_key in ("", config.id) else radio_key,
        protocol="civ",
        port_kind=PORT_KIND,
        baud=None,
        host=host,
        samples=samples,
        notes=notes + session.notices,
    )


# ── capture tx ─────────────────────────────────────────────────────────────


async def _poll_ptt_state_lan(
    session,
    status_frame: bytes,
    want: bytes,
    *,
    deadline_s: float,
    poll_s: float,
    watchdog: st.KeyedWatchdog,
    clock: Callable[[], float],
    sleep: Callable[[float], "asyncio.Future[None]"],
) -> float:
    """Poll ``1C 00`` until it reads *want*; returns the completion time (s)."""
    while True:
        watchdog.check()
        now = clock()
        if now >= deadline_s:
            raise st.SwitchDeadline(f"PTT did not reach {want.hex()} within deadline")
        try:
            reply = await session.transact(status_frame, timeout_s=0.3)
        except TimeoutError:
            reply = None
        if reply is not None and reply.frame.data == want:
            return clock()
        await sleep(poll_s)


async def _lan_emergency_unkey(
    session,
    unkey_frame: bytes,
    status_frame: bytes,
    *,
    attempts: int = _UNKEY_ATTEMPTS,
    gap_s: float = _UNKEY_GAP_S,
    sleep: Callable[[float], "asyncio.Future[None]"] = asyncio.sleep,
) -> bool:
    """Repeat ``1C 00 00`` with PTT read-back verification; best-effort,
    every step swallows errors (same pattern as st.emergency_unkey)."""
    for _ in range(attempts):
        try:
            await session.transact(unkey_frame, timeout_s=0.3)
        except Exception:
            pass
        await sleep(gap_s)
        try:
            reply = await session.transact(status_frame, timeout_s=0.3)
            if reply.frame.data == b"\x00":
                return True
        except Exception:
            pass
    return False


async def run_tx_capture_lan(
    *,
    session,
    config: st.RigConfig,
    radio_key: str,
    host: str | None = None,
    reps: int = _DEFAULT_REPS,
    cap_keyed_s: float = _KEYED_CAP_S,
    hold_s: float = _HOLD_S,
    poll_s: float = _PTT_POLL_S,
    read_offset_s: float = _READ_WHILE_SWITCHING_OFFSET_S,
    switch_deadline_s: float = _SWITCH_DEADLINE_S,
    watchdog_tick_s: float = 0.01,
    clock: Callable[[], float] = time.monotonic,
    sleep: Callable[[float], "asyncio.Future[None]"] = asyncio.sleep,
) -> tuple[dict[str, object], int]:
    """TX capture over LAN: key/unkey at minimum power, measuring the same
    classes as the serial tool so the two documents compare directly.

    Callers must have confirmed the CI-V identity live BEFORE calling this.
    On any failure the transmitter is unkeyed in ``finally`` (LAN frames)
    and the partial evidence is returned with exit code 1.
    """
    power_min_raw = 0
    controls = config.controls or {}
    rf_power = controls.get("rf_power")
    if rf_power is not None and "raw_min" in rf_power:
        power_min_raw = int(rf_power["raw_min"])

    frames = {
        "power": st._civ_frame_from_config(config, "get_rf_power"),
        "ptt_on": st._civ_frame_from_config(config, "ptt_on"),
        "ptt_off": st._civ_frame_from_config(config, "ptt_off"),
        "ptt_read": st._civ_frame_from_config(config, "get_transceiver_status"),
        "freq": st._civ_frame_from_config(config, "get_freq"),
    }

    samples: dict[str, list[float]] = {}
    notes: list[str] = []

    def add(cls: str, value_ms: float) -> None:
        samples.setdefault(cls, []).append(value_ms)

    watchdog = st.KeyedWatchdog(cap_keyed_s, tick_s=watchdog_tick_s, clock=clock)
    watchdog.start()
    abort_reason: str | None = None
    confirmed: bool | None = None
    try:
        for rep in range(reps):
            watchdog.check()
            power = await session.transact(frames["power"], timeout_s=1.0)
            add("write_drain", power.write_drain_ms)
            add("ack_read", power.ack_ms)
            raw_level = st._decode_level_raw(power.frame.data)
            if raw_level != power_min_raw:
                raise st.RefusalError(
                    f"RF power raw {raw_level} != profile minimum {power_min_raw}: "
                    "the owner sets minimum power; this tool never raises it"
                )
            idle = await session.transact(frames["ptt_read"], timeout_s=1.0)
            add("ptt_read_idle", idle.ack_ms)
            if idle.frame.data == b"\x01":
                raise st.RefusalError("radio is already keyed before the capture rep")

            # ── key on ──
            # Keyed time counts from BEFORE the key frame is sent: when the
            # send keys the radio but the ACK is lost, the cap still applies.
            watchdog.key_on()
            key_reply = await session.transact(frames["ptt_on"], timeout_s=1.0)
            keyed_at = clock()
            add("write_drain", key_reply.write_drain_ms)
            add("ptt_set_ack_on", key_reply.ack_ms)
            add("ack_set", key_reply.ack_ms)

            # One read shortly after the key command, while the path switches.
            wait_s = read_offset_s - (clock() - keyed_at)
            if wait_s > 0:
                await sleep(wait_s)
            during = await session.transact(frames["freq"], timeout_s=0.5)
            add("ack_read_while_switching", during.ack_ms)

            switched_on_at = await _poll_ptt_state_lan(
                session,
                frames["ptt_read"],
                b"\x01",
                deadline_s=keyed_at + switch_deadline_s,
                poll_s=poll_s,
                watchdog=watchdog,
                clock=clock,
                sleep=sleep,
            )
            add("tx_on_switch", (switched_on_at - keyed_at) * 1000.0)

            await sleep(hold_s)

            # ── key off ──
            unkey_reply = await session.transact(frames["ptt_off"], timeout_s=1.0)
            unkeyed_at = clock()
            add("write_drain", unkey_reply.write_drain_ms)
            add("ptt_set_ack_off", unkey_reply.ack_ms)
            add("ack_set", unkey_reply.ack_ms)

            switched_off_at = await _poll_ptt_state_lan(
                session,
                frames["ptt_read"],
                b"\x00",
                deadline_s=unkeyed_at + switch_deadline_s,
                poll_s=poll_s,
                watchdog=watchdog,
                clock=clock,
                sleep=sleep,
            )
            # Keyed time ends when the radio is READ BACK as RX, not at the
            # unkey ACK (same conservative rule as the serial tool).
            watchdog.key_off()
            add("tx_off_switch", (switched_off_at - unkeyed_at) * 1000.0)
            watchdog.check()
            notes.append(f"rep {rep + 1}/{reps} complete")

        # Re-read the power once after the last rep: a mid-run change
        # invalidates every timing in this document.
        final_power = await session.transact(frames["power"], timeout_s=1.0)
        add("write_drain", final_power.write_drain_ms)
        add("ack_read", final_power.ack_ms)
        final_level = st._decode_level_raw(final_power.frame.data)
        notes.append(
            f"final RF power raw {final_level} (profile minimum {power_min_raw})"
        )
        if final_level != power_min_raw:
            abort_reason = (
                f"RF power changed during the capture: {power_min_raw} -> "
                f"{final_level}; document FAILED, discard these timings"
            )
    except st.RefusalError:
        raise
    except BaseException as exc:  # deadline, watchdog, link error, SIGINT/SIGTERM
        abort_reason = f"{type(exc).__name__}: {exc}"
    finally:
        if abort_reason is not None:
            confirmed = await _lan_emergency_unkey(
                session, frames["ptt_off"], frames["ptt_read"], sleep=sleep
            )
            if confirmed:
                # Keyed time ends at the emergency unkey's RX confirmation.
                watchdog.key_off()
            # Never confirmed: keyed time keeps counting until process end.
        keyed = watchdog.keyed_total_s()
        watchdog.close()

    if abort_reason is None:
        return (
            st.build_document(
                radio_id=radio_key,
                protocol="civ",
                port_kind=PORT_KIND,
                baud=None,
                host=host,
                samples=samples,
                notes=notes + session.notices + [f"total keyed time {keyed:.3f}s"],
            ),
            EXIT_OK,
        )

    doc = st.build_document(
        radio_id=radio_key,
        protocol="civ",
        port_kind=PORT_KIND,
        baud=None,
        host=host,
        samples=samples,
        notes=notes
        + session.notices
        + [
            f"ABORTED: {abort_reason}",
            f"emergency unkey sent up to {_UNKEY_ATTEMPTS}x; "
            f"radio confirmed RX: {confirmed}",
            f"total keyed time before abort {keyed:.3f}s",
        ]
        + (
            []
            if confirmed
            else ["RX never confirmed: keyed time kept counting until process end"]
        ),
    )
    return doc, EXIT_ABORT


def _emergency_serial_unkey(
    port: str,
    *,
    baud: int,
    config: st.RigConfig,
    serial_module: object | None = None,
) -> str:
    """Independent second unkey path: after the LAN session is closed, open
    the USB CI-V port (DTR/RTS False before open, write_timeout set — the
    serial tool's ``open_serial_port``) and repeat FE FE A4 E0 1C 00 00 FD
    with read-back verification (the serial tool's ``emergency_unkey``)."""
    unkey = build_civ_frame(
        config.civ_addr, CONTROLLER_ADDR, 0x1C, sub=0x00, data=b"\x00"
    )
    status = build_civ_frame(config.civ_addr, CONTROLLER_ADDR, 0x1C, sub=0x00)
    try:
        ser = st.open_serial_port(port, baud, serial_module=serial_module)
    except Exception as exc:
        return f"emergency-serial {port}: open failed: {exc}"
    try:
        confirmed = st.emergency_unkey(ser, unkey, status)
    finally:
        try:
            ser.close()
        except Exception:
            pass
    return (
        f"emergency-serial {port}: repeated 1C 00 00 up to "
        f"{st._UNKEY_ATTEMPTS}x; radio confirmed RX: {confirmed}"
    )


# ── CLI ────────────────────────────────────────────────────────────────────


def _cmd_analyze(args: argparse.Namespace) -> tuple[dict[str, object], int]:
    records = Recording.load(args.recording)
    radio_key = args.radio or Path(args.recording).parent.name
    profiles = st.load_profiles()
    config = st.resolve_profile(radio_key, profiles)
    sigs = st._civ_signatures(config) if config is not None else []
    radio_id = config.id if config is not None else radio_key
    doc = analyze_recording(
        records,
        idle_split_s=args.idle_split_s,
        radio_id=radio_id,
        sigs=sigs,
    )
    if config is None:
        doc["notes"].append(
            f"no rig profile for {radio_key!r}: fallback CI-V classification"
        )
    return doc, EXIT_OK


async def _capture_async(
    args: argparse.Namespace,
    config: st.RigConfig,
    *,
    session,
    username: str,
    password: str,
    record_path: str | None,
    serial_module: object | None,
) -> tuple[dict[str, object], int]:
    recorder = None
    host, control_port = args.host, args.control_port
    if record_path is not None:
        from rigplane_testkit.icom.recorder import IcomSessionRecorder

        recorder = IcomSessionRecorder(
            args.host,
            args.control_port,
            record_path,
            listen_host="127.0.0.1",
        )
        await recorder.start()
        host, control_port = "127.0.0.1", recorder.control_port
    try:
        await session.connect(host, control_port, username=username, password=password)
        try:
            if args.capture_mode == "rx":
                doc = await run_rx_capture_lan(
                    session=session,
                    config=config,
                    radio_key=args.radio,
                    host=args.host,
                    minutes=args.minutes,
                )
                code = EXIT_OK
            else:
                # Live CI-V identity BEFORE any keyed write (19 00 -> 0xA4).
                id_frame = st._civ_frame_from_config(config, "get_transceiver_id")
                try:
                    reply = await session.transact(id_frame, timeout_s=1.0)
                except TimeoutError as exc:
                    raise st.RefusalError(f"identity read timed out: {exc}")
                if not reply.frame.data or reply.frame.data[0] != config.civ_addr:
                    raise st.RefusalError(
                        f"identity read answered {reply.raw.hex() or 'nothing'}: "
                        f"expected CI-V address 0x{config.civ_addr:02X} for {config.id}"
                    )
                doc, code = await run_tx_capture_lan(
                    session=session,
                    config=config,
                    radio_key=args.radio,
                    host=args.host,
                    reps=args.reps,
                )
        finally:
            try:
                await session.close()
            except Exception:
                pass
    finally:
        if recorder is not None:
            await recorder.stop()

    if recorder is not None:
        recording = Recording.load(record_path)
        doc["network"] = network_block(
            recording.records, idle_split_s=args.idle_split_s
        )
        doc["notes"].append(
            f"datagram recording through IcomSessionRecorder: {record_path} "
            f"({len(recording.records)} datagrams)"
        )
    else:
        doc["notes"].append("no datagram recording (test double); network block absent")

    if args.capture_mode == "tx" and args.emergency_serial:
        baud = args.baud or config.default_baud
        doc["notes"].append(
            _emergency_serial_unkey(
                args.emergency_serial,
                baud=baud,
                config=config,
                serial_module=serial_module,
            )
        )
    return doc, code


def _cmd_capture(
    args: argparse.Namespace,
    *,
    session_factory: Callable[[], object] | None = None,
    serial_module: object | None = None,
    env: dict[str, str] | None = None,
) -> tuple[dict[str, object] | None, int]:
    profiles = st.load_profiles()
    config = st.resolve_profile(args.radio, profiles)
    if config is None:
        print(
            f"refusal: --radio {args.radio!r} is not a shipped profile",
            file=sys.stderr,
        )
        return None, EXIT_REFUSED
    if args.capture_mode == "tx":
        if config.id != "icom_ic705":
            print(
                f"refusal: capture tx keys the IC-705 only, not {config.id}",
                file=sys.stderr,
            )
            return None, EXIT_REFUSED
        if not args.tx_armed or not args.dummy_load_confirmed:
            print(
                "refusal: capture tx requires --tx-armed --dummy-load-confirmed",
                file=sys.stderr,
            )
            return None, EXIT_REFUSED
    try:
        username, password = read_credentials(
            user_env=args.user_env, pass_file=args.pass_file, env=env
        )
    except st.RefusalError as exc:
        print(f"refusal: {exc}", file=sys.stderr)
        return None, EXIT_REFUSED
    session = (
        session_factory()
        if session_factory is not None
        else LanCivSession(radio_name=config.model)
    )
    record_path = args.record
    if record_path is None and session_factory is None:
        # Packet-level truth is required: always record a real capture.
        fd, record_path = tempfile.mkstemp(prefix="lan_timing_", suffix=".jsonl")
        os.close(fd)
        print(f"note: no --record given; recording to {record_path}", file=sys.stderr)

    # SIGTERM must become a catchable KeyboardInterrupt so the finally unkey
    # runs on termination too. Installed here (before the first keyed write,
    # also covering embeddings that call main()), restored in finally.
    previous_sigterm = None
    if args.capture_mode == "tx":

        def _sigterm_as_interrupt(_sig: int, _frame: object) -> None:
            raise KeyboardInterrupt()

        previous_sigterm = signal.getsignal(signal.SIGTERM)
        signal.signal(signal.SIGTERM, _sigterm_as_interrupt)
    try:
        try:
            return asyncio.run(
                _capture_async(
                    args,
                    config,
                    session=session,
                    username=username,
                    password=password,
                    record_path=record_path,
                    serial_module=serial_module,
                )
            )
        except st.RefusalError as exc:
            print(f"refusal: {exc}", file=sys.stderr)
            return None, EXIT_REFUSED
        except KeyboardInterrupt:
            print("interrupted before the capture could finish", file=sys.stderr)
            return None, EXIT_ABORT
        except (ConnectionError, TimeoutError, OSError) as exc:
            print(f"refusal: cannot reach {args.host}: {exc}", file=sys.stderr)
            return None, EXIT_REFUSED
    finally:
        if previous_sigterm is not None:
            signal.signal(signal.SIGTERM, previous_sigterm)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="lan_timing",
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    sub = parser.add_subparsers(dest="command", required=True)

    analyze = sub.add_parser(
        "analyze", help="derive timings + network quality from a datagram recording"
    )
    analyze.add_argument("recording", help="IcomSessionRecorder JSONL path")
    analyze.add_argument("--idle-split-s", type=float, default=_DEFAULT_IDLE_SPLIT_S)
    analyze.add_argument(
        "--radio", default=None, help="profile key (default: recording dir name)"
    )

    cap = sub.add_parser("capture", help="measure against a real IC-705 over Wi-Fi")
    cap_sub = cap.add_subparsers(dest="capture_mode", required=True)
    for mode in ("rx", "tx"):
        p = cap_sub.add_parser(mode)
        p.add_argument("--host", required=True)
        p.add_argument("--control-port", type=int, default=50001)
        p.add_argument("--radio", default="ic705")
        p.add_argument("--user-env", default="ICOM_USER")
        p.add_argument("--pass-file", default=None)
        p.add_argument("--record", default=None, help="datagram recording JSONL path")
        p.add_argument("--idle-split-s", type=float, default=_DEFAULT_IDLE_SPLIT_S)
        if mode == "rx":
            p.add_argument("--minutes", type=float, default=3.0)
        else:
            p.add_argument("--reps", type=int, default=_DEFAULT_REPS)
            p.add_argument("--tx-armed", action="store_true")
            p.add_argument("--dummy-load-confirmed", action="store_true")
            p.add_argument(
                "--emergency-serial",
                default=None,
                help="USB CI-V port for the independent second unkey path",
            )
            p.add_argument(
                "--baud",
                type=int,
                default=None,
                help="baud for --emergency-serial (default: profile default_baud)",
            )

    summ = sub.add_parser("summarize", help="print tables / TOML for documents")
    summ.add_argument("files", nargs="+")
    summ.add_argument(
        "--timing-toml",
        action="store_true",
        help="print a [timing] TOML block per document (serial mapping); PRINT-only",
    )
    summ.add_argument(
        "--fault-plan",
        action="store_true",
        help="print FaultRule-shaped TOML blocks from the network block; PRINT-only",
    )
    return parser


def _summarize(args: argparse.Namespace, out: Callable[[str], None]) -> int:
    docs = [json.loads(Path(f).read_text()) for f in args.files]
    if args.fault_plan:
        out(fault_plan_toml(docs))
    elif args.timing_toml:
        out("\n\n".join(st.timing_toml_block(doc) for doc in docs))
    else:
        text = st.summarize_documents(docs)
        lines = [text.rstrip()]
        for doc in docs:
            net = doc.get("network")
            if not net:
                continue
            rtt = net.get("rtt_ms") or {}
            lines.append(
                f"- network: rtt median {rtt.get('median', 'n/a')} ms, "
                f"jitter {net.get('jitter_ms', 'n/a')} ms, "
                f"loss {net.get('loss_pct')}%, reorder {net.get('reorder_pct')}%, "
                f"duplicates {net.get('duplicate_pct')}%"
            )
        out("\n".join(lines))
    return EXIT_OK


def main(
    argv: Sequence[str] | None = None,
    *,
    session_factory: Callable[[], object] | None = None,
    serial_module: object | None = None,
    env: dict[str, str] | None = None,
    out: Callable[[str], None] = print,
) -> int:
    args = build_parser().parse_args(argv)
    if args.command == "analyze":
        doc, code = _cmd_analyze(args)
    elif args.command == "capture":
        doc, code = _cmd_capture(
            args,
            session_factory=session_factory,
            serial_module=serial_module,
            env=env,
        )
    else:
        return _summarize(args, out)
    if doc is not None:
        out(json.dumps(doc, indent=2))
    return code


if __name__ == "__main__":
    sys.exit(main())
