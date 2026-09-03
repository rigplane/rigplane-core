"""Tests for _state_queries.build_state_queries()."""

from __future__ import annotations

from collections import Counter
from dataclasses import replace
import logging
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, call, patch

import pytest

from rigplane.commands._frame import build_civ_frame, decode_wire_tuple
from rigplane.commands.bound import BoundCommands
from rigplane.commands.command_map import CommandMap
from rigplane.commands.commander import Priority
from rigplane.commands.scope import (
    SCOPE_RECEIVER_SELECTOR_SUBS,
    SCOPE_SELECTOR_MAIN,
)
from rigplane.core.acquisition_scheduler import IcomCivAcquisitionExecutor
from rigplane.core.state_pipeline_contracts import FieldPath
from rigplane.exceptions import CommandError
from rigplane.profiles import resolve_radio_profile
from rigplane.profiles.rig_loader import discover_rigs
from rigplane.rigctld.server import RigctldServer
from rigplane.runtime import _state_queries as state_queries_module
from rigplane.runtime._state_queries import build_state_queries
from rigplane.runtime._state_queries import acquisition_query_resolver_for_profile
from rigplane.runtime._state_queries import wire_parts_for_query
from rigplane.runtime.radio_initial_state import fetch_initial_state
from rigplane.web.radio_poller import RadioPoller
from _acquisition_query_helpers import (
    AcquisitionQueryCase,
    acquisition_query,
    assert_acquisition_query_representation_contract,
    civ_frame_parts,
    query_command,
    query_selector,
)

# The 0x27 read sub-commands the sweep sends, split by whether the frame
# carries the one-byte Main/Sub scope selector.  Spelled out here rather
# than imported from the production constant, so a change to that set has
# to be made twice, deliberately (MOR-1981).
_SELECTOR_SUBS = frozenset({0x14, 0x15, 0x16, 0x17, 0x19, 0x1A, 0x1D, 0x1F})
_BARE_SUBS = frozenset({0x12, 0x13, 0x1B, 0x1C})

_FrameParts = tuple[int, int | None, bytes]
_RIGS_DIR = Path(__file__).resolve().parents[1] / "rigs"


def _shipped_civ_models() -> tuple[str, ...]:
    return tuple(
        model
        for model, config in sorted(discover_rigs(_RIGS_DIR).items())
        if config.protocol_type == "civ"
    )


def _profile_resolver_cases() -> list[tuple[FieldPath, str]]:
    cases = [
        (FieldPath.active("main", "freq_mode", "freq_hz"), "get_selected_freq"),
        (
            FieldPath.unselected("main", "freq_mode", "freq_hz"),
            "get_unselected_freq",
        ),
        (FieldPath.active("main", "freq_mode", "mode"), "get_selected_mode"),
        (
            FieldPath.unselected("main", "freq_mode", "mode"),
            "get_unselected_mode",
        ),
        (
            FieldPath.active("main", "freq_mode", "filter_width"),
            "get_filter_width",
        ),
        (
            FieldPath.active("main", "freq_mode", "filter_num"),
            "get_selected_mode",
        ),
        (
            FieldPath.unselected("main", "freq_mode", "filter_num"),
            "get_unselected_mode",
        ),
        (FieldPath.active("main", "freq_mode", "data_mode"), "get_data_mode"),
        (FieldPath.receiver("main", "meters", "s_meter"), "get_s_meter"),
    ]
    cases.extend(
        (FieldPath.receiver("main", "operator_toggles", field), getter)
        for field, getter in {
            "digisel": "get_digisel",
            "ipplus": "get_ip_plus",
            "nb": "get_nb",
            "nr": "get_nr",
            "auto_notch": "get_auto_notch",
            "manual_notch": "get_manual_notch",
            "twin_peak_filter": "get_twin_peak_filter",
            "repeater_tone": "get_repeater_tone",
            "repeater_tsql": "get_repeater_tsql",
        }.items()
    )
    cases.extend(
        (FieldPath.receiver("main", "operator_controls", field), getter)
        for field, getter in {
            "af_level": "get_af_level",
            "rf_gain": "get_rf_gain",
            "squelch": "get_squelch",
            "apf_type_level": "get_apf_type_level",
            "nr_level": "get_nr_level",
            "pbt_inner": "get_pbt_inner",
            "pbt_outer": "get_pbt_outer",
            "notch_filter": "get_notch_filter",
            "nb_level": "get_nb_level",
            "digisel_shift": "get_digisel_shift",
            "att": "get_attenuator",
            "preamp": "get_preamp",
            "agc": "get_agc",
            "audio_peak_filter": "get_audio_peak_filter",
            "filter_shape": "get_filter_shape",
            "manual_notch_width": "get_manual_notch_width",
            "agc_time_constant": "get_agc_time_constant",
            "tone_freq": "get_tone_freq",
            "tsql_freq": "get_tsql_freq",
        }.items()
    )
    cases.extend(
        (FieldPath.global_("meters", field), getter)
        for field, getter in {
            "power": "get_power_meter",
            "swr": "get_swr",
            "alc": "get_alc",
            "comp": "get_comp_meter",
            "vd": "get_vd_meter",
            "id": "get_id_meter",
        }.items()
    )
    cases.append((FieldPath.global_("slow_state", "active"), "get_main_sub_band"))
    cases.extend(
        (FieldPath.global_("tx_state", field), getter)
        for field, getter in {
            "ptt": "get_transceiver_status",
            "rit_on": "get_rit_status",
            "rit_tx": "get_rit_tx_status",
            "compressor_on": "get_compressor",
            "monitor_on": "get_monitor",
            "vox_on": "get_vox",
            "split": "get_split",
            "dual_watch": "get_dual_watch",
        }.items()
    )
    cases.extend(
        (FieldPath.global_("operator_controls", field), getter)
        for field, getter in {
            "rit_freq": "get_rit_frequency",
            "vox_delay": "get_vox_delay",
            "tuner_status": "get_tuner_status",
            "break_in": "get_break_in",
            "power_level": "get_rf_power",
            "mic_gain": "get_mic_gain",
            "cw_pitch": "get_cw_pitch",
            "key_speed": "get_key_speed",
            "compressor_level": "get_compressor_level",
            "break_in_delay": "get_break_in_delay",
            "drive_gain": "get_drive_gain",
            "monitor_gain": "get_monitor_gain",
            "vox_gain": "get_vox_gain",
            "anti_vox_gain": "get_anti_vox_gain",
        }.items()
    )
    assert len(cases) == 66
    return cases


