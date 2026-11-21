"""Zero-argument ``rigplane probe`` (MOR-4464 PR A2): unit tests.

Render-level pins for every new customer-visible string, the Documents
save location with collision/fallback, and the interactive folder
opener. Discovery selection through the real parser + dispatch lives in
``test_zero_arg_seam.py``; the serial boundary fakes stay there.
"""

from __future__ import annotations

import asyncio
import io
import re
import sys
from pathlib import Path

import pytest

from rigplane.backends.discovery import RadioDiscoveryResult
from rigplane.cli import _probe as probe_cli
from rigplane.cli._probe import execute, resolve_config
from rigplane.probe import render
from rigplane.probe.model import ProbeFile
from rigplane.profiles.rig_loader import discover_rigs

REPO_ROOT = Path(__file__).resolve().parents[2]
RIGS = discover_rigs(REPO_ROOT / "rigs")

GAP = 0.0
TIMEOUT = 2.0


def _stub_answers(seen: list[bytes], *, identity: bytes = b"ID0840;"):
    async def factory():
        async def exchange(request: bytes | None, timeout: float) -> bytes | None:
            assert request is not None
            seen.append(request)
            if request == b"ID;":
                return identity
            return b"?;"

        return exchange

    return factory


def _civ_result(
    model: str = "IC-7300",
    profile_id: str = "icom_ic7300",
    port: str = "COM5",
    baudrate: int = 115200,
    address: int = 0x94,
) -> RadioDiscoveryResult:
    return RadioDiscoveryResult(
        port=port,
        protocol="civ",
        model=model,
        profile_id=profile_id,
        baudrate=baudrate,
        address=address,
    )


def _cat_result(
    model: str = "FTX-1",
    profile_id: str = "yaesu_ftx1",
    port: str = "COM6",
    baudrate: int = 38400,
    address: str = "0840",
) -> RadioDiscoveryResult:
    return RadioDiscoveryResult(
        port=port,
        protocol="yaesu_cat",
        model=model,
        profile_id=profile_id,
        baudrate=baudrate,
        address=address,
    )


# --- Plain-words skip reasons (exact strings) ---


def test_skip_reason_unsafe_family() -> None:
    assert (
        render.skip_reason_text("unsafe_family")
        == "never asked: asking could change something on the radio"
    )


def test_skip_reason_response_code() -> None:
    assert (
        render.skip_reason_text("response_code")
        == "not a question: this entry is an answer the radio gives"
    )


def test_skip_reason_private() -> None:
    assert (
        render.skip_reason_text("private")
        == "stored memory, only read if you ask for it"
    )


def test_skip_reason_no_unambiguous_bytes() -> None:
    assert (
        render.skip_reason_text("no_unambiguous_bytes")
        == "no safe way to ask it for this radio"
    )


def test_skip_reason_run_aborted() -> None:
    assert (
        render.skip_reason_text("run_aborted") == "the run stopped before this question"
    )


def test_skipped_line_uses_plain_sentences() -> None:
    assert render.skipped_line({"unsafe_family": 3, "private": 1}) == (
        "  - 4 reads were not sent (3 never asked: asking could change "
        "something on the radio; 1 stored memory, only read if you ask for it); "
        "each is listed under 'skipped' in the file with why\n"
    )


# --- Human read names (exact strings) ---


def test_human_read_name_strips_get_and_underscores() -> None:
    assert render.human_read_name("get_auto_notch") == "auto notch"
    assert render.human_read_name("get_data_mode") == "data mode"
    assert render.human_read_name("get_freq") == "freq"


def test_absent_answered_line_uses_human_name() -> None:
    assert render.absent_answered_line(
        [
            render.AbsentAnswer(
                name="get_auto_notch",
                request_hex="1601",
                reply_hex="fefee01a160102fd",
            )
        ]
    ) == (
        "  - notable: the radio answered 1 read the profile calls absent: "
        "auto notch (02)\n"
    )


def test_present_missed_line_uses_human_name() -> None:
    assert render.present_missed_line(["get_auto_notch"]) == (
        "  - notable: the radio did not answer 1 read the profile declares "
        "present: auto notch\n"
    )


