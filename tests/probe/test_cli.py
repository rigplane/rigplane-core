"""CLI wiring for ``rigplane probe`` (MOR-4464 PR A).

The command never touches hardware here: ``execute`` takes an exchange
factory and prompt doubles, so every path below runs without a radio.
"""

from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path
from typing import Awaitable, Callable

import pytest

from rigplane.cli._probe import (
    UnknownProfileError,
    UnsupportedProtocolError,
    execute,
    resolve_config,
    run,
)
from rigplane.probe import runner
from rigplane.probe.model import ProbeFile
from rigplane.profiles.rig_loader import discover_rigs

REPO_ROOT = Path(__file__).resolve().parents[2]
RIGS = discover_rigs(REPO_ROOT / "rigs")

GAP = 0.0
TIMEOUT = 2.0


def _stub_answers(
    seen: list[bytes],
    *,
    identity: bytes = b"ID0840;",
    silent: bool = False,
) -> Callable[[], Awaitable[runner.Exchange]]:
    async def factory() -> runner.Exchange:
        async def exchange(request: bytes | None, timeout: float) -> bytes | None:
            assert request is not None
            seen.append(request)
            if silent:
                return None
            if request == b"ID;":
                return identity
            return b"?;"

        return exchange

    return factory


def _args(**overrides) -> argparse.Namespace:
    params = {
        "profile": "FT-991A",
        "port": "socket://127.0.0.1:1",
        "baud": 38400,
        "address": None,
        "out": None,
        "include_private": False,
        "label": "",
        "yes": False,
    }
    params.update(overrides)
    return argparse.Namespace(**params)


def test_resolve_unknown_profile() -> None:
    with pytest.raises(UnknownProfileError) as exc_info:
        resolve_config("no-such-radio", RIGS)
    assert "no-such-radio" in str(exc_info.value)


def test_resolve_unsupported_protocol() -> None:
    with pytest.raises(UnsupportedProtocolError, match="not supported yet"):
        resolve_config("TX-500", RIGS)


def test_run_unknown_profile_is_exit_2(capsys) -> None:
    assert run(_args(profile="no-such-radio")) == 2
    assert "no-such-radio" in capsys.readouterr().out


async def test_execute_refuses_to_save_without_tty_or_yes(tmp_path) -> None:
    out = tmp_path / "probe.json"
    seen: list[bytes] = []

    async def factory() -> runner.Exchange:
        raise AssertionError("must not open the port")

    code = await execute(
        resolve_config("FT-991A", RIGS),
        (REPO_ROOT / "rigs" / "ft991a.toml").read_bytes(),
        device_label="COM5 @ 38400",
        baud=38400,
        out=str(out),
        include_private=False,
        label="",
        yes=False,
        is_tty=False,
        ask=lambda prompt: "y",
        exchange_factory=factory,
        gap_s=GAP,
        timeout_s=TIMEOUT,
    )
    assert code == 2
    assert not out.exists()
    assert seen == []


async def test_execute_start_decline_runs_nothing(tmp_path) -> None:
    out = tmp_path / "probe.json"

    async def factory() -> runner.Exchange:
        raise AssertionError("must not open the port")

    code = await execute(
        resolve_config("FT-991A", RIGS),
        (REPO_ROOT / "rigs" / "ft991a.toml").read_bytes(),
        device_label="COM5 @ 38400",
        baud=38400,
        out=str(out),
        include_private=False,
        label="",
        yes=False,
        is_tty=True,
        ask=lambda prompt: "n",
        exchange_factory=factory,
        gap_s=GAP,
        timeout_s=TIMEOUT,
    )
    assert code == 0
    assert not out.exists()


async def test_execute_save_decline_writes_nothing(tmp_path) -> None:
    out = tmp_path / "probe.json"
    seen: list[bytes] = []
    answers = iter(["y", "n"])
    code = await execute(
        resolve_config("FT-991A", RIGS),
        (REPO_ROOT / "rigs" / "ft991a.toml").read_bytes(),
        device_label="COM5 @ 38400",
        baud=38400,
        out=str(out),
        include_private=False,
        label="",
        yes=False,
        is_tty=True,
        ask=lambda prompt: next(answers),
        exchange_factory=_stub_answers(seen),
        gap_s=GAP,
        timeout_s=TIMEOUT,
    )
    assert code == 0
    assert seen
    assert not out.exists()


