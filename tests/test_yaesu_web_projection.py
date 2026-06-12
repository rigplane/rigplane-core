"""Yaesu web-projection acceptance: observation-backed TX controls survive.

MOR-447 establishes a Yaesu-driven analogue of the Icom acceptance pattern in
``tests/test_civ_rx_coverage.py`` (value / survives-unrelated-poll /
projects-available). The :class:`YaesuObservationAdapter` ``poll_tx_controls``
lane feeds a :class:`StateStore`; the public payload must then project each TX
control as ``availability == "available"`` and the value must not snap back
when an unrelated observation lands on a later cycle. The scaffold is reusable
by MOR-443 / MOR-448.
"""
# mypy: disable-error-code=untyped-decorator

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from rigplane.core.state_acquisition_policy import RadioAcquisitionProfile
from rigplane.core.state_store import StateStore
from rigplane.core.types import BreakInMode
from rigplane.profiles import get_radio_profile
from rigplane.web.runtime_helpers import build_public_state_payload_from_snapshot

from rigplane.backends.yaesu_cat.observations import YaesuObservationAdapter

# (store path, public fieldStatus key, expected value) for the five TX controls.
_TX_CONTROL_CASES = (
    ("global.operator_controls.power_level", "powerLevel", 0.55),
    ("global.operator_controls.mic_gain", "micGain", 40),
    ("global.tx_state.compressor_on", "compressorOn", True),
    ("global.operator_controls.compressor_level", "compressorLevel", 25),
    ("global.tx_state.vox_on", "voxOn", True),
)

# (store path, public fieldStatus key, expected value) for the MAIN-only RF
# front-end + AGC controls (MOR-443). The attenuator getter returns a bool;
# the int registry path receives the coerced ``int(True) == 1``.
_RF_FRONT_END_CASES = (
    ("receiver.main.operator_controls.att", "main.att", 1),
    ("receiver.main.operator_controls.preamp", "main.preamp", 2),
    ("receiver.main.operator_controls.agc", "main.agc", 3),
)

# (store path, public fieldStatus key, expected value) for the ALC/COMP
# stream-like TX meters (MOR-448/460). Unlike the slow controls, meters expire
# on a short freshness TTL; a freshly applied sample still reports FRESH (no
# decay tick has run), so it projects ``available`` exactly like power/swr.
# ``comp`` projects to the public ``compMeter`` key.
_TX_METER_CASES = (
    ("global.meters.alc", "alcMeter", 42),
    ("global.meters.comp", "compMeter", 30),
)

# (store path, public fieldStatus key, expected value) for the MAIN-only
# IF-shift / narrow DSP controls (MOR-445), emitted in the slow-control lane.
_DSP_CONTROL_CASES = (
    ("receiver.main.operator_controls.if_shift", "main.ifShift", 200),
    ("receiver.main.operator_toggles.narrow", "main.narrow", True),
)

# (store path, public fieldStatus key, expected value) for filter_width
# (MOR-445). It is a ``freq_mode`` ACTIVE-slot field, emitted in the freq/mode
# (medium) lane rather than the slow-control lane.
_FILTER_WIDTH_CASES = (
    ("receiver.main.active.freq_mode.filter_width", "main.filterWidth", 500),
)

# (store path, public fieldStatus key, expected value) for the MAIN-only NB/NR
# levels + derived toggles, auto/manual notch + manual notch freq DSP controls
# (MOR-444), emitted in the slow-control lane. The nb/nr toggles are derived
# from a non-zero level read in the same cycle (``level > 0`` → ON).
_NB_NR_NOTCH_CASES = (
    ("receiver.main.operator_controls.nb_level", "main.nbLevel", 5),
    ("receiver.main.operator_toggles.nb", "main.nb", True),
    ("receiver.main.operator_controls.nr_level", "main.nrLevel", 9),
    ("receiver.main.operator_toggles.nr", "main.nr", True),
    ("receiver.main.operator_toggles.auto_notch", "main.autoNotch", True),
    ("receiver.main.operator_toggles.manual_notch", "main.manualNotch", True),
    (
        "receiver.main.operator_controls.manual_notch_freq",
        "main.manualNotchFreq",
        128,
    ),
)

# (store path, public fieldStatus key, expected value) for split (MOR-446),
# a global tx_state bool emitted in the tx-control lane.
_SPLIT_CASES = (("global.tx_state.split", "split", True),)

