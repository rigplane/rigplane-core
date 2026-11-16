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
    from rigplane.profiles.rig_loader import load_rig

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

    bad = CAT_CHECK.replace('command = "set_freq"', 'command = "set_ptt"', 1)
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
        'command = "set_freq"', 'command = "set_auto_info"', 1
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
