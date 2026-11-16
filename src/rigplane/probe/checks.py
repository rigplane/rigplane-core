"""The write-check engine (MOR-4464 B1): one shared mechanism per protocol.

After the read pass each consented check changes one receive-side setting
at a time, reads it back, puts it back and verifies. Preconditions gate the
first write; originals form a stack popped in reverse; every restore write is
rehearsed as a no-op first; witness snapshots bracket the check; Ctrl-C and
SIGHUP/SIGTERM run the restore first; the ``ask`` window times out as
skipped. Every send goes through the read pass's :class:`_SendGate`, and every
write body is policy-checked at send time.
"""

from __future__ import annotations

import asyncio
import os
import signal
import threading
from collections.abc import Awaitable, Callable
from contextlib import suppress
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from rigplane.backends.yaesu_cat.parser import CatCommandParser, format_command
from rigplane.commands import parse_civ_frame
from rigplane.probe import render
from rigplane.probe.journal import ChecksJournal, delete_journal, write_journal
from rigplane.probe.model import CheckAnswerRow, CheckRecord, CheckStepRow, CheckWitness
from rigplane.probe.readlist import ReadRequest
from rigplane.probe.runner import (
    _NAK,
    Exchange,
    _garble_check,
    _gated_ask,
    _matcher,
    _outcome,
    _SendGate,
    _snapshot_value,
)
from rigplane.profiles.probe_checks import (
    ProbeCheck,
    ProbeCheckStep,
    ProbeRestoreItem,
    both_vfos,
    check_send_allowed,
    command_key,
    declared_body,
)
from rigplane.profiles.rig_loader import RigConfig

__all__ = ["ASK_TIMEOUT_S", "CheckCancelled", "ChecksResult", "run_checks"]

#: How long an ``ask`` waits for the person before skipping.
ASK_TIMEOUT_S = 60.0

_RAW = ("raw_read", "raw_write")


class CheckCancelled(BaseException):
    """A check cancelled by the person or by signal (SIGHUP/SIGTERM)."""


@dataclass(frozen=True, slots=True)
class ChecksResult:
    """Every check that ran, plus the exit code (4: a restore did not
    verify or could not be attempted)."""

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
    consent: Callable[[str], str]
    ask: Callable[[str], Awaitable[str | None]]
    ask_timeout_s: float
    both_vfos: bool = False


@dataclass(slots=True)
class _State:
    """What one check has done so far; the restore reads it, the record
    reports it. ``planned`` holds (item, original reply, restore body)."""

    steps: list[CheckStepRow] = field(default_factory=list)
    answers: list[CheckAnswerRow] = field(default_factory=list)
    originals: dict[str, str] = field(default_factory=dict)
    planned: list[tuple[ProbeRestoreItem, bytes, bytes]] = field(default_factory=list)
    before: dict[str, str | None] = field(default_factory=dict)
    after: dict[str, str | None] = field(default_factory=dict)
    error: str | None = None
    verified: bool = True
    restored: bool = True
    ran: bool = False
    task_cancelled: bool = False


def _display(protocol: str, reply: bytes) -> str:
    return _snapshot_value(protocol, reply) or ""


def _body_of(config: RigConfig, name: str, part: str = "read") -> bytes:
    """The declared wire bytes of one profile command."""
    body = declared_body(config.commands.get(name), config.protocol_type, part)
    if body is None:
        raise ValueError(f"profile command {name!r} declares no {part}")
    return body


def _cat_fields(config: RigConfig, name: str, part: str, text: bytes) -> dict[str, Any]:
    """A CAT line parsed through the profile command's own template."""
    template = _body_of(config, name, part).decode("ascii")
    return dict(CatCommandParser(template).parse(text.decode("ascii")))


def _step_body(config: RigConfig, step: ProbeCheckStep) -> bytes:
    cat = config.protocol_type == "yaesu_cat"
    if step.kind in _RAW:
        return step.line.encode("ascii") if cat else bytes.fromhex(step.body_hex)
    if step.kind == "read":
        return _body_of(config, step.command)
    if cat:
        template = _body_of(config, step.command, "write").decode("ascii")
        return str(format_command(template, **dict(step.values))).encode("ascii")
    return _body_of(config, step.command) + bytes.fromhex(step.data_hex)


def _civ_data(config: RigConfig, read: str, reply: bytes) -> bytes:
    """The data bytes of a CI-V reply, without the echoed command prefix."""
    declared = _body_of(config, read)
    data = bytes(parse_civ_frame(reply).data)
    return data[len(declared) :] if data.startswith(declared) else data