# (store path, public fieldStatus key, expected value) for the active-slot /
# "which receiver is active" field (MOR-446), emitted in the slow-control lane.
# The neutral target is the global ``slow_state.active`` "MAIN"/"SUB" str (the
# rigctld VFOA/VFOB mapping + dual-RX runtime consume it); the ``VS`` index 1
# coerces to "SUB".
_ACTIVE_SLOT_CASES = (("global.slow_state.active", "active", "SUB"),)

# (store path, public fieldStatus key, expected value) for the clarifier RIT/XIT
# controls (MOR-454), emitted in the tx-control lane. rit_on/rit_tx are global
# tx_state bools (RX/TX clarifier flags); rit_freq is the global operator-control
# signed Hz offset on the device scale (cross-vendor calibration is MOR-453).
_RIT_CASES = (
    ("global.tx_state.rit_on", "ritOn", True),
    ("global.tx_state.rit_tx", "ritTx", False),
    ("global.operator_controls.rit_freq", "ritFreq", -250),
)

# (store path, public fieldStatus key, expected value) for the tuner + dial-lock
# controls (MOR-455), emitted in the tx-control lane. tuner_status is the global
# operator-control antenna-tuner int (raw device scale 0-3; cross-vendor
# calibration is MOR-453); dial_lock is a global tx_state bool.
_TUNER_LOCK_CASES = (
    ("global.operator_controls.tuner_status", "tunerStatus", 2),
    ("global.tx_state.dial_lock", "dialLock", True),
)

# (store path, public fieldStatus key, expected value) for the CW keyer family
# (MOR-456). key_speed/cw_pitch/break_in/break_in_delay are global
# operator-control ints emitted on the tx-control lane; cw_pitch is Hz, break_in
# is the device int (1=SEMI) from the BreakInMode IntEnum (matching the legacy
# poller's ``1 if get_break_in() else 0`` int store). Raw device scale
# (cross-vendor calibration is MOR-453).
_CW_KEYER_CASES = (
    ("global.operator_controls.key_speed", "keySpeed", 24),
    ("global.operator_controls.cw_pitch", "cwPitch", 600),
    ("global.operator_controls.break_in", "breakIn", 1),
    ("global.operator_controls.break_in_delay", "breakInDelay", 300),
)

# (store path, public fieldStatus key, expected value) for CW spot (MOR-456), a
# global slow_state bool emitted on the slow-control lane beside active-slot.
_CW_SPOT_CASES = (("global.slow_state.cw_spot", "cwSpot", True),)

# (store path, public fieldStatus key, expected value) for the tone / CTCSS
# squelch-type booleans (MOR-457). MAIN-only per-receiver operator toggles
# (public ``main.repeaterTone``/``main.repeaterTsql``) derived from a single CAT
# ``CT`` read on the slow-control lane. The fixture returns P2 code 2 ("TSQL"),
# so repeater_tone=False and repeater_tsql=True.
_CTCSS_CASES = (
    ("receiver.main.operator_toggles.repeater_tone", "main.repeaterTone", False),
    ("receiver.main.operator_toggles.repeater_tsql", "main.repeaterTsql", True),
)

# (store path, public fieldStatus key, expected value) for the CTCSS tone
# frequency (MOR-458). MAIN-only per-receiver operator controls (public
# ``main.toneFreq``/``main.tsqlFreq``) derived from a single CAT ``CN`` read on
# the slow-control lane. The fixture returns tone-chart index 8 (88.5 Hz), so
# both tone_freq and tsql_freq project as 8850 centiHz (single Yaesu tone).
_TONE_FREQ_CASES = (
    ("receiver.main.operator_controls.tone_freq", "main.toneFreq", 8850),
    ("receiver.main.operator_controls.tsql_freq", "main.tsqlFreq", 8850),
)


def _clock() -> float:
    return 123.456


def _profile_state_acquisition() -> RadioAcquisitionProfile:
    profile = get_radio_profile("FTX-1")
    assert profile.state_acquisition is not None
    return profile.state_acquisition


