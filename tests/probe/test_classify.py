"""Outcome classes for both probe protocols (MOR-4464 PR A)."""

from __future__ import annotations

from rigplane.commands import CONTROLLER_ADDR, build_civ_frame, build_cmd29_frame
from rigplane.probe.classify import classify_cat, classify_civ

# Full CI-V frames on the wire: FE FE <to> <from> <body> FD.
_NAK = bytes.fromhex("fefee094fa fd".replace(" ", ""))
_ACK = bytes.fromhex("fefee094fbfd")
_FREQ_DATA = bytes.fromhex("fefe e094 030014250700 fd".replace(" ", ""))
_MODE_DATA = bytes.fromhex("fefe e094 0401 fd".replace(" ", ""))


def test_cat_data_refused_silent() -> None:
    assert classify_cat("FA;", "FA014074000;") == "data"
    assert classify_cat("KS;", "?;") == "refused"
    assert classify_cat("KS;", None) == "silent"


def test_cat_echo_quirk_is_data() -> None:
    # The radio answers the SUB S-meter read with the side digit echoed
    # as 0 (rigs/ftx1.toml: get_s_meter_sub).
    assert classify_cat("SM1;", "SM0000;") == "data"


def test_cat_wrong_verb_is_malformed() -> None:
    assert classify_cat("FA;", "MD02;") == "malformed"
    # A late reply to an earlier request that merely shares the verb.
    assert classify_cat("NA1;", "NA00;") == "malformed"


def test_cat_garbled_bytes_are_malformed() -> None:
    assert classify_cat("FA;", "FA014\x074000;") == "malformed"


def test_civ_data_nak_ack_silent() -> None:
    assert classify_civ(bytes((0x03,)), _FREQ_DATA) == "data"
    assert classify_civ(bytes((0x03,)), _NAK) == "refused"
    assert classify_civ(bytes((0x03,)), _ACK) == "ack_only"
    assert classify_civ(bytes((0x03,)), None) == "silent"


def test_civ_bad_frame_is_malformed() -> None:
    assert classify_civ(bytes((0x03,)), b"\xfe\xfe\x00") == "malformed"
    assert classify_civ(bytes((0x03,)), b"not a frame") == "malformed"


def test_civ_reply_to_another_command_is_malformed() -> None:
    assert classify_civ(bytes((0x03,)), _MODE_DATA) == "malformed"


def test_civ_cmd29_wrapper_matches_wrapped_reply() -> None:
    wrapped = build_cmd29_frame(
        CONTROLLER_ADDR,
        0x94,
        0x03,
        data=bytes.fromhex("0014250700"),
        receiver=0x00,
    )
    assert classify_civ(bytes((0x29, 0x00, 0x03)), wrapped) == "data"


def test_civ_unknown_request_accepts_data() -> None:
    assert classify_civ(bytes((0x7F,)), _FREQ_DATA, kind="unknown") == "data"
    assert classify_civ(bytes((0x7F,)), _NAK, kind="unknown") == "refused"


def test_civ_stale_selector_is_malformed() -> None:
    # MOR-4464 finding 3: a reply echoing another menu item is not this
    # read's answer, even though the (command, sub) pair matches.
    stale = build_civ_frame(
        CONTROLLER_ADDR, 0x94, 0x1A, sub=0x05, data=bytes.fromhex("000301")
    )
    assert classify_civ(bytes.fromhex("1a050002"), stale) == "malformed"


def test_civ_wrong_receiver_is_malformed() -> None:
    # MOR-4464 finding 3: a wrapped MAIN reply is not a SUB read's answer.
    main_reply = build_cmd29_frame(
        CONTROLLER_ADDR,
        0x98,
        0x14,
        sub=0x01,
        data=bytes.fromhex("0010"),
        receiver=0x00,
    )
    assert classify_civ(bytes.fromhex("29011401"), main_reply) == "malformed"


def test_civ_plain_reply_to_wrapped_request_is_malformed() -> None:
    # MOR-4464 finding 3: the shared matcher cannot attribute an
    # unwrapped reply to a wrapped read, so classification refuses it.
    plain = build_civ_frame(
        CONTROLLER_ADDR, 0x98, 0x14, sub=0x01, data=bytes.fromhex("0010")
    )
    assert classify_civ(bytes.fromhex("29001401"), plain) == "malformed"
