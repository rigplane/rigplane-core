"""MOR-3634: per-band and per-power-source rated-power ceilings, with clamp.

The shipped profiles carry one flat ``[power].max_watts``; the manuals differ
per band and per power source (IC-9700: 144 MHz 100 W / 430 MHz 75 W /
1.2 GHz 10 W, tier B- -- not hardware-verified; IC-705: 10 W on DC, 5 W on
battery). The failure this pins: an absolute-watt write above the rated power
of the transmitting band or of the power source -- from band stacking
carrying a wide-band setting into a narrow band, an operator mistake, or a
percent-to-watt conversion that differs per band.

The mechanism is radio-agnostic profile data: ``[power.ceilings_w]`` (band
name -> watts) and ``[power.sources]`` (source name -> watts), resolved by
``rigplane.profiles.power_ceilings``. The ceiling only CAPS a write: admission
(``command_service.py: resolve_power_level_target``) estimates it for the
expected readback, and the Yaesu backend's ``set_power`` is the authority at
the wire -- it re-reads the transmit band from the radio under its lock, so no
poll, echo or cached context can be out of date. The normalized 0.0-1.0 power
domain stays a fraction of the flat ``max_watts`` on every band. A profile
without the tables keeps today's behaviour (the FTX-1: its per-band rows are
unverified, so it declares none).
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import time
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from rigplane.backends.yaesu_cat.observations import YaesuObservationAdapter
from rigplane.backends.yaesu_cat.radio import YaesuCatRadio
from rigplane.backends.yaesu_cat.transport import CatTimeoutError
from rigplane.core.command_service import (
    command_intent_from_request,
    resolve_power_level_target,
)
from rigplane.core.radio_state import RadioState
from rigplane.core.state_pipeline_contracts import (
    FieldPath,
    Observation,
    SourceMetadata,
)
from rigplane.core.state_store import StateStore
from rigplane.core.tx_target import KnownTxTarget, UnknownTxTarget
from rigplane.profiles import get_radio_profile
from rigplane.profiles.power_ceilings import (
    band_frequencies_hz_from_snapshot,
    has_power_ceilings,
    observed_band_freqs_hz,
    resolve_power_ceiling_watts,
)
from rigplane.rig_loader import RigLoadError, load_rig
from rigplane.web.runtime_helpers import build_public_state_payload_from_snapshot
from rigplane.web.server import WebConfig, WebServer
from test_web_server import (
    _read_http_response,
    _ws_connect,
    _ws_recv_frame,
    _ws_send_text,
)

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

_IC9700 = "IC-9700"


@pytest.mark.parametrize(
    ("freqs_hz", "expected"),
    [
        ((145_500_000,), 100),
        ((435_000_000,), 75),
        ((1_297_000_000,), 10),
        # Unknown or not-yet-observed frequency: the lowest declared
        # ceiling, never the widest band's.
        ((None,), 10),
        ((0,), 10),
        ((), 10),
        # A frequency no declared band covers resolves the same way.
        ((7_100_000,), 10),
        # Several candidates for the transmitting band: the minimum wins,
        # and one unknown candidate drags it to the lowest row.
        ((145_500_000, 435_000_000), 75),
        ((145_500_000, None), 10),
    ],
)
def test_band_ceiling_is_the_minimum_over_the_candidate_bands(freqs_hz, expected):
    profile = get_radio_profile(_IC9700)
    assert resolve_power_ceiling_watts(profile, freqs_hz=freqs_hz) == expected


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
        get_radio_profile(_IC9700), power_sources_w={"dc": 100, "battery": 50}
    )
    assert (
        resolve_power_ceiling_watts(
            profile, freqs_hz=(435_000_000,), power_source="battery"
        )
        == 50
    )
    assert (
        resolve_power_ceiling_watts(profile, freqs_hz=(435_000_000,), power_source="dc")
        == 75
    )


def test_profile_without_tables_resolves_no_ceiling():
    profile = get_radio_profile("FTX-1")
    assert not has_power_ceilings(profile)
    assert resolve_power_ceiling_watts(profile, freqs_hz=(145_000_000,)) is None
    assert has_power_ceilings(get_radio_profile(_IC9700))
    assert has_power_ceilings(get_radio_profile("IC-705"))


# ── Which observations define the transmit band ───────────────────

_FTX1_ROWS = {"2m": 75, "70cm": 50}
_MAIN_FREQ = FieldPath.active("main", "freq_mode", "freq_hz")
_SUB_FREQ = FieldPath.active("sub", "freq_mode", "freq_hz")
_TX_TARGET = FieldPath.global_("tx_state", "tx_target")


def _observe(
    store: StateStore,
    path: FieldPath,
    value: Any,
    *,
    at: float = 10.0,
    max_age: float | None = 30.0,
) -> None:
    store.apply(
        Observation(
            path=path,
            value=value,
            source=SourceMetadata("poll_response", "test", "fake", "test"),
            timestamp_monotonic=at,
            max_age=max_age,
        )
    )


def _freqs(store: StateStore, *, receiver_count: int | None = 2):
    return band_frequencies_hz_from_snapshot(
        store.snapshot(), receiver_count=receiver_count
    )


def test_known_tx_target_names_the_transmitting_band():
    """Split/cross-band: the TRANSMITTING receiver's band governs."""
    store = StateStore()
    _observe(store, _MAIN_FREQ, 145_000_000)
    _observe(store, _SUB_FREQ, 435_000_000)
    _observe(
        store,
        _TX_TARGET,
        KnownTxTarget(receiver="SUB", slot=None, frequency_hz=435_000_000),
    )
    profile = replace(get_radio_profile("FTX-1"), power_ceilings_w=_FTX1_ROWS)

    freqs = _freqs(store)
    assert freqs == (435_000_000,)
    # MAIN sits on 2 m (75 W) but TX routes to 70 cm: 50 W.
    assert resolve_power_ceiling_watts(profile, freqs_hz=freqs) == 50


def test_newer_tx_target_beats_an_older_receiver_read():
    """Pre-review round 2: the older 145 MHz read must not win over the
    later bracketed TX-target read of 435 MHz."""
    store = StateStore()
    _observe(store, _MAIN_FREQ, 145_000_000, at=10.0)
    _observe(
        store,
        _TX_TARGET,
        KnownTxTarget(receiver="MAIN", slot=None, frequency_hz=435_000_000),
        at=11.0,
    )
    assert _freqs(store) == (435_000_000,)


