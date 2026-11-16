"""The probe write-check table holds only what the first checks need (MOR-4464 B1a).

Entries per protocol are pinned here; everything else (band select, duplex,
VFO equalize and select, dual watch, the 0x14 transmit levels, raw lines) is
ABSENT: its bytes do not parse on any profile, so no check can name or send it.
"""

from __future__ import annotations

import pytest

from tests.probe.test_probe_table_closure import ALL_TABLES, table_for


def test_the_table_entries_are_exactly_the_first_checks_needs() -> None:
    from rigplane.profiles.probe_checks import TABLE

    ids = {protocol: [spec.id for spec in specs] for protocol, specs in TABLE.items()}
    assert ids == {
        "yaesu_cat": [
            "freq_a",
            "freq_b",
            "mode_main",
            "split",
            "power",
            "speech_processor",
            "info",
        ],
        "civ": [
            "freq_selected",
            "freq_unselected",
            "mode_selected",
            "mode_unselected",
            "vfo_exchange",
            "rf_power",
            "af_level",
        ],
    }


def test_read_only_and_write_only_entries() -> None:
    from rigplane.profiles.probe_checks import TABLE

    forms = {spec.id: spec.forms for specs in TABLE.values() for spec in specs}
    assert all(form.write is None for form in forms["info"])
    assert all(form.read is None for form in forms["vfo_exchange"])
    wrapper = {spec.id for specs in TABLE.values() for spec in specs if spec.wrapper_ok}
    assert wrapper == {"af_level"}


ABSENT_CAT = (
    b"BS05;",
    b"BS;",
    b"AB;",
    b"BA;",
    b"SV;",
    b"PR1;",
    b"PR;",
    b"ST1;",
    b"AI1;",
    b"MD12;",
    b"MD1;",
    b"PL050;",
    b"FB;TX1;",
    b"KR1;",
    b"EX0301;",
)
ABSENT_CIV = (
    "07",
    "0700",
    "0701",
    "07a0",
    "07b1",
    "07c1",
    "07d0",
    "07d1",
    "0f",
    "0f00",
    "0f01",
    "0f10",
    "0f11",
    "0f12",
    "140b0128",
    "140e0128",
    "140f0128",
    "14150128",
    "14160128",
    "14170128",
    "14020128",
    "14030128",
    "16460001",
    "1a050001",
    "2800",
    "1c0001",
)


@pytest.mark.parametrize("name", ALL_TABLES)
def test_absent_settings_do_not_parse_on_any_profile(name: str) -> None:
    from rigplane.profiles.probe_checks import parse_frame

    table = table_for(name)
    bodies = (
        ABSENT_CAT
        if table.protocol == "yaesu_cat"
        else [bytes.fromhex(h) for h in ABSENT_CIV]
    )
    for body in bodies:
        assert parse_frame(table, body) is None, body


@pytest.mark.parametrize("name", ("ic705", "ic7300", "ic7610"))
def test_the_civ_read_forms_carry_no_value(name: str) -> None:
    from rigplane.profiles.probe_checks import parse_frame

    table = table_for(name)
    for entry in table.entries:
        if entry.read is not None:
            got = parse_frame(table, entry.read)
            assert (got.entry.id, got.form, got.value) == (entry.id, "read", None)
