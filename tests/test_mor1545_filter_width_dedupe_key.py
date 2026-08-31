"""MOR-1545 follow-up: pin the receiver scoping of ``get_filter_width``'s
single-flight dedupe key.

``radio.py: IcomRadio.get_filter_width`` registers its in-flight read under
``key=f"get_filter_width:{receiver}"`` at two call sites -- the VFO-select
fallback branch (``receiver != MAIN`` on a profile with no cmd29 route for
0x1A/0x03) and the direct branch. ``commander.py: IcomCommander.send``
coalesces a caller onto an already-registered not-done future for the same
key and sends nothing, so a bare ``key="get_filter_width"`` would hand a
concurrent SUB reader MAIN's answer.

The sibling dedupe-key tests for the repeater-tone family cite
``get_filter_width`` as the precedent their own receiver-scoped keys were
modelled on. These tests pin that precedent itself, one class per call site.
"""

from __future__ import annotations

import asyncio
from collections.abc import Iterator

import pytest

from rigplane import IC_7610_ADDR
from rigplane.commands import CONTROLLER_ADDR, build_civ_frame, build_cmd29_frame
from rigplane.radio import IcomRadio

from _helpers import wrap_civ_in_udp
from test_radio import MockTransport

_IC9700_ADDR = 0xA2

# Generous per-request CI-V timeout. No assertion in this file measures
# elapsed time; a tight timeout only risks a failure mode unrelated to what
# is being pinned here -- a request racing the clock and raising
# ``TimeoutError`` inside ``_civ_rx.py: CivRuntime._execute_civ_raw`` instead
# of exercising the dedupe key under test.
_CIV_TIMEOUT_S = 2.0

# Filter-width payloads and the widths they decode to. ``get_filter_width``
# parses a 1-byte BCD index and maps it through
# ``profiles: RadioProfile.resolve_filter_rule`` / ``filter_index_to_hz``.
# Both values below were measured on this tree, on IC-7610 and IC-9700
# alike, for a radio whose state was never populated by a poll.
_INDEX_MAIN = b"\x01"
_INDEX_SUB = b"\x02"
_WIDTH_MAIN_HZ = 100
_WIDTH_SUB_HZ = 150


def _cmd29_filter_width_response(payload: bytes, *, receiver: int) -> bytes:
    """A cmd29-wrapped 0x1A/0x03 answer, as an IC-7610 sends it."""
    return wrap_civ_in_udp(
        build_cmd29_frame(
            CONTROLLER_ADDR,
            IC_7610_ADDR,
            0x1A,
            sub=0x03,
            data=payload,
            receiver=receiver,
        )
    )


def _plain_filter_width_response(payload: bytes) -> bytes:
    """A plain 0x1A/0x03 answer, as an IC-9700 sends it.

    Carries no receiver tag: on this profile the MAIN read and the SUB
    fallback read put byte-identical requests on the wire, so nothing but
    the dedupe key distinguishes the two in-flight reads.
    """
    return wrap_civ_in_udp(
        build_civ_frame(CONTROLLER_ADDR, _IC9700_ADDR, 0x1A, sub=0x03, data=payload)
    )


@pytest.fixture
def mock_transport() -> MockTransport:
    return MockTransport()


@pytest.fixture
def ic7610_radio(mock_transport: MockTransport) -> Iterator[IcomRadio]:
    """Dual-RX profile that lists [0x1A, 0x03] in its cmd29 routes, so SUB
    takes the direct branch rather than the VFO-select fallback."""
    r = IcomRadio("192.168.1.100", timeout=_CIV_TIMEOUT_S)
    r._civ_transport = mock_transport
    r._ctrl_transport = mock_transport
    r._connected = True
    yield r
    r._connected = False


@pytest.fixture
def ic9700_radio(mock_transport: MockTransport) -> Iterator[IcomRadio]:
    """Dual-RX profile with VFO select codes but an empty cmd29 route list,
    so SUB takes the VFO-select fallback branch."""
    r = IcomRadio("192.168.1.102", timeout=_CIV_TIMEOUT_S, model="IC-9700")
    r._civ_transport = mock_transport
    r._ctrl_transport = mock_transport
    r._connected = True

    async def instant_select(vfo: str) -> None:
        """No-op VFO swap: keeps the fallback path's own dedupe check inside
        the window where MAIN's read is still in flight."""
        return None

    r._set_vfo_wire = instant_select  # type: ignore[method-assign]
    yield r
    r._connected = False


