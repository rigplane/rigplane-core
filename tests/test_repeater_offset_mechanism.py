"""Contract suite for the radio-agnostic repeater-offset mechanism (MOR-3624 O1).

Later shim PRs (O2 Icom CI-V ``0C``/``0D``, O3 FTX-1 ``EX``) plug in by
appending ONE entry to ``SHIM_FIXTURES`` (profile model name + read/write
frame expectations); the suite itself names no radio model. Unsupported =
not declared in the profile = refused before anything reaches the wire.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest

from rigplane.core.capabilities import CAP_REPEATER_OFFSET
from rigplane.core.command_dispatch import (
    CommandUnsupportedError,
    command_descriptor,
    execute_command_intent,
    prepare_command_intent,
)
from rigplane.core.radio_protocol import RepeaterOffsetCapable
from rigplane.core.radio_state import RadioState
from rigplane.core.state_pipeline_contracts import (
    DEFAULT_FIELD_REGISTRY,
    FieldPath,
    Observation,
    SourceMetadata,
)
from rigplane.core.state_store import StateStore
from rigplane.core.types import RepeaterOffsetBand, RepeaterOffsetProfile
from rigplane.profiles import RadioProfile
from rigplane.profiles.rig_loader import RigLoadError, load_rig
from rigplane.web.runtime_helpers import (
    _RECEIVER_OPERATOR_CONTROL_FIELDS,
    build_public_state_payload_from_snapshot,
    runtime_capabilities,
)
from rigplane.web.state_schema import ReceiverStatePublic

VHF = RepeaterOffsetBand(
    low_hz=144_000_000, high_hz=146_000_000, max_hz=1_000_000, step_hz=5_000
)
UHF = RepeaterOffsetBand(
    low_hz=430_000_000, high_hz=440_000_000, max_hz=10_000_000, step_hz=25_000
)
BANDS = RepeaterOffsetProfile(bands=(VHF, UHF))
MAIN_PATH = FieldPath.receiver("main", "operator_controls", "repeater_offset")


@dataclass(frozen=True)
class ShimFixture:
    """One radio's plug-in slot. Frame expectations stay empty until a shim
    PR lands wire codecs; the mechanism pins only the forwarded call."""

    profile_model: str  # never branched on
    bands: RepeaterOffsetProfile
    freq_hz: int  # receiver's current frequency; picks the guard's band
    accepted_hz: tuple[int, ...]
    refused_hz: tuple[int, ...]  # per-band refusals: sign, step, max
    write_frames: tuple[bytes, ...] = ()
    read_frames: tuple[bytes, ...] = ()


SHIM_FIXTURES = [
    ShimFixture("SYNTHETIC-OFFSET", BANDS, 145_000_000, (0, 600_000, 1_000_000), (-5_000, 600_001, 1_005_000))
]


class FakeOffsetRadio:
    """``RepeaterOffsetCapable`` double with the duck-typed seats the guard
    reads (``capabilities``, ``profile``, ``radio_state``)."""

    def __init__(self, profile: RadioProfile, *, freq_hz: int = 145_000_000) -> None:
        self.capabilities = set(profile.capabilities)
        self.profile = profile
        self.radio_state = RadioState()
        self.radio_state.main.freq = freq_hz
        self.writes: list[tuple[int, int]] = []

    def supports_command(self, command: str, *, receiver: int | None = None) -> bool:
        return True

    async def get_repeater_offset(self, receiver: int = 0) -> int:
        return self.writes[-1][0] if self.writes else 0

    async def set_repeater_offset(self, offset_hz: int, receiver: int = 0) -> None:
        self.writes.append((offset_hz, receiver))


def _profile(offset: RepeaterOffsetProfile | None = BANDS) -> RadioProfile:
    return RadioProfile(
        id="synthetic_offset",
        model="SYNTHETIC-OFFSET",
        civ_addr=0x00,
        receiver_count=1,
        capabilities=frozenset({CAP_REPEATER_OFFSET}) if offset else frozenset(),
        cmd29_routes=frozenset(),
        repeater_offset=offset,
    )


def _prepare(radio: FakeOffsetRadio, offset_hz: Any) -> None:
    prepare_command_intent(
        radio, "set_repeater_offset", {"offset_hz": offset_hz}, source="websocket"
    )


# ── Accepted values forward the exact call ───────────────────────


@pytest.mark.parametrize("fixture", SHIM_FIXTURES, ids=lambda f: f.profile_model)
async def test_accepted_offsets_forward_exact_call(fixture: ShimFixture) -> None:
    radio = FakeOffsetRadio(_profile(fixture.bands), freq_hz=fixture.freq_hz)
    assert fixture.write_frames == fixture.read_frames == ()  # no shim yet
    for offset in fixture.accepted_hz:
        intent = prepare_command_intent(
            radio, "set_repeater_offset", {"offset_hz": offset}, source="websocket"
        )
        await execute_command_intent(radio, intent)
    assert radio.writes == [(offset, 0) for offset in fixture.accepted_hz]


async def test_second_band_uses_its_own_step_and_max() -> None:
    radio = FakeOffsetRadio(_profile(), freq_hz=435_000_000)
    await execute_command_intent(
        radio,
        prepare_command_intent(
            radio, "set_repeater_offset", {"offset_hz": 5_000_000}, source="websocket"
        ),
    )
    assert radio.writes == [(5_000_000, 0)]
    with pytest.raises(ValueError, match="multiple"):
        _prepare(radio, 5_005_000)  # not a multiple of the UHF 25 kHz step
    assert radio.writes == [(5_000_000, 0)]


# ── Refusals happen before anything reaches the wire ─────────────


@pytest.mark.parametrize("fixture", SHIM_FIXTURES, ids=lambda f: f.profile_model)
def test_band_refusals_forward_nothing(fixture: ShimFixture) -> None:
    radio = FakeOffsetRadio(_profile(fixture.bands), freq_hz=fixture.freq_hz)
    for offset in fixture.refused_hz:
        with pytest.raises(ValueError):
            _prepare(radio, offset)
    assert radio.writes == []


@pytest.mark.parametrize("offset", [True, "600000", 0.6, None])
def test_non_int_offsets_are_refused_and_forward_nothing(offset: Any) -> None:
    radio = FakeOffsetRadio(_profile())
    with pytest.raises(ValueError):
        _prepare(radio, offset)
    assert radio.writes == []


@pytest.mark.parametrize("freq_hz", [0, 20_000_000])  # unknown; outside every band
def test_unbanded_frequency_is_refused_and_forwards_nothing(freq_hz: int) -> None:
    radio = FakeOffsetRadio(_profile(), freq_hz=freq_hz)
    with pytest.raises(ValueError, match="band"):
        _prepare(radio, 600_000)
    assert radio.writes == []


# ── Capability gating ────────────────────────────────────────────


def test_descriptor_declares_capability_and_tx_policy() -> None:
    descriptor = command_descriptor("set_repeater_offset")
    assert descriptor is not None
    assert descriptor.required_capability == CAP_REPEATER_OFFSET
    assert descriptor.tx_policy == "tx_safe"
    assert descriptor.argument_names == ("offset_hz", "receiver")


def test_capability_absent_refuses_and_projects_nothing() -> None:
    radio = FakeOffsetRadio(_profile(None))
    with pytest.raises(CommandUnsupportedError, match="repeater_offset"):
        _prepare(radio, 600_000)
    assert radio.writes == []
    assert CAP_REPEATER_OFFSET not in runtime_capabilities(radio)


class _GetOnlyRadio:
    capabilities = {"repeater_offset"}

    async def get_repeater_offset(self, receiver: int = 0) -> int:
        return 0


def test_protocol_is_runtime_checkable_and_gates_the_tag() -> None:
    assert isinstance(FakeOffsetRadio(_profile()), RepeaterOffsetCapable)
    assert CAP_REPEATER_OFFSET in runtime_capabilities(FakeOffsetRadio(_profile()))
    assert not isinstance(_GetOnlyRadio(), RepeaterOffsetCapable)
    assert CAP_REPEATER_OFFSET not in runtime_capabilities(_GetOnlyRadio())


# ── State, registry and public projection ────────────────────────


def _payload(store: StateStore) -> dict[str, Any]:
    return build_public_state_payload_from_snapshot(
        store.snapshot(), radio=None, receiver_count=1
    )


def test_registry_schema_and_operator_controls_declare_the_field() -> None:
    spec = DEFAULT_FIELD_REGISTRY.require(MAIN_PATH)
    assert spec.writable and spec.unit == "hz"
    assert "repeater_offset" in _RECEIVER_OPERATOR_CONTROL_FIELDS
    assert "repeaterOffset" in ReceiverStatePublic.model_fields


def test_projection_shows_the_hz_value_only_when_observed() -> None:
    assert _payload(StateStore())["main"].get("repeaterOffset") is None
    store = StateStore()
    store.apply(
        Observation(
            path=MAIN_PATH,
            value=600_000,
            source=SourceMetadata(
                source="poll_response",
                provider="offset_contract",
                transport="fake",
                native_id="offset",
            ),
            timestamp_monotonic=10.0,
            quality=("confirmed",),
        )
    )
    assert _payload(store)["main"]["repeaterOffset"] == 600_000


# ── Loader: the sanity guard's profile data ──────────────────────

_MINIMAL_TOML = """\
[radio]
id = "offset_test"
model = "Offset Test"
civ_addr = 0x94
receiver_count = 1
has_lan = false
has_wifi = false

