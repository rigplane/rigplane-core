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
from rigplane.commands import (
    CONTROLLER_ADDR,
    RECEIVER_MAIN,
    _level_bcd_encode,
    build_civ_frame,
    build_cmd29_frame,
)
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
    """These getters must build the same frame with IC-7610 cmd_map as without.

    commands/levels.py migrated onto the bound command map in MOR-2006
    Steps 5..N (module 2): ``get_af_level``, ``get_rf_gain`` and
    ``get_rf_power`` now require ``cmd_map`` -- there is no more "without"
    to compare against, so their three cases below pin the map path
    against the frame the deleted fallback used to build instead
    (``rigs/ic7610.toml`` declares the same wire tuples the fallback did,
    per ``tests/command_map_parity_divergences.txt`` naming no IC-7610 row
    for any of the three).
    """

    def test_get_frequency(self, cmd_map):
        assert commands.get_freq(cmd_map=cmd_map) == commands.get_freq()

    def test_get_mode(self, cmd_map):
        assert commands.get_mode(cmd_map=cmd_map) == commands.get_mode()

    def test_get_af_level(self, cmd_map):
        # IC-7610 declares a cmd29 route for 0x14/0x01, so command29=True
        # (the builder's default) wraps MAIN too (MOR-1543).
        expected = build_cmd29_frame(
            IC_7610_ADDR, CONTROLLER_ADDR, 0x14, sub=0x01, receiver=RECEIVER_MAIN
        )
        assert commands.get_af_level(cmd_map=cmd_map) == expected

    def test_get_rf_gain(self, cmd_map):
        expected = build_cmd29_frame(
            IC_7610_ADDR, CONTROLLER_ADDR, 0x14, sub=0x02, receiver=RECEIVER_MAIN
        )
        assert commands.get_rf_gain(cmd_map=cmd_map) == expected

    def test_get_power(self, cmd_map):
        expected = build_civ_frame(IC_7610_ADDR, CONTROLLER_ADDR, 0x14, sub=0x0A)
        assert commands.get_rf_power(cmd_map=cmd_map) == expected

    def test_get_s_meter(self, cmd_map):
        assert commands.get_s_meter(cmd_map=cmd_map) == commands.get_s_meter()

    def test_get_swr(self, cmd_map):
        assert commands.get_swr(cmd_map=cmd_map) == commands.get_swr()

    def test_get_alc(self, cmd_map):
        assert commands.get_alc(cmd_map=cmd_map) == commands.get_alc()

    def test_get_tuning_step(self, cmd_map):
        # commands/vfo.py migrated onto the bound command map in MOR-2007
        # Steps 5..N (module 3): get_tuning_step now requires cmd_map --
        # pinned against the frame the deleted fallback used to build,
        # same as get_af_level/get_rf_gain/get_rf_power above.
        expected = build_civ_frame(IC_7610_ADDR, CONTROLLER_ADDR, 0x10)
        assert commands.get_tuning_step(cmd_map=cmd_map) == expected

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
        # commands/scope.py migrated onto the bound command map in MOR-2007
        # Steps 5..N (module 5): scope_on now requires cmd_map -- pinned
        # against the frame the deleted fallback used to build.
        expected = build_civ_frame(
            IC_7610_ADDR, CONTROLLER_ADDR, 0x27, sub=0x10, data=b"\x01"
        )
        assert commands.scope_on(cmd_map=cmd_map) == expected

    def test_scope_off(self, cmd_map):
        expected = build_civ_frame(
            IC_7610_ADDR, CONTROLLER_ADDR, 0x27, sub=0x10, data=b"\x00"
        )
        assert commands.scope_off(cmd_map=cmd_map) == expected


# ── Profile parity: setters (with data) ────────────────────────


