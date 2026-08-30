"""Tests for D1 (plan §4 Step 4,
``docs/plans/2026-08-29-profile-driven-command-bytes.md`` §8.1): what the
command path does for a command name a profile does not declare, with the
same behaviour in development and production -- the only asymmetry lives
in ``tests/test_profile_command_coverage.py``, never here.

1. Declared -> the profile's bytes, unchanged; pinned by
   ``tests/test_profile_command_binding.py``, not repeated here.
2. Declared absent (``RadioProfile.absent_command_names``) -> refuse with
   ``CommandError`` naming the recorded source. Never a bare ``KeyError``
   and no log line -- a confirmed fact needs no warning.
3. Neither declared nor declared absent -> the same shape of refusal, plus
   a WARNING through the caller-supplied ``on_undeclared`` hook. Must not
   exist at release; this file pins what happens if reached anyway.

Both surfaces ``commands/bound.py: BoundCommands`` exposes are covered:
``__getattr__`` (a bound builder reached via attribute access, then
called) and ``expect`` (the reply-matching half).
"""

from __future__ import annotations

import dataclasses
import logging
import pathlib

import pytest

from rigplane.commands import get_freq, get_speech, ptt_on
from rigplane.commands.bound import BoundCommands
from rigplane.commands.command_map import CommandMap
from rigplane.core.exceptions import CommandError
from rigplane.profiles.rig_loader import discover_rigs
from rigplane.runtime.radio import CoreRadio

REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]
RIGS_DIR = REPO_ROOT / "rigs"

# ``get_powerstat`` is a real, single-argument-set public builder
# (``power.py: get_powerstat``) that no current CI-V profile declares or
# records absent -- confirmed by ``tests/test_profile_command_coverage.py``
# recording it as a universal gap. Using a real, un-synthesised name here
# means this file exercises the same undeclared surface the coverage guard
# tracks, rather than a name invented only for this test.
_UNIVERSALLY_UNDECLARED = "get_powerstat"


class TestDeclaredAbsentRefusal:
    """State 2: declared absent, via ``absent_command_names``."""

    def test_getattr_call_refuses_not_keyerror(self) -> None:
        bound = BoundCommands(
            CommandMap({}), {"ptt_on": "IC-9700 Full Manual, no PTT-on item"}
        )
        with pytest.raises(CommandError, match="not supported by this radio"):
            bound.ptt_on(to_addr=0x94)

    def test_refusal_names_the_recorded_source(self) -> None:
        bound = BoundCommands(
            CommandMap({}), {"ptt_on": "IC-9700 Full Manual, no PTT-on item"}
        )
        with pytest.raises(CommandError, match="IC-9700 Full Manual"):
            bound.ptt_on(to_addr=0x94)

    def test_expect_refuses_not_keyerror_and_names_the_source(self) -> None:
        bound = BoundCommands(
            CommandMap({}), {"ptt_on": "IC-9700 Full Manual, no PTT-on item"}
        )
        with pytest.raises(CommandError, match="IC-9700 Full Manual"):
            bound.expect(ptt_on)

    def test_no_warning_is_logged(self) -> None:
        """D1: state 2 is 'not log-and-continue' -- a confirmed fact needs
        no warning, so the ``on_undeclared`` hook must not fire for it."""
        seen: list[str] = []
        bound = BoundCommands(
            CommandMap({}), {"ptt_on": "src"}, on_undeclared=seen.append
        )
        with pytest.raises(CommandError):
            bound.ptt_on(to_addr=0x94)
        assert seen == []