# --- ASCII-only screen text ---


def test_start_screen_has_no_em_dash() -> None:
    text = render.start_screen(
        profile_name="FT-991A", port_label="COM5 @ 38400", n_questions=140
    )
    assert "\u2014" not in text
    assert text.startswith("RigPlane radio probe - READ ONLY\n")


def _every_customer_string() -> list[str]:
    return [
        render.probe_help(),
        render.start_screen(
            profile_name="FT-991A", port_label="COM5 @ 38400", n_questions=140
        ),
        render.progress_line(20, 140),
        render.silent_stop_message(),
        render.pre_save_summary(
            path="/tmp/x.json",
            size_bytes=38912,
            n_commands=142,
            identity_text="model code 0670",
            settings_lines=["frequency 14.074 MHz", "mode USB"],
            private_reads_included=False,
            skipped_counts={"unsafe_family": 1},
            absent_answered=[
                render.AbsentAnswer(
                    name="get_data_mode",
                    request_hex="1a06",
                    reply_hex="fefee01a1a0601fd",
                )
            ],
            present_missed=["get_freq"],
        ),
        render.closing_line("/tmp/x.json"),
        render.not_supported_message("kenwood_cat"),
        render.cannot_open_message("COM5"),
        render.no_answers_message(),
        render.discovery_announcement(),
        render.found_line("FT-991A", "COM5"),
        render.confirm_radio_prompt(),
        render.radio_choice_list([("FT-991A", "COM5"), ("IC-7300", "COM6")]),
        render.radio_choice_prompt(2),
        render.bad_choice_message(),
        render.no_radio_found_message(profile=None),
        render.no_radio_found_message(profile="FT-991A"),
        render.port_without_profile_message(),
        render.single_no_tty_message("FT-991A", "COM5"),
        render.several_no_tty_message(),
        render.no_profile_for_radio_message("Yaesu (1234)"),
        render.opening_folder_line("/tmp/docs"),
    ]


def test_every_customer_visible_string_is_ascii() -> None:
    import argparse

    from rigplane.cli import _build_parser

    parser = _build_parser()
    sub = next(
        action
        for action in parser._actions
        if isinstance(action, argparse._SubParsersAction)
    )
    help_text: list[str] = [sub.choices["probe"].format_help()]
    for text in _every_customer_string() + help_text:
        assert text.isascii(), repr(text)


def test_discovery_announcement_mentions_read_only_identity() -> None:
    text = render.discovery_announcement()
    assert "read-only identity question" in text
    assert text.isascii()


def test_found_line_format() -> None:
    assert render.found_line("FT-991A", "COM5") == "Found: FT-991A on COM5"
    assert render.confirm_radio_prompt() == "Is this your radio? [Y/n] "


def test_choice_list_and_prompt() -> None:
    assert render.radio_choice_list([("FT-991A", "COM5"), ("IC-7300", "COM6")]) == (
        "Found 2 radios:\n  1. FT-991A on COM5\n  2. IC-7300 on COM6"
    )
    assert render.radio_choice_prompt(2) == (
        "Type the number of your radio (1-2), or press Enter to quit: "
    )
    assert render.bad_choice_message() == "That is not one of the numbers above."


def test_no_radio_found_checklist() -> None:
    text = render.no_radio_found_message(profile=None)
    assert text.startswith("No radio found.")
    for line in (
        "the radio is on",
        "the USB cable is firmly connected",
        "the USB driver is installed",
        "RigPlane Pro is closed, if you use it",
        "other programs that use the radio are closed",
        "rigplane probe <model> --port <device>",
    ):
        assert line in text


def test_no_radio_found_names_profile() -> None:
    text = render.no_radio_found_message(profile="FT-991A")
    assert text.startswith("No FT-991A found.")
    assert "rigplane probe FT-991A --port <device>" in text


def test_port_without_profile_message() -> None:
    text = render.port_without_profile_message()
    assert "name your radio model too" in text
    assert "rigplane probe" in text
    assert "--port" in text


def test_closing_line_prints_full_path() -> None:
    text = render.closing_line("/home/user/Documents/FT-991A-radio-probe.json")
    assert "/home/user/Documents/FT-991A-radio-probe.json" in text
    assert "Nothing was uploaded" in text