async def test_execute_yes_writes_parseable_file(tmp_path) -> None:
    out = tmp_path / "probe.json"
    seen: list[bytes] = []
    code = await execute(
        resolve_config("FT-991A", RIGS),
        (REPO_ROOT / "rigs" / "ft991a.toml").read_bytes(),
        device_label="COM5 @ 38400",
        baud=38400,
        out=str(out),
        include_private=False,
        label="bench",
        yes=True,
        is_tty=False,
        ask=lambda prompt: (_ for _ in ()).throw(AssertionError("no prompt")),
        exchange_factory=_stub_answers(seen),
        gap_s=GAP,
        timeout_s=TIMEOUT,
    )
    assert code == 0
    assert seen
    document = ProbeFile.from_json(out.read_text(encoding="utf-8"))
    assert document.schema == "rigplane-probe-v1"
    assert document.label == "bench"
    assert document.private_reads_included is False


async def test_execute_g7250_file_records_skipped_and_summary(tmp_path) -> None:
    from rigplane.commands import CONTROLLER_ADDR, build_civ_frame

    config = resolve_config("G7250", RIGS)
    nak = build_civ_frame(config.civ_addr, CONTROLLER_ADDR, 0xFA)
    seen: list[bytes] = []

    async def factory() -> runner.Exchange:
        async def exchange(request: bytes | None, timeout: float) -> bytes | None:
            assert request is not None
            seen.append(request)
            return nak

        return exchange

    lines: list[str] = []
    out = tmp_path / "probe.json"
    code = await execute(
        config,
        (REPO_ROOT / "rigs" / "g7250.toml").read_bytes(),
        device_label="COM5 @ 115200",
        baud=115200,
        out=str(out),
        include_private=False,
        label="",
        yes=True,
        is_tty=False,
        ask=lambda prompt: (_ for _ in ()).throw(AssertionError("no prompt")),
        exchange_factory=factory,
        gap_s=GAP,
        timeout_s=TIMEOUT,
        emit=lines.append,
        sibling_configs=list(RIGS.values()),
    )
    assert code == 0
    document = ProbeFile.from_json(out.read_text(encoding="utf-8"))
    assert document.skipped
    assert {entry.name for entry in document.skipped} >= {
        "get_memory_contents",
        "get_transceiver_status",
    }
    assert any("were not sent" in line for line in lines)


async def test_execute_g7250_reports_matched_identity(tmp_path) -> None:
    # MOR-4464 finding 4: the file reports the answer that established
    # the match (parsed 7250), and the summary shows that model code.
    from rigplane.commands import CONTROLLER_ADDR, build_civ_frame

    config = resolve_config("G7250", RIGS)
    addr = config.civ_addr
    addr_echo = build_civ_frame(
        CONTROLLER_ADDR, addr, 0x19, sub=0x00, data=bytes((addr,))
    )
    model = build_civ_frame(CONTROLLER_ADDR, addr, 0x1D, data=bytes.fromhex("197250"))
    queues: dict[bytes, list[bytes | None]] = {
        b"\x19\x00": [addr_echo],
        b"\x1d\x19": [model],
    }
    pending: list[bytes | None] = []

    async def factory() -> runner.Exchange:
        async def exchange(request: bytes | None, timeout: float) -> bytes | None:
            nonlocal pending
            if request is not None:
                pending = list(queues.get(request, []))
                if pending:
                    return pending.pop(0)
                return build_civ_frame(addr, CONTROLLER_ADDR, 0xFA)
            if pending:
                return pending.pop(0)
            return None

        return exchange

    lines: list[str] = []
    out = tmp_path / "probe.json"
    code = await execute(
        config,
        (REPO_ROOT / "rigs" / "g7250.toml").read_bytes(),
        device_label="COM5 @ 115200",
        baud=115200,
        out=str(out),
        include_private=False,
        label="",
        yes=True,
        is_tty=False,
        ask=lambda prompt: (_ for _ in ()).throw(AssertionError("no prompt")),
        exchange_factory=factory,
        gap_s=GAP,
        timeout_s=TIMEOUT,
        emit=lines.append,
        sibling_configs=list(RIGS.values()),
    )
    assert code == 0
    document = ProbeFile.from_json(out.read_text(encoding="utf-8"))
    assert document.radio.identity_matches_profile is True
    assert document.radio.identity_reported.parsed == "7250"
    assert document.radio.identity_reported.raw == model.hex()
    assert any("model code 7250" in line for line in lines)


