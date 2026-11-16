"""Typed [[probe.checks]] profile data (MOR-4464 B1a).

The loader parses checks into frozen dataclasses on ``RigConfig.probe_checks``
(empty when absent), rejects unknown keys, and builds every step's bytes from
the closed table at load. Check data names table entries and values only.
"""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from tests.probe.test_probe_table_closure import (
    CAT_EXTRA,
    CHECK_HEAD,
    check_toml,
    facts,
    load_checks,
)

REPO_ROOT = Path(__file__).resolve().parents[2]
RIGS = REPO_ROOT / "rigs"

CAT_CHECK = """
[probe]
[[probe.checks]]
id = "synth-freq-mode"
title = "Synthetic frequency and mode"
explain_what = "Sets VFO-A to 7.074 MHz and mode to USB for a moment, then reads it back."
explain_after = "Puts frequency and mode back exactly as they are now."
witness = ["freq_a", "mode_main"]
[[probe.checks.preconditions]]
entry = "freq_a"
equals = "FA007074000;"
[[probe.checks.steps]]
kind = "write"
entry = "freq_a"
value = 7074000
[[probe.checks.steps]]
kind = "read"
entry = "freq_a"
[[probe.checks.steps]]
kind = "ask"
question = "Does the display look right?"
[[probe.checks.restore]]
entry = "freq_a"
[[probe.checks.restore]]
entry = "mode_main"
rehearse = false
"""


def _load_with_probe(
    tmp_path: Path, base_name: str, probe_toml: str, old: str = "", new: str = ""
):
    from rigplane.profiles.rig_loader import load_rig

    for helper in ("_ctcss_tables_v1.toml", "_keyboard-default.toml"):
        shutil.copy(RIGS / helper, tmp_path / helper)
    base = (RIGS / base_name).read_text(encoding="utf-8")
    if old:
        assert base.count(old) == 1
        base = base.replace(old, new)
    path = tmp_path / "synth.toml"
    path.write_text(base + "\n" + probe_toml, encoding="utf-8")
    return load_rig(path)


def _fails(
    toml: str, match: str, name: str = "ft991a", extra: dict | None = None
) -> None:
    from rigplane.profiles.rig_loader import RigLoadError

    with pytest.raises(RigLoadError, match=match):
        load_checks(facts(name, extra), toml)


def test_profile_without_probe_section_loads_unchanged() -> None:
    from rigplane.profiles.rig_loader import load_rig

    for name in ("ft991a", "ic7300", "ic705"):
        assert load_rig(RIGS / f"{name}.toml").probe_checks == ()


def test_cat_check_loads_into_typed_records(tmp_path: Path) -> None:
    from rigplane.profiles.probe_checks import compile_table, judged_frame

    config = _load_with_probe(tmp_path, "ft991a.toml", CAT_CHECK)
    (check,) = config.probe_checks
    assert check.id == "synth-freq-mode"
    assert check.title == "Synthetic frequency and mode"
    assert check.witness == ("freq_a", "mode_main")
    assert [(p.entry, p.equals) for p in check.preconditions] == [
        ("freq_a", "FA007074000;")
    ]
    assert [step.kind for step in check.steps] == ["write", "read", "ask"]
    assert (check.steps[0].entry, check.steps[0].value) == ("freq_a", 7074000)
    table = compile_table(facts("ft991a"), ("freq_a", "mode_main"))
    step0, step1 = check.steps[:2]
    write = (step0.entry, step0.kind, step0.value, None, check.restored)
    assert judged_frame(table, *write) == b"FA007074000;"
    assert judged_frame(table, step1.entry, step1.kind, None, None, ()) == b"FA;"
    assert check.steps[2].question == "Does the display look right?"
    assert [(item.entry, item.rehearse) for item in check.restore] == [
        ("freq_a", True),
        ("mode_main", False),
    ]
    assert check.restored == frozenset({("freq_a", None), ("mode_main", None)})


def test_unknown_keys_rejected() -> None:
    _fails(CAT_CHECK.replace('id = "synth-freq-mode"', 'id = "x"\nbogus = 1'), "bogus")
    _fails(
        CAT_CHECK.replace(
            'entry = "mode_main"', 'entry = "mode_main"\nread = "get_mode"'
        ),
        "read",
    )
    _fails(
        CAT_CHECK.replace("value = 7074000", 'value = 7074000\ncommand = "set_freq"'),
        "command",
    )


def test_non_ascii_title_rejected() -> None:
    _fails(
        CAT_CHECK.replace("Synthetic frequency and mode", "Synthétique"),
        "[Aa][Ss][Cc][Ii][Ii]",
    )