def test_newer_receiver_read_beats_an_older_tx_target():
    store = StateStore()
    _observe(
        store,
        _TX_TARGET,
        KnownTxTarget(receiver="MAIN", slot=None, frequency_hz=145_000_000),
        at=10.0,
    )
    _observe(store, _MAIN_FREQ, 435_000_000, at=11.0)
    assert _freqs(store) == (435_000_000,)


def test_equally_old_disagreeing_evidence_keeps_both_candidates():
    store = StateStore()
    _observe(store, _MAIN_FREQ, 145_000_000, at=10.0)
    _observe(
        store,
        _TX_TARGET,
        KnownTxTarget(receiver="MAIN", slot=None, frequency_hz=435_000_000),
        at=10.0,
    )
    assert _freqs(store) == (145_000_000, 435_000_000)


def test_tx_target_on_a_named_slot_ignores_the_receivers_active_frequency():
    """Pre-review round 2: a split target on MAIN/B is not MAIN's active A."""
    store = StateStore()
    _observe(store, _MAIN_FREQ, 145_000_000, at=11.0)
    _observe(
        store,
        _TX_TARGET,
        KnownTxTarget(receiver="MAIN", slot="B", frequency_hz=435_000_000),
        at=10.0,
    )
    assert _freqs(store) == (435_000_000,)

    # A named slot without its own frequency has no evidence at all.
    bare = StateStore()
    _observe(bare, _MAIN_FREQ, 145_000_000)
    _observe(
        bare, _TX_TARGET, KnownTxTarget(receiver="MAIN", slot="B", frequency_hz=None)
    )
    assert _freqs(bare) == (None,)


def test_unproven_tx_route_counts_every_receiver_that_could_transmit():
    """Pre-review round 2: no tx_target is no proof that focus transmits.

    MAIN's frequency arrived before the first TX-target poll while the
    physical transmitter may already be routed to SUB: both receivers are
    candidates, and a receiver nobody observed is unknown.
    """
    store = StateStore()
    _observe(store, _MAIN_FREQ, 145_000_000)
    profile = replace(get_radio_profile("FTX-1"), power_ceilings_w=_FTX1_ROWS)

    freqs = _freqs(store)
    assert freqs == (145_000_000, None)
    assert resolve_power_ceiling_watts(profile, freqs_hz=freqs) == 50

    _observe(store, _SUB_FREQ, 435_000_000)
    assert _freqs(store) == (145_000_000, 435_000_000)


def test_single_receiver_profile_reads_its_only_receiver():
    store = StateStore()
    _observe(store, _MAIN_FREQ, 145_000_000)
    _observe(store, _SUB_FREQ, 435_000_000)
    assert _freqs(store, receiver_count=1) == (145_000_000,)


def test_observed_unknown_tx_target_is_conservative():
    """A tx_target the authority cannot name resolves no band at all."""
    store = StateStore()
    _observe(store, _MAIN_FREQ, 145_000_000)
    _observe(store, _TX_TARGET, UnknownTxTarget(reason="not-observed"))
    assert _freqs(store) == (None,)


def test_stale_frequency_is_unobserved_evidence():
    """A frequency the freshness driver aged to STALE does not count."""
    store = StateStore()
    _observe(store, _MAIN_FREQ, 145_000_000, at=10.0, max_age=1.0)
    store.mark_stale_due(now=20.0)
    assert _freqs(store, receiver_count=1) == (None,)


def test_expired_tx_target_stays_conservative():
    """An expired target must not resurrect receiver focus (pre-review r3)."""
    store = StateStore()
    _observe(store, _MAIN_FREQ, 145_000_000, max_age=30.0)
    _observe(store, _SUB_FREQ, 435_000_000, max_age=30.0)
    _observe(
        store,
        _TX_TARGET,
        KnownTxTarget(receiver="SUB", slot=None, frequency_hz=435_000_000),
        max_age=1.0,
    )
    store.mark_stale_due(now=20.0)
    assert _freqs(store) == (None,)


def _state_double(*, main_hz: int = 0, sub_hz: int = 0) -> RadioState:
    state = RadioState()
    state.main.freq = main_hz
    state.sub.freq = sub_hz
    return state


def _profile_double(receiver_count: int) -> SimpleNamespace:
    return SimpleNamespace(receiver_count=receiver_count)


def test_observed_band_freqs_prefer_the_canonical_store_over_the_mirror():
    """Production polling feeds the store; the mirror may lag or stay empty.

    With a store attached its answer is final even when it holds nothing (an
    emptied/provider-reset store must not resurrect the mirror's
    previous-connection frequency).
    """
    store = StateStore()
    _observe(store, _MAIN_FREQ, 435_000_000)
    radio = SimpleNamespace(
        profile=_profile_double(1),
        _state_store=store,
        radio_state=_state_double(main_hz=145_000_000),
    )
    assert observed_band_freqs_hz(radio) == (435_000_000,)

    emptied = SimpleNamespace(
        profile=_profile_double(1),
        _state_store=StateStore(),
        radio_state=_state_double(main_hz=145_000_000),
    )
    assert observed_band_freqs_hz(emptied) == (None,)


def test_store_less_radio_reads_every_possible_transmitter_from_the_mirror():
    dual = SimpleNamespace(
        profile=_profile_double(2),
        radio_state=_state_double(main_hz=145_000_000),
    )
    assert observed_band_freqs_hz(dual) == (145_000_000, None)
    single = SimpleNamespace(
        profile=_profile_double(1),
        radio_state=_state_double(main_hz=145_000_000),
    )
    assert observed_band_freqs_hz(single) == (145_000_000,)
    assert observed_band_freqs_hz(SimpleNamespace()) == (None, None)


# ── Admission clamp in the watts domain ───────────────────────────


