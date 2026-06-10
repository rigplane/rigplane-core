from __future__ import annotations

import copy
import datetime
import time
from typing import TYPE_CHECKING, Any, cast

from ..core.state_pipeline_contracts import FieldFamily, FieldScope, FieldPath, VfoSlot
from ..core.state_store import FieldSnapshot, FreshnessState, StateSnapshot
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
    "build_public_state_payload_from_snapshot",
    "primary_receiver_snapshot_ids",
]

_RECEIVER_KEY_MAP = {"freq": "freqHz"}
_SNAPSHOT_RECEIVER_IDS = {
    "0": "main",
    "1": "sub",
    "main": "main",
    "sub": "sub",
}
_EMPTY_STATE_DICT = RadioState().to_dict()
_RECEIVER_FREQ_MODE_FIELDS = {
    "freq_hz": "freq",
    "mode": "mode",
    "filter": "filter",
    "filter_num": "filter",
    "data_mode": "data_mode",
    "filter_width": "filter_width",
}
_VFO_SLOT_FIELDS = {
    "freq_hz": "freq_hz",
    "mode": "mode",
    "filter_num": "filter_num",
    "data_mode": "data_mode",
}
_RECEIVER_OPERATOR_CONTROL_FIELDS = {
    "af_level",
    "rf_gain",
    "squelch",
    "att",
    "preamp",
    "pbt_inner",
    "pbt_outer",
    "nr_level",
    "nb_level",
    "filter_width",
    "if_shift",
    "agc",
    "agc_time_constant",
    "audio_peak_filter",
    "apf_type_level",
    "apf_freq",
    "filter_shape",
    "manual_notch_freq",
    "manual_notch_width",
    "digisel_shift",
    "tone_freq",
    "tsql_freq",
    "key_speed",
    "cw_pitch",
    "monitor_gain",
    "mic_gain",
    "compressor_level",
    "break_in_delay",
    "vox_gain",
    "anti_vox_gain",
    "vox_delay",
}
_RECEIVER_OPERATOR_TOGGLE_FIELDS = {
    "nb",
    "nr",
    "digisel",
    "manual_notch",
    "auto_notch",
    "audio_peak_filter",
    "twin_peak_filter",
    "af_mute",
    "ipplus",
    "dcd",
    "apf_on",
    "narrow",
    "repeater_tone",
    "repeater_tsql",
}
_RECEIVER_SLOW_STATE_FIELDS = {
    "vfo_a",
    "vfo_b",
    "active_slot",
    "filter",
    "contour",
}
_GLOBAL_TX_FIELDS = {
    "ptt",
    "power_on",
    "split",
    "dual_watch",
    "rit_on",
    "rit_tx",
    "monitor_on",
    "vox_on",
    "compressor_on",
    "main_sub_tracking",
    "dial_lock",
    "tx_freq_monitor",
}
_GLOBAL_OPERATOR_CONTROL_FIELDS = {
    "power_level",
    "tuner_status",
    "rit_freq",
    "cw_pitch",
    "mic_gain",
    "key_speed",
    "notch_filter",
    "compressor_level",
    "break_in_delay",
    "break_in",
    "drive_gain",
    "monitor_gain",
    "vox_gain",
    "anti_vox_gain",
    "vox_delay",
    "ssb_tx_bandwidth",
    "ref_adjust",
    "dash_ratio",
    "nb_depth",
    "nb_width",
    "tx_antenna",
}
_GLOBAL_METER_FIELDS = {
    "alc",
    "power",
    "swr",
    "comp",
    "vd",
    "id",
}
_GLOBAL_SLOW_STATE_FIELDS = {
    "active",
    "scanning",
    "scan_type",
    "scan_resume_mode",
    "tuning_step",
    "overflow",
    "cw_spot",
    "vfo_select",
    "rx_antenna_1",
    "rx_antenna_2",
    "tx_band_edges",
    "scope_controls",
    "yaesu",
}
# Public ``scopeControls.<suffix>`` leaves the toolbar/LCD gate on, mapped to
# their backend scope-control field name. The whole group is unobserved until
# a real scope-control observation lands, so every leaf is seeded ``missing``
# in the default snapshot — otherwise an absent leaf would resolve to
# ``available`` on the frontend and render its default (CTR / MID / …) as
# confirmed (MOR-429).
_SCOPE_CONTROL_PUBLIC_FIELDS = {
    "mode": "mode",
    "edge": "edge",
    "span": "span",
    "speed": "speed",
    "hold": "hold",
    "refDb": "ref_db",
    "dual": "dual",
    "receiver": "receiver",
}
# Inverse map: backend scope-control leaf name → public ``scopeControls.``
# suffix, used to project store snapshot fields back out (MOR-557).
_SCOPE_CONTROL_PUBLIC_SUFFIXES = {
    control_name: public_suffix
    for public_suffix, control_name in _SCOPE_CONTROL_PUBLIC_FIELDS.items()
}


