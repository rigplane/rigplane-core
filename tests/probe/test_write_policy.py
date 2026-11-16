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

    levels = (0x01, 0x02, 0x03, 0x06, 0x07, 0x08, 0x09, 0x0A, 0x0D, 0x12)
    assert CIV_WRITE_ALLOW == frozenset(
        {(0x25, 0x00), (0x25, 0x01), (0x26, 0x00), (0x26, 0x01), (0x0F, None)}
        | {(0x07, op) for op in (0xA0, 0xB0, 0xB1)}
        | {(0x14, sub) for sub in levels}
    )


def test_yaesu_never_list_names_transmit_memory_and_keyer_verbs() -> None:
    from rigplane.profiles.probe_checks import YAESU_NEVER

    for verb in ("TX", "MX", "MW", "MC", "KY", "KM", "SC", "VX", "BI", "DT"):
        assert verb in YAESU_NEVER, verb


def _everything(protocol: str, body: bytes):
    """Every setting the body could need, so only the allow-list decides."""
    from rigplane.profiles.probe_checks import setting_of

    return {setting_of(protocol, body)} | {
        (rx, command, sub)
        for rx in (None, 0, 1)
        for command, sub in ((0x25, 0), (0x25, 1), (0x26, 0), (0x26, 1))
    }


VOICE = frozenset({b"\x17", b"\x22"})


def test_civ_voice_and_tx_families_are_refused_as_writes() -> None:
    for body in ("2800", "164601", "164701", "2200", "1c0001", "1a050001", "0f11"):
        data = bytes.fromhex(body)
        why = _refusal("civ", data, restored=_everything("civ", data))
        assert why and "allow-list" in why, body


def test_civ_refused_level_and_vfo_subs_are_off_the_allow_list() -> None:
    from rigplane.profiles.probe_checks import CIV_WRITE_ALLOW

    for sub in (0x0B, 0x0E, 0x0F, 0x15, 0x16, 0x17):
        assert (0x14, sub) not in CIV_WRITE_ALLOW
    for op in (None, 0x00, 0x01, 0xC0, 0xC1, 0xD0, 0xD1):
        assert (0x07, op) not in CIV_WRITE_ALLOW
    for selector in (0x10, 0x11, 0x12):
        assert (0x0F, selector) not in CIV_WRITE_ALLOW


def test_no_allow_entry_is_also_never_listed() -> None:
    from rigplane.profiles import probe_checks as policy

    assert set(policy.YAESU_WRITE_ALLOW) & set(policy.YAESU_NEVER) == set()


def test_digital_voice_mode_values_are_refused() -> None:
    for body in ("0617", "062201", "2600170001", "2601220001", "29010617"):
        data = bytes.fromhex(body)
        why = _refusal("civ", data, restored=_everything("civ", data))
        assert why and "digital voice" in why, body
    assert _refusal("civ", b"\x06\x01", restored=_everything("civ", b"\x06")) is None
    md = b"MD0H;"
    assert "digital voice" in _refusal("yaesu_cat", md, restored={b"MD0"}, voice_codes={b"H"})
    assert "digital voice" in _refusal("yaesu_cat", md, restored={b"MD0"}, voice_codes=None)
    assert _refusal("yaesu_cat", b"MD0E;", restored={b"MD0"}, voice_codes={b"H"}) is None


def test_digital_voice_codes_come_from_the_profile() -> None:
    from rigplane.profiles.probe_checks import digital_voice_codes

    assert digital_voice_codes("civ", (), None) == VOICE
    for name, codes in (("ftx1", {b"H", b"I"}), ("ft991a", {b"E"})):
        config = _config(name)
        got = digital_voice_codes(config.protocol_type, config.modes, config.mode_codes)
        assert got == codes, name
    assert digital_voice_codes("yaesu_cat", ("C4FM",), None) is None


def test_send_gate_refuses_yaesu_tx_verb_naming_it() -> None:
    from rigplane.profiles.probe_checks import check_send_allowed

    try:
        check_send_allowed("yaesu_cat", b"TX1;", restored={b"TX"}, voice_codes=None)
    except ValueError as error:
        assert "TX" in str(error)
    else:
        raise AssertionError("TX write must be refused")


def test_send_gate_allows_profile_write_verbs() -> None:
    from rigplane.profiles.probe_checks import check_send_allowed

    check_send_allowed("yaesu_cat", b"FA007074000;", restored={b"FA"}, voice_codes=None)
    freq = bytes((0x05, 0x00, 0x00, 0x74, 0x07, 0x00))
    check_send_allowed("civ", freq, restored={(None, 0x25, 0)}, voice_codes=VOICE)


