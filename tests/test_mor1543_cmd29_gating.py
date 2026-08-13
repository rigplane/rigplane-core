"""Tests for MOR-1543 — profile-gated cmd29 for the 0x14 level family.

MOR-1517 (#2434) established the pattern, MOR-1537 (#2451) extended it to
AGC/AF-mute/DIGI-SEL, and the MOR-1537 follow-up (#2455) extended it again to
NB/NR/IP+: a CI-V command builder must accept an explicit ``command29: bool``
keyword-only override, and the ``radio.py`` call site must compute
``cmd29 = self._profile.supports_cmd29(cmd, sub)`` and thread it through —
never derive the wrap decision from ``receiver`` inside the builder.

Auditing ``commands/levels.py`` found four more commands with the exact
stale shape:

- ``get_rf_gain``/``set_rf_gain`` (0x14/0x02)
- ``get_af_level``/``set_af_level`` (0x14/0x01)
- ``get_squelch``/``set_squelch`` (0x14/0x03)
- ``get_notch_filter``/``set_notch_filter`` (0x14/0x0D)

All four computed ``command29=(receiver != RECEIVER_MAIN)`` internally with
no override parameter. IC-7610 declares ``[cmd29]`` routes for all four subs
(``rigs/ic7610.toml``), so e.g. ``set_rf_gain(level, receiver=0)`` on IC-7610
sent a PLAIN frame for the MAIN receiver even though the profile declares a
cmd29 route — the same shape mismatch against the acquisition/scheduler path
that MOR-1537 fixed for AGC.

Notch filter (0x14/0x0D) deserves special note: ic7610.toml's route comment
records a PDF-vs-wfview disagreement (wfview's IC-7610.rig records
Command29=false, but the IC-7610 CI-V Reference Guide Rev.4 p.3 shows the
Command29 glyph) — already adjudicated in favor of the PDF, with rationale,
in a prior review (issue #708; see ``docs/parity/ic7610_command_matrix.json``
"cmd29_rationale" for sub 0x0D). There is no in-repo evidence of a live NAK
against the wrapped frame — the disagreement is source-provenance only, and
it was already resolved by keeping the route. So this fix converts
``get_notch_filter``/``set_notch_filter`` exactly like the other three
instead of removing the route.

IC-7300 declares no ``[cmd29]`` section at all -> every frame must remain
bare/unwrapped there, byte-for-byte identical to the pre-fix behavior.
"""

from __future__ import annotations

import pytest

from rigplane.types import CivFrame

from test_mor1517_cmd29_gating import (
    _connected_icom,
    _mock_expect,
    _mock_raw,
    _sent_civ,
)

_IC7300_ADDR = 0x94
_IC7610_ADDR = 0x98


# ---------------------------------------------------------------------------
# Route-table sanity: the whole point of the fix is that IC-7610 *does*
# declare cmd29 routes for these subs.
# ---------------------------------------------------------------------------


class TestRouteTableSanity:
    @pytest.mark.parametrize("sub", [0x01, 0x02, 0x03, 0x0D])
    def test_ic7610_declares_route(self, sub: int) -> None:
        radio = _connected_icom(model="IC-7610")
        assert radio._profile.supports_cmd29(0x14, sub) is True

    @pytest.mark.parametrize("sub", [0x01, 0x02, 0x03, 0x0D])
    def test_ic7300_declares_no_route(self, sub: int) -> None:
        radio = _connected_icom(model="IC-7300")
        assert radio._profile.supports_cmd29(0x14, sub) is False


# ---------------------------------------------------------------------------
# Builder-level: explicit command29 override, receiver no longer decides.
# Expected frames are raw byte literals on purpose — they pin the wire format
# independently of the builder under test.
# ---------------------------------------------------------------------------


