"""The keystone test for MOR-2006 Steps 5..N of
``docs/plans/2026-08-29-profile-driven-command-bytes.md`` (§4, §6, §7).

Plan §7: "the dangerous half is requests moved, replies not: reads fail on
the 46 class-A rows and keep working everywhere else, which looks like an
intermittent radio rather than a bug." A getter whose request now goes
through the profile's own map but whose reply matcher still expects the
old, hardcoded command/sub bytes silently mismatches on exactly the
commands this migration fixes, and nowhere else.

This test makes that state impossible to ship. For every CI-V profile and
every matcher-backed getter registered in ``MATCHER_BACKED_GETTERS`` below,
it captures the ``(command, sub, prefix)`` ``runtime/radio.py: CoreRadio``
actually hands to ``_get_bcd_level``/``_get_bool_value`` when parsing a
reply, and compares it against the shape independently derived from the
same profile's ``CommandMap`` entry via
``commands/_frame.py: decode_wire_tuple`` -- the same decoder
``commands/bound.py: BoundCommands.expect`` and the request-building
``_build_from_map`` both resolve through. The two must always agree.

No transport, connection or event I/O is needed for any radio backend:
``_get_bcd_level``/``_get_bool_value`` are monkeypatched at the class level
to record their keyword arguments and return a dummy value immediately,
short-circuiting before either method would touch a socket.

Written to extend, not to be rewritten: add a ``_GetterSpec`` to
``MATCHER_BACKED_GETTERS`` (§6 population 1) as later modules migrate, and
this file exercises it against every CI-V profile automatically.
"""

from __future__ import annotations

import dataclasses
from typing import Any

import pytest

from rigplane.commands._frame import decode_wire_tuple
from rigplane.core.types import CivFrame
from rigplane.runtime.radio import CoreRadio

from test_profile_command_binding import _civ_rig_configs


@dataclasses.dataclass(frozen=True)
class _GetterSpec:
    """One matcher-backed getter.

    ``method`` is both the ``CoreRadio`` method name and the ``CommandMap``
    key it resolves -- true for every getter registered so far because
    each one's builder exposes a literal key equal to its own name (the
    first case in plan §3.1's four-way classification of how a builder
    exposes its command-map key; none of config.py's getters is the
    fourth case, ``commands/speech.py: get_speech``, whose key is a
    function of the map). ``parser`` is the ``CoreRadio`` method the
    getter parses its reply through -- ``_get_bcd_level`` for a BCD level
    or index, ``_get_bool_value`` for a boolean.
    """

    method: str
    parser: str