def _mapped(value: object, table: dict[str, str]) -> object:
    new = table.get(str(value))
    if new is None:
        return value
    if isinstance(value, int):
        with suppress(ValueError):
            return int(new)
    return new


def _restore_body(config: RigConfig, item: ProbeRestoreItem, reply: bytes) -> bytes:
    """The write that puts one original back, built from its reply."""
    table = dict(item.map)
    if config.protocol_type == "yaesu_cat":
        fields = _cat_fields(config, item.read, "parse", reply)
        template = _body_of(config, item.write, "write").decode("ascii")
        restored = {key: _mapped(value, table) for key, value in fields.items()}
        return str(format_command(template, **restored)).encode("ascii")
    data = _civ_data(config, item.read, reply).hex()
    return _body_of(config, item.write) + bytes.fromhex(table.get(data, data))


def _readback_matches(
    config: RigConfig,
    write: ProbeCheckStep,
    sent: bytes,
    read: ProbeCheckStep,
    reply: bytes,
) -> bool:
    """Whether a read after a same-target write shows the written value:
    parsed through the profile's templates, else the payload after the verb."""
    try:
        if config.protocol_type != "yaesu_cat":
            written = sent[len(_body_of(config, write.command)) :]
            return _civ_data(config, read.command, reply) == written
        try:
            requested = _cat_fields(config, write.command, "write", sent)
            answered = _cat_fields(config, read.command, "parse", reply)
            shared = set(requested) & set(answered)
            if shared:
                return all(str(requested[k]) == str(answered[k]) for k in shared)
        except ValueError:
            pass
        return sent[2:].decode("ascii") == reply[2:].decode("ascii")
    except ValueError:
        return False


def _poll_cancel(ctx: _CheckContext) -> None:
    if ctx.cancel.is_set():
        raise CheckCancelled("cancelled")


