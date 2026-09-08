"""Every dispatched set command reads back the field it sets, through one path.

Owner ruling R39/R42 (MOR-2425): a command that changes something on the
radio is followed by a read of that same thing. The mechanism is
``CommandIntent.expected_observations`` -- derived once, in
``core/command_service.py``, from ``_command_target`` -- and fired by
``web/radio_poller.py: RadioPoller._request_post_write_readback`` as a
``USER``-priority ``ensure_fresh``. Legacy ``Command`` dataclasses reach it
by declaring which canonical command name they are
(``runtime/_poller_types.py: LEGACY_COMMAND_NAMES``); they no longer carry
their own field table.
"""

from __future__ import annotations

import ast
import time
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from rigplane.capabilities import (
    CAP_AGC,
    CAP_BREAK_IN,
    CAP_COMPRESSOR,
    CAP_CW,
    CAP_FILTER_SHAPE,
    CAP_NOTCH,
    CAP_SCOPE,
    CAP_TUNER,
    CAP_VOX,
)
from rigplane.core.acquisition_scheduler import (
    AcquisitionPriority,
    AcquisitionScheduler,
)
from rigplane.core.command_service import (
    CommandExecutionResult,
    CommandService,
    expected_observations_for_command,
)
from rigplane.core.state_pipeline_contracts import (
    CommandIntent,
    FieldPath,
    Observation,
    SourceMetadata,
)
from rigplane.profiles import resolve_radio_profile
from rigplane.radio_state import RadioState
from rigplane.runtime._poller_types import LEGACY_COMMAND_NAMES
from rigplane.web.handlers import ControlHandler
from rigplane.web.radio_poller import (
    CommandQueue,
    RadioPoller,
    SetAgc,
    SetAgcTimeConstant,
    SetAntiVoxGain,
    SetAutoNotch,
    SetBreakIn,
    SetCompressor,
    SetCompressorLevel,
    SetCwPitch,
    SetDataMode,
    SetFilterShape,
    SetFreq,
    SetKeySpeed,
    SetManualNotch,
    SetManualNotchWidth,
    SetMicGain,
    SetMode,
    SetMonitor,
    SetMonitorGain,
    SetNBLevel,
    SetNotchFilter,
    SetNRLevel,
    SetPbtInner,
    SetPbtOuter,
    SetRitFrequency,
    SetRitStatus,
    SetRitTxStatus,
    SetScopeCenterType,
    SetScopeDual,
    SetScopeDuringTx,
    SetScopeEdge,
    SetScopeHold,
    SetScopeMode,
    SetScopeRef,
    SetScopeSpan,
    SetScopeSpeed,
    SetScopeVbw,
    SetToneFreq,
    SetTsqlFreq,
    SetTunerStatus,
    SetTwinPeak,
    SetVox,
    SetVoxDelay,
    SetVoxGain,
)

pytestmark = pytest.mark.usefixtures("observed_rx_dispatch_premise")

_SRC = Path(__file__).resolve().parents[1] / "src" / "rigplane"


class _NoopCommandExecutor:
    async def execute(self, intent: object) -> CommandExecutionResult:
        del intent
        return CommandExecutionResult()


# ---------------------------------------------------------------------------
# Arm enumeration: every ``case Set*`` arm is classified, exactly once
# ---------------------------------------------------------------------------

# Arms whose write has no field in the state model: nothing to read back.
# Established by ``test_no_observable_field_arms_name_no_state_model_field``
# below, which greps the declared field specs rather than trusting this list.
_NO_OBSERVABLE_FIELD: dict[str, str] = {
    # Clock/calendar writes.
    "SetSystemDate": "system_date",
    "SetSystemTime": "system_time",
    "SetUtcOffset": "utc_offset",
    # CI-V link configuration, not radio state.
    "SetCivTransceive": "civ_transceive",
    # Band-stack recall acts through freq/mode; it has no field of its own.
    "SetBand": "band",
    "SetBsr": "bsr",
    "SetMemoryContents": "memory_contents",
    "SetMemoryMode": "memory_mode",
    # Panel-shortcut preferences.
    "SetQuickSplit": "quick_split",
    "SetQuickDualWatch": "quick_dual_watch",
    "SetXfcStatus": "xfc_status",
    # MOR-2425 PR-1b: the descriptor's own target
    # (``global.slow_state.civ_output_ant``) names a field neither
    # ``state_pipeline_contracts.py`` nor any CI-V decode branch in
    # ``runtime/_civ_rx.py`` declares -- there is nothing to read back to.
    "SetCivOutputAnt": "civ_output_ant",
    # No state-model field anywhere for these three (grepped
    # ``state_pipeline_contracts.py`` and ``runtime/_civ_rx.py`` directly).
    "SetAcc1ModLevel": "acc1_mod_level",
    "SetUsbModLevel": "usb_mod_level",
    "SetLanModLevel": "lan_mod_level",
}