# --- Documents save location ---


async def _execute_default_out(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    *,
    documents: Path | None,
    yes: bool = True,
    is_tty: bool = False,
) -> tuple[int, list[str]]:
    monkeypatch.chdir(tmp_path)
    docs = tmp_path / "Documents"
    docs.mkdir(exist_ok=True)
    lines: list[str] = []
    code = await execute(
        resolve_config("FT-991A", RIGS),
        (REPO_ROOT / "rigs" / "ft991a.toml").read_bytes(),
        device_label="COM5 @ 38400",
        baud=38400,
        out=None,
        include_private=False,
        label="",
        yes=yes,
        is_tty=is_tty,
        ask=lambda prompt: "y",
        exchange_factory=_stub_answers([]),
        gap_s=GAP,
        timeout_s=TIMEOUT,
        emit=lines.append,
        documents_dir=(lambda: docs) if documents is None else (lambda: documents),
    )
    return code, lines


def test_default_save_goes_to_documents_with_dated_name(tmp_path, monkeypatch) -> None:
    code, lines = asyncio.run(
        _execute_default_out(tmp_path, monkeypatch, documents=None)
    )
    assert code == 0
    from datetime import datetime, timezone

    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    matches = list((tmp_path / "Documents").glob(f"FT-991A-radio-probe-{stamp}.json"))
    assert len(matches) == 1
    assert not list(tmp_path.glob("*.json"))
    summary = "\n".join(lines)
    assert str(matches[0].resolve()) in summary


def test_default_save_never_overwrites_adds_suffix(tmp_path, monkeypatch) -> None:
    from datetime import datetime, timezone

    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    docs = tmp_path / "Documents"
    docs.mkdir(exist_ok=True)
    first = docs / f"FT-991A-radio-probe-{stamp}.json"
    first.write_text("{}", encoding="utf-8")
    code, _ = asyncio.run(_execute_default_out(tmp_path, monkeypatch, documents=None))
    assert code == 0
    assert first.read_text(encoding="utf-8") == "{}"
    second = docs / f"FT-991A-radio-probe-{stamp}-2.json"
    assert second.exists()
    document = ProbeFile.from_json(second.read_text(encoding="utf-8"))
    assert document.schema == "rigplane-probe-v1"


def test_default_save_continues_when_documents_resolver_fails(
    tmp_path, monkeypatch
) -> None:
    # P2-2: a failing Documents resolver must not fail the run while
    # home is usable; each location is tried only when reached.
    monkeypatch.chdir(tmp_path)
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: home))

    def no_documents() -> Path:
        raise OSError("Documents unavailable")

    lines: list[str] = []
    code = asyncio.run(
        execute(
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
            emit=lines.append,
            documents_dir=no_documents,
        )
    )
    assert code == 0
    assert list(home.glob("FT-991A-radio-probe-*.json"))


def test_default_save_uses_documents_when_cwd_is_gone(tmp_path, monkeypatch) -> None:
    # P2-2: a removed current directory must not fail the run while
    # Documents is fine.
    docs = tmp_path / "Documents"
    docs.mkdir()
    gone = tmp_path / "gone"
    gone.mkdir()
    monkeypatch.chdir(gone)
    gone.rmdir()
    lines: list[str] = []
    code = asyncio.run(
        execute(
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
            emit=lines.append,
            documents_dir=lambda: docs,
        )
    )
    assert code == 0
    from datetime import datetime, timezone

    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    assert list(docs.glob(f"FT-991A-radio-probe-{stamp}.json"))


def test_default_save_falls_back_when_documents_missing(tmp_path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    missing = tmp_path / "no-such-documents"
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: home))
    lines: list[str] = []
    code = asyncio.run(
        execute(
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
            emit=lines.append,
            documents_dir=lambda: missing,
        )
    )
    assert code == 0
    assert list(home.glob("FT-991A-radio-probe-*.json"))
    summary = "\n".join(lines)
    assert str(home) in summary


# --- Folder opener ---


class _TtyStdout(io.StringIO):
    """Stdout on a terminal (isatty True) that still captures writes."""

    def isatty(self) -> bool:
        return True


