"""Shared state query list for populating RadioState.

Used by RadioPoller (periodic polling) and by the one-shot sweep run at
connect (``runtime/radio_initial_state.py: fetch_initial_state``). Each query
keeps its CI-V command, semantic sub-command, payload data, and optional
cmd29 receiver route distinct.
"""

from __future__ import annotations

import logging

from rigplane.commands._frame import decode_wire_tuple
from rigplane.commands.scope import (
    SCOPE_RECEIVER_SELECTOR_SUBS,
    SCOPE_SELECTOR_MAIN,
)
from rigplane.core.acquisition_scheduler import (
    AcquisitionQuery,
    AcquisitionQueryResolver,
)
from rigplane.core.state_pipeline_contracts import FieldPath
from rigplane.profiles import RadioProfile

logger = logging.getLogger(__name__)

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


def build_state_queries(
    profile: RadioProfile,
    capabilities: set[str],
    *,
    is_serial: bool = False,
) -> list[AcquisitionQuery]:
    """Build the full list of CI-V state queries for the given profile.

    Parameters
    ----------
    profile:
        Radio profile (model, cmd29 support, receiver count).
    capabilities:
        Set of capability strings the radio exposes.
    is_serial:
        True for serial backends (adds extra meter queries).

    Returns
    -------
    list[AcquisitionQuery]
        Ordered list of lossless acquisition queries.
    """
    receivers = [0]
    if profile.receiver_count > 1:
        receivers.append(1)

    queries: list[AcquisitionQuery] = []

    for receiver in receivers:
        # Freq/mode — needed even on serial for initial state and to pick up
        # filter/attenuator/preamp that don't come via transceive.
        queries.append(AcquisitionQuery(0x25, data=bytes([receiver])))  # frequency
        queries.append(AcquisitionQuery(0x26, data=bytes([receiver])))  # mode
        if receiver == 0 and profile.vfo_readback == "selected_unselected":
            queries.append(AcquisitionQuery(0x25, data=b"\x01"))
            queries.append(AcquisitionQuery(0x26, data=b"\x01"))

        # Per-receiver state queries.  On dual-receiver radios these use
        # cmd29 wrapping.  On single-receiver radios without cmd29 we send
        # plain CI-V queries (receiver=None).
        _PER_RX_QUERIES: list[tuple[str, int, int | None]] = [
            ("attenuator", 0x11, None),
            ("af_level", 0x14, 0x01),
            ("rf_gain", 0x14, 0x02),
            ("squelch", 0x14, 0x03),
            ("preamp", 0x16, 0x02),
            ("nb", 0x16, 0x22),
            ("nr", 0x16, 0x40),
            ("digisel", 0x16, 0x4E),
            ("ip_plus", 0x16, 0x65),
            ("repeater_tone", 0x16, 0x42),
            ("tsql", 0x16, 0x43),
            ("repeater_tone", 0x1B, 0x00),  # Tone frequency
            ("tsql", 0x1B, 0x01),  # TSQL frequency
            ("nr", 0x14, 0x06),  # NR Level
            ("nb", 0x14, 0x12),  # NB Level
            ("notch", 0x14, 0x0D),  # Notch position
            ("filter_width", 0x1A, 0x03),
            ("pbt", 0x14, 0x07),  # PBT Inner
            ("pbt", 0x14, 0x08),  # PBT Outer
            ("notch", 0x16, 0x57),  # Manual notch width
            ("squelch", 0x15, 0x01),  # S-meter squelch status
        ]
        for cap, cmd_byte, sub_byte in _PER_RX_QUERIES:
            if cap not in capabilities:
                logger.debug(
                    "Skipping %s: capability '%s' not supported by %s",
                    f"query 0x{cmd_byte:02X}/0x{sub_byte:02X}"
                    if sub_byte is not None
                    else f"query 0x{cmd_byte:02X}",
                    cap,
                    profile.model,
                )
                continue
            if profile.supports_cmd29(cmd_byte, sub_byte):
                # Dual-receiver: cmd29-wrapped with receiver byte
                queries.append(
                    AcquisitionQuery(cmd_byte, sub=sub_byte, receiver=receiver)
                )
            elif receiver == 0:
                # Single-receiver: plain CI-V query (only once, not per-rx)
                queries.append(AcquisitionQuery(cmd_byte, sub=sub_byte))

        # Per-receiver feature queries that use cmd29 wrapping.
        # Added for any radio whose profile declares cmd29 support for these.
        for cmd_byte, sub_byte in (
            (0x16, 0x12),  # AGC mode
            (0x16, 0x32),  # Audio peak filter
            (0x16, 0x41),  # Auto notch
            (0x16, 0x48),  # Manual notch
            (0x16, 0x4F),  # Twin peak filter
            (0x16, 0x56),  # Filter shape
            (0x1A, 0x04),  # AGC time constant
        ):
            if profile.supports_cmd29(cmd_byte, sub_byte):
                queries.append(
                    AcquisitionQuery(cmd_byte, sub=sub_byte, receiver=receiver)
                )

    # Global queries (not per-receiver)
    queries.extend(
        [
            AcquisitionQuery(0x18),  # Power status (on/off)
            AcquisitionQuery(0x1C, sub=0x00),  # PTT (global)
            AcquisitionQuery(0x1C, sub=0x01),  # Tuner/ATU status
            AcquisitionQuery(0x1C, sub=0x03),  # TX frequency monitor
            AcquisitionQuery(0x14, sub=0x0A),  # Power level (global)
            AcquisitionQuery(0x14, sub=0x0B),  # Mic gain (global)
            AcquisitionQuery(0x14, sub=0x0E),  # Compressor level (global)
            AcquisitionQuery(0x14, sub=0x15),  # Monitor gain (global)
            AcquisitionQuery(0x14, sub=0x09),  # CW pitch (global)
            AcquisitionQuery(0x14, sub=0x0C),  # Key speed (global)
            AcquisitionQuery(0x0F),  # Split (global)
            AcquisitionQuery(0x21, sub=0x00),  # RIT frequency
            AcquisitionQuery(0x21, sub=0x01),  # RIT status
            AcquisitionQuery(0x21, sub=0x02),  # RIT TX status
        ]
    )
    if profile.receiver_count > 1:
        queries.append(AcquisitionQuery(0x07, data=b"\xd2"))
    if "dual_watch" in capabilities:
        queries.append(
            acquisition_query_from_wire_tuple(profile.command_map.get("get_dual_watch"))
        )

    # Common feature queries (data-driven: if radio has the command, poll it)
    _COMMON_FEATURE_QUERIES: list[tuple[int, int]] = [
        (0x16, 0x44),  # Compressor status
        (0x16, 0x45),  # Monitor status
        (0x16, 0x46),  # VOX status
        (0x16, 0x47),  # Break-in mode
        (0x16, 0x50),  # Dial lock status
        (0x14, 0x16),  # VOX gain
        (0x14, 0x17),  # Anti-VOX gain
        (0x14, 0x0F),  # Break-in delay
    ]
    # NOTE: Antenna status (0x12) is NOT polled.
    # CI-V 0x12 sub-commands are SET-only on IC-7610 (0x12 0x00 = select
    # ANT1, 0x12 0x01 = select ANT2).  Polling them would toggle the
    # antenna every cycle.
    if not profile.supports_cmd29(0x16, 0x12):
        _COMMON_FEATURE_QUERIES.insert(0, (0x16, 0x12))  # AGC mode

    # For serial: ALC/comp/VD/Id meters move to slow state queries
    if is_serial:
        _COMMON_FEATURE_QUERIES.extend(
            [
                (0x15, 0x13),  # ALC meter
                (0x15, 0x14),  # Compressor meter
                (0x15, 0x15),  # VD (voltage)
                (0x15, 0x16),  # Id (PA drain current)
            ]
        )

    for cmd, sub in _COMMON_FEATURE_QUERIES:
        queries.append(AcquisitionQuery(cmd, sub=sub))

    # Capability-gated optional queries
    if "meters" in capabilities:
        queries.append(AcquisitionQuery(0x15, sub=0x07))  # Overflow status
    if "ssb_tx_bw" in capabilities:
        queries.append(AcquisitionQuery(0x16, sub=0x58))  # SSB TX bandwidth
    if "scope" in capabilities:
        # 0x27 reads come in two shapes.  The sub-commands in
        # SCOPE_RECEIVER_SELECTOR_SUBS carry a one-byte Main/Sub scope
        # selector; the rest must go out bare, where the same byte would be
        # read as the first data byte of a WRITE (see commands/scope.py).
        #
        # SCOPE_SELECTOR_MAIN is what this list can legitimately name: it is
        # built at connect, before any 0x27 0x12 response has said which
        # scope the radio has selected, and MAIN is the value every profile
        # accepts -- on a single-scope radio it is the only legal one, and
        # per-model selector ranges are MOR-1988.  A sender that does know
        # the live selection substitutes it
        # (web/radio_poller.py: RadioPoller._send_one_state_query).
        for scope_sub in (
            0x12,  # Scope receiver selection
            0x13,  # Scope single/dual mode
            0x14,  # Scope mode (center/fixed)
            0x15,  # Scope span
            0x16,  # Scope edge number
            0x17,  # Scope hold
            0x19,  # Scope REF level
            0x1A,  # Scope sweep speed
            0x1B,  # Scope during TX
            0x1C,  # Scope center type
            0x1D,  # Scope VBW
            0x1F,  # Scope RBW
        ):
            if scope_sub in SCOPE_RECEIVER_SELECTOR_SUBS:
                queries.append(
                    AcquisitionQuery(
                        0x27,
                        sub=scope_sub,
                        data=bytes([SCOPE_SELECTOR_MAIN]),
                    )
                )
            else:
                queries.append(AcquisitionQuery(0x27, sub=scope_sub))

    return queries
