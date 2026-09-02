"""Shared state query list for populating RadioState.

Used by RadioPoller (periodic polling) and by the one-shot sweep run at
connect (``runtime/radio_initial_state.py: fetch_initial_state``). Each query
keeps its CI-V command, semantic sub-command, payload data, and optional
cmd29 receiver route distinct.
"""

from __future__ import annotations

from dataclasses import replace

from rigplane.commands._frame import decode_wire_tuple, parse_civ_frame
from rigplane.commands.scope import (
    SCOPE_RECEIVER_SELECTOR_SUBS,
    SCOPE_SELECTOR_MAIN,
    get_scope_fixed_edge,
)
from rigplane.core.acquisition_scheduler import (
    AcquisitionQuery,
    AcquisitionQueryResolver,
)
from rigplane.core.state_pipeline_contracts import FieldPath
from rigplane.profiles import RadioProfile

_RECEIVER_IDS = {"0": 0, "main": 0, "1": 1, "sub": 1}
_RECEIVER_TOGGLE_GETTERS = {
    "digisel": "get_digisel",
    "ipplus": "get_ip_plus",
    "nb": "get_nb",
    "nr": "get_nr",
    "auto_notch": "get_auto_notch",
    "manual_notch": "get_manual_notch",
    "twin_peak_filter": "get_twin_peak_filter",
    "repeater_tone": "get_repeater_tone",
    "repeater_tsql": "get_repeater_tsql",
}
_RECEIVER_CONTROL_GETTERS = {
    "af_level": "get_af_level",
    "rf_gain": "get_rf_gain",
    "squelch": "get_squelch",
    "apf_type_level": "get_apf_type_level",
    "nr_level": "get_nr_level",
    "pbt_inner": "get_pbt_inner",
    "pbt_outer": "get_pbt_outer",
    "notch_filter": "get_notch_filter",
    "nb_level": "get_nb_level",
    "digisel_shift": "get_digisel_shift",
    "att": "get_attenuator",
    "preamp": "get_preamp",
    "agc": "get_agc",
    "audio_peak_filter": "get_audio_peak_filter",
    "filter_shape": "get_filter_shape",
    "manual_notch_width": "get_manual_notch_width",
    "agc_time_constant": "get_agc_time_constant",
    "tone_freq": "get_tone_freq",
    "tsql_freq": "get_tsql_freq",
}
_GLOBAL_METER_GETTERS = {
    "power": "get_power_meter",
    "swr": "get_swr",
    "alc": "get_alc",
    "comp": "get_comp_meter",
    "vd": "get_vd_meter",
    "id": "get_id_meter",
}
_GLOBAL_TX_STATE_GETTERS = {
    "ptt": "get_transceiver_status",
    "rit_on": "get_rit_status",
    "rit_tx": "get_rit_tx_status",
    "compressor_on": "get_compressor",
    "monitor_on": "get_monitor",
    "vox_on": "get_vox",
    "split": "get_split",
    "dual_watch": "get_dual_watch",
}
_GLOBAL_CONTROL_GETTERS = {
    "rit_freq": "get_rit_frequency",
    "vox_delay": "get_vox_delay",
    "tuner_status": "get_tuner_status",
    "break_in": "get_break_in",
    "power_level": "get_rf_power",
    "mic_gain": "get_mic_gain",
    "cw_pitch": "get_cw_pitch",
    "key_speed": "get_key_speed",
    "compressor_level": "get_compressor_level",
    "break_in_delay": "get_break_in_delay",
    "drive_gain": "get_drive_gain",
    "monitor_gain": "get_monitor_gain",
    "vox_gain": "get_vox_gain",
    "anti_vox_gain": "get_anti_vox_gain",
}
_SCOPE_CONTROL_GETTERS = {
    "receiver": "get_scope_main_sub",
    "dual": "get_scope_single_dual",
    "mode": "get_scope_mode",
    "span": "get_scope_span",
    "edge": "get_scope_edge",
    "hold": "get_scope_hold",
    "ref_db": "get_scope_ref",
    "speed": "get_scope_speed",
    "during_tx": "get_scope_during_tx",
    "center_type": "get_scope_center_type",
    "vbw_narrow": "get_scope_vbw",
    "fixed_edge": "get_scope_fixed_edge",
    "rbw": "get_scope_rbw",
}


def acquisition_query_from_wire_tuple(
    wire: tuple[int, ...],
    *,
    receiver: int | None = None,
) -> AcquisitionQuery:
    """Decode a command-map wire tuple into a lossless acquisition query."""

    command, sub, data = decode_wire_tuple(wire)
    return AcquisitionQuery(command, sub=sub, data=data, receiver=receiver)


