"""The write-check engine (MOR-4464 B1): one shared mechanism,
per protocol, never per radio.

After the unchanged read pass, each consented check changes one
receive-side setting at a time, reads it back, puts it back and
verifies. Preconditions gate the first write; originals form a stack
popped in reverse; every restore write is rehearsed as a no-op first
(unless the item opts out); witness snapshots bracket the check;
Ctrl-C, SIGHUP/SIGTERM and console close all run the restore first;
the ``ask`` window times out as skipped. All sends go through the
existing :class:`_SendGate` (pacing, silence stop, allow-list): the
declared step bodies plus the one computed restore body per item,
admitted only after validation, at load and again at send time.
"""

from __future__ import annotations

import asyncio
import os
import signal
import threading
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from rigplane.backends.yaesu_cat.parser import CatCommandParser, format_command
from rigplane.commands import parse_civ_frame
from rigplane.commands.command_spec import CatCommandSpec, CivCommandSpec
from rigplane.probe import render
from rigplane.probe.classify import (
    cat_reply_closes,
    civ_reply_matches_request,
    classify_cat,
    classify_civ,
)
from rigplane.probe.journal import ChecksJournal, delete_journal, write_journal
from rigplane.probe.model import (
    CheckAnswerRow,
    CheckRecord,
    CheckStepRow,
    CheckWitness,
)
from rigplane.profiles.probe_checks import ProbeCheck
from rigplane.profiles.rig_loader import RigConfig
from rigplane.probe.runner import Exchange, _gated_ask, _SendGate
from rigplane.probe.write_policy import check_raw_read_allowed, check_send_allowed

__all__ = [
    "ASK_TIMEOUT_S",
    "CheckCancelled",
    "ChecksResult",
    "run_checks",
]

#: How long an ``ask`` waits for the person before skipping.
ASK_TIMEOUT_S = 60.0


class CheckCancelled(BaseException):
    """A check cancelled by the person or by signal (SIGHUP/SIGTERM)."""


@dataclass(frozen=True, slots=True)
class ChecksResult:
    """Every check that ran, plus the process exit code.

    Exit code 4 means a restore did not verify or could not be
    attempted; 0 means every original is back and verified (or nothing
    was ever written).
    """

    records: tuple[CheckRecord, ...]
    exit_code: int


@dataclass(slots=True)
class _CheckContext:
    config: RigConfig
    exchange: Exchange
    gate: _SendGate
    window: float
    journal_path: Path
    emit: Callable[[str], None]
    on_event: Callable[[str], None] | None
    cancel: threading.Event
    both_vfos: bool = False


@dataclass(slots=True)
class _Planned:
    item_read: str
    item_write: str
    reply: bytes
    body: bytes
    display: str
    rehearse: bool


def _display(protocol: str, reply: bytes) -> str:
    if protocol == "yaesu_cat":
        try:
            return reply.decode("ascii")
        except UnicodeDecodeError:
            return reply.hex()
    return reply.hex()


def _read_body(config: RigConfig, name: str) -> bytes:
    """The wire bytes of one profile read command."""
    spec = config.commands.get(name)
    if isinstance(spec, CatCommandSpec) and spec.read is not None:
        return spec.read.encode("ascii")
    if isinstance(spec, CivCommandSpec):
        return bytes(spec.bytes)
    raise ValueError(f"check read {name!r} declares no read")


def _write_template(config: RigConfig, name: str) -> str:
    spec = config.commands.get(name)
    if isinstance(spec, CatCommandSpec) and spec.write is not None:
        return spec.write
    raise ValueError(f"check write {name!r} declares no CAT write")


def _parse_template(config: RigConfig, name: str) -> str:
    spec = config.commands.get(name)
    if isinstance(spec, CatCommandSpec):
        if spec.parse is not None:
            return spec.parse
        if spec.read is not None:
            return spec.read
    raise ValueError(f"check read {name!r} declares no CAT parse")


def _civ_declared(config: RigConfig, name: str) -> bytes:
    spec = config.commands.get(name)
    if isinstance(spec, CivCommandSpec):
        return bytes(spec.bytes)
    raise ValueError(f"check command {name!r} declares no CI-V bytes")