async def test_execute_cannot_open_is_exit_3(capsys, tmp_path) -> None:
    async def factory() -> runner.Exchange:
        raise OSError("connection refused")

    code = await execute(
        resolve_config("FT-991A", RIGS),
        (REPO_ROOT / "rigs" / "ft991a.toml").read_bytes(),
        device_label="socket://127.0.0.1:1",
        baud=38400,
        out=str(tmp_path / "probe.json"),
        include_private=False,
        label="",
        yes=True,
        is_tty=False,
        ask=lambda prompt: "y",
        exchange_factory=factory,
        gap_s=GAP,
        timeout_s=TIMEOUT,
    )
    assert code == 3
    assert "Cannot open" in capsys.readouterr().out


async def test_execute_no_answers_is_exit_3_but_file_saved(tmp_path) -> None:
    out = tmp_path / "probe.json"
    lines: list[str] = []
    code = await execute(
        resolve_config("FT-991A", RIGS),
        (REPO_ROOT / "rigs" / "ft991a.toml").read_bytes(),
        device_label="COM5 @ 38400",
        baud=38400,
        out=str(out),
        include_private=False,
        label="",
        yes=True,
        is_tty=False,
        ask=lambda prompt: "y",
        exchange_factory=_stub_answers([], silent=True),
        gap_s=GAP,
        timeout_s=TIMEOUT,
        emit=lines.append,
    )
    assert code == 3
    assert json.loads(out.read_text(encoding="utf-8"))["schema"] == (
        "rigplane-probe-v1"
    )
    assert any("stopped early" in line for line in lines)


async def test_execute_identifying_label_is_saved_verbatim(tmp_path) -> None:
    # MOR-4464 finding 2: the label is saved exactly as typed, so the
    # summary must not promise the file holds no name or address.
    label = "Alice Example 192.168.1.7 MY-COMPUTER"
    out = tmp_path / "probe.json"
    seen: list[bytes] = []
    lines: list[str] = []
    code = await execute(
        resolve_config("FT-991A", RIGS),
        (REPO_ROOT / "rigs" / "ft991a.toml").read_bytes(),
        device_label="COM5 @ 38400",
        baud=38400,
        out=str(out),
        include_private=False,
        label=label,
        yes=True,
        is_tty=False,
        ask=lambda prompt: (_ for _ in ()).throw(AssertionError("no prompt")),
        exchange_factory=_stub_answers(seen),
        gap_s=GAP,
        timeout_s=TIMEOUT,
        emit=lines.append,
    )
    assert code == 0
    text = out.read_text(encoding="utf-8")
    assert label in text
    assert ProbeFile.from_json(text).label == label
    summary = "\n".join(lines)
    assert "does NOT contain: your name" not in summary
    assert "saved exactly as you typed it" in summary


async def test_execute_default_out_path_uses_documents_and_date(
    tmp_path, monkeypatch
) -> None:
    from datetime import datetime, timezone

    monkeypatch.chdir(tmp_path)
    docs = tmp_path / "Documents"
    docs.mkdir()
    code = await execute(
        resolve_config("FT-991A", RIGS),
        (REPO_ROOT / "rigs" / "ft991a.toml").read_bytes(),
        device_label="COM5 @ 38400",
        baud=38400,
        out=None,
        include_private=False,
        label="",
        yes=True,
        is_tty=False,
        ask=lambda prompt: "y",
        exchange_factory=_stub_answers([]),
        gap_s=GAP,
        timeout_s=TIMEOUT,
        documents_dir=lambda: docs,
    )
    assert code == 0
    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    matches = list(docs.glob(f"FT-991A-radio-probe-{stamp}.json"))
    assert len(matches) == 1
    assert not list(tmp_path.glob("*.json"))


# --- P2 batch ---


def _parse_probe(argv: list[str]) -> argparse.Namespace:
    from rigplane.cli import _build_parser

    return _build_parser().parse_args(["probe", *argv])


def test_parser_rejects_address_above_byte_range() -> None:
    with pytest.raises(SystemExit) as exc_info:
        _parse_probe(["FTX-1", "--port", "x", "--address", "0x100"])
    assert exc_info.value.code == 2


