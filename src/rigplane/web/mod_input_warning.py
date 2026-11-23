"""MOR-4660 — warn when a USB voice key cannot carry the operator's audio.

Bench 2026-11-21 (IC-705 over USB, Pro beta.20-rc.1): the radio keyed, no
voice went out. The station pushed TX PCM over USB while the radio's DATA
OFF MOD input was WLAN (CI-V ``1A 05 01 18`` = 3), so the modulator took
its audio from WLAN and the USB stream went nowhere. Nothing on either
side said why — a silent failure on a keyed radio.

The fix is a warning, never a refusal (owner/adviser decision after
PR #4756 was parked with ordering defects around a blocking check): when
an accepted managed PTT keys a voice mode over a USB TX audio link on a
profile that has a modulation-input setting, read the DATA-OFF modulation
source and tell the operator when it is not the profile's USB value — or
when it cannot be read. PTT is never delayed, refused or reordered by
this check: it runs as a detached task started after admission, reads one
CI-V menu item, and its only effects are one session toast (the same
``notification`` wire every other warning uses) and one ``WARNING`` log
line (the support report copies the log). The radio's setting is never
written, and CW and data modes are never touched: the check reads the
DATA-OFF source only, and only when the transmitting VFO is in a voice
mode with the DATA flag off.

Every radio fact lives in profile data: the command bytes come from the
profile's command map (``get_data_off_mod_input``), the source enum and
the USB value from ``[data_mode].inputs`` (``profile.data_mode_inputs``).
Yaesu and Xiegu profiles declare no inputs, so the check skips itself
there — by data, not by radio name. The one piece of mode physics that
is not a radio fact is the phone-mode set (USB/LSB/AM/FM), the same set
for every radio.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from rigplane.audio.route import TxAudioSource, resolve_audio_route
from rigplane.core.state_pipeline_contracts import FieldPath
from rigplane.core.state_store import FreshnessState, StateStore

from .runtime_helpers import primary_receiver_snapshot_ids  # noqa: TID251

if TYPE_CHECKING:
    from rigplane.core.state_pipeline_contracts import StateSnapshot

__all__ = [
    "MOD_INPUT_NOT_USB_CODE",
    "MOD_INPUT_UNREADABLE_CODE",
    "VOICE_MODES",
    "warn_when_mod_input_blocks_voice",
]

logger = logging.getLogger(__name__)

#: Stable reason codes the frontend resolves via ``core.toast.<code>``.
MOD_INPUT_NOT_USB_CODE = "modInputNotUsb"
MOD_INPUT_UNREADABLE_CODE = "modInputUnreadable"

_NOT_USB_MESSAGE = "MOD input is not USB: your voice will not go out"
_UNREADABLE_MESSAGE = "MOD input could not be read: your voice may not go out"

#: The phone (voice) modes a USB voice key carries. Not a radio fact: the
#: same set for every radio, and the modes the DATA flag can ride on
#: (``[data_mode].modes`` where a profile declares it). CW keys through the
#: keyer, RTTY/DV/WFM are not this path's voice keys.
VOICE_MODES: frozenset[str] = frozenset({"USB", "LSB", "AM", "FM"})


@dataclass(frozen=True, slots=True)
class _TxFacts:
    """FRESH mode facts of the transmitting VFO (MOR-1496 derivation)."""

    mode: str
    data_mode: int


def _fresh(snapshot: StateSnapshot, path: FieldPath) -> Any:
    """One snapshot field's value when FRESH, else ``None``."""
    try:
        field = snapshot.field(path)
    except KeyError:
        return None
    if field.freshness is not FreshnessState.FRESH:
        return None
    return field.value


def tx_voice_facts(store: StateStore) -> _TxFacts | None:
    """The transmitting VFO's mode and DATA flag, or ``None``.

    Split OFF transmits on the selected slot; split ON transmits on the
    OTHER slot of the same receiver — the same identity derivation
    ``RadioPoller._compute_tx_target`` documents (MOR-1496), read here for
    the mode instead of the frequency. Any missing or stale input returns
    ``None``: without a fresh voice fact there is nothing to warn about,
    and the check stays silent rather than guess (fail-closed gate, the
    read itself is what fails loudly).
    """
    snapshot = store.snapshot()
    for receiver_id in primary_receiver_snapshot_ids():
        slot = _fresh(snapshot, FieldPath.active_slot(receiver_id))
        if slot not in ("A", "B"):
            continue
        split = _fresh(snapshot, FieldPath.global_("tx_state", "split"))
        if not isinstance(split, bool):
            continue
        mode_path = FieldPath.unselected if split else FieldPath.active
        mode = _fresh(snapshot, mode_path(receiver_id, "freq_mode", "mode"))
        data_mode = _fresh(snapshot, mode_path(receiver_id, "freq_mode", "data_mode"))
        if isinstance(mode, str) and type(data_mode) is int:
            return _TxFacts(mode=mode, data_mode=data_mode)
    return None


