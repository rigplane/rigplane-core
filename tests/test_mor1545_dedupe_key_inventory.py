"""MOR-1545 follow-up: pin the whole receiver-scoped dedupe-key inventory.

``commander.py: IcomCommander.send`` coalesces concurrent callers that share
a ``key`` onto one wire read via ``_pending_by_key``. Every per-receiver read
in the ``rigplane.runtime`` layer therefore registers under
``key=f"get_<something>:{receiver}"``; a bare key would hand a concurrent SUB
reader MAIN's answer (MOR-1545 / #2458).

Coverage before this file was thinner than three per-family test files
suggest. Measured on this tree: reverting all 27 keys to bare and running the
whole suite reddens 3 cases in 2 existing test functions -- and both functions
cover the same two families, ``get_repeater_tone`` and ``get_repeater_tsql``.
``test_mor1545_vfo_fallback_failure_paths.py:
TestVfoFallbackReadDedupeKeyReceiverScoped`` is a tone/TSQL test too, not a
filter-width one, and PR #2889 (which adds ``get_filter_width``) is still open.
So 22 of the 24 families -- 23 of the 27 sites -- had no detection at all.

This file generalises over the inventory rather than adding a fourth
per-family file. The list of sites is *derived from the source* by
:func:`_scoped_key_sites`, so a per-receiver read written in the shape all 27
current sites use -- the key inline as a literal or f-string, in a function
that takes ``receiver`` as a parameter -- cannot join the tree without either
a race row in :data:`_RACE_TABLE` or an entry in :data:`_NO_DUAL_RX_HOST`
that has to justify itself against the profiles. Other shapes are NOT seen;
:func:`_authored_key_sites` lists them. The
tone/TSQL rows here overlap the two families already covered; they are kept
because the table is derived from the inventory, and dropping them would mean
a second hand-maintained exception list.

What is pinned here, and what is not:

* :func:`test_no_receiver_scoped_read_authors_a_bare_dedupe_key` covers all
  27 sites at once -- it fails on any one of them reverting to a bare key.
* The race table now executes all 27 sites. Some families have both a direct
  and VFO-fallback call site, so the source-derived inventory assertion remains
  necessary alongside the wire races. ``get_manual_notch_width`` is raced on
  IC-7610 because its official guide marks 0x16/0x57 as Command 29 supported.
* That ``IcomCommander.send`` coalesces at all is *not* pinned here; the
  same-receiver control tests only keep a change that stopped deduping
  altogether from passing this file silently.
"""

from __future__ import annotations

import ast
import asyncio
import contextlib
import pathlib
import tomllib
from collections.abc import AsyncIterator, Iterator
from typing import Any, NamedTuple

import pytest

import rigplane.runtime.radio as radio_module
from rigplane import IC_7610_ADDR
from rigplane.commands import CONTROLLER_ADDR, build_civ_frame, build_cmd29_frame
from rigplane.core.exceptions import CommandError
from rigplane.core.types import AudioPeakFilter, FilterShape
from rigplane.radio import IcomRadio

from _helpers import wrap_civ_in_udp
from test_radio import MockTransport

# ---------------------------------------------------------------------------
# Inventory, derived from the source rather than listed by hand
# ---------------------------------------------------------------------------

_RUNTIME_ROOT = pathlib.Path(radio_module.__file__).parent
"""The whole ``rigplane.runtime`` package, not just ``radio.py``.

Scanning the layer rather than the one file is what makes this a guard
instead of a snapshot: ``_dual_rx_runtime.py`` already authors a dedupe key
(``_get_frequency_main``, correctly bare -- it is MAIN-only and takes no
``receiver``), so that module is exactly where a per-receiver read could
land next and slip past a radio.py-only check.
"""


class _KeySite(NamedTuple):
    """One ``key=`` argument the runtime layer *authors* (as opposed to
    forwards): a string literal or an f-string, never a bare name."""

    path: str
    lineno: int
    owner: str
    template: str
    receiver_in_scope: bool
    scoped: bool

    @property
    def family(self) -> str:
        """The key's command family -- the part before ``:{receiver}``."""
        return self.template.split(":", 1)[0]

    @property
    def where(self) -> str:
        return f"{self.path}:{self.lineno} {self.owner}"


