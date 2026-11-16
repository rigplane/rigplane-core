"""Write policy lists and send-time gate (MOR-4464 B1, RED first).

Per-protocol allow-lists and never-lists for every byte the write
checks may send. Static shape first: the verbs/commands on each list,
and that no allow-list entry is also on the never-list.
"""

from __future__ import annotations

from pathlib import Path


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


def _refusal(protocol: str, body: bytes, **kwargs):
    from rigplane.profiles.probe_checks import policy_refusal

    return policy_refusal(protocol, body, **kwargs)


def test_cat_compound_or_unterminated_line_is_refused() -> None:
    assert _refusal("yaesu_cat", b"FA007074000;TX1;", write=True)
    assert _refusal("yaesu_cat", b"FA;TX1;", write=False)
    assert _refusal("yaesu_cat", b"FA007074000", write=True)
    assert _refusal("yaesu_cat", b"MD02;TX1;", write=True)
    assert _refusal("yaesu_cat", b"FA007074000;", write=True) is None


def test_civ_body_with_frame_delimiters_is_refused() -> None:
    tail = bytes.fromhex("050000740700fdfefe94e01c0001")
    assert _refusal("civ", tail, write=True)
    assert _refusal("civ", bytes.fromhex("03fd"), write=False)
    assert _refusal("civ", bytes.fromhex("03fefe"), write=False)


def test_civ_receiver_wrapper_is_unwrapped_before_the_never_list() -> None:
    assert _refusal("civ", bytes.fromhex("29001c0001"), write=False)
    assert _refusal("civ", bytes.fromhex("29011c0001"), write=True)
    assert _refusal("civ", bytes.fromhex("29002800"), write=True)
    assert _refusal("civ", bytes.fromhex("290029001c0001"), write=False)
    assert _refusal("civ", bytes.fromhex("29000300"), write=False) is None
    assert _refusal("civ", bytes.fromhex("290114010000"), write=True) is None


def test_civ_vfo_operations_need_both_vfos_including_swap() -> None:
    for body in ("07a0", "07b1", "07b0"):
        data = bytes.fromhex(body)
        assert _refusal("civ", data, write=True), body
        assert _refusal("civ", data, write=True, both_vfos=False), body
        assert _refusal("civ", data, write=True, both_vfos=True) is None, body
    assert _refusal("civ", bytes.fromhex("0700"), write=True) is None


def _config(name: str):
    from rigplane.profiles.rig_loader import load_rig

    return load_rig(Path(__file__).resolve().parents[2] / "rigs" / f"{name}.toml")


def _items(*pairs: tuple[str, str]):
    from rigplane.profiles.probe_checks import ProbeRestoreItem

    return tuple(ProbeRestoreItem(read=r, write=w) for r, w in pairs)


def test_both_vfos_come_from_restore_targets_not_distinct_reads() -> None:
    from rigplane.profiles.probe_checks import both_vfos

    selected = _items(("get_freq", "set_freq"), ("get_mode", "set_mode"))
    full = selected + _items(
        ("get_unselected_freq", "set_unselected_freq"),
        ("get_unselected_mode", "set_unselected_mode"),
    )
    for name in ("ic7300", "ic705", "ic7610"):
        config = _config(name)
        args = (config.commands, config.protocol_type)
        assert not both_vfos(selected, *args), name
        assert not both_vfos(full[:3], *args), name
        assert both_vfos(full, *args), name
