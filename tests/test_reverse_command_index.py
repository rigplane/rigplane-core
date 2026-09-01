"""Reverse command index (MOR-1993 Z2,
`docs/plans/2026-09-01-reverse-command-index.md` §4.1/§5).

``ReverseCommandIndex`` (``commands/command_map.py``) is the inverse of
``CommandMap``: given an incoming ``(command, sub, data)`` frame, resolve
the declared name it came from. This file has three parts:

- ``test_round_trip_contract``: for every declared name in every CI-V
  profile, building a frame from that name's own declared tuple and
  decoding it back must yield that name, or an explicitly-recorded
  ambiguity -- never a *different*, wrong name. ``ftx1``/``tx500`` are CAT
  radios (Yaesu text protocol) and carry no CI-V ``[commands]`` entries,
  so they are excluded the same way ``tests/test_profile_command_coverage.py``
  excludes them (``config.protocol_type == "civ"``).
- ``TestKnownCollisionShapes``: a handful of structurally distinct cases
  pinned individually, so a regression names the specific shape that broke
  instead of only the aggregate counts below.
- ``test_census_matches_baseline``: per-profile name/key/collision counts,
  regenerable rather than hand-maintained::

    RIGPLANE_REGEN_REVERSE_COMMAND_INDEX_CENSUS=1 uv run pytest tests/test_reverse_command_index.py
"""

from __future__ import annotations

import os
import pathlib

import pytest

from rigplane.commands._frame import decode_wire_tuple
from rigplane.commands.command_map import CommandMap, ReverseCommandIndex
from rigplane.profiles import RadioProfile, get_radio_profile, reload_profiles
from rigplane.profiles.rig_loader import discover_rigs

REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]
RIGS_DIR = REPO_ROOT / "rigs"
CENSUS_FILE = pathlib.Path(__file__).with_name("reverse_command_index_census.txt")
REGEN_ENV = "RIGPLANE_REGEN_REVERSE_COMMAND_INDEX_CENSUS"

Groups = dict[tuple[int, "int | None"], dict[bytes, tuple[str, ...]]]


def _civ_profiles() -> dict[str, RadioProfile]:
    """Every CI-V (non-CAT) profile, built fresh -- mirrors
    `tests/test_profile_command_coverage.py: _civ_rigs`.
    """
    reload_profiles()
    return {
        model: config.to_profile()
        for model, config in sorted(discover_rigs(RIGS_DIR).items())
        if config.protocol_type == "civ"
    }


def _group_names(command_map: CommandMap) -> Groups:
    """Group declared names by (command, sub, prefix).

    An independent reference computation for this test file -- calls the
    same `commands/_frame.py: decode_wire_tuple` the index itself uses,
    but does not read `ReverseCommandIndex`'s internal buckets, so the
    tests below exercise ``resolve()`` rather than just re-checking this
    function's own output.
    """
    raw: dict[tuple[int, int | None], dict[bytes, list[str]]] = {}
    for name in command_map:
        command, sub, prefix = decode_wire_tuple(command_map.get(name))
        raw.setdefault((command, sub), {}).setdefault(prefix, []).append(name)
    return {
        key: {prefix: tuple(names) for prefix, names in group.items()}
        for key, group in raw.items()
    }


def _safe_write_probe(
    index: ReverseCommandIndex,
    command: int,
    sub: int | None,
    prefix: bytes,
    group: frozenset[str],
) -> bytes | None:
    """A 1-byte suffix appended to *prefix* that resolves back into *group*.

    Needed to round-trip a non-``get_`` group member: its own declared
    tuple is byte-identical to its ``get_`` sibling's (that identity *is*
    the collision), so testing it requires a frame with something past
    the declared prefix -- but a naive fixed byte can collide with an
    unrelated, longer sibling prefix at the same ``(command, sub)`` (e.g.
    ``0x1C 0x00``'s bare group sits alongside ``ptt_on``/``ptt_off``'s own
    longer, unique prefixes). Tries every byte and keeps the first one
    ``resolve()`` itself confirms stays inside *group* (resolved to a
    member, or ambiguous with candidates that are a subset of *group*).
    Returns ``None`` if no byte in 0..255 is safe.
    """
    for value in range(256):
        candidate = bytes([value])
        result = index.resolve(command, sub, prefix + candidate)
        if result.name in group or (result.ambiguous and result.candidates <= group):
            return candidate
    return None


def test_round_trip_contract() -> None:
    resolved = 0
    ambiguous = 0
    wrong: list[str] = []
    for model, profile in _civ_profiles().items():
        command_map = profile.command_map
        index = profile.reverse_index
        assert command_map is not None and index is not None, model
        for (command, sub), prefix_groups in _group_names(command_map).items():
            for prefix, names in prefix_groups.items():
                group = frozenset(names)
                for name in names:
                    if len(names) == 1 or name.startswith("get_"):
                        data = prefix
                    else:
                        suffix = _safe_write_probe(index, command, sub, prefix, group)
                        assert suffix is not None, (
                            f"{model}: no safe write probe for {name!r} at "
                            f"command={command:#x} sub={sub} prefix={prefix!r}"
                        )
                        data = prefix + suffix
                    result = index.resolve(command, sub, data)
                    if result.name == name:
                        resolved += 1
                    elif result.ambiguous and name in result.candidates:
                        ambiguous += 1
                    else:
                        wrong.append(f"{model}:{name} -> {result}")
    assert not wrong, "resolved to the wrong name:\n" + "\n".join(wrong)
    # Not vacuous: both outcomes are expected to occur across six profiles.
    assert resolved > 0
    assert ambiguous > 0


