#!/usr/bin/env python3
"""MOR-3839: capture REAL per-radio serial timings as data (rigplane-serial-timing/1).

The testkit simulator answers instantly, so TX races (MOR-3838) never reproduce
in CI. The simulator mechanism is another session's job; this tool produces the
DATA that ends up in the sim profiles' ``[timing]`` table: per-class
request->reply latencies, in milliseconds, measured with
``time.perf_counter_ns``.

Subcommands:

* ``from-recordings`` -- derive per-class latencies from recorded
  ``tests/fixtures/<radio>/{civ_pairs,cat_pairs}.jsonl`` ``t`` stamps. Pure:
  no serial port is touched.
* ``capture rx`` -- receive-side capture against a real port (no TX ever):
  reads plus set-to-current-value writes, paced >= 100 ms.
* ``capture tx`` -- keys the transmitter (IC-705 only, CI-V identity
  confirmed live, explicit ``--tx-armed --dummy-load-confirmed``, power at
  the profile minimum, 10 s keyed-time watchdog, emergency unkey in
  ``finally``).
* ``summarize`` -- print the per-radio median/p95/max table for Linear, or
  (``--timing-toml``) a ``[timing]`` TOML block per document for the sim
  profiles. This tool only PRINTS; it never edits profile TOMLs.

HARD SAFETY: the two ``capture`` modes are the only ones that open a serial
port. ``capture rx`` never sends a PTT command. ``capture tx`` refuses
(exit 2, nothing written) on any unmet precondition -- including an
unavailable ``lsof`` (it fails closed; ``capture rx`` only notes it) -- and
unkeys in ``finally`` on any failure (exit 1, evidence still written).
Keyed time is counted from BEFORE the key frame is written until the radio
is READ BACK as RX (or, when RX is never confirmed, until process end).
"""

from __future__ import annotations

import argparse
import json
import math
import os
import re
import socket
import subprocess
import sys
import threading
import time
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[2]
_SRC = _REPO_ROOT / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from rigplane.commands import (  # noqa: E402
    CONTROLLER_ADDR,
    build_civ_frame,
    parse_civ_frame,
)
from rigplane.commands._frame import decode_wire_tuple  # noqa: E402
from rigplane.commands.command_spec import CatCommandSpec, CivCommandSpec  # noqa: E402
from rigplane.core.civ import iter_civ_frames  # noqa: E402
from rigplane.core.types import CivFrame  # noqa: E402
from rigplane.profiles.rig_loader import RigConfig, load_rig  # noqa: E402

__all__ = [
    "SCHEMA",
    "KeyedWatchdog",
    "RefusalError",
    "SwitchDeadline",
    "build_document",
    "classify_cat_line",
    "classify_civ_frame",
    "derive_cat_latencies",
    "derive_civ_latencies",
    "emergency_unkey",
    "main",
    "open_serial_port",
    "port_holders",
    "run_rx_capture",
    "run_tx_capture",
    "summarize_documents",
    "timing_toml_block",
    "TIMING_TOML_MAPPING",
]

SCHEMA = "rigplane-serial-timing/1"

# Latency classes emitted by ``from-recordings``.
CLASS_READ = "read"
CLASS_SET = "set"
CLASS_PTT_READ = "ptt_read"
CLASS_PTT_SET = "ptt_set"
CLASS_OTHER = "other"
RECORDING_CLASSES = (CLASS_READ, CLASS_SET, CLASS_PTT_READ, CLASS_PTT_SET, CLASS_OTHER)

# Fallback read/set classification when no rig profile is available: CI-V
# commands that are write-only by nature (send/set freq & mode, VFO/memory
# select, scan, CW send). Reads carry no data on the shared get/set pairs,
# so "data empty" is the read fallback.
_FALLBACK_SET_COMMANDS = frozenset(
    (0x00, 0x01, 0x05, 0x06, 0x07, 0x08, 0x09, 0x0B, 0x0D, 0x17)
)

# CI-V ACK / NAK command bytes (a set's only reply).
_CIV_ACK = 0xFB
_CIV_NAK = 0xFA

# Hard caps for ``capture tx``.
_DEFAULT_REPS = 12
_KEYED_CAP_S = 10.0
_SWITCH_DEADLINE_S = 1.5
_HOLD_S = 0.3
_PTT_POLL_S = 0.003
_READ_WHILE_SWITCHING_OFFSET_S = 0.02
_UNKEY_ATTEMPTS = 5
_UNKEY_GAP_S = 0.05

EXIT_OK = 0
EXIT_ABORT = 1
EXIT_REFUSED = 2


class RefusalError(Exception):
    """A precondition failed before (or between) keying: exit 2, nothing written."""


class SwitchDeadline(Exception):
    """A TX on/off switch did not confirm within the deadline: abort, exit 1."""


class WatchdogTripped(Exception):
    """The keyed-time watchdog tripped: abort, exit 1."""


# ── statistics / document ────────────────────────────────────────────────


def _p95(samples: Sequence[float]) -> float:
    """Nearest-rank p95."""
    ordered = sorted(samples)
    return ordered[max(0, math.ceil(0.95 * len(ordered)) - 1)]


