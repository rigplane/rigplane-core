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

from rigplane.commands._codec import bcd_encode_value
from rigplane.commands._frame import decode_wire_tuple
from rigplane.commands.command_map import CommandMap
from rigplane.core.exceptions import CommandError
from rigplane.core.types import CivFrame, bcd_encode
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
    # commands/dsp.py's matcher-backed getters (MOR-2008 Steps 5..N, batch
    # 3). get_attenuator/get_preamp/get_digisel/get_nb/get_nr/get_ip_plus
    # are NOT included: each reaches its reply through
    # `runtime/radio.py: CoreRadio._send_civ_expect`, a transaction-
    # correlated wait with no command/sub matching at all -- never through
    # _get_bcd_level/_get_bool_value with a map-derived shape, the same
    # exclusion reason as commands/levels.py's get_rf_power/get_rf_gain/
    # get_af_level above.
    _GetterSpec("get_agc", "_get_bcd_level"),
    _GetterSpec("get_audio_peak_filter", "_get_bcd_level"),
    _GetterSpec("get_break_in", "_get_bcd_level"),
    _GetterSpec("get_manual_notch_width", "_get_bcd_level"),
    _GetterSpec("get_auto_notch", "_get_bool_value"),
    _GetterSpec("get_compressor", "_get_bool_value"),
    _GetterSpec("get_monitor", "_get_bool_value"),
    _GetterSpec("get_vox", "_get_bool_value"),
    _GetterSpec("get_manual_notch", "_get_bool_value"),
    _GetterSpec("get_twin_peak_filter", "_get_bool_value"),
    _GetterSpec("get_dial_lock", "_get_bool_value"),
    _GetterSpec("get_af_mute", "_get_bool_value"),
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
    try:
        await getattr(radio, spec.method)()
    except ValueError:
        # dsp.py's get_agc (MOR-2008 batch 3) validates its parsed value
        # against the profile's own declared `[agc] modes` domain AFTER
        # the mocked parser returns -- the fixed dummy value `0` this
        # fake parser always returns is legitimately outside that domain
        # for profiles that don't declare AGC OFF (IC-705/7300/7610/9700
        # all declare (1, 2, 3), no 0). `captured["shape"]` is already
        # set by the time that domain check runs (inside the mocked
        # parser, which returns before get_agc's own post-check), so the
        # shape assertion below still holds; only get_agc's business-rule
        # validation, irrelevant to this file's one job, is tolerated
        # here. No other registered getter raises for this dummy value.
        pass

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


# commands/scope.py's fifteen parse_scope_*_response functions (MOR-2008,
# this module's last residual step) are this file's third keystone case:
# each parses through its own module-level parser, never
# _get_bcd_level/_get_bool_value, so none can register in
# MATCHER_BACKED_GETTERS above. Unlike the date/time/utc case, no CI-V
# profile's declared [0x27, sub] tuple has ever diverged from another's for
# any scope key (commands/scope.py's module docstring), so there is no real
# pair of profiles to prove a mismatch with the way IC-7300 vs IC-7610 did
# for date/time/utc. Each case below instead builds a CommandMap that
# deliberately DIVERGES one scope key from its real, TOML-declared value --
# simulating the "a future profile disagrees with the hardcoded default"
# scenario the retrofit exists to survive -- and checks that
# ScopeRuntimeMixin's getter follows the mutated map, not the module's
# constant.
_SCOPE_DIVERGENT_SUB = 0x7E


def _mutate_command_map(
    cmd_map: CommandMap, key: str, wire: tuple[int, ...]
) -> CommandMap:
    return CommandMap({**{k: cmd_map.get(k) for k in cmd_map}, key: wire})


@dataclasses.dataclass(frozen=True)
class _ScopeGetterSpec:
    """One scope getter parsed by a dedicated ``parse_scope_*_response``.

    ``method`` is the ``ScopeRuntimeMixin`` getter to call (takes no
    arguments for every entry below). ``map_key`` is the ``CommandMap`` key
    its request builder resolves -- equal to ``method`` for every entry
    except ``get_scope_receiver``/``get_scope_dual``, whose builders are
    named ``get_scope_main_sub``/``get_scope_single_dual``
    (`commands/scope.py`). ``payload`` is a wire-valid ``CivFrame.data`` for
    that getter's reply -- sized and ranged to satisfy its parser's own
    length/value checks, independent of the command/sub shape this test
    varies.
    """

    method: str
    map_key: str
    payload: bytes