@pytest.mark.parametrize("kind", ["raw_write", "raw_read", "send"])
def test_only_write_read_and_ask_are_step_kinds(kind: str) -> None:
    _fails(CAT_CHECK.replace('kind = "ask"', f'kind = "{kind}"'), "kind")


@pytest.mark.parametrize(
    ("name", "entry"),
    [
        ("ft991a", "band"),
        ("ft991a", "set_freq"),
        ("ic705", "duplex"),
        ("ic705", "mic_gain"),
    ],
)
def test_an_entry_outside_the_table_fails(name: str, entry: str) -> None:
    toml = check_toml([("read", entry)], [])
    _fails(toml, "not in the table", name)


def test_an_entry_the_profile_does_not_declare_fails() -> None:
    toml = check_toml([("read", "speech_processor")], [])
    _fails(toml, "not available")
    assert load_checks(facts("ft991a", CAT_EXTRA), toml)


def test_a_declared_template_of_the_wrong_shape_fails() -> None:
    """FTX-1 split is ST, not FT; its power carries a head field."""
    _fails(check_toml([("read", "split")], []), "ST", "ftx1")
    _fails(check_toml([("read", "power")], []), "power", "ftx1")


def test_a_value_outside_the_domain_fails() -> None:
    for value in (1_000_000_000, "7074000", -1):
        _fails(check_toml([("write", "freq_a", value)], ["freq_a"]), "domain")
    _fails(check_toml([("write", "split", "2")], ["split"]), "domain")


def test_a_read_needs_a_read_form_and_a_restore_item_needs_both() -> None:
    _fails(check_toml([("read", "vfo_exchange")], []), "read", "ic705")
    _fails(check_toml([("read", "freq_a")], ["info"]), "info", "ft991a", CAT_EXTRA)
    _fails(
        check_toml([("read", "freq_selected")], [], 'witness = ["vfo_exchange"]'),
        "read",
        "ic705",
    )


def test_receiver_is_civ_only_and_only_on_routed_entries() -> None:
    _fails(check_toml([("read", "freq_a", None, "sub")], []), "receiver")
    _fails(
        check_toml([("write", "rf_power", 1, "sub")], ["rf_power@sub"]),
        "receiver",
        "ic7610",
    )
    _fails(
        check_toml([("write", "af_level", 1, "left")], ["af_level"]),
        "receiver",
        "ic7610",
    )


def test_write_steps_need_restore_items() -> None:
    _fails(check_toml([("write", "freq_a", 7074000)], []), "restore")


def test_repeated_check_ids_fail() -> None:
    body = CAT_CHECK.split("[probe]\n", 1)[1]
    _fails(CAT_CHECK + body, "repeats")


def test_probe_checks_need_civ_or_yaesu_cat(tmp_path: Path) -> None:
    from rigplane.profiles.rig_loader import RigLoadError

    with pytest.raises(RigLoadError, match="CI-V or Yaesu CAT"):
        _load_with_probe(tmp_path, "tx500.toml", CAT_CHECK)


def test_profile_without_probe_ignores_probe_only_mode_code_rules(
    tmp_path: Path,
) -> None:
    config = _load_with_probe(
        tmp_path, "ft991a.toml", "", '"B", "C", "D", "E",', '"B", "C", "D", "É",'
    )
    assert config.probe_checks == ()
    assert config.mode_codes is not None and config.mode_codes[-1] == "É"


def test_non_ascii_c4fm_code_with_probe_checks_is_a_load_error(tmp_path: Path) -> None:
    from rigplane.profiles.rig_loader import RigLoadError

    with pytest.raises(RigLoadError, match="ASCII"):
        _load_with_probe(
            tmp_path,
            "ft991a.toml",
            CAT_CHECK,
            '"B", "C", "D", "E",',
            '"B", "C", "D", "É",',
        )


STEP = '[[probe.checks.steps]]\nkind = "read"\nentry = "freq_a"\n'


@pytest.mark.parametrize(
    "toml",
    [
        CAT_CHECK.replace('witness = ["freq_a", "mode_main"]', "witness = 1"),
        CHECK_HEAD + "steps = false\n",
        CHECK_HEAD + "restore = 7\n" + STEP,
        CHECK_HEAD + 'preconditions = "x"\n' + STEP,
        CHECK_HEAD + STEP + "receiver = []\n",
        CHECK_HEAD + STEP.replace('entry = "freq_a"', "entry = []"),
        CHECK_HEAD + "witness = [[1]]\n" + STEP,
        "[probe]\nchecks = false\n",
        "[probe]\nchecks = {}\n",
    ],
)
def test_malformed_shapes_are_load_errors_not_type_errors(toml: str) -> None:
    _fails(toml, ".")


def test_an_absent_checks_field_is_no_checks() -> None:
    assert load_checks(facts("ft991a"), "[probe]\n") == ()
