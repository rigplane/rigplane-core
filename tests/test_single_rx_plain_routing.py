"""MOR-178: single-RX plain CAT routing for ATT + PREAMP.

Single-RX radios (e.g. Xiegu X6200, receiver_count=1, no cmd29 routes)
must emit plain CI-V frames for attenuator (0x11) and preamp (0x16/0x02),
because they answer the plain command — not a cmd29-wrapped one.

Dual-RX radios (e.g. IC-7610) declare cmd29 routes for 0x11 and 0x16/0x02
and MUST keep emitting byte-identical cmd29 frames (zero regression).
"""

from __future__ import annotations

import dataclasses

import pytest

from rigplane.commands import (
    _bcd_byte,
    build_cmd29_frame,
    get_attenuator,
    get_preamp,
    set_preamp,
)
from rigplane.commands._frame import _CMD_ATT, _CMD_PREAMP, _SUB_PREAMP_STATUS
from rigplane.exceptions import CommandError
from rigplane.profiles import get_radio_profile
from rigplane.radio import IcomRadio
from rigplane.types import CivFrame

# Reused rather than re-implemented, per the MOR-2086 "enumerate the shipped
# profiles, don't restate them" requirement -- same civ-only filter
# test_profile_command_coverage.py's own _civ_rigs() applies.
from test_profile_command_binding import _civ_rig_configs

X6200_PROFILE_ID = "xiegu_x6200"
IC7610_PROFILE_ID = "icom_ic7610"
IC7610_ADDR = 0x98
X6200_ADDR = 0xA4  # arbitrary radio addr for builder byte assertions

# commands/dsp.py migrated onto the bound command map in MOR-2008 (batch
# 3): get_attenuator/get_preamp/set_preamp now require cmd_map. Both
# profiles declare the same [0x11]/[0x16, 0x02] wire tuples the deleted
# fallback used to build (zero divergence, confirmed by
# tests/command_map_parity_divergences.txt being empty), so passing each
# profile's own map changes no expected byte below.
_X6200_CMD_MAP = get_radio_profile(X6200_PROFILE_ID).command_map
_IC7610_CMD_MAP = get_radio_profile(IC7610_PROFILE_ID).command_map


# ---------------------------------------------------------------------------
# (a) Builder byte assertions
# ---------------------------------------------------------------------------


class TestBuildersPlain:
    """Single-RX plain form (command29=False) — no 0x29 wrapper."""

    def test_get_attenuator_plain(self) -> None:
        frame = get_attenuator(
            to_addr=X6200_ADDR, command29=False, cmd_map=_X6200_CMD_MAP
        )
        assert frame == bytes([0xFE, 0xFE, X6200_ADDR, 0xE0, _CMD_ATT, 0xFD])
        assert frame[4] == _CMD_ATT
        assert 0x29 not in (frame[4],)

    def test_get_preamp_plain(self) -> None:
        frame = get_preamp(to_addr=X6200_ADDR, command29=False, cmd_map=_X6200_CMD_MAP)
        assert frame[4] == _CMD_PREAMP
        assert frame[5] == _SUB_PREAMP_STATUS
        assert frame == bytes(
            [0xFE, 0xFE, X6200_ADDR, 0xE0, _CMD_PREAMP, _SUB_PREAMP_STATUS, 0xFD]
        )

    def test_set_preamp_plain(self) -> None:
        frame = set_preamp(
            1, to_addr=X6200_ADDR, command29=False, cmd_map=_X6200_CMD_MAP
        )
        assert frame[4] == _CMD_PREAMP
        assert frame[5] == _SUB_PREAMP_STATUS
        assert frame == bytes(
            [
                0xFE,
                0xFE,
                X6200_ADDR,
                0xE0,
                _CMD_PREAMP,
                _SUB_PREAMP_STATUS,
                0x01,
                0xFD,
            ]
        )


