"""Capability and acquisition-policy schema for MOR-344."""

from __future__ import annotations

import json
import textwrap
from pathlib import Path
from typing import cast

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

    with pytest.raises(ValueError, match="supportedControls must be a sequence of strings"):
        FieldCapability.from_dict({"path": freq, "supportedControls": "set_freq"})

    with pytest.raises(ValueError, match="supportedControls must be a sequence of strings"):
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


def test_field_capability_direct_construction_rejects_coerced_controls() -> None:
    freq = FieldPath.active("main", "freq_mode", "freq_hz")

    with pytest.raises(ValueError, match="supported_controls must be a sequence of strings"):
        FieldCapability(path=freq, supported_controls="set_freq")

    with pytest.raises(ValueError, match="supported_controls must be a sequence of strings"):
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

    for expected in ("IC-7610", "FTX-1", "X6200"):
        assert expected in profiles
        assert profiles[expected].state_acquisition is not None

    x6200 = get_radio_profile("X6200")
    mode = FieldPath.active("main", "freq_mode", "mode")
    assert x6200.state_acquisition is not None
    assert x6200.state_acquisition.policy_for(mode).reconciliation_priority == (
        "command_response"
    )


def test_known_profiles_stream_like_meters_use_fast_non_decaying_policies() -> None:
    for model in ("IC-7610", "FTX-1", "X6200"):
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
            assert policy.freshness_ttl_seconds <= 1.0
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
    assert (
        ftx1.state_acquisition.policy_for(af_level).freshness_ttl_seconds == 120.0
    )
    assert ftx1.state_acquisition.policy_for(af_level).cadence_seconds == 30.0