def acquisition_query_resolver_for_profile(
    profile: RadioProfile,
) -> AcquisitionQueryResolver:
    """Bind acquisition FieldPaths to one profile's declared getter bytes."""

    command_map = profile.command_map

    def from_getter(
        getter: str | None,
        *,
        receiver: int | None = None,
    ) -> AcquisitionQuery | None:
        if getter is None or command_map is None or not command_map.has(getter):
            return None
        return acquisition_query_from_wire_tuple(
            command_map.get(getter),
            receiver=receiver,
        )

    def resolve_freq_mode(path: FieldPath, receiver: int) -> AcquisitionQuery | None:
        slot = None if path.slot is None else path.slot.value
        if slot in {"A", "B"} or (slot == "unselected" and receiver != 0):
            return None
        selected = receiver == 0 and slot != "unselected"
        if path.name == "freq_hz":
            getter = "get_selected_freq" if selected else "get_unselected_freq"
        elif path.name in {"mode", "filter_num"}:
            getter = "get_selected_mode" if selected else "get_unselected_mode"
        elif path.name == "filter_width":
            return from_getter(
                "get_filter_width" if slot != "unselected" else None,
                receiver=receiver,
            )
        elif path.name == "data_mode":
            return from_getter(
                "get_data_mode" if slot != "unselected" else None,
                receiver=receiver,
            )
        else:
            getter = None
        return from_getter(getter)

    def resolve(path: FieldPath) -> AcquisitionQuery | None:
        scope = path.scope.value
        family = path.family.value
        if scope == "receiver":
            receiver = _RECEIVER_IDS.get(path.receiver_id or "")
            if receiver is None or not profile.supports_receiver(receiver):
                return None
            if family == "freq_mode":
                return resolve_freq_mode(path, receiver)
            if family == "meters":
                getter = "get_s_meter" if path.name == "s_meter" else None
            elif family == "operator_toggles":
                getter = _RECEIVER_TOGGLE_GETTERS.get(path.name)
            elif family == "operator_controls":
                getter = _RECEIVER_CONTROL_GETTERS.get(path.name)
            else:
                getter = None
            return from_getter(getter, receiver=receiver)
        if scope == "scope_controls":
            getter = _SCOPE_CONTROL_GETTERS.get(path.name)
            query = from_getter(getter)
            if query is None:
                return None
            if getter == "get_scope_fixed_edge":
                frame = parse_civ_frame(
                    get_scope_fixed_edge(
                        0x00,
                        from_addr=0x00,
                        cmd_map=command_map,
                    )
                )
                return AcquisitionQuery(
                    frame.command,
                    sub=frame.sub,
                    data=frame.data,
                )
            if query.command == 0x27 and query.sub in SCOPE_RECEIVER_SELECTOR_SUBS:
                return AcquisitionQuery(
                    query.command,
                    sub=query.sub,
                    data=bytes([SCOPE_SELECTOR_MAIN]) + query.data,
                )
            return query
        if scope != "global":
            return None
        if family == "meters":
            getter = _GLOBAL_METER_GETTERS.get(path.name)
        elif family == "slow_state":
            getter = "get_main_sub_band" if path.name == "active" else None
        elif family == "tx_state":
            getter = _GLOBAL_TX_STATE_GETTERS.get(path.name)
        elif family == "operator_controls":
            getter = _GLOBAL_CONTROL_GETTERS.get(path.name)
        else:
            getter = None
        return from_getter(getter)

    return resolve


def build_state_queries(profile: RadioProfile) -> list[AcquisitionQuery]:
    """Build the profile-declared list of CI-V state queries.

    Parameters
    ----------
    profile:
        Radio profile (model, cmd29 support, receiver count).

    Returns
    -------
    list[AcquisitionQuery]
        Ordered list of lossless acquisition queries.
    """
    acquisition = profile.state_acquisition
    if acquisition is None:
        return []

    resolve = acquisition_query_resolver_for_profile(profile)
    queries: list[AcquisitionQuery] = []
    seen: set[AcquisitionQuery] = set()
    for path in acquisition.pollable_paths():
        query = resolve(path)
        if query is None:
            continue
        if query.receiver is not None and not profile.supports_cmd29(
            query.command, query.sub
        ):
            if query.receiver != 0:
                continue
            query = replace(query, receiver=None)
        if query in seen:
            continue
        seen.add(query)
        queries.append(query)
    return queries