@pytest.mark.parametrize(
    ("value", "ceiling", "floor", "expected_native"),
    [
        # Above the transmitting band's ceiling: clamped down.
        (90, 75, None, 75),
        (300, 75, None, 75),
        (75, 75, None, 75),
        (10, 75, None, 10),
        # The floor keeps lifting within the ceiling's range.
        (2, 75, 5, 5),
        (0, 75, 5, 5),
        # A ceiling below the flat max also caps a normalized request.
        (0.9, 50, None, 50),
        (0.3, 50, 5, 30),
    ],
)
def test_watts_admission_clamps_to_the_ceiling(value, ceiling, floor, expected_native):
    native, level = resolve_power_level_target(
        value,
        power_native_unit="watts",
        power_max_watts=100,
        power_min_watts=floor,
        power_ceiling_watts=ceiling,
    )
    assert native == expected_native
    # The normalized readback is always a fraction of the flat maximum.
    assert level == expected_native / 100


@pytest.mark.parametrize(
    ("value", "expected_native"),
    [
        # Above the flat range: capped at the profile maximum.
        (150, 100),
        (101, 100),
        (100, 100),
        # Inside the range: untouched.
        (60, 60),
        (1, 1),
        (0, 0),
        # A negative integer is not a power: it lands on the bottom of the
        # range instead of reaching the wire as a malformed frame.
        (-1, 0),
        (-5, 0),
    ],
)
def test_flat_range_clamps_a_bare_int_with_neither_floor_nor_ceiling(
    value, expected_native
):
    """MOR-3634 widened the bare-int clamp to profiles without a floor: this
    configuration (no ``min_watts``, no band/source rows) exercises that
    branch alone -- the ceiling and the floor each mask it elsewhere."""
    native, level = resolve_power_level_target(
        value,
        power_native_unit="watts",
        power_max_watts=100,
        power_min_watts=None,
        power_ceiling_watts=None,
    )
    assert native == expected_native
    assert level == expected_native / 100


@pytest.mark.parametrize("floor", [None, 5])
@pytest.mark.parametrize("ceiling", [None, 75, 10])
def test_the_clamp_only_lowers_a_request_inside_the_range(floor, ceiling):
    """TX safety: no request in the profile's range ever leaves admission
    above what the operator asked for (the floor lift is the declared
    exception below the range, pinned separately)."""
    for value in range(floor or 0, 101):
        native, _ = resolve_power_level_target(
            value,
            power_native_unit="watts",
            power_max_watts=100,
            power_min_watts=floor,
            power_ceiling_watts=ceiling,
        )
        assert native <= value
        assert native == min(value, ceiling or 100)


def test_raw_255_admission_ignores_the_ceiling():
    native, level = resolve_power_level_target(
        1.0, power_native_unit="raw_255", power_ceiling_watts=10
    )
    assert (native, level) == (255, 1.0)


def test_intent_expectation_is_the_clamped_fraction_of_the_flat_maximum():
    intent = command_intent_from_request(
        "set_power",
        {"level": 90},
        source="http",
        power_native_unit="watts",
        power_max_watts=100,
        power_min_watts=5,
        power_ceiling_watts=50,
    )
    assert intent.params["power_level"] == 0.5


# ── Backend: the ceiling is resolved from the radio at the wire ───


class _FakeYaesuWire:
    """A tiny CAT radio behind the transport: it answers reads and applies
    writes, so every read in the tests is the radio's real current state."""

    def __init__(self, *, main_hz: int, sub_hz: int = 0, tx: int = 0) -> None:
        self.main_hz = main_hz
        self.sub_hz = sub_hz
        self.tx = tx
        self.power_w = 0
        self.writes: list[str] = []
        self.queries: list[str] = []
        self.unanswered: set[str] = set()
        # Verbatim replies that replace the radio's real answer (a malformed
        # frame, an out-of-range selector).
        self.replies: dict[str, str] = {}
        # Called once a read was answered: the operator touches the radio
        # between two reads of one resolution (front panel, a reconnect).
        self.after_query: Any = None
        # Awaited after a write's bytes took effect on the radio and before
        # the transport call returns (the "drain" of a real serial write).
        self.after_effect: Any = None

    async def query(self, command: str, **_kwargs: object) -> str:
        self.queries.append(command)
        if command in self.unanswered:
            raise CatTimeoutError("no answer")
        if command in self.replies:
            reply = self.replies[command]
        elif command == "FA;":
            reply = f"FA{self.main_hz:09d}"
        elif command == "FB;":
            reply = f"FB{self.sub_hz:09d}"
        elif command == "FT;":
            reply = f"FT{self.tx}"
        elif command == "PC;":
            reply = f"PC2{self.power_w:03d}"
        else:
            raise AssertionError(f"unexpected read {command!r}")
        if self.after_query is not None:
            self.after_query(command)
        return reply

    async def write(self, command: str, **_kwargs: object) -> None:
        self.writes.append(command)
        if command.startswith("FA"):
            self.main_hz = int(command[2:-1])
        elif command.startswith("FB"):
            self.sub_hz = int(command[2:-1])
        elif command.startswith("FT"):
            self.tx = int(command[2:-1])
        elif command.startswith("PC"):
            self.power_w = int(command[3:-1])
        if self.after_effect is not None:
            await self.after_effect(command)

    @property
    def power_writes(self) -> list[str]:
        return [w for w in self.writes if w.startswith("PC")]


def _yaesu(
    *,
    ceilings: dict[str, int] | None = None,
    sources: dict[str, int] | None = None,
    acquisition: bool = True,
    min_watts: int | None = 5,
    main_hz: int = 145_000_000,
    sub_hz: int = 435_000_000,
    tx: int = 0,
) -> tuple[YaesuCatRadio, _FakeYaesuWire]:
    """A YaesuCatRadio on the ftx1 profile plus per-band ceiling rows.

    The shipped 5 W floor stays unless *min_watts* is ``None``; ``ceilings={}``
    declares no rows (the flat ``max_watts`` range alone). *sources* adds a
    per-power-source table; ``acquisition=False`` drops the optional
    ``state_acquisition`` polling metadata.
    """
    config = replace(
        load_rig(_RIGS_DIR / "ftx1.toml"),
        power_ceilings_w=_FTX1_ROWS if ceilings is None else ceilings,
        power_sources_w=sources,
        min_watts=min_watts,
    )
    if not acquisition:
        config = replace(config, state_acquisition=None)
    radio = YaesuCatRadio("/dev/null", profile=config)
    wire = _FakeYaesuWire(main_hz=main_hz, sub_hz=sub_hz, tx=tx)
    radio._transport._connected = True
    radio._transport.query = wire.query
    radio._transport.write = wire.write
    return radio, wire


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("main_hz", "requested", "wire"),
    [
        # 2 m ceiling 75: a request under it is untouched on the wire.
        (145_000_000, 60, "PC2060;"),
        (145_000_000, 90, "PC2075;"),
        # 70 cm ceiling 50: a 90 W request is clamped down.
        (435_000_000, 90, "PC2050;"),
        # A band no row covers: the lowest declared ceiling.
        (7_100_000, 90, "PC2050;"),
        # The floor lifts under a ceiling (5 W floor, 50 W ceiling).
        (435_000_000, 3, "PC2005;"),
    ],
)
async def test_backend_set_power_clamps_to_the_transmit_band_ceiling(
    main_hz, requested, wire
):
    radio, fake = _yaesu(main_hz=main_hz)
    await radio.set_power(requested)
    assert fake.power_writes == [wire]
    # The clamp path adjusts power only: no keying frame goes out.
    assert all(not frame.startswith("TX") for frame in fake.writes)


