"""MOR-1540: IC-705 must not over-declare the ``digisel`` capability.

``rigs/ic705.toml`` declared the ``"digisel"`` capability (0x16 0x4E DIGI-SEL
toggle) with its own comment admitting IC-705 has no 0x16 0x4E command —
only DIGI-SEL Shift (0x14 0x13, ``get_digisel_shift``/``set_digisel_shift``).
Because ``IcomRadio.set_preamp(level>0)`` runs a DIGI-SEL pre-flight gated
purely on ``"digisel" in self.capabilities``, and the CI-V builder for
``get_digisel``/``set_digisel`` unconditionally targets 0x16/0x4E regardless
of whether the profile declares the command, every ``set_preamp(level>0)``
call on IC-705 sent an unanswerable CI-V frame and blocked for a full
timeout.

TDD red-first: written against the unfixed ``rigs/ic705.toml`` (before this
ticket's edit), every test below fails/hangs. After removing "digisel" from
IC-705's declared capabilities, they pass.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from rigplane.radio import IcomRadio
from rigplane.rig_loader import load_rig
from rigplane.runtime._state_queries import build_state_queries
from test_radio import MockTransport

RIGS_DIR = Path(__file__).resolve().parent.parent / "rigs"
IC705_PATH = RIGS_DIR / "ic705.toml"


@pytest.fixture()
def rig():
    return load_rig(IC705_PATH)


@pytest.fixture()
def profile(rig):
    return rig.to_profile()


@pytest.fixture()
def cmdmap(rig):
    return rig.to_command_map()


@pytest.fixture()
def mock_transport() -> MockTransport:
    return MockTransport()


class TestDigiselCapabilityRemoved:
    """Profile-level: "digisel" (0x16 0x4E toggle) is not declared."""

    def test_no_digisel_capability(self, profile) -> None:
        assert "digisel" not in profile.capabilities

    def test_no_digisel_toggle_commands(self, cmdmap) -> None:
        assert not cmdmap.has("get_digisel")
        assert not cmdmap.has("set_digisel")

    def test_digisel_shift_commands_still_present(self, cmdmap) -> None:
        """DIGI-SEL *Shift* (0x14 0x13) is real on IC-705 and must survive."""
        assert cmdmap.has("get_digisel_shift")
        assert cmdmap.has("set_digisel_shift")


class TestPollerSkipsDigiselQuery:
    """Cascade: the per-receiver state-query builder follows the capability."""

    def test_no_0x16_0x4e_query_for_ic705(self, profile) -> None:
        caps = set(profile.capabilities)
        queries = build_state_queries(profile, caps, is_serial=False)
        digisel_queries = [q for q in queries if q[0] == 0x16 and q[1] == 0x4E]
        assert digisel_queries == []


class TestD2DocumentaryCommandBytes:
    """MOR-2016 (D2): commands sourced from the IC-705 CI-V Reference Guide
    (A7560-8EX-1, Jul.2020). Covers the corrected ``civ_transceive`` address
    and the newly-declared BYTES gap rows this pass fills; the declared-
    absent gap rows are pinned separately in
    ``TestIc705DeclaresAbsentCommands`` (tests/test_rig_loader.py).
    """

    def test_civ_transceive_corrected(self, cmdmap) -> None:
        """0131 (guide p.8), not the previous 0112 -- AF Beep/Speech Output
        on this radio (guide p.7) -- or 0129, config.py's removed
        pre-MOR-2006 fallback default, itself wrong here too (guide p.8:
        External Keypad > KEYER setting)."""
        assert cmdmap.get("get_civ_transceive") == (0x1A, 0x05, 0x01, 0x31)
        assert cmdmap.get("set_civ_transceive") == (0x1A, 0x05, 0x01, 0x31)

    def test_get_agc_time_constant(self, cmdmap) -> None:
        assert cmdmap.get("get_agc_time_constant") == (0x1A, 0x04)

    def test_set_agc_time_constant(self, cmdmap) -> None:
        assert cmdmap.get("set_agc_time_constant") == (0x1A, 0x04)

    def test_get_manual_notch_width(self, cmdmap) -> None:
        assert cmdmap.get("get_manual_notch_width") == (0x16, 0x57)

    def test_set_manual_notch_width(self, cmdmap) -> None:
        assert cmdmap.get("set_manual_notch_width") == (0x16, 0x57)

    def test_get_notch_filter(self, cmdmap) -> None:
        assert cmdmap.get("get_notch_filter") == (0x14, 0x0D)

    def test_set_notch_filter(self, cmdmap) -> None:
        assert cmdmap.get("set_notch_filter") == (0x14, 0x0D)

    def test_get_split(self, cmdmap) -> None:
        assert cmdmap.get("get_split") == (0x0F,)

    def test_scan_set_df_span(self, cmdmap) -> None:
        assert cmdmap.get("scan_set_df_span") == (0x0E,)

    def test_get_nb_depth(self, cmdmap) -> None:
        """1A 05 0357, NOT the shared fallback's 0290 (that was IC-7610's
        address) -- that fallback and its `_CTL_MEM_NB_DEPTH` constant were
        deleted in MOR-2006 Steps 5..N module 2."""
        assert cmdmap.get("get_nb_depth") == (0x1A, 0x05, 0x03, 0x57)

    def test_set_nb_depth(self, cmdmap) -> None:
        assert cmdmap.get("set_nb_depth") == (0x1A, 0x05, 0x03, 0x57)

    def test_get_nb_width(self, cmdmap) -> None:
        """1A 05 0358, NOT the shared fallback's 0291 -- that fallback and
        its `_CTL_MEM_NB_WIDTH` constant were deleted in MOR-2006 Steps
        5..N module 2."""
        assert cmdmap.get("get_nb_width") == (0x1A, 0x05, 0x03, 0x58)

    def test_set_nb_width(self, cmdmap) -> None:
        assert cmdmap.get("set_nb_width") == (0x1A, 0x05, 0x03, 0x58)

    def test_get_vox_delay(self, cmdmap) -> None:
        """1A 05 0359, NOT the shared fallback's 0292 -- that builder-side
        fallback and its `_CTL_MEM_VOX_DELAY` constant were deleted in
        MOR-2006 Steps 5..N module 2; 0292 survives only as an RX-parser
        value in runtime/_civ_rx.py for unsolicited frames, not as anything
        any builder can emit."""
        assert cmdmap.get("get_vox_delay") == (0x1A, 0x05, 0x03, 0x59)

    def test_set_vox_delay(self, cmdmap) -> None:
        assert cmdmap.get("set_vox_delay") == (0x1A, 0x05, 0x03, 0x59)


class TestSetPreampSkipsDigiselPreflight:
    """Cascade: set_preamp(level>0) no longer probes DIGI-SEL on IC-705."""

    @pytest.mark.asyncio
    async def test_set_preamp_sends_no_digisel_frame(
        self, mock_transport: MockTransport
    ) -> None:
        radio = IcomRadio("192.168.1.100", model="IC-705", timeout=0.05)
        radio._civ_transport = mock_transport
        radio._ctrl_transport = mock_transport
        radio._connected = True
        try:
            await radio.set_preamp(1)
        finally:
            radio._connected = False

        # No get_digisel pre-flight frame (opcode 0x16 sub 0x4E) may appear
        # anywhere on the wire -- before the fix this frame was sent and the
        # call blocked for the full CI-V timeout waiting on a reply that
        # never comes.
        assert not any(b"\x16\x4e" in pkt for pkt in mock_transport.sent_packets)