class TestKnownCollisionShapes:
    """Individually pinned, structurally distinct cases (established
    directly from ``rigs/*.toml`` -- see each test's own docstring)."""

    def test_ptt_on_off_are_distinct_full_tuples(self) -> None:
        index = get_radio_profile("ic7300").reverse_index
        assert index is not None
        assert index.resolve(0x1C, 0x00, b"\x01").name == "ptt_on"
        assert index.resolve(0x1C, 0x00, b"\x00").name == "ptt_off"

    def test_get_set_pair_resolves_by_payload_length(self) -> None:
        index = get_radio_profile("ic7300").reverse_index
        assert index is not None
        assert index.resolve(0x14, 0x01, b"").name == "get_af_level"
        assert index.resolve(0x14, 0x01, b"\x50").name == "set_af_level"

    def test_menu_family_does_not_collapse_to_one_key(self) -> None:
        """IC-7300 declares 84 distinct prefixes under 0x1A 0x05 (168
        names); a (command, sub)-blind index would answer every one of
        them identically. Two different addresses must resolve
        differently."""
        index = get_radio_profile("ic7300").reverse_index
        assert index is not None
        first = index.resolve(0x1A, 0x05, b"\x00\x01")
        second = index.resolve(0x1A, 0x05, b"\x00\x02")
        assert first.name == "get_ssb_rx_hpflpf"
        assert second.name == "get_ssb_rx_bass"

    def test_direction_only_write_family_stays_ambiguous(self) -> None:
        """Scan (0x0E, class (c)): five write-only names share one bare
        tuple with nothing to break the tie until Z3's annotations."""
        index = get_radio_profile("ic7300").reverse_index
        assert index is not None
        result = index.resolve(0x0E, None, b"\x01")
        assert result.ambiguous
        assert result.candidates == frozenset(
            {
                "scan_start",
                "scan_stop",
                "scan_start_type",
                "scan_set_df_span",
                "scan_set_resume",
            }
        )

    def test_ic7610_antenna_bare_tuple_stays_ambiguous(self) -> None:
        """rigs/ic7610.toml declares get_antenna/get_rx_antenna_ant2 (and
        their setters) as the identical bare [0x12] tuple -- the ANT1/ANT2
        selector is a runtime sub-byte the TOML never records for either."""
        index = get_radio_profile("ic7610").reverse_index
        assert index is not None
        result = index.resolve(0x12, None, b"")
        assert result.ambiguous
        assert result.candidates == frozenset({"get_antenna", "get_rx_antenna_ant2"})

    def test_transceiver_status_family_resolves_all_four_names(self) -> None:
        """rigs/x6100.toml (and ic705/x6200) declares four names under
        0x1C 0x00: get_/set_transceiver_status share the empty prefix,
        ptt_on/ptt_off each declare their own longer, unique prefix at
        the same (command, sub). The full-prefix (non-prefix-blind) index
        this design requires resolves all four -- see this session's
        report to the coordinator for why that reads against plan §2(d)'s
        "genuine residual, decides nothing" framing of the fourth name."""
        index = get_radio_profile("x6100").reverse_index
        assert index is not None
        assert index.resolve(0x1C, 0x00, b"").name == "get_transceiver_status"
        assert index.resolve(0x1C, 0x00, b"\x02").name == "set_transceiver_status"
        assert index.resolve(0x1C, 0x00, b"\x01").name == "ptt_on"
        assert index.resolve(0x1C, 0x00, b"\x00").name == "ptt_off"

    def test_unknown_command_is_unrecognized(self) -> None:
        index = get_radio_profile("ic7300").reverse_index
        assert index is not None
        assert index.resolve(0xFF, None, b"").unrecognized


# ── regenerable census baseline ──


def _census_rows() -> dict[str, tuple[int, int, int]]:
    rows: dict[str, tuple[int, int, int]] = {}
    for model, profile in _civ_profiles().items():
        command_map = profile.command_map
        assert command_map is not None
        groups = _group_names(command_map)
        names = len(command_map)
        keys = sum(len(prefix_groups) for prefix_groups in groups.values())
        colliding = sum(
            len(names_)
            for prefix_groups in groups.values()
            for names_ in prefix_groups.values()
            if len(names_) > 1
        )
        rows[model] = (names, keys, colliding)
    return rows


def _render_census(rows: dict[str, tuple[int, int, int]]) -> str:
    header = CENSUS_FILE.read_text(encoding="utf-8").splitlines()
    header = [line for line in header if line.startswith("#")]
    body = [f"{model}\t{n}\t{k}\t{c}" for model, (n, k, c) in sorted(rows.items())]
    return "\n".join(header + body) + "\n"


def _read_census() -> dict[str, tuple[int, int, int]]:
    rows: dict[str, tuple[int, int, int]] = {}
    for line in CENSUS_FILE.read_text(encoding="utf-8").splitlines():
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        model, names, keys, colliding = line.split("\t")
        rows[model] = (int(names), int(keys), int(colliding))
    return rows


def test_census_matches_baseline() -> None:
    measured = _census_rows()
    if os.environ.get(REGEN_ENV, "") not in {"", "0"}:
        CENSUS_FILE.write_text(_render_census(measured), encoding="utf-8")
        pytest.skip(f"{REGEN_ENV} set: baseline rewritten, not checked")
    assert measured == _read_census()
