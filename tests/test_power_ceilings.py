"""MOR-3634: per-band and per-power-source rated-power ceilings, with clamp.

The shipped profiles carry one flat ``[power].max_watts``; the manuals differ
per band and per power source (IC-9700: 144 MHz 100 W / 430 MHz 75 W /
1.2 GHz 10 W, tier B- — not hardware-verified; IC-705: 10 W on DC, 5 W on
battery). The failure this pins: an absolute-watt write above the rated power
of the active receiver's band or of the power source — from band stacking
carrying a wide-band setting into a narrow band, an operator mistake, or a
percent-to-watt conversion that differs per band.

The mechanism is radio-agnostic profile data: ``[power.ceilings_w]`` (band
name → watts) and ``[power.sources]`` (source name → watts), resolved by
``rigplane.profiles.power_ceilings`` and applied where ``max_watts`` already
acts — admission (``command_service.py: resolve_power_level_target`` inputs),
the backend's ``set_power`` floor site, and the watt scale the UI projects
(``_normalize_power_level`` / ``_power_level_max_value``). A profile without
the tables keeps today's flat-``max_watts`` behaviour (the FTX-1: its
per-band rows are unverified, so it declares none).
"""

from __future__ import annotations

import asyncio
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from rigplane.backends.yaesu_cat.observations import YaesuObservationAdapter
from rigplane.backends.yaesu_cat.radio import YaesuCatRadio
from rigplane.core.command_service import resolve_power_level_target
from rigplane.core.radio_state import RadioState
from rigplane.core.state_acquisition_policy import RadioAcquisitionProfile
from rigplane.core.state_pipeline_contracts import (
    FieldPath,
    Observation,
    SourceMetadata,
)
from rigplane.core.state_store import StateStore
from rigplane.core.tx_target import KnownTxTarget, UnknownTxTarget
from rigplane.profiles import get_radio_profile
from rigplane.profiles.power_ceilings import (
    active_receiver_freq_hz,
    band_frequency_hz_from_snapshot,
    effective_power_max_watts,
    observed_band_freq_hz,
    resolve_power_ceiling_watts,
)
from rigplane.rig_loader import RigLoadError, load_rig
from rigplane.web.runtime_helpers import build_public_state_payload_from_snapshot

_RIGS_DIR = Path(__file__).parents[1] / "rigs"

# Minimal loadable rig with two declared bands, so ceiling keys have bands
# to be validated against (self-contained: no shipped file is patched).
_MINIMAL_BANDS_TOML = """\
[radio]
id = "test_bands"
model = "TEST-BANDS"
civ_addr = 0x94
receiver_count = 1
has_lan = true
has_wifi = false

[spectrum]
seq_max = 11
amp_max = 160
data_len_max = 475

[capabilities]
features = ["audio", "scope", "meters", "tx"]

[modes]
list = ["USB", "LSB", "CW"]

[filters]
list = ["FIL1", "FIL2"]

[vfo]
scheme = "ab"

[[freq_ranges.ranges]]
label = "2m"
start_hz = 144000000
end_hz = 148000000

[[freq_ranges.ranges.bands]]
name = "2m"
start_hz = 144000000
end_hz = 148000000
default_hz = 145000000

[[freq_ranges.ranges]]
label = "70cm"
start_hz = 430000000
end_hz = 450000000

[[freq_ranges.ranges.bands]]
name = "70cm"
start_hz = 430000000
end_hz = 450000000
default_hz = 435000000

[commands]
get_freq = [0x03]
set_freq = [0x05]

[commands.overrides]
"""


def _write_rig(tmp_path: Path, extra: str, name: str = "test.toml") -> Path:
    path = tmp_path / name
    path.write_text(_MINIMAL_BANDS_TOML + extra, encoding="utf-8")
    return path


# ── Shipped profile data ─────────────────────────────────────────


def test_ic9700_profile_declares_per_band_ceilings():
    profile = get_radio_profile("IC-9700")
    assert profile.power_ceilings_w == {"2m": 100, "70cm": 75, "23cm": 10}
    assert profile.power_sources_w is None


def test_ic705_profile_declares_per_source_ceilings():
    profile = get_radio_profile("IC-705")
    assert profile.power_sources_w == {"dc": 10, "battery": 5}
    assert profile.power_ceilings_w is None


