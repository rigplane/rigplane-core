from __future__ import annotations

import datetime
from typing import TYPE_CHECKING, Any

from ..radio_protocol import (
    AudioCapable,
    DualReceiverCapable,
    ScopeCapable,
    UsbAudioCapable,
)
from ..radio_state import RadioState

if TYPE_CHECKING:
    from ..radio_protocol import Radio

__all__ = ["runtime_capabilities", "radio_ready", "build_public_state_payload"]

_RECEIVER_KEY_MAP = {"freq": "freqHz"}


def runtime_capabilities(radio: "Radio | None") -> set[str]:
    """Return conservative runtime capabilities for the active radio.

    Semantics:
    - If ``radio`` is ``None`` → empty set.
    - If ``radio.capabilities`` is a set (including the empty set), use it as the
      starting point and *do not* fall back to Protocol checks, but drop tags
      that contradict the runtime Protocols (e.g. tag ``"scope"`` without
      :class:`ScopeCapable`).
    - If ``radio.capabilities`` is missing or not a set, derive tags purely from
      the capability Protocols implemented by the instance.
    """
    if radio is None:
        return set()

    raw_caps = getattr(radio, "capabilities", None)
    if isinstance(raw_caps, set):
        caps = set(raw_caps)
        if "scope" in caps and not isinstance(radio, ScopeCapable):
            caps.discard("scope")
        if "audio" in caps and not isinstance(radio, AudioCapable | UsbAudioCapable):
            # Radios that don't implement in-band ``AudioCapable`` and
            # don't expose OS-level USB Audio Class devices (via the
            # ``UsbAudioCapable`` marker) cannot deliver audio to the
            # frontend — drop the tag so the UI doesn't render dead
            # controls.
            caps.discard("audio")
        if "dual_rx" in caps and not isinstance(radio, DualReceiverCapable):
            caps.discard("dual_rx")
        return caps

    result: set[str] = set()
    if isinstance(radio, ScopeCapable):
        result.add("scope")
    if isinstance(radio, AudioCapable | UsbAudioCapable):
        result.add("audio")
    if isinstance(radio, DualReceiverCapable):
        result.add("dual_rx")
    return result


def radio_ready(radio: "Radio | None") -> bool:
    """Return backend radio readiness (CI-V healthy), with fallback.

    Rules:
    - ``None`` → ``False``.
    - If ``radio.radio_ready`` is a bool, use it.
    - Otherwise fall back to ``bool(radio.connected)`` when the attribute is a
      proper bool; non-bool truthy values do not count as connected.
    """
    if radio is None:
        return False
    ready: Any = getattr(radio, "radio_ready", None)
    if isinstance(ready, bool):
        return ready
    connected: Any = getattr(radio, "connected", False)
    return connected if isinstance(connected, bool) else False


def _to_camel(s: str) -> str:
    parts = s.split("_")
    return parts[0] + "".join(p.capitalize() for p in parts[1:])


def _camel_keys(d: dict[str, Any]) -> dict[str, Any]:
    return {
        _to_camel(k): (_camel_keys(v) if isinstance(v, dict) else v)
        for k, v in d.items()
    }


def _camel_case_state(d: dict[str, Any]) -> dict[str, Any]:
    connection = {
        "rigConnected": d.get("connected", False),
        "radioReady": d.get("radio_ready", False),
        "controlConnected": d.get("control_connected", False),
    }
    skip = {"connected", "radio_ready", "control_connected"}
    result: dict[str, Any] = {}
    for key, value in d.items():
        if key in skip:
            continue
        if key in ("main", "sub") and isinstance(value, dict):
            inner = {}
            for inner_key, inner_value in value.items():
                new_key = _RECEIVER_KEY_MAP.get(inner_key, _to_camel(inner_key))
                inner[new_key] = (
                    _camel_keys(inner_value)
                    if isinstance(inner_value, dict)
                    else inner_value
                )
            result[key] = inner
        elif isinstance(value, dict):
            result[_to_camel(key)] = _camel_keys(value)
        else:
            result[_to_camel(key)] = value
    result["connection"] = connection
    return result


def build_public_state_payload(
    radio_state: RadioState,
    *,
    radio: "Radio | None",
    revision: int,
    receiver_count: int,
    updated_at: str | None = None,
    scope_clients: int = 0,
    control_clients: int = 0,
    audio_clients: int = 0,
) -> dict[str, Any]:
    """Build the canonical public web state payload from RadioState.

    This is the single web-facing state contract used by HTTP and WebSocket
    consumers. During the migration away from StateCache, all public state
    should be derived here.
    """
    state = radio_state.to_dict()
    raw_connected = getattr(radio, "connected", False) if radio else False
    state["connected"] = raw_connected if isinstance(raw_connected, bool) else False
    state["radio_ready"] = radio_ready(radio)
    raw_control_connected = (
        getattr(radio, "control_connected", False) if radio else False
    )
    state["control_connected"] = (
        raw_control_connected if isinstance(raw_control_connected, bool) else False
    )
    state["revision"] = revision
    state["updated_at"] = (
        updated_at or datetime.datetime.now(datetime.timezone.utc).isoformat()
    )
    if receiver_count < 2:
        state.pop("sub", None)

    # Radio connection detail for status bar
    conn_state_val = getattr(radio, "conn_state", None) if radio else None
    radio_status: str = "disconnected"
    if conn_state_val is not None and hasattr(conn_state_val, "value"):
        raw_val = conn_state_val.value
        if isinstance(raw_val, str):
            radio_status = raw_val
    elif raw_connected:
        # Serial backends (Yaesu CAT) don't have conn_state enum;
        # fall back to the connected boolean.
        radio_status = "connected"
    state["radio_detail"] = {
        "status": radio_status,
    }
    state["ws_clients"] = {
        "scope": scope_clients,
        "control": control_clients,
        "audio": audio_clients,
    }

    return _camel_case_state(state)
