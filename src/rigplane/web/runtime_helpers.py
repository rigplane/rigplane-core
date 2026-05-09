from __future__ import annotations

import datetime
import time
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

__all__ = [
    "runtime_capabilities",
    "radio_ready",
    "classify_radio_health",
    "build_public_state_payload",
]

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


def _bool_attr(obj: Any, name: str, default: bool = False) -> bool:
    raw = getattr(obj, name, default)
    return raw if isinstance(raw, bool) else default


def _conn_state_value(radio: Any) -> str | None:
    conn_state = getattr(radio, "conn_state", None)
    raw = getattr(conn_state, "value", conn_state)
    return raw if isinstance(raw, str) else None


def _civ_stats(radio: Any) -> dict[str, Any]:
    stats_fn = getattr(radio, "civ_stats", None)
    if not callable(stats_fn):
        return {}
    try:
        stats = stats_fn()
    except Exception:
        return {}
    return stats if isinstance(stats, dict) else {}


def classify_radio_health(
    radio: "Radio | None",
    *,
    server_reachable: bool = True,
    now_monotonic: float | None = None,
) -> dict[str, Any]:
    """Classify server/radio health from runtime evidence.

    The web server can only assert its own reachability when it is answering
    the request; browser-side failures should override this to
    ``server_unreachable``. Radio classification is intentionally conservative:
    short CI-V gaps are ``delayed``, longer gaps are ``stalled``, and
    ``radio_powered_off_likely`` requires prior availability plus repeated
    timeout/recovery evidence.
    """
    now = time.monotonic() if now_monotonic is None else now_monotonic
    if not server_reachable:
        return {
            "serverReachable": False,
            "radioLink": "unknown",
            "readiness": "stalled",
            "likelyCause": "server_unreachable",
            "sinceMs": 0,
            "lastError": None,
        }
    if radio is None:
        return {
            "serverReachable": True,
            "radioLink": "unknown",
            "readiness": "stalled",
            "likelyCause": "unknown",
            "sinceMs": 0,
            "lastError": None,
        }

    connected = _bool_attr(radio, "connected")
    ready = radio_ready(radio)
    conn_state = _conn_state_value(radio)
    if conn_state in {"connecting", "reconnecting"}:
        radio_link = "reconnecting"
    elif connected or conn_state == "connected":
        radio_link = "connected"
    elif conn_state == "disconnected":
        radio_link = "disconnected"
    else:
        radio_link = "unknown"

    stats = _civ_stats(radio)
    last_error = getattr(radio, "last_error", None)
    last_error_value = last_error if isinstance(last_error, str) else None

    if ready:
        return {
            "serverReachable": True,
            "radioLink": "connected",
            "readiness": "ready",
            "likelyCause": "unknown",
            "sinceMs": 0,
            "lastError": last_error_value,
        }

    if radio_link in {"reconnecting", "disconnected"}:
        return {
            "serverReachable": True,
            "radioLink": radio_link,
            "readiness": "recovering" if radio_link == "reconnecting" else "stalled",
            "likelyCause": "radio_network_lost",
            "sinceMs": 0,
            "lastError": last_error_value,
        }

    last_civ = getattr(radio, "_last_civ_data_received", None)
    last_civ_value: float | None = None
    if isinstance(last_civ, (int, float)) and not isinstance(last_civ, bool):
        last_civ_value = float(last_civ)
    has_civ_evidence = last_civ_value is not None
    had_success = _bool_attr(radio, "_has_connected_once") or has_civ_evidence
    if radio_link == "unknown" and not had_success and not stats:
        return {
            "serverReachable": True,
            "radioLink": "unknown",
            "readiness": "stalled",
            "likelyCause": "unknown",
            "sinceMs": 0,
            "lastError": last_error_value,
        }

    idle_s = 0.0
    if last_civ_value is not None:
        idle_s = max(0.0, now - last_civ_value)
    ready_timeout = getattr(radio, "_civ_ready_idle_timeout", 2.0)
    if not isinstance(ready_timeout, (int, float)) or isinstance(ready_timeout, bool):
        ready_timeout = 2.0
    delayed_limit = max(float(ready_timeout) * 2.0, 2.0)
    readiness = "delayed" if idle_s <= delayed_limit else "stalled"

    timeouts = stats.get("timeouts", 0)
    timeout_count = timeouts if isinstance(timeouts, int) else 0
    recovering = _bool_attr(radio, "_civ_recovering")
    powered_off_likely = (
        had_success
        and readiness == "stalled"
        and (timeout_count >= 3 or recovering)
        and idle_s >= max(delayed_limit, 10.0)
    )

    return {
        "serverReachable": True,
        "radioLink": radio_link,
        "readiness": readiness,
        "likelyCause": (
            "radio_powered_off_likely" if powered_off_likely else "radio_not_responding"
        ),
        "sinceMs": int(idle_s * 1000),
        "lastError": last_error_value,
    }


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
    radio_health: dict[str, Any] | None = None,
    health_revision: int = 0,
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
    state["health_revision"] = health_revision
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
    state["radio_health"] = radio_health or classify_radio_health(radio)
    state["ws_clients"] = {
        "scope": scope_clients,
        "control": control_clients,
        "audio": audio_clients,
    }

    return _camel_case_state(state)
