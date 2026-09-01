"""Tests for _state_queries.build_state_queries()."""

from __future__ import annotations

from collections import Counter
from unittest.mock import AsyncMock, call, patch

import pytest

from rigplane.commands._frame import build_civ_frame, decode_wire_tuple
from rigplane.commands.scope import (
    SCOPE_RECEIVER_SELECTOR_SUBS,
    SCOPE_SELECTOR_MAIN,
)
from rigplane.runtime._state_queries import build_state_queries
from rigplane.profiles import resolve_radio_profile

# The 0x27 read sub-commands the sweep sends, split by whether the frame
# carries the one-byte Main/Sub scope selector.  Spelled out here rather
# than imported from the production constant, so a change to that set has
# to be made twice, deliberately (MOR-1981).
_SELECTOR_SUBS = frozenset({0x14, 0x15, 0x16, 0x17, 0x19, 0x1A, 0x1D, 0x1F})
_BARE_SUBS = frozenset({0x12, 0x13, 0x1B, 0x1C})


def _scope_queries(
    queries: list[tuple[int, int | bytes | None, int | None]],
) -> list[tuple[int, int | bytes | None, int | None]]:
    return [q for q in queries if q[0] == 0x27]


def _ic7610_caps() -> set[str]:
    """Return the full capability set for IC-7610."""
    profile = resolve_radio_profile(model="IC-7610")
    return set(profile.capabilities)


def _ic7300_caps() -> set[str]:
    """Return the full capability set for IC-7300."""
    profile = resolve_radio_profile(model="IC-7300")
    return set(profile.capabilities)


class TestBuildStateQueries:
    """Verify build_state_queries produces correct query lists."""

    def test_returns_list_of_3_tuples(self) -> None:
        profile = resolve_radio_profile(model="IC-7610")
        queries = build_state_queries(profile, _ic7610_caps())
        assert isinstance(queries, list)
        assert len(queries) > 0
        for q in queries:
            assert isinstance(q, tuple)
            assert len(q) == 3

    def test_ic7610_includes_dual_receiver_queries(self) -> None:
        """IC-7610 has 2 receivers — freq/mode must appear for rx 0 and rx 1."""
        profile = resolve_radio_profile(model="IC-7610")
        queries = build_state_queries(profile, _ic7610_caps())
        freq_receivers = [q[2] for q in queries if q[0] == 0x25]
        assert 0 in freq_receivers
        assert 1 in freq_receivers

    def test_ic7300_single_receiver(self) -> None:
        """IC-7300 has selected/unselected reads on its one receiver."""
        profile = resolve_radio_profile(model="IC-7300")
        queries = build_state_queries(profile, _ic7300_caps())
        freq_queries = [q for q in queries if q[0] == 0x25]
        assert freq_queries == [(0x25, None, 0), (0x25, 0x01, None)]
        assert (0x07, 0xD2, None) not in queries
        assert (0x07, 0xC2, None) not in queries

    def test_ic7610_includes_scope_queries(self) -> None:
        """IC-7610 should have scope sub-commands (0x27)."""
        profile = resolve_radio_profile(model="IC-7610")
        queries = build_state_queries(profile, _ic7610_caps())
        scope_queries = [q for q in queries if q[0] == 0x27]
        assert len(scope_queries) > 0

    def test_ic7300_has_scope_queries_if_capable(self) -> None:
        """IC-7300 has scope capability — should include 0x27 queries."""
        profile = resolve_radio_profile(model="IC-7300")
        queries = build_state_queries(profile, _ic7300_caps())
        scope_queries = [q for q in queries if q[0] == 0x27]
        if "scope" in _ic7300_caps():
            assert len(scope_queries) > 0
        else:
            assert len(scope_queries) == 0

    def test_global_queries_present(self) -> None:
        """Power, PTT, split, RIT etc. must be in every query list."""
        profile = resolve_radio_profile(model="IC-7610")
        queries = build_state_queries(profile, _ic7610_caps())
        cmds = {(q[0], q[1]) for q in queries}
        assert (0x18, None) in cmds  # Power status
        assert (0x1C, 0x00) in cmds  # PTT
        assert (0x0F, None) in cmds  # Split
        assert (0x21, 0x00) in cmds  # RIT frequency

    def test_serial_adds_meter_queries(self) -> None:
        """Serial backends should include ALC/comp/VD/Id meter queries."""
        profile = resolve_radio_profile(model="IC-7610")
        lan_queries = build_state_queries(profile, _ic7610_caps(), is_serial=False)
        serial_queries = build_state_queries(profile, _ic7610_caps(), is_serial=True)
        # Serial should have more queries (the extra meters)
        assert len(serial_queries) > len(lan_queries)
        serial_cmds = {(q[0], q[1]) for q in serial_queries}
        assert (0x15, 0x13) in serial_cmds  # ALC meter
        assert (0x15, 0x14) in serial_cmds  # Compressor meter
        assert (0x15, 0x15) in serial_cmds  # VD
        assert (0x15, 0x16) in serial_cmds  # Id

    def test_missing_capability_skips_query(self) -> None:
        """If a capability is missing, its per-rx queries should be skipped."""
        profile = resolve_radio_profile(model="IC-7610")
        full_caps = _ic7610_caps()
        # Remove 'nb' capability
        reduced_caps = full_caps - {"nb"}
        full_queries = build_state_queries(profile, full_caps)
        reduced_queries = build_state_queries(profile, reduced_caps)
        # NB queries (0x16/0x22 and 0x14/0x12) should be missing
        nb_in_full = [q for q in full_queries if q[0] == 0x16 and q[1] == 0x22]
        nb_in_reduced = [q for q in reduced_queries if q[0] == 0x16 and q[1] == 0x22]
        assert len(nb_in_full) > 0
        assert len(nb_in_reduced) == 0

    def test_empty_capabilities_still_has_globals(self) -> None:
        """Even with no capabilities, global queries should be present."""
        profile = resolve_radio_profile(model="IC-7610")
        queries = build_state_queries(profile, set())
        # Should still have freq/mode + globals
        assert len(queries) > 0
        cmds = {(q[0], q[1]) for q in queries}
        assert (0x18, None) in cmds  # Power status

    def test_deterministic_output(self) -> None:
        """Same inputs should produce identical output."""
        profile = resolve_radio_profile(model="IC-7610")
        caps = _ic7610_caps()
        q1 = build_state_queries(profile, caps)
        q2 = build_state_queries(profile, caps)
        assert q1 == q2

    def test_ic9700_dual_watch_query_uses_profile_wire_tuple(self) -> None:
        profile = resolve_radio_profile(model="IC-9700")
        command, sub, prefix = decode_wire_tuple(
            profile.command_map.get("get_dual_watch")
        )
        assert (command, sub, prefix) == (0x16, 0x59, b"")

        dual_watch_delta = Counter(
            build_state_queries(profile, {"dual_watch"})
        ) - Counter(build_state_queries(profile, set()))

        assert dual_watch_delta == Counter({(0x16, 0x59, None): 1})

    def test_ic7610_dual_watch_query_preserves_wire_tuple(self) -> None:
        profile = resolve_radio_profile(model="IC-7610")
        command, sub, prefix = decode_wire_tuple(
            profile.command_map.get("get_dual_watch")
        )
        assert (command, sub, prefix) == (0x07, None, b"\xc2")

        dual_watch_delta = Counter(
            build_state_queries(profile, {"dual_watch"})
        ) - Counter(build_state_queries(profile, set()))

        assert dual_watch_delta == Counter({(0x07, 0xC2, None): 1})
        assert (0x07, None, None) not in dual_watch_delta
        assert build_civ_frame(0x98, 0xE0, 0x07, sub=0xC2) == bytes.fromhex(
            "FE FE 98 E0 07 C2 FD"
        )