def test_ftx1_profile_declares_no_per_band_rows():
    # The FTX-1's per-band rows are unverified (field head 5-10 W versus
    # SPA-1 5-100 W): no invented numbers, the flat max/floor pair stays.
    profile = get_radio_profile("FTX-1")
    assert profile.power_ceilings_w is None
    assert profile.power_sources_w is None
    assert profile.max_watts == 100
    assert profile.min_watts == 5


# ── Loader validation ─────────────────────────────────────────────


def test_loader_parses_power_tables_into_profile(tmp_path):
    p = _write_rig(
        tmp_path,
        "\n[power]\nmax_watts = 75\n\n[power.ceilings_w]\n2m = 75\n70cm = 50\n"
        "\n[power.sources]\ndc = 75\nbattery = 50\n",
    )

    rig = load_rig(p)
    profile = rig.to_profile()

    assert rig.power_ceilings_w == {"2m": 75, "70cm": 50}
    assert rig.power_sources_w == {"dc": 75, "battery": 50}
    assert profile.power_ceilings_w == {"2m": 75, "70cm": 50}
    assert profile.power_sources_w == {"dc": 75, "battery": 50}


@pytest.mark.parametrize(
    ("extra", "match"),
    [
        # A ceiling above the radio's own flat maximum is a profile error.
        (
            "\n[power]\nmax_watts = 75\n\n[power.ceilings_w]\n2m = 76\n",
            r"ceilings_w\.2m must be in 1\.\.max_watts \(75\), got 76",
        ),
        (
            "\n[power]\nmax_watts = 75\n\n[power.ceilings_w]\n2m = 0\n",
            r"ceilings_w\.2m must be in 1\.\.max_watts \(75\), got 0",
        ),
        (
            "\n[power]\nmax_watts = 75\n\n[power.ceilings_w]\n2m = 7.5\n",
            r"ceilings_w\.2m must be an integer",
        ),
        (
            "\n[power]\nmax_watts = 75\n\n[power.sources]\ndc = true\n",
            r"sources\.dc must be an integer",
        ),
        # Ceiling keys must name a declared band.
        (
            "\n[power]\nmax_watts = 75\n\n[power.ceilings_w]\n40m = 50\n",
            r"ceilings_w names no declared band.*40m",
        ),
        # Both tables require the flat maximum to exist.
        (
            "\n[power]\n\n[power.ceilings_w]\n2m = 50\n",
            r"ceilings_w requires \[power\]\.max_watts",
        ),
        (
            "\n[power]\n\n[power.sources]\ndc = 50\n",
            r"sources requires \[power\]\.max_watts",
        ),
        # An empty table declares nothing: refuse it loudly.
        (
            "\n[power]\nmax_watts = 75\n\n[power.ceilings_w]\n",
            r"ceilings_w must be a non-empty table",
        ),
        # A floor above any declared ceiling makes that row unreachable.
        (
            "\n[power]\nmax_watts = 75\nmin_watts = 60\n\n"
            "[power.ceilings_w]\n2m = 75\n70cm = 50\n",
            r"min_watts must be <= every declared",
        ),
    ],
)
def test_loader_refuses_malformed_power_tables(tmp_path, extra, match):
    with pytest.raises(RigLoadError, match=match):
        load_rig(_write_rig(tmp_path, extra))


# ── Resolver (profile data + band/source facts) ──────────────────


@pytest.mark.parametrize(
    ("freq_hz", "expected"),
    [
        (145_500_000, 100),
        (435_000_000, 75),
        (1_297_000_000, 10),
        # Unknown or not-yet-observed frequency: the lowest declared
        # ceiling, never the widest band's.
        (None, 10),
        (0, 10),
        # A frequency no declared band covers resolves the same way.
        (7_100_000, 10),
    ],
)
def test_band_ceiling_follows_the_active_band(freq_hz, expected):
    profile = get_radio_profile("IC-9700")
    assert resolve_power_ceiling_watts(profile, freq_hz=freq_hz) == expected


@pytest.mark.parametrize(
    ("power_source", "expected"),
    [
        ("dc", 10),
        ("battery", 5),
        # The state does not know the source yet: the lower ceiling.
        (None, 5),
        ("generator", 5),
    ],
)
def test_source_ceiling_follows_the_known_source(power_source, expected):
    profile = get_radio_profile("IC-705")
    assert resolve_power_ceiling_watts(profile, power_source=power_source) == expected


