"""The send gate of the probe write checks (MOR-4464 B1a).

``judged_frame`` is the one function that builds a frame from a table entry
and judges it with ``parse_frame`` + ``sendable``; load uses it, and the B1b
engine runs it on the bytes it is about to write.
"""

from __future__ import annotations

import pytest

from tests.probe.test_probe_table_closure import table_for


def _judged(name: str, entry: str, form: str, value=None, receiver=None, restored=()):
    from rigplane.profiles.probe_checks import judged_frame

    return judged_frame(
        table_for(name), entry, form, value, receiver, frozenset(restored)
    )


def test_a_covered_write_is_built_and_allowed() -> None:
    assert (
        _judged("ft991a+", "freq_a", "write", 7074000, None, {("freq_a", None)})
        == b"FA007074000;"
    )
    assert (
        _judged("ft991a+", "split", "write", "on", None, {("split", None)}) == b"FT3;"
    )
    freq = _judged(
        "ic705", "freq_selected", "write", 7074000, None, {("freq_selected", None)}
    )
    assert freq == bytes.fromhex("25000040070700")


@pytest.mark.parametrize(
    ("name", "entry", "value", "restored"),
    [
        ("ft991a+", "freq_a", 7074000, {("freq_b", None), ("mode_main", None)}),
        ("ic705", "freq_selected", 7074000, {("freq_unselected", None)}),
        (
            "ic705",
            "vfo_exchange",
            (),
            {("freq_selected", None), ("mode_selected", None)},
        ),
        ("ic7610", "af_level", 1, {("af_level", None)}),
    ],
)
def test_a_write_without_its_restore_is_refused(name, entry, value, restored) -> None:
    receiver = 1 if entry == "af_level" else None
    with pytest.raises(ValueError, match="restore"):
        _judged(name, entry, "write", value, receiver, restored)


def test_reads_need_no_restore() -> None:
    assert _judged("ft991a+", "split", "read") == b"FT;"
    assert _judged("ft991a+", "info", "read") == b"IF;"
    assert _judged("ic7610", "af_level", "read", None, 1) == bytes.fromhex("29011401")


@pytest.mark.parametrize(
    ("name", "entry", "form", "value", "receiver"),
    [
        ("ft991a+", "freq_a", "write", 10**9, None),
        ("ft991a+", "mode_main", "write", "C4FM", None),
        ("ft991a+", "info", "write", "x", None),
        ("ic705", "vfo_exchange", "read", None, None),
        ("ic705", "af_level", "read", None, 1),
        ("ic705", "band", "read", None, None),
    ],
)
def test_frames_outside_the_table_are_refused(
    name, entry, form, value, receiver
) -> None:
    every = {(e.id, r) for e in table_for(name).entries for r in (None, 0, 1)}
    with pytest.raises((ValueError, KeyError)):
        _judged(name, entry, form, value, receiver, every)


@pytest.mark.parametrize(
    ("name", "data"),
    [
        ("ft991a+", b"TX1;"),
        ("ft991a+", b"BS05;"),
        ("ft991a+", b"AC002;"),
        ("ic705", bytes.fromhex("1c0001")),
        ("ic705", bytes.fromhex("2800")),
        ("ic705", bytes.fromhex("0f11")),
        ("ic705", bytes.fromhex("07a0")),
        ("ic7610", bytes.fromhex("29001c0001")),
    ],
)
def test_transmit_memory_and_absent_settings_do_not_parse(
    name: str, data: bytes
) -> None:
    from rigplane.profiles.probe_checks import parse_frame

    assert parse_frame(table_for(name), data) is None