class TestBuilderOverride:
    def test_set_rf_gain_wrapped_by_default_for_main(self) -> None:
        from rigplane.commands import set_rf_gain

        frame = set_rf_gain(128, to_addr=_IC7610_ADDR, receiver=0)
        assert frame == bytes.fromhex("fefe98e0290014020128fd")

    def test_set_rf_gain_unwrapped_with_override(self) -> None:
        from rigplane.commands import set_rf_gain

        frame = set_rf_gain(128, to_addr=_IC7300_ADDR, receiver=0, command29=False)
        assert frame == bytes.fromhex("fefe94e014020128fd")

    def test_get_rf_gain_wrapped_by_default(self) -> None:
        from rigplane.commands import get_rf_gain

        frame = get_rf_gain(to_addr=_IC7610_ADDR)
        assert frame == bytes.fromhex("fefe98e029001402fd")

    def test_get_rf_gain_unwrapped_with_override(self) -> None:
        from rigplane.commands import get_rf_gain

        frame = get_rf_gain(to_addr=_IC7300_ADDR, command29=False)
        assert frame == bytes.fromhex("fefe94e01402fd")

    def test_set_af_level_wrapped_by_default_for_main(self) -> None:
        from rigplane.commands import set_af_level

        frame = set_af_level(200, to_addr=_IC7610_ADDR, receiver=0)
        assert frame == bytes.fromhex("fefe98e0290014010200fd")

    def test_set_af_level_unwrapped_with_override(self) -> None:
        from rigplane.commands import set_af_level

        frame = set_af_level(200, to_addr=_IC7300_ADDR, receiver=0, command29=False)
        assert frame == bytes.fromhex("fefe94e014010200fd")

    def test_get_af_level_wrapped_by_default(self) -> None:
        from rigplane.commands import get_af_level

        frame = get_af_level(to_addr=_IC7610_ADDR)
        assert frame == bytes.fromhex("fefe98e029001401fd")

    def test_get_af_level_unwrapped_with_override(self) -> None:
        from rigplane.commands import get_af_level

        frame = get_af_level(to_addr=_IC7300_ADDR, command29=False)
        assert frame == bytes.fromhex("fefe94e01401fd")

    def test_set_squelch_wrapped_by_default_for_main(self) -> None:
        from rigplane.commands import set_squelch

        frame = set_squelch(64, to_addr=_IC7610_ADDR, receiver=0)
        assert frame == bytes.fromhex("fefe98e0290014030064fd")

    def test_set_squelch_unwrapped_with_override(self) -> None:
        from rigplane.commands import set_squelch

        frame = set_squelch(64, to_addr=_IC7300_ADDR, receiver=0, command29=False)
        assert frame == bytes.fromhex("fefe94e014030064fd")

    def test_get_squelch_wrapped_by_default(self) -> None:
        from rigplane.commands import get_squelch

        frame = get_squelch(to_addr=_IC7610_ADDR)
        assert frame == bytes.fromhex("fefe98e029001403fd")

    def test_get_squelch_unwrapped_with_override(self) -> None:
        from rigplane.commands import get_squelch

        frame = get_squelch(to_addr=_IC7300_ADDR, command29=False)
        assert frame == bytes.fromhex("fefe94e01403fd")

    def test_set_notch_filter_wrapped_by_default_for_main(self) -> None:
        from rigplane.commands import set_notch_filter

        frame = set_notch_filter(90, to_addr=_IC7610_ADDR, receiver=0)
        assert frame == bytes.fromhex("fefe98e02900140d0090fd")

    def test_set_notch_filter_unwrapped_with_override(self) -> None:
        from rigplane.commands import set_notch_filter

        frame = set_notch_filter(90, to_addr=_IC7300_ADDR, receiver=0, command29=False)
        assert frame == bytes.fromhex("fefe94e0140d0090fd")

    def test_get_notch_filter_wrapped_by_default(self) -> None:
        from rigplane.commands import get_notch_filter

        frame = get_notch_filter(to_addr=_IC7610_ADDR)
        assert frame == bytes.fromhex("fefe98e02900140dfd")

    def test_get_notch_filter_unwrapped_with_override(self) -> None:
        from rigplane.commands import get_notch_filter

        frame = get_notch_filter(to_addr=_IC7300_ADDR, command29=False)
        assert frame == bytes.fromhex("fefe94e0140dfd")


# ---------------------------------------------------------------------------
# Runtime call sites: cmd29 must come from the profile, not from receiver.
# The pinned defect is the IC-7610 receiver=0 (MAIN) case: it must now go
# out 0x29-wrapped because the profile declares the route.
# ---------------------------------------------------------------------------


