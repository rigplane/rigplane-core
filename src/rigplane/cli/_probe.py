"""Read-only radio probe wiring (MOR-4464 PR A).

Thin CLI over ``rigplane.probe``: argument parsing, prompts, and the USB
serial transport. Nothing here decides what is sent; the runner sends
only what ``readlist.build_read_plan`` asks, and records the rest as
skipped with a reason. With ``--checks`` the engine (``probe/checks.py``)
builds and judges every write; this module only asks the person.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import os
import re
import shutil
import subprocess
import sys
import tomllib
from collections.abc import Awaitable, Callable, Iterable, Iterator, Mapping
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Any, Protocol, runtime_checkable

import platformdirs

from rigplane.backends.discovery import RadioDiscoveryResult, discover_serial_radios
from rigplane.cli._models import match_rig
from rigplane.commands import CONTROLLER_ADDR, build_civ_frame, command_carries_sub
from rigplane.core.serial_open import open_serial_port
from rigplane.profiles.rig_loader import RigConfig
from rigplane.probe import render
from rigplane.probe.checks import ASK_TIMEOUT_S, ChecksRequest
from rigplane.probe.journal import (
    journal_path,
    leftover_journal_line,
    remove_journal,
)
from rigplane.probe.model import ProbeFile
from rigplane.probe.render import DEFAULT_PROBE_PROG
from rigplane.probe.readlist import ReadRequest, build_read_plan
from rigplane.probe.runner import (
    CIV_TIMEOUT_S,
    CAT_TIMEOUT_S,
    Exchange,
    ProbeRun,
    run_probe,
)

if TYPE_CHECKING:
    from rigplane.profiles.probe_checks import ProbeCheck

__all__ = [
    "RadioSelection",
    "UnknownProfileError",
    "UnsupportedProtocolError",
    "add_subparser",
    "build_probe_parser",
    "execute",
    "open_result_folder",
    "resolve_config",
    "run",
    "select_radio",
]

_SUPPORTED = ("civ", "yaesu_cat")


class UnknownProfileError(ValueError):
    """No rig profile matches the requested model name."""


class UnsupportedProtocolError(ValueError):
    """The profile's protocol is not probed yet (USB CI-V and Yaesu CAT only)."""


def resolve_config(name: str, rigs: Mapping[str, RigConfig]) -> RigConfig:
    """Return the rig profile for a ``rigplane models`` name.

    Raises :class:`UnknownProfileError` for an unknown model and
    :class:`UnsupportedProtocolError` for a protocol the probe does not
    speak yet.
    """
    config = match_rig(rigs, name)
    if config is None:
        known = ", ".join(sorted(rigs))
        raise UnknownProfileError(f"unknown profile {name!r} (known: {known})")
    if config.protocol_type not in _SUPPORTED:
        raise UnsupportedProtocolError(
            render.not_supported_message(config.protocol_type)
        )
    return config


@dataclass(frozen=True, slots=True)
class RadioSelection:
    """One radio picked for the probe run: its profile and how to reach it."""

    config: RigConfig
    profile_toml_bytes: bytes
    device: str
    baud: int
    address: int | None  # None means the profile's own CI-V address


def _config_for_result(
    rigs: Mapping[str, RigConfig], result: RadioDiscoveryResult
) -> RigConfig | None:
    """The shipped profile for a discovered radio, or None without one."""
    for key in (result.model, result.profile_id):
        if key and (matched := match_rig(rigs, key)) is not None:
            return matched
    return None


def _matches_config(
    rigs: Mapping[str, RigConfig], result: RadioDiscoveryResult, config: RigConfig
) -> bool:
    """Whether a discovered radio is the requested profile's radio."""
    return _config_for_result(rigs, result) is config


def _pick_selection(
    rigs: Mapping[str, RigConfig],
    rigs_dir: Path,
    found: RadioDiscoveryResult,
    picked: RigConfig,
    baud_override: int | None,
    address_override: int | None,
) -> RadioSelection:
    """Build the run parameters for one chosen discovery result."""
    baud = baud_override if baud_override is not None else found.baudrate
    if address_override is not None:
        address: int | None = address_override
    elif picked.protocol_type == "civ" and isinstance(found.address, int):
        address = found.address
    else:
        address = None
    return RadioSelection(
        config=picked,
        profile_toml_bytes=profile_source_bytes(rigs_dir, picked),
        device=found.port,
        baud=baud,
        address=address,
    )


