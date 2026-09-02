"""Reverse command index (MOR-1993 Z2,
`docs/plans/2026-09-01-reverse-command-index.md` §4.1/§5).

``ReverseCommandIndex`` (``commands/command_map.py``) is the conservative
inverse of ``CommandMap``: given an incoming ``(command, sub, data)`` frame,
return every declared name compatible with those bytes. This file has three
parts:

- ``test_all_profile_probe_census_returns_exact_viable_names``: independently
  derives the viable name set for every declared prefix plus representative
  reply payloads on every CI-V profile. ``ftx1``/``tx500`` are CAT radios
  (Yaesu text protocol) and carry no CI-V ``[commands]`` entries.
- ``TestKnownCollisionShapes``: a handful of structurally distinct cases
  pinned individually, so a regression names the specific shape that broke
  instead of only the aggregate counts below.
- ``test_census_matches_baseline``: per-profile name/key/collision counts,
  regenerable rather than hand-maintained::

    RIGPLANE_REGEN_REVERSE_COMMAND_INDEX_CENSUS=1 uv run pytest tests/test_reverse_command_index.py
"""

from __future__ import annotations

import itertools
import os
import pathlib

import pytest

from rigplane.commands._frame import decode_wire_tuple
from rigplane.commands._frame import parse_civ_frame
from rigplane.commands.bound import BoundCommands
from rigplane.commands.command_map import (
    CommandMap,
    ReverseCommandIndex,
    ReverseLookupResult,
)
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


def _viable_names(
    groups: Groups,
    command: int,
    sub: int | None,
    data: bytes,
) -> frozenset[str]:
    """Names whose independently grouped declared prefix matches *data*."""
    return frozenset(
        name
        for prefix, names in groups.get((command, sub), {}).items()
        if data.startswith(prefix)
        for name in names
    )


def _result_names(result: ReverseLookupResult) -> frozenset[str]:
    if result.name is not None:
        return frozenset({result.name})
    return result.candidates


def test_result_contract_accepts_exactly_three_states() -> None:
    unrecognized = ReverseLookupResult()
    resolved = ReverseLookupResult(name="get_x")
    ambiguous = ReverseLookupResult(candidates=frozenset({"get_x", "set_x"}))

    assert unrecognized.unrecognized and not unrecognized.resolved
    assert resolved.resolved and not resolved.ambiguous
    assert ambiguous.ambiguous and not ambiguous.unrecognized
    same_ambiguity = ReverseLookupResult(candidates=frozenset({"set_x", "get_x"}))
    assert same_ambiguity == ambiguous
    assert hash(same_ambiguity) == hash(ambiguous)


@pytest.mark.parametrize(
    ("name", "candidates"),
    (
        (None, frozenset({"get_x"})),
        ("get_x", frozenset({"get_x", "set_x"})),
    ),
)
def test_result_contract_rejects_invalid_states(
    name: str | None, candidates: frozenset[str]
) -> None:
    with pytest.raises(ValueError):
        ReverseLookupResult(name=name, candidates=candidates)


def test_index_equality_hash_and_resolution_ignore_declaration_order() -> None:
    declarations = (
        ("get_x", (0x14, 0x01)),
        ("set_x", (0x14, 0x01)),
        ("set_x_on", (0x14, 0x01, 0x01)),
    )
    indexes = [
        ReverseCommandIndex(CommandMap(dict(permutation)))
        for permutation in itertools.permutations(declarations)
    ]

    assert all(index == indexes[0] for index in indexes)
    assert len({hash(index) for index in indexes}) == 1
    for index in indexes:
        assert index.resolve(0x14, 0x01, b"\x01").candidates == frozenset(
            {"get_x", "set_x", "set_x_on"}
        )


def test_index_equality_detects_semantic_prefix_difference() -> None:
    baseline = ReverseCommandIndex(
        CommandMap({"get_x": (0x14, 0x01), "set_x": (0x14, 0x01)})
    )
    changed = ReverseCommandIndex(
        CommandMap({"get_x": (0x14, 0x01), "set_x": (0x14, 0x01, 0x01)})
    )

    assert baseline != changed


def test_all_profile_probe_census_returns_exact_viable_names() -> None:
    probes = 0
    for model, profile in _civ_profiles().items():
        command_map = profile.command_map
        index = profile.reverse_index
        assert command_map is not None and index is not None, model
        groups = _group_names(command_map)
        for (command, sub), prefix_groups in groups.items():
            for prefix in prefix_groups:
                for data in (prefix, prefix + b"\x00", prefix + b"\x01"):
                    probes += 1
                    result = index.resolve(command, sub, data)
                    expected = _viable_names(groups, command, sub, data)
                    assert _result_names(result) == expected, (
                        f"{model}: command={command:#x} sub={sub} data={data!r}"
                    )
                    assert result.resolved is (len(expected) == 1)
                    assert result.ambiguous is (len(expected) > 1)
    assert probes > 0