class TestRfGainGating:
    @pytest.mark.asyncio
    async def test_set_rf_gain_main_wrapped_on_ic7610(self) -> None:
        radio = _connected_icom(model="IC-7610")
        mock = _mock_raw(radio)
        await radio.set_rf_gain(128, receiver=0)
        assert _sent_civ(mock) == bytes.fromhex("fefe98e0290014020128fd")

    @pytest.mark.asyncio
    async def test_set_rf_gain_sub_still_wrapped_on_ic7610(self) -> None:
        radio = _connected_icom(model="IC-7610")
        mock = _mock_raw(radio)
        await radio.set_rf_gain(128, receiver=1)
        assert _sent_civ(mock) == bytes.fromhex("fefe98e0290114020128fd")

    @pytest.mark.asyncio
    async def test_set_rf_gain_unwrapped_on_ic7300(self) -> None:
        radio = _connected_icom(model="IC-7300")
        mock = _mock_raw(radio)
        await radio.set_rf_gain(128, receiver=0)
        assert _sent_civ(mock) == bytes.fromhex("fefe94e014020128fd")

    @pytest.mark.asyncio
    async def test_get_rf_gain_wrapped_on_ic7610(self) -> None:
        radio = _connected_icom(model="IC-7610")
        response = CivFrame(
            to_addr=0xE0,
            from_addr=_IC7610_ADDR,
            command=0x14,
            sub=0x02,
            data=b"\x01\x28",
        )
        mock = _mock_expect(radio, response)
        value = await radio.get_rf_gain()
        assert _sent_civ(mock) == bytes.fromhex("fefe98e029001402fd")
        assert value == 128

    @pytest.mark.asyncio
    async def test_get_rf_gain_unwrapped_on_ic7300(self) -> None:
        radio = _connected_icom(model="IC-7300")
        response = CivFrame(
            to_addr=0xE0,
            from_addr=_IC7300_ADDR,
            command=0x14,
            sub=0x02,
            data=b"\x01\x28",
        )
        mock = _mock_expect(radio, response)
        value = await radio.get_rf_gain()
        assert _sent_civ(mock) == bytes.fromhex("fefe94e01402fd")
        assert value == 128


class TestAfLevelGating:
    @pytest.mark.asyncio
    async def test_set_af_level_main_wrapped_on_ic7610(self) -> None:
        radio = _connected_icom(model="IC-7610")
        mock = _mock_raw(radio)
        await radio.set_af_level(200, receiver=0)
        assert _sent_civ(mock) == bytes.fromhex("fefe98e0290014010200fd")

    @pytest.mark.asyncio
    async def test_set_af_level_sub_still_wrapped_on_ic7610(self) -> None:
        radio = _connected_icom(model="IC-7610")
        mock = _mock_raw(radio)
        await radio.set_af_level(200, receiver=1)
        assert _sent_civ(mock) == bytes.fromhex("fefe98e0290114010200fd")

    @pytest.mark.asyncio
    async def test_set_af_level_unwrapped_on_ic7300(self) -> None:
        radio = _connected_icom(model="IC-7300")
        mock = _mock_raw(radio)
        await radio.set_af_level(200, receiver=0)
        assert _sent_civ(mock) == bytes.fromhex("fefe94e014010200fd")

    @pytest.mark.asyncio
    async def test_get_af_level_wrapped_on_ic7610(self) -> None:
        radio = _connected_icom(model="IC-7610")
        response = CivFrame(
            to_addr=0xE0,
            from_addr=_IC7610_ADDR,
            command=0x14,
            sub=0x01,
            data=b"\x02\x00",
        )
        mock = _mock_expect(radio, response)
        value = await radio.get_af_level()
        assert _sent_civ(mock) == bytes.fromhex("fefe98e029001401fd")
        assert value == 200

    @pytest.mark.asyncio
    async def test_get_af_level_unwrapped_on_ic7300(self) -> None:
        radio = _connected_icom(model="IC-7300")
        response = CivFrame(
            to_addr=0xE0,
            from_addr=_IC7300_ADDR,
            command=0x14,
            sub=0x01,
            data=b"\x02\x00",
        )
        mock = _mock_expect(radio, response)
        value = await radio.get_af_level()
        assert _sent_civ(mock) == bytes.fromhex("fefe94e01401fd")
        assert value == 200


