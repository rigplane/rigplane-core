"""Paced, single-flight, read-only probe runner (MOR-4464).

One command in flight, never retried, one pass. The transport is a small
typed protocol so tests use fakes: ``exchange`` sends one request and
returns the first reply chunk (``None`` on timeout); ``exchange(None,
...)`` only reads. Production exchanges built on
``core.serial_open.open_serial_port`` keep DTR/RTS low; USB serial only
(LAN is deferred).
"""

from __future__ import annotations

import asyncio
import platform
import time
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Protocol

from rigplane import __version__ as _rigplane_version
from rigplane.commands import parse_civ_frame
from rigplane.commands.command_spec import CatCommandSpec
from rigplane.profiles.rig_loader import RigConfig

from rigplane.probe.classify import (
    cat_reply_closes,
    civ_reply_matches_request,
    classify_cat,
    classify_civ,
)
from rigplane.probe.model import (
    CommandRow,
    IdentityReport,
    ProbeFile,
    RadioInfo,
    SkippedRead,
    Snapshot,
    TerminationInfo,
    ToolInfo,
    UnsolicitedRow,
)
from rigplane.probe.readlist import (
    ReadRequest,
    build_read_plan,
    identity_requests,
    snapshot_names,
)

__all__ = ["Exchange", "ProbeRun", "run_probe"]

#: Gap between commands; per-command answer windows by protocol.
DEFAULT_GAP_S = 0.15
CAT_TIMEOUT_S = 1.0
CIV_TIMEOUT_S = 0.5

#: Consecutive silences that end the run with a plain message.
SILENT_LIMIT = 5
SILENT_ABORT_MESSAGE = (
    "The radio stopped answering. Check it is on, and the port and speed are right."
)

#: Refusal and bare-acknowledgement command bytes on CI-V.
_NAK = 0xFA
_ACK = 0xFB

#: How many stray pushes one answer wait absorbs before giving up.
_MAX_STRAYS = 8


class Exchange(Protocol):
    """One request on the wire; ``None`` means read without sending.

    Returns the first reply chunk, or ``None`` when the window runs
    out. Raises ``OSError`` (or ``asyncio.IncompleteReadError``) when
    the transport itself breaks; the run then ends as ``transport_error``.
    """

    async def __call__(self, request: bytes | None, timeout: float) -> bytes | None: ...


@dataclass(frozen=True, slots=True)
class ProbeRun:
    """The finished pass: the probe file plus why the run stopped early."""

    file: ProbeFile
    aborted: str | None


@dataclass(frozen=True, slots=True)
class _Stray:
    chunk: bytes
    arrived: float  # time.monotonic() when the chunk was observed


@dataclass(frozen=True, slots=True)
class _Answer:
    reply: bytes | None
    strays: tuple[_Stray, ...]
    latency_ms: float
    broken: BaseException | None
    garbled: bool


class _SendGate:
    """The one gate every transmitted read passes through.

    Applies ``gap_s`` pacing before each send, refuses bodies off the
    read plan's allow-list before anything goes out, and counts
    consecutive silences. While nothing has answered yet the count runs
    across every phase (identity, snapshots, main pass), so a fully
    silent radio still stops after the same five requests. Once
    anything answers, each phase counts on its own: five silent in a
    row inside a snapshot phase ends that phase only, five in a row
    inside the main pass (or a transport break anywhere) stops the run,
    and nothing more is sent afterwards.
    """

    def __init__(self, gap_s: float, *, allowed: frozenset[bytes]) -> None:
        self.gap_s = gap_s
        self.allowed = allowed
        self.silent_run = 0
        self.answered = False
        self.snapshot_ended = False
        self.stopped = False
        self.stop_status = "completed"
        self.stop_detail = ""

    def stop(self, status: str, detail: str) -> None:
        """Stop the run; the first stop wins and names the file's reason."""
        if not self.stopped:
            self.stopped = True
            self.stop_status = status
            self.stop_detail = detail

    def stop_transport(self, phase: str, error: BaseException) -> None:
        """Stop the run on a broken exchange, naming the phase and error."""
        self.stop("transport_error", f"Transport error in {phase}: {error}")

    def begin_phase(self) -> None:
        """A snapshot or main-pass boundary.

        Silence never carries across phases once something answered;
        while nothing answered yet the run-wide count carries on, so a
        fully silent radio still stops at the same five requests.
        """
        self.snapshot_ended = False
        if self.answered:
            self.silent_run = 0

    def note_silent(self) -> None:
        """Count one unanswered read; five in a row stops the run."""
        self.silent_run += 1
        if self.silent_run >= SILENT_LIMIT:
            self.stop("silence_stop", SILENT_ABORT_MESSAGE)

    def note_snapshot_silent(self) -> None:
        """Count one unanswered snapshot read.

        While nothing has answered yet this is the run-wide count;
        once something answered, five in a row ends the snapshot phase
        only and the run goes on.
        """
        self.silent_run += 1
        if self.silent_run < SILENT_LIMIT:
            return
        if self.answered:
            self.snapshot_ended = True
        else:
            self.stop("silence_stop", SILENT_ABORT_MESSAGE)

    def note_answered(self) -> None:
        """Any answer, even a refusal, breaks the silence run."""
        self.answered = True
        self.silent_run = 0