@pytest.mark.asyncio
@pytest.mark.parametrize("main_hz", [145_000_000, 435_000_000, 7_100_000])
async def test_backend_set_power_never_writes_more_than_was_asked(main_hz):
    """TX safety: the clamp only lowers a request that lies inside the range,
    and set_power returns exactly the watts it put on the wire (the value a
    caller builds its readback expectation from)."""
    radio, fake = _yaesu(main_hz=main_hz)
    for requested in (5, 20, 49, 50, 51, 74, 75, 76, 100):
        before = len(fake.power_writes)
        returned = await radio.set_power(requested)
        [frame] = fake.power_writes[before:]
        written = int(frame[3:6])
        assert written <= requested
        assert returned == written
    assert all(not frame.startswith(("TX", "MX")) for frame in fake.writes)


@pytest.mark.asyncio
async def test_backend_set_rf_power_alias_clamps_like_set_power():
    radio, fake = _yaesu(main_hz=435_000_000)
    await radio.set_rf_power(90)
    assert fake.power_writes == ["PC2050;"]


@pytest.mark.asyncio
async def test_backend_without_tables_reads_nothing_and_keeps_todays_wire():
    config = load_rig(_RIGS_DIR / "ftx1.toml")
    radio = YaesuCatRadio("/dev/null", profile=config)
    fake = _FakeYaesuWire(main_hz=435_000_000)
    radio._transport._connected = True
    radio._transport.query = fake.query
    radio._transport.write = fake.write
    await radio.set_power(90)
    assert fake.power_writes == ["PC2090;"]
    assert fake.queries == []


@pytest.mark.asyncio
@pytest.mark.parametrize("acquisition", [True, False])
async def test_backend_source_only_ceiling_clamps_on_the_source_row_alone(acquisition):
    """Review round 4: a source-only table needs no band, so it needs no
    observation metadata either -- the write goes out clamped to the lowest
    source row (no source is observed), nothing is read from the radio."""
    radio, fake = _yaesu(
        ceilings={}, sources={"dc": 100, "battery": 50}, acquisition=acquisition
    )
    assert (radio.profile.state_acquisition is None) is (not acquisition)
    assert await radio.set_power(90) == 50
    assert fake.power_writes == ["PC2050;"]
    assert fake.queries == [], "a source-only table reads no band"
    assert await radio.set_power(30) == 30
    assert fake.power_writes == ["PC2050;", "PC2030;"]


@pytest.mark.asyncio
async def test_backend_band_table_without_observation_metadata_takes_the_lowest_row():
    """A band table cannot read the band without ``state_acquisition``: the
    write still goes out, at the lowest declared ceiling, never the widest
    and never a refusal."""
    radio, fake = _yaesu(acquisition=False, main_hz=145_000_000)
    assert await radio.set_power(90) == 50
    assert fake.power_writes == ["PC2050;"]
    assert fake.queries == []


@pytest.mark.asyncio
async def test_backend_power_follows_the_routed_transmitter():
    """MAIN on 2 m (75 W), SUB on 70 cm (50 W): FT; names who transmits."""
    radio, fake = _yaesu(tx=1)
    await radio.set_power(90)
    assert fake.power_writes == ["PC2050;"]
    # The one validated reader: the route read brackets the frequency read.
    assert fake.queries == ["FT;", "FB;", "FT;"]

    radio, fake = _yaesu(tx=0)
    await radio.set_power(90)
    assert fake.power_writes == ["PC2075;"]
    assert fake.queries == ["FT;", "FA;", "FT;"]


@pytest.mark.asyncio
async def test_backend_band_change_then_power_uses_the_destination_ceiling():
    radio, fake = _yaesu(main_hz=145_000_000)
    await radio.set_freq(435_000_000)
    await radio.set_power(90)  # before any poll could have run
    assert fake.writes == ["FA435000000;", "PC2050;"]


@pytest.mark.asyncio
@pytest.mark.parametrize("route", ["set_tx_func", "set_tx_source"])
async def test_backend_tx_route_change_then_power_uses_the_new_transmitter(route):
    """Pre-review round 2: a TX-route change moves the ceiling immediately."""
    radio, fake = _yaesu(tx=0)
    await getattr(radio, route)(1)
    await radio.set_power(90)
    assert fake.power_writes == ["PC2050;"]


@pytest.mark.asyncio
async def test_backend_partial_frequency_write_cannot_leave_the_old_band():
    """Pre-review round 2: bytes reached the radio, then the call was cancelled.

    The radio is on 70 cm now; whatever the caller (or a cache) believes, the
    next power write reads the radio and clamps to 70 cm.
    """
    radio, fake = _yaesu(main_hz=145_000_000)

    async def cancel_after_effect(command: str) -> None:
        if command.startswith("FA"):
            raise asyncio.CancelledError

    fake.after_effect = cancel_after_effect
    task = asyncio.create_task(radio.set_freq(435_000_000))
    with pytest.raises(asyncio.CancelledError):
        await task
    fake.after_effect = None

    await radio.set_power(90)
    assert fake.main_hz == 435_000_000
    assert fake.power_writes == ["PC2050;"]


