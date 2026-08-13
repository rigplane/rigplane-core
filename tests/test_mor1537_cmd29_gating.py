"""Tests for MOR-1537 — unify cmd29 gating for AGC/AF-mute/DIGI-SEL and the
web poller's raw-command send path.

MOR-1517 (#2434) established the pattern: a CI-V command builder must accept
an explicit ``command29: bool`` keyword-only override, and the ``radio.py``
call site must compute ``cmd29 = self._profile.supports_cmd29(cmd, sub)`` and
thread it through — never derive the wrap decision from ``receiver`` inside
the builder, and never hardcode ``command29=True`` unconditionally.

This file extends that same audit to three stragglers MOR-1517 explicitly
flagged but did not fix:

1. ``get_agc``/``set_agc`` (0x16/0x12) derived ``command29`` from
   ``receiver != RECEIVER_MAIN`` *inside the builder*, with no override.
   IC-7610 declares ``[0x16, 0x12]`` in its own ``[cmd29]`` routes, so
   ``receiver=0`` (MAIN) should be wrapped there too, matching the
   acquisition/scheduler path (which already sends wrapped AGC reads and the
   radio answers them) — this is a real behavior change on IC-7610, pinned
   here.
2. ``get_af_mute``/``set_af_mute`` (0x1A/0x09) and ``get_digisel``/
   ``set_digisel`` (0x16/0x4E) had no ``command29`` override at all — always
   unconditionally cmd29-wrapped via ``build_cmd29_frame``. Only IC-7610
   declares these commands today, and it has cmd29 routes for both, so this
   was unreachable dead-path risk rather than a live defect (confirmed by
   MOR-1517's audit) — fixed here for consistency and to remove a hidden trap
   for future profiles. ``get_digisel`` additionally had a hard
   ``CommandError`` raise for *any* receiver (including MAIN) when the
   profile lacked the cmd29 route — stricter than every other converted
   command, which unwraps for receiver=MAIN instead of raising. That extra
   raise is removed; ``_require_cmd29_route`` (a no-op for receiver=MAIN,
   which raises for receiver!=MAIN without a route) remains the sole guard,
   matching every other command in this family.
3. ``RadioPoller._send_cmd``'s wrap rule was
   ``receiver != 0 and supports_cmd29(cmd, sub)`` — never wrapping receiver 0
   even when the cmd29 route exists. Unified to plain ``supports_cmd29(cmd,
   sub)``, matching ``CoreRadio``.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from rigplane.commands import (
    build_civ_frame,
    get_af_mute,
    get_agc,
    get_digisel,
    set_af_mute,
    set_agc,
    set_digisel,
)
from rigplane.exceptions import CommandError
from rigplane.profiles import RadioProfile, resolve_radio_profile
from rigplane.radio_state import RadioState
from rigplane.types import AgcMode, CivFrame
from rigplane.web.radio_poller import CommandQueue, RadioPoller

from test_mor1517_cmd29_gating import (
    _IC7300_ADDR,
    _IC7610_ADDR,
    _connected_icom,
    _mock_expect,
    _mock_raw,
    _sent_civ,
)

_IC9700_ADDR = 0xA2


# ---------------------------------------------------------------------------
# get_agc / set_agc (0x16/0x12) — pinned defect: builder no longer derives
# command29 from receiver, radio.py now threads the profile gate through.
# ---------------------------------------------------------------------------


class TestAgcGating:
    @pytest.mark.asyncio
    async def test_set_agc_unwrapped_on_ic7300_receiver0(self) -> None:
        radio = _connected_icom(model="IC-7300")
        mock = _mock_raw(radio)
        await radio.set_agc(AgcMode.FAST, receiver=0)
        expected = set_agc(1, to_addr=_IC7300_ADDR, receiver=0, command29=False)
        assert _sent_civ(mock) == expected
        assert b"\x29" not in expected

    @pytest.mark.asyncio
    async def test_set_agc_wrapped_on_ic7610_receiver0(self) -> None:
        """Behavior change: receiver=0 (MAIN) now wraps on IC-7610 because the
        profile declares a cmd29 route for 0x16/0x12 — matching what the
        acquisition path already sends."""
        radio = _connected_icom(model="IC-7610")
        mock = _mock_raw(radio)
        await radio.set_agc(AgcMode.FAST, receiver=0)
        expected = set_agc(1, to_addr=_IC7610_ADDR, receiver=0, command29=True)
        assert _sent_civ(mock) == expected
        assert expected[4] == 0x29

    @pytest.mark.asyncio
    async def test_set_agc_wrapped_on_ic7610_receiver1_unchanged(self) -> None:
        radio = _connected_icom(model="IC-7610")
        mock = _mock_raw(radio)
        await radio.set_agc(AgcMode.FAST, receiver=1)
        expected = set_agc(1, to_addr=_IC7610_ADDR, receiver=1, command29=True)
        assert _sent_civ(mock) == expected

    @pytest.mark.asyncio
    async def test_set_agc_unwrapped_on_ic9700_receiver0(self) -> None:
        """IC-9700 declares routes = [] -> no cmd29 route for anything, so
        MAIN stays plain (unchanged before/after this fix)."""
        radio = _connected_icom(model="IC-9700")
        mock = _mock_raw(radio)
        await radio.set_agc(AgcMode.FAST, receiver=0)
        expected = set_agc(1, to_addr=_IC9700_ADDR, receiver=0, command29=False)
        assert _sent_civ(mock) == expected

    @pytest.mark.asyncio
    async def test_set_agc_raises_on_ic9700_receiver1_dual_rx_guard(self) -> None:
        """Dual-RX guard preserved: SUB targeting with no cmd29 route raises,
        same as every other command in this family."""
        radio = _connected_icom(model="IC-9700")
        _mock_raw(radio)
        with pytest.raises(CommandError, match="no cmd29 route"):
            await radio.set_agc(AgcMode.FAST, receiver=1)

    @pytest.mark.asyncio
    async def test_get_agc_unwrapped_on_ic7300_receiver0(self) -> None:
        radio = _connected_icom(model="IC-7300")
        response = CivFrame(
            to_addr=0xE0, from_addr=_IC7300_ADDR, command=0x16, sub=0x12, data=b"\x01"
        )
        mock = _mock_expect(radio, response)
        value = await radio.get_agc(receiver=0)
        expected = get_agc(to_addr=_IC7300_ADDR, receiver=0, command29=False)
        assert _sent_civ(mock) == expected
        assert value == AgcMode.FAST

    @pytest.mark.asyncio
    async def test_get_agc_wrapped_on_ic7610_receiver0(self) -> None:
        """Behavior change: same as set_agc — MAIN now wraps on IC-7610."""
        radio = _connected_icom(model="IC-7610")
        response = CivFrame(
            to_addr=0xE0, from_addr=_IC7610_ADDR, command=0x16, sub=0x12, data=b"\x01"
        )
        mock = _mock_expect(radio, response)
        value = await radio.get_agc(receiver=0)
        expected = get_agc(to_addr=_IC7610_ADDR, receiver=0, command29=True)
        assert _sent_civ(mock) == expected
        assert expected[4] == 0x29
        assert value == AgcMode.FAST

    @pytest.mark.asyncio
    async def test_get_agc_wrapped_on_ic7610_receiver1_unchanged(self) -> None:
        radio = _connected_icom(model="IC-7610")
        response = CivFrame(
            to_addr=0xE0, from_addr=_IC7610_ADDR, command=0x16, sub=0x12, data=b"\x02"
        )
        mock = _mock_expect(radio, response)
        value = await radio.get_agc(receiver=1)
        expected = get_agc(to_addr=_IC7610_ADDR, receiver=1, command29=True)
        assert _sent_civ(mock) == expected
        assert value == AgcMode.MID

    @pytest.mark.asyncio
    async def test_get_agc_unwrapped_on_ic9700_receiver0(self) -> None:
        radio = _connected_icom(model="IC-9700")
        response = CivFrame(
            to_addr=0xE0, from_addr=_IC9700_ADDR, command=0x16, sub=0x12, data=b"\x01"
        )
        mock = _mock_expect(radio, response)
        value = await radio.get_agc(receiver=0)
        expected = get_agc(to_addr=_IC9700_ADDR, receiver=0, command29=False)
        assert _sent_civ(mock) == expected
        assert value == AgcMode.FAST

    @pytest.mark.asyncio
    async def test_get_agc_raises_on_ic9700_receiver1_dual_rx_guard(self) -> None:
        radio = _connected_icom(model="IC-9700")
        with pytest.raises(CommandError, match="no cmd29 route"):
            await radio.get_agc(receiver=1)


# ---------------------------------------------------------------------------
# get_af_mute / set_af_mute (0x1A/0x09) — builder gained a command29 override;
# radio.py now threads self._profile.supports_cmd29(0x1A, 0x09) through.
# ---------------------------------------------------------------------------


class TestAfMuteGating:
    def test_builder_command29_false_produces_plain_frame(self) -> None:
        """The builder previously had no override at all -- calling with
        command29=False used to raise TypeError. Locks in the new param."""
        got = get_af_mute(to_addr=_IC7300_ADDR, receiver=0, command29=False)
        expected = build_civ_frame(_IC7300_ADDR, 0xE0, 0x1A, sub=0x09)
        assert got == expected
        assert b"\x29" not in got

    def test_builder_set_command29_false_produces_plain_frame(self) -> None:
        got = set_af_mute(True, to_addr=_IC7300_ADDR, receiver=0, command29=False)
        expected = build_civ_frame(_IC7300_ADDR, 0xE0, 0x1A, sub=0x09, data=b"\x01")
        assert got == expected

    @pytest.mark.asyncio
    async def test_get_af_mute_wrapped_on_ic7610_receiver0_unchanged(self) -> None:
        radio = _connected_icom(model="IC-7610")
        response = CivFrame(
            to_addr=0xE0, from_addr=_IC7610_ADDR, command=0x1A, sub=0x09, data=b"\x01"
        )
        mock = _mock_expect(radio, response)
        value = await radio.get_af_mute(receiver=0)
        expected = get_af_mute(to_addr=_IC7610_ADDR, receiver=0, command29=True)
        assert _sent_civ(mock) == expected
        assert value is True

    @pytest.mark.asyncio
    async def test_set_af_mute_wrapped_on_ic7610_receiver1_unchanged(self) -> None:
        radio = _connected_icom(model="IC-7610")
        mock = _mock_raw(radio)
        await radio.set_af_mute(True, receiver=1)
        expected = set_af_mute(True, to_addr=_IC7610_ADDR, receiver=1, command29=True)
        assert _sent_civ(mock) == expected

    @pytest.mark.asyncio
    async def test_get_af_mute_unwraps_when_profile_lacks_route(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """New behavior: a profile that declares af_mute without a cmd29
        route must send plain wire for MAIN, not always wrap. No shipped
        profile hits this today (only IC-7610 declares af_mute, and it has
        the route) -- simulated via a monkeypatched profile to lock in the
        new gate rather than the old unconditional wrap."""
        radio = _connected_icom(model="IC-7610")
        monkeypatch.setattr(
            RadioProfile, "supports_cmd29", lambda self, cmd, sub=None: False
        )
        response = CivFrame(
            to_addr=0xE0, from_addr=_IC7610_ADDR, command=0x1A, sub=0x09, data=b"\x00"
        )
        mock = _mock_expect(radio, response)
        value = await radio.get_af_mute(receiver=0)
        expected = get_af_mute(to_addr=_IC7610_ADDR, receiver=0, command29=False)
        assert _sent_civ(mock) == expected
        assert value is False

    @pytest.mark.asyncio
    async def test_set_af_mute_raises_on_receiver1_when_profile_lacks_route(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        radio = _connected_icom(model="IC-7610")
        monkeypatch.setattr(
            RadioProfile, "supports_cmd29", lambda self, cmd, sub=None: False
        )
        _mock_raw(radio)
        with pytest.raises(CommandError, match="no cmd29 route"):
            await radio.set_af_mute(True, receiver=1)


# ---------------------------------------------------------------------------
# get_digisel / set_digisel (0x16/0x4E) — same builder treatment as af_mute.
# get_digisel additionally had a hard CommandError raise for receiver=MAIN
# when the profile lacked a cmd29 route (stricter than the rest of the
# family, which unwraps for MAIN instead). That extra raise is removed.
# ---------------------------------------------------------------------------


class TestDigiselGating:
    def test_builder_command29_false_produces_plain_frame(self) -> None:
        got = get_digisel(to_addr=_IC7610_ADDR, receiver=0, command29=False)
        expected = build_civ_frame(_IC7610_ADDR, 0xE0, 0x16, sub=0x4E)
        assert got == expected
        assert b"\x29" not in got

    def test_builder_set_command29_false_produces_plain_frame(self) -> None:
        got = set_digisel(True, to_addr=_IC7610_ADDR, receiver=0, command29=False)
        expected = build_civ_frame(_IC7610_ADDR, 0xE0, 0x16, sub=0x4E, data=b"\x01")
        assert got == expected

    @pytest.mark.asyncio
    async def test_get_digisel_wrapped_on_ic7610_receiver0_unchanged(self) -> None:
        radio = _connected_icom(model="IC-7610")
        response = CivFrame(
            to_addr=0xE0, from_addr=_IC7610_ADDR, command=0x16, sub=0x4E, data=b"\x01"
        )
        mock = _mock_expect(radio, response)
        value = await radio.get_digisel(receiver=0)
        expected = get_digisel(to_addr=_IC7610_ADDR, receiver=0, command29=True)
        assert _sent_civ(mock) == expected
        assert value is True

    @pytest.mark.asyncio
    async def test_set_digisel_wrapped_on_ic7610_receiver1_unchanged(self) -> None:
        radio = _connected_icom(model="IC-7610")
        response = CivFrame(
            to_addr=0xE0, from_addr=_IC7610_ADDR, command=0x16, sub=0x4E, data=b"\xfb"
        )
        mock = _mock_expect(radio, response)
        await radio.set_digisel(False, receiver=1)
        expected = set_digisel(False, to_addr=_IC7610_ADDR, receiver=1, command29=True)
        assert _sent_civ(mock) == expected

    @pytest.mark.asyncio
    async def test_get_digisel_unwraps_instead_of_raising_when_no_route(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Pinned defect: get_digisel(receiver=0) on a route-less profile must
        NOT raise CommandError -- it must unwrap like every other command in
        this family (_require_cmd29_route already no-ops for receiver=MAIN;
        the extra hard raise this test used to hit is removed)."""
        radio = _connected_icom(model="IC-7610")
        monkeypatch.setattr(
            RadioProfile, "supports_cmd29", lambda self, cmd, sub=None: False
        )
        response = CivFrame(
            to_addr=0xE0, from_addr=_IC7610_ADDR, command=0x16, sub=0x4E, data=b"\x00"
        )
        mock = _mock_expect(radio, response)
        value = await radio.get_digisel(receiver=0)
        expected = get_digisel(to_addr=_IC7610_ADDR, receiver=0, command29=False)
        assert _sent_civ(mock) == expected
        assert value is False

    @pytest.mark.asyncio
    async def test_get_digisel_raises_on_receiver1_when_no_route_dual_rx_guard(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The dual-RX guard (_require_cmd29_route) still raises for SUB
        targeting without a route -- only the MAIN-targeting hard raise was
        removed."""
        radio = _connected_icom(model="IC-7610")
        monkeypatch.setattr(
            RadioProfile, "supports_cmd29", lambda self, cmd, sub=None: False
        )
        with pytest.raises(CommandError, match="no cmd29 route"):
            await radio.get_digisel(receiver=1)


# ---------------------------------------------------------------------------
# RadioPoller._send_cmd fourth wrap rule (web/radio_poller.py) -- unified to
# match CoreRadio: supports_cmd29(cmd, sub), not
# `receiver != 0 and supports_cmd29(cmd, sub)`.
# ---------------------------------------------------------------------------


def _make_poller_radio(model: str) -> MagicMock:
    profile = resolve_radio_profile(model=model)
    radio = MagicMock()
    radio.profile = profile
    radio.model = profile.model
    radio.capabilities = set(profile.capabilities)
    radio.send_civ = AsyncMock()
    return radio


def _make_poller(model: str) -> tuple[RadioPoller, MagicMock]:
    radio = _make_poller_radio(model)
    poller = RadioPoller(radio, CommandQueue(), radio_state=RadioState())
    return poller, radio


class TestSendCmdCmd29Unification:
    @pytest.mark.asyncio
    async def test_send_cmd_wraps_receiver0_on_ic7610_when_route_exists(self) -> None:
        """Behavior change: previously `receiver != 0 and supports_cmd29(...)`
        never wrapped receiver 0 even though IC-7610 declares the route."""
        poller, radio = _make_poller("IC-7610")
        found = await poller._send_cmd("set_agc", bytes([1]), receiver=0)
        assert found is True
        args: tuple[Any, ...]
        kwargs: dict[str, Any]
        args, kwargs = radio.send_civ.call_args
        assert args[0] == 0x29
        assert kwargs["data"] == bytes([0x00, 0x16, 0x12, 0x01])

    @pytest.mark.asyncio
    async def test_send_cmd_wraps_receiver1_on_ic7610_unchanged(self) -> None:
        poller, radio = _make_poller("IC-7610")
        await poller._send_cmd("set_agc", bytes([1]), receiver=1)
        args, kwargs = radio.send_civ.call_args
        assert args[0] == 0x29
        assert kwargs["data"] == bytes([0x01, 0x16, 0x12, 0x01])

    @pytest.mark.asyncio
    async def test_send_cmd_unwrapped_on_ic7300_no_route(self) -> None:
        poller, radio = _make_poller("IC-7300")
        await poller._send_cmd("set_agc", bytes([1]), receiver=0)
        args, kwargs = radio.send_civ.call_args
        assert args[0] == 0x16
        assert kwargs["sub"] == 0x12
        assert kwargs["data"] == bytes([0x01])

    @pytest.mark.asyncio
    async def test_send_cmd_matches_core_radio_set_agc_wrap_shape(self) -> None:
        """The set_agc dispatch arm falls back to _send_cmd when CAP_AGC is
        not declared. Confirm the wrap/no-wrap decision it makes for
        receiver=0 on IC-7610 now matches CoreRadio.set_agc's own decision
        (both gated purely on supports_cmd29, no receiver-based override)."""
        core_radio = _connected_icom(model="IC-7610")
        core_mock = _mock_raw(core_radio)
        await core_radio.set_agc(AgcMode.FAST, receiver=0)
        core_frame = _sent_civ(core_mock)

        poller, radio = _make_poller("IC-7610")
        await poller._send_cmd("set_agc", bytes([1]), receiver=0)
        args, kwargs = radio.send_civ.call_args

        # core_frame is a full CI-V frame (FE FE to from CMD ... FD); the
        # poller's _civ() call only carries the inner cmd/sub/data -- compare
        # the wrap decision (cmd byte 0x29) and the cmd29 inner payload shape.
        assert core_frame[4] == 0x29
        assert args[0] == 0x29
        assert core_frame[5:8] == bytes([0x00, 0x16, 0x12])
        assert kwargs["data"][:3] == bytes([0x00, 0x16, 0x12])
