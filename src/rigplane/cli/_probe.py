"""Read-only radio probe wiring (MOR-4464 PR A).

Thin CLI over ``rigplane.probe``: argument parsing, prompts, and the USB
serial transport. Nothing here decides what is sent; the runner sends
only what ``readlist.build_read_plan`` asks, and records the rest as
skipped with a reason.
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
import threading
import tomllib
from collections.abc import Awaitable, Callable, Iterable, Iterator, Mapping
from contextlib import contextmanager
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Protocol, runtime_checkable

import platformdirs

from rigplane.backends.discovery import RadioDiscoveryResult, discover_serial_radios
from rigplane.cli._models import match_rig
from rigplane.commands import CONTROLLER_ADDR, build_civ_frame, command_carries_sub
from rigplane.core.serial_open import open_serial_port
from rigplane.profiles.rig_loader import RigConfig
from rigplane.probe import render
from rigplane.probe.checks import run_checks
from rigplane.probe.journal import find_pending_journal, read_journal
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
    and its full path is already printed.
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


def _is_yes(answer: str) -> bool:
    return answer.strip().lower() in ("", "y", "yes")


async def _ask_async(
    ask: Callable[[str], str], prompt: str
) -> str | None:
    """Ask on a daemon thread so a 60 s timeout never holds the process.

    ``input()`` blocks until the person answers; after the engine times
    the question out as skipped the thread stays blocked, and only a
    daemon thread lets the run still exit.
    """
    loop = asyncio.get_running_loop()
    future: asyncio.Future[str | None] = loop.create_future()

    def target() -> None:
        try:
            answer = ask(prompt)
        except (EOFError, OSError):
            loop.call_soon_threadsafe(future.set_result, None)
        except BaseException as error:  # noqa: BLE001 -- ask runs user code
            loop.call_soon_threadsafe(future.set_exception, error)
        else:
            loop.call_soon_threadsafe(future.set_result, answer)

    thread = threading.Thread(target=target, daemon=True)
    thread.start()
    return await future


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
) -> int:
    """Run the probe end to end; return the process exit code.

    0 ok, 2 bad usage (unknown profile, unsupported protocol, refusing to
    save without ``--yes`` outside a TTY, ``--checks`` without a
    terminal), 3 the port cannot be opened or nothing answered at all, 4
    a write check's restore did not verify. The file is written only
    after the save answer. The tool itself records no port path, host,
    or user - only what you type in ``--label``, plus the radio's
    answers.
    """
    if config.protocol_type not in _SUPPORTED:
        emit(render.not_supported_message(config.protocol_type))
        return 2
    if not is_tty and not yes:
        emit("Refusing to save without --yes when stdin is not a terminal.")
        return 2
    if checks and not is_tty:
        emit(render.checks_no_tty_message())
        return 2
    if checks:
        pending = find_pending_journal(
            out, _resolve_save_dir(documents_dir) if out is None else Path(".")
        )
        if pending is not None:
            try:
                journal = read_journal(pending)
                items = [
                    f"{render.human_read_name(name)} {value}"
                    for name, value in journal.originals.items()
                ]
            except ValueError:
                emit(render.journal_unreadable_message(str(pending)))
            else:
                emit(render.journal_pending_message(items, str(pending)))

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
            checks=checks,
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
    checks: bool = False,
) -> int:
    """The probe pass and save; the caller owns closing the exchange."""

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
    )
    if report.aborted is not None:
        emit(report.aborted)

    if out is not None:
        saved_abs = Path(out).resolve()
        exclusive = False
    else:
        # The default name is only a candidate until the save answer;
        # the file itself is created exclusively at save time below.
        saved_abs = _default_out_path(config.model, documents_dir)
        exclusive = True
    check_records = ()
    checks_exit = 0
    checks_line: str | None = None
    if checks and report.termination.status == "completed":
        if not config.probe_checks:
            emit(render.checks_no_checks_message(config.model))
        else:
            emit(render.checks_intro(len(config.probe_checks)))
            # --yes never covers checks: every check asks, default no.
            if _is_yes(ask(render.checks_consent_prompt())):
                journal_path = Path(f"{saved_abs}.checks-journal.json")

                async def ask_one(prompt: str) -> str | None:
                    return await _ask_async(ask, prompt)

                result = await run_checks(
                    config=config,
                    exchange=exchange,
                    gap_s=gap_s,
                    timeout_s=window,
                    journal_path=journal_path,
                    consent=ask,
                    ask=ask_one,
                    emit=emit,
                )
                check_records = result.records
                checks_exit = result.exit_code
                if check_records:
                    changed: list[str] = []
                    for record in check_records:
                        for name in record.originals:
                            human = render.human_read_name(name)
                            if human not in changed:
                                changed.append(human)
                    checks_line = render.checks_summary_line(
                        len(check_records),
                        sum(1 for record in check_records if record.verified),
                        len(config.probe_checks) - len(check_records),
                        changed,
                    )
    file = report.file
    if check_records:
        file = replace(file, mode="read+checks", checks=check_records)
    content = file.to_json()
    identity = file.radio.identity_reported
    settings = [
        f"{name} {value}" for name, value in file.snapshot.before.items() if value is not None
    ]
    skipped_counts: dict[str, int] = {}
    for entry in file.skipped:
        skipped_counts[entry.reason] = skipped_counts.get(entry.reason, 0) + 1
    absent_answered = [
        render.AbsentAnswer(
            name=row.name,
            request_hex=row.request_hex,
            reply_hex=row.reply_hex,
        )
        for row in file.commands
        if row.expected == "absent"
        and row.outcome == "data"
        and not row.name.startswith("unknown:")
    ]
    present_missed = [
        row.name
        for row in file.commands
        if row.expected == "present" and row.outcome in ("refused", "silent")
    ]
    emit(
        render.pre_save_summary(
            path=str(saved_abs),
            size_bytes=len(content.encode("utf-8")),
            n_commands=len(file.commands),
            identity_text=(
                f"model code {identity.parsed}" if identity.parsed else "unknown model"
            ),
            settings_lines=settings,
            private_reads_included=include_private,
            skipped_counts=skipped_counts,
            absent_answered=absent_answered,
            present_missed=present_missed,
            termination=file.termination,
            checks_line=checks_line,
        )
    )
    if not yes and not _is_yes(ask("Save the file? [Y/n] ")):
        return 0
    try:
        actual = _write_capture(saved_abs, content, exclusive=exclusive)
    except OSError as exc:
        emit(f"Cannot save {saved_abs}: {exc.strerror or exc}")
        return 1
    emit(render.closing_line(str(actual)))
    if is_tty and not yes:
        opener = open_folder if open_folder is not None else open_result_folder
        emit(render.opening_folder_line(str(actual.parent)))
        try:
            opener(actual.parent)
        except Exception:
            pass  # an opening failure is never an error and is silent
    if not _any_answer(file):
        emit(render.no_answers_message())
        return 3
    return checks_exit


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
        help="Answer both prompts with yes (needed without a TTY)",
    )
    p.add_argument(
        "--checks",
        action="store_true",
        help="After the read-only pass, run the optional write checks "
        "(interactive only: each check asks first)",
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
    """Driver for the radio probe. Returns a process exit code."""
    from rigplane.cli import _rigs_dir
    from rigplane.rig_loader import RigLoadError, discover_available_rigs

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
        checks=getattr(args, "checks", False),
    )