async def _gated_ask(
    gate: _SendGate,
    exchange: Exchange,
    body: bytes,
    window: float,
    *,
    attribute: Callable[[bytes], bool],
    garbled: Callable[[bytes], bool],
) -> _Answer | None:
    """Pace and send once; ``None`` when the run already stopped."""
    if body not in gate.allowed:
        raise ValueError(f"refusing to send {body!r}: not in the read list")
    if gate.stopped:
        return None
    if gate.gap_s:
        await asyncio.sleep(gate.gap_s)
    return await _ask(exchange, body, window, attribute=attribute, garbled=garbled)


def _elapsed_ms(started: float) -> float:
    return round((time.monotonic() - started) * 1000.0, 1)


async def _ask(
    exchange: Exchange,
    body: bytes,
    timeout: float,
    *,
    attribute: Callable[[bytes], bool],
    garbled: Callable[[bytes], bool],
) -> _Answer:
    """Send once; return the attributed answer plus stray pushes.

    A well-formed chunk that does not attribute is a push seen in the
    answer slot: it is kept as unsolicited and reading continues in the
    remaining window. A garbled chunk ends the wait: it was the reply,
    and the row is ``malformed``.
    """
    started = time.monotonic()
    strays: list[_Stray] = []
    try:
        chunk = await exchange(body, timeout)
        while chunk is not None and not attribute(chunk) and not garbled(chunk):
            strays.append(_Stray(chunk, time.monotonic()))
            remaining = timeout - (time.monotonic() - started)
            if remaining <= 0 or len(strays) >= _MAX_STRAYS:
                chunk = None
                break
            chunk = await exchange(None, remaining)
    except (OSError, asyncio.IncompleteReadError) as error:
        kept = list(strays)
        if isinstance(error, asyncio.IncompleteReadError) and error.partial:
            kept.append(_Stray(bytes(error.partial), time.monotonic()))
        return _Answer(None, tuple(kept), _elapsed_ms(started), error, False)
    if chunk is not None and garbled(chunk):
        return _Answer(chunk, tuple(strays), _elapsed_ms(started), None, True)
    if chunk is not None and attribute(chunk):
        return _Answer(chunk, tuple(strays), _elapsed_ms(started), None, False)
    return _Answer(None, tuple(strays), _elapsed_ms(started), None, False)


def _match_cat(body: bytes, chunk: bytes) -> bool:
    try:
        line = body.decode("ascii")
        text = chunk.decode("ascii")
    except UnicodeDecodeError:
        return False
    return text == "?;" or cat_reply_closes(line, text)


def _garbled_cat(chunk: bytes) -> bool:
    try:
        text = chunk.decode("ascii")
    except UnicodeDecodeError:
        return True
    return any(ord(char) < 0x20 or ord(char) > 0x7E for char in text)


def _match_civ(item: ReadRequest, chunk: bytes) -> bool:
    if item.kind == "unknown":
        try:
            parse_civ_frame(chunk)
        except ValueError:
            return False
        return True
    return civ_reply_matches_request(item.body, chunk)