def _enclosing_functions(
    node: ast.AST, parents: dict[ast.AST, ast.AST]
) -> Iterator[ast.FunctionDef | ast.AsyncFunctionDef]:
    current = parents.get(node)
    while current is not None:
        if isinstance(current, (ast.FunctionDef, ast.AsyncFunctionDef)):
            yield current
        current = parents.get(current)


def _receiver_in_scope(
    node: ast.AST, parents: dict[ast.AST, ast.AST]
) -> tuple[bool, str]:
    """Whether any enclosing function takes ``receiver`` as a parameter.

    A parameter, specifically: a ``receiver`` introduced by assignment inside
    the function is not seen. Walking the whole chain rather than only the
    innermost function is load-bearing:
    the VFO-select fallbacks of ``get_filter_width``, ``get_repeater_tone`` and
    ``get_repeater_tsql`` author their key inside a nested ``_action()``
    closure that takes no arguments and captures ``receiver`` from the getter
    around it. Checking only the innermost function would drop those three
    sites -- precisely the ones a fallback-branch regression would hit.
    """
    owner = "<module>"
    for index, function in enumerate(_enclosing_functions(node, parents)):
        if index == 0:
            owner = function.name
        args = function.args
        names = [a.arg for a in args.posonlyargs + args.args + args.kwonlyargs]
        if "receiver" in names:
            return True, function.name
    return False, owner


def _template_of(value: ast.Constant | ast.JoinedStr) -> tuple[str, bool]:
    """``(rendered template, interpolates receiver)`` for an authored key."""
    if isinstance(value, ast.Constant):
        return str(value.value), False
    rendered: list[str] = []
    scoped = False
    for part in value.values:
        if isinstance(part, ast.Constant):
            rendered.append(str(part.value))
            continue
        assert isinstance(part, ast.FormattedValue)
        rendered.append("{receiver}")
        if "receiver" in ast.unparse(part.value):
            scoped = True
    return "".join(rendered), scoped


def _authored_key_sites() -> list[_KeySite]:
    """Every authored ``key=`` argument in the ``rigplane.runtime`` package.

    Collected: a ``key=`` keyword argument whose value is written inline as
    a string literal or an f-string. A forwarded ``key=key`` authors nothing
    -- the caller already chose the string -- so it is skipped.

    NOT collected, and therefore invisible to
    :func:`test_no_receiver_scoped_read_authors_a_bare_dedupe_key`: a key
    assigned to a local first and then passed by name; a key built by
    concatenation or ``.format``; a key reaching ``send`` through ``**kwargs``;
    and a key authored where ``receiver`` was bound by assignment rather than
    declared as a parameter. Each was tried against this file and passed
    unnoticed. Every one of the 27 sites in the tree today is written inline,
    so the guard covers what exists -- it is a guard against the shape the
    codebase uses, not a proof about every shape Python allows.

    A module that fails to parse is raised rather than skipped: a silent
    ``SyntaxError`` would shrink the inventory and turn this guard green for
    the wrong reason.
    """
    sites: list[_KeySite] = []
    modules = sorted(_RUNTIME_ROOT.rglob("*.py"))
    assert modules, f"{_RUNTIME_ROOT} has no Python modules to scan"
    for module in modules:
        relative = module.relative_to(_RUNTIME_ROOT.parent).as_posix()
        try:
            tree = ast.parse(module.read_text(encoding="utf-8"))
        except SyntaxError as exc:  # pragma: no cover - a broken tree
            raise AssertionError(f"{relative} failed to parse: {exc}") from exc
        parents: dict[ast.AST, ast.AST] = {}
        for node in ast.walk(tree):
            for child in ast.iter_child_nodes(node):
                parents[child] = node
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            for keyword in node.keywords:
                if keyword.arg != "key":
                    continue
                value = keyword.value
                if not isinstance(value, (ast.Constant, ast.JoinedStr)):
                    continue
                template, scoped = _template_of(value)
                in_scope, owner = _receiver_in_scope(node, parents)
                sites.append(
                    _KeySite(relative, node.lineno, owner, template, in_scope, scoped)
                )
    assert sites, f"{_RUNTIME_ROOT.name}: no authored key= arguments found"
    return sites


def _scoped_key_sites() -> list[_KeySite]:
    """The inventory under test: authored keys where ``receiver`` is in scope."""
    return [site for site in _authored_key_sites() if site.receiver_in_scope]


# ---------------------------------------------------------------------------
# The race table
# ---------------------------------------------------------------------------