def _receiver_public_key(name: str) -> str:
    return _RECEIVER_KEY_MAP.get(name, _to_camel(name))


def _public_field_path(*parts: str) -> str:
    return ".".join(parts)


def _snapshot_field_public_paths(path: FieldPath) -> tuple[str, ...]:
    if path.scope is FieldScope.RECEIVER:
        receiver_key = _snapshot_receiver_key(path.receiver_id)
        if receiver_key is None:
            return ()
        if path.family is FieldFamily.FREQ_MODE:
            if path.slot in (VfoSlot.A, VfoSlot.B):
                slot_key = "vfoA" if path.slot is VfoSlot.A else "vfoB"
                target = _VFO_SLOT_FIELDS.get(path.name)
                if target is None:
                    return ()
                return (
                    _public_field_path(
                        receiver_key,
                        slot_key,
                        _receiver_public_key(target),
                    ),
                )
            if path.slot not in (None, VfoSlot.ACTIVE):
                return ()
            target = _RECEIVER_FREQ_MODE_FIELDS.get(path.name)
            if target is None:
                return ()
            return (_public_field_path(receiver_key, _receiver_public_key(target)),)
        if path.family is FieldFamily.VFO and path.name == "active_slot":
            return (_public_field_path(receiver_key, "activeSlot"),)
        if path.family is FieldFamily.METERS and path.name == "s_meter":
            return (_public_field_path(receiver_key, "sMeter"),)
        if (
            path.family is FieldFamily.OPERATOR_CONTROLS
            and path.name in _RECEIVER_OPERATOR_CONTROL_FIELDS
        ):
            return (_public_field_path(receiver_key, _receiver_public_key(path.name)),)
        if (
            path.family is FieldFamily.OPERATOR_TOGGLES
            and path.name in _RECEIVER_OPERATOR_TOGGLE_FIELDS
        ):
            public_key = _public_field_path(
                receiver_key, _receiver_public_key(path.name)
            )
            if path.name == "dcd":
                # DEPRECATED alias (MOR-466): remove after migration window.
                # ``dcd`` is the neutral promotion of the legacy squelch-open
                # status; project it under both ``dcd`` and the old
                # ``sMeterSqlOpen`` public key (same value + same availability)
                # so existing frontend consumers keep working during migration.
                return (
                    public_key,
                    _public_field_path(receiver_key, "sMeterSqlOpen"),
                )
            return (public_key,)
        if (
            path.family is FieldFamily.SLOW_STATE
            and path.name in _RECEIVER_SLOW_STATE_FIELDS
        ):
            return (_public_field_path(receiver_key, _receiver_public_key(path.name)),)
        return ()

    if path.scope is FieldScope.GLOBAL:
        if path.family is FieldFamily.TX_STATE and path.name in _GLOBAL_TX_FIELDS:
            return (_to_camel(path.name),)
        if (
            path.family is FieldFamily.OPERATOR_CONTROLS
            and path.name in _GLOBAL_OPERATOR_CONTROL_FIELDS
        ):
            return (_to_camel(path.name),)
        if path.family is FieldFamily.METERS and path.name in _GLOBAL_METER_FIELDS:
            return (_to_camel(f"{path.name}_meter"),)
        if (
            path.family is FieldFamily.SLOW_STATE
            and path.name in _GLOBAL_SLOW_STATE_FIELDS
        ):
            return (_to_camel(path.name),)
        return ()

    if path.scope is FieldScope.SCOPE_CONTROLS and path.family is FieldFamily.DISPLAY:
        paths: list[str] = []
        if path.receiver_id is not None and _snapshot_receiver_key(path.receiver_id):
            paths.append("scopeControls.receiver")
        public_suffix = _SCOPE_CONTROL_PUBLIC_SUFFIXES.get(path.name)
        if public_suffix is not None:
            public_path = f"scopeControls.{public_suffix}"
            if public_path not in paths:
                paths.append(public_path)
        return tuple(paths)

    return ()