def _garbled_civ(chunk: bytes) -> bool:
    try:
        parse_civ_frame(chunk)
    except ValueError:
        return True
    return False


def _attribute(protocol: str, item: ReadRequest, chunk: bytes) -> bool:
    if protocol == "yaesu_cat":
        return _match_cat(item.body, chunk)
    return _match_civ(item, chunk)


def _is_garbled(protocol: str, chunk: bytes) -> bool:
    if protocol == "yaesu_cat":
        return _garbled_cat(chunk)
    return _garbled_civ(chunk)


def _matcher(protocol: str, item: ReadRequest) -> Callable[[bytes], bool]:
    def attribute(chunk: bytes) -> bool:
        return _attribute(protocol, item, chunk)

    return attribute


def _garble_check(protocol: str) -> Callable[[bytes], bool]:
    def garbled(chunk: bytes) -> bool:
        return _is_garbled(protocol, chunk)

    return garbled


def _cat_template_of(config: RigConfig, name: str, body: bytes) -> str:
    spec = config.commands.get(name)
    if isinstance(spec, CatCommandSpec):
        # Declared local: the loader types stay precise even where
        # follow_imports=skip leaves cross-module attributes as Any.
        read: str | None = spec.read
        if read is not None:
            return read
    try:
        return body.decode("ascii")
    except UnicodeDecodeError:
        return body.hex()


@dataclass(frozen=True, slots=True)
class _Identity:
    raw: str
    parsed: str
    matched: bool


def _parse_identity_answer(
    config: RigConfig, name: str, reply: bytes | None
) -> tuple[str, str]:
    """The ``(raw, parsed)`` identity pair for one identity read."""
    if reply is None:
        return "", ""
    if config.protocol_type == "yaesu_cat":
        try:
            text = reply.decode("ascii")
        except UnicodeDecodeError:
            return reply.hex(), ""
        if not (text.startswith("ID") and text.endswith(";")):
            return text, ""
        return text, text[2:-1]
    raw = reply.hex()
    try:
        frame = parse_civ_frame(reply)
    except ValueError:
        return raw, ""
    if name == "get_xiegu_model_id":
        data = frame.data
        if data[:1] == bytes((0x19,)):
            data = data[1:]
        return raw, data.hex()
    return raw, frame.data.hex()


def _identity_matched(config: RigConfig, name: str, parsed: str) -> bool:
    if not parsed:
        return False
    if name == "get_xiegu_model_id":
        expected = config.xiegu_model_id
        return expected is not None and parsed == format(expected, "04x")
    return parsed in set(config.expected_identity_ids)


class _Strays:
    def __init__(self, run_started: float) -> None:
        self._run_started = run_started
        self.rows: list[UnsolicitedRow] = []

    def add(self, chunk: bytes, protocol: str, *, at: float | None = None) -> None:
        try:
            text = chunk.decode("ascii")
        except UnicodeDecodeError:
            text = ""
        if protocol == "civ":
            text = ""
        observed = time.monotonic() if at is None else at
        self.rows.append(
            UnsolicitedRow(
                t_ms=round((observed - self._run_started) * 1000.0, 1),
                hex=chunk.hex(),
                text=text,
            )
        )


async def _read_identity(
    config: RigConfig,
    exchange: Exchange,
    window: float,
    gate: _SendGate,
    strays: _Strays,
) -> _Identity:
    """Send the identity reads through the gate.

    A mismatch never stops the run; silence counts toward the stop,
    and a transport break stops everything.
    """
    protocol = config.protocol_type
    garbled = _garble_check(protocol)
    raws: list[tuple[str, str, str]] = []
    for item in identity_requests(config):
        answer = await _gated_ask(
            gate,
            exchange,
            item.body,
            window,
            attribute=_matcher(protocol, item),
            garbled=garbled,
        )
        if answer is None:
            break
        if answer.broken is not None:
            gate.stop_transport("identity", answer.broken)
            break
        for stray in answer.strays:
            strays.add(stray.chunk, protocol, at=stray.arrived)
        if answer.reply is None:
            gate.note_silent()
        else:
            gate.note_answered()
        raw, parsed = _parse_identity_answer(config, item.name, answer.reply)
        raws.append((item.name, raw, parsed))
    if not raws:
        return _Identity("", "", False)
    hit = next(
        (
            index
            for index, (name, _, parsed) in enumerate(raws)
            if _identity_matched(config, name, parsed)
        ),
        None,
    )
    if hit is None:
        _name, raw, parsed = raws[0]
        return _Identity(raw, parsed, False)
    _name, raw, parsed = raws[hit]
    return _Identity(raw, parsed, True)