class TestBuildersCmd29Regression:
    """Dual-RX regression lock: default (command29=True) == cmd29 frame, byte-identical."""

    def test_get_attenuator_cmd29_default(self) -> None:
        frame = get_attenuator(to_addr=IC7610_ADDR, cmd_map=_IC7610_CMD_MAP)
        assert frame[4] == 0x29
        assert frame == build_cmd29_frame(IC7610_ADDR, 0xE0, _CMD_ATT)

    def test_get_preamp_cmd29_default(self) -> None:
        frame = get_preamp(to_addr=IC7610_ADDR, cmd_map=_IC7610_CMD_MAP)
        assert frame[4] == 0x29
        assert frame == build_cmd29_frame(
            IC7610_ADDR, 0xE0, _CMD_PREAMP, sub=_SUB_PREAMP_STATUS
        )

    def test_set_preamp_cmd29_default(self) -> None:
        frame = set_preamp(1, to_addr=IC7610_ADDR, cmd_map=_IC7610_CMD_MAP)
        assert frame[4] == 0x29
        assert frame == build_cmd29_frame(
            IC7610_ADDR, 0xE0, _CMD_PREAMP, sub=_SUB_PREAMP_STATUS, data=bytes([0x01])
        )


# ---------------------------------------------------------------------------
# (b) Runtime routing — real IcomRadio with real profiles, captured frames
# ---------------------------------------------------------------------------


def _make_radio(profile_id: str) -> tuple[IcomRadio, list[bytes]]:
    """Build a connected radio with real profile; capture emitted CI-V bytes."""
    radio = IcomRadio("192.168.0.1", profile=profile_id, timeout=0.05)
    radio._connected = True
    # _check_connected delegates to the CI-V runtime, which requires a live
    # _civ_transport; a sentinel suffices because we stub the send methods.
    radio._civ_transport = object()  # type: ignore[assignment]
    captured: list[bytes] = []

    async def _capture_raw(civ_frame: bytes, **kwargs: object) -> None:
        captured.append(civ_frame)
        return None

    radio._send_civ_raw = _capture_raw  # type: ignore[method-assign]
    return radio, captured


def _expect_factory(captured: list[bytes], response: CivFrame):
    async def _capture_expect(civ_frame: bytes, **kwargs: object) -> CivFrame:
        captured.append(civ_frame)
        return response

    return _capture_expect


@pytest.mark.asyncio
class TestX6200RuntimePlain:
    async def test_set_attenuator_emits_plain(self) -> None:
        radio, captured = _make_radio(X6200_PROFILE_ID)
        await radio.set_attenuator(True)
        assert len(captured) == 1
        frame = captured[0]
        assert frame[4] in (_CMD_ATT, _CMD_PREAMP)
        assert frame[4] == _CMD_ATT
        assert frame[4] != 0x29

    async def test_set_preamp_emits_plain(self) -> None:
        radio, captured = _make_radio(X6200_PROFILE_ID)
        await radio.set_preamp(1)
        assert len(captured) == 1
        frame = captured[0]
        assert frame[4] == _CMD_PREAMP
        assert frame[5] == _SUB_PREAMP_STATUS
        assert frame[4] != 0x29

    async def test_get_attenuator_level_plain_and_decodes(self) -> None:
        radio, captured = _make_radio(X6200_PROFILE_ID)
        # Plain response: FE FE E0 A4 11 00 FD -> data 0x00 (off)
        response = CivFrame(
            to_addr=0xE0, from_addr=radio._radio_addr, command=_CMD_ATT, data=b"\x00"
        )
        radio._send_civ_expect = _expect_factory(captured, response)  # type: ignore[method-assign]
        value = await radio.get_attenuator_level()
        assert value == 0
        frame = captured[0]
        assert frame[4] == _CMD_ATT
        assert frame[4] != 0x29

    async def test_get_preamp_plain_and_decodes(self) -> None:
        radio, captured = _make_radio(X6200_PROFILE_ID)
        # Plain response: FE FE E0 A4 16 02 00 FD -> data 0x00 (off)
        response = CivFrame(
            to_addr=0xE0,
            from_addr=radio._radio_addr,
            command=_CMD_PREAMP,
            sub=_SUB_PREAMP_STATUS,
            data=b"\x00",
        )
        radio._send_civ_expect = _expect_factory(captured, response)  # type: ignore[method-assign]
        value = await radio.get_preamp()
        assert value == 0
        frame = captured[0]
        assert frame[4] == _CMD_PREAMP
        assert frame[5] == _SUB_PREAMP_STATUS
        assert frame[4] != 0x29


@pytest.mark.asyncio
class TestX6200SubReceiverRejected:
    async def test_set_attenuator_sub_raises(self) -> None:
        radio, _captured = _make_radio(X6200_PROFILE_ID)
        # receiver_count == 1 -> SUB unsupported
        assert get_radio_profile(X6200_PROFILE_ID).receiver_count == 1
        with pytest.raises(CommandError):
            await radio.set_attenuator(True, receiver=1)


