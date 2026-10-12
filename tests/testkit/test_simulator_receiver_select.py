"""Native IC-7610 receiver select (07 D0/D1/D2) in the LAN simulator.

``MockIcomRadio`` answers the MAIN/SUB receiver select natively only with
``dual_receiver=True``: ``07 D0``/``D1`` swap the selected and unselected
receiver fields (frequency, mode, DATA-mode flag, filter) and ``07 D2``
reads the selection. The recorded ``vfo_select`` window of
``tests/fixtures/ic7610/civ_pairs.jsonl`` pins the wire facts (each select
answered FB, the bare read answering 00/01, ``03`` reading the selected
receiver); every dispatch below goes through the simulator's in-process
``_dispatch_civ``, so no echo frames are involved.
"""

from __future__ import annotations

import json
from pathlib import Path

from rigplane_testkit.icom import (
    MockIcomRadio,
    _bcd_decode_freq,
    _bcd_encode_freq,
)

from testkit.ic7610_fakes import ic7610_lan_fake

_FIXTURES = Path(__file__).resolve().parent.parent / "fixtures" / "ic7610"

_FROM_CONTROLLER = 0xE0
_ACK = bytes.fromhex("fefee098fbfd")
_NAK = bytes.fromhex("fefee098fafd")


def _request_reply_pairs() -> list[tuple[bytes, bytes]]:
    """Every client frame of the recorded window with the reply behind it.

    A client frame is followed by its echo frames and then its reply;
    the reply is the last radio frame before the next client frame.
    """
    rows = [
        json.loads(line)
        for line in (_FIXTURES / "civ_pairs.jsonl").read_text().splitlines()
        if line
    ]
    window = [
        row
        for row in rows
        if row.get("transport") == "lan" and row.get("window") == "vfo_select"
    ]
    assert window, "the vfo_select window is recorded"
    pairs: list[tuple[bytes, bytes]] = []
    index = 0
    while index < len(window):
        assert window[index]["dir"] == "c2r", window[index]
        request = bytes.fromhex(window[index]["frame"])
        replies: list[bytes] = []
        index += 1
        while index < len(window) and window[index]["dir"] == "r2c":
            replies.append(bytes.fromhex(window[index]["frame"]))
            index += 1
        assert replies, f"no reply behind {request.hex()}"
        pairs.append((request, replies[-1]))
    return pairs


def _recorded_receiver_frequencies(
    pairs: list[tuple[bytes, bytes]],
) -> tuple[int, int]:
    """MAIN and SUB frequencies decoded from the recorded ``03`` replies.

    The first ``03`` reply reads MAIN; the first ``03`` reply with a
    different value reads SUB (after the ``07 D1`` select); every ``03``
    reply reads one of the two.
    """
    gets = [(request, reply) for request, reply in pairs if request[4] == 0x03]
    assert len(gets) >= 2, "the window reads both receivers"
    main_hz = _bcd_decode_freq(gets[0][1][5:-1])
    sub_hz = next(
        _bcd_decode_freq(reply[5:-1])
        for _, reply in gets[1:]
        if _bcd_decode_freq(reply[5:-1]) != main_hz
    )
    for _, reply in gets:
        assert _bcd_decode_freq(reply[5:-1]) in (main_hz, sub_hz)
    return main_hz, sub_hz


def test_recorded_vfo_select_replies_are_byte_identical() -> None:
    """Every window frame but ``07 C2`` is answered as recorded, natively."""
    pairs = _request_reply_pairs()
    main_hz, sub_hz = _recorded_receiver_frequencies(pairs)
    sim = MockIcomRadio(dual_receiver=True, recorded_answers=None)
    sim._frequency = main_hz  # noqa: SLF001
    sim._unselected_frequency = sub_hz  # noqa: SLF001
    covered: set[tuple[int, bytes]] = set()
    skipped = 0
    for request, expected in pairs:
        assert request[:2] == b"\xfe\xfe" and request[-1:] == b"\xfd"
        cmd, payload = request[4], request[5:-1]
        if cmd == 0x07 and payload == b"\xc2":
            skipped += 1  # no native handler; the recorded table answers it
            continue
        covered.add((cmd, payload))
        assert sim._dispatch_civ(cmd, payload, _FROM_CONTROLLER) == expected  # noqa: SLF001
    assert skipped == 1, "exactly the 07 C2 frame stays on the recorded table"
    assert {(0x03, b""), (0x07, b"\xd0"), (0x07, b"\xd1"), (0x07, b"\xd2")} <= covered