async def select_radio(
    *,
    rigs: Mapping[str, RigConfig],
    rigs_dir: Path,
    profile_name: str | None,
    baud_override: int | None,
    address_override: int | None,
    yes: bool,
    is_tty: bool,
    ask: Callable[[str], str],
    emit: Callable[[str], None] = print,
    discover: Callable[[], Awaitable[list[RadioDiscoveryResult]]] | None = None,
    prog: str = DEFAULT_PROBE_PROG,
) -> RadioSelection | int:
    """Pick the radio to probe: discovery result or an exit code.

    Returns a :class:`RadioSelection` to probe, or the process exit code
    when the run stops here (0 the customer quit, 2 bad usage, 3 no
    radio found). Only USB discovery has touched the ports at this
    point: declining or quitting sends nothing more.
    """
    config: RigConfig | None = None
    if profile_name is not None:
        try:
            config = resolve_config(profile_name, rigs)
        except UnknownProfileError as error:
            emit(f"Error: {error}")
            return 2
        except UnsupportedProtocolError as error:
            emit(str(error))
            return 2
    emit(render.discovery_announcement())
    probe = discover if discover is not None else discover_serial_radios
    try:
        results = await probe()
    except OSError:
        # A USB device that disappears mid-scan fails the scan, not the
        # run: pyserial's SerialException subclasses OSError (see
        # backends/discovery.py), and CancelledError is not an OSError,
        # so cancellation still propagates.
        emit(render.discovery_failed_message())
        return 3
    if config is not None:
        results = [found for found in results if _matches_config(rigs, found, config)]
    if not results:
        emit(render.no_radio_found_message(profile_name, prog))
        return 3
    if len(results) == 1:
        found = results[0]
        emit(render.found_line(found.model, found.port))
        picked = config if config is not None else _config_for_result(rigs, found)
        if picked is None:
            emit(render.no_profile_for_radio_message(found.model))
            return 2
        if picked.protocol_type not in _SUPPORTED:
            emit(render.not_supported_message(picked.protocol_type))
            return 2
        if not yes and not is_tty:
            emit(render.single_no_tty_message(found.model, found.port, prog))
            return 2
        if not yes and not _is_yes(ask(render.confirm_radio_prompt())):
            return 0
        return _pick_selection(
            rigs, rigs_dir, found, picked, baud_override, address_override
        )
    emit(render.radio_choice_list([(found.model, found.port) for found in results]))
    if not is_tty or yes:
        # No guessing: several radios need a person (or a named port) to choose.
        emit(render.several_no_tty_message(prog))
        return 2
    while True:
        answer = ask(render.radio_choice_prompt(len(results))).strip()
        if answer == "":
            return 0
        try:
            index = int(answer)
        except ValueError:
            index = 0
        if 1 <= index <= len(results):
            found = results[index - 1]
            picked = config if config is not None else _config_for_result(rigs, found)
            if picked is None:
                emit(render.no_profile_for_radio_message(found.model))
                return 2
            if picked.protocol_type not in _SUPPORTED:
                emit(render.not_supported_message(picked.protocol_type))
                return 2
            return _pick_selection(
                rigs, rigs_dir, found, picked, baud_override, address_override
            )
        emit(render.bad_choice_message())


@runtime_checkable
class _Closable(Protocol):
    """An exchange that owns its serial writer (closed when the run ends)."""

    def close(self) -> Any: ...


class _OwnedExchange:
    """An exchange bound to its serial writer.

    The runner closes the port when the run ends; the writer is recorded
    here so ``execute`` can do that without changing the exchange type
    the tests double.
    """

    def __init__(self, exchange: Exchange, writer: Any) -> None:
        self._exchange = exchange
        self._writer = writer

    async def __call__(self, request: bytes | None, timeout: float) -> bytes | None:
        return await self._exchange(request, timeout)

    def close(self) -> None:
        self._writer.close()