class TestSquelchGating:
    @pytest.mark.asyncio
    async def test_set_squelch_main_wrapped_on_ic7610(self) -> None:
        radio = _connected_icom(model="IC-7610")
        mock = _mock_raw(radio)
        await radio.set_squelch(64, receiver=0)
        assert _sent_civ(mock) == bytes.fromhex("fefe98e0290014030064fd")

    @pytest.mark.asyncio
    async def test_set_squelch_sub_still_wrapped_on_ic7610(self) -> None:
        radio = _connected_icom(model="IC-7610")
        mock = _mock_raw(radio)
        await radio.set_squelch(64, receiver=1)
        assert _sent_civ(mock) == bytes.fromhex("fefe98e0290114030064fd")

    @pytest.mark.asyncio
    async def test_set_squelch_unwrapped_on_ic7300(self) -> None:
        radio = _connected_icom(model="IC-7300")
        mock = _mock_raw(radio)
        await radio.set_squelch(64, receiver=0)
        assert _sent_civ(mock) == bytes.fromhex("fefe94e014030064fd")

    @pytest.mark.asyncio
    async def test_get_squelch_wrapped_on_ic7610(self) -> None:
        radio = _connected_icom(model="IC-7610")
        response = CivFrame(
            to_addr=0xE0,
            from_addr=_IC7610_ADDR,
            command=0x14,
            sub=0x03,
            data=b"\x00\x64",
        )
        mock = _mock_expect(radio, response)
        value = await radio.get_squelch()
        assert _sent_civ(mock) == bytes.fromhex("fefe98e029001403fd")
        assert value == 64

    @pytest.mark.asyncio
    async def test_get_squelch_unwrapped_on_ic7300(self) -> None:
        radio = _connected_icom(model="IC-7300")
        response = CivFrame(
            to_addr=0xE0,
            from_addr=_IC7300_ADDR,
            command=0x14,
            sub=0x03,
            data=b"\x00\x64",
        )
        mock = _mock_expect(radio, response)
        value = await radio.get_squelch()
        assert _sent_civ(mock) == bytes.fromhex("fefe94e01403fd")
        assert value == 64


class TestNotchFilterGating:
    @pytest.mark.asyncio
    async def test_set_notch_filter_main_wrapped_on_ic7610(self) -> None:
        radio = _connected_icom(model="IC-7610")
        mock = _mock_raw(radio)
        await radio.set_notch_filter(90, receiver=0)
        assert _sent_civ(mock) == bytes.fromhex("fefe98e02900140d0090fd")

    @pytest.mark.asyncio
    async def test_set_notch_filter_sub_still_wrapped_on_ic7610(self) -> None:
        radio = _connected_icom(model="IC-7610")
        mock = _mock_raw(radio)
        await radio.set_notch_filter(90, receiver=1)
        assert _sent_civ(mock) == bytes.fromhex("fefe98e02901140d0090fd")

    @pytest.mark.asyncio
    async def test_set_notch_filter_unwrapped_on_ic7300(self) -> None:
        radio = _connected_icom(model="IC-7300")
        mock = _mock_raw(radio)
        await radio.set_notch_filter(90, receiver=0)
        assert _sent_civ(mock) == bytes.fromhex("fefe94e0140d0090fd")

    @pytest.mark.asyncio
    async def test_get_notch_filter_wrapped_on_ic7610(self) -> None:
        radio = _connected_icom(model="IC-7610")
        response = CivFrame(
            to_addr=0xE0,
            from_addr=_IC7610_ADDR,
            command=0x14,
            sub=0x0D,
            data=b"\x00\x90",
        )
        mock = _mock_expect(radio, response)
        value = await radio.get_notch_filter()
        assert _sent_civ(mock) == bytes.fromhex("fefe98e02900140dfd")
        assert value == 90

    @pytest.mark.asyncio
    async def test_get_notch_filter_unwrapped_on_ic7300(self) -> None:
        radio = _connected_icom(model="IC-7300")
        response = CivFrame(
            to_addr=0xE0,
            from_addr=_IC7300_ADDR,
            command=0x14,
            sub=0x0D,
            data=b"\x00\x90",
        )
        mock = _mock_expect(radio, response)
        value = await radio.get_notch_filter()
        assert _sent_civ(mock) == bytes.fromhex("fefe94e0140dfd")
        assert value == 90