def test_frequency_write_while_sub_selected_lands_on_sub() -> None:
    """05 while SUB is selected moves SUB; MAIN still reads its old value."""
    sim = MockIcomRadio(dual_receiver=True, recorded_answers=None)
    main_before = sim._frequency  # noqa: SLF001
    assert sim._dispatch_civ(0x07, b"\xd1", _FROM_CONTROLLER) == _ACK  # noqa: SLF001
    new_f = 7_200_000
    assert new_f not in (main_before, sim._unselected_frequency)  # noqa: SLF001
    assert sim._dispatch_civ(0x05, _bcd_encode_freq(new_f), _FROM_CONTROLLER) == _ACK  # noqa: SLF001
    assert sim._dispatch_civ(0x07, b"\xd0", _FROM_CONTROLLER) == _ACK  # noqa: SLF001
    assert sim._selected_receiver == 0  # noqa: SLF001
    assert sim._dispatch_civ(0x03, b"", _FROM_CONTROLLER) == (  # noqa: SLF001
        b"\xfe\xfe\xe0\x98\x03" + _bcd_encode_freq(main_before) + b"\xfd"
    )
    assert sim._dispatch_civ(0x25, b"\x01", _FROM_CONTROLLER) == (  # noqa: SLF001
        b"\xfe\xfe\xe0\x98\x25\x01" + _bcd_encode_freq(new_f) + b"\xfd"
    )


def test_mode_write_while_sub_selected_lands_on_sub() -> None:
    """06 while SUB is selected moves SUB; MAIN still reads its old mode."""
    sim = MockIcomRadio(dual_receiver=True, recorded_answers=None)
    main_mode, main_filter = sim._mode, sim._filter  # noqa: SLF001
    assert (main_mode, main_filter) == (0x01, 1), "USB/FIL1 defaults"
    assert sim._dispatch_civ(0x07, b"\xd1", _FROM_CONTROLLER) == _ACK  # noqa: SLF001
    assert sim._dispatch_civ(0x06, bytes((0x00, 0x02)), _FROM_CONTROLLER) == _ACK  # noqa: SLF001
    assert sim._dispatch_civ(0x07, b"\xd0", _FROM_CONTROLLER) == _ACK  # noqa: SLF001
    assert sim._dispatch_civ(0x04, b"", _FROM_CONTROLLER) == (  # noqa: SLF001
        b"\xfe\xfe\xe0\x98\x04" + bytes((main_mode, main_filter)) + b"\xfd"
    )
    assert sim._dispatch_civ(0x26, b"\x01", _FROM_CONTROLLER) == (  # noqa: SLF001
        b"\xfe\xfe\xe0\x98\x26\x01\x00\x00\x02\xfd"
    )


def test_reselecting_current_receiver_swaps_nothing() -> None:
    """07 D0 twice leaves 03 (and the unselected fields) unchanged."""
    sim = MockIcomRadio(dual_receiver=True, recorded_answers=None)
    before = sim._dispatch_civ(0x03, b"", _FROM_CONTROLLER)  # noqa: SLF001
    unselected_before = sim._unselected_frequency  # noqa: SLF001
    for _ in range(2):
        assert sim._dispatch_civ(0x07, b"\xd0", _FROM_CONTROLLER) == _ACK  # noqa: SLF001
        assert sim._selected_receiver == 0  # noqa: SLF001
        assert sim._dispatch_civ(0x03, b"", _FROM_CONTROLLER) == before  # noqa: SLF001
        assert sim._unselected_frequency == unselected_before  # noqa: SLF001