def _make_radio() -> MagicMock:
    radio = MagicMock()
    radio.profile = get_radio_profile("FTX-1")
    radio.capabilities = {
        "dual_rx",
        "meters",
        "tx",
        "vox",
        "compressor",
        "attenuator",
        "preamp",
        "af_level",
        "rf_gain",
        "squelch",
        "filter_width",
        "if_shift",
        "nb",
        "nr",
        "notch",
        "split",
        "rit",
        "tuner",
        "dial_lock",
        "cw",
        "sql_type",
    }
    # Power emits the watt SETPOINT (read_power), not the RM5 meter.
    radio.read_power = AsyncMock(return_value=(2, 55))
    radio.read_mic_gain = AsyncMock(return_value=40)
    radio.read_processor = AsyncMock(return_value=True)
    radio.read_processor_level = AsyncMock(return_value=25)
    radio.read_vox = AsyncMock(return_value=True)
    # RF front-end + AGC reads (MOR-443). Attenuator returns a bool.
    radio.read_attenuator = AsyncMock(return_value=True)
    radio.read_preamp = AsyncMock(return_value=2)
    radio.read_agc = AsyncMock(return_value=3)
    # Per-receiver RX controls so poll_slow_controls runs cleanly.
    radio.read_af_level = AsyncMock(side_effect=lambda receiver=0: 128)
    radio.read_rf_gain = AsyncMock(side_effect=lambda receiver=0: 180)
    radio.read_squelch = AsyncMock(side_effect=lambda receiver=0: 12)
    # ALC/COMP stream-like TX meters (MOR-448/460) plus power/swr for the lane.
    radio.read_alc_meter = AsyncMock(return_value=42)
    radio.read_comp_meter = AsyncMock(return_value=30)
    radio.read_power_meter = AsyncMock(return_value=180)
    radio.read_swr_meter = AsyncMock(return_value=120)
    # An unrelated RX-meter read used to prove no snap-back.
    radio.read_s_meter = AsyncMock(return_value=150)
    # Filter / IF-shift / narrow DSP controls (MOR-445). filter_width is read
    # in the medium (freq/mode) lane; if_shift/narrow in the slow lane.
    radio.read_freq = AsyncMock(side_effect=lambda receiver=0: 14_074_000)
    radio.read_mode = AsyncMock(side_effect=lambda receiver=0: ("USB", None))
    radio.read_ptt = AsyncMock(return_value=False)
    radio.read_filter_width = AsyncMock(return_value=500)
    radio.read_if_shift = AsyncMock(return_value=200)
    radio.read_narrow = AsyncMock(return_value=True)
    # NB/NR levels + derived toggles, auto/manual notch + manual notch freq
    # (MOR-444). Non-zero levels derive nb/nr toggles ON in the same cycle.
    radio.read_nb_level = AsyncMock(return_value=5)
    radio.read_nr_level = AsyncMock(return_value=9)
    radio.read_auto_notch = AsyncMock(return_value=True)
    radio.read_manual_notch = AsyncMock(return_value=True)
    radio.read_manual_notch_freq = AsyncMock(return_value=128)
    # Split + active-slot observation reads (MOR-446). split rides the
    # tx-control lane; active-slot the slow-control lane. ``read_vfo_select``
    # returns the active receiver index (1=SUB) → neutral "SUB" str.
    radio.read_split = AsyncMock(return_value=True)
    radio.read_vfo_select = AsyncMock(return_value=1)
    # Clarifier RIT/XIT observation reads (MOR-454). rit_on/rit_tx ride the
    # tx-control lane (global tx_state bools); rit_freq is the global
    # operator-control signed Hz offset on the device scale.
    radio.read_clarifier = AsyncMock(return_value=(True, False))
    radio.read_clarifier_freq = AsyncMock(return_value=-250)
    # Tuner + dial-lock observation reads (MOR-455). tuner_status is a global
    # operator-control int (raw device scale, 0-3); dial_lock is a global
    # tx_state bool — both ride the tx-control lane.
    radio.read_tuner = AsyncMock(return_value=2)
    radio.read_lock = AsyncMock(return_value=True)
    # CW keyer family observation reads (MOR-456). key_speed/cw_pitch/break_in/
    # break_in_delay ride the tx-control lane (global operator-control ints);
    # cw_spot rides the slow-control lane (global slow_state bool). cw_pitch is
    # Hz; break_in is the BreakInMode IntEnum emitted downstream as int (1=SEMI).
    radio.read_keyer_speed = AsyncMock(return_value=24)
    radio.read_cw_pitch = AsyncMock(return_value=600)
    radio.read_break_in = AsyncMock(return_value=BreakInMode.SEMI)
    radio.read_break_in_delay = AsyncMock(return_value=300)
    radio.read_cw_spot = AsyncMock(return_value=True)
    # Tone / CTCSS squelch-type observation read (MOR-457). ``read_sql_type``
    # returns the CAT ``CT`` P2 code; code 2 ("TSQL": ENC+DEC) derives
    # repeater_tone=False, repeater_tsql=True. MAIN-only, slow-control lane.
    radio.read_sql_type = AsyncMock(return_value=2)
    # CTCSS tone frequency observation read (MOR-458). ``read_ctcss_tone_index``
    # returns the CAT ``CN`` P3 tone-chart index; index 8 (88.5 Hz) maps to 8850
    # centiHz, emitted to BOTH tone_freq and tsql_freq (single Yaesu tone).
    radio.read_ctcss_tone_index = AsyncMock(return_value=8)
    return radio