_SCOPE_GETTER_SPECS: tuple[_ScopeGetterSpec, ...] = (
    _ScopeGetterSpec("get_scope_receiver", "get_scope_main_sub", b"\x00"),
    _ScopeGetterSpec("get_scope_dual", "get_scope_single_dual", b"\x01"),
    _ScopeGetterSpec("get_scope_mode", "get_scope_mode", b"\x02"),
    _ScopeGetterSpec("get_scope_span", "get_scope_span", b"\x03"),
    _ScopeGetterSpec(
        "get_scope_edge", "get_scope_edge", bcd_encode_value(2, byte_count=1)
    ),
    _ScopeGetterSpec("get_scope_hold", "get_scope_hold", b"\x01"),
    _ScopeGetterSpec("get_scope_ref", "get_scope_ref", b"\x05\x00\x00"),
    _ScopeGetterSpec("get_scope_speed", "get_scope_speed", b"\x01"),
    _ScopeGetterSpec("get_scope_during_tx", "get_scope_during_tx", b"\x01"),
    _ScopeGetterSpec("get_scope_center_type", "get_scope_center_type", b"\x01"),
    _ScopeGetterSpec("get_scope_vbw", "get_scope_vbw", b"\x01"),
    _ScopeGetterSpec(
        "get_scope_fixed_edge",
        "get_scope_fixed_edge",
        bcd_encode_value(1, byte_count=1)
        + bcd_encode_value(1, byte_count=1)
        + bcd_encode(1_000_000)
        + bcd_encode(2_000_000),
    ),
    _ScopeGetterSpec("get_scope_rbw", "get_scope_rbw", b"\x01"),
)


def _scope_cases() -> list[tuple[str, _ScopeGetterSpec]]:
    return [
        (model, spec)
        for model in sorted(_civ_rig_configs())
        for spec in _SCOPE_GETTER_SPECS
    ]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "case",
    _scope_cases(),
    ids=[f"{model}-{spec.method}" for model, spec in _scope_cases()],
)
async def test_scope_getter_reply_shape_comes_from_the_map(
    case: tuple[str, _ScopeGetterSpec], monkeypatch: pytest.MonkeyPatch
) -> None:
    """A scope getter's reply matcher must follow ITS OWN profile's map entry.

    Manually confirmed red for the half-done shape (evidence in the PR):
    reverting any one of `runtime/_scope_runtime.py`'s sixteen
    ``self._expect_shape(get_scope_*)`` call sites to that getter's
    pre-migration hardcoded ``_CMD_SCOPE``/``_SUB_SCOPE_*`` constants makes
    this test's second assertion fail for every profile and that one
    getter -- the divergent-map reply would be rejected (module constant
    doesn't match the mutated map), while the stale-map reply would be
    wrongly accepted (module constant happens to equal the pre-mutation
    value) -- exactly the "requests moved, replies not" failure mode this
    file exists to make impossible (plan §7).
    """
    model, spec = case
    config = _civ_rig_configs()[model]
    profile = config.to_profile()
    cmd_map = profile.command_map
    assert cmd_map is not None, f"{model}: to_profile() produced no command_map"
    if not cmd_map.has(spec.map_key):
        pytest.skip(f"{model} does not declare {spec.map_key}")

    real_command, real_sub, real_prefix = decode_wire_tuple(cmd_map.get(spec.map_key))
    assert real_prefix == b"", (
        f"{model}:{spec.map_key} declares a data prefix ({real_prefix!r}) this "
        "test does not model -- every scope.py wire tuple observed so far is "
        "a bare [command, sub]."
    )
    assert real_sub != _SCOPE_DIVERGENT_SUB

    mutated_map = _mutate_command_map(
        cmd_map, spec.map_key, (real_command, _SCOPE_DIVERGENT_SUB)
    )
    mutated_profile = dataclasses.replace(profile, command_map=mutated_map)
    monkeypatch.setattr(CoreRadio, "_check_connected", lambda self: None)
    radio = CoreRadio("198.51.100.1", profile=mutated_profile)

    def _reply(sub: int) -> CivFrame:
        return CivFrame(
            to_addr=0xE0,
            from_addr=profile.civ_addr,
            command=real_command,
            sub=sub,
            data=spec.payload,
        )

    # A reply carrying the MUTATED map's own sub-command must parse without
    # error -- proves the shape checked is THIS profile's (here,
    # deliberately divergent) map entry, not the module's hardcoded default.
    async def _fake_expect_divergent(civ: bytes, **kwargs: Any) -> CivFrame:
        return _reply(_SCOPE_DIVERGENT_SUB)

    monkeypatch.setattr(radio, "_send_civ_expect", _fake_expect_divergent)
    await getattr(radio, spec.method)()

    # A reply carrying the key's REAL (pre-mutation) sub-command must NOT
    # parse as if it matched -- it no longer does, once this profile's own
    # map has been mutated away from it.
    async def _fake_expect_stale(civ: bytes, **kwargs: Any) -> CivFrame:
        return _reply(real_sub)

    monkeypatch.setattr(radio, "_send_civ_expect", _fake_expect_stale)
    with pytest.raises(ValueError, match="Not a scope response"):
        await getattr(radio, spec.method)()


