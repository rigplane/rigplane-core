"""The write-check engine (MOR-4464 B1b): one mechanism for CI-V and Yaesu CAT.

A check (profile data, ``profiles/probe_checks.py``) names table entries and
values, never bytes. Every frame of a check is built and judged by
``judged_frame`` inside :func:`_send`, immediately before the port, and
``_send`` is the only function here that reaches the port.

Per check, before any write: preconditions (a shell-style pattern over the
reply text), witness reads, the originals of every restore item, and each
original encoded through ``judged_frame`` as the write that puts it back: an
original outside its entry's domain (a digital-voice mode, a frequency
outside the profile's ranges) refuses the check, so a restore is always
possible. Then the person's consent, then the write-ahead journal (fsynced),
then the no-op rehearsal of each restore write, the steps, and the restore:
originals in reverse order, each write judged again, retried once, read back
and compared; witness reads after. A power write above its original is
refused at run time. SIGINT/SIGTERM/SIGHUP cancel into the same restore,
and a signal during the restore is ignored. An ``ask`` waits 60 s at most.

Known gap: no Windows console-close handler here (MOR-4497, its own PR
before B2 reaches any customer); the journal covers a closed window.
"""

from __future__ import annotations

import asyncio
import fnmatch
import signal
from collections.abc import Awaitable, Callable
from contextlib import suppress
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from rigplane.commands import parse_civ_frame
from rigplane.probe.journal import ChecksJournal, remove_journal, write_journal
from rigplane.probe.model import (
    CheckAnswerRow,
    CheckRecord,
    CheckStepRow,
    CheckWitness,
    TerminationInfo,
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

__all__ = [
    "ASK_TIMEOUT_S",
    "ChecksRequest",
    "ChecksResult",
    "radio_facts",
    "run_checks",
]

#: How long an ``ask`` waits for the person before it is skipped.
ASK_TIMEOUT_S = 60.0

#: Entries whose writes may never exceed the value read before the check.
_POWER = frozenset({"power", "rf_power"})

#: Hand-settable names of the table entries (the journal and the file).
_LABELS = {
    "freq_a": "VFO-A",
    "freq_b": "VFO-B",
    "mode_main": "mode",
    "split": "split",
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
    """What the CLI hands the runner when ``--checks`` was asked for:
    ``consent(check, originals)`` decides each check, ``ask`` puts a question
    (``None``: skipped), ``on_event`` sees ``journal_written``,
    ``steps_begin`` and ``restore_begin``."""

    journal_path: Path
    consent: Callable[[ProbeCheck, dict[str, str]], bool]
    ask: Callable[[str], Awaitable[str | None]]
    on_event: Callable[[str], None] | None = None
    ask_timeout_s: float = ASK_TIMEOUT_S


@dataclass(frozen=True, slots=True)
class ChecksResult:
    """Every check record; ``exit_code`` 4 when a check could not be put back
    or verified; ``termination`` when the checks ended the run differently."""

    records: tuple[CheckRecord, ...]
    exit_code: int
    termination: TerminationInfo | None


class _Stop(Exception):
    """A check stops here: refused before writing, or a step failed."""


@dataclass(slots=True)
class _Run:
    config: RigConfig
    exchange: Exchange
    gate: _SendGate
    window: float
    request: ChecksRequest
    task: asyncio.Task[None] | None = None
    restoring: bool = False
    cancelled: bool = False


@dataclass(slots=True)
class _Check:
    check: ProbeCheck
    table: Table
    steps: list[CheckStepRow] = field(default_factory=list)
    answers: list[CheckAnswerRow] = field(default_factory=list)
    originals: list[tuple[Key, object]] = field(default_factory=list)
    before: dict[str, str | None] = field(default_factory=dict)


def radio_facts(config: RigConfig) -> RadioFacts:
    """The facts the loader compiled the checks against, from the config."""
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
        freq_ranges=tuple((r["start_hz"], r["end_hz"]) for r in config.freq_ranges),
    )


def _hand(entry: str, receiver: int | None, value: object) -> tuple[str, str]:
    """(name, value) as a person sets it on the radio."""
    name = _LABELS.get(entry, entry) + (" (SUB)" if receiver == 1 else "")
    if entry.startswith("freq") and isinstance(value, int):
        return name, f"{value / 1e6:.6f}".rstrip("0").rstrip(".") + " MHz"
    if isinstance(value, tuple) and len(value) == 3:  # CI-V mode, data, filter
        return name, f"{value[0]} (data {value[1]}, filter {value[2]})"
    return name, str(value)


def _value(run: _Run, entry: Entry, reply: bytes | None) -> object | None:
    """Decode a read reply with the entry's own codec (the FT; read answer
    goes through the profile's ``[vfo.split].read_map``)."""
    if reply is None or entry.write is None or entry.codec is None:
        return None
    if run.config.protocol_type == "yaesu_cat":
        payload = reply[:-1] if reply.endswith(b";") else b""
    else:
        frame = parse_civ_frame(reply)
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
    run: _Run,
    state: _Check,
    entry: str,
    form: str,
    value: object = None,
    receiver: int | None = None,
) -> tuple[bytes, bytes | None, str]:
    """The ONLY path to the port for check traffic: judge the exact bytes
    with ``judged_frame``, then send them once through the read pass gate."""
    body = judged_frame(state.table, entry, form, value, receiver, state.check.restored)
    protocol = run.config.protocol_type
    cat = protocol == "yaesu_cat"
    item = ReadRequest(entry, body, "cat" if cat else "get")
    run.gate.allowed = run.gate.allowed | {body}
    answer = await _gated_ask(
        run.gate,
        run.exchange,
        body,
        run.window,
        attribute=(lambda chunk: chunk == b"?;")
        if cat and form == "write"
        else _matcher(protocol, item),
        garbled=_garble_check(protocol),
    )
    if answer is None:  # the gate already stopped: nothing was sent
        stop = run.gate.stop_status
        return body, None, "silent" if stop == "silence_stop" else "transport_error"
    if answer.broken is not None:
        run.gate.stop_transport("write check", answer.broken)
        return body, None, "transport_error"
    if answer.reply is not None:
        run.gate.note_answered()
    elif not (cat and form == "write"):  # a Yaesu write answers nothing
        run.gate.note_silent()
    return body, answer.reply, _outcome(protocol, item, answer)