@pytest.mark.asyncio
async def test_backend_power_waits_for_an_in_flight_band_change():
    radio, fake = _yaesu(main_hz=145_000_000)
    started = asyncio.Event()
    release = asyncio.Event()

    async def hold(command: str) -> None:
        if command.startswith("FA"):
            started.set()
            await release.wait()

    fake.after_effect = hold
    freq_task = asyncio.create_task(radio.set_freq(435_000_000))
    await asyncio.wait_for(started.wait(), timeout=1.0)
    power_task = asyncio.create_task(radio.set_power(90))
    await asyncio.sleep(0)
    assert not power_task.done(), "power write must wait for the band change"
    assert fake.power_writes == []
    release.set()
    await asyncio.wait_for(asyncio.gather(freq_task, power_task), timeout=1.0)
    assert fake.writes == ["FA435000000;", "PC2050;"]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "operation",
    [
        lambda radio: radio.set_freq(435_000_000),
        lambda radio: radio.set_band(14),
        lambda radio: radio.band_up(),
        lambda radio: radio.band_down(),
        lambda radio: radio.set_split(True),
        lambda radio: radio.set_tx_func(1),
        lambda radio: radio.set_tx_source(1),
        lambda radio: radio.set_vfo_select(1),
        lambda radio: radio.vfo_a_to_b(),
        lambda radio: radio.vfo_b_to_a(),
    ],
    ids=[
        "set_freq",
        "set_band",
        "band_up",
        "band_down",
        "set_split",
        "set_tx_func",
        "set_tx_source",
        "set_vfo_select",
        "vfo_a_to_b",
        "vfo_b_to_a",
    ],
)
async def test_every_band_or_route_mutation_waits_for_an_in_flight_power_write(
    operation,
):
    """Pre-review round 2: no mutation interleaves with resolve + write."""
    radio, fake = _yaesu()
    async with radio._mode_lock:
        task = asyncio.create_task(operation(radio))
        await asyncio.sleep(0)
        await asyncio.sleep(0)
        assert not task.done()
        assert fake.writes == []
    await asyncio.wait_for(task, timeout=1.0)
    assert fake.writes != []


@pytest.mark.asyncio
async def test_backend_unanswered_reads_fall_back_to_the_lowest_ceiling():
    radio, fake = _yaesu(main_hz=145_000_000, tx=0)
    fake.unanswered = {"FA;"}
    await radio.set_power(90)
    assert fake.power_writes == ["PC2050;"]  # unknown band: 70 cm row, not 2 m


@pytest.mark.asyncio
async def test_backend_unreadable_tx_route_applies_the_lowest_ceiling():
    # Both receivers sit in the 75 W band: only "no proof of the band" can
    # explain the 50 W write (the lowest declared row).
    radio, fake = _yaesu(main_hz=145_000_000, sub_hz=145_000_000)
    fake.unanswered = {"FT;"}
    await radio.set_power(90)
    assert fake.power_writes == ["PC2050;"]


@pytest.mark.asyncio
async def test_backend_route_change_during_the_read_applies_the_lowest_ceiling():
    """The operator flips TX to SUB at the front panel between the route read
    and the frequency reply: the bracketing route read disagrees, so the
    transmit band is not proven and the write takes the lowest row."""
    radio, fake = _yaesu(main_hz=145_000_000, sub_hz=145_000_000, tx=0)

    def operator_switches_tx(command: str) -> None:
        if command == "FA;":
            fake.tx = 1

    fake.after_query = operator_switches_tx
    await radio.set_power(90)
    assert fake.power_writes == ["PC2050;"]
    assert fake.queries == ["FT;", "FA;", "FT;"]


@pytest.mark.asyncio
@pytest.mark.parametrize("selector", ["FT2", "FT9", "FTx", "FT"])
async def test_backend_invalid_tx_selector_applies_the_lowest_ceiling(selector):
    """A selector outside 0/1 is not a route: it must not silently mean SUB."""
    radio, fake = _yaesu(main_hz=435_000_000, sub_hz=145_000_000)
    fake.replies["FT;"] = selector
    await radio.set_power(90)
    # SUB (what a lax reader would pick) is the 75 W band; MAIN the 50 W one.
    assert fake.power_writes == ["PC2050;"]
    assert "FB;" not in fake.queries


@pytest.mark.asyncio
async def test_backend_connection_change_during_the_read_applies_the_lowest_ceiling():
    radio, fake = _yaesu(main_hz=145_000_000, sub_hz=145_000_000, tx=0)

    def link_reconnects(command: str) -> None:
        if command == "FA;":
            radio._transport.stats.reconnects += 1

    fake.after_query = link_reconnects
    await radio.set_power(90)
    assert fake.power_writes == ["PC2050;"]


@pytest.mark.asyncio
@pytest.mark.parametrize("reply", ["FA", "FAx45000000", "FA-45000000"])
async def test_backend_malformed_frequency_reply_applies_the_lowest_ceiling(reply):
    radio, fake = _yaesu(main_hz=145_000_000, sub_hz=145_000_000)
    fake.replies["FA;"] = reply
    await radio.set_power(90)
    assert fake.power_writes == ["PC2050;"]


@pytest.mark.asyncio
async def test_backend_reading_the_band_never_widens_a_failed_write():
    """A write error still propagates; the failed read is not swallowed into
    a wider ceiling."""
    radio, fake = _yaesu(main_hz=435_000_000)
    original = fake.write

    async def refuse(command: str, **kwargs: object) -> None:
        raise RuntimeError("serial down")

    radio._transport.write = refuse
    with pytest.raises(RuntimeError):
        await radio.set_power(90)
    radio._transport.write = original
    assert fake.power_writes == []


# ── Web seam: real handler, queue, poller and backend over the API ─


class _QueueRecorder:
    def __init__(self) -> None:
        self.items: list[object] = []

    def put(self, item, **_metadata: object) -> None:
        self.items.append(item)

    def put_ordered(self, item, **_metadata: object) -> None:
        self.items.append(item)


def _watts_handler(profile: Any, state: RadioState, *, native_power_unit="watts"):
    from rigplane.web.handlers.control import ControlHandler

    radio = SimpleNamespace(
        capabilities={"tx"},
        profile=profile,
        native_power_unit=native_power_unit,
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
            _IC9700,
            server=server,
            session_id=f"power-ceilings-{native_power_unit}",
        ),
    )


