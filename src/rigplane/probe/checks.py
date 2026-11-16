"""The write-check engine (MOR-4464 B1b), one mechanism for CI-V and Yaesu CAT.

Every frame is built and judged by ``judged_frame`` inside :func:`_send`, the
only path to the port, which records each frame sent and each push seen.
Before any write: preconditions, witness, originals (each encoded as its
restore write: outside the domain, refused), consent, the fsynced journal.
Then rehearsal, steps, and the protected restore of every original (reverse
order, retried once, read back) even after a silence stop, failed step or
cancel. A check left changed ends the batch with its journal (now vs was).

Known gap: no Windows console-close handler (MOR-4497, own PR before B2).
"""

from __future__ import annotations

import asyncio
import fnmatch
import signal
import time
from collections.abc import Awaitable, Callable
from contextlib import suppress
from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from rigplane.commands import parse_civ_frame
from rigplane.probe.journal import (
    ChecksJournal,
    journal_path,
    leftover_journal_line,
    remove_journal,
    write_journal,
)
from rigplane.probe.model import (
    CheckAnswerRow,
    CheckRecord,
    CheckStepRow,
    CheckWitness,
    TerminationInfo,
    UnsolicitedRow,
)
from rigplane.probe.readlist import ReadRequest
from rigplane.probe.runner import (
    Exchange,
    _garble_check,
    _gated_ask,
    _matcher,
    _outcome,
    _SendGate,
    _snapshot_value,
    _Strays,
)
from rigplane.profiles import selector_one_is_sub
from rigplane.profiles.probe_checks import (
    Entry,
    ProbeCheck,
    RadioFacts,
    Table,
    compile_table,
    judged_frame,
)
from rigplane.profiles.rig_loader import RigConfig

__all__ = ["ASK_TIMEOUT_S", "ChecksRequest", "ChecksResult", "run_checks"]

ASK_TIMEOUT_S = 60.0  # an unanswered ask is skipped after this
_LABELS = {
    "freq_a": "VFO-A",
    "freq_b": "VFO-B",
    "mode_main": "mode",
    "power": "RF power",
    "speech_processor": "speech processor",
    "freq_selected": "frequency of the selected VFO",
    "freq_unselected": "frequency of the other VFO",
    "mode_selected": "mode of the selected VFO",
    "mode_unselected": "mode of the other VFO",
    "rf_power": "RF power",
    "af_level": "AF level",
}

Key = tuple[str, int | None]


@dataclass(frozen=True, slots=True)
class ChecksRequest:
    """``consent(check, originals)``, ``ask`` (``None``: skipped), ``on_event``
    (journal_written, steps_begin, restore_begin)."""

    consent: Callable[[ProbeCheck, dict[str, str]], bool]
    ask: Callable[[str], Awaitable[str | None]]
    on_event: Callable[[str], None] | None = None
    ask_timeout_s: float = ASK_TIMEOUT_S
    journal_path: Path = field(default_factory=journal_path)  # tests override


@dataclass(frozen=True, slots=True)
class ChecksResult:
    """``exit_code`` 4: a check not put back or not verified."""

    records: tuple[CheckRecord, ...]
    exit_code: int
    termination: TerminationInfo | None
    unsolicited: tuple[UnsolicitedRow, ...] = ()


class _Stop(Exception): ...


@dataclass(slots=True)
class _Run:
    config: RigConfig
    exchange: Exchange
    gate: _SendGate
    window: float
    request: ChecksRequest
    strays: _Strays
    task: asyncio.Future[None] | None = None
    restoring: bool = False
    cancelled: bool = False
    pending: Callable[[bytes], bool] | None = None  # a cancelled ask's answer


@dataclass(slots=True)
class _Check:
    check: ProbeCheck
    table: Table
    steps: list[CheckStepRow] = field(default_factory=list)
    answers: list[CheckAnswerRow] = field(default_factory=list)
    originals: list[tuple[Key, object]] = field(default_factory=list)
    now: dict[Key, object] = field(default_factory=dict)  # last value seen
    before: dict[str, str | None] = field(default_factory=dict)


def radio_facts(config: RigConfig) -> RadioFacts:
    """The facts the loader compiled the checks against."""
    return RadioFacts(
        protocol=config.protocol_type,
        commands=config.commands,
        modes=tuple(config.modes),
        mode_codes=None if config.mode_codes is None else tuple(config.mode_codes),
        cmd29_routes=frozenset(config.cmd29_routes),
        swap_ab=config.vfo_swap_ab,
        swap_main_sub=config.vfo_swap_main_sub,
        unselected_is_sub=selector_one_is_sub(
            config.receiver_count, config.split_tx_target
        ),
        data_modes=config.data_mode_count,
        freq_ranges=tuple((r.start, r.end) for r in config.to_profile().freq_ranges),
    )