_CIV_TIMEOUT_S = 2.0
"""Generous per-request CI-V timeout. Nothing here measures elapsed time, so a
tight one would only risk ``TimeoutError`` inside ``_civ_rx.py:
CivRuntime._execute_civ_raw`` instead of exercising the dedupe key."""

_RIGS_ROOT = pathlib.Path(__file__).resolve().parent.parent / "rigs"

_IC7610_HOST = "192.168.1.100"
_IC9700_HOST = "192.168.1.102"
_IC9700_ADDR = 0xA2


class _Race(NamedTuple):
    """One command family raced MAIN-against-SUB on one dual-RX profile.

    ``main_payload``/``sub_payload`` are the reply bodies and
    ``main_value``/``sub_value`` what the getter decodes them to -- every pair
    measured on this tree, not read off a datasheet. The two values must
    differ, or the row could not tell a coalesced read from a correct one;
    :func:`test_every_row_can_detect_coalescing` enforces that.
    """

    family: str
    profile: str
    command: int
    sub: int
    main_payload: bytes
    sub_payload: bytes
    main_value: Any
    sub_value: Any

    @property
    def label(self) -> str:
        return f"{self.family}-{self.profile}"


def _bcd(family: str, command: int, sub: int) -> _Race:
    """A 2-byte BCD level on IC-7610: 0x0100 decodes to 100, 0x0250 to 250.

    100 and 250 rather than 1 and 2 so a failure message can never be misread
    as a receiver index.
    """
    return _Race(family, "IC-7610", command, sub, b"\x01\x00", b"\x02\x50", 100, 250)


def _flag(family: str, command: int, sub: int) -> _Race:
    """A 1-byte boolean on IC-7610."""
    return _Race(family, "IC-7610", command, sub, b"\x01", b"\x00", True, False)


def _byte(
    family: str,
    command: int,
    sub: int,
    *,
    main: tuple[bytes, Any],
    sub_rx: tuple[bytes, Any],
) -> _Race:
    """A 1-byte value on IC-7610 that decodes to an int or an ``IntEnum``."""
    return _Race(
        family, "IC-7610", command, sub, main[0], sub_rx[0], main[1], sub_rx[1]
    )


# IC-7610 lists every command below in its ``[cmd29] routes``, so both
# receivers take the *direct* branch and the two requests differ only in the
# cmd29 receiver byte -- measured as ``29 00`` against ``29 01`` on this tree.
_IC7610_ROWS = [
    _bcd("get_af_level", 0x14, 0x01),
    _bcd("get_rf_gain", 0x14, 0x02),
    _bcd("get_squelch", 0x14, 0x03),
    _bcd("get_apf_type_level", 0x14, 0x05),
    _bcd("get_nr_level", 0x14, 0x06),
    _bcd("get_pbt_inner", 0x14, 0x07),
    _bcd("get_pbt_outer", 0x14, 0x08),
    _bcd("get_notch_filter", 0x14, 0x0D),
    _bcd("get_nb_level", 0x14, 0x12),
    _bcd("get_digisel_shift", 0x14, 0x13),
    _flag("get_s_meter_sql_status", 0x15, 0x01),
    _flag("get_various_squelch", 0x15, 0x05),
    _flag("get_auto_notch", 0x16, 0x41),
    _flag("get_manual_notch", 0x16, 0x48),
    _flag("get_twin_peak_filter", 0x16, 0x4F),
    _flag("get_af_mute", 0x1A, 0x09),
    _byte("get_agc", 0x16, 0x12, main=(b"\x01", 1), sub_rx=(b"\x02", 2)),
    _byte("get_agc_time_constant", 0x1A, 0x04, main=(b"\x01", 1), sub_rx=(b"\x02", 2)),
    _byte(
        "get_audio_peak_filter",
        0x16,
        0x32,
        main=(b"\x01", AudioPeakFilter.WIDE),
        sub_rx=(b"\x02", AudioPeakFilter.MID),
    ),
    _byte(
        "get_filter_shape",
        0x16,
        0x56,
        main=(b"\x01", FilterShape.SOFT),
        sub_rx=(b"\x00", FilterShape.SHARP),
    ),
    _byte(
        "get_manual_notch_width",
        0x16,
        0x57,
        main=(b"\x00", 0),
        sub_rx=(b"\x02", 2),
    ),
    _byte("get_filter_width", 0x1A, 0x03, main=(b"\x01", 100), sub_rx=(b"\x02", 150)),
]

