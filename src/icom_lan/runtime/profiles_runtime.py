"""Generic capability-aware radio profile system.

A profile is a declarative desired state — set only the fields you want
to change. ``apply_profile`` inspects the radio for each setter and silently
skips fields the radio does not support.

Example::

    from icom_lan import OperatingProfile, apply_profile, PRESETS

    # Custom profile
    profile = OperatingProfile(
        frequency_hz=145_500_000,
        mode="FM",
        data_mode=True,
        vox=False,
    )
    snapshot = await apply_profile(radio, profile)
    # ... operate ...
    await radio.restore_state(snapshot)

    # Using a built-in preset
    snapshot = await apply_profile(radio, PRESETS.ft8_20m)
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from types import SimpleNamespace
from typing import Any

from icom_lan.core.types import ScopeCompletionPolicy

logger = logging.getLogger(__name__)

__all__ = [
    "OperatingProfile",
    "apply_profile",
    "PRESETS",
]


async def _apply_vfo(radio: Any, vfo: str) -> None:
    """Apply ``profile.vfo`` via the canonical receiver-tier protocols.

    Routes ``"MAIN"`` / ``"SUB"`` through
    :meth:`~icom_lan.radio_protocol.ReceiverBankCapable.select_receiver`
    and ``"A"`` / ``"B"`` through
    :meth:`~icom_lan.radio_protocol.VfoSlotCapable.set_vfo_slot`, falling
    back to the other if only one is implemented.  The legacy
    ``set_vfo("A"/"B"/"MAIN"/"SUB")`` overload was removed in v0.20
    (#1206), so backends must expose the typed protocol surface.
    """
    target = vfo.upper()
    is_receiver = target in ("MAIN", "SUB")
    is_slot = target in ("A", "B")

    select_receiver = getattr(radio, "select_receiver", None)
    set_vfo_slot = getattr(radio, "set_vfo_slot", None)

    if is_receiver and select_receiver is not None:
        await select_receiver(target)
        return
    if is_slot and set_vfo_slot is not None:
        await set_vfo_slot(target)
        return
    # Cross-protocol fallback for backends that only implement one tier.
    if select_receiver is not None and is_slot:
        await select_receiver("MAIN" if target == "A" else "SUB")
        return
    if set_vfo_slot is not None and is_receiver:
        await set_vfo_slot("A" if target == "MAIN" else "B")
        return
    logger.debug("apply_profile: radio has no select_receiver / set_vfo_slot, skipping")


@dataclass
class OperatingProfile:
    """Declarative desired radio state.

    Each field defaults to ``None``, meaning "do not touch this setting".
    Set only the fields you want to change. Boolean fields like ``vox=False``
    explicitly disable the feature — they are distinct from ``None`` (unchanged).

    Attributes:
        frequency_hz: Tuning frequency in Hz.
        mode: Operating mode string, e.g. ``"FM"``, ``"USB"``, ``"CW"``.
        filter_width: Passband filter width in Hz (passed to ``set_mode``).
        vox: ``True`` to enable VOX, ``False`` to disable.
        split: ``True`` to enable split, ``False`` to disable.
        vfo: VFO to select, ``"A"`` or ``"B"``.
        data_mode: ``True`` to enable DATA mode, ``False`` to disable.
        data_off_mod_input: DATA-OFF modulation input source index.
        data1_mod_input: DATA-1 modulation input source index.
        squelch_level: Squelch level (0 = open).
        equalize_vfo: If ``True``, copy the active VFO to both VFOs after
            tuning. Dispatches to ``radio.equalize_main_sub()`` on dual-RX
            profiles and ``radio.equalize_vfo_ab(0)`` on single-RX profiles.
        scope_enabled: ``True`` to enable spectrum scope, ``False`` to disable,
            ``None`` to leave unchanged.
        scope_mode: Scope centre/fixed mode index.
        scope_span: Scope span index.
        scope_output: Passed as the ``output`` keyword arg to ``enable_scope``.
        scope_policy: Scope completion policy (``ScopeCompletionPolicy``).
        scope_timeout: Timeout in seconds for scope enable/verify.
    """

    frequency_hz: int | None = None
    mode: str | None = None
    filter_width: int | None = None
    vox: bool | None = None
    split: bool | None = None
    vfo: str | None = None
    data_mode: bool | None = None
    data_off_mod_input: int | None = None
    data1_mod_input: int | None = None
    squelch_level: int | None = None
    equalize_vfo: bool = False
    scope_enabled: bool | None = None
    scope_mode: int | None = None
    scope_span: int | None = None
    scope_output: bool = False
    scope_policy: ScopeCompletionPolicy | str = ScopeCompletionPolicy.FAST
    scope_timeout: float = 5.0


async def apply_profile(radio: Any, profile: OperatingProfile) -> dict[str, object]:
    """Apply a declarative profile to a radio and return a restore snapshot.

    Steps are applied in a safe order: VOX → VFO selection → split →
    frequency → mode → DATA mode → modulation inputs → VFO equalise →
    squelch → scope → final VFO re-select.

    Each step is silently skipped if:

    - The field is ``None`` in the profile (not specified by the caller).
    - The radio object lacks the required setter method.

    A ``DEBUG`` log message is emitted for every skipped capability.

    Args:
        radio: Any radio object — LAN-connected ``IcomRadio``, serial backend,
            or a test double.
        profile: Desired state to apply.

    Returns:
        A snapshot dict from ``radio.snapshot_state()`` suitable for passing
        to ``radio.restore_state()`` to undo all changes.
    """
    snapshot = await radio.snapshot_state()

    if profile.vox is not None:
        if hasattr(radio, "set_vox"):
            await radio.set_vox(profile.vox)
        else:
            logger.debug("apply_profile: radio has no set_vox, skipping")

    if profile.vfo is not None:
        await _apply_vfo(radio, profile.vfo)

    if profile.split is not None:
        if hasattr(radio, "set_split"):
            await radio.set_split(profile.split)
        else:
            logger.debug("apply_profile: radio has no set_split, skipping")

    if profile.frequency_hz is not None:
        if hasattr(radio, "set_freq"):
            await radio.set_freq(profile.frequency_hz)
        else:
            logger.debug("apply_profile: radio has no set_freq, skipping")

    if profile.mode is not None:
        if hasattr(radio, "set_mode"):
            if profile.filter_width is not None:
                await radio.set_mode(profile.mode, filter_width=profile.filter_width)
            else:
                await radio.set_mode(profile.mode)
        else:
            logger.debug("apply_profile: radio has no set_mode, skipping")

    if profile.data_mode is not None:
        if hasattr(radio, "set_data_mode"):
            await radio.set_data_mode(profile.data_mode)
        else:
            logger.debug("apply_profile: radio has no set_data_mode, skipping")

    if profile.data_off_mod_input is not None:
        if hasattr(radio, "set_data_off_mod_input"):
            await radio.set_data_off_mod_input(profile.data_off_mod_input)
        else:
            logger.debug("apply_profile: radio has no set_data_off_mod_input, skipping")

    if profile.data1_mod_input is not None:
        if hasattr(radio, "set_data1_mod_input"):
            await radio.set_data1_mod_input(profile.data1_mod_input)
        else:
            logger.debug("apply_profile: radio has no set_data1_mod_input, skipping")

    if profile.equalize_vfo:
        # Inline the dispatch previously hidden behind the deprecated
        # ``vfo_equalize`` alias: dual-RX profiles use ``equalize_main_sub``
        # (MAIN→SUB), single-RX profiles use ``equalize_vfo_ab(0)`` (A→B).
        # The profile guard tolerates duck-typed radios that may not expose a
        # ``profile`` attribute (e.g. lightweight test doubles) — when in
        # doubt, prefer the canonical method that is actually present.
        radio_profile = getattr(radio, "profile", None)
        receiver_count = getattr(radio_profile, "receiver_count", None)
        is_dual_rx = isinstance(receiver_count, int) and receiver_count > 1
        if is_dual_rx and hasattr(radio, "equalize_main_sub"):
            await radio.equalize_main_sub()
        elif not is_dual_rx and hasattr(radio, "equalize_vfo_ab"):
            await radio.equalize_vfo_ab(0)
        elif hasattr(radio, "equalize_main_sub"):
            # No profile info but the dual-RX method is available — assume
            # the caller knows what they're doing (covers AsyncMock-based
            # test doubles that pre-declare ``equalize_main_sub``).
            await radio.equalize_main_sub()
        elif hasattr(radio, "equalize_vfo_ab"):
            await radio.equalize_vfo_ab(0)
        else:
            logger.debug(
                "apply_profile: radio has no equalize_main_sub or "
                "equalize_vfo_ab, skipping"
            )

    if profile.squelch_level is not None:
        if hasattr(radio, "set_squelch"):
            await radio.set_squelch(profile.squelch_level)
        else:
            logger.debug("apply_profile: radio has no set_squelch, skipping")

    if profile.scope_enabled is not None:
        if profile.scope_enabled:
            if hasattr(radio, "enable_scope"):
                await radio.enable_scope(
                    output=profile.scope_output,
                    policy=profile.scope_policy,
                    timeout=profile.scope_timeout,
                )
            else:
                logger.debug("apply_profile: radio has no enable_scope, skipping")
            if profile.scope_mode is not None:
                if hasattr(radio, "set_scope_mode"):
                    await radio.set_scope_mode(profile.scope_mode)
                else:
                    logger.debug("apply_profile: radio has no set_scope_mode, skipping")
            if profile.scope_span is not None:
                if hasattr(radio, "set_scope_span"):
                    await radio.set_scope_span(profile.scope_span)
                else:
                    logger.debug("apply_profile: radio has no set_scope_span, skipping")
        else:
            if hasattr(radio, "disable_scope"):
                await radio.disable_scope()
            else:
                logger.debug("apply_profile: radio has no disable_scope, skipping")

    # Re-select VFO at the end to ensure consistent state after all operations.
    if profile.vfo is not None:
        await _apply_vfo(radio, profile.vfo)

    return snapshot  # type: ignore[no-any-return]


#: Built-in operating presets for common modes.
PRESETS = SimpleNamespace(
    aprs_vhf=OperatingProfile(
        frequency_hz=145_500_000,
        mode="FM",
        data_mode=True,
        vox=False,
    ),
    ft8_20m=OperatingProfile(
        frequency_hz=14_074_000,
        mode="USB",
        data_mode=True,
        vox=False,
    ),
    cw_contest=OperatingProfile(
        vox=False,
        split=False,
    ),
    ssb_40m=OperatingProfile(
        frequency_hz=7_040_000,
        mode="LSB",
    ),
)