def _restore_body(
    config: RigConfig,
    protocol: str,
    read: str,
    write: str,
    reply: bytes,
    amap: tuple[tuple[str, str], ...],
) -> bytes:
    """The write that puts one original back, built from its reply."""
    table = dict(amap)
    if protocol == "yaesu_cat":
        text = reply.decode("ascii")
        fields = CatCommandParser(_parse_template(config, read)).parse(text)
        restored: dict[str, object] = {}
        for key, value in fields.items():
            replacement = table.get(str(value))
            if replacement is None:
                restored[key] = value
            elif isinstance(value, int):
                try:
                    restored[key] = int(replacement)
                except ValueError:
                    restored[key] = replacement
            else:
                restored[key] = replacement
        return format_command(_write_template(config, write), **restored).encode(
            "ascii"
        )
    frame = parse_civ_frame(reply)
    declared_read = _civ_declared(config, read)
    data = bytes(frame.data)
    if data.startswith(declared_read):
        data = data[len(declared_read):]
    data_hex = table.get(data.hex(), data.hex())
    return _civ_declared(config, write) + bytes.fromhex(data_hex)


def _check_target(protocol: str, body: bytes) -> str | int:
    if protocol == "yaesu_cat":
        return body.decode("ascii")[:2].upper()
    return body[0]


def _readback_matches(
    config: RigConfig,
    protocol: str,
    write_body: bytes,
    write_command: str,
    read_reply: bytes,
    read_command: str,
) -> bool:
    """Whether a read after a same-target write shows the written value.

    Parsed through the profile's own templates (CAT) or the frame data
    (CI-V); falls back to the payload after the verb when a template
    cannot parse.
    """
    try:
        if protocol == "yaesu_cat":
            request = write_body.decode("ascii")
            reply = read_reply.decode("ascii")
            try:
                requested = CatCommandParser(
                    _write_template(config, write_command)
                ).parse(request)
                answered = CatCommandParser(
                    _parse_template(config, read_command)
                ).parse(reply)
                shared = set(requested) & set(answered)
                if shared:
                    return all(
                        str(requested[key]) == str(answered[key]) for key in shared
                    )
            except ValueError:
                pass
            return request[2:] == reply[2:]
        frame = parse_civ_frame(read_reply)
        declared_read = _civ_declared(config, read_command)
        data = bytes(frame.data)
        if data.startswith(declared_read):
            data = data[len(declared_read):]
        declared_write = _civ_declared(config, write_command)
        return data == bytes(write_body[len(declared_write):])
    except ValueError:
        return False


def _poll_cancel(ctx: _CheckContext) -> None:
    if ctx.cancel.is_set():
        raise CheckCancelled("cancelled")


async def _send_read(
    ctx: _CheckContext, body: bytes, *, phase: str
) -> tuple[bytes | None, str]:
    """Send one read through the gate; returns (reply, outcome)."""
    _poll_cancel(ctx)
    protocol = ctx.config.protocol_type
    if protocol == "yaesu_cat":
        line = body.decode("ascii")

        def attribute(chunk: bytes) -> bool:
            try:
                text = chunk.decode("ascii")
            except UnicodeDecodeError:
                return False
            return text == "?;" or cat_reply_closes(line, text)

        def garbled(chunk: bytes) -> bool:
            try:
                chunk.decode("ascii")
            except UnicodeDecodeError:
                return True
            return False

        answer = await _gated_ask(
            ctx.gate, ctx.exchange, body, ctx.window,
            attribute=attribute, garbled=garbled,
        )
    else:

        def attribute_civ(chunk: bytes) -> bool:
            return civ_reply_matches_request(body, chunk)

        def garbled_civ(chunk: bytes) -> bool:
            try:
                parse_civ_frame(chunk)
            except ValueError:
                return True
            return False

        answer = await _gated_ask(
            ctx.gate, ctx.exchange, body, ctx.window,
            attribute=attribute_civ, garbled=garbled_civ,
        )
    if answer is None:
        return None, "stopped"
    if answer.broken is not None:
        ctx.gate.stop_transport(phase, answer.broken)
        raise OSError(str(answer.broken))
    if answer.reply is None:
        ctx.gate.note_silent()
        if ctx.gate.stopped:
            return None, "stopped"
        return None, "silent"
    ctx.gate.note_answered()
    _poll_cancel(ctx)
    if protocol == "yaesu_cat":
        try:
            text = answer.reply.decode("ascii")
        except UnicodeDecodeError:
            return answer.reply, "malformed"
        return answer.reply, classify_cat(line, text)
    return answer.reply, classify_civ(body, answer.reply)