def test_band_and_source_ceiling_combine_as_the_minimum():
    profile = replace(
        get_radio_profile("IC-9700"), power_sources_w={"dc": 100, "battery": 50}
    )
    assert (
        resolve_power_ceiling_watts(
            profile, freq_hz=435_000_000, power_source="battery"
        )
        == 50
    )
    assert (
        resolve_power_ceiling_watts(profile, freq_hz=435_000_000, power_source="dc")
        == 75
    )


def test_profile_without_tables_resolves_no_ceiling():
    profile = get_radio_profile("FTX-1")
    assert resolve_power_ceiling_watts(profile, freq_hz=145_000_000) is None
    # Today's flat max survives untouched as the effective scale.
    assert effective_power_max_watts(profile, freq_hz=145_000_000) == 100


def test_effective_max_follows_the_band_or_falls_back_to_flat_max():
    profile = get_radio_profile("IC-9700")
    assert effective_power_max_watts(profile, freq_hz=435_000_000) == 75
    assert effective_power_max_watts(profile, freq_hz=None) == 10


# ── Active-receiver frequency from the radio state ───────────────


def _state_double(*, active: str = "MAIN", main_hz: int = 0, sub_hz: int = 0):
    state = RadioState()
    state.active = active
    state.main.freq = main_hz
    state.sub.freq = sub_hz
    return state


def test_active_receiver_freq_reads_the_selected_receiver():
    radio = SimpleNamespace(
        radio_state=_state_double(main_hz=435_000_000, sub_hz=145_000_000)
    )
    assert active_receiver_freq_hz(radio) == 435_000_000
    radio.radio_state.active = "SUB"
    assert active_receiver_freq_hz(radio) == 145_000_000


def test_active_receiver_freq_is_none_when_unobserved():
    assert active_receiver_freq_hz(SimpleNamespace(radio_state=_state_double())) is None
    assert active_receiver_freq_hz(SimpleNamespace()) is None


# ── Which observation defines the band ────────────────────────────


def _store_with_observations(*observations: Observation) -> StateStore:
    store = StateStore()
    for observation in observations:
        store.apply(observation)
    return store


def test_known_tx_target_frequency_defines_the_band():
    """Split/cross-band: the TRANSMITTING receiver's band governs."""
    store = _store_with_observations(
        _snapshot_observation(
            FieldPath.active("main", "freq_mode", "freq_hz"),
            145_000_000,
            provider="test",
        ),
        _snapshot_observation(
            FieldPath.active("sub", "freq_mode", "freq_hz"),
            435_000_000,
            provider="test",
        ),
        _snapshot_observation(
            FieldPath.global_("slow_state", "active"), "MAIN", provider="test"
        ),
        _snapshot_observation(
            FieldPath.global_("tx_state", "tx_target"),
            KnownTxTarget(receiver="SUB", slot=None, frequency_hz=435_000_000),
            provider="test",
        ),
    )
    profile = replace(
        get_radio_profile("FTX-1"), power_ceilings_w={"2m": 75, "70cm": 50}
    )

    freq_hz = band_frequency_hz_from_snapshot(store.snapshot())
    assert freq_hz == 435_000_000
    # The active receiver sits on 2 m (75 W) but TX routes to 70 cm: 50 W.
    assert effective_power_max_watts(profile, freq_hz=freq_hz) == 50


def test_absent_tx_target_uses_the_active_receiver():
    """A radio that never observes tx_target (e.g. single-receiver) reads focus."""
    store = _store_with_observations(
        _snapshot_observation(
            FieldPath.active("main", "freq_mode", "freq_hz"),
            145_000_000,
            provider="test",
        ),
        _snapshot_observation(
            FieldPath.global_("slow_state", "active"), "MAIN", provider="test"
        ),
    )
    assert band_frequency_hz_from_snapshot(store.snapshot()) == 145_000_000