# Arms this PR leaves uncovered, checked against IC-7300 and FTX-1 --
# CLAUDE.md's live-bench pair -- via each profile's
# ``[state_acquisition.capabilities]`` table (not just a
# ``state_pipeline_contracts.py`` field spec, which only says the field
# exists in the schema, not that any profile has wired it up for
# acquisition). A name absent from this set and from ``_NO_OBSERVABLE_FIELD``
# is covered by ``LEGACY_COMMAND_NAMES``.
_PENDING_LATER_PR: frozenset[str] = frozenset(
    {
        # The two scope-display leaves MOR-2425 PR-3 could not fold into the
        # one read-back path; the other ten, and the CW keyer trio, are
        # covered below.
        #
        # rbw: ``rigs/ic7300.toml``'s ``[state_acquisition.capabilities]``
        # declares twelve ``scope_controls.global.display.*`` leaves, and
        # ``rbw`` is not one of them (of the profiles in ``rigs/``, only
        # ``ic7610.toml`` declares it), so ``AcquisitionScheduler
        # .ensure_fresh`` answers UNAVAILABLE and queues nothing on the
        # live-bench profile.
        #
        # fixed_edge: the shared acquisition resolver (``runtime/
        # _state_queries.py: acquisition_query_resolver_for_profile``) builds
        # ``commands/scope.py: get_scope_fixed_edge`` with its
        # ``range_index=1, edge=1`` defaults -- the slot-less re-read the
        # ``SetScopeFixedEdge`` arm's own MOR-662 comment says comes back
        # with an unrelated slot's data.
        #
        # Both arms keep their inline ``RadioPoller._reconfirm_scope_field``.
        "SetScopeRbw",
        "SetScopeFixedEdge",
        # TX audio / modulation family: mic_gain, monitor(_gain),
        # compressor(_level), vox(_gain/_delay/anti) are covered (see
        # below). These seven have a state-model field but no declared
        # acquisition capability on IC-7300 or FTX-1.
        "SetAfMute",
        "SetSsbTxBandwidth",
        "SetDriveGain",
        "SetDataOffModInput",
        "SetData1ModInput",
        "SetData2ModInput",
        "SetData3ModInput",
        # Antenna family: rx_antenna_1/rx_antenna_2 have a state-model
        # field (``global.slow_state.rx_antenna_1``/``_2``) but no profile
        # checked (ic7300/ic7610/ic9700/ic705/ftx1) declares it as an
        # acquisition capability -- ``ensure_fresh`` would resolve
        # UNAVAILABLE. set_tuner_status is covered (see below); its
        # descriptor's target bug (pointed at ``global.slow_state
        # .tuner_status``, which no profile declares either) is fixed in
        # ``core/command_dispatch.py`` as part of this PR.
        "SetAntenna1",
        "SetAntenna2",
        "SetRxAntenna",
        "SetRxAntennaAnt1",
        "SetRxAntennaAnt2",
        # Remaining RX controls: agc is covered (see below). if_shift is
        # NOT (review B2, MOR-2425 PR-1b): on a real FTX-1 the command
        # queue is drained by ``backends/yaesu_cat/poller.py:
        # YaesuCatPoller`` (``YaesuCatRadio.create_state_poller`` returns
        # it), not ``RadioPoller`` -- ``YaesuCatPoller`` has its own
        # ``match cmd:`` with no ``ensure_fresh`` call anywhere in the
        # file, so ``RadioPoller._execute``'s ``SetIfShift`` arm is never
        # reached in production. ``IcomRadio`` has no ``set_if_shift``
        # method, so the arm cannot be reached from that side either.
        # apf/audio_peak_filter, digisel_shift, nb_depth, nb_width have a
        # state-model field but no declared acquisition capability on
        # IC-7300 or FTX-1. repeater_tone/repeater_tsql are the one case
        # where NEITHER profile has both halves: IC-7300 declares the
        # write feature ("repeater_tone"/"tsql") but not the acquisition
        # capability; FTX-1 declares the acquisition capability but not
        # the write feature, and ``YaesuCatRadio`` has no
        # ``set_repeater_tone``/``set_repeater_tsql`` method for
        # ``RadioPoller._execute`` to call.
        "SetIfShift",
        "SetApf",
        "SetAudioPeakFilter",
        "SetDigiselShift",
        "SetNbDepth",
        "SetNbWidth",
        "SetRepeaterTone",
        "SetRepeaterTsql",
        # Global/panel settings: dial_lock is NOT covered, for the same
        # reason as if_shift above -- FTX-1's real dispatcher is
        # ``YaesuCatPoller``, which has no readback path. Icom profiles
        # (checked ic705/ic7300/ic7610/ic9700/x6100/x6200/tx500) declare
        # "dial_lock" as a write feature, but none declares it in
        # ``[state_acquisition.capabilities]`` -- only ftx1.toml does.
        # tuning_step/ref_adjust/dash_ratio/main_sub_tracking/dual_watch
        # have a state-model field but no declared acquisition capability
        # on IC-7300 or FTX-1.
        "SetDialLock",
        "SetTuningStep",
        "SetRefAdjust",
        "SetDashRatio",
        "SetMainSubTracking",
        "SetDualWatch",
    }
)