def test_parser_rejects_non_numeric_address() -> None:
    with pytest.raises(SystemExit) as exc_info:
        _parse_probe(["FTX-1", "--port", "x", "--address", "nope"])
    assert exc_info.value.code == 2


def test_parser_rejects_negative_baud() -> None:
    with pytest.raises(SystemExit) as exc_info:
        _parse_probe(["FTX-1", "--port", "x", "--baud", "-1"])
    assert exc_info.value.code == 2


def test_parser_rejects_zero_baud() -> None:
    with pytest.raises(SystemExit) as exc_info:
        _parse_probe(["FTX-1", "--port", "x", "--baud", "0"])
    assert exc_info.value.code == 2


def test_parser_accepts_valid_address_and_baud() -> None:
    args = _parse_probe(
        ["IC-7300", "--port", "x", "--address", "0x94", "--baud", "115200"]
    )
    assert args.address == 0x94
    assert args.baud == 115200


async def test_execute_save_failure_reports_path_without_traceback(
    tmp_path,
) -> None:
    # P2: an unwritable output path is a clean nonzero exit naming the
    # path, not a traceback and no "Done.".
    out = tmp_path / "missing-dir" / "probe.json"
    lines: list[str] = []
    code = await execute(
        resolve_config("FT-991A", RIGS),
        (REPO_ROOT / "rigs" / "ft991a.toml").read_bytes(),
        device_label="COM5 @ 38400",
        baud=38400,
        out=str(out),
        include_private=False,
        label="",
        yes=True,
        is_tty=False,
        ask=lambda prompt: (_ for _ in ()).throw(AssertionError("no prompt")),
        exchange_factory=_stub_answers([]),
        gap_s=GAP,
        timeout_s=TIMEOUT,
        emit=lines.append,
    )
    assert code != 0
    assert "Done." not in lines
    assert str(out) in "\n".join(lines)


class _ClosableStubExchange:
    """Exchange double that records closure (P2: the runner owns the port)."""

    def __init__(
        self,
        *,
        script: dict[bytes, bytes] | None = None,
        block: "asyncio.Event | None" = None,
        break_now: bool = False,
    ) -> None:
        self.script = script if script is not None else {}
        self.seen: list[bytes] = []
        self.block: asyncio.Event | None = block
        self.break_now = break_now
        self.closed = False

    async def __call__(self, request: bytes | None, timeout: float) -> bytes | None:
        if request is not None:
            self.seen.append(request)
        if self.break_now:
            raise OSError("device is gone")
        if self.block is not None:
            await self.block.wait()
            return None
        assert request is not None
        if request == b"ID;":
            return b"ID0840;"
        return self.script.get(request, b"?;")

    def close(self) -> None:
        self.closed = True


async def _run_with_closable(
    stub: _ClosableStubExchange, tmp_path, **overrides
) -> tuple[int, list[str]]:
    import asyncio

    params = {
        "config": resolve_config("FT-991A", RIGS),
        "profile_toml_bytes": (REPO_ROOT / "rigs" / "ft991a.toml").read_bytes(),
        "device_label": "COM5 @ 38400",
        "baud": 38400,
        "out": str(tmp_path / "probe.json"),
        "include_private": False,
        "label": "",
        "yes": True,
        "is_tty": False,
        "ask": lambda prompt: (_ for _ in ()).throw(AssertionError("no prompt")),
        "gap_s": GAP,
        "timeout_s": TIMEOUT,
    }
    params.update(overrides)

    async def factory() -> runner.Exchange:
        await asyncio.sleep(0)
        return stub  # type: ignore[return-value]

    params["exchange_factory"] = factory
    lines: list[str] = []
    code = await execute(emit=lines.append, **params)  # type: ignore[arg-type]
    return code, lines


async def test_execute_closes_writer_on_completion(tmp_path) -> None:
    stub = _ClosableStubExchange()
    code, _lines = await _run_with_closable(stub, tmp_path)
    assert code == 0
    assert (tmp_path / "probe.json").exists()
    assert stub.closed is True


async def test_execute_closes_writer_on_save_decline(tmp_path) -> None:
    stub = _ClosableStubExchange()
    answers = iter(["y", "n"])
    code, _lines = await _run_with_closable(
        stub,
        tmp_path,
        yes=False,
        is_tty=True,
        ask=lambda prompt: next(answers),
    )
    assert code == 0
    assert not (tmp_path / "probe.json").exists()
    assert stub.closed is True


