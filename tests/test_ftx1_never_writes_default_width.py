"""MOR-3677 / MOR-3693: only the owner-sanctioned paths can send SH0 000.

The FTX-1 answers a width read with code 00 in DATA and CW modes (MOR-3677
bench frames ``MD0C; SH0000;`` and ``MD03; SH0000;``), so 00 on the wire is a
read the UI must render honestly — never something Core invents. MOR-3693
asks for the proof: no Core path writes ``SH0 000`` except the
owner-sanctioned radio-default reset (the width-row double-click and the
sidebar Reset button, both user clicks dispatching ``reset_filter_width``).

Part A pins the call graph by scanning the sources with an allow-list, so a
new caller fails the test. Part B drives a fake FTX-1 through polls and
commands and asserts the recorded writes never contain ``SH[01]000``; the
positive control proves the detector by producing exactly that frame.
"""

from __future__ import annotations

import ast
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from rigplane.backends.yaesu_cat.observations import YaesuObservationAdapter
from rigplane.backends.yaesu_cat.poller import YaesuCatPoller
from rigplane.backends.yaesu_cat.radio import YaesuCatRadio
from rigplane.runtime._poller_types import (
    ResetFilterWidth,
    SetFilterWidth,
    SetMode,
    SetNarrow,
)

ROOT = Path(__file__).parents[1]
SRC = ROOT / "src" / "rigplane"
FRONTEND_SRC = ROOT / "frontend" / "src"

_DEFAULT_WRITE = re.compile(r"^SH[01]000;$")


@dataclass(frozen=True, slots=True)
class _Site:
    """One scanned use site: file (repo-relative posix), line, scope."""

    file: str
    lineno: int
    scope: str


class _ScopeVisitor(ast.NodeVisitor):
    """Collect reset/default-code use sites with their enclosing scope."""

    def __init__(self, rel: str) -> None:
        self.rel = rel
        self.scope: list[str] = []
        self.constructed: list[_Site] = []
        self.method_calls: list[_Site] = []
        self.attr_reads: list[_Site] = []

    def _site(self, node: ast.AST) -> _Site:
        return _Site(self.rel, node.lineno, ".".join(self.scope))

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        self.scope.append(node.name)
        self.generic_visit(node)
        self.scope.pop()

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        self.scope.append(node.name)
        self.generic_visit(node)
        self.scope.pop()

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        self.scope.append(node.name)
        self.generic_visit(node)
        self.scope.pop()

    def visit_Call(self, node: ast.Call) -> None:
        func = node.func
        if isinstance(func, ast.Name) and func.id == "ResetFilterWidth":
            self.constructed.append(self._site(node))
        elif isinstance(func, ast.Attribute) and func.attr == "reset_filter_width":
            self.method_calls.append(self._site(node))
        self.generic_visit(node)

    def visit_Attribute(self, node: ast.Attribute) -> None:
        if node.attr == "filter_width_radio_default_code":
            self.attr_reads.append(self._site(node))
        self.generic_visit(node)


def _scan_src() -> _ScopeVisitor:
    merged = _ScopeVisitor("")
    for path in sorted(SRC.rglob("*.py")):
        visitor = _ScopeVisitor(path.relative_to(ROOT).as_posix())
        visitor.visit(ast.parse(path.read_text(encoding="utf-8")))
        merged.constructed.extend(visitor.constructed)
        merged.method_calls.extend(visitor.method_calls)
        merged.attr_reads.extend(visitor.attr_reads)
    return merged


def test_reset_filter_width_is_constructed_only_by_the_web_control_handler() -> None:
    """``ResetFilterWidth`` has no internal producer: only the WS command
    handler builds it (``case`` arms are match patterns, not constructions,
    so the AST scan ignores them)."""

    sites = _scan_src().constructed

    assert {site.file for site in sites} == {"src/rigplane/web/handlers/control.py"}
    assert len(sites) == 1


def test_reset_filter_width_is_awaited_only_by_the_two_dispatchers() -> None:
    """The Yaesu dispatcher and the generic poller are the only runners of
    the radio's reset; the protocol definition carries no dot-call."""

    sites = _scan_src().method_calls

    assert {site.file for site in sites} == {
        "src/rigplane/backends/yaesu_cat/poller.py",
        "src/rigplane/web/radio_poller.py",
    }
    assert len(sites) == 2
    assert {site.scope for site in sites} == {
        "YaesuCatPoller._execute_command",
        "RadioPoller._execute_unlocked",
    }


def test_radio_default_code_is_used_only_by_reset_and_the_width_read() -> None:
    """``filter_width_radio_default_code`` is read only where the SH 00
    contract lives: the reset write and the width read that flags a 00
    answer (MOR-3677)."""

    sites = [
        site
        for site in _scan_src().attr_reads
        if site.file == "src/rigplane/backends/yaesu_cat/radio.py"
    ]

    assert {site.scope for site in sites} == {
        "YaesuCatRadio.reset_filter_width",
        "YaesuCatRadio.read_filter_width_reading",
    }


