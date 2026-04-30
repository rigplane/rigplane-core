"""Snapshot/restore helpers for :class:`IcomRadio` state.

Extracted from ``radio.py`` (issue #1258, Tier 3 wave 3 of #1063) to slim down
the god-object module. The two functions here read from / write to a radio
instance using only its public API (``get_*``/``set_*``) plus a small number of
documented internal attributes (``_last_*`` caches, ``_attenuator_state``,
``_preamp_level``, ``_filter_width``) and the wire-level helper
``_set_vfo_wire``.

The behaviour is intentionally identical to the previous ``IcomRadio.snapshot_state``
and ``IcomRadio.restore_state`` methods: best-effort, with all per-field failures
swallowed and logged at DEBUG level. The public ``IcomRadio.snapshot_state`` /
``IcomRadio.restore_state`` methods now delegate here.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, cast

from icom_lan.types import Mode

if TYPE_CHECKING:
    # Internal implementation module for IcomRadio — the TID251 ban targets
    # external consumers (web/, rigctld/), not radio.py's own helpers.
    from icom_lan.radio import IcomRadio  # type: ignore[attr-defined]  # noqa: TID251

logger = logging.getLogger(__name__)


async def snapshot_state(radio: IcomRadio) -> dict[str, object]:
    """Best-effort snapshot of core rig state for safe restore."""
    radio._check_connected()
    state: dict[str, object] = {}

    try:
        state["frequency"] = await radio.get_freq()
    except Exception:
        logger.debug("snapshot: get_freq failed, using cache", exc_info=True)
        if radio._last_freq_hz is not None:
            state["frequency"] = radio._last_freq_hz

    try:
        mode, filt = await radio.get_mode_info()
        state["mode"] = mode
        if filt is not None:
            state["filter"] = filt
    except Exception:
        logger.debug("snapshot: get_mode_info failed, using cache", exc_info=True)
        if radio._last_mode is not None:
            state["mode"] = radio._last_mode
        if radio._filter_width is not None:
            state["filter"] = radio._filter_width

    try:
        state["power"] = await radio.get_rf_power()
    except Exception:
        logger.debug("snapshot: get_rf_power failed, using cache", exc_info=True)
        if radio._last_power is not None:
            state["power"] = radio._last_power

    if radio._last_split is not None:
        state["split"] = radio._last_split
    if radio._last_vfo is not None:
        state["vfo"] = radio._last_vfo
    if radio._attenuator_state is not None:
        state["attenuator"] = radio._attenuator_state
    if radio._preamp_level is not None:
        state["preamp"] = radio._preamp_level
    try:
        state["vox"] = await radio.get_vox()
    except Exception:
        logger.debug("snapshot: get_vox failed", exc_info=True)
    try:
        state["data_mode"] = await radio.get_data_mode()
    except Exception:
        logger.debug("snapshot: get_data_mode failed", exc_info=True)
    try:
        state["data_off_mod_input"] = await radio.get_data_off_mod_input()
    except Exception:
        logger.debug("snapshot: get_data_off_mod_input failed", exc_info=True)
    try:
        state["data1_mod_input"] = await radio.get_data1_mod_input()
    except Exception:
        logger.debug("snapshot: get_data1_mod_input failed", exc_info=True)

    return state


async def restore_state(radio: IcomRadio, state: dict[str, object]) -> None:
    """Best-effort restore of state produced by :func:`snapshot_state`."""
    radio._check_connected()

    if "split" in state:
        try:
            await radio.set_split(bool(state["split"]))
        except Exception:
            logger.debug("restore_state: set_split failed", exc_info=True)
    if "vfo" in state:
        try:
            # Internal: ``_set_vfo_wire`` accepts the full
            # "A"/"B"/"MAIN"/"SUB" alphabet that snapshots may carry
            # (the public ``set_vfo`` overload was removed in v0.20,
            # see #1206 — ``set_vfo_slot`` only accepts A/B).
            await radio._set_vfo_wire(str(state["vfo"]))
        except Exception:
            logger.debug("restore_state: set_vfo failed", exc_info=True)

    if "power" in state:
        try:
            await radio.set_rf_power(int(cast(int, state["power"])))
        except Exception:
            logger.debug("restore_state: set_rf_power failed", exc_info=True)

    mode = state.get("mode")
    filt = state.get("filter")
    if isinstance(mode, Mode):
        try:
            await radio.set_mode(
                mode, filter_width=int(filt) if isinstance(filt, int) else None
            )
        except Exception:
            logger.debug("restore_state: set_mode failed", exc_info=True)

    if "frequency" in state:
        try:
            await radio.set_freq(int(cast(int, state["frequency"])))
        except Exception:
            logger.debug("restore_state: set_frequency failed", exc_info=True)

    if "attenuator" in state:
        try:
            await radio.set_attenuator(bool(state["attenuator"]))
        except Exception:
            logger.debug("restore_state: set_attenuator failed", exc_info=True)

    if "preamp" in state:
        try:
            await radio.set_preamp(int(cast(int, state["preamp"])))
        except Exception:
            logger.debug("restore_state: set_preamp failed", exc_info=True)
    if "vox" in state:
        try:
            await radio.set_vox(bool(state["vox"]))
        except Exception:
            logger.debug("restore_state: set_vox failed", exc_info=True)
    if "data_mode" in state:
        try:
            await radio.set_data_mode(bool(state["data_mode"]))
        except Exception:
            logger.debug("restore_state: set_data_mode failed", exc_info=True)
    if "data_off_mod_input" in state:
        try:
            await radio.set_data_off_mod_input(
                int(cast(int, state["data_off_mod_input"]))
            )
        except Exception:
            logger.debug("restore_state: set_data_off_mod_input failed", exc_info=True)
    if "data1_mod_input" in state:
        try:
            await radio.set_data1_mod_input(int(cast(int, state["data1_mod_input"])))
        except Exception:
            logger.debug("restore_state: set_data1_mod_input failed", exc_info=True)