def _median(samples: Sequence[float]) -> float:
    ordered = sorted(samples)
    n = len(ordered)
    return ordered[n // 2] if n % 2 else (ordered[n // 2 - 1] + ordered[n // 2]) / 2


def summarize_samples(samples: Sequence[float]) -> dict[str, object]:
    """One ``classes`` entry: n, the raw samples, median, p95, max (ms)."""
    ordered = sorted(samples)
    return {
        "n": len(ordered),
        "samples_ms": [round(s, 3) for s in samples],
        "median": round(_median(samples), 3),
        "p95": round(_p95(samples), 3),
        "max": round(ordered[-1], 3),
    }


def _git_sha() -> str | None:
    try:
        proc = subprocess.run(
            ["git", "-C", str(_REPO_ROOT), "rev-parse", "HEAD"],
            capture_output=True,
            text=True,
            timeout=5,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    return proc.stdout.strip() or None if proc.returncode == 0 else None


def build_document(
    *,
    radio_id: str,
    protocol: str,
    port_kind: str | None,
    baud: int | None,
    samples: dict[str, list[float]],
    notes: list[str],
    host: str | None = None,
    generated_at: str | None = None,
    git_sha: str | None = None,
) -> dict[str, object]:
    """Assemble one ``rigplane-serial-timing/1`` document."""
    return {
        "schema": SCHEMA,
        "radio_id": radio_id,
        "protocol": protocol,
        "port_kind": port_kind,
        "baud": baud,
        "host": host if host is not None else socket.gethostname(),
        "generated_at": (
            generated_at
            if generated_at is not None
            else datetime.now(timezone.utc).isoformat()
        ),
        "git_sha": _git_sha() if git_sha is None else git_sha,
        "classes": {
            name: summarize_samples(values)
            for name, values in sorted(samples.items())
            if values
        },
        "notes": notes,
    }


# ── profile access ───────────────────────────────────────────────────────


def load_profiles(rigs_dir: Path | None = None) -> dict[str, RigConfig]:
    """All shipped rig profiles, keyed by file stem AND profile id AND model."""
    directory = rigs_dir or (_REPO_ROOT / "rigs")
    profiles: dict[str, RigConfig] = {}
    for path in sorted(directory.glob("*.toml")):
        if path.name.startswith("_") or path.name.endswith(".draft.toml"):
            continue
        config = load_rig(path)
        profiles[path.stem] = config
        profiles[config.id] = config
        profiles[config.model] = config
    return profiles


def resolve_profile(radio: str, profiles: dict[str, RigConfig]) -> RigConfig | None:
    return profiles.get(radio)


def _civ_signatures(
    config: RigConfig,
) -> list[tuple[str, int, int | None, bytes]]:
    """(name, command, sub, constant prefix) for every declared CI-V command."""
    sigs: list[tuple[str, int, int | None, bytes]] = []
    for name, spec in config.commands.items():
        if not isinstance(spec, CivCommandSpec):
            continue
        cmd, sub, prefix = decode_wire_tuple(tuple(spec.bytes))
        sigs.append((name, cmd, sub, prefix))
        for _value, wire in spec.value_variants:
            vcmd, vsub, vprefix = decode_wire_tuple(tuple(wire))
            sigs.append((name, vcmd, vsub, vprefix))
    return sigs


def _civ_frame_from_config(
    config: RigConfig, name: str, *, data: bytes | None = None
) -> bytes:
    """Build a complete CI-V frame for profile command *name* (+ *data*)."""
    spec = config.commands[name]
    if not isinstance(spec, CivCommandSpec):
        raise RefusalError(f"{config.id}: command {name!r} is not a CI-V command")
    cmd, sub, prefix = decode_wire_tuple(tuple(spec.bytes))
    payload = prefix + data if data else (prefix or None)
    return build_civ_frame(config.civ_addr, CONTROLLER_ADDR, cmd, sub=sub, data=payload)


# ── classification (from-recordings) ─────────────────────────────────────


def classify_civ_frame(
    frame: CivFrame,
    sigs: Sequence[tuple[str, int, int | None, bytes]] = (),
) -> str:
    """Classify a CI-V REQUEST frame as read/set/ptt_read/ptt_set/other.

    Uses the rig profile's declared wire tuples where practical: a request
    whose data equals a declared ``get_*`` prefix exactly is a read; a
    request whose data starts with a declared non-``get_*`` prefix and
    carries value bytes beyond it is a set. Falls back to a small command
    table otherwise. Command 0x1C sub 0x00 is the PTT pair.
    """
    command, sub, data = frame.command, frame.sub, frame.data
    if command == 0x1C and sub == 0x00:
        if not data:
            return CLASS_PTT_READ
        if data in (b"\x00", b"\x01"):
            return CLASS_PTT_SET
        return CLASS_OTHER
    read_hit = False
    set_hit = False
    for name, cmd, sig_sub, prefix in sigs:
        if (cmd, sig_sub) != (command, sub):
            continue
        if name.startswith("get_") and data == prefix:
            read_hit = True
        elif (
            not name.startswith("get_")
            and len(data) > len(prefix)
            and data.startswith(prefix)
        ):
            set_hit = True
    if set_hit and not read_hit:
        return CLASS_SET
    if read_hit:
        return CLASS_READ
    if command in _FALLBACK_SET_COMMANDS:
        return CLASS_SET
    if not data:
        return CLASS_READ
    return CLASS_OTHER


_CAT_FIELD = re.compile(r"\{[^{}]*\}")


def _cat_template_regex(template: str) -> re.Pattern[str]:
    """A read/write template as a fullmatch regex; ``{fields}`` become ``.+?``."""
    parts = _CAT_FIELD.split(template)
    return re.compile(".+?".join(re.escape(part) for part in parts))


def classify_cat_line(
    line: str,
    read_templates: Sequence[str] = (),
    write_templates: Sequence[str] = (),
) -> str:
    """Classify a Yaesu CAT REQUEST line as read/set/ptt_read/ptt_set/other.

    ``TX;`` reads PTT, ``TX<n>;`` keys it. Otherwise the profile's declared
    read/write templates decide; the fallback treats a bare two-letter
    command as a read (CAT reads like ``SM0;`` carry selector digits, so
    without a profile anything longer is "other", not silently "set").
    """
    body = line.rstrip(";")
    if body.startswith("TX"):
        return CLASS_PTT_READ if body == "TX" else CLASS_PTT_SET
    for template in read_templates:
        if _cat_template_regex(template).fullmatch(line):
            return CLASS_READ
    for template in write_templates:
        if _cat_template_regex(template).fullmatch(line):
            return CLASS_SET
    if len(body) <= 2:
        return CLASS_READ
    return CLASS_OTHER


def _cat_templates(config: RigConfig) -> tuple[list[str], list[str]]:
    reads: list[str] = []
    writes: list[str] = []
    for spec in config.commands.values():
        if not isinstance(spec, CatCommandSpec):
            continue
        if spec.read:
            reads.append(spec.read)
        if spec.write:
            writes.append(spec.write)
    return reads, writes


# ── from-recordings pairing ──────────────────────────────────────────────


@dataclass
class DeriveResult:
    samples: dict[str, list[float]] = field(
        default_factory=lambda: {name: [] for name in RECORDING_CLASSES}
    )
    echoes: int = 0
    unsolicited: int = 0
    unpaired: int = 0
    transports: list[str] = field(default_factory=list)


def _infer_civ_radio_addr(rows: Iterable[dict[str, object]]) -> int | None:
    """The radio's CI-V address: the most common c2r destination."""
    counts: dict[int, int] = {}
    for row in rows:
        if row.get("dir") != "c2r" or "frame" not in row:
            continue
        try:
            frame = parse_civ_frame(bytes.fromhex(str(row["frame"])))
        except ValueError:
            continue
        counts[frame.to_addr] = counts.get(frame.to_addr, 0) + 1
    return max(counts, key=counts.get) if counts else None


def derive_civ_latencies(
    rows: Iterable[dict[str, object]],
    sigs: Sequence[tuple[str, int, int | None, bytes]] = (),
) -> DeriveResult:
    """Pair each c2r request with its r2c reply; latency = ``t`` delta (ms).

    Echo-enabled links (a r2c row addressed TO the radio, e.g. IC-7300 LAN
    recordings echoing our own frame back) and unsolicited frames (scope
    waves, transceive broadcasts, answers to other commands) are skipped
    without being paired.
    """
    rows = list(rows)
    result = DeriveResult()
    radio_addr = _infer_civ_radio_addr(rows)
    pending: tuple[int, int | None, float, str] | None = None
    for row in rows:
        transport = row.get("transport")
        if isinstance(transport, str) and transport not in result.transports:
            result.transports.append(transport)
        if "frame" not in row:
            continue
        try:
            frame = parse_civ_frame(bytes.fromhex(str(row["frame"])))
        except ValueError:
            continue
        t = float(row.get("t", 0.0))
        if row.get("dir") == "c2r":
            if pending is not None:
                result.unpaired += 1
            pending = (
                frame.command,
                frame.sub,
                t,
                classify_civ_frame(frame, sigs),
            )
            continue
        # r2c row
        if radio_addr is not None and frame.to_addr == radio_addr:
            result.echoes += 1  # our own frame echoed back at us
            continue
        if radio_addr is not None and frame.from_addr != radio_addr:
            result.unsolicited += 1
            continue
        if pending is None:
            result.unsolicited += 1
            continue
        cmd, sub, t0, cls = pending
        if frame.command in (_CIV_ACK, _CIV_NAK) or (frame.command, frame.sub) == (
            cmd,
            sub,
        ):
            latency_ms = (t - t0) * 1000.0
            if latency_ms >= 0:
                result.samples[cls].append(latency_ms)
            pending = None
        else:
            result.unsolicited += 1  # not our reply; the request stays pending
    if pending is not None:
        result.unpaired += 1
    return result


def derive_cat_latencies(
    rows: Iterable[dict[str, object]],
    read_templates: Sequence[str] = (),
    write_templates: Sequence[str] = (),
) -> DeriveResult:
    """Pair each c2r CAT line with the first r2c line on the same command."""
    result = DeriveResult()
    pending: tuple[str, float, str] | None = None
    for row in rows:
        transport = row.get("transport")
        if isinstance(transport, str) and transport not in result.transports:
            result.transports.append(transport)
        line = row.get("line")
        if not isinstance(line, str):
            continue
        t = float(row.get("t", 0.0))
        if row.get("dir") == "c2r":
            if pending is not None:
                result.unpaired += 1
            pending = (
                line[:2],
                t,
                classify_cat_line(line, read_templates, write_templates),
            )
            continue
        if line.startswith("?"):
            result.unpaired += 1
            pending = None
            continue
        if pending is None:
            result.unsolicited += 1
            continue
        cmd, t0, cls = pending
        if line[:2] == cmd:
            latency_ms = (t - t0) * 1000.0
            if latency_ms >= 0:
                result.samples[cls].append(latency_ms)
            pending = None
        else:
            result.unsolicited += 1
    if pending is not None:
        result.unpaired += 1
    return result


def _merge_probe_answers(
    path: Path,
    samples: dict[str, list[float]],
    sigs: Sequence[tuple[str, int, int | None, bytes]],
) -> int:
    """Merge a probe_answers JSON (frame_hex/frame + latency_ms) as samples."""
    data = json.loads(path.read_text())
    entries = data.get("probes") or data.get("answers") or []
    merged = 0
    for entry in entries:
        latency = entry.get("latency_ms")
        frame_hex = entry.get("frame") or entry.get("frame_hex")
        if not isinstance(latency, (int, float)) or not frame_hex:
            continue
        if entry.get("reply", True) is None:
            continue  # silence entries carry a timeout, not a radio latency
        try:
            frame = parse_civ_frame(bytes.fromhex(str(frame_hex)))
        except ValueError:
            continue
        samples[classify_civ_frame(frame, sigs)].append(float(latency))
        merged += 1
    return merged


# ── serial port handling (capture modes only) ────────────────────────────


def port_holders(
    port: str, runner: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run
) -> list[str] | None:
    """Processes holding *port* per ``lsof``; ``None`` when lsof is unavailable."""
    try:
        proc = runner(["lsof", "--", port], capture_output=True, text=True, timeout=10)
    except (FileNotFoundError, subprocess.SubprocessError):
        return None
    if proc.returncode != 0:
        return []
    return [line for line in proc.stdout.splitlines()[1:] if line.strip()]


def open_serial_port(port: str, baud: int, serial_module: object | None = None):
    """Open *port* with DTR/RTS forced False BEFORE open (never key via RS-232).

    *serial_module* is injectable for tests; production uses pyserial.
    """
    if serial_module is None:
        import serial as serial_module  # pyserial

    ser = serial_module.Serial()  # type: ignore[union-attr]
    ser.port = port
    ser.baudrate = baud
    ser.dtr = False
    ser.rts = False
    ser.timeout = 0.05
    # A blocked write must not hang past the keyed-time cap.
    ser.write_timeout = 1.0
    ser.open()
    return ser


def _drain(ser: object) -> None:
    """Wait until the write has physically left the UART."""
    ser.flush()  # type: ignore[attr-defined]
    fd = getattr(ser, "fd", None)
    if fd is not None and hasattr(os, "tcdrain"):
        try:
            os.tcdrain(fd)
        except OSError:
            pass


@dataclass(frozen=True, slots=True)
class CivReply:
    """One measured CI-V request->reply exchange."""

    frame: CivFrame
    raw: bytes
    write_drain_ms: float
    ack_ms: float  # last byte written -> first non-echo reply byte
    frame_ms: float  # last byte written -> complete reply frame


def _read_civ_reply(
    ser: object,
    request: bytes,
    *,
    deadline_ns: int,
    clock: Callable[[], int],
) -> tuple[CivFrame, bytes, int, int] | None:
    """Read until the first non-echo CI-V frame completes, or *deadline_ns*.

    Returns ``(frame, raw, first_byte_ns, complete_ns)``. Echo bytes (our own
    request frame coming back at us on echo-enabled links) are dropped and
    never mark ``first_byte_ns``.
    """
    buf = bytearray()
    first_ns: int | None = None
    while True:
        now = clock()
        if now >= deadline_ns:
            return None
        ser.timeout = max(0.0, min(0.02, (deadline_ns - now) / 1e9))  # type: ignore[attr-defined]
        chunk = ser.read(64)  # type: ignore[attr-defined]
        if not chunk:
            continue
        buf += chunk
        complete_ns: int | None = None
        for frame_bytes in iter_civ_frames(bytes(buf)):
            if frame_bytes == request:
                # Echo of our own write: drop it so first_ns only ever marks
                # bytes of the radio's own answer.
                del buf[: buf.find(frame_bytes) + len(frame_bytes)]
                if not buf:
                    first_ns = None
                continue
            try:
                parsed = parse_civ_frame(frame_bytes)
            except ValueError:
                continue
            complete_ns = clock()
            if first_ns is None:
                first_ns = complete_ns
            return parsed, bytes(frame_bytes), first_ns, complete_ns
        if first_ns is None and buf:
            first_ns = clock()


def civ_transact(
    ser: object,
    request: bytes,
    *,
    timeout_s: float,
    clock: Callable[[], int] = time.perf_counter_ns,
) -> CivReply:
    """Write *request*, drain, and measure ack/frame latency of the reply.

    Raises ``TimeoutError`` when no non-echo frame completes in time.
    """
    t0 = clock()
    ser.write(request)  # type: ignore[attr-defined]
    _drain(ser)
    t1 = clock()
    got = _read_civ_reply(
        ser, request, deadline_ns=t1 + int(timeout_s * 1e9), clock=clock
    )
    if got is None:
        raise TimeoutError(f"no reply to {request.hex()} within {timeout_s:.3f}s")
    frame, raw, first_ns, complete_ns = got
    return CivReply(
        frame=frame,
        raw=raw,
        write_drain_ms=(t1 - t0) / 1e6,
        ack_ms=(first_ns - t1) / 1e6,
        frame_ms=(complete_ns - t1) / 1e6,
    )


def read_cat_line(
    ser: object,
    *,
    deadline_ns: int,
    clock: Callable[[], int] = time.perf_counter_ns,
) -> tuple[str, int, int] | None:
    """Read one ``;``-terminated CAT line; returns (line, first_ns, complete_ns)."""
    buf = bytearray()
    first_ns: int | None = None
    while True:
        now = clock()
        if now >= deadline_ns:
            return None
        ser.timeout = max(0.0, min(0.02, (deadline_ns - now) / 1e9))  # type: ignore[attr-defined]
        chunk = ser.read(64)  # type: ignore[attr-defined]
        if not chunk:
            continue
        if first_ns is None:
            first_ns = clock()
        buf += chunk
        end = buf.find(b";")
        if end >= 0:
            return buf[: end + 1].decode("ascii", errors="replace"), first_ns, clock()


# ── capture rx ───────────────────────────────────────────────────────────

#: CI-V read probes for ``capture rx`` (profile command names, resolved
#: against the rig profile; missing names are skipped).
_RX_READ_PROBES = (
    "get_freq",
    "get_mode",
    "get_s_meter",
    "get_transceiver_status",  # 1C 00 PTT read
    "get_transceiver_id",  # 19 00 identity
)

#: CAT read lines for ``capture rx``.
_RX_CAT_READS = ("FA;", "MD;", "SM0;", "TX;", "ID;")


def _civ_identity_check(
    ser: object, config: RigConfig, clock: Callable[[], int]
) -> None:
    frame = _civ_frame_from_config(config, "get_transceiver_id")
    reply = civ_transact(ser, frame, timeout_s=1.0, clock=clock)
    if not reply.frame.data or reply.frame.data[0] != config.civ_addr:
        raise RefusalError(
            f"identity read answered {reply.raw.hex() or 'nothing'}: "
            f"expected CI-V address 0x{config.civ_addr:02X} for {config.id}"
        )


def _cat_identity_check(
    ser: object, config: RigConfig, clock: Callable[[], int]
) -> None:
    ser.write(b"ID;")  # type: ignore[attr-defined]
    _drain(ser)
    got = read_cat_line(ser, deadline_ns=clock() + 1_000_000_000, clock=clock)
    if got is None:
        raise RefusalError("identity read ID; timed out")
    line = got[0]
    expected = config.expected_identity_ids
    model = line[2:].rstrip(";")
    if expected and model not in expected:
        raise RefusalError(
            f"identity read answered {line!r}: expected one of {expected} for {config.id}"
        )


def run_rx_capture(
    *,
    ser: object,
    config: RigConfig,
    radio_key: str,
    baud: int,
    n: int = 50,
    pace_s: float = 0.1,
    clock: Callable[[], int] = time.perf_counter_ns,
    sleep: Callable[[float], None] = time.sleep,
) -> dict[str, object]:
    """Receive-side capture: reads + set-to-current-value only. Never TX.

    *set* samples write back exactly what was just read (AF level), then
    require a read-back equal -- the radio never moves.
    """
    samples: dict[str, list[float]] = {}
    notes: list[str] = []

    def add(cls: str, value_ms: float) -> None:
        samples.setdefault(cls, []).append(value_ms)

    last_request_end = 0.0

    def pace() -> None:
        nonlocal last_request_end
        now = time.monotonic()
        wait = pace_s - (now - last_request_end)
        if wait > 0:
            sleep(wait)
        last_request_end = time.monotonic()

    if config.protocol_type == "civ":
        _civ_identity_check(ser, config, clock)
        probe_frames = [
            (name, _civ_frame_from_config(config, name))
            for name in _RX_READ_PROBES
            if name in config.commands
        ]
        set_level_frame_read = (
            _civ_frame_from_config(config, "get_af_level")
            if "get_af_level" in config.commands
            else None
        )
        for _rep in range(n):
            for name, frame in probe_frames:
                pace()
                reply = civ_transact(ser, frame, timeout_s=1.0, clock=clock)
                add("write_drain", reply.write_drain_ms)
                add("ack_read", reply.ack_ms)
                add("frame_read", reply.frame_ms)
                if name == "get_transceiver_status":
                    add("ptt_read_idle", reply.ack_ms)
            if set_level_frame_read is not None:
                pace()
                read1 = civ_transact(
                    ser, set_level_frame_read, timeout_s=1.0, clock=clock
                )
                add("write_drain", read1.write_drain_ms)
                add("ack_read", read1.ack_ms)
                add("frame_read", read1.frame_ms)
                set_frame = _civ_frame_from_config(
                    config, "set_af_level", data=read1.frame.data
                )
                pace()
                acked = civ_transact(ser, set_frame, timeout_s=1.0, clock=clock)
                add("write_drain", acked.write_drain_ms)
                add("ack_set", acked.ack_ms)
                add("frame_set", acked.frame_ms)
                pace()
                read2 = civ_transact(
                    ser, set_level_frame_read, timeout_s=1.0, clock=clock
                )
                if read2.frame.data != read1.frame.data:
                    raise RefusalError(
                        "set-to-current read-back differs: "
                        f"{read1.frame.data.hex()} -> {read2.frame.data.hex()}"
                    )
    else:
        _cat_identity_check(ser, config, clock)
        freq_line: str | None = None
        for _rep in range(n):
            for line in _RX_CAT_READS:
                pace()
                t0 = clock()
                ser.write(line.encode("ascii"))  # type: ignore[attr-defined]
                _drain(ser)
                t1 = clock()
                got = read_cat_line(ser, deadline_ns=t1 + 1_000_000_000, clock=clock)
                if got is None:
                    raise TimeoutError(f"no reply to {line!r}")
                text, first_ns, complete_ns = got
                add("write_drain", (t1 - t0) / 1e6)
                add("ack_read", (first_ns - t1) / 1e6)
                add("frame_read", (complete_ns - t1) / 1e6)
                if line == "TX;":
                    add("ptt_read_idle", (first_ns - t1) / 1e6)
                if line == "FA;":
                    freq_line = text
            # set-to-current on CAT: write the frequency just read back; a
            # CAT write is answered with silence, so only write_drain is
            # measurable -- the read-back below proves the radio did not move.
            if freq_line is None:
                raise RefusalError("no FA; read succeeded: cannot set-to-current")
            pace()
            t0 = clock()
            ser.write(freq_line.encode("ascii"))  # type: ignore[attr-defined]
            _drain(ser)
            add("write_drain", (clock() - t0) / 1e6)
            pace()
            t0 = clock()
            ser.write(b"FA;")  # type: ignore[attr-defined]
            _drain(ser)
            t1 = clock()
            got = read_cat_line(ser, deadline_ns=t1 + 1_000_000_000, clock=clock)
            if got is None or got[0] != freq_line:
                raise RefusalError(
                    f"set-to-current read-back differs: {freq_line!r} -> "
                    f"{got[0] if got else 'timeout'!r}"
                )
        notes.append(
            "CAT sets are answered with silence: ack_set/frame_set unmeasurable"
        )

    return build_document(
        radio_id=config.id if radio_key in ("", config.id) else radio_key,
        protocol="civ" if config.protocol_type == "civ" else "cat",
        port_kind="serial",
        baud=baud,
        samples=samples,
        notes=notes,
    )


# ── capture tx ───────────────────────────────────────────────────────────


class KeyedWatchdog:
    """Hard cap on cumulative keyed time, enforced by a monitor thread.

    ``check()`` (called before every rep and inside every poll loop) raises
    :class:`WatchdogTripped` once cumulative keyed time exceeds *cap_s*.
    """

    def __init__(
        self,
        cap_s: float = _KEYED_CAP_S,
        *,
        tick_s: float = 0.01,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._cap_s = cap_s
        self._tick_s = tick_s
        self._clock = clock
        self._lock = threading.Lock()
        self._keyed_since: float | None = None
        self._spent_s = 0.0
        self._tripped = threading.Event()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def key_on(self) -> None:
        with self._lock:
            self._keyed_since = self._clock()

    def key_off(self) -> None:
        with self._lock:
            if self._keyed_since is not None:
                self._spent_s += self._clock() - self._keyed_since
                self._keyed_since = None

    def keyed_total_s(self) -> float:
        with self._lock:
            total = self._spent_s
            if self._keyed_since is not None:
                total += self._clock() - self._keyed_since
            return total

    @property
    def tripped(self) -> bool:
        return self._tripped.is_set()

    def check(self) -> None:
        if self._tripped.is_set():
            raise WatchdogTripped(
                f"keyed-time watchdog tripped (cap {self._cap_s:.3f}s)"
            )

    def start(self) -> None:
        def run() -> None:
            while not self._stop.wait(self._tick_s):
                if self.keyed_total_s() > self._cap_s:
                    self._tripped.set()
                    return

        self._thread = threading.Thread(
            target=run, name="tx-keyed-watchdog", daemon=True
        )
        self._thread.start()

    def close(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=1.0)


def _decode_level_raw(data: bytes) -> int:
    """Icom 2-byte BCD level (0..255) as carried by e.g. 14 0A replies.

    Byte 0 is the hundreds digit (0-2), byte 1 holds the tens and units
    digits: ``00 00`` -> 0, ``02 55`` -> 255.
    """
    if len(data) < 2:
        raise RefusalError(f"short level reply: {data.hex()}")
    return data[0] * 100 + (data[1] >> 4) * 10 + (data[1] & 0x0F)


def emergency_unkey(
    ser: object,
    unkey_frame: bytes,
    status_frame: bytes,
    *,
    attempts: int = _UNKEY_ATTEMPTS,
    gap_s: float = _UNKEY_GAP_S,
    clock: Callable[[], int] = time.perf_counter_ns,
    sleep: Callable[[float], None] = time.sleep,
) -> bool:
    """Send the unkey frame up to *attempts* times, verifying with a PTT read.

    Best-effort by construction: every step swallows errors so a dead link
    still gets all attempts. Returns True when the radio confirmed RX.
    """
    for _ in range(attempts):
        try:
            ser.write(unkey_frame)  # type: ignore[attr-defined]
            _drain(ser)
        except Exception:
            pass
        sleep(gap_s)
        try:
            reply = civ_transact(ser, status_frame, timeout_s=0.3, clock=clock)
            if reply.frame.data == b"\x00":
                return True
        except Exception:
            pass
    return False


def _poll_ptt_state(
    ser: object,
    status_frame: bytes,
    want: bytes,
    *,
    deadline_ns: int,
    poll_s: float,
    watchdog: KeyedWatchdog,
    clock: Callable[[], int],
    sleep: Callable[[float], None],
) -> int:
    """Poll ``1C 00`` until it reads *want*; returns the completion time (ns)."""
    while True:
        watchdog.check()
        now = clock()
        if now >= deadline_ns:
            raise SwitchDeadline(f"PTT did not reach {want.hex()} within deadline")
        try:
            reply = civ_transact(ser, status_frame, timeout_s=0.2, clock=clock)
        except TimeoutError:
            reply = None
        if reply is not None and reply.frame.data == want:
            return clock()
        sleep(poll_s)


def run_tx_capture(
    *,
    ser: object,
    config: RigConfig,
    radio_key: str,
    baud: int,
    reps: int = _DEFAULT_REPS,
    cap_keyed_s: float = _KEYED_CAP_S,
    hold_s: float = _HOLD_S,
    poll_s: float = _PTT_POLL_S,
    read_offset_s: float = _READ_WHILE_SWITCHING_OFFSET_S,
    switch_deadline_s: float = _SWITCH_DEADLINE_S,
    watchdog_tick_s: float = 0.01,
    clock: Callable[[], int] = time.perf_counter_ns,
    sleep: Callable[[float], None] = time.sleep,
) -> tuple[dict[str, object], int]:
    """TX capture: key/unkey at minimum power, measuring switch times.

    Callers must have resolved *config* to the IC-705 profile and confirmed
    the CI-V identity live BEFORE calling this (see :func:`main`). On any
    failure the transmitter is unkeyed in ``finally`` and the partial
    evidence is returned with exit code 1.
    """
    power_min_raw = 0
    controls = config.controls or {}
    rf_power = controls.get("rf_power")
    if rf_power is not None and "raw_min" in rf_power:
        power_min_raw = int(rf_power["raw_min"])

    frames = {
        "power": _civ_frame_from_config(config, "get_rf_power"),
        "ptt_on": _civ_frame_from_config(config, "ptt_on"),
        "ptt_off": _civ_frame_from_config(config, "ptt_off"),
        "ptt_read": _civ_frame_from_config(config, "get_transceiver_status"),
        "freq": _civ_frame_from_config(config, "get_freq"),
    }

    samples: dict[str, list[float]] = {}
    notes: list[str] = []

    def add(cls: str, value_ms: float) -> None:
        samples.setdefault(cls, []).append(value_ms)

    watchdog = KeyedWatchdog(cap_keyed_s, tick_s=watchdog_tick_s)
    watchdog.start()
    abort_reason: str | None = None
    confirmed: bool | None = None
    try:
        for rep in range(reps):
            watchdog.check()
            power = civ_transact(ser, frames["power"], timeout_s=1.0, clock=clock)
            add("write_drain", power.write_drain_ms)
            add("ack_read", power.ack_ms)
            raw_level = _decode_level_raw(power.frame.data)
            if raw_level != power_min_raw:
                raise RefusalError(
                    f"RF power raw {raw_level} != profile minimum {power_min_raw}: "
                    "the owner sets minimum power; this tool never raises it"
                )
            idle = civ_transact(ser, frames["ptt_read"], timeout_s=1.0, clock=clock)
            add("ptt_read_idle", idle.ack_ms)
            if idle.frame.data == b"\x01":
                raise RefusalError("radio is already keyed before the capture rep")

            # ── key on ──
            # Keyed time counts from BEFORE the key frame is written: when
            # the write keys the radio but the ACK is lost, the cap still
            # applies and the abort path measures the real exposure.
            watchdog.key_on()
            key_reply = civ_transact(ser, frames["ptt_on"], timeout_s=1.0, clock=clock)
            keyed_at = clock()
            add("write_drain", key_reply.write_drain_ms)
            add("ptt_set_ack_on", key_reply.ack_ms)
            add("ack_set", key_reply.ack_ms)

            # One read 20 ms after the key command, while the TX path switches.
            wait_s = read_offset_s - (clock() - keyed_at) / 1e9
            if wait_s > 0:
                sleep(wait_s)
            during = civ_transact(ser, frames["freq"], timeout_s=0.5, clock=clock)
            add("ack_read_while_switching", during.ack_ms)

            switched_on_at = _poll_ptt_state(
                ser,
                frames["ptt_read"],
                b"\x01",
                deadline_ns=keyed_at + int(switch_deadline_s * 1e9),
                poll_s=poll_s,
                watchdog=watchdog,
                clock=clock,
                sleep=sleep,
            )
            add("tx_on_switch", (switched_on_at - keyed_at) / 1e6)

            sleep(hold_s)

            # ── key off ──
            unkey_reply = civ_transact(
                ser, frames["ptt_off"], timeout_s=1.0, clock=clock
            )
            unkeyed_at = clock()
            add("write_drain", unkey_reply.write_drain_ms)
            add("ptt_set_ack_off", unkey_reply.ack_ms)
            add("ack_set", unkey_reply.ack_ms)

            switched_off_at = _poll_ptt_state(
                ser,
                frames["ptt_read"],
                b"\x00",
                deadline_ns=unkeyed_at + int(switch_deadline_s * 1e9),
                poll_s=poll_s,
                watchdog=watchdog,
                clock=clock,
                sleep=sleep,
            )
            # Keyed time ends when the radio is READ BACK as RX, not at the
            # unkey ACK (same conservative rule as the MOR-3818 bench).
            watchdog.key_off()
            add("tx_off_switch", (switched_off_at - unkeyed_at) / 1e6)
            watchdog.check()
            notes.append(f"rep {rep + 1}/{reps} complete")

        # Re-read the power once after the last rep: a mid-run change
        # invalidates every timing in this document.
        final_power = civ_transact(ser, frames["power"], timeout_s=1.0, clock=clock)
        add("write_drain", final_power.write_drain_ms)
        add("ack_read", final_power.ack_ms)
        final_level = _decode_level_raw(final_power.frame.data)
        notes.append(
            f"final RF power raw {final_level} (profile minimum {power_min_raw})"
        )
        if final_level != power_min_raw:
            abort_reason = (
                f"RF power changed during the capture: {power_min_raw} -> "
                f"{final_level}; document FAILED, discard these timings"
            )
    except RefusalError:
        raise
    except BaseException as exc:  # deadline, watchdog, serial error, SIGINT/SIGTERM
        abort_reason = f"{type(exc).__name__}: {exc}"
    finally:
        if abort_reason is not None:
            confirmed = emergency_unkey(
                ser, frames["ptt_off"], frames["ptt_read"], clock=clock, sleep=sleep
            )
            if confirmed:
                # Keyed time ends at the emergency unkey's RX confirmation.
                watchdog.key_off()
            # Never confirmed: keyed time keeps counting until process end.
        keyed = watchdog.keyed_total_s()
        watchdog.close()

    if abort_reason is None:
        return (
            build_document(
                radio_id=radio_key,
                protocol="civ",
                port_kind="serial",
                baud=baud,
                samples=samples,
                notes=notes + [f"total keyed time {keyed:.3f}s"],
            ),
            EXIT_OK,
        )

    doc = build_document(
        radio_id=radio_key,
        protocol="civ",
        port_kind="serial",
        baud=baud,
        samples=samples,
        notes=notes
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


# ── summarize ────────────────────────────────────────────────────────────


def summarize_documents(docs: Iterable[dict[str, object]]) -> str:
    """Render one Markdown table per radio for pasting into Linear."""
    out: list[str] = []
    for doc in docs:
        out.append(
            f"### {doc.get('radio_id')} "
            f"({doc.get('protocol')}, {doc.get('port_kind')}, "
            f"baud={doc.get('baud')}, host={doc.get('host')}, "
            f"{doc.get('generated_at')})"
        )
        out.append("| class | n | median ms | p95 ms | max ms |")
        out.append("|---|---|---|---|---|")
        classes = doc.get("classes") or {}
        for name, stats in classes.items():  # type: ignore[union-attr]
            out.append(
                f"| {name} | {stats['n']} | {stats['median']} | "
                f"{stats['p95']} | {stats['max']} |"
            )
        notes = doc.get("notes") or []
        for note in notes:  # type: ignore[union-attr]
            out.append(f"- {note}")
        out.append("")
    return "\n".join(out).rstrip() + "\n"


#: ``summarize --timing-toml`` mapping: TOML key -> source latency classes
#: (median over all their samples together; a key is omitted when none of
#: its classes has samples). Data for the simulator ``[timing]`` table.
TIMING_TOML_MAPPING: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("reply_latency_ms", ("ack_read",)),
    ("ack_ms_set", ("ack_set",)),
    ("ptt_set_ack_ms", ("ptt_set_ack_on", "ptt_set_ack_off")),
    ("write_drain_ms", ("write_drain",)),
    ("tx_on_switch_ms", ("tx_on_switch",)),
    ("tx_off_switch_ms", ("tx_off_switch",)),
    ("readback_ms", ("ptt_read_idle",)),
)


def timing_toml_block(doc: dict[str, object]) -> str:
    """One ``[timing]`` TOML block (float ms, median only) for a document.

    PRINT-only output for the sim profile ``[timing]`` table; this tool
    never edits profile TOMLs (another session owns the loader).
    """
    classes: dict[str, object] = doc.get("classes") or {}  # type: ignore[assignment]
    lines = [
        f"# {doc.get('radio_id')} ({doc.get('protocol')}, "
        f"host={doc.get('host')}, {doc.get('generated_at')})",
        "[timing]",
    ]
    for key, sources in TIMING_TOML_MAPPING:
        samples: list[float] = []
        for name in sources:
            entry = classes.get(name)
            if entry:
                samples.extend(
                    float(s)
                    for s in entry["samples_ms"]  # type: ignore[index]
                )
        if not samples:
            continue
        lines.append(f"{key} = {round(_median(samples), 3)}")
    return "\n".join(lines)


# ── CLI ──────────────────────────────────────────────────────────────────


def _serial_recorder_rows(path: Path) -> list[dict[str, object]]:
    """SerialCivRecorder JSONL (``{"t","dir","port","hex"}``) as pairing rows.

    Frame splitting is the testkit's :func:`civ_frames` -- re-assembling
    frames split across chunks is NOT re-implemented here.
    """
    if str(_REPO_ROOT) not in sys.path:
        sys.path.insert(0, str(_REPO_ROOT))
    from rigplane_testkit.serial.recorder import civ_frames

    return [
        {"t": t, "dir": direction, "frame": frame.hex()}
        for t, direction, frame in civ_frames(path)
    ]


def _cmd_from_recordings(
    args: argparse.Namespace,
) -> tuple[dict[str, object] | None, int]:
    fixture = Path(args.fixture)
    lines = [line for line in fixture.read_text().splitlines() if line.strip()]
    first = json.loads(lines[0]) if lines else {}
    notes: list[str] = []
    if "hex" in first:  # SerialCivRecorder JSONL: {"t","dir","port","hex"}
        rows = _serial_recorder_rows(fixture)
        notes.append(
            f"SerialCivRecorder JSONL: re-assembled {len(rows)} frames "
            "via rigplane_testkit civ_frames()"
        )
    else:
        rows = [json.loads(line) for line in lines]
    radio_key = fixture.parent.name
    profiles = load_profiles()
    config = resolve_profile(radio_key, profiles)
    protocol = "cat" if any("line" in row for row in rows) else "civ"
    if protocol == "civ":
        sigs = _civ_signatures(config) if config is not None else []
        result = derive_civ_latencies(rows, sigs)
    else:
        reads, writes = _cat_templates(config) if config is not None else ([], [])
        result = derive_cat_latencies(rows, reads, writes)
    if config is None:
        notes.append(f"no rig profile for {radio_key!r}: fallback classification")
    if args.probe_answers:
        merged = _merge_probe_answers(
            Path(args.probe_answers),
            result.samples,
            _civ_signatures(config) if config is not None else [],
        )
        notes.append(f"merged {merged} probe-answer samples from {args.probe_answers}")
    notes.append(
        f"pairing: echoes={result.echoes} unsolicited={result.unsolicited} "
        f"unpaired={result.unpaired}"
    )
    port_kind = result.transports[0] if result.transports else None
    doc = build_document(
        radio_id=radio_key,
        protocol=protocol,
        port_kind=port_kind,
        baud=None,
        samples=result.samples,
        notes=notes,
    )
    return doc, EXIT_OK


def _resolve_capture_profile(
    radio: str, profiles: dict[str, RigConfig]
) -> RigConfig | None:
    return resolve_profile(radio, profiles)


def _cmd_capture(
    args: argparse.Namespace,
    *,
    serial_module: object | None = None,
    runner: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run,
) -> tuple[dict[str, object] | None, int]:
    profiles = load_profiles()
    config = _resolve_capture_profile(args.radio, profiles)
    if config is None:
        print(
            f"refusal: --radio {args.radio!r} is not a shipped profile", file=sys.stderr
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
    holders = port_holders(args.port, runner=runner)
    if holders is None:
        if args.capture_mode == "tx":
            # TX keys a transmitter: without lsof the port's freeness is
            # unverifiable, so fail closed (rx never keys: note only).
            print(
                f"refusal: lsof unavailable; cannot verify {args.port} is "
                "free; capture tx fails closed",
                file=sys.stderr,
            )
            return None, EXIT_REFUSED
        print(
            f"note: lsof unavailable; cannot verify {args.port} is free",
            file=sys.stderr,
        )
    elif holders:
        print(
            f"refusal: {args.port} is held by another process:\n" + "\n".join(holders),
            file=sys.stderr,
        )
        return None, EXIT_REFUSED
    try:
        ser = open_serial_port(args.port, args.baud, serial_module=serial_module)
    except Exception as exc:
        print(f"refusal: cannot open {args.port}: {exc}", file=sys.stderr)
        return None, EXIT_REFUSED
    try:
        if args.capture_mode == "rx":
            return (
                run_rx_capture(
                    ser=ser,
                    config=config,
                    radio_key=args.radio,
                    baud=args.baud,
                    n=args.n,
                ),
                EXIT_OK,
            )
        # tx: SIGTERM must become a catchable KeyboardInterrupt so the
        # finally unkey runs on termination too. Installed here (not only in
        # __main__) BEFORE the first keyed write, restored in finally.
        import signal

        def _sigterm_as_interrupt(_sig: int, _frame: object) -> None:
            raise KeyboardInterrupt()

        previous_sigterm = signal.getsignal(signal.SIGTERM)
        signal.signal(signal.SIGTERM, _sigterm_as_interrupt)
        try:
            # identity confirmed live BEFORE any keyed write
            try:
                _civ_identity_check(ser, config, time.perf_counter_ns)
            except TimeoutError as exc:
                print(f"refusal: identity read timed out: {exc}", file=sys.stderr)
                return None, EXIT_REFUSED
            return run_tx_capture(
                ser=ser,
                config=config,
                radio_key=args.radio,
                baud=args.baud,
                reps=args.reps,
            )
        finally:
            signal.signal(signal.SIGTERM, previous_sigterm)
    except RefusalError as exc:
        print(f"refusal: {exc}", file=sys.stderr)
        return None, EXIT_REFUSED
    finally:
        try:
            ser.close()  # type: ignore[attr-defined]
        except Exception:
            pass


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="serial_timing",
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    sub = parser.add_subparsers(dest="command", required=True)

    rec = sub.add_parser(
        "from-recordings", help="derive latencies from recorded fixture t stamps"
    )
    rec.add_argument("--fixture", required=True, help="civ_pairs/cat_pairs JSONL path")
    rec.add_argument(
        "--probe-answers", default=None, help="optional probe_answers JSON"
    )

    cap = sub.add_parser("capture", help="measure against a real serial port")
    cap_sub = cap.add_subparsers(dest="capture_mode", required=True)
    for mode in ("rx", "tx"):
        p = cap_sub.add_parser(mode)
        p.add_argument("--radio", required=True)
        p.add_argument("--port", required=True)
        p.add_argument("--baud", type=int, default=None)
        if mode == "rx":
            p.add_argument("--n", type=int, default=50)
        else:
            p.add_argument("--reps", type=int, default=_DEFAULT_REPS)
            p.add_argument("--tx-armed", action="store_true")
            p.add_argument("--dummy-load-confirmed", action="store_true")

    summ = sub.add_parser("summarize", help="print the per-radio table for Linear")
    summ.add_argument("files", nargs="+")
    summ.add_argument(
        "--timing-toml",
        action="store_true",
        help="print a [timing] TOML block per document (median ms) instead "
        "of the table; PRINT-only, never edits profile TOMLs",
    )
    return parser


def main(
    argv: Sequence[str] | None = None,
    *,
    serial_module: object | None = None,
    runner: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run,
    out: Callable[[str], None] = print,
) -> int:
    args = build_parser().parse_args(argv)
    if args.command == "from-recordings":
        doc, code = _cmd_from_recordings(args)
    elif args.command == "capture":
        if args.baud is None:
            profiles = load_profiles()
            cfg = _resolve_capture_profile(args.radio, profiles)
            args.baud = cfg.default_baud if cfg is not None else 115200
        doc, code = _cmd_capture(args, serial_module=serial_module, runner=runner)
    else:
        docs = [json.loads(Path(f).read_text()) for f in args.files]
        if args.timing_toml:
            out("\n\n".join(timing_toml_block(doc) for doc in docs))
        else:
            out(summarize_documents(docs))
        return EXIT_OK
    if doc is not None:
        out(json.dumps(doc, indent=2))
    return code


if __name__ == "__main__":
    # The SIGTERM -> KeyboardInterrupt conversion for the tx path lives in
    # _cmd_capture (installed before the first keyed write, restored in
    # finally) so it also covers main() called from embeddings, not just
    # this entry point.
    sys.exit(main())