class TestScopeReceiverSelector:
    """MOR-1981: which 0x27 reads carry the Main/Sub scope selector byte.

    The separation is the point.  On a sub-command that takes no selector
    the extra ``0x00`` is not an ignored byte but the first data byte of a
    WRITE, so a mutation that widens the set has to fail here.
    """

    def test_constant_holds_exactly_the_eight(self) -> None:
        assert SCOPE_RECEIVER_SELECTOR_SUBS == _SELECTOR_SUBS
        assert len(SCOPE_RECEIVER_SELECTOR_SUBS) == 8
        assert SCOPE_SELECTOR_MAIN == 0x00

    def test_the_bare_four_are_not_in_the_set(self) -> None:
        """``27 1C 00`` reads as SET center_type=0 (Filter center), not a query."""
        assert _BARE_SUBS.isdisjoint(SCOPE_RECEIVER_SELECTOR_SUBS)
        assert len(_BARE_SUBS) == 4

    def test_fixed_edge_is_not_in_the_set(self) -> None:
        """0x1E takes ``<frequency range><edge number>``, not the selector.

        ``00`` is not a legal frequency range -- they start at ``01`` -- so
        ``27 1E 00`` is neither a bare read nor a valid one.  The pair is
        built by ``commands/scope.py: get_scope_fixed_edge``.
        """
        assert 0x1E not in SCOPE_RECEIVER_SELECTOR_SUBS

    def test_sweep_splits_scope_reads_eight_selector_four_bare(self) -> None:
        profile = resolve_radio_profile(model="IC-7300")
        scope = _scope_queries(build_state_queries(profile, _ic7300_caps()))

        with_selector = {
            sub[0] for _, sub, _ in scope if isinstance(sub, (bytes, bytearray))
        }
        bare = {sub for _, sub, _ in scope if isinstance(sub, int)}

        assert with_selector == _SELECTOR_SUBS
        assert bare == _BARE_SUBS
        assert len(scope) == len(_SELECTOR_SUBS) + len(_BARE_SUBS) == 12
        # The scope reads are the only queries this builder gives a
        # payload-carrying sub element to.
        queries = build_state_queries(profile, _ic7300_caps())
        assert [
            cmd for cmd, sub, _ in queries if isinstance(sub, (bytes, bytearray))
        ] == [0x27] * len(_SELECTOR_SUBS)

    def test_sweep_selector_is_one_byte_and_main(self) -> None:
        profile = resolve_radio_profile(model="IC-7300")
        scope = _scope_queries(build_state_queries(profile, _ic7300_caps()))

        carried = [sub for _, sub, _ in scope if isinstance(sub, (bytes, bytearray))]
        assert carried, "no scope read carried a selector"
        for sub in carried:
            assert len(sub) == 2
            assert sub[1] == SCOPE_SELECTOR_MAIN

    @pytest.mark.parametrize("model", ["IC-7300", "IC-7610"])
    def test_a_payload_carrying_sub_is_never_paired_with_a_receiver(
        self, model: str
    ) -> None:
        """The selector is payload, not the cmd29 receiver slot.

        A non-``None`` third element routes the query through cmd29 in both
        senders, which is a different frame entirely, and neither carries
        the payload half of the sub element down that path:
        ``runtime/radio_initial_state.py: fetch_initial_state`` wraps the
        sub-command byte alone, and
        ``web/radio_poller.py: RadioPoller._send_one_state_query`` asserts
        the combination cannot arise.
        """
        profile = resolve_radio_profile(model=model)
        caps = set(profile.capabilities)
        queries = build_state_queries(profile, caps)

        assert _scope_queries(queries), "no scope reads to check"
        assert all(
            receiver is None
            for _, sub, receiver in queries
            if isinstance(sub, (bytes, bytearray))
        )