def _freshness_availability(freshness: FreshnessState) -> str:
    if freshness is FreshnessState.FRESH:
        return "available"
    if freshness is FreshnessState.STALE:
        return "stale"
    return "missing"


def _missing_field_status(path: FieldPath) -> dict[str, Any]:
    return {
        "storePath": str(path),
        "observed": False,
        "freshness": FreshnessState.UNKNOWN.value,
        "availability": "missing",
    }


def _observed_field_status(field: FieldSnapshot) -> dict[str, Any]:
    return {
        "storePath": str(field.path),
        "observed": True,
        "freshness": field.freshness.value,
        "availability": _freshness_availability(field.freshness),
        "lastObservedMonotonic": field.last_observed_monotonic,
        "maxAge": field.max_age,
        "source": field.source.to_dict(),
    }


def _set_missing_field_status(
    statuses: dict[str, dict[str, Any]],
    public_path: str,
    path: FieldPath,
) -> None:
    statuses.setdefault(public_path, _missing_field_status(path))


def _default_receiver_field_status(
    statuses: dict[str, dict[str, Any]],
    receiver_key: str,
) -> None:
    for name, state_key in _RECEIVER_FREQ_MODE_FIELDS.items():
        _set_missing_field_status(
            statuses,
            _public_field_path(receiver_key, _receiver_public_key(state_key)),
            FieldPath.active(receiver_key, "freq_mode", name),
        )
    for slot_name, slot_key in (("A", "vfoA"), ("B", "vfoB")):
        for name, state_key in _VFO_SLOT_FIELDS.items():
            _set_missing_field_status(
                statuses,
                _public_field_path(
                    receiver_key,
                    slot_key,
                    _receiver_public_key(state_key),
                ),
                FieldPath.vfo_slot(receiver_key, slot_name, "freq_mode", name),
            )
    _set_missing_field_status(
        statuses,
        _public_field_path(receiver_key, "activeSlot"),
        FieldPath.active_slot(receiver_key),
    )
    _set_missing_field_status(
        statuses,
        _public_field_path(receiver_key, "sMeter"),
        FieldPath.receiver(receiver_key, "meters", "s_meter"),
    )
    for name in _RECEIVER_OPERATOR_CONTROL_FIELDS:
        _set_missing_field_status(
            statuses,
            _public_field_path(receiver_key, _receiver_public_key(name)),
            FieldPath.receiver(receiver_key, "operator_controls", name),
        )
    for name in _RECEIVER_OPERATOR_TOGGLE_FIELDS:
        _set_missing_field_status(
            statuses,
            _public_field_path(receiver_key, _receiver_public_key(name)),
            FieldPath.receiver(receiver_key, "operator_toggles", name),
        )
        if name == "dcd":
            # DEPRECATED alias (MOR-466): remove after migration window. Seed the
            # legacy ``sMeterSqlOpen`` public key ``missing`` from the same ``dcd``
            # FieldPath so an absent observation does not resolve to ``available``.
            _set_missing_field_status(
                statuses,
                _public_field_path(receiver_key, "sMeterSqlOpen"),
                FieldPath.receiver(receiver_key, "operator_toggles", "dcd"),
            )
    for name in _RECEIVER_SLOW_STATE_FIELDS:
        _set_missing_field_status(
            statuses,
            _public_field_path(receiver_key, _receiver_public_key(name)),
            FieldPath.receiver(receiver_key, "slow_state", name),
        )


