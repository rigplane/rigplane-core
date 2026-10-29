"""DualRxRuntimeMixin — dual-receiver routing methods extracted from CoreRadio.

Part of the radio.py decomposition (#505). All methods are accessed via
``IcomRadio`` which inherits this mixin.
"""

from __future__ import annotations

import asyncio
import logging
from contextlib import contextmanager
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from typing import Any, Awaitable, Callable, Iterator

    from rigplane.commands.command_map import CommandMap
    from rigplane.commands.commander import InterferenceScope

    from .radio import CoreRadio as _MixinBase  # type: ignore[attr-defined]
else:
    _MixinBase = object

from rigplane.commands import (
    CONTROLLER_ADDR,
    RECEIVER_MAIN,
    build_civ_frame,
    parse_frequency_response,
    parse_ack_nak,
    parse_mode_response,
)
from rigplane.commands._frame import decode_wire_tuple
from rigplane.core.radio_protocol import RelativeVfoState
from rigplane.commands import (
    parse_selected_freq_response as _parse_selected_freq_response,
)
from rigplane.commands import (
    parse_selected_mode_response as _parse_selected_mode_response,
)
from rigplane.core.exceptions import (
    CommandError,
    CommandRejectedError,
    ConnectionError,
    StaleBeforeWireError,
    TimeoutError,
)
from rigplane.core.types import Mode, bcd_encode
from rigplane.profiles import RadioProfile
from rigplane.runtime.callable_support import split_tx_routes_unselected_selector

# CI-V command byte for VFO select / equal / swap (0x07).
_CMD_VFO = 0x07

logger = logging.getLogger(__name__)


def _relative_vfo_routing_available(
    profile: RadioProfile, cmd_map: CommandMap | None, required: tuple[str, ...]
) -> bool:
    """Whether relative-role 0x25/0x26 I/O is routable for this profile.

    Two profile-declared schemes route the selected/unselected
    selector commands, and neither mutates the operator's RX
    selection (MOR-3754):

    * a single-RX ``ab`` profile with ``selected_unselected``
      readback — selector 01 names the unselected A/B slot;
    * a dual-RX profile with an explicit ``split_tx_target``
      (``sub_receiver``: selector 01 is the SUB band;
      ``main_unselected``: selector 01 is the unselected VFO inside
      MAIN) — a fixed physical address for the split-TX role.

    Anything else — including a dual-RX profile that declares no
    ``split_tx_target`` — fails closed. Every name in ``required``
    must be declared in the command map: a GET declaration never
    licenses the matching SET.
    """
    if cmd_map is None or not all(cmd_map.has(key) for key in required):
        return False
    if split_tx_routes_unselected_selector(profile):
        return True
    return (
        profile.receiver_count == 1
        and profile.vfo_scheme == "ab"
        and profile.vfo_readback == "selected_unselected"
    )


class _SelectionWatch:
    """Notes whether another task started a VFO selection change (MOR-3623).

    ``owners`` holds the routed operation's own task and the cleanup tasks
    it runs its restoration in: their selection changes are its own.
    """

    __slots__ = ("owners", "moved")

    def __init__(self, owner: "asyncio.Task[Any] | None") -> None:
        self.owners: set[asyncio.Task[Any] | None] = {owner}
        self.moved = False


class _JoinedCleanup:
    """Run cleanup steps to completion even when the caller is cancelled.

    Each step runs in its own task and the caller waits for it: a
    cancellation delivered while waiting is held back (``cancelled``) and
    the wait resumes until the step finished, so the step's result or
    error reaches the caller's own handling (the restoration's timeout
    retry). :meth:`raise_if_cancelled` then propagates the held
    cancellation (MOR-3623).
    """

    def __init__(self, watch: _SelectionWatch) -> None:
        self.cancelled = False
        self._watch = watch

    async def run(self, step: "Awaitable[None]") -> None:
        task = asyncio.ensure_future(step)
        # The step restores this operation's own selection: not a foreign
        # change for the operation's watch.
        self._watch.owners.add(task)
        while not task.done():
            try:
                # ``wait`` never cancels the step it waits for.
                await asyncio.wait((task,))
            except asyncio.CancelledError:
                self.cancelled = True
        if task.cancelled():
            raise asyncio.CancelledError
        error = task.exception()
        if error is not None:
            # Raised here, in the caller's frame, so the error chains to
            # whatever the caller is handling (an action failure, a first
            # restoration timeout) like an inline await would.
            raise error

    def raise_if_cancelled(self) -> None:
        if self.cancelled:
            raise asyncio.CancelledError


def require_receiver_for_profile(
    profile: RadioProfile, receiver: int, *, operation: str
) -> None:
    """Refuse ``receiver`` when ``profile`` does not serve it (MOR-2484)."""
    if profile.supports_receiver(receiver):
        return
    raise CommandError(
        f"{operation} does not support receiver={receiver} for profile "
        f"{profile.model} (receivers={profile.receiver_count})"
    )