def test_all_strict_prefix_overlaps_preserve_shorter_candidates() -> None:
    overlaps = 0
    for model, profile in _civ_profiles().items():
        command_map = profile.command_map
        index = profile.reverse_index
        assert command_map is not None and index is not None, model
        groups = _group_names(command_map)
        for (command, sub), prefix_groups in groups.items():
            prefixes = tuple(prefix_groups)
            for shorter in prefixes:
                for longer in prefixes:
                    if len(shorter) >= len(longer) or not longer.startswith(shorter):
                        continue
                    overlaps += 1
                    result = index.resolve(command, sub, longer)
                    expected = _viable_names(groups, command, sub, longer)
                    assert _result_names(result) == expected, (
                        f"{model}: command={command:#x} sub={sub} "
                        f"shorter={shorter!r} longer={longer!r}"
                    )
                    assert frozenset(prefix_groups[shorter]) <= expected
    assert overlaps == 20


class TestKnownCollisionShapes:
    """Individually pinned, structurally distinct cases (established
    directly from ``rigs/*.toml`` -- see each test's own docstring)."""

    @pytest.mark.parametrize(
        ("reply_data", "setter"),
        ((b"\x00", "set_dual_watch_off"), (b"\x01", "set_dual_watch_on")),
    )
    def test_ic9700_dual_watch_reply_retains_getter(
        self, reply_data: bytes, setter: str
    ) -> None:
        profile = get_radio_profile("ic9700")
        command_map = profile.command_map
        index = profile.reverse_index
        assert command_map is not None and index is not None

        request = parse_civ_frame(
            BoundCommands(command_map).get_dual_watch(to_addr=profile.civ_addr)
        )
        assert (request.command, request.sub, request.data) == (0x16, 0x59, b"")

        result = index.resolve(0x16, 0x59, reply_data)
        assert result.ambiguous
        assert result.candidates == frozenset({"get_dual_watch", setter})

    def test_ic7300_af_level_reply_retains_getter(self) -> None:
        profile = get_radio_profile("ic7300")
        command_map = profile.command_map
        index = profile.reverse_index
        assert command_map is not None and index is not None
        commands = BoundCommands(command_map)

        get_request = parse_civ_frame(commands.get_af_level(to_addr=profile.civ_addr))
        set_request = parse_civ_frame(
            commands.set_af_level(128, to_addr=profile.civ_addr)
        )
        assert (get_request.command, get_request.sub, get_request.data) == (
            0x14,
            0x01,
            b"",
        )
        assert (set_request.command, set_request.sub, set_request.data) == (
            0x14,
            0x01,
            b"\x01\x28",
        )

        result = index.resolve(0x14, 0x01, b"\x01\x28")
        assert result.ambiguous
        assert result.candidates == frozenset({"get_af_level", "set_af_level"})

    def test_menu_family_does_not_collapse_to_one_key(self) -> None:
        """IC-7300 declares 84 distinct prefixes under 0x1A 0x05 (168
        names); a (command, sub)-blind index would answer every one of
        them identically. Two different addresses must resolve
        differently."""
        index = get_radio_profile("ic7300").reverse_index
        assert index is not None
        first = index.resolve(0x1A, 0x05, b"\x00\x01")
        second = index.resolve(0x1A, 0x05, b"\x00\x02")
        assert first.candidates == frozenset({"get_ssb_rx_hpflpf", "set_ssb_rx_hpflpf"})
        assert second.candidates == frozenset({"get_ssb_rx_bass", "set_ssb_rx_bass"})

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
        assert result.candidates == frozenset(
            {
                "get_antenna",
                "set_antenna",
                "get_rx_antenna_ant2",
                "set_rx_antenna_ant2",
            }
        )

    def test_transceiver_status_family_preserves_strict_prefix_candidates(
        self,
    ) -> None:
        """rigs/x6100.toml (and ic705/x6200) declares four names under
        0x1C 0x00: get_/set_transceiver_status share the empty prefix,
        ptt_on/ptt_off each declare their own longer, unique prefix at
        the same (command, sub). The full-prefix (non-prefix-blind) index
        this design requires keeps the bare names viable when the incoming
        byte also matches either PTT tuple."""
        index = get_radio_profile("x6100").reverse_index
        assert index is not None
        assert index.resolve(0x1C, 0x00, b"").candidates == frozenset(
            {"get_transceiver_status", "set_transceiver_status"}
        )
        assert index.resolve(0x1C, 0x00, b"\x02").candidates == frozenset(
            {"get_transceiver_status", "set_transceiver_status"}
        )
        assert index.resolve(0x1C, 0x00, b"\x01").candidates == frozenset(
            {"get_transceiver_status", "set_transceiver_status", "ptt_on"}
        )
        assert index.resolve(0x1C, 0x00, b"\x00").candidates == frozenset(
            {"get_transceiver_status", "set_transceiver_status", "ptt_off"}
        )

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