def _adapter(radio: MagicMock) -> YaesuObservationAdapter:
    return YaesuObservationAdapter(
        radio,
        profile=_profile_state_acquisition(),
        clock=_clock,
    )


async def _apply_tx_controls(store: StateStore, radio: MagicMock) -> None:
    for observation in await _adapter(radio).poll_tx_controls():
        store.apply(observation)


async def _apply_slow_controls(store: StateStore, radio: MagicMock) -> None:
    for observation in await _adapter(radio).poll_slow_controls():
        store.apply(observation)


async def _apply_tx_meters(store: StateStore, radio: MagicMock) -> None:
    for observation in await _adapter(radio).poll_tx_meters():
        store.apply(observation)


async def _apply_medium(store: StateStore, radio: MagicMock) -> None:
    for observation in await _adapter(radio).poll_medium():
        store.apply(observation)


@pytest.mark.asyncio
async def test_tx_controls_observation_value() -> None:
    store = StateStore()
    radio = _make_radio()

    await _apply_tx_controls(store, radio)

    snapshot = store.snapshot()
    for store_path, _public_path, expected in _TX_CONTROL_CASES:
        assert snapshot.field(store_path).value == expected


@pytest.mark.asyncio
async def test_tx_controls_survive_unrelated_observation() -> None:
    store = StateStore()
    radio = _make_radio()

    await _apply_tx_controls(store, radio)
    # An unrelated RX-meter observation on a later cycle must not disturb the
    # TX-control setpoints (no snap-back to the default snapshot value).
    for observation in await _adapter(radio).poll_rx_meters():
        store.apply(observation)

    snapshot = store.snapshot()
    for store_path, _public_path, expected in _TX_CONTROL_CASES:
        assert snapshot.field(store_path).value == expected


@pytest.mark.asyncio
async def test_tx_controls_project_available() -> None:
    store = StateStore()
    radio = _make_radio()

    await _apply_tx_controls(store, radio)
    # An unrelated observation must not re-gate the projected availability.
    for observation in await _adapter(radio).poll_rx_meters():
        store.apply(observation)

    payload = build_public_state_payload_from_snapshot(
        store.snapshot(),
        radio=None,
        receiver_count=2,
    )

    for _store_path, public_path, _expected in _TX_CONTROL_CASES:
        status = payload["fieldStatus"][public_path]
        assert status["observed"] is True
        assert status["availability"] == "available"


@pytest.mark.asyncio
async def test_tx_power_level_projects_watts_against_profile_max() -> None:
    store = StateStore()
    radio = _make_radio()

    await _apply_tx_controls(store, radio)
    payload = build_public_state_payload_from_snapshot(
        store.snapshot(),
        radio=radio,
        receiver_count=2,
    )

    assert store.snapshot().field("global.operator_controls.power_level").value == 0.55
    assert radio.profile.max_watts == 100
    assert payload["powerLevel"] == 0.55


@pytest.mark.asyncio
async def test_rf_front_end_observation_value() -> None:
    store = StateStore()
    radio = _make_radio()

    await _apply_slow_controls(store, radio)

    snapshot = store.snapshot()
    for store_path, _public_path, expected in _RF_FRONT_END_CASES:
        assert snapshot.field(store_path).value == expected


@pytest.mark.asyncio
async def test_rf_front_end_survive_unrelated_observation() -> None:
    store = StateStore()
    radio = _make_radio()

    await _apply_slow_controls(store, radio)
    # An unrelated RX-meter observation on a later cycle must not disturb the
    # RF front-end controls (no snap-back to the default snapshot value).
    for observation in await _adapter(radio).poll_rx_meters():
        store.apply(observation)

    snapshot = store.snapshot()
    for store_path, _public_path, expected in _RF_FRONT_END_CASES:
        assert snapshot.field(store_path).value == expected