def _set_command_arms() -> frozenset[str]:
    """Every ``case Set*`` arm in ``RadioPoller._execute``'s ``match cmd:``."""
    source = (_SRC / "web" / "radio_poller.py").read_text()
    tree = ast.parse(source)
    arms: set[str] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Match):
            continue
        for case in node.cases:
            pattern = case.pattern
            cls = None
            if isinstance(pattern, ast.MatchClass) and isinstance(
                pattern.cls, ast.Name
            ):
                cls = pattern.cls.id
            if cls is not None and cls.startswith("Set"):
                arms.add(cls)
    return frozenset(arms)


def _web_ingress_names() -> dict[str, set[str]]:
    """Legacy dataclass name -> the ``set_*`` names that enqueue it.

    Parsed from ``web/handlers/control.py``'s ``_enqueue_rc_*`` group
    handlers. Those handlers are not the file's only construction site for
    a legacy dataclass (``_ro_set_tuner_status`` builds one inline), so a
    dataclass missing from this mapping is not proof it is never enqueued.
    """
    source = (_SRC / "web" / "handlers" / "control.py").read_text()
    tree = ast.parse(source)
    handler = next(
        node
        for node in tree.body
        if isinstance(node, ast.ClassDef) and node.name == "ControlHandler"
    )
    mapping: dict[str, set[str]] = {}
    for fn in handler.body:
        if not isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        if not fn.name.startswith("_enqueue_rc_"):
            continue
        for node in ast.walk(fn):
            if not isinstance(node, ast.match_case):
                continue
            names: list[str] = []

            def collect(pattern: ast.pattern, into: list[str] = names) -> None:
                if isinstance(pattern, ast.MatchValue) and isinstance(
                    pattern.value, ast.Constant
                ):
                    into.append(str(pattern.value.value))
                elif isinstance(pattern, ast.MatchOr):
                    for alternative in pattern.patterns:
                        collect(alternative, into)

            collect(node.pattern)
            if not names:
                continue
            for call in ast.walk(node):
                if not isinstance(call, ast.Call):
                    continue
                if not isinstance(call.func, ast.Attribute):
                    continue
                if call.func.attr not in ("put", "put_ordered"):
                    continue
                if not call.args:
                    continue
                built = call.args[0]
                if isinstance(built, ast.Call) and isinstance(built.func, ast.Name):
                    mapping.setdefault(built.func.id, set()).update(names)
    return mapping


def test_every_set_command_arm_is_classified() -> None:
    arms = _set_command_arms()
    covered = {cls.__name__ for cls in LEGACY_COMMAND_NAMES}
    no_field = frozenset(_NO_OBSERVABLE_FIELD)
    classified = covered | no_field | _PENDING_LATER_PR

    assert arms - classified == set(), (
        "new Set* dispatch arm with no readback classification: add it to "
        "LEGACY_COMMAND_NAMES, or to _NO_OBSERVABLE_FIELD / _PENDING_LATER_PR "
        "here with a reason"
    )
    assert classified - arms == set(), (
        "classification names a dispatch arm that is gone"
    )
    assert covered & no_field == set()
    assert covered & _PENDING_LATER_PR == set()
    assert no_field & _PENDING_LATER_PR == set()


def test_no_observable_field_arms_name_no_state_model_field() -> None:
    """The "nothing to read back" claim, checked against the declared specs.

    ``core/state_pipeline_contracts.py`` is where every readable field is
    declared; a name that appears there as a quoted field name is readable
    and does not belong in ``_NO_OBSERVABLE_FIELD``.
    """
    declared = (_SRC / "core" / "state_pipeline_contracts.py").read_text()
    readable = [
        f"{arm} -> {field}"
        for arm, field in _NO_OBSERVABLE_FIELD.items()
        if f'"{field}"' in declared
    ]
    assert readable == []


