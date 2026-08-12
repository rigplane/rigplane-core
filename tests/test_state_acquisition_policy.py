"""Capability and acquisition-policy schema for MOR-344."""

from __future__ import annotations

import json
import textwrap
from pathlib import Path
from typing import Any, cast

import pytest

from rigplane.core.state_acquisition_policy import (
    AcquisitionPolicy,
    AdaptiveDecayPolicy,
    FieldAvailability,
    FieldCapability,
    MeterCoalescingPolicy,
    RadioAcquisitionProfile,
)
from rigplane.core.state_pipeline_contracts import FieldPath
from rigplane.profiles import get_radio_profile
from rigplane.rig_loader import RigLoadError, discover_rigs, load_rig

RIGS_DIR = Path(__file__).resolve().parent.parent / "rigs"


def _write_toml(tmp_path: Path, content: str, name: str = "test.toml") -> Path:
    path = tmp_path / name
    path.write_text(textwrap.dedent(content))
    return path


def _minimal_state_acquisition_toml(state_acquisition: str) -> str:
    return f"""
    [radio]
    id = "policy_schema_test"
    model = "POLICY-SCHEMA-TEST"
    civ_addr = 0x94
    receiver_count = 1
    has_lan = true
    has_wifi = false

    [capabilities]
    features = ["audio"]

    [modes]
    list = ["USB"]

    [filters]
    list = ["FIL1"]

    [vfo]
    scheme = "ab"

    {state_acquisition}
    """


def test_radio_acquisition_profile_serializes_capabilities_and_policy() -> None:
    freq = FieldPath.active("main", "freq_mode", "freq_hz")
    s_meter = FieldPath.receiver("main", "meters", "s_meter")
    profile = RadioAcquisitionProfile(
        provider="icom_civ",
        capabilities=(
            FieldCapability(
                path=freq,
                unsolicited_push=True,
                polling=True,
                command_response_observable=True,
                supported_controls=("set_freq",),
            ),
            FieldCapability(path=s_meter, polling=True, stream_like=True),
        ),
        default_policy=AcquisitionPolicy(
            cadence_seconds=2.0,
            freshness_ttl_seconds=6.0,
            reconciliation_priority="unsolicited",
            adaptive_decay=AdaptiveDecayPolicy(
                enabled=True,
                idle_multiplier=4.0,
                max_cadence_seconds=30.0,
            ),
            meter_coalescing=MeterCoalescingPolicy(window_seconds=0.2),
            external_cat_pause="pause_polling",
        ),
    )

    payload = json.loads(json.dumps(profile.to_dict()))
    restored = RadioAcquisitionProfile.from_dict(payload)

    assert restored == profile
    assert restored.capability_for(freq).can_poll is True
    assert restored.capability_for(s_meter).stream_like is True
    assert restored.policy_for(freq).freshness_ttl_seconds == 6.0


def test_invalid_capability_and_policy_combinations_are_rejected() -> None:
    freq = FieldPath.active("main", "freq_mode", "freq_hz")
    meter = FieldPath.receiver("main", "meters", "s_meter")

    with pytest.raises(ValueError, match="unavailable fields cannot be acquired"):
        FieldCapability(
            path=freq,
            availability=FieldAvailability.UNSUPPORTED,
            polling=True,
        )

    with pytest.raises(ValueError, match="stream_like fields must be meters"):
        FieldCapability(path=freq, stream_like=True)

    with pytest.raises(ValueError, match="freshness_ttl_seconds"):
        AcquisitionPolicy(cadence_seconds=5.0, freshness_ttl_seconds=2.0)

    with pytest.raises(ValueError, match="meter_coalescing requires meter fields"):
        RadioAcquisitionProfile(
            provider="icom_civ",
            capabilities=(FieldCapability(path=freq, polling=True),),
            field_policies={
                freq: AcquisitionPolicy(
                    cadence_seconds=1.0,
                    freshness_ttl_seconds=2.0,
                    meter_coalescing=MeterCoalescingPolicy(window_seconds=0.1),
                )
            },
        )

    RadioAcquisitionProfile(
        provider="icom_civ",
        capabilities=(FieldCapability(path=meter, polling=True, stream_like=True),),
        field_policies={
            meter: AcquisitionPolicy(
                cadence_seconds=0.2,
                freshness_ttl_seconds=1.0,
                meter_coalescing=MeterCoalescingPolicy(window_seconds=0.1),
            )
        },
    )