# commands/config.py's matcher-backed getters (MOR-2006 Steps 5..N, module
# 1). Extend this tuple module by module as later steps migrate their own
# getters -- everything below runs for every entry, against every CI-V
# profile, with no further change required.
MATCHER_BACKED_GETTERS: tuple[_GetterSpec, ...] = (
    _GetterSpec("get_acc1_mod_level", "_get_bcd_level"),
    _GetterSpec("get_usb_mod_level", "_get_bcd_level"),
    _GetterSpec("get_lan_mod_level", "_get_bcd_level"),
    _GetterSpec("get_data_off_mod_input", "_get_bcd_level"),
    _GetterSpec("get_data1_mod_input", "_get_bcd_level"),
    _GetterSpec("get_data2_mod_input", "_get_bcd_level"),
    _GetterSpec("get_data3_mod_input", "_get_bcd_level"),
    _GetterSpec("get_civ_transceive", "_get_bool_value"),
    _GetterSpec("get_civ_output_ant", "_get_bool_value"),
    # commands/levels.py's matcher-backed getters (MOR-2006 Steps 5..N,
    # module 2). get_rf_power, get_rf_gain and get_af_level are not
    # included: they parse their reply with _level_bcd_decode/_parse_level
    # directly, without checking the reply's command/sub bytes, so they
    # have no matcher shape to compare here (module docstring in
    # runtime/radio.py, "commands/levels.py, migrated...").
    _GetterSpec("get_squelch", "_get_bcd_level"),
    _GetterSpec("get_apf_type_level", "_get_bcd_level"),
    _GetterSpec("get_nr_level", "_get_bcd_level"),
    _GetterSpec("get_pbt_inner", "_get_bcd_level"),
    _GetterSpec("get_pbt_outer", "_get_bcd_level"),
    _GetterSpec("get_cw_pitch", "_get_bcd_level"),
    _GetterSpec("get_mic_gain", "_get_bcd_level"),
    _GetterSpec("get_key_speed", "_get_bcd_level"),
    _GetterSpec("get_notch_filter", "_get_bcd_level"),
    _GetterSpec("get_compressor_level", "_get_bcd_level"),
    _GetterSpec("get_break_in_delay", "_get_bcd_level"),
    _GetterSpec("get_nb_level", "_get_bcd_level"),
    _GetterSpec("get_digisel_shift", "_get_bcd_level"),
    _GetterSpec("get_drive_gain", "_get_bcd_level"),
    _GetterSpec("get_monitor_gain", "_get_bcd_level"),
    _GetterSpec("get_vox_gain", "_get_bcd_level"),
    _GetterSpec("get_anti_vox_gain", "_get_bcd_level"),
    _GetterSpec("get_ref_adjust", "_get_bcd_level"),
    _GetterSpec("get_dash_ratio", "_get_bcd_level"),
    _GetterSpec("get_nb_depth", "_get_bcd_level"),
    _GetterSpec("get_nb_width", "_get_bcd_level"),
    _GetterSpec("get_vox_delay", "_get_bcd_level"),
    # commands/vfo.py's matcher-backed getters (MOR-2007 Steps 5..N,
    # module 3). get_dual_watch is NOT included: 0x07 carries no CI-V
    # sub-command (_frame.py: _COMMANDS_WITH_SUB excludes it, unlike
    # 0x1A here), so its reply marker lands in data[0] rather than
    # .sub -- it cannot route through _get_bcd_level/_get_bool_value at
    # all, and is instead pinned directly by
    # test_get_dual_watch_reply_marker_comes_from_the_map below, this
    # file's keystone case for that shape. get_vfo, get_main_sub_band,
    # vfo_a_equals_b and vfo_swap have no production caller in
    # runtime/radio.py, so there is no CoreRadio getter to register here.
    _GetterSpec("get_quick_split", "_get_bool_value"),
    _GetterSpec("get_quick_dual_watch", "_get_bool_value"),
)


def _cases() -> list[tuple[str, _GetterSpec]]:
    return [
        (model, spec)
        for model in sorted(_civ_rig_configs())
        for spec in MATCHER_BACKED_GETTERS
    ]


def _case_id(case: tuple[str, _GetterSpec]) -> str:
    model, spec = case
    return f"{model}-{spec.method}"


@pytest.mark.asyncio
@pytest.mark.parametrize("case", _cases(), ids=[_case_id(c) for c in _cases()])
async def test_reply_shape_matches_request_shape(
    case: tuple[str, _GetterSpec], monkeypatch: pytest.MonkeyPatch
) -> None:
    model, spec = case
    config = _civ_rig_configs()[model]
    profile = config.to_profile()
    cmd_map = profile.command_map
    assert cmd_map is not None, f"{model}: to_profile() produced no command_map"
    if not cmd_map.has(spec.method):
        pytest.skip(f"{model} does not declare {spec.method}")

    expected_shape = decode_wire_tuple(cmd_map.get(spec.method))

    captured: dict[str, Any] = {}

    async def _fake_parser(
        self: CoreRadio,
        civ: bytes,
        *,
        key: str,
        command: int,
        sub: int | None,
        prefix: bytes = b"",
        **kwargs: Any,
    ) -> Any:
        captured["shape"] = (command, sub, prefix)
        return 0

    monkeypatch.setattr(CoreRadio, spec.parser, _fake_parser)
    # get_squelch (commands/levels.py) checks the connection itself, ahead
    # of the mocked parser -- unlike every other getter registered above,
    # which reaches the connection check only inside the mocked method.
    # No-op it here so this test stays about reply-shape matching alone,
    # not connection state, for every entry regardless of that difference.
    monkeypatch.setattr(CoreRadio, "_check_connected", lambda self: None)
    radio = CoreRadio("198.51.100.1", profile=profile)
    await getattr(radio, spec.method)()

    assert "shape" in captured, (
        f"{model}:{spec.method} never reached CoreRadio.{spec.parser}"
    )
    assert captured["shape"] == expected_shape, (
        f"{model}:{spec.method} parsed its reply against {captured['shape']}, "
        f"but the request the same call built goes to {expected_shape} -- "
        "the reply matcher was not migrated with the request."
    )