# set_tuner_status is descriptor-backed: its target only resolves once its
# ``bind`` requirement (``value``) is present, unlike every other name here
# which needs only ``receiver`` (MOR-2425 PR-1b).
_TARGET_RESOLUTION_EXTRA_PARAMS: dict[str, dict[str, Any]] = {
    "set_tuner_status": {"value": 1},
}


def test_every_covered_command_resolves_to_at_least_one_field_path() -> None:
    for command_type, name in LEGACY_COMMAND_NAMES.items():
        params = {"receiver": 0, **_TARGET_RESOLUTION_EXTRA_PARAMS.get(name, {})}
        paths = expected_observations_for_command(name, params)
        assert paths, f"{command_type.__name__} -> {name!r} resolves to no field path"


def test_legacy_command_names_match_the_web_ingress() -> None:
    """The declared name is one the web ingress actually dispatches as.

    Keeps ``LEGACY_COMMAND_NAMES`` from drifting away from
    ``control.py``'s ``_enqueue_rc_*`` handlers, which is where the
    dataclass is built.
    """
    ingress = _web_ingress_names()
    unreachable = {
        "SetAttenuator",  # descriptor-backed name; see the PR body
        # Built inline in ``_ro_set_tuner_status``, not an ``_enqueue_rc_*``
        # arm (MOR-2425 PR-1b).
        "SetTunerStatus",
    }
    for command_type, name in LEGACY_COMMAND_NAMES.items():
        if command_type.__name__ in unreachable:
            continue
        assert command_type.__name__ in ingress, (
            f"{command_type.__name__} is not enqueued by any _enqueue_rc_* arm"
        )
        assert name in ingress[command_type.__name__], (
            f"{command_type.__name__} declares {name!r} but control.py dispatches "
            f"it as {sorted(ingress[command_type.__name__])}"
        )


# ---------------------------------------------------------------------------
# Behaviour: one USER-priority readback of the right path, per dispatch
# ---------------------------------------------------------------------------


_SETTERS: tuple[str, ...] = (
    "set_freq",
    "set_mode",
    "set_data_mode",
    "set_tone_freq",
    "set_tsql_freq",
    "set_nr_level",
    "set_nb_level",
    "set_notch_filter",
    "set_manual_notch_width",
    "set_auto_notch",
    "set_manual_notch",
    "set_agc_time_constant",
    "set_filter_shape",
    "set_twin_peak_filter",
    "set_pbt_inner",
    "set_pbt_outer",
    "set_rit_frequency",
    "set_rit_status",
    "set_rit_tx_status",
)


# IC-7300-witnessed additions (MOR-2425 PR-1b): agc, tuner_status, and the
# TX audio/modulation nine dispatch through ``IcomRadio``.
_IC7300_SETTERS: tuple[str, ...] = (
    "set_agc",
    "set_tuner_status",
    "set_mic_gain",
    "set_compressor",
    "set_compressor_level",
    "set_monitor",
    "set_monitor_gain",
    "set_vox",
    "set_vox_gain",
    "set_anti_vox_gain",
    "set_vox_delay",
)


# MOR-2425 PR-3: the CW keyer trio and the ten scope-display leaves folded off
# the two bespoke confirms (``RadioPoller._confirm_global_operator_write``,
# deleted, and ``RadioPoller._reconfirm_scope_field``, kept only for
# fixed_edge/rbw and the scope-receiver switch). Each entry pairs the arm's
# setter with the getter the bespoke confirm used to await, so a witness can
# assert the inline read is gone.
_FOLDED_SETTER_GETTER: tuple[tuple[str, str], ...] = (
    ("set_cw_pitch", "get_cw_pitch"),
    ("set_key_speed", "get_key_speed"),
    ("set_break_in", "get_break_in"),
    ("set_scope_during_tx", "get_scope_during_tx"),
    ("set_scope_center_type", "get_scope_center_type"),
    ("set_scope_edge", "get_scope_edge"),
    ("set_scope_vbw", "get_scope_vbw"),
    ("set_scope_dual", "get_scope_dual"),
    ("set_scope_mode", "get_scope_mode"),
    ("set_scope_span", "get_scope_span"),
    ("set_scope_speed", "get_scope_speed"),
    ("set_scope_ref", "get_scope_ref"),
    ("set_scope_hold", "get_scope_hold"),
)
_FOLDED_SETTERS: tuple[str, ...] = tuple(setter for setter, _ in _FOLDED_SETTER_GETTER)
_FOLDED_GETTERS: tuple[str, ...] = tuple(getter for _, getter in _FOLDED_SETTER_GETTER)