def test_schema_from_dict_rejects_unknown_and_coerced_values() -> None:
    freq = "receiver.main.active.freq_mode.freq_hz"

    with pytest.raises(ValueError, match="unknown keys.*pollin"):
        FieldCapability.from_dict({"path": freq, "pollin": True})

    with pytest.raises(ValueError, match="polling must be a bool"):
        FieldCapability.from_dict({"path": freq, "polling": "false"})

    with pytest.raises(
        ValueError, match="supportedControls must be a sequence of strings"
    ):
        FieldCapability.from_dict({"path": freq, "supportedControls": "set_freq"})

    with pytest.raises(
        ValueError, match="supportedControls must be a sequence of strings"
    ):
        FieldCapability.from_dict({"path": freq, "supportedControls": [1]})

    with pytest.raises(ValueError, match="cadenceSeconds must be a number"):
        AcquisitionPolicy.from_dict({"cadenceSeconds": "1.0"})

    with pytest.raises(ValueError, match="enabled must be a bool"):
        AcquisitionPolicy.from_dict(
            {
                "adaptiveDecay": {
                    "enabled": "false",
                    "idleMultiplier": 2.0,
                }
            }
        )

    with pytest.raises(ValueError, match="provider must be a string"):
        RadioAcquisitionProfile.from_dict({"provider": 123})

    with pytest.raises(ValueError, match="tx_only must be a bool"):
        AcquisitionPolicy(cadence_seconds=1.0, freshness_ttl_seconds=2.0, tx_only="yes")  # type: ignore[arg-type]


def test_acquisition_policy_tx_only_defaults_false_and_round_trips() -> None:
    """MOR-1485: ``tx_only`` defaults false and survives a to_dict/from_dict trip."""

    default_policy = AcquisitionPolicy()
    assert default_policy.tx_only is False

    tx_only_policy = AcquisitionPolicy(
        cadence_seconds=1.0,
        freshness_ttl_seconds=2.0,
        tx_only=True,
    )
    payload = json.loads(json.dumps(tx_only_policy.to_dict()))
    assert payload["txOnly"] is True
    restored = AcquisitionPolicy.from_dict(payload)
    assert restored == tx_only_policy
    assert restored.tx_only is True


def test_field_capability_direct_construction_rejects_coerced_controls() -> None:
    freq = FieldPath.active("main", "freq_mode", "freq_hz")

    with pytest.raises(
        ValueError, match="supported_controls must be a sequence of strings"
    ):
        FieldCapability(path=freq, supported_controls="set_freq")

    with pytest.raises(
        ValueError, match="supported_controls must be a sequence of strings"
    ):
        FieldCapability(path=freq, supported_controls=cast(tuple[str, ...], (123,)))


def test_missing_and_unsupported_capabilities_are_explicitly_unavailable() -> None:
    freq = FieldPath.active("main", "freq_mode", "freq_hz")
    power = FieldPath.global_("tx_state", "power_on")
    profile = RadioAcquisitionProfile(
        provider="external_rigctld",
        capabilities=(
            FieldCapability(
                path=power,
                availability=FieldAvailability.UNSUPPORTED,
                diagnostic="Hamlib model does not expose power control",
            ),
        ),
    )

    missing = profile.capability_for(freq)
    unsupported = profile.capability_for(power)

    assert missing.availability is FieldAvailability.UNKNOWN
    assert missing.can_poll is False
    assert "missing capability metadata" in missing.diagnostic
    assert unsupported.availability is FieldAvailability.UNSUPPORTED
    assert unsupported.can_poll is False
    assert profile.pollable_paths() == ()


