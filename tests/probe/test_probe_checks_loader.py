"""Typed [[probe.checks]] profile data (MOR-4464 B1, RED first).

The loader parses checks into frozen dataclasses on
``RigConfig.probe_checks`` (empty when absent), rejects unknown keys,
and enforces the write policy at load with the verb named.
"""

from __future__ import annotations

from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
RIGS = REPO_ROOT / "rigs"

CAT_CHECK = """
[probe]
[[probe.checks]]
id = "synth-freq-mode"
title = "Synthetic frequency and mode"
explain_what = "Sets VFO-A to 7.074 MHz and mode to USB for a moment, then reads it back."
explain_after = "Puts frequency and mode back exactly as they are now."
witness = ["get_freq", "get_mode"]
[[probe.checks.steps]]
kind = "write"
command = "set_freq"
[probe.checks.steps.values]
freq = 7074000
[[probe.checks.steps]]
kind = "read"
command = "get_freq"
[[probe.checks.steps]]
kind = "ask"
question = "Does the display look right?"
[[probe.checks.restore]]
read = "get_freq"
write = "set_freq"
[[probe.checks.restore]]
read = "get_mode"
write = "set_mode"
"""


def _load_with_probe(tmp_path: Path, base_name: str, probe_toml: str):
    import shutil

    from rigplane.profiles.rig_loader import load_rig

    for helper in ("_ctcss_tables_v1.toml", "_keyboard-default.toml"):
        shutil.copy(RIGS / helper, tmp_path / helper)
    base = (RIGS / base_name).read_text(encoding="utf-8")
    path = tmp_path / "synth.toml"
    path.write_text(base + "\n" + probe_toml, encoding="utf-8")
    return load_rig(path)


def test_profile_without_probe_section_loads_unchanged() -> None:
    from rigplane.profiles.rig_loader import load_rig

    config = load_rig(RIGS / "ft991a.toml")
    assert config.probe_checks == ()


def test_cat_check_loads_into_typed_records(tmp_path: Path) -> None:
    config = _load_with_probe(tmp_path, "ftx1.toml", CAT_CHECK)
    (check,) = config.probe_checks
    assert check.id == "synth-freq-mode"
    assert check.title == "Synthetic frequency and mode"
    assert check.witness == ("get_freq", "get_mode")
    assert [step.kind for step in check.steps] == ["write", "read", "ask"]
    assert check.steps[0].command == "set_freq"
    assert dict(check.steps[0].values) == {"freq": 7074000}
    assert check.steps[2].question == "Does the display look right?"
    assert [(item.read, item.write) for item in check.restore] == [
        ("get_freq", "set_freq"),
        ("get_mode", "set_mode"),
    ]
    assert all(item.rehearse for item in check.restore)


def test_never_list_verb_fails_at_load_naming_the_verb(tmp_path: Path) -> None:
    from rigplane.profiles.rig_loader import RigLoadError

    bad = CAT_CHECK.replace(
        'command = "set_freq"\n[probe.checks.steps.values]\nfreq = 7074000',
        'command = "set_ptt"\n[probe.checks.steps.values]\nstate = "1"',
        1,
    )
    with pytest.raises(RigLoadError, match="TX"):
        _load_with_probe(tmp_path, "ftx1.toml", bad)


def test_raw_write_without_restore_item_fails(tmp_path: Path) -> None:
    from rigplane.profiles.rig_loader import RigLoadError

    bad = CAT_CHECK.replace(
        'kind = "ask"\nquestion = "Does the display look right?"',
        'kind = "raw_write"\nline = "PR1;"',
    )
    with pytest.raises(RigLoadError, match="restore"):
        _load_with_probe(tmp_path, "ftx1.toml", bad)


def test_write_verb_off_the_allow_list_fails(tmp_path: Path) -> None:
    from rigplane.profiles.rig_loader import RigLoadError

    bad = CAT_CHECK.replace(
        'command = "set_freq"\n[probe.checks.steps.values]\nfreq = 7074000',
        'command = "set_auto_info"\n[probe.checks.steps.values]\nstate = "1"',
        1,
    )
    with pytest.raises(RigLoadError, match="AI"):
        _load_with_probe(tmp_path, "ftx1.toml", bad)


def test_unknown_keys_rejected(tmp_path: Path) -> None:
    from rigplane.profiles.rig_loader import RigLoadError

    bad = CAT_CHECK.replace('id = "synth-freq-mode"', 'id = "x"\nbogus = 1')
    with pytest.raises(RigLoadError, match="bogus"):
        _load_with_probe(tmp_path, "ftx1.toml", bad)


