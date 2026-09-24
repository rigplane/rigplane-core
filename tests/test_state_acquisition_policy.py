"""Capability and acquisition-policy schema for MOR-344."""

from __future__ import annotations

import json
import textwrap
from collections import Counter
from pathlib import Path
from typing import Any, cast

import pytest

from rigplane.core.acquisition_scheduler import (
    AcquisitionScheduler,
    FRESHNESS_DECAY_LIMIT_RATIO,
    StateFreshnessService,
    adaptive_cadence_limit,
    provider_uses_civ_acquisition,
)
from rigplane.core.observation_adapter import ProviderObservationAdapter
from rigplane.core.state_acquisition_policy import (
    ACQUISITION_CLASS_TABLE,
    AcquisitionPhase,
    AcquisitionPolicy,
    AdaptiveDecayPolicy,
    AvailabilityClause,
    FieldAvailability,
    FieldCapability,
    MeterCoalescingPolicy,
    RadioAcquisitionProfile,
    fit_to_budget,
)
from rigplane.core.state_pipeline_contracts import (
    LIVE_FIELD_NAMES,
    ON_DEMAND_FIELD_NAMES,
    PANEL_ADJUSTABLE_FIELD_NAMES,
    AcquisitionClass,
    DEFAULT_FIELD_REGISTRY,
    FieldPath,
    FieldScope,
    acquisition_class_for_path,
)
from rigplane.core.state_store import FreshnessState, StateStore
from rigplane.profiles import get_radio_profile
from rigplane.rig_loader import RigLoadError, discover_rigs, load_rig
from _acquisition_query_helpers import (
    AcquisitionQueryCase,
    civ_frame_parts,
    recording_executor,
)

RIGS_DIR = Path(__file__).resolve().parent.parent / "rigs"

# Fields exempted from
# test_command_response_observable_fields_are_reachable_by_prime_or_poll.
# IC-9700 sub-receiver freq/mode is declared command_response_observable and
# resolves to the unselected-VFO (selector 1) read, but no IC-9700 has been
# on the bench to confirm the radio answers that read with SUB data, so no
# field_policies entry is added for it here.
_UNPRIMABLE_COMMAND_RESPONSE_EXEMPTIONS: dict[str, frozenset[FieldPath]] = {
    "IC-9700": frozenset(
        {
            FieldPath.active("sub", "freq_mode", "freq_hz"),
            FieldPath.active("sub", "freq_mode", "mode"),
        }
    ),
}


# --- Cadence classes -------------------------------------------------------
# Longest a class of field may lag the front panel, in seconds. The class
# MEMBERSHIP lives in the production registry since MOR-2574 step 1 --
# ``rigplane.core.state_pipeline_contracts`` exports the name sets -- so
# this file reads the sets and keeps no second copy of them. The BOUNDS
# below are literals of the owner's rulings, not reads of the production
# table: these tests guard that table, so raising a production constant
# must go red here.

#: Continuously-moving readings the profile declares ``stream_like``
#: (owner ruling 2026-09-07; 0.4s is IC-7300's cadence after the S-meter
#: gave 2.5 q/s back to fund the panel tier below; every other profile
#: declares 0.2s or 0.25s).
_STREAM_METER_MAX_CADENCE_SECONDS = 0.4
#: Facts that move while the operator tunes or keys (owner ruling
#: 2026-09-07).
_LIVE_MAX_CADENCE_SECONDS = 1.0
#: Everything the operator reaches by turning a knob or opening a menu.
#: The owner's single threshold for a panel change reaching the web
#: (ruling of 2026-09-07).
_OPERATOR_SET_MAX_CADENCE_SECONDS = 5.0

#: Classified paths with no own ``field_policies`` entry, so
#: ``test_field_policies_obey_their_cadence_class_not_their_rig`` cannot see
#: them (it walks ``field_policies.items()``, not ``capabilities``): each
#: inherits the profile's ``default_cadence_seconds`` instead, and that
#: inherited cadence exceeds the path's class bound. Reproduced by
#: ``_inherited_default_out_of_class`` below, which walks the
#: ``capabilities`` of every profile that declares ``field_policies`` and
#: keeps only paths where that comparison fails. Profiles declaring no
#: ``field_policies`` (X6100, since MOR-2540 the only one) are outside this
#: list: the same walk without that skip finds 4 more such paths there,
#: which no test bounds. IC-705/IC-9700 joined the walked set when MOR-2540
#: gave their four TX meters ``field_policies`` entries.
_INHERITED_DEFAULT_OUT_OF_CLASS: dict[str, tuple[FieldPath, ...]] = {
    "X6200": (
        # inherits default 2.0s; live bound is 1.0s
        FieldPath.active("main", "freq_mode", "freq_hz"),
    ),
    "IC-705": (
        # vd/id inherit default 2.0s; stream meter bound is 0.4s
        FieldPath.global_("meters", "id"),
        FieldPath.global_("meters", "vd"),
        # inherits default 2.0s; live bound is 1.0s
        FieldPath.global_("tx_state", "ptt"),
        FieldPath.active("main", "freq_mode", "freq_hz"),
        FieldPath.active("main", "freq_mode", "mode"),
        # inherits default 2.0s; stream meter bound is 0.4s
        FieldPath.receiver("main", "meters", "s_meter"),
        # inherits default 2.0s; live bound is 1.0s
        FieldPath.unselected("main", "freq_mode", "freq_hz"),
        FieldPath.unselected("main", "freq_mode", "mode"),
    ),
    "IC-7610": (
        # inherits default 2.0s; live bound is 1.0s
        FieldPath.active("main", "freq_mode", "mode"),
        FieldPath.active("sub", "freq_mode", "mode"),
    ),
    "IC-9700": (
        # vd/id inherit default 2.0s; stream meter bound is 0.4s
        FieldPath.global_("meters", "id"),
        FieldPath.global_("meters", "vd"),
        # inherits default 2.0s; live bound is 1.0s
        FieldPath.global_("tx_state", "ptt"),
        FieldPath.active("main", "freq_mode", "freq_hz"),
        FieldPath.active("main", "freq_mode", "mode"),
        # inherits default 2.0s; stream meter bound is 0.4s
        FieldPath.receiver("main", "meters", "s_meter"),
        # inherits default 2.0s; live bound is 1.0s
        FieldPath.active("sub", "freq_mode", "freq_hz"),
        FieldPath.active("sub", "freq_mode", "mode"),
    ),
    "IC-7300": (
        # inherits default 1.5s; live bound is 1.0s
        FieldPath.unselected("main", "freq_mode", "freq_hz"),
        FieldPath.unselected("main", "freq_mode", "mode"),
    ),
    "FTX-1": (
        # inherits default 2.0s; live bound is 1.0s
        FieldPath.global_("tx_state", "ptt"),
        FieldPath.active("main", "freq_mode", "freq_hz"),
        FieldPath.active("main", "freq_mode", "mode"),
    ),
}

#: The ten panel knobs the owner's ruling named, on the one rig it named.
_IC7300_PANEL_KNOB_PATHS = (
    FieldPath.active("main", "freq_mode", "filter_width"),
    FieldPath.global_("operator_controls", "rit_freq"),
    FieldPath.receiver("main", "operator_controls", "manual_notch_width"),
    FieldPath.receiver("main", "operator_controls", "nb_level"),
    FieldPath.receiver("main", "operator_controls", "notch_filter"),
    FieldPath.receiver("main", "operator_controls", "nr_level"),
    FieldPath.receiver("main", "operator_controls", "pbt_inner"),
    FieldPath.receiver("main", "operator_controls", "pbt_outer"),
    FieldPath.receiver("main", "operator_toggles", "auto_notch"),
    FieldPath.receiver("main", "operator_toggles", "manual_notch"),
)
_IC7300_PANEL_KNOB_CADENCE_SECONDS = 5.0
#: Twice the cadence, the ratio the IC-7300 profile default carries
#: (1.5/3.0), as do its 1.0/2.0, 3.0/6.0 and 30.0/60.0 tiers.
_IC7300_PANEL_KNOB_TTL_SECONDS = 10.0

#: The menu settings that stay on-demand: reached by
#: ``AcquisitionScheduler.prime_unobserved`` and refreshed by their own
#: command response, never by a cadence read.
_IC7300_ON_DEMAND_PATHS = (
    FieldPath.active("main", "freq_mode", "data_mode"),
    FieldPath.active("main", "freq_mode", "filter_num"),
    FieldPath.global_("operator_controls", "break_in"),
    FieldPath.global_("operator_controls", "break_in_delay"),
    FieldPath.global_("operator_controls", "cw_pitch"),
    FieldPath.global_("operator_controls", "key_speed"),
    FieldPath.global_("operator_controls", "vox_delay"),
    FieldPath.global_("tx_state", "monitor_on"),
    FieldPath.global_("tx_state", "rit_on"),
    FieldPath.global_("tx_state", "rit_tx"),
    FieldPath.global_("tx_state", "vox_on"),
    FieldPath.receiver("main", "operator_controls", "agc_time_constant"),
    FieldPath.receiver("main", "operator_controls", "filter_shape"),
    FieldPath.receiver("main", "operator_controls", "tone_freq"),
    FieldPath.receiver("main", "operator_controls", "tsql_freq"),
    FieldPath.receiver("main", "operator_toggles", "twin_peak_filter"),
)


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

    with pytest.raises(ValueError, match="startupRequired must be a bool"):
        FieldCapability.from_dict({"path": freq, "startupRequired": "false"})

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


