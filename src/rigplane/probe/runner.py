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
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Protocol

from rigplane import __version__ as _rigplane_version
from rigplane.commands import parse_civ_frame
from rigplane.commands.command_spec import CatCommandSpec
from rigplane.profiles.rig_loader import RigConfig

from rigplane.probe.classify import (
    cat_reply_closes,
    civ_requested_command,
    classify_cat,
    classify_civ,
)
from rigplane.probe.model import (
    CommandRow,
    IdentityReport,
    ProbeFile,
    RadioInfo,
    Snapshot,
    ToolInfo,
    UnsolicitedRow,
)
from rigplane.probe.readlist import (
    ReadRequest,
    build_read_list,
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
class _Answer:
    reply: bytes | None
    strays: tuple[bytes, ...]
    latency_ms: float
    broken: BaseException | None
    garbled: bool


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
    strays: list[bytes] = []
    try:
        chunk = await exchange(body, timeout)
        while chunk is not None and not attribute(chunk) and not garbled(chunk):
            strays.append(chunk)
            remaining = timeout - (time.monotonic() - started)
            if remaining <= 0 or len(strays) >= _MAX_STRAYS:
                chunk = None
                break
            chunk = await exchange(None, remaining)
    except (OSError, asyncio.IncompleteReadError) as error:
        return _Answer(None, tuple(strays), _elapsed_ms(started), error, False)
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


def _match_civ(body: bytes, chunk: bytes) -> bool:
    try:
        frame = parse_civ_frame(chunk)
    except ValueError:
        return False
    if frame.command in (_NAK, _ACK):
        return True
    return (frame.command, frame.sub) == civ_requested_command(body)


def _garbled_civ(chunk: bytes) -> bool:
    try:
        parse_civ_frame(chunk)
    except ValueError:
        return True
    return False


def _attribute(protocol: str, body: bytes, chunk: bytes) -> bool:
    if protocol == "yaesu_cat":
        return _match_cat(body, chunk)
    return _match_civ(body, chunk)


def _is_garbled(protocol: str, chunk: bytes) -> bool:
    if protocol == "yaesu_cat":
        return _garbled_cat(chunk)
    return _garbled_civ(chunk)


def _matcher(protocol: str, body: bytes) -> Callable[[bytes], bool]:
    def attribute(chunk: bytes) -> bool:
        return _attribute(protocol, body, chunk)

    return attribute


def _garble_check(protocol: str) -> Callable[[bytes], bool]:
    def garbled(chunk: bytes) -> bool:
        return _is_garbled(protocol, chunk)

    return garbled


def _cat_template_of(config: RigConfig, name: str, body: bytes) -> str:
    spec = config.commands.get(name)
    if isinstance(spec, CatCommandSpec):
        read = spec.read
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

    def add(self, chunk: bytes, protocol: str) -> None:
        try:
            text = chunk.decode("ascii")
        except UnicodeDecodeError:
            text = ""
        if protocol == "civ":
            text = ""
        self.rows.append(
            UnsolicitedRow(
                t_ms=round((time.monotonic() - self._run_started) * 1000.0, 1),
                hex=chunk.hex(),
                text=text,
            )
        )


async def _read_identity(
    config: RigConfig,
    exchange: Exchange,
    window: float,
    strays: _Strays,
) -> tuple[_Identity, BaseException | None]:
    """Send the identity reads; a mismatch never stops the run."""
    protocol = config.protocol_type
    garbled = _garble_check(protocol)
    raws: list[tuple[str, str, str]] = []
    for item in identity_requests(config):
        answer = await _ask(
            exchange,
            item.body,
            window,
            attribute=_matcher(protocol, item.body),
            garbled=garbled,
        )
        if answer.broken is not None:
            return _Identity("", "", False), answer.broken
        for stray in answer.strays:
            strays.add(stray, protocol)
        raw, parsed = _parse_identity_answer(config, item.name, answer.reply)
        raws.append((item.name, raw, parsed))
    if not raws:
        return _Identity("", "", False), None
    matched = any(_identity_matched(config, name, parsed) for name, _, parsed in raws)
    _name, raw, parsed = raws[0]
    return _Identity(raw, parsed, matched), None


async def _read_snapshot(
    config: RigConfig,
    full: dict[str, ReadRequest],
    exchange: Exchange,
    window: float,
    strays: _Strays,
) -> dict[str, str | None]:
    """Read the profile state reads once; silence records ``None``."""
    protocol = config.protocol_type
    garbled = _garble_check(protocol)
    out: dict[str, str | None] = {}
    for name in snapshot_names(config):
        item = full[name]
        answer = await _ask(
            exchange,
            item.body,
            window,
            attribute=_matcher(protocol, item.body),
            garbled=garbled,
        )
        if answer.broken is not None:
            out[name] = None
            break
        for stray in answer.strays:
            strays.add(stray, protocol)
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
    progress: Callable[[int, int], None] | None = None,
) -> ProbeRun:
    """Run one read pass over *items* (default: the whole read list).

    Identity reads go first, then the before snapshot, the pass, and the
    after snapshot. Five silent commands in a row stop the pass; a
    transport break stops everything. Identity mismatch never stops.
    """
    protocol = config.protocol_type
    if protocol not in ("civ", "yaesu_cat"):
        raise ValueError(f"probe supports 'civ' and 'yaesu_cat', not {protocol!r}")
    window = timeout_s if timeout_s is not None else _default_timeout(protocol)
    full = build_read_list(config, include_private=include_private)
    allowed = {item.body for item in full} | {
        item.body for item in identity_requests(config)
    }
    watch = list(full) if items is None else list(items)
    for item in watch:
        if item.body not in allowed:
            raise ValueError(f"refusing to send {item.body!r}: not in the read list")
    strays = _Strays(time.monotonic())

    identity, broken = await _read_identity(config, exchange, window, strays)
    before = await _read_snapshot(
        config, {item.name: item for item in full}, exchange, window, strays
    )

    rows: list[CommandRow] = []
    aborted: str | None = None
    if broken is not None:
        aborted = f"Transport error: {broken}"
        after = dict(before)
    else:
        silent_run = 0
        for index, item in enumerate(watch):
            row, silent = await _ask_once(config, item, exchange, window, strays)
            rows.append(row)
            silent_run = silent_run + 1 if silent else 0
            if progress is not None:
                progress(index + 1, len(watch))
            if gap_s:
                await asyncio.sleep(gap_s)
            if row.outcome == "transport_error":
                aborted = f"Transport error: {item.name}"
                break
            if silent_run >= SILENT_LIMIT:
                aborted = SILENT_ABORT_MESSAGE
                break
        if aborted is not None and rows and rows[-1].outcome == "transport_error":
            after = dict(before)
        else:
            after = await _read_snapshot(
                config, {item.name: item for item in full}, exchange, window, strays
            )

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
        unsolicited=tuple(strays.rows),
        snapshot=Snapshot(before=before, after=after, changed=changed),
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
    strays: _Strays,
) -> tuple[CommandRow, bool]:
    """Send one read; return its file row and whether it was silent."""
    protocol = config.protocol_type
    garbled = _garble_check(protocol)
    answer = await _ask(
        exchange,
        item.body,
        window,
        attribute=_matcher(protocol, item.body),
        garbled=garbled,
    )
    for stray in answer.strays:
        strays.add(stray, protocol)
    outcome = _outcome(protocol, item, answer)
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
        note=item.note,
    )
    return row, outcome == "silent"