def test_observed_unknown_tx_target_is_conservative():
    """A tx_target the authority cannot name resolves no band at all.

    Pre-review round 2: substituting receiver focus here would let an
    unreadable FT answer authorize the focused (possibly wider) band
    while TX is routed elsewhere — the lowest declared ceiling applies.
    """
    store = _store_with_observations(
        _snapshot_observation(
            FieldPath.active("main", "freq_mode", "freq_hz"),
            145_000_000,
            provider="test",
        ),
        _snapshot_observation(
            FieldPath.global_("tx_state", "tx_target"),
            UnknownTxTarget(reason="not-observed"),
            provider="test",
        ),
    )
    profile = replace(
        get_radio_profile("FTX-1"), power_ceilings_w={"2m": 75, "70cm": 50}
    )
    assert band_frequency_hz_from_snapshot(store.snapshot()) is None
    assert effective_power_max_watts(profile, freq_hz=None) == 50


def test_stale_band_context_resolves_no_band():
    """A frequency the freshness driver aged to STALE is unobserved evidence."""
    store = StateStore()
    store.apply(
        Observation(
            path=FieldPath.active("main", "freq_mode", "freq_hz"),
            value=145_000_000,
            source=SourceMetadata("poll_response", "test", "fake", "test"),
            timestamp_monotonic=10.0,
            max_age=1.0,
        )
    )
    store.mark_stale_due(now=20.0)

    assert band_frequency_hz_from_snapshot(store.snapshot()) is None


def test_observed_band_freq_prefers_the_canonical_store_over_the_mirror():
    """Production polling feeds the store; the mirror may lag or stay empty.

    With a store attached its answer is final even when ``None`` (an
    emptied/provider-reset store must not resurrect the mirror's
    previous-connection frequency).
    """
    store = _store_with_observations(
        _snapshot_observation(
            FieldPath.active("main", "freq_mode", "freq_hz"),
            435_000_000,
            provider="test",
        )
    )
    radio = SimpleNamespace(
        _state_store=store,
        radio_state=_state_double(main_hz=145_000_000),
    )
    assert observed_band_freq_hz(radio) == 435_000_000

    emptied = _store_with_observations()
    assert (
        observed_band_freq_hz(
            SimpleNamespace(
                _state_store=emptied,
                radio_state=_state_double(main_hz=145_000_000),
            )
        )
        is None
    )
    # Without a store the compatibility mirror answers.
    assert (
        observed_band_freq_hz(
            SimpleNamespace(radio_state=_state_double(main_hz=145_000_000))
        )
        == 145_000_000
    )


# ── Admission clamp in the watts domain ───────────────────────────


@pytest.mark.parametrize(
    ("value", "ceiling", "floor", "expected_native"),
    [
        # Above the active band's ceiling: clamped down, exactly as a
        # flat max_watts clamps today.
        (90, 75, None, 75),
        (300, 75, None, 75),
        (75, 75, None, 75),
        (10, 75, None, 10),
        # The floor keeps lifting within the ceiling's range.
        (2, 75, 5, 5),
        (0, 75, 5, 5),
    ],
)
def test_watts_admission_clamps_to_the_ceiling(value, ceiling, floor, expected_native):
    native, level = resolve_power_level_target(
        value,
        power_native_unit="watts",
        power_max_watts=ceiling,
        power_min_watts=floor,
    )
    assert native == expected_native
    assert level == expected_native / ceiling


def test_normalized_write_scales_by_the_ceiling():
    native, level = resolve_power_level_target(
        1.0, power_native_unit="watts", power_max_watts=75
    )
    assert native == 75
    assert level == 1.0


# ── Backend clamp (paths that bypass admission) ───────────────────


@pytest.fixture()
def watts_radio_with_ceilings():
    """A YaesuCatRadio on the ftx1 profile plus per-band ceiling rows."""
    config = replace(
        load_rig(_RIGS_DIR / "ftx1.toml"),
        power_ceilings_w={"2m": 75, "70cm": 50},
    )
    radio = YaesuCatRadio("/dev/null", profile=config)
    radio._transport._connected = True
    radio._transport.write = AsyncMock()
    return radio


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("freq_hz", "requested", "wire"),
    [
        # 2 m ceiling 75: a request under it is untouched on the wire.
        (145_000_000, 60, "PC2060;"),
        # 70 cm ceiling 50: a 90 W request is clamped down.
        (435_000_000, 90, "PC2050;"),
        # Unknown band: the lowest declared ceiling.
        (0, 90, "PC2050;"),
    ],
)
async def test_backend_set_power_clamps_to_band_ceiling(
    watts_radio_with_ceilings, freq_hz, requested, wire
):
    radio = watts_radio_with_ceilings
    radio.radio_state.main.freq = freq_hz
    await radio.set_power(requested)
    radio._transport.write.assert_called_once_with(wire)


