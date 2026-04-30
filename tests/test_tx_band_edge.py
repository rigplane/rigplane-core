"""Tests for TX band edge support (CI-V command 0x1E, issue #541)."""

from __future__ import annotations

import pytest

from icom_lan.commands import (
    CONTROLLER_ADDR,
)
from icom_lan.commands.tx_band import (
    get_tx_band_count,
    get_tx_band_edge,
    parse_tx_band_count_response,
    parse_tx_band_edge_response,
)
from icom_lan.commands._frame import _CMD_TX_BAND_EDGE
from icom_lan.radio_state import RadioState, TxBandEdge
from icom_lan.types import CivFrame

_RADIO_ADDR = 0x98


# ---------------------------------------------------------------------------
# Command constant
# ---------------------------------------------------------------------------


class TestCmdConstant:
    def test_cmd_tx_band_edge_value(self) -> None:
        assert _CMD_TX_BAND_EDGE == 0x1E


# ---------------------------------------------------------------------------
# Frame builders
# ---------------------------------------------------------------------------


class TestGetTxBandCount:
    def test_frame_structure(self) -> None:
        frame = get_tx_band_count(to_addr=_RADIO_ADDR)
        # FE FE 98 E0 1E 00 FD
        assert frame == bytes([0xFE, 0xFE, 0x98, 0xE0, 0x1E, 0x00, 0xFD])

    def test_custom_from_addr(self) -> None:
        frame = get_tx_band_count(to_addr=_RADIO_ADDR, from_addr=0xA0)
        assert frame == bytes([0xFE, 0xFE, 0x98, 0xA0, 0x1E, 0x00, 0xFD])


class TestGetTxBandEdge:
    def test_band_1(self) -> None:
        frame = get_tx_band_edge(1, to_addr=_RADIO_ADDR)
        # FE FE 98 E0 1E 01 01 FD  (band 1 as BCD = 0x01)
        assert frame == bytes([0xFE, 0xFE, 0x98, 0xE0, 0x1E, 0x01, 0x01, 0xFD])

    def test_band_12(self) -> None:
        frame = get_tx_band_edge(12, to_addr=_RADIO_ADDR)
        # band 12 as BCD = 0x12
        assert frame == bytes([0xFE, 0xFE, 0x98, 0xE0, 0x1E, 0x01, 0x12, 0xFD])

    def test_band_0(self) -> None:
        frame = get_tx_band_edge(0, to_addr=_RADIO_ADDR)
        assert frame == bytes([0xFE, 0xFE, 0x98, 0xE0, 0x1E, 0x01, 0x00, 0xFD])


# ---------------------------------------------------------------------------
# Response parsers
# ---------------------------------------------------------------------------


class TestParseTxBandCount:
    def test_single_digit(self) -> None:
        # BCD byte 0x09 = 9 bands
        assert parse_tx_band_count_response(bytes([0x09])) == 9

    def test_two_digit(self) -> None:
        # BCD byte 0x12 = 12 bands
        assert parse_tx_band_count_response(bytes([0x12])) == 12

    def test_empty_data(self) -> None:
        assert parse_tx_band_count_response(b"") == 0


class TestParseTxBandEdge:
    def test_160m_band(self) -> None:
        """1.8 MHz - 2.0 MHz band edge."""
        from icom_lan.types import bcd_encode

        start = bcd_encode(1_800_000)  # 1.8 MHz
        end = bcd_encode(2_000_000)  # 2.0 MHz
        start_hz, end_hz = parse_tx_band_edge_response(start + end)
        assert start_hz == 1_800_000
        assert end_hz == 2_000_000

    def test_20m_band(self) -> None:
        """14.0 MHz - 14.35 MHz band edge."""
        from icom_lan.types import bcd_encode

        start = bcd_encode(14_000_000)
        end = bcd_encode(14_350_000)
        start_hz, end_hz = parse_tx_band_edge_response(start + end)
        assert start_hz == 14_000_000
        assert end_hz == 14_350_000

    def test_short_data_raises(self) -> None:
        with pytest.raises(ValueError, match="too short"):
            parse_tx_band_edge_response(bytes(9))

    def test_exact_10_bytes(self) -> None:
        from icom_lan.types import bcd_encode

        data = bcd_encode(7_000_000) + bcd_encode(7_300_000)
        assert len(data) == 10
        start_hz, end_hz = parse_tx_band_edge_response(data)
        assert start_hz == 7_000_000
        assert end_hz == 7_300_000


# ---------------------------------------------------------------------------
# TxBandEdge dataclass
# ---------------------------------------------------------------------------