def test_send_gate_refuses_a_write_without_its_restore() -> None:
    from rigplane.profiles.probe_checks import check_send_allowed

    freq = bytes((0x05, 0x00, 0x00, 0x74, 0x07, 0x00))
    for protocol, body, restored in (
        ("yaesu_cat", b"FA007074000;", {b"FB", b"MD0"}),
        ("civ", freq, {(None, 0x25, 1), (0, 0x25, 0)}),
    ):
        try:
            check_send_allowed(protocol, body, restored=restored, voice_codes=VOICE)
        except ValueError as error:
            assert "restore" in str(error)
        else:
            raise AssertionError(f"{body!r} must need its restore")


def test_send_gate_refuses_civ_voice_memory_naming_it() -> None:
    from rigplane.profiles.probe_checks import check_send_allowed

    try:
        check_send_allowed("civ", bytes((0x28, 0x00)), restored=(), voice_codes=VOICE)
    except ValueError as error:
        assert "28" in str(error)
    else:
        raise AssertionError("0x28 write must be refused")


def _refusal(protocol: str, body: bytes, **kwargs):
    from rigplane.profiles.probe_checks import policy_refusal

    kwargs.setdefault("voice_codes", VOICE)
    kwargs.setdefault("restored", _everything(protocol, body))
    return policy_refusal(protocol, body, **kwargs)


def test_cat_compound_or_unterminated_line_is_refused() -> None:
    assert _refusal("yaesu_cat", b"FA007074000;TX1;")
    assert _refusal("yaesu_cat", b"FA;TX1;")
    assert _refusal("yaesu_cat", b"FA007074000")
    assert _refusal("yaesu_cat", b"MD02;TX1;")
    assert _refusal("yaesu_cat", b"FA007074000;") is None


def test_civ_body_with_frame_delimiters_is_refused() -> None:
    tail = bytes.fromhex("050000740700fdfefe94e01c0001")
    assert _refusal("civ", tail)
    assert _refusal("civ", bytes.fromhex("1401fd"))
    assert _refusal("civ", bytes.fromhex("1401fefe"))


def test_civ_receiver_wrapper_is_unwrapped_before_the_allow_list() -> None:
    assert _refusal("civ", bytes.fromhex("29001c0001"))
    assert _refusal("civ", bytes.fromhex("29011c0001"))
    assert _refusal("civ", bytes.fromhex("29002800"))
    assert _refusal("civ", bytes.fromhex("290029001c0001"))
    assert _refusal("civ", bytes.fromhex("290114010000")) is None
    sub_af = bytes.fromhex("290114010000")
    assert _refusal("civ", sub_af, restored={(None, 0x14, 0x01)})
    assert _refusal("civ", sub_af, restored={(1, 0x14, 0x01)}) is None


def _vfo_restored(*names: str):
    from rigplane.profiles.probe_checks import restored_settings

    config = _config("ic7300")
    items = _items(*((n.replace("set_", "get_"), n) for n in names))
    return restored_settings(items, config.commands, config.protocol_type)


def test_civ_vfo_operations_need_both_vfos_including_swap() -> None:
    full = ("set_freq", "set_mode", "set_unselected_freq", "set_unselected_mode")
    for body in ("07a0", "07b1", "07b0"):
        data = bytes.fromhex(body)
        assert _refusal("civ", data, restored=_vfo_restored(*full[:3])), body
        assert _refusal("civ", data, restored=_vfo_restored(*full)) is None, body
    assert _refusal("civ", bytes.fromhex("0700"), restored=_vfo_restored(*full))


def _config(name: str):
    from rigplane.profiles.rig_loader import load_rig

    return load_rig(Path(__file__).resolve().parents[2] / "rigs" / f"{name}.toml")


def _items(*pairs: tuple[str, str]):
    from rigplane.profiles.probe_checks import ProbeRestoreItem

    return tuple(ProbeRestoreItem(read=r, write=w) for r, w in pairs)


def test_both_vfos_come_from_restore_targets_not_distinct_reads() -> None:
    from rigplane.profiles.probe_checks import restored_settings

    selected = _items(("get_freq", "set_freq"), ("get_mode", "set_mode"))
    full = selected + _items(
        ("get_unselected_freq", "set_unselected_freq"),
        ("get_unselected_mode", "set_unselected_mode"),
    )
    for name in ("ic7300", "ic705", "ic7610"):
        config = _config(name)
        args = (config.commands, config.protocol_type)
        for items, refused in ((selected, True), (full[:3], True), (full, False)):
            why = _refusal("civ", b"\x07\xa0", restored=restored_settings(items, *args))
            assert bool(why) is refused, (name, len(items))
