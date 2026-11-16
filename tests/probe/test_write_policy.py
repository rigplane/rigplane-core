"""Write policy lists and send-time gate (MOR-4464 B1, RED first).

Per-protocol allow-lists and never-lists for every byte the write
checks may send. Static shape first: the verbs/commands on each list,
and that no allow-list entry is also on the never-list.
"""

from __future__ import annotations


def test_yaesu_write_allow_list_is_exact() -> None:
    from rigplane.profiles.probe_checks import YAESU_WRITE_ALLOW

    assert YAESU_WRITE_ALLOW == frozenset({"FA", "FB", "MD", "FT", "PC", "BS", "PR"})


def test_civ_write_allow_list_is_exact() -> None:
    from rigplane.profiles.probe_checks import CIV_WRITE_ALLOW

    assert CIV_WRITE_ALLOW == frozenset({0x05, 0x06, 0x07, 0x0F, 0x14, 0x25, 0x26})


def test_yaesu_never_list_names_transmit_memory_and_keyer_verbs() -> None:
    from rigplane.profiles.probe_checks import YAESU_NEVER

    for verb in ("TX", "MX", "MW", "MC", "KY", "KM", "SC", "VX", "BI", "DT"):
        assert verb in YAESU_NEVER, verb


def test_civ_never_list_names_voice_and_tx_families() -> None:
    from rigplane.profiles.probe_checks import CIV_NEVER_ANY

    for entry in ((0x28, None), (0x16, 0x46), (0x16, 0x47), (0x22, None)):
        assert entry in CIV_NEVER_ANY, entry


def test_no_allow_entry_is_also_never_listed() -> None:
    from rigplane.profiles import probe_checks as policy

    yaesu_overlap = set(policy.YAESU_WRITE_ALLOW) & set(policy.YAESU_NEVER)
    assert yaesu_overlap == set()
    civ_allow = {(command, None) for command in policy.CIV_WRITE_ALLOW}
    civ_overlap = {
        command
        for command, _sub in civ_allow
        if (command, None) in policy.CIV_NEVER_ANY
    }
    assert civ_overlap == set()


def test_send_gate_refuses_yaesu_tx_verb_naming_it() -> None:
    from rigplane.profiles.probe_checks import check_send_allowed

    try:
        check_send_allowed("yaesu_cat", b"TX1;")
    except ValueError as error:
        assert "TX" in str(error)
    else:
        raise AssertionError("TX write must be refused")


def test_send_gate_allows_profile_write_verbs() -> None:
    from rigplane.profiles.probe_checks import check_send_allowed

    check_send_allowed("yaesu_cat", b"FA007074000;")
    check_send_allowed("civ", bytes((0x05, 0x00, 0x00, 0x74, 0x07, 0x00)))


def test_send_gate_refuses_civ_voice_memory_naming_it() -> None:
    from rigplane.profiles.probe_checks import check_send_allowed

    try:
        check_send_allowed("civ", bytes((0x28, 0x00)))
    except ValueError as error:
        assert "28" in str(error)
    else:
        raise AssertionError("0x28 write must be refused")
