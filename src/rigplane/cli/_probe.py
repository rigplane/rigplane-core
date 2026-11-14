"""``rigplane probe``: read-only radio probe wiring (MOR-4464 PR A).

Thin CLI over ``rigplane.probe``: argument parsing, prompts, and the USB
serial transport. Nothing here decides what is sent; the runner sends
only what ``readlist.build_read_list`` produces.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import re
import sys
import tomllib
from collections.abc import Awaitable, Callable, Mapping
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from rigplane.cli._models import match_rig
from rigplane.commands import CONTROLLER_ADDR, build_civ_frame, command_carries_sub
from rigplane.core.serial_open import open_serial_port
from rigplane.profiles.rig_loader import RigConfig
from rigplane.probe import render
from rigplane.probe.model import ProbeFile
from rigplane.probe.readlist import build_read_list
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


async def _yaesu_exchange(reader: Any, writer: Any) -> Exchange:
    async def exchange(request: bytes | None, timeout: float) -> bytes | None:
        if request:
            writer.write(request)
            await writer.drain()
        try:
            raw: bytes = await asyncio.wait_for(reader.readuntil(b";"), timeout)
        except (asyncio.TimeoutError, asyncio.IncompleteReadError):
            return None
        return raw

    return exchange


async def _civ_exchange(reader: Any, writer: Any, radio_addr: int) -> Exchange:
    async def exchange(request: bytes | None, timeout: float) -> bytes | None:
        sent: bytes | None = None
        if request:
            command, rest = request[0], request[1:]
            if command_carries_sub(command) and rest:
                sub, data = rest[0], rest[1:]
            else:
                sub, data = None, rest
            sent = build_civ_frame(
                radio_addr, CONTROLLER_ADDR, command, sub=sub, data=data or None
            )
            writer.write(sent)
            await writer.drain()
        try:
            while True:
                raw: bytes = await asyncio.wait_for(
                    reader.readuntil(b"\xfd"), timeout
                )
                if sent is not None and raw == sent:
                    continue  # USB echo of our own frame, not the answer
                return raw
        except (asyncio.TimeoutError, asyncio.IncompleteReadError):
            return None

    return exchange


async def _open_exchange(
    config: RigConfig, device: str, baud: int, radio_addr: int
) -> Exchange:
    """Open the USB serial port and build the protocol exchange."""
    reader, writer = await open_serial_port(device, baudrate=baud)
    if config.protocol_type == "yaesu_cat":
        return await _yaesu_exchange(reader, writer)
    return await _civ_exchange(reader, writer, radio_addr)


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
) -> int:
    """Run the probe end to end; return the process exit code.

    0 ok, 2 bad usage (unknown profile, unsupported protocol, refusing to
    save without ``--yes`` outside a TTY), 3 the port cannot be opened or
    nothing answered at all. The file is written only after the save
    answer, and never contains the port path, host, or user.
    """
    if config.protocol_type not in _SUPPORTED:
        emit(render.not_supported_message(config.protocol_type))
        return 2
    if not is_tty and not yes:
        emit("Refusing to save without --yes when stdin is not a terminal.")
        return 2

    items = build_read_list(config, include_private=include_private)
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
        )
    )
    if not yes and not _is_yes(ask("Save the file? [Y/n] ")):
        return 0
    saved_abs.write_text(content, encoding="utf-8")
    emit(render.closing_line())
    if not _any_answer(report.file):
        emit(render.no_answers_message())
        return 3
    return 0


def _any_answer(file: ProbeFile) -> bool:
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


def add_subparser(sub: Any) -> argparse.ArgumentParser:
    """Register the ``probe`` subparser."""
    p: argparse.ArgumentParser = sub.add_parser(
        "probe",
        help="Record how your radio answers, into one file you can send to us.",
        description=render.probe_help(),
    )
    p.add_argument("profile", help="Radio model from `rigplane models`")
    p.add_argument("--port", required=True, help="Serial device (e.g. /dev/ttyUSB0)")
    p.add_argument("--baud", type=int, default=None, help="Baud rate")
    p.add_argument(
        "--address",
        type=lambda value: int(value, 0),
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
        )
    )