def test_non_ascii_title_rejected(tmp_path: Path) -> None:
    from rigplane.profiles.rig_loader import RigLoadError

    bad = CAT_CHECK.replace("Synthetic frequency and mode", "Synthétique")
    with pytest.raises(RigLoadError, match="[Aa][Ss][Cc][Ii][Ii]"):
        _load_with_probe(tmp_path, "ftx1.toml", bad)


CIV_BASE = """
[probe]
[[probe.checks]]
id = "synth"
title = "Synthetic"
explain_what = "Changes a setting for a moment."
explain_after = "Puts it back."
{extra}
{steps}
{restore}
"""


def _civ(steps: str, restore: str, extra: str = "") -> str:
    return CIV_BASE.format(steps=steps, restore=restore, extra=extra)


def _restore(*pairs: tuple[str, str]) -> str:
    return "\n".join(
        f'[[probe.checks.restore]]\nread = "{r}"\nwrite = "{w}"' for r, w in pairs
    )


def _raw(kind: str, key: str, value: str) -> str:
    return f'[[probe.checks.steps]]\nkind = "{kind}"\n{key} = "{value}"'


SELECTED = (("get_freq", "set_freq"), ("get_mode", "set_mode"))
UNSELECTED = (
    ("get_unselected_freq", "set_unselected_freq"),
    ("get_unselected_mode", "set_unselected_mode"),
)
CIV_RIGS = ("ic7300.toml", "ic705.toml", "ic7610.toml")


def _fails(tmp_path: Path, rig: str, toml: str, match: str) -> None:
    from rigplane.profiles.rig_loader import RigLoadError

    with pytest.raises(RigLoadError, match=match):
        _load_with_probe(tmp_path, rig, toml)


def test_cat_injected_value_cannot_smuggle_a_second_command(tmp_path: Path) -> None:
    bad = CAT_CHECK.replace(
        'command = "set_freq"\n[probe.checks.steps.values]\nfreq = 7074000',
        'command = "set_mode"\n[probe.checks.steps.values]\nmode = "2;TX1"',
        1,
    )
    _fails(tmp_path, "ftx1.toml", bad, "TX|one command|';'")


@pytest.mark.parametrize("rig", ["ftx1.toml", "ft991a.toml"])
def test_cat_raw_write_with_second_command_is_refused(tmp_path: Path, rig: str) -> None:
    bad = CAT_CHECK.replace(
        'kind = "ask"\nquestion = "Does the display look right?"',
        'kind = "raw_write"\nline = "FA007074000;TX1;"',
    )
    _fails(tmp_path, rig, bad, "one command|';'")


@pytest.mark.parametrize("rig", CIV_RIGS)
def test_civ_raw_write_with_a_second_frame_is_refused(tmp_path: Path, rig: str) -> None:
    body = "050000740700fdfefe94e01c0001"
    toml = _civ(_raw("raw_write", "body_hex", body), _restore(*SELECTED))
    _fails(tmp_path, rig, toml, "delimiter|one command|frame")


@pytest.mark.parametrize("rig", CIV_RIGS)
@pytest.mark.parametrize("body", ["29001c0001", "29002800", "29011c0001"])
def test_civ_wrapped_never_listed_raw_read_is_refused(
    tmp_path: Path, rig: str, body: str
) -> None:
    toml = _civ(_raw("raw_read", "body_hex", body), "")
    _fails(tmp_path, rig, toml, "never-listed")


def _vfo_step(data: str) -> str:
    return (
        '[[probe.checks.steps]]\nkind = "write"\ncommand = "set_vfo"\n'
        f'[probe.checks.steps.values]\ndata_hex = "{data}"'
    )


@pytest.mark.parametrize("rig", CIV_RIGS)
@pytest.mark.parametrize("data", ["a0", "b1", "b0"])
def test_vfo_operations_need_selected_and_unselected_restores(
    tmp_path: Path, rig: str, data: str
) -> None:
    for pairs in (SELECTED, SELECTED + UNSELECTED[:1]):
        _fails(tmp_path, rig, _civ(_vfo_step(data), _restore(*pairs)), "both VFOs")
    (check,) = _load_with_probe(
        tmp_path, rig, _civ(_vfo_step(data), _restore(*SELECTED, *UNSELECTED))
    ).probe_checks
    assert check.steps[0].data_hex == data