def _hand(config: RigConfig, key: Key, value: object) -> tuple[str, str]:
    """(name, value) in front-panel units: MHz, mode name, watts, percent of
    the 0-255 CI-V level (with the profile's watts for RF power)."""
    entry, receiver = key
    name = _LABELS.get(entry, entry) + (" (SUB)" if receiver == 1 else "")
    if entry.startswith("freq") and isinstance(value, int):
        return name, f"{value / 1e6:.6f}".rstrip("0").rstrip(".") + " MHz"
    if entry == "power" and isinstance(value, int):
        return name, f"{value} W"
    if entry in ("rf_power", "af_level") and isinstance(value, int):
        watts = config.max_watts if entry == "rf_power" else None
        about = f" (about {value * watts / 255:.1f} W)" if watts else ""
        return name, f"{value * 100 / 255:.0f}%{about}"
    if isinstance(value, tuple) and len(value) == 3:  # CI-V mode, data, filter
        return name, f"{value[0]} (data {value[1]}, filter {value[2]})"
    return name, str(value)


def _value(run: _Run, entry: Entry, reply: bytes) -> object | None:
    """A data reply through the entry's codec (FT; via ``[vfo.split]``)."""
    if entry.write is None or entry.codec is None:
        return None
    if run.config.protocol_type == "yaesu_cat":
        payload = reply[:-1] if reply.endswith(b";") else b""
    else:
        frame = parse_civ_frame(reply)  # a data reply always parses
        sub = b"" if frame.sub is None else bytes((frame.sub,))
        payload = bytes((frame.command,)) + sub + bytes(frame.data)
    if not payload.startswith(entry.write):
        return None
    rest = payload[len(entry.write) :]
    if entry.id == "split":
        on = run.config.split.read_map.get(rest.decode("ascii", "replace"))
        return None if on is None else ("on" if on else "off")
    return entry.codec.decode(rest) if len(rest) == entry.codec.width else None


async def _send(
    run: _Run, state: _Check, key: Key, *write: object
) -> tuple[str, object | None, bytes | None]:
    """The ONLY path to the port: judge the exact bytes, send them once, and
    record the frame and any pushes. Returns (outcome, value, reply); a write
    is accepted (value = the written value) only without a refusal, a garbled
    answer or a broken link."""
    form, sent = ("write", write[0]) if write else ("read", None)
    body = judged_frame(state.table, key[0], form, sent, key[1], state.check.restored)
    protocol = run.config.protocol_type
    cat_write = protocol == "yaesu_cat" and bool(write)
    item = ReadRequest(key[0], body, "get" if protocol == "civ" else "cat")
    if (owed := run.pending) is not None:  # settle a cancelled exchange
        run.pending, deadline = None, time.monotonic() + run.window
        try:  # pushes are kept, never extend the wait past one window
            while (left := deadline - time.monotonic()) > 0:
                chunk = await run.exchange(None, left)
                if chunk is None or owed(chunk):
                    break
                run.strays.add(chunk, protocol)
        except (OSError, asyncio.IncompleteReadError) as broken:
            run.gate.stop_transport("write check", broken)
    run.gate.allowed = run.gate.allowed | {body}
    run.pending = (lambda c: c == b"?;") if cat_write else _matcher(protocol, item)
    answer = await _gated_ask(
        run.gate,
        run.exchange,
        body,
        run.window,
        attribute=run.pending,
        garbled=_garble_check(protocol),
    )
    run.pending = None
    if answer is None:  # the gate stopped: nothing was sent
        if run.restoring:
            return "transport_error", None, None
        raise _Stop(run.gate.stop_detail)
    for stray in answer.strays:
        run.strays.add(stray.chunk, protocol, at=stray.arrived)
    outcome = _outcome(protocol, item, answer)
    if answer.broken is not None:
        run.gate.stop_transport("write check", answer.broken)
    elif answer.reply is not None:
        run.gate.note_answered()
    elif not (cat_write or run.restoring):  # restore silence never stops it
        run.gate.note_silent()
    ok = (
        ("silent", "unsolicited")
        if cat_write
        else ("ack_only",)
        if write
        else ("data",)
    )
    reply = answer.reply
    value = None
    if outcome in ok:
        value = sent if write else _value(run, state.table.entry(key[0]), reply or b"")
    if value is not None:
        state.now[key] = value
    shown = None if value is None else _hand(run.config, key, value)[1]
    hexed = None if reply is None else reply.hex()
    state.steps.append(CheckStepRow(form, key[0], body.hex(), hexed, outcome, shown))
    return outcome, value, reply