@pytest.mark.asyncio
async def test_rf_front_end_project_available() -> None:
    store = StateStore()
    radio = _make_radio()

    await _apply_slow_controls(store, radio)
    # An unrelated observation must not re-gate the projected availability.
    for observation in await _adapter(radio).poll_rx_meters():
        store.apply(observation)

    payload = build_public_state_payload_from_snapshot(
        store.snapshot(),
        radio=None,
        receiver_count=2,
    )

    for _store_path, public_path, _expected in _RF_FRONT_END_CASES:
        status = payload["fieldStatus"][public_path]
        assert status["observed"] is True
        assert status["availability"] == "available"


@pytest.mark.asyncio
async def test_tx_meter_observation_value() -> None:
    store = StateStore()
    radio = _make_radio()

    await _apply_tx_meters(store, radio)

    snapshot = store.snapshot()
    for store_path, _public_path, expected in _TX_METER_CASES:
        assert snapshot.field(store_path).value == expected


@pytest.mark.asyncio
async def test_tx_meter_project_available_for_fresh_sample() -> None:
    store = StateStore()
    radio = _make_radio()

    await _apply_tx_meters(store, radio)

    payload = build_public_state_payload_from_snapshot(
        store.snapshot(),
        radio=None,
        receiver_count=2,
    )

    # Meters expire on a short TTL, but a freshly applied sample still reports
    # FRESH (no decay tick) and therefore projects ``available``.
    for _store_path, public_path, _expected in _TX_METER_CASES:
        status = payload["fieldStatus"][public_path]
        assert status["observed"] is True
        assert status["availability"] == "available"


@pytest.mark.asyncio
async def test_dsp_controls_observation_value() -> None:
    store = StateStore()
    radio = _make_radio()

    await _apply_slow_controls(store, radio)

    snapshot = store.snapshot()
    for store_path, _public_path, expected in _DSP_CONTROL_CASES:
        assert snapshot.field(store_path).value == expected


@pytest.mark.asyncio
async def test_dsp_controls_survive_unrelated_observation() -> None:
    store = StateStore()
    radio = _make_radio()

    await _apply_slow_controls(store, radio)
    # An unrelated RX-meter observation on a later cycle must not disturb the
    # IF-shift / narrow controls (no snap-back to the default snapshot value).
    for observation in await _adapter(radio).poll_rx_meters():
        store.apply(observation)

    snapshot = store.snapshot()
    for store_path, _public_path, expected in _DSP_CONTROL_CASES:
        assert snapshot.field(store_path).value == expected


@pytest.mark.asyncio
async def test_dsp_controls_project_available() -> None:
    store = StateStore()
    radio = _make_radio()

    await _apply_slow_controls(store, radio)
    for observation in await _adapter(radio).poll_rx_meters():
        store.apply(observation)

    payload = build_public_state_payload_from_snapshot(
        store.snapshot(),
        radio=None,
        receiver_count=2,
    )

    for _store_path, public_path, _expected in _DSP_CONTROL_CASES:
        status = payload["fieldStatus"][public_path]
        assert status["observed"] is True
        assert status["availability"] == "available"


@pytest.mark.asyncio
async def test_filter_width_observation_value_lands_at_active_slot_path() -> None:
    store = StateStore()
    radio = _make_radio()

    await _apply_medium(store, radio)

    snapshot = store.snapshot()
    for store_path, _public_path, expected in _FILTER_WIDTH_CASES:
        assert snapshot.field(store_path).value == expected


@pytest.mark.asyncio
async def test_filter_width_survives_unrelated_observation() -> None:
    store = StateStore()
    radio = _make_radio()

    await _apply_medium(store, radio)
    for observation in await _adapter(radio).poll_rx_meters():
        store.apply(observation)

    snapshot = store.snapshot()
    for store_path, _public_path, expected in _FILTER_WIDTH_CASES:
        assert snapshot.field(store_path).value == expected


@pytest.mark.asyncio
async def test_filter_width_projects_available() -> None:
    store = StateStore()
    radio = _make_radio()

    await _apply_medium(store, radio)
    for observation in await _adapter(radio).poll_rx_meters():
        store.apply(observation)

    payload = build_public_state_payload_from_snapshot(
        store.snapshot(),
        radio=None,
        receiver_count=2,
    )

    for _store_path, public_path, _expected in _FILTER_WIDTH_CASES:
        status = payload["fieldStatus"][public_path]
        assert status["observed"] is True
        assert status["availability"] == "available"