class DualRxRuntimeMixin(_MixinBase):  # type: ignore[misc]
    """Dual-receiver routing methods for CoreRadio (mixin)."""

    @contextmanager
    def _vfo_selection_change(self) -> Iterator[None]:
        # One integer encodes (2 * revision + 1) << in_flight. Odd means idle;
        # every entry adds a factor of two, and every exit removes one while
        # advancing the revision. Overlapping/nested changes stay even until
        # ALL have exited, including on cancellation or an uncertain failure.
        # The owning task of every in-flight change is counted alongside, and
        # every watch held by another task is marked, so a caller nested
        # inside its own change still sees a competitor's (MOR-3623: the
        # receiver-routed operation below).
        owner = asyncio.current_task()
        owners = self._vfo_selection_owners
        owners[owner] = owners.get(owner, 0) + 1
        for watch in self._vfo_selection_watches:
            if owner not in watch.owners:
                watch.moved = True
        self._vfo_selection_epoch <<= 1
        try:
            yield
        finally:
            epoch = self._vfo_selection_epoch
            self._vfo_selection_epoch = (epoch >> 1) + (epoch & -epoch)
            remaining = owners[owner] - 1
            if remaining:
                owners[owner] = remaining
            else:
                del owners[owner]

    @contextmanager
    def _watch_vfo_selection(self) -> "Iterator[_SelectionWatch]":
        """Watch for selection changes started by other tasks (MOR-3623)."""
        watch = _SelectionWatch(asyncio.current_task())
        self._vfo_selection_watches.add(watch)
        try:
            yield watch
        finally:
            self._vfo_selection_watches.discard(watch)

    def _foreign_vfo_selection_in_flight(self) -> bool:
        """True while another task holds a VFO selection change open."""
        current = asyncio.current_task()
        return any(owner is not current for owner in self._vfo_selection_owners)

    def _vfo_selection_epoch_moved(self, selection_epoch: int | None) -> bool:
        """True when the pinned selection epoch changed or a change is in flight.

        Shared by the pre-attempt, pre-SET and post-readback checks of
        ``write_relative_vfo_mode`` (MOR-3743): ``selection_epoch`` is
        the topology pin captured before the first attempt; an even
        epoch means a selection operation is still in flight.
        """
        return selection_epoch is not None and (
            self._vfo_selection_epoch != selection_epoch
            or self._vfo_selection_epoch % 2 == 0
        )

    def _require_receiver(self, receiver: int, *, operation: str) -> None:
        """Validate receiver index against active profile."""
        require_receiver_for_profile(self._profile, receiver, operation=operation)

    def _require_capability(self, capability: str, *, operation: str) -> None:
        """Ensure a profile capability exists before executing operation."""
        if self._profile.supports_capability(capability):
            return
        raise CommandError(
            f"{operation} is not supported by profile {self._profile.model} "
            f"(missing capability: {capability})"
        )

    def _require_cmd29_route(
        self,
        command: int,
        sub: int | None,
        *,
        receiver: int,
        operation: str,
    ) -> None:
        """Require Command29 support for per-receiver command routing."""
        if receiver == RECEIVER_MAIN:
            return
        if self._profile.supports_cmd29(command, sub):
            return
        raise CommandError(
            f"{operation} receiver={receiver} is unsupported for profile "
            f"{self._profile.model}: command 0x{command:02X}"
            + (f"/0x{sub:02X}" if sub is not None else "")
            + " has no cmd29 route"
        )

    def _active_receiver_name(self) -> str:
        """Best-effort active receiver name for VFO-routing fallbacks."""
        active = getattr(self._radio_state, "active", None)
        if active in {"MAIN", "SUB"}:
            return str(active)
        if self._last_vfo in {"SUB", "B"}:
            return "SUB"
        return "MAIN"

    def _mark_active_receiver(self, name: str) -> None:
        """Record ``name`` as the active receiver the radio itself confirmed."""
        self._radio_state.active = name
        self._active_receiver_civ_epoch = self._civ_epoch

    async def _established_active_receiver(self, *, operation: str) -> str:
        """The active receiver, read from the radio unless already established.

        ``RadioState.active`` defaults to ``"MAIN"`` and the initial ``07 D2``
        query is fire-and-forget, so until the radio answered in this CI-V
        generation (a ``07 D2`` readback or an acknowledged MAIN/SUB
        selection) the cached name is a default, not a reading. It is then
        read through the profile-declared ``get_main_sub_band``; a profile
        that declares none is refused rather than trusted (MOR-3623).
        """
        if self._active_receiver_civ_epoch == self._civ_epoch:
            return self._active_receiver_name()
        cmd_map = self._profile.command_map
        if cmd_map is None or not cmd_map.has("get_main_sub_band"):
            raise CommandError(
                f"{operation}: the selected receiver of {self._profile.model} is "
                "unknown and the profile declares no get_main_sub_band readback"
            )
        civ = self._commands.get_main_sub_band(to_addr=self._radio_addr)
        resp = await self._send_civ_expect(civ, label="get_main_sub_band")
        if len(resp.data) != 2 or resp.data[0] != 0xD2 or resp.data[1] not in (0, 1):
            raise CommandError(
                f"invalid main/sub band readback: {resp.data.hex() or 'empty'}"
            )
        name = "SUB" if resp.data[1] else "MAIN"
        self._mark_active_receiver(name)
        return name

    async def _run_with_receiver_vfo_fallback(
        self,
        *,
        receiver: int,
        operation: str,
        action: "Callable[[Callable[[], bool]], Awaitable[Any]]",
    ) -> Any:
        """Run an operation for a receiver using temporary MAIN/SUB VFO switching.

        ``action`` receives the routed operation's currency predicate and
        must pass it as ``is_current`` to the send that carries its frame:
        the wire refuses the frame when the selection it was routed to is no
        longer ours. See :meth:`_run_receiver_routed_once` (MOR-3623).
        """
        return await self._run_receiver_routed_once(
            receiver, operation=operation, action=action
        )

    async def _run_receiver_routed_once(
        self,
        receiver: int,
        *,
        operation: str,
        action: "Callable[[Callable[[], bool]], Awaitable[Any]]",
    ) -> Any:
        """One switch → action → restore critical section (MOR-3623).

        * The whole operation is one VFO selection change, so a competing
          routed operation or selection-sensitive write sees it in flight.
          It is refused (``CommandRejectedError``) while another task's
          selection change is in flight, and when another task starts one
          before its action: neither can route its frame through the
          other's selection.
        * The switch decision and the restoration target rest on a reading:
          an active receiver not established by the radio is read back first
          (:meth:`_established_active_receiver`).
        * The selection is ours while no other task started a selection
          change since this operation began (:meth:`_watch_vfo_selection`)
          and the radio has not reported another active receiver (``07 D2``
          → ``RadioState.active``). The action's frame carries that
          predicate as ``is_current`` to the wire currency hook, which
          refuses a stale frame before it is sent.
        * Restoration runs only while the selection is still ours: a later
          selection by anyone, even of the same receiver, owns the radio
          and is preserved.
        * Restoration (with its one timeout retry, skipped once a later
          selection owns the radio) runs in its own task and is joined: a
          cancellation of the caller waits for it, then propagates.

        The commander's interference scope is deliberately not used here:
        it marks every foreign frame, including commands that cannot move
        the selection, and would refuse this action for them.
        """
        target = "MAIN" if receiver == RECEIVER_MAIN else "SUB"
        if self._foreign_vfo_selection_in_flight():
            raise CommandRejectedError(
                f"{operation}: the receiver selection is changing; retry"
            )
        with self._vfo_selection_change(), self._watch_vfo_selection() as watch:
            cleanup = _JoinedCleanup(watch)
            # Both the switch decision and the restoration target rest on a
            # reading, never on the dataclass default.
            current = await self._established_active_receiver(operation=operation)
            if current != target:
                if target == "SUB" and self._profile.vfo_sub_code is None:
                    raise CommandError(
                        f"{operation} receiver={receiver} is unsupported for profile "
                        f"{self._profile.model}: no SUB VFO select code"
                    )
                if target == "MAIN" and self._profile.vfo_main_code is None:
                    raise CommandError(
                        f"{operation} receiver={receiver} is unsupported for profile "
                        f"{self._profile.model}: no MAIN VFO select code"
                    )
            switched = False

            async def restore_selection() -> None:
                await self._set_vfo_wire(current)
                self._mark_active_receiver(current)

            def selection_is_ours() -> bool:
                return not watch.moved and self._radio_state.active == target

            try:
                if current != target:
                    try:
                        await self._set_vfo_wire(target)
                    except BaseException:
                        await self._restore_after_failed_switch(
                            cleanup,
                            restore_selection,
                            watch,
                            operation=operation,
                            current=current,
                            target=target,
                        )
                        raise
                    switched = True
                    if not watch.moved:
                        self._mark_active_receiver(target)
                if not selection_is_ours():
                    # Another task's selection change started during our
                    # readback or switch: its frame may sit between ours.
                    self._active_receiver_civ_epoch = None
                    raise CommandRejectedError(
                        f"{operation}: the receiver selection changed; retry"
                    )
                try:
                    return await action(selection_is_ours)
                except StaleBeforeWireError as exc:
                    if selection_is_ours():
                        raise
                    raise CommandRejectedError(
                        f"{operation}: the receiver selection changed before the "
                        "command was sent; retry"
                    ) from exc
                except ConnectionError as exc:
                    if selection_is_ours():
                        raise
                    raise CommandError(
                        f"{operation}: the receiver selection changed while the "
                        "command was pending; its outcome is unknown, re-read"
                    ) from exc
            finally:
                try:
                    if switched and selection_is_ours():
                        await self._restore_routed_selection(
                            cleanup,
                            restore_selection,
                            selection_is_ours,
                            operation=operation,
                            current=current,
                        )
                finally:
                    cleanup.raise_if_cancelled()

    async def _restore_after_failed_switch(
        self,
        cleanup: "_JoinedCleanup",
        restore: "Callable[[], Awaitable[None]]",
        watch: "_SelectionWatch",
        *,
        operation: str,
        current: str,
        target: str,
    ) -> None:
        """One best-effort restoration after a switch whose outcome is unknown.

        The switch may have reached the radio while its answer was lost (or
        the caller was cancelled while it was pending). Never raises: the
        switch's own error is what the caller propagates. Skipped once
        another task started a selection change — that selection owns the
        radio.
        """
        if watch.moved or self._foreign_vfo_selection_in_flight():
            self._active_receiver_civ_epoch = None
            return
        try:
            await cleanup.run(restore())
        except Exception:
            self._active_receiver_civ_epoch = None
            logger.warning(
                "%s: restoring receiver %s after a failed %s selection also "
                "failed; the radio's receiver selection is uncertain",
                operation,
                current,
                target,
            )

    async def _restore_routed_selection(
        self,
        cleanup: "_JoinedCleanup",
        restore: "Callable[[], Awaitable[None]]",
        selection_is_ours: "Callable[[], bool]",
        *,
        operation: str,
        current: str,
    ) -> None:
        """Restore the pre-operation receiver, retrying once on a timeout.

        Not swallowed: the radio would silently remain on the temporary
        receiver. The first attempt may fail because a prior
        fire-and-forget ACK sink consumed the timeout budget; a second
        failure propagates and leaves the selection unestablished. The
        retry is skipped once a later selection owns the radio.
        """
        try:
            try:
                await cleanup.run(restore())
            except TimeoutError:
                if not selection_is_ours():
                    self._active_receiver_civ_epoch = None
                    return
                logger.warning(
                    "%s: timeout restoring VFO receiver to %s, retrying once",
                    operation,
                    current,
                )
                await cleanup.run(restore())
        except BaseException:
            self._active_receiver_civ_epoch = None
            raise

    async def _get_frequency_main(
        self, *, bypass_cache: bool = False, update_cache: bool = True
    ) -> int:
        """Read MAIN receiver frequency with optional cache updates."""
        civ = self._commands.get_freq(to_addr=self._radio_addr)
        try:
            resp = await self._send_civ_expect(
                civ,
                key="get_frequency",
                dedupe=not bypass_cache,
                label="get_frequency",
            )
            freq = parse_frequency_response(resp)
            if update_cache:
                self._last_freq_hz = freq
                self._state_cache.update_freq(freq)
            return freq
        except TimeoutError:
            if update_cache and self._state_cache.is_fresh(
                "freq", self._cache_ttl_freq
            ):
                logger.debug(
                    "get_frequency: timeout, returning cached %d Hz",
                    self._state_cache.freq,
                )
                return self._state_cache.freq  # type: ignore[no-any-return]
            raise

    async def _set_frequency_main(
        self,
        freq_hz: int,
        *,
        update_cache: bool = True,
        is_current: "Callable[[], bool] | None" = None,
    ) -> None:
        """Set MAIN receiver frequency with optional cache updates."""
        civ = self._commands.set_freq(
            freq_hz, to_addr=self._radio_addr, receiver=RECEIVER_MAIN
        )
        if is_current is None:
            resp = await self._send_civ_expect(civ, label="set_frequency_main")
        else:
            resp = await self._send_civ_expect(
                civ, label="set_frequency_main", is_current=is_current
            )
        if parse_ack_nak(resp) is False:
            raise CommandRejectedError(f"Radio rejected MAIN set_frequency({freq_hz})")
        if update_cache:
            self._last_freq_hz = freq_hz

    async def _get_mode_info_main(
        self, *, update_cache: bool = True
    ) -> tuple[Mode, int | None]:
        """Read MAIN receiver mode/filter with optional cache updates."""
        civ = self._commands.get_mode(to_addr=self._radio_addr)
        try:
            resp = await self._send_civ_expect(civ, label="get_mode_info_main")
            mode, filt = parse_mode_response(resp)
            if update_cache:
                self._last_mode = mode
                if filt is not None:
                    self._filter_width = filt
                self._state_cache.update_mode(mode.name, filt)
            return mode, filt
        except TimeoutError:
            if update_cache and self._state_cache.is_fresh(
                "mode", self._cache_ttl_mode
            ):
                logger.debug(
                    "get_mode_info: timeout, returning cached %s",
                    self._state_cache.mode,
                )
                return Mode[self._state_cache.mode], self._state_cache.filter_width
            raise

    async def _set_mode_main(
        self,
        mode: Mode,
        *,
        filter_width: int | None = None,
        update_cache: bool = True,
        is_current: "Callable[[], bool] | None" = None,
    ) -> None:
        """Set MAIN receiver mode/filter with optional cache updates."""
        if self._profile.set_mode_via_selected:
            # Rigs declaring ``set_selected_mode`` (e.g. X6200) ignore the bare
            # 0x06 mode-set; route through CI-V 0x26 0x00 with the full
            # (mode, data_mode, filter) tuple so a mode-only change preserves
            # the radio's current data-mode and filter.
            target = self._radio_state.receiver("MAIN")
            data_mode = int(getattr(target, "data_mode", 0) or 0)
            resolved_filter = (
                filter_width
                if filter_width is not None
                else (self._filter_width if self._filter_width is not None else 1)
            )
            civ = self._commands.set_selected_mode(
                mode, data_mode, resolved_filter, to_addr=self._radio_addr
            )
        else:
            civ = self._commands.set_mode(
                mode,
                filter_width=filter_width,
                to_addr=self._radio_addr,
                receiver=RECEIVER_MAIN,
            )
        if is_current is None:
            await self._send_civ_raw(civ, wait_response=False)
        else:
            await self._send_civ_raw(civ, wait_response=False, is_current=is_current)
        self._last_mode = mode
        if update_cache:
            if filter_width is not None:
                self._filter_width = filter_width

    # ------------------------------------------------------------------
    # Selected / Unselected receiver freq & mode (0x25 / 0x26)
    # ------------------------------------------------------------------

    async def _get_selected_freq(self) -> int:
        """Read the selected (active) receiver frequency via CI-V 0x25 0x00."""
        civ = self._commands.get_selected_freq(to_addr=self._radio_addr)
        resp = await self._send_civ_expect(civ, label="get_selected_freq")
        _rcvr, freq = _parse_selected_freq_response(resp)
        return freq

    async def _get_unselected_freq(self) -> int:
        """Read the unselected (inactive) receiver frequency via CI-V 0x25 0x01."""
        civ = self._commands.get_unselected_freq(to_addr=self._radio_addr)
        resp = await self._send_civ_expect(civ, label="get_unselected_freq")
        _rcvr, freq = _parse_selected_freq_response(resp)
        return freq

    async def _get_selected_mode(self) -> tuple[Mode, int | None]:
        """Read the selected (active) receiver mode via CI-V 0x26 0x00."""
        civ = self._commands.get_selected_mode(to_addr=self._radio_addr)
        resp = await self._send_civ_expect(civ, label="get_selected_mode")
        _rcvr, mode, _data_mode, filt = _parse_selected_mode_response(resp)
        return mode, filt

    async def _get_unselected_mode(self) -> tuple[Mode, int | None]:
        """Read the unselected (inactive) receiver mode via CI-V 0x26 0x01."""
        civ = self._commands.get_unselected_mode(to_addr=self._radio_addr)
        resp = await self._send_civ_expect(civ, label="get_unselected_mode")
        _rcvr, mode, _data_mode, filt = _parse_selected_mode_response(resp)
        return mode, filt

    async def read_relative_vfo(self, *, selected: bool) -> RelativeVfoState:
        """Read one Selected/Unselected tuple without changing radio state."""

        role = "selected" if selected else "unselected"
        cmd_map = self._profile.command_map
        if cmd_map is None or not _relative_vfo_routing_available(
            self._profile, cmd_map, (f"get_{role}_freq", f"get_{role}_mode")
        ):
            raise CommandError("direct VFO read is unavailable")

        freq_cmd = (
            self._commands.get_selected_freq
            if selected
            else self._commands.get_unselected_freq
        )
        mode_cmd = (
            self._commands.get_selected_mode
            if selected
            else self._commands.get_unselected_mode
        )
        freq_resp = await self._send_civ_expect(
            freq_cmd(to_addr=self._radio_addr), label=f"get_{role}_freq"
        )
        receiver, freq = _parse_selected_freq_response(freq_resp)
        if receiver != (0 if selected else 1):
            raise CommandError("relative VFO frequency readback selector mismatch")
        mode_resp = await self._send_civ_expect(
            mode_cmd(to_addr=self._radio_addr), label=f"get_{role}_mode"
        )
        receiver, mode, data_mode, filt = _parse_selected_mode_response(mode_resp)
        if receiver != (0 if selected else 1):
            raise CommandError("relative VFO mode readback selector mismatch")
        if data_mode is None:
            raise CommandError("relative VFO mode readback is missing DATA")
        return RelativeVfoState(
            freq_hz=freq,
            mode=mode.name,
            filter_num=filt,
            data_mode=int(data_mode),
        )

    async def write_relative_vfo_frequency(
        self,
        freq_hz: int,
        *,
        selected: bool,
        before_set: Callable[[], None] | None = None,
        after_ack: Callable[[], None] | None = None,
        after_readback: Callable[[], None] | None = None,
    ) -> int:
        """Write a profile-declared relative frequency and return its readback Hz.

        SET currency reuses the synchronous before_set validator at dispatch.
        Other hooks run after a positive ACK and after readback I/O.
        """
        role = "selected" if selected else "unselected"
        cmd_map = self._profile.command_map
        if cmd_map is None or not _relative_vfo_routing_available(
            self._profile, cmd_map, (f"set_{role}_freq", f"get_{role}_freq")
        ):
            raise CommandError("direct VFO frequency is unavailable")

        def is_current() -> bool:
            if before_set is not None:
                before_set()
            return True

        opcode, sub, prefix = decode_wire_tuple(cmd_map.get(f"set_{role}_freq"))
        if before_set is not None:
            before_set()
        try:
            response = await self.send_civ(
                opcode,
                sub,
                data=prefix + bcd_encode(freq_hz),
                wait_response=True,
                is_current=is_current if before_set is not None else None,
            )
        except ConnectionError:
            if before_set is not None:
                before_set()
            raise
        ack = None if response is None else parse_ack_nak(response)
        if ack is False:
            raise CommandRejectedError("Radio rejected direct VFO frequency")
        if ack is not True:
            raise CommandError("direct VFO frequency was not acknowledged")
        if after_ack is not None:
            after_ack()
        opcode, sub, data = decode_wire_tuple(cmd_map.get(f"get_{role}_freq"))
        readback = await self.send_civ(opcode, sub, data=data, wait_response=True)
        if after_readback is not None:
            after_readback()
        if readback is None:
            raise CommandError("direct VFO frequency readback unavailable")
        selector, observed_freq = _parse_selected_freq_response(readback)
        if selector != (0 if selected else 1):
            raise CommandError("direct VFO readback selector mismatch")
        return observed_freq

    async def write_relative_vfo_mode(
        self,
        mode: int,
        *,
        selected: bool,
        data: bool | None = None,
        filter_width: int | None = None,
        before_set: Callable[[], None] | None = None,
    ) -> RelativeVfoState:
        """Preserve omitted fields; carry before_set validation through dispatch.

        The whole preservation read → SET → readback runs inside a
        commander interference scope (MOR-3743): a concurrent command
        dispatched by another task between the preservation read and the
        SET marks the scope, the SET is refused before the wire — a
        ``StaleBeforeWireError``, the only retryable refusal — and the
        operation retries from the preservation read — up to three
        attempts, then a retryable ``CommandError``.  A SET that already
        reached the wire is never retried: a currency failure behind the
        send (interference detected while its ACK was pending)
        propagates as the plain ``ConnectionError``, and interference
        behind an acknowledged SET is decided by the readback, not by a
        second SET.  The VFO selection epoch is pinned before the first
        attempt; a selection change during the write — including one
        that completes during the readback — fails it with a
        ``CommandError`` even though the SET was already applied; the
        caller must re-read.  Nothing is ever delayed or reordered.
        Without a commander the write runs once, exactly as before.
        """
        role = "selected" if selected else "unselected"
        cmd_map = self._profile.command_map
        if cmd_map is None or not _relative_vfo_routing_available(
            self._profile,
            cmd_map,
            (f"set_{role}_mode", f"get_{role}_mode", f"get_{role}_freq"),
        ):
            raise CommandError("direct VFO mode is unavailable")
        try:
            mode_code = int(Mode(mode))
        except (TypeError, ValueError) as exc:
            raise CommandError(f"Unsupported direct VFO mode: {mode!r}") from exc
        if data is not None and not isinstance(data, bool):
            raise CommandError("direct VFO DATA must be a boolean")
        if filter_width is not None and (
            type(filter_width) is not int or filter_width not in (1, 2, 3)
        ):
            raise CommandError("direct VFO filter number must be 1, 2 or 3")

        commander = self._commander
        if commander is None:
            return await self._write_relative_vfo_mode_once(
                mode_code,
                selected=selected,
                data=data,
                filter_width=filter_width,
                before_set=before_set,
                scope=None,
                selection_epoch=None,
            )
        # MOR-3743: pin the VFO topology once for every attempt.  A
        # selection change between attempts would silently redirect the
        # retry at a different physical VFO, so each attempt re-validates
        # this epoch and the write fails instead of retrying across it.
        selection_epoch = self._vfo_selection_epoch
        if selection_epoch % 2 == 0:
            raise CommandRejectedError("VFO selection is changing; retry mode write")
        for _ in range(3):
            async with commander.interference_scope() as scope:
                observed = await self._write_relative_vfo_mode_once(
                    mode_code,
                    selected=selected,
                    data=data,
                    filter_width=filter_width,
                    before_set=before_set,
                    scope=scope,
                    selection_epoch=selection_epoch,
                )
            if observed is not None:
                return observed
            # Only a proven pre-wire refusal (``StaleBeforeWireError``
            # with the scope marked) returns ``None``; every other
            # failure raises out of the attempt untouched.
        raise CommandError(
            "split-mode write interfered with a concurrent command; retry"
        )

    async def _write_relative_vfo_mode_once(
        self,
        mode_code: int,
        *,
        selected: bool,
        data: bool | None,
        filter_width: int | None,
        before_set: Callable[[], None] | None,
        scope: InterferenceScope | None,
        selection_epoch: int | None,
    ) -> RelativeVfoState | None:
        """One preservation read → SET → readback attempt (MOR-3743).

        Returns ``None`` only when the SET was refused before the wire
        (``StaleBeforeWireError``) because ``scope`` reported
        interference; every other failure raises exactly as it would
        without a scope — a currency failure behind the send propagates
        as the plain ``ConnectionError``, never a retry.
        ``selection_epoch`` is the topology pin captured before the
        first attempt (or the per-attempt capture on a commander-less
        single shot): a move, an in-flight selection change, or one that
        lands during the readback fails the attempt instead of
        redirecting its SET at a different physical VFO or reporting
        success for a topology the caller no longer holds.
        """
        role = "selected" if selected else "unselected"
        cmd_map = self._profile.command_map
        if self._vfo_selection_epoch_moved(selection_epoch):
            raise CommandRejectedError(
                "VFO selection changed during preservation; retry"
            )
        if data is None or filter_width is None:
            if selection_epoch is None:
                # Commander-less single shot: pin per attempt.
                selection_epoch = self._vfo_selection_epoch
                if selection_epoch % 2 == 0:
                    raise CommandRejectedError(
                        "VFO selection is changing; retry mode write"
                    )
            try:
                current = await self.read_relative_vfo(selected=selected)
            except ValueError as exc:
                raise CommandError(
                    "Invalid direct VFO mode preservation readback"
                ) from exc
            # Explicit fields do not depend on the preservation read. In
            # particular, clearing DATA must work from IC-7610 DATA2/3.
            if data is None:
                if current.data_mode not in (0, 1):
                    raise CommandError(
                        "direct VFO mode preservation readback is unavailable"
                    )
                data = bool(current.data_mode)
            if filter_width is None:
                if current.filter_num not in (1, 2, 3):
                    raise CommandError(
                        "direct VFO mode preservation readback is unavailable"
                    )
                filter_width = current.filter_num
        if data is None or filter_width is None or filter_width not in (1, 2, 3):
            raise CommandError("direct VFO filter readback is unavailable")
        opcode, sub, prefix = decode_wire_tuple(cmd_map.get(f"set_{role}_mode"))

        def is_current() -> bool:
            if before_set is not None:
                before_set()
            return scope is None or scope.is_current()

        if before_set is not None:
            before_set()
        if self._vfo_selection_epoch_moved(selection_epoch):
            raise CommandRejectedError(
                "VFO selection changed during preservation; retry"
            )
        try:
            response = await self.send_civ(
                opcode,
                sub,
                data=prefix + bytes([mode_code, int(data), filter_width]),
                wait_response=True,
                is_current=is_current
                if (before_set is not None or scope is not None)
                else None,
            )
        except StaleBeforeWireError:
            if before_set is not None:
                before_set()
            if scope is not None and scope.interfered:
                # The wire seam refused the SET before the transport
                # because a concurrent command dispatched since the
                # preservation read: discard the attempt and re-read
                # (MOR-3743).  This proven pre-wire refusal is the ONLY
                # retryable outcome — the SET never reached the wire.
                return None
            raise
        except ConnectionError:
            if before_set is not None:
                before_set()
            # A currency failure raised behind the send — interference
            # detected while the SET's ACK was pending (MOR-3743): the
            # SET may already be applied, so never repeat it; surface
            # the error and let the caller re-read.
            raise
        ack = None if response is None else parse_ack_nak(response)
        if ack is False:
            raise CommandRejectedError("Radio rejected direct VFO mode")
        if ack is not True:
            raise CommandError("direct VFO mode was not acknowledged")
        try:
            observed = await self.read_relative_vfo(selected=selected)
        except ValueError as exc:
            raise CommandError("Invalid direct VFO mode readback") from exc
        # The SET is already applied and this attempt can never retry,
        # but success must not be reported against a topology the caller
        # pinned: a selection change that completed during the write —
        # including during this readback — fails the write even though
        # the SET was applied, and the caller must re-read (MOR-3743).
        if self._vfo_selection_epoch_moved(selection_epoch):
            raise CommandError("VFO selection changed during the write; retry")
        # The SET already reached the wire: never send a second one
        # (MOR-3743).  Interference behind it is not a retry trigger —
        # the readback below is the verdict: consistent means success,
        # contradictory raises the readback error.
        if observed.data_mode not in (0, 1) or observed.filter_num not in (1, 2, 3):
            raise CommandError("direct VFO mode readback is unavailable")
        # ACK alone is not success: a valid but contradictory readback
        # means the radio did not end up in the requested state.
        if (
            Mode[observed.mode] != Mode(mode_code)
            or observed.data_mode != int(data)
            or observed.filter_num != filter_width
        ):
            raise CommandError(
                "direct VFO mode readback contradicts the requested "
                f"{Mode(mode_code).name} data={int(data)} filter={filter_width}"
            )
        return observed

    # ------------------------------------------------------------------
    # Explicit swap/equalize — MAIN/SUB vs A/B (issue #714)
    # ------------------------------------------------------------------

    async def swap_main_sub(self) -> None:
        """Swap MAIN and SUB VFO frequencies. Requires a dual-RX profile."""
        self._check_connected()
        if self._profile.receiver_count < 2:
            raise CommandError(
                f"swap_main_sub not supported by profile {self._profile.model}: "
                "not dual-RX"
            )
        code = self._profile.swap_main_sub_code
        if code is None:
            raise CommandError(
                f"swap_main_sub not supported by profile {self._profile.model}: "
                "no swap_main_sub_code"
            )
        civ = build_civ_frame(
            self._radio_addr, CONTROLLER_ADDR, _CMD_VFO, data=bytes([code])
        )
        with self._vfo_selection_change():
            await self._send_civ_raw(civ, wait_response=False)

    async def equalize_main_sub(self) -> None:
        """Copy MAIN VFO state to SUB. Requires a dual-RX profile."""
        self._check_connected()
        if self._profile.receiver_count < 2:
            raise CommandError(
                f"equalize_main_sub not supported by profile "
                f"{self._profile.model}: not dual-RX"
            )
        code = self._profile.equal_main_sub_code
        if code is None:
            raise CommandError(
                f"equalize_main_sub not supported by profile "
                f"{self._profile.model}: no equal_main_sub_code"
            )
        civ = build_civ_frame(
            self._radio_addr, CONTROLLER_ADDR, _CMD_VFO, data=bytes([code])
        )
        with self._vfo_selection_change():
            await self._send_civ_raw(civ, wait_response=False)

    async def swap_vfo_ab(self, receiver: int = 0) -> None:
        """Swap VFO A and VFO B within ``receiver``.

        On dual-RX profiles the target receiver (MAIN/SUB) is selected
        first so the swap opcode affects the intended receiver.  On
        single-RX profiles the swap is issued directly.

        Raises ``CommandError`` when the profile does not declare a
        dedicated ``swap_ab_code``.  We do NOT silently fall back to
        ``swap_main_sub_code`` because on IC-7610 / IC-9700 that opcode
        exchanges MAIN↔SUB — a different semantic than A↔B within a
        single receiver.  Callers wanting MAIN↔SUB must use
        ``swap_main_sub()`` explicitly.
        """
        self._check_connected()
        self._require_receiver(receiver, operation="swap_vfo_ab")
        code = self._profile.swap_ab_code
        if code is None:
            raise CommandError(
                f"swap_vfo_ab not supported by {self._profile.model}: "
                "profile declares no swap_ab_code. "
                "For MAIN↔SUB exchange use swap_main_sub()."
            )
        with self._vfo_selection_change():
            if self._profile.receiver_count > 1:
                target = "MAIN" if receiver == RECEIVER_MAIN else "SUB"
                await self._set_vfo_wire(target)
            civ = build_civ_frame(
                self._radio_addr, CONTROLLER_ADDR, _CMD_VFO, data=bytes([code])
            )
            await self._send_civ_raw(civ, wait_response=False)

    async def equalize_vfo_ab(self, receiver: int = 0) -> None:
        """Copy the active VFO's state to the inactive VFO on ``receiver``.

        Raises ``CommandError`` when the profile does not declare a
        dedicated ``equal_ab_code``.  We do NOT silently fall back to
        ``equal_main_sub_code``: on dual-RX rigs that opcode copies
        MAIN→SUB, not A→B within a receiver.
        """
        self._check_connected()
        self._require_receiver(receiver, operation="equalize_vfo_ab")
        code = self._profile.equal_ab_code
        if code is None:
            raise CommandError(
                f"equalize_vfo_ab not supported by {self._profile.model}: "
                "profile declares no equal_ab_code. "
                "For MAIN→SUB copy use equalize_main_sub()."
            )
        with self._vfo_selection_change():
            if self._profile.receiver_count > 1:
                target = "MAIN" if receiver == RECEIVER_MAIN else "SUB"
                await self._set_vfo_wire(target)
            civ = build_civ_frame(
                self._radio_addr, CONTROLLER_ADDR, _CMD_VFO, data=bytes([code])
            )
            await self._send_civ_raw(civ, wait_response=False)

    # ------------------------------------------------------------------
    # ReceiverBankCapable — Transceiver → Receiver tier (issue #1170)
    # ------------------------------------------------------------------

    @property
    def receiver_count(self) -> int:
        """Number of independent receivers exposed by this transceiver.

        Profile-driven via ``[radio] receiver_count`` in the rig TOML.
        IC-7610 / IC-9700 report ``2`` (MAIN + SUB); IC-7300 / IC-705
        report ``1``.
        """
        return int(self._profile.receiver_count)

    @staticmethod
    def _normalize_receiver_index(which: int | str) -> int:
        """Normalize a ``select_receiver`` argument to a 0-based index.

        Accepts integer indices (``0`` / ``1``) or case-insensitive names
        (``"main"`` / ``"sub"``).  Raises :class:`ValueError` on any other
        value.
        """
        if isinstance(which, bool):
            # ``bool`` is a subclass of ``int`` but is never a valid receiver
            # index — reject explicitly to avoid silent ``True``→1 conversion.
            raise ValueError(
                f"select_receiver: which must be int or str, got {type(which).__name__}"
            )
        if isinstance(which, str):
            key = which.strip().lower()
            if key == "main":
                return 0
            if key == "sub":
                return 1
            raise ValueError(
                f"select_receiver: unknown receiver name {which!r} "
                "(expected 'main' or 'sub')"
            )
        if isinstance(which, int):
            return int(which)
        raise ValueError(
            f"select_receiver: which must be int or str, got {type(which).__name__}"
        )

    async def select_receiver(self, which: int | str) -> None:
        """Make ``which`` the active receiver for subsequent commands.

        On dual-RX Icom rigs (IC-7610 / IC-9700) issues the profile's
        ``main_select`` / ``sub_select`` opcode (``0x07 0xD0`` /
        ``0x07 0xD1``) and updates :attr:`RadioState.active`.  On single-RX
        profiles only ``which == 0`` is accepted and the call is a no-op
        (matching the :class:`~rigplane.radio_protocol.ReceiverBankCapable`
        contract).  Out-of-range indices and unknown names raise
        :class:`ValueError`.
        """
        self._check_connected()
        index = self._normalize_receiver_index(which)
        count = self.receiver_count
        if index < 0 or index >= count:
            raise ValueError(
                f"select_receiver: receiver index {index} out of range "
                f"for receiver_count={count}"
            )
        if count <= 1:
            # Single-RX: nothing to switch.
            return
        target = "MAIN" if index == 0 else "SUB"
        await self._set_vfo_wire(target)
        self._mark_active_receiver(target)

    async def get_active_receiver(self) -> int:
        """Return the index of the currently active receiver.

        Returns ``0`` for MAIN, ``1`` for SUB.  Reads the cached
        :attr:`RadioState.active` value (poller-populated on dual-RX rigs).
        Single-RX profiles always return ``0``.
        """
        if self.receiver_count <= 1:
            return 0
        active = getattr(self._radio_state, "active", "MAIN")
        return 1 if active == "SUB" else 0

    # ------------------------------------------------------------------
    # VfoSlotCapable — per-receiver A/B slot ops (issue #1170)
    # ------------------------------------------------------------------

    @staticmethod
    def _normalize_vfo_slot(slot: str) -> str:
        """Validate and upper-case a VFO slot argument (``"A"`` or ``"B"``)."""
        if not isinstance(slot, str):
            raise ValueError(f"slot must be str, got {type(slot).__name__}")
        norm = slot.strip().upper()
        if norm not in {"A", "B"}:
            raise ValueError(f"slot must be 'A' or 'B', got {slot!r}")
        return norm

    def _check_vfo_slot_receiver(self, receiver: int, *, operation: str) -> None:
        """Raise ``ValueError`` when ``receiver`` is out of range for the profile."""
        count = self.receiver_count
        if receiver < 0 or receiver >= count:
            raise ValueError(
                f"{operation}: receiver index {receiver} out of range "
                f"for receiver_count={count}"
            )

    async def get_vfo_slot(self, receiver: int = 0) -> str:
        """Return the active VFO slot (``"A"`` or ``"B"``) for ``receiver``.

        Reads cached :attr:`ReceiverState.active_slot` (poller-populated).
        Single-RX rigs only accept ``receiver == 0``; out-of-range indices
        raise :class:`ValueError`.
        """
        self._check_vfo_slot_receiver(receiver, operation="get_vfo_slot")
        if self.receiver_count > 1:
            rx_state = (
                self._radio_state.sub if receiver == 1 else self._radio_state.main
            )
        else:
            rx_state = self._radio_state.main
        slot = getattr(rx_state, "active_slot", "A")
        return "B" if str(slot).upper() == "B" else "A"

    async def set_vfo_slot(self, slot: str, receiver: int = 0) -> None:
        """Make ``slot`` (``"A"`` or ``"B"``) the active VFO on ``receiver``.

        Wire bytes: CI-V ``0x07 0x00`` (A) or ``0x07 0x01`` (B).  On
        dual-RX rigs (IC-7610 / IC-9700) the target receiver is selected
        first via the VFO-switch pattern (``0x07 0xD0`` / ``0xD1``) so the
        slot-select opcode affects the intended receiver, then the
        previous receiver is restored.  Single-RX rigs send the opcode
        directly.

        Raises :class:`ValueError` for an invalid slot or out-of-range
        receiver index.
        """
        await self._set_vfo_slot_impl(slot, receiver=receiver, confirmed=False)

    async def _set_vfo_slot_confirmed(self, slot: str, receiver: int = 0) -> None:
        """Private ACK-confirmed path; public ``set_vfo_slot`` stays fire-and-forget."""

        await self._set_vfo_slot_impl(slot, receiver=receiver, confirmed=True)

    async def _set_vfo_slot_impl(
        self, slot: str, *, receiver: int, confirmed: bool
    ) -> None:
        operation = "set_vfo_slot_confirmed" if confirmed else "set_vfo_slot"

        self._check_connected()
        self._check_vfo_slot_receiver(receiver, operation=operation)
        norm_slot = self._normalize_vfo_slot(slot)
        slot_code = 0x00 if norm_slot == "A" else 0x01

        async def emit(is_current: "Callable[[], bool] | None" = None) -> None:
            civ = build_civ_frame(
                self._radio_addr,
                CONTROLLER_ADDR,
                _CMD_VFO,
                data=bytes([slot_code]),
            )
            if confirmed:
                if is_current is None:
                    response = await self._send_civ_expect(civ, label=operation)
                else:
                    response = await self._send_civ_expect(
                        civ, label=operation, is_current=is_current
                    )
                if parse_ack_nak(response) is not True:
                    raise CommandError(f"Radio rejected VFO slot {norm_slot}")
            elif is_current is None:
                await self._send_civ_raw(civ, wait_response=False)
            else:
                await self._send_civ_raw(
                    civ, wait_response=False, is_current=is_current
                )
            rx_state = (
                self._radio_state.sub
                if (receiver == 1 and self.receiver_count > 1)
                else self._radio_state.main
            )
            rx_state.active_slot = norm_slot

        with self._vfo_selection_change():
            if self.receiver_count > 1:
                await self._run_with_receiver_vfo_fallback(
                    receiver=receiver,
                    operation=operation,
                    action=emit,
                )
            else:
                await emit()