async def test_execute_closes_writer_on_transport_error(tmp_path) -> None:
    stub = _ClosableStubExchange(break_now=True)
    code, _lines = await _run_with_closable(stub, tmp_path)
    assert code != 0
    assert stub.closed is True


async def test_execute_closes_writer_on_cancellation(tmp_path) -> None:
    import asyncio

    block = asyncio.Event()
    stub = _ClosableStubExchange(block=block)
    task = asyncio.ensure_future(
        _run_with_closable(stub, tmp_path, out=str(tmp_path / "cancel.json"))
    )
    for _ in range(1000):
        if stub.seen:
            break
        await asyncio.sleep(0)
    assert stub.seen, "the run never reached the exchange"
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert stub.closed is True


async def test_execute_default_save_uses_exclusive_create_on_late_collision(
    tmp_path,
) -> None:
    # P1-1: a capture that appears between filename selection and the
    # save answer must not be truncated. The late file survives, the new
    # capture takes the next suffix, and the closing line names the real
    # path (the pre-save summary may still show the candidate).
    from datetime import datetime, timezone

    docs = tmp_path / "Documents"
    docs.mkdir()
    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    base_name = f"FT-991A-radio-probe-{stamp}.json"

    def ask(prompt: str) -> str:
        if prompt.startswith("Save the file?"):
            # Another run's capture lands after the pre-save summary
            # showed the base name but before this run answers.
            (docs / base_name).write_text('{"other": "run"}', encoding="utf-8")
            return "y"
        return "y"

    lines: list[str] = []
    code = await execute(
        resolve_config("FT-991A", RIGS),
        (REPO_ROOT / "rigs" / "ft991a.toml").read_bytes(),
        device_label="COM5 @ 38400",
        baud=38400,
        out=None,
        include_private=False,
        label="",
        yes=False,
        is_tty=True,
        ask=ask,
        exchange_factory=_stub_answers([]),
        gap_s=GAP,
        timeout_s=TIMEOUT,
        emit=lines.append,
        documents_dir=lambda: docs,
        open_folder=lambda path: None,
    )
    assert code == 0
    first = docs / base_name
    assert first.read_text(encoding="utf-8") == '{"other": "run"}'
    second = docs / f"FT-991A-radio-probe-{stamp}-2.json"
    document = ProbeFile.from_json(second.read_text(encoding="utf-8"))
    assert document.schema == "rigplane-probe-v1"
    closing = [line for line in lines if line.startswith("Saved to")]
    assert len(closing) == 1
    assert str(second.resolve()) in closing[0]
    assert str(first.resolve()) not in closing[0]


async def test_execute_folder_opener_launches_without_waiting(
    tmp_path, monkeypatch
) -> None:
    # P2-3: the folder opener must not wait on the desktop process. A
    # persistent opener cannot hold the run, and the writer still closes.
    import shutil
    import subprocess
    import sys

    monkeypatch.setattr(sys, "platform", "linux")
    monkeypatch.setattr(shutil, "which", lambda name: "/usr/bin/xdg-open")
    launched: list[list[str]] = []

    def fake_popen(argv: list[str], **kwargs: object) -> object:
        launched.append(list(argv))
        return object()

    def no_wait(*args: object, **kwargs: object) -> object:
        raise AssertionError("the folder opener must not wait")

    monkeypatch.setattr(subprocess, "run", no_wait)
    monkeypatch.setattr(subprocess, "Popen", fake_popen)
    stub = _ClosableStubExchange()
    answers = iter(["y", "y"])

    async def factory() -> runner.Exchange:
        return stub  # type: ignore[return-value]

    lines: list[str] = []
    code = await execute(
        resolve_config("FT-991A", RIGS),
        (REPO_ROOT / "rigs" / "ft991a.toml").read_bytes(),
        device_label="COM5 @ 38400",
        baud=38400,
        out=str(tmp_path / "probe.json"),
        include_private=False,
        label="",
        yes=False,
        is_tty=True,
        ask=lambda prompt: next(answers),
        exchange_factory=factory,
        gap_s=GAP,
        timeout_s=TIMEOUT,
        emit=lines.append,
    )
    assert code == 0
    assert launched == [["xdg-open", str(tmp_path)]]
    assert stub.closed is True