@pytest.mark.asyncio
async def test_get_dual_watch_reply_marker_comes_from_the_map(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Keystone case for ``get_dual_watch``, which cannot register in
    ``MATCHER_BACKED_GETTERS`` above: 0x07 carries no CI-V sub-command
    (``_frame.py: _COMMANDS_WITH_SUB`` excludes it, unlike 0x1A/0x16 for
    the getters above), so its reply marker is echoed in ``data[0]``
    rather than landing in ``.sub`` -- it cannot route through
    ``_get_bcd_level``/``_get_bool_value`` at all, and
    ``runtime/radio.py: CoreRadio.get_dual_watch`` handles both wire
    shapes itself instead.

    Proven against two profiles whose ``get_dual_watch`` commands are
    wire-incompatible -- IC-7610's ``[0x07, 0xC2]`` (marker in data[0])
    and IC-9700's ``[0x16, 0x59]`` (marker in .sub, since 0x16 IS in
    ``_COMMANDS_WITH_SUB``) -- so a marker hardcoded to either family's
    byte answers the OTHER family's reply wrongly regardless of the
    actual on/off value: exactly the "requests moved, replies not"
    failure mode this file exists to make impossible (plan §7).

    Manually confirmed red for the half-done shape: reverting
    ``get_dual_watch`` to its pre-migration check (literal
    ``resp.data[0] == 0xC2``, ignoring ``resp.sub``/the map) makes
    ``test_ic9700`` below fail -- IC-9700's real reply is
    ``command=0x16, sub=0x59, data=[value]``, which never contains
    ``0xC2`` anywhere, so the reverted check would always return
    ``False`` regardless of the actual toggle state.
    """

    async def _get_dual_watch_answering(radio: CoreRadio, frame) -> bool:
        async def _fake_expect(civ: bytes, **kwargs: Any) -> Any:
            return frame

        monkeypatch.setattr(radio, "_send_civ_expect", _fake_expect)
        monkeypatch.setattr(radio, "_check_connected", lambda: None)
        return await radio.get_dual_watch()

    # IC-7610: [0x07, 0xC2] -- 0x07 has no CI-V sub-command, marker in data[0].
    ic7610 = _civ_rig_configs()["IC-7610"].to_profile()
    command, sub, prefix = decode_wire_tuple(ic7610.command_map.get("get_dual_watch"))
    assert (command, sub, prefix) == (0x07, 0xC2, b"")
    radio_7610 = CoreRadio("198.51.100.1", profile=ic7610)

    on_reply = CivFrame(
        to_addr=0xE0, from_addr=0x98, command=command, sub=None, data=b"\xc2\x01"
    )
    assert await _get_dual_watch_answering(radio_7610, on_reply) is True

    off_reply = CivFrame(
        to_addr=0xE0, from_addr=0x98, command=command, sub=None, data=b"\xc2\x00"
    )
    assert await _get_dual_watch_answering(radio_7610, off_reply) is False

    # A reply with the WRONG marker byte (echoing some other 0x07 query)
    # must not be misread as dual-watch state.
    wrong_marker = CivFrame(
        to_addr=0xE0, from_addr=0x98, command=command, sub=None, data=b"\xd2\x01"
    )
    assert await _get_dual_watch_answering(radio_7610, wrong_marker) is False

    # IC-9700: [0x16, 0x59] -- 0x16 IS in _COMMANDS_WITH_SUB, marker in .sub.
    ic9700 = _civ_rig_configs()["IC-9700"].to_profile()
    command9700, sub9700, prefix9700 = decode_wire_tuple(
        ic9700.command_map.get("get_dual_watch")
    )
    assert (command9700, sub9700, prefix9700) == (0x16, 0x59, b"")
    radio_9700 = CoreRadio("198.51.100.1", profile=ic9700)

    on_reply_9700 = CivFrame(
        to_addr=0xE0, from_addr=0xA2, command=command9700, sub=sub9700, data=b"\x01"
    )
    assert await _get_dual_watch_answering(radio_9700, on_reply_9700) is True

    off_reply_9700 = CivFrame(
        to_addr=0xE0, from_addr=0xA2, command=command9700, sub=sub9700, data=b"\x00"
    )
    assert await _get_dual_watch_answering(radio_9700, off_reply_9700) is False

    # Wrong sub-command (not 0x59) must not be misread either.
    wrong_sub_9700 = CivFrame(
        to_addr=0xE0, from_addr=0xA2, command=command9700, sub=0x12, data=b"\x01"
    )
    assert await _get_dual_watch_answering(radio_9700, wrong_sub_9700) is False