async def _witness(run: _Run, state: _Check) -> dict[str, str | None]:
    """Witness texts; ``None`` unless the radio answered with data."""
    out: dict[str, str | None] = {}
    for entry in state.check.witness:
        outcome, _, reply = await _send(run, state, (entry, None))
        good = outcome == "data"
        out[entry] = _snapshot_value(run.config.protocol_type, reply) if good else None
    return out


async def _prepare(run: _Run, state: _Check) -> None:
    """Everything before the first write."""
    for pre in state.check.preconditions:
        outcome, _, reply = await _send(run, state, (pre.entry, None))
        text = _snapshot_value(run.config.protocol_type, reply) or ""
        if outcome != "data" or not fnmatch.fnmatchcase(text, pre.equals):
            raise _Stop(f"precondition {pre.entry} not met (radio answered {text!r})")
    state.before = await _witness(run, state)
    for item in state.check.restore:
        key = (item.entry, item.receiver)
        _, value, _ = await _send(run, state, key)
        try:
            if value is None:
                raise ValueError("unreadable")
            judged_frame(
                state.table, key[0], "write", value, key[1], state.check.restored
            )
        except ValueError:
            name = _hand(run.config, key, 0)[0]
            raise _Stop(
                f"the radio's current {name} is not something this check can put back"
            ) from None
        state.originals.append((key, value))


async def _steps(run: _Run, state: _Check) -> None:
    originals = dict(state.originals)
    for item in (o for o in state.check.restore if o.rehearse):
        key = (item.entry, item.receiver)
        name = _hand(run.config, key, 0)[0]
        if (await _send(run, state, key, originals[key]))[1] is None:
            raise _Stop(f"the radio refused to keep its own {name}")
        if (await _send(run, state, key))[1] != originals[key]:
            raise _Stop(f"{name} changed on a write of its own value")
    if run.request.on_event:
        run.request.on_event("steps_begin")
    for step in state.check.steps:
        key = (step.entry, step.receiver)
        if step.kind == "ask":
            try:
                answer = await asyncio.wait_for(
                    run.request.ask(step.question), run.request.ask_timeout_s
                )
            except TimeoutError:
                answer = None
            state.answers.append(CheckAnswerRow(step.question, answer, answer is None))
        elif step.kind == "read":
            await _send(run, state, key)
        elif (await _send(run, state, key, step.value))[1] is None:
            raise _Stop(f"the radio did not accept {step.entry} = {step.value!r}")


Witness = dict[str, str | None]


async def _restore(run: _Run, state: _Check) -> tuple[bool, bool, str | None, Witness]:
    """Every original back in reverse order (a refusal or a lost link never
    raises, so no item skips the next), then the witness."""
    run.restoring = True
    run.gate.resume()
    if run.request.on_event:
        run.request.on_event("restore_begin")
    restored = verified = True
    error = None
    for key, value in reversed(state.originals):
        name, text = _hand(run.config, key, value)
        accepted = (await _send(run, state, key, value))[1] is not None or (
            await _send(run, state, key, value)
        )[1] is not None
        same = accepted and (await _send(run, state, key))[1] == value
        if not accepted:
            restored = verified = False
            error = f"could not put {name} back to {text}; please set it by hand"
        elif not same:
            verified = False
            error = error or f"{name} reads differently after the restore (was {text})"
    return restored, verified, error, await _witness(run, state)


