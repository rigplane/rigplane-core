"""Advise the keying session when USB voice cannot reach the radio MOD input."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Coroutine
from dataclasses import dataclass
from typing import Protocol, runtime_checkable

from rigplane.audio.route import TxAudioSource, resolve_audio_route
from rigplane.core.radio_protocol import Radio
from rigplane.core.state_pipeline_contracts import FieldPath
from rigplane.core.state_store import FreshnessState, StateSnapshot, StateStore
from rigplane.profiles import RadioProfile, selector_one_is_sub

from .runtime_helpers import primary_receiver_snapshot_ids  # noqa: TID251

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

VOICE_MODES: frozenset[str] = frozenset({"USB", "LSB", "AM", "FM"})


@runtime_checkable
class ModInputReadable(Protocol):
    @property
    def profile(self) -> RadioProfile: ...

    async def get_data_off_mod_input(self) -> int: ...


@runtime_checkable
class ModInputWarningHost(Protocol):
    @property
    def command_state_store(self) -> StateStore: ...

    def _spawn(self, coro: Coroutine[object, object, None]) -> asyncio.Task[None]: ...

    def send_notification_to_session(
        self,
        session_id: str,
        level: str,
        message: str,
        category: str,
        *,
        code: str,
        params: dict[str, str],
    ) -> bool: ...


@dataclass(frozen=True, slots=True)
class _TxFacts:
    mode: str
    data_mode: int


def _fresh(snapshot: StateSnapshot, path: FieldPath) -> object:
    """One snapshot field's value when FRESH, else ``None``."""
    try:
        field = snapshot.field(path)
    except KeyError:
        return None
    if field.freshness is not FreshnessState.FRESH:
        return None
    return field.value


def tx_voice_facts(store: StateStore, profile: RadioProfile) -> _TxFacts | None:
    """Read fresh TX mode and DATA from the profile's active or split path."""
    snapshot = store.snapshot()
    split = _fresh(snapshot, FieldPath.global_("tx_state", "split"))
    if not isinstance(split, bool):
        return None
    receiver_ids = primary_receiver_snapshot_ids()
    mode_path = FieldPath.unselected if split else FieldPath.active
    uses_ab = profile.vfo_scheme == "ab"
    if split and profile.vfo_scheme == "main_sub":
        if selector_one_is_sub(profile.receiver_count, profile.split_tx_target):
            receiver_ids = ("1", "sub")
            mode_path = FieldPath.active
        elif profile.split_tx_target != "main_unselected":
            return None
    for receiver_id in receiver_ids:
        if uses_ab and _fresh(snapshot, FieldPath.active_slot(receiver_id)) not in (
            "A",
            "B",
        ):
            continue
        mode = _fresh(snapshot, mode_path(receiver_id, "freq_mode", "mode"))
        data_mode = _fresh(snapshot, mode_path(receiver_id, "freq_mode", "data_mode"))
        if isinstance(mode, str) and type(data_mode) is int:
            return _TxFacts(mode=mode, data_mode=data_mode)
    return None


def _usb_source_value(profile: RadioProfile) -> tuple[int, dict[int, str]] | None:
    names = dict(profile.data_mode_inputs or ())
    for value, name in names.items():
        if name == "USB":
            return value, names
    return None


async def warn_when_mod_input_blocks_voice(
    server: ModInputWarningHost, radio: Radio, session_id: str
) -> None:
    """Read the DATA-OFF MOD input once and warn when voice cannot pass."""
    try:
        await _check(server, radio, session_id)
    except Exception:
        logger.warning(
            "mod-input: the USB voice check itself failed and was skipped "
            "(this is a Core bug, not a radio condition)",
            exc_info=True,
        )


async def _check(server: ModInputWarningHost, radio: Radio, session_id: str) -> None:
    if not isinstance(radio, ModInputReadable):
        return
    profile = radio.profile
    mapping = _usb_source_value(profile)
    if (
        mapping is None
        or resolve_audio_route(radio).tx_audio_source is not TxAudioSource.USB
    ):
        return
    usb_value, names = mapping
    facts = tx_voice_facts(server.command_state_store, profile)
    if facts is None or facts.mode not in VOICE_MODES or facts.data_mode != 0:
        return
    model = profile.model
    try:
        source = await radio.get_data_off_mod_input()
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
        server.send_notification_to_session(
            session_id,
            "warning",
            _UNREADABLE_MESSAGE,
            "audio",
            code=MOD_INPUT_UNREADABLE_CODE,
            params={"reason": reason},
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
    server.send_notification_to_session(
        session_id,
        "warning",
        _NOT_USB_MESSAGE,
        "audio",
        code=MOD_INPUT_NOT_USB_CODE,
        params={"source": names.get(source, str(source)), "expected": "USB"},
    )