@contextmanager
def _closing(exchange: Exchange) -> Iterator[Exchange]:
    """Close the owned writer, if any, on completion, decline, error or cancel."""
    try:
        yield exchange
    finally:
        if isinstance(exchange, _Closable):
            exchange.close()


async def _yaesu_exchange(reader: Any, writer: Any) -> Exchange:
    last_sent: bytes | None = None

    async def exchange(request: bytes | None, timeout: float) -> bytes | None:
        nonlocal last_sent
        if request:
            last_sent = bytes(request)
            writer.write(request)
            await writer.drain()
        try:
            if request is not None:
                return await asyncio.wait_for(reader.readuntil(b";"), timeout)
            while True:
                raw: bytes = await asyncio.wait_for(reader.readuntil(b";"), timeout)
                if raw == last_sent:
                    continue  # USB echo of our own request, not the answer
                return raw
        except asyncio.TimeoutError:
            return None
        # IncompleteReadError (a disconnected stream, with .partial bytes
        # kept) propagates: the runner records it as a transport failure.

    return _OwnedExchange(exchange, writer)


async def _civ_exchange(reader: Any, writer: Any, radio_addr: int) -> Exchange:
    last_sent: bytes | None = None

    async def exchange(request: bytes | None, timeout: float) -> bytes | None:
        nonlocal last_sent
        if request:
            command, rest = request[0], request[1:]
            if command_carries_sub(command) and rest:
                sub, data = rest[0], rest[1:]
            else:
                sub, data = None, rest
            last_sent = build_civ_frame(
                radio_addr, CONTROLLER_ADDR, command, sub=sub, data=data or None
            )
            writer.write(last_sent)
            await writer.drain()
        try:
            while True:
                raw: bytes = await asyncio.wait_for(reader.readuntil(b"\xfd"), timeout)
                if raw == last_sent:
                    continue  # USB echo of our own frame, not the answer
                return raw
        except asyncio.TimeoutError:
            return None
        # IncompleteReadError (a disconnected stream, with .partial bytes
        # kept) propagates: the runner records it as a transport failure.

    return _OwnedExchange(exchange, writer)


def _model_file_stem(model: str) -> str:
    """The model name made safe for a file name, case preserved."""
    return re.sub(r"[^A-Za-z0-9._-]+", "-", model).strip("-") or "radio"


def _default_documents_dir() -> Path:
    """The customer's Documents folder (patched in tests via ``documents_dir``)."""
    return Path(platformdirs.user_documents_dir())


def _resolve_save_dir(documents_dir: Callable[[], Path] | None) -> Path:
    """Where the default output file goes: Documents, else home, else cwd.

    The first folder that exists and is writable wins, so a missing or
    read-only Documents folder falls back instead of failing the run.
    Each location is resolved only when reached: a failing Documents
    resolver does not prevent trying home, and an unavailable current
    directory does not fail a run whose Documents folder is fine.
    """
    candidates: list[Callable[[], Path]] = []
    if documents_dir is not None:
        candidates.append(documents_dir)
    else:
        candidates.append(_default_documents_dir)
    candidates.append(lambda: Path.home())
    candidates.append(lambda: Path.cwd())
    for resolve in candidates:
        try:
            candidate = resolve()
        except OSError:
            continue
        try:
            if candidate.is_dir() and os.access(candidate, os.W_OK):
                return candidate
        except OSError:
            continue
    return Path.cwd()


def _default_out_path(
    model: str, documents_dir: Callable[[], Path] | None = None
) -> Path:
    """The default output candidate: Documents, dated, never overwriting."""
    base = _resolve_save_dir(documents_dir)
    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    stem = _model_file_stem(model)
    candidate = base / f"{stem}-radio-probe-{stamp}.json"
    index = 2
    while candidate.exists():
        candidate = base / f"{stem}-radio-probe-{stamp}-{index}.json"
        index += 1
    return candidate