@pytest.mark.asyncio
@pytest.mark.parametrize("model", sorted(_civ_rig_configs()))
async def test_get_scope_session_state_reply_shape_comes_from_the_map(
    model: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``get_scope_session_state`` parses TWO replies through TWO different
    map keys (``scope_on``/``scope_data_output``) in one call, so it cannot
    register as a single ``_ScopeGetterSpec`` row above. Mutates only the
    panel key (``scope_on``); the data-output reply stays a real,
    unmutated match throughout, proving the two replies are checked
    independently against their own keys rather than one shared shape.
    """
    config = _civ_rig_configs()[model]
    profile = config.to_profile()
    cmd_map = profile.command_map
    assert cmd_map is not None
    if not (cmd_map.has("scope_on") and cmd_map.has("scope_data_output")):
        pytest.skip(f"{model} does not declare scope_on/scope_data_output")

    panel_command, panel_sub, _ = decode_wire_tuple(cmd_map.get("scope_on"))
    output_command, output_sub, _ = decode_wire_tuple(cmd_map.get("scope_data_output"))
    assert panel_sub != _SCOPE_DIVERGENT_SUB

    mutated_map = _mutate_command_map(
        cmd_map, "scope_on", (panel_command, _SCOPE_DIVERGENT_SUB)
    )
    mutated_profile = dataclasses.replace(profile, command_map=mutated_map)
    monkeypatch.setattr(CoreRadio, "_check_connected", lambda self: None)
    radio = CoreRadio("198.51.100.1", profile=mutated_profile)

    call_count = {"n": 0}

    async def _fake_expect_divergent(civ: bytes, **kwargs: Any) -> CivFrame:
        call_count["n"] += 1
        if call_count["n"] == 1:
            return CivFrame(
                to_addr=0xE0,
                from_addr=profile.civ_addr,
                command=panel_command,
                sub=_SCOPE_DIVERGENT_SUB,
                data=b"\x01",
            )
        return CivFrame(
            to_addr=0xE0,
            from_addr=profile.civ_addr,
            command=output_command,
            sub=output_sub,
            data=b"\x01",
        )

    monkeypatch.setattr(radio, "_send_civ_expect", _fake_expect_divergent)
    assert await radio.get_scope_session_state() == (True, True)

    async def _fake_expect_stale_panel(civ: bytes, **kwargs: Any) -> CivFrame:
        return CivFrame(
            to_addr=0xE0,
            from_addr=profile.civ_addr,
            command=panel_command,
            sub=panel_sub,
            data=b"\x01",
        )

    monkeypatch.setattr(radio, "_send_civ_expect", _fake_expect_stale_panel)
    with pytest.raises(ValueError, match="Not a scope response"):
        await radio.get_scope_session_state()


@pytest.mark.asyncio
@pytest.mark.parametrize("model", sorted(_civ_rig_configs()))
async def test_set_scope_fixed_edge_reply_shape_comes_from_the_map(
    model: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``set_scope_fixed_edge`` makes no network round trip: it re-parses the
    frame it just built (`runtime/_scope_runtime.py`) to recover the
    resolved ``range_index``, a second call site for
    ``parse_scope_fixed_edge_response`` sharing ``get_scope_fixed_edge``'s
    map key. The request builder and this re-parse must read the SAME
    mutated map for the round trip to still succeed -- checked directly,
    with no mocked reply to manipulate (there is no network call to mock).
    """
    config = _civ_rig_configs()[model]
    profile = config.to_profile()
    cmd_map = profile.command_map
    assert cmd_map is not None
    if not cmd_map.has("get_scope_fixed_edge"):
        pytest.skip(f"{model} does not declare get_scope_fixed_edge")

    real_command, real_sub, _ = decode_wire_tuple(cmd_map.get("get_scope_fixed_edge"))
    assert real_sub != _SCOPE_DIVERGENT_SUB
    mutated_map = _mutate_command_map(
        cmd_map, "get_scope_fixed_edge", (real_command, _SCOPE_DIVERGENT_SUB)
    )
    mutated_profile = dataclasses.replace(profile, command_map=mutated_map)
    monkeypatch.setattr(CoreRadio, "_check_connected", lambda self: None)
    radio = CoreRadio("198.51.100.1", profile=mutated_profile)

    async def _fake_send_civ_raw(civ: bytes, **kwargs: Any) -> None:
        return None

    monkeypatch.setattr(radio, "_send_civ_raw", _fake_send_civ_raw)

    # The request builder reads this SAME mutated map, so the frame it
    # builds already carries sub=_SCOPE_DIVERGENT_SUB. A parser still
    # checking the module's hardcoded _SUB_SCOPE_FIXED_EDGE would reject
    # its own request's echo; deriving the shape from the same map instead
    # (this call site's retrofit) must not raise here.
    await radio.set_scope_fixed_edge(edge=1, start_hz=1_000_000, end_hz=2_000_000)


# MOR-2106: `commands/scope.py: scope_main_sub` (the WRITE builder) used to
# resolve "get_scope_main_sub" -- the READ key -- through `_build_from_map`,
# so a profile that declared only the getter (or declared the setter
# explicitly absent) could not refuse the write: the getter's declared bytes
# went out regardless. The two tests below build a profile explicitly missing
# `set_scope_main_sub` (never relying on any shipped `rigs/*.toml` staying
# that way -- sibling ticket MOR-2105 is editing `rigs/ic7300.toml`) and
# assert both that `CoreRadio.set_scope_receiver` raises `CommandError`
# naming the setter key AND that no frame reaches the transport.
def _drop_command_map_key(cmd_map: CommandMap, key: str) -> CommandMap:
    return CommandMap({k: cmd_map.get(k) for k in cmd_map if k != key})


@pytest.mark.asyncio
@pytest.mark.parametrize("model", sorted(_civ_rig_configs()))
async def test_scope_main_sub_setter_refuses_when_only_getter_declared(
    model: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A profile declaring `get_scope_main_sub` but NOT `set_scope_main_sub`
    must refuse `set_scope_receiver` before any CI-V bytes are sent.

    Pre-fix, this was false: `scope_main_sub` (the write builder) resolved
    "get_scope_main_sub" through `_build_from_map`, so the getter's declared
    wire bytes went out on write too, unconditionally.
    """
    config = _civ_rig_configs()[model]
    profile = config.to_profile()
    cmd_map = profile.command_map
    assert cmd_map is not None
    if not cmd_map.has("get_scope_main_sub"):
        pytest.skip(f"{model} does not declare get_scope_main_sub")

    mutated_map = _drop_command_map_key(cmd_map, "set_scope_main_sub")
    mutated_profile = dataclasses.replace(profile, command_map=mutated_map)
    monkeypatch.setattr(CoreRadio, "_check_connected", lambda self: None)
    radio = CoreRadio("198.51.100.1", profile=mutated_profile)

    frames: list[bytes] = []

    async def _record_send_civ_raw(civ: bytes, **kwargs: Any) -> None:
        frames.append(civ)
        return None

    monkeypatch.setattr(radio, "_send_civ_raw", _record_send_civ_raw)

    with pytest.raises(CommandError, match="set_scope_main_sub"):
        await radio.set_scope_receiver(1)

    assert frames == []


@pytest.mark.asyncio
@pytest.mark.parametrize("model", sorted(_civ_rig_configs()))
async def test_scope_main_sub_setter_refuses_when_declared_absent(
    model: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Stronger form of the test above: `set_scope_main_sub` is declared
    ABSENT (D1 state 2, `commands/bound.py: BoundCommands._refusal_for`)
    rather than merely missing (state 3) -- this cannot pass by accident
    off a KeyError from an unrelated typo, because the refusal message must
    quote the declared source.
    """
    config = _civ_rig_configs()[model]
    profile = config.to_profile()
    cmd_map = profile.command_map
    assert cmd_map is not None
    if not cmd_map.has("get_scope_main_sub"):
        pytest.skip(f"{model} does not declare get_scope_main_sub")

    mutated_map = _drop_command_map_key(cmd_map, "set_scope_main_sub")
    source = "test fixture (MOR-2106): scope MAIN/SUB write declared absent"
    mutated_profile = dataclasses.replace(
        profile,
        command_map=mutated_map,
        absent_command_sources={
            **profile.absent_command_sources,
            "set_scope_main_sub": source,
        },
    )
    monkeypatch.setattr(CoreRadio, "_check_connected", lambda self: None)
    radio = CoreRadio("198.51.100.1", profile=mutated_profile)

    frames: list[bytes] = []

    async def _record_send_civ_raw(civ: bytes, **kwargs: Any) -> None:
        frames.append(civ)
        return None

    monkeypatch.setattr(radio, "_send_civ_raw", _record_send_civ_raw)

    with pytest.raises(CommandError, match="set_scope_main_sub") as excinfo:
        await radio.set_scope_receiver(1)

    assert source in str(excinfo.value)
    assert frames == []