@pytest.mark.asyncio
async def test_nb_nr_notch_observation_value() -> None:
    store = StateStore()
    radio = _make_radio()

    await _apply_slow_controls(store, radio)

    snapshot = store.snapshot()
    for store_path, _public_path, expected in _NB_NR_NOTCH_CASES:
        assert snapshot.field(store_path).value == expected


@pytest.mark.asyncio
async def test_nb_nr_notch_survive_unrelated_observation() -> None:
    store = StateStore()
    radio = _make_radio()

    await _apply_slow_controls(store, radio)
    # An unrelated RX-meter observation on a later cycle must not disturb the
    # NB/NR + notch controls (no snap-back to the default snapshot value).
    for observation in await _adapter(radio).poll_rx_meters():
        store.apply(observation)

    snapshot = store.snapshot()
    for store_path, _public_path, expected in _NB_NR_NOTCH_CASES:
        assert snapshot.field(store_path).value == expected


@pytest.mark.asyncio
async def test_nb_nr_notch_project_available() -> None:
    store = StateStore()
    radio = _make_radio()

    await _apply_slow_controls(store, radio)
    for observation in await _adapter(radio).poll_rx_meters():
        store.apply(observation)

    payload = build_public_state_payload_from_snapshot(
        store.snapshot(),
        radio=None,
        receiver_count=2,
    )

    for _store_path, public_path, _expected in _NB_NR_NOTCH_CASES:
        status = payload["fieldStatus"][public_path]
        assert status["observed"] is True
        assert status["availability"] == "available"


@pytest.mark.asyncio
async def test_split_observation_value() -> None:
    store = StateStore()
    radio = _make_radio()

    await _apply_tx_controls(store, radio)

    snapshot = store.snapshot()
    for store_path, _public_path, expected in _SPLIT_CASES:
        assert snapshot.field(store_path).value == expected


@pytest.mark.asyncio
async def test_split_survives_unrelated_observation() -> None:
    store = StateStore()
    radio = _make_radio()

    await _apply_tx_controls(store, radio)
    # An unrelated RX-meter observation on a later cycle must not disturb split
    # (no snap-back to the default snapshot value).
    for observation in await _adapter(radio).poll_rx_meters():
        store.apply(observation)

    snapshot = store.snapshot()
    for store_path, _public_path, expected in _SPLIT_CASES:
        assert snapshot.field(store_path).value == expected


@pytest.mark.asyncio
async def test_split_projects_available() -> None:
    store = StateStore()
    radio = _make_radio()

    await _apply_tx_controls(store, radio)
    for observation in await _adapter(radio).poll_rx_meters():
        store.apply(observation)

    payload = build_public_state_payload_from_snapshot(
        store.snapshot(),
        radio=None,
        receiver_count=2,
    )

    for _store_path, public_path, _expected in _SPLIT_CASES:
        status = payload["fieldStatus"][public_path]
        assert status["observed"] is True
        assert status["availability"] == "available"


@pytest.mark.asyncio
async def test_active_slot_observation_value() -> None:
    store = StateStore()
    radio = _make_radio()

    await _apply_slow_controls(store, radio)

    snapshot = store.snapshot()
    for store_path, _public_path, expected in _ACTIVE_SLOT_CASES:
        assert snapshot.field(store_path).value == expected


@pytest.mark.asyncio
async def test_active_slot_survives_unrelated_observation() -> None:
    store = StateStore()
    radio = _make_radio()

    await _apply_slow_controls(store, radio)
    # An unrelated RX-meter observation on a later cycle must not disturb the
    # active-slot field (no snap-back to the default snapshot value).
    for observation in await _adapter(radio).poll_rx_meters():
        store.apply(observation)

    snapshot = store.snapshot()
    for store_path, _public_path, expected in _ACTIVE_SLOT_CASES:
        assert snapshot.field(store_path).value == expected


@pytest.mark.asyncio
async def test_active_slot_projects_available() -> None:
    store = StateStore()
    radio = _make_radio()

    await _apply_slow_controls(store, radio)
    for observation in await _adapter(radio).poll_rx_meters():
        store.apply(observation)

    payload = build_public_state_payload_from_snapshot(
        store.snapshot(),
        radio=None,
        receiver_count=2,
    )

    for _store_path, public_path, _expected in _ACTIVE_SLOT_CASES:
        status = payload["fieldStatus"][public_path]
        assert status["observed"] is True
        assert status["availability"] == "available"


