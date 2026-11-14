"""CLI wiring for ``rigplane probe`` (MOR-4464 PR A).

The command never touches hardware here: ``execute`` takes an exchange
factory and prompt doubles, so every path below runs without a radio.
"""

from __future__ import annotations

import argparse
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
    )
    assert code == 3
    assert json.loads(out.read_text(encoding="utf-8"))["schema"] == (
        "rigplane-probe-v1"
    )


async def test_execute_default_out_path_uses_profile_and_timestamp(
    tmp_path, monkeypatch
) -> None:
    monkeypatch.chdir(tmp_path)
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
    )
    assert code == 0
    matches = list(tmp_path.glob("ft991a-probe-*.json"))
    assert len(matches) == 1
