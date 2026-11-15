"""``rigplane probe``: read-only radio probe wiring (MOR-4464 PR A).

Thin CLI over ``rigplane.probe``: argument parsing, prompts, and the USB
serial transport. Nothing here decides what is sent; the runner sends
only what ``readlist.build_read_plan`` asks, and records the rest as
skipped with a reason.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import re
import sys
import tomllib
from collections.abc import Awaitable, Callable, Iterable, Iterator, Mapping
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Protocol, runtime_checkable

from rigplane.cli._models import match_rig
from rigplane.commands import CONTROLLER_ADDR, build_civ_frame, command_carries_sub
from rigplane.core.serial_open import open_serial_port
from rigplane.profiles.rig_loader import RigConfig
from rigplane.probe import render
from rigplane.probe.model import ProbeFile
from rigplane.probe.readlist import ReadRequest, build_read_plan
from rigplane.probe.runner import (
    CIV_TIMEOUT_S,
    CAT_TIMEOUT_S,
    Exchange,
    ProbeRun,
    run_probe,
)

__all__ = [
    "UnknownProfileError",
    "UnsupportedProtocolError",
    "add_subparser",
    "execute",
    "resolve_config",
    "run",
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


def _default_out_path(model: str) -> Path:
    stem = re.sub(r"[^a-z0-9]+", "", model.lower())
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    return Path.cwd() / f"{stem}-probe-{stamp}.json"


def _is_yes(answer: str) -> bool:
    return answer.strip().lower() in ("", "y", "yes")


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
) -> int:
    """Run the probe end to end; return the process exit code.

    0 ok, 2 bad usage (unknown profile, unsupported protocol, refusing to
    save without ``--yes`` outside a TTY), 3 the port cannot be opened or
    nothing answered at all. The file is written only after the save
    answer. The tool itself records no port path, host, or user — only
    what you type in ``--label``, plus the radio's answers.
    """
    if config.protocol_type not in _SUPPORTED:
        emit(render.not_supported_message(config.protocol_type))
        return 2
    if not is_tty and not yes:
        emit("Refusing to save without --yes when stdin is not a terminal.")
        return 2

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

    saved_path = Path(out) if out else _default_out_path(config.model)
    saved_abs = saved_path.resolve()
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
        )
    )
    if not yes and not _is_yes(ask("Save the file? [Y/n] ")):
        return 0
    try:
        saved_abs.write_text(content, encoding="utf-8")
    except OSError as exc:
        emit(f"Cannot save {saved_abs}: {exc.strerror or exc}")
        return 1
    emit(render.closing_line())
    if not _any_answer(report.file):
        emit(render.no_answers_message())
        return 3
    return 0


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


def add_subparser(sub: Any) -> argparse.ArgumentParser:
    """Register the ``probe`` subparser."""
    p: argparse.ArgumentParser = sub.add_parser(
        "probe",
        help="Record how your radio answers, into one file you can send to us.",
        description=render.probe_help(),
    )
    p.add_argument("profile", help="Radio model from `rigplane models`")
    p.add_argument("--port", required=True, help="Serial device (e.g. /dev/ttyUSB0)")
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
    return p


def run(args: argparse.Namespace) -> int:
    """Driver for ``rigplane probe``. Returns a process exit code."""
    from rigplane.cli import _rigs_dir
    from rigplane.rig_loader import RigLoadError, discover_available_rigs

    try:
        rigs = discover_available_rigs(_rigs_dir())
    except RigLoadError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1
    try:
        config = resolve_config(args.profile, rigs)
    except UnknownProfileError as error:
        print(f"Error: {error}")
        return 2
    except UnsupportedProtocolError as error:
        print(error)
        return 2
    baud = args.baud if args.baud is not None else config.default_baud
    device = args.port
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
        )
    )
