"""Probe write checks: one read rule and one restore cover (MOR-4464 B1a r3).

A named read is only what the shipped read pass would send; every write
step needs a restore item for the very setting it changes (command, sub,
selector, receiver); a restore item reads and writes the same setting;
digital-voice mode values are refused. Every family runs on several radios.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from tests.probe.test_probe_checks_loader import (
    SELECTED,
    UNSELECTED,
    _civ,
    _fails,
    _load_with_probe,
    _raw,
    _restore,
)

CIV_RIGS = ("ic7300.toml", "ic705.toml", "ic7610.toml")
CAT_RIGS = ("ftx1.toml", "ft991a.toml")


def _write(command: str, **values: object) -> str:
    rows = "\n".join(
        f"{key} = {value!r}".replace("'", '"') for key, value in values.items()
    )
    return (
        f'[[probe.checks.steps]]\nkind = "write"\ncommand = "{command}"\n'
        f"[probe.checks.steps.values]\n{rows}"
    )


def _read(command: str) -> str:
    return f'[[probe.checks.steps]]\nkind = "read"\ncommand = "{command}"'


def _loads(tmp_path: Path, rig: str, toml: str):
    (check,) = _load_with_probe(tmp_path, rig, toml).probe_checks
    return check


# --- B1: raw_read is gone ----------------------------------------------------


@pytest.mark.parametrize(
    ("rig", "key", "body"),
    [
        *((rig, "body_hex", "1a050001") for rig in CIV_RIGS),
        *((rig, "body_hex", "0f01") for rig in CIV_RIGS),
        *((rig, "line", "AI1;") for rig in CAT_RIGS),
        *((rig, "line", "SV;") for rig in CAT_RIGS),
    ],
)
def test_raw_read_is_not_a_step_kind(
    tmp_path: Path, rig: str, key: str, body: str
) -> None:
    _fails(tmp_path, rig, _civ(_raw("raw_read", key, body), ""), "kind")


# --- B2: a named read is a read the read pass sends ---------------------------


@pytest.mark.parametrize(
    ("rig", "name"),
    [
        ("ic7300.toml", "set_split"),
        ("ic7300.toml", "set_speech"),
        ("ic7300.toml", "set_vfo"),
        ("ic705.toml", "get_vfo"),
        ("ic705.toml", "set_freq_offset"),
        ("ic7610.toml", "get_main_sub_band"),
        ("ic7610.toml", "set_dual_watch_on"),
        ("ftx1.toml", "set_freq"),
        ("ft991a.toml", "set_band"),
    ],
)
def test_named_read_must_be_a_read_the_read_pass_sends(
    tmp_path: Path, rig: str, name: str
) -> None:
    safe = _read("get_freq")
    restore = _restore(("get_freq", "set_freq"))
    _fails(tmp_path, rig, _civ(_read(name), restore), name)
    _fails(tmp_path, rig, _civ(safe, restore, f'witness = ["{name}"]'), name)
    pre = f'[[probe.checks.preconditions]]\nread = "{name}"\nequals = "x"\n'
    _fails(tmp_path, rig, _civ(safe, restore, pre), name)
    _fails(tmp_path, rig, _civ(safe, _restore((name, "set_freq"))), name)


# --- B3: digital-voice mode values ---------------------------------------------


@pytest.mark.parametrize("rig", CIV_RIGS)
@pytest.mark.parametrize(
    ("command", "data"),
    [("set_mode", "17"), ("set_mode", "2201"), ("set_unselected_mode", "170001")],
)
def test_civ_digital_voice_mode_value_is_refused(
    tmp_path: Path, rig: str, command: str, data: str
) -> None:
    toml = _civ(_write(command, data_hex=data), _restore(*SELECTED, *UNSELECTED))
    _fails(tmp_path, rig, toml, "digital voice")
    ok = _write(command, data_hex="01" + data[2:])
    assert _loads(tmp_path, rig, _civ(ok, _restore(*SELECTED, *UNSELECTED)))


@pytest.mark.parametrize(
    ("rig", "code"), [("ftx1.toml", "H"), ("ftx1.toml", "I"), ("ft991a.toml", "E")]
)
def test_cat_c4fm_mode_code_from_the_profile_is_refused(
    tmp_path: Path, rig: str, code: str
) -> None:
    restore = _restore(("get_freq", "set_freq"), ("get_mode", "set_mode"))
    _fails(tmp_path, rig, _civ(_write("set_mode", mode=code), restore), "digital voice")
    raw = _raw("raw_write", "line", f"MD0{code};")
    _fails(tmp_path, rig, _civ(raw, restore), "digital voice")


def test_cat_mode_codes_are_profile_data_not_one_table(tmp_path: Path) -> None:
    """FTX-1 code E is AM-N (allowed); on the FT-991A E is C4FM (refused)."""
    restore = _restore(("get_freq", "set_freq"), ("get_mode", "set_mode"))
    check = _loads(tmp_path, "ftx1.toml", _civ(_write("set_mode", mode="E"), restore))
    assert dict(check.steps[0].values) == {"mode": "E"}


# --- R1: every write step needs its own restore --------------------------------


@pytest.mark.parametrize("rig", CIV_RIGS)
def test_civ_named_write_needs_a_restore_of_its_own_setting(
    tmp_path: Path, rig: str
) -> None:
    step = _write("set_rf_power", data_hex="0128")
    _fails(tmp_path, rig, _civ(step, _restore(("get_freq", "set_freq"))), "restore")
    assert _loads(tmp_path, rig, _civ(step, _restore(("get_rf_power", "set_rf_power"))))
    sub = _write("set_unselected_freq", data_hex="0040070700")
    _fails(tmp_path, rig, _civ(sub, _restore(*SELECTED)), "restore")


@pytest.mark.parametrize(
    ("rig", "values"),
    [("ftx1.toml", {"head": "1", "watts": 5}), ("ft991a.toml", {"watts": 5})],
)
def test_cat_named_write_needs_a_restore_of_its_own_setting(
    tmp_path: Path, rig: str, values: dict[str, object]
) -> None:
    step = _write("set_power", **values)
    _fails(tmp_path, rig, _civ(step, _restore(("get_freq", "set_freq"))), "restore")
    assert _loads(tmp_path, rig, _civ(step, _restore(("get_power", "set_power"))))


# --- R2: band select and the CI-V 0x07 sub allow-list ---------------------------


MAIN_FM = (("get_freq", "set_freq"), ("get_mode", "set_mode"))
SUB_FM = (("get_freq_sub", "set_freq_sub"), ("get_mode_sub", "set_mode_sub"))


@pytest.mark.parametrize(
    ("rig", "command", "pairs", "wrong"),
    [
        ("ft991a.toml", "set_band", MAIN_FM, ()),
        ("ftx1.toml", "set_band", MAIN_FM, SUB_FM),
        ("ftx1.toml", "set_band_sub", SUB_FM, MAIN_FM),
    ],
)
def test_band_select_needs_frequency_and_mode_of_its_receiver(
    tmp_path: Path, rig: str, command: str, pairs: tuple, wrong: tuple
) -> None:
    step = _write(command, band=5)
    for partial in (pairs[:1], pairs[1:], wrong):
        if partial:
            _fails(tmp_path, rig, _civ(step, _restore(*partial)), "restore")
    assert _loads(tmp_path, rig, _civ(step, _restore(*pairs)))


@pytest.mark.parametrize("line", ["BS105;", "BS10;"])
def test_raw_band_select_takes_the_stricter_receiver(tmp_path: Path, line: str) -> None:
    """``BS1x`` may be the sub receiver: the main restore does not cover it."""
    toml = _civ(_raw("raw_write", "line", line), _restore(*MAIN_FM))
    _fails(tmp_path, "ft991a.toml", toml, "restore")


@pytest.mark.parametrize("rig", CIV_RIGS)
@pytest.mark.parametrize("data", ["", "00", "01", "d0", "d1", "c0", "c1", "c2", "c3"])
def test_civ_vfo_family_writes_off_the_sub_list_are_refused(
    tmp_path: Path, rig: str, data: str
) -> None:
    toml = _civ(_write("set_vfo", data_hex=data), _restore(*SELECTED, *UNSELECTED))
    _fails(tmp_path, rig, toml, "allow-list")


def test_civ_dual_watch_write_entry_is_refused(tmp_path: Path) -> None:
    toml = _civ(
        _write("set_dual_watch_on", data_hex=""), _restore(*SELECTED, *UNSELECTED)
    )
    _fails(tmp_path, "ic7610.toml", toml, "allow-list")


# --- R3: split and duplex share 0x0F but not a setting ---------------------------


@pytest.mark.parametrize("rig", CIV_RIGS)
@pytest.mark.parametrize("data", ["10", "11", "12"])
def test_civ_duplex_selector_is_not_covered_by_a_split_restore(
    tmp_path: Path, rig: str, data: str
) -> None:
    split = _restore(("get_split", "set_split"))
    _fails(tmp_path, rig, _civ(_write("set_split", data_hex=data), split), "0x0F")
    raw = _raw("raw_write", "body_hex", "0f" + data)
    _fails(tmp_path, rig, _civ(raw, split), "0x0F")


@pytest.mark.parametrize("rig", CIV_RIGS)
def test_civ_split_write_is_covered_only_by_a_split_restore(
    tmp_path: Path, rig: str
) -> None:
    step = _write("set_split", data_hex="01")
    _fails(tmp_path, rig, _civ(step, _restore(*SELECTED)), "restore")
    assert _loads(tmp_path, rig, _civ(step, _restore(("get_split", "set_split"))))


def test_ic705_repeater_shift_duplex_write_is_refused(tmp_path: Path) -> None:
    shift = _restore(("get_repeater_shift", "set_repeater_shift"))
    toml = _civ(_write("set_repeater_shift", data_hex="11"), shift)
    _fails(tmp_path, "ic705.toml", toml, "0x0F")


# --- R4: a restore item reads and writes the same setting ------------------------


@pytest.mark.parametrize(
    ("rig", "pair"),
    [
        *((rig, ("get_freq", "set_mode")) for rig in CIV_RIGS),
        *((rig, ("get_af_level", "set_rf_gain")) for rig in CIV_RIGS),
        *((rig, ("get_selected_freq", "set_unselected_freq")) for rig in CIV_RIGS),
        *((rig, ("get_freq", "set_mode")) for rig in CAT_RIGS),
        *((rig, ("get_freq", "set_freq_sub")) for rig in CAT_RIGS),
    ],
)
def test_restore_item_reads_and_writes_the_same_setting(
    tmp_path: Path, rig: str, pair: tuple[str, str]
) -> None:
    _fails(tmp_path, rig, _civ(_read("get_freq"), _restore(pair)), "same setting")


# --- CI-V 0x14 sub allow-list ----------------------------------------------------

LEVELS_ALLOWED = (
    "af_level rf_gain squelch nr_level pbt_inner pbt_outer cw_pitch notch_filter "
    "nb_level rf_power"
).split()
LEVELS_REFUSED = (
    "mic_gain compressor_level break_in_delay monitor_gain vox_gain anti_vox_gain"
).split()


@pytest.mark.parametrize("rig", CIV_RIGS)
def test_civ_level_writes_follow_the_sub_allow_list(tmp_path: Path, rig: str) -> None:
    for name in LEVELS_ALLOWED:
        own = _restore((f"get_{name}", f"set_{name}"))
        assert _loads(tmp_path, rig, _civ(_write(f"set_{name}", data_hex="0128"), own))
    for name in LEVELS_REFUSED:
        own = _restore((f"get_{name}", f"set_{name}"))
        toml = _civ(_write(f"set_{name}", data_hex="0128"), own)
        _fails(tmp_path, rig, toml, "allow-list")