def _default_snapshot_field_status(receiver_count: int) -> dict[str, dict[str, Any]]:
    statuses: dict[str, dict[str, Any]] = {}
    _default_receiver_field_status(statuses, "main")
    if receiver_count >= 2:
        _default_receiver_field_status(statuses, "sub")
    for name in _GLOBAL_TX_FIELDS:
        _set_missing_field_status(
            statuses,
            _to_camel(name),
            FieldPath.global_("tx_state", name),
        )
    for name in _GLOBAL_OPERATOR_CONTROL_FIELDS:
        _set_missing_field_status(
            statuses,
            _to_camel(name),
            FieldPath.global_("operator_controls", name),
        )
    for name in _GLOBAL_METER_FIELDS:
        _set_missing_field_status(
            statuses,
            _to_camel(f"{name}_meter"),
            FieldPath.global_("meters", name),
        )
    for name in _GLOBAL_SLOW_STATE_FIELDS:
        if name == "scope_controls":
            # No observation ever writes ``global.slow_state.scope_controls``
            # (scope-control observations land under
            # ``scope_controls.global.display.*``), so a group-level
            # ``scopeControls`` entry would stay ``missing`` forever and the
            # frontend MOR-429 parent-veto rule would disable every observed
            # ``scopeControls.<leaf>`` (MOR-557). The eight per-leaf entries
            # seeded below are the real gate.
            continue
        _set_missing_field_status(
            statuses,
            _to_camel(name),
            FieldPath.global_("slow_state", name),
        )
    for public_suffix, control_name in _SCOPE_CONTROL_PUBLIC_FIELDS.items():
        _set_missing_field_status(
            statuses,
            f"scopeControls.{public_suffix}",
            FieldPath.scope_control("display", control_name),
        )
    return statuses


def _build_snapshot_field_status(
    snapshot: StateSnapshot,
    *,
    receiver_count: int,
) -> dict[str, dict[str, Any]]:
    statuses = _default_snapshot_field_status(receiver_count)
    for field in snapshot.fields:
        observed_status = _observed_field_status(field)
        for public_path in _snapshot_field_public_paths(field.path):
            previous_at = None
            previous = statuses.get(public_path)
            if previous is not None:
                raw_previous_at = previous.get("lastObservedMonotonic")
                if isinstance(raw_previous_at, (int, float)) and not isinstance(
                    raw_previous_at, bool
                ):
                    previous_at = float(raw_previous_at)
            if (
                previous is not None
                and previous.get("observed") is True
                and previous_at is not None
                and previous_at > field.last_observed_monotonic
            ):
                continue
            statuses[public_path] = dict(observed_status)
    return statuses


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


def _snapshot_receiver_key(receiver_id: str | None) -> str | None:
    if receiver_id is None:
        return None
    return _SNAPSHOT_RECEIVER_IDS.get(receiver_id)


def primary_receiver_snapshot_ids() -> tuple[str, ...]:
    """Return the backend-native receiver-ids for the primary receiver.

    Snapshots key the primary receiver under different ids depending on the
    backend: the legacy Icom state poller uses ``"0"`` while Yaesu CAT and
    rigctld backends use ``"main"``. Both normalize to the canonical public
    key ``"main"`` via :data:`_SNAPSHOT_RECEIVER_IDS`.

    Consumers that need a single freq/mode (e.g. the audio FFT scope center
    frequency) should try these ids in order and use the first one present in
    the snapshot, rather than hardcoding a scheme-specific id.

    The order is significant: ``"0"`` precedes ``"main"`` to preserve the
    historical Icom-first lookup behavior.
    """
    return tuple(
        receiver_id
        for receiver_id, canonical in _SNAPSHOT_RECEIVER_IDS.items()
        if canonical == "main"
    )


