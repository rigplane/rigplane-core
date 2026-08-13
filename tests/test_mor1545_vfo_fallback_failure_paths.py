"""MOR-1545 (PR #2458 follow-up 2): failure-path coverage for
``_run_with_receiver_vfo_fallback`` (``_dual_rx_runtime.py:102-147``).

13 call sites route through this one helper (VFO-select receiver targeting
on cmd29-less dual-RX profiles, e.g. IC-9700), yet it had zero direct tests
-- existing tests only exercise the happy path via public tone/TSQL methods.
These call the private helper directly with a stubbed ``_set_vfo_wire`` to
pin the failure branches.
"""

from __future__ import annotations

import pytest

from rigplane.exceptions import TimeoutError
from rigplane.radio import IcomRadio


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