def test_loader_parses_x6200_like_tuning_policy_without_delivery_branches(
    tmp_path: Path,
) -> None:
    toml = """
    [radio]
    id = "x6200_like"
    model = "X6200-LIKE"
    civ_addr = 0xA4
    receiver_count = 1
    has_lan = false
    has_wifi = true

    [capabilities]
    features = ["audio", "meters"]

    [modes]
    list = ["USB"]

    [filters]
    list = ["FIL1"]

    [vfo]
    scheme = "ab"

    [[freq_ranges.ranges]]
    label = "HF"
    start_hz = 100000
    end_hz = 54000000

    [commands]
    get_freq = [0x03]
    set_freq = [0x05]
    get_mode = [0x04]
    set_mode = [0x06]
    get_selected_mode = [0x26, 0x00]
    set_selected_mode = [0x26, 0x00]

    [state_acquisition]
    provider = "xiegu_civ"
    default_cadence_seconds = 2.0
    default_freshness_ttl_seconds = 8.0
    default_reconciliation_priority = "poll"
    external_cat_pause = "pause_polling"

    [state_acquisition.capabilities]
    polling_only = [
        "receiver.main.active.freq_mode.freq_hz",
        "receiver.main.active.freq_mode.mode",
    ]
    command_response_observable = [
        "receiver.main.active.freq_mode.freq_hz",
        "receiver.main.active.freq_mode.mode",
    ]
    supported_controls = [
        "receiver.main.active.freq_mode.freq_hz",
        "receiver.main.active.freq_mode.mode",
    ]

    [state_acquisition.field_policies."receiver.main.active.freq_mode.mode"]
    cadence_seconds = 1.0
    freshness_ttl_seconds = 4.0
    reconciliation_priority = "command_response"
    external_cat_pause = "pause_polling"
    """

    profile = load_rig(_write_toml(tmp_path, toml)).to_profile()
    policy = profile.state_acquisition
    mode = FieldPath.active("main", "freq_mode", "mode")

    assert policy is not None
    assert policy.provider == "xiegu_civ"
    assert policy.capability_for(mode).command_response_observable is True
    assert policy.policy_for(mode).reconciliation_priority == "command_response"
    assert profile.set_mode_via_selected is True


def test_loader_parses_tx_only_field_policy_flag(tmp_path: Path) -> None:
    """MOR-1485: ``tx_only = true`` in a field_policies table parses through."""

    toml = _minimal_state_acquisition_toml(
        """
        [state_acquisition]
        provider = "icom_civ"
        default_cadence_seconds = 2.0
        default_freshness_ttl_seconds = 8.0

        [state_acquisition.capabilities]
        polling_only = ["global.meters.power"]

        [state_acquisition.field_policies."global.meters.power"]
        cadence_seconds = 1.0
        freshness_ttl_seconds = 2.0
        tx_only = true
        """
    )

    profile = load_rig(_write_toml(tmp_path, toml)).to_profile()
    policy = profile.state_acquisition
    power = FieldPath.global_("meters", "power")

    assert policy is not None
    assert policy.policy_for(power).tx_only is True
    # A path with no field_policies override must not silently inherit
    # tx_only=True from some other field's override.
    assert policy.default_policy.tx_only is False


def test_loader_rejects_polling_unsupported_fields(tmp_path: Path) -> None:
    toml = """
    [radio]
    id = "bad_policy"
    model = "BAD-POLICY"
    civ_addr = 0x94
    receiver_count = 1
    has_lan = true
    has_wifi = false

    [capabilities]
    features = ["audio"]

    [modes]
    list = ["USB"]

    [filters]
    list = ["FIL1"]

    [vfo]
    scheme = "ab"

    [state_acquisition.capabilities]
    polling_only = ["global.tx_state.power_on"]
    unsupported = ["global.tx_state.power_on"]
    """

    with pytest.raises(RigLoadError, match="global.tx_state.power_on"):
        load_rig(_write_toml(tmp_path, toml))


def test_loader_rejects_unknown_state_acquisition_keys(
    tmp_path: Path,
) -> None:
    cases: tuple[tuple[str, str], ...] = (
        (
            """
            [state_acquisition]
            provider = "profile"
            default_cadence_seconds = 1.0
            default_freshness_ttl_seconds = 3.0
            cadance_seconds = 99.0
            """,
            r"\[state_acquisition\].*unknown key.*cadance_seconds",
        ),
        (
            """
            [state_acquisition.capabilities]
            polling = ["receiver.main.active.freq_mode.freq_hz"]
            """,
            r"\[state_acquisition.capabilities\].*unknown key.*polling",
        ),
        (
            """
            [state_acquisition.field_policies."receiver.main.active.freq_mode.freq_hz"]
            cadance_seconds = 99.0
            """,
            r"\[state_acquisition.field_policies.receiver.main.active.freq_mode.freq_hz\].*unknown key.*cadance_seconds",
        ),
    )

    for index, (state_acquisition, message) in enumerate(cases):
        with pytest.raises(RigLoadError, match=message):
            load_rig(
                _write_toml(
                    tmp_path,
                    _minimal_state_acquisition_toml(state_acquisition),
                    name=f"unknown-{index}.toml",
                )
            )