def test_receiver_select_off_by_default() -> None:
    """Without the keyword 07 D1 NAKs (no table) and 03 is unchanged."""
    sim = MockIcomRadio()
    before = sim._dispatch_civ(0x03, b"", _FROM_CONTROLLER)  # noqa: SLF001
    assert sim._dispatch_civ(0x07, b"\xd1", _FROM_CONTROLLER) == _NAK  # noqa: SLF001
    assert sim._dispatch_civ(0x03, b"", _FROM_CONTROLLER) == before  # noqa: SLF001


def test_data_mode_flag_follows_receiver_select() -> None:
    """07 D1 swaps the DATA-mode flag: 26 01 reports MAIN's flag, 26 00 SUB's."""
    sim = MockIcomRadio(dual_receiver=True, recorded_answers=None)
    # MAIN (the selected receiver) DATA flag 01; SUB stays 00.
    assert (
        sim._dispatch_civ(0x26, bytes((0x00, 0x01, 0x01, 1)), _FROM_CONTROLLER)  # noqa: SLF001
        == _ACK
    )
    assert sim._dispatch_civ(0x07, b"\xd1", _FROM_CONTROLLER) == _ACK  # noqa: SLF001
    # SUB is now selected: 26 00 (selected) reports SUB's old flag (00) ...
    assert sim._dispatch_civ(0x26, b"\x00", _FROM_CONTROLLER) == (  # noqa: SLF001
        b"\xfe\xfe\xe0\x98\x26\x00\x01\x00\x01\xfd"
    )
    # ... and 26 01 (MAIN, now unselected) reports the flag we set on MAIN (01).
    assert sim._dispatch_civ(0x26, b"\x01", _FROM_CONTROLLER) == (  # noqa: SLF001
        b"\xfe\xfe\xe0\x98\x26\x01\x01\x01\x01\xfd"
    )


def _recorded_c2_reply() -> bytes:
    """The recorded radio reply to ``07 C2`` from the vfo_select window.

    ``07 C2`` has no native handler, so only the recorded table answers it;
    the expected bytes are read from the same rows the table is built from,
    never hard-coded.
    """
    for request, reply in _request_reply_pairs():
        if request[4] == 0x07 and request[5:-1] == b"\xc2":
            return reply
    raise AssertionError("the vfo_select window has no 07 C2 pair")


def test_c2_answered_from_recorded_table() -> None:
    """07 C2 has no native handler: the LAN fake's table answers it."""
    sim = ic7610_lan_fake(dual_receiver=True)
    assert sim._dispatch_civ(0x07, b"\xc2", _FROM_CONTROLLER) == _recorded_c2_reply()  # noqa: SLF001


def test_c2_naks_without_table() -> None:
    """07 C2 has no native handler: without a table it NAKs."""
    sim = MockIcomRadio(dual_receiver=True, recorded_answers=None)
    assert sim._dispatch_civ(0x07, b"\xc2", _FROM_CONTROLLER) == _NAK  # noqa: SLF001


def test_extra_receiver_select_byte_naks_and_changes_nothing() -> None:
    """07 D0/D1/D2 with a trailing byte NAKs and changes no state."""
    sim = MockIcomRadio(dual_receiver=True, recorded_answers=None)
    freq_before = sim._frequency  # noqa: SLF001
    unselected_before = sim._unselected_frequency  # noqa: SLF001
    for payload in (b"\xd0\x00", b"\xd1\x00", b"\xd2\x00"):
        assert sim._dispatch_civ(0x07, payload, _FROM_CONTROLLER) == _NAK  # noqa: SLF001
    assert sim._selected_receiver == 0  # noqa: SLF001
    assert sim._frequency == freq_before  # noqa: SLF001
    assert sim._unselected_frequency == unselected_before  # noqa: SLF001


def test_lan_fake_dual_receiver_default() -> None:
    """ic7610_lan_fake() models receiver select by default: 07 D2 reads 01."""
    sim = ic7610_lan_fake()
    assert sim._dual_receiver is True  # noqa: SLF001
    assert sim._dispatch_civ(0x07, b"\xd1", _FROM_CONTROLLER) == _ACK  # noqa: SLF001
    assert sim._dispatch_civ(0x07, b"\xd2", _FROM_CONTROLLER) == (  # noqa: SLF001
        b"\xfe\xfe\xe0\x98\x07\xd2\x01\xfd"
    )