class TestUnknownRefusal:
    """State 3: neither declared nor declared absent."""

    def test_getattr_call_refuses_not_keyerror(self) -> None:
        bound = BoundCommands(CommandMap({}))
        with pytest.raises(CommandError, match="not supported by this radio"):
            bound.ptt_on(to_addr=0x94)

    def test_expect_refuses_not_keyerror(self) -> None:
        bound = BoundCommands(CommandMap({}))
        with pytest.raises(CommandError, match="not supported by this radio"):
            bound.expect(ptt_on)

    def test_on_undeclared_hook_fires_with_the_command_name(self) -> None:
        seen: list[str] = []
        bound = BoundCommands(CommandMap({}), on_undeclared=seen.append)
        with pytest.raises(CommandError):
            bound.ptt_on(to_addr=0x94)
        assert seen == ["ptt_on"]

    def test_no_hook_supplied_still_refuses(self) -> None:
        """``commands/`` performs no I/O of its own (LAYER.md): omitting
        ``on_undeclared`` must not stop the refusal, only its side effect."""
        bound = BoundCommands(CommandMap({}))
        with pytest.raises(CommandError):
            bound.ptt_on(to_addr=0x94)

    def test_get_speech_per_map_probe_still_refuses_via_getattr(self) -> None:
        """``get_speech``'s key is a function of the map (neither
        ``set_speech`` nor ``get_speech`` present here) -- the miss must
        still classify as a refusal, not a bare ``KeyError`` from whichever
        key it tried."""
        bound = BoundCommands(CommandMap({}))
        with pytest.raises(CommandError, match="not supported by this radio"):
            bound.get_speech(to_addr=0x94)

    def test_get_speech_per_map_probe_still_refuses_via_expect(self) -> None:
        """Same as above, through ``expect`` -- which already knows the key
        via ``get_speech.cmd_map_key`` rather than needing to call and
        catch, so it exercises the pre-check branch in ``expect`` instead
        of the ``__getattr__`` wrapper's catch-and-classify branch."""
        bound = BoundCommands(CommandMap({}))
        with pytest.raises(CommandError, match="not supported by this radio"):
            bound.expect(get_speech)


class TestCoreRadioWiring:
    """Integration: ``CoreRadio.__init__`` wires the profile's absent
    sources and a logging hook into ``BoundCommands`` -- not just the
    ``BoundCommands`` unit in isolation."""

    @staticmethod
    def _ic7300_profile():
        config = discover_rigs(RIGS_DIR)["IC-7300"]
        return config.to_profile()

    def test_declared_absent_source_reaches_the_refusal(self) -> None:
        profile = self._ic7300_profile()
        assert "set_agc" in profile.command_names, (
            "fixture assumption: IC-7300 declares set_agc"
        )
        assert profile.command_map is not None and profile.command_map.has("set_agc")
        stripped_map = CommandMap(
            {
                name: profile.command_map.get(name)
                for name in profile.command_map
                if name != "set_agc"
            }
        )
        synthetic = dataclasses.replace(
            profile,
            command_names=profile.command_names - {"set_agc"},
            absent_command_names=profile.absent_command_names | {"set_agc"},
            absent_command_sources={
                **profile.absent_command_sources,
                "set_agc": "test double -- not a real manual citation",
            },
            command_map=stripped_map,
        )
        radio = CoreRadio("127.0.0.1", profile=synthetic)
        with pytest.raises(CommandError, match="test double"):
            radio._commands.set_agc(0, to_addr=0x94)  # noqa: SLF001

    def test_state_three_logs_a_warning(self, caplog: pytest.LogCaptureFixture) -> None:
        profile = self._ic7300_profile()
        assert _UNIVERSALLY_UNDECLARED not in profile.command_names
        assert _UNIVERSALLY_UNDECLARED not in profile.absent_command_names
        radio = CoreRadio("127.0.0.1", profile=profile)
        with caplog.at_level(logging.WARNING, logger="rigplane.runtime.radio"):
            with pytest.raises(CommandError, match="not supported by this radio"):
                radio._commands.get_powerstat(to_addr=0x94)  # noqa: SLF001
        assert _UNIVERSALLY_UNDECLARED in caplog.text
        assert "not recorded as absent" in caplog.text

    def test_declared_command_is_unaffected(self) -> None:
        """Sanity check: state 1 (declared) is untouched by this wiring."""
        profile = self._ic7300_profile()
        assert "get_freq" in profile.command_names
        radio = CoreRadio("127.0.0.1", profile=profile)
        assert radio._commands.get_freq(to_addr=0x94) == get_freq(  # noqa: SLF001
            to_addr=0x94, cmd_map=profile.command_map
        )