def test_field_capability_startup_required_defaults_true_and_round_trips() -> None:
    path = FieldPath.global_("slow_state", "scope_mode")

    default_capability = FieldCapability(path=path)
    assert default_capability.startup_required is True

    startup_optional = FieldCapability(
        path=path,
        polling=True,
        startup_required=False,
    )
    payload = json.loads(json.dumps(startup_optional.to_dict()))
    assert payload["startupRequired"] is False
    restored = FieldCapability.from_dict(payload)
    assert restored == startup_optional
    assert restored.startup_required is False


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


def test_loader_parses_startup_optional_capability(tmp_path: Path) -> None:
    path_text = "scope_controls.global.display.mode"
    toml = _minimal_state_acquisition_toml(
        f"""
        [state_acquisition]
        provider = "icom_civ"

        [state_acquisition.capabilities]
        polling_only = ["{path_text}"]
        startup_optional = ["{path_text}"]
        """
    )

    profile = load_rig(_write_toml(tmp_path, toml)).to_profile()
    acquisition = profile.state_acquisition
    path = FieldPath.parse(path_text)

    assert acquisition is not None
    capability = acquisition.capability_for(path)
    assert capability.polling is True
    assert capability.startup_required is False


def test_loader_rejects_startup_optional_path_without_acquisition_route(
    tmp_path: Path,
) -> None:
    path_text = "scope_controls.global.display.mode"
    toml = _minimal_state_acquisition_toml(
        f"""
        [state_acquisition]
        provider = "icom_civ"

        [state_acquisition.capabilities]
        startup_optional = ["{path_text}"]
        """
    )

    with pytest.raises(RigLoadError, match="must also be declared acquisitive"):
        load_rig(_write_toml(tmp_path, toml))


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


