"""MOR-1545 (PR #2458 follow-up 2): failure-path coverage for
``runtime/_dual_rx_runtime.py: DualRxRuntimeMixin._run_with_receiver_vfo_fallback``.

14 call sites route through this one helper (13 in ``runtime/radio.py``, 1
in ``runtime/_dual_rx_runtime.py`` itself -- counted via
``grep -rn '_run_with_receiver_vfo_fallback(' src/ | grep -v 'def _run_with_receiver_vfo_fallback'``),
yet it had zero direct tests -- existing tests only exercise the happy path
via public tone/TSQL methods. These call the private helper directly with a
stubbed ``_set_vfo_wire`` to pin the failure branches.
"""

from __future__ import annotations

import asyncio

import pytest

from rigplane.commands import CONTROLLER_ADDR, build_civ_frame
from rigplane.exceptions import TimeoutError
from rigplane.radio import IcomRadio

from test_radio import MockTransport, _wrap_civ_in_udp

_IC9700_ADDR = 0xA2


@pytest.fixture
def mock_transport() -> MockTransport:
    return MockTransport()


@pytest.fixture
def ic9700_radio() -> IcomRadio:
    """Dual-RX profile with VFO select codes but no cmd29 route -- the
    profile shape ``_run_with_receiver_vfo_fallback`` exists for."""
    r = IcomRadio("192.168.1.102", timeout=0.05, model="IC-9700")
    r._connected = True
    yield r
    r._connected = False


class TestActionRaisesMidSwap:
    """(a) action raises mid-swap -> VFO restored via finally."""

    @pytest.mark.asyncio
    async def test_restores_vfo_before_propagating(
        self, ic9700_radio: IcomRadio
    ) -> None:
        calls: list[str] = []

        async def fake_set_vfo_wire(vfo: str) -> None:
            calls.append(vfo)

        ic9700_radio._set_vfo_wire = fake_set_vfo_wire  # type: ignore[method-assign]

        async def failing_action() -> None:
            raise ValueError("boom mid-swap")

        with pytest.raises(ValueError, match="boom mid-swap"):
            await ic9700_radio._run_with_receiver_vfo_fallback(
                receiver=1, operation="test_op", action=failing_action
            )

        # select SUB, run the (failing) action, restore MAIN regardless.
        assert calls == ["SUB", "MAIN"]
        assert ic9700_radio._radio_state.active == "MAIN"


class TestTimeoutRetryOnRestore:
    """(b) TimeoutError retry branch on the restore-back call."""

    @pytest.mark.asyncio
    async def test_first_restore_times_out_retry_succeeds(
        self, ic9700_radio: IcomRadio
    ) -> None:
        calls: list[str] = []

        async def flaky_restore(vfo: str) -> None:
            calls.append(vfo)
            if vfo == "MAIN" and calls.count("MAIN") == 1:
                raise TimeoutError("restore timed out")

        ic9700_radio._set_vfo_wire = flaky_restore  # type: ignore[method-assign]

        async def action() -> str:
            return "action-result"

        result = await ic9700_radio._run_with_receiver_vfo_fallback(
            receiver=1, operation="test_op", action=action
        )

        assert result == "action-result"
        # select SUB, first restore attempt (times out), retry (succeeds).
        assert calls == ["SUB", "MAIN", "MAIN"]
        assert ic9700_radio._radio_state.active == "MAIN"

    @pytest.mark.asyncio
    async def test_retried_restore_also_fails_propagates(
        self, ic9700_radio: IcomRadio
    ) -> None:
        """Retry is attempted exactly once: if it also times out, the
        second TimeoutError propagates uncaught (no second retry)."""
        calls: list[str] = []

        async def always_timeout_restore(vfo: str) -> None:
            calls.append(vfo)
            if vfo == "MAIN":
                raise TimeoutError("restore timed out")

        ic9700_radio._set_vfo_wire = always_timeout_restore  # type: ignore[method-assign]

        async def action() -> str:
            return "action-result"

        with pytest.raises(TimeoutError, match="restore timed out"):
            await ic9700_radio._run_with_receiver_vfo_fallback(
                receiver=1, operation="test_op", action=action
            )

        assert calls == ["SUB", "MAIN", "MAIN"]