# IC-9700 declares ``routes = []``, so MAIN takes the direct branch and SUB the
# VFO-select fallback: the two *different* call sites whose keys must not
# collide. Its replies carry no receiver tag, so on this profile nothing but
# the dedupe key distinguishes the two in-flight reads.
_IC9700_ROWS = [
    _Race("get_filter_width", "IC-9700", 0x1A, 0x03, b"\x01", b"\x02", 100, 150),
    _Race("get_repeater_tone", "IC-9700", 0x16, 0x42, b"\x01", b"\x00", True, False),
    _Race("get_repeater_tsql", "IC-9700", 0x16, 0x43, b"\x01", b"\x00", True, False),
]

_RACE_TABLE = [*_IC7610_ROWS, *_IC9700_ROWS]

_NO_DUAL_RX_HOST: set[str] = set()


def _dual_rx_models() -> list[str]:
    """Every profile in ``rigs/`` that declares ``receiver_count = 2``.

    Derived, not listed. A hand-written pair here was short by one --
    ``FTX-1`` is a dual-RX profile too -- which made the exemption below
    a measurement on two profiles and an assumption on the third. Since
    the whole point of this file is an inventory the tree cannot drift
    away from, the profile list beside it must be derived the same way.
    """
    models: list[str] = []
    for toml_path in sorted(_RIGS_ROOT.glob("*.toml")):
        radio = tomllib.loads(toml_path.read_text(encoding="utf-8")).get("radio", {})
        if radio.get("receiver_count") == 2:
            models.append(str(radio["model"]))
    assert models, f"{_RIGS_ROOT}: no profile declares receiver_count = 2"
    return models


_DUAL_RX_MODELS = _dual_rx_models()


# ---------------------------------------------------------------------------
# Harness
# ---------------------------------------------------------------------------


def _build_radio(host: str, transport: MockTransport, **kwargs: Any) -> IcomRadio:
    radio = IcomRadio(host, timeout=_CIV_TIMEOUT_S, **kwargs)
    radio._civ_transport = transport
    radio._ctrl_transport = transport
    radio._connected = True
    return radio


@contextlib.asynccontextmanager
async def _reader(row: _Race) -> AsyncIterator[tuple[IcomRadio, MockTransport]]:
    """A connected radio for *row*'s profile with the commander worker running.

    Starting the worker is load-bearing, not hygiene: with ``_commander is
    None`` ``_civ_rx.py: CivRuntime._send_civ_raw`` never reaches
    ``IcomCommander.send`` and drops ``key``/``dedupe`` outright, so every
    cross-receiver case below would pass on any tree while checking nothing.
    """
    transport = MockTransport()
    if row.profile == "IC-7610":
        radio = _build_radio(_IC7610_HOST, transport, model="IC-7610")
    else:
        radio = _build_radio(_IC9700_HOST, transport, model="IC-9700")

        async def _instant_select(vfo: str) -> None:
            """No-op VFO swap, so the fallback branch's own dedupe check
            happens while MAIN's read is still in flight."""
            return None

        radio._set_vfo_wire = _instant_select  # type: ignore[method-assign]
    radio._civ_runtime.start_worker()
    try:
        yield radio, transport
    finally:
        await radio._civ_runtime.stop_worker()
        radio._connected = False


def _reply(row: _Race, payload: bytes, *, receiver: int) -> bytes:
    if row.profile == "IC-7610":
        return wrap_civ_in_udp(
            build_cmd29_frame(
                CONTROLLER_ADDR,
                IC_7610_ADDR,
                row.command,
                sub=row.sub,
                data=payload,
                receiver=receiver,
            )
        )
    return wrap_civ_in_udp(
        build_civ_frame(
            CONTROLLER_ADDR, _IC9700_ADDR, row.command, sub=row.sub, data=payload
        )
    )


# ---------------------------------------------------------------------------
# The inventory invariant -- all 27 sites at once
# ---------------------------------------------------------------------------


def test_no_receiver_scoped_read_authors_a_bare_dedupe_key() -> None:
    """Every authored key in a scope that has a ``receiver`` interpolates it.

    This is the one assertion that covers the whole inventory: it reddens on
    any single site losing its ``{receiver}``, including the 6 sites that no
    race row can detect on its own. It sees only inline literals and
    f-strings -- :func:`_authored_key_sites` lists the shapes it misses.
    """
    bare = [site for site in _scoped_key_sites() if not site.scoped]
    assert not bare, "receiver-scoped reads authoring a bare dedupe key:\n" + "\n".join(
        f"  {site.where} key={site.template!r}" for site in bare
    )


