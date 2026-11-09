"""ScopeRuntimeMixin — scope/waterfall methods extracted from CoreRadio.

Part of the radio.py decomposition (#505). All methods are accessed via
``IcomRadio`` which inherits this mixin.
"""

from __future__ import annotations

from rigplane.scope.models import ScopeDemand

import asyncio
import logging
import warnings
from typing import TYPE_CHECKING, AsyncGenerator, Literal

if TYPE_CHECKING:
    from typing import Callable

    from .radio import CoreRadio as _MixinBase  # type: ignore[attr-defined]
else:
    _MixinBase = object

from rigplane.commands import (
    get_scope_center_type,
    get_scope_data_output_enabled,
    get_scope_during_tx,
    get_scope_edge,
    get_scope_enabled,
    get_scope_fixed_edge,
    get_scope_hold,
    get_scope_main_sub,
    get_scope_mode,
    get_scope_rbw,
    get_scope_ref,
    get_scope_single_dual,
    get_scope_span,
    get_scope_speed,
    get_scope_vbw,
    parse_ack_nak,
    parse_scope_center_type_response,
    parse_scope_data_output_enabled_response,
    parse_scope_during_tx_response,
    parse_scope_edge_response,
    parse_scope_enabled_response,
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
from rigplane.core.exceptions import CommandError, TimeoutError
from rigplane.core._bounded_queue import BoundedQueue
from rigplane.core.radio_state import ScopeControlsState
from rigplane.scope import ScopeFrame
from rigplane.core.types import ScopeCompletionPolicy, ScopeFixedEdge
from rigplane.runtime.scope_session import (
    ScopeSession,
    ScopeState,
    ScopeSetting,
    ScopeValue,
)

logger = logging.getLogger(__name__)


class ScopeRuntimeMixin(_MixinBase):  # type: ignore[misc]
    """Scope/waterfall methods for CoreRadio (mixin)."""

    _scope_stream_callbacks: list[Callable[[ScopeFrame], None]]

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
        """Consume frames through the deprecated SDK callback adapter.

        .. deprecated::
           Use :meth:`on_scope_data` for native frames or the scope WebSocket
           subscription API. Each active iterator has a bounded 64-frame
           buffer; call :meth:`enable_scope` separately to start data.
        """
        warnings.warn(
            "scope_stream() is deprecated; use on_scope_data() or a scope "
            "WebSocket subscription",
            DeprecationWarning,
            stacklevel=2,
        )
        queue: BoundedQueue[ScopeFrame] = BoundedQueue(maxsize=64)
        callback = queue.put_drop_oldest
        self._scope_stream_callbacks.append(callback)
        try:
            while self._connected:
                try:
                    frame = await asyncio.wait_for(queue.get(), timeout=1.0)
                except asyncio.TimeoutError:
                    continue
                try:
                    yield frame
                finally:
                    queue.task_done()
        finally:
            self._scope_stream_callbacks.remove(callback)

    def scope_session(self) -> ScopeSession:
        """Return the single scope-control owner for this radio."""
        session = getattr(self, "_control_scope_session", None)
        if not isinstance(session, ScopeSession):
            session = ScopeSession(
                self._read_scope_session,
                self._write_scope_setting,
                lambda range_index, edge: self.get_scope_fixed_edge(
                    range_index=range_index, edge=edge
                ),
                self._read_scope_receiver,
                lambda receiver: float(
                    self._radio_state.main.freq
                    if receiver == 0
                    else self._radio_state.sub.freq
                ),
                self._profile.scope_span_presets_hz,
                lambda: bool(self._radio_state.ptt),
                ready=self._connected,
                validate=self._scope_setting_frame,
            )
            self._control_scope_session = session
        return session

    async def _scope_session_ready(self) -> None:
        session = getattr(self, "_control_scope_session", None)
        if isinstance(session, ScopeSession):
            try:
                await session.set_ready(True)
            except Exception:
                logger.warning(
                    "scope reconciliation remains pending after readiness",
                    exc_info=True,
                )

    async def _read_scope_receiver(self, receiver: int) -> ScopeState:
        _, mode = await self._read_scope_view_setting("mode", receiver)
        _, span = await self._read_scope_view_setting("span", receiver)
        _, edge = await self._read_scope_view_setting("edge", receiver)
        return ScopeState(False, False, mode, span, edge)

    async def _read_scope_view_setting(
        self, name: Literal["mode", "span", "edge"], receiver: int
    ) -> tuple[int | None, int]:
        """Read an explicit receiver without changing shared SDK selection/cache."""
        self._check_connected()
        builder = getattr(self._commands, f"get_scope_{name}")
        resp = await self._send_civ_expect(
            builder(to_addr=self._radio_addr, receiver=receiver),
            label=f"get_scope_{name}",
        )
        if name == "span":
            command, sub, _ = self._expect_shape(get_scope_span)
            return parse_scope_span_response(
                resp, self._profile.scope_span_presets_hz, command=command, sub=sub
            )
        if name == "mode":
            command, sub, _ = self._expect_shape(get_scope_mode)
            return parse_scope_mode_response(resp, command=command, sub=sub)
        command, sub, _ = self._expect_shape(get_scope_edge)
        return parse_scope_edge_response(resp, command=command, sub=sub)

    async def _read_scope_session(self) -> ScopeState:
        panel, output = await self.get_scope_session_state()
        controls = self._scope_controls()
        return ScopeState(
            panel, output, controls.mode or 0, controls.span or 0, controls.edge or 1,
            await self.get_scope_dual() if self._profile.receiver_count > 1 else False,
        )

    def _scope_setting_frame(
        self, name: ScopeSetting, value: ScopeValue, receiver: int
    ) -> bytes:
        if name == "scope_on":
            builder = self._commands.scope_on if value else self._commands.scope_off
            frame = builder(to_addr=self._radio_addr)
        elif name == "output_on":
            frame = self._commands.scope_data_output(
                bool(value), to_addr=self._radio_addr
            )
        elif name == "dual":
            frame = self._commands.scope_single_dual(bool(value), to_addr=self._radio_addr)
        elif name == "fixed_edge":
            assert isinstance(value, ScopeFixedEdge)
            frame = self._commands.scope_set_fixed_edge(
                edge=value.edge,
                start_hz=value.start_hz,
                end_hz=value.end_hz,
                range_index=value.range_index,
                to_addr=self._radio_addr,
            )
        elif name == "span":
            assert isinstance(value, int)
            frame = self._commands.scope_set_span(
                value,
                presets=self._profile.scope_span_presets_hz,
                receiver=receiver,
                to_addr=self._radio_addr,
            )
        else:
            assert isinstance(value, int)
            builder = (
                self._commands.scope_set_mode
                if name == "mode"
                else self._commands.scope_set_edge
            )
            frame = builder(value, receiver=receiver, to_addr=self._radio_addr)
        return frame

    async def _write_scope_setting(
        self, name: ScopeSetting, value: ScopeValue, receiver: int
    ) -> None:
        """The sole writer for scope lifecycle and viewport commands."""
        self._check_connected()
        if self.external_cat_session_active:
            raise CommandError(
                "scope restore deferred while an external CAT session owns the wire"
            )
        frame = self._scope_setting_frame(name, value, receiver)
        response = await self._send_civ_raw(frame, wait_response=True)
        if response is None or parse_ack_nak(response) is not True:
            raise CommandError(f"Radio did not acknowledge scope {name}")
        controls = self._scope_controls()
        if name == "dual":
            controls.dual = bool(value)
        elif name == "fixed_edge":
            assert isinstance(value, ScopeFixedEdge)
            controls.fixed_edge = value
        elif name not in ("scope_on", "output_on") and receiver == controls.receiver:
            setattr(controls, name, value)

    async def enable_scope(
        self,
        *,
        output: bool = True,
        policy: ScopeCompletionPolicy | str = ScopeCompletionPolicy.VERIFY,
        timeout: float = 5.0,
    ) -> None:
        """Acquire SDK scope demand through the shared acknowledged session."""
        self._check_connected()
        pol = ScopeCompletionPolicy(policy)
        if pol == ScopeCompletionPolicy.VERIFY or not self.scope_session().enabled:
            self._scope_activity_event.clear()
        await self.scope_session().set_demand(ScopeDemand(1, int(output)), owner="sdk")
        if not output:
            await self.scope_session().configure(scope_on=True)
        if output and pol == ScopeCompletionPolicy.VERIFY:
            try:
                await asyncio.wait_for(
                    self._scope_activity_event.wait(), timeout=timeout
                )
            except asyncio.TimeoutError:
                raise TimeoutError("Scope enable verification timed out (no data seen)")

    async def get_scope_session_state(self) -> tuple[bool, bool]:
        """Read panel-scope and waveform-output state without changing either."""
        self._check_connected()
        panel_response = await self._send_civ_expect(
            self._commands.get_scope_enabled(to_addr=self._radio_addr),
            label="get_scope_enabled",
        )
        output_response = await self._send_civ_expect(
            self._commands.get_scope_data_output_enabled(to_addr=self._radio_addr),
            label="get_scope_data_output_enabled",
        )
        panel_command, panel_sub, _ = self._expect_shape(get_scope_enabled)
        output_command, output_sub, _ = self._expect_shape(
            get_scope_data_output_enabled
        )
        return (
            parse_scope_enabled_response(
                panel_response, command=panel_command, sub=panel_sub
            ),
            parse_scope_data_output_enabled_response(
                output_response, command=output_command, sub=output_sub
            ),
        )

    async def restore_scope_session_state(self, state: tuple[bool, bool]) -> None:
        """Apply an explicit SDK panel/output snapshot through the shared writer."""
        session = self.scope_session()
        await session.set_demand(ScopeDemand(0, 0), owner="sdk")
        if not session.active:
            await session.configure(scope_on=state[0], output_on=state[1])

    async def disable_scope(
        self, *, policy: ScopeCompletionPolicy | str = ScopeCompletionPolicy.FAST
    ) -> None:
        """Release SDK demand and restore the pre-session scope settings."""
        ScopeCompletionPolicy(policy)
        await self.scope_session().set_demand(ScopeDemand(0, 0), owner="sdk")

    async def get_scope_receiver(self) -> int:
        """Read the selected scope receiver (0=MAIN, 1=SUB)."""
        self._check_connected()
        resp = await self._send_civ_expect(
            self._commands.get_scope_main_sub(to_addr=self._radio_addr),
            label="get_scope_receiver",
        )
        command, sub, _ = self._expect_shape(get_scope_main_sub)
        receiver = parse_scope_main_sub_response(resp, command=command, sub=sub)
        self._scope_controls().receiver = receiver
        return receiver

    async def set_scope_receiver(self, receiver: int) -> None:
        """Select the scope receiver (0=MAIN, 1=SUB)."""
        self._check_connected()
        if receiver not in (0, 1):
            raise ValueError(f"scope receiver must be 0 or 1, got {receiver}")
        await self._send_civ_raw(
            self._commands.scope_main_sub(receiver, to_addr=self._radio_addr),
            wait_response=False,
        )
        self._scope_controls().receiver = receiver

    async def get_scope_dual(self) -> bool:
        """Read whether the scope is in dual-display mode."""
        self._check_connected()
        resp = await self._send_civ_expect(
            self._commands.get_scope_single_dual(to_addr=self._radio_addr),
            label="get_scope_dual",
        )
        command, sub, _ = self._expect_shape(get_scope_single_dual)
        dual: bool = parse_scope_single_dual_response(resp, command=command, sub=sub)
        self._scope_controls().dual = dual
        return dual

    async def set_scope_dual(self, dual: bool) -> None:
        """Enable or disable dual scope mode."""
        self._check_connected()
        # Single/dual is a GLOBAL scope setting — get_scope_dual sends no
        # receiver prefix, so the setter must not either (MOR-664). A stray
        # receiver byte malforms the write and the radio ignores it.
        await self._send_civ_raw(
            self._commands.scope_single_dual(dual, to_addr=self._radio_addr),
            wait_response=False,
        )
        self._scope_controls().dual = dual

    async def get_scope_mode(self) -> int:
        """Read the current scope mode (0=center, 1=fixed, 2=scroll-C, 3=scroll-F)."""
        rx_hint, mode = await self._read_scope_view_setting(
            "mode", self._scope_controls().receiver
        )
        self._apply_scope_receiver_hint(rx_hint)
        self._scope_controls().mode = mode
        return mode

    async def set_scope_mode(self, mode: int) -> None:
        """Set the scope mode through the shared session."""
        await self.scope_session().configure(
            receiver=self._scope_controls().receiver, mode=mode
        )

    async def get_scope_span(self) -> int:
        """Read the scope span preset index (0..7)."""
        rx_hint, span = await self._read_scope_view_setting(
            "span", self._scope_controls().receiver
        )
        self._apply_scope_receiver_hint(rx_hint)
        self._scope_controls().span = span
        return span

    async def set_scope_span(self, span: int) -> None:
        """Set the scope span through the shared session."""
        await self.scope_session().configure(
            receiver=self._scope_controls().receiver, span=span
        )

    async def get_scope_edge(self) -> int:
        """Read the fixed-edge selection (1..4)."""
        rx_hint, edge = await self._read_scope_view_setting(
            "edge", self._scope_controls().receiver
        )
        self._apply_scope_receiver_hint(rx_hint)
        self._scope_controls().edge = edge
        return edge

    async def set_scope_edge(self, edge: int) -> None:
        """Set the fixed-edge slot through the shared session."""
        await self.scope_session().configure(
            receiver=self._scope_controls().receiver, edge=edge
        )

    async def get_scope_hold(self) -> bool:
        """Read whether scope hold is enabled."""
        self._check_connected()
        receiver = self._scope_controls().receiver
        resp = await self._send_civ_expect(
            self._commands.get_scope_hold(to_addr=self._radio_addr, receiver=receiver),
            label="get_scope_hold",
        )
        command, sub, _ = self._expect_shape(get_scope_hold)
        rx_hint, hold = parse_scope_hold_response(resp, command=command, sub=sub)
        self._apply_scope_receiver_hint(rx_hint)
        self._scope_controls().hold = hold
        return hold

    async def set_scope_hold(self, on: bool) -> None:
        """Enable or disable scope hold."""
        self._check_connected()
        receiver = self._scope_controls().receiver
        await self._send_civ_raw(
            self._commands.scope_set_hold(
                on, to_addr=self._radio_addr, receiver=receiver
            ),
            wait_response=False,
        )
        self._scope_controls().hold = on

    async def get_scope_ref(self) -> float:
        """Read the scope reference level in dB."""
        self._check_connected()
        receiver = self._scope_controls().receiver
        resp = await self._send_civ_expect(
            self._commands.get_scope_ref(to_addr=self._radio_addr, receiver=receiver),
            label="get_scope_ref",
        )
        command, sub, _ = self._expect_shape(get_scope_ref)
        rx_hint, ref_db = parse_scope_ref_response(resp, command=command, sub=sub)
        self._apply_scope_receiver_hint(rx_hint)
        self._scope_controls().ref_db = ref_db
        return ref_db

    async def set_scope_ref(self, ref: float) -> None:
        """Set the scope reference level in dB.

        The range check uses the profile's declared ``[scope]``
        ``ref_min_db``/``ref_max_db`` (passed in because ``commands/``
        may not import ``profiles/``, the same contract as span presets);
        with none declared, the encoder's own historical IC-7610 default
        applies.
        """
        self._check_connected()
        receiver = self._scope_controls().receiver
        ref_bounds = (
            (self._profile.scope_ref_min_db, self._profile.scope_ref_max_db)
            if self._profile.scope_ref_min_db is not None
            and self._profile.scope_ref_max_db is not None
            else None
        )
        await self._send_civ_raw(
            self._commands.scope_set_ref(
                ref,
                to_addr=self._radio_addr,
                receiver=receiver,
                ref_bounds=ref_bounds,
            ),
            wait_response=False,
        )
        self._scope_controls().ref_db = ref

    async def get_scope_speed(self) -> int:
        """Read the scope speed preset (0=fast, 1=mid, 2=slow)."""
        self._check_connected()
        receiver = self._scope_controls().receiver
        resp = await self._send_civ_expect(
            self._commands.get_scope_speed(to_addr=self._radio_addr, receiver=receiver),
            label="get_scope_speed",
        )
        command, sub, _ = self._expect_shape(get_scope_speed)
        rx_hint, speed = parse_scope_speed_response(resp, command=command, sub=sub)
        self._apply_scope_receiver_hint(rx_hint)
        self._scope_controls().speed = speed
        return speed

    async def set_scope_speed(self, speed: int) -> None:
        """Set the scope speed preset (0=fast, 1=mid, 2=slow)."""
        self._check_connected()
        receiver = self._scope_controls().receiver
        await self._send_civ_raw(
            self._commands.scope_set_speed(
                speed, to_addr=self._radio_addr, receiver=receiver
            ),
            wait_response=False,
        )
        self._scope_controls().speed = speed

    async def get_scope_during_tx(self) -> bool:
        """Read whether the scope remains visible during transmit."""
        self._check_connected()
        resp = await self._send_civ_expect(
            self._commands.get_scope_during_tx(to_addr=self._radio_addr),
            label="get_scope_during_tx",
        )
        command, sub, _ = self._expect_shape(get_scope_during_tx)
        during_tx = parse_scope_during_tx_response(resp, command=command, sub=sub)
        self._scope_controls().during_tx = during_tx
        return during_tx

    async def set_scope_during_tx(self, on: bool) -> None:
        """Enable or disable scope during transmit."""
        self._check_connected()
        await self._send_civ_raw(
            self._commands.scope_set_during_tx(on, to_addr=self._radio_addr),
            wait_response=False,
        )
        self._scope_controls().during_tx = on

    async def get_scope_center_type(self) -> int:
        """Read the scope center-type setting (0..2)."""
        self._check_connected()
        resp = await self._send_civ_expect(
            self._commands.get_scope_center_type(to_addr=self._radio_addr),
            label="get_scope_center_type",
        )
        command, sub, _ = self._expect_shape(get_scope_center_type)
        receiver, center_type = parse_scope_center_type_response(
            resp, command=command, sub=sub
        )
        self._apply_scope_receiver_hint(receiver)
        self._scope_controls().center_type = center_type
        return center_type

    async def set_scope_center_type(self, center_type: int) -> None:
        """Set the scope center-type setting (0..2)."""
        self._check_connected()
        await self._send_civ_raw(
            self._commands.scope_set_center_type(center_type, to_addr=self._radio_addr),
            wait_response=False,
        )
        self._scope_controls().center_type = center_type

    async def get_scope_vbw(self) -> bool:
        """Read whether narrow scope VBW is enabled."""
        self._check_connected()
        receiver = self._scope_controls().receiver
        resp = await self._send_civ_expect(
            self._commands.get_scope_vbw(to_addr=self._radio_addr, receiver=receiver),
            label="get_scope_vbw",
        )
        command, sub, _ = self._expect_shape(get_scope_vbw)
        rx_hint, narrow = parse_scope_vbw_response(resp, command=command, sub=sub)
        self._apply_scope_receiver_hint(rx_hint)
        self._scope_controls().vbw_narrow = narrow
        return narrow

    async def set_scope_vbw(self, narrow: bool) -> None:
        """Enable or disable narrow scope VBW."""
        self._check_connected()
        # get_scope_vbw reads the selected receiver (sends a receiver prefix),
        # so the setter must address the same receiver or the write never
        # reflects back (MOR-664).
        receiver = self._scope_controls().receiver
        await self._send_civ_raw(
            self._commands.scope_set_vbw(
                narrow, to_addr=self._radio_addr, receiver=receiver
            ),
            wait_response=False,
        )
        self._scope_controls().vbw_narrow = narrow

    async def get_scope_fixed_edge(
        self, *, range_index: int | None = None, edge: int | None = None
    ) -> ScopeFixedEdge:
        """Read the fixed-edge scope bounds for one <range><edge> slot.

        The IC-7610 selector on this query addresses ONE specific slot, not
        "whatever is currently displayed" (MOR-662) — a bare re-read after a
        write to a different slot silently fetches unrelated data and
        clobbers the mirror the write just populated (MOR-1530). Defaults to
        the slot most recently resolved by ``set_scope_fixed_edge`` (or a
        prior ``get_scope_fixed_edge``), so an argument-less reconfirm call
        re-reads the SAME slot. Falls back to the original range 1 / edge 1
        default only when no slot is known yet (``range_index == 0``, the
        ``ScopeFixedEdge`` dataclass default).
        """
        self._check_connected()
        current = self._scope_controls().fixed_edge
        resolved_range = (
            range_index if range_index is not None else (current.range_index or 1)
        )
        resolved_edge = edge if edge is not None else (current.edge or 1)
        resp = await self._send_civ_expect(
            self._commands.get_scope_fixed_edge(
                to_addr=self._radio_addr,
                range_index=resolved_range,
                edge=resolved_edge,
            ),
            label="get_scope_fixed_edge",
        )
        command, sub, _ = self._expect_shape(get_scope_fixed_edge)
        fixed_edge = parse_scope_fixed_edge_response(resp, command=command, sub=sub)
        self._scope_controls().fixed_edge = fixed_edge
        return fixed_edge

    async def set_scope_fixed_edge(
        self,
        *,
        edge: int,
        start_hz: int,
        end_hz: int,
        range_index: int | None = None,
    ) -> None:
        """Set a fixed-edge slot using the profile's documented limits."""
        if range_index is None:
            limits = next(
                (
                    item
                    for item in self._profile.scope_fixed_edge_ranges
                    if item.start_hz <= start_hz < end_hz <= item.end_hz
                ),
                None,
            )
            if limits is None:
                raise ValueError("scope fixed edges are outside profile limits")
            range_index = limits.range_index
        step = self._profile.scope_fixed_edge_step_hz
        if step is not None and (start_hz % step or end_hz % step):
            raise ValueError("scope fixed edges must follow the profile frequency step")
        await self.scope_session().configure(
            fixed_edge=ScopeFixedEdge(range_index, edge, start_hz, end_hz)
        )

    async def get_scope_rbw(self) -> int:
        """Read the scope RBW preset (0=wide, 1=mid, 2=narrow)."""
        self._check_connected()
        receiver = self._scope_controls().receiver
        resp = await self._send_civ_expect(
            self._commands.get_scope_rbw(to_addr=self._radio_addr, receiver=receiver),
            label="get_scope_rbw",
        )
        command, sub, _ = self._expect_shape(get_scope_rbw)
        rx_hint, rbw = parse_scope_rbw_response(resp, command=command, sub=sub)
        self._apply_scope_receiver_hint(rx_hint)
        self._scope_controls().rbw = rbw
        return rbw

    async def set_scope_rbw(self, rbw: int) -> None:
        """Set the scope RBW preset (0=wide, 1=mid, 2=narrow)."""
        self._check_connected()
        # get_scope_rbw reads the selected receiver (sends a receiver prefix),
        # so the setter must address the same receiver or the write never
        # reflects back (MOR-664).
        receiver = self._scope_controls().receiver
        await self._send_civ_raw(
            self._commands.scope_set_rbw(
                rbw, to_addr=self._radio_addr, receiver=receiver
            ),
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