def _set_receiver_value(
    state: dict[str, Any],
    receiver_key: str,
    name: str,
    value: Any,
) -> None:
    receiver = state.get(receiver_key)
    if not isinstance(receiver, dict):
        return
    receiver[name] = value


def _apply_snapshot_field(
    state: dict[str, Any],
    path: FieldPath,
    value: Any,
) -> None:
    if path.scope is FieldScope.RECEIVER:
        receiver_key = _snapshot_receiver_key(path.receiver_id)
        if receiver_key is None:
            return
        if path.family is FieldFamily.FREQ_MODE:
            if path.slot in (VfoSlot.A, VfoSlot.B):
                receiver = state.get(receiver_key)
                if not isinstance(receiver, dict):
                    return
                slot_key = "vfo_a" if path.slot is VfoSlot.A else "vfo_b"
                slot_state = receiver.get(slot_key)
                if not isinstance(slot_state, dict):
                    return
                target = _VFO_SLOT_FIELDS.get(path.name)
                if target is not None:
                    slot_state[target] = value
                return
            if path.slot not in (None, VfoSlot.ACTIVE):
                return
            target = _RECEIVER_FREQ_MODE_FIELDS.get(path.name)
            if target is not None:
                _set_receiver_value(state, receiver_key, target, value)
            return
        if path.family is FieldFamily.VFO and path.name == "active_slot":
            _set_receiver_value(state, receiver_key, "active_slot", value)
            return
        if path.family is FieldFamily.METERS and path.name == "s_meter":
            _set_receiver_value(state, receiver_key, "s_meter", value)
            return
        if (
            path.family is FieldFamily.OPERATOR_CONTROLS
            and path.name in _RECEIVER_OPERATOR_CONTROL_FIELDS
        ):
            _set_receiver_value(state, receiver_key, path.name, value)
            return
        if (
            path.family is FieldFamily.OPERATOR_TOGGLES
            and path.name in _RECEIVER_OPERATOR_TOGGLE_FIELDS
        ):
            if path.name == "audio_peak_filter":
                _set_receiver_value(state, receiver_key, "audio_peak_filter", value)
            elif path.name == "twin_peak_filter":
                _set_receiver_value(state, receiver_key, "twin_peak_filter", value)
            elif path.name == "auto_notch":
                _set_receiver_value(state, receiver_key, "auto_notch", value)
            elif path.name == "af_mute":
                _set_receiver_value(state, receiver_key, "af_mute", value)
            elif path.name == "dcd":
                _set_receiver_value(state, receiver_key, "dcd", value)
                # DEPRECATED alias (MOR-466): remove after migration window. Also
                # publish the value under the legacy ``s_meter_sql_open`` key
                # (camel-cased to ``sMeterSqlOpen``) so existing frontend
                # consumers keep working during migration.
                _set_receiver_value(state, receiver_key, "s_meter_sql_open", value)
            else:
                _set_receiver_value(state, receiver_key, path.name, value)
            return
        if (
            path.family is FieldFamily.SLOW_STATE
            and path.name in _RECEIVER_SLOW_STATE_FIELDS
        ):
            _set_receiver_value(state, receiver_key, path.name, value)
            return

    if path.scope is FieldScope.GLOBAL:
        if path.family is FieldFamily.TX_STATE and path.name in _GLOBAL_TX_FIELDS:
            state[path.name] = value
            return
        if (
            path.family is FieldFamily.OPERATOR_CONTROLS
            and path.name in _GLOBAL_OPERATOR_CONTROL_FIELDS
        ):
            state[path.name] = value
            return
        if path.family is FieldFamily.METERS and path.name in _GLOBAL_METER_FIELDS:
            state[f"{path.name}_meter"] = value
            return
        if (
            path.family is FieldFamily.SLOW_STATE
            and path.name in _GLOBAL_SLOW_STATE_FIELDS
        ):
            state[path.name] = value
            return

    if path.scope is FieldScope.SCOPE_CONTROLS and path.family is FieldFamily.DISPLAY:
        scope_controls = state.get("scope_controls")
        if not isinstance(scope_controls, dict):
            return
        if path.receiver_id is not None:
            receiver_key = _snapshot_receiver_key(path.receiver_id)
            if receiver_key == "main":
                scope_controls["receiver"] = 0
            elif receiver_key == "sub":
                scope_controls["receiver"] = 1
        if path.name in _SCOPE_CONTROL_PUBLIC_SUFFIXES:
            scope_controls[path.name] = value