class TestFilterWidthDirectBranchDedupeKeyReceiverScoped:
    """The direct branch of ``radio.py: IcomRadio.get_filter_width`` on
    IC-7610, where MAIN and SUB both register under that one call site's key.

    Load-bearing choices, each measured rather than assumed:

    * ``_civ_runtime.start_worker()`` is required. With no commander,
      ``_civ_rx.py: CivRuntime._send_civ_raw`` bypasses
      ``IcomCommander.send`` entirely and drops ``key``/``dedupe``; measured
      on a tree with both keys reverted to the bare form, the cross-receiver
      cases below then pass while checking nothing.
    * Both ``asyncio.gather`` argument orders are exercised. Each was
      measured to discriminate on this profile (bare key: one frame, one
      shared answer), but the coalesced answer is whichever read went first,
      so a test asserting only the SUB value would stay green under SUB-first
      coalescing. Both returned values are asserted for that reason.
    * The frame count is asserted in addition to the values, never instead of
      them: measured 2 frames on this tree and 1 on the bare-key tree.
    """

    @pytest.mark.asyncio
    @pytest.mark.parametrize("main_first", [True, False], ids=["main-1st", "sub-1st"])
    async def test_concurrent_main_and_sub_reads_do_not_coalesce(
        self,
        ic7610_radio: IcomRadio,
        mock_transport: MockTransport,
        main_first: bool,
    ) -> None:
        main_response = _cmd29_filter_width_response(_INDEX_MAIN, receiver=0)
        sub_response = _cmd29_filter_width_response(_INDEX_SUB, receiver=1)
        ic7610_radio._civ_runtime.start_worker()
        try:
            if main_first:
                mock_transport.queue_response_on_send(1, main_response)
                mock_transport.queue_response_on_send(2, sub_response)
                main_width, sub_width = await asyncio.gather(
                    ic7610_radio.get_filter_width(receiver=0),
                    ic7610_radio.get_filter_width(receiver=1),
                )
            else:
                mock_transport.queue_response_on_send(1, sub_response)
                mock_transport.queue_response_on_send(2, main_response)
                sub_width, main_width = await asyncio.gather(
                    ic7610_radio.get_filter_width(receiver=1),
                    ic7610_radio.get_filter_width(receiver=0),
                )
            assert main_width == _WIDTH_MAIN_HZ
            assert sub_width == _WIDTH_SUB_HZ
            assert len(mock_transport.sent_packets) == 2
        finally:
            await ic7610_radio._civ_runtime.stop_worker()

    @pytest.mark.asyncio
    async def test_concurrent_same_receiver_reads_still_dedupe(
        self, ic7610_radio: IcomRadio, mock_transport: MockTransport
    ) -> None:
        """Control: the dedupe mechanism itself is intact, so a change that
        simply stopped deduping would not keep the test above green."""
        ic7610_radio._civ_runtime.start_worker()
        try:
            mock_transport.queue_response_on_send(
                1, _cmd29_filter_width_response(_INDEX_MAIN, receiver=0)
            )
            first, second = await asyncio.gather(
                ic7610_radio.get_filter_width(receiver=0),
                ic7610_radio.get_filter_width(receiver=0),
            )
            assert first == _WIDTH_MAIN_HZ
            assert second == _WIDTH_MAIN_HZ
            assert len(mock_transport.sent_packets) == 1
        finally:
            await ic7610_radio._civ_runtime.stop_worker()


class TestFilterWidthVfoFallbackDedupeKeyReceiverScoped:
    """The VFO-select fallback branch of ``radio.py:
    IcomRadio.get_filter_width`` on IC-9700, where SUB registers under the
    fallback site's key and MAIN under the direct site's.

    Because the two receivers reach two different call sites here, the two
    keys collide whenever the two sites evaluate to the same string -- which
    an edit to a single site can cause, not only a literal shared by both.
    Measured on this tree: reverting both keys to a bare
    ``key="get_filter_width"`` coalesces the pair; reverting either one
    alone does not, because the remaining site still interpolates. What
    this class pins that the direct-branch class cannot is the fallback
    site's own ``{receiver}`` interpolation -- replacing it with a
    hard-coded ``get_filter_width:0`` was measured to coalesce here and to
    leave IC-7610 untouched.

    The same three load-bearing choices as the direct-branch class apply
    (worker started, both gather orders, both values asserted alongside the
    frame count), each measured on this profile too.
    """

    @pytest.mark.asyncio
    @pytest.mark.parametrize("main_first", [True, False], ids=["main-1st", "sub-1st"])
    async def test_concurrent_main_direct_and_sub_fallback_reads_do_not_coalesce(
        self,
        ic9700_radio: IcomRadio,
        mock_transport: MockTransport,
        main_first: bool,
    ) -> None:
        main_response = _plain_filter_width_response(_INDEX_MAIN)
        sub_response = _plain_filter_width_response(_INDEX_SUB)
        ic9700_radio._civ_runtime.start_worker()
        try:
            if main_first:
                mock_transport.queue_response_on_send(1, main_response)
                mock_transport.queue_response_on_send(2, sub_response)
                main_width, sub_width = await asyncio.gather(
                    ic9700_radio.get_filter_width(receiver=0),
                    ic9700_radio.get_filter_width(receiver=1),
                )
            else:
                mock_transport.queue_response_on_send(1, sub_response)
                mock_transport.queue_response_on_send(2, main_response)
                sub_width, main_width = await asyncio.gather(
                    ic9700_radio.get_filter_width(receiver=1),
                    ic9700_radio.get_filter_width(receiver=0),
                )
            assert main_width == _WIDTH_MAIN_HZ
            assert sub_width == _WIDTH_SUB_HZ
            assert len(mock_transport.sent_packets) == 2
        finally:
            await ic9700_radio._civ_runtime.stop_worker()

    @pytest.mark.asyncio
    async def test_concurrent_same_receiver_reads_still_dedupe(
        self, ic9700_radio: IcomRadio, mock_transport: MockTransport
    ) -> None:
        """Control: the fallback branch's dedupe mechanism is intact, so a
        change that stopped deduping at the fallback site would not keep
        the test above green.

        Both reads use ``receiver=1`` (SUB), the only receiver that reaches
        the fallback call site on this profile -- ``receiver=0`` (MAIN)
        takes the direct branch, already covered by the direct-branch
        class's own control.
        """
        ic9700_radio._civ_runtime.start_worker()
        try:
            mock_transport.queue_response_on_send(
                1, _plain_filter_width_response(_INDEX_SUB)
            )
            first, second = await asyncio.gather(
                ic9700_radio.get_filter_width(receiver=1),
                ic9700_radio.get_filter_width(receiver=1),
            )
            assert first == _WIDTH_SUB_HZ
            assert second == _WIDTH_SUB_HZ
            assert len(mock_transport.sent_packets) == 1
        finally:
            await ic9700_radio._civ_runtime.stop_worker()
