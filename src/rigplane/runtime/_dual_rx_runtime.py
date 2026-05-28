"""DualRxRuntimeMixin — dual-receiver routing methods extracted from CoreRadio.

Part of the radio.py decomposition (#505). All methods are accessed via
``IcomRadio`` which inherits this mixin.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from typing import Any, Awaitable, Callable

    from .radio import CoreRadio as _MixinBase  # type: ignore[attr-defined]
else:
    _MixinBase = object

from rigplane.commands import (
    CONTROLLER_ADDR,
    RECEIVER_MAIN,
    build_civ_frame,
    get_freq,
    get_mode,
    parse_frequency_response,
    parse_mode_response,
    set_freq,
    set_mode,
)
from rigplane.commands import get_selected_freq as _get_selected_freq_cmd
from rigplane.commands import get_selected_mode as _get_selected_mode_cmd
from rigplane.commands import set_selected_mode as _set_selected_mode_cmd
from rigplane.commands import get_unselected_freq as _get_unselected_freq_cmd
from rigplane.commands import get_unselected_mode as _get_unselected_mode_cmd
from rigplane.commands import (
    parse_selected_freq_response as _parse_selected_freq_response,
)
from rigplane.commands import (
    parse_selected_mode_response as _parse_selected_mode_response,
)
from rigplane.core.exceptions import CommandError, TimeoutError
from rigplane.core.types import Mode

# CI-V command byte for VFO select / equal / swap (0x07).
_CMD_VFO = 0x07

logger = logging.getLogger(__name__)


class DualRxRuntimeMixin(_MixinBase):  # type: ignore[misc]
    """Dual-receiver routing methods for CoreRadio (mixin)."""

    def _require_receiver(self, receiver: int, *, operation: str) -> None:
        """Validate receiver index against active profile."""
        if self._profile.supports_receiver(receiver):
            return
        raise CommandError(
            f"{operation} does not support receiver={receiver} for profile "
            f"{self._profile.model} (receivers={self._profile.receiver_count})"
        )

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

    async def _run_with_receiver_vfo_fallback(
        self,
        *,
        receiver: int,
        operation: str,
        action: Callable[[], Awaitable[Any]],
    ) -> Any:
        """Run an operation for a receiver using temporary MAIN/SUB VFO switching."""
        target = "MAIN" if receiver == RECEIVER_MAIN else "SUB"
        current = self._active_receiver_name()
        switched = False

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
            await self._set_vfo_wire(target)
            self._radio_state.active = target
            switched = True

        try:
            return await action()
        finally:
            if switched:
                try:
                    await self._set_vfo_wire(current)
                    self._radio_state.active = current
                except TimeoutError:
                    # Do not swallow — radio would silently remain on the
                    # temporary receiver. Retry once (the first attempt may
                    # fail because a prior fire-and-forget ACK sink consumed
                    # the timeout budget), then propagate on a second failure.
                    logger.warning(
                        "%s: timeout restoring VFO receiver to %s, retrying once",
                        operation,
                        current,
                    )
                    await self._set_vfo_wire(current)
                    self._radio_state.active = current

    async def _get_frequency_main(
        self, *, bypass_cache: bool = False, update_cache: bool = True
    ) -> int:
        """Read MAIN receiver frequency with optional cache updates."""
        civ = get_freq(to_addr=self._radio_addr)
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
        self, freq_hz: int, *, update_cache: bool = True
    ) -> None:
        """Set MAIN receiver frequency with optional cache updates."""
        civ = set_freq(freq_hz, to_addr=self._radio_addr, receiver=RECEIVER_MAIN)
        await self._send_civ_raw(civ, wait_response=False)
        if update_cache:
            self._last_freq_hz = freq_hz
            self._state_cache.update_freq(freq_hz)

    async def _get_mode_info_main(
        self, *, update_cache: bool = True
    ) -> tuple[Mode, int | None]:
        """Read MAIN receiver mode/filter with optional cache updates."""
        civ = get_mode(to_addr=self._radio_addr)
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
            civ = _set_selected_mode_cmd(
                mode, data_mode, resolved_filter, to_addr=self._radio_addr
            )
        else:
            civ = set_mode(
                mode,
                filter_width=filter_width,
                to_addr=self._radio_addr,
                receiver=RECEIVER_MAIN,
            )
        await self._send_civ_raw(civ, wait_response=False)
        self._last_mode = mode
        if update_cache:
            if filter_width is not None:
                self._filter_width = filter_width
            cached_filter = (
                filter_width if filter_width is not None else self._filter_width
            )
            self._state_cache.update_mode(mode.name, cached_filter)

    # ------------------------------------------------------------------
    # Selected / Unselected receiver freq & mode (0x25 / 0x26)
    # ------------------------------------------------------------------

    async def _get_selected_freq(self) -> int:
        """Read the selected (active) receiver frequency via CI-V 0x25 0x00."""
        civ = _get_selected_freq_cmd(to_addr=self._radio_addr)
        resp = await self._send_civ_expect(civ, label="get_selected_freq")
        _rcvr, freq = _parse_selected_freq_response(resp)
        return freq

    async def _get_unselected_freq(self) -> int:
        """Read the unselected (inactive) receiver frequency via CI-V 0x25 0x01."""
        civ = _get_unselected_freq_cmd(to_addr=self._radio_addr)
        resp = await self._send_civ_expect(civ, label="get_unselected_freq")
        _rcvr, freq = _parse_selected_freq_response(resp)
        return freq

    async def _get_selected_mode(self) -> tuple[Mode, int | None]:
        """Read the selected (active) receiver mode via CI-V 0x26 0x00."""
        civ = _get_selected_mode_cmd(to_addr=self._radio_addr)
        resp = await self._send_civ_expect(civ, label="get_selected_mode")
        _rcvr, mode, _data_mode, filt = _parse_selected_mode_response(resp)
        return mode, filt

    async def _get_unselected_mode(self) -> tuple[Mode, int | None]:
        """Read the unselected (inactive) receiver mode via CI-V 0x26 0x01."""
        civ = _get_unselected_mode_cmd(to_addr=self._radio_addr)
        resp = await self._send_civ_expect(civ, label="get_unselected_mode")
        _rcvr, mode, _data_mode, filt = _parse_selected_mode_response(resp)
        return mode, filt

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
        self._radio_state.active = target

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
        self._check_connected()
        self._check_vfo_slot_receiver(receiver, operation="set_vfo_slot")
        norm_slot = self._normalize_vfo_slot(slot)
        slot_code = 0x00 if norm_slot == "A" else 0x01

        async def _emit_slot() -> None:
            civ = build_civ_frame(
                self._radio_addr,
                CONTROLLER_ADDR,
                _CMD_VFO,
                data=bytes([slot_code]),
            )
            await self._send_civ_raw(civ, wait_response=False)
            # Update cached per-receiver active_slot for subsequent get_vfo_slot.
            rx_state = (
                self._radio_state.sub
                if (receiver == 1 and self.receiver_count > 1)
                else self._radio_state.main
            )
            rx_state.active_slot = norm_slot

        if self.receiver_count > 1:
            await self._run_with_receiver_vfo_fallback(
                receiver=receiver,
                operation="set_vfo_slot",
                action=_emit_slot,
            )
        else:
            await _emit_slot()
