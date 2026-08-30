"""Tests for Radio.supports_command() — Phase 3 of unified capability gating."""

from __future__ import annotations

import dataclasses
from pathlib import Path

import pytest

from rigplane.backends.yaesu_cat.radio import YaesuCatRadio
from rigplane.radio import CoreRadio
from rigplane.rig_loader import load_rig

_RIGS_DIR = Path(__file__).parents[1] / "rigs"


@pytest.fixture()
def ic7300_profile():
    return load_rig(_RIGS_DIR / "ic7300.toml").to_profile()


@pytest.fixture()
def ic7300_radio(ic7300_profile):
    return CoreRadio("127.0.0.1", profile=ic7300_profile)


# ---------------------------------------------------------------------------
# CoreRadio (base for all Icom LAN + serial backends)
# ---------------------------------------------------------------------------


class TestIcomSupportsCommand:
    """Reconciled against the profile now (MOR-2005 step 4b): these pins
    used to call the method unbound against the bare class
    (``CoreRadio.supports_command(CoreRadio, cmd)``), which worked only
    because the old body never read ``self`` beyond ``_KNOWN_COMMANDS``.
    The reconciled body reads ``self._profile``, so every case here goes
    through a real, profile-bound instance instead.
    """

    def test_known_commands_return_true(self, ic7300_radio):
        known = [
            "get_freq",
            "set_freq",
            "get_mode",
            "set_mode",
            "set_ptt",
            "get_s_meter",
            "set_nb",
            "set_nr",
            "set_agc",
            "set_attenuator",
            "set_preamp",
            "set_filter",
            "send_cw_text",
            "set_key_speed",
            "get_key_speed",
            "enable_scope",
            "disable_scope",
            "get_powerstat",
            "set_powerstat",
            "send_civ",
        ]
        for cmd in known:
            assert ic7300_radio.supports_command(cmd), f"{cmd} should be supported"

    def test_unknown_commands_return_false(self, ic7300_radio):
        unknown = [
            "do_magic",
            "fly_to_moon",
            "get_coffee",
            "set_hyperdrive",
            "",
            "GET_FREQ",
        ]
        for cmd in unknown:
            assert not ic7300_radio.supports_command(cmd), (
                f"{cmd!r} should NOT be supported"
            )

    def test_known_commands_match_public_async_methods(self):
        """Every entry in _KNOWN_COMMANDS must correspond to an actual method."""
        for cmd in CoreRadio._KNOWN_COMMANDS:
            assert hasattr(CoreRadio, cmd), (
                f"_KNOWN_COMMANDS lists {cmd!r} but no such method exists"
            )


class TestSupportsCommandReconciliation:
    """MOR-2005 (2026-08-29 comment): before this, ``supports_command``
    checked only ``_KNOWN_COMMANDS``, disagreeing with the profile both
    ways: TOML keys the literal knows only under a different, runtime-
    method-level name (e.g. ``get_alc`` vs ``get_alc_meter``), and
    literal-known composite ops a profile can never declare (e.g.
    ``capture_scope_frame``). Neither direction is empty for IC-7300 --
    pinned by ``test_reconciliation_directions_are_both_nonempty`` below,
    recomputed rather than hardcoded, since a count would go stale
    silently the next time a rig TOML changes.

    Reconciled: the profile speaks first; the literal is the fallback only
    for a name the profile does not mention either way; a confirmed-absent
    name overrides both. No rig TOML uses the
    ``{ absent = "<source>" }`` spelling yet (plan §8.1 D2 has not filled
    the profiles), so that case is exercised via ``dataclasses.replace``.
    """

    def test_toml_declared_name_the_literal_does_not_know_is_supported(
        self, ic7300_profile, ic7300_radio
    ):
        candidates = ic7300_profile.command_names - CoreRadio._KNOWN_COMMANDS
        assert candidates, "fixture assumption: IC-7300 has such a name"
        name = sorted(candidates)[0]
        assert ic7300_radio.supports_command(name)

    def test_composite_op_never_a_toml_entry_stays_supported(
        self, ic7300_profile, ic7300_radio
    ):
        assert "capture_scope_frame" in CoreRadio._KNOWN_COMMANDS
        assert "capture_scope_frame" not in ic7300_profile.command_names
        assert ic7300_radio.supports_command("capture_scope_frame")

    def test_declared_absent_name_is_not_supported_even_if_literal_claims_it(
        self, ic7300_profile
    ):
        assert "set_agc" in CoreRadio._KNOWN_COMMANDS
        assert "set_agc" in ic7300_profile.command_names
        absent_profile = dataclasses.replace(
            ic7300_profile,
            command_names=ic7300_profile.command_names - {"set_agc"},
            absent_command_names=ic7300_profile.absent_command_names | {"set_agc"},
        )
        radio = CoreRadio("127.0.0.1", profile=absent_profile)
        assert not radio.supports_command("set_agc")

    def test_reconciliation_directions_are_both_nonempty(self, ic7300_profile):
        """Guards the two prior-disagreement counts named in the docstring
        against silently going to zero -- if either direction vanished, the
        reconciliation would have nothing left to prove and the two tests
        above would be exercising an empty case unnoticed."""
        declared_not_known = ic7300_profile.command_names - CoreRadio._KNOWN_COMMANDS
        known_not_declared = CoreRadio._KNOWN_COMMANDS - ic7300_profile.command_names
        assert declared_not_known
        assert known_not_declared