[capabilities]
features = [{features}]

[modes]
list = ["USB", "FM"]

[filters]
list = ["FIL1"]

[vfo]
scheme = "ab"

[[freq_ranges.ranges]]
label = "VHF"
start_hz = 144000000
end_hz = 146000000

[commands]
get_freq = [0x03]
set_freq = [0x05]

{section}
"""

_BANDS = """\
[repeater_offset]

[[repeater_offset.bands]]
low_hz = 144000000
high_hz = 146000000
max_hz = 1000000
step_hz = 5000

[[repeater_offset.bands]]
low_hz = 430000000
high_hz = 440000000
max_hz = 10000000
step_hz = 25000
"""


def _load(tmp_path: Path, section: str = _BANDS, features: str = '"tx"') -> Any:
    path = tmp_path / "offset_test.toml"
    path.write_text(
        _MINIMAL_TOML.format(features=features, section=section), encoding="utf-8"
    )
    return load_rig(path)


def test_loader_parses_bands_into_config_and_profile(tmp_path: Path) -> None:
    rig = _load(tmp_path, features='"repeater_offset"')
    assert rig.repeater_offset == rig.to_profile().repeater_offset == BANDS
    assert _load(tmp_path, section="").repeater_offset is None


def test_declared_capability_without_section_is_rejected(tmp_path: Path) -> None:
    with pytest.raises(RigLoadError, match="repeater_offset"):
        _load(tmp_path, section="", features='"repeater_offset"')


@pytest.mark.parametrize(
    "section",
    [
        _BANDS.replace("430000000", "145000000", 1),  # overlapping bands
        _BANDS.replace("high_hz = 146000000", "high_hz = 144000000"),  # low >= high
        _BANDS.replace("max_hz = 1000000", "max_hz = 1000001"),  # step ∤ max
        _BANDS.replace("step_hz = 5000", "step_hz = 5000\nfoo = 1"),  # unknown key
    ],
    ids=["overlap", "inverted", "step", "unknown-key"],
)
def test_malformed_sections_are_rejected(tmp_path: Path, section: str) -> None:
    with pytest.raises(RigLoadError):
        _load(tmp_path, section=section)
