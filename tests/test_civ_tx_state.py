"""Raw CI-V TX-state helper checks (MOR-3839).

Covers scripts/bench/civ_tx_state.py frame building, reply parsing and the
unkey-if-keyed flow as pure functions with a stubbed transport: no serial
port, no radio, no simulator.
"""

import importlib.util
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
HELPER = REPO_ROOT / "scripts" / "bench" / "civ_tx_state.py"


def load_helper():
    spec = importlib.util.spec_from_file_location("civ_tx_state", HELPER)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_read_frame_format():
    helper = load_helper()
    assert helper.read_frame(0x94) == bytes(
        (0xFE, 0xFE, 0x94, 0xE0, 0x1C, 0x00, 0xFD)
    )


def test_unkey_frame_format():
    helper = load_helper()
    assert helper.unkey_frame(0x94) == bytes(
        (0xFE, 0xFE, 0x94, 0xE0, 0x1C, 0x00, 0x00, 0xFD)
    )


def test_parse_rx_and_tx():
    helper = load_helper()
    rx = bytes((0xFE, 0xFE, 0xE0, 0x94, 0x1C, 0x00, 0x00, 0xFD))
    tx = bytes((0xFE, 0xFE, 0xE0, 0x94, 0x1C, 0x00, 0x01, 0xFD))
    assert helper.parse_tx_state(rx, 0x94) == 0
    assert helper.parse_tx_state(tx, 0x94) == 1


def test_parse_no_answer_and_garbage():
    helper = load_helper()
    assert helper.parse_tx_state(b"", 0x94) is None
    assert helper.parse_tx_state(bytes((0xFE, 0xFE, 0xE0, 0x94, 0x1C)), 0x94) is None
    assert helper.parse_tx_state(b"not a CI-V reply", 0x94) is None


def test_parse_ignores_other_addresses():
    helper = load_helper()
    tx94 = bytes((0xFE, 0xFE, 0xE0, 0x94, 0x1C, 0x00, 0x01, 0xFD))
    assert helper.parse_tx_state(tx94, 0x88) is None


def test_unkey_if_keyed_unkeys_and_rereads(monkeypatch, capsys):
    helper = load_helper()
    tx = bytes((0xFE, 0xFE, 0xE0, 0x94, 0x1C, 0x00, 0x01, 0xFD))
    rx = bytes((0xFE, 0xFE, 0xE0, 0x94, 0x1C, 0x00, 0x00, 0xFD))
    written = []
    replies = [tx, b"", rx]

    def fake_exchange(port, addr, baud, frame):
        written.append(bytes(frame))
        return replies.pop(0)

    monkeypatch.setattr(helper, "_exchange", fake_exchange)
    assert (
        helper.main(["--port", "COM3", "--addr", "94", "--unkey-if-keyed"]) == 0
    )
    assert written == [
        helper.read_frame(0x94),
        helper.unkey_frame(0x94),
        helper.read_frame(0x94),
    ]
    assert "state byte: 0" in capsys.readouterr().out


def test_tx_without_unkey_flag_is_failure(monkeypatch):
    helper = load_helper()
    tx = bytes((0xFE, 0xFE, 0xE0, 0x94, 0x1C, 0x00, 0x01, 0xFD))
    monkeypatch.setattr(
        helper, "_exchange", lambda port, addr, baud, frame: tx
    )
    assert helper.main(["--port", "COM3", "--addr", "94"]) == 1


def test_invalid_addr_is_rejected(capsys):
    helper = load_helper()
    with pytest.raises(SystemExit) as exc:
        helper.main(["--port", "COM3", "--addr", "zz"])
    assert exc.value.code == 2
    assert "hex" in capsys.readouterr().err