def test_acquisition_profile_resolver_six_profile_census_and_exact_declared_bytes() -> (
    None
):
    models = ("IC-705", "IC-7300", "IC-7610", "IC-9700", "X6100", "X6200")
    former_divergences = {
        ("IC-705", "get_vox_delay"),
        ("IC-7610", "get_vox_delay"),
        ("IC-9700", "get_dual_watch"),
        ("IC-9700", "get_vox_delay"),
    }
    census = Counter()
    selector_getters = {
        "get_selected_freq",
        "get_unselected_freq",
        "get_selected_mode",
        "get_unselected_mode",
    }

    for model in models:
        profile = resolve_radio_profile(model=model)
        resolver = acquisition_query_resolver_for_profile(profile)
        assert profile.command_map is not None
        for path, getter in _profile_resolver_cases():
            if profile.command_map.has(getter):
                census[
                    "diverge" if (model, getter) in former_divergences else "agree"
                ] += 1
                receiver = (
                    0
                    if path.scope.value == "receiver" and getter not in selector_getters
                    else None
                )
                assert resolver(
                    path
                ) == state_queries_module.acquisition_query_from_wire_tuple(
                    profile.command_map.get(getter), receiver=receiver
                )
            elif getter in profile.absent_command_names:
                census["absent"] += 1
                assert resolver(path) is None
            else:
                census["missing"] += 1
                assert resolver(path) is None

    assert census == Counter(agree=302, diverge=4, absent=23, missing=67)


def test_acquisition_profile_resolver_relative_vfo_and_refusal_rules() -> None:
    resolver = acquisition_query_resolver_for_profile(
        resolve_radio_profile(model="IC-7610")
    )
    assert resolver(FieldPath.active("main", "freq_mode", "mode")) == acquisition_query(
        0x26, selector=0
    )
    assert resolver(FieldPath.active("sub", "freq_mode", "mode")) == acquisition_query(
        0x26, selector=1
    )
    assert resolver(FieldPath.unselected("main", "freq_mode", "mode")) == (
        acquisition_query(0x26, selector=1)
    )
    assert resolver(FieldPath.unselected("sub", "freq_mode", "mode")) is None
    assert resolver(FieldPath.vfo_slot("main", "A", "freq_mode", "mode")) is None
    assert resolver(FieldPath.unselected("main", "freq_mode", "filter_width")) is None
    assert resolver(FieldPath.unselected("main", "freq_mode", "data_mode")) is None


class _RecordingCivRadio:
    def __init__(self) -> None:
        self.sent: list[_FrameParts] = []
        self.options: list[tuple[bool, object, bool]] = []

    async def send_civ(
        self,
        command: int,
        sub: int | None = None,
        data: bytes | None = None,
        *,
        wait_response: bool = True,
        priority: object = None,
        wait_dispatch: bool = True,
    ) -> None:
        self.sent.append((command, sub, b"" if data is None else data))
        self.options.append((wait_response, priority, wait_dispatch))


async def _send_through_initial(
    query: AcquisitionQueryCase,
    *,
    scope_receiver: int = 0,
) -> _FrameParts:
    radio = _RecordingCivRadio()
    radio._profile = SimpleNamespace(has_lan=True)
    radio.capabilities = set()
    radio._INITIAL_STATE_GAP_SERIAL = 0.0
    radio._INITIAL_STATE_GAP_LAN = 0.0
    radio._initial_state_fetched = False
    radio.radio_state = SimpleNamespace(
        scope_controls=SimpleNamespace(receiver=scope_receiver),
    )
    with patch.object(
        state_queries_module, "build_state_queries", return_value=[query]
    ):
        await fetch_initial_state(radio)  # type: ignore[arg-type]
    assert radio.options == [(False, None, True)]
    assert radio._initial_state_fetched is True
    return radio.sent[0]