def test_command_response_observable_fields_are_reachable_by_prime_or_poll() -> None:
    """R36b gates the web listener on every declared field observed once.

    A ``command_response_observable`` field with a non-polling capability
    (``polling=False``) is primed only by
    ``AcquisitionScheduler.prime_unobserved``, which iterates
    ``field_policies`` (see that method's docstring). Every such field must
    be in ``polling_only`` (``capability.polling``) or ``field_policies``,
    except the named exemptions above.
    """

    failures: list[str] = []
    for model, rig in discover_rigs(RIGS_DIR).items():
        profile = rig.to_profile()
        acquisition = profile.state_acquisition
        if acquisition is None:
            continue
        exempt = _UNPRIMABLE_COMMAND_RESPONSE_EXEMPTIONS.get(model, frozenset())
        for capability in acquisition.capabilities:
            if not capability.command_response_observable:
                continue
            if capability.polling:
                continue
            if capability.path in acquisition.field_policies:
                continue
            if capability.path in exempt:
                continue
            failures.append(f"{model}: {capability.path}")

    assert not failures, (
        "command_response_observable field(s) with no polling_only or "
        "field_policies coverage (never primable) and not in "
        f"_UNPRIMABLE_COMMAND_RESPONSE_EXEMPTIONS: {sorted(failures)}"
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
            assert policy.cadence_seconds <= _STREAM_METER_MAX_CADENCE_SECONDS
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


def _cadence_class(
    path: FieldPath,
    capability: FieldCapability,
) -> tuple[str, float] | None:
    """Name the cadence class a path belongs to, or ``None`` if unclassified.

    The membership comes from the production registry (MOR-2574 step 1):
    ``LIVE_FIELD_NAMES``, ``PANEL_ADJUSTABLE_FIELD_NAMES`` and
    ``ON_DEMAND_FIELD_NAMES`` in ``rigplane.core.state_pipeline_contracts``.
    """

    if capability.stream_like:
        return ("stream meter", _STREAM_METER_MAX_CADENCE_SECONDS)
    if path.scope is FieldScope.SCOPE_CONTROLS:
        # The spectrum-scope display settings share several names with radio
        # state (``mode``, ``span``, ``speed``) and are not what any class
        # below is about. No ruling covers them; they stay unclassified.
        return None
    if path.name in LIVE_FIELD_NAMES:
        return ("live", _LIVE_MAX_CADENCE_SECONDS)
    if path.name in PANEL_ADJUSTABLE_FIELD_NAMES:
        return ("panel-adjustable", _OPERATOR_SET_MAX_CADENCE_SECONDS)
    if path.name in ON_DEMAND_FIELD_NAMES:
        return ("on-demand", _OPERATOR_SET_MAX_CADENCE_SECONDS)
    return None


def test_field_policies_obey_their_cadence_class_not_their_rig() -> None:
    """One bound per class, applied to every path with its own field_policies entry.

    Owner ruling (2026-09-07): a field's cadence follows what the field is,
    not which radio it sits on. Each class constant above is the longest the
    web may lag a front-panel change for the fields in it; a profile may poll
    faster, and going slower needs a measured budget for that link (the only
    one in this repo is IC-7300's 20 q/s serial ceiling, asserted in
    ``test_ic7300_profile_enrolls_exact_supported_observation_rows``).

    This gate only checks a path that carries its own ``field_policies``
    entry, because it walks ``field_policies.items()``. A classified path
    that instead inherits the profile's ``default_cadence_seconds`` is
    invisible to it -- see ``_INHERITED_DEFAULT_OUT_OF_CLASS`` and
    ``test_inherited_default_cadence_out_of_class_paths_are_exactly_named``
    below for the eight such paths that are out of class today. A profile that
    declares no ``field_policies`` at all makes no per-field cadence claim --
    every path inherits one default -- so it is skipped; ``checked ==
    {...}`` below pins which profiles were walked, not that every classified
    path on each one carries its own entry. A classified path whose
    capability is not pollable is the on-demand shape instead: nothing
    re-reads it on a cadence, so it must carry no expiry.
    """

    checked: set[str] = set()
    failures: list[str] = []
    for model, rig in sorted(discover_rigs(RIGS_DIR).items()):
        acquisition = rig.to_profile().state_acquisition
        if acquisition is None or not acquisition.field_policies:
            continue
        checked.add(model)
        for path, policy in sorted(
            acquisition.field_policies.items(), key=lambda item: str(item[0])
        ):
            capability = acquisition.capability_for(path)
            classified = _cadence_class(path, capability)
            if classified is None:
                continue
            class_name, bound = classified
            if not capability.can_poll:
                if policy.freshness_ttl_seconds is not None:
                    failures.append(
                        f"{model}: {path} ({class_name}) is not pollable yet "
                        f"expires after {policy.freshness_ttl_seconds}s"
                    )
                continue
            cadence = policy.cadence_seconds
            if cadence is None or cadence > bound:
                failures.append(
                    f"{model}: {path} ({class_name}) cadence={cadence}s "
                    f"exceeds the class bound of {bound}s"
                )

    assert not failures, f"field_policies entries outside their class: {failures}"
    assert checked == {"FTX-1", "IC-705", "IC-7300", "IC-7610", "IC-9700", "X6200"}


def _inherited_default_out_of_class() -> dict[str, tuple[FieldPath, ...]]:
    """Classified paths the gate above cannot see, that are out of class.

    Walks the ``capabilities`` (not ``field_policies``, which is what the
    gate above walks) of every profile that declares ``field_policies`` —
    profiles declaring none are skipped, as the gate skips them — and keeps a
    path only if it (a) has no own
    ``field_policies`` entry, so its effective cadence is the profile's
    inherited ``default_cadence_seconds``, and (b) that inherited cadence
    exceeds its class bound.
    """

    found: dict[str, list[FieldPath]] = {}
    for model, rig in sorted(discover_rigs(RIGS_DIR).items()):
        acquisition = rig.to_profile().state_acquisition
        if acquisition is None or not acquisition.field_policies:
            continue
        for capability in sorted(acquisition.capabilities, key=lambda c: str(c.path)):
            path = capability.path
            if path in acquisition.field_policies:
                continue
            classified = _cadence_class(path, capability)
            if classified is None:
                continue
            _class_name, bound = classified
            cadence = acquisition.policy_for(path).cadence_seconds
            if cadence is not None and cadence <= bound:
                continue
            found.setdefault(model, []).append(path)
    return {model: tuple(paths) for model, paths in found.items()}


def test_inherited_default_cadence_out_of_class_paths_are_exactly_named() -> None:
    """On the profiles the gate walks, the paths it cannot see are exactly the named eight.

    ``test_field_policies_obey_their_cadence_class_not_their_rig`` only
    checks a path with its own ``field_policies`` entry. This test covers
    the gap: it re-derives ``_INHERITED_DEFAULT_OUT_OF_CLASS`` from the
    profiles that declare ``field_policies``, so fixing one of the eight (or
    introducing a new inherited-default violation on one of those profiles)
    changes the derived set and this assertion goes red, naming what
    changed. A profile with no ``field_policies`` is not walked, so a
    violation introduced there is not caught here.
    """

    assert _inherited_default_out_of_class() == _INHERITED_DEFAULT_OUT_OF_CLASS


def test_all_profiles_decay_ceiling_never_exceeds_freshness_limit() -> None:
    """MOR-2540: adaptive decay must never slow a field past its freshness window.

    For every pollable path on a CI-V acquisition profile, with adaptive decay
    enabled and a declared TTL, the effective maximum cadence — the base
    cadence widened no further than
    :func:`~rigplane.core.acquisition_scheduler.adaptive_cadence_limit` — must
    stay at or below ``TTL / FRESHNESS_DECAY_LIMIT_RATIO``. At origin/main the
    scheduler caps decay at ``max_cadence_seconds`` (30 s by profile default)
    with no TTL awareness, so an idle ``freq_hz`` (TTL 2.0 s) decays to 30 s
    and this walk fails, naming each profile and path; with the scheduler rule
    in place every profile passes with no profile edits. Only the CI-V
    acquisition providers (``icom_civ`` / ``xiegu_civ``) route through
    ``AcquisitionScheduler.record_acquisition_result``, where the decay rule
    lives; the Yaesu CAT path does not.
    """

    failures: list[str] = []
    checked: set[str] = set()
    for model, rig in sorted(discover_rigs(RIGS_DIR).items()):
        acquisition = rig.to_profile().state_acquisition
        if acquisition is None:
            continue
        if not provider_uses_civ_acquisition(acquisition.provider):
            continue
        checked.add(model)
        for capability in sorted(acquisition.capabilities, key=lambda c: str(c.path)):
            if not capability.can_poll:
                continue
            policy = acquisition.policy_for(capability.path)
            if not policy.adaptive_decay.enabled:
                continue
            ttl = policy.freshness_ttl_seconds
            base = policy.cadence_seconds
            if ttl is None or base is None:
                continue
            limit = adaptive_cadence_limit(policy)
            effective_max = base if limit is None else max(base, limit)
            freshness_limit = ttl / FRESHNESS_DECAY_LIMIT_RATIO
            if effective_max > freshness_limit:
                failures.append(
                    f"{model}: {capability.path} (base {base}s, decay ceiling "
                    f"{limit}s) exceeds freshness limit {freshness_limit}s"
                )

    assert not failures, f"decay exceeds freshness window: {failures}"
    assert checked == {"IC-705", "IC-7300", "IC-7610", "IC-9700", "X6100", "X6200"}


# --- MOR-2574 step 1: shared acquisition classes ----------------------------


def test_every_capability_path_of_every_profile_has_a_registry_class() -> None:
    """Every capability path of every shipped profile is registry-covered.

    Walks the LOADED profiles (never a hand list): each declared capability
    path must resolve in ``DEFAULT_FIELD_REGISTRY`` and therefore carry a
    required ``acquisition_class``. A new profile path that escapes the
    registry fails here by name, before any scheduler step can rely on the
    class column.
    """

    failures: list[str] = []
    checked: set[str] = set()
    for model, rig in sorted(discover_rigs(RIGS_DIR).items()):
        acquisition = rig.to_profile().state_acquisition
        if acquisition is None:
            continue
        checked.add(model)
        for capability in sorted(acquisition.capabilities, key=lambda c: str(c.path)):
            try:
                spec = DEFAULT_FIELD_REGISTRY.require(capability.path)
            except KeyError:
                failures.append(f"{model}: {capability.path} is not registered")
                continue
            expected = acquisition_class_for_path(capability.path)
            if spec.acquisition_class is not expected:
                failures.append(
                    f"{model}: {capability.path} carries "
                    f"{spec.acquisition_class.value}, derived {expected.value}"
                )

    assert not failures, f"capability paths without a registry class: {failures}"
    assert checked == {
        "FTX-1",
        "IC-705",
        "IC-7300",
        "IC-7610",
        "IC-9700",
        "X6100",
        "X6200",
    }


def test_registry_specs_carry_the_derived_acquisition_class() -> None:
    """The registry builders fill the class column via the derivation.

    Every ``FieldSpec`` in ``DEFAULT_FIELD_REGISTRY`` must carry exactly
    what ``acquisition_class_for_path`` derives for its path, so a spec
    hand-built with a mismatched class cannot slip in.
    """

    mismatches = [
        f"{spec.path}: {spec.acquisition_class.value}"
        f" != {acquisition_class_for_path(spec.path).value}"
        for spec in DEFAULT_FIELD_REGISTRY.fields
        if spec.acquisition_class is not acquisition_class_for_path(spec.path)
    ]
    assert not mismatches, mismatches


def test_acquisition_class_table_is_the_owner_approved_literal() -> None:
    """The table is exactly the owner-approved 8-row table of MOR-2574.

    A literal second copy, on purpose (owner decisions of 2026-09-24 in
    MOR-2574): the owner-ruling tests must not take their limits from the
    production constants they guard, or raising a constant would stay
    green. Rows are (nominal, ceiling, polled_in, held_at_ceiling_in_tx);
    the literal order is the rank order, high -> low.
    """

    expected: dict[AcquisitionClass, tuple[float, float, AcquisitionPhase, bool]] = {
        AcquisitionClass.KEYING: (0.3, 0.5, AcquisitionPhase.BOTH, False),
        AcquisitionClass.TX_METER: (0.25, 1.0, AcquisitionPhase.TRANSMIT, False),
        AcquisitionClass.LIVE: (1.0, 1.0, AcquisitionPhase.BOTH, False),
        AcquisitionClass.METER: (0.3, 0.4, AcquisitionPhase.BOTH, False),
        AcquisitionClass.CONTROL: (2.0, 5.0, AcquisitionPhase.BOTH, False),
        AcquisitionClass.PANEL: (5.0, 10.0, AcquisitionPhase.BOTH, False),
        AcquisitionClass.SETTING: (10.0, 30.0, AcquisitionPhase.BOTH, True),
        AcquisitionClass.MENU: (30.0, 60.0, AcquisitionPhase.BOTH, True),
    }

    assert tuple(ACQUISITION_CLASS_TABLE) == tuple(expected)
    for klass, (nominal, ceiling, polled_in, held) in expected.items():
        policy = ACQUISITION_CLASS_TABLE[klass]
        assert policy.nominal_cadence_seconds == nominal, klass
        assert policy.ceiling_cadence_seconds == ceiling, klass
        assert policy.polled_in is polled_in, klass
        assert policy.held_at_ceiling_in_tx is held, klass
        assert policy.freshness_ttl_seconds == max(2.0 * ceiling, ceiling + 0.7), klass


def test_fit_to_budget_stretches_the_lowest_class_first_within_ceilings() -> None:
    """Menu stretches to its ceiling before setting moves; live never moves.

    Demand: one live field (1.0 q/s), twenty settings (2.0 q/s) and ten
    menu reads (0.333 q/s) -- 3.333 q/s nominal against a 2.0 q/s limit
    (budget 4.0, margin 0.5). Menu alone cannot close the gap (0.167 q/s
    at its ceiling), so setting stretches next, to exactly the cadence
    that closes on the limit; live keeps its nominal cadence.
    """

    fit = fit_to_budget(
        {
            AcquisitionClass.LIVE: 1,
            AcquisitionClass.SETTING: 20,
            AcquisitionClass.MENU: 10,
        },
        budget_hz=4.0,
        margin=0.5,
        tx=False,
    )

    assert fit.fits is True
    assert fit.demand_hz == pytest.approx(2.0)
    ceilings = ACQUISITION_CLASS_TABLE[AcquisitionClass.MENU].ceiling_cadence_seconds
    assert fit.effective_cadence_seconds[AcquisitionClass.MENU] == ceilings
    assert fit.effective_cadence_seconds[AcquisitionClass.SETTING] == pytest.approx(
        24.0
    )
    assert fit.effective_cadence_seconds[AcquisitionClass.LIVE] == pytest.approx(1.0)


def test_fit_to_budget_reports_no_fit_when_ceilings_cannot_close_the_gap() -> None:
    """When even the ceilings overshoot, every stretched class sits at its ceiling."""

    fit = fit_to_budget(
        {AcquisitionClass.METER: 100},
        budget_hz=10.0,
        margin=1.0,
        tx=False,
    )

    assert fit.fits is False
    assert (
        fit.effective_cadence_seconds[AcquisitionClass.METER]
        == ACQUISITION_CLASS_TABLE[AcquisitionClass.METER].ceiling_cadence_seconds
    )
    assert fit.demand_hz == pytest.approx(100 / 0.4)


def test_fit_to_budget_applies_the_tx_window_rules() -> None:
    """TX-only classes are absent in receive; held classes start at their ceiling in TX.

    The slack case is the one that tells the two apart: ``{SETTING: 3,
    LIVE: 1}`` sits far under the 10 q/s budget in either window, so
    nothing stretches, and the setting cadence IS the start cadence —
    its nominal 10.0s in receive, its held ceiling 30.0s in TX. Dropping
    the held-at-ceiling-in-TX condition would leave 10.0s in both windows
    and fail the 30.0s assertion.
    """

    # Slack, receive: no stretch, settings at their nominal 10.0s.
    slack_receive = fit_to_budget(
        {AcquisitionClass.SETTING: 3, AcquisitionClass.LIVE: 1},
        budget_hz=10.0,
        margin=1.0,
        tx=False,
    )
    assert slack_receive.fits is True
    assert slack_receive.effective_cadence_seconds[AcquisitionClass.SETTING] == (
        pytest.approx(10.0)
    )
    # Slack, TX: no stretch, settings held at their 30.0s ceiling.
    slack_transmit = fit_to_budget(
        {AcquisitionClass.SETTING: 3, AcquisitionClass.LIVE: 1},
        budget_hz=10.0,
        margin=1.0,
        tx=True,
    )
    assert slack_transmit.fits is True
    assert slack_transmit.effective_cadence_seconds[AcquisitionClass.SETTING] == (
        pytest.approx(30.0)
    )
    assert slack_transmit.effective_cadence_seconds[AcquisitionClass.LIVE] == (
        pytest.approx(1.0)
    )

    # Over budget in receive: TX-only classes are absent entirely.
    receive = fit_to_budget(
        {AcquisitionClass.TX_METER: 4, AcquisitionClass.SETTING: 10},
        budget_hz=0.5,
        margin=0.75,
        tx=False,
    )
    assert AcquisitionClass.TX_METER not in receive.effective_cadence_seconds
    assert receive.fits is True
    # The ten settings must land on the 0.375 q/s limit exactly:
    # 10 fields / (10 / 0.375) = 0.375 q/s.
    assert receive.effective_cadence_seconds[AcquisitionClass.SETTING] == (
        pytest.approx(10.0 / 0.375)
    )
    assert receive.demand_hz == pytest.approx(0.375)

    # Over budget in TX: the setting class starts held at its ceiling and
    # is never stretched past it: the two live fields alone (2.0 q/s
    # nominal, 2.1 q/s with the held settings) cannot close on the 2.0 q/s
    # limit, and live has no headroom either -- no fit, settings stay at
    # 30.0.
    transmit = fit_to_budget(
        {AcquisitionClass.SETTING: 3, AcquisitionClass.LIVE: 2},
        budget_hz=2.0,
        margin=1.0,
        tx=True,
    )
    assert transmit.fits is False
    assert transmit.effective_cadence_seconds[AcquisitionClass.SETTING] == (
        pytest.approx(30.0)
    )
    assert transmit.effective_cadence_seconds[AcquisitionClass.LIVE] == (
        pytest.approx(1.0)
    )


def test_fit_to_budget_reproduces_the_design_ic7610_lan_receive_figure() -> None:
    """The design's IC-7610 LAN receive figure: setting stretched to ~14.09 s.

    Class counts are computed here from the LOADED IC-7610 profile. The
    MOR-2574 headline measurement folded the panel knobs into the setting
    class, before the owner's 2026-09-24 decision gave the 2026-09-07
    panel set its own 5 s class; this test reproduces that fold (57
    setting fields) so the 21.43 q/s limit stretches setting to about
    14.09 s and closes exactly on it.
    """

    acquisition = get_radio_profile("IC-7610").state_acquisition
    assert acquisition is not None
    counts: Counter[AcquisitionClass] = Counter()
    for capability in acquisition.capabilities:
        policy = acquisition.policy_for(capability.path)
        if not capability.can_poll or policy.cadence_seconds is None:
            continue
        counts[DEFAULT_FIELD_REGISTRY.require(capability.path).acquisition_class] += 1

    folded = dict(counts)
    folded[AcquisitionClass.SETTING] = folded.get(
        AcquisitionClass.SETTING, 0
    ) + folded.pop(AcquisitionClass.PANEL, 0)
    # LAN CI-V gap: runtime/radio.py ICOM_CIV_MIN_INTERVAL_MS default 35 ms.
    lan_budget_hz = 1.0 / 0.035
    fit = fit_to_budget(
        folded,
        budget_hz=lan_budget_hz,
        margin=0.75,
        tx=False,
    )

    assert fit.fits is True
    assert fit.effective_cadence_seconds[AcquisitionClass.SETTING] == (
        pytest.approx(14.09, abs=0.01)
    )
    assert fit.demand_hz == pytest.approx(0.75 * lan_budget_hz)


def test_ic7300_panel_knob_fields_are_polled_at_the_panel_class_cadence() -> None:
    """The ten knobs the ruling named must be cadence-polled, not on-demand.

    Left non-polling they sit in ``command_response_observable`` only, which
    ``AcquisitionScheduler._poll_cadence_groups`` skips -- a front-panel
    change would never reach the web until a command happened to touch the
    field, which is the case the 5.0s bound exists to close.
    """

    acquisition = get_radio_profile("IC-7300").state_acquisition
    assert acquisition is not None

    for path in _IC7300_PANEL_KNOB_PATHS:
        capability = acquisition.capability_for(path)
        assert capability.can_poll is True, f"{path} is not cadence-polled"
        policy = acquisition.policy_for(path)
        assert policy.cadence_seconds == _IC7300_PANEL_KNOB_CADENCE_SECONDS, path
        assert policy.freshness_ttl_seconds == _IC7300_PANEL_KNOB_TTL_SECONDS, path


def test_ic7300_supply_meter_ttls_clear_twice_their_cadence() -> None:
    """MOR-2425: ``vd``/``id`` are what a CI-V observation is stamped with.

    Since ``runtime/_civ_rx.py: _observation_max_age`` reads this key, the
    declared TTL is the window a supply-rail reading actually gets. These two
    were the only IC-7300 meters whose TTL sat below twice their cadence.
    """

    acquisition = get_radio_profile("IC-7300").state_acquisition
    assert acquisition is not None

    for name in ("vd", "id"):
        path = FieldPath.global_("meters", name)
        policy = acquisition.policy_for(path)
        assert acquisition.capability_for(path).can_poll is True, path
        assert policy.cadence_seconds == 60.0, path
        assert policy.freshness_ttl_seconds == 2 * policy.cadence_seconds, path


def test_ic7300_on_demand_fields_keep_the_never_ttl() -> None:
    """The menu settings stay on-demand: no cadence read, so no expiry."""

    acquisition = get_radio_profile("IC-7300").state_acquisition
    assert acquisition is not None

    for path in _IC7300_ON_DEMAND_PATHS:
        assert acquisition.capability_for(path).can_poll is False, path
        assert acquisition.policy_for(path).freshness_ttl_seconds is None, path

    never_ttl = {
        path
        for path, policy in acquisition.field_policies.items()
        if policy.freshness_ttl_seconds is None
    }
    assert never_ttl == set(_IC7300_ON_DEMAND_PATHS)


def test_ftx1_profile_declares_slow_control_policies_for_polling_adapter() -> None:
    ftx1 = get_radio_profile("FTX-1")
    assert ftx1.state_acquisition is not None

    af_level = FieldPath.receiver("main", "operator_controls", "af_level")
    squelch = FieldPath.receiver("sub", "operator_controls", "squelch")
    ptt = FieldPath.global_("tx_state", "ptt")

    assert ftx1.state_acquisition.capability_for(af_level).can_poll is True
    assert ftx1.state_acquisition.capability_for(squelch).can_poll is True
    assert ftx1.state_acquisition.capability_for(ptt).can_poll is True
    assert ftx1.state_acquisition.policy_for(af_level).freshness_ttl_seconds == 2.0
    assert ftx1.state_acquisition.policy_for(af_level).cadence_seconds == 1.0


def test_ftx1_tx_meters_are_declared_tx_only() -> None:
    """MOR-2425/T80b: YaesuCatPoller._emit_fast_observations (poller.py) only
    emits ALC/power/SWR/comp via ``poll_tx_meters`` while PTT observes true;
    the profile must say so for the startup gate that treats ``tx_only`` as
    profile-authoritative.
    """
    ftx1 = get_radio_profile("FTX-1")
    assert ftx1.state_acquisition is not None

    for path in (
        FieldPath.global_("meters", "alc"),
        FieldPath.global_("meters", "power"),
        FieldPath.global_("meters", "swr"),
        FieldPath.global_("meters", "comp"),
    ):
        assert ftx1.state_acquisition.policy_for(path).tx_only is True


def test_ftx1_drain_meters_poll_in_receive_at_the_slow_control_cadence() -> None:
    """MOR-2425/T147: VDD/IDD are declared, pollable, and NOT ``tx_only``.

    A read-only bench probe on 2026-09-08 got an answer to all ten ``RM7;``
    and all ten ``RM8;`` reads with the FTX-1 receiving, so these two carry
    neither ``tx_only`` nor an ``available_when`` clause -- unlike the four
    transmit meters above. They are read by
    ``YaesuObservationAdapter.poll_slow_controls``, so their cadence is the
    slow-control tier's 1.0s, the same number
    ``YaesuCatPoller._SLOW_INTERVAL`` carries, with the TTL at twice that.
    """
    ftx1 = get_radio_profile("FTX-1")
    assert ftx1.state_acquisition is not None
    acquisition = ftx1.state_acquisition

    af_level = FieldPath.receiver("main", "operator_controls", "af_level")
    for path in (
        FieldPath.global_("meters", "vd"),
        FieldPath.global_("meters", "id"),
    ):
        capability = acquisition.capability_for(path)
        policy = acquisition.policy_for(path)
        assert capability.can_poll is True, path
        # Not the stream-meter class: that class is bound to a fast cadence by
        # test_known_profiles_stream_like_meters_use_fast_non_decaying_policies.
        assert capability.stream_like is False, path
        assert policy.tx_only is False, path
        assert policy.available_when == (), path
        assert policy.cadence_seconds == 1.0, path
        assert policy.freshness_ttl_seconds == 2.0, path
        assert (
            policy.cadence_seconds == acquisition.policy_for(af_level).cadence_seconds
        )


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
        FieldPath.receiver("main", "operator_toggles", "ipplus"),
        FieldPath.global_("operator_controls", "power_level"),
        FieldPath.global_("tx_state", "compressor_on"),
        FieldPath.global_("operator_controls", "compressor_level"),
        FieldPath.global_("tx_state", "ptt"),
        FieldPath.global_("operator_controls", "tuner_status"),
        FieldPath.global_("tx_state", "split"),
        # MOR-2425 (owner ruling, 2026-09-07): the ten panel knobs, moved off
        # command_response-only membership onto a 5.0s cadence -- see
        # _IC7300_PANEL_KNOB_PATHS and
        # test_ic7300_panel_knob_fields_are_polled_at_the_panel_class_cadence.
        *_IC7300_PANEL_KNOB_PATHS,
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
        FieldPath.scope_control("display", "receiver"),
        FieldPath.scope_control("display", "dual"),
        FieldPath.scope_control("display", "mode"),
        FieldPath.scope_control("display", "span"),
        FieldPath.scope_control("display", "edge"),
        FieldPath.scope_control("display", "hold"),
        FieldPath.scope_control("display", "ref_db"),
        FieldPath.scope_control("display", "speed"),
        FieldPath.scope_control("display", "during_tx"),
        FieldPath.scope_control("display", "center_type"),
        FieldPath.scope_control("display", "vbw_narrow"),
        FieldPath.scope_control("display", "fixed_edge"),
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

    # MOR-1452 (review fix) / MOR-1484 (bench-measured tightening): the 4
    # mic/monitor/VOX/anti-VOX gain fields sit at 10.0s/15.0s, NOT IC-7610's
    # 3.0s/5.0s for the same fields (rigs/ic7610.toml) — IC-7610 is LAN,
    # IC-7300 is serial and shares one ~20 q/s software floor across every
    # poll, operator command, and keep-alive on the same lane (see the
    # serial-budget assertion below for the exact arithmetic that rules out
    # 3.0s here). MOR-1484 tightened these from 15.0s/25.0s after the
    # ticket's bench probe measured mic_gain populate (slow front-panel
    # rotation) at 10.73s/16.25s against this tier's "<=15s populate" intent.
    for path in (
        FieldPath.global_("operator_controls", "mic_gain"),
        FieldPath.global_("operator_controls", "monitor_gain"),
        FieldPath.global_("operator_controls", "vox_gain"),
        FieldPath.global_("operator_controls", "anti_vox_gain"),
    ):
        policy = acquisition.policy_for(path)
        assert policy.cadence_seconds == 10.0
        assert policy.freshness_ttl_seconds == 15.0

    # MOR-1484: freq_hz/mode (active VFO) and rf_gain/squelch were pulled out
    # of the profile's shared 1.5s/3.0s default tier into their own 1.0s/2.0s
    # tier — the bench probe's most-measured "pending frequency echo" /
    # "slider trail" symptoms — so they no longer share the default policy's
    # freshness TTL.
    for path in (
        FieldPath.active("main", "freq_mode", "freq_hz"),
        FieldPath.active("main", "freq_mode", "mode"),
        FieldPath.receiver("main", "operator_controls", "rf_gain"),
        FieldPath.receiver("main", "operator_controls", "squelch"),
    ):
        policy = acquisition.policy_for(path)
        assert policy.cadence_seconds == 1.0
        assert policy.freshness_ttl_seconds == 2.0
    assert (
        acquisition.policy_for(
            FieldPath.active("main", "freq_mode", "freq_hz")
        ).freshness_ttl_seconds
        < acquisition.default_policy.freshness_ttl_seconds
    )

    # MOR-1484: budget freed for the tier above by giving back cadence on six
    # rarely-touched settings (tuner match, RF power level, compressor
    # on/level, attenuator, preamp) — none in the ticket's measured or
    # operator-felt symptom list.
    for path in (
        FieldPath.global_("operator_controls", "tuner_status"),
        FieldPath.global_("operator_controls", "power_level"),
        FieldPath.global_("tx_state", "compressor_on"),
        FieldPath.global_("operator_controls", "compressor_level"),
        FieldPath.receiver("main", "operator_controls", "att"),
        FieldPath.receiver("main", "operator_controls", "preamp"),
    ):
        policy = acquisition.policy_for(path)
        assert policy.cadence_seconds == 3.0
        assert policy.freshness_ttl_seconds == 6.0

    ipplus_policy = acquisition.policy_for(
        FieldPath.receiver("main", "operator_toggles", "ipplus")
    )
    assert ipplus_policy.cadence_seconds == 5.0
    assert ipplus_policy.freshness_ttl_seconds == 10.0
    assert ipplus_policy.adaptive_decay.enabled is False

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
    # one that must stay near the MOR-1484 baseline below -- not the
    # transient TX-window total.
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
    # MOR-1484 baseline: pre-MOR-1484 total was 19.967 q/s. MOR-1484 moved
    # freq_hz(active)/mode(active)/rf_gain/squelch (4 fields) from the 1.5s
    # default tier to a dedicated 1.0s tier (+2.667 -> +4.0 = +1.333 q/s) and
    # mic/monitor/VOX/anti-VOX gain (4 fields) from 15.0s to 10.0s (+0.267 ->
    # +0.4 = +0.133 q/s), funded by giving six rarely-touched settings
    # (tuner_status, power_level, compressor_on/level, att, preamp; 6 fields)
    # back from 1.5s to 3.0s (-4.0 -> -2.0 = -2.0 q/s). Net:
    # 19.967 + 1.333 + 0.133 - 2.0 = 19.433 q/s before the twelve 30s scope
    # reads declared by MOR-1983 add 0.4 q/s, for 19.833 q/s.
    #
    # MOR-2425 (owner ruling, 2026-09-07) enrols the ten panel knobs at 5.0s
    # (10 x 1/5.0 = +2.0 q/s) and funds them by halving the S-meter's rate,
    # 0.2s -> 0.4s (5.0 -> 2.5 = -2.5 q/s). Net:
    # 19.833 + 2.0 - 2.5 = 19.333 q/s. MOR-2449 adds the IP+ read at 5.0s
    # (+0.2 q/s), for 19.533 q/s: still below the 20 q/s serial ceiling.
    assert rx_state_demand_hz == pytest.approx(19.533, abs=0.001)
    assert rx_state_demand_hz < serial_ceiling_hz
    # Po/SWR/ALC/COMP: 4 fields / 1.0s = 4.0 q/s, ONLY while tx_only gating
    # lets them through (PTT observed true) — a transient TX-window cost, not
    # a steady-state one. Untouched by MOR-1484.
    assert tx_only_demand_hz == pytest.approx(4.0, abs=0.001)
    total_during_tx_hz = rx_state_demand_hz + tx_only_demand_hz
    assert total_during_tx_hz == pytest.approx(23.533, abs=0.001)

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
# key_speed (cmd 0x14, non-linear decode helpers), vox_delay (cmd 0x1A
# sub 0x05, 2-byte ctl-mem prefix rather than a plain sub byte), filter_num
# (cmd 0x26, a selector-form query so its query sub is always None -- MOR-2425)
# and data_mode (cmd 0x1A sub 0x06, MOR-2425). Kept as an explicit set -- not
# derivable from a dict import -- because the production code itself is
# branch-shaped there, not table-shaped; extend this set if a future field
# adds another such branch.
_HARDCODED_OBSERVABLE_FIELDS = {
    ("global", "operator_controls", "rit_freq"),
    ("global", "tx_state", "rit_on"),
    ("global", "tx_state", "rit_tx"),
    ("receiver", "operator_controls", "agc_time_constant"),
    ("receiver", "freq_mode", "filter_width"),
    ("global", "operator_controls", "cw_pitch"),
    ("global", "operator_controls", "key_speed"),
    ("global", "operator_controls", "vox_delay"),
    ("receiver", "freq_mode", "filter_num"),
    ("receiver", "freq_mode", "data_mode"),
}

# Minimal, per-field synthetic response payload for the round-trip probe
# below. For vox_delay this is APPENDED after the 2-byte ctl-mem prefix the
# query itself supplies (see ``_round_trip_observes``); filter_num's query
# similarly carries a 1-byte cmd-0x26 selector prefix, so its payload is
# appended after that byte (mode=LSB, data_mode=0, filter_num=1, satisfying
# the >= 4 byte length the 0x26 branch requires to emit filter_num at all).
# For every other field the query carries no prefix, so the payload is the
# frame's entire ``data``. Values are chosen only to satisfy each branch's
# own length/shape checks (e.g. "at least 3 bytes", "at least 2 bytes") --
# the decoded VALUE is never asserted, only that a matching observation is
# produced at all.
_HARDCODED_SYNTHETIC_PAYLOAD: dict[str, bytes] = {
    "rit_freq": b"\x00\x00\x00",
    "rit_on": b"\x01",
    "rit_tx": b"\x01",
    "agc_time_constant": b"\x00",
    "filter_width": b"\x01",
    "cw_pitch": b"\x00\x00",
    "key_speed": b"\x00\x00",
    "vox_delay": b"\x00",
    "filter_num": b"\x00\x00\x01",
    "data_mode": b"\x00",
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
    query: AcquisitionQueryCase,
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

    from rigplane.commands import CONTROLLER_ADDR
    from rigplane.types import CivFrame

    parts = civ_frame_parts(query)
    payload = _HARDCODED_SYNTHETIC_PAYLOAD[path.name]
    frame = CivFrame(
        to_addr=CONTROLLER_ADDR,
        from_addr=0x94,  # IC-7300's civ_addr (rigs/ic7300.toml)
        command=parts.command,
        sub=parts.sub,
        data=parts.data + payload,
        receiver=parts.receiver,
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
        f"{path}: synthetic response (command=0x{parts.command:02x}, "
        f"sub={parts.sub!r}, data={(parts.data + payload).hex()}) produced no "
        "matching observation -- "
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

    from rigplane.radio import IcomRadio

    profile = get_radio_profile("IC-7300")
    acquisition = profile.state_acquisition
    assert acquisition is not None

    executor, _sent = recording_executor(profile)
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

        parts = civ_frame_parts(query)
        sub = parts.sub
        assert isinstance(sub, int), (
            f"{path}: table-backed field must query with a plain int sub, "
            f"got {sub!r} -- extend _HARDCODED_OBSERVABLE_FIELDS if this is "
            "actually a new branch-shaped ingress mapping"
        )
        assert parts.data == b"", (
            f"{path}: table-backed field must not carry data, got {parts.data!r}"
        )
        resolved = _table_backed_ingress_spec(parts.command, sub)
        assert resolved == key, (
            f"{path}: query sends command=0x{parts.command:02x} sub=0x{sub:02x}, "
            f"but the ingress table at that exact key resolves to {resolved} "
            f"instead of {key} -- a primed read for this path would never "
            "leave UNKNOWN"
        )


def test_ic7300_activation_does_not_change_ftx1_acquisition_contract() -> None:
    ftx1 = get_radio_profile("FTX-1")
    acquisition = ftx1.state_acquisition
    assert acquisition is not None

    assert acquisition.provider == "yaesu_cat"
    # MOR-2111 tone half: +4 SUB tone fields (repeater_tone/tsql toggles,
    # tone_freq/tsql_freq controls) on top of the previous 69/64.
    assert len(acquisition.capabilities) == 73
    assert len(acquisition.field_policies) == 68
    assert acquisition.default_policy.cadence_seconds == 2.0
    assert acquisition.default_policy.freshness_ttl_seconds == 8.0

    sub_shift = FieldPath.receiver("sub", "operator_controls", "repeater_shift")
    assert acquisition.capability_for(sub_shift).can_poll is True
    assert acquisition.policy_for(sub_shift).cadence_seconds == 1.0
    assert acquisition.policy_for(sub_shift).freshness_ttl_seconds == 2.0

    sub_tone = FieldPath.receiver("sub", "operator_toggles", "repeater_tone")
    assert acquisition.capability_for(sub_tone).can_poll is True
    assert acquisition.policy_for(sub_tone).cadence_seconds == 1.0
    assert acquisition.policy_for(sub_tone).freshness_ttl_seconds == 2.0


def test_non_polling_field_policies_declare_no_freshness_expiry() -> None:
    """A field nothing re-reads on a cadence must not expire on its own.

    ``AcquisitionScheduler._poll_cadence_groups`` skips every capability
    whose ``can_poll`` is false, so no cadence read ever refreshes such a
    field. The profile spells "no expiry" as ``freshness_ttl_seconds = "never"``, which
    the loader resolves to ``None`` — the value
    ``StateStore.mark_stale_due`` skips instead of ageing.
    """

    failures: list[str] = []
    for model, rig in discover_rigs(RIGS_DIR).items():
        acquisition = rig.to_profile().state_acquisition
        if acquisition is None:
            continue
        for path, policy in sorted(
            acquisition.field_policies.items(), key=lambda item: str(item[0])
        ):
            if acquisition.capability_for(path).can_poll:
                continue
            if policy.freshness_ttl_seconds is None:
                continue
            failures.append(
                f"{model}: {path} freshness_ttl_seconds={policy.freshness_ttl_seconds}"
            )

    assert not failures, (
        "field_policies entry on a non-polling capability with a finite "
        f"freshness_ttl_seconds (nothing will ever refresh it): {failures}"
    )


def test_ic7300_on_demand_field_stays_fresh_while_polled_field_expires() -> None:
    """Only the cadence-polled field ages out; the on-demand one does not.

    Drives the profile-policy chain end to end over the IC-7300 profile's
    policies through ``ProviderObservationAdapter`` (the adapter the Yaesu
    CAT and rigctld-client backends build observations with); the IC-7300
    CI-V ingress is not under test here.
    ``ProviderObservationAdapter`` reads each path's ``max_age`` from its
    ``field_policies`` entry, ``StateStore`` keeps it on the entry, and
    ``StateFreshnessService.tick`` is what ages it. ``cw_pitch`` rides a
    ``polling=False`` capability, so no cadence read refreshes it;
    ``freq_hz`` is cadence-polled and must still expire, so the decay
    mechanism is not disabled wholesale.
    """

    profile = get_radio_profile("IC-7300")
    acquisition = profile.state_acquisition
    assert acquisition is not None
    on_demand = FieldPath.global_("operator_controls", "cw_pitch")
    polled = FieldPath.active("main", "freq_mode", "freq_hz")
    assert acquisition.capability_for(on_demand).can_poll is False
    assert acquisition.capability_for(polled).can_poll is True
    polled_ttl = acquisition.policy_for(polled).freshness_ttl_seconds
    assert polled_ttl is not None and polled_ttl < 60.0

    observed_at = 1_000.0
    adapter = ProviderObservationAdapter(
        profile=acquisition,
        source="poll_response",
        clock=lambda: observed_at,
    )
    store = StateStore()
    store.apply(adapter.observation(on_demand, 2_400))
    store.apply(adapter.observation(polled, 14_074_000))
    service = StateFreshnessService(store=store)

    delta = service.tick(now=observed_at + 60.0)

    stale_paths = {
        request.path
        for request in delta.reconciliation_requests
        if request.reason == "stale"
    }
    assert polled in stale_paths
    assert on_demand not in stale_paths
    freshness = {field.path: field.freshness for field in store.snapshot().fields}
    assert freshness[polled] is FreshnessState.STALE
    assert freshness[on_demand] is FreshnessState.FRESH


def test_loader_parses_never_as_absent_freshness_ttl(tmp_path: Path) -> None:
    """``"never"`` is the TOML spelling of a ``None`` seconds value.

    TOML has no null literal, and an omitted key inherits the profile
    default rather than clearing it, so a policy that must carry no expiry
    needs an explicit token.
    """

    toml = _minimal_state_acquisition_toml(
        """
        [state_acquisition]
        provider = "icom_civ"
        default_cadence_seconds = 2.0
        default_freshness_ttl_seconds = 8.0

        [state_acquisition.capabilities]
        polling_only = ["global.meters.power"]
        command_response_observable = ["global.tx_state.vox_on"]

        [state_acquisition.field_policies."global.tx_state.vox_on"]
        cadence_seconds = 25.0
        freshness_ttl_seconds = "never"
        reconciliation_priority = "command_response"
        """
    )

    acquisition = load_rig(_write_toml(tmp_path, toml)).to_profile().state_acquisition
    vox_on = FieldPath.global_("tx_state", "vox_on")
    power = FieldPath.global_("meters", "power")

    assert acquisition is not None
    assert acquisition.policy_for(vox_on).freshness_ttl_seconds is None
    assert acquisition.policy_for(vox_on).cadence_seconds == 25.0
    # A path with no override still inherits the numeric profile default.
    assert acquisition.policy_for(power).freshness_ttl_seconds == 8.0


def test_ic7300_on_demand_field_primes_with_its_cadence_as_max_age() -> None:
    """A no-expiry entry's ``cadence_seconds`` becomes the prime's max_age.

    ``AcquisitionScheduler.prime_unobserved`` falls back to
    ``policy.cadence_seconds`` for the prime read's ``max_age`` whenever
    ``freshness_ttl_seconds`` is ``None``, so ``cadence_seconds`` is
    load-bearing on these entries even though nothing polls them.
    """

    acquisition = get_radio_profile("IC-7300").state_acquisition
    assert acquisition is not None
    on_demand = FieldPath.global_("operator_controls", "cw_pitch")
    assert acquisition.policy_for(on_demand).freshness_ttl_seconds is None

    scheduler = AcquisitionScheduler(profile=acquisition)
    request_by_path: dict[FieldPath, Any] = {}
    for _ in range(len(acquisition.field_policies)):
        for request in scheduler.prime_unobserved(observed_paths=()):
            for path in request.paths:
                request_by_path[path] = request

    assert request_by_path[on_demand].max_age == (
        acquisition.policy_for(on_demand).cadence_seconds
    )


def _available_when_toml(value: str) -> str:
    return _minimal_state_acquisition_toml(
        f"""
        [state_acquisition]
        provider = "icom_civ"
        default_cadence_seconds = 2.0
        default_freshness_ttl_seconds = 8.0

        [state_acquisition.capabilities]
        polling_only = ["global.meters.power", "global.tx_state.vox_on"]

        [state_acquisition.field_policies."global.meters.power"]
        available_when = {value}
        """
    )


def test_loader_parses_every_available_when_operator(tmp_path: Path) -> None:
    """Each of the five clause operators reaches the parsed field policy."""

    toml = _available_when_toml(
        """[
            { field = "receiver.main.active.freq_mode.mode", in = ["USB", "LSB"] },
            { field = "receiver.main.active.freq_mode.mode", not_in = ["FM"] },
            { field = "receiver.main.active.freq_mode.freq_hz", min = 1800000 },
            { field = "receiver.main.active.freq_mode.freq_hz", max = 60000000 },
            { field = "global.tx_state.split", equals = false },
        ]"""
    )

    acquisition = load_rig(_write_toml(tmp_path, toml)).to_profile().state_acquisition
    assert acquisition is not None
    mode = FieldPath.active("main", "freq_mode", "mode")
    freq = FieldPath.active("main", "freq_mode", "freq_hz")
    split = FieldPath.global_("tx_state", "split")

    assert acquisition.policy_for(
        FieldPath.global_("meters", "power")
    ).available_when == (
        AvailabilityClause(field=mode, operator="in", value=("USB", "LSB")),
        AvailabilityClause(field=mode, operator="not_in", value=("FM",)),
        AvailabilityClause(field=freq, operator="min", value=1800000.0),
        AvailabilityClause(field=freq, operator="max", value=60000000.0),
        AvailabilityClause(field=split, operator="equals", value=False),
    )
    # A path with no override of its own does not inherit these clauses.
    assert (
        acquisition.policy_for(FieldPath.global_("tx_state", "vox_on")).available_when
        == ()
    )


@pytest.mark.parametrize(
    ("value", "message"),
    [
        ('"main only"', "must be a list of clauses"),
        ('["main only"]', "must be a table"),
        ('[{ field = "global.tx_state.split" }]', "exactly one"),
        (
            '[{ field = "global.tx_state.split", equals = true, min = 1 }]',
            "exactly one",
        ),
        ('[{ field = "global.tx_state.split", nope = 1 }]', "unknown key"),
        ("[{ equals = true }]", "field"),
        ('[{ field = "global tx_state split", equals = true }]', "field"),
        ('[{ field = "global.tx_state.split", in = "USB" }]', "must be a list"),
        ('[{ field = "global.tx_state.split", not_in = "USB" }]', "must be a list"),
        ('[{ field = "global.tx_state.split", min = "1" }]', "must be a number"),
        ('[{ field = "global.tx_state.split", max = "1" }]', "must be a number"),
    ],
)
def test_loader_rejects_malformed_available_when(
    tmp_path: Path, value: str, message: str
) -> None:
    """Every malformed clause shape is a load error naming the policy path."""

    with pytest.raises(RigLoadError) as excinfo:
        load_rig(_write_toml(tmp_path, _available_when_toml(value)))

    text = str(excinfo.value)
    assert message in text
    assert "field_policies.global.meters.power" in text


def test_available_when_round_trips_through_to_dict() -> None:
    policy = AcquisitionPolicy(
        cadence_seconds=1.0,
        freshness_ttl_seconds=2.0,
        available_when=(
            AvailabilityClause(
                field=FieldPath.active("main", "freq_mode", "mode"),
                operator="not_in",
                value=("FM",),
            ),
        ),
    )

    payload = json.loads(json.dumps(policy.to_dict()))
    assert payload["availableWhen"] == [
        {
            "field": "receiver.main.active.freq_mode.mode",
            "operator": "not_in",
            "value": ["FM"],
        }
    ]
    assert AcquisitionPolicy.from_dict(payload) == policy
    assert AcquisitionPolicy(cadence_seconds=1.0).available_when == ()


def test_ftx1_declares_when_manual_notch_and_attenuator_exist() -> None:
    """The two FTX-1 paths a 2026-09-08 bench probe found conditional."""

    acquisition = get_radio_profile("FTX-1").state_acquisition
    assert acquisition is not None

    notch = FieldPath.receiver("main", "operator_controls", "manual_notch_freq")
    assert acquisition.policy_for(notch).available_when == (
        AvailabilityClause(
            field=FieldPath.active("main", "freq_mode", "mode"),
            operator="not_in",
            value=("FM", "FM-N", "DATA-FM", "DATA-FM-N"),
        ),
    )

    att = FieldPath.receiver("main", "operator_controls", "att")
    assert acquisition.policy_for(att).available_when == (
        AvailabilityClause(
            field=FieldPath.active("main", "freq_mode", "freq_hz"),
            operator="max",
            value=60000000.0,
        ),
    )


def test_available_when_is_declared_only_where_a_probe_established_it() -> None:
    """Loading every shipped profile turns up exactly these declarations."""

    declared: set[tuple[str, str]] = set()
    for model, rig in discover_rigs(RIGS_DIR).items():
        acquisition = rig.to_profile().state_acquisition
        if acquisition is None:
            continue
        for path, policy in acquisition.field_policies.items():
            if policy.available_when:
                declared.add((model, str(path)))

    assert declared == {
        ("FTX-1", "global.meters.alc"),
        ("FTX-1", "global.meters.comp"),
        ("FTX-1", "global.meters.power"),
        ("FTX-1", "global.meters.swr"),
        ("FTX-1", "receiver.main.operator_controls.att"),
        ("FTX-1", "receiver.main.operator_controls.manual_notch_freq"),
        ("FTX-1", "receiver.sub.operator_controls.manual_notch_freq"),
        ("IC-705", "global.meters.alc"),
        ("IC-705", "global.meters.comp"),
        ("IC-705", "global.meters.power"),
        ("IC-705", "global.meters.swr"),
        ("IC-7300", "global.meters.alc"),
        ("IC-7300", "global.meters.comp"),
        ("IC-7300", "global.meters.power"),
        ("IC-7300", "global.meters.swr"),
        ("IC-7610", "global.meters.alc"),
        ("IC-7610", "global.meters.comp"),
        ("IC-7610", "global.meters.power"),
        ("IC-7610", "global.meters.swr"),
        ("IC-9700", "global.meters.alc"),
        ("IC-9700", "global.meters.comp"),
        ("IC-9700", "global.meters.power"),
        ("IC-9700", "global.meters.swr"),
    }


def test_ftx1_declares_the_transmit_meters_absent_outside_transmit() -> None:
    """The four tx_only meters exist only while the canonical PTT reads true."""

    acquisition = get_radio_profile("FTX-1").state_acquisition
    assert acquisition is not None

    ptt = FieldPath.global_("tx_state", "ptt")
    clause = AvailabilityClause(field=ptt, operator="equals", value=True)
    tx_meters = (
        FieldPath.global_("meters", "alc"),
        FieldPath.global_("meters", "power"),
        FieldPath.global_("meters", "swr"),
        FieldPath.global_("meters", "comp"),
    )
    for path in tx_meters:
        policy = acquisition.policy_for(path)
        assert policy.available_when == (clause,), path
        assert policy.tx_only is True, path

    # The condition source is itself polled, or nothing would ever resolve it.
    assert acquisition.capability_for(ptt).can_poll
    assert acquisition.policy_for(ptt).available_when == ()


def test_ic7300_declares_the_transmit_meters_absent_outside_transmit() -> None:
    """The four tx_only meters exist only while the canonical PTT reads true."""

    acquisition = get_radio_profile("IC-7300").state_acquisition
    assert acquisition is not None

    ptt = FieldPath.global_("tx_state", "ptt")
    clause = AvailabilityClause(field=ptt, operator="equals", value=True)
    tx_meters = (
        FieldPath.global_("meters", "alc"),
        FieldPath.global_("meters", "power"),
        FieldPath.global_("meters", "swr"),
        FieldPath.global_("meters", "comp"),
    )
    for path in tx_meters:
        policy = acquisition.policy_for(path)
        assert policy.available_when == (clause,), path
        assert policy.tx_only is True, path

    # The condition source is itself polled, or nothing would ever resolve it.
    assert acquisition.capability_for(ptt).can_poll
    assert acquisition.policy_for(ptt).available_when == ()


#: The four 0x15 TX-only meter paths MOR-2540 gates on the canonical PTT.
_TX_ONLY_METER_PATHS = (
    FieldPath.global_("meters", "alc"),
    FieldPath.global_("meters", "comp"),
    FieldPath.global_("meters", "power"),
    FieldPath.global_("meters", "swr"),
)

#: (model, path) pairs deliberately left ungated. X6100 has no backend at
#: all (``backends/factory.py`` refuses the model -- only the rigctld client
#: reaches the radio) and neither X6100 nor X6200 declares a pollable or
#: observable ``global.tx_state.ptt`` capability, so ``tx_only`` would fail
#: closed (``acquisition_scheduler.derive_tx_active`` returns False for an
#: unobserved PTT) and the power meter would never poll again. X6200 also
#: declares swr/alc ``unknown`` and comp not at all, so power is its only
#: declared transmit meter.
_TX_METER_GATE_EXEMPTIONS = {
    ("X6100", "global.meters.power"),
    ("X6200", "global.meters.power"),
}


def test_every_declared_transmit_meter_is_gated_on_ptt() -> None:
    """MOR-2540: every profile that declares a Po/SWR/ALC/COMP meter gates it.

    The gate is the IC-7300/FTX-1 shape the IC-7610 was missing: ``tx_only =
    true`` so AcquisitionScheduler skips the cadence group while PTT reads
    false, plus the ``available_when`` ptt clause so StateFreshnessService.tick
    discards the last TX values on dekey. The exempted pairs above are pinned
    ungated, with the reason, so a future profile cannot silently join them.
    """

    ptt = FieldPath.global_("tx_state", "ptt")
    clause = AvailabilityClause(field=ptt, operator="equals", value=True)
    gated: set[tuple[str, str]] = set()
    ungated: set[tuple[str, str]] = set()
    for model, rig in sorted(discover_rigs(RIGS_DIR).items()):
        acquisition = rig.to_profile().state_acquisition
        if acquisition is None:
            continue
        for path in _TX_ONLY_METER_PATHS:
            capability = acquisition.capability_for(path)
            if not (capability.can_poll or path in acquisition.field_policies):
                continue
            policy = acquisition.policy_for(path)
            if policy.tx_only and policy.available_when == (clause,):
                gated.add((model, str(path)))
                # The condition source is itself polled, or nothing would
                # ever resolve it.
                assert acquisition.capability_for(ptt).can_poll, (model, path)
                assert acquisition.policy_for(ptt).available_when == (), (
                    model,
                    path,
                )
            else:
                # An exemption is a deliberate non-gate: neither half of the
                # shape may be present.
                assert policy.tx_only is False, (model, path)
                assert policy.available_when == (), (model, path)
                ungated.add((model, str(path)))

    assert ungated == _TX_METER_GATE_EXEMPTIONS
    # The MOR-2540 regression subject must be in the gated set.
    for name in ("power", "swr", "alc", "comp"):
        assert ("IC-7610", f"global.meters.{name}") in gated


def test_ftx1_gates_nothing_on_dual_receive() -> None:
    """Bench 2026-09-18 (MOR-2511): every SUB-side read answers SUB's own
    value in single receive, so no SUB path is gated on dual receive."""

    acquisition = get_radio_profile("FTX-1").state_acquisition
    assert acquisition is not None

    sub_paths = (
        FieldPath.active("sub", "freq_mode", "freq_hz"),
        FieldPath.active("sub", "freq_mode", "mode"),
        FieldPath.active("sub", "freq_mode", "filter_width"),
        FieldPath.receiver("sub", "meters", "s_meter"),
        FieldPath.receiver("sub", "operator_controls", "af_level"),
        FieldPath.receiver("sub", "operator_controls", "rf_gain"),
        FieldPath.receiver("sub", "operator_controls", "squelch"),
        FieldPath.receiver("sub", "operator_controls", "repeater_shift"),
    )
    for path in sub_paths:
        assert acquisition.policy_for(path).available_when == (), path

    # dual_watch is still polled: the web state publishes it for display
    # (tests/test_web_server_coverage.py asserts the ``dualWatch`` key).
    dual_watch = FieldPath.global_("tx_state", "dual_watch")
    assert acquisition.capability_for(dual_watch).can_poll
    assert acquisition.policy_for(dual_watch).available_when == ()


# ── MOR-2540: TX-target source coverage ─────────────────────────────────────

_TX_TARGET_PATH = FieldPath.global_("tx_state", "tx_target")

#: Profiles with a TX or tuner capability and NO TX-target source, each with
#: the recorded reason. Membership is a deliberate decision, not an
#: oversight: the guard below fails for any TX/tuner profile that is neither
#: sourced nor listed here.
_TX_TARGET_UNSUPPORTED: dict[str, str] = {
    "IC-9700": (
        "taken out of MOR-2540 round 2: its band-selection and split "
        "semantics are unverified and no IC-9700 is on the bench, so no "
        "receiver-labelling rule is declared for it; MOR-2567 covers its "
        "other data problems"
    ),
    "TX-500": (
        "kenwood_cat profile with no backend (backends/factory.py has no "
        "TX-500 route); no TX-target read is wired for the Lab599 CAT set"
    ),
    "X6100": (
        "backends/factory.py refuses the model (only the rigctld client "
        "reaches the radio); the Xiegu CI-V dialect declares no "
        "transmit-frequency read"
    ),
    "X6200": (
        "Xiegu-dialect CI-V profile: no transmit-frequency read is declared, "
        "and the RadioPoller derivation requires vfo_readback "
        '"selected_unselected", which this profile does not carry'
    ),
}


def _declared_tx_target_source(profile: Any) -> str | None:
    """Name the mechanism that produces ``global.tx_state.tx_target``, if any."""

    if profile.vfo_readback == "selected_unselected":
        return "RadioPoller selected/unselected derivation (MOR-1496)"
    acquisition = profile.state_acquisition
    if acquisition is None:
        return None
    if not acquisition.capability_for(_TX_TARGET_PATH).can_poll:
        return None
    if provider_uses_civ_acquisition(acquisition.provider):
        from rigplane.runtime._state_queries import (
            acquisition_query_resolver_for_profile,
        )

        if acquisition_query_resolver_for_profile(profile)(_TX_TARGET_PATH) is None:
            return None
        return "CI-V get_tx_target read"
    return f"{acquisition.provider} native read"


def test_ic7610_declares_a_polled_1c03_tx_target() -> None:
    """MOR-2540: the IC-7610 polls the radio's own transmit-frequency read
    (CI-V 1C 03) instead of deriving the target from MAIN, and declares the
    data rule that labels the transceiver-wide reply by split."""

    from rigplane.runtime._state_queries import acquisition_query_resolver_for_profile

    profile = get_radio_profile("IC-7610")
    acquisition = profile.state_acquisition
    assert acquisition is not None
    capability = acquisition.capability_for(_TX_TARGET_PATH)
    assert capability.can_poll is True
    policy = acquisition.policy_for(_TX_TARGET_PATH)
    assert policy.cadence_seconds == 1.0
    assert policy.freshness_ttl_seconds == 4.0
    assert policy.freshness_ttl_seconds >= 2 * policy.cadence_seconds
    assert policy.adaptive_decay.enabled is False
    assert profile.command_map is not None
    assert profile.command_map.get("get_tx_target") == (0x1C, 0x03)
    assert profile.tx_receiver_rule == "main_unless_split"
    query = acquisition_query_resolver_for_profile(profile)(_TX_TARGET_PATH)
    assert query is not None
    assert (query.command, query.sub, query.data, query.receiver) == (
        0x1C,
        0x03,
        b"",
        None,
    )


def test_every_tx_capable_profile_declares_a_tx_target_source() -> None:
    """MOR-2540: a profile with a TX or tuner capability must say where its
    TX target comes from — a native read or the MOR-1496 derivation — or be
    listed in ``_TX_TARGET_UNSUPPORTED`` with the reason.

    Fails for a repeat of the IC-7610 gap: a main_sub profile whose
    derivation silently never runs (``vfo_readback`` defaults to ``"none"``)
    and whose profile declares no native read either, leaving TUNE and the
    web TX authority fail-closed for the radio's whole lifetime.
    """

    sourced: dict[str, str] = {}
    unsupported_seen: dict[str, str] = {}
    for model, rig in sorted(discover_rigs(RIGS_DIR).items()):
        profile = rig.to_profile()
        features = set(profile.capabilities)
        if "tx" not in features and "tuner" not in features:
            continue
        source = _declared_tx_target_source(profile)
        if source is not None:
            sourced[model] = source
            assert model not in _TX_TARGET_UNSUPPORTED, (
                f"{model}: has a TX-target source ({source}) yet is still "
                "listed in _TX_TARGET_UNSUPPORTED — remove the stale entry"
            )
            continue
        reason = _TX_TARGET_UNSUPPORTED.get(model)
        assert reason is not None, (
            f"{model}: TX/tuner capability but no TX-target source and no "
            "recorded reason — declare a source (a native transmit-frequency "
            "read, or the selected_unselected derivation) or add the model "
            "to _TX_TARGET_UNSUPPORTED with the reason"
        )
        unsupported_seen[model] = reason

    assert unsupported_seen == _TX_TARGET_UNSUPPORTED
    # The MOR-2540 regression subject is sourced by the radio's own read;
    # IC-9700 stays unsupported (see its entry above).
    assert sourced.get("IC-7610") == "CI-V get_tx_target read"