async def _run_one(run: _Run, check: ProbeCheck) -> CheckRecord:
    ids = {s.entry for s in check.steps if s.entry} | {r.entry for r in check.restore}
    ids |= set(check.witness) | {p.entry for p in check.preconditions}
    state = _Check(check, compile_table(radio_facts(run.config), ids))
    originals: dict[str, str] = {}

    def record(status: str, error: str | None = None, **ran: Any) -> CheckRecord:
        done = (tuple(state.steps), tuple(state.answers), originals)
        return CheckRecord(check.id, check.title, status, *done, error=error, **ran)

    try:  # a journal from an earlier check holds originals nobody restored
        left = run.request.journal_path.exists() and leftover_journal_line(
            run.request.journal_path
        )
    except ValueError:
        left = (
            f"the journal {run.request.journal_path} cannot be read; check it by hand"
        )
    if left:
        return record("refused", error=left)
    try:
        await _prepare(run, state)
    except _Stop as stop:
        return record("refused", error=str(stop))
    originals.update(_hand(run.config, k, v) for k, v in state.originals)
    if run.cancelled:
        return record("refused", error="cancelled before any write")
    if not run.request.consent(check, dict(originals)):
        return record("declined")
    journal, now = run.request.journal_path, datetime.now(timezone.utc).isoformat()
    noted = ChecksJournal(
        check.id, check.title, run.config.model, (*originals.items(),), now
    )
    try:  # published only if no journal exists: another run's stays intact
        write_journal(journal, noted, new=True)
    except FileExistsError:
        return record("refused", error=leftover_journal_line(journal))
    if run.request.on_event:
        run.request.on_event("journal_written")
    error: str | None = None
    external = False
    run.task = asyncio.ensure_future(_steps(run, state))
    try:
        await run.task
    except asyncio.CancelledError:
        external = not run.cancelled
        error = "cancelled; the settings were put back first"
    except Exception as stop:  # noqa: BLE001 -- a failed step still restores
        error = str(stop)
    finally:
        run.task = None
    cleanup = asyncio.ensure_future(_restore(run, state))
    while not cleanup.done():  # protected: a cancel waits for every original
        try:
            await asyncio.shield(cleanup)
        except asyncio.CancelledError:
            external = True
    restored, verified, restore_error, after = cleanup.result()
    run.restoring = False
    changed = sorted(
        n
        for n in check.witness
        if after.get(n) is None or state.before.get(n) != after[n]
    )
    verified = verified and not changed
    if restored and verified:
        remove_journal(journal)
    else:  # now vs was, for the person to set by hand
        seen = tuple(_hand(run.config, k, v) for k, v in state.now.items())
        write_journal(journal, replace(noted, now=seen))
    if external:
        raise asyncio.CancelledError
    return record(
        "ran",
        witness=CheckWitness(dict(state.before), after, changed),
        restored=restored,
        verified=verified,
        error=restore_error
        or (f"witness {changed[0]} unverified" if changed else error),
    )


def _install_signals(run: _Run) -> Callable[[], None]:
    """SIGINT/SIGTERM/SIGHUP cancel the running steps into the restore; a
    signal while the restore runs is ignored. POSIX only (MOR-4497). The
    handlers that were there before are put back afterwards."""
    loop = asyncio.get_running_loop()

    def cancel() -> None:
        if run.restoring:
            return
        run.cancelled = True
        if run.task is not None:
            run.task.cancel()

    previous: dict[int, Any] = {}
    for name in ("SIGINT", "SIGTERM", "SIGHUP"):
        with suppress(AttributeError, NotImplementedError, RuntimeError, ValueError):
            number = getattr(signal, name)
            old = signal.getsignal(number)
            loop.add_signal_handler(number, cancel)
            previous[number] = old

    def remove() -> None:
        for number, old in previous.items():
            loop.remove_signal_handler(number)
            signal.signal(number, old)

    return remove


async def run_checks(
    config: RigConfig,
    request: ChecksRequest,
    *,
    exchange: Exchange,
    gap_s: float,
    timeout_s: float,
    strays: _Strays | None = None,
) -> ChecksResult:
    """Run the profile's checks after the read pass (pushes go to *strays*)."""
    gate = _SendGate(gap_s, allowed=frozenset())
    seen = strays or _Strays(time.monotonic())
    run = _Run(config, exchange, gate, timeout_s, request, seen)
    remove = _install_signals(run)
    records: list[CheckRecord] = []
    try:
        for check in config.probe_checks:
            records.append(record := await _run_one(run, check))
            left = record.status == "ran" and not (record.restored and record.verified)
            if left or run.cancelled or gate.stop_status != "completed":
                break
    finally:
        remove()
    failed = [
        r for r in records if r.status == "ran" and not (r.restored and r.verified)
    ]
    termination = None
    if failed:
        termination = TerminationInfo("restore_failed", failed[0].error or failed[0].id)
    elif run.cancelled:
        termination = TerminationInfo("cancelled", "a signal or Ctrl-C stopped them")
    elif gate.stop_status != "completed":
        termination = TerminationInfo(gate.stop_status, gate.stop_detail)
    return ChecksResult(tuple(records), 4 if failed else 0, termination, (*seen.rows,))