async def _enqueued_power(profile: Any, state: RadioState, level: Any):
    _radio, handler = _watts_handler(profile, state)
    result = await handler._enqueue_command("set_power", {"level": level})
    [command] = handler._server.command_queue.items
    assert type(command).__name__ == "SetPower"
    assert command.unit == "watts"
    return command.level, result


@pytest.mark.asyncio
async def test_handler_queues_the_operators_request_unclamped_by_band():
    """Pre-review round 2: the band the request is admitted on is not the
    band it executes on, so the queued value is the request itself (inside
    the flat range); the backend clamps at the wire."""
    profile = get_radio_profile(_IC9700)
    state = _state_double(main_hz=1_297_000_000)  # 23 cm: 10 W row
    queued, _ = await _enqueued_power(profile, state, 90)
    assert queued == 90
    # Normalized requests keep their meaning on every band: a fraction of the
    # flat 100 W maximum, so 0.5 is 50 W whatever band is current.
    queued, _ = await _enqueued_power(profile, state, 0.5)
    assert queued == 50


@pytest.mark.asyncio
async def test_handler_reply_expects_the_clamped_fraction_of_the_flat_maximum():
    profile = get_radio_profile(_IC9700)
    both_70cm = _state_double(main_hz=435_000_000, sub_hz=435_000_000)
    _, reply = await _enqueued_power(profile, both_70cm, 90)
    assert reply["level"] == 90
    assert reply["admitted_level"] == 0.75  # 75 W row of the 100 W maximum

    # Nothing observed yet: the lowest declared row. So is a dual-receiver
    # radio whose second receiver is unobserved (it might be the transmitter).
    _, reply = await _enqueued_power(profile, _state_double(), 90)
    assert reply["admitted_level"] == 0.1
    one_known = _state_double(main_hz=435_000_000)
    _, reply = await _enqueued_power(profile, one_known, 90)
    assert reply["admitted_level"] == 0.1


@pytest.mark.asyncio
async def test_handler_profile_without_tables_keeps_flat_max_and_floor():
    state = _state_double(main_hz=435_000_000)
    profile = get_radio_profile("FTX-1")
    assert (await _enqueued_power(profile, state, 90))[0] == 90
    assert (await _enqueued_power(profile, state, 150))[0] == 100
    assert (await _enqueued_power(profile, state, 2))[0] == 5


@pytest.mark.asyncio
async def test_handler_raw_255_radio_keeps_the_raw_wire_scale():
    """The shipped IC-9700 rides raw-255 CI-V: the ceiling never re-scales it."""
    _radio, handler = _watts_handler(
        get_radio_profile(_IC9700),
        _state_double(main_hz=1_297_000_000),
        native_power_unit="raw_255",
    )

    await handler._enqueue_command("set_power", {"level": 90})
    await handler._enqueue_command("set_power", {"level": 1.0})

    [first, second] = handler._server.command_queue.items
    assert (first.level, first.unit) == (90, "raw_255")
    assert (second.level, second.unit) == (255, "raw_255")


@contextlib.asynccontextmanager
async def _serving(radio: YaesuCatRadio):
    """A real WebServer + Yaesu observation poller over the fake wire."""
    web = WebServer(radio, WebConfig(host="127.0.0.1", port=0))

    def apply_observations(observations: Any) -> None:
        for observation in observations:
            web.command_service.apply_observation(observation)

    poller = radio.create_observation_poller(
        callback=apply_observations, command_queue=web.command_queue
    )
    poller.bind_provider_generation(
        capture=lambda: web.command_state_store.provider_generation
    )
    web._server = await asyncio.start_server(web._accept_client, "127.0.0.1", 0)

    async def drain() -> None:
        while True:
            await web.command_queue.wait(timeout=5.0)
            await poller._drain_commands()

    consumer = asyncio.create_task(drain())
    try:
        yield web, poller
    finally:
        consumer.cancel()
        web._server.close()
        await web._server.wait_closed()
        web._server = None
        tasks = list(web._client_tasks)
        for task in tasks:
            task.cancel()
        await asyncio.gather(consumer, *tasks, return_exceptions=True)


@pytest.fixture()
async def yaesu_web():
    """The FTX-1 with 2 m / 70 cm rows, MAIN on 2 m and SUB on 70 cm."""
    radio, fake = _yaesu(main_hz=145_000_000, sub_hz=435_000_000, tx=0)
    async with _serving(radio) as (web, poller):
        yield web, radio, fake, poller


@pytest.fixture()
async def flat_range_web():
    """A 0-100 W radio with neither a floor nor any ceiling row."""
    radio, fake = _yaesu(ceilings={}, min_watts=None)
    async with _serving(radio) as (web, poller):
        yield web, radio, fake, poller


async def _until(predicate: Any) -> None:
    async with asyncio.timeout(5.0):
        while not predicate():
            await asyncio.sleep(0.01)


def _poll_source(radio: YaesuCatRadio) -> MagicMock:
    """The radio as the observation adapter polls it: the setpoint read goes
    through the real backend and the fake wire; unrelated reads are stubbed."""
    source = MagicMock(spec=YaesuCatRadio)
    source.profile = radio.profile
    source.capabilities = {"tx"}
    source._state_store = None
    source.read_power = radio.read_power
    source.read_mic_gain = AsyncMock(return_value=40)
    return source


def _observation_profile(radio: YaesuCatRadio):
    acquisition = radio.profile.state_acquisition
    assert acquisition is not None
    return acquisition


async def _http_post(web: WebServer, payload: dict[str, Any]) -> tuple[int, Any]:
    reader, writer = await asyncio.open_connection("127.0.0.1", web.port)
    try:
        body = json.dumps(payload).encode()
        writer.write(
            (
                "POST /api/v1/commands HTTP/1.1\r\n"
                f"Host: 127.0.0.1:{web.port}\r\n"
                "Connection: close\r\nContent-Type: application/json\r\n"
                f"Content-Length: {len(body)}\r\n\r\n"
            ).encode()
            + body
        )
        await writer.drain()
        status, _headers, response = await asyncio.wait_for(
            _read_http_response(reader), 5
        )
        return status, json.loads(response)
    finally:
        writer.close()
        await writer.wait_closed()


