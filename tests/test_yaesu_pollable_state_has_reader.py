"""Every pollable Yaesu CAT state path must have its profile read command.

MOR-4434: FT-991A work found ``global.tx_state.tx_target`` declared pollable
while the profile had no ``get_tx_func``, so
``YaesuObservationAdapter._read_tx_func`` raised ``CommandError`` every cycle
and aborted the whole medium poll batch. The adapter never guards an
observation read with ``supports_command()`` — every read in its four poll
lanes (``poll_medium``, ``poll_rx_meters``, ``poll_tx_meters``,
``poll_slow_controls``, ``poll_tx_controls``) is gated only on the profile's
per-field ``can_poll`` policy plus runtime capability tags, and
``_safe_read`` (``observations.py``) catches malformed answers and refusals
but NOT the ``CommandError`` ``_query`` raises for a command the profile does
not declare (``radio.py`` ``_get_spec`` / ``_query`` / ``_query_decoded``).
So a profile that declares a path pollable without the command its read needs
turns every poll cycle of that lane into an exception.

This file walks every shipped Yaesu CAT profile (``rigs/*.toml`` whose
``state_acquisition`` provider is ``yaesu_cat``, loaded through the real
``discover_rigs`` loader) and fails on any pollable path whose required read
command is missing. The path -> command mapping below is derived from the
adapter's own read call sites (each group cites them) crossed with the
``_query`` command name each ``radio.py`` read method uses; the paths
themselves are the adapter's own module constants, not a re-typed copy.
Only unconditional reads are listed: every adapter observation read raises
instead of guarding with ``supports_command()`` (no such guard exists in
``observations.py``), so every lane read qualifies. Deliberately excluded:

- ``global.tx_state.observed_ptt``: derived from the same PTT read, emitted
  only when the PTT path itself is pollable — no extra command involved.
- ``available_when`` clause gates (``_available``): resolved against live
  store state and default-allow with no store attached; not statically
  knowable, and withholding only narrows the read set.
- The ``tx_target`` bracketing ``read_freq`` calls: covered by the freq rows
  themselves when those paths are pollable.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from rigplane.backends.yaesu_cat import observations as yaesu_obs
from rigplane.commands.command_spec import CatCommandSpec
from rigplane.core.state_pipeline_contracts import FieldPath
from rigplane.rig_loader import discover_rigs, load_rig

RIGS_DIR = Path(__file__).resolve().parent.parent / "rigs"
_FIXTURE_TOML = (
    Path(__file__).resolve().parent
    / "fixtures"
    / "rigs"
    / "yaesu_single_rx_fixture.toml"
)


@dataclass(frozen=True, slots=True)
class _RequiredRead:
    """One pollable path and the profile command its adapter read needs.

    ``caps_all`` holds runtime capability tags that must ALL be present for
    the adapter to issue the read; ``caps_any`` holds tags of which at least
    one must be present (the clarifier status/frequency read fires when
    either ``rit`` or ``xit`` is declared). ``requires_pollable`` holds
    sibling paths that must themselves be pollable for the read to fire
    (the radio-default flag twin-leaf reads only run inside their width
    path's own pollable block).
    """

    path: FieldPath
    command: str
    caps_all: frozenset[str] = field(default_factory=frozenset)
    caps_any: frozenset[str] = field(default_factory=frozenset)
    requires_pollable: tuple[FieldPath, ...] = ()


def _all(*names: str) -> frozenset[str]:
    return frozenset(names)


# poll_medium, freq/mode lane (observations.py:399-457): radio.read_freq /
# read_mode query get_freq(_sub) / get_mode(_sub) (radio.py:1581-1647).
# The tx_target lane (observations.py:458-465) reads get_tx_func through
# _read_tx_target, which returns Unknown without reading unless the radio
# reports the "tx" runtime capability (observations.py:708-711);
# get_tx_func itself is radio.py:2937-2940. PTT (observations.py:466+)
# reads get_ptt via read_transmit_state -> read_ptt_token (radio.py:2117+).
_MEDIUM_READS: tuple[_RequiredRead, ...] = (
    _RequiredRead(yaesu_obs._MAIN_FREQ, "get_freq"),
    _RequiredRead(yaesu_obs._MAIN_MODE, "get_mode"),
    _RequiredRead(yaesu_obs._SUB_FREQ, "get_freq_sub", _all("dual_rx")),
    _RequiredRead(yaesu_obs._SUB_MODE, "get_mode_sub", _all("dual_rx")),
    _RequiredRead(yaesu_obs._TX_TARGET, "get_tx_func", _all("tx")),
    _RequiredRead(yaesu_obs._PTT, "get_ptt"),
)

# poll_medium, filter-width lane (observations.py:552-671): read_filter_width
# and the radio-default twin-leaf read_filter_width_reading query
# get_filter_width(_sub) (radio.py:2711-2790); the narrow prime
# (observations.py:2155-2168) and the slow lane (observations.py:1156-1181)
# read get_narrow(_sub) with no capability gate on MAIN.
_WIDTH_READS: tuple[_RequiredRead, ...] = (
    _RequiredRead(
        yaesu_obs._MAIN_FILTER_WIDTH, "get_filter_width", _all("filter_width")
    ),
    _RequiredRead(
        yaesu_obs._MAIN_FILTER_WIDTH_RADIO_DEFAULT,
        "get_filter_width",
        _all("filter_width"),
        requires_pollable=(yaesu_obs._MAIN_FILTER_WIDTH,),
    ),
    _RequiredRead(
        yaesu_obs._SUB_FILTER_WIDTH,
        "get_filter_width_sub",
        _all("dual_rx", "filter_width"),
    ),
    _RequiredRead(
        yaesu_obs._SUB_FILTER_WIDTH_RADIO_DEFAULT,
        "get_filter_width_sub",
        _all("dual_rx", "filter_width"),
        requires_pollable=(yaesu_obs._SUB_FILTER_WIDTH,),
    ),
    _RequiredRead(yaesu_obs._MAIN_NARROW, "get_narrow"),
    _RequiredRead(yaesu_obs._SUB_NARROW, "get_narrow_sub", _all("dual_rx")),
)

# poll_rx_meters (observations.py:756-796): read_s_meter queries
# get_s_meter(_sub).
_METER_READS: tuple[_RequiredRead, ...] = (
    _RequiredRead(yaesu_obs._MAIN_S_METER, "get_s_meter", _all("meters")),
    _RequiredRead(yaesu_obs._SUB_S_METER, "get_s_meter_sub", _all("meters", "dual_rx")),
)

# poll_tx_meters (observations.py:862-932) and the supply-telemetry tail of
# poll_slow_controls (observations.py:1616-1649): every meter routes through
# _read_meter, which queries the single parameterised get_meter command
# (radio.py:2269-2281).
_TX_METER_READS: tuple[_RequiredRead, ...] = (
    _RequiredRead(yaesu_obs._ALC_METER, "get_meter", _all("meters")),
    _RequiredRead(yaesu_obs._POWER_METER, "get_meter", _all("meters")),
    _RequiredRead(yaesu_obs._SWR_METER, "get_meter", _all("meters")),
    _RequiredRead(yaesu_obs._COMP_METER, "get_meter", _all("meters")),
    _RequiredRead(yaesu_obs._VD_METER, "get_meter", _all("meters")),
    _RequiredRead(yaesu_obs._ID_METER, "get_meter", _all("meters")),
)

# poll_slow_controls (observations.py:934-1650). Call sites and their radio
# read -> _query command: dual_watch -> get_rx_func (L937; radio.py:2928);
# af/rf/sql -> get_af_level(_sub)/get_rf_gain(_sub)/get_squelch(_sub)
# (L948-1052); att/preamp -> get_attenuator/get_preamp (L1064-1091);
# agc -> get_agc(_sub) with no capability gate on MAIN (L1092-1117);
# if_shift -> get_if_shift(_sub) (L1127-1155); nb/nr levels gate on their
# capability with the read issued on the capability alone (L1190/L1243), the
# nb/nr toggles deriving from that same read; notch ->
# get_auto_notch(_sub)/get_manual_notch(_sub)/get_manual_notch_freq(_sub)
# (L1296-1396); the CT squelch-type read get_sql_type serves the two toggle
# booleans plus the tone_squelch_type path per receiver (L1418-1480, single
# receiver-parameterised command); the CN tone-index read get_ctcss_tone
# serves tone_freq and tsql_freq per receiver (L1499-1542, single command);
# repeater shift -> get_repeater_shift, one receiver-parameterised command
# for both receivers (L1546-1569); active slot -> get_vfo_select with no
# capability gate (L1575-1589); cw spot -> get_cw_spot (L1594-1608).
_SLOW_READS: tuple[_RequiredRead, ...] = (
    _RequiredRead(yaesu_obs._DUAL_WATCH, "get_rx_func"),
    _RequiredRead(yaesu_obs._MAIN_AF, "get_af_level", _all("af_level")),
    _RequiredRead(yaesu_obs._MAIN_RF, "get_rf_gain", _all("rf_gain")),
    _RequiredRead(yaesu_obs._MAIN_SQL, "get_squelch", _all("squelch")),
    _RequiredRead(yaesu_obs._SUB_AF, "get_af_level_sub", _all("dual_rx", "af_level")),
    _RequiredRead(yaesu_obs._SUB_RF, "get_rf_gain_sub", _all("dual_rx", "rf_gain")),
    _RequiredRead(yaesu_obs._SUB_SQL, "get_squelch_sub", _all("dual_rx", "squelch")),
    _RequiredRead(yaesu_obs._MAIN_ATT, "get_attenuator", _all("attenuator")),
    _RequiredRead(yaesu_obs._MAIN_PREAMP, "get_preamp", _all("preamp")),
    _RequiredRead(yaesu_obs._MAIN_AGC, "get_agc"),
    _RequiredRead(yaesu_obs._SUB_AGC, "get_agc_sub", _all("dual_rx")),
    _RequiredRead(yaesu_obs._MAIN_IF_SHIFT, "get_if_shift", _all("if_shift")),
    _RequiredRead(
        yaesu_obs._SUB_IF_SHIFT, "get_if_shift_sub", _all("dual_rx", "if_shift")
    ),
    _RequiredRead(yaesu_obs._MAIN_NB_LEVEL, "get_nb_level", _all("nb")),
    _RequiredRead(yaesu_obs._MAIN_NB, "get_nb_level", _all("nb")),
    _RequiredRead(yaesu_obs._SUB_NB_LEVEL, "get_nb_level_sub", _all("dual_rx", "nb")),
    _RequiredRead(yaesu_obs._SUB_NB, "get_nb_level_sub", _all("dual_rx", "nb")),
    _RequiredRead(yaesu_obs._MAIN_NR_LEVEL, "get_nr_level", _all("nr")),
    _RequiredRead(yaesu_obs._MAIN_NR, "get_nr_level", _all("nr")),
    _RequiredRead(yaesu_obs._SUB_NR_LEVEL, "get_nr_level_sub", _all("dual_rx", "nr")),
    _RequiredRead(yaesu_obs._SUB_NR, "get_nr_level_sub", _all("dual_rx", "nr")),
    _RequiredRead(yaesu_obs._MAIN_AUTO_NOTCH, "get_auto_notch", _all("notch")),
    _RequiredRead(
        yaesu_obs._SUB_AUTO_NOTCH, "get_auto_notch_sub", _all("dual_rx", "notch")
    ),
    _RequiredRead(yaesu_obs._MAIN_MANUAL_NOTCH, "get_manual_notch", _all("notch")),
    _RequiredRead(
        yaesu_obs._SUB_MANUAL_NOTCH, "get_manual_notch_sub", _all("dual_rx", "notch")
    ),
    _RequiredRead(
        yaesu_obs._MAIN_MANUAL_NOTCH_FREQ, "get_manual_notch_freq", _all("notch")
    ),
    _RequiredRead(
        yaesu_obs._SUB_MANUAL_NOTCH_FREQ,
        "get_manual_notch_freq_sub",
        _all("dual_rx", "notch"),
    ),
    _RequiredRead(yaesu_obs._MAIN_REPEATER_TONE, "get_sql_type", _all("sql_type")),
    _RequiredRead(yaesu_obs._MAIN_REPEATER_TSQL, "get_sql_type", _all("sql_type")),
    _RequiredRead(yaesu_obs._MAIN_TONE_SQUELCH_TYPE, "get_sql_type", _all("sql_type")),
    _RequiredRead(
        yaesu_obs._SUB_REPEATER_TONE, "get_sql_type", _all("dual_rx", "sql_type")
    ),
    _RequiredRead(
        yaesu_obs._SUB_REPEATER_TSQL, "get_sql_type", _all("dual_rx", "sql_type")
    ),
    _RequiredRead(
        yaesu_obs._SUB_TONE_SQUELCH_TYPE, "get_sql_type", _all("dual_rx", "sql_type")
    ),
    _RequiredRead(yaesu_obs._MAIN_TONE_FREQ, "get_ctcss_tone", _all("sql_type")),
    _RequiredRead(yaesu_obs._MAIN_TSQL_FREQ, "get_ctcss_tone", _all("sql_type")),
    _RequiredRead(
        yaesu_obs._SUB_TONE_FREQ, "get_ctcss_tone", _all("dual_rx", "sql_type")
    ),
    _RequiredRead(
        yaesu_obs._SUB_TSQL_FREQ, "get_ctcss_tone", _all("dual_rx", "sql_type")
    ),
    _RequiredRead(
        yaesu_obs._MAIN_REPEATER_SHIFT, "get_repeater_shift", _all("repeater_shift")
    ),
    _RequiredRead(
        yaesu_obs._SUB_REPEATER_SHIFT,
        "get_repeater_shift",
        _all("dual_rx", "repeater_shift"),
    ),
    _RequiredRead(yaesu_obs._ACTIVE, "get_vfo_select"),
    _RequiredRead(yaesu_obs._CW_SPOT, "get_cw_spot", _all("cw")),
)

# poll_tx_controls (observations.py:1652-1904). Call sites and their radio
# read -> _query command: power_level -> read_power -> get_power (L1667);
# mic_gain -> get_mic_gain with no capability gate (L1684); compressor ->
# get_processor/get_processor_level (L1695-1724); vox -> get_vox (L1725);
# split -> get_split (L1739); the clarifier status read get_clarifier fires
# when either rit or xit is declared and serves rit_on/rit_tx (L1754-1780),
# the shared clarifier frequency read get_clarifier_freq likewise (L1781);
# tuner -> get_tuner_status -> _read_atu_route -> get_tuner (L1799,
# radio.py:4105-4131); dial_lock -> get_lock (L1817); cw keyer ->
# get_keyer_speed/get_key_pitch (via read_key_pitch)/get_break_in/
# get_break_in_delay (L1843-1903, radio.py:3326-3434).
_TX_CONTROL_READS: tuple[_RequiredRead, ...] = (
    _RequiredRead(yaesu_obs._POWER_LEVEL, "get_power", _all("tx")),
    _RequiredRead(yaesu_obs._MIC_GAIN, "get_mic_gain"),
    _RequiredRead(yaesu_obs._COMPRESSOR_ON, "get_processor", _all("compressor")),
    _RequiredRead(
        yaesu_obs._COMPRESSOR_LEVEL, "get_processor_level", _all("compressor")
    ),
    _RequiredRead(yaesu_obs._VOX_ON, "get_vox", _all("vox")),
    _RequiredRead(yaesu_obs._SPLIT, "get_split", _all("split")),
    _RequiredRead(yaesu_obs._RIT_ON, "get_clarifier", frozenset(), _all("rit", "xit")),
    _RequiredRead(yaesu_obs._RIT_TX, "get_clarifier", frozenset(), _all("rit", "xit")),
    _RequiredRead(
        yaesu_obs._RIT_FREQ, "get_clarifier_freq", frozenset(), _all("rit", "xit")
    ),
    _RequiredRead(yaesu_obs._TUNER, "get_tuner", _all("tuner")),
    _RequiredRead(yaesu_obs._DIAL_LOCK, "get_lock", _all("dial_lock")),
    _RequiredRead(yaesu_obs._KEY_SPEED, "get_keyer_speed", _all("cw")),
    _RequiredRead(yaesu_obs._CW_PITCH, "get_key_pitch", _all("cw")),
    _RequiredRead(yaesu_obs._BREAK_IN, "get_break_in", _all("cw")),
    _RequiredRead(yaesu_obs._BREAK_IN_DELAY, "get_break_in_delay", _all("cw")),
)

_YAESU_POLLABLE_READS: tuple[_RequiredRead, ...] = (
    *_MEDIUM_READS,
    *_WIDTH_READS,
    *_METER_READS,
    *_TX_METER_READS,
    *_SLOW_READS,
    *_TX_CONTROL_READS,
)
assert _YAESU_POLLABLE_READS, "the adapter read table must not be empty"


def _yaesu_pollable_read_gaps(rig: object) -> list[str]:
    """Pollable paths of a Yaesu profile whose read command is missing.

    A gap means the adapter would issue a CAT query the profile cannot
    encode: ``_query`` raises ``CommandError`` for a command with no
    ``CatCommandSpec`` (``radio.py`` ``_get_spec``), no read template
    (``radio.py`` ``_query``) or no parse template (``radio.py``
    ``_query_decoded``), and ``_safe_read`` does not catch it — so the
    whole poll lane aborts. Non-Yaesu profiles and profiles without
    acquisition metadata yield no gaps.
    """
    to_profile = getattr(rig, "to_profile", None)
    if not callable(to_profile):
        return []
    profile = to_profile()
    acquisition = profile.state_acquisition
    if acquisition is None or acquisition.provider != "yaesu_cat":
        return []
    pollable = set(acquisition.pollable_paths())
    capabilities = set(profile.capabilities)
    commands = getattr(rig, "commands", {})
    gaps: list[str] = []
    for required in _YAESU_POLLABLE_READS:
        if required.path not in pollable:
            continue
        if any(sibling not in pollable for sibling in required.requires_pollable):
            continue
        if not required.caps_all <= capabilities:
            continue
        if required.caps_any and required.caps_any.isdisjoint(capabilities):
            continue
        spec = commands.get(required.command)
        if isinstance(spec, CatCommandSpec) and spec.read and spec.parse:
            continue
        gaps.append(
            f"{required.path} is pollable but the profile has no readable "
            f"[{required.command}] command for the adapter's read"
        )
    return gaps


def test_every_shipped_yaesu_profile_declares_readers_for_pollable_state() -> None:
    """Census: no shipped yaesu_cat profile declares a pollable path whose
    adapter read has no profile command. A gap here is a per-cycle
    ``CommandError`` aborting that poll lane, so there are no allow-lists:
    a flagged profile means a new ticket, not a waiver."""
    checked: list[str] = []
    failures: list[str] = []
    for model, rig in sorted(discover_rigs(RIGS_DIR).items()):
        acquisition = rig.to_profile().state_acquisition
        if acquisition is None or acquisition.provider != "yaesu_cat":
            continue
        checked.append(model)
        failures.extend(f"{model}: {gap}" for gap in _yaesu_pollable_read_gaps(rig))
    assert checked, "no yaesu_cat profile found under rigs/"
    assert not failures, (
        "shipped Yaesu profiles declare pollable state with no adapter read command:\n"
        + "\n".join(failures)
    )


def _write_tx_target_probe_profile(tmp_path: Path, *, with_tx_func: bool) -> Path:
    """Copy of the single-RX fixture declaring tx_target pollable.

    The fixture is a transceiver CAT surface (FT-991A class) but lists only
    ``split``/``vfo_swap`` features, so the probe adds the ``tx`` feature:
    without it ``_read_tx_target`` returns Unknown before reading
    (observations.py:710-711) and the probe would not exercise the lane.
    With ``with_tx_func`` the probe also declares the ``get_tx_func`` read
    the lane needs; without it the profile reproduces the FT-991A defect.
    """
    text = _FIXTURE_TOML.read_text()
    text = text.replace(
        'features = ["split", "vfo_swap"]',
        'features = ["split", "vfo_swap", "tx"]',
    )
    if with_tx_func:
        text += 'get_tx_func = { cat = { read = "FT;", parse = "FT{vfo};" } }\n'
    text += """
[state_acquisition]
provider = "yaesu_cat"

[state_acquisition.capabilities]
polling_only = ["global.tx_state.tx_target"]
"""
    path = tmp_path / "yaesu_tx_target_probe.toml"
    path.write_text(text)
    return path


def test_pollable_tx_target_without_get_tx_func_is_reported(tmp_path: Path) -> None:
    """Failing-first: the FT-991A-class defect the walker hunts.

    ``global.tx_state.tx_target`` pollable with no ``get_tx_func`` command
    must be reported (only it — the probe declares nothing else pollable).
    """
    rig = load_rig(_write_tx_target_probe_profile(tmp_path, with_tx_func=False))
    gaps = _yaesu_pollable_read_gaps(rig)
    assert len(gaps) == 1, f"expected exactly the tx_target gap, got: {gaps}"
    assert "global.tx_state.tx_target" in gaps[0]
    assert "get_tx_func" in gaps[0]


def test_pollable_tx_target_with_get_tx_func_is_clean(tmp_path: Path) -> None:
    """Precision pin: declaring the read command clears the same profile."""
    rig = load_rig(_write_tx_target_probe_profile(tmp_path, with_tx_func=True))
    assert _yaesu_pollable_read_gaps(rig) == []