def _project_snapshot_state_dict(snapshot: StateSnapshot) -> dict[str, Any]:
    state = copy.deepcopy(_EMPTY_STATE_DICT)
    for field in snapshot.fields:
        _apply_snapshot_field(state, field.path, field.value)
    return cast(dict[str, Any], state)


def _build_public_state_payload_from_dict(
    state: dict[str, Any],
    *,
    radio: "Radio | None",
    revision: int,
    state_revision: int,
    freshness_revision: int,
    observation_seq: int,
    receiver_count: int,
    updated_at: str | None = None,
    scope_clients: int = 0,
    control_clients: int = 0,
    audio_clients: int = 0,
    radio_health: dict[str, Any] | None = None,
    health_revision: int = 0,
) -> dict[str, Any]:
    state = copy.deepcopy(state)
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
    state["state_revision"] = state_revision
    state["freshness_revision"] = freshness_revision
    state["observation_seq"] = observation_seq
    state["health_revision"] = health_revision
    state["updated_at"] = (
        updated_at or datetime.datetime.now(datetime.timezone.utc).isoformat()
    )
    if receiver_count < 2:
        state.pop("sub", None)

    conn_state_val = getattr(radio, "conn_state", None) if radio else None
    radio_status: str = "disconnected"
    if conn_state_val is not None and hasattr(conn_state_val, "value"):
        raw_val = conn_state_val.value
        if isinstance(raw_val, str):
            radio_status = raw_val
    elif raw_connected:
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


def build_public_state_payload(
    radio_state: RadioState,
    *,
    radio: "Radio | None",
    revision: int,
    state_revision: int | None = None,
    freshness_revision: int = 0,
    observation_seq: int = 0,
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
    return _build_public_state_payload_from_dict(
        radio_state.to_dict(),
        radio=radio,
        revision=revision,
        state_revision=revision if state_revision is None else state_revision,
        freshness_revision=freshness_revision,
        observation_seq=observation_seq,
        receiver_count=receiver_count,
        updated_at=updated_at,
        scope_clients=scope_clients,
        control_clients=control_clients,
        audio_clients=audio_clients,
        radio_health=radio_health,
        health_revision=health_revision,
    )


def build_public_state_payload_from_snapshot(
    snapshot: StateSnapshot,
    *,
    radio: "Radio | None",
    receiver_count: int,
    updated_at: str | None = None,
    scope_clients: int = 0,
    control_clients: int = 0,
    audio_clients: int = 0,
    radio_health: dict[str, Any] | None = None,
    health_revision: int = 0,
) -> dict[str, Any]:
    """Build the public web payload from one StateStore snapshot."""

    state = _project_snapshot_state_dict(snapshot)
    state["field_status"] = _build_snapshot_field_status(
        snapshot,
        receiver_count=receiver_count,
    )
    return _build_public_state_payload_from_dict(
        state,
        radio=radio,
        revision=snapshot.state_revision,
        state_revision=snapshot.state_revision,
        freshness_revision=snapshot.freshness_revision,
        observation_seq=snapshot.observation_seq,
        receiver_count=receiver_count,
        updated_at=updated_at,
        scope_clients=scope_clients,
        control_clients=control_clients,
        audio_clients=audio_clients,
        radio_health=radio_health,
        health_revision=health_revision,
    )