@pytest.mark.asyncio
async def test_rit_observation_value() -> None:
    store = StateStore()
    radio = _make_radio()

    await _apply_tx_controls(store, radio)

    snapshot = store.snapshot()
    for store_path, _public_path, expected in _RIT_CASES:
        assert snapshot.field(store_path).value == expected


@pytest.mark.asyncio
async def test_rit_survives_unrelated_observation() -> None:
    store = StateStore()
    radio = _make_radio()

    await _apply_tx_controls(store, radio)
    # An unrelated RX-meter observation on a later cycle must not disturb the
    # RIT/XIT controls (no snap-back to the default snapshot value).
    for observation in await _adapter(radio).poll_rx_meters():
        store.apply(observation)

    snapshot = store.snapshot()
    for store_path, _public_path, expected in _RIT_CASES:
        assert snapshot.field(store_path).value == expected


@pytest.mark.asyncio
async def test_rit_projects_available() -> None:
    store = StateStore()
    radio = _make_radio()

    await _apply_tx_controls(store, radio)
    for observation in await _adapter(radio).poll_rx_meters():
        store.apply(observation)

    payload = build_public_state_payload_from_snapshot(
        store.snapshot(),
        radio=None,
        receiver_count=2,
    )

    for _store_path, public_path, _expected in _RIT_CASES:
        status = payload["fieldStatus"][public_path]
        assert status["observed"] is True
        assert status["availability"] == "available"


@pytest.mark.asyncio
async def test_tuner_lock_observation_value() -> None:
    store = StateStore()
    radio = _make_radio()

    await _apply_tx_controls(store, radio)

    snapshot = store.snapshot()
    for store_path, _public_path, expected in _TUNER_LOCK_CASES:
        assert snapshot.field(store_path).value == expected


@pytest.mark.asyncio
async def test_tuner_lock_survives_unrelated_observation() -> None:
    store = StateStore()
    radio = _make_radio()

    await _apply_tx_controls(store, radio)
    # An unrelated RX-meter observation on a later cycle must not disturb the
    # tuner / dial-lock controls (no snap-back to the default snapshot value).
    for observation in await _adapter(radio).poll_rx_meters():
        store.apply(observation)

    snapshot = store.snapshot()
    for store_path, _public_path, expected in _TUNER_LOCK_CASES:
        assert snapshot.field(store_path).value == expected


@pytest.mark.asyncio
async def test_tuner_lock_projects_available() -> None:
    store = StateStore()
    radio = _make_radio()

    await _apply_tx_controls(store, radio)
    for observation in await _adapter(radio).poll_rx_meters():
        store.apply(observation)

    payload = build_public_state_payload_from_snapshot(
        store.snapshot(),
        radio=None,
        receiver_count=2,
    )

    for _store_path, public_path, _expected in _TUNER_LOCK_CASES:
        status = payload["fieldStatus"][public_path]
        assert status["observed"] is True
        assert status["availability"] == "available"


@pytest.mark.asyncio
async def test_cw_keyer_observation_value() -> None:
    store = StateStore()
    radio = _make_radio()

    await _apply_tx_controls(store, radio)

    snapshot = store.snapshot()
    for store_path, _public_path, expected in _CW_KEYER_CASES:
        assert snapshot.field(store_path).value == expected


@pytest.mark.asyncio
async def test_cw_keyer_survives_unrelated_observation() -> None:
    store = StateStore()
    radio = _make_radio()

    await _apply_tx_controls(store, radio)
    # An unrelated RX-meter observation on a later cycle must not disturb the
    # CW keyer controls (no snap-back to the default snapshot value).
    for observation in await _adapter(radio).poll_rx_meters():
        store.apply(observation)

    snapshot = store.snapshot()
    for store_path, _public_path, expected in _CW_KEYER_CASES:
        assert snapshot.field(store_path).value == expected


@pytest.mark.asyncio
async def test_cw_keyer_projects_available() -> None:
    store = StateStore()
    radio = _make_radio()

    await _apply_tx_controls(store, radio)
    for observation in await _adapter(radio).poll_rx_meters():
        store.apply(observation)

    payload = build_public_state_payload_from_snapshot(
        store.snapshot(),
        radio=None,
        receiver_count=2,
    )

    for _store_path, public_path, _expected in _CW_KEYER_CASES:
        status = payload["fieldStatus"][public_path]
        assert status["observed"] is True
        assert status["availability"] == "available"


