"""Tests for _state_queries.build_state_queries()."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from icom_lan.runtime._state_queries import build_state_queries
from icom_lan.profiles import resolve_radio_profile


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
        """IC-7300 has 1 receiver — freq/mode only for rx 0."""
        profile = resolve_radio_profile(model="IC-7300")
        queries = build_state_queries(profile, _ic7300_caps())
        freq_receivers = [q[2] for q in queries if q[0] == 0x25]
        assert freq_receivers == [0]

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


# ------------------------------------------------------------------
# CoreRadio._fetch_initial_state tests
# ------------------------------------------------------------------


class TestFetchInitialState:
    """Tests for CoreRadio._fetch_initial_state method."""

    @pytest.fixture(autouse=True)
    def _no_real_pacing(self):
        """Skip the 12ms inter-query sleep (~1.2s per test) — tests assert
        call counts and flag state, not real pacing."""
        with patch("icom_lan.radio.asyncio.sleep", new=AsyncMock()):
            yield

    @pytest.fixture
    def radio(self):
        from icom_lan.radio import CoreRadio

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
    async def test_sets_flag_on_success(self, radio) -> None:
        await radio._fetch_initial_state()
        assert radio._initial_state_fetched is True

    @pytest.mark.asyncio
    async def test_sets_flag_on_failure(self, radio) -> None:
        with patch(
            "icom_lan._state_queries.build_state_queries",
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
