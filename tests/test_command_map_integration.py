"""CommandMap integration tests — IC-7610 parity plus custom-map cases.

The parity classes below build one command with the IC-7610 TOML's cmd_map
and again without it, and require the two frames to match; the override
class instead feeds a hand-made CommandMap and checks that the frame
follows its wire bytes.

Parity here is a property of these builders at these arguments, not of the
package. For IC-7610 alone, tests/command_map_parity_divergences.txt
records builders whose two frames differ; a builder asserted equal below
can still be listed there at arguments this file does not use. No dsp.py
builder is listed as of MOR-1986 — get_attenuator, get_preamp, get_af_mute
and get_digisel were, at command29=False only, until the cmd_map branch was
made to forward that argument.

tests/test_command_map_parity.py generalises the parity cases below: every
builder it can reach, every profile in rigs/, and one probe per optional
argument. It does not replace this file — the custom-map cases have no
counterpart there. What it could not compare is listed in
tests/command_map_parity_uncovered.txt; check there before assuming a
builder named below is covered only here.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from rigplane import commands, IC_7610_ADDR
from rigplane.command_map import CommandMap
from rigplane.profiles.rig_loader import discover_rigs
from rigplane.rig_loader import load_rig
from _command_test_helpers import bind_default_addr_globals

RIG_DIR = Path(__file__).resolve().parents[1] / "rigs"

bind_default_addr_globals(globals(), to_addr=IC_7610_ADDR)


@pytest.fixture()
def cmd_map():
    rig = load_rig(RIG_DIR / "ic7610.toml")
    return rig.to_command_map()


# ── Profile parity: getters (no data) ──────────────────────────


class TestGetterParity:
    """These getters must build the same frame with IC-7610 cmd_map as without."""

    def test_get_frequency(self, cmd_map):
        assert commands.get_freq(cmd_map=cmd_map) == commands.get_freq()

    def test_get_mode(self, cmd_map):
        assert commands.get_mode(cmd_map=cmd_map) == commands.get_mode()

    def test_get_af_level(self, cmd_map):
        assert commands.get_af_level(cmd_map=cmd_map) == commands.get_af_level()

    def test_get_rf_gain(self, cmd_map):
        assert commands.get_rf_gain(cmd_map=cmd_map) == commands.get_rf_gain()

    def test_get_power(self, cmd_map):
        assert commands.get_rf_power(cmd_map=cmd_map) == commands.get_rf_power()

    def test_get_s_meter(self, cmd_map):
        assert commands.get_s_meter(cmd_map=cmd_map) == commands.get_s_meter()

    def test_get_swr(self, cmd_map):
        assert commands.get_swr(cmd_map=cmd_map) == commands.get_swr()

    def test_get_alc(self, cmd_map):
        assert commands.get_alc(cmd_map=cmd_map) == commands.get_alc()

    def test_get_tuning_step(self, cmd_map):
        assert commands.get_tuning_step(cmd_map=cmd_map) == commands.get_tuning_step()

    def test_get_nb(self, cmd_map):
        assert commands.get_nb(cmd_map=cmd_map) == commands.get_nb()

    def test_get_nr(self, cmd_map):
        assert commands.get_nr(cmd_map=cmd_map) == commands.get_nr()

    def test_get_ip_plus(self, cmd_map):
        assert commands.get_ip_plus(cmd_map=cmd_map) == commands.get_ip_plus()

    def test_get_power_meter(self, cmd_map):
        assert commands.get_power_meter(cmd_map=cmd_map) == commands.get_power_meter()

    def test_get_transceiver_id(self, cmd_map):
        assert (
            commands.get_transceiver_id(cmd_map=cmd_map)
            == commands.get_transceiver_id()
        )

    def test_get_band_edge_freq(self, cmd_map):
        assert (
            commands.get_band_edge_freq(cmd_map=cmd_map)
            == commands.get_band_edge_freq()
        )

    def test_scope_on(self, cmd_map):
        assert commands.scope_on(cmd_map=cmd_map) == commands.scope_on()

    def test_scope_off(self, cmd_map):
        assert commands.scope_off(cmd_map=cmd_map) == commands.scope_off()


# ── Profile parity: setters (with data) ────────────────────────


class TestSetterParity:
    """These setters must build the same frame with IC-7610 cmd_map as without."""

    def test_set_frequency(self, cmd_map):
        assert commands.set_freq(14_200_000, cmd_map=cmd_map) == commands.set_freq(
            14_200_000
        )

    def test_set_power(self, cmd_map):
        assert commands.set_rf_power(128, cmd_map=cmd_map) == commands.set_rf_power(128)

    def test_set_af_level(self, cmd_map):
        assert commands.set_af_level(200, cmd_map=cmd_map) == commands.set_af_level(200)

    def test_set_rf_gain(self, cmd_map):
        assert commands.set_rf_gain(128, cmd_map=cmd_map) == commands.set_rf_gain(128)

    def test_set_squelch(self, cmd_map):
        assert commands.set_squelch(64, cmd_map=cmd_map) == commands.set_squelch(64)

    def test_set_tuning_step(self, cmd_map):
        assert commands.set_tuning_step(3, cmd_map=cmd_map) == commands.set_tuning_step(
            3
        )

    def test_ptt_on(self, cmd_map):
        assert commands.ptt_on(cmd_map=cmd_map) == commands.ptt_on()

    def test_ptt_off(self, cmd_map):
        assert commands.ptt_off(cmd_map=cmd_map) == commands.ptt_off()

    def test_power_on(self, cmd_map):
        assert commands.power_on(cmd_map=cmd_map) == commands.power_on()

    def test_power_off(self, cmd_map):
        assert commands.power_off(cmd_map=cmd_map) == commands.power_off()

    def test_stop_cw(self, cmd_map):
        assert commands.stop_cw(cmd_map=cmd_map) == commands.stop_cw()

    def test_start_scan(self, cmd_map):
        assert commands.scan_start(cmd_map=cmd_map) == commands.scan_start()

    def test_stop_scan(self, cmd_map):
        assert commands.scan_stop(cmd_map=cmd_map) == commands.scan_stop()


# ── PTT wire-tuple contract: every CI-V profile ─────────────────


class TestPttWireContractAcrossProfiles:
    """MOR-2002 step 2b-ptt (Q7, ``docs/plans/2026-08-29-profile-driven-
    command-bytes.md`` §8.1): a ``[commands]`` tuple carries the full
    constant prefix, payload byte included. Before this fix,
    ``rigs/ic705.toml``, ``rigs/ic7300.toml``, ``rigs/ic7610.toml``,
    ``rigs/ic9700.toml`` and ``rigs/x6200.toml`` held a 2-byte
    ``ptt_on``/``ptt_off`` tuple and the ``cmd_map`` branch appended the
    payload byte itself; ``rigs/x6100.toml`` already held the 3-byte tuple,
    so its map branch doubled the payload
    (``tests/command_map_parity_divergences.txt``, X6100 rows). This
    sweeps every CI-V profile in ``rigs/`` that declares
    ``ptt_on``/``ptt_off`` and requires the map branch and the fallback
    branch to build the identical frame, ending in the explicit payload
    byte.
    """

    @staticmethod
    def _civ_ptt_maps() -> dict[str, CommandMap]:
        maps: dict[str, CommandMap] = {}
        for model, config in sorted(discover_rigs(RIG_DIR).items()):
            cmd_map = config.to_command_map()
            if cmd_map.has("ptt_on") and cmd_map.has("ptt_off"):
                maps[model] = cmd_map
        return maps

    def test_at_least_one_civ_profile_declares_ptt(self) -> None:
        assert self._civ_ptt_maps()

    def test_ptt_on_identical_to_fallback_on_every_civ_profile(self) -> None:
        for model, cmd_map in self._civ_ptt_maps().items():
            mapped = commands.ptt_on(cmd_map=cmd_map)
            fallback = commands.ptt_on()
            assert mapped == fallback, model
            assert mapped.endswith(b"\x1c\x00\x01\xfd"), model

    def test_ptt_off_identical_to_fallback_on_every_civ_profile(self) -> None:
        for model, cmd_map in self._civ_ptt_maps().items():
            mapped = commands.ptt_off(cmd_map=cmd_map)
            fallback = commands.ptt_off()
            assert mapped == fallback, model
            assert mapped.endswith(b"\x1c\x00\x00\xfd"), model


# ── Helper-delegating functions ─────────────────────────────────


class TestHelperCallerParity:
    """These _build_*-delegating functions must match with cmd_map."""

    def test_get_apf_type_level(self, cmd_map):
        assert (
            commands.get_apf_type_level(cmd_map=cmd_map)
            == commands.get_apf_type_level()
        )

    def test_get_nr_level(self, cmd_map):
        assert commands.get_nr_level(cmd_map=cmd_map) == commands.get_nr_level()

    def test_get_nb_level(self, cmd_map):
        assert commands.get_nb_level(cmd_map=cmd_map) == commands.get_nb_level()

    def test_get_agc(self, cmd_map):
        assert commands.get_agc(cmd_map=cmd_map) == commands.get_agc()

    def test_get_compressor(self, cmd_map):
        assert commands.get_compressor(cmd_map=cmd_map) == commands.get_compressor()

    def test_get_monitor(self, cmd_map):
        assert commands.get_monitor(cmd_map=cmd_map) == commands.get_monitor()

    def test_get_vox(self, cmd_map):
        assert commands.get_vox(cmd_map=cmd_map) == commands.get_vox()

    def test_get_break_in(self, cmd_map):
        assert commands.get_break_in(cmd_map=cmd_map) == commands.get_break_in()

    def test_get_dial_lock(self, cmd_map):
        assert commands.get_dial_lock(cmd_map=cmd_map) == commands.get_dial_lock()

    def test_get_filter_shape(self, cmd_map):
        assert commands.get_filter_shape(cmd_map=cmd_map) == commands.get_filter_shape()

    def test_get_ref_adjust(self, cmd_map):
        assert commands.get_ref_adjust(cmd_map=cmd_map) == commands.get_ref_adjust()

    def test_get_s_meter_sql_status(self, cmd_map):
        assert (
            commands.get_s_meter_sql_status(cmd_map=cmd_map)
            == commands.get_s_meter_sql_status()
        )

    def test_get_agc_time_constant(self, cmd_map):
        assert (
            commands.get_agc_time_constant(cmd_map=cmd_map)
            == commands.get_agc_time_constant()
        )


# ── cmd29-aware functions ───────────────────────────────────────


class TestCmd29Parity:
    """These cmd29-framing functions must match with cmd_map at command29's default.

    ``get_attenuator``, ``get_preamp``, ``get_digisel`` and ``get_af_mute``
    used to match only at that default, because the cmd_map branch passed a
    hardcoded ``command29=True`` while the fallback honoured the argument.
    MOR-1986 made the branch forward it, so they now match at
    ``command29=False`` too. The ``_without_cmd29`` tests below name that
    property at the point of use; they are not its only guard.
    ``test_command_map_parity.py`` probes every optional argument at a
    non-default value, so it reaches ``command29=False`` on every profile --
    regressing this fails those four tests and both of that file's baseline
    tests, six in all.
    """

    def test_get_attenuator(self, cmd_map):
        assert commands.get_attenuator(cmd_map=cmd_map) == commands.get_attenuator()

    def test_get_preamp(self, cmd_map):
        assert commands.get_preamp(cmd_map=cmd_map) == commands.get_preamp()

    def test_get_digisel(self, cmd_map):
        assert commands.get_digisel(cmd_map=cmd_map) == commands.get_digisel()

    def test_get_af_mute(self, cmd_map):
        assert commands.get_af_mute(cmd_map=cmd_map) == commands.get_af_mute()

    def test_get_attenuator_without_cmd29(self, cmd_map):
        assert commands.get_attenuator(
            cmd_map=cmd_map, command29=False
        ) == commands.get_attenuator(command29=False)

    def test_get_preamp_without_cmd29(self, cmd_map):
        assert commands.get_preamp(
            cmd_map=cmd_map, command29=False
        ) == commands.get_preamp(command29=False)

    def test_get_digisel_without_cmd29(self, cmd_map):
        assert commands.get_digisel(
            cmd_map=cmd_map, command29=False
        ) == commands.get_digisel(command29=False)

    def test_get_af_mute_without_cmd29(self, cmd_map):
        assert commands.get_af_mute(
            cmd_map=cmd_map, command29=False
        ) == commands.get_af_mute(command29=False)

    def test_get_audio_peak_filter(self, cmd_map):
        assert (
            commands.get_audio_peak_filter(cmd_map=cmd_map)
            == commands.get_audio_peak_filter()
        )

    def test_get_auto_notch(self, cmd_map):
        assert commands.get_auto_notch(cmd_map=cmd_map) == commands.get_auto_notch()

    def test_get_manual_notch(self, cmd_map):
        assert commands.get_manual_notch(cmd_map=cmd_map) == commands.get_manual_notch()

    def test_get_twin_peak_filter(self, cmd_map):
        assert (
            commands.get_twin_peak_filter(cmd_map=cmd_map)
            == commands.get_twin_peak_filter()
        )

    def test_get_various_squelch(self, cmd_map):
        assert (
            commands.get_various_squelch(cmd_map=cmd_map)
            == commands.get_various_squelch()
        )


# ── Custom CommandMap (different wire bytes) ────────────────────


class TestCommandMapOverride:
    """A hand-made CommandMap's wire bytes must show up in the frame it builds."""

    def test_different_wire_bytes_produce_different_frame(self):
        custom = CommandMap({"get_af_level": (0x16, 0x43)})
        result = commands.get_af_level(cmd_map=custom)
        hardcoded = commands.get_af_level()
        assert result != hardcoded
        assert b"\x16\x43" in result

    def test_custom_single_byte_command(self):
        custom = CommandMap({"get_freq": (0xFF,)})
        result = commands.get_freq(cmd_map=custom)
        assert b"\xff" in result
        assert result != commands.get_freq()

    def test_custom_setter(self):
        custom = CommandMap({"set_rf_power": (0x14, 0xFF)})
        result = commands.set_rf_power(128, cmd_map=custom)
        hardcoded = commands.set_rf_power(128)
        assert result != hardcoded
        assert b"\x14\xff" in result

    def test_four_byte_wire_extended_sub(self):
        """IC-7300 style: 4-byte wire like [0x1A, 0x05, 0x00, 0x64].

        Bytes 0-1 are command+sub, bytes 2+ are prepended to data payload.
        """
        custom = CommandMap({"get_acc1_mod_level": (0x1A, 0x05, 0x00, 0x64)})
        result = commands.get_acc1_mod_level(cmd_map=custom)
        # Frame: FE FE <to> <from> 1A 05 00 64 FD
        assert b"\x1a\x05\x00\x64" in result
        assert result.startswith(b"\xfe\xfe")
        assert result.endswith(b"\xfd")

    def test_four_byte_wire_with_set_data(self):
        """4-byte wire + data: extra wire bytes prepend to data."""
        custom = CommandMap({"set_acc1_mod_level": (0x1A, 0x05, 0x00, 0x64)})
        result = commands.set_acc1_mod_level(128, cmd_map=custom)
        # Frame: FE FE <to> <from> 1A 05 00 64 <level_bcd> FD
        assert b"\x1a\x05\x00\x64" in result
        # The level data should follow the extended sub-command bytes
        idx = result.index(b"\x00\x64")
        assert idx + 2 < len(result) - 1  # there's data after 00 64 before FD

    def test_three_byte_wire_trailing_byte_is_constant_not_addressing(self):
        """Q7 (docs/plans/2026-08-29-profile-driven-command-bytes.md §8.1):
        a tuple's trailing byte can be a constant payload or selector byte,
        not only extended menu addressing — the shape of
        rigs/x6100.toml's ptt_on = [0x1C, 0x00, 0x01]. It must still land
        in the frame even though this getter passes no data of its own,
        so a future change that truncated the tuple at the sub-command
        would be caught here.
        """
        custom = CommandMap({"get_acc1_mod_level": (0x1C, 0x00, 0x01)})
        result = commands.get_acc1_mod_level(cmd_map=custom)
        # Frame: FE FE <to> <from> 1C 00 01 FD
        assert b"\x1c\x00\x01" in result
        assert result.startswith(b"\xfe\xfe")
        assert result.endswith(b"\xfd")