async def _read_snapshot(
    config: RigConfig,
    full: dict[str, ReadRequest],
    exchange: Exchange,
    window: float,
    gate: _SendGate,
    strays: _Strays,
) -> dict[str, str | None]:
    """Read the profile state reads once through the gate.

    Silence records ``None``; a broken exchange stops the run, and
    once stopped nothing more is sent. Five silent in a row ends the
    snapshot phase only once something answered (the rest stay
    unsent); while nothing answered yet it stops the whole run.
    """
    protocol = config.protocol_type
    garbled = _garble_check(protocol)
    out: dict[str, str | None] = {}
    for name in snapshot_names(config, set(full)):
        if gate.stopped or gate.snapshot_ended:
            out[name] = None
            continue
        item = full[name]
        answer = await _gated_ask(
            gate,
            exchange,
            item.body,
            window,
            attribute=_matcher(protocol, item),
            garbled=garbled,
        )
        if answer is None:
            out[name] = None
            continue
        if answer.broken is not None:
            gate.stop_transport("snapshot", answer.broken)
            out[name] = None
            continue
        for stray in answer.strays:
            strays.add(stray.chunk, protocol, at=stray.arrived)
        if answer.reply is None:
            gate.note_snapshot_silent()
            out[name] = None
        else:
            gate.note_answered()
            out[name] = _snapshot_value(protocol, answer.reply)
    return out


def _snapshot_value(protocol: str, reply: bytes | None) -> str | None:
    if reply is None:
        return None
    if protocol == "yaesu_cat":
        try:
            return reply.decode("ascii")
        except UnicodeDecodeError:
            return reply.hex()
    return reply.hex()


def _outcome(protocol: str, item: ReadRequest, answer: _Answer) -> str:
    if answer.broken is not None:
        return "transport_error"
    if answer.garbled:
        return "malformed"
    if answer.reply is None:
        return "unsolicited" if answer.strays else "silent"
    if protocol == "yaesu_cat":
        try:
            line = item.body.decode("ascii")
            text = answer.reply.decode("ascii")
        except UnicodeDecodeError:
            return "malformed"
        return classify_cat(line, text)
    kind = "unknown" if item.kind == "unknown" else "get"
    return classify_civ(item.body, answer.reply, kind=kind)