# ------------------------------------------------------------------
# CoreRadio._fetch_initial_state tests
# ------------------------------------------------------------------


class TestFetchInitialState:
    """Tests for CoreRadio._fetch_initial_state method."""

    @pytest.fixture(autouse=True)
    def _no_real_pacing(self):
        """Skip the 12ms inter-query sleep (~1.2s per test) — tests assert
        call counts and flag state, not real pacing."""
        with patch("rigplane.radio.asyncio.sleep", new=AsyncMock()):
            yield

    @pytest.fixture
    def radio(self):
        from rigplane.radio import CoreRadio

        with patch.object(CoreRadio, "__init__", lambda self: None):
            r = CoreRadio.__new__(CoreRadio)
            profile = resolve_radio_profile(model="IC-7610")
            r._profile = profile
            r._initial_state_fetched = False
            r.send_civ = AsyncMock()
            return r

    @pytest.mark.asyncio
    async def test_dispatches_all_queries(self, radio) -> None:
        queries = build_state_queries(radio._profile, set(radio._profile.capabilities))
        await radio._fetch_initial_state()
        assert radio.send_civ.call_count == len(queries)
        assert radio._initial_state_fetched is True

    @pytest.mark.asyncio
    async def test_single_rx_unselected_selector_is_data_not_sub_receiver(
        self, radio
    ) -> None:
        radio._profile = resolve_radio_profile(model="IC-7300")

        await radio._fetch_initial_state()

        assert (
            call(0x25, data=b"\x01", wait_response=False)
            in radio.send_civ.await_args_list
        )
        assert (
            call(0x26, data=b"\x01", wait_response=False)
            in radio.send_civ.await_args_list
        )

    @pytest.mark.asyncio
    async def test_scope_reads_carry_the_selector_only_where_it_is_legal(
        self, radio
    ) -> None:
        """MOR-1981: the eight get ``<sub> 00``, the four go out bare.

        Measured on a live IC-7300: the bare form of each of the eight is
        refused with a NAK, and the four answer.  A mutation that adds the
        byte to one of the four -- 0x1C above all, where ``27 1C 00`` is a
        SET -- fails here.
        """
        radio._profile = resolve_radio_profile(model="IC-7300")

        await radio._fetch_initial_state()

        sent = radio.send_civ.await_args_list
        for sub in sorted(_SELECTOR_SUBS):
            assert (
                call(
                    0x27,
                    sub=sub,
                    data=bytes([SCOPE_SELECTOR_MAIN]),
                    wait_response=False,
                )
                in sent
            ), f"0x27/0x{sub:02X} was not sent with a selector byte"
        for sub in sorted(_BARE_SUBS):
            assert call(0x27, sub=sub, data=b"", wait_response=False) in sent, (
                f"0x27/0x{sub:02X} was not sent bare"
            )

    @pytest.mark.asyncio
    async def test_sets_flag_on_success(self, radio) -> None:
        await radio._fetch_initial_state()
        assert radio._initial_state_fetched is True

    @pytest.mark.asyncio
    async def test_sets_flag_on_failure(self, radio) -> None:
        with patch(
            "rigplane._state_queries.build_state_queries",
            side_effect=RuntimeError("boom"),
        ):
            await radio._fetch_initial_state()
        assert radio._initial_state_fetched is True

    @pytest.mark.asyncio
    async def test_send_failure_nonfatal(self, radio) -> None:
        call_count = 0

        async def flaky_send(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count % 3 == 0:
                raise RuntimeError("transient error")

        radio.send_civ = flaky_send
        await radio._fetch_initial_state()
        assert radio._initial_state_fetched is True
        assert call_count > 0
