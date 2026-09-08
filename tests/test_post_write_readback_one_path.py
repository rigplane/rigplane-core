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
    CAP_FILTER_SHAPE,
    CAP_NOTCH,
)
from rigplane.core.acquisition_scheduler import (
    AcquisitionPriority,
    AcquisitionScheduler,
)
from rigplane.core.command_service import expected_observations_for_command
from rigplane.core.state_pipeline_contracts import CommandIntent, FieldPath
from rigplane.profiles import resolve_radio_profile
from rigplane.radio_state import RadioState
from rigplane.runtime._poller_types import LEGACY_COMMAND_NAMES
from rigplane.web.handlers import ControlHandler
from rigplane.web.radio_poller import (
    CommandQueue,
    RadioPoller,
    SetAgcTimeConstant,
    SetAutoNotch,
    SetDataMode,
    SetFilterShape,
    SetFreq,
    SetManualNotch,
    SetManualNotchWidth,
    SetMode,
    SetNBLevel,
    SetNotchFilter,
    SetNRLevel,
    SetPbtInner,
    SetPbtOuter,
    SetRitFrequency,
    SetRitStatus,
    SetRitTxStatus,
    SetToneFreq,
    SetTsqlFreq,
    SetTwinPeak,
)

pytestmark = pytest.mark.usefixtures("observed_rx_dispatch_premise")

_SRC = Path(__file__).resolve().parents[1] / "src" / "rigplane"


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
}

# Arms this PR leaves uncovered. PR-1 ships the mechanism plus the RX
# audio/DSP family; every other family keeps whatever it had. This list is
# what PR-1b owes.
_PENDING_LATER_PR: frozenset[str] = frozenset(
    {
        # Confirmed instead by RadioPoller._confirm_global_operator_write
        # (direct getter, apply-if-match). Folding that into this path is
        # PR-3.
        "SetCwPitch",
        "SetKeySpeed",
        "SetBreakIn",
        # Scope display settings.
        "SetScopeCenterType",
        "SetScopeDual",
        "SetScopeDuringTx",
        "SetScopeEdge",
        "SetScopeFixedEdge",
        "SetScopeHold",
        "SetScopeMode",
        "SetScopeRbw",
        "SetScopeRef",
        "SetScopeSpan",
        "SetScopeSpeed",
        "SetScopeVbw",
        # TX audio / modulation family.
        "SetAcc1ModLevel",
        "SetUsbModLevel",
        "SetLanModLevel",
        "SetDataOffModInput",
        "SetData1ModInput",
        "SetData2ModInput",
        "SetData3ModInput",
        "SetMicGain",
        "SetMonitor",
        "SetMonitorGain",
        "SetAfMute",
        "SetCompressor",
        "SetCompressorLevel",
        "SetSsbTxBandwidth",
        "SetDriveGain",
        "SetVox",
        "SetVoxGain",
        "SetVoxDelay",
        "SetAntiVoxGain",
        # Antenna family: the antenna names are descriptor-backed, so their
        # target lives on ``CommandDescriptor.target`` and needs bound
        # params this lookup does not have.
        "SetAntenna1",
        "SetAntenna2",
        "SetRxAntenna",
        "SetRxAntennaAnt1",
        "SetRxAntennaAnt2",
        "SetCivOutputAnt",
        "SetTunerStatus",
        # Remaining RX controls whose state-model field this PR did not
        # establish.
        "SetAgc",
        "SetApf",
        "SetAudioPeakFilter",
        "SetIfShift",
        "SetDigiselShift",
        "SetNbDepth",
        "SetNbWidth",
        "SetRepeaterTone",
        "SetRepeaterTsql",
        # Global/panel settings that DO carry a state-model field
        # (``global.tx_state.dial_lock`` and friends) but whose
        # ``_command_target`` entry this PR did not add.
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


def test_every_covered_command_resolves_to_at_least_one_field_path() -> None:
    for command_type, name in LEGACY_COMMAND_NAMES.items():
        paths = expected_observations_for_command(name, {"receiver": 0})
        assert paths, f"{command_type.__name__} -> {name!r} resolves to no field path"


def test_legacy_command_names_match_the_web_ingress() -> None:
    """The declared name is one the web ingress actually dispatches as.

    Keeps ``LEGACY_COMMAND_NAMES`` from drifting away from
    ``control.py``'s ``_enqueue_rc_*`` handlers, which is where the
    dataclass is built.
    """
    ingress = _web_ingress_names()
    unreachable = {"SetAttenuator"}  # descriptor-backed name; see the PR body
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


def test_mocked_setters_exist_on_the_real_backend() -> None:
    """A renamed backend setter must not survive as a silent mock attribute."""
    from rigplane.runtime.radio import IcomRadio

    missing = [name for name in _SETTERS if not hasattr(IcomRadio, name)]
    assert missing == []


def _poller(model: str = "IC-7300") -> tuple[RadioPoller, AcquisitionScheduler]:
    profile = resolve_radio_profile(model=model)
    assert profile.state_acquisition is not None
    radio = MagicMock()
    radio.profile = profile
    radio.model = profile.model
    radio.capabilities = {CAP_NOTCH, CAP_FILTER_SHAPE}
    radio._radio_state = SimpleNamespace(active="MAIN")
    for setter in _SETTERS:
        setattr(radio, setter, AsyncMock())
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
