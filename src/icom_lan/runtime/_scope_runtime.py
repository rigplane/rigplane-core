"""ScopeRuntimeMixin — scope/waterfall methods extracted from CoreRadio.

Part of the radio.py decomposition (#505). All methods are accessed via
``IcomRadio`` which inherits this mixin.
"""

from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING, AsyncGenerator

if TYPE_CHECKING:
    from typing import Callable

    from .radio import CoreRadio as _MixinBase  # type: ignore[attr-defined]
else:
    _MixinBase = object

from icom_lan.commands import (
    parse_ack_nak,
    parse_civ_frame,
    parse_scope_center_type_response,
    parse_scope_during_tx_response,
    parse_scope_edge_response,
    parse_scope_fixed_edge_response,
    parse_scope_hold_response,
    parse_scope_main_sub_response,
    parse_scope_mode_response,
    parse_scope_rbw_response,
    parse_scope_ref_response,
    parse_scope_single_dual_response,
    parse_scope_span_response,
    parse_scope_speed_response,
    parse_scope_vbw_response,
)
from icom_lan.commands import get_scope_center_type as _get_scope_center_type_cmd
from icom_lan.commands import get_scope_during_tx as _get_scope_during_tx_cmd
from icom_lan.commands import get_scope_edge as _get_scope_edge_cmd
from icom_lan.commands import get_scope_fixed_edge as _get_scope_fixed_edge_cmd
from icom_lan.commands import get_scope_hold as _get_scope_hold_cmd
from icom_lan.commands import get_scope_main_sub as _get_scope_main_sub_cmd
from icom_lan.commands import get_scope_mode as _get_scope_mode_cmd
from icom_lan.commands import get_scope_rbw as _get_scope_rbw_cmd
from icom_lan.commands import get_scope_ref as _get_scope_ref_cmd
from icom_lan.commands import get_scope_single_dual as _get_scope_single_dual_cmd
from icom_lan.commands import get_scope_span as _get_scope_span_cmd
from icom_lan.commands import get_scope_speed as _get_scope_speed_cmd
from icom_lan.commands import get_scope_vbw as _get_scope_vbw_cmd
from icom_lan.commands import scope_data_output as _scope_data_output_cmd
from icom_lan.commands import scope_main_sub as _scope_main_sub_cmd
from icom_lan.commands import scope_on as _scope_on_cmd
from icom_lan.commands import scope_set_center_type as _scope_set_center_type_cmd
from icom_lan.commands import scope_set_during_tx as _scope_set_during_tx_cmd
from icom_lan.commands import scope_set_edge as _scope_set_edge_cmd
from icom_lan.commands import scope_set_fixed_edge as _scope_set_fixed_edge_cmd
from icom_lan.commands import scope_set_hold as _scope_set_hold_cmd
from icom_lan.commands import scope_set_mode as _scope_set_mode_cmd
from icom_lan.commands import scope_set_rbw as _scope_set_rbw_cmd
from icom_lan.commands import scope_set_ref as _scope_set_ref_cmd
from icom_lan.commands import scope_set_span as _scope_set_span_cmd
from icom_lan.commands import scope_set_speed as _scope_set_speed_cmd
from icom_lan.commands import scope_set_vbw as _scope_set_vbw_cmd
from icom_lan.commands import scope_single_dual as _scope_single_dual_cmd
from icom_lan.core.exceptions import CommandError, TimeoutError
from icom_lan.core.radio_state import ScopeControlsState
from icom_lan.scope import ScopeFrame
from icom_lan.core.types import ScopeCompletionPolicy, ScopeFixedEdge

logger = logging.getLogger(__name__)