def _frontend_sources() -> list[Path]:
    return [
        path
        for path in sorted(FRONTEND_SRC.rglob("*"))
        if path.suffix in {".ts", ".svelte"}
        and "__tests__" not in path.parts
        and ".test." not in path.name
        and ".spec." not in path.name
    ]


def _grep_frontend(pattern: str) -> list[tuple[str, int, str]]:
    found: list[tuple[str, int, str]] = []
    compiled = re.compile(pattern)
    for path in _frontend_sources():
        for lineno, line in enumerate(
            path.read_text(encoding="utf-8").splitlines(), start=1
        ):
            if compiled.search(line):
                found.append((path.relative_to(ROOT).as_posix(), lineno, line))
    return found


def _enclosing_function(path: Path, lineno: int) -> str | None:
    """Name the ``function`` whose brace block holds ``lineno`` (1-based)."""
    lines = path.read_text(encoding="utf-8").splitlines()
    candidates = [
        (index, line)
        for index, line in enumerate(lines[:lineno])
        if re.search(r"\bfunction\s+(\w+)", line)
    ]
    for index, line in reversed(candidates):
        depth = 0
        for probe in lines[index:lineno]:
            depth += probe.count("{") - probe.count("}")
            if depth <= 0 and probe is not lines[index]:
                break
        else:
            match = re.search(r"\bfunction\s+(\w+)", line)
            assert match is not None
            return match.group(1)
    return None


def test_frontend_reset_reaches_the_wire_only_through_the_two_clicks() -> None:
    """``onFilterWidthReset`` is defined once (panel-commands, the only
    ``reset_filter_width`` dispatch site) and invoked only by the width-row
    double-click (``resetWidthRow`` via the wired ``onWidthReset`` prop) and
    the sidebar Reset button (``handleTableReset``)."""

    # An object-literal member whose value is the handler; a JSX ternary such
    # as ``cond ? filterIntents.onFilterWidthReset : undefined`` only passes
    # the handler along and is not a definition.
    definitions = _grep_frontend(r"^\s*onFilterWidthReset\s*:\s*\(")
    assert [file for file, _, _ in definitions] == [
        "frontend/src/lib/runtime/commands/panel-commands.ts"
    ]

    dispatches = _grep_frontend(
        r"dispatchRadioIntent\(\{\s*name:\s*'reset_filter_width'"
    )
    assert [file for file, _, _ in dispatches] == [
        "frontend/src/lib/runtime/commands/panel-commands.ts"
    ]

    calls = _grep_frontend(
        r"\bonFilterWidthReset\s*\?\.\s*\(|\.onFilterWidthReset\s*\("
    )
    assert [file for file, _, _ in calls] == [
        "frontend/src/components-v2/panels/FilterPanel.svelte"
    ]
    (call_file, call_lineno, _) = calls[0]
    assert _enclosing_function(ROOT / call_file, call_lineno) == "handleTableReset"

    width_resets = _grep_frontend(r"(?<![\w.])onWidthReset\s*\(")
    assert [file for file, _, _ in width_resets] == [
        "frontend/src/semantic/FilterSurface.svelte"
    ]
    (reset_file, reset_lineno, _) = width_resets[0]
    assert _enclosing_function(ROOT / reset_file, reset_lineno) == "resetWidthRow"
    bindings = _grep_frontend(r"ondblclick=\{resetWidthRow\}")
    assert len(bindings) == 2


# Silent-action answers for every other read the three lanes issue (copied
# from tests/test_ftx1_sub_acquisition.py::_CAT_ANSWERS, whose tests prove
# the set is complete for these lanes). Mode/width/narrow answers come from
# the mutable table below instead. The mock answers WITHOUT the trailing
# ";" — the radio's query path adds the frame terminator itself.
_STATIC_ANSWERS = {
    "FA;": "FA014043200",
    "FB;": "FB144500000",
    "SM0;": "SM0000",
    "SM1;": "SM0078",
    "FT;": "FT0",
    "TX;": "TX0",
    "FR;": "FR01",
    "AG0;": "AG0000",
    "AG1;": "AG1020",
    "RG0;": "RG0255",
    "RG1;": "RG1225",
    "SQ0;": "SQ0000",
    "SQ1;": "SQ1025",
    "OS0;": "OS03",
    "OS1;": "OS11",
    "PA0;": "PA00",
    "GT0;": "GT06",
    "GT1;": "GT11",
    "IS0;": "IS00+0000",
    "IS1;": "IS10+0200",
    "NL0;": "NL0000",
    "NL1;": "NL1003",
    "RL0;": "RL000",
    "RL1;": "RL105",
    "BC0;": "BC00",
    "BC1;": "BC11",
    "BP00;": "BP00000",
    "BP10;": "BP10001",
    "BP11;": "BP11120",
    "CT0;": "CT00",
    "CN00;": "CN00008",
    "CT1;": "CT12",
    "CN10;": "CN10015",
    "VS;": "VS0",
    "CS;": "CS0",
    "RM8;": "RM8080020",
    "RM7;": "RM7010000",
}