def _usb_source_value(radio: Any) -> tuple[int, dict[int, str]] | None:
    """``(usb value, value → source name)`` from profile data, or ``None``.

    ``None`` means the profile has no modulation-input setting (or no USB
    source in it): the check is skipped by data. Only the entry named
    exactly ``USB`` counts — the combo entries (``MIC+USB`` &c.) are not
    the setting that guarantees the USB voice path.
    """
    profile = getattr(radio, "profile", None)
    inputs = getattr(profile, "data_mode_inputs", None) or ()
    names: dict[int, str] = {}
    usb_value: int | None = None
    for value, name in inputs:
        if type(value) is int and isinstance(name, str):
            names[value] = name
            if name == "USB":
                usb_value = value
    if usb_value is None:
        return None
    return usb_value, names


def _notify(
    server: Any,
    session_id: str | None,
    code: str,
    message: str,
    params: dict[str, Any],
) -> None:
    """One operator toast to the keying session (MOR-1422 doctrine)."""
    send = getattr(server, "send_notification_to_session", None)
    if session_id and callable(send):
        send(session_id, "warning", message, "audio", code=code, params=params)


async def warn_when_mod_input_blocks_voice(
    server: Any, radio: Any, session_id: str | None
) -> None:
    """Read the DATA-OFF MOD input once and warn when voice cannot pass.

    Fully contained: nothing this coroutine does — including its own bugs —
    reaches the caller or the event loop; the worst case is one extra log
    line. Cancellation is the only thing that propagates.
    """
    try:
        await _check(server, radio, session_id)
    except asyncio.CancelledError:
        raise
    except Exception:
        logger.warning(
            "mod-input: the USB voice check itself failed and was skipped "
            "(this is a Core bug, not a radio condition)",
            exc_info=True,
        )


async def _check(server: Any, radio: Any, session_id: str | None) -> None:
    if radio is None or server is None:
        return
    route = resolve_audio_route(radio)
    if route.tx_audio_source is not TxAudioSource.USB:
        return
    mapping = _usb_source_value(radio)
    if mapping is None:
        return
    usb_value, names = mapping
    getter = getattr(radio, "get_data_off_mod_input", None)
    if not callable(getter):
        return
    store = getattr(server, "command_state_store", None)
    if not isinstance(store, StateStore):
        return
    facts = tx_voice_facts(store)
    if facts is None or facts.mode not in VOICE_MODES or facts.data_mode != 0:
        return
    model = getattr(getattr(radio, "profile", None), "model", "?")
    try:
        source = int(await getter())
    except asyncio.CancelledError:
        raise
    except Exception as exc:
        reason = type(exc).__name__
        logger.warning(
            "mod-input: %s voice TX over USB but the DATA OFF MOD input could "
            "not be read (%s: %s) — the voice may not go out; check the "
            "radio's MOD input (session=%s)",
            model,
            reason,
            exc,
            session_id,
        )
        _notify(
            server,
            session_id,
            MOD_INPUT_UNREADABLE_CODE,
            _UNREADABLE_MESSAGE,
            {"reason": reason},
        )
        return
    if source == usb_value:
        logger.debug(
            "mod-input: %s DATA OFF MOD is USB (%d); the USB voice path is "
            "clear (session=%s)",
            model,
            source,
            session_id,
        )
        return
    logger.warning(
        "mod-input: %s voice TX over USB but DATA OFF MOD is %s (%d), not USB "
        "(%d) — the voice will not go out; set the radio's MOD input to USB "
        "(session=%s)",
        model,
        names.get(source, "unknown"),
        source,
        usb_value,
        session_id,
    )
    _notify(
        server,
        session_id,
        MOD_INPUT_NOT_USB_CODE,
        _NOT_USB_MESSAGE,
        {"source": names.get(source, str(source)), "expected": "USB"},
    )