class _PipedStdout(io.StringIO):
    """Stdout without a terminal (a pipe, a capture, a sidecar hand-over)."""

    def isatty(self) -> bool:
        return False


def test_folder_opener_called_only_when_interactive(tmp_path, monkeypatch) -> None:
    opened: list[Path] = []
    monkeypatch.chdir(tmp_path)
    docs = tmp_path / "Documents"
    docs.mkdir(exist_ok=True)
    common = dict(
        config=resolve_config("FT-991A", RIGS),
        profile_toml_bytes=(REPO_ROOT / "rigs" / "ft991a.toml").read_bytes(),
        device_label="COM5 @ 38400",
        baud=38400,
        out=None,
        include_private=False,
        label="",
        gap_s=GAP,
        timeout_s=TIMEOUT,
        emit=lambda line: None,
        documents_dir=lambda: docs,
        open_folder=opened.append,
    )
    # --yes: no folder is opened even on a TTY.
    monkeypatch.setattr(sys, "stdout", _TtyStdout())
    code = asyncio.run(
        execute(
            **common,  # type: ignore[arg-type]
            yes=True,
            is_tty=True,
            ask=lambda prompt: (_ for _ in ()).throw(AssertionError("no prompt")),
            exchange_factory=_stub_answers([]),
        )
    )
    assert code == 0
    assert opened == []
    # Stdin is a TTY but stdout is not: a piped run never opens the folder.
    monkeypatch.setattr(sys, "stdout", _PipedStdout())
    answers = iter(["y", "y"])
    code = asyncio.run(
        execute(
            **common,  # type: ignore[arg-type]
            yes=False,
            is_tty=True,
            ask=lambda prompt: next(answers),
            exchange_factory=_stub_answers([]),
        )
    )
    assert code == 0
    assert opened == []
    # Interactive without --yes (both streams terminals): opened exactly once.
    monkeypatch.setattr(sys, "stdout", _TtyStdout())
    answers = iter(["y", "y"])
    code = asyncio.run(
        execute(
            **common,  # type: ignore[arg-type]
            yes=False,
            is_tty=True,
            ask=lambda prompt: next(answers),
            exchange_factory=_stub_answers([]),
        )
    )
    assert code == 0
    assert opened == [docs]


