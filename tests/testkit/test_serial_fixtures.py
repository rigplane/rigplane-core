"""``rigplane_testkit.serial.fixtures``: bridge recording -> civ_pairs rows."""

from __future__ import annotations

import json

from rigplane_testkit.icom.replay import RecordedAnswerTable
from rigplane_testkit.serial.fixtures import extract_window, main

RADIO = 0x94


def _wave(seq: int, seq_max: int) -> str:
    bcd = lambda n: ((n // 10) << 4) | (n % 10)  # noqa: E731
    body = bytes([0x27, 0x00, 0x00, bcd(seq), bcd(seq_max), 0x01, 0x02])
    return (bytes([0xFE, 0xFE, 0xE0, RADIO]) + body + b"\xfd").hex()


def _recording(tmp_path, rows):
    path = tmp_path / "rec.jsonl"
    path.write_text(
        "".join(
            json.dumps({"t": t, "dir": d, "port": "serial", "hex": h}) + "\n"
            for t, d, h in rows
        )
    )
    return path


FREQ_REQ = "fefe94e003fd"
FREQ_REPLY = "fefee094030000071400fd"
MODE_REQ = "fefe94e004fd"
ACK = "fefee094fbfd"
BAD_REQ = "fefe94e07ffd"
NAK = "fefee094fafd"


def test_first_occurrence_pairs_with_replies_and_silence(tmp_path) -> None:
    path = _recording(
        tmp_path,
        [
            (0.1, "c2r", FREQ_REQ),
            (0.2, "r2c", FREQ_REPLY),
            (0.3, "c2r", FREQ_REQ),  # repeat: dropped, with its reply
            (0.4, "r2c", FREQ_REPLY),
            (0.5, "c2r", MODE_REQ),  # never answered
            (0.6, "c2r", BAD_REQ),
            (0.7, "r2c", NAK),
        ],
    )
    out = extract_window(path, "probe", radio_addr=RADIO)
    # An ACK/NAK closes the OLDEST outstanding request, so the silent MODE_REQ
    # takes the NAK and BAD_REQ is left unanswered: silence is only provable
    # by a later data reply.
    assert [(r["dir"], r["frame"]) for r in out.rows] == [
        ("c2r", FREQ_REQ),
        ("r2c", FREQ_REPLY),
        ("c2r", MODE_REQ),
        ("r2c", NAK),
        ("c2r", BAD_REQ),
    ]
    assert not out.echo
    table = RecordedAnswerTable.from_civ_pairs(out.rows, "usb")
    assert table.answer(bytes.fromhex(FREQ_REQ)) == bytes.fromhex(FREQ_REPLY)


def test_echo_is_detected_and_kept_once(tmp_path) -> None:
    path = _recording(
        tmp_path,
        [
            (0.1, "c2r", FREQ_REQ),
            (0.11, "r2c", FREQ_REQ),
            (0.12, "r2c", FREQ_REPLY),
            (0.3, "c2r", FREQ_REQ),
            (0.31, "r2c", FREQ_REQ),
            (0.32, "r2c", FREQ_REPLY),
        ],
    )
    out = extract_window(path, "probe", radio_addr=RADIO)
    assert out.echo
    assert [r["frame"] for r in out.rows] == [FREQ_REQ, FREQ_REQ, FREQ_REPLY]
    assert RecordedAnswerTable.from_civ_pairs(out.rows, "usb").echo


def test_unsolicited_frames_are_summarised_not_paired(tmp_path) -> None:
    broadcast = "fefe0094000000071400fd"  # transceive broadcast, to address 00
    path = _recording(
        tmp_path,
        [
            (0.05, "r2c", broadcast),
            (0.1, "c2r", FREQ_REQ),
            (0.15, "r2c", broadcast),
            (0.2, "r2c", FREQ_REPLY),
        ],
    )
    out = extract_window(path, "probe", radio_addr=RADIO)
    assert [r["frame"] for r in out.rows] == [FREQ_REQ, FREQ_REPLY]
    assert out.unsolicited == [{"frame": broadcast, "first_t": 0.05, "count": 2}]


def test_scope_keeps_whole_sweeps_apart_from_the_pair_rows(tmp_path) -> None:
    rows = [(0.1, "c2r", "fefe94e0271101fd"), (0.15, "r2c", ACK)]
    t = 0.2
    rows.append((t, "r2c", _wave(3, 3)))  # tail of a sweep already running
    for _ in range(3):  # three complete sweeps of 3 frames
        for seq in (1, 2, 3):
            t += 0.01
            rows.append((t, "r2c", _wave(seq, 3)))
    path = _recording(tmp_path, rows)
    out = extract_window(path, "scope", radio_addr=RADIO, scope_sweeps=2)
    assert len(out.scope_rows) == 6  # two whole sweeps, the partial tail skipped
    assert {r["window"] for r in out.scope_rows} == {"scope_scope"}
    assert [r["dir"] for r in out.rows] == ["c2r", "r2c"]  # pairs untouched
    assert out.scope["frames"] == 10
    assert out.scope["sweeps_kept"] == 2


def test_a_wave_packet_between_request_and_reply_does_not_break_the_pair(
    tmp_path,
) -> None:
    path = _recording(
        tmp_path,
        [
            (0.1, "c2r", FREQ_REQ),
            (0.11, "r2c", _wave(1, 1)),  # the stream interleaves with the reply
            (0.12, "r2c", FREQ_REPLY),
        ],
    )
    out = extract_window(path, "poll", radio_addr=RADIO)
    assert [(r["dir"], r["frame"]) for r in out.rows] == [
        ("c2r", FREQ_REQ),
        ("r2c", FREQ_REPLY),
    ]
    table = RecordedAnswerTable.from_civ_pairs(out.rows, "usb")
    assert table.answer(bytes.fromhex(FREQ_REQ)) == bytes.fromhex(FREQ_REPLY)


def test_a_sweep_with_a_lost_or_reordered_packet_is_dropped_whole(tmp_path) -> None:
    rows = []
    t = 0.0
    for seq in (1, 3, 4):  # packet 2 lost
        t += 0.01
        rows.append((t, "r2c", _wave(seq, 4)))
    for seq in (1, 2, 3, 4):  # a clean sweep follows
        t += 0.01
        rows.append((t, "r2c", _wave(seq, 4)))
    for seq in (1, 3, 2, 4):  # reordered
        t += 0.01
        rows.append((t, "r2c", _wave(seq, 4)))
    out = extract_window(_recording(tmp_path, rows), "scope", radio_addr=RADIO)
    assert len(out.scope_rows) == 4 and out.scope["sweeps_kept"] == 1


def test_blocked_frames_are_counted_and_never_rows(tmp_path) -> None:
    path = _recording(
        tmp_path,
        [
            (0.1, "blocked", "fefe94e0050000071400fd"),
            (0.2, "c2r", FREQ_REQ),
            (0.3, "r2c", FREQ_REPLY),
        ],
    )
    out = extract_window(path, "probe", radio_addr=RADIO)
    assert out.blocked_frames == 1
    assert [r["frame"] for r in out.rows] == [FREQ_REQ, FREQ_REPLY]


def test_cli_merges_windows_and_replaces_a_rerun(tmp_path, capsys) -> None:
    first = _recording(tmp_path, [(0.1, "c2r", FREQ_REQ), (0.2, "r2c", FREQ_REPLY)])
    out_dir = tmp_path / "fx"
    for window in ("probe", "readonly_session", "probe"):
        assert (
            main(
                [
                    "extract",
                    "--in",
                    str(first),
                    "--window",
                    window,
                    "--radio-addr",
                    "0x94",
                    "--out-dir",
                    str(out_dir),
                ]
            )
            == 0
        )
    rows = [
        json.loads(x) for x in (out_dir / "civ_pairs.jsonl").read_text().splitlines()
    ]
    assert [r["window"] for r in rows] == ["readonly_session"] * 2 + ["probe"] * 2
    assert capsys.readouterr().out.count('"window"') == 3


def test_pipelined_requests_pair_in_order_and_older_silent_ones_are_marked(
    tmp_path,
) -> None:
    path = _recording(
        tmp_path,
        [
            (0.10, "c2r", MODE_REQ),  # silent: never answered
            (0.11, "c2r", FREQ_REQ),
            (0.12, "c2r", BAD_REQ),
            (0.13, "r2c", FREQ_REPLY),  # closes FREQ_REQ; MODE_REQ is silent
            (0.14, "r2c", NAK),  # closes BAD_REQ (oldest remaining)
        ],
    )
    out = extract_window(path, "poll", radio_addr=RADIO)
    assert [(r["dir"], r["frame"]) for r in out.rows] == [
        ("c2r", MODE_REQ),
        ("c2r", FREQ_REQ),
        ("r2c", FREQ_REPLY),
        ("c2r", BAD_REQ),
        ("r2c", NAK),
    ]
    assert out.unsolicited == []
