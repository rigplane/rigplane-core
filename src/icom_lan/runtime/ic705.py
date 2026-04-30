"""IC-705 convenience helpers for data and packet-mode workflows.

These functions are thin wrappers around the generic :func:`apply_profile`
system with IC-705-specific defaults pre-filled.  They exist for backward
compatibility; new code should use :func:`apply_profile` directly.
"""

from __future__ import annotations

from typing import Any

from icom_lan.profiles_runtime import OperatingProfile, apply_profile
from icom_lan.types import ScopeCompletionPolicy


async def prepare_ic705_data_profile(
    radio: Any,
    *,
    frequency_hz: int,
    mode: str = "FM",
    data_off_mod_input: int | None = None,
    data1_mod_input: int | None = None,
    disable_vox: bool = True,
    squelch_level: int | None = 0,
    enable_scope: bool = False,
    scope_output: bool = False,
    scope_policy: ScopeCompletionPolicy | str = ScopeCompletionPolicy.FAST,
    scope_timeout: float = 5.0,
    scope_mode: int | None = 0,
    scope_span: int | None = 7,
) -> dict[str, object]:
    """Prepare an IC-705 for packet/data work and return a restore snapshot.

    The helper applies the common packet/data workflow used by downstream
    integrations:
    - select VFO A
    - disable split
    - tune both VFOs to the same frequency
    - set a mode such as FM
    - enable DATA mode
    - optionally route modulation inputs
    - optionally open squelch and enable scope

    Returns:
        Snapshot from :meth:`snapshot_state` suitable for
        :func:`restore_ic705_data_profile`.
    """
    profile = OperatingProfile(
        frequency_hz=int(frequency_hz),
        mode=mode,
        vox=False if disable_vox else None,
        split=False,
        vfo="A",
        data_mode=True,
        data_off_mod_input=int(data_off_mod_input)
        if data_off_mod_input is not None
        else None,
        data1_mod_input=int(data1_mod_input) if data1_mod_input is not None else None,
        squelch_level=int(squelch_level) if squelch_level is not None else None,
        equalize_vfo=True,
        scope_enabled=True if enable_scope else None,
        scope_mode=int(scope_mode)
        if (enable_scope and scope_mode is not None)
        else None,
        scope_span=int(scope_span)
        if (enable_scope and scope_span is not None)
        else None,
        scope_output=scope_output,
        scope_policy=scope_policy,
        scope_timeout=scope_timeout,
    )
    return await apply_profile(radio, profile)


async def restore_ic705_data_profile(
    radio: Any,
    snapshot: dict[str, object],
) -> None:
    """Restore a snapshot returned by :func:`prepare_ic705_data_profile`."""
    await radio.restore_state(snapshot)


__all__ = [
    "prepare_ic705_data_profile",
    "restore_ic705_data_profile",
]