def test_mocked_setters_exist_on_the_real_backend() -> None:
    """A renamed backend method must not survive as a silent mock attribute."""
    from rigplane.runtime.radio import IcomRadio

    missing = [
        name
        for name in (
            *_SETTERS,
            *_IC7300_SETTERS,
            *_FOLDED_SETTERS,
            *_FOLDED_GETTERS,
        )
        if not hasattr(IcomRadio, name)
    ]
    assert missing == []


def _poller(
    model: str = "IC-7300",
    *,
    setters: tuple[str, ...] = _SETTERS,
    getters: tuple[str, ...] = (),
    capabilities: frozenset[str] = frozenset({CAP_NOTCH, CAP_FILTER_SHAPE}),
) -> tuple[RadioPoller, AcquisitionScheduler]:
    profile = resolve_radio_profile(model=model)
    assert profile.state_acquisition is not None
    radio = MagicMock()
    radio.profile = profile
    radio.model = profile.model
    radio.capabilities = set(capabilities)
    radio._radio_state = SimpleNamespace(active="MAIN")
    for setter in setters:
        setattr(radio, setter, AsyncMock())
    # Explicit AsyncMocks, not MagicMock's auto-attributes: an awaited
    # MagicMock() result raises TypeError, which the deleted confirm helpers
    # swallowed -- so a bare MagicMock getter would let a surviving inline
    # read pass as "not called".
    for getter in getters:
        setattr(radio, getter, AsyncMock(return_value=0))
    scheduler = AcquisitionScheduler(profile=profile.state_acquisition)
    radio._acquisition_scheduler = scheduler
    poller = RadioPoller(radio, CommandQueue(), radio_state=RadioState())
    return poller, scheduler


def _readback_paths(scheduler: AcquisitionScheduler) -> set[FieldPath]:
    return {
        path
        for request in scheduler.pending_requests()
        if request.reason == "post_write_readback"
        for path in request.paths
    }


def _readback_priorities(scheduler: AcquisitionScheduler) -> set[AcquisitionPriority]:
    return {
        request.priority
        for request in scheduler.pending_requests()
        if request.reason == "post_write_readback"
    }


_RX_FAMILY: tuple[tuple[Any, str], ...] = (
    (SetNRLevel(level=5, receiver=0), "receiver.main.operator_controls.nr_level"),
    (SetNBLevel(level=5, receiver=0), "receiver.main.operator_controls.nb_level"),
    (
        SetNotchFilter(level=5, receiver=0),
        "receiver.main.operator_controls.notch_filter",
    ),
    (
        SetManualNotchWidth(value=1, receiver=0),
        "receiver.main.operator_controls.manual_notch_width",
    ),
    (SetAutoNotch(on=True, receiver=0), "receiver.main.operator_toggles.auto_notch"),
    (
        SetManualNotch(on=True, receiver=0),
        "receiver.main.operator_toggles.manual_notch",
    ),
    (
        SetAgcTimeConstant(value=3, receiver=0),
        "receiver.main.operator_controls.agc_time_constant",
    ),
    (
        SetFilterShape(shape=1, receiver=0),
        "receiver.main.operator_controls.filter_shape",
    ),
    (
        SetTwinPeak(on=True, receiver=0),
        "receiver.main.operator_toggles.twin_peak_filter",
    ),
    (SetPbtInner(level=128, receiver=0), "receiver.main.operator_controls.pbt_inner"),
    (SetPbtOuter(level=128, receiver=0), "receiver.main.operator_controls.pbt_outer"),
    (SetRitFrequency(freq=100), "global.operator_controls.rit_freq"),
    (SetRitStatus(on=True), "global.tx_state.rit_on"),
    (SetRitTxStatus(on=True), "global.tx_state.rit_tx"),
)


@pytest.mark.parametrize(
    ("command", "path"), _RX_FAMILY, ids=[type(c).__name__ for c, _ in _RX_FAMILY]
)
@pytest.mark.asyncio
async def test_rx_family_dispatch_requests_exactly_one_user_readback(
    command: Any, path: str
) -> None:
    poller, scheduler = _poller()

    await poller._execute(command)  # noqa: SLF001

    assert _readback_paths(scheduler) == {FieldPath.parse(path)}
    assert _readback_priorities(scheduler) == {AcquisitionPriority.USER}