async def _send_write(
    ctx: _CheckContext, body: bytes, *, phase: str
) -> tuple[bytes | None, str]:
    """Send one write through the gate; returns (reply, outcome).

    A silent CAT write applied; a CI-V ACK applied; ``?;``/NAK
    refused. Every body is policy-checked at send time.
    """
    _poll_cancel(ctx)
    check_send_allowed(
        ctx.config.protocol_type, body, vfo_pair_restored=ctx.both_vfos
    )
    ctx.gate.allow(body)
    protocol = ctx.config.protocol_type
    if protocol == "yaesu_cat":

        def attribute(chunk: bytes) -> bool:
            return chunk == b"?;"

        def garbled(chunk: bytes) -> bool:
            try:
                chunk.decode("ascii")
            except UnicodeDecodeError:
                return True
            return False

        answer = await _gated_ask(
            ctx.gate, ctx.exchange, body, ctx.window,
            attribute=attribute, garbled=garbled,
        )
    else:

        def attribute_civ(chunk: bytes) -> bool:
            return civ_reply_matches_request(body, chunk)

        def garbled_civ(chunk: bytes) -> bool:
            try:
                parse_civ_frame(chunk)
            except ValueError:
                return True
            return False

        answer = await _gated_ask(
            ctx.gate, ctx.exchange, body, ctx.window,
            attribute=attribute_civ, garbled=garbled_civ,
        )
    if answer is None:
        return None, "stopped"
    if answer.broken is not None:
        ctx.gate.stop_transport(phase, answer.broken)
        raise OSError(str(answer.broken))
    if answer.reply is None:
        ctx.gate.note_silent()
        if ctx.gate.stopped:
            return None, "stopped"
        if protocol == "yaesu_cat":
            return None, "sent"
        return None, "silent"
    ctx.gate.note_answered()
    _poll_cancel(ctx)
    if protocol == "yaesu_cat":
        if answer.reply == b"?;":
            return answer.reply, "refused"
        return answer.reply, "sent"
    try:
        frame = parse_civ_frame(answer.reply)
    except ValueError:
        return answer.reply, "malformed"
    if frame.command == 0xFA:
        return answer.reply, "refused"
    return answer.reply, "sent"


def _step_row(
    protocol: str,
    kind: str,
    name: str,
    body: bytes,
    reply: bytes | None,
    outcome: str,
) -> CheckStepRow:
    if protocol == "yaesu_cat":
        try:
            request_text = body.decode("ascii")
        except UnicodeDecodeError:
            request_text = body.hex()
        reply_text = _display(protocol, reply) if reply is not None else None
    else:
        request_text = ""
        reply_text = None
    return CheckStepRow(
        kind=kind,
        name=name,
        request_hex=body.hex(),
        request_text=request_text,
        reply_hex=reply.hex() if reply is not None else None,
        reply_text=reply_text,
        outcome=outcome,
    )


def _step_body(
    config: RigConfig,
    protocol: str,
    *,
    kind: str,
    command: str,
    values: tuple[tuple[str, object], ...],
    data_hex: str,
    line: str,
    body_hex: str,
) -> bytes:
    """The wire body of one declared or raw step."""
    if protocol == "yaesu_cat":
        if kind in ("raw_read", "raw_write"):
            return line.encode("ascii")
        if kind == "write":
            return format_command(
                _write_template(config, command), **dict(values)
            ).encode("ascii")
        return _read_body(config, command)
    if kind in ("raw_read", "raw_write"):
        return bytes.fromhex(body_hex)
    if kind == "write":
        return _civ_declared(config, command) + bytes.fromhex(data_hex)
    return _read_body(config, command)


async def _read_snapshot(
    ctx: _CheckContext, names: tuple[str, ...], *, phase: str
) -> dict[str, str | None]:
    """Read profile snapshot reads once; silence records ``None``."""
    out: dict[str, str | None] = {}
    for name in names:
        body = _read_body(ctx.config, name)
        if body not in ctx.gate.allowed:
            ctx.gate.allow(body)
        reply, _outcome = await _send_read(ctx, body, phase=phase)
        out[name] = (
            _display(ctx.config.protocol_type, reply)
            if reply is not None
            else None
        )
    return out