def test_loader_rejects_coerced_state_acquisition_values(
    tmp_path: Path,
) -> None:
    cases: tuple[tuple[str, str], ...] = (
        (
            """
            [state_acquisition]
            adaptive_decay = "false"
            adaptive_decay_idle_multiplier = 2.0
            """,
            r"\[state_acquisition\].*adaptive_decay must be a bool",
        ),
        (
            """
            [state_acquisition]
            default_cadence_seconds = "1.0"
            """,
            r"\[state_acquisition\].*default_cadence_seconds must be a number",
        ),
        (
            """
            [state_acquisition.field_policies."receiver.main.active.freq_mode.freq_hz"]
            cadence_seconds = "1.0"
            """,
            r"\[state_acquisition.field_policies.receiver.main.active.freq_mode.freq_hz\].*cadence_seconds must be a number",
        ),
    )

    for index, (state_acquisition, message) in enumerate(cases):
        with pytest.raises(RigLoadError, match=message):
            load_rig(
                _write_toml(
                    tmp_path,
                    _minimal_state_acquisition_toml(state_acquisition),
                    name=f"coerced-{index}.toml",
                )
            )


def test_known_profiles_load_with_state_acquisition_compatibility() -> None:
    profiles = {rig.model: rig.to_profile() for rig in discover_rigs(RIGS_DIR).values()}

    for expected in ("IC-7300", "IC-7610", "FTX-1", "X6200"):
        assert expected in profiles
        assert profiles[expected].state_acquisition is not None

    x6200 = get_radio_profile("X6200")
    mode = FieldPath.active("main", "freq_mode", "mode")
    assert x6200.state_acquisition is not None
    assert x6200.state_acquisition.policy_for(mode).reconciliation_priority == (
        "command_response"
    )


def test_known_profiles_stream_like_meters_use_fast_non_decaying_policies() -> None:
    for model in ("IC-7300", "IC-7610", "FTX-1", "X6200"):
        profile = get_radio_profile(model)
        acquisition = profile.state_acquisition
        assert acquisition is not None
        assert acquisition.default_policy.meter_coalescing is not None

        stream_like = tuple(
            capability
            for capability in acquisition.capabilities
            if capability.stream_like
        )
        assert stream_like

        for capability in stream_like:
            policy = acquisition.policy_for(capability.path)
            assert policy.cadence_seconds is not None
            assert policy.cadence_seconds <= 0.25
            assert policy.freshness_ttl_seconds is not None
            # MOR-334 (s_meter stuck-low): a streaming meter's freshness TTL must
            # EXCEED its own cadence so the field stays FRESH between live
            # arrivals (otherwise it decays to stale between every sample and
            # floors to S0 on the LCD layout). It must also stay fast-expiring
            # relative to slow controls so a genuinely stopped stream is detected
            # promptly. Bound it well below the slow-control TTLs (>= 8.0s) while
            # comfortably above the streaming cadence.
            assert policy.cadence_seconds < policy.freshness_ttl_seconds
            assert policy.freshness_ttl_seconds <= 2.0
            assert policy.adaptive_decay.enabled is False
            assert policy.meter_coalescing is not None
            assert (
                policy.meter_coalescing.window_seconds
                == acquisition.default_policy.meter_coalescing.window_seconds
            )


def test_ftx1_profile_declares_slow_control_policies_for_polling_adapter() -> None:
    ftx1 = get_radio_profile("FTX-1")
    assert ftx1.state_acquisition is not None

    af_level = FieldPath.receiver("main", "operator_controls", "af_level")
    squelch = FieldPath.receiver("sub", "operator_controls", "squelch")
    ptt = FieldPath.global_("tx_state", "ptt")

    assert ftx1.state_acquisition.capability_for(af_level).can_poll is True
    assert ftx1.state_acquisition.capability_for(squelch).can_poll is True
    assert ftx1.state_acquisition.capability_for(ptt).can_poll is True
    assert ftx1.state_acquisition.policy_for(af_level).freshness_ttl_seconds == 120.0
    assert ftx1.state_acquisition.policy_for(af_level).cadence_seconds == 30.0