@pytest.mark.asyncio
async def test_cw_spot_observation_value() -> None:
    store = StateStore()
    radio = _make_radio()

    await _apply_slow_controls(store, radio)

    snapshot = store.snapshot()
    for store_path, _public_path, expected in _CW_SPOT_CASES:
        assert snapshot.field(store_path).value == expected


@pytest.mark.asyncio
async def test_cw_spot_survives_unrelated_observation() -> None:
    store = StateStore()
    radio = _make_radio()

    await _apply_slow_controls(store, radio)
    # An unrelated RX-meter observation on a later cycle must not disturb the
    # CW spot field (no snap-back to the default snapshot value).
    for observation in await _adapter(radio).poll_rx_meters():
        store.apply(observation)

    snapshot = store.snapshot()
    for store_path, _public_path, expected in _CW_SPOT_CASES:
        assert snapshot.field(store_path).value == expected


@pytest.mark.asyncio
async def test_cw_spot_projects_available() -> None:
    store = StateStore()
    radio = _make_radio()

    await _apply_slow_controls(store, radio)
    for observation in await _adapter(radio).poll_rx_meters():
        store.apply(observation)

    payload = build_public_state_payload_from_snapshot(
        store.snapshot(),
        radio=None,
        receiver_count=2,
    )

    for _store_path, public_path, _expected in _CW_SPOT_CASES:
        status = payload["fieldStatus"][public_path]
        assert status["observed"] is True
        assert status["availability"] == "available"


@pytest.mark.asyncio
async def test_ctcss_observation_value() -> None:
    store = StateStore()
    radio = _make_radio()

    await _apply_slow_controls(store, radio)

    snapshot = store.snapshot()
    for store_path, _public_path, expected in _CTCSS_CASES:
        assert snapshot.field(store_path).value == expected


@pytest.mark.asyncio
async def test_ctcss_survives_unrelated_observation() -> None:
    store = StateStore()
    radio = _make_radio()

    await _apply_slow_controls(store, radio)
    # An unrelated RX-meter observation on a later cycle must not disturb the
    # CTCSS booleans (no snap-back to the default snapshot value).
    for observation in await _adapter(radio).poll_rx_meters():
        store.apply(observation)

    snapshot = store.snapshot()
    for store_path, _public_path, expected in _CTCSS_CASES:
        assert snapshot.field(store_path).value == expected


@pytest.mark.asyncio
async def test_ctcss_projects_available() -> None:
    store = StateStore()
    radio = _make_radio()

    await _apply_slow_controls(store, radio)
    for observation in await _adapter(radio).poll_rx_meters():
        store.apply(observation)

    payload = build_public_state_payload_from_snapshot(
        store.snapshot(),
        radio=None,
        receiver_count=2,
    )

    for _store_path, public_path, _expected in _CTCSS_CASES:
        status = payload["fieldStatus"][public_path]
        assert status["observed"] is True
        assert status["availability"] == "available"


@pytest.mark.asyncio
async def test_tone_freq_observation_value() -> None:
    store = StateStore()
    radio = _make_radio()

    await _apply_slow_controls(store, radio)

    snapshot = store.snapshot()
    for store_path, _public_path, expected in _TONE_FREQ_CASES:
        assert snapshot.field(store_path).value == expected


@pytest.mark.asyncio
async def test_tone_freq_survives_unrelated_observation() -> None:
    store = StateStore()
    radio = _make_radio()

    await _apply_slow_controls(store, radio)
    # An unrelated RX-meter observation on a later cycle must not disturb the
    # CTCSS tone-frequency fields (no snap-back to the default snapshot value).
    for observation in await _adapter(radio).poll_rx_meters():
        store.apply(observation)

    snapshot = store.snapshot()
    for store_path, _public_path, expected in _TONE_FREQ_CASES:
        assert snapshot.field(store_path).value == expected


@pytest.mark.asyncio
async def test_tone_freq_projects_available() -> None:
    store = StateStore()
    radio = _make_radio()

    await _apply_slow_controls(store, radio)
    for observation in await _adapter(radio).poll_rx_meters():
        store.apply(observation)

    payload = build_public_state_payload_from_snapshot(
        store.snapshot(),
        radio=None,
        receiver_count=2,
    )

    for _store_path, public_path, _expected in _TONE_FREQ_CASES:
        status = payload["fieldStatus"][public_path]
        assert status["observed"] is True
        assert status["availability"] == "available"