@pytest.mark.asyncio
async def test_backend_floor_still_lifts_under_a_ceiling(watts_radio_with_ceilings):
    radio = watts_radio_with_ceilings
    radio.radio_state.main.freq = 435_000_000
    await radio.set_power(3)  # 5 W floor lifts, then the 50 W ceiling caps
    radio._transport.write.assert_called_once_with("PC2005;")


@pytest.mark.asyncio
async def test_backend_without_tables_keeps_todays_wire():
    radio = YaesuCatRadio("/dev/null", profile=load_rig(_RIGS_DIR / "ftx1.toml"))
    radio._transport._connected = True
    radio._transport.write = AsyncMock()
    radio.radio_state.main.freq = 435_000_000
    await radio.set_power(90)
    radio._transport.write.assert_called_once_with("PC2090;")


@pytest.mark.asyncio
async def test_backend_band_change_then_power_uses_destination_ceiling():
    """Pre-review round 2: set_freq must refresh the band the clamp reads.

    With a canonical store attached (production shape), a direct-backend
    band change echoes into the store under the same lock, so the next
    power write resolves the DESTINATION band instead of the store's
    previous (wider) one.
    """
    config = replace(
        load_rig(_RIGS_DIR / "ftx1.toml"),
        power_ceilings_w={"2m": 75, "70cm": 50},
    )
    radio = YaesuCatRadio("/dev/null", profile=config)
    radio._transport._connected = True
    radio._transport.write = AsyncMock()
    radio._state_store = StateStore()
    radio._state_store.apply(
        _snapshot_observation(
            FieldPath.active("main", "freq_mode", "freq_hz"),
            145_000_000,
            provider="test",
        )
    )

    await radio.set_freq(435_000_000)
    await radio.set_power(90)

    wires = [call.args[0] for call in radio._transport.write.await_args_list]
    assert wires == ["FA435000000;", "PC2050;"]


@pytest.mark.asyncio
async def test_backend_power_waits_for_an_in_flight_band_change():
    """A power write racing a band change resolves the new band (lock).

    The frequency write pauses inside the lock; the concurrent power
    write must not resolve the ceiling until the band change (wire and
    store echo) completes, then clamp to the destination band.
    """
    config = replace(
        load_rig(_RIGS_DIR / "ftx1.toml"),
        power_ceilings_w={"2m": 75, "70cm": 50},
    )
    radio = YaesuCatRadio("/dev/null", profile=config)
    radio._transport._connected = True
    radio._state_store = StateStore()
    radio._state_store.apply(
        _snapshot_observation(
            FieldPath.active("main", "freq_mode", "freq_hz"),
            145_000_000,
            provider="test",
        )
    )
    freq_started = asyncio.Event()
    freq_release = asyncio.Event()
    wire_mock = AsyncMock()

    async def gated_write(payload: str, **kwargs: object) -> None:
        if payload.startswith("FA"):
            freq_started.set()
            await freq_release.wait()
        await wire_mock(payload, **kwargs)

    radio._transport.write = gated_write

    freq_task = asyncio.create_task(radio.set_freq(435_000_000))
    await asyncio.wait_for(freq_started.wait(), timeout=1.0)
    power_task = asyncio.create_task(radio.set_power(90))
    await asyncio.sleep(0)  # let the power write reach the lock
    assert not power_task.done(), "power write must wait for the band change"
    freq_release.set()
    await asyncio.wait_for(freq_task, timeout=1.0)
    await asyncio.wait_for(power_task, timeout=1.0)

    wires = [call.args[0] for call in wire_mock.await_args_list]
    assert wires == ["FA435000000;", "PC2050;"]


# ── Web handler seam: enqueue set_power on a watts radio ─────────


class _QueueRecorder:
    def __init__(self) -> None:
        self.items: list[object] = []

    def put(self, item, **_metadata: object) -> None:
        self.items.append(item)

    def put_ordered(self, item, **_metadata: object) -> None:
        self.items.append(item)


