"""MOR-4252: the published query contracts are the producer's single source.

Two shipped regressions of the same class — ``state_delta=2`` on
``/api/v1/ws`` (MOR-4206, phones refused with ``query_not_allowed`` in
beta.14) and ``GET /api/v1/memories?count&group&start`` (Core v3.3.0,
PRO-1H) — came from a gateway mirroring Core's query parsing by hand.
Core now publishes the contracts from the top-level ``rigplane`` package
and consumes them in its own parsers.

These tests pin both directions:

* the producer parsers read exactly the keys the contracts list (adding a
  query key to Core's parse path without listing it fails here), and
* the shipped frontend URL builders emit only keys the contracts list,

plus the two acceptance levels: Core's own lenient parse (unknown keys
ignored, ``state_delta`` other than exactly ``"2"`` degrades to delta
version 1) and the strict gateway-grade parse that refuses anything
outside the contract by returning ``None``.
"""

from __future__ import annotations

import inspect
import re
from pathlib import Path
from typing import get_args

import pytest

import rigplane
from rigplane import (
    CONTROL_WS_QUERY_KEYS,
    MEMORIES_QUERY_KEYS,
    ClientChannelParams,
    ControllerRole,
    ControlWsQuery,
    MemoriesQuery,
)
from rigplane.core.types import MemoryProfile
from rigplane.runtime.controller_authority import ControllerError
from rigplane.web import channel_attach, memory_api
from rigplane.web.memory_api import parse_memory_query

_FRONTEND_LIB = Path(__file__).resolve().parents[2] / "frontend" / "src" / "lib"


def test_top_level_exports_are_the_canonical_objects() -> None:
    """``rigplane.X`` resolves to the same object the canonical module defines."""
    assert rigplane.ControlWsQuery is channel_attach.ControlWsQuery
    assert rigplane.CONTROL_WS_QUERY_KEYS is channel_attach.CONTROL_WS_QUERY_KEYS
    assert rigplane.ControllerRole is channel_attach.ControllerRole
    assert rigplane.MemoriesQuery is memory_api.MemoriesQuery
    assert rigplane.MEMORIES_QUERY_KEYS is memory_api.MEMORIES_QUERY_KEYS


def test_controller_role_literal() -> None:
    assert set(get_args(ControllerRole)) == {"primary", "auxiliary"}


def _query_key_literals(*objects: object) -> set[str]:
    """Every ``mapping.get("key")`` string literal in the objects' source."""
    keys: set[str] = set()
    for obj in objects:
        keys |= set(
            re.findall(r"""\.get\(\s*["']([a-z0-9_]+)["']""", inspect.getsource(obj))
        )
    return keys


def test_ws_parser_reads_exactly_the_contract_keys() -> None:
    """Adding a key to the WS parse path without listing it fails here."""
    keys = _query_key_literals(ControlWsQuery, ClientChannelParams.from_ws)
    assert keys == set(CONTROL_WS_QUERY_KEYS), (
        f"the control-WS parse path reads {sorted(keys)} but the published "
        f"contract lists {sorted(CONTROL_WS_QUERY_KEYS)}; update "
        f"CONTROL_WS_QUERY_KEYS (and ControlWsQuery.parse) with the new key"
    )


def test_memory_parser_reads_exactly_the_contract_keys() -> None:
    """Adding a key to the /api/v1/memories parse path fails here likewise."""
    keys = _query_key_literals(MemoriesQuery, parse_memory_query)
    assert keys == set(MEMORIES_QUERY_KEYS), (
        f"the memories parse path reads {sorted(keys)} but the published "
        f"contract lists {sorted(MEMORIES_QUERY_KEYS)}; update "
        f"MEMORIES_QUERY_KEYS (and MemoriesQuery.parse) with the new key"
    )


def test_frontend_ws_client_emits_only_contract_keys() -> None:
    """The shipped ws-client URL builder never emits an unlisted key."""
    source = (_FRONTEND_LIB / "transport" / "ws-client.ts").read_text(encoding="utf-8")
    keys = set(re.findall(r"withWsSearchParam\([^)]*?['\"]([a-z0-9_]+)['\"]", source))
    assert keys, "ws-client.ts no longer appends query keys via withWsSearchParam"
    assert keys <= set(CONTROL_WS_QUERY_KEYS), (
        f"ws-client.ts appends {sorted(keys - set(CONTROL_WS_QUERY_KEYS))}, "
        f"which CONTROL_WS_QUERY_KEYS does not list; a strict gateway would "
        f"refuse the shipped UI"
    )