def test_ic7300_profile_enrolls_exact_supported_observation_rows() -> None:
    profile = get_radio_profile("IC-7300")
    acquisition = profile.state_acquisition
    assert acquisition is not None
    assert acquisition.provider == "icom_civ"

    expected_pollable = {
        FieldPath.active("main", "freq_mode", "freq_hz"),
        FieldPath.active("main", "freq_mode", "mode"),
        FieldPath.unselected("main", "freq_mode", "freq_hz"),
        FieldPath.unselected("main", "freq_mode", "mode"),
        FieldPath.receiver("main", "meters", "s_meter"),
        FieldPath.receiver("main", "operator_controls", "af_level"),
        FieldPath.receiver("main", "operator_controls", "rf_gain"),
        FieldPath.receiver("main", "operator_controls", "squelch"),
        FieldPath.receiver("main", "operator_controls", "att"),
        FieldPath.receiver("main", "operator_controls", "preamp"),
        FieldPath.receiver("main", "operator_controls", "agc"),
        FieldPath.receiver("main", "operator_toggles", "nb"),
        FieldPath.receiver("main", "operator_toggles", "nr"),
        FieldPath.global_("operator_controls", "power_level"),
        FieldPath.global_("tx_state", "compressor_on"),
        FieldPath.global_("operator_controls", "compressor_level"),
        FieldPath.global_("tx_state", "ptt"),
        FieldPath.global_("operator_controls", "tuner_status"),
        FieldPath.global_("tx_state", "split"),
        # MOR-1452: documented-readable 0x14 sub-commands (mic/monitor/VOX/
        # anti-VOX gain, docs/validation/cat-audits/ic7300.md) added to the
        # slow poll tier so the TX-aux panel stops showing a permanent "?".
        FieldPath.global_("operator_controls", "mic_gain"),
        FieldPath.global_("operator_controls", "monitor_gain"),
        FieldPath.global_("operator_controls", "vox_gain"),
        FieldPath.global_("operator_controls", "anti_vox_gain"),
        # MOR-1485: TX/PA meters. Statically pollable (the capability layer
        # doesn't know about runtime TX/RX gating) even though power/swr/alc/
        # comp only actually fire while ``tx_only`` reads true at runtime —
        # see the dedicated tx_only-partitioned demand assertion below.
        FieldPath.global_("meters", "power"),
        FieldPath.global_("meters", "swr"),
        FieldPath.global_("meters", "alc"),
        FieldPath.global_("meters", "comp"),
        FieldPath.global_("meters", "vd"),
        FieldPath.global_("meters", "id"),
    }

    assert set(acquisition.pollable_paths()) == expected_pollable
    assert all(
        acquisition.capability_for(path).availability is FieldAvailability.SUPPORTED
        for path in expected_pollable
    )
    for path in (
        FieldPath.global_("operator_controls", "power_level"),
        FieldPath.global_("tx_state", "compressor_on"),
        FieldPath.global_("operator_controls", "compressor_level"),
    ):
        assert acquisition.capability_for(path).command_response_observable is True

    # MOR-1452 (review fix): the 4 newly-polled fields sit at 15.0s/25.0s, NOT
    # IC-7610's 3.0s/5.0s for the same fields (rigs/ic7610.toml) — IC-7610 is
    # LAN, IC-7300 is serial and shares one ~20 q/s software floor across
    # every poll, operator command, and keep-alive on the same lane (see the
    # serial-budget assertion below for the exact arithmetic that rules out
    # 3.0s here).
    for path in (
        FieldPath.global_("operator_controls", "mic_gain"),
        FieldPath.global_("operator_controls", "monitor_gain"),
        FieldPath.global_("operator_controls", "vox_gain"),
        FieldPath.global_("operator_controls", "anti_vox_gain"),
    ):
        policy = acquisition.policy_for(path)
        assert policy.cadence_seconds == 15.0
        assert policy.freshness_ttl_seconds == 25.0

    fast_tier_ttl = acquisition.policy_for(
        FieldPath.active("main", "freq_mode", "freq_hz")
    ).freshness_ttl_seconds
    assert fast_tier_ttl == acquisition.default_policy.freshness_ttl_seconds
    assert fast_tier_ttl < 25.0

    # MOR-1452 (review fix): pin the actual serial-lane arithmetic, not just
    # the cadence numbers, so a future "just speed this field up a bit" edit
    # cannot silently blow the shared IC-7300 serial budget again. The
    # software floor is one CI-V frame per _SERIAL_DEFAULT_CIV_MIN_INTERVAL_MS
    # (50ms => 20 transactions/s), shared by every poll, every operator
    # command, AND the keep-alive — polling demand alone must leave headroom
    # under that ceiling, not just avoid exceeding it exactly.
    from rigplane.backends._icom_serial_base import (
        _SERIAL_DEFAULT_CIV_MIN_INTERVAL_MS,
    )

    serial_ceiling_hz = 1000.0 / _SERIAL_DEFAULT_CIV_MIN_INTERVAL_MS
    assert serial_ceiling_hz == 20.0

    # MOR-1485: TX/PA meters split the pollable set into an always-on
    # (RX-state) share and a tx_only share that AcquisitionScheduler.
    # due_requests only ever queries while PTT reads true (see
    # test_acquisition_scheduler.py's due_polling_*tx_only* coverage for the
    # gating behavior itself). The RX-state share is what MOR-1484's live
    # medians (s_meter/ptt/freq) were measured against, so THAT figure is the
    # one that must stay near the pre-existing ~19.933 q/s baseline -- not
    # the transient TX-window total.
    rx_state_demand_hz = sum(
        1.0 / acquisition.policy_for(path).cadence_seconds
        for path in acquisition.pollable_paths()
        if not acquisition.policy_for(path).tx_only
    )
    tx_only_demand_hz = sum(
        1.0 / acquisition.policy_for(path).cadence_seconds
        for path in acquisition.pollable_paths()
        if acquisition.policy_for(path).tx_only
    )
    # Pre-existing baseline (MOR-1452) was 19.933 q/s, already only 0.067 q/s
    # below the 20 q/s ceiling. MOR-1485 adds only Vd/Id (2 fields / 60.0s =
    # 0.033 q/s) to the always-on RX-state total, spending under half of that
    # remaining headroom (19.933 -> 19.967 q/s) and staying BELOW the
    # ceiling — required, not just desirable, since exceeding it would
    # necessarily slow every other polled field's real throughput and risk
    # regressing the MOR-1484 medians this profile is measured against.
    assert rx_state_demand_hz == pytest.approx(19.967, abs=0.001)
    assert rx_state_demand_hz < serial_ceiling_hz
    # Po/SWR/ALC/COMP: 4 fields / 1.0s = 4.0 q/s, ONLY while tx_only gating
    # lets them through (PTT observed true) — a transient TX-window cost, not
    # a steady-state one.
    assert tx_only_demand_hz == pytest.approx(4.0, abs=0.001)
    total_during_tx_hz = rx_state_demand_hz + tx_only_demand_hz
    assert total_during_tx_hz == pytest.approx(23.967, abs=0.001)

    assert (
        acquisition.capability_for(
            FieldPath.receiver("sub", "operator_controls", "af_level")
        ).availability
        is FieldAvailability.UNKNOWN
    )
    assert profile.cmd29_routes == frozenset()