class TestSelectBackFailureSemantics:
    """(c) Pin -- not change -- what the ``finally`` does when the restore
    itself fails outright (both attempts time out)."""

    @pytest.mark.asyncio
    async def test_active_receiver_left_on_switched_target_when_restore_fails(
        self, ic9700_radio: IcomRadio
    ) -> None:
        """``_radio_state.active`` only updates on a successful restore;
        when both attempts fail it stays on the switched-to receiver (SUB).
        As-built behavior -- documented here, not endorsed or changed."""

        async def always_timeout_restore(vfo: str) -> None:
            if vfo == "MAIN":
                raise TimeoutError("restore timed out")

        ic9700_radio._set_vfo_wire = always_timeout_restore  # type: ignore[method-assign]

        async def action() -> str:
            return "action-result"

        with pytest.raises(TimeoutError):
            await ic9700_radio._run_with_receiver_vfo_fallback(
                receiver=1, operation="test_op", action=action
            )

        assert ic9700_radio._radio_state.active == "SUB"

    @pytest.mark.asyncio
    async def test_action_exception_is_masked_by_retried_restore_failure(
        self, ic9700_radio: IcomRadio
    ) -> None:
        """When BOTH action and retried restore raise, the caller sees the
        restore's TimeoutError, not the action's exception -- preserved
        only as ``__context__`` (two hops: retry -> first attempt -> the
        action's ValueError). Pinned, not endorsed."""

        async def always_timeout_restore(vfo: str) -> None:
            if vfo == "MAIN":
                raise TimeoutError("restore timed out")

        ic9700_radio._set_vfo_wire = always_timeout_restore  # type: ignore[method-assign]

        async def failing_action() -> None:
            raise ValueError("original action failure")

        with pytest.raises(TimeoutError) as excinfo:
            await ic9700_radio._run_with_receiver_vfo_fallback(
                receiver=1, operation="test_op", action=failing_action
            )

        first_restore_failure = excinfo.value.__context__
        assert isinstance(first_restore_failure, TimeoutError)
        original_action_failure = first_restore_failure.__context__
        assert isinstance(original_action_failure, ValueError)
        assert str(original_action_failure) == "original action failure"


class TestVfoFallbackReadDedupeKeyReceiverScoped:
    """MOR-1545 F1: the receiver-scoped dedupe-key fix also covers the
    VFO-fallback read call sites (``IcomRadio.get_repeater_tone`` /
    ``IcomRadio.get_repeater_tsql``), not just the direct cmd29 path. On
    IC-9700 (no cmd29 route) a MAIN direct read and a SUB fallback read
    build byte-identical request frames -- only the dedupe key can tell
    them apart. ``_set_vfo_wire`` is stubbed to an instant no-op so the SUB
    fallback path reaches its own dedupe check before the single-worker
    commander finishes dispatching MAIN's read, reproducing the coalescing
    race deterministically (pre-fix: SUB sends zero read frames of its own
    and returns MAIN's stale answer).

    Compare ``test_radio.py::TestRepeaterToneDedupeKeyReceiverScoped::
    test_concurrent_main_and_sub_reads_do_not_coalesce``: this test here
    stubs ``_set_vfo_wire`` with a yield-free no-op and asserts a 2-frame
    path (below), whereas that one drives the real 3-frame VFO
    select/restore path on the wire (select SUB, GET, restore MAIN). That
    difference in what each test actually exercises is why neither is
    redundant with the other."""

    @pytest.mark.asyncio
    async def test_concurrent_main_direct_and_sub_fallback_reads_do_not_coalesce(
        self, ic9700_radio: IcomRadio, mock_transport: MockTransport
    ) -> None:
        ic9700_radio._civ_transport = mock_transport
        ic9700_radio._ctrl_transport = mock_transport

        async def instant_select(vfo: str) -> None:
            return None

        ic9700_radio._set_vfo_wire = instant_select  # type: ignore[method-assign]
        ic9700_radio._civ_runtime.start_worker()
        try:
            mock_transport.queue_response_on_send(
                1,
                _wrap_civ_in_udp(
                    build_civ_frame(
                        CONTROLLER_ADDR, _IC9700_ADDR, 0x16, sub=0x42, data=b"\x01"
                    )
                ),
            )
            mock_transport.queue_response_on_send(
                2,
                _wrap_civ_in_udp(
                    build_civ_frame(
                        CONTROLLER_ADDR, _IC9700_ADDR, 0x16, sub=0x42, data=b"\x00"
                    )
                ),
            )
            main_result, sub_result = await asyncio.gather(
                ic9700_radio.get_repeater_tone(receiver=0),
                ic9700_radio.get_repeater_tone(receiver=1),
            )
            assert main_result is True
            assert sub_result is False
            assert len(mock_transport.sent_packets) == 2
        finally:
            await ic9700_radio._civ_runtime.stop_worker()