def test_frontend_memory_page_request_emits_only_contract_keys() -> None:
    """The MemoryPageRequest fields are exactly the memories query keys."""
    source = (_FRONTEND_LIB / "transport" / "http-client.ts").read_text(
        encoding="utf-8"
    )
    match = re.search(r"interface MemoryPageRequest\s*\{([^}]*)\}", source)
    assert match is not None, "MemoryPageRequest interface not found"
    keys = set(re.findall(r"(\w+)\?:\s*number", match.group(1)))
    assert keys, "MemoryPageRequest declares no query fields"
    assert keys <= set(MEMORIES_QUERY_KEYS), (
        f"fetchMemoryPage can emit {sorted(keys - set(MEMORIES_QUERY_KEYS))}, "
        f"which MEMORIES_QUERY_KEYS does not list"
    )


class TestControlWsQueryAccept:
    """Core's own parse: lenient for released clients (MOR-4252)."""

    def test_defaults(self) -> None:
        assert ControlWsQuery.accept(None) == ControlWsQuery(
            controller_role="primary", state_delta=1
        )
        assert ControlWsQuery.accept({}) == ControlWsQuery(
            controller_role="primary", state_delta=1
        )

    def test_role_and_delta(self) -> None:
        parsed = ControlWsQuery.accept(
            {"controller_role": ["auxiliary"], "state_delta": ["2"]}
        )
        assert parsed.controller_role == "auxiliary"
        assert parsed.state_delta == 2

    def test_state_delta_other_value_degrades_to_1(self) -> None:
        """``state_delta=1`` (or any non-"2") degrades; it is not refused."""
        assert ControlWsQuery.accept({"state_delta": ["1"]}).state_delta == 1
        assert ControlWsQuery.accept({"state_delta": ["3"]}).state_delta == 1

    def test_repeated_state_delta_degrades_to_1(self) -> None:
        parsed = ControlWsQuery.accept({"state_delta": ["2", "2"]})
        assert parsed.state_delta == 1

    def test_unknown_key_is_ignored(self) -> None:
        parsed = ControlWsQuery.accept({"state_delta": ["2"], "bogus": ["x"]})
        assert parsed == ControlWsQuery(controller_role="primary", state_delta=2)

    def test_repeated_controller_role_is_refused(self) -> None:
        with pytest.raises(ControllerError) as excinfo:
            ControlWsQuery.accept({"controller_role": ["primary", "auxiliary"]})
        assert excinfo.value.code == "invalid_request"
        assert excinfo.value.status == 400


class TestControlWsQueryParse:
    """The strict contract a gateway may enforce: ``None`` means refuse."""

    def test_absent_keys_take_the_core_defaults(self) -> None:
        assert ControlWsQuery.parse(None) == ControlWsQuery(
            controller_role="primary", state_delta=1
        )
        assert ControlWsQuery.parse({}) == ControlWsQuery(
            controller_role="primary", state_delta=1
        )

    def test_full_contract_query(self) -> None:
        parsed = ControlWsQuery.parse(
            {"controller_role": ["primary"], "state_delta": ["2"]}
        )
        assert parsed == ControlWsQuery(controller_role="primary", state_delta=2)

    def test_unknown_key_is_refused(self) -> None:
        assert ControlWsQuery.parse({"bogus": ["x"]}) is None

    def test_repeated_key_is_refused(self) -> None:
        assert ControlWsQuery.parse({"controller_role": ["primary", "primary"]}) is None
        assert ControlWsQuery.parse({"state_delta": ["2", "2"]}) is None

    def test_role_outside_the_literal_is_refused(self) -> None:
        assert ControlWsQuery.parse({"controller_role": ["bogus"]}) is None

    def test_state_delta_other_than_2_is_refused(self) -> None:
        assert ControlWsQuery.parse({"state_delta": ["1"]}) is None
        assert ControlWsQuery.parse({"state_delta": [""]}) is None