@contextlib.asynccontextmanager
async def _ws_session(web: WebServer, *, streams: tuple[str, ...] = ()):
    """A control WebSocket past its handshake (hello + first state frame),
    subscribed to *streams* (the app subscribes to ``events``, which is what
    carries the issuer's ``command_lifecycle`` frames)."""
    reader, writer, _ = await _ws_connect("127.0.0.1", web.port, "/api/v1/ws")
    try:
        for expected in ("hello", "state_update"):
            _, frame = await _ws_recv_frame(reader, timeout=5.0)
            assert json.loads(frame)["type"] == expected
        if streams:
            await _ws_send_text(
                writer, json.dumps({"type": "subscribe", "streams": list(streams)})
            )
        yield reader, writer
    finally:
        writer.close()
        await writer.wait_closed()


async def _ws_send_commands(
    reader: Any, writer: Any, commands: list[dict[str, Any]]
) -> list[Any]:
    """Send *commands* and collect their responses."""
    for command in commands:
        await _ws_send_text(writer, json.dumps({"type": "cmd", **command}))
    pending = {command["id"] for command in commands}
    results = []
    while pending:
        _, frame = await _ws_recv_frame(reader, timeout=5.0)
        message = json.loads(frame)
        if message.get("type") == "response" and message.get("id") in pending:
            pending.discard(message["id"])
            results.append(message)
    return results


async def _ws_commands(
    web: WebServer,
    commands: list[dict[str, Any]],
    *,
    then: Any = None,
) -> list[Any]:
    """Send *commands*, collect their responses, run *then* with the socket
    still open (a queued command dies with its control session)."""
    async with _ws_session(web) as (reader, writer):
        results = await _ws_send_commands(reader, writer, commands)
        if then is not None:
            await then()
        return results


@pytest.mark.asyncio
@pytest.mark.parametrize("protocol", ["http", "ws"])
@pytest.mark.parametrize("level", [0.9, 90])
async def test_api_band_change_then_power_request_reaches_the_wire_clamped(
    yaesu_web, protocol, level
):
    """Pre-review round 2: API entry -> queue -> poller -> backend -> state.

    MAIN starts on 2 m (75 W row). The operator asks for 435 MHz and 90 W
    (as an absolute or a normalized request) through the real API; the radio
    ends up on 70 cm, the wire carries the 70 cm ceiling, and the public
    state agrees with it.
    """
    web, radio, fake, _poller = yaesu_web
    commands = [
        {"id": "band", "name": "set_freq", "params": {"freq": 435_000_000}},
        {"id": "power", "name": "set_power", "params": {"level": level}},
    ]
    if protocol == "http":
        for command in commands:
            status, result = await _http_post(web, command)
            assert (status, result["ok"]) == (200, True), result
    else:
        results = await _ws_commands(web, commands)
        assert all(result["ok"] for result in results), results

    await _until(lambda: fake.power_writes)
    assert fake.writes == ["FA435000000;", "PC2050;"]
    assert fake.main_hz == 435_000_000

    # The radio answers the next poll; the public state reports what it
    # actually runs: 50 W, on the one scale every band shares (the flat
    # 100 W maximum).
    store = web.command_state_store
    for observation in await YaesuObservationAdapter(
        _poll_source(radio), profile=_observation_profile(radio), clock=lambda: 1.0
    ).poll_tx_controls():
        store.apply(observation)
    state = build_public_state_payload_from_snapshot(
        store.snapshot(), radio=radio, receiver_count=2
    )
    assert state["powerLevel"] == pytest.approx(0.5)


async def _publish_poll(web: WebServer, radio: YaesuCatRadio, poller: Any) -> None:
    """The next poll as the radio answers it, through production's path.

    The setpoint read goes through the real backend and the fake wire; the
    poller then correlates it with the command it dispatched and the command
    service reconciles (or does not reconcile) the command's expectation.
    """
    observations = await YaesuObservationAdapter(
        _poll_source(radio),
        profile=_observation_profile(radio),
        clock=time.monotonic,
    ).poll_tx_controls()
    stamped = poller._stamp_provider_generation(
        observations, poller._captured_provider_generation()
    )
    for observation in poller._annotate_yaesu_readbacks(stamped):
        web.command_service.apply_observation(observation)


_POWER_LEVEL = FieldPath.global_("operator_controls", "power_level")


def _power_lifecycle_states(web: WebServer) -> list[str]:
    """Lifecycle states of the (single) command that targets the RF power."""
    return [
        event.state
        for event in web.command_service.lifecycle_events()
        if event.target == _POWER_LEVEL
    ]


# (radio_hz, estimate_hz, queued_hz, wire): the band the radio is on, the band
# admission estimated from (0: nothing observed), a band change queued ahead
# of the power write, and the PC frame that must go out.
_ADMISSION_VS_WIRE_CASES = [
    # Startup: nothing observed yet (the estimate is the lowest row, 50 W)
    # while the radio really is on 2 m (75 W row).
    (145_000_000, 0, None, "PC2075;"),
    # A band change queued ahead of the power write, upward: the estimate
    # was made on 70 cm (50 W) and the write lands on 2 m (75 W).
    (435_000_000, 435_000_000, 145_000_000, "PC2075;"),
    # ...and downward: estimated on 2 m (75 W), written on 70 cm (50 W).
    (145_000_000, 145_000_000, 435_000_000, "PC2050;"),
    # Control: the estimate holds, so the harness itself confirms.
    (145_000_000, 145_000_000, None, "PC2075;"),
]
_ADMISSION_VS_WIRE_IDS = [
    "startup-unknown-band",
    "queued-to-wider-band",
    "queued-to-narrower-band",
    "estimate-holds",
]


