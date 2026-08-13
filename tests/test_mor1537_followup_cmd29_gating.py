"""Tests for the MOR-1537 follow-up — profile-gated cmd29 for NB/NR/IP+.

MOR-1517 (#2434) established the pattern and MOR-1537 (#2451) extended it to
AGC/AF-mute/DIGI-SEL: a CI-V command builder must accept an explicit
``command29: bool`` keyword-only override, and the ``radio.py`` call site must
compute ``cmd29 = self._profile.supports_cmd29(cmd, sub)`` and thread it
through — never derive the wrap decision from ``receiver`` inside the builder.

Auditing that bug class found three more commands with the exact stale shape
in ``commands/dsp.py``:

- ``set_nb`` (0x16/0x22), ``set_nr`` (0x16/0x40), ``set_ip_plus`` (0x16/0x65)
  computed ``command29=(receiver != RECEIVER_MAIN)`` internally with no
  override parameter, and their GET twins (``get_nb``/``get_nr``/
  ``get_ip_plus``) were always-plain with no override at all.

IC-7610 declares ``[cmd29]`` routes for all three subs, so
``set_nb(on, receiver=0)`` on IC-7610 sent a PLAIN frame for the MAIN
receiver even though the profile declares a cmd29 route — the same shape
mismatch against the acquisition/scheduler path that MOR-1537 fixed for AGC.
(MOR-1517's audit classified these three as "already correct" without
checking IC-7610's route table; that classification was wrong.)

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
# declare cmd29 routes for these subs (contrary to MOR-1517's classification).
# ---------------------------------------------------------------------------


class TestRouteTableSanity:
    @pytest.mark.parametrize("sub", [0x22, 0x40, 0x65])
    def test_ic7610_declares_route(self, sub: int) -> None:
        radio = _connected_icom(model="IC-7610")
        assert radio._profile.supports_cmd29(0x16, sub) is True

    @pytest.mark.parametrize("sub", [0x22, 0x40, 0x65])
    def test_ic7300_declares_no_route(self, sub: int) -> None:
        radio = _connected_icom(model="IC-7300")
        assert radio._profile.supports_cmd29(0x16, sub) is False


# ---------------------------------------------------------------------------
# Builder-level: explicit command29 override, receiver no longer decides.
# Expected frames are raw byte literals on purpose — they pin the wire format
# independently of the builder under test.
# ---------------------------------------------------------------------------


class TestBuilderOverride:
    def test_set_nb_wrapped_by_default_for_main(self) -> None:
        from rigplane.commands import set_nb

        frame = set_nb(True, to_addr=_IC7610_ADDR, receiver=0)
        assert frame == bytes.fromhex("fefe98e02900162201fd")

    def test_set_nb_unwrapped_with_override(self) -> None:
        from rigplane.commands import set_nb

        frame = set_nb(True, to_addr=_IC7300_ADDR, receiver=0, command29=False)
        assert frame == bytes.fromhex("fefe94e0162201fd")

    def test_set_nr_wrapped_by_default_for_main(self) -> None:
        from rigplane.commands import set_nr

        frame = set_nr(False, to_addr=_IC7610_ADDR, receiver=0)
        assert frame == bytes.fromhex("fefe98e02900164000fd")

    def test_set_nr_unwrapped_with_override(self) -> None:
        from rigplane.commands import set_nr

        frame = set_nr(False, to_addr=_IC7300_ADDR, receiver=0, command29=False)
        assert frame == bytes.fromhex("fefe94e0164000fd")

    def test_set_ip_plus_wrapped_by_default_for_main(self) -> None:
        from rigplane.commands import set_ip_plus

        frame = set_ip_plus(True, to_addr=_IC7610_ADDR, receiver=0)
        assert frame == bytes.fromhex("fefe98e02900166501fd")

    def test_set_ip_plus_unwrapped_with_override(self) -> None:
        from rigplane.commands import set_ip_plus

        frame = set_ip_plus(True, to_addr=_IC7300_ADDR, receiver=0, command29=False)
        assert frame == bytes.fromhex("fefe94e0166501fd")

    def test_get_nb_wrapped_by_default(self) -> None:
        from rigplane.commands import get_nb

        frame = get_nb(to_addr=_IC7610_ADDR)
        assert frame == bytes.fromhex("fefe98e029001622fd")

    def test_get_nb_unwrapped_with_override(self) -> None:
        from rigplane.commands import get_nb

        frame = get_nb(to_addr=_IC7300_ADDR, command29=False)
        assert frame == bytes.fromhex("fefe94e01622fd")

    def test_get_nr_wrapped_by_default(self) -> None:
        from rigplane.commands import get_nr

        frame = get_nr(to_addr=_IC7610_ADDR)
        assert frame == bytes.fromhex("fefe98e029001640fd")

    def test_get_nr_unwrapped_with_override(self) -> None:
        from rigplane.commands import get_nr

        frame = get_nr(to_addr=_IC7300_ADDR, command29=False)
        assert frame == bytes.fromhex("fefe94e01640fd")

    def test_get_ip_plus_wrapped_by_default(self) -> None:
        from rigplane.commands import get_ip_plus

        frame = get_ip_plus(to_addr=_IC7610_ADDR)
        assert frame == bytes.fromhex("fefe98e029001665fd")

    def test_get_ip_plus_unwrapped_with_override(self) -> None:
        from rigplane.commands import get_ip_plus

        frame = get_ip_plus(to_addr=_IC7300_ADDR, command29=False)
        assert frame == bytes.fromhex("fefe94e01665fd")


# ---------------------------------------------------------------------------
# Runtime call sites: cmd29 must come from the profile, not from receiver.
# The pinned defect is the IC-7610 receiver=0 (MAIN) case: it must now go
# out 0x29-wrapped because the profile declares the route.
# ---------------------------------------------------------------------------


class TestNbGating:
    @pytest.mark.asyncio
    async def test_set_nb_main_wrapped_on_ic7610(self) -> None:
        radio = _connected_icom(model="IC-7610")
        mock = _mock_raw(radio)
        await radio.set_nb(True, receiver=0)
        assert _sent_civ(mock) == bytes.fromhex("fefe98e02900162201fd")

    @pytest.mark.asyncio
    async def test_set_nb_sub_still_wrapped_on_ic7610(self) -> None:
        radio = _connected_icom(model="IC-7610")
        mock = _mock_raw(radio)
        await radio.set_nb(True, receiver=1)
        assert _sent_civ(mock) == bytes.fromhex("fefe98e02901162201fd")

    @pytest.mark.asyncio
    async def test_set_nb_unwrapped_on_ic7300(self) -> None:
        radio = _connected_icom(model="IC-7300")
        mock = _mock_raw(radio)
        await radio.set_nb(True, receiver=0)
        assert _sent_civ(mock) == bytes.fromhex("fefe94e0162201fd")

    @pytest.mark.asyncio
    async def test_get_nb_wrapped_on_ic7610(self) -> None:
        radio = _connected_icom(model="IC-7610")
        response = CivFrame(
            to_addr=0xE0, from_addr=_IC7610_ADDR, command=0x16, sub=0x22, data=b"\x01"
        )
        mock = _mock_expect(radio, response)
        value = await radio.get_nb()
        assert _sent_civ(mock) == bytes.fromhex("fefe98e029001622fd")
        assert value is True

    @pytest.mark.asyncio
    async def test_get_nb_unwrapped_on_ic7300(self) -> None:
        radio = _connected_icom(model="IC-7300")
        response = CivFrame(
            to_addr=0xE0, from_addr=_IC7300_ADDR, command=0x16, sub=0x22, data=b"\x01"
        )
        mock = _mock_expect(radio, response)
        value = await radio.get_nb()
        assert _sent_civ(mock) == bytes.fromhex("fefe94e01622fd")
        assert value is True


class TestNrGating:
    @pytest.mark.asyncio
    async def test_set_nr_main_wrapped_on_ic7610(self) -> None:
        radio = _connected_icom(model="IC-7610")
        mock = _mock_raw(radio)
        await radio.set_nr(False, receiver=0)
        assert _sent_civ(mock) == bytes.fromhex("fefe98e02900164000fd")

    @pytest.mark.asyncio
    async def test_set_nr_sub_still_wrapped_on_ic7610(self) -> None:
        radio = _connected_icom(model="IC-7610")
        mock = _mock_raw(radio)
        await radio.set_nr(True, receiver=1)
        assert _sent_civ(mock) == bytes.fromhex("fefe98e02901164001fd")

    @pytest.mark.asyncio
    async def test_set_nr_unwrapped_on_ic7300(self) -> None:
        radio = _connected_icom(model="IC-7300")
        mock = _mock_raw(radio)
        await radio.set_nr(True, receiver=0)
        assert _sent_civ(mock) == bytes.fromhex("fefe94e0164001fd")

    @pytest.mark.asyncio
    async def test_get_nr_wrapped_on_ic7610(self) -> None:
        radio = _connected_icom(model="IC-7610")
        response = CivFrame(
            to_addr=0xE0, from_addr=_IC7610_ADDR, command=0x16, sub=0x40, data=b"\x00"
        )
        mock = _mock_expect(radio, response)
        value = await radio.get_nr()
        assert _sent_civ(mock) == bytes.fromhex("fefe98e029001640fd")
        assert value is False

    @pytest.mark.asyncio
    async def test_get_nr_unwrapped_on_ic7300(self) -> None:
        radio = _connected_icom(model="IC-7300")
        response = CivFrame(
            to_addr=0xE0, from_addr=_IC7300_ADDR, command=0x16, sub=0x40, data=b"\x01"
        )
        mock = _mock_expect(radio, response)
        value = await radio.get_nr()
        assert _sent_civ(mock) == bytes.fromhex("fefe94e01640fd")
        assert value is True


class TestIpPlusGating:
    @pytest.mark.asyncio
    async def test_set_ip_plus_main_wrapped_on_ic7610(self) -> None:
        radio = _connected_icom(model="IC-7610")
        mock = _mock_raw(radio)
        await radio.set_ip_plus(True, receiver=0)
        assert _sent_civ(mock) == bytes.fromhex("fefe98e02900166501fd")

    @pytest.mark.asyncio
    async def test_set_ip_plus_sub_still_wrapped_on_ic7610(self) -> None:
        radio = _connected_icom(model="IC-7610")
        mock = _mock_raw(radio)
        await radio.set_ip_plus(True, receiver=1)
        assert _sent_civ(mock) == bytes.fromhex("fefe98e02901166501fd")

    @pytest.mark.asyncio
    async def test_set_ip_plus_unwrapped_on_ic7300(self) -> None:
        radio = _connected_icom(model="IC-7300")
        mock = _mock_raw(radio)
        await radio.set_ip_plus(True, receiver=0)
        assert _sent_civ(mock) == bytes.fromhex("fefe94e0166501fd")

    @pytest.mark.asyncio
    async def test_get_ip_plus_wrapped_on_ic7610(self) -> None:
        radio = _connected_icom(model="IC-7610")
        response = CivFrame(
            to_addr=0xE0, from_addr=_IC7610_ADDR, command=0x16, sub=0x65, data=b"\x01"
        )
        mock = _mock_expect(radio, response)
        value = await radio.get_ip_plus()
        assert _sent_civ(mock) == bytes.fromhex("fefe98e029001665fd")
        assert value is True

    @pytest.mark.asyncio
    async def test_get_ip_plus_unwrapped_on_ic7300(self) -> None:
        radio = _connected_icom(model="IC-7300")
        response = CivFrame(
            to_addr=0xE0, from_addr=_IC7300_ADDR, command=0x16, sub=0x65, data=b"\x00"
        )
        mock = _mock_expect(radio, response)
        value = await radio.get_ip_plus()
        assert _sent_civ(mock) == bytes.fromhex("fefe94e01665fd")
        assert value is False