def _write_capture(path: Path, content: str, *, exclusive: bool) -> Path:
    """Write the capture file; return the path actually written.

    An explicit ``--out`` names the file, so it is written as today
    (truncating). The default name is only a candidate until the save
    answer: it is created exclusively (mode ``"x"``), advancing to the
    next ``-N`` suffix on collision and retrying, so a capture that
    appears between selection and the save answer is never truncated.
    """
    if not exclusive:
        path.write_text(content, encoding="utf-8")
        return path
    candidate = path
    index = 2
    while True:
        try:
            with candidate.open("x", encoding="utf-8") as handle:
                handle.write(content)
            return candidate
        except FileExistsError:
            candidate = path.with_name(f"{path.stem}-{index}{path.suffix}")
            index += 1


def open_result_folder(directory: Path) -> None:
    """Open the save folder for the customer; never raises or waits.

    Windows opens it directly; macOS uses ``open``; Linux uses
    ``xdg-open`` only when it is on PATH. The opener is launched without
    waiting (a foreground file manager must not hold the run or the
    serial port). Any failure is silent: the file is saved either way
    and its full path is already printed. This is the only GUI opener
    in ``src``; callers invoke it only for an interactive run (see
    ``_stdout_is_terminal``), never from tests, pipes or sidecars.
    """
    try:
        if sys.platform == "win32":
            starter = getattr(os, "startfile", None)
            if starter is not None:
                starter(str(directory))
        elif sys.platform == "darwin":
            subprocess.Popen(
                ["open", str(directory)],
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
        elif shutil.which("xdg-open") is not None:
            subprocess.Popen(
                ["xdg-open", str(directory)],
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
    except Exception:
        pass


def _stdout_is_terminal() -> bool:
    """Stdout is a terminal the customer watches.

    The save folder opens only for an interactive run: stdin is a
    terminal (``is_tty``) and stdout is one too. Tests capture stdout,
    pipes redirect it, and the sidecar hand-over inherits none, so none
    of them ever opens a Finder window. Every documented customer run
    (PowerShell, Terminal, a shell) has both streams on the terminal.
    """
    isatty = getattr(sys.stdout, "isatty", None)
    return bool(isatty is not None and isatty())


def _is_yes(answer: str) -> bool:
    return answer.strip().lower() in ("", "y", "yes")


def _is_strict_yes(answer: str) -> bool:
    """The checks questions default to no: only an explicit yes counts."""
    return answer.strip().lower() in ("y", "yes")


def _answer(prompt: str) -> str:
    """One terminal answer; end of input is an empty answer."""
    try:
        return input(prompt)
    except EOFError:
        return ""


Lines = Callable[[str], Awaitable[str | None]]  # a prompt -> its answer (None: EOF)


class _TerminalLines:
    """The ``--checks`` questions, read from the terminal on the event loop
    (signal handlers and the question time limit keep running). Each prompt
    first discards whatever was typed before it opened, so a late answer to
    a question that timed out never answers a later one; the terminal is
    watched only while a prompt waits. POSIX only (Windows: MOR-4497)."""

    def __init__(self, fd: int, write: Callable[[str], None]) -> None:
        self._fd, self._write = fd, write

    async def __call__(self, prompt: str) -> str | None:
        import termios

        termios.tcflush(self._fd, termios.TCIFLUSH)
        self._write(prompt)
        loop = asyncio.get_running_loop()
        answer: asyncio.Future[str | None] = loop.create_future()
        typed = bytearray()

        def readable() -> None:
            chunk = os.read(self._fd, 4096)
            typed.extend(chunk)
            if answer.done():
                return
            if b"\n" in typed:
                answer.set_result(typed.split(b"\n", 1)[0].decode(errors="replace"))
            elif not chunk:
                answer.set_result(None)

        loop.add_reader(self._fd, readable)
        try:
            return await answer
        finally:
            loop.remove_reader(self._fd)


_HAS_TERMINAL_READER = sys.platform != "win32"  # termios + loop.add_reader


def _write_prompt(text: str) -> None:
    sys.stdout.write(text)
    sys.stdout.flush()


def _checks_request(
    config: RigConfig, journal: Path, lines: Lines, emit: Callable[[str], None]
) -> ChecksRequest:
    """The person's side of the checks: the block question, one question per
    check (both default no, never answered by ``--yes``, end of input is a
    no) and the check asks. Every answer is awaited on the event loop."""
    total = len(config.probe_checks)

    async def agreed(prompt: str) -> bool:
        return _is_strict_yes(await lines(prompt) or "")

    async def begin() -> bool:
        emit(render.checks_block_start(total))
        return await agreed(render.checks_prompt())

    async def consent(check: ProbeCheck, originals: dict[str, str]) -> bool:
        index = config.probe_checks.index(check) + 1
        what, after = check.explain_what, check.explain_after
        emit(
            render.check_screen(
                index, total, check.title, what, after, originals, check.warnings
            )
        )
        return await agreed(render.check_prompt())

    async def ask(question: str) -> str | None:
        answer = await lines(render.check_question_prompt(question, ASK_TIMEOUT_S))
        return (answer or "").strip() or None

    return ChecksRequest(
        consent, ask, ask_timeout_s=ASK_TIMEOUT_S, begin=begin, journal_path=journal
    )


async def execute(
    config: RigConfig,
    profile_toml_bytes: bytes,
    *,
    device_label: str,
    baud: int,
    out: str | None,
    include_private: bool,
    label: str,
    yes: bool,
    is_tty: bool,
    ask: Callable[[str], str],
    exchange_factory: Callable[[], Awaitable[Exchange]],
    gap_s: float = 0.15,
    timeout_s: float | None = None,
    emit: Callable[[str], None] = print,
    sibling_configs: Iterable[RigConfig] | None = None,
    open_folder: Callable[[Path], None] | None = None,
    documents_dir: Callable[[], Path] | None = None,
    checks: bool = False,
    check_lines: Lines | None = None,
) -> int:
    """Run the probe end to end; return the process exit code.

    0 ok, 2 bad usage (unknown profile, unsupported protocol, refusing to
    save without ``--yes`` outside a TTY, ``--checks`` outside a TTY), 3
    the port cannot be opened or nothing answered at all, 4 a check could
    not put the radio back. The file is written only after the save
    answer. The save folder opens only for an interactive run (stdin and
    stdout are both terminals); anything else never opens a file
    manager. The tool itself records no port path, host, or user - only
    what you type in ``--label``, plus the radio's answers. The checks
    questions come from *check_lines* (default: the terminal).
    """
    if config.protocol_type not in _SUPPORTED:
        emit(render.not_supported_message(config.protocol_type))
        return 2
    if checks and not is_tty:
        emit(render.checks_need_terminal_message())
        return 2
    if not is_tty and not yes:
        emit("Refusing to save without --yes when stdin is not a terminal.")
        return 2
    if checks and not config.probe_checks:
        emit(render.no_checks_message())
        checks = False
    if checks and check_lines is None and not _HAS_TERMINAL_READER:
        emit(render.checks_not_on_windows_message())  # no reader yet (MOR-4497)
        checks = False
    request = None
    if checks:
        lines = check_lines or _TerminalLines(sys.stdin.fileno(), _write_prompt)
        request = _checks_request(config, journal_path(), lines, emit)

    plan = build_read_plan(
        config, include_private=include_private, siblings=sibling_configs
    )
    items = list(plan.asked)
    window = timeout_s
    if window is None:
        window = CIV_TIMEOUT_S if config.protocol_type == "civ" else CAT_TIMEOUT_S
    emit(
        render.start_screen(
            profile_name=config.model,
            port_label=f"{device_label} @ {baud}",
            n_questions=len(items),
            checks=checks,
        )
    )
    if not yes and not _is_yes(ask("Continue? [Y/n] ")):
        return 0

    try:
        exchange = await exchange_factory()
    except OSError:
        emit(render.cannot_open_message(device_label))
        return 3

    with _closing(exchange):
        return await _execute_after_open(
            config=config,
            profile_toml_bytes=profile_toml_bytes,
            baud=baud,
            out=out,
            include_private=include_private,
            label=label,
            yes=yes,
            is_tty=is_tty,
            ask=ask,
            exchange=exchange,
            gap_s=gap_s,
            window=window,
            items=items,
            sibling_configs=sibling_configs,
            emit=emit,
            open_folder=open_folder,
            documents_dir=documents_dir,
            checks=request,
        )


async def _execute_after_open(
    *,
    config: RigConfig,
    profile_toml_bytes: bytes,
    baud: int,
    out: str | None,
    include_private: bool,
    label: str,
    yes: bool,
    is_tty: bool,
    ask: Callable[[str], str],
    exchange: Exchange,
    gap_s: float,
    window: float,
    items: list[ReadRequest],
    sibling_configs: Iterable[RigConfig] | None,
    emit: Callable[[str], None],
    open_folder: Callable[[Path], None] | None,
    documents_dir: Callable[[], Path] | None,
    checks: ChecksRequest | None,
) -> int:
    """The probe pass, the checks and save; the caller owns closing the exchange."""

    def on_progress(done: int, total: int) -> None:
        if done % 20 == 0 or done == total:
            emit(render.progress_line(done, total))

    report: ProbeRun = await run_probe(
        config,
        hashlib.sha256(profile_toml_bytes).hexdigest(),
        exchange=exchange,
        port_kind="usb",
        baud=baud,
        label=label,
        include_private=include_private,
        gap_s=gap_s,
        timeout_s=window,
        items=items,
        siblings=sibling_configs,
        progress=on_progress,
        checks=checks,
    )
    records = report.file.checks
    if checks is not None and any(
        r.status == "ran" and not r.restored for r in records
    ):
        # first the engine's own now-vs-was line from the journal it kept
        emit(leftover_journal_line(checks.journal_path) or "")
    elif report.aborted is not None:
        emit(report.aborted)
    for index, record in enumerate(records, 1):
        emit(render.check_result_line(index, record))
    if records:
        emit(render.checks_done_line())
    if checks is not None and report.exit_code == 4:
        emit(render.journal_instruction_line(str(checks.journal_path)))

    if out is not None:
        saved_abs = Path(out).resolve()
        exclusive = False
    else:
        # The default name is only a candidate until the save answer;
        # the file itself is created exclusively at save time below.
        saved_abs = _default_out_path(config.model, documents_dir)
        exclusive = True
    content = report.file.to_json()
    identity = report.file.radio.identity_reported
    settings = [
        f"{name} {value}"
        for name, value in report.file.snapshot.before.items()
        if value is not None
    ]
    skipped_counts: dict[str, int] = {}
    for entry in report.file.skipped:
        skipped_counts[entry.reason] = skipped_counts.get(entry.reason, 0) + 1
    absent_answered = [
        render.AbsentAnswer(
            name=row.name,
            request_hex=row.request_hex,
            reply_hex=row.reply_hex,
        )
        for row in report.file.commands
        if row.expected == "absent"
        and row.outcome == "data"
        and not row.name.startswith("unknown:")
    ]
    present_missed = [
        row.name
        for row in report.file.commands
        if row.expected == "present" and row.outcome in ("refused", "silent")
    ]
    emit(
        render.pre_save_summary(
            path=str(saved_abs),
            size_bytes=len(content.encode("utf-8")),
            n_commands=len(report.file.commands),
            identity_text=(
                f"model code {identity.parsed}" if identity.parsed else "unknown model"
            ),
            settings_lines=settings,
            private_reads_included=include_private,
            skipped_counts=skipped_counts,
            absent_answered=absent_answered,
            present_missed=present_missed,
            termination=report.file.termination,
            checks=records,
        )
    )
    if not yes and not _is_yes(ask("Save the file? [Y/n] ")):
        return report.exit_code
    try:
        actual = _write_capture(saved_abs, content, exclusive=exclusive)
    except OSError as exc:
        emit(f"Cannot save {saved_abs}: {exc.strerror or exc}")
        return report.exit_code or 1
    emit(render.closing_line(str(actual)))
    if is_tty and not yes and _stdout_is_terminal():
        opener = open_folder if open_folder is not None else open_result_folder
        emit(render.opening_folder_line(str(actual.parent)))
        try:
            opener(actual.parent)
        except Exception:
            pass  # an opening failure is never an error and is silent
    if not _any_answer(report.file):
        emit(render.no_answers_message())
        return 3
    return report.exit_code


def _any_answer(file: ProbeFile) -> bool:
    if file.radio.identity_reported.raw:
        return True
    if any(value is not None for value in file.snapshot.before.values()):
        return True
    if any(value is not None for value in file.snapshot.after.values()):
        return True
    return any(
        row.outcome in ("data", "refused", "ack_only", "malformed", "unsolicited")
        for row in file.commands
    )


def profile_source_bytes(rigs_dir: Path, config: RigConfig) -> bytes:
    """The exact TOML bytes the profile was loaded from.

    The discovery map keeps models, not paths, so the directory is
    scanned for the file whose ``[radio].model`` matches. Drafts and
    underscore helpers are skipped exactly like in discovery.
    """
    for path in sorted(rigs_dir.glob("*.toml")):
        if path.name.startswith("_") or path.name.endswith(".draft.toml"):
            continue
        try:
            with path.open("rb") as handle:
                data = tomllib.load(handle)
        except (OSError, tomllib.TOMLDecodeError):
            continue
        radio = data.get("radio")
        if isinstance(radio, dict) and radio.get("model") == config.model:
            return path.read_bytes()
    raise UnknownProfileError(f"profile source for {config.model!r} not found")


def _civ_address(value: str) -> int:
    """Parse a CI-V radio address; a bad value fails parsing, not the port open."""
    try:
        number = int(value, 0)
    except ValueError:
        raise argparse.ArgumentTypeError(
            f"CI-V address must be a byte 0-255, got {value!r}"
        ) from None
    if not 0 <= number <= 0xFF:
        raise argparse.ArgumentTypeError(
            f"CI-V address must be a byte 0-255, got {value!r}"
        )
    return number


def _positive_baud(value: str) -> int:
    """Parse a baud rate; a bad value fails parsing, not the port open."""
    try:
        number = int(value, 0)
    except ValueError:
        raise argparse.ArgumentTypeError(
            f"baud rate must be a positive number, got {value!r}"
        ) from None
    if number <= 0:
        raise argparse.ArgumentTypeError(
            f"baud rate must be a positive number, got {value!r}"
        )
    return number


def add_probe_arguments(p: argparse.ArgumentParser) -> None:
    """Add the probe options to a parser (one definition, both entry points)."""
    p.add_argument(
        "profile",
        nargs="?",
        default=None,
        help="Radio model from `rigplane models` (omit to scan USB for it)",
    )
    p.add_argument(
        "--port",
        required=False,
        default=None,
        help="Serial device (e.g. /dev/ttyUSB0 or COM5); omit to scan USB for it",
    )
    p.add_argument("--baud", type=_positive_baud, default=None, help="Baud rate")
    p.add_argument(
        "--address",
        type=_civ_address,
        default=None,
        help="CI-V radio address (default: the profile's)",
    )
    p.add_argument("--out", default=None, help="Output JSON path")
    p.add_argument(
        "--include-private",
        action="store_true",
        help="Also read stored memory contents (may hold callsigns/text)",
    )
    p.add_argument("--label", default="", help="Free-text label stored in the file")
    p.add_argument(
        "--yes",
        action="store_true",
        help="Answer the start and save prompts with yes (needed without a TTY; "
        "never answers a --checks question)",
    )
    p.add_argument(
        "--checks",
        action="store_true",
        help="After the read part, offer the radio's optional checks: each changes "
        "settings for a moment and puts them back, and asks you first",
    )


def add_subparser(sub: Any) -> argparse.ArgumentParser:
    """Register the ``probe`` subparser."""
    p: argparse.ArgumentParser = sub.add_parser(
        "probe",
        help="Record how your radio answers, into one file you can send to us.",
        description=render.probe_help(),
    )
    add_probe_arguments(p)
    return p


def build_probe_parser(prog: str = DEFAULT_PROBE_PROG) -> argparse.ArgumentParser:
    """Standalone probe parser showing ``prog`` as the command name.

    The Pro sidecar entry point parses with this; the usage line and
    ``--help`` name the command the user actually ran.
    """
    parser = argparse.ArgumentParser(prog=prog, description=render.probe_help(prog))
    add_probe_arguments(parser)
    return parser


def run(args: argparse.Namespace, prog: str = DEFAULT_PROBE_PROG) -> int:
    """Driver for the radio probe. Returns a process exit code.

    A check journal left behind is printed first, before anything else can
    stop the command (discovery, a usage error, a missing radio).
    """
    from rigplane.cli import _rigs_dir
    from rigplane.rig_loader import RigLoadError, discover_available_rigs

    journal = journal_path()
    if (left := leftover_journal_line(journal)) is not None:
        print(left)
        if not sys.stdin.isatty():
            print(render.journal_instruction_line(str(journal)))
        elif _is_strict_yes(_answer(render.journal_set_back_prompt())):
            remove_journal(journal)  # set back by hand: the checks may run again

    try:
        rigs = discover_available_rigs(_rigs_dir())
    except RigLoadError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1
    profile = getattr(args, "profile", None)
    port = getattr(args, "port", None)
    if port is not None and profile is None:
        print(render.port_without_profile_message(prog))
        return 2
    if profile is not None and port is not None:
        try:
            config = resolve_config(profile, rigs)
        except UnknownProfileError as error:
            print(f"Error: {error}")
            return 2
        except UnsupportedProtocolError as error:
            print(error)
            return 2
        baud = args.baud if args.baud is not None else config.default_baud
        device = port
        rigs_dir = _rigs_dir()
        toml_bytes = profile_source_bytes(rigs_dir, config)

        async def factory() -> Exchange:
            if config.protocol_type == "yaesu_cat":
                reader, writer = await open_serial_port(device, baudrate=baud)
                return await _yaesu_exchange(reader, writer)
            addr = args.address if args.address is not None else config.civ_addr
            reader, writer = await open_serial_port(device, baudrate=baud)
            return await _civ_exchange(reader, writer, addr)

        return asyncio.run(
            execute(
                config,
                toml_bytes,
                device_label=device,
                baud=baud,
                out=args.out,
                include_private=args.include_private,
                label=args.label,
                yes=args.yes,
                is_tty=sys.stdin.isatty(),
                ask=lambda prompt: input(prompt),
                exchange_factory=factory,
                sibling_configs=list(rigs.values()),
                checks=getattr(args, "checks", False),
            )
        )
    return asyncio.run(_run_with_selection(args, rigs, prog))


async def _run_with_selection(
    args: argparse.Namespace,
    rigs: Mapping[str, RigConfig],
    prog: str = DEFAULT_PROBE_PROG,
) -> int:
    """Discovery path: pick the radio, then probe it exactly like a named run."""
    from rigplane.cli import _rigs_dir

    is_tty = sys.stdin.isatty()
    checks = getattr(args, "checks", False)
    if checks and not is_tty:  # before the scan touches any port
        print(render.checks_need_terminal_message())
        return 2
    selection = await select_radio(
        rigs=rigs,
        rigs_dir=_rigs_dir(),
        profile_name=getattr(args, "profile", None),
        baud_override=getattr(args, "baud", None),
        address_override=getattr(args, "address", None),
        yes=args.yes,
        is_tty=is_tty,
        ask=lambda prompt: input(prompt),
        emit=print,
        prog=prog,
    )
    if isinstance(selection, int):
        return selection
    config = selection.config

    async def factory() -> Exchange:
        if config.protocol_type == "yaesu_cat":
            reader, writer = await open_serial_port(
                selection.device, baudrate=selection.baud
            )
            return await _yaesu_exchange(reader, writer)
        addr = selection.address if selection.address is not None else config.civ_addr
        reader, writer = await open_serial_port(
            selection.device, baudrate=selection.baud
        )
        return await _civ_exchange(reader, writer, addr)

    return await execute(
        config,
        selection.profile_toml_bytes,
        device_label=f"{selection.device} @ {selection.baud}",
        baud=selection.baud,
        out=args.out,
        include_private=args.include_private,
        label=args.label,
        yes=args.yes,
        is_tty=is_tty,
        ask=lambda prompt: input(prompt),
        exchange_factory=factory,
        sibling_configs=list(rigs.values()),
        checks=checks,
    )