def _stream_radio() -> tuple[YaesuCatRadio, list[str], dict[str, Any], dict[str, str]]:
    """A real FTX-1 radio on a fake transport with mutable mode/width/narrow.

    Returns the radio, the recorded write list, the state table, and the
    mode-code table. Writes update the table like the radio would, so
    verified writes (``set_mode``) read back what they wrote.
    """
    radio = YaesuCatRadio("/dev/null", profile="ftx1", audio_driver=MagicMock())
    radio._transport._connected = True  # noqa: SLF001
    codes: dict[str, str] = dict(radio._mode_to_code)  # noqa: SLF001
    by_code = {code: mode for mode, code in codes.items()}
    state: dict[str, Any] = {
        "main_mode": "USB",
        "sub_mode": "LSB",
        "main_width": 20,
        "sub_width": 20,
        "main_narrow": False,
        "sub_narrow": False,
    }
    writes: list[str] = []

    def query(command: str) -> str:
        if command == "MD0;":
            return f"MD0{codes[state['main_mode']]}"
        if command == "MD1;":
            return f"MD1{codes[state['sub_mode']]}"
        if command == "SH0;":
            return f"SH0{state['main_width']:03d}"
        if command == "SH1;":
            return f"SH1{state['sub_width']:03d}"
        if command == "NA0;":
            return f"NA0{int(bool(state['main_narrow']))}"
        if command == "NA1;":
            return f"NA1{int(bool(state['sub_narrow']))}"
        return _STATIC_ANSWERS[command]

    def write(command: str) -> None:
        writes.append(command)
        if command.startswith("MD0"):
            state["main_mode"] = by_code[command[3]]
        elif command.startswith("MD1"):
            state["sub_mode"] = by_code[command[3]]
        elif command.startswith("SH0"):
            state["main_width"] = int(command[3:6])
        elif command.startswith("SH1"):
            state["sub_width"] = int(command[3:6])
        elif command.startswith("NA0"):
            state["main_narrow"] = command[3] == "1"
        elif command.startswith("NA1"):
            state["sub_narrow"] = command[3] == "1"

    radio._transport.query = AsyncMock(side_effect=query)  # noqa: SLF001
    radio._transport.write = AsyncMock(side_effect=write)  # noqa: SLF001
    return radio, writes, state, codes


@pytest.mark.asyncio
async def test_polling_and_commands_never_write_the_default_width_code() -> None:
    """Full command stream: polls plus mode/narrow/width writes never emit
    ``SH[01]000``. Provenance for every row: the FTX-1 on the Mac mini over
    USB (MOR-3677 CAT watch 2026-10-22, MOR-3693 reads 2026-10-21)."""
    radio, writes, state, _codes = _stream_radio()
    poller = YaesuCatPoller(radio)
    adapter = YaesuObservationAdapter.from_radio(radio)

    # USB (MD02; SH0020; NA00;), DATA-U (MD0C; SH0000; NA00;), CW-U
    # (MD03; SH0000; NA00;), LSB (MD01; SH0020; NA00;).
    for mode, code in (("USB", 20), ("DATA-U", 0), ("CW-U", 0), ("LSB", 20)):
        state["main_mode"] = mode
        state["sub_mode"] = mode
        state["main_width"] = code
        state["sub_width"] = code
        state["main_narrow"] = False
        state["sub_narrow"] = False
        for _ in range(3):
            await adapter.poll_rx_meters()
            await adapter.poll_medium()
            await adapter.poll_slow_controls()

    for mode in ("USB", "DATA-U", "CW-U", "LSB"):
        await poller._execute_command(SetMode(mode=mode, receiver=0))  # noqa: SLF001
        await poller._execute_command(SetMode(mode=mode, receiver=1))  # noqa: SLF001
    for receiver in (0, 1):
        await poller._execute_command(SetNarrow(on=True, receiver=receiver))  # noqa: SLF001
        await poller._execute_command(SetNarrow(on=False, receiver=receiver))  # noqa: SLF001

    # CW-U 500 Hz is Table 5 code 10: the only legitimate width write in
    # this stream (MOR-3693, 2026-10-21 20:47 EDT).
    state["main_mode"] = "CW-U"
    before = len(writes)
    await poller._execute_command(SetFilterWidth(width=500, receiver=0))  # noqa: SLF001
    assert writes[before:] == ["SH0010;"]

    assert [frame for frame in writes if _DEFAULT_WRITE.match(frame)] == []


@pytest.mark.asyncio
async def test_reset_filter_width_writes_the_default_code() -> None:
    """Positive control: the sanctioned reset is exactly the frame the
    detector above looks for, so the stream test cannot pass on a blind
    matcher."""
    radio, writes, _state, _codes = _stream_radio()
    poller = YaesuCatPoller(radio)

    await poller._execute_command(ResetFilterWidth(receiver=0))  # noqa: SLF001
    assert writes == ["SH0000;"]
    assert all(_DEFAULT_WRITE.match(frame) for frame in writes)

    writes.clear()
    await poller._execute_command(ResetFilterWidth(receiver=1))  # noqa: SLF001
    assert writes == ["SH1000;"]
    assert all(_DEFAULT_WRITE.match(frame) for frame in writes)
