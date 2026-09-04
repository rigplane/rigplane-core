"""Tests for YaesuCatPoller."""

from __future__ import annotations

import ast
import asyncio
from collections.abc import Callable, Sequence
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, call as mock_call, patch

import pytest

from rigplane.core.command_dispatch import bind_command_intent
from rigplane.core.command_service import CommandService
from rigplane.core.observation_adapter import ProviderObservationAdapter
from rigplane.core.state_acquisition_policy import (
    FieldCapability,
    RadioAcquisitionProfile,
)
from rigplane.core.state_pipeline_contracts import FieldPath, Observation
from rigplane.core.state_store import FreshnessClock, StateStore
from rigplane.core.tx_observation import (
    OBSERVED_PTT_PATH,
    ObservedPtt,
    TxStateReading,
    project_observed_ptt,
)
from rigplane.core.tx_target import KnownTxTarget, UnknownTxTarget
from rigplane.core.tx_interlock_contract import (
    TxInterlockCommandFamily,
    TxInterlockDisposition,
)
from rigplane.exceptions import CommandError
from rigplane.backends.yaesu_cat.poller import YaesuCatPoller
from rigplane.backends.yaesu_cat.observations import YaesuObservationAdapter
from rigplane.backends.yaesu_cat.radio import YaesuCatRadio
from rigplane.backends.yaesu_cat.parser import CatParseError
from rigplane.backends.yaesu_cat.transport import (
    CatCommandRejected,
    CatTimeoutError,
    CatTransportError,
)
from rigplane.profiles import get_radio_profile
from rigplane.radio_state import RadioState
from rigplane.web.handlers.control import ControlHandler
from rigplane.web.radio_poller import (
    CommandQueue,
    SelectVfo,
    SetBand,
    SetFreq,
    SetMode,
    SetSplit,
    VfoEqualize,
    VfoSwap,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _canonical_ptt_path(
    monkeypatch: pytest.MonkeyPatch,
    *,
    state_map: dict[str, str] | None = None,
    later_field: bool = False,
) -> tuple[
    YaesuCatRadio, YaesuCatPoller, StateStore, FreshnessClock, list[Observation]
]:
    radio = YaesuCatRadio("/dev/null", profile="ftx1", audio_driver=MagicMock())
    legacy = FieldPath.global_("tx_state", "ptt")
    width = FieldPath.active("main", "freq_mode", "filter_width")
    profile = radio.profile
    acquisition = profile.state_acquisition
    assert acquisition is not None
    acquisition = replace(
        acquisition,
        capabilities=tuple(
            item
            for item in acquisition.capabilities
            if item.path == legacy or (later_field and item.path == width)
        ),
        field_policies={
            legacy: replace(
                acquisition.policy_for(legacy),
                freshness_ttl_seconds=2.0,
                meter_coalescing=None,
            )
        },
    )
    radio._profile_cache = replace(  # noqa: SLF001
        profile,
        state_acquisition=acquisition,
        tx_policy=(
            profile.tx_policy
            if state_map is None
            else replace(profile.tx_policy, tx_state_map=state_map)
        ),
    )
    radio._transport._connected = True  # noqa: SLF001
    radio._transport.query = AsyncMock(return_value="TX1")  # noqa: SLF001
    clock = FreshnessClock(start=128.0)
    store = StateStore(freshness_clock=clock)
    service = CommandService(executor=AsyncMock(), state_store=store, clock=clock.now)
    emitted: list[Observation] = []

    def accept(observations: Sequence[Observation]) -> None:
        emitted.extend(observations)
        for observation in observations:
            service.apply_observation(observation)

    factory = YaesuObservationAdapter.from_radio
    monkeypatch.setattr(
        YaesuObservationAdapter,
        "from_radio",
        lambda radio: factory(radio, clock=clock.now),
    )
    poller = radio.create_observation_poller(callback=accept)
    poller.bind_provider_generation(
        capture=lambda: store.provider_generation,
        advance=store.begin_provider_generation,
    )
    return radio, poller, store, clock, emitted


def _seed_canonical_ptt(
    radio: YaesuCatRadio,
    store: StateStore,
    clock: FreshnessClock,
    value: ObservedPtt,
) -> None:
    acquisition = radio.profile.state_acquisition
    assert acquisition is not None
    store.apply_current(
        ProviderObservationAdapter(
            acquisition, "yaesu_poll_response", "serial", clock.now
        ).observation(
            OBSERVED_PTT_PATH,
            value,
            native_id="fixture_seed",
            max_age=acquisition.policy_for(
                FieldPath.global_("tx_state", "ptt")
            ).freshness_ttl_seconds,
        )
    )
    assert project_observed_ptt(store.snapshot()) is value


@pytest.mark.asyncio
@pytest.mark.parametrize("later_field", [False, True])
async def test_canonical_ptt_fixture_has_valid_non_meter_policy(
    monkeypatch: pytest.MonkeyPatch, later_field: bool
) -> None:
    radio, _, store, clock, emitted = _canonical_ptt_path(
        monkeypatch, later_field=later_field
    )
    acquisition = radio.profile.state_acquisition
    assert acquisition is not None
    legacy = FieldPath.global_("tx_state", "ptt")
    assert acquisition.policy_for(legacy).meter_coalescing is None
    assert acquisition.policy_for(legacy).freshness_ttl_seconds == 2.0
    assert set(acquisition.pollable_paths()) == (
        {legacy, FieldPath.active("main", "freq_mode", "filter_width")}
        if later_field
        else {legacy}
    )
    _seed_canonical_ptt(radio, store, clock, ObservedPtt.ON)
    assert store.snapshot().field(OBSERVED_PTT_PATH).provider_generation == 0
    assert emitted == []


@pytest.mark.asyncio
async def test_stopped_poller_releases_connection_generation_binding() -> None:
    queue = CommandQueue()
    retired = YaesuCatPoller(make_radio(), command_queue=queue)
    with pytest.raises(RuntimeError, match="already bound"):
        YaesuCatPoller(make_radio(), command_queue=queue)

    await retired.stop()
    fresh = YaesuCatPoller(make_radio(), command_queue=queue)
    assert queue.capture_connection_generation() == (
        fresh._current_tx_target_generation()  # noqa: SLF001
    )
    await fresh.stop()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("frame", "state_map", "expected", "legacy"),
    [
        ("TX0", None, ObservedPtt.OFF, False),
        ("TX1", None, ObservedPtt.ON, True),
        ("TX2", None, ObservedPtt.ON, True),
        ("TX9", None, ObservedPtt.UNKNOWN, True),
        ("TX0", {}, ObservedPtt.UNKNOWN, False),
        ("TX7", {"7": "rx"}, ObservedPtt.OFF, False),
        ("TX0", {"0": "tx_other"}, ObservedPtt.ON, True),
        ("TX1", {"1": "unrecognized"}, ObservedPtt.UNKNOWN, True),
    ],
)
async def test_canonical_ptt_real_read_reaches_store_with_legacy_and_shared_ttl(
    monkeypatch: pytest.MonkeyPatch,
    frame: str,
    state_map: dict[str, str] | None,
    expected: ObservedPtt,
    legacy: bool,
) -> None:
    radio, poller, store, clock, emitted = _canonical_ptt_path(
        monkeypatch, state_map=state_map
    )
    radio._transport.query.return_value = frame  # noqa: SLF001
    await poller._emit_medium_observations()  # noqa: SLF001
    assert [call.args[0] for call in radio._transport.query.await_args_list] == ["TX;"]  # noqa: SLF001
    by_path = {item.path: item for item in emitted}
    old = by_path[FieldPath.global_("tx_state", "ptt")]
    assert old.value is legacy
    assert OBSERVED_PTT_PATH in by_path, "PTT_READ_PUBLICATION"
    observed = by_path[OBSERVED_PTT_PATH]
    assert observed.value is expected
    assert observed.timestamp_monotonic == old.timestamp_monotonic == clock.now()
    assert observed.max_age == old.max_age == 2.0
    assert observed.provider_generation == old.provider_generation == 0
    assert observed.source.source == old.source.source == "yaesu_poll_response"
    assert observed.source.provider == old.source.provider == "yaesu_cat"
    assert observed.source.transport == old.source.transport == "serial"
    assert radio.radio_state.ptt is False
    assert project_observed_ptt(store.snapshot()) is expected
    clock.advance(1.0)
    assert project_observed_ptt(store.snapshot()) is expected
    clock.advance(1.0)
    assert project_observed_ptt(store.snapshot()) is ObservedPtt.UNKNOWN


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("reading", "expected"),
    [
        (TxStateReading(1, "tx_cat"), ObservedPtt.UNKNOWN),
        (TxStateReading(True, "tx_cat"), ObservedPtt.ON),
        (TxStateReading(0, "rx"), ObservedPtt.UNKNOWN),
        (TxStateReading(False, "rx"), ObservedPtt.OFF),
    ],
    ids=["integer-one", "valid-on", "integer-zero", "valid-off"],
)
async def test_canonical_ptt_typed_reading_requires_strict_value(
    monkeypatch: pytest.MonkeyPatch, reading: TxStateReading, expected: ObservedPtt
) -> None:
    radio, poller, store, _, emitted = _canonical_ptt_path(monkeypatch)
    reading = replace(reading, source="yaesu_poll_response", verified_readback=True)
    radio.read_transmit_state = AsyncMock(return_value=reading)
    await poller._emit_medium_observations()  # noqa: SLF001
    assert radio.read_transmit_state.await_args_list == [mock_call()], (
        "PTT_TYPED_READER"
    )
    radio._transport.query.assert_not_awaited()  # noqa: SLF001
    assert [item.value for item in emitted if item.path == OBSERVED_PTT_PATH] == [
        expected
    ], "PTT_TYPED_VALUE_PUBLICATION"
    assert store.snapshot().field(OBSERVED_PTT_PATH).value is expected
    assert project_observed_ptt(store.snapshot()) is expected
    legacy = [item.value for item in emitted if str(item.path) == "global.tx_state.ptt"]
    assert legacy == ([reading.value] if type(reading.value) is bool else [])


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "qualification",
    [
        {"source": "command_response"},
        {"verified_readback": False},
        {"verified_readback": 1},
        {"failure": "read-error"},
        {"attributed": None},
        {"attributed": "unrecognized"},
    ],
)
async def test_canonical_ptt_requires_qualified_readback(
    monkeypatch: pytest.MonkeyPatch, qualification: dict[str, object]
) -> None:
    radio, poller, store, _, emitted = _canonical_ptt_path(monkeypatch)
    radio.read_transmit_state = AsyncMock(
        return_value=replace(
            TxStateReading(True, "tx_cat", "yaesu_poll_response", True),
            **qualification,
        )
    )
    await poller._emit_medium_observations()  # noqa: SLF001
    assert [item.value for item in emitted if item.path == OBSERVED_PTT_PATH] == [
        ObservedPtt.UNKNOWN
    ]
    assert project_observed_ptt(store.snapshot()) is ObservedPtt.UNKNOWN
    radio.read_transmit_state.assert_awaited_once_with()
    radio._transport.query.assert_not_awaited()  # noqa: SLF001


@pytest.mark.asyncio
@pytest.mark.parametrize("later_failure", [False, True])
async def test_canonical_ptt_publishes_before_later_read(
    monkeypatch: pytest.MonkeyPatch, later_failure: bool
) -> None:
    radio, poller, store, _, emitted = _canonical_ptt_path(
        monkeypatch, later_field=True
    )

    async def width(receiver: int, *, mode: str | None) -> int:
        assert (receiver, mode) == (0, None)
        assert project_observed_ptt(store.snapshot()) is ObservedPtt.ON
        assert [item.value for item in emitted] == [ObservedPtt.ON]
        if later_failure:
            raise CatTimeoutError("later")
        return 2400

    radio.read_filter_width = AsyncMock(side_effect=width)
    if later_failure:
        with pytest.raises(CatTimeoutError, match="later"):
            await poller._emit_medium_observations()  # noqa: SLF001
    else:
        await poller._emit_medium_observations()  # noqa: SLF001
    radio.read_filter_width.assert_awaited_once_with(0, mode=None)
    assert [item.value for item in emitted if item.path == OBSERVED_PTT_PATH] == [
        ObservedPtt.ON
    ]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "error",
    [
        "NOTTX",
        CatParseError("TX{state};", "bad TX", "mismatch"),
        CatCommandRejected("reject"),
    ],
)
@pytest.mark.parametrize("later_failure", [False, True])
async def test_canonical_ptt_read_error_reaches_store_even_if_later_field_fails(
    monkeypatch: pytest.MonkeyPatch, error: str | Exception, later_failure: bool
) -> None:
    radio, poller, store, clock, emitted = _canonical_ptt_path(
        monkeypatch, later_field=later_failure
    )
    for _ in range(2):
        radio._transport.query.side_effect = None  # noqa: SLF001
        radio._transport.query.return_value = "TX1"  # noqa: SLF001
        radio.read_filter_width = AsyncMock(return_value=2400)
        await poller._emit_medium_observations()  # noqa: SLF001
        _seed_canonical_ptt(radio, store, clock, ObservedPtt.ON)
        emitted.clear()
        radio._transport.query.reset_mock()  # noqa: SLF001
        radio._transport.query.side_effect = (
            error if isinstance(error, Exception) else None
        )  # noqa: SLF001
        radio._transport.query.return_value = error  # noqa: SLF001
        if later_failure:

            async def width(receiver: int, *, mode: str | None) -> int:
                assert (receiver, mode) == (0, None)
                assert project_observed_ptt(store.snapshot()) is ObservedPtt.UNKNOWN
                assert [item.value for item in emitted] == [ObservedPtt.UNKNOWN]
                raise CatTimeoutError("later")

            radio.read_filter_width = AsyncMock(side_effect=width)
            with pytest.raises(CatTimeoutError):
                await poller._emit_medium_observations()  # noqa: SLF001
        else:
            await poller._emit_medium_observations()  # noqa: SLF001
        assert not any(str(item.path) == "global.tx_state.ptt" for item in emitted)
        assert [call.args[0] for call in radio._transport.query.await_args_list] == [
            "TX;"
        ]  # noqa: SLF001
        if later_failure:
            radio.read_filter_width.assert_awaited_once_with(0, mode=None)
        else:
            radio.read_filter_width.assert_not_awaited()
        assert [item.value for item in emitted if item.path == OBSERVED_PTT_PATH] == [
            ObservedPtt.UNKNOWN
        ], "PTT_READ_ERROR_PUBLICATION"
        assert store.snapshot().field(OBSERVED_PTT_PATH).value is ObservedPtt.UNKNOWN


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "error", [CatTimeoutError("TX timeout"), CatTransportError("TX link")]
)
async def test_canonical_ptt_transport_failure_preserves_reconnect_path(
    monkeypatch: pytest.MonkeyPatch, error: Exception
) -> None:
    radio, poller, store, clock, emitted = _canonical_ptt_path(monkeypatch)
    _seed_canonical_ptt(radio, store, clock, ObservedPtt.ON)
    radio._transport.query.side_effect = error  # noqa: SLF001
    poller._try_reconnect = AsyncMock(side_effect=asyncio.CancelledError)  # noqa: SLF001
    with pytest.raises(asyncio.CancelledError):
        await poller._run_poll_cycle("medium", poller._poll_medium, 0.1)  # noqa: SLF001
    poller._try_reconnect.assert_awaited_once_with()  # noqa: SLF001
    assert [call.args[0] for call in radio._transport.query.await_args_list] == ["TX;"]  # noqa: SLF001
    assert [item.value for item in emitted if item.path == OBSERVED_PTT_PATH] == [
        ObservedPtt.UNKNOWN
    ], "PTT_TRANSPORT_ERROR_PUBLICATION"
    assert store.snapshot().field(OBSERVED_PTT_PATH).value is ObservedPtt.UNKNOWN
    assert not any(str(item.path) == "global.tx_state.ptt" for item in emitted)


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["timeout", "transport"])
@pytest.mark.parametrize("sink_error", [RuntimeError, asyncio.CancelledError])
async def test_canonical_ptt_error_sink_failure_preserves_reconnect(
    monkeypatch: pytest.MonkeyPatch,
    failure: str,
    sink_error: type[BaseException],
) -> None:
    radio, poller, _, _, _ = _canonical_ptt_path(monkeypatch)
    radio.read_transmit_state = AsyncMock(
        return_value=TxStateReading(None, failure=failure)
    )
    sink = MagicMock(side_effect=sink_error("sink failed"))
    poller._observation_callback = sink  # noqa: SLF001
    error_type = CatTimeoutError if failure == "timeout" else CatTransportError
    with pytest.raises(error_type, match=f"PTT read failed: {failure}"):
        await poller._emit_medium_observations()  # noqa: SLF001
    poller._try_reconnect = AsyncMock(side_effect=asyncio.CancelledError)  # noqa: SLF001
    with pytest.raises(asyncio.CancelledError):
        await poller._run_poll_cycle("medium", poller._poll_medium, 0.1)  # noqa: SLF001
    poller._try_reconnect.assert_awaited_once_with()  # noqa: SLF001
    assert [args.args[0][0].value for args in sink.call_args_list] == [
        ObservedPtt.UNKNOWN,
        ObservedPtt.UNKNOWN,
    ]