def _accepted(run: _Run, outcome: str) -> bool:
    return outcome == (
        "silent" if run.config.protocol_type == "yaesu_cat" else "ack_only"
    )


def _hex(reply: bytes | None) -> str | None:
    return None if reply is None else reply.hex()


async def _read(run: _Run, state: _Check, key: Key) -> object | None:
    body, reply, outcome = await _send(run, state, key[0], "read", None, key[1])
    value = _value(run, state.table.entry(key[0]), reply)
    shown = None if value is None else _hand(*key, value)[1]
    state.steps.append(
        CheckStepRow("read", key[0], body.hex(), _hex(reply), outcome, shown)
    )
    return value


async def _write(run: _Run, state: _Check, key: Key, value: object) -> bool:
    body, reply, outcome = await _send(run, state, key[0], "write", value, key[1])
    state.steps.append(
        CheckStepRow(
            "write",
            key[0],
            body.hex(),
            _hex(reply),
            outcome,
            _hand(*key, value)[1],
        )
    )
    return _accepted(run, outcome)


async def _witness(run: _Run, state: _Check) -> dict[str, str | None]:
    out: dict[str, str | None] = {}
    for entry in state.check.witness:
        _, reply, _ = await _send(run, state, entry, "read")
        out[entry] = _snapshot_value(run.config.protocol_type, reply)
    return out


async def _prepare(run: _Run, state: _Check) -> None:
    """Everything before the first write; ``_Stop`` refuses the check."""
    for pre in state.check.preconditions:
        _, reply, _ = await _send(run, state, pre.entry, "read")
        text = _snapshot_value(run.config.protocol_type, reply) or ""
        if not fnmatch.fnmatchcase(text, pre.equals):
            raise _Stop(f"precondition {pre.entry} not met (radio answered {text!r})")
    state.before = await _witness(run, state)
    for item in state.check.restore:
        key = (item.entry, item.receiver)
        value = await _read(run, state, key)
        try:
            if value is None:
                raise ValueError("unreadable")
            judged_frame(
                state.table, key[0], "write", value, key[1], state.check.restored
            )
        except ValueError:
            name = _hand(*key, 0)[0]
            raise _Stop(
                f"the radio's current {name} is not something this check can put back"
            ) from None
        state.originals.append((key, value))


async def _steps(run: _Run, state: _Check) -> None:
    originals = dict(state.originals)
    for item in (o for o in state.check.restore if o.rehearse):
        key = (item.entry, item.receiver)
        if not await _write(run, state, key, originals[key]):
            raise _Stop(f"the radio refused to keep its own {_hand(*key, 0)[0]}")
        if await _read(run, state, key) != originals[key]:
            raise _Stop(f"{_hand(*key, 0)[0]} changed on a write of its own value")
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
            await _read(run, state, key)
        else:
            original, value = originals.get(key), step.value
            if (
                step.entry in _POWER
                and isinstance(value, int)
                and isinstance(original, int)
            ):
                if value > original:
                    raise _Stop(
                        f"a write of {value} is above the original power {original}"
                    )
            if not await _write(run, state, key, step.value):
                raise _Stop(f"the radio did not accept {step.entry} = {step.value!r}")