# Ingress mappings that live as hardcoded command/sub branches in
# ``_observations_from_frame`` rather than one of its lookup dicts: rit_freq/
# rit_on/rit_tx (cmd 0x21), agc_time_constant (cmd 0x1A sub 0x04),
# filter_width (cmd 0x1A sub 0x03, profile-dependent decode), cw_pitch/
# key_speed (cmd 0x14, non-linear decode helpers), and vox_delay (cmd 0x1A
# sub 0x05, 2-byte ctl-mem prefix rather than a plain sub byte). Kept as an
# explicit set -- not derivable from a dict import -- because the production
# code itself is branch-shaped there, not table-shaped; extend this set if a
# future field adds another such branch.
_HARDCODED_OBSERVABLE_FIELDS = {
    ("global", "operator_controls", "rit_freq"),
    ("global", "tx_state", "rit_on"),
    ("global", "tx_state", "rit_tx"),
    ("receiver", "operator_controls", "agc_time_constant"),
    ("receiver", "freq_mode", "filter_width"),
    ("global", "operator_controls", "cw_pitch"),
    ("global", "operator_controls", "key_speed"),
    ("global", "operator_controls", "vox_delay"),
}

# Minimal, per-field synthetic response payload for the round-trip probe
# below. For vox_delay this is APPENDED after the 2-byte ctl-mem prefix the
# query itself supplies (see ``_round_trip_observes``); for every other
# field it is the frame's entire ``data``. Values are chosen only to satisfy
# each branch's own length/shape checks (e.g. "at least 3 bytes", "at least
# 2 bytes") -- the decoded VALUE is never asserted, only that a matching
# observation is produced at all.
_HARDCODED_SYNTHETIC_PAYLOAD: dict[str, bytes] = {
    "rit_freq": b"\x00\x00\x00",
    "rit_on": b"\x01",
    "rit_tx": b"\x01",
    "agc_time_constant": b"\x00",
    "filter_width": b"\x01",
    "cw_pitch": b"\x00\x00",
    "key_speed": b"\x00\x00",
    "vox_delay": b"\x00",
}