async def _send_through_web(
    query: AcquisitionQueryCase,
    *,
    scope_receiver: int | None = None,
) -> _FrameParts:
    poller = object.__new__(RadioPoller)
    poller._radio_state = (
        None
        if scope_receiver is None
        else SimpleNamespace(
            scope_controls=SimpleNamespace(receiver=scope_receiver),
        )
    )
    poller._civ = AsyncMock()
    await RadioPoller._send_one_state_query(poller, query)  # noqa: SLF001
    sent = poller._civ.await_args
    assert sent.kwargs["priority"] is Priority.BACKGROUND
    assert sent.kwargs["wait_dispatch"] is False
    return (
        sent.args[0],
        sent.kwargs.get("sub"),
        sent.kwargs.get("data", b""),
    )


async def _send_through_rigctld(
    query: AcquisitionQueryCase,
    *,
    scope_receiver: int = 0,
) -> _FrameParts:
    radio = _RecordingCivRadio()
    radio.radio_state = SimpleNamespace(
        scope_controls=SimpleNamespace(receiver=scope_receiver),
    )
    server = object.__new__(RigctldServer)
    server._radio = radio
    await RigctldServer._send_one_state_query(server, query)  # noqa: SLF001
    assert radio.options == [(False, None, True)]
    return radio.sent[0]


@pytest.mark.parametrize(
    ("wire", "receiver", "expected"),
    [
        ((0x18,), None, acquisition_query(0x18)),
        ((0x16, 0x59), None, acquisition_query(0x16, sub=0x59)),
        (
            (0x1A, 0x05, 0x01, 0x91),
            None,
            acquisition_query(0x1A, sub=0x05, data=b"\x01\x91"),
        ),
        ((0x07, 0xC2), None, acquisition_query(0x07, data=b"\xc2")),
        ((0x03, 0xAA, 0xBB), None, acquisition_query(0x03, data=b"\xaa\xbb")),
        ((0x25, 0x01), None, acquisition_query(0x25, selector=1)),
        ((0x26, 0x00), None, acquisition_query(0x26, selector=0)),
        (
            (0x1A, 0x05, 0x01, 0x91),
            1,
            acquisition_query(
                0x1A,
                sub=0x05,
                data=b"\x01\x91",
                receiver=1,
            ),
        ),
    ],
)
def test_wire_tuple_converter_preserves_semantic_frame_parts(
    wire: tuple[int, ...],
    receiver: int | None,
    expected: AcquisitionQueryCase,
) -> None:
    converter = state_queries_module.acquisition_query_from_wire_tuple
    assert converter(wire, receiver=receiver) == expected


@pytest.mark.parametrize(
    "query",
    [
        acquisition_query(0x18),
        acquisition_query(0x16, sub=0x59),
        acquisition_query(0x1A, sub=0x05, data=b"\x01\x91"),
        acquisition_query(0x07, data=b"\xc2"),
        acquisition_query(0x03, data=b"\xaa\xbb"),
        acquisition_query(0x25, selector=1),
        acquisition_query(0x26, selector=0),
    ],
)
@pytest.mark.asyncio
async def test_nonrouting_senders_emit_identical_exact_frames(
    query: AcquisitionQueryCase,
) -> None:
    expected = (query.command, query.sub, query.data)
    assert await _send_through_initial(query) == expected
    assert await _send_through_web(query) == expected
    assert await _send_through_rigctld(query) == expected


@pytest.mark.asyncio
async def test_all_senders_preserve_cmd29_receiver_sub_and_suffix() -> None:
    query = acquisition_query(
        0x1A,
        sub=0x05,
        data=b"\x01\x91",
        receiver=1,
    )
    expected = (0x29, None, b"\x01\x1a\x05\x01\x91")
    assert await _send_through_initial(query) == expected
    assert await _send_through_web(query) == expected
    assert await _send_through_rigctld(query) == expected


@pytest.mark.asyncio
async def test_web_scope_receiver_rewrite_preserves_data_suffix() -> None:
    query = acquisition_query(0x27, sub=0x14, data=b"\x00\xaa\xbb")
    assert await _send_through_web(query, scope_receiver=1) == (
        0x27,
        0x14,
        b"\x01\xaa\xbb",
    )


@pytest.mark.asyncio
async def test_web_scope_receiver_none_radio_state_falls_back_to_main() -> None:
    """Pins ``poller._radio_state is None`` -> substituted byte 0 == 0.

    ``_send_through_web`` sets ``poller._radio_state = None`` when
    ``scope_receiver`` is omitted (see its own default), so this exercises
    the fallback branch directly rather than relying on an unexercised
    default value.
    """
    query = acquisition_query(0x27, sub=0x14, data=b"\x00\xaa\xbb")
    assert await _send_through_web(query) == (0x27, 0x14, b"\x00\xaa\xbb")


