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
    # file's keystone case for that shape. get_vfo and get_main_sub_band
    # have no production caller in runtime/radio.py, so there is no
    # CoreRadio getter to register here. The 0x07 swap/equalize ops --
    # `runtime/_dual_rx_runtime.py: DualRxRuntimeMixin.swap_vfo_ab`/
    # `equalize_vfo_ab`/`swap_main_sub`/`equalize_main_sub` -- are
    # runtime methods that build the frame from a profile-declared code,
    # not `rigplane.commands` builders, so there is no commands-layer
    # getter to register for them either.
    _GetterSpec("get_quick_split", "_get_bool_value"),
    _GetterSpec("get_quick_dual_watch", "_get_bool_value"),
    # commands/mode.py's, commands/tone.py's and commands/meters.py's
    # matcher-backed getters (MOR-2008 Steps 5..N, batch 2). get_mode,
    # get_data_mode and get_filter_width (mode.py) are NOT included: each
    # parses its reply through its own module-level parse_*_response
    # function (a hardcoded command/sub check) or, for get_filter_width,
    # bespoke per-profile BCD/raw-byte handling -- never through
    # _get_bcd_level/_get_bool_value with a map-derived shape (mirrors
    # commands/levels.py's get_rf_power/get_rf_gain/get_af_level exclusion
    # above). meters.py's get_s_meter/get_swr/get_alc/get_power_meter/
    # get_comp_meter/get_vd_meter/get_id_meter are excluded the same way,
    # via parse_meter_response; CoreRadio's own getter for get_alc is
    # additionally named get_alc_meter, not get_alc, which would violate
    # this dataclass's own method-equals-key invariant besides. antenna.py's
    # four getters are excluded for the reason documented in antenna.py's
    # own module docstring: the ANT1/ANT2 selector is a CI-V protocol
    # invariant supplied as caller data, not a map-declared ``sub`` --
    # registering them here would derive ``sub=None`` off the map's own
    # bare ``[0x12]`` tuple and fail every case.
    _GetterSpec("get_filter_shape", "_get_bcd_level"),
    _GetterSpec("get_ssb_tx_bandwidth", "_get_bcd_level"),
    _GetterSpec("get_main_sub_tracking", "_get_bool_value"),
    _GetterSpec("get_agc_time_constant", "_get_bcd_level"),
    _GetterSpec("get_repeater_tone", "_get_bool_value"),
    _GetterSpec("get_repeater_tsql", "_get_bool_value"),
    _GetterSpec("get_s_meter_sql_status", "_get_bool_value"),
    _GetterSpec("get_overflow_status", "_get_bool_value"),
    _GetterSpec("get_various_squelch", "_get_bool_value"),
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


# system.py's date/time/UTC-offset getters (MOR-2008 batch 1) are this
# file's second keystone case: like get_dual_watch above, they cannot
# register in MATCHER_BACKED_GETTERS, since they parse through their own
# module-level parse_system_date_response/parse_system_time_response/
# parse_utc_offset_response rather than _get_bcd_level/_get_bool_value.
# Each parser takes the map-derived prefix as a keyword (default: the
# shared IC-7610-shaped constant, kept only so pre-migration tests that
# never passed one still work) -- CoreRadio.get_system_date/time/utc_offset
# must pass their own profile's prefix through via _expect_shape, not rely
# on that default.
_DATE_TIME_UTC_CASES: tuple[tuple[str, str, bytes], ...] = (
    ("get_system_date", "get_system_date", b"\x00\x94"),
    ("get_system_time", "get_system_time", b"\x00\x95"),
    ("get_utc_offset", "get_utc_offset", b"\x00\x96"),
)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "radio_method,map_key,ic7300_prefix",
    _DATE_TIME_UTC_CASES,
    ids=[case[0] for case in _DATE_TIME_UTC_CASES],
)
async def test_date_time_utc_reply_prefix_comes_from_the_map(
    radio_method: str,
    map_key: str,
    ic7300_prefix: bytes,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """IC-7300's own extended-address prefix must reach the reply parser.

    Manually confirmed red for the half-done shape: pinning the parser's
    ``prefix`` default (IC-7610-shaped ``0x01 0x58``/``0x01 0x59``/
    ``0x01 0x62``) instead of ``_expect_shape``'s map-derived one makes the
    IC-7300 case below raise ``ValueError`` (prefix mismatch) on a reply
    that correctly echoes IC-7300's real ``0x00 0x94``/``0x95``/``0x96`` --
    exactly the "requests moved, replies not" failure mode this file
    exists to make impossible (plan §7): the request would already be
    right (``self._commands.<method>`` uses the map), only the reply
    parse would still expect IC-7610's bytes.
    """
    ic7300 = _civ_rig_configs()["IC-7300"].to_profile()
    command, sub, prefix = decode_wire_tuple(ic7300.command_map.get(map_key))
    assert (command, sub, prefix) == (0x1A, 0x05, ic7300_prefix)
    radio = CoreRadio("198.51.100.1", profile=ic7300)

    async def _fake_expect(civ: bytes, **kwargs: Any) -> Any:
        return CivFrame(
            to_addr=0xE0,
            from_addr=0x94,
            command=command,
            sub=sub,
            data=prefix + b"\x00" * 4,
        )

    monkeypatch.setattr(radio, "_send_civ_expect", _fake_expect)
    monkeypatch.setattr(radio, "_check_connected", lambda: None)

    # A real reply carrying IC-7300's own prefix must parse without error.
    await getattr(radio, radio_method)()

    # A reply carrying the WRONG (IC-7610-shaped) prefix must not parse as
    # if it were IC-7300's -- proves the prefix used is this profile's own,
    # not the parser's hardcoded default.
    wrong_prefix = b"\x01" + ic7300_prefix[1:]
    assert wrong_prefix != prefix

    async def _fake_expect_wrong(civ: bytes, **kwargs: Any) -> Any:
        return CivFrame(
            to_addr=0xE0,
            from_addr=0x94,
            command=command,
            sub=sub,
            data=wrong_prefix + b"\x00" * 4,
        )

    monkeypatch.setattr(radio, "_send_civ_expect", _fake_expect_wrong)
    with pytest.raises(ValueError, match="prefix mismatch"):
        await getattr(radio, radio_method)()