def _table_backed_ingress_spec(command: int, sub: int) -> tuple[str, str, str] | None:
    """Resolve the ingress table entry AT this exact (command, sub) key.

    Mirrors ``_civ_rx._observations_from_frame``'s own table selection --
    looked up by the SAME sub key the query uses, not scanned out of a union
    of every table's values. That distinction matters: a transposed ingress
    key (e.g. filter_shape accidentally keyed under 0x55 instead of the real
    0x56) still leaves the correct spec present *somewhere* in a
    ``set(table.values())`` union, so a plain membership check against that
    union cannot catch it. A keyed ``.get(sub)`` lookup can: it fails (or
    resolves to the WRONG spec) the moment the query's sub and the ingress
    table's key disagree.
    """

    from rigplane.runtime import _civ_rx

    if command == 0x14:
        return _civ_rx._OBSERVABLE_CMD14_FIELDS.get(sub)
    if command == 0x15:
        return _civ_rx._OBSERVABLE_CMD15_FIELDS.get(sub)
    if command == 0x16:
        bool_spec = _civ_rx._OBSERVABLE_CMD16_FIELDS.get(sub)
        if bool_spec is not None:
            return bool_spec
        value_entry = _civ_rx._OBSERVABLE_CMD16_VALUE_FIELDS.get(sub)
        return None if value_entry is None else value_entry[0]
    if command == 0x1B:
        return _civ_rx._OBSERVABLE_CMD1B_FIELDS.get(sub)
    return None


def _round_trip_observes(
    radio: Any,
    path: FieldPath,
    query: tuple[int, int | bytes | None, int | None],
) -> None:
    """Push a synthetic response for ``query`` through the REAL ingress
    decode and assert ``path`` comes out the other side.

    Unlike a static table/set lookup, this exercises the actual branch in
    ``_observations_from_frame`` end to end: it fails just as loudly whether
    the bug is a wrong sub byte, a wrong ctl-mem prefix (vox_delay), or a
    branch condition that silently doesn't match the exact bytes
    ``IcomCivAcquisitionExecutor.query_for_path`` sends -- there is no
    static structure left to reason about that a value-set membership check
    could paper over.
    """

    from rigplane.core.acquisition_scheduler import split_ctl_mem_sub
    from rigplane.commands import CONTROLLER_ADDR
    from rigplane.types import CivFrame

    command, sub_raw, receiver = query
    civ_sub, prefix = split_ctl_mem_sub(sub_raw)
    payload = _HARDCODED_SYNTHETIC_PAYLOAD[path.name]
    frame = CivFrame(
        to_addr=CONTROLLER_ADDR,
        from_addr=0x94,  # IC-7300's civ_addr (rigs/ic7300.toml)
        command=command,
        sub=civ_sub,
        data=prefix + payload,
        receiver=receiver,
    )

    observations = radio._civ_runtime._observations_from_frame(frame)  # noqa: SLF001
    expected = (path.scope.value, path.family.value, path.name)
    matched = [
        observation
        for observation in observations
        if (
            observation.path.scope.value,
            observation.path.family.value,
            observation.path.name,
        )
        == expected
    ]
    assert matched, (
        f"{path}: synthetic response (command=0x{command:02x}, sub={civ_sub!r}, "
        f"data={(prefix + payload).hex()}) produced no matching observation -- "
        "either the ingress branch doesn't match what query_for_path actually "
        "sends, or a primed read for this path would never leave UNKNOWN"
    )