@pytest.mark.asyncio
class TestIC7610RuntimeRegression:
    async def test_set_attenuator_off_main_emits_cmd29(self) -> None:
        """``on=False`` is unambiguous (always resolves to 0), so it still
        goes over the wire cmd29-wrapped, byte-identical to before MOR-2086.
        """
        radio, captured = _make_radio(IC7610_PROFILE_ID)
        await radio.set_attenuator(False)
        assert len(captured) == 1
        frame = captured[0]
        assert frame[4] == 0x29
        assert frame == build_cmd29_frame(
            radio._radio_addr, 0xE0, _CMD_ATT, data=bytes([0x00])
        )

    async def test_set_attenuator_on_refused_for_stepped_attenuator(self) -> None:
        """MOR-2086: IC-7610 declares 15 non-zero attenuator steps (3..45 dB
        in 3 dB increments), so the boolean "on" form has no single defined
        answer -- refused rather than guessing a level, and nothing is sent.
        """
        radio, captured = _make_radio(IC7610_PROFILE_ID)
        with pytest.raises(CommandError, match="set_attenuator_level"):
            await radio.set_attenuator(True)
        assert captured == []

    async def test_set_preamp_main_emits_cmd29(self) -> None:
        radio, captured = _make_radio(IC7610_PROFILE_ID)
        # set_preamp(level>0) pre-checks DIGI-SEL via _send_civ_expect; return OFF.
        digisel_off = CivFrame(
            to_addr=0xE0,
            from_addr=radio._radio_addr,
            command=_CMD_PREAMP,
            sub=0x4E,
            data=b"\x00",
        )

        async def _expect(civ_frame: bytes, **kwargs: object) -> CivFrame:
            return digisel_off

        radio._send_civ_expect = _expect  # type: ignore[method-assign]
        await radio.set_preamp(1)
        assert len(captured) == 1
        frame = captured[0]
        assert frame[4] == 0x29
        assert frame == build_cmd29_frame(
            radio._radio_addr,
            0xE0,
            _CMD_PREAMP,
            sub=_SUB_PREAMP_STATUS,
            data=bytes([0x01]),
        )


# ---------------------------------------------------------------------------
# (c) MOR-2086 -- CoreRadio.set_attenuator resolves ``on`` from the
# profile's own declared ``[attenuator] values`` instead of a hardcoded
# constant (the deleted ``commands/dsp.py: set_attenuator`` hardcoded 18,
# an IC-7610 step invalid on every other CI-V profile). Enumerated via
# test_profile_command_binding.py's own ``_civ_rig_configs`` (same
# discover_rigs + civ-only filter test_profile_command_coverage.py's
# ``_civ_rigs`` applies) rather than restated as a hand-written table, so a
# new CI-V profile is covered automatically, with no edit here.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
class TestSetAttenuatorResolvesFromProfile:
    async def test_on_puts_the_single_declared_value_on_the_wire(self) -> None:
        single_value_models: dict[str, int] = {}
        for model, config in _civ_rig_configs().items():
            att_values = config.to_profile().att_values or ()
            non_zero = sorted({v for v in att_values if v != 0})
            if len(non_zero) == 1:
                single_value_models[model] = non_zero[0]
        # A CI-V profile with exactly one non-zero step must actually be
        # found here, or the loop below would pass vacuously.
        assert single_value_models

        for model, expected in sorted(single_value_models.items()):
            expected_byte = _bcd_byte(expected)
            radio, captured = _make_radio(model)

            await radio.set_attenuator(True)
            frame = captured[-1]
            data_index = 7 if frame[4] == 0x29 else 5
            assert frame[data_index] == expected_byte, (
                f"{model}: expected declared value {expected} "
                f"(BCD 0x{expected_byte:02x}) on the wire, got {frame.hex(' ')}"
            )

            await radio.set_attenuator(False)
            frame = captured[-1]
            data_index = 7 if frame[4] == 0x29 else 5
            assert frame[data_index] == 0, model

    async def test_on_is_refused_when_profile_declares_no_attenuator_values(
        self,
    ) -> None:
        """A profile that omits ``[attenuator] values`` entirely must refuse
        aloud rather than fall back to a constant -- the whole defect MOR-2086
        removes."""
        undeclared = dataclasses.replace(
            get_radio_profile(IC7610_PROFILE_ID), att_values=None
        )
        radio, captured = _make_radio(undeclared)
        with pytest.raises(CommandError, match="attenuator values"):
            await radio.set_attenuator(True)
        assert captured == []