def test_race_table_matches_the_derived_inventory() -> None:
    """The table is checked against the source, so it cannot silently rot.

    A new per-receiver read fails here until it is either raced by a row or
    declared in :data:`_NO_DUAL_RX_HOST` -- and that declaration has to survive
    :func:`test_declared_exception_cannot_host_a_dual_receiver_race`.
    """
    inventory = {site.family for site in _scoped_key_sites()}
    covered = {row.family for row in _RACE_TABLE} | _NO_DUAL_RX_HOST
    assert covered == inventory


@pytest.mark.parametrize("row", _RACE_TABLE, ids=lambda r: r.label)
def test_every_row_can_detect_coalescing(row: _Race) -> None:
    """A row whose two receivers decode to the same value could never fail."""
    assert row.main_payload != row.sub_payload
    assert row.main_value != row.sub_value


@pytest.mark.asyncio
@pytest.mark.parametrize("family", sorted(_NO_DUAL_RX_HOST))
@pytest.mark.parametrize("model", _DUAL_RX_MODELS)
async def test_declared_exception_cannot_host_a_dual_receiver_race(
    family: str, model: str
) -> None:
    """The exemption is a measurement, not an allowlist.

    Runs against every profile declaring ``receiver_count = 2``, taken from
    ``rigs/`` rather than from a list here. Should any of them ever gain the
    route, this fails and forces the family into :data:`_RACE_TABLE` rather
    than letting it sit permanently exempt.
    """
    transport = MockTransport()
    radio = _build_radio(_IC7610_HOST, transport, model=model)
    try:
        with pytest.raises(CommandError, match="no cmd29 route"):
            await getattr(radio, family)(receiver=1)
        assert transport.sent_packets == []
    finally:
        radio._connected = False


# ---------------------------------------------------------------------------
# The races themselves -- executing 26 of the 27 sites
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.parametrize("row", _RACE_TABLE, ids=lambda r: r.label)
@pytest.mark.parametrize("main_first", [True, False], ids=["main-1st", "sub-1st"])
async def test_concurrent_main_and_sub_reads_do_not_coalesce(
    row: _Race, main_first: bool
) -> None:
    """A MAIN read in flight must not hand its answer to a SUB reader.

    Both ``asyncio.gather`` orders run because the coalesced answer is
    whichever read went first: under a bare key a test asserting only the SUB
    value would stay green on SUB-first coalescing. Both returned values are
    asserted for that reason, and the frame count only *in addition* to them
    -- on the repeater-tone family a reverted tree was measured emitting the
    same frame count, because the VFO restore timed out and retried.
    """
    async with _reader(row) as (radio, transport):
        read = getattr(radio, row.family)
        main_reply = _reply(row, row.main_payload, receiver=0)
        sub_reply = _reply(row, row.sub_payload, receiver=1)
        if main_first:
            transport.queue_response_on_send(1, main_reply)
            transport.queue_response_on_send(2, sub_reply)
            main_value, sub_value = await asyncio.gather(
                read(receiver=0), read(receiver=1)
            )
        else:
            transport.queue_response_on_send(1, sub_reply)
            transport.queue_response_on_send(2, main_reply)
            sub_value, main_value = await asyncio.gather(
                read(receiver=1), read(receiver=0)
            )
        assert main_value == row.main_value
        assert sub_value == row.sub_value
        assert len(transport.sent_packets) == 2


@pytest.mark.asyncio
@pytest.mark.parametrize("row", _RACE_TABLE, ids=lambda r: r.label)
async def test_concurrent_same_receiver_reads_still_dedupe(row: _Race) -> None:
    """Control: the dedupe mechanism is intact, so a change that merely
    stopped deduping could not keep the race tests above green.

    Both reads use SUB, the receiver that reaches the VFO-select fallback on
    IC-9700; on IC-7610 both receivers share the one direct call site anyway.
    """
    async with _reader(row) as (radio, transport):
        read = getattr(radio, row.family)
        transport.queue_response_on_send(1, _reply(row, row.sub_payload, receiver=1))
        first, second = await asyncio.gather(read(receiver=1), read(receiver=1))
        assert first == row.sub_value
        assert second == row.sub_value
        assert len(transport.sent_packets) == 1