class TestSetterParity:
    """These setters must build the same frame with IC-7610 cmd_map as without.

    Same MOR-2006 migration as ``TestGetterParity`` above: ``set_rf_power``,
    ``set_af_level``, ``set_rf_gain`` and ``set_squelch`` now require
    ``cmd_map``, so their cases pin the map path against the frame the
    deleted fallback used to build.
    """

    def test_set_frequency(self, cmd_map):
        assert commands.set_freq(14_200_000, cmd_map=cmd_map) == commands.set_freq(
            14_200_000
        )

    def test_set_power(self, cmd_map):
        expected = build_civ_frame(
            IC_7610_ADDR, CONTROLLER_ADDR, 0x14, sub=0x0A, data=_level_bcd_encode(128)
        )
        assert commands.set_rf_power(128, cmd_map=cmd_map) == expected

    def test_set_af_level(self, cmd_map):
        # IC-7610 declares a cmd29 route for 0x14/0x01 (MOR-1543).
        expected = build_cmd29_frame(
            IC_7610_ADDR,
            CONTROLLER_ADDR,
            0x14,
            sub=0x01,
            data=_level_bcd_encode(200),
            receiver=RECEIVER_MAIN,
        )
        assert commands.set_af_level(200, cmd_map=cmd_map) == expected

    def test_set_rf_gain(self, cmd_map):
        expected = build_cmd29_frame(
            IC_7610_ADDR,
            CONTROLLER_ADDR,
            0x14,
            sub=0x02,
            data=_level_bcd_encode(128),
            receiver=RECEIVER_MAIN,
        )
        assert commands.set_rf_gain(128, cmd_map=cmd_map) == expected

    def test_set_squelch(self, cmd_map):
        expected = build_cmd29_frame(
            IC_7610_ADDR,
            CONTROLLER_ADDR,
            0x14,
            sub=0x03,
            data=_level_bcd_encode(64),
            receiver=RECEIVER_MAIN,
        )
        assert commands.set_squelch(64, cmd_map=cmd_map) == expected

    def test_set_tuning_step(self, cmd_map):
        # commands/vfo.py migrated onto the bound command map in MOR-2007
        # Steps 5..N (module 3): set_tuning_step now requires cmd_map --
        # pinned against the frame the deleted fallback used to build.
        expected = build_civ_frame(
            IC_7610_ADDR, CONTROLLER_ADDR, 0x10, data=bytes([0x03])
        )
        assert commands.set_tuning_step(3, cmd_map=cmd_map) == expected

    def test_ptt_on(self, cmd_map):
        # commands/ptt.py migrated onto the bound command map in MOR-2007
        # Steps 5..N (module 4): ptt_on now requires cmd_map -- pinned
        # against the frame the deleted fallback used to build.
        expected = build_civ_frame(
            IC_7610_ADDR, CONTROLLER_ADDR, 0x1C, sub=0x00, data=b"\x01"
        )
        assert commands.ptt_on(cmd_map=cmd_map) == expected

    def test_ptt_off(self, cmd_map):
        expected = build_civ_frame(
            IC_7610_ADDR, CONTROLLER_ADDR, 0x1C, sub=0x00, data=b"\x00"
        )
        assert commands.ptt_off(cmd_map=cmd_map) == expected

    def test_power_on(self, cmd_map):
        assert commands.power_on(cmd_map=cmd_map) == commands.power_on()

    def test_power_off(self, cmd_map):
        assert commands.power_off(cmd_map=cmd_map) == commands.power_off()

    def test_stop_cw(self, cmd_map):
        assert commands.stop_cw(cmd_map=cmd_map) == commands.stop_cw()

    def test_start_scan(self, cmd_map):
        # commands/vfo.py migrated onto the bound command map in MOR-2007
        # Steps 5..N (module 3): scan_start now requires cmd_map -- pinned
        # against the frame the deleted fallback used to build.
        expected = build_civ_frame(IC_7610_ADDR, CONTROLLER_ADDR, 0x0E, data=b"\x01")
        assert commands.scan_start(cmd_map=cmd_map) == expected

    def test_stop_scan(self, cmd_map):
        expected = build_civ_frame(IC_7610_ADDR, CONTROLLER_ADDR, 0x0E, data=b"\x00")
        assert commands.scan_stop(cmd_map=cmd_map) == expected


# ── PTT wire-tuple contract: every CI-V profile ─────────────────


