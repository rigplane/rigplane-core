"""Observation adapter coverage for the Yaesu CAT backend."""
# mypy: disable-error-code=untyped-decorator

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from rigplane.core.state_acquisition_policy import RadioAcquisitionProfile
from rigplane.core.types import BreakInMode
from rigplane.profiles import get_radio_profile
from rigplane.radio_state import RadioState

from rigplane.backends.yaesu_cat.observations import YaesuObservationAdapter
from rigplane.backends.yaesu_cat.radio import YaesuCatRadio


def _clock() -> float:
    return 123.456


def _make_radio() -> MagicMock:
    radio = MagicMock()
    radio.capabilities = {
        "dual_rx",
        "af_level",
        "rf_gain",
        "squelch",
        "meters",
        "filter_width",
        "if_shift",
        "tx",
        "vox",
        "compressor",
        "attenuator",
        "preamp",
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
    radio.get_freq = AsyncMock(
        side_effect=lambda receiver=0: 14_074_000 if receiver == 0 else 7_074_000
    )
    radio.read_freq = AsyncMock(
        side_effect=lambda receiver=0: 14_074_000 if receiver == 0 else 7_074_000
    )
    radio.get_mode = AsyncMock(
        side_effect=lambda receiver=0: ("USB", None) if receiver == 0 else ("LSB", None)
    )
    radio.read_mode = AsyncMock(
        side_effect=lambda receiver=0: ("USB", None) if receiver == 0 else ("LSB", None)
    )
    radio.get_ptt = AsyncMock(return_value=False)
    radio.read_ptt = AsyncMock(return_value=False)
    radio.get_af_level = AsyncMock(
        side_effect=lambda receiver=0: 128 if receiver == 0 else 64
    )
    radio.read_af_level = AsyncMock(
        side_effect=lambda receiver=0: 128 if receiver == 0 else 64
    )
    radio.get_rf_gain = AsyncMock(
        side_effect=lambda receiver=0: 180 if receiver == 0 else 90
    )
    radio.read_rf_gain = AsyncMock(
        side_effect=lambda receiver=0: 180 if receiver == 0 else 90
    )
    radio.get_squelch = AsyncMock(
        side_effect=lambda receiver=0: 12 if receiver == 0 else 8
    )
    radio.read_squelch = AsyncMock(
        side_effect=lambda receiver=0: 12 if receiver == 0 else 8
    )
    # RF front-end + AGC controls (MOR-443). The FTX-1 attenuator getter
    # returns a bool; the registry FieldPath is int, so the adapter coerces.
    radio.get_attenuator = AsyncMock(return_value=True)
    radio.read_attenuator = AsyncMock(return_value=True)
    radio.get_preamp = AsyncMock(return_value=2)
    radio.read_preamp = AsyncMock(return_value=2)
    radio.get_agc = AsyncMock(return_value=3)
    radio.read_agc = AsyncMock(return_value=3)
    # Filter / IF-shift / narrow DSP controls (MOR-445). MAIN-only: the
    # ``read_filter_width`` decode reads (but never writes) legacy state, and
    # IF-shift/narrow have no per-receiver CAT command (no IS1/NA1).
    radio.get_filter_width = AsyncMock(return_value=500)
    radio.read_filter_width = AsyncMock(return_value=500)
    radio.get_if_shift = AsyncMock(return_value=200)
    radio.read_if_shift = AsyncMock(return_value=200)
    radio.get_narrow = AsyncMock(return_value=True)
    radio.read_narrow = AsyncMock(return_value=True)
    # NB/NR levels + derived toggles, auto/manual notch DSP controls (MOR-444).
    # MAIN-only: no per-receiver CAT command (no NL1/RL1/BC1/BP10/BP11). The
    # nb/nr toggles are derived from the non-zero level read in the same cycle.
    radio.get_nb_level = AsyncMock(return_value=5)
    radio.read_nb_level = AsyncMock(return_value=5)
    radio.get_nr_level = AsyncMock(return_value=9)
    radio.read_nr_level = AsyncMock(return_value=9)
    radio.get_auto_notch = AsyncMock(return_value=True)
    radio.read_auto_notch = AsyncMock(return_value=True)
    radio.read_manual_notch = AsyncMock(return_value=True)
    radio.read_manual_notch_freq = AsyncMock(return_value=128)
    # TX meters: ALC mirrors power/swr as a stream-like meter (MOR-448).
    radio.get_alc_meter = AsyncMock(return_value=42)
    radio.read_alc_meter = AsyncMock(return_value=42)
    radio.get_power_meter = AsyncMock(return_value=180)
    radio.read_power_meter = AsyncMock(return_value=180)
    radio.get_swr_meter = AsyncMock(return_value=120)
    radio.read_swr_meter = AsyncMock(return_value=120)
    # Global TX / operator-control setpoints (MOR-447).
    radio.get_power = AsyncMock(return_value=(2, 55))
    radio.read_power = AsyncMock(return_value=(2, 55))
    radio.get_mic_gain = AsyncMock(return_value=40)
    radio.read_mic_gain = AsyncMock(return_value=40)
    radio.get_processor = AsyncMock(return_value=True)
    radio.read_processor = AsyncMock(return_value=True)
    radio.get_processor_level = AsyncMock(return_value=25)
    radio.read_processor_level = AsyncMock(return_value=25)
    radio.get_vox = AsyncMock(return_value=True)
    radio.read_vox = AsyncMock(return_value=True)
    # Split + active-slot observation reads (MOR-446). ``read_split`` is a
    # global tx_state bool; ``read_vfo_select`` returns the active receiver
    # index (0=MAIN, 1=SUB) which coerces to the neutral "MAIN"/"SUB" str.
    radio.get_split = AsyncMock(return_value=True)
    radio.read_split = AsyncMock(return_value=True)
    radio.get_vfo_select = AsyncMock(return_value=1)
    radio.read_vfo_select = AsyncMock(return_value=1)
    # Clarifier RIT/XIT observation reads (MOR-454). ``read_clarifier`` returns
    # the (rx, tx) clarifier flags; ``read_clarifier_freq`` returns the signed
    # Hz offset on the device scale.
    radio.get_clarifier = AsyncMock(return_value=(True, False))
    radio.read_clarifier = AsyncMock(return_value=(True, False))
    radio.get_clarifier_freq = AsyncMock(return_value=-250)
    radio.read_clarifier_freq = AsyncMock(return_value=-250)
    # Tuner + dial-lock observation reads (MOR-455). ``read_tuner`` returns the
    # raw device int (0=OFF, 1=ON, 2=tuning, 3=tune-start); ``read_lock``
    # returns the dial-lock bool.
    radio.get_tuner = AsyncMock(return_value=2)
    radio.read_tuner = AsyncMock(return_value=2)
    radio.get_lock = AsyncMock(return_value=True)
    radio.read_lock = AsyncMock(return_value=True)
    # CW keyer family observation reads (MOR-456). ``read_keyer_speed`` returns
    # the WPM; ``read_cw_pitch`` returns the sidetone in Hz (idx → 300+idx*10);
    # ``read_break_in`` returns the BreakInMode IntEnum (emitted as int 0/1);
    # ``read_break_in_delay`` returns the QSK delay in ms; ``read_cw_spot``
    # returns the CW-spot bool.
    radio.get_keyer_speed = AsyncMock(return_value=24)
    radio.read_keyer_speed = AsyncMock(return_value=24)
    radio.get_cw_pitch = AsyncMock(return_value=600)
    radio.read_cw_pitch = AsyncMock(return_value=600)
    radio.get_break_in = AsyncMock(return_value=BreakInMode.SEMI)
    radio.read_break_in = AsyncMock(return_value=BreakInMode.SEMI)
    radio.get_break_in_delay = AsyncMock(return_value=300)
    radio.read_break_in_delay = AsyncMock(return_value=300)
    radio.get_cw_spot = AsyncMock(return_value=True)
    radio.read_cw_spot = AsyncMock(return_value=True)
    # Tone / CTCSS squelch-type observation read (MOR-457). ``read_sql_type``
    # returns the CAT ``CT`` P2 code; the default value 1 ("TONE": ENC ON /
    # DEC OFF) derives repeater_tone=True, repeater_tsql=False.
    radio.get_sql_type = AsyncMock(return_value=1)
    radio.read_sql_type = AsyncMock(return_value=1)
    # CTCSS tone frequency observation read (MOR-458). ``read_ctcss_tone_index``
    # returns the CAT ``CN`` P3 tone-chart index (0-49); the default index 8 maps
    # to 88.5 Hz -> 8850 centiHz, emitted to BOTH tone_freq and tsql_freq.
    radio.get_ctcss_tone = AsyncMock(return_value=8850)
    radio.read_ctcss_tone_index = AsyncMock(return_value=8)
    return radio


def _profile_state_acquisition() -> RadioAcquisitionProfile:
    profile = get_radio_profile("FTX-1")
    assert profile.state_acquisition is not None
    return profile.state_acquisition


class _SideEffectingYaesuRadio:
    capabilities = {
        "dual_rx",
        "af_level",
        "rf_gain",
        "squelch",
        "meters",
        "tx",
        "vox",
        "compressor",
        "rit",
        "tuner",
        "dial_lock",
        "cw",
        "sql_type",
    }

    def __init__(self) -> None:
        self.radio_state = RadioState()
        self.radio_state.main.freq = 1
        self.radio_state.main.mode = "INIT-MAIN"
        self.radio_state.sub.freq = 2
        self.radio_state.sub.mode = "INIT-SUB"
        self.radio_state.main.s_meter = 3
        self.radio_state.sub.s_meter = 4
        self.radio_state.alc_meter = 5
        self.radio_state.power_meter = 5
        self.radio_state.swr_meter = 6
        self.radio_state.main.af_level = 7
        self.radio_state.main.rf_gain = 8
        self.radio_state.main.squelch = 9
        self.radio_state.sub.af_level = 10
        self.radio_state.sub.rf_gain = 11
        self.radio_state.sub.squelch = 12
        self.radio_state.power_level = 13
        self.radio_state.mic_gain = 14
        self.radio_state.compressor_on = False
        self.radio_state.compressor_level = 15
        self.radio_state.vox_on = False
        self.radio_state.main.att = 16
        self.radio_state.main.preamp = 17
        self.radio_state.main.agc = 18
        self.radio_state.main.filter_width = 19
        self.radio_state.main.if_shift = 20
        self.radio_state.main.narrow = False
        self.radio_state.split = False
        self.radio_state.active = "MAIN"
        self.radio_state.vfo_select = 0
        self.radio_state.rit_on = False
        self.radio_state.rit_tx = False
        self.radio_state.rit_freq = 21
        self.radio_state.tuner_status = 0
        self.radio_state.dial_lock = False
        self.radio_state.key_speed = 99
        self.radio_state.cw_pitch = 999
        self.radio_state.break_in = 2
        self.radio_state.break_in_delay = 888
        self.radio_state.cw_spot = False
        self.radio_state.main.repeater_tone = True
        self.radio_state.main.repeater_tsql = True
        # CTCSS tone freq (MOR-458): pre-seed sentinel centiHz values; a pure
        # read_ctcss_tone_index must not overwrite these from legacy state.
        self.radio_state.main.tone_freq = 11111
        self.radio_state.main.tsql_freq = 22222

    async def read_freq(self, receiver: int = 0) -> int:
        return 14_074_000 if receiver == 0 else 7_074_000

    async def get_freq(self, receiver: int = 0) -> int:
        value = await self.read_freq(receiver)
        target = self.radio_state.main if receiver == 0 else self.radio_state.sub
        target.freq = value
        return value

    async def read_mode(self, receiver: int = 0) -> tuple[str, int | None]:
        return ("USB" if receiver == 0 else "LSB"), None

    async def get_mode(self, receiver: int = 0) -> tuple[str, int | None]:
        value, filter_width = await self.read_mode(receiver)
        target = self.radio_state.main if receiver == 0 else self.radio_state.sub
        target.mode = value
        return value, filter_width

    async def read_ptt(self) -> bool:
        return True

    async def get_ptt(self) -> bool:
        value = await self.read_ptt()
        self.radio_state.ptt = value
        return value

    async def read_s_meter(self, receiver: int = 0) -> int:
        return 150 if receiver == 0 else 75

    async def get_s_meter(self, receiver: int = 0) -> int:
        value = await self.read_s_meter(receiver)
        target = self.radio_state.main if receiver == 0 else self.radio_state.sub
        target.s_meter = value
        return value

    async def read_alc_meter(self) -> int:
        return 200

    async def get_alc_meter(self) -> int:
        value = await self.read_alc_meter()
        self.radio_state.alc_meter = value
        return value

    async def read_power_meter(self) -> int:
        return 180

    async def get_power_meter(self) -> int:
        value = await self.read_power_meter()
        self.radio_state.power_meter = value
        return value

    async def read_swr_meter(self) -> int:
        return 120

    async def get_swr_meter(self) -> int:
        value = await self.read_swr_meter()
        self.radio_state.swr_meter = value
        return value

    async def read_af_level(self, receiver: int = 0) -> int:
        return 128 if receiver == 0 else 64

    async def get_af_level(self, receiver: int = 0) -> int:
        value = await self.read_af_level(receiver)
        target = self.radio_state.main if receiver == 0 else self.radio_state.sub
        target.af_level = value
        return value

    async def read_rf_gain(self, receiver: int = 0) -> int:
        return 180 if receiver == 0 else 90

    async def get_rf_gain(self, receiver: int = 0) -> int:
        value = await self.read_rf_gain(receiver)
        target = self.radio_state.main if receiver == 0 else self.radio_state.sub
        target.rf_gain = value
        return value

    async def read_squelch(self, receiver: int = 0) -> int:
        return 12 if receiver == 0 else 8

    async def get_squelch(self, receiver: int = 0) -> int:
        value = await self.read_squelch(receiver)
        target = self.radio_state.main if receiver == 0 else self.radio_state.sub
        target.squelch = value
        return value

    async def read_power(self) -> tuple[int, int]:
        return (2, 55)

    async def get_power(self) -> tuple[int, int]:
        head, watts = await self.read_power()
        self.radio_state.power_level = watts
        return head, watts

    async def read_mic_gain(self) -> int:
        return 40

    async def get_mic_gain(self) -> int:
        value = await self.read_mic_gain()
        self.radio_state.mic_gain = value
        return value

    async def read_processor(self) -> bool:
        return True

    async def get_processor(self) -> bool:
        value = await self.read_processor()
        self.radio_state.compressor_on = value
        return value

    async def read_processor_level(self) -> int:
        return 25

    async def get_processor_level(self) -> int:
        value = await self.read_processor_level()
        self.radio_state.compressor_level = value
        return value

    async def read_vox(self) -> bool:
        return True

    async def get_vox(self) -> bool:
        value = await self.read_vox()
        self.radio_state.vox_on = value
        return value

    async def read_attenuator(self, receiver: int = 0) -> bool:
        return True

    async def get_attenuator(self, receiver: int = 0) -> bool:
        value = await self.read_attenuator(receiver)
        self.radio_state.main.att = int(value)
        return value

    async def read_preamp(self, receiver: int = 0) -> int:
        return 2

    async def get_preamp(self, band: int = 0) -> int:
        value = await self.read_preamp(band)
        self.radio_state.main.preamp = value
        return value

    async def read_agc(self, receiver: int = 0) -> int:
        return 3

    async def get_agc(self, receiver: int = 0) -> int:
        value = await self.read_agc(receiver)
        self.radio_state.main.agc = value
        return value

    async def read_filter_width(self, receiver: int = 0) -> int:
        return 500

    async def get_filter_width(self, receiver: int = 0) -> int:
        value = await self.read_filter_width(receiver)
        self.radio_state.main.filter_width = value
        return value

    async def read_if_shift(self, receiver: int = 0) -> int:
        return 200

    async def get_if_shift(self, receiver: int = 0) -> int:
        value = await self.read_if_shift(receiver)
        self.radio_state.main.if_shift = value
        return value

    async def read_narrow(self, receiver: int = 0) -> bool:
        return True

    async def get_narrow(self, receiver: int = 0) -> bool:
        value = await self.read_narrow(receiver)
        self.radio_state.main.narrow = value
        return value

    async def read_split(self) -> bool:
        return True

    async def get_split(self) -> bool:
        value = await self.read_split()
        self.radio_state.split = value
        return value

    async def read_vfo_select(self) -> int:
        return 1

    async def get_vfo_select(self) -> int:
        value = await self.read_vfo_select()
        self.radio_state.vfo_select = value
        self.radio_state.active = "SUB" if value else "MAIN"
        return value

    async def read_clarifier(self, receiver: int = 0) -> tuple[bool, bool]:
        return True, False

    async def get_clarifier(self, receiver: int = 0) -> tuple[bool, bool]:
        rx_clar, tx_clar = await self.read_clarifier(receiver)
        self.radio_state.rit_on = rx_clar
        self.radio_state.rit_tx = tx_clar
        return rx_clar, tx_clar

    async def read_clarifier_freq(self, receiver: int = 0) -> int:
        return -250

    async def get_clarifier_freq(self, receiver: int = 0) -> int:
        value = await self.read_clarifier_freq(receiver)
        self.radio_state.rit_freq = value
        return value

    async def read_tuner(self) -> int:
        return 2

    async def get_tuner(self) -> int:
        value = await self.read_tuner()
        self.radio_state.tuner_status = value
        return value

    async def read_lock(self) -> bool:
        return True

    async def get_lock(self) -> bool:
        value = await self.read_lock()
        self.radio_state.dial_lock = value
        return value

    async def read_keyer_speed(self) -> int:
        return 24

    async def get_keyer_speed(self) -> int:
        value = await self.read_keyer_speed()
        self.radio_state.key_speed = value
        return value

    async def read_cw_pitch(self) -> int:
        return 600

    async def get_cw_pitch(self) -> int:
        value = await self.read_cw_pitch()
        self.radio_state.cw_pitch = value
        return value

    async def read_break_in(self) -> BreakInMode:
        return BreakInMode.SEMI

    async def get_break_in(self) -> BreakInMode:
        value = await self.read_break_in()
        self.radio_state.break_in = int(value)
        return value

    async def read_break_in_delay(self) -> int:
        return 300

    async def get_break_in_delay(self) -> int:
        value = await self.read_break_in_delay()
        self.radio_state.break_in_delay = value
        return value

    async def read_cw_spot(self) -> bool:
        return True

    async def get_cw_spot(self) -> bool:
        value = await self.read_cw_spot()
        self.radio_state.cw_spot = value
        return value

    async def read_sql_type(self, receiver: int = 0) -> int:
        # CAT ``CT`` P2 code 1 = "TONE" (ENC ON / DEC OFF). Pure read: the
        # derived neutral booleans must come from the observation pipeline, not
        # from any legacy-state write (the pre-seeded main.repeater_tone/tsql=True
        # is the impossible-via-CT combination that proves no mutation).
        return 1

    async def get_sql_type(self, receiver: int = 0) -> int:
        return await self.read_sql_type(receiver)

    async def read_ctcss_tone_index(self, receiver: int = 0) -> int:
        # CAT ``CN`` P3 tone-chart index 8 = 88.5 Hz. Pure read: the derived
        # centiHz value must come from the observation pipeline, not from any
        # legacy-state write (the pre-seeded sentinel tone_freq/tsql_freq prove
        # no mutation).
        return 8


@pytest.mark.asyncio
async def test_medium_poll_emits_frequency_mode_and_ptt_observations() -> None:
    radio = _make_radio()
    adapter = YaesuObservationAdapter(
        radio,
        profile=_profile_state_acquisition(),
        clock=_clock,
    )

    observations = await adapter.poll_medium()

    # filter_width is a ``freq_mode`` field emitted in the freq/mode lane
    # (MOR-445), MAIN-only and gated on the ``filter_width`` capability, after
    # PTT — mirroring the legacy poller which reads it in ``_poll_medium``.
    assert [(str(item.path), item.value) for item in observations] == [
        ("receiver.main.active.freq_mode.freq_hz", 14_074_000),
        ("receiver.main.active.freq_mode.mode", "USB"),
        ("receiver.sub.active.freq_mode.freq_hz", 7_074_000),
        ("receiver.sub.active.freq_mode.mode", "LSB"),
        ("global.tx_state.ptt", False),
        ("receiver.main.active.freq_mode.filter_width", 500),
    ]
    radio.read_filter_width.assert_awaited_once()
    radio.get_filter_width.assert_not_awaited()
    assert {item.source.source for item in observations} == {"yaesu_poll_response"}
    assert {item.source.provider for item in observations} == {"yaesu_cat"}
    assert {item.source.transport for item in observations} == {"serial"}
    assert all(item.timestamp_monotonic == 123.456 for item in observations)
    # freq/mode/ptt use the default 8.0s freshness TTL; filter_width carries
    # its own slow-control TTL (120.0s) from the per-field policy, even though
    # it shares the freq/mode lane (MOR-445).
    by_path = {str(item.path): item for item in observations}
    assert by_path["global.tx_state.ptt"].max_age == 8.0
    assert by_path["receiver.main.active.freq_mode.freq_hz"].max_age == 8.0
    assert by_path["receiver.main.active.freq_mode.filter_width"].max_age == 120.0
    assert all(item.source.capability_id == str(item.path) for item in observations)


@pytest.mark.asyncio
async def test_slow_poll_emits_declared_control_observations_only() -> None:
    radio = _make_radio()
    adapter = YaesuObservationAdapter(
        radio,
        profile=_profile_state_acquisition(),
        clock=_clock,
    )

    observations = await adapter.poll_slow_controls()

    # ATT/preamp/AGC are MAIN-only: the FTX-1 has no per-receiver CAT
    # command for these front-end controls (no RA1/PA1/GT1), matching the
    # legacy poller which only writes ``main.{att,preamp,agc}``. The
    # attenuator ``read`` returns a bool; the int registry path receives the
    # coerced ``int(True) == 1``.
    # IF-shift (operator_controls, gated on ``if_shift`` cap) and narrow
    # (operator_toggles, unconditional like AGC) are MAIN-only DSP controls
    # (MOR-445), emitted after the RF front-end in the slow-control lane.
    # NB/NR levels + derived nb/nr toggles, auto/manual notch + manual notch
    # freq (MOR-444) follow, MAIN-only, gated on ``nb``/``nr``/``notch`` caps.
    # The nb/nr toggles are derived from the level read in the same cycle
    # (``level > 0``); a non-zero level → toggle ON, a single read each.
    assert [(str(item.path), item.value) for item in observations] == [
        ("receiver.main.operator_controls.af_level", 128),
        ("receiver.main.operator_controls.rf_gain", 180),
        ("receiver.main.operator_controls.squelch", 12),
        ("receiver.sub.operator_controls.af_level", 64),
        ("receiver.sub.operator_controls.rf_gain", 90),
        ("receiver.sub.operator_controls.squelch", 8),
        ("receiver.main.operator_controls.att", 1),
        ("receiver.main.operator_controls.preamp", 2),
        ("receiver.main.operator_controls.agc", 3),
        ("receiver.main.operator_controls.if_shift", 200),
        ("receiver.main.operator_toggles.narrow", True),
        ("receiver.main.operator_controls.nb_level", 5),
        ("receiver.main.operator_toggles.nb", True),
        ("receiver.main.operator_controls.nr_level", 9),
        ("receiver.main.operator_toggles.nr", True),
        ("receiver.main.operator_toggles.auto_notch", True),
        ("receiver.main.operator_toggles.manual_notch", True),
        ("receiver.main.operator_controls.manual_notch_freq", 128),
        # Tone / CTCSS squelch-type (MOR-457): MAIN-only per-receiver toggles
        # grouped with the other receiver DSP toggles, derived from a single
        # ``read_sql_type(0)`` CAT ``CT`` read. The default code 1 ("TONE")
        # yields repeater_tone=True, repeater_tsql=False; both are emitted every
        # cycle. Gated on the ``sql_type`` cap.
        ("receiver.main.operator_toggles.repeater_tone", True),
        ("receiver.main.operator_toggles.repeater_tsql", False),
        # CTCSS tone frequency (MOR-458): MAIN-only, a single ``read_ctcss_tone_index``
        # CAT ``CN`` read mapped index -> Hz -> centiHz. The FTX-1 has one CTCSS
        # tone (CN P2=0) used for both TONE and TSQL, so the same centiHz value
        # (default index 8 = 88.5 Hz = 8850) is emitted to BOTH paths. Gated on
        # the ``sql_type`` cap.
        ("receiver.main.operator_controls.tone_freq", 8850),
        ("receiver.main.operator_controls.tsql_freq", 8850),
        # active-slot (MOR-446): the global "which receiver is active" field,
        # emitted in the slow-control lane, unconditional (no FTX-1 cap gate)
        # like the legacy poller's always-on ``get_vfo_select`` read. The int
        # receiver index (1=SUB) coerces to the neutral "MAIN"/"SUB" str.
        ("global.slow_state.active", "SUB"),
        # cw_spot (MOR-456): global slow_state bool (CAT ``CS``), closes the
        # slow-control lane, gated on the legacy poller's ``"cw" in caps`` gate.
        ("global.slow_state.cw_spot", True),
    ]
    assert all(item.source.source == "yaesu_poll_response" for item in observations)
    assert all(item.max_age == 120.0 for item in observations)
    assert radio.read_vfo_select.await_count == 1
    radio.get_vfo_select.assert_not_awaited()
    assert radio.read_cw_spot.await_count == 1
    radio.get_cw_spot.assert_not_awaited()
    assert radio.read_af_level.await_count == 2
    assert radio.read_rf_gain.await_count == 2
    assert radio.read_squelch.await_count == 2
    assert radio.read_attenuator.await_count == 1
    assert radio.read_preamp.await_count == 1
    assert radio.read_agc.await_count == 1
    assert radio.read_if_shift.await_count == 1
    assert radio.read_narrow.await_count == 1
    # Single CAT read per family — nb/nr toggles derive from the level read.
    assert radio.read_nb_level.await_count == 1
    assert radio.read_nr_level.await_count == 1
    assert radio.read_auto_notch.await_count == 1
    assert radio.read_manual_notch.await_count == 1
    assert radio.read_manual_notch_freq.await_count == 1
    # Tone/CTCSS: a SINGLE read_sql_type feeds both derived booleans (MOR-457).
    assert radio.read_sql_type.await_count == 1
    radio.get_sql_type.assert_not_awaited()
    # CTCSS tone freq: a SINGLE read_ctcss_tone_index feeds both tone_freq and
    # tsql_freq in centiHz (MOR-458).
    assert radio.read_ctcss_tone_index.await_count == 1
    radio.get_ctcss_tone.assert_not_awaited()
    radio.get_af_level.assert_not_awaited()
    radio.get_rf_gain.assert_not_awaited()
    radio.get_squelch.assert_not_awaited()
    radio.get_attenuator.assert_not_awaited()
    radio.get_preamp.assert_not_awaited()
    radio.get_agc.assert_not_awaited()
    radio.get_if_shift.assert_not_awaited()
    radio.get_narrow.assert_not_awaited()
    radio.get_nb_level.assert_not_awaited()
    radio.get_nr_level.assert_not_awaited()
    radio.get_auto_notch.assert_not_awaited()


@pytest.mark.asyncio
async def test_slow_poll_skips_sub_controls_without_matching_runtime_capability() -> None:
    radio = _make_radio()
    radio.capabilities = {"dual_rx", "af_level", "tx"}
    adapter = YaesuObservationAdapter(
        radio,
        profile=_profile_state_acquisition(),
        clock=_clock,
    )

    observations = await adapter.poll_slow_controls()

    # ATT/preamp/if_shift are gated by their runtime capabilities (dropped
    # here); AGC and narrow have no FTX-1 capability tag and mirror the legacy
    # poller's unconditional poll, so they still emit when policy is pollable.
    assert [(str(item.path), item.value) for item in observations] == [
        ("receiver.main.operator_controls.af_level", 128),
        ("receiver.sub.operator_controls.af_level", 64),
        ("receiver.main.operator_controls.agc", 3),
        ("receiver.main.operator_toggles.narrow", True),
        # active-slot is unconditional (like AGC/narrow), so it still emits when
        # policy is pollable even though no FTX-1 capability tag gates it.
        ("global.slow_state.active", "SUB"),
    ]
    assert radio.read_af_level.await_count == 2
    radio.read_rf_gain.assert_not_awaited()
    radio.read_squelch.assert_not_awaited()
    radio.read_attenuator.assert_not_awaited()
    radio.read_preamp.assert_not_awaited()
    assert radio.read_agc.await_count == 1
    radio.read_if_shift.assert_not_awaited()
    assert radio.read_narrow.await_count == 1
    # NB/NR/notch dropped: their runtime caps are absent (MOR-444).
    radio.read_nb_level.assert_not_awaited()
    radio.read_nr_level.assert_not_awaited()
    radio.read_auto_notch.assert_not_awaited()
    radio.read_manual_notch.assert_not_awaited()
    radio.read_manual_notch_freq.assert_not_awaited()
    # Tone/CTCSS dropped: the ``sql_type`` runtime cap is absent here (MOR-457).
    radio.read_sql_type.assert_not_awaited()
    # CTCSS tone freq dropped likewise — same ``sql_type`` cap gate (MOR-458).
    radio.read_ctcss_tone_index.assert_not_awaited()
    radio.get_af_level.assert_not_awaited()
    radio.get_rf_gain.assert_not_awaited()
    radio.get_squelch.assert_not_awaited()


@pytest.mark.asyncio
async def test_slow_poll_derives_nb_nr_toggles_off_from_zero_level() -> None:
    """MOR-444: the nb/nr toggles are derived from the level (``level > 0``).

    A zero level (OFF) → toggle ``False``, mirroring the legacy poller's
    ``state.main.nb = nb_level > 0`` derivation. A single CAT read per family
    feeds both the level and the derived toggle — no second query.
    """
    radio = _make_radio()
    radio.read_nb_level = AsyncMock(return_value=0)
    radio.read_nr_level = AsyncMock(return_value=0)
    adapter = YaesuObservationAdapter(
        radio,
        profile=_profile_state_acquisition(),
        clock=_clock,
    )

    observations = await adapter.poll_slow_controls()
    by_path = {str(item.path): item.value for item in observations}

    assert by_path["receiver.main.operator_controls.nb_level"] == 0
    assert by_path["receiver.main.operator_toggles.nb"] is False
    assert by_path["receiver.main.operator_controls.nr_level"] == 0
    assert by_path["receiver.main.operator_toggles.nr"] is False
    # Single read per family — the toggle reuses the level read.
    assert radio.read_nb_level.await_count == 1
    assert radio.read_nr_level.await_count == 1


@pytest.mark.asyncio
async def test_slow_poll_skips_dsp_without_matching_runtime_capability() -> None:
    """MOR-444: NB/NR gate on ``nb``/``nr``; notch fields gate on ``notch``."""
    radio = _make_radio()
    # Keep nb only; drop nr and notch to prove per-family gating.
    radio.capabilities = {"meters", "nb"}
    adapter = YaesuObservationAdapter(
        radio,
        profile=_profile_state_acquisition(),
        clock=_clock,
    )

    observations = await adapter.poll_slow_controls()
    paths = [str(item.path) for item in observations]

    assert "receiver.main.operator_controls.nb_level" in paths
    assert "receiver.main.operator_toggles.nb" in paths
    assert "receiver.main.operator_controls.nr_level" not in paths
    assert "receiver.main.operator_toggles.nr" not in paths
    assert "receiver.main.operator_toggles.auto_notch" not in paths
    assert "receiver.main.operator_toggles.manual_notch" not in paths
    assert "receiver.main.operator_controls.manual_notch_freq" not in paths
    assert radio.read_nb_level.await_count == 1
    radio.read_nr_level.assert_not_awaited()
    radio.read_auto_notch.assert_not_awaited()
    radio.read_manual_notch.assert_not_awaited()
    radio.read_manual_notch_freq.assert_not_awaited()


@pytest.mark.asyncio
async def test_slow_poll_coerces_attenuator_bool_to_registry_int() -> None:
    """The FTX-1 attenuator getter returns a bool; the int FieldPath gets 0/1."""
    radio = _make_radio()
    radio.read_attenuator = AsyncMock(return_value=False)
    adapter = YaesuObservationAdapter(
        radio,
        profile=_profile_state_acquisition(),
        clock=_clock,
    )

    observations = await adapter.poll_slow_controls()

    att = next(
        item
        for item in observations
        if str(item.path) == "receiver.main.operator_controls.att"
    )
    assert att.value == 0
    assert isinstance(att.value, int)
    assert not isinstance(att.value, bool)


@pytest.mark.asyncio
async def test_tx_meters_poll_emits_alc_power_swr_stream_like_meters() -> None:
    """ALC joins power/swr as a stream-like TX meter (MOR-448).

    ALC is read via the non-mutating ``read_alc_meter`` and emitted with the
    same short ``max_age`` (the meter freshness TTL) as power/swr — NOT the
    indefinite slow-control treatment.
    """
    radio = _make_radio()
    adapter = YaesuObservationAdapter(
        radio,
        profile=_profile_state_acquisition(),
        clock=_clock,
    )

    observations = await adapter.poll_tx_meters()

    assert [(str(item.path), item.value) for item in observations] == [
        ("global.meters.alc", 42),
        ("global.meters.power", 180),
        ("global.meters.swr", 120),
    ]
    assert all(item.source.source == "yaesu_poll_response" for item in observations)
    # Stream-like meters expire on a short TTL — same freshness TTL as power/swr.
    assert all(item.max_age == 0.8 for item in observations)
    radio.read_alc_meter.assert_awaited_once()
    radio.read_power_meter.assert_awaited_once()
    radio.read_swr_meter.assert_awaited_once()
    radio.get_alc_meter.assert_not_awaited()
    radio.get_power_meter.assert_not_awaited()
    radio.get_swr_meter.assert_not_awaited()


@pytest.mark.asyncio
async def test_tx_meters_poll_skips_alc_without_meters_capability() -> None:
    radio = _make_radio()
    radio.capabilities = {"dual_rx", "tx", "vox", "compressor"}
    adapter = YaesuObservationAdapter(
        radio,
        profile=_profile_state_acquisition(),
        clock=_clock,
    )

    observations = await adapter.poll_tx_meters()

    assert observations == ()
    radio.read_alc_meter.assert_not_awaited()
    radio.read_power_meter.assert_not_awaited()
    radio.read_swr_meter.assert_not_awaited()


@pytest.mark.asyncio
async def test_tx_controls_poll_emits_global_setpoints() -> None:
    radio = _make_radio()
    adapter = YaesuObservationAdapter(
        radio,
        profile=_profile_state_acquisition(),
        clock=_clock,
    )

    observations = await adapter.poll_tx_controls()

    assert [(str(item.path), item.value) for item in observations] == [
        ("global.operator_controls.power_level", 55),
        ("global.operator_controls.mic_gain", 40),
        ("global.tx_state.compressor_on", True),
        ("global.operator_controls.compressor_level", 25),
        ("global.tx_state.vox_on", True),
        # split (MOR-446) is a global tx_state bool, gated on the ``split``
        # capability — mirroring the legacy poller's ``"split" in caps`` gate.
        ("global.tx_state.split", True),
        # Clarifier RIT/XIT (MOR-454): global tx_state bools + global
        # operator-control signed Hz offset, gated on the ``rit`` capability —
        # mirroring the legacy poller's ``"rit" in caps`` gate. A single
        # ``read_clarifier`` read feeds both flags; ``read_clarifier_freq``
        # feeds the signed offset on the device scale.
        ("global.tx_state.rit_on", True),
        ("global.tx_state.rit_tx", False),
        ("global.operator_controls.rit_freq", -250),
        # Tuner + dial-lock (MOR-455): tuner_status is a global operator-control
        # int (raw device scale, 0-3) gated on the ``tuner`` capability;
        # dial_lock is a global tx_state bool gated on the ``dial_lock``
        # capability — mirroring the legacy poller's ``"tuner" in caps`` /
        # ``"dial_lock" in caps`` gates.
        ("global.operator_controls.tuner_status", 2),
        ("global.tx_state.dial_lock", True),
        # CW keyer family (MOR-456): key_speed/cw_pitch/break_in/break_in_delay
        # are global operator-control ints gated on the legacy poller's single
        # ``"cw" in caps`` gate. cw_pitch is Hz; break_in is the device int
        # (1=SEMI) from the BreakInMode IntEnum. (cw_spot is a slow_state bool
        # emitted on the slow-control lane, asserted separately below.)
        ("global.operator_controls.key_speed", 24),
        ("global.operator_controls.cw_pitch", 600),
        ("global.operator_controls.break_in", 1),
        ("global.operator_controls.break_in_delay", 300),
    ]
    assert all(item.source.source == "yaesu_poll_response" for item in observations)
    assert all(item.max_age == 120.0 for item in observations)
    # Power emits the watt SETPOINT (read_power), never the RM5 meter.
    radio.read_power.assert_awaited_once()
    radio.read_mic_gain.assert_awaited_once()
    radio.read_processor.assert_awaited_once()
    radio.read_processor_level.assert_awaited_once()
    radio.read_vox.assert_awaited_once()
    radio.read_split.assert_awaited_once()
    # Single CAT read pair for the clarifier — flags share one read.
    radio.read_clarifier.assert_awaited_once()
    radio.read_clarifier_freq.assert_awaited_once()
    # Tuner + dial-lock each use a single read (MOR-455).
    radio.read_tuner.assert_awaited_once()
    radio.read_lock.assert_awaited_once()
    # CW keyer family each use a single read (MOR-456).
    radio.read_keyer_speed.assert_awaited_once()
    radio.read_cw_pitch.assert_awaited_once()
    radio.read_break_in.assert_awaited_once()
    radio.read_break_in_delay.assert_awaited_once()
    radio.get_power.assert_not_awaited()
    radio.get_mic_gain.assert_not_awaited()
    radio.get_processor.assert_not_awaited()
    radio.get_processor_level.assert_not_awaited()
    radio.get_vox.assert_not_awaited()
    radio.get_split.assert_not_awaited()
    radio.get_clarifier.assert_not_awaited()
    radio.get_clarifier_freq.assert_not_awaited()
    radio.get_tuner.assert_not_awaited()
    radio.get_lock.assert_not_awaited()
    radio.get_keyer_speed.assert_not_awaited()
    radio.get_cw_pitch.assert_not_awaited()
    radio.get_break_in.assert_not_awaited()
    radio.get_break_in_delay.assert_not_awaited()


@pytest.mark.asyncio
async def test_tx_controls_poll_skips_fields_without_matching_runtime_capability() -> (
    None
):
    radio = _make_radio()
    # Drop tx/vox/compressor; mic_gain is unconditional, so it remains.
    radio.capabilities = {"dual_rx", "af_level", "rf_gain", "squelch", "meters"}
    adapter = YaesuObservationAdapter(
        radio,
        profile=_profile_state_acquisition(),
        clock=_clock,
    )

    observations = await adapter.poll_tx_controls()

    assert [(str(item.path), item.value) for item in observations] == [
        ("global.operator_controls.mic_gain", 40),
    ]
    radio.read_power.assert_not_awaited()
    radio.read_processor.assert_not_awaited()
    radio.read_processor_level.assert_not_awaited()
    radio.read_vox.assert_not_awaited()
    # split is dropped: the ``split`` runtime cap is absent here.
    radio.read_split.assert_not_awaited()
    # RIT/XIT is dropped: the ``rit`` runtime cap is absent here (MOR-454).
    radio.read_clarifier.assert_not_awaited()
    radio.read_clarifier_freq.assert_not_awaited()
    # Tuner + dial-lock are dropped: ``tuner``/``dial_lock`` caps absent (MOR-455).
    radio.read_tuner.assert_not_awaited()
    radio.read_lock.assert_not_awaited()
    # CW keyer family dropped: the ``cw`` runtime cap is absent here (MOR-456).
    radio.read_keyer_speed.assert_not_awaited()
    radio.read_cw_pitch.assert_not_awaited()
    radio.read_break_in.assert_not_awaited()
    radio.read_break_in_delay.assert_not_awaited()


@pytest.mark.asyncio
async def test_adapter_uses_read_only_yaesu_paths_when_getters_mutate_state() -> None:
    radio = _SideEffectingYaesuRadio()
    adapter = YaesuObservationAdapter(
        radio,
        profile=_profile_state_acquisition(),
        clock=_clock,
    )

    observations = (
        await adapter.poll_medium()
        + await adapter.poll_rx_meters()
        + await adapter.poll_tx_meters()
        + await adapter.poll_slow_controls()
        + await adapter.poll_tx_controls()
    )

    assert [(str(item.path), item.value) for item in observations] == [
        ("receiver.main.active.freq_mode.freq_hz", 14_074_000),
        ("receiver.main.active.freq_mode.mode", "USB"),
        ("receiver.sub.active.freq_mode.freq_hz", 7_074_000),
        ("receiver.sub.active.freq_mode.mode", "LSB"),
        ("global.tx_state.ptt", True),
        ("receiver.main.meters.s_meter", 150),
        ("receiver.sub.meters.s_meter", 75),
        ("global.meters.alc", 200),
        ("global.meters.power", 180),
        ("global.meters.swr", 120),
        ("receiver.main.operator_controls.af_level", 128),
        ("receiver.main.operator_controls.rf_gain", 180),
        ("receiver.main.operator_controls.squelch", 12),
        ("receiver.sub.operator_controls.af_level", 64),
        ("receiver.sub.operator_controls.rf_gain", 90),
        ("receiver.sub.operator_controls.squelch", 8),
        # AGC has no FTX-1 capability tag → unconditional, MAIN-only; ATT and
        # preamp are skipped because this radio lacks those runtime caps.
        ("receiver.main.operator_controls.agc", 3),
        # narrow is unconditional (like AGC) → emits; filter_width and if_shift
        # are skipped because this radio lacks the ``filter_width`` /
        # ``if_shift`` runtime caps.
        ("receiver.main.operator_toggles.narrow", True),
        # Tone / CTCSS squelch-type (MOR-457): gated on the ``sql_type`` cap
        # (present here); a single ``read_sql_type`` (code 1 = "TONE") derives
        # both booleans and does not mutate legacy state.
        ("receiver.main.operator_toggles.repeater_tone", True),
        ("receiver.main.operator_toggles.repeater_tsql", False),
        # CTCSS tone freq (MOR-458): gated on the ``sql_type`` cap (present
        # here); a single ``read_ctcss_tone_index`` (index 8 = 88.5 Hz) maps to
        # 8850 centiHz, emitted to BOTH tone_freq and tsql_freq, and does not
        # mutate legacy state.
        ("receiver.main.operator_controls.tone_freq", 8850),
        ("receiver.main.operator_controls.tsql_freq", 8850),
        # active-slot (MOR-446); unconditional like AGC/narrow, the SUB index
        # coerces to the neutral "SUB" str.
        ("global.slow_state.active", "SUB"),
        # cw_spot (MOR-456) — global slow_state bool, gated on the ``cw`` cap
        # (present here); ``read_cw_spot`` does not mutate legacy state.
        ("global.slow_state.cw_spot", True),
        ("global.operator_controls.power_level", 55),
        ("global.operator_controls.mic_gain", 40),
        ("global.tx_state.compressor_on", True),
        ("global.operator_controls.compressor_level", 25),
        ("global.tx_state.vox_on", True),
        # split is skipped: this radio lacks the ``split`` runtime cap.
        # Clarifier RIT/XIT (MOR-454): gated on the ``rit`` cap (present here);
        # a single ``read_clarifier`` feeds both flags, ``read_clarifier_freq``
        # the signed Hz offset — none of which mutate legacy state.
        ("global.tx_state.rit_on", True),
        ("global.tx_state.rit_tx", False),
        ("global.operator_controls.rit_freq", -250),
        # Tuner + dial-lock (MOR-455): gated on the ``tuner``/``dial_lock`` caps
        # (present here); a single ``read_tuner``/``read_lock`` each — neither
        # mutates legacy state. tuner_status is the raw device int (0-3).
        ("global.operator_controls.tuner_status", 2),
        ("global.tx_state.dial_lock", True),
        # CW keyer family (MOR-456): gated on the ``cw`` cap (present here); a
        # single ``read_keyer_speed``/``read_cw_pitch``/``read_break_in``/
        # ``read_break_in_delay`` each — none mutates legacy state. cw_pitch is
        # Hz; break_in is the device int (1=SEMI). (cw_spot rides the
        # slow-control lane, asserted above.)
        ("global.operator_controls.key_speed", 24),
        ("global.operator_controls.cw_pitch", 600),
        ("global.operator_controls.break_in", 1),
        ("global.operator_controls.break_in_delay", 300),
    ]
    assert radio.radio_state.main.freq == 1
    assert radio.radio_state.main.mode == "INIT-MAIN"
    assert radio.radio_state.sub.freq == 2
    assert radio.radio_state.sub.mode == "INIT-SUB"
    assert radio.radio_state.ptt is False
    assert radio.radio_state.main.s_meter == 3
    assert radio.radio_state.sub.s_meter == 4
    assert radio.radio_state.alc_meter == 5
    assert radio.radio_state.power_meter == 5
    assert radio.radio_state.swr_meter == 6
    assert radio.radio_state.main.af_level == 7
    assert radio.radio_state.main.rf_gain == 8
    assert radio.radio_state.main.squelch == 9
    assert radio.radio_state.sub.af_level == 10
    assert radio.radio_state.sub.rf_gain == 11
    assert radio.radio_state.sub.squelch == 12
    # The new read_* TX-control paths must not mutate legacy state either.
    assert radio.radio_state.power_level == 13
    assert radio.radio_state.mic_gain == 14
    assert radio.radio_state.compressor_on is False
    assert radio.radio_state.compressor_level == 15
    assert radio.radio_state.vox_on is False
    # RF front-end + AGC read_* paths must not mutate legacy state (MOR-443).
    assert radio.radio_state.main.att == 16
    assert radio.radio_state.main.preamp == 17
    assert radio.radio_state.main.agc == 18
    # Filter / IF-shift / narrow read_* paths must not mutate legacy state
    # either — even ``read_filter_width``, which READS the mode mirror for its
    # width-table decode but never writes ``self._state`` (MOR-445).
    assert radio.radio_state.main.filter_width == 19
    assert radio.radio_state.main.if_shift == 20
    assert radio.radio_state.main.narrow is False
    # Split + active-slot read_* paths must not mutate legacy state (MOR-446).
    assert radio.radio_state.split is False
    assert radio.radio_state.active == "MAIN"
    assert radio.radio_state.vfo_select == 0
    # Clarifier RIT/XIT read_* paths must not mutate legacy state (MOR-454).
    assert radio.radio_state.rit_on is False
    assert radio.radio_state.rit_tx is False
    assert radio.radio_state.rit_freq == 21
    # Tuner + dial-lock read_* paths must not mutate legacy state (MOR-455).
    assert radio.radio_state.tuner_status == 0
    assert radio.radio_state.dial_lock is False
    # CW keyer family read_* paths must not mutate legacy state (MOR-456).
    assert radio.radio_state.key_speed == 99
    assert radio.radio_state.cw_pitch == 999
    assert radio.radio_state.break_in == 2
    assert radio.radio_state.break_in_delay == 888
    assert radio.radio_state.cw_spot is False
    # Tone/CTCSS read_sql_type must not mutate legacy state (MOR-457). The
    # pre-seeded impossible-via-CT combination (both True) is preserved.
    assert radio.radio_state.main.repeater_tone is True
    assert radio.radio_state.main.repeater_tsql is True
    # CTCSS tone freq read_ctcss_tone_index must not mutate legacy state
    # (MOR-458). The pre-seeded sentinel centiHz values are preserved.
    assert radio.radio_state.main.tone_freq == 11111
    assert radio.radio_state.main.tsql_freq == 22222


@pytest.mark.asyncio
async def test_read_split_and_vfo_select_are_pure_reads() -> None:
    """MOR-446: ``read_split`` / ``read_vfo_select`` are pure CAT reads.

    Both delegate to the underlying ``get_*`` query path but must NOT write
    ``self._state`` — the public ``get_split`` / ``get_vfo_select`` getters keep
    the legacy mutation; only the ``read_*`` variants feed the observation
    pipeline (MOR-434 pattern).
    """
    radio = YaesuCatRadio("/dev/null", audio_driver=MagicMock())
    radio.radio_state.split = False
    radio.radio_state.active = "MAIN"

    radio._query = AsyncMock(return_value={"state": "1"})  # type: ignore[method-assign]
    assert await radio.read_split() is True
    assert radio.radio_state.split is False

    radio._query = AsyncMock(return_value={"vfo": "1"})  # type: ignore[method-assign]
    assert await radio.read_vfo_select() == 1
    assert radio.radio_state.active == "MAIN"


@pytest.mark.asyncio
async def test_read_clarifier_and_freq_are_pure_reads() -> None:
    """MOR-454: ``read_clarifier`` / ``read_clarifier_freq`` are pure CAT reads.

    Both delegate to the underlying ``get_*`` query/parse path but must NOT
    write ``self._state`` — only the ``read_*`` variants feed the observation
    pipeline (MOR-434 pattern). The signed Hz offset is returned on the device
    scale (cross-vendor calibration is MOR-453).
    """
    radio = YaesuCatRadio("/dev/null", audio_driver=MagicMock())
    radio.radio_state.rit_on = False
    radio.radio_state.rit_tx = False
    radio.radio_state.rit_freq = 99
    state_before = radio.radio_state

    radio._query = AsyncMock(  # type: ignore[method-assign]
        return_value={"rx": "1", "tx": "0"}
    )
    assert await radio.read_clarifier(0) == (True, False)
    assert radio.radio_state is state_before
    assert radio.radio_state.rit_on is False
    assert radio.radio_state.rit_tx is False

    radio._query = AsyncMock(  # type: ignore[method-assign]
        return_value={"sign": "-", "offset": 250}
    )
    assert await radio.read_clarifier_freq(0) == -250
    assert radio.radio_state.rit_freq == 99


@pytest.mark.asyncio
async def test_read_tuner_and_lock_are_pure_reads() -> None:
    """MOR-455: ``read_tuner`` / ``read_lock`` are pure CAT reads.

    Both delegate to the underlying ``get_*`` query/parse path but must NOT
    write ``self._state`` — only the ``read_*`` variants feed the observation
    pipeline (MOR-434 pattern). ``read_tuner`` returns the raw device int (0-3);
    ``read_lock`` returns the dial-lock bool.
    """
    radio = YaesuCatRadio("/dev/null", audio_driver=MagicMock())
    radio.radio_state.tuner_status = 0
    radio.radio_state.dial_lock = False
    state_before = radio.radio_state

    radio._query = AsyncMock(return_value={"state": "2"})  # type: ignore[method-assign]
    assert await radio.read_tuner() == 2
    assert radio.radio_state is state_before
    assert radio.radio_state.tuner_status == 0

    radio._query = AsyncMock(return_value={"state": "1"})  # type: ignore[method-assign]
    assert await radio.read_lock() is True
    assert radio.radio_state.dial_lock is False


@pytest.mark.asyncio
async def test_read_cw_keyer_family_are_pure_reads() -> None:
    """MOR-456: the CW keyer ``read_*`` helpers are pure CAT reads.

    Each delegates to the underlying ``get_*`` query/parse path but must NOT
    write ``self._state`` — only the ``read_*`` variants feed the observation
    pipeline (MOR-434 pattern). ``read_cw_pitch`` maps the FTX-1 idx to Hz
    (``300 + idx * 10``); ``read_break_in`` returns the BreakInMode IntEnum
    (emitted downstream as the device int). Raw device scale (cross-vendor
    calibration is MOR-453).
    """
    radio = YaesuCatRadio("/dev/null", audio_driver=MagicMock())
    radio.radio_state.key_speed = 99
    radio.radio_state.cw_pitch = 999
    radio.radio_state.break_in = 2
    radio.radio_state.break_in_delay = 888
    radio.radio_state.cw_spot = False
    state_before = radio.radio_state

    radio._query = AsyncMock(return_value={"wpm": "24"})  # type: ignore[method-assign]
    assert await radio.read_keyer_speed() == 24
    assert radio.radio_state is state_before
    assert radio.radio_state.key_speed == 99

    radio._query = AsyncMock(return_value={"idx": "30"})  # type: ignore[method-assign]
    assert await radio.read_cw_pitch() == 600  # 300 + 30 * 10
    assert radio.radio_state.cw_pitch == 999

    radio._query = AsyncMock(return_value={"state": "1"})  # type: ignore[method-assign]
    assert await radio.read_break_in() is BreakInMode.SEMI
    assert radio.radio_state.break_in == 2

    radio._query = AsyncMock(return_value={"delay": "300"})  # type: ignore[method-assign]
    assert await radio.read_break_in_delay() == 300
    assert radio.radio_state.break_in_delay == 888

    radio._query = AsyncMock(return_value={"state": "1"})  # type: ignore[method-assign]
    assert await radio.read_cw_spot() is True
    assert radio.radio_state.cw_spot is False


@pytest.mark.asyncio
async def test_read_sql_type_is_a_pure_read() -> None:
    """MOR-457: ``read_sql_type`` is a pure CAT read.

    It delegates to the same ``CT0`` query/parse path as ``get_sql_type`` but
    must NOT write ``self._state`` — only the ``read_*`` variant feeds the
    observation pipeline (MOR-434 pattern). It returns the raw FTX-1 ``CT`` P2
    "SQL TYPE" code (FTX-1_CAT_OM_ENG_2507); the neutral-boolean derivation
    happens in the adapter, never here.
    """
    radio = YaesuCatRadio("/dev/null", audio_driver=MagicMock())
    radio.radio_state.main.repeater_tone = True
    radio.radio_state.main.repeater_tsql = True
    state_before = radio.radio_state

    radio._query = AsyncMock(return_value={"type": "02"})  # type: ignore[method-assign]
    assert await radio.read_sql_type() == 2
    assert radio.radio_state is state_before
    # The impossible-via-CT pre-seeded combination is untouched.
    assert radio.radio_state.main.repeater_tone is True
    assert radio.radio_state.main.repeater_tsql is True

    # get_sql_type delegates to the same pure read.
    radio._query = AsyncMock(return_value={"type": "01"})  # type: ignore[method-assign]
    assert await radio.get_sql_type() == 1


@pytest.mark.parametrize(
    ("code", "expected_tone", "expected_tsql"),
    [
        # CAT ``CT`` P2 "SQL TYPE" codes (FTX-1_CAT_OM_ENG_2507) → neutral
        # mutually-exclusive CTCSS booleans (Hamlib/Icom convention):
        (0, False, False),  # CTCSS OFF
        (1, True, False),  # CTCSS ENC ON / DEC OFF ("TONE")
        (2, False, True),  # CTCSS ENC ON / DEC ON ("TSQL")
        (3, False, False),  # DCS — no neutral CTCSS-boolean representation
        (4, False, False),  # PR FREQ — no neutral CTCSS-boolean representation
        (5, False, False),  # REV TONE — no neutral CTCSS-boolean representation
    ],
)
@pytest.mark.asyncio
async def test_sql_type_code_maps_to_ctcss_booleans(
    code: int, expected_tone: bool, expected_tsql: bool
) -> None:
    """MOR-457: each ``CT`` P2 code derives the correct CTCSS boolean pair.

    Both ``repeater_tone`` and ``repeater_tsql`` are emitted every cycle (incl.
    the False derivations) so the store always reflects current state. Gated on
    the ``sql_type`` cap (present here) + can_poll.
    """
    radio = _make_radio()
    radio.read_sql_type = AsyncMock(return_value=code)
    adapter = YaesuObservationAdapter(
        radio,
        profile=_profile_state_acquisition(),
        clock=_clock,
    )

    observations = await adapter.poll_slow_controls()
    by_path = {str(item.path): item.value for item in observations}

    assert by_path["receiver.main.operator_toggles.repeater_tone"] is expected_tone
    assert by_path["receiver.main.operator_toggles.repeater_tsql"] is expected_tsql
    # A single read feeds both derived booleans.
    assert radio.read_sql_type.await_count == 1


@pytest.mark.asyncio
async def test_sql_type_skipped_without_ctcss_capability() -> None:
    """MOR-457: neither CTCSS path emits when the ``sql_type`` cap is absent."""
    radio = _make_radio()
    radio.capabilities = radio.capabilities - {"sql_type"}
    adapter = YaesuObservationAdapter(
        radio,
        profile=_profile_state_acquisition(),
        clock=_clock,
    )

    observations = await adapter.poll_slow_controls()
    paths = {str(item.path) for item in observations}

    assert "receiver.main.operator_toggles.repeater_tone" not in paths
    assert "receiver.main.operator_toggles.repeater_tsql" not in paths
    radio.read_sql_type.assert_not_awaited()


@pytest.mark.parametrize(
    ("index", "expected_centihz"),
    [
        (0, 6700),  # 67.0 Hz
        (8, 8850),  # 88.5 Hz (default CTCSS tone)
        (49, 25410),  # 254.1 Hz (highest standard EIA tone)
    ],
)
@pytest.mark.asyncio
async def test_ctcss_tone_freq_emits_both_paths_in_centihz(
    index: int, expected_centihz: int
) -> None:
    """MOR-458: one CN read feeds BOTH tone_freq and tsql_freq in centiHz.

    The FTX-1 has a single CTCSS tone frequency (CN P2=0) used for both encode
    (TONE) and decode (TSQL), so the same centiHz value is emitted to both
    neutral paths from a single ``read_ctcss_tone_index`` read. Gated on the
    ``sql_type`` cap (present here) + can_poll.
    """
    radio = _make_radio()
    radio.read_ctcss_tone_index = AsyncMock(return_value=index)
    adapter = YaesuObservationAdapter(
        radio,
        profile=_profile_state_acquisition(),
        clock=_clock,
    )

    observations = await adapter.poll_slow_controls()
    by_path = {str(item.path): item.value for item in observations}

    assert by_path["receiver.main.operator_controls.tone_freq"] == expected_centihz
    assert by_path["receiver.main.operator_controls.tsql_freq"] == expected_centihz
    # A SINGLE CN read feeds both emissions.
    assert radio.read_ctcss_tone_index.await_count == 1


@pytest.mark.asyncio
async def test_ctcss_tone_freq_skipped_without_ctcss_capability() -> None:
    """MOR-458: neither tone_freq nor tsql_freq emits without the ``sql_type`` cap."""
    radio = _make_radio()
    radio.capabilities = radio.capabilities - {"sql_type"}
    adapter = YaesuObservationAdapter(
        radio,
        profile=_profile_state_acquisition(),
        clock=_clock,
    )

    observations = await adapter.poll_slow_controls()
    paths = {str(item.path) for item in observations}

    assert "receiver.main.operator_controls.tone_freq" not in paths
    assert "receiver.main.operator_controls.tsql_freq" not in paths
    radio.read_ctcss_tone_index.assert_not_awaited()


@pytest.mark.asyncio
async def test_active_slot_coerces_main_index_to_neutral_str() -> None:
    """MOR-446: the int receiver index (0=MAIN) coerces to the neutral str.

    The neutral ``global.slow_state.active`` field is a ``"MAIN"``/``"SUB"`` str
    (consumed by the rigctld VFOA/VFOB mapping and the dual-RX runtime), so the
    adapter maps the ``VS`` index 0 → ``"MAIN"`` rather than emitting the raw int.
    """
    radio = _make_radio()
    radio.read_vfo_select = AsyncMock(return_value=0)
    adapter = YaesuObservationAdapter(
        radio,
        profile=_profile_state_acquisition(),
        clock=_clock,
    )

    observations = await adapter.poll_slow_controls()

    active = next(
        item
        for item in observations
        if str(item.path) == "global.slow_state.active"
    )
    assert active.value == "MAIN"
    assert isinstance(active.value, str)


@pytest.mark.asyncio
async def test_public_get_data_mode_returns_flat_value_without_state_synthesis() -> None:
    """MOR-434: a public ``get_*`` returns a flat value, not synthesized state.

    ``get_data_mode`` is the representative public read called out for the
    provider backends. It derives a flat ``bool`` from the existing mode and
    must not fabricate or hand out a synthesized ``RadioState`` as consumer
    state. The consumer pipeline is fed by :class:`YaesuObservationAdapter`
    (which uses the non-mutating ``read_*`` paths); the private ``self._state``
    mirror is legacy compat only.
    """
    # Real backend; only the USB audio driver is stubbed (not under test).
    radio = YaesuCatRadio("/dev/null", audio_driver=MagicMock())
    radio.radio_state.main.mode = "USB-D"
    state_before = radio.radio_state

    result = await radio.get_data_mode()

    # Flat derived bool, never a RadioState object.
    assert result is True
    assert isinstance(result, bool)
    # No synthesized RadioState handed back as consumer state.
    assert radio.radio_state is state_before
    # The read derives from the mirror without mutating it.
    assert radio.radio_state.main.mode == "USB-D"


@pytest.mark.asyncio
async def test_read_filter_width_reads_mode_without_mutating_state() -> None:
    """MOR-445: ``read_filter_width`` may READ the mode mirror for its
    width-table decode, but it must NOT WRITE ``self._state``.

    ``get_filter_width`` (the public, mutating path) and ``read_filter_width``
    return the same Hz value, but only ``read_filter_width`` is wired into the
    observation adapter so that legacy state is never mutated by polling.
    """
    radio = YaesuCatRadio("/dev/null", audio_driver=MagicMock())
    # SSB table → code 12 decodes to 2400 Hz (see rigs/ftx1.toml SSB table).
    radio.radio_state.main.mode = "USB"
    radio.radio_state.main.filter_width = 999
    radio._query = AsyncMock(return_value={"code": 12})  # type: ignore[method-assign]
    state_before = radio.radio_state

    value = await radio.read_filter_width(0)

    assert value == 2400
    assert isinstance(value, int)
    # Read of the mode mirror is fine; a write to legacy state is not.
    assert radio.radio_state is state_before
    assert radio.radio_state.main.mode == "USB"
    assert radio.radio_state.main.filter_width == 999


@pytest.mark.asyncio
async def test_read_if_shift_and_narrow_are_pure_reads() -> None:
    """MOR-445: ``read_if_shift`` / ``read_narrow`` are pure CAT reads."""
    radio = YaesuCatRadio("/dev/null", audio_driver=MagicMock())
    radio.radio_state.main.if_shift = 111
    radio.radio_state.main.narrow = False

    radio._query = AsyncMock(  # type: ignore[method-assign]
        return_value={"sign": "+", "offset": 200}
    )
    assert await radio.read_if_shift(0) == 200
    assert radio.radio_state.main.if_shift == 111

    radio._query = AsyncMock(return_value={"state": "1"})  # type: ignore[method-assign]
    assert await radio.read_narrow(0) is True
    assert radio.radio_state.main.narrow is False