@pytest.mark.parametrize(
    ("rig", "read", "where"),
    [
        ("ftx1.toml", "get_ptt", "TX"),
        ("ft991a.toml", "get_ptt", "TX"),
        ("ic7300.toml", "ptt_on", "never-listed"),
        ("ic7610.toml", "ptt_on", "never-listed"),
    ],
)
def test_never_listed_reads_fail_in_every_read_position(
    tmp_path: Path, rig: str, read: str, where: str
) -> None:
    step = f'[[probe.checks.steps]]\nkind = "read"\ncommand = "{read}"'
    restore = _restore(("get_freq", "set_freq"))
    safe = step.replace(read, "get_freq")
    _fails(tmp_path, rig, _civ(step, restore), where)
    _fails(tmp_path, rig, _civ(safe, restore, f'witness = ["{read}"]'), where)
    pre = f'[[probe.checks.preconditions]]\nread = "{read}"\nequals = "x"\n'
    _fails(tmp_path, rig, _civ(safe, restore, pre), where)
    _fails(tmp_path, rig, _civ(safe, _restore((read, "set_freq"))), where)


def test_precondition_with_missing_command_fails(tmp_path: Path) -> None:
    pre = '[[probe.checks.preconditions]]\nread = "get_nothing"\nequals = "x"\n'
    steps = '[[probe.checks.steps]]\nkind = "read"\ncommand = "get_freq"'
    _fails(tmp_path, "ic7300.toml", _civ(steps, "", pre), "get_nothing")
    _fails(tmp_path, "ftx1.toml", _civ(steps, "", pre), "get_nothing")


@pytest.mark.parametrize("rig", CIV_RIGS)
def test_civ_raw_restore_matches_the_specific_setting(tmp_path: Path, rig: str) -> None:
    raw = _raw("raw_write", "body_hex", "14010000")
    _fails(
        tmp_path, rig, _civ(raw, _restore(("get_rf_gain", "set_rf_gain"))), "restore"
    )
    config = _load_with_probe(
        tmp_path, rig, _civ(raw, _restore(("get_af_level", "set_af_level")))
    )
    assert config.probe_checks[0].steps[0].body_hex == "14010000"
    wrapped = _raw("raw_write", "body_hex", "290114010000")
    _fails(
        tmp_path,
        rig,
        _civ(wrapped, _restore(("get_af_level", "set_af_level"))),
        "restore",
    )


def test_cat_raw_restore_matches_the_selector(tmp_path: Path) -> None:
    sub = _raw("raw_write", "line", "MD12;")
    main = _restore(("get_mode", "set_mode"))
    _fails(tmp_path, "ftx1.toml", _civ(sub, main), "restore")
    _fails(
        tmp_path,
        "ftx1.toml",
        _civ(
            _raw("raw_write", "line", "FB007074000;"),
            _restore(("get_freq", "set_freq")),
        ),
        "restore",
    )
    ok = _load_with_probe(
        tmp_path, "ftx1.toml", _civ(sub, _restore(("get_mode_sub", "set_mode_sub")))
    )
    assert ok.probe_checks[0].steps[0].line == "MD12;"
    ok = _load_with_probe(
        tmp_path, "ft991a.toml", _civ(_raw("raw_write", "line", "MD02;"), main)
    )
    assert ok.probe_checks


def test_non_ascii_cat_template_is_a_load_error(tmp_path: Path) -> None:
    from rigplane.commands.command_spec import CatCommandSpec
    from rigplane.profiles.probe_checks import parse_probe_checks
    from rigplane.profiles.rig_loader import RigLoadError, load_rig

    config = load_rig(RIGS / "ftx1.toml")
    spec = CatCommandSpec(read="F\u00e9;", write="F\u00e9{x};", parse="F\u00e9{x};")
    commands = {**config.commands, "get_odd": spec, "set_odd": spec}
    step = '[probe.checks.steps.values]\nx = "1"\n'
    pieces = {
        "read": '[[probe.checks.steps]]\nkind = "read"\ncommand = "get_odd"',
        "write": '[[probe.checks.steps]]\nkind = "write"\ncommand = "set_odd"\n' + step,
        "restore": _restore(("get_odd", "set_odd")),
    }
    import tomllib

    for steps, restore in (
        (pieces["read"], ""),
        (pieces["write"], _restore(("get_freq", "set_freq"))),
        (pieces["read"], pieces["restore"]),
    ):
        raw = tomllib.loads(_civ(steps, restore))["probe"]
        with pytest.raises(RigLoadError, match="ASCII"):
            parse_probe_checks(
                "t.toml", raw, commands=commands, protocol_type="yaesu_cat"
            )