async def _send(
    ctx: _CheckContext, body: bytes, *, phase: str, write: bool = False
) -> tuple[bytes | None, str]:
    """Send one body through the gate; returns (reply, outcome).

    A silent CAT write applied (``sent``); a CI-V ACK applied; ``?;``/NAK
    refused. Writes are policy-checked here, at send time.
    """
    _poll_cancel(ctx)
    protocol = ctx.config.protocol_type
    cat = protocol == "yaesu_cat"
    if write:
        check_send_allowed(protocol, body, both_vfos=ctx.both_vfos)
    ctx.gate.allow(body)
    item = ReadRequest("check", body, "cat" if cat else "get")
    answer = await _gated_ask(
        ctx.gate,
        ctx.exchange,
        body,
        ctx.window,
        attribute=(lambda chunk: chunk == b"?;")
        if write and cat
        else _matcher(protocol, item),
        garbled=_garble_check(protocol),
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
        return None, "sent" if write and cat else "silent"
    ctx.gate.note_answered()
    _poll_cancel(ctx)
    if not write:
        return answer.reply, _outcome(protocol, item, answer)
    if answer.garbled:
        return answer.reply, "malformed"
    refused = (
        answer.reply == b"?;" if cat else parse_civ_frame(answer.reply).command == _NAK
    )
    return answer.reply, "refused" if refused else "sent"


async def _read(ctx: _CheckContext, name: str, phase: str) -> tuple[bytes | None, str]:
    return await _send(ctx, _body_of(ctx.config, name), phase=phase)


async def _snapshot(
    ctx: _CheckContext, names: tuple[str, ...]
) -> dict[str, str | None]:
    """Read profile reads once; silence records ``None``."""
    protocol = ctx.config.protocol_type
    return {
        name: _snapshot_value(protocol, (await _read(ctx, name, "witness"))[0])
        for name in names
    }


def _changed(check: ProbeCheck, state: _State) -> list[str]:
    return sorted(n for n in check.witness if state.before.get(n) != state.after.get(n))


def _step_row(
    protocol: str, step: ProbeCheckStep, body: bytes, reply: bytes | None, outcome: str
) -> CheckStepRow:
    cat = protocol == "yaesu_cat"
    return CheckStepRow(
        kind=step.kind,
        name=step.command or step.kind,
        request_hex=body.hex(),
        request_text=body.decode("ascii") if cat else "",
        reply_hex=None if reply is None else reply.hex(),
        reply_text=_snapshot_value(protocol, reply) if cat else None,
        outcome=outcome,
    )


async def _prepare(
    ctx: _CheckContext, check: ProbeCheck, state: _State
) -> CheckRecord | None:
    """Preconditions, witness-before and originals; a record when the check
    cannot start."""
    protocol = ctx.config.protocol_type

    def stop(message: str, *, gate_held: bool) -> CheckRecord:
        ctx.emit(message)
        return CheckRecord(
            check.id,
            check.title,
            preconditions_ok=gate_held,
            originals=dict(state.originals),
            error=message,
        )

    for precondition in check.preconditions:
        reply, _ = await _read(ctx, precondition.read, "precondition")
        if reply is None or _display(protocol, reply) != precondition.equals:
            return stop(render.precondition_abort_message(), gate_held=False)
    state.before = await _snapshot(ctx, check.witness)
    for item in check.restore:
        reply, outcome = await _read(ctx, item.read, "originals")
        if reply is None or outcome in ("refused", "malformed"):
            return stop(render.originals_unreadable_message(item.read), gate_held=True)
        state.originals[item.read] = _display(protocol, reply)
        state.planned.append((item, reply, _restore_body(ctx.config, item, reply)))
    return None


async def _put_back(
    ctx: _CheckContext,
    planned: tuple[ProbeRestoreItem, bytes, bytes],
    phase: str,
    *,
    retry: bool = False,
) -> bool:
    """Write one original and re-read it; ``True`` when it reads equal
    (``retry``: a failed write is sent once more)."""
    item, reply, body = planned
    _, outcome = await _send(ctx, body, phase=phase, write=True)
    if outcome != "sent" and retry:
        _, outcome = await _send(ctx, body, phase=phase, write=True)
    if outcome != "sent":
        return False
    return (await _read(ctx, item.read, phase))[0] == reply


async def _run_steps(ctx: _CheckContext, check: ProbeCheck, state: _State) -> None:
    protocol = ctx.config.protocol_type
    last_write: dict[str | int, tuple[ProbeCheckStep, bytes]] = {}
    for step in check.steps:
        if step.kind == "ask":
            prompt = render.ask_question_prompt(step.question)
            ctx.emit(prompt)
            try:
                answer = await asyncio.wait_for(ctx.ask(prompt), ctx.ask_timeout_s)
            except TimeoutError:
                answer = None
            state.answers.append(CheckAnswerRow(step.question, answer, answer is None))
            continue
        body = _step_body(ctx.config, step)
        write = step.kind in ("write", "raw_write")
        if write and ctx.on_event is not None:
            ctx.on_event("step_write_begin")
        if step.kind == "raw_read":
            check_send_allowed(protocol, body, write=False)
        reply, outcome = await _send(ctx, body, phase="steps", write=write)
        state.steps.append(_step_row(protocol, step, body, reply, outcome))
        name = step.command or step.kind
        target = command_key(protocol, body)
        if write:
            if outcome != "sent":
                state.error = render.write_failed_message(name, outcome)
                state.verified = False
                return
            last_write[target] = (step, body)
        elif reply is None or outcome in ("refused", "malformed"):
            state.error = render.read_failed_message(name, outcome)
            state.verified = False
            return
        elif (prior := last_write.get(target)) and not _readback_matches(
            ctx.config, prior[0], prior[1], step, reply
        ):
            state.verified = False
            state.error = render.readback_mismatch_message(step.command)


async def _restore(ctx: _CheckContext, number: int, state: _State) -> None:
    """Put every original back in reverse order, each retried once and
    re-read; Ctrl-C and cancels never skip an item."""
    if ctx.on_event is not None:
        with suppress(KeyboardInterrupt, CheckCancelled):
            ctx.on_event("restore_begin")
    for planned in reversed(state.planned):
        try:
            if not await _put_back(ctx, planned, "restore", retry=True):
                name = render.human_read_name(planned[0].read)
                state.restored = state.verified = False
                state.error = render.check_restore_warning(
                    name, state.originals[planned[0].read]
                )
                ctx.emit(state.error)
        except (KeyboardInterrupt, CheckCancelled):
            continue
        except asyncio.CancelledError:
            state.task_cancelled = True
        except OSError:
            state.restored = state.verified = False
            state.error = state.error or render.transport_error_message(
                "during restore"
            )
    if state.restored:
        delete_journal(ctx.journal_path)
        ctx.emit(
            render.check_result_line(number, state.verified and state.error is None)
        )


async def _run_one(
    ctx: _CheckContext, check: ProbeCheck, number: int
) -> CheckRecord | None:
    """Run one check; ``None`` means the person skipped it."""
    protocol = ctx.config.protocol_type
    total = len(ctx.config.probe_checks)
    state = _State()
    try:
        if (early := await _prepare(ctx, check, state)) is not None:
            return early
        verbs = dict.fromkeys(
            command_key(protocol, _step_body(ctx.config, step))
            for step in check.steps
            if step.kind in _RAW
        )
        prompt = render.check_consent_prompt(
            number,
            total,
            check.title,
            check.explain_what,
            check.explain_after,
            [f"{render.human_read_name(n)} {d}" for n, d in state.originals.items()],
            check.warnings,
            tuple(v if isinstance(v, str) else f"0x{v:02X}" for v in verbs),
        )
        if ctx.consent(prompt).strip().lower() not in ("y", "yes"):
            return None
        state.ran = True
        ctx.emit(render.check_running_line(number, total, check.title))
        saved = datetime.now(timezone.utc).isoformat()
        write_journal(
            ctx.journal_path,
            ChecksJournal(check.id, check.title, dict(state.originals), saved),
        )
        for planned in (p for p in state.planned if p[0].rehearse):
            if not await _put_back(ctx, planned, "rehearsal"):
                state.error = render.rehearsal_failed_message(planned[0].read)
                ctx.emit(state.error)
                delete_journal(ctx.journal_path)
                break
        else:
            await _run_steps(ctx, check, state)
            state.after = await _snapshot(ctx, check.witness)
            restored_reads = {item.read for item in check.restore}
            outside = [n for n in _changed(check, state) if n not in restored_reads]
            if outside:
                state.verified = False
                state.error = render.witness_changed_message(outside[0])
    except (KeyboardInterrupt, CheckCancelled) as cancelled:
        state.error = render.cancelled_message(str(cancelled))
    except asyncio.CancelledError:
        state.error = render.cancelled_message("cancelled")
        state.task_cancelled = True
    except OSError as transport:
        state.error = render.transport_error_message(str(transport))
        state.verified = state.restored = False
    except Exception as failed:  # noqa: BLE001 -- a broken check records, never crashes
        state.error = render.check_failed_message(str(failed))
        ctx.emit(state.error)
    if state.ran:
        await _restore(ctx, number, state)
    if state.task_cancelled:
        raise asyncio.CancelledError()
    witness = CheckWitness(
        dict(state.before), dict(state.after), _changed(check, state)
    )
    return CheckRecord(
        check.id,
        check.title,
        steps=tuple(state.steps),
        answers=tuple(state.answers),
        originals=dict(state.originals),
        witness=witness,
        restored=state.restored,
        verified=state.verified,
        error=state.error,
    )


def _install_cancel_handlers(cancel: threading.Event) -> Callable[[], None]:
    """SIGHUP/SIGTERM (POSIX, main thread) take the same path as Ctrl-C."""
    if os.name != "posix" or threading.current_thread() is not threading.main_thread():
        return lambda: None

    def _raise(signum: int, frame: object) -> None:
        cancel.set()
        raise CheckCancelled(f"signal {signum}")

    old: dict[int, Any] = {}
    for name in ("SIGTERM", "SIGHUP"):
        with suppress(ValueError, OSError, RuntimeError, AttributeError):
            number = getattr(signal, name)
            old[number] = signal.signal(number, _raise)

    def remove() -> None:
        for number, handler in old.items():
            if handler is not None:
                with suppress(ValueError, OSError, RuntimeError):
                    signal.signal(number, handler)

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
    """Run the consented write checks after the read pass; one typed record
    per check that ran, exit code 4 when a restore did not verify."""
    if config.protocol_type not in ("yaesu_cat", "civ"):
        raise ValueError("probe checks need USB CI-V or Yaesu CAT")
    cancel = threading.Event()
    remove_handlers = _install_cancel_handlers(cancel)
    try:
        gate = _SendGate(gap_s, allowed=frozenset())
        ctx = _CheckContext(
            config,
            exchange,
            gate,
            timeout_s,
            journal_path,
            emit,
            on_event,
            cancel,
            consent,
            ask,
            ask_timeout_s,
        )
        records: list[CheckRecord] = []
        for number, check in enumerate(config.probe_checks, 1):
            ctx.both_vfos = both_vfos(check.restore)
            try:
                record = await _run_one(ctx, check, number)
            except (KeyboardInterrupt, CheckCancelled, OSError) as stopped:
                lost = isinstance(stopped, OSError)
                message = (
                    render.transport_error_message if lost else render.cancelled_message
                )
                records.append(
                    CheckRecord(
                        check.id,
                        check.title,
                        restored=not lost,
                        verified=not lost,
                        error=message(str(stopped)),
                    )
                )
                break
            if record is None:
                continue
            records.append(record)
            if gate.stopped:
                break
        exit_code = 4 if any(not record.restored for record in records) else 0
        return ChecksResult(records=tuple(records), exit_code=exit_code)
    finally:
        remove_handlers()