@pytest.mark.asyncio
async def test_canonical_ptt_boundary_sink_failure_preserves_reconnect(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    radio, poller, store, _, _ = _canonical_ptt_path(monkeypatch)
    sink = MagicMock(side_effect=RuntimeError("sink failed"))
    poller._observation_callback = sink  # noqa: SLF001
    radio._transport._maybe_reconnect_needed = lambda: True  # noqa: SLF001
    radio._transport.reconnect = AsyncMock()  # noqa: SLF001
    await poller._try_reconnect()  # noqa: SLF001
    radio._transport.reconnect.assert_awaited_once_with()  # noqa: SLF001
    sink.assert_called_once()
    observation = sink.call_args.args[0][0]
    assert (observation.value, observation.provider_generation) == (
        ObservedPtt.UNKNOWN,
        store.provider_generation,
    )
    assert store.provider_generation == 1


@pytest.mark.asyncio
async def test_canonical_ptt_capture_only_reconnect_uses_current_generation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    radio, poller, store, clock, emitted = _canonical_ptt_path(monkeypatch)
    poller.bind_provider_generation(capture=lambda: store.provider_generation)
    generation = store.begin_provider_generation()
    assert generation > 0
    _seed_canonical_ptt(radio, store, clock, ObservedPtt.ON)
    radio._transport._maybe_reconnect_needed = lambda: True  # noqa: SLF001

    async def reconnect() -> None:
        assert store.provider_generation == generation
        assert project_observed_ptt(store.snapshot()) is ObservedPtt.UNKNOWN
        assert [(item.value, item.provider_generation) for item in emitted] == [
            (ObservedPtt.UNKNOWN, generation)
        ]
        await asyncio.sleep(0)
        radio._transport.stats.reconnects += 1  # noqa: SLF001

    radio._transport.reconnect = AsyncMock(side_effect=reconnect)  # noqa: SLF001
    await poller._try_reconnect()  # noqa: SLF001
    radio._transport.reconnect.assert_awaited_once_with()  # noqa: SLF001
    assert project_observed_ptt(store.snapshot()) is ObservedPtt.UNKNOWN
    assert store.provider_generation == generation
    assert store.snapshot().field(OBSERVED_PTT_PATH).provider_generation == generation
    assert radio._transport.stats.reconnects == 1  # noqa: SLF001


@pytest.mark.asyncio
async def test_canonical_ptt_cancelled_read_does_not_emit_success(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    radio, poller, _, _, emitted = _canonical_ptt_path(monkeypatch)
    radio._transport.query.side_effect = asyncio.CancelledError  # noqa: SLF001
    with pytest.raises(asyncio.CancelledError):
        await poller._emit_medium_observations()  # noqa: SLF001
    assert emitted == []


@pytest.mark.asyncio
@pytest.mark.parametrize("advance_store", [False, True])
async def test_canonical_ptt_reconnect_discards_late_old_read(
    monkeypatch: pytest.MonkeyPatch, advance_store: bool
) -> None:
    radio, poller, store, clock, emitted = _canonical_ptt_path(monkeypatch)
    _seed_canonical_ptt(radio, store, clock, ObservedPtt.ON)
    started = asyncio.get_running_loop().create_future()
    release: asyncio.Future[str] = asyncio.get_running_loop().create_future()

    async def delayed(*args: object, **kwargs: object) -> str:
        started.set_result(asyncio.current_task())
        return await release

    radio._transport.query.side_effect = delayed  # noqa: SLF001
    task = asyncio.create_task(poller._emit_medium_observations())  # noqa: SLF001
    try:
        assert await asyncio.wait_for(asyncio.shield(started), 5.0) is task
        assert not task.done() and not release.done()
        assert store.provider_generation == 0
        assert project_observed_ptt(store.snapshot()) is ObservedPtt.ON
        if advance_store:
            radio._transport._maybe_reconnect_needed = lambda: True  # noqa: SLF001

            async def reconnect() -> None:
                assert store.provider_generation == 1
                assert project_observed_ptt(store.snapshot()) is ObservedPtt.UNKNOWN
                assert [item.value for item in emitted] == [ObservedPtt.UNKNOWN]
                radio._transport.stats.reconnects += 1  # noqa: SLF001

            radio._transport.reconnect = AsyncMock(side_effect=reconnect)  # noqa: SLF001
            await poller._try_reconnect()  # noqa: SLF001
            radio._transport.reconnect.assert_awaited_once_with()  # noqa: SLF001
        else:
            radio._transport.stats.reconnects += 1  # noqa: SLF001
            poller._sync_tx_target_generation()  # noqa: SLF001
        assert store.provider_generation == int(advance_store)
        assert radio._transport.stats.reconnects == 1  # noqa: SLF001
        assert not task.done() and not release.done()
        boundary_snapshot = store.snapshot()
        boundary = len(emitted)
        release.set_result("TX1")
        assert await asyncio.wait_for(asyncio.shield(task), 5.0) is True
    finally:
        if not release.done():
            release.set_result("TX1")
        if not task.done():
            task.cancel()
        await asyncio.gather(task, return_exceptions=True)
    assert task.done() and not task.cancelled() and task.exception() is None
    assert [call.args[0] for call in radio._transport.query.await_args_list] == ["TX;"]  # noqa: SLF001
    assert project_observed_ptt(boundary_snapshot) is ObservedPtt.UNKNOWN, (
        "PTT_BOUNDARY_INVALIDATION"
    )
    assert any(
        item.path == OBSERVED_PTT_PATH
        and item.value is ObservedPtt.UNKNOWN
        and item.provider_generation == store.provider_generation
        for item in emitted[:boundary]
    ), "PTT_BOUNDARY_PUBLICATION"
    assert project_observed_ptt(store.snapshot()) is ObservedPtt.UNKNOWN
    assert store.snapshot().field(OBSERVED_PTT_PATH).value is ObservedPtt.UNKNOWN
    assert not any(
        item.path in (OBSERVED_PTT_PATH, FieldPath.global_("tx_state", "ptt"))
        for item in emitted[boundary:]
    )


@pytest.mark.asyncio
async def test_canonical_ptt_store_only_generation_discards_held_read(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    radio, poller, store, clock, emitted = _canonical_ptt_path(monkeypatch)
    _seed_canonical_ptt(radio, store, clock, ObservedPtt.ON)
    started = asyncio.get_running_loop().create_future()
    release: asyncio.Future[str] = asyncio.get_running_loop().create_future()

    async def delayed(*args: object, **kwargs: object) -> str:
        started.set_result(asyncio.current_task())
        return await release

    radio._transport.query.side_effect = delayed  # noqa: SLF001
    task = asyncio.create_task(poller._emit_medium_observations())  # noqa: SLF001
    try:
        assert await asyncio.wait_for(asyncio.shield(started), 5.0) is task
        assert not task.done() and not release.done()
        assert store.provider_generation == 0
        assert radio._transport.stats.reconnects == 0  # noqa: SLF001
        assert project_observed_ptt(store.snapshot()) is ObservedPtt.ON
        assert store.begin_provider_generation() == 1
        assert not task.done() and not release.done()
        assert radio._transport.stats.reconnects == 0  # noqa: SLF001
        assert emitted == []
        release.set_result("TX1")
        assert await asyncio.wait_for(asyncio.shield(task), 5.0) is True
    finally:
        if not release.done():
            release.set_result("TX1")
        if not task.done():
            task.cancel()
        await asyncio.gather(task, return_exceptions=True)
    assert task.done() and not task.cancelled() and task.exception() is None
    assert store.provider_generation == 1
    assert radio._transport.stats.reconnects == 0  # noqa: SLF001
    assert [call.args[0] for call in radio._transport.query.await_args_list] == ["TX;"]  # noqa: SLF001
    assert emitted == []
    assert project_observed_ptt(store.snapshot()) is ObservedPtt.UNKNOWN


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("frame", "expected"), [("TX0", ObservedPtt.OFF), ("TX1", ObservedPtt.ON)]
)
async def test_canonical_ptt_current_generation_held_read_publishes_after_release(
    monkeypatch: pytest.MonkeyPatch, frame: str, expected: ObservedPtt
) -> None:
    radio, poller, store, clock, emitted = _canonical_ptt_path(monkeypatch)
    previous = ObservedPtt.OFF if expected is ObservedPtt.ON else ObservedPtt.ON
    _seed_canonical_ptt(radio, store, clock, previous)
    started = asyncio.get_running_loop().create_future()
    release: asyncio.Future[str] = asyncio.get_running_loop().create_future()

    async def delayed(*args: object, **kwargs: object) -> str:
        started.set_result(asyncio.current_task())
        return await release

    radio._transport.query.side_effect = delayed  # noqa: SLF001
    task = asyncio.create_task(poller._emit_medium_observations())  # noqa: SLF001
    try:
        assert await asyncio.wait_for(asyncio.shield(started), 5.0) is task
        assert not task.done() and not release.done()
        assert emitted == []
        assert project_observed_ptt(store.snapshot()) is previous
        release.set_result(frame)
        assert await asyncio.wait_for(asyncio.shield(task), 5.0) is True
    finally:
        if not release.done():
            release.set_result(frame)
        if not task.done():
            task.cancel()
        await asyncio.gather(task, return_exceptions=True)
    assert task.done() and not task.cancelled() and task.exception() is None
    assert store.provider_generation == 0
    assert radio._transport.stats.reconnects == 0  # noqa: SLF001
    assert [call.args[0] for call in radio._transport.query.await_args_list] == ["TX;"]  # noqa: SLF001
    assert [item.value for item in emitted if item.path == OBSERVED_PTT_PATH] == [
        expected
    ], "PTT_CURRENT_COMPLETION_PUBLICATION"
    assert project_observed_ptt(store.snapshot()) is expected
    assert store.snapshot().field(OBSERVED_PTT_PATH).provider_generation == 0


def make_radio(
    *,
    s_meter_main: int = 100,
    s_meter_sub: int = 50,
    freq_main: int = 14_074_000,
    freq_sub: int = 7_074_000,
    mode_main: tuple = ("USB", None),
    mode_sub: tuple = ("LSB", None),
    ptt: bool = False,
    agc: int = 2,
    af_level: int = 128,
    rf_gain: int = 200,
    squelch: int = 0,
    clarifier: tuple[bool, bool] = (False, False),
    clarifier_freq: int = 0,
    manual_notch: tuple[bool, int] = (False, 0),
    narrow: bool = False,
    vfo_select: int = 0,
) -> MagicMock:
    """Return a mock YaesuCatRadio with sensible defaults."""
    radio = MagicMock()
    radio.radio_state = RadioState()
    radio.capabilities = {
        "audio",
        "dual_rx",
        "af_level",
        "rf_gain",
        "squelch",
        "attenuator",
        "preamp",
        "nb",
        "nr",
        "notch",
        "if_shift",
        "contour",
        "filter_width",
        "tx",
        "split",
        "vox",
        "compressor",
        "cw",
        "rit",
        "xit",
        "tuner",
        "meters",
        "repeater_tone",
        "tsql",
        "data_mode",
        "scan",
        "dial_lock",
    }
    radio.profile.tx_interlock_disposition_overrides = {}

    radio.get_s_meter = AsyncMock(
        side_effect=lambda r=0: s_meter_main if r == 0 else s_meter_sub
    )
    radio.read_s_meter = AsyncMock(
        side_effect=lambda r=0: s_meter_main if r == 0 else s_meter_sub
    )
    radio.get_freq = AsyncMock(
        side_effect=lambda r=0: freq_main if r == 0 else freq_sub
    )
    radio.read_freq = AsyncMock(
        side_effect=lambda r=0: freq_main if r == 0 else freq_sub
    )
    radio.get_mode = AsyncMock(
        side_effect=lambda r=0: mode_main if r == 0 else mode_sub
    )
    radio.read_mode = AsyncMock(
        side_effect=lambda r=0: mode_main if r == 0 else mode_sub
    )
    radio.get_ptt = AsyncMock(return_value=ptt)
    radio.read_ptt = AsyncMock(return_value=ptt)
    radio.read_transmit_state = AsyncMock(
        return_value=TxStateReading(
            ptt, "tx_cat" if ptt else "rx", "yaesu_poll_response", True
        )
    )
    radio.get_agc = AsyncMock(return_value=agc)
    radio.get_af_level = AsyncMock(return_value=af_level)
    radio.read_af_level = AsyncMock(return_value=af_level)
    radio.get_rf_gain = AsyncMock(return_value=rf_gain)
    radio.read_rf_gain = AsyncMock(return_value=rf_gain)
    radio.get_squelch = AsyncMock(return_value=squelch)
    radio.read_squelch = AsyncMock(return_value=squelch)
    radio.get_clarifier = AsyncMock(return_value=clarifier)
    radio.get_clarifier_freq = AsyncMock(return_value=clarifier_freq)
    radio.get_manual_notch = AsyncMock(return_value=manual_notch)
    radio.get_narrow = AsyncMock(return_value=narrow)
    radio.read_narrow = AsyncMock(return_value=narrow)
    # Filter / IF-shift DSP control reads (MOR-445).
    radio.get_filter_width = AsyncMock(return_value=2400)
    radio.read_filter_width = AsyncMock(return_value=2400)
    radio.get_if_shift = AsyncMock(return_value=0)
    radio.read_if_shift = AsyncMock(return_value=0)
    radio.get_vfo_select = AsyncMock(return_value=vfo_select)
    radio.read_vfo_select = AsyncMock(return_value=vfo_select)
    radio.get_split = AsyncMock(return_value=False)
    radio.read_split = AsyncMock(return_value=False)
    radio.get_alc_meter = AsyncMock(return_value=0)
    radio.read_alc_meter = AsyncMock(return_value=0)
    radio.get_power_meter = AsyncMock(return_value=0)
    radio.read_power_meter = AsyncMock(return_value=0)
    radio.get_comp_meter = AsyncMock(return_value=0)
    radio.read_comp_meter = AsyncMock(return_value=0)
    radio.get_swr_meter = AsyncMock(return_value=0)
    radio.read_swr_meter = AsyncMock(return_value=0)
    radio._read_meter = AsyncMock(return_value=(0, 0))
    radio.get_keyer_speed = AsyncMock(return_value=20)
    radio.get_key_pitch = AsyncMock(return_value=30)  # idx — Yaesu-internal API
    radio.get_cw_pitch = AsyncMock(return_value=600)  # Hz — Icom-spelled API (#1162)
    radio.get_break_in = AsyncMock(return_value=False)
    radio.get_break_in_delay = AsyncMock(return_value=0)
    radio.get_cw_spot = AsyncMock(return_value=False)
    radio.get_rx_func = AsyncMock(return_value=0)
    radio.get_tx_func = AsyncMock(return_value=0)
    return radio


def _real_ftx1_control_path(
    *, receiver_count: int = 2
) -> tuple[
    YaesuCatRadio,
    ControlHandler,
    YaesuCatPoller,
    StateStore,
    Callable[[Sequence[Observation]], None],
]:
    """Build the production Web queue and observation-poller route."""
    radio = YaesuCatRadio("/dev/null", profile="ftx1", audio_driver=MagicMock())
    if receiver_count != radio.receiver_count:
        radio._config = replace(  # noqa: SLF001
            radio._config,  # noqa: SLF001
            receiver_count=receiver_count,
            vfo_scheme="ab" if receiver_count == 1 else radio._config.vfo_scheme,  # noqa: SLF001
        )
        radio._profile_cache = None  # noqa: SLF001
    radio._transport._connected = True  # noqa: SLF001
    radio._transport.write = AsyncMock()  # noqa: SLF001
    store = StateStore()
    queue = CommandQueue()
    server = SimpleNamespace(command_queue=queue, command_state_store=store)
    handler = ControlHandler(
        None,  # type: ignore[arg-type]
        radio,
        "test",
        radio.model,
        server=server,
        session_id="ws-ftx1",
    )
    server.command_service = handler._command_service  # noqa: SLF001

    def accept(observations: Sequence[Observation]) -> None:
        for observation in observations:
            server.command_service.apply_observation(observation)

    poller = radio.create_observation_poller(
        callback=accept,
        command_queue=queue,
    )
    poller.bind_provider_generation(capture=lambda: store.provider_generation)
    _set_fresh_ptt_observation(poller, active=False)
    return radio, handler, poller, store, accept


def _normalized_255(raw: int) -> float:
    return raw / 255.0


def _normalized_power(raw_watts: int, *, max_watts: int = 100) -> float:
    return raw_watts / max_watts


def _declared_tx_target(frequency_hz: int) -> list[tuple[str, object]]:
    profile = _profile_state_acquisition()
    path = FieldPath.global_("tx_state", "tx_target")
    if not profile.capability_for(path).can_poll:
        return []
    return [
        (
            str(path),
            KnownTxTarget(receiver="MAIN", slot=None, frequency_hz=frequency_hz),
        )
    ]


class _SideEffectingYaesuRadio:
    capabilities = {
        "dual_rx",
        "af_level",
        "rf_gain",
        "squelch",
        "meters",
        "tx",
        "vox",
        "compressor",
    }

    def __init__(self) -> None:
        self.radio_state = RadioState()
        self.radio_state.main.freq = 1
        self.radio_state.main.mode = "INIT-MAIN"
        self.radio_state.sub.freq = 2
        self.radio_state.sub.mode = "INIT-SUB"
        self.radio_state.main.s_meter = 3
        self.radio_state.sub.s_meter = 4
        self.radio_state.main.af_level = 5
        self.radio_state.main.rf_gain = 6
        self.radio_state.main.squelch = 7
        self.radio_state.sub.af_level = 8
        self.radio_state.sub.rf_gain = 9
        self.radio_state.sub.squelch = 10
        self.radio_state.power_level = 11
        self.radio_state.mic_gain = 12
        self.radio_state.compressor_on = False
        self.radio_state.compressor_level = 13
        self.radio_state.vox_on = False
        self.radio_state.main.att = 14
        self.radio_state.main.preamp = 15
        self.radio_state.main.agc = 16
        self.radio_state.main.filter_width = 17
        self.radio_state.main.if_shift = 18
        self.radio_state.main.narrow = False
        self.profile = get_radio_profile("FTX-1")
        self.legacy_getter_calls = 0

    async def read_freq(self, receiver: int = 0) -> int:
        return 14_074_000 if receiver == 0 else 7_074_000

    async def get_freq(self, receiver: int = 0) -> int:
        self.legacy_getter_calls += 1
        value = await self.read_freq(receiver)
        target = self.radio_state.main if receiver == 0 else self.radio_state.sub
        target.freq = value
        return value

    async def read_mode(self, receiver: int = 0) -> tuple[str, int | None]:
        return ("USB" if receiver == 0 else "LSB"), None

    async def get_mode(self, receiver: int = 0) -> tuple[str, int | None]:
        self.legacy_getter_calls += 1
        value, filter_width = await self.read_mode(receiver)
        target = self.radio_state.main if receiver == 0 else self.radio_state.sub
        target.mode = value
        return value, filter_width

    async def read_ptt(self) -> bool:
        return False

    async def read_transmit_state(self) -> TxStateReading:
        return TxStateReading(await self.read_ptt(), "rx", "yaesu_poll_response", True)

    async def get_ptt(self) -> bool:
        self.legacy_getter_calls += 1
        value = await self.read_ptt()
        self.radio_state.ptt = value
        return value

    async def get_tx_func(self) -> int:
        """Pure native FT0 fixture; unlike legacy getters it mutates no state."""
        return 0

    async def read_s_meter(self, receiver: int = 0) -> int:
        return 150 if receiver == 0 else 75

    async def get_s_meter(self, receiver: int = 0) -> int:
        self.legacy_getter_calls += 1
        value = await self.read_s_meter(receiver)
        target = self.radio_state.main if receiver == 0 else self.radio_state.sub
        target.s_meter = value
        return value

    async def read_af_level(self, receiver: int = 0) -> int:
        return 128 if receiver == 0 else 64

    async def get_af_level(self, receiver: int = 0) -> int:
        self.legacy_getter_calls += 1
        value = await self.read_af_level(receiver)
        target = self.radio_state.main if receiver == 0 else self.radio_state.sub
        target.af_level = value
        return value

    async def read_rf_gain(self, receiver: int = 0) -> int:
        return 180 if receiver == 0 else 90

    async def get_rf_gain(self, receiver: int = 0) -> int:
        self.legacy_getter_calls += 1
        value = await self.read_rf_gain(receiver)
        target = self.radio_state.main if receiver == 0 else self.radio_state.sub
        target.rf_gain = value
        return value

    async def read_squelch(self, receiver: int = 0) -> int:
        return 12 if receiver == 0 else 8

    async def get_squelch(self, receiver: int = 0) -> int:
        self.legacy_getter_calls += 1
        value = await self.read_squelch(receiver)
        target = self.radio_state.main if receiver == 0 else self.radio_state.sub
        target.squelch = value
        return value

    async def read_power(self) -> tuple[int, int]:
        return (2, 55)

    async def get_power(self) -> tuple[int, int]:
        self.legacy_getter_calls += 1
        head, watts = await self.read_power()
        self.radio_state.power_level = watts
        return head, watts

    async def read_mic_gain(self) -> int:
        return 40

    async def get_mic_gain(self) -> int:
        self.legacy_getter_calls += 1
        value = await self.read_mic_gain()
        self.radio_state.mic_gain = value
        return value

    async def read_processor(self) -> bool:
        return True

    async def get_processor(self) -> bool:
        self.legacy_getter_calls += 1
        value = await self.read_processor()
        self.radio_state.compressor_on = value
        return value

    async def read_processor_level(self) -> int:
        return 25

    async def get_processor_level(self) -> int:
        self.legacy_getter_calls += 1
        value = await self.read_processor_level()
        self.radio_state.compressor_level = value
        return value

    async def read_vox(self) -> bool:
        return True

    async def get_vox(self) -> bool:
        self.legacy_getter_calls += 1
        value = await self.read_vox()
        self.radio_state.vox_on = value
        return value

    async def read_attenuator(self, receiver: int = 0) -> bool:
        return True

    async def get_attenuator(self, receiver: int = 0) -> bool:
        self.legacy_getter_calls += 1
        value = await self.read_attenuator(receiver)
        self.radio_state.main.att = int(value)
        return value

    async def read_preamp(self, receiver: int = 0) -> int:
        return 2

    async def get_preamp(self, band: int = 0) -> int:
        self.legacy_getter_calls += 1
        value = await self.read_preamp(band)
        self.radio_state.main.preamp = value
        return value

    async def read_agc(self, receiver: int = 0) -> int:
        return 3

    async def get_agc(self, receiver: int = 0) -> int:
        self.legacy_getter_calls += 1
        value = await self.read_agc(receiver)
        self.radio_state.main.agc = value
        return value

    async def read_filter_width(
        self, receiver: int = 0, mode: str | None = None
    ) -> int:
        return 2400

    async def get_filter_width(self, receiver: int = 0) -> int:
        self.legacy_getter_calls += 1
        value = await self.read_filter_width(receiver)
        self.radio_state.main.filter_width = value
        return value

    async def read_if_shift(self, receiver: int = 0) -> int:
        return 200

    async def get_if_shift(self, receiver: int = 0) -> int:
        self.legacy_getter_calls += 1
        value = await self.read_if_shift(receiver)
        self.radio_state.main.if_shift = value
        return value

    async def read_narrow(self, receiver: int = 0) -> bool:
        return True

    async def get_narrow(self, receiver: int = 0) -> bool:
        self.legacy_getter_calls += 1
        value = await self.read_narrow(receiver)
        self.radio_state.main.narrow = value
        return value

    async def read_vfo_select(self) -> int:
        return 1

    async def get_vfo_select(self) -> int:
        self.legacy_getter_calls += 1
        value = await self.read_vfo_select()
        self.radio_state.vfo_select = value
        self.radio_state.active = "SUB" if value else "MAIN"
        return value


def _profile_state_acquisition() -> RadioAcquisitionProfile:
    profile = get_radio_profile("FTX-1")
    assert profile.state_acquisition is not None
    return profile.state_acquisition


def _tx_target_radio() -> MagicMock:
    radio = make_radio()
    profile = _profile_state_acquisition()
    target_path = FieldPath.global_("tx_state", "tx_target")
    if not profile.capability_for(target_path).can_poll:
        profile = replace(
            profile,
            capabilities=profile.capabilities
            + (FieldCapability(path=target_path, polling=True),),
        )
    radio.profile.state_acquisition = profile
    radio._transport = SimpleNamespace(
        stats=SimpleNamespace(reconnects=0),
        reconnect=AsyncMock(),
        _maybe_reconnect_needed=lambda: False,
    )
    return radio


def _target_observation(
    radio: MagicMock, value: KnownTxTarget | UnknownTxTarget
) -> Observation:
    profile = radio.profile.state_acquisition
    return ProviderObservationAdapter(
        profile, source="yaesu_poll_response", transport="serial"
    ).observation(
        FieldPath.global_("tx_state", "tx_target"),
        value,
        native_id="read_tx_target",
    )


def _ptt_observation(
    value: bool, *, observed_at: float, max_age: float = 1.0
) -> Observation:
    return replace(
        ProviderObservationAdapter(
            _profile_state_acquisition(), "yaesu_poll_response", "serial"
        ).observation(FieldPath.global_("tx_state", "ptt"), value),
        timestamp_monotonic=observed_at,
        max_age=max_age,
    )


def _set_fresh_ptt_observation(poller: YaesuCatPoller, *, active: bool) -> None:
    poller._ptt_observation = replace(  # noqa: SLF001
        _ptt_observation(active, observed_at=asyncio.get_running_loop().time()),
        provider_generation=poller._captured_provider_generation(),  # noqa: SLF001
    )
    poller._ptt_connection_generation = (  # noqa: SLF001
        poller._current_tx_target_generation()  # noqa: SLF001
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["cancel", "replace", "error", "readback"])
async def test_yaesu_drain_claims_live_pending_finite_turn(mode, monkeypatch):
    from test_command_queue_execution import assert_live_pending_turn

    queue = CommandQueue()
    poller = YaesuCatPoller(make_radio(), command_queue=queue)
    _set_fresh_ptt_observation(poller, active=False)

    def install_readback(note):
        original = poller._track_receiver_select_readback

        def track(entry):
            original(entry)
            if entry.command == SetFreq(1):
                note()

        monkeypatch.setattr(poller, "_track_receiver_select_readback", track)

    await assert_live_pending_turn(
        queue,
        poller._drain_commands,
        lambda leaf: monkeypatch.setattr(poller, "_execute_command", leaf),
        mode=mode,
        install_readback=install_readback,
    )


async def _drain_with_ptt(
    poller: YaesuCatPoller,
    clock: list[float],
    now: float,
    active: bool | None,
) -> None:
    clock[0] = now
    if active is None:
        poller._invalidate_ptt_observation()  # noqa: SLF001
    else:
        poller._ptt_observation = _ptt_observation(  # noqa: SLF001
            active, observed_at=now
        )
        poller._ptt_connection_generation = (  # noqa: SLF001
            poller._current_tx_target_generation()  # noqa: SLF001
        )
    await poller._drain_commands()  # noqa: SLF001


@pytest.mark.asyncio
async def test_yaesu_releases_held_entry_before_finite_current_turn(monkeypatch):
    clock, queue, seen = [20.0], CommandQueue(), []
    monkeypatch.setattr(
        "rigplane.backends.yaesu_cat.poller.time.monotonic", lambda: clock[0]
    )
    poller = YaesuCatPoller(make_radio(), command_queue=queue)
    reply = asyncio.get_running_loop().create_future()
    held = SetSplit(True)

    async def leaf(command):
        seen.append(command)
        if command == SetFreq(1):
            queue.put_ordered(SetFreq(3))

    monkeypatch.setattr(poller, "_execute_command", leaf)
    queue.put_ordered(held, future=reply)
    try:
        await _drain_with_ptt(poller, clock, 20.0, True)
        await _drain_with_ptt(poller, clock, 20.1, False)
        assert not reply.done()
        queue.put_ordered(SetFreq(1))
        queue.put_ordered(SetFreq(2))
        await _drain_with_ptt(poller, clock, 21.1, False)
        assert seen == [held, SetFreq(1), SetFreq(2)]
        assert reply.result() is None
        assert [e.command for e in queue.drain_entries()] == [SetFreq(3)]
    finally:
        reply.cancel()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("active", "message"),
    [(True, "RF state is TX"), (None, "RF state is unknown")],
    ids=("tx", "unknown"),
)
async def test_yaesu_immediate_hard_block_families_fail_before_dispatch(
    monkeypatch: pytest.MonkeyPatch,
    active: bool | None,
    message: str,
) -> None:
    from rigplane.runtime._poller_types import (
        PttOn,
        ScanStart,
        SendCiv,
        SetAntenna1,
        SetAntenna2,
        SetCivOutputAnt,
        SetRxAntenna,
        SetRxAntennaAnt1,
        SetRxAntennaAnt2,
        SetTunerStatus,
    )

    monkeypatch.setattr(
        "rigplane.backends.yaesu_cat.poller.time.monotonic", lambda: 10.5
    )
    commands = (
        PttOn(),
        SendCiv(command=0x1C),
        ScanStart(),
        SetTunerStatus(1),
        SetTunerStatus(2),
        SetAntenna1(True),
        SetAntenna2(True),
        SetRxAntenna(antenna=1, on=True),
        SetRxAntennaAnt1(True),
        SetRxAntennaAnt2(True),
        SetCivOutputAnt(True),
    )

    for command in commands:
        radio = make_radio()
        radio.set_ptt = AsyncMock()
        radio.set_tuner = AsyncMock()
        poller = YaesuCatPoller(radio, callback=lambda _: None)
        if active is not None:
            _set_fresh_ptt_observation(poller, active=active)

        with pytest.raises(CommandError, match=message):
            await poller._execute_command(command)  # noqa: SLF001

        radio.set_ptt.assert_not_awaited()
        radio.set_tuner.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("active", (True, None), ids=("tx", "unknown"))
async def test_yaesu_emergency_off_commands_bypass_immediate_gate(
    monkeypatch: pytest.MonkeyPatch, active: bool | None
) -> None:
    from rigplane.runtime._poller_types import PttOff, SetPowerstat, SetTunerStatus

    monkeypatch.setattr(
        "rigplane.backends.yaesu_cat.poller.time.monotonic", lambda: 10.5
    )
    radio = make_radio()
    radio.set_ptt = AsyncMock()
    radio.set_powerstat = AsyncMock()
    radio.set_tuner = AsyncMock()
    poller = YaesuCatPoller(radio, callback=lambda _: None)
    if active is not None:
        _set_fresh_ptt_observation(poller, active=active)

    await poller._execute_command(PttOff())  # noqa: SLF001
    await poller._execute_command(SetPowerstat(on=False))  # noqa: SLF001
    await poller._execute_command(SetTunerStatus(0))  # noqa: SLF001

    radio.set_ptt.assert_awaited_once_with(False)
    radio.set_powerstat.assert_awaited_once_with(False)
    radio.set_tuner.assert_awaited_once_with(0)


@pytest.mark.asyncio
async def test_yaesu_known_rx_preserves_immediate_dispatch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from rigplane.runtime._poller_types import PttOn, SendCiv

    monkeypatch.setattr(
        "rigplane.backends.yaesu_cat.poller.time.monotonic", lambda: 10.5
    )
    radio = make_radio()
    radio.set_ptt = AsyncMock()
    poller = YaesuCatPoller(radio, callback=lambda _: None)
    _set_fresh_ptt_observation(poller, active=False)

    await poller._execute_command(PttOn())  # noqa: SLF001
    radio.set_ptt.assert_awaited_once_with(True)
    with pytest.raises(
        NotImplementedError, match="SendCiv unsupported by Yaesu CAT dispatcher"
    ):
        await poller._execute_command(SendCiv(command=0x1C))  # noqa: SLF001


@pytest.mark.asyncio
async def test_yaesu_profile_power_on_defer_is_command_bound_across_queue_and_execute(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from rigplane.runtime._poller_types import SetPowerstat

    clock = [10.0]
    monkeypatch.setattr(
        "rigplane.backends.yaesu_cat.poller.time.monotonic", lambda: clock[0]
    )
    radio, queue = make_radio(), CommandQueue()
    radio.set_powerstat = AsyncMock()
    poller = YaesuCatPoller(radio, command_queue=queue)
    override = {TxInterlockCommandFamily.POWER_ON: TxInterlockDisposition.DEFER}
    radio.profile.tx_interlock_disposition_overrides = override

    with pytest.raises(CommandError, match="unknown"):
        await poller._execute_command(SetPowerstat(on=True))  # noqa: SLF001

    future = asyncio.get_running_loop().create_future()
    queue.put_ordered(SetPowerstat(on=True), future=future)
    await _drain_with_ptt(poller, clock, 10.0, True)
    await _drain_with_ptt(poller, clock, 10.5, False)
    await _drain_with_ptt(poller, clock, 11.5, False)
    await poller._drain_commands()  # noqa: SLF001
    assert future.result() is None

    trapping = MagicMock(wraps=override)
    trapping.items.side_effect = (override.items(), RuntimeError("second access"))
    radio.profile.tx_interlock_disposition_overrides = trapping
    trapped, service = asyncio.get_running_loop().create_future(), MagicMock()
    queue.put_ordered(
        SetPowerstat(on=True),
        future=trapped,
        command_id="trap",
        command_service=service,
    )
    await _drain_with_ptt(poller, clock, 12.0, True)
    assert isinstance(trapped.exception(), RuntimeError)
    service.fail_command.assert_called_once()
    assert poller._deferred_tx_lane.pending is None  # noqa: SLF001
    radio.set_powerstat.assert_awaited_once_with(True)


@pytest.mark.asyncio
async def test_yaesu_profile_override_unknown_and_invalid_mapping_never_fail_open(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from rigplane.runtime._poller_types import SetPowerstat

    monkeypatch.setattr(
        "rigplane.backends.yaesu_cat.poller.time.monotonic", lambda: 30.0
    )
    radio, queue = make_radio(), CommandQueue()
    radio.set_powerstat = AsyncMock()
    poller = YaesuCatPoller(radio, command_queue=queue)
    radio.profile.tx_interlock_disposition_overrides = {
        TxInterlockCommandFamily.POWER_ON: TxInterlockDisposition.DEFER
    }
    unknown = asyncio.get_running_loop().create_future()
    queue.put_ordered(SetPowerstat(on=True), future=unknown)
    await poller._drain_commands()  # noqa: SLF001
    assert isinstance(unknown.exception(), CommandError)
    assert poller._deferred_tx_lane.pending is None  # noqa: SLF001

    radio.profile.tx_interlock_disposition_overrides = {
        TxInterlockCommandFamily.POWER_ON: TxInterlockDisposition.ALWAYS_PASS
    }
    malformed = asyncio.get_running_loop().create_future()
    queue.put_ordered(SetPowerstat(on=True), future=malformed)
    await poller._drain_commands()  # noqa: SLF001
    assert isinstance(malformed.exception(), ValueError)
    assert poller._deferred_tx_lane.pending is None  # noqa: SLF001
    radio.set_powerstat.assert_not_awaited()


@pytest.mark.asyncio
async def test_yaesu_trapping_profile_override_accessor_terminally_fails_entry(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from rigplane.runtime._poller_types import SetPowerstat

    class TrappingProfile:
        state_acquisition = None

        @property
        def tx_interlock_disposition_overrides(self) -> object:
            raise RuntimeError("trapping profile override accessor")

    monkeypatch.setattr(
        "rigplane.backends.yaesu_cat.poller.time.monotonic", lambda: 35.0
    )
    radio, queue = make_radio(), CommandQueue()
    radio.profile = TrappingProfile()
    radio.set_powerstat = AsyncMock()
    radio._transport.reconnect = AsyncMock()
    poller = YaesuCatPoller(radio, command_queue=queue)
    future = asyncio.get_running_loop().create_future()
    service = MagicMock()
    queue.put_ordered(
        SetPowerstat(on=True),
        future=future,
        command_id="trapping-profile",
        command_service=service,
    )

    await poller._drain_commands()  # noqa: SLF001

    assert isinstance(future.exception(), RuntimeError)
    service.fail_command.assert_called_once()
    assert poller._deferred_tx_lane.pending is None  # noqa: SLF001
    radio.set_powerstat.assert_not_awaited()
    radio._transport.reconnect.assert_not_awaited()


@pytest.mark.asyncio
async def test_yaesu_profile_override_cannot_change_structural_floors(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from rigplane.runtime._poller_types import PttOff, PttOn, SetPowerstat

    monkeypatch.setattr(
        "rigplane.backends.yaesu_cat.poller.time.monotonic", lambda: 40.0
    )
    radio = make_radio()
    radio.profile.tx_interlock_disposition_overrides = {
        TxInterlockCommandFamily.POWER_ON: TxInterlockDisposition.DEFER
    }
    radio.set_ptt = AsyncMock()
    radio.set_powerstat = AsyncMock()
    poller = YaesuCatPoller(radio)
    _set_fresh_ptt_observation(poller, active=True)

    with pytest.raises(CommandError, match="RF state is TX"):
        await poller._execute_command(PttOn())  # noqa: SLF001
    await poller._execute_command(PttOff())  # noqa: SLF001
    await poller._execute_command(SetPowerstat(on=False))  # noqa: SLF001
    radio.set_ptt.assert_awaited_once_with(False)
    radio.set_powerstat.assert_awaited_once_with(False)


@pytest.mark.asyncio
async def test_yaesu_deferred_command_supersedes_without_extending_expiry(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    clock = [10.0]
    monkeypatch.setattr(
        "rigplane.backends.yaesu_cat.poller.time.monotonic", lambda: clock[0]
    )
    radio, queue = make_radio(), CommandQueue()
    radio.set_split = AsyncMock()
    poller = YaesuCatPoller(radio, command_queue=queue)
    service = MagicMock()
    first = asyncio.get_running_loop().create_future()
    second = asyncio.get_running_loop().create_future()
    queue.put_ordered(
        SetSplit(False),
        future=first,
        command_id="first",
        command_service=service,
    )
    await _drain_with_ptt(poller, clock, 10.0, True)
    assert not first.done()
    queue.put_ordered(SetSplit(True), future=second)
    await _drain_with_ptt(poller, clock, 12.5, True)
    assert isinstance(first.exception(), CommandError)
    assert "superseded" in str(first.exception())
    assert not second.done()
    assert service.emit_lifecycle.call_args.args[1] == "superseded"
    await _drain_with_ptt(poller, clock, 13.0, False)
    assert isinstance(second.exception(), TimeoutError)
    radio.set_split.assert_not_awaited()


@pytest.mark.asyncio
async def test_yaesu_deferred_command_emits_held_lifecycle_once(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    clock = [10.0]
    monkeypatch.setattr(
        "rigplane.backends.yaesu_cat.poller.time.monotonic", lambda: clock[0]
    )
    radio, queue = make_radio(), CommandQueue()
    service = CommandService(
        executor=MagicMock(), state_store=StateStore(), clock=lambda: clock[0]
    )
    poller = YaesuCatPoller(radio, command_queue=queue)
    queue.put_ordered(SetSplit(True), command_id="held", command_service=service)

    await _drain_with_ptt(poller, clock, 10.0, True)
    event = service.lifecycle_events()[0]
    assert (event.command_id, event.state, event.timestamp_monotonic) == (
        "held",
        "queued",
        10.0,
    )
    assert event.details == {
        "heldBy": "tx_interlock",
        "reason": "tx_active",
        "expiresAt": 13.0,
    }

    await _drain_with_ptt(poller, clock, 10.5, True)
    await _drain_with_ptt(poller, clock, 10.75, False)
    await _drain_with_ptt(poller, clock, 11.0, None)
    assert service.lifecycle_events() == (event,)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("source", "expected_session"),
    (("websocket", "ws-ftx1"), ("http", None)),
)
async def test_ftx1_web_deferred_hold_preserves_ingress_lifecycle_context(
    monkeypatch: pytest.MonkeyPatch,
    source: str,
    expected_session: str | None,
) -> None:
    clock = [10.0]
    monkeypatch.setattr(
        "rigplane.backends.yaesu_cat.poller.time.monotonic", lambda: clock[0]
    )
    monkeypatch.setattr(
        "rigplane.core.command_service.time.monotonic", lambda: clock[0]
    )
    radio, handler, poller, _store, _accept = _real_ftx1_control_path()
    service = handler._command_service  # noqa: SLF001
    params = {"on": True}
    original_params = dict(params)

    await handler._enqueue_command(  # noqa: SLF001
        "set_split", params, command_id=f"held-{source}", source=source
    )
    ingress = service.lifecycle_events()[0]
    assert params == original_params
    await _drain_with_ptt(poller, clock, 10.0, True)

    held = service.lifecycle_events()[-1]
    expected_details = {
        "heldBy": "tx_interlock",
        "reason": "tx_active",
        "expiresAt": 13.0,
    }
    if expected_session is not None:
        expected_details["session_id"] = expected_session
    assert (held.command_id, held.state, held.source, held.target) == (
        ingress.command_id,
        "queued",
        ingress.source,
        ingress.target,
    )
    assert held.details == expected_details
    radio._transport.write.assert_not_awaited()  # noqa: SLF001


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("replacement_at", "terminal_state", "replacement_expiry"),
    ((22.5, "superseded", 23.0), (23.0, "timed_out", 26.0)),
)
async def test_yaesu_deferred_replacement_lifecycle_preserves_deadline_truth(
    monkeypatch: pytest.MonkeyPatch,
    replacement_at: float,
    terminal_state: str,
    replacement_expiry: float,
) -> None:
    clock = [20.0]
    monkeypatch.setattr(
        "rigplane.backends.yaesu_cat.poller.time.monotonic", lambda: clock[0]
    )
    radio, queue = make_radio(), CommandQueue()
    service = CommandService(
        executor=MagicMock(), state_store=StateStore(), clock=lambda: clock[0]
    )
    poller = YaesuCatPoller(radio, command_queue=queue)
    first = asyncio.get_running_loop().create_future()
    queue.put_ordered(
        SetSplit(False),
        future=first,
        command_id="first",
        command_service=service,
    )
    await _drain_with_ptt(poller, clock, 20.0, True)
    queue.put_ordered(SetSplit(True), command_id="replacement", command_service=service)
    await _drain_with_ptt(poller, clock, replacement_at, True)

    events = service.lifecycle_events()
    assert [(event.command_id, event.state) for event in events] == [
        ("first", "queued"),
        ("first", terminal_state),
        ("replacement", "queued"),
    ]
    assert events[-1].timestamp_monotonic == replacement_at
    assert events[-1].details == {
        "heldBy": "tx_interlock",
        "reason": "tx_active",
        "expiresAt": replacement_expiry,
    }
    assert first.exception() is not None


@pytest.mark.asyncio
async def test_yaesu_deferred_release_requires_continuous_fresh_rx(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    clock = [20.0]
    monkeypatch.setattr(
        "rigplane.backends.yaesu_cat.poller.time.monotonic", lambda: clock[0]
    )
    radio, queue = make_radio(), CommandQueue()
    radio.set_split = AsyncMock()
    poller = YaesuCatPoller(radio, command_queue=queue)
    future = asyncio.get_running_loop().create_future()
    queue.put_ordered(SetSplit(True), future=future)
    await _drain_with_ptt(poller, clock, 20.0, True)
    await _drain_with_ptt(poller, clock, 20.5, False)
    await _drain_with_ptt(poller, clock, 21.0, None)
    await _drain_with_ptt(poller, clock, 21.1, False)
    await _drain_with_ptt(poller, clock, 22.099, False)
    assert not future.done()
    radio.set_split.assert_not_awaited()
    await _drain_with_ptt(poller, clock, 22.1, False)
    await poller._drain_commands()  # noqa: SLF001
    assert future.result() is None
    radio.set_split.assert_awaited_once_with(True)


@pytest.mark.asyncio
async def test_yaesu_frequency_now_dispatches_without_entering_the_deferred_lane(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    clock = [20.0]
    monkeypatch.setattr(
        "rigplane.backends.yaesu_cat.poller.time.monotonic", lambda: clock[0]
    )
    radio, queue = make_radio(), CommandQueue()
    radio.set_freq = AsyncMock()
    poller = YaesuCatPoller(radio, command_queue=queue)
    future = asyncio.get_running_loop().create_future()
    queue.put_ordered(SetFreq(7_100_000), future=future)

    await _drain_with_ptt(poller, clock, 20.0, True)

    assert future.result() is None
    radio.set_freq.assert_awaited_once_with(7_100_000, receiver=0)
    assert poller._deferred_tx_lane.pending is None  # noqa: SLF001


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("command", "family", "method", "expected_args"),
    (
        (
            SetFreq(7_100_000),
            TxInterlockCommandFamily.FREQUENCY,
            "set_freq",
            (7_100_000,),
        ),
        (SetMode("USB"), TxInterlockCommandFamily.MODE, "set_mode", ("USB",)),
        (SetBand(3), TxInterlockCommandFamily.BAND, "set_band", (3,)),
        (
            SelectVfo("A"),
            TxInterlockCommandFamily.VFO_SELECT,
            "set_vfo_select",
            (0,),
        ),
        (
            VfoSwap(),
            TxInterlockCommandFamily.VFO_CONTENTS,
            "swap_vfo_ab",
            (0,),
        ),
        (
            VfoEqualize(),
            TxInterlockCommandFamily.VFO_CONTENTS,
            "equalize_vfo_ab",
            (0,),
        ),
    ),
)
async def test_yaesu_authority_approved_commands_dispatch_during_observed_tx(
    command: object,
    family: TxInterlockCommandFamily,
    method: str,
    expected_args: tuple[object, ...],
) -> None:
    radio = make_radio()
    radio.receiver_count = 1
    radio.profile.tx_interlock_disposition_overrides = {
        family: TxInterlockDisposition.DEFER
    }
    setattr(radio, method, AsyncMock())
    poller = YaesuCatPoller(radio)
    _set_fresh_ptt_observation(poller, active=True)

    await poller._execute_command(command)  # type: ignore[arg-type] # noqa: SLF001

    getattr(radio, method).assert_awaited_once()
    assert getattr(radio, method).await_args.args == expected_args
    assert poller._deferred_tx_lane.pending is None  # noqa: SLF001


@pytest.mark.asyncio
async def test_yaesu_descriptor_intent_uses_bound_authority_once() -> None:
    radio = make_radio()
    radio.set_civ_output_ant = AsyncMock()
    authority = MagicMock()
    authority.admit_managed_write = AsyncMock(return_value=True)
    poller = YaesuCatPoller(radio)
    poller.bind_managed_tx_authority(authority)
    _set_fresh_ptt_observation(poller, active=True)
    intent = bind_command_intent("set_civ_output_ant", {"on": True}, source="websocket")

    await poller._execute_command(intent)  # noqa: SLF001

    authority.admit_managed_write.assert_awaited_once_with(intent)
    radio.set_civ_output_ant.assert_awaited_once_with(on=True)
    with pytest.raises(RuntimeError, match="already bound"):
        poller.bind_managed_tx_authority(authority)


@pytest.mark.asyncio
@pytest.mark.parametrize("knownness", ("missing", "stale", "generation_mismatch"))
async def test_yaesu_unknown_deferred_command_fails_without_entering_lane(
    monkeypatch: pytest.MonkeyPatch,
    knownness: str,
) -> None:
    monkeypatch.setattr(
        "rigplane.backends.yaesu_cat.poller.time.monotonic", lambda: 30.0
    )
    radio, queue = make_radio(), CommandQueue()
    radio.set_split = AsyncMock()
    poller = YaesuCatPoller(radio, command_queue=queue)
    service = MagicMock()
    if knownness == "stale":
        _set_fresh_ptt_observation(poller, active=True)
        poller._ptt_observation = _ptt_observation(True, observed_at=28.0)  # noqa: SLF001
    elif knownness == "generation_mismatch":
        poller.bind_provider_generation(capture=lambda: 2)
        _set_fresh_ptt_observation(poller, active=True)
        poller._ptt_observation = replace(  # noqa: SLF001
            poller._ptt_observation,  # noqa: SLF001
            timestamp_monotonic=30.0,
            provider_generation=1,
        )
    future = asyncio.get_running_loop().create_future()
    queue.put_ordered(
        SetSplit(True),
        future=future,
        command_id="unknown",
        command_service=service,
    )
    await poller._drain_commands()  # noqa: SLF001
    error = future.exception()
    assert isinstance(error, CommandError)
    assert "unknown" in str(error)
    radio.set_split.assert_not_awaited()
    assert poller._deferred_tx_lane.pending is None  # noqa: SLF001
    service.emit_lifecycle.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("boundary", "reason"),
    (
        ("stop", "stopped"),
        ("reconnect", "serial reconnect"),
        ("provider-generation", "provider generation changed"),
        ("provider-replacement", "provider binding replaced"),
        ("connection-generation", "connection generation changed"),
        ("command-terminal", "no longer active"),
        ("session", "gone"),
    ),
)
async def test_yaesu_lifecycle_boundary_retires_held_deferred_command(
    monkeypatch: pytest.MonkeyPatch,
    boundary: str,
    reason: str,
) -> None:
    clock = [10.0]
    monkeypatch.setattr(
        "rigplane.backends.yaesu_cat.poller.time.monotonic", lambda: clock[0]
    )
    radio, queue, store = _tx_target_radio(), CommandQueue(), StateStore()
    radio.set_split = AsyncMock()
    poller = YaesuCatPoller(radio, command_queue=queue)
    poller.bind_provider_generation(
        capture=lambda: store.provider_generation,
        advance=store.begin_provider_generation,
    )
    future = asyncio.get_running_loop().create_future()
    service = MagicMock()
    queue.register_session("ws")
    queue.put_ordered(
        SetSplit(True),
        future=future,
        command_id="held",
        source="websocket",
        session_id="ws",
        command_service=service,
    )
    _set_fresh_ptt_observation(poller, active=True)
    await poller._drain_commands()  # noqa: SLF001
    assert not future.done()

    if boundary == "stop":
        await poller.stop()
    elif boundary == "reconnect":
        radio._transport._maybe_reconnect_needed = lambda: True
        await poller._try_reconnect()  # noqa: SLF001
    elif boundary == "provider-generation":
        store.begin_provider_generation()
    elif boundary == "provider-replacement":
        poller.bind_provider_generation(capture=lambda: 1)
    elif boundary == "command-terminal":
        service.retain_readback_expectations_for_dispatch.return_value = None
    elif boundary == "session":
        queue.unregister_session("ws")
    else:
        radio._transport.stats.reconnects += 1
        poller._sync_tx_target_generation()  # noqa: SLF001
    if boundary in {"command-terminal", "session"}:
        poller._deferred_tx_lane._entry.quiet_since = 9.0  # type: ignore[union-attr]  # noqa: SLF001
        poller._ptt_observation = _ptt_observation(False, observed_at=10.0)  # noqa: SLF001
    await poller._drain_commands()  # noqa: SLF001

    error = future.exception()
    assert isinstance(error, CommandError)
    assert reason in str(error)
    if boundary != "command-terminal":
        assert service.fail_command.call_args.kwargs["timed_out"] is False
    assert poller._deferred_tx_entry is None  # noqa: SLF001
    assert poller._deferred_tx_lane.pending is None  # noqa: SLF001

    clock[0] = 10.5
    _set_fresh_ptt_observation(poller, active=False)
    await poller._drain_commands()  # noqa: SLF001
    clock[0] = 11.5
    _set_fresh_ptt_observation(poller, active=False)
    await poller._drain_commands()  # noqa: SLF001
    radio.set_split.assert_not_awaited()


@pytest.mark.asyncio
async def test_yaesu_unhandled_always_pass_command_fails_truthfully() -> None:
    from rigplane.runtime._poller_types import ScanStop

    queue = CommandQueue()
    future: asyncio.Future[None] = asyncio.get_running_loop().create_future()
    queue.put_ordered(ScanStop(), future=future)
    poller = YaesuCatPoller(make_radio(), command_queue=queue)

    await poller._drain_commands()  # noqa: SLF001

    error = future.exception()
    assert isinstance(error, NotImplementedError)
    assert "ScanStop unsupported by Yaesu CAT dispatcher" in str(error)


@pytest.mark.asyncio
@pytest.mark.parametrize("error", (False, True))
async def test_stale_yaesu_medium_has_no_side_effects(error: bool) -> None:
    radio, store, gate = _tx_target_radio(), StateStore(), asyncio.Event()
    emitted: list[Observation] = []

    async def delayed_medium(
        *, ptt_callback: Callable[[Observation], None] | None = None
    ) -> tuple[Observation, ...]:
        await gate.wait()
        if error:
            raise CatTimeoutError("stale")
        return (
            ProviderObservationAdapter(
                _profile_state_acquisition(), "yaesu_poll_response", "serial"
            ).observation(FieldPath.global_("tx_state", "ptt"), True),
        )

    poller = YaesuCatPoller(radio, observation_callback=emitted.extend)
    poller.bind_provider_generation(
        capture=lambda: store.provider_generation,
        advance=store.begin_provider_generation,
    )
    with patch.object(YaesuObservationAdapter, "from_radio") as adapter:
        adapter.return_value.poll_medium = delayed_medium
        task = asyncio.create_task(poller._emit_medium_observations())  # noqa: SLF001
        await asyncio.sleep(0)
        store.begin_provider_generation()
        radio._transport.stats.reconnects += 1
        gate.set()
        await task

    assert not emitted and not poller._last_ptt  # noqa: SLF001
    assert poller._tx_target_known_generation is None  # noqa: SLF001


@pytest.mark.asyncio
@pytest.mark.parametrize("active", (False, True), ids=("rx", "tx"))
async def test_fresh_yaesu_ptt_observation_is_known(
    monkeypatch: pytest.MonkeyPatch, active: bool
) -> None:
    radio, store = _tx_target_radio(), StateStore()
    monkeypatch.setattr(
        YaesuObservationAdapter,
        "poll_medium",
        AsyncMock(return_value=(_ptt_observation(active, observed_at=10.0),)),
    )
    poller = YaesuCatPoller(radio, observation_callback=lambda _: None)
    poller.bind_provider_generation(capture=lambda: store.provider_generation)

    await poller._poll_medium()  # noqa: SLF001

    observation = poller._current_ptt_observation(now=10.5)  # noqa: SLF001
    assert observation is not None
    assert (observation.value, observation.timestamp_monotonic) == (active, 10.0)
    assert observation.provider_generation == store.provider_generation


@pytest.mark.asyncio
async def test_stale_yaesu_ptt_observation_is_unknown(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    radio = _tx_target_radio()
    monkeypatch.setattr(
        YaesuObservationAdapter,
        "poll_medium",
        AsyncMock(return_value=(_ptt_observation(True, observed_at=10.0),)),
    )
    poller = YaesuCatPoller(radio, observation_callback=lambda _: None)
    await poller._poll_medium()  # noqa: SLF001

    assert poller._current_ptt_observation(now=11.0) is None  # noqa: SLF001


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ("unread", "transport-error", "generic-error"))
async def test_yaesu_ptt_failure_invalidates_known_state(
    monkeypatch: pytest.MonkeyPatch, failure: str
) -> None:
    radio = _tx_target_radio()
    error_type = {
        "transport-error": CatTimeoutError,
        "generic-error": RuntimeError,
    }.get(failure)
    next_result: object = () if error_type is None else error_type("lost")
    monkeypatch.setattr(
        YaesuObservationAdapter,
        "poll_medium",
        AsyncMock(
            side_effect=[(_ptt_observation(True, observed_at=10.0),), next_result]
        ),
    )
    poller = YaesuCatPoller(radio, observation_callback=lambda _: None)
    await poller._poll_medium()  # noqa: SLF001

    if error_type is not None:
        with pytest.raises(error_type, match="lost"):
            await poller._poll_medium()  # noqa: SLF001
    else:
        await poller._poll_medium()  # noqa: SLF001

    assert poller._current_ptt_observation(now=10.5) is None  # noqa: SLF001
    assert not poller._last_ptt  # noqa: SLF001


@pytest.mark.asyncio
@pytest.mark.parametrize("boundary", ("reconnect", "provider-generation"))
async def test_yaesu_ptt_generation_change_is_unknown_until_newer_poll(
    monkeypatch: pytest.MonkeyPatch, boundary: str
) -> None:
    radio, store = _tx_target_radio(), StateStore()
    monkeypatch.setattr(
        YaesuObservationAdapter,
        "poll_medium",
        AsyncMock(
            side_effect=[
                (_ptt_observation(True, observed_at=10.0),),
                (_ptt_observation(False, observed_at=11.0),),
            ]
        ),
    )
    poller = YaesuCatPoller(radio, observation_callback=lambda _: None)
    poller.bind_provider_generation(capture=lambda: store.provider_generation)
    await poller._poll_medium()  # noqa: SLF001

    if boundary == "reconnect":
        radio._transport.stats.reconnects += 1
        poller._sync_tx_target_generation()  # noqa: SLF001
    else:
        store.begin_provider_generation()
    monkeypatch.setattr(
        "rigplane.backends.yaesu_cat.poller.time.monotonic", lambda: 10.5
    )
    with patch.object(YaesuObservationAdapter, "from_radio") as adapter:
        adapter.return_value.poll_rx_meters = AsyncMock(return_value=())
        adapter.return_value.poll_tx_meters = AsyncMock(return_value=())
        await poller._emit_fast_observations()  # noqa: SLF001
        adapter.return_value.poll_rx_meters.assert_awaited_once()
        adapter.return_value.poll_tx_meters.assert_not_awaited()
    assert poller._current_ptt_observation(now=10.5) is None  # noqa: SLF001
    assert not poller._last_ptt  # noqa: SLF001

    await poller._poll_medium()  # noqa: SLF001
    recovered = poller._current_ptt_observation(now=11.5)  # noqa: SLF001
    assert recovered is not None and recovered.value is False
    assert recovered.provider_generation == store.provider_generation


@pytest.mark.asyncio
async def test_stale_yaesu_fast_and_slow_have_no_ema_or_callback() -> None:
    radio, store, gate = make_radio(), StateStore(), asyncio.Event()
    emitted: list[Observation] = []
    poller = YaesuCatPoller(radio, observation_callback=emitted.extend)
    poller.bind_provider_generation(capture=lambda: store.provider_generation)

    async def fast(*, smooth_s_meter: object) -> tuple[Observation, ...]:
        await gate.wait()
        smooth_s_meter(0, 100)  # type: ignore[operator]
        return ()

    async def slow() -> tuple[Observation, ...]:
        await gate.wait()
        return ()

    adapter = MagicMock()
    adapter.poll_rx_meters = fast
    adapter.poll_slow_controls = slow
    adapter.poll_tx_controls = AsyncMock(return_value=())
    with patch.object(YaesuObservationAdapter, "from_radio", return_value=adapter):
        fast_task = asyncio.create_task(poller._emit_fast_observations())  # noqa: SLF001
        slow_task = asyncio.create_task(poller._emit_slow_control_observations())  # noqa: SLF001
        await asyncio.sleep(0)
        store.begin_provider_generation()
        gate.set()
        await asyncio.gather(fast_task, slow_task)

    assert not emitted and poller._ema_s_main is None  # noqa: SLF001


@pytest.mark.asyncio
async def test_each_serialized_reconnect_invalidates_its_store_generation() -> None:
    radio = _tx_target_radio()
    radio._transport._maybe_reconnect_needed = lambda: True
    store, emitted = StateStore(), []
    poller = YaesuCatPoller(radio, observation_callback=emitted.extend)
    poller.bind_provider_generation(
        capture=lambda: store.provider_generation,
        advance=store.begin_provider_generation,
    )
    await poller._try_reconnect()  # noqa: SLF001
    await poller._try_reconnect()  # noqa: SLF001
    poller._invalidate_tx_target(provider_generation=2)  # noqa: SLF001
    assert (radio._transport.reconnect.await_count, store.provider_generation) == (2, 2)
    assert [(item.path, item.provider_generation) for item in emitted] == [
        (path, generation)
        for generation in (1, 2)
        for path in (FieldPath.global_("tx_state", "tx_target"), OBSERVED_PTT_PATH)
    ]


def _state_write_target(node: ast.AST) -> str | None:
    parts: list[str] = []
    current = node
    while isinstance(current, ast.Attribute):
        parts.append(current.attr)
        current = current.value
    if isinstance(current, ast.Name) and current.id == "state" and parts:
        return ".".join(reversed(parts))
    if (
        len(parts) >= 2
        and parts[-1] == "_radio"
        and parts[-2] == "radio_state"
        and isinstance(current, ast.Name)
        and current.id == "self"
    ):
        state_parts = parts[:-2]
        if not state_parts:
            return None
        return ".".join(reversed(state_parts))
    return None


def _yaesu_poller_state_write_targets() -> set[str]:
    root = Path(__file__).resolve().parents[1]
    source = (root / "src/rigplane/backends/yaesu_cat/poller.py").read_text()
    module = ast.parse(source)
    targets: set[str] = set()
    for item in module.body:
        if not isinstance(item, ast.ClassDef) or item.name != "YaesuCatPoller":
            continue
        for method in item.body:
            if not isinstance(method, ast.AsyncFunctionDef):
                continue
            if method.name not in {"_poll_fast", "_poll_medium", "_poll_slow"}:
                continue
            for node in ast.walk(method):
                assignment_targets: list[ast.AST] = []
                if isinstance(node, ast.Assign):
                    assignment_targets.extend(node.targets)
                elif isinstance(node, ast.AnnAssign):
                    assignment_targets.append(node.target)
                elif isinstance(node, ast.AugAssign):
                    assignment_targets.append(node.target)
                for target in assignment_targets:
                    name = _state_write_target(target)
                    if name is not None:
                        targets.add(name)
    return targets


# ---------------------------------------------------------------------------
# Start / stop
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_start_creates_tasks() -> None:
    radio = make_radio()
    calls: list[RadioState] = []
    poller = YaesuCatPoller(radio, callback=calls.append, fast_interval=0.01)

    await poller.start()
    assert poller.running
    assert len(poller._tasks) == 3

    await poller.stop()
    assert not poller.running
    assert poller._tasks == []


@pytest.mark.asyncio
async def test_start_is_idempotent() -> None:
    radio = make_radio()
    poller = YaesuCatPoller(radio, callback=lambda s: None, fast_interval=0.01)

    await poller.start()
    tasks_first = list(poller._tasks)
    await poller.start()  # second call — no-op
    assert poller._tasks is tasks_first or poller._tasks == tasks_first

    await poller.stop()


@pytest.mark.asyncio
async def test_stop_cancels_tasks() -> None:
    radio = make_radio()
    poller = YaesuCatPoller(radio, callback=lambda s: None, fast_interval=10.0)

    await poller.start()
    await poller.stop()

    assert not poller.running


# ---------------------------------------------------------------------------
# Callback invocation
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_fast_poll_invokes_callback() -> None:
    radio = make_radio(s_meter_main=120)
    calls: list[RadioState] = []

    poller = YaesuCatPoller(
        radio,
        callback=calls.append,
        fast_interval=0.01,
        medium_interval=10.0,
        slow_interval=10.0,
        ema_alpha=1.0,  # no smoothing so raw == smoothed
    )
    await poller.start()
    await asyncio.sleep(0.05)
    await poller.stop()

    assert len(calls) >= 1
    # Callback receives the RadioState object
    assert isinstance(calls[0], RadioState)


@pytest.mark.asyncio
async def test_medium_poll_invokes_callback() -> None:
    radio = make_radio()
    calls: list[RadioState] = []

    poller = YaesuCatPoller(
        radio,
        callback=calls.append,
        fast_interval=10.0,
        medium_interval=0.01,
        slow_interval=10.0,
    )
    await poller.start()
    await asyncio.sleep(0.05)
    await poller.stop()

    assert len(calls) >= 1


@pytest.mark.asyncio
async def test_slow_poll_invokes_callback() -> None:
    radio = make_radio()
    calls: list[RadioState] = []

    poller = YaesuCatPoller(
        radio,
        callback=calls.append,
        fast_interval=10.0,
        medium_interval=10.0,
        slow_interval=0.01,
    )
    await poller.start()
    await asyncio.sleep(0.05)
    await poller.stop()

    assert len(calls) >= 1


# ---------------------------------------------------------------------------
# State updates
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_fast_poll_updates_s_meter() -> None:
    radio = make_radio(s_meter_main=150, s_meter_sub=75)

    poller = YaesuCatPoller(
        radio,
        callback=lambda s: None,
        fast_interval=0.01,
        medium_interval=10.0,
        slow_interval=10.0,
        ema_alpha=1.0,  # raw pass-through
    )
    await poller.start()
    await asyncio.sleep(0.03)
    await poller.stop()

    assert radio.radio_state.main.s_meter == 150
    assert radio.radio_state.sub.s_meter == 75


@pytest.mark.asyncio
async def test_medium_poll_updates_freq_mode_ptt() -> None:
    radio = make_radio(freq_main=14_074_000, ptt=True)

    poller = YaesuCatPoller(
        radio,
        callback=lambda s: None,
        fast_interval=10.0,
        medium_interval=0.01,
        slow_interval=10.0,
    )
    await poller.start()
    await asyncio.sleep(0.03)
    await poller.stop()

    radio.get_freq.assert_called()
    radio.get_mode.assert_called()
    radio.get_ptt.assert_called()


@pytest.mark.asyncio
async def test_medium_poll_emits_observations_without_legacy_state_callback() -> None:
    radio = make_radio(freq_main=14_074_000, ptt=True)
    radio.profile.state_acquisition = _profile_state_acquisition()
    legacy_calls: list[RadioState] = []
    observations: list[Observation] = []

    poller = YaesuCatPoller(
        radio,
        callback=legacy_calls.append,
        observation_callback=observations.extend,
        fast_interval=10.0,
        medium_interval=10.0,
        slow_interval=10.0,
    )

    await poller._poll_medium()  # noqa: SLF001

    assert legacy_calls == []
    # filter_width shares the freq/mode lane (MOR-445); ``make_radio`` declares
    # the ``filter_width`` cap, so it emits after PTT, MAIN-only.
    assert [(str(item.path), item.value) for item in observations] == [
        ("global.tx_state.observed_ptt", ObservedPtt.ON),
        ("receiver.main.active.freq_mode.freq_hz", 14_074_000),
        ("receiver.main.active.freq_mode.mode", "USB"),
        ("receiver.sub.active.freq_mode.freq_hz", 7_074_000),
        ("receiver.sub.active.freq_mode.mode", "LSB"),
        *_declared_tx_target(14_074_000),
        ("global.tx_state.ptt", True),
        ("receiver.main.active.freq_mode.filter_width", 2400),
    ]
    assert {item.source.source for item in observations} == {"yaesu_poll_response"}


@pytest.mark.parametrize("operation", ["poll", "reconnect"])
@pytest.mark.parametrize("callback_error", [RuntimeError, asyncio.CancelledError])
async def test_invalidation_callback_failure_preserves_transport_flow(
    monkeypatch: pytest.MonkeyPatch,
    operation: str,
    callback_error: type[BaseException],
) -> None:
    radio = _tx_target_radio()
    callback = MagicMock(side_effect=callback_error("consumer failed"))
    poller = YaesuCatPoller(radio, observation_callback=callback)
    if operation == "poll":
        monkeypatch.setattr(
            YaesuObservationAdapter,
            "poll_medium",
            AsyncMock(side_effect=[CatTimeoutError("cat reset")] * 2),
        )
        for _ in range(2):
            with pytest.raises(CatTimeoutError, match="cat reset"):
                await poller._poll_medium()  # noqa: SLF001
        assert callback.call_count == 1
    else:
        radio._transport._maybe_reconnect_needed = lambda: True
        await poller._try_reconnect()  # noqa: SLF001
        radio._transport.reconnect.assert_awaited_once()
    assert [args.args[0][0].path for args in callback.call_args_list] == (
        [FieldPath.global_("tx_state", "tx_target")]
        + ([OBSERVED_PTT_PATH] if operation == "reconnect" else [])
    )


@pytest.mark.parametrize("boundary", ["reconnect", "provider"])
async def test_tx_target_known_state_is_generation_scoped(
    monkeypatch: pytest.MonkeyPatch, boundary: str
) -> None:
    radio = _tx_target_radio()
    known = KnownTxTarget(receiver="MAIN", slot=None, frequency_hz=14_074_000)
    emitted: list[Observation] = []
    monkeypatch.setattr(
        YaesuObservationAdapter,
        "poll_medium",
        AsyncMock(side_effect=[(_target_observation(radio, known),), (), ()]),
    )
    poller = YaesuCatPoller(radio, observation_callback=emitted.extend)

    await poller._poll_medium()  # noqa: SLF001
    for generation in (1, 2):
        if boundary == "reconnect":
            radio._transport.stats.reconnects = generation
        else:
            radio.profile.state_acquisition = replace(
                radio.profile.state_acquisition, provider=f"yaesu_cat_{generation}"
            )
        await poller._poll_medium()  # noqa: SLF001

    assert [item.value for item in emitted] == [
        known,
        UnknownTxTarget(reason="stale"),
        ObservedPtt.UNKNOWN,
        UnknownTxTarget(reason="not-observed"),
        ObservedPtt.UNKNOWN,
    ]
    unsupported = UnknownTxTarget(reason="unsupported")
    monkeypatch.setattr(
        YaesuObservationAdapter,
        "poll_medium",
        AsyncMock(return_value=(_target_observation(radio, unsupported),)),
    )
    await poller._poll_medium()  # noqa: SLF001
    assert emitted[-1].value == unsupported
    radio._transport.reconnect.assert_not_awaited()

    async def late(
        _adapter: YaesuObservationAdapter,
        *,
        ptt_callback: Callable[[Observation], None] | None = None,
    ) -> tuple[Observation, ...]:
        radio._transport.stats.reconnects += 1
        return (_target_observation(radio, known),)

    monkeypatch.setattr(YaesuObservationAdapter, "poll_medium", late)
    await poller._poll_medium()  # noqa: SLF001
    assert emitted[-1].value == UnknownTxTarget(reason="not-observed")


@pytest.mark.asyncio
async def test_fast_poll_emits_rx_meter_observations_without_legacy_state_callback() -> (
    None
):
    radio = make_radio(s_meter_main=150, s_meter_sub=75, ptt=False)
    radio.profile.state_acquisition = _profile_state_acquisition()
    legacy_calls: list[RadioState] = []
    observations: list[Observation] = []

    poller = YaesuCatPoller(
        radio,
        callback=legacy_calls.append,
        observation_callback=observations.extend,
        fast_interval=10.0,
        medium_interval=10.0,
        slow_interval=10.0,
        ema_alpha=1.0,
    )

    await poller._poll_fast()  # noqa: SLF001

    assert legacy_calls == []
    assert [(str(item.path), item.value) for item in observations] == [
        ("receiver.main.meters.s_meter", 150),
        ("receiver.sub.meters.s_meter", 75),
    ]
    assert radio.radio_state.main.s_meter == 0
    assert radio.radio_state.sub.s_meter == 0


@pytest.mark.asyncio
async def test_observation_poller_uses_read_only_paths_when_getters_mutate_state() -> (
    None
):
    radio = _SideEffectingYaesuRadio()
    legacy_calls: list[RadioState] = []
    observations: list[Observation] = []

    poller = YaesuCatPoller(
        radio,
        callback=legacy_calls.append,
        observation_callback=observations.extend,
        fast_interval=10.0,
        medium_interval=10.0,
        slow_interval=10.0,
        ema_alpha=1.0,
    )

    await poller._poll_medium()  # noqa: SLF001
    await poller._poll_fast()  # noqa: SLF001
    await poller._poll_slow()  # noqa: SLF001

    assert legacy_calls == []
    assert [(str(item.path), item.value) for item in observations] == [
        ("global.tx_state.observed_ptt", ObservedPtt.OFF),
        ("receiver.main.active.freq_mode.freq_hz", 14_074_000),
        ("receiver.main.active.freq_mode.mode", "USB"),
        ("receiver.sub.active.freq_mode.freq_hz", 7_074_000),
        ("receiver.sub.active.freq_mode.mode", "LSB"),
        *_declared_tx_target(14_074_000),
        ("global.tx_state.ptt", False),
        ("receiver.main.meters.s_meter", 6),
        ("receiver.sub.meters.s_meter", -37),
        (
            "receiver.main.operator_controls.af_level",
            pytest.approx(_normalized_255(128)),
        ),
        (
            "receiver.main.operator_controls.rf_gain",
            pytest.approx(_normalized_255(180)),
        ),
        (
            "receiver.main.operator_controls.squelch",
            pytest.approx(_normalized_255(12)),
        ),
        (
            "receiver.sub.operator_controls.af_level",
            pytest.approx(_normalized_255(64)),
        ),
        (
            "receiver.sub.operator_controls.rf_gain",
            pytest.approx(_normalized_255(90)),
        ),
        (
            "receiver.sub.operator_controls.squelch",
            pytest.approx(_normalized_255(8)),
        ),
        # ATT/preamp need their runtime caps (absent here); AGC is
        # unconditional and MAIN-only, mirroring the legacy poller.
        ("receiver.main.operator_controls.agc", 3),
        # filter_width/if_shift need their runtime caps (absent here); narrow
        # is unconditional and MAIN-only, like AGC (MOR-445).
        ("receiver.main.operator_toggles.narrow", True),
        # active-slot (MOR-446) closes the slow-control lane; unconditional like
        # AGC/narrow, the SUB index coerces to the neutral "SUB" str. split is
        # skipped: this radio lacks the ``split`` runtime cap.
        ("global.slow_state.active", "SUB"),
        (
            "global.operator_controls.power_level",
            pytest.approx(_normalized_power(55)),
        ),
        ("global.operator_controls.mic_gain", 40),
        ("global.tx_state.compressor_on", True),
        ("global.operator_controls.compressor_level", 25),
        ("global.tx_state.vox_on", True),
    ]
    assert radio.legacy_getter_calls == 0
    assert radio.radio_state.main.freq == 1
    assert radio.radio_state.main.mode == "INIT-MAIN"
    assert radio.radio_state.sub.freq == 2
    assert radio.radio_state.sub.mode == "INIT-SUB"
    assert radio.radio_state.ptt is False
    assert radio.radio_state.main.s_meter == 3
    assert radio.radio_state.sub.s_meter == 4
    assert radio.radio_state.main.af_level == 5
    assert radio.radio_state.main.rf_gain == 6
    assert radio.radio_state.main.squelch == 7
    assert radio.radio_state.sub.af_level == 8
    assert radio.radio_state.sub.rf_gain == 9
    assert radio.radio_state.sub.squelch == 10
    assert radio.radio_state.power_level == 11
    assert radio.radio_state.mic_gain == 12
    assert radio.radio_state.compressor_on is False
    assert radio.radio_state.compressor_level == 13
    assert radio.radio_state.vox_on is False
    assert radio.radio_state.main.att == 14
    assert radio.radio_state.main.preamp == 15
    assert radio.radio_state.main.agc == 16
    # Filter / IF-shift / narrow read_* paths must not mutate legacy state
    # (MOR-445), including read_filter_width which reads but never writes mode.
    assert radio.radio_state.main.filter_width == 17
    assert radio.radio_state.main.if_shift == 18
    assert radio.radio_state.main.narrow is False
    # Split + active-slot read_* paths must not mutate legacy state (MOR-446).
    assert radio.radio_state.split is False
    assert radio.radio_state.active == "MAIN"
    assert radio.radio_state.vfo_select == 0


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("capabilities", "expected"),
    [
        ({"rit"}, (True, True, -250)),
        ({"xit"}, (False, False, -250)),
        ({"rit", "xit"}, (True, False, -250)),
        (set(), (False, True, 123)),
    ],
)
async def test_legacy_slow_poll_scopes_clarifier_state_to_declared_capabilities(
    capabilities: set[str], expected: tuple[bool, bool, int]
) -> None:
    radio = make_radio(clarifier=(True, False), clarifier_freq=-250)
    radio.capabilities = capabilities
    radio.radio_state.rit_on = False
    radio.radio_state.rit_tx = True
    radio.radio_state.rit_freq = 123
    poller = YaesuCatPoller(radio)

    await poller._poll_slow()  # noqa: SLF001

    assert (
        radio.radio_state.rit_on,
        radio.radio_state.rit_tx,
        radio.radio_state.rit_freq,
    ) == expected
    if capabilities:
        radio.get_clarifier.assert_awaited_once()
        radio.get_clarifier_freq.assert_awaited_once()
    else:
        radio.get_clarifier.assert_not_awaited()
        radio.get_clarifier_freq.assert_not_awaited()


@pytest.mark.asyncio
async def test_fast_poll_emits_profiled_tx_meter_observations_only() -> None:
    radio = make_radio(ptt=True)
    radio.profile.state_acquisition = _profile_state_acquisition()
    radio.read_alc_meter = AsyncMock(return_value=42)
    radio.read_power_meter = AsyncMock(return_value=180)
    radio.read_swr_meter = AsyncMock(return_value=120)
    radio.read_comp_meter = AsyncMock(return_value=30)
    radio.get_alc_meter = AsyncMock(return_value=42)
    radio.get_power_meter = AsyncMock(return_value=180)
    radio.get_comp_meter = AsyncMock(return_value=30)
    radio.get_swr_meter = AsyncMock(return_value=120)
    observations: list[Observation] = []

    poller = YaesuCatPoller(
        radio,
        observation_callback=observations.extend,
        fast_interval=10.0,
        medium_interval=10.0,
        slow_interval=10.0,
    )

    await poller._poll_medium()  # noqa: SLF001
    observations.clear()
    await poller._poll_fast()  # noqa: SLF001

    # ALC and COMP are now observation-backed stream-like meters
    # (MOR-448/460); both emit via the non-mutating read_* path.
    assert [(str(item.path), item.value) for item in observations] == [
        ("global.meters.alc", 42),
        ("global.meters.power", 180),
        ("global.meters.swr", 120),
        ("global.meters.comp", 30),
    ]
    radio.get_alc_meter.assert_not_awaited()
    radio.get_power_meter.assert_not_awaited()
    radio.get_comp_meter.assert_not_awaited()
    radio.get_swr_meter.assert_not_awaited()
    radio.read_comp_meter.assert_awaited_once()
    assert radio.radio_state.alc_meter == 0
    assert radio.radio_state.comp_meter == 0
    assert radio.radio_state.power_meter == 0
    assert radio.radio_state.swr_meter == 0


def test_legacy_yaesu_state_writes_are_observed_or_explicit_limitations() -> None:
    decisions = {
        "main.s_meter": "observation:receiver.main.meters.s_meter",
        "sub.s_meter": "observation:receiver.sub.meters.s_meter",
        "power_meter": "observation:global.meters.power",
        "swr_meter": "observation:global.meters.swr",
        "main.af_level": "observation:receiver.main.operator_controls.af_level",
        "main.rf_gain": "observation:receiver.main.operator_controls.rf_gain",
        "main.squelch": "observation:receiver.main.operator_controls.squelch",
        "sub.af_level": "observation:receiver.sub.operator_controls.af_level",
        "sub.rf_gain": "observation:receiver.sub.operator_controls.rf_gain",
        "sub.squelch": "observation:receiver.sub.operator_controls.squelch",
        "alc_meter": "observation:global.meters.alc",
        "comp_meter": "observation:global.meters.comp",
        "main.filter_width": "observation:receiver.main.active.freq_mode.filter_width",
        "main.agc": "observation:receiver.main.operator_controls.agc",
        "main.nb_level": "observation:receiver.main.operator_controls.nb_level",
        "main.nb": "observation:receiver.main.operator_toggles.nb",
        "main.nr_level": "observation:receiver.main.operator_controls.nr_level",
        "main.nr": "observation:receiver.main.operator_toggles.nr",
        "main.auto_notch": "observation:receiver.main.operator_toggles.auto_notch",
        "power_level": "observation:global.operator_controls.power_level",
        "mic_gain": "observation:global.operator_controls.mic_gain",
        "split": "observation:global.tx_state.split",
        "vox_on": "observation:global.tx_state.vox_on",
        "dial_lock": "observation:global.tx_state.dial_lock",
        "compressor_on": "observation:global.tx_state.compressor_on",
        "compressor_level": "observation:global.operator_controls.compressor_level",
        "main.att": "observation:receiver.main.operator_controls.att",
        "main.preamp": "observation:receiver.main.operator_controls.preamp",
        "tuner_status": "observation:global.operator_controls.tuner_status",
        "main.contour": "limitation: Yaesu Contour (S-DX) is vendor-specific tone-shaping with no Icom/neutral equivalent; kept vendor-namespaced/compat-only per docs/architecture/field-path-promotion-criterion.md",
        "main.if_shift": "observation:receiver.main.operator_controls.if_shift",
        "rit_on": "observation:global.tx_state.rit_on",
        "rit_tx": "observation:global.tx_state.rit_tx",
        "rit_freq": "observation:global.operator_controls.rit_freq",
        "main.manual_notch": "observation:receiver.main.operator_toggles.manual_notch",
        "main.manual_notch_freq": "observation:receiver.main.operator_controls.manual_notch_freq",
        "main.narrow": "observation:receiver.main.operator_toggles.narrow",
        "key_speed": "observation:global.operator_controls.key_speed",
        "cw_pitch": "observation:global.operator_controls.cw_pitch",
        "break_in": "observation:global.operator_controls.break_in",
        "break_in_delay": "observation:global.operator_controls.break_in_delay",
        "cw_spot": "observation:global.slow_state.cw_spot",
        "yaesu": "limitation: Yaesu extension namespace is backend-specific compatibility state",
        "yaesu.rx_func_mode": "limitation: Yaesu FR mode is backend-specific compatibility state",
        "yaesu.tx_func_mode": "limitation: Yaesu FT mode is backend-specific compatibility state",
        "vfo_select": "observation:global.slow_state.active",
    }

    assert _yaesu_poller_state_write_targets() == set(decisions)

    profile = _profile_state_acquisition()
    canonical_paths = [
        FieldPath.parse(decision.removeprefix("observation:"))
        for decision in decisions.values()
        if decision.startswith("observation:")
    ]
    assert all(profile.capability_for(path).can_poll for path in canonical_paths)


@pytest.mark.asyncio
async def test_slow_poll_updates_agc_and_levels() -> None:
    radio = make_radio(agc=3, af_level=200, rf_gain=180, squelch=20)

    poller = YaesuCatPoller(
        radio,
        callback=lambda s: None,
        fast_interval=10.0,
        medium_interval=10.0,
        slow_interval=0.01,
    )
    await poller.start()
    await asyncio.sleep(0.03)
    await poller.stop()

    assert radio.radio_state.main.agc == 3
    assert radio.radio_state.main.af_level == 200
    assert radio.radio_state.main.rf_gain == 180
    assert radio.radio_state.main.squelch == 20


# ---------------------------------------------------------------------------
# EMA smoothing
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_ema_smoothing_applied() -> None:
    """With alpha=0.5 two identical samples should converge to the value."""
    radio = make_radio(s_meter_main=100)
    states: list[RadioState] = []

    poller = YaesuCatPoller(
        radio,
        callback=states.append,
        fast_interval=0.005,
        medium_interval=10.0,
        slow_interval=10.0,
        ema_alpha=0.5,
    )
    await poller.start()
    await asyncio.sleep(0.05)
    await poller.stop()

    # After several samples of 100, EMA should converge to 100.
    assert states, "No callbacks received"
    final = states[-1].main.s_meter
    assert 90 <= final <= 110, f"EMA didn't converge: {final}"


@pytest.mark.asyncio
async def test_ema_zero_alpha_no_smoothing() -> None:
    """alpha=0 means EMA always returns the first sample."""
    radio = make_radio(s_meter_main=77)
    states: list[RadioState] = []

    poller = YaesuCatPoller(
        radio,
        callback=states.append,
        fast_interval=0.005,
        medium_interval=10.0,
        slow_interval=10.0,
        ema_alpha=0,
    )
    await poller.start()
    await asyncio.sleep(0.03)
    await poller.stop()

    # alpha=0: formula returns float(raw) on first call, then 0*raw + 1*prev = prev
    # but first call always returns float(raw) = 77
    assert states[0].main.s_meter == 77


# ---------------------------------------------------------------------------
# Pause / resume
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_pause_stops_callbacks() -> None:
    radio = make_radio()
    calls: list[RadioState] = []

    poller = YaesuCatPoller(
        radio,
        callback=calls.append,
        fast_interval=0.01,
        medium_interval=10.0,
        slow_interval=10.0,
    )
    await poller.start()
    await asyncio.sleep(0.03)

    before = len(calls)
    await poller.pause()
    await asyncio.sleep(0.05)
    after = len(calls)

    # At most one in-flight request completes after pause().
    assert after - before <= 1

    await poller.stop()


@pytest.mark.asyncio
async def test_resume_restarts_callbacks() -> None:
    radio = make_radio()
    calls: list[RadioState] = []

    poller = YaesuCatPoller(
        radio,
        callback=calls.append,
        fast_interval=0.01,
        medium_interval=10.0,
        slow_interval=10.0,
    )
    await poller.start()
    await poller.pause()
    await asyncio.sleep(0.03)

    before = len(calls)
    await poller.resume()
    await asyncio.sleep(0.05)
    after = len(calls)

    assert after > before

    await poller.stop()


# ---------------------------------------------------------------------------
# Error handling
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_fast_poll_continues_after_error() -> None:
    """A transient get_s_meter error must not crash the poller."""
    call_count = 0

    async def flaky_s_meter(receiver: int = 0) -> int:
        nonlocal call_count
        call_count += 1
        if call_count <= 2:
            raise RuntimeError("timeout")
        return 100

    radio = make_radio()
    radio.get_s_meter = AsyncMock(side_effect=flaky_s_meter)

    calls: list[RadioState] = []
    poller = YaesuCatPoller(
        radio,
        callback=calls.append,
        fast_interval=0.01,
        medium_interval=10.0,
        slow_interval=10.0,
        ema_alpha=1.0,
    )
    await poller.start()
    await asyncio.sleep(0.08)
    await poller.stop()

    # Should have recovered and fired callbacks after early errors.
    assert len(calls) >= 1


@pytest.mark.asyncio
async def test_sub_receiver_unavailable_does_not_crash() -> None:
    """If sub S-meter raises, main polling must still work."""
    radio = make_radio()

    async def s_meter_side_effect(receiver: int = 0) -> int:
        if receiver == 1:
            raise RuntimeError("sub not supported")
        return 80

    radio.get_s_meter = AsyncMock(side_effect=s_meter_side_effect)

    calls: list[RadioState] = []
    poller = YaesuCatPoller(
        radio,
        callback=calls.append,
        fast_interval=0.01,
        medium_interval=10.0,
        slow_interval=10.0,
        ema_alpha=1.0,
    )
    await poller.start()
    await asyncio.sleep(0.05)
    await poller.stop()

    assert len(calls) >= 1
    assert calls[-1].main.s_meter == 80


@pytest.mark.asyncio
async def test_slow_poll_continues_after_partial_error() -> None:
    """Even if get_agc raises, the remaining slow-poll commands run."""
    radio = make_radio(af_level=99)
    radio.get_agc = AsyncMock(side_effect=RuntimeError("agc error"))

    calls: list[RadioState] = []
    poller = YaesuCatPoller(
        radio,
        callback=calls.append,
        fast_interval=10.0,
        medium_interval=10.0,
        slow_interval=0.01,
    )
    await poller.start()
    await asyncio.sleep(0.05)
    await poller.stop()

    # get_af_level should still have run.
    radio.get_af_level.assert_called()
    assert calls[-1].main.af_level == 99


# ---------------------------------------------------------------------------
# Polling rates (rough verification)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_fast_polls_more_than_slow() -> None:
    """Fast loop should fire at least 3× more often than slow."""
    radio = make_radio()
    fast_count = 0
    slow_count = 0

    _original_fast = radio.get_s_meter

    async def count_fast(receiver: int = 0) -> int:
        nonlocal fast_count
        if receiver == 0:
            fast_count += 1
        return 0

    async def count_slow(receiver: int = 0) -> int:
        nonlocal slow_count
        slow_count += 1
        return 0

    radio.get_s_meter = AsyncMock(side_effect=count_fast)
    radio.get_agc = AsyncMock(side_effect=count_slow)

    poller = YaesuCatPoller(
        radio,
        callback=lambda s: None,
        fast_interval=0.02,
        medium_interval=10.0,
        slow_interval=0.1,
    )
    await poller.start()
    await asyncio.sleep(0.25)
    await poller.stop()

    assert fast_count > 0
    assert slow_count > 0
    assert fast_count >= slow_count * 3, (
        f"fast={fast_count} should be >= 3×slow={slow_count}"
    )


# ---------------------------------------------------------------------------
# TX meter polling (#559)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_fast_poll_reads_tx_meters_when_ptt_active() -> None:
    """When PTT is on, fast poll should read ALC, Power, COMP, SWR meters."""
    radio = make_radio(ptt=True)
    radio.radio_state.ptt = True
    radio.get_alc_meter = AsyncMock(return_value=42)
    radio.get_power_meter = AsyncMock(return_value=180)
    radio.get_comp_meter = AsyncMock(return_value=30)
    radio.get_swr_meter = AsyncMock(return_value=120)

    poller = YaesuCatPoller(
        radio,
        callback=lambda s: None,
        fast_interval=0.01,
        medium_interval=10.0,
        slow_interval=10.0,
        ema_alpha=1.0,
    )
    await poller.start()
    await asyncio.sleep(0.05)
    await poller.stop()

    radio.get_alc_meter.assert_called()
    radio.get_power_meter.assert_called()
    radio.get_comp_meter.assert_called()
    radio.get_swr_meter.assert_called()
    assert radio.radio_state.alc_meter == 42
    assert radio.radio_state.power_meter == 180
    assert radio.radio_state.comp_meter == 30
    assert radio.radio_state.swr_meter == 120


# ---------------------------------------------------------------------------
# Command queue: future exception propagation
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_ftx1_web_receiver_selection_writes_once_and_waits_for_vs_readback() -> (
    None
):
    """Web MAIN/SUB uses the real Yaesu queue and confirmed VS observation path."""
    radio, handler, poller, store, accept = _real_ftx1_control_path()
    service = handler._command_service  # noqa: SLF001
    active_path = FieldPath.global_("slow_state", "active")

    await handler._enqueue_command(  # noqa: SLF001
        "select_vfo",
        {"vfo": "SUB"},
        command_id="select-sub",
    )
    assert (
        service.pending_overlays(source="websocket", session_id="ws-ftx1")[0].value
        == "SUB"
    )

    await poller._drain_commands()  # noqa: SLF001

    radio._transport.write.assert_awaited_once_with("VS1;")  # noqa: SLF001
    assert radio.radio_state.active == "MAIN"
    with pytest.raises(KeyError):
        store.snapshot().field(active_path)
    assert service.pending_overlays(source="websocket", session_id="ws-ftx1")

    radio._transport.query = AsyncMock(side_effect=("VS0", "VS1"))  # noqa: SLF001
    profile = radio.profile.state_acquisition
    assert profile is not None

    def observe_active(value: str) -> None:
        accept(
            poller._annotate_receiver_select_readback(  # noqa: SLF001
                (
                    replace(
                        ProviderObservationAdapter(
                            profile,
                            source="yaesu_poll_response",
                            transport="serial",
                        ).observation(
                            active_path,
                            value,
                            native_id="read_vfo_select",
                        ),
                        provider_generation=store.provider_generation,
                    ),
                )
            )
        )

    assert await radio.read_vfo_select() == 0
    observe_active("MAIN")
    assert store.snapshot().field(active_path).value == "MAIN"
    assert service.pending_overlays(source="websocket", session_id="ws-ftx1")
    assert radio.radio_state.active == "MAIN"

    assert await radio.read_vfo_select() == 1
    observe_active("SUB")
    assert radio._transport.query.await_args_list == [  # noqa: SLF001
        mock_call("VS;"),
        mock_call("VS;"),
    ]
    assert radio.radio_state.active == "MAIN"
    assert store.snapshot().field(active_path).value == "SUB"
    assert service.pending_overlays(source="websocket", session_id="ws-ftx1") == ()

    await handler._enqueue_command(  # noqa: SLF001
        "select_vfo",
        {"vfo": "MAIN"},
        command_id="select-main",
    )
    await poller._drain_commands()  # noqa: SLF001
    await poller._drain_commands()  # a second drain must not duplicate the write

    assert radio._transport.write.await_args_list == [  # noqa: SLF001
        mock_call("VS1;"),
        mock_call("VS0;"),
    ]
    assert store.snapshot().field(active_path).value == "SUB"
    observed_path = FieldPath.active("sub", "freq_mode", "freq_hz")
    for command_id, freq in (("old", 7_100_000), ("latest", 7_200_000)):
        await handler._enqueue_command(  # noqa: SLF001
            "set_freq", {"freq": freq, "receiver": 1}, command_id=command_id
        )
        dispatched_at = asyncio.get_running_loop().time()
        await poller._drain_commands()  # noqa: SLF001
    [latest] = service.readback_expectations(
        source="websocket", session_id="ws-ftx1", command_id="latest"
    )
    assert 1.9 < latest.expires_at_monotonic - dispatched_at <= 2.01
    observed = ProviderObservationAdapter(
        radio.profile.state_acquisition,
        source="yaesu_poll_response",
        transport="serial",
    ).observation(observed_path, 7_100_000)
    observed = replace(observed, provider_generation=store.provider_generation)
    assert poller._annotate_yaesu_readbacks((observed,))[0].correlation_id is None  # noqa: SLF001
    truth_trap = MagicMock(**{"__bool__.side_effect": RuntimeError("truthiness trap")})
    untrusted = MagicMock()
    untrusted.__eq__.side_effect = (RuntimeError("equality trap"), object(), truth_trap)
    for value in [untrusted] * 3 + [float("nan"), float("inf"), float("-inf")]:
        candidate = replace(observed, value=value)
        guarded = poller._annotate_yaesu_readbacks((candidate,))[0]  # noqa: SLF001
        accept((guarded,))
    matched = poller._annotate_yaesu_readbacks(  # noqa: SLF001
        (replace(observed, value=7_200_000),)
    )[0]
    assert matched.correlation_id == "latest"
    assert matched.source == replace(
        observed.source, command_source="websocket", session_id="ws-ftx1"
    )
    accept((matched,))
    assert service.lifecycle_events()[-1].state == "reconciled"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("failure", "expected_state"),
    [
        (CatTimeoutError("receiver dispatch timed out"), "timed_out"),
        (RuntimeError("receiver dispatch failed"), "failed"),
    ],
)
async def test_ftx1_receiver_dispatch_failure_is_terminal(
    failure: Exception,
    expected_state: str,
) -> None:
    """Post-ack Yaesu dispatch failures clear pending and report terminal truth."""
    radio, handler, poller, _store, _accept = _real_ftx1_control_path()
    service = handler._command_service  # noqa: SLF001
    radio._transport.write = AsyncMock(side_effect=failure)  # noqa: SLF001

    await handler._enqueue_command(  # noqa: SLF001
        "select_vfo",
        {"vfo": "SUB"},
        command_id="failed-select",
    )
    assert service.pending_overlays(source="websocket", session_id="ws-ftx1")

    await poller._drain_commands()  # noqa: SLF001

    assert service.pending_overlays(source="websocket", session_id="ws-ftx1") == ()
    terminal = [
        event
        for event in service.lifecycle_events()
        if event.command_id == "failed-select"
        and event.state in {"failed", "timed_out"}
    ]
    assert [event.state for event in terminal] == [expected_state]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("receiver_count", "vfo"),
    [(2, "SIDE"), (1, "SUB")],
)
async def test_yaesu_receiver_selection_rejects_unsupported_target_or_topology(
    receiver_count: int,
    vfo: str,
) -> None:
    """Invalid names and SUB on single-RX profiles fail without CAT writes."""
    radio, handler, poller, _store, _accept = _real_ftx1_control_path(
        receiver_count=receiver_count
    )
    service = handler._command_service  # noqa: SLF001

    await handler._enqueue_command(  # noqa: SLF001
        "select_vfo",
        {"vfo": vfo},
        command_id="unsupported-select",
    )
    await poller._drain_commands()  # noqa: SLF001

    radio._transport.write.assert_not_awaited()  # noqa: SLF001
    terminal = [
        event
        for event in service.lifecycle_events()
        if event.command_id == "unsupported-select" and event.state == "failed"
    ]
    assert len(terminal) == 1


@pytest.mark.asyncio
async def test_drain_commands_sets_future_exception_on_execution_failure() -> None:
    """_drain_commands must set_exception on the future when _execute_command raises."""
    radio = make_radio()
    boom = RuntimeError("rig error")
    radio.set_freq = AsyncMock(side_effect=boom)

    queue = CommandQueue()
    poller = YaesuCatPoller(radio, callback=lambda s: None, command_queue=queue)
    _set_fresh_ptt_observation(poller, active=False)

    loop = asyncio.get_running_loop()
    future: asyncio.Future[None] = loop.create_future()
    queue.put_ordered(SetFreq(144_030_000, receiver=0), future=future)

    await poller._drain_commands()

    assert future.done()
    assert not future.cancelled()
    assert future.exception() is boom


@pytest.mark.asyncio
@pytest.mark.parametrize("profile_name", ["FTX-1", "TX-500", "X6100", "X6200"])
@pytest.mark.parametrize(
    ("command", "profile_primitive", "radio_method"),
    [
        (VfoSwap, "swap_ab_code", "swap_vfo_ab"),
        (VfoEqualize, "equal_ab_code", "equalize_vfo_ab"),
    ],
)
async def test_undeclared_vfo_commands_reject_before_radio_mutation(
    profile_name: str,
    command: type[VfoSwap] | type[VfoEqualize],
    profile_primitive: str,
    radio_method: str,
) -> None:
    """Undeclared direct Yaesu VFO actions must fail closed before mutation."""
    radio = make_radio()
    radio.profile = get_radio_profile(profile_name)
    radio.vfo_a_to_b = AsyncMock()
    radio.swap_vfo_ab = AsyncMock()
    radio.equalize_vfo_ab = AsyncMock()
    poller = YaesuCatPoller(radio, callback=lambda s: None)

    assert getattr(radio.profile, profile_primitive) is None
    with pytest.raises(
        NotImplementedError,
        match=rf"{command.__name__} unsupported on {profile_name}: .*{profile_primitive}",
    ):
        await poller._execute_command(command())  # noqa: SLF001

    radio.vfo_a_to_b.assert_not_awaited()
    getattr(radio, radio_method).assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("profile_name", ["FTX-1", "TX-500", "X6100", "X6200"])
@pytest.mark.parametrize(
    ("command", "profile_primitive", "radio_method"),
    [
        (VfoSwap, "swap_ab_code", "swap_vfo_ab"),
        (VfoEqualize, "equal_ab_code", "equalize_vfo_ab"),
    ],
)
async def test_undeclared_queued_vfo_commands_set_future_exception_before_mutation(
    profile_name: str,
    command: type[VfoSwap] | type[VfoEqualize],
    profile_primitive: str,
    radio_method: str,
) -> None:
    """Queued undeclared Yaesu VFO actions must never acknowledge success."""
    radio = make_radio()
    radio.profile = get_radio_profile(profile_name)
    radio.vfo_a_to_b = AsyncMock()
    radio.swap_vfo_ab = AsyncMock()
    radio.equalize_vfo_ab = AsyncMock()
    queue = CommandQueue()
    poller = YaesuCatPoller(radio, callback=lambda s: None, command_queue=queue)
    _set_fresh_ptt_observation(poller, active=False)

    assert getattr(radio.profile, profile_primitive) is None
    future: asyncio.Future[None] = asyncio.get_running_loop().create_future()
    queue.put_ordered(command(), future=future)

    await poller._drain_commands()  # noqa: SLF001

    error = future.exception()
    assert isinstance(error, NotImplementedError)
    assert f"{command.__name__} unsupported on {profile_name}" in str(
        error
    ) and profile_primitive in str(error)
    radio.vfo_a_to_b.assert_not_awaited()
    getattr(radio, radio_method).assert_not_awaited()


@pytest.mark.asyncio
async def test_fast_poll_skips_tx_meters_when_ptt_off() -> None:
    """When PTT is off, fast poll should NOT read TX meters."""
    radio = make_radio(ptt=False)
    radio.radio_state.ptt = False

    poller = YaesuCatPoller(
        radio,
        callback=lambda s: None,
        fast_interval=0.01,
        medium_interval=10.0,
        slow_interval=10.0,
        ema_alpha=1.0,
    )
    await poller.start()
    await asyncio.sleep(0.05)
    await poller.stop()

    radio.get_alc_meter.assert_not_called()
    radio.get_power_meter.assert_not_called()


@pytest.mark.asyncio
async def test_tx_meter_partial_failure_does_not_block_others() -> None:
    """If one TX meter fails, the rest must still be polled."""
    radio = make_radio(ptt=True)
    radio.radio_state.ptt = True
    radio.get_alc_meter = AsyncMock(side_effect=RuntimeError("ALC timeout"))
    radio.get_power_meter = AsyncMock(return_value=200)
    radio.get_comp_meter = AsyncMock(return_value=15)
    radio.get_swr_meter = AsyncMock(return_value=80)

    poller = YaesuCatPoller(
        radio,
        callback=lambda s: None,
        fast_interval=0.01,
        medium_interval=10.0,
        slow_interval=10.0,
        ema_alpha=1.0,
    )
    await poller.start()
    await asyncio.sleep(0.05)
    await poller.stop()

    # ALC failed, but power/comp/swr should still have been read
    radio.get_power_meter.assert_called()
    radio.get_comp_meter.assert_called()
    radio.get_swr_meter.assert_called()
    assert radio.radio_state.power_meter == 200
    assert radio.radio_state.comp_meter == 15
    assert radio.radio_state.swr_meter == 80


# ---------------------------------------------------------------------------
# CW parameter polling (#560)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_slow_poll_reads_cw_params() -> None:
    """Slow poll should read keyer speed, CW pitch (Hz), and break-in when CW capable."""
    radio = make_radio()
    radio.get_keyer_speed = AsyncMock(return_value=25)
    radio.get_cw_pitch = AsyncMock(return_value=700)  # Hz (#1162)
    radio.get_break_in = AsyncMock(return_value=True)

    poller = YaesuCatPoller(
        radio,
        callback=lambda s: None,
        fast_interval=10.0,
        medium_interval=10.0,
        slow_interval=0.01,
    )
    await poller.start()
    await asyncio.sleep(0.05)
    await poller.stop()

    radio.get_keyer_speed.assert_called()
    radio.get_cw_pitch.assert_called()
    radio.get_break_in.assert_called()
    assert radio.radio_state.key_speed == 25
    assert radio.radio_state.cw_pitch == 700
    assert radio.radio_state.break_in == 1


@pytest.mark.asyncio
async def test_slow_poll_skips_cw_without_capability() -> None:
    """Without 'cw' capability, CW params should not be polled."""
    radio = make_radio()
    radio.capabilities.discard("cw")

    poller = YaesuCatPoller(
        radio,
        callback=lambda s: None,
        fast_interval=10.0,
        medium_interval=10.0,
        slow_interval=0.01,
    )
    await poller.start()
    await asyncio.sleep(0.05)
    await poller.stop()

    radio.get_keyer_speed.assert_not_called()
    radio.get_cw_pitch.assert_not_called()
    radio.get_break_in.assert_not_called()


@pytest.mark.asyncio
async def test_cw_partial_failure_does_not_block_others() -> None:
    """If get_keyer_speed fails, pitch and break-in must still be polled."""
    radio = make_radio()
    radio.get_keyer_speed = AsyncMock(side_effect=RuntimeError("CAT timeout"))
    radio.get_cw_pitch = AsyncMock(return_value=700)  # Hz (#1162)
    radio.get_break_in = AsyncMock(return_value=False)

    poller = YaesuCatPoller(
        radio,
        callback=lambda s: None,
        fast_interval=10.0,
        medium_interval=10.0,
        slow_interval=0.01,
    )
    await poller.start()
    await asyncio.sleep(0.05)
    await poller.stop()

    radio.get_cw_pitch.assert_called()
    radio.get_break_in.assert_called()
    assert radio.radio_state.cw_pitch == 700
    assert radio.radio_state.break_in == 0


# ---------------------------------------------------------------------------
# SUB receiver level polling (#563)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_slow_poll_reads_sub_levels_when_dual_rx() -> None:
    """Slow poll should read SUB AF/RF/squelch and assign to state."""
    radio = make_radio()
    radio.get_af_level = AsyncMock(side_effect=lambda r=0: 128 if r == 0 else 200)
    radio.get_rf_gain = AsyncMock(side_effect=lambda r=0: 180 if r == 0 else 160)
    radio.get_squelch = AsyncMock(side_effect=lambda r=0: 0 if r == 0 else 30)

    poller = YaesuCatPoller(
        radio,
        callback=lambda s: None,
        fast_interval=10.0,
        medium_interval=10.0,
        slow_interval=0.01,
    )
    await poller.start()
    await asyncio.sleep(0.05)
    await poller.stop()

    # SUB receiver levels should be polled
    assert any(
        call.args == (1,) or call.kwargs.get("receiver") == 1
        for call in radio.get_af_level.call_args_list
    ), "get_af_level(1) was never called"

    # SUB receiver levels must be assigned to RadioState
    state = radio.radio_state
    assert state.sub.af_level == 200, f"sub.af_level={state.sub.af_level}, expected 200"
    assert state.sub.rf_gain == 160, f"sub.rf_gain={state.sub.rf_gain}, expected 160"
    assert state.sub.squelch == 30, f"sub.squelch={state.sub.squelch}, expected 30"


@pytest.mark.asyncio
async def test_slow_poll_skips_sub_levels_without_dual_rx() -> None:
    """Without dual_rx, SUB levels should not be polled."""
    radio = make_radio()
    radio.capabilities.discard("dual_rx")

    poller = YaesuCatPoller(
        radio,
        callback=lambda s: None,
        fast_interval=10.0,
        medium_interval=10.0,
        slow_interval=0.01,
    )
    await poller.start()
    await asyncio.sleep(0.05)
    await poller.stop()

    # Only receiver=0 calls should exist for all three SUB level methods
    for method_name in ("get_af_level", "get_rf_gain", "get_squelch"):
        for call in getattr(radio, method_name).call_args_list:
            assert call.args == (0,) or call.args == (), (
                f"SUB receiver was polled via {method_name}"
            )


# ---------------------------------------------------------------------------
# New RadioState fields in to_dict() (#551)
# ---------------------------------------------------------------------------


def test_new_fields_in_to_dict() -> None:
    """All #551 fields must appear in RadioState.to_dict() output."""
    state = RadioState()
    d = state.to_dict()
    for key in (
        "cw_spot",
        "yaesu",
        "break_in_delay",
        "key_speed",
        "cw_pitch",
        "break_in",
    ):
        assert key in d, f"{key} missing from to_dict()"
    # ReceiverState fields live under main/sub
    assert "apf_on" in d["main"]
    assert "apf_freq" in d["main"]


# ---------------------------------------------------------------------------
# CW polling block populates all fields (#551)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_slow_poll_reads_full_cw_block() -> None:
    """Slow poll populates key_speed, cw_pitch, break_in, break_in_delay, cw_spot."""
    radio = make_radio()
    radio.get_keyer_speed = AsyncMock(return_value=30)
    radio.get_cw_pitch = AsyncMock(return_value=750)  # Hz (#1162)
    radio.get_break_in = AsyncMock(return_value=True)
    radio.get_break_in_delay = AsyncMock(return_value=42)
    radio.get_cw_spot = AsyncMock(return_value=True)

    poller = YaesuCatPoller(
        radio,
        callback=lambda s: None,
        fast_interval=10.0,
        medium_interval=10.0,
        slow_interval=0.01,
    )
    await poller.start()
    await asyncio.sleep(0.05)
    await poller.stop()

    assert radio.radio_state.key_speed == 30
    assert radio.radio_state.cw_pitch == 750
    assert radio.radio_state.break_in == 1
    assert radio.radio_state.break_in_delay == 42
    assert radio.radio_state.cw_spot is True


# ---------------------------------------------------------------------------
# FR/FT polling populates rx_func_mode / tx_func_mode (#551)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_slow_poll_reads_rx_tx_func_mode() -> None:
    """FR/FT polling populates rx_func_mode and tx_func_mode."""
    radio = make_radio()
    radio.get_rx_func = AsyncMock(return_value=1)
    radio.get_tx_func = AsyncMock(return_value=1)

    poller = YaesuCatPoller(
        radio,
        callback=lambda s: None,
        fast_interval=10.0,
        medium_interval=10.0,
        slow_interval=0.01,
    )
    await poller.start()
    await asyncio.sleep(0.05)
    await poller.stop()

    radio.get_rx_func.assert_called()
    radio.get_tx_func.assert_called()
    assert radio.radio_state.yaesu is not None
    assert radio.radio_state.yaesu.rx_func_mode == 1
    assert radio.radio_state.yaesu.tx_func_mode == 1


@pytest.mark.asyncio
async def test_slow_poll_skips_fr_ft_without_dual_rx() -> None:
    """Without dual_rx capability, FR/FT should not be polled."""
    radio = make_radio()
    radio.capabilities.discard("dual_rx")
    radio.get_rx_func = AsyncMock(return_value=1)
    radio.get_tx_func = AsyncMock(return_value=1)

    poller = YaesuCatPoller(
        radio,
        callback=lambda s: None,
        fast_interval=10.0,
        medium_interval=10.0,
        slow_interval=0.01,
    )
    await poller.start()
    await asyncio.sleep(0.05)
    await poller.stop()

    radio.get_rx_func.assert_not_called()
    radio.get_tx_func.assert_not_called()


# ---------------------------------------------------------------------------
# Command dispatch — SetApf (formerly dropped as "Icom-only DSP feature")
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_execute_command_set_apf_dispatches_to_radio() -> None:
    """SetApf must reach radio.set_audio_peak_filter — used to be silently dropped."""
    from rigplane.runtime._poller_types import SetApf

    radio = make_radio()
    radio.set_audio_peak_filter = AsyncMock()
    poller = YaesuCatPoller(radio, callback=lambda s: None, fast_interval=10.0)

    await poller._execute_command(SetApf(mode=1, receiver=0))

    radio.set_audio_peak_filter.assert_awaited_once_with(1, receiver=0)


@pytest.mark.asyncio
async def test_execute_command_set_apf_off_dispatches_to_radio() -> None:
    """SetApf(mode=0) reaches the canonical entry point too."""
    from rigplane.runtime._poller_types import SetApf

    radio = make_radio()
    radio.set_audio_peak_filter = AsyncMock()
    poller = YaesuCatPoller(radio, callback=lambda s: None, fast_interval=10.0)

    await poller._execute_command(SetApf(mode=0, receiver=0))

    radio.set_audio_peak_filter.assert_awaited_once_with(0, receiver=0)


# ---------------------------------------------------------------------------
# Command dispatch — SetPower unit-tag (#1168)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_execute_command_set_power_watts_unit_dispatches_to_radio() -> None:
    """SetPower(unit='watts') flows directly to radio.set_power(watts)."""
    from rigplane.runtime._poller_types import SetPower

    radio = make_radio()
    radio.set_power = AsyncMock()
    poller = YaesuCatPoller(radio, callback=lambda s: None, fast_interval=10.0)

    await poller._execute_command(SetPower(level=50, unit="watts"))

    radio.set_power.assert_awaited_once_with(50)


@pytest.mark.asyncio
async def test_execute_command_set_power_raw_255_unit_rejected() -> None:
    """SetPower with wrong unit raises ValueError so the caller can report failure."""
    from rigplane.runtime._poller_types import SetPower

    radio = make_radio()
    radio.set_power = AsyncMock()
    poller = YaesuCatPoller(radio, callback=lambda s: None, fast_interval=10.0)

    with pytest.raises(ValueError, match="unit='raw_255'"):
        await poller._execute_command(SetPower(level=200))  # default unit='raw_255'

    radio.set_power.assert_not_awaited()