def test_folder_opener_failure_is_silent(tmp_path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    docs = tmp_path / "Documents"
    docs.mkdir(exist_ok=True)
    # A terminal stdout so the opener really runs and its failure is swallowed.
    monkeypatch.setattr(sys, "stdout", _TtyStdout())

    def boom(path: Path) -> None:
        raise RuntimeError("no desktop here")

    answers = iter(["y", "y"])
    lines: list[str] = []
    code = asyncio.run(
        execute(
            resolve_config("FT-991A", RIGS),
            (REPO_ROOT / "rigs" / "ft991a.toml").read_bytes(),
            device_label="COM5 @ 38400",
            baud=38400,
            out=None,
            include_private=False,
            label="",
            yes=False,
            is_tty=True,
            ask=lambda prompt: next(answers),
            exchange_factory=_stub_answers([]),
            gap_s=GAP,
            timeout_s=TIMEOUT,
            emit=lines.append,
            documents_dir=lambda: docs,
            open_folder=boom,
        )
    )
    assert code == 0
    assert list(docs.glob("FT-991A-radio-probe-*.json"))
    assert any("Saved to" in line for line in lines)
    assert any(line.startswith("Opening the folder: ") for line in lines)


def test_no_real_gui_opener_guard_intercepts_the_call(_no_real_gui_opener) -> None:
    """The suite guard patches the real opener instead of opening a folder."""
    probe_cli.open_result_folder(Path("/tmp/should-never-open"))
    assert _no_real_gui_opener == [Path("/tmp/should-never-open")]
    _no_real_gui_opener.clear()


def test_no_real_gui_opener_guard_fails_when_called() -> None:
    """The guard's verdict: silence on no calls, a clear failure on any call."""
    from tests.conftest import _assert_no_gui_opener_calls

    _assert_no_gui_opener_calls([])  # passes quietly
    with pytest.raises(AssertionError, match="real radio-probe folder opener"):
        _assert_no_gui_opener_calls([Path("/tmp/x")])


_BARE_CONFTEST_IMPORT = re.compile(
    r"^\s*(?:from conftest import|import conftest(?=\s|$))", re.MULTILINE
)


def test_bare_conftest_import_matcher_catches_nonleading_imports() -> None:
    """Pin the guard below: bare imports past the first line must match.

    The matcher is `^`-anchored, so without `re.MULTILINE` it only sees
    the start of the file and a bare import after a docstring — or an
    indented one inside a function — escapes the regression check.
    """
    assert _BARE_CONFTEST_IMPORT.search(
        '"""Docstring first."""\nfrom conftest import helper\n'
    )
    assert _BARE_CONFTEST_IMPORT.search(
        '"""Docstring first."""\nimport conftest\n'
    )
    assert _BARE_CONFTEST_IMPORT.search(
        "def test_something() -> None:\n    from conftest import helper\n"
    )
    assert _BARE_CONFTEST_IMPORT.search("x = 1\nimport conftest\n")
    # Absolute imports and lookalikes must stay clean.
    assert not _BARE_CONFTEST_IMPORT.search(
        "from tests.conftest import _assert_no_gui_opener_calls\n"
    )
    assert not _BARE_CONFTEST_IMPORT.search(
        '"""Docstring."""\n    from tests.conftest import helper\n'
    )
    assert not _BARE_CONFTEST_IMPORT.search("# from conftest import helper\n")
    assert not _BARE_CONFTEST_IMPORT.search('x = "from conftest import y"\n')


def test_no_test_uses_a_bare_conftest_import() -> None:
    """No test may import the bare `conftest` module (MOR-4214 follow-up).

    `tests/integration/` has no `__init__.py`, so under pytest's default
    prepend import mode any integration test module imported in the same
    worker puts `tests/integration/` on `sys.path` ahead of `tests/`. A
    bare import then resolves to `tests/integration/conftest.py` instead
    of `tests/conftest.py` and fails with `cannot import name ...` — but
    only on workers whose shard happens to import an integration module
    first, so only one xdist shard went red (PR #4709). Absolute imports
    from `tests.conftest` are immune to that ordering; this pin keeps the
    bare form from coming back.
    """
    offenders = sorted(
        str(path.relative_to(REPO_ROOT))
        for path in (REPO_ROOT / "tests").rglob("*.py")
        if _BARE_CONFTEST_IMPORT.search(path.read_text(encoding="utf-8"))
    )
    assert offenders == [], (
        "bare `conftest` imports resolve to whichever conftest.py directory "
        f"happens to be first on sys.path; use `tests.conftest`: {offenders}"
    )


# --- Discovery selection (unit level; dispatch level is in test_zero_arg_seam) ---


def _select(**overrides):
    params = dict(
        rigs=RIGS,
        rigs_dir=REPO_ROOT / "rigs",
        profile_name=None,
        baud_override=None,
        address_override=None,
        yes=False,
        is_tty=True,
        ask=lambda prompt: "y",
        emit=lambda line: None,
        discover=None,
    )
    params.update(overrides)
    return asyncio.run(probe_cli.select_radio(**params))  # type: ignore[arg-type]


def test_select_single_radio_confirmed_with_enter() -> None:
    seen_prompts: list[str] = []
    selection = _select(
        discover=lambda: asyncio.sleep(0, result=[_civ_result()]),
        ask=lambda prompt: seen_prompts.append(prompt) or "",
    )
    assert not isinstance(selection, int)
    assert selection.device == "COM5"
    assert selection.baud == 115200
    assert selection.address == 0x94
    assert selection.config.model == "IC-7300"
    assert any("Is this your radio?" in prompt for prompt in seen_prompts)


def test_select_single_radio_decline_quits_without_probing() -> None:
    # Declining stops inside selection: no exchange factory is ever built,
    # so nothing beyond discovery can touch a port.
    emitted: list[str] = []
    outcome = asyncio.run(
        probe_cli.select_radio(
            rigs=RIGS,
            rigs_dir=REPO_ROOT / "rigs",
            profile_name=None,
            baud_override=None,
            address_override=None,
            yes=False,
            is_tty=True,
            ask=lambda prompt: "n",
            emit=emitted.append,
            discover=lambda: asyncio.sleep(0, result=[_civ_result()]),
        )
    )
    assert outcome == 0
    assert any("Found: IC-7300 on COM5" in line for line in emitted)


def test_select_several_radios_numbered_choice() -> None:
    answers = iter(["2"])
    selection = _select(
        discover=lambda: asyncio.sleep(0, result=[_civ_result(), _cat_result()]),
        ask=lambda prompt: next(answers),
    )
    assert not isinstance(selection, int)
    assert selection.device == "COM6"
    assert selection.config.model == "FTX-1"


def test_select_several_radios_bad_number_then_enter_quits() -> None:
    emitted: list[str] = []
    answers = iter(["9", ""])
    selection = asyncio.run(
        probe_cli.select_radio(
            rigs=RIGS,
            rigs_dir=REPO_ROOT / "rigs",
            profile_name=None,
            baud_override=None,
            address_override=None,
            yes=False,
            is_tty=True,
            ask=lambda prompt: next(answers),
            emit=emitted.append,
            discover=lambda: asyncio.sleep(0, result=[_civ_result(), _cat_result()]),
        )
    )
    assert selection == 0
    assert any("not one of the numbers" in line for line in emitted)


def test_select_no_radios_is_exit_3_with_checklist() -> None:
    emitted: list[str] = []
    selection = _select(
        discover=lambda: asyncio.sleep(0, result=[]), emit=emitted.append
    )
    assert selection == 3
    text = "\n".join(emitted)
    assert "No radio found." in text
    assert "the USB cable is firmly connected" in text
    assert "rigplane probe <model> --port <device>" in text


def test_select_profile_without_port_filters_discovery() -> None:
    seen_prompts: list[str] = []
    selection = _select(
        profile_name="FTX-1",
        discover=lambda: asyncio.sleep(0, result=[_civ_result(), _cat_result()]),
        ask=lambda prompt: seen_prompts.append(prompt) or "",
    )
    assert not isinstance(selection, int)
    assert selection.device == "COM6"
    assert selection.config.model == "FTX-1"


def test_select_unsupported_protocol_is_exit_2() -> None:
    emitted: list[str] = []
    kenwood = RadioDiscoveryResult(
        port="COM7",
        protocol="kenwood_cat",
        model="TX-500",
        profile_id="lab599_tx500",
        baudrate=38400,
        address="500",
    )
    selection = asyncio.run(
        probe_cli.select_radio(
            rigs=RIGS,
            rigs_dir=REPO_ROOT / "rigs",
            profile_name=None,
            baud_override=None,
            address_override=None,
            yes=True,
            is_tty=False,
            ask=lambda prompt: (_ for _ in ()).throw(AssertionError("no prompt")),
            emit=emitted.append,
            discover=lambda: asyncio.sleep(0, result=[kenwood]),
        )
    )
    assert selection == 2
    assert any("not supported yet" in line for line in emitted)


def test_select_yes_takes_single_radio_without_prompt() -> None:
    selection = _select(
        yes=True,
        is_tty=False,
        ask=lambda prompt: (_ for _ in ()).throw(AssertionError("no prompt")),
        discover=lambda: asyncio.sleep(0, result=[_civ_result()]),
    )
    assert not isinstance(selection, int)
    assert selection.device == "COM5"


def test_select_several_without_tty_is_exit_2_listing() -> None:
    emitted: list[str] = []
    selection = asyncio.run(
        probe_cli.select_radio(
            rigs=RIGS,
            rigs_dir=REPO_ROOT / "rigs",
            profile_name=None,
            baud_override=None,
            address_override=None,
            yes=True,
            is_tty=False,
            ask=lambda prompt: (_ for _ in ()).throw(AssertionError("no prompt")),
            emit=emitted.append,
            discover=lambda: asyncio.sleep(0, result=[_civ_result(), _cat_result()]),
        )
    )
    assert selection == 2
    text = "\n".join(emitted)
    assert "IC-7300" in text and "FTX-1" in text
