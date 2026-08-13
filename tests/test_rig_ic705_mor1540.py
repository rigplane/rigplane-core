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