_TABLE_SUCCESSION: tuple[tuple[Any, tuple[str, ...]], ...] = (
    (SetFreq(freq=14_000_000, receiver=0), ("receiver.main.active.freq_mode.freq_hz",)),
    (SetMode(mode="USB", receiver=0), ("receiver.main.active.freq_mode.mode",)),
    (SetDataMode(mode=1, receiver=0), ("receiver.main.active.freq_mode.data_mode",)),
    (
        SetToneFreq(freq_hz=8850, receiver=0),
        ("receiver.main.operator_controls.tone_freq",),
    ),
    (
        SetTsqlFreq(freq_hz=8850, receiver=0),
        ("receiver.main.operator_controls.tsql_freq",),
    ),
)


@pytest.mark.parametrize(
    ("command", "paths"),
    _TABLE_SUCCESSION,
    ids=[type(c).__name__ for c, _ in _TABLE_SUCCESSION],
)
@pytest.mark.asyncio
async def test_readback_paths_the_deleted_table_used_are_unchanged(
    command: Any, paths: tuple[str, ...]
) -> None:
    """The slot-bearing spellings the deleted read-back table used survive.

    ``_command_target`` returns slot-less ``receiver.<n>.freq_mode.<name>``;
    the declared, observable path is ``receiver.<id>.active.freq_mode.<name>``.
    ``observable_field_path`` is what reconciles them.
    """
    poller, scheduler = _poller()

    await poller._execute(command)  # noqa: SLF001

    assert _readback_paths(scheduler) >= {FieldPath.parse(path) for path in paths}


@pytest.mark.asyncio
async def test_sub_receiver_write_reads_back_the_sub_path() -> None:
    poller, scheduler = _poller(model="IC-7610")

    await poller._execute(SetNRLevel(level=5, receiver=1))  # noqa: SLF001

    assert _readback_paths(scheduler) == {
        FieldPath.parse("receiver.sub.operator_controls.nr_level")
    }


# ---------------------------------------------------------------------------
# MOR-2425 PR-1b: RX controls / global panel / TX audio / scope-display,
# witnessed on the real profile that actually declares each field.
# ---------------------------------------------------------------------------


_IC7300_FAMILY: tuple[tuple[Any, str], ...] = (
    (SetAgc(mode=2, receiver=0), "receiver.main.operator_controls.agc"),
    (SetTunerStatus(value=1), "global.operator_controls.tuner_status"),
    (SetMicGain(level=128), "global.operator_controls.mic_gain"),
    (SetMonitor(on=True), "global.tx_state.monitor_on"),
    (SetMonitorGain(level=128), "global.operator_controls.monitor_gain"),
    (SetCompressor(on=True), "global.tx_state.compressor_on"),
    (SetCompressorLevel(level=128), "global.operator_controls.compressor_level"),
    (SetVox(on=True), "global.tx_state.vox_on"),
    (SetVoxGain(level=128), "global.operator_controls.vox_gain"),
    (SetAntiVoxGain(level=128), "global.operator_controls.anti_vox_gain"),
    (SetVoxDelay(level=5), "global.operator_controls.vox_delay"),
)


@pytest.mark.parametrize(
    ("command", "path"),
    _IC7300_FAMILY,
    ids=[type(c).__name__ for c, _ in _IC7300_FAMILY],
)
@pytest.mark.asyncio
async def test_ic7300_family_dispatch_requests_exactly_one_user_readback(
    command: Any, path: str
) -> None:
    """agc, tuner_status, and TX audio, on the real IC-7300 profile."""
    poller, scheduler = _poller(
        setters=_IC7300_SETTERS,
        capabilities=frozenset({CAP_AGC, CAP_TUNER, CAP_COMPRESSOR, CAP_VOX}),
    )

    await poller._execute(command)  # noqa: SLF001

    assert _readback_paths(scheduler) == {FieldPath.parse(path)}
    assert _readback_priorities(scheduler) == {AcquisitionPriority.USER}


# ---------------------------------------------------------------------------
# MOR-2425 PR-3: the two bespoke confirms, folded into the one path
# ---------------------------------------------------------------------------


