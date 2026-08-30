"""IC-705 TOML profile tests — pin what ``rigs/ic705.toml`` must (not) declare."""

from __future__ import annotations

from pathlib import Path

import pytest

from rigplane.rig_loader import load_rig

RIGS_DIR = Path(__file__).resolve().parent.parent / "rigs"
IC705_PATH = RIGS_DIR / "ic705.toml"


@pytest.fixture()
def rig():
    return load_rig(IC705_PATH)


@pytest.fixture()
def cmdmap(rig):
    return rig.to_command_map()


class TestNoStrayCivOutputKeys:
    """``get_civ_output``/``set_civ_output`` (bare spelling) are dead keys.

    No builder in ``src/rigplane/commands/`` resolves this spelling; the
    only reachable builder for 0x1C 0x04 is ``get_civ_output_ant`` /
    ``set_civ_output_ant`` (``src/rigplane/commands/config.py``). Per the
    IC-705 CI-V Reference Guide (A7560-8EX-1, Jul.2020) p.8, IC-705 has no
    ANT-output CI-V setting at all, so this pins the bare keys' absence
    rather than asserting anything about the ``_ant`` spelling.
    """

    def test_bare_civ_output_keys_absent(self, cmdmap) -> None:
        assert not cmdmap.has("get_civ_output")
        assert not cmdmap.has("set_civ_output")

    def test_neighboring_key_still_present(self, cmdmap) -> None:
        """Discrimination guard: the map is loaded and non-empty."""
        assert cmdmap.has("get_tx_freq_monitor")