async def run_probe(
    config: RigConfig,
    config_toml_sha256: str,
    *,
    exchange: Exchange,
    port_kind: str,
    baud: int,
    label: str,
    include_private: bool,
    gap_s: float = DEFAULT_GAP_S,
    timeout_s: float | None = None,
    items: list[ReadRequest] | None = None,
    siblings: Iterable[RigConfig] | None = None,
    progress: Callable[[int, int], None] | None = None,
) -> ProbeRun:
    """Run one read pass over *items* (default: the whole read plan).

    Identity reads go first, then the before snapshot, the pass, and the
    after snapshot. While nothing answered yet, five silent reads in a
    row stop the run; once something answered, a silent snapshot phase
    ends that phase only while five silent in a row inside the main
    pass still stop the run. A transport break stops everything.
    Identity mismatch never stops. Declared-absent CI-V reads borrow
    bytes from *siblings*, the other shipped profiles; without them
    those reads are recorded as skipped.
    """
    protocol = config.protocol_type
    if protocol not in ("civ", "yaesu_cat"):
        raise ValueError(f"probe supports 'civ' and 'yaesu_cat', not {protocol!r}")
    window = timeout_s if timeout_s is not None else _default_timeout(protocol)
    plan = build_read_plan(config, include_private=include_private, siblings=siblings)
    full = list(plan.asked)
    allowed = {item.body for item in full} | {
        item.body for item in identity_requests(config)
    }
    watch = list(full) if items is None else list(items)
    strays = _Strays(time.monotonic())

    gate = _SendGate(gap_s, allowed=frozenset(allowed))
    identity = await _read_identity(config, exchange, window, gate, strays)
    gate.begin_phase()
    before = await _read_snapshot(
        config, {item.name: item for item in full}, exchange, window, gate, strays
    )

    rows: list[CommandRow] = []
    if not gate.stopped:
        gate.begin_phase()
        for index, item in enumerate(watch):
            row = await _ask_once(config, item, exchange, window, gate, strays)
            if row is None:
                break
            rows.append(row)
            if progress is not None:
                progress(index + 1, len(watch))
            if gate.stopped:
                break
    if gate.stopped:
        after = dict(before)
    else:
        gate.begin_phase()
        after = await _read_snapshot(
            config,
            {item.name: item for item in full},
            exchange,
            window,
            gate,
            strays,
        )
    aborted = gate.stop_detail or None
    if gate.stopped:
        unattempted = [
            SkippedRead(item.name, item.body.hex(), "run_aborted", gate.stop_detail)
            for item in watch[len(rows) :]
        ]
    else:
        unattempted = []

    changed = sorted(name for name in before if before[name] != after.get(name))
    file = ProbeFile(
        tool=ToolInfo(
            rigplane_version=_rigplane_version,
            python=platform.python_version(),
            os=platform.system(),
            created_utc=datetime.now(timezone.utc).isoformat(),
        ),
        mode="read",
        radio=RadioInfo(
            profile=config.model,
            profile_sha256=config_toml_sha256,
            protocol=protocol,
            port_kind=port_kind,
            baud=baud,
            identity_reported=IdentityReport(raw=identity.raw, parsed=identity.parsed),
            identity_matches_profile=identity.matched,
        ),
        private_reads_included=include_private,
        label=label,
        commands=tuple(rows),
        skipped=plan.skipped + tuple(unattempted),
        unsolicited=tuple(strays.rows),
        snapshot=Snapshot(before=before, after=after, changed=changed),
        termination=TerminationInfo(status=gate.stop_status, detail=gate.stop_detail),
        checks=(),
    )
    return ProbeRun(file=file, aborted=aborted)


def _default_timeout(protocol: str) -> float:
    return CIV_TIMEOUT_S if protocol == "civ" else CAT_TIMEOUT_S


async def _ask_once(
    config: RigConfig,
    item: ReadRequest,
    exchange: Exchange,
    window: float,
    gate: _SendGate,
    strays: _Strays,
) -> CommandRow | None:
    """Send one read through the gate; ``None`` when already stopped."""
    protocol = config.protocol_type
    garbled = _garble_check(protocol)
    answer = await _gated_ask(
        gate,
        exchange,
        item.body,
        window,
        attribute=_matcher(protocol, item),
        garbled=garbled,
    )
    if answer is None:
        return None
    for stray in answer.strays:
        strays.add(stray.chunk, protocol, at=stray.arrived)
    outcome = _outcome(protocol, item, answer)
    if answer.broken is not None:
        gate.stop_transport(f"main pass ({item.name})", answer.broken)
    elif outcome == "silent":
        gate.note_silent()
    else:
        gate.note_answered()
    if protocol == "yaesu_cat":
        template = _cat_template_of(config, item.name, item.body)
        try:
            request_text = item.body.decode("ascii")
        except UnicodeDecodeError:
            request_text = item.body.hex()
        reply_text: str | None = None
        if answer.reply is not None and outcome != "unsolicited":
            try:
                reply_text = answer.reply.decode("ascii")
            except UnicodeDecodeError:
                reply_text = None
    else:
        template = item.body.hex()
        request_text = ""
        reply_text = None
    reply_hex = answer.reply.hex() if answer.reply is not None else None
    if outcome == "unsolicited":
        reply_hex, reply_text = None, None
    row = CommandRow(
        name=item.name,
        template=template,
        request_hex=item.body.hex(),
        request_text=request_text,
        reply_hex=reply_hex,
        reply_text=reply_text,
        outcome=outcome,
        latency_ms=answer.latency_ms,
        expected=item.expected,
        note=item.note,
    )
    return row