_FOLDED_FAMILY: tuple[tuple[Any, str], ...] = (
    (SetCwPitch(value=650), "global.operator_controls.cw_pitch"),
    (SetKeySpeed(speed=24), "global.operator_controls.key_speed"),
    (SetBreakIn(mode=1), "global.operator_controls.break_in"),
    (SetScopeDuringTx(on=True), "scope_controls.global.display.during_tx"),
    (
        SetScopeCenterType(center_type=1),
        "scope_controls.global.display.center_type",
    ),
    (SetScopeEdge(edge=2), "scope_controls.global.display.edge"),
    (SetScopeVbw(narrow=True), "scope_controls.global.display.vbw_narrow"),
    (SetScopeDual(dual=True), "scope_controls.global.display.dual"),
    (SetScopeMode(mode=1), "scope_controls.global.display.mode"),
    (SetScopeSpan(span=6), "scope_controls.global.display.span"),
    (SetScopeSpeed(speed=2), "scope_controls.global.display.speed"),
    (SetScopeRef(ref=5), "scope_controls.global.display.ref_db"),
    (SetScopeHold(on=True), "scope_controls.global.display.hold"),
)

_FOLDED_CAPABILITIES = frozenset({CAP_CW, CAP_BREAK_IN, CAP_SCOPE})


@pytest.mark.parametrize(
    ("command", "path"),
    _FOLDED_FAMILY,
    ids=[type(c).__name__ for c, _ in _FOLDED_FAMILY],
)
@pytest.mark.asyncio
async def test_folded_family_dispatch_requests_exactly_one_user_readback(
    command: Any, path: str
) -> None:
    """One scheduler readback, the right path, USER -- and no inline GET.

    The CW keyer trio used to await ``RadioPoller._confirm_global_operator_write``'s
    direct backend getter and these ten scope leaves
    ``RadioPoller._reconfirm_scope_field``'s. Both are gone from these arms:
    the getters must not be awaited during ``_execute``.
    """
    poller, scheduler = _poller(
        setters=_FOLDED_SETTERS,
        getters=_FOLDED_GETTERS,
        capabilities=_FOLDED_CAPABILITIES,
    )

    await poller._execute(command)  # noqa: SLF001

    assert _readback_paths(scheduler) == {FieldPath.parse(path)}
    assert _readback_priorities(scheduler) == {AcquisitionPriority.USER}
    awaited = [
        getter
        for getter in _FOLDED_GETTERS
        if getattr(poller._radio, getter).await_count  # noqa: SLF001
    ]
    assert awaited == []


@pytest.mark.parametrize(
    ("command", "path"),
    _FOLDED_FAMILY,
    ids=[type(c).__name__ for c, _ in _FOLDED_FAMILY],
)
@pytest.mark.asyncio
async def test_folded_family_queues_the_written_path_exactly_once(
    command: Any, path: str
) -> None:
    """The written path appears once across every queued request.

    Guards the double-read the PR-1b review found: a second, scheduler-queued
    ``ensure_fresh`` alongside an inline confirm for the same field.
    """
    poller, scheduler = _poller(
        setters=_FOLDED_SETTERS,
        getters=_FOLDED_GETTERS,
        capabilities=_FOLDED_CAPABILITIES,
    )

    await poller._execute(command)  # noqa: SLF001

    queued = [
        queued_path
        for request in scheduler.pending_requests()
        for queued_path in request.paths
    ]
    assert queued == [FieldPath.parse(path)]


@pytest.mark.asyncio
async def test_cw_pitch_readback_applies_the_value_the_radio_reports() -> None:
    """A mismatched readback now lands in the store.

    ``_confirm_global_operator_write`` compared the readback against the
    requested value and discarded anything else, so the store kept its
    pre-write value. The generic path applies whatever the radio reports.
    The readback-path assertion is what discriminates a reinstated bespoke
    confirm; the store-value assertion pins what the generic path does with
    the reported value.

    The lifecycle assertion below is a guard, not a discriminator: nothing
    confirms ``SetCwPitch`` today either way, because ``set_cw_pitch`` has no
    command descriptor (``command_dispatch.py: command_descriptor`` returns
    ``None``), so no scoped ``CommandIntent`` -- and hence no pending
    overlay -- is ever built for it.
    """
    path = FieldPath.parse("global.operator_controls.cw_pitch")
    poller, scheduler = _poller(
        setters=_FOLDED_SETTERS,
        getters=_FOLDED_GETTERS,
        capabilities=_FOLDED_CAPABILITIES,
    )
    store = poller._state_store  # noqa: SLF001
    generation = store.begin_provider_generation()
    store.apply(
        Observation(
            path=path,
            value=600,
            source=SourceMetadata(source="poll_response", provider="test"),
            timestamp_monotonic=time.monotonic(),
            provider_generation=generation,
        )
    )
    command_service = CommandService(executor=_NoopCommandExecutor(), state_store=store)

    await poller._execute(SetCwPitch(value=600))  # noqa: SLF001

    assert _readback_paths(scheduler) == {path}
    # The radio answers 650, not the 600 that was asked for.
    command_service.apply_observation(
        Observation(
            path=path,
            value=650,
            source=SourceMetadata(source="poll_response", provider="test"),
            timestamp_monotonic=time.monotonic(),
            provider_generation=generation,
        )
    )

    assert store.snapshot().field(path).value == 650
    assert [
        event.state
        for event in command_service.lifecycle_events()
        if event.state in ("confirmed", "reconciled")
    ] == []