@pytest.mark.asyncio
async def test_all_senders_substitute_live_scope_receiver_on_0x27() -> None:
    query = acquisition_query(0x27, sub=0x14, data=b"\x00\xaa\xbb")
    expected = (0x27, 0x14, b"\x01\xaa\xbb")
    assert await _send_through_initial(query, scope_receiver=1) == expected
    assert await _send_through_web(query, scope_receiver=1) == expected
    assert await _send_through_rigctld(query, scope_receiver=1) == expected


def test_wire_parts_for_query_cmd29_wraps_receiver_sub_and_data() -> None:
    query = acquisition_query(0x1A, sub=0x05, data=b"\x01\x91", receiver=1)
    assert wire_parts_for_query(query, 0) == (0x29, None, b"\x01\x1a\x05\x01\x91")


def test_wire_parts_for_query_substitutes_live_scope_receiver_on_0x27() -> None:
    query = acquisition_query(0x27, sub=0x14, data=b"\x00\xaa\xbb")
    assert wire_parts_for_query(query, 1) == (0x27, 0x14, b"\x01\xaa\xbb")


def test_wire_parts_for_query_passes_through_non_scope_selector_query() -> None:
    query = acquisition_query(0x1A, sub=0x05, data=b"\x01\x91")
    assert wire_parts_for_query(query, 1) == (0x1A, 0x05, b"\x01\x91")


@pytest.mark.asyncio
async def test_receiver_zero_fallback_preserves_sub_and_data() -> None:
    path = FieldPath.receiver("main", "operator_controls", "vox_delay")
    query = acquisition_query(
        0x1A,
        sub=0x05,
        data=b"\x01\x91",
        receiver=0,
    )
    sent: list[AcquisitionQueryCase] = []

    async def sender(sent_query: AcquisitionQueryCase) -> None:
        sent.append(sent_query)

    executor = IcomCivAcquisitionExecutor(
        sender,
        resolve_query=lambda _path: query,
        supports_cmd29=lambda _command, _sub: False,
    )
    request = SimpleNamespace(paths=(path,))
    result = await executor.execute(  # type: ignore[arg-type]
        request,
        already_sent_paths=frozenset(),
    )

    assert sent == [
        acquisition_query(0x1A, sub=0x05, data=b"\x01\x91"),
    ]
    assert result.sent_paths == (path,)
    assert result.failed_paths == ()


@pytest.mark.asyncio
async def test_executor_does_not_swallow_sender_exception() -> None:
    path = FieldPath.global_("tx_state", "ptt")

    async def sender(_query: AcquisitionQueryCase) -> None:
        raise RuntimeError("send failed")

    executor = IcomCivAcquisitionExecutor(
        sender,
        resolve_query=lambda _path: acquisition_query(0x1C, sub=0x00),
    )
    request = SimpleNamespace(paths=(path,))

    with pytest.raises(RuntimeError, match="send failed"):
        await executor.execute(  # type: ignore[arg-type]
            request,
            already_sent_paths=frozenset(),
        )


def _scope_queries(
    queries: list[AcquisitionQueryCase],
) -> list[AcquisitionQueryCase]:
    return [query for query in queries if query_command(query) == 0x27]


def _ic7300_caps() -> set[str]:
    """Return the full capability set for IC-7300."""
    profile = resolve_radio_profile(model="IC-7300")
    return set(profile.capabilities)