class TestFromWsConsumesTheContract:
    """``ClientChannelParams.from_ws`` parses through the same definition."""

    def test_control_path(self) -> None:
        params = ClientChannelParams.from_ws(
            {"controller_role": ["auxiliary"], "state_delta": ["2"]},
            [],
            "/api/v1/ws",
        )
        assert params.controller_role == "auxiliary"
        assert params.state_delta == 2
        assert params.controller_key is None

    def test_control_path_refuses_a_repeated_role(self) -> None:
        with pytest.raises(ControllerError) as excinfo:
            ClientChannelParams.from_ws(
                {"controller_role": ["primary", "auxiliary"]}, [], "/api/v1/ws"
            )
        assert excinfo.value.code == "invalid_request"
        assert excinfo.value.status == 400

    def test_other_paths_force_auxiliary_and_keep_state_delta(self) -> None:
        params = ClientChannelParams.from_ws(
            {"controller_role": ["primary"], "state_delta": ["2"]},
            [],
            "/api/v1/scope",
        )
        assert params.controller_role == "auxiliary"
        assert params.state_delta == 2

    def test_other_paths_ignore_a_repeated_role(self) -> None:
        """Only ``/api/v1/ws`` refuses a repeated role (historical rule)."""
        params = ClientChannelParams.from_ws(
            {"controller_role": ["primary", "auxiliary"]}, [], "/api/v1/audio"
        )
        assert params.controller_role == "auxiliary"


_PROFILE = MemoryProfile("group_channel", 0, 99, 0, 99)


class TestMemoriesQueryAccept:
    """Core's own parse: lenient for released clients (MOR-4252)."""

    def test_defaults(self) -> None:
        assert MemoriesQuery.accept({}) == MemoriesQuery(
            group=None, start=None, count=None
        )

    def test_values(self) -> None:
        parsed = MemoriesQuery.accept(
            {"group": ["7"], "start": ["25"], "count": ["25"]}
        )
        assert parsed == MemoriesQuery(group=7, start=25, count=25)

    def test_repeated_key_keeps_the_first_value(self) -> None:
        assert MemoriesQuery.accept({"count": ["10", "20"]}).count == 10

    def test_unknown_key_is_ignored(self) -> None:
        assert MemoriesQuery.accept({"page": ["2"]}) == MemoriesQuery()

    def test_non_integer_is_a_value_error(self) -> None:
        with pytest.raises(ValueError, match="count must be an integer"):
            MemoriesQuery.accept({"count": ["abc"]})


class TestMemoriesQueryParse:
    """The strict contract a gateway may enforce: ``None`` means refuse."""

    def test_empty(self) -> None:
        assert MemoriesQuery.parse({}) == MemoriesQuery()

    def test_full_contract_query(self) -> None:
        parsed = MemoriesQuery.parse({"group": ["0"], "start": ["25"], "count": ["25"]})
        assert parsed == MemoriesQuery(group=0, start=25, count=25)

    def test_unknown_key_is_refused(self) -> None:
        assert MemoriesQuery.parse({"page": ["2"]}) is None

    def test_repeated_key_is_refused(self) -> None:
        assert MemoriesQuery.parse({"count": ["25", "25"]}) is None

    def test_non_integer_is_refused(self) -> None:
        assert MemoriesQuery.parse({"start": ["abc"]}) is None

    def test_count_outside_the_page_bounds_is_refused(self) -> None:
        assert MemoriesQuery.parse({"count": ["0"]}) is None
        assert MemoriesQuery.parse({"count": ["51"]}) is None
        assert MemoriesQuery.parse({"count": ["50"]}) is not None


class TestParseMemoryQueryConsumesTheContract:
    """The producer applies the profile bounds on top of the contract."""

    def test_page(self) -> None:
        assert parse_memory_query(
            {"group": ["0"], "start": ["25"], "count": ["25"]}, _PROFILE
        ) == (0, list(range(25, 50)))

    def test_unknown_key_is_ignored_end_to_end(self) -> None:
        group, channels = parse_memory_query({"page": ["2"]}, _PROFILE)
        assert group == 0
        assert channels == list(range(25))

    def test_profile_bounds_are_still_enforced(self) -> None:
        with pytest.raises(ValueError, match="group must be 0-99"):
            parse_memory_query({"group": ["100"]}, _PROFILE)
        with pytest.raises(ValueError, match="count must be 1-50"):
            parse_memory_query({"count": ["51"]}, _PROFILE)