@pytest.mark.asyncio
async def test_rigctld_set_level_and_the_web_slider_read_back_the_same_field() -> None:
    """The nr_level asymmetry the census recorded is closed.

    rigctld builds a ``CommandIntent`` for ``set_level``; the web slider
    enqueues the legacy ``SetNRLevel``. Both now request the same path.
    """
    intent_poller, intent_scheduler = _poller()
    legacy_poller, legacy_scheduler = _poller()
    intent = CommandIntent(
        id="rigctld-1",
        name="set_level",
        params={"level_name": "NR", "value": 5, "receiver": 0},
        source="rigctld",
        target=FieldPath.receiver("0", "operator_controls", "nr_level"),
        expected_observations=(
            FieldPath.receiver("0", "operator_controls", "nr_level"),
        ),
    )

    intent_poller._request_post_write_readback(intent)  # noqa: SLF001
    await legacy_poller._execute(SetNRLevel(level=5, receiver=0))  # noqa: SLF001

    assert _readback_paths(intent_scheduler) == _readback_paths(legacy_scheduler)
    assert _readback_paths(legacy_scheduler) == {
        FieldPath.parse("receiver.main.operator_controls.nr_level")
    }


# ---------------------------------------------------------------------------
# One readback per FLUSHED command, not per pointer event
# ---------------------------------------------------------------------------


class _QueueRecorder:
    def __init__(self) -> None:
        self.items: list[Any] = []

    def put(self, item: Any, **_: Any) -> None:
        self.items.append(item)


@pytest.mark.asyncio
async def test_coalesced_slider_burst_produces_one_dispatch_and_one_readback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Ten superseded frames -> one enqueued command -> one readback.

    Drives the real ``ControlChannel`` coalescer (``_coalesce_command`` /
    ``_flush_coalesced_command``) and then runs whatever it actually
    enqueued through the real poller and scheduler. ``_cmd_last`` is seeded
    so that every frame of the burst, the first included, lands inside one
    pacing window -- the mid-drag case.
    """
    ws = SimpleNamespace(send_text=AsyncMock(), recv=AsyncMock())
    queue = _QueueRecorder()
    handler = ControlHandler(
        ws,
        SimpleNamespace(
            connected=True,
            capabilities={"nr"},
            profile=resolve_radio_profile(model="IC-7300"),
        ),
        "9.9.9",
        "IC-7300",
        server=SimpleNamespace(command_queue=queue),
    )
    handler._cmd_last["set_nr_level:0"] = time.monotonic()  # noqa: SLF001

    for index in range(10):
        await handler._handle_command(  # noqa: SLF001
            {
                "id": str(index),
                "name": "set_nr_level",
                "params": {"level": index, "receiver": 0},
            }
        )
    for task in list(handler._cmd_flush_tasks.values()):  # noqa: SLF001
        await task

    assert [type(item) for item in queue.items] == [SetNRLevel]
    assert queue.items[0].level == 9

    poller, scheduler = _poller()
    # ``pending_requests()`` alone cannot count readbacks: the scheduler
    # groups repeat requests for one path into a single pending entry, so
    # two dispatches of the same command also read as one. Count the calls
    # into the real implementation instead of replacing it.
    calls: list[tuple[FieldPath, ...]] = []
    real_ensure_fresh = AcquisitionScheduler.ensure_fresh

    def counting_ensure_fresh(
        self: AcquisitionScheduler, paths: Any, **kwargs: Any
    ) -> Any:
        if kwargs.get("reason") == "post_write_readback":
            calls.append(tuple(paths))
        return real_ensure_fresh(self, paths, **kwargs)

    monkeypatch.setattr(AcquisitionScheduler, "ensure_fresh", counting_ensure_fresh)
    for item in queue.items:
        await poller._execute(item)  # noqa: SLF001

    assert calls == [(FieldPath.parse("receiver.main.operator_controls.nr_level"),)]
    assert [
        request
        for request in scheduler.pending_requests()
        if request.reason == "post_write_readback"
    ]