class TestTxBandEdge:
    def test_defaults(self) -> None:
        edge = TxBandEdge()
        assert edge.start_hz == 0
        assert edge.end_hz == 0

    def test_equality(self) -> None:
        a = TxBandEdge(start_hz=14_000_000, end_hz=14_350_000)
        b = TxBandEdge(start_hz=14_000_000, end_hz=14_350_000)
        assert a == b

    def test_inequality(self) -> None:
        a = TxBandEdge(start_hz=14_000_000, end_hz=14_350_000)
        b = TxBandEdge(start_hz=7_000_000, end_hz=7_300_000)
        assert a != b


# ---------------------------------------------------------------------------
# RadioState integration
# ---------------------------------------------------------------------------


class TestRadioStateTxBandEdges:
    def test_default_empty(self) -> None:
        rs = RadioState()
        assert rs.tx_band_edges == []

    def test_to_dict_empty(self) -> None:
        rs = RadioState()
        d = rs.to_dict()
        assert d["tx_band_edges"] == []

    def test_to_dict_with_edges(self) -> None:
        rs = RadioState()
        rs.tx_band_edges.append(TxBandEdge(start_hz=14_000_000, end_hz=14_350_000))
        rs.tx_band_edges.append(TxBandEdge(start_hz=7_000_000, end_hz=7_300_000))
        d = rs.to_dict()
        assert d["tx_band_edges"] == [
            {"start_hz": 14_000_000, "end_hz": 14_350_000},
            {"start_hz": 7_000_000, "end_hz": 7_300_000},
        ]


# ---------------------------------------------------------------------------
# CI-V RX parser integration
# ---------------------------------------------------------------------------


class TestCivRxTxBandEdge:
    """Test that _update_radio_state_from_frame handles 0x1E responses."""

    def _make_frame(
        self, sub: int, data: bytes, from_addr: int = _RADIO_ADDR
    ) -> CivFrame:
        return CivFrame(
            to_addr=CONTROLLER_ADDR,
            from_addr=from_addr,
            command=0x1E,
            sub=sub,
            data=data,
        )

    def test_band_edge_parsed(self) -> None:
        """0x1E 0x01 with 10-byte BCD data should populate tx_band_edges."""
        from icom_lan.types import bcd_encode

        data = bcd_encode(14_000_000) + bcd_encode(14_350_000)
        frame = self._make_frame(sub=0x01, data=data)

        rs = RadioState()
        # Import and call the parser directly
        from icom_lan.runtime._civ_rx import CivRuntime

        # We need a minimal host to test the parser
        from unittest.mock import MagicMock

        host = MagicMock()
        host._radio_state = rs
        host._change_listeners = []
        runtime = CivRuntime(host)

        runtime._update_radio_state_from_frame(frame)

        assert len(rs.tx_band_edges) == 1
        assert rs.tx_band_edges[0].start_hz == 14_000_000
        assert rs.tx_band_edges[0].end_hz == 14_350_000

    def test_no_duplicates(self) -> None:
        """Same edge should not be added twice."""
        from icom_lan.types import bcd_encode

        data = bcd_encode(14_000_000) + bcd_encode(14_350_000)
        frame = self._make_frame(sub=0x01, data=data)

        rs = RadioState()
        from icom_lan.runtime._civ_rx import CivRuntime
        from unittest.mock import MagicMock

        host = MagicMock()
        host._radio_state = rs
        host._change_listeners = []
        runtime = CivRuntime(host)

        runtime._update_radio_state_from_frame(frame)
        runtime._update_radio_state_from_frame(frame)

        assert len(rs.tx_band_edges) == 1

    def test_short_data_ignored(self) -> None:
        """0x1E 0x01 with < 10 bytes should be silently ignored."""
        frame = self._make_frame(sub=0x01, data=bytes(5))

        rs = RadioState()
        from icom_lan.runtime._civ_rx import CivRuntime
        from unittest.mock import MagicMock

        host = MagicMock()
        host._radio_state = rs
        host._change_listeners = []
        runtime = CivRuntime(host)

        runtime._update_radio_state_from_frame(frame)

        assert len(rs.tx_band_edges) == 0


# ---------------------------------------------------------------------------
# __init__.py re-export
# ---------------------------------------------------------------------------


class TestReExport:
    def test_tx_band_symbols_exported(self) -> None:
        from icom_lan import commands

        assert hasattr(commands, "get_tx_band_count")
        assert hasattr(commands, "get_tx_band_edge")
        assert hasattr(commands, "parse_tx_band_count_response")
        assert hasattr(commands, "parse_tx_band_edge_response")