# ---------------------------------------------------------------------------
# YaesuCatRadio
# ---------------------------------------------------------------------------


@pytest.fixture()
def ftx1_config():
    return load_rig(_RIGS_DIR / "ftx1.toml")


@pytest.fixture()
def yaesu_radio(ftx1_config):
    return YaesuCatRadio("/dev/null", profile=ftx1_config)


class TestYaesuSupportsCommand:
    """YaesuCatRadio.supports_command delegates to _has_command."""

    def test_defined_commands_return_true(self, yaesu_radio):
        for cmd in (
            "get_freq",
            "set_freq",
            "get_mode",
            "set_mode",
            "set_ptt",
            "get_s_meter",
            "get_af_level",
            "set_af_level",
        ):
            assert yaesu_radio.supports_command(cmd), (
                f"{cmd} should be supported on FTX-1"
            )

    def test_undefined_commands_return_false(self, yaesu_radio):
        for cmd in ("do_magic", "fly_to_moon", "get_coffee", ""):
            assert not yaesu_radio.supports_command(cmd), (
                f"{cmd!r} should NOT be supported on FTX-1"
            )

    def test_matches_has_command(self, yaesu_radio, ftx1_config):
        """supports_command must agree with _has_command for every TOML key."""
        for name in ftx1_config.commands:
            assert yaesu_radio.supports_command(name) == yaesu_radio._has_command(name)


# ---------------------------------------------------------------------------
# Serial Icom backends (all inherit CoreRadio)
# ---------------------------------------------------------------------------


class TestSerialBackendsSupportsCommand:
    """Serial backends inherit supports_command from CoreRadio."""

    def test_ic7300_serial(self):
        from rigplane.backends.ic7300.serial import Ic7300SerialRadio

        assert hasattr(Ic7300SerialRadio, "supports_command")
        assert Ic7300SerialRadio.supports_command is CoreRadio.supports_command

    def test_ic705_serial(self):
        from rigplane.backends.ic705.serial import Ic705SerialRadio

        assert hasattr(Ic705SerialRadio, "supports_command")
        assert Ic705SerialRadio.supports_command is CoreRadio.supports_command

    def test_ic9700_serial(self):
        from rigplane.backends.ic9700.serial import Ic9700SerialRadio

        assert hasattr(Ic9700SerialRadio, "supports_command")
        assert Ic9700SerialRadio.supports_command is CoreRadio.supports_command

    def test_icom7610_serial(self):
        from rigplane.backends.icom7610.serial import Icom7610SerialRadio

        assert hasattr(Icom7610SerialRadio, "supports_command")
        assert Icom7610SerialRadio.supports_command is CoreRadio.supports_command


# ---------------------------------------------------------------------------
# DspControlCapable structural conformance — issue #1102
# ---------------------------------------------------------------------------


class TestDspControlCapableNotchExtension:
    """Both backends carry the extended notch surface (set/get_notch_filter)."""

    def test_core_radio_exposes_notch_filter_methods(self):
        for name in ("set_notch_filter", "get_notch_filter"):
            assert hasattr(CoreRadio, name), (
                f"CoreRadio must implement {name} (DspControlCapable, #1102)"
            )

    def test_yaesu_cat_radio_exposes_notch_filter_methods(self):
        for name in ("set_notch_filter", "get_notch_filter"):
            assert hasattr(YaesuCatRadio, name), (
                f"YaesuCatRadio must implement {name} (DspControlCapable, #1102)"
            )

    def test_notch_filter_signature_accepts_receiver(self):
        """set/get_notch_filter must accept the receiver kwarg on both backends."""
        import inspect

        for cls in (CoreRadio, YaesuCatRadio):
            for name in ("set_notch_filter", "get_notch_filter"):
                sig = inspect.signature(getattr(cls, name))
                assert "receiver" in sig.parameters, (
                    f"{cls.__name__}.{name} must accept 'receiver' kwarg"
                )