def test_ic7300_non_polling_field_policies_have_full_acquisition_chain() -> None:
    """MOR-1483/1491/1492/1493 (field-policy membership wave) guard.

    ``AcquisitionScheduler.prime_unobserved`` (MOR-1490) only has an effect on
    a ``field_policies`` path whose capability is NOT pollable -- that is
    exactly the shape a "poll membership" addition riding the priming
    mechanism takes (see the mechanism's own docstring). A path added there
    without a matching CI-V query mapping head-of-line-starves the burst cap
    forever (``no_civ_query_mapping``); a path added without an ingress
    observation mapping primes silently and never actually leaves ``UNKNOWN``.

    This iterates the *real*, currently-loaded IC-7300 profile's
    ``field_policies`` table generically -- filtered to non-polling entries,
    a structural property, not a hardcoded list of field names -- so any
    future membership addition of this shape is caught by this same test
    without editing it.

    MOR-1501 (#2430 note) hardened the ingress check itself: the previous
    version only asserted the query's (scope, family, name) triple appeared
    SOMEWHERE in the union of every ingress table's values, which cannot
    catch a query-sub / ingress-key mismatch (e.g. a transposed 0x56 -> 0x55)
    as long as the triple is still present at some OTHER key. Table-backed
    fields are now checked by keyed lookup at the query's own sub byte;
    branch-shaped fields (rit_freq/rit_on/rit_tx, agc_time_constant,
    filter_width, cw_pitch, key_speed, vox_delay) are checked with an actual
    round trip through ``_observations_from_frame`` using a synthetic
    response built from the query's own command/sub/prefix bytes -- a
    transposed sub or a wrong ctl-mem prefix both make the corresponding
    branch condition fail to match, producing zero observations and a red
    test either way.
    """

    from rigplane.core.acquisition_scheduler import IcomCivAcquisitionExecutor
    from rigplane.radio import IcomRadio

    profile = get_radio_profile("IC-7300")
    acquisition = profile.state_acquisition
    assert acquisition is not None

    async def _noop_send(
        command: int, sub: int | bytes | None, receiver: int | None
    ) -> None:
        return None

    executor = IcomCivAcquisitionExecutor(_noop_send)
    radio = IcomRadio(host="192.168.1.100", model="IC-7300")

    non_polling_paths = [
        path
        for path in acquisition.field_policies
        if not acquisition.capability_for(path).can_poll
    ]
    # This membership wave is the reason any non-polling field_policies entry
    # exists on IC-7300 at all -- guard against the set silently going empty
    # again (e.g. a future edit moves everything back into polling_only and
    # stops exercising prime_unobserved on this profile).
    assert non_polling_paths

    for path in non_polling_paths:
        capability = acquisition.capability_for(path)
        assert capability.command_response_observable or capability.unsolicited_push, (
            f"{path}: non-polling field_policies entry with no acquisition hook"
        )

        query = executor.query_for_path(path)
        assert query is not None, (
            f"{path}: no CI-V query mapping (no_civ_query_mapping) -- "
            "prime_unobserved would head-of-line-starve the burst cap on this"
        )

        key = (path.scope.value, path.family.value, path.name)
        if key in _HARDCODED_OBSERVABLE_FIELDS:
            _round_trip_observes(radio, path, query)
            continue

        command, sub = query[0], query[1]
        assert isinstance(sub, int), (
            f"{path}: table-backed field must query with a plain int sub, "
            f"got {sub!r} -- extend _HARDCODED_OBSERVABLE_FIELDS if this is "
            "actually a new branch-shaped ingress mapping"
        )
        resolved = _table_backed_ingress_spec(command, sub)
        assert resolved == key, (
            f"{path}: query sends command=0x{command:02x} sub=0x{sub:02x}, "
            f"but the ingress table at that exact key resolves to {resolved} "
            f"instead of {key} -- a primed read for this path would never "
            "leave UNKNOWN"
        )


def test_ic7300_activation_does_not_change_ftx1_acquisition_contract() -> None:
    ftx1 = get_radio_profile("FTX-1")
    acquisition = ftx1.state_acquisition
    assert acquisition is not None

    assert acquisition.provider == "yaesu_cat"
    assert len(acquisition.capabilities) == 53
    assert len(acquisition.field_policies) == 46
    assert acquisition.default_policy.cadence_seconds == 2.0
    assert acquisition.default_policy.freshness_ttl_seconds == 8.0