class TestBuildStateQueries:
    """Verify build_state_queries produces correct query lists."""

    def test_returns_current_acquisition_query_representation(self) -> None:
        assert_acquisition_query_representation_contract()
        profile = resolve_radio_profile(model="IC-7610")
        queries = build_state_queries(profile)
        assert isinstance(queries, list)
        assert len(queries) > 0
        for query in queries:
            civ_frame_parts(query)

    def test_ic7610_includes_dual_receiver_queries(self) -> None:
        """IC-7610 has 2 receivers — freq/mode must appear for rx 0 and rx 1."""
        profile = resolve_radio_profile(model="IC-7610")
        queries = build_state_queries(profile)
        freq_selectors = [
            query_selector(query) for query in queries if query_command(query) == 0x25
        ]
        assert 0 in freq_selectors
        assert 1 in freq_selectors

    def test_ic7300_single_receiver(self) -> None:
        """IC-7300 has selected/unselected reads on its one receiver."""
        profile = resolve_radio_profile(model="IC-7300")
        queries = build_state_queries(profile)
        freq_queries = [query for query in queries if query_command(query) == 0x25]
        assert freq_queries == [
            acquisition_query(0x25, selector=0),
            acquisition_query(0x25, data=b"\x01"),
        ]
        assert acquisition_query(0x07, data=b"\xd2") not in queries
        assert acquisition_query(0x07, data=b"\xc2") not in queries
        assert acquisition_query(0x14, sub=0x01) in queries
        assert acquisition_query(0x14, sub=0x01, receiver=0) not in queries

    def test_ic7610_includes_scope_queries(self) -> None:
        """IC-7610 should have scope sub-commands (0x27)."""
        profile = resolve_radio_profile(model="IC-7610")
        queries = build_state_queries(profile)
        scope_queries = [query for query in queries if query_command(query) == 0x27]
        assert len(scope_queries) > 0

    def test_ic7300_has_scope_queries_if_capable(self) -> None:
        """IC-7300 has scope capability — should include 0x27 queries."""
        profile = resolve_radio_profile(model="IC-7300")
        queries = build_state_queries(profile)
        scope_queries = [query for query in queries if query_command(query) == 0x27]
        if "scope" in _ic7300_caps():
            assert len(scope_queries) > 0
        else:
            assert len(scope_queries) == 0

    def test_queries_follow_profile_field_capabilities_and_command_map(self) -> None:
        profile = resolve_radio_profile(model="IC-7610")
        queries = build_state_queries(profile)
        assert profile.state_acquisition is not None
        pollable = profile.state_acquisition.pollable_paths()
        resolve = acquisition_query_resolver_for_profile(profile)
        expected = []
        for path in pollable:
            query = resolve(path)
            if query is None:
                continue
            if query.receiver is not None and not profile.supports_cmd29(
                query.command, query.sub
            ):
                if query.receiver != 0:
                    continue
                query = replace(query, receiver=None)
            if query not in expected:
                expected.append(query)

        assert queries == expected
        assert acquisition_query(0x18) not in queries
        assert acquisition_query(0x1C, sub=0x03) not in queries

    def test_command_map_mutation_changes_built_query(self) -> None:
        profile = resolve_radio_profile(model="IC-7300")
        assert profile.command_map is not None
        commands = {name: profile.command_map.get(name) for name in profile.command_map}
        commands["get_rf_power"] = (0x14, 0x7E)
        mutated = replace(profile, command_map=CommandMap(commands))

        original_queries = build_state_queries(profile)
        mutated_queries = build_state_queries(mutated)

        assert acquisition_query(0x14, sub=0x0A) in original_queries
        assert acquisition_query(0x14, sub=0x7E) not in original_queries
        assert acquisition_query(0x14, sub=0x0A) not in mutated_queries
        assert acquisition_query(0x14, sub=0x7E) in mutated_queries

    def test_polling_membership_mutation_changes_built_query(self) -> None:
        profile = resolve_radio_profile(model="IC-7300")
        acquisition = profile.state_acquisition
        assert acquisition is not None
        power_path = FieldPath.global_("operator_controls", "power_level")
        capabilities = tuple(
            capability
            for capability in acquisition.capabilities
            if capability.path != power_path
        )
        mutated = replace(
            profile,
            state_acquisition=replace(acquisition, capabilities=capabilities),
        )

        assert acquisition_query(0x14, sub=0x0A) in build_state_queries(profile)
        assert acquisition_query(0x14, sub=0x0A) not in build_state_queries(mutated)

    def test_removed_capabilities_and_is_serial_arguments_raise_type_error(
        self,
    ) -> None:
        """MOR-2244: both legacy arguments were unread and are now removed."""
        profile = resolve_radio_profile(model="IC-7610")
        with pytest.raises(TypeError):
            build_state_queries(profile, capabilities=set())  # type: ignore[call-arg]
        with pytest.raises(TypeError):
            build_state_queries(profile, is_serial=False)  # type: ignore[call-arg]

    @pytest.mark.parametrize("model", _shipped_civ_models())
    def test_every_shipped_civ_profile_has_traceable_acquisition(
        self, model: str
    ) -> None:
        profile = resolve_radio_profile(model=model)
        acquisition = profile.state_acquisition
        assert acquisition is not None
        pollable = acquisition.pollable_paths()
        assert pollable

        resolver = acquisition_query_resolver_for_profile(profile)
        expected = []
        for path in pollable:
            query = resolver(path)
            assert query is not None, f"{model}: unresolved declared path {path}"
            if query.receiver is not None and not profile.supports_cmd29(
                query.command, query.sub
            ):
                assert query.receiver == 0, (
                    f"{model}: declared pollable path {path} has no "
                    "executable receiver route"
                )
                query = replace(query, receiver=None)
            if query not in expected:
                expected.append(query)

        queries = build_state_queries(profile)
        assert queries
        assert queries == expected
        assert len(queries) == len(set(queries))

    @pytest.mark.asyncio
    @pytest.mark.parametrize("model", _shipped_civ_models())
    async def test_every_shipped_civ_pollable_path_is_scheduler_executable(
        self, model: str
    ) -> None:
        profile = resolve_radio_profile(model=model)
        acquisition = profile.state_acquisition
        assert acquisition is not None
        pollable = acquisition.pollable_paths()
        sent: list[AcquisitionQueryCase] = []

        async def sender(query: AcquisitionQueryCase) -> None:
            sent.append(query)

        executor = IcomCivAcquisitionExecutor(
            sender,
            resolve_query=acquisition_query_resolver_for_profile(profile),
            supports_cmd29=profile.supports_cmd29,
        )
        result = await executor.execute(  # type: ignore[arg-type]
            SimpleNamespace(paths=pollable),
            already_sent_paths=frozenset(),
        )

        assert result.sent_paths == pollable
        assert result.failed_paths == ()
        assert sent == build_state_queries(profile)

    def test_ic9700_polling_paths_have_exact_executable_query_parity(self) -> None:
        profile = resolve_radio_profile(model="IC-9700")
        acquisition = profile.state_acquisition
        assert acquisition is not None
        pollable = acquisition.pollable_paths()
        excluded_sub_controls = {
            FieldPath.receiver("sub", "operator_controls", name)
            for name in ("af_level", "rf_gain", "squelch", "att", "preamp", "agc")
        } | {
            FieldPath.receiver("sub", "operator_toggles", name) for name in ("nb", "nr")
        }
        excluded_sub_freq_mode = {
            FieldPath.active("sub", "freq_mode", name) for name in ("freq_hz", "mode")
        }

        assert profile.cmd29_routes == frozenset()
        assert not excluded_sub_controls.intersection(pollable)
        assert not excluded_sub_freq_mode.intersection(pollable)
        queries = build_state_queries(profile)
        assert len(pollable) == len(queries) == 34
        assert all(query.receiver is None for query in queries)
        assert not any(
            query.command in {0x25, 0x26} and query.data == b"\x01" for query in queries
        )

    @pytest.mark.parametrize("model", ("IC-705", "IC-9700"))
    def test_profiles_without_documented_scope_rbw_emit_no_271f(
        self, model: str
    ) -> None:
        profile = resolve_radio_profile(model=model)
        acquisition = profile.state_acquisition
        assert acquisition is not None
        rbw_path = FieldPath.scope_control("display", "rbw")
        commands = BoundCommands(
            profile.command_map or CommandMap({}),
            profile.absent_command_sources,
        )

        assert rbw_path not in acquisition.pollable_paths()
        assert not any(
            query.command == 0x27 and query.sub == 0x1F
            for query in build_state_queries(profile)
        )
        with pytest.raises(CommandError, match="get_scope_rbw is not supported"):
            commands.get_scope_rbw(to_addr=profile.civ_addr)
        with pytest.raises(CommandError, match="get_scope_rbw is not supported"):
            commands.scope_set_rbw(1, to_addr=profile.civ_addr)

    def test_ic7610_documented_scope_rbw_remains_pollable(self) -> None:
        profile = resolve_radio_profile(model="IC-7610")
        acquisition = profile.state_acquisition
        assert acquisition is not None
        rbw_path = FieldPath.scope_control("display", "rbw")

        assert rbw_path in acquisition.pollable_paths()
        assert acquisition_query(0x27, sub=0x1F, data=b"\x00") in build_state_queries(
            profile
        )

    @pytest.mark.parametrize("model", ("IC-705", "IC-7300", "IC-7610", "IC-9700"))
    def test_xfc_builders_remain_bound_to_boolean_1c02(self, model: str) -> None:
        profile = resolve_radio_profile(model=model)
        commands = BoundCommands(
            profile.command_map or CommandMap({}),
            profile.absent_command_sources,
        )

        assert commands.get_xfc_status(to_addr=profile.civ_addr) == build_civ_frame(
            profile.civ_addr, 0xE0, 0x1C, sub=0x02
        )
        assert commands.set_xfc_status(
            True, to_addr=profile.civ_addr
        ) == build_civ_frame(profile.civ_addr, 0xE0, 0x1C, sub=0x02, data=b"\x01")

    def test_deterministic_output(self) -> None:
        """Same inputs should produce identical output."""
        profile = resolve_radio_profile(model="IC-7610")
        q1 = build_state_queries(profile)
        q2 = build_state_queries(profile)
        assert q1 == q2

    def test_ic7610_dual_watch_query_preserves_wire_tuple(self) -> None:
        profile = resolve_radio_profile(model="IC-7610")
        command, sub, prefix = decode_wire_tuple(
            profile.command_map.get("get_dual_watch")
        )
        assert (command, sub, prefix) == (0x07, None, b"\xc2")
        queries = build_state_queries(profile)
        assert queries.count(acquisition_query(0x07, data=b"\xc2")) == 1
        assert acquisition_query(0x07) not in queries
        assert build_civ_frame(0x98, 0xE0, 0x07, sub=0xC2) == bytes.fromhex(
            "FE FE 98 E0 07 C2 FD"
        )

    def test_duplicate_query_resolution_is_deduplicated_in_profile_order(self) -> None:
        profile = resolve_radio_profile(model="IC-7300")
        acquisition = profile.state_acquisition
        assert acquisition is not None
        filter_num = FieldPath.active("main", "freq_mode", "filter_num")
        capabilities = tuple(
            replace(capability, polling=True)
            if capability.path == filter_num
            else capability
            for capability in acquisition.capabilities
        )
        assert any(capability.path == filter_num for capability in capabilities)
        mutated = replace(
            profile,
            state_acquisition=replace(acquisition, capabilities=capabilities),
        )

        queries = build_state_queries(mutated)
        assert len(queries) == len(set(queries))
        assert queries.count(acquisition_query(0x26, selector=0)) == 1