class ScopeRuntimeMixin(_MixinBase):  # type: ignore[misc]
    """Scope/waterfall methods for CoreRadio (mixin)."""

    # ------------------------------------------------------------------
    # Scope / Waterfall API
    # ------------------------------------------------------------------

    def on_scope_data(self, callback: Callable[[ScopeFrame], None] | None) -> None:
        """Register a callback for completed scope frames.

        Args:
            callback: Function taking a ScopeFrame, or None to unregister.
        """
        self._scope_callback = callback

    def _scope_controls(self) -> ScopeControlsState:
        """Return the mutable scope-control state bucket."""
        return self._radio_state.scope_controls  # type: ignore[no-any-return]

    def _apply_scope_receiver_hint(self, receiver: int | None) -> None:
        if receiver is not None:
            self._scope_controls().receiver = receiver

    async def scope_stream(self) -> AsyncGenerator[ScopeFrame, None]:
        """Consume scope frames asynchronously.

        Yields:
            ScopeFrame objects as they are assembled.
            Stops yielding if the radio disconnects.

        Note:
            Uses a bounded queue (maxsize=64) that drops oldest frames if not
            consumed fast enough. Call enable_scope() separately to start data.
        """
        while self._connected:
            try:
                frame = await asyncio.wait_for(
                    self._scope_frame_queue.get(), timeout=1.0
                )
                yield frame
                self._scope_frame_queue.task_done()
            except asyncio.TimeoutError:
                continue

    async def enable_scope(
        self,
        *,
        output: bool = True,
        policy: ScopeCompletionPolicy | str = ScopeCompletionPolicy.VERIFY,
        timeout: float = 5.0,
    ) -> None:
        """Enable scope display and data output on the radio.

        Args:
            output: Also enable wave data output (default True).
            policy: Completion policy (strict, fast, verify).
            timeout: Verification timeout in seconds.

        Raises:
            CommandError: If the radio rejects the command (in strict mode).
            TimeoutError: If verification times out (in verify mode).
        """
        self._check_connected()
        pol = ScopeCompletionPolicy(policy)
        wait_resp = pol == ScopeCompletionPolicy.STRICT

        if pol == ScopeCompletionPolicy.VERIFY:
            self._scope_activity_event.clear()

        resp = await self._send_civ_raw(
            _scope_on_cmd(to_addr=self._radio_addr), wait_response=wait_resp
        )
        if wait_resp and resp is not None:
            if parse_ack_nak(resp) is False:
                raise CommandError("Radio rejected scope enable")
        if output:
            resp = await self._send_civ_raw(
                _scope_data_output_cmd(True, to_addr=self._radio_addr),
                wait_response=wait_resp,
            )
            if wait_resp and resp is not None:
                if parse_ack_nak(resp) is False:
                    raise CommandError("Radio rejected scope data output enable")

        if pol == ScopeCompletionPolicy.VERIFY:
            try:
                await asyncio.wait_for(
                    self._scope_activity_event.wait(), timeout=timeout
                )
            except asyncio.TimeoutError:
                raise TimeoutError("Scope enable verification timed out (no data seen)")

    async def disable_scope(
        self, *, policy: ScopeCompletionPolicy | str = ScopeCompletionPolicy.FAST
    ) -> None:
        """Disable scope data output on the radio.

        Args:
            policy: Completion policy, usually fast.

        Raises:
            CommandError: If the radio rejects the command (strict mode).
        """
        self._check_connected()
        pol = ScopeCompletionPolicy(policy)
        wait_resp = pol == ScopeCompletionPolicy.STRICT

        resp = await self._send_civ_raw(
            _scope_data_output_cmd(False, to_addr=self._radio_addr),
            wait_response=wait_resp,
        )
        if wait_resp and resp is not None:
            if parse_ack_nak(resp) is False:
                raise CommandError("Radio rejected scope data output disable")

    async def get_scope_receiver(self) -> int:
        """Read the selected scope receiver (0=MAIN, 1=SUB)."""
        self._check_connected()
        resp = await self._send_civ_expect(
            _get_scope_main_sub_cmd(to_addr=self._radio_addr),
            label="get_scope_receiver",
        )
        receiver = parse_scope_main_sub_response(resp)
        self._scope_controls().receiver = receiver
        return receiver

    async def set_scope_receiver(self, receiver: int) -> None:
        """Select the scope receiver (0=MAIN, 1=SUB)."""
        self._check_connected()
        if receiver not in (0, 1):
            raise ValueError(f"scope receiver must be 0 or 1, got {receiver}")
        await self._send_civ_raw(
            _scope_main_sub_cmd(receiver, to_addr=self._radio_addr),
            wait_response=False,
        )
        self._scope_controls().receiver = receiver

    async def get_scope_dual(self) -> bool:
        """Read whether the scope is in dual-display mode."""
        self._check_connected()
        resp = await self._send_civ_expect(
            _get_scope_single_dual_cmd(to_addr=self._radio_addr),
            label="get_scope_dual",
        )
        dual: bool = parse_scope_single_dual_response(resp)
        self._scope_controls().dual = dual
        return dual

    async def set_scope_dual(self, dual: bool) -> None:
        """Enable or disable dual scope mode."""
        self._check_connected()
        receiver = self._scope_controls().receiver
        await self._send_civ_raw(
            _scope_single_dual_cmd(dual, to_addr=self._radio_addr, receiver=receiver),
            wait_response=False,
        )
        self._scope_controls().dual = dual

    async def get_scope_mode(self) -> int:
        """Read the current scope mode (0=center, 1=fixed, 2=scroll-C, 3=scroll-F)."""
        self._check_connected()
        receiver = self._scope_controls().receiver
        resp = await self._send_civ_expect(
            _get_scope_mode_cmd(to_addr=self._radio_addr, receiver=receiver),
            label="get_scope_mode",
        )
        rx_hint, mode = parse_scope_mode_response(resp)
        self._apply_scope_receiver_hint(rx_hint)
        self._scope_controls().mode = mode
        return mode

    async def set_scope_mode(self, mode: int) -> None:
        """Set the scope mode (0=center, 1=fixed, 2=scroll-C, 3=scroll-F)."""
        self._check_connected()
        receiver = self._scope_controls().receiver
        await self._send_civ_raw(
            _scope_set_mode_cmd(mode, to_addr=self._radio_addr, receiver=receiver),
            wait_response=False,
        )
        self._scope_controls().mode = mode

    async def get_scope_span(self) -> int:
        """Read the scope span preset index (0..7)."""
        self._check_connected()
        receiver = self._scope_controls().receiver
        resp = await self._send_civ_expect(
            _get_scope_span_cmd(to_addr=self._radio_addr, receiver=receiver),
            label="get_scope_span",
        )
        rx_hint, span = parse_scope_span_response(resp)
        self._apply_scope_receiver_hint(rx_hint)
        self._scope_controls().span = span
        return span

    async def set_scope_span(self, span: int) -> None:
        """Set the scope span preset index (0..7)."""
        self._check_connected()
        receiver = self._scope_controls().receiver
        await self._send_civ_raw(
            _scope_set_span_cmd(span, to_addr=self._radio_addr, receiver=receiver),
            wait_response=False,
        )
        self._scope_controls().span = span

    async def get_scope_edge(self) -> int:
        """Read the fixed-edge selection (1..4)."""
        self._check_connected()
        receiver = self._scope_controls().receiver
        resp = await self._send_civ_expect(
            _get_scope_edge_cmd(to_addr=self._radio_addr, receiver=receiver),
            label="get_scope_edge",
        )
        rx_hint, edge = parse_scope_edge_response(resp)
        self._apply_scope_receiver_hint(rx_hint)
        self._scope_controls().edge = edge
        return edge

    async def set_scope_edge(self, edge: int) -> None:
        """Set the fixed-edge selection (1..4)."""
        self._check_connected()
        receiver = self._scope_controls().receiver
        await self._send_civ_raw(
            _scope_set_edge_cmd(edge, to_addr=self._radio_addr, receiver=receiver),
            wait_response=False,
        )
        self._scope_controls().edge = edge

    async def get_scope_hold(self) -> bool:
        """Read whether scope hold is enabled."""
        self._check_connected()
        receiver = self._scope_controls().receiver
        resp = await self._send_civ_expect(
            _get_scope_hold_cmd(to_addr=self._radio_addr, receiver=receiver),
            label="get_scope_hold",
        )
        rx_hint, hold = parse_scope_hold_response(resp)
        self._apply_scope_receiver_hint(rx_hint)
        self._scope_controls().hold = hold
        return hold

    async def set_scope_hold(self, on: bool) -> None:
        """Enable or disable scope hold."""
        self._check_connected()
        receiver = self._scope_controls().receiver
        await self._send_civ_raw(
            _scope_set_hold_cmd(on, to_addr=self._radio_addr, receiver=receiver),
            wait_response=False,
        )
        self._scope_controls().hold = on

    async def get_scope_ref(self) -> float:
        """Read the scope reference level in dB."""
        self._check_connected()
        receiver = self._scope_controls().receiver
        resp = await self._send_civ_expect(
            _get_scope_ref_cmd(to_addr=self._radio_addr, receiver=receiver),
            label="get_scope_ref",
        )
        rx_hint, ref_db = parse_scope_ref_response(resp)
        self._apply_scope_receiver_hint(rx_hint)
        self._scope_controls().ref_db = ref_db
        return ref_db

    async def set_scope_ref(self, ref: float) -> None:
        """Set the scope reference level in dB."""
        self._check_connected()
        receiver = self._scope_controls().receiver
        await self._send_civ_raw(
            _scope_set_ref_cmd(ref, to_addr=self._radio_addr, receiver=receiver),
            wait_response=False,
        )
        self._scope_controls().ref_db = ref

    async def get_scope_speed(self) -> int:
        """Read the scope speed preset (0=fast, 1=mid, 2=slow)."""
        self._check_connected()
        receiver = self._scope_controls().receiver
        resp = await self._send_civ_expect(
            _get_scope_speed_cmd(to_addr=self._radio_addr, receiver=receiver),
            label="get_scope_speed",
        )
        rx_hint, speed = parse_scope_speed_response(resp)
        self._apply_scope_receiver_hint(rx_hint)
        self._scope_controls().speed = speed
        return speed

    async def set_scope_speed(self, speed: int) -> None:
        """Set the scope speed preset (0=fast, 1=mid, 2=slow)."""
        self._check_connected()
        receiver = self._scope_controls().receiver
        await self._send_civ_raw(
            _scope_set_speed_cmd(speed, to_addr=self._radio_addr, receiver=receiver),
            wait_response=False,
        )
        self._scope_controls().speed = speed

    async def get_scope_during_tx(self) -> bool:
        """Read whether the scope remains visible during transmit."""
        self._check_connected()
        resp = await self._send_civ_expect(
            _get_scope_during_tx_cmd(to_addr=self._radio_addr),
            label="get_scope_during_tx",
        )
        during_tx = parse_scope_during_tx_response(resp)
        self._scope_controls().during_tx = during_tx
        return during_tx

    async def set_scope_during_tx(self, on: bool) -> None:
        """Enable or disable scope during transmit."""
        self._check_connected()
        await self._send_civ_raw(
            _scope_set_during_tx_cmd(on, to_addr=self._radio_addr),
            wait_response=False,
        )
        self._scope_controls().during_tx = on

    async def get_scope_center_type(self) -> int:
        """Read the scope center-type setting (0..2)."""
        self._check_connected()
        resp = await self._send_civ_expect(
            _get_scope_center_type_cmd(to_addr=self._radio_addr),
            label="get_scope_center_type",
        )
        receiver, center_type = parse_scope_center_type_response(resp)
        self._apply_scope_receiver_hint(receiver)
        self._scope_controls().center_type = center_type
        return center_type

    async def set_scope_center_type(self, center_type: int) -> None:
        """Set the scope center-type setting (0..2)."""
        self._check_connected()
        await self._send_civ_raw(
            _scope_set_center_type_cmd(center_type, to_addr=self._radio_addr),
            wait_response=False,
        )
        self._scope_controls().center_type = center_type

    async def get_scope_vbw(self) -> bool:
        """Read whether narrow scope VBW is enabled."""
        self._check_connected()
        receiver = self._scope_controls().receiver
        resp = await self._send_civ_expect(
            _get_scope_vbw_cmd(to_addr=self._radio_addr, receiver=receiver),
            label="get_scope_vbw",
        )
        rx_hint, narrow = parse_scope_vbw_response(resp)
        self._apply_scope_receiver_hint(rx_hint)
        self._scope_controls().vbw_narrow = narrow
        return narrow

    async def set_scope_vbw(self, narrow: bool) -> None:
        """Enable or disable narrow scope VBW."""
        self._check_connected()
        await self._send_civ_raw(
            _scope_set_vbw_cmd(narrow, to_addr=self._radio_addr),
            wait_response=False,
        )
        self._scope_controls().vbw_narrow = narrow

    async def get_scope_fixed_edge(self) -> ScopeFixedEdge:
        """Read the fixed-edge scope bounds."""
        self._check_connected()
        resp = await self._send_civ_expect(
            _get_scope_fixed_edge_cmd(to_addr=self._radio_addr),
            label="get_scope_fixed_edge",
        )
        fixed_edge = parse_scope_fixed_edge_response(resp)
        self._scope_controls().fixed_edge = fixed_edge
        self._scope_controls().edge = fixed_edge.edge
        return fixed_edge

    async def set_scope_fixed_edge(
        self,
        *,
        edge: int,
        start_hz: int,
        end_hz: int,
        range_index: int | None = None,
    ) -> None:
        """Set the fixed-edge scope bounds."""
        self._check_connected()
        civ = _scope_set_fixed_edge_cmd(
            edge=edge,
            start_hz=start_hz,
            end_hz=end_hz,
            range_index=range_index,
            to_addr=self._radio_addr,
        )
        await self._send_civ_raw(
            civ,
            wait_response=False,
        )
        # Re-parse the frame we just built to recover the resolved range_index
        # (computed inside scope_set_fixed_edge_cmd) without duplicating logic.
        fixed_edge = parse_scope_fixed_edge_response(parse_civ_frame(civ))
        self._scope_controls().fixed_edge = fixed_edge
        self._scope_controls().edge = fixed_edge.edge

    async def get_scope_rbw(self) -> int:
        """Read the scope RBW preset (0=wide, 1=mid, 2=narrow)."""
        self._check_connected()
        receiver = self._scope_controls().receiver
        resp = await self._send_civ_expect(
            _get_scope_rbw_cmd(to_addr=self._radio_addr, receiver=receiver),
            label="get_scope_rbw",
        )
        rx_hint, rbw = parse_scope_rbw_response(resp)
        self._apply_scope_receiver_hint(rx_hint)
        self._scope_controls().rbw = rbw
        return rbw

    async def set_scope_rbw(self, rbw: int) -> None:
        """Set the scope RBW preset (0=wide, 1=mid, 2=narrow)."""
        self._check_connected()
        await self._send_civ_raw(
            _scope_set_rbw_cmd(rbw, to_addr=self._radio_addr),
            wait_response=False,
        )
        self._scope_controls().rbw = rbw

    async def capture_scope_frame(self, timeout: float = 5.0) -> ScopeFrame:
        """Enable scope and capture one complete frame.

        Does NOT disable scope after — caller decides when to stop.

        Args:
            timeout: Maximum time to wait for a frame in seconds.

        Returns:
            First complete ScopeFrame received.

        Raises:
            TimeoutError: If no frame is received within timeout.
        """
        frames = await self.capture_scope_frames(count=1, timeout=timeout)
        return frames[0]

    async def capture_scope_frames(
        self, count: int = 50, timeout: float = 10.0
    ) -> list[ScopeFrame]:
        """Enable scope and capture *count* complete frames.

        Does NOT disable scope after — caller decides when to stop.

        Args:
            count: Number of complete frames to capture.
            timeout: Maximum time to wait in seconds.

        Returns:
            List of ScopeFrame objects, oldest first.

        Raises:
            TimeoutError: If fewer than *count* frames arrive within timeout.
        """
        self._check_connected()

        collected: list[ScopeFrame] = []
        frame_ready = asyncio.Event()

        def _on_frame(frame: ScopeFrame) -> None:
            collected.append(frame)
            if len(collected) >= count:
                frame_ready.set()

        old_callback = self._scope_callback
        self.on_scope_data(_on_frame)
        try:
            await self.enable_scope(
                policy=ScopeCompletionPolicy.VERIFY, timeout=timeout
            )
            try:
                await asyncio.wait_for(frame_ready.wait(), timeout=timeout)
            except asyncio.TimeoutError:
                raise TimeoutError(
                    f"Scope capture timed out: received {len(collected)}/{count} frames"
                )
        finally:
            self.on_scope_data(old_callback)
        return collected[:count]