async def _restore(run: _Run, state: _Check) -> tuple[bool, bool, str | None]:
    """Originals back in reverse order: (restored, verified, error)."""
    run.restoring = True
    if run.request.on_event:
        run.request.on_event("restore_begin")
    restored = verified = True
    error = None
    for key, value in reversed(state.originals):
        accepted = await _write(run, state, key, value) or await _write(
            run, state, key, value
        )
        name, text = _hand(*key, value)
        if not accepted:
            restored = verified = False
            error = f"could not put {name} back to {text}; please set it by hand"
        elif await _read(run, state, key) != value:
            verified = False
            error = error or f"{name} reads differently after the restore (was {text})"
    return restored, verified, error


async def _run_one(run: _Run, check: ProbeCheck) -> CheckRecord:
    ids = {s.entry for s in check.steps if s.entry} | {r.entry for r in check.restore}
    ids |= set(check.witness) | {p.entry for p in check.preconditions}
    state = _Check(check, compile_table(radio_facts(run.config), ids))
    originals: dict[str, str] = {}

    def record(
        status: str,
        error: str | None = None,
        witness: CheckWitness | None = None,
        restored: bool = True,
        verified: bool = True,
    ) -> CheckRecord:
        return CheckRecord(
            check.id,
            check.title,
            status,
            tuple(state.steps),
            tuple(state.answers),
            originals,
            witness or CheckWitness({}, {}, []),
            restored,
            verified,
            error,
        )

    try:
        await _prepare(run, state)
    except _Stop as stop:
        return record("refused", error=str(stop))
    originals.update(_hand(*key, value) for key, value in state.originals)
    if run.cancelled:
        return record("refused", error="cancelled before any write")
    if not run.request.consent(check, dict(originals)):
        return record("declined")
    journal = run.request.journal_path
    saved = datetime.now(timezone.utc).isoformat()
    write_journal(
        journal,
        ChecksJournal(
            check.id, check.title, run.config.model, tuple(originals.items()), saved
        ),
    )
    if run.request.on_event:
        run.request.on_event("journal_written")
    error: str | None = None
    external = False
    run.task = asyncio.ensure_future(_steps(run, state))
    try:
        await run.task
    except _Stop as stop:
        error = str(stop)
    except asyncio.CancelledError:
        external = not run.cancelled
        error = "cancelled; the settings were put back first"
    finally:
        run.task = None
    restored, verified, restore_error = await _restore(run, state)
    after = await _witness(run, state)
    changed = sorted(n for n in check.witness if state.before.get(n) != after.get(n))
    verified = verified and not changed
    if restored and verified:
        remove_journal(journal)
    run.restoring = False
    if external:
        raise asyncio.CancelledError
    return record(
        "ran",
        witness=CheckWitness(dict(state.before), after, changed),
        restored=restored,
        verified=verified,
        error=restore_error or (f"witness {changed[0]} changed" if changed else error),
    )


def _install_signals(run: _Run) -> Callable[[], None]:
    """SIGINT/SIGTERM/SIGHUP cancel the running steps into the restore; a
    signal while the restore runs is ignored. POSIX only (MOR-4497)."""
    loop = asyncio.get_running_loop()

    def cancel() -> None:
        if run.restoring:
            return
        run.cancelled = True
        if run.task is not None:
            run.task.cancel()

    installed: list[int] = []
    for name in ("SIGINT", "SIGTERM", "SIGHUP"):
        number = getattr(signal, name, None)
        with suppress(NotImplementedError, RuntimeError, ValueError):
            if number is not None:
                loop.add_signal_handler(number, cancel)
                installed.append(number)

    def remove() -> None:
        for number in installed:
            loop.remove_signal_handler(number)

    return remove


async def run_checks(
    config: RigConfig,
    request: ChecksRequest,
    *,
    exchange: Exchange,
    gap_s: float,
    timeout_s: float,
    checks: tuple[ProbeCheck, ...] | None = None,
) -> ChecksResult:
    """Run the profile's checks (or *checks*) after the read pass."""
    run = _Run(
        config, exchange, _SendGate(gap_s, allowed=frozenset()), timeout_s, request
    )
    remove = _install_signals(run)
    records: list[CheckRecord] = []
    try:
        for check in config.probe_checks if checks is None else checks:
            records.append(await _run_one(run, check))
            if run.cancelled or run.gate.stopped:
                break
    finally:
        remove()
    failed = [
        r for r in records if r.status == "ran" and not (r.restored and r.verified)
    ]
    if failed:
        termination = TerminationInfo("restore_failed", failed[0].error or failed[0].id)
    elif run.cancelled:
        termination = TerminationInfo(
            "cancelled", "a signal or Ctrl-C stopped the checks"
        )
    elif run.gate.stopped:
        termination = TerminationInfo(run.gate.stop_status, run.gate.stop_detail)
    else:
        termination = None
    return ChecksResult(tuple(records), 4 if failed else 0, termination)