async def _run_one(
    ctx: _CheckContext,
    check: ProbeCheck,
    number: int,
    total: int,
    consent: Callable[[str], str],
    ask: Callable[[str], Awaitable[str | None]],
    ask_timeout_s: float,
) -> tuple[CheckRecord | None, bool]:
    """Run one check; ``(None, True)`` means skipped by the person."""
    protocol = ctx.config.protocol_type
    steps: list[CheckStepRow] = []
    answers: list[CheckAnswerRow] = []
    originals: dict[str, str] = {}
    planned: list[_Planned] = []
    witness_before: dict[str, str | None] = {}
    witness_after: dict[str, str | None] = {}
    error: str | None = None
    verified = True
    restored = True
    ran = False
    task_cancelled = False

    def record(preconditions_ok: bool, witness: CheckWitness) -> CheckRecord:
        return CheckRecord(
            id=check.id,
            title=check.title,
            preconditions_ok=preconditions_ok,
            steps=tuple(steps),
            answers=tuple(answers),
            originals=dict(originals),
            witness=witness,
            restored=restored,
            verified=verified,
            error=error,
        )

    try:
        for precondition in check.preconditions:
            body = _read_body(ctx.config, precondition.read)
            if body not in ctx.gate.allowed:
                ctx.gate.allow(body)
            reply, _outcome = await _send_read(ctx, body, phase="precondition")
            if reply is None or _display(protocol, reply) != precondition.equals:
                error = render.precondition_abort_message()
                ctx.emit(error)
                return record(False, CheckWitness(before={}, after={}, changed={})), False
        witness_before = await _read_snapshot(ctx, check.witness, phase="witness")
        for item in check.restore:
            body = _read_body(ctx.config, item.read)
            if body not in ctx.gate.allowed:
                ctx.gate.allow(body)
            reply, outcome = await _send_read(ctx, body, phase="originals")
            if reply is None or outcome in ("refused", "malformed"):
                error = render.originals_unreadable_message(item.read)
                ctx.emit(error)
                return record(True, CheckWitness(before={}, after={}, changed={})), False
            display = _display(protocol, reply)
            originals[item.read] = display
            planned.append(
                _Planned(
                    item_read=item.read,
                    item_write=item.write,
                    reply=reply,
                    body=_restore_body(
                        ctx.config, protocol, item.read, item.write, reply, item.map
                    ),
                    display=display,
                    rehearse=item.rehearse,
                )
            )
        right_now = [
            f"{render.human_read_name(name)} {display}"
            for name, display in originals.items()
        ]
        seen_verbs: list[str] = []
        for step in check.steps:
            if step.kind in ("raw_read", "raw_write"):
                verb = (
                    step.line[:2].upper()
                    if protocol == "yaesu_cat"
                    else f"0x{bytes.fromhex(step.body_hex)[0]:02X}"
                )
                if verb not in seen_verbs:
                    seen_verbs.append(verb)
        prompt = render.check_consent_prompt(
            number, total, check.title, check.explain_what, check.explain_after,
            right_now, check.warnings, tuple(seen_verbs),
        )
        if consent(prompt).strip().lower() not in ("y", "yes"):
            return None, True
        ran = True
        ctx.emit(render.check_running_line(number, total, check.title))
        write_journal(
            ctx.journal_path,
            ChecksJournal(
                check_id=check.id,
                check_title=check.title,
                originals=dict(originals),
                saved_utc=datetime.now(timezone.utc).isoformat(),
            ),
        )
        rehearsed = True
        for entry in planned:
            if not entry.rehearse:
                continue
            reply, outcome = await _send_write(ctx, entry.body, phase="rehearsal")
            reread: bytes | None = None
            if outcome == "sent":
                planned_body = _read_body(ctx.config, entry.item_read)
                if planned_body not in ctx.gate.allowed:
                    ctx.gate.allow(planned_body)
                reread, _ = await _send_read(ctx, planned_body, phase="rehearsal")
            if outcome != "sent" or reread is None or bytes(reread) != bytes(
                entry.reply
            ):
                error = render.rehearsal_failed_message(entry.item_read)
                ctx.emit(error)
                delete_journal(ctx.journal_path)
                rehearsed = False
                break
        last_write: dict[str | int, tuple[bytes, str]] = {}
        if rehearsed:
            for step in check.steps:
                if step.kind == "ask":
                    prompt_text = render.ask_question_prompt(step.question)
                    ctx.emit(prompt_text)
                    try:
                        answer = await asyncio.wait_for(
                            ask(prompt_text), ask_timeout_s
                        )
                    except (asyncio.TimeoutError, TimeoutError):
                        answer = None
                    answers.append(
                        CheckAnswerRow(
                            question=step.question,
                            answer=answer,
                            skipped=answer is None,
                        )
                    )
                    continue
                body = _step_body(
                    ctx.config, protocol, kind=step.kind, command=step.command,
                    values=step.values, data_hex=step.data_hex,
                    line=step.line, body_hex=step.body_hex,
                )
                if step.kind in ("write", "raw_write"):
                    if ctx.on_event is not None:
                        ctx.on_event("step_write_begin")
                    reply, outcome = await _send_write(ctx, body, phase="steps")
                    steps.append(
                        _step_row(
                            protocol, step.kind, step.command or step.kind,
                            body, reply, outcome,
                        )
                    )
                    if outcome != "sent":
                        error = render.write_failed_message(step.command or step.kind, outcome)
                        verified = False
                        break
                    last_write[_check_target(protocol, body)] = (body, step.command)
                    continue
                if step.kind == "raw_read":
                    check_raw_read_allowed(protocol, body)
                if body not in ctx.gate.allowed:
                    ctx.gate.allow(body)
                reply, outcome = await _send_read(ctx, body, phase="steps")
                steps.append(
                    _step_row(
                        protocol, step.kind, step.command or step.kind,
                        body, reply, outcome,
                    )
                )
                if reply is None or outcome in ("refused", "malformed"):
                    error = render.read_failed_message(step.command or step.kind, outcome)
                    verified = False
                    break
                target = _check_target(protocol, body)
                if target in last_write:
                    write_body, write_command = last_write[target]
                    if not _readback_matches(
                        ctx.config, protocol, write_body, write_command,
                        reply, step.command,
                    ):
                        verified = False
                        error = render.readback_mismatch_message(step.command)
            witness_after = await _read_snapshot(ctx, check.witness, phase="witness")
            restore_reads = {item.read for item in check.restore}
            changed = sorted(
                name
                for name in check.witness
                if witness_before.get(name) != witness_after.get(name)
            )
            outside = [name for name in changed if name not in restore_reads]
            if outside:
                verified = False
                error = render.witness_changed_message(outside[0])
    except (KeyboardInterrupt, CheckCancelled) as cancelled:
        error = render.cancelled_message(str(cancelled))
    except asyncio.CancelledError:
        error = render.cancelled_message("cancelled")
        task_cancelled = True
    except OSError as transport:
        error = render.transport_error_message(str(transport))
        verified = False
        restored = False
    except Exception as failed:  # noqa: BLE001 -- a broken check records, never crashes
        error = render.check_failed_message(str(failed))
        ctx.emit(error)
    if ran:
        if ctx.on_event is not None:
            try:
                ctx.on_event("restore_begin")
            except (KeyboardInterrupt, CheckCancelled):
                pass
        for entry in reversed(planned):
            try:
                reply, outcome = await _send_write(ctx, entry.body, phase="restore")
                if outcome != "sent":
                    reply, outcome = await _send_write(ctx, entry.body, phase="restore")
                if outcome != "sent":
                    restored = False
                    verified = False
                    warning = render.check_restore_warning(
                        render.human_read_name(entry.item_read), entry.display
                    )
                    ctx.emit(warning)
                    error = warning
                    continue
                reread, _ = await _send_read(
                    ctx, _read_body(ctx.config, entry.item_read), phase="restore"
                )
                if reread is None or bytes(reread) != bytes(entry.reply):
                    restored = False
                    verified = False
                    warning = render.check_restore_warning(
                        render.human_read_name(entry.item_read), entry.display
                    )
                    ctx.emit(warning)
                    error = warning
            except (KeyboardInterrupt, CheckCancelled):
                continue
            except asyncio.CancelledError:
                task_cancelled = True
                continue
            except OSError:
                restored = False
                verified = False
                if error is None:
                    error = render.transport_error_message("during restore")
        if restored:
            delete_journal(ctx.journal_path)
        if restored and verified and error is None:
            ctx.emit(render.check_result_line(number, True))
        elif restored:
            ctx.emit(render.check_result_line(number, False))
    if task_cancelled:
        raise asyncio.CancelledError()
    witness = CheckWitness(
        before=dict(witness_before), after=dict(witness_after), changed=sorted(
            name
            for name in check.witness
            if witness_before.get(name) != witness_after.get(name)
        ),
    )
    return record(True, witness), False