class TestScopeReceiverSelector:
    """MOR-1981: which 0x27 reads carry the Main/Sub scope selector byte.

    The separation is the point.  On a sub-command that takes no selector
    the extra ``0x00`` is not an ignored byte but the first data byte of a
    WRITE, so a mutation that widens the set has to fail here.
    """

    def test_constant_holds_exactly_the_eight(self) -> None:
        assert SCOPE_RECEIVER_SELECTOR_SUBS == _SELECTOR_SUBS
        assert len(SCOPE_RECEIVER_SELECTOR_SUBS) == 8
        assert SCOPE_SELECTOR_MAIN == 0x00

    def test_the_bare_four_are_not_in_the_set(self) -> None:
        """``27 1C 00`` reads as SET center_type=0 (Filter center), not a query."""
        assert _BARE_SUBS.isdisjoint(SCOPE_RECEIVER_SELECTOR_SUBS)
        assert len(_BARE_SUBS) == 4

    def test_fixed_edge_is_not_in_the_set(self) -> None:
        """0x1E takes ``<frequency range><edge number>``, not the selector.

        ``00`` is not a legal frequency range -- they start at ``01`` -- so
        ``27 1E 00`` is neither a bare read nor a valid one.  The pair is
        built by ``commands/scope.py: get_scope_fixed_edge``.
        """
        assert 0x1E not in SCOPE_RECEIVER_SELECTOR_SUBS

    def test_sweep_scope_reads_follow_declared_commands_and_shapes(self) -> None:
        profile = resolve_radio_profile(model="IC-7300")
        scope = _scope_queries(build_state_queries(profile))

        frame_parts = [civ_frame_parts(query) for query in scope]
        with_selector = {
            part.sub
            for part in frame_parts
            if len(part.data) == 1 and part.data == bytes([SCOPE_SELECTOR_MAIN])
        }
        bare = {part.sub for part in frame_parts if not part.data}

        assert with_selector == _SELECTOR_SUBS - {0x1F}
        assert bare == _BARE_SUBS
        assert acquisition_query(0x27, sub=0x1E, data=b"\x01\x01") in scope
        assert acquisition_query(0x27, sub=0x1F, data=b"\x00") not in scope
        assert len(scope) == 12

    def test_sweep_selector_is_one_byte_and_main(self) -> None:
        profile = resolve_radio_profile(model="IC-7300")
        scope = _scope_queries(build_state_queries(profile))

        carried = [
            part.data
            for part in map(civ_frame_parts, scope)
            if part.sub in SCOPE_RECEIVER_SELECTOR_SUBS
        ]
        assert carried, "no scope read carried a selector"
        for data in carried:
            assert data == bytes([SCOPE_SELECTOR_MAIN])

    @pytest.mark.parametrize("model", ["IC-7300", "IC-7610"])
    def test_a_payload_carrying_sub_is_never_paired_with_a_receiver(
        self, model: str
    ) -> None:
        """The selector is payload, not the cmd29 receiver slot.

        A receiver route uses cmd29 in every sender, which is a different
        frame entirely. The query envelope keeps selector data separate from
        receiver routing, so no sender can confuse one for the other.
        """
        profile = resolve_radio_profile(model=model)
        queries = build_state_queries(profile)

        assert _scope_queries(queries), "no scope reads to check"
        assert all(
            part.receiver is None for part in map(civ_frame_parts, queries) if part.data
        )