class TestPttWireContractAcrossProfiles:
    """MOR-2002 step 2b-ptt (Q7, ``docs/plans/2026-08-29-profile-driven-
    command-bytes.md`` §8.1) fixed a doubled-payload divergence while
    ``ptt.py`` still had a fallback to compare against: ``rigs/ic705.toml``,
    ``rigs/ic7300.toml``, ``rigs/ic7610.toml``, ``rigs/ic9700.toml`` and
    ``rigs/x6200.toml`` held a 2-byte ``ptt_on``/``ptt_off`` tuple and the
    ``cmd_map`` branch appended the payload byte itself; ``rigs/x6100.toml``
    already held the 3-byte tuple, so its map branch doubled the payload
    (``tests/command_map_parity_divergences.txt``, X6100 rows, since fixed
    by commits 9957ee49/713172a6).

    MOR-2007 Steps 5..N (module 4) made ``cmd_map`` required and deleted the
    fallback, so there is no more "identical to fallback" to assert --
    converted the same way ``config.py``'s/``levels.py``'s/``vfo.py``'s own
    migrations converted their equivalent classes (the transitional pin
    recorded from the #2820 review): every CI-V profile that declares
    ``ptt_on``/``ptt_off`` is swept and each is pinned directly, ending in
    the explicit payload byte the Q7 contract requires.
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

    def test_ptt_on_ends_with_the_payload_byte_on_every_civ_profile(self) -> None:
        for model, cmd_map in self._civ_ptt_maps().items():
            mapped = commands.ptt_on(cmd_map=cmd_map)
            assert mapped.endswith(b"\x1c\x00\x01\xfd"), model

    def test_ptt_off_ends_with_the_payload_byte_on_every_civ_profile(self) -> None:
        for model, cmd_map in self._civ_ptt_maps().items():
            mapped = commands.ptt_off(cmd_map=cmd_map)
            assert mapped.endswith(b"\x1c\x00\x00\xfd"), model


# ── VFO / scope wire contracts: every declaring profile ─────────


class TestVfoScopeMapFallbackParityAcrossProfiles:
    """MOR-2002 step 2b-vfo-scope (Q7, ``docs/plans/2026-08-29-profile-driven-
    command-bytes.md`` §4 Step 2) closed two doubled-byte divergences while
    ``vfo.py`` still had a fallback to compare against:

    ``vfo.py: get_dual_watch`` / ``get_main_sub_band`` declare a 2-byte
    ``[command, sub]`` tuple that already carries the query byte as its
    sub-command; the ``cmd_map`` branch also passed that byte as ``data``,
    doubling it (``07 c2 c2`` / ``07 d2 d2`` instead of ``07 c2`` / ``07
    d2``).

    ``scope.py: get_scope_center_type`` took a ``receiver`` keyword whose
    ``cmd_map`` branch dropped it while the fallback appended it -- on
    0x1C the extra byte is a SET, not a read (MOR-1981). The fix refuses
    the argument outright rather than special-casing it, so there is only
    one call shape left and both branches agree by construction.

    ``vfo.py``'s two getters and ``scope.py``'s ``get_scope_center_type``
    below no longer have a fallback to compare against (MOR-2007 Steps
    5..N modules 3 and 5 made ``cmd_map`` required and deleted it in each)
    -- converted the same way ``config.py``'s/``levels.py``'s own
    migrations converted their "identical to fallback" classes: pin the
    map path directly against the frame the deleted fallback used to
    build.
    """

    @staticmethod
    def _maps_declaring(name: str) -> dict[str, CommandMap]:
        maps: dict[str, CommandMap] = {}
        for model, config in sorted(discover_rigs(RIG_DIR).items()):
            cmd_map = config.to_command_map()
            if cmd_map.has(name):
                maps[model] = cmd_map
        return maps

    def test_get_dual_watch_declared_by_ic7610_and_ic9700(self) -> None:
        assert set(self._maps_declaring("get_dual_watch")) == {"IC-7610", "IC-9700"}

    def test_get_dual_watch_on_ic7610(self) -> None:
        cmd_map = self._maps_declaring("get_dual_watch")["IC-7610"]
        mapped = commands.get_dual_watch(cmd_map=cmd_map)
        assert mapped.endswith(b"\x07\xc2\xfd")

    def test_get_dual_watch_on_ic9700(self) -> None:
        """MOR-2007 ruling 3: IC-9700 has no 0x07 family for dual watch at
        all -- its ``get_dual_watch`` map entry is the guide-confirmed
        0x16 0x59 ("Send/read the sub band (the Dualwatch function)",
        IC-9700 CI-V Reference Guide (Icom, 2019) p.5), where the deleted
        fallback used to send the guide-refuted 0x07 0xC2 (D2, MOR-2015).
        """
        cmd_map = self._maps_declaring("get_dual_watch")["IC-9700"]
        mapped = commands.get_dual_watch(cmd_map=cmd_map)
        assert mapped.endswith(b"\x16\x59\xfd")

    def test_get_main_sub_band_declared_by_ic7610_and_ic9700(self) -> None:
        assert set(self._maps_declaring("get_main_sub_band")) == {
            "IC-7610",
            "IC-9700",
        }

    def test_get_main_sub_band_on_every_declaring_profile(self) -> None:
        for model, cmd_map in self._maps_declaring("get_main_sub_band").items():
            mapped = commands.get_main_sub_band(cmd_map=cmd_map)
            assert mapped.endswith(b"\x07\xd2\xfd"), model

    def test_get_scope_center_type_declared_by_all_four_scope_profiles(self) -> None:
        assert set(self._maps_declaring("get_scope_center_type")) == {
            "IC-705",
            "IC-7300",
            "IC-7610",
            "IC-9700",
        }

    def test_get_scope_center_type_on_every_declaring_profile(self) -> None:
        for model, cmd_map in self._maps_declaring("get_scope_center_type").items():
            mapped = commands.get_scope_center_type(cmd_map=cmd_map)
            assert mapped.endswith(b"\x27\x1c\xfd"), model

    def test_get_scope_center_type_refuses_receiver_argument(self, cmd_map) -> None:
        # cmd_map is passed so this raises on the removed receiver kwarg
        # specifically, not merely because cmd_map was omitted.
        with pytest.raises(TypeError, match="receiver"):
            commands.get_scope_center_type(receiver=0, cmd_map=cmd_map)


# ── Helper-delegating functions ─────────────────────────────────


class TestHelperCallerParity:
    """These _build_*-delegating functions must match with cmd_map.

    ``get_apf_type_level``, ``get_nr_level`` and ``get_nb_level``
    (commands/levels.py) and ``get_ref_adjust`` (below) migrated onto the
    bound command map in MOR-2006 Steps 5..N (module 2) and now require
    ``cmd_map`` -- their cases pin the map path against the frame the
    deleted fallback used to build.
    """

    def test_get_apf_type_level(self, cmd_map):
        expected = build_cmd29_frame(
            IC_7610_ADDR, CONTROLLER_ADDR, 0x14, sub=0x05, receiver=RECEIVER_MAIN
        )
        assert commands.get_apf_type_level(cmd_map=cmd_map) == expected

    def test_get_nr_level(self, cmd_map):
        expected = build_cmd29_frame(
            IC_7610_ADDR, CONTROLLER_ADDR, 0x14, sub=0x06, receiver=RECEIVER_MAIN
        )
        assert commands.get_nr_level(cmd_map=cmd_map) == expected

    def test_get_nb_level(self, cmd_map):
        expected = build_cmd29_frame(
            IC_7610_ADDR, CONTROLLER_ADDR, 0x14, sub=0x12, receiver=RECEIVER_MAIN
        )
        assert commands.get_nb_level(cmd_map=cmd_map) == expected

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
        expected = build_civ_frame(
            IC_7610_ADDR, CONTROLLER_ADDR, 0x1A, sub=0x05, data=b"\x00\x70"
        )
        assert commands.get_ref_adjust(cmd_map=cmd_map) == expected

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

    def test_different_wire_bytes_produce_different_frame(self, cmd_map):
        # MOR-2006 Steps 5..N (module 2): get_af_level now requires
        # cmd_map, so "different wire bytes" is shown against the real
        # IC-7610 map rather than the deleted fallback.
        custom = CommandMap({"get_af_level": (0x16, 0x43)})
        result = commands.get_af_level(cmd_map=custom)
        known_good = commands.get_af_level(cmd_map=cmd_map)
        assert result != known_good
        assert b"\x16\x43" in result

    def test_custom_single_byte_command(self):
        custom = CommandMap({"get_freq": (0xFF,)})
        result = commands.get_freq(cmd_map=custom)
        assert b"\xff" in result
        assert result != commands.get_freq()

    def test_custom_setter(self, cmd_map):
        # MOR-2006 Steps 5..N (module 2): set_rf_power now requires
        # cmd_map, so the "different wire bytes" comparison uses the real
        # IC-7610 map rather than the deleted fallback.
        custom = CommandMap({"set_rf_power": (0x14, 0xFF)})
        result = commands.set_rf_power(128, cmd_map=custom)
        known_good = commands.set_rf_power(128, cmd_map=cmd_map)
        assert result != known_good
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