def _watts_handler(profile: Any, state: RadioState):
    from rigplane.web.handlers.control import ControlHandler

    radio = SimpleNamespace(
        capabilities={"tx"},
        profile=profile,
        native_power_unit="watts",
        radio_state=state,
        set_rf_power=AsyncMock(),
        get_rf_power=AsyncMock(return_value=0),
        get_powerstat=AsyncMock(return_value=True),
        set_powerstat=AsyncMock(),
    )
    server = SimpleNamespace(
        command_queue=_QueueRecorder(), command_state_store=StateStore()
    )
    return (
        radio,
        ControlHandler(
            SimpleNamespace(send_text=AsyncMock(), recv=AsyncMock()),
            radio,
            "9.9.9",
            "IC-9700",
            server=server,
            session_id="power-ceilings",
        ),
    )


async def _enqueued_level(profile: Any, state: RadioState, level: int) -> int:
    _radio, handler = _watts_handler(profile, state)
    await handler._enqueue_command("set_power", {"level": level})
    [command] = handler._server.command_queue.items
    assert type(command).__name__ == "SetPower"
    assert command.unit == "watts"
    return command.level


@pytest.mark.asyncio
async def test_handler_clamps_watts_write_to_the_active_band_ceiling():
    state = _state_double(main_hz=435_000_000)
    assert await _enqueued_level(get_radio_profile("IC-9700"), state, 90) == 75


@pytest.mark.asyncio
async def test_handler_band_change_then_too_high_write_is_clamped():
    profile = get_radio_profile("IC-9700")
    state = _state_double(main_hz=145_500_000)
    # On 2 m the 90 W request passes under the 100 W ceiling…
    assert await _enqueued_level(profile, state, 90) == 90
    # …a band change to 1.2 GHz must not carry it across.
    state.main.freq = 1_297_000_000
    assert await _enqueued_level(profile, state, 90) == 10


@pytest.mark.asyncio
async def test_handler_unknown_band_clamps_to_the_lowest_declared_ceiling():
    state = _state_double()  # no frequency observed yet
    assert await _enqueued_level(get_radio_profile("IC-9700"), state, 90) == 10


@pytest.mark.asyncio
async def test_handler_profile_without_tables_keeps_flat_max_and_floor():
    state = _state_double(main_hz=435_000_000)
    profile = get_radio_profile("FTX-1")
    assert await _enqueued_level(profile, state, 90) == 90
    assert await _enqueued_level(profile, state, 150) == 100
    assert await _enqueued_level(profile, state, 2) == 5


@pytest.mark.asyncio
async def test_handler_raw_255_radio_keeps_the_raw_wire_scale():
    """The shipped IC-9700 rides raw-255 CI-V: the ceiling never re-scales it.

    The watts doubles above force ``native_power_unit = "watts"`` to
    exercise the radio-agnostic mechanism; on the real Icom wire a bare
    int is the documented 0-255 raw value and a float scales onto 0-255,
    exactly as before the ceilings landed.
    """
    from rigplane.web.handlers.control import ControlHandler

    radio = SimpleNamespace(
        capabilities={"tx"},
        profile=get_radio_profile("IC-9700"),
        native_power_unit="raw_255",
        radio_state=_state_double(main_hz=1_297_000_000),
        set_rf_power=AsyncMock(),
        get_rf_power=AsyncMock(return_value=0),
        get_powerstat=AsyncMock(return_value=True),
        set_powerstat=AsyncMock(),
    )
    server = SimpleNamespace(
        command_queue=_QueueRecorder(), command_state_store=StateStore()
    )
    handler = ControlHandler(
        SimpleNamespace(send_text=AsyncMock(), recv=AsyncMock()),
        radio,
        "9.9.9",
        "IC-9700",
        server=server,
        session_id="power-ceilings-raw",
    )

    await handler._enqueue_command("set_power", {"level": 90})
    await handler._enqueue_command("set_power", {"level": 1.0})

    [first, second] = server.command_queue.items
    assert (first.level, first.unit) == (90, "raw_255")
    assert (second.level, second.unit) == (255, "raw_255")


# ── Watt scale shown per band (observation + public projection) ──