# ------------------------------------------------------------------
# CoreRadio._fetch_initial_state tests
# ------------------------------------------------------------------


class TestFetchInitialState:
    """Tests for CoreRadio._fetch_initial_state method."""

    @pytest.fixture(autouse=True)
    def _no_real_pacing(self):
        """Skip the 12ms inter-query sleep (~1.2s per test) — tests assert
        call counts and flag state, not real pacing."""
        with patch("rigplane.radio.asyncio.sleep", new=AsyncMock()):
            yield

    @pytest.fixture
    def radio(self):
        from rigplane.radio import CoreRadio

        with patch.object(CoreRadio, "__init__", lambda self: None):
            r = CoreRadio.__new__(CoreRadio)
            profile = resolve_radio_profile(model="IC-7610")
            r._profile = profile
            r._initial_state_fetched = False
            r._radio_state = SimpleNamespace(
                scope_controls=SimpleNamespace(receiver=0),
            )
            r.send_civ = AsyncMock()
            return r

    @pytest.mark.asyncio
    @pytest.mark.parametrize("model", _shipped_civ_models())
    async def test_dispatches_all_queries(self, radio, model: str) -> None:
        radio._profile = resolve_radio_profile(model=model)
        queries = build_state_queries(radio._profile)
        assert queries, f"{model}: initial acquisition must not be empty"

        await radio._fetch_initial_state()
        expected_calls = []
        for query in queries:
            if query.receiver is not None:
                inner = bytes([query.receiver, query.command])
                if query.sub is not None:
                    inner += bytes([query.sub])
                expected_calls.append(
                    call(
                        0x29,
                        sub=None,
                        data=inner + query.data,
                        wait_response=False,
                    )
                )
            else:
                expected_calls.append(
                    call(
                        query.command,
                        sub=query.sub,
                        data=query.data,
                        wait_response=False,
                    )
                )
        assert radio.send_civ.await_args_list == expected_calls
        assert radio._initial_state_fetched is True

    @pytest.mark.asyncio
    async def test_single_rx_unselected_selector_is_data_not_sub_receiver(
        self, radio
    ) -> None:
        radio._profile = resolve_radio_profile(model="IC-7300")

        await radio._fetch_initial_state()

        assert (
            call(0x25, sub=None, data=b"\x01", wait_response=False)
            in radio.send_civ.await_args_list
        )
        assert (
            call(0x26, sub=None, data=b"\x01", wait_response=False)
            in radio.send_civ.await_args_list
        )

    @pytest.mark.asyncio
    async def test_scope_reads_carry_the_selector_only_where_it_is_legal(
        self, radio
    ) -> None:
        radio._profile = resolve_radio_profile(model="IC-7300")

        await radio._fetch_initial_state()

        sent = radio.send_civ.await_args_list
        for sub in sorted(_SELECTOR_SUBS - {0x1F}):
            assert (
                call(
                    0x27,
                    sub=sub,
                    data=bytes([SCOPE_SELECTOR_MAIN]),
                    wait_response=False,
                )
                in sent
            ), f"0x27/0x{sub:02X} was not sent with a selector byte"
        for sub in sorted(_BARE_SUBS):
            assert call(0x27, sub=sub, data=b"", wait_response=False) in sent, (
                f"0x27/0x{sub:02X} was not sent bare"
            )
        assert (
            call(
                0x27,
                sub=0x1E,
                data=b"\x01\x01",
                wait_response=False,
            )
            in sent
        )
        assert (
            call(
                0x27,
                sub=0x1F,
                data=b"\x00",
                wait_response=False,
            )
            not in sent
        )

    @pytest.mark.asyncio
    async def test_sets_flag_on_success(self, radio) -> None:
        await radio._fetch_initial_state()
        assert radio._initial_state_fetched is True

    @pytest.mark.asyncio
    async def test_sets_flag_on_failure(self, radio) -> None:
        with patch(
            "rigplane._state_queries.build_state_queries",
            side_effect=RuntimeError("boom"),
        ):
            await radio._fetch_initial_state()
        assert radio._initial_state_fetched is True

    @pytest.mark.asyncio
    async def test_send_failure_nonfatal(self, radio) -> None:
        call_count = 0
        sleep = AsyncMock()

        async def flaky_send(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count % 3 == 0:
                raise RuntimeError("transient error")

        radio.send_civ = flaky_send
        with patch("rigplane.runtime.radio_initial_state.asyncio.sleep", new=sleep):
            await radio._fetch_initial_state()
        assert radio._initial_state_fetched is True
        assert call_count > 0
        assert sleep.await_count == call_count

    @pytest.mark.asyncio
    async def test_summary_log_names_sends_not_ok(self, radio, caplog) -> None:
        """MOR-2224: send_civ is called with wait_response=False, so the sweep
        never learns whether the radio replied — the summary must say what
        was measured (queries sent), not the unchecked word "ok"."""
        queries = build_state_queries(radio._profile)
        with patch(
            "rigplane.runtime.radio_initial_state.asyncio.sleep", new=AsyncMock()
        ):
            with caplog.at_level(
                logging.INFO, logger="rigplane.runtime.radio_initial_state"
            ):
                await radio._fetch_initial_state()

        summary_records = [
            r
            for r in caplog.records
            if r.getMessage().startswith("initial state fetch sent")
        ]
        assert len(summary_records) == 1
        message = summary_records[0].getMessage()
        assert "ok" not in message.split()
        assert (
            message == f"initial state fetch sent {len(queries)}/{len(queries)} queries"
        )

    @pytest.mark.asyncio
    async def test_send_failure_logs_the_failing_query_and_continues(
        self, radio, caplog
    ) -> None:
        radio._profile = resolve_radio_profile(model="IC-7300")
        queries = build_state_queries(radio._profile)
        failing = queries[0]

        call_count = 0

        async def flaky_send(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise RuntimeError("transient error")

        radio.send_civ = flaky_send
        with patch(
            "rigplane.runtime.radio_initial_state.asyncio.sleep", new=AsyncMock()
        ):
            with caplog.at_level(
                logging.DEBUG, logger="rigplane.runtime.radio_initial_state"
            ):
                await radio._fetch_initial_state()

        debug_records = [r for r in caplog.records if r.levelno == logging.DEBUG]
        assert len(debug_records) == 1
        message = debug_records[0].getMessage()
        assert f"command=0x{failing.command:02X}" in message
        assert "RuntimeError" in message
        # the sweep continues past the failure: every query is still attempted
        assert call_count == len(queries)
        assert radio._initial_state_fetched is True