def _install_cancel_handlers(cancel: threading.Event) -> Callable[[], None]:
    """SIGHUP/SIGTERM (POSIX) convert to the same cancel path as Ctrl-C."""
    old_term = None
    old_hup = None
    use_signals = (
        os.name == "posix" and threading.current_thread() is threading.main_thread()
    )

    def _raise(signum: int, frame: object) -> None:
        cancel.set()
        raise CheckCancelled(f"signal {signum}")

    if use_signals:
        try:
            old_term = signal.signal(signal.SIGTERM, _raise)
            if hasattr(signal, "SIGHUP"):
                old_hup = signal.signal(signal.SIGHUP, _raise)
        except (ValueError, OSError, RuntimeError):
            old_term, old_hup = None, None

    def remove() -> None:
        if old_term is not None:
            try:
                signal.signal(signal.SIGTERM, old_term)
            except (ValueError, OSError, RuntimeError):
                pass
        if old_hup is not None and hasattr(signal, "SIGHUP"):
            try:
                signal.signal(signal.SIGHUP, old_hup)
            except (ValueError, OSError, RuntimeError):
                pass

    return remove


async def run_checks(
    *,
    config: RigConfig,
    exchange: Exchange,
    gap_s: float,
    timeout_s: float,
    journal_path: Path,
    consent: Callable[[str], str],
    ask: Callable[[str], Awaitable[str | None]],
    emit: Callable[[str], None],
    on_event: Callable[[str], None] | None = None,
    ask_timeout_s: float = ASK_TIMEOUT_S,
) -> ChecksResult:
    """Run the consented write checks after the read pass.

    Returns one typed record per check that ran plus the exit code (4
    when a restore did not verify or could not be attempted).
    """
    protocol = config.protocol_type
    if protocol not in ("yaesu_cat", "civ"):
        raise ValueError("probe checks need USB CI-V or Yaesu CAT")
    cancel = threading.Event()
    remove_handlers = _install_cancel_handlers(cancel)
    try:
        gate = _SendGate(gap_s, allowed=frozenset())
        ctx = _CheckContext(
            config=config,
            exchange=exchange,
            gate=gate,
            window=timeout_s,
            journal_path=journal_path,
            emit=emit,
            on_event=on_event,
            cancel=cancel,
        )
        records: list[CheckRecord] = []
        for number, check in enumerate(config.probe_checks, 1):
            ctx.both_vfos = len({item.read for item in check.restore}) >= 2
            try:
                record, was_skipped = await _run_one(
                    ctx, check, number, len(config.probe_checks),
                    consent, ask, ask_timeout_s,
                )
            except asyncio.CancelledError:
                raise
            except (KeyboardInterrupt, CheckCancelled) as cancelled:
                records.append(
                    CheckRecord(
                        id=check.id,
                        title=check.title,
                        preconditions_ok=True,
                        steps=(),
                        answers=(),
                        originals={},
                        witness=CheckWitness(before={}, after={}, changed=[]),
                        restored=True,
                        verified=True,
                        error=render.cancelled_message(str(cancelled)),
                    )
                )
                break
            except OSError as transport:
                records.append(
                    CheckRecord(
                        id=check.id,
                        title=check.title,
                        preconditions_ok=True,
                        steps=(),
                        answers=(),
                        originals={},
                        witness=CheckWitness(before={}, after={}, changed=[]),
                        restored=False,
                        verified=False,
                        error=render.transport_error_message(str(transport)),
                    )
                )
                break
            if was_skipped:
                continue
            assert record is not None
            records.append(record)
            if gate.stopped:
                break
        exit_code = 4 if any(not record.restored for record in records) else 0
        return ChecksResult(records=tuple(records), exit_code=exit_code)
    finally:
        remove_handlers()