def _yaesu_poll_radio(profile: Any, state: RadioState) -> MagicMock:
    radio = MagicMock()
    radio.profile = profile
    radio.radio_state = state
    # No canonical store attached: the compatibility mirror answers.
    radio._state_store = None
    radio.capabilities = {"tx"}
    radio.read_power = AsyncMock(return_value=(2, 40))
    # mic_gain rides the same lane ungated by any capability.
    radio.read_mic_gain = AsyncMock(return_value=40)
    return radio


def _observation_profile() -> RadioAcquisitionProfile:
    profile = get_radio_profile("FTX-1")
    assert profile.state_acquisition is not None
    return profile.state_acquisition


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("freq_hz", "expected_fraction"),
    [
        (145_000_000, 40 / 75),  # 2 m: full scale is the 75 W ceiling
        (435_000_000, 40 / 50),  # 70 cm: full scale is the 50 W ceiling
        (0, 40 / 50),  # unknown band: the lowest declared ceiling
    ],
)
async def test_power_level_observation_scales_watts_by_band_ceiling(
    freq_hz, expected_fraction
):
    profile = replace(
        get_radio_profile("FTX-1"),
        power_ceilings_w={"2m": 75, "70cm": 50},
    )
    radio = _yaesu_poll_radio(profile, _state_double(main_hz=freq_hz))

    store = StateStore()
    for observation in await YaesuObservationAdapter(
        radio, profile=_observation_profile(), clock=lambda: 123.456
    ).poll_tx_controls():
        store.apply(observation)

    assert store.snapshot().field(
        "global.operator_controls.power_level"
    ).value == pytest.approx(expected_fraction)


@pytest.mark.asyncio
async def test_over_ceiling_readback_saturates_through_the_public_projection():
    """A readback above the resolved ceiling stays a valid 0.0-1.0 fraction.

    Pre-review round 1: with the band unobserved the conservative 50 W
    ceiling applies while the radio still reports a legal-on-2m 60 W
    setpoint; ``60 / 50 = 1.2`` used to leave the normalized domain and
    the public projection divided by the scale a second time (2.4 %).
    The observation saturates at 1.0 and the projection keeps it there.
    """
    profile = replace(
        get_radio_profile("FTX-1"),
        power_ceilings_w={"2m": 75, "70cm": 50},
    )
    radio = _yaesu_poll_radio(profile, _state_double(main_hz=0))
    radio.read_power = AsyncMock(return_value=(2, 60))

    store = StateStore()
    for observation in await YaesuObservationAdapter(
        radio, profile=_observation_profile(), clock=lambda: 123.456
    ).poll_tx_controls():
        store.apply(observation)

    assert store.snapshot().field(
        "global.operator_controls.power_level"
    ).value == pytest.approx(1.0)
    payload = build_public_state_payload_from_snapshot(
        store.snapshot(),
        radio=radio,
        receiver_count=2,
    )
    assert payload["powerLevel"] == pytest.approx(1.0)


def _snapshot_observation(
    path: FieldPath, value: Any, *, provider: str = "yaesu_cat"
) -> Observation:
    return Observation(
        path=path,
        value=value,
        source=SourceMetadata("poll_response", provider, "fake", "test"),
        timestamp_monotonic=10.0,
    )


@pytest.mark.parametrize(
    ("freq_hz", "raw_watts", "expected"),
    [
        (145_000_000, 50, 50 / 75),  # 2 m scale
        (435_000_000, 50, 50 / 50),  # 70 cm scale: full scale
        (None, 50, 50 / 50),  # unknown band: lowest declared ceiling
    ],
)
def test_public_power_level_projection_divides_by_band_ceiling(
    freq_hz, raw_watts, expected
):
    profile = replace(
        get_radio_profile("FTX-1"),
        power_ceilings_w={"2m": 75, "70cm": 50},
    )

    store = StateStore()
    store.apply(
        _snapshot_observation(
            FieldPath.global_("operator_controls", "power_level"), raw_watts
        )
    )
    if freq_hz is not None:
        store.apply(
            _snapshot_observation(
                FieldPath.active("0", "freq_mode", "freq_hz"),
                freq_hz,
                provider="test",
            )
        )

    payload = build_public_state_payload_from_snapshot(
        store.snapshot(),
        radio=SimpleNamespace(profile=profile),
        receiver_count=1,
    )

    assert payload["powerLevel"] == pytest.approx(expected)