@pytest.mark.asyncio
@pytest.mark.parametrize("protocol", ["http", "ws"])
@pytest.mark.parametrize(
    ("radio_hz", "estimate_hz", "queued_hz", "wire"),
    _ADMISSION_VS_WIRE_CASES,
    ids=_ADMISSION_VS_WIRE_IDS,
)
async def test_api_power_command_is_confirmed_by_what_the_backend_wrote(
    yaesu_web, protocol, radio_hz, estimate_hz, queued_hz, wire
):
    """Review round 3: the readback a command waits for is the clamp the
    locked backend applied, not the admission-time estimate.

    Admission answers from the latest observations (``estimate_hz`` is the
    band they name); a queued band change then moves the radio before the
    write runs. The wire carries the real ceiling and the radio reads it
    back, so that very value must close the command.
    """
    web, radio, fake, poller = yaesu_web
    fake.main_hz = radio_hz
    assert radio._state_store is web.command_state_store
    if estimate_hz:
        now = time.monotonic()
        for path in (_MAIN_FREQ, _SUB_FREQ):
            _observe(web.command_state_store, path, estimate_hz, at=now, max_age=60.0)
    commands = []
    if queued_hz is not None:
        commands.append(
            {"id": "band", "name": "set_freq", "params": {"freq": queued_hz}}
        )
    commands.append({"id": "power", "name": "set_power", "params": {"level": 90}})

    # The queue is held behind the radio's own lock: every command is
    # admitted against the observations above before any of them runs.
    await radio._mode_lock.acquire()

    async def release_and_wait_for_the_write() -> None:
        assert fake.writes == []
        radio._mode_lock.release()
        await _until(lambda: fake.power_writes)

    try:
        if protocol == "http":
            posts = []
            for command in commands:
                posts.append(asyncio.create_task(_http_post(web, command)))
                await _until(
                    lambda c=command: (
                        c["name"] != "set_power" or _power_lifecycle_states(web)
                    )
                )
            await release_and_wait_for_the_write()
            for status, result in await asyncio.gather(*posts):
                assert (status, result["ok"]) == (200, True), result
        else:
            results = await _ws_commands(
                web, commands, then=release_and_wait_for_the_write
            )
            assert all(result["ok"] for result in results), results
    finally:
        if radio._mode_lock.locked():
            radio._mode_lock.release()

    await _until(lambda: fake.power_writes)
    assert fake.power_writes == [wire]
    # The clamp path adjusts power only: nothing keys the transmitter.
    assert all(not frame.startswith(("TX", "MX")) for frame in fake.writes)

    await _publish_poll(web, radio, poller)
    assert "reconciled" in _power_lifecycle_states(web)


async def _ws_lifecycle_frame(reader: Any, command_id: str, state: str) -> Any:
    """The ``command_lifecycle`` frame of *command_id* in *state*, off the
    still-open socket."""
    async with asyncio.timeout(5.0):
        while True:
            _, frame = await _ws_recv_frame(reader, timeout=5.0)
            message = json.loads(frame)
            if (
                message.get("type") == "command_lifecycle"
                and message.get("commandId") == command_id
                and message.get("state") == state
            ):
                return message


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("radio_hz", "estimate_hz", "queued_hz", "wire"),
    _ADMISSION_VS_WIRE_CASES,
    ids=_ADMISSION_VS_WIRE_IDS,
)
async def test_ws_client_is_told_the_level_the_backend_wrote(
    yaesu_web, radio_hz, estimate_hz, queued_hz, wire
):
    """Pre-review round 4: the ``admitted_level`` in the response is an
    admission-time estimate; the client must end up with the level that was
    written, or its RF-power target (an equality check) never confirms.

    The socket stays open from admission through the queued band change, the
    write and the radio's readback. When the estimate and the wire differ
    (both directions) the response still carries the estimate, and the
    command's ``reconciled`` frame carries the written level -- the very
    value the public state then reports.
    """
    web, radio, fake, poller = yaesu_web
    fake.main_hz = radio_hz
    if estimate_hz:
        now = time.monotonic()
        for path in (_MAIN_FREQ, _SUB_FREQ):
            _observe(web.command_state_store, path, estimate_hz, at=now, max_age=60.0)
    commands = []
    if queued_hz is not None:
        commands.append(
            {"id": "band", "name": "set_freq", "params": {"freq": queued_hz}}
        )
    commands.append({"id": "power", "name": "set_power", "params": {"level": 90}})
    written = int(wire[3:6]) / 100
    estimate_holds = estimate_hz == radio_hz and queued_hz is None

    await radio._mode_lock.acquire()
    try:
        async with _ws_session(web, streams=("events",)) as (reader, writer):
            responses = await _ws_send_commands(reader, writer, commands)
            [power] = [r for r in responses if r["id"] == "power"]
            assert power["ok"], power
            admitted = power["result"]["admitted_level"]
            assert (admitted == written) is estimate_holds, (admitted, written)

            radio._mode_lock.release()
            await _until(lambda: fake.power_writes)
            assert fake.power_writes == [wire]
            await _publish_poll(web, radio, poller)

            frame = await _ws_lifecycle_frame(reader, "power", "reconciled")
    finally:
        if radio._mode_lock.locked():
            radio._mode_lock.release()

    assert set(frame["details"]) == {"revision", "observationSeq", "writtenLevel"}
    assert frame["details"]["writtenLevel"] == written
    state = build_public_state_payload_from_snapshot(
        web.command_state_store.snapshot(), radio=radio, receiver_count=2
    )
    assert state["powerLevel"] == frame["details"]["writtenLevel"]


@pytest.mark.asyncio
@pytest.mark.parametrize("protocol", ["http", "ws"])
@pytest.mark.parametrize(
    ("level", "wire", "admitted"),
    [
        (150, "PC2100;", 1.0),
        (60, "PC2060;", 0.6),
        (-5, "PC2000;", 0.0),
    ],
)
async def test_api_flat_range_without_floor_or_ceiling_caps_at_the_maximum(
    flat_range_web, protocol, level, wire, admitted
):
    """Review round 3: the bare-int clamp on a profile with neither a floor
    nor a ceiling row, end to end: the wire never carries more than the
    operator asked for or the profile's maximum, and nothing keys the radio."""
    web, _radio, fake, _poller = flat_range_web
    command = {"id": "power", "name": "set_power", "params": {"level": level}}
    if protocol == "http":
        status, result = await _http_post(web, command)
        assert (status, result["ok"]) == (200, True), result
        reply = result["result"]
    else:
        [result] = await _ws_commands(
            web, [command], then=lambda: _until(lambda: fake.power_writes)
        )
        assert result["ok"], result
        reply = result["result"]
    await _until(lambda: fake.power_writes)

    assert fake.power_writes == [wire]
    assert reply["admitted_level"] == admitted
    assert fake.queries == [], "no ceiling rows: nothing to read from the radio"
    assert all(not frame.startswith(("TX", "MX")) for frame in fake.writes)
